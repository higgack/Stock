"""Operator's manual ignore list for inbox.jsonl messages that aren't
export/import data (e.g. '[비온 인사이트]' promotional posts that
land on the BeOn channel from time to time).

State lives in plain text at `~/.trade/ignored.txt`, one Telegram
message_id per line. Comments (`#`) and blank lines are skipped, so
the operator can hand-edit if they want.

Also exposes `IGNORED_PREFIXES` — a hard-coded tuple of caption
prefixes that are silently dropped at ingest time without needing
per-msg_id curation. Use this for recurring promo series the operator
has confirmed are off-topic (currently just `[비온 인사이트]`).
New prefixes are added by appending to the tuple; reversal is the
opposite. msg_id-based ignoring still works for one-off posts that
don't fit a prefix.

Used by:
  - trade/scripts/ingest_inbox.py — skips ignored msg_ids AND
    prefix-matched captions so neither reaches store.db
  - trade/scripts/unstored_check.py — skips both so the daily
    00:00 KST integrity alert stays quiet on known off-topic posts
  - bot DM commands /ignore, /unignore, /ignored

Reversal is always safe: `/unignore <msg_id>` (or remove the line
from the file) brings the message back into the next ingest cycle;
removing a prefix from `IGNORED_PREFIXES` does the same for the
series. inbox.jsonl and media files are never touched.
"""

from __future__ import annotations

import os
from pathlib import Path


_DEFAULT_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
DEFAULT_PATH = _DEFAULT_DIR / "ignored.txt"

# Recurring promo / commentary series that BeOn occasionally posts on
# the same channel as the export/import data alerts. Captions starting
# with any of these (after stripping) are silently dropped at ingest —
# no store.db row, no daily ⚠️ integrity alert. Append to extend;
# the comparison is exact prefix match on the stripped caption.
IGNORED_PREFIXES: tuple[str, ...] = (
    "[비온 인사이트]",
)


def matches_prefix(caption: str | None) -> bool:
    """True if `caption` starts with any IGNORED_PREFIXES entry.

    Leading whitespace is stripped before matching so a stray space
    or newline doesn't bypass the filter. Empty / None captions
    return False — they're handled by the no-caption path elsewhere.
    """
    if not caption:
        return False
    head = caption.lstrip()
    return any(head.startswith(p) for p in IGNORED_PREFIXES)


def _ensure(path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.touch()
    return p


def load(path: Path | str = DEFAULT_PATH) -> set[int]:
    """Return the current ignore-set as ints. Bad lines (non-numeric,
    blank, comments) are skipped silently — hand-edit friendly.
    """
    p = _ensure(path)
    out: set[int] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.add(int(line))
        except ValueError:
            continue
    return out


def add(msg_id: int, path: Path | str = DEFAULT_PATH) -> bool:
    """Append msg_id to the ignore list. Returns True if newly added,
    False if the id was already present (idempotent).
    """
    current = load(path)
    if msg_id in current:
        return False
    p = _ensure(path)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"{msg_id}\n")
    return True


def remove(msg_id: int, path: Path | str = DEFAULT_PATH) -> bool:
    """Remove msg_id from the ignore list. Returns True if it was
    present, False if it was not (idempotent)."""
    current = load(path)
    if msg_id not in current:
        return False
    current.discard(msg_id)
    p = _ensure(path)
    body = "\n".join(str(x) for x in sorted(current))
    p.write_text(body + ("\n" if body else ""), encoding="utf-8")
    return True
