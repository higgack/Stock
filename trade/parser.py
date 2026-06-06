"""BeOn caption parser — RULE 1~10 (see CLAUDE.md / trade/README.md).

A pure-Python single-file module. Takes one caption string (the text body
of a forwarded BeOn alert, e.g. trade-bot's inbox.jsonl 'caption' field)
and returns a ParsedAlert dataclass. Returns None when the caption
isn't a BeOn data alert at all (no recognizable trailing status line).

Design choices:
- Be forgiving on whitespace / dash variants, strict on structure.
- Never lose data: anything unparseable goes into parse_warnings rather
  than silently dropped. ingest_inbox.py surfaces warning counts so we
  notice new variants instead of guessing about them.
- item-only alerts (BeOn knows the HS code but not the Korean company)
  are first-class citizens — stocks=[] with no warning. They show up
  in the 품목별 view and are absent from 회사별 view by virtue of
  having no companies attached.
"""

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date


# --- RULE 3 — period + status + direction (the final line of every alert) ---
# Accepts ~, –, —, - between days; tolerant of whitespace; the day-range
# group is optional (확정치 typically only has 'YYYY년 M월' with no days).
_FINAL_LINE_RE = re.compile(
    r"(\d{4})\s*년\s*(\d{1,2})\s*월"
    r"(?:\s*(\d{1,2})\s*일\s*[~–—\-]\s*"
    r"(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일)?"
    r"\s*(잠정치|확정치)\s*(수출|수입)\s*데이터"
)

# --- RULE 5 (broadened) — company-first prefix: '<회사[/회사…]> : <품목...>' ---
# 첫 콜론의 왼쪽에 괄호가 없으면 회사 접두로 본다(콜론 앞 공백 유무 무관). 나머지는
# 아래에서 품목으로 다시 파싱(지역괄호/복합 +). 'BeOn은 휴스틸: 품목'처럼 콜론 앞
# 공백 없이도 씀 → 옛 '\s+:\s+'가 못 잡아 회사가 품목에 박히던 문제 수정.
# RULE 12 '품목 (지역): 회사'는 콜론이 ')' 뒤라 왼쪽에 괄호가 있어 매치 안 됨.
# item_first는 콜론 자체가 없음. 회사명에 '(' 없으니 품목 괄호('음극재 (천연흑연)')는
# 안 삼킴. 상한 없음(최소 2자) — 긴 복합사('HD현대일렉트릭 / 효성중공업 / LS ELECTRIC
# / 일진전기 등' ~39자)도 회사로 잡아야 함. 콜론 앞 괄호 없는 건 회사접두뿐이라 길이
# 상한이 없어도 item_first 오매칭 위험 없음(20자 상한이 긴 복합사를 놓쳐 회귀했음).
_COMPANY_TITLE_RE = re.compile(r"^([^():]{2,}?)\s*:\s*(.+)$")

# --- RULE 4 — standard title: '<품목> (<지역>)' ---
# Right-anchored to (...) so multi-paren items keep their inner parens
# inside the item, with only the rightmost group treated as region.
_STANDARD_TITLE_RE = re.compile(r"^(.+?)\s*\(([^()]+)\)\s*$")

# --- RULE 12 — inline stocks: '<품목> (<지역>): <회사>/<회사>/...' ---
# Same line carries title + 관련종목. Caller must verify group(1) has no
# '/' to reject the inverted synthesis-post shape (companies → items).
_INLINE_STOCKS_TITLE_RE = re.compile(r"^(.+?)\s*\(([^()]+)\)\s*:\s*(.+)$")

# --- RULE 13 — trailing text after region paren: '<품목> (<지역>) <suffix>' ---
# Used for 'H형강 (전국) 합산' style. Caller must verify the suffix doesn't
# start with ':' (that's the synthesis-post shape, not trailing text).
_TRAILING_TEXT_TITLE_RE = re.compile(r"^(.+?)\s*\(([^()]+)\)\s*(.+)$")

