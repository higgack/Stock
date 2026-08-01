"""일목균형표(一目均衡表, Ichimoku Kinko Hyo) · 이격도(Disparity) — 순수 함수.

`bot/chart_data.py` 의 다른 오버레이(볼린저·MACD·RSI)는 pandas 한두 줄이라
인라인이지만, 이 둘은 (a) 선행스팬을 **앞쪽(미래)** 에 그리느라 시간축을 늘려야
하고 (b) 구름 위치·전환/기준 크로스·삼역호전 같은 **신호 판정**이 붙어 회귀
고정이 필요하다. 그래서 `bot/elliott_fib.py` 와 같은 방식으로 pandas 없이
리스트만 다루는 순수 모듈로 분리한다(샌드박스에서 그대로 단위테스트 가능).

전 시장 동일(universal) — 순수 가격 연산이라 US/KR/JP/TW/CN/HK 구분 없음.

## 표준 근거 (2026-07-31 웹 리서치 — 정석 대조)
호소다 고이치(細田悟一, 필명 一目山人)가 1930년대에 만들어 1969년 공표. 파라미터
9·26·52 + 26 이격이 원본이며 TradingView·StockCharts·MT4·국내 HTS 전부 이 기본값.

- **전환선·기준선·선행스팬2 는 이동평균이 아니라 그 기간 고가·저가의 한가운데**
  (Donchian midpoint). `close.rolling(9).mean()` 로 쓰면 비슷해 보이지만 다른
  지표가 된다 — 가장 흔한 오구현(StockCharts ChartSchool / 알파스퀘어 일치).
- **선행스팬1 은 '오늘의' 전환선·기준선 평균**을 통째로 앞으로 민 것(26봉 전의
  값이 아님).
- **주기 무관**: 9/26/52 는 일봉 캘린더(주6일 거래 시절 한 달≈26일)에서 왔지만
  현대 관행은 **분봉·주봉에서도 그대로 유지**한다. 7/22/44(주5일 환산) 대안이
  있으나 소수설이고, 같은 구름을 보는 참여자가 많다는 점(자기실현)이 표준 유지의
  실질적 근거다. → 우리도 전 interval 동일 파라미터(스케일링 없음).

⚠️ **이격량 26 vs 25 (off-by-one)** — 소스가 실제로 갈린다:
  · TradingView 내장: `offset = displacement - 1` = **25봉** (Pine 원문 확인)
  · MT4/MT5, StockCharts 문서: **26봉**
  · 일본 원전 해석: "당봉 포함 26번째 자리" = 당봉에서 **25봉** → 원전은 25 쪽
  일본 FX 자료(info.fxtrade.co.jp 등)도 "MT4 는 한 봉 더 민다"고 명시한다.
  사용자가 눈으로 대조할 대상이 TradingView 이므로 **25봉(`_SHIFT`)** 을 쓰고,
  바꿀 수 있게 상수로 노출한다. 일봉에선 한 봉 차이라 시각적으론 거의 동일.
"""
from __future__ import annotations

from typing import Optional, Sequence

# 표준 파라미터 — 주기(일/주/분봉) 무관 동일(봉 개수 기준, 위 독스트링 참조).
TENKAN = 9      # 전환선 (Conversion Line)
KIJUN = 26      # 기준선 (Base Line)
SENKOU_B = 52   # 선행스팬2 (Leading Span B)
DISPLACEMENT = 26        # 원 정의상의 이격("당봉 포함 26번째")
_SHIFT = DISPLACEMENT - 1   # 실제 봉 이격 = 25 (TradingView 관행). ⚠️ 위 참조.

# 현재 봉의 구름이 성립하려면 선행스팬2(52봉) 를 `_SHIFT` 봉 전에 이미 계산할 수
# 있어야 한다 → 52 + 25. 이보다 짧으면 구름 없는 반쪽 출력이 되므로 아예 미제공
# (부분 NaN 구름을 그리는 게 가장 흔한 오구현 — 리서치 지적).
MIN_BARS = SENKOU_B + _SHIFT

Num = Optional[float]


