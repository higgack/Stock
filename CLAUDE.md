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

## ⛔ UNIVERSAL CHANGES ONLY — every change applies to every market

**This is the single most important rule in this file. Read it first.**

Every change shipped to this codebase — prompt rule, polish step,
data-source integration, dashboard surface, helper module, schema,
configuration default — **applies universally to US + KR + JP (+
future CN/EU) by default**. Market-specific code is an exception
that requires explicit justification, not the default.

User confirmed this principle 2026-05-18: "모든 변경은 universal
변경임" — embed it permanently here so no future Claude session
backslides into ticker-specific or market-specific patches when a
universal rule would do.

Concretely:

1. **Default behavior**: when in doubt, make the rule apply to all
   markets. Even when surfaced by a single-market case (e.g. a 두산
   bug exposed by a KR analysis), the resulting commit should ship
   the fix in code paths that ALL markets traverse — not branch
   it behind `if market == "KR"`.

2. **Market-specific code requires a documented data-source reason**:
   - DART / EDINET / Naver / Kabutan / BoK ECOS / FRED / pykrx
     are external APIs only one market consumes — code touching
     them is naturally market-gated.
   - Korean / Japanese language output (RULE 9 chaebol, 백만 polish,
     RULE 11 JP keiretsu deferred) is a regional-output specialization,
     not a different evaluation rule.
   - Anything else — RULEs 1-8, PM discipline, beta labels, polish
     steps for unlabeled series, canonical 시가총액, Stop Loss
     formatting, dashboard accuracy criterion, outcome verdict text,
     auto_resolve gate, search box behavior, ETF benchmark display
     — is universal. No `if market ==` gate.

3. **Cross-market parity audit on every commit**: when adding any
   guard or rule, ask "does this apply to US + KR + JP equally?" If
   yes, no gate. If no, document WHY in the commit body. The
   commit 41068ca "universal guard symmetry: 6 gaps closed" is the
   canonical example — it found 6 US-side asymmetric weaknesses
   where KR had richer coverage (mandatory peer set, naming
   directive, RULE 10/11 etc.) and shipped equivalent US support
   in one commit.

