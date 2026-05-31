"""관세청 HS code reference lookup (dataset 15049722).

Loads a downloaded copy of the 관세청_HS부호 file and offers substring /
prefix search so the bot can build inline-button menus for '/hs 반도체'
queries. Without this the operator must know the exact code already,
defeating search-then-pin.

FILE FORMAT: data.go.kr ships 15049722 as an Excel **.xlsx** (≈12.5K
rows). We read it directly with the stdlib (zipfile + ElementTree) — no
pandas/openpyxl dependency. A plain **.csv** export is also accepted. The
operator just drops the downloaded file at ~/.trade/hs_codes.xlsx (or
.csv); no conversion needed.

UPDATE STORY (operator asked 'what if the data changes?'): HS codes are
revised ~annually (the file name carries the effective date, e.g.
20260101). To update, re-download and overwrite the same file — that's
it. We key an in-memory cache on the file's mtime, so a replaced file is
picked up on the next search with no restart. Already-pinned codes
(hs_map.tsv) are untouched by a reference refresh; a code that gets
discontinued simply returns no rows from the customs API (the panel
shows it, fetch logs 'no rows') — nothing breaks. Discontinued codes
also stop appearing in search because we filter on 적용종료일자 (see
below).

Columns are matched by HEADER NAME (not position) so a column reorder in
a future release doesn't break parsing. We use 한글품목명 + 영문품목명 for
search and HS부호 for the code; 적용종료일자 (an Excel serial date) lets
us drop expired codes from results.
"""

from __future__ import annotations

import csv
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
# Preferred order when no explicit path is given: the raw downloaded
# .xlsx first, then a .csv export. The .xlsx path is also what the
# 'missing file' hint points the operator at.
DEFAULT_XLSX = _DATA_DIR / "hs_codes.xlsx"
DEFAULT_CSV = _DATA_DIR / "hs_codes.csv"

_HS_HEADERS = ("HS부호", "HS코드", "HSCD", "hs_code")
_KO_HEADERS = ("한글품목명", "품목명", "한글명", "ko_name")
_EN_HEADERS = ("영문품목명", "영문명", "en_name")
_END_HEADERS = ("적용종료일자", "종료일자")
# Nature/material category — disambiguates generic leaf names. Many leaves
# share an identical 한글품목명 (e.g. '반도체 제조용') but differ by this
# field: (무기화합물) vs (사진용화합물) vs (기타 정밀화학제품). Surfacing it
# on the button label is what lets the operator tell candidate codes apart.
_NATURE_HEADERS = ("성질통합분류코드명", "성질명")

_HS_RE = re.compile(r"^\d+$")
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Generic leaf names that carry no material information on their own —
# when a leaf's 한글품목명 is one of these we walk up to an ancestor row
# (e.g. '반도체 제조용' → its 8-digit parent '황산') to recover the actual
# substance. Kept as a frozenset; extend if new filler phrases appear.
_GENERIC_NAMES = frozenset({
    "반도체 제조용", "기타", "그 밖의 것", "기타의 것",
    "따로 분류되지 않은 것", "기타의 것으로 한정한다", "",
})

# HS 2-digit chapter (類) names — international HS standard, stable, so we
# can supply a category floor even when the file has no usable ancestor
# row. This is reference data (not a guess): chapter 28 IS 무기화학품
# worldwide. Covered range focuses on the chemicals / metals / machinery
# chapters that dominate Korean trade + the semiconductor supply chain;
# chapters outside the map fall back to the 성질통합분류 nature field.
_HS_CHAPTERS = {
    "28": "무기화학품", "29": "유기화학품", "30": "의료용품", "31": "비료",
    "32": "염료·안료·페인트·잉크", "33": "정유·화장품", "34": "비누·계면활성제·왁스",
    "35": "단백질·변성전분·효소", "36": "화약류", "37": "사진·영화용 재료",
    "38": "각종 화학공업 생산품", "39": "플라스틱 제품", "40": "고무 제품",
    "70": "유리 제품", "71": "귀금속·보석", "72": "철강", "73": "철강 제품",
    "74": "구리 제품", "75": "니켈 제품", "76": "알루미늄 제품", "78": "납 제품",
    "79": "아연 제품", "80": "주석 제품", "81": "기타 비금속", "82": "비금속 공구",
    "83": "비금속 각종 제품", "84": "기계·기계류", "85": "전기기기·전자",
    "90": "광학·정밀기기",
}

# mtime-keyed cache: {resolved_path: (mtime_ns, [HsCode, ...])}
_cache: dict[str, tuple[int, list]] = {}


