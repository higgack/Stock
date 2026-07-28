"""Fetch 관세청 monthly figures for operator-pinned items → local cache.

Walks every 품목→HS pin in trade/hs_map.py, pulls a rolling
LOOKBACK_MONTHS window from the 관세청 품목별 수출입실적 API (dataset
15101609), aggregates each pin to its chosen HS granularity, and upserts
into ~/.trade/customs.db.

SILENT by design — this script sends NO Telegram messages, ever. It runs
daily and on a quiet day (no new month, or data unchanged) it only writes
journal lines. The ONLY Telegram output in the whole customs feature is
the ±30% change alert, which lives in a SEPARATE script (added later),
goes to operator DM, stays baseline-silent on first run, and is capped.
So a daily poll can never spam the channel.

Idempotent: re-running re-fetches the same rolling window and refreshes
existing months in place (only a genuinely new month is recorded as
'new', which the later alert layer keys on). Safe to run any number of
times — a missed or duplicated tick costs nothing.

Failure handling (also silent — journal only):
  - TRADE_DATA_GO_KR_KEY unset  → log + exit 0 (operator hasn't wired the
    key yet; not an error worth a 3am alert)
  - no pins                     → log + exit 0
  - per-item API failure        → log + continue with the rest

Schedule: trade-bot-customs-fetch.timer (daily). Data is monthly and
refreshes ~the 15th, so a daily idempotent poll reflects the new
confirmed month within a day of publication.

Run by hand:
    .venv/bin/python -m trade.scripts.fetch_customs
    .venv/bin/python -m trade.scripts.fetch_customs --lookback-months 24
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import customs, hs_map

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("fetch-customs")

# now-앵커 윈도라 최소 lookback = customs.YOY_LOOKBACK_MONTHS(=16, 단일 소스).
# 과거 "12 months → enough for YoY" 는 오판이었다(2026-07-01): 최신 확정월이
# 매월 1~15일엔 now-2 라 그 전년동월(now-14)이 12개월 윈도(now-11..now) 밖 →
# pin 종목 YoY 가 '신규(전기0)' (히트맵 무색과 동일 클래스). 16이면 커버. pure
# cache fill·무알림이라 넓혀도 blast-radius 없음. env 로 override 가능.
LOOKBACK_MONTHS_DEFAULT = int(
    os.environ.get("TRADE_CUSTOMS_LOOKBACK_MONTHS")
    or str(customs.YOY_LOOKBACK_MONTHS)
)


def _yymm(dt: datetime) -> str:
    return f"{dt.year:04d}{dt.month:02d}"


def _window(lookback_months: int, now: datetime | None = None) -> tuple[str, str]:
    """Return (start_yymm, end_yymm) for a rolling window ending this
    month and spanning `lookback_months` months inclusive."""
    now = now or datetime.now(timezone.utc)
    end = now
    # Walk back lookback_months-1 whole months from the current month.
    y, m = now.year, now.month
    back = lookback_months - 1
    m -= back
    while m <= 0:
        m += 12
        y -= 1
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    return _yymm(start), _yymm(end)


def run(lookback_months: int) -> int:
    key = os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        log.info("TRADE_DATA_GO_KR_KEY not set — skipping (silent, no alert)")
        return 0

    pins = hs_map.entries()
    if not pins:
        log.info("no HS pins in hs_map — nothing to fetch")
        return 0

    start_yymm, end_yymm = _window(lookback_months)
    log.info(
        "fetching %d pinned item(s), window %s→%s",
        len(pins), start_yymm, end_yymm,
    )

    fetched_items = 0
    new_months_total = 0
    failed = 0
    with customs.session() as conn:
        for item, hs_code in pins:
            try:
                rows = customs.fetch(hs_code, start_yymm, end_yymm, key=key)
            except Exception as exc:
                failed += 1
                log.warning(
                    "fetch failed for %r (HS %s): %s — continuing",
                    item, hs_code, exc,
                )
                continue
            by_month = customs.aggregate_by_month(rows, hs_code)
            if not by_month:
                log.info("no rows for %r (HS %s) in window", item, hs_code)
                continue
            new_here = 0
            for ym, agg in sorted(by_month.items()):
                if customs.upsert_month(conn, hs_code, ym, agg):
                    new_here += 1
            fetched_items += 1
            new_months_total += new_here
            log.info(
                "%r (HS %s): %d month(s) cached, %d new",
                item, hs_code, len(by_month), new_here,
            )

    log.info(
        "done: items_ok=%d failed=%d new_months=%d (silent — no Telegram)",
        fetched_items, failed, new_months_total,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch 관세청 monthly figures for pinned items into customs.db (silent)."
    )
    ap.add_argument(
        "--lookback-months",
        type=int,
        default=LOOKBACK_MONTHS_DEFAULT,
        metavar="N",
        help=(
            f"Rolling window size (default: {LOOKBACK_MONTHS_DEFAULT}, "
            "or TRADE_CUSTOMS_LOOKBACK_MONTHS). Pure cache fill — no alerts."
        ),
    )
    args = ap.parse_args()
    return run(args.lookback_months)


if __name__ == "__main__":
    sys.exit(main())
