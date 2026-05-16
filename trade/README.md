# Trade — Korea import/export dashboard bot

Sibling project to the NOAH stock bot. **Shares the GitHub repo, runs
from its own clone on the host** so the two auto-update timers don't
fight over the working tree's branch / HEAD.

## What it does

BeOn (`t.me/BeOn_BeClear`) publishes Korean import/export alerts in
bursts (~50 events/year, 100-300 messages per event, each item shipped
as text + a graph image + a table image). The user gets these forwarded
to a private channel by an external forwarder tool. This bot turns that
firehose into a **two-view dashboard**:

- **품목별** — one card per (item, country), showing only the latest alert
- **회사별** — one section per related company, listing every (item, country)
  that mentions it, again only the latest

Older alerts stay in the database (so a future "history" toggle is cheap)
but the dashboard surfaces only the most recent state per key.

## Design principles (in order)

1. **Speed** — message arrival → dashboard reflects ≤ 90s, even under burst
2. **Don't crash** — bot must survive 300-message bursts without restart loops
3. **Cost ≈ $0** — no OCR, no LLM, no external APIs; just storage + Telegram
4. **Readability** — mobile-first, image-centric, only two views
5. **Accuracy** — BeOn images shown verbatim (no transformation = no distortion);
   metadata extracted by regex with `parse_warnings` for ambiguous cases

## Architecture (phase 1.5: ingestion)

```
BeOn_BeClear (public)
        │  external forwarder (preserves images & forward_origin OR
        │  prepends "BeOn - 비온" header in copy mode — both supported)
        ▼
my private channel ──► trade-bot ──► ~/.trade/inbox.jsonl
                              │                ~/.trade/media/YYYY-MM-DD/<uid>.jpg
                              │
                              └─► (phase 2a: parser → store.db)
                              └─► (phase 2b: dashboard/index.html)
```

### Burst handling

Each `on_channel_post` returns within ~1ms (synchronous JSONL append +
`asyncio.create_task` for image download). Photo downloads share a
semaphore (default 8 concurrent) to stay under Telegram's ~30 req/sec
bot API cap. Telegram delivers album members (text + 2 images) as
separate messages with the same `media_group_id`; phase 2's parser joins
siblings at read time so the bot itself is stateless across restarts.

The watchdog's polling-stall window is 300s — wide enough to absorb
short Telegram API hiccups during a burst, narrow enough that a real
hang is still caught quickly.

## On-host layout

```
/home/higgack/stock/         ← stock-bot clone (existing, untouched)
/home/higgack/stock-trade/   ← trade-bot clone (new, this README's repo)
~/.tradingagents/            ← stock-bot data
~/.trade/                    ← trade-bot data
  ├ inbox.jsonl              one row per Telegram message (append-only)
  └ media/YYYY-MM-DD/        photos, deterministic file_unique_id.jpg
```

Two clones, two working trees, two branches, zero conflict. Both clones
point at the same `origin` (GitHub `higgack/stock`).

## One-time host setup

```bash
# 1. Clone into a separate path, check out the trade dev branch.
git clone <repo-url> ~/stock-trade
cd ~/stock-trade
git checkout claude/export-import-dashboard-zQsi2

# 2. Dedicated venv (isolated from stock-bot's deps).
python -m venv .venv
.venv/bin/pip install -r trade/requirements.txt

# 3. Secrets — separate .env in THIS clone (not shared with stock-bot).
cp trade/.env.example .env
$EDITOR .env  # fill in TRADE_BOT_TOKEN at minimum

# 4. Install systemd units (from EITHER clone — they're identical files).
sudo cp deploy/trade-bot.service /etc/systemd/system/
sudo cp deploy/trade-bot-update.service /etc/systemd/system/
sudo cp deploy/trade-bot-update.timer /etc/systemd/system/
sudo cp deploy/trade-bot-watchdog.service /etc/systemd/system/
sudo cp deploy/trade-bot-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trade-bot trade-bot-update.timer trade-bot-watchdog.timer
```

## Discovering the private channel's chat ID

