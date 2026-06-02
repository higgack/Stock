"""Industry-level export trends from the 관세청 chapter sweep.

Phase-1 (this module): pure data — turn the per-HS-leaf monthly series
that customs_scan.build_series() already produces into per-INDUSTRY
monthly series, then compute the indicators the trend dashboard needs:

  - exp (monthly export USD, industry total = Σ member HS leaves)
  - yoy (year-over-year %, vs the same month 12 months earlier)
  - dyoy (ΔYoY = YoY 1st difference, '가속도' — this month's YoY minus
    last month's YoY, in %p)
  - ma12 (12-month moving average of exp)
  - classify (초고성장/강세 · 턴어라운드 후보 · 부진/재하락) from the
    latest YoY + ΔYoY, mirroring the reference dashboard's grouping

No new API calls: the caller passes the leaves dict from the chapter
scan it already ran. Industry membership comes from the official
HSK-MTI 연계표 (trade/mti_map.py). Leaves with no industry (or the
catch-all '기타') are dropped from the named-industry view.

Phase-2 (separate) renders these series as the SVG trend cards.
"""
from __future__ import annotations

from typing import Optional

from trade import mti_map


def aggregate_by_industry(
    leaves: dict[str, dict],
    *,
    path=mti_map.DEFAULT_PATH,
    field: str = "exp_dlr",
) -> dict[str, dict[str, int]]:
    """{industry: {ym: total}} summing each industry's member HS leaves
    per month for `field` ('exp_dlr' exports / 'imp_dlr' imports).
    Unmapped / '기타' leaves are excluded.

    leaves is customs_scan.build_series() output:
        {hs_code: {"name": str, "months": {ym: {"exp_dlr", "imp_dlr"}}}}
    """
    try:
        hsk_to_ind = mti_map.load(path)
    except mti_map.HskMtiFileMissing:
        return {}
    out: dict[str, dict[str, int]] = {}
    for hs, node in leaves.items():
        industry = hsk_to_ind.get(hs)
        if not industry or industry == mti_map.CATCH_ALL:
            continue
        bucket = out.setdefault(industry, {})
        for ym, fig in node["months"].items():
            bucket[ym] = bucket.get(ym, 0) + (fig.get(field) or 0)
    return out


def aggregate_by_mti(
    leaves: dict[str, dict],
    *,
    path=mti_map.DEFAULT_PATH,
    field: str = "exp_dlr",
) -> dict[str, dict]:
    """{mti6: {"name","industry","months":{ym:total}}} summing member HS
    leaves per month. The 하위품목 view ranks these (D램·낸드·웨이퍼 …)
    one level below industry. Unmapped/'기타' excluded."""
    try:
        hsk_map = mti_map.load_mti(path)
        names = mti_map.mti_names(path)
    except mti_map.HskMtiFileMissing:
        return {}
    out: dict[str, dict] = {}
    for hs, node in leaves.items():
        rec = hsk_map.get(hs)
        if not rec:
            continue
        mti6, industry, _ = rec
        if not mti6 or industry == mti_map.CATCH_ALL:
            continue
        nm, ind = names.get(mti6, (mti6, industry))
        bucket = out.setdefault(mti6, {"name": nm, "industry": ind, "months": {}})
        for ym, fig in node["months"].items():
            bucket["months"][ym] = bucket["months"].get(ym, 0) + (fig.get(field) or 0)
    return out


def _prev_year_month(ym: str) -> str:
    """'2026-04' → '2025-04'. Accepts 'YYYY-MM' or 'YYYYMM'."""
    s = ym.replace("-", "")
    y, m = int(s[:4]), int(s[4:6])
    return f"{y - 1:04d}-{m:02d}"


def _prev_month(ym: str) -> str:
    """'2026-04' → '2026-03', '2026-01' → '2025-12' (for MoM/전월대비).
    Accepts 'YYYY-MM' or 'YYYYMM'."""
    s = ym.replace("-", "")
    y, m = int(s[:4]), int(s[4:6])
    m -= 1
    if m == 0:
        m = 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _ma(values: list[int], window: int = 12) -> Optional[float]:
    """Moving average of the last `window` values, or None when fewer
    than `window` points exist (so a half-formed MA isn't shown)."""
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def industry_series(
    by_industry: dict[str, dict[str, int]],
) -> dict[str, list[dict]]:
    """Per industry, a month-ascending list of points:
        {ym, exp, yoy, dyoy, ma12}
    yoy is None when the year-earlier month is absent; dyoy is None when
    either this or last month's yoy is undefined; ma12 None until 12
    months exist. Pure — no I/O."""
    out: dict[str, list[dict]] = {}
    for industry, months in by_industry.items():
        yms = sorted(months)
        # normalise keys to 'YYYY-MM' for year-ago lookup
        norm = {}
        for ym in yms:
            s = ym.replace("-", "")
            norm[f"{s[:4]}-{s[4:6]}"] = months[ym]
        keys = sorted(norm)
        points: list[dict] = []
        exp_running: list[int] = []
        prev_yoy: Optional[float] = None
        ttm_hist: dict[str, float] = {}   # ym → trailing-12-mo sum (for TTM YoY)
        for k in keys:
            exp = norm[k] or 0
            exp_running.append(exp)
            ago = norm.get(_prev_year_month(k))
            yoy = ((exp - ago) / ago * 100.0) if ago else None
            dyoy = (yoy - prev_yoy) if (yoy is not None and prev_yoy is not None) else None
            # TTM = trailing 12-month export sum (only once 12 months exist).
            ttm = sum(exp_running[-12:]) if len(exp_running) >= 12 else None
            if ttm is not None:
                ttm_hist[k] = ttm
            # TTM YoY needs this TTM and the one 12 months earlier (=24 mo data)
            ttm_ago = ttm_hist.get(_prev_year_month(k))
            ttm_yoy = ((ttm - ttm_ago) / ttm_ago * 100.0) if (ttm and ttm_ago) else None
            points.append({
                "ym": k,
                "exp": exp,
                "yoy": yoy,
                "dyoy": dyoy,
                "ma12": _ma(exp_running, 12),
                "ttm": ttm,
                "ttm_yoy": ttm_yoy,
            })
            if yoy is not None:
                prev_yoy = yoy
        out[industry] = points
    return out


