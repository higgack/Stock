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
