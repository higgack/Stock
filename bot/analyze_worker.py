"""CLI entrypoint that runs a single analysis in an isolated subprocess.

Spawned by `bot.telegram_bot` for each /TICKER request so the main bot's
asyncio loop is fully insulated from the analysis. Effects:

  * Telegram polling cannot starve under heavy LLM/langgraph work — the
    main process never holds the GIL during analysis.
  * Memory leaks (langgraph state retention, regex DFA caches, etc.) are
    released on each subprocess exit instead of growing in the long-lived
    bot process.
  * If the analysis hangs the parent can SIGKILL the subprocess and keep
    serving updates without restarting the bot.

Wire protocol (stdout, single JSON line at process end):
  success → {"ok": true, "summary": "...", "full": "..."}
  failure → {"ok": false, "error": "..."}

Logs (INFO/WARN from analyzer, third-party libs) go to stderr and are
captured by systemd alongside the parent bot's logs.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback


def main() -> int:
    if len(sys.argv) != 3:
        print(
            json.dumps(
                {"ok": False, "error": f"usage: {sys.argv[0]} <ticker> <YYYY-MM-DD>"}
            )
        )
        return 2

    ticker = sys.argv[1]
    target_date = sys.argv[2]

    # Configure logging to stderr so stdout stays clean for the JSON result.
    logging.basicConfig(
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO,
    )

    try:
        # Import lazily so a help-text/usage failure above doesn't pay the
        # langgraph cold-start cost.
        from bot.analyzer import analyze

        summary, full = analyze(ticker, target_date)
    except Exception as exc:  # noqa: BLE001 — surface anything to the parent
        logging.getLogger("analyze-worker").exception("analysis failed")
        # Trim huge tracebacks before serializing — the parent only needs the
        # message for its user-facing failure renderer.
        msg = str(exc) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": msg}), flush=True)
        return 1

    print(json.dumps({"ok": True, "summary": summary, "full": full}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
