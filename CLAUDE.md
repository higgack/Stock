# NOAH Stock Bot — Project Notes for Claude

Operational rules for working in this repo. Apply to **every subproject** in
this repo (currently: `bot/` NOAH stock-bot, `trade/` Korea import/export bot).

## Default workflow — review first, commit only on request

For **any** request (analysis output, feature idea, bug report, refactor):

1. **Review and propose** — never edit/commit yet. Surface the diagnosis,
   the proposed change as a **generalized universal rule** (never a
   ticker-specific or one-off patch), and the trade-offs.
2. **Wait for explicit "커밋"** from the user before staging anything.
   Until then, the deliverable is the proposal itself, not committed code.
   The only exception is when the user opens with an explicit instruction
   to commit ("이대로 커밋", "스캐폴드 만들고 커밋해줘", etc.).
3. **After explicit commit**: stage, commit, and push to the current
   `claude/...` branch. Open / update the draft PR if one doesn't exist.

## Deploy notifications — Telegram start/complete/fail are mandatory

Every deploy automation in this repo (any auto-update script, any
provisioner, anything that promotes code to a running service) MUST send
the project's Telegram channel the three lifecycle messages:

- `🚀 <b>배포 시작</b>: <code>{old_sha7}</code> → <code>{new_sha7}</code>` plus the new commit subject on a new line
- `✅ <b>배포 완료</b>: <code>{old_sha7}</code> → <code>{new_sha7}</code>` plus the new commit subject
- `❌ <b>배포 실패</b>: <reason> (<code>{new_sha7}</code>)` on any abort path (`git reset` failure, `systemctl restart` failure, service not `active` after restart)

Rationale: the user runs the host alone and treats the channel as the
single source of truth for what's live. Silent deploys leave no audit
trail and break that trust.

Implementation pattern (mirror `deploy/auto-update.sh` for stock-bot,
`deploy/trade-auto-update.sh` for trade-bot):

