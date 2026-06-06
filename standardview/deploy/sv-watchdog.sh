#!/bin/bash
# Standard View watchdog — detect stalled daily_generator and re-kick.
#
# Behavior:
#   • Latest HTML age > 90 min during operating hours (08:00-22:00 KST)
#     → kick daily_generator.py asynchronously
#   • Stale BUSY_MARKER (>30 min) → remove + retry
#   • Outside operating hours → skip (timer fires every 30 min regardless)

set -euo pipefail

LIVE=/home/higgack/standardview
BUSY_MARKER=/home/higgack/.standardview/.daily_generator_busy
LATEST_HTML="$LIVE/reports/generated/latest.html"
KICK_LOG=/home/higgack/.standardview/watchdog.log
MAX_AGE_MIN=90
BUSY_STALE_MIN=30

mkdir -p "$(dirname "$BUSY_MARKER")"

# 주말(토/일)엔 일일 브리프를 생성하지 않으므로(daily_generator 주말 가드)
# 워치독 재실행도 skip — 안 하면 토/일 stale 감지 → 재실행 → 주말 브리프
# 재생성으로 가드 무력화 (사용자 정책 2026-06-06). 주간브리프(weekly_pusher,
# 일 22시)는 별도 unit 이라 영향 없음.
DOW_KST=$(TZ=Asia/Seoul date +%u)   # 1=Mon … 5=Fri 6=Sat 7=Sun
if [ "$DOW_KST" -ge 6 ]; then
    echo "$(date -Is) sv-watchdog: weekend (DOW ${DOW_KST} KST) — skip" >> "$KICK_LOG"
    exit 0
fi

# KST hour (operating hours 08:00-22:00 → kick window)
HOUR_KST=$(TZ=Asia/Seoul date +%H)
if [ "$HOUR_KST" -lt 8 ] || [ "$HOUR_KST" -ge 22 ]; then
    echo "sv-watchdog: outside operating hours (KST ${HOUR_KST}) — skip" >> "$KICK_LOG"
    exit 0
fi

# Clear stale busy marker so a crashed prior run doesn't permanently
# block kicks.
if [ -f "$BUSY_MARKER" ]; then
    if [ -n "$(find "$BUSY_MARKER" -mmin +"$BUSY_STALE_MIN" 2>/dev/null)" ]; then
        rm -f "$BUSY_MARKER"
        echo "$(date -Is) sv-watchdog: cleared stale busy marker" >> "$KICK_LOG"
    else
        echo "$(date -Is) sv-watchdog: generator running, defer" >> "$KICK_LOG"
        exit 0
    fi
fi

# Skip if latest HTML is fresh enough.
if [ -f "$LATEST_HTML" ]; then
    if [ -z "$(find "$LATEST_HTML" -mmin +"$MAX_AGE_MIN" 2>/dev/null)" ]; then
        exit 0
    fi
fi

# Kick. Run in background detached from this oneshot so systemd
# doesn't time out waiting for daily_generator's 5-10 min run.
echo "$(date -Is) sv-watchdog: kicking daily_generator" >> "$KICK_LOG"
touch "$BUSY_MARKER"
(
    cd "$LIVE"
    "$LIVE/.venv/bin/python" scripts/daily_generator.py >> "$KICK_LOG" 2>&1
    rm -f "$BUSY_MARKER"
) & disown
