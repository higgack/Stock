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
) -> dict[str, dict[str, int]]:
    """{industry: {ym: exp_dlr_total}} summing each industry's member
    HS leaves per month. Unmapped / '기타' leaves are excluded.

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
            bucket[ym] = bucket.get(ym, 0) + (fig.get("exp_dlr") or 0)
    return out


def _prev_year_month(ym: str) -> str:
    """'2026-04' → '2025-04'. Accepts 'YYYY-MM' or 'YYYYMM'."""
    s = ym.replace("-", "")
    y, m = int(s[:4]), int(s[4:6])
    return f"{y - 1:04d}-{m:02d}"


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
        for k in keys:
            exp = norm[k] or 0
            exp_running.append(exp)
            ago = norm.get(_prev_year_month(k))
            yoy = ((exp - ago) / ago * 100.0) if ago else None
            dyoy = (yoy - prev_yoy) if (yoy is not None and prev_yoy is not None) else None
            points.append({
                "ym": k,
                "exp": exp,
                "yoy": yoy,
                "dyoy": dyoy,
                "ma12": _ma(exp_running, 12),
            })
            if yoy is not None:
                prev_yoy = yoy
        out[industry] = points
    return out


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
    """USD → '억 달러' label matching the reference (e.g. 11.18B → '112억').
    1억 USD = 1e8. Keeps one decimal under 1000억."""
    if n is None:
        return "—"
    eok = n / 1e8
    if abs(eok) >= 1000:
        return f"{eok/10000:.2f}조억"  # extremely rare; keep sane
    if abs(eok) >= 100:
        return f"{eok:.0f}억"
    return f"{eok:.1f}억"


def _pct(p, suffix="%") -> str:
    return f"{p:+.1f}{suffix}" if p is not None else "—"


def _svg_chart(points: list[dict]) -> str:
    """Line chart: export value (solid) + 12M MA (dashed) over the series.
    Scales y to [min,max] of exports. Returns an <svg> string."""
    pts = [p for p in points if p.get("exp") is not None]
    if len(pts) < 2:
        return ""
    exps = [p["exp"] for p in pts]
    mas = [p["ma12"] for p in pts if p.get("ma12") is not None]
    lo = min(exps + mas) if mas else min(exps)
    hi = max(exps + mas) if mas else max(exps)
    span = (hi - lo) or 1
    n = len(pts)
    plot_w = _VW - _PAD_L - _PAD_R
    plot_h = _VH - _PAD_T - _PAD_B

    def x(i):
        return _PAD_L + (plot_w * i / (n - 1)) if n > 1 else _PAD_L
    def y(v):
        return _PAD_T + plot_h * (1 - (v - lo) / span)

    # grid (3 horizontal lines)
    grid = "".join(
        f'<line x1="{_PAD_L}" y1="{_PAD_T + plot_h*f:.1f}" '
        f'x2="{_VW-_PAD_R}" y2="{_PAD_T + plot_h*f:.1f}" class="ind-grid"/>'
        for f in (0.0, 0.5, 1.0)
    )
    # value polyline
    vpts = " ".join(f"{x(i):.1f},{y(p['exp']):.1f}" for i, p in enumerate(pts))
    value_line = f'<polyline points="{vpts}" class="ind-value-line"/>'
    # MA polyline (only where ma12 defined; contiguous from first non-None)
    ma_seq = [(i, p["ma12"]) for i, p in enumerate(pts) if p.get("ma12") is not None]
    ma_line = ""
    if len(ma_seq) >= 2:
        mpts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in ma_seq)
        ma_line = f'<polyline points="{mpts}" class="ind-ma-line"/>'
    # latest dot
    last = pts[-1]
    dot = (f'<circle cx="{x(n-1):.1f}" cy="{y(last["exp"]):.1f}" r="3.2" '
           f'class="ind-latest-dot"/>')
    # tooltips (title per point) — hover shows month/value/YoY
    titles = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(p["exp"]):.1f}" r="3.5" '
        f'fill="transparent"><title>{_html.escape(p["ym"])} | '
        f'{_eokusd(p["exp"])} | YoY {_pct(p["yoy"])}</title></circle>'
        for i, p in enumerate(pts)
    )
    return (
        f'<svg viewBox="0 0 {_VW} {_VH}" role="img" '
        f'aria-label="수출액 추이와 12개월 이동평균" class="ind-chart">'
        f'{grid}{value_line}{ma_line}{dot}{titles}</svg>'
    )


def _stat_row(points: list[dict]) -> str:
    """The <dl> stat block: 최신월·수출액·YoY·ΔYoY·12M MA·MA대비."""
    latest = points[-1]
    exp, ma = latest["exp"], latest.get("ma12")
    ma_rel = ((exp - ma) / ma * 100.0) if ma else None
    yoy_cls = "pos" if (latest.get("yoy") or 0) > 0 else "neg"
    dyoy_cls = "pos" if (latest.get("dyoy") or 0) > 0 else "neg"
    return (
        "<dl class='ind-stats'>"
        f"<div><dt>최신월</dt><dd>{_html.escape(latest['ym'])}</dd></div>"
        f"<div><dt>수출액</dt><dd>{_eokusd(exp)}</dd></div>"
        f"<div><dt>YoY</dt><dd class='{yoy_cls}'>{_pct(latest.get('yoy'))}</dd></div>"
        f"<div><dt>ΔYoY</dt><dd class='{dyoy_cls}'>{_pct(latest.get('dyoy'),'%p')}</dd></div>"
        f"<div><dt>12M MA</dt><dd>{_eokusd(ma)}</dd></div>"
        f"<div><dt>MA대비</dt><dd>{_pct(ma_rel)}</dd></div>"
        "</dl>"
    )


def init_db(conn) -> None:
    """One-row-per-industry store of the aggregated monthly series. The
    24-month chapter fetch is too heavy for render time, so the scan job
    precomputes & stores; the dashboard reads this."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS industry_series ("
        "industry TEXT PRIMARY KEY, months_json TEXT NOT NULL, "
        "updated_ts REAL)"
    )
    conn.commit()


def store(conn, by_industry: dict[str, dict[str, int]], now: float | None = None) -> int:
    """Replace the stored series with the latest aggregation. Returns rows
    written. Empty input is ignored (never wipes a good snapshot)."""
    import time
    if not by_industry:
        return 0
    now = now if now is not None else time.time()
    init_db(conn)
    conn.execute("DELETE FROM industry_series")
    for ind, months in by_industry.items():
        conn.execute(
            "INSERT INTO industry_series (industry, months_json, updated_ts) "
            "VALUES (?,?,?)",
            (ind, _json.dumps(months, ensure_ascii=False), now),
        )
    conn.commit()
    return len(by_industry)


def load_stored(conn) -> dict[str, dict[str, int]]:
    """{industry: {ym: exp}} from the store, or {} when the table is
    absent/empty."""
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


def render_industry_html(by_industry: dict[str, dict[str, int]]) -> str:
    """Full 산업트렌드 panel: cards grouped by classification. Returns ''
    when there's no data (so the dashboard omits the tab body)."""
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
    out.append(
        f"<div class='ind-note'>산업분류별 월 수출액 · YoY/ΔYoY/12M 이동평균 "
        f"(HSK-MTI 연계표 기준, 최신 {_html.escape(latest_ym)}) · "
        f"관세청 확정치</div>"
    )
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
                + _stat_row(pts)
                + _svg_chart(pts)
                + "</section>"
            )
        out.append("</div>")
    return "".join(out)

