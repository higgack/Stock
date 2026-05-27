#!/bin/bash
# Standard View systemd installer — idempotent. Re-run after any
# standardview/deploy/*.{service,timer} change. Designed for sv-update.sh
# auto-invocation (single sudoers NOPASSWD line covers all changes).
#
# Units installed (canonical paths from stock repo):
#   • sv-update.{service,timer}          — git auto-deploy (1 min)
#   • sv-cache-rollover.{service,timer}  — macro_news_cache flush 00:05
#   • sv-watchdog.{service,timer}        — stale latest.html kick (30 min)
#   • standardview-daily.{service,timer}  — daily_generator 07:30 + 20:30
#   • standardview-push.{service,timer}   — telegram_pusher 08:00 + 21:00
#   • standardview-weekly.{service,timer} — weekly_pusher 금 08:00
#
# Side-effects:
#   • Disables standardview-hourly.timer (legacy Mon-Fri 12/16시 push)
#   • Patches standardview-daily.service in-place to remove pusher
#     ExecStart line (push is now its own service)

set -euo pipefail

DEPLOY_DIR=/home/higgack/stock/standardview/deploy

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
    sv-update.service          sv-update.timer \
    sv-cache-rollover.service  sv-cache-rollover.timer \
    sv-watchdog.service        sv-watchdog.timer \
    standardview-daily.timer \
    standardview-push.service  standardview-push.timer \
    standardview-weekly.service standardview-weekly.timer ;
do
    if [ -f "$DEPLOY_DIR/$unit" ]; then
        install -m 0644 "$DEPLOY_DIR/$unit" /etc/systemd/system/
    fi
done

chmod +x "$DEPLOY_DIR/sv-update.sh" "$DEPLOY_DIR/sv-watchdog.sh"

# Patch existing standardview-daily.service in-place — strip pusher
# ExecStart line so daily.timer runs daily_generator only. push.timer
# now handles the pusher invocation.
if grep -q "telegram_pusher.py --text-only" /etc/systemd/system/standardview-daily.service 2>/dev/null; then
    echo "→ patching standardview-daily.service (removing pusher ExecStart)"
    sed -i '/telegram_pusher.py --text-only/d' /etc/systemd/system/standardview-daily.service
fi

systemctl daemon-reload

echo "→ enabling timers"
systemctl enable --now sv-update.timer
systemctl enable --now sv-cache-rollover.timer
systemctl enable --now sv-watchdog.timer
systemctl enable --now standardview-push.timer
systemctl enable --now standardview-weekly.timer
systemctl restart standardview-daily.timer  # pick up new 07:30/20:30 schedule

# Disable legacy hourly timer (12/16시 push 제거)
if systemctl is-enabled standardview-hourly.timer 2>/dev/null | grep -q enabled; then
    echo "→ disabling legacy standardview-hourly.timer"
    systemctl disable --now standardview-hourly.timer
fi

echo
echo "→ active SV timers:"
systemctl list-timers --no-pager --all 2>/dev/null | grep -E "standardview|sv-" || true

echo
echo "✓ install complete."
