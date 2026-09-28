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
TRADE_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^(trade/|bot/|deploy/(trade-auto-update\.sh|trade-watchdog\.sh|trade-bot[^/]*\.(service|timer))$)' || true)
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
# ⚠️ 리스너가 **import 하는** trade 모듈이 바뀌어도 재시작한다(실수 #423 — 옛 규칙은
# 스크립트 파일만 봐서, 리스너가 최상위에서 import 하는 `trade/tg_entities.py`(#258
# FloodWait 처리)·`trade/listener_health.py`·패키지 `__init__.py` 만 바뀐 배포는 옛 코드로
# 계속 돌았다 — 형제 나쁜양파 리스너가 #411 에서 고친 그 병이다, #38). 규칙은 나쁜양파와
# 같다. `bot.daily_kr_flow` 는 `--why` 진단(`_why`)에서만 부르므로 재시작 조건 밖이다.
BEON_LISTENER_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/scripts/listen_beon\.py$|^trade/scripts/__init__\.py$|^trade/[^/]+\.py$' || true)
LISTENER_NOTE=""
if [ -n "$BEON_LISTENER_RELEVANT" ]; then
    if sudo -n /bin/systemctl restart trade-bot-beon-listener 2>/dev/null; then
        echo "trade-bot-update: also restarted trade-bot-beon-listener"
        LISTENER_NOTE=$'\n'"<i>+ BeOn 리스너 재시작</i>"
    else
        echo "trade-bot-update: beon-listener restart skipped (no sudoers entry)"
        LISTENER_NOTE=$'\n'"<i>⚠️ 리스너 재시작 권한 없음 — 다음 배포에서 sudoers 자동 설치 후 재시도</i>"
    fi
fi

# Best-effort: 나쁜양파(대만) 리스너 재시작 — listen_badonion.py 변경 시
# (BeOn 리스너와 동일 클래스, 사용자 2026-07-10). sudoers 항목은
# install-trade-units.sh 가 자기확장 설치.
# ⚠️ 리스너가 **import 하는** 모듈(관련성 필터 `badonion_sources` 와 그 파서들 ·
# 재게시 보증 `relay_origins` · `tg_entities`)이 바뀌어도 재시작한다(실수 #411 —
# 옛 판은 스크립트 파일만 봐서, 보증 형식이 바뀌면 리스너가 옛 형식으로 쓰고 새
# 봇이 못 읽어 재게시 글을 버렸을 것이다). trade 최상위 모듈 규칙(`^trade/[^/]+\.py$`)
# 이고, 리스너의 trade.* import 폐포(진입점 `-m trade.scripts.listen_badonion` 이 실행하는
# `trade/scripts/__init__.py` 포함 — 독립 리뷰 #411 L9)가 이 정규식에 걸리는지 회귀가 잰다
# (`tests/test_restart_closure_20260928.py` — 상시 유닛 전부 공용, #423). ⚠️ `trade/*.py` 는
# 폐포보다 넓다(대시보드 모듈도 걸린다) — 리스너 재시작은 몇 초라 그 사이 올라온 글은 주기
# sync 가 회수한다. `bot.market`(종목 링크 렌더)은 리스너 경로가 부르지 않아 조건 밖이다.
BADONION_LISTENER_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/scripts/listen_badonion\.py$|^trade/scripts/__init__\.py$|^trade/[^/]+\.py$' || true)
if [ -n "$BADONION_LISTENER_RELEVANT" ]; then
    if sudo -n /bin/systemctl restart trade-bot-badonion-listener 2>/dev/null; then
        echo "trade-bot-update: also restarted trade-bot-badonion-listener"
        LISTENER_NOTE="${LISTENER_NOTE}"$'\n'"<i>+ 나쁜양파 리스너 재시작</i>"
    else
        echo "trade-bot-update: badonion-listener restart skipped (no sudoers entry)"
        LISTENER_NOTE="${LISTENER_NOTE}"$'\n'"<i>⚠️ 나쁜양파 리스너 재시작 권한 없음 — 다음 배포에서 sudoers 자동 설치 후 재시도</i>"
    fi
fi

# Best-effort: also restart the dashboard server when the change set
# touches its code. Requires a sudoers entry; logs and continues if
# the entry is missing so the main deploy doesn't fail because of it.
# 2026-06-12: trade/*.py 전체로 확대 (NOAH #263 동일 클래스) — dashboard 가
# industry/customs_provisional/heatmap/customs_scan 등 top-level 모듈을
# import 하므로 dashboard.py 만 감시하면 그 모듈 변경(MoM 컬럼·(잠정)
# 라벨·히트맵)이 장기실행 서버에 영원히 미적용되던 것.
# 2026-09-28(실수 #423): `bot/*.py` · `trade/scripts/*.py` · `trade/data/` 도. 대시보드는
# 함수 안에서 `bot.dart_client`(기업 리포트·DART 매출) · `bot.market` · `bot.daily_kr_flow` 를,
# `trade/llm_usage.py` 를 거쳐 `bot.usage_tracker` 를, 리포트·DART 매출 경로에서
# `trade.scripts.customs_alert`·`probe_dart_revenue` 등을 부른다 — #422 fix(aae6ea6:
# bot/dart_client · bot/env_keys)가 base 에 들어간 뒤에도 이 서버는 재시작되지 않아 옛
# 코드로 돌았고 사용자가 손으로 재시작했다. `trade/data/` 는 코드는 아니지만 메모리에
# 캐시된다(`mti_companies._REINFORCE_APPROVED_CACHE` 는 오버레이 mtime 만 보고 repo CSV
# 의 변경은 안 본다). NOAH 대시보드와 같은 정책(무상태·몇 초라 닿는 가족 전체 — 코드와
# `data/` — 를 덮는다)이고, 이 프로세스의 import 폐포가 규칙에 걸리는지
# `tests/test_restart_closure_20260928.py` 가 소스에서 잰다.
DASHBOARD_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/[^/]+\.py$|^trade/scripts/[^/]+\.py$|^trade/data/|^bot/[^/]+\.py$|^deploy/trade-bot-dashboard.*\.(service|timer)$' || true)
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
