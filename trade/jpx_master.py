"""JPX 상장종목 목록(영문판) → {도쿄 코드: 영문 회사명} 로컬 마스터.

사용자 2026-09-23 "일본 마스터 해줘" — 혼합시장 보드(중국·말레이시아 수출 등)에
실리는 도쿄 상장사(`Taiyo Yuden (6976)` · `Renesas Electronics Corporation
(6723)`)에 딥링크를 붙이려면 그 코드가 **도쿄의 그 회사인지** 확인할 거래소
목록이 필요하다. 4자리 숫자는 대만(TWSE) 코드와 모양이 같아서, 보드가 시장을
선언하지 않는 곳에서 추측하면 남의 회사 화면이 열린다(#34·#144·#400).

원천은 **재고 나서** 골랐다(#12·#151·#345 — `jp_master_probe` VM 실측 2026-09-23):
  · JPX 「その他統計資料」 영문 페이지가 200 이고 `data_e.xlsx` 한 파일에
    `Local Code` · `Name (English)` 열로 전 상장종목을 준다(주 1회 1콜).
  · 캡션 이름은 영문이다 — 日本語판(`data_j.xlsx` 銘柄名)은 대조에 못 쓴다.
  · yfinance 도 12/12 이름을 줬지만 종목마다 1콜이고 트레이드 venv 엔 없다
    (② 실측). 거래소 목록이 신원의 정본이다(#86·#400).

⚠️ **표준 라이브러리만 쓴다.** 트레이드 venv 엔 pandas·openpyxl 이 없다
(requirements 에 없고 ② 가 설치본을 쟀다). xlsx 는 zip 안의 XML 이고 HTTP 는
형제 `price_provider` 처럼 `urllib` 이다 — 의존성을 늘리면 그 venv 를 따로 고쳐야
하고, 안 고치면 이 모듈이 조용히 죽는다(#151·#42a). 회귀가 import 를 전수로 잰다.

⚠️ JPX 는 **월 1회** 목록을 갱신한다(`Effective Date` 실측 20260831). 그 사이
상장한 종목은 목록에 없어 평문으로 남는다 — 틀린 링크가 아니라 없는 링크다(#144).
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

from trade.price_provider import _DATA_DIR
from trade.stock_link import _JP_CODE

log = logging.getLogger(__name__)

# 로컬 마스터(`build_jpx_codes` 가 원자적으로 쓴다). 테스트는 이 상수를
# 갈아끼운다(루트 conftest 리다이렉트 · #30·#312·#344).
PATH = _DATA_DIR / "jpx_codes.json"

# 2026-09-23 VM 실측 — 목록 페이지가 200 이고 파일 링크가 이 경로였다.
# 파일 주소는 **페이지에서 찾는다**(JPX 는 확장자를 .xls→.xlsx 로 바꾼 적이
# 있다). 페이지에서 못 찾을 때만 실측 주소로 떨어지고, 떨어졌다고 말한다(#42a).
LIST_PAGE = ("https://www.jpx.co.jp/english/markets/statistics-equities/misc/"
             "01.html")
KNOWN_FILE = ("https://www.jpx.co.jp/english/markets/statistics-equities/misc/"
              "tvdivq0000001vg2-att/data_e.xlsx")
_FILE_HREF = re.compile(r'href=["\']([^"\']*/data_e\.xlsx)["\']', re.I)
# 프로브가 200 을 받은 UA 모양(`Mozilla/5.0 (compatible; …)`)을 따른다(#145).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NOAH-trade/1.0)"}
# ⚠️ 소켓 타임아웃은 **바이트가 끊긴 시간**만 잰다 — 몸통이 조금씩 흘러오면
# 영원히 안 끝난다. 이 단계는 5분 타이머 유닛 안에서 돌므로 요청마다 **총
# 상한**을 두고 크기에도 상한을 둔다(독립 리뷰 2026-09-23 · #116 예산).
_TIMEOUT = 15
_DEADLINE_S = 30
_MAX_BYTES = 20 * 2 ** 20      # 실측 파일은 22만 바이트대(⑥) — 넉넉한 벽

# 실측 헤더(⑥ 표본) — 위치가 아니라 **이름으로** 열을 찾는다(#46).
_COL_CODE = "Local Code"
_COL_NAME = "Name (English)"
_COL_DATE = "Effective Date"

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def fail_path() -> Path:
    """빌더의 마지막 실패 기록(곁파일). 빌더가 쓰고 프로브 ⑦ 이 읽는다 —
    경로를 두 곳에 적으면 갈라진다(#38). `PATH` 에서 **호출 시점에** 파생해
    테스트 리다이렉트를 따라간다."""
    return PATH.with_name(PATH.stem + ".fail.json")


class FetchError(RuntimeError):
    """못 받았거나 못 읽었다 — 메시지가 갈래를 이름으로 말한다(#82)."""


# ── xlsx (표준 라이브러리) ────────────────────────────────────────────────


def _col_index(ref: str) -> int:
    """`B12` → 1. 열 글자만 센다."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _rich_text(el) -> str:
    """문자열 요소(`<si>` 또는 인라인 `<is>`)의 본문.

    ⚠️ 일본 Excel 파일은 문자열 안에 **후리가나**(`<rPh><t>…</t></rPh>`)를 싣는다.
    아래 `<t>` 를 전부 이으면 회사명 뒤에 읽기가 붙어 이름이 조용히 틀린다 —
    본문(`<t>` 직속 또는 `<r>` 런 안)만 잇는다. 공유 문자열과 인라인 문자열이
    **같은 함수**를 쓴다(한쪽만 런을 읽으면 셀 모양에 따라 이름이 빈다, #38)."""
    parts: list[str] = []
    for child in el:
        if child.tag == _NS + "t":
            parts.append(child.text or "")
        elif child.tag == _NS + "r":
            t = child.find(_NS + "t")
            parts.append((t.text or "") if t is not None else "")
    return "".join(parts)


def _xml(z: zipfile.ZipFile, name: str):
    """zip 안 XML 을 읽는다 — 깨졌으면 원문 파서 예외가 아니라 FetchError 로
    **어느 부분이** 깨졌는지 말한다(#82)."""
    try:
        return ET.fromstring(z.read(name))
    except ET.ParseError as e:
        raise FetchError(f"{name} XML 파싱 실패({e})") from e


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    """공유 문자열 표(없으면 [])."""
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    return [_rich_text(si)
            for si in _xml(z, "xl/sharedStrings.xml").findall(_NS + "si")]


def _first_sheet(z: zipfile.ZipFile) -> str:
    """통합문서가 **첫 번째로 선언한** 시트의 zip 경로(없으면 sheet1)."""
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find(f"{_NS}sheets/{_NS}sheet")
        rid = first.get(_REL_NS + "id") if first is not None else None
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rels.findall(_PKG_REL_NS + "Relationship"):
            if rel.get("Id") == rid:
                target = rel.get("Target") or ""
                if target.startswith("/"):
                    return target.lstrip("/")
                return "xl/" + target
    except (KeyError, ET.ParseError):
        pass
    return "xl/worksheets/sheet1.xml"


def xlsx_rows(data: bytes) -> list[list[str]]:
    """첫 시트의 행들(빈 칸은 ""). xlsx 가 아니면 FetchError."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        head = data[:8]
        raise FetchError(f"xlsx(zip)가 아니다 — 앞 8바이트 {head!r}") from e
    with z:
        shared = _shared_strings(z)
        path = _first_sheet(z)
        if path not in z.namelist():
            raise FetchError(f"시트 {path} 가 없다 — 목록 {z.namelist()[:8]}")
        sheet = _xml(z, path)
    rows: list[list[str]] = []
    for row in sheet.iter(_NS + "row"):
        cells: dict[int, str] = {}
        nxt = 0
        for c in row.findall(_NS + "c"):
            ref = c.get("r")
            col = _col_index(ref) if ref else nxt
            nxt = col + 1
            kind = c.get("t")
            v = c.find(_NS + "v")
            if kind == "s":
                try:
                    val = shared[int(v.text)] if v is not None and v.text else ""
                except (ValueError, IndexError):
                    val = ""
            elif kind == "inlineStr":
                is_ = c.find(_NS + "is")
                val = _rich_text(is_) if is_ is not None else ""
            else:
                val = (v.text or "") if v is not None else ""
            cells[col] = val
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _code(raw: str) -> str:
    """셀 값 → 도쿄 코드. 숫자 셀이 `1301.0` 으로 올 수 있다."""
    s = str(raw or "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    return s.upper()


def parse_listing(data: bytes) -> tuple[dict[str, str], dict]:
    """`data_e.xlsx` 바이트 → ({코드: 영문명}, 통계).

    통계 = rows(헤더 뒤 행 수) · rejected(코드 모양이 아니거나 이름이 빈 행) ·
    dupes · effective(목록 기준일 최댓값, YYYY-MM-DD) · rejected_sample.
    헤더를 못 찾으면 FetchError — 첫 행들을 원문으로 싣는다(#109)."""
    rows = xlsx_rows(data)
    hdr_i = -1
    for i, r in enumerate(rows[:20]):
        cells = [x.strip() for x in r]
        if _COL_CODE in cells and _COL_NAME in cells:
            hdr_i = i
            break
    if hdr_i < 0:
        raise FetchError(f"헤더({_COL_CODE!r}·{_COL_NAME!r})를 못 찾았다 — "
                         f"첫 행 {rows[:2]!r}"[:400])
    hdr = [x.strip() for x in rows[hdr_i]]
    ci, ni = hdr.index(_COL_CODE), hdr.index(_COL_NAME)
    di = hdr.index(_COL_DATE) if _COL_DATE in hdr else -1
    out: dict[str, str] = {}
    stats = {"rows": 0, "rejected": 0, "dupes": 0, "effective": "",
             "rejected_sample": []}
    dates: list[str] = []
    for r in rows[hdr_i + 1:]:
        if not any(x.strip() for x in r):
            continue
        stats["rows"] += 1
        code = _code(r[ci] if ci < len(r) else "")
        name = (r[ni] if ni < len(r) else "").strip()
        if not (_JP_CODE.fullmatch(code) and name):
            stats["rejected"] += 1
            if len(stats["rejected_sample"]) < 3:
                stats["rejected_sample"].append((code, name))
            continue
        if code in out:
            stats["dupes"] += 1
            continue
        out[code] = name
        if 0 <= di < len(r):
            d = _code(r[di])
            if re.fullmatch(r"\d{8}", d):
                dates.append(d)
    if dates:
        d = max(dates)
        stats["effective"] = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return out, stats


# ── 원천 ──────────────────────────────────────────────────────────────────


def _get(url: str, *, deadline: float = _DEADLINE_S,
         max_bytes: int = _MAX_BYTES, opener=None) -> tuple[int, bytes]:
    """(status, body). HTTP 오류 상태는 (코드, b"") 로 돌려준다 — 호출부가 갈래를
    이름으로 말한다(#82). **총 시간·크기 상한**을 넘으면 FetchError."""
    opener = opener or urllib.request.urlopen
    t0 = time.monotonic()
    try:
        with opener(urllib.request.Request(url, headers=_HEADERS),
                    timeout=_TIMEOUT) as r:
            chunks: list[bytes] = []
            got = 0
            while True:
                if time.monotonic() - t0 > deadline:
                    raise FetchError(f"총 {deadline:g}초 상한 초과 — {got}바이트에서 "
                                     f"끊었다({url})")
                b = r.read(65536)
                if not b:
                    break
                got += len(b)
                if got > max_bytes:
                    raise FetchError(f"{max_bytes}바이트 상한 초과({url})")
                chunks.append(b)
            return getattr(r, "status", 200), b"".join(chunks)
    except urllib.error.HTTPError as e:
        return e.code, b""


def find_file_url(html: str, base: str = LIST_PAGE) -> str:
    """목록 페이지에서 `…/data_e.xlsx` 링크(상대경로는 페이지 기준으로 잇는다)."""
    m = _FILE_HREF.search(html or "")
    return urljoin(base, m.group(1)) if m else ""


def fetch(*, get=None) -> tuple[dict[str, str], dict]:
    """JPX 목록을 받아 파싱. 실패하면 FetchError(갈래를 이름으로).

    통계에 `via`(주소를 어떻게 얻었나)와 `url` 을 싣는다 — 실측 주소로
    떨어졌으면 그렇다고 말한다(#42a 폴백은 탔는지 재고 알린다)."""
    get = get or _get
    url, via = "", ""
    try:
        st, body = get(LIST_PAGE)
    except Exception as e:                                # noqa: BLE001
        st, body, via = 0, b"", f"목록 페이지 요청 실패({type(e).__name__})"
    if st == 200:
        url = find_file_url(body.decode("utf-8", "replace"))
        via = "목록 페이지 링크" if url else "목록 페이지에 data_e.xlsx 링크 없음"
    elif st:
        via = f"목록 페이지 HTTP {st}"
    if not url:
        url = KNOWN_FILE
        via += " → 실측 주소(2026-09-23)로 시도"
    try:
        st, body = get(url)
    except Exception as e:                                # noqa: BLE001
        raise FetchError(f"파일 요청 실패({type(e).__name__}: {e}) · {via}") from e
    if st != 200:
        raise FetchError(f"파일 HTTP {st} · {url} · {via}")
    master, stats = parse_listing(body)
    stats.update(via=via, url=url, bytes=len(body))
    return master, stats


# ── 로컬 마스터 읽기 ──────────────────────────────────────────────────────

_memo: dict = {"key": None, "data": {}, "meta": {}}


def load_envelope() -> tuple[dict[str, str], dict]:
    """({코드: 영문명}, 메타). 없거나 깨졌으면 ({}, {"error": 갈래}).

    (경로, mtime) 메모이즈 — 페이지마다 불러도 파일이 바뀔 때만 다시 읽는다.
    ⚠️ 없음·깨짐을 구별해 돌려준다(진단용) — 렌더는 둘 다 평문이다(#82)."""
    try:
        key = (str(PATH), PATH.stat().st_mtime)
    except OSError:
        return {}, {"error": "없음"}
    if _memo["key"] != key:
        data: dict[str, str] = {}
        meta: dict = {}
        try:
            raw = json.loads(PATH.read_text(encoding="utf-8"))
            codes = raw.get("codes") if isinstance(raw, dict) else None
            if isinstance(codes, dict):
                data = {str(k).upper(): str(v) for k, v in codes.items()
                        if v and _JP_CODE.fullmatch(str(k))}
                meta = {k: raw.get(k) for k in ("effective", "built_at", "url",
                                                "via")}
            else:
                meta = {"error": "형식이 다르다(codes 없음)"}
        except Exception as e:                            # noqa: BLE001
            meta = {"error": f"못 읽음({type(e).__name__})"}
            log.warning("JPX master unreadable (%s) — %s", PATH, e)
        _memo.update(key=key, data=data, meta=meta)
    return _memo["data"], _memo["meta"]


def load() -> dict[str, str]:
    """{도쿄 코드: JPX 영문명} — 없으면 {}(전 칩 평문)."""
    return load_envelope()[0]
