"""Phase-1 verification CLI: print per-industry monthly export from the
관세청 chapter sweep, so the operator can sanity-check the figures
against 산업부 수출동향 BEFORE the dashboard tab is built.

Reuses customs_scan.fetch_chapter (same data the surge scan pulls) — no
new API surface. Read-only: writes nothing, sends nothing.

    .venv/bin/python -m trade.scripts.industry_report               # latest month, all industries
    .venv/bin/python -m trade.scripts.industry_report --industry 반도체   # one industry's full series
    .venv/bin/python -m trade.scripts.industry_report --lookback-months 24
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from trade import customs, customs_scan, industry, mti_map

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("industry-report")


def _fmt(n) -> str:
    return customs.fmt_usd(n)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--industry", default=None,
                    help="show one industry's full monthly series")
    ap.add_argument("--lookback-months", type=int, default=24,
                    help="window (YoY needs ≥13 months; 24 = 1 full YoY series)")
    ap.add_argument("--max-chapters", type=int, default=len(customs_scan.CHAPTERS))
    ap.add_argument("--store", action="store_true",
                    help="persist the aggregated series to customs.db for the dashboard")
    ap.add_argument("--min-coverage", type=float, default=0.9,
                    help="min chapter success fraction before --store overwrites")
    args = ap.parse_args(argv)

    key = os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        log.warning("TRADE_DATA_GO_KR_KEY not set — skip")
        return 0
    try:
        mti_map.load()
    except mti_map.HskMtiFileMissing as exc:
        log.error("HSK-MTI linkage missing: %s", exc)
        return 1

    # rolling window ending this month
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    back = args.lookback_months - 1
    while back > 0:
        m -= 1
        if m == 0:
            m = 12; y -= 1
        back -= 1
    start, end = f"{y:04d}{m:02d}", now.strftime("%Y%m")

    # 관세청 API caps a single query at 1 year; fetch_chapter_range splits
    # the 24-month YoY window into ≤12-month sub-windows automatically.
    all_rows: list[dict] = []
    ok = fail = 0
    for ch in customs_scan.CHAPTERS[: args.max_chapters]:
        try:
            all_rows.extend(customs_scan.fetch_chapter_range(ch, start, end, key=key))
            ok += 1
        except Exception as exc:
            fail += 1
            log.warning("chapter %s failed: %s", ch, exc)
    log.info("scan: chapters ok=%d fail=%d rows=%d window=%s→%s",
             ok, fail, len(all_rows), start, end)
    if ok == 0:
        return 1

    leaves = customs_scan.build_series(all_rows)
    by_ind = industry.aggregate_by_industry(leaves)
    series = industry.industry_series(by_ind)

    # Coverage guard (mirror scan_customs): a partial scan must not store a
    # holey snapshot over a good one.
    coverage = ok / (ok + fail) if (ok + fail) else 0.0
    if args.store:
        if coverage < args.min_coverage:
            log.warning("coverage %.0f%% < %.0f%% — not storing (partial scan)",
                        coverage * 100, args.min_coverage * 100)
        else:
            with customs.session() as conn:
                n = industry.store(conn, by_ind)
            log.info("stored %d industries to customs.db", n)

    if args.industry:
        pts = series.get(args.industry)
        if not pts:
            print(f"산업 '{args.industry}' 없음. 가능한 산업: "
                  f"{', '.join(mti_map.industries())}")
            return 1
        print(f"\n=== {args.industry} 월별 수출 (분류: {industry.classify(pts)}) ===")
        print(f"{'월':>8} {'수출액':>12} {'YoY':>9} {'ΔYoY':>9} {'12M MA':>12}")
        for p in pts:
            yoy = f"{p['yoy']:+.1f}%" if p['yoy'] is not None else "—"
            dyoy = f"{p['dyoy']:+.1f}%p" if p['dyoy'] is not None else "—"
            ma = _fmt(p['ma12']) if p['ma12'] is not None else "—"
            print(f"{p['ym']:>8} {_fmt(p['exp']):>12} {yoy:>9} {dyoy:>9} {ma:>12}")
        return 0

    # all industries — latest month snapshot, grouped by classification
    print(f"\n=== 산업별 최신월 수출 ({len(series)}개 산업) ===")
    rows = []
    for ind, pts in series.items():
        if not pts:
            continue
        latest = pts[-1]
        rows.append((ind, latest, industry.classify(pts)))
    # sort by latest export desc
    rows.sort(key=lambda r: r[1]["exp"], reverse=True)
    groups = {"초고성장/강세": [], "턴어라운드 후보": [], "부진/재하락": [], "데이터부족": []}
    for ind, latest, cls in rows:
        groups.setdefault(cls, []).append((ind, latest))
    for cls in ["초고성장/강세", "턴어라운드 후보", "부진/재하락", "데이터부족"]:
        if not groups[cls]:
            continue
        print(f"\n[{cls}]")
        for ind, latest in groups[cls]:
            yoy = f"{latest['yoy']:+.1f}%" if latest['yoy'] is not None else "—"
            dyoy = f"{latest['dyoy']:+.1f}%p" if latest['dyoy'] is not None else "—"
            print(f"  {ind:<10} {latest['ym']}  수출 {_fmt(latest['exp']):>10}"
                  f"  YoY {yoy:>8}  ΔYoY {dyoy:>9}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
