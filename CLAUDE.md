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

## Per-ticker reviews are SYSTEM-WIDE rule changes

When the user shares a single analysis output (e.g. 삼성전기 2026-05-17)
and asks for a review, the resulting fixes are **always universal
rules**, never ticker-specific patches. The ticker that exposed the
issue is cited in the commit body for traceability, but the fix
applies to every future analysis of every ticker that hits the same
code path. Specifically:

- A bug surfaced by a US ticker fixes the US + KR + JP + CN code
  path, not just the US one. Phase-3 markets will inherit the fix
  automatically.
- A KR-specific failure (DART field name, KRW unit) gets a KR
  branch; the existing US branch stays correct.
- A prompt-rule update (RULE 1-6, stance extraction, etc.) applies
  to every future analyst run, not just the case it was surfaced by.
- Commit messages MUST say "rule applies to all analyses going
  forward, surfaced by the {ticker} review" — not "fix for {ticker}".

This rule exists because the bot is consumed by many users (channel
subscribers) and many tickers, but the user only sees one analysis
at a time. Without this principle, the codebase would accrete N
ticker-specific shims instead of converging on robust universal rules.

## Pre-commit verification — mandatory

**Every change must be verified before the commit goes out.** No "ship and pray." Concretely, before staging:

1. **Syntax check** every Python file touched:
   ```bash
   python3 -c "import ast; ast.parse(open('<path>').read()); print('OK')"
   ```
2. **Logic check** any non-trivial pure function — write a quick smoke
   test inline (in a `python3 -c '...'` one-liner is fine) that exercises
   the happy path and one obvious edge case. Mirror the existing pattern
   in this repo: when stance extraction changed, the diff was validated
   with four input cases before the commit. Do the same for any new
   parser, classifier, mapper, or formatter.
3. **Length check** for `_HELP_TEXT` whenever it's touched — see Help
   text maintenance section below.
4. **Cross-file consistency check** when a rule moves between modules —
   `grep -rn` to confirm no orphaned references remain (e.g. when
   renaming RULE numbers or relocating a helper).
5. **Multi-step phase work**: after each item finishes, verify the
   item works in isolation (syntax + smoke test + help text if user-
   visible) BEFORE starting the next item in the sequence. Report the
   verification result to the user; only continue when they signal
   "OK / 다음" or no objection arrives. Don't batch four items into
   one commit when the user asked for sequential validation.

Skipping verification is treated the same as skipping the explicit-
commit-request rule — never do it.

## Help text registration of changes — mandatory

Whenever a change ships that is user-visible (new command, new data
source, new RULE, new analyst, new dashboard feature, removed
behavior, etc.), `_HELP_TEXT` MUST be updated in the SAME commit. The
help is pinned as a channel announcement; out-of-sync help is treated
as a public spec bug. The two surfaces it must keep current are spelled
out under "Help text maintenance" below (current-state sections 2-11
+ '진행 중 / 예정' section 12).

**If the new content cannot fit inside the 4096 UTF-16 cap after
reasonable prose compression, STOP and REPORT to the user.** Specifically:
- Try compressing existing sections first (bullets → inline phrases,
  prose → terse fragments).
- If still over the cap, surface the situation: "현재 help 길이 X UTF-16,
  추가 필요분 Y, 한도 4096. 압축 더 시도할지 / 어느 섹션을 줄일지 / 한도
  올리기 위해 다중 메시지로 분할할지 결정 요청." Do NOT silently drop a
  feature, do NOT silently split into multiple messages, do NOT commit
  with a too-long _HELP_TEXT. The default is to stop and ask.

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

## Portfolio Manager override discipline

When the four-analyst stance bar shows unanimous (4-0) or
near-unanimous (3-0 with 1 abstain) agreement on a direction AND
the Wall Street / KR analyst consensus is 강매수 / 매수, the
Portfolio Manager's verdict MAY override to the opposite direction
ONLY if at least one of these triggers is named in the PM rationale:

- 5-day-horizon technical extreme: RSI > 75 (for Buy-reverse to
  Underweight) or RSI < 25 (for Sell-reverse to Overweight)
- Imminent specific catalyst: earnings within ±5 days, FOMC,
  guide cut, regulatory event
- Stance-vs-decision mismatch detector explicit warning text was
  visible to the PM in its prompt
- Data-availability HOLD per portfolio_manager.py guard

Without ONE of those triggers documented, the PM defaults to
following the analyst-consensus direction: Buy / Overweight when
analysts lean buy, Hold when split, Sell / Underweight when
analysts lean sell. This rule exists because the bot was producing
"분석가 4명 합의 보유 + 컨센서스 강매수 → PM Underweight on RSI
alone" patterns (현대모비스 / 호텔신라 / 한전 2026-05-17 cluster);
unanimous analyst signals plus consensus shouldn't be flipped on
a single technical indicator without explicit justification.

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

**The help text doubles as a public spec AND a public roadmap.** Two surfaces to keep current:

