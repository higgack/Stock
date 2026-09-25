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
3. `journalctl -u trade-bot -n 20 | grep 'channel chat ID'` will show `channel chat ID is -100...` (grep 으로 거르는 이유: 봇이 토큰을 가리기 전 판이 찍은 줄엔 토큰이 평문으로 남아 있다).
4. Set `TRADE_CHANNEL_CHAT_IDS=-100...` in `.env`, `systemctl restart trade-bot`.

## Verifying ingestion

```bash
tail -f ~/.trade/inbox.jsonl                # one line per message
ls ~/.trade/media/$(date -I)/                # downloaded photos for today
# live log — 봇은 자기 로그의 토큰을 `BOT_TOKEN` 으로 가린다(실수 #406 리뷰). ⚠️ 가리기
# 전 판이 찍은 getUpdates 줄엔 토큰이 평문으로 남아 있다(저널 보존기간 동안) — 출력을
# 어디 붙여 넣을 거면 가릴 것:
journalctl -u trade-bot -f | sed -u -E 's/[0-9]{8,10}:[A-Za-z0-9_-]{30,}/<TOKEN>/g'
```

포워드했는데 inbox 에 안 들어오면(실수 #406) 봇이 못 받았는지 · 받고 버렸는지 ·
어디서 막혔는지(미가동·409·웹훅·수신 종류·관리자·게이트·inbox 경로)를 한 번에
가른다 — 읽기 전용이고 getUpdates 를 부르지 않으며 토큰을 찍지 않는다:

```bash
cd ~/stock-trade && .venv/bin/python -m trade.bot_health
```

종료코드는 0 이상 없음 · 1 문제(❌) · 2 판정 불가(❓)다. 버림을 적지 않는 **옛 판** 봇이
돌면 증상이 없어도 2 다 — 그래서 다시 포워드는 `bot_health && 백필` 로 이으면 봇이 새
판으로 재시작되기 전엔 나가지 않는다(실수 #409).

같은 대조(릴레이가 보낸 수 ↔ 봇이 받은 수 — 받음은 기록 + 출처 게이트 버림)를
`trade-bot-health.timer` 가 매시간 돌려, 못 받았거나 릴레이 원천의 글을 버렸거나 채널
글을 처리하다 예외로 놓쳤으면(수신 줄 없는 번호 — 수와 무관한 직접 증거) 채널로 알린다.
다른 출처 포워드의 버림은 알리지 않는다 — BeOn 이 되포워드한 남의 글이 대부분이라서다.
⚠️ 그래서 나쁜양파가 **재게시**한 관련 글(다른 채널에서 퍼 온 글 — 텔레그램은 원래 출처를
단다)을 버린 손실은 알림에 안 잡힌다. 진단의 ⚠️ 메모가 그 출처·건수와 가르는 명령(`--find`
로 그 글이 여전히 to-forward 인가)을 적는다. 같은 사실은 한 번만 알린다 — **전달된** 알림의
사실만 `~/.trade/.health-markers/delivery-alerted.json` 에 적고(전달 실패는 다음 실행이 다시
알린다), 끊김이 이어지면 새로 빠진 포워드만 알린다. ⚠️ 수로 대조하므로 같은 창의 다른 글
수신(운영자가 직접 포워드한 글 등)이 예외 없는 손실을 덮을 수 있다. 손으로 돌린 백필은
저널에 안 남아 '보낸 수' 에 안 든다.

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
#    ⚠️ telethon 은 그 파일에 **고정**돼 있다(실수 #404). 트레이드 텔레그램
#    스크립트는 이 venv 로만 돌릴 것 — 다른 venv 의 더 새 telethon 이 세션 파일을
#    한 번 열면 새 형식으로 올라가 이 venv 가 못 연다(가드가 막고 알린다).
#    고정판을 올렸으면 여기 다시 깔 것 — 자동 배포는 pip 를 돌리지 않는다.

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
journalctl -u trade-bot -f | sed -u -E 's/[0-9]{8,10}:[A-Za-z0-9_-]{30,}/<TOKEN>/g'   # 토큰 가림
```

If a ⏸ Telegram alert arrives mid-run:
```bash
bash trade/scripts/free_disk.sh   # one-shot safe cleanup
# backfill auto-detects the free space and resumes within ~60 s
```

## 대만·중국·일본(2번째 소스) 수출 데이터 — Badonions(나쁜양파, t.me/Badonions)

일본(BeOn)과 동일한 Telethon relay 패턴의 두 번째 소스(사용자 2026-07-10,
중국 추가 2026-07-11 — "같은 텔레그램", 일본 2번째 소스 추가 2026-07-11 —
"일본은 2개의 서로 다른 채널에서 2개의 대쉬보드로 갈거야"). 나쁜양파 채널이
BeOn 과 별도로 자체 일본 수출 통계도 발행하는데, 운영자가 두 소스를 의도적으로
분리 유지하길 원해서 `jp_exports.py`(BeOn)를 건드리지 않고 완전히 독립된
`jp2_exports.py` + `jp2.db` + `jp2.html` 로 추가했다.

`trade/scripts/backfill_badonion.py` + `trade/scripts/listen_badonion.py` 가
`backfill_beon.py`/`listen_beon.py` 를 그대로 미러링 — 같은 `.backfill-venv`,
같은 `TRADE_TELETHON_API_ID`/`TRADE_TELETHON_API_HASH`, 같은 목적지 채널
(`TRADE_CHANNEL_CHAT_IDS`)을 재사용하고, 세션 파일만 별도(`.badonion-session`
/ `.badonion-listener-session`)라 BeOn 파이프라인과 독립적으로 동작한다.

나쁜양파는 대만·중국·일본 수출 데이터 외에 애널리스트 레이팅표 등 무관한
트레이딩 정보도 섞어 올리는 일반 채널이라(BeOn 과 다른 점), forward 시점에
`trade.tw_exports.parse_tw_export()`/`trade.cn_exports.parse_cn_export()`/
`trade.jp2_exports.parse_jp2_export()` 중 하나로 매칭되는 캡션(앨범이면 멤버
중 하나라도)만 골라 보낸다. 대만은 `store.db`·`jp.db` 와 분리된 `tw.db` →
`tw.html`, 중국은 별도 `cn.db` → `cn.html`, 일본(나쁜양파)은 별도 `jp2.db` →
`jp2.html` 로 적재/렌더되어 대시보드에 나란히 링크된다.

```bash
# 1. (BeOn 이미 설정돼 있으면 스킵) .backfill-venv 준비 — 위 BeOn 섹션 참고

# 2. 리스너 최초 1회 인증 (전화번호 + 코드 + 2FA) — 별도 세션 파일
cd ~/stock-trade
.backfill-venv/bin/python -m trade.scripts.listen_badonion --auth

# 3. systemd 유닛 설치 (install-trade-units.sh 가 다음 배포 틱에 자동 설치도 함)
sudo cp deploy/trade-bot-badonion-sync.service /etc/systemd/system/
sudo cp deploy/trade-bot-badonion-sync.timer /etc/systemd/system/
sudo cp deploy/trade-bot-badonion-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trade-bot-badonion-sync.timer trade-bot-badonion-listener

# 4. 과거 히스토리 백필 (선택)
.backfill-venv/bin/python trade/scripts/backfill_badonion.py --since 2026-01-01 --dry-run
tmux new -s badonion-backfill -d ".backfill-venv/bin/python trade/scripts/backfill_badonion.py --since 2026-01-01 2>&1 | tee -a ~/badonion-backfill.log"
```

## Dashboard (phase 2b/2c)

The dashboard is a single static HTML file regenerated from `store.db`
every 5 minutes by a systemd timer, and served by a thin HTTP server
with optional Basic Auth.

### One-time install

```bash
cd ~/stock-trade
sudo cp deploy/trade-bot-dashboard.service /etc/systemd/system/
sudo cp deploy/trade-bot-dashboard-refresh.service /etc/systemd/system/
sudo cp deploy/trade-bot-dashboard-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload

# (optional but recommended) Lock the dashboard behind Basic Auth.
# Edit ~/stock-trade/.env, fill in:
#   TRADE_DASHBOARD_USER=...
#   TRADE_DASHBOARD_PASSWORD=...   (random 16+ chars)
#   TRADE_DASHBOARD_TOKEN=...      (optional URL prefix, e.g. /xyz123/)

sudo systemctl enable --now trade-bot-dashboard trade-bot-dashboard-refresh.timer
sudo systemctl start trade-bot-dashboard-refresh.service    # first render now

# Open port 8765 if your VM has a firewall (GCP/AWS Security Groups).
# Or skip and use SSH tunneling from your laptop (next section).
```

### Accessing from your laptop

**Option A — SSH tunnel (recommended, no firewall change):**
```bash
ssh -L 8765:localhost:8765 higgack@<host>
# then on your laptop browser:
#   http://localhost:8765/dashboard/
```

**Option B — Direct (requires open port):**
```
http://<host-public-ip>:8765/dashboard/
```

If you set `TRADE_DASHBOARD_USER` + `_PASSWORD`, the browser prompts
for Basic Auth on first load. If you set `TRADE_DASHBOARD_TOKEN=xyz`,
the URL becomes `http://.../xyz/dashboard/` — the token gates every
request and isn't logged on most reverse proxies, useful as a
mild-obscurity gate before Basic Auth.

### What's auto

- `trade-bot.service` ingests forwards into `inbox.jsonl` in real time
- `trade-bot-dashboard-refresh.timer` fires every 5 min:
    1. `ingest_inbox` → upserts any new captured rows into `store.db`
    2. `dashboard` → re-renders `~/.trade/dashboard/index.html`
- `trade-bot-dashboard.service` serves the file (and `~/.trade/media/`)
- `trade-bot-dashboard-audit.timer` fires daily 08:10 KST: renders-vs-store
  count checks, freshness, sibling-page/lazy-panel integrity, archive
  counts, eval backlog visibility — Telegram alert **only when ❌**
  (`python -m trade.scripts.dashboard_audit` to run manually)

Worst-case latency from "BeOn publishes" → "card shows up in
dashboard" is **5 minutes** (the refresh tick). The HTML page itself
filters/searches client-side, so user interactions never round-trip.

### Manual operations

Force an immediate refresh after pushing a parser fix or merging new
forwards mid-cycle:
```bash
sudo systemctl start trade-bot-dashboard-refresh.service
journalctl -u trade-bot-dashboard-refresh -n 20 --no-pager
```

Restart the server (after changing auth settings in `.env`):
```bash
sudo systemctl restart trade-bot-dashboard
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