1. Add the bot as an **admin** of the destination private channel
   (regular members don't receive channel posts via Bot API).
2. With `TRADE_CHANNEL_CHAT_IDS=` empty, post any message in the channel.
3. `journalctl -u trade-bot -n 20` will show `channel chat ID is -100...`.
4. Set `TRADE_CHANNEL_CHAT_IDS=-100...` in `.env`, `systemctl restart trade-bot`.

## Verifying ingestion

```bash
tail -f ~/.trade/inbox.jsonl                # one line per message
ls ~/.trade/media/$(date -I)/                # downloaded photos for today
journalctl -u trade-bot -f                   # live log
```

## One-time backfill — Telethon (`trade/scripts/backfill_beon.py`)

Bots can't read channel history; only messages that arrive after the
bot joined are delivered via `getUpdates`. To seed `inbox.jsonl` with
posts older than the trade-bot's first boot, a one-shot Telethon
script uses your personal Telegram account to forward BeOn_BeClear's
history into the private destination channel — trade-bot then ingests
each forward as if it were live.

**Isolated from the live bot:**
- separate venv (`.backfill-venv/`) so Telethon's crypto stack never
  touches `trade-bot.service`'s deps
- separate session file (`.backfill-session`), gitignored
- API creds (`TRADE_TELETHON_API_ID`, `TRADE_TELETHON_API_HASH`) live
  in the same `.env` (chmod 600) but are read by the backfill script only

**Safe under load:**
- *Idempotent* — scans `inbox.jsonl` and skips any BeOn `message_id`
  already ingested. Rerun after Ctrl+C, FloodWait abort, or disk-low
  pause just resumes where it stopped.
- *Adaptive pacing* — starts at 1.5 s/unit, grows by 0.1 s every 500
  msgs, caps at 3.0 s. Tunable via `TRADE_PAUSE_BASE_S` etc. in `.env`.
- *FloodWait hard cap* — exits gracefully with a Telegram alert past
  `TRADE_MAX_FLOOD_WAIT_S` (default 600 s) instead of sleeping for
  hours inside the script. Rerun next day picks up the rest.
- *Disk guard* — every 20 units, checks free space on `/`. If below
  `TRADE_MIN_FREE_GB` (default 2.0), pauses with a ⏸ Telegram alert,
  polls every 60 s, auto-resumes (▶ alert) once free space recovers.
  Manual cleanup helper: `bash trade/scripts/free_disk.sh`.
- *Restart-safe under git updates* — auto-update's `git reset --hard`
  rewrites the script on disk but the running Python process keeps
  its in-memory bytecode. Pushing a fix mid-run is safe; the new code
  applies to the next invocation.

```bash
# 1. Get personal-account API credentials (one-time)
#    https://my.telegram.org/apps  →  Create application
#    add TRADE_TELETHON_API_ID + TRADE_TELETHON_API_HASH to ~/stock-trade/.env

# 2. Temp venv for the backfill (Telethon only)
cd ~/stock-trade
python -m venv .backfill-venv
.backfill-venv/bin/pip install -r trade/scripts/requirements.txt

# 3. Dry-run first to see how many messages are in range
.backfill-venv/bin/python trade/scripts/backfill_beon.py --since 2026-05-01 --dry-run

# 4. Actual run, in a tmux session so SSH drops don't kill it.
#    First time: prompts for your phone number + SMS code (+ 2FA password
#    if enabled). Subsequent runs: silent.
tmux new -s backfill -d ".backfill-venv/bin/python trade/scripts/backfill_beon.py --since 2026-05-01 2>&1 | tee -a ~/backfill.log"
tmux attach -t backfill   # observe; Ctrl+B then D to detach

# 5. (Optional) Tear down once the backfill is done
rm -rf .backfill-venv .backfill-session*
```

While it runs, watch ingestion in another terminal:
```bash
tail -f ~/.trade/inbox.jsonl
journalctl -u trade-bot -f
```

If a ⏸ Telegram alert arrives mid-run:
```bash
bash trade/scripts/free_disk.sh   # one-shot safe cleanup
# backfill auto-detects the free space and resumes within ~60 s
```

## Coexistence with the stock-bot

| What                       | Stock-bot                                | Trade-bot                                  |
|----------------------------|------------------------------------------|--------------------------------------------|
| Clone path                 | `~/stock`                                | `~/stock-trade`                            |
| systemd unit               | `stock-bot.service`                      | `trade-bot.service`                        |
| venv                       | `~/stock/.venv`                          | `~/stock-trade/.venv`                      |
| Env file                   | `~/stock/.env`                           | `~/stock-trade/.env`                       |
| Env var prefix             | `TELEGRAM_BOT_TOKEN`, `CHANNEL_CHAT_IDS` | `TRADE_BOT_TOKEN`, `TRADE_CHANNEL_CHAT_IDS`, `TRADE_SOURCE_ORIGIN` |
| Data dir                   | `~/.tradingagents`                       | `~/.trade`                                 |
| Auto-update branch         | `claude/stock-trading-automation-xqYf7`  | `claude/export-import-dashboard-zQsi2`     |
| Update script              | `deploy/auto-update.sh`                  | `deploy/trade-auto-update.sh`              |
| Watchdog                   | `deploy/watchdog.sh`                     | `deploy/trade-watchdog.sh`                 |

A crash in one cannot touch the other. They share only the host's
journald and (optionally) the bot-deploy notification curl path.
