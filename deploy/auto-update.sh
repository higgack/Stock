#!/bin/bash
# Auto-update stock-bot from origin and restart on new commits.
# Triggered by stock-bot-update.timer every couple of minutes.

set -euo pipefail

# 기본값이 운영 경로다. 환경 변수는 회귀가 임시 저장소에서 이 스크립트를 실제로
# 돌려 보려고 둔 것(`tests/test_restart_closure_20260928.py`, 실수 #423 — 형제
# trade-auto-update.sh 의 TRADE_REPO/TRADE_BRANCH 와 같은 방식). 유닛은 설정하지 않는다.
REPO="${STOCK_REPO:-/home/higgack/stock}"
BRANCH="${STOCK_BRANCH:-claude/stock-trading-automation-xqYf7}"
BUSY_MARKER="${STOCK_BUSY_MARKER:-/home/higgack/.tradingagents/.busy}"
# install.sh 출력 로그 — 회귀가 이 경로를 임시 디렉터리로 돌려 `deploy/` 변경 경로를 실제로 태운다
# (실수 #423 독립 리뷰 L⑥ — 옛 판은 /tmp 고정이라, 태우면 호스트에 쓰게 돼 회귀가 그 경로를 피했다).
INSTALL_LOG="${STOCK_INSTALL_LOG:-/tmp/stock-bot-install.log}"
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

# Restart the dashboard HTTP server. auto-update only restarts stock-bot
# by default; dashboard server-layer code (bot/dashboard_server.py /
# bot/dashboard.py / bot/archive.py) changes need a dashboard cycle to
# take effect (e.g. Cache-Control header, /api delete endpoints, regen
# imports). 2026-06-03 사용자 요청 — Cache-Control no-cache 적용이 서버
# 코드라 자동 배포 대상에 포함. Graceful skip when the NOPASSWD sudoers
# line is absent (install.sh provisions it; legacy setups fall back to
# the daily RuntimeMaxSec cycle).
restart_dashboard() {
    if sudo -n /bin/systemctl restart stock-bot-dashboard 2>/dev/null; then
        sleep 2
        if systemctl is-active --quiet stock-bot-dashboard; then
            notify "✅ <b>대시보드 재시작</b>: 서버 코드 변경 반영 (Cache-Control 등)"
        else
            notify "⚠️ <b>대시보드 재시작 후 active 아님</b> — 확인 필요"
        fi
        return 0
    fi
    # 직접 restart 권한 거부 — higgack-stock-restart sudoers drop-in 이
    # 미설치이거나 stock-bot-dashboard 라인이 없는 구버전(2026-06-03 이전).
    # 옛 동작은 echo 한 줄 silent skip 이라, bot/*.py 만 바뀐 배포(예: 뉴스
    # 링크·lookup 캐시클리어 fix)가 장기실행 대시보드 프로세스에 영영 반영
    # 안 돼 사용자가 매번 수동 재시작해야 했음 — silent-fail 근본원인(실수
    # 기록 #11 배포≠화면 / #12d silent-except 금지). self-heal: install.sh 를
    # sudo -n(higgack-stock-deploy NOPASSWD)으로 실행 → sudoers drop-in
    # 재설치(stock-bot-dashboard restart 라인 포함) + try-restart 동반 →
    # 이후 직접 restart 가 영구 작동. 실패도 더는 조용히 넘기지 않고 notify.
    echo "stock-bot-update: dashboard direct restart denied — install.sh self-heal 시도"
    if [ -x "$REPO/deploy/install.sh" ] \
            && sudo -n "$REPO/deploy/install.sh" >"$INSTALL_LOG" 2>&1 \
            && systemctl is-active --quiet stock-bot-dashboard; then
        notify "✅ <b>대시보드 재시작</b>: sudoers self-heal (install.sh 재설치로 권한 복구·이후 자동)"
    else
        notify "⚠️ <b>대시보드 재시작 실패</b>: restart NOPASSWD 권한 부재. VM 1회 실행 필요: <code>sudo /home/higgack/stock/deploy/install.sh</code>"
    fi
}