def _avg(vals: list) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def momentum(points: list[dict]) -> dict:
    """Latest-state momentum metrics used by the interpretation text:
    3-month average YoY / ΔYoY (the reference card shows these)."""
    last3 = points[-3:]
    return {
        "yoy3": _avg([p.get("yoy") for p in last3]),
        "dyoy3": _avg([p.get("dyoy") for p in last3]),
    }



# Classification thresholds (mirror the reference dashboard's buckets;
# env-free constants — these are display grouping, not blast-radius
# parameters). YoY in %, ΔYoY in %p.
_HIGH_GROWTH_YOY = 20.0     # 초고성장/강세: strong & (still) accelerating
_TURNAROUND_DYOY = 0.0      # 턴어라운드: YoY accelerating (ΔYoY > 0)


def classify(points: list[dict]) -> str:
    """Bucket an industry by its latest YoY + ΔYoY:

      초고성장/강세   — latest YoY ≥ +20%
      턴어라운드 후보 — YoY < +20% but ΔYoY > 0 (accelerating off a low base)
      부진/재하락     — everything else (decelerating / negative)

    Returns '데이터부족' when the latest point has no YoY yet."""
    if not points:
        return "데이터부족"
    latest = points[-1]
    yoy, dyoy = latest.get("yoy"), latest.get("dyoy")
    if yoy is None:
        return "데이터부족"
    if yoy >= _HIGH_GROWTH_YOY:
        return "초고성장/강세"
    if dyoy is not None and dyoy > _TURNAROUND_DYOY:
        return "턴어라운드 후보"
    return "부진/재하락"


def interpret(points: list[dict]) -> dict:
    """Plain-language reading of an industry's latest state, mirroring the
    reference dashboard's two lines:

      summary    — overall level (3-month avg YoY based)
      signal     — momentum label + ΔYoY explanation (가속/둔화/반등 …)

    Sentences are taken verbatim from the reference so the tone matches.
    Returns {summary, signal_label, signal_text} (signal_* '' when N/A)."""
    if not points:
        return {"summary": "", "signal_label": "", "signal_text": ""}
    latest = points[-1]
    yoy = latest.get("yoy")
    dyoy = latest.get("dyoy")
    m = momentum(points)
    yoy3 = m["yoy3"]

    # summary — level (matches reference phrasing)
    if yoy is None:
        summary = "YoY 계산에 필요한 전년동월 데이터가 아직 부족합니다."
    elif yoy3 is not None and yoy3 >= 50:
        summary = "최근 3개월 YoY 평균이 매우 높고 최신월도 압도적입니다."
    elif yoy >= 0 and yoy3 is not None and yoy3 >= 10:
        summary = "양의 성장률이 이어지고 최근 평균도 두 자릿수입니다."
    elif yoy >= 0 and (dyoy is not None and dyoy > 0):
        summary = "최근 마이너스 구간을 지나 양의 성장률로 돌아섰습니다."
    elif yoy >= 0:
        summary = "성장은 유지되지만 직전월 대비 속도가 둔화했습니다."
    else:
        summary = "최근 성장률이 마이너스에 머물러 있습니다."

    # signal — ΔYoY (1차 미분) momentum
    label, text = "", ""
    if dyoy is not None:
        if yoy is not None and yoy >= _HIGH_GROWTH_YOY and dyoy < 0:
            label = "고성장 둔화"
            text = "절대 성장률은 높지만 YoY의 1차 미분이 마이너스로 꺾였습니다."
        elif dyoy >= 5:
            label = "가속 확대"
            text = "YoY의 1차 미분이 큰 폭의 플러스입니다."
        elif dyoy > 0:
            label = "가속 유지"
            text = "YoY가 전월보다 더 높아졌습니다."
        elif dyoy < 0 and yoy is not None and yoy < 0:
            label = "재하락"
            text = "직전월 플러스 이후 다시 마이너스로 내려왔습니다."
        else:
            label = "둔화"
            text = "YoY가 전월보다 낮아졌습니다."
    return {"summary": summary, "signal_label": label, "signal_text": text}


# ───────────────────────── rendering (SVG trend cards) ─────────────────────────
# Self-contained SVG (no chart lib), mirroring the reference dashboard:
# export-value line + 12-month MA line + markers + grid, one card per
# industry with a stat row, grouped by classification. Same pattern as
# dashboard.py's other panels.

import html as _html
import json as _json

# Pixel geometry of one chart (matches the reference's 380×132 viewBox).
_VW, _VH = 380, 132
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 34, 10, 12, 22

# Classification → (badge label, CSS class). Order = display order.
_GROUPS = [
    ("초고성장/강세", "hot"),
    ("턴어라운드 후보", "turn"),
    ("부진/재하락", "down"),
    ("데이터부족", "na"),
]


def _eokusd(n) -> str:
    """USD → '억 달러' label matching the reference (e.g. 11.18B → '112억',
    TTM 131B → '1,310억'). 1억 USD = 1e8. The reference uses 억 throughout
    (no 조 unit), so big TTM sums just get a thousands separator."""
    if n is None:
        return "—"
    eok = n / 1e8
    if abs(eok) >= 100:
        return f"{eok:,.0f}억"
    return f"{eok:.1f}억"


def _pct(p, suffix="%") -> str:
    return f"{p:+.1f}{suffix}" if p is not None else "—"


def _dot_ym(ym: str) -> str:
    """'2026-04' → '2026.4' (drops month leading zero), matching the
    reference axis-label style."""
    if "-" not in ym:
        return ym
    y, m = ym.split("-", 1)
    return f"{y}.{int(m)}"