1. **Current state description** — sections 2-11 describe what the model
   does TODAY. Every time a user-visible behavior changes (new analyst,
   new RULE, new pre-fetch source, new quality guard, new dashboard
   feature, dropped feature), update the relevant section in the same
   commit. Stale text is a bug.
2. **"현재 진행 중 / 예정" section** — a final section listing every
   open multi-step initiative (e.g. Korean market support Phase N,
   pending data-source integrations, planned model upgrades). Update
   this section in the same commit that creates / closes a TODO. The
   user pins the help as a channel announcement, so this section
   functions as a publicly-visible roadmap. Never let it drift.

Both surfaces must fit inside the 4096 UTF-16 cap — keep them concise.

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

## Multi-market expansion (US → KR → JP → CN)

Phase tracking — what's done, what's blocking the next phase:

**Phase 0 — Infrastructure (done)**
- `bot/market.py` — `detect_market()` + `MARKET_CONFIG` (US/KR/JP/CN)
- `TICKER_RE` accepts numeric-start tickers (was the blocker for `005930.KS`)
- `_resolve_benchmark` picks KODEX sector ETFs for KR tickers
- `get_sector_relative_strength` uses KOSPI 200 as broad benchmark for KR

**Phase 1 — KR data sources (done)**
- `bot/dart_client.py` — DART OpenAPI thin wrapper (corp_code map cached 30d on disk, 공시 / 임원지분 / 실적 윈도)
- KR-tilted macro 9-series in `_MACRO_SERIES_KR` (USD/KRW · KOSPI · KOSDAQ · 美10Y · VIX · WTI · 구리 · CNY · JPY); `get_macro_for` routes by market
- KR consensus path: yfinance 1차 + `bot/fnguide_consensus.py` (FnGuide CompanyGuide HTML scrape) 2차 fallback; small-cap KOSDAQ degrades to "분석가 커버리지 없음" silently
- `build_instrument_context` injects DART block (최근 30일 공시 / 임원·주요주주 지분 top 5 / 다음 정기보고서 윈도) for KR tickers
- Currency rendering market-aware: ₩ whole-won for KR, $ for US

**Phase 2 — KR validation (in progress)**
- ✅ Validated: `/005930.KS` (SNG 2026-05-17), `/039030.KS` (이오테크닉스 2026-05-17). Fixes shipped from each run.
- Known yfinance KR data quality issues — list grows as we hit them, fix in code where possible, document workarounds otherwise:
  - **MUTUALFUND misclassification**: yfinance tagged 039030.KS as MUTUALFUND despite being a regular KOSDAQ company. Fix: in `build_instrument_context`, override `qt` to EQUITY for KR tickers when DART has the corp_name (DART corp_code only includes real corporations; ETFs/funds aren't in DART). KODEX/TIGER ETFs correctly fall through to the fund branch.
  - **Stock-split staleness in historical series**: yfinance occasionally serves an unadjusted historical price series alongside a split-adjusted current price (or vice versa), producing impossible-looking gaps like 039030.KS current ₩202,000 vs 50d SMA ₩445,660 (-55%). Fix: `build_instrument_context` flags any |current − fiftyDayAverage| / fiftyDayAverage > 0.40 with a sanity warning telling the market analyst to skip MA-based comparisons.
  - **Sparse coverage for small-cap KOSDAQ**: handled in Phase 1 — yfinance returns None for consensus fields and we silently degrade to '분석가 커버리지 없음'.
- Watch for additional KR failure modes: FnGuide page structure shifts, DART 429 / rate limits, currency unit drift in fundamentals tables.
- Update analyst-count / RULE-count if any KR-specific rule lands.

**Phase 3 — JP / CN expansion** (further out)
- Same shape: market-specific benchmark mapping + data source adapters
- TOPIX sector ETFs for JP, CSI 300 sectors for CN

## TODO

- **KR consensus implementation** (research complete — see Phase 1).
  yfinance primary for KOSPI Top 100 (already returns `targetMeanPrice`
  + `numberOfAnalystOpinions` + `recommendationMean` for `.KS`/`.KQ`
  large/mid-caps); FnGuide CompanyGuide HTML scrape as fallback when
  yfinance is empty. DART has ZERO sell-side consensus data, confirmed
  — only regulatory filings, do not waste time querying it for target
  prices. KIS Developers requires a 한국투자증권 brokerage account
  (no-go for general users). Alpha Vantage / Finnhub free tiers don't
  cover `.KS`/`.KQ` analyst aggregates.

- **Hydrator-registry refactor for pre-fetch** (deferred). When
  `build_instrument_context`'s data sources grow past ~10 (currently
  7: yfinance .info, yfinance averages, macro 9-series, risk metrics,
  sector strength, DART block, FnGuide consensus), revisit the
  typed-stage pattern from xai-org/x-algorithm's `candidate-pipeline`
  crate: each source declares its inputs / outputs / failure mode,
  the orchestrator handles parallelism + per-stage error isolation
  instead of the current imperative try/except chain. Triggered by
  Phase 3 (JP / CN expansion) which will add Japan / China data
  sources. Not worth introducing now — the chain is still readable
  at 7 sources.