def _mid(highs: Sequence[Num], lows: Sequence[Num], n: int) -> list:
    """(n봉 최고가 + n봉 최저가) / 2 — 일목의 모든 선이 쓰는 '중간값'.

    **당봉 포함** n봉(리서치: `ta.highest(len)` 도 당봉 포함). 이동평균이 아니라
    기간 고저의 한가운데 = 그 구간 가격대의 균형점이라는 게 일목의 핵심."""
    out: list = []
    for i in range(len(highs)):
        if i + 1 < n:
            out.append(None)
            continue
        hs = [h for h in highs[i - n + 1:i + 1] if h is not None]
        ls = [lo for lo in lows[i - n + 1:i + 1] if lo is not None]
        out.append((max(hs) + min(ls)) / 2.0 if len(hs) == n and len(ls) == n else None)
    return out


def ichimoku(highs: Sequence[Num], lows: Sequence[Num], closes: Sequence[Num],
             *, tenkan: int = TENKAN, kijun: int = KIJUN,
             senkou_b: int = SENKOU_B, shift: int = _SHIFT) -> Optional[dict]:
    """5선을 **그려질 자리에 맞춰 정렬된** 배열로 반환.

    반환 배열 길이는 전부 `len(closes) + shift` — 선행스팬이 차트 오른쪽 빈
    공간(아직 캔들이 없는 미래)까지 이어지기 때문. 호출부는 시간축도 같은
    길이로 늘려 주면 그대로 zip 해서 그린다. (기존 index 에 그냥 shift 하면
    투영 구간이 잘려나가는 게 가장 흔한 버그 — 리서치 지적.)

      - tenkan/kijun : 실제 봉 구간에만 값, 미래 구간은 None
      - span_a/span_b: 앞 `shift`개가 None, 오른쪽 미래까지 이어짐
      - chikou       : 당봉 종가를 `shift`봉 뒤로 → 끝 `shift`개는 None
        (미래 종가를 알 수 없으므로 후행선이 오른쪽에서 끊기는 건 정상)

    `MIN_BARS` 미만이면 None(호출부 graceful)."""
    n = len(closes)
    if n < max(senkou_b + shift, 1) or n != len(highs) or n != len(lows):
        return None
    ten = _mid(highs, lows, tenkan)
    kij = _mid(highs, lows, kijun)
    # 선행스팬1 = '오늘의' 전환선·기준선 평균(26봉 전 값이 아님) 후 통째로 이동.
    span_a = [None if (ten[i] is None or kij[i] is None) else (ten[i] + kij[i]) / 2.0
              for i in range(n)]
    span_b = _mid(highs, lows, senkou_b)
    pad = [None] * shift
    return {
        "tenkan": list(ten) + pad,
        "kijun": list(kij) + pad,
        "span_a": pad + list(span_a),
        "span_b": pad + list(span_b),
        "chikou": [closes[i + shift] if i + shift < n else None
                   for i in range(n)] + pad,
        "shift": shift,
        "periods": [tenkan, kijun, senkou_b],
        "bars": n,
    }


def _cross_age(a: Sequence[Num], b: Sequence[Num], end: int, cur: int) -> Optional[int]:
    """`end` 봉 기준, a−b 부호가 `cur` 로 바뀐 지 몇 봉 됐나(=크로스 경과).

    거슬러 올라가 부호가 처음 뒤집히는 봉을 찾으면, **크로스가 성립한 봉은
    그 다음 봉**이므로 k−1 을 돌려준다(당봉에서 크로스했으면 0). 이 −1 이
    빠지면 `tk_at` 이 크로스 직전 봉의 구름 위치를 보게 돼 강·약 등급이
    뒤집힌다(독립 리뷰 2026-07-31). 창 전체가 같은 부호면(=이 화면 안에
    크로스가 없음) None."""
    for k in range(1, end + 1):
        i = end - k
        if a[i] is None or b[i] is None or a[i] == b[i]:
            continue
        if (1 if a[i] > b[i] else -1) != cur:
            return k - 1
    return None


def _pos_vs_cloud(px: Num, sa: Num, sb: Num) -> Optional[str]:
    """'above' / 'in' / 'below' — 가격이 구름 위·안·아래 중 어디인가."""
    if px is None or sa is None or sb is None:
        return None
    top, bot = max(sa, sb), min(sa, sb)
    return "above" if px > top else ("below" if px < bot else "in")


