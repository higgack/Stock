#!/bin/bash
# Daily snapshot of ~/.trade/store.db. Keeps last 14 daily backups
# under ~/.trade/backups/, rotating the oldest out. Idempotent — same
# day rerun just overwrites today's snapshot.
#
# Uses SQLite's online .backup command via sqlite3 CLI when available
# (consistent point-in-time snapshot even while the live bot writes);
# falls back to cp on hosts without sqlite3 installed.
#
# Wired to trade-bot-backup.timer (daily, 03:00 KST).

set -euo pipefail

DATA_DIR="${TRADE_DATA_DIR:-$HOME/.trade}"
SRC="$DATA_DIR/store.db"
BACKUP_DIR="$DATA_DIR/backups"
KEEP=14

if [ ! -f "$SRC" ]; then
    echo "trade-backup: no store.db yet, nothing to back up"
    exit 0
fi

mkdir -p "$BACKUP_DIR"
TODAY=$(date +%Y-%m-%d)
DST="$BACKUP_DIR/store-${TODAY}.db"

if command -v sqlite3 >/dev/null 2>&1; then
    # Online backup — safe under concurrent writes.
    sqlite3 "$SRC" ".backup '$DST'"
else
    # WAL mode means a plain cp can capture a slightly torn view.
    # Better than nothing for the dev fallback.
    cp "$SRC" "$DST"
    # Also copy WAL + SHM so restore can replay.
    [ -f "$SRC-wal" ] && cp "$SRC-wal" "$DST-wal" || true
    [ -f "$SRC-shm" ] && cp "$SRC-shm" "$DST-shm" || true
fi

SIZE=$(du -h "$DST" | cut -f1)
echo "trade-backup: wrote $DST ($SIZE)"

# Rotate: keep newest $KEEP files, drop older.
mapfile -t old < <(ls -1t "$BACKUP_DIR"/store-*.db 2>/dev/null | tail -n +$((KEEP + 1)))
for f in "${old[@]:-}"; do
    [ -n "$f" ] && rm -f "$f" "$f-wal" "$f-shm" && echo "trade-backup: rotated $f"
done
