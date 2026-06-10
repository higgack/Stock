"""Operator DM chat-id capture for trade-bot alerts.

The customs ±30% alert DMs the operator — but the bot doesn't otherwise
know which Telegram chat 'the operator' is. Rather than make the operator
look up their numeric id, we capture it the first time they use any
customs DM command (/hs, /customs, …) and persist it to
~/.trade/operator_chat_id. The customs-alert script reads it.

Env TRADE_OPERATOR_CHAT_ID overrides the captured file when set
(explicit wins, useful for a fixed alert target or testing).

Single-operator assumption: this bot is run by one person, so 'most
recent DM-command user' == the operator. remember() only rewrites on
change, so it's a no-op on the steady state.
"""

from __future__ import annotations

import os
from pathlib import Path

_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
DEFAULT_PATH = _DIR / "operator_chat_id"


def remember(chat_id: int | None, path: Path | str = DEFAULT_PATH) -> None:
    """Persist the operator's chat_id (idempotent — writes only on change).
    Best-effort: any filesystem error is swallowed so a DM command never
    fails just because we couldn't record the id."""
    if not chat_id:
        return
    p = Path(path)
    try:
        current = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        if current == str(chat_id):
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(str(chat_id), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def get(path: Path | str = DEFAULT_PATH) -> int | None:
    """Operator chat_id: env TRADE_OPERATOR_CHAT_ID wins, else the
    captured file, else None (caller skips alerting)."""
    env = (os.environ.get("TRADE_OPERATOR_CHAT_ID") or "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    p = Path(path)
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