def ichimoku_signal(ich: dict, closes: Sequence[Num]) -> Optional[dict]:
    """마지막 실제 봉 기준 표준 신호 묶음.

    표준 해석(StockCharts ChartSchool · 楽天/マネックス証券 · 알파스퀘어 공통):
      1. 가격 vs 구름 — 위=상승추세(구름이 지지), 아래=하락추세(구름이 저항),
         **안=추세 없음**(대부분의 룰이 이 구간 신호를 억제)
      2. 구름 색 — 선행1>선행2 양운(강세) / 반대 음운(약세). 두께 = 지지·저항 강도
      3. 전환선 vs 기준선 — 전환선이 위면 호전(골든)/아래면 역전(데드).
         **크로스 위치로 강도 등급**: 구름 위=강 · 구름 안=중 · 구름 아래=약
      4. 후행스팬 vs 그 자리 과거 가격 — 위면 강세 확인(가장 중요한 확인선)
      5. **삼역호전(三役好転)** — ①전환>기준(기준선 상승/횡보) ②후행스팬 상향
         ③가격이 구름 위, 셋 동시. 일목에서 가장 신뢰도 높은 복합 신호.
         삼역역전은 정확한 거울상.
      6. 구름 전환(twist) — 선행1·2 가 교차하는 지점. 구름은 앞에 그려지므로
         **미래 구간의 twist 가 미리 보인다**(선행 지표적 성격).

    점수는 1~4번의 단순 합(-4~+4)이며 **확률이 아니라 체크리스트 요약**이다."""
    if not ich or not closes:
        return None
    n = len(closes)
    i = n - 1                      # 마지막 실제 봉
    px = closes[i]
    if px is None:
        return None
    sa, sb = ich["span_a"][i], ich["span_b"][i]
    ten, kij = ich["tenkan"][i], ich["kijun"][i]
    sh = ich["shift"]

    price_vs_cloud = cloud = tk = chikou = None
    cloud_top = cloud_bot = None
    if sa is not None and sb is not None:
        cloud_top, cloud_bot = max(sa, sb), min(sa, sb)
        price_vs_cloud = _pos_vs_cloud(px, sa, sb)
        cloud = "bull" if sa > sb else ("bear" if sa < sb else None)
    if ten is not None and kij is not None:
        tk = "golden" if ten > kij else ("dead" if ten < kij else None)
    # 후행스팬은 `shift`봉 전 자리에 놓이므로, 눈으로 보는 비교 대상도 그 자리의
    # 종가다(플롯 이격과 신호 비교를 같은 값으로 맞춰 화면-설명 불일치 방지).
    if i - sh >= 0:
        past = closes[i - sh]
        if past is not None:
            chikou = "above" if px > past else ("below" if px < past else None)

    tk_age = _cross_age(ich["tenkan"], ich["kijun"], i,
                        1 if tk == "golden" else -1) if tk else None
    # 전환/기준 크로스 강도 — **크로스가 일어난 그 봉**에서 가격이 구름 위/안/
    # 아래 어디였는지로 등급을 매긴다(StockCharts 관행). 현재 위치로 대신하면
    # 크로스 후 가격이 이동한 경우 등급이 틀어진다. 크로스 시점을 못 찾으면
    # (창 안에 크로스가 없음) 현재 위치로 폴백.
    tk_at = price_vs_cloud
    if tk_age is not None:
        j = i - tk_age
        if j >= 0:
            tk_at = _pos_vs_cloud(closes[j], ich["span_a"][j], ich["span_b"][j])
    tk_strength = None
    if tk and tk_at:
        tk_strength = ({"above": "strong", "in": "neutral", "below": "weak"}
                       if tk == "golden" else       # 약세 크로스는 거울상
                       {"above": "weak", "in": "neutral", "below": "strong"}
                       ).get(tk_at)

    # 기준선 기울기 — 삼역호전은 '기준선 상승 또는 횡보'를 요구한다.
    kij_slope = None
    if kij is not None and i - 1 >= 0 and ich["kijun"][i - 1] is not None:
        prev = ich["kijun"][i - 1]
        kij_slope = "up" if kij > prev else ("down" if kij < prev else "flat")

    three_role = None
    if tk and price_vs_cloud and chikou and kij_slope:
        if (tk == "golden" and chikou == "above" and price_vs_cloud == "above"
                and kij_slope in ("up", "flat")):
            three_role = "bull"       # 삼역호전
        elif (tk == "dead" and chikou == "below" and price_vs_cloud == "below"
                and kij_slope in ("down", "flat")):
            three_role = "bear"       # 삼역역전

    # 미래 구간(마지막 실제 봉 이후)의 구름 전환 — 며칠 뒤 구름 색이 바뀌는지.
    twist_in = None
    sa_f, sb_f = ich["span_a"], ich["span_b"]
    if cloud:
        cur = 1 if cloud == "bull" else -1
        for k in range(1, sh + 1):
            j = i + k
            if j >= len(sa_f) or sa_f[j] is None or sb_f[j] is None:
                break
            if sa_f[j] == sb_f[j]:
                continue
            if (1 if sa_f[j] > sb_f[j] else -1) != cur:
                twist_in = k
                break

    pos = {"above": 1, "below": -1, "in": 0}
    score = (pos.get(price_vs_cloud, 0)
             + (1 if cloud == "bull" else -1 if cloud == "bear" else 0)
             + (1 if tk == "golden" else -1 if tk == "dead" else 0)
             + (1 if chikou == "above" else -1 if chikou == "below" else 0))
    return {
        "price_vs_cloud": price_vs_cloud, "cloud": cloud,
        "tk_cross": tk, "tk_strength": tk_strength, "tk_age": tk_age,
        "tk_at": tk_at,      # 크로스 시점의 구름 대비 위치(강도 근거)
        "kijun_slope": kij_slope, "chikou": chikou,
        "three_role": three_role, "twist_in": twist_in,
        "score": score,
        "tenkan": ten, "kijun": kij, "span_a": sa, "span_b": sb,
        "cloud_top": cloud_top, "cloud_bot": cloud_bot, "price": px,
    }


