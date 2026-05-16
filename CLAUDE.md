# NOAH Stock Bot — Project Notes for Claude

Operational rules for working in this repo. Apply to **every subproject** in
this repo (currently: `bot/` NOAH stock-bot, `trade/` Korea import/export bot).

## Pre-push verification — mandatory

Before pushing **any** commit, run every applicable check below and
state the results in the commit message (or as a single explicit
"verified" line). Pushing code that hasn't been verified is a
regression even when it compiles.

1. **Tests** — `python3 -m unittest discover <project>/tests` must
   show all green. Never push with failing or skipped tests unless
   the commit message names the test and explains why.

2. **Syntax** — for every changed `*.py` run `python -c "import ast;
   ast.parse(open('FILE').read())"`; for every changed `*.sh` run
   `bash -n FILE`; for every changed systemd unit grep for the
   required `[Unit]` plus the matching `[Service]` / `[Timer]`.

3. **Help-text** — if `_HELP_TEXT` (or any user-facing pinned spec)
   was touched, verify:
   - UTF-16 length < 4096 (Telegram cap, see Help text maintenance
     section). Headroom target ≥ 200.
   - The 최종 갱신 line was updated and one-line-summarizes the
     actual change.
   - Every listed feature in the commit message resolves to a
     keyword search hit in the help text.

4. **Render / smoke check** — for dashboard or report-generating
   changes, render once against a representative dataset (real
   store.db when available, in-memory seed otherwise) and grep the
   output for the markers each new feature should produce. Tests can
   replace this when they assert the same markers.

5. **Scope coverage** — when the user supplies a numbered list of
   work items, the commit message must enumerate which items are
   addressed in this commit and which are deferred. Don't claim a
   list of N items is "all done" without one ✓ per item.

6. **Verify in the artifact, not in your head** — actually run
   `grep` / open the rendered HTML / inspect the SQLite output.
   Implementing-then-trusting-the-diff is how regressions ship.

If any check fails, fix forward and re-run all checks; never push
"will fix after" or "should be fine".

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

## Deploy notifications — Telegram on every commit, lifecycle-shaped

Every deploy automation in this repo (any auto-update script, any
provisioner, anything that promotes code to a running service) MUST
send the project's Telegram channel at least one message per new
commit so the operator can track exactly what landed on the host:

Scope-relevant commits (the script's own subproject) → full
restart cycle:
- `🚀 <b>배포 시작</b>: <code>{old_sha7}</code> → <code>{new_sha7}</code>` plus the new commit subject on a new line
- `✅ <b>배포 완료</b>: <code>{old_sha7}</code> → <code>{new_sha7}</code>` plus the new commit subject
- `❌ <b>배포 실패</b>: <reason> (<code>{new_sha7}</code>)` on any abort path (`git reset` failure, `systemctl restart` failure, service not `active` after restart)

Scope-irrelevant commits (docs, shared infra, sibling subproject) →
pull silently but still notify so nothing on the host is invisible:
- `📝 <b>운영 업데이트</b>: <code>{old_sha7}</code> → <code>{new_sha7}</code>` plus the subject, plus `<i>재시작 불필요 — doc / 다른 서브프로젝트 변경</i>`

Rationale: the user runs the host alone and treats the channel as the
single source of truth for what's live. Both silent deploys and
invisible doc-only commits break that trust — every commit gets
exactly one notification (📝 or 🚀+✅/❌), never zero, never
duplicated.

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

When a subproject grows a sibling service (e.g. trade-bot-dashboard
alongside the main trade-bot), the auto-update script restarts BOTH
on changes that touch the sibling's code, with a `sudo -n` graceful
fallback when the operator hasn't added the sibling-restart sudoers
entry yet. The deploy notification appends a one-line note about the
sibling restart (or the missing-sudoers warning) so the operator can
spot setup gaps from Telegram instead of digging through journals.

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

## Help text maintenance — every user-facing bot

Each user-facing bot in this repo carries a `_HELP_TEXT` constant. Treat
it as the public-facing spec — pinned in the channel, the first thing
the operator reads on `/help`.

**Whenever a change affects user-visible behavior, update the relevant
`_HELP_TEXT` in the same commit.** The trailing `최종 갱신: YYYY-MM-DD
— <one-line summary>` line records what just changed so the operator
can track drift between releases by reading the spec only.

Current help texts:

- `bot/telegram_bot.py::_HELP_TEXT` — NOAH 주식분석 봇
- `trade/bot.py::_HELP_TEXT` — 한국 수출입 데이터 대쉬보드 봇

### NOAH stock-bot — what to watch for

- New / removed commands → section 1 (명령어)
- Pipeline stage changes (analyst count, model tiers, retry logic) → section 2 (분석 흐름)
- New pre-fetched data signals → section 4 (자동 데이터 소스)
- New fundamentals RULEs → section 7 (안정성), update "RULE 1~N" count
- New stability / quality guards → section 7
- Dashboard changes → section 11

### Trade-bot — what to watch for

- Dashboard URL / port / auth → section 2 (대쉬보드)
- BeOn cycle change → section 3 (발표 사이클)
- View / sort / badge changes → sections 4-6
- New filter, search behavior, CSV column, modal capability → section 7 (부가 기능)
- New commands → section 8 (명령어)
- New systemd unit, refresh interval → section 9 (자동화 systemd)
- Always touch the trailing `최종 갱신` line on the same commit

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
