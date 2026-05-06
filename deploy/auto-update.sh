#!/bin/bash
# Auto-update stock-bot from origin and restart on new commits.
# Triggered by stock-bot-update.timer every couple of minutes.

set -euo pipefail

REPO=/home/higgack/stock
BRANCH=claude/stock-trading-automation-xqYf7

cd "$REPO"

git fetch --quiet origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # nothing to do
fi

echo "stock-bot-update: pulling ${LOCAL:0:7} → ${REMOTE:0:7}"
git pull --quiet --ff-only origin "$BRANCH"
sudo /bin/systemctl restart stock-bot
echo "stock-bot-update: restart complete"