# --- RULE 14 — company-first without region paren: '<회사> : <품목>' ---
# Used only when the title has NO parens at all. region/country = None.
_COMPANY_NO_REGION_RE = re.compile(r"^([^():]+?)\s+:\s+(.+?)\s*$")

# --- RULE 9 — related-stocks line ---
# Allows '관련종목 :' / '관련종목:' / '관련종목  :'.
_STOCKS_LINE_RE = re.compile(r"^\s*관련종목\s*:\s*(.*)$")

# --- RULE 8 — composite item separator ' + ' (with or without spaces) ---
# `&` is NOT a separator — e.g. 'SB&MSB' is a single item name.
_COMPOSITE_SPLIT_RE = re.compile(r"\s*\+\s*")

# Trailing '등' on the stocks line (with leading whitespace or '/').
_ETC_SUFFIX_RE = re.compile(r"[\s/]+등\s*$")

# Marker like '(비상장)' at the tail of a stock name.
_STOCK_MARKER_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


@dataclass
class ParsedAlert:
    """Structured form of one BeOn caption. Field order mirrors the
    SQLite schema in trade/store.py so dict serialization is direct.
    """

    direction: str  # 'export' | 'import'
    status: str  # 'preliminary' | 'final'
    period_start: date
    period_end: date
    period_kind: str  # 'decadal_10' | 'decadal_20' | 'monthly' | 'custom'

    title_kind: str  # 'item_first' | 'company_first' | 'unknown'
    item: str  # normalized (whitespace collapsed)
    item_raw: str  # as it appeared in the title
    is_composite: bool

    composite_parts: list[str] = field(default_factory=list)

    region: str | None = None
    country: str | None = None
    regions: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)

    stocks: list[str] = field(default_factory=list)
    stocks_meta: dict[str, str] = field(default_factory=dict)
    has_etc: bool = False

    commentary: str | None = None
    parse_warnings: list[str] = field(default_factory=list)

    def dedup_key(self) -> str:
        """Group key for the 'latest only' dashboard query: same
        (direction, item, region, country) tuple shares one slot.
        """
        return "|".join(
            [self.direction, self.item, self.region or "", self.country or ""]
        )


