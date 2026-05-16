#!/bin/bash
# Safe disk cleanup helper for the trade-bot host.
#
# Run when backfill posts a ⏸ pause alert ("디스크 잔여 X.XGB < Y.YGB").
# Touches only journald and apt caches — never user data, never
# ~/.trade/* . Backfill auto-detects free space within ~60 s and posts
# a ▶ resume alert before continuing.
#
# Usage:
#   bash trade/scripts/free_disk.sh

set -euo pipefail

echo "=== before ==="
df -h /

echo
echo "=== journald → vacuum to 200M ==="
sudo journalctl --vacuum-size=200M

echo
echo "=== apt cache → clean ==="
sudo apt clean

echo
echo "=== after ==="
df -h /
