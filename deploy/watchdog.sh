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
# Skips the check entirely when the bot was started within the same
# window — Python imports + langgraph/google-genai cold start can take
# 2+ minutes on a 1GB VM, and we don't want to kill a bot that just
# hasn't finished booting yet.

set -euo pipefail

WINDOW_SECONDS=180

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
