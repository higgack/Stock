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

# Scan compares the latest two CONFIRMED months. 관세청 confirms a month
# ~the 15th of the following month, so early in any month the freshest
# confirmed data is ~2 months back (on 2026-06-01 the newest was 4월, and
# 5·6월 had no rows at all). A 3-month window then yields only ONE real
# month → no comparison → empty ranking (the bug that wiped live on
# 6/1). 6 months guarantees ≥2 confirmed months year-round with slack.
# Decoupled from fetch_customs' 12-month history window via its OWN env
# var.
# 13개월 확장 (2026-06-12, 히트맵 YoY 색용 — 사용자 승인): leaf 별 작년
# 동월 필요. 비용 실측 기준 — 6개월 윈도 실사용 ~1,512콜/일(대시보드
# 헤더 실측; 옛 '1,200콜/스캔' 추정은 과대) × ~2.2배 ≈ 3,300콜/일
# « 10,000 무료 한도. 페이지수는 월수에 비례.
LOOKBACK_MONTHS_DEFAULT = int(
    os.environ.get("TRADE_CUSTOMS_SCAN_LOOKBACK_MONTHS") or "13"
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
    """Reuse customs_alert's sender + the recorded operator chat.

    operator.get() returns the single recorded operator chat_id (or None
    before the operator has DM'd the bot). The earlier all_ids() call did
    not exist and crashed _send_alert with AttributeError whenever a scan
    produced new entrants (seen 2026-06-01 17:59). Mirror customs_alert,
    which also targets operator.get()."""
    from trade import operator
    from trade.scripts import customs_alert
    chat = operator.get()
    if not chat:
        log.info("no operator chat recorded — skipping surge alert")
        return False
    return customs_alert._send(chat, body)


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
    parser.add_argument(
        "--min-coverage", type=float,
        default=float(os.environ.get("TRADE_CUSTOMS_SCAN_MIN_COVERAGE") or "0.9"),
        help="min fraction of chapters that must succeed before the scan "
             "may overwrite the live snapshot (default 0.9; a partial scan "
             "below this keeps the last good snapshot)")
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
            # fetch_chapter_range — 13개월 윈도를 ≤12개월로 쪼개 호출.
            # 관세청이 1년 초과 조회를 resultCode 99 로 전부 거부해
            # ok=0 fail=97 rows=0 (히트맵 영구 empty) 였던 근본 원인:
            # splitter(_split_windows)는 구현·테스트돼 있었는데 이
            # 호출부만 raw fetch_chapter 를 쓰던 배선 누락 (2026-06-12
            # scan_customs_kick.log 로 적발).
            all_rows.extend(
                customs_scan.fetch_chapter_range(ch, start, end, key=key))
            ok += 1
        except Exception as exc:
            fail += 1
            log.warning("chapter %s failed: %s", ch, exc)
    log.info("scan: chapters ok=%d fail=%d rows=%d", ok, fail, len(all_rows))
    if ok == 0:
        return 1
    coverage = ok / (ok + fail) if (ok + fail) else 0.0

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

    # Coverage guard: a partial scan (many chapters failed — API outage or
    # FloodWait storm) ranks only the chapters that DID respond, which then
    # overwrites a complete prior snapshot, dropping every item from the
    # missing chapters. Observed 2026-06-01 17:59: ok=9 fail=88 → live went
    # 24→4 items (only ch 90/93 survived) AND mis-fired 27 'new entrant'
    # alerts. Below the coverage floor, keep the last good snapshot.
    if coverage < args.min_coverage:
        log.warning(
            "chapter coverage %.0f%% (ok=%d fail=%d) < %.0f%% floor — "
            "partial scan, keeping previous live snapshot (no store/alert)",
            coverage * 100, ok, fail, args.min_coverage * 100,
        )
        return 0

    empty = not (ranked[customs_scan.SECTION_RATE]
                 or ranked[customs_scan.SECTION_AMOUNT])
    with customs.session(db) as conn:
        customs_scan.init_db(conn)
        if empty:
            # Defensive: an all-empty ranking (e.g. a transient API hiccup,
            # or early in a month before any month has confirmed figures)
            # must NOT wipe the last good live snapshot. Skip store_live so
            # the panel keeps showing the most recent real ranking until a
            # non-empty scan replaces it. Archive/alerts also no-op here.
            log.warning("ranking empty (rate=0 amount=0) — keeping previous "
                        "live snapshot, skipping store/archive")
            return 0
        customs_scan.store_live(conn, ranked)
        archived = customs_scan.upsert_archive(conn, ranked)
        new_entrants = customs_scan.eval_new_entrants(conn, ranked)
        # 히트맵 leaf 스냅샷 (2026-06-12) — 같은 스윕 데이터 재사용(API 0).
        # ranking 비어있지 않은(=커버리지 양호) 경로에서만 교체 저장.
        hm_rows = customs_scan.heatmap_rows(leaves)
        customs_scan.store_heatmap(conn, hm_rows)
    log.info("stored live; archived=%d new_entrants=%d heatmap=%d",
             archived, len(new_entrants), len(hm_rows))

    if new_entrants:
        body = customs_scan.format_alert(new_entrants)
        _send_alert(body)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
