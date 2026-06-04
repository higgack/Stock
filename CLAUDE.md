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
6. **Regression suite** (사용자 정책 2026-06-01): `tests/test_regression.
   py` 가 실제 surfaced 버그 클래스(`_strip_meta_commentary` catastrophic
   regex / dashboard `<details>` open-close 균형 / `_SECTOR_ETFS` dedup·
   한자·시장 누락 / screener post-process idempotency) 영구 차단. 매
   commit 전 실행 의무:
   ```bash
   make test
   # 또는 venv 직접: .venv/bin/python -m pytest tests/ -v
   ```
   VM 실 환경 ~37초 (bot.dashboard import 시 langgraph 등 무거운 의존성
   로드). 0.08초가 아닌 이유 인지. 새 회귀 패턴이 surfaced 되면
   `tests/test_regression.py` 에 영구 테스트로 추가 (ad-hoc smoke test 한
   번 쓰고 버리지 말 것). 본 슈트가 fail 하면 commit 금지 — 사용자/Claude
   무관. Makefile 도 `make syntax` (4 핵심 파일 ast.parse) / `make
   help-len` (_HELP_TEXT UTF-16 cap 확인) shortcut 제공.

Skipping verification is treated the same as skipping the explicit-
commit-request rule — never do it.

## Help text registration of changes — mandatory

Whenever a change ships that is user-visible (new command, new data
source, new RULE, new analyst, new dashboard feature, removed
behavior, etc.), `_HELP_TEXT` MUST be updated in the SAME commit. The
help is pinned as a channel announcement; out-of-sync help is treated
as a public spec bug.

**Bottleneck Screener 변경도 동일 적용 (사용자 정책 2026-05-29):**
새로운 대시보드 surface · 새로운 가드 룰 · 새로운 outcome 지표 ·
새로운 trash/edit 기능 등 무엇이든 user-visible 인 screener 변경은
`_HELP_TEXT` section 1 (commands) + section 11 (대시보드) + section 12
(예정) 의 관련 줄을 동시에 갱신 의무. "이번에 생긴 변경도 help 에
넣어줘 — 앞으로 변경 있을 때마다 계속" — 사용자 강조 2026-05-29. 본
CLAUDE.md 의 'Bottleneck Screener — 운영 중' 섹션도 같은 commit 에서
함께 갱신해 다음 세션 Claude 가 현재 상태를 정확히 파악할 수 있게 할
것. The two surfaces it must keep current are spelled out under "Help
text maintenance" below (current-state sections 2-11 + '진행 중 / 예정'
section 12).

**⛔ Screener 도메인 목록은 `_HELP_TEXT` inline 금지 (사용자 정책
2026-05-29 후속):** Wave 1/2/3 + Wave ∞ 까지 도메인이 무한 확장될 예정
이므로 모든 도메인을 help text 에 나열하면 4096 UTF-16 cap 압박이
영구화 됨. 도메인 목록은 두 surface 에 자동 generation:

  1. **텔레그램 `/screener_list` 명령** — `bot.screener_themes.list_
     domains()` 결과를 형식화. `_format_screener_domains_list()` 단일
     helper, DM 핸들러 (`cmd_screener_list`) + 채널 핸들러 (channel
     post `screener_list`) 양쪽에서 호출.
  2. **대시보드 페이지** — `archive/screener_domains.html`,
     `regenerate_screener_index()` 가 `screener.html` 옆에 자동 생성.
     NOAH 메인 헤더 + screener.html nav 양쪽에서 링크.

새 도메인 추가 = `bot/screener_themes/<slug>.py` 모듈 1개 drop 만.
`_HELP_TEXT` 변경 불필요. 위 두 surface 가 registry 에서 즉시 auto-
update. `_HELP_TEXT` 섹션 1 은 "/screener [도메인] — 전체 도메인 →
/screener_list" 라인 1개만 유지, 도메인명 inline 금지.

이 규칙이 깨지면 (예: 다음 세션 Claude 가 새 도메인 추가하며 help text
에 `/screener xxx|yyy|zzz` 인라인 형태로 나열) cap 압박이 다시 시작되
므로 review 단계에서 반드시 차단할 것.

**If the new content cannot fit inside the 4096 UTF-16 cap after
reasonable prose compression, STOP and REPORT to the user.** Specifically:
- Try compressing existing sections first (bullets → inline phrases,
  prose → terse fragments).
- If still over the cap, surface the situation: "현재 help 길이 X UTF-16,
  추가 필요분 Y, 한도 4096. 압축 더 시도할지 / 어느 섹션을 줄일지 / 한도
  올리기 위해 다중 메시지로 분할할지 결정 요청." Do NOT silently drop a
  feature, do NOT silently split into multiple messages, do NOT commit
  with a too-long _HELP_TEXT. The default is to stop and ask.

## Dashboard surface registration of changes — mandatory (사용자 정책 2026-06-04)

user-visible 변경은 `_HELP_TEXT` **뿐 아니라 대시보드 표면도 같은
commit 에서** 갱신 의무. help 만 고치고 대시보드 설명을 방치하면 대시보드가
낡은/틀린 설명을 노출 → public spec 버그 (help 와 동급 취급). 사용자 강조
2026-06-04: "변경사항이 있다면 help 외 대시보드에도 업데이트가 필요" — 영구
규칙으로 박음.

대시보드 표면 = 사용자가 화면에서 읽는 모든 설명/라벨/범례:
- **차트**: legend (`_render_chart_section` 의 `chart-legend`) + `ℹ️ 차트
  보는 법` `<details>` 가이드 (`_CHART_JS` 근처) + 값 패널 항목명 + 축/
  series 라벨.
- **카드/페이지**: 카드 필드, outcome 컬럼 헤더(예: 1개월/3개월/6개월),
  페이지 헤더·nav 라벨, 범례.

규칙 (help-text 등록 규칙의 대시보드 확장):
1. 새 차트 지표/라인/값/토글 추가 → 차트 legend + `ℹ️ 차트 보는 법`
   가이드 **둘 다** 같은 commit 에서 갱신 (둘 중 하나만 고치면 불일치).
2. 새 대시보드 surface / 카드 필드 / outcome 컬럼 → 그 페이지의 헤더·
   설명·범례 동시 갱신.
3. **동작이 바뀌면 설명도 정확히** — 예: 라이브 현재가가 이상치 시 직전
   종가로 폴백하도록 바꾸면, 가이드의 '현재가' 설명도 그 폴백을 명시해
   사용자가 화면만 보고 오해하지 않게 (2026-06-04 라이브 가드 케이스).
4. commit body 에 "help + dashboard 동시 갱신" (또는 해당 surface 명시).

이 규칙은 차트뿐 아니라 모든 대시보드 표면에 universal 적용. 대시보드는
help 와 마찬가지로 public-facing spec 이므로 out-of-sync = 버그.

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
- Dashboard 서버 코드 자동 재배포 (2026-06-03) → `auto-update.sh` 가 매 deploy 시 `bot/dashboard_server.py` / `bot/dashboard.py` / `bot/archive.py` 변경을 diff 로 감지하면 `sudo -n systemctl restart stock-bot-dashboard` 자동 호출 (`restart_dashboard()` helper). 기본 auto-update 는 `stock-bot` 만 재시작하므로 대시보드 **서버-레이어** 변경(Cache-Control 헤더 / `/api` delete endpoint / regen import)은 그동안 수동 재시작이 필요했음 — 이제 자동. 권한은 `install.sh` 가 `/etc/sudoers.d/higgack-stock-restart` drop-in (`stock-bot` + `stock-bot-dashboard` restart NOPASSWD) 을 visudo -c 검증 후 idempotent 설치 → 사용자 sudoers 추가 단계 0 (기존 install.sh NOPASSWD 1줄이 self-extend). VM 직접 push (diff range 없음) 경로는 stateless 대시보드 재시작을 항상 동반. **주의**: Cache-Control no-cache 는 HTML/디렉토리 응답에만 적용 (정적 이미지·JSON 은 캐싱 유지). 브라우저에 이미 캐싱된 옛 HTML 은 최초 1회 강력 새로고침(Ctrl/Cmd+Shift+R) 필요, 이후 일반 새로고침으로 항상 최신.
- /ticker 상세 페이지 가격 차트 (2026-06-03, 사용자 요청 — 대시보드 주 소비) → `bot/chart_data.py` `build_price_chart(ticker)` 가 분석 종료 후 yfinance 1년 종가 + 10EMA/50SMA/200SMA 를 `_compute_technical_snapshot` **동일 계산식**으로 산출 (차트 MA = 본문 TECHNICAL SNAPSHOT SSoT 일치). `archive.py` SCHEMA_VERSION 2 로 `price_chart` 필드 저장 (parallel arrays: times/close/ema10/sma50/sma200, 통화·decimals 포함, ~11KB/분석, try/except 비치명적). `dashboard.py` `_render_detail` 이 `_render_chart_section` 으로 종가+3 MA 라인 차트 emit (lightweight-charts v4.2.0, 다크/라이트 테마 연동, JSON 은 `<script type=application/json>` 블록 — `</` defuse). 라이브러리는 `_ensure_chart_lib()` 가 regen 시 ARCHIVE_ROOT 에 1회 자동 다운로드 → **self-host (CDN 의존 제거, 오프라인 작동)**, 실패 시 텍스트만 graceful. **새 분석부터** 차트. **Phase 2 완료 (2026-06-03)**: (a) **entry/stop/target 수평선 마커** — `chart_data.parse_trade_levels(full_report, close_values)` 가 본문의 진입가/손절가/목표가(Entry Price·Stop Loss·Price Target + 한국어 변형) 파싱, **종가 series 대비 0.2x~5x 밴드 밖이면 drop** (비현실 오파싱이 잘못된 라인 그리는 risk 차단 — v1 제외 사유 해소). render-time 파싱(미저장)이라 파서 개선·백필 모두 즉시 반영. `_CHART_JS` 가 createPriceLine 으로 진입(보라)·손절(빨강)·목표(초록) dashed 라인. (b) **옛 기록 소급 백필** — `archive.backfill_price_charts()` 가 schema v1 기록에 price_chart 추가, **분석일 시점(as_of) 1년 윈도 fetch** (lookahead 없음·MA 가 그날 SNAPSHOT 일치). telegram_bot startup 에서 marker 파일(`.charts_backfilled`) gate 로 **1회만** 백그라운드 thread 실행 → 완료 후 regenerate_index. 비용 ₩0 (yfinance only). universal (전 시장). **Phase 3 — 일/주/월봉 + 기간 선택 (2026-06-04, on-demand)**: 상세 페이지 토글 버튼(일/주/월봉 · 6개월/1년/3년/5년/전체). 기본=저장된 1년 일봉(즉시·SSoT 일치·오프라인). 다른 조합 클릭 시 `dashboard_server.py` `/api/chart?ticker=&interval=&range=` GET (인증 gate, `_TICKER_RE` + interval/range 화이트리스트, `~/.tradingagents/chart_cache/` 1h 디스크 캐시) → `chart_data.fetch_chart_payload()` 가 yfinance fetch → `_CHART_JS` 가 chart.remove() 후 재렌더. 주/월봉은 MA 가 그 interval 기준 재계산(10wk/50wk/200wk — 일봉 기본만 본문 SSoT 일치, 의도된 동작). 마커는 가격 라인이라 모든 interval 재적용. 비용 ₩0 (yfinance only, 1h 캐시). **Phase 4 — 현재가/시점가 분리 (2026-06-04, 사용자 요청)**: 메인 라인을 분석일까지가 아닌 **오늘까지(현재가)** 로 변경 — `_CHART_JS` 의 `load()` 가 기본 뷰(1d/1y)도 `/api/chart` 로 to-today fetch (start 에서 저장된 payload 로 즉시 placeholder 렌더 후 교체). 우측 축 라벨 메인 series title `현재가`. **시점가**(분석일 종가) = stored payload 의 `as_of_close`(=close[-1], `_render_chart_section` 이 주입)를 회색 점선 priceLine(`시점가`)으로 항상 표시 — "그때 가격 ↔ 지금 가격" 복기 view. ⚠️ 기본 뷰가 to-today 라 차트 MA 는 더 이상 본문 TECHNICAL SNAPSHOT(분석일 기준)과 일치하지 않음 (시점가 라인이 분석 시점 표지). offline/서버 다운 시 placeholder(분석일까지) 유지 + status 에 실패 표시. **Phase 5 — 라벨 정리 + 보조지표 (2026-06-04, 사용자 요청)**: (a) 라벨 겹침 해소 — 이동평균(10EMA/50SMA/200SMA)+RSI+거래량 값은 차트 **오른쪽 바깥 패널**(`#chart-values`), 현재가/시점가/진입/손절/목표(분석 가격)는 차트 안 축 라벨(`axisLabelVisible`) 유지 (실제 가격 대비 분석가 위치 확인). 차트 높이 440. (b) **보조지표** — `_series_payload` 가 `rsi`(Wilder 14, 본문 SNAPSHOT SSoT 동일식)+`volume` 배열 추가. `_CHART_JS` 가 거래량을 가격 pane 하단 18% 히스토그램(상승 초록/하락 빨강, `priceScaleId:'vol'` overlay), RSI 를 **하단 별도 pane**(`#rsi-chart`, 120px, 70/30 기준선)로 emit, `syncTime()` 가 두 차트 시간축 동기화 (줌/팬 연동, `minimumWidth:72` 로 정렬). 주/월봉 전환 시 RSI/거래량도 그 interval 기준 재계산. 비용 ₩0. **차트 라벨/로고 (2026-06-04)**: 현재가/시점가만 차트 안 축 라벨로도 표시(우측 패널과 중복), 진입/손절/목표는 선만(겹침 방지). TradingView attribution 로고는 `layout.attributionLogo:false`(v4.2.0) 로 제거(모든 pane). 지표 기본 ON=이평선·거래량·RSI(캡처 기준), localStorage 영속으로 사용자 조정 유지. **Phase 6 — 볼린저+MACD+캔들+토글 (2026-06-04, 사용자 "3개 다+캔들")**: `_series_payload` 가 볼린저(20,2σ: bb_u/bb_m/bb_l)+MACD(12,26,9: macd/macd_signal/macd_hist)+OHLC(open/high/low) 추가 — 볼린저·MACD 는 본문 SNAPSHOT SSoT 동일식. `_CHART_JS` 에 지표 on/off 토글 6종(캔들/이평선/볼린저/거래량/RSI/MACD), `localStorage('noah_chart_ind_v1')` 영속(페이지 넘나들어도 유지), 토글 시 `lastData` 로 refetch 없이 재렌더. 캔들=`addCandlestickSeries`(OHLC 있을 때만, 없으면 라인). 볼린저=가격 overlay 점선 3선. MACD=하단 별도 pane(라인+시그널+히스토그램). `linkTimeScales([chart,rsi,macd])` 가 최대 3 pane 시간축 동기화. `subChart()`/`showSub()` 헬퍼. 기본 표시: 이평선·거래량·RSI ON, 캔들·볼린저·MACD OFF(클러터 방지, 사용자가 켜면 저장). v4 는 네이티브 pane 미지원이라 오실레이터=동기화 sub-chart, 실용 한계 2~3 pane. **Phase 7 — 장중 현재가 (~15분 지연, 2026-06-04)**: `chart_data.fetch_chart_payload` 가 yfinance `fast_info.last_price`(fallback lastPrice/regularMarketPrice, ~50ms, 무료·무키)를 payload `last_price` 필드로 추가(1d interval 만). 프론트가 series 마지막 봉을 라이브로 대체 — 라인은 마지막 점 value 교체, 캔들은 close + high/low 보정. MA 는 별도 사전 계산이라 흔들리지 않음(시각적 1봉만 갱신). 우측 패널 '현재가' 도 last_price 우선. 캐시 키 v2→v3 + TTL 1h→5min(yfinance 호출 12x↑, 단일 채널 audience 면 무료한도 ~2000/h 안전). KR(.KS/.KQ)은 yfinance 가 종종 EOD only → 해 없음(last close 와 같음). 라벨에 '~15분 지연·KR EOD 가능' 정직 표기. build_price_chart(snapshot, archive) 는 last_price 안 넣음(시점가 보존). **Phase 8 — 과거 추천 마커 + 로그스케일 + 크로스헤어 툴팁 (2026-06-04, 사용자 요청)**: (a) **과거 추천 마커** — `regenerate_index` 가 종목별 모든 분석(archive)의 판정(_RATING_RE)+5거래일 결과(resolved_lookup.raw)를 `_ticker_analysis_markers` 로 모아(한 번만, O(n²) 회피) `_render_detail`→`_render_chart_section` 으로 전달 → payload `analysis_markers` → `_CHART_JS` 가 `mainS.setMarkers` 로 ▲매수(초록 belowBar)/▼매도(빨강 aboveBar)/●보유(회색 circle) + '+8.3%' 결과 텍스트. 데이터 범위(firstT~lastT) 밖 필터. **우리만의 차별점 — 차트에 우리 track record 시각화**. (b) **로그 스케일** — `로그` 토글(ind.log, localStorage) → main 차트 rightPriceScale.mode 1, 긴 기간 % 비교. sub-pane 은 선형 유지. (c) **크로스헤어 툴팁** — crosshair mode 1(Magnet) + `subscribeCrosshairMove` → 커서 지점 날짜+종가+이평선+RSI+거래량 floating div(`.chart-tooltip`, pointer-events:none, lastData index 로 조회). 비용 ₩0.
- Stale process recovery → `stock-bot-watchdog.service` restarts if main loop hangs 12 min. ⚠️ watchdog 는 180초 polling-hang(getUpdates 부재) + `.busy` marker(분석 중이면 12분까지 skip) 두 체크. **무거운 작업(Screener 5-10분, /ticker)은 반드시 `_busy_acquire()`/`_busy_release()` 로 감싸야** watchdog 가 실행 중 재시작해 작업을 살해하지 않음. 2026-06-01 Screener 가 busy marker 미사용으로 Hospitality & Leisure run 이 watchdog 재시작에 살해됨 → `_run_screener_and_send` 에 busy wrap 추가. 새 long-running 핸들러 추가 시 동일 패턴 필수.
- Memory pending-entry resolution → `_periodic_auto_resolve` asyncio task, 12 h cycle
- Daily dashboard regen → `_periodic_dashboard_refresh` asyncio task, 00:01 KST
- Journal log size → `SystemMaxUse=500M` in `journald.conf` (auto-trim)
- Standard View code updates → `sv-update.service` polls git every 1 min, rsyncs `standardview/scripts` + `standardview/backend` into live tree, restarts backend if changed, defers when daily_generator is running. Same pattern as `stock-bot-update`.
- Standard View cache rollover → `sv-cache-rollover.service` runs 00:05 KST daily, flushes `macro_news_cache` so the first news-brief call of the new date regenerates from scratch (fixes the 2026-05-21 midnight stub-cache bug).
- Standard View watchdog → `sv-watchdog.service` runs every 30 min; if `latest.html` is >90 min stale during 08:00-22:00 KST and the BUSY_MARKER is clear, re-kicks `daily_generator.py` in the background.
- Standard View systemd units 자동 배포 → `sv-update.sh` 가 `standardview/deploy/*.{service,timer,sh}` 변경 감지 시 `sudo /home/higgack/stock/standardview/deploy/install.sh` 자동 호출 → systemd unit 재설치 + daemon-reload + enable + Telegram 알림. 사용자 1회 setup (NOPASSWD line + 첫 install.sh) 이후 SSH 영원히 미진입 — stock repo push 만으로 timer/service 변경까지 1분 내 자동 적용.
- Stock-bot systemd units 자동 배포 (2026-05-29 신규 — SV 패턴 mirror)
  → `deploy/auto-update.sh` 가 매 1분 git pull 시 `deploy/*.{service,
  timer,sh}` 변경 감지하면 `sudo /home/higgack/stock/deploy/install.sh`
  자동 호출. install.sh 는 idempotent — stock-bot · dashboard · update
  timer · watchdog timer · screener-gics-check timer · trade-bot 전부
  re-install + daemon-reload + enable. 사용자 1회 setup:
  ```
  sudo visudo -f /etc/sudoers.d/higgack-stock-deploy
  # 다음 라인 추가:
  higgack ALL=(root) NOPASSWD: /home/higgack/stock/deploy/install.sh
  # 그리고 첫 실행:
  sudo /home/higgack/stock/deploy/install.sh
  ```
  이후 새 systemd unit 추가/변경 시도 SSH 진입 없이 push 만으로 1분
  내 적용. NOPASSWD 미설정 시 silent skip (legacy bot 호환).
