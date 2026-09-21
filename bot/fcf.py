"""잉여현금흐름(FCF) 단일 산출기 — 전 시장 공통.

사용자 2026-08-21: "FCF 를 밸류에이션탭에 분기/연간 모두 부채비율 밑에 …
분기실적탭에도 당기순이익 밑에 별도의 차트로 … 이건 모든 나라에 적용이야."

⚠️ 산식은 **여기 한 곳**이다. 화면이 셋(밸류에이션 분기표·연간표·분기실적
차트)인데 각자 계산하면 같은 회사가 화면마다 다른 FCF 를 갖는다(실수 #38).

산식(원천이 확정해 줬다 — FnGuide 실측 FY2025: 영업활동현금흐름 1,241 −
CAPEX 230 = FCF 1,011):

    FCF = 영업활동현금흐름 − |CAPEX|

⚠️ `abs()` 를 쓰는 이유. CAPEX 는 원천마다 부호 규약이 갈린다 — yfinance 는
음수(유출)로, FnGuide·DART 는 양수로 준다. 부호를 그대로 더하거나 빼면
**한쪽 시장에서 FCF 가 영업현금흐름의 두 배**가 된다(둘 다 '맞는 값'처럼
보여서 눈으로는 안 잡힌다, 실수 #34 의 부호판). CAPEX 는 정의상 유출이므로
크기만 쓴다.

⚠️ 원천이 `Free Cash Flow` 를 **직접** 주면 그걸 쓴다. 우리가 다시 계산하면
원천의 정의(임차자산·무형자산 포함 여부)와 갈라진다 — 비교표에 자체계산을
넣지 말라는 규칙(#32)의 같은 이유다.

⚠️ 재료가 없으면 **None**. 0 을 넣으면 '현금흐름 0' 이라는 없는 사실을
그린 게 된다(빈칸이 틀린 숫자보다 낫다).
"""

from __future__ import annotations

# yfinance 라인아이템 후보. 앞이 없으면 뒤를 쓴다.
# ⚠️ 이름은 yfinance 판올림마다 바뀐다 — 하나만 적으면 조용히 전 종목이
# 빈칸이 된다. 실제로 'Total Cash From Operating Activities'(구판) →
# 'Operating Cash Flow'(신판)로 바뀐 전례가 있다.
_FCF_NAMES = ("Free Cash Flow", "FreeCashFlow")
_OCF_NAMES = ("Operating Cash Flow",
              "Total Cash From Operating Activities",
              "Cash Flow From Continuing Operating Activities",
              "Net Cash Provided By Used In Operating Activities")
_CAPEX_NAMES = ("Capital Expenditure", "Capital Expenditures",
                "Purchase Of PPE", "Net PPE Purchase And Sale")


