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