- Standard View 스케줄 (2026-05-21):
  • `standardview-daily.timer` — 07:30 + 20:30 KST 매일, `daily_generator.py` 만 (refresh)
  • `standardview-push.timer` — 08:00 + 21:00 KST **평일만(Mon-Fri)**, `telegram_pusher.py` 만 (push). 주말 무음(사용자 2026-06-01 "주말에는 빼줘"). daily_generator 는 매일 유지 — HTML 대시보드는 주말에도 신선.
  • 30분 gap 으로 generator 가 완료된 latest.html 을 pusher 가 사용
  • legacy `standardview-hourly.timer` (Mon-Fri 12/16시 push) disabled
- Screener GICS 분기 점검 (2026-05-29 사용자 정책):
  • `screener-gics-check.timer` — 3·6·9·12월 5일 09:00 KST (4x/year, 사용자
    정책 2026-06-01 — 1일→5일 변경 사유: 한국이 미국 대비 시차 앞서므로
    1일 09:00 KST 면 미국 시장/공시 마감 데이터가 충분히 반영되지 않은
    시점. 5일이면 미국 4일 close 데이터까지 확보 → 분기 정리 안정.)
  • `bot/screener_gics_check.py` 가 Pro + web search 로 (a) S&P GICS /
    MSCI 공식 분류 변경 + (b) 신규 emerging industry trend (시총 $50B+
    pure-play 5+ 종목) 식별 → 기존 65 도메인과 비교 → 신규 후보만
    텔레그램 알림 → 사용자 직접 검증 후 모듈 add 결정
  • 비용 ~₩300/quarter (Pro web search 1 call) ≈ ₩1.2K/year
  • Raw 응답 audit log `~/.tradingagents/gics_check_audit.jsonl` 에
    저장 — 환각 의심 시 참조. Pro 의 false-positive tolerance 가 정책
    (놓치는 것보다 noise 가 나음). 사용자 직접 확인 정책 명시.
- 미국 레딧 게시물 분석 Watcher — t.me/insidertracking 채널의 '미국 레딧
  게시물 분석' 제목 메시지만 자동 forward+archive (사용자 요청 2026-06-03).
  `bot/reddit_insider_watch.py` — Telethon **userbot**(봇은 남의 채널 멤버
  불가 → 사용자 본인 계정 client) 5분 polling (`reddit-insider-watch.timer`)
  → 제목 매칭 + seen-set (msg_id) 중복 차단 + 첫 run 기존 메시지 seen 처리
  (폭주 방지, blog_watch mirror) → 우리 NOAH 채널 원본 그대로 forward + 
  `reddit_insider_archive/YYYY-MM-DD/HHMMSS_<msg_id>.json` 저장 →
  `reddit_insider.html` 대시보드 (Daily Byte 패턴: 월/일 collapse + 검색 +
  카드). **비용 ₩0** (LLM 가공 0 — 원본이 이미 한국어 + 구조화). 사전 준비:
  사용자 본인 계정으로 https://my.telegram.org 가입 → API_ID/API_HASH 발급,
  `.env` 에 `TG_USER_API_ID` + `TG_USER_API_HASH` + `TG_INSIDER_CHANNEL`
  (기본 'insidertracking') 추가, 첫 실행 시 본인 전화번호 + 인증코드 1회 입력
  (`~/.tradingagents/reddit_user.session` 저장 후 무인). nav '📨 미국 레딧'
  맨 끝, help §7 알림 + §9 대시보드 동기 추가.
- Watchlist 조건 알림 — `bot/watchlist.py` (vibe-trade heartbeat/trigger
  패턴 영감, 2026-06-04). `/watch TICKER rsi<30 price>950 >sma50 52whigh
  earnings foreignbuy …` → `watchlist-check.timer` 30분 간격 `check_all()`
  이 종목당 yfinance 1회(chart_data.fetch_chart_payload 재사용, **LLM 0·
  비용 ₩0**) fetch → 조건 평가 → **edge-trigger**(false→true 1회만, 스팸
  방지) → 등록한 chat_id 로 텔레그램 알림 + `/<TICKER>` 분석 권유. 조건:
  rsi</>N · price</>X · </>sma50/200 · 52whigh/low · earnings(D-5) · **KR
  수급 foreignbuy/foreignsell/instbuy/instsell**(pykrx 외인·기관 5일 순매수,
  .KS/.KQ 만, edge-trigger 가 "전환"을 잡음, creds 없으면 graceful skip).
  저장 `~/.tradingagents/watchlist.json`(atomic) + 알림 이력 `watchlist_
  alerts.jsonl`. **대시보드 `watchlist.html`**(활성 워치 테이블 + 알림 이력,
  읽기전용, nav 끝, regenerate_watchlist_index — check_all/startup/midnight/
  /watch·/unwatch 시 갱신). 명령 /watch · /watchlist · /unwatch(TICKER|id|
  all), set_my_commands 등록. help §1 예시 첨부. **실행 아님 — 알림만**(교육
  스탠스 유지). Fincept(C++/AGPL)·vibe-trade(TS/실거래)는 스코프 불일치라
  이 트리거 개념만 차용, 나머지(모델 티어링/불변 저널/라이브-데이터-only/
  페르소나)는 이미 보유 확인.
