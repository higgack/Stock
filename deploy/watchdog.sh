#!/bin/bash
# Watchdog: ensure stock-bot is actively polling Telegram.
#
# python-telegram-bot's run_polling() calls /getUpdates every ~10 seconds
# while the asyncio event loop is healthy. If no /getUpdates request shows
# up in the journal for the configured window, the bot has hung (event
# loop stalled under memory pressure, frozen on a system call, etc.) and
# is no longer servicing analyses. Restart it; the bot's startup recovery
# code will clean up any orphaned 'analysis started' progress message.
#
# Skips the check entirely when:
#  - The bot was started within the same window (Python imports +
#    langgraph cold start can take 2+ minutes on a small VM).
#  - An analysis is currently running (.busy marker present and fresh).
#    During heavy Gemini calls the worker thread can briefly starve the
#    main asyncio loop of GIL time, dropping /getUpdates frequency below
#    detection. analyze() has its own 10-min timeout for true hangs.

set -euo pipefail

WINDOW_SECONDS=180
BUSY_MARKER=/home/higgack/.tradingagents/.busy
# If the busy marker is older than this, treat it as stale and let the
# watchdog kick in regardless — protects against a crashed analyze()
# leaving the marker behind forever.
BUSY_STALE_MINUTES=30

# Active analysis? Skip — analyze() has its own asyncio.wait_for(timeout=600).
if [ -f "$BUSY_MARKER" ]; then
    if [ -z "$(find "$BUSY_MARKER" -mmin +"$BUSY_STALE_MINUTES" 2>/dev/null)" ]; then
        echo "stock-bot-watchdog: analysis in progress (.busy fresh) — skipping"
        exit 0
    fi
    echo "stock-bot-watchdog: stale .busy (>${BUSY_STALE_MINUTES}m) — proceeding"
fi

# Did the bot log its 'bot starting' line recently? If so it's still
# warming up and has reasonable cause to not be polling yet.
RECENT_START=$(journalctl -u stock-bot --since "${WINDOW_SECONDS} seconds ago" \
    2>/dev/null | grep -c "bot starting" || true)

if [ "$RECENT_START" -gt 0 ]; then
    echo "stock-bot-watchdog: bot started within last ${WINDOW_SECONDS}s — skipping"
    exit 0
fi

# This service runs as root, so journalctl + systemctl don't need sudo.
RECENT=$(journalctl -u stock-bot --since "${WINDOW_SECONDS} seconds ago" \
    2>/dev/null | grep -c "getUpdates" || true)

if [ "$RECENT" = "0" ]; then
    echo "stock-bot-watchdog: no Telegram polling for ${WINDOW_SECONDS}s — restarting"
    /bin/systemctl restart stock-bot
fi