def _line_svg(series: list[tuple[int, float]], titles: list[str], *,
              dashed_second=None, label="",
              x_first="", x_last="", y_hi_lbl="", y_lo_lbl="",
              callouts: list[tuple[int, float, str, str]] | None = None,
              main_class: str = "ind-value-line",
              dot_class: str = "ind-latest-dot") -> str:
    """Generic 1-or-2 line SVG with axis labels.

    series       — [(i, value)] PRIMARY line (solid).
    dashed_second— optional [(i, value)] secondary line (dashed; 12M MA).
    titles       — hover <title> per primary point.
    x_first/x_last — X-axis date labels (e.g. '2024.1' / '2026.5').
    y_hi_lbl/y_lo_lbl — Y-axis top/bottom tick labels (e.g. '394' / '72').
    callouts     — [(i, value, text, cls)] in-chart text labels anchored at
                   a point (e.g. (last, exp, '최신 372억', 'ind-cl')).
    Returns '' if < 2 points."""
    if len(series) < 2:
        return ""
    n_max = max(i for i, _ in series)
    allv = [v for _, v in series] + [v for _, v in (dashed_second or [])]
    lo, hi = min(allv), max(allv)
    span = (hi - lo) or 1
    plot_w = _VW - _PAD_L - _PAD_R
    plot_h = _VH - _PAD_T - _PAD_B

    def x(i):
        return _PAD_L + (plot_w * i / n_max) if n_max else _PAD_L
    def y(v):
        return _PAD_T + plot_h * (1 - (v - lo) / span)

    grid = "".join(
        f'<line x1="{_PAD_L}" y1="{_PAD_T + plot_h*f:.1f}" '
        f'x2="{_VW-_PAD_R}" y2="{_PAD_T + plot_h*f:.1f}" class="ind-grid"/>'
        for f in (0.0, 0.5, 1.0)
    )
    main = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in series)
    main_line = f'<polyline points="{main}" class="{main_class}"/>'
    second = ""
    if dashed_second and len(dashed_second) >= 2:
        sp = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in dashed_second)
        second = f'<polyline points="{sp}" class="ind-ma-line"/>'
    li, lv = series[-1]
    dot = f'<circle cx="{x(li):.1f}" cy="{y(lv):.1f}" r="3.2" class="{dot_class}"/>'
    tip = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5" fill="transparent">'
        f'<title>{_html.escape(titles[k])}</title></circle>'
        for k, (i, v) in enumerate(series) if k < len(titles)
    )
    # axis tick labels
    axes = ""
    if y_hi_lbl:
        axes += f'<text x="2" y="{_PAD_T+4:.0f}" class="ind-axis">{_html.escape(y_hi_lbl)}</text>'
    if y_lo_lbl:
        axes += f'<text x="2" y="{_PAD_T+plot_h:.0f}" class="ind-axis">{_html.escape(y_lo_lbl)}</text>'
    if x_first:
        axes += f'<text x="{_PAD_L}" y="{_VH-3}" class="ind-axis">{_html.escape(x_first)}</text>'
    if x_last:
        axes += f'<text x="{_VW-_PAD_R}" y="{_VH-3}" class="ind-axis" text-anchor="end">{_html.escape(x_last)}</text>'
    # in-chart callouts (최신/저점/MA)
    co = ""
    for ci, cv, ctext, ccls in (callouts or []):
        cx = x(ci); cy = y(cv)
        anchor = "end" if cx > _VW * 0.6 else "start"
        co += (f'<text x="{cx:.0f}" y="{max(cy-4,9):.0f}" class="{ccls}" '
               f'text-anchor="{anchor}">{_html.escape(ctext)}</text>')
    return (
        f'<svg viewBox="0 0 {_VW} {_VH}" role="img" aria-label="{label}" '
        f'class="ind-chart">{grid}{axes}{main_line}{second}{dot}{co}{tip}</svg>'
    )


def _monthly_chart(pts: list[dict]) -> str:
    """수출액 + 12M MA, with axis labels + 최신/저점/MA callouts (reference)."""
    vs = [(i, p["exp"]) for i, p in enumerate(pts) if p.get("exp") is not None]
    ma = [(i, p["ma12"]) for i, p in enumerate(pts) if p.get("ma12") is not None]
    titles = [f"{p['ym']} | {_eokusd(p['exp'])} | YoY {_pct(p.get('yoy'))}" for p in pts]
    if len(vs) < 2:
        return ""
    exps = [v for _, v in vs]
    hi, lo = max(exps), min(exps)
    # callouts: latest value, trough, and the latest MA
    li, lv = vs[-1]
    lo_i, lo_v = min(vs, key=lambda t: t[1])
    callouts = [
        (li, lv, f"최신 {_eokusd(lv)}", "ind-cl"),
        (lo_i, lo_v, f"저점 {_eokusd(lo_v)}", "ind-cl"),
    ]
    if ma:
        mi, mv = ma[-1]
        callouts.append((mi, mv, f"MA {_eokusd(mv)}", "ind-cl-ma"))
    return _line_svg(
        vs, titles, dashed_second=ma, label="수출액 추이와 12개월 이동평균",
        x_first=_dot_ym(pts[0]["ym"]), x_last=_dot_ym(pts[-1]["ym"]),
        y_hi_lbl=_eokusd(hi), y_lo_lbl=_eokusd(lo), callouts=callouts,
    )


def _ttm_chart(pts: list[dict]) -> str:
    """12M TTM 수출액 line (only where TTM defined)."""
    vs = [(i, p["ttm"]) for i, p in enumerate(pts) if p.get("ttm") is not None]
    if len(vs) < 2:
        return ""
    tps = [p for p in pts if p.get("ttm") is not None]
    titles = [f"{p['ym']} | TTM {_eokusd(p['ttm'])} | TTM YoY {_pct(p.get('ttm_yoy'))}"
              for p in tps]
    vals = [v for _, v in vs]
    li, lv = vs[-1]
    return _line_svg(
        vs, titles, label="12개월 TTM 수출액 추이",
        x_first=_dot_ym(tps[0]["ym"]), x_last=_dot_ym(tps[-1]["ym"]),
        y_hi_lbl=_eokusd(max(vals)), y_lo_lbl=_eokusd(min(vals)),
        callouts=[(li, lv, f"최신 {_eokusd(lv)}", "ind-cl")],
    )


