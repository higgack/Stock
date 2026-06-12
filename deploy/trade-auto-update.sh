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
# 2026-06-11 통합: trade 코드가 메인 deploy 브랜치(xqYf7)로 합쳐짐 —
# 이제 두 봇이 같은 브랜치를 추적(한 PR 플로우). 옛 zQsi2 는 은퇴.
BRANCH="${TRADE_BRANCH:-claude/stock-trading-automation-xqYf7}"

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
# SILENTLY — 채널 알림 없음 (사용자 2026-06-11: NOAH 쪽 변경이 수출입
# 채널로 알람 오던 것 차단). 추적은 journald 로그로만.
CHANGED_FILES=$(git diff --name-only "$LOCAL" "$REMOTE")
TRADE_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^(trade/|deploy/(trade-auto-update\.sh|trade-watchdog\.sh|trade-bot[^/]*\.(service|timer))$)' || true)
if [ -z "$TRADE_RELEVANT" ]; then
    echo "trade-bot-update: non-trade-bot changes (${LOCAL_SHORT} → ${REMOTE_SHORT}: ${SUBJECT}) — silent pull, no restart/notify"
    git reset --hard "origin/${BRANCH}" --quiet
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
# install-trade-units.sh itself is in the trigger set so that updating
# the installer (e.g. adding a new sudoers line or auto-enable rule)
# applies on the next deploy tick without needing to also touch a unit
# file.
UNIT_FILES_CHANGED=$(echo "$CHANGED_FILES" | grep -E '^deploy/(trade-bot[^/]*\.(service|timer)|install-trade-units\.sh)$' || true)
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

# Best-effort: BeOn 리스너 재시작 — listen_beon.py 변경 시 (2026-06-11).
# 리스너는 별도 상시 서비스라 trade-bot 재시작으로는 새 코드가 로드되지
# 않음 ('배포 완료 ≠ 프로세스에 로드' 클래스, FloodWait fix 가 안 실리던
# 케이스). sudoers 항목은 install-trade-units.sh 가 자기확장 설치.
LISTENER_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/scripts/listen_beon\.py$' || true)
LISTENER_NOTE=""
if [ -n "$LISTENER_RELEVANT" ]; then
    if sudo -n /bin/systemctl restart trade-bot-beon-listener 2>/dev/null; then
        echo "trade-bot-update: also restarted trade-bot-beon-listener"
        LISTENER_NOTE=$'\n'"<i>+ BeOn 리스너 재시작</i>"
    else
        echo "trade-bot-update: beon-listener restart skipped (no sudoers entry)"
        LISTENER_NOTE=$'\n'"<i>⚠️ 리스너 재시작 권한 없음 — 다음 배포에서 sudoers 자동 설치 후 재시도</i>"
    fi
fi

# Best-effort: also restart the dashboard server when the change set
# touches its code. Requires a sudoers entry; logs and continues if
# the entry is missing so the main deploy doesn't fail because of it.
# 2026-06-12: trade/*.py 전체로 확대 (NOAH #263 동일 클래스) — dashboard 가
# industry/customs_provisional/heatmap/customs_scan 등 top-level 모듈을
# import 하므로 dashboard.py 만 감시하면 그 모듈 변경(MoM 컬럼·(잠정)
# 라벨·히트맵)이 장기실행 서버에 영원히 미적용되던 것.
DASHBOARD_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/[^/]+\.py$|^deploy/trade-bot-dashboard.*\.(service|timer)$' || true)
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
    msg="${msg}${INSTALL_NOTE}${DASH_NOTE}${LISTENER_NOTE}"
    notify "$msg"
    echo "trade-bot-update: restart complete"
else
    notify "❌ <b>배포 실패</b>: trade-bot 서비스가 재시작 후 active 상태가 아님 (${REMOTE_SHORT})"
    exit 1
fi
