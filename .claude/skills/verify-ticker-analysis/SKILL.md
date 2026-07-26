---
name: verify-ticker-analysis
description: Run the 7-axis per-ticker analysis review (numeric accuracy, prose consistency, format, logic, analyst-chain linkage, data-vs-hallucination, 5-day horizon) before shipping or reviewing a stock analysis output.
---

## Verify Ticker Analysis

Hand-authored house skill (2026-07-26) — adopted the Agent-Skills-for-Context-Engineering
SKILL.md template (routing disambiguation + numbered Gotchas + Integration section) for
this project's own recurring review workflow, sourced from CLAUDE.md's existing
"Per-ticker 분석 검증 7축" + "⛔ 과거 실수" sections rather than duplicating them —
this file is the *procedure*, CLAUDE.md stays the source of truth for the rules.

### When to Activate

- Reviewing a freshly generated per-ticker analysis (any market: US/KR/JP/TW/CN_A/HK)
  before it ships to the channel or dashboard.
- Debugging a user report that an analysis looks wrong ("숫자가 이상해", "결론이
  이상해", stale/hallucinated data suspected).
- Auditing a batch of archived analyses for a systemic bug class (e.g. after finding
  one glitch, checking whether it recurs across other tickers).

### Do NOT activate for

- Code changes to `bot/`/`trade/` that don't touch analyst prompt output — use
  `review-changes` (code-review-graph skill) instead, this skill is for **analysis
  content**, not source code.
- Backtest/screener/risk-gate correctness — those are deterministic code, verify with
  `pytest` + `review-changes`, not this prose-review procedure.

### The 7 axes (full detail: CLAUDE.md "Per-ticker 분석 검증 7축")

1. **숫자정확성** — canonical price/mcap consistent across all sections, PER/PSR/PBR
   cross-consistent, quarterly-sum vs annual within ±10% (>50x gap = unit-drop, omit),
   beta label (90d vs 5y-monthly distinguished), comma/million/%/currency-prefix format.
2. **글 일관성** — company name/industry/mcap/multiples/beta agree across all analysts,
   peer tickers = real yfinance longName, horizon consistent.
3. **형식** — empty table headers stripped, inline tables get newlines, RULE 1 period
   labels (descending year), currency+unit consistent.
4. **논리** — RULE 1-15 fired, corp-action HARD GUARD (capital reduction/split → block
   technical-indicator citation), PM override discipline, DATA OFFLINE guard (no
   fabricated disclosure format when a data key is missing).
5. **분석가 연결** — market→sentiment→news→fundamentals→Plan→Trader→PM actually use each
   other's facts, stance matches PM direction, Trader keeps Plan's numeric values.
6. **데이터 vs 환각** — company name/ticker/date/numbers trace to actual API output
   (API = ground truth, prior model knowledge = assume stale), no paraphrased/invented
   peer or insider or disclosure facts, no unit/currency leakage across markets.
7. **5거래일 horizon** — conclusion is a 5-trading-day directional call, not a 12-month
   thesis; DCF is reference-only, never the dominant driver of the verdict line.

### Gotchas (numbered, failure → fix — mirrors CLAUDE.md "⛔ 과거 실수")

1. **Deploy ≠ merged.** A draft PR alone is not a deploy — only report "배포함" after
   confirming the squash-merge landed on base (`git log origin/<base> -1`).
2. **URL template double-prefix.** `CIK{cik}` can duplicate a prefix already baked into
   the base URL → silent 404. Check the fully-interpolated URL, not just the template.
3. **HTML-escape user input / conditionals in `parse_mode=HTML` text.** Raw `<`/`>` in a
   condition string or user input breaks Telegram rendering — always `&lt;`/`&gt;`.
4. **f-string double-brace leak.** `{{cards}}` in an f-string renders as literal `{cards}`
   — use `.replace("{cards}", ...)` on a plain string, not inside an f-string.
5. **"신규" alert semantics.** New-alert logic must be a permanent seen-set + first-active
   seed + date guard — never a blanket push of every matching ticker every run.
6. **Global display conventions apply to every new surface automatically:** (a) all
   timestamps = explicit KST computation, never server-local-time-dependent (b) data
   widgets show the source's as-of time, not render time (c) commands live in one
   Telegram+dashboard registry.
7. **"Deployed" ≠ "visible on screen."** When a feature report doesn't match what's
   actually showing, trace the real data path end-to-end (source → cache →
   budget/window → render) and name the exact break point — don't guess.
8. **No speculative reporting.** If you can't verify something from this sandbox (can't
   see the VM), say so and ask for one exact verification command — don't assert. Two+
   repeats of the same symptom means stop guessing and add visibility (log to file,
   status label, warn on silent-except) until the root cause is actually found.
9. **"UI broken" reports: ask for the exact browser URL first** (port/proxy/token path).
   `curl 200` ≠ "looks right in a proxied browser." DOM measurement ≠ screenshot.
10. **Dashboard CSS**: this sandbox can't render HTML, so CSS debugging by guessing wastes
    turns — check `DESIGN.md` first, dump the generated HTML structure before guessing,
    watch for inherited `text-align`/`direction:rtl`/child-table-width-leak traps. Two+
    guesses on the same symptom → ask for a DOM/screenshot dump instead of guessing again.

### Output format

`✅ 작동` / `❌ 문제(Critical/Major/Minor)` per axis, plus a universal-fix proposal if the
bug is systemic (never a `ticker == "X"` patch — CLAUDE.md's per-ticker-fix-is-a-system-rule
principle). Only commit changes when the user gives the explicit "커밋" trigger — a review
finding is not itself a commit signal.

### Integration

- Reads: CLAUDE.md §"Per-ticker 분석 검증 7축", §"⛔ 과거 실수 — 반복 금지".
- Sibling skill: `review-changes` (code-review-graph) — use that one for source-code diffs,
  this one for analysis *content*. If a finding here turns out to be a code bug (not a
  prompt-compliance issue), hand off to `review-changes`/`impact` for the blast-radius check
  before fixing.
- Downstream: fixes to systemic issues found here typically land in
  `TradingAgents/tradingagents/agents/analysts/*.py` or `agent_utils.py` (shared analyst
  directive) — not per-ticker patches.
