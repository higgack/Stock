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

# sudo 로 부른 명령이 실패했을 때 알림 한 줄(앞에 줄바꿈). 권한 줄이 없는 것과 명령 자체가 실패한
# 것은 처방이 다르다 — 옛 판은 원인과 상관없이 "권한 없음" 이라 적었다(실수 #423 독립 리뷰 L④:
# NOAH `restart_daju_listener` 의 갈래(L5)를 옮겼다). 판정이 sudo 의 영어 문구에 기대므로 호출부는
# `LC_ALL=C` 로 부른다(L⑤). 원문은 HTML 이스케이프해 싣는다(실수 #7 — sed 인 이유는 auto-update.sh
# 의 같은 줄 주석). 로그 줄은 stderr 로 낸다 — 알림 줄은 호출부가 `$(…)` 로 받는다.
sudo_failure_note() {
    local what="$1" err="$2" check="$3"
    if grep -q 'password is required' <<<"$err"; then
        echo "trade-bot-update: ${what} 권한 없음(sudoers NOPASSWD 줄 부재)" >&2
        printf '\n<i>⚠️ %s 권한 없음 — sudoers 를 다시 심는다: <code>sudo /home/higgack/stock/deploy/install.sh</code></i>' "$what"
    else
        err=$(printf '%s' "${err:0:200}" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')
        echo "trade-bot-update: ${what} 실패: ${err}" >&2
        printf '\n<i>⚠️ %s 실패: <code>%s</code> — 확인: %s</i>' "$what" "$err" "$check"
    fi
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
# `core.quotePath=false` — git 이 비ASCII 경로를 따옴표로 감싸면 아래 `^trade/…` 앵커가
# 빗나간다(독립 리뷰 #423 L3).
CHANGED_FILES=$(git -c core.quotePath=false diff --name-only "$LOCAL" "$REMOTE")
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
# entry; gracefully degrades when missing. 그 권한 줄은 NOAH `deploy/install.sh`(운영자 1회
# 권한으로 root 실행)와 이 설치기가 **같은 drop-in 에 같은 줄을 같은 순서로** 심는다 — 옛 판은
# 설치기가 자기 줄을 빼고 덮어써, 한 번 돌고 나면 다음 유닛 변경 배포에서 설치기를 못 불렀다
# (실수 #423 독립 리뷰 M②).
INSTALL_NOTE=""
# install-trade-units.sh itself is in the trigger set so that updating
# the installer (e.g. adding a new sudoers line or auto-enable rule)
# applies on the next deploy tick without needing to also touch a unit
# file.
UNIT_FILES_CHANGED=$(echo "$CHANGED_FILES" | grep -E '^deploy/(trade-bot[^/]*\.(service|timer)|install-trade-units\.sh)$' || true)
if [ -n "$UNIT_FILES_CHANGED" ]; then
    if INSTALL_OUTPUT=$(LC_ALL=C sudo -n "$REPO/deploy/install-trade-units.sh" 2>&1); then
        echo "$INSTALL_OUTPUT"
        # `|| true` — 설치기는 바꿀 게 없으면 SUMMARY 줄 없이 "no changes" 로 끝난다. 그때 grep 이
        # 1 을 내면 `set -eo pipefail` 이 이 스크립트를 **trade-bot 재시작 전에** 끝내고, 다음
        # tick 은 LOCAL==REMOTE 라 다음 trade 관련 배포가 올 때까지 새 코드가 안 실린다(실수 #423
        # 동작 회귀가 찾았다).
        SUMMARY=$(grep -oE 'SUMMARY .*$' <<<"$INSTALL_OUTPUT" | head -1 || true)
        if [ -n "$SUMMARY" ]; then
            INSTALL_NOTE=$'\n'"<i>+ systemd: ${SUMMARY}</i>"
        elif ! grep -qx 'install-trade-units: no changes' <<<"$INSTALL_OUTPUT"; then
            # 바꿀 게 없었으면(위 줄) 알림에 붙이지 않는다 — 저널엔 위 echo 가 남는다(#25). 요약 줄도
            # "no changes" 도 아닌 출력은 모르는 모양이라 사실만 적는다(독립 리뷰 #423 L③ — 옛 판은
            # 둘 다 "자동 설치 완료" 라 적었다, #165).
            INSTALL_NOTE=$'\n'"<i>+ systemd: 설치기는 돌았는데 요약 줄이 없다 — 확인: journalctl -u trade-bot-update -n 30</i>"
        fi
    else
        echo "$INSTALL_OUTPUT"
        echo "trade-bot-update: install-trade-units.sh failed"
        INSTALL_NOTE=$(sudo_failure_note "systemd 자동 설치(install-trade-units.sh)" "$INSTALL_OUTPUT" \
            "journalctl -u trade-bot-update -n 30")
    fi
fi

# Telethon 고정판 설치 — 핀(`trade/scripts/requirements.txt`)이 바뀐 배포에서만. 핀은 git 으로
# 움직이지만 운영 venv(`.backfill-venv`, Telethon 유닛 4개의 인터프리터)는 pip 를 돌려야
# 움직인다 — 그 틈에서 비고정판이 운영 세션을 올렸다(실수 #404). 사람이 pip 를 돌려야 효력이 나는
# fix 는 잘못된 fix 다(Automation-first). 실패해도 배포는 계속한다(`set -e` 밖 if) — 실패는 알림이
# 처방과 함께 말한다. 성공·실패와 무관하게 리스너는 아래 리스너 조건(requirements.txt 포함)이
# 재시작해 지금 venv 의 패키지를 다시 읽는다 — 동기화는 타이머라 다음 틱이 읽는다. `timeout`:
# 이 유닛은 Type=oneshot 이라 시작 타임아웃이 없어, pip 가 매달리면 다음 배포가 영영 못 돈다.
PIP_NOTE=""
REQ_CHANGED=$(echo "$CHANGED_FILES" | grep -xE 'trade/scripts/requirements\.txt' || true)
if [ -n "$REQ_CHANGED" ]; then
    PIP_MANUAL="cd $REPO &amp;&amp; .backfill-venv/bin/pip install -r trade/scripts/requirements.txt"
    if [ ! -x "$REPO/.backfill-venv/bin/pip" ]; then
        echo "trade-bot-update: .backfill-venv 없음 — Telethon 고정판 설치 생략"
        PIP_NOTE=$'\n'"<i>⚠️ 운영 venv(.backfill-venv)가 없어 Telethon 고정판을 못 깔았다 — trade/README.md 의 venv 생성 후: <code>${PIP_MANUAL}</code></i>"
    elif PIP_OUT=$(timeout 600 "$REPO/.backfill-venv/bin/pip" install -q -r trade/scripts/requirements.txt 2>&1); then
        echo "trade-bot-update: .backfill-venv 에 requirements.txt 설치 완료"
        PIP_NOTE=$'\n'"<i>+ 운영 venv 에 Telethon 고정판 설치(requirements.txt)</i>"
    else
        # 꼬리만 싣는다 — `${v: -N}` 은 v 가 N 자보다 짧으면 **빈 문자열**이라 짧은 오류가 통째로
        # 사라진다. 길이를 먼저 본다(문자 단위라 한글이 반쪽 나지 않는다).
        PIP_TAIL="$PIP_OUT"
        [ "${#PIP_TAIL}" -gt 200 ] && PIP_TAIL="${PIP_TAIL: -200}"
        echo "trade-bot-update: .backfill-venv pip install 실패: ${PIP_TAIL}"
        PIP_TAIL=$(printf '%s' "$PIP_TAIL" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')
        PIP_NOTE=$'\n'"<i>⚠️ Telethon 고정판 설치 실패: <code>${PIP_TAIL}</code> — 손으로: <code>${PIP_MANUAL}</code></i>"
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
# ⚠️ 두 리스너는 **돌고 있을 때만** 재시작한다(실수 #423 독립 리뷰 M① — NOAH DAJU 리스너와 같은
# 이유, #38): 미설치·세션 미인증(exit 78 → failed)·운영자 중지 상태를 배포가 되살리면 안 된다(그
# 경우 새 코드는 다음 기동 때 로드된다). 옛 판은 멈춰 둔 리스너를 배포마다 다시 켰고, 이 PR 이 더한
# 생존 확인이 그때마다 "active 아님" 경보를 붙였을 것이다. 대시보드는 이 가드를 두지 않는다 —
# 세션 인증 단계가 없는 서버라 멈춰 있으면 띄우는 게 맞다(NOAH `install.sh` 도 무조건 재시작한다).
BEON_LISTENER_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/scripts/listen_beon\.py$|^trade/scripts/__init__\.py$|^trade/[^/]+\.py$|^trade/scripts/requirements\.txt$' || true)
LISTENER_NOTE=""
# 재시작한 상시 유닛 — 아래 `sleep 3` 뒤 trade-bot 과 함께 살아 있는지 본다(실수 #423 독립
# 리뷰 M1 의 형제: 새 코드가 기동에서 죽으면 systemd 가 조용히 다시 띄우고 있을 뿐이다).
RESTARTED_UNITS=""
if [ -n "$BEON_LISTENER_RELEVANT" ]; then
    if ! systemctl is-active --quiet trade-bot-beon-listener 2>/dev/null; then
        echo "trade-bot-update: trade-bot-beon-listener 비활성(미설치·미인증·중지) — 재시작 생략, 새 코드는 다음 기동 때 로드"
    elif err=$(LC_ALL=C sudo -n /bin/systemctl restart trade-bot-beon-listener 2>&1); then
        echo "trade-bot-update: also restarted trade-bot-beon-listener"
        LISTENER_NOTE=$'\n'"<i>+ BeOn 리스너 재시작</i>"
        RESTARTED_UNITS="${RESTARTED_UNITS} trade-bot-beon-listener"
    else
        LISTENER_NOTE=$(sudo_failure_note "BeOn 리스너 재시작" "$err" "journalctl -u trade-bot-beon-listener -n 30")
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
# 폐포보다 넓다(대시보드 모듈도 걸린다) — 리스너가 재시작하는 사이 올라온 글은 주기 sync 가
# 회수한다. `bot.market`(종목 링크 렌더)은 리스너 경로가 부르지 않아 조건 밖이다.
BADONION_LISTENER_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/scripts/listen_badonion\.py$|^trade/scripts/__init__\.py$|^trade/[^/]+\.py$|^trade/scripts/requirements\.txt$' || true)
if [ -n "$BADONION_LISTENER_RELEVANT" ]; then
    if ! systemctl is-active --quiet trade-bot-badonion-listener 2>/dev/null; then
        echo "trade-bot-update: trade-bot-badonion-listener 비활성(미설치·미인증·중지) — 재시작 생략, 새 코드는 다음 기동 때 로드"
    elif err=$(LC_ALL=C sudo -n /bin/systemctl restart trade-bot-badonion-listener 2>&1); then
        echo "trade-bot-update: also restarted trade-bot-badonion-listener"
        LISTENER_NOTE="${LISTENER_NOTE}"$'\n'"<i>+ 나쁜양파 리스너 재시작</i>"
        RESTARTED_UNITS="${RESTARTED_UNITS} trade-bot-badonion-listener"
    else
        LISTENER_NOTE="${LISTENER_NOTE}$(sudo_failure_note "나쁜양파 리스너 재시작" "$err" \
            "journalctl -u trade-bot-badonion-listener -n 30")"
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
# 의 변경은 안 본다). NOAH 대시보드와 같은 정책(무상태라 닿는 가족 전체 — 코드와
# `data/` — 를 덮는다)이고, 이 프로세스의 import 폐포가 규칙에 걸리는지
# `tests/test_restart_closure_20260928.py` 가 소스에서 잰다.
DASHBOARD_RELEVANT=$(echo "$CHANGED_FILES" | grep -E '^trade/[^/]+\.py$|^trade/scripts/[^/]+\.py$|^trade/data/|^bot/[^/]+\.py$|^deploy/trade-bot-dashboard.*\.(service|timer)$' || true)
DASH_NOTE=""
if [ -n "$DASHBOARD_RELEVANT" ]; then
    if err=$(LC_ALL=C sudo -n /bin/systemctl restart trade-bot-dashboard 2>&1); then
        echo "trade-bot-update: also restarted trade-bot-dashboard"
        DASH_NOTE=$'\n'"<i>+ trade-bot-dashboard 재시작</i>"
        RESTARTED_UNITS="${RESTARTED_UNITS} trade-bot-dashboard"
    else
        DASH_NOTE=$(sudo_failure_note "trade-bot-dashboard 재시작" "$err" "journalctl -u trade-bot-dashboard -n 30")
    fi
fi

sleep 3
DEAD_NOTE=""
for unit in $RESTARTED_UNITS; do
    if ! systemctl is-active --quiet "$unit"; then
        echo "trade-bot-update: ${unit} 재시작 뒤 active 아님"
        DEAD_NOTE="${DEAD_NOTE}"$'\n'"<i>⚠️ ${unit} 재시작 후 active 아님 — 확인: journalctl -u ${unit} -n 30</i>"
    fi
done
if systemctl is-active --quiet trade-bot; then
    msg="✅ <b>배포 완료</b>: <code>${LOCAL_SHORT}</code> → <code>${REMOTE_SHORT}</code>"
    if [ -n "$SUBJECT" ]; then
        msg="${msg}"$'\n'"${SUBJECT}"
    fi
    msg="${msg}${INSTALL_NOTE}${PIP_NOTE}${DASH_NOTE}${LISTENER_NOTE}${DEAD_NOTE}"
    notify "$msg"
    echo "trade-bot-update: restart complete"
else
    notify "❌ <b>배포 실패</b>: trade-bot 서비스가 재시작 후 active 상태가 아님 (${REMOTE_SHORT})${DEAD_NOTE}"
    exit 1
fi
