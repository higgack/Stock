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
    값·목표가·동종사 수치까지 걸려 늘 뜨는 경고가 된다(#25·#260). 그래서
    지표 라벨 바로 뒤에 오는 첫 숫자만 보고, 그 앞뒤에 '현재 값이 아님'을
    뜻하는 낱말(평균·목표·과거·연도·시나리오…)이 있으면 판정하지 않는다.
  · **고치지 않고 말한다.** 원문은 그대로 두고 섹션 끝에 어느 값이 기준과
    얼마나 다른지 적는다 — 다른 원천(네이버·DART)의 값을 정당하게 인용했을
    수도 있으므로 '환각'이라 단정하지 않는다(#165).
  · 통화가 다르게 적힌 값(원화 종목의 달러 시총 등)·소수 통화 단위
    (GBp 등)는 판정하지 않는다 — 환산 기준을 모르면 대조할 수 없다(#54).
  · 이 가드가 쓴 줄은 다시 재지 않는다 — 제 출력의 인용을 결함으로 세면
    재실행마다 늘어난다(#250·#268).

순수 함수만 둔다 — 네트워크 0 · LLM 0. 기준값은 호출부가 넘긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 이 가드가 쓰는 줄의 머리. 추출기는 이 머리로 시작하는 줄을 건너뛴다.
MARKER = "🔎 **수치 대조**"
_ITEM_PREFIX = "  · "

# 옛 시총 교차검사(`_canonical_crosscheck`, 2026-10-22 이 가드로 대체)가
# 아카이브에 남긴 경고 줄 — 감사가 옛 리포트를 다시 잴 때 그 줄 안의 기준값
# 인용을 리포트 본문으로 세지 않게 같이 건너뛴다.
_LEGACY_SKIP = ("⚠️ [시가총액 불일치", "⚠️ <b>가격 환각 의심", "⚠️ **가격 환각 의심")

# 지표별 허용 차이(기준 대비 비율). 값을 옮기다 생기는 반올림('약 29배')은
# 통과하고, 단위 실수·옛 값·다른 기준은 걸리게 잡았다. ⚠️ 실측이 아니라
# 출발점이다 — `bot.scripts.report_guard_audit` 가 아카이브로 발화율을 재
# 고쳐 간다(#12).
TOLERANCE = {
    "price": 0.05,
    "market_cap": 0.30,
    "per": 0.20,
    "fwd_per": 0.20,
    "pbr": 0.20,
    "eps": 0.20,
    "high_52w": 0.05,
    "low_52w": 0.05,
}

LABEL_KO = {
    "price": "현재가",
    "market_cap": "시가총액",
    "per": "PER(후행)",
    "fwd_per": "PER(선행)",
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
    "fwd_per": "forward_pe",
    "pbr": "price_to_book",
    "eps": "trailing_eps",
    "high_52w": "high_52w",
    "low_52w": "low_52w",
}

_NUM = r"[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?"

# 라벨. 영문 약어는 앞뒤가 알파벳이면 안 된다(SUPER·PERFORMANCE·EPSILON).
# 앞뒤 '/' 도 막는다 — 'PER/PBR 28.9/1.2' 처럼 묶인 라벨은 어느 값이 누구
# 것인지 모른다.
_LABELS: list[tuple[str, re.Pattern]] = [
    ("high_52w", re.compile(r"52\s*주\s*(?:최고가?|고가|신고가)|52[\s-]*(?:week|wk|W)\s*high", re.I)),
    ("low_52w", re.compile(r"52\s*주\s*(?:최저가?|저가|신저가)|52[\s-]*(?:week|wk|W)\s*low", re.I)),
    ("price", re.compile(r"현재\s*가(?!치)|현재\s*주가|현\s*주가|current\s*price", re.I)),
    ("market_cap", re.compile(r"시가\s*총액|시총|market\s*cap(?:italization)?", re.I)),
    ("per", re.compile(r"(?<![A-Za-z/])(?:PER|P/E)(?![A-Za-z/])")),
    ("pbr", re.compile(r"(?<![A-Za-z/])(?:PBR|P/B)(?![A-Za-z/])")),
    ("eps", re.compile(r"(?<![A-Za-z/])EPS(?![A-Za-z/])")),
]

# 라벨 앞 창·라벨과 숫자 사이에 있으면 '현재 값'이 아니다 → 판정하지 않는다.
_SKIP_WORDS = re.compile(
    r"평균|업종|섹터|산업|피어|경쟁|동종|업계|목표|적정|타깃|과거|역사|밴드|"
    r"중앙|고점|저점|당시|이전|직전|전년|작년|성장|증가|감소|변화|증감|"
    r"시나리오|가정|만약|할인|프리미엄|적용|역산|대비|비교|기준\s*연도|"
    r"상단|하단|범위|레인지|희석|조정|"
    r"peer|target|histor|avg|average|median|sector|industry|band|growth|"
    r"bull|bear|base|scenario|implied|discount|premium|range|"
    r"(?<!\d)(?:19|20)\d{2}(?!\d)|FY|[1-4]\s*Q|Q\s*[1-4]|[12]\s*H\b|분기|반기|"
    r"상반기|하반기|연간",
    re.I,
)
# 이 낱말이 있으면 선행(Forward) PER — 없으면 후행으로 본다.
_FWD_WORDS = re.compile(
    r"forward|fwd|선행|12\s*M|NTM|추정|예상|컨센|전망", re.I)
_TRAIL_WORDS = re.compile(r"TTM|LTM|trailing|후행|현재", re.I)

# 소제목이 이 말을 담으면 그 아래는 비교·동종사 문단이다(소제목이 바뀔 때까지).
_COMPARE_HEADING = re.compile(
    r"비교|동종|경쟁|피어|peer|comps|업종|섹터|밸류에이션\s*밴드|시나리오|DCF", re.I)
# 한 줄 안에 이 말이 있으면 비교 문장이다.
_COMPARE_LINE = re.compile(r"경쟁사|동종|피어|peer|comps|업계\s*평균|vs\.?", re.I)

_PRE_WINDOW = 14
_MAX_GAP = 18

_UNIT_RE = re.compile(
    r"\s*(조|억|만|兆|億|萬|trillion|billion|million|tn|bn|mn|[TBMK](?![A-Za-z])|"
    r"배|times|[xX×](?![A-Za-z])|%)?",
)
_SCALE = {
    "조": 1e12, "兆": 1e12, "억": 1e8, "億": 1e8, "만": 1e4, "萬": 1e4,
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6, "k": 1e3,
}

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
# ¥ 는 엔·위안 겸용 — 기준 통화가 둘 중 하나일 때만 그 통화로 본다.
_YEN_SIGN = re.compile(r"¥|￥")
# 소수 단위 통화(펜스·아고롯·센트) — 표기가 갈려 금액은 판정하지 않는다.
_MINOR_UNIT_CCY = {"GBp", "GBX", "ILA", "ZAc", "ZAC"}

_MONEY_METRICS = {"price", "market_cap", "eps", "high_52w", "low_52w"}
_MULTIPLE_METRICS = {"per", "fwd_per", "pbr"}


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
    """값 주변 한 줄 발췌 — 앞뒤를 잘랐으면 … 로 표시한다(#45)."""
    a = max(0, start - width // 2)
    b = min(len(line), end + width // 2)
    s = line[a:b].strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(line) else "")


def _normalize_ccy(ccy: str) -> str:
    return "GBP" if ccy in ("GBp", "GBX") else ccy


def _quote_value(metric: str, num: float, unit: str, ccy_text: str,
                 ref_ccy: str) -> float | None:
    """원문 숫자 → 기준과 같은 단위의 값. 판정할 수 없으면 None."""
    u = (unit or "").strip()
    if u == "%":
        return None
    if metric in _MULTIPLE_METRICS:
        # 배수 칸에 금액 단위·통화가 붙어 있으면 배수가 아니다.
        if u and u.lower() not in ("배", "times", "x", "×"):
            return None
        if _detect_ccy(ccy_text, ref_ccy):
            return None
        return num if num > 0 else None
    # 금액 지표
    if u.lower() in ("배", "times", "x", "×"):
        return None
    if ref_ccy in _MINOR_UNIT_CCY:
        return None
    quoted_ccy = _detect_ccy(ccy_text, ref_ccy)
    if quoted_ccy and quoted_ccy != _normalize_ccy(ref_ccy):
        return None
    scale = _SCALE.get(u if u in _SCALE else u.lower(), 1.0) if u else 1.0
    if metric == "market_cap":
        if not u and abs(num) < 1e6:
            # 단위 없는 작은 수(‘시총 36.9’)는 단위를 빠뜨린 것 — 못 잰다.
            return None
        return num * scale
    if metric in ("price", "high_52w", "low_52w"):
        if u and u not in ("만", "萬"):
            return None
        return num * scale if num > 0 else None
    if metric == "eps":
        if u and u not in ("만", "萬"):
            return None
        return num * scale
    return None


def find_mismatches(body: str, canonical: dict | None) -> list[Finding]:
    """`body` 에서 라벨이 붙은 값을 찾아 기준과 허용 차이 밖인 것만 돌려준다.

    순수 함수. 기준이 없는 지표는 재지 않는다(재지 못한 것은 통과가 아니라
    판정하지 않은 것이다 — 호출부가 계수를 원하면 `scan` 을 쓸 것).
    """
    return [f for f, ok in scan(body, canonical) if not ok]


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
        if stripped.startswith("#"):
            in_compare = bool(_COMPARE_HEADING.search(stripped))
            continue
        # 굵게 쓴 한 줄 소제목('**동종업계 비교**')도 소제목으로 본다.
        if re.fullmatch(r"\*\*[^*]+\*\*:?", stripped):
            in_compare = bool(_COMPARE_HEADING.search(stripped))
            continue
        if not stripped or in_compare:
            continue
        if stripped.startswith(MARKER) or (
                stripped.startswith(_ITEM_PREFIX.strip() + " ") and "↔ 기준" in stripped):
            continue
        if any(stripped.startswith(p) for p in _LEGACY_SKIP):
            continue
        if _COMPARE_LINE.search(line):
            continue
        taken: list[tuple[int, int]] = []
        for metric, lab in _LABELS:
            for m in lab.finditer(line):
                s, e = m.span()
                if any(a <= s < b for a, b in taken):
                    continue  # '52주 최고가' 안의 '가' 등 겹친 라벨
                pre = line[max(0, s - _PRE_WINDOW):s]
                rest = line[e:]
                nm = re.match(
                    r"(?P<gap>[^\d\n%\-−]{0," + str(_MAX_GAP) + r"}?)"
                    r"(?P<num>" + _NUM + r")", rest)
                if not nm:
                    continue
                gap = nm.group("gap")
                if _SKIP_WORDS.search(pre) or _SKIP_WORDS.search(gap):
                    continue
                num = _to_float(nm.group("num"))
                if num is None:
                    continue
                after = rest[nm.end():]
                um = _UNIT_RE.match(after)
                unit = (um.group(1) or "") if um else ""
                tail = after[um.end():um.end() + 6] if um else after[:6]
                # '28.9배 →' 처럼 바로 뒤가 다른 숫자로 이어지는 범위('28~32배')
                if re.match(r"\s*[~～\-–]\s*\d", tail):
                    continue
                m_key = metric
                if metric == "per":
                    ctx = pre + line[s:e] + gap
                    if _FWD_WORDS.search(ctx):
                        m_key = "fwd_per"
                    elif not _TRAIL_WORDS.search(ctx) and _FWD_WORDS.search(tail):
                        m_key = "fwd_per"
                if metric == "eps" and _FWD_WORDS.search(pre + gap):
                    continue  # 선행 EPS 는 기준값이 없다
                ref = canonical.get(CANON_KEY[m_key])
                if not isinstance(ref, (int, float)) or ref != ref or ref == 0:
                    continue
                if m_key in _MULTIPLE_METRICS and ref <= 0:
                    continue
                q = _quote_value(m_key, num, unit, gap + tail, ref_ccy)
                if q is None:
                    continue
                taken.append((s, e + nm.end()))
                ok = _within(m_key, q, float(ref))
                out.append((Finding(m_key, q, float(ref),
                                    _excerpt(line, s, e + nm.end() + len(unit))),
                            ok))
    return out


def _within(metric: str, quoted: float, ref: float) -> bool:
    tol = TOLERANCE[metric]
    # 부호가 다르면 비율이 음수라 |비율−1| > 1 — 아래 검사가 이미 어긋남으로 본다.
    if metric == "market_cap":
        # 시총은 크기 비율로 — 단위 실수(10배·100배)가 주된 실패다.
        lo, hi = sorted((abs(quoted), abs(ref)))
        return lo > 0 and hi / lo <= 1.0 + tol
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