4. **Commit message phrasing**: every commit body MUST contain the
   phrase "Rule applies to all analyses going forward" (or
   equivalent — "applies universal-by-default", "covers US + KR +
   JP", etc.). This is the structural enforcement of rule (1) —
   if the phrase doesn't fit, the change probably IS too
   market-specific and needs broadening before commit.

5. **Per-ticker review fixes are system-wide**: when the user
   shares a single analysis output (코미코 / Toyota / 두산 / JPM
   /etc.) and asks for review, the resulting fixes are ALWAYS
   universal — never ticker-specific. The ticker exposing the
   issue is cited in the commit body for traceability; the fix
   applies to every future analysis of every ticker that hits the
   same code path.

6. **What this looks like in practice — recent examples**:
   - 코미코 2026-05-17 surfaced 5 issues (corp action staleness,
     peer multiples missing, etc.). Commits c6eeef7 + 41068ca
     fixed all 5 universally — every market gets the corp-action
     HARD GUARD (KR DART scan + JP EDINET scan + universal yfinance
     .splits), every market gets pre-fetched peer multiples, every
     market gets the canonical 현재가/시가총액 directive.
   - Toyota 7203.T 2026-05-18 surfaced 6 (EDINET fabrication,
     market cap divergence, PM override discipline violation,
     4-digit comma break, Comps subject row, beta label). Commit
     1612993 fixed all 6 universally — Rule A DATA OFFLINE applies
     to KR + JP + future CN keys, canonical market cap uses
     market-aware currency rendering, PM discipline is code-enforced
     for every analysis regardless of market.
   - 두산 000150.KS 2026-05-18 surfaced 8 (백만 unit, Conglomerates
     peer set, chaebol RULE 9 skip, PM discipline silent no-op,
     부채비율 배 unit, etc.). Commit 7d6824c fixed all 8 with
     universal-default polish steps + PM discipline hardening
     applying to US/KR/JP, plus appropriately KR-specific guards
     (재벌, 백만) with documented reasons.

Without this principle the codebase would accrete N ticker-specific
shims instead of converging on robust universal rules. The bot is
consumed by many users (channel subscribers) and many tickers, but
the user only sees one analysis at a time — each review must produce
a fix that's seen by the next thousand analyses.

## Per-ticker reviews are SYSTEM-WIDE rule changes (legacy section header — see rule 5 above)

Same content as rule 5 above. Keep the section header so old links
into this file still resolve. Mechanics:

- A bug surfaced by a US ticker fixes the US + KR + JP + CN code
  path, not just the US one. Phase-3 markets will inherit the fix
  automatically.
- A KR-specific failure (DART field name, KRW unit) gets a KR
  branch; the existing US branch stays correct.
- A prompt-rule update (RULE 1-12, stance extraction, etc.) applies
  to every future analyst run, not just the case it was surfaced by.
- Commit messages MUST say "rule applies to all analyses going
  forward, surfaced by the {ticker} review" — not "fix for {ticker}".

This rule exists because the bot is consumed by many users (channel
subscribers) and many tickers, but the user only sees one analysis
at a time. Without this principle, the codebase would accrete N
ticker-specific shims instead of converging on robust universal rules.

## Per-ticker analysis verification framework

When the user shares an analysis output for review (KR/JP/TW/US — any
market), audit it against ALL seven axes below before listing issues.
Skipping an axis silently misses bugs that compound across markets.
User confirmed this framework 2026-05-18 ("이것도 클로드 엠디에 명시").

**Axis 1: 숫자 정확성 (numbers + system reflection)**
Check that every system guard fired correctly + data integrity holds:
- Canonical 현재가 / 시가총액 (Rule B): all sections cite the SAME value.
  Different analysts producing different 시총 for same stock is FORBIDDEN.
- PER / PSR / PBR / EV-EBITDA: cross-section consistency. News PER 29.5
  vs Fundamentals PER 30.43 for same stock = Rule E violation.
- 분기 합 vs 연간 sanity (RULE 8.1): sum of Q1-Q4 should be within
  ±10% of annual. >50x divergence = yfinance unit drop, OMIT corrupt row.
- 베타 라벨 (Rule F): "(90일, vs benchmark)" + "(5년 월간, vs S&P 500)"
  must be labeled distinctly so reader doesn't see two unlabeled betas.
- 4자리 콤마 strip + 백만 polish + 부채비율 % unit + Stop Loss 콤마 +
  통화 prefix (NT$/₩/¥/HK$/$) all applied at output layer.

**Axis 2: 글의 일관성 (text consistency across sections)**
- Same stock 다른 분석가 사이: 회사명 / 산업 / 시총 / multiples / 현재가
  / 베타 같은 facts가 일치해야. fabrication / paraphrase 차단.
- Peer 회사명: yfinance longName 그대로 사용. 2379.TW를 'ASE Tech'로
  부르고 다른 분석가는 'Realtek'으로 부르면 위반 (MediaTek 2454.TW
  2026-05-18 케이스).
- 5거래일 평가 윈도 vs 장기 12개월 narrative: 분석가가 horizon 일관성
  유지하는지. PM이 "5거래일에서는 ..." 명시하는지.
- past_outcomes에서 인용된 지난 추천이 현 분석의 thesis와 어긋나는지.

**Axis 3: 형식상 문제 (markdown + formatting)**
- 빈 표 헤더 ('| col1 | col2 |' + '|---|---|' followed by no data rows)
  자동 strip 필요. LLM이 표 시작 후 prose로 fallback한 패턴.
- Inline table merge ('|---|---|---| | row | val |' 한 줄로 합침) —
  newline 자동 삽입 필요.
- RULE 1 PERIOD LABELS: 'FY25 X | FY24 Y' 형식 강제, 연도 내림차순.
- 통화 + 단위 정합성: '주' (shares) vs '元/원' (currency) 혼합 금지.
  '약 X만 원 주' 같은 leak (MediaTek 2454.TW 2026-05-18 case).
- ADR 표기: TSMC↔TSM, UMC↔UMC 등 cross-listing은 명시는 OK,
  multiples 섞기 금지.

**Axis 4: 논리 구조 (logic + RULE adherence)**
- RULE 1~14 enforcement: 각 RULE이 발화해야 할 케이스에서 발화했는지.
  특히 RULE 10/11/12/14 dominant variable enforcement — 산업 변수
  모두 결론에 명시 의무 (TSMC '美 對中 수출규제' 누락 케이스).
- CORPORATE ACTION HARD GUARD: 감자/분할/병합 detected → 기술 지표
  분석 자동 차단 (text directive + polish banner).
- PM override discipline (Rule C): 분석가 다수와 PM 결정 방향이
  다르면 trigger (RSI>75/<25, 임박 catalyst, mismatch warning,
  data-availability HOLD) 명시 의무.
- DATA OFFLINE 가드 (Rule A): API 키 부재 시 LLM이 EDINET/MOPS/
  DART 형식 data fabrication 차단.
- HARD GUARD 본인 인지: 분석가가 corp action 인용하고도 MA/MACD/
  RSI 분석을 진행하는 패턴 (코미코/프로텍 케이스).

**Axis 5: 분석가 간 연결성 (cross-analyst flow)**
- 시장 → 감정 → 뉴스 → 펀더멘털 → Plan → Trader → PM 순서로 각
  분석이 앞 분석의 사실을 활용하는지. 끊김 / 모순 / 같은 facts에
  다른 해석을 다른 곳에 두면 위반.
- Stance bar (시장:보유·감정:매수·뉴스:매수·펀더멘털:보유) ↔ PM
  결정 (Overweight) 방향 일관성. 다수와 반대 방향이면 mismatch
  warning + Rule C trigger 명시.
- Trader가 Plan에서 받은 entry / stop / position을 그대로 활용하는지
  (자기 마음대로 변경 금지).

**Axis 6: 데이터 vs LLM fabrication 구분**
- 회사명 / 티커 / 날짜 / specific 수치가 yfinance / DART / MOPS /
  EDINET 출처와 일치하는지. 다음 항목 특히 의심:
  - Peer 회사명 (yfinance longName 비교)
  - 임원지분 / 내부자 명단 (raw 출력 수 vs LLM 표시 수)
  - 공시 dates + 사건 (DART/EDINET/MOPS 원본 vs 분석가 paraphrase)
  - 5%+ 대량보유 BlackRock / Vanguard 등 (specific % + 날짜)
  - 회계연도 EPS / 시총 (corp action 영향 시 LLM 자체 recalc 금지)
- "약 X백만 원" / "약 X만 원 주" 등 단위-통화 leak 패턴.

**Axis 7: 5거래일 horizon 적합성**
- 결론이 "장기 6-12개월 thesis" 가 아닌 "5거래일 가격 방향성" 관점
  인지. Bull/Bear 사이드 모두 5거래일 horizon 고려하는지.
- DCF / 밸류에이션 추정치는 reference로만, 5거래일 decision은
  momentum / catalyst / sector flow 기반.
- Buffett/Lynch (Bull persona) 가 "10년 보유" 관점 주장하면 PM이
  "5거래일 평가 horizon" reminder + adjust 의무.

---

When auditing, walk all 7 axes systematically. Don't skip — each axis
catches a distinct class of bug. Result format:
- ✅ "작동 확인된 부분" — list what's working per axis
- ❌ "발견된 문제" — list issues per axis with severity (Critical /
  Major / Minor)
- Proposed universal fixes for each ❌ — never ticker-specific
- Commit only on user's explicit "커밋" signal

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
- Code updates → `stock-bot-update.service` polls git every 1 min and redeploys without manual intervention (was 2 min; tightened 2026-05-21 with SV automation rollout)
- Stale process recovery → `stock-bot-watchdog.service` restarts if main loop hangs 12 min
- Memory pending-entry resolution → `_periodic_auto_resolve` asyncio task, 12 h cycle
- Daily dashboard regen → `_periodic_dashboard_refresh` asyncio task, 00:01 KST
- Journal log size → `SystemMaxUse=500M` in `journald.conf` (auto-trim)
- Standard View code updates → `sv-update.service` polls git every 1 min, rsyncs `standardview/scripts` + `standardview/backend` into live tree, restarts backend if changed, defers when daily_generator is running. Same pattern as `stock-bot-update`.
- Standard View cache rollover → `sv-cache-rollover.service` runs 00:05 KST daily, flushes `macro_news_cache` so the first news-brief call of the new date regenerates from scratch (fixes the 2026-05-21 midnight stub-cache bug).
- Standard View watchdog → `sv-watchdog.service` runs every 30 min; if `latest.html` is >90 min stale during 08:00-22:00 KST and the BUSY_MARKER is clear, re-kicks `daily_generator.py` in the background.

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

When the analyst stance bar shows **consistent** agreement — defined
as ALL the analysts that actually ran (i.e. weren't pre-skipped for
missing data) leaning the same way — the Portfolio Manager's verdict
MAY override to the opposite direction ONLY if at least one of these
triggers is named in the PM rationale:

- 5-day-horizon technical extreme: RSI > 75 (for Buy-reverse to
  Underweight) or RSI < 25 (for Sell-reverse to Overweight)
- Imminent specific catalyst: earnings within ±5 days, FOMC,
  guide cut, regulatory event
- Stance-vs-decision mismatch detector explicit warning text was
  visible to the PM in its prompt
- Data-availability HOLD per portfolio_manager.py guard

This rule covers ALL voter-count combinations, not just 4-of-4:
- 4-of-4 unanimous
- 3-of-4 with 1 abstain
- **2-of-2 with 2 abstain** (news / sentiment skipped — common for
  KR/JP low-coverage tickers; the smaller voter count does NOT
  lower the trigger bar)
- 3-of-3 with 1 abstain

Without ONE of those triggers documented, the PM defaults to
following the analyst direction: Buy / Overweight when analysts
lean buy, Hold when analysts lean hold (even a 2-of-2 partial Hold
counts), Sell / Underweight when analysts lean sell. This rule
exists because the bot was producing "analysts 합의 보유 → PM Sell
on a single technical indicator" patterns (현대모비스 / 호텔신라 /
한전 2026-05-17 cluster, **코미코 2026-05-17** with only 시장 +
펀더멘털 voters both Hold and PM flipped to Sell on RSI 55.36 /
'단기 모멘텀 약화'). Consistent analyst signals should not flip on
a single technical indicator or generic phrasing without explicit
justification.