- Source the project's `.env` for `*_BOT_TOKEN` and `*_CHANNEL_CHAT_IDS`
- POST to `https://api.telegram.org/bot{token}/sendMessage` with `parse_mode=HTML`
- On missing creds: silently skip the notify (don't fail the deploy)
- Use `curl -s -m 10` so a Telegram outage can't block the deploy

When introducing a new subproject or a new deploy path, scaffold the
three notifications **in the same commit** as the deploy script — never
ship the script first and add notifications later.

## Scope-matched auto-update

Each subproject's auto-update script MUST decide whether incoming
commits touch its own runtime before restarting the service or sending
a deploy notification. Pull silently for everything else so:

- the service isn't restarted for unrelated subprojects' commits
- the deploy channel doesn't get spammed with irrelevant 🚀/✅ for
  changes the operator only cares about in another channel

Pattern (mirror `deploy/auto-update.sh` and `deploy/trade-auto-update.sh`):

```bash
CHANGED_FILES=$(git diff --name-only "$LOCAL" "$REMOTE")
RELEVANT=$(echo "$CHANGED_FILES" | grep -E '<this subproject's path regex>' || true)
if [ -z "$RELEVANT" ]; then
    git reset --hard "origin/${BRANCH}" --quiet
    exit 0
fi
```

The regex must cover the subproject's code dir, its own service / timer
unit files, its own deploy/watchdog scripts, and any root-level files
(`requirements.txt`, entry-point scripts) that participate in its
runtime. New subprojects scaffold both the auto-update script and the
existing siblings' scope-guard regex updates in the same commit —
adding a subproject without updating siblings' guards means trade
commits would still restart NOAH (and vice versa).

## One-shot maintenance scripts — required safety pattern

Long-running one-shot scripts (backfills, migrations, large reindexes)
MUST be safe to start, interrupt, and resume without operator
hand-holding. Mirror `trade/scripts/backfill_beon.py`:

- **Idempotent** — track what's already done in a persistent store
  (e.g. scan `inbox.jsonl` / SQLite / a marker file) and skip on
  rerun. Aborting and restarting with the same args must Just Work.
- **Adaptive pacing for rate-limited APIs** — start slow, grow slower
  as the run accumulates, cap at a safe ceiling. Defaults in env vars
  so the operator can tune without editing code. Avoid the "flat 1
  s/call worked in dev → wall-of-FloodWait at 90 % done" trap.
- **Hard cap on retry backoff** — past a sane threshold (e.g. 10 min)
  exit gracefully with a Telegram alert instead of sleeping in-process.
  Rerunning next day costs nothing thanks to idempotency.
- **Disk guard** — before / during writes, check free space on `/`.
  Pause + ⏸ alert below threshold, auto-resume (▶ alert) when a
  user-provided cleanup helper (e.g. `free_disk.sh`) brings it back
  above threshold + hysteresis. Never auto-delete project data.
- **Telegram lifecycle alerts** — at minimum ⏸/▶ on pause/resume, ❌
  on abort, ✅ on completion. Reuse the project's `*_BOT_TOKEN` +
  `*_CHANNEL_CHAT_IDS`. Missing creds = silent skip, never an error.
- **Restart-safe vs concurrent deploys** — assume `git reset --hard`
  can land mid-run. Don't rely on the script's own file staying on
  disk; Python's in-memory bytecode survives. New code applies on
  the next invocation, not in-flight.

A cleanup helper script (e.g. `trade/scripts/free_disk.sh`) ships in
the same commit as anything that introduces a disk guard, so the
operator's "what do I do about the ⏸ alert?" answer is one line.

## Automation-first principle

**Every recurring operation MUST be automated** (cron / systemd timer /
asyncio task). The user runs operations alone and explicitly does not
want to ssh / paste shell commands when avoidable. Before proposing any
manual server step, ask: "can this be a cron job, systemd timer, asyncio
task, or in-process scheduler instead?" If a fix involves the operator
running a command more than once, the fix is wrong — re-design until the
runtime drives itself. Examples of what's already automated and the
pattern to follow:

- Bot lifecycle → systemd (`stock-bot.service`)
- Code updates → `stock-bot-update.service` polls git every 2 min and redeploys without manual intervention
- Stale process recovery → `stock-bot-watchdog.service` restarts if main loop hangs 12 min
- Memory pending-entry resolution → `_periodic_auto_resolve` asyncio task, 12 h cycle
- Daily dashboard regen → `_periodic_dashboard_refresh` asyncio task, 00:01 KST
- Journal log size → `SystemMaxUse=500M` in `journald.conf` (auto-trim)

**Acceptable manual steps** (rare, one-time):
- Initial systemd unit installation
- Secret rotation (API keys leaked, etc.)
- Investigating an unknown failure

**Unacceptable as a recurring ask**: "ssh in and run X every time Y happens." If it recurs, it must be automated. When proposing a fix, prefer (in order):
1. In-process scheduler (asyncio task / APScheduler)
2. systemd timer / oneshot service
3. Cron entry
4. Manual command (only if 1-3 are infeasible and impact is one-time)

## Help text maintenance (`_HELP_TEXT` in `bot/telegram_bot.py`)

The help text is **pinned as a channel announcement**. Treat it as a public-facing spec.

**Whenever a feature changes that affects user-visible behavior, update `_HELP_TEXT` in the same commit.** Specifically watch for changes in:

- New / removed commands → section 1 (명령어)
- Pipeline stage changes (analyst count, model tiers, retry logic) → section 2 (분석 흐름)
- New pre-fetched data signals → section 4 (자동 데이터 소스)
- New fundamentals RULEs → section 7 (안정성), update "RULE 1~N" count
- New stability / quality guards → section 7
- Dashboard changes → section 11

**Constraints when editing:**

- **Single Telegram message** — total ≤ 4096 UTF-16 units. Verify with:
  ```python
  import re
  text = re.search(r'_HELP_TEXT\s*=\s*"""(.*?)"""', open('bot/telegram_bot.py').read(), re.DOTALL).group(1)
  print(len(text.encode('utf-16-le')) // 2)  # must be < 4096
  ```
- Headroom target: keep ≥ 200 units of slack so future additions fit
- If a new feature pushes over the limit, **compress existing prose** rather than skip the new feature. Bullets → inline-separated phrases; explanatory commentary → terse phrasing
- **All slash commands MUST stay** (`/start`, `/help`, `/usage`, `/NVDA` etc., `/compare`)
- Content must reflect the CURRENT model state — no aspirational or deprecated features

## Stance / RULE counting

When user-facing prose mentions "분석가 4명" or "RULE 1~N", verify against code before editing:
- Analyst count: search `add_node` calls in `TradingAgents/tradingagents/graph/setup.py`
- RULE count: `grep "^.*RULE [0-9]" TradingAgents/tradingagents/agents/analysts/fundamentals_analyst.py`

## /start and /help

Already share a single function (`cmd_help`) and a single constant (`_HELP_TEXT`). Don't fork them — preserve the structural sync.

## Secrets

The user has accidentally pasted API keys in chat multiple times. When discussing `.env`, **always** suggest:
- `cat ~/stock/.env | sed 's/=.*$/=***REDACTED***/'` for sharing
- Never echo or quote the user's real key values back
- Recommend revocation if a real key was exposed