# DAJU 리스너(daju-listener.service — Telethon 상시). 옛 판은 배포가 이 프로세스를
# 한 번도 재시작하지 않았다(실수 #423): 파서(`bot/daju_parse.py`)가 바뀌어도 옛
# 파서로 판정해 새 형식 알림을 '무관 메시지' 로 버리고, 메시지마다 부르는 블로그
# 재생성(`bot.dashboard`)도 옛 렌더러로 blog.html 을 덮는다. **돌고 있을 때만**
# 재시작한다 — 미설치·세션 미인증(exit 78)·운영자 중지 상태를 배포가 되살리면 안
# 된다(그 경우 새 코드는 다음 기동 때 로드된다). 재시작 사이에 온 알림은 백필 경로가
# 없어 놓칠 수 있다(형제 trade 리스너와 달리 주기 sync 가 없다) — 옛 코드가 새 형식을
# 영영 버리는 것보다 작은 위험이라 이쪽을 택했다.
# 재시작 뒤엔 **살아 있는지** 본다(독립 리뷰 #423 M1): 옛 판은 배포가 이 프로세스를 안
# 건드려 '낡았지만 살아 있음' 이었는데, 재시작하면 새 코드가 기동에서 죽는 경우가 생긴다
# — `Restart=on-failure`·`RestartSec=15` 라 systemd 는 계속 다시 띄우고 아무도 알리지 않는다.
# 실패는 갈래로 말한다(L5): 권한 줄이 없는 것과 재시작 자체가 실패한 것은 처방이 다르다. 판정이
# sudo 의 영어 문구에 기대므로 `LC_ALL=C` 로 부른다(델타 리뷰 L⑤ — 로캘이 번역하면 권한 부재가
# "재시작 실패" 로 분류된다).
restart_daju_listener() {
    if ! systemctl is-active --quiet daju-listener 2>/dev/null; then
        echo "stock-bot-update: daju-listener 비활성(미설치·미인증·중지) — 재시작 생략, 새 코드는 다음 기동 때 로드"
        return 0
    fi
    local err
    if err=$(LC_ALL=C sudo -n /bin/systemctl restart daju-listener 2>&1); then
        sleep 3
        if systemctl is-active --quiet daju-listener 2>/dev/null; then
            echo "stock-bot-update: also restarted daju-listener"
        else
            echo "stock-bot-update: daju-listener 재시작 뒤 active 아님"
            notify "⚠️ <b>DAJU 리스너 재시작 후 active 아님</b> — 새 코드가 기동에서 실패했을 수 있습니다. 확인: <code>journalctl -u daju-listener -n 30</code>"
        fi
    elif grep -q 'password is required' <<<"$err"; then
        echo "stock-bot-update: daju-listener restart 권한 없음 — install.sh 가 sudoers 를 설치해야 한다"
        notify "⚠️ <b>DAJU 리스너 재시작 실패</b>: restart NOPASSWD 권한 부재 — 다음 deploy/ 변경 때 install.sh 가 설치한다(즉시: <code>sudo /home/higgack/stock/deploy/install.sh</code>)"
    else
        # parse_mode=HTML 이라 `<`·`>`·`&` 를 이스케이프한다(실수 #7). `${err//</&lt;}` 는 쓰지
        # 않는다 — bash 5.2 의 patsub_replacement 가 치환 문자열의 `&` 를 일치한 글자로 바꿔
        # `<lt;` 를 만든다(실측). sed 의 `\&` 는 판에 상관없이 글자 그대로다.
        err=$(printf '%s' "${err:0:200}" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')
        echo "stock-bot-update: daju-listener restart 실패: ${err}"
        notify "⚠️ <b>DAJU 리스너 재시작 실패</b>: <code>${err}</code> — 확인: <code>journalctl -u daju-listener -n 30</code>"
    fi
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
        # VM 직접 push 라 pull range (old → new) 가 없으므로 HEAD SHA 단일
        # 표기. 일반 pull 배포(아래 Path B)와 동일한 '배포 시작/완료 + 커밋
        # subject' 형식으로 통일.
        HEAD_SHORT="$(git rev-parse --short HEAD)"
        SUBJECT="$(git log -1 --format='%s' HEAD 2>/dev/null || echo '')"
        start_msg="🚀 <b>배포 시작</b>: <code>${HEAD_SHORT}</code> (VM 직접 push)"
        if [ -n "$SUBJECT" ]; then
            start_msg="${start_msg}"$'\n'"${SUBJECT}"
        fi
        notify "$start_msg"
        if sudo /bin/systemctl restart stock-bot; then
            sleep 3
            if systemctl is-active --quiet stock-bot; then
                done_msg="✅ <b>배포 완료</b>: <code>${HEAD_SHORT}</code> (VM 직접 push)"
                if [ -n "$SUBJECT" ]; then
                    done_msg="${done_msg}"$'\n'"${SUBJECT}"
                fi
                notify "$done_msg"
                # VM 직접 push 는 diff range 가 없어 대시보드 파일 변경 여부를
                # 가릴 수 없음 → 비용 거의 0 인 stateless 재시작을 항상 동반해
                # 서버-레이어 변경(예: Cache-Control)도 확실히 반영.
                restart_dashboard
                restart_daju_listener
            else
                notify "❌ <b>배포 실패</b>: 재시작 후 active 아님 (${HEAD_SHORT})"
            fi
        else
            notify "❌ <b>배포 실패</b>: systemctl restart (${HEAD_SHORT})"
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
# Detect deploy/*.{service,timer,sh} changes BEFORE git reset so we
# can decide whether install.sh needs to re-deploy systemd units.
# 사용자 정책 2026-05-29: SV 패턴 mirror — sudo install.sh NOPASSWD 1회
# 설정 후 deploy/ 변경 시도 SSH 진입 없이 자동 install + daemon-reload
# + enable. Quiet skip when install.sh missing NOPASSWD (legacy bot
# 호환).
# 변경 목록은 한 번만 구하고 here-string 으로 grep 한다(독립 리뷰 #423 L2·L3): `set -o
# pipefail` 아래 `echo … | grep -q` 는 grep 이 첫 일치에서 끝나면 echo 가 SIGPIPE 로 죽어
# 파이프라인이 실패로 읽힌다 — 목록이 파이프 버퍼(64KiB) 근처를 넘는 배포에서 조건이 조용히
# 0 이 된다(실측: 57KB 에서 10회 중 1회 · 190KB 에서 10회 전부 · here-string 은 전부 잡았다).
# `core.quotePath=false` 는 git 이 비ASCII 경로를 따옴표로 감싸 `^bot/…` 앵커를 빗나가게
# 하지 않으려고다.
PULLED_FILES=$(git -c core.quotePath=false diff --name-only "$LOCAL" "$REMOTE" 2>/dev/null || true)
DEPLOY_CHANGED=0
if grep -qE '^deploy/.*\.(service|timer|sh)$' <<<"$PULLED_FILES"; then
    DEPLOY_CHANGED=1
fi

# Dashboard server-layer — restart when ANY bot/*.py changes (2026-06-11).
# 옛 트리거(4파일 화이트리스트)는 on-demand 페이지가 import 하는 모듈
# (finviz_client/us_pages/naver_*/earnings_calendar 등) 변경을 놓쳐 새
# 코드가 서버 프로세스에 로드되지 않는 drift 발생 (#259/#262 신고저
# fix 가 페이지에 반영 안 되던 실사례 — 실수기록 #11 클래스). 대시보드
# 재시작은 stateless·수 초라 bot/*.py 전체로 넓혀 클래스 자체 제거.
# 2026-09-28(실수 #423): 대시보드는 `trade.kg_candidates` 등 trade 모듈과
# TradingAgents(`tradingagents.agents.utils…`), bot 하위 패키지(`bot/scripts/
# probe_progress.py` · `bot/screener_themes/`)도 import 한다 — 그 변경도 옛 서버에
# 안 실리던 것. trade 가족에 닿으므로 그 가족이 메모리에 캐시하는 `trade/data/` 도
# 덮는다(trade 대시보드와 같은 정책). 같은 체크아웃의 DAJU 리스너(상시)도 이 조건으로
# 재시작한다(옛 판은 배포가 그 프로세스를 한 번도 재시작하지 않았다). 두 프로세스의
# import 폐포가 이 정규식에 걸리는지 `tests/test_restart_closure_20260928.py` 가 잰다.
CODE_CHANGED=0
if grep -qE '^bot/[^/]+\.py$|^bot/(scripts|screener_themes)/[^/]+\.py$|^trade/[^/]+\.py$|^trade/data/|^TradingAgents/tradingagents/.+\.py$' <<<"$PULLED_FILES"; then
    CODE_CHANGED=1
fi

if ! git reset --hard "origin/${BRANCH}" --quiet; then
    notify "❌ <b>배포 실패</b>: git reset --hard (${LOCAL_SHORT} → ${REMOTE_SHORT})"
    exit 1
fi

# systemd unit / shell-script 자동 재설치 — install.sh 가 idempotent.
# user 가 sudoers NOPASSWD line 1회 추가 (`higgack ALL=(root) NOPASSWD:
# /home/higgack/stock/deploy/install.sh`) 한 경우에만 작동, 미설정 시
# silent skip. 로그 파일 (/tmp/stock-bot-install.log) 에 install.sh 출력
# 저장.
if [ "$DEPLOY_CHANGED" = "1" ]; then
    INSTALL_SH="$REPO/deploy/install.sh"
    if [ -x "$INSTALL_SH" ]; then
        if sudo -n "$INSTALL_SH" >"$INSTALL_LOG" 2>&1; then
            notify "✅ <b>stock-bot systemd 자동 재설치</b>: install.sh 성공 (deploy/* 변경 감지)"
        else
            notify "⚠️ <b>stock-bot systemd 재설치 실패</b> (NOPASSWD 미설정 또는 install.sh error). 로그: ${INSTALL_LOG}"
        fi
    fi
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
    # 상시 프로세스가 import 하는 코드가 바뀌었으면 대시보드·DAJU 리스너도 재시작
    # (문서·테스트만 바뀐 배포는 건드리지 않는다 — 위 CODE_CHANGED).
    if [ "$CODE_CHANGED" = "1" ]; then
        echo "stock-bot-update: long-running code changed — restarting dashboard + daju-listener"
        restart_dashboard
        restart_daju_listener
    fi
else
    notify "❌ <b>배포 실패</b>: stock-bot 서비스가 재시작 후 active 상태가 아님 (${REMOTE_SHORT})"
    exit 1
fi