@dataclass(frozen=True)
class HsCode:
    hs_code: str       # 10-digit (the file's leaf granularity)
    ko_name: str
    en_name: str = ""
    nature: str = ""   # 성질통합분류코드명, parens stripped (e.g. '무기화합물')
    context: str = ""  # most specific material/category hint (see _compute_context)

    @property
    def label(self) -> str:
        """Display name combining the leaf name with its best context.

        '반도체 제조용' + context '황산' → '황산 (반도체 제조용)' so the
        operator sees the actual substance first. When the leaf name is
        already specific (not generic), it's shown as-is with the context
        appended only if it adds info."""
        ko = self.ko_name
        if self.context and self.context != ko:
            if ko in _GENERIC_NAMES or ko == "반도체 제조용":
                return f"{self.context} · {ko}"
            return f"{ko} ({self.context})"
        return ko


class HsCodeFileMissing(FileNotFoundError):
    """Raised when no reference file is present. The bot catches this to
    show a download-instruction message instead of a stack trace."""


def _default_path() -> Path:
    """Existing .xlsx, else existing .csv, else the .xlsx path (for the
    missing-file error message)."""
    if DEFAULT_XLSX.exists():
        return DEFAULT_XLSX
    if DEFAULT_CSV.exists():
        return DEFAULT_CSV
    return DEFAULT_XLSX


# ---------------------------------------------------------------------
# Raw readers → (headers, rows) where each is a list[str]
# ---------------------------------------------------------------------

def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                reader = list(csv.reader(fh))
            break
        except UnicodeDecodeError:
            reader = None
    if not reader:
        return [], []
    return reader[0], reader[1:]


