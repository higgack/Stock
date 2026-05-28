"""Screener domain registry change history — JSONL append-only log.

User policy (2026-05-29): "screener_list 가 추가/변경될때마다 그 내용은
매번 기록되어야돼. 즉 내가 help 를 보고 그 페이지로 들어가서 어떤게
있는지 확인할수 있도록. 그래야 내가 사용하고 또 지속개발할수 있으니."

Workflow:
 1. ``regenerate_screener_index()`` (every dashboard refresh) calls
    ``record_snapshot()`` here.
 2. ``record_snapshot()`` loads the JSONL, replays added/removed entries
    to derive the last-known domain set, then compares against the live
    ``bot.screener_themes.list_domains()`` result.
 3. If any addition or removal is detected, appends one entry capturing
    the delta + total-after + display names for added domains.
 4. ``screener_domains.html`` reads ``load_history()`` and renders a
    "📜 변경 이력" section so the user can see when each Wave shipped
    and what the active registry looked like at every step.

First call ever (empty file or none-exists) seeds the history with a
single ``initial=True`` entry listing all current domains. From then
on every additive PR / commit (Wave 2-B / Wave 3 / Wave ∞) auto-logs.

History is append-only; we do not retroactively rewrite older entries
when a domain is later removed — the removal gets its own entry so the
chronological story stays faithful to git history without needing it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.screener_history")

_HISTORY_FILE = Path.home() / ".tradingagents" / "screener_domain_history.jsonl"
_KST = timezone(timedelta(hours=9))


def _kst_now_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def _replay_to_last_set() -> set[str]:
    """Walk the JSONL chronologically (oldest → newest) and return the
    domain set after the last recorded entry. Empty when file absent."""
    if not _HISTORY_FILE.exists():
        return set()
    current: set[str] = set()
    with _HISTORY_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("screener_history: skipping bad JSONL line: %s", exc)
                continue
            for slug in entry.get("added", []) or []:
                current.add(slug)
            for slug in entry.get("removed", []) or []:
                current.discard(slug)
    return current


def record_snapshot() -> Optional[dict]:
    """Compare current registry vs replayed last-known set, append diff
    entry when any addition or removal is detected. Returns the appended
    entry dict or None when nothing changed (no-op call).

    Errors are logged but never raised — history tracking is best-effort
    so a corrupted JSONL line never blocks the screener archive page
    regeneration that drives the dashboard refresh."""
    try:
        from bot.screener_themes import list_domains
        live = list_domains()
        live_by_slug = {d["slug"]: d for d in live}
        live_set = set(live_by_slug)

        last_set = _replay_to_last_set()
        is_initial = not last_set and not _HISTORY_FILE.exists()

        added = sorted(live_set - last_set)
        removed = sorted(last_set - live_set)
        if not added and not removed:
            return None

        entry = {
            "ts": _kst_now_iso(),
            "added": added,
            "removed": removed,
            "added_with_names": {
                s: live_by_slug[s].get("domain", s) for s in added
            },
            "total_after": len(live_set),
            "initial": is_initial,
        }
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info(
            "screener_history: appended entry — added %d, removed %d, "
            "total_after %d%s",
            len(added), len(removed), entry["total_after"],
            " (INITIAL seed)" if is_initial else "",
        )
        return entry
    except Exception as exc:
        log.warning("screener_history: record_snapshot failed: %s", exc)
        return None


def load_history() -> list[dict]:
    """Return all history entries, NEWEST first (so the dashboard page
    can render most-recent additions at the top)."""
    if not _HISTORY_FILE.exists():
        return []
    out: list[dict] = []
    try:
        with _HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("screener_history: load_history failed: %s", exc)
        return []
    out.reverse()
    return out
