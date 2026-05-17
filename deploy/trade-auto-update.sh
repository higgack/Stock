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

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

LOCAL_SHORT="${LOCAL:0:7}"
REMOTE_SHORT="${REMOTE:0:7}"
SUBJECT="$(git log -1 --format='%s' "$REMOTE" 2>/dev/null || echo '')"

# Scope guard — restart trade-bot only when commits actually touch
# its runtime files. Doc-only / stock-bot / shared-infra updates pull
# silently but ALWAYS send a 📝 notification to the trade channel so
# the operator can track every new commit landing on the host.
CHANGED_FILES=$(git diff --name-only "$LOCAL" "$REMOTE")
TRADE_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^(trade/|deploy/(trade-auto-update\.sh|trade-watchdog\.sh|trade-bot[^/]*\.(service|timer))$)' || true)
if [ -z "$TRADE_RELEVANT" ]; then
    echo "trade-bot-update: non-trade-bot changes — pull + 📝 notify (no restart)"
    git reset --hard "origin/${BRANCH}" --quiet
    note_msg="📝 <b>운영 업데이트</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
    if [ -n "$SUBJECT" ]; then
        note_msg="${note_msg}"$'\n'"${SUBJECT}"
    fi
    note_msg="${note_msg}"$'\n'"<i>재시작 불필요 — doc / 다른 서브프로젝트 변경</i>"
    notify "$note_msg"
    exit 0
fi

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

# Auto-install any new / changed systemd unit files. install-trade-units.sh
# is idempotent — copies what differs, daemon-reloads, enables new timers,
# restarts running services with changed unit files. Requires a sudoers
# entry (one-time, see trade/README.md); gracefully degrades when missing.
INSTALL_NOTE=""
UNIT_FILES_CHANGED=$(echo "$CHANGED_FILES" | grep -E '^deploy/trade-bot[^/]*\.(service|timer)$' || true)
if [ -n "$UNIT_FILES_CHANGED" ]; then
    if INSTALL_OUTPUT=$(sudo -n "$REPO/deploy/install-trade-units.sh" 2>&1); then
        echo "$INSTALL_OUTPUT"
        SUMMARY=$(echo "$INSTALL_OUTPUT" | grep -oE 'SUMMARY .*$' | head -1)
        if [ -n "$SUMMARY" ]; then
            INSTALL_NOTE=$'\n'"<i>+ systemd: ${SUMMARY}</i>"
        else
            INSTALL_NOTE=$'\n'"<i>+ systemd: 자동 설치 완료</i>"
        fi
    else
        echo "trade-bot-update: install-trade-units.sh failed"
        INSTALL_NOTE=$'\n'"<i>⚠️ systemd 자동 설치 권한 없음 — sudoers에 install-trade-units.sh 추가</i>"
    fi
fi

if ! sudo /bin/systemctl restart trade-bot; then
    notify "❌ <b>배포 실패</b>: systemctl restart (${REMOTE_SHORT})"
    exit 1
fi

# Best-effort: also restart the dashboard server when the change set
# touches its code. Requires a sudoers entry; logs and continues if
# the entry is missing so the main deploy doesn't fail because of it.
DASHBOARD_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/dashboard(_server)?\.py$|^deploy/trade-bot-dashboard.*\.(service|timer)$' || true)
DASH_NOTE=""
if [ -n "$DASHBOARD_RELEVANT" ]; then
    if sudo -n /bin/systemctl restart trade-bot-dashboard 2>/dev/null; then
        echo "trade-bot-update: also restarted trade-bot-dashboard"
        DASH_NOTE=$'\n'"<i>+ trade-bot-dashboard 재시작</i>"
    else
        echo "trade-bot-update: trade-bot-dashboard restart skipped (no sudoers entry)"
        DASH_NOTE=$'\n'"<i>⚠️ dashboard 재시작 권한 없음 — sudoers에 'restart trade-bot-dashboard' 추가 필요</i>"
    fi
fi

sleep 3
if systemctl is-active --quiet trade-bot; then
    msg="✅ <b>배포 완료</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
    if [ -n "$SUBJECT" ]; then
        msg="${msg}"$'\n'"${SUBJECT}"
    fi
    msg="${msg}${INSTALL_NOTE}${DASH_NOTE}"
    notify "$msg"
    echo "trade-bot-update: restart complete"
else
    notify "❌ <b>배포 실패</b>: trade-bot 서비스가 재시작 후 active 상태가 아님 (${REMOTE_SHORT})"
    exit 1
fi
