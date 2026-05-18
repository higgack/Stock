"""Append-only markdown decision log for TradingAgents."""

from typing import List, Optional
from pathlib import Path
import re

from tradingagents.agents.utils.rating import parse_rating


class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\nOUTCOMES:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)(?=\nOUTCOMES:|\Z)", re.DOTALL)
    # OUTCOMES section holds long-horizon (15d / 30d) follow-up returns
    # written after the initial 5-trading-day resolution. Each line:
    # `15d | +2.3% | -1.2%p`. The 5d outcome stays in the entry tag for
    # backward compat — only 15d+ live here.
    _OUTCOMES_RE = re.compile(r"OUTCOMES:\n(.*?)$", re.DOTALL)
    _OUTCOME_LINE_RE = re.compile(
        r"^\s*(\d+)d\s*\|\s*([+-]?\d+\.?\d*%)\s*\|\s*([+-]?\d+\.?\d*%p?)\s*$"
    )

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Optional cap on resolved entries. None disables rotation.
        self._max_entries = cfg.get("memory_log_max_entries")

    # --- Write path (Phase A) ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        if not self._log_path:
            return
        # Idempotency guard: fast raw-text scan instead of full parse
        if self._log_path.exists():
            raw = self._log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("| pending]"):
                    return
        rating = parse_rating(final_trade_decision)
        tag = f"[{trade_date} | {ticker} | {rating} | pending]"
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Read path (Phase A) ---

    def load_entries(self) -> List[dict]:
        """Parse all entries from log. Returns list of dicts."""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> List[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(self, ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
        """Return formatted past context string for agent prompt injection."""
        entries = [e for e in self.load_entries() if not e.get("pending")]
        if not entries:
            return ""

        same, cross = [], []
        for e in reversed(entries):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
    ) -> None:
        """Replace pending tag and append REFLECTION section using atomic write.

        Finds the first pending entry matching (trade_date, ticker), updates
        its tag with return figures, and appends a REFLECTION section.  Uses
        a temp-file + os.replace() so a crash mid-write never corrupts the log.
        """
        if not self._log_path or not self._log_path.exists():
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        pending_prefix = f"[{trade_date} | {ticker} |"
        raw_pct = f"{raw_return:+.1%}"
        alpha_pct = f"{alpha_return:+.1%}"

        updated = False
        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            if (
                not updated
                and tag_line.startswith(pending_prefix)
                and tag_line.endswith("| pending]")
            ):
                # Parse rating from the existing pending tag
                fields = [f.strip() for f in tag_line[1:-1].split("|")]
                rating = fields[2]
                new_tag = (
                    f"[{trade_date} | {ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {holding_days}d]"
                )
                rest = "\n".join(lines[1:])
                new_blocks.append(
                    f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                )
                updated = True
            else:
                new_blocks.append(block)

        if not updated:
            return

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    def batch_append_long_horizon_outcomes(self, updates: List[dict]) -> None:
        """Append 15d / 30d outcome lines to already-resolved entries.

        Each element of `updates`: {ticker, trade_date, days, raw_return,
        alpha_return}. `days` is the trading-day horizon (15 or 30).

        Skips entries that already carry an outcome at the given `days`
        (idempotent — safe to re-run auto_resolve in a tight loop). The
        existing OUTCOMES section is parsed and rewritten so lines stay
        sorted ascending by days.

        Used by bot/auto_resolve.py 12h cycle: once a pending entry has
        its 5d resolved (initial pass), subsequent passes append 15d
        when the 21-calendar-day gate clears, then 30d at 42 calendar
        days. Each window is independent — a flaky yfinance fetch at
        15d doesn't block 30d resolution.
        """
        if not self._log_path or not self._log_path.exists() or not updates:
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        # Group updates by (trade_date, ticker) → list of {days, raw, alpha}.
        # Store alpha WITHOUT a trailing 'p' suffix to match the 5d alpha
        # stored in the entry tag (memory.py update_with_outcome writes
        # f"{alpha_return:+.1%}" — no 'p'). The renderer adds 'p' once at
        # display time. Storing 'p' here too caused a double-suffix bug
        # ('-0.6%pp') visible on the dashboard.
        update_map: dict[tuple[str, str], list[dict]] = {}
        for u in updates:
            key = (u["trade_date"], u["ticker"])
            update_map.setdefault(key, []).append({
                "days": int(u["days"]),
                "raw": f"{u['raw_return']:+.1%}",
                "alpha": f"{u['alpha_return']:+.1%}",
            })

        new_blocks: List[str] = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue
            tag_line = stripped.splitlines()[0].strip()
            # Only touch resolved entries (skip pending and malformed)
            if not (tag_line.startswith("[") and tag_line.endswith("]")
                    and not tag_line.endswith("| pending]")):
                new_blocks.append(block)
                continue
            fields = [f.strip() for f in tag_line[1:-1].split("|")]
            if len(fields) < 4:
                new_blocks.append(block)
                continue
            key = (fields[0], fields[1])
            if key not in update_map:
                new_blocks.append(block)
                continue

            # Merge existing OUTCOMES (if any) with new updates for this key.
            body = "\n".join(stripped.splitlines()[1:])
            existing_outcomes: dict[int, dict] = {}
            outcomes_match = self._OUTCOMES_RE.search(body)
            outcomes_text_start = -1
            if outcomes_match:
                outcomes_text_start = outcomes_match.start()
                for line in outcomes_match.group(1).splitlines():
                    m = self._OUTCOME_LINE_RE.match(line)
                    if m:
                        d = int(m.group(1))
                        existing_outcomes[d] = {
                            "days": d, "raw": m.group(2), "alpha": m.group(3),
                        }
            # Idempotent merge: new updates win over existing at the same
            # `days` key (e.g. a 15d row gets recomputed with cleaner
            # benchmark data on a later auto_resolve cycle).
            for upd in update_map[key]:
                existing_outcomes[upd["days"]] = upd
            ordered = sorted(existing_outcomes.values(), key=lambda x: x["days"])
            outcomes_block = "OUTCOMES:\n" + "\n".join(
                f"{o['days']}d | {o['raw']} | {o['alpha']}" for o in ordered
            )

            if outcomes_text_start >= 0:
                # Replace existing OUTCOMES section in place.
                new_body = body[:outcomes_text_start].rstrip() + "\n\n" + outcomes_block
            else:
                # Append fresh OUTCOMES section at the end of the body.
                new_body = body.rstrip() + "\n\n" + outcomes_block
            new_blocks.append(f"{tag_line}\n\n{new_body.lstrip()}")
            del update_map[key]

        # Rotation logic still applies — long-horizon updates count as
        # resolved entries (they only happen on resolved entries).
        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    def batch_update_with_outcomes(self, updates: List[dict]) -> None:
        """Apply multiple outcome updates in a single read + atomic write.

        Each element of updates must have keys: ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection.
        """
        if not self._log_path or not self._log_path.exists() or not updates:
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        # Build lookup keyed by (trade_date, ticker) for O(1) dispatch
        update_map = {(u["trade_date"], u["ticker"]): u for u in updates}

        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            matched = False
            for (trade_date, ticker), upd in list(update_map.items()):
                pending_prefix = f"[{trade_date} | {ticker} |"
                if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    raw_pct = f"{upd['raw_return']:+.1%}"
                    alpha_pct = f"{upd['alpha_return']:+.1%}"
                    new_tag = (
                        f"[{trade_date} | {ticker} | {rating}"
                        f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                    )
                    del update_map[(trade_date, ticker)]
                    matched = True
                    break

            if not matched:
                new_blocks.append(block)

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    # --- Helpers ---

    def _apply_rotation(self, blocks: List[str]) -> List[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries
        kept: List[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _parse_entry(self, raw: str) -> Optional[dict]:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "pending": fields[3] == "pending",
            "raw": fields[3] if fields[3] != "pending" else None,
            "alpha": fields[4] if len(fields) > 4 else None,
            "holding": fields[5] if len(fields) > 5 else None,
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""

        # Long-horizon outcomes (15d, 30d, ...) live in an OUTCOMES section
        # appended after REFLECTION when auto_resolve catches up. The 5d
        # outcome stays in the tag for backward compat. outcomes_extra is
        # a list of dicts so the dashboard renderer can iterate in order.
        outcomes_extra: list[dict] = []
        outcomes_match = self._OUTCOMES_RE.search(body)
        if outcomes_match:
            for line in outcomes_match.group(1).splitlines():
                m = self._OUTCOME_LINE_RE.match(line)
                if not m:
                    continue
                outcomes_extra.append({
                    "days": int(m.group(1)),
                    "raw": m.group(2),
                    "alpha": m.group(3),
                })
        entry["outcomes_extra"] = outcomes_extra
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        # Surface long-horizon outcomes (15d / 30d) so the next analysis
        # sees how the previous call played out beyond the 5d window.
        # E.g. 'OUTCOMES: 15d +2.3%/-1.2%p · 30d +5.8%/+0.4%p' — a Sell
        # call that was right at 5d but reversed by 30d is exactly the
        # signal the next analysis's reflection should weigh.
        extras = e.get("outcomes_extra") or []
        if extras:
            ext_str = " · ".join(
                f"{o['days']}d {o['raw']}/{o['alpha']}" for o in extras
            )
            parts.append(f"OUTCOMES_EXTRA: {ext_str}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"
