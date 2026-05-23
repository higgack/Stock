"""Retroactive cleanup of store.db rows matching the current
IGNORED_PREFIXES / IGNORED_CONTAINS filter.

Use case: filter was extended (e.g. new prefix or new substring
marker) and rows that landed before the extension are still in
store.db / dashboard. ingest_inbox.py only skips on insert; it
doesn't go back and delete already-stored rows. This script
closes that loop.

Idempotent — rerun finds 0 rows once everything's purged.
inbox.jsonl and media files are never touched (audit trail
intact; re-adding to the filter is reversible).

Usage:
    .venv/bin/python -m trade.scripts.purge_ignored --dry-run
    .venv/bin/python -m trade.scripts.purge_ignored
    .venv/bin/python -m trade.scripts.purge_ignored --db PATH

Telegram alerts (best-effort):
  ✅ <b>store.db 정리 완료</b>: N건 삭제 (filter: ...)
  (silent on 0 deletions and on missing creds)
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from trade import ignored as _ignored
from trade.store import open_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("purge-ignored")

DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
DEFAULT_DB = DATA_DIR / "store.db"


def _notify(text: str) -> None:
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        return
    chat_id = chat_ids.split(",")[0].strip()
    if not chat_id:
        return
    try:
        subprocess.run(
            [
                "curl", "-s", "-m", "10",
                "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "--data-urlencode", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}",
                "--data-urlencode", "parse_mode=HTML",
            ],
            timeout=15,
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def find_matching(conn) -> list[tuple[int, int, str, str]]:
    """Return (id, source_message_id, posted_at, reason) for every
    alert row whose raw_text matches the current filter. Reason is
    'prefix:<entry>' or 'contains:<entry>' for transparency.
    """
    out: list[tuple[int, int, str, str]] = []
    rows = conn.execute(
        "SELECT id, source_message_id, posted_at, raw_text FROM alerts"
    ).fetchall()
    for row in rows:
        rid, smid, posted_at, raw = row
        if _ignored.matches_prefix(raw):
            # Identify which prefix actually matched for the log.
            head = (raw or "").lstrip()
            hit = next(
                (p for p in _ignored.IGNORED_PREFIXES if head.startswith(p)),
                _ignored.IGNORED_PREFIXES[0] if _ignored.IGNORED_PREFIXES else "?",
            )
            out.append((rid, smid, posted_at, f"prefix:{hit}"))
            continue
        if _ignored.matches_contains(raw):
            hit = next(
                (n for n in _ignored.IGNORED_CONTAINS if n in (raw or "")),
                _ignored.IGNORED_CONTAINS[0] if _ignored.IGNORED_CONTAINS else "?",
            )
            out.append((rid, smid, posted_at, f"contains:{hit}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Delete store.db rows that match the current ignored filter "
            "(retroactive cleanup after extending IGNORED_PREFIXES / "
            "IGNORED_CONTAINS). Idempotent."
        )
    )
    ap.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"SQLite path (default: {DEFAULT_DB})",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="List rows that would be deleted without touching the DB.",
    )
    args = ap.parse_args()

    if not args.db.exists():
        log.error("db not found: %s", args.db)
        return 1

    conn = open_db(args.db)
    try:
        matches = find_matching(conn)
        log.info(
            "filter: prefixes=%d contains=%d → matched %d row(s)",
            len(_ignored.IGNORED_PREFIXES),
            len(_ignored.IGNORED_CONTAINS),
            len(matches),
        )
        for rid, smid, posted_at, reason in matches[:20]:
            log.info(
                "  id=%d msg_id=%d posted=%s reason=%s",
                rid, smid, posted_at, reason,
            )
        if len(matches) > 20:
            log.info("  … (+%d more)", len(matches) - 20)

        if args.dry_run:
            log.info("dry-run: no changes")
            return 0

        if not matches:
            log.info("nothing to delete")
            return 0

        ids = [m[0] for m in matches]
        # Clear any superseded_by pointer that targets a row we're about
        # to delete, so we don't leave dangling FK references behind.
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE alerts SET superseded_by = NULL "
            f"WHERE superseded_by IN ({placeholders})",
            ids,
        )
        conn.execute(
            f"DELETE FROM alerts WHERE id IN ({placeholders})", ids
        )
        conn.commit()

        # Summarise hit reasons for the alert (e.g. "DART=12, 비온인사이트=3").
        reasons: dict[str, int] = {}
        for _, _, _, reason in matches:
            reasons[reason] = reasons.get(reason, 0) + 1
        reason_str = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))

        log.info("deleted %d row(s) — reasons: %s", len(matches), reason_str)
        _notify(
            "✅ <b>store.db 정리 완료</b>\n"
            f"{len(matches)}건 삭제\n"
            f"<i>filter: {reason_str}</i>"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
