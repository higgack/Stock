"""분기 실적분석 인포그래픽 — 분기 스코어카드 PNG (전 시장).

사용자 2026-08-19 (X @Jaymin_Alpha 스타일). 종목 상세 '분기실적' 탭에서
온디맨드 생성. 구성(위→아래):
  헤더(종목명·티커·마켓·통화·시총·최신분기) / 헤드라인 콜아웃(LLM, 없으면
  생략) / 지표 타일 5종(매출·영업이익·영업이익률·당기순이익·TTM PER,
  YoY·QoQ) / **세로 2단 차트**(각 전체폭: 위=금액 막대, 아래=이익률 % —
  분리된 축이라 이중 Y축 오독이 없다) / 성장동력·리스크 카드(LLM, 없으면
  섹션 생략) / TTM 푸터·각주·출처·면책.

데이터 소스(2026-08-16 멀티마켓 확장):
  KR      = bot/dart_quarterly.get_quarterly_series (DART 단일분기 원천)
  그 외    = bot/quarterly_series.series_from_yfinance (스냅샷에 이미 전
            시장 공통으로 수집된 yfinance 분기 손익 — 추가 호출 0)
숫자는 전부 실측값을 그대로 주입 — 렌더러가 값을 만들지 않는다(환각 0).
이상치(매출 음수·보고서 간 계정 불일치)는 보정하지 않고 해당 항목의 TTM·
PSR 산출에서 제외한 뒤 각주로 이유를 밝힌다. 정성 카드만 LLM
(bot/dart_growth_risk)이고 DART 원문 전용이라 **KR 에서만** 제공된다.

캐시: 파일명 자체가 캐시 키({티커}_{분기키}_{날짜}.png). 분기키는 KR 이
{연도}{reprt_code}, 그 외는 분기 종료일 — 새 분기가 나오면 파일명이 바뀌어
시간 TTL 없이 자동 갱신된다. 날짜를 넣는 이유는 cache_path 주석 참조.

폰트: NanumGothic 부재 시 None 반환(daily_byte_infographic 과 동일 규약)
→ 호출부가 표(HTML)만 보여준다.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger("bot.quarterly_infographic")

# pyplot 은 전역 상태(rcParams·figure 스택)라 동시 렌더가 서로를 오염시킬 수
# 있다. 대시보드는 ThreadingHTTPServer 라 동시 요청이 실제로 가능 → 직렬화.
_RENDER_LOCK = threading.Lock()

# 팔레트 — daily_byte/realestate 인포그래픽과 동일 톤(대시보드 일관성).
_BG = "#070a14"; _PANEL = "#131a2e"; _PANEL2 = "#1a2238"; _LINE = "#2a3656"
_TEXT = "#e8ecf6"; _MUTED = "#93a0bd"; _ACCENT = "#4da3ff"; _ACCENTW = "#22d3ee"
_POS = "#34d399"; _NEG = "#f87171"; _GOLD = "#fbbf24"; _PUR = "#a78bfa"

_IMG_DIR = Path.home() / ".tradingagents" / "archive" / "quarterly_infographic_img"

# 렌더 버전 — 파일명에 들어가 **레이아웃/표기 변경 시 캐시를 무효화**한다.
# 캐시 키가 (티커, 분기, 날짜)뿐이면 오늘 이미 본 종목은 배포 후에도 옛
# 그림이 그대로 뜬다("배포완료 ≠ 화면에 보임", 실수 #11). 렌더러의 출력이
# 달라지는 변경을 하면 이 값을 **반드시** 올릴 것.
#   v2 (2026-08-16) 타일 2줄 YoY/QoQ · Forward PER · 막대 값 라벨 · 축 확대
#   v3 (2026-08-16) 성장동력·리스크 카드 상자를 항목 수에 맞춰 가변 높이로
#       (4번 항목이 상자 밖으로 넘치던 것) + 항목 상한 4→6
#   v4 (2026-08-16) 수주잔고·재고자산 막대차트(데이터 있는 것만) + 시총·PER·
#       PSR 라이브화(캐시 버킷 일→시)
_RENDER_VER = "v4"


def _eok(v, currency: str = "KRW") -> str:
    """금액 표기 — 통화 인지(조/억 · 兆/億 · T/B/M). None 이면 '—'.

    옛 구현은 `/1e8` → 억/조 로 **KRW 를 물리적으로 가정**했다. 멀티마켓
    확장(사용자 2026-08-16)에서 달러 종목이 '억' 으로 나오는 것을 막기 위해
    `quarterly_series.fmt_money`(dashboard._fmt_mcap 단위 규약 재사용)에
    위임한다. 기본값 KRW 라 기존 호출부는 동작 불변."""
    from bot.quarterly_series import fmt_money
    return fmt_money(v, currency)


def _pct(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _chg(now, prev) -> float | None:
    """증감률(%). 분모가 0/None 이거나 부호가 뒤집히면(적자→흑자 등) None
    — 그런 구간의 '%'는 의미가 없어 배지를 비운다(억지 숫자 금지)."""
    if now is None or prev is None or prev == 0:
        return None
    if prev < 0:
        return None
    return (now / prev - 1) * 100


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", (ticker or "").upper())


def _today_kst() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _now_hour_kst() -> str:
    """캐시 버킷 = KST 날짜+시(YYYY-MM-DD_HH). 시세 기반 값의 갱신 주기."""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d_%H")


def cache_path(ticker: str, period_key, reprt_code=None,
               asof: str | None = None) -> Path:
    """캐시 파일 경로 = 캐시 키. 새 분기면 파일명이 달라져 자동 재렌더.

    ⚠️ 파일명에 **KST 날짜+시**를 포함한다 — 이미지에 시가총액·TTM PER·PSR
    같은 **시세 기반 값**이 구워지는데, 분기 키만 쓰면 다음 분기까지(최대
    3개월) 그 값이 얼어붙는다(2026-08-19 code-review). 하루 1회로는 아침에
    한 번 그린 값이 종일 남아 장중에 보면 어긋난다(사용자 2026-08-16
    "스냅샷이 되면 안돼") → 시 단위 버킷으로 바꿨다. 재렌더는 matplotlib
    1회(₩0)이고 LLM 은 rcept_no 캐시라 재과금 없다.

    `period_key` 는 분기 식별자다. KR 은 (연도, 보고서코드) 2인자 형태를
    유지하고(하위호환), 비-KR 은 분기 종료일 문자열 하나를 준다 —
    reprt_code 가 DART 전용 개념이라 멀티마켓에선 쓸 수 없다."""
    key = f"{period_key}{reprt_code}" if reprt_code is not None else str(period_key)
    return (_IMG_DIR /
            f"{_safe_name(ticker)}_{key}_{asof or _now_hour_kst()}"
            f"_{_RENDER_VER}.png")


def _font_ok() -> bool:
    from bot.daily_byte_infographic import _font_ready, _setup_font
    return bool(_font_ready() and _setup_font())


def render_infographic(payload: dict, out_path: str) -> str | None:
    """payload → PNG. 성공 시 out_path, 실패(폰트 부재·오류) 시 None.

    ⚠️ 전 구간을 try 로 감싼다 — 그리기 단계 예외가 새면 호출부(API 핸들러)가
    500 을 내며 무료 DART 표 폴백조차 못 보여준다(2026-08-19 code-review).
    pyplot 전역 상태 + rcParams 를 만지므로 **렌더 락**으로 직렬화한다
    (ThreadingHTTPServer 에서 동시 렌더 시 figure 교차오염 방지).

    payload = {ticker, company, market, market_cap, currency, quarters[],
               ttm{}, per, psr, growth_risk{}}
    quarters = 오래된→최신 순 [{label, financials{}, ratios{}}...]"""
    with _RENDER_LOCK:
        try:
            return _render_locked(payload, out_path)
        except Exception as exc:
            log.warning("quarterly_infographic: render failed: %s", exc)
            try:
                import matplotlib.pyplot as plt
                plt.close("all")      # 예외 시 figure 누수 방지
            except Exception:
                pass
            return None


# 차트 눈금 파라미터 — 함수 안에 박아두면 테스트가 소스 grep 밖에 못 한다.
# ⚠️ steps 에서 **2.5 를 뺐다**: 0.25 간격이 잡히면 dec=1 포맷이 0.2·0.5·0.8
# 로 반올림해 눈금이 불규칙해 보인다(렌더 실측). 1/2/5 계열만 쓴다.
# nbins 은 최대 구간 수라 작으면 한 단계 굵은 간격으로 떨어진다 — 12 로는
# 0~2.5 에서 0.2(12.5구간)가 탈락해 0.5 간격 5칸이 됐다(옛 7보다 성겼다).
# 카드 항목 한 줄에 들어가는 글자 수 — 카드 폭 실측치(가용 812px, 8pt 기준
# 34자 782px · 38자 874px 넘침). LLM 프롬프트는 dart_growth_risk.ITEM_CHARS
# 로 이보다 짧게 요구해 말줄임이 예외가 되게 한다.
_CARD_CHARS = 34

_TICK_STEPS = [1, 2, 5, 10]
# 상한 — 실제 nbins 는 **축의 픽셀 높이**에서 계산한다(_nbins_for).
# 고정 상수는 두 방향으로 틀린다: 크면 낮은 축(% 패널은 142px 뿐)에서
# 9pt 라벨이 겹치고, 작으면 큰 축에서 한 단계 굵은 간격으로 떨어져
# 오히려 성겨진다(둘 다 2026-08-16 렌더 실측). 픽셀 기준이면 레이아웃
# 상수를 바꿔도 자동으로 따라온다.
_AMT_NBINS, _PCT_NBINS = 16, 9
# 라벨당 최소 세로 공간(px). 9pt @180dpi 의 em 은 ≈22.5px 이고 숫자 글리프
# 자체는 그보다 낮아(≈16px) 23px 이면 눈에 보이는 간격이 남는다(렌더 실측).
# ⚠️ 이 값을 조금만 키워도 한 단계 굵은 눈금으로 **떨어진다** — 실측 기준
# 331px 축에서 23 → 14칸(0.2 간격), 25 → 13칸이라 0.2(13.1칸 필요)가
# 탈락해 0.5 간격 6개가 됐다. 바꿀 땐 반드시 렌더로 확인할 것.
_TICK_MIN_PX = 23.0


def _nbins_for(height_px: float, cap: int) -> int:
    """축 픽셀 높이에 들어가는 최대 눈금 수(라벨 겹침 없이). 상한은 cap."""
    return max(3, min(cap, int((height_px or 0) // _TICK_MIN_PX)))


def _label_decimals(scaled_peak: float, dec: int) -> int:
    """막대 값 라벨의 소수 자릿수. **축 눈금(dec)보다 촘촘해야 한다** —
    축 자릿수를 그대로 쓰면 2.15·2.24·2.31 이 전부 '2.1/2.2/2.3' 으로
    뭉개져 라벨을 붙인 목적(분기간 차이 식별)이 사라진다(렌더 실측).

    ⚠️ **시리즈별로** 호출할 것. 두 시리즈의 공통 peak 로 한 번만 정하면,
    스케일이 100배 작은 쪽(매출 2.31B 옆 영업이익 4.8M)이 전 분기 '0.00'
    으로 찍힌다(2026-08-16 독립 리뷰). 작은 값은 **유효숫자 2자리**가
    보이도록 자릿수를 늘린다."""
    import math
    p = abs(scaled_peak or 0)
    if p >= 100:
        d = 0
    elif p >= 10:
        d = 1
    elif p >= 1 or p == 0:
        d = 2
    else:
        # 0.0048 → 4자리("0.0048") = 유효숫자 2개. 6자리에서 끊는다.
        d = min(6, int(math.floor(-math.log10(p))) + 2)
    return max(dec, d)


# 분기실적 탭 추가 막대차트 — (financials 키, 화면 제목) 순서대로 그린다.
# 사용자 2026-08-16: "여기는 영업이익률이나 순이익률은 필요없고 막대그래프만".
# 시장 게이트가 아니라 **데이터 유무 게이트**다(값이 없으면 패널 자체가 생략)
# — 지금은 재고자산만 소스가 있고(DART 재무제표 계정), 다른 시장·항목에
# 소스가 생기면 이 목록에 한 줄 추가하면 켜진다.
# ⚠️ **생산자가 있는 키만 올린다.** 수주잔고는 DART 사업보고서 「수주상황」
# 표에 있지만 아직 파서가 없어(원문 평문의 실제 배열을 확인해야 한다 —
# bot/scripts/detail_gaps_probe.py) 여기 올리면 영원히 안 그려지는 죽은
# 항목이 되고 Help 에도 없는 기능을 광고하게 된다(2026-08-16 독립 리뷰).
_EXTRA_CHARTS = (("재고자산", "재고자산"),)


def _extra_series(qs: list) -> list[tuple[str, str, list]]:
    """[(키, 제목, 값들)] — **값이 하나라도 있는 항목만**. 없으면 빈 목록."""
    out = []
    for key, title in _EXTRA_CHARTS:
        vals = [(q.get("financials") or {}).get(key) for q in qs]
        if any(v is not None for v in vals):
            out.append((key, title, vals))
    return out


def _footnotes(payload: dict, qs: list) -> list[tuple[str, str]]:
    """푸터 각주 (문구, 색). **레이아웃보다 먼저** 호출돼 H_FOOT 높이를
    정한다 — 줄 수 고정이면 각주가 늘 때 아래 출처줄을 덮어쓴다."""
    cur = payload.get("currency") or "KRW"
    label = (qs[-1] if qs else {}).get("label", "")
    # 기준 기간 명시 — 사용자가 "이 영업이익률이 연간이야 분기야?"라고 물은
    # 지점(2026-08-16). 값만 있고 기간 표기가 없어 판별 불가였다.
    # ⚠️ 두 줄로 나눈다: 한 줄이면 100 단위 폭을 넘겨 bbox_inches="tight" 가
    #    PNG 폭을 종목마다 다르게 늘린다(독립 리뷰 실측).
    # ⚠️ "TTM PER = 최근 4분기 합" 은 **틀린 설명**이었다 — per 는 기본적으로
    #    야후 trailingPE(후행 12개월)이고, 4분기 합을 직접 쓰는 건 PSR 과
    #    자체계산 폴백뿐이다(per_self 각주가 그 경우를 따로 밝힌다).
    notes: list[tuple[str, str]] = [
        (f"* 상단 지표 타일 = {label} 단일분기 기준 "
         f"(YoY = 전년 동기 · QoQ = 직전 분기, 이익률은 %p 차이)", _MUTED),
        ("* TTM PER = 후행 12개월 · Forward PER = 예상실적 기준 "
         "· PSR = 시총 ÷ 최근 4분기 매출 합", _MUTED),
    ]
    bad_keys = payload.get("anomaly_keys") or []
    if bad_keys:
        lbls = payload.get("anomaly_labels") or []
        where = f"({', '.join(lbls)}) " if lbls else ""
        notes.append((
            f"! {'·'.join(bad_keys)} 이상치 감지 {where}— DART 계정 불일치 "
            f"가능. 해당 TTM·PSR 산출 제외(추정 보정 없음)", _NEG))
    if payload.get("per_self"):
        notes.append(("* TTM PER = 시가총액 ÷ TTM 순이익 자체계산"
                      "(데이터 소스가 PER 미제공)", _MUTED))
    comp = payload.get("component_accounts") or {}
    if comp:
        # 이상치와 달리 값은 유효하다 — 막지 않고 '총액 아님'만 알린다.
        notes.append((
            "! " + " · ".join(f"{k} = {v}(구성요소 계정)"
                              for k, v in sorted(comp.items()))
            + " — 총액 계정 미공시라 DART 원자료 그대로(합산·추정 없음)",
            _GOLD))
    if payload.get("currency_mismatch"):
        notes.append((f"! 재무({cur})와 시총({payload.get('trade_currency','')}) "
                      "통화가 달라 PER·PSR 산출 제외(환산 없이 나누면 틀린 배수)",
                      _NEG))
    if payload.get("fiscal_note"):
        notes.append((f"* {payload['fiscal_note']}", _MUTED))
    return notes


def _render_locked(payload: dict, out_path: str) -> str | None:
    if not _font_ok():
        log.warning("quarterly_infographic: Nanum 폰트 없음 — skip")
        return None
    qs = payload.get("quarters") or []
    if not qs:
        return None
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as exc:
        log.warning("quarterly_infographic: matplotlib import failed: %s", exc)
        return None

    gr = payload.get("growth_risk") or {}
    has_llm = bool(gr.get("ok"))
    drivers = gr.get("growth_drivers") or []
    risks = gr.get("sustain_risks") or []
    has_cards = has_llm and (drivers or risks)
    headline = (gr.get("headline") or "").strip() if has_llm else ""
    risk_sub = (gr.get("risk_subline") or "").strip() if has_llm else ""

    # 세로 레이아웃(W=100 좌표계). LLM 섹션이 없으면 그 높이만큼 줄인다.
    W = 100.0
    H_HEAD, H_CALL = 16.0, (9.0 if headline else 0.0)
    # H_CHART 26 → 62: 차트를 좌우 2분할에서 **세로 2단**(각 전체폭)으로
    # 바꿨다. 옛 배치는 inset 폭이 37.5 단위(≈652px)뿐이라 항목이 뭉갰다
    # (사용자 2026-08-16). 이제 86 단위(≈1500px) = 2.3배. 이 상수 하나로
    # H·figsize 가 자동으로 따라 커진다(아래 H 계산 + y 누적 방식).
    # H_TILE 17 → 22: YoY/QoQ 를 한 줄 → **두 줄**로 나눠 폰트를 7.5→9.0 으로
    # 키웠다(사용자 2026-08-16 '숫자가 잘 안 보여'). 두 줄이 들어갈 높이 확보.
    # H_CHART 62 → 88: 같은 지시의 차트 판("더 길게"). 0 기준선 막대에서
    # 매출 2.15→2.31B(+7%)는 짧은 축에선 눈으로 구분이 안 된다 — 축을 늘려
    # 같은 델타가 더 많은 픽셀을 차지하게 하고, 눈금을 촘촘히 하고, 막대에
    # 값 라벨을 붙여 '변화가 안 보인다'를 세 겹으로 해결한다.
    H_TILE, H_CHART = 22.0, 88.0
    # 추가 막대차트(수주잔고·재고자산) — 사용자 2026-08-16 "미래의 수익을
    # 가늠해보고 싶어서". **데이터가 있는 것만** 그리고, 없으면 높이 0 이라
    # 레이아웃이 통째로 줄어든다(빈 패널 = 없는 사실을 그린 것).
    # 이익률 선이 없는 순수 막대라 한 단은 위 2단보다 낮게 잡는다.
    _extra = _extra_series(qs)
    _EXTRA_H = 34.0
    H_EXTRA = _EXTRA_H * len(_extra)
    # 카드 상자 높이는 **항목 수에서 도출**한다(H_FOOT 을 각주 줄 수로 잡은
    # 것과 같은 패턴). 옛 코드는 20.0 고정이라 패널이 17.0 뿐이었는데 4번
    # 항목의 칩 하단이 17.95 라 **상자 밖으로 0.95 단위(≈20px) 튀어나갔다**
    # (사용자 2026-08-16 스크린샷). 3개 이하에선 안 보이던 잠복 버그다.
    # 1번 중심 6.6 + 간격 3.4×(n-1) + 칩 반높이 1.15 + 하단 여백 2.2.
    from bot.dart_growth_risk import MAX_ITEMS as _MAX_CARD_ITEMS
    _n_card = min(_MAX_CARD_ITEMS, max(len(drivers), len(risks)))
    _card_h = 3.4 * _n_card + 6.55
    H_CARDS = (_card_h + 3.0) if has_cards else 0.0
    # 각주를 **레이아웃 전에** 만들어 높이를 실제 줄 수로 잡는다. 옛 코드는
    # H_FOOT 을 15.0 으로 고정해 두고 아래에서 각주를 만들었다 — 각주가
    # 3줄을 넘으면 맨 아래 출처·면책 줄을 덮어쓴다(기준기간 각주를 추가하며
    # 실제로 그 한계에 닿았다). 줄 수에 따라 커지게 해 구조적으로 막는다.
    notes = _footnotes(payload, qs)
    H_FOOT = 8.4 + len(notes) * 2.4 + 4.2
    H = (H_HEAD + H_CALL + H_TILE + H_CHART + H_EXTRA + H_CARDS
         + H_FOOT + 6)

    fig_w = 11.6
    fig, ax = plt.subplots(figsize=(fig_w, fig_w * (H / W)), dpi=180)
    fig.patch.set_facecolor(_BG)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.invert_yaxis(); ax.axis("off")

    def panel(x, y, w, h, fc=_PANEL, ec=_LINE, rad=1.8):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rad}",
            facecolor=fc, edgecolor=ec, linewidth=1.0, mutation_aspect=1))

    def txt(x, y, s, size=10, color=_TEXT, weight="normal", ha="left",
            va="center"):
        ax.text(x, y, s, fontsize=size, color=color, weight=weight, ha=ha,
                va=va, transform=ax.transData)

    cur = payload.get("currency") or "KRW"

    def amt(v) -> str:
        return _eok(v, cur)

    last = qs[-1]
    lf, lr = last.get("financials") or {}, last.get("ratios") or {}
    prev_q = qs[-2] if len(qs) > 1 else None
    yoy_q = qs[-5] if len(qs) >= 5 else None   # 4분기 전 = 전년 동기

    # ── 헤더 ────────────────────────────────────────────────────────
    y = 3.0
    panel(2.5, y, 95, H_HEAD - 4, fc="#1d4ed8", ec="#1d4ed8", rad=2.6)
    company = payload.get("company") or payload.get("ticker") or ""
    txt(6, y + 3.0, f"{last.get('label','')} 실적 분석", size=9.5,
        color="#cfe3ff", weight="bold")
    txt(6, y + 7.4, company, size=17, color="white", weight="bold")
    mk = payload.get("market") or ""
    # 연결/별도는 실제 fs_div 를 따라야 한다 — get_quarterly_series 는 CFS 가
    # 없으면 OFS 로 폴백하는데 헤더에 '연결'을 박아두면 푸터 출처 라벨과
    # 모순되고 숫자 성격을 오인하게 된다(2026-08-19 code-review).
    # 연결/별도는 KR(DART) 전용 개념 — fs_div 가 없는 시장엔 표기하지 않는다
    # (없는 구분을 그리면 숫자 성격을 오인시킨다). 대신 통화가 다르면
    # '재무 CNY · 시총 HKD' 처럼 명시한다(HK 본토 자회사에서 흔함).
    _bits = [mk, payload.get("ticker", "")]
    if last.get("fs_div"):
        _bits.append("연결" if last.get("fs_div") == "CFS" else "별도")
    if payload.get("currency_mismatch"):
        _bits.append(f"재무 {cur} · 시총 {payload.get('trade_currency', '')}")
    elif cur and cur != "KRW":
        _bits.append(cur)
    txt(6, y + 11.0, " · ".join(b for b in _bits if b), size=9,
        color="#dbe9ff")
    mcap = payload.get("market_cap")
    if mcap:
        txt(94, y + 4.5, "시가총액", size=8.5, color="#cfe3ff", ha="right")
        # ⚠️ 시가총액은 **거래통화** — 재무통화(amt)로 찍으면 HK 처럼 둘이
        # 다른 종목에서 통화기호가 틀린다(렌더 스모크에서 실측: HKD 시총이
        # ¥로 표기됨). 재무제표 금액만 amt(재무통화)를 쓴다.
        txt(94, y + 8.6, _eok(mcap, payload.get("trade_currency") or cur),
            size=14, color="white", weight="bold",
            ha="right")
    y += H_HEAD

    # ── 헤드라인 콜아웃(LLM) ────────────────────────────────────────
    if headline:
        panel(2.5, y, 95, H_CALL - 2.5, fc=_PANEL2, rad=1.8)
        txt(6, y + 2.6, headline, size=11.5, weight="bold")
        if risk_sub:
            txt(6, y + 5.6, f"확인할 리스크  {risk_sub}", size=9, color=_MUTED)
        y += H_CALL

    # ── 지표 타일 5종 ───────────────────────────────────────────────
    def _fin(q, k):
        return ((q or {}).get("financials") or {}).get(k)

    def _rat(q, k):
        return ((q or {}).get("ratios") or {}).get(k)

    def _subs(now, yoy_v, qoq_v, *, pp: bool = False):
        """YoY·QoQ 를 **두 줄**로. 옛 코드는 한 줄 size 7.5 _MUTED 라
        사용자가 '숫자가 잘 안 보인다'고 지적했다(2026-08-16). 줄을 나눠
        폰트를 키우고 부호색(증가 초록·감소 빨강)을 입힌다.

        pp=True 는 **비율 지표**용 — 이익률의 변화는 '변화율(%)'이 아니라
        **%p 차이**다. 20.8% → 21.0% 를 '+1.0%' 로 쓰면 0.2%p 상승을
        1% 상승으로 오독시킨다."""
        out = []
        for tag, prev in (("YoY", yoy_v), ("QoQ", qoq_v)):
            if now is None or prev is None:
                continue
            if pp:
                d = now - prev
                s = f"{tag} {d:+.1f}%p"
            else:
                d = _chg(now, prev)
                if d is None:
                    continue
                s = f"{tag} {d:+.1f}%"
            out.append((s, _POS if d >= 0 else _NEG))
        return out

    _fwd = payload.get("per_forward")
    tiles = [
        ("매출", amt(lf.get("매출")), _ACCENT,
         _subs(lf.get("매출"), _fin(yoy_q, "매출"), _fin(prev_q, "매출"))),
        ("영업이익", amt(lf.get("영업이익")), _POS,
         _subs(lf.get("영업이익"), _fin(yoy_q, "영업이익"),
               _fin(prev_q, "영업이익"))),
        # ⚠️ **최근 단일분기** 이익률이다(연간 아님 — 헤더의 분기 라벨 기준).
        # 사용자가 "이게 연간이야 분기야?"라고 물은 지점 — 값만 있고 비교가
        # 없어 판별 불가였다. YoY/QoQ %p 를 붙이면 분기 기준이 자명해진다.
        ("영업이익률", _pct(lr.get("영업이익률")), _GOLD,
         _subs(lr.get("영업이익률"), _rat(yoy_q, "영업이익률"),
               _rat(prev_q, "영업이익률"), pp=True)),
        ("당기순이익", amt(lf.get("당기순이익")), _PUR,
         _subs(lf.get("당기순이익"), _fin(yoy_q, "당기순이익"),
               _fin(prev_q, "당기순이익"))),
        # '*' = 자체계산(시총÷TTM순이익). ASCII 라 폰트 결손 위험이 없다
        # (이모지·특수기호는 NanumGothic 에서 두부로 나올 수 있음).
        # 서브라인 = Forward PER(예상실적 기준). 미제공이면 'N/A' 를 **명시**
        # — 빈칸이면 '계산 중'인지 '없는지' 구분이 안 된다(사용자 2026-08-16).
        ("TTM PER" + ("*" if payload.get("per_self") else ""),
         ("—" if payload.get("per") is None
          else f"{payload['per']:,.2f}배"), _ACCENTW,
         [(f"Fwd {_fwd:,.2f}배" if _fwd is not None else "Fwd N/A",
           _ACCENTW if _fwd is not None else _MUTED)]),
    ]
    tw, gap = 18.0, 1.5
    tx = 2.5
    for name, val, col, subs in tiles:
        panel(tx, y, tw, H_TILE - 3, fc=_PANEL, rad=1.6)
        ax.add_patch(Rectangle((tx, y), tw, 0.7, facecolor=col,
                               edgecolor="none"))
        txt(tx + 1.6, y + 3.4, name, size=8.5, color=_MUTED, weight="bold")
        txt(tx + 1.6, y + 7.8, val, size=12.5, color=col, weight="bold")
        sy = y + 12.4
        for s, scol in subs[:2]:
            txt(tx + 1.6, sy, s, size=9.0, color=scol, weight="bold")
            sy += 3.6
        tx += tw + gap
    y += H_TILE

    # ── 콤보차트 2개(막대 + 선, 실제 matplotlib 축) ─────────────────
    labels = [q.get("label", "") for q in qs]
    rev = [(q.get("financials") or {}).get("매출") for q in qs]
    op = [(q.get("financials") or {}).get("영업이익") for q in qs]
    ni = [(q.get("financials") or {}).get("당기순이익") for q in qs]
    opm = [(q.get("ratios") or {}).get("영업이익률") for q in qs]
    nim = [(q.get("ratios") or {}).get("순이익률") for q in qs]

    bad_labels = set(payload.get("anomaly_labels") or [])

    def combo(rect, bars, bar_labels, bar_colors, line, line_color,
              line_title, title_base):
        """rect=(x0,y0,w,h) 데이터좌표 패널 → 금액 막대(위) + 이익률 %(아래)를
        **분리된 두 축**으로 그린다.

        옛 구현은 `twinx()` 이중 Y축이었다. 두 스케일의 정렬 기준이 임의라
        존재하지 않는 상관을 만들어 내고, 실제로 '25.4Q 매출 -21조인데
        이익률 선은 0% 근처'처럼 읽혀 오독을 유발했다(사용자 2026-08-16).
        축을 나누면 그 오독이 **구조적으로 불가능**해진다. 두 축의 xlim 을
        같게 잡아 같은 분기가 세로로 정렬되고, x 라벨은 아래 축에만 둔다."""
        from matplotlib.ticker import FuncFormatter, MaxNLocator
        x0, y0, w, h = rect
        panel(x0, y0, w, h, fc=_PANEL, rad=1.6)
        # 금액 단위 자동 선택 — 억 단위로 두면 대형주가 100만(억)대라
        # matplotlib 이 y축에 '1e6' 오프셋을 붙여 제목을 가리고 값도 안 읽힌다.
        # 최대값이 1조 이상이면 조 단위로 스케일(라벨도 함께 바꾼다).
        from bot.quarterly_series import chart_unit
        peak = max((abs(v) for b in bars for v in b if v is not None),
                   default=0)
        div, unit, dec = chart_unit(cur, peak)
        txt(x0 + 2.5, y0 + 2.6, f"{title_base} ({unit})", size=10.5,
            weight="bold")
        # data 좌표 → figure fraction. ⚠️ x/W 로 직접 나누면 안 된다 —
        # 메인 ax 는 figure 전체가 아니라 기본 여백 안쪽만 차지하므로
        # 축이 패널 밖으로 삐져나온다. 실제 transData→transFigure 변환 사용.
        inv = fig.transFigure.inverted()

        def _d2f(dx, dy):
            return inv.transform(ax.transData.transform((dx, dy)))

        # 여백: 좌 8(y 눈금 라벨 — 폰트를 키웠으므로 옛 6 에서 확대)·우 3·
        # 위 7(제목+범례)·아래 4(x 라벨). y 축이 invert 돼 '아래'가 큰 y 값.
        LM, RM, TP, BP, GAP = 8.0, 3.0, 7.0, 4.0, 1.6
        # 이익률이 전량 결측이면(매출 미제공·0 등) % 패널을 만들지 않는다.
        # 빈 축을 그리면 '0% / 0% / -0%' 눈금이 붙어 **마진 0%** 로 읽힌다
        # (없는 사실을 그린 셈 — 2026-08-16 독립 리뷰, "환각 0" 원칙).
        has_pct = any(v is not None for v in (line or []))
        plot_h = h - TP - BP - (GAP if has_pct else 0.0)
        bar_h = plot_h * (0.70 if has_pct else 1.0)
        pct_h = plot_h - bar_h

        def _axes(top, bot):
            fx0, fy0 = _d2f(x0 + LM, y0 + bot)
            fx1, fy1 = _d2f(x0 + w - RM, y0 + top)
            a = fig.add_axes([fx0, fy0, fx1 - fx0, fy1 - fy0])
            a.set_facecolor(_PANEL)
            for sp in a.spines.values():
                sp.set_color(_LINE)
            a.grid(axis="y", color=_LINE, linewidth=0.5, alpha=0.6, zorder=0)
            a.set_axisbelow(True)
            return a

        bax = _axes(TP, TP + bar_h)
        pax = (_axes(TP + bar_h + GAP, TP + bar_h + GAP + pct_h)
               if has_pct else None)

        n = len(labels)
        idx = list(range(n))
        # 결측 분기를 0 으로 바꾸면 '실적 0' 막대가 그려져 없는 사실을
        # 그린 셈이 된다("환각 0" 푸터와 모순, 2026-08-19 code-review).
        # NaN 은 matplotlib 이 막대를 아예 안 그린다 → 결측이 결측으로 보인다.
        nan = float("nan")
        vals = [[(nan if v is None else v / div) for v in b] for b in bars]
        width = 0.34 if len(bars) > 1 else 0.46
        offs = ([-width / 2, width / 2] if len(bars) > 1 else [0])
        # 값 라벨 자릿수는 **축 눈금보다 촘촘해야 한다** — 축 자릿수(dec)를
        # 그대로 쓰면 2.15·2.24·2.31 이 전부 '2.1/2.2/2.3' 으로 뭉개져
        # 라벨을 붙인 목적(분기간 차이 식별)이 사라진다(렌더 실측).
        for k, series in enumerate(vals):
            # 자릿수는 **이 시리즈의 peak** 기준 — 공통 peak 로 정하면
            # 작은 쪽 시리즈가 전 분기 '0.00' 이 된다(독립 리뷰 실측).
            _spk = max((abs(v) for v in series if v == v), default=0)
            ldec = _label_decimals(_spk, dec)
            _c = bax.bar([i + offs[k] for i in idx], series, width=width,
                         color=bar_colors[k], label=bar_labels[k], zorder=2)
            # 막대 값 라벨 — 0 기준선 막대는 분기간 델타가 축 높이의 몇 %에
            # 불과해(매출 +7% = 눈으로 거의 동일) '변화가 안 보인다'는 지적의
            # 근본 원인이다(사용자 2026-08-16). 숫자를 직접 얹으면 축 스케일과
            # 무관하게 읽힌다. NaN(결측)은 빈 라벨 — 없는 값을 0 으로 쓰지 않는다.
            bax.bar_label(
                _c, labels=[("" if v != v else f"{v:,.{ldec}f}") for v in series],
                fontsize=7.5, color=bar_colors[k], padding=1.5, fontweight="bold")
        # 값 라벨이 축 경계에 붙으면 잘린다 — 헤드룸 8% 확보. **아래쪽도**
        # 넓힌다: 적자 분기의 라벨은 막대 아래에 그려져 축 밖으로 나가
        # x 라벨·아래 % 패널과 겹친다(2026-08-16 독립 리뷰 실측).
        _b0, _b1 = bax.get_ylim()
        _pad = (_b1 - _b0) * 0.08
        bax.set_ylim(_b0 - (_pad if _b0 < 0 else 0), _b1 + _pad)
        # 지수 오프셋('1e6') 금지 — 위 단위 스케일링으로 자릿수를 이미 줄였고,
        # 오프셋 텍스트가 축 위에 그려져 제목을 침범한다.
        bax.ticklabel_format(axis="y", style="plain", useOffset=False)
        # 눈금 세밀화 — 옛 코드엔 locator 설정이 아예 없어 축 높이에 맞춰
        # 3~4개로 성기게 잡혔다(사용자 2026-08-16 '숫자간격 더 세밀하게').
        # 눈금 세밀화(사용자 2026-08-16 '왼쪽 축의 숫자를 더 세밀하게').
        # nbins 은 축의 실제 픽셀 높이에서 — 근거는 _nbins_for 참조.
        bax.yaxis.set_major_locator(MaxNLocator(
            nbins=_nbins_for(bax.get_window_extent().height, _AMT_NBINS),
            steps=_TICK_STEPS))
        bax.yaxis.set_major_formatter(
            FuncFormatter(lambda v, _p: f"{v:,.{dec}f}"))
        bax.tick_params(axis="y", labelsize=9, colors=_MUTED, length=2)
        bax.tick_params(axis="x", length=0, labelbottom=False)

        if pax is not None:
            pax.plot(idx, [nan if v is None else v for v in line],
                     color=line_color, marker="o", markersize=4.2,
                     linewidth=2.0, zorder=3)
            # % 패널은 금액 패널의 ~30% 높이(실측 142px)뿐이라 같은 상수를
            # 쓰면 라벨이 겹친다 — 여기서도 픽셀 기준으로 뽑는다.
            pax.yaxis.set_major_locator(MaxNLocator(
                nbins=_nbins_for(pax.get_window_extent().height, _PCT_NBINS),
                steps=_TICK_STEPS))
            # 정수 %로 고정하면 마진 폭이 좁을 때 '11% 10% 10% 10%' 처럼 눈금이
            # 중복 표기된다(2026-08-16 독립 리뷰, 렌더 실측). 축 범위에 맞춰
            # 소수 자리를 자동 확보한다.
            _lo, _hi = pax.get_ylim()
            _pdec = 0 if (_hi - _lo) >= 4 else (1 if (_hi - _lo) >= 0.4 else 2)
            pax.yaxis.set_major_formatter(
                FuncFormatter(lambda v, _p: f"{v:,.{_pdec}f}%"))
            pax.tick_params(axis="y", labelsize=9, colors=_MUTED, length=2)
            pax.tick_params(axis="x", length=0)
            # 시리즈가 1개인 축은 범례 대신 제목이 이름을 담당한다.
            # ⚠️ set_ylabel 은 쓰지 않는다 — 축이 낮아 세로쓰기 한글이 글자끼리
            # 겹쳐 뭉갠다(렌더 스모크에서 실측). 축 위 좌측 가로 제목으로.
            pax.set_title(line_title, fontsize=8.5, color=line_color,
                          loc="left", pad=2.0)

        # x 라벨은 맨 아래 축에만(두 축이 세로로 정렬돼 같은 분기가 일치).
        x_ax = pax if pax is not None else bax
        for a in (bax, pax):
            if a is None:
                continue
            a.set_xlim(-0.6, n - 0.4)   # 두 축 x 정렬(같은 분기가 세로로 일치)
            a.set_xticks(idx)
        if x_ax is bax:
            bax.tick_params(axis="x", length=0, labelbottom=True)
        x_ax.set_xticklabels(labels, fontsize=9, color=_MUTED)
        # 이상치가 붙은 분기는 x 라벨을 경고색으로 — 어느 분기가 문제인지
        # 그림 안에서 바로 보이게(각주와 짝).
        for lab, t_ in zip(labels, x_ax.get_xticklabels()):
            if lab in bad_labels:
                t_.set_color(_NEG)
                t_.set_weight("bold")
        # 범례는 축 **바깥 위**로 — 옛 loc="upper left" 는 가장 높은 첫 분기
        # 막대와 정면 충돌했다(frameon=False 라 가려지는 게 그대로 보였음).
        # 시리즈가 1개면 범례를 아예 두지 않는다(패널 제목이 이름을 담당).
        if len(bars) > 1:
            lg = bax.legend(loc="lower left", bbox_to_anchor=(0, 1.02),
                            fontsize=8.5, frameon=False, labelcolor=_MUTED,
                            ncol=len(bars), handlelength=1.2,
                            columnspacing=1.4)
            if lg:
                lg.set_zorder(4)

    _ch = (H_CHART - 4) / 2.0        # 두 단 각각의 패널 높이
    combo((2.5, y, 95, _ch), [rev, op], ["매출", "영업이익"],
          [_ACCENT, _POS], opm, _GOLD, "영업이익률",
          "매출 · 영업이익")
    combo((2.5, y + _ch + 2.0, 95, _ch), [ni], ["당기순이익"], [_PUR],
          nim, _NEG, "순이익률", "당기순이익")
    y += H_CHART

    # ── 수주잔고 · 재고자산 (막대만, 있는 항목만) ───────────────────
    # line 을 전부 None 으로 넘기면 combo 의 has_pct 가 False → % 패널을
    # 만들지 않고 막대가 패널 전체를 쓴다(사용자가 요청한 형태).
    _EX_COLOR = {"수주잔고": _ACCENTW, "재고자산": _GOLD}
    for _i, (_k, _title, _vals) in enumerate(_extra):
        combo((2.5, y + _i * _EXTRA_H, 95, _EXTRA_H - 2.0), [_vals], [_title],
              [_EX_COLOR.get(_k, _ACCENT)], [None] * len(labels), _MUTED,
              "", _title)
    y += H_EXTRA

    # ── 성장동력 / 리스크 카드(LLM, 없으면 섹션 자체 생략) ──────────
    if has_cards:
        def card_col(x0, w, title, items, color):
            panel(x0, y, w, H_CARDS - 3, fc=_PANEL, rad=1.6)
            ax.add_patch(Rectangle((x0, y), w, 0.7, facecolor=color,
                                   edgecolor="none"))
            txt(x0 + 2, y + 3.2, title, size=9.5, weight="bold")
            iy = y + 6.6
            # 상한은 dart_growth_risk.MAX_ITEMS 하나가 정본 — 여기 숫자를
            # 따로 박으면 생산 단계 cap 과 어긋난다(옛 코드가 그랬다).
            for i, s in enumerate(items[:_MAX_CARD_ITEMS], 1):
                ax.add_patch(FancyBboxPatch(
                    (x0 + 2, iy - 1.15), 2.4, 2.3,
                    boxstyle="round,pad=0,rounding_size=1.15",
                    facecolor=color, edgecolor="none", mutation_aspect=1))
                txt(x0 + 3.2, iy, str(i), size=7.5, color="#0b1020",
                    weight="bold", ha="center")
                # 34 = 카드 폭 실측 한계(812px 가용, 8pt 34자 782px · 38자
                # 874px 넘침). 프롬프트는 ITEM_CHARS(32)로 더 짧게 요구해
                # 말줄임을 예외로 만든다 — 두 숫자가 어긋나면 문장이 매번
                # 중간에 끊긴다(2026-08-16 독립 리뷰).
                body = s if len(s) <= _CARD_CHARS else s[:_CARD_CHARS - 1] + "…"
                txt(x0 + 5.6, iy, body, size=8)
                iy += 3.4
        # ⚠️ `cw` 는 **정의된 적이 없다** — 최초 구현(c4397c4)부터 여기서
        # NameError 가 났고, render_infographic 의 포괄 except 가 그걸 삼켜
        # LLM 카드가 붙는 KR 경로는 **PNG 가 아예 안 나오고** 표로 폴백해
        # 왔다(과금까지 하는 경로인데 그림이 없었다, 2026-08-16 독립 리뷰).
        # 좌 2.5 + 우 51.0 배치에 맞춰 폭을 정의한다(2.5+46.5=49, 51+46.5=97.5).
        card_w = 46.5
        card_col(2.5, card_w, "확인된 성장동력", drivers, _POS)
        card_col(51.0, card_w, "지속조건 · 무효화 리스크", risks, _NEG)
        y += H_CARDS

    # ── 푸터(TTM + 출처 + 면책) ─────────────────────────────────────
    ttm = payload.get("ttm") or {}
    panel(2.5, y, 95, 6.4, fc=_PANEL2, rad=1.6)
    foot_items = [
        ("TTM 매출", amt(ttm.get("매출"))),
        ("TTM 영업이익", amt(ttm.get("영업이익"))),
        ("TTM 순이익", amt(ttm.get("당기순이익"))),
        ("TTM PER" + ("*" if payload.get("per_self") else ""),
         "—" if payload.get("per") is None
         else f"{payload['per']:,.2f}배"),
        # 타일 서브라인과 같은 값 — 두 표면이 어긋나면 그게 곧 버그다.
        ("Forward PER", "N/A" if payload.get("per_forward") is None
         else f"{payload['per_forward']:,.2f}배"),
        ("PSR", "—" if payload.get("psr") is None
         else f"{payload['psr']:,.2f}배"),
    ]
    fx = 6.0
    _fgap = 88.0 / max(len(foot_items), 1)   # 항목이 늘어도 패널 안에 들어오게
    for name, val in foot_items:
        txt(fx, y + 2.2, name, size=8, color=_MUTED)
        txt(fx, y + 4.6, val, size=10.5, weight="bold")
        fx += _fgap
    # 이상치·자체계산 각주 — 값이 '—' 로 비었을 때 "왜 비었나"를 화면에서
    # 알 수 있어야 한다(빈칸만 두면 데이터 없음과 구분 불가). 이모지 대신
    # ASCII 마커 + 색으로 표기(NanumGothic 글리프 결손 회피).
    _ny = y + 7.6
    for _note, _ncol in notes:
        txt(6.0, _ny, _note, size=7.5, color=_ncol)
        _ny += 2.4
    # 기준시각 표기 의무(CLAUDE.md 실수기록 10-b) — 시총/PER/PSR 은 시세
    # 기반이라 '언제 기준'인지 없으면 오래된 값을 현재값으로 오인한다.
    src = payload.get("source_label") or "DART 정기보고서(K-IFRS 연결)"
    # asof 는 캐시 버킷(YYYY-MM-DD_HH) 이라 그대로 찍으면 '2026-08-16_14'
    # 라는 날것이 화면에 나간다 — 사람이 읽는 형태로 바꾼다(독립 리뷰).
    asof = payload.get("asof") or _now_hour_kst()
    _asof_s = asof.replace("_", " ") + "시" if "_" in asof else asof
    txt(2.5, H - 2.6, f"수치: {src} · 시총·PER {_asof_s} 기준(KST) · 환각 0",
        size=8, color=_MUTED)
    txt(97.5, H - 2.6, "투자 참고용이며 매수·매도를 권유하지 않습니다",
        size=8, color=_MUTED, ha="right")

    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fig.savefig(out_path, facecolor=_BG, bbox_inches="tight",
                    pad_inches=0.15)
        plt.close(fig)
        return out_path
    except Exception as exc:
        log.warning("quarterly_infographic: savefig failed: %s", exc)
        try:
            plt.close(fig)
        except Exception:
            pass
        return None


# 이상치 플래그 → 그 플래그가 오염시키는 항목. 두 플래그 모두 '매출'
# canonical 계정 판정에서 나오므로 매출만 막는다(영업이익·당기순이익은
# 별도 계정이라 무관 — 실제로 메리츠금융지주도 순이익만 정상이었다).
_ANOMALY_AFFECTS: dict[str, tuple[str, ...]] = {
    "_anomaly_revenue_negative": ("매출",),
    "_anomaly_account_mismatch": ("매출",),
}


def anomalous_keys(qs: list) -> set:
    """이 분기 구간에서 신뢰할 수 없는 항목 집합."""
    out: set = set()
    for q in qs or []:
        fin = q.get("financials") or {}
        for flag, keys in _ANOMALY_AFFECTS.items():
            if fin.get(flag):
                out.update(keys)
    return out


def anomalous_labels(qs: list) -> list:
    """이상치가 붙은 분기 라벨(화면에 '어느 분기가 문제인지' 표시용)."""
    return [q.get("label", "") for q in qs or []
            if any((q.get("financials") or {}).get(f) for f in _ANOMALY_AFFECTS)]


def _ttm(qs: list) -> dict:
    """최근 4분기 합 = TTM. 4개 미만이면 빈 dict(부분합으로 TTM 이라 부르면
    틀린 값 — 억지로 만들지 않는다).

    ⚠️ 이상치 플래그가 붙은 분기가 창 안에 있으면 **그 항목은 만들지
    않는다**. 옛 코드는 플래그를 무시하고 그냥 합산해서, DART 계정 승자
    불일치로 한 분기 매출이 -21조가 된 종목의 TTM 매출을 -10.30조로 찍고
    거기서 PSR -1.87배까지 파생시켰다(사용자 2026-08-16 메리츠금융지주).
    오염된 합계를 'TTM' 이라 부르지 않는다 — 빈칸이 틀린 숫자보다 낫다."""
    if len(qs) < 4:
        return {}
    window = qs[-4:]
    bad = anomalous_keys(window)
    out: dict = {}
    for k in ("매출", "영업이익", "당기순이익"):
        if k in bad:
            continue
        vals = [(q.get("financials") or {}).get(k) for q in window]
        if all(v is not None for v in vals):
            out[k] = sum(vals)
    return out


# PER 범위 가드 — dart_feed._compute_per 와 동일 기준(음수·비현실 값 차단).
_PER_MIN, _PER_MAX = 0.0, 500.0


def self_per(market_cap, ttm_net_income) -> float | None:
    """시총 ÷ TTM 순이익. 야후가 trailingPE 를 안 주는 종목(보험·금융지주
    에서 흔하다)의 폴백 — 두 값 모두 payload 안에 이미 있어 추가 호출 0.
    전 시장 공통(통화가 같을 때만 호출할 것 — 호출부 책임)."""
    if not market_cap or not ttm_net_income or ttm_net_income <= 0:
        return None
    per = market_cap / ttm_net_income
    return per if _PER_MIN < per < _PER_MAX else None


# 지원 시장 — KR 은 DART(단일분기 원천), 그 외는 yfinance 분기 손익.
SUPPORTED_MARKETS = ("KR", "US", "JP", "TW", "CN_A", "HK")

# 시장 → 거래소 라벨 폴백(스냅샷 exchange 가 비었을 때).
_MARKET_LABEL = {"US": "US", "JP": "TSE", "TW": "TWSE", "CN_A": "A주",
                 "HK": "HKEX"}


def _live_quote(ticker: str, market: str, shares: float | None = None) -> dict:
    """장중 라이브 시세 {price, mcap}. 실패하면 {}(호출부가 스냅샷 폴백).

    `dashboard.build_live_quote` 의 LIGHT 경로와 **같은 소스**를 쓴다 —
    KR=네이버 국내, US/JP/HK/CN=네이버 해외, TW=TWSE. 키 불필요·₩0·
    VM 차단 없음. 두 화면이 다른 소스를 쓰면 같은 종목의 시총이 탭마다
    달라진다(사용자 2026-08-16 "스냅샷이 되면 안돼").

    ⚠️ **해외 시총은 단위 sanity 통과 시에만** 채택한다. 네이버 해외
    `marketValueFullRaw` 는 단위가 불확실해 원/달러가 섞여 나오는 사례가
    있고(`market_favorites.py` 가 같은 이유로 같은 게이트를 건다), 그대로
    쓰면 PSR·시총이 통째로 자릿수가 틀린다(2026-08-16 독립 리뷰).
    주식수를 모르면 검증할 수 없으므로 해외 시총은 채택하지 않는다 —
    스냅샷 시총으로 폴백하는 편이 안전하다. KR 국내는 원 단위가 확정이다."""
    try:
        if market == "KR":
            from bot.naver_quote import fetch_kr_quote
            q = fetch_kr_quote(ticker)
        elif market == "TW":
            from bot.tw_quote import fetch_tw_quote
            q = fetch_tw_quote(ticker)
        elif market in ("US", "JP", "HK", "CN_A"):
            from bot.world_quote import fetch_world_quote
            q = fetch_world_quote(ticker)
        else:
            return {}
    except Exception as exc:
        log.debug("quarterly_infographic: live quote %s: %s", ticker, exc)
        return {}
    out: dict = {}
    for k in ("price", "mcap"):
        v = (q or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[k] = float(v)
    if "mcap" in out and market != "KR":
        implied = (out.get("price") or 0) * (shares or 0)
        if not implied or not (0.5 <= out["mcap"] / implied <= 2.0):
            log.debug("quarterly_infographic: %s 해외 시총 단위 sanity 실패 "
                      "(mcap=%s implied=%s) — 스냅샷 사용", ticker,
                      out["mcap"], implied or None)
            out.pop("mcap")
    return out


def _dart_name(dart, ticker: str) -> str | None:
    """DART corp_code 맵의 회사명(디스크 캐시 · 네트워크 0). 실패 시 None."""
    if not dart:
        return None
    try:
        return dart.stock_code_to_name((ticker or "").upper().split(".")[0])
    except Exception as exc:
        log.debug("quarterly_infographic: corp name %s: %s", ticker, exc)
        return None


def build_payload(ticker: str, snap: dict | None = None, *,
                  run_llm: bool = False) -> dict | None:
    """분기 시계열 + 스냅샷(시총/PER) + (선택)LLM 카드 → 렌더 payload.

    KR = DART 정기보고서(단일분기 원천), 그 외 = yfinance 분기 손익계산서
    (스냅샷에 이미 8분기치가 전 시장 공통으로 수집돼 있어 추가 호출 0 —
    사용자 2026-08-16 '다른 나라도'). 분기 데이터가 없으면 None.
    """
    from bot.market import detect_market
    from bot.quarterly_series import fiscal_note, series_from_yfinance
    t = (ticker or "").upper()
    mkt = detect_market(t)
    if mkt not in SUPPORTED_MARKETS:
        return None
    snap = snap or {}
    is_kr = mkt == "KR"
    dart = None
    if is_kr:
        try:
            from bot.dart_client import get_dart
            from bot.dart_quarterly import get_quarterly_series
            dart = get_dart()
            qs = get_quarterly_series(dart, t, n=5)
        except Exception as exc:
            log.warning("quarterly_infographic: series %s: %s", t, exc)
            return None
    else:
        # KR 은 야후로 폴백하지 않는다 — DART 가 단일분기를 직접 주므로
        # 소스를 섞으면 같은 화면에서 숫자 성격이 달라진다.
        qs = series_from_yfinance(snap, n=5)
    if not qs:
        return None
    latest = qs[-1]
    ttm = _ttm(qs)
    # 스냅샷 최상위는 snake `market_cap` 하나뿐 — 옛 camel 시도는 항상
    # 죽은 코드였다(stock_snapshot.py 는 marketCap 을 그 이름으로 저장하지 않음).
    # 라이브 시총 우선 — 스냅샷 시총은 (a) 마지막 수집 시각에 얼어붙고
    # (b) KR 은 캐시가 cold 면 아예 없어 '—' 로 뜬다(사용자 2026-08-16
    # "일주일 후에 다시 보면 그때 시점의 시총이 되어야 돼").
    live = _live_quote(t, mkt, snap.get("shares_outstanding"))
    mcap = live.get("mcap") or snap.get("market_cap")
    # 통화 — 재무제표는 financialCurrency 기준, 시총·주가는 currency 기준.
    trade_cur = (snap.get("currency") or "").upper()
    fin_cur = (snap.get("financial_currency") or "").upper() or trade_cur
    if is_kr:
        fin_cur = fin_cur or "KRW"
        trade_cur = trade_cur or "KRW"
    # 통화 불일치 HARD GUARD — HK 본토 자회사는 거래 HKD·재무 CNY 가 흔하다
    # (CLAUDE_REFERENCE: yfinance financialCurrency mismatch HK > JP 빈도).
    # 시총 ÷ 매출·순이익은 서로 다른 통화라 그냥 나누면 틀린 배수가 된다.
    cur_mismatch = bool(trade_cur and fin_cur and trade_cur != fin_cur)

    # 소스 제공 PER 은 **후행(TTM) · 선행(Forward) 둘 다 같은 규칙**으로
    # 정제한다. 옛 코드는 trailingPE 를 그대로 썼는데, 그러면 (a) 문자열이
    # 오면 f-string 포맷에서 터져 렌더 전체가 죽고 (b) 자체계산(self_per)엔
    # 걸리는 _PER_MIN/_PER_MAX 범위 가드가 소스값엔 안 걸려 같은 타일에
    # 'TTM 1,234.50배 / Fwd N/A' 같은 비대칭이 난다(2026-08-16 독립 리뷰).
    # 둘 다 거래통화 안에서 계산돼 오므로 통화 불일치 가드는 불필요하다
    # (자체계산 self_per 과 다른 점 — 그쪽은 시총÷재무통화 순이익).
    def _clean_per(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if _PER_MIN < f < _PER_MAX else None

    # PER 을 라이브화하되 **다른 탭과 같은 분모**를 쓴다.
    #   1순위: 라이브 주가 ÷ 야후 EPS — `dashboard.build_live_quote` 가
    #          PER/PBR 을 재산출하는 것과 **똑같은 공식**이라 분기실적 탭과
    #          종합·밸류에이션 탭의 PER 이 어긋나지 않는다.
    #   2순위: 스냅샷 trailingPE(수집 시점 주가로 굳은 값)
    #   3순위: 시총 ÷ DART TTM 순이익 자체계산(`*` 각주로 출처 표기)
    # 옛 시도는 곧장 3순위를 우선했는데, 그러면 같은 종목의 PER 이 탭마다
    # 달라 보인다(연결 총이익 vs 지배주주 EPS 분모, 2026-08-16 독립 리뷰).
    # ⚠️ EPS 는 재무통화 기준이라 거래통화 주가와 나누려면 통화가 같아야
    # 한다 — cur_mismatch 면 1순위·3순위 모두 건너뛴다.
    def _live_per(eps_key):
        eps = snap.get(eps_key)
        if (live.get("price") and not cur_mismatch
                and isinstance(eps, (int, float)) and not isinstance(eps, bool)
                and eps > 0):
            return _clean_per(live["price"] / eps)
        return None

    per_self = False
    per = _live_per("trailingEps") or _clean_per(snap.get("trailingPE"))
    if per is None and not cur_mismatch:
        # 야후가 trailingPE·EPS 를 안 주는 종목(보험·금융지주에서 흔함) 폴백 —
        # 옛 코드는 단일 소스라 그냥 '—' 였다(사용자 2026-08-16).
        per = self_per(mcap, ttm.get("당기순이익"))
        per_self = per is not None
    # Forward PER — 라이브 주가 ÷ 야후 forwardEps(컨센서스라 장중 불변).
    per_fwd = _live_per("forwardEps") or _clean_per(snap.get("forwardPE"))
    psr = None
    _ttm_rev = ttm.get("매출")
    # 음수 분모 가드 — 옛 코드는 truthy 검사만 해서 매출이 음수여도 그대로
    # 나눠 PSR -1.87배를 찍었다.
    if mcap and _ttm_rev and _ttm_rev > 0 and not cur_mismatch:
        psr = mcap / _ttm_rev
    _window = qs[-4:] if len(qs) >= 4 else qs
    _fs_div = latest.get("fs_div")
    if is_kr:
        src_label = (f"DART 정기보고서"
                     f"(K-IFRS {'연결' if _fs_div == 'CFS' else '별도'})")
        mkt_label = "KOSPI" if t.endswith(".KS") else "KOSDAQ"
    else:
        src_label = "yfinance 분기 손익계산서"
        mkt_label = snap.get("exchange") or _MARKET_LABEL.get(mkt, mkt)
    payload = {
        "ticker": t,
        # 스냅샷은 long_name(스네이크) 키를 쓴다 — longName/shortName 은
        # 절대 안 잡혀 폴백이 죽어 있었다(2026-08-19 code-review).
        # KR 은 스냅샷이 cold 여도 DART corp_code 맵(디스크 캐시)에서 회사명을
        # 얻는다 — 그게 없으면 헤더가 '039030.KQ' 로 뜬다(독립 리뷰).
        "company": ((snap.get("kr", {}) or {}).get("corp_name")
                    or snap.get("long_name") or _dart_name(dart, t) or t),
        "market": mkt_label,
        "market_cap": mcap,
        "quarters": qs,
        "ttm": ttm,
        "per": per,
        "per_forward": per_fwd,
        # 야후 제공값과 자체계산을 구분해 표기(출처 표기 의무).
        "per_self": per_self,
        "psr": psr,
        "currency": fin_cur or "KRW",
        "trade_currency": trade_cur or fin_cur or "KRW",
        "currency_mismatch": cur_mismatch,
        "fiscal_note": fiscal_note(snap.get("fiscal_year_end")) if not is_kr else "",
        # LLM 성장동력/리스크는 DART 원문 전용 — 비-KR 은 버튼 자체를 숨긴다
        # (누를 수 없는 버튼을 보여주지 않는다).
        "llm_supported": is_kr,
        # 렌더러가 '어느 항목이·어느 분기가' 신뢰 불가인지 표시하도록 전달.
        # 플래그를 만들어 두고 렌더러가 안 읽던 게 -10.30조가 그대로
        # 화면에 나간 이유였다(dashboard 표에만 배지가 있었음).
        # 두 값의 창을 맞춘다 — labels 만 5분기로 잡으면 TTM 창 밖 분기가
        # 빨간 x라벨로만 뜨고 각주가 안 붙는다(2026-08-16 독립 리뷰).
        "anomaly_keys": sorted(anomalous_keys(_window)),
        "anomaly_labels": anomalous_labels(_window),
        # 구성요소 계정에서 온 값 — 이상치와 달리 **값은 유효**하므로 TTM 을
        # 막지 않고 표기만 한다(총매출이 아니라는 사실만 알림).
        "component_accounts": {
            k: v for q in _window
            for k, v in ((q.get("financials") or {}).get(
                "_component_accounts") or {}).items()},
        "latest_year": latest.get("year"),
        "latest_reprt_code": latest.get("reprt_code"),
        # 캐시 키 — KR 은 (연도+보고서코드), 그 외는 분기 종료일.
        "period_key": (f"{latest.get('year')}{latest.get('reprt_code')}"
                       if is_kr else
                       str(latest.get("period", "")).replace("-", "")),
        "source_label": src_label,
        # 시세 기반 값(시총·PER·PSR)의 기준시각 = 렌더 시각(KST, 시 단위).
        # 캐시 키와 **같은 값**이라 화면 스탬프와 재렌더 주기가 어긋나지 않는다.
        "asof": _now_hour_kst(),
    }
    if not is_kr:
        payload["growth_risk"] = {"ok": False, "error": "not_supported"}
        return payload
    try:
        from bot.dart_growth_risk import build_growth_risk
        ctx = {"분기": latest.get("label"),
               "매출": (latest.get("financials") or {}).get("매출"),
               "영업이익": (latest.get("financials") or {}).get("영업이익"),
               "당기순이익": (latest.get("financials") or {}).get("당기순이익")}
        payload["growth_risk"] = build_growth_risk(
            dart, t, latest.get("year"), latest.get("reprt_code"), ctx,
            company=payload["company"], run_llm=run_llm)
    except Exception as exc:
        log.warning("quarterly_infographic: growth_risk %s: %s", t, exc)
        payload["growth_risk"] = {"ok": False, "error": "요약 실패"}
    return payload


def table_html(payload: dict) -> str:
    """PNG 폴백 — 서버에 한글 폰트가 없어 렌더가 None 일 때 같은 숫자를 표로.

    ⚠️ 컬럼은 **오래된→최신**(최신이 오른쪽) — 이 표가 대신하는 PNG 차트가
    그 방향이고, 같은 페이지의 밸류에이션 탭 분기표도 2026-08-16 사용자
    지시로 같은 방향이 됐다. 여기만 뒤집혀 있으면 폰트 없는 서버에서 한
    화면의 두 분기표가 서로 반대를 가리킨다(2026-08-16 독립 리뷰)."""
    import html as _h
    qs = list(payload.get("quarters") or [])
    if not qs:
        return ""
    cur = payload.get("currency") or "KRW"
    head = "<th>항목</th>" + "".join(
        f"<th class='num'>{_h.escape(str(q.get('label','')))}</th>" for q in qs)
    rows = ""
    for label, key in (("매출", "매출"), ("영업이익", "영업이익"),
                       ("당기순이익", "당기순이익")):
        cells = "".join(
            f"<td class='num'>{_eok((q.get('financials') or {}).get(key), cur)}</td>"
            for q in qs)
        rows += f"<tr><td>{_h.escape(label)}</td>{cells}</tr>"
    for label, key in (("영업이익률", "영업이익률"), ("순이익률", "순이익률")):
        cells = "".join(
            f"<td class='num'>{_pct((q.get('ratios') or {}).get(key))}</td>"
            for q in qs)
        rows += f"<tr><td>{_h.escape(label)}</td>{cells}</tr>"
    tbl = ('<table class="si-table"><thead><tr>' + head
           + "</tr></thead><tbody>" + rows + "</tbody></table>")
    # 배수 요약 — PNG 타일·푸터와 같은 값을 같은 규칙으로. 이 표는 폰트가
    # 없어 PNG 를 못 만들 때 뜨는 **유일한 화면**이라, 여기에만 없으면
    # 그 경로에서 지표가 통째로 사라진다(실수 #10 표면 동기화).
    _mults = [
        ("TTM PER" + ("*" if payload.get("per_self") else ""),
         "—" if payload.get("per") is None else f"{payload['per']:,.2f}배"),
        ("Forward PER", "N/A" if payload.get("per_forward") is None
         else f"{payload['per_forward']:,.2f}배"),
        ("PSR", "—" if payload.get("psr") is None
         else f"{payload['psr']:,.2f}배"),
    ]
    tbl += ('<div style="font-size:12px;color:var(--fg-soft);margin-top:6px">'
            + " · ".join(f"{_h.escape(k)} <b>{_h.escape(v)}</b>"
                         for k, v in _mults) + "</div>")
    # 각주 — PNG 와 **같은 소스**(_footnotes)를 쓴다. 옛 코드는 구성요소
    # 계정 한 줄만 따로 손으로 복제해, 통화 불일치·이상치로 값이 '—' 가
    # 됐을 때 그 이유가 이 화면에서만 사라졌다(폰트 없는 서버에서 이게
    # 유일한 화면이다 — 2026-08-16 독립 리뷰). 색은 PNG 팔레트를 그대로.
    _fn = _footnotes(payload, payload.get("quarters") or [])
    if _fn:
        tbl += "".join(
            f'<div style="font-size:11px;color:{c};margin-top:4px">'
            f'{_h.escape(t)}</div>' for t, c in _fn)
    return tbl


def get_or_render(ticker: str, snap: dict | None = None, *,
                  run_llm: bool = False) -> dict:
    """온디맨드 진입점. 캐시(파일명=분기 키) 우선, 없으면 렌더.
    반환 {ok, image, payload} — image 는 PNG 경로(폰트 부재 시 None)."""
    payload = build_payload(ticker, snap, run_llm=run_llm)
    if not payload:
        return {"ok": False,
                "error": "분기 재무 데이터 없음(소스 미제공 또는 미지원 시장)"}
    p = cache_path(ticker, payload.get("period_key") or "na",
                   asof=payload.get("asof"))
    # LLM 카드가 **이번에 새로** 붙었을 때만 기존 PNG 를 버린다.
    # ⚠️ 옛 조건은 `ok and run_llm` 이었다 — 탭을 열면 항상 run_llm=True 가
    # 된 뒤로(사용자 2026-08-16 자동 실행) 캐시가 **영구 미스**가 되어 매
    # 조회마다 전체 인포그래픽을 다시 그리고 저장했다(2026-08-16 독립 리뷰).
    # `cached` 는 dart_growth_risk 가 캐시에서 꺼냈을 때만 True 다.
    _gr = payload.get("growth_risk") or {}
    fresh_llm = bool(_gr.get("ok")) and not _gr.get("cached")
    if p.exists() and not fresh_llm:
        return {"ok": True, "image": str(p), "payload": payload, "cached": True}
    img = render_infographic(payload, str(p))
    if img:
        _purge_stale(ticker, p)
    return {"ok": True, "image": img, "payload": payload, "cached": False}


def _purge_stale(ticker: str, keep: Path) -> None:
    """같은 티커의 옛 PNG 정리. 캐시 키에 **시**가 들어가 하루 최대 24장씩
    쌓이는데(분기·렌더버전까지 곱해지면 더) 지우는 곳이 아무 데도 없었다
    (2026-08-16 독립 리뷰). 방금 만든 것만 남긴다 — 옛 파일은 캐시 키가
    달라 어차피 다시 안 읽힌다."""
    try:
        for old in _IMG_DIR.glob(f"{_safe_name(ticker)}_*.png"):
            if old != keep:
                old.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("quarterly_infographic: 옛 PNG 정리 실패: %s", exc)