**Corollary** — when build_instrument_context emits a CORPORATE
ACTION IN-FLIGHT (HARD GUARD), the standard technical triggers
(RSI / MACD / SMA) are invalid for the run and CANNOT be used as
override triggers; only the imminent-catalyst or data-availability
HOLD triggers apply.

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

## Multi-market expansion (US → KR → JP → TW → CN)

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

**Phase 3 — JP expansion (foundation shipped)**
- Ticker / classification: `bot/market.py` has `_JP_ENGLISH_ALIAS` (50+ romanized JP company names → `.T` tickers — TOYOTA→7203.T, SONY→6758.T, NINTENDO→7974.T, MUFG→8306.T, SOFTBANK→9984.T, …) + `_JP_INDUSTRY_PEERS` (30+ industries). `resolve_english_alias` / `resolve_peer_set` are market-aware.
- Sector strength: `_JP_INDUSTRY_OVERRIDES` maps yfinance industries to NEXT FUNDS TOPIX-17 ETFs (1617 식품 ~ 1633 부동산); `_JP_BROAD_FALLBACK = ("1306.T", "TOPIX (1306)")`.
- Macro: JP-tilted 9-series in `_MACRO_SERIES_JP` (USD/JPY · Nikkei 225 · TOPIX · 美 10Y · VIX · WTI · 구리 · USD/CNY · USD/KRW); `get_macro_for` dispatches by market.
- FRED client (`bot/fred_client.py`) — BoJ 정책금리 / JGB 10Y / JP CPI YoY via FRED's OECD/BoJ mirror; 12h disk cache; same shape as `bok_ecos_client`.
- EDINET client (`bot/edinet_client.py`) — daily document listing API, secCode filter (4-digit ticker + 0 checksum), surfaces 120/130/140/150/160/170/180/190/350/360 doc types (annual / quarterly / semi-annual / 임시 / 대량보유). Per-day disk cache (forever for past days, 12h for today). `next_earnings_window` infers JP fiscal-year-end 3/31 + 45/90-day filing windows.
- Kabutan consensus (`bot/kabutan_consensus.py`) — HTML scrape for target / rating (強気/中立/弱気 → 매수/보유/매도) / analyst count / last_report_date. Fallback path when yfinance is empty; mirrors `fnguide_consensus.py`.
- Kabutan news (`bot/kabutan_news.py`) — HTML scrape of `kabutan.jp/stock/news?code=NNNN`; year-implicit date resolution; 4h cache; same `{date, title, source, link, summary}` schema as `naver_news_client`.
- `build_instrument_context` injections (JP branch): JP naming directive (yfinance longName + ticker), JP currency directive (¥, 兆/億, 회계연도 3/31 인지, financialCurrency ≠ JPY 경고), EDINET 공시 / 5% 대량보유 / 분기 윈도 block, Kabutan 뉴스 block, FRED JP 매크로 block, J-REIT MUTUALFUND-misclass override.
- `get_market_signals_for` JP path: yfinance 1차 + Kabutan 2차 consensus fallback, last_report_date staleness warning shared with KR.
- `has_recent_news` JP fallback: Kabutan when yfinance .news returns 0 articles.
- `fundamentals_analyst.py` RULE 11 (JP INDUSTRY-SPECIFIC POLICY / MACRO VARIABLES — JP ONLY): 自動車·銀行·不動産·製薬·商社·반도체 장비·통신·철강·전력 9개 산업별 단일 매크로 변수 명시 의무. Keiretsu cross-holding rule deferred (current keiretsu coupling is weak vs KR chaebol).
- `analyzer._display_ticker` JP branch: prepend longName ("Toyota Motor Corporation / 7203.T") when available.
- Needs user-supplied: `EDINET_API_KEY` (free at https://disclosure2.edinet-fsa.go.jp/), `FRED_API_KEY` (free at https://fredaccount.stlouisfed.org/). Kabutan needs no key.
- Phase 3 validation: pending — `/7203.T` (Toyota), `/6758.T` (Sony), `/8306.T` (MUFG) will be the first three test cases once keys are loaded.

**Phase 4-TW — Taiwan expansion (foundation + clients + RULE 14 shipped)**
- User chose TW as Phase 4 priority 2026-05-18 after comparison vs CN:
  TW has no GFW / no geofence, yfinance US-quality coverage on large/
  mid-caps, official MOPS disclosure portal freely accessible, cleaner
  industry policy variables, ADR cross-listings (TSMC↔TSM etc.) for
  reader recognition. Expected analysis quality ~90% (JP-level), vs
  CN ~70%.
- Ticker / classification: `bot/market.py` has `_TW_ENGLISH_ALIAS`
  (60+ aliases — TSMC, MediaTek, HonHai / Foxconn, UMC, Quanta,
  Pegatron, Largan, Fubon, Cathay, Evergreen, AVC, Auras, Tripod,
  Unimicron, ASE, Powertech, AUO, Innolux, etc.) + `_TW_INDUSTRY_PEERS`
  (25 industries, tech-heavy). `MARKET_CONFIG['TW']` uses TWD / NT$
  / 0050.TW broad benchmark.
- Sector strength: `_TW_INDUSTRY_OVERRIDES` maps yfinance industries
  to Yuanta + Fubon TW sector ETFs (0053 electronics, 0055 financial,
  00891 semiconductor ESG, 0050 broad). `_TW_BROAD_FALLBACK = ('0050.TW',
  'TAIEX 50 (0050)')`.
- Macro: TW-tilted 9-series in `_MACRO_SERIES_TW` (USD/TWD · TAIEX
  ^TWII · 美 10Y · VIX · WTI · 구리 · USD/JPY · USD/CNY · SOXX) —
  SOXX included because TW market cap is ~60% TSMC + MediaTek + OSAT
  chain, all directly tied to the global semi cycle.
- FRED client extended with `_SERIES_TW`: CBC 重貼現率 + TW 10Y +
  TW CPI YoY via OECD mirror. Same FRED_API_KEY as JP.
- MOPS client (`bot/mops_client.py`) — TWSE/TPEx official disclosure
  portal. 重大訊息 + 內部人持股 + next-earnings window (TW fiscal-
  year-end 12/31). No API key. ROC date → Gregorian conversion.
- 鉅亨網 client (`bot/cnyes_client.py`) — TW's largest 繁體中文
  financial news portal, per-ticker tag pages. Same schema as Naver
  / Kabutan. No API key.
- `build_instrument_context` injections (TW branch): TW naming
  directive (longName + ticker, ADR cross-listing note), TW currency
  directive (TWD/NT$, 兆/億/万 元, financialCurrency mismatch warning),
  MOPS 重大訊息 / 內部人持股 / 次期 보고 윈도 block, 鉅亨網 뉴스
  block, FRED TW 매크로 block.
- `get_market_signals_for` TW path: yfinance only (no scrape fallback
  yet; cnyes consensus scrape would be Phase 4-TW-E expansion if
  yfinance proves insufficient for mid-cap TW names during validation).
- `has_recent_news` TW fallback: cnyes when yfinance .news returns 0.
- `fundamentals_analyst.py` RULE 14 (TW INDUSTRY-SPECIFIC POLICY /
  MACRO VARIABLES — TW ONLY): 14 industries with single dominant
  variable (半導體 Foundry → 美 對中 수출규제 + AI capex 사이클 +
  USD/TWD; IC 設計 → 5G/AI smartphone 사이클; OSAT → CoWoS capacity;
  EMS → iPhone + AI 서버; 散熱 → NVIDIA Blackwell 채택률; PCB →
  ABF substrate; 광학 → iPhone camera; 패널 → DSCC index + 한·중
  panel 가동률; 金融 → CBC 重貼現率 + 생보 USD/TWD 손익; 海運 →
  SCFI; 通信 → 5G ARPU; 石化 → 油價 + 中国 PE/PP 수요; 自動車 →
  Toyota brand + USD/TWD; Biotech → FDA + Medicare). ADR / .TW
  multiples mixing FORBIDDEN.
- `analyzer._display_ticker` TW branch: longName prefix ("Taiwan
  Semiconductor Manufacturing Company / 2330.TW").

**Phase 4-CN — China + HK expansion (Foundation shipped 2026-05-18)**

Phase 4-CN-A Foundation shipped 2026-05-18 — all 6 design decisions
(AKShare 전체 설치 lazy import, CN_A + HK 시장 분기, A주 default + HK
명시 dual-listing, STAR/ChiNext ±20% RULE 13 명시, 港股通 flow High
priority, RULE 13 13 산업 모두) user-ratified at recommended Option α
defaults. Foundation includes:
 • bot/market.py — MARKET_CONFIG['CN_A']/['HK'] 분리, detect_market
   .SS/.SZ/.BJ→CN_A + .HK→HK, detect_cn_sub_market() returning
   CN_A_MAIN/CN_A_STAR/CN_A_CHINEXT/CN_A_BJSE/HK_MAIN/HK_GEM (for
   RULE 13 ±limit reasoning), _CN_ENGLISH_ALIAS (~60 entries —
   Tencent/BABA/JD/BYD/CATL/SMIC/Moutai/4대은행/Internet VIE), peer
   set dicts _CN_A_INDUSTRY_PEERS + _HK_INDUSTRY_PEERS covering all
   13 RULE 13 industries (백주/은행/부동산/Internet VIE/半導體/EV/배터리/
   광전지/보험/통신/항공/석유철강/소비가전).
 • sector_strength_tools.py — _CN_A_INDUSTRY_OVERRIDES (国泰CES半导体/
   华宝中证银行/医疗/주류/汇添富 소비/国泰 신에너지차/易方达 신에너지/
   光伏/통신/부동산/에너지/강철/家电 ETFs), _HK_INDUSTRY_OVERRIDES
   (恒生科技 internet+semi+EV/HSCEI 은행+보험+石油+통신/HK property),
   broad fallbacks 510300.SS / 2800.HK.
 • macro_context_tools.py — _MACRO_SERIES_CN_A (9종: USD/CNY + CSI 300
   + HSI + 美10Y + VIX + WTI + 구리 + USD/JPY + USD/HKD), _MACRO_SERIES_HK
   (HKD peg + HSI + HSCEI + 美10Y + VIX + WTI + 구리 + USD/CNY + CSI 300).
 • bot/dashboard_server.py — _TICKER_RE 정규식이 첫 문자 [A-Z0-9]로 완화
   되어 CN/HK 숫자 시작 ticker 도 대시보드 URL 통과.
 • bot/telegram_bot.py 도움말 — 진행 중/예정 12 섹션의 중국 라인이
   "Foundation 가동" 표기로 업데이트.

Phase 4-CN-B AKShare client shipped 2026-05-18:
 • bot/akshare_client.py — lazy-import wrapper (~600 lines, AKShare
   ~200MB dep loaded only on first CN_A/HK analysis). 7 endpoint
   classes wrapped:
    - 공告: stock_zh_a_disclosure_announcement_cninfo (A주 巨潮资讯)
      + stock_zh_h_disclosure_em (HK 东方财富), 최근 30일 / 상위 8건
    - 主要 流通股东: stock_circulate_stock_holder (A주 top-10 분기갱신)
    - ST/*ST: stock_zh_a_st_em — 거래소 특별처리 분류 (HARD GUARD)
    - 停牌: stock_zh_a_stop_em — 거래정지 상태 (HARD GUARD, 차트 freeze)
    - 港股通 flow: stock_hsgt_north_net_flow_em + south_net_flow_em
      5거래일 净 매수 합계 (KR pykrx flow 등가물)
    - CN 매크로: macro_china_lpr + macro_china_cpi_monthly +
      macro_china_pmi/pmi_yearly (LPR 1Y/5Y + CPI YoY + 제조 PMI)
    - 东方财富 news: stock_news_em — 个股 中文 뉴스 (Naver/Kabutan/
      cnyes 등가물). HK 종목은 best-effort (Eastmoney HK 커버리지 lag).
   모든 endpoint 12h disk cache + AKShare ImportError graceful
   degradation. CN_A 5자리 SH/SZ/BJ prefix + HK 5자리 padded 변환.
 • agent_utils.py build_instrument_context: CN_A/HK branch 추가 —
   ST HARD GUARD banner + 停牌 HARD GUARD banner + AKShare 公告/홀더/
   윈도 block + 港股通 flow block + 东方财富 뉴스 block + CN 매크로
   block. _section_allowed gate 확장: eastmoney_news (시장+펀더멘털
   제외), hsgt_flow (시장만 포함), akshare_macro (감정 제외).
 • Rule A DATA OFFLINE: AKShare 미설치 시 anti-hallucination guard 가
   '公告 dates / 主要 流通股东 / 港股通 flow / LPR 절대 fabrication 금지'
   directive 주입.
 • has_recent_news: CN_A/HK fallback 가 AKShare Eastmoney 로 라우팅 —
   yfinance .news 비어있어도 中文 뉴스 fallback 작동.

Phase 4-CN-C RULE 13 + STAR/ChiNext + Dual-listing + VIE shipped 2026-05-18:
 • fundamentals_analyst.py — RULE 13 신설 (RULE 10 KR / RULE 11 JP /
   RULE 12 US / RULE 14 TW 와 동일 shape, 13 산업 단일 매크로 / 정책
   변수 명시 의무): 白酒 茅台 1499元 / 4대 国有 은행 LPR + 三道红线
   / 부동산 三道红线 + 首套房 LPR / Internet VIE 反독占 + 판호 + 美
   entity list + VIE 구조 자체 risk / 半導體 国産대체 美 BIS / EV
   정부 보조금 + 미·EU 관세 / 锂电池 锂가격 + IRA / 光伏 反倾销 +
   CBAM / 보험 国债 10Y + 港股通 southbound / 통신 5G ARPU + 美 SDN /
   항공 油价 + 春운 / 석유철강 双碳 限産 / 소비가전 美 관세 + 以旧
   换新. STAR/ChiNext 일일 한도 ±20% RULE 보강. ST/*ST + Dual-listing
   + Internet VIE 위험 + 港股통 flow dominant 변수 보강.
 • DOMINANT VARIABLE ENFORCEMENT 헤더: 'RULE 10/11/12/14' →
   'RULE 10/11/12/13/14' 로 확장.
 • build_instrument_context: detect_cn_sub_market() 호출 시 sub-board
   ±limit 인지 banner 자동 주입 — CN_A_STAR / CN_A_CHINEXT / CN_A_BJSE
   / HK_GEM 별 일일 한도 명시. 메인보드는 banner 없음 (기본값).

Phase 4-CN remaining (after 4-CN-C):
 • 4-CN-D: validation cycle (5-8 종목, 2-3 review/fix iterations 예상).
   추천 검증 종목: 600519.SS 茅台 (白酒 + A주 메인), 1398.HK ICBC
   (4대 은행 + HK), 002594.SZ BYD (EV + A주 dual-listing default),
   688981.SS SMIC (STAR + 半導體 国産대체), 0700.HK Tencent (Internet
   VIE + HK), 300750.SZ CATL (ChiNext + 锂电池), 600048.SS 保利 (부동산),
   0857.HK CNPC (석유철강).
 • 배포 호스트 AKShare 설치 (`pip install akshare`, ~200MB). 미설치
   상태에서도 봇 정상 작동 — Rule A guard fabrication 차단, CN 종목
   분석은 yfinance + RULE 13 텍스트 + sub-board banner 만으로도 진행
   가능. 설치 시 lazy import 활성화 + 풀 데이터 주입.

Rule applies to all analyses going forward — Foundation is universal-
by-default (every analyst sees CN_A/HK as separate first-class markets,
not as a "CN fallback"). Each Foundation file change covers US + KR +
JP + TW + CN_A + HK consistently.

**Phase 4-CN — original deferred design (preserved for follow-up commits)**
- After TW validation lands, start CN expansion. User-confirmed scope
  2026-05-18: HK + A주 대형, AKShare-based data clients, Tushare
  deferred. Expected quality ~70-75% (lower than TW due to GFW +
  weaker free API ecosystem + policy-shock event volatility).
- Same shape: market-specific benchmark mapping + data source adapters
- CSI 300 + Hang Seng sector mappings, AKShare for filings + news +
  consensus (no API key needed), FRED for LPR + CPI (same key as JP/TW).
- RULE 13 (CN INDUSTRY-SPECIFIC POLICY): 백주 / 4대 은행 / 부동산 /
  Internet VIE / 半導體 國産代替 / EV 補助金 / 锂电池 / 光伏 / 보험 /
  통신 / 항공 / 红筹 vs H-share vs ADR vs VIE structure 인지.
- Need user-supplied: none planned (AKShare key-less; FRED already
  registered). Tushare deferred unless AKShare reliability issues
  surface during validation.

## Universal guard symmetry (US ↔ KR ↔ JP ↔ TW)

All structural guards added during KR/JP expansion now also cover US,
preventing US-side asymmetric weakness. Reflect this in any future
review:

- **MANDATORY COMPS PEER SET** — `_US_INDUSTRY_PEERS` (bot/market.py)
  with ~70 yfinance-industry rows covers S&P 500 mega/large + active
  mid-caps. `resolve_peer_set` dispatches by market: KR→`_KR_*`,
  JP→`_JP_*`, default→`_US_*`. Peer multiples pre-fetch (Rule C in
  agent_utils._fetch_peer_multiples) runs for any market once a peer
  set is returned.
- **CORPORATE ACTION HARD GUARD — 3-source** in build_instrument_context:
  (1) DART scan for 무상증자/주식분할/액면분할/주식병합/감자 (KR),
  (2) EDINET scan for 株式分割/株式無償割当/株式併合 (JP),
  (3) universal yfinance `.splits` ex-date scan (`_detect_yf_corp_action`,
  any market, 14-day lookback). All three emit the same "ban
  SMA/EMA/MACD/RSI/Bollinger comparisons" HARD GUARD body. US gets
  layer (3) only; KR/JP get (1)+(3) or (2)+(3). DART/EDINET catch
  the ANNOUNCEMENT (before ex-date, more useful), yfinance catches
  the EX-DATE (universal fallback).
- **소유구조 빈 결과 환각 차단** — KR branch (DART insider holdings empty
  → "임원지분 데이터 미수집" prose, no fabricated 공기업/정부 narrative),
  JP branch (EDINET 大量保有 + yfinance heldPercentInsiders both empty
  → "JP 소유구조 데이터 미수집" prose). US implicitly covered: yfinance
  heldPercentInsiders + heldPercentInstitutions are populated for almost
  all US large/mid-caps, so the no-data case is rare; no separate US
  directive needed.
- **RULE 12 (US INDUSTRY POLICY)** — fundamentals_analyst.py mirrors
  RULE 10 (KR) and RULE 11 (JP). 11 US industries with a single
  dominant policy / macro variable: Banks/FOMC, Oil/OPEC+, Biotech/FDA,
  Semis/CHIPS+對中 수출규제, Healthcare Plans/Medicare, Autos/IRA EV
  credit, Aerospace+Defense/DoD budget, REIT/Fed rate, Utilities/PUC,
  Telecom/5G capex+FCC, Tobacco-Alcohol/FDA-소비세, Travel-Lodging-
  Airlines/jet fuel+TSA.
- **US NAMING DIRECTIVE (soft)** — when yfinance longName differs from
  the ticker symbol meaningfully (SNDK/SanDisk, AVGO/Broadcom,
  GOOGL/Alphabet, BRK-B/Berkshire), the directive recommends
  '{Company} ({TICKER})' form on first mention. Soft (not MANDATORY)
  because most US tickers (AAPL, NVDA, TSLA) are recognizable bare.
- **Dashboard JP name display** — `bot/dashboard._ticker_display_name`
  (renamed from `_ticker_kr_name`, alias preserved) resolves KR via
  DART, JP via yfinance longName, US returns None. JP analyses in the
  card list now show "Toyota Motor Corporation / 7203.T" instead of
  bare "7203.T". Search filter's `data-name` attribute covers both KR
  and JP names automatically.

The rule of thumb: when adding any structural guard going forward,
default to **universal** (no market gate) unless the guard depends on
a market-specific data source. Even then, prefer a universal helper
with market-aware branches over per-market parallel functions.

## KR quality enhancement roadmap (Step 2, 2026-05-19)

Step 1 (infra) shipped: F1-MVP Gemini caching + F2 Option 4 verify +
F3-light parallel prefetch. KR quality 강화 작업은 다음 순서로 진행
— API 키 필요한 항목은 final batch (위 TODO API-blocked section)
로 분리:

### Step 2A — no API required (proceed first, ~3.5일):
 1. **B1 5-day horizon enforcement** (0.25일, prompt only)
    펀더멘털 결론이 12개월 thesis 톤으로 흐르는 경향 차단. 시스템
    프롬프트 + 펀더멘털 RULE 에 5거래일 horizon 강제 directive 강화.
    surfaced by: 마오타이 / SMIC 펀더멘털 결론 "장기 보유 의견" 패턴.
 2. **A2 KRX 시장경보 + 상한가/하한가 + 거래정지 detect** (0.5일)
    `bot/krx_alert_client.py` 신규 — data.krx.co.kr HTTP scrape.
    무료, no key. 단기과열/투자경고/투자주의/거래정지 종목 list.
 3. **B4 시장경보 HARD GUARD inject** (0.25일, A2 의존)
    instrument_context 에 banner — 단기과열 종목 분석 시 "5일 정상
    가격 분석 보류" 자동 directive.
 4. **D1 yfinance KR 데이터 quality fallback 강화** (1일)
    MUTUALFUND misclass / split staleness / 매출 단위 mismatch (Rule
    G) / financialCurrency=USD 글로벌 자회사 케이스에서 DART /
    FnGuide 데이터로 자동 cross-check + override.
 5. **D2 USD/KRW 영향 자동 계산** (0.5일)
    수출 의존도 큰 종목 (현대차 / 기아 / LG에너지솔루션 / SK하이닉
    스 등) 의 USD/KRW 1% 변동 시 영업이익 ±X% 영향 자동 계산 +
    매크로 분석 inject.
 6. **A3 한경 컨센서스 scrape** (0.5일)
    `bot/hk_consensus_client.py` — consensus.hankyung.com 스크랩.
    FnGuide 가 mid-cap KOSDAQ 누락하는 영역 보강.
 7. **B2 KR ETF analyzer 특화** (0.5일)
    KODEX / TIGER ETF 종목을 generic fund 모드가 아닌 KR-ETF
    specific 모드로 처리 — 기초자산 sector 익스포저 / AUM 추이 /
    추적오차 / 분배금 / 환헤지 여부.

### Step 2B — API-required (final batch after Step 2A):
 8. **A1 KIS Open API integration** (1.5일, KIS 키 발급 후)
 9. **B3 외인 한도 RULE 10 변수 추가** (0.25일, A1 의존)

Trigger to start Step 2B: KIS_APP_KEY + KIS_APP_SECRET 가 .env 에
로드된 후. 발급되기 전 까지 Step 2A 모두 완료 가능 — 병렬 가능
(user 가 KIS 등록 진행하는 동안 bot 은 Step 2A 코드 작업).

Rule applies to all analyses going forward — KR-specific 작업이지만
infra (D1 fallback / D2 환율 계산) 는 다른 시장 (JP/TW/CN 수출주)
에도 동일 패턴 적용 검토 가능. Universal-by-default 가 KR 우선
shipping 후 cross-market parity audit 으로 확장.


## TODO

## 🔐 API-blocked tasks (deferred to final batch per user 2026-05-19)

User policy: tasks that require new external API keys / registration
are parked here and addressed AT THE END of all other infra work.
Reason: API registration often blocks (geofence / account approval
/ payment) and shouldn't gate the rest of the development. Each task
keeps a clear pickup state so the final batch is easy to resume.

These need new credentials BEFORE work can ship:

- **KIS Open API (한국투자증권)** — KR 외인 지분 한도 / 신용잔고 /
  대차잔고 / 프로그램 매매 / 시장경보 종목 분류. KR 시장의 가장 큰
  비어있는 단기 수급 영역 — 5거래일 horizon 가격 동인의 핵심.
  Required: 한국투자증권 계좌 + KIS Developers portal 가입 →
  `KIS_APP_KEY` + `KIS_APP_SECRET` 발급 → `.env` 에 추가.
  Estimated work after keys arrive: 1.5일 (`bot/kis_client.py` +
  agent_utils 주입 + RULE 10 dominant 변수 보강).
  Blocks: B3 (외인 한도 RULE 10 변수). Recommend kicking off
  registration in parallel with non-API work.

- **EDINET API key — pending user registration** (Phase 3 validation
  blocker). EDINET registration portal (disclosure2.edinet-fsa.go.jp)
  is behind Akamai geofencing — blocks all non-Japan IPs with "The
  request is blocked" + tracking ID. User attempted ProtonVPN Free
  (Japan moved to paid tier), Windscribe Free (same — Japan paid),
  TunnelBear Free (same — Japan paid). All mainstream free VPNs have
  recently moved Japan/Singapore to paid-only tiers. Working options:
  (a) ProtonVPN VPN Plus 1 month ₩9,990, cancel after registration;
  (b) Oracle Cloud Always Free Tokyo VM + SSH SOCKS5 tunnel (~45 min
  setup, free forever); (c) Japan-based contact who can register on
  user's behalf. Until key is loaded, JP analysis runs at ~80% capacity
  — Kabutan consensus + Kabutan news + FRED macro + yfinance .splits
  corp-action layer 3 + RULE 11 + JP COMPS PEER SET + currency
  directive all work. Missing: EDINET 공시 list, 5%+ 대량보유 변동,
  사전 announcement scan, next-earnings-window inference. Rule A
  (DATA SOURCE OFFLINE HARD GUARD, this commit) prevents the LLM from
  fabricating EDINET output when the key is absent — Toyota 7203.T
  2026-05-18 fabricated `BlackRock 5.1% / Vanguard 5.0%` 대량보유
  + specific 공시 dates without the key. Surface to user once they
  resolve the registration path.

- **FRED API key — loaded** (2026-05-18). User confirmed key is in
  `.env` (verified via redacted `cat ~/stock/.env | sed 's/=.*$/=***REDACTED***/'`
  output during MediaTek review session). JP macro block (BoJ 정책금리
  + JGB 10Y + JP CPI), TW macro (CBC 重貼現率 + TW 10Y + TW CPI), and
  the FRED slot of CN macro pathways all active. Single key drives all
  three markets. Resolved; preserved here as a status marker only — no
  further action needed.

- **Phase 4-CN-D validation cycle — pending** (2026-05-18). Phase 4-CN-
  A Foundation + 4-CN-B AKShare client + 4-CN-C RULE 13 all shipped in
  this session. Remaining: actual analysis runs on 5-8 representative
  CN_A + HK tickers + 2-3 review/fix iterations to harden RULE 13 +
  STAR/ChiNext + Dual-listing + Internet VIE rules. Recommended
  validation set:
   • /600519.SS 茅台 (백주 + A주 메인, RULE 13.1)
   • /1398.HK ICBC (4대 은행 + HK default, RULE 13.2)
   • /002594.SZ BYD (EV + A주 dual-listing default, RULE 13.6)
   • /688981.SS SMIC (STAR ±20% + 半導體 国産대체, RULE 13.5)
   • /0700.HK Tencent (Internet VIE + HK, RULE 13.4)
   • /300750.SZ CATL (ChiNext ±20% + 锂电池, RULE 13.7)
   • /600048.SS 保利 (부동산 + 三道红线, RULE 13.3)
   • /0857.HK CNPC (석유철강 + 双碳, RULE 13.12)
  Each run verified against 7-axis framework (per-ticker analysis
  verification framework section above). Bot host needs `pip install
  akshare` on bot/.venv before validation; without it the bot still
  routes CN tickers correctly but AKShare 公告 / 港股통 flow / LPR
  blocks return empty (Rule A guard prevents fabrication).

- **Phase 4-CN (China + HK expansion) — original deferred design,
  preserved**. User chose 2026-05-18 (Option γ) to ship CN AFTER the
  TW validation (Phase 4-TW-D) closes — sequential rollout avoids
  carrying two large in-flight rewrites at once. Re-reviewed CN
  scope at TW-level depth on 2026-05-18 (Option A) and captured
  preserved design notes here so the implementation starts from
  full context, not from the shallower v1 review.

  Design notes for the actual implementation:

  Sub-market structure (must split, not just '.SS/.SZ/.HK'):
   • 上海 메인보드 600/601/603/605.SS → ±10% 涨跌停
   • 上海 STAR 科創板 688.SS → **±20%** (등록제, 신상장 첫 5거래일 ±30%)
   • 深圳 메인보드 000/001.SZ → ±10%
   • 深圳 ChiNext 創業板 300/301.SZ → **±20%** (신상장 첫 5거래일 ±30%)
   • 北京 北交소 .BJ → ±30%, yfinance 커버리지 미약, 분석 보류
   • HK Main Board 0001-3999/6000-8999.HK → 无 涨跌停
   • HK GEM 8XXX.HK → 유동성 낮음, 분석 보류

  Required AKShare endpoints (~13):
   • Disclosure: stock_zh_a_disclosure_announcement_cninfo,
     stock_zh_a_disclosure_relation_cninfo, stock_zh_h_disclosure_em
   • News: stock_news_em, stock_news_main_cx
   • Ticker → name: stock_info_a_code_name, stock_hk_ggt_components_em
   • **港股通 flow (가장 critical, 이전 v1 리뷰 누락)**:
     stock_hsgt_north_net_flow_em, stock_hsgt_south_net_flow_em,
     stock_hsgt_individual_em (KR pykrx flow의 CN 등가물)
   • 매크로: macro_china_lpr, macro_china_mlf, macro_china_rrr,
     macro_china_cpi, macro_china_pmi
   • 상태: stock_zh_a_st_em (ST/*ST 분류), stock_zh_a_stop_em (停牌)
   • 펀더멘털: stock_a_indicator_lg, stock_circulate_stock_holder,
     stock_em_jgcg

  AKShare 우려:
   • ~50 패키지 의존성 (bs4 + tqdm + scipy + openpyxl + pyecharts
     등). bot/.venv에 추가 install ~200MB
   • IP-based rate limit (东方财富 / 同花顺 / 新浪) — 단일 분석에서
     endpoint 5-10 호출 시 403 가능. 12h cache로 보강 필요
   • 한국 IP에서 일부 endpoint 차단 (특히 stock_hsgt_* GFW 인접)
   • upstream 사이트 HTML 변경 시 1-2주 lag 후 패치

  통화 분리:
   • MARKET_CONFIG['CN_A']: CNY ¥, broad 510300.SS (CSI 300)
   • MARKET_CONFIG['HK']: HKD HK$, broad 2800.HK (Tracker Fund HK)
   • detect_market: .SS/.SZ → 'CN_A', .HK → 'HK'. 기존 'CN' 사용처
     검색 + 호환성 유지 필요
   • HK 본토 자회사 (Tencent, Alibaba 등): **거래 HKD, 재무 CNY**.
     yfinance financialCurrency mismatch HK > JP 빈도. Canonical
     시총 directive HKD 강제 + 재무 RMB 별도 인용 명시

  Sector ETFs:
   • HK broad: 2800.HK (Tracker Fund HK / HSI)
   • HK 중국기업: 3033.HK (HSCEI), 인터넷 KWEB (US)
   • A주 broad: 510300.SS (沪深300), 중형 510500.SS
   • A주 STAR: 588000.SS, ChiNext: 159915.SZ
   • A주 산업: 512760.SS 반도체, 512170.SS 의료, 512690.SS 백주,
     512800.SS 银行, 159805.SZ 자동차

  Regulatory vocabulary (RULE 13 작성용):
   反垄断 (SAMR) · 数据安全 (CAC) · 网络安전审查 · 双减 (Education) ·
   游戏판호 (NPC) · 三道红线 (Property) · 城投 부채 · 国家集成电路产业
   投资基金 (Big Fund) · 美 entity list / SDN · 美 IRA EV credit ·
   一带一路 · 双碳 (2030/2060) · 房贷 LPR · 国资 央企 통합

  RULE 13 (13 산업, RULE 14 TW 수준 깊이):
   1. 白酒: 600519 茅台, 000858 五粮液, 000568 泸州老窖 etc. →
      节日 수요 + 反腐 cycle + 茅台 1499元 정책
   2. 4大 国有 银行 + HSBC: 1398/0939/3988/1288.HK + 0005.HK → PBoC
      LPR + 三道红线 부실채권 + 城投 부채 + 资本충족율
   3. Property: 1109/0688/2007/2202/1813.HK + 600048.SS → 三道红线
      비율 + 一手房 매출 + 토지 经매 价格 + 首套房 LPR
   4. Internet VIE: 0700/9988/9618/3690/1024/9626/9888/9999.HK →
      반독占 罰款 + 数据 审查 + 게임 판호 + 美 entity list + VIE
      구조 자체 위험 (Cayman 법인 ↔ 본토 VIE 단절 risk)
   5. Semis: 688981.SS SMIC, 603501.SS 韦尔, 300782.SZ 卓胜微,
      688012.SS 中微, 0981.HK SMIC → 美 export ban + Big Fund +
      SMIC capacity (28nm/14nm/7nm) + 글로벌 AI 사이클
   6. EV: 002594.SZ + 1211.HK BYD, 9866 蔚来, 9868 小鹏, 2015
      理想, 3692 华夏 → 政府 补贴 + 出口 관세 (美 100%/EU 38%) +
      价格战 + 锂가
   7. Battery: 300750.SZ CATL, 300014 亿纬, 002460 赣锋 → EV
      demand + 锂矿가 + 美 IRA 영향 + 새로운 EU 보조금
   8. Solar: 601012 隆基, 600438 通威, 300274 阳光, 002129 TCL中环
      → 美/EU 反倾销 + 多晶硅가 + 분산형 보조금
   9. Insurance: 2318 平安, 1299 友邦 AIA, 1336 新华, 1339 人保 →
      国债 10Y + A주 시장 시세 + 重疾险 수요 + 港股通 southbound
  10. Telecom: 0941 中移동, 0728 中电信, 0762 中联通 → 5G capex 회수
      + ARPU + 国家 数字경제 + 美 SDN
  11. Airlines: 0753 国航, 0670 东方航, 1055 南方航, 0293 国泰 (HK)
      → 油가 + 国际线 회복 + 春运
  12. Petro + Steel: 0857 CNPC, 0386 Sinopec, 0883 CNOOC, 0347/0323
      鞍钢 → WTI/Brent + 国家 战略石油储备 + 房产/인프라 钢수요 +
      双碳 限産
  13. Consumer + Appliance: 600887 伊利, 000333 美的, 000651 格力,
      002241 歌尔 → 소비자 신뢰지수 + 价格战 + 美 가전 관세

  ST/*ST 처리 (이전 누락):
   • ST: 2년 연속 적자, 涨跌停 ±5% (RULE 6 자본잠식 연관)
   • *ST: 3년 연속 적자, 퇴출 위험
   • ST摘帽 (해제): 거래일 갭 +20% 흔함
   • 停牌: yfinance 미반영, AKShare stock_zh_a_stop_em 필요

  Dual-listing default 정책 (이전 미정):
   • BYD: 002594.SZ default, 1211.HK 명시
   • SMIC: 688981.SS default, 0981.HK 명시
   • ICBC: 1398.HK default (HK 더 liquid)
   • Sinopec: 0386.HK default
   • Tencent: 0700.HK default (본토 미상장)
   • Alibaba: 9988.HK default (본토 미상장 — VIE)

  영문 alias 60+ (이전 v1 8-10 → 60+ 확장):
   • Internet/Tech: TENCENT 0700, ALIBABA/BABA 9988, JD 9618,
     MEITUAN 3690, NIO 9866, XPENG/XPEV 9868, LIAUTO 2015,
     KUAISHOU 1024, BILIBILI 9626, NETEASE 9999, BAIDU 9888
   • 银行: ICBC 1398, CCB 0939, BOC 3988, ABC 1288, BOCOM 3328,
     HSBC 0005, STAN 2888, PINGAN 2318, AIA 1299
   • 통신/유틸: CHINAMOBILE 0941, CHINATELECOM 0728, POWERASSETS
     0006, CLP 0002
   • 항공/석유: AIRCHINA 0753, CATHAY 0293, SINOPEC 0386, CNPC
     0857, CNOOC 0883
   • A주 백주: MOUTAI 600519, WULIANGYE 000858, LUZHOU 000568,
     FENJIU 600809
   • EV/Battery: BYD 002594, CATL 300750, EVE 300014, GANFENG 002460
   • Tech: SMIC 688981, WILL-SEMI 603501, LONGI 601012, TONGWEI
     600438, SUNGROW 300274
   • Property: POLY 600048, VANKE 000002, COUNTRY-GARDEN 2007,
     CHINA-OVERSEAS 0688, CR-LAND 1109, EVERGRANDE 3333
   • 가전: MIDEA 000333, GREE 000651, YILI 600887

  6 User decisions (재검토 추천 — Option α):
   1. AKShare 설치 정책 → (a) 전체 설치 (~200MB, lazy import)
   2. 시장 분기 → (a) CN_A + HK 분리
   3. Dual-listing default → (a) A주 default + HK 명시 (case별 위)
   4. STAR/ChiNext ±20% 처리 → (a) RULE 13 텍스트 명시
   5. 港股통 flow priority → (a) High (KR pykrx 패턴 재사용)
   6. RULE 13 산업 범위 → (a) 13개 모두

  예상 작업량 (재산정):
   • 4-CN-A Foundation: ~1,000줄, 6-8h (TW 461 + 시장 분기 + dual)
   • 4-CN-B AKShare client: ~700줄, 5-6h (13 endpoints)
   • 4-CN-B HKEXnews + 港股통 flow: ~400줄, 3-4h (TW 추가)
   • 4-CN-C RULE 13 + ST 가드: ~300줄, 2h
   • 합계 ~2,400줄, 16-20h (TW의 1.8x)
   • 검증 사이클: 5-8 종목, TW와 유사 (2-3 review/fix cycle 예상)

- **Gemini Context Caching** (deferred, 2026-05-18). Cost-reduction
  Option 2 from the 2026-05-18 analysis. The instrument_context block
  (~5-10K tokens per analyst) is identical across the 4 analyst runs
  for a single ticker; Gemini's explicit-cache API
  (`cache_content` / `cached_content_id`) lets repeated reads of the
  same prefix bill at ~25% of the input rate. Implementation requires
  a Python-side cache lifecycle (create on first analyst, reference on
  subsequent 3, delete after analysis completes — caches live max 60
  min) and instrumentation to confirm the cached_content_id propagates
  through `langchain_google_genai` to the underlying `genai` SDK.
  Expected saving: -15% total input cost on top of Option 1 + 3 + 4.
  User chose 2026-05-18 to ship Options B (1+3+4) first and validate
  before paying the integration cost for caching. Pick this up when
  Phase 4-CN validation completes — caching ROI scales with the number
  of markets / analysts, so it gets more valuable after CN.

- **PM Option 4 propagation verification** (post Option B commit, 2026-
  05-18). Option 4 routes Portfolio Manager to a thinking_budget=2048
  variant of Gemini 2.5 Pro when the four analysts are unanimous on
  direction. The `thinking_budget_override` kwarg flows through
  `GoogleClient.get_llm()` into the `ChatGoogleGenerativeAI`
  constructor. Verify in production logs that the lighter LLM is
  actually invoked on consensus runs (look for the 'pm-budget:
  ... using light LLM' INFO log emitted from portfolio_manager.py).
  If `langchain_google_genai` ever changes how `thinking_budget` is
  consumed (e.g. moves it under model_kwargs or generation_config),
  the override path needs adjustment to keep landing on the API
  call. Quality regression detection: compare PM verdict distribution
  on the next 10-20 unanimous-consensus runs vs the pre-Option-B
  baseline; if Hold-rate or override-discipline triggers shift more
  than ~5pp, suspect the lighter budget is under-thinking and bump
  pm_consensus_thinking_budget back up.

- **Hydrator-registry refactor for pre-fetch** (deferred until Phase 4).
  `build_instrument_context` currently has 15 data sources stitched
  together as sequential imperative try/except blocks: yfinance .info,
  yfinance averages, macro 9-series, risk metrics, sector strength,
  DART (KR), FnGuide consensus (KR), KRX pykrx flow (KR), KRX 30-day
  trends (KR), BoK ECOS macro (KR), Naver news (KR), EDINET (JP),
  Kabutan consensus (JP), Kabutan news (JP), FRED JP macro (JP).
  Re-evaluated 2026-05-18: bot works fine today, sequential is OK
  at 15 sources, parallel would save ~200-400ms out of 3-4 min total
  analysis time (imperceptible). Adding the abstraction now would
  violate CLAUDE.md's 'no premature abstraction' rule — three similar
  lines is better than a registry.
  Trigger to refactor: Phase 4 (CN expansion) will push the source
  count past 20 and add Tushare / akshare / CSI-300 / 신화재경 /
  국가통계국 macro. At that point the readability + parallelism
  benefits outweigh the abstraction cost, and the framework can be
  designed around Phase 4's actual requirements rather than guessed
  ones. Until then: keep the imperative chain.
