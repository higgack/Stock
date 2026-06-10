"""Read-only diagnostic for the beon-sync dedup mismatch.

Iterates the last N days of BeOn messages via Telethon and, for each
message, prints:
  - BeOn's msg.id
  - whether msg.fwd_from is set (and its extracted (chat_id, post_id))
  - the dedup key _msg_key would compute
  - whether that key is in inbox.jsonl's existing-key set
  - first 80 chars of caption (or '<no caption>')

Goal: identify why the 2026-05-23 DART 파두 message keeps re-forwarding
every 2-hour beon-sync tick despite the dedup fix in 6b6a97a.

Read-only:
  - never calls forward_messages
  - never touches store.db
  - never writes inbox.jsonl
  - never sends Telegram notifications

Usage on host:
    cd ~/stock-trade
    .backfill-venv/bin/python -m trade.scripts.diagnose_dedup --days 3
    .backfill-venv/bin/python -m trade.scripts.diagnose_dedup --grep 파두
    .backfill-venv/bin/python -m trade.scripts.diagnose_dedup --days 1 --grep dart

Then share the output (token values are not present in this output)."""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telethon import TelegramClient

from trade.scripts.backfill_beon import (
    API_ID,
    API_HASH,
    SESSION_PATH,
    SOURCE_USERNAME,
    _load_existing_keys,
    _msg_key,
    _tutils,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("diagnose-dedup")


def _fwd_summary(fwd) -> str:
    if fwd is None:
        return "fwd_from=None  (BeOn-authored, no relay chain)"
    from_peer = getattr(fwd, "from_id", None)
    channel_post = getattr(fwd, "channel_post", None)
    from_name = getattr(fwd, "from_name", None)
    bits = []
    if from_peer is not None:
        try:
            cid = _tutils.get_peer_id(from_peer)
            bits.append(f"from_id={type(from_peer).__name__}({cid})")
        except Exception as e:
            bits.append(f"from_id={from_peer!r}  ← get_peer_id err: {e}")
    else:
        bits.append("from_id=None")
    bits.append(f"channel_post={channel_post}")
    if from_name:
        bits.append(f"from_name={from_name!r}")
    return "fwd_from(" + ", ".join(bits) + ")"


async def run(days: int, grep: str | None, sample_count: int) -> int:
    existing = _load_existing_keys()
    log.info(
        "inbox.jsonl existing forward keys: %d  (sample first 5: %s)",
        len(existing),
        list(sorted(existing))[:5],
    )

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()
    try:
        source = await client.get_entity(SOURCE_USERNAME)
        source_chat_id = _tutils.get_peer_id(source)
        log.info(
            "source=@%s raw_id=%s marked=%s",
            SOURCE_USERNAME, source.id, source_chat_id,
        )

        since = datetime.now(timezone.utc) - timedelta(days=days)
        log.info(
            "scanning BeOn messages since %s (window=%dd, grep=%r)",
            since.isoformat(), days, grep,
        )

        printed = 0
        total = 0
        fwd_with_chain = 0
        fwd_without_chain = 0
        miss_in_inbox = 0

        async for msg in client.iter_messages(
            source, offset_date=since, reverse=True
        ):
            total += 1
            caption = (msg.text or "")[:200]
            if grep and grep.lower() not in caption.lower():
                continue
            if printed >= sample_count:
                continue
            printed += 1

            fwd = getattr(msg, "fwd_from", None)
            if fwd is not None:
                fwd_with_chain += 1
            else:
                fwd_without_chain += 1

            key = _msg_key(msg, source_chat_id)
            hit = key in existing

            if not hit:
                miss_in_inbox += 1

            print("─" * 78)
            print(f"BeOn msg.id={msg.id}  date={msg.date.isoformat()}")
            print(f"  {_fwd_summary(fwd)}")
            print(f"  _msg_key → {key}")
            print(f"  in_inbox_keys: {'✓ yes (would skip)' if hit else '✗ NO  (would FORWARD)'}")
            print(f"  caption[:80]: {caption[:80]!r}")

        print("=" * 78)
        log.info(
            "scanned %d messages | printed %d | fwd_chain=%d "
            "fwd_authored=%d would-forward=%d",
            total, printed, fwd_with_chain, fwd_without_chain, miss_in_inbox,
        )
        return 0
    finally:
        await client.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Read-only dedup diagnosis: prints _msg_key + inbox membership "
            "for recent BeOn messages so we can see why a known-relayed "
            "post is being treated as 'new' by backfill."
        )
    )
    ap.add_argument(
        "--days", type=int, default=3,
        help="Lookback window in days (default: 3).",
    )
    ap.add_argument(
        "--grep", type=str, default=None,
        help="Only sample messages whose caption contains this substring "
             "(case-insensitive). Use to zero in on '파두' or 'dart'.",
    )
    ap.add_argument(
        "--sample-count", type=int, default=20,
        help="Max messages to print details for (default: 20).",
    )
    args = ap.parse_args()
    rc = asyncio.run(run(args.days, args.grep, args.sample_count))
    sys.exit(rc)


if __name__ == "__main__":
    main()