def _ttm_yoy_chart(pts: list[dict]) -> str:
    """12M TTM YoY 성장률 line (violet, matches reference). Needs ≥24 mo."""
    vs = [(i, p["ttm_yoy"]) for i, p in enumerate(pts) if p.get("ttm_yoy") is not None]
    if len(vs) < 2:
        return ""
    tps = [p for p in pts if p.get("ttm_yoy") is not None]
    titles = [f"{p['ym']} | TTM YoY {_pct(p.get('ttm_yoy'))}" for p in tps]
    vals = [v for _, v in vs]
    li, lv = vs[-1]
    return _line_svg(
        vs, titles, label="12개월 TTM YoY 성장률",
        x_first=_dot_ym(tps[0]["ym"]), x_last=_dot_ym(tps[-1]["ym"]),
        y_hi_lbl=f"{max(vals):.0f}%", y_lo_lbl=f"{min(vals):.0f}%",
        callouts=[(li, lv, f"{_pct(lv)}", "ind-cl")],
        main_class="ind-ttm-yoy-line", dot_class="ind-ttm-yoy-dot",
    )


def _stat_row(points: list[dict]) -> str:
    """<dl>: 최신월·수출액·YoY·ΔYoY·3M평균YoY·3M평균ΔYoY·12M MA·MA대비
    (mirrors the reference card)."""
    latest = points[-1]
    exp, ma = latest["exp"], latest.get("ma12")
    ma_rel = ((exp - ma) / ma * 100.0) if ma else None
    m = momentum(points)
    def cls(v): return "pos" if (v or 0) > 0 else "neg"
    return (
        "<dl class='ind-stats'>"
        f"<div><dt>최신월</dt><dd>{_html.escape(_dot_ym(latest['ym']))}</dd></div>"
        f"<div><dt>수출액</dt><dd>{_eokusd(exp)}</dd></div>"
        f"<div><dt>YoY</dt><dd class='{cls(latest.get('yoy'))}'>{_pct(latest.get('yoy'))}</dd></div>"
        f"<div><dt>ΔYoY</dt><dd class='{cls(latest.get('dyoy'))}'>{_pct(latest.get('dyoy'),'%p')}</dd></div>"
        f"<div><dt>3개월 평균 YoY</dt><dd class='{cls(m['yoy3'])}'>{_pct(m['yoy3'])}</dd></div>"
        f"<div><dt>3개월 평균 ΔYoY</dt><dd class='{cls(m['dyoy3'])}'>{_pct(m['dyoy3'],'%p')}</dd></div>"
        f"<div><dt>12M MA</dt><dd>{_eokusd(ma)}</dd></div>"
        f"<div><dt>MA대비</dt><dd>{_pct(ma_rel)}</dd></div>"
        "</dl>"
    )


def _yoy_bar_svg(pts: list[dict]) -> str:
    """YoY growth-rate bar chart (green up / red down), with a zero line —
    mirrors the reference's 2nd chart. Bars only where YoY is defined."""
    ys = [(i, p["yoy"]) for i, p in enumerate(pts) if p.get("yoy") is not None]
    if len(ys) < 2:
        return ""
    n_max = len(pts) - 1
    vals = [v for _, v in ys]
    hi = max(vals + [0]); lo = min(vals + [0])
    span = (hi - lo) or 1
    plot_w = _VW - _PAD_L - _PAD_R
    plot_h = _VH - _PAD_T - _PAD_B
    def x(i): return _PAD_L + (plot_w * i / n_max) if n_max else _PAD_L
    def y(v): return _PAD_T + plot_h * (1 - (v - lo) / span)
    zero_y = y(0)
    bw = max(2.0, plot_w / (n_max + 1) * 0.7)
    bars = []
    for i, v in ys:
        bx = x(i) - bw / 2
        if v >= 0:
            by, bh = y(v), zero_y - y(v)
            cls = "ind-bar-pos"
        else:
            by, bh = zero_y, y(v) - zero_y
            cls = "ind-bar-neg"
        p = pts[i]
        bars.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" '
            f'height="{max(bh,0.6):.1f}" class="{cls}">'
            f'<title>{_html.escape(p["ym"])} | YoY {_pct(p.get("yoy"))} | '
            f'ΔYoY {_pct(p.get("dyoy"),"%p")}</title></rect>'
        )
    zero = (f'<line x1="{_PAD_L}" y1="{zero_y:.1f}" x2="{_VW-_PAD_R}" '
            f'y2="{zero_y:.1f}" class="ind-zero-line"/>')
    # axis labels: top=+hi%, bottom=lo%, X dates, latest YoY callout
    li, lv = ys[-1]
    axes = (
        f'<text x="2" y="{_PAD_T+4:.0f}" class="ind-axis">+{hi:.0f}%</text>'
        f'<text x="2" y="{_PAD_T+plot_h:.0f}" class="ind-axis">{lo:.0f}%</text>'
        f'<text x="{_PAD_L}" y="{_VH-3}" class="ind-axis">{_html.escape(_dot_ym(pts[0]["ym"]))}</text>'
        f'<text x="{_VW-_PAD_R}" y="{_VH-3}" class="ind-axis" text-anchor="end">'
        f'{_html.escape(_dot_ym(pts[-1]["ym"]))}</text>'
        f'<text x="{x(li):.0f}" y="{max(y(lv)-4,9):.0f}" class="ind-cl" '
        f'text-anchor="end">{_pct(lv)}</text>'
    )
    return (f'<svg viewBox="0 0 {_VW} {_VH}" role="img" aria-label="전년동월 대비 성장률" '
            f'class="ind-chart">{zero}{axes}{"".join(bars)}</svg>')


