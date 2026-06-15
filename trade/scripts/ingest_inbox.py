"""Bulk-ingest the inbox.jsonl into store.db.

Reads every line in ~/.trade/inbox.jsonl, groups Telegram messages by
media_group_id (so a BeOn album of one caption + N photos becomes one
alert row carrying all photo paths), parses each captioned member with
trade.parser, and upserts to store.db.

Idempotent. Reruns skip already-stored alerts (UNIQUE on
source_chat_id + source_message_id), so it's safe to invoke after each
new burst of forwards or after improving the parser — old work isn't
redone but newly-eligible captions land.

Usage:
    .venv/bin/python -m trade.scripts.ingest_inbox
    .venv/bin/python -m trade.scripts.ingest_inbox --verbose
    .venv/bin/python -m trade.scripts.ingest_inbox --inbox PATH --db PATH
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import ignored as _ignored
from trade.parser import parse_caption
from trade.store import (
    alert_to_row,
    open_db,
    stats,
    update_media_paths,
    upsert_alert,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ingest")

DATA_DIR = Path(
    os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade")
)
DEFAULT_INBOX = DATA_DIR / "inbox.jsonl"
DEFAULT_MEDIA_ROOT = DATA_DIR / "media"
DEFAULT_DB = DATA_DIR / "store.db"


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("inbox line %d: malformed JSON (%s)", n, e)
    return rows


def _resolve_photo_path(media_root: Path, row: dict) -> str | None:
    """media/<YYYY-MM-DD>/<file_unique_id>.jpg if the file exists.

    The date partition comes from the row's `date` field (Telegram
    receive time, matching what bot.py writes), so we stay consistent
    even if the operator changed MEDIA_ROOT layout.
    """
    uid = row.get("photo_file_unique_id")
    if not uid:
        return None
    date_iso = row.get("date") or ""
    day = date_iso.split("T", 1)[0] if "T" in date_iso else date_iso[:10]
    if not day:
        return None
    full = media_root / day / f"{uid}.jpg"
    if not full.exists():
        return None
    # Store relative to ~/.trade for portability (so moving the data
    # dir doesn't invalidate every row).
    return str(full.relative_to(media_root.parent))


def _stored_media_empty(conn, chat_id: int, msg_id: int) -> bool:
    """True when the already-stored alert has no media_paths yet — the
    signal that its photo finished downloading AFTER ingest first ran
    (the backfill/burst race), so a re-link is warranted. Missing row →
    False (nothing to heal; the upsert will insert it instead)."""
    cur = conn.execute(
        "SELECT media_paths FROM alerts "
        "WHERE source_chat_id=? AND source_message_id=?",
        (chat_id, msg_id),
    )
    r = cur.fetchone()
    if r is None:
        return False
    try:
        return not json.loads(r[0] or "[]")
    except Exception:
        return False


def _group_messages(rows: list[dict]) -> list[list[dict]]:
    """Group rows by media_group_id. Solo rows (None) each become a
    one-element group. Preserves the original arrival order so the
    'first captioned row' rule maps to the album's primary message.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    solos: list[list[dict]] = []
    for row in rows:
        gid = row.get("media_group_id")
        if gid:
            groups[gid].append(row)
        else:
            solos.append([row])
    return list(groups.values()) + solos