def _num(v):
    """숫자만 통과. 문자열·None·NaN 은 None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN 배제


def _first(row: dict, names: tuple[str, ...]):
    for nm in names:
        v = _num((row or {}).get(nm))
        if v is not None:
            return v
    return None


def fcf_from_row(row: dict | None) -> float | None:
    """현금흐름표 한 기간(dict) → FCF. 재료가 없으면 None.

    `row` 는 라인아이템명 → 값 매핑(yfinance `_df_to_rows` 산출과 동형).
    """
    if not row:
        return None
    direct = _first(row, _FCF_NAMES)
    if direct is not None:
        return direct                     # 원천이 직접 준 값이 정본
    return fcf_from_parts(_first(row, _OCF_NAMES), _first(row, _CAPEX_NAMES))


# DART 는 CAPEX 를 단일 계정으로 주지 않는다 — 취득이 자산 종류별로 온다.
# ⚠️ **유형자산취득만** 쓴다. 사용자가 신뢰 기준으로 제시한 FnGuide 산식이
# `CAPEX = 유형자산의증가` 이고 무형은 안 들어간다(LG이노텍 011070.KS 세 해
# 실측, #215).
# ⚠️ #215 는 여기에 "yfinance `Capital Expenditure`(PP&E 취득)와도 정의가
# 같아 시장 간 기준이 하나로 맞는다" 고 덧붙였는데 그건 **재지 않은
# 단언**이었다(#165). 2026-09-21 181710.KS 실측 8기간(분기 5·연간 3)에서
# `|yf CAPEX| = 유형 + 무형` 이 전부 성립했다 — yfinance 는 무형을 묶어
# 준다. 산식은 그대로 둔다(FnGuide 기준이 정본이고 사용자 결정이다) —
# 바뀐 건 **정의가 하나로 맞는다는 주장**뿐이고, 그 차이는 화면이 이미
# 말하며(#102·#186·#220) 감사는 `scripts.fcf_audit.bundled_intangible`
# 로 잰다.
_DART_CAPEX_KEY = "유형자산취득"
# ⚠️ 무형도 **상수**로 둔다. 감사의 정의차 판정(#396)이 이 계정을 읽는데
# 한쪽만 리터럴이면 canonical 이름이 바뀌는 날 `dart_capex` 는 계속 돌고
# 무형만 조용히 None 이 되어 그 판정이 통째로 죽는다(#214 가 중복 키로
# 지배주주자본을 몇 달 잃은 그 형태).
_DART_INTANGIBLE_KEY = "무형자산취득"


def dart_capex(fin: dict | None):
    """DART 재무 dict → CAPEX 크기(없으면 None). **단일 출처**.

    ⚠️ 화면(`dart_quarterly._attach_fcf`)과 감사(`scripts.fcf_audit
    .recompute_dart`)가 **둘 다 이걸** 부른다. 예전엔 각자 적어 놓고
    #215 로 화면만 유형자산취득으로 좁혀서, 감사가 무형까지 더한 값으로
    대조해 정상 종목을 ❌ 로 찍었다(2026-09-07 098070.KQ 4분기 전부 —
    차이가 정확히 그 분기 무형자산취득이었다). 감사가 판정을 **재계산**
    하면 제품과 다른 기준선을 비교한다(#169·#35).

    ⚠️ 값은 `_num` 을 통과시킨다 — 이 모듈의 다른 진입점과 **같은 규약**
    (bool 배제 · NaN → None · 숫자가 아니면 None)이어야 한다. 단일 출처가
    된 뒤로는 여기서 `float()` 가 던지면 화면 렌더 경로가 통째로 터진다.
    """
    v = _num((fin or {}).get(_DART_CAPEX_KEY))
    return None if v is None else abs(v)


# 두 탭의 FCF 기준 차이를 **화면이 말하는 한 문장** — 단일 출처(#38).
# ⚠️ 왜 필요한가: 2026-09-21 에 감사 ② 가 이 차이를 ❌ 로 찍던 것을 ✅ 로
# 내렸다(#396). 그 근거가 "화면이 이미 말한다" 였는데, 각주는 '값이 다를 수
# 있습니다' 까지만 적고 **원인도 크기도** 말하지 않았다 — 그러면 측정한 사실이
# 전체 로그에만 남고 사용자는 또 묻는다(#202 '다르다'만 말하면 안 통한다 ·
# #186·#209 같은 질문이 세 번 왔다). 판정을 내릴 때 그 근거가 참이 되게 만든다.
# ⚠️ 범위를 넘겨 주장하지 않는다(#165) — 우리가 잰 것은 일부 종목이고,
# 모든 발행사가 그런지는 재지 않았다.
CAPEX_BASIS_NOTE = (
    "두 탭의 <b>FCF 기준이 다릅니다</b> — DART 쪽 CAPEX 는 "
    "<b>유형자산 취득만</b>이고(FnGuide 산식) yfinance CAPEX 에는 "
    "<b>무형자산 취득</b>이 함께 들어오는 경우가 있어(일부 종목 실측) "
    "그만큼 차이가 납니다")


def dart_intangible(fin: dict | None):
    """DART 재무 dict → 무형자산취득 크기(없으면 None). `dart_capex` 의 짝.

    ⚠️ FCF 산식에는 **안 들어간다**(#215 FnGuide 기준). 이건 교차출처 감사가
    yfinance CAPEX 의 구성을 재려고 읽는 값이다(#396)."""
    v = _num((fin or {}).get(_DART_INTANGIBLE_KEY))
    return None if v is None else abs(v)


def fcf_from_parts(ocf, capex) -> float | None:
    """영업활동현금흐름 · CAPEX → FCF(= OCF − |CAPEX|).

    ⚠️ 둘 중 하나라도 없으면 None. CAPEX 만 없을 때 **OCF 를 그대로 FCF
    로 쓰면 안 된다** — 설비투자가 큰 회사일수록 크게 부풀려진다."""
    o, c = _num(ocf), _num(capex)
    if o is None or c is None:
        return None
    return o - abs(c)


def missing_reason(row: dict | None) -> str:
    """FCF 를 못 낸 **사유**. 낼 수 있으면 빈 문자열.

    ⚠️ 왜(사용자 2026-08-22 ASML "이거 맞는거야?" — 26.2Q 가 빈칸): 재료가
    없으면 None 을 두는 건 옳지만(빈칸 > 틀린 숫자), 화면이 **왜 비었는지**
    말하지 않으면 사용자가 물어야 한다. 같은 실수를 각주 없는 화면에서 세
    번 반복했다(#123 계정 불일치 · #129 수주잔고 · 여기).
    """
    if not row:
        return "현금흐름표 없음"
    if _first(row, _FCF_NAMES) is not None:
        return ""
    o = _first(row, _OCF_NAMES)
    c = _first(row, _CAPEX_NAMES)
    if o is None and c is None:
        return "영업활동현금흐름·CAPEX 모두 미제공"
    if o is None:
        return "영업활동현금흐름 미제공"
    if c is None:
        # ⚠️ OCF 를 그대로 FCF 로 쓰면 설비투자가 큰 회사가 크게 부풀려진다
        # — 그래서 비우고, 비운 이유를 밝힌다.
        return "CAPEX 미제공(영업현금흐름만으로는 FCF 가 아님)"
    return ""


def attach_to_series(qs: list | None, cf_rows: list | None) -> int:
    """분기 시계열에 `financials["FCF"]` 를 채운다 → 채운 개수.

    ⚠️ **기간(period)으로 조인**한다. 두 표(손익·현금흐름)가 같은 순서로
    온다고 가정하면 원천이 한쪽만 한 분기 덜 줄 때 **전 분기가 한 칸씩
    밀린다**(실수 #46 — 위치로 매기지 말 것). 화면은 멀쩡해 보이고 값만
    통째로 틀린다.
    """
    if not qs or not cf_rows:
        return 0
    by_period = {str(r.get("period", "")): r for r in cf_rows
                 if isinstance(r, dict)}
    n = 0
    for q in qs:
        if not isinstance(q, dict):
            continue
        row = by_period.get(str(q.get("period", "")))
        v = fcf_from_row(row) if row is not None else None
        if v is not None:
            q.setdefault("financials", {})["FCF"] = v
            n += 1
        else:
            # 사유를 **버리지 않는다** — 화면 각주가 빈 분기를 설명한다.
            q.setdefault("_meta", {})["fcf_why"] = (
                missing_reason(row) or "사유미상")
    return n


def cumulative_smell(quarters: list, annual) -> str | None:
    """분기 시계열이 **누적처럼 보이면** 그 사유를 돌려준다. 아니면 None.

    ⚠️ 왜 필요한가(2026-08-21 KR 실측). DART 는 현금흐름을 **누적**으로
    주는데 손익과 같이 다뤄 분기 FCF 가 누적으로 떴다 — 농심 25.2Q 1,145
    → 25.3Q 1,634 → 25.4Q 2,008 이고 25.4Q 가 FY 와 **완전히 같았다**.
    화면만 보면 "실적이 좋아지는 중"으로 읽혀 **틀린 줄도 모른다**.

    시장마다 원천이 다르므로(KR=DART · 그 외=yfinance) 같은 함정이 어디서
    또 나올지 가정하지 않고 **잰다**. 두 신호를 본다:
      · 부호가 한쪽인 구간이 단조 증가(|값|이 계속 커짐)
      · 마지막 분기 ≈ 연간(누적의 마지막은 곧 연간이다)
    둘 다면 거의 확실하다. 하나만이면 판단보류(None) — 실제로 4분기가
    유난히 큰 회사가 있다(#44b: 판단보류를 결론으로 내지 말 것).
    """
    vals = [v for v in (quarters or []) if v is not None]
    if len(vals) < 3:
        return None
    mono = (all(abs(vals[i]) > abs(vals[i - 1]) for i in range(1, len(vals)))
            and len({v >= 0 for v in vals}) == 1)
    a = _num(annual)
    near = a is not None and a != 0 and abs(vals[-1] - a) <= abs(a) * 0.01
    if mono and near:
        return ("|값|이 단조 증가하고 마지막 분기가 연간과 같다 "
                "— 누적을 단일분기로 표기했을 가능성")
    return None
