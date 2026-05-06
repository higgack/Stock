"""Track in-flight analysis state across bot restarts.

When a bot starts up, it reads the state file. If the previous
instance was in the middle of an analysis (state present), the
orphaned 'analysis started' progress message is edited to a
'restart interrupted' notice so the user isn't left waiting forever.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("stock-bot.recovery")

_STATE_FILE = Path.home() / ".tradingagents" / ".progress.json"


def write(chat_id: int, message_id: int, ticker: str) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"chat_id": chat_id, "message_id": message_id, "ticker": ticker}),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("failed to write progress state: %s", exc)


def clear() -> None:
    try:
        _STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("failed to clear progress state: %s", exc)


def read() -> dict | None:
    if not _STATE_FILE.exists():
        return None
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("failed to read progress state: %s", exc)
        return None
