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

F&G 지수·히스토그램은 **기록·표시만** 한다. 캡처 어디에도 게이트로 쓰인다는
근거가 없어(「과매도 확대 신호: False (Breadth <30% 아님)」는 Breadth 조건이다)
없는 규칙을 지어내지 않는다.

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
    return {"pct": round(above / counted * 100, 1) if counted else None,
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
    out.sort(key=lambda r: str(r.get("month") or ""))
    return out[-limit:]


def append_signal(market: str, rec: dict) -> bool:
    """월말 확정 신호 append. 같은 `month` 가 이미 있으면 쓰지 않는다
    (재실행·재시작에 멱등 — 6시간 주기로 도는 잡이라 필수)."""
    month = rec.get("month")
    if not month:
        return False
    if any(r.get("month") == month for r in load_signals(market, limit=10_000)):
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
      · KR 장중(09:00~15:30)이 6시간 스케줄러보다 넓어 **부분봉**이 월말
        종가로 박히고, 멱등성 때문에 진짜 종가는 영원히 무시됐다.
      · 휴장일(KRX 12/31, US 성금요일 등)에 월 마지막 평일 봉이 없으면
        그 달 신호가 통째로 누락됐다.
    닫힌 달의 마지막 봉은 **이미 확정된 데이터**이고, 휴장일이 언제든
    '그 달의 마지막 봉' 은 정의상 하나뿐이라 캘린더가 필요 없다.

    **인덱스가 아니라 날짜**를 돌려주는 이유: 섹터마다 시계열 길이가 다르다
    (네이버 폴백을 탄 섹터는 3년치, 아닌 섹터는 1년치). 벤치마크 인덱스로
    다른 섹터를 자르면 엉뚱한 봉에서 잘려 '월말 확정' 값에 월말 이후 봉이
    섞인다 — 멱등 기록이라 그 오염이 영구화된다(2026-08-16 독립 리뷰).

    한 달에 여러 개가 아니라 **완결된 달 전부**를 돌려주는 이유: 6시간 잡이
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

    # F&G 는 **현재값만** 있는 지표라 과거 시점으로 되돌릴 수 없다. 확정분
    # (cut 지정)에 오늘 값을 박으면 그 달의 값인 척하는 거짓 기록이 된다
    # — 안 넣는다(2026-08-16 독립 리뷰). 어차피 판정에 안 쓰는 표시용이다.
    fng = {}
    if cut is None:
        try:
            from bot.fear_greed_client import fetch_fear_greed
            fng = fetch_fear_greed() or {}
        except Exception as exc:
            log.debug("breadth_strategy: F&G 조회 실패: %s", exc)

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
        "fng": {"index": fng.get("score"), "label": fng.get("rating_kr", "")},
        "asof": dates[-1] if dates else "",
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
    have = {str(r.get("month") or "") for r in load_signals(market, limit=10_000)}
    written: list[dict] = []
    for d in completed_month_ends([r["date"] for r in bench_rows],
                                  limit=_BACKFILL_MONTHS):
        if d[:7] in have:
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


def min_sectors_for(n: int, pct: float) -> int:
    """n개 표본에서 breadth 가 `pct`% **이상**이 되는 최소 섹터 수.

    경계는 '이상' 포함이다(classify_regime 이 `<` 로 자른다). 딱 나누어
    떨어지는 경우(10개의 30% = 3개)를 올림이 밀어내지 않도록 아주 작은
    여유를 뺀 뒤 올린다 — 분기 없이 한 줄로 둘 다 맞는다."""
    import math
    return max(0, min(n, math.ceil(n * pct / 100.0 - 1e-9)))


def _pct_s(v, digits: int = 2) -> str:
    return "—" if v is None else f"{v:,.{digits}f}%"


def _market_section(d: dict) -> str:
    import html as _h
    if not d:
        return ""
    mkt = d.get("market", "")
    regime = d.get("regime")
    state = d.get("state") or "—"
    badge = ("<span class='sub'>월말 종가 확정 신호</span>" if d.get("is_confirmed")
             else "<span class='sub'>중간점검 — 6시간마다 재계산(봇 기동 직후 1회) · "
                  "Breadth·DD·RS 는 <b>일봉 종가</b> 입력이라 거래일마다 한 번 "
                  "바뀝니다(F&amp;G 는 재계산 때마다 갱신) · "
                  "확정은 월말 종가 기준</span>")
    rows = "".join(
        f"<tr class='{'on' if key == regime else ''}'>"
        f"<td>{_h.escape(label)}</td><td>{rng}</td><td>{_h.escape(st)}</td>"
        f"<td>{_h.escape(strat)}</td><td>{_h.escape(tgt)}</td>"
        f"<td>{_h.escape(w)}</td></tr>"
        for key, label, rng, st, strat, tgt, w in _REGIME_TABLE)
    tgt_html = " · ".join(
        f"{_h.escape(t['name'])} {t['weight'] * 100:.0f}%"
        for t in (d.get("targets") or [])) or "없음(현금)"
    rs_html = " · ".join(
        f"{_h.escape(m['name'])} {m['rs']:+.1f}%" if m.get("rs") is not None
        else _h.escape(m["name"]) for m in (d.get("rs_ranked") or [])) or "—"
    miss = d.get("sectors_missing") or []
    miss_html = (f"<div class='sub'>⚠️ 제외: {_h.escape('·'.join(miss))} "
                 f"(데이터 없음 — 티커 확인 필요)</div>" if miss else "")
    b = d.get("breadth") or {}
    fng = d.get("fng") or {}
    # ⚠️ CNN Fear &amp; Greed 는 **미국 증시** 지표다. KR 카드에 'F&G 65' 만
    # 적으면 한국 지표로 읽힌다(사용자 2026-08-16 "F&G 65 는 한국기준이야?").
    # 출처를 값 옆에 붙이고, 판정에 쓰지 않는다는 사실은 가이드에 있다.
    fng_s = (f"{fng['index']:.0f} ({_h.escape(fng.get('label', ''))}, 美 CNN)"
             if fng.get("index") is not None else "—")
    hist = "".join(
        f"<tr><td>{_h.escape(str(r.get('month', '')))}</td>"
        f"<td>{_h.escape(STATE_LABEL.get(r.get('state'), r.get('state') or '—'))}</td>"
        f"<td class='num'>{_pct_s(r.get('breadth_pct'))}</td>"
        f"<td class='num'>{_pct_s(r.get('dd_pct'))}</td>"
        f"<td class='num'>{(r.get('index_w') or 0) * 100:.0f}%</td>"
        f"<td class='num'>{(r.get('total_w') or 0) * 100:.0f}%</td>"
        f"<td class='num'>{(r.get('cash_w') or 0) * 100:.0f}%</td></tr>"
        for r in reversed(load_signals(mkt, limit=24)))
    # ⚠️ 숫자 컬럼은 **헤더도** 우측정렬(class="num") — 셀만 우측이고 헤더가
    # 좌측이면 제목과 값이 어긋나 보인다(사용자 2026-08-16 스크린샷).
    hist_html = (f"<table class='bs-tbl'><thead><tr><th>월</th><th>상태</th>"
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
<div class="sub" style="margin-top:6px">투자 대상 <b>{tgt_html}</b> · RS 순위(상위 5, 지수 대비 6개월) {rs_html}
· F&amp;G {fng_s} · 기준일 {_h.escape(str(d.get('asof', '')))}</div>
{miss_html}
<div class="sub">표본 {_h.escape(str(d.get('source_label', '')))} {b.get('counted', 0)}개
(MA120 상회 {b.get('above', 0)}개) · {_h.escape(str(d.get('resolution_note', '')))}</div>
<table class="bs-tbl" style="margin-top:10px"><tr><th>구간</th><th>Breadth</th><th>상태</th>
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
</style>
"""


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
<b>구간</b> — 30/40/60% 를 경계로 역추세·회복·비추세·추세 4구간. 구간마다 투자 대상과
비중이 달라집니다(아래 표에서 <b>현재 구간이 강조</b>됩니다).<br>
<b>현금 대기</b> — 역추세 구간이어도 지수 낙폭이 −12%에 못 미치면 매수 트랜치가 0이라
현금입니다(별도 구간이 아니라 이 조건의 결과).<br>
<b>중간점검 vs 확정</b> — 원 전략은 <b>월말 종가 신호를 다음 거래일부터 적용</b>합니다.
위쪽 카드(중간점검)는 <b>6시간 주기</b>로 다시 계산되고 봇 기동 직후에도 한 번 돕니다.
다만 판정 입력(Breadth·지수 DD·RS)이 <b>일봉 종가</b>라 그 값들이 실제로 바뀌는 건
거래일마다 한 번(그 시장의 직전 거래일 종가가 확정된 뒤)이고, 기준일을 카드에
<b>기준일 YYYY-MM-DD</b> 로 적어 둡니다. 같은 줄의 F&amp;G 는 판정에 쓰지 않는
표시용이라 재계산 때마다(6시간) 최신값으로 바뀝니다.
아래 <b>확정 신호 이력</b>만 월말 종가 기준이며, 이력에 기록되는 값은 그 달 마지막
거래일까지만 잘라 계산해 장중에 조회해도 달라지지 않습니다.<br>
<b>비추세 구간의 'RS 강도'</b> — 원 전략은 25/50/75/100% 4단계만 밝히고 강도의 정의를
주지 않아, 여기서는 <b>지수를 이긴 섹터의 비율</b>을 4분위로 나눠 씁니다(≤25%→25% …
&gt;75%→100%). 표본 수와 무관한 정의라 KR·US 에 같은 뜻으로 적용됩니다 —
<b>해석이 들어간 유일한 지점</b>이므로 다른 정의를 원하시면 알려주세요.<br>
<b>F&amp;G</b> — 기록·표시만 합니다. 판정에는 쓰지 않습니다(원 전략에 게이트로 쓴다는
근거가 없어 임의 규칙을 만들지 않았습니다). 과거값을 되돌릴 수 없는 지표라
월말 확정 이력에는 남기지 않습니다(오늘 값을 그 달 값인 척하지 않기 위해).<br>
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