# ── 이격도 (Disparity) ────────────────────────────────────────────────────
# ⚠️ 한국 관행과 서구 'Disparity Index'(Steve Nison, *Beyond Candlesticks*)는
# **기준선이 다르다**:
#   한국 이격도          = 종가 / 이동평균 × 100        → **100 이 기준**
#   서구 Disparity Index = (종가 − 이평) / 이평 × 100   → 0 이 기준
# 정확히 100 만큼 평행이동한 같은 값. 사용자가 '이격도'로 요청했고 국내 HTS
# (키움·한국투자 등) 표기가 100 기준이므로 **한국 관행**을 쓴다. 임계값도
# 100 기준 숫자라 두 관행을 섞으면 '105'가 +105% 로 읽히는 대형 오독이 된다.
DISPARITY_PERIODS = (20, 60)   # 국내 표준 조합(키움 도움말: "보통 20일·60일")

# 과열/침체 기준 — **키움증권 기술적지표 가이드 기준**(가장 널리 쓰이는 단순
# 대칭형). ⚠️ 국내 소스가 실제로 갈린다: 한경 경제용어사전 계열은 상승/하락
# 국면을 나눈 표(25일 106/98·75일 110/98 …)를 쓰고, 이를 20일/60일 로 재라벨한
# 판본이 돌아 20일 매도선이 106 이라는 표와 110 이라는 표가 공존한다. 국면
# 분류기가 필요 없고 최대 리테일 증권사가 공표한 값이라 키움판을 기본으로 삼되,
# 화면 안내에 출처와 "절대 기준 아님"을 명시한다(리서치 2026-07-31).
DISPARITY_BANDS = {
    20: {"hot": 105.0, "cold": 95.0},
    60: {"hot": 110.0, "cold": 90.0},
}


def disparity(closes: Sequence[Num], periods: Sequence[int] = DISPARITY_PERIODS) -> dict:
    """{f"d{기간}": [이격도...]} — 종가/단순이동평균×100. 창이 안 찬 구간은 None.

    이동평균이 0/None 인 구간도 None (0 나눗셈의 inf 는 JSON 을 깨뜨린다)."""
    out: dict = {}
    n = len(closes)
    for p in periods:
        if n < p:
            continue
        vals: list = []
        for i in range(n):
            c = closes[i]
            if i + 1 < p or c is None:
                vals.append(None)
                continue
            win = [v for v in closes[i - p + 1:i + 1] if v is not None]
            ma = (sum(win) / p) if len(win) == p else None
            vals.append(round(c / ma * 100.0, 2) if ma else None)
        if any(v is not None for v in vals):
            out[f"d{p}"] = vals
    return out


def disparity_zone(value: Num, period: int) -> Optional[str]:
    """'hot'(과열) / 'cold'(침체) / 'normal' — DISPARITY_BANDS 기준. 값 없으면 None."""
    band = DISPARITY_BANDS.get(period)
    if value is None or not band:
        return None
    if value >= band["hot"]:
        return "hot"
    if value <= band["cold"]:
        return "cold"
    return "normal"