- 블로그 Watcher — 네이버 '변화하는 기업을 찾아서'(beatthemkt) 새 글 자동
  포워드+ingest (2026-05-31 사용자 요청). `bot/blog_watch.py` — RSS
  (rss.blog.naver.com/<id>.xml, 브라우저 UA+Referer) 30분 polling
  (`blog-watch.timer`) → 새 GUID 감지 → Gemini Flash 3줄 요약(grounding off,
  발췌 기반 환각 0) → 채널 push + `blog_archive/` JSON 아카이브. state 파일
  (`blog_watch_state.json`) 중복 차단 + 첫 run 은 기존 글 seen 처리만(폭주
  방지). **대시보드 surface 없음** (사용자 정책 2026-05-31 — "정확히 자동
  포워드되는 채널처럼 + ingest까지"): 채널 push + `blog_archive/` JSON ingest
  (봇 참조용 raw 보관)만, blog.html/nav/delete endpoint 미생성. 비용
  subsystem="blog" (글당 ~₩10 Flash). 키 불필요. ⚠️ VM 네이버 접근은 되나
  RSS 403 시 헤더/대체 endpoint 점검 (news client 는 작동 중).
- 부동산 Byte — 아파트 실거래가 주간 브리프 (2026-05-31 사용자 요청,
  ticker·5거래일 완전 독립). `bot/realestate_client.py` (MOLIT 아파트 매매
  실거래 data.go.kr, 대표 10개 법정동) + `bot/realestate_brief.py` (Pro
  narrate: 지역 가격대·거래량·금리 연계·건설/부동산/은행 섹터 함의 중립).
  `realestate-byte.timer` 금 09:00 KST (R-ONE 주간동향 목 발표 후). 채널
  push + `realestate.html` 대시보드 (오늘/누적 비용·검색·🗑️) + subsystem=
  "realestate". **DATA_GO_KR_API_KEY 무료 키 필요** (data.go.kr 가입 →
  '국토교통부 아파트 실거래가' 활용신청 → .env). `realestate_key_ready()`
  gate 로 키 없으면 graceful skip. **DATA_GO_KR_API_KEY LIVE (2026-05-31)** —
  아파트 매매 + 전월세 + 오피스텔 + 연립다세대 실거래 모두 작동
  (브리프 ₩17.2, 인포그래픽 yes 확인). 7개 data.go.kr API 활용신청 완료
  (매매/전월세/오피스텔/연립/건축인허가/청약/통계리스트).
  **R-ONE 추세 통합 LIVE (2026-05-31)** — `bot/rone_client.py` (한국부동산원
  reb.or.kr 자체 OpenAPI, 별도 키 `REB_RONE_API_KEY` 발급 완료). R-ONE 은
  **주간 동향 미개방**(보도자료 only) → **월간 지역별 아파트 지수**(매매
  A_2024_00178 / 전세 A_2024_00182) 의 MoM/3M 추세를 실거래(개별 노이즈)
  대비 매끄러운 방향성 시그널로 brief 에 주입. 엔드포인트 `SttsApiTblData.do`
  (KOSIS 식 StatisticSearch.do 아님 — ERROR-310), 기간 필수
  (START/END_WRTTIME), 필드 WRTTIME_IDTFR_ID/CLS_NM/DTA_VAL. `realestate_
  trend()` (전국·수도권·지방·서울, 표당 1 fetch) → `build_trend_block`.
  probe: `python -m bot.rone_client` / 통계표 재탐색 `--tables`.
  **건축인허가 공급 파이프라인 LIVE (2026-05-31)** — `bot/buildperm_client.py`
  (건축HUB `apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo`,
  동일 DATA_GO_KR_API_KEY, sigunguCd+bjdongCd 필수). 필드 archPmsDay(허가일)
  /totArea(연면적)/hhldCnt(세대)/mainPurpsCdNm(주용도). `permits_for_region`
  가 archPmsDay 로 최근 N개월 주거 인허가 필터·집계, `permits_aggregate`
  (건물 단위 raw). ⚠️ **공급 band 는 건축HUB 가 아니라 R-ONE 전국 집계
  통계로 통합** (사용자 결정 2026-05-31): per-법정동 건축HUB 기본개요는
  희소·상업동 편향(역삼/서초=오피스, 주거 인허가 0)·hhldCnt 0 으로
  공급 추세 지표 부적합 판명. `buildperm_client.py` 는 코드 보관(향후 동
  단위 상세용), brief 비연결. **R-ONE 공급 통계 LIVE (2026-05-31)** —
  discovery 로 4 표 확인: 주택건설인허가실적 T235263129553687 / 주택착공
  실적 T233033129823134 / 미분양주택현황 T237973129847263 (+ 신규분양세대
  T244633134443498 은 0행, 미사용). 핵심: **지역이 cls_fullnm 첫 세그먼트**
  ("전국>합계(가구수기준)"·"서울>계"), GRP_NM 은 빈값. `_region_of` 가
  split('>')[0] 로 전국 추출, `supply_summary()` 가 인허가(합계 가구수기준)
  ·착공(총계)·미분양(계) 전국 최신·MoM·YoY 산출 → `build_supply_block`
  로 "📐 공급 파이프라인" band (단위 호). fetch_index 페이지네이션(8p×
  1000) 로 큰 표 truncation 방지 — 안 하면 옛날 1000행만 와 최근 잘림.
  시간축 3-band 완성: R-ONE 가격추세(현재 방향)·MOLIT 실거래(현재 거래)
  ·R-ONE 인허가/착공/미분양(미래 공급). probe `python -m bot.rone_client
  --supply` / 통계표 재탐색 `--tables`. 청약홈 분양정보+경쟁률은 별도
  surface(위).
  완전 미러링 (2026-05-31): Daily Byte 와 동등 — `realestate_infographic.py`
  (지역별 평균가/거래량 막대 + 평당가 matplotlib PNG, 사진 push + 카드
  임베드), `realestate_monthly.py` (매월 1일 09:00 `realestate-byte-monthly.
  timer` — 주간이 이미 weekly 라 Daily Byte 의 Weekly 에 대응하는 위계는
  monthly), `/realestate_cost` 명령(DM+채널) + /usage·메인 대시보드 cost
  subsystem '부동산'. 블로그는 subsystem '블로그' 로 cost 만 합산(전용 명령
  없음 — 사용자 정책상 채널 포워드 surface).
- 청약 Byte — 신규 아파트 분양 모집공고 daily 피드 (2026-05-31 사용자 요청,
  ticker·5거래일 독립). **부동산 Byte 와 별도 surface** — 분양 모집공고는
  매일 신규로 뜨고 청약 일정이 임박 이벤트라 주간 가격 브리프와 성격이
  다름 (사용자 정책 "청약은 Daily"). `bot/cheongyak_client.py` (청약홈
  **odcloud.kr** API — apis.data.go.kr 실거래가와 호출 방식 다름, page/
  perPage/JSON, 동일 DATA_GO_KR_API_KEY 공유. 엔드포인트 `getAPTLttotPblanc
  Detail`, 필드 HOUSE_NM/SUBSCRPT_AREA_CODE_NM/TOT_SUPLY_HSHLDCO/RCRIT_
  PBLANC_DE/RCEPT_BGNDE·ENDDE/PRZWNER_PRESNATN_DE/PBLANC_URL) + `bot/
  cheongyak_brief.py` (Pro 1-2줄 맥락 narrate + 단지 목록 구조화). seen-set
  (`cheongyak_seen.json`, PBLANC_NO) 중복 차단 + 최근 3일 내 신규만 push,
  신규 없으면 graceful skip(비용 0). `cheongyak-byte.timer` 평일 10:00 +
  14:00 KST 2회(사용자 2026-06-01 — 청약홈 오전·오후 갱신 대응, 같은 공고
  는 seen-set 으로 2회 push 안 됨).
  채널 push + `cheongyak.html` 대시보드 (Daily Byte 패턴 mirror, 오늘/누적
  비용·검색·🗑️ `/api/cheongyak_delete`) + `/cheongyak_cost` 명령(DM+채널)
  + /usage·메인 대시보드 cost subsystem '청약'. 인포그래픽 없음(피드 성격).
  nav 위치: Daily Byte 뒤(daily 그룹). **경쟁률(수요 측) LIVE (2026-05-31)** —
  별도 활용신청 (`한국부동산원_청약홈 청약접수 경쟁률 및 특별공급 신청현황`)
  승인 후 `ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet` 작동. raw 행은
  (해당지역/기타)×(1·2순위)로 분할 → `aggregate_competition_by_unit` 가
  단지·주택형 단위 합산(총접수/총공급 = 진짜 경쟁률, 미달 세대수 산출).
  `recent_competition_enriched` 가 PBLANC_NO 로 announcements 와 join 해
  단지명·지역 enrich. brief 본문에 [최근 마감 청약 경쟁률] 섹션 자동
  부록(미달 우선·경쟁률 내림차순 TOP 8). graceful skip — 경쟁률 미등록/
  비어있어도 신규 공고 push 는 정상.
- Daily Byte — 장 마감 후 KR 수급 브리프 (2026-05-29 사용자 요청):
  • `daily-byte.timer` — 평일(Mon-Fri) 19:00 KST oneshot → `bot/daily_
    kr_flow.py`. pykrx EOD 수급 (~17-18시 갱신) 안정 후 19:00 실행.
  • 설계: A=pykrx 단일(무료) · B=Pro+google_search grounding ON ·
    C=KOSPI+KOSDAQ 전체 net-buy 랭킹 · D=기존 NOAH 채널 push · E=구조화
    long-form · F=수급 중심 "주목 종목" 중립(BUY/SELL 권고 아님).
  • 원칙: **수치는 Python 이 pykrx 에서 정확 산출 (환각 0)** — 시장
    총평 (get_market_trading_value_by_investor) + per-stock 랭킹
    (get_market_net_purchases_of_equities, 외인/기관/연기금/투신/사모
    당일+5일누적) + 양→음 전환 감지. Pro 는 섹터 그룹핑 + 로테이션
    narrative + catalyst (web search) 만, 수치는 anchor copy.
  • 가드 재사용: audit 의 _strip_future_dated_citations + _strip_invalid_
    dates + markdown strip post-process. 미래 날짜 citation / 가공
    catalyst 차단.
  • 비용 ~₩70/일 (Pro 1 call) ≈ 월 ₩2K. 제목 "Daily Byte - YYYY.MM.DD"
    (거래일 자동). 데이터 부재(공휴일) 시 walk-back 4회 후 graceful skip.
  • ✅ **LIVE (2026-05-29)** — KRX_ID/KRX_PW 가 `.env` 에 로드됨. 수동
    검증 run 에서 `KRX 로그인 완료 (ID higgack)` → 수급 fetch → Pro 종합
    → 채널 push 확인 (₩29.7, 실데이터: 기관 +20,548억 등). 배경: KRX 가
    **2025-12-27 부터 'KRX Data Marketplace' 로그인 필수**로 전환 →
    pykrx≥1.2.8 가 KRX_ID/KRX_PW 인증. 같은 pykrx 를 쓰는 main /ticker KR
    수급(`pykrx_client.py`)도 이 creds 로 동시 부활 (외인/기관 flow · 시총
    fallback · 52주/SMA · 베타60m · 외인지분/공매도 추이).
    가드: `krx_login_ready()` preflight (creds 없으면 1회 경고 + clean
    skip) + `_quiet_pykrx_logging()` (내부 logging 버그 폭주 차단). creds
    제거 시 자동 graceful skip 으로 복귀.
  • 대시보드 (2026-05-29): `archive/daily_byte.html` — screener.html 패턴
    mirror (date-그룹 카드 + 검색창 scr-* + 🗑️ 휴지통 `/api/daily_byte_
    delete` + 기존 _THEME_JS light/dark + _SCREENER_CSS 재사용). 메인
    index.html nav 최상단 "📊 Daily Byte" 링크. `_save_daily_byte_archive`
    가 run 마다 `~/.tradingagents/daily_byte_archive/YYYY-MM-DD/HHMMSS_
    daily_byte[_weekly].json` 기록 → `regenerate_daily_byte_index()` (startup
    + 자정 periodic + delete 후). dashboard.py `_load/_render_daily_byte_*`.
  • Weekly 종합 (2026-05-29): `bot/daily_kr_weekly.py` (SV weekly_pusher
    mirror) — 이번 주 daily 아카이브 본문 모아 Pro 종합 → push + 아카이브
    (kind="weekly"). `daily-byte-weekly.timer` **일 22:00 KST** (SV
    standardview-weekly 동일 시각). install.sh 등록.
  • 비용 통합 (2026-05-29): `_log_daily_byte_usage` 가 **subsystem=
    "daily_byte"** 로 usage.jsonl 기록 (screener._log_usage 는 screener
    하드코딩이라 별도) + daily_byte_usage.jsonl. → 메인 대시보드 cost 카드
    총합 자동 합산 + subsystem 분포에 "Daily Byte" 행, `/usage` 분포 + 총합
    포함(분석에서 분리), `/daily_byte_cost` 전용 카드 (screener_cost mirror,
    DM+채널). help §1 명령 + §8 알림 갱신.
  • 내용 강화 (2026-05-29, 벤치마크 수준): `collect_flow_data` 가 (a) 20일
    누적(외인·기관), (b) breadth(외인+기관 합산 순매수종목 비율 %),
    (c) per-ticker 등락률(`_fetch_price_change`), (d) 시총(`_fetch_mcap_eok`)
    + net/시총 비중 추가. `build_data_summary` 가 행마다 등락률·시총·비중
    병기, 프롬프트는 당일/5일/20일 다중 시간축 가속 판단 + 시총대비 강한
    매집 + 5선 catalyst 의무화.
  • 인포그래픽 (2026-05-29): `bot/daily_byte_infographic.py` — matplotlib 로
    수급 데이터(정확값 직접 주입, 환각 0)를 전문 PNG 렌더 (헤더/주체별 막대/
    breadth/당일 TOP/경고). `generate()` 가 렌더 → `push_telegram_photo`
    (sendPhoto) 로 텍스트 브리프 앞에 사진 push. **한글 렌더는 VM 에
    NanumGothic 필요**: `sudo apt install -y fonts-nanum` (없으면 `_font_
    ready()` False → 인포그래픽만 skip, 텍스트 정상). 이모지는 NanumGothic
    미지원이라 컬러 탭으로 대체, 음수는 ASCII '-'(U+2212 미지원).
  • 인포그래픽 대시보드 임베드 (2026-05-29): PNG 를 대시보드가 서빙하는
    `archive/daily_byte_img/{date}_{HHMMSS}.png` 에 저장 → JSON `png` 필드
    (archive/ 상대경로) → `_render_daily_byte_page` 카드에 `<img>` 임베드
    (regex 검증 `^daily_byte_img/[\w.\-]+\.png$`). 텔레그램은 sendPhoto 로
    같은 PNG push. delete 시 `_handle_daily_byte_delete` 가 png 도 함께 unlink.
  • 폰트 자동 설치 (2026-05-29): `deploy/install.sh` 가 `fc-list | grep nanum`
    부재 시 `apt-get install -y fonts-nanum` + `fc-cache -f` (idempotent,
    `|| true`). auto-update 가 deploy/* 변경 감지 시 install.sh 자동 호출 →
    SSH 없이 폰트 설치. 실패해도 텍스트 브리프 정상.
  • 메인 대시보드 nav 순서 (사용자 2026-05-29): errors → Bottleneck Screener
    → 도메인 목록 → **Daily Byte** → 외부(SV/🇰🇷). help §10(대시보드)에 Daily
    Byte 링크+설명 추가, §9(트러블슈팅) 삭제로 4096 cap 확보(섹션 1~11 재번호,
    트러블슈팅 내용은 본 CLAUDE.md 보존).
  • 다듬기 (2026-05-29 실데이터 1차 review): (a) 인포그래픽 **landscape
    3-column** 재설계 (헤더 전폭+breadth 우측 / 시장수급·외국인TOP·기관TOP
    3열 / 경고 전폭) — 기존 세로형이 화면 반절만 채우던 것 해결. 대시보드
    `<img>` max-width 520px→100% (카드 full-width). (b) `_post_process` 가
    markdown 수평선(`---`/`***`/`___`) 줄 제거 + 연속 빈 줄 정리. (c) 프롬프트:
    굵게(`**`)는 헤더·핵심 수치만, catalyst·맥락 문장은 일반 텍스트.
  • ⚠️ **watchdog↔httpx 결합 (회귀 주의)**: `deploy/watchdog.sh` 는
    journald 의 `getUpdates` INFO 로그 유무로 polling 생존을 감지한다.
    토큰 누출 막겠다고 httpx 로거를 WARNING 으로 **억제하면 getUpdates
    로그가 사라져 watchdog 가 매 사이클 오탐 → 봇 무한 재시작**(2026-05-29
    실제 발생). 해결: telegram_bot 은 httpx 를 INFO 유지하되 `_TokenRedact
    Filter` 로 토큰 문자열만 마스킹 (getUpdates 보존 + 누출 차단). **앞으로
    httpx/httpcore 로거를 telegram_bot 에서 절대 억제하지 말 것.** (daily_kr_
    flow 는 oneshot·watchdog 무관이라 WARNING 억제 유지 OK.)
  • help §10 대시보드: 모든 URL 을 full bare URL 로 표기 (Telegram 자동
    하이퍼링크 → 클릭 가능 + 전체 주소 표시). Daily Byte 링크 포함. slack 112.
  • 다듬기 2차 (2026-05-29): (a) separator strip 정규식 확대 `^[^\w\n]*[-*_]
    {2,}[^\w\n]*$` — '--- / ---' 슬래시 혼합형까지 차단. `_render_daily_byte_
    page` 도 render 시점 strip (strip-fix 이전 아카이브된 옛 run 소급 정리).
    (b) 인포그래픽 dpi 124→150 (선명도), 대시보드 `<img>` max-width 100%→680px
    + margin auto (크기 축소 + 중앙 배치) — 사용자 '너무 크고 화질 별로'.
  • 비용 (2026-05-29 검토): Pro 1콜, ₩29.7→**₩53.4** (내용 강화로 output
    증가 — output $10/M 이 dominant, input enrich 는 $1.25/M 소액). 인포그래픽
    은 matplotlib 라 ₩0. 월 ~₩1.6K — Pro 유지가 품질-최적 (Flash 전환 시
    ~4x 절감되나 narrative/grounding 품질 저하, 일 1회 ₩53 는 무시 가능).

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

**배너 정확성 (IBM 2026-06-02 review)** — Fix F/G 가 강제 HOLD 하면
(분석가 전원 합의 ↔ PM override + trigger 없음), 최종 등급은 PM 의견이
아닌 시스템 보정 결과다. 옛 배너 '⚠️ 트레이더 매수 → 최종 보유 (방향
상충 — PM이 트레이더와 다른 결론)' 는 오독을 부른다: IBM 은 PM=Overweight
+ Trader=Buy (둘 다 매수) 였으나 분석가 4명 전원 보유 + RSI≥75 sell-side
trigger 도 D-N일 임박 catalyst 도 PM rationale 에 없어 Fix F 가 HOLD 강제
한 케이스. PM 이 트레이더와 다른 결론을 낸 게 아니라 시스템이 양쪽을
모두 HOLD 로 보정한 것. `analyzer._format_summary` 가 `override_rating ==
"Hold"` 일 때 `_detect_discipline_forced_hold_banner` 로 분기해 '⚠️ 시스템
강제 보유 (PM override discipline): 분석가 전원 X 합의인데 PM Y override
시도 → trigger 미인용 자동 HOLD. PM·트레이더 의견이 아닌 시스템 보정 결과'
정확 배너 출력. enum lock(Overweight→BUY 무조건 매핑)은 거부 — discipline
자체가 현대모비스/호텔신라/코미코 클러스터 방지 정책이므로 우회 불가.

**기술 지표 SSoT 확장 (IBM 2026-06-02)** — `_compute_technical_snapshot`
이 RSI/MACD/볼린저만 SSoT 였고 10 EMA/50 SMA/200 SMA 는 별도 경로
(stockstats/alpha_vantage)라 시점 어긋남 → IBM 10 EMA 266 vs 현재가 325
(22% 격차) stale 출력. fix: 현재가 + 10 EMA + 50 SMA + 200 SMA 를 같은
yfinance close series(1y 윈도)에서 계산해 snapshot 에 함께 박고 '현재가
대비 %' 병기 + 글자단위 copy 강제. 매크로(`get_macro_context`)도 (market,
date) 단일 캐시지만 본문 재서술 시 paraphrase drift (IBM 뉴스 '10Y 4.51%'
vs 매크로 '4.45%') → SSoT 글자단위 copy directive 추가.

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

### Dashboard 인증 — 기본 정책 (사용자 정책 2026-05-31)

**우리가 만드는 모든 대시보드는 HTTP Basic Auth 기본 적용.** 기본 사용자
ID = `higgack`, 비밀번호는 **`.env` 의 `DASHBOARD_PASSWORD` 에만 보관**
(literal 값을 코드·CLAUDE.md·git 어디에도 적지 말 것 — gitignored `.env`
전용). NOAH archive 서버(`bot/dashboard_server.py`)가 `DASHBOARD_USER` +
`DASHBOARD_PASSWORD` 둘 다 set 이면 모든 경로에 Basic Auth 강제 →
screener / daily_byte / realestate / cheongyak / 향후 추가 surface 가
**단일 서버·단일 자격증명으로 자동 일괄 보호** (대시보드는 전부 같은
ARCHIVE_ROOT 정적 서빙). 적용:
```
DASHBOARD_USER=higgack
DASHBOARD_PASSWORD=<.env 에만>
```
**Nav 순서 정책 (사용자 2026-05-31):** NOAH archive 헤더 nav 에서 새로
추가되는 대시보드는 **항상 제일 끝(마지막)에 append**. 현재 순서:
errors → Bottleneck Screener → 도메인 목록 → 📨 미국 레딧 → Daily Byte
→ (external: Standard View · 한국 수출입) → 🏠 부동산 → 🎟️ 청약. 앞으로
만드는 대시보드(예: 신규 surface)는 기본적으로 이 줄 맨 끝에 붙일 것 —
사용자가 명시 위치를 지정하면 그에 따름(미국 레딧은 사용자 2026-06-03
요청으로 도메인 목록 다음으로 이동). (`bot/dashboard.py` errors_link 두
분기 모두 갱신.)

**💰 비용 합산 정책 (사용자 2026-06-02):** 메인 NOAH 대시보드 비용 카드
(+ `/usage`)는 **nav 에 링크된 비용-발생 surface 전부의 cost 를 합산**해
총합을 표시하고, 각 surface 는 개별 비용을 자기 카드 / 전용 명령에서
표시. 현재 합산 대상 8개 subsystem:
  - usage.jsonl (subsystem 태그): 분석 / Screener / Daily Byte / 청약 /
    부동산 / 블로그 — `_compute_stats` 가 subsystem 매핑으로 분리 합산.
  - sv_usage.jsonl: Standard View (cost_krw + date 스키마).
  - **한국 수출입(trade)**: 별도 repo(stock-trade) 의 usage.jsonl.
    경로 `$TRADE_DATA_DIR/usage.jsonl`, 미설정 시 `~/.trade/usage.jsonl`.
    그 repo 스키마를 우리가 100% 통제 못 하므로 **방어적 reader** —
    cost_usd / cost_krw 양쪽 + date(YYYY-MM-DD str) / ts(epoch) 양쪽
    tolerant. 파일 부재·다른 호스트 시 silent skip. 모델 분포 미상이라
    by_model 미합산(총합·subsystem 분포만). subsystem 키 "수출입".
  새 비용-발생 대시보드를 nav 에 추가하면 **반드시** (a) `_compute_stats`
  의 `_sub_keys` + 합산 루프 + `_render_stats_panel` 의 sub_parts 순서,
  (b) `telegram_bot.cmd_usage` 의 합산·분포 라인 두 곳을 동시 갱신.
  breakdown 은 `if m_usd > 0` 으로 이번 달 0 인 surface 는 숨김(compact).

**⛔ 외부 사이트(우리가 운영하지 않는 third-party URL) surface 정책
(사용자 2026-06-01):** Standard View / 한국 수출입 처럼 우리가 같은 VM
에서 운영하는 보조 대시보드는 메인 nav `_external_links` 에 유지.
그 외 외부 third-party 사이트(Stockeasy / Jusikbot / aibottlenecks.app
/ analytics.blancwm.com / reports.blueming.net 등 사용자가 참고용으로
모아두는 링크)는 **오직 `/sites` 명령 (`bot/telegram_bot.py` `_SITES_
TEXT`) 한 곳에만 추가**. 메인 대시보드 nav `_external_links` 추가 금지
+ `_HELP_TEXT` §9 대시보드 절 추가 금지. 이유: 메인 nav 와 help 는
우리 시스템 surface 만 깔끔히 유지 (사용자가 nav 클릭으로 외부 사이트
가는 혼란 방지), 외부 참고 모음은 `/sites` 가 전담 단일 source. 새 외부
사이트 추가 시 `_SITES_TEXT` 의 `<li>` 줄 하나만 추가하면 끝 — 다른
파일/섹션 동기화 불필요.

**⛔ `_SITES_TEXT` 항목은 이모지 없이 plain text 만 (사용자 정책
2026-06-01):** 새 외부 사이트 추가 시 `📝` `📊` `🔗` 등 이모지 prefix
금지. 기존 항목 (Stockeasy / Stockhub / Jusikbot 등) 전부 plain text
형식 유지 중이며 일관성 보존이 사용자 명시 요청. anchor text 는 원본
사이트 이름 그대로 (필요 시 한국어 부제 병기 OK). 이 규칙은 향후 모든
외부 사이트 추가에 영구 적용.

.env 변경 후 `sudo systemctl restart dashboard` (env 는 import 시 1회 읽음).
**향후 별도 포트로 새 대시보드 서버를 만들면 동일하게 `DASHBOARD_USER`/
`DASHBOARD_PASSWORD` 를 읽어 같은 기본 자격증명으로 보호할 것** — 이것이
default. (SV·한국수출입 등 외부 앱은 자체 인증 env 보유.) ⚠️ 비번이
채팅에 노출되면 회전 권고.

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

✅ Phase 4-CN-D validation — 완료 (fix#1~#11, commits 91427a9~925f926):
 CN/HK + TW/JP/KR 크로스-마켓 검증 포함. BYD/Tencent/현대차증권/
 노바렉스/LG생활건강 등 11개 fix batch 완료.
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

- **SEC XBRL 권위 재무 (US authoritative financials, 2026-06-04)** —
  `bot/edgar_client.py` `get_key_financials(ticker)` 가 data.sec.gov
  `/api/xbrl/companyconcept` 에서 매출/순이익/EPS희석/영업현금흐름/자산/
  부채/자본/발행주식수 8개를 **실제 10-K/10-Q 원본**(us-gaap/dei)에서
  fetch (CIK 맵 24h + concept 12h 캐시, 무키, UA 헤더, 404=concept 부재로
  빈 캐시). metric 당 concept fallback 리스트(Revenue 4종 등 filer 별 태깅
  차이), 정정 공시는 max filed 선택, annual(FY/10-K) + 최근분기 병기.
  `format_xbrl_block` → `_prefetch_market_io` US 브랜치 병렬 task
  `edgar_xbrl` (지연 0, 8 HTTP·12h 캐시) → build_instrument_context 가
  **펀더멘털 분석가만**(market/social/news 는 `_ANALYST_CONTEXT_EXCLUDE`
  로 제외, cashflow/balance/ratios 와 동급) 주입. directive: "US 재무는
  SEC 원본 최우선 인용, yfinance 와 다르면 SEC 정본, 글자단위 사용".
  **이것이 US 의 DART/EDINET/MOPS 등가물** — KR/JP/TW 는 공식 공시 원본을
  쓰는데 US 만 yfinance 집계에 의존하던 비대칭 해소. **Phase 2 완료
  (2026-06-04)**: (a) **ADR/외국법인 20-F IFRS** — metric 당 us-gaap +
  ifrs-full concept fallback (Revenue/ProfitLoss/Equity 등), `_choose_unit`
  이 외화 단위(EUR/JPY/CNY) 자동 선택 + 표시, 연간 form 필터 10-K/20-F/40-F.
  (b) **yfinance divergence 자동 플래그** — 발행주식수(point-in-time 라
  깨끗이 비교 가능)만 robust 하게 대조: SEC vs yfinance 10%+ 차이 시 ⚠️
  "분할/stale 의심, SEC 우선"(injection 이 `_instrument_info.sharesOutstanding`
  전달). flow 지표(매출/순이익)는 TTM≠FY 노이즈라 자동 대조 미적용(의도).
- **MANDATORY COMPS PEER SET** — `_US_INDUSTRY_PEERS` (bot/market.py)
  with ~70 yfinance-industry rows covers S&P 500 mega/large + active
  mid-caps. `resolve_peer_set` dispatches by market: KR→`_KR_*`,
  JP→`_JP_*`, default→`_US_*`. Peer multiples pre-fetch (Rule C in
  agent_utils._fetch_peer_multiples) runs for any market once a peer
  set is returned.
- **CORPORATE ACTION HARD GUARD — 4-source** in build_instrument_context:
  (1) DART scan for 무상증자/주식분할/액면분할/주식병합/감자 (KR),
  (2) EDINET scan for 株式分割/株式無償割当/株式併合 (JP),
  (3) universal yfinance `.splits` ex-date scan (`_detect_yf_corp_action`,
  any market, 14-day lookback),
  (4) **FSC 권리일정 백업 (KR, `_detect_fsc_corp_action`, 2026-05-31)** —
  DART scan miss 시 금융위 권리일정(getRighExerReasSche_V2, KSD) 의
  rcdNm 이 증자/감자/분할/병합/교환 인 행 필터(정기 기준일·배당 제외),
  crno(fsc item_info)로 매핑. DART 키 부재/키워드 변형으로 놓친 케이스
  백업. All four emit the same "ban SMA/EMA/MACD/RSI/Bollinger" HARD
  GUARD body. US gets (3); KR gets (1)+(3)+(4); JP gets (2)+(3).
- **KRX-login-free 시세 백본 (FSC, `bot/fsc_client.py`, Phase 1 2026-05-31)** —
  pykrx 가 2025-12 KRX 유료화로 KRX_ID 의존 → creds 부재/장애 시 KR 시총·
  종가·거래량 dormant 였던 취약점 해소. `pykrx_client.get_kr_market_cap`
  이 None(creds 없음 OR 무데이터) 일 때 `fsc_client.latest_price`(금융위
  주식시세 getStockPriceInfo)로 자동 fallback — 동일 shape(market_cap=
  mrktTotAmt/close=clpr/volume=trqu/shares=lstgStCnt), `_source:"fsc"` 태그.
  FSC 는 T+1 지연이라 5거래일 horizon·시총 cross-check 무해. 무료·동일
  DATA_GO_KR_API_KEY·12h 디스크캐시. item_info 의 crno 는 향후 DART corp
  매핑 연결키. 시세/종목=공공누리 제한없음, 권리일정=2유형(출처표시+비상업,
  출처 KSD) — NOAH 비상업 OK.
- **FSC Phase 2 통합 (2026-05-31)** — `bot/fsc_client.py` 3개 신규 + 3지점
  통합 (전부 additive·try/except·12h 캐시·실패 시 블록 생략):
  (A) **증권상품시세 ETF/ETN** (GetSecuritiesProductInfoService 15094806,
  제한없음) `securities_product_quote` → 종가·NAV·**괴리율**·순자산·기초지수.
  build_instrument_context KR ETF/ETN 브랜치(B2)에 "공식 시세" 블록 주입
  (yfinance KR ETF 빈약 보완, KODEX200 괴리율 -0.06% 검증).
  (B+C) **금융투자협회 종합통계** (GetKofiaStatisticsInfoService 15094809,
  제한없음) `market_deposit`(투자자예탁금 invrDpsgAmt)·`margin_balance`
  (신용융자 crdTrFingWhl) → `market_liquidity_line` 한 줄(조 단위·WoW/MoM,
  LLM 에 raw 원 미노출=환각 방지). **시장 전체값→1 fetch 12h 캐시로 전 KR
  분석·Daily Byte 공유**(per-ticker 부담 0). 주입 2곳: Daily Byte 시장총평
  (build_data_summary) + KR equity build_instrument_context "KR 시장 유동성"
  블록(시장분석가가 retail 자금·레버리지 frame 으로 해석). 예탁금↑=대기매수,
  신용융자↑=레버리지 과열. 미통합 FSC API(배당/대차)는 Phase 4+.
- **FSC Phase 3 — 의무보호예수(lock-up) (2026-05-31)** — 단기 공급 overhang
  신호(우리 모델에 전무했던 gap). 금융위 주식발행정보 V3
  (GetStocIssuInfoService_V3/getLockUpRetuInfo_V3, basDt 필수+crno 필터).
  `fsc_client.lockup_releases(ticker)` → rsrnDt(반환일=해제일)·rsrnStckCnt
  (반환주식수)·afrsRsqtCnt(잔량)·사유. build_instrument_context KR equity
  브랜치에 "📌 의무보호예수 해제 예정" soft 배너(해제일 -7~+90일 윈도).
  corp-action HARD GUARD 와 별개 — 기술지표 무효화 X, 수급 압력만. additive·
  실패 시 생략. crno=item_info 연결(Phase 1). 유통주식수(같은 V3 주식발행현황
  op)는 후속.
- **FSC Phase 4 — 소액주주현황 + dilution 공시 (2026-05-31)** — 사용자
  "소액주주+CB/BW까지, 재벌 제외" 결정. 둘 다 crno 조회·KR equity context
  주입·additive·실패 시 생략.
  (소액주주) GetCGDiscInfoService/getCGSmamInfo → `minority_holders` —
  smamSthdRto(소액주주비율)·smamSthdCnt·whlSthdCnt·holdStckCnt. free
  float 근사 ("소액주주현황" 블록: 비율↑=유통물량 많음/변동성↑, 비율↓=
  최대주주 집중·품절 취약). 연/분기 공시라 정적.
  (dilution) GetDiscInfoService_V2 2 op → `dilution_events` — CB
  (getCbRighIssuDiscInfo_V2, cpbdCnvrStckCnt/cbCnvrPrc) + BW
  (getBwRighIssuDiscInfo_V2, prmrIssuStckCnt/bwrExertPrc). "📉 잠재 희석
  이벤트" 배너(기술지표 차단 X, 수급 경고만). ⚠️ **유상증자는 제외** —
  corp-action HARD GUARD 키워드(_KR_CORP_ACTION_KEYWORDS)에 '유상증자'
  이미 존재 → 중복 배너 방지(2026-05-31 review). CB/BW 는 corp-action
  키워드에 없는 별개 잠재희석이라 dilution_events 전담. 나머지 30 op(무상
  증자/감자/합병 등)은 corp-action·DART·뉴스 중복이라 미통합. probe:
  `--minor` / `--lockup`. ⚠️ 소액주주(getCGSmamInfo)는 `금융위원회_기업
  지배구조 공시정보` 활용신청 필요 — 미승인 시 null(graceful 생략).
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
- **GBp (London pence) normalization** — `_instrument_info()` 단일
  지점에서 currency='GBp'/'GBX' 라벨 + heuristic fallback (.L suffix +
  px > 1000) 으로 pence→pounds 변환. cross-anchor check (Fix A/B/C/H)
  100x mismatch false-fire 차단. WEIR.L (Metals & Mining 2026-05-29 review)
  + BA.L (Space Launch 2026-05-29 review) surfaced. Commits `4254da0`,
  `1abf2bf`. Rule applies to all LSE 종목 universally.
- **EU dual-class (Wallenberg 패밀리 A/B + 독일 Vz/St)** — `_EU_DUAL_
  CLASS_TICKERS` set (Sweden 17쌍 + Denmark 4 + Norway 1 + Germany 9 Vz).
  Fix C (shares × price vs marketCap > 5%) auto-suppress for these
  tickers — yfinance 가 한 class shares 만 반환하지만 marketCap 은
  양 class 합산이라 구조적 mismatch. EPI-A.ST (Epiroc Class A) 2026-
  05-29 surfaced. Commit `4254da0`.
- **KR 우선주 false transitional (Samsung 005930 2026-05-31 review)** —
  보통주(005930)에 상장 우선주(005935)가 있으면 yfinance sharesOutstanding
  (보통주)×price vs marketCap(보통주+우선주 합산) 이 ~10% 괴리 → cross-
  anchor MC check 가 'corp action 의심/transitional' false fire → 전체
  리포트 기술지표 분석 보류 난장판. EU dual-class 와 동일 클래스 버그.
  `bot/market.has_kr_preferred_shares` 가 우선주 코드(끝자리 0→5/7/K)가
  pykrx 상장목록에 있으면 자동 감지 → skip_mc_check + info_lines(정보성,
  ⛔ HARD GUARD 미발화). 시총은 marketCap canonical, 기술지표 정상 진행.
  하드코딩 리스트 불필요(우선주 페어 자동 감지). 우선주 부재 종목은 진짜
  corp-action 정상 발화(보수적).
- **DART 임원지분 100% 환각 (Samsung 005930 2026-05-31 review)** — DART
  elestock 의 sp_stock_lmp_rate(특정증권 소유비율)가 가끔 100.0(본인
  보유분 비율=항상 100%)으로 와, 이를 회사 전체 지분율로 오인 → "이종민
  부사장 100% 지분" 같은 금융정보 파괴. dart_client 가 pct≥50% → None +
  pct_suspect 플래그, build_instrument_context 가 '회사 지분율 N/A (N주)'
  로 렌더 + "개인 보유분 비율 ≠ 회사 지분율" 가드. 시총 2,000조 회사 개인
  100% 보유 불가.
- **pykrx 5일 누적 수급 단위 혼동 (Samsung 005930 2026-05-31 review)** —
  LLM 이 5일 누적 +2조(기관)를 RULE 10 의 '당일 ±100억 noise' 기준에 잘못
  적용해 "2조 순매수 → 노이즈 수준" 모순 서술. format_flow_for_prompt 가
  ±1,000억 이상 누적은 Python 이 '강한 dominant 수급, 노이즈 아님' 가드
  라인 박음 (KIS 패턴 mirror) + '당일 아닌 5거래일 누적' 명시.
- **Nano-cap (<$50M USD) 경고 강화** — bot/screener.py 의 S 티어 유동성
  경고 directive 가 시총 segment 별 분리: Micro-cap ($50M-$300M) =
  기존 ⚠️ 메시지, Nano-cap (<$50M) = NANO-CAP LIQUIDITY WARNING (일일
  거래대금 <$1M, 호가 공백, 슬리피지 ±5%+, 기관 진입 사실상 불가).
  AQMS ($14M) 2026-05-29 Metals & Mining review surfaced. Commit
  `4254da0`.
- **RULE 11 半導體/化学 소재 카테고리** (JP) — fundamentals_analyst.py
  의 RULE 11 에 9 산업 (자동차/은행/부동산/제약/상사/반도체장비/통신/
  철강/전력) + 半導體 소재/化学 신설 (4063 신에쓰 / 3436 SUMCO / 6963
  ROHM / 4631 DIC / 7741 HOYA). 글로벌 wafer/포토레지스트/SiC/EUV 마스크
  substrate 점유율 dominant 종목. Dominant 변수: 美 對中 수출규제 BIS +
  AI capex sustainability + TSMC/Samsung wafer 발주 cycle + USD/JPY.
  4063.T 2026-05-29 review (USD/JPY 단일 변수만 단조 fire) surfaced.
  Commit `1721eeb`.

## Universal screener guards (Bottleneck Screener Pro 출력 검증)

Screener Pro Phase 4·5 출력은 NOAH /TICKER 와 별도 post-process pass
를 거친다. 2026-05-29 외부 리뷰 batch (EV / Quantum / Space Launch /
Metals & Mining) 가 surfaced 한 universal 결함을 prompt + Python 양면
에 fix:

- **FUTURE FABRICATION HARD GUARD** (prompt) — `bot/screener.py`. Pro
  가 시뮬레이션 시간 (2026) 과 학습 cutoff (2024-Q2) 사이 1-2년 갭을
  채우려 미래 실적/M&A/citation 가공하는 패턴 차단. 5 규칙:
  (a) 미래 실적 (매출/EPS/RPO/가이던스) fabricate 금지,
  (b) 미래 M&A/사업부 매각/인수 fabricate 금지,
  (c) sourced citation 날짜 ≤ 오늘 강제,
  (d) Catalyst+시기 구체 YYYY-MM-DD stamp 금지 (fuzzy window 만),
  (e) PAST event (IBM Condor / Switch 2) 의 FUTURE framing 차단.
  Quantum review 가 IONQ $64.7M Q1 2026 매출 + OXIG.L 2025-06 매각 +
  IBM 2027-2028 Condor 등 4건 fabrication surfaced. Commit `6f9d01a`.
- **Future-dated citation Python strip** — `_strip_future_dated_
  citations()` 가 'sourced: <pub>, YYYY-MM-DD' regex 매칭 후 date >
  today_kst 면 '⚠️ inferred — future-dated citation' 치환. egregious
  케이스 backstop (Pro 의 prompt 위반). Commit `6f9d01a`.
- **Transitional / corp action 의심 tag strip** — `_strip_
  transitional_tags()` 가 '(데이터 transitional)' / '(corp action 의심)'
  인라인 phrase strip. screener 는 공시 fetch 안 함 → 이 phrase 인용
  금지 (CLAUDE.md line 331-333). VOYG / 017960.KQ / BA.L (Space Launch
  2026-05-29) 위반 surfaced. Commit `1abf2bf`.
- **RULE 12-14 dominant variable enforcement (screener prompt)** —
  screener 도메인 종목 중 반도체 supply chain / EV / 방산 / 신재생 /
  제약 / 양자 / 금융 종목이 있으면 산업 dominant 정책/매크로 변수
  명시 의무. Quantum review 가 반도체 supply chain 8종 (FORM/CAMT/
  COHR/KEYS/WOLF/PLAB/LASR/Hamamatsu) 에 美 對中 수출규제 + CHIPS Act
  한 번도 cite 안 함 surfaced. Commit `6f9d01a`.
- **Sub-theme padding 자제** — 도메인 자체가 4 sub-theme 만 식별되면
  무리하게 5-6 째 만들지 말 것. Quantum PQC (Cisco/Cloudflare) catalyst
  '2030년대 초반 PQC 의무화' 가 6-24m thesis 윈도 밖 케이스. Commit
  `6f9d01a`.
- **MACD 글자 단위 copy 강제** (NOAH /ticker — `_compute_technical_
  snapshot`) — TECHNICAL SNAPSHOT 의 SINGLE SOURCE OF TRUTH 항목에
  글자 단위 copy 의무 추가. 4063.T review (시장 'Hist 7.476' vs 뉴스
  'Hist 7.986' 0.5 mismatch) surfaced. paraphrase / 반올림 금지.
  모든 분석가 같은 문자열 copy 의무. Commit `1721eeb`.
- **Calendar validator** (`_strip_invalid_dates`) — datetime.date(y,m,d)
  validate. 2026-02-29 (non-leap Feb 29) / 2025-04-31 / 2026-13-01
  같은 invalid 날짜 fabrication 자동 '⚠️ inferred — invalid date' 치환.
  Semiconductors review 2026-05-29 디아이 003160.KS '2026-02-29 공급
  계약' surfaced. Commit `7380b05`.
- **Single-ticker-per-row** (prompt) — Master Table 한 행 = 정확히 한
  ticker (또는 `(inferred)` + 단일 회사 OR 'no clean public name').
  '글로벌 파운드리 (TSMC, Tower Semi)' 같은 multi-ticker mixed 라벨
  금지. Commit `7380b05`.
- **KR suffix normalization** (`_normalize_kr_suffix`) — pykrx KOSPI /
  KOSDAQ ticker list cache 로 잘못된 .KS↔.KQ suffix 자동 정정. GST
  083450.KS → .KQ (Hardware review 2026-05-29) surfaced. 다운스트림
  pykrx flow / KIS 수급 / KRX 시장경보 silent miss 차단. Commit pending.
- **Local currency symbol** (`_fix_currency_symbols`) — ticker suffix
  → 통화 기호 state machine. TPRO.MI '$34.00' → '€34.00', 3231.TW
  '$161.00' → 'NT$161.00', SUBC.OL '$305.00' → 'kr305.00', MCE.AX
  '$0.39' → 'A$0.39', ITC.NS '$286.90' → '₹286.90' 자동 fix (Tobacco
  2026-05-31 surfaced — 인도 NS/BO 매핑 누락이었음). 정규식 `\$(\d[\d,]*\.\d{2})(?![a-zA-Z\d])`
  (괄호 선택 + 소수 2자리 의무 + 뒤 영문 없음 → '$1B'/'$8.4B' 시장사이징
  보존, 괄호 없는 prose '현재가 $305.00' 도 매칭 — Oil/Gas 2026-05-29
  surfaced 누수 해소). prompt 단에서도 의무화. Hardware + Oil/Gas review
  2026-05-29 surfaced.
- **DATA INTEGRITY ABSOLUTE RULE** (prompt) — 외부 API(yfinance/Finviz/
  KRX/DART/EDINET/MOPS/AKShare)가 반환한 ticker · company name · 시총 ·
  등락률 · 통화는 절대적 사실. "yfinance 상 'X' 로 표기되나 해당 코드는
  'Y'이므로 데이터 해석 주의" 같은 자의적 수정·경고문 절대 금지. Oil/Gas
  2026-05-29 review 100130.KQ 동국S&C 케이스 — LLM 사전지식(cutoff 2024-
  Q2)이 stale, KRX 공식이 맞았음. 원칙: API 가 맞다 · 사전지식이 stale
  하다. 정말 회사 정체성이 도메인과 어긋나면 OMIT (경고 부착 금지).
- **STALE QUARTERLY DATA** (prompt) — TODAY 기준 6mo+ 과거 quarterly
  데이터를 '최근' 부사와 함께 cite 금지. Vertiv '2025 Q3 (3 분기
  전) 수주 +60% 최근 보고' 위반 (Hardware review 2026-05-29) surfaced.
  Newer Q web verify 또는 'past data' 명시 의무. Commit pending.
- **현재가 출력 의무 + 로컬 통화 기호** (Phase 4·5 prompt) — 통화
  정규화 가드 추가 후 LLM 이 'safe-fallback' 으로 가격을 아예 적지
  않는 회피 기동 발생 (NPL/RegTech 2026-05-30 PRAA/CSGP/TEMN.SW/
  NCNO/BX 케이스). directive 추가: '가격 반영도'/'Valuation' 섹션
  서술 시 instrument context 의 canonical 현재가를 시장별 통화 기호
  ($/₩/¥/€/A$/kr/NT$/HK$) 와 함께 명시 의무. 백엔드 _fix_currency_
  symbols 가 후처리하므로 누락만 피하면 됨 — 회피 절대 금지.
- **회피성 문구 금지 — N/A·N/M 명시** (Phase 4·5 prompt) — Tobacco
  2026-05-31 surfaced (6969.HK·ISPR·PRGO 등 6종목이 '데이터 깊이 부족'
  회피문 사용, 멀티플 누락). directive: 멀티플 부재 시 'N/A' 또는 적자면
  'N/M (적자)' 명시, PER 부재 시 PSR/PBR 등 가용 지표로 대체 연산. 누락
  자체를 회피하지 말고 정확히 무엇이 N/A 인지 명시 → reader 가 '없음' vs
  '모름' 구분.
- **ADR 라벨링 정확성** (Phase 4·5 prompt) — Tobacco 2026-05-31 surfaced
  (PM 美 본사 법인 S&P500 구성을 'NYSE 상장 ADR' 로 오인). directive:
  미국 본사 법인 (SEC 1차 등록) → 'NYSE/NASDAQ 상장' (ADR 금지), 외국
  본사 법인 미국 상장 (BTI 영국·NVO 덴마크·TSM 대만·BABA 케이맨) →
  'NYSE/NASDAQ 상장 ADR' OK, non-US (.KS·.T·.HK·.L·.AS) → 해당 거래소
  명시. ticker 1차 상장 SEC 등록 유형 기준 (해외 사업 영위 여부 무관).
- **티어 비대칭 — 빈 티어 사유 명시** (Phase 4·5 prompt) — 한 layer
  의 L/M/S 중 누락이 있으면 빈칸 아닌 1줄 사유 명시 의무 ('S-tier
  부재 사유: 글로벌 oligopoly 구조 pure-play micro-cap 부재' 등).
  강제 채우기 lock 은 적용 X (가짜 종목 회피 정책 — defense·우라늄
  처럼 industry-wide 대형주 도메인은 S-tier 가 구조적으로 없음).
  reader 가 '찾기 귀찮음 vs 구조적 부재' 구분 가능. NPL/RegTech
  2026-05-30 surfaced.

The rule of thumb: when adding any structural guard going forward,
default to **universal** (no market gate) unless the guard depends on
a market-specific data source. Even then, prefer a universal helper
with market-aware branches over per-market parallel functions.

## KR quality enhancement roadmap (Step 2, 2026-05-19)

Step 1 (infra) shipped: F1-MVP Gemini caching + F2 Option 4 verify +
F3-light parallel prefetch. KR quality 강화 작업은 다음 순서로 진행
— API 키 필요한 항목은 final batch (위 TODO API-blocked section)
로 분리:

### ✅ Step 2A — 전체 완료 (2026-05-21 이전 세션):
 1. ✅ **B1** 5-day horizon enforcement — commit `3294cfd`
 2. ✅ **A2** KRX 시장경보 + 거래정지 detect (`bot/krx_alert_client.py`) — commit `c08c612`
 3. ✅ **B4** 시장경보 HARD GUARD inject in build_instrument_context — commit `c08c612`
 4. ✅ **D1** yfinance KR fallback (pykrx 시총·종가 + financialCurrency HARD GUARD + DART Rule G override) — commits `bfe9237`, `f72847c`
 5. ✅ **D2** USD/KRW 영향 자동 계산 (수출주 sensitivity) — commit `e0bede7`
 6. ✅ **A3** 한경 컨센서스 scrape (`bot/hk_consensus_client.py`) — commit `6d1714d`
 7. ✅ **B2** KR ETF analyzer 특화 (KODEX/TIGER metadata) — commit `c7e3f43`

### ✅ Step 2B — 완료 (2026-05-22):
 8. ✅ **A1** KIS Open API 7종 (`bot/kis_client.py`) — 현재가·외인flow·기관주체별·한도소진율·신용·프로그램·공매도
 9. ✅ **B3** RULE 10 KIS 수급 dominant variable 4종 추가 (외인±100억/연기금/한도95%/신용4%/공매도15%)

### ✅ Step 2C — 완료 (2026-05-29, commit `5946213`):
 10. ✅ RULE 10 KIS 수급 dominant variable 4종 추가:
    - 외인 한도소진율 ≥95% → ceiling impact (buy flow 무효)
    - 개인 5일 +100억 + 외인/기관 5일 -100억 → Retail 떠받침 (KR classic 약세)
    - 투신 5일 -50억 → 펀드 환매 leading indicator
    - 프로그램 비차익 ±200억 → 알고리즘 systematic flow dominant
 11. ✅ `format_kis_block` ⚠️ 자동 발화 (Python contrast 계산, LLM 누락 차단)
 RULE 10 KR 변수 총 8개 (Step 2B 4 + Step 2C 4). ₩0 비용 — 기존 fetch 활용.

Trigger to start Step 2B: KIS_APP_KEY + KIS_APP_SECRET 가 .env 에
로드된 후. 발급되기 전 까지 Step 2A 모두 완료 가능 — 병렬 가능
(user 가 KIS 등록 진행하는 동안 bot 은 Step 2A 코드 작업).

Rule applies to all analyses going forward — KR-specific 작업이지만
infra (D1 fallback / D2 환율 계산) 는 다른 시장 (JP/TW/CN 수출주)
에도 동일 패턴 적용 검토 가능. Universal-by-default 가 KR 우선
shipping 후 cross-market parity audit 으로 확장.


## 2026-05-29 모델 audit (cost / quality / robustness) — 3-tier 적용 중

전체 파이프라인 audit (3 병렬 agent + 직접 정독). 위험성 평가 포함, 3축
(문제점 / 개선 / 비용) 으로 분류. 1차 (위험 0, 고가치) → 2차 (견고성) →
3차 (정확성 hygiene) 순서 적용.

### ✅ 1차 — 완료 (위험 0, deterministic):
- **C1 [Critical]** `_extract_rating` (bot/analyzer.py) 가 고정 키워드
  우선순위 스캔 → PM thesis 에 'overweight/underweight' 단어 (특히
  override note 의 pre-correction rating) 있으면 카드에 반대 등급 표시
  + 메모리(`parse_rating`)와 split-brain. Fix: canonical `rating.
  parse_rating` (라벨 우선) 로 라우팅, None-on-absent 보존.
- **BUG1 [High cost]** Gemini 캐시 이중청구 — RM/Trader/PM 가
  `cached_content` bind **+** 같은 instrument_context inline 주입
  (research_manager:76 / trader:142 / portfolio_manager:499). context
  2회 전송 → 캐시 없을 때보다 비쌈. Fix: `cache_active` flag, 캐시
  bind 성공 시 inline context "" 로 (캐시 prefix 로 전달). ~5-12% 절감.
- **F1 [High latency]** `build_instrument_context` 분석당 8회 (캐시
  seed + 분석가 4 + 결정 3) + 분석가 tool-round 재진입마다 재실행, 매번
  ~20-task prefetch fan-out. Fix: `(ticker, analyst_id, KST-date)`
  memoize (`_INSTRUMENT_CONTEXT_CACHE`, 256 cap) + `clear_instrument_
  caches()` 를 trading_graph._run_graph 시작 시 호출 (run 간 fresh +
  F7 intraday staleness 동시 해결: `_INSTRUMENT_INFO_CACHE` /
  `_PEER_MULTIPLES_CACHE` 도 run 시작 시 clear). screener 는 clear
  안 함 (24h 캐시 철학 + Phase-3 intra-run reuse 이득).
- **BUG3 [observability]** `usage_tracker` 가 `cached_content_token_
  count` 미추적 → 캐시 효과 검증 불가. Fix: `_extract_token_usage`
  4-tuple (cached 추가), `estimate_cost_usd(... cached_tokens=0)` 가
  cached 75% 할인 (Gemini 캐시 input ~25% 청구), log 에 non-zero 시
  `cached_tokens` emit. BUG1 fix 검증 가능케 함.

### ✅ 2차 — 완료 (견고성, 위험 낮음):
- **M3** structured-output 실패 fallback 의 원시 `.content` (멀티파트 list
  → downstream parse_rating AttributeError) → `structured.py:72` +
  `portfolio_manager.py:611` 모두 `_content_to_str` 경유. (free-text 의
  override-discipline 은 analyzer Fix F/G 가 backstop — 의도적 유지.)
- **M4** 토론/리스크 5노드 (bull/bear/aggressive/neutral/conservative) bare
  `llm.invoke` → 503 하나가 전체 graph crash (분석가 4 리포트 폐기). →
  `safe_invoke_text(llm, prompt, label)` 헬퍼 (agent_utils) 로 통일:
  try/except → 한국어 placeholder degrade + `_content_to_str` 정규화.
  advisory 노드라 1턴 실패해도 PM 이 분석가 리포트로 합성 가능.
- **F3** DART `get_recent_disclosures` / `get_insider_holdings` 캐시 0 →
  KR 분석당 ~16-21 중복 HTTP (429 위험). → `_disk_cache_daily` 데코레이터
  (`(stock_code, args, today)` key, 12h, **truthy-only** 캐시 — transient
  실패 미pin). `next_earnings_window` 는 순수 날짜 계산 (네트워크 0) 라
  캐시 불요 — agent 가 over-flag, 직접 확인 정정.
- **F5** AKShare inline 호출 (`_instrument_info` CN_A overlay +
  `_fetch_peer_multiples` fallback) timeout 0 → `_call_with_timeout(fn,
  15s, label)` 헬퍼로 bound (throwaway thread + cancel_futures). "stuck
  >15min" 클래스를 /ticker 경로에서도 차단.
- **F6** options chain (`get_options_signals`) 캐시·timeout 0 →
  `(ticker, today)` 12h 디스크 캐시 wrapper (`_compute_options_signals`
  분리), non-None 만 캐시. IV/PCR intraday drift 는 5일 horizon 허용.

### ✅ 3차 — 완료 (정확성 hygiene):
- **m7** `_extract_stance` bare-keyword fallback 이 ASCII 'buy/sell/hold'
  를 word-boundary 없이 rfind → household / buyback / seller / threshold
  / stronghold substring 오매칭. `_match_positions` 헬퍼: ASCII 키워드는
  `\bkw\b` regex, 한국어는 plain rfind (false-friend 는 이미 ○○ mask).
  smoke: household/buyback/seller → "" , 'strong buy'/'hold' → 정상.
- **m8** auto-resolve readiness gate in-graph(trading_graph) +7 vs
  background(auto_resolve) +3 불일치 → 같은 entry 가 다른 actual_days 로
  해소 가능. in-graph 를 +3 으로 통일 (auto_resolve 의 JPM rationale 따름).
- **m9** sector ETF <2 closes 시 entry 영구 skip → 양쪽 _fetch_returns
  copy 에 SPY fallback (benchmark thin + != SPY 시 SPY 재조회). raw return
  은 unitless ratio 라 alpha 산술 정상 (broad-market 벤치, sector 관련성만
  ↓). 무한 block 보다 우월.

### ✅ audit 3-tier 전체 완료 (commits 5e45caf / de900a1 / e8cbc91).

### 🔬 M2 — 측정 인프라 완료, 데이터 대기 (위험 중간이라 측정-우선):
M2 (PM override 이중 레이어 충돌) 는 버그가 아니라 **정책 결정** — 분석가
만장일치 Buy + Trader Hold 시 최종이 뭐여야 하는가. 추측 통합 = quality
회귀 위험 (Trader 신중함 상실 / free-text 백스톱 상실 / Hold-rate 급변).
그래서 데이터로 결정하는 측정 인프라부터 구축 (Phase 0+1, 위험 0):
- **충돌 구조**: in-graph `_enforce_pm_override_discipline` 는 분석가 다수로
  PM 정렬 (Buy/Sell) ↔ analyzer Fix G 는 Trader=Hold 시 Hold 강제. 둘 다
  발화 시 in-graph 정렬을 Fix G 가 되돌림 (sentinel `[PM override discipline
  자동 보정]` + Fix 발화 = 충돌 signature).
- **Phase 0** (analyzer `_log_pm_override_conflict`): 충돌 케이스를
  `~/.tradingagents/pm_override_audit.jsonl` 에 기록. **동작 무변경** —
  override 는 현재대로 적용, 기록만.
- **Phase 1** (`bot/pm_override_audit.py`): state log (full_states_log_*)
  replay → 충돌 set → 메모리 resolved 5d return join → policyA(분석가
  우선, sentinel 시 Fix G skip) vs legacy(현행) 의 mean P&L / hit-rate /
  Hold-rate 비교. read-only. VM 에서 `.venv/bin/python -m bot.pm_override_
  audit` 실행.
- **결정 gate** (CLAUDE.md): policyA 채택은 (a) mean P&L ↑ (b) hit-rate ↑
  (c) |Hold-rate Δ| ≤ 5pp **3개 모두 충족 시에만**. 미충족 또는 충돌 희소
  시 현행 유지 (over-engineering 방지).
- **다음**: VM 에서 backtest 실행 → 결과 보고 → 정책 확정 → (채택 시)
  feature flag 뒤 Phase 2 shadow + Phase 3 gated rollout.

### ✅ M2 — 종결: 통합 불필요 (2026-05-29 backtest 데이터 결정)
VM backtest 결과 (실데이터 107 evaluated runs):
```
M2 conflicts : 0 (0.0% of runs)
Hold-rate    : legacy 53.3% = policyA 53.3% (Δ +0.0pp)
```
**107 분석 중 M2 충돌 0건** — in-graph 가 PM 을 분석가 다수로 정렬한 뒤
analyzer Fix F/G 가 Hold 로 되돌린 케이스가 실데이터에 존재하지 않음.
이유: in-graph discipline 발화 자체가 드물고, 발화해 Buy/Sell 정렬 시
Trader 도 대개 동의 (같은 research plan 참조) → Fix G 미발화. **결정:
M2 통합 구현 안 함** — 이론적 충돌이 실측 비존재, 추측 통합은 복잡도 +
회귀 위험만 추가. 측정-우선 원칙이 정확히 검증됨 (blind 통합 회피).
- Phase 0 instrumentation (`_log_pm_override_conflict`) + Phase 1
  backtest (`bot/pm_override_audit.py`) 는 **forward tripwire 로 유지** —
  PM 프롬프트 / discipline 로직 변경 후 M2 충돌이 새로 생기면 jsonl 에
  자동 포착, 분기 재실행으로 재확인. 비용 0 (충돌 시에만 write).
- baseline 데이터포인트: Hold-rate 53.3% (향후 PM 분포 회귀 감지 기준).

### ✅ SV audit — 완료 (2026-05-29, 3-agent + 직접 정독, 3-tier 적용)
SV (`standardview/`) 는 **Gemini 2.5 Flash** (call_claude_cli 는 misnomer,
2026-05-19 패치). LLM 비용 작음 → 핵심은 correctness/reliability. 1~3차
적용 완료 (commits 아래). screener 별도 audit 불필요 (/ticker 인프라 상속).

**1차 — correctness/reliability (위험 0):**
- C-A: news-brief stub/mock 6h 캐시 → outage 시 빈 placeholder serve (3
  agent 모두 지목, 2026-05-21 midnight 버그 root cause). degraded 시
  `_mnb_cache_set` skip.
- C-B: latest.html/md 비원자적 write → half-write read. `_atomic_write`
  (temp+os.replace) 4 지점.
- C-C: busy-marker 를 watchdog 만 set → 스케줄 run 에 sv-update/watchdog
  blind (double-kick + 중간 redeploy). daily_generator.main() 가 marker
  touch+finally unlink (main 본체 → _main_impl 분리).
- timeout 무시: call_claude_cli(timeout=N) 이 genai 에 미전달 → worker
  thread .result(timeout) 로 enforce.
- B5: _log_sv_usage endpoint 라벨 (call_claude_cli endpoint 파라미터 +
  daily 고볼륨 4 호출처).

**2차 — 견고성 (위험 낮음):**
- C-D: pusher freshness gate (latest.html >6h stale 시 push skip).
- M1: `&amp;amp;` regex 가 hex `&#x;` + named entity 미커버 → `(#x[0-9A-Fa-f]+
  |#\d+|[A-Za-z][A-Za-z0-9]{1,31})` 확장 (2 지점).
- M2: chunker hard-cut 이 태그 중간 split → tag-close '>' fallback +
  fallback POST 실패 시 raise 대신 continue (batch tail 보존).
- date cache-key: news-brief cache_key 에 `kst_date` → flush 의존 제거.

**3차 — latency/cost (위험 낮음):**
- 매크로 지표 fetch 병렬화 (15×8s 순차 → ThreadPool 8).
- Naver KO 뉴스 병렬화 (5×10s 순차 → ThreadPool, map 순서 보존 dedup).
- `_pool` 6→12 (daily run 중 interactive starve 방지).
- news-brief force_refresh True→False (date-key + degraded-skip + 6h TTL
  로 안전, watchdog re-kick double-spend 회피; macro 는 intraday FX
  freshness 위해 True 유지).

**미적용 (의도)**: 산업 8→1 batch (cost 최대지만 JSON 1개 실패 시 8개
손실 = M2-analog 품질 tradeoff, Flash 라 절감<위험) · cosmetic m1-m4
(label regex 오타/중복 정의/HTTP 200/weekly silent). NewsAPI 100/day cap
+ UTC/KST 불일치는 별도 검토 (정책/키 영역).

### ⏸ 신중 (위험 중간, A/B 검증 권장):
- M2 PM override 이중 레이어 (in-graph 분석가 다수 보정 ↔ analyzer
  Fix-G Trader 불일치 Hold 강제) 상반 정책 → 단일 지점 통합 필요.

### ✅ 이미 최적화됨 (건드리지 말 것):
분석가 thinking_budget=0, per-analyst context slicing (~25-30%↓),
Bear-skip + PM light-LLM 만장일치 단축, output cap (deep/decision 16384
/ quick 2000), signal_processing LLM 호출 0 (deterministic parse_rating),
screener 2-Pro + 병렬 fetch + 24h 캐시, 분석가 캐시 bind 제거 (model-
mismatch no-op 였음), 병렬 prefetch + 디스크 캐시 TTL 일관.

## TODO

## 📋 Standard View open issues (2026-05-21 session pickup)

User 2026-05-21 새벽 1-12시 세션에서 발견 + 진단 + patch + 검증
완료. 다음 세션 pickup 항목 정리.

### ✅ 2026-05-21 완료 — SV major work CLOSED

- **A**: `&amp;` / `&quot;` 이중 escape (15회 → 0). daily_generator
  최종 HTML write 직전 post-process `re.sub(r'&amp;(amp|quot|...);'
  → r'&\1;')`.
- **E**: 산업 호출 ThreadPoolExecutor(max_workers=4) 병렬화 (7-10분
  → 4분 10초). + news-brief retry sleep 10s/20s → 3s/6s.
- **B**: en_articles fallback (0 → 10). NewsAPI/GDELT rate-limit 시
  ko_articles 의 외신 source 자동 분리. + backend wait_for 20s→60s,
  NEWSAPI_KEY ↔ NEWS_API_KEY fallback.
- **C**: brief 중복 시각 검증 — user 2026-05-21 12시 확인, 문제 없음.
- **D**: PAT 등록 + push 성공.
- **F**: Live → canonical mirror (156cd5d, 5add8d0).
- **G**: sv-update.timer 가 canonical 변경 시 1분 내 LIVE rsync
  → 별도 통합 installer 불필요.
- **TimeoutStartSec=1200**: daily/hourly service timeout 10→20분.
- **bullet/numbered/label 가독성**: pusher md_to_tg_html 가 markdown
  bullet (`* X`) / numbered (`1. X`) / label (`VC:`, `PE:`, `CFO:` 등)
  모두 빈 줄 separator 추가.

### 🟢 SV 잔여 항목 — 다음 자동 push 시 시각 확인만

- 16:00 KST hourly push 캡쳐로 새 가독성 포맷 확인. 필요 시 fine-tune
  은 다음 세션 optional task.
- ✅ E-3 완료 (2026-05-27): news-brief 호출을 main() 시작 시 background
  thread 로 submit → macro-snapshot fetch + DOM swap 와 병렬 실행, section 3
  에서 future.result() 로 join. 두 독립 느린 호출 (analyze + news-brief)
  중첩 → ~10초 단축. 출력 무변경, retry 경로 보존. `_fetch_brief()` 헬퍼
  로 초기 호출 + retry 중복 제거.

각 fix 는 universal (모든 brief 출력 / 모든 분석 / 모든 시장) 패턴
으로 적용. Per-ticker / per-market 가드는 부재.

## Bottleneck Screener — 운영 중 (Phase β + Wave 1 LIVE · 2026-05-29)

**내용 강화 (2026-05-29 사용자 요청 — A+C)**:
- **A. MANDATORY QUANT EXTRACTION** (Phase 4·5 prompt) — 각 candidate 행
  에 5개 정량 신호 의무 추출: (1) 컨센서스 PT %upside + analyst 수 +
  recommendationKey, (2) Next earnings ±5일 (⚡), (3) Quarterly YoY 가속/
  둔화 라벨, (4) Peer multiples 대비 valuation % 위치, (5) insider/옵션
  IV 등 위치성 신호. 누락 시 'N/A' 명시(=low-coverage 그 자체 신호) 또는
  reject. build_instrument_context 가 이미 끌어오는 데이터를 LLM 이 묻어
  두던 것을 명시 노출 — 추가 fetch 0, 추가 비용 ~₩0 (input token 소액).
- **C. Past-outcomes memory feedback** — `_format_past_outcomes_for_domain
  (domain)` (screener.py): 같은 도메인의 screener_memory.md resolved 항목
  에서 1m/3m/6m mean·hit-rate·α 통계 자동 추출 → Phase 4·5 prompt 헤더에
  주입. self-correcting (LNG layer 6개월 +22% / hit 100% → 같은 layer
  재추천 강화, S-tier micro-cap 1m -15% → 신중). 표본 <2 시 빈 문자열
  (noise 회피). 통계 smoke 100% 정확.

**Outcome 측정 horizon (사용자 정책 2026-05-29)**: screener Top-3 picks 의
5/15/30d (trading days) outcome 컬럼은 **1개월/3개월/6개월 (캘린더)** 로
변경. screener 는 6-18M thesis 라 NOAH /ticker 5거래일과 별개 horizon.
`bot/auto_resolve.py:_fetch_returns_calendar(ticker, trade_date, calendar_
days)` — target = trade_date + N캘린더일, yfinance 가 영업일만 반환하므로
target 이 weekend/holiday 면 자동으로 **다음 영업일 close** 가 사용된다.
windows: pass1 = 30(1m) · pass2 = (90,180) = (3m,6m). gate +3 buffer.
대시보드: `<th>1개월</th><th>3개월</th><th>6개월</th>` + "1m resolved" 통계.
NOAH /ticker 의 `_fetch_returns` 는 5거래일 그대로 유지 (정책 분리).


**현재 가동 상태 (변경 시 본 섹션 즉시 업데이트 의무 — 사용자 정책
2026-05-29):**

- `/screener` (= `/screener bottleneck` 디폴트) 텔레그램 명령 LIVE.
  Theme registry 패키지 `bot/screener_themes/` 분리, **10 도메인 동시
  운영** (Wave 1 trend 5 + Wave 2-A Finviz sector 5). Python dict 기반.
  각 모듈은 top-level `THEME` dict export. Registry `__init__.py` 가
  `pkgutil.iter_modules` 로 자동 discover + import-time validate. 새
  도메인 추가 = 새 모듈 1 파일 drop, orchestrator 수정 0.

  **3-layer 도메인 모델** (2026-05-29 사용자 정식 분류 = 미국 GICS-like,
  Finviz 폐기): 각 모듈 THEME dict 에 `layer="L1_TREND"|"L2_SECTOR"|
  "L3_INDUSTRY"` 필드 명시. registry `list_domains()` 가 layer 포함
  반환. 대시보드 페이지 + `/screener_list` Telegram 출력 모두 layer
  별 그룹핑 (📈 L1 / 🏢 L2 / 🔬 L3).

  **L1 Trend themes (8, 좁은 cycle 베팅)**:
  - `bottleneck.py` — AI Data Center Buildout (별칭 ai · 데이터센터)
  - `ev.py` — EV & Battery (별칭 전기차 · 배터리 · 이차전지)
  - `defense.py` — Defense, Aerospace & Space (별칭 방산 · 우주)
  - `pharma.py` — Biotech & Pharma · GLP-1/CDMO/Biosimilar
  - `solar.py` — Solar, Wind, ESS & Grid (별칭 신재생 · 태양광)
  - `robot.py` — Robotics & Humanoid Buildout (별칭 로봇 · 휴머노이드 ·
    협동로봇 · 자동화 · optimus · 감속기)
  - `quantum.py` — Quantum Computing (별칭 양자 · 큐비트 · pqc). GICS
    check 2026-05-29 식별 → 사용자 add 결정 후 ship.
  - `carbon_capture.py` — Carbon Capture, Utilization & Storage (CCUS)
    (별칭 탄소포집 · ccs · dac · 블루수소). GICS check 2026-05-29 식별.

  **L2 Sector themes (11, 미국 GICS-like 공식 분류)** — domain 표기는
  "Industrials (산업재)" 형태 (영문 정식 + 한국어 보조). binding_layer_
  taxonomy 는 각 sector 의 L3 sub-industry 정식 명칭. 각 모듈 ~150-
  200 lines, regional_concentration 은 cross-market (US/KR/JP/TW/HK/CN/
  EU) 종목 분포 명시.
  - `industrials.py` — Industrials. L3 8: Aerospace & Defense / Airlines /
    Building Products / Electrical Equipment / Commercial & Professional
    Services / Machinery / Transportation & Logistics / Waste &
    Environmental Services
  - `healthcare.py` — Health Care. L3 3: Pharma & Biotech / Health Care
    Equipment & Supplies / Health Care Providers & Services
  - `financial.py` — Financials. L3 6: Banks / Capital Markets &
    Investment / Consumer Finance / Insurance / BDCs / Digital Assets &
    Cryptocurrency
  - `energy.py` — Energy. L3 2: Oil/Gas/Consumable Fuels / Energy
    Equipment & Services
  - `technology.py` — Technology. L3 4: Software / Hardware & Equipment /
    Semiconductors & Equipment / IT Services & Fintech
  - `basic_materials.py` — Basic Materials. L3 5: Chemicals / Construction
    Materials / Containers & Packaging / Metals & Mining / Forest & Paper
  - `communication.py` — Communication Services. L3 4: Interactive Media
    & Services / Entertainment / Gaming / Telecommunication Services
  - `consumer_cyclical.py` — Consumer Discretionary. L3 6: Automotive /
    Apparel Luxury / Hospitality & Leisure / Retail / Homebuilding /
    Education Services
  - `consumer_defensive.py` — Consumer Staples. L3 5: Beverages / Food
    Retailing / Food Products / Household & Personal / Tobacco
  - `real_estate.py` — Real Estate. L3 2: Real Estate Services / REITs
  - `utilities.py` — Utilities. L3 3: Electric & Multi-Utilities /
    Independent Power & Renewable / Gas & Water

  L1 ↔ L2 중복 허용 정책: `/screener defense` (L1 재무장 cycle niche) 와
  `/screener industrials` (L2 산업재 sector 전체) 양쪽 동작. 사용자가
  의도에 맞는 lens 선택. `/screener pharma` (L1 GLP-1/CDMO cycle) vs
  `/screener healthcare` (L2 헬스케어 전체) 도 동일 패턴.

  registry `_VALID_LAYERS = ("L1_TREND", "L2_SECTOR", "L3_INDUSTRY")`.
  Layer 누락 → default `L1_TREND` (back-compat). `_validate()` 가 layer
  값 + binding_layer_taxonomy/regional_concentration 최소 2개 (Energy/
  Real Estate L2 가 공식 L3 2개씩) 체크.

  **L3 Industry themes (48, 사용자 정식 분류 sub-industry 전체)** —
  Phase B 2026-05-29 ✅ 한 batch 로 전체 ship. 각 L3 모듈 ~100-150 줄,
  binding_layer_taxonomy = 좁고 깊은 niche sub-categories (L4 가 없으
  므로 L3 안에서 catalyst + regional 로 cover). slug 는 sector prefix
  없이 unique (`/screener_aerospace_defense`, `/screener_banks` 등 — 다른
  module 의 alias 와 conflict 시 slug self-mapping 우선).
  - Industrials (8 L3): aerospace_defense / airlines / building_products /
    electrical_equipment / commercial_services / machinery /
    transport_logistics / waste_management
  - Health Care (3): pharma_biotech / medical_equipment / healthcare_providers
  - Financials (6): banks / capital_markets / consumer_finance /
    insurance / bdc / digital_assets
  - Consumer Discretionary (6): automotive / apparel_luxury / hospitality
    / retail / homebuilding / education_services
  - Consumer Staples (5): beverages / food_retailing / food_products /
    household_personal / tobacco
  - Energy (2): oil_gas / energy_services
  - Basic Materials (5): chemicals / construction_materials / packaging /
    metals_mining / forest_paper
  - Real Estate (2): real_estate_services / reits
  - Utilities (3): electric_utility / ipp_renewable / gas_water
  - Communication Services (4): interactive_media / entertainment /
    gaming / telecom
  - Technology (4): software / hardware_storage / semiconductors /
    it_fintech

  대시보드 페이지 + Telegram `/screener_list` 모두 L1/L2/L3 layer 별
  그룹핑. 변경 이력은 footer 한 줄 (`📜 최근 변경 (ts) — 추가 ... → 총
  N개`) — 사용자 요청 "변경이력으로 남기지 말고 그냥 3-layer 기준" 반영.
- `/screener_cost` 텔레그램 명령 LIVE — 별도 비용 카드 (sv_cost 패턴).
- Phase β orchestration: Pro Phase 1·2 (JSON 후보) → ticker 검증 +
  yfinance mcap-based tier 강제 → Phase 3 build_instrument_context
  병렬 (120s hard timeout, hung-thread protection) → Pro Phase 4·5
  (web search grounding + 한국어 출력) → Top-3 JSON tail 추출.
- 트래킹 인프라:
  - 비용 → `~/.tradingagents/screener_usage.jsonl` (screener-specific) +
    `~/.tradingagents/usage.jsonl` (NOAH 통합, subsystem='screener').
    `/usage` 가 전체 surface(분석+Screener+Daily Byte+청약+부동산+블로그+
    SV+한국수출입) 합산 + subsystem 분포 표시.
  - 아카이브 → `~/.tradingagents/screener_archive/YYYY-MM-DD/HHMMSS_
    {slug}.json` (raw_output + binding_constraint + top3_section +
    bottom_line 섹션 분리 저장).
  - 메모리 로그 → `~/.tradingagents/memory/screener_memory.md` (NOAH
    `_TAG_RE` 호환 포맷, `auto_resolve.py` 가 5d→15d→30d outcome 자동
    채움).
  - 대시보드 → NOAH archive 의 `screener.html` (link in main index).
    각 run 카드: 도메인 헤더 (Wave 1 확장 시 EV/방산/바이오 등으로
    자동 교체) + 분석 collapsible (binding/Top-3/bottom_line) + Top-3
    mini-table (5/15/30d + α vs sector) + 🗑️.
  - 휴지통 → `/api/screener_delete` (date+filename POST, filename
    regex `^\d{6}_[a-zA-Z0-9_]{1,60}\.json$` path traversal guard).
- 가드:
  - TENSE DISCIPLINE (오늘 < 이전 사건 "전망" 금지)
  - CORP ACTION 환각 차단 (yfinance EPS/PER 누락 → "기업 분할 의심"
    소설 금지)
  - VALUATION DISTORTION (PER>100x cyclical bottom + PER N/M 적자 →
    PBR/Fwd EV/EBITDA 대체 지표 + 턴어라운드 stage 명시 의무)
  - Wildcard 금지 (날짜 `2026-01-_` → quarter `2026-Q1`)
  - DATA INTEGRITY (yfinance company_name mismatch 종목 OMIT)
  - Ticker scope 격리 (최종 본문 기준 reject 표시)
  - Ticker noise reject (2026-05-29 EV + defense review): `150M` /
    `800V` / `2026-H2` / `EBITDA` / `FEC` / `DLE` / `LFP` / `NCM` /
    `OLED` / `BMS` / `GLP` / `CRO` / `CDMO` / `AESA` / `AUKUS` /
    `ICBM` / `K9` / `K2` / `THAAD` / `SRM` / `HIMARS` / `JDAM` /
    `NGAD` / `LRHW` / `HACM` / `CoWoS` / `HBM3` / `HBM4` 등 도메인
    약어 + 숫자-LED 토큰 blacklist + regex 룰로 차단
  - TICKERS_USED_JSON tail (2026-05-29 defense review #5, 구조적
    해결): Phase 4·5 출력 끝에 Pro 가 본문 cite ticker 만 JSON 배열로
    별도 선언 → 검증 단의 primary source. regex 추출은 cross-check
    만 사용 (mismatch INFO 로그). Wave 2 도메인 추가 시도 blacklist
    의존 없이 noise 0 자동 유지.
  - TIER 분포 nudge + monitoring (2026-05-29 defense review #6 +
    pharma review 후속): Phase 1·2 프롬프트에 layer 별 S/M/L 모두 시도
    + 구조적 부재 layer 는 'tier_unavailable_reason' 명시 의무. Post-
    Pro 통계 2개 로그 — (a) 도메인 전체 `screener tier distribution
    [domain]: L=N M=N S=N` INFO, (b) per-theme `screener theme tier
    skew [domain / theme_name]: L=N M=N S=N — missing X` WARNING (2+
    행 있는 theme 에 한해). Pharma 2026-05-29 review 가 도메인 전체
    L=4/M=2/S=3 으로 OK 보였지만 'CDMO 탈중국' theme 만 보면 L=2/M=0
    /S=1 violation 캐치 못 한 것 fix. Block 안 함 — defense / 우라늄
    같이 industry-wide micro-cap 부재 케이스 차단 안 됨.
  - TENSE DISCIPLINE 강화 (2026-05-29 pharma review): 외부 source 의
    'YYYY 완공 목표' / 'YYYY 가동 개시' / 'YYYY 출시 예정' prose 중
    YYYY < 오늘 (target date 이미 지남) 인 경우 실제 가동 상태 web
    search 재검증 의무. 검증 실패 시 OMIT 또는 'YYYY 목표였으나 미확정'
    재작성. 'sourced 2026-05-29' source 가 fresh 라고 안전한 게 아님 —
    source 의 prose 가 stale target date 그대로 cite 한 경우 (300037.SZ
    Tinci '2025 완공 목표 건설 중' surfaced) blocked.
  - 도메인 변경 자동 기록 (사용자 정책 2026-05-29): `bot/screener_
    history.py` 가 매 `regenerate_screener_index()` 호출 시 registry
    snapshot 비교 → 차이 시 `~/.tradingagents/screener_domain_history.
    jsonl` append. `archive/screener_domains.html` 페이지 하단 '📜 변경
    이력' 섹션에 chronological 표시. 사용자가 help 에서 /screener_list
    클릭 → 페이지 진입 → 어느 도메인이 언제 추가됐는지 한눈에 확인.
  - `/screener_<slug>` 단일-탭 명령 (사용자 ref `/find_all`·`/papers_
    guide` 패턴 2026-05-29): `_register_dynamic_screener_handlers` 가
    boot 시 registered 도메인 모두에 대해 `screener_<slug>` 명령
    핸들러 등록 + `set_my_commands` 호출로 텔레그램 BotFather 측에도
    같은 list 등록 → 모든 클라이언트 (특히 mobile) 에서 messages body
    안의 `/screener_<slug>` 자동 hyperlink + autocomplete + menu 노출
    보장. 정적 9 (`/start /help /usage /sv_cost /screener_cost /screener
    _list /sites /screener /compare`) + 동적 N (도메인 수) = 자동.
    Telegram cap 100/scope — Wave 3 ~30 도메인까지 안전. 새 도메인
    추가 시 봇 재시작 (auto-deploy 1분) 후 텔레그램 측 명령 list 자동
    갱신. ⚠️ set_my_commands 호출 누락 시 mobile 클라이언트에서 모든
    `/cmd` 가 plain text 가 됨 (사용자 ref 2026-05-29 mobile UX 확인).
  - Liquidity 경고 (S-tier ~$100M micro-cap)
  - TIER 강제 (2026-05-29 EV review + 2026-05-31 Machinery review): Pro
    의 자체 분류를 Python mcap 기반 결과로 자동 치환 (Master Table 행 +
    Top-3 picks parenthetical + TOP_3_JSON tail). AMPX 등 모니터링 로그.
    **Machinery review 2개 보강**: (1) `_MT_TIER_ROW_RE` 에 본문 bullet
    '• X · TICKER' 형식 추가 — bullet 뒤는 '·' 아닌 공백이라 기존 bracket
    -only regex 가 058610.KQ(에스피지, $1.9B=M 인데 S 로 출력) prose 행을
    놓침. (2) `_override_tiers_from_mcap` 가 KR(.KS/.KQ) yfinance mcap
    None 시 FSC `latest_price.mrktTotAmt`(원→USD) fallback — KOSDAQ mcap
    누락으로 override 자체 미작동하던 케이스 해소.
  - 메타-코멘터리(핑계) strip (2026-05-31 Machinery review): `_strip_meta_
    commentary` — '데이터 미수집'/'현재가 확인 필요'/'데이터 깊이 부족'/
    '정량 데이터 추가 확인 필요' 등 데이터 부재 변명을 'N/A' 로 치환(정성
    catalyst 서술·web verify 권고는 보존). IFX.DE/SKF-B.ST/6472.T 에서
    남발 surfaced. 프롬프트 § 회피성 문구 금지 강화 + Python backstop.
  - CN A-share multiples 폴백 (2026-05-29 EV review): yfinance PER/PBR/
    PSR None 시 AKShare `stock_a_indicator_lg` 의 latest row 로 자동
    overlay (`_instrument_info` 단일 지점, downstream 전체 혜택)
  - Markdown 금지 (`**`, `*`, `##` literal noise 차단 — HTML 만)

**✅ 자유텍스트 도메인 — 완료 (2026-05-29, commits `1a0d15c` + `37138cd`):**
- `/screener <자유어>` — alias miss 자동 fallback, Pro Phase 0 가 30초
  내 theme dict 생성 (~₩50-80, google_search grounding).
- `bot/screener_freetext.py` 단일 모듈. fuzzy redirect (token coverage
  ≥0.7) → existing 도메인 자동 라우팅. 24h disk cache
  (`~/.tradingagents/freetext_themes/{sha256[:12]}.json`).
- REJECT 룰 — "주식 추천" / "좋은 종목" 같이 vague 입력은 Pro 가
  reject reason 반환.
- Daily soft cap 5회 — 초과 시 ⚠️ 비용 reminder (블록 X).
- Audit log `~/.tradingagents/freetext_audit.jsonl` — input · cache_key
  · cost · outcome.
- **자동 promotion** (`promote_to_module()`): 같은 자유어 5회+ 사용 시
  `bot/screener_themes/<slug>.py` 정식 모듈 자동 생성. Slug 우선순위:
  (1) ASCII alias → (2) domain ASCII 조각 → (3) `freetext_<hash[:8]>`.
  layer=AD_HOC 보존 (사용자 수동 reclassify). 다음 봇 재시작 후 registry
  자동 픽업 → /screener <slug> 정적 라우팅. Idempotent (os.path.exists
  guard).
- Telegram set_my_commands cap 100/scope — 현재 65 도메인 + 9 정적 +
  자유어 (dynamic 등록 안 됨, set_my_commands 노출 X) = 안전.

**✅ 24h 디스크 캐시 — 완료 (2026-05-29, commit `b953de2`):**
- `bot/screener_cache.py` 단일 모듈. ScreenerResult 를 `~/.tradingagents/
  screener_cache/{slug}_YYYY-MM-DD.json` 로 저장. KST 자정 만료.
- Cache key = canonical slug (resolve_slug 가 alias 통일 → `/screener
  AI 데이터센터` + `/screener bottleneck` 같은 cache 행 collapse).
  자유어는 freetext cache_key (sha256[:12]). 정적 + 자유어 모두 동일
  cache 인프라.
- `/screener {slug} fresh` flag = 캐시 무시 + 강제 재실행. case-
  insensitive, alias 우선.
- Header '💾 오늘 캐시' 표시 (hit 시). cost_krw / elapsed_sec 은 원본
  (첫 실행값) 그대로 보존.
- Audit `~/.tradingagents/screener_cache_audit.jsonl` (event='cache_
  hit' / 'cache_save').
- 보안: _SAFE_KEY_RE (`^[a-zA-Z0-9_-]{1,80}$`) path traversal 차단.

**다음 작업 — Sanity Check 만 남음:**
- 65 도메인 (6 L1 + 11 L2 + 48 L3) Sanity check — 각 L3 모듈을
  실제 `/screener_<slug>` 호출로 binding constraint quality + tier 분포
  + region coverage 검증. 우선순위 / 결함 발견 시 fix batch. 24h
  캐시 덕에 같은 도메인 반복 호출 시 ₩0 — sanity check 비용 절감.
- ~~AD_HOC layer 대시보드 display~~ ✅ 완료 (commit `2640c00`)
- ~~정적 도메인 24h 캐시~~ ✅ 완료 (commit `b953de2`)

## Bottleneck Screener — 설계 메모리 (origin 2026-05-28, kept for reference)

다종목 idea 발굴 모듈. 기존 NOAH `/ticker` 는 사용자 지정 단일 종목
deep dive — screener 는 그 funnel 의 top: **테마 → 후보 종목군 발굴**.
사용자 vision: AI bottleneck 도메인부터 시작해 **궁극적으로 전 산업
커버** (EV / 방산 / 바이오 / 신재생 / 럭셔리 / rare earth / 우라늄 ...
무제한 확장). 별도 명령·별도 파이프라인 — NOAH 메인 분석과 분리.

### 핵심 철학 (원본: 2026-05-28 Singularity Research 'ruthless bottleneck')

1. **Ruthless buy-side analyst persona** — 충성심·내러티브 무시, 수익만.
2. **Theory of Constraints (TOC)** — 공급망 한 단계가 binding, choke
   point owner 가 rent 독식. 모든 산업에 보편적 (어휘만 다름).
3. **Rerate focus** — 단순 earnings beat 가 아니라 multiple expansion;
   제약 해소 시 동일 폭의 de-rate 위험 동시 인지.
4. **Niche 2-3 layers down** — GPU·전력 같은 1차 헤드라인 X. Substrates
   / quick disconnects / vapor chambers / TIMs / busbars / 특수가스 /
   test-burn-in 같은 sub-layer.
5. **Global mandate** — US 편향 금지. KR/JP/TW + EU(독일·프랑스·스위스·
   네덜란드·북유럽) + CN A/H 적극 포함. 진짜 pure-play 들이 offshore.
6. **3티어 size** — ~$100M micro (가장 pure) / ~$1B mid / ~$10B 유동성.
   USD 환산 후 분류. clean public name 없으면 "no clean public name".

### 신호 무게 결정 (사용자 통찰 2026-05-28 — lagging 재무는 sanity only)

**Bottleneck rerating 은 forward signal 이 결정**. 재무제표는 lagging.
점수 배분:

- **Tier A — Catalyst 신호 (50%)**: 신제품·신기술 발표 (30-90일) /
  신규 고객·계약·qual 통과 / 경쟁사 stumble (recall·지연·미스) /
  정책 변경 (IRA·BIS·보조금·CBAM·관세) / 캐파 확장 + online date /
  sell-side PT·rating 변경 (30일 momentum).
- **Tier B — 실적 발표 Content (25%)**: forward guidance / 본인의
  constraint 인용 ("limited by X" → 한 단계 아래 진짜 수익자 표지) /
  backlog QoQ + RPO / 가격 인상 + 효력 시점 / utilization tightness.
  (= RULE 15 EARNINGS-CALL CONSTRAINT EXTRACTION 의 6개 신호 모두 활용)
- **Tier C — 시황 펄스 (15%)**: 30/90일 sector 상대 강도 / 옵션 IV
  (이벤트 임박) / 외인·기관·港股통 flow / 단기 모멘텀 / short interest.
- **Tier D — 재무 sanity (10%)**: 시총·PER·PSR (밸류에이션 baseline,
  "이미 priced in" 평가용) / 매출 YoY 가속·둔화 / 적자기업 의도된 투자
  vs 무너지는 모델 구분만.

→ Tier D 는 dominant 신호 아님. screener 출력 상단에 반드시 명시.

### 데이터 인프라 — 이미 모두 wired (재사용)

| Forward Signal | 데이터 소스 (구축 완료) |
|---|---|
| 신제품·M&A·material events | EDGAR 8-K (US, 2026-05-28 신규) / DART / EDINET / MOPS / AKShare 公告 |
| 회사별 뉴스·계약·가격 인상 | yfinance .news / Naver / Kabutan / cnyes / AKShare 东方财富 / NewsAPI / GDELT |
| 정책 변경 | RULE 12·13·14 의 산업별 dominant 변수 (fundamentals_analyst 내) |
| 캐파 확장 + 실적 transcript | 위 공시 + build_instrument_context 의 실적 윈도 |
| Sell-side revisions | 컨센서스 (yfinance + FnGuide + Kabutan + cnyes_consensus) — 30일 비교 추가 필요 |
| 시황 펄스 | sector_strength + risk_metrics + options_signals (US) + KRX flow (KR) |

→ screener 는 fetch 인프라 거의 새로 짤 게 없음. 후보 N개에 대해
`build_instrument_context` 병렬 호출하는 게 핵심.

### 6단계 Method (재정렬, 사용자 통찰 반영)

1. **Map the Stack** — 도메인 의존성 chain (hyperscaler → 말단 공급사),
   SPOF / 지리적 집중도 마킹.
2. **Locate Choke Point** — 공급자 집중도 + qual 시간 + switching cost
   점수화 (구조 점수, Tier 외 별도 15%).
3. **Extract FORWARD Signals** — Tier A·B·C 메인 비중. instrument
   context 병렬 호출 + 30일 sell-side delta 추가.
4. **Sanity-check Financials** — Tier D. 적자기업 의도된 투자 vs 망함만
   구분.
5. **Score "What's priced in"** — 현재 multiple vs 자기 5년 + 컨센
   PT 갭 + 30일 PT 변화 (= edge 측정).
6. **Catalyst + Kill Trigger** — 시기 + 선행지표 + 디레이팅 트리거
   (kill_trigger 필드, 2026-05-28 schemas.py 도입).

### 5-Phase Orchestrator (실행 흐름)

```
사용자: /screener bottleneck (또는 /screener <freetext>)
        ↓
Phase 1·2 (Gemini Pro + web search) — Theme & Candidate Discovery
        - 도메인 binding layer 4-6개 식별 (Map the Stack)
        - 각 layer × 3 size tier 후보 ticker 후보군 (Global)
        - 모든 ticker yfinance fetch 검증 — 가짜 ticker 즉시 reject
        ↓
Phase 3 (Flash, 병렬) — Forward Signal Extraction
        - 후보 종목별 build_instrument_context 호출
        - Tier A/B/C/D 신호 분리 추출
        - RULE 15 6개 신호 모두 추출
        ↓
Phase 4 (Pro) — Scoring
        - 5축: constraint severity / durability / supplier concentration
               / revenue exposure / what's priced in
        - 0-10 점, theme 내 + 전체 랭킹
        ↓
Phase 5 (Pro) — Master Table + Top-3 Narrative
        - master table (theme × tier × ticker × Tier A/B/C/D summary)
        - top-3 conviction + 접근 경로 (ADR/local/illiquid)
        - one-line bottom line
        - 출력 추론/팩트 분리 ("source" vs "inferred" 컬럼/태그)
        - disclaimer (6-18개월 thesis, 5거래일 트레이드 아님)
```

### Theme Registry (전 산업 확장의 핵심)

`bot/screener_themes/` 디렉토리에 산업별 YAML/JSON config.
공통 스키마:

```yaml
domain: "<도메인명>"
binding_layer_taxonomy:        # 4-6 layers
  - <layer 1>
  - <layer 2>
catalyst_types:                # 도메인 특정 catalyst
  - <catalyst kind>
data_sources:
  earnings: [<리딩 종목 list>]
  industry_reports: [<출처 list>]
regional_concentration:        # SPOF 지리적
  <layer>: <region/회사>
horizon: "<6-18 months 등>"
```

확장 로드맵:

| Wave | 도메인 | 우선순위 근거 |
|---|---|---|
| MVP α | AI Data Center | 원 프롬프트 검증 + 데이터 풍부 |
| Wave 1 | EV/배터리 · 신재생(solar/wind) · 방산/우주 | 데이터 풍부, regional concentration 명확 |
| Wave 2 | 바이오 (GLP-1/CDMO/CRO) · 헬스케어 · 진단 | 임상 단계 binary catalyst |
| Wave 3 | 럭셔리/소비재 cycle · 핀테크/결제망 · rare earth · 우라늄 · 농업 | 데이터 sparser, thematic gap 큼 |
| Wave ∞ | `/screener <freetext>` 자유 입력 — on-the-fly registry 생성 | 무제한 확장 |

### 비용·시간·아카이브

- 1회 실행 예상 비용: **~$0.25 (~₩330)** — Phase 1·4·5 Pro + Phase 3
  Flash 병렬 (~15종목)
- 소요 시간: **~3-5분** (Phase 3 병렬 + Pro 순차)
- 캐시: 같은 날 같은 도메인 24h TTL (`~/.tradingagents/screener_cache/`)
- 아카이브: dashboard 에 영구 저장 + URL link → 추천 추적·accuracy 측정
  - Wave β 이후: 분기마다 screener 추천 종목 6-18개월 후 성과 평가
    (NOAH 5거래일 평가와 별도 horizon 트랙)

### 위험·완화

| 위험 | 완화 |
|---|---|
| 환각 (가짜 ticker / catalyst) | 모든 ticker yfinance fetch 검증 필수, 실패 시 "no clean public name". 출처 link / publisher 필수 (RULE 15 + F7 NEWS FABRICATION 가드 재활용). |
| 5거래일 horizon 미스매치 | 매 출력에 disclaimer — "6-18개월 thesis, 5거래일 트레이드 아님". /TICKER deep dive 권유. |
| 데이터 sparse 도메인 (rare earth 등) | 도메인별 confidence score. Sparse 시 "데이터 한계, 추론 기반" 명시. |
| 비용 scaling | 도메인별 24h 캐시 + 사용자 명령 시만 trigger. 월 1-2회/도메인 가정 ₩30-50K. |
| PM Override Discipline | screener 는 idea generation, 최종 verdict 아님 — override 발화 안 함. /TICKER follow-up 시에만. |
| 법적 면책 | 매 출력에 "교육 목적 / 추천 아님" disclaimer 필수. |

### Phase α MVP 구현 (다음 세션 착수)

신규 파일:
- `bot/screener.py` — 5-phase orchestrator
- `bot/screener_themes.py` (또는 `screener_themes/*.yaml`) — registry
- `bot/screener_score.py` — 5축 scoring

기존 파일 수정:
- `bot/telegram_bot.py` — `cmd_screener` + `/screener` handler 등록 +
  `_HELP_TEXT` 12 섹션에 "screener Phase α 진행 중"
- `bot/dashboard.py` — screener archive 카드 + URL endpoint

MVP 범위:
- `/screener` (= `/screener bottleneck` 디폴트) AI Data Center 도메인만
- 5-phase 전체 작동, Tier A/B/C/D 정렬, master table 출력
- 텔레그램 분할 + dashboard 아카이브
- 다른 도메인은 Wave 1 별도 commit

### 보존 핵심 결정 (사용자 합의 2026-05-28)

1. ✅ 도메인 자유입력 (`/screener <freetext>`) — Wave ∞
2. ✅ 글로벌 web search 활용 (Option B) — Pro 에게 위임
3. ✅ Dashboard 아카이브 영구 저장 (Option B)
4. ✅ 24h 캐시 (Option A)
5. ✅ NOAH /ticker 연계 deep link (Option A)
6. ✅ Phase 3 Flash 병렬 / 나머지 Pro (Option B)

이 6개 + forward signal weighting + 6-step method 재정렬은 사용자
확인됨 — Phase α 구현 시 그대로 따를 것.

## 🔐 API-blocked tasks (deferred to final batch per user 2026-05-19)

User policy: tasks that require new external API keys / registration
are parked here and addressed AT THE END of all other infra work.
Reason: API registration often blocks (geofence / account approval
/ payment) and shouldn't gate the rest of the development. Each task
keeps a clear pickup state so the final batch is easy to resume.

These need new credentials BEFORE work can ship:

- **KRX Data Marketplace 로그인 (KRX_ID / KRX_PW) — ✅ loaded (2026-05-29)**.
  User registered + `.env` 에 KRX_ID/KRX_PW 추가, 검증 run 에서 `KRX 로그인
  완료` 확인. Daily Byte + main /ticker KR pykrx 수급 모두 LIVE. 이하 배경은
  기록 보존용. ⚠️ 노출된 KRX 비밀번호는 회전 권고 (채팅 노출).
  surfaced 2026-05-29 (Daily Byte 검증). KRX 가 **2025-12-27 부터**
  데이터 포털을 회원제 'KRX Data Marketplace' 로 전환하며 **로그인을
  필수**로 만들었다 (AI 봇 무단 수집 차단 목적; 데이터 조회는 여전히
  무료, Naver/Kakao 소셜 로그인 가능). pykrx (≥1.2.8, requirements 핀
  상향 완료) 는 `KRX_ID`/`KRX_PW` 환경변수로 인증한다.
  **영향 범위 (universal)**: pykrx 를 쓰는 **모든** 경로가 creds 없으면
  dormant — (a) Daily Byte 일일 수급 브리프 (`bot/daily_kr_flow.py`,
  현재 19:00 timer 가 매일 fire 하지만 creds 없어 graceful skip), (b)
  main `/ticker` 의 KR 수급/시총/52주/베타/외인지분/공매도 fallback
  (`bot/pykrx_client.py`) — SK Hynix 2026-05-29 리뷰의 'pykrx flow
  데이터 미수집' 이 바로 이것. KIS API 가 per-ticker 는 메꿔주지만
  시장 전체 종목 랭킹(Daily Byte)은 pykrx 가 유일.
  **코드는 ready**: `krx_login_ready()` gate (creds 미설정 시 1회 경고
  + None) + `_quiet_pykrx_logging()` (pykrx 내부 logging.info 버그 도배
  차단) shipped — creds 가 `.env` 에 들어오는 즉시 두 경로 자동 작동,
  코드 변경 0.
  Required: KRX Data Marketplace (data.krx.co.kr) 무료 가입 →
  `.env` 에 `KRX_ID` + `KRX_PW` 추가. 작업량 0 (코드 완료, 등록만).

- **KIS Open API (한국투자증권)** — KR 외인 지분 한도 / 신용잔고 /
  대차잔고 / 프로그램 매매 / 시장경보 종목 분류. KR 시장의 가장 큰
  비어있는 단기 수급 영역 — 5거래일 horizon 가격 동인의 핵심.
  Required: 한국투자증권 계좌 + KIS Developers portal 가입 →
  `KIS_APP_KEY` + `KIS_APP_SECRET` 발급 → `.env` 에 추가.
  Estimated work after keys arrive: 1.5일 (`bot/kis_client.py` +
  agent_utils 주입 + RULE 10 dominant 변수 보강).
  Blocks: B3 (외인 한도 RULE 10 변수). Recommend kicking off
  registration in parallel with non-API work.

- **EDINET API key — ✅ loaded (2026-05-27)**. User registered via
  ProtonVPN Japan paid tier (one-day refund path). Key format: 32-char
  hex UUID without dashes. Stored as `EDINET_API_KEY` in `~/stock/.env`.
  JP Phase 3 validation started: 8306.T (MUFG) confirmed EDINET 공시
  블록 실데이터 출력 (대량보유보고서 5/18·5/8 등). 사전 announcement
  scan + 5%+ 대량보유 변동 + next-earnings-window 전부 live.
  Remaining: /7203.T (Toyota) + /6758.T (Sony) validation runs.

- **FRED API key — loaded** (2026-05-18). User confirmed key is in
  `.env` (verified via redacted `cat ~/stock/.env | sed 's/=.*$/=***REDACTED***/'`
  output during MediaTek review session). JP macro block (BoJ 정책금리
  + JGB 10Y + JP CPI), TW macro (CBC 重貼現率 + TW 10Y + TW CPI), and
  the FRED slot of CN macro pathways all active. Single key drives all
  three markets. Resolved; preserved here as a status marker only — no
  further action needed.

