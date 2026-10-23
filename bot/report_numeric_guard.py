"""분석 리포트 수치 대조 가드 — 라벨이 붙은 숫자를 분석 기준 숫자와 맞춘다.

분석가 프롬프트 맨 위에는 FACTUAL ANCHOR(현재가·시총·52주 고저·PER·PBR·
EPS, yfinance `.info`)가 실리고 "그대로 인용하라"는 규칙이 붙는다
(`agent_utils._build_factual_anchor`). 그런데 LLM 이 그 값을 옮기다 틀리거나
학습 시점의 옛 값을 쓰면, 그 사실을 알려 주는 것은 시총(KRW·JPY 한정)과
Trader 진입·손절가 둘뿐이었다. 7축 검증의 ①숫자정확성 · ⑥데이터 vs 환각을
사람이 매번 눈으로 하던 것을 기계가 하게 한다(외부 레포 검토 2026-10-22 —
FinRobot `report_drift.py` 의 아이디어를 우리 규칙으로 다시 짰다. 코드 복사
없음).

설계 원칙:
  · **라벨이 붙은 값만** 잰다. 리포트의 모든 숫자를 재면 업종 평균·과거
    값·목표가·동종사 수치까지 걸려 늘 뜨는 경고가 된다(#25·#260). 지표 라벨
    바로 뒤의 첫 숫자만 보고, 그 숫자가 '현재 값' 이라고 읽힐 때만 잰다 —
    라벨 앞·라벨과 숫자 사이·숫자 뒤에 다른 뜻(평균·목표·연도·분기·조건·
    기간 단위)이 붙으면 판정하지 않는다. 오탐이 놓침보다 나쁘다.
  · **고치지 않고 말한다.** 원문은 그대로 두고 섹션 끝에 어느 값이 기준과
    얼마나 다른지 적는다 — 다른 원천(네이버·DART)의 값을 정당하게 인용했을
    수도 있으므로 '환각'이라 단정하지 않는다(#165).
  · **기준값은 앵커가 실제로 보여 준 것만** 쓴다. 선행 PER 은 앵커에 없으므로
    재지 않는다(국내 선행 PER 원천은 네이버 컨센서스로 정해져 있다, #197·#206).
  · 통화가 다르게 적힌 값(원화 종목의 달러 시총 등)·소수 단위 통화(GBp 등)의
    금액은 판정하지 않는다 — 환산 기준을 모르면 대조할 수 없다(#54).
  · 이 가드가 쓴 줄은 다시 재지 않는다(#250·#268).

⚠️ 못 보는 축(#274): 비교 낱말 없이 **다른 회사 이름만** 앞에 둔 값
('SK하이닉스 PER 12.1배') 은 우리 회사 값과 구별하지 못한다 — 비교 소제목·
비교 낱말이 있는 줄은 건너뛰지만, 그 밖은 경고 표본으로 드러난다.
`bot.scripts.report_guard_audit` 가 아카이브로 발화율과 표본을 잰다.

순수 함수만 둔다 — 네트워크 0 · LLM 0. 기준값은 호출부가 넘긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 이 가드가 쓰는 줄의 머리. 추출기는 이 머리로 시작하는 줄을 건너뛴다.
MARKER = "🔎 **수치 대조**"
_ITEM_PREFIX = "  · "

# 지표별 허용 차이(기준 대비 비율). 값을 옮기다 생기는 반올림('약 29배')은
# 통과하고, 단위 실수·옛 값·다른 기준은 걸리게 잡았다. ⚠️ 실측이 아니라
# 출발점이다 — `bot.scripts.report_guard_audit` 가 아카이브로 발화율을 재
# 고쳐 간다(#12).
TOLERANCE = {
    "price": 0.05,
    "market_cap": 0.30,
    "per": 0.20,
    "pbr": 0.20,
    "eps": 0.20,
    "high_52w": 0.05,
    "low_52w": 0.05,
}

LABEL_KO = {
    "price": "현재가",
    "market_cap": "시가총액",
    "per": "PER(후행)",
    "pbr": "PBR",
    "eps": "EPS(후행)",
    "high_52w": "52주 최고",
    "low_52w": "52주 최저",
}

# 기준값 dict 의 키(analyzer._extract_canonical 이 채운다).
CANON_KEY = {
    "price": "current_price",
    "market_cap": "market_cap",
    "per": "trailing_pe",
    "pbr": "price_to_book",
    "eps": "trailing_eps",
    "high_52w": "high_52w",
    "low_52w": "low_52w",
}

_NUM = r"[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?"
_NUM_RE = re.compile(_NUM)

# 라벨. 영문 약어는 앞뒤가 알파벳·'/' 면 안 된다(SUPER·PERFORMANCE·EPSILON ·
# 'PER/PBR 28.9/1.2' 처럼 묶인 라벨은 어느 값이 누구 것인지 모른다).
_LABELS: list[tuple[str, re.Pattern]] = [
    ("high_52w", re.compile(r"52\s*주\s*(?:최고가?|고가|신고가)|52[\s-]*(?:week|wk|W)\s*high", re.I)),
    ("low_52w", re.compile(r"52\s*주\s*(?:최저가?|저가|신저가)|52[\s-]*(?:week|wk|W)\s*low", re.I)),
    ("price", re.compile(r"현재\s*가(?!치)|현재\s*주가|현\s*주가|current\s*price", re.I)),
    ("market_cap", re.compile(r"시가\s*총액|시총|market\s*cap(?:italization)?", re.I)),
    ("per", re.compile(r"(?<![A-Za-z/])(?:PER|P/E)(?![A-Za-z/])")),
    ("pbr", re.compile(r"(?<![A-Za-z/])(?:PBR|P/B)(?![A-Za-z/])")),
    ("eps", re.compile(r"(?<![A-Za-z/])EPS(?![A-Za-z/])")),
]
# 라벨 이름 아무거나 — 라벨과 숫자 사이에 또 다른 라벨이 끼면 그 숫자는 뒤 라벨 것이다.
_ANY_LABEL = re.compile("|".join(f"(?:{p.pattern})" for _, p in _LABELS), re.I)

# 라벨 앞 창·라벨과 숫자 사이에 있으면 '현재 값'이 아니다 → 판정하지 않는다.
_SKIP_WORDS = re.compile(
    r"평균|업종|섹터|산업|피어|경쟁|동종|업계|목표|적정|타깃|과거|역사|밴드|"
    r"중앙|고점|저점|당시|이전|직전|전년|작년|성장|증가|감소|변화|증감|"
    r"시나리오|가정|만약|할인|프리미엄|적용|역산|대비|비교|기준\s*연도|"
    r"상단|하단|범위|레인지|희석|조정|선행|추정|예상|컨센|전망|가이던스|"
    r"peer|target|histor|avg|average|median|sector|industry|band|growth|"
    r"bull|bear|base|scenario|implied|discount|premium|range|forward|fwd|"
    r"estimate|consensus|guidance|beat|miss|came|reported|surprise|"
    r"(?<!\d)(?:19|20)\d{2}(?!\d)|FY|[1-4]\s*Q|Q\s*[1-4]|[12]\s*H\b|분기|반기|"
    r"상반기|하반기|연간|NTM|12\s*M",
    re.I,
)
# 숫자 바로 뒤가 이것이면 그 숫자는 값이 아니라 기간·순위·횟수·이평선 등이다
# ('52주' · '200일' · '10년 평균' · '1Q26' · '5일 연속' · '2026E').
_NUM_SUFFIX_NOT_VALUE = re.compile(
    r"\s*(?:주(?!당)|일|년|개월|거래일|분기|반기|위|명|회|선|차|번|종|곳|개|"
    r"대|세|%|[EFQH](?![A-Za-z])|[-−~～–]\s*\d)", re.I)
# 숫자(와 단위) 뒤 짧은 창에 이게 있으면 조건·임계·분기 실적·가이던스다
# ('PER 12배 적용 시' · 'PBR 0.9배까지 하락' · 'EPS $1.62 in Q3' ·
# 'PER 15.2배(2026E)'). ⚠️ `_SKIP_WORDS` 전부를 걸지는 않는다 — 'PER 28.9배로
# 업종 평균 대비 할인' 의 업종·대비는 우리 값 **뒤**의 비교 대상이다.
_POST_SKIP = re.compile(
    r"까지|이하|이상|미만|초과|수준|적용|하락\s*시|상승\s*시|도달|돌파|가정|"
    r"분기|반기|(?<!\d)(?:19|20)\d{2}(?!\d)|\d{2}\s*E\b|[1-4]\s*Q|Q\s*[1-4]|"
    r"quarter|beat|miss|guidance|가이던스|surprise|서프라이즈|estimate|컨센|"
    r"예상|추정|전망|목표|선행|forward|fwd|last\s+year|year\s+ago",
    re.I,
)
_POST_WINDOW = 16
_POST_CUT = re.compile(r"[,;·]|(?<!\d)\.(?!\d)")

# 소제목이 이 말을 담으면 그 아래는 비교·동종사 문단이다(소제목이 바뀔 때까지).
_COMPARE_HEADING = re.compile(
    r"비교|동종|경쟁|피어|peer|comps|업종|섹터|밸류에이션\s*밴드|시나리오|DCF", re.I)
# 한 줄 안에 이 말이 있으면 여러 회사를 나란히 놓은 비교 문장이다.
_COMPARE_LINE = re.compile(r"경쟁사|동종|피어|peer|comps|업계\s*평균|vs\.?", re.I)
# 라벨과 숫자 사이에 끼면 그 숫자는 이 라벨 것이 아니다(값 없음 표시·나열 기호).
_GAP_BREAK = re.compile(r"N\s*/?\s*A|N\s*/?\s*M|·|/|&|,|;|\band\b|와\s|과\s|및", re.I)

_LIST_TAIL = re.compile(
    "(?:" + "|".join(f"(?:{p.pattern})" for _, p in _LABELS) + r")\s*[·/&,]\s*$", re.I)

_PRE_WINDOW = 14
_MAX_GAP = 12

# 단위. 긴 것을 먼저(백만 → 만). 영문 단어 단위는 대소문자를 가리지 않는다.
_UNIT_RE = re.compile(
    r"\s*(백만|십억|천억|조|억|만|兆|億|萬|trillion|billion|million|tn|bn|mn|"
    r"[TBMK](?![A-Za-z])|배|times|[xX×](?![A-Za-z])|%)?",
    re.I,
)
_SCALE = {
    "조": 1e12, "兆": 1e12, "천억": 1e11, "십억": 1e9, "억": 1e8, "億": 1e8,
    "백만": 1e6, "만": 1e4, "萬": 1e4,
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6, "k": 1e3,
}
_MULTIPLE_UNITS = {"배", "times", "x", "×"}
# 한국식 복합 금액('1조 9,000억' · '9만 8,500원')의 뒤 조각 — 앞 단위보다 작아야 한다.
_TAIL_PIECE = re.compile(r"\s*(" + _NUM + r")\s*(억|億|만|萬)?")

# (정규식, 통화) — 앞에서부터 처음 맞는 것. 복합 기호($ 앞 접두)를 먼저.
_CCY_MARKS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"NT\$|TWD|대만\s*달러|新台幣"), "TWD"),
    (re.compile(r"HK\$|HKD|홍콩\s*달러|港元"), "HKD"),
    (re.compile(r"A\$|AUD|호주\s*달러"), "AUD"),
    (re.compile(r"C\$|CAD|캐나다\s*달러"), "CAD"),
    (re.compile(r"S\$|SGD|싱가포르\s*달러"), "SGD"),
    (re.compile(r"₩|KRW|원(?![가-힣])"), "KRW"),
    (re.compile(r"\$|USD|달러"), "USD"),
    (re.compile(r"円|JPY|엔(?![가-힣])"), "JPY"),
    (re.compile(r"위안|元|RMB|CNY"), "CNY"),
    (re.compile(r"€|EUR|유로"), "EUR"),
    (re.compile(r"£|GBP|파운드"), "GBP"),
    (re.compile(r"₹|INR|루피"), "INR"),
]
# 숫자·단위 바로 뒤의 통화 낱말 — 조사가 붙어도('25만원으로') 그 통화다.
_CCY_RIGHT_AFTER = [
    (re.compile(r"\s*원"), "KRW"), (re.compile(r"\s*엔"), "JPY"),
    (re.compile(r"\s*달러"), "USD"), (re.compile(r"\s*위안"), "CNY"),
]
# ¥ 는 엔·위안 겸용 — 기준 통화가 둘 중 하나일 때만 그 통화로 본다.
_YEN_SIGN = re.compile(r"¥|￥")
# 소수 단위 통화(펜스·아고롯·센트) — 표기가 갈려 금액은 판정하지 않는다.
_MINOR_UNIT_CCY = {"GBp", "GBX", "ILA", "ZAc", "ZAC"}

_MULTIPLE_METRICS = {"per", "pbr"}
# 후행 EPS 는 **명시될 때만** 잰다 — 리포트의 EPS 는 대개 분기 실적·추정치다.
_EPS_TRAILING = re.compile(r"TTM|LTM|trailing|후행|최근\s*12\s*개월|연환산", re.I)


@dataclass(frozen=True)
class Finding:
    metric: str          # TOLERANCE 의 키
    quoted: float        # 리포트에 적힌 값(기준 통화 단위로 환산)
    reference: float     # 기준값
    quote_text: str      # 원문 발췌(한 줄 안, 잘림 표시 포함)

    @property
    def gap_pct(self) -> float:
        return (self.quoted / self.reference - 1.0) * 100.0


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", "").replace("−", "-"))
    except ValueError:
        return None


def _detect_ccy(text: str, ref_ccy: str) -> str | None:
    for pat, ccy in _CCY_MARKS:
        if pat.search(text):
            return ccy
    if _YEN_SIGN.search(text):
        return ref_ccy if ref_ccy in ("JPY", "CNY") else "JPY"
    return None


def _excerpt(line: str, start: int, end: int, width: int = 60) -> str:
    """값 주변 한 줄 발췌 — 앞뒤를 잘랐으면 … 로 표시한다(#45). 굵게 표시는
    걷는다(잘린 자리에 짝 없는 `**` 가 남으면 글자 그대로 보인다)."""
    a = max(0, start - width // 2)
    b = min(len(line), end + width // 2)
    s = line[a:b].replace("**", "").strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(line) else "")


def _normalize_ccy(ccy: str) -> str:
    return "GBP" if ccy in ("GBp", "GBX") else ccy


def _scale_of(unit: str) -> float:
    if not unit:
        return 1.0
    return _SCALE.get(unit, _SCALE.get(unit.lower(), 1.0))


def _quote_value(metric: str, num: float, unit: str, quoted_ccy: str | None,
                 ref_ccy: str) -> float | None:
    """원문 숫자 → 기준과 같은 단위의 값. 판정할 수 없으면 None."""
    u = (unit or "").strip()
    is_mult_unit = u.lower() in _MULTIPLE_UNITS
    if metric in _MULTIPLE_METRICS:
        # 배수 칸엔 단위가 없거나 '배·x' 여야 한다('%'·금액 단위면 다른 값이다).
        if u and not is_mult_unit:
            return None
        return num if num > 0 else None
    # 금액 지표
    if is_mult_unit or ref_ccy in _MINOR_UNIT_CCY:
        return None
    if quoted_ccy and quoted_ccy != _normalize_ccy(ref_ccy):
        return None
    if metric == "market_cap":
        if not u and abs(num) < 1e6:
            return None            # 단위를 빠뜨린 작은 수('시총 36.9') — 못 잰다
        return num * _scale_of(u) if num > 0 else None
    # 가격·EPS — 큰 단위(억·조·B…)가 붙으면 다른 값을 가리킨 것이다.
    if u and u not in ("만", "萬"):
        return None
    if metric in ("price", "high_52w", "low_52w") and num <= 0:
        return None
    return num * _scale_of(u)


def find_mismatches(body: str, canonical: dict | None) -> list[Finding]:
    """`body` 에서 라벨이 붙은 값을 찾아 기준과 허용 차이 밖인 것만 돌려준다.

    순수 함수. 기준이 없는 지표는 재지 않는다(재지 못한 것은 통과가 아니라
    판정하지 않은 것이다 — 계수가 필요하면 `scan` 을 쓸 것).
    """
    return [f for f, ok in scan(body, canonical) if not ok]


def _heading_text(stripped: str) -> str | None:
    """소제목이면 그 글자, 아니면 None. `#` 머리 또는 한 줄짜리 `**굵게**`."""
    if stripped.startswith("#"):
        return stripped
    if re.fullmatch(r"\*\*[^*]+\*\*:?", stripped):
        return stripped
    return None


def scan(body: str, canonical: dict | None) -> list[tuple[Finding, bool]]:
    """판정한 모든 인용을 (Finding, 허용 안인가) 로 돌려준다 — 감사용."""
    if not body or not isinstance(canonical, dict):
        return []
    ref_ccy = str(canonical.get("currency") or "")
    out: list[tuple[Finding, bool]] = []
    in_compare = False
    for raw_line in body.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        head = _heading_text(stripped)
        if head is not None:
            in_compare = bool(_COMPARE_HEADING.search(head))
            continue
        if not stripped or in_compare:
            continue
        if stripped.startswith(MARKER) or (
                stripped.startswith(_ITEM_PREFIX.strip() + " ") and "↔ 기준" in stripped):
            continue
        if _COMPARE_LINE.search(line):
            continue
        for metric, lab in _LABELS:
            for m in lab.finditer(line):
                f = _judge_one(line, metric, m, canonical, ref_ccy)
                if f is not None:
                    out.append(f)
    return out


def _judge_one(line: str, metric: str, m: re.Match, canonical: dict,
               ref_ccy: str) -> tuple[Finding, bool] | None:
    s, e = m.span()
    pre = line[max(0, s - _PRE_WINDOW):s]
    if _SKIP_WORDS.search(pre):
        return None
    # 라벨이 나열의 뒤쪽이면('PBR·PER 1.2배·28.9배' · '현재가·52주 최고 …') 뒤
    # 숫자들이 어느 라벨 것인지 순서로만 짐작하게 된다 — 판정하지 않는다.
    if _LIST_TAIL.search(pre):
        return None
    # 라벨 뒤 첫 숫자까지 — 짧은 틈만 허용(_MAX_GAP).
    j = e
    lim = min(len(line), e + _MAX_GAP + 1)
    while j < lim and not line[j].isdigit() and line[j] not in "-−\n%":
        j += 1
    if j >= len(line) or not (line[j].isdigit() or line[j] in "-−"):
        return None
    gap = line[e:j]
    if _SKIP_WORDS.search(gap) or _GAP_BREAK.search(gap) or _ANY_LABEL.search(gap):
        return None
    nm = _NUM_RE.match(line, j)
    if not nm:
        return None
    # 숫자 자리에서 다른 라벨이 시작하면('현재가·52주 최고…') 그 숫자는 라벨의 일부다.
    if _ANY_LABEL.match(line, nm.start()):
        return None
    num = _to_float(nm.group(0))
    if num is None:
        return None
    um = _UNIT_RE.match(line, nm.end())
    unit = (um.group(1) or "") if um else ""
    pos = um.end() if um else nm.end()
    # 숫자 바로 뒤가 기간·순위·횟수 단위면 값이 아니다('52주' · '10년' · '1Q26').
    if not unit and _NUM_SUFFIX_NOT_VALUE.match(line, nm.end()):
        return None
    if re.match(r"\s*[-−~～–]\s*\d", line[pos:pos + 4]):
        return None                     # 범위('28~32배' · '1.0-1.2x')
    # 한국식 복합 금액 — 뒤 조각을 더한다('1조 9,000억' · '9만 8,500원').
    if unit in ("조", "兆", "억", "億", "만", "萬"):
        tp = _TAIL_PIECE.match(line, pos)
        if tp:
            t_unit = tp.group(2) or ""
            t_num = _to_float(tp.group(1))
            if t_num is not None and t_num >= 0 and (
                    (unit in ("조", "兆") and t_unit in ("억", "億", "만", "萬"))
                    or (unit in ("억", "億") and t_unit in ("만", "萬"))
                    or (unit in ("만", "萬") and not t_unit)):
                num = num * _scale_of(unit) + t_num * _scale_of(t_unit)
                unit = ""
                pos = tp.end()
    # 숫자 뒤 짧은 창 — 다음 쉼표·마침표·나열 기호나 다음 라벨에서 끊는다('현재가
    # 187,000원, 목표가 240,000원' 의 '목표' 는 다음 항목 것이다).
    post = line[pos:pos + _POST_WINDOW]
    cut = _POST_CUT.search(post)
    if cut:
        post = post[:cut.start()]
    nxt = _ANY_LABEL.search(post)
    if nxt:
        post = post[:nxt.start()]
    if _POST_SKIP.search(post):
        return None
    if metric == "eps" and not _EPS_TRAILING.search(pre + line[s:e] + gap + post):
        return None
    # 통화 — 틈·숫자 바로 뒤(조사가 붙어도)에서 읽는다.
    quoted_ccy = _detect_ccy(gap, ref_ccy)
    if quoted_ccy is None:
        for pat, ccy in _CCY_RIGHT_AFTER:
            if pat.match(line, pos):
                quoted_ccy = ccy
                break
    if quoted_ccy is None:
        quoted_ccy = _detect_ccy(line[pos:pos + 4], ref_ccy)
    ref = canonical.get(CANON_KEY[metric])
    if not isinstance(ref, (int, float)) or ref != ref or ref == 0:
        return None
    if metric in _MULTIPLE_METRICS and ref <= 0:
        return None
    q = _quote_value(metric, num, unit, quoted_ccy, ref_ccy)
    if q is None:
        return None
    ok = _within(metric, q, float(ref))
    return (Finding(metric, q, float(ref), _excerpt(line, s, pos)), ok)


def _finite(v):
    return v if isinstance(v, (int, float)) and v == v and abs(v) != float("inf") else None


def canonical_from_info(info: dict | None) -> dict:
    """yfinance `.info` 꼴 dict → 가드 기준값. 순수.

    FACTUAL ANCHOR(`agent_utils._build_factual_anchor`)가 분석가에게 보여 준
    값만 남긴다 — 앵커가 숨기거나 쓰지 말라고 한 값을 기준으로 삼으면 그
    지시를 **따른** 분석가가 경고를 받는다(독립 리뷰 2026-10-22):
      · PER 은 앵커처럼 0~10000 만. PBR 은 0~1000(앵커는 음수도 보여 주지만
        음수 배수는 대조할 뜻이 없어 뺀다 — 놓칠 뿐 오탐은 없다).
      · 선행 PER 은 앵커에 없다(국내 선행 원천은 네이버 컨센서스, #206).
      · 52주 고저는 앵커의 '⚠️데이터오류' 규칙(현재가의 1% 미만 · 최저>현재가
        ×1.005 · 최고<현재가×0.99)을 그대로 따른다.
      · 앵커가 현재가 글리치를 선언하면(분석가에게 보정 현재가를 쓰라고
        한다) 현재가·52주는 뺀다.
    분석기(`analyzer._extract_canonical`)와 감사 CLI 가 이 함수 하나를 쓴다(#38).
    """
    info = info or {}
    out: dict = {}
    px = _finite(info.get("currentPrice") or info.get("regularMarketPrice"))
    mc = _finite(info.get("marketCap"))
    if mc and mc > 0:
        out["market_cap"] = float(mc)
    if px and px > 0:
        out["current_price"] = float(px)
    pe = _finite(info.get("trailingPE"))
    if pe is not None and 0 < pe < 10000:
        out["trailing_pe"] = float(pe)
    pb = _finite(info.get("priceToBook"))
    if pb is not None and 0 < pb < 1000:
        out["price_to_book"] = float(pb)
    eps = _finite(info.get("trailingEps"))
    if eps is not None and eps != 0:
        out["trailing_eps"] = float(eps)
    hi, lo = _finite(info.get("fiftyTwoWeekHigh")), _finite(info.get("fiftyTwoWeekLow"))
    if px and px > 0:
        if hi is not None and hi >= px * 0.01 and hi >= px * 0.99:
            out["high_52w"] = float(hi)
        if lo is not None and lo >= px * 0.01 and lo <= px * 1.005:
            out["low_52w"] = float(lo)
        try:
            from bot.price_sanity import price_outlier_vs_refs
            glitch = price_outlier_vs_refs(
                px, lo, hi, info.get("fiftyDayAverage"),
                info.get("twoHundredDayAverage"))
        except Exception:                                   # noqa: BLE001
            glitch = False
        if glitch:
            for k in ("current_price", "high_52w", "low_52w"):
                out.pop(k, None)
    return out


def _within(metric: str, quoted: float, ref: float) -> bool:
    tol = TOLERANCE[metric]
    if metric == "market_cap":
        # 시총은 크기 비율로(대칭) — 단위 실수(10배·100배)가 주된 실패다.
        lo, hi = sorted((abs(quoted), abs(ref)))
        return lo > 0 and hi / lo <= 1.0 + tol
    # 부호가 다르면 비율이 음수라 |비율−1| > 1 — 어긋남으로 본다.
    return abs(quoted / ref - 1.0) <= tol


def _fmt(metric: str, v: float, sym: str) -> str:
    if metric in _MULTIPLE_METRICS:
        return f"{v:,.2f}배"
    if metric == "market_cap":
        a = abs(v)
        if a >= 1e12:
            return f"{sym}{v / 1e12:,.2f}조"
        if a >= 1e8:
            return f"{sym}{v / 1e8:,.0f}억"
        return f"{sym}{v:,.0f}"
    return f"{sym}{v:,.2f}" if abs(v) < 1000 else f"{sym}{v:,.0f}"


def render_note(findings: list[Finding], currency_symbol: str = "",
                limit: int = 6) -> str:
    """섹션 끝에 붙일 대조 결과. 없으면 빈 문자열."""
    if not findings:
        return ""
    head = (f"{MARKER} — 아래 {len(findings)}개 값이 분석 기준 숫자(FACTUAL"
            f" ANCHOR · yfinance)와 다릅니다. 다른 원천·기준을 인용했거나 잘못"
            f" 옮긴 값일 수 있으니 기준값으로 확인해 주십시오.")
    lines = [head]
    for f in findings[:limit]:
        lines.append(
            f"{_ITEM_PREFIX}{LABEL_KO[f.metric]} {_fmt(f.metric, f.quoted, currency_symbol)}"
            f" ↔ 기준 {_fmt(f.metric, f.reference, currency_symbol)}"
            f" ({f.gap_pct:+.0f}%) — 「{f.quote_text}」")
    if len(findings) > limit:
        lines.append(f"{_ITEM_PREFIX}외 {len(findings) - limit}건")
    return "\n".join(lines)


def annotate(body: str, canonical: dict | None, currency_symbol: str = "") -> str:
    """원문은 그대로 두고, 어긋난 값이 있으면 섹션 끝에 대조 결과를 붙인다."""
    if not body or not isinstance(body, str):
        return body
    note = render_note(find_mismatches(body, canonical), currency_symbol)
    return f"{body.rstrip()}\n\n{note}" if note else body
