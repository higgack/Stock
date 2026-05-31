"""전 chapter 관세청 급변 스캔 — daily auto-discovery entrypoint.

Sweeps HS chapters 01~97, ranks two views (📈 급등률 / 💵 급증액), refreshes
the live snapshot, appends to the permanent archive, and DMs the operator
the items NEW to this run (baseline-silent on first run). Replaces the
pin-only fetch as the panel's data source.

Modes:
  --dry-run     scan + print a value-floor histogram; NO DB writes, NO
                alerts. Use once to see how many surges exist before
                trusting the live feed.
  (default)     scan → store_live (replace) → upsert_archive (forever) →
                DM new entrants. Idempotent: re-running the same month
                re-confirms the same snapshot without duplicate alerts.

One-time migration: on first successful real run, the legacy pin file
(~/.trade/hs_map.tsv) is backed up to hs_map.bak.<ts>.tsv and cleared —
the operator asked for a fresh start where the panel is surge-driven.
Guarded by a marker (~/.trade/.surge_migrated) so it happens exactly
once; manual /hs pins added later are untouched. Skip with
--keep-pins.

Exit codes: 0 success / nothing to do; 1 every chapter fetch failed.

Schedule: trade-bot-customs-fetch.timer (daily), chained after the
pinned fetch_customs so manual pins still cache too.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import customs, customs_scan, hs_map

# Load host .env so a manual run (e.g. --dry-run from a shell) sees
# TRADE_DATA_GO_KR_KEY. systemd injects it via EnvironmentFile, but the
# sibling scripts (fetch_customs / customs_alert) load_dotenv too so an
# operator can run any of them by hand; this one was missing it.
load_dotenv()

log = logging.getLogger("scan-customs")

# Scan only needs the latest-vs-previous month (customs_scan._latest_move
# looks at the last 2 months), so a short window is enough — 3 gives one
# month of slack. Decoupled from fetch_customs' 12-month window (which
# feeds the pin panel's history) via its OWN env var so tuning one never
# silently widens the other. A 3-month scan is ~1/4 the API calls of 12,
# which is what makes a 4×/day cadence fit under the daily quota.
LOOKBACK_MONTHS_DEFAULT = int(
    os.environ.get("TRADE_CUSTOMS_SCAN_LOOKBACK_MONTHS") or "3"
)
_MIGRATE_MARKER = Path.home() / ".trade" / ".surge_migrated"


def _window(lookback_months: int, now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    end = now.strftime("%Y%m")
    y, m = now.year, now.month
    back = lookback_months - 1
    while back > 0:
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        back -= 1
    return f"{y:04d}{m:02d}", end


def _reset_pins_once() -> str | None:
    """Back up + clear the legacy pin file exactly once. Returns a human
    note for the deploy/alert log, or None if nothing to do / already
    migrated."""
    if _MIGRATE_MARKER.exists():
        return None
    entries = hs_map.entries()
    _MIGRATE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    if entries:
        src = Path(
            os.environ.get("TRADE_HS_MAP_PATH")
            or str(Path.home() / ".trade" / "hs_map.tsv")
        )
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = src.with_name(f"hs_map.bak.{ts}.tsv")
        try:
            bak.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("pin backup failed (%s) — not clearing", exc)
            return None
        for item, _code in list(entries):
            hs_map.remove(item)
        _MIGRATE_MARKER.write_text(str(time.time()))
        return f"기존 핀 {len(entries)}개 백업({bak.name}) 후 초기화"
    _MIGRATE_MARKER.write_text(str(time.time()))
    return None


def _send_alert(body: str) -> bool:
    """Reuse customs_alert's sender + operator recipients."""
    from trade import operator
    from trade.scripts import customs_alert
    sent = False
    for cid in operator.all_ids():
        if customs_alert._send(cid, body):
            sent = True
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-pins", action="store_true",
                        help="skip the one-time legacy-pin reset")
    parser.add_argument("--top-n", type=int, default=customs_scan.TOP_N)
    parser.add_argument("--pct", type=float, default=customs_scan.PCT_THRESHOLD)
    parser.add_argument("--lookback-months", type=int, default=LOOKBACK_MONTHS_DEFAULT)
    parser.add_argument("--db", default=None)
    parser.add_argument("--max-chapters", type=int, default=len(customs_scan.CHAPTERS),
                        help="limit chapters scanned (testing/throttling)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    key = os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        log.warning("TRADE_DATA_GO_KR_KEY not set — skip")
        return 0

    start, end = _window(args.lookback_months)
    chapters = customs_scan.CHAPTERS[: args.max_chapters]

    all_rows: list[dict] = []
    ok = fail = 0
    for ch in chapters:
        try:
            all_rows.extend(customs_scan.fetch_chapter(ch, start, end, key=key))
            ok += 1
        except Exception as exc:
            fail += 1
            log.warning("chapter %s failed: %s", ch, exc)
    log.info("scan: chapters ok=%d fail=%d rows=%d", ok, fail, len(all_rows))
    if ok == 0:
        return 1

    leaves = customs_scan.build_series(all_rows)
    ranked = customs_scan.rank(leaves, top_n=args.top_n, pct_threshold=args.pct)
    log.info("ranked: rate=%d amount=%d (leaves=%d)",
             len(ranked[customs_scan.SECTION_RATE]),
             len(ranked[customs_scan.SECTION_AMOUNT]), len(leaves))

    if args.dry_run:
        hist = customs_scan.floor_histogram(leaves, pct_threshold=args.pct)
        print(f"[dry-run] leaves={len(leaves)} "
              f"rate(+{args.pct:.0f}%, ≥{customs.fmt_usd(customs_scan.RATE_MIN_USD)})"
              f"={len(ranked[customs_scan.SECTION_RATE])} "
              f"amount={len(ranked[customs_scan.SECTION_AMOUNT])}")
        print("[dry-run] +%.0f%% surges surviving each export floor:" % args.pct)
        for floor, cnt in sorted(hist.items()):
            print(f"  ≥ {customs.fmt_usd(floor)}: {cnt}건")

        # Preview the ACTUAL top items that would be registered this run —
        # dry-run writes nothing, but this shows what the live panel would
        # contain so the operator can sanity-check before enabling.
        def _preview(title: str, rows: list[dict], metric: str) -> None:
            print(f"\n[dry-run] {title} — 실제 등록 예정 (상위 {len(rows)}):")
            if not rows:
                print("  (해당 없음)")
                return
            for i, m in enumerate(rows, 1):
                move = (
                    customs.fmt_pct(m["pct"]) if metric == "pct"
                    else "Δ" + customs.fmt_usd(m["delta"])
                )
                print(
                    f"  {i:2d}. {m['name']} ({m['hs_code']}) "
                    f"{customs.fmt_usd(m['prev'])}→{customs.fmt_usd(m['curr'])} "
                    f"[{move}]"
                )

        _preview("📈 급등률 TOP", ranked[customs_scan.SECTION_RATE], "pct")
        _preview("💵 급증액 TOP", ranked[customs_scan.SECTION_AMOUNT], "amount")
        return 0

    db = Path(args.db) if args.db else customs.DEFAULT_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    note = None
    if not args.keep_pins:
        note = _reset_pins_once()
        if note:
            log.info("migration: %s", note)

    with customs.session(db) as conn:
        customs_scan.init_db(conn)
        customs_scan.store_live(conn, ranked)
        archived = customs_scan.upsert_archive(conn, ranked)
        new_entrants = customs_scan.eval_new_entrants(conn, ranked)
    log.info("stored live; archived=%d new_entrants=%d", archived, len(new_entrants))

    if new_entrants:
        body = customs_scan.format_alert(new_entrants)
        _send_alert(body)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
