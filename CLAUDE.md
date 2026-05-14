# NOAH Stock Bot — Project Notes for Claude

Operational rules for working in this repo.

## Workflow

1. User shares analysis output → review (system check / fact-check / improvement opportunities)
2. Propose changes as **generalized universal rules** — never ticker-specific patches
3. Wait for explicit "커밋" before committing
4. After commit, push to current `claude/...` branch

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
