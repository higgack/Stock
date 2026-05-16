#!/bin/bash
# Watchdog: ensure trade-bot is actively polling Telegram.
#
# python-telegram-bot's run_polling() calls /getUpdates every ~10 seconds
# while the asyncio loop is healthy. If no /getUpdates request shows up
# in the journal for the configured window, the bot has hung and is no
# longer ingesting forwards. Restart it.
#
# Trade-bot has no "busy" marker (phase 1 is ingestion-only, no long
# analyses), so the only skip condition is a recent start — Python imports
# can take 30-60s on a small VM.

set -euo pipefail

WINDOW_SECONDS=180
REPO="${TRADE_REPO:-/home/higgack/stock-trade}"

# --- Telegram notify helper -------------------------------------------
TRADE_BOT_TOKEN=""
TRADE_CHANNEL_CHAT_IDS=""
if [ -f "$REPO/.env" ]; then
    set +u; set -a
    # shellcheck disable=SC1091
    source "$REPO/.env" || true
    set +a; set -u
fi

notify() {
    local text="$1"
    if [ -z "${TRADE_BOT_TOKEN:-}" ] || [ -z "${TRADE_CHANNEL_CHAT_IDS:-}" ]; then
        return 0
    fi
    local chat_id="${TRADE_CHANNEL_CHAT_IDS%%,*}"
    curl -s -m 10 \
        -X POST "https://api.telegram.org/bot${TRADE_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "parse_mode=HTML" \
        >/dev/null 2>&1 || true
}
# ----------------------------------------------------------------------

RECENT_START=$(journalctl -u trade-bot --since "${WINDOW_SECONDS} seconds ago" \
    2>/dev/null | grep -c "trade-bot starting" || true)

if [ "$RECENT_START" -gt 0 ]; then
    echo "trade-bot-watchdog: bot started within last ${WINDOW_SECONDS}s — skipping"
    exit 0
fi

RECENT=$(journalctl -u trade-bot --since "${WINDOW_SECONDS} seconds ago" \
    2>/dev/null | grep -c "getUpdates" || true)

if [ "$RECENT" = "0" ]; then
    echo "trade-bot-watchdog: no Telegram polling for ${WINDOW_SECONDS}s — restarting"
    notify "⚠️ <b>trade-bot hang 감지</b>: Telegram polling ${WINDOW_SECONDS}초 멈춤 — 재시작 시도"
    /bin/systemctl restart trade-bot
fi
