"""One-shot Telethon backfill: forward historical BeOn_BeClear posts
into the private trade channel so trade-bot can ingest them like live
forwards.

Run once after setup. Idempotent — scans inbox.jsonl and skips any
BeOn message_id that's already been ingested, so re-running is safe
(picks up where it left off after a FloodWait abort, etc.).

Setup (one-time):
  cd ~/stock-trade
  python -m venv .backfill-venv
  .backfill-venv/bin/pip install -r trade/scripts/requirements.txt
  # add TRADE_TELETHON_API_ID + TRADE_TELETHON_API_HASH to .env
  # (issue them at https://my.telegram.org/apps — keep them in .env only)

Run:
  .backfill-venv/bin/python trade/scripts/backfill_beon.py --since 2026-05-01
  # optional: --to 2026-05-16, --dry-run

After it finishes you can remove the temp venv and session file:
  rm -rf .backfill-venv .backfill-session*
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("backfill")

API_ID = int(os.environ["TRADE_TELETHON_API_ID"])
API_HASH = os.environ["TRADE_TELETHON_API_HASH"]

_dest_raw = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
DEST_IDS = [int(x) for x in _dest_raw.split(",") if x.strip()]
if len(DEST_IDS) != 1:
    raise SystemExit(
        "backfill: TRADE_CHANNEL_CHAT_IDS must be a single chat ID for backfill"
    )
DEST_ID = DEST_IDS[0]

INBOX_DIR = Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))
INBOX_PATH = INBOX_DIR / "inbox.jsonl"

SOURCE_USERNAME = "BeOn_BeClear"
SESSION_PATH = ".backfill-session"  # cwd-relative; .gitignored

# Pause between send units. Telegram throttles user-account forwards
# aggressively; 1s/group is well below the floor that triggers FloodWait
# in normal conditions while keeping a 30-day backfill under an hour.
PAUSE_BETWEEN_SENDS = 1.0


def _load_existing_beon_ids() -> set[int]:
    """Collect BeOn message IDs that trade-bot has already ingested.

    Both live forwards and prior backfill runs land in inbox.jsonl with
    forward_origin_chat_username = "BeOn_BeClear" and the original
    message_id in forward_origin_message_id. Skipping these makes
    repeated backfill runs additive instead of duplicating posts.
    """
    if not INBOX_PATH.exists():
        return set()
    ids: set[int] = set()
    with INBOX_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("forward_origin_chat_username") == SOURCE_USERNAME:
                mid = rec.get("forward_origin_message_id")
                if mid is not None:
                    ids.add(int(mid))
    return ids


def _group_by_album(messages: list[Message]) -> list[list[Message]]:
    """Walk chronological messages and return a list of send-units:
    each unit is one standalone message OR one full album (same
    grouped_id, contiguous in time). Forwarding an album as a list
    preserves its album-ness in the destination, which the trade-bot's
    media_group_id grouping relies on.
    """
    units: list[list[Message]] = []
    pending: list[Message] = []
    pending_gid: int | None = None
    for m in messages:
        gid = getattr(m, "grouped_id", None)
        if gid is None:
            if pending:
                units.append(pending)
                pending = []
                pending_gid = None
            units.append([m])
        elif gid == pending_gid:
            pending.append(m)
        else:
            if pending:
                units.append(pending)
            pending = [m]
            pending_gid = gid
    if pending:
        units.append(pending)
    return units


async def _forward_unit(client, source, unit: list[Message], dest) -> bool:
    """Forward one send-unit (single message or album) with FloodWait
    retry. Returns True on success, False after 5 failed attempts.
    """
    msg_ids = [m.id for m in unit]
    delay = 0
    for attempt in range(5):
        if delay > 0:
            log.info("flood wait %ds before retry %d", delay, attempt + 1)
            await asyncio.sleep(delay)
        try:
            await client.forward_messages(dest, msg_ids, from_peer=source)
            return True
        except FloodWaitError as e:
            delay = e.seconds + 1
    log.error("giving up on msgs=%s after 5 attempts", msg_ids)
    return False


async def run(since: datetime, until: datetime | None, dry_run: bool) -> None:
    existing = _load_existing_beon_ids()
    log.info("already ingested: %d BeOn messages", len(existing))

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()
    log.info("Telethon session ready")
    try:
        source = await client.get_entity(SOURCE_USERNAME)
        dest = await client.get_entity(DEST_ID)
        log.info("source=%s dest=%s", source.id, dest.id)

        # reverse=True means iterate oldest-first starting from
        # offset_date. Caps with our own date check on `until`.
        candidates: list[Message] = []
        skipped_existing = 0
        async for msg in client.iter_messages(
            source, offset_date=since, reverse=True
        ):
            if until is not None and msg.date > until:
                break
            if msg.id in existing:
                skipped_existing += 1
                continue
            candidates.append(msg)

        until_label = (until or datetime.now(timezone.utc)).date().isoformat()
        log.info(
            "range %s → %s — candidates=%d skipped_existing=%d",
            since.date().isoformat(),
            until_label,
            len(candidates),
            skipped_existing,
        )

        units = _group_by_album(candidates)
        log.info(
            "grouped into %d send units (singles + albums)", len(units)
        )

        if dry_run:
            log.info("dry-run: not forwarding")
            return

        forwarded_msgs = 0
        for i, unit in enumerate(units, 1):
            if await _forward_unit(client, source, unit, dest):
                forwarded_msgs += len(unit)
            if i % 20 == 0:
                log.info(
                    "progress: %d/%d units (%d msgs forwarded)",
                    i,
                    len(units),
                    forwarded_msgs,
                )
            await asyncio.sleep(PAUSE_BETWEEN_SENDS)

        log.info(
            "done: forwarded %d of %d candidate messages",
            forwarded_msgs,
            len(candidates),
        )
    finally:
        await client.disconnect()


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill BeOn_BeClear → private trade channel via Telethon."
    )
    ap.add_argument(
        "--since", required=True, help="YYYY-MM-DD (UTC), inclusive lower bound"
    )
    ap.add_argument(
        "--to", help="YYYY-MM-DD (UTC), inclusive upper bound. Default: now."
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="enumerate candidates without forwarding",
    )
    args = ap.parse_args()

    asyncio.run(
        run(
            _parse_date(args.since),
            _parse_date(args.to) if args.to else None,
            args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