def _ingest_group(
    conn,
    group: list[dict],
    media_root: Path,
    counters: dict,
    ignored_ids: set[int],
) -> None:
    """Resolve one album/solo into a single alert row + media paths."""
    captioned = [r for r in group if r.get("caption_present")]
    if not captioned:
        # Photo-only album with no caption — typical when the captioned
        # sibling hasn't arrived yet at scan time. Skip; the next run
        # picks it up.
        counters["skipped_no_caption"] += 1
        return
    if len(captioned) > 1:
        counters["multi_caption_album"] += 1

    primary = captioned[0]
    # Operator-curated ignore list — promo posts ([비온 인사이트] etc.)
    # that aren't export/import alerts. Never reach store.db, never
    # surface in the daily integrity check.
    if primary.get("message_id") in ignored_ids:
        counters["skipped_ignored"] += 1
        return
    caption_text = primary.get("caption") or primary.get("text") or ""
    # Recurring promo prefixes (currently '[비온 인사이트]') — silently
    # dropped without needing per-msg_id /ignore. Same downstream effect
    # as the msg_id list but covers the whole series at once.
    if _ignored.matches_prefix(caption_text):
        counters["skipped_prefix"] += 1
        return
    # Body-marker filter (currently 'dart.fss.or.kr' for DART 공시 릴레이
    # — line 1 ticker varies per post so prefix matching can't catch it,
    # but the DART link in the body is a reliable marker).
    if _ignored.matches_contains(caption_text):
        counters["skipped_contains"] += 1
        return
    parsed = parse_caption(caption_text)
    if parsed is None:
        counters["unparseable"] += 1
        return

    media_paths = []
    for r in group:
        p = _resolve_photo_path(media_root, r)
        if p:
            media_paths.append(p)

    row = alert_to_row(
        parsed,
        source_chat_id=primary["chat_id"],
        source_message_id=primary["message_id"],
        media_group_id=primary.get("media_group_id"),
        ingested_at=primary.get("ingested_at")
        or datetime.now(timezone.utc).isoformat(),
        posted_at=primary.get("forward_origin_date") or primary.get("date") or "",
        raw_text=caption_text,
        media_paths=media_paths,
    )
    inserted = upsert_alert(conn, row)
    if inserted:
        counters["inserted"] += 1
        if parsed.parse_warnings:
            counters["with_warnings"] += 1
    else:
        counters["already_present"] += 1
        # Self-heal the download↔ingest race. upsert is ON CONFLICT DO
        # NOTHING, so a backfilled/burst alert that was ingested BEFORE
        # its own photo finished the background download stays imageless
        # forever — the file lands seconds later but the row is never
        # revisited. When we now resolve media (file on disk) and the
        # stored row still has none, re-link it so the next 5-min refresh
        # cycle surfaces the image. (Album siblings already use
        # update_media_paths in the live path; this covers the whole-
        # alert race the BeOn backfill exposes — 732 imageless cards
        # 2026-06-15.) Gated on stored-empty so healed rows aren't
        # rewritten every cycle.
        if media_paths and _stored_media_empty(
            conn, primary["chat_id"], primary["message_id"]
        ):
            update_media_paths(
                conn, primary["chat_id"], primary["message_id"], media_paths
            )
            counters["media_relinked"] += 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bulk-ingest inbox.jsonl → store.db (idempotent)."
    )
    ap.add_argument(
        "--inbox", type=Path, default=DEFAULT_INBOX,
        help=f"jsonl path (default: {DEFAULT_INBOX})",
    )
    ap.add_argument(
        "--media-root", type=Path, default=DEFAULT_MEDIA_ROOT,
        help=f"media dir (default: {DEFAULT_MEDIA_ROOT})",
    )
    ap.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"SQLite path (default: {DEFAULT_DB})",
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    rows = _read_jsonl(args.inbox)
    log.info("inbox: %d rows from %s", len(rows), args.inbox)
    if not rows:
        log.warning("nothing to ingest; exiting")
        return 0

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(args.db)
    log.info("store: %s (existing alerts: %d)", args.db, stats(conn)["total"])

    groups = _group_messages(rows)
    log.info("grouped into %d send units", len(groups))

    ignored_ids = _ignored.load()
    log.info("operator ignore list: %d entries", len(ignored_ids))

    counters = {
        "inserted": 0,
        "already_present": 0,
        "skipped_no_caption": 0,
        "skipped_ignored": 0,
        "skipped_prefix": 0,
        "skipped_contains": 0,
        "unparseable": 0,
        "multi_caption_album": 0,
        "with_warnings": 0,
        "media_relinked": 0,
    }
    for grp in groups:
        _ingest_group(conn, grp, args.media_root, counters, ignored_ids)

    log.info("ingest counters: %s", counters)
    s = stats(conn)
    log.info("store stats: %s", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
