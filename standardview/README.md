# Standard View — canonical files + automation

Mirror of the live `~/standardview/` deploy. Commits to `standardview/`
in this repo auto-deploy to the VM within 1 minute via `sv-update.timer`.

## Layout

```
standardview/
├── scripts/         canonical daily_generator.py + telegram_pusher.py
├── backend/         canonical main.py + requirements.txt
├── tools/           one-off helpers (cache_rollover.py, etc.)
└── deploy/          systemd units + auto-update + watchdog
```

Friend's StanLee5767/standardview repo provides the upstream. We only
track files we've patched here; everything else lives in
`~/standardview/` on the deploy host unchanged.

## Automation

| Timer                     | Cadence       | Purpose                                                   |
|---------------------------|---------------|-----------------------------------------------------------|
| `sv-update.timer`         | 1 min         | git pull + rsync changed files + restart backend if needed |
| `sv-cache-rollover.timer` | 00:05 KST     | `DELETE FROM macro_news_cache` — kills midnight stub-cache bug |
| `sv-watchdog.timer`       | 30 min        | re-kick `daily_generator.py` if `latest.html` >90 min stale during 08:00-22:00 KST |

The `daily_generator.py` is also driven by friend's existing
`standardview-daily.timer` (08:00 KST text push) and
`standardview-hourly.timer` (Mon-Fri 12:00/16:00 KST). Those continue
to run; watchdog only kicks if they miss / hang.

## One-time install (deploy host)

```bash
sudo /home/higgack/stock/standardview/deploy/install.sh
```

Requires passwordless sudo on `/bin/systemctl restart` for the
backend service. If not already in `/etc/sudoers.d/`, add:

```
higgack ALL=NOPASSWD: /bin/systemctl restart standardview-backend
```

(Adjust the service name if backend service has a different name.)

## Development flow

```bash
# 1. edit standardview/scripts/daily_generator.py locally
# 2. commit + push to the branch
# 3. wait ~1 min — sv-update.service rsyncs the new file + restarts
#    backend if needed + posts a Telegram deploy notification
# 4. next standardview-hourly.timer / standardview-daily.timer fires
#    pick up the new code automatically
```

No SSH required for routine edits.

## BUSY_MARKER

`~/.standardview/.daily_generator_busy` — created by watchdog before
kicking the generator, removed when it completes. `sv-update.sh`
checks this marker and defers deploys while the generator is mid-run
to avoid a partial-write on `latest.html`. Stale markers (>20 min)
are treated as crashed runs and ignored.

## Universal-by-default note

This automation lives in the stock repo because the deploy host is
shared with NOAH. The same `stock-bot-update.timer` (1 min) and
`sv-update.timer` (1 min) both run on the same VM, watching the same
git branch, but each only touches its own paths (NOAH watches
`bot/`/`TradingAgents/`, SV watches `standardview/`). No conflict.
