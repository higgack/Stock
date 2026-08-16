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


def cache_path(ticker: str, period_key, reprt_code=None,
               asof: str | None = None) -> Path:
    """캐시 파일 경로 = 캐시 키. 새 분기면 파일명이 달라져 자동 재렌더.

    ⚠️ 파일명에 날짜(KST)를 포함한다 — 이미지에 시가총액·TTM PER·PSR 같은
    **시세 기반 값**이 구워지는데, 분기 키만 쓰면 다음 분기까지(최대 3개월)
    그 값이 얼어붙는다(2026-08-19 code-review). 날짜를 넣어 하루 1회 재렌더
    (LLM 은 rcept_no 캐시라 재과금 없음 — 이미지만 다시 그린다).

    `period_key` 는 분기 식별자다. KR 은 (연도, 보고서코드) 2인자 형태를
    유지하고(하위호환), 비-KR 은 분기 종료일 문자열 하나를 준다 —
    reprt_code 가 DART 전용 개념이라 멀티마켓에선 쓸 수 없다."""
    key = f"{period_key}{reprt_code}" if reprt_code is not None else str(period_key)
    return (_IMG_DIR /
            f"{_safe_name(ticker)}_{key}_{asof or _today_kst()}.png")


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
    H_TILE, H_CHART = 17.0, 62.0
    H_CARDS = 20.0 if has_cards else 0.0
    # 11 → 15: 푸터 패널(6.4) 아래에 이상치·자체계산 각주가 최대 2줄 들어가고
    # 그 아래 출처/면책 줄이 온다. 11 이면 각주와 출처줄이 붙어 버린다.
    H_FOOT = 15.0
    H = H_HEAD + H_CALL + H_TILE + H_CHART + H_CARDS + H_FOOT + 6

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
    tiles = [
        ("매출", amt(lf.get("매출")), _ACCENT,
         _chg(lf.get("매출"), (yoy_q or {}).get("financials", {}).get("매출")),
         _chg(lf.get("매출"), (prev_q or {}).get("financials", {}).get("매출"))),
        ("영업이익", amt(lf.get("영업이익")), _POS,
         _chg(lf.get("영업이익"), (yoy_q or {}).get("financials", {}).get("영업이익")),
         _chg(lf.get("영업이익"), (prev_q or {}).get("financials", {}).get("영업이익"))),
        ("영업이익률", _pct(lr.get("영업이익률")), _GOLD, None, None),
        ("당기순이익", amt(lf.get("당기순이익")), _PUR,
         _chg(lf.get("당기순이익"), (yoy_q or {}).get("financials", {}).get("당기순이익")),
         _chg(lf.get("당기순이익"), (prev_q or {}).get("financials", {}).get("당기순이익"))),
        # '*' = 자체계산(시총÷TTM순이익). ASCII 라 폰트 결손 위험이 없다
        # (이모지·특수기호는 NanumGothic 에서 두부로 나올 수 있음).
        ("TTM PER" + ("*" if payload.get("per_self") else ""),
         ("—" if payload.get("per") is None
          else f"{payload['per']:,.2f}배"), _ACCENTW, None, None),
    ]
    tw, gap = 18.0, 1.5
    tx = 2.5
    for name, val, col, yoy, qoq in tiles:
        panel(tx, y, tw, H_TILE - 3, fc=_PANEL, rad=1.6)
        ax.add_patch(Rectangle((tx, y), tw, 0.7, facecolor=col,
                               edgecolor="none"))
        txt(tx + 1.6, y + 3.2, name, size=8.5, color=_MUTED, weight="bold")
        txt(tx + 1.6, y + 7.2, val, size=12.5, color=col, weight="bold")
        sub = []
        if yoy is not None:
            sub.append(f"YoY {yoy:+.1f}%")
        if qoq is not None:
            sub.append(f"QoQ {qoq:+.1f}%")
        if sub:
            txt(tx + 1.6, y + 11.2, " · ".join(sub), size=7.5, color=_MUTED)
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
        for k, series in enumerate(vals):
            bax.bar([i + offs[k] for i in idx], series, width=width,
                    color=bar_colors[k], label=bar_labels[k], zorder=2)
        # 지수 오프셋('1e6') 금지 — 위 단위 스케일링으로 자릿수를 이미 줄였고,
        # 오프셋 텍스트가 축 위에 그려져 제목을 침범한다.
        bax.ticklabel_format(axis="y", style="plain", useOffset=False)
        # 눈금 세밀화 — 옛 코드엔 locator 설정이 아예 없어 축 높이에 맞춰
        # 3~4개로 성기게 잡혔다(사용자 2026-08-16 '숫자간격 더 세밀하게').
        bax.yaxis.set_major_locator(
            MaxNLocator(nbins=7, steps=[1, 2, 2.5, 5, 10]))
        bax.yaxis.set_major_formatter(
            FuncFormatter(lambda v, _p: f"{v:,.{dec}f}"))
        bax.tick_params(axis="y", labelsize=9, colors=_MUTED, length=2)
        bax.tick_params(axis="x", length=0, labelbottom=False)

        if pax is not None:
            pax.plot(idx, [nan if v is None else v for v in line],
                     color=line_color, marker="o", markersize=4.2,
                     linewidth=2.0, zorder=3)
            pax.yaxis.set_major_locator(
                MaxNLocator(nbins=4, steps=[1, 2, 2.5, 5, 10]))
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

    # ── 성장동력 / 리스크 카드(LLM, 없으면 섹션 자체 생략) ──────────
    if has_cards:
        def card_col(x0, w, title, items, color):
            panel(x0, y, w, H_CARDS - 3, fc=_PANEL, rad=1.6)
            ax.add_patch(Rectangle((x0, y), w, 0.7, facecolor=color,
                                   edgecolor="none"))
            txt(x0 + 2, y + 3.2, title, size=9.5, weight="bold")
            iy = y + 6.6
            for i, s in enumerate(items[:4], 1):
                ax.add_patch(FancyBboxPatch(
                    (x0 + 2, iy - 1.15), 2.4, 2.3,
                    boxstyle="round,pad=0,rounding_size=1.15",
                    facecolor=color, edgecolor="none", mutation_aspect=1))
                txt(x0 + 3.2, iy, str(i), size=7.5, color="#0b1020",
                    weight="bold", ha="center")
                body = s if len(s) <= 34 else s[:33] + "…"
                txt(x0 + 5.6, iy, body, size=8)
                iy += 3.4
        card_col(2.5, cw, "확인된 성장동력", drivers, _POS)
        card_col(51.0, cw, "지속조건 · 무효화 리스크", risks, _NEG)
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
        ("PSR", "—" if payload.get("psr") is None
         else f"{payload['psr']:,.2f}배"),
    ]
    fx = 6.0
    for name, val in foot_items:
        txt(fx, y + 2.2, name, size=8, color=_MUTED)
        txt(fx, y + 4.6, val, size=10.5, weight="bold")
        fx += 18.4
    # 이상치·자체계산 각주 — 값이 '—' 로 비었을 때 "왜 비었나"를 화면에서
    # 알 수 있어야 한다(빈칸만 두면 데이터 없음과 구분 불가). 이모지 대신
    # ASCII 마커 + 색으로 표기(NanumGothic 글리프 결손 회피).
    notes: list[tuple[str, str]] = []
    _bad_keys = payload.get("anomaly_keys") or []
    if _bad_keys:
        _lbls = payload.get("anomaly_labels") or []
        _where = f"({', '.join(_lbls)}) " if _lbls else ""
        notes.append((
            f"! {'·'.join(_bad_keys)} 이상치 감지 {_where}— DART 계정 불일치 "
            f"가능. 해당 TTM·PSR 산출 제외(추정 보정 없음)", _NEG))
    if payload.get("per_self"):
        notes.append(("* TTM PER = 시가총액 ÷ TTM 순이익 자체계산"
                      "(데이터 소스가 PER 미제공)", _MUTED))
    _comp = payload.get("component_accounts") or {}
    if _comp:
        # 이상치와 달리 값은 유효하다 — 막지 않고 '총액 아님'만 알린다.
        notes.append((
            "! " + " · ".join(f"{k} = {v}(구성요소 계정)"
                              for k, v in sorted(_comp.items()))
            + " — 총액 계정 미공시라 DART 원자료 그대로(합산·추정 없음)",
            _GOLD))
    if payload.get("currency_mismatch"):
        notes.append((f"! 재무({cur})와 시총({payload.get('trade_currency','')}) "
                      "통화가 달라 PER·PSR 산출 제외(환산 없이 나누면 틀린 배수)",
                      _NEG))
    if payload.get("fiscal_note"):
        notes.append((f"* {payload['fiscal_note']}", _MUTED))
    _ny = y + 7.6
    for _note, _ncol in notes:
        txt(6.0, _ny, _note, size=7.5, color=_ncol)
        _ny += 2.4
    # 기준시각 표기 의무(CLAUDE.md 실수기록 10-b) — 시총/PER/PSR 은 시세
    # 기반이라 '언제 기준'인지 없으면 오래된 값을 현재값으로 오인한다.
    src = payload.get("source_label") or "DART 정기보고서(K-IFRS 연결)"
    asof = payload.get("asof") or _today_kst()
    txt(2.5, H - 2.6, f"수치: {src} · 시총·PER {asof} 기준(KST) · 환각 0",
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
    mcap = snap.get("market_cap")
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

    per = snap.get("trailingPE")
    per_self = False
    if per is None and not cur_mismatch:
        # 야후가 trailingPE 를 안 주는 종목(보험·금융지주에서 흔함) 폴백 —
        # 옛 코드는 단일 소스라 그냥 '—' 였다(사용자 2026-08-16).
        per = self_per(mcap, ttm.get("당기순이익"))
        per_self = per is not None
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
        "company": (snap.get("kr", {}) or {}).get("corp_name")
                   or snap.get("long_name") or t,
        "market": mkt_label,
        "market_cap": mcap,
        "quarters": qs,
        "ttm": ttm,
        "per": per,
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
        "asof": _today_kst(),
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
    최신이 왼쪽(대시보드 분기표와 동일 방향)."""
    import html as _h
    qs = list(reversed(payload.get("quarters") or []))
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
    # 각주는 PNG 쪽에만 있었는데, 이 표는 **폰트가 없어 PNG 를 못 만들 때**
    # 뜨는 유일한 화면이다 — 정확히 그 경로에서 표기가 죽어 있었다
    # (2026-08-16 독립 리뷰).
    comp = payload.get("component_accounts") or {}
    if comp:
        tbl += ('<div style="font-size:11px;color:var(--fg-soft);margin-top:4px">'
                '⚠️ ' + _h.escape(" · ".join(f"{k} = {v}(구성요소 계정)"
                                             for k, v in sorted(comp.items())))
                + ' — 총액 계정 미공시라 DART 원자료 그대로(합산·추정 없음)</div>')
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
    # LLM 카드가 이번에 새로 붙었으면 기존(카드 없는) PNG 를 재사용하면 안 된다.
    fresh_llm = bool((payload.get("growth_risk") or {}).get("ok")) and run_llm
    if p.exists() and not fresh_llm:
        return {"ok": True, "image": str(p), "payload": payload, "cached": True}
    img = render_infographic(payload, str(p))
    return {"ok": True, "image": img, "payload": payload, "cached": False}
