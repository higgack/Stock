#!/bin/bash
# One-time installer for Standard View systemd automation.
#
# After NOAH-style stock repo auto-deploy is set up (which is already
# the case for the user), this script installs SV-specific units:
#   • sv-update.timer (1 min)            — git pull + selective rsync
#   • sv-cache-rollover.timer (00:05 KST) — DELETE FROM macro_news_cache
#   • sv-watchdog.timer (30 min)         — kick if daily_generator stuck
#
# Run with sudo. Idempotent — safe to re-run after timer/service edits.

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
install -m 0644 "$DEPLOY_DIR/sv-update.service"          /etc/systemd/system/
install -m 0644 "$DEPLOY_DIR/sv-update.timer"            /etc/systemd/system/
install -m 0644 "$DEPLOY_DIR/sv-cache-rollover.service"  /etc/systemd/system/
install -m 0644 "$DEPLOY_DIR/sv-cache-rollover.timer"    /etc/systemd/system/
install -m 0644 "$DEPLOY_DIR/sv-watchdog.service"        /etc/systemd/system/
install -m 0644 "$DEPLOY_DIR/sv-watchdog.timer"          /etc/systemd/system/

chmod +x "$DEPLOY_DIR/sv-update.sh" "$DEPLOY_DIR/sv-watchdog.sh"

systemctl daemon-reload

echo "→ enabling timers"
systemctl enable --now sv-update.timer
systemctl enable --now sv-cache-rollover.timer
systemctl enable --now sv-watchdog.timer

echo
echo "→ status"
systemctl list-timers --no-pager sv-update.timer sv-cache-rollover.timer sv-watchdog.timer

echo
echo "✓ install complete. Verify:"
echo "    sudo journalctl -u sv-update -n 30 --no-pager"
echo "    sudo systemctl status sv-update.timer"
