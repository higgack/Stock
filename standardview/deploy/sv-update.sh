#!/bin/bash
# Standard View auto-update — mirrors NOAH's deploy/auto-update.sh pattern.
#
# Triggered by sv-update.timer every 1 minute. Pulls latest stock repo,
# if standardview/* paths changed, rsyncs canonical files into
# ~/standardview/ live tree, restarts backend service if needed, and
# notifies via Telegram. Defers when daily_generator.py is running.

set -euo pipefail

REPO=/home/higgack/stock
BRANCH=claude/stock-trading-automation-xqYf7
LIVE=/home/higgack/standardview
BUSY_MARKER=/home/higgack/.standardview/.daily_generator_busy
STALE_AFTER_MINUTES=20

cd "$REPO"

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
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${CHANNEL_CHAT_IDS:-}" ]; then
        return 0
    fi
    local chat_id="${CHANNEL_CHAT_IDS%%,*}"
    curl -s -m 10 \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "parse_mode=HTML" >/dev/null 2>&1 || true
}

git fetch --quiet origin "$BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

# VM 직접 push edge case (auto-update.sh 와 동일 패턴, 2026-05-21):
# LOCAL==REMOTE 라도 canonical (REPO/standardview/*) 과 LIVE
# (~/standardview/*) 의 directory diff 검사해서 다르면 force re-sync.
# Drift 가 user 의 수동 sed / 이전 timer 우회 / git push 외의 변경
# 으로 발생했을 가능성. notify + 모든 sub 강제 sync.
DRIFT_RESYNC=0
if [ "$LOCAL" = "$REMOTE" ]; then
    # rsync --dry-run 으로 canonical → LIVE 방향만 검사 (LIVE 에만
    # 있는 파일 — *.bak / .env.bak / 로그 — 은 무시. 이전 'diff -r
    # --brief' 가 LIVE-only file 도 drift 로 잡아서 무한 루프 발생
    # 2026-05-21 fix).
    for sub in scripts backend; do
        if [ -d "$REPO/standardview/$sub" ] && [ -d "$LIVE/$sub" ]; then
            CHANGES=$(rsync -avc --dry-run --itemize-changes \
                --exclude='__pycache__/' --exclude='*.pyc' \
                --exclude='*.bak.*' --exclude='*.bak' \
                "$REPO/standardview/$sub/" "$LIVE/$sub/" 2>/dev/null \
                | grep -E '^[<>ch]f' || true)
            if [ -n "$CHANGES" ]; then
                DRIFT_RESYNC=1
                break
            fi
        fi
    done
    if [ "$DRIFT_RESYNC" = "0" ]; then
        exit 0
    fi
    echo "sv-update: LOCAL==REMOTE but live↔canonical drift — re-syncing"
fi

# Only proceed if the new commits actually touched standardview/* paths.
# DRIFT_RESYNC=1 이면 git diff 가 empty 여도 rsync 강행.
CHANGED_FILES=$(git diff --name-only "$LOCAL" "$REMOTE" -- standardview/ 2>/dev/null || true)
if [ -z "$CHANGED_FILES" ] && [ "$DRIFT_RESYNC" = "0" ]; then
    # NOAH-only commit; just fast-forward git (no SV redeploy) so the
    # next SV-touching commit sees a clean diff.
    git reset --hard "origin/${BRANCH}" --quiet
    exit 0
fi

if [ -f "$BUSY_MARKER" ]; then
    if [ -z "$(find "$BUSY_MARKER" -mmin +"$STALE_AFTER_MINUTES" 2>/dev/null)" ]; then
        echo "sv-update: daily_generator running, deferring"
        exit 0
    fi
    echo "sv-update: stale busy marker (>${STALE_AFTER_MINUTES}m), proceeding"
fi

LOCAL_SHORT="${LOCAL:0:7}"
REMOTE_SHORT="${REMOTE:0:7}"
SUBJECT="$(git log -1 --format='%s' "$REMOTE" 2>/dev/null || echo '')"
echo "sv-update: deploying ${LOCAL_SHORT} → ${REMOTE_SHORT}"
notify "🚀 <b>SV 배포 시작</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>
${SUBJECT}"

if ! git reset --hard "origin/${BRANCH}" --quiet; then
    notify "❌ <b>SV 배포 실패</b>: git reset (${REMOTE_SHORT})"
    exit 1
fi