def _raw_table(pts: list[dict]) -> str:
    """월별 원자료 table (가로 스크롤): 수출액·YoY·ΔYoY·12M MA·MA대비."""
    months = [p["ym"] for p in pts]
    def row(label, fn, cls_fn=None):
        cells = []
        for p in pts:
            val = fn(p)
            c = f" class='{cls_fn(p)}'" if cls_fn else ""
            cells.append(f"<td{c}>{val}</td>")
        return f"<tr><th>{label}</th>{''.join(cells)}</tr>"
    def cls(v):
        return "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "")
    head = "<tr><th>구분</th>" + "".join(
        f"<th>{_html.escape(_dot_ym(m))}</th>" for m in months) + "</tr>"
    body = (
        row("수출액", lambda p: _eokusd(p["exp"]))
        + row("YoY", lambda p: _pct(p.get("yoy")), lambda p: cls(p.get("yoy")))
        + row("ΔYoY", lambda p: _pct(p.get("dyoy"), "%p"), lambda p: cls(p.get("dyoy")))
        + row("12M MA", lambda p: _eokusd(p.get("ma12")))
        + row("MA 대비", lambda p: _pct(
            ((p["exp"] - p["ma12"]) / p["ma12"] * 100.0) if p.get("ma12") else None),
            lambda p: cls(((p["exp"] - p["ma12"]) / p["ma12"] * 100.0)
                          if p.get("ma12") else None))
    )
    # Always-visible (no details) matching the reference. Horizontal scroll
    # for the wide month range, sticky first column.
    return (f"<div class='ind-raw'><div class='ind-raw-title'>월별 원자료</div>"
            f"<div class='ind-raw-scroll'><table class='ind-table'>"
            f"<thead>{head}</thead><tbody>{body}</tbody></table></div></div>")


def _card_body(pts: list[dict]) -> str:
    """Reference layout — a flat 3-column row: meta | chart-1 | chart-2.
    Each chart cell holds a monthly panel and a ttm panel; the toggle
    swaps which is visible (chart-1: 수출액+MA ↔ TTM 수출액; chart-2:
    YoY 막대 ↔ TTM YoY). Wide charts, compact meta — matches the original
    (meta 0.95fr : chart1 1.25fr : chart2 1fr)."""
    interp = interpret(pts)
    note = ""
    if interp["signal_label"]:
        note = (f"<p class='ind-signal'><b>{_html.escape(interp['signal_label'])}</b>"
                f" · {_html.escape(interp['signal_text'])}</p>")
    summary = (f"<p class='ind-summary'>{_html.escape(interp['summary'])}</p>"
               if interp["summary"] else "")
    toggle = (
        "<div class='ind-toggle' role='group'>"
        "<button type='button' class='ind-tg-btn is-active' data-ind-view='monthly'>월별</button>"
        "<button type='button' class='ind-tg-btn' data-ind-view='ttm'>12M TTM</button>"
        "</div>"
    )
    meta = f"<div class='ind-meta'>{toggle}{_stat_row(pts)}{summary}{note}</div>"

    monthly = _monthly_chart(pts)
    bars = _yoy_bar_svg(pts)
    ttm = _ttm_chart(pts)
    ttm_yoy = _ttm_yoy_chart(pts)

    def cell(title_m, svg_m, title_t, svg_t, na_t):
        """One chart column: monthly panel + ttm panel (toggle-swapped)."""
        m = (f"<div class='ind-panel ind-monthly'>"
             f"<div class='ind-chart-title'>{title_m}</div>{svg_m}</div>"
             if svg_m else "<div class='ind-panel ind-monthly'></div>")
        if svg_t:
            t = (f"<div class='ind-panel ind-ttm' hidden>"
                 f"<div class='ind-chart-title'>{title_t}</div>{svg_t}</div>")
        else:
            t = (f"<div class='ind-panel ind-ttm' hidden>"
                 f"<div class='ind-na'>{na_t}</div></div>")
        return f"<div class='ind-chart-cell'>{m}{t}</div>"

    cell1 = cell("수출액 및 12개월 이동평균", monthly,
                 "12개월 TTM 수출액", ttm,
                 "TTM은 24개월 이상 데이터가 필요합니다.")
    cell2 = cell("YoY 성장률", bars,
                 "12개월 TTM YoY 성장률", ttm_yoy,
                 "TTM YoY는 24개월 이상 데이터가 필요합니다.")
    return (f"<div class='ind-row'>{meta}{cell1}{cell2}</div>"
            f"{_raw_table(pts)}")


