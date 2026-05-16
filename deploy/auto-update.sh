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

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # nothing to do
fi

LOCAL_SHORT_PRE="${LOCAL:0:7}"
REMOTE_SHORT_PRE="${REMOTE:0:7}"
SUBJECT_PRE="$(git log -1 --format='%s' "$REMOTE" 2>/dev/null || echo '')"

# Scope guard — restart NOAH only when commits actually touch
# stock-bot's runtime files. Trade-bot commits, docs, and shared infra
# (arriving on this branch via PR merges) pull silently but still
# emit a 📝 notification so the operator can track every commit
# landing on the host.
CHANGED_FILES=$(git diff --name-only "$LOCAL" "$REMOTE")
STOCK_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^(bot/|TradingAgents/|requirements\.txt$|deploy/(auto-update\.sh|watchdog\.sh|stock-bot[^/]*\.(service|timer))$)' || true)
if [ -z "$STOCK_RELEVANT" ]; then
    echo "stock-bot-update: non-stock-bot changes — pull + 📝 notify (no restart)"
    git reset --hard "origin/${BRANCH}" --quiet
    note_msg="📝 <b>운영 업데이트</b>: <code>${LOCAL_SHORT_PRE}</code> → <code>${REMOTE_SHORT_PRE}</code>"
    if [ -n "$SUBJECT_PRE" ]; then
        note_msg="${note_msg}"$'\n'"${SUBJECT_PRE}"
    fi
    note_msg="${note_msg}"$'\n'"<i>재시작 불필요 — doc / 다른 서브프로젝트 변경</i>"
    notify "$note_msg"
    exit 0
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