# Track whether backend changed (restart needed) vs scripts only (no restart).
BACKEND_CHANGED=0
SCRIPTS_CHANGED=0
DEPLOY_CHANGED=0
if [ "$DRIFT_RESYNC" = "1" ]; then
    # Drift detected — force rsync all subtrees + run install.sh.
    BACKEND_CHANGED=1
    SCRIPTS_CHANGED=1
    DEPLOY_CHANGED=1
    notify "🔁 <b>SV drift re-sync</b>: live↔canonical 불일치 → 전체 rsync"
else
    echo "$CHANGED_FILES" | grep -q '^standardview/backend/' && BACKEND_CHANGED=1
    echo "$CHANGED_FILES" | grep -q '^standardview/scripts/' && SCRIPTS_CHANGED=1
    echo "$CHANGED_FILES" | grep -qE '^standardview/deploy/.*\.(service|timer|sh)$' && DEPLOY_CHANGED=1
fi

# Rsync canonical files into live tree. Each subtree only if present
# in repo + actually changed; --checksum guards against false-positive
# mtime mismatch after git checkout.
RSYNC_OPTS="-a --checksum --no-times"

if [ -d "$REPO/standardview/scripts" ] && [ "$SCRIPTS_CHANGED" = "1" ]; then
    mkdir -p "$LIVE/scripts"
    rsync $RSYNC_OPTS \
        "$REPO/standardview/scripts/" "$LIVE/scripts/" || {
        notify "❌ <b>SV 배포 실패</b>: rsync scripts (${REMOTE_SHORT})"
        exit 1
    }
fi
if [ -d "$REPO/standardview/backend" ] && [ "$BACKEND_CHANGED" = "1" ]; then
    mkdir -p "$LIVE/backend"
    rsync $RSYNC_OPTS \
        --exclude='__pycache__/' --exclude='*.pyc' \
        "$REPO/standardview/backend/" "$LIVE/backend/" || {
        notify "❌ <b>SV 배포 실패</b>: rsync backend (${REMOTE_SHORT})"
        exit 1
    }
fi

# Restart backend service only if backend code changed. Service name
# may vary across installs; try the canonical name first, fall back to
# detected match. Failures are non-fatal (rsync already shipped).
if [ "$BACKEND_CHANGED" = "1" ]; then
    BACKEND_SVC=""
    for cand in standardview-backend standardview-api standard-view-backend; do
        if systemctl list-unit-files "${cand}.service" --no-legend 2>/dev/null | grep -q .; then
            BACKEND_SVC="$cand"
            break
        fi
    done
    if [ -z "$BACKEND_SVC" ]; then
        # Last-resort discovery — any active service whose name contains
        # 'standardview' AND 'backend|api'.
        BACKEND_SVC=$(systemctl list-units --type=service --state=running --no-legend 2>/dev/null \
            | awk '{print $1}' | grep -i 'standardview.*\(backend\|api\)' | head -1 | sed 's/\.service$//')
    fi
    if [ -n "$BACKEND_SVC" ]; then
        sudo /bin/systemctl restart "$BACKEND_SVC" || true
        sleep 3
        if systemctl is-active --quiet "$BACKEND_SVC"; then
            notify "✅ <b>SV 백엔드 재시작</b>: <code>${BACKEND_SVC}</code>"
        else
            notify "⚠️ <b>SV 백엔드 재시작 실패</b>: <code>${BACKEND_SVC}</code>"
        fi
    else
        notify "⚠️ <b>SV 백엔드 서비스 미탐지</b> — backend 변경됐지만 재시작 안 함"
    fi
fi

# systemd unit files (deploy/*.{service,timer,sh}) — sudo install.sh
# 로 재설치 + daemon-reload + enable. user 가 install.sh 에 NOPASSWD
# 부여한 경우에만 작동 (없으면 silent skip).
if [ "$DEPLOY_CHANGED" = "1" ]; then
    INSTALL_SH="$REPO/standardview/deploy/install.sh"
    if [ -x "$INSTALL_SH" ]; then
        if sudo -n "$INSTALL_SH" >/tmp/sv-install.log 2>&1; then
            notify "✅ <b>SV systemd 자동 재설치</b>: install.sh 성공"
        else
            notify "⚠️ <b>SV systemd 재설치 실패</b> (NOPASSWD 미설정 또는 install.sh error). 로그: /tmp/sv-install.log"
        fi
    fi
fi

notify "✅ <b>SV 배포 완료</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>
${SUBJECT}"
echo "sv-update: deploy complete"
