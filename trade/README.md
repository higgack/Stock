# Trade — Korea import/export dashboard bot

Sibling project to the NOAH stock bot. **Shares the GitHub repo, but runs
from its own clone on the host** so the two auto-update timers don't
fight over the working tree's branch / HEAD.

## Architecture (phase 1: ingestion only)

```
BeOn_BeClear (public)
        │  (auto-forwarder tool, externally configured)
        ▼
my private channel ──► trade-bot ──► ~/.trade/inbox.jsonl
                              │
                              └─► (phase 2: parser + SQLite + dashboard)
```

Phase 1 only captures forwarded messages with full metadata. Parsing,
aggregation, and dashboard rendering land once we have real samples.

## On-host layout

```
/home/higgack/stock/         ← stock-bot clone (existing, untouched)
/home/higgack/stock-trade/   ← trade-bot clone (new, this README's repo)
~/.tradingagents/            ← stock-bot data
~/.trade/                    ← trade-bot data (inbox.jsonl etc.)
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
tail -f ~/.trade/inbox.jsonl
```

Each line is one forwarded post: text, forward-origin chat ID, original
message ID, timestamps, media flags — enough for the parser to reconstruct
the source without re-fetching Telegram.

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
