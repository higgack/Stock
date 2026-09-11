"""Breadth 4구간 전략 — 시장 내부 확산도로 투자 대상·비중을 결정.

사용자 2026-08-16 제공(캡처 3장). 시장 폭(Breadth)을 4구간으로 나눠 구간마다
다른 플레이를 한다 — 극단 과매도에선 지수를 단계매수하고, 회복 초기엔 과거
리더의 놀림목을, 추세가 확립되면 현재 RS 상위를 100% 잡는 방식.

    역추세 B<30%    KOSPI 단계매수 (252일 DD -12%→50% / -18%→75% / -24%↓→100%)
    회복   30~40%   과거 리더 놀림목 (최대 3개, 총 50% — 나머지 현금)
    비추세 40~60%   현재 RS Top3, RS 강도별 25/50/75/100%
    추세   B≥60%    현재 RS Top3 100% (현금 0)

`CASH` 는 **별도 구간이 아니다** — 역추세인데 DD 가 -12%에 못 미쳐 매수
트랜치가 0인 경우다(사용자 캡처 히스토리 101행: B 23.08%·DD -7.00%·현금 100%가
그 증거). 구간을 하나 더 만들면 스펙과 어긋난다.

Breadth 정의(사용자 확정): **섹터 ETF 중 MA120 상회 비율**. 시장타이밍 보드의
breadth 카드(20/50/200일선)와는 **다른 지표**다 — 그쪽은 세 창을 동시에 보는
관측용이고 이쪽은 전략 입력이라 창이 하나로 고정돼야 한다.

표본은 `market_timing._BREADTH_SECTORS` 를 그대로 쓴다(KR=KODEX 섹터 ETF,
US=SPDR GICS). 사용자 2026-08-16 "26개에 맞추지 않아도 돼, 숫자가 다르면
그거에 맞게" — 임계값이 **비율**이라 분모와 무관하게 성립한다. 다만 표본이
작으면 한 섹터의 무게가 커져 구간 경계가 성기다(13개면 1개=7.7%p) — 그
해상도를 화면에 명시한다(`resolution_note`).

심리·변동성 지표(KR=VKOSPI · 그 밖=CNN F&G)는 **기록·표시만** 한다. 캡처
어디에도 게이트로 쓰인다는 근거가 없어(「과매도 확대 신호: False (Breadth
<30% 아님)」는 Breadth 조건이다) 없는 규칙을 지어내지 않는다.

신호 주기: 원 전략은 "월말 종가 신호 → 다음 거래일부터 적용". 매일 값은
**중간점검**이고 월말 값이 확정 신호다 — 둘을 배지로 구분해 보여준다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

# ── 구간 경계(사용자 캡처 그대로) ───────────────────────────────────────────
_B_RECOVERY, _B_NON_TREND, _B_TREND = 30.0, 40.0, 60.0
# 역추세 단계매수 — KOSPI 252일 DD 트랜치(더 깊을수록 더 산다)
_DD_TRANCHES = ((-24.0, 1.00), (-18.0, 0.75), (-12.0, 0.50))
# 회복 구간 놀림목 조건 — 최근 20일 고점 대비 낙폭이 이 밴드 안
_PULLBACK_MIN, _PULLBACK_MAX = -15.0, -5.0
_RECOVERY_TOTAL_W = 0.50      # 회복 구간 총 투자비중(나머지 현금)
_MAX_TARGETS = 3              # 후보 최대 개수(전 구간 공통)

REGIME_LABEL = {
    "CONTRARIAN": "역추세 구간", "RECOVERY": "회복 구간",
    "NON_TREND": "비추세 구간", "TREND": "추세 구간", "CASH": "현금",
}
STATE_LABEL = {
    "CONTRARIAN_KOSPI": "지수 단계매수", "RECOVERY_LEADER_PULLBACK": "과거 리더 놀림목",
    "NON_TREND_RS": "RS 강도별 분할", "TREND_RS_TOP3": "RS Top3 집중", "CASH": "현금 대기",
}


def classify_regime(breadth_pct: float | None) -> str | None:
    """Breadth(%) → 구간. None 이면 판정 불가(입력 없음)."""
    if breadth_pct is None:
        return None
    if breadth_pct < _B_RECOVERY:
        return "CONTRARIAN"
    if breadth_pct < _B_NON_TREND:
        return "RECOVERY"
    if breadth_pct < _B_TREND:
        return "NON_TREND"
    return "TREND"


def index_tranche(dd_pct: float | None) -> float:
    """지수 252일 낙폭(%) → 매수 비중(0/0.5/0.75/1.0). 얕으면 0 = 현금 대기."""
    if dd_pct is None:
        return 0.0
    for threshold, weight in _DD_TRANCHES:
        if dd_pct <= threshold:
            return weight
    return 0.0


def rs_weight(n_strong: int, n_total: int) -> float:
    """비추세 구간 — 'RS 강도' 4단계(25/50/75/100%).

    ⚠️ **해석이 들어간 지점이다.** 캡처는 '25/50/75/100%' 라고만 적고 강도의
    정의를 주지 않는다. 여기서는 강도 = **지수를 이긴 섹터의 비율**로 읽고
    4분위로 나눈다: ≤25%→25 / ≤50%→50 / ≤75%→75 / >75%→100.

    두 번 고쳤다(2026-08-16 독립 리뷰 2회):
      · 후보 상한(3)을 강도 상한으로 쓰면 **75% 단계가 도달 불가**.
      · 절대 개수(0.25×n)로 세면 표본 13개에서 4개만 양(+)이어도 100% —
        비추세가 추세와 같은 '100%·현금0' 으로 **붕괴**한다.
    비율은 표본 크기(US 11 · KR 13)와 무관하게 같은 뜻이라 universal 하다.
    화면에도 이 해석임을 밝힌다."""
    if n_strong <= 0 or n_total <= 0:
        return 0.0
    share = n_strong / n_total * 100
    for cut, w in ((25.0, 0.25), (50.0, 0.50), (75.0, 0.75)):
        if share <= cut:
            return w
    return 1.0


def is_recovery_candidate(m: dict) -> bool:
    """회복 구간 놀림목 조건 — 캡처 명시 3종을 **모두** 만족.

    ① 현재가 > MA120  ② 6개월 지수 대비 RS > 0
    ③ 최근 20일 고점 대비 -15% ~ -5% 놀림(너무 안 빠졌거나 너무 빠지면 제외)
    값이 하나라도 없으면 False — 모르는 걸 통과시키지 않는다."""
    close, ma120 = m.get("close"), m.get("ma120")
    rs6m, pb = m.get("rs_6m"), m.get("pullback_pct")
    if close is None or ma120 is None or rs6m is None or pb is None:
        return False
    return close > ma120 and rs6m > 0 and _PULLBACK_MIN <= pb <= _PULLBACK_MAX


def cash_reason(regime: str | None, state: str | None,
                dd_pct: float | None = None) -> str:
    """`현금 대기` 가 **왜** 현금인지 — 저장된 필드에서 파생하는 순수 함수.

    ⚠️ 왜 필요한가(사용자 2026-09-07 "8월말에 왜 현금대기인지 안알려줘?"):
    `CASH` 는 **네 갈래**로 도달한다 — ① 역추세인데 낙폭이 트랜치(-12%)에
    못 미침 ② 회복인데 조건 충족 섹터 없음 ③ 비추세·추세인데 지수를 웃도는
    섹터 없음 ④ 판정 불가. 처방이 다 다른데 화면은 넷을 **한 라벨**로 묶어
    놨다(#82 갈래는 이름으로 · #34 한 라벨이 여럿을 대표하면 하나는 거짓말).

    ⚠️ 렌더타임 파생이라 **옛 확정 기록도 그대로 따라온다**(#270) — 백필이
    필요 없다. 대신 모르는 조합은 **지어내지 않고** 빈 문자열을 낸다(#165).
    """
    if state != "CASH":
        return ""
    if regime is None:
        return "판정 불가 — Breadth 를 계산하지 못했습니다"
    if regime == "CONTRARIAN":
        dd = f"{dd_pct:.2f}%" if isinstance(dd_pct, (int, float)) else "—"
        return (f"지수 낙폭({dd})이 첫 매수 트랜치(−12%)에 못 미쳐 "
                "단계매수를 시작하지 않았습니다")
    if regime == "RECOVERY":
        # ⚠️ 이 문자열은 HTML escape 를 거치므로 `**볼드**`·태그를 쓰면
        # 기호가 그대로 화면에 나온다(실측). 평문으로 쓴다.
        return ("과거 리더 중 회복조건(현재가>MA120 · 6개월 RS>0 · "
                "20일 고점 대비 −15~−5% 놀림목)을 셋 다 충족한 섹터가 "
                "없었습니다")
    if regime in ("NON_TREND", "TREND"):
        return "RS 가 지수를 웃도는(>0) 섹터가 없어 매수 대상이 없었습니다"
    return ""


# ── 시장별 심리·변동성 지표 ────────────────────────────────────────────────
# ⚠️ CNN Fear & Greed 는 **미국 증시** 지표다. 2026-08-16 에 사용자가
# "F&G 65 는 한국기준이야?" 를 물어 출처(`美 CNN`)를 라벨에 박았는데(#34),
# 2026-09-11 에 "한국은 VKOSPI 를 적어줘. CNN VIX 말고" 로 **그 시장의
# 지표를 쓰라**는 결정이 왔다. 시장 게이트를 렌더러에 흩지 않고 레지스트리
# 한 줄로 둔다 — 조건문에 흩어 적으면 다음 시장이 샌다(#24·#31).
# VKOSPI 는 KRX 산출·KIS 제공이라 **한국에만 존재하는 원천**이다(§UNIVERSAL
# 의 '문서화된 데이터소스 사유' 예외 — DART/ECOS/Naver 와 같은 갈래).
# 값은 시장타이밍 보드가 쓰는 `market_timing.fetch_vkospi_rows` 를 **그대로
# 재사용**한다(KIS 지수코드·타당범위 가드·1시간 디스크 캐시가 거기 있다 —
# 복제하면 두 화면이 갈린다, #38).
_SENTIMENT_KIND = {"KR": "vkospi"}       # 그 밖의 시장 = "fng"
# VKOSPI 가 빈손인 세 갈래 — 처방이 다르다(#82). `implausible` 은 원천 장애가
# 아니라 **지수코드가 바뀐 것**이라 로그를 봐야 한다.
_VKOSPI_WHY = {
    "credentials": "원천(KIS)을 조회하지 못했습니다 — 크리덴셜·네트워크 확인",
    "empty": "원천(KIS)이 값을 주지 않았습니다",
    "implausible": "원천(KIS) 응답이 변동성지수 범위 밖이라 채택하지 않았습니다"
                   " — 지수코드 확인(로그)",
}


def sentiment_kind(market: str | None) -> str:
    """그 시장 카드가 실을 지표 종류 — 'vkospi' | 'fng' (순수)."""
    return _SENTIMENT_KIND.get(str(market or "").upper(), "fng")


def sentiment_text(s: dict | None) -> str:
    """심리·변동성 한 줄 — `VKOSPI 15.20 (2026-09-10 종가, KIS)` (순수, escape 전).

    ⚠️ 값이 없으면 `—` 로 침묵하지 않는다 — **왜 없는지** 적는다(#43·#131·#82).
    그리고 KR 에서 VKOSPI 를 못 받았다고 CNN F&G 로 조용히 내려가지 않는다:
    그건 사용자가 바로 그 이유로 빼 달라고 한 값이라, 폴백하면 고치려던
    거짓말을 되살린다(#136 화면은 payload 가 밝힌 원천을 따른다).
    """
    s = s or {}
    label = str(s.get("label") or "")
    val = s.get("value")
    # ⚠️ 숫자가 아닌 값이 오면 **여기서 던지지 않는다** — 이 한 줄이 `_market_
    # section` 안에서 터지면 카드가 통째로 사라진다(#315 곁들이가 본체를 지운다).
    # 못 읽은 사실은 사유로 말한다(#43·#54 판정 불가는 통과가 아니다).
    if val is not None and not isinstance(val, (int, float)):
        try:
            val = float(val)
        except (TypeError, ValueError):
            return f"{label} — 값을 숫자로 읽지 못했습니다" if label else "—"
    if val is None:
        why = str(s.get("why") or "")
        if label and why:
            return f"{label} — {why}"
        return label or "—"
    digits = int(s.get("digits") or 0)
    bits = [b for b in (str(s.get("note") or ""), str(s.get("source") or "")) if b]
    tail = f" ({', '.join(bits)})" if bits else ""
    return f"{label} {val:,.{digits}f}{tail}"


def _fetch_sentiment(market: str) -> dict:
    """그 시장의 심리·변동성 지표 — 실패해도 **사유를 담아** 돌려준다.

    반환 {kind, label, value, note, source, digits, why}. `sentiment_text` 가
    화면 문구를 만든다(문구를 두 군데 적으면 한쪽만 고쳐진다, #38).
    """
    kind = sentiment_kind(market)
    if kind == "vkospi":
        out = {"kind": "vkospi", "label": "VKOSPI", "value": None, "note": "",
               "source": "KIS", "digits": 2, "why": ""}
        try:
            from bot.market_timing import vkospi_rows_with_reason, vol_asof_label
            rows, why = vkospi_rows_with_reason()
        except Exception as exc:                                   # noqa: BLE001
            out["why"] = f"원천(KIS) 조회 실패({type(exc).__name__})"
            log.warning("breadth_strategy: VKOSPI 조회 실패(%s: %s) — 카드에 "
                        "사유 표기", type(exc).__name__, exc)
            return out
        if not rows:
            # 갈래마다 처방이 다르다 — 크리덴셜/네트워크 · 원천 빈 응답 ·
            # 지수코드가 바뀌어 가격지수를 받은 것(#82 · L5).
            out["why"] = _VKOSPI_WHY.get(why, "원천(KIS)에서 값을 받지 못했습니다")
            log.info("breadth_strategy: VKOSPI 행 없음(%s) — 카드에 사유 표기",
                     why or "unknown")
            return out
        out["value"] = rows[-1].get("close")
        # ⚠️ **'종가' 를 박지 않는다.** 장중이면 그건 현재값이다 — 시장타이밍
        # 보드가 같은 원천에 대해 이미 그 판정을 갖고 있으므로(`vol_asof_label`,
        # 사용자 2026-08-20 "VKOSPI 가 현지 10:26 에 08-20 종가") 그걸 부른다.
        # 복제하면 같은 값이 두 화면에서 다른 라벨을 단다(#38·#34·#43a). 3시간
        # 주기 재생성은 KR 장중(09:00~15:30 KST)에도 돈다.
        out["note"] = vol_asof_label({"date": rows[-1].get("date"),
                                      "market": "KR"}).get("label", "")
        if out["value"] is None:
            out["why"] = "원천(KIS) 마지막 봉에 종가가 없습니다"
        return out
    out = {"kind": "fng", "label": "F&G", "value": None, "note": "",
           "source": "美 CNN", "digits": 0, "why": ""}
    try:
        from bot.fear_greed_client import fetch_fear_greed
        fng = fetch_fear_greed() or {}
    except Exception as exc:                                       # noqa: BLE001
        out["why"] = f"CNN F&G 조회 실패({type(exc).__name__})"
        log.warning("breadth_strategy: F&G 조회 실패(%s: %s) — 카드에 사유 표기",
                    type(exc).__name__, exc)
        return out
    out["value"] = fng.get("score")
    out["note"] = str(fng.get("rating_kr") or "")
    if out["value"] is None:
        out["why"] = "CNN F&G 응답에 값이 없습니다"
    return out


def decide(breadth_pct: float | None, dd_pct: float | None, *,
           recovery_pool: list[dict] | None = None,
           rs_ranked: list[dict] | None = None) -> dict:
    """구간 판정 → {regime, state, targets, index_w, total_w, cash_w}.

    반환 필드는 사용자 캡처의 히스토리 표 컬럼과 1:1이다(상태·지수비중·
    최종비중·현금). `recovery_pool` = 최근 6개월 Top3 이력 섹터의 지표,
    `rs_ranked` = 현재 RS 내림차순 섹터."""
    regime = classify_regime(breadth_pct)
    out = {"regime": regime, "state": "CASH", "targets": [],
           "index_w": 0.0, "total_w": 0.0, "cash_w": 1.0,
           "breadth_pct": breadth_pct, "dd_pct": dd_pct}
    if regime is None:
        out["state"] = None
        return out

    if regime == "CONTRARIAN":
        w = index_tranche(dd_pct)
        # DD 가 얕으면 트랜치 0 → CASH. 별도 구간이 아니라 이 경우다.
        out.update(state="CONTRARIAN_KOSPI" if w > 0 else "CASH",
                   targets=[{"name": "지수", "weight": w}] if w > 0 else [],
                   index_w=w, total_w=w, cash_w=round(1.0 - w, 4))
        return out

    if regime == "RECOVERY":
        picks = [m for m in (recovery_pool or []) if is_recovery_candidate(m)]
        picks = picks[:_MAX_TARGETS]
        if not picks:
            return out                      # 조건 충족 없음 → 현금
        each = round(_RECOVERY_TOTAL_W / len(picks), 4)
        out.update(state="RECOVERY_LEADER_PULLBACK",
                   targets=[{"name": m["name"], "weight": each} for m in picks],
                   total_w=_RECOVERY_TOTAL_W, cash_w=round(1 - _RECOVERY_TOTAL_W, 4))
        return out

    ranked = rs_ranked or []
    top = ranked[:_MAX_TARGETS]
    # ⚠️ 두 구간 모두 **RS 가 양(+)인 섹터만** 산다. rs 가 None(벤치마크 조회
    # 실패)이거나 음수인 후보에 비중을 주면 '지수보다 약한 섹터를 근거 없이
    # 사는' 매수가 된다 — breadth 는 계산됐는데 RS 가 전부 None 이라 TREND 가
    # 뜨는 조합이 실제로 가능하다(2026-08-16 독립 리뷰 실측).
    picks = [m for m in top if (m.get("rs") is not None and m["rs"] > 0)]
    if regime == "NON_TREND":
        # 강도 = 지수를 이긴 섹터의 **비율** — 전체 랭킹 기준(상위 3개만 세면
        # 4단계째가 안 나오고, 절대 개수로 세면 표본이 클수록 쉽게 100%).
        n_strong = sum(1 for m in ranked
                       if m.get("rs") is not None and m["rs"] > 0)
        w = rs_weight(n_strong, len(ranked))
    else:                                    # TREND
        w = 1.0 if picks else 0.0
    if not picks or w <= 0:
        return out
    each = round(w / len(picks), 4)
    out.update(state="NON_TREND_RS" if regime == "NON_TREND" else "TREND_RS_TOP3",
               targets=[{"name": m["name"], "weight": each} for m in picks],
               total_w=w, cash_w=round(1.0 - w, 4))
    return out


# ── 지표 계산(순수) ─────────────────────────────────────────────────────────
def ma(closes: list, period: int):
    """단순이동평균 마지막 값. 부족하면 None (market_timing.sma 와 동일 규약)."""
    if not closes or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def drawdown_pct(closes: list, lookback: int = 252):
    """최근 lookback 구간 최고점 대비 현재 낙폭(%). 데이터 부족 시 None."""
    if not closes:
        return None
    window = closes[-lookback:]
    peak = max(window)
    if not peak:
        return None
    return round((window[-1] / peak - 1) * 100, 2)


def pullback_from_high(closes: list, lookback: int = 20):
    """최근 lookback 고점 대비 현재 낙폭(%). 음수 = 고점 아래."""
    return drawdown_pct(closes, lookback)


def relative_strength(closes: list, bench: list, lookback: int):
    """기간 수익률 차이(종목% - 벤치마크%). 어느 한쪽이라도 부족하면 None."""
    if len(closes) <= lookback or len(bench) <= lookback:
        return None
    a = closes[-1] / closes[-1 - lookback] - 1
    b = bench[-1] / bench[-1 - lookback] - 1
    return round((a - b) * 100, 2)


def breadth_above_ma(sector_closes: dict, period: int = 120) -> dict:
    """{ticker: closes} → MA{period} 상회 비율. 데이터 부족 섹터는 분모에서
    제외하고 **몇 개가 빠졌는지 함께 돌려준다**(분모가 조용히 줄지 않게)."""
    above = counted = 0
    skipped: list[str] = []
    for ticker, closes in sector_closes.items():
        m = ma(closes, period)
        if m is None:
            skipped.append(ticker)
            continue
        counted += 1
        if closes[-1] > m:
            above += 1
    # ⚠️ **여기서 반올림하지 않는다** — 옛 판은 `round(...,1)` 이라 (a) 구간
    # 판정이 반올림된 값을 보고 (b) 화면이 2자리로 찍어 `4/13` 이 `30.80%` 로
    # 보였다(실제 30.77% — 사용자가 눈으로 나눠 봐도 안 맞는다, #33·2026-09-07).
    # 반올림은 표시에서만 한다.
    return {"pct": above / counted * 100 if counted else None,
            "above": above, "counted": counted, "skipped": skipped,
            "period": period}


# ── 신호 이력(월말 확정분) ──────────────────────────────────────────────────
_HOME = Path(os.environ.get("TRADINGAGENTS_HOME") or Path.home())
_SIGNAL_DIR = _HOME / ".tradingagents" / "breadth_strategy"


def signal_path(market: str) -> Path:
    return _SIGNAL_DIR / f"signals_{market.upper()}.jsonl"


def load_signals(market: str, limit: int = 60) -> list[dict]:
    """확정 신호 이력(오래된→최신). 깨진 줄은 건너뛴다."""
    p = signal_path(market)
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        log.warning("breadth_strategy: 신호 이력 읽기 실패: %s", exc)
        return []
    # 파일 순서 ≠ 시간 순서 — 빠진 달을 나중에 백필하면 뒤에 append 된다.
    # 회복 후보 풀도 화면 이력도 '최근 N개월' 기준이라 월로 정렬해서 준다.
    #
    # ⚠️ 한 달에 **정정본**이 여러 줄 있을 수 있다(원천이 늦어 이른 종가로
    # 먼저 굳은 뒤 진짜 월말이 들어온 경우 — append_signal 참조). 화면·후보
    # 풀 모두 달마다 **하나**여야 하므로 기준일이 가장 늦은 것만 남긴다.
    best: dict[str, dict] = {}
    for r in out:
        m = str(r.get("month") or "")
        cur = best.get(m)
        if cur is None or str(r.get("asof") or "") >= str(cur.get("asof") or ""):
            best[m] = r
    out = sorted(best.values(), key=lambda r: str(r.get("month") or ""))
    return out[-limit:]


def append_signal(market: str, rec: dict) -> bool:
    """월말 확정 신호 append. 재실행·재시작에 멱등(3시간 주기 잡이라 필수).

    ⚠️ 옛 판은 `month` 만 보고 건너뛰었다 — 그래서 **원천이 하루 늦은 날**
    기록되면 그 달의 '월말' 이 영영 틀린 채로 굳었다(2026-09-07 VM 실측:
    2026-08 확정이 `asof=08-28`(금)인데 실제 마지막 거래일은 **08-31**(월)
    이었다. 09-01 에 기록될 때 야후 시계열에 08-31 이 아직 없었던 것).
    기록은 회복 후보 풀(Top3 이력)의 원천이라 그 오염이 뒤로 전파된다(#18
    구워진 데이터는 코드를 고쳐도 안 바뀐다).

    무해한 사고가 아니다 — 같은 실행의 프로브가 08-24 는 `과거 리더 놀림목`
    (50% 투자), 08-25~31 은 `현금 대기` 임을 보였다. **어느 날을 월말로 잡느냐가
    신호를 바꾼다.**

    그래서 멱등 기준을 (월) → (월, 기준일)로 좁힌다: 같은 달에 **더 늦은
    종가**가 들어오면 정정본을 덧쓴다. 같거나 이른 기준일은 종전대로 무시한다.
    """
    month = rec.get("month")
    if not month:
        return False
    asof = str(rec.get("asof") or "")
    prior = [str(r.get("asof") or "") for r in load_signals(market, limit=10_000)
             if r.get("month") == month]
    if prior and max(prior) >= asof:
        return False
    try:
        _SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
        with signal_path(market).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        log.warning("breadth_strategy: 신호 기록 실패: %s", exc)
        return False


def completed_month_ends(dates: list[str], limit: int = 12) -> list[str]:
    """**완결된 달마다 그 달의 마지막 거래일 날짜**(오래된→최신, 최대 limit).

    이 함수가 "월말 종가 신호" 의 정의다. 옛 구현은 '오늘이 이 달 마지막
    거래일인가' 를 물었는데 두 가지가 깨졌다(2026-08-16 독립 리뷰):
      · KR 장중(09:00~15:30)이 3시간 스케줄러보다 넓어 **부분봉**이 월말
        종가로 박히고, 멱등성 때문에 진짜 종가는 영원히 무시됐다.
      · 휴장일(KRX 12/31, US 성금요일 등)에 월 마지막 평일 봉이 없으면
        그 달 신호가 통째로 누락됐다.
    닫힌 달의 마지막 봉은 **이미 확정된 데이터**이고, 휴장일이 언제든
    '그 달의 마지막 봉' 은 정의상 하나뿐이라 캘린더가 필요 없다.

    **인덱스가 아니라 날짜**를 돌려주는 이유: 섹터마다 시계열 길이가 다르다
    (네이버 폴백을 탄 섹터는 3년치, 아닌 섹터는 1년치). 벤치마크 인덱스로
    다른 섹터를 자르면 엉뚱한 봉에서 잘려 '월말 확정' 값에 월말 이후 봉이
    섞인다 — 멱등 기록이라 그 오염이 영구화된다(2026-08-16 독립 리뷰).

    한 달에 여러 개가 아니라 **완결된 달 전부**를 돌려주는 이유: 3시간 잡이
    한 달 내내 실패하면 그 달 신호가 영구 유실되고, 회복 구간 후보 풀의
    원천인 Top3 이력에 구멍이 난다. 신규 배포 시 이력 백필 효과도 있다."""
    seen: dict[str, str] = {}
    for d in dates:
        d = str(d or "")
        if len(d) >= 7:
            seen[d[:7]] = d       # 같은 달의 마지막 등장 = 그 달 마지막 봉
    months = list(seen)
    if len(months) < 2:
        return []
    # 마지막 달은 아직 진행 중일 수 있으므로 제외한다(부분 달 = 미확정).
    return [seen[m] for m in months[:-1]][-limit:]


# ── 데이터 수집(I/O) ────────────────────────────────────────────────────────
# 전략 표본 = 시장타이밍 breadth 레지스트리 그대로. 사용자 2026-08-16
# "26개에 맞추지 않아도 돼 … 미국도 마찬가지" — 임계값이 비율이라 분모가
# 달라도 성립한다. 벤치마크는 각 시장의 대표지수(RS·DD 기준).
_BENCH = {"KR": ("^KS11", "KOSPI"), "US": ("^GSPC", "S&P 500")}
_RS_LOOKBACK_6M = 126        # 약 6개월 거래일
_RECOVERY_MONTHS = 6         # '최근 6개월 Top3 이력' 창(확정 신호 개수)
_BACKFILL_MONTHS = 12        # 확정 신호 백필 상한(신규 배포·장기 장애 대비)


def _series(ticker: str, days: int = 400) -> list[dict]:
    from bot.market_timing import fetch_index_history
    # min_rows — yfinance 절단 방어(market_timing.fetch_index_history 참조).
    # MA120 이 전략의 축이라 짧은 시계열은 판정을 통째로 바꾼다.
    # days=400 — 252일 DD 를 **월말 확정분에서도** 온전히 계산하려면 1년치
    # (≈250봉)로는 모자라다(앞쪽을 더 잘라내므로).
    return fetch_index_history(ticker, days=days, min_rows=200)


def _cut_rows(rows: list, cut_date: str | None) -> list:
    """`cut_date`(포함)까지의 봉만. None 이면 전체.

    **날짜로** 자른다 — 섹터마다 시계열 길이가 달라(네이버 폴백 여부) 공통
    인덱스로 자르면 섹터마다 다른 날에서 잘린다(2026-08-16 독립 리뷰)."""
    if cut_date is None:
        return rows
    return [r for r in rows if str(r.get("date") or "") <= cut_date]


def _assemble(market: str, sectors: dict, bench_name: str, bench_rows: list,
              rows_by_label: dict, missing: list, *, cut: str | None) -> dict:
    """지표 계산 + 구간 판정. `cut` = 이 **날짜**(포함)까지만 잘라서 계산.

    None 이면 전체(=오늘, 중간점검). 월말 확정 신호는 **그 달 마지막 봉**
    까지 잘라 계산한다 — 장중 부분봉이 '월말 종가' 로 박히는 걸 막는다."""
    from bot.market_timing import _BREADTH_SOURCE_LABEL
    bench_cut = _cut_rows(bench_rows, cut)
    bench_closes = [r["close"] for r in bench_cut]
    sliced = {k: [r["close"] for r in _cut_rows(v, cut)]
              for k, v in rows_by_label.items()}

    b = breadth_above_ma(sliced, period=120)

    metrics = [{
        "name": label,
        "close": closes[-1] if closes else None,
        "ma120": ma(closes, 120),
        "rs_6m": relative_strength(closes, bench_closes, _RS_LOOKBACK_6M),
        "pullback_pct": pullback_from_high(closes, 20),
    } for label, closes in sliced.items()]
    # RS 내림차순 = 현재 리더. 값 없는 섹터는 뒤로(값 없는 걸 '강하다' 고
    # 볼 수 없다) — decide 가 None RS 를 매수 후보에서 제외한다.
    rs_ranked = sorted(({**m, "rs": m["rs_6m"]} for m in metrics),
                       key=lambda m: (m["rs"] is None, -(m["rs"] or 0)))
    # 과거 달을 백필할 땐 그 달 **이전** 기록만 후보 풀에 쓴다(미래 정보 차단).
    recovery_pool = _recovery_pool(market, rs_ranked,
                                   before=cut[:7] if cut else None)

    dd = drawdown_pct(bench_closes, 252)
    dec = decide(b["pct"], dd, recovery_pool=recovery_pool, rs_ranked=rs_ranked)

    # 심리·변동성 지표는 **현재값만** 있어 과거 시점으로 되돌릴 수 없다. 확정분
    # (cut 지정)에 오늘 값을 박으면 그 달의 값인 척하는 거짓 기록이 된다
    # — 안 넣는다(2026-08-16 독립 리뷰). 어차피 판정에 안 쓰는 표시용이다.
    sentiment = _fetch_sentiment(market) if cut is None else {}

    dates = [r["date"] for r in bench_cut]
    n = b["counted"] or len(sectors)
    # 히스토리가 짧아 MA120 을 못 만든 섹터도 **이름으로** 밝힌다 — rows 는
    # 왔으니 missing 엔 안 들어가고 분모에서만 조용히 빠진다(독립 리뷰).
    all_missing = list(missing) + [f"{lb}(기간부족)" for lb in b.get("skipped", [])]
    return {
        **dec,
        "market": market.upper(),
        "bench_name": bench_name,
        "breadth": b,
        "source_label": _BREADTH_SOURCE_LABEL.get(market.upper(), "섹터 ETF"),
        "sectors_missing": all_missing,
        "rs_ranked": rs_ranked[:5],
        # ⚠️ 시장마다 **다른 지표**다 — KR=VKOSPI(KIS) · 그 밖=CNN F&G(美).
        # 화면 문구는 `sentiment_text` 하나가 만든다(#38).
        "sentiment": sentiment,
        "asof": dates[-1] if dates else "",
        # 기준일 옆에 **그날의 지수 종가**를 같이 싣는다 — 시장타이밍 보드와
        # 같은 규약("기준 2026-09-10 · 최근 종가 7591.7")이라 두 화면을 나란히
        # 놓고 눈으로 대조할 수 있다(사용자 2026-09-11 요청 · #33·#51).
        "latest_close": bench_closes[-1] if bench_closes else None,
        "is_confirmed": cut is not None,
        # 해상도 각주 — 표본이 작으면 한 섹터가 큰 폭을 차지해 경계가 성기다.
        # ⚠️ '3.9개 지점' 같은 소수는 실제로 존재하지 않는다(섹터는 정수) —
        # **실제로 구간이 바뀌는 정수 개수**를 함께 적는다(사용자 2026-08-16
        # "이건 무슨 뜻이야?").
        "resolution_note": (
            f"표본 {n}개 — 섹터 1개 = {100 / n:.1f}%p. 구간 경계"
            f"({_B_RECOVERY:.0f}/{_B_NON_TREND:.0f}/{_B_TREND:.0f}%)에 닿으려면 "
            f"각각 {min_sectors_for(n, _B_RECOVERY)}·"
            f"{min_sectors_for(n, _B_NON_TREND)}·"
            f"{min_sectors_for(n, _B_TREND)}개 이상이 MA120 위여야 합니다"
        ) if n else "",
    }


def _recovery_pool(market: str, rs_ranked: list[dict],
                   before: str | None = None) -> list[dict]:
    """회복 구간 후보 풀 = '최근 6개월 Top3 이력'.

    확정 신호 로그에 매달 Top3 를 적재해 두고(`append_signal` 의 `top3`),
    최근 6개월치의 **합집합**을 쓴다 — 이게 원 전략의 정의다. 급락 전
    리더였다가 지금 순위가 밀린 섹터가 바로 '놀림목' 대상인데, 현재 RS
    상위만 보면 그 섹터가 후보에 못 든다(2026-08-16 독립 리뷰).

    순서는 **최신 달 우선** — `decide` 가 3개로 자르므로 6개월 전 리더가
    지난달 리더를 밀어내면 안 된다. `before`(YYYY-MM) 를 주면 그 달 이전
    기록만 본다(과거 달 백필 시 미래 정보 차단).
    로그가 아직 비었으면(신규 배포) 현재 RS 상위로 부트스트랩한다."""
    by_name = {m["name"]: m for m in rs_ranked}
    recs = load_signals(market, limit=10_000)
    if before:
        recs = [r for r in recs if str(r.get("month") or "") < before]
    past: list[str] = []
    for rec in reversed(recs[-_RECOVERY_MONTHS:]):     # 최신 달부터
        past.extend(rec.get("top3") or [])
    pool = [by_name[n] for n in dict.fromkeys(past) if n in by_name]
    if pool:
        return pool
    return [m for m in rs_ranked if m.get("rs") is not None][:_RECOVERY_MONTHS]


def _fetch_market(market: str):
    """섹터·벤치마크 시계열 1회 수집. 중간점검·확정 둘 다 이 한 벌을 쓴다
    (같은 티커를 두 번 받지 않는다 — 확정분은 날짜로 잘라 쓴다)."""
    from bot.market_timing import _BREADTH_SECTORS
    sectors = _BREADTH_SECTORS.get(market.upper()) or {}
    if not sectors:
        return None
    bench_ticker, bench_name = _BENCH.get(market.upper(), ("", ""))
    bench_rows = _series(bench_ticker) if bench_ticker else []
    rows_by_label: dict[str, list] = {}
    missing: list[str] = []
    for ticker, label in sectors.items():
        rows = _series(ticker)
        if rows:
            rows_by_label[label] = rows
        else:
            missing.append(label)
    return sectors, bench_name, bench_rows, rows_by_label, missing


def build_market(market: str, *, cut: str | None = None) -> dict:
    """한 시장의 전략 스냅샷. 조각 실패는 그 필드만 비운다(graceful)."""
    got = _fetch_market(market)
    if not got:
        return {}
    sectors, bench_name, bench_rows, rows_by_label, missing = got
    return _assemble(market, sectors, bench_name, bench_rows, rows_by_label,
                     missing, cut=cut)


def _signal_record(d: dict) -> dict:
    """확정 스냅샷 → 신호 로그 1줄. `top3` 는 회복 후보 풀의 원천이라 필수."""
    return {
        "month": str(d.get("asof", ""))[:7], "asof": d.get("asof"),
        "state": d.get("state"), "regime": d.get("regime"),
        "breadth_pct": d.get("breadth_pct"), "dd_pct": d.get("dd_pct"),
        # ⚠️ **분모를 같이 남긴다** — %만 남기면 그 30.8% 가 4/13 인지, 원천
        # 절단으로 분모가 줄어든 4/12 인지 **영원히 못 가른다**. 기록은
        # 멱등이라 한 번 잘못 들어가면 그대로 굳는다(#18·#43·#45, 2026-09-07).
        "breadth_above": (d.get("breadth") or {}).get("above"),
        "breadth_counted": (d.get("breadth") or {}).get("counted"),
        "breadth_skipped": (d.get("breadth") or {}).get("skipped") or [],
        "index_w": d.get("index_w"), "total_w": d.get("total_w"),
        "cash_w": d.get("cash_w"),
        "targets": [t["name"] for t in (d.get("targets") or [])],
        "top3": [m["name"] for m in (d.get("rs_ranked") or [])
                 if m.get("rs") is not None][:_MAX_TARGETS],
    }


def build_with_signals(market: str) -> tuple[dict, list[dict]]:
    """(오늘 중간점검, 이번에 새로 기록한 확정 신호들).

    닫힌 달을 **오래된 순서로** 계산하며 그때그때 기록한다 — 회복 후보
    풀이 직전 달들의 Top3 를 읽으므로 순서가 뒤바뀌면 백필한 달들이 서로를
    못 본다. 이미 기록된 달은 건너뛴다(멱등). 중간점검은 백필이 끝난 뒤
    계산해 방금 채운 이력을 반영한다."""
    got = _fetch_market(market)
    if not got:
        return {}, []
    sectors, bench_name, bench_rows, rows_by_label, missing = got
    have = {str(r.get("month") or ""): str(r.get("asof") or "")
            for r in load_signals(market, limit=10_000)}
    written: list[dict] = []
    for d in completed_month_ends([r["date"] for r in bench_rows],
                                  limit=_BACKFILL_MONTHS):
        # ⚠️ `have` 는 (월 → 기록된 기준일)이다. 같은 달이라도 **더 늦은
        # 종가**가 들어왔으면 다시 계산해 정정한다(append_signal 참조).
        if have.get(d[:7], "") >= d:
            continue
        snap = _assemble(market, sectors, bench_name, bench_rows, rows_by_label,
                         missing, cut=d)
        if snap.get("breadth_pct") is None:
            continue          # 그 시점 히스토리 부족 — 빈 신호를 남기지 않는다
        rec = _signal_record(snap)
        if append_signal(market, rec):
            written.append(rec)
    live = _assemble(market, sectors, bench_name, bench_rows, rows_by_label,
                     missing, cut=None)
    return live, written


# ── 렌더 ────────────────────────────────────────────────────────────────────
_REGIME_TABLE = (
    ("CONTRARIAN", "역추세 구간", "Breadth &lt; 30%", "극단적 과매도",
     "지수 단계매수", "지수", "DD −12%:50% / −18%:75% / −24%↓:100%"),
    ("RECOVERY", "회복 구간", "30% ≤ Breadth &lt; 40%", "회복 초기",
     "과거 리더 놀림목", "과거 리더 중 회복조건 충족", "총 50% (나머지 현금)"),
    ("NON_TREND", "비추세 구간", "40% ≤ Breadth &lt; 60%", "회복 지속·확산 확인",
     "RS 강도별 분할", "현재 RS Top3(지수 상회분만)",
     "지수 상회 섹터 비율 4분위 → 25 / 50 / 75 / 100%"),
    ("TREND", "추세 구간", "Breadth ≥ 60%", "상승 추세 확립",
     "RS Top3 집중", "현재 RS Top3", "100% (현금 0)"),
)


def regime_weight_cell(rule_text: str, *, active: bool, total_w) -> str:
    """구간 표의 '비중' 칸 — **활성 구간이면 이번 판정의 실제 비중**을 덧붙인다.

    ⚠️ 왜 필요한가(사용자 2026-09-11 "위에 표랑 아래 표랑 전략이 다른데.
    최종 투자비중이랑 현금비중이랑. 어떤 로직이야?"): 아래 구간 표는 **규칙
    설명(고정 문구)** 이고 위 stat-grid 는 **오늘 계산한 결과**다. 회복 구간의
    규칙은 '총 50%' 인데 회복조건 셋을 다 채운 과거 리더가 0개면 `decide` 가
    기본값(투자 0% · 현금 100%)을 그대로 낸다 — 두 표를 나란히 놓으면 서로
    모순으로 읽힌다(#33 나란히 놓인 칸은 산수가 맞아야 한다 · #34 한 라벨이
    둘을 대표하면 하나는 거짓말). 규칙 문구는 그대로 두고 **활성 행에만**
    실제값을 덧붙여, 규칙과 결과가 한 줄에서 갈린다.

    비활성 행은 손대지 않는다 — 그 구간은 오늘 적용되지 않았으므로 '이번
    판정' 이라 부를 값이 없다(#165 재지 않은 것을 적지 않는다).
    """
    if not active or not isinstance(total_w, (int, float)):
        return rule_text
    return f"{rule_text} → 이번 판정 {total_w * 100:.0f}%"


def min_sectors_for(n: int, pct: float) -> int:
    """n개 표본에서 breadth 가 `pct`% **이상**이 되는 최소 섹터 수.

    경계는 '이상' 포함이다(classify_regime 이 `<` 로 자른다). 딱 나누어
    떨어지는 경우(10개의 30% = 3개)를 올림이 밀어내지 않도록 아주 작은
    여유를 뺀 뒤 올린다 — 분기 없이 한 줄로 둘 다 맞는다."""
    import math
    return max(0, min(n, math.ceil(n * pct / 100.0 - 1e-9)))


# ── 가이드용 생성 표 — 손으로 적으면 상수와 어긋난다(#55) ────────────────────
# 상태 → (속한 구간, 규칙 한 줄). STATE_LABEL 의 **모든** 키가 있어야 한다(회귀).
_STATE_RULE = {
    "CONTRARIAN_KOSPI": ("CONTRARIAN", "지수를 낙폭 트랜치대로 단계매수(아래 트랜치 표)"),
    "RECOVERY_LEADER_PULLBACK": ("RECOVERY",
        f"과거 리더(최근 {_RECOVERY_MONTHS}개월 확정 신호의 RS Top3 이력) 중 "
        f"회복조건 셋(현재가>MA{120} · 6개월 RS>0 · 20일 고점 대비 −15~−5% 놀림목)을 "
        f"다 채운 섹터에 총 {int(_RECOVERY_TOTAL_W*100)}%, 나머지 현금"),
    "NON_TREND_RS": ("NON_TREND",
        "현재 RS Top3(지수를 웃도는 섹터만)에 '지수 상회 섹터 비율' 4분위 비중(25/50/75/100%)"),
    "TREND_RS_TOP3": ("TREND", "현재 RS Top3 에 100%(현금 0)"),
    "CASH": (None, "구간 조건은 맞지만 매수 대상이 없어 현금 — 별도 구간이 아니라 결과"),
}


def _state_table_html() -> str:
    import html as _hh
    rows = []
    for key, label in STATE_LABEL.items():
        regime, rule = _STATE_RULE[key]
        rows.append(f"<tr><th>{_hh.escape(label)}</th>"
                    f"<td>{_hh.escape(REGIME_LABEL.get(regime, '어느 구간이든'))}</td>"
                    f"<td>{_hh.escape(rule)}</td></tr>")
    return ("<table class='mini-tbl'><tr><th>상태</th><th>구간</th><th>규칙</th></tr>"
            + "".join(rows) + "</table>")


def _tranche_table_html() -> str:
    """역추세 단계매수 트랜치 — `_DD_TRANCHES` 에서 생성."""
    rows = "".join(f"<tr><th>{dd:+.0f}% 이하</th><td>{int(w*100)}%</td></tr>"
                   for dd, w in sorted(_DD_TRANCHES, key=lambda t: -t[0]))
    return ("<table class='mini-tbl'><tr><th>지수 252일 고점 대비 낙폭</th>"
            "<th>지수 비중</th></tr>" + rows + "</table>")


def _threshold_line() -> str:
    """구간 경계 — `_B_*` 상수에서."""
    return (f"Breadth &lt; {_B_RECOVERY:.0f}% 역추세 · {_B_RECOVERY:.0f}~{_B_NON_TREND:.0f}% 회복 · "
            f"{_B_NON_TREND:.0f}~{_B_TREND:.0f}% 비추세 · ≥ {_B_TREND:.0f}% 추세")


def _pct_s(v, digits: int = 2) -> str:
    return "—" if v is None else f"{v:,.{digits}f}%"


def _cash_why(rec: dict) -> str:
    """`현금 대기` 옆에 **왜** 현금인지 한 줄. 없으면 아무것도 안 붙인다.

    ⚠️ 저장된 (구간·상태·DD)에서 **렌더타임에 파생**하므로 옛 확정 기록도
    그대로 따라온다 — 백필 패스가 필요 없다(#270). 모르는 조합엔 지어내지
    않는다(#165).
    """
    import html as _hh          # `_h` 는 렌더 함수 안의 지역 import 다
    why = cash_reason(rec.get("regime"), rec.get("state"), rec.get("dd_pct"))
    if not why:
        return ""
    return (f"<div class='si-note' style='margin-top:2px'>"
            f"{_hh.escape(why)}</div>")


def _live_cash_why(d: dict) -> str:
    """**라이브 카드**의 현금 사유 한 줄 — 없으면 빈 문자열.

    ⚠️ `cash_reason` 은 2026-09-07 에 확정 이력 표를 위해 만들었는데(#298),
    정작 위쪽 카드는 `투자 대상 없음(현금)` 까지만 적고 **왜** 현금인지는
    말하지 않았다 — 사용자가 "위에 표랑 아래 표랑 전략이 다른데" 를 물은
    이유가 정확히 그것이다(계산해 둔 판정을 표시까지 배선하지 않는 실수:
    #123·#129·#189·#228·#234·#292 계열).

    ⚠️ 표 안의 `_cash_why` 와 **클래스를 공유하지 않는다** — `.si-note` 는
    `_BS_CSS` 에서 `.bs-tbl .si-note` 로만 정의돼 있어 표 밖에서 쓰면 본문
    크기로 뜬다(#201·#273 "지금 쓰는 곳이 전부 그 번들인가"). 카드 줄은
    `_BOARD_CSS` 가 정의하는 `.sub` 를 쓴다.
    """
    import html as _hh
    why = cash_reason((d or {}).get("regime"), (d or {}).get("state"),
                      (d or {}).get("dd_pct"))
    if not why:
        return ""
    return (f"<div class='sub' style='margin-top:4px'>💵 현금인 이유 — "
            f"{_hh.escape(why)}</div>")


def _breadth_cell(rec: dict) -> str:
    """확정 이력의 Breadth 칸 — 있으면 **분모까지** 적는다(`30.77% (4/13)`).

    ⚠️ %만 적으면 사용자가 눈으로 나눠 검산할 수 없고, 원천 절단으로 분모가
    줄어든 달을 정상 달과 구별할 수도 없다(#33·#45, 사용자 2026-09-07
    "8월말 현금대기가 맞는거야?"). 옛 기록엔 분모가 없으므로 **지어내지 않고**
    %만 적는다(#32).
    """
    pct = _pct_s(rec.get("breadth_pct"))
    above, counted = rec.get("breadth_above"), rec.get("breadth_counted")
    if above is None or not counted:
        return pct
    skip = rec.get("breadth_skipped") or []
    tail = f" ({above}/{counted}" + (f", 제외 {len(skip)}" if skip else "") + ")"
    return pct + tail


def _market_section(d: dict) -> str:
    import html as _h
    if not d:
        return ""
    mkt = d.get("market", "")
    regime = d.get("regime")
    state = d.get("state") or "—"
    badge = ("<span class='sub'>월말 종가 확정 신호</span>" if d.get("is_confirmed")
             else "<span class='sub'>중간점검 — 3시간마다 재계산(봇 기동 직후 1회) · "
                  "Breadth·DD·RS 는 <b>일봉 종가</b> 입력이라 거래일마다 한 번 "
                  "바뀝니다(심리·변동성 지표는 재계산 때마다 갱신) · "
                  "확정은 월말 종가 기준</span>")
    rows = "".join(
        f"<tr class='{'on' if key == regime else ''}'>"
        f"<td>{_h.escape(label)}</td><td>{rng}</td><td>{_h.escape(st)}</td>"
        f"<td>{_h.escape(strat)}</td><td>{_h.escape(tgt)}</td>"
        f"<td>{_h.escape(regime_weight_cell(w, active=(key == regime), total_w=d.get('total_w')))}</td></tr>"
        for key, label, rng, st, strat, tgt, w in _REGIME_TABLE)
    # ⚠️ 정수로 자르면 **눈으로 더했을 때 안 맞는다**: RS Top3 = 1/3 씩인데
    # 33+33+33 = 99% 인데 옆 칸은 '최종 투자비중 100%' 였다(사용자 2026-08-20
    # 캡처). 실수 #33("나란히 놓인 칸은 산수가 맞아야 한다")과 같은 종류 —
    # 합이 정수로 안 떨어지면 소수 1자리로 보여 준다.
    _tg = d.get("targets") or []
    _int_ok = all(abs(t["weight"] * 100 - round(t["weight"] * 100)) < 1e-9
                  for t in _tg)
    tgt_html = " · ".join(
        f"{_h.escape(t['name'])} {t['weight'] * 100:{'.0f' if _int_ok else '.1f'}}%"
        for t in _tg) or "없음(현금)"
    rs_html = " · ".join(
        f"{_h.escape(m['name'])} {m['rs']:+.1f}%" if m.get("rs") is not None
        else _h.escape(m["name"]) for m in (d.get("rs_ranked") or [])) or "—"
    miss = d.get("sectors_missing") or []
    miss_html = (f"<div class='sub'>⚠️ 제외: {_h.escape('·'.join(miss))} "
                 f"(데이터 없음 — 티커 확인 필요)</div>" if miss else "")
    b = d.get("breadth") or {}
    # ⚠️ 그 **시장의** 지표를 싣는다 — KR=VKOSPI(KIS) · 그 밖=CNN F&G(美).
    # 문구·라벨은 `sentiment_text` 가 만들고 여기선 escape 만 한다(#38).
    sent = d.get("sentiment") or {}
    sent_s = _h.escape(sentiment_text(sent))
    hist = "".join(
        f"<tr><td>{_h.escape(str(r.get('month', '')))}"
        # ⚠️ **어느 종가로 확정했는지**를 적는다 — 원천이 늦으면 월말이 며칠
        # 이르게 굳을 수 있고(2026-09-07 실측: 2026-08 이 08-28 로 굳었는데
        # 실제 마지막 거래일은 08-31), 월만 적으면 그 사실이 안 보인다(#43).
        f"<div class='si-note'>{_h.escape(str(r.get('asof', '') or ''))}"
        f" 종가</div></td>"
        # ⚠️ **구간**은 기록에 있는데 표에 열이 없었다 — 사용자가 "이거는
        # 역추세·추세·회복·비추세 중 어떤거야?" 를 물어야 했다(2026-09-07).
        # 상태만으로는 못 가른다(`현금 대기` 는 네 구간 모두에서 나온다).
        f"<td>{_h.escape(REGIME_LABEL.get(r.get('regime'), '—'))}</td>"
        f"<td>{_h.escape(STATE_LABEL.get(r.get('state'), r.get('state') or '—'))}"
        f"{_cash_why(r)}</td>"
        f"<td class='num'>{_breadth_cell(r)}</td>"
        f"<td class='num'>{_pct_s(r.get('dd_pct'))}</td>"
        f"<td class='num'>{(r.get('index_w') or 0) * 100:.0f}%</td>"
        f"<td class='num'>{(r.get('total_w') or 0) * 100:.0f}%</td>"
        f"<td class='num'>{(r.get('cash_w') or 0) * 100:.0f}%</td></tr>"
        for r in reversed(load_signals(mkt, limit=24)))
    # ⚠️ 기준일은 **stat-grid 바로 아래 자기 줄**로 뺀다 — 옛 판은 투자대상·RS·
    # F&G 뒤에 묻혀 있어 사용자가 "언제기준인지 명시해줘" 를 물어야 했다
    # (2026-09-11, 시장타이밍 보드의 `기준 … · 최근 종가 …` 와 같은 규약).
    # 같은 지수를 두 화면이 그리므로 종가를 같이 적어 눈으로 대조하게 한다(#51).
    asof_html = _asof_line(d)
    live_cash_why = _live_cash_why(d)
    # ⚠️ 숫자 컬럼은 **헤더도** 우측정렬(class="num") — 셀만 우측이고 헤더가
    # 좌측이면 제목과 값이 어긋나 보인다(사용자 2026-08-16 스크린샷).
    hist_html = (f"<table class='bs-tbl'><thead><tr><th>월</th><th>구간</th><th>상태</th>"
                 f"<th class='num'>Breadth</th><th class='num'>지수 DD</th>"
                 f"<th class='num'>지수비중</th><th class='num'>최종비중</th>"
                 f"<th class='num'>현금</th></tr></thead><tbody>{hist}"
                 f"</tbody></table>"
                 if hist else
                 "<div class='sub'>아직 확정 신호 이력이 없습니다 — "
                 "첫 월말 종가에 기록됩니다.</div>")
    return f"""
<div class="panel"><div class="panel-title">🧭 {_h.escape(mkt)} — {_h.escape(REGIME_LABEL.get(regime, '판정 불가'))}
· {_h.escape(STATE_LABEL.get(state, state))}</div>
{badge}
<div class="stat-grid">
<div class="stat"><div class="k">Breadth (MA120 상회)</div><div class="v">{_pct_s(d.get('breadth_pct'), 2)}</div></div>
<div class="stat"><div class="k">{_h.escape(d.get('bench_name', '지수'))} 252일 DD</div><div class="v">{_pct_s(d.get('dd_pct'))}</div></div>
<div class="stat"><div class="k">최종 투자비중</div><div class="v">{(d.get('total_w') or 0) * 100:.0f}%</div></div>
<div class="stat"><div class="k">현금</div><div class="v">{(d.get('cash_w') or 0) * 100:.0f}%</div></div>
</div>
{asof_html}
<div class="sub" style="margin-top:6px">투자 대상 <b>{tgt_html}</b> · RS 순위(상위 5, 지수 대비 6개월) {rs_html}
· {sent_s}</div>
{live_cash_why}
{miss_html}
<div class="sub">표본 {_h.escape(str(d.get('source_label', '')))} {b.get('counted', 0)}개
(MA120 상회 {b.get('above', 0)}개) · {_h.escape(str(d.get('resolution_note', '')))}</div>
<div class="sub" style="margin-top:10px">구간별 <b>규칙</b>(고정) — 현재 구간이 강조되고,
그 행의 '비중' 에 <b>이번 판정의 실제 비중</b>을 덧붙입니다(규칙은 상한이고, 조건을 채운
대상이 없으면 실제는 0%)</div>
<table class="bs-tbl"><tr><th>구간</th><th>Breadth</th><th>상태</th>
<th>전략</th><th>투자 대상</th><th>비중</th></tr>{rows}</table>
<div class="sub" style="margin-top:10px">확정 신호 이력(월말 종가 기준)</div>
{hist_html}
</div>"""


_BS_CSS = """
<style>
.bs-tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
.bs-tbl th,.bs-tbl td{padding:5px 8px;border-bottom:1px solid var(--border,#2a3656);text-align:left}
.bs-tbl th{color:var(--fg-soft,#93a0bd);font-weight:500}
.bs-tbl th.num,.bs-tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.bs-tbl tr.on{background:rgba(77,163,255,.14);font-weight:600}
/* 각주(확정 기준일·현금 사유) — 표보다 작고 흐리게. ⚠️ 이 클래스를 쓰면서
   **이 페이지 번들에 정의를 안 두면** 각주가 본문 크기로 떠서 표보다 커
   보인다(실수 #201 "너무 크잖아"). 대시보드에 같은 이름이 있어도 이 페이지는
   그 번들을 안 쓴다 — 쓰는 곳이 전부 그 번들인지 먼저 답할 것(#273). */
.bs-tbl .si-note{font-size:11px;color:var(--fg-soft,#93a0bd);font-weight:400}
/* 기준일 줄 — stat-grid 바로 아래, 본문보다 한 톤 또렷하게(묻히면 또 묻는다) */
.bs-asof{margin-top:8px;font-size:12.5px}
</style>
"""


def _asof_line(d: dict) -> str:
    """`기준 2026-09-10(배지) · 최근 종가 7591.7 · KOSPI` 한 줄(순수).

    사용자 2026-09-11 "한국/미국에 언제기준인지 명시해줘. 시장타이밍보드에 나온
    '기준 2026-09-10 · 최근 종가 7591.7' 와 같이". 옛 판은 기준일을 투자대상·RS·
    F&G 뒤에 묻어 놔서 '이거 최신이야?' 에 화면이 답하지 못했다(#43 · 규칙 10b).

    ⚠️ 기준일이 없으면 **침묵하지 말고 그렇게 말한다** — 빈 줄은 '오늘 것'으로
    읽힌다(#43 침묵이 최악). 종가는 못 받을 수 있으므로 있을 때만 붙인다(#165).
    """
    import html as _h          # `_h` 는 렌더 함수 안의 지역 import 다(모듈 전역 아님)

    asof = str((d or {}).get("asof") or "")
    if not asof:
        return ("<div class='sub bs-asof'>기준 <b>미기록</b> — 지수 시계열을 "
                "받지 못해 어느 종가 기준인지 말할 수 없습니다</div>")
    close = (d or {}).get("latest_close")
    bits = [f"기준 <b>{_h.escape(asof)}</b>"
            f"{_session_badge((d or {}).get('market'), asof)}"]
    if isinstance(close, (int, float)):
        # 지수는 소수 1자리(시장타이밍과 같은 눈금) — 두 화면을 나란히 놓고
        # 같은 값인지 확인할 수 있어야 한다(#51).
        bits.append(f"최근 종가 {close:,.1f}")
    name = str((d or {}).get("bench_name") or "")
    if name:
        bits.append(_h.escape(name))
    return "<div class='sub bs-asof'>" + " · ".join(bits) + "</div>"


def _session_badge(market, asof) -> str:
    """기준일 옆 세션 상태 배지 — 시장타이밍 보드와 **같은 판정**을 재사용.

    사용자 2026-08-20: "Breadth 4구간 전략도 가장 최근에 종가를 가져오는거
    맞지?" — 화면이 스스로 답해야 하는 질문이다. KR 은 장중(09:00~15:30)에
    재계산되면 기준일이 오늘이지만 그 봉은 **종가가 아니라 부분봉**이라
    Breadth·DD·RS 가 확정 전 값이다(감사는 그걸 찍는데 이 화면은 조용했다).
    복제하면 두 화면 판정이 갈라지므로 market_timing 것을 그대로 쓴다."""
    if not (market and asof):
        return ""
    try:
        from bot.market_timing import _idx_stale
        return _idx_stale(str(market), str(asof))
    except Exception as exc:                                   # noqa: BLE001
        log.debug("breadth_strategy: 세션 배지 실패: %s", exc)
        return ""


def render_page(data: dict, now=None) -> str:
    """breadth_strategy.html — KR·US 섹션 + 4구간 표 + 확정 신호 이력."""
    import html as _h

    from bot.fred_boards import _BOARD_CSS, _NAV, _theme_head
    ts = (now or datetime.now(_KST)).strftime("%Y-%m-%d %H:%M KST")
    sections = "".join(_market_section(data.get(m) or {}) for m in ("KR", "US"))
    if not sections:
        sections = ("<div class='panel'><div class='sub'>데이터를 받지 못했습니다 "
                    "— 섹터 ETF 히스토리·지수 조회를 확인하세요.</div></div>")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breadth 전략</title>{_BOARD_CSS}{_BS_CSS}
{_theme_head()}
</head><body><div class="wrap">
{_NAV}
<h1>🧭 <em>Breadth</em> 4구간 전략</h1>
<div class="sub">시장 내부 확산도(Breadth = 섹터 ETF 중 MA120 상회 비율)로 구간을 나눠
투자 대상과 비중을 정합니다 · 갱신 {_h.escape(ts)}</div>
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>Breadth</b> — 섹터 ETF 중 120일 이평선 위에 있는 비율. 낮으면 소수만 버티는 장,
높으면 전반적 참여.<br>
<b>카드의 숫자</b> — "Breadth (MA120 상회)" 는 섹터 ETF 중 120일 이평선 위에 있는
비율(분모 = 히스토리가 충분한 섹터만, 빠진 개수는 같이 적습니다) · "지수 252일 DD" 는
그 시장 벤치마크 지수(KR=KOSPI · US=S&amp;P 500)의 <b>최근 252거래일 고점 대비 낙폭</b> ·
"최종 투자비중" 은 아래 상태 규칙으로 정한 총 투자 비중, "현금" 은 그 나머지입니다.<br>
<b>RS(상대강도)</b> — 섹터 ETF 의 <b>6개월(126거래일) 수익률 − 지수의 같은 기간 수익률</b>
(%p). 양수면 지수를 이긴 것. "RS Top3" 는 이 값이 큰 순서 셋입니다.<br>
<b>구간</b> — 30/40/60% 를 경계로 역추세·회복·비추세·추세 4구간({_threshold_line()}). 구간마다 투자 대상과
비중이 달라집니다(아래 표에서 <b>현재 구간이 강조</b>됩니다).<br>
<b>상태</b> — 구간이 정해지면 그 안에서 <b>무엇을 얼마나</b> 사는지가 상태입니다.
{_state_table_html()}
<b>역추세 단계매수 트랜치</b> — 낙폭이 깊어질수록 지수 비중을 올립니다. 첫 트랜치(−12%)에
못 미치면 비중 0 = "현금 대기" 입니다.
{_tranche_table_html()}
<b>현금 대기</b> — 역추세 구간이어도 지수 낙폭이 −12%에 못 미치면 매수 트랜치가 0이라
현금입니다(별도 구간이 아니라 이 조건의 결과).<br>
<b>중간점검 vs 확정</b> — 원 전략은 <b>월말 종가 신호를 다음 거래일부터 적용</b>합니다.
위쪽 카드(중간점검)는 <b>3시간 주기</b>로 다시 계산되고 봇 기동 직후에도 한 번 돕니다.
다만 판정 입력(Breadth·지수 DD·RS)이 <b>일봉 종가</b>라 그 값들이 실제로 바뀌는 건
거래일마다 한 번(그 시장의 직전 거래일 종가가 확정된 뒤)이고, 기준일을 카드에
<b>기준일 YYYY-MM-DD</b> 로 적어 둡니다. 같은 줄의 심리·변동성 지표(한국 VKOSPI ·
그 밖 F&amp;G)는 판정에 쓰지 않는 표시용이라 재계산 때마다(3시간) 최신값으로 바뀝니다.
아래 <b>확정 신호 이력</b>만 월말 종가 기준이며, 이력에 기록되는 값은 그 달 마지막
거래일까지만 잘라 계산해 장중에 조회해도 달라지지 않습니다.<br>
<b>확정 신호 이력 표의 열</b> — 월 · 구간 · 상태 · Breadth(그 달 마지막 거래일 종가
기준, 분모 같이 표기) · 지수 DD · 지수비중(역추세 트랜치분) · 최종비중 · 현금. 확정에
쓴 종가 날짜를 같이 적으니, 월말 봉이 늦게 들어와 정정된 달은 그 날짜가 바뀝니다.
"현금 대기" 옆의 사유는 저장된 (구간·상태·DD)에서 화면이 만들어 붙입니다.<br>
<b>비추세 구간의 'RS 강도'</b> — 원 전략은 25/50/75/100% 4단계만 밝히고 강도의 정의를
주지 않아, 여기서는 <b>지수를 이긴 섹터의 비율</b>을 4분위로 나눠 씁니다(≤25%→25% …
&gt;75%→100%). 표본 수와 무관한 정의라 KR·US 에 같은 뜻으로 적용됩니다 —
<b>해석이 들어간 유일한 지점</b>이므로 다른 정의를 원하시면 알려주세요.<br>
<b>심리·변동성 지표</b> — 카드 오른쪽 끝에 <b>그 시장의</b> 지표를 싣습니다:
한국은 <b>VKOSPI</b>(코스피200 변동성지수 · KRX 산출, KIS 제공 · 종가 기준일 같이 표기),
그 밖은 <b>CNN Fear &amp; Greed</b>(미국 증시 지표라 <b>美 CNN</b> 을 붙입니다).
둘 다 <b>기록·표시만</b> 하고 판정에는 쓰지 않습니다(원 전략에 게이트로 쓴다는
근거가 없어 임의 규칙을 만들지 않았습니다). 과거값을 되돌릴 수 없는 지표라
월말 확정 이력에는 남기지 않습니다(오늘 값을 그 달 값인 척하지 않기 위해).
값을 못 받으면 <b>왜 없는지</b> 그 자리에 적습니다 — 다른 시장 지표로 대신
채우지 않습니다.<br>
<b>위 카드와 아래 구간 표가 달라 보일 때</b> — 아래 구간 표는 <b>규칙</b>(고정
문구)이고 위 카드는 <b>오늘 그 규칙을 적용한 결과</b>입니다. 예: 회복 구간의 규칙은
"총 50%" 지만 회복조건 셋(현재가&gt;MA120 · 6개월 RS&gt;0 · 20일 고점 대비 −15~−5%)을
모두 채운 과거 리더가 <b>한 개도 없으면</b> 살 대상이 없어 최종 투자비중 0% ·
현금 100% 가 됩니다. 그래서 현재 구간 행의 '비중' 칸에는 규칙 옆에
<b>→ 이번 판정 N%</b> 를 덧붙이고, 카드에는 <b>현금인 이유</b>를 한 줄로 적습니다.<br>
<b>⚠️ 시장 간 Breadth %를 직접 비교하지 마세요</b> — 표본(KODEX 섹터 vs SPDR GICS)과
섹터 수가 달라 같은 값이 같은 의미가 아닙니다.<br>
자동 신호이므로 참고용 — 확정 판단 금지.
</details>
{sections}
<div class="footer">Breadth 4구간 전략 — 신호는 참고용(투자 판단 아님) · NOAH</div>
</div></body></html>"""


def build_all() -> dict:
    """KR·US 중간점검 스냅샷 + 미기록 확정 달 백필(멱등). 한 시장이 실패해도
    다른 시장은 그대로 만든다."""
    out: dict = {}
    for mkt in ("KR", "US"):
        try:
            live, written = build_with_signals(mkt)
        except Exception as exc:
            log.warning("breadth_strategy: %s 스냅샷 실패: %s", mkt, exc)
            continue
        if not live:
            continue
        if written:
            log.info("breadth_strategy: %s 확정 신호 %d개월 기록(%s)",
                     mkt, len(written), ", ".join(r["month"] for r in written))
        out[mkt] = live
    return out


def regenerate() -> None:
    """breadth_strategy.html 재생성. 실패해도 기존 파일 유지(graceful)."""
    from bot.dashboard import ARCHIVE_ROOT
    try:
        html = render_page(build_all())
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "breadth_strategy.html").write_text(html, encoding="utf-8")
        log.info("breadth_strategy: 페이지 재생성 완료")
    except Exception as exc:
        log.warning("breadth_strategy: 재생성 실패: %s", exc)


# ── 진단 CLI ────────────────────────────────────────────────────────────────
# ⚠️ **읽기 전용**이다 — `build_with_signals` 는 확정 신호를 append 하므로
# 절대 부르지 않는다(진단이 자기가 읽을 신호를 오염시키면 안 된다, #30·#264·
# #283). 수집(`_fetch_market`)과 순수 계산(`_assemble`)만 쓴다.
def _sector_lines(sliced: dict, cut: str) -> list[str]:
    """섹터별 (마지막 봉·종가·MA120·상회 여부) 한 줄씩 — 분모를 눈으로 센다.

    ⚠️ 확정 이력은 Breadth **%만** 남기므로(2026-09-07 실측) '30.8% 가 4/13
    인가 4/12 인가' 를 사람이 확인할 방법이 없었다. 표본 원문을 같이 찍는다
    (#109 · #43).
    """
    out = []
    for label, closes in sorted(sliced.items()):
        m = ma(closes, 120)
        if m is None:
            out.append(f"   {label:<14} 봉 {len(closes):>4}개 — MA120 불가"
                       f"(기간부족) → **분모에서 제외**")
            continue
        c = closes[-1]
        mark = "위 ✅" if c > m else "아래  "
        out.append(f"   {label:<14} 봉 {len(closes):>4}개 · 종가 {c:>10,.2f}"
                   f" · MA120 {m:>10,.2f} · {mark}")
    return out


def _why_verdict(rec: dict, snap: dict) -> tuple[str, list[str]]:
    """기록 ↔ 재계산 대조 → (판정, 어긋난 칸들).

    ⚠️ 대조할 게 없으면 통과가 아니라 **판정 불가**다(#54·#274). 그리고
    옛 기록은 `breadth_pct` 가 1자리로 반올림돼 있으므로 0.05%p 여유를 준다
    (반올림 차이를 '불일치' 로 부르면 진짜 불일치를 가린다).
    """
    if not rec:
        return "❓ 판정 불가 — 그 달 확정 기록이 없다", []
    if snap.get("breadth_pct") is None:
        return "❓ 판정 불가 — 그 시점 히스토리가 모자라 재계산이 안 된다", []
    bad = []
    a, b = rec.get("breadth_pct"), snap.get("breadth_pct")
    if a is None or b is None or abs(float(a) - float(b)) > 0.05:
        bad.append(f"Breadth 기록 {_pct_s(a)} vs 재계산 {_pct_s(b)}")
    for key, name in (("dd_pct", "지수 DD"), ("regime", "구간"),
                      ("state", "상태"), ("total_w", "최종비중"),
                      ("index_w", "지수비중")):
        x, y = rec.get(key), snap.get(key)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if abs(float(x) - float(y)) > 0.05:
                bad.append(f"{name} 기록 {x} vs 재계산 {y}")
        elif x != y:
            bad.append(f"{name} 기록 {x} vs 재계산 {y}")
    if bad:
        return "❌ 기록과 재계산이 다르다", bad
    return "✅ 기록 = 재계산(그 달 종가 기준으로 재현됨)", []


def _cli_why(market: str, month: str | None) -> int:
    """확정 신호 한 달을 **원문까지** 펼쳐 보인다 → rc(0 일치 / 1 그 외).

    사용자 2026-09-07 "8월말 현금대기가 맞는거야? 8월 마지막날엔 극단적
    과매도로 봤는데" — 화면은 Breadth **%만** 보여줘 (a) 분모가 몇이었는지
    (b) 그 달 마지막 며칠이 어떻게 움직였는지 를 답할 수 없었다.
    """
    import sys

    print(f"🧭 breadth_strategy --why · {market.upper()} · 읽기 전용"
          "(확정 신호를 쓰지 않는다)")
    print(f"   인터프리터: {sys.executable}")
    print(f"   신호 로그: {signal_path(market)}")

    recs = load_signals(market, limit=10_000)
    if not recs:
        print("❌ 확정 신호 이력이 0건 — 아직 한 달도 기록되지 않았다"
              "(첫 월말 종가에 기록된다)")
        return 1
    months = [str(r.get("month") or "") for r in recs]
    target = month or months[-1]
    rec = next((r for r in recs if str(r.get("month") or "") == target), None)
    if rec is None:
        print(f"❌ {target} 기록이 없다 — 있는 달: {', '.join(months)}")
        return 1
    print(f"\n① 기록된 확정 신호 {target} (기준일 {rec.get('asof')})")
    print(f"   Breadth {_pct_s(rec.get('breadth_pct'))}"
          f" · DD {_pct_s(rec.get('dd_pct'))}"
          f" · 구간 {REGIME_LABEL.get(rec.get('regime'), rec.get('regime'))}"
          f" · 상태 {STATE_LABEL.get(rec.get('state'), rec.get('state'))}")
    print(f"   지수비중 {rec.get('index_w')} · 최종비중 {rec.get('total_w')}"
          f" · 현금 {rec.get('cash_w')} · 대상 {rec.get('targets')}")
    if rec.get("breadth_above") is None:
        print("   ⚠️ 이 기록엔 **분모(상회/표본)가 없다** — 옛 형식이라"
              " %만 남았다. 아래 ③ 이 그 자리를 대신한다.")
    else:
        print(f"   표본 {rec.get('breadth_counted')}개 중 상회"
              f" {rec.get('breadth_above')}개"
              f" · 기간부족 제외 {rec.get('breadth_skipped') or []}")

    print(f"\n② 원천 재수집 — 섹터 시계열을 받는다(수십 초)")
    got = _fetch_market(market)
    if not got:
        print("❌ 섹터 레지스트리가 비었다 — 재계산 불가")
        return 1
    sectors, bench_name, bench_rows, rows_by_label, missing = got
    print(f"   섹터 {len(rows_by_label)}/{len(sectors)}개 수신"
          f" · 벤치 {bench_name} {len(bench_rows)}봉"
          + (f" · 미수신 {missing}" if missing else ""))
    if not bench_rows:
        print("❌ 벤치마크 시계열을 못 받았다 — 재계산 불가(원천 장애 의심)")
        return 1

    cut = str(rec.get("asof") or "")
    snap = _assemble(market, sectors, bench_name, bench_rows, rows_by_label,
                     missing, cut=cut)
    sliced = {k: [r["close"] for r in _cut_rows(v, cut)]
              for k, v in rows_by_label.items()}
    b = snap.get("breadth") or {}
    print(f"\n③ {cut} 종가로 재계산 — 상회 {b.get('above')}개"
          f" / 표본 {b.get('counted')}개 = {_pct_s(snap.get('breadth_pct'))}")
    for line in _sector_lines(sliced, cut):
        print(line)

    n = b.get("counted") or 0
    if n:
        print(f"\n④ 경계 민감도(표본 {n}개 · 한 섹터 = {100 / n:.1f}%p)")
        for k in (b.get("above", 0) - 1, b.get("above", 0),
                  b.get("above", 0) + 1):
            if 0 <= k <= n:
                pct = round(k / n * 100, 1)
                reg = classify_regime(pct)
                here = "  ← 이 달" if k == b.get("above") else ""
                print(f"   상회 {k:>2}개 → {pct:>5.1f}%"
                      f" · {REGIME_LABEL.get(reg, reg)}{here}")

    print(f"\n⑤ 그 달 마지막 거래일들 — 날짜마다 종가로 다시 계산")
    same_month = [r["date"] for r in bench_rows
                  if str(r.get("date") or "")[:7] == target]
    if not same_month:
        print(f"   ❓ 벤치 시계열에 {target} 봉이 없다"
              " — 400일 창 밖이라 이 구간은 판정 불가")
    for d in same_month[-7:]:
        s = _assemble(market, sectors, bench_name, bench_rows, rows_by_label,
                      missing, cut=d)
        sb = s.get("breadth") or {}
        star = "  ← 월말 확정" if d == cut else ""
        print(f"   {d}  상회 {sb.get('above')}/{sb.get('counted')}"
              f" = {_pct_s(s.get('breadth_pct'))}"
              f" · {REGIME_LABEL.get(s.get('regime'), s.get('regime'))}"
              f" · {STATE_LABEL.get(s.get('state'), s.get('state'))}{star}")

    verdict, bad = _why_verdict(rec, snap)
    print(f"\n⑥ 판정: {verdict}")
    for line in bad:
        print(f"   · {line}")
    print("   ⚠️ 화면의 '중간점검' 은 **오늘 마지막 봉**(장중이면 부분봉)이고"
          " 확정 이력은 **그 달 마지막 종가**다 — 둘이 다른 건 정상이다.")
    return 0 if verdict.startswith("✅") else 1


if __name__ == "__main__":                                     # pragma: no cover
    import argparse

    _ap = argparse.ArgumentParser(
        description="Breadth 전략 확정 신호 진단(읽기 전용)")
    _ap.add_argument("--why", nargs="?", const="", metavar="YYYY-MM",
                     help="그 달 확정 신호를 재계산해 대조(생략 시 최신 달)")
    _ap.add_argument("--market", default="KR", help="KR 또는 US")
    _a = _ap.parse_args()
    if _a.why is not None:
        raise SystemExit(_cli_why(_a.market, _a.why or None))
    _ap.print_help()
