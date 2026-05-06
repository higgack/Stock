#!/bin/bash
# Auto-update stock-bot from origin and restart on new commits.
# Triggered by stock-bot-update.timer every couple of minutes.

set -euo pipefail

REPO=/home/higgack/stock
BRANCH=claude/stock-trading-automation-xqYf7
BUSY_MARKER=/home/higgack/.tradingagents/.busy
# If the busy marker is older than this, treat it as stale (bot crashed
# without cleaning up) and proceed with the restart anyway.
STALE_AFTER_MINUTES=20

cd "$REPO"

git fetch --quiet origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

if [ "$LOCAL" = "$REMOTE" ]; then
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

echo "stock-bot-update: pulling ${LOCAL:0:7} → ${REMOTE:0:7}"
git pull --quiet --ff-only origin "$BRANCH"
sudo /bin/systemctl restart stock-bot
echo "stock-bot-update: restart complete"
