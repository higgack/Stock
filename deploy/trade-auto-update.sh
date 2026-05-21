#!/bin/bash
# Auto-update trade-bot from origin and restart on new commits.
# Triggered by trade-bot-update.timer every couple of minutes.
#
# Mirrors deploy/auto-update.sh but operates on a SEPARATE clone of the
# repo at $REPO (defaults to ~/stock-trade) so it can sit on its own
# branch without thrashing the stock-bot's working tree. Restarts only
# the trade-bot service.

set -euo pipefail

REPO="${TRADE_REPO:-/home/higgack/stock-trade}"
BRANCH="${TRADE_BRANCH:-claude/export-import-dashboard-zQsi2}"

cd "$REPO"

# Pull bot creds from .env so deploy notifications can land in the trade
# channel. Falls back silently when token / channel are unset.
TRADE_BOT_TOKEN=""
TRADE_CHANNEL_CHAT_IDS=""
if [ -f .env ]; then
    set +u; set -a
    # shellcheck disable=SC1091
    source .env || true
    set +a; set -u
fi

notify() {
    local text="$1"
    if [ -z "${TRADE_BOT_TOKEN:-}" ] || [ -z "${TRADE_CHANNEL_CHAT_IDS:-}" ]; then
        return 0
    fi
    local chat_id="${TRADE_CHANNEL_CHAT_IDS%%,*}"
    local response
    response=$(curl -s -m 10 \
        -X POST "https://api.telegram.org/bot${TRADE_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "parse_mode=HTML" 2>&1) || true
    echo "trade-bot-update: notify response: ${response:0:200}"
}

git fetch --quiet origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

# VM 직접 push edge case (auto-update.sh 와 동일 패턴, 2026-05-21).
# LOCAL==REMOTE 인데 봇이 commit 보다 먼저 실행됐으면 재시작.
if [ "$LOCAL" = "$REMOTE" ]; then
    HEAD_TS=$(git log -1 --format=%ct HEAD 2>/dev/null || echo "")
    BOT_START_STR=$(systemctl show trade-bot --property=ExecMainStartTimestamp --value 2>/dev/null)
    BOT_TS=""
    if [ -n "$BOT_START_STR" ]; then
        BOT_TS=$(date -d "$BOT_START_STR" +%s 2>/dev/null || echo "")
    fi
    if [ -n "$HEAD_TS" ] && [ -n "$BOT_TS" ] && [ "$HEAD_TS" -gt "$BOT_TS" ]; then
        echo "trade-bot-update: LOCAL==REMOTE but HEAD newer than bot start — restarting"
        sudo /bin/systemctl restart trade-bot && sleep 3
        exit 0
    fi
    exit 0
fi

LOCAL_SHORT="${LOCAL:0:7}"
REMOTE_SHORT="${REMOTE:0:7}"
SUBJECT="$(git log -1 --format='%s' "$REMOTE" 2>/dev/null || echo '')"

echo "trade-bot-update: pulling ${LOCAL_SHORT} → ${REMOTE_SHORT}"

start_msg="🚀 <b>배포 시작</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
if [ -n "$SUBJECT" ]; then
    start_msg="${start_msg}"$'\n'"${SUBJECT}"
fi
notify "$start_msg"

if ! git reset --hard "origin/${BRANCH}" --quiet; then
    notify "❌ <b>배포 실패</b>: git reset --hard (${LOCAL_SHORT} → ${REMOTE_SHORT})"
    exit 1
fi

if ! sudo /bin/systemctl restart trade-bot; then
    notify "❌ <b>배포 실패</b>: systemctl restart (${REMOTE_SHORT})"
    exit 1
fi

sleep 3
if systemctl is-active --quiet trade-bot; then
    msg="✅ <b>배포 완료</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
    if [ -n "$SUBJECT" ]; then
        msg="${msg}"$'\n'"${SUBJECT}"
    fi
    notify "$msg"
    echo "trade-bot-update: restart complete"
else
    notify "❌ <b>배포 실패</b>: trade-bot 서비스가 재시작 후 active 상태가 아님 (${REMOTE_SHORT})"
    exit 1
fi
