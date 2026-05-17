#!/bin/bash
# Idempotent systemd unit installer for trade-bot.
#
# Compares every deploy/trade-bot*.{service,timer} against
# /etc/systemd/system/, copies what changed (mode 644), runs
# daemon-reload if anything changed, enables+starts any newly-
# introduced timer, and restarts any currently-active service whose
# unit file content changed.
#
# Wired into trade-auto-update.sh as `sudo -n install-trade-units.sh`
# so new units land on the host automatically on the next deploy
# tick. Safe to run by hand too. Reads $TRADE_REPO if set, else
# defaults to /home/higgack/stock-trade.
#
# Sudoers entry (one-time, see trade/README.md):
#   higgack ALL=(ALL) NOPASSWD: /home/higgack/stock-trade/deploy/install-trade-units.sh

set -euo pipefail

REPO="${TRADE_REPO:-/home/higgack/stock-trade}"
DEPLOY="$REPO/deploy"
SYSTEMD_DIR="/etc/systemd/system"

if [ ! -d "$DEPLOY" ]; then
    echo "install-trade-units: $DEPLOY not found" >&2
    exit 1
fi

CHANGED_FILES=()
CHANGED_SERVICES=()
NEW_TIMERS=()

shopt -s nullglob
for src in "$DEPLOY"/trade-bot*.service "$DEPLOY"/trade-bot*.timer; do
    name=$(basename "$src")
    dst="$SYSTEMD_DIR/$name"
    if [ ! -e "$dst" ] || ! cmp -s "$src" "$dst"; then
        cp -- "$src" "$dst"
        chmod 644 "$dst"
        CHANGED_FILES+=("$name")
        if [[ "$name" == *.timer ]]; then
            # Newly-introduced timer (not yet active) → enable+start it.
            if ! systemctl is-active --quiet "$name" 2>/dev/null; then
                NEW_TIMERS+=("$name")
            fi
        elif [[ "$name" == *.service ]]; then
            base="${name%.service}"
            # Only restart currently-active services. New services are
            # picked up by their corresponding timer (if any).
            if systemctl is-active --quiet "$base" 2>/dev/null; then
                CHANGED_SERVICES+=("$base")
            fi
        fi
    fi
done
shopt -u nullglob

if [ ${#CHANGED_FILES[@]} -eq 0 ]; then
    echo "install-trade-units: no changes"
    exit 0
fi

echo "install-trade-units: copied ${CHANGED_FILES[*]}"
systemctl daemon-reload
echo "install-trade-units: daemon-reload done"

for t in "${NEW_TIMERS[@]:-}"; do
    [ -z "$t" ] && continue
    systemctl enable --now "$t"
    echo "install-trade-units: enabled+started $t"
done

for s in "${CHANGED_SERVICES[@]:-}"; do
    [ -z "$s" ] && continue
    # Don't restart trade-bot-update mid-run (we're literally inside it
    # right now via trade-auto-update.sh). The next timer fire will
    # already use the new unit content after daemon-reload.
    if [[ "$s" == "trade-bot-update" ]]; then
        echo "install-trade-units: skip restart of self ($s)"
        continue
    fi
    systemctl restart "$s" || true
    echo "install-trade-units: restarted $s"
done

echo "install-trade-units: SUMMARY changed=${#CHANGED_FILES[@]} new_timers=${#NEW_TIMERS[@]:-0} restarted=${#CHANGED_SERVICES[@]:-0}"