def _col_index(ref: str) -> int:
    """'D2' → 3 (0-based column). Robust against sparse/skipped cells,
    which xlsx writers emit when a cell is empty."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _read_xlsx(path: Path) -> tuple[list[str], list[list[str]]]:
    z = zipfile.ZipFile(path)
    names = z.namelist()
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in sroot.findall(f"{_XLSX_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_XLSX_NS}t")))
    sheet_name = "xl/worksheets/sheet1.xml"
    if sheet_name not in names:
        sheets = [n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")]
        if not sheets:
            return [], []
        sheet_name = sorted(sheets)[0]
    root = ET.fromstring(z.read(sheet_name))

    def _cell_val(c) -> str:
        t = c.get("t")
        if t == "s":
            v = c.find(f"{_XLSX_NS}v")
            if v is not None and v.text is not None:
                try:
                    return shared[int(v.text)]
                except (ValueError, IndexError):
                    return ""
            return ""
        if t == "inlineStr":
            is_node = c.find(f"{_XLSX_NS}is")
            if is_node is not None:
                return "".join(tt.text or "" for tt in is_node.iter(f"{_XLSX_NS}t"))
            return ""
        v = c.find(f"{_XLSX_NS}v")
        return v.text if (v is not None and v.text is not None) else ""

    out: list[list[str]] = []
    for row in root.findall(f".//{_XLSX_NS}row"):
        cells: dict[int, str] = {}
        maxc = -1
        for c in row.findall(f"{_XLSX_NS}c"):
            ref = c.get("r") or ""
            col = _col_index(ref) if ref else len(cells)
            cells[col] = _cell_val(c)
            if col > maxc:
                maxc = col
        out.append([cells.get(i, "") for i in range(maxc + 1)])
    if not out:
        return [], []
    return out[0], out[1:]


# ---------------------------------------------------------------------
# Build HsCode list from (headers, rows)
# ---------------------------------------------------------------------

def _pick(headers: list[str], wanted: tuple[str, ...]) -> int | None:
    norm = [(h or "").strip().lower() for h in headers]
    for w in wanted:
        wl = w.strip().lower()
        if wl in norm:
            return norm.index(wl)
    return None


# Excel 1900 date system: serial 1 == 1900-01-01, with the well-known
# 1900-leap-year bug, so the practical epoch is 1899-12-30.
_EXCEL_EPOCH = datetime(1899, 12, 30)


def _excel_serial_to_date(value: str) -> date | None:
    v = (value or "").strip()
    if not v.isdigit():
        return None
    try:
        return (_EXCEL_EPOCH + timedelta(days=int(v))).date()
    except (ValueError, OverflowError):
        return None


def _build(
    headers: list[str],
    rows: list[list[str]],
    today: date | None = None,
) -> list[HsCode]:
    today = today or date.today()
    i_hs = _pick(headers, _HS_HEADERS)
    i_ko = _pick(headers, _KO_HEADERS)
    i_en = _pick(headers, _EN_HEADERS)
    i_end = _pick(headers, _END_HEADERS)
    i_nat = _pick(headers, _NATURE_HEADERS)
    if i_hs is None or i_ko is None:
        return []
    out: list[HsCode] = []
    for row in rows:
        if len(row) <= max(i_hs, i_ko):
            continue
        hs = (row[i_hs] or "").strip()
        ko = (row[i_ko] or "").strip()
        if not hs or not ko or not _HS_RE.match(hs):
            continue
        # Drop codes whose application end-date is already in the past so
        # the operator can't pin a discontinued code. Blank / unparseable
        # end-dates are kept (fail-open — better to show than to hide).
        if i_end is not None and i_end < len(row):
            end = _excel_serial_to_date(row[i_end])
            if end is not None and end < today:
                continue
        en = (row[i_en] or "").strip() if i_en is not None and i_en < len(row) else ""
        nat = ""
        if i_nat is not None and i_nat < len(row):
            nat = (row[i_nat] or "").strip().strip("()").strip()
        out.append(HsCode(hs_code=hs, ko_name=ko, en_name=en, nature=nat))

    # Second pass: fill `context` for generic-named leaves using the most
    # specific source available — a non-generic ancestor row's name (e.g.
    # '황산'), else the HS 2-digit chapter (e.g. '무기화학품'), else the
    # 성질통합분류 nature. Needs the full code→name map, hence a 2nd pass.
    name_by_code = {c.hs_code: c.ko_name for c in out}
    resolved: list[HsCode] = []
    for c in out:
        ctx = _compute_context(c, name_by_code)
        resolved.append(
            HsCode(hs_code=c.hs_code, ko_name=c.ko_name,
                   en_name=c.en_name, nature=c.nature, context=ctx)
            if ctx else c
        )
    return resolved


def _compute_context(c: HsCode, name_by_code: dict[str, str]) -> str:
    """Most specific material/category hint for a leaf.

    Priority (most → least specific):
      1) longest non-generic ANCESTOR row name in the file
         (10-digit '반도체 제조용' under 8-digit '황산' → '황산')
      2) HS 2-digit chapter name (international standard floor)
      3) 성질통합분류코드명 nature

    Returns '' when the leaf name is already specific AND no extra context
    helps, so .label can decide whether to append anything."""
    code = c.hs_code
    # 1) walk DOWN prefix lengths, longest first, for a real ancestor name.
    for L in (9, 8, 7, 6, 5, 4):
        if L < len(code):
            nm = name_by_code.get(code[:L], "")
            if nm and nm not in _GENERIC_NAMES:
                return nm
    # 2) HS chapter floor.
    chap = _HS_CHAPTERS.get(code[:2])
    if chap:
        return chap
    # 3) nature fallback.
    return c.nature


def load(path: Path | str | None = None, today: date | None = None) -> list[HsCode]:
    """Read the reference into HsCode rows (mtime-cached). Raises
    HsCodeFileMissing when no file is present.

    `today` is exposed for testing the expiry filter; production uses the
    real date. Passing an explicit `today` bypasses the cache so tests are
    deterministic."""
    p = Path(path) if path is not None else _default_path()
    if not p.exists():
        raise HsCodeFileMissing(f"HS code reference not found at {p}")

    if today is None:
        key = str(p)
        mtime = p.stat().st_mtime_ns
        cached = _cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

    if p.suffix.lower() == ".xlsx":
        headers, rows = _read_xlsx(p)
    else:
        headers, rows = _read_csv(p)
    built = _build(headers, rows, today)

    if today is None:
        _cache[str(p)] = (p.stat().st_mtime_ns, built)
    return built


def search(
    query: str,
    *,
    path: Path | str | None = None,
    limit: int = 20,
) -> tuple[list[HsCode], int]:
    """Search the HS reference. Returns (results_capped_at_limit, total_hits).

    Numeric query → HS-code prefix match (e.g. '8542' → semiconductor
    leaves). Otherwise → 한글품목명 substring (case-insensitive), with
    영문품목명 as a fallback so '/hs DRAM' works too. Sorted by HS code so
    related leaves cluster. `total_hits` lets the caller note when the
    list was capped."""
    q = (query or "").strip()
    if not q:
        return [], 0
    rows = load(path)  # may raise HsCodeFileMissing
    is_numeric = bool(_HS_RE.match(q))
    hits: list[HsCode] = []
    if is_numeric:
        for r in rows:
            if r.hs_code.startswith(q):
                hits.append(r)
    else:
        ql = q.lower()
        for r in rows:
            if ql in r.ko_name.lower() or (r.en_name and ql in r.en_name.lower()):
                hits.append(r)
    hits.sort(key=lambda r: r.hs_code)
    return hits[:limit], len(hits)