def init_db(conn) -> None:
    """One-row-per-industry store of the aggregated monthly series (export
    + import). The 24-month chapter fetch is too heavy for render time, so
    the scan job precomputes & stores; the dashboard reads this."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS industry_series ("
        "industry TEXT PRIMARY KEY, months_json TEXT NOT NULL, "
        "imports_json TEXT, updated_ts REAL)"
    )
    # backward-compat: add imports_json to a pre-existing table
    try:
        conn.execute("ALTER TABLE industry_series ADD COLUMN imports_json TEXT")
    except Exception:
        pass
    conn.commit()


def store(conn, by_industry: dict[str, dict[str, int]],
          by_import: dict[str, dict[str, int]] | None = None,
          now: float | None = None) -> int:
    """Replace the stored series with the latest aggregation (export +
    optional import). Returns rows written. Empty export is ignored
    (never wipes a good snapshot)."""
    import time
    if not by_industry:
        return 0
    by_import = by_import or {}
    now = now if now is not None else time.time()
    init_db(conn)
    conn.execute("DELETE FROM industry_series")
    for ind, months in by_industry.items():
        imp = by_import.get(ind)
        conn.execute(
            "INSERT INTO industry_series "
            "(industry, months_json, imports_json, updated_ts) VALUES (?,?,?,?)",
            (ind, _json.dumps(months, ensure_ascii=False),
             _json.dumps(imp, ensure_ascii=False) if imp else None, now),
        )
    conn.commit()
    return len(by_industry)


def load_stored(conn) -> dict[str, dict[str, int]]:
    """{industry: {ym: exp}} from the store, or {} when absent/empty."""
    try:
        cur = conn.execute("SELECT industry, months_json FROM industry_series")
    except Exception:
        return {}
    out: dict[str, dict[str, int]] = {}
    for ind, mj in cur.fetchall():
        try:
            out[ind] = _json.loads(mj)
        except Exception:
            continue
    return out


def store_mti(conn, by_mti: dict[str, dict],
              by_mti_import: dict[str, dict] | None = None,
              now: float | None = None) -> int:
    """Store MTI6 하위품목 export series + optional import series (each a
    JSON blob per MTI6). Separate table from industry_series. Empty export
    input ignored."""
    import time
    if not by_mti:
        return 0
    by_mti_import = by_mti_import or {}
    now = now if now is not None else time.time()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mti_series ("
        "mti6 TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
        "import_json TEXT, updated_ts REAL)"
    )
    try:
        conn.execute("ALTER TABLE mti_series ADD COLUMN import_json TEXT")
    except Exception:
        pass
    conn.execute("DELETE FROM mti_series")
    for mti6, node in by_mti.items():
        imp = by_mti_import.get(mti6)
        conn.execute(
            "INSERT INTO mti_series (mti6, payload_json, import_json, updated_ts) "
            "VALUES (?,?,?,?)",
            (mti6, _json.dumps(node, ensure_ascii=False),
             _json.dumps(imp, ensure_ascii=False) if imp else None, now),
        )
    conn.commit()
    return len(by_mti)


def load_mti_stored(conn) -> dict[str, dict]:
    """{mti6: {name,industry,months}} or {} when absent/empty."""
    try:
        cur = conn.execute("SELECT mti6, payload_json FROM mti_series")
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for mti6, pj in cur.fetchall():
        try:
            out[mti6] = _json.loads(pj)
        except Exception:
            continue
    return out


def load_mti_imports(conn) -> dict[str, dict]:
    """{mti6: {name,industry,months}} import series, or {} when absent/old
    schema (import_json NULL)."""
    try:
        cur = conn.execute("SELECT mti6, import_json FROM mti_series")
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for mti6, ij in cur.fetchall():
        if not ij:
            continue
        try:
            out[mti6] = _json.loads(ij)
        except Exception:
            continue
    return out


def load_stored_imports(conn) -> dict[str, dict[str, int]]:
    """{industry: {ym: imp}} from the store, or {} when absent/empty/old
    schema (imports_json NULL)."""
    try:
        cur = conn.execute("SELECT industry, imports_json FROM industry_series")
    except Exception:
        return {}
    out: dict[str, dict[str, int]] = {}
    for ind, mj in cur.fetchall():
        if not mj:
            continue
        try:
            out[ind] = _json.loads(mj)
        except Exception:
            continue
    return out


def _summary_board(series: dict[str, list[dict]]) -> str:
    """Top-of-tab summary board mirroring the reference header:
      - 4 classification boxes (초고성장/턴어라운드/부진 with %YoY chips,
        + a 분류 기준 explainer)
      - 2 derivative boxes (가속 확대 / 고성장 둔화·기울기 하락, ΔYoY chips)
    Pure presentation over the already-computed series. As 하위품목(B)/
    수입(C) land, their entries flow into the same series dict and appear
    here automatically — no board change needed."""
    rows = [(ind, pts[-1]) for ind, pts in series.items() if pts]

    def chip(name, val, suffix="%"):
        cl = "pos" if (val or 0) > 0 else "neg"
        return (f"<span class='ind-mini-chip'><b>{_html.escape(name)}</b> "
                f"<span class='{cl}'>{_pct(val, suffix)}</span></span>")

    # classification boxes — chips sorted by latest YoY desc
    by_cls: dict[str, list[tuple[str, float]]] = {}
    for ind, pts in series.items():
        if not pts:
            continue
        by_cls.setdefault(classify(pts), []).append((ind, pts[-1].get("yoy")))
    boxes = []
    for label, cls in _GROUPS:
        items = [t for t in by_cls.get(label, []) if t[1] is not None]
        if not items and label != "데이터부족":
            items = by_cls.get(label, [])
        if not items:
            continue
        items.sort(key=lambda t: (t[1] if t[1] is not None else -9e9), reverse=True)
        chips = "".join(chip(n, v) for n, v in items)
        boxes.append(
            f"<div class='ind-sbox ind-sbox-{cls}'><h3>{label}</h3>"
            f"<div class='ind-chip-wrap'>{chips}</div></div>"
        )
    # 분류 기준 explainer (verbatim from reference)
    boxes.append(
        "<div class='ind-sbox ind-sbox-info'><h3>분류 기준</h3>"
        "<p>최신 YoY · 3개월 평균 ΔYoY(가속도)로 분류합니다. YoY가 높아도 "
        "ΔYoY가 마이너스로 꺾이면 기대의 가속이 둔화되는 신호로 봅니다.</p></div>"
    )

    # derivative boxes — by latest ΔYoY (가속 vs 둔화)
    dyoy = [(ind, pts[-1].get("dyoy")) for ind, pts in series.items()
            if pts and pts[-1].get("dyoy") is not None]
    accel = sorted([t for t in dyoy if t[1] > 0], key=lambda t: t[1], reverse=True)[:6]
    decel = sorted([t for t in dyoy if t[1] < 0], key=lambda t: t[1])[:6]
    deriv = ""
    if accel:
        deriv += ("<div class='ind-sbox ind-sbox-accel'><h3>가속 확대</h3>"
                  f"<div class='ind-chip-wrap'>"
                  f"{''.join(chip(n,v,'%p') for n,v in accel)}</div></div>")
    if decel:
        deriv += ("<div class='ind-sbox ind-sbox-decel'><h3>고성장 둔화/기울기 하락</h3>"
                  f"<div class='ind-chip-wrap'>"
                  f"{''.join(chip(n,v,'%p') for n,v in decel)}</div></div>")
    # C: import-surge box — 수입 YoY 급증 = 생산/투자 선행신호. Each surging
    # industry is annotated with its TOP DRIVER MTI subitem (어떤 세부품목
    # 수입이 끌었나, e.g. 반도체 ← 반도체제조용장비 +120%) computed from
    # the per-MTI import series. No extra API calls — same scan leaves.
    imp_box = ""
    if _import_series:
        # top import-YoY driver MTI per industry
        driver: dict[str, tuple[str, float]] = {}
        for mti6, mpts in _import_mti_series.items():
            if not mpts or mpts[-1].get("yoy") is None:
                continue
            ind = _import_mti_industry.get(mti6, "")
            nm = _import_mti_name.get(mti6, mti6)
            y = mpts[-1]["yoy"]
            cur = mpts[-1].get("exp") or 0
            # require a non-trivial import base so a tiny line doesn't 'drive'
            if cur < 10_000_000:
                continue
            if ind not in driver or y > driver[ind][1]:
                driver[ind] = (nm, y)

        imp_yoy = []
        for ind, ipts in _import_series.items():
            if ipts and ipts[-1].get("yoy") is not None:
                imp_yoy.append((ind, ipts[-1]["yoy"]))
        surges = sorted([t for t in imp_yoy if t[1] >= 20.0],
                        key=lambda t: t[1], reverse=True)[:8]
        if surges:
            rows_html = []
            for ind, v in surges:
                drv = driver.get(ind)
                sub = (f"<span class='ind-imp-drv'>← {_html.escape(drv[0])} "
                       f"{_pct(drv[1])}</span>" if drv else "")
                rows_html.append(
                    f"<div class='ind-imp-row'>{chip(ind, v)}{sub}</div>")
            imp_box = (
                "<div class='ind-sbox ind-sbox-imp'><h3>📥 수입 급증 (생산·투자 선행신호)</h3>"
                "<p class='ind-sbox-sub'>원자재·설비 수입 YoY 급증은 향후 생산/수출 확대의 "
                "선행지표일 수 있습니다. <b>← 표기</b>는 그 산업 수입을 가장 끌어올린 "
                "세부품목(MTI).</p>"
                f"<div class='ind-imp-list'>{''.join(rows_html)}</div></div>")
    return (f"<div class='ind-summary-grid'>{''.join(boxes)}</div>"
            f"<div class='ind-deriv-grid'>{deriv}</div>"
            f"<div class='ind-deriv-grid'>{imp_box}</div>")


# module-level handoff for the import series (set by render_industry_html
# before calling _summary_board — keeps _summary_board's signature stable
# while B/C grow what flows into it).
_import_series: dict[str, list[dict]] = {}
# per-MTI import series + lookups, for annotating each import-surge
# industry with its top driver subitem.
_import_mti_series: dict[str, list[dict]] = {}
_import_mti_industry: dict[str, str] = {}
_import_mti_name: dict[str, str] = {}


def render_subitem_html(by_mti: dict[str, dict],
                        rate_min_usd: int = 200_000_000) -> str:
    """하위품목 TOP (MTI6 단위) — one level below industry, ranked by
    MoM(전월대비, 최근 모멘텀) so 기저효과에 휘둘리지 않음:
    📈급등률(MoM% 양수 상위 30, 수출 ≥하한) + 💵급증액(전월대비 Δ$ 상위
    30, 수출 ≥하한). 두 표 모두 수출 하한 적용.
    Each row: 품목명(산업) · 수출 · 전월대비. Returns '' when no data."""
    if not by_mti:
        return ""
    rows = []
    pts_by_mti: dict[str, list[dict]] = {}
    for mti6, node in by_mti.items():
        months = node["months"]
        pts = industry_series({mti6: months}).get(mti6) or []
        if not pts:
            continue
        pts_by_mti[mti6] = pts
        latest = pts[-1]
        # MoM = 최신 확정월 vs 달력상 직전월. 직전월 포인트가 없으면(데이터
        # 공백) MoM 미정의 → 두 랭킹표에서 제외. 미래 미발표월은 애초에
        # 포인트로 생기지 않으므로 latest는 항상 최신 확정월.
        prev_exp = next((p["exp"] for p in pts
                         if p["ym"] == _prev_month(latest["ym"])), None)
        mom = (((latest["exp"] - prev_exp) / prev_exp * 100.0)
               if (prev_exp and prev_exp > 0) else None)
        mom_delta = (latest["exp"] - prev_exp) if prev_exp is not None else None
        rows.append({
            "mti6": mti6,
            "name": node["name"], "industry": node["industry"],
            "exp": latest["exp"], "mom": mom, "mom_delta": mom_delta,
        })
    if not rows:
        return ""

    def chip_rows(items, metric):
        out = []
        for r in items:
            raw = r["mom"] if metric == "mom" else r["mom_delta"]
            val = _pct(raw) if metric == "mom" else "Δ" + _eokusd(raw)
            cl = "pos" if (raw or 0) > 0 else "neg"
            out.append(
                f"<tr><td>{_html.escape(r['name'])}</td>"
                f"<td class='ind-sub-ind'>{_html.escape(r['industry'])}</td>"
                f"<td>{_eokusd(r['exp'])}</td>"
                f"<td class='{cl}'>{val}</td></tr>")
        return "".join(out)

    # 급등률: 수출 ≥ 하한 + 전월대비(MoM) 양수 중 MoM% 내림차순 상위 30.
    # 고정 %컷(예전 +30%)은 두지 않음 — MoM에선 +30%가 과도하게 빡빡해
    # 표가 거의 비어버려서. '가장 빨리 오른 것 순'으로 항상 채움.
    rate = sorted([r for r in rows
                   if r["mom"] is not None and r["mom"] > 0
                   and (r["exp"] or 0) >= rate_min_usd],
                  key=lambda r: r["mom"], reverse=True)[:30]
    # 급증액: 수출 ≥ 하한(급등률과 동일) + 전월대비 Δ$ 내림차순 상위 30.
    # 소액 기저 품목이 큰 변화율로 끼는 것을 막기 위해 같은 하한 적용.
    amount = sorted([r for r in rows
                     if r["mom_delta"] is not None
                     and (r["exp"] or 0) >= rate_min_usd],
                    key=lambda r: r["mom_delta"], reverse=True)[:30]

    def tbl(title, items, metric):
        if not items:
            return ""
        th = "전월대비" if metric == "mom" else "증감액"
        return (f"<details class='ind-sub-card' open><summary>{title} ({len(items)})</summary>"
                f"<div class='ind-raw-scroll'><table class='ind-table'><thead>"
                f"<tr><th>품목(MTI)</th><th>산업</th><th>수출</th><th>{th}</th></tr></thead>"
                f"<tbody>{chip_rows(items, metric)}</tbody></table></div></details>")

    # TOP 10 풀 카드 (수출액 큰 순) — 산업 카드와 완전히 동일한 형식
    # (_card_body 재사용: 차트+TTM 토글+8지표+해석문+원자료표). 이름은
    # '품목 (산업)'으로 출처를 보임.
    top10 = sorted(rows, key=lambda r: r["exp"], reverse=True)[:10]
    cards = []
    for r in top10:
        pts = pts_by_mti[r["mti6"]]
        cls = {"초고성장/강세": "hot", "턴어라운드 후보": "turn",
               "부진/재하락": "down"}.get(classify(pts), "na")
        label = classify(pts)
        title = f"{r['name']} <small class='ind-sub-ind'>({r['industry']})</small>"
        cards.append(
            "<section class='ind-card'>"
            f"<div class='ind-head'><h3>{title}</h3>"
            f"<span class='ind-badge ind-badge-{cls}'>{label}</span></div>"
            + _card_body(pts) + "</section>"
        )
    cards_html = ("<div class='ind-cards'>" + "".join(cards) + "</div>") if cards else ""

    return (
        "<h2 class='ind-group ind-group-hot'>하위품목 (MTI 세분)</h2>"
        "<div class='ind-sub-note'>20개 산업 아래 세부 품목(D램·낸드·웨이퍼 등 "
        "MTI 6자리). 수출액 TOP 10은 풀 카드로, 전체는 급등률·증감액 랭킹표로 — "
        "산업이 가려버리는 '산업 안의 스타 품목'을 발굴.</div>"
        + cards_html
        + "<div class='ind-sub-wrap'>"
        + tbl("📈 급등률 (MoM↑ 상위, 수출 ≥" + _eokusd(rate_min_usd) + ")", rate, "mom")
        + tbl("💵 급증액 (전월대비, 수출 ≥" + _eokusd(rate_min_usd) + ")", amount, "amount")
        + "</div>"
    )


def render_industry_html(by_industry: dict[str, dict[str, int]],
                         by_import: dict[str, dict[str, int]] | None = None,
                         by_mti: dict[str, dict] | None = None,
                         by_mti_import: dict[str, dict] | None = None) -> str:
    """Full 산업트렌드 panel: summary board (+ 수입 급증 신호 with per-MTI
    driver) + cards grouped by classification. Returns '' when no data."""
    global _import_series, _import_mti_series, _import_mti_industry, _import_mti_name
    _import_series = industry_series(by_import) if by_import else {}
    # per-MTI import series for the 수입 급증 driver annotation
    _import_mti_series, _import_mti_industry, _import_mti_name = {}, {}, {}
    if by_mti_import:
        for mti6, node in by_mti_import.items():
            s = industry_series({mti6: node["months"]}).get(mti6)
            if s:
                _import_mti_series[mti6] = s
                _import_mti_industry[mti6] = node.get("industry", "")
                _import_mti_name[mti6] = node.get("name", mti6)
    if not by_industry:
        return ""
    series = industry_series(by_industry)
    if not series:
        return ""
    # bucket industries by classification, each sorted by latest export desc
    buckets: dict[str, list[tuple[str, list[dict]]]] = {g: [] for g, _ in _GROUPS}
    for ind, pts in series.items():
        if not pts:
            continue
        buckets.setdefault(classify(pts), []).append((ind, pts))
    for g in buckets:
        buckets[g].sort(key=lambda t: t[1][-1]["exp"], reverse=True)

    out = []
    latest_ym = ""
    for _, pts in series.items():
        if pts and pts[-1]["ym"] > latest_ym:
            latest_ym = pts[-1]["ym"]
    # Label spells out WHY the latest month lags ~1 month: this API is
    # 관세청 CONFIRMED-only, published ~the 15th of the following month, so
    # early in any month the freshest confirmed data is the month before
    # last. Daily 4× polling reflects a new confirmed month within hours of
    # its ~15th publication.
    # TODO(잠정치): 산업부 수출입동향 보도자료(매월 1일, 전월 잠정치)를 별도
    # 소스로 붙이면 최신월을 한 달 앞당길 수 있음.
    # 조사결론(2026-06): data.go.kr엔 접근 가능한 산업분류 잠정 OpenAPI가
    # 없음 — 확정됨. 202605 조회는 code 00 정상이나 0건, nitemtrade도 0건,
    # 모든 관세청 OpenAPI가 GW(확정치)뿐. 잠정치는 산업부 보도자료(PDF/HWP)
    # 비정형 소스에만 존재 → 별도 파서/수집 설계 필요. 보강 마무리 후 작업.
    out.append(
        "<div class='ind-topbar'>"
        f"<div class='ind-note'>산업분류별 월 수출액 · YoY/ΔYoY/12M 이동평균 "
        f"(HSK-MTI 연계표 기준) · 최신 <b>{_html.escape(latest_ym)}</b> "
        f"관세청 확정치(익월 ~15일 발표·매일 갱신)</div>"
        "<div class='ind-legend'><span><i class='ind-lg-v'></i>수출액</span>"
        "<span><i class='ind-lg-m'></i>12M MA</span></div>"
        "</div>"
    )
    # A: summary board (분류·미분 칩 보드) — mirrors reference header
    out.append(_summary_board(series))
    for label, cls in _GROUPS:
        items = buckets.get(label) or []
        if not items:
            continue
        out.append(f"<h2 class='ind-group ind-group-{cls}'>{label} ({len(items)})</h2>")
        out.append("<div class='ind-cards'>")
        for ind, pts in items:
            out.append(
                "<section class='ind-card'>"
                f"<div class='ind-head'><h3>{_html.escape(ind)}</h3>"
                f"<span class='ind-badge ind-badge-{cls}'>{label}</span></div>"
                + _card_body(pts)
                + "</section>"
            )
        out.append("</div>")
    # B: 하위품목 TOP (MTI6) — appended below the industry cards
    if by_mti:
        out.append(render_subitem_html(by_mti))
    return "".join(out)

