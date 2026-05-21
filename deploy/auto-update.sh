#!/bin/bash
# Auto-update stock-bot from origin and restart on new commits.
# Triggered by stock-bot-update.timer every couple of minutes.

set -euo pipefail

REPO=/home/higgack/stock
BRANCH=claude/stock-trading-automation-xqYf7
BUSY_MARKER=/home/higgack/.tradingagents/.busy
# If the busy marker is older than this, treat it as stale (bot crashed
# without cleaning up) and proceed with the restart anyway.
STALE_AFTER_MINUTES=20

cd "$REPO"

# Pull bot creds from .env so we can post deploy result notifications.
# Done in a subshell-equivalent block with set -a so any KEY=VAL pairs
# become exported for this script's lifetime, then turned back off.
TELEGRAM_BOT_TOKEN=""
CHANNEL_CHAT_IDS=""
if [ -f .env ]; then
    set +u
    set -a
    # shellcheck disable=SC1091
    source .env || true
    set +a
    set -u
fi

notify() {
    local text="$1"
    local has_token has_chan
    has_token=$([ -n "${TELEGRAM_BOT_TOKEN:-}" ] && echo "yes" || echo "no")
    has_chan=$([ -n "${CHANNEL_CHAT_IDS:-}" ] && echo "yes" || echo "no")
    echo "stock-bot-update: notify token=${has_token} channel=${has_chan}"
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${CHANNEL_CHAT_IDS:-}" ]; then
        return 0
    fi
    # CHANNEL_CHAT_IDS may be a comma-separated list — pick the first
    local chat_id="${CHANNEL_CHAT_IDS%%,*}"
    local response
    response=$(curl -s -m 10 \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "parse_mode=HTML" 2>&1) || true
    # Log first ~200 chars of the response so we can diagnose silent failures.
    echo "stock-bot-update: notify response: ${response:0:200}"
}

git fetch --quiet origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

# Edge case: user pushed directly from VM (with PAT) so LOCAL == REMOTE
# already, but the running bot was started BEFORE this commit. Detect
# by comparing bot's process start time vs HEAD commit time — if HEAD
# is newer, restart the bot even though no git pull is needed.
# 2026-05-21 user reported: 53bc3cc pushed from VM → local already at
# HEAD → auto-update exited with 'nothing to do' → bot ran stale code.
if [ "$LOCAL" = "$REMOTE" ]; then
    HEAD_TS=$(git log -1 --format=%ct HEAD 2>/dev/null || echo "")
    BOT_START_STR=$(systemctl show stock-bot --property=ExecMainStartTimestamp --value 2>/dev/null)
    BOT_TS=""
    if [ -n "$BOT_START_STR" ]; then
        BOT_TS=$(date -d "$BOT_START_STR" +%s 2>/dev/null || echo "")
    fi
    if [ -n "$HEAD_TS" ] && [ -n "$BOT_TS" ] && [ "$HEAD_TS" -gt "$BOT_TS" ]; then
        echo "stock-bot-update: LOCAL==REMOTE but HEAD ($HEAD_TS) newer than bot start ($BOT_TS) — restarting"
        notify "🔁 <b>봇 재시작</b>: VM 직접 push 감지 (HEAD $(git rev-parse --short HEAD) 이 봇 실행 후 commit). 코드 새로고침."
        if sudo /bin/systemctl restart stock-bot; then
            sleep 3
            if systemctl is-active --quiet stock-bot; then
                notify "✅ <b>봇 재시작 완료</b>"
            else
                notify "❌ <b>봇 재시작 실패</b>"
            fi
        fi
        exit 0
    fi
    exit 0  # nothing to do
fi

if [ -f "$BUSY_MARKER" ]; then
    # find prints the marker only if it is older than the threshold
    if [ -z "$(find "$BUSY_MARKER" -mmin +"$STALE_AFTER_MINUTES" 2>/dev/null)" ]; then
        echo "stock-bot-update: analysis in progress, deferring restart"
        exit 0
    fi
    echo "stock-bot-update: stale busy marker (>${STALE_AFTER_MINUTES}m), proceeding"
fi

LOCAL_SHORT="${LOCAL:0:7}"
REMOTE_SHORT="${REMOTE:0:7}"
SUBJECT="$(git log -1 --format='%s' "$REMOTE" 2>/dev/null || echo '')"

echo "stock-bot-update: pulling ${LOCAL_SHORT} → ${REMOTE_SHORT}"

start_msg="🚀 <b>배포 시작</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
if [ -n "$SUBJECT" ]; then
    start_msg="${start_msg}"$'\n'"${SUBJECT}"
fi
notify "$start_msg"

# Force-sync to origin. Any local edits (e.g. an admin running 'sed -i'
# on a deploy script) get overwritten — auto-deploy assumes origin is
# the source of truth and we never want a half-applied state.
if ! git reset --hard "origin/${BRANCH}" --quiet; then
    notify "❌ <b>배포 실패</b>: git reset --hard (${LOCAL_SHORT} → ${REMOTE_SHORT})"
    exit 1
fi

if ! sudo /bin/systemctl restart stock-bot; then
    notify "❌ <b>배포 실패</b>: systemctl restart (${REMOTE_SHORT})"
    exit 1
fi

# Give systemd a moment to actually start the new process before we check.
sleep 3
if systemctl is-active --quiet stock-bot; then
    msg="✅ <b>배포 완료</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
    if [ -n "$SUBJECT" ]; then
        msg="${msg}"$'\n'"${SUBJECT}"
    fi
    notify "$msg"
    echo "stock-bot-update: restart complete"
else
    notify "❌ <b>배포 실패</b>: stock-bot 서비스가 재시작 후 active 상태가 아님 (${REMOTE_SHORT})"
    exit 1
fi