def _last_day(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _looks_like_placeholder(stocks_line_raw: str) -> bool:
    """RULE 10 — '월별 수출 데이터' / '분기 수출 데이터' style placeholder.

    BeOn writes these when the HS code is published but no specific
    Korean company is associated. We treat the alert as item-only
    (stocks=[]); the item itself is the value.
    """
    s = stocks_line_raw.strip()
    if not s:
        return False
    if "/" in s:
        return False  # real lists have slash separators
    if " " not in s:
        return False  # bare names like '유니드' are single companies
    return "데이터" in s


def _parse_period(
    year: int,
    month: int,
    day_start_str: str | None,
    end_month_str: str | None,
    day_end_str: str | None,
) -> tuple[date, date, str]:
    """RULE 3 — period bounds + canonical period_kind."""
    if day_start_str is None:
        # bare month → full month, typical of 확정치
        return (
            date(year, month, 1),
            date(year, month, _last_day(year, month)),
            "monthly",
        )

    day_start = int(day_start_str)
    end_month = int(end_month_str) if end_month_str else month
    day_end = int(day_end_str)
    start = date(year, month, day_start)
    end = date(year, end_month, day_end)

    last = _last_day(year, month)
    if end_month != month:
        kind = "custom"
    elif day_start == 1 and day_end == 10:
        kind = "decadal_10"
    elif day_start == 1 and day_end == 20:
        kind = "decadal_20"
    elif day_start == 1 and day_end == last:
        kind = "monthly"
    else:
        kind = "custom"
    return start, end, kind


def _parse_region(
    region_part: str,
) -> tuple[str | None, str | None, list[str], list[str]]:
    """RULE 6 — split the (...) content into region/country/multi.

    Variants observed in the wild:
      (전국)                       → 전국 / None
      (전국_중국)                  → 전국 / 중국
      (전국_중국+베트남)           → 전국 / 중국 + [중국, 베트남]
      (경기 평택시)                → 경기 평택시 / None
      (경기 평택시_글로벌)         → 경기 평택시 / 글로벌
      (경기 이천시 + 충북 청주시)  → multi-region, no country
      ""                          → None / None (RULE 14 fallback)
    """
    s = region_part.strip()
    if not s:
        return None, None, [], []
    if "+" in s and "_" not in s:
        regions = [p.strip() for p in s.split("+")]
        return regions[0], None, regions, []
    if "_" in s:
        region, _, country = s.partition("_")
        region = region.strip()
        country = country.strip()
        if "+" in country:
            countries = [c.strip() for c in country.split("+")]
            return region, countries[0], [region], countries
        return region, country, [region], [country]
    return s, None, [s], []


def _parse_stocks(
    raw: str,
) -> tuple[list[str], dict[str, str], bool, list[str]]:
    """RULE 9 + 10 — split the 관련종목 line into stocks + metadata.

    Returns (stocks, stocks_meta, has_etc, warnings).
    """
    raw = raw.strip()
    warnings: list[str] = []
    if not raw:
        return [], {}, False, []

    if _looks_like_placeholder(raw):
        return [], {}, False, []  # legitimate item-only alert

    has_etc = False
    m = _ETC_SUFFIX_RE.search(raw)
    if m:
        has_etc = True
        raw = raw[: m.start()].rstrip()

    stocks: list[str] = []
    meta: dict[str, str] = {}
    for part in raw.split("/"):
        part = part.strip()
        if not part:
            continue
        mk = _STOCK_MARKER_RE.search(part)
        if mk:
            name = part[: mk.start()].strip()
            marker = mk.group(1).strip()
            if name:
                stocks.append(name)
                meta[name] = marker
            else:
                warnings.append("stocks_marker_without_name")
        else:
            stocks.append(part)

    return stocks, meta, has_etc, warnings


def _is_known_company(name: str) -> bool:
    """이름이 KRX 상장 마스터(이름→코드)에 있나(공백제거 정확일치). 마스터 없으면 False."""
    try:
        from trade import price_provider as pp
        return (name or "").replace(" ", "") in pp._load_krx_master()
    except Exception:
        return False


def _company_left_inverted(x: str, y: str, z: str) -> bool:
    """'X (Y): Z' inline 제목이 회사-우선(거꾸로)인지 판별.

    BeOn은 '품목 (지역): 회사'(RULE 12)도, 반대인 '회사 (별칭/지역/비상장): 품목'도
    쓴다. 거꾸로로 보는 조건은 **좌변(X 또는 괄호 Y)이 KRX 상장사이거나 Y가 '비상장'
    마커**이고, 동시에 **우변(Z)이 회사목록이 아닐 때**. 실제 RULE 12는 X=품목(비회사)·
    Y=지역(비회사·비'비상장')이라 이 조건이 참이 될 수 없어 정상 행은 절대 안 뒤집힘
    (회귀 0). 마스터 못 읽으면 '비상장' 마커만으로 보수적 판정."""
    try:
        from trade import price_provider as pp
        master = pp._load_krx_master()
        n = lambda s: (s or "").replace(" ", "")
        left_is_company = n(x) in master or y == "비상장" or n(y) in master
        if not left_is_company:
            return False
        # 우변이 회사목록이면(=실제 RULE 12) 뒤집지 않음 — 이중 가드.
        z_companies = [t for t in pp.split_names([z]) if n(t) in master]
        return not z_companies
    except Exception:
        return y == "비상장"


def parse_caption(caption: str) -> ParsedAlert | None:
    """Top-level entry point. None if the caption is not a BeOn alert."""
    if not caption:
        return None
    lines = caption.strip().split("\n")
    if not lines:
        return None

    # Locate the trailing status line (search from bottom).
    final_match = None
    final_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        m = _FINAL_LINE_RE.search(lines[i])
        if m:
            final_match = m
            final_idx = i
            break
    if final_match is None:
        return None

    year = int(final_match.group(1))
    month = int(final_match.group(2))
    period_start, period_end, period_kind = _parse_period(
        year,
        month,
        final_match.group(3),
        final_match.group(4),
        final_match.group(5),
    )
    status = "preliminary" if final_match.group(6) == "잠정치" else "final"
    direction = "export" if final_match.group(7) == "수출" else "import"

    # RULE 11 — stitch consecutive non-empty lines into one logical
    # title until we hit a blank line, the 관련종목 line, or the final
    # status line. Handles wrap-arounds like the 항생제 and 해상용
    # 안테나 samples where parens / qualifiers spill onto a second
    # line. Single-line titles are unaffected (the very next iteration
    # breaks on blank/stocks/final).
    title_lines: list[str] = []
    title_idx = -1
    title_end_idx = -1
    for i, line in enumerate(lines):
        if line.strip():
            if title_idx == -1:
                title_idx = i
            if _STOCKS_LINE_RE.match(line) or _FINAL_LINE_RE.search(line):
                break
            title_lines.append(line.strip())
            title_end_idx = i
        elif title_lines:
            break
    if not title_lines:
        return None
    title = " ".join(title_lines)

    warnings: list[str] = []

    company_from_title: str | None = None
    inline_stocks_raw: str | None = None
    item_raw: str | None = None
    region_part: str | None = None
    title_kind: str = ""

    # Try variants in order of specificity.
    cm = _COMPANY_TITLE_RE.match(title)
    if cm:
        company_from_title = cm.group(1).strip()
        rest = cm.group(2).strip()
        title_kind = "company_first"
        # 나머지를 품목으로: 지역 괄호 있으면 분리, 없으면(복합 '+'/무지역) 통째 품목.
        rm = _STANDARD_TITLE_RE.match(rest)
        if rm:
            item_raw = rm.group(1).strip()
            region_part = rm.group(2).strip()
        else:
            item_raw = rest
            region_part = ""
            warnings.append("title_no_region")
    else:
        # RULE 12 — inline stocks. Reject when LEFT side has '/' because
        # that's the synthesis-post inversion (companies / companies (region) :
        # items + items), not '품목 (지역): 회사/회사'.
        ism = _INLINE_STOCKS_TITLE_RE.match(title)
        if ism and "/" not in ism.group(1):
            x = ism.group(1).strip()        # 좌변
            y = ism.group(2).strip()        # 괄호(지역 or 별칭 or '비상장')
            z = ism.group(3).strip()        # 우변
            if _company_left_inverted(x, y, z):
                # 'X (Y): Z'가 거꾸로 — 좌변이 회사, 우변이 품목(회사-우선). 실제
                # RULE 12(품목 (지역): 회사)는 X=품목이라 여기 안 들어옴(회귀 0).
                company_from_title = x
                title_kind = "company_first"
                rm = _STANDARD_TITLE_RE.match(z)
                if rm:                       # 우변 품목에 지역 괄호가 붙어있으면 분리
                    item_raw = rm.group(1).strip()
                    region_part = rm.group(2).strip()
                else:                        # 우변에 지역 없으면 Y가 지역(별칭/비상장은 제외)
                    item_raw = z
                    region_part = "" if (y == "비상장" or _is_known_company(y)) else y
                warnings.append("company_first_inverted_inline")
            else:
                item_raw = x
                region_part = y
                inline_stocks_raw = z
                title_kind = "item_first"
        else:
            sm = _STANDARD_TITLE_RE.match(title)
            if sm:
                item_raw = sm.group(1).strip()
                region_part = sm.group(2).strip()
                title_kind = "item_first"
            else:
                # RULE 13 — '<품목> (<지역>) <suffix>' (e.g. 'H형강 (전국) 합산').
                # Reject when LEFT has '/' or suffix starts with ':'
                # (both signal synthesis-post shape).
                ttm = _TRAILING_TEXT_TITLE_RE.match(title)
                if (
                    ttm
                    and "/" not in ttm.group(1)
                    and not ttm.group(3).lstrip().startswith(":")
                ):
                    item_raw = (ttm.group(1).strip() + " " + ttm.group(3).strip()).strip()
                    region_part = ttm.group(2).strip()
                    title_kind = "item_first"
                    warnings.append("title_has_trailing_text")
                elif "(" not in title and ")" not in title and " : " in title:
                    # RULE 14 — company-first with no region paren at all.
                    cnrm = _COMPANY_NO_REGION_RE.match(title)
                    if cnrm:
                        company_from_title = cnrm.group(1).strip()
                        item_raw = cnrm.group(2).strip()
                        region_part = ""
                        title_kind = "company_first"
                        warnings.append("title_no_region")
                    else:
                        return ParsedAlert(
                            direction=direction,
                            status=status,
                            period_start=period_start,
                            period_end=period_end,
                            period_kind=period_kind,
                            title_kind="unknown",
                            item=_norm_ws(title),
                            item_raw=title,
                            is_composite=False,
                            parse_warnings=["title_format_unknown"],
                        )
                else:
                    return ParsedAlert(
                        direction=direction,
                        status=status,
                        period_start=period_start,
                        period_end=period_end,
                        period_kind=period_kind,
                        title_kind="unknown",
                        item=_norm_ws(title),
                        item_raw=title,
                        is_composite=False,
                        parse_warnings=["title_format_unknown"],
                    )

    parts = [p.strip() for p in _COMPOSITE_SPLIT_RE.split(item_raw) if p.strip()]
    is_composite = len(parts) > 1
    composite_parts = parts if is_composite else []
    item = _norm_ws(item_raw)

    region, country, regions, countries = _parse_region(region_part)

    if title_kind == "company_first":
        # 회사 접두를 stocks로 — 복합사('넥스틸 / 세아제강')는 _parse_stocks가 분리.
        stocks, stocks_meta, has_etc, stock_warns = _parse_stocks(company_from_title or "")
        warnings.extend(stock_warns)
    elif inline_stocks_raw is not None:
        # RULE 12 — stocks list came inline with the title.
        stocks, stocks_meta, has_etc, stock_warns = _parse_stocks(inline_stocks_raw)
        warnings.extend(stock_warns)
    else:
        stocks_raw: str | None = None
        for i in range(title_end_idx + 1, final_idx):
            sm2 = _STOCKS_LINE_RE.match(lines[i])
            if sm2:
                stocks_raw = sm2.group(1)
                break
        if stocks_raw is None:
            stocks, stocks_meta, has_etc = [], {}, False
        else:
            stocks, stocks_meta, has_etc, stock_warns = _parse_stocks(stocks_raw)
            warnings.extend(stock_warns)

    # Free-text commentary: lines between stocks (or title for
    # company_first / inline_stocks) and the final status line, skipping
    # blanks and the stocks line itself.
    commentary_lines: list[str] = []
    seen_stocks_line = title_kind == "company_first" or inline_stocks_raw is not None
    for i in range(title_end_idx + 1, final_idx):
        line = lines[i].strip()
        if not line:
            continue
        if _STOCKS_LINE_RE.match(lines[i]):
            seen_stocks_line = True
            continue
        if seen_stocks_line:
            commentary_lines.append(line)
    commentary = "\n".join(commentary_lines) if commentary_lines else None

    return ParsedAlert(
        direction=direction,
        status=status,
        period_start=period_start,
        period_end=period_end,
        period_kind=period_kind,
        title_kind=title_kind,
        item=item,
        item_raw=item_raw,
        is_composite=is_composite,
        composite_parts=composite_parts,
        region=region,
        country=country,
        regions=regions,
        countries=countries,
        stocks=stocks,
        stocks_meta=stocks_meta,
        has_etc=has_etc,
        commentary=commentary,
        parse_warnings=warnings,
    )
