#!/bin/bash
# Stock-bot systemd installer — idempotent. Re-run after any
# deploy/*.{service,timer,sh} change. Designed for auto-update.sh
# auto-invocation (single sudoers NOPASSWD line covers all changes).
# 사용자 정책 2026-05-29: SV 패턴 mirror — sudo 1회 setup 후 SSH 영원히
# 미진입. 새 systemd unit add 시도 stock repo push 만으로 1분 내 자동
# 설치 + reload + enable + Telegram 알림.
#
# Units installed (canonical paths from stock repo):
#   • stock-bot.service                 — Telegram bot (Type=simple)
#   • stock-bot-dashboard.service       — dashboard HTTP server
#   • stock-bot-update.{service,timer}  — git auto-deploy (1 min poll)
#   • stock-bot-watchdog.{service,timer}— hang detector (12 min)
#   • trade-bot.service                 — Korea trade-bot (if present)
#   • trade-bot-update.{service,timer}  — trade-bot git poll
#   • trade-bot-watchdog.{service,timer}— trade-bot hang detector
#   • screener-gics-check.{service,timer} — 분기 GICS 변경 점검 (4x/year)
#
# Side effects:
#   • Each unit `install -m 0644` 으로 /etc/systemd/system/ 에 복사
#   • Shell scripts chmod +x (auto-update.sh · watchdog.sh · trade-* · install.sh)
#   • systemctl daemon-reload
#   • Timer 들 enable --now (idempotent — 이미 enabled 면 no-op)
#   • Main service 들 (stock-bot · dashboard) 는 restart 하지 않음 —
#     auto-update.sh 의 stock-bot restart 가 별도 관리. Dashboard 만
#     enable.

set -euo pipefail

DEPLOY_DIR=/home/higgack/stock/deploy

if [ "$(id -u)" -ne 0 ]; then
    echo "must be run as root (use sudo)"
    exit 1
fi

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "deploy dir not found: $DEPLOY_DIR"
    exit 1
fi

echo "→ installing systemd units"
for unit in \
    stock-bot.service \
    stock-bot-dashboard.service \
    stock-bot-update.service        stock-bot-update.timer \
    stock-bot-watchdog.service      stock-bot-watchdog.timer \
    trade-bot.service \
    trade-bot-update.service        trade-bot-update.timer \
    trade-bot-watchdog.service      trade-bot-watchdog.timer \
    screener-gics-check.service     screener-gics-check.timer \
    daily-byte.service              daily-byte.timer ;
do
    if [ -f "$DEPLOY_DIR/$unit" ]; then
        install -m 0644 "$DEPLOY_DIR/$unit" /etc/systemd/system/
    fi
done

# Shell scripts — auto-update / watchdog / install — must be exec.
for sh in auto-update.sh watchdog.sh trade-auto-update.sh trade-watchdog.sh install.sh; do
    if [ -f "$DEPLOY_DIR/$sh" ]; then
        chmod +x "$DEPLOY_DIR/$sh"
    fi
done

systemctl daemon-reload

echo "→ enabling timers + dashboard"
# Timers — enable --now is idempotent (no-op when already enabled+active)
systemctl enable --now stock-bot-update.timer
systemctl enable --now stock-bot-watchdog.timer
if [ -f "$DEPLOY_DIR/screener-gics-check.timer" ]; then
    systemctl enable --now screener-gics-check.timer
fi
if [ -f "$DEPLOY_DIR/daily-byte.timer" ]; then
    systemctl enable --now daily-byte.timer
fi
if [ -f "$DEPLOY_DIR/trade-bot-update.timer" ]; then
    systemctl enable --now trade-bot-update.timer
fi
if [ -f "$DEPLOY_DIR/trade-bot-watchdog.timer" ]; then
    systemctl enable --now trade-bot-watchdog.timer
fi

# Main services — enable but don't restart (auto-update.sh handles stock-bot
# restart for code changes; dashboard restart only on user demand).
systemctl enable stock-bot.service
if [ -f "$DEPLOY_DIR/stock-bot-dashboard.service" ]; then
    systemctl enable --now stock-bot-dashboard.service
fi
if [ -f "$DEPLOY_DIR/trade-bot.service" ]; then
    systemctl enable trade-bot.service
fi

echo
echo "→ active stock-bot timers:"
systemctl list-timers --no-pager --all 2>/dev/null \
    | grep -E "stock-bot|trade-bot|screener-gics" || true

echo
echo "✓ install complete."
