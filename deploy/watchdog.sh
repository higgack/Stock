#!/bin/bash
# Watchdog: ensure stock-bot is actively polling Telegram.
#
# python-telegram-bot's run_polling() calls /getUpdates every ~10 seconds
# while the asyncio event loop is healthy. If no /getUpdates request shows
# up in the journal for 90 seconds, the bot has hung (event loop stalled
# under memory pressure, frozen on a system call, etc.) and is no longer
# servicing analyses. Restart it; the bot's startup recovery code will
# clean up any orphaned 'analysis started' progress message.

set -euo pipefail

WINDOW_SECONDS=90

# This service runs as root, so journalctl + systemctl don't need sudo.
RECENT=$(journalctl -u stock-bot --since "${WINDOW_SECONDS} seconds ago" \
    2>/dev/null | grep -c "getUpdates" || true)

if [ "$RECENT" = "0" ]; then
    echo "stock-bot-watchdog: no Telegram polling for ${WINDOW_SECONDS}s — restarting"
    /bin/systemctl restart stock-bot
fi