- **Phase 4-CN-D validation cycle — ✅ 완료** (2026-05-21). fix#1~#11 (commits 91427a9~925f926), CN/HK + TW/JP/KR 크로스-마켓 검증 완료.
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

- **Gemini Context Caching** — ✅ SHIPPED 2026-05-21 (commit 6ac0041),
  ⚠️ CORRECTED 2026-05-26. Cache 는 `model="gemini-2.5-pro"` 로 생성되며
  (gemini_cache_manager.py / trading_graph.py maybe_create_cache) **오직
  decision-tier (research_manager / trader / portfolio_manager, Pro) 에서만
  실제 작동** — live probe 2026-05-26 로 Pro 호출이 ~12K 토큰 cache hit
  확인. 분석가 4명은 Flash 라 Pro 캐시 바인딩이 **model-mismatch no-op**
  (Gemini API 가 모델 일치를 요구; langchain 이 mismatch 를 조용히 drop
  해 에러는 안 나지만 절감도 0). 따라서 이전의 "4 analysts input ~75%
  절감" 기술은 **사실이 아니었고**, 분석가 측 cached_content 바인딩은
  2026-05-26 제거됨 (절감 0 + future langchain 버전업 시 hard error 로
  돌변할 latent 위험 차단). Cache 미생성 시 자동 fallback. 분석가 input
  을 실제로 캐싱하려면 별도 Flash-model 캐시가 필요 (절감 ~5%, 미적용
  — 분석가 context 가 sliced→full 로 바뀌어 품질 무손실 보장 안 됨).

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

