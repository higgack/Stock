"""분기 실적분석 인포그래픽 — KR 종목 분기 스코어카드 PNG.

사용자 2026-08-19 (X @Jaymin_Alpha 스타일). 종목 상세 '분기실적' 탭에서
온디맨드 생성. 구성:
  헤더(종목명·티커·마켓·시총·최신분기) / 헤드라인 콜아웃(LLM, 없으면 생략)
  / 지표 타일 5종(매출·영업이익·영업이익률·당기순이익·TTM PER, YoY·QoQ)
  / 콤보차트 2개(매출+영업이익+영업이익률 / 순이익+순이익률, 최근 5분기)
  / 성장동력·리스크 카드(LLM, 없으면 섹션 생략) / TTM 푸터·출처·면책.

숫자는 전부 bot/dart_quarterly.get_quarterly_series() 의 DART 실측값을
그대로 주입 — 렌더러가 값을 만들지 않는다(환각 0). 정성 카드만 LLM
(bot/dart_growth_risk)이고, 실패하면 그 섹션을 통째로 생략한다.

캐시: 파일명 자체가 캐시 키({티커}_{연도}{reprt_code}.png). 새 분기가
공시되면 probe_latest_reprt_code 가 다른 (year, reprt_code) 를 찾아
파일명이 바뀌므로 시간 TTL 없이 자동 갱신된다.

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


def _eok(v) -> str:
    """원 단위 → 억/조 표기. None 이면 '—'."""
    if v is None:
        return "—"
    a = abs(v) / 1e8
    sign = "-" if v < 0 else ""
    if a >= 10000:
        return f"{sign}{a/10000:,.2f}조"
    return f"{sign}{a:,.0f}억"


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


def cache_path(ticker: str, year: int, reprt_code: str,
               asof: str | None = None) -> Path:
    """캐시 파일 경로 = 캐시 키. 새 분기면 파일명이 달라져 자동 재렌더.

    ⚠️ 파일명에 날짜(KST)를 포함한다 — 이미지에 시가총액·TTM PER·PSR 같은
    **시세 기반 값**이 구워지는데, 분기 키만 쓰면 다음 분기까지(최대 3개월)
    그 값이 얼어붙는다(2026-08-19 code-review). 날짜를 넣어 하루 1회 재렌더
    (LLM 은 rcept_no 캐시라 재과금 없음 — 이미지만 다시 그린다)."""
    return (_IMG_DIR /
            f"{_safe_name(ticker)}_{year}{reprt_code}_{asof or _today_kst()}.png")


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
    H_TILE, H_CHART = 17.0, 26.0
    H_CARDS = 20.0 if has_cards else 0.0
    H_FOOT = 11.0
    H = H_HEAD + H_CALL + H_TILE + H_CHART + H_CARDS + H_FOOT + 6

    fig_w = 11.6
    fig, ax = plt.subplots(figsize=(fig_w, fig_w * (H / W)), dpi=150)
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
    fs_kr = "연결" if last.get("fs_div") == "CFS" else "별도"
    txt(6, y + 11.0, f"{mk} · {payload.get('ticker','')} · {fs_kr}", size=9,
        color="#dbe9ff")
    mcap = payload.get("market_cap")
    if mcap:
        txt(94, y + 4.5, "시가총액", size=8.5, color="#cfe3ff", ha="right")
        txt(94, y + 8.6, _eok(mcap), size=14, color="white", weight="bold",
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
        ("매출", _eok(lf.get("매출")), _ACCENT,
         _chg(lf.get("매출"), (yoy_q or {}).get("financials", {}).get("매출")),
         _chg(lf.get("매출"), (prev_q or {}).get("financials", {}).get("매출"))),
        ("영업이익", _eok(lf.get("영업이익")), _POS,
         _chg(lf.get("영업이익"), (yoy_q or {}).get("financials", {}).get("영업이익")),
         _chg(lf.get("영업이익"), (prev_q or {}).get("financials", {}).get("영업이익"))),
        ("영업이익률", _pct(lr.get("영업이익률")), _GOLD, None, None),
        ("당기순이익", _eok(lf.get("당기순이익")), _PUR,
         _chg(lf.get("당기순이익"), (yoy_q or {}).get("financials", {}).get("당기순이익")),
         _chg(lf.get("당기순이익"), (prev_q or {}).get("financials", {}).get("당기순이익"))),
        ("TTM PER", ("—" if payload.get("per") is None
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

    def combo(rect, bars, bar_labels, bar_colors, line, line_color,
              line_label, title_base):
        """rect = (x0,y0,w,h) in data coords → figure 좌표로 변환해 inset 축."""
        x0, y0, w, h = rect
        panel(x0, y0, w, h, fc=_PANEL, rad=1.6)
        # 금액 단위 자동 선택 — 억 단위로 두면 대형주가 100만(억)대라
        # matplotlib 이 y축에 '1e6' 오프셋을 붙여 제목을 가리고 값도 안 읽힌다.
        # 최대값이 1조 이상이면 조 단위로 스케일(라벨도 함께 바꾼다).
        peak = max((abs(v) for b in bars for v in b if v is not None),
                   default=0)
        if peak >= 1e12:
            div, unit = 1e12, "조원"
        else:
            div, unit = 1e8, "억원"
        txt(x0 + 2, y0 + 2.4, f"{title_base} ({unit} / %)", size=9,
            weight="bold")
        # data 좌표 → figure fraction. ⚠️ x/W 로 직접 나누면 안 된다 —
        # 메인 ax 는 figure 전체가 아니라 기본 여백 안쪽만 차지하므로
        # 축이 패널 밖으로 삐져나온다. 실제 transData→transFigure 변환 사용.
        inv = fig.transFigure.inverted()

        def _d2f(dx, dy):
            return inv.transform(ax.transData.transform((dx, dy)))

        # 제목(baseline y0+2.4)과 겹치지 않게 위 여백 4.5, 눈금 라벨 자리로
        # 좌 6·우 3·아래 3. y 축이 invert 돼 있어 '아래'가 큰 y 값.
        fx0, fy0 = _d2f(x0 + 6, y0 + h - 3.0)
        fx1, fy1 = _d2f(x0 + w - 3.0, y0 + 4.5)
        sub = fig.add_axes([fx0, fy0, fx1 - fx0, fy1 - fy0])
        sub.set_facecolor(_PANEL)
        for sp in sub.spines.values():
            sp.set_color(_LINE)
        n = len(labels)
        idx = list(range(n))
        # 결측 분기를 0 으로 바꾸면 '실적 0' 막대가 그려져 없는 사실을
        # 그린 셈이 된다("환각 0" 푸터와 모순, 2026-08-19 code-review).
        # NaN 은 matplotlib 이 막대를 아예 안 그린다 → 결측이 결측으로 보인다.
        nan = float("nan")
        vals = [[(nan if v is None else v / div) for v in b] for b in bars]
        width = 0.36 if len(bars) > 1 else 0.5
        offs = ([-width / 2, width / 2] if len(bars) > 1 else [0])
        for k, series in enumerate(vals):
            sub.bar([i + offs[k] for i in idx], series, width=width,
                    color=bar_colors[k], label=bar_labels[k], zorder=2)
        sub.set_xticks(idx)
        sub.set_xticklabels(labels, fontsize=7, color=_MUTED)
        # 지수 오프셋('1e6') 금지 — 위 단위 스케일링으로 자릿수를 이미 줄였고,
        # 오프셋 텍스트가 축 위에 그려져 제목을 침범한다.
        sub.ticklabel_format(axis="y", style="plain", useOffset=False)
        sub.tick_params(axis="y", labelsize=7, colors=_MUTED, length=2)
        sub.tick_params(axis="x", length=0)
        sub.grid(axis="y", color=_LINE, linewidth=0.5, alpha=0.6, zorder=0)
        sub.set_axisbelow(True)
        if any(v is not None for v in line):
            s2 = sub.twinx()
            s2.plot(idx, [None if v is None else v for v in line],
                    color=line_color, marker="o", markersize=3.2,
                    linewidth=1.8, zorder=3, label=line_label)
            s2.tick_params(axis="y", labelsize=7, colors=line_color, length=2)
            for sp in s2.spines.values():
                sp.set_color(_LINE)
            s2.set_ylabel("%", fontsize=7, color=line_color)
        lg = sub.legend(loc="upper left", fontsize=6.5, frameon=False,
                        labelcolor=_MUTED, ncol=len(bars))
        if lg:
            lg.set_zorder(4)

    cw = 46.5
    combo((2.5, y, cw, H_CHART - 3), [rev, op], ["매출", "영업이익"],
          [_ACCENT, _POS], opm, _GOLD, "영업이익률",
          "매출 · 영업이익 · 영업이익률")
    combo((51.0, y, cw, H_CHART - 3), [ni], ["당기순이익"], [_PUR],
          nim, _NEG, "순이익률", "당기순이익 · 순이익률")
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
        ("TTM 매출", _eok(ttm.get("매출"))),
        ("TTM 영업이익", _eok(ttm.get("영업이익"))),
        ("TTM 순이익", _eok(ttm.get("당기순이익"))),
        ("TTM PER", "—" if payload.get("per") is None
         else f"{payload['per']:,.2f}배"),
        ("PSR", "—" if payload.get("psr") is None
         else f"{payload['psr']:,.2f}배"),
    ]
    fx = 6.0
    for name, val in foot_items:
        txt(fx, y + 2.2, name, size=8, color=_MUTED)
        txt(fx, y + 4.6, val, size=10.5, weight="bold")
        fx += 18.4
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


def _ttm(qs: list) -> dict:
    """최근 4분기 합 = TTM. 4개 미만이면 빈 dict(부분합으로 TTM 이라 부르면
    틀린 값 — 억지로 만들지 않는다)."""
    if len(qs) < 4:
        return {}
    out: dict = {}
    for k in ("매출", "영업이익", "당기순이익"):
        vals = [(q.get("financials") or {}).get(k) for q in qs[-4:]]
        if all(v is not None for v in vals):
            out[k] = sum(vals)
    return out


def build_payload(ticker: str, snap: dict | None = None, *,
                  run_llm: bool = False) -> dict | None:
    """DART 분기 시계열 + 스냅샷(시총/PER) + (선택)LLM 카드 → 렌더 payload.
    KR 전용. 분기 데이터가 없으면 None(DATA OFFLINE)."""
    t = (ticker or "").upper()
    if not t.endswith((".KS", ".KQ")):
        return None
    try:
        from bot.dart_client import get_dart
        from bot.dart_quarterly import get_quarterly_series, probe_latest_reprt_code
        dart = get_dart()
        qs = get_quarterly_series(dart, t, n=5)
    except Exception as exc:
        log.warning("quarterly_infographic: series %s: %s", t, exc)
        return None
    if not qs:
        return None
    latest = qs[-1]
    snap = snap or {}
    ttm = _ttm(qs)
    per = snap.get("trailingPE")
    mcap = snap.get("marketCap") or snap.get("market_cap")
    psr = None
    if mcap and ttm.get("매출"):
        psr = mcap / ttm["매출"]
    payload = {
        "ticker": t,
        # 스냅샷은 long_name(스네이크) 키를 쓴다 — longName/shortName 은
        # 절대 안 잡혀 폴백이 죽어 있었다(2026-08-19 code-review).
        "company": (snap.get("kr", {}) or {}).get("corp_name")
                   or snap.get("long_name") or t,
        "market": "KOSPI" if t.endswith(".KS") else "KOSDAQ",
        "market_cap": mcap,
        "quarters": qs,
        "ttm": ttm,
        "per": per,
        "psr": psr,
        "latest_year": latest.get("year"),
        "latest_reprt_code": latest.get("reprt_code"),
        "source_label": f"DART 정기보고서(K-IFRS {'연결' if latest.get('fs_div')=='CFS' else '별도'})",
        "asof": _today_kst(),
    }
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
    head = "<th>항목</th>" + "".join(
        f"<th class='num'>{_h.escape(str(q.get('label','')))}</th>" for q in qs)
    rows = ""
    for label, key in (("매출", "매출"), ("영업이익", "영업이익"),
                       ("당기순이익", "당기순이익")):
        cells = "".join(
            f"<td class='num'>{_eok((q.get('financials') or {}).get(key))}</td>"
            for q in qs)
        rows += f"<tr><td>{_h.escape(label)}</td>{cells}</tr>"
    for label, key in (("영업이익률", "영업이익률"), ("순이익률", "순이익률")):
        cells = "".join(
            f"<td class='num'>{_pct((q.get('ratios') or {}).get(key))}</td>"
            for q in qs)
        rows += f"<tr><td>{_h.escape(label)}</td>{cells}</tr>"
    return ('<table class="si-table"><thead><tr>' + head
            + "</tr></thead><tbody>" + rows + "</tbody></table>")


def get_or_render(ticker: str, snap: dict | None = None, *,
                  run_llm: bool = False) -> dict:
    """온디맨드 진입점. 캐시(파일명=분기 키) 우선, 없으면 렌더.
    반환 {ok, image, payload} — image 는 PNG 경로(폰트 부재 시 None)."""
    payload = build_payload(ticker, snap, run_llm=run_llm)
    if not payload:
        return {"ok": False, "error": "분기 재무 없음(DART 미제공 또는 비-KR)"}
    p = cache_path(ticker, payload["latest_year"], payload["latest_reprt_code"],
                   payload.get("asof"))
    # LLM 카드가 이번에 새로 붙었으면 기존(카드 없는) PNG 를 재사용하면 안 된다.
    fresh_llm = bool((payload.get("growth_risk") or {}).get("ok")) and run_llm
    if p.exists() and not fresh_llm:
        return {"ok": True, "image": str(p), "payload": payload, "cached": True}
    img = render_infographic(payload, str(p))
    return {"ok": True, "image": img, "payload": payload, "cached": False}
