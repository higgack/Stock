# NOAH Stock Bot — 상세 레퍼런스 / 이력 (full archive)

> 활성 행동 규칙 요약은 **CLAUDE.md**. 이 파일은 기능 구현 로그·이력·상세 전량
> (원 CLAUDE.md 전문 보존, 2026-06-20 분리). 해당 영역 작업 전 관련 섹션 확인.

---

# NOAH Stock Bot — Project Notes for Claude

Operational rules for working in this repo. Apply to **every subproject** in
this repo (currently: `bot/` NOAH stock-bot, `trade/` Korea import/export bot).

## ⛔ trade/ (한국 수출입 봇) — 단일 브랜치 통합 (2026-06-11)

`trade/` 풀 코드는 **이 브랜치(메인 deploy, xqYf7)** 에서 관리한다. 옛
`claude/export-import-dashboard-zQsi2` 브랜치는 은퇴(전환 포인터 커밋만
남김 — 거기에 새 작업 금지). 운영 구조:
- VM 은 체크아웃 2개: `~/stock`(NOAH 봇) + `~/stock-trade`(trade 봇,
  자기 venv) — **둘 다 같은 repo·같은 브랜치(xqYf7)** 를 추적.
- trade 배포: `~/stock-trade/deploy/trade-auto-update.sh`(1분 폴링)가
  xqYf7 을 reset --hard 후 `trade/` 변경 시 trade-bot 재시작, trade-bot
  유닛 변경 시 `install-trade-units.sh` 자동 설치. NOAH 쪽 auto-update
  와 독립(서로 다른 체크아웃·서비스).
- trade 작업도 이 세션 플로우 그대로: gifted-bell 에서 수정 → PR →
  xqYf7 merge → 1분 내 양 봇 각자 배포. trade/ 변경 commit 도 본
  CLAUDE.md 규칙(검증·커밋 게이트) 동일 적용.

## Default workflow — 배치 적재 (사용자 정책 2026-06-12, 옛 review-first 대체)

사용자 시간 절약이 목적: "한꺼번에 니가 처리하는동안 내가 다른일하고,
너 처리하면 최종으로 보고". 요청은 묶어서 처리한다.

1. **평소 = 적재 모드**: 사용자가 명시적으로 커밋/배포/푸쉬라고 하지
   않으면, 요청을 **구현·검증까지 하되 배포(merge)는 안 한다**. 무리가
   없는 한 계속 쌓는다. **매 답변 끝에 `📦 누적: N개 파일` 표시**
   (N = base 브랜치 대비 미배포 변경 파일 수, `git diff --name-only
   origin/<base>` 기준).
2. **중간 응답 최소화**: 적재 확인 1~2줄 + 📦 카운트만. 요청마다 긴
   보고 금지 — 사용자를 기다리게 하지 말 것. 질문/리뷰만 있는 요청
   (코드 변경 없음)은 답변만 하고 적재 카운트 무변동.
3. **적재 내구성**: 컨테이너가 휘발성이므로 적재분은 dev 브랜치에
   `[배치 보류 — merge 금지]` prefix 커밋으로 체크포인트 + push 한다.
   merge 전엔 VM 에 절대 도달하지 않으므로 사용자 관점 '아직 커밋 안 된
   누적'과 동일하고, squash merge 가 어차피 1커밋으로 합친다. draft PR
   은 '[배치 보류]' 제목으로 보관 (auto-update 는 base 만 감시).
4. **"커밋해/푸시해/배포해" 한마디 = 일괄 flush**: 검증(syntax+회귀)
   → **배포 전 셀프 리뷰(아래 Pre-commit §7 — diff 재독·시그니처/
   타임존 가정 검증·배선 E2E·진입점 스모크)**
   → 잔여 작업 커밋 → PR 제목/본문 최종화 + ready → **squash merge
   1회** → 자동배포 → **최종 통합 보고** (변경/적용사항 표 + 사용자가
   화면에서 확인할 체크포인트 + `📦 누적: 0개`). merge 까지가 배포다
   (사용자 정책 2026-06-08) — draft 만 열어두고 멈추지 말고, "merge
   할까요?" 되묻지 말 것. VM auto-update 는 merge 된 base 브랜치만 감시.
5. **즉시 배포 예외 (무리가 없는 한의 경계)**: 라이브 장애(봇 다운·
   watchdog 재시작 루프·대시보드 500 등)는 그 fix 만 최소 분리해 즉시
   merge 판단 가능. 적재가 너무 커져 충돌·검증 부담이 생기면 중간
   flush 를 제안.
6. 변경은 항상 **generalized universal rule** (ticker-specific·one-off
   패치 금지 — 아래 UNIVERSAL CHANGES ONLY 섹션).

## ⛔ 과거 실수 기록 — 반복 금지 (사용자 정책 2026-06-08)

**모든 세션에서 이 섹션을 먼저 읽고 같은 실수 반복하지 말 것.**

1. **배포 = merge까지 (2026-06-08)**: draft PR 만 열고 멈추면 VM
   auto-update 가 감지 못함. PR ready → merge → auto-update 1분 내
   배포. "배포했습니다" 라고 말하기 전에 merge 확인 필수.
2. **watchdog 연동 확인 (2026-06-08)**: `deploy/watchdog.sh` 가
   `"bot starting"` 로그를 180초 윈도에서 찾아 startup skip 판단.
   startup 에 무거운 작업(dashboard regen 3-5분)을 추가하면 이 로그
   **이전에** 찍혀야 watchdog 이 false-restart 안 함. 봇 startup
   경로 변경 시 반드시 watchdog 시나리오 검증.
3. **URL 템플릿 이중 prefix (2026-06-08)**: `_SUBS_URL` 에 이미
   `CIK{cik}` 가 있는데 호출부에서 `f"CIK{cik}"` 로 또 붙여
   `CIKCIK...` 404. 템플릿 변수에 prefix 가 있는지 항상 확인.
4. **auto-update 브랜치 (2026-06-08)**: `auto-update.sh` 가 감시하는
   브랜치(`claude/stock-trading-automation-xqYf7`)에 merge 해야 배포.
   다른 브랜치에 push 하고 "배포됐다" 하면 안 됨.
5. **.env 오타 (2026-06-08)**: 사용자가 준 키를 등록할 때 오타(한글
   자모 `ㅋ` 혼입) 가능. 등록 후 `grep` 으로 확인 코드 항상 제공.
6. **봇 restart loop 중 알림 불가 (2026-06-08)**: watchdog 가 봇을
   반복 재시작하면 auto-update 알림도 안 옴 — 봇이 polling 못 하니까.
   이 상태에선 사용자에게 VM 수동 restart 안내 필요.
7. **텔레그램 HTML escape 누락 (2026-06-08)**: `부채비율<200` 의
   `<200` 이 텔레그램 HTML 파서에서 태그로 오인 → BadRequest.
   `parse_mode=HTML` 로 보내는 텍스트에 사용자 입력/조건식의
   `<`/`>` 가 포함되면 반드시 `&lt;`/`&gt;` escape 필수.
8. **f-string `{{` + `.replace` 불일치 (2026-06-08)**: f-string 안의
   `{{cards}}` 는 `{cards}` 로 변환됨. `.replace("{{cards}}", val)`
   은 이중 브레이스를 찾아 매칭 실패 → 리터럴 `{cards}` 노출.
   `.replace("{cards}", val)` 이어야 함.
9. **알림 '신규' 판정은 영구 seen-set 필수 (2026-06-11)**: #19 리스크
   알림이 휘발성 큐 파일 기준 '신규' 판정 → 봇 재시작(배포·watchdog·
   자정)마다 같은 알림 통째 재발송. 알림류는 반드시 영구 seen-set +
   첫 활성화 seed(blog/reddit watcher 패턴) + 날짜 가드. 또한 전 종목
   무차별 푸시는 스팸 — 알림은 관심종목 등 타겟 한정이 기본.
10. **전역 표기·연결 정책은 새 표면에 '자동' 적용 (2026-06-11, 사용자
   "미칠것 같아")**: (a) **모든 시간 표기 = 한국시간, 명시적 UTC+9 로
   계산** — `time.localtime()`/`fromtimestamp()` 등 서버 로컬타임 의존
   금지(우연히 맞는 것 ≠ 보장). (b) **데이터 위젯/페이지엔 '실제 적용
   시각 · 소스' 라벨** (캐시 mtime 또는 산출 시각 — 렌더 시각 아님).
   (c) **명령어 = 텔레그램·대시보드 단일 레지스트리**. 새 위젯·페이지·
   명령 추가 시 사용자가 말 안 해도 기본 적용 — 표면마다 반복 지적하게
   만든 것이 이날 최대 불만. 신규 surface 체크리스트: KST 명시? ts·소스
   라벨? 레지스트리 등록? help·대시보드 동기?
11. **'배포 완료' ≠ '사용자 화면에서 보임' (2026-06-11)**: 신고저 전량
   페이지네이션을 배포하고도 Finviz 1차가 죽어 폴백(S&P500 9/10)만
   보였고, 백필을 배포하고도 시총워밍 45초 예산·enrich 3일 윈도에 걸려
   카드가 비어 보였음. 기능 배포 보고 시 (a) 사용자가 화면에서 확인할
   체크포인트(출처 라벨·카운트·기준시각)를 함께 제시, (b) 화면이 기대와
   다르면 "배포됐다"로 끝내지 말고 소스→캐시→예산/윈도→렌더 전 경로를
   실데이터로 추적해 끊긴 지점을 특정해 보고. 부분 성공 단서(업종은
   Finviz 성공 vs 신고저만 실패)를 교차하면 원인이 좁혀진다.
12. **추측 보고 금지 + 재요청 프로토콜 (2026-06-12, 트레이드 히트맵
   '하루종일' — 사용자 "미칠것 같아", 막대한 시간낭비)**: 히트맵 미표시를
   3번에 걸쳐 추측으로 답함 ("재시작되면 보일 것"→"캐시 자정에 풀릴
   것"→"백필 돌고 있을 것") — 실제로는 ①sudoers 부재로 재시작 silent-
   skip ②스캔 출력 DEVNULL 로 사망 무흔적 ③**분할기(fetch_chapter_
   range)가 구현·테스트 완비인데 호출부만 raw fetch_chapter 사용** (관세청
   1년 초과 거부 → ok=0 fail=97 → 히트맵 영구 empty). 영구 규칙:
   (a) **검증 불가능하면 단정 보고 금지** — "~일 겁니다" 대신 사용자에게
       **정확한 확인 명령 1블록**(journal grep·파일 stat·DB count)을 요청
       하고 그 출력으로 판정. 샌드박스에서 VM 은 안 보인다.
   (b) **같은 증상 재요청(2회+) = 추측 단계 종료 신호** — 즉시 가시성
       (로그 파일화·상태 라벨·silent-except 경고)을 심어 배포하고, 사용자
       확인 명령으로 근본 원인을 특정할 때까지 "됐다" 보고 금지.
   (c) **배선 E2E 확인**: 헬퍼/가드를 만들었으면 "구현됐나"가 아니라
       "**호출부가 실제로 그걸 타나**"를 grep 으로 확인 (분할기·재시작
       권한·킥 로그 전부 이 클래스). 테스트는 호출부 배선까지 가드.
   (d) silent-fail(DEVNULL·except-pass) 은 디버깅 비용 폭증의 근원 —
       새 background 작업/렌더 경로에 절대 금지.

13. **UI '안 됨' 디버깅은 접속 URL/렌더 환경부터 — 코드 추측 전 (2026-06-18,
   trade 보고서 위젯 '네트워크 오류' 8회 추측, 사용자 격분)**: 기업/전체 보고서
   위젯이 '네트워크 오류'인 걸 credentials·서버재시작·venv·라우트 등 **8번 코드로
   헛짚다**, 사용자가 준 **접속 URL 한 줄**(http://…:8081/<token>/trade/)이 근본을
   즉시 가리켰다 — NOAH(8081)가 trade(8765) 리버스 프록시인데 `_trade_upstream_url`
   이 `/api/*` 를 `8765/dashboard/api`(404)로 보냄(미디어만 ROOT였음). 교훈:
   (a) **UI 버그는 브라우저 접속 URL**(포트·프록시·토큰경로)을 **맨 먼저** 물어라.
       `curl localhost:PORT` 가 200/401 이어도 브라우저는 프록시로 다른 경로를 탄다 —
       서버 정상 ≠ 브라우저 정상. 프록시(8081→8765) 구조를 늦게 인지한 게 최대 낭비.
   (b) **콘솔 DOM 측정 ≠ 화면**: 미국 더보기 `shown:40` 을 보고 '정상'이라 단정했으나
       사용자 화면은 여전 10. DOM 상태와 실제 렌더(스크린샷)를 교차 안 하면 오진.
   (c) **같은 증상 반복 호소(위젯·미국 더보기) = 추측 종료 신호**(실수 #12 재확인) —
       즉시 가시 정보(URL·캡처) 요청, '됐다/정상' 단정 금지.
   (d) **실수 기록 요구를 미루지 말 것** — 사용자가 'CLAUDE.md에 기록하라'를 여러 번
       말했는데 안 해서 격분. user-visible 변경처럼 실수도 같은 턴에 기록한다.

새 실수 발생 시 이 리스트에 날짜 + 한 줄 교훈 추가 의무.

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
5. **Multi-step phase work** — ⚠️ **2026-09-06 대체됨**. 아래는 원문 보존이고
   현행 규칙은 `CLAUDE.md` §Pre-commit 5 다(항목별로 검증하되 한 턴에 끝까지
   간다 · 대기는 merge 게이트에서만 · 사용자가 순차 검증을 명시하면 그때는 기다림).
   2026-06-20 압축이 아래 한정어 둘("or no objection arrives" · "when the user
   asked for sequential validation")을 떨어뜨려, 2026-06-12 배치 적재 정책이
   이미 대체한 review-first 가 78일 동안 정지 규칙으로 살아남았다(#287).
   원문:
   after each item finishes, verify the
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
7. **배포(merge/flush) 전 셀프 리뷰 — 의무 (사용자 2026-06-13 '같은 거
   두 번 하는 거 정말 힘들어')**: 테스트 green 만으로 merge 금지. flush
   직전 **base 대비 전체 diff 를 다시 읽고** 아래를 grep 으로 재확인:
   (a) **기존 함수 호출 가정 검증** — 새 코드가 부르는 기존 함수의 실제
       시그니처·반환형을 정의부에서 확인 (digest `open_db()` 인자 누락
       클래스 — 순수 단위테스트는 mock 이라 못 잡음).
   (b) **데이터 의미 검증** — 타임존(UTC↔KST)·단위(원/천원/백만원·$)·
       포맷(isoformat/substr)을 쓰는 비교/집계는 **실제 기록 포맷을
       기록부 코드에서 확인** 후 경계 케이스 테스트 동반 (digest
       `ingested_at` UTC 를 KST date 로 substr 비교하던 클래스 — KST
       하루 = UTC 전일 15시~당일 15시).
   (c) **호출부 배선 E2E** — 헬퍼/파라미터를 추가했으면 모든 호출부가
       실제로 그걸 타는지 grep (heatmap_html 인자 누락 → 5분 렌더 전부
       크래시 클래스, 실수 #12c).
   (d) **진입점 1회 실행** — 순수 함수 테스트와 별개로 실제 진입 경로
       (render/_build_html·스크립트 main·실 스키마 DB)를 기본값으로 한
       번 통과시키는 스모크 (NameError/ImportError 류는 이것만이 잡음).
   2026-06-13 digest 버그 2종(a·b)이 이 리뷰에서 배포 직전 적발된 것이
   제정 계기 — 리뷰 없이 나갔으면 '결산 0건' 으로 또 하루를 썼다.

Skipping verification is treated the same as skipping the explicit-
commit-request rule — never do it.

## Help text registration of changes — mandatory

**✅ 갱신 타이밍 — 같은 commit 자동 갱신 복원 (사용자 재확인
2026-06-11 오후):** 한때 '사용자 요청 시 일괄 갱신' override 가 있었으나
사용자가 같은 날 폐기 ("당연히 help 에도 자동 update 맞지? 내가 안말해도
하는거야"). 아래 '같은 commit 의무' 원칙이 그대로 유효 — user-visible
변경은 `_HELP_TEXT` 를 같은 commit 에서 갱신한다. **기능을 제거할 때도
help 의 해당 줄 제거 의무** (소송/리스크 알림 제거 시 §8 줄 잔존했던 것
같은 drift 방지). 4096 cap·압축·도메인 inline 금지 등 나머지 규칙 유효.

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

## 매크로/글로벌 스냅샷 카드 인벤토리 — canonical 순서 (2026-06-23 사용자 '기억해둬')

> 두 표면의 카드 종류·순서 = 사용자 지정 spec. 카드 추가/제거/이동 시 이 목록을
> 같은 commit 으로 갱신(out-of-sync = 버그). 새 카드는 소스(FRED/ECOS/naver) 코드를
> VM 에서 검증 후 추가(추측 코드 = 빈 카드, 2026-06-23 ECOS saga 교훈).

### A. Macro Snapshot (`bot/macro_snapshot.py` — `DOMESTIC` / `GLOBAL`)
- 카드 = `(key, label, unit, src, sid, decimals)`. src: ecos(BOK)·fred·yf(값=naver `_MACRO_NAVER`).
- ECOS 단위 변환은 `bot/bok_ecos_client.py` `_SERIES[*]["scale"]`(default 1.0).
- **국내 (10)**: 한국 기준금리(ecos base_rate) · 국고채 3년(kr3y) · 국고채 10년(kr10y) ·
  한국 CPI(cpi_idx) · USD/KRW(yf USDKRW=X) · 한국 수출(ecos export_amt 901Y118/T002 천$→억$) ·
  한국 수입(ecos import_amt 901Y118/T004) · 경상수지(ecos current_account 301Y017/SA000 백만$→억$) ·
  외환보유액(ecos fx_reserve 732Y001/99 천$→억$) · 한국 GDP(ecos kr_gdp 902Y015/KOR 분기%).
- **글로벌 (30)**: 달러인덱스(DXY, DX-Y.NYB — `_MACRO_NAVER` 미매핑, yf 폴백) · 미국 2Y(DGS2) ·
  미국 10Y(DGS10) · 미국 장단기금리차(T10Y2Y) · 미국 하이일드(BAMLH0A0HYM2) ·
  미국 CPI(CPIAUCSL) · 미국 PPI(PPIFIS Final Demand) · 미국 실업률(UNRATE) ·
  미국 GDP(A191RL1Q225SBEA 분기) · S&P 500 · NASDAQ · VIX · 필라델피아 반도체(SOX) ·
  WTI · 브렌트유 · 천연가스 · 금 · 은 · 구리 · 알루미늄 합금 · 니켈 · 옥수수 · 대두 ·
  소맥 · 돈육 · 커피 · 면화 · 비트코인 · 이더리움 · 솔라나.
  (미국 FFR 제거 2026-06-23. 미국 경기활동(CFNAI) 제거 + 달러인덱스 복원 2026-07-31.)

### B. 글로벌 시장 스냅샷 (`bot/market_overview.py` — `ALL_CARDS` 9 섹션 + `FRED_INDICATORS`)
별개 표면(홈 스냅샷). 카드 = `(label, "src:code")` — nvd/nvi/nvx/nvk/nv/nvc/nvf/nve = 네이버 경로.
1. **한국 & 아시아**: 코스피·코스닥·니케이225·상해종합·심천종합·홍콩항셍·대만가권·말레이시아 KLCI·인니 IDX종합·인도 Sensex
2. **주요 환율 (FX)**: 원/달러·엔/원(100엔)·유로/달러·파운드/달러·달러/위안·달러/HKD·대만달러/USD·루피/달러·호주달러/달러·스위스프랑/달러
3. **핵심 지표 (금리/경제)** `FRED_INDICATORS`: 미국채 2년·10년·30년·JOLTS·실업수당청구·GDP성장률(QoQ)·근원PCE물가(YoY)·소매판매(YoY)·CPI(YoY)·PPI(YoY, PPIACO)
4. **원자재 & 귀금속**: WTI·브렌트·두바이·천연가스·금·은·구리·알루미늄·니켈
5. **시장 심리 & 코인**: VIX·비트코인·이더리움·테더·USD코인·리플·솔라나·트론·도지코인
6. **미국 지수**: S&P500·나스닥종합·다우존스·다우운송·필라델피아반도체·XLF금융·XLV헬스케어·XLC커뮤니케이션·XLP필수소비
7. **미국 지수-2**: XLY임의소비·XLI산업재·XLE에너지·XLU유틸리티·IYR부동산·XLB소재
8. **미국 지수 선물 & 운송 (Futures)**: S&P500선물·다우선물·나스닥100선물·러셀2000선물·BDI건화물운임·중국컨테이너운임(CCFI)
9. **유럽 지수**: 유로스톡스50·독일DAX·영국FTSE100·프랑스CAC40·이탈리아FTSE MIB·네덜란드AEX

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
- /ticker 상세 페이지 가격 차트 (2026-06-03, 사용자 요청 — 대시보드 주 소비) → `bot/chart_data.py` `build_price_chart(ticker)` 가 분석 종료 후 yfinance 1년 종가 + 10EMA/50SMA/200SMA 를 `_compute_technical_snapshot` **동일 계산식**으로 산출 (차트 MA = 본문 TECHNICAL SNAPSHOT SSoT 일치). `archive.py` SCHEMA_VERSION 2 로 `price_chart` 필드 저장 (parallel arrays: times/close/ema10/sma50/sma200, 통화·decimals 포함, ~11KB/분석, try/except 비치명적). `dashboard.py` `_render_detail` 이 `_render_chart_section` 으로 종가+3 MA 라인 차트 emit (lightweight-charts v4.2.0, 다크/라이트 테마 연동, JSON 은 `<script type=application/json>` 블록 — `</` defuse). 라이브러리는 `_ensure_chart_lib()` 가 regen 시 ARCHIVE_ROOT 에 1회 자동 다운로드 → **self-host (CDN 의존 제거, 오프라인 작동)**, 실패 시 텍스트만 graceful. **새 분석부터** 차트. **Phase 2 완료 (2026-06-03)**: (a) **entry/stop/target 수평선 마커** — `chart_data.parse_trade_levels(full_report, close_values)` 가 본문의 진입가/손절가/목표가(Entry Price·Stop Loss·Price Target + 한국어 변형) 파싱, **종가 series 대비 0.2x~5x 밴드 밖이면 drop** (비현실 오파싱이 잘못된 라인 그리는 risk 차단 — v1 제외 사유 해소). render-time 파싱(미저장)이라 파서 개선·백필 모두 즉시 반영. `_CHART_JS` 가 createPriceLine 으로 진입(보라)·손절(빨강)·목표(초록) dashed 라인. (b) **옛 기록 소급 백필** — `archive.backfill_price_charts()` 가 schema v1 기록에 price_chart 추가, **분석일 시점(as_of) 1년 윈도 fetch** (lookahead 없음·MA 가 그날 SNAPSHOT 일치). telegram_bot startup 에서 marker 파일(`.charts_backfilled`) gate 로 **1회만** 백그라운드 thread 실행 → 완료 후 regenerate_index. 비용 ₩0 (yfinance only). universal (전 시장). **Phase 3 — 일/주/월봉 + 기간 선택 (2026-06-04, on-demand)**: 상세 페이지 토글 버튼(일/주/월봉 · 6개월/1년/3년/5년/전체). 기본=저장된 1년 일봉(즉시·SSoT 일치·오프라인). 다른 조합 클릭 시 `dashboard_server.py` `/api/chart?ticker=&interval=&range=` GET (인증 gate, `_TICKER_RE` + interval/range 화이트리스트, `~/.tradingagents/chart_cache/` 1h 디스크 캐시) → `chart_data.fetch_chart_payload()` 가 yfinance fetch → `_CHART_JS` 가 chart.remove() 후 재렌더. 주/월봉은 MA 가 그 interval 기준 재계산(10wk/50wk/200wk — 일봉 기본만 본문 SSoT 일치, 의도된 동작). 마커는 가격 라인이라 모든 interval 재적용. 비용 ₩0 (yfinance only, 1h 캐시). **Phase 4 — 현재가/시점가 분리 (2026-06-04, 사용자 요청)**: 메인 라인을 분석일까지가 아닌 **오늘까지(현재가)** 로 변경 — `_CHART_JS` 의 `load()` 가 기본 뷰(1d/1y)도 `/api/chart` 로 to-today fetch (start 에서 저장된 payload 로 즉시 placeholder 렌더 후 교체). 우측 축 라벨 메인 series title `현재가`. **시점가**(분석일 종가) = stored payload 의 `as_of_close`(=close[-1], `_render_chart_section` 이 주입)를 회색 점선 priceLine(`시점가`)으로 항상 표시 — "그때 가격 ↔ 지금 가격" 복기 view. ⚠️ 기본 뷰가 to-today 라 차트 MA 는 더 이상 본문 TECHNICAL SNAPSHOT(분석일 기준)과 일치하지 않음 (시점가 라인이 분석 시점 표지). offline/서버 다운 시 placeholder(분석일까지) 유지 + status 에 실패 표시. **Phase 5 — 라벨 정리 + 보조지표 (2026-06-04, 사용자 요청)**: (a) 라벨 겹침 해소 — 이동평균(10EMA/50SMA/200SMA)+RSI+거래량 값은 차트 **오른쪽 바깥 패널**(`#chart-values`), 현재가/시점가/진입/손절/목표(분석 가격)는 차트 안 축 라벨(`axisLabelVisible`) 유지 (실제 가격 대비 분석가 위치 확인). 차트 높이 440. (b) **보조지표** — `_series_payload` 가 `rsi`(Wilder 14, 본문 SNAPSHOT SSoT 동일식)+`volume` 배열 추가. `_CHART_JS` 가 거래량을 가격 pane 하단 18% 히스토그램(상승 초록/하락 빨강, `priceScaleId:'vol'` overlay), RSI 를 **하단 별도 pane**(`#rsi-chart`, 120px, 70/30 기준선)로 emit, `syncTime()` 가 두 차트 시간축 동기화 (줌/팬 연동, `minimumWidth:72` 로 정렬). 주/월봉 전환 시 RSI/거래량도 그 interval 기준 재계산. 비용 ₩0. **차트 라벨/로고 (2026-06-04)**: 현재가/시점가만 차트 안 축 라벨로도 표시(우측 패널과 중복), 진입/손절/목표는 선만(겹침 방지). TradingView attribution 로고는 `layout.attributionLogo:false`(v4.2.0) 로 제거(모든 pane). 지표 기본 ON=이평선·거래량·RSI(캡처 기준). **새로고침 시 기본값 회귀 — 미영속(사용자 정책 2026-06-06): 페이지 안에서는 토글 자유, 새로고침하면 기존 디폴트(캔들·이평선·거래량·RSI ON / 볼린저·MACD·로그·공시 OFF)로 복귀. 봉·기간도 일봉·1년 하드코딩이라 같이 회귀.** **Phase 6 — 볼린저+MACD+캔들+토글 (2026-06-04, 사용자 "3개 다+캔들")**: `_series_payload` 가 볼린저(20,2σ: bb_u/bb_m/bb_l)+MACD(12,26,9: macd/macd_signal/macd_hist)+OHLC(open/high/low) 추가 — 볼린저·MACD 는 본문 SNAPSHOT SSoT 동일식. `_CHART_JS` 에 지표 on/off 토글 6종(캔들/이평선/볼린저/거래량/RSI/MACD), 토글 시 `lastData` 로 refetch 없이 재렌더. ⚠️ `noah_chart_ind_v1` localStorage 영속은 **2026-06-06 제거** — `loadInd()` 가 항상 IND_DEFAULT 반환 + 옛 키 removeItem, `saveInd()` no-op. 새로고침 시 기본값 회귀(위 Phase 5 정책). 캔들=`addCandlestickSeries`(OHLC 있을 때만, 없으면 라인). 볼린저=가격 overlay 점선 3선. MACD=하단 별도 pane(라인+시그널+히스토그램). `linkTimeScales([chart,rsi,macd])` 가 최대 3 pane 시간축 동기화. `subChart()`/`showSub()` 헬퍼. 기본 표시: 이평선·거래량·RSI ON, 캔들·볼린저·MACD OFF(클러터 방지, 사용자가 켜면 저장). v4 는 네이티브 pane 미지원이라 오실레이터=동기화 sub-chart, 실용 한계 2~3 pane. **Phase 7 — 장중 현재가 (~15분 지연, 2026-06-04)**: `chart_data.fetch_chart_payload` 가 yfinance `fast_info.last_price`(fallback lastPrice/regularMarketPrice, ~50ms, 무료·무키)를 payload `last_price` 필드로 추가(1d interval 만). 프론트가 series 마지막 봉을 라이브로 대체 — 라인은 마지막 점 value 교체, 캔들은 close + high/low 보정. MA 는 별도 사전 계산이라 흔들리지 않음(시각적 1봉만 갱신). 우측 패널 '현재가' 도 last_price 우선. 캐시 키 v2→v3 + TTL 1h→5min(yfinance 호출 12x↑, 단일 채널 audience 면 무료한도 ~2000/h 안전). KR(.KS/.KQ)은 yfinance 가 종종 EOD only → 해 없음(last close 와 같음). 라벨에 '~15분 지연·KR EOD 가능' 정직 표기. build_price_chart(snapshot, archive) 는 last_price 안 넣음(시점가 보존). **Phase 8 — 과거 추천 마커 + 로그스케일 + 크로스헤어 툴팁 (2026-06-04, 사용자 요청)**: (a) **과거 추천 마커** — `regenerate_index` 가 종목별 모든 분석(archive)의 판정(_RATING_RE)+5거래일 결과(resolved_lookup.raw)를 `_ticker_analysis_markers` 로 모아(한 번만, O(n²) 회피) `_render_detail`→`_render_chart_section` 으로 전달 → payload `analysis_markers` → `_CHART_JS` 가 `mainS.setMarkers` 로 ▲매수(초록 belowBar)/▼매도(빨강 aboveBar)/●보유(회색 circle) + '+8.3%' 결과 텍스트. 데이터 범위(firstT~lastT) 밖 필터. **우리만의 차별점 — 차트에 우리 track record 시각화**. (b) **로그 스케일** — `로그` 토글(ind.log, 미영속) → main 차트 rightPriceScale.mode 1, 긴 기간 % 비교. sub-pane 은 선형 유지. (c) **크로스헤어 툴팁** — crosshair mode 1(Magnet) + `subscribeCrosshairMove` → 커서 지점 날짜+종가+이평선+RSI+거래량 floating div(`.chart-tooltip`, pointer-events:none, lastData index 로 조회). 비용 ₩0. **Phase 9 — 공시 이벤트 마커 (2026-06-05, 사용자 요청, 전 시장·공시만)**: `bot/chart_events.py` `fetch_disclosure_events(ticker)` 가 시장별 무료 클라이언트(KR DART get_recent_disclosures / US EDGAR get_recent_8k / JP EDINET / TW MOPS / CN·HK AKShare)의 공시를 `{time,title,type,color}` 로 통일. `classify()` 다국어 키워드로 분류 + **화이트리스트 8종만 표시**(사용자 2026-06-05): 수주·계약(초록)/소송(초록 동색)/시설투자(파랑)/주주환원(보라, 배당·자기주식)/자본변동(주황, 증자·CB·감자)/M&A·지분(청록, 합병·영업양수도·타법인주식)/리스크(빨강, 상장폐지·거래정지·횡령배임)/최대주주변경(분홍). 그 외(실적·임원·Reg FD 등='other')는 `_SHOW_TYPES` 필터로 제외. 검사 순서 = 주주환원→소송→리스크→최대주주→M&A→시설투자→수주→자본변동(자기주식취득이 capex/mna 로 오분류 안 되게). **US EDGAR 8-K 는 축약 라벨**('Material Agreement'·'Unregistered Sales of Equity'·'Acquisition/Disposition'·'Change in Control'·'Exchange Delisting')이라 각 KW 에 축약형 포함(2026-06-05 버그 fix — 안 하면 US 8-K 가 거의 다 'other' 로 필터돼 마커 0). ⚠️ US 8-K 는 항목이 ~15종으로 넓어 매칭 마커가 KR DART(세밀한 공시명)보다 구조적으로 적음. **호재·악재 '판단' 안 함**(종류만 색). `_norm` 이 `url`(DART rcpNo·EDGAR 원문) 포함. `chart_data._series_payload(...,ticker=)` 가 보이는 날짜구간 필터해 payload `events` 주입(build_price_chart + fetch_chart_payload). `_CHART_JS` 가 추천 마커와 합쳐 `setMarkers`(작은 사각 belowBar, 텍스트 없음). **공시 내용은 floating 툴팁이 아니라 차트 아래 `#chart-disc` 패널**(마커 hover 시 종류·전체 제목·간단 설명·원문 링크, escTt HTML escape, 공시 없는 날 hover는 직전 표시 유지). `공시` 토글(ind.events **기본 OFF** — 선택 표시, 미영속·새로고침 시 OFF 회귀). **가독성 (2026-06-05, 최종)**: 사용자 의도 = 태그(현재가/시점가/RSI/MACD)를 **지우는 게 아니라 가격축 바깥 오른쪽 여백으로** 빼 캔들을 안 가리게. 원인 = lightweight-charts 가 축 라벨(이름+값)을 가격축 폭이 좁으면 플롯 안쪽으로 넘쳐 그림(series `title` 이 이름 라벨 생성). fix: 라벨 복원(`lastValueVisible:true`+title, 시점가 priceLine `axisLabelVisible:true`) + **rightPriceScale `minimumWidth` 72→130**(메인·sub 동일=시간축 정렬) → 라벨이 넓은 가격축 여백 안에 들어가 캔들 위로 안 넘침. `localization.priceFormatter`(만/억)로 메인 라벨 짧음('현재가 177.6만'). rightOffset 7 로 최근 캔들과 라벨 분리. 추천 마커 size 2. 별도 #chart-values 패널(이름+값 리스트)은 그대로 더 오른쪽에. **KR 구조화 요약 (2026-06-05, ₩0)**: `bot/dart_detail.py` `get_disclosure_summaries(stock_code)` 가 DART **주요사항보고서 주요정보** API(유상증자 piicDecsn / 자기주식취득 tsstkAqDecsn / 전환사채 cvbdIsDecsn / 합병 cmpMgDecsn)에서 핵심 숫자(증자금액·취득금액/수량·발행총액·합병비율)를 뽑아 {rcept_no: '한 줄'} 반환 + 배당은 정기보고서 alotMatter(주당배당금·시가배당률)를 '배당' 마커에 context('__DIVIDEND__'). chart_events KR 경로가 rcpNo 매칭해 event['summary'] 부착 → 차트 아래 패널이 종류설명 대신 요약 표시. LLM 0·무료·graceful(키/필드 부재 시 제목+원문 fallback). ⚠️ 배당·공급계약은 주요사항보고서가 아닌 거래소 수시공시라 구조화 숫자 제한(배당=연간 context, 공급계약=제목만). DART 구조화 필드명은 라이브 검증 필요(샌드박스 키 부재 — 후보 필드 다중 시도 + graceful). **공시 제목 한국어화 (2026-06-05)**: US 8-K 항목명은 고정 ~21종이라 `chart_events._US_8K_KR` 사전으로 ₩0 완역(분류는 영문 라벨로 먼저 확정 후 표시만 번역 — 순서 중요). CN/JP/TW 는 자유 텍스트라 `bot/chart_translate.py` `translate_titles_kr` 가 Gemini Flash 로 번역(영구 디스크 캐시 `chart_title_kr.json` → 제목당 1회·이후 ₩0, 배치 ≤40, graceful, usage.jsonl subsystem='chart_translate'→총합 포함·분석 버킷 폴딩). chart_events 가 CN/JP/TW 에서만 whitelist 필터+slice 후 번역(최종 마커만, 분류는 원문 CJK 로 이미 확정).
**커버리지 (2026-06-05 확장 — 차트 기간 span 을 fetch_disclosure_events(days=) 로 전달)**: KR·US = **차트 선택 기간 풀히스토리**(DART 페이지네이션 6p×100=600건 캡 / EDGAR submissions 1콜, 캡 ~11년), JP = **180일 캡**(EDINET 이 하루씩 호출하는 구조라 1년=365콜 = 첫 로딩 지연·차단 위험 → 리스크-프리 캡), TW/CN = **1년**(호출 1~2회로 싸나 무료 API 가 최근 위주 데이터). 비용 ₩0(LLM 0, 무료 API, 12h/영구 캐시). **기본 토글 OFF**(events:false — 사용자가 '공시' 버튼으로 선택). legend + `ℹ️ 보는 법` 가이드 동기. universal.
**Phase 10 — 헤드라인·OHLC 바·YTD·캡션 (2026-06-07, 레퍼런스 터미널 벤치마크 Tier 1)**: 상세 차트 상단에 (a) **헤드라인** `#chart-headline`(현재가 large + 기간 수익률[표시 구간 first→현재가, 절대+%] + 거래량), (b) **OHLC 상단바** `#chart-ohlc`(날짜·시고저종·일간등락·활성 이평선; 기본=마지막 봉, 크로스헤어 hover 시 그 봉으로, 이탈 시 복귀), (c) 차트 하단 **캡션** `#chart-caption`(범위·봉종류·봉 개수·날짜범위·Yahoo Finance). 기간 버튼에 **YTD**(연초 1/1→오늘) 추가 — `chart_data._VALID_PERIODS` + `dashboard_server._VALID_RANGES` 화이트리스트 + `fetch_chart_payload` ytd 분기(start=datetime(now.year,1,1)). `_CHART_JS` 에 `buildHeadline/buildOHLC/buildCaption` + `rangeLabel/intervalLabel` 헬퍼(기존 inline map 대체), render() 끝 + 크로스헤어에서 호출. 순수 프론트 + 화이트리스트 2줄, ₩0, universal(전 시장). legend + `ℹ️ 보는 법` 가이드 동기 갱신. **레전드 stale fix**: '(설정 저장됨)' → '(새로고침 시 기본값으로 회귀)' — Phase 6 미영속 전환(2026-06-06) 시 guide 만 갱신되고 legend 는 누락됐던 것 정정.

**Phase 11 — 일목균형표 · 이격도 (2026-07-31, 사용자 요청 '웹상에서 잘 검색해서 표준으로')**:
`bot/ichimoku.py` (신규, **pandas 없는 순수 함수** — elliott_fib 와 같은 이유: 신호 판정 로직이
있어 회귀 고정 필요 + 샌드박스 단위테스트 가능). 정석 웹 리서치(StockCharts ChartSchool ·
TradingView Pine 원문 · 楽天/マネックス証券 · 키움증권 기술적지표 가이드 · 알파스퀘어)와 대조.
- **일목균형표**: 전환 9 · 기준 26 · 선행2 52. 세 선은 **이동평균이 아니라 그 기간 고가·저가의
  한가운데**(Donchian midpoint, 당봉 포함) — `close.rolling(9).mean()` 로 쓰는 게 최다 오구현.
  선행1=(당봉 전환+기준)/2. **이격량은 소스가 갈린다**(TradingView 25봉 vs MT4/StockCharts 26봉;
  일본 원전 "당봉 포함 26번째"=25봉) → 사용자 대조 대상이 TradingView 라 `_SHIFT=25` 채택,
  상수로 노출. 반환 배열은 전부 `len+shift` 길이 = **선행스팬이 캔들 없는 미래까지** 이어짐
  (기존 index 에 그냥 shift 하면 투영 구간이 잘리는 게 오구현 1위). 후행스팬은 반대로 우측
  `shift`개가 None(정상). 최소 77봉(52+25) 미만이면 **아예 미제공**(반쪽 구름 금지).
  신호: 가격 vs 구름 / 구름색(양운·음운) / 전환-기준 크로스(+**크로스 난 그 봉**의 구름 대비
  위치로 강·중·약 등급) / 후행스팬 vs 과거가격 / **삼역호전(三役好転)·삼역역전** / 미래 구름
  전환(twist) 예정 봉수 / 종합 -4~+4. 파라미터는 **주봉·분봉에서도 고정**(현대 표준 관행).
- **이격도**: 한국 관행 `종가/이평×100`(**100 기준**) — 서구 Disparity Index 는 0 기준이라
  임계값 숫자가 다름(섞으면 '105'가 +105% 로 읽히는 대형 오독). 20일·60일. 과열/침체 밴드는
  **키움증권 가이드값**(20일 105/95 · 60일 110/90) 채택 — 국내 소스가 갈려(한경 계열은 국면별
  106/98 등) 화면 안내에 출처와 '절대 기준 아님' 명시. 추세장 함정 경고 필수.
- 배선: `chart_data._series_payload` → `payload["ichimoku"]`(자체 확장 시간축 포함) ·
  `payload["disparity"]`. 미래 축은 신규 `chart_data._future_times()` 가 생성 — **기존 times 와
  타입 일치 필수**(분봉=epoch 정수·그 외 날짜문자열), 일봉은 휴장일 반영 실제 세션
  (`market_calendar.future_sessions()` 신규, exchange_calendars 부재 시 주5일 근사 폴백).
  `_round` 는 isnan→**isfinite**(±inf 가 JSON 을 깨뜨림).
- 프론트: 버튼 `일목균형표`/`이격도`(**기본 OFF**, 양쪽 차트 화면). 구름 채우기는
  lightweight-charts 에 밴드 시리즈가 없어 **series primitive**(`attachPrimitive` +
  `useMediaCoordinateSpace`)로 캔버스 직접 폴리곤 fill, **봉마다** 양운/음운 판정(시리즈 단위로
  한 번만 정하면 twist 가 틀리게 칠해짐). primitive 미지원이면 선 5개만 남고 정상(graceful).
  이격도는 RSI/MACD 와 같은 하단 sub-pane(100 기준선 + 과열/침체 밴드). 툴팁은 **미래(투영)
  구간 hover 에서도** 구름 값을 보여주도록 early-return 완화(`idx<0 && ichiT<0` 일 때만 숨김).
  `#chart-ichi` 설명 패널 — 피보나치·엘리엇에서 '설명이 필요하다'는 지적을 받았으므로 이번엔
  처음부터 동봉. 캐시 v5→**v6**(페이로드 필드 추가).
- **독립 리뷰(`/code-review` 게이트)가 잡은 것** — 배포 전 수정: (a) 하위 pane 시간축 어긋남
  — lightweight-charts 논리 인덱스는 그 차트가 가진 시간점 순번이라 RSI(앞 14봉 결측)·MACD(26봉)
  가 이미 메인과 다른 봉을 가리켰고, 일목을 켜면 메인만 25봉 길어져 **오른쪽 끝까지** 틀어졌다
  → `padAxis()` 가 whitespace 데이터(`{time}`만)로 전 pane 에 같은 시간점을 깔아 인덱스 공간
  일치(기존 RSI/MACD 어긋남도 같이 해소). (b) 일목은 기본 OFF 라 **버튼 토글이 유일 진입로**인데
  그 경로는 preserve 재렌더(fitContent 생략)라 옛 범위를 복원 → 미래 구름이 화면 밖에 남음
  (실수#11 '배포완료 ≠ 화면에 보임') → `lastIchiOn` 추적해 켤 때 `to += shift`, 끌 때 `-= shift`.
  (c) `_cross_age` off-by-one — 크로스 **직전** 봉을 세어 `tk_at` 이 한 봉 전 구름 위치를 보고
  강·약 등급이 뒤집혔다 → `k-1`. (d) 일목/이격도가 한 `try` 에 묶여 한쪽 예외에 둘 다 소실 →
  분리 + `signal` 을 dict 리터럴 밖에서 선계산. (e) 이격도 기간이 JS 4곳 하드코딩 →
  `Object.keys` 순회 + `dispColor()` 폴백. (f) 테스트 결함 — `assert x in (None,) or True` 가
  **항상 참**(아무것도 검증 못 함), 일봉 미래축 테스트가 실제 캘린더 경로를 타 VM 에서만 깨질
  하드코딩(→ `ticker=None` 로 폴백 고정 + 캘린더 경로는 구조 검증).
- 검증: pandas 없는 샌드박스라 `bot/ichimoku.py` 는 직접 단위테스트, `_CHART_JS` 는 node 로
  실제 실행(스텁 DOM+lightweight-charts)해 시리즈 데이터·구름 폴리곤·패널·툴팁까지 확인.
  회귀는 `tests/test_regression.py::TestIchimokuDisparity20260731` (+`node --check` 로 53KB JS
  문법 상시 검증 — 파이썬 ast.parse 로는 JS 오타를 못 잡는다).
- Stale process recovery → `stock-bot-watchdog.service` restarts if main loop hangs 12 min. ⚠️ watchdog 는 180초 polling-hang(getUpdates 부재) + `.busy` marker(분석 중이면 12분까지 skip) 두 체크. **무거운 작업(Screener 5-10분, /ticker)은 반드시 `_busy_acquire()`/`_busy_release()` 로 감싸야** watchdog 가 실행 중 재시작해 작업을 살해하지 않음. 2026-06-01 Screener 가 busy marker 미사용으로 Hospitality & Leisure run 이 watchdog 재시작에 살해됨 → `_run_screener_and_send` 에 busy wrap 추가. 새 long-running 핸들러 추가 시 동일 패턴 필수.
- Memory pending-entry resolution → `_periodic_auto_resolve` asyncio task, 12 h cycle
- Daily dashboard regen → `_periodic_dashboard_refresh` asyncio task, 00:01 KST
- 대시보드 자동 신선 (사용자 2026-06-17 '모든 대시보드가 내가 안 들어가도 자기
  리프레쉬 주기로 갱신') — 표면별로 서버사이드 스케줄에 묶임, 방문 불요:
  • 홈(market.html) = `_periodic_market_refresh` 30초 · 무거운 52주(JP/HK/CN/TW
    +US) = **독립 `highlow-scan.timer`(:00/:15/:30/:45, 봇 배포·재시작과 무관 — 별
    프로세스 oneshot `bot.highlow_scan`)**, 장중 force·장밖 freeze. 슬롯 로직 단일
    소스 `bot/highlow_scan.py`(`_HL_SCAN_SLOTS`/`run_slot`/`_current_slot`+daemon
    join). in-process `_periodic_highlow_scan` 는 타이머 미설치 VM 폴백(타이머 활성
    시 `_highlow_timer_active()` 로 skip — 이중 스캔 방지). 사유: 옛 in-process
    asyncio 만이면 매 배포(봇 재시작)가 진행 중 스캔을 죽여 잦은 배포일에 14:00 류로
    멈춤(사용자 2026-06-19 '배포 중간에도 신고가 그대로 돌게').
  • **경량 동적 보드** = `_periodic_light_board_warm`(180초, env `LIGHT_BOARD_WARM_
    SEC`) — 무버(KR/JP/HK/CN/US)·KR 52주·장전후(US/KR)·NXT·테마/업종을 **render
    함수 호출('방문 시뮬')**로 그 페이지가 읽는 파일캐시를 채움(dashboard_server
    별프로세스지만 같은 파일캐시 cross-process). 시장시간+pause(naver/yf) 게이트,
    `_LIGHT_WARM_RUNNING` 가드. 무버 on-visit SWR=30초지만 서버워머는 네이버
    안티봇 부하 고려 180초(필요시 env 하향, 차단 시 /naverpause 로 워머도 자동 skip).
  • timer 구동(daily_byte·cheongyak·realestate·blog·reddit·dart_feed·portfolio·
    watchlist 30분) + 이벤트구동(분석/screener/업로드) + 00:01 일일 regen 이
    나머지 커버. portfolio/budget=업로드시 변동(라이브가 없어 intraday 무의미),
    GICS=분기·realestate=주간이 의도된 cadence(갭 아님).
- DART 공시 피드 → `dart-feed.timer` 1분(준실시간, 접수→카드 ~1.5분).
  매분 당일 3p 증분 + 시간당 4일 풀스캔 + 일일 콜버짓 15k 자가감속.
  enrich = 사이클당 8건(시간당 480) 점진 백필, 실패 쿨다운(30m/2h/12h),
  성공 항목은 INFO 로그에 회사명(접수번호) 병기. VM 1줄 진단:
  `cd ~/stock && .venv/bin/python -m bot.dart_feed --selftest`.
  **카테고리 정책 (사용자 2026-06-11, 하나씩 검토 중 — 임의 변경 금지)**:
  IR 은 상세 파싱(기업설명회 일시·장소·목적·대상·후원, 사용자 2026-06-13)
  + 캘린더 공급 유지(둘 겸용).
  지분공시는 **대량보유(majorstock) + 임원·주요주주 소유상황(elestock)
  둘 다 enrich** (사용자 2026-06-12 '지분공시도 다 파싱' — 옛 대량보유-
  only 폐기. elestock 100% 비율 환각 가드 유지, 소유상황 카드는 detail
  파싱된 것만 대시보드 노출 — 제목만 홍수 방지). 감사보고서 등 generic
  '보고서' 분류분은 구조화 소스 없어 제목만이 정상. 나머지 카테고리
  정밀화는 사용자가 순차 지시할 때만 변경. **배당 = 주주환원에서 분리된
  독립 카테고리** (사용자 2026-06-12; 공정공시 wrapper 의 주주환원정책/
  배당 본문은 `_fair_disclosure_category`+`_upgrade_category` 가 실적→
  주주환원/배당 승격 — 'KG 5사 왜 실적이야' fix). **칩 순서 = 사용자 지정
  고정**(전체·중요·미파싱·실적·계약·신규시설투자·주주환원·자금조달·배당·
  지분공시·리스크·소송·회사구조·자산양수도·조회공시·IR — `_DART_
  CATEGORIES`). 🔥 중요 **10규칙** (+배당 시가배당률 3%↑ 2026-06-13, +활동주의/
  경영권 분쟁 2026-07-04 사용자 승인 — open-proxy-mcp 검토 채택: 대량보유 보고사유·
  보유목적 '경영참가/경영권'(부정 '없' 라인 제외, 지분공시 한정) · 제목/파싱
  제목·사건의 '주주제안'/'주주총회소집허가'. 의결권대리행사권유는 회사 routine
  이라 제외): 소각·자사주 취득 **발행주식 3%↑만**
  (신도기연 1.21% 류 미발화), 상장폐지·실질심사·관리종목(지정우려/지정/해제, 2026-07-05) 항상('상장폐지시까지'
  기간 boilerplate 는 미발화 — 에코마케팅 병합/분할 기계적 정지 오발 fix),
  **유상증자 시총대비 5%↑**(FSC 시총, 정정 제외), **신규시설투자 자기자본
  15%↑**(20→15 + % 없는 '39.00' 표기 미발화 버그 fix, 2026-06-13), **손익구조 변동은 매출액
  +20%↑ OR 영업이익 +30%↑ OR 영업익 흑자전환**(증가만·적자전환 제외,
  And 아님 Or, 제목만으론 미발화 — detail 증감률 게이트, 사용자 2026-06-12).
  **파싱 배치 완료 (2026-06-12, 사용자 예시 ~37양식 12묶음 — '이제 파싱
  예제는 다 끝났어')**: 소송(제기/판결 + 기타경영사항 소송성 승격) ·
  리스크(거래정지/해제·불성실·실질심사·해산·회생·생산중단/재개·SPAC상폐)
  · 조회공시(요구/풍문해명/시황답변 — ' : ' 구분자·'오후 12:00까지' 시한
  변형 포함) · 공정공시(물량형 잠정실적·수시·장래계획) · 투자판단(제목
  기반 카테고리 승격) · 확인서/종료보고서/자산보관/투자설명서정정/특수
  관계인담보/감자/발행가액/합병/영업양수도/분할철회/최대주주변경/공개
  매수결과/대량보유 doc 폴백 등 — 전부 순수 `_*_lines(txt)` + 회귀
  테스트. **카드 UI**: 🔥 중요(금색)/⚠️ 미파싱(파랑 점선) 색 구분 +
  카테고리와 **동시선택 필터**(AND 결합). `is_parse_target` = enrich·
  대시보드 단일 소스. **백필 v4**(`backfill_v4_once_if_needed`, marker
  `.dart_feed_backfilled_v4`, startup 1회): ①변경 파서(계약·자사주
  취득/처분)의 기존 detail 재추출 — **성공 시에만 교체**(실패=옛 detail
  유지·doc_fail 오염 0) ②doc_fail 캐시 클리어(신설 파서의 과거 실패분
  즉시 재시도 → run_once 14일 대기열이 8건/분 자연 드레인) ③당월
  재fetch(기타경영사항·투자판단 keep 신설분 소급 수집 + 전체 재분류).
  **잔여 미파싱 일괄 재추출 백필 (`backfill_unparsed_once_if_needed`, marker
  `.dart_unparsed_reextract_v1`, startup 1회, 2026-06-20)**: 파서가 나중 추가됐는데
  옛 항목이 generic detail(시가총액 줄)이라 — detail-부재만 채우는 `backfill_admin_
  issue`(`if detail: continue` 갭)·kw-한정 `reparse_details` 에 안 닿던 잔여 미파싱
  (관리종목·조회공시 등)을 전 카테고리에서 재추출. 미파싱 판정(`_has_meaningful_
  detail` = 시총/주요사업 줄 제외 후 남는 줄 有)과 동일 게이트로 골라 새 detail 에
  meaningful 줄 있을 때만 교체(회귀 0). 콜버짓 가드. 사용자 2026-06-20 '미파싱처리'.
  파서 작성 시 재사용할 클래스: 정정 래퍼 stale-value 는 findall[-1]
  (마지막 출현), 라벨-값 gap 은 greedy(이중 콜론 차단), 압축표기 stop
  은 `\d{1,2}\s*\.`(뒤 공백 불요), 날짜 vs 항번호 구분은 stop 에 라벨형
  한글 run 요구, 머리말 오캡처는 번호 라벨 필수로 차단.
  **coverage 감사 정리 + 보강후보 분류 (2026-06-13)**: (a) `--coverage-audit`
  의 '보강 후보'에서 발행·등록 서류(투자설명서/일괄신고/증권신고서 — 펀드
  투자설명서 동류)를 `_is_noncorp_doc` 로 분리해 '의도된 제외' 그룹으로
  (사용자 '펀드 투자설명서를 미파싱과 구분되게'). `_tally_drop` 도 동일 제외.
  (b) 기타-드롭되던 저빈도 실제 사건을 `_classify_report` 로 카테고리화 —
  벌금/과태료/과징금/중대재해→리스크(단 '소송/청구의소/제소/가처분'은 제외=
  소송 우선, chart_events 'risk' 가 먼저 잡는 과징금소송류는 그 단계서 처리),
  대표이사·대표집행임원·상호·본점소재지 변경/사외이사 선임해임/주식매수선택권
  →회사구조, 기업가치제고(밸류업)→주주환원. 제목 완결형이라 `_TITLE_ONLY_OK_KW`
  로 is_parse_target False(미파싱 색칠 제외) — 드롭→가시 카드화가 목표, 벌금액·
  스톡옵션 수량 구조화 detail 은 라이브 양식 확인 후 후속. 라이브 적발
  2 빈틈(대표이사(대표집행임원)변경 괄호형·재해발생 bare) 후속 fix.
  **최대주주등소유주식변동신고서(고빈도 36/3일) = 처리 완료 (라이브 5예시
  2026-06-13)**: report_nm '소유주식변동' ↔ doc 제목 '최대주주 등의 주식보유
  변동'(공정거래법) 불일치로 기존 '주식보유'+'변동' dispatch 가 못 잡던 것 —
  ①분류 지분공시 ②dispatch 에 '소유주식변동' 추가→기존 `_major_holding_
  change_lines` 라우팅 ③파서 broaden(관계열 계열회사 단일→친족/본인/임원/특수
  관계인 전체 + '-'=0 처리 → 희석·집중·전량처분 5예시 전부 파싱) ④`_equity_
  noise`(dashboard) 에 소유주식변동 추가 = 소유상황처럼 **detail 파싱된 것만
  노출**(미파싱 숨김 → 36/3일 홍수 방지). 파서 실패해도 게이트가 숨겨 무해.
  **변형/신규 양식 자율 대응 (사용자 2026-06-12 '조금 다른 건 니가
  판단해서')**: 예시 수집 단계 종료 — 이후 변형은 ①generic **번호 항목
  폴백** `_numbered_rows_lines`('N. 라벨 값' 행 발췌, 비정보 값 스킵,
  6줄 캡, `_extract_generic_document` 가 고정 라벨 부족 시 자동 시도 —
  전용 파서 실패/부재 유형도 카드에 핵심 줄 표시) + `_DOC_TEXT_MEM`
  (프로세스 내 원문 캐시 64 — 전용 파서가 fail 마킹해도 같은 시도 안
  에서 폴백이 본문 재사용) ②Claude 가 위 클래스 가이드로 **사용자 예시
  없이** 전용 파서를 판단 작성 (드롭 분포 `--coverage-audit` 로 후보
  탐지). 사용자에게 양식 더 요청하지 말 것.
- Journal log size → `SystemMaxUse=500M` in `journald.conf` (auto-trim)
- ⛔ **Standard View 폐기 완료** — 2026-06-09 #148 (타이머 7종 disable +
  nav/help 제거) → 2026-06-12 최종 정리: `standardview/` 소스 트리 삭제
  (git 이력 보존), `standardview-backend.service` disable (install.sh
  decommission 블록), `/sv_cost` 명령 + `/usage`·대시보드 sv_usage 합산
  reader + analyzer push hook + `bot/standardview_push.py`/`standardview_
  bridge.py` 제거, `/sites` 링크 제거. **재도입 금지 — 이 아래 과거 SV
  스케줄/audit 기록은 역사 보존용.** (dart_client 의 'StanLee5767/
  standardview 차용' 주석은 코드 출처 표기라 유지.)
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
- ~~Standard View 스케줄~~ (폐기 — 위 ⛔ 참조)
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
  불가 → 사용자 본인 계정 client) 1분 polling (`reddit-insider-watch.timer`,
  2026-06-04 5분→1분 단축 — 사용자 요청, ₩0·rate-limit 안전이라 부담 X)
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
- 자산관리 대시보드 (뱅크샐러드 export → portfolio.html) — 사용자 정책 2026-06-04.
  4개 증권사(NH/삼성/유안타/미래에셋 +메리츠)·예적금·부동산·동산·연금·대출·보험을
  뱅크샐러드 마이데이터 export(비번 zip)로 통합 표시 + 보유주식 NOAH 분석 연결.
  **봇 DM 안 씀(깨끗하게) — 입력은 'Noah의 RAG' 채널 watcher.** 파이프라인:
  • `bot/portfolio_parser.py` — 비번 zip(ZipCrypto, stdlib zipfile pwd, AES 아님)
    → '뱅샐현황' 시트 6섹션 파싱(1.고객정보=PII 의도적 제외). 총자산/순자산은
    export 가 summary 셀을 비워둬 **항목 합으로 산출**. pandas/openpyxl 불필요.
  • `bot/portfolio_resolve.py` — 종목 한글명→티커. 순서: 해외 alias → 국내 pykrx
    역맵 → KR ETF → **네이버 자동완성**(`resolve_via_naver`, ac.stock.naver.com →
    국내 KOSPI/KOSDAQ=.KS/.KQ + 해외 NASDAQ/NYSE=US 티커, 정확일치만·7일 캐시·
    graceful, 사용자 2026-06-20 '자동 분류 + 네이버로 국내도') → **yfinance Search**
    (`resolve_via_yfinance`, top EQUITY·국내 .KS/.KQ·미국거래소만 보수적, 사용자 '야후도')
    → 미매칭 '이름만 표시'.
    페르미=FRMI 등은 `_OVERSEAS_ALIAS` 큐레이션이 우선(운영자 확정). `bot/portfolio.py` — 집계
    모델+저장(~/.tradingagents/portfolio.json, atomic)+요약. `dashboard.
    _render_portfolio_page` → 풀 nav(메인 맨앞·단어 줄바꿈 방지)·순자산 헤더·
    자산배분 도넛(동산=자동차)·💹주식요약·증권사별 카드(국내/해외 비중·수익률 분포·
    TOP/WORST 와 등높이)·수익률 TOP/WORST(종목 중복제거)·보유 테이블(매칭 시 NOAH
    링크)·대출(한도/잔액/금리)·보험 표·🕒마지막 업데이트 시각.
    **증분 정책 (사용자 2026-06-04): '지난 업데이트 대비 순자산·주식평가 ±%' 는
    같은 업로드 날짜면 표시 안 함. 같은 날짜 재업로드는 마지막 것이 현재가 되고
    비교 기준(baseline)은 직전 '다른 날짜' 업로드 그대로 (ingest 가 KST 날짜로
    baseline 승계 관리).**
  • `bot/portfolio_watch.py` — Telethon **userbot**(reddit_insider 와 세션 공유 →
    추가 로그인 0) `portfolio-watch.timer` 2분 polling → `TG_RAG_CHANNEL`('Noah의
    RAG' 채널)의 .zip/.xlsx 문서 픽업 → ingest → 대시보드 갱신 → NOAH 채널 confirm.
    seen-set·첫 run seen 처리(reddit/blog mirror). 비용 ₩0(LLM 0, pykrx/yfinance만).
    nav 맨끝 '💼 자산', help §1, set_my_commands `/portfolio`(조회 전용).
  • .env: `BANKSALAD_ZIP_PW`(zip 비번) + `TG_RAG_CHANNEL`(@username 또는 -100…ID)
    + TG_USER_API_ID/HASH(reddit_insider 와 공유). **NOAH 판정/5거래일 성과
    오버레이 = 증분5 완료** (보유종목 ↔ 분석 아카이브 join, 차트 과거-추천 마커
    헬퍼 `_ticker_analysis_markers` 재사용, read-only·네트워크 0, 보유 테이블에
    'NOAH 판정' 컬럼=최신 분석 판정+해소 시 5거래일 결과). 보유표 **헤더 클릭
    정렬 + 증권사/검색/NOAH 필터**(2026-06-04, `_PF_TABLE_JS`, data-* raw 값).
    종목명·판정은 일반 텍스트색 링크(파란 기본색 제거), 판정은 방향색. 라이브
    가격(추정수량)은 357종목 fetch 비용/rate-limit 으로 보류(스냅샷 이미 최신).
    **'보유 종목' 카운트 = 고유 종목(증권사 중복 제외, 사용자 2026-06-05)** —
    같은 종목을 여러 증권사에 보유하면 export 행이 분리돼 단순 건수가 중복
    카운트. `portfolio._distinct_stock_keys`(매칭=ticker, 미매칭=종목명)로
    산출. build_model 이 `model['distinct_count']` 저장(텔레그램 요약 '주식
    N종목' 용), **대시보드는 렌더 시점에 holdings 로 재계산**(`_render_portfolio_
    page` 가 `_distinct_stock_keys(holdings)`) → 옛 저장 모델(필드 부재)도
    재업로드 없이 다음 regen 에 즉시 반영. 테이블 **행은 원본(증권사별 포지션)
    유지**(증권사 필터 보존)이라 헤더는 'N종목 · M건' 병기(M=건수≥N), note 에
    의미 명시. snapshot 종목수/holding_count(증분 baseline)는 포지션 건수로 그대로.
  • **가계부 (P2, 2026-06-04 완료)** — 같은 export 의 '2.현금흐름현황' 을
    `bot/budget.py` `build_budget_model` 로 월별 수입/지출/순저축/저축률 +
    카테고리 도넛 + 카테고리×월 매트릭스 모델링 → **별도 `budget.html`**
    (`dashboard._render_budget_page`, nav·help 에서 자산 다음). `portfolio.ingest`
    가 자산과 함께 `save_budget` + `regenerate_portfolio_index` 가
    `regenerate_budget_index` 동반 호출(같은 export 산물이라 항상 동기). 수입/지출
    분류는 카테고리명 키워드 추정(빗나가도 매트릭스는 원본 보존). 뱅샐 총계행이
    비어(0)면 카테고리 monthly 합이 canonical(finance 섹션과 동일 정책). PII
    (고객정보)는 파서가 제외.
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
- 블로그 Watcher — 네이버 블로그 새 글 자동 포워드+아카이브 (2026-05-31~).
  `bot/blog_watch.py` `_BLOGS` **다중 블로그 (현재 4개)**: 변화하는 기업을
  찾아서(beatthemkt, categories=관심종목·기업탐방) · 필승(teasky0221, 전체) ·
  의교창(doctordk, 전체) · **천상천하(jkhan012, 전체, 사용자 2026-06-18)**.
  새 블로그 추가 = `_BLOGS` 항목 1개(자동 first-run seed·`/blog`·nav 자동 반영).
  RSS (rss.blog.naver.com/<id>.xml, 브라우저 UA+Referer) 30분 polling
  (`blog-watch.timer`) → 새 GUID 감지. **⛔ LLM 0 · 비용 ₩0 (요약 제거
  2026-06-11, 사용자 재확인 2026-06-18 '원문만·비용 안 드는 방식')** — 텔레그램
  push 는 `_push` 가 **제목 1줄 + 원문 링크만**(Flash·요약 절대 금지, 재도입
  하지 말 것), 본문은 `_fetch_post_text` 가 **HTML→텍스트 파싱만**(LLM 아님)
  해서 blog.html 아카이브 카드에 전문 표시. state(`blog_watch_state.json`)
  per-blog init 으로 첫 run 기존 글 seen 처리(폭주 방지). **대시보드 `blog.html`**
  (사용자 2026-06-11 — 옛 '대시보드 없음' 폐기): 레딧 페이지 mirror(월/일
  collapse + 검색 + 카드 = 제목·원문링크·**본문 파싱 전문**). `regenerate_blog_
  index` — 새 글 후 + startup + 자정 regen. **nav 노출 두 곳 고정: 홈
  (market.html) + NOAH 종목분석(index.html), '한국 수출입' 다음.** 다른 그룹
  nav 추가 금지. 비용 subsystem="blog" 매핑은 잔존하나 LLM 0 이라 항상 ₩0
  (합산 무해). 키 불필요. ⚠️ VM 네이버 접근은 되나 RSS 403 시 헤더/대체
  endpoint 점검 (news client 는 작동 중).
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
  채널 push + **부동산 대시보드(realestate.html)에 합쳐서 표시**(🎟️ 카드 ·
  🗑️ `/api/cheongyak_delete`) + `/cheongyak_cost` 명령(DM+채널)
  ⚠️ 옛 `cheongyak.html` 단독 페이지는 제거(2026-08-20) — regenerate 가 이미
  realestate 로 리다이렉트만 해서 6월 이후 갱신이 멈춘 화석이었고, 같은 레코드를
  두 번 그릴 뿐이었다. 부동산 재생성 때 옛 파일을 unlink 한다.
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
  • `daily-byte.timer` — 평일(Mon-Fri) 19:00 KST(18:00 시도 후 원복 2026-06-13) oneshot → `bot/daily_
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
  • Weekly 종합 **한·미 양국** (2026-05-29 KR → 2026-06-12 US 추가, 사용자
    '주간 한국 미국 다·같은 시간'): KR=`bot/daily_kr_weekly.py`(kind=
    "weekly") + US=`bot/us_market_weekly.py`(kind="us_weekly", 파일명
    `*_us_daily_byte_weekly.json`) — 각각 이번 주 자기 daily 아카이브만
    모아 Pro 종합 → push + 아카이브. KR 로더는 `us_daily_byte` 파일 제외
    (#280 이 같은 디렉토리 공유 — 안 하면 KR 주간에 US 브리프 혼입).
    타이머 `daily-byte-weekly.timer` + `daily-byte-weekly-us.timer` 둘 다
    **일 22:00 KST**. install.sh 등록. 대시보드 badge 📅 한국/미국 Weekly.
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

**배너 양 레이어 확장 (삼성전기 009150.KS 2026-06-04 review)** — 위 IBM fix 는
`override_rating == "Hold"` (analyzer Fix F/G 강제)만 덮었다. 그런데 discipline
은 **in-graph `_enforce_pm_override_discipline`(portfolio_manager.py)** 에서도
강제 가능 — 이 경로는 decision 을 이미 Hold 로 다운그레이드 + 센티넬
`[PM override discipline 자동 보정]` 을 thesis 에 주입한다. 009150 은 PM 1차
Overweight → in-graph 강제 Hold 였는데, decision rating 이 이미 Hold 라 analyzer
Fix F 가 `unanimous==final` 로 미발화 → `override_rating=None` → 옛 오해 배너로
누수(외부 리뷰어가 enum 버그로 오진 → 위험한 enum 하드코딩 제안). fix:
`analyzer._format_summary` 가 `override_rating=="Hold"` **또는 센티넬 present**
시 정확 배너로 라우팅 + `_detect_discipline_forced_hold_banner` 가 센티넬 노트의
'PM 1차 판단 (X)' 를 regex 파싱해 1차 등급 복원(없으면 'Hold override Hold'
무의미 출력). analyzer Fix F 경로(센티넬 없음)는 `_extract_rating` 로 직접 읽어
IBM 회귀 유지. enum lock 여전히 거부. Universal.

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
**Nav 구조 정책 (사용자 2026-06-09 갱신):** **market.html 이 전체
대시보드의 홈(hub)** 역할. 3개 그룹으로 나뉘며, 각 대시보드는 홈 +
자기 그룹 peers 에만 링크:
  - **Group 1 (자산)**: 💼 자산, 📒 가계부 — nav: 🌍 홈 + 그룹 peer
  - **Group 2 (분석)**: 🦉 NOAH 종목분석, 📊 Screener, 🗂️ 도메인 목록,
    🔔 워치리스트, 📨 미국 레딧, 📊 Daily Byte, 📈 Standard View,
    🇰🇷 한국 수출입 — nav: 🌍 홈 + 그룹 peers
  - **Group 3 (부동산)**: 🏠 부동산, 🎟️ 청약 — nav: 🌍 홈 + 그룹 peer
  - **market.html (홈)**: 전체 대시보드 링크 (그룹별 `|` 구분)
**새로 추가되는 대시보드는 해당 그룹의 nav 에만 추가** + market.html
홈 nav 에 추가. 갱신 지점: (a) `bot/dashboard.py` `_render_market_page`
nav (홈 풀 nav) + 해당 그룹 페이지들의 nav,
(b) `bot/telegram_bot.py` `_HELP_TEXT` §9.

**💰 비용 합산 정책 (사용자 2026-06-02):** 메인 NOAH 대시보드 비용 카드
(+ `/usage`)는 **nav 에 링크된 비용-발생 surface 전부의 cost 를 합산**해
총합을 표시하고, 각 surface 는 개별 비용을 자기 카드 / 전용 명령에서
표시. 현재 합산 대상 7개 subsystem (SV 제거 2026-06-12):
  - usage.jsonl (subsystem 태그): 분석 / Screener / Daily Byte / 청약 /
    부동산 / 블로그 — `_compute_stats` 가 subsystem 매핑으로 분리 합산.
  - ~~sv_usage.jsonl: Standard View~~ (폐기 2026-06-12 — reader 제거).
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
  • **chart_translate (차트 공시 제목 CN/JP/TW→KR 번역)** 은 subsystem
    매핑에 없어 default **분석** 버킷으로 폴딩 (`_compute_stats` line
    ~443). 분석 차트 생성 시점(`save_analysis` 내 `build_price_chart`)에
    발생, 영구 캐시라 제목당 1회. 의도된 동작 — 차트 번역은 그 분석의
    부속 비용.

**🧾 개별 분석 비용 stamp (사용자 2026-06-05):** 상세 페이지 메타 라인
(`_render_detail`, "분석일 · 실행 시각 · 소요 · **비용: ₩X**")에 그 분석
1건의 Gemini 비용을 표기. `archive.save_analysis(started_at=)` 가
**build_price_chart(번역 발생) 직후** `usage_tracker.sum_analysis_cost_krw
(started_at)` 로 run 윈도 `[started_at, now]` 의 usage.jsonl 을 합산해
record `cost_krw` 로 저장 → 상세 페이지가 렌더. 합산 대상 = 분석 본체
호출(subsystem 없음) + **chart_translate**(이 분석 차트 번역, 사용자가
포함 요청). 제외 = screener/daily_byte/realestate/cheongyak/blog(별도 timer
프로세스에서 동시 실행 가능 → `_ANALYSIS_COST_SUBSYSTEMS` 화이트리스트로
차단). usage.jsonl **직접 합산**인 이유: 분석 본체는 langchain
`UsageCallback`, chart_translate 는 `_call_pro` 직접 기록이라 둘이 같은
파일에만 공통으로 떨어짐 — in-process accumulator 면 번역비를 놓침. busy
marker 가 /TICKER run 을 직렬화하므로 직전 run 호출(ts < started_at)은
윈도에 안 겹침. **옛 기록(이 기능 이전)은 cost_krw 부재 → 비용 라인
생략**(usage.jsonl 30일 회전이라 소급 불가, "앞으로" 만 표기). additive·
graceful (합산 실패해도 archive write 안 깨짐). universal — 전 시장 동일.

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

## Gemini 백엔드 — AI Studio ↔ Vertex AI (env 토글, 2026-06-19)

사용자가 Google Gemini 를 **AI Studio(Gemini Developer API) → Vertex AI**
로 전환 (GCP project `gen-lang-client-0325676393`, region `us-central1`,
VM SA `roles/aiplatform.user` + ADC + `aiplatform.googleapis.com` 활성).
코드는 **단일 env 토글**로 두 백엔드를 오감 — `GOOGLE_GENAI_USE_VERTEXAI=
true` 면 전 봇이 Vertex(ADC, API 키 불요), 미설정/false 면 기존 AI Studio
(`GOOGLE_API_KEY`). 토글 off 가 기본이라 **안전한 롤백**(한 줄로 복구).

두 surface 가 따로:
1. **google-genai SDK** (`genai.Client`) — `bot/genai_factory.py` 가 단일
   factory. `make_client(api_key)` 가 vertex 면 `Client(vertexai=True,
   project, location)`, 아니면 `Client(api_key=)`. `effective_key()` 는
   vertex 모드에서 **truthy sentinel `"vertex-adc"`** 반환 → 전 봇의
   `if not api_key:` readiness 가드가 키 없이도 통과(ADC 인증이므로).
   적용: `screener._call_pro`(피드 11모듈 공용)·`screener_gics_check`·피드
   모듈 11개 guard. `gemini_cache_manager` 는 vertex 시 **명시적 캐싱 보류**
   (Vertex 캐시 규약 다름·cached_content bind 미검증, ~5% 비용↑ graceful).
2. **langchain Chat** (`ChatGoogleGenerativeAI`) — AI Studio 전용 패키지라
   Vertex 는 **별도 `langchain-google-vertexai`(ChatVertexAI)** 필요(신규
   의존성). `TradingAgents/.../google_client.py` 가 `_use_vertex()` 분기
   (`_get_vertex_llm`/`_get_aistudio_llm`, thinking_budget 매핑 공유 +
   구버전 reject 시 graceful fallback). 트레이드 봇은 `trade/llm_insights.
   make_chat()` 단일 factory (`_llm_ready()` 가드, company/period_report 공용).

VM 적용(1회): ① 양 venv 에 `pip install langchain-google-vertexai`
(`~/stock/.venv` + `~/stock-trade/.venv`) ② `.env` 에 `GOOGLE_GENAI_USE_
VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION=us-central1`
(GOOGLE_API_KEY 공백 가능) ③ 서비스 재시작 ④ probe:
`~/stock/.venv/bin/python3 -c "from google import genai; c=genai.Client(
vertexai=True,project='gen-lang-client-0325676393',location='us-central1');
print(c.models.generate_content(model='gemini-2.5-flash',contents='ok').text)"`
(client 를 변수로 — 체인 금지, 아래 운영 노트 참조).
회귀 테스트: `tests/test_regression.py::TestGenaiFactoryVertexToggle`(10).
⚠️ 비용: stock + 봇이 같은 GCP 크레딧 공유 → 결제 예산 알림 권장.

**VM 검증 (2026-06-19 실측, 토글 ON)**: 양 경로 라이브 확인 — screener/피드
`genai.Client(vertexai=True)`(stock venv) + 분석가·트레이드 `ChatVertexAI`(양
venv) 정상 응답. ⚠️ **genai.Client 는 반환값을 변수로 잡아 쓸 것** — 체인
(`genai.Client(...).models.generate_content(...)`)은 임시 Client 가 GC 되며
httpx transport 를 닫아 "Cannot send a request, as the client has been closed"
발생(우리 코드는 `client = make_client(...)` 변수형이라 무관 — make_client
docstring 참조). ⚠️ `ChatVertexAI` 는 langchain-google-vertexai 3.2 에서
**deprecated**(4.0 제거 예정, 대체 = langchain-google-genai 의 ChatGoogleGenerativeAI)
— 단 env-only Vertex 경로(GOOGLE_GENAI_USE_VERTEXAI + 키 없음)는 **빈 응답**이라
미신뢰 → 현행 ChatVertexAI 유지(후속 cleanup 보류). ⚠️ langchain-google-vertexai
설치가 google-cloud-aiplatform 를 끌어와 **protobuf 7→6 다운그레이드**(양 venv)
— Gemini 외 이상 동작 시 1순위 의심.

## 트레이드 레퍼런스북 HS↔수출입 매칭 — 핀 + HS검색 (2026-06-19)

`trade/` 레퍼런스북·기업보고서의 HS↔MTI↔회사↔수출입 연계 정밀화 (회사·품목·
HS 어느 쪽으로 검색해도 같은 수출입 숫자로 수렴). 핵심:
- **`trade/mti_companies._THEME_MTI_PIN`** = catch-all HS6 과잉부착 해결.
  HS 는 **앞 6자리만 MTI 에 연결**(`mti_map.hs6_to_mti6`)되므로 `2106.90`
  ('기타조제식료품' → 음료·홍삼·효모·로얄제리 등 10+ MTI 분산) 같은 catch-all
  은 한 코드로도 무관 품목에 회사가 과잉부착됨. → 테마명 → **정확한 MTI6 핀**
  (operator 큐레이션). ⚠️ HS10 자릿수 정밀화는 **무효**(테마코드 다수가 실제
  HSK10 leaf 아님; `2106909099`=로얄제리 오착, 실데이터 검증). 현재 **5핀**:
  건기식→016900·가정용미용기기→829100·탈철기→729010·연마재→290090·카드프린터
  →813390. `build_rows`·`company_report` 공용 `theme_mti6(name, hs)` 가 핀
  우선, 없으면 HS6 자동해석. **새 과잉부착 = `_THEME_MTI_PIN` 한 줄 추가**
  (감사: `theme_mti6` 가 4+ MTI 뱉는 테마 = 후보).
- **카메라모듈** = `8529.90`(음향·LCD·OLED 혼재) → **`8517.79`**(스마트폰부품
  MTI 812820). 식이보충제·카메라 등 '실무 통관 분류 ≠ 이론 HS' 케이스.
- **HS코드 검색** = `company_report.gather` 가 HS 입력(점/대시·8~10자리; 6자리
  bare 는 주식코드라 제외, HS6 는 `8517.79` 점표기)을 `_hs_code_search` 로
  라우팅 → HS6→MTI6 품목별 수출입(YoY/MoM)+관련 상장사 (item-mode 렌더 재사용).
- 회귀: `trade/tests/test_company_report.py::test_hs_pin_and_hs_code_search` +
  `test_theme_hs_camera_and_hs_list`. 새 핀 추가 시 theme_mti6 회귀 동반.
- 상시: '🔍 미매칭 알림 후보' 패널(레퍼런스북)이 어느 품목에도 안 붙는 알림
  회사를 빈도순 노출 → 새 데이터 들어올 때 별칭/매핑 추가.
- **catch-all '기타' MTI 수출입 누락 해소 (2026-06-19 MLCC)**: `industry.
  aggregate_by_mti` 가 산업='기타'(CATCH_ALL) MTI 를 통째 제외 → 고정식축전기
  (MLCC=MTI 833310) 등 catch-all 품목이 `load_mti_stored(by_mti)` 에 없어 별칭
  검색 시 관련기업만 나오고 수출입 숫자 영구 누락. fix = `industry.load_mti_
  heatmap(conn)` 이 `customs_heatmap_leaf`(전 leaf, 기타 제외 없음)를 MTI6 로 묶어
  최신월 exp/imp + YoY·MoM(3-포인트라 ΔYoY/ΔMoM None, precomputed 'metrics')
  반환 → `company_report.gather` 가 `by_mti`/`by_imp` 결손분만 `setdefault` gap-fill
  (named-industry full-series 노드 보존). 기존 테마 HS→MTI→노드 경로가 자동으로
  실제 품목명·수출입 표시. universal(전 catch-all 별칭). `_item_matches` (2b)도
  `theme_mti6`(핀 존중)으로 통일. 결과 품목명 클릭 → 실제 품목명 재검색(dashboard
  rb 위젯 위임 핸들러 `data-rb-search`). 회귀 `CatchAllHeatmapFallbackTests`.
- **회사명 오타(타이포) 교정 = 단일 레지스트리 + 운영자 확인 (사용자 2026-06-19
  '타이포 교정 저장 + 앞으로 들어오는 것 처리')**: BeOn/텔레그램 알림의 회사명
  오타는 두 곳에 등재 — ① **매칭/표시** = `mti_companies._COMPANY_TYPO`(canon_
  company 가 _COMPANY_CANON 보다 먼저 적용 → chip·dedup·테마/노출 매칭 전부 정확
  표기로 수렴) ② **가격 해석** = `price_provider._NAME_ALIASES`. 현재 3건(에스테
  아이→에스티아이·SK바이오센서→SK바이오사이언스·메티바이오메드→메타바이오메드).
  ⚠️ **fuzzy 자동교정 금지**(오병합 위험) — forward 절차: '🔍 미매칭 알림 후보'
  패널이 새 오타 노출 → 운영자 확인 → 두 dict 에 1줄씩 추가. 회귀 `TypoAndAlias
  BatchTests`.
- **미매칭 후보 공백-결합 토큰 분리 (2026-06-20 'CNT 왜 계속 안없어져')**: `_clean_
  stocks` 는 쉼표만 쪼개 BeOn 1-space 결합('나노신소재 제이오')이 한 토큰으로 남아
  canon('나노신소재제이오')이 surfaced 와 영구 불일치 → CNT 등 미매칭 잔존. 회사 뷰
  (`_company_alert_items`)는 `split_names`로 쪼개 정상이었는데 `unmatched_candidates`
  만 안 쪼갰음 — 동일하게 `split_names` 적용('JYP Ent.' 마침표류는 통째 보존). 회귀
  `test_space_joined_companies_split`. ⚠️ 두 알림-소비 표면(회사 뷰·미매칭)은 항상
  같은 split/canon 파이프를 타야 한다.
- **미매칭 알림 후보 배치 매칭 (2026-06-19 Unmatched_Items_HS_Mapping.xlsx)**:
  29행 검토 → 기존 테마 회사 추가 5(골심지+경산제지·수산화리튬+이녹스리튬·MLCC+
  다이요유덴·천연가스+대성산업/SH에너지화학·변압기+KP일렉트릭) + 신규 테마 18(NCM·
  무수불산·ECH·포토레지스트·챔버부품·평판디스플레이·광섬유·태양광셀·SEM·SEM부품·
  EDS·이온빔·봉합바늘·캐뉼러·의료기기부분품·줄자·절삭공구·담배). 회사명은 운영자
  확인 표기 그대로(DATA INTEGRITY) — '+' 파편화 복합행은 메인장비 테마로 그룹핑.
- **기준표 = 공식 2026 무협 연계표와 100% 일치 검증 (2026-06-19)**: `trade/data/
  hsk_mti.tsv` 를 운영자 제공 `2026_MTIHSK_..vFF_260507.xlsx`(HSK-MTI 연계표 11,327행
  + MTI코드표 1,294 6단위 품목명)와 행 단위 대조 → HSK/MTI6/산업/품목명 **불일치 0**.
  2026 대규모 개편(반도체·일반기계·이차전지·디스플레이·바이오헬스·철강 코드개편, 연계표
  2,288행 재매핑·MTI 신규290/변경106/삭제251)도 이미 신코드로 반영(구코드 잔존 0).
  테마 74개 HS·핀 5개 전부 공식표에서 해석됨. tsv 갱신 시 이 vFF 파일이 원천.
- **품목별 정밀화 (2026-06-19 운영자 검토)**: ①CNT 도전재 = 파우더(2803.00→228900)
  +슬러리(3824.99) 둘 다 — 단 3824.99 는 농약원제/전해액 분산이라 `_THEME_MTI_PIN`
  으로 228900+290090(기타화학)만 핀. ②평판디스플레이 = 8524.11(LCD,837110) 아닌
  **8524.12(OLED,837120)** — 삼성디스플레이 TV LCD 철수·QD-OLED 집중.
- **DART 매출구성 보강 후보 = 각 품목에 추가 상장사 발굴 (2026-06-19 '더 많이 붙여')**:
  `dart_match.additional_candidates(inv, item_current, min_share=30.0)` 를 **전 품목**
  (미매핑뿐 아니라 이미 매핑된 것 포함)에 돌려 **현재 큐레이션에 없는** DART 매출구성
  매칭 회사를 추림(canon 표기변형 통일). ⚠️ **주력만**(사용자 2026-06-19 '진짜 주력인것만
  — 안맞는게 너무 많다'): 매칭 DART 제품의 **매출비중(share_pct) ≥ 30%** 인 회사만 —
  부수 세그먼트(한국주철관공업 소액 화장품 류) 노이즈 제거. share 미상·저비중 제외.
  너무 적으면 min_share 낮추고 노이즈 많으면 올림. **18일 자동(`trade-bot-
  dart-revenue.timer` = `dart_revenue --refresh`)** — 전수 인벤토리 갱신 직후 `dart_
  revenue._build_reinforce()` 가 갓 빌드된 인벤토리로 계산(사용자 2026-06-19 '18일에
  같이 자동') → `~/.trade/dart_reinforce_candidates.json` 적재 + 운영자 DM(`reference_
  book.reinforce_telegram` 상위 12). `reference_book` 가 그 JSON 읽어 '🧬 DART 보강
  후보' 패널(파랑, 접이식 — 미매칭 노랑과 색구분) 렌더(렌더는 캐시 read 만, 전품목×전
  상장사 매칭은 무거워 18일 전수갱신 + **매일 reparse-stale 타이머에서도 재계산** —
  사용자 2026-06-20 'VM 안돌려도': 인벤토리는 18일 갱신이나 큐레이션 변동·주력 기준
  변경은 매일 자동 반영, 수동 실행 불요). 패널은 **전수**(상위 N 아님) + 검색박스(rfq)·📥 CSV
  (rfcsv → DART_보강후보.csv)로 운영자 전수 검토(사용자 2026-06-19 '전체 확인'). DM 만
  상위 12 요약. **승인 전 후보**(자동 큐레이션 X — 오매핑이 신뢰 깎음;
  승인=`_MAP`/테마 추가하면 reference·report·heatmap-클릭 전 표면 자동 반영). curation_
  candidates(1·11·15·21일)는 **미매핑 전용**으로 환원(보강은 18일로 이동 — 느린 전품목
  매칭을 4회/월 안 돌림). 속도: `suggest_companies_for_items` 가 _canon(정규식)을 품목·
  제품당 1회만 선계산 → 핫루프 순수 문자열(전품목×전상장사 ~2초, 이전 수분). 즉시
  채우려면 VM `python -m trade.dart_revenue --refresh`. 회귀: `test_dart_match::test_
  additional_candidates_excludes_current` + `test_reference_book::test_reinforce_*`/
  `test_samsung_display_dedup`.
- **표기변형 중복 통합 (2026-06-19)**: `_MAP` OLED 의 모회사표기 '삼성디스플레이(삼성
  전자)' ↔ 테마/알림의 '삼성디스플레이' 가 OLED 행에 둘 다 떠 중복(타이포처럼 보임) →
  `_COMPANY_CANON['삼성디스플레이(삼성전자)']='삼성디스플레이'` 로 dedup·매칭 통합.
  이런 '같은 회사 다른 표기' 케이스 = _COMPANY_CANON, 순수 오타 = _COMPANY_TYPO.

## 레퍼런스북 관계후보 자동발굴 (kg-gen 패턴, 2026-06-24)

7개 RAG/KB/KG 라이브러리 검토(사용자 2026-06-23) 결론 = **kg-gen(stair-lab)** 의
핵심 아이디어만 cherry-pick(LLM 구조화추출 → (회사,관계,대상) 트리플 → dedup),
의존성(DSPy) 없이 기존 trade Gemini 인프라 재사용 → ₩0 추가 인프라. 나머지(kotaemon/
ApeRAG/AKB)는 인프라·라이선스 부담으로 skip, OpenKB vectorless RAG 는 후순위.
- **모듈** `trade/kg_candidates.py`. 관계 어휘 = 취급품목/테마/납품/고객/계열. 추출
  모델 = flash 기본(`KG_LLM_MODEL`/`--model` override). graceful(키없음/킬스위치/실패 → []).
- **블로그쪽 적재**(사용자 "트레이드 대시보드말고 블로그쪽" + "둘 다(페이지+채널)"):
  `bot/blog_watch.run()` 이 새 글 본문을 모아 `run_blog_extraction()` 후킹(자동화-first,
  blog-watch.timer 30분 편승 — 별도 타이머 없음). 새 글 없으면 no-op. 킬스위치
  `KG_CANDIDATES_ENABLED`(기본 on). 사이클당 콜 = min(새글수, 12, 일일상한).
- **승인 큐** = `~/.tradingagents/kg_candidates.csv`(gitignore → auto-update git 충돌
  회피, VM 영속). 헤더 회사/관계/대상/근거/출처/추출일/상태. 중복알림 방지=`_new_against_csv`.
- **알림** = 블로그 채널(`daily_kr_flow.push_telegram`, CHANNEL_CHAT_IDS) 1건/배치.
  **대시보드** = blog.html 상단 '🔗 관계후보' 섹션(`_render_kg_candidates_section`,
  상태 배지·등재분 숨김·범례).
- **이관**(운영자 승인분만, CLAUDE.md 정합): 큐에서 상태='승인'+관계='취급품목' 행 →
  `reinforce_approved.csv`(품목=대상, 회사=회사). `ingest_approved()` / CLI `--ingest`.
  ⛔ reinforce 는 repo 체크인 — dev/repo 컨텍스트서 실행·커밋·배포(VM 직접 실행 시 git
  충돌). 테마/납품 등은 자동등재 안 함(운영자 수동). 회귀 `test_kg_candidates`(11).
- **DART 소스 추가(2026-06-24)**: dart_feed 1분 사이클(`__main__` run_once 후)에서 새
  계약 공시(category=계약/제목 공급계약·단일판매·수주) **본문**(document.xml 산문)을
  `kg_candidates.run_extraction(label="DART공시")` 로 추출 — 표 파서·reinforce 가 못
  잡는 공급망(A→B 납품)·고객 관계 소스. seen-set(`~/.tradingagents/kg_dart_seen.json`,
  성공분만 확정·실패는 `_doc_fail_recent` 쿨다운 후 재시도)·사이클당 6건 cap·
  `_BUDGET_HARD` 예산가드·`_DOC_TEXT_MEM` 캐시히트 재사용. 블로그·DART 후보는 같은
  큐·대시보드·채널.
- **비용 surface — LLM 비용만(네트워크/공공 API 제외), 자식 카드 + 메인 합산
  (2026-06-24, 사용자 '각 대시보드 자기 비용카드 + 모두 메인 리포팅')**: kg = Gemini라
  ₩0 아님. **소스별 kind 태그**로 각 대시보드가 자기 비용만 표시(메인은 합산):
  · `trade.llm_usage`: `KG_KINDS=(kg_candidate,kg_blog,kg_dart)` · `is_kg()` ·
    `summary(kinds=/exclude_kinds=)` 필터. record kind = run_extraction(kind=) 인자.
  · 블로그 발굴 = `kind=kg_blog`(run_blog_extraction) → blog.html 카드
    (`_kg_candidate_cost_usd(kinds={kg_blog,kg_candidate})`, kg_candidate=레거시).
  · DART 발굴 = `kind=kg_dart`(dart_feed.extract_kg_candidates) → dart_feed.html 카드
    (`kinds={kg_dart}`).
  · 메인 `_compute_stats`: trade usage.jsonl 의 kind `startswith('kg')` → **'관계후보'
    버킷**(=블로그카드+DART카드 합), 그 외 → **'수출입'**(insight). `_render_stats_panel`
    월 합산 + `cmd_usage`('관계후보(kg)' 줄) 동시. kind 미기록 옛 레코드 → 수출입.
  · 수출입 대시보드(`trade/cost.py collect`)= `summary(exclude_kinds=KG_KINDS)` →
    insight 만(kg 제외, 헤더 💰 줄). **완전성 점검 결과**: 분석(index)·Screener·
    DailyByte·부동산·블로그·관계후보·수출입 7개 LLM surface 전부 자식 카드+메인 합산
    일치. 나머지(레딧·워치·페이퍼·청약뷰·market)는 LLM 0(무료). 네트워크/공공 API
    (DART·관세청·naver·FRED)는 비용 아님. **kg 비용 변경 시: kind 태그→집계 6곳 동기
    (_compute_stats/_render_stats_panel/cmd_usage/blog카드/dart카드/trade cost.py).**

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

## 실거래 실행 — 설계 + E0 페이퍼 엔진 (진행 중, 2026-06-05)

`docs/execution_architecture.md` (v0.1) — NOAH 분석 신호를 실제 주문으로
연결하는 안전 아키텍처·가드레일. 불변 7원칙(fail-closed·paper-first·
human-in-loop·하드 캡 코드강제·idempotency·kill-switch·분석≠실행 분리),
**멀티 브로커 시장별 라우터**(브로커=pluggable 어댑터, 나머지 계층 broker-
무관): KR=KIS(모의투자 ✅)·TW/EU=IBKR(유니버설·paper)·US/JP/HK/CN=KIS해외
또는 IBKR·Toss=오픈 후. ⚠️ 외국인 접근 규제(TW FINI·CN Stock Connect)는
ADR 우회. 단계 E0(페이퍼)→E1(shadow)→E2(live confirm)→E3(bounded).
⚠️ **현행 '알림만(교육)' 스탠스를 뒤집는 결정** — E2(실주문) 전 이
CLAUDE.md 정책 변경 + §12 결정(캡 수치·트리거·horizon)이 선결.

**E0 페이퍼 엔진 = 구현 완료 (리스크 0·돈 0).** `bot/paper_trading.py` —
모의 계좌(시작 ₩10M)·시장가 즉시 체결(glitch-guarded 현재가, chart_data
재사용)·KR(₩)+US($ FX환산)·포지션/평단/실현·미실현 P&L. 순수 ledger
math(`_apply_buy/_apply_sell`, idempotency key·weighted avg·FX-aware
realized)는 단위테스트. `/paper [buy|sell|close|reset]` 텔레그램 명령 +
`paper.html` 대시보드(nav 워치리스트 뒤 🧪) + startup regen. **실주문 없음**
— 나중에 이 위에 KIS/IBKR 어댑터가 '모의 체결→실주문' 교체로 얹힘. 지정가/
next-open PENDING·NOAH 자동신호 = E0.5b+ 증분.

**E0.5a Risk Gate = 완료.** `bot/risk_gate.py`(순수·broker-무관·단위테스트) —
주문 전 하드 캡 fail-closed: 거래당/종목당 비중·동시 종목 수·일일 실현손실
HALT + **kill-switch**(`~/.tradingagents/TRADING_HALT` 파일, 봇 밖에서도 touch
가능). **매수만 게이트, 매도/청산은 항상 허용(de-risk)**. `paper_trading._order`
가 매수 전 `check_order` 호출. `/paper halt`·`/paper resume` 로 kill-switch
토글, `/paper` 요약 + `paper.html` 에 게이트 상태/배너. ⚠️ 캡은 **페이퍼
프로파일**(거래당 60%/종목당 80%/10종목/일손실 30% — 테스트 안 막힘); 실거래
(E2) 전 §5 LIVE 값(2%/10%/5/3%)으로 조이고 LIVE 프로파일 분리. **같은 게이트가
실거래에도 재사용**.

**E0.5b NOAH 판정 → 자동 신호 = 완료.** `bot/paper_signals.py`(분석≠실행
분리 — analyzer 가 아니라 이 레이어가 신호를 주문으로 변환). `on_analysis
(ticker, rating)`: 매수(Buy/Overweight)→자본 5% 사이징 페이퍼 매수+진입 후
**5거래일 자동 청산**(market_calendar), 매도(Sell/Underweight)→보유 시 청산,
보유→무시. **기본 OFF**, `/paper auto on|off` 로 opt-in. 멱등 idem=auto:
{ticker}:{KST date}. Risk Gate 동일 적용. 배선: telegram 단일 분석 완료
직후(summary 전송 뒤) `on_analysis` 호출→알림 채널 전송(graceful, 분석
흐름 무영향). `paper_trading.buy_value`(금액 사이징)+`close_due_positions`
(5거래일 만기 청산, `_periodic_dashboard_refresh` 00:01 KST 에서 호출)+
`_set_horizon`. /paper 요약·paper.html 에 자동매매 ON/OFF + 포지션 🤖~청산일 배지.

**E0.5c 다듬기 = 완료.** (1) **horizon 청산 채널 알림** — `_periodic_dashboard_
refresh(application)` 가 close_due 청산분을 CHANNEL_CHAT_IDS 로 '🤖 자동청산
(5거래일 만기)' 전송(조용히 안 닫힘). (2) **사이징 설정** — `/paper auto size N`
(`paper_trading.set_auto_size`, 1~50% clamp, 계좌 `auto_size_pct` 저장, paper_
signals 가 읽음). (3) **성과 통계** — `paper_trading.trade_stats()`(청산 실현
기반 거래수·승률·평균), `/paper`·paper.html 에 '거래 N회·승률 X%' + 사이징
표시.

**E0.5d 다듬기 = 완료.** **자산 추이 equity 커브** — `paper_trading.snapshot_
equity()` 가 오늘(KST) 자산 1점을 `equity_history`(365 cap, 일별 dedupe)에
기록. `regenerate_paper_index`(매 페이퍼 액션)·reset(baseline)·자정 regen 이
호출 → paper.html 에 **인라인 SVG 폴리라인**(외부 라이브러리 0, 2점 이상일
때, 상승 초록/하락 빨강) + '시작 → 현재' 라인. 커브는 일별로 누적(오늘부터).
**DM 자동신호는 불요** — DM `/TICKER` 은 hint 만, 분석은 채널 전용(on_channel_
post)이라 기존 훅이 전 분석 커버.

**E0.5e 자동 안전성 + 라벨 명확화 = 완료 (AAPL 2026-06-06 review).** (a)
**라벨 명확화** — "🧭 투자 계획" → "**🧭 투자 계획 (리서치 매니저)**"(PM 아님!
외부 리뷰어가 리서치매니저를 PM으로 오독→'합산 파서 고장' 오진). divergence
배너도 "**PM(최종 결정권자)의 자체 판단 — 리서치 매니저·트레이더와 다른 결론**"
으로(섹션 regex `🧭\s*투자\s*계획\b` 는 prefix+\b 라 suffix 무해, dashboard dup
마커는 substring 라 무해). 코드 확인 결과 그 케이스 최종 Hold 는 **PM 의 진짜
결정**(시스템 강제 아님 — `_check_pm_override_required` None 반환), 배너 정확.
(b) **자동 컨빅션 게이트** — `paper_signals._is_contested(summary)` 가 요약에
'방향 상충/시스템 강제/PM OVERRIDE/자동 차단/다른 결론' 마커 있으면 contested
로 보고 **신규 자동매수 보류**(수동 확인 권장). 청산은 de-risk 라 진행(Risk
Gate 철학 동일).

**E0.5e+ 결정 체인 audit 로깅 = 완료 (추적성).** 각 자동매매(매수/보류/청산)
시 `paper_signals._extract_chain` 이 **RM 추천 / 트레이더 액션 / PM 최종 /
분석가 다수 / discipline 발화 / contested** 를 추출(RM·트레이더는 full report
섹션, 다수·banner 는 요약 카드 파싱) → `~/.tradingagents/paper/auto_audit.
jsonl` 에 `{ts,ticker,chain,outcome}` append + 알림 메시지에 compact 체인('RM
Overweight·Tr Buy·PM Hold·다수 보유') + `paper.html` '🤖 자동매매 결정 이력'
표(최근 10). 어느 매니저가 뭘 말했고 왜 그 결과가 났는지 영구 추적.

**E0.5f 지정가 주문(limit) = 완료.** `/paper buy TICKER 수량 @지정가` →
현재가가 조건(매수 ≤·매도 ≥) 미충족이면 `pending` 보관(체결 X), 충족이면
즉시 체결(지정가보다 유리/동일). `_periodic_paper_pending`(30분)가 fill_
pending() 으로 가격 도달 시 시장가 체결(Risk Gate 포함)+채널 알림+regen.
`/paper pending`(목록)·`/paper cancel id|TICKER|all`. `paper_trading.
_marketable`/`fill_pending`/`cancel_pending`/`list_pending`. paper.html 에
⏳ 지정가 대기 표. 멱등 idem 으로 중복 대기 차단. dynamic comps(mega-cap→
Mag-7)·라벨 명확화 별도 커밋. trader pct-only 는 기존 가드+auto live-price
로 안전 처리라 보류(프롬프트 변경 회귀 위험).

## 휴장일-인지 거래일 캘린더 (2026-06-05)

`bot/market_calendar.py` — `exchange_calendars`(무료·무키·전 시장
XNYS/XKRX/XTKS/XTAI/XSHG/XHKG)로 `add_trading_days`/`next_trading_day`/
`is_trading_day`. graceful None(라이브러리/시장 미지원). `auto_resolve`
의 `_fetch_returns`(5거래일)·`_fetch_returns_calendar`(N캘린더일) readiness
gate 가 'holding_days+3 캘린더일' 근사 대신 만기 세션 + `_SETTLE_BUFFER_
DAYS`(1) 로 공휴일까지 반영(윈도에 공휴일 끼면 일찍 발동해 부분 결과를
최종 기록하던 오발 차단). None 시 기존 휴리스틱 폴백(회귀 0). requirements
에 `exchange_calendars>=4.5`. 실측 VM 검증(샌드박스 pandas/lib 부재).
실행 서브시스템도 거래일 만기 계산에 재사용.

## Universal guard symmetry (US ↔ KR ↔ JP ↔ TW)

All structural guards added during KR/JP expansion now also cover US,
preventing US-side asymmetric weakness. Reflect this in any future
review:

- **종목별 실제 상·하한가로 glitch 임계 정밀화 (KR, 2026-06-05)** —
  price-glitch 가드가 KR 일일 한도를 하드코딩(±35%)하던 것을, KIS
  `get_current_price` 의 `stck_mxpr`(상한가)·`stck_llam`(하한가)로 정밀화.
  `price_sanity.exact_session_gap(prev_close, upper, lower)`(pure·0.35 캡
  밖 오파싱 방어) 가 정확한 일일 max 변동률 산출 → `agent_utils._compute_
  technical_snapshot` 이 KR 종목에 한해 `snapshot_gap_for_market`(0.35)
  대신 사용(전일종가=현재가/(1+등락률/100), KIS 실패 시 기본값 폴백).
  market-gated(소스가 KR/KIS) 이나 원칙(거래소 일일 한도 있으면 정확값)은
  universal. 실측 VM(KIS 키).
- **KR 차트 라이브 현재가 = KIS 실시간 우선 (2026-06-05)** —
  `chart_data._live_last_price` 가 KR 종목이면 `kis_client.get_realtime_
  price`(2분 캐시, 12h `get_current_price` 와 별개) 를 yfinance fast_info
  (~15분 지연·KR EOD) 보다 우선. 검증(직전 종가 대비) 통과 시 사용, 실패/
  비-KR/creds 부재 시 yfinance 폴백. 호출은 차트층 5분 + KIS 2분 캐시로
  이중 bound. 대시보드 차트 가이드도 동기 갱신. 1분봉 intraday 차트 후속.

- **호가 글리치 가드 전 대시보드 일괄 (2026-06-12, KLAC $2,411/$3.15T
  surfaced — 사용자 'KLA 같은 사례 안 나오게 모든 대시보드')** —
  `price_sanity.quote_glitch_gap(last, prev, 0.75)` 공유 프리미티브
  (순수·단위테스트): 직전 종가 대비 ±75% 초과 = yfinance 분할 미조정/
  stale 글리치 (대형주 일일 ±75% 는 무한도 시장에서도 비현실 → 진짜
  뉴스 갭 보존). 적용 지점: ①검색카드/상세(`stock_snapshot` — 가격→직전
  종가 교체 + 시총 재산출 + ⚠️ 표기) ②관심종목(`market_favorites._refresh`
  — 가격·시총) ③홈 live 등락(`market_overview` — daily 봉 폴백) ④미국
  Daily Byte(지수 snap 교체 + 무버 글리치 종목 제외) ⑤시총 fetch
  (`finviz_client._fetch_mcaps` — prev/last 비율 역보정, 신고저·무버
  카드 공용). 차트/분석 경로는 기존 가드(`last_close_is_glitch` 등) 유지.
  **2차 가드 (KLAC 잔존, 2026-06-12 밤)**: price·previousClose 가 **둘 다
  같은 미조정 기준**이면 상대비교 1차가 장님($2,411 vs $2,398 통과) —
  조정 일봉 히스토리 종가와 교차(±75%)해 교체. 적용: 검색카드/상세 +
  관심종목 + 홈 live(batch 일봉과 교차, 호출 0) + 미국 지수 snap +
  시총 fetch. 비슷한 케이스 전부 = 이 5표면 + 차트/무버(이미 히스토리
  기반이라 면역).
- **현재가 글리치 가드 (price-glitch HARD GUARD, 2026-06-04)** —
  `bot/price_sanity.py` (의존성 경량·순수·단위테스트 가능). yfinance 가 한
  종목의 한 시점 가격을 잘못된 분할/조정 기준·stale·junk 로 반환해 phantom
  폭락/급등을 만드는 클래스. 파크시스템스 140860.KS 2026-05-20 surfaced:
  분석·차트 둘 다 현재가 ₩163,700 (실제 ~₩280-300K, **52주 최저 ₩205,000
  미만 = 물리적 불가능**, 10EMA 대비 -43%) → 시장·펀더멘털·트레이더 전원이
  phantom 폭락 위에 Sell/Underweight 결론을 쌓음. 사용자 정책 2026-06-04:
  **교체(직전 정상 종가) 우선, 안 되면 차단·중립**. 적용 (전부 universal):
  - **분석 SSoT 교체** — `_compute_technical_snapshot` (agent_utils) 가
    `last_close_is_glitch` 로 마지막 일봉 종가가 직전 종가 중앙값 대비 시장
    일일 한도(KR/JP/CN/TW ±35%, 그 외 ±50%) 초과면 그 봉을 드롭 → 현재가·
    10EMA·50/200SMA·RSI·MACD·볼린저 전부 직전 정상 종가 기준 재계산 + 보정
    배너. 실제 한도 이동(KR ≤±30%)은 절대 안 드롭.
  - **값 블록 차단** — FACTUAL ANCHOR (`_build_factual_anchor`) 가
    `price_outlier_vs_refs` (현재가 < 52주 최저 / > 52주 최고, 또는 50d·
    200d MA 둘 다에서 > ±35%)로 outlier 감지 시 블록-레벨 ⛔ HARD 경고
    (기존 `_52w()` 인라인 ⚠️ 는 LLM 이 조용히 떼버려 무력화됐던 것 보강 —
    이번 케이스가 정확히 그 경로로 누수). 가격 파생 등락·괴리·valuation
    5거래일 dominant 인용 금지 directive.
  - **Comps 마스킹 (④, 티로보틱스 117730 2026-06-04 review)** — 같은
    `price_outlier_vs_refs` 로 현재가 suspect 시 MANDATORY COMPS 블록의
    **subject 행에 ⚠️ 데이터 격리 플래그**(PBR/PSR 등 가격파생 멀티플 신뢰성
    낮음, 절대값 매출/이익 위주 비교). 분석가가 행을 verbatim 복사하면 플래그
    도 함께 전파 → Comps 표에서도 일관되게 노출. ①②③ 후 frozen 케이스가
    드물어 impact 작지만 일관성 보강.
  - **차트(이미 배포 2026-06-04, `bot/chart_data._live_last_price`)** —
    라이브 ~15분 현재가가 시리즈 종가 대비 비현실이면 직전 종가로 폴백.
    셋이 같은 글리치 클래스를 분석·표시 양면에서 차단. ⚠️ no-limit 시장
    (US/EU/HK)은 진짜 뉴스 갭 보존을 위해 밴드가 느슨(2x/0.5x) — magnitude
    만으론 진짜 -40% 뉴스 갭과 글리치 구분 불가하므로, 분석 SSoT 의 series-
    median 교체 + 52주/MA outlier 검사가 1차 방어선.
- **freeze 가드 52주-레인지 게이트 (`should_hard_freeze_technicals`,
  2026-06-04)** — 30% SMA-gap HARD GUARD(`agent_utils` ~4150) 의 **오발**
  보강. split-staleness 는 현재가를 52주 레인지 밖으로 밀어내지만(글리치),
  진짜 하락/급등은 레인지 안이다. 티로보틱스 117730 2026-06-04 review:
  현재가 ₩11,280 ∈ 52주[₩9,820~₩30,900] 의 실제 -41% drawdown 인데
  shares×price↔marketCap divergence(비-가격축, suffix 오조회發 가능) 신호로
  HARD GUARD 가 발화해 기술분석 전체를 freeze. fix: price-axis 신호(split/
  거래정지/시장경보)면 항상 HARD, 그 외엔 현재가가 52주 안이면 SOFT 강등
  (marketCap divergence 는 valuation 데이터 lag 라 기술지표엔 무관). 140860
  류(현재가 < 52주 최저)는 outside-range 라 HARD 유지. `bot/price_sanity.py`
  순수 함수(단위테스트). Universal.
- **52주 범위 안 MA-대이격 ≠ 글리치 (`price_outlier_vs_refs` 정정, 삼성전기
  009150.KS 2026-06-04 review)** — 위 '값 블록 차단'·'Comps 마스킹'이 쓰는
  `price_outlier_vs_refs` 의 MA-이격 분기(50d·200d 둘 다 >±35%)가 **유효 52주
  범위 안에서도 발화**해, 진짜 포물선 급등/깊은 하락을 '데이터 이상'으로
  오발하던 것 차단. 009150: 시뮬 환경 실제 ~14배 급등(현재가 ₩1,716,000 ∈
  52주[₩122,800, ₩2,200,000], PER 162 = 진짜 froth)인데 MA +95%/+317% 이격
  으로 suspect 발화 → 정확한 데이터를 '신뢰성 낮음' 라벨해 진짜 고평가(froth)
  신호를 묻음. fix: MA-이격은 **유효 52주 ref(low+high 둘 다) 부재 시에만**
  발화(docstring 원래 의도 복원). 52주 범위 안이면 큰 MA-이격 = 진짜 강추세지
  글리치 아님 → '데이터 이상' 마스킹 안 함(분석가가 '과열/평균회귀 리스크'로
  정확히 서술). 52주 위반(140860 류)·단일봉(`last_close_is_glitch`)·52주 부재
  MA fallback 은 그대로 글리치 차단. ⚠️ **DATA INTEGRITY ABSOLUTE RULE** 교훈:
  외부 리뷰가 stale 실세계가(SEMCO ~17만원)로 실 시뮬값(₩1.716M)을 10x 글리치
  로 오인 → API/시뮬값이 정답, 사전지식이 stale(동국S&C 100130 precedent).
  magnitude 만으론 진짜 급등과 글리치 구분 불가 → in-range 는 신뢰가 default.
- **마크다운 표 구분선 자동 삽입 (`bot/md_tables.insert_table_separators`,
  2026-06-04)** — analyst LLM 이 GFM 표 헤더 다음 필수 구분선(`|---|---|`)을
  빼먹으면 표 전체가 raw `|...|` 텍스트로 깨지거나 텔레그램에서 bullet 로
  flatten 됨. 티로보틱스 117730 2026-06-04 raw 확인: `요약표\n| 지표 | 현재
  값 | 비고 |\n| 평균 | ... |` (구분선 없음). `analyzer._polish` 파이프라인
  (split-inline-tables 다음)에 스텝 추가 — 헤더 행 다음이 데이터 행인데
  구분선이 없으면 헤더 컬럼 수만큼 `|---|...|` 삽입. valid 표는 idempotent
  (무변경). 경량 모듈로 분리해 단위테스트. Universal — 전 분석 출력.
- **공시→뉴스 fallback (저커버리지 KR/JP/TW/CN/US, 2026-06-04)** — 뉴스 기사
  0건이어도 공식 공시(수주/계약/실적)가 있으면 그게 primary catalyst.
  티로보틱스 117730 2026-06-04: 북미 물류·AMR 수주가 **'공시로'** 나왔는데
  yfinance+Naver 기사 0건이라 news/sentiment 가 통째로 skip. fix: `has_recent_
  news` 가 뉴스 API 모두 비면 시장별 공시 클라이언트(KR DART / JP EDINET /
  TW MOPS / CN AKShare / **US EDGAR 8-K**)의 최근 14일 공시를 확인 → 있으면
  분석 진행(공시 블록은 build_instrument_context 가 분석가에 이미 ungated
  주입 → gate 만으로도 분석가가 공시를 봄). 뉴스 부재 시 "위 공시 블록을
  material news 로 활용하라" directive 는 KR/JP/TW/CN/US 5개 시장 전부에 주입
  (영문 무관 헤드라인 메우기·날조 금지). skip 메시지도 "+ 공시 0건"(KR DART /
  JP EDINET / TW MOPS / US SEC 8-K) 으로 정확히 갱신(공시까지 확인됐음 표기). 데이터
  소스가 시장별이라 market-gated(CLAUDE.md documented data-source 예외) —
  원칙(뉴스 부재 시 공식 공시 fallback)은 universal 5개 시장 전부. ⚠️ 품질
  차등: KR DART / TW MOPS 重大訊息 / US 8-K = timely material events(강함);
  JP EDINET = periodic(분기/연차)+大量保有 (timely 적시공시 TDnet 미포함 —
  Kabutan 이 1차로 timely 커버, EDINET 은 14일 내 실적 filing 보완); CN
  AKShare = dep 무거움·HK lag.
- **Trader Stop Loss 환각 배너 권장 stop (`_flag_trader_price_hallucination`,
  2026-06-04 ①-lite)** — 기존 2층 가드(trader.py 프롬프트 ±15% + Python
  백스톱)가 Entry/Stop/Target 이 현재가 ±tol 밖이면 ⚠️ 경고 + entry 권장
  범위를 emit. MRVL 2026-06-04: Trader 가 폭등 전 옛 주가(~$72, 52주 최저
  $61 부근)에 anchoring 해 $301 종목 Stop Loss $72(-76%) 출력 → 배너는
  정확히 발화했으나 stop 대안은 없었음. fix: Stop Loss 가 flagged 면 배너에
  롱(-5~8%)·숏(+5~8%) 권장 stop 밴드도 함께 제시(구체 fallback). warn-only
  유지(auto-correct 안 함 — 진짜 이동 가능성). 리뷰어의 %/ATR 재설계·요약
  표 JSON 락은 효용 대비 침습/리스크 과다로 채택 안 함(불릿 요약은 readable,
  117730 의 깨진 파이프 표와 다른 클래스). Universal — 전 시장.
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
- **KR suffix normalization** (`bot/market.normalize_kr_ticker_suffix`,
  2026-06-04 LIVE) — pykrx KOSPI/KOSDAQ ticker list (7d 디스크 캐시) 로
  잘못된 .KS↔.KQ suffix 자동 정정. **메인 분석 경로에 wire** — `analyzer.
  analyze()` 진입 즉시 교정해 모든 다운스트림(build_instrument_context /
  뉴스 / yfinance / KIS 시장구분 J·Q / 수급)이 올바른 장부 조회. 티로보틱스
  117730.KS → .KQ (2026-06-04 외부 review surfaced: KOSDAQ 종목을 .KS 로
  조회 → 시세·시총 불일치 + 뉴스 0건 + freeze 오발) + GST 083450.KS → .KQ
  (Hardware 2026-05-29). 순수 코어 `_correct_kr_suffix`(코드 집합 기반,
  단위테스트) + `_kr_market_code_sets`(pykrx + 캐시, creds/pykrx 부재 시
  빈 집합 → graceful no-op). screener 의 옛 "Commit pending" 의도를 main
  path 에서 완수. ⚠️ pykrx 가 KRX_ID/KRX_PW 필요 — 부재 시 정규화 skip
  (원본 유지), 잘못된 suffix 가 들어와도 크래시는 없음.
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

## 🚨 2026-08-09 모델 audit 후속 — Gemini 2.5 전면 퇴역 대응 (데드라인 있음)

사용자 요청("우리 모델에 최적화 꼼꼼히 검토") — WebSearch 외부조사(ai.google.dev/
Vertex 공식 가격표는 샌드박스 차단이라 3자 소스 교차확인) + 코드베이스 gemini-2.5-*
전 호출처 전수조사(에이전트, bot/ + trade/ + TradingAgents/).

### 데드라인 — 협상 여지 없음
- **gemini-2.5-pro / -flash / -flash-lite 전부 2026-10-16 서비스 종료 확정**
  (원래 6/17 예정이었다가 사용자 반발로 연기된 이력까지 GitHub changelog·
  benchr.org 등 복수 소스로 확인). 오늘(8/9) 기준 **68일 남음**.
- NOAH 는 이 3개 모델ID 에 전면 하드코딩 의존 — TradingAgents/ 분석 파이프라인
  (4분석가+5토론+3결정노드), bot/screener.py, 전 Daily Byte류(us_market_daily/
  daily_kr_flow/realestate_brief/cheongyak_brief), bot/chart_translate.py,
  bot/translate.py, bot/governance.py, bot/portfolio_auto_resolve.py,
  trade/llm_insights.py, trade/kg_candidates.py 전부 해당.

### 코드베이스 전수조사 — 티어별 분석 1회당 노드수 × 캐싱 여부
- **pro**: 결정 3노드(RM/Trader/PM, TradingAgents/…managers/) — 유일하게
  컨텍스트 캐싱 적용(`bot/gemini_cache_manager.py`, AI Studio 전용 — Vertex
  는 캐시 API 구조가 달라 명시적 early-return 으로 미지원). + 스크리너/전
  Daily Byte류/trade·llm_insights(전부 grounding, 캐싱 없음, 온디맨드/스케줄).
- **flash**: 4명 분석가(시장/감정/뉴스/펀더멘털, 분석 1회당 실호출 4건) +
  대시보드 번역·이름해석(chart_translate.py/translate.py/portfolio_auto_
  resolve.py) — 이쪽은 영구 파일캐시로 호출이 사실상 0 에 수렴, 실비용은
  분석가 4콜이 지배적.
- **flash-lite**: Bull/Bear 토론 2 + Risk 3인 토론 3 = 분석 1회당 5노드,
  **캐싱 전혀 없음**(원래 최저가 티어라 캐싱 불필요했던 설계) + 종목당
  일일 1회 기술분석 토론(technical_analysis.py, 파일캐시 있음).
- `GOOGLE_GENAI_USE_VERTEXAI` 토글은 실제 런타임 반영(문서용 아님) — 3곳
  독립구현(`bot/genai_factory.py`/`TradingAgents/…google_client.py`/
  `trade/llm_insights.py`). 같은 모델ID 면 AI Studio/Vertex 가격 동일(공식
  기준) — Vertex 는 컨텍스트캐시 미지원이라 전환해도 비용 우회책 안 됨.

### 신모델 가격 (3자 소스 교차확인 — 프리뷰/정식가 혼재로 소스간 편차 있음,
전환 전 공식 페이지(ai.google.dev/pricing) 재확인 필수 — 이 세션에선
샌드박스 차단으로 직접 검증 불가):

| 티어 | 현재(2.5) $입력/$출력(1M) | 최신 대체 후보 | 방향 |
|---|---|---|---|
| pro → Gemini 3.1 Pro(2026-02-19) | $1.25/$10.00 | 소스별 $1.00~2.00/$6~12 | 평평~개선 가능성 |
| flash → Gemini 3.6 Flash(2026-07-21, 3.5 대비 output 17%↓) | $0.30/$2.50 | ~$1.50/$7.50 | 약 5x/3x 상승 |
| flash-lite → Gemini 3.5 Flash-Lite(2026-07-21) | $0.10/$0.40 | ~$0.30/$2.50 | 약 3x/6x 상승 |

**핵심 시사점**: 비용 부담이 제일 큰 pro 티어(결정 3노드)는 전환 영향이
적은데, 정작 **분석 1회당 노드수가 제일 많은 flash-lite 토론 5노드(캐싱
0)**가 청구서에 가장 크게 찍힐 지점.

### 다음 세션 착수 시
1. **10/16 전 마이그레이션 필수** — 여유 갖고 사전 테스트(구조화출력/
   thinking_budget 파라미터 신모델 호환성, 회귀테스트 1400+ 전체 재확인).
2. flash-lite 토론 5노드에 컨텍스트 캐싱 확장 검토 — 가격 상승폭이 제일
   큰 지점을 구조적으로 상쇄 가능(현재 결정 3노드만 캐싱 적용된 인프라
   `bot/gemini_cache_manager.py` 재사용/확장).
3. Vertex 전환은 비용 우회책 아님(가격 동일 + 캐싱 손실) — 검토 불필요.
4. 착수 전 반드시 공식 가격표로 위 표 재검증(VM 은 네트워크 열려있어
   가능 — 이 세션은 3자 소스 교차확인까지만).

## TODO

- **부동산 Byte — 광주 대표구 ✅ 완료 (2026-08-08)**. 대전 유성구(30200)·
  세종시(36110)·광주 남구(12270) 전부 `bot/realestate_client.py` `_REGIONS`
  등록 완료. 광주는 2026-04-28/7-1 광주광역시+전라남도가
  "전남광주통합특별시"(전국 최초 광역통합)로 출범하며 舊 시도코드(29xxx)가
  전부 무효화(실측: 29155 및 인접 29150~29159 전부 0건) — 새 시도코드를
  이 세션 검색으로 못 찾아 최초 보류했으나, 사용자가 code.go.kr 원문에서
  확인해 제공(시도코드 12 신설, 기초단체가 전남 5개시 → 광주 5개구
  [동구12210·서구12240·남구12270·북구12300·광산구12330] → 전남 17개군
  순서로 재배정)한 코드를 VM 라이브 재조회로 검증(방림동·백운동·봉선동 등
  남구 관할동 일치, 5개구 전부 정상 응답) 후 확정. 남은 항목 없음.

- **리서치 액션 다국가 (JP/TW/CN/HK) — 보류 (사용자 2026-06-13 '리서치는
  우선 나중에 To do')**. 홈(market.html) '최근 리서치 액션' 은 현재 한국
  (Naver Finance 기업/산업/전략) + 미국(yfinance upgrades_downgrades)만.
  일본/대만/중국/홍콩 확장은 보류 — **소스 빈약**이 근본 제약: yfinance
  international analyst coverage 가 얇고(대형주만 산발적), Naver 같은
  통합 브로커 리포트 피드의 시장별 등가물이 불명확(JP=みんかぶ/Kabutan
  레이팅은 종목별·집계 피드 아님, TW=鉅亨網/MoneyDJ, CN=동방재부, HK=
  AAStocks — 무료·구조화·전시장 목록형 피드 후보를 먼저 검증해야 함).
  착수 시: 시장별 무료 피드 1개씩 검증(목록형·날짜·브로커·목표가 구조화
  여부) → 되는 것만 탭 추가(실적빌드처럼 graceful). 실적빌드(#330)와 달리
  yfinance .calendar 같은 단일 범용 소스가 없어 시장별 클라이언트 필요.

## 52주 신고가/신저가 정확도 점검 (2026-08-01 사용자 '제대로 잡아내고 있는지')

전 시장(US/JP/CN/HK/TW)이 `finviz_client._compute_highlow_from` 한 곳을 공유한다.
판정 기준 = **당일 intraday 고가/저가가 오늘 제외 251일 극값을 갱신**(장중 한 번
찍으면 종가 무관 — 시장 통용). 점검에서 나온 결함 5건 수정:

1. **JP/HK 유니버스가 조용히 축소돼 있었음(MAJOR·화면 증상의 주원인)** — 원래
   공식 상장목록 전종목(JPX 3,733 / HKEX ~2,600)이었는데 `fe766e3`(2026-07-29
   "Stabilize regression suite", Claude 아님)가 `HIGHLOW_USE_FULL_UNIVERSE` opt-in
   으로 바꿔 **기본값이 peer 102/51종목**이 됐다. "일본 신고가 1종목"의 정체.
   → 기본 ON 복원, 환경변수는 opt-out 으로만. fetch 실패 시 peer 폴백은 유지.
   이를 지키던 테스트(`test_full_market_session_aware_wired`)가 **문자열만 확인**해
   회귀를 못 잡았음 → 동작 기반 테스트 추가.
2. **정지·상장폐지 종목이 매일 신고저로 샘(MAJOR)** — `_proc` 에 **마지막 봉 날짜
   검사가 아예 없었다**. 1년 프레임이 몇 달 전에서 끝나는 종목의 그날 극값이 매일
   '오늘의 신고저'로 보고됐다. 이름 기반 `prune_non_stock` 은 CJK 종목명에 무발화라
   HK/JP/TW 를 못 걸렀다. → **최근 2세션 신선도 가드**(기준은 전 배치 공용 —
   배치별로 잡으면 정지 종목만 든 재시도 배치가 자기 기준을 만들어 뚫림).
3. **휴면 종목이 '비거래 제외' 가드를 통과(MAJOR)** — Volume 을 따로 `dropna` 해
   당일 거래량이 NaN 이면 **과거** 거래량을 집어왔다. 정작 그걸 막으려던
   `if not vol` 가드가 무력화됐고, 거래대금도 '오늘 종가 × 과거 거래량' 오표시.
   → 같은 행에서 읽기(NaN 이면 ValueError → 제외).
4. **컬럼별 개별 dropna 로 날짜 어긋남(MINOR)** — Close/High/Low 를 각각 dropna 해
   길이가 달라지면 `.iloc[-1]` 이 서로 다른 날짜를 가리킬 수 있었다.
   → 행 단위 `dropna(subset=["Close","High","Low"])` 로 정렬.
5. **아웃사이드 데이의 신저가가 숨겨짐(MINOR)** — `elif` 라 신고가·신저가를 같은 날
   찍은 종목은 신고가로만 잡혔다. → 독립 `if` 2개. (옛 테스트가 `elif` 를 문자열로
   못박아 **버그를 고정**하고 있었음 → 정정.)

검증: pandas/yfinance 로 `_compute_highlow_from` 을 실제 실행해 수정 전/후 비교 —
BEFORE 신저가=[정지종목, 휴면종목] / AFTER 신저가=[아웃사이드데이]만, 정상 신고가·
대조군은 불변. 회귀 3종 추가(`test_highlow_excludes_stale_and_dormant_tickers`,
`test_jp_hk_full_universe_is_the_default`, `test_universe_label_reflects_actual_scan`).
화면 라벨에 **실제 스캔 종목 수**를 찍어 커버리지(전종목인지 peer 폴백인지)를 노출.

⚠️ 미검증: JPX/HKEX 공식목록 fetch 는 샌드박스 프록시가 막아 VM 확인 필요. 실패해도
peer 폴백이라 회귀는 없고, 라벨의 종목 수로 어느 쪽인지 바로 판별 가능.

- **한국 52주 신고저 KIS 1콜 경로 — ✅ 이미 구현+VM검증 완료, 결론: 폐기
  (2026-08-09 재확인 — 아래는 stale 이었던 옛 기록을 정정)**. 과거 이 항목은
  "미구현·TR_ID 추정(`FHPST01890000`/`new-highlow`)"으로 적혀 있었으나 **틀린
  정보** — 실제로는 사용자가 2026-07-04 직접 구현+VM검증까지 완료한 상태였음:
  `kis_client.fetch_kr_new_highlow()`(정확한 TR_ID는 `FHPST01870000`, path는
  `/uapi/domestic-stock/v1/ranking/near-new-highlow` — 위에 적혀있던 890000/
  new-highlow 는 둘 다 오기였고 실제 호출하면 404) + `intl_highlow._compute_kr_kis()`
  로 배선까지 끝나 있었음. 단 `_compute_kr_kis()` 자체 docstring 에 **"레거시 —
  `_compute_kr_full` 로 대체, KIS 캡 ~30이 ETF/SPAC 에 잠식돼 실종목이 1~3개뿐"**
  이라 명시 — VM 검증 결과 KIS 신고저 순위 응답이 상한 30개 인근으로 잘려 오는데
  그 중 대부분이 ETF/ETN/SPAC 이라 실제 종목이 거의 안 남는 문제가 실측 확인됐고,
  그래서 현재 `/kr52` 는 의도적으로 전종목 pykrx 유니버스 + yfinance 당일 스캔
  (`_compute_kr_full`, KR/intl_highlow.py:226)으로 운영 중. `_compute_kr_kis()`
  함수 자체는 코드에 보존(미사용)만 되어 있음 — 재활성화 불필요, 재조사도 불필요.
  (교훈: TODO 문서가 실제 코드 상태보다 stale 해지면 다음 세션이 이미 끝난 조사를
  반복함 — REFERENCE 갱신 안 하고 "추정" 항목 방치 금지.)

- **해외주식 KIS 신규 함수 2종 — 원천만 추가, 라이브 미배선 (2026-08-09
  사용자 제공 공식문서)**. 위 국내 조사 중 사용자가 해외주식 문서 2건을
  직접 붙여넣어 review+구현: `kis_client.get_overseas_price_detail(ticker)`
  (HHDFS76200200, `/uapi/overseas-price/v1/quotations/price-detail` —
  PER/PBR/EPS/BPS/52주고저/시총 등, US/JP/HK/CN(SS/SZ) 지원·TW 미지원 —
  기존 `_overseas_excd_symb` 재사용) + `kis_client.fetch_overseas_new_highlow(excd,...)`
  (HHDFS76300000, `/uapi/overseas-stock/v1/ranking/new-highlow` — 거래소별
  신고/신저 랭킹). 필드명은 문서 그대로 매핑(추정 없음), 회귀 8종
  (`TestKisOverseasPriceDetailAndNewHighlow20260809`)으로 파싱 계약 고정.
  ⚠️ 둘 다 **VM 실호출 미검증** + 라이브 파이프라인(intl_highlow/agent_utils)
  **미배선** — 특히 `fetch_overseas_new_highlow` 는 국내 근접판(`_compute_kr_kis`)
  이 위 항목처럼 ETF/SPAC 캡 잠식으로 폐기된 전례가 있어 VM 에서 실종목
  비율 확인 전엔 배선 금지. `get_overseas_price_detail` 은 상대적으로 유망
  (KR D1 Phase 3 와 동일 패턴 — US/JP/HK/CN 펀더 fallback 원천 결측 메꿈)
  — 다음 세션: VM probe 로 실제 응답 확인 → 문제없으면 agent_utils.py 의
  yfinance `.info` PER/PBR/EPS/BPS/52주고저 결측 시 폴백으로 배선 검토.

  **VM 1블록 probe (바로 실행 가능 — 2026-08-09 미리 작성)**. 함수가 이미
  배포돼 있으므로 raw HTTP 가 아니라 함수를 직접 호출해 검증한다(`_get` 은
  실패 시 None 반환 + stderr 경고라, None 이면 로그를 같이 봐야 원인이 보임).
  ⚠️ bare `python -c` 는 `.env` 자동 로드 안 함 → `load_dotenv` 선행 필수
  (안 하면 creds 부재로 전부 None → "API 가 안 된다"고 오진, 실수기록 #12).
  ⚠️ 캐시가 살아있으면 옛 값을 볼 수 있어 `_cache_get` 을 무력화하고 실행.
  ```
  cd ~/stock && .venv/bin/python -c "
  import logging; logging.basicConfig(level=logging.WARNING)
  from dotenv import load_dotenv; from pathlib import Path
  load_dotenv(Path.home() / 'stock' / '.env')
  from bot import kis_client as k
  k._cache_get = lambda *a, **kw: None      # 캐시 우회(신선한 응답 강제)
  print('token:', bool(k._get_token()))
  # ① 현재가상세 — 시장별 1종목씩(US/JP/HK/CN)
  for t in ('AAPL', '7203.T', '0700.HK', '600519.SS'):
      d = k.KisClient().get_overseas_price_detail(t)
      if not d:
          print(f'{t}: None (위 WARNING 로그에 rt_cd/msg1 확인)'); continue
      print(f\"{t}: px={d['price']} per={d['per']} pbr={d['pbr']} \"
            f\"eps={d['eps']} 52h={d['high_52w']}({d['high_52w_date']}) \"
            f\"52l={d['low_52w']} mcap={d['market_cap']} cur={d['currency']}\")
  # ② 신고저 랭킹 — 실종목 비율이 관건(국내판 폐기 사유)
  for ex in ('NAS', 'NYS', 'TSE'):
      rows = k.fetch_overseas_new_highlow(ex, is_high=True)
      if rows is None:
          print(f'{ex}: None'); continue
      print(f'{ex}: {len(rows)}건 | 샘플:',
            [(r['symbol'], r['name'], r['price']) for r in rows[:8]])"
  ```
  판정 기준: ① 은 per/pbr/eps 가 **0 또는 빈값이 아닌 실제 값**으로 오는지가
  핵심(0 으로만 오면 yfinance fallback 으로서 가치 없음 → 배선 불가). 52주
  고저는 yfinance 값과 대조해 자릿수/통화 일치 확인. ② 는 **건수와 ETF/SPAC
  비율** — 국내판처럼 30건 캡에 ETF 가 대부분이면 폐기, 실종목이 충분하면
  `intl_highlow` 의 US/JP 경로 대체 후보로 검토(현재 yfinance 유니버스 스캔
  대비 콜 수가 압도적으로 적음). 추측 보고 금지 — probe 출력 없이 단정 금지.

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

  registry `_VALID_LAYERS = ("L1_TREND", "L2_SECTOR", "L3_INDUSTRY",
  "L4_SUBINDUSTRY", "AD_HOC")`. Layer 누락 → default `L1_TREND` (back-
  compat). `_validate()` 가 layer 값 + binding_layer_taxonomy/regional_
  concentration 최소 2개 (Energy/Real Estate L2 가 공식 L3 2개씩) 체크.

  **L4 Sub-industry themes (2026-06-14 — 전 GICS L4 comprehensive, 272개)**:
  각 L3 의 binding_layer 를 독립 lens 로 분리. **전 48 L3 커버**(사용자
  '실제 GICS L4 레벨 다 넣어'). 구성: (a) Semiconductors L4 8개 = **수작업
  고품질**(`semiconductors_memory`/`_foundry`/`_equipment`/`_logic_ai`/`_logic
  _mobile`/`_analog`/`_eda`/`_specialty`, 전용 별칭 dram/wfe/sic/npu...),
  (b) 나머지 47 L3 × binding_layer = 264개 = **`scripts/gen_l4_modules.py`
  자동 생성**(부모 L3 binding_layer_taxonomy + regional_concentration 재구조화,
  환각 0·실제 종목/카탈리스트 보존, 지역 그룹 split 으로 binding≥2). 새 L3
  추가/수정 시 `python -m scripts.gen_l4_modules` 재실행(idempotent, 자동생성
  마커 '자동 생성(부모 L3' 로 식별·교체, 수작업 semis 보존).
  slug = `<L3slug>_<세부>` (parent prefix 전역 unique). ⛔ **별칭 부모 L3 와
  충돌 금지** — registry first-wins(`semiconductors` 가 `semiconductors_memory`
  보다 먼저 claim) → 자동생성은 generic 토큰(big/diversified/group 등) 별칭화
  제외(junk 방지, 슬러그 직접 접근 기본). `/screener <부모별칭>`(반도체)=L3
  유지. ⛔ **L4 는 `set_my_commands` 메뉴 제외**(Telegram 100/scope cap —
  272개라 필수). 핸들러는 등록 → `/screener_<slug>` 명령 동작, 메뉴만 미노출.
  `/screener_list` 는 **L4 요약만**(272 전수 나열 시 6+ 메시지 스팸 →
  카운트+접근법), 전체 목록은 대시보드 `screener_domains.html`(🎯 `.l4` 파랑
  collapsible)이 전수 enumerate. 고가치 L4 는 semis 처럼 수작업 업그레이드
  가능(자동생성 마커 제거 후 hand-curate → 재생성 시 보존).

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

- **Finnhub API key — ✅ loaded** (2026-06-08). `FINNHUB_API_KEY` in
  `.env`. US 전용: earnings surprise + analyst recommendation trends +
  insider sentiment (MSPR). 무료 60 req/min. `bot/finnhub_client.py`.
  `finnhub_key_ready()` gate — 키 없으면 graceful skip.

- **Alpha Vantage API key — ✅ loaded** (2026-06-08). `ALPHA_VANTAGE_API_KEY`
  in `.env`. 전 시장 universal: 뉴스 sentiment score (기사별 bullish/
  bearish/neutral 정량 점수). 무료 25 req/day + 12h 캐시. `bot/
  av_sentiment_client.py`. `av_key_ready()` gate.

- **Seibro/KSD 외국인보유 — ⏳ 활용신청 필요** (2026-06-08). 기존
  `DATA_GO_KR_API_KEY` 공유. `seibro_client.py` 가 사용하는 서비스 URL 은
  `https://apis.data.go.kr/1160100/service/GetStocSecuritiesInfoService/
  getStockForeignStat` 이다. data.go.kr 에서 이 operation 을 포함하는 정확한
  서비스명이 확인되지 않음 — '금융위원회_주식시세정보' (publicDataPk=15094808,
  `GetStockSecuritiesInfoService` **'k' 포함**) 의 하위 operation 가능성이
  있으나 문서상 4개 시세 op 만 기재. 코드의 `GetStoc` (**'k' 미포함**) 는
  구세대(15043xxx) API 패턴. 사용자가 data.go.kr Swagger UI 에서 '금융위원회_
  주식시세정보' 의 실제 operation 목록 확인 요망. `seibro_key_ready()` gate.
  KR 외국인 보유비율 + 한도소진율 (pykrx KRX-login-free fallback).
  `bot/seibro_client.py`.

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

## 실수 상세 — CLAUDE.md 에서 접은 서사 (2026-09-06 지시서 감사)

CLAUDE.md 의 `## ⛔ 과거 실수` 는 매 턴 전량 주입된다. 항목 평균이 319자에서
1,090자로 자라 **1,200자 넘는 22개가 38,842자**(그 섹션의 20%)를 차지하는데,
그 22개가 받는 상호참조는 다 합쳐 35회뿐이었다(매번 되짚는 상위 14개 법칙은
5,979자로 312~354회). 그래서 **규칙 문장은 CLAUDE.md 에 남기고 서사·실측·독립
리뷰 지적만** 여기로 옮겼다 — 지운 것은 없다. 원문 그대로다.

⚠️ 이 파일은 자동 주입되지 않는다. CLAUDE.md 의 접힌 항목이 `→ REFERENCE §실수 N`
으로 가리키므로, 그 실패모드를 다시 건드릴 때 해당 절을 열 것.

### 실수 #22

22. **JSON 캐시는 dict 의 int 키를 문자열로 바꾼다**(2026-08-18 수급 다기간추이):
    `{5: pp}` 로 만들어 `json.dumps` 로 캐시 → 다시 읽으면 `{"5": pp}` 인데 화면은
    `pds.get(5)` 로 찾아 **캐시가 사는 12시간 내내 전 칸이 `—`** 였다(신선 수집 직후
    에만 보이는 유령 버그라 재현이 안 됐다). 디스크 캐시를 왕복하는 dict 는 int·tuple
    키 금지 또는 **읽는 지점에서 복원**. 캐시 경유 경로는 E2E 로 한 번 태울 것.
23. 진단 스크립트는 **`.env` 를 안 읽는다**(2026-08-18 KRX·FRED 자격증명 오보 2회): `load_dotenv()`
    는 봇 엔트리포인트만 호출 — `python -m bot.scripts.…` 는 `os.environ` 이 비어 있어
    키가 **있는데도** '미설정'으로 보고한다(사용자를 이미 넣은 키 다시 넣게 만들 뻔).
    readiness 체크는 `dotenv_values` 로 **필요한 키만** `.env` 폴백(전체 주입 금지).
    프로브는 값의 **출처**(환경변수/.env)까지 찍을 것.
    ⚠️ KRX 에 넣고 **바로 다음 프로브에서 FRED 를 빠뜨려** 같은 오보를 반복했다 —
    복제로는 못 막는다. `bot/env_keys.py` **단일 헬퍼**로 통일했고 회귀가 raw
    `os.environ.get("*_API_KEY")` 재발을 차단한다(SUPERSEDED 아님 — 새 클라이언트는
    여전히 헬퍼를 써야 하고, 그걸 테스트가 강제한다).

### 실수 #42

42. **폴백이 가리고 있던 버그는 의존성이 깔리는 순간 터진다**(2026-08-20
    exchange_calendars): `add_trading_days` 는 **n≥0 전용**(앞방향 범위를
    `sessions[n]` 인덱싱)인데 내가 `-1` 을 넘겨 왔다. 캘린더가 없어 계속
    폴백을 타서 무증상이다가, 사용자가 `pip install` 하자 KR 이 **2026-09-07**
    (18일 미래)을 '마지막 완결 세션'으로 돌려줬다. 교훈 둘:
    (a) graceful 폴백은 **버그를 숨긴다** — 폴백 경로만 돌던 코드는 의존성을
        깔아 보고 검증할 것(설치 전후 둘 다).
    (b) 방향이 있는 헬퍼(`add_trading_days`)를 반대 방향으로 쓰지 말고
        전용 함수를 둘 것(`last_session_on_or_before`) — n=0 조차 앞방향
        의미라 휴일에 부르면 **미래**가 나온다.
    ⚠️ 그리고 휴일 분기는 **오늘이 거래일이면 테스트가 한 번도 안 탄다** —
    되돌리는 뮤테이션이 실제로 통과했다. 날짜를 고정해 강제로 태울 것.

### 실수 #51

51. **여러 스냅샷을 한 화면에 얹으면 기준시각도 스냅샷 단위로**(2026-08-20
    Market cap 6축): 축을 1.5초 간격으로 따로 긁는데 기준시각을 **분 단위**로
    찍어 여섯 탭이 같은 순간의 스냅샷처럼 보였다. 장이 열린 시장(KR·CN)
    종목은 탭마다 Today% 가 갈렸다(SK Hynix 11.19 vs 11.46). 초까지 찍고
    "축마다 따로 수집" 을 명시. 검산법도 같이 배웠다 — **같은 티커의 같은
    지표를 여러 축에서 대조**하면 외부 자료 없이 열의 의미 차이를 판정할 수
    있다(≤1%p 는 시차, 그 이상은 의미 차이).

### 실수 #61

61. **`max_bytes` 같은 상한이 무엇을 줄이는지 확인할 것**(2026-08-21 원문
    재다운로드): `_fetch_doc_text(max_bytes=)` 는 HTTP 다운로드를 줄이지
    않고 **압축 해제량만** 줄인다 — 3MB→40MB '상한 escalation' 은 같은
    zip 을 통째로 한 번 더 받는 것이었고, 평문↔마크업 전환도 마찬가지였다
    (같은 문서 4가지 요청 = HTTP 4건). 대응: zip **바이트**를 접수번호로
    캐시(상한·모드 무관 공유) + 받은 길이가 상한에 안 닿았으면 **잘리지
    않은 것이므로 재시도 자체를 생략**. 일반화: 캐시 키를 나누기 전에
    "이 파라미터가 비용의 어느 단계를 줄이나"를 먼저 답할 것 — 키를 잘게
    나눌수록 비용이 주는 게 아니라 늘 수도 있다.
    같이 배운 것: 표마다 보고서를 따로 걸으면 같은 문서를 표 수만큼 다시
    받는다(8건→4건). 그리고 그 낭비는 **모듈 테스트가 못 잡는다** —
    `tables_rolling` 을 직접 부르는 테스트는 서버가 그걸 두 번 불러도
    통과했다. 호출 **횟수** 계약은 진입점을 태워 세야 한다(#20).

### 실수 #64

64. **바이트 예산을 문자 길이로 재면 판정이 한쪽으로만 기운다**(2026-08-21
    잘림 오판): `_fetch_doc_text(max_bytes=)` 는 **바이트** 예산인데 반환은
    태그·공백을 정규화한 **문자열**이라 언제나 더 짧다. `len(markup) >=
    cap*0.98` 로 잘림을 추정했더니 한글 문서는 3바이트/자라 **영원히
    '안 잘림'** — 40MB 재시도가 한 번도 안 돌아 대형사 15건이 '섹션없음'
    (원천에 없음)으로 찍혔다. 삼성전자 2,826k자/3,000k바이트=0.94배.
    고친 뒤 실측: 상한 300k·3M 둘 다 원천판정 잘림=True, 옛 추정=False.
    규칙: **상태는 아는 쪽이 말하게 하라** — 수집기가 예산 소진 여부를
    직접 기록(`_DOC_TRUNC`)하고 호출부는 그걸 읽는다. 단위가 다른 두 값을
    비교하는 임계값은 그 자체로 냄새다(#37 임계값이 증상을 덮는 것의 사촌).
    ⚠️ 같은 실행에서: 절 제목 폴백 앵커의 스캔창이 40k 라 '무관표만' 7건이
    13~21개 표를 보고도 최고점 0 이었다 — 정확 앵커와 **절 제목 앵커는 창
    크기가 달라야** 한다(하위 항목이 여럿이라 표가 한참 뒤에 온다).
    그리고 `diagnose` 가 창·앵커 규칙을 따로 적고 있어 넓히자 판정이
    화면과 갈라졌다 → 감사는 **화면이 쓰는 그 선택기**를 부를 것(#35).

### 실수 #66

66. **비용 최적화가 조용히 커버리지를 깎는다**(2026-08-21 수주잔고): "안
    쓰는 회사가 다수니 최신 분기가 비면 즉시 중단"이라는 게이트가, DART 가
    **최신 접수건 문서를 안 주는** 경우(`status=014` 실측)에 나머지 4분기가
    다 있어도 회사 전체를 '미공시'로 만들었다. 옆에 있는 생산능력·제품 표는
    롤링(최신부터 거슬러 첫 성공)인데 여기만 1회였다 — **같은 성격의 항목은
    같은 규율**을 써야 하고, 다르면 그 이유가 주석에 있어야 한다.
    대응: 탐색 예산을 2로(회복 가능·비용 여전히 유계). 일반화: "없으면
    빨리 포기" 최적화를 넣을 땐 **'없다'의 근거가 한 번의 관측인지** 물을 것.
    ⚠️ 같이 배운 것: 그 비용 상한을 검증하는 테스트를 `_BACKLOG_PROBE_N`
    으로 쓰면 그 상수를 99로 올리는 뮤테이션이 **그대로 통과**한다(실측) —
    자기 상수로 자기를 검증하는 tautology. 상한은 **리터럴로** 못박을 것.
    그리고 감사·프로브도 화면과 같은 탐색 규율을 써야 통계가 안 갈라진다(#35).

### 실수 #69

69. **느린 곳을 고치려면 재는 것부터**(2026-08-21 상세 로딩): 어느 탭을
    클릭 로딩으로 뗄지 추측하지 않고 제품 코드에 단계별 계측을 심었다
    (`stock_snapshot._TIMING` + `last_timing()`). 프로브가 수집을
    **재구현하면 제품과 다른 걸 잰다**(#35) — 프로브는 읽기만 한다.
    심자마자 드러난 것: 보조 6종은 **병렬**이라 벽시계에 최대값 하나만
    실리는데, `_enrich_kr` 은 그 **뒤에 직렬로** 돌며 FnGuide·네이버리서치·
    wisereport·KRX·한경컨센서스를 차례로 친다. 샌드박스(전부 실패, 즉시
    반환)에서도 총 1.93초 중 1.76초가 거기였다. 병렬화가 이미 된 곳을
    또 보지 말고 **직렬로 남은 구간**을 먼저 볼 것.

### 실수 #70

70. **PNG 안의 글씨는 '몇 pt'가 아니라 '화면에서 몇 px'로 재라**(2026-08-21
    가독성): 인포그래픽을 11.6in×144dpi(1670px)로 그려 1200px 화면에서 0.72배
    축소하니 8pt 가 **11.5px** 밖에 안 됐다 — 바로 옆 HTML 표는 13px 이라
    같은 카드 안에서 PNG 만 작아 보였다. 대비는 전 항목 6.2:1 이상이라
    색 문제가 아니었다(측정으로 배제). 고칠 때 크기를 하나씩 올리면 비율이
    깨지므로 **도화지만 좁힌다**(11.6→9.8in) — 레이아웃은 데이터 좌표라
    그대로고 글씨만 1.18배가 된다. 단 절단폭(`_CARD_CHARS`)은 도화지와
    **한 쌍**이라 같이 내려야 하고(34→30), 프롬프트 글자수도 따라간다.
    ⚠️ 같이 배운 것 셋:
    (a) 텍스트 오버플로는 **렌더해서 bbox 로** 재야 한다. figure 가 닫힌
        뒤에 재려다 extent 가 전부 예외였고 그걸 삼켜 '오버플로 0'으로
        보고했다 — 대조 대상 0건은 통과가 아니다(#54). savefig 를 가로채
        살아있는 figure 에서 잰다.
    (b) `axis("off")` 인 축도 `get_xticklabels()` 는 **자동 눈금**을 그대로
        돌려준다 — 안 보이는 걸 세면 멀쩡한 렌더가 '패널 밖'으로 오보된다.
        `ax.axison` 으로 가를 것. 본 축 판별도 xlim 이 아니라 `get_position`
        으로(퍼센트 inset 이 0..100 이라 xlim 검사에 걸린다).
    (c) 뮤테이션이 약하면 통과한다 — 막대 라벨을 24pt 로 키워도 안 겹쳐서
        충돌 검사가 통과했다. 40pt 에서야 발화. 가드가 '작동함'을 보이려면
        **실제로 깨지는 값**까지 밀어 볼 것.

### 실수 #71

71. **raw markup 을 훑는 앵커를 평문 정규식으로 쓰면 대형사가 통째로 샌다**
    (2026-08-21 스윕 v4): 표를 원본 구조 그대로 뜨려고 원문을 **태그 포함**
    으로 훑는데 앵커는 `생산\s*및\s*설비에?\s*관한\s*사항` 이었다. 제목이
    `생산 및 <SPAN>설비</SPAN>에 관한 사항` 이면 못 잡는다 — 삼성전자(893만자)
    ·SK하이닉스(925만자)·삼성바이오(834만자)가 **잘리지도 않았는데**
    '섹션없음'으로 찍혔고, 문서가 클수록(=대형사) 제목 서식 태그가 많아
    정확히 중요한 종목부터 빠졌다. 글자 사이에 `(?:<[^>]*>|\s){0,40}` 를
    허용해 해결. 규칙: **무엇을 상대로 매칭하는지**(평문/마크업)를 먼저 답할 것.
    ⚠️ 상한 `{0,40}` 은 필수다 — 무제한 `*` 로 바꾸자 9MB 문서에서 정규식이
    **영영 안 끝나** 테스트가 실패하는 대신 프로세스를 멈춰 세웠다. 시간
    단언은 멈추는 걸 못 잡으므로 상한은 **소스로** 못박을 것(#60 의 사촌).

### 실수 #73

73. **원천의 서식이 한 벌이라고 가정하지 말 것**(2026-08-21 생산 절 앵커):
    DART 「II. 사업의 내용」 목차가 두 벌이다 — 현행 표준 `3. 원재료 및
    생산설비`, 옛 보고서 `생산 및 설비에 관한 사항`. 구 서식만 알고 있어
    삼성전자·SK하이닉스·삼성바이오가 '섹션없음'으로 남았다.
    ⚠️ **진단을 두 번 틀렸다.** 처음엔 잘림(#64), 다음엔 태그(#71)로 봤는데
    둘 다 고쳐도 그대로였다. 결정적 단서는 스윕 표에 이미 있었다 — 같은
    문서에서 `주요 제품 및 서비스` 앵커는 **매칭됐다**(`제품` 표시). 같은
    문서에서 한 앵커는 되고 다른 앵커는 안 되면 원인은 문서(잘림·태그)가
    아니라 **그 앵커 자체**다. 커버리지 감사는 항목을 나란히 놓고 세야
    이런 대조가 보인다(#31 의 이유).
    대응: 앵커 후보를 `_PROD_SPECS` **단일 목록**으로 두고 순서대로 시도,
    결과에 어느 서식으로 잡혔는지 실어 스윕이 서식별로 집계하게 했다 —
    "어느 서식이 남았나"를 다음 라운드에 바로 볼 수 있다.

### 실수 #74

74. **원본의 서식을 그대로 따라가면 원본의 비일관성까지 따라간다**
    (2026-08-21 가동률 표 정렬): DART 표의 `ALIGN=RIGHT` 만 살렸더니
    **한 열 안에서 정렬이 갈렸다** — 한솔아이원스 실측: 같은 열의
    `44,727`(속성 있음)은 우측, `55.7%`(속성 없음)는 좌측이라 숫자를 눈으로
    따라갈 수 없었다. 원본이 스스로 일관되지 않으므로 **우리가 내용으로
    정한다**(숫자·머리행=가운데, 글자=좌측). 일반화: 원본 재현이 목적일 때도
    **표시 규약은 우리 것**이어야 한다 — 원본을 따르는 건 '값'이지 '정렬'이
    아니다(#32 '비교표에 자체계산 금지'의 반대편: 값은 원본, 서식은 우리).
    ⚠️ 같이 겪은 것: 새 정규식 이름 `_CELL_RE` 가 파일 **아래에 이미 있던**
    같은 이름과 충돌해 내 정의가 통째로 무시됐다(group(1) IndexError 로 즉시
    드러남). #59 의 짝 — 이번엔 옛 정의가 새것을 가린 게 아니라 **새 이름이
    기존 이름을 밟았다**. 모듈 상수도 AST 로 중복을 훑을 것.

### 실수 #75

75. **"~로 시작하는 게 하나라도 있나" 식 단언은 옆 칸이 대신 만족시킨다**
    (2026-08-21 헤더 현재가): 현재가가 **거래통화**로 찍히는지 보려고
    `any(t.startswith("HK$"))` 로 쟀는데, 같은 헤더의 **시가총액**이 이미
    HK$ 라 뮤테이션(현재가만 재무통화로)이 그대로 통과했다. 여러 값이 한
    화면에 있으면 단언은 **그 값 자체를 집어야** 한다.
    ⚠️ 같은 실행의 짝: 헤더 렌더 테스트는 payload 를 손으로 만들어 넘기므로
    `build_payload` 가 `price` 를 안 실어도 통과했다(#20 배선 사각). 반환
    dict 의 키를 AST 로 못박아 보완.
    그리고 옛 회귀가 `'_eok(mcap, payload.get("trade_currency") or cur)' in
    src` 라는 **소스 문자열**을 박아 뒀던 탓에, 같은 계약을 지키는 리팩터
    (`_tc` 로 묶기)에 깨졌다 — 동작 테스트로 교체(#19).

### 실수 #80

80. **후보를 '순서대로 시도'하는 선택기는 문턱을 낮추면 첫 후보에서
    멈춘다**(2026-08-21 감사와 화면이 갈라짐): `diagnose` 가 "왜 실패했나"
    를 알려고 `_pick_any(..., min_score=0, ...)` 로 물었는데, `_pick_any`
    는 **첫 앵커에서 하나라도 잡히면 반환**하므로 0점 미끼 표를 집고 멈춰
    뒤 앵커의 진짜 표를 영영 안 봤다. VM 스윕 실측: SK(034730) 가
    `무관표만` 인데 같은 원문에서 파서는 가동률까지 뽑고 있었다 — 감사가
    화면과 **다른 표**를 보고 통계를 낸 것(#35 의 재발이지만 이번엔 캐시도
    재시도 사다리도 아닌 **문턱**이 원인). 대응: 감사도 화면과 **같은
    문턱**으로 먼저 묻고, 실패 사유를 가를 때만 전수 스캔(`_scan_any`)을
    쓴다. 회귀는 개별 문자열이 아니라 **불변식**으로 — "화면에 실리는 판정
    ⇔ 파서가 채택한다". ⚠️ 같이 드러난 짝: 프로브 미리보기가 앵커 목록을
    **따로 적어** 두 앵커만 보고 창도 40k 로 고정한 탓에, 판정이 세 번째
    앵커에서 나온 종목은 '무관표만인데 (표 없음)'이라는 모순 출력이 됐다
    (#24 열거형의 재발). 감사의 모든 경로가 제품의 선택기 **하나**를 불러야
    한다.

### 실수 #81

81. **패널을 맞추는 것과 '보이는 내용'을 맞추는 것은 다르다**(2026-08-21
    TTM 푸터): 차트 패널과 푸터 패널은 둘 다 x=2.5·폭 95 라 코드상으론
    정렬돼 있었는데, 차트는 안쪽 여백(LM=8) 뒤에서 **플롯 프레임**이
    시작하고 푸터는 글자를 6.0 에 둬서 화면에선 좌우가 어긋나 보였다.
    사용자가 "위의 차트랑 균형을 맞췄으면" 이라고 한 지점. 정렬은
    **눈에 보이는 경계**끼리 맞출 것 — 그리고 그 좌표를 두 군데 적으면
    한쪽만 바뀌어 또 어긋나므로 `_PANEL_X`/`_CHART_LM` 단일 상수에서
    읽게 했다(#38). 회귀는 소스가 아니라 **렌더된 축·글자 좌표**로.

### 실수 #82

82. **'없음'만 말하는 진단은 추측을 부른다**(2026-08-21 DATA_GO_KR 키):
    프로브가 "미설정"이라 했는데 VM 의 `.env` 에는 키 줄이 있었다 —
    `env_source` 가 '없음'까지만 말해 주니 그다음(파일을 못 찾았나 ·
    키가 없나 · 값이 비었나 · dotenv 미설치인가)을 사람이 짐작하게
    된다. 갈래를 말하는 `env_why` 를 두고 '없음'일 때만 붙여 찍는다.
    그리고 `.env` 폴백의 `except: pass` 를 로그로 바꿨다(#12). 값은
    절대 안 찍고 **길이**까지만 — 길이는 빈값·오타 판별에 필요하다.
    ⚠️ 부수: 그 사유를 적은 독스트링에 `KEY=` 가 들어가 시크릿 스캔이
    멀쩡한 코드를 물었다. `nosec` 로 덮지 말고 **문구를 바꿀 것**.

### 실수 #83

83. **어순이 반대인 형제 포맷은 이제 네 번째다**(2026-08-21 중국 수출
    종목별): 품목 파서 마커가 `N월 수출 중국` 인데 종목판은 `중국 수출`
    이라 관련성 필터를 통과 못 하고 **저장도 미매칭 알림도 없이** 드랍
    됐다 — 일본(08-16)·말레이시아(08-20)와 같은 사고. 새 나라의 종목판이
    뜨면 또 난다고 보는 게 맞다. 나쁜양파 소스 추가는 `badonion_sources.
    SOURCES` 1줄 + 모듈 1개가 전부이고 nav·재생성·백필은 전부 레지스트리
    에서 파생된다(그렇게 만들어 둔 덕에 이번엔 한 곳만 고쳤다).
    ⚠️ 같이 배운 것: 불릿(`- …`)을 **단어**로 가르면 안 된다. 코멘트가
    '매출'이라는 낱말을 품는 경우가 실재해(DELL "백로그의 매출 전환
    속도") 문장이 금액 칸에 앉는다 — **금액 토큰 유무**라는 구조로 가를 것.
    그리고 회사 간 값 누수 테스트는 **값이 적은 회사를 앞**에 둬야 한다.
    값이 많은 회사가 앞이면 구간 제한을 없애는 뮤테이션이 그대로 통과한다.

### 실수 #84

84. **같은 날 형제 방향이 오면 복제 대신 엔진을 나눠라**(2026-08-21 중국
    수출·수입 종목판): 수출 모듈을 머지한 **한 시간 뒤** "중국수입 7월
    기업도 있어. 수출건이랑 똑같이" 가 왔다. 마커 한 단어(`수출`↔`수입`)와
    금액 라벨만 다르고 문법·표·카드가 전부 같다 — 복제했으면 상관 계열
    처리·불릿 판정이 곧 갈라진다(실측: 복제로 만든 krs·jps·mys 가 이미
    조금씩 다르다). `cn_stock_flow` 공용 엔진 + 얇은 어댑터 2개로 갈랐고,
    레지스트리 입장에선 여전히 독립 소스 2개다(DB·페이지·라우팅 별개).
    ⚠️ 어댑터를 복사하면 **라벨이 수출로 남는다** — '수입인데 수출액'
    회귀를 반드시 둘 것(뮤테이션으로 실제 확인). 그리고 형제 페이지 링크는
    없으면 **비워라**: 억지로 걸면 404 다.

### 실수 #87

87. **레포에 없는 프로덕션 유닛이 두 번 나왔다**(2026-08-21): 08-02 에
    beon 2쌍이 VM 에만 있는 걸 발견하고 문서에 적어 뒀는데, 이번에
    `dashboard-refresh`·`unstored-check`·`health`·`customs-fetch`·`backup`
    **5쌍**이 또 VM 에만 있었다. 최악인 건 `dashboard-refresh` 가
    inbox→DB→화면의 **유일한 경로**이고 `unstored-check` 가 그게 멈춘 걸
    알려줄 안전망이라는 점 — 레포에서 VM 을 재구축하면 데이터가 조용히
    안 오르고 **그 사실조차 안 알려진다**. 규율(표 갱신 잊지 않기)로는
    두 번 다 실패했으므로 `trade.scripts.unit_drift_check` 를 daily audit
    에 붙여 **기계가 대조**하게 했다(양방향: VM 전용 = 미반영, 레포 전용 =
    설치 안 됨 = 한 번도 안 돎).
    ⚠️ 같이 배운 것 둘: (a) 그 회귀("모든 timer 가 인벤토리에 있는가")가
    **첫 실행에서 또 하나**(`dashboard-audit.timer`)를 잡았다 — 새 가드는
    켜자마자 뭔가 잡는 게 정상이고, 아무것도 안 잡히면 가드를 의심할 것(#54).
    (b) 08-02 에 만든 기존 가드가 `["systemctl", …]` 의 **마지막 문자열을
    무조건 유닛명**으로 봐서, 조회형 호출(플래그로 끝남)에 `--plain` 을
    유닛으로 읽고 멀쩡한 코드를 틀렸다고 했다(#47 재발). 인자를 뜯어
    플래그·서브커맨드를 걷어낸 뒤 남는 것만 유닛으로 보게 고쳤고, 진짜
    누락(`trade-bot-ghost`)은 여전히 잡히는지 뮤테이션으로 확인했다.

### 실수 #88

88. **부호 규약은 원천마다 갈린다 — 크기만 써라**(2026-08-21 FCF):
    CAPEX 를 yfinance 는 음수(유출), FnGuide·DART 는 양수로 준다. 부호를
    그대로 더하거나 빼면 **한쪽 시장에서 FCF 가 영업현금흐름의 두 배**가
    되는데 둘 다 '맞는 값'처럼 보여 눈으로는 안 잡힌다(#34 의 부호판).
    정의상 유출인 항목은 `abs()` 로 크기만 쓴다. 그리고 산식은 화면(밸류에이션
    분기표·연간표·분기실적 차트) 셋이 공유하므로 `bot/fcf.py` **한 곳**에
    뒀다 — 복제하면 같은 회사가 화면마다 다른 FCF 를 갖는다(#38).
    ⚠️ 같이 지킨 것: 재료가 없으면 **None**. CAPEX 가 없다고 영업현금흐름을
    그대로 FCF 로 쓰면 설비투자가 큰 회사가 크게 부풀려진다(빈칸이 낫다).
    그리고 두 표(손익·현금흐름)를 합칠 땐 **기간으로 조인**할 것 — 위치로
    매기면 원천이 한쪽만 한 분기 덜 줄 때 전 분기가 밀린다(#46).

### 실수 #90

90. **PNG 가독성은 '크기'와 '선명도' 두 축이고 손잡이가 다르다**
    (2026-08-21 성장동력 카드): 화면 CSS px = `pt/72 × 컨테이너폭 ÷ _FIG_W`
    라 **dpi 가 약분된다** — dpi 를 올려도 글자는 1px도 안 커지고 파일만
    무거워진다. 크기는 `_FIG_W`(좁힐수록 커짐), 선명도는 dpi 다.
    이번 건은 **선명도**가 원인이었다: 컨테이너 CSS 1100px 에 출력 1411px
    인데 사용자 캡처가 1801px(=고DPI) 이라 브라우저가 **확대**해 그렸다.
    dpi 144→216(2117px)으로 배율 2에서도 축소만 되게 했다.
    ⚠️ 크기를 같이 올리려면 절단폭과 한 쌍이다 — 8.5pt 로 30자는 카드 밖
    (VM 실측 100.2 vs 97.5)이라 **자르는 대신 두 줄로** 접었다. 옛 절단은
    LLM 이 쓴 근거를 화면에서 없애고 있었다(`…`).
    ⚠️ 그리고 샌드박스에서 한글 폭을 재려다 **DejaVu 두부**를 쟀다 —
    폰트가 없는 환경의 텍스트 실측은 무의미하다(실수 #25 의 재발).
    소스 주석에 남아 있던 VM 실측을 근거로 썼다.

### 실수 #92

92. **'느리다'는 진단이 아니다 — 이미 고쳐 놓은 걸 또 고치려 들었다**
    (2026-08-21 enrich:KR): 19.1초를 보고 "9개 소스가 직렬"이라고 사용자에게
    말했는데, `_enrich_kr` 은 **2026-06-10에 이미 병렬화**돼 있었다(13블록
    ThreadPool). 같은 답변에서 수주잔고 파서를 "2종"이라 했는데 실제로는
    **10종**이었다. 둘 다 코드를 안 열고 말한 것이다(실수 #12).
    병렬이면 합이 아니라 **최대값 하나**가 벽시계다 — 그걸 짚으려면 task
    마다 이름이 있어야 하는데 없었다(#69). 계측을 심고 프로브가 내부
    분포를 내게 했다.
    ⚠️ 계측하며 진짜 결함이 나왔다: 풀이 8인데 task 가 12라 **두 물결**로
    돌아 벽시계가 `max(1차)+max(2차)` 였다. 풀 크기를 리터럴로 두면 task 를
    더할 때 조용히 물결이 생긴다 → `max_workers=len(tasks)` 로 고정.
    그리고 프로브의 '병렬최대'가 `kr:*` 를 안 걸러 **엉뚱한 항목**을 병목
    으로 보고할 뻔했다 — 판정을 순수 함수(`slowest_parallel`)로 빼서 값으로
    검증(#41).

### 실수 #93

93. **커버리지 숫자만으로 개선 여지를 말하지 말 것**(2026-08-21 수주잔고
    22%): 9/40 을 보고 "파서 형식이 부족하다"고 단정했는데, 그 22%가 원천
    부재(미공시)인지 파서 공백(형식미지원)인지 **알 수 있는 출력이 없었다**
    — 사유는 `_MISS_LOG` 에만 갔고 그마저 미공시류는 건너뛴다.
    `backlog_probe` 가 (값, 사유)를 반환하게 하고 프로브가 분포를 찍되
    **미공시류를 뺀 '개선 여지'** 를 따로 세게 했다. 숫자는 행동으로
    이어질 때만 쓸모가 있다.
    ⚠️ 함수를 (값)→(값,사유)로 바꾸자 **소스 문자열을 슬라이스하던 회귀
    2개**가 깨졌다 — 이번 세션 7·8번째다(#89 에 적은 그 패턴). 둘 다 스텁
    으로 태우는 동작 테스트로 교체했고, 그러자 '미스 기록 제거' 뮤테이션이
    비로소 잡혔다(옛 문자열 검사는 로직이 옮겨간 것만 알았지 기록 여부는
    한 번도 안 봤다).

### 실수 #94

94. **줄 구조를 버리면 줄바꿈이 공백이 돼 낱말이 갈린다**(2026-08-21
    농심 사업부문 `식 품 제 조및판 매 등`): DART 는 좁은 셀에서 한 낱말을
    **여러 줄**로 쪼개 놓는다(`<P>식 품 제</P><P>조및판 매</P><P>등</P>`).
    블록 태그를 그냥 지우면 그 줄바꿈이 HTML 에서 **공백**으로 렌더돼
    원문에 없는 자리에서 낱말이 갈린다 — 원문은 `식품제조및판매 등` 이다.
    `<br>` 로 살려 원본과 같은 3줄로 되돌렸다. 원본 재현이 목적일 때
    값뿐 아니라 **줄 구조도 원본**이다(#74 '값은 원본, 서식은 우리 것'의
    짝 — 줄바꿈은 서식이 아니라 구조다).
    ⚠️ 셀 안 `라 면`·`스 낵` 같은 낱글자 사이 공백은 DART 자체 조판이라
    그대로 둔다 — 지우려 들면 `매출 및` 같은 진짜 어절 경계까지 붙는다.

### 실수 #95

95. **같은 함수의 캐시 버전을 세 번째로 안 올렸다**(2026-08-21 FCF):
    `get_normalized_financials` 는 7일 TTL 디스크 캐시(`qfinN_…`)를 쓰는데,
    CF 계정을 추가하고 **키를 안 올려** 최근 조회분이 CF 없는 옛 결과를
    그대로 서빙했다. 주석에 v4·v5 로 **같은 실패가 두 번** 적혀 있었는데도
    세 번째를 냈다 — 규율로는 못 막는다(실수 #18·#21b).
    ⚠️ 증상이 특이했다: **최근 기간만 ❌, 옛 기간은 ✅**(FY2023·FY2024·
    25.2Q 만 값). 두 종목에서 패턴이 **완전히 동일**해서 캐시가 범인임이
    드러났다 — 회사별로 갈리면 파서, 기간으로 갈리면 캐시다.
    대응 셋: (a) 키를 `_FIN_CACHE_VER` **상수 하나**에서 만든다(리터럴이면
    또 잊는다) (b) 파서가 내는 canonical 키 집합을 회귀가 고정 — 계정을
    더하면 테스트가 깨지고 고치려면 버전을 올려야 한다(#24 회피를 위해
    목록은 매핑에서 도출) (c) 프로브가 그 패턴을 **기계가 지목**하게 했다
    ("최근만 비었으면 캐시부터 의심").
    ⚠️ 같이 고친 것: 누적 dict 에 FCF 가 있으면 4분기 차분이 그걸 차분해
    남기는데, 같은 차분에서 구성요소가 `_src` 불일치로 None 이 될 수 있다
    — 화면엔 재료가 빈칸인데 FCF 만 숫자가 남는다(#33 검산 불일치).
    재료가 불완전하면 **남아 있던 FCF 도 지운다**.

### 실수 #96

96. **한 API 의 같은 필드가 표(sj_div)마다 의미가 다르다**(2026-08-21 분기
    FCF 가 누적): DART `thstrm_amount` 는 손익(IS)에선 '당기 3개월'인데
    현금흐름(CF)엔 3개월 개념이 없어 **누적**(연초~해당분기말)이다. 같은
    dict 에 담아 같이 다뤘더니 분기 FCF 가 통째로 누적으로 떴다 — 농심
    25.2Q 1,145 → 25.3Q 1,634 → 25.4Q 2,008 이고 **25.4Q 가 FY2025 와
    완전히 같았다**(SK 도 동일). 값이 다 '있어서' 신선도·재료 감사는 전부
    ✅ 였고, 화면은 "실적이 좋아지는 중"으로 읽혀 **틀린 줄도 모른다**.
    ⚠️ 캐시 버전(#95)을 고쳐 값이 **채워지자** 비로소 보였다 — 빈칸을
    채우는 fix 는 그다음 층의 오류를 드러내므로 채운 직후 값을 **검산**할 것.
    대응: (a) 계정 집합을 `_CUM_KEYS` 로 명시해 4분기 차분에서 빼고
    `_undo_cumulative_cf` 가 전 분기를 일괄 환산 (b) 직전 분기가 **바로 앞
    분기일 때만** 차분하고 아니면 비운다(#29 빈칸 > 틀린 라벨) (c) 창의
    가장 오래된 분기가 늘 빈칸이 되지 않게 **n+1 개**를 받아 기준으로 쓴다
    (#44a 경계값 요청 금지). 일반화: 한 응답에서 여러 표를 꺼낼 땐 필드
    의미가 표마다 같은지 **원천 문서로** 확인할 것 — "같은 필드니 같은
    의미"는 가정이다. 그리고 이런 오류는 **단조성 + 마지막분기≈연간**
    으로 기계가 잡는다(`bot.fcf.cumulative_smell`) — 시장마다 원천이
    다르므로(KR=DART · 그 외=yfinance) "여기서 고쳤으니 저기도" 대신 **잰다**.

### 실수 #99

99. **창을 안 맞춘 검산은 정상 데이터를 '어긋남'으로 오보한다**
    (2026-08-21 FCF 프로브): "최근 4분기 합 vs 최신 연간"으로 재서
    AAPL 38% · 7203.T 781% 를 ⚠️ 로 찍었다. 두 회사 다 결산월이 12월이
    아니라(9월·3월) 최근 4분기가 최신 연간과 **다른 기간**이었을 뿐이다
    — 창을 맞추자 7203.T 는 633,559−204,078−488,899+238,990 = 179,572 로
    연간과 **한 자리도 안 틀렸다**. 그 오보 때문에 "다른 나라도 누적일지
    모른다"고 보고할 뻔했다. 합을 비교할 땐 **두 창이 같은 기간인지**
    먼저 답하고, 못 맞추면 판정을 내지 말 것(#41 여유로 덮지 않기의 짝).
    부수 소득: 이 판정이 yfinance 분기 현금흐름은 **단일분기**임을 실측
    으로 확정했다 — 누적 오염은 DART(KR) 경로에만 있었다.

### 실수 #102

102. **파생값은 중간 단계에 남기지 말 것 — 그리고 한 값이 두 원천에서
    오면 잰다**(2026-08-21 FCF 3화면 감사): FCF 는 한 종목에 대해 밸류에이션
    표·재무제표 차트·분기실적 인포그래픽 **세 곳**에 실리는데 원천이
    둘이다(KR=DART 현금흐름표 · 전 시장=yfinance). 정의가 갈리면 같은
    회사가 탭마다 다른 FCF 를 갖는데 **어느 쪽도 틀려 보이지 않는다**
    (#34 의 값판). 대응 셋:
    (a) `get_normalized_financials` 가 **분기보고서에도** FCF 를 붙이고
        있었다 — 그 자리의 현금흐름은 누적이라 그 FCF 는 '이 분기'가 아니라
        '연초부터'다. 사업보고서(11011)에만 붙이고, 단일분기 FCF 는 누적을
        되돌린 **뒤에** 만든다. 4분기 차분(`_diff_quarter`)에서도 파생키를
        빼 — 재료는 누적인데 결과만 차분된 값이 남으면 눈으로 검산해 안 맞는다.
        (지금까지 안 터진 건 `_attach_fcf` 가 마지막에 덮어썼기 때문 =
        **순서에 기댄 안전**이라 언제든 깨진다. 구조로 바꿨다.)
    (b) 화면 문구가 코드와 어긋나 있었다 — DART 표 각주가 "원천이 FCF 를
        직접 주면 그 값을 씁니다"라고 적었는데 그건 yfinance 경로 얘기다(#55).
        각 표면에 **출처와 산식**을 박고, 탭 간 값이 다를 수 있음을 밝힌다.
    (c) 사용자가 "숫자를 내가 판단하기 어렵다"고 한 영역은 **감사 도구가
        판정을 대신**해야 한다 — `bot.scripts.fcf_audit` 가 ① 재계산
        ② 교차출처(DART↔yfinance 같은 기간) ③ 표면일치 ④ 회계연도 정렬
        분기합↔연간 ⑤ 누적냄새 를 재고 주간 sweep 에 등록됐다. 감사는
        **화면이 쓰는 그 경로**를 부르고(#35), 대조 0건은 통과가 아니라
        실패로 찍는다(#54).

### 실수 #103

103. **오래 걸리는 진단은 '진행'을 말해야 한다 — 그리고 내가 준 명령이
    그걸 가렸다**(2026-08-21 "여기서 너무 오래 진행이 안되는데..."):
    40종목 스윕은 원래 수십 분인데 **한 줄도 안 나왔다**. 두 가지가 겹쳤다 —
    파이썬 stdout 은 파이프에 물리면 **블록 버퍼링**이고, 내가 안내한
    `| tail -40` 은 정의상 **프로세스가 끝나야** 출력한다. 사용자는 "멈췄나"를
    판단할 방법이 없었다. 오래 걸리는 건 사실이므로 숨기지 말고 **얼마나
    남았는지**를 말할 것: 시작 줄에 대상 수 + 예상 시간, 매 항목에 ETA,
    그리고 `stream_stdout()`(`bot/scripts/probe_progress.py`).
    안내는 `| tail` 대신 **`| tee`**. 가드는 이름 열거가 아니라 "`--limit` 를
    받는 스크립트 = 스윕" 이라는 **구조**로 걸고 예외만 allowlist(#24) —
    켜자마자 `fcf_probe` 를 잡았다(#87a).
    ⚠️ 그 가드가 하마터면 눈이 멀 뻔했다: 이 컨테이너엔 `PYTHONUNBUFFERED=1`
    이 걸려 있어 자식 프로세스를 그냥 띄우면 **버퍼링이 아예 안 일어나**
    `reconfigure` 를 지우는 뮤테이션이 통과했다. 자식 환경에서 그 변수를
    지우고 재야 실제 계약을 본다(#68 → #91 '재는 대상이 맞나'의 환경판).

### 실수 #108

108. **폭이 0 인 컨테이너에 그린 차트는 '데이터 문제'처럼 보인다**
    (2026-08-21 "차트가 왜 잘 안뜨지? … 3년 클릭하고 돌아가야 제대로 떠"):
    헤더·푸터(가격·`242개 봉`·범례)는 별도 DOM 이라 멀쩡히 뜨고 **플롯만
    빈칸**이라 수집 실패로 읽힌다. 실제로는 첫 `render` 가 레이아웃이 잡히기
    전에 돌아 `el.clientWidth === 0` 인 것 — 기간 버튼을 누르면 그땐 폭이
    있어서 제대로 뜬다. 처방 셋: (a) 폭이 0 이면 **다음 프레임으로 미룬다**
    (상한 필요 — 숨은 탭에서 프레임을 계속 먹는다) (b) `applyOptions({width})`
    를 **`fitContent()` 보다 먼저** — 순서가 반대면 잘못된 폭 기준으로 보이는
    구간이 계산된다 (c) `window.resize` 만으로는 부족하다. 폰트 로딩·스크롤바
    등장처럼 **컨테이너만** 바뀌면 안 뜨므로 `ResizeObserver` 로 컨테이너를
    직접 볼 것.
    ⚠️ 원인 규명에서 배운 것: 사용자가 "속도 건드리면서 바뀐 것 아니냐"고
    물었고 실제로 우리 커밋은 `_CHART_JS` 를 **한 줄도 안 건드렸다**(확인
    후 답했다). 다만 로딩이 47초→10초로 빨라지면 첫 페인트가 앞당겨져
    **원래 있던 경쟁을 이기는 쪽이 바뀐다** — "내 변경이 이 파일을 안
    건드렸다"는 무관함의 증명이 아니다. 타이밍을 바꾸는 변경은 타이밍에
    의존하던 버그를 깨운다.

### 실수 #109

109. **범용 낱말을 라벨로 쓰면 '개선 여지' 숫자 자체가 거짓이 된다**
    (2026-08-21 수주잔고 `기말`): 미수집 8건을 "파서 개선 여지"로 보고했는데
    원문 발췌를 찍어 보니 **전부 수주와 무관한 표**였다 — 현금흐름표
    (`기말 현금및현금성자산`)·유형자산 증감(`기말장부가액`)·자기주식
    (`기말수량`)·채무보증(`기초 증감 기말`). `_BAL_LABELS` 의 `기말` 과
    `_OPEN_LABELS` 의 `기초` 는 DART 원문 어디에나 있다. 즉 그 종목들은
    **원천에 수주잔고 공시가 없는 것**이고, 고칠 게 없는 걸 고칠 것으로
    세어 다음 작업 목록이 통째로 틀렸다.
    대응: 범용 낱말로 잡힌 자리는 주변에 **수주 문맥**(수주·도급·계약잔액)이
    있어야 인정. 그리고 그 판정을 `_balance_matches` **공용 매처 하나**에
    두고 파서·진단이 같이 쓴다(한쪽만 좁히면 통계와 화면이 갈라진다, #105).
    회귀는 "라벨 목록을 직접 훑는 곳이 하나뿐인가"로 고정.
    ⚠️ 세 라운드가 걸렸다(#105 범위 → #107 관문 → #109 어휘). 매번 **원문
    발췌를 안 찍었기 때문**이다 — 사유 히스토그램은 '무엇이 많은가'까지만
    말하고 '그게 정말 그건가'는 원문만 답한다. 커버리지 진단은 처음부터
    **표본 원문을 같이 찍을 것**.

### 실수 #110

110. **한 요청을 빠르게 한 병렬화가 여러 요청에선 서로를 느리게 한다**
    (2026-08-21 "전보다 더 걸리는것 같아. 동시에 돌리는게 많아서 그런가?"):
    대시보드는 `ThreadingHTTPServer` 라 요청마다 스레드가 뜨는데, 거기에
    **요청마다 새 풀**을 만들면(#104 미리받기·fsc 일별 프로브·연간 3개년)
    바깥 원천으로 나가는 동시 요청이 그대로 곱해진다. 프로세스 전체에
    상한 하나(`bot.pool.map_bounded`, 기본 24)를 두는 게 맞다.
    ⚠️ **중첩 금지**: 공용 풀에서 도는 작업이 다시 공용 풀에 제출하고
    결과를 기다리면 슬롯끼리 기다려 **교착**한다. 공용 풀은 **말단 팬아웃
    전용**이고 `enrich:KR` 같은 상위 병렬은 자기 풀을 쓴다(회귀로 고정).
    ⚠️ 더 큰 잘못: 나는 **수집기**(`collect_stock_snapshot`)만 재 놓고
    "47초→10초" 라고 보고했는데, 사용자가 기다리는 건 `/api/chart` ·
    `/api/quote?full=1` **요청**이다 — 그 둘은 한 번도 안 재 봤다.
    재지 않은 것을 두고 빨라졌다고 말하면 안 된다(#79·#92 의 재발).
    `do_GET` 이 모든 요청의 소요시간을 `api-timing` 으로 남기게 했다.
    ⚠️ 그 회귀가 하마터면 눈이 멀 뻔했다 — **독스트링**에 `grep api-timing`
    이라고 적어 둔 탓에 로그 호출을 지우는 뮤테이션이 통과했다(#59b).

### 실수 #113

113. **디스크 캐시는 '진행 중인 중복'을 못 막는다**(2026-08-21 `api-timing`
    실측): 같은 요청이 **같은 밀리초에 두 번** 들어오고 있었다 —
    `/api/quarterly USDE 10325ms` 와 `10327ms`, `/api/band 376300.KQ`
    2116/1982ms. 둘 다 바깥 원천을 각각 두드린다. 캐시는 **끝난 뒤에만**
    도와주므로 겹친 구간은 무방비다. 탭을 두 개 열어도 같은 일이 나므로
    막는 자리는 **서버**다(`bot.singleflight.once`) — 하나만 돌리고 결과를
    나눠 주되, 리더의 예외는 팔로워에게도 그대로 전달하고(한쪽만 성공한
    척하면 화면이 갈라진다) 리더가 걸릴 때를 대비해 대기 상한을 둔다.
    ⚠️ 그리고 이 계약의 테스트는 **겹치게 만들어야** 의미가 있다 — 즉시
    던지는 함수로 쓰면 세 스레드가 전부 리더가 되어 팔로워 경로가 한 번도
    안 타고, 그 경로를 망가뜨리는 뮤테이션이 통과한다(실측, #91c).
    같이 배운 것: 내가 최적화한 `enrich:KR`(6초)은 실측 목록에 **없었다**.
    실제 병목은 `/api/lookup_detail` 50초 · `/api/quarterly` 44초 ·
    `/api/chart` 39초다 — 재고 나서야 어디를 고칠지 알 수 있다(#110).

### 실수 #115

115. **열 단위로 정해도 '열 번호'가 틀리면 소용없다**(2026-08-21 케이씨씨):
    같은 `평균 가동률` 열인데 `59.0` 은 좌측, `88.0` 은 가운데였다 —
    앞 두 열이 `ROWSPAN=2` 라 다음 행은 셀이 적게 오는데 **colspan 만 세어**
    열 번호가 통째로 밀렸다. #78(셀→표) → #97(표→열) 로 판정 단위를 두 번
    바꿨는데도 같은 증상이 세 번째로 났다 — 단위를 고쳐도 **좌표계**가
    틀리면 갈라진다. 표를 열로 판정하려면 rowspan 점유를 추적할 것.
    ⚠️ 같이 잡은 것: **머리행이 열의 성격을 정하면 안 된다**. 케이씨씨
    `평균 가동률 (생산실적 ÷ 생산능력)` 처럼 헤더만 긴 **숫자 열**이 있어,
    헤더를 세면 숫자가 좌측으로 밀린다. 성격은 **몸통**이 정하고 머리행은
    그 열을 따른다.
    ⚠️ 뮤테이션이 두 번 통과했다 — 첫 픽스처는 긴 글 열이 없어 전부 ctr
    이었고, 둘째는 밀린 열도 '긴 글'로 잡혀 단언이 그대로 만족됐다.
    **밀렸을 때만 달라지는 칸**(부문·회사가 ctr 인가)을 집어야 잡힌다(#91c).

### 실수 #116

116. **장식용 데이터를 예산 없이 부르면 본 화면이 통째로 멈춘다**(2026-08-21
    차트 공시 마커): `/api/chart` 385초 중 `indicators` 가 380.451초인데 같은
    250봉의 순수 계산은 0.02초였다 — 전부 `fetch_disclosure_events` 의 바깥
    원천 I/O(DART list.json 페이지네이션 + 주요사항보고서 5종 + CN/JP/TW 제목
    번역)였고, 캐시가 **한 겹도 없었다**. 게다가 그 마커는 화면 **기본 OFF**
    다 — 아무도 안 보는 값 때문에 차트가 6분 넘게 멈춰 있었다. 대응 둘:
    (a) 12시간 디스크 캐시 (b) **예산**(8초) — 넘으면 이번 응답은 마커 없이
    내보내고 백그라운드가 캐시를 예열해 다음 요청이 곧바로 받는다.
    일반화: 본문이 아닌 값(마커·배지·요약)을 본 응답 경로에서 부를 땐
    **예산과 캐시를 같이** 달 것. 그리고 차트 payload 캐시(5분)가 있으니
    괜찮다고 보면 안 된다 — 캐시는 **끝난 뒤에만** 도와준다(#113).
    ⚠️ 캐시 키를 차트 기간마다 나누면 거의 안 맞는다(#61) — 비용은 창
    길이가 아니라 페이지 수가 정하므로 창은 시장별로 **고정**하고 호출부가
    보이는 구간으로 거른다. 빈 결과는 '원천에 없음'과 '원천 실패'를 구별
    못 하므로 **짧게만** 믿을 것(길게 믿으면 장애 한 번에 12시간 공백).
    ⚠️ 이 회귀를 sleep 으로 쓰면 단독으론 green 인데 전체 실행에선 red 다
    (실측). 예산 테스트는 시간이 아니라 **신호**(Event)로 잴 것. 그리고
    캐시 헬퍼는 호출 **시작 시점**에 묶이므로 스파이는 먼저 걸어야 한다.

### 실수 #117

117. **전역 dict 하나로 잰 단계 시간은 동시 요청에서 남의 값을 말한다**
    (2026-08-22 탭 3개): 사용자가 세 종목을 한꺼번에 열자 302430.KQ ·
    005880.KS · 294870.KS 수집이 같은 구간에 겹쳤는데, `_TIMING` 이 모듈
    전역 dict 하나라 `chart-timing` 한 줄이 **누구 것인지 알 수 없었다**.
    그 값으로 병목을 짚으면 엉뚱한 곳을 고친다(#35 의 계측판) — 옛
    `indicators=380.451s` 판독도 같은 위험을 안고 있었다. 스레드로컬은
    답이 아니다: `kr:*`·보조 6종은 **풀 워커 스레드**가 기록한다.
    **요청 키**(티커·차트키)로 가를 것(`bot/timing.Stages`) — 워커는 자기가
    무슨 종목을 재는지 안다. 그리고 로그를 **찍는 쪽과 재는 쪽이 같은 키
    생성 함수**를 써야 한다(리터럴을 손으로 적으면 갈려서 로그가 조용히
    사라진다 — 회귀를 AST 구조로 고정).
    ⚠️ 같이 배운 것: 이 세션에서만 **소스 문자열 단언이 아홉 번** 깨졌다
    (`_TIMING[f"kr:{name}"]` 등). 새 테스트에 소스 문자열을 박기 전에
    "이 표현이 바뀌어도 계약은 유지되나"를 먼저 물을 것(#19·#89).

### 실수 #118

118. **정렬 판정의 단위는 열 번호가 아니라 '슬롯'(시작열, span)이다**
    (2026-08-22 IPARK현대산업개발 294870.KS — 같은 증상 **네 번째**):
    `국내/해외` 하위열 때문에 품목 셀이 **전부 `colspan=2`** 였는데, 판정이
    `span != 1` 을 통째로 건너뛰어 그 열은 **한 칸도 안 세어지고** 기본값
    (가운데)으로 굳었다 — 긴 품목명이 행마다 들쭉날쭉. #78(셀→표) →
    #97(표→열) → #115(rowspan 열번호) → 여기. 단위를 고쳐도 **그 단위에서
    빠지는 케이스**가 남으면 같은 증상이 다시 난다.
    ⚠️ 그렇다고 걸친 셀을 전부 좌측으로 두면 `제품 및 상품 등 소 계
    (내부거래 조정 전)`(colspan=3) 같은 요약 라벨까지 좌측이 된다 —
    좌측정렬의 이유는 '같은 열에서 행마다 시작 위치가 어긋남'이므로
    **칸이 둘 이상인 슬롯에만** 적용한다(칸이 하나면 어긋날 상대가 없다).
    ⚠️ 그리고 화면을 못 보는 환경에서 이걸 특정한 방법: 스크린샷의 표
    **구조를 그대로 재구성**해 로컬에서 파서에 태웠다. 1차 재구성(품목이
    span=1)은 정상이 나왔고, 2차(품목이 전부 colspan)에서 재현됐다 —
    "화면이 이상하다"를 원문 없이 고치려 들지 말고 **구조 가설을 코드로
    태워** 가를 것(#12 추측보고 금지의 실행형).

### 실수 #123

123. **아는 것을 화면이 말하지 않으면 사용자가 물어야 한다**(2026-08-22
    LG화학 25.4Q "계정이 불일치한거 좀 봐줄수 있어?"): `_diff_quarter` 는
    **어느 계정끼리** 어긋났는지 이미 기록해 두는데(`_mismatched_accounts`,
    2026-08-18 CJ 때 바로 그 이유로 넣었다) 각주는 "DART 계정 불일치 가능"
    까지만 말했다. 그래서 이게 고칠 수 있는 건지(총액끼리 라벨만 다름)
    원천 한계인지(구성요소가 끼어듦) 사용자도 나도 못 갈랐다.
    수집해 둔 진단 정보는 **표시까지 배선**해야 의미가 있다(#43 의 짝).
    ⚠️ 같이 고친 것: 스냅샷 릴레이가 플래그 이름을 **열거**하고 있어
    `_mismatched_accounts` 가 조용히 빠지고 있었다 — 접두어로 훑는
    `relay_anomaly_fields` 로 바꿨다(#24 의 필드판).

### 실수 #124

124. **손으로 올리는 캐시 버전은 네 번째로 잊었다**(2026-08-22 LG화학
    각주): 각주에 계정쌍을 싣도록 고쳤는데(#123) 화면은 **옛 문구 그대로**
    였다 — PNG 캐시 키의 `_RENDER_VER = "v11"` 이 리터럴이라 bump 를 잊은
    것. #18(아카이브) · #21b(파싱 캐시) · #95(재무 캐시 — v4·v5 실패를
    주석에 적어 두고도 v6 을 잊었다) 에 이어 **네 번째**다. 같은 날 나는
    파서 캐시 두 곳(#1039·#1041)엔 소스 지문을 넣어 놓고 **렌더 캐시엔
    안 넣었다** — 규율은 이렇게 매번 진다.
    처방: 산출물 캐시 키에는 **그 산출물을 만드는 모듈의 소스 지문**을
    넣을 것(`_render_sig`). 재렌더 비용이 유계인지만 확인하면 된다
    (여기선 matplotlib 1회 ₩0, LLM 은 rcept_no 캐시라 재과금 없음).
    ⚠️ 회귀도 같이 바꿔야 한다 — `endswith(f"_{_RENDER_VER}.png")` 로
    재던 옛 테스트는 **손으로 bump 하는 규약으로 되돌아가도 통과**한다.

### 실수 #126

126. **그룹이 다르다고 무조건 막으면 멀쩡한 회사가 빈칸이 된다**(2026-08-22
    LG화학 25.4Q): 각주(#123)가 `매출: 영업수익 ↔ 매출액` 이라고 말해 주자
    바로 보였다 — 연간과 9개월 보고서가 **같은 총액**을 다른 라벨로 적었을
    뿐이다. 가드가 진짜 막아야 하는 건 **총액 ↔ 구성요소**(메리츠금융지주
    영업수익 ↔ 이자수익 → TTM 매출 -10.30조)이므로, 판정 단위를 '그룹이
    같은가'가 아니라 '**같은 급인가**'(`_COMPONENT_GROUPS` 포함 여부)로
    바꾼다. 일반화: 안전 가드를 **동등 비교**로 만들면 정당한 변형까지
    막는다 — 무엇을 막으려는지 그 **성질**로 판정할 것.
    ⚠️ 이 진단은 #123(아는 것을 화면이 말하게)이 없었으면 못 했다. 표시
    의무가 실제로 버그를 잡아냈다(#44 와 같은 패턴).

### 실수 #127

127. **직렬로 남은 구간을 먼저 볼 것 — 그리고 계측 이름이 겹치면 빈
    구간을 못 본다**(2026-08-22 분기실적 마무리): 계측을 고치고 나니
    `total = build_payload + render_png` 이 정확히 맞았다 — 앞서 내가
    "178초 미측정 구간"이라 부른 건 **섞인 계측의 허상**이었다(#121 에
    적어 놓고 그 수치로 결론을 냈다). 진짜 병목은 `total` **밖**에 있었다:
    `h.production_html=76.1s`(제품·가동률 표)와 `bp.backlog=83.065s`
    (수주잔고)가 **같은 정기보고서를 따로, 직렬로** 걷고 있었다.
    대응: 시계열을 받은 직후 표 수집을 **미리 데운다**(fire-and-forget)
    — 둘이 동시에 돌아 직렬 합이 최대값 하나가 된다. 미리받기는 호출부와
    **같은 인자**여야 캐시 키가 맞는다(#104), 이미 따뜻하면 **스레드조차
    띄우지 않는다**.
    ⚠️ 계측 버그 둘을 같이 잡았다: (a) `pre_render` 가 `_t0` 리셋 누락으로
    `build_payload` 와 **같은 값**을 찍고 있었다(3.181/3.181) — 같은 값을
    두 이름으로 찍는 계측은 빈 구간을 못 본다. (b) 미리받기 in-flight
    집합이 **모듈 전역**이라 앞 테스트가 남긴 키로 다음 테스트가 조기
    반환했다(전체 실행에서만 나는 흔들림) — 전역 상태를 쓰는 테스트는
    시작할 때 비울 것.

### 실수 #128

128. **직렬로 남은 구간은 '읽는 게 없으면' 겹칠 수 있다**(2026-08-22
    `/api/lookup_detail` 60~192초): 시장 enrichment 가 보조 6종 병렬 **뒤에
    직렬로** 돌고 있었고, 옛 주석이 그 사실을 적어 두고도 그대로 있었다.
    AST 로 확인하니 `_enrich_*` 는 **snap 에서 아무것도 읽지 않고 자기 시장
    키만 쓴다** — 별도 dict 에 받아 나중에 합치면 서로 기다릴 이유가 없다.
    겹치기의 **전제**(읽지 않음)를 회귀로 못박을 것 — 누가 읽기 시작하면
    그때 깨져야 한다. 그리고 합치는 자리는 `snap` **재바인딩(None strip) 뒤**
    여야 한다(앞이면 새 dict 에 안 실려 시장 데이터가 통째로 사라진다).
    ⚠️ 겹침 회귀를 **순서·시간**으로 쓰면 흔들린다(단독 green, 전체 실행
    red — 실측 2회). 양쪽이 서로가 도는 중임을 확인하는 **악수**(Event)로
    잴 것. "enrich 뒤에 뭔가 끝났다"류 단언은 **직렬이어도 통과**한다.
    ⚠️ 겹치기용 풀은 `shutdown(wait=False)` 를 **제출 직후**에 부를 것 —
    정리를 뒤에 두면 예외 경로에서 샌다(스레드 누수 회귀로 고정).
    ⚠️ 그리고 렌더처럼 **전역 락**으로 직렬화되는 구간은 '대기'와 '그리기'를
    나눠 재야 한다 — 합쳐 재면 남이 그리는 동안 기다린 걸 "그리기가 느리다"로
    오진한다(#92 의 계측판).

### 실수 #132

132. **진단 도구가 제품과 다른 인터프리터로 돌면 결과가 통째로 거짓이다**
    (2026-08-22 ASML FCF 감사): VM 에서 `python3 -m bot.scripts.fcf_audit`
    로 돌렸더니 venv 밖이라 `yfinance` 가 없어 스냅샷이 통째로 비었는데,
    감사는 `① 재계산 ✅ 전 기간 일치` · `⑤ ✅ 누적 냄새 없음` 을 찍었다 —
    **대조 0건에 ✅**(#54 를 내 도구에서 또 위반). 게다가 배너가
    "자격증명 DART_API_KEY=.env 파일" 이라 건강해 보였다.
    처방 둘: (a) 배너에 **`sys.executable` 과 필수 모듈 유무**를 찍고 없으면
    바로 중단(#23 '.env 를 안 읽는다' 의 인터프리터판) (b) 각 섹션은 대조
    건수를 세어 **0 이면 ❌**, 판정에 필요한 최소 개수 미달이면 ❓.
    ⚠️ 회귀에서: `any("인터프리터" in s for s in 문자열들)` 로 배너를 재면
    **의존성 오류 메시지가 대신 만족시켜** 배너를 지워도 통과한다(#75) —
    `sys.executable` 을 print 하는 **구조**로 볼 것.

### 실수 #135

135. **"매일 바뀌는 값"을 월 해상도 시계열의 마지막 점으로 보여주고 있었다**
    (2026-08-22 PER 밴드 요약, 사용자 "PER 은 주가에 따라 매일 바뀌잖아.
    그게 고려된거 맞지?"): 표의 마지막 행 주가는 EPS 기간에 붙은 **월봉**
    종가이고 KR 은 FnGuide 밴드를 **12시간 캐시**로 받는다 — 즉 화면의
    '최근 PER' 은 최대 한 달 낡은 값이었는데, 값이 다 '있어서' 아무 감사도
    안 걸렸다(#96 의 재발: 채워진 칸은 틀려도 조용하다). 시계열의 마지막
    점은 '현재'가 아니다 — **매일 바뀌는 파생값은 라이브 원천으로 다시
    만들고**, 못 받았으면 그렇게 밝힐 것(#43).
    ⚠️ 같이 지킨 것: PER 은 주가에 선형(PER = 주가 × k)인데 k 를 원천별로
    따로 적으면(비-KR 1÷TTM EPS · KR 최고배수÷밴드선) 두 벌이 되어 갈라진다
    — **표의 마지막 행**에서 `k = PER ÷ 주가` 로 만들면 현재 PER 이 정의상
    이력 행과 같은 기준이고 사용자가 눈으로 나눠 봐도 맞는다(#33·#38).
    ⚠️ 그리고 라이브 시세 검증에 `chart_data._validate_live_price` 의
    **일일밴드**(KR ±35%)를 그대로 쓰면 안 된다 — 기준이 월봉이라 정당한
    한 달 등락을 reject 한다. 가드의 밴드는 **기준값이 얼마나 낡았는지**에
    맞출 것(여기선 자릿수 사고만 거르는 0.2~5배).

### 실수 #136

136. **"한 번 더 물어본다"는 원천이 계속 같은 답을 주면 무력하다**
    (2026-08-22 인텍플러스 064290.KS — #122 의 재발): 1년을 요청했는데
    `25개 봉 · 07-16 → 08-21`. 08-22 에 넣은 **기간 키워드 재질의**는 실제로
    돌았고(스텁으로 호출 2회 확인) 야후가 **두 질의 모두** 잘라 주자 25봉을
    그대로 '1년' 라벨로 내보냈다. 우리에겐 네이버 일봉(키 불필요·차단 없음)이
    이미 있었는데 **야후가 빈 응답일 때만** 쓰고 있었다 — 잘렸지만 비지 않은
    응답이 그 폴백을 통째로 건너뛰게 했다. 규칙: 폴백 조건을 '실패'로 잡지
    말고 **'요구를 충족했나'**로 잡을 것(빈 응답은 미충족의 한 경우일 뿐이다).
    같은 방식으로 재질의도 '성공/실패'가 아니라 폭으로 판정한다.
    ⚠️ 그리고 폴백을 **자주 타게 만들면** 그 경로의 빈틈이 드러난다: 폴백
    payload 엔 라이브 현재가·52주 고저가가 안 붙어 오른쪽 패널이 조용히 비고,
    조기반환 지점에서 `decimals` 가 아직 바인딩되지 않아 클로저가
    UnboundLocalError 를 냈다(바깥 except 가 삼켜 **증상 없이** 폴백 품질만
    떨어졌다 — 회귀가 잡았다). 드문 경로를 상시 경로로 승격할 땐 그 경로가
    본 경로와 **같은 것을 채우는지** 먼저 확인할 것.
    ⚠️ 짝: 캡션이 출처를 `'Yahoo Finance'` 로 박아 두고 있어, 네이버로
    되돌아간 날에도 화면이 야후라고 말했다(#55). 폴백을 로그로만 알리면
    사용자는 영영 모른다(#42a) — payload 가 밝힌 원천을 화면이 따라야 한다.
    ⚠️ 뮤테이션 주의: 이 가드는 바깥(재질의 진입)·안쪽(폴백 진입) **둘이
    겹쳐** 있어 하나만 뒤집으면 다른 하나가 막는다. 둘 다 뒤집어야 발화하는
    것을 확인해야 테스트가 눈멀지 않았음을 안다(#91b '재는 대상이 맞나').

### 실수 #137

137. **원천의 축 라벨을 그대로 옮기면 오지 않은 날짜가 관측일이 된다**
    (2026-08-22 PER 밴드 표, 사용자 "아직 2026.08.31 이 안됐는데 해당 월
    마지막날로 그냥 넣은거야?"): FnGuide 밴드차트의 x축은 **월말 버킷**이라
    진행 중인 달도 그 달 마지막 날로 찍혀 온다. 값은 원천 것이 맞지만 날짜가
    미래라 표가 거짓말한다 — 차트에선 눈금이라 안 보이던 것이 **표로 옮기는
    순간** 관측일로 읽힌다. 규칙: 원천의 축 좌표를 **날짜 칸**에 옮길 땐
    그게 관측일인지 버킷 라벨인지 먼저 답하고, 버킷이면 오늘(KST)로 자를 것
    (#29 '기간 라벨은 날짜로 되짚어라' 의 반대 방향 — 그건 과거를 잘못 되짚은
    경우, 이건 미래를 적은 경우다).
    ⚠️ 짝: 같은 캡처의 `최저 0.00x · 그 배수에서의 주가 0.00`. FnGuide 는
    **정의 불가**(적자 등)를 0 으로 채워 보내고 차트는 이미 그 선을 안 그리는데
    (`mult<=0` 제외) 표만 0 을 숫자로 그리고 있었다 — **차트에서 이미 거른
    값을 표로 옮길 땐 그 필터도 같이 옮길 것**(#38 의 렌더판). 0 은 값이 아니라
    '없음'이므로 행을 빼되 **왜 없는지 말한다**(#43).

### 실수 #138

138. **산수가 전부 맞는데 값이 틀릴 수 있다 — 입력에 구멍이 있으면**
    (2026-08-22 MSFT PER 밴드, 사용자 "PER Band 가 좀 이상하지 않아? 밑에
    기간도 제대로 안나오고"): 이력 표 전 행이 `주가 ÷ TTM EPS = PER` ·
    `배수 × EPS = 적정가` · `현재가 × k = 현재 PER` 로 **완벽히 일관**했는데
    TTM EPS 자체가 15개월 합이었다. 원인은 EDGAR `companyconcept` 의 분기
    프레임이 **10-Q 에서만** 나온다는 것 — 회계연도 4분기는 10-Q 를 안 내고
    10-K 의 연간 수치에만 들어간다. MSFT(6월 결산)는 6월이 빠졌고, 12월
    결산이면 12월이 빠지므로 **미국 종목 전체**가 틀리고 있었다.
    사용자가 알아챈 단서는 값이 아니라 **기간의 간격**이었다(9→12→3→9월).
    교훈: 검산이 통과해도 **입력의 규칙성**(간격·개수·연속성)을 따로 볼 것 —
    파생값끼리의 일관성은 공통 입력이 틀리면 그대로 유지된다.
    대응 둘: (a) `연간 − 3분기 합` 으로 결산분기를 복원(DART 4분기와 같은
    규율) (b) 더 중요한 **구조적 방어** — TTM 은 **연속한 4분기일 때만**
    만든다. 복원에 실패해도 틀린 값 대신 빈칸이 된다(#29).
    검산법: **결산 시점 TTM == 연간 EPS** 여야 한다(실측 11.80·13.64 일치).
    ⚠️ 같이 고친 표시 결함 셋: 밴드는 전 구간(31개) 분포인데 바로 위 요약은
    5년(16개)이라 '5년 최저 22.20' 옆에 '최저 19.18' 이 놓였다 — 표마다
    **자기 창**을 밝히게 했다(#34). 이력 표는 31개 중 24개만 실으면서 말을
    안 했다(#45). 국내는 EPS/BPS 를 안 줘 분모 열 전체가 '—' 인 죽은 열이었다.
    ⚠️ 뮤테이션 4개가 통과했다 — 셋은 **옆 가드가 대신 막아서**(개수 가드를
    지우면 길이 가드가, 중복 가드를 지우면 개수 가드가 막는다), 하나는
    **네트워크가 필요한 배선**이라 단위테스트가 못 태워서. 그 가드만 무너지는
    픽스처로 바꾸고 배선은 AST 로 못박아야 발화한다(#91b).

### 실수 #139

139. **재무제표 통화 ≠ 거래 통화 — 배수를 만들 땐 매번 확인할 것**
    (2026-08-22 TSM PER 0.92x, 사용자 "PER 도 너무 낮고...제발 꼼꼼히 좀
    봐줘"): yfinance 가 주는 주당순이익이 **NT$327.35**(ADR 주당 = 보통주×5)
    인데 주가는 **US$302.33** 이라 그냥 나눠 0.92 가 나왔다(실제 ≈29).
    ADR(TSM·ASML·BABA…) 전반에 해당한다. 이 가드는 **동종비교표·분기실적에
    이미 있었는데**(`cur_mismatch` HARD GUARD) 새 모듈이 그걸 안 쓴 것 —
    새 배수 계산을 만들 때 "이 두 값이 같은 통화인가"를 먼저 답할 것.
    처방: 다르면 **그 시점 환율로 환산**한다(과거 PER 은 실제로 환율에
    의존했으므로 근사가 아니라 정의다 — 현재 환율 하나로 전 기간을 바꾸면
    #28 빈티지 오염). 환율을 못 받으면 **만들지 않는다**(#32).
    ⚠️ 야후 환율 심볼은 쌍에 따라 **한 방향만** 있다 — 정방향이 비면
    역방향을 받아 뒤집을 것.
    ⚠️ 2차 그물을 같이 둘 것: PER 중앙값이 1 미만이거나 500 초과면 단위·통화
    사고로 보고 거부한다(`implausible_reason`). 통화 가드가 1차지만 같은
    증상이 다른 경로로 또 온다(#54 대조 0건 금지의 값판).
    ⚠️ 뮤테이션 셋이 통과했다 — **픽스처가 눈멀어서**였다: (a) 헬퍼를 직접
    부르는 테스트는 배선 제거를 못 잡는다(#20) (b) `_monthly_closes` 를
    비우면 `for_ticker` 가 통화 블록 **전에** 반환해 스파이가 안 불린다
    (c) 밴드 5년 창은 **마지막 행** 기준이라 전 행이 5년 안에 몰린 픽스처
    에선 폴백 분기가 안 탄다. 뮤테이션이 통과하면 순서대로 의심할 것:
    ① 그 테스트가 돌긴 했나 ② 재는 대상이 맞나 ③ 픽스처가 충분히 센가(#91).
    ⚠️ 그리고 이 세션에서 `git checkout <file>` 로 미커밋 작업을 날렸다 —
    뮤테이션 복원은 **백업 파일**로만 할 것(`git checkout` 은 그날 작업 전체를
    HEAD 로 되돌린다).

### 실수 #142

142. **비율 지표는 분모가 0 에 가까우면 발산한다 — 한 점이 밴드를 죽인다**
    (2026-08-22 MKSI 밴드 최고 **1,073.25x**, 중상 36.78x 의 29배): 순이익이
    거의 0 인 분기가 하나 끼자 그 시점 PER 이 1000배가 되어 '최고'가 통째로
    무의미해졌고 '그 배수에서의 주가'는 6,729 였다. 값 자체는 **버그가 아니라
    실제 관측**이다 — 문제는 극단값을 분포의 끝점으로 쓰는 것이다.
    대응: 밴드 분위수는 **IQR 3배 울타리** 밖을 제외한다. 임의의 절대 상한
    (200x 류)을 쓰면 정상 종목의 고배수 구간이 잘린다 — **분포가 스스로 정하는**
    울타리라야 한다(CMCSA 실측: 울타리 33.81 > 최고 23.73 → 아무것도 안 버림).
    뺀 값은 **이력 표에는 그대로 남기고** 몇 개를 왜 뺐는지 밝힌다(#43).
    ⚠️ 진단에서 배운 것: 사용자는 "밴드를 5년으로 해달라"고 했지만 밴드는
    **이미 5년**이었다(CMCSA 에서 요약 '5년 최고 23.73' 과 밴드 '최고 23.73'
    이 정확히 일치해 확인). 요청을 액면 그대로 실행했으면 아무것도 안 고쳐진다
    — **화면 숫자끼리 대조해 이미 맞는 것을 먼저 배제**할 것.
    ⚠️ 그리고 이 가드의 "다 버리면 그대로 둔다" 분기는 3×IQR 로는 **도달
    불가능**했다 — 픽스처가 분기를 못 태우면 그 단언은 아무것도 안 재는
    것이므로(#54) `min_points` 를 올려 실제로 태웠다.

### 실수 #144

144. **2단계 로딩에서 '주소'를 2단계에만 실으면 1단계 화면이 통째로 죽는다**
    (2026-08-22 SIE.DE 밴드 탭에 원시 오류 `bad ticker`): `NOAH_TICKER` 는
    라이브 quote 뿐 아니라 **모든 지연 pane**(밴드·분기실적·기술지표)이 쓰는
    주소인데 `if (has_tabs and enrich)` 로 full 단계에만 방출했다. enrichment
    가 실패해 core 만 남으면 `tkr()` 이 빈 문자열이 되고 `/api/band?ticker=`
    가 400 을 돌려줬으며, 그 **서버 오류 문자열이 화면에 그대로** 떴다.
    티커는 core 에서도 아는 값이다 — 무거운 것만 뒤로 미루고 **식별자는 항상
    먼저** 실을 것. 그리고 클라이언트는 필수 인자가 없으면 **부르지 말고**
    사람 말로 상태를 적을 것(원시 API 오류 노출 금지, #43).
    ⚠️ 뮤테이션을 **엉뚱한 함수**에 걸어 한 번 통과했다 — 같은 이름의 블록이
    아카이브 렌더와 라이브 렌더 **두 곳**에 있었다(아카이브는 원래 정상).
    치환이 1건인지 세는 것만으론 부족하고 **어느 함수인지**를 봐야 한다.

### 실수 #145

145. **프로브는 경로뿐 아니라 '인자'도 제품과 같아야 한다**(2026-08-22 JP
    프로브 v1): `for_ticker(ticker)` 를 **스냅샷 없이** 불러 9984.T·7203.T·
    6758.T 세 종목이 전부 'EPS 이력이 부족' 으로 나왔다 — 비-KR EPS 는
    스냅샷에서 꺼내므로 그건 원천이 아니라 **프로브가 만든 결과**였다.
    하마터면 "일본은 EPS 이력이 없다"로 결론 낼 뻔했다(#35 의 인자판).
    v2 는 `collect_stock_snapshot` 을 넘기고 **스냅샷의 EPS 개수·양수 개수**
    까지 찍어 어느 단계에서 끊기는지 보이게 한다.
    ⚠️ 같은 실행에서 확인된 사실: yfinance 는 JP 분기 손익을 **4~5열 + EPS
    행까지** 준다. 즉 "일본은 원천이 없다"는 틀렸고, 분기가 4~5개뿐이라
    TTM 이 1~2점이라 밴드 최소치를 못 넘는 것이다 — **원천 부재와 표본 부족은
    다른 문제**이고 처방도 다르다.

### 실수 #149

149. **'없음'은 한 단어인데 원인은 셋이다 — 진단은 단계별로 세야 한다**
    (2026-08-22 HK 0700/0522/0002.HK 분기실적): 화면의 '원천 미제공' 과
    '분기 재무 데이터 없음' 이 같은 말처럼 보이지만 갈래가 셋이다 —
    (a) 원천이 **열 자체를** 안 줌 (b) 열은 있는데 **그 항목**(Total Revenue
    등)이 없음 (c) 열·항목은 있는데 **우리 매핑**이 못 잡음. 열 수만 세는
    프로브로는 (b)(c)를 못 가른다. **항목별 채움 개수**를 찍고, 화면이 쓰는
    변환 함수(`series_from_yfinance`)를 그대로 태워야 셋이 갈린다.
    ⚠️ 프로브 관찰 목록이 화면 매핑(`_ITEM_CANDIDATES`)과 갈라지면 진단이
    엉뚱해진다 — 회귀로 "화면이 매핑하는 이름은 전부 프로브 관찰 목록에
    있는가"를 고정했다(#24 열거형이 갈라지는 것을 테스트가 잡게).
    ⚠️ JP 전용이던 프로브를 전 시장용으로 넓히며 **대안 원천 섹션을 시장으로
    게이트**했다 — HK 종목 출력에 EDINET 줄이 찍히면 그 자체가 거짓말이다(#34).
    이름도 `jp_fundamentals_probe` → `fundamentals_probe` 로 바꿨다(배너 문구
    포함 — 이름과 내용이 어긋나면 출력이 거짓말한다).

### 실수 #151

151. **`__init__.py` 에 이름이 보인다고 그 함수가 있는 건 아니다 — 그리고 죽은
    이름 하나가 두 기능을 함께 재운다**(2026-08-22, #150 을 배포한 **바로 다음
    턴**에 사용자 프로브가 뒤집었다): 나는 #150 에서 "이미 부르고 있던 API 가
    이력을 다 준다"고 배포하고 사용자에게 "중국 지원됨"이라고 말했다. VM 실측은
    `module 'akshare' has no attribute 'stock_a_indicator_lg'` 였다 — 그 이름은
    설치본 v1.18.62 의 `__init__.py` 에 **changelog 문자열로만** 남아 있어
    `grep` 은 통과하고 `import` 는 실패한다(#25 '능력은 이름이 아니라 실측'의
    라이브러리판). 더 나쁜 건 그 이름에 **두 기능**이 걸려 있었다는 것이다 —
    새로 만든 PER 밴드뿐 아니라 **스크리너의 CN PER/PBR 폴백(`get_valuation`)도
    이미 죽어 있었고**, 아무도 알려주지 않았다(#42a). 교훈 셋:
    (a) 외부 라이브러리 함수를 쓸 땐 **호출 가능한지**(`hasattr`/실호출)로 재고,
        소스 grep 이나 문서로 판정하지 말 것. 배포 전에 **그 경로가 실제로
        실행됐나**를 먼저 답할 것(#79).
    (b) 라이브러리 이름에 기대는 대신 **원천 API 를 직접** 부르면 버전 드리프트가
        사라진다(바이두 股市通 → 래퍼는 폴백으로만). 그러자 홍콩까지 같은 코드로
        커버돼 야후가 HK 손익을 반기로만 주는 문제(#149)가 같이 풀렸다.
    (c) 사용자에게 "된다"고 말하기 전에 **실측 출력**을 볼 것 — 배선을 넣은 것과
        동작하는 것은 다르다(#20 의 외부의존판).
    ⚠️ 같은 턴에 소스 문자열 단언이 **또 세 개** 깨졌다(`"·자산·"`·
    `"PPI·CPI·유동성"`·`"<b>분기실적</b> 탭"`) — Help 문구를 다듬자 구분자에
    공백이 붙은 것뿐인데 멀쩡한 코드를 틀렸다고 했다. 열거를 **그룹 규칙**으로
    바꿨다(차트보드 그룹 안에 그 보드들이 있는가, #31).

### 실수 #152

152. **회사별 검색 API 가 없는 원천의 탐색은 본 응답 경로에 두지 말 것**
    (2026-08-22 EDINET): 일본 유가증권보고서를 찾으려면 일별 목록을 최대 430일
    훑어야 한다(회사 검색 API 가 없다) — 콜드 캐시면 수백 HTTP 라 밴드 탭이
    통째로 멈춘다. 그래서 캐시가 없으면 **빈 리스트를 즉시 주고**(호출부는
    yfinance 로 폴백) 백그라운드가 데운다(#116 그대로). 대신 비용이 유계인
    이유를 코드가 말해야 한다 — 여기선 **문서 한 부에 5기**가 들어 있어
    (「主要な経営指標等の推移」) 두 부면 10년이고, 일별 목록은 전 종목 공용이라
    두 번째 JP 종목부터 거의 공짜다.
    ⚠️ 빈 결과는 **캐시하지 않는다** — 원천 장애 한 번이 한 달 빈 표가 된다.
    ⚠️ EDINET CSV(`type=5`)는 **UTF-16 LE** 다. UTF-8 로 가정하면 전 행이 조용히
    깨진다 — 못 읽으면 빈 리스트가 아니라 **예외**를 올려 실패를 드러낸다.
    ⚠️ 일본은 2024년에 四半期報告書(140)를 폐지하고 半期報告書(160)로 바꿨다 —
    연도마다 주기가 다르므로 **분기 계열은 이 원천에서 만들지 않는다**(섞인
    주기를 'TTM' 이라 부르면 #147 재발). 연차만 낸다.

### 실수 #155

155. **필드가 따옴표로 감싸져 오면 컬럼 키가 통째로 어긋난다**(2026-08-22 EDINET
    CSV): 프로브가 `③ CSV 행: 2182행` 을 받아 놓고 `④ 아는 요소 매칭: 0종`
    이었다. 원인은 헤더에 그대로 찍혀 있었다 — `['"要素ID"', '"項目名"', …]`.
    EDINET CSV 는 **모든 필드를 큰따옴표로 감싸는데** 내가 탭으로만 쪼개
    키가 `"要素ID"` 가 됐고 `row.get("要素ID")` 가 전부 None 이었다. 구분자를
    직접 쪼개지 말고 `csv` 모듈에 맡길 것(따옴표 안의 탭·줄바꿈까지 처리한다).
    ⚠️ **픽스처가 원문 형식이 아니면 이 버그를 영영 못 잡는다** — 내 테스트는
    따옴표 없는 CSV 를 만들어 8개가 전부 green 이었다(#59a '뮤테이션이 통과하면
    픽스처부터 의심'의 앞 단계: 통과한 게 아니라 **틀린 것을 재현조차 안 했다**).
    픽스처는 원천이 실제로 보내는 바이트를 흉내낼 것.
    ⚠️ 그리고 진단이 "0종"이라고만 말해 **원천 부재처럼 읽혔다** — 매칭이 0이면
    원문 표본을 그대로 찍게 했다(#54 대조 0건은 통과가 아니라 실패 · #109).
    ✅ 같은 실행에서 확인된 것: #154 의 1년 창 탐색은 통했다(2건 15.4초), 그리고
    20-F 폴백도 통했다(NVMI `basis=edgar-a` 관측 4개 → **10개**).

### 실수 #156

156. **후보 목록에 넣기 전에 "그 항목이 정말 그 뜻인가"를 답할 것**(2026-08-22
    도요타 매출 4.2조엔): `OrdinaryIncomeLossSummaryOfBusinessResults`(経常利益)
    를 매출 후보에 넣었더니 도요타 매출이 **4.2조엔**으로 찍혔다(실제 ~48조).
    経常利益은 **이익 항목**이다. 게다가 같은 키에 후보를 여럿 두면서 우선순위를
    안 정해 두면 **파일에 나중에 오는 쪽이 이겨** 문서마다 값이 갈린다 — 후보
    목록은 순서가 곧 우선순위여야 하고 코드가 그걸 지켜야 한다.
    ⚠️ 같은 문서에서 **連結(IFRS)과 個別(JP GAAP)이 나란히** 온다. 提出会社
    블록은 정의가 다른 계열이라 한 행에 섞으면 매출·이익의 뜻이 갈린다(#32) —
    連結이 있으면 連結만 쓰고 그 항목에 連結이 없으면 **비운다**. 판정은
    컨텍스트 ID 접미 추측이 아니라 원천이 주는 `連結・個別` **컬럼**으로.
    ⚠️ 그리고 실제 EPS 요소는 `…EarningsLossPerShare…`(중간에 **Loss**)인데
    내 매핑엔 그게 빠져 EPS 가 0개였다 — **픽스처를 내가 지어낸 ID 로 만들면
    매핑이 틀려도 전부 green** 이다(#155 의 요소 ID 판). 픽스처는 프로브가 찍은
    원문 ID 를 복사해 올 것. 뮤테이션 2건이 픽스처가 눈이 멀어 통과했다(#91c).
    ⚠️ 진단 목록을 25종에서 **자른 탓에** IFRS 매출 ID 가 '외 2종' 안에 숨어
    한 라운드를 더 썼다 — 매핑을 넓히는 근거가 되는 목록은 자르지 말 것.

### 실수 #159

159. **검산법을 문서에만 적어 두면 매번 진다 — 제품 가드로 옮겨라**
    (2026-08-22 KLAC): TTM EPS 가 2.03 → 3.63 → 5.51 → 9.24 → **3.04** 로
    **매 6월(회계연도말)마다 붕괴**했다. 결산분기 복원(`연간 − 3분기`)이
    어긋나 그 한 분기가 4개 시점을 연달아 오염시켜 톱니가 된 것이다. 각 행의
    산수는 전부 맞아(주가÷EPS=PER) 눈으로는 안 잡힌다. #138 에 **"검산법:
    결산 시점 TTM == 연간 EPS"** 라고 적어 두고도 그걸 코드에 안 넣어서
    같은 병을 다른 종목에서 다시 만났다 — 어긋나면 분기 경로를 버리고 연간
    경로로 내려간다(`ttm_annual_mismatch`).
    ⚠️ 그리고 사용자가 "다른 사이트를 찾아본던 어떻게 해서든...너무 힘들다"
    고 했다. **사용자가 외부 사이트로 검산해야 하면 그건 화면의 결함이다**
    (#102c) → `bot.scripts.per_band_audit` 이 산수·분할·결산검산·현재값·창·
    상식성 6축을 기계가 판정한다. 감사는 화면이 쓰는 그 경로를 태우고(#35),
    대조 0건은 ✅ 가 아니라 **❓(판정 불가)** 로 찍는다(#54).

### 실수 #160

160. **보조 데이터를 별도 호출로 받으면 조용히 비고, 주 계산이 통째로 틀린다**
    (2026-08-22 LRCX 분할 환산): #158 을 고쳐 배포하고 "고쳤다"고 말했는데,
    감사 실측은 **여전히 14.3배** 튀었다. 원인은 `yf.Ticker(t).splits` 라는
    **두 번째 호출**이 빈 리스트를 돌려준 것 — 실패가 로그 debug 로만 남아
    환산이 한 번도 안 돌았다. `auto_adjust=False` 응답은 `Close` 와
    `Stock Splits` 를 **같이** 주므로 한 번에 받으면 두 기준이 정의상 어긋날 수
    없다. 규칙: 값 A 를 값 B 의 기준에 맞추는 코드는 **A 와 B 를 같은 응답에서**
    받을 것. (#79 '그 경로가 실제로 실행됐나'의 재발 — 배포 전에 실행 증거를
    안 봤다.)
    ⚠️ 그리고 가드가 **표를 통째로 비웠다** — LRCX·KLAC 이 빈 화면이 됐고
    그건 사용자에게 '수집 실패'로 보인다. 어긋난 건 경계 **이전** 구간이므로
    거기까지만 잘라내고 최근 구간은 보여줄 것(#29 '빈칸이 낫다'는 **값**에
    적용되지 **계열 전체**에 적용되는 게 아니다). 잘랐으면 말할 것(#43),
    잘라서 점이 모자라면 "이력 부족"이 아니라 **잘라낸 사유**를 말할 것(#131).
    ⚠️ 그리고 임계값 4배가 **정당한 성장까지 막았다**(NVMI 2016→2017 연간 EPS
    4.7배는 실적이다, #146). 한때 '관측 배수가 분할비율(2·3·10…)에 가까운가'로
    가르려 했는데 그건 **추측**이다 — 분할과 실적 변동이 곱해져 관측값은 정수가
    아니다(LRCX 실측 9.9·14.3인데 실제 분할은 10:1). 원인을 단정하지 말고
    **크기**로만 자르고 사유도 "기준이 갈릴 수 있습니다"로 적을 것.

### 실수 #161

161. **빈 목록을 영구 캐시하면 그 날은 영영 비어 보인다**(2026-08-22 EDINET
    소니): `_fetch_day` 가 "과거 날짜는 안 변한다"는 전제로 빈 결과까지 영원히
    믿었다. API 가 한 번 빈 응답을 주면 그 날의 문서는 영원히 없는 것이 되고,
    6758.T 가 201일을 훑고도 유가증권보고서 0건이었다(#119 의 EDINET 판).
    휴일은 정상적으로 비므로 **빈 결과는 짧게만** 믿는다.
    ⚠️ 진단도 갈라야 한다 — '빈 날 72일'은 주말 수와 비슷해 정상인지 이상인지
    알 수 없다. **평일**이 비었는지를 따로 세야 원천 오류·캐시 오염이 보인다.

### 실수 #162

162. **한 사건이 두 봉에 실려 오면 보정이 두 번 걸린다**(2026-08-22 AAPL
    0.74): 월봉 응답의 `Stock Splits` 가 4:1 분할을 **두 봉**에 실어 EPS 를
    4로 두 번 나눴다 — as-reported 11.91 이 2.98 이 아니라 **0.74** 가 됐고,
    LRCX 는 10:1 이 100 이 됐다. 과다적용은 미적용과 **똑같이 급변을 남기는데
    방향만 반대**라 눈으로는 구별이 안 되고, 내 가드는 그걸 '분할 미조정'으로
    읽어 멀쩡한 이력을 잘라냈다(LRCX 관측 4개). 처방 둘:
    (a) 같은 비율이 짧은 간격으로 두 번 나오면 **같은 사건**으로 묶는다.
    (b) 더 중요한 건 **결과를 재는 것** — 보정 뒤 인접 급변이 오히려 커지면
        되돌린다. 잘 맞은 보정은 반드시 급변을 줄인다. 원인(중복인가 누락인가)을
        추측하는 대신 **효과로 판정**하면 새로운 원인에도 자동으로 옳다.

### 실수 #165

165. **원천이 무엇을 쓰는지는 재서 말해야 한다 — 라벨을 추측으로 붙이면
    화면이 거짓말한다**(2026-08-22 KR 밴드 분모): 사용자가 "혹시 분기단위로
    하는 게 맞지 않을까? 실제 FnGuide 밴드차트는 어떻게 하는지 보고 리뷰해줘"
    라고 물어 화면 값으로 되짚었더니 EPS 가 **매달 +81~82 로 완벽히 선형**
    이었다(분기 확정 실적이면 3개월마다 계단이어야 한다). 즉 원천 밴드선의
    분모는 분기 확정치가 아니라 매달 갱신되는 값이다.
    ⚠️ 그런데 나는 **바로 전 라운드**(#164)에 근거 없이 "분기 실적 기준"
    이라고 라벨을 박았다 — 사용자 질문의 표현을 그대로 사실로 옮긴 것이다.
    같은 커밋에서 화면 문구를 고치면서 그 문구가 맞는지는 안 쟀다(#55 의
    재발이자 #12 추측보고 금지 위반).
    대응: 판정을 순수 함수(`per_band.eps_cadence`)로 두고 **화면 라벨·분모
    이름·감사 축이 전부 그 측정 결과에서 파생**되게 했다. 분모 이름도
    'TTM EPS' 라고 우기지 않는다 — KR 은 배수를 원천 밴드선에서 되뽑으므로
    분모가 무엇인지는 원천이 정한다.
    ⚠️ 그리고 이 라벨 테스트가 처음엔 **헬퍼만 직접 불러** 배선을 떼는
    뮤테이션 3건을 놓쳤다(#20) — payload·렌더·감사 축을 각각 결과로 확인해야
    발화한다. 부분문자열 단언도 사유 설명("분기 확정치가 **아니라**")에 걸려
    한 번 오작동했다 — **주장하는 라벨**(괄호 안)을 집을 것(#65).

### 실수 #166

166. **가드는 자기가 적용될 수 있는 자리에만 걸어야 한다**(2026-08-22 전 시장
    감사 첫 실행): 분할 기준 가드를 만들어 놓고 **적용 불가능한 경로에도**
    돌려 멀쩡한 종목을 죽였다. 한 번의 전 시장 스윕이 넷을 한꺼번에 드러냈다.
    (a) **정렬을 안 해서 '인접'이 인접이 아니었다** — 000660.KS 가
        `2024-12-31 → 2022-12-31` 로 2년을 건너뛴 비교에 걸려 표가 통째로
        비었다. 그리고 적자기(EPS≤0)를 걸러내면 그 자리에서 **인접을 끊어야**
        한다 — 안 끊으면 '무너졌다 회복'이 분할 불일치로 읽힌다.
    (b) **원천이 PER 을 직접 주는 경로**(FinMind·바이두·FnGuide)에서는 EPS 가
        우리가 나눠 만든 값이라 급변이 있어도 실적이다 — 0002.HK 가 그 이유로
        유일한 ❌ 였다. 분할 기준 불일치는 **우리가 나누는 자리에만** 성립한다.
    (c) **잘라내기는 '모를 때'의 안전망**이다 — 가격 응답에 `Stock Splits` 가
        같이 온 날은 분할 여부를 **아는** 것이므로(빈 리스트 = 분할 없음)
        남은 급변은 실적이다. 알면서 자르면 정당한 이력을 버린다.
    (d) **같은 종목의 두 계열이 다른 기준으로 끝날 수 있다** — EDGAR 분기는
        후속 보고서의 분할 소급조정분이 오는데(최신 접수분 채택) 연간은
        as-reported 라, AAPL 이 `TTM 11.87 vs 연간 2.98` 이 됐다. 그걸
        '분기 복원이 깨졌다'고 말하면 **거짓 사유**다 — 기준이 다르면 대조
        자체를 하지 않는다(판정 불가가 틀린 사유보다 낫다).
    ✅ 같은 실행에서 확인된 것: 사용자가 네이버 밴드차트(이오테크닉스)와 우리
    화면을 나란히 붙여 줬는데 **PER 10.7·26.7·42.6·58.6 · PBR 1.5·3.1·4.7·6.3
    네 배수가 전부 일치**했다 — 같은 원천(FnGuide)을 그대로 읽고 있다는 뜻이다.
    화면끼리 대조하면 외부 자료 없이도 판정할 수 있다(#51).

### 실수 #167

167. **복원한 값은 형제와 같은 급인지 재라 — 안 그러면 한 칸이 네 시점을
    오염시킨다**(2026-08-22 전 시장 감사 ❌ 3건이 전부 **회계연도말**):
    LRCX 2023-06-25 10.8배 · KLAC 2024-06-30 9.4배 · KLAC 2017-06-30
    결산검산. 결산분기는 10-Q 가 없어 `연간 − 3분기` 로 복원하는데, EDGAR
    **분기**는 후속 보고서의 분할 소급조정분이 오고(최신 접수분 채택)
    **연간**은 as-reported 라 두 계열의 스케일이 갈릴 수 있다 — 그걸 빼면
    복원값이 쓰레기가 된다(KLAC: 2.03 − 14.12 = **-12.09**). 그 한 분기가
    TTM 4개 시점을 연달아 오염시켜 톱니가 됐다(#159 의 진짜 원인).
    스케일이 갈렸는지 직접 알 방법은 없다 — 대신 **결과를 잰다**: 복원값이
    형제 분기 평균의 3배를 넘거나, 형제가 전부 양수인데 음수면 안 만든다.
    일반화: 다른 값에서 **빼서 만든 값**은 반드시 형제와 크기를 대조할 것.
    ⚠️ 같이 배운 것: 분위수를 **선형보간**으로 잡으면 중상·중하가 아무
    시점에도 없던 값이 된다 — 사용자가 "그 배수가 언제였나"를 물으면 답할 수
    없고, '관측 분포'라는 라벨과도 어긋난다. nearest-rank 로 바꿔 네 배수가
    전부 **실재한 관측**이 되게 했다. 붙일 시점이 없으면 **비운다**(가장
    가까운 걸 붙이면 우리가 지어낸 시점이다, #32).

### 실수 #172

172. **원천이 잘라 준 응답에 요청 라벨을 붙이면 화면이 거짓말한다**(2026-08-23
    SK): 38일치에 `기간 1년 -39.4%` 가 붙었다(#29 의 헤더판). 라벨은
    **실제 데이터 폭**에서 만든다. 그리고 잘린 응답의 처방은 하나 더 있었다
    — 야후는 구간 질의만 잘라 주는 일이 있어 `period="max"` 로 받아 **잘라
    쓰면** 온전히 오는 경우가 있다(네이버 폴백은 국내 전용이라 그 밖의
    시장엔 이게 마지막 수단이다). 폴백 사다리는 '실패'가 아니라
    **'요구를 충족했나'**로 이어 붙일 것(#136).

### 실수 #175

175. **빈 열을 '원천이 안 준다'로 두기 전에 이미 부르는 원천을 볼 것**
    (2026-08-23 관심종목 국내 현재 PER): yfinance 가 KR `trailingEps` 를 안
    줘서 국내 종목 PER 이 **전부 빈칸**이었는데, 같은 레포의 스크리너가
    쓰는 `stock_screener._fetch_kr_bulk`(KRX 투자지표)가 **전 종목 EPS 를
    HTTP 2건으로** 이미 주고 있었다(#150 '우리가 그걸 다 쓰고 있나'의 재발).
    종목마다 치면 140콜이지만 벌크는 1콜이다 — 비용을 재기 전에 **벌크가
    있는지** 먼저 물을 것. ⚠️ 그리고 한 열에 두 원천(yfinance TTM · KRX
    투자지표)이 섞이면 세로로 읽는 눈이 속는다(#32·#34) → 셀이 산식과
    출처를 스스로 말하게 했다. ⚠️ 벌크는 **풀 밖에서 한 번** 부를 것 —
    스레드마다 부르면 캐시가 채워지기 전에 종목 수만큼 나간다(#113).

### 실수 #176

176. **경로 분기를 핸들러 안에 인라인으로 두면 감사가 그걸 못 탄다**
    (2026-08-23 국내 밴드): 감사가 `per_band.for_ticker` 만 불러 국내 종목을
    `yf-a · 관측 4개` 로 판정하고 000660.KS 를 '밴드 없음'으로 찍었는데,
    화면은 FnGuide 밴드(**관측 49개**)를 그리고 있었다 — 국내 분기가
    `_handle_band_api` 본문에만 있었기 때문이다. #35('감사는 화면이 쓰는
    그 경로를') 를 지키려면 **그 경로가 함수로 존재**해야 한다. 분기를
    `band_source.resolve` 로 빼고 화면·감사가 둘 다 부르게 했고, 회귀는
    "양쪽이 그 해석기를 호출하는가 + 감사가 `for_ticker` 를 직접 부르지
    않는가"로 AST 에 고정했다. 감사 표본에 시장이 빠져 있으면 그 시장은
    영영 판정되지 않으므로(일본 2종목뿐이었다) 시장 커버리지도 회귀로 고정.

### 실수 #180

180. **감사에 새 경로를 태우면 그 경로에서 축이 여전히 유효한지 다시 물을 것**
    (2026-08-23 감사 재점검): #176 으로 국내를 FnGuide 경로로 태우자마자
    산수 축이 **거짓 초록**을 냈다 — FnGuide 는 분모를 안 줘서 `eps` 가 전 행
    None 인데 "49/49 행에서 주가÷EPS=PER ✅" 라고 찍었다(#54 를 내 도구에서
    네 번째로 위반). 필터로 거른 뒤 세지 말고 **검산한 행 수를 세고, 0 이면
    ❓**. 같은 함정이 옆 축(분할)에도 있어 같이 막았다.
    ⚠️ 짝: PBR 표의 분모는 **BPS** 인데 화면·감사가 'EPS' 라고 적고 있었다
    (캡처: "PBR = 주가 ÷ 원천 EPS(매달 갱신)"). 지표별 분모 이름을
    `_DENOM_NAME` 한 곳에서 파생시키고 `eps_cadence(rows, denom)` 이 그걸
    받아 말한다 — 문구를 복제하면 한쪽만 고쳐진다(#38·#34).

### 실수 #181

181. **한 축에 여러 계열을 겹치면 큰 계열이 나머지를 지운다**(2026-08-23 수익성
    추이): 매출·영업이익·순이익·FCF 를 한 그림에 그리니 축이 매출로 잡혀
    나머지 셋이 바닥에 붙었다 — 사용자 "너무 헷갈려. 지표별로 나눠 달라".
    #112 에서 "축은 그 그림에 그려지는 전 계열에서 정한다"로 폭주는 막았지만,
    그건 **틀린 그림을 안전하게** 만든 것이지 읽히게 만든 건 아니었다.
    지표마다 **자기 축을 가진 작은 차트**로 나눈다.
    ⚠️ 대신 높이를 지표끼리 비교할 수 없게 되므로 **각 차트가 자기 축
    최대값을 밝혀야** 한다(#34) — 안 밝히면 "영업이익 막대가 매출만큼 크다"
    로 읽힌다. 그 축약은 표와 **같은 함수**를 써야 한다(#38, `compact_amount`).
    ⚠️ 그리고 값이 없는 기간은 0 짜리 막대를 그리지 말되(없는 사실) 조용히
    비우지도 말 것 — 빈칸은 '0' 으로 읽힌다(#43). '—' 를 찍는다.

### 실수 #182

182. **'낡음'은 결함이 아닐 수 있다 — 죄는 말하지 않는 것이다**(2026-08-23
    전 시장 감사 ❌ 1건, 소니 6758.T): FY2026 이 **순손실**이라 PER 이 정의되지
    않아 `per_series` 가 그 기간을 조용히 버렸고, 이력이 2025-03-31 에서 멈춘
    채 현재 PER 20.15x 가 그때 EPS 로 만들어졌다. 원천 사실이지만 화면이
    **한 마디도 안 했다**(#43·#131 빈칸이면 왜 비었는지도 말하라).
    값을 버리는 코드는 그 자리에서 사유를 기록하고(`loss_tail_reason`),
    감사는 **설명된 낡음(✅)과 조용한 낡음(❌)** 을 가른다 — 그래야 ❌ 가
    고칠 수 있는 것만 가리킨다.
    ⚠️ 같은 실행에서 나온 짝 둘: (a) `assemble` 이 싣는 `band_from/to` 를
    FnGuide 경로만 빠뜨려 국내가 '창 판정 불가'였다 — 한 payload 를 두 곳에서
    만들면 키가 갈린다(#38) (b) "현재 PER **또는** 실시간 시세 없음" 처럼
    갈래를 뭉뚱그린 사유는 다음 라운드를 낭비한다(#82) — 어느 것이 없는지
    이름을 대라.
    ⚠️ 그리고 편집 중 **같은 렌더 블록을 두 번** 넣어 화면에 사유가 두 줄로
    뜰 뻔했다(먼저 실행된 heredoc 이 이미 패치했는데 `count(old)==1` 만 보고
    또 넣었다). '있는지'만 보는 검사는 하나를 지워도 통과한다 — 렌더 블록은
    **몇 번 나오는지**로 고정할 것(#59·#120).

### 실수 #185

185. **`preserveAspectRatio="none"` 은 폭이 틀리면 글씨를 가로로 늘인다**
    (2026-08-23 COP "차트그래픽이 이상하게 변했지? 나왔다가 안나왔다가"):
    밴드 SVG 는 높이가 고정이라 그 설정을 쓰는데, viewBox 폭(`clientWidth`)이
    0 이면 폴백 340 으로 그려지고 그게 전폭(≈1900px)으로 펴져 **5.6배 늘어난
    글씨**가 된다. 폭이 잡힌 뒤 다시 그리면 멀쩡하니 '나왔다 안 나왔다'로
    보인다 — 가격 차트가 앓던 그 병이다(#108). 처방도 같다: 폭 0 이면 다음
    프레임으로 미루고(상한 필수), `ResizeObserver` 로 컨테이너를 직접 본다.
    ⚠️ **레이아웃을 바꾸면 잠자던 이 병이 깨어난다** — 2열을 1열로 바꾸자
    (#181) 늘어나는 비율이 커져 비로소 눈에 띄었다. 폭에 의존하는 그림이
    있는 화면의 배치를 바꿀 땐 폭 0 경로를 같이 확인할 것.
    ⚠️ 회귀에서: `n<60` 을 `n\s*<\s*\d+` 로 찾으면 **`ymin<0` 이 대신
    만족**시킨다(#75) — 단어 경계로 집을 것.

### 실수 #188

188. **주식수는 EPS·BPS 의 분모다 — 헤더가 자기 산수를 못 맞추면 주당지표가
    통째로 밀린다**(2026-08-23 서희건설 035890.KQ, 사용자 "지표가 안맞어..난
    네이버(이게 FnGuide) 라서 이걸 제일 신뢰하는데"): 헤더가 `현재가 2,140 ·
    시가총액 4,442억 · 발행주식수 185.4M` 을 나란히 띄웠는데 4,442억 ÷ 185.4M
    = 2,396 이다 — **화면의 세 칸이 서로 안 맞았다**(#33). 네이버 상장주식수
    207,588,536 은 2,140 × 207,588,536 = 4,442억 으로 정확히 맞는다. 즉 시총·
    현재가가 맞고 yfinance `sharesOutstanding` 만 낡은 것이다(⚠️ **이 진단은
    #190 에서 뒤집혔다** — 캡처의 시총만 다른 원천이었고, 평소엔 시총·주식수가
    같이 낡아 항등식이 통과한다). 실측 대조:
    BPS 6,680.92 × 185.4M = 12,386억 vs FnGuide 5,856 × 207.59M = 12,156억
    (1.9% 차) — **자본총계는 사실상 같고 차이가 거의 전부 주식수**였다.
    ⚠️ `pykrx_client.get_kr_market_cap` 이 KRX 상장주식수(+FSC `lstgStCnt`
    폴백)를 이미 돌려주는데 **레포 전체에서 한 번도 안 불리고 있었다**(#150
    '우리가 그걸 다 쓰고 있나' 의 재발 — 정의만 있고 호출 0건).
    처방: 어느 원천이 옳은지 추측하지 말고 **항등식을 얼마나 만족하는가**로
    고른다(`bot/share_count.pick`, #162 효과로 판정). 판정은 3-상태이고
    재료가 없으면 통과가 아니라 **판정 불가**(#54). 못 고칠 때는 헤더가
    **어긋난 사실을 말한다**(#43) — 그러면 이미 구워진 아카이브도 재수집
    없이 정직해진다(#32 의 렌더판).
    ⚠️ 아직 안 끝났다: EPS 는 주식수만으로 설명이 안 된다(1,315.53 × 185.4M
    = 2,439억 vs FnGuide 539 × 207.59M = 1,119억, **2.18배**). 우리 DART
    재무는 연결 총액(비지배 포함)이고 FnGuide 는 지배주주 TTM 이라는 게
    유력하지만 **재기 전에는 가정이다**(#12) → `bot.scripts.kr_metrics_probe`
    가 우리 스냅샷·yfinance·KRX 등록·KRX 투자지표·네이버·밴드탭·DART 를
    나란히 찍고 각 줄마다 항등식을 검산한다(#51 같은 지표를 여러 축에서
    대조하면 외부 자료 없이 판정할 수 있다).
    ✅ 같이 배운 것(가드가 실제로 일했다): 판정을 `_enrich_kr` 안에 뒀더니
    #128 회귀("`_enrich_*` 는 snap 을 아무것도 안 읽는다")가 즉시 깨졌다 —
    그 전제로 보조 6종과 겹쳐 도는 구조라, 읽기 시작하면 겹치기가 조용히
    틀려진다. 시총·현재가를 읽는 단계는 **enrich 밖 공통 단계**
    (`_apply_share_count`)여야 한다. 전제를 회귀로 못박아 둔 값이 여기서
    돌아왔다.

### 실수 #189

189. **판정 사유를 계산해 놓고 라벨만 실으면 사용자는 라벨을 못 읽는다**
    (2026-08-23 밴드 '기준' 칩, 사용자 "이것도 이해가 잘 안가.. 특히 밴드차트
    설명"): `eps_cadence` 는 판정과 **사람이 읽는 사유**를 같이 돌려주는데
    화면은 `분모 = 매달 갱신 EPS(47/48 변동)` 라는 라벨만 싣고 사유를 버렸다
    — #123(계정 불일치) · #129(수주잔고) · #131(FCF 빈칸) 에 이은 **네 번째**
    같은 실수다. 값을 비우거나 판정을 요약하는 코드를 쓸 때마다 "버리는
    정보가 화면에 필요한가"를 그 자리에서 물을 것. 여기선 툴팁(ⓘ)으로 실었다.
    ⚠️ 같이 잡은 것 둘: (a) 그 칩 바로 위 주석이 "밴드 4선은 FnGuide 가 준
    값(**분기 실적 기준**)" 이라고 적고 있었다 — #165 가 실측으로 반증한
    바로 그 주장이 코드에 그대로 남아 다음 사람을 오도한다(설명 out-of-sync
    = 버그). (b) 사유 문구 자체가 "(선행 컨센서스·보간)" 이라고 **재지 않은
    귀속**을 괄호로 단정하고 있었다 — 우리가 잰 건 '매달 바뀐다'까지고
    무엇으로 갱신하는지는 FnGuide 만 안다(#165 의 재발을 그 항목의 산출물
    안에서 발견). 회귀는 문구 리터럴이 아니라 "사유가 라벨보다 길다 +
    payload→칩→title 로 실린다 + 안 잰 것을 주장하지 않는다"로 고정.

### 실수 #190

190. **항등식이 통과해도 두 값이 같은 낡은 기준 위에 있을 수 있다 —
    대조군 없는 자체검산은 눈이 먼다**(2026-08-23 서희건설 VM 실측이 #188 의
    내 진단을 뒤집었다): 캡처를 보고 "시총·현재가는 맞고 주식수만 낡았다"고
    단정해 `시가총액 ÷ 현재가 = 발행주식수` 가드를 배포했는데, VM 은
    `시총 3,967억 · 주식수 185,368,615 · 현재가 2,140` 이라 **항등식이 오차
    0.00% 로 통과**했다 — 진짜 시총은 4,442억(= 2,140 × 207,588,536)이고
    yfinance 는 **둘 다 같은 낡은 주식수 위**에 있었을 뿐이다. 즉 내 가드는
    그 종목을 **한 글자도 못 고쳤다**(#37 임계값이 증상을 덮는 것의 자체검산판,
    #143 대조군이 없으면 '없음'과 '못 받음'을 못 가르는 것과 같은 구조).
    처방: 자체검산(내부 일관성)과 **대조군**(외부 사실)은 다른 축이다 —
    거래소 등록 주식수를 축으로 두고, 교체하면 **시총도 같은 주식수 위로**
    재계산한다(안 하면 헤더가 다시 어긋난다). 시총 재계산은 등록 주식수가
    있을 때만 — 복수 클래스 상장은 한 클래스 주식수 × 주가 ≠ 전체 시총이다.
    실측 효과: BPS 6,680.92 → 5,965.80(FnGuide 5,856 과 1.9%, 남는 건
    비지배지분) · EPS 1,315.53 → 1,174.72.
    ⚠️ 같은 실행에서 드러난 것 셋:
    (a) 내 프로브가 **수집기만** 태워 EPS·BPS 가 전부 '—' 였다 — 국내는
        yfinance 가 fundamentals 를 404 로 주고 화면 값은 **렌더 단계**
        (`_derive_missing_multiples`)가 DART 로 만든다(#35 를 내 도구에서
        또 어겼다 — 이번엔 캐시도 문턱도 아닌 **단계**가 달랐다).
    (b) `stock_screener._fetch_kr_bulk` 가 pykrx 기본값(**KOSPI**)으로 불러
        **코스닥이 통째로 빠져 있었다**(실측 914종목). 스크리너 유니버스가
        조용히 반쪽이었다 — 라이브러리 기본값이 커버리지를 깎는 경우다.
    (c) EPS 2.18배 차이는 주식수로 설명이 안 되고 **한 분기**(2026.2Q
        1,502억 = 나머지 3분기 합의 1.6배)에 몰려 있다. 합만 찍는 진단은
        이걸 못 본다 — 분기별 값과 비중을 같이 찍을 것(#45).

### 실수 #191

191. **폴백이 둘이면 둘 다 죽는 날이 온다 — 그리고 그날 화면은 조용하다**
    (2026-08-23 서희건설 2차 프로브): #190 의 등록 주식수 대조가 **한 번도
    안 돌았다**. KRX 는 `.env` 로드 순서 때문에 수집 시점에 로그인 전이었고
    (같은 실행의 뒤쪽 ③ 에서는 로그인 성공했다 — 즉 `.env` 를 나중에 읽는
    다른 import 가 고쳐 준 것), 금융위 FSC 는 **일일 요청 한도 429** 였다.
    그런데 같은 실행에서 네이버는 상장주식수 207,588,536 을 멀쩡히 줬다 —
    폴백을 하나 더 두는 것이 답이었다(KRX → FSC → 네이버). 폴백 조건은
    '실패'가 아니라 **'요구를 충족했나'**로 잡을 것(#136) — FSC 는 dict 를
    돌려주면서 `shares` 만 없었다.
    ⚠️ 배포 전에 "그 경로가 실제로 실행됐나"를 물었어야 했다(#79) — 나는
    회귀 7건과 뮤테이션으로 **로직**만 확인하고 배포했고, 실행 증거는 다음
    프로브에서야 봤다. 로직이 맞아도 **재료가 안 오면 아무 일도 안 일어난다**.
    ⚠️ 같이 배운 것: 사다리를 task 클로저 안에 두면 테스트가 못 태운다 —
    모듈 함수(`kr_registry_shares`)로 빼야 '앞이 주면 뒤를 안 부른다'는
    반대 증거까지 고정된다(#25 '있다'만 묻는 검사는 눈이 먼다).
    ⚠️ 그리고 진단은 **자격증명 출처를 전부** 찍어야 한다 — 배너가
    DART·DATA_GO_KR 만 찍고 KRX_ID/KRX_PW 를 빠뜨려, '왜 KRX 가 안 됐나'를
    한 라운드 늦게 알았다(#23·#24 열거형은 새 키를 못 잡는다).
    ⚠️ 같은 벽에 두 번: 사용자가 `venv/bin/activate` 가 없어서 한 번(디렉터리가
    `.venv`), 시스템 파이썬으로 돌아서 한 번 헛돌렸다. 안내 문구를 다듬는 대신
    프로브가 **알아서 venv 로 갈아탄다**(Automation-first). 루프 방지는 조건
    **둘**(센티널 환경변수 + 같은 경로)이어야 하고, `os.execv` 는 테스트할 수
    없으니 판정을 순수 함수로 빼서 값으로 고정한다(#41).

### 실수 #193

193. **주당지표의 분자는 '지배주주 귀속분'이다**(2026-08-23 사용자가 FnGuide
    산식을 직접 제시): `EPS = (지배주주지분)당기순이익 × 1000 / 수정평균
    발행주식수(보통주+우선주)` · `BPS = (지배주주지분)자본총계 / 수정기말
    발행주식수(보통주+우선주, 자사주차감)` · `PER = 보통주수정주가(기말)/EPS`.
    우리는 DART 연결 **총액**(비지배 포함)으로 나눠 왔다 — 총액과 지배주주분이
    갈리는 회사에서 EPS 가 통째로 부푼다(서희건설 1,175 vs FnGuide 539).
    대응: `ifrs-full_ProfitLossAttributableToOwnersOfParent` ·
    `ifrs-full_EquityAttributableToOwnersOfParent` 를 canonical 로 추가하고
    주당지표가 그걸 먼저 쓴다. 원천이 안 주면 총액으로 떨어지되 **화면이
    어느 기준인지 밝힌다**(#43). 일반화: 사용자가 신뢰하는 화면의 **산식을
    구해서 그대로 따를 것** — 우리 정의가 '틀린' 게 아니라 **다른 것**이고,
    나란히 놓이는 순간 사용자는 한쪽이 틀렸다고 읽는다(#34).
    ⚠️ 계정을 늘렸으면 디스크 캐시 키를 **반드시** 올린다(v9→v10). 같은
    함수에서 v4·v5·v8 로 세 번 실패했고 회귀가 그걸 강제한다(#95).
    ⚠️ `지배기업 소유주지분` 은 손익계산서와 재무상태표에 **둘 다** 나온다 —
    이름 매칭은 계정과목에 순이익/자본이 함께 적힌 것만 등재하고, 제표 구분
    (`_IS_KEYS`/`_BS_KEYS`)을 2차 방어로 둔다(#34 같은 이름 다른 시리즈).
    ⚠️ `지배주주자본`은 **저량**이라 4분기 차분 금지(`_STOCK_KEYS`).

### 실수 #195

195. **값이 맞는데도 화면이 거짓말처럼 보일 수 있다 — 구성이 특이하면 말하라**
    (2026-08-23 서희건설 035890.KQ 결착, VM 프로브가 DART 원본으로 확정):
    우리 EPS 1,175 vs FnGuide 539 를 세 라운드 동안 '우리 버그'로 쫓았는데
    답은 **일회성 이익**이었다. 26.2Q 순이익 1,502억이 TTM 의 **62%** 이고
    그 분기 **영업이익(364억)의 4.1배**다(영업외이익 ~1,138억). 누적이 분기
    칸에 앉은 것도 아니다 — 반기 누적 1,695억 − 당기 1,502억 = 193억이
    우리 26.1Q 와 **정확히 일치**하고, FY2025 연간 1,206억도 분기합과 같다.
    즉 우리 분해는 옳고 FnGuide 가 그 분기를 아직 안 실었을 뿐이다.
    대응: 한 분기가 TTM 의 40% 를 넘으면 화면이 그 사실을 말한다
    (`_ttm_concentration`) — 값을 고치는 게 아니라 **구성을 밝히는** 것이다.
    ⚠️ 가설 둘이 실측으로 반증됐다: (a) 비지배지분 — 지배주주 비중이 매
    분기 97~102% 라 무관했다(#193 의 계정 추가는 옳지만 이 종목엔 영향 0)
    (b) 누적 오염(#96) — 원본이 당기/누적을 따로 주고 둘 다 맞았다.
    **가설을 코드로 태워 반증한 것이 진단의 전부**였다 — 원본을 안 찍었으면
    또 한 라운드를 '우리 버그'로 썼을 것이다(#109 표본 원문을 같이 찍을 것).
    ⚠️ 그리고 이번에도 `_derived_scope`·`_ttm_note` 를 계산해 놓고 프로브가
    안 찍고 있었다 — #123·#129·#131·#189 에 이은 **다섯 번째** 같은 실수다.

### 실수 #196

196. **비율의 분모 규약이 다르면 두 지표가 같은 방향으로 어긋난다**
    (2026-08-23 기아 000270.KS): 우리 ROE 12.3% · ROA 7.6% vs 네이버
    12.92 · 7.88 — **둘 다 낮은 쪽으로** 어긋난 게 단서였다(값이 제각각
    이면 계정 문제, 같은 방향이면 분모 규약, #51 여러 축 대조). FnGuide
    산식은 **평균 분모**다: `ROE = (지배주주지분)당기순이익 / ((지배주주
    지분)자본총계[당기]+[전기])/2`. 우리는 **기말**로 나눠 자본이 늘어나는
    회사에서 체계적으로 낮았다 — 코드 주석이 "평균을 쓰는 벤더도 있어
    소수점이 다를 수 있으나" 라고 **알면서 적어 두고** 방치했다.
    ⚠️ 분기 표만 고치면 기업 탭 연간 ROE 가 그대로 어긋난다 — 같은 계산을
    하는 화면을 즉시 grep 할 것(#38·#147). `apply_ttm_returns`(분기) ·
    `apply_annual_returns`(연간) 이 같은 규약을 쓰고, 전기를 못 구하면
    기말로 나누되 **그렇다고 말한다**(#43).
    ⚠️ 뮤테이션 둘이 픽스처가 약해 통과했다 — 분자 두 개(당기순이익 ·
    지배주주순이익)를 **같은 값**으로 두면 '지배주주 대신 총액' 변형이
    안 잡히고, 파서 검사를 **소스 문자열**로 쓰면 폴백 제거가 안 잡힌다
    (#91b·#91c). 값을 다르게 두고 파서를 실제로 태워야 발화한다.
    ⚠️ 그리고 ROE·ROA·ROIC 는 **국내 전용 화면**이다(DART 파생) — 다른
    시장엔 그 표 자체가 없다. "다른 나라도 검수" 의 답은 '고칠 게 없다'가
    아니라 '그 surface 가 없다' 였고, 그건 재 봐야 말할 수 있다.

### 실수 #197

197. **화면의 다른 칸으로 나눠 볼 수 없는 행은 결함이다 — 원천을 바꿔서라도**
    (2026-08-23 기아 `PER (선행) 5.50x` 옆에 `EPS (선행) —`): yfinance 는
    국내 종목의 `forwardEps` 를 안 준다. 우리 `_per_mark` 는 "검산 불가"
    라고 정직하게 말하고 있었지만(#186), 사용자는 정직한 빈칸이 아니라
    **값**을 원했다 — 네이버(FnGuide)는 추정PER·추정EPS 를 **둘 다** 준다.
    대응: 국내는 네이버 추정 EPS 를 싣고 배수는 **화면의 현재가로 다시**
    만든다(원천 추정PER 은 전일 종가 기준이라 그대로 실으면 화면의 다른
    칸으로 나눠 봤을 때 안 맞는다, #33·#135). 그 밖의 시장은 yfinance 그대로.
    ⚠️ 원천 id 하나만 믿지 말 것 — `_cns_per`/`_cns_eps` 가 사라지면 조용히
    빈칸이 된다. 라벨 기반 폴백을 같이 두고 **파서를 태우는 테스트**로
    둘 다 고정한다(소스에 정규식이 있는지로 재면 폴백 제거가 통과한다).

### 실수 #198

198. **아카이브 스키마 버전을 손으로 안 올려 fix 가 화면에 한 글자도 안
    닿았다 — 이 레포에서 다섯 번째**(2026-08-23 기아 ROE): #196 으로 ROE 를
    평균 분모로 고쳐 배포했는데 화면은 **12.3% 그대로**였다(75,542억 ÷
    611,905억 = 12.35% = 정확히 기말 분모). `_KR_FIN_SCHEMA_VER` 를 안 올려
    `_e_kr_financials` 게이트가 아카이브에 구워진 값을 그대로 서빙한 것이다.
    그 게이트는 **바로 이 실패를 막으려고 만든 것**인데(#18) 내가 그 규율을
    또 어겼다 — #21b(파싱 캐시) · #95(재무 캐시 v4·v5) · #124(렌더 캐시)에
    이어 다섯 번째다.
    처방: 규율로 기억할 일을 **구조로** 옮긴다(#119) — `kr_fin_signature()`
    가 payload 를 만드는 모듈들(`stock_snapshot`·`dart_client`)의 **소스
    지문**을 내고, 게이트가 버전 숫자 **와** 지문을 함께 대조한다. 지문이
    바뀌면 다음 조회에서 자동으로 다시 수집된다(비용은 DART 7일 디스크
    캐시라 유계).
    ⚠️ 지문 테스트는 **소스에 반응하는지**까지 봐야 한다 — 상수를 돌려주는
    뮤테이션이 통과하면 가드가 눈이 먼 것이다(#91b).
    ⚠️ 같이 드러난 것: 그 표의 각주가 `출처: yfinance` 였다 — K-IFRS 재무
    요약은 **DART** 파생인데(#55 설명이 코드와 어긋나면 버그). 그리고 국내
    선행 PER 재료(네이버 추정 EPS)도 아카이브엔 없어 같은 자리에서 채운다 —
    **새 필드를 더할 때마다 '아카이브엔 없다'를 먼저 물을 것**.

### 실수 #199

199. **서버가 그린 값을 라이브 오버레이가 되돌린다**(2026-08-23 기아 선행
    PER): 국내 선행 PER 을 네이버 추정 EPS + 현재가로 다시 만들도록 고쳤는데
    화면엔 **5.50**(yfinance)이 그대로 떴다 — `/api/quote` 가 `data-q=
    "forwardPE"` 칸을 yfinance 값으로 덮고 있었다. 같은 지표에 **경로가 둘**
    이면 한쪽만 고쳐진다(#38·#183). 단서는 옆 칸이었다: `EPS (선행)` 은 우리
    값(21,895)이 살아 있었는데, yfinance 가 국내 forwardEps 를 안 줘서
    오버레이가 그 칸만 건너뛴 것이다 — **한 행의 두 칸이 다른 원천**이면
    경로가 갈렸다는 뜻이다.
    대응: 규칙을 순수 함수 하나(`kr_forward_from_naver`)에 두고 파생부와
    라이브 오버레이가 **둘 다** 부른다. 그리고 배수는 가격이 확정된 뒤
    블록에서 만든다(후행·PBR 과 같은 자리) — 앞에서 만들면 라이브 가격과
    어긋난다.
    ⚠️ 회귀는 "부르는 곳이 2곳" 으로 못박았다. 한쪽에 규칙을 복제하는
    뮤테이션이 그때 잡힌다.

### 실수 #202

202. **'분모가 다르다'고 말만 하면 안 통한다 — 숫자로 나란히 놓아라**
    (2026-08-23 기아, 사용자 "밸류에이션 탭과 밴드차트간에 차이가 이해가 안가.
    둘다 현재 주가를 기반으로 TTM 으로 하는데 왜 차이가 나지?"): 화면은
    "밸류에이션 탭은 TTM 실적 EPS 기준이라 값이 다릅니다" 라고 이미 적고
    있었는데도 세 번째 같은 질문이 왔다. 문장으로 '다르다'만 말하면 **얼마나·
    왜** 다른지 알 방법이 없기 때문이다.
    되짚어 보면 답이 바로 보인다 — 밴드 6.24x → 분모 **20,984**, 밸류에이션
    7.21x → **18,161**(우리 TTM 실적), 네이버 추정 21,895. 밴드 분모는 실적과
    추정 **사이 76% 지점**이라 우리가 잰 '매달 갱신·변화폭 일정'(#194 보간
    모양)과 정확히 들어맞는다.
    대응: 되짚은 분모를 `denom_now` 로 실어 **숫자로** 적고, 밸류에이션 탭이
    쓰는 분모는 **서버가 찍은 DOM 에서 JS 가 읽어** 나란히 놓는다(#48 JS 가
    문구를 만들지 말 것 · #33 눈으로 나눠 봤을 때 맞아야). 일반화: 두 화면이
    같은 이름의 지표를 다르게 내면 라벨을 다듬는 것으로는 끝나지 않는다 —
    **분모를 값으로 보여줘야** 사용자가 스스로 납득한다.
    ⚠️ PBR 은 분모가 BPS 다 — 비교 대상 셀도 `bookValue` 로 갈라야 한다(#34).

### 실수 #203

203. **라이브 오버레이가 '재계산도 못 한 배수'를 서버 파생값 위에 덮었다**
    (2026-08-23 슈프리마 236200.KQ — #199 의 본체): 화면이 `PER (후행) 3.48x`
    옆에 `EPS (후행) 7,514.88` 을 띄웠다. 56,100 ÷ 7,514.88 = **7.47** 이라
    **화면이 자기 산수를 못 맞췄다**(#33). PBR 0.62(실제 1.32) · PSR 1.24
    (실제 2.66)도 같았다.
    원인: `/api/quote` 는 `price / trailingEps` 로 배수를 다시 만드는데
    yfinance 가 국내 `trailingEps`·`bookValue` 를 **안 준다** → 재계산이
    안 되고, 그러면서 **자기 스냅샷 배수**를 그대로 실어 서버가 그린 DART
    파생값을 덮었다. 앞 라운드(#199)에서 선행 PER 만 고치고 **후행·PBR·PSR
    에 같은 구멍이 남아 있는지 안 봤다** — 한 화면에서 버그를 고치면 같은
    구조를 즉시 grep 할 것(#38·#147).
    규칙: **오버레이는 자기가 다시 계산한 배수만 보낸다.** 분모를 못 주면
    배수도 보내지 않는다(서버 값이 그대로 산다). PSR·EV/EBITDA 는 화면에
    분모 칸이 없어 애초에 재계산 불가라 아예 안 보낸다.
    ✅ 같은 실행에서 확인된 것: ROE(TTM) 네 분기가 FnGuide 와 **전부 일치**
    (13.5/12.8/13.0/19.3 vs 13.54/12.75/13.04/19.30) — #196 평균 분모 fix 가
    실제로 먹었다. 그리고 남은 EPS·BPS 소수점 차이는 **분모 규약**이었다:
    EPS 는 FnGuide 가 수정평균(역산 7,142,857 — 기말 6,974,311 ↔ 직전
    7,257,273 사이), BPS 는 자사주 차감(역산 6,859,105). 값이 틀린 게 아니라
    기준이 다른 것이라 화면이 그렇게 밝힌다(#34).
    ⚠️ 곁다리 둘: (a) 연간 ROE 각주가 `next(items)` 로 **가장 오래된 열**
    (전기가 없어 기말 분모)을 집어 표 전체를 "기말로 나눴습니다"로 설명했다
    — 최신 열로 적을 것 (b) 헤더가 `7.0M` 만 보여 줘 시가총액 ÷ 현재가
    검산을 눈으로 할 수 없었다 — 정확한 주식수를 같이 적는다.

### 실수 #204

204. **"우리가 맞나 네이버가 맞나" — 기준을 찾았다고 답이 아니다. 재야 한다**
    (2026-08-23 슈프리마 EPS·BPS): 나는 K-IFRS 1033 만 보고 "둘 다 네이버가
    맞다"고 답했다가 **프로브 출력에 뒤집혔다**. 실측 분모(지배주주순이익 ÷
    보고 EPS)가 답을 줬다 —
      · DART 보고 EPS 분모 ≈ 6.92~6.97M = 가중평균 **유통**주식수(자사주 제외)
        = **K-IFRS 1033 정의 그대로**
      · FnGuide EPS 분모 ≈ 그 분기 상장주식수(7,257,273 → 6,974,311)
        = 수정평균 **발행**주식수(자사주 **포함**) — 자기 산식에 그렇게 적혀
        있다(BPS 에만 '자사주차감'이 붙는다)
    즉 **EPS 는 DART/우리 쪽이 기준에 가깝고 네이버가 벤더 관례**다(내 첫
    답이 틀렸다). **BPS 는 네이버가 맞다** — 자사주는 유통주식이 아니고
    자본은 이미 취득원가만큼 차감돼 있어 분모에 넣으면 -1.6% 과소된다.
    ⚠️ 그리고 "보고 EPS 4분기 합으로 바꾸면 네이버와 맞겠다"는 가설도 실측이
    부정했다 — 합 7,543 vs 우리 7,523 vs 네이버 7,336 으로 **오히려 멀어진다**.
    바꿨으면 아무것도 안 고치고 코드만 복잡해졌을 것이다. 기준 문서를 찾은
    것과 **그 원천이 실제로 그걸 쓰는지**는 다른 문제다(#25 이름이 아니라 실측).
    · (옛 서술) EPS — 슈프리마는 7,257,273 → 6,974,311 로 283k주가 줄었다:
    · BPS — 자사주는 유통주식이 아니고 자본에 대한 청구권도 없다(자본은 이미
      취득원가만큼 차감돼 있다). 자사주를 분모에 넣으면 **-1.6% 과소**된다.
    우리 방식(기말 상장주식수)은 **화면의 다른 칸과 산수가 맞는다**는 장점이
    있어 틀린 값은 아니지만, 기준으로는 벤더 쪽이 옳다.
    ⚠️ 고칠 재료가 있는지 **재기 전에는 말하지 않는다**(#12): DART 는 보고된
    기본주당이익(`ifrs-full_BasicEarningsLossPerShare`)을 직접 주고 그건
    정의상 가중평균 기준이다 — 우리 canonical `EPS` 로 이미 잡히고 있다.
    다만 분기 EPS 가 당기(3개월)인지 누적인지 먼저 갈라야 합할 수 있다(#96)
    → 프로브가 당기/누적을 병기하고 4분기 합을 찍게 했다.
    ⚠️ 그 프로브 회귀가 처음엔 **소스 문자열**로 재서 출력을 지우는 뮤테이션이
    통과했다 — AST 로 "재무에서 EPS 를 읽는 대입 + 그 변수를 쓰는 print" 를
    확인해야 발화한다(#91b 재는 대상이 맞나).

### 실수 #205

205. **문턱을 둔 '더 나은 원천으로 교체'는 값이 흔들린다**(2026-08-23
    슈프리마 주식수): 등록 주식수와 소스가 **0.11%**(yfinance 6,966,583 vs
    KRX 6,974,311)만 달라 2% 문턱을 못 넘었고, yfinance 값이 남아 시총이
    3,908억으로 네이버 3,913억과 어긋났다. 더 나쁜 건 오차가 문턱을 넘나들면
    **조회할 때마다 기준이 바뀌는 것**이다 — 같은 종목이 어제는 KRX, 오늘은
    yfinance 가 된다. 거래소 등록 주식수는 **정의상 사실**이라 문턱을 둘 이유가
    없다: 있으면 무조건 그걸 쓰고, **사유 문구만** 큰 차이일 때 적는다.
    일반화: 문턱은 '어느 쪽이 나은지 모를 때' 쓰는 것이다 — 한쪽이 정의상
    사실이면 문턱이 아니라 우선순위다.

### 실수 #206

206. **검산 불가한 배수는 남기느니 비우고 사유를 적어라**(2026-08-23
    슈프리마 선행 PER, 사용자 "이 방법은 별로 좋지 않은것 같아. 한국종목의
    경우는 네이버의 컨센서스를 사용하는게 좋을것 같아"): 네이버가 이 종목의
    추정PER·EPS 를 **N/A** 로 줘서 폴백으로 yfinance forwardPE 11.25x 가
    남았는데, 국내는 yfinance 가 forwardEps 를 안 줘 화면에 분모가 없다 —
    사용자가 **눈으로 나눠 볼 수 없는 배수**다(#33). 게다가 그 값은 글로벌·
    외국계 커버리지 집계라 화면의 다른 칸과 기준이 다르다.
    국내는 네이버가 없으면 **비운다**(#29·#32 빈칸이 낫다) — 대신 왜 비었는지
    각주가 말한다(#43). 그 밖의 시장은 yfinance 가 유일한 원천이라 그대로.
    ⚠️ '검산 불가' 문구의 뜻도 사용자가 물었다 — 화면의 EPS 칸으로 그 배수를
    나눠 볼 수 없다는 뜻이다. 진단 문구는 **무엇을 못 하는지**까지 적어야
    한다(#82 '없음'만 말하면 추측을 부른다).

### 실수 #207

207. **각주를 표 안에 넣으면 일부러 맞춰 둔 정렬이 깨진다**(2026-08-23
    K-IFRS 재무 요약): 좌(금액)/우(비율) 두 표는 행 수를 **일부러** 맞춰
    놨는데(배당수익률을 왼쪽에 더한 게 그 때문 — 2026-08-19 ROIC 추가로
    우측이 7행이 되며 어긋난 것을 고친 것) 내가 ROE 산식 각주를 오른쪽 표
    안에 `<tr colspan=2>` 로 넣어 그 정렬을 다시 깼다. 각주는 **표 밖**에.
    ⚠️ 옮기다 보니 옛 버그가 드러났다 — `_kf_note` 를 `=` 로 **두 번 대입**
    하고 있어, 매출 보강 각주와 구성요소 경고가 **둘 다 있으면 앞엣것이
    조용히 사라진다**. 목록으로 모아 이어 붙이게 고쳤다. 같은 변수에 여러
    분기가 `=` 로 쓰면 언젠가 하나가 없어진다 — **append 로 모을 것**.

### 실수 #210

210. **함수 본문을 문자열 replace 로 갈아끼우면 뒤따르는 함수가 통째로
    사라진다 — 그리고 syntax·import 는 통과한다**(2026-08-23 `_derived_desc`
    정리): 각주를 깔끔하게 다듬으려고 `_derived_desc` 본문을 갈아끼웠는데
    범위를 잘못 잡아 뒤의 `kr_forward_from_naver`·`_ttm_concentration`·
    `_derive_missing_multiples` **252줄이 함께 지워졌다**. `ast.parse` 통과,
    `import bot.dashboard` 도 통과 — 호출이 함수 본문 안이라 NameError 는
    **그 화면을 열었을 때만** 난다. 회귀 2,785개 중 그 함수를 직접 import
    하는 테스트 **하나**만 잡았다(#59 의 반대 방향 — 그건 옛 정의가 새 정의를
    가린 경우, 이건 새 정의가 옛 이웃을 지운 경우).
    처방 둘: (a) 함수를 통째로 바꿀 땐 AST 의 `end_lineno` 로 **그 함수 범위만**
    교체할 것(창·인덱스로 자르지 말 것, #60) (b) 규율로 기억하지 말고
    구조로(#119) — 표준 라이브러리 `symtable` 로 `bot/`·`trade/` 전 모듈의
    **정의되지 않은 전역 참조**를 전수 검사하는 회귀를 두었다(외부 린터 의존
    없음, 이름 열거가 아니라 디렉터리 전체 + allowlist, #24).
    ⚠️ 그 가드가 **켜자마자 5건**을 잡았다(#87a): `analyzer` 의 `json`(PM
    override 감사 기록), `telegram_bot` 의 `Path`(trade 누적비용 집계),
    `finviz_client` 의 `yf` 3곳(급등락·JP 상하한가·표시이름). 뒤 넷은 전부
    `except Exception` 안이라 **조용히 빈 결과**였다 — silent-fail 이 몇 달
    동안 기능 하나를 없는 셈 치고 있었다(#12).

### 실수 #211

211. **긴 각주는 '줄바꿈이 이상한' 것으로 보인다 — 기준으로 묶어 평행하게**
    (2026-08-23 "이것도 깔끔하게 잘 정리해주고… 왜 줄 바꿈했는지도 모르겠네"):
    파생 설명이 `BPS(최근분기말), EPS(선행)(네이버 추정), PER(선행)(네이버
    추정), PBR(최근분기말), …` 처럼 **같은 괄호를 여섯 번** 반복했고, 그걸
    **우측정렬 한 줄짜리 출처 줄에 이어 붙여** 브라우저가 아무 데서나 접었다.
    처방 셋: (a) 반복되는 꼬리표는 **키로 묶어** 한 번씩만(기준 3줄) (b) 긴
    문단은 출처 줄에 붙이지 말고 **좌측정렬 블록**으로 뺄 것 (c) 각주는
    **짧은 평행 줄**로(후행/선행/밴드) — 한 줄이 길면 접히는 자리가 매번
    다르다. 회귀는 `<br>` 개수를 세지 말고 조건부(IfExp) 안팎을 **구조로**
    갈라서 볼 것 — 안 그러면 무조건 줄을 하나 더 넣는 변형이 통과한다(#60·#65).
    ⚠️ 같이 잡은 것: **분기실적 탭의 TTM PER 분자가 연결 총액**이었다
    (밸류에이션 탭은 #193 으로 지배주주 귀속분으로 바꿔 놓고 여기를 안 봤다)
    — 사용자가 "다른곳과도 일치되는게 맞는거겠지?"라고 묻지 않았으면 같은
    종목의 TTM PER 이 탭마다 갈린 채로 남았다. 한 화면에서 분자·분모 규약을
    바꾸면 **같은 지표를 내는 다른 화면을 즉시 grep** 할 것(#38·#147).
    ⚠️ 그리고 각주를 **기준별로 묶자** 잠자던 결함 둘이 드러났다(묶기 전엔
    같은 창이 두 번 적혀도 눈에 안 띄었다): (a) 같은 4분기인데 PSR 만 'TTM',
    EPS·PER 은 'TTM 25.3Q~26.2Q' 라 **창이 둘인 것처럼** 그려졌다 (b) 지배주주
    **순이익**은 주는데 지배주주 **자본**은 안 주는 회사에서 `_derived_scope`
    가 통째로 '연결 총액'으로 덮여, EPS 는 지배주주로 만들어 놓고 화면은
    총액이라고 말했다 — **한 라벨이 두 계정을 대표하면 한쪽은 반드시
    거짓말**이다(#34·#55). 파생된 항목만 골라 계정별로 적는다.

### 실수 #212

212. **값이 티커와 정확히 같으면 그건 값이 아니다**(2026-08-23 쿠콘
    294570.KQ `목표가 ₩294,570 (+997.1%)`): 한경 컨센서스 행 파서가 '≥1000
    인 첫 정수'를 목표가로 집는데, 목표가가 빈 행에서 **6자리 종목코드**가
    잡혔다. 위치·형태로 추정하는 판정은 옆 칸이 같은 모양이면 진다(#46).
    파서에 티커를 넘겨 그 셀을 건너뛴다. ⚠️ 그리고 **오늘치 캐시가 옛 값을
    서빙**하므로 캐시 키에 파서 버전을 넣어야 고친 날 바로 반영된다(#21b —
    이 레포에서 여섯 번째). 같이 잡힌 것: 컨센서스 `src` 가 리터럴이라 한경
    폴백으로 내려온 결과에도 'Naver Finance' 라고 적고 있었다 — 같은 값을
    싣는 리서치 탭은 '한경 컨센서스' 라 **화면끼리 어긋난 게 단서**였다(#55).

### 실수 #213

213. **한 지표가 안 맞으면 분자·분모를 따로 물을 것**(2026-08-23 POSCO홀딩스
    005490.KS, 사용자 "네이버랑 비교할 때 우리가 잘못계산하고 있는게 있을까?"):
    BPS 809,716 vs 네이버 759,917(+6.6%) 인데 원인 후보가 **둘**이었다 —
    (a) 지배주주자본이 없어 연결 총액(비지배 포함)으로 나눴나 (b) 분모에서
    자사주를 안 뺐나. 사용자가 준 FnGuide 산식은 `BPS = (지배주주지분)자본총계
    / 수정기말발행주식수(보통주+우선주, **자사주차감**)` 이고, EPS 는 반대로
    자사주를 **포함**한다(#204 역산으로 확인) — **한 화면의 두 지표가 분모
    규약이 다르다**. DART `stockTotqySttus`(주식의 총수 현황)로 유통주식수를
    받아 BPS 에만 쓴다.
    ⚠️ 그리고 BPS 분모를 바꾸면 **PBR 을 같이 바꿔야 한다** — `시총 ÷ 자본`
    으로 만들던 PBR 이 `현재가 ÷ BPS` 와 어긋나 화면이 자기 산수를 못 맞춘다
    (#33). 파생 칸은 화면의 다른 칸에서 만든다.
    ⚠️ ROE 도 같은 뿌리다: 분자는 지배주주순이익인데 분모가 연결 자본총계로
    떨어지면 체계적으로 낮게 나온다. 그런데 각주는 그때도 'FnGuide 산식과
    같은 기준'이라고 적고 있었다 — **폴백이 있는 설명은 폴백일 때 거짓말이
    된다**(#55·#179).
    ⚠️ 배당수익률도 원천이 갈렸다(우리 2.58% vs 네이버 3.23%) — yfinance
    `dividendRate` 가 국내 결산배당을 빠뜨린다. 국내는 네이버(FnGuide) 값을
    쓰되 **라이브 오버레이도 같은 규칙**을 써야 서버가 그린 값을 되돌리지
    않는다(#199 그대로 — 같은 실수를 다른 지표에서 반복하지 말 것).
    ⚠️ 파서 함정: 라벨 뒤 **첫 숫자**를 집으면 기준시점(`2026.02`)이 잡힌다
    — 값은 `%` 가 따라붙는 숫자다(실측으로 발각).

### 실수 #215

215. **주석에 적힌 "원천과 맞는다"가 한 종목만 보고 쓴 것일 수 있다**
    (2026-08-23 FCF): `_attach_fcf` 주석이 "유형·무형 취득을 **둘을 더해야**
    FnGuide CAPEX 와 맞는다"고 단언하고 있었다. LG이노텍 011070.KS 실측은
    반대였다 — FnGuide FY2025 `OCF 13,314 − CAPEX 6,110 = FCF 7,204` 인데
    우리는 5,769 이고, 세 해 모두 차이가 **무형자산취득 크기**와 맞았다.
    사용자가 준 FnGuide 산식도 `CAPEX = 유형자산의증가` 다. 유형만 쓰면
    yfinance `Capital Expenditure`(PP&E 취득)와도 정의가 같아져 **시장 간
    정의가 하나로** 맞는다.
    ⚠️ 사용자가 제안한 '처분액 차감'은 **하지 않는다** — 표준 FCF 의 CAPEX 는
    총 취득액이고, 순취득을 쓰면 자산을 판 해에 FCF 가 부풀려진다. 다만
    이 판정은 **유형자산취득 단독 값이 FnGuide CAPEX 와 같은가**로 확정되므로
    프로브가 구성요소를 전부 찍게 했다(#109 표본을 같이 찍을 것).

### 실수 #216

216. **파싱 결과 캐시에 버전이 없으면 같은 날 고친 게 안 보인다 — 일곱
    번째**(2026-08-23 `get_share_totals`): 발행주식수를 수권주식수에서 실제
    발행분으로 고쳐 배포했는데 **같은 날 프로브가 여전히 200,000,000** 을
    찍었다. `_disk_cache_daily` 가 (함수명·티커·날짜)로만 키를 만들어서다.
    #18(아카이브)·#21b(파싱)·#95(재무)·#124(렌더)·#198(스키마)·#212(한경)에
    이어 같은 병이다 — 규율로는 매번 진다. 데코레이터에 `@_cache_ver(n)` 를
    두어 **파서가 바뀐 함수만** 버전을 키에 싣게 했다.
    ⚠️ 그리고 이 실수는 **프로브 출력이 그대로였기 때문에** 잡혔다 — 고친 뒤
    출력이 한 글자도 안 변하면 캐시부터 의심할 것(#21b 그대로).
    ⚠️ 같이 잡은 프로브 결함 둘: (a) 분기 항목은 비율을 **평평하게** 싣는데
    `ratios` 중첩만 읽어 멀쩡한 ROE 를 `—` 로 오보했다 (b) TTM 을 **총액**
    으로만 찍어 화면(지배주주 귀속분)과 다른 EPS 를 말했다(POSCO 17,428 vs
    화면 17,004) — 프로브는 화면이 읽는 그 자리를 읽어야 한다(#35).

### 실수 #217

217. **역산 분모는 '분모가 다르다'의 증거가 아니다 — 분자가 다를 수도
    있다**(2026-08-23 POSCO EPS 1.6%): 나는 "FnGuide 역산 분모 80,507,495 >
    기말 79,241,527 이니 수정평균이라 그렇다"고 말했는데, 그 역산은 **우리
    분자가 곧 그들 분자**라는 가정 위에 있다(미지수 둘, 식 하나). 같은
    프로브에 반증 재료가 이미 있었다 — DART 보고 EPS 로 되짚은 분기별
    가중평균 유통주식수가 **네 분기 모두 75.62M 로 평평**했다(자사주 소각이
    있었다면 발행 평균만 커질 수 있으니 완전 반증은 아니지만, 단정할 근거는
    없다). 값 하나에서 두 미지수를 읽지 말 것(#12).
    대응: 사용자 요청대로 EPS 분모를 **수정평균 발행주식수**(FnGuide 산식)로
    바꾸되, 그 재료(`stockTotqySttus` 분기별 발행주식수)를 **실제로 받아서**
    평균을 내고 화면·프로브가 이력을 그대로 찍게 했다 — 변동이 없으면
    평균 = 기말이라 EPS 가 안 바뀌고, 그러면 남은 차이는 분자에서 온 것임이
    **자동으로 드러난다**(고치면서 동시에 판정한다).
    ⚠️ 분모를 바꾸면 PER 도 **화면의 EPS 에서** 만들어야 한다 — `시총 ÷
    순이익` 으로 두면 `현재가 ÷ EPS` 와 어긋난다(#33, PBR 과 같은 이유).
    ⚠️ 창의 분기가 하나라도 비면 평균을 **만들지 않는다** — 3개로 낸 평균을
    그 창의 값이라 부르면 거짓말이다(#99).

### 실수 #219

219. **한 화면의 두 칸이 다른 원천이면 경고문이 안 보이는 값을 가리킨다**
    (2026-08-24 BYD 1211.HK, 사용자 "왜 이런 오차가 나오지? 그냥 야후에서
    받아오는거잖아"): 헤더 경고가 `시가총액÷현재가 = 9,117,197,357주 와
    -59.6% 차이` 라고만 적었는데, **그 시가총액은 화면에 없다** — 서버는
    yfinance 시총(전 클래스, HK$8,479억)으로 계산했고 라이브 오버레이는
    네이버 해외의 H주 시총(HK$3,431억)으로 카드를 덮었다. 사용자는 카드
    숫자로 되짚을 수 없으니 "왜?"를 물을 수밖에 없다.
    대응: 경고가 **비교한 시가총액을 카드와 같은 문자열로** 적는다(#202
    '다르다'만 말하면 안 통한다 · #43). 그리고 A주+H주처럼 복수 클래스·복수
    상장이면 시총(전 클래스)과 발행주식수(한 클래스)의 **모집단이 달라**
    항등식이 원래 안 맞는다는 것을 밝히되 **단정하지 않는다** — 우리는 다른
    클래스의 존재를 재지 않았다(#165).
    ⚠️ 남은 것: 같은 회사 시총이 헤더(H주 3,431억)와 동종비교표(전 클래스
    8,493억)에서 2.5배 갈린다 — 라이브 오버레이가 시총만 다른 원천으로
    바꾸기 때문이다(#34). 어느 모집단을 표준으로 할지는 사용자 결정 사항.

### 실수 #220

220. **같은 지표를 두 탭이 다른 원천으로 그리면 사용자는 한쪽이 틀렸다고
    읽는다**(2026-08-24 NHN KCP 060250.KQ, 사용자 "아 너무 헷갈려. 특히
    FCF"): 재무제표 탭 = **yfinance**, 밸류에이션 탭 분기표 = **DART** 다.
    매출·영업이익은 세 곳(우리 둘 + 네이버)이 완전히 같은데 순이익은
    yfinance 가 **지배주주**(136/124/161), DART 표가 **총액**(138/125/162)
    이라 라벨이 같은 채로 갈렸다 — 둘 다 맞는 값이다(#34). 두 표면이
    **서로를 가리키며** 원천과 계정 기준을 밝히게 했다(#186).
    ⚠️ FCF 는 진짜로 갈렸다: yfinance 경로는 네이버와 ±1~2억인데 DART
    경로만 25.3Q 370(네이버 331) · FY2025 2,026(네이버 1,956)이다. 합만
    보면 '어딘가 다르다'까지고 재료(OCF·유형자산취득)를 **분기별로** 놔야
    보인다 → 프로브 v12 가 분기 CF 구성요소를 찍는다(#109).
    ⚠️ 그리고 카드가 `Fwd N/A` 였다 — **아카이브 스냅샷엔 `naver_val` 이
    없다**(밸류에이션 탭은 상세 보강이 채운다). 새 필드를 더할 때마다
    '아카이브엔 없다'를 먼저 물을 것(#198 그대로).
    ⚠️ 부수: 중첩 함수 본문에서 바깥 변수(`is_kr`)를 그냥 쓰자 그 함수를
    **떼어 태우는 회귀 12개가 NameError** 로 무너졌다 — 인자로 받을 것.
    그리고 고정 길이 창으로 소스를 재던 테스트가 주석 한 줄에 또 무너졌다
    (#60 이 세션 세 번째) — 창이 아니라 함수 **전체**에서 셀 것.

### 실수 #223

223. **"왜 네이버랑 다르지?" 의 답이 대개 '네이버가 아직 안 실었다' 다 —
    그걸 재서 말하게 했다**(2026-08-24 이오테크닉스 039030.KQ): 우리 TTM
    (25.3Q~26.2Q) EPS 8,808 vs 네이버 6,008. 되짚으니 **지배주주 25.2Q~26.1Q
    합 74,018,885,672 ÷ 12,319,550 = 6,008.25** — 네이버가 한 분기 뒤처진
    것이고 우리가 맞다(BPS·PBR 도 같은 이유로 갈렸다: 네이버 PBR 7.05 →
    26.1Q 자본 기준). 같은 질문을 세 번 받았으므로(NHN KCP·POSCO·이오테크닉스)
    화면이 **창을 되짚어** 말하게 했다(`naver_ttm_window`).
    ⚠️ **단정하지 않는다** — 되짚은 창이 오차 1% 안으로 맞을 때만 말하고,
    최신 창이 맞으면 침묵한다(#165). 어느 창과도 안 맞으면 아무 말도 안 한다.
    ✅ 같은 실행에서 확인된 것: POSCO BPS 우리 759,917.29 = 네이버 Financial
    Summary **759,917**(지배주주자본 57.4655조 ÷ 유통 75,620,779). 네이버
    투자정보 패널의 751,025 는 **한 분기 전**(2026.03 지배자본 56.7931조 ÷
    같은 유통주식수) 값이다 — 원천 안에서도 화면마다 분기가 다르다.

### 실수 #224

224. **모집단이 다른 두 값을 한 화면에 두면 어느 쪽으로든 통일해야 한다**
    (2026-08-24 BYD 1211.HK, 사용자 "H주로"): yfinance 는 A주+H주 **전체
    시총**과 H주만의 **발행주식수**를 같이 준다. 헤더(네이버 H주 3,431억)와
    동종비교표(전 클래스 8,493억)가 같은 회사를 2.5배 다르게 적고 있었다.
    `listed_market_cap` 하나로 **거래되는 클래스**(현재가 × 발행주식수)로
    맞췄다 — 단일 클래스에서는 둘이 같아 **no-op** 이라 다른 시장은 영향이
    없다(시장 게이트 불필요).
    ⚠️ 국내는 **등록 주식수가 먼저다** — 자체검산은 어느 쪽이 낡았는지 모른다
    (#190). 거래소 원천이 있으면 이 재계산을 타지 않게 가드를 뒀고, 그
    가드는 픽스처가 **실제로 발동하는 값**이어야 검증된다(첫 픽스처는 오차가
    작아 분기를 못 태워 뮤테이션이 통과했다, #91c).

### 실수 #225

225. **비율의 분자와 분모를 따로 고르면 급이 갈린다**(2026-08-24
    뉴파워프라즈마 144960.KQ ROE): `apply_ttm_returns` 가 분자는
    "지배주주순이익이 전 분기에 있으면", 분모는 "지배주주자본이 있으면"
    으로 **따로** 판정했다. 한 분기라도 지배주주순이익이 없으면 분자만
    연결 총액으로 떨어지고 분모는 지배주주자본으로 남아 — 비지배지분이
    자본의 33%인 회사에서 ROE 가 크게 부풀려졌다(우리 26.1Q 8.0% vs
    네이버 5.96%). 총액/총액 · 지배/지배 **둘 중 하나로만** 갈 것(#34).
    각주가 `당기순이익(연결 총액) ÷ 평균 지배주주자본` 이라고 **정직하게
    적고 있었는데도** 그게 잘못이라는 걸 못 알아봤다 — 표시는 제 일을 했고
    읽는 쪽이 놓친 것이다.
    ⚠️ 그리고 이름 열거는 새 표기를 못 잡으므로 `지배주주순이익 =
    당기순이익 − 비지배순이익` 항등식을 최종 그물로 뒀다(#214 자본판의
    손익 버전). 실측으로 네이버 값을 정확히 재현했다(155 − 36 = 119).
    ✅ 같은 실행에서 확인된 것: **FCF 는 세 분기 모두 네이버와 정확히
    일치**(122/31/-108) — #221 의 `영업에서창출된현금흐름` 제거가 통했다.

### 실수 #227

227. **`abs()` 를 차분 뒤에 걸면 뺄셈이 덧셈이 된다**(2026-08-24 S-Oil
    010950.KS 25.4Q FCF -47,120억, FnGuide +6,151억): DART 는 같은 취득
    계정을 보고서마다 음수(현금유출)로도 양수(취득액)로도 준다 — 우리도
    그걸 알아서 `_attach_fcf` 가 `abs()` 를 쓴다(#88). 그런데 그 `abs()` 는
    **누적 차분이 끝난 뒤**라, 연간(+X)과 9개월 누적(−Y)의 부호가 갈리면
    `X − (−Y) = X + Y` 로 CAPEX 가 두 배 넘게 부푼다. 부푼 CAPEX 는 '큰
    설비투자'처럼 보여 **눈으로는 안 잡힌다**(#34 의 부호판). 크기 규약은
    값을 **쓰는 곳이 아니라 산술이 일어나는 가장 이른 곳**에 걸 것
    (`_cum_magnitude`, 부호가 이미 맞으면 no-op).
    ⚠️ 영업활동현금흐름은 크기로 보면 안 된다 — 음수 누적이 정당하다
    (FnGuide 실측 S-Oil 2026/03 -5,239억). 같은 표에 있어도 항목마다
    규약이 다르다.
    ⚠️ 그 회귀가 처음엔 눈이 멀었다: OCF 를 유출 목록에 넣는 뮤테이션이
    통과했다 — 픽스처의 OCF 두 값이 **둘 다 양수**라 `abs()` 가 no-op
    이었기 때문이다. 부호 규약을 검증하려면 픽스처에 **음수를 넣어야**
    한다(#91b 재는 대상이 맞나 → #91c 깨지는 값까지 밀어 볼 것).
    ⚠️ 진단에서 배운 것: 같은 화면의 손익 3칸(매출·영업이익·순이익)이
    FnGuide 와 **한 자리도 안 틀렸는데** FCF 만 갈렸다 — 그러면 원인은
    회사도 원천도 아니고 **현금흐름 경로 한 곳**이다. 그리고 25.3Q 는
    -285억으로 정확히 맞았으니 **4분기 파생 경로**로 더 좁혀진다. 어느
    칸이 맞는지를 세면 고칠 자리가 저절로 좁아진다(#51).
    ⚠️ 다만 이 fix 가 S-Oil 의 원인이라는 것은 **아직 실측이 아니다** —
    구조적 결함이라 그 자체로 고칠 값어치가 있어 먼저 고쳤고, 확인은
    `kr_metrics_probe 010950.KS` 의 분기 재료로 한다(#79 "그 경로가 실제로
    실행됐나"를 배포 전에 답할 수 없으면 그렇게 밝힐 것).

### 실수 #229

229. **주가가 없는 구간에도 밴드선은 값이 있다**(2026-08-24 와이지-원
    019210.KQ '그 배수에서의 주가'): 적정주가를 `top.get(price[-1][0])` 로
    만들었는데, FnGuide 는 **전망 구간까지 밴드선을 주고** 그 구간의 주가는
    None 이다 — `price` 배열의 마지막 원소는 전망 끝이라 적정주가가 **전망
    EPS**(3,038)로 만들어졌고, 캡션이 적는 분모는 마지막 **관측**(2,253)
    이었다. 그래서 최저 4.80x → 14,582 가 현재가 14,600 과 거의 같아졌는데
    같은 표의 현재 PER 은 6.48x다 — **표가 자기 산수를 못 맞춘 것**(#33).
    파생 칸은 **화면의 다른 칸에서** 만들 것: `적정주가 = 배수 × denom_now`
    (밴드선(t) = 배수 × 분모(t) 이므로 값은 정의상 원천 것 그대로이고,
    사용자가 눈으로 곱해 검산할 수 있다).
    ⚠️ 검산법이 하나 늘었다: **현재가는 현재 배수를 감싸는 두 배수의
    적정주가 사이**에 있어야 한다. 그게 깨지면 두 칸의 기준이 갈린 것이다
    — 회귀로 고정했다(비-KR `assemble` 은 이미 `eps_now = rows[-1][2]` 로
    옳았다: 같은 계산이 시장마다 갈려 있었던 것, #38).
    ⚠️ 진단은 **구조 가설을 코드로 태워** 했다(#118) — 전망 꼬리를 붙인
    합성 블록이 화면의 네 숫자(34,329.40 · 27,645.80 · 21,266.00 ·
    14,582.40)를 **센트까지 그대로** 재현해 원인이 확정됐다.

### 실수 #232

232. **표를 복제하지 말고 렌더러에 원천을 인자로 주라**(2026-08-24 비-KR
    재무추이): "미국종목도 한국종목처럼" 요청에 표를 새로 짜면 행 구성·정렬·
    각주가 곧 갈라진다(#38·#63). `_kr_fin_trend_table` 에 `fmt`(통화)·
    `source`(각주 문구)·`sibling_note` 만 받게 하고, 값은 `bot.fin_trend` 가
    yfinance 라인아이템을 **canonical 한글 키**로 옮겨 DART 경로와 **같은
    함수**(`calc_kr_financial_ratios`·`apply_ttm_returns`)에 태운다 —
    ROE 평균분모 규약(#196)을 두 번 따로 고치는 일이 없다.
    ⚠️ 원천이 늘면 **각주의 고유명사도 갈라야 한다** — 'FnGuide 산식과 같은
    기준'·'DART 현금흐름표'는 KR 전용이다. 그 문구를 비-KR 표가 그대로 적으면
    화면이 거짓말한다(#55). 그리고 그 회귀를 **페이지 전체 grep** 으로 쓰면
    밴드 탭 ℹ️ 가이드의 'FnGuide' 가 대신 만족시킨다 — **그 표 하나를
    잘라내서** 볼 것(#55 실측 재발).
    ⚠️ 화면에 찍는 값은 다른 탭과 **같은 라인아이템**이어야 한다 —
    `당기순이익` 을 여기서만 `Net Income Including Noncontrolling Interests`
    로 고르면 같은 분기가 탭마다 다른 숫자가 된다(#34). 비율의 분자·분모는
    그와 **별개로** 같은 급끼리 고른다(#225).
    ⚠️ 열 수를 제목에 적으면 **표에서 파생**시킬 것 — 리터럴 '최근 4분기'가
    5분기로 바뀐 표 위에 그대로 남아 있었고, 그 리터럴로 호출부를 찾던
    회귀가 같이 깨졌다(#19 소스 문자열, 이 세션 두 번째).

### 실수 #235

235. **`or 0` 은 '없음'을 '0'으로 바꾼다**(2026-08-24 LS ELECTRIC 010120.KS
    연간 차트): `r.get("Total Revenue", 0) or 0` 라 야후가 안 채운 가장
    오래된 연간 열이 **0 짜리 막대**로 그려졌다 — 렌더러는 이미 None 을
    '—' 로 그리는 경로를 갖고 있었고(FCF 가 그렇게 동작한다) 그 경로를
    `or 0` 이 막고 있었다(#43·#181 빈칸은 '0'으로 읽힌다).
    ⚠️ 그리고 빈 열이 하나 섞이면 **실제로 보이는 해가 4개**가 된다 →
    연간을 한 해 더 받아(`max_periods=6`) 빈 열을 걷어낸다(#44a).
    ⚠️ 그 회귀의 첫 픽스처가 눈이 멀었다 — 6개를 받으면 빈 가장 오래된 열은
    `[-5:]` 슬라이스만으로도 잘려, 필터를 지우는 뮤테이션이 통과했다. 원천이
    **5개만 주는** 경우(실제 증상)로 강제해야 발화한다(#91c).

### 실수 #236

236. **주식분할은 소급조정 대상이다 — 계단 평균이 분모를 분할비만큼 깎았다**
    (2026-08-24 LS ELECTRIC 010120.KS, 사용자 "PER (후행) 숫자가 너무 안맞는것
    같은데"): 2026.06 에 5:1 액면분할이 있어 창이 `30M·30M·30M·150M` 이었고
    #218 의 계단 평균이 **60,000,000** 을 냈다 — TTM 지배주주순이익
    388,760,977,637 ÷ 60M = EPS 6,479.35(네이버 2,592 의 2.5배). 분할·무상
    증자는 **자원 유입 없이 주식 수만** 늘어나므로 K-IFRS 1033·FnGuide 모두
    표시 전 기간을 분할 후 기준으로 **소급조정**한다. 150M 으로 나누면
    2,591.74 — 네이버와 한 자리도 안 틀린다.
    ⚠️ 유상증자는 소급하면 안 된다(자원이 들어온다) — **정수비 여부**로
    가른다(#146 증상이 아니라 원인으로). 분할은 5:1·2:1 처럼 정확한 정수비다.
    ⚠️ 그 회귀의 첫 픽스처(1.35배 유상증자)가 눈이 멀었다 — `round(1.35)=1`
    이라 정수 근접 검사를 지워도 None 이 나온다. **반올림이 2 인데 가깝지는
    않은 값**(2.4배)으로 강제해야 발화한다(#91b).
    ⚠️ 이 종목은 화면 자체는 일관됐다(187,000÷6,479.35=28.86 ✅) — 틀린 건
    **분모 하나**였고, 잡은 방법은 네이버·재무제표 탭과 **값을 대조**한 것이다.

### 실수 #241

241. **'분모가 화면에 없다'는 판정을 화면이 바뀌면 다시 물어야 한다**
    (2026-08-24 NC소프트 036570 PSR): #234 에서 PER·PBR 만 화면 분모로 다시
    만들고 PSR 은 '검산할 분모가 화면에 없다'며 소스값을 그대로 뒀다. 그런데
    같은 탭 **분기별 재무추이 표에 매출이 분기별로** 실려 있어 넷을 더하면
    검산된다 — 시총 4조 8,479억 ÷ TTM 매출 20,921억 = **2.32x** 인데 화면은
    **2.09x**(야후 제공값)였다. 표를 5분기로 늘리고(#231) 비-KR 까지 붙이면서
    화면이 바뀌었는데 그 판정을 다시 안 물은 것이다.
    ⚠️ 이 종목은 나머지가 **전부 맞았다** — 분기표 7행 × 4분기가 FnGuide 와
    한 자리도 안 틀렸고(FCF 포함), PER·PBR·선행 PER·배당수익률·52주 위치도
    검산이 맞았다. **한 칸만 틀린 화면은 전체가 맞아 보인다** — 항목을 하나씩
    나눠 봐야 나온다(#33 의 검산 규율이 실제로 잡아낸 사례).
    ⚠️ 비-KR 은 `rev` 가 None 이라 이 분기를 안 타고 소스값이 그대로 산다 —
    **시장 게이트 없이** 그렇게 된다(재료 유무가 곧 조건).

### 실수 #242

242. **`or 0` 이 비율 계산기에도 있었다 — '없음'이 0.0% 로 찍혔다**
    (2026-08-24 NVDA 연간 부채비율 네 해 모두 `0.0%`): 실제는 31.5%(부채
    $49.51B ÷ 자본 $157.29B)다. `calc_kr_financial_ratios` 가 `dbt =
    financials.get("부채총계") or 0` 이라 값이 없으면 `0/자본×100` = 0.0 을
    낸다 — 빈칸이면 '—' 로 보여야 한다(#235 의 비율판, 같은 병이 다른 모듈에
    또 있었다 #38·#147). 유동비율도 같았다.
    ⚠️ **진짜 0 은 살려야 한다** — 무차입 회사의 부채비율 0% 는 사실이다.
    그래서 `or 0` 을 지우는 게 아니라 **분자가 None 인지**로 가른다.
    ⚠️ 분모(자산·자본)는 0 이면 어차피 비율을 안 만들므로 `or 0` 이 안전하다 —
    같은 함수 안에서도 자리마다 판정이 다르다.

### 실수 #243

243. **발행사는 태그를 바꾼다 — '데이터가 있는 첫 후보'에서 멈추면 옛 태그에
    굳는다**(2026-08-24 NVDA SEC XBRL): 매출만 `연간 $26.91B FY2022 · 최근
    분기 $3.10B(2020-01-26)` 였고 다른 행은 전부 FY2026 이었다. `_XBRL_METRICS`
    의 후보를 순서대로 돌다 **아무 데이터나 있으면 break** 했기 때문이다 —
    NVDA 가 옛 태그를 FY2022 에 버렸는데 우리는 거기 멈춰 있었다(#150 '우리가
    그걸 다 쓰고 있나' 의 태그판). 후보를 전부 보고 **가장 최근 데이터**를
    고른다(동률이면 목록 순서=선호도가 이긴다). 비용은 12시간 디스크 캐시가
    유계로 만든다(404 도 빈 값으로 캐시).
    ⚠️ 한 행만 다른 연도면 **그 지표의 태그**를 먼저 의심할 것 — 나머지가
    전부 맞으면 원천 장애도 캐시도 아니다.

### 실수 #246

246. **'분기 길이 우선'은 그 길이가 옛 분기에만 있으면 거기 붙박이가 된다**
    (2026-08-24 LRCX SEC XBRL 표): 매출·순이익·EPS 는 `25-12-29~26-03-29`
    (FY2026 3분기)인데 **영업현금흐름만 `25-06-30~25-09-28`**(1분기)로 두
    분기 뒤처져 있었다. 대부분의 미국 제출사는 **현금흐름표를 누적(YTD)으로만**
    내므로 분기 길이 팩트가 회계연도 1분기에만 있고, #238 에서 넣은 '80~100일
    우선'이 전 팩트에서 그 하나를 찾아 `max(end)` 를 거기로 고정한다. 열
    헤더는 '최근 분기'라고 단정하는데 **열은 세로로 읽히므로** 행마다 '최근'이
    다르면 그 열의 정의가 갈린다(#32·#45). 값은 다 '있어서' 아무 감사도 안
    걸렸다. 대응: 같은 `start` 를 공유하는 두 누적을 빼서 그 분기를 복원한다
    (DART 4분기 차분·EDGAR 결산분기 복원과 **같은 규율**, #96) — 구조 가드는
    `start` 일치·`fy` 일치·결과 길이 80~100일, 그리고 **형제 크기 대조**(#167,
    원천이 분기로 준 팩트가 있으면 그 최대의 5배 초과는 거부). 원천이 그
    형태로 준 값이 아니므로 화면이 **복원했다고 밝힌다**(#43·#131).
    ⚠️ 같은 자리에서 **연간 쪽은 한 번도 안 봤다** — `fp == "FY"` 만 보면
    누적 팩트가 연간 칸에 앉을 수 있다(#238 은 '최근 분기'만 고쳤다).
    한 해 길이(350~380일)를 우선하되 없으면 그대로 둔다(#171 가드가 '못
    만든다'로 끝나면 그 자리가 영원히 빈다).
    ⚠️ 뮤테이션 둘이 픽스처·단언이 약해 통과했다: (a) 연간 가드는 한 해 길이
    팩트가 **마침 `end` 도 가장 늦어서** `max(end)` 만으로 뽑혔다 — 누적이 더
    늦게 끝나는 경우로 강제해야 발화한다(#91c) (b) `"복원" in html` 은 **각주가
    대신 만족**시켜 Form 열 라벨을 지워도 통과했다 — 그 셀이 만드는 문자열
    (`기간 · 복원`)을 집어야 한다(#75).

### 실수 #247

247. **원천이 한 분기 늦으면 화면이 낡은 줄도 모른다 — 대조군은 이미 받아
    뒀는데 안 쓰고 있었다**(2026-08-24 AMAT, 사용자 "가장 최근실적이 3Q
    아닐까? 이건 아직 야후는 안올라온것 같은데... 회사마다 다른거 참 힘들다"):
    AMAT 는 10월 결산이라 2026-07-26 종료 분기가 이미 공시됐는데 yfinance
    분기 손익은 **2026-04-26 에서 멈춰** 있었고, 분기실적 카드·손익계산서 표는
    그걸 그냥 '최근 분기'라고 적었다. 사용자가 **회사 결산월을 외우고 있어야**
    낡은 줄 알 수 있다 — '조용한 것'과 '죽은 것'을 화면이 구별 못 한 것이다
    (#52 의 재무판 · #43). 값은 다 '있어서' 아무 감사도 안 걸렸다(#96).
    대조군은 **이미 받아 둔 SEC XBRL** 이었다 — 새 원천을 찾을 필요가 없었다
    (#150 '우리가 그걸 다 쓰고 있나' 의 재발 · #143 대조군 없이는 '없음'과
    '못 받음'을 못 가른다). SEC 가 아는 가장 늦은 기간 끝이 원천보다 한 분기
    넘게 앞서면 화면이 그렇게 말한다(`quarterly_series.source_lag_note`).
    ⚠️ 문턱은 **기간 끝 관례 차이**(며칠)를 걸러야 한다 — 그걸로 경고하면 늘
    뜨고, 늘 뜨는 경고는 아무것도 안 재는 것과 같다(#25). 참조가 미래면
    판정하지 않는다(#12). 그리고 낡은 원천을 그리는 표면이 둘이므로(분기실적·
    재무제표) **한 곳에서 만들어 둘 다** 싣는다 — 복제하면 한쪽만 고쳐진다
    (#38·#147).
    ⚠️ 사용자가 알아챈 단서는 **화면 안에 이미 있었다**: 헤더 '다음 실적
    2026-11-12' 옆에 표의 최신 분기가 2026-04 면 보고 간격이 7개월이라는
    뜻인데, 그런 미국 제출사는 없다. 화면 칸끼리 대조하면 외부 자료 없이도
    판정할 수 있다(#51) — 그 대조를 **기계가** 하게 만들 것.

### 실수 #248

248. **오버레이가 분자를 갈아끼우면 파생값도 같이 갈아끼워야 한다 — 안 그러면
    오버레이가 불일치를 만든다**(2026-08-24 컴투스 078340.KS, 사용자 "왜 TTM
    PER 가 다른거야? … 왜 같은 이슈가 계속 나오는지 도무지 이해가 안 간다"):
    화면이 현재가 ₩31,750 · EPS 981.33 · BPS 84,138.18 을 띄우면서 PER 39.64x ·
    PBR 0.46x · PSR 0.73x 를 실었다. 세 배수를 각각 되짚으면 **전부 ₩38,900**
    (스냅샷 수집 시점 주가)이다 — 한 칸이 아니라 세 칸이 같은 값을 가리키면
    원인은 계산이 아니라 **분자 하나**다(#51 여러 축 대조). 서버 렌더는 그
    시점 기준으로 일관됐고, 라이브 오버레이가 가격·시총·52주 위치만 갈고
    배수는 안 갈아 **오버레이가 불일치를 만들었다**(#33).
    원인은 #203 의 규칙("분모를 못 주면 배수도 보내지 않는다 — 서버 값이
    그대로 산다")이 **yfinance `vals` 만 분모로 봤기** 때문이다. 국내는
    yfinance 가 EPS·BPS 를 안 주지만 **화면은 이미 DART 로 만든 분모를 그리고
    있다**(#183 전용 호출이 실패하면 화면이 이미 아는 값으로 되돌아갈 것) —
    포기가 아니라 화면의 분모를 쓰는 게 답이었다.
    대응: `live_multiples()` 가 스냅샷의 **분자만 라이브로 바꿔**
    `_derive_missing_multiples` 를 그대로 태운다 — 규칙을 복제하지 않으므로
    서버 렌더와 오버레이가 정의상 같은 규약이다(#38·#199). 불변식은
    "**화면의 분자 ÷ 화면의 분모 = 화면의 배수**" 하나(#33).
    ⚠️ 그리고 카드의 `TTM PER*`(31.11 = 시총 ÷ TTM 순이익)는 별표가 곧
    "배수를 못 받아 시총 기준으로 떨어졌다"는 신호였다 — **별표 유무가 두 탭이
    갈렸다는 조기 경보**다. 별표가 뜬 화면을 보면 그 경로에 배수가 왜 없는지
    먼저 물을 것.
    ⚠️ 옛 회귀가 "PSR 은 화면에 분모 칸이 없으니 보내지 말라"고 못박고 있었다.
    #241 로 분기 매출이 화면에 실리며 그 전제가 깨졌는데 회귀는 그대로였다 —
    **전제가 바뀐 계약은 지우지 말고 다시 쓸 것**(#222). 다시 쓸 때 핵심 계약
    ("재계산한 것만 보낸다")은 남기고 목록 포함 여부가 아니라 **게이트**를
    재도록 바꿨다(뮤테이션으로 옛 슈프리마 증상을 여전히 잡는지 확인).

### 실수 #249

249. **한 카드가 '오늘'을 두 개 쓰고 있었다**(2026-08-24 변동성 카드, 회귀가
    스스로 발각): 장중/종가 라벨은 `_market_today(market)`(시장 현지일)로
    판정하는데 지연 배지(`_vol_age_days`)는 **KST** 로 재고 있었다. 같은
    판정을 두 곳이 다른 출처로 하면 갈라지고(#38), '오늘'을 KST 로 잡으면
    미국이 하루 앞선다(#40). 그 결과 카드가 `08-20 장중` 이라고 써 놓고 바로
    아래 각주가 `2026-08-20 종가를 기준으로` 라고 적어 **자기 라벨을 뒤집었다**
    — 각주가 '종가'를 **하드코딩**하고 있었기 때문이다(#43a·#55 의 각주판).
    파생 라벨은 카드가 이미 계산한 `_kind` 를 **재사용**할 것.
    ⚠️ 이 결함은 **시간이 지나면서** 빨간불이 됐다 — 픽스처 날짜와 실제 오늘의
    간격이 지연 문턱을 넘는 날에야 각주가 뜬다. 날짜에 의존하는 테스트는
    "오늘이 언제냐에 따라 다른 코드가 도는가"를 먼저 물을 것(#42 휴일 분기의
    반대 방향 — 그건 안 타서 문제, 이건 나중에 타서 문제).
    ⚠️ 그리고 배선 뮤테이션(`_vol_age_days(date)` 로 되돌리기)이 **한 번
    통과했다** — 헬퍼를 직접 부르는 테스트와 그 헬퍼를 통째로 monkeypatch 하는
    렌더 테스트뿐이라 호출부의 **인자**를 아무도 안 봤다(#20). 스파이로
    "호출부가 시장을 넘기는가"를 재야 발화한다.

### 실수 #250

250. **판정 글자를 계수 라벨로 쓰면 감사가 제 결과를 결함으로 센다**
    (2026-08-26 일일 감사 `❌ 5건` → 실제 결함 1건): 관심종목 요약이
    `❌ 불일치 0` 을 찍는데 sweep 은 **❌ 든 줄을 결함으로** 세므로, 불일치가
    0건인 정상 결과가 결함으로 보고됐다(#47 계수 패턴 오류의 **생산부**판).
    같은 실행에서 신선도 **요약이 지연 항목을 다시 나열**해 같은 결함이 두
    번 세어졌다(#45) — 2건이 4건이 됐다. 요약은 **세기만** 하고 이름은 위
    표가 댄다.
    ⚠️ **소비부에서 고치려다 진짜 결함을 삼켰다.** `❌ … 0` 을 '계수 라벨'로
    보고 거르는 정규식을 넣었더니 `❌ 카드 0개`(대조 0건 = 결함, #54)까지
    걸러졌다 — 기존 회귀가 잡았다. **0 은 문맥에 따라 정상이기도 결함이기도
    하므로 문자열로는 못 가른다**(#65·#60 문자열이 아니라 구조로). 해법은
    생산부 하나뿐이다: 판정 글자는 판정에만 쓴다.
    ⚠️ 그리고 그 fix 를 설명하는 **주석이 소스 검사를 만족**시켜 회귀가
    눈이 멀 뻔했다 — 소스 검사는 주석을 지우고 볼 것(#59b).

### 실수 #251

251. **한 원천을 두 경로가 다른 상한으로 부르면 한쪽만 잘린다**(2026-08-26
    ECOS 한국 수출·수입이 한 달 뒤짐): 스냅샷 경로만 `/1/100/` 이고 시계열
    경로는 `/1/1000/` 이었다(#38). 한 표가 부가 차원을 함께 주면 100행은
    쉽게 넘고, 정렬 후 `rows[-1]` 을 최신이라 부르므로 **잘리는 쪽이 최신
    월**이라 카드가 조용히 뒤처진다. 더 나쁜 건 원천이 `list_total_count` 로
    총 건수를 주는데 **주석에만 있고 읽지 않았다** — #173 에 적어 둔 그
    실수를 다른 클라이언트에서 그대로 반복했다. 상한을 맞추고, 총 건수와
    받은 행 수가 다르면 **꼬리 페이지를 다시 받고 로그로 알린다**(#82 —
    그래야 다음 감사가 '원천 지연'과 '우리가 잘랐음'을 가른다).
    ⚠️ 캐시는 범인이 아니었다(키에 날짜가 들어가 12시간 TTL). **고친 뒤
    다시 재기 전에는 원인 확정이 아니다**(#79·#107) — 샌드박스는 네트워크가
    막혀 실측을 못 했고, 다음 감사 로그가 답한다.

### 실수 #252

252. **반환값의 모양을 확인 안 하고 확인 명령을 건넸다 — 그리고 그 출력이
    그럴듯했다**(2026-08-26 블로그 추가 검증): `_fetch_rss` 는 파싱 결과가
    아니라 **원문 XML 문자열**을 돌려주는데 `r[0]` · `len(r[1])` 로 읽는
    명령을 드렸다. 사용자 VM 출력은 `채널: <  · 항목: 1` — **문자열의 첫
    글자와 두 번째 글자 길이**다. 오류가 아니라 그럴듯한 숫자가 나와서
    하마터면 '피드가 깨졌다'로 진단할 뻔했다(#86 대리 지표로 단정 금지의
    반환형판). 손으로 조립한 진단은 **제품 코드로 검증되지 않는다** —
    반복되는 확인은 제품에 심을 것(§Automation-first). `bot.blog_watch
    --check <blogId>` 가 레지스트리 등록·RSS 도달·채널명·항목 수·초기화
    상태를 갈래로 말한다(#82), 항목 0이면 원문 표본을 같이 찍는다(#54·#109).
    ⚠️ 그 CLI 회귀가 처음엔 눈이 멀었다 — AST 로 "`check` 호출이 있나 +
    `'--check'` 문자열이 있나" 를 쟀는데 **게이트만 꺼도 호출 노드는 남고**
    문자열은 사용법 안내가 대신 만족시켰다(#141·#75). `main` 을 실제로
    태워 디스패치되는지 봐야 발화한다.
    ⚠️ 그리고 그 출력이 실제로 증명한 것은 있다 — `_fetch_rss` 는 200 이고
    `<item` 이 있을 때만 문자열을 돌려주므로 **피드는 정상 도달**이었다.
    틀린 프로브도 무엇을 증명하는지는 반환 조건을 읽어야 안다.

### 실수 #253

253. **헤더는 엔티티로 풀고 목록은 원시 질의로 걸렀다 — 부분문자열이 남의
    회사를 실었다**(2026-08-26 밸류체인 "GS 에 GST 가 들어가있어"): 페이지가
    resolveKind 로 회사를 **정확히** 해석해 🏢 GS 헤더·공급사·수출품목 그룹을
    그려 놓고, 하단 목록만 `norm(e.c).indexOf(nq)` 부분문자열로 다시 걸러
    `norm('gst')` 가 'gs' 를 포함해 GST(무관 회사)의 관세청 행이 GS 페이지에
    실렸다 — #45(헤더와 목록이 다른 모집단)의 검색판. 해석된 엔티티가 있으면
    목록도 **그 엔티티로 정확히**(회사=양끝·품목=target·업종=e.g) 걸고, 해석
    실패 시에만 자유검색으로 떨어지되 라벨이 **'포함'** 이라고 밝힌다.
    ⚠️ 기존 밸류체인 JS 회귀는 전부 소스 문자열이었다 — 주석에 "클라 JS 라
    렌더 직접 실행 불가"라고 적혀 있었는데 **node 가 깔려 있었다**(#25 능력은
    이름이 아니라 실측 — 이번엔 우리 환경의 능력을 안 재 본 것). DOM 스텁
    하네스로 실제 렌더를 태워 값으로 검증하니 뮤테이션 4종이 전부 발화했다.
    생성물이 JS 면 파서만 태우지 말고(#26) **실행까지** 태울 수 있는지 먼저
    물을 것.

### 실수 #254

254. **헤더의 시총이 낡으면 그걸 분자로 쓰는 배수가 통째로 갈린다**
    (2026-08-27 NKE, 사용자 "왜 밸류에이션이랑 분기실적의 PSR 이 다른거야?
    …주가에 따라 바뀌는 지표는 돌리는 시점 주가로 모든 탭에서"): 헤더가
    현재가 $38.59 · 발행주식수 1,202,110,951 · 시총 **$57.25B** 를 나란히
    실었는데 곱은 $46.39B 다 — 시총만 **주가 $47.62 시절 야후 값**이었다
    (#33 나란히 놓인 칸은 산수가 맞아야 한다). 그 낡은 시총이 밸류에이션
    PSR 의 분자가 돼 1.23x, 카드는 `시총 = 현재가 × 주식수` 로 자체계산해
    1.00배 — **두 탭이 갈렸다**.
    ⚠️ 두 결함이 겹쳤다. 둘째는 **#241 의 'PSR 은 화면의 매출로 재계산'이
    KR 전용 배선**이었던 것 — 비-KR 은 `rev` 가 None 이라 소스값이 그대로
    살았다. #232 로 비-KR 도 분기 매출이 화면에 실리며 그 전제가 깨졌는데
    배선은 안 따라갔다(#248 과 **같은 형태**: 전제가 바뀐 규칙을 그 자리에서
    다시 묻지 않으면 한쪽 시장만 조용히 옛 동작으로 남는다).
    대응: (a) `price_consistent_mcap` — 시총이 현재가 × 발행주식수와 2%
    넘게 어긋나면 그 곱으로 재계산(tol 안쪽은 원천 존중 — 주식수 쪽이
    낡았을 수 있다, #190). 파생부와 **라이브 오버레이가 같은 헬퍼**를
    부른다(서버만 고치면 오버레이가 되돌린다, #199) (b) 비-KR TTM 매출은
    카드가 쓰는 **같은 시리즈**(`series_from_yfinance`)로 만든다 — 규칙을
    복제하면 또 갈린다(#38).
    ⚠️ 옛 회귀가 "어긋나면 **경고**를 띄운다"(#219)를 못박고 있었다. 이제는
    어긋나는 순간 **고치므로** 경고할 '어긋남'이 없다 — 지우지 말고 새 계약
    으로 다시 썼다(#222). 그리고 사유를 발행주식수 카드 **툴팁**에 두려다
    #228 을 재발시킬 뻔했다 — 바뀐 값은 시가총액이므로 **그 카드가 보이는
    줄로** 말한다(뮤테이션으로 툴팁 회귀가 잡히는 것 확인).

### 실수 #255

255. **오버레이 불변식이 '분석 이력 없는 종목'에서 조용히 no-op — 그리고 첫
    진단은 수치가 우연히 같은 다른 원인이었다**(2026-08-27 NKE 2차, 사용자
    "이거 맞는거야? …예전에 한번 돌려서 그게 Fix 된거라서 그런거야?"):
    헤더 시총이 $57.25B 인 채 각주만 "거래 클래스 기준 재계산" — 값이 자기
    각주를 뒤집었다. 오버레이의 `price_consistent_mcap` 이 주식수를 **분석
    아카이브에서만** 읽어(#35 화면과 다른 저장소) 검색으로만 본 종목은
    None → no-op → 야후 값이 서버의 재계산($46.39B)을 되덮었다. 폴백은
    같은 .info 응답의 sharesOutstanding(화면 발행주식수 카드와 같은 원천),
    단 KR 은 등록 주식수가 기준이라(#190) 폴백하지 않는다.
    ⚠️ #254 진단 정정: $57.25B 는 "주가 $47.62 시절 낡은 값"이 아니라 **전
    클래스(A+B) 모집단**이었다 — 47.62×1.202B(낡은가×상장주식수)와
    38.59×1.4835B(현재가×전체주식수)가 **같은 숫자**라 한 장으론 구별
    불가였고, 구글 캡처(같은 57.25B 가 현재가에 붙어 움직임)가 갈랐다.
    두 가설이 같은 수치를 내면 단정하지 말고 다른 표면의 관측으로 가를 것
    (#51). 처방은 어느 원인이든 같아(#224) 코드는 옳았고 틀린 서술이
    주석·독스트링 3곳에 복제돼 있었다(#55).
    ⚠️ 옛 회귀는 오버레이가 헬퍼를 **부르는지**(AST)만 봤다 — 빈 재료로
    부르면 no-op 인 걸 못 잡는다(#120 의 다음 단계: 정의→호출→**재료**).
    스텁 yfinance 로 `fmt["mcap"]` 값을 보는 동작 테스트로 보강했다.

### 실수 #256

256. **재계산으로 '고치기' 전에 그 원천의 다른 지표들이 어느 모집단 위에
    있는지 먼저 물을 것**(2026-08-27 "야후나 구글도 전 클래스로 보여주지?
    맞으면 미국도 전 클래스로 해줘" — #254·#255 가 하루 만에 뒤집힘):
    미국 시총을 현재가 × 상장주식수로 재계산하게 만들었는데, 야후·구글이
    보여주는 값도, 야후 trailingEps(2.05 = 전사 순이익 ÷ **전체** 주식수)로
    만든 PER 18.82 도 전부 **전 클래스** 기준이었다 — 내가 만든 상장 클래스
    시총(46.39B) ÷ 전사 순이익은 오히려 **모집단 혼합**이다(#33 의 모집단판:
    각 칸의 산수가 맞아도 급이 섞이면 표가 틀린다). 값을 갈아끼우는 대신
    **모집단이 다름만 밝히는 각주**(#43)로 충분했다 — 재계산은 결정이고,
    결정 전에는 표시가 먼저다.
    현행 기준: US 등 = 원천(전 클래스) 시총 + 현재가 배율 추종(같은 .info
    응답의 시총÷가격이 곧 모집단) · HK/CN_A 이중상장 = 거래 클래스(#224
    "H주로", #255 폴백 포함) · KR = 네이버 국내(#190). 시장 게이트 사유 =
    사용자 결정 2건이 시장별로 다름 + 이중상장은 타 거래소·타 통화 모집단.
    ⚠️ 어제 만든 테스트 6개를 지우지 않고 새 계약으로 다시 썼다(#222) —
    피어 행 픽스처에 ticker 가 없으면 시장 게이트가 못 돌므로 픽스처도
    실제 행 모양을 따라간다(#155 픽스처는 원천이 실제로 보내는 모양대로).

### 실수 #257

257. **"고쳤다"의 대상이 화면 경로인지부터 — #251 의 fix 는 카드에 한 글자도
    안 닿았다**(2026-08-27 한국 수출/수입 카드 "원천이 지연인지 우리문제인지"):
    #251 은 절단 검사를 단일값 경로(`_fetch_indicator`)에 넣었는데, 카드
    (스파크라인·기준월)는 같은 파일의 **형제 함수 `fetch_series_points`** 를
    쓴다 — #35 의 재발이고, 형제가 같은 파일 안이라 더 안 보였다. 시리즈
    경로에 총건수 대조 + **페이지 전체** 이어받기(꼬리만으론 스파크라인이
    구멍난다) + 캐시 파일명 버전(#21b — 같은 날 배포를 옛 캐시가 가린다)을
    넣었다. ⚠️ 그래도 '원천 지연 vs 우리 절단'은 실측만 답한다(#12,
    샌드박스는 키·네트워크 없음) — `bok_ecos_client --check` 가 화면 경로의
    최신 월과 원시 총건수·월별 행수를 나란히 찍어 가른다(#252 반복 확인은
    제품에 심는다). 일반화: 한 원천을 부르는 형제 함수가 여럿이면 fix·감사·
    프로브의 대상이 **화면이 부르는 그 형제**인지 grep 으로 확정하고 시작할 것.
    ✅ VM 실측(같은 날)이 판정을 끝냈다: 총 12행 · 1쪽 수신 12행 · TIME 당
    1행 — **절단도 부가 차원도 없고 원천(ECOS 901Y118)이 202607 을 아직 안
    실은 것**이다. 즉 #251 의 "행 상한이 최신 월을 잘랐다"는 가설은 반증됐다
    — 창 13개월 × 1행 = 13행이라 옛 100행 상한도 애초에 안 걸렸다. 재지
    않고 상한을 범인으로 지목했던 것(#12 — #251 스스로 "고친 뒤 다시 재기
    전에는 원인 확정이 아니다"라고 적어 두었다). 절단 검사·페이지네이션은
    방어로 유지, 원천이 7월분을 올리면 12h 캐시 안에서 자동 반영된다.

### 실수 #258

258. **매 실행 username 해석 + 크래시 재시작 루프 = 요청 제한 증폭기, 그리고
    알림이 엉뚱한 처방을 말했다**(2026-08-27 나쁜양파 "FloodWaitError: A wait
    of 8073 seconds … (caused by ResolveUsernameRequest)" 인데 알림은 "세션
    만료 — 재인증 확인"): `client.get_entity("<username>")` 은 매 실행
    ResolveUsernameRequest 를 쏘고 텔레그램은 그걸 계정 단위로 강하게
    제한한다. 백필 타이머 2종 + 리스너 2종 + 진단이 같은 계정으로 반복 해석
    했고, 리스너는 그 예외를 안 잡아 **systemd 재시작마다 또 해석**하는
    증폭 루프가 됐다. 대응 셋(`trade/tg_entities.py` 단일 헬퍼, #38):
    (a) `resolve_peer` = 세션 DB 캐시 우선(`get_input_entity`, username 포함)
        — 캐시 미스 첫 1회만 네트워크 해석, 이후 실행은 해석 요청 0.
    (b) 리스너 main 에 FloodWaitError 분기 — **대기를 소화한 뒤** 종료(상한
        1h). 곧장 exit 하면 즉시 재시작이 한도를 더 태운다.
    (c) 알림 사유는 `startup_failure_note` 가 갈래로 말한다(#82) — FloodWait
        ="재인증 불필요, 자동 재시도" / AuthKey·SessionRevoked="재인증 필요"
        / ChannelPrivate="채널 상태 확인". FloodWait 에 '재인증'이라고 적으면
        운영자가 멀쩡한 세션을 지우러 간다(#187b 틀린 로그가 헛걸음을 만든다).
    일반화: 장수 프로세스의 시작 경로에서 나가는 **해석성 요청**(이름→ID)은
    캐시 가능한지 먼저 묻고, 크래시-재시작 루프가 그 요청의 배수기가 되지
    않는지 볼 것. 회귀는 디렉터리 전수(#24)로 옛 패턴 0건을 고정.

### 실수 #259

259. **분할이 여러 번이면 기저 경계도 여러 개다 — 단일 경계 탐색은 통째로
    되돌리고, '아는 날엔 안 자른다'가 그 실패를 화면에 실었다**(2026-08-27
    NVDA "분할이 제대로 안먹힌것 같은데…우리 이거 이미 몇번 잡지 않았어?" +
    "중간에 없는 기간이 엄청 많네"): NVDA 는 2021 4:1 + 2024 10:1 이라
    EDGAR EPS 계열에 기저가 **셋**(×40·×10·×1) — 원천이 사건마다 소급조정
    범위가 다르다(#177 의 다중판). 단일 경계 탐색(#177)은 하나만 고칠 수
    있어 개선 폭 미달로 **전부 되돌려졌고**, 되돌림 뒤 잘라내기는 "분할을
    아는 날엔 안 자른다"(#166c)라 raw as-reported EPS 가 분할반영 주가 옆에
    그대로 실렸다 — 옛 PER 0.5~6(정답의 1/40)이 5년치, 값이 다 '있어서'
    아무 감사도 안 걸렸다(#96). 대응 셋:
    (a) 사건별 정합(`reconcile_eps_splits`) — 최신 사건부터 하나씩 자기
        경계를 찾고 곱이 자동 누적된다. 수락 규칙은 기존 그대로(#162
        효과로 판정).
    (b) 정합이 되돌려졌으면(reverted) **아는 날에도 자른다**(`should_trim`,
        #222 로 옛 계약 다시 씀) — 남은 급변은 실적이 아니라 미정합 기준이다.
    (c) 결산분기 재복원(`refill_fiscal_q4`) — edgar 쪽 복원은 as-reported
        단계라 분할 낀 해에 형제 가드(#167)로 폐기돼 그 해 TTM 4점이 빈다
        (#168 서명 — NVDA 는 두 번이라 2021·2023~24 공백). **기저를 맞춘
        뒤** 빠진 결산분기만 다시 채운다(형제 가드 재사용, #38).
    일반화: 가드가 실패를 '되돌림'으로 처리하면 그 실패 상태가 화면에
    그대로 가는지 물을 것 — 되돌림은 안전이 아니라 **미정합 상태의 채택**일
    수 있다. 그리고 같은 회사에 사건이 N번이면 처리기도 N번 돌아야 한다.
    ⚠️ 2차(같은 날 사용자 "그대로임."): 사건별 정합(a)도 NVDA 를 못 고쳤다
    — 측정 기반 수락(#162)은 **성장이 기저 차이를 정확히 상쇄하면 눈이
    먼다**. 2022년 ×10 기저 EPS(~0.59/분기)와 2024년 실제 EPS(~0.60)가
    같은 크기(그 사이 이익 10배 성장)라 인접 급변이 1.0배 → "고칠 게
    없다(noop)". #255(두 가설이 같은 수치)의 시계열판이다. 답은 측정이
    아니라 **원천의 사실**: EDGAR 팩트의 `filed`(제출일) — 분할 '전'에
    제출된 보고서는 그 분할을 알 수 없으므로 확실히 미조정이고, '후'
    제출분은 소급조정 의무(ASC 260)다. `adjust_eps_by_filed` 가 행별로
    "제출일 이후에 일어난 분할의 곱"으로 확정 환산하고, 측정 정합은
    filed 가 없는 경로의 안전망으로 남았다. 복원한 결산분기의 기저는
    그 연간 보고서의 제출일이다. 일반화: 측정으로 판정이 안 서는 자리에
    원천이 **시점 사실**(제출일·접수일)을 주고 있는지 먼저 뒤질 것 —
    버리고 있던 필드(filed)가 열쇠였다(#150 '우리가 그걸 다 쓰고 있나').
    ⚠️ 3차(--explain VM 프로브가 확정): filed 환산 **뒤에도** 측정 정합이
    FY2024 실적 점프(연 ~7배, 실제 AI 붐)를 '미정합(reverted)'으로 읽었고,
    should_trim(reverted) 가 연간 계열을 잘라 2021·2023 연간이 사라지자
    결산분기 복원 재료가 없어져 TTM 4행씩 공백이 남았다(프로브는 trim 을
    안 태워 복원 ✅ — 그 차이가 기제를 드러냈다). 대응: **기저가 사실로
    확정된 계열(`basis_established_by_filed`)에는 급변 휴리스틱(측정 정합·
    잘라내기)을 아예 태우지 않는다** — 남은 급변은 정의상 실적이다. 독립
    검증은 결산검산(TTM=연간, 같은 기간 대조라 성장 무관)이 맡는다.
    일반화: 사실 기반 판정이 이미 선 자리에 휴리스틱 안전망을 겹치면,
    안전망이 **정상 데이터를 파괴하는 쪽**으로 오발한다(#146 의 재발 —
    증상(급변)으로 막으면 그 증상을 정당하게 갖는 실적이 같이 죽는다).

### 실수 #261

261. **원천이 같은 보드의 레이아웃을 바꾼다 — 형제 중 하나만 옛 형식에 묶여
    있었다**(2026-08-28 나쁜양파 일본 종목별 7월분 미전달): 헤더가
    `이름 (티커) 일본 수출 Update` **1줄** → `이름 (티커)` / `일본 수출` /
    `26년 7월 Update` **3줄**로 바뀌었는데 일본 파서만 1줄 고정이라 통째로
    드랍됐다. 이 파서가 곧 리스너의 **관련성 필터**라 저장도 미매칭 알림도
    없는 조용한 유실이다(#83 의 다섯 번째 변주).
    ⚠️ 형제 대조가 원인을 확정했다 — 말레이시아·중국은 처음부터 **둘 다**
    받고, 한국은 반대로 3줄만 받는 **거울상 구멍**이 있었다(전수 회귀가
    잡았다). 어순·마커를 나라마다 따로 적으면 반드시 갈라진다(#27·#38).
    같은 템플릿 변경이 두 가지를 더 망가뜨렸는데 **사용자는 셋 다 못 본다**:
    (a) 새 지표(`동시상관`·`방향 일치율`)가 일본 **품목 슬롯**을 차지해
        카드에 `동시상관: 0.96` 이 품목으로 실렸다(형제는 이미 걸러냈다).
    (b) 각주 마커가 `*` → `-` 로 바뀌어 **네 보드(jps·mys·cns·cni)가 동시에**
        코멘트를 잃었다 — "합산하지 마라" 같은 해석 필수 경고가 사라진다.
    처방: 형식 계약을 **레지스트리 전수 회귀**로 못박는다(이름 열거는 다음
    나라를 못 잡는다, #24) — 종목 기준 소스는 ① 1줄·3줄 둘 다 수용 ② 지표
    줄이 품목 슬롯에 안 들어감 ③ 각주 마커 `*`·`-` 둘 다(공백 필수 — 강조
    표기 오탐 차단은 유지). 일반화: 외부 템플릿을 파싱하는 형제 소스가 여럿
    이면 "이 변주를 나머지도 받나"를 **재서** 답할 것.
    ⚠️ **관련성 필터가 버그면 어느 층도 자가회복하지 않는다**(같은 날 VM
    로그로 확정): 리스너와 6시간 백필이 **같은 파서**를 필터로 쓴다 —
    실측 `relevance filter: 0/49 units` (fix 전) → `8/33 units` (fix 후).
    나는 "백필은 필터 없이 전달하니 6시간 안에 알아서 회수된다"고 안내했는데
    **틀렸다**(코드의 후보 루프만 보고 그 뒤 필터 단계를 안 봤다, #12).
    그래서 파서를 고친 뒤엔 **백필을 손으로 한 번 돌려야** 회수된다 —
    그리고 백필 기본 창이 3일이라 **회수 시한이 3일**이다(그 밖은
    `--since` 명시). 파서 fix 를 배포할 때 회수까지가 한 세트다.

### 실수 #262

262. **같은 지표를 나라마다 따로 적으면 수용 범위가 갈린다 — 오늘 사고의
    뿌리였다**(2026-08-29 종목판 상관 지표 전 보드 통일): 원천은 종목판에
    `동시상관`·`방향 일치율`(+선행 쌍)을 싣는데 실측 수용 범위가 제각각
    이었다 — mys·cns·cni 는 4개 전부, **krs 는 선행 쌍만, jps 는 아예
    없음**. 같은 정규식이 두 모듈에 복제돼 있던 게 원인이고(#38·#84
    "복제 대신 엔진을 나눠라"), 전날 일본 7월분 유실(#261)도 같은 뿌리다.
    `trade/badonion_metrics.py` 한 벌로 모으고 네 보드가 그걸 쓴다.
    ⚠️ 지표를 늘릴 땐 **파싱·컬럼·화면 셋이 한 세트**다 — 파싱만 하고
    컬럼이 없으면 적재가 터지고(`CREATE TABLE IF NOT EXISTS` 는 기존
    테이블을 안 건드리므로 **마이그레이션 필수**), 저장만 하고 화면이
    없으면 아무도 못 본다. 회귀가 셋을 다 태운다(옛 DB 를 만들어
    마이그레이션까지 실행).
    ⚠️ 카드 라벨: 한국은 **선행** 일치율을 '방향 일치율' 로만 적고 있어
    동시 지표가 붙는 순간 구별 불가였다(#34 같은 주제어면 라벨에 기준을
    박을 것). 붙이면서 함께 고쳤다.
    ⚠️ 복제 금지 가드는 판정축이 **둘**이어야 했다 — 문자열로 `상관` 만
    찾으면 건너뛰기 목록(`_SKIP_LINE`)이 걸리고, 숫자 추출만 보면 일반
    `라벨: 숫자` 패턴(`parser._COMPANY_TITLE_RE`)이 걸린다(둘 다 실측
    오탐). "패턴이 `상관` 을 말하고 + 값을 뽑아낸다" 로 합쳐야 정확하다.
    ⚠️ 그리고 **새 공용 모듈은 untracked 로 남기 쉽다** — 독립 리뷰가
    그걸 잡았다(빠지면 네 보드가 전부 import 에서 죽는다). 모듈을 새로
    만든 커밋은 `git status` 로 추적 여부를 확인할 것.

### 실수 #263

263. **원천이 접두를 안 붙이면 '추측 금지' 가드가 그 칸을 영원히 비운다**
    (2026-08-29 ROHM 6963 · Tokyo Electron 8035 상관 반쪽): 어제 회수한
    7월 8건에 상관을 채우자 두 종목만 `dir_hit=100.0` 인데 `corr=None`
    이었다. 원문은 `상관: 0.98` / `방향 일치율: 100%` — **접두가 없다**.
    공용 파서가 비대칭이었다: 일치율은 맨 것을 받고 상관은 접두 없으면
    버렸다(#262 에서 옮겨 온 옛 규칙 "어느 계열인지 모르니 받지 않는다").
    버리기와 지어내기 사이에 답이 있다 — **값은 싣고 계열은 '미표기'라고
    말한다**(`corr_basis`, #171 가드가 '못 만든다'로 끝나면 그 자리가
    영원히 비는지 먼저 물을 것 · #43 침묵이 최악 · #165 재지 않은 귀속을
    단정하지 말 것).
    ⚠️ 접두 없는 **일치율**의 계열은 추측이 아니라 **같은 캡션의 상관이
    정한다** — 동시/미표기 상관이 있으면 그 짝, 선행 상관만 있으면 선행
    짝이다(원문 구조를 읽는 것이지 짐작이 아니다).
    ⚠️ 옛 계약을 못박아 둔 테스트 2개(`test_bare_correlation_is_not_guessed`
    — my·cn 형제)가 **정확히 오늘 뒤집힌 전제** 위에 있었다. 지우지 말고
    새 계약으로 다시 썼다(#222) — 남는 보장(선행 칸 오염 금지 · 동시 단정
    금지)은 그대로 두고, 무엇이 왜 바뀌었는지 docstring 에 실측(ROHM·TEL)
    으로 남긴다. 그 둘이 **형제라 한 곳만 고치면 다른 하나가 빨간불**이다
    (실제로 root 를 green 으로 만든 뒤 trade 에서 나머지가 터졌다 — 규칙을
    바꿀 땐 `grep -rn` 으로 형제 계약부터 셀 것).
    ⚠️ 픽스처가 두 계열을 **다 갖고 있으면** '선행 칸 오염' 단언이 오히려
    틀린다(cn `_TTM` 은 선행 0.85 도 온다 — 그건 오염이 아니라 제 값이다).
    단언은 "미표기 값이 그 칸을 **덮었나**"를 집어야 한다(#75).

### 실수 #264

264. **화면이 '미파싱'이라고만 말해서 원인을 셋 중 무엇인지 못 갈랐다**
    (2026-08-30 DART 카드 3건 — 주권매매거래정지기간변경·최대주주변경·
    불성실공시법인지정): 캡처의 표 구조를 재구성해 파서에 태우니(#118)
    **셋 다 실제 결함이 나왔다** — ① 기간변경 양식엔 `_SUSPENSION_FIELDS`
    가 아는 라벨(정지사유·정지일시·해제일시)이 **하나도 없어** 0필드였고
    generic 폴백이 `주식병합: 관련 - 신주권…` 처럼 번호 항목을 라벨로 오독
    했다(#55) ② 라벨이 `최대주주**등**` 이라 이름이 `등 최영권` 으로 나왔고,
    사유는 다음 줄이 `-실권주…` 라 stop 앞의 `\s*` 가 하이픈을 못 넘어 통째로
    안 잡혔다(1줄만 남아 미파싱) ③ 코스닥 지정 표는 라벨이 맨 `유형`·`지정일`
    이고 누계가 `최근 1년간 … 부과벌점` 이라 벌점 하나만 건졌고, `내용` 의
    stop 이 맨 `지정` 이라 `내부결산시점 관리종목` 에서 잘려 **문장이 뜻을
    잃었다**. 원천이 한 벌 서식을 쓴다는 가정이 틀린 #73 의 공시판이다.
    ⚠️ 그런데 **화면이 미파싱인 이유는 아직 실측이 아니다** — 재구성본은
    수정 전에도 generic 폴백으로 줄이 나왔으므로, 카드가 빈 원인이 파서 갭인지
    원문 미수신인지 12h 쿨다운인지 콜 예산인지 이 출력만으론 못 가른다(#12).
    갈래마다 처방이 달라 `dart_feed --why <접수번호|회사명>` 을 제품에 심었다
    (#82·#252 반복 확인은 제품에 심는다) — 인터프리터·자격증명 출처·예산·
    쿨다운·원문 길이·전용/generic 결과를 갈래로 찍고, 빈손이면 **원문 표본**
    을 같이 찍는다(#109). 대조 0건은 ✅ 가 아니라 ❌(#54).
    ⚠️ 진단이 운영 상태를 바꾸면 안 된다 — 파서가 빈손이면 `_doc_fail_mark`
    가 **12h 쿨다운을 새로 심어** 다음 실수집을 막는다. 프로브 구간에서만
    무력화했다(#30 의 프로브판, 뮤테이션으로 확인).
    ⚠️ 뮤테이션 하나가 픽스처 때문에 통과했다 — '변경 **후**만 싣는다' 를
    실측 문서로 재려 했는데 그 문서는 변경전 조건이 변경후에도 그대로 들어
    있어(신주권 변경상장일 전일까지) 두 블록을 같이 읽어도 결과가 같았다.
    **두 블록이 실제로 다른** 픽스처를 따로 둬야 발화한다(#91c).
    ⚠️ 독립 리뷰가 넷을 더 잡았다(셀프리뷰 사각): (a) 누계벌점 패턴이
    지정예고 양식의 **한 행짜리** '최근 1년간 … 부과벌점' 을 벌점과 **둘 다**
    집어 같은 값이 두 줄이 됐다(하나는 라벨이 틀리다) — 당해 부과벌점 행이
    앞에 있을 때만 누계다 (b) 프로브의 쿨다운 무력화를 fetch **뒤**에 걸어
    `_fetch_doc_text` 가 심는 마크를 못 막았다 (c) 쿨다운으로 조회를 건너뛴
    것을 '원문 미수신(원천 문제)'으로 오보했다 — 처방이 정반대다 (d) 종료
    앵커를 정규식 창(400자) 안에 두어 변경후 블록이 길면 **매치가 통째로
    실패**해 정지기간만 조용히 빠졌다(2줄은 남아 미파싱으로도 안 잡힌다).
    ⚠️ 그 프로브가 **첫 실행에서 눈이 멀었다**(VM 실측): `❌ 아카이브 3일
    에서 '에스아이리소스' 를 못 찾았다` 한 줄뿐이라, 윈도가 좁은 건지 ·
    이름 표기가 다른 건지 · 아카이브 자체가 빈 건지 사용자가 명령을 다시
    조합해야 했다 — 내가 방금 적은 '갈래로 말하라'(#82)를 **0건 경로에는
    적용하지 않은 것**이다. 대응: 요청 윈도에 없으면 스스로 30일로 넓혀
    보고 **넓혔다고 밝히고**(Automation-first — 윈도를 사용자가 고르게 하지
    말 것), 그래도 0건이면 조회 건수·아카이브 날짜범위와 함께 갈래를 찍는다
    (빈 아카이브=수집 문제 / 이름 후보 제시 / 후보도 없으면 드롭 의심).
    ⚠️ 그리고 그 프로브가 **정렬 없이 5건만** 찍고 자른 사실도 말하지
    않았다(VM 2차 실측): 에스아이리소스 히트가 08-03~08-11 로 나와, 정작
    사용자가 물은 **08-28 건이 잘려** '그 공시는 아카이브에 없다'는 틀린
    인상을 줬다 — 상한에 정렬이 없으면 하필 최신이 버려지고, 총계와 표시분이
    다른데 말을 안 하면 사용자가 그걸 전체로 읽는다(#45). 최신순 정렬 +
    '매치 N건 — 최신 5건만 표시' 고지.
    ⚠️ 곁다리(같은 실행의 full suite 가 잡았다): `test_mark_stale_quarterly_
    freq_doubles_threshold` 가 나이를 **일수**(182일)로 만들어, 오늘이 며칠
    이냐에 따라 5개월도 6개월도 됐다 — 2026-08-30 에 red. 계약이 **개월**이면
    픽스처도 개월로 되짚을 것(#249 날짜 의존 테스트의 재발).

### 실수 #265

265. **'미파싱'의 진짜 원인은 파서가 아니라 원천이 원문을 안 주는 것이었다**
    (2026-08-30 VM 실측, #264 의 결말): `--why` 가 답을 줬다 —
    에스아이리소스 08-28 3건이 전부 `status=014 파일이 존재하지 않습니다`.
    거래소 소관 공시는 DART **뷰어**엔 본문이 멀쩡히 있는데 open API
    `document.xml` 만 없다. 즉 #264 에서 고친 파서 3종은 옳지만 그것만으로는
    이 카드가 안 채워진다 — **커버리지가 낮으면 파서를 더 짜기 전에 원문이
    도달하는지부터 물을 것**(#111 의 재발이자, 진단을 심어서 한 라운드에
    끝낸 사례). 대응 셋: (a) 014 를 **갈래로 기록**(`source_has_no_document`)
    — 네트워크 실패와 달리 영구적이라 파서 갭으로 세면 '개선 여지' 숫자가
    통째로 틀린다(#93) (b) 뷰어를 폴백으로 읽는다(카드가 이미 링크로 걸고
    있는 그 공개 페이지) (c) 그래도 없으면 카드가 **사유를 말한다**(#43).
    ⚠️ 독립 리뷰가 여섯을 잡았고 그중 둘은 치명적이었다: (i) 실제 뷰어
    페이지는 `function viewDoc(rcpNo, dcmNo, …)` **정의**를 담는데 인자가
    '비었나'로만 걸러 **파라미터 이름이 질의값**이 될 뻔했다 — 내 픽스처가
    `viewDoc(){}` 라 그 상태로도 green 이었다(#155 픽스처는 원천이 실제로
    보내는 모양대로). 접수번호가 숫자인 것으로 갈랐다. (ii) 사유 줄을
    `detail` 에 저장하면 멱등 가드가 그걸 **영원히 재생**해 나중에 DART 가
    파일을 올려도 카드가 안 바뀐다(#18) — 쿨다운 중에만 재사용하고 끝나면
    다시 묻는다. 나머지 넷: 뷰어 성공 후에도 '원천 미제공' 표시가 남음 ·
    마크업 요청에 평문을 줘 `<TABLE>` 을 못 찾게 함 · 재지 않은 잘림 여부를
    '안 잘림'으로 단정 · 계수에서 014 가 성공도 실패도 아닌데 어디에도 안
    잡힘.
    ⚠️ 그리고 내 변경이 **기존 silent-fail 가드를 red 로 만들었다** —
    '마킹 **바로 앞** 형제가 사유 로그' 라는 규칙이라, 로그와 마킹 사이에
    분기 하나가 끼자 멀쩡한 로그를 조용한 실패로 오보했다(#60 이 창을
    구조로 바꾼 그 검사의 다음 단계). 계약은 '이 마킹에 사유가 있는가'
    이지 '몇 번째 문장인가'가 아니다 — 같은 블록에서 그 마킹 **앞에**
    사유 로그가 있었는지로 바꾸고(마킹마다 소비), 로그를 통째로 지우는
    뮤테이션이 여전히 발화하는 것을 확인했다.

### 실수 #266

266. **채우는 fix 가 그다음 층 셋을 한꺼번에 드러냈다**(2026-08-30 뷰어 폴백
    성공 직후, #265 의 다음 층): 폴백은 통했다(1,307·2,249·2,503자 수신,
    전용 파서 3줄·7줄). 그런데 같은 출력이 셋을 더 보여 줬다 —
    (a) 뷰어 본문은 **스타일시트를 통째로 안고 온다**(`.xforms * {
        font-family: 돋움체;}`). 태그만 지우면 CSS **본문**이 남아 파서가
        그걸 문서로 본다 — 기타시장안내가 1,307자를 받고도 0줄이었다.
        `<style>`·`<script>` 는 태그 제거 **전에 블록째** 걷어낼 것.
    (b) `is_parse_target` 이 report_nm 의 **괄호 부기**까지 훑어
        `주권매매거래정지기간변경   (상장적격성 실질심사 대상(사유발생))`
        이 '상장적격성' 제목완결 예외에 걸렸다 — **원문이 와도 그 카드는
        영원히 빈칸**이다(enrich 루프가 건너뛴다). 예외의 뜻은 '이 제목이면
        본문이 필요 없다' 이지 '사유에 그 말이 있으면' 이 아니다. DART
        report_nm 은 `제목      (부기)` 형태이므로 **제목 부분으로만** 판정.
    (c) 파서를 고쳐 배포해도 **12h 쿨다운이 남아** 반나절 더 빈칸이다.
        손으로 비우는 규율은 이 레포에서 다섯 번 졌다(#18·#21b·#95·#124·
        #198) → 쿨다운 마크에 **파서 지문**을 실어 배포가 자동 무효화한다
        (#119 구조로 옮기기).
    ⚠️ 독립 리뷰가 그 지문의 부작용을 잡았다: **파일 전체 해시는 주석 한
    줄에도 전 쿨다운을 한꺼번에 푼다** — 이 파일은 실수 기록 주석이 많아
    실제로 자주 그렇게 된다. 독스트링·주석을 걷어낸 뒤 재도록(AST unparse)
    바꿨다. 그리고 `is_parse_target` 만 제목 규칙으로 바꾸면 `_tally_drop`·
    `coverage_audit` 은 여전히 부기를 훑어 **개선 후보가 '의도된 제외'로
    사라진다**(#35·#105 감사와 화면이 갈라진다) — 같은 헬퍼를 쓰게 하고
    회귀는 AST 로 "세 곳이 다 `_report_title` 을 부르는가"로 고정했다.

### 실수 #267

267. **파서를 고쳐도 옛 카드는 안 바뀐다 — 회수 경로가 marker 1회로 고정돼
    있었다**(2026-08-31 대시보드 미파싱 6건): `run_once` 는 최근 3일만 다시
    fetch 하므로 08-05·08-13·08-14 정지 공시는 `enrich_disclosures` 가 아예
    안 본다. 유일한 회수 경로인 미파싱 재추출 백필이 **marker 파일 존재
    여부**(수동 `_v2` bump)로 갈려 배포해도 안 돌았다 — #18·#21b·#95·#124·
    #198·#266c 에 이은 같은 실패다. 지문으로 바꿔 배포가 곧 재실행이 되게
    했다(#119). ⚠️ 독립 리뷰: **예산으로 중단된 실행이 지문을 남기면** 남은
    날짜가 그 배포에서 영영 안 돈다 — 중단이면 기록하지 않는다.
    ⚠️ 같이 잡은 것: 정지기간 종료조건을 `'…까지'` **어구**로 뽑아 문장이
    잘렸다(스코넥 실측: charset 이 괄호를 안 받아 `개선기간 종료(차기 …` 가
    `차기 …10일) 후` 로 시작하고, 번호가 앞을 끊어 `상장적격성` 이 `적격성`
    이 됐다 — 원본에 없는 자리에서 낱말이 갈렸다, #94). 원문이 번호로 가른
    **항목 단위**로 통째로 싣는다. 그리고 시작 날짜는 블록 **머리**에서만
    찾을 것 — 아무 데서나 찾으면 조건문 안의 날짜가 시작이 되고 그 앞이
    통째로 잘린다(독립 리뷰가 재현). 종료 앵커를 못 찾으면 잘린 조각을
    싣지 말고 **사유를 말한다**(#43).
    ⚠️ 이어서 VM 이 **또 다른 갈래**를 보여 줬다(노바렉스 20260827000811):
    `status=013 접수번호 오류`. 같은 날 001049 가 정상 수신되므로 정정본으로
    대체된 문서로 **보이지만 그건 추정**이라 화면 문구에 단정하지 않았다
    (#165) — 014 만 실측상 영구이므로 013 은 쿨다운도 짧게(2h) 잡아 다시
    묻는다. '원천이 원문을 안 준다'는 갈래가 하나가 아니다.
    ⚠️ 독립 리뷰가 셋 더 잡았다: (a) 정상 원문을 받아도 미제공 표시를
    **뷰어 폴백 분기에서만** 지워, 그 뒤의 진짜 파서 갭이 '원천 미제공'으로
    오라벨되고 계수도 갈렸다 (b) 013 을 영구로 다뤄 2h → 12h 억제 + 단정
    문구 (c) 첫 바이트 가드가 **BOM·선행 공백**을 못 넘어 014 를 놓치면
    뷰어 폴백이 통째로 안 돈다.
    ⚠️ 뮤테이션 자체가 **엉뚱한 곳을 쳤다** — 앵커를 `    if stop:` 로 주니
    들여쓰기가 깊은 `        if stop:` 의 부분문자열로 먼저 매치돼 다른 분기를
    껐고, 그래서 '통과'로 보였다. 뮤테이션이 통과하면 재는 대상뿐 아니라
    **뮤테이션이 실제로 그 자리를 쳤는지**도 볼 것(#91b 의 도구판).
    ⚠️ 그리고 이어붙이기로 테스트를 추가해 **또 엉뚱한 클래스에 들어갔다**
    (#68, 오늘 두 번째).

### 실수 #268

268. **감사가 제 출력을 결함으로 세고, 요약이 같은 결함을 다시 센다 — #250 을
    한 도구에만 적용했다**(2026-08-31 일일 감사 `❌ 19건`): 19건 중 14건이
    감사 도구의 오보였다. (a) `per_band_audit` 의 `총 ❌ 0건 — …` 이 결함으로
    세어졌다(판정 글자를 **계수 라벨**로 씀 — #250 그대로) (b)
    `liquidity_audit` 요약이 지연 7건을 **다시 나열**해 14건이 됐다(#45 총계와
    소계가 다른 모집단) (c) `fcf_audit` 결함 줄에 **종목이 없어** sweep 이 바로
    위 줄을 섹션으로 집었다(`[③ 두 화면이…] ① 재계산 ❌`) — 어느 종목인지
    모르면 고칠 수가 없다(#114).
    #250 은 "판정 글자는 판정에만 · 요약은 세기만" 을 `board_audit` 에만
    적용했다 — **한 화면에서 버그를 고치면 같은 계산을 하는 다른 화면을 즉시
    grep**(#38·#147). 재발 방지는 소비부가 아니라 생산부이고(0 은 문맥에 따라
    정상이기도 결함이기도 해 문자열로는 못 가른다), 가드는 `bot/scripts/
    *audit*.py` **디렉터리 전수**로 건다(#24).
    ⚠️ 그 가드가 켜자마자 **자기 설명을 물었다** — 규칙을 적은 독스트링에
    `총 ❌ 0건` 이 들어 있었다(#59b 소스 검사는 독스트링을 지우고 볼 것).
    ⚠️ 그리고 헬퍼(`mark_finding`)만 재는 테스트는 **배선을 떼는 변형을 못
    잡았다**(실측) — `say()` 가 그걸 부르는지 AST 로 같이 못박아야 발화한다(#20).

### 실수 #269

269. **관측 주기와 공표 주기는 다르다 — 일별 관측을 일별 공표로 규약했다**
    (2026-08-31 FRED 환율 7종): 감사가 매일 `❌ 지연(기대 08-24)` 을 냈는데
    실측은 `observation_end=2026-08-21 · last_updated=2026-08-24 15:16` —
    **원천이 정상**이었다. FRED H.10 은 월요일 16:15 ET 에 전주 금요일까지를
    한꺼번에 내므로 그 사이 6일은 최신 관측이 그대로다. 규약이 `("D", 3)` 이라
    릴리스 다음날부터 다음 릴리스까지 계속 오탐이었고, 고칠 수 없는 ❌ 가
    매일 오면 진짜 ❌ 를 가린다(#260). 시리즈를 등록할 때 **관측이 얼마나
    자주 찍히나**가 아니라 **원천이 얼마나 자주 내나**로 규약할 것.
    ⚠️ 감사가 답을 이미 갖고 있었다 — `↪ 원천 observation_end=… →
    **원천이 여기까지밖에 없다**(규약을 늘린다)`. 판정 옆에 원천 메타를 같이
    찍어 둔 덕에 '우리 수집 실패'와 한 번에 갈렸다(#143 대조군).
    ⚠️ lag 는 추측하지 말고 **주기에서 도출**할 것: 정상 시점(월요일 아침 =
    직전 주 금요일)이 통과하고 한 릴리스 결손이 잡히는 값을 회귀로 양쪽
    고정했다(5 + GRACE 4). 완화는 다른 축의 증거를 같이 요구하는 것이지
    문턱을 그냥 내리는 게 아니다(#57).
    ⚠️ 그리고 형제 7종을 하나씩 적으면 한쪽만 고쳐져 갈라진다(#27·#31) —
    `H10_SERIES` **릴리스 그룹**에서 파생시키고, 회귀가 "DEX* 는 전부 주간"
    을 강제한다(이름 열거는 다음 시리즈를 못 잡는다, #24).

### 실수 #270

270. **분류를 화면에서 파생시키면 백필이 공짜다 — 단, 파생 시점이 다른
    판정보다 앞서면 그 판정이 죽는다**(2026-08-31 상장폐지 칩 분리):
    사용자가 "상장폐지 관련을 리스크에서 따로 빼서 리스크 앞에 · 이전것까지
    모두 백필" 을 요청했다. `significance()` 가 렌더타임 순수 판정이라
    수집기(`_classify_report`)를 손대지 않고 **화면에서 파생**하면 옛 아카이브가
    자동으로 따라온다(#32 '렌더에서 내리면 재수집 없이 정리된다' 의 반대 방향
    — 재분류 백필 패스가 아예 불필요했다).
    ⚠️ 독립 리뷰가 셋을 잡았고 하나는 치명적이었다: (a) 카테고리 교체를
    **미파싱 계산보다 먼저** 두어 `is_parse_target` 이 새 이름을 몰라(=
    `_PARSE_CATS` 밖) ⚠️ 배지가 통째로 사라졌다 — 하필 #264~#267 의 그 정지
    공시들이고, 감사는 저장된 '리스크' 로 계속 세니 화면과 갈린다(#35).
    파생은 **다른 판정이 끝난 뒤**에 할 것. (b) `significance()` 는 [기재정정]을
    통째로 제외하는데 그걸 카테고리에도 쓰면 **같은 사건의 원공시와 정정이 두
    칩으로 쪼개진다** — 정정 제외는 🔥 배지 규칙이지 분류 규칙이 아니다.
    판정을 순수 술어(`is_delisting_related`)로 빼서 둘이 공유하게 했다(#38).
    (c) 빈 카테고리를 `""` 로 돌려줘 `data-cat=""` 빈 칩이 생기고 클릭하면
    필터가 전체로 리셋됐다 — 종전 폴백('기타')을 지킬 것.
    ⚠️ 그 술어를 빼고 보니 **'상장폐지시까지'(기간 boilerplate) 가드에 회귀가
    없었다**(뮤테이션으로 확인 — 지워도 전 스위트 green). 🔥 배지에만 걸려
    있을 땐 넘어갔지만 이제 칩 분류까지 좌우하므로 못박았다. 코드를 옮길 땐
    **옮기는 그 로직이 테스트로 덮여 있는지** 확인할 것.
    ⚠️ 같은 날 2차로 범위가 넓어졌다("상장폐지를 언급하는 소송 조회공시도
    새로 만든 곳으로") — 1차 문구가 '리스크에서 빼서' 라 리스크만 옮겼는데
    사용자가 그 경계를 옮긴 것이다. 옮겨 오는 칩을 `DELIST_FROM` **상수
    하나**가 정하게 해 두면 이런 변경이 한 줄이다(조건문에 흩어 적으면 다음
    변경에서 한쪽만 고쳐진다, #38). 옛 계약("리스크에서만")은 지우지 말고
    다시 쓴다(#222).
    ⚠️ 그리고 칩 순서 계약이 전체 목록을 리터럴로 박아 두어 사용자가 지시한
    삽입마다 깨졌다 — 계약은 "2026-06-12 상대 순서 유지"이지 "목록이 그때
    그대로"가 아니다. 삽입분을 빼면 원본이 복원되는지로 다시 썼다(#19·#222).

### 실수 #271

271. **'백필했다'와 '화면에 보인다'는 또 다르다 — 페이지 창이 30일이었다**
    (2026-09-01 "8월 이전은 없는거야?"): 상장폐지 칩(#270)을 렌더타임 파생으로
    만들어 "옛 아카이브가 자동으로 따라온다"고 보고했는데, 정작 **페이지가
    최근 30일만 렌더**하고 있었다(모든 칩이 그랬다). 파생은 맞았지만 그 앞
    데이터는 화면에 도달할 경로가 없었다 — 실수 #11('배포완료 ≠ 화면에 보임')의
    창(window)판이다. 백필을 말할 땐 **그 데이터가 실릴 창이 있는지** 같이
    확인할 것.
    ⚠️ 넓히는 비용을 재 보니 CPU 가 아니라 **디스크 쓰기**였다(실측): 과거 월
    프래그먼트는 내용이 안 바뀌는데 1분 사이클마다 통째로 다시 쓰고 있었다 —
    30일 창에서도 7.98MB/분(하루 11GB), 90일이면 24.5MB/분. 내용이 같으면
    쓰지 않게 하자 2회차부터 프래그먼트를 **한 개도 안 쓰고**(0.32MB/사이클)
    렌더도 4만 건 0.42초였다. **창을 3배로 넓히면서 I/O 는 오히려 줄었다.**
    일반화: 창·주기를 넓히기 전에 "무엇이 비용인가"를 재고, 그 비용이 사실은
    **낭비**(안 바뀌는 것을 다시 쓰기)가 아닌지 먼저 볼 것 — 낭비를 없애면
    확장이 공짜가 된다.

### 실수 #272

272. **'이 숫자 맞아?' 에 사전지식으로 답하지 말 것 — 데이터의 불변식으로
    재라**(2026-09-02 잠정 속보 8월): 사용자가 "09/01 에 8월 전체 잠정이
    나오는데 이거 8월 잠정 맞어?" 라고 물었다. **타이밍은 정상**이다(관세청은
    익월 1일에 전월 1~말일 잠정을 낸다 — 모듈 독스트링에 이미 있다).
    의심스러운 건 크기였는데(전체 수출 983억$·무역수지 347억$/월·반도체가
    전체의 47.6%), "한 달 수출이 그럴 리 없다" 는 **내 사전지식이지 측정이
    아니다**(#12 — 사전지식 stale 가정 금지). 샌드박스는 키·네트워크가 없어
    실물 대조도 불가능하다.
    대신 **데이터 스스로 답하게** 했다: priodDt 는 월초 누적이므로 같은 달에서
    `FULL ≥ D2 ≥ D1` 이어야 하고, 창 폭이 30:20:10 이니 금액비도 그 비율을
    따라야 한다(`window_sanity`). 깨지면 원천이 누적이 아니거나 우리가 창을
    잘못 고른(합산한) 것이다 — 사전지식 없이 갈린다.
    `fetch_provisional --why [YYYY-MM]` 는 창별 절대액을 **작년 동창과 나란히**
    찍는다(#51 나란히 놔야 보인다). API 0콜·읽기 전용이고, 저장된 시계열이
    없으면 ✅ 가 아니라 ❌ 로 찍는다(#54).
    ⚠️ 허용범위를 좁게 잡지 말 것 — 월말 밀어내기·휴일로 정상 달도 비율이
    흔들린다. 좁게 잡으면 매달 경고가 떠 아무도 안 본다(#260).

### 실수 #273

273. **CSS 정의 없는 클래스는 소스를 훑어선 못 잡는다 — 렌더 출력으로 재야
    한다**(2026-09-02 `js-` 훅 규약): #201 을 규율로 두고 1년을 지냈더니
    `.stat-num`/`.stat-lbl` 이 **레포 어디에도 정의 없이 33곳**에서 쓰이고
    있었다(포트폴리오·가계부·페이퍼·GICS 4화면). 같은 용도에 이름이 셋이었고
    (`.stat-v`/`.stat-l` · `.stat-value`/`.stat-label` · 이것) 셋째만 CSS 를
    못 받은 것이다. `.nav` 는 `_BASE_CSS` 계열(watchlist·screener_domains)에,
    `.muted` 는 paper 번들에 없었다. **값이 다 '있어서' 아무 감사도 안 걸렸다**
    — 샌드박스는 렌더를 못 보므로(#14) 사용자만 발견한다.
    소스 문자열 스캔(외부 lint 방식)은 이 레포에서 **오탐 71건**이다 —
    복합선택자(`.df-pill-sig.active{`) · JS 훅 · 스크레이퍼 선택자(`.rank-td`) ·
    테스트 픽스처가 전부 걸린다. 렌더 **출력**을 재면 정확하다(#35 화면이 쓰는
    그 경로). 단 `<style>` 만 보면 **JS 가 런타임 주입하는 CSS**(`.cmd-panel`
    5건)를 미정의로 오보하므로 출력 전체를 볼 것.
    ⚠️ CSS 가 없는 게 **정상**인 클래스가 실재한다(`querySelectorAll` 전용
    훅 — `mc-tab`·`mc-embed`). 그래서 가드가 성립하려면 예외를 **이름으로
    열거**하지 말고(#24) `js-` 접두로 **스스로 밝히게** 해야 한다. 면제를
    두면 우회 통로가 되므로 반대 증거도 같이 본다 — `js-` 인데 CSS 가 있으면
    실패, 아무 JS 도 안 쿼리하는 `js-` 도 실패(#25·#53).
    ⚠️ 훅 이름을 바꿀 땐 **감사·회귀의 계수 패턴도 같은 커밋에서** 바꿀 것 —
    `dart_mcap_audit` 이 `class="mc-tab"` 을 세고 있어 안 고쳤으면 0건을 세고
    조용히 통과했다(#47·#54).
    ⚠️ 첫 뮤테이션이 통과했다: `.nav{` 한 줄만 지웠는데 `.nav a{` 가 남아
    정의로 세어졌다 — 지우려면 **그 이름의 모든 선택자**를 지워야 발화한다
    (#91c 깨지는 값까지 밀어 볼 것).
    ⚠️ **빈 픽스처는 페이지 껍데기만 그린다**(독립 리뷰가 잡았다): 빈 gics 는
    초록인데 run 하나를 넣자 `run-*` **6개**가 드러났고, screener 에 아카이브를
    넣자 `cnt`·`cs-card`·`cs-day` 가 더 나왔다. paper 는 `rows`/`trades` 가
    없으면 조기 반환해 stat 타일을 통째로 안 그려, 옛 이름으로 되돌리는
    뮤테이션이 그대로 통과했다. 렌더 가드의 픽스처는 **카드·표·상세 블록이
    실제로 그려지는** 내용이어야 한다(#54·#91c).
    ⚠️ **정의가 있어도 캐스케이드에서 지면 화면은 그대로 깨져 있다**: paper 는
    `_SCREENER_CSS + _PF_CSS` 라 뒤에 오는 `.pf-tbl td`(0,1,1)가 일반
    `.muted`(0,1,0)를 이겨 표 셀의 muted 가 죽어 있었다. 커버리지 가드는
    '정의가 있다'까지만 본다 — 명시도 축은 따로 못박을 것.
    ⚠️ 그리고 그 명시도 테스트가 **내 CSS 주석을 셀렉터로 세어** 눈이 멀었다
    (`/* … #273 … */` 이 (1,3,2)로 최강). 정의 판정도 같은 구멍이었다 —
    CSS 를 재는 코드는 **주석을 지우고 볼 것**(#59b, 이번엔 소스가 아니라
    렌더 출력에서 났다).
    ⚠️ 가드가 **내 변경의 회귀도 잡았다**: `.muted` 를 `_PF_CSS` 로 옮기자
    같은 번들을 안 쓰는 screener 페이지가 그걸 잃었다. 규칙을 옮길 땐
    "지금 쓰는 곳이 전부 그 번들인가"를 먼저 답할 것(#38).

### 실수 #274

274. **이상 없음을 말하지 않는 진단은 '검사가 돌았다'를 증명하지 못한다**
    (2026-09-02 잠정 `--why` VM 실측): 표만 찍히고 ✅/⚠️ 가 **한 줄도 없었다**
    — 불변식이 돌아서 통과한 건지, 그 코드가 아예 안 탄 건지 사용자가 알 방법이
    없다. #272 에서 "좁게 잡으면 매달 경고가 떠 아무도 안 본다"며 **오탐**만
    막고 **침묵**은 안 막은 것이다(#41 감사는 사실을 항상 말할 것 · #43 침묵이
    최악 · #54 대조 0건은 통과가 아니다).
    대응: 통과도 한 줄로 말하고 **무엇을 쟀는지 실측값을 같이** 적는다
    (`D1→D2 2.59배[창 2.00]`) — 그래야 사용자가 눈으로 검산한다(#202).
    창이 하나뿐이면 ✅ 가 아니라 **판정 불가(❓)** 다.
    ⚠️ 일반화: 진단을 쓸 때 "이상이 없을 때 이 도구는 무엇을 출력하나"를
    먼저 답할 것. 빈 출력이 정답인 도구는 없다.
    ⚠️ 같이 드러난 것: 창 불변식(누적 단조·창 폭 비율)은 **한 달 안에서만**
    본다 — 전체 칸 자체가 어긋난 경우는 통과시킨다. 원천이 `amt[0]=전체 ·
    amt[1..10]=상위10` 을 같이 주므로 **구성요소와 대조**하면 갈린다(#51):
    상위10 합 > 전체는 불가능(하드 불변식)이고, 전체만 부풀면 상위10 **비중**이
    떨어지지만 전 품목이 고루 늘었으면 비중은 그대로다. 두 해를 나란히 찍는다.
    검사를 넣을 땐 "이 검사가 못 보는 축은 무엇인가"를 먼저 답할 것.
    ⚠️ **찍기만 하고 판정하지 않으면 침묵이 한 층 아래에서 재발한다**
    (독립 리뷰가 잡았다): 상위10 비중을 두 해 나란히 찍어 놓고 **판정을 안 해서**,
    이 항목이 근거로 든 바로 그 시나리오(전체만 1.69배 부풀림 · 비중 71%→42%)에
    `✅ 정상` 이 그대로 찍혔다. 값을 보여주는 것과 판정하는 것은 다르다 —
    ✅ 는 **무엇을 쟀고 얼마였는지**까지 말해야 한다.
    ⚠️ 같은 리뷰가 잡은 나머지: (a) ❓ 사유를 "창이 하나뿐"으로 **고정**해
    표에 3줄이 찍힌 뒤 자기 표를 뒤집었다(실제 조건은 '금액 있는 창 2개 미만')
    (b) `not amt[0]` 가드 때문에 **전체=0 인데 상위10 이 채워진 행**(가장 확실한
    손상)에서 검사가 통째로 안 돌았다 (c) 중복 decile 에서 표는 첫 행, 판정은
    마지막 행을 봐 실측배수가 눈검산과 어긋났다 (d) 표시 경로만 `amt` 부재를
    안 막아 **손상 데이터를 보려는 도구가 손상 데이터에서 먼저 죽었다**
    (e) 가장 직접적인 무료 불변식 `exp_item 전체 == exp_cnty 전체`(같은 총액,
    문턱 불필요)를 안 쓰고 있었다.
    ⚠️ 그리고 그 교차 대조에 **짝이 없을 때 ✅ 를 찍는** 버그를 내가 다시 냈다
    — 대조 0건은 통과가 아니다(#54). 검사를 추가할 때마다 "대조할 게 없으면
    무엇을 출력하나"를 같이 답할 것.
    ⚠️ 품목별 기여를 붙이며(사용자 요청) 리뷰가 또 여섯을 잡았다: (a) 총계가
    **줄어든 달**엔 기여율 부호가 뒤집혀 늘어난 품목이 '기여 -200%' 로 찍혔다
    — 방향(증가/감소)을 라벨이 말해야 한다 (b) 창 선택이 `_decile_amounts`
    (전체 0 은 건너뜀)와 갈려 손상된 창을 집어 **손상 경고보다 먼저** 표를
    찍었다(#38 단일 출처) (c) 작년 전체만 0 이고 항목은 채워진 경우 전 칸이
    '—' 로 조용히 비었다(#43) (d) 국가별 계열에 '품목별' 헤더(#34), 작년이
    없는데 '기여'를 약속 (e) `"기여" in out` 단언을 **헤더가 대신 만족**시켜
    그 칸을 지워도 통과했다(#75) (f) 슬롯 순서를 원천으로 확인한 계열은
    `imp_cnty` 뿐인데 이름을 사실처럼 적고 있었다 — `LABELS_VERIFIED` 로
    가르고 나머지는 '슬롯 순서 미검증'이라 밝힌다(#165·#50).

### 실수 #275

275. **히스토그램이 다음 라운드를 엉뚱한 데로 보낸다 — 진단이 파서와 다른
    창을 봤다**(2026-09-04 격주 수주잔고 리뷰 `미지원단위 (단위 : 사) 30건`):
    읽으면 "단위를 더 지원하면 30건이 풀린다" 인데, `사`(회사 수)를
    `_UNIT_MULT` 에 넣으면 **개수를 금액으로 읽는다**. 파서 `_unit_mult` 는
    캡션이 4000자보다 멀면 거부하는데 진단은 본문 **전체**를 역탐색해,
    파서가 거리 때문에 버린 캡션을 근거로 사유를 지었다 — 실측으로
    `(단위 : 백만원)`(우리가 **지원하는** 단위)을 '미지원단위' 라고 말했다.
    #105(범위)·#107(관문)·#109(어휘)·#111(원천 부재)에 이어 **같은 보고서가
    네 번째로 같은 함정**을 냈다: 창·범위·문턱·어휘 중 하나만 갈라도
    감사와 화면이 갈라진다(#35·#38 — 창을 `_CAP_WINDOW` 단일 상수로).
    대응: 갈래를 사실대로 나눈다(#82) — `금액캡션 멀다 N자(창 4000)`(표 경계
    문제) · `캡션 외화`(환율 필요) · `캡션 비금액`(그 라벨이 다른 표에 있다) ·
    `캡션 미지원`(정말 모르는 단위) · `캡션없음`. 처방이 다섯 다 다르다.
    ⚠️ 그리고 **진단 어휘를 바꾸면 이미 쌓인 기록이 다음 보고서를 지배한다** —
    옛 `미지원단위 (단위 : 사)` 줄이 최상단에 남아 이번 fix 가 화면에 한 글자도
    안 닿는다(#21b·#216 캐시가 fix 를 가리는 형태, dedup 이 JSON 한 줄 전체라
    옛 줄과 새 줄이 **둘 다** 남아 중복 계수까지 된다). 레코드에 어휘 판을
    찍고 옛 줄은 **세지 않되 몇 건인지 말한다**(#43). 판 번호는 손으로 올리지
    말고 **갈래 목록에서 파생**시킬 것(`_vocab_sig(_DETAIL_KINDS)`) — 손 bump 는
    이 레포에서 다섯 번 졌다(#119 규율을 구조로). 읽는 쪽·쓰는 쪽·CLI 가
    **같은 술어**(`is_current_vocab`)를 써야 통계가 안 갈린다(#35·#38).
    ⚠️ 독립 리뷰가 잡은 나머지: (a) far/near 갈래 판정이 갈려 far 를 전부
    '금액캡션' 이라 불러 '창을 넓혀라' 로 읽혔다 — `_cap_kind` 하나로 통일
    (b) `_FX_CAP_RE` 가 `불`·`엔`(억불·천엔)을 몰라 외화가 '미지원' 이 됐다
    (c) `_UNIT_RE` 의 lookahead 가 공백을 안 넘어 `(단위 : 백만 달러)` 를
    1e6(백만원)으로 읽었다 — **1,400배 오차**. ⚠️ 그 fix 가 곧바로 100만배
    버그를 낳았다: 띄어쓴 `백만 원` 에서 `백만` 이 lookahead 에 걸려 물러나자
    맨 `원` 이 잡혀 **1.0** 이 됐다 — 정규식에 띄어쓴 대안을 같이 넣고
    `_UNIT_MULT` 조회 전에 공백을 지울 것. 부정 lookahead 를 넣을 땐 "물러나면
    무엇이 대신 잡히나"를 먼저 답할 것.
    ⚠️⚠️ **그리고 그 lookahead 가 정상 캡션을 통째로 죽였다**(2차 독립 리뷰
    실측): 공백을 넘어 막자 `(단위 : 억원 기준)`·`(단위 : 백만원 미만 절사)`·
    `(단위 : 원 단위)` 가 전부 None 이 되어, 0.35조짜리 표가 파싱되던 것이
    `캡션 미지원` 으로 찍혔다 — **내가 없애려던 바로 그 오보를 새로 만든 것**
    이다(#146 재발: 증상(뒤에 글자가 온다)으로 막으면 그 증상을 정당하게 갖는
    케이스가 같이 죽는다). 원인으로 갈라야 한다 — **토큰에 `원` 이 있으면
    통화가 확정**이므로 뒤 낱말과 무관하고, 맨 스케일(`백만`·`천`)일 때만
    공백을 넘어 막는다. 가드를 좁힐 땐 "지금 통과하던 것 중 무엇이 같이
    죽나"를 **실측으로** 먼저 셀 것.
    ⚠️ 어휘 판은 **상세를 만드는 모든 코드**를 덮어야 한다 — 처음엔 캡션
    갈래만 넣었는데 단위를 **찾은** 행(히스토그램의 다수)은 전부 `_gate_stage`
    가 문구를 만든다. 그 문구를 바꿔도 판이 그대로면 옛 줄이 계속 지배한다.
    문구를 `_GATE_LABELS` 한 곳에 두고 판이 거기서 파생되게 했다(#38).
    ⚠️ 그리고 **prune 은 첫 쓰기에 끝난다** — 옛 줄을 지우면 격주 보고서가
    299건에서 몇 건으로 **조용히** 줄고 고지는 한 번도 안 뜬다. 걷어낸 수를
    묘비(`legacy_dropped`)로 남겨 30일 동안만 말하게 했다(#43 → #260).
    ⚠️ 깨진 줄을 '옛 어휘' 로 세면 **틀린 사유**를 말한다 — 어휘 판정과 파싱
    실패는 다른 갈래다(#82). 그리고 `picked` 를 루프 안에서 재대입해 목록 밖
    값이 오면 IndexError 였다(찾았을 때만 바꿀 것).
    ⚠️ 가까운 무관표 캡션이 **먼 금액 캡션**을 가렸다 — 후자가 고칠 수 있는
    사유(표 경계·창)이므로 먼저 말해야 한다(#260 ❌ 는 고칠 수 있는 것을
    가리켜야 한다). 여러 라벨 자리를 훑는 진단은 "가장 먼저 찾은 것"이 아니라
    **"가장 행동 가능한 것"** 을 머리에 둘 것.
    ⚠️ 같은 보고서가 `000660 ×10` 과 `000660.KS ×4` 를 **따로** 올렸다 —
    호출부마다 티커 표기가 달라 집계가 갈리고 '종목 N개' 도 부풀었다(#45).
    쓰기·읽기 **양쪽**에서 통일하되 국내 6자리+KS/KQ/KN 만 뗄 것(해외는 접미가
    시장을 가른다 — `7203.T` 를 떼면 다른 나라 코드와 충돌). 형제 CLI
    (`backlog_misses`)도 같은 헬퍼를 import 할 것 — 복제하면 감사와 화면이
    다른 수를 낸다.
    ⚠️ 뮤테이션이 **쓰기 정규화 사각**을 드러냈다: 읽기 테스트가 로그를
    직접 써서 `_log_miss` 를 한 번도 안 태웠고, 쓰기 정규화를 지워도 전
    테스트가 통과했다(#20). 그리고 그건 중복 기록 방지(`line in old`)까지
    무력화한다 — 같은 종목·분기가 두 줄로 쌓인다.
    ⚠️ **파서는 이 라운드에서 늘리지 않았다** — #111 이 "낮은 커버리지를
    보면 파서를 더 짜기 전에 표본 원문부터" 라고 적었고, 그 히스토그램이
    지금 틀렸다는 게 이번에 확인됐다. 진단을 고쳐 **다시 잰 뒤** 원문
    표본(`backlog_misses --ticker`)으로 판정한다.
    ⚠️ 같은 실행의 전체 회귀가 **무관한 시한폭탄**을 드러냈다: 블로그
    픽스처가 `Fri, 21 Aug 2026` 을 박아 두어 오늘(09-04)이 `_MAX_AGE_DAYS`
    (14일)를 넘자 멀쩡한 코드가 빨간불이 됐다(#249·#42 재발). 같은 형태가
    **네 클래스**에 있어 하나만 고치면 나머지 셋이 며칠 뒤 터진다(#24) —
    `_rss_pubdate()` 로 **오늘 기준 상대**로 바꿨다. 날짜가드 자체를 재는
    테스트는 일부러 옛 날짜를 쓰므로 그대로 뒀다(대조 0건 방지, #54).

### 실수 #276

276. **다섯 자리에서 조용히 None 을 돌려주는 함수 + "아직 없습니다" 카드 =
    며칠을 모른다**(2026-09-04 "한글 Daily byte 가 갑자기 며칠간 또 작동안한것
    같은데 이것 또 왜그런거야? 아 진짜 힘들다"): `daily_kr_flow.generate()` 는
    LLM 키 없음 · **KRX 로그인 없음** · 수급 데이터 없음 · Pro 호출 실패 · 빈
    응답 다섯 자리에서 None 을 돌려주고 `main()` 은 로그만 남긴다. 화면은 그
    사이 "Daily Byte 아카이브가 아직 없습니다" 한 줄이었다 — #52 가 바로 이
    구별('조용한 것 vs 죽은 것')을 위해 `feed_health` 를 만들어 놨는데 **Daily
    Byte 는 등록돼 있지 않았다**(blog·reddit·cheongyak·realestate 넷뿐).
    ⚠️ 그리고 그 문구는 **사실과 달랐다** — 카드 로더가 최근 **7개 날짜**만
    보므로 8일 넘게 멈추면 기록이 있는데도 '없다'고 단정한다(#43·#82).
    대응 셋: (a) 유닛 도장(`feed_health.mark`)을 KR·US 양쪽에 (b) 빈 카드는
    '한 번도 없음' / '마지막 N일 전 — 이후 멈춤' + 점검 시각으로 **갈라 말한다**
    (c) `daily_kr_flow --why` 가 인터프리터·자격증명 출처·휴장·KRX 실호출·
    아카이브(미국을 **대조군**으로)를 갈래로 찍는다(#82·#143·#252).
    ⚠️ **엔트리포인트를 파일 중간에 두면 그 아래 정의는 영영 안 닿는다** —
    `cat >>` 로 `why()` 를 덧붙였더니 `if __name__` 블록이 그 **위**에 있어
    `--why` 가 한 번도 실행될 수 없었다(#68 의 재발, 이번엔 클래스가 아니라
    모듈 레벨). 엔트리포인트는 항상 맨 끝.
    ⚠️ 독립 리뷰가 여덟을 잡았고 그중 넷이 **진단이 거짓말하는** 종류였다:
    (a) 판정 순서가 휴장보다 KRX 를 먼저 봐 **휴장일에 멀쩡한 자격증명을
        범인으로** 지목 (b) 프로브가 walk-back 없이 오늘만 조회해 장중·휴장에
        '조회 실패' 오보(#56 프로브는 제품의 루프 구조까지 베낄 것)
    (c) raw `GOOGLE_API_KEY` 를 봐 **Vertex 모드에서 '생성 불가'** 오보(제품은
        `genai_factory.effective_key()` 를 쓴다, #35) (d) `load_dotenv` 를 출처
        판정 **앞**에 둬 모든 키가 '환경변수' 로 찍혀 #23 의 출처 갈래가 통째로
        무력화. 나머지: 도장이 거래일 게이트 뒤라 **금요일 공휴일+주말 96h >
        상한 80h** 로 멀쩡한 타이머가 ⚠️ · 프로브가 화면 헬퍼를 복제 ·
        빈 카드 상태에서 30초마다 90개 디렉터리 재스캔(#271).
    ⚠️ 뮤테이션 둘이 처음에 통과했다: 하나는 **뮤테이션 자체가 no-op**
    이었고(#267 뮤테이션이 그 자리를 실제로 쳤는지 볼 것), 하나는 AST 로
    "호출이 있나"만 봐서 **게이트만 꺼도 통과**했다(#141) — 렌더 출력을 값으로
    봐야 발화한다. 그리고 `today` 를 늘 넘기는 픽스처 때문에 기본 인자 분기가
    한 번도 안 돌아 `_KST` 미정의를 **#210 가드가 대신** 잡았다(#91c).

### 실수 #277

277. **가이드에만 적은 '기준이 다름'은 사용자가 매번 묻는다 — 행이 말해야
    한다**(2026-09-04 "행 자체가 밝히게 해줘"): 유동성 보드는 최신값만 실시간
    으로 덮고 1M/3M/YoY·차트는 FRED 확정 히스토리를 쓴다. ℹ️ 가이드가 그걸
    정확히 적어 뒀는데도 사용자는 `₩1,351.1` 옆 `-3.99%` 를 가로로 읽고 물었다
    — 가이드는 표 밖에 있고 눈은 행을 읽는다(#202 '다르다'만 말하면 안 통한다).
    대응: 덮기 **전에** FRED 앵커를 남겨(`fred_date`) 기준일 칸이 "등락·차트
    기준 FRED 09-03" 을 적고, 상세엔 두 기준의 **값을 나란히**.
    ⚠️ **사유를 단정했다가 독립 리뷰에 잡혔다**: 스프레드 불일치를 "FRED 자체
    시리즈라서" 라고 적었는데 T10Y2Y 는 정의상 DGS10−DGS2 이고 **이 레포
    자신의 `macro_cadence` 가 "위 둘의 차"라고 적어 두고 있었다**. 진짜 원인은
    화면에 실린 값들의 **기준일이 다른 것**(10Y 실시간 · 2Y 하루 전)이다 —
    우리가 잰 것은 '어느 구성 행이 어떤 기준인가' 까지이므로 그것만 적는다
    (#165 재지 않은 귀속 금지 · #187b 틀린 사유가 헛걸음을 만든다).
    ⚠️ 그리고 **화면에 없는 구성요소를 가리키는 플래그는 조용한 no-op** 이다 —
    T10Y3M 의 `DGS3MO` 는 보드에 없어 판정이 영원히 안 돌았다. 대조할 게
    없으면 ✅ 가 아니라 애초에 달지 말 것(#54). 회귀도 id 를 **보드 횡단**으로
    합치면 남의 보드 id 가 통과한다(실측) — 같은 목록 안에서만 대조할 것.
    ⚠️ 그리고 **DOM 스텁이 `textContent`/`innerHTML` 을 한 칸으로 쓰면
    `esc()` 가 항등 함수**가 되어 이스케이프 회귀를 못 잡는다(#253 하네스의
    다음 단계). 실제 DOM 처럼 갈라 둘 것.
    ⚠️ 하네스가 제품 로직을 **재구현하면** 그 로직을 지워도 통과한다(#19) —
    실시간 스태시를 하네스에서 흉내 내던 것을 `fetch` 동기 스텁으로 바꿔
    **제품의 `liveFx()` 를 그대로 태웠다**. 그리고 열려 있는 상세가 그 틱에
    의존하는 행이면 같이 갱신해야 한다(표는 '기준일 다름', 상세는 옛 값 = 한
    화면이 두 말, #38).

### 실수 #278

278. **진단 도구를 심어 놓고 그 실행 명령을 틀리게 인쇄했다**(2026-09-04
    `~/stock/.venv/bin/python -m bot.daily_kr_flow --why` → `ModuleNotFoundError:
    No module named 'bot'`): `python -m` 은 **cwd 에서** 패키지를 찾으므로
    홈에서 돌리면 진단 자체가 안 뜬다. 레포 관례는 `cd ~/stock && .venv/bin/
    python -m …`(trade 는 `~/stock-trade`)이고 systemd 도 `WorkingDirectory=`
    를 준다 — 파일 맨 위 독스트링엔 그렇게 적혀 있는데 **내가 인쇄한 문구만**
    `cd` 를 빼먹었다. 사용자가 무음 원인을 확인하려던 그 한 턴을 날렸다.
    안내 문구를 다듬는 규율로는 또 진다(#191 같은 벽에 두 번) → **인쇄되는**
    실행 안내를 디렉터리 전수로 검사한다(#24). 켜자마자 5건을 잡았고 그중
    셋은 내 것이 아니었다(#87a 새 가드는 켜자마자 뭔가 잡는 게 정상).
    ⚠️ 검사 **범위**를 처음에 틀렸다: 문자열 상수를 전부 훑어 49건이 나왔는데
    대부분 독스트링의 `cd` 가 내 40자 창 밖에 있던 오탐이었다(#47 감사 도구의
    계수 패턴 자체가 틀릴 수 있다). 사용자가 실제로 보는 것은 `print(...)`
    이므로 거기로 좁히고, "대조 0건이면 실패"를 같이 뒀다(#54).
    ⚠️ **그리고 뮤테이션 복원을 `git checkout tests/…` 로 해 미커밋 테스트를
    통째로 날렸다** — #139 에 "뮤테이션 복원은 백업 파일로만" 이라고 적어 두고
    같은 실수를 냈다. 다행히 '복원 후' 실행이 0건으로 나와 바로 발각됐다:
    복원 뒤에는 **테스트가 실제로 도는지** 숫자로 확인할 것(#68).

### 실수 #279

279. **진단이 '휴장'으로 확정된 실패를 덮었다 — 그리고 답이 나온 뒤에도 계정을
    열 번 두드렸다**(2026-09-04 VM `--why` 실측, #276 의 다음 층): 출력은
    ④ 에서 `⚠️ KRX 비밀번호 변경이 필요합니다 / 오류 메시지: 패스워드 변경
    필요` 를 열 번 찍고 ⑤ 는 `한국 마지막 기록 2026-08-27(9일 전)` 인데,
    ⑥ 판정은 `⏸ 오늘은 휴장 — 이상 없음` 이었다. 휴장을 **가장 먼저** 보게
    한 건 독립 리뷰가 옳게 지적한 것(휴장일엔 수급이 정상적으로 빈다)인데,
    그 순서가 **휴장과 무관한 사실**(로그인 확정 실패·9일 공백)까지 덮었다
    — #41 을 적어 두고 내 도구에서 다시 어겼다(#260 ❌ 는 고칠 수 있는 것을
    가리켜야 한다). 휴장이 정당하게 설명하는 것은 **빈 수급 응답 하나**뿐이다.
    ⚠️ 그리고 사유를 갈래로 안 말했다: 프로브는 `전부 빈 응답 — 로그인
    만료/차단 의심` 이라고 **추측**했는데, 진짜 사유는 바로 위에 원문으로
    찍혀 있었다(#82·#187b). pykrx 는 우리 코드가 아니라 **라이브러리가
    stdout 으로** 사유를 찍으므로 그 원문을 잡아 갈래를 읽는다
    (`krx_login_verdict`) — `pw_change`(계정 잠김: 키를 고쳐도 안 풀린다) ·
    `bad_cred`(값 오류) · `env_unset`. 같은 실행에 둘 다 찍히므로 더
    **행동 가능한** 쪽이 이겨야 하고(#275), 아는 문구가 없으면 단정 대신
    **원문 표본**을 돌려준다(#109·#165).
    ⚠️ 확정 실패인데도 walk-back 4일 × 2시장을 그대로 돌아 실패 로그인을
    **열 번** 반복했다 — 답이 나온 뒤의 아홉 번은 계정만 두드린다. 반대로
    '조용한 빈 응답'은 제품처럼 4영업일까지 거슬러야 한다(#56): 재시도 중단
    조건은 '실패했나'가 아니라 **'더 물어서 답이 바뀌나'** 다.
    ⚠️ 곁다리 둘: (a) pykrx 는 **import 시점에** 로그인을 시도해 `KRX_ID
    또는 KRX_PW 환경 변수가 설정되지 않았습니다` 를 찍는데, `load_dotenv`
    前이라 키가 멀쩡해도 늘 나온다 — 그게 ① 위에 찍혀 바로 아래 ② 의 ✅ 와
    정면으로 모순됐다(사용자가 첫 줄을 원인으로 읽는다). (b) SV 토큰은
    `TELEGRAM_BOT_TOKEN` 의 폴백일 뿐인데 ❌ 로 찍어 무해한 줄로 눈을 끌었다.
    ⚠️ 뮤테이션 M1 이 통과했다 — 픽스처가 `sys.modules` 에 스텁을 꽂아
    `__import__` 가 **아무것도 실행하지 않았다**(찍히지도 않는 노이즈를
    '안 샌다'고 단언한 것, #91c). `find_spec` 으로 **실행 없이** 물어
    finder 가 이기게 해야 발화한다.
    ⚠️ 그리고 독립 리뷰가 **10건**을 더 잡았는데 절반이 '진단이 사실 아닌
    것을 단정' 하는 종류였다: (a) `kr_age` 2~3 이 ⑤ 는 ✅ 인데 ⑥ 은 '기록이
    없다' — 두 곳이 다른 상수를 봤다(#38) (b) ④ 가 '✅ 수신' 인데 버퍼에 남은
    옛 문구로 ⑥ 이 '로그인 실패가 원인' — **재료를 받았으면 로그인은 정의상
    됐다** (c) `td is None`(캘린더 판정 불가)을 거래일로 취급해 진짜 휴장을
    '자격증명 확인' 으로 보냈다 (d) '연휴(최대 3일)' 는 설·추석(4~6일
    비거래일)에 매년 오탐 → **놓친 거래일 수**로 재야 한다 (e) '타이머가 안
    돌았을 수 있다' 를 **안 재고** 적었다 — #276 에서 바로 그 구별을 위해
    심은 `feed_health` 도장을 안 읽고 있었다(#150·#165). (f) 힌트 목록이
    '실패·오류' 라 **아무 한글 오류**나 로그인 실패로 읽고 휴장·LLM 분기를
    덮었다 — 확정 갈래에서만 원인이라 말하고 재조회를 멈춘다.
    일반화: 진단의 **모든 문장**이 잰 것인지 물을 것. '없음'을 말하는 자리
    (#82)만이 아니라 **'있음'을 말하는 자리**도 근거가 필요하다.

### 실수 #280

280. **요청 경로에서 7페이지를 직렬로 받고 있었다 — 그리고 병렬화가 '부분
    스냅샷' 이라는 새 실패모드를 열었다**(2026-09-04 "업종별(시세) 여기는 …
    좀 많이 느려"): `/theme` 는 네이버 테마 1~7쪽을 **차례로** 받고(각 상한
    15초) 캐시는 장중 30초라, 대부분의 클릭이 콜드로 왕복 7번을 그대로
    기다렸다(#116 본 응답 경로의 무거운 값). 페이지끼리 의존이 없으므로 한
    물결(`map_bounded`)로 받고, 낡은 스냅샷은 즉시 주고 뒤에서 채운다(SWR).
    ⚠️ 그런데 **직렬 판의 `break` 가 사실은 가드였다** — 첫 실패에서 멈춰
    부분 결과를 안 만들었다. 병렬로 바꾸면 4쪽만 429 를 맞아도 나머지가
    합쳐져 **~170개를 완전본으로** 굽는다(독립 리뷰). 실패(`None`)와 빈
    페이지(`[]`)를 갈라 `partial` 을 싣고, 부분·빈 결과는 **캐시하지
    않는다**(#119). 일반화: 직렬 루프를 병렬로 바꿀 때 **`break` 가 무엇을
    막고 있었는지** 먼저 답할 것.
    ⚠️ 독립 리뷰가 다섯을 더 잡았다: (a) 콜드 경로가 결과를 무조건 캐시해
    전 페이지 실패 시 `{"themes": []}` 가 굳는데, `_session_fresh` 는 장
    마감 뒤 스냅샷을 무조건 fresh 로 보므로 **다음 개장까지 빈 화면이
    재시도 없이** 서빙된다 (b) 배경 갱신과 전경이 **다른 키**로 dedupe 해
    (`_BG_KEYS` vs single-flight) 창을 벗어난 요청이 겹치면 14건이 동시에
    나가고 두 writer 가 경합한다 (c) `write_text` 는 truncate 후 버퍼 쓰기라
    배경 writer 와 요청 reader 가 겹치면 **찢어진 JSON** 을 본다 →
    임시파일+`os.replace` (d) `Thread.start()` 가 던지면 `finally` 가 안 돌아
    그 키의 배경 갱신이 프로세스 수명 내내 죽는다 (e) SWR 창(10분)이 캐시
    TTL(30초)보다 넓어 **정상 조회의 대부분이 stale** 이라 '갱신 중' 배지가
    늘 켜진다 — 늘 뜨는 배지는 아무것도 안 재는 것과 같다(#25·#260). 잰
    나이로 게이트하고 그 값을 같이 적는다.
    ⚠️ 뮤테이션 넷이 통과했다. 둘은 **#130 을 그대로 재발**시킨 것 —
    공용 풀의 '최대 동시 실행 수' 와 스레드 3개 경합으로 잰 단언이 단독
    실행에선 green, **전체 실행에선 red** 였다(부하가 걸리면 리더가 팔로워
    보다 먼저 끝난다). 배선 계약(`map_bounded` 에 7개를 한 번에 넘긴다 ·
    그 키로 `once` 를 탄다)으로 다시 썼다. 나머지 둘은 독스트링의
    `os.replace` 가 소스 검사를 대신 만족시킨 것(#59b)과, SWR 분기가 먼저
    걸려 콜드 경로 폴백을 **한 번도 안 태운** 픽스처(#91c)였다.

### 실수 #281

281. **도장이 없다는 것과 타이머가 안 돌았다는 것은 다른 사실이다 — 그리고 내가
    준 확인 명령은 침묵할 수 있었다**(2026-09-05, #279 의 다음 층): `--why` 가
    `↪ 점검 도장이 없다 — 타이머가 아예 안 돌았을 수 있다` 를 찍었는데, 도장을
    찍는 `feed_health.mark("daily_byte_kr")` 자체가 **마지막 발화(09-04 19:00
    KST) 뒤에 배포**됐다. 즉 도장 부재는 타이머 실패의 증거가 아니었다 — #279
    에서 "안 재고 적으면 헛걸음을 만든다"고 적으며 넣은 그 줄이, 도장 하나만
    보고 **다시 안 잰 문장**이 된 것이다(#165·#187b). 상태는 **아는 쪽에
    묻는다**(#86): 발화 시각과 종료 상태는 systemd 가 안다 →
    `systemd_facts()`(읽기 전용 `systemctl show`) + 순수 `timer_verdict()` 가
    다섯 갈래로 말한다(못 물음 / 미설치 / 한 번도 발화 안 함 / 발화했는데 실패 /
    발화·정상종료). 마지막 갈래에서도 도장 부재의 원인은 **단정하지 않는다**
    (배포가 나중이었을 수도, 실행이 도장 앞에서 죽었을 수도 — 회귀가 그 단정을
    금지한다).
    ⚠️ 짝: 업종별 시세가 왜 느린지 확인하라고 준 `journalctl … | grep
    'naver_sector: themes'` 가 **세 번 다 빈 출력**이었다. 장 마감 뒤엔
    `_session_fresh` 가 마지막 마감 이후 스냅샷을 fresh 로 보므로 수집이 아예
    안 돌고, 그러면 **로그가 없는 게 정상**이다 — 그 침묵은 '배포 안 됨'·
    '재시작 안 됨'·'페이지를 안 눌렀음'과 구별되지 않는다(#274 이상 없음도
    말해야 한다). 확인 명령을 줄 때 **"이상이 없으면 이 명령이 무엇을
    출력하나"** 를 먼저 답할 것 — 빈 출력이 정답인 명령은 안내가 아니다.
    반복되는 확인은 제품에 심는다(#252) → `naver_sector_client --check` 가
    캐시 나이·신선 판정(화면이 쓰는 그 술어로, #35)·SWR 창 안팎을 갈래로 찍고,
    기본은 읽기 전용이라 진단이 운영 상태를 안 바꾼다(#264).
    ⚠️ 독립 리뷰가 **일곱**을 더 잡았고 절반이 같은 병의 다른 얼굴이었다:
    (a) **도장이 있으면** 무조건 현재형('타이머는 돌았다')으로 말하고 systemd
        조회를 통째로 건너뛰었다 — 도장은 실행 **시작**에 찍히므로 옛 도장은
        '지난주에 돌았다'는 뜻이다. 부재만 재고 **나이는 안 잰 것**(#165 의
        같은 실수를 반대편에서 반복). `feed_health.overdue` 로 상한과 대조한다.
    (b) `LoadState` 는 '유닛 파일이 파싱됐다' 까지만 말한다 — 중지·비활성
        타이머도 `loaded` 이고 `LastTriggerUSec` 는 `Persistent=true` 로 남아,
        **'재배포 뒤 조용히 멈춘 타이머'(이 도구의 존재 이유)가 정상으로
        보고**됐다. `ActiveState` 를 같이 물어야 갈린다. `Type=oneshot` 은
        실행 중에도 `Result=success` 라 `SubState` 없이는 '정상 종료' 오보.
    (c) `LoadState` 네 값(`not-found`/`masked`/`error`/`bad-setting`)에 처방
        하나를 붙여 **masked 에 `enable --now`** 를 시켰다(그 명령은 실패한다).
    (d) `--check` 가 **테마 0개인 fresh 캐시에 ✅** 를 찍었다 — 형제 가드
        (`_collect_and_store`)가 막으려던 바로 그 상태를 프로브가 축복(#54).
    (e) SWR 갈래를 **나이로만** 갈라, 쓸 저장분이 없어 동기 수집을 타는
        경우에 '즉시 응답'이라 말했다 — 화면과 **같은 조건**(`have_old`)으로.
    (f) 배경 갱신이 `once()` 결과를 **다시 `_cache_write`** 해서,
        `_collect_and_store` 가 "부분이라 안 굽는다"고 거부한 스냅샷을 그대로
        구웠다(#280 에서 넣은 가드가 우회됐다). 쓰기는 **단일 출처**(#38).
    (g) 배선 테스트가 **자기가 배선을 수행**하고 그 호출을 세어, 호출부의
        `timer=` 를 지워도 통과했다(#141 의 변종) — E2E 로 대체(#222).

### 실수 #282

282. **갈래를 맞게 짚어도 사유 원문을 안 실으면 라운드가 하나 더 든다**
    (2026-09-05 VM 실측, #281 의 다음 층 — 그리고 #281 이 실제로 일했다):
    새 판정이 `↪ 타이머는 발화했지만(마지막 Fri 2026-09-04 19:00:00 KST)
    서비스가 실패했다(Result=exit-code exit=1)` 를 냈다. **옛 판이었으면
    '점검 도장이 없다 — 타이머가 아예 안 돌았을 수 있다'** 로 운영자를
    타이머 설치 확인으로 보냈을 자리다(도장 부재는 mark 코드가 그 발화보다
    2시간 28분 뒤에 배포된 탓이라 무관한 사실이었다). systemd 에 물은 것이
    갈래를 바로잡았다.
    ⚠️ 그런데 거기서 끝나 **사유는 `journalctl` 로 사람이 따로 봐야 했다** —
    반복 확인은 제품에 심는다(#252·Automation-first). ⑦ 섹션이 저널 꼬리를
    직접 싣는다: 사유로 읽히는 줄만 고르되 **하나도 없으면 걸렀다고 하지 말고
    꼬리를 그대로** 준다(#187b 틀린 사유가 헛걸음을 만든다).
    ⚠️ 저널을 화면에 실을 땐 **비밀값 마스킹이 한 세트**다(§Secrets) —
    httpx 로거 억제(2026-05-29)는 *이 프로세스* 얘기이고 저널엔 옛 줄·다른
    유닛 줄이 남는다. 값은 안 찍고 자리만 남긴다(`redact`).
    ⚠️ 그리고 `journalctl` 은 빈 결과에 **`-- No entries --` 배너**를 준다 —
    그걸 내용으로 세면 '읽었다'가 거짓이 된다(#54). 권한 부족(저널 그룹 밖)도
    별도 갈래로 말한다(#82).
    ⚠️ **그 ⑦ 이 바로 다음 날 갈래를 뭉뚱그렸다**(2026-09-06): 09-04 줄이
    저널에서 사라지자 `저널에 이 유닛의 줄이 없다(로테이션·권한 확인)` 만
    찍었다 — 처방이 정반대인 둘(회전=손쓸 것 없음 / 권한=그룹 추가)을 한
    괄호에 넣은 것이다(#82 를 그 항목을 쓰면서 어겼다). 갈래는 **잴 수
    있다**: 유닛 없이 한 줄이라도 읽히면 권한은 있는 것이므로 이 유닛만
    빈 것은 보존기간이다(`journal_readable` 대조군, #143). 전날 같은 코드가
    그 저널을 읽었다는 사실이 곧 반증이었다.
    ⚠️⚠️ **그 대조군을 처음엔 엉뚱한 저널에 물었다**(독립 리뷰가 잡았다):
    `-u` 없이 `journalctl -n 1` 은 호출자 **자기 사용자 저널**이라
    systemd-journal 그룹 밖에서도 읽힌다 — 진짜 권한 문제에 '권한 문제
    아님' 을 찍었을 것이다(고치려던 그 오진을 새로 만든 셈, #146). 유닛이
    사는 **시스템 저널**(`--system`)을 겨냥해야 대조군이 성립한다.
    일반화: 대조군을 둘 땐 "이게 **그 대상**을 재나"를 먼저 답할 것(#91b).
    ⚠️ 같이 잡힌 둘: (a) rc=0 인데 줄이 0건인 것은 '비었다' 와 '못 읽는다'
    를 구별 못 하는데 그걸 **권한 확정**으로 매겼다 — 멀쩡히 빈 저널에 대고
    그룹 추가를 시킨다(확정 갈래는 원천이 권한이라고 말할 때뿐, #165).
    (b) 회전으로 **확정**된 자리에 "직접 볼 것"을 그대로 붙여 화면이 스스로를
    뒤집었다(같은 명령은 아무것도 안 보여준다) — 갈래를 계산해 놓고 표시에
    안 쓰면 #123·#129·#189 와 같은 실수다. 갈래(`journal_empty_kind`)를
    반환값에 실어 화면이 그걸 읽게 했다.
    ⚠️ 실패 줄엔 **서비스의 마지막 실행 시각**을 같이 적는다 — 타이머 발화와
    서비스 실행은 갈릴 수 있고(손으로 `systemctl start`), 저널을 맞춰 보려면
    그 시각이 필요하다(#114 감사는 무엇을 보고 말하는지 밝힐 것). 그리고
    ⑦ 은 **실패했을 때만** 붙인다 — 늘 뜨는 섹션은 아무것도 안 재는 것과
    같다(#25·#260).

### 실수 #283

283. **재료를 재는 진단은 '이제 되나'에 답하지 못한다 — 층이 다르다**
    (2026-09-05, #282 의 다음 층): ⑦ 저널이 09-04 19:06 실패를 원문으로
    확정했다(`KRX 로그인 실패: 자격 증명을 확인하세요` · `패스워드 변경
    필요` ×3 — 9일 공백의 원인이 이걸로 끝났다). 자격증명은 고쳐졌고 ④ 는
    **실호출**로 ✅ 다. 그런데 그건 **재료**까지고, 생성(LLM)·푸시 층은
    08-27 이후 한 번도 안 돌았다 — 그래서 "제대로 오는거야?"에 세 번째로
    '다음 거래일 저녁에 보세요' 라고 답할 뻔했다(#79 그 경로가 실제로
    실행됐나를 먼저 답할 것).
    대응: `--dry-run` — **푸시 없이** `generate()` 만 태운다. 거래일 게이트를
    일부러 건너뛰되 **건너뛴다고 말하고**(#43), 성공하면 비용·소요·본문
    길이를, 실패하면 None/예외를 **이름을 대서** 말한다(#12 예외를 삼키면
    다시 못 묻는다). 실패는 rc=1(#54).
    ⚠️ LLM 을 실제로 부르므로 **요금이 든다** — 그래서 자동 실행이 아니라
    사람이 부르는 플래그이고, 부르기 전에 그 사실을 찍는다(에이전트가 임의
    과금 유발 금지, §개발도구 Skill_Seekers 항의 원칙).
    ⚠️ 플래그가 둘이면 **순서가 계약**이다 — `--why`(읽기 전용·무료)가 먼저다.
    일반화: 파이프라인 진단을 만들 땐 "이게 **어느 층**까지 재나"를 먼저
    답하고, 답이 '재료까지'면 산출 층을 재는 짝을 같이 둘 것.
    ⚠️⚠️ **그리고 그 dry-run 이 운영 상태를 바꿨다**(같은 날 VM 실측):
    출력에 `dashboard: daily_byte.html regenerated (155 runs)` 가 찍혔다 —
    `generate()` 주석이 스스로 "push 와 무관하게 **항상 기록**" 이라고 적어
    뒀는데 내가 그걸 안 읽고 '푸시만 안 하면 read-only' 라고 봤다. 결과:
    텔레그램엔 안 간 브리프가 화면엔 있고, `--why` ⑤ 가 '마지막 기록
    2026-09-04' 로 **공백이 메워진 것처럼** 보고하게 됐다 — 진단이 **자기가
    읽을 신호를 오염**시킨 것이다(#30 의 진단판 · #264). 대응: `generate(*,
    archive=True)` 게이트로 진단은 아카이브도 인포그래픽 파일도 남기지 않고
    (임시 경로로 렌더해 '되는지'만 증명), **무엇을 안 쓰는지 화면이 말한다**.
    회귀는 AST 로 "저장 호출이 `if archive:` 안에 있고 그 밖엔 없다"(#59b
    주석이 대신 만족시키지 않게) + **기본값은 True**(정기 실행이 화면을
    채우는 경로라 그게 꺼지면 대시보드가 통째로 빈다).
    일반화: 기존 함수를 진단에 재사용할 땐 **그 함수가 무엇을 쓰는지 본문을
    읽고** 시작할 것 — '푸시 안 함'과 '아무것도 안 씀'은 다르다.

### 실수 #284

284. **'아무것도 안 쓴다'고 적어 놓고 비용은 쓰고 있었다**(2026-09-06, #283 의
    바로 다음 라운드): `--dry-run` 배너에 "아카이브·대시보드에도 **안 씁니다**"
    라고 적었는데, `generate()` 는 `_log_daily_byte_usage()` 로 실제 과금된
    비용을 daily_byte_usage.jsonl·usage.jsonl 에 남기고 **그 두 로그가 곧
    대시보드 비용 카드**다(§Help/Dashboard 비용합산). 동작은 옳다 — dry-run 은
    진짜 돈을 쓰므로(실측 ₩54.7) 장부에서 빼면 지출이 조용히 사라진다. 틀린 건
    **문구**이고, 그건 #283 을 고치며 내가 같은 커밋에서 적은 것이다(#55 설명이
    코드와 어긋나면 버그). 일반화: '안 쓴다'는 주장은 **무엇을 안 쓰는지**까지
    적어야 참이 된다 — 함수가 남기는 것을 전부 세어 보고 쓸 것(#283 의
    "그 함수가 무엇을 쓰는지 본문을 읽고" 가 한 층 더 있었다).
    ⚠️ 그리고 이 오염의 뒷정리는 화면이 이미 갖고 있었다 — 감사가 그 레코드를
    `ts 날짜 ≠ 폴더 날짜` ❌ 로 잡아냈고(폴더 09-04 · ts 09-05 = dry-run 이
    쓴 시각), 카드엔 🗑️ 삭제 API 가 이미 배선돼 있었다. **진단이 남긴
    흔적은 감사가 잡는다** — 그러라고 만든 축이 실제로 일했다(#87a).

### 실수 #285

285. **여러 계열을 한 'as-of' 칸에 섞으면 한쪽의 침묵이 다른 쪽 기록에 가려진다**
    (2026-09-06 Daily Byte): 페이지의 `마지막 브리프 (KST)` 가
    `_feed_latest_ts(runs)` 라 KR·US·Weekly 를 통틀어 **최신 하나**만 찍었다.
    KR 이 08-27 에 멈춘 열흘 동안 그 칸은 미국 09-04 를 띄웠고, 화면은
    '이거 최신이야?' 에 답하지 못했다 — 사용자가 나한테 물어야 알았다
    (#43·#52 조용한 것과 죽은 것). 바로 옆 `Weekly 종합` 은 `kind` 로 세는데
    이 칸만 안 갈랐다(#45 같은 화면의 두 칸이 다른 모집단).
    대응: 🇰🇷/🇺🇸 를 나눠 찍고, 갈래는 이름 열거가 아니라 **접두**
    (`us_` = `_daily_byte_is_us`)로 본다 — 목록을 열거하면 새 미국 kind 가
    조용히 한국 칸에 앉는다(#24). 두 갈래가 **배타·전수**라 합이 총 건수와
    같다는 것을 회귀로 고정했다.
    ⚠️ 라벨을 바꾸면 **그 라벨로 재던 계약**이 깨진다 — 전 피드 공용 회귀가
    `startswith("마지막 브리프 (KST)")` 라 국기 접두에 무너졌다(#19·#273
    "훅·라벨을 바꿀 땐 감사·회귀의 계수 패턴도 같은 커밋에서"). 계약은
    '기준시각을 말한다' 이지 '그 문자열로 시작한다' 가 아니므로 포함으로
    다시 쓰고, Daily Byte 는 국가 칸이 둘인지도 같이 본다(#222).
    ⚠️ 그 회귀에서 배운 것: 국기 이모지는 **regional indicator 두 코드포인트**
    라 `label[0]` 로 자르면 반쪽(`'🇰'`)이 나온다 — 실측으로 발각됐다.

### 실수 #286

_지시서가 자기 자신에 대해 거짓을 말한다 (2026-09-06)_

> CLAUDE.md §⛔ 과거 실수 #286 의 서사 전문. 규칙 절은 CLAUDE.md 에 남아 있다.

```
286. **지시서가 자기 자신에 대해 사실이 아닌 것을 말할 수 있다 — 그리고 그 거짓이
    가드를 건너뛰게 한다**(2026-09-06 지시서 감사, 8축 44건 → 적대적 검증 통과 9건):
    (a) #59 가 "AST 로 top-level def 중복을 **전수** 검사" 라고 적어 놨는데 실제 가드는
        `bot/dart_production.py` **한 파일**을 리터럴로 열고 있었다 — #24 와 같은 어휘라
        "레포 전체가 보호된다"로 읽혀 바로 뒤의 수동 `grep` 을 건너뛰게 만든다. 전수로
        넓히자 **즉시 둘**을 잡았다(`chart_data._LiteSkip` 이 두 번, `archive_template.
        _KST` 가 두 번 — #87a 새 가드는 켜자마자 뭔가 잡는 게 정상).
    (b) '완료' 정의가 `§Pre-commit **1~8** 전부` 였는데 항목은 1~9다(9번 '재현 테스트
        먼저'가 나중에 추가됐다) — **번호로 가리키면 목록이 자라도 안 따라온다**.
        범위는 번호가 아니라 이름·전칭으로 적을 것.
    (c) 게이트를 여는 낱말("라이브 장애")의 정의가 **주입되지 않는 파일**(REFERENCE)에만
        있었고, 두 지시서가 그 낱말에 서로 다른 면제를 걸고 있었다. 게이트 낱말은
        **읽히는 곳에** 정의를 두고 형제 문서와 대조할 것.
    (d) "MCP 불가 시 base 직접 push" 처럼 **자가 선언으로 열리는 예외**는 실측 증거를
        요구할 것(#25·#79) — 시도 없이 '불가' 로 판단하면 리뷰 없는 배포가 열린다.
    ⚠️ 그리고 손으로 센 서수가 실제로 어긋나 있었다(#195·#228 이 둘 다 '다섯', #216·
    #233 이 둘 다 '일곱') — 계수는 **§주제 색인**이 하고, "한 계열을 3개 이상 인용하는
    새 항목은 그 계열에 등재된다" 는 규칙을 회귀가 강제한다(#24 "이 목록은 누가
    갱신하나" 의 답 = 사람이 아니라 테스트, #119). 켜자마자 내가 손으로 쓴 색인에서
    **네 건**이 빠져 있었다.
    ⚠️ 그 규칙이 **이 항목(#286) 자신을 오탐**했다 — 캐시 계열 셋을 다른 이유로
    인용했을 뿐인데 계열원으로 봤다. 문턱을 올리거나 '`·` 나열' 로 좁히면 오탐은
    사라지지만 **원래 잡던 것도 같이 죽는다**(실측, #146·#275) → 감도는 두고 예외만
    명시(#24). 그리고 서사를 접자 인용 나열이 사라져 **규칙 감도가 같이 떨어졌다** —
    압축이 그 압축을 감시하는 신호를 깎을 수 있다. 이 가드는 **추가 방향만** 잡고
    색인에서 빼는 방향은 못 잡는다(#274 못 보는 축을 밝힐 것).
    ⚠️⚠️ **접기는 실제로 규칙을 잃었다**(2026-09-06 재검토): 서사만 접었다고 보고했고
    독립 리뷰의 토큰 중첩 대조도 통과했는데, 접기 전 원문에서 **명령형 절만 뽑아**
    다시 재니 #273('규칙을 다른 번들로 옮길 땐 지금 쓰는 곳이 전부 그 번들인가')·
    #277('실시간 틱이 오면 열려 있는 상세도 같이 갱신')이 통째로 빠져 있었다
    (#274·#275 도 한 절씩). 넷 다 복원했다. 교훈: **압축의 검수는 "무엇이 남았나"가
    아니라 "무엇이 사라졌나"를 원문에서 뽑아 물어야 한다** — 남은 글만 읽으면 잘
    읽히고, 그래서 잃은 걸 못 본다(#287 의 문장판).
    ⚠️ 다만 그 대조는 **자동 게이트로 못 쓴다** — 실측 오탐 7/11(규칙이 다른 낱말로
    살아 있으면 토큰이 안 겹친다). 늘 뜨는 가드는 아무것도 안 재는 것과 같으므로
    (#25·#260) 회귀로 만들지 않고 **접는 그 턴의 수동 점검**으로 둔다. 못박을 수
    없는 것은 못박지 않는다(규칙은 늘릴수록 약해진다).
    ⚠️ **요약본은 원본이 자라는 것을 모른다**(같은 감사의 (e) 회수분): Copilot 이
    자동 주입받는 유일한 문서는 `.github/copilot-instructions.md` 인데(378KB 짜리
    CLAUDE.md 는 자동 로드 안 됨) 거기 실수 발췌는 초기 항목에서 멈춰 있었고,
    스킬은 헤더에 `mirrors CLAUDE.md "⛔ 과거 실수"` 라고 **완결성을 주장**하고
    있었다 — 읽는 쪽은 거기 없는 실패모드를 **없는 것으로** 읽는다. 처방은 복제가
    아니다(그러면 예산 문제를 요약본 쪽에 새로 만든다): ① **발췌라고 밝히고**
    출처를 가리킬 것 ② **충돌하면 누가 이기는지** 적을 것(요약이 더 느슨해 보이면
    낡은 것이지 예외가 생긴 게 아니다 — 실제로 copilot 이 CLAUDE.md 에 없는 예외를
    열고 있었다) ③ 커버리지는 **원본 헤딩에서 파생해 대조**할 것(CLAUDE.md 가
    스스로 '⛔/의무'로 표시한 섹션 = 매니페스트, 새 섹션이 생기면 테스트가 잡는다).
    ⚠️⚠️ 그리고 독립 리뷰가 **내가 같은 턴에 쓴 '작동 확인' 테스트 둘이 전부
    tautology** 임을 실측으로 보였다 — 감시 대상을 부르지 않고 판정을 **인라인으로
    재구현**해서, 스캔 본문을 `return 999, []` 로 바꾸는 뮤테이션이 0.01초에
    통과했다. 독스트링은 "검사가 눈이 멀었는지 이 테스트가 가른다" 고 약속하고
    있었다. **fires 테스트는 반드시 제품 함수를 태울 것**(#19·#91b). 같이 잡힌 것:
    allowlist 는 항목을 넣는 것만으로 가드를 무음으로 만드므로 **크기를 단언**해
    늘리려면 테스트도 고치게 할 것(#24·#119) · allowlist 키를 **산문 라벨**로 두면
    라벨을 다듬는 순간 오탐이 살아나 **틀린 수정**을 지시한다(#19·#200) · 포인터
    가드는 **양방향**이어야 한다(한 방향만 보면 포인터를 지웠을 때 서사가 고아가
    된다) · **크기 상한을 현재값에 바싹 붙이면 안 된다**(여유 3일 = 며칠 뒤 무관한
    커밋을 막는 시한폭탄, #67·#275 — 증가율을 재서 창으로 잡을 것).
    ⚠️ 감사 하네스도 같은 병에 걸렸다: `ok.length > 0 && ok.every(v => !v.refuted)` 가
    검증자가 **전부 죽은** 항목(0표)을 '반증됨' 으로 세, 세션 한도로 끊긴 실행이
    "37건 반증" 으로 위장했다 — **대조 0건은 통과도 반증도 아니다**(#54 의 반대 방향).
    판정은 verified/refuted/**unjudged** 세 상태로 가를 것.
```

### 실수 #287

287. **압축이 한정어를 떨어뜨리면 이미 대체된 규칙이 살아남는다 — 78일 동안**
    (2026-09-06 지시서 감사 ⑧, 사용자 승인 후 복원): `§Pre-commit 5` 가
    `항목별 검증 후 다음("OK/다음" 전 batch 금지)` 였는데, 원문
    (`CLAUDE_REFERENCE.md` §5)에는 한정어가 **둘** 있었다 — `or no objection
    arrives`(침묵이면 진행)와 `when the user asked for sequential validation`
    (사용자가 순차 검증을 요청했을 때만). 2026-06-20 압축 커밋(3047→128줄)이
    둘 다 떨어뜨려, **사용자가 8일 전(2026-06-12)에 배치 적재로 이미 대체한**
    review-first 가 무조건 정지 규칙으로 남았다. #101("'안 건드린 것 둘' 은
    보고가 아니라 미이행")이 그 정지가 만든 사고 기록이다.
    ⚠️ 판정 근거는 **git 이력**이었다 — `git log -S'OK/다음'` 이 그 줄을 딱
    한 번(압축 커밋) 보여 줬고 이후 재확인이 없었다. "사용자가 정한 정지인가"는
    문구를 읽어서가 아니라 **언제 누가 넣었는지를 재서** 답할 것(#12·#165).
    보조 신호: 이 파일은 사용자가 정한 정지에 예외 없이 **날짜 귀속**을 붙이는데
    그 항목에만 없었다.
    ⚠️ 그래서 **압축할 때 조건절·예외절을 먼저 세라**. 규칙에서 "~할 때만"·
    "~아니면"이 빠지면 남은 문장은 더 강해지고, 강해진 규칙은 나중 정책과
    조용히 충돌한다(#200 라벨이 자라면 동등 비교가 깨지는 것의 반대 방향 —
    여기선 라벨이 **줄어서** 범위가 넓어졌다).
    ⚠️ 그리고 대체된 원문을 아카이브에 그대로 두면 다음 사람이 되살린다 —
    `CLAUDE_REFERENCE.md` 쪽에 **대체됨 + 현행 위치**를 박았다(#55).

### 실수 #288

288. **화면이 산식을 좁혔는데 감사는 옛 산식을 그대로 적고 있었다**
    (2026-09-07 대시보드 감사 ❌ 098070.KQ 4분기 전부): `_attach_fcf` 는 #215 로
    CAPEX 를 **유형자산취득만**으로 좁혔는데 `fcf_audit.recompute_dart` 는
    `유형+무형` 을 그대로 더해, 정상 종목을 매일 ❌ 로 찍었다 — 차이가 정확히
    그 분기 무형자산취득이었다. 감사가 판정을 **재계산**하면 제품과 다른
    기준선을 비교한다(#169·#35). 산식은 `bot.fcf.dart_capex` **한 곳**에 두고
    화면·감사가 둘 다 부른다(#38).
    ⚠️ 규칙을 좁히는 커밋(#215)에서 **같은 산식을 적은 곳을 즉시 grep** 했어야
    한다 — 화면만 고치면 감사가 그날부터 거짓 경보를 낸다(#147).
    ⚠️ 배선 회귀를 소스 문자열로 쓰면 이 항목을 설명하는 **독스트링이 대신
    만족**시킨다(#59b) — AST 로 본문만 보고, `dart_capex` 호출까지 셀 것.

### 실수 #289

289. **요약 줄이 판정 글자를 다시 쓰면 같은 결함이 두 번 세어지고, 하필
    엉뚱한 축이 붙는다**(2026-09-07 결산): 098070.KQ 는 결함이 ① 재계산
    하나뿐인데 `[② 교차출처(DART ↔ yfinance)] 판정: ❌ 불일치 1건` 이 따로
    떴다 — 종목별 요약이 ❌ 를 재사용하고, `audit_sweep._findings` 는 **바로
    위 섹션 제목**을 붙이므로 멀쩡한 ② 가 지목됐다. #250(판정 글자는 판정에만)
    ·#268(요약은 세기만)을 `fcf_audit` 에는 적용하지 않았던 것(#38·#147).
    ⚠️ **설명 문구에 써도 세어진다** — 첫 fix 가 `불일치 1건 — 위 ❌ 줄 참조`
    였고 sweep 은 줄에 그 글자가 있으면 결함으로 센다. 회귀가 잡았다(#91b
    재는 대상이 맞나). 글리프는 **한 자도** 안 쓴다.

### 실수 #291

291. **거짓 ❌ 를 없앤 fix 가 그 자리를 거짓 ✅ 로 채웠다**(2026-09-07, #288 을
    배포하기 전 독립 리뷰가 잡았다): 감사 ①(DART 재계산)은 `get_quarterly_series`
    가 **반환 직전 `_attach_fcf` 로 채운 그 dict** 를 같은 재료·같은 식으로 다시
    계산해 대조한다 — 산식을 하나로 모으자 **구조상 영원히 일치**가 됐다(리뷰
    실측 2,000회 불일치 0건). 늘 ✅ 인 축은 판정이 아니라 거짓 안심이다(#54·#41).
    더 나쁜 건 내가 같은 커밋에서 "이 축은 tautology 가 아니다 — 캐시·차분
    결함을 여전히 잡는다" 는 **틀린 독스트링**을 적은 것이다(#55·#286 지시서가
    자기 자신에 대해 사실이 아닌 것을 말한다). 그 자리를 실제 실패모드(누적
    오염 #96)로 바꾸고, tautology 라는 사실 자체를 **재는 회귀**로 못박았다.
    ⚠️ 일반화: 두 경로를 **단일 출처로 합치면** 그 둘을 대조하던 검사는 죽는다
    — 합치기 전에 "이 대조가 여전히 무언가를 재나"를 먼저 답할 것.
    ⚠️ **원천이 예외를 재포장하면 갈래 분류가 통째로 도달 불가**다(#20·#141):
    `customs._http_get` 이 HTTPError 를 `CustomsAPIError(...) from exc` 로 감싸
    429·403·5xx 가 전부 '원천응답' 한 통에 담겼다 — 내 테스트는 `HTTPError` 를
    **직접 만들어** 재서 그 사각을 못 봤다(#155 픽스처는 원천이 실제로 보내는
    모양대로). `__cause__` 를 풀고, 회귀도 제품이 만드는 그 예외를 태운다.
    ⚠️ 그리고 **형제 화면 셋째**(`fcf_probe`)가 옛 산식에 남아, 제품이 정상적
    으로 비운 기간을 '배선 결함' 이라 지목하고 있었다 — 거짓 경보는 다음
    라운드를 없는 버그 사냥으로 보낸다(#38·#147·#109).
    ⚠️ 배선 회귀가 없으면 **헬퍼 테스트 37개가 전부 green 인 채** `main()` 이
    옛 인라인 문구로 되돌아간다(리뷰 실측). 같은 파일의 형제(`mark_finding`)는
    #268 에서 이미 AST 로 호출을 못박아 뒀다 — 새 가드는 **형제가 어떻게
    지켜지는지 보고 같은 수준으로** 맞출 것.
    ⚠️ 테스트에 **날짜를 리터럴로 박지 말 것** — 원장 prune(14일)이 그 키를
    지워 며칠 뒤 무관한 커밋에서 빨간불이 된다(#249·#286 시한폭탄).

### 실수 #295

295. **백업을 만드는 코드가 백업을 파괴했다 — 재실행이 no-op 이 아니어서**
    (2026-09-07 관심종목 저장일 정리 CLI, 독립 리뷰가 배포 전에 잡았다):
    `--apply` 는 쓰기 전에 `market_favorites.backup-{초}.json` 을 남기는데
    (a) 바꿀 게 없어도 백업하고 다시 쓰고 (b) 파일명이 **초 해상도**라
    `shutil.copy2` 가 말없이 덮는다. 그래서 위쪽 화살표+Enter 로 두 번 치면
    유일한 백업이 **이미 정렬된 것**으로 갈리고, 화면이 인쇄한 `되돌리려면 cp`
    가 정렬본을 가리켜 복구가 no-op 이 된다 — 사용자가 ↕ 로 맞춘 순서가 영구
    유실이다. 파괴적 1회성 도구는 **바뀔 게 없으면 아무것도 쓰지 않는 것**이
    가장 강한 방어다(그러면 덮어쓰기 경로 자체가 도달 불가). `moved` 를 이미
    세어 놓고 판정에 안 쓴 것이 원인이다(#123·#129·#189·#228 계열).
    ⚠️ 그리고 화면이 **재지 않은 상한**을 적었다 — "3분 캐시라 최대 3분" 인데
    `get_favorites_with_prices` 는 TTL 만료 뒤 **스테일을 즉시 주고** 백그라운드
    로만 갱신한다(리뷰 실측: 3분 뒤에도 옛 순서). 그 문구를 믿은 운영자는
    "안 먹었다"며 재실행해 위 (b)를 밟는다 — **틀린 안내가 데이터 유실의
    방아쇠**였다(#165 재지 않은 것을 단정하지 말 것).
    ⚠️ 쓰기 뒤엔 **되읽어 확인**할 것 — 같은 파일을 `_load`→`_save` 하는 다른
    경로(대시보드 name_kr 백필)와 겹치면 조용히 되덮인다(#79).
    ⚠️ `len(new) != len(cur)` 는 `sorted()` 가 길이를 안 바꾸므로 **영원히
    통과하는 죽은 가드**였다(#291) — 티커 다중집합으로 재야 실제로 잰다.
    ⚠️ 그리고 그 CLI 를 프로세스로 태우는 회귀의 단언 `"NEW" in stdout` 을
    **바로 위 `[현재 위 3]` 줄이 대신 만족**시켜, 정렬을 아예 안 하는 뮤테이션이
    통과했다(#75 그대로 — 내가 그 함정을 독스트링에 적어 놓고 다음 줄에서
    밟았다). 표면별 단언은 **그 줄 하나를 잘라내서** 볼 것(#55).

### 실수 #297

_`or 0` 이 판정을 뒤집는다 (2026-09-07)_

> CLAUDE.md §⛔ 과거 실수 #297 의 서사 전문.

```
297. **결측값을 `or 0` 으로 읽어 판정 조건이 조용히 면제됐다 — 그것도 그
    지표의 정의였다**(2026-09-07 시장타이밍 FTD, 사용자 "분산일·FTD 가 제대로
    입력되는지 꼼꼼히 봐줘"): `vol_up = (row.volume or 0) > (prev_volume or 0)`
    이라 **직전 봉의 거래량이 없으면 0 으로 읽혀** 어떤 거래량이든 '증가' 가
    됐다. 거래량 증가는 팔로우스루데이의 **정의**인데 그게 면제된 것이다 —
    실측으로 거래량이 1000→500 으로 **줄었는데도** `FTD_CONFIRMED · 품질점수
    80`(최고 등급)이 나왔다. `or 0` 은 '없음'을 '0'으로 바꾼다(#235·#242 의
    비교판 — 그 둘은 표시가 0 이 됐고, 여기선 **판정이 뒤집혔다**).
    ⚠️ 재료가 없으면 조건을 **통과시키지도 탈락시키지도 말고 셀 것** —
    `vol_unknown_days`·`unjudged` 로 몇 세션을 판정 못 했는지 남기고 화면이
    말한다(#54 대조 0건은 통과가 아니다 · #43).
    ⚠️ 원인은 **우리 폴백이 만든 구멍**이었다: 야후가 하루 늦으면 `_quote_tail`
    이 네이버 종가를 `volume: None` 으로 붙인다. 그 봉 하나가 자기 세션뿐
    아니라 **다음 세션의 판정까지** 막는다(전일 거래량이 없으므로) — 폴백을
    넣을 땐 그 폴백이 **채우지 못하는 칸**이 무엇을 망가뜨리는지 물을 것(#42a
    폴백은 버그를 숨긴다).
    ⚠️ 같이 잡은 표시 결함 셋: (a) `🟢 FTD 확정` 을 **날짜 없이** 띄웠다 —
    탐색 창이 41봉이라 두 달 전 신호일 수 있는데 오늘 일처럼 읽힌다. `ftd_date`
    는 계산해 놓고 렌더가 안 썼다(#123·#129·#189·#228 계열) (b) `🟢 FTD 확정`
    과 `위험도 HIGH` 가 나란히 뜨는데 설명이 없었다 → **FTD 이후 분산일 개수**
    만 사실로 적는다(몇 개부터 '압박' 인지는 원본이 안 정한다 — 판정을 대신
    내리지 않는다, #165) (c) `품질점수` 가 **O'Neil 원본에 없는 우리 자체
    산식**인데 가이드에 한 줄도 없었다(#32·#55) → 산식을 적었다.
    ⚠️ 그리고 TW·CN_A·HK 는 지수가 아니라 **ETF**(0050.TW·510300.SS·2800.HK)라
    거래량이 그 펀드 것이다 — IBD 분산일의 '기관 매도' 해석이 성립하지 않는데
    US·KR·JP 와 같은 카드에 같은 라벨로 떴다(#34 라벨에 기준을 박을 것).
    ⚠️ 뮤테이션에서: 상태 가드(`state != FTD_CONFIRMED`)를 지우는 변형이
    통과했다 — 픽스처가 `ftd_date` **없는** dict 라 날짜 가드가 대신 만족시켰다
    (#91c 깨지는 값까지 밀어 볼 것). 그리고 렌더 배선은 헬퍼 테스트가 못 잡아
    별도 렌더 테스트가 필요했다(#20).
```

### 실수 #292

_판정 축을 버리면 요약이 추측을 부른다 (2026-09-07)_

> CLAUDE.md §⛔ 과거 실수 #292 의 서사 전문.

```
292. **판정을 세면서 '어느 축인지'를 버리면 요약이 추측을 부른다 — 그리고
    그걸 검사한 내 셀프리뷰가 동어반복이었다**(2026-09-07 "② 교차출처 ❌
    불일치 1건도 확인해줘"): ② 는 멀쩡했다(❌ 는 ① 재계산의 재진술, #289 —
    형식으로 확정: `화면 … ≠ 재계산 …` 은 DART ① 전용이고 ② 는
    `DART …억 vs yfinance …억 ❌ 차이 N%` 라는 다른 줄을 찍는다). 진짜로
    답이 없던 건 `(판정불가 1건)` 이었다 — `flag()` 가 카운터만 올리고 축
    이름을 버려, 어느 축이 ❓ 였는지 전체 로그를 열어야 했다(#82 갈래는
    이름으로 · #123·#129·#189·#228 계산해 둔 판정을 표시까지 배선).
    13개 호출 전부에 축을 달고 요약이 `판정불가 3건 — ②교차출처(분기)×2 ·
    ④검산` 이라고 말하게 했다.
    ⚠️ **틀린 라벨은 라벨이 없는 것보다 나쁘다** — DART 클라이언트가 아예
    없는 조기 반환에 `③payload` 를 달아, 운영자를 `get_quarterly_series`
    로 보낼 뻔했다(진짜 원인은 자격증명). 라벨을 붙일 땐 **그 자리의 화면
    줄이 말하는 것과 같은지** 하나씩 대조할 것(#187b 틀린 로그가 헛걸음을
    만든다).
    ⚠️⚠️ **새 테스트 5개가 전부 AST 모양 검사라 정작 이 변경의 핵심 배선이
    무가드였다**(독립 리뷰 실측): `flag()` 안의 `append` 두 줄을 지워도 전부
    green — 요약이 다시 `판정불가 1건` 으로 되돌아가는 바로 그 회귀다(#20).
    같은 형태로 **인자 순서 뒤바꿈**(main)·**반환 dict 값 뒤바꿈**도 통과했다
    (둘 다 두 버킷이 서로 뒤집혀 찍히는 '틀린 라벨'). 모양이 아니라 **결과**로
    재는 행동 테스트 하나가 셋을 한꺼번에 잡는다 — 형제(`test_tautology_is_
    real_not_asserted`)가 이미 그렇게 하고 있었는데 새 가드만 수준이 낮았다(#291).
    ⚠️ 그리고 축 이름을 요약에 **삽입**하게 만든 순간, 옛 `verdict_line` 의
    닫힌 리터럴 집합을 덮던 전수 ❌ 가드가 **무력화**됐다 — 300줄 떨어진 13개
    리터럴 중 하나에 ❌ 를 넣으면 #289 가 그대로 재발한다(실측). 값을 바깥에서
    **주입받게 바꾸면 그 값의 가드도 같이 옮겨야** 한다.
    ⚠️⚠️ 그 대조를 자동화한 내 스캐너가 **동어반복**이었다 — 라벨을 찾는
    역탐색 시작점이 `flag` 줄 **자신**이라 라벨이 자기를 매칭해 13개가 전부
    `OK` 로 나왔다. 시작점을 앞줄로 옮기고 `say(` 만 보게 하자 그 1건이
    비로소 드러났다(#286 fires 테스트는 제품 함수를 태울 것 · #91b 재는
    대상이 맞나 — **내 점검 도구에서 같은 날 재발**). 검사를 만들면 "틀린
    상태를 실제로 재현해 ❌ 가 뜨는지" 부터 볼 것(#47).
```

### 실수 #293

293. **'대조 0건은 통과가 아니다' 가드를 한 갈래에만 걸었고, 파 보니 그 축은
    애초에 자기 자신을 대조하고 있었다**(2026-09-07 `fcf_audit` ① yfinance 축):
    VM 실측 098070.KQ 가 `① 재계산 ✅ 전 기간 일치(0건)` — 현금흐름표 8행을
    받았는데 대조 0건인데도 통과로 찍혔다(#54). 2026-08-22 에 같은 축에 넣은
    가드는 `not yq and not ya`(스냅샷이 통째로 빈 경우) **하나만** 막고 있었다.
    ⚠️⚠️ 그런데 그 0건을 ❓ 로 바꾸는 fix 를 독립 리뷰에 걸었더니 **더 깊은
    결함**이 나왔다: 옛 루프는 원천이 직접 준 FCF 행을 **건너뛰고 파생분만**
    다시 계산했는데, 파생분의 `fcf_from_row` 는 정의상 `fcf_from_parts` 그
    자체라 **구조상 영원히 일치**했다(리뷰 실측 4,589행 비교·불일치 0건 ·
    CAPEX 를 흔든 대조군은 3,023건이라 검사가 눈먼 게 아니라 정말 같았다).
    `❌ 불일치` 가지는 **도달 불가능한 죽은 코드**였다. #291 이 **같은 날**
    DART 축에서 지운 tautology 가 yfinance 쌍둥이에 그대로 남아 있었던 것
    — **한 축을 고칠 땐 형제 축이 같은 병을 앓는지 먼저 grep** 할 것(#38·#147).
    대응: 대조 대상을 **원천이 직접 준 FCF ↔ 우리가 만든 `OCF−|CAPEX|`** 로
    바꿨다(입력이 서로 달라 실제로 잰다). 못 대는 행은 사유를 갈래로 적고 ❓.
    ⚠️ 재료가 0인 이유는 갈래가 여럿이고(못 받음 / 원천이 직접 안 줌 / 영업CF
    없음 / CAPEX 없음 / 분모 0) 처방이 다 다르다 — **사유를 적기 전에 그
    사유가 몇 갈래인지부터 셀 것**(#82). 첫 fix 는 둘로만 갈라 "재료가 아예
    없음" 이라 적었는데 CAPEX 만 없는 행도 거기 앉았다(#292 틀린 라벨은 라벨이
    없는 것보다 나쁘다). 문구는 화면 각주와 같은 `bot.fcf.missing_reason`
    에서 가져온다(복제하면 갈라진다, #38).
    ⚠️ **총계는 소계와 같은 리스트에서 파생시킬 것**(#45) — 총계를 `len(yq)
    + len(ya)` 로 따로 세니 연간 행을 빼먹는 변형이 회귀 23개를 **전부
    통과**했다(소계 8건에 총계 5행). 건너뛴 행을 행마다 한 줄씩 모아 그
    길이를 쓰면 갈라질 수가 없다. 픽스처엔 **연간 행을 반드시** 넣을 것(#91c).
    ⚠️ `✅` 문구도 '전 기간' 이라 적으면 건너뛴 행을 뺀 모집단을 전체인 것처럼
    말한다(#45) — 대조한 수와 제외한 수를 같이 적는다.
    ⚠️ ❓ 는 sweep 이 세지 않으므로(❌ 만 결함, ⚠️ 만 사람 확인 — 실측 확인)
    이 정직함은 알림 노이즈를 하나도 안 늘린다. **정직하게 만들면 시끄러워
    지나**를 먼저 재고 판단할 것(#25·#260).
    ⚠️ 회귀엔 **반대 증거**를 같이 둔다 — 잴 수 있는 행이 여전히 ✅ 와 건수를
    말하는지, 그리고 **실제로 어긋나면 ❌ 가 뜨는지**를 안 보면 축을 통째로
    ❓ 로 바꾸는 변형이 통과한다(#25·#47).
    ⚠️ 그리고 이 결함은 **감사의 ❌ 만 보고 끝냈으면 안 보였다** — 같은
    실행의 ✅ 줄을 읽다가 나왔다(#187 로그를 훑을 것의 판정줄판).

### 실수 #294

294. **'미설정' 이라고만 적은 경고가 바로 다음 줄의 '로그인 완료' 와 모순됐다**
    (2026-09-07 VM `fcf_audit` 로그, 사용자 "KRX 로그인 모순도 env_why 붙여서
    고쳐줘"): `pykrx: KRX_ID/KRX_PW 미설정` 바로 뒤에 라이브러리가 `KRX 로그인
    완료` 를 stdout 으로 찍었다 — 둘 중 무엇이 맞는지 출력만으론 못 가르고,
    갈래마다 처방이 **완전히 다르다**(파일 못 찾음=cwd / 키 없음=.env 추가 /
    값 비었음=값 확인 / dotenv 미설치=설치). '없음' 만 말하는 진단은 추측을
    부른다(#82·#279) → `env_diag(*names)` 가 **채워진 키는 빼고** 없는 키만
    갈래로 적는다(값은 안 찍고 길이까지만, §Secrets).
    ⚠️ **VM 모순의 원인은 아직 안 쟀다 — 그러니 단정하지 않는다**(#12·#165).
    후보 둘: (a) `env_key` 가 첫 조회 실패를 `_TRIED` 에 넣고 다시 안 읽어
    그 뒤 `.env` 가 읽히면 경고만 남는 경우 (b) 지연 `load_dotenv(~/stock/
    .env)`(daily_kr_weekly·rone_client·dashboard_server 등)가 **once-only**
    경고 발화 **뒤에** 도는 경우 — pykrx 는 `os.environ` 으로 인증하고
    `env_key` 는 성공했을 때만 주입하므로 (b)가 더 그럴듯하다. `env_diag` 는
    원인을 말하는 게 아니라 **지금 다시 읽어 재서** 갈래를 댄다("지금 다시
    읽으면 있다 — 첫 조회가 실패해 캐시됨 / 이 프로세스에서 아직 조회 전").
    '미설정' 이라고만 적으면 운영자가 이미 있는 키를 넣으러 간다(#187b).
    ⚠️ **같은 스캔이 두 곳에 복제돼 있었다** — `env_key`·`env_why` 가 각각
    `.env` 두 경로를 도는 루프를 갖고 있어 한쪽만 고치면 두 자리가 같은
    상태를 다르게 말한다(#38). `_dotenv_lookup` 하나로 합치고 회귀가
    **AST 로 호출 1회**를 못박는다(리터럴 `"dotenv_values(p)"` 를 세면 주석
    한 줄에 빨간불이고 변수명만 바꾼 복제는 통과한다, #19·#59b).
    ⚠️ 호출부가 사유를 **문자열에서 냄새 맡게 하지 말 것** — 오류 갈래는
    반환값에 구조로 실어야 문구를 다듬어도 `env_key` 의 silent-fail 로그가
    안 죽는다(#19·#12).
    ⚠️ 그 '스캔은 하나뿐' 회귀가 처음 빨간불을 냈을 때 **내 계수 패턴이
    틀린 줄 알았다** — 실제로는 코드가 정말 중복이었다(#47 의 반대 방향:
    가드가 맞고 코드가 틀린 경우도 있다). 오탐이라 부르기 전에 **무엇이
    두 번째로 잡혔는지** 볼 것.
    ⚠️⚠️ **함수를 합칠 땐 반환 조건부터 대조할 것** — 옛 `env_key` 는 값이
    falsy 면 다음 `.env` 경로로 계속 돌았는데, 공용 스캔은 '키는 있으나 값이
    비었다' 에서 **즉시 반환**하게 짜여 뒤 경로의 진짜 값을 못 볼 뻔했다
    (배포전 셀프리뷰가 잡음, §Pre-commit 7a). 합치기는 '같은 일을 한 곳에서'
    지 '같은 결과' 가 아니다 — 조건·순서·조기반환을 하나씩 맞춰 볼 것.
    ⚠️ 배선은 **결과로** 본다 — `krx_login_ready` 가 `env_diag` 를 부르는지
    AST 로 재면 게이트만 꺼도 통과한다(#141·#292). 실제 로그 레코드에 갈래가
    실리는지 caplog 로 확인하고, "정상 키는 안 적는다"는 반대 증거도 같이
    둔다(#25).
    ⚠️⚠️ **독립 리뷰가 잡은 Blocking — 새 테스트가 운영자의 진짜 `~/stock/
    .env` 를 읽고 있었다.** 하네스가 cwd 만 격리하고 `Path.home()` 을 안 막아
    `_dotenv_lookup` 의 둘째 경로가 실제 자격증명 파일이었다. 그래서 **이 fix
    가 지시하는 조치("`.env` 에 KRX_ID/KRX_PW 추가")를 운영자가 하는 순간**
    `make test` 가 5건 빨간불이 되고 §Pre-commit 6('fail 시 commit 금지')으로
    무관한 커밋 전부가 막힌다 — 성공 조건이 뇌관인 시한폭탄이다(#67·#249·
    #291). 실측으로 재현·확인했다(키 있으면 5 failed → 격리 후 8 passed).
    **파일 경로를 읽는 테스트는 cwd 뿐 아니라 `Path.home()` 도 막을 것.**
    그리고 전역 캐시는 `clear()` 가 아니라 `monkeypatch.setattr` 로 갈아끼워
    복원되게 할 것(#30·#130).
    ⚠️⚠️ **함수를 합치며 예외 범위가 좁아져 단일 게터가 던질 수 있게 됐다**
    (리뷰 High): 옛 `env_key` 는 `find_dotenv(usecwd=True)`·`Path.home()` 까지
    통째로 감싸 로그만 남기고 `""` 를 냈는데, 새 스캔은 import 와
    `dotenv_values` 만 감쌌다 — cwd 가 지워지면 FileNotFoundError, HOME 이
    없으면 RuntimeError 가 **호출부 47곳**으로 나간다. 특히 `daily_kr_flow`
    의 `except Exception: pass` 안에서 던지면 `return None` 게이트를 건너뛰어
    막으려던 pykrx 로그폭주가 열린다. **합칠 땐 조기반환뿐 아니라 `try` 의
    범위도 대조할 것**(회귀: `env_key` 는 어떤 경우에도 안 던진다).
    ⚠️ 배선 가드는 **이름 열거가 아니라 전수 불변식**으로 — 첫 판은 4곳 중
    하나만 덮여 나머지 셋을 동시에 지워도 green 이었다. `bot/` 를 훑어
    "자격증명 이름 + 미설정" 을 적는 함수엔 `env_diag`/`env_why` 가 있어야
    한다로 바꾸자 **켜자마자 넷째 자리**(대시보드 수급 표 각주)를 잡았다
    (#24·#87a). 그 가드가 처음엔 **별칭 import**(`env_diag as _env_diag`)를
    못 보고 멀쩡한 배선을 '없다'고 했고, `get_docstring(clean=True)` 가
    들여쓰기를 지워 **규칙을 설명하는 독스트링이 스스로 걸렸다**(#47·#59b).
    ⚠️ 그리고 "값을 안 찍는다" 테스트가 **동어반복**이었다 — 비밀값을
    *채워진* 키에 넣었는데 `env_diag` 는 채워진 키를 건너뛰므로 애초에 출력
    후보가 아니었고, `환경변수(길이 N)` → `{v}` 누출 뮤테이션이 그대로
    통과했다. 누출은 **갈래마다** 막을 것(#286·#292).
    뮤테이션 9종 fail-before 확인.

### 실수 #298

298. **한 라벨이 네 갈래를 대표하는데 화면엔 그 라벨뿐이었다**(2026-09-07
    Breadth 확정 이력, 사용자 "8월말에 왜 현금대기인지 안알려줘? 그리고 이거는
    역추세·추세·회복·비추세중에 어떤건거야?"): `CASH`(현금 대기)는 **네 갈래**
    로 도달한다 — ① 역추세인데 낙폭이 트랜치(−12%)에 못 미침 ② 회복인데 조건
    충족 섹터 없음 ③ 비추세·추세인데 RS>0 섹터 없음 ④ 판정 불가. 처방이 다
    다른데 표는 `현금 대기` 하나로 묶었고(#82·#34), **구간 열은 아예 없어서**
    그 달이 네 구간 중 무엇이었는지 화면으로는 알 수 없었다 — `regime` 은
    기록에 **있는데** 표가 안 썼다(#123·#129·#189·#228 계열).
    ⚠️ 처방은 렌더타임 파생이다 — 저장된 (구간·상태·DD)에서 사유를 만들면
    **옛 확정 기록도 그대로 따라온다**(#270 백필이 공짜). 모르는 조합엔
    지어내지 않는다(#165).
    ⚠️ 그 문자열은 HTML escape 를 거치므로 `**볼드**` 를 쓰면 별표가 화면에
    그대로 나온다(실측) — 사유 문구는 평문으로.
    ⚠️ 열을 하나 늘리자 **열 개수를 리터럴로 박은 회귀 둘**이 깨졌다
    (`hdr[:7]` · `== 7`) — 계약은 "숫자 헤더는 우측정렬 · 헤더 수 = 셀 수"
    이지 "열이 7개" 가 아니다(#19·#67). 헤더에서 파생시켜 다시 썼다(#222).
    ⚠️ 그리고 같은 편집 실수를 **한 세션에 두 번** 했다 — 헬퍼 문자열이 앵커로
    끝나는데 `s.replace(anchor, helper + anchor)` 를 써서 `def` 줄이 중복
    삽입됐다(`post_ftd_note`·`decide`). 문법 오류로 즉시 드러났지만, AST 중복
    정의 검사(#59)가 있는 레포에서 같은 실수를 반복한 것이다 — 함수를 삽입할
    땐 앵커를 **한 번만** 쓸 것.

### 실수 #299

299. **멱등 키가 너무 굵으면 원천이 늦은 날 굳은 값이 영영 안 고쳐진다**
    (2026-09-07 Breadth 8월 확정, `--why` VM 실측이 발각): 2026-08 확정이
    `asof=2026-08-28`(금)인데 그 달 마지막 거래일은 **08-31**(월)이었다 —
    09-01 에 기록될 때 야후 시계열에 08-31 이 아직 없었고, `append_signal`
    의 멱등 가드가 **`month` 만** 봐서 나중에 그 봉이 들어와도 다시 쓰지
    않았다(#18 구워진 데이터 · #122·#136 야후가 하루 늦는 그 문제).
    ⚠️ 무해한 사고가 아니다 — 같은 프로브 출력에서 08-24 는 `과거 리더
    놀림목`(50% 투자), 08-25~31 은 `현금 대기` 였다. **어느 날을 월말로
    잡느냐가 신호를 바꾸고**, 그 기록은 회복 후보 풀(Top3 이력)의 원천이라
    오염이 뒤로 전파된다.
    처방: 멱등 기준을 (월) → **(월, 기준일)** 로 좁혀 더 **늦은 종가**가
    오면 정정본을 덧쓰고, 읽는 쪽이 달마다 가장 늦은 것만 남긴다. 그리고
    화면이 **어느 종가로 확정했는지** 적는다 — 월만 적으면 이 사고가 다시
    안 보인다(#43).
    ⚠️ 일반화: 멱등 키를 정할 땐 "이 키가 같은데 **내용이 달라질 수 있나**"
    를 먼저 물을 것. 달라질 수 있으면 그 축을 키에 넣어야 한다.
    ⚠️ 그리고 이 결함은 **진단을 심었기 때문에** 보였다 — 사용자 질문에
    답하려고 만든 `--why` 의 ⑤(그 달 마지막 거래일들)가 확정일(08-28)보다
    뒤에 08-31 이 있다는 걸 나란히 보여줬다. 답만 내고 끝냈으면 못 봤다
    (#51 나란히 놔야 보인다 · #187 판정줄 말고 같은 실행의 다른 줄도 읽을 것).
    ⚠️ 뮤테이션에서 또: 루프의 스킵 조건(`have.get(월) >= d`)을 옛 `월 in
    have` 로 되돌리는 변형이 **회귀 65개를 전부 통과**했다 — 헬퍼
    (`append_signal`)만 재고 수집기를 안 태웠기 때문이다(#20).
    ⚠️⚠️ 그리고 배포전 셀프리뷰가 **내가 방금 배포한 것**에서 #201 재발을
    잡았다 — 각주에 `class='si-note'` 를 쓰면서 그 페이지 번들(`_BS_CSS`)에
    정의를 안 뒀다(대시보드에 같은 이름이 있어도 이 페이지는 그 번들을 안
    쓴다, #273). #273 이 만든 CSS 가드가 못 잡은 이유는 그 가드의 페이지
    목록이 **`bot/dashboard.py` 페이지만 열거**하고 있었기 때문이다(#24) —
    `bot/` 에서 `<!doctype` 을 그리는 모듈은 10개다. 두 페이지를 목록에
    넣고, "완결 페이지 모듈은 목록에 있거나 부채로 명시" 를 전수 가드로
    걸었다(켜자마자 `us_pages` 를 잡았다, #87a).
    ⚠️ 그 새 목록 항목이 **처음엔 눈이 멀었다** — 확정 이력이 빈 픽스처라
    각주 블록을 한 번도 안 그려, 정의를 지워도 통과했다(#91c. 그 파일
    독스트링이 "빈 픽스처는 껍데기만 그린다" 고 이미 경고하고 있었다).

### 실수 #300

300. **같은 명시도면 뒤엣것이 이긴다 — 판정이 하나여도 화면이 갈렸다**
    (2026-09-07 DART 피드, 사용자 "중요로 필터했을때 … 왜 어떤건 금색테두리로
    표시안되는거야?"): `🔥` 배지와 금색 테두리는 코드상 **같은 `_sig`** 에서
    나오는데 화면에서 갈렸다 — `.df-card.df-significant`(금색)와
    `.df-card.df-noparse`(회색 점선)가 명시도가 같은데 후자가 **뒤에** 선언돼
    `border-color` 를 이겼다. 즉 🔥 이면서 미파싱제외인 카드는 **배지만 금색**
    이고 테두리는 회색이었다. 소스를 눈으로 읽으면 못 잡는다(#273 "정의가
    있어도 캐스케이드에서 지면 화면은 깨져 있다 — 명시도 축은 따로 못박을 것"
    을 적어 두고 그 축을 안 만들었다).
    처방: 금색 규칙을 플래그 규칙 **뒤로** 옮긴다 — `border-style`(점선·파선)은
    위 규칙이 계속 담당하므로 **금색 점선 = 중요 + 미파싱제외** 로 두 신호가
    같이 보인다. 회귀는 렌더된 CSS 에서 **승자를 계산**해 못박는다.
    ⚠️ 진단에서 두 번 헛짚었다: (a) `df-badge-sig in html` 로 쟀는데 그게
    **스타일시트**에 매칭됐다(#47 계수 패턴 · #91b 재는 대상이 맞나) (b) 픽스처가
    `report`/문자열 `detail` 이라 `_annotate` 가 안 돌아 플래그가 빈 채로 나왔다
    — 원천은 `report_nm`/**리스트** `detail` 이다(#155 픽스처는 원천이 실제로
    보내는 모양대로). 관측된 조합을 코드로 태워서야 갈렸다(#118).

### 실수 #301

301. **한 봉만 잇는 보강은 다음 장이 열리는 순간 영영 못 잇는다 — 그리고 그
    생략이 조용했다**(2026-09-08 "이건 왜 지연되는거야?" KR·JP 기준 09-04 ·
    기대 09-07): 야후가 지수 종가를 하루 늦게 주면 `_quote_tail` 이 시세 한
    봉을 덧붙이는데, 붙일 자격을 **'시세의 전일종가 == 야후 마지막 봉'** 으로
    본다. 그래서 다음 세션이 열리면 시세의 prev 가 옮겨가 정렬이 영원히 안
    맞고, 빠진 봉은 다시는 안 채워진다. 2026-08-20(#37)에 같은 증상을 고쳤는데
    같은 화면이 두 번 안 바뀐 것이다 — 그때는 여유(grace)가 증상을 덮었고
    이번엔 보강의 **사거리**가 짧았다.
    ⚠️ 더 나쁜 건 생략이 `log.info` 한 줄로도 갈래를 안 남긴 것이다("정렬
    불가로 보강 생략(장중 추정)" 이라고 **원인을 단정**했다 — 장중일 수도,
    2세션 뒤처진 것일 수도 있고 처방이 다르다, #82·#165). 판정을 순수 함수
    (`tail_diag`)로 빼 갈래를 이름으로 부르고, 화면·로그·진단이 **같은
    사유 함수**(`tail_reason`)를 쓴다(#35·#38).
    ⚠️ 같은 증상 2회+ 는 추측 종료다(#12) — 다음 패치 대신 `--why` 를 심어
    (§Automation-first·#252) 어느 갈래인지 **재고 나서** 고친다. 문턱을 못
    쟀는데 고치면 엉뚱한 관문을 손본다(#107·#146).

### 실수 #302

302. **네 갈래 중 셋이 조용한 판정은 "원천이 최선인가"에 답하지 못한다**
    (2026-09-08 미국채 "오늘 9/8 일인데 이게 가져올수 있는 최선인거야?" —
    화면 기준 09-03): FRED `DGS*` 는 D+1 이라 재무부 원천으로 하루 당기는
    `fresher_than` 이 있는데, 실패 갈래 넷 중 **셋이 로그 한 줄도 안 남겼다**
    (곡선 미수신 · 겹치는 날 없음 · 더 새 날짜 없음). 그래서 화면이 왜 안
    당겨졌는지 **로그로도** 알 수 없었고, 무엇보다 마지막 갈래(`no_newer`)는
    결함이 아니라 **원천이 이미 최선**이라는 뜻인데 나머지와 구별이 안 됐다
    — 고칠 수 없는 것과 고칠 수 있는 것이 같은 침묵이면 둘 다 안 고쳐진다
    (#82·#260). 갈래를 이름으로 부르고 화면·로그·진단이 같은 사유 함수를
    쓰게 했다(#38).
    ⚠️ 코드 재독에서 같이 나온 구조적 결함: 대조용 곡선을 **현재 달만**
    받아, 매달 초 2~3영업일 동안은 FRED 최신일이 아직 지난달이라 겹치는
    날이 표에 없고 검산이 성립하지 않아 보강이 **조용히 죽는다**. 가드가
    '못 만든다'로 끝나면 그 자리가 영원히 비는지 먼저 물을 것(#171).
    ⚠️ 그리고 '최선'은 **휴장일을 반영해야** 한다 — 2026-09-07 은 미 노동절
    이라 값 자체가 없고, 당일 값은 재무부가 15:30 ET(한국 다음날 새벽)에
    낸다. 즉 한국 낮에는 09-04 가 최선이다. 그 판정을 진단이 자체 재구현하면
    화면과 갈라지므로 시장타이밍의 `_expected_session` 을 그대로 쓴다(#35·#176).
    ⚠️ 진단 전에 grep 만으로 "이 화면은 재무부 보강이 아예 없다"고 단정할
    뻔했다 — `macro_snapshot` 은 `treasury_yield_client` 를 직접 import 하지
    않지만 `market_overview._fred_fetch_series` 를 거쳐 **같은 경로**를 탄다.
    import 그래프 한 겹만 보고 배선을 판정하지 말 것(#53·#25).

### 실수 #303

303. **감사는 다섯 번 다 잡고 있었다 — sweep 이 세지 않는 글자로 찍었을
    뿐이다**(2026-09-08 "메인대시보드에것들 Delay 되는게 없는지 봐줘. 이것만
    벌써 다섯번째"): `audit_sweep._findings` 는 `❌` 만 결함으로 세고 `warn` 은
    `⚠️` 만 센다. 그런데 `macro_staleness_audit` 은 **도달 실패 · 관측 없음 ·
    라벨 판독 실패**를 `❗` 로 찍고 있었다(5곳) — 재무부가 며칠을 못 닿아도
    일일 결산이 **한 글자도 말하지 않는다**. 그래서 매번 사용자가 화면을 눈으로
    보고 먼저 물었다. 도구는 눈이 안 멀었고 **집계 규약이 갈린 것**이다
    (#250 판정 글자는 판정에만 · #24 새 글자는 열거된 집계 밖 · #268 을 한
    도구에만 적용한 것의 재발).
    처방은 생산부다 — 그 다섯을 `❌`/`⚠️` 로 바꾸고, `bot/scripts/*audit*.py`
    **디렉터리 전수**로 "sweep 이 세지 않는 판정 글자 금지" 가드를 건다(독스트링·
    주석은 걷어내고 본다, #59b — 규칙을 설명하는 글이 스스로 걸린다).
    ⚠️ 그 가드는 **열거형**이라 새 이모지를 지어 쓰면 못 잡는다 — '판정 위치의
    이모지'는 구조로 갈릴 수가 없어 감도를 택했고, 못 보는 축을 독스트링에
    밝혔다(#274).
    ⚠️ VM 실측이 갈래를 확정했다: `no_curve` = 재무부 XML **read timeout 6/6**.
    같이 드러난 비용 결함 — 실패를 캐시하지 않아 렌더 경로가 시리즈 3종 ×
    달 2개 = **6회를 매번 타임아웃까지** 기다렸다(#116 예산과 캐시는 한 세트).
    실패는 **짧게만**(10분) 믿는다 — 길게 믿으면 원천 장애 한 번이 하루를
    비운다(#152·#161). 그리고 도달 실패는 갈래를 이름으로 말한다(timeout /
    http4xx / network — 처방이 정반대다, #82·#282). 기본 UA(`python-requests`)
    도 통제 가능한 축이라 먼저 맞췄다(원인 단정은 아니다, #12·#165).
    ⚠️ 그리고 그 감사가 판정을 **재구현**하고 있었다 — 제품의 `fresher_diag`
    를 태우고 '최선' 은 시장타이밍 `_expected_session` 을 쓰게 했다(#35·#38·#169).
    ✅ 같은 실행에서 확인: KR·JP 지수 지연(#301)은 **야후가 따라잡아 해소**
    됐다(09-07·09-08 수신) — 즉 그건 원천 지연이었고 `_quote_tail` 의 사거리
    문제는 구조적 결함으로 남아 있다(다음에 야후가 하루 빠지면 또 난다).

### 실수 #304

304. **'실시간이라 기준일이 없다'가 22장을 판정 불가로 만들었다**(2026-09-08
    매크로 스냅샷, 사용자 "매크로 22카드 기준일부터 봐줘"): 가격·원자재·코인·환율
    카드는 `asof=''` 로 **아무 기준도 안 실었고**(설계상 의도였다), board_audit 은
    매일 "❓ 기준일 미표기 22" 를 찍으며 스스로 "판정 자체를 못 한다 = 가장
    위험하다"고 적고 있었다. 위험은 실재였다 — 네이버 값 풀 4종(idx/com/coin/fx)과
    yf 월간 배치가 **실패하면 전부** `_cached(..., ttl=86400)` 로 떨어져 최대
    24시간 낡은 값을 아무 표시 없이 돌려준다(소스로 확인). 규칙 10b 위반이다.
    ⚠️ 그런데 원천 응답에 시각 필드가 있는지는 **재지 않았다** — 우리 코드는
    `closePrice` 만 읽는다. 그래서 추측 대신 **아는 쪽에 물었다**(#64·#86):
    값이 실제로 온 **캐시 파일의 mtime**(`finviz_client.cache_age_sec` →
    `naver_marketindex.value_age_sec`)이 곧 수집 시각이다. 라벨도 '기준'이 아니라
    **'값 수집'** — 우리가 잰 건 우리가 받아온 시각이지 거래소가 그 가격을 찍은
    시각이 아니다(#165 안 잰 것을 단정하지 말 것 · #34 한 접두사가 두 뜻을
    대표하면 한쪽은 거짓말).
    ⚠️ 문턱은 **그 값을 채우는 캐시의 주기에서** 정한다 — 처음엔 15분 하나로
    두었는데 yf 월간 배치(TTL 1시간)를 쓰는 DXY 가 한 시간 중 45분을 '지연'으로
    띄웠다(독립 리뷰가 배포 전 실측: 40분 → stale). 내 E2E 픽스처가 5분이라
    그 구간을 한 번도 안 태웠다(#91c). 늘 뜨는 배지는 아무것도 안 재는 것과
    같다(#25·#260).
    ⚠️ **병합 캐리오버는 파일 mtime 을 거짓말로 만든다** — 지수 풀은
    `{**known, **fetched}` 를 다시 써서 원천이 빠뜨린 코드까지 '방금 받은 것'이
    된다. 나이는 파일이 아니라 **값이 알아야** 하므로 이번에 실제로 받은 것에만
    도장(`_at`)을 찍는다(#64).
    ⚠️ 값의 출처는 **분기 이름이 아니라 값으로** 정할 것(`_val_tag`) — 같은
    분기 안에서도 네이버 값 풀/히스토리 폴백/yf 월간이 갈린다. 나이를 못 잰
    경로는 ✅ 가 아니라 **판정 불가**로 남기고 사유를 말한다(#54·#82).
    ⚠️ 감사 판정을 인라인으로 두면 태워볼 수 없다 — `_audit_macro_snapshot_rows`
    로 빼서 값으로 고정했다(#176·#19). 그리고 payload 스키마가 바뀌었으니 30초
    스냅샷 캐시 salt 를 같이 올렸다(#18·#95·#198).

### 실수 #305

305. **실시간 값에 기준이 없다고 침묵하면 감사가 `기준 —` 만 찍는다 — #304 의
    변동성판**(2026-09-08 VIX): `fetch_volatility_snapshot` 이 네이버 실시간
    경로에 `date=None` 을 실어 화면·감사가 매일 `vix 15.3 · 기준 —` 이었다.
    #304 가 매크로 22장에 만든 **값 수집 시각**(캐시 mtime) 규약을 그대로
    쓰면 되는 자리였다 — 새 규약을 만들 게 아니라 **선행 사례를 찾는 것**이
    먼저다(§작업 원칙). 라벨은 `macro_snapshot.live_asof` 한 곳에서 온다(#38).
    ⚠️ 판정을 감사에 인라인으로 두면 화면과 갈라진다 — `vol_asof_label` 로
    빼서 화면·감사가 **같은 함수**를 부른다(#35·#169·#176). 그리고 나이를
    **두 번째 호출**로 물으면 그 사이 갱신된 값의 나이를 옛 값에 붙인다(#160)
    — `_fetch_vix_naver` 가 값과 나이를 같은 응답에서 묶어 돌려준다.
    ⚠️ 반환형을 바꿀 땐 **실패 sentinel 을 유지**할 것 — `None` 을 그대로 둬서
    `lambda: None` 스텁 7개가 살았다(#183 시그니처를 바꾸면 스텁이 무너진다).

### 실수 #307

307. **`0건` 은 갈래가 셋인데 화면도 로그도 한 마디도 안 했다**(2026-09-08
    실적 캘린더 US): `🇺🇸 미국 실적 0건` 옆에 KR 은 562건인데, 원천 부재 ·
    키 미설정 · 호출 실패가 전부 같은 `0건` 이었다(#82·#43). `_fetch_us_month`
    는 `if not key: return []` 로 **조용히** 끝났다. `us_month()` 가 (행, 사유)
    를 돌려주고 화면이 사유를 적는다 — 값만 꺼내는 얇은 래퍼는 남기되 화면은
    전체를 쓴다(#129 래퍼를 만들 땐 버리는 정보가 화면에 필요한지 먼저).
    ⚠️⚠️ **그리고 여기서 내가 틀렸다** — "빈 응답을 6시간 캐시해 원천 장애가
    반나절 빈 달력이 된다"고 적고 TTL 을 10분으로 줄였는데, 독립 리뷰가 전제를
    뒤집었다: **장애는 애초에 캐시되지 않는다**(예외 경로가 쓰기 전에 빠진다).
    고칠 수 있는 실패모드가 없는데 정당하게 0건인 달만 상류 호출이 **36배**가
    됐다(요청마다 라이브로 그리는 no-cache 경로다). 1시간으로 되돌렸다.
    교훈: **"이 완화가 실제로 어느 경로를 고치나"를 코드로 짚고 나서** 문턱을
    건드릴 것 — 재지 않은 이득을 위해 비용을 늘리는 것이 가장 흔한 형태다
    (#61 "이 파라미터가 비용의 어느 단계를 줄이나"의 반대 방향).
    ⚠️ 방어용으로 넣은 `if market == "us"` 게이트는 **도달 불가능한 죽은
    코드**였고 뮤테이션이 그대로 통과했다(#291) — 지우고 그 불변식
    (`_us_reason` 은 us 분기에서만 설정)을 AST 로 못박았다.

### 실수 #315

315. **곁들이 하나가 본체를 지웠다 — 넓은 `try` 는 폴백까지 같이 삼킨다**
    (2026-09-08 독립 리뷰 Low #19, VIX 카드): `fetch_volatility_snapshot` 의 VIX
    블록은 히스토리·현재값·나이·과거창이 **한 `try`** 안이라 `_live_age_fields`/
    `live_asof` 의 모양이 어긋나기만 해도 카드가 통째로 사라졌다 — 바로 아래
    yfinance 폴백도 같은 try 라 함께 죽고 남는 건 debug 한 줄뿐이었다(#12
    silent-fail 금지 · #42a 폴백은 버그를 숨긴다). 단계마다 따로 감싸 **덜
    중요한 것만** 잃게 하고(값 > 나이 > 과거창), 못 붙인 자리엔 사유를 남겨
    감사가 '판정 불가'로 집게 한다(#43·#54·#82). 일반화: `try` 를 넓힐 땐 "이
    안에서 **덜 중요한 게 던지면 무엇이 같이 죽나**"를 먼저 답할 것.
    ⚠️ 같이 다시 쓴 옛 단언 셋(#222): (a) 순서 단언이 `href="lookup/2467.TT"`·
    `data-name` 에 걸려 별칭을 티커 **앞으로** 옮기는 변형이 통과했다(실측, #75)
    — 태그를 걷은 **보이는 텍스트**로 잴 것 (b) 그 단언은 공백까지 박은
    정규식이었다(#19) → 생성 JS 는 파서만이 아니라 **실행**까지 태운다(#253)
    (c) 지역 변수 이름(`rec`)을 AST 로 박으면 이름 리팩터에 깨지고, 정작 같은
    이름의 **다른 레코드**를 넘기는 변형은 통과한다 — **객체 동일성**으로.
    ⚠️ "도달 불가한 가드"는 **호출부가 하나일 때만** 참이다 — `sessions_behind`
    의 앞선봉(→0) 분기는 `_quote_tail` 에선 못 닿지만 `fetch_index_history`
    에선 장중 부분봉(#40)으로 닿는다. 지우기 전에 호출부를 세고, 남길 거면
    **닿는다는 것을 값으로 증명**할 것(#291 의 반대 방향).
    ⚠️⚠️ 그리고 **부정 기억의 전제를 내가 틀렸다**(같은 날 독립 리뷰가 반증):
    `_resolve_yf` 가 성공만 기억해 후보가 전부 빈 티커는 갱신마다 순손실 호출을
    냈길래 "'없다'는 **답**은 기억하고 예외('못 물었다')는 안 기억한다"로 갈랐는데
    — yfinance 는 `hide_exceptions` 기본값 때문에 read timeout·5xx 에도 **예외
    없이 빈 프레임**을 준다(라이브러리 스스로 "possibly delisted" 라고 적는다).
    즉 빈 답은 두 갈래를 **구별해 주지 않으므로**, 예외 유무로 갈라 영구 기억하면
    야후 30초 블립 하나가 그 티커를 프로세스 수명 내내 굳힌다(화면은 값이 빈 채로,
    로그 한 줄도 없이). 일반화: **갈래를 못 재면 갈랐다고 가정하지 말고**(#165)
    시간으로 만료시킬 것 — 그러면 어느 갈래든 스스로 회복한다. 그리고 만료 기억을
    쓰는 동안에도 그 사실을 말할 것(#43). 내 회귀도 `history()` 를 `[]` 로 스텁해
    **틀린 전제를 그대로 굳히고** 있었다 — 스텁이 곧 원천의 실패 모양이면 그
    테스트는 결함을 시멘트로 바른다(#155 픽스처는 원천이 실제로 보내는 모양대로).

### 실수 #317

317. **비용 원장이 '공짜'라고 말할 수 있었다 — 단가표가 두 벌 + 모르는 모델은
    조용히 ₩0**(2026-09-08, `or 0` 계열 전수 스캔에서): 이 레포에서 세 번 사고를
    낸 계열(#235 표시 · #242 비율 · #297 판정 뒤집힘)을 AST 로 54건 훑어 '없음 →
    판정 완화·사실 날조' 인 것만 골라 실측했다. `risk_gate`(이미 `is not None` 로
    걸러 도달 불가)와 `customs_scan`(관세청이 미확정 월을 **0 으로 채워 보낸다** —
    주석대로 맞다)은 **결함이 아니었고**, 남은 둘이 진짜였다.
    (a) 단가표(`_PRICING`)와 산식이 `bot/usage_tracker` 와 `trade/llm_usage` 에
        **각각** 있었다. 오늘은 한 자리도 안 틀렸지만(실측) 메인 비용카드는
        **둘을 합산**하므로 단가를 한쪽만 고치는 날 화면이 조용히 틀린다(#38).
        표시 환율(`KRW_PER_USD`)의 "keep in sync" 주석도 규율이라 단일 출처로
        묶었다(#119).
        ⚠️⚠️ **그리고 여기서 내가 리뷰 지적을 재지 않고 받아들여 틀렸다**:
        리뷰가 "메인 카드 1330 vs `/usage` 1380 분기" 라고 해서 통일했는데,
        실측해 보니 둘은 **다른 일**을 한다 — 1330 은 레거시 `cost_krw` 가
        **기록될 때** 쓰인 환율(`cheongyak_brief` 등이 `cost_krw/_USD_TO_KRW`
        로 canonical USD 를 적는 그 상수)이라 되읽을 때도 그걸로 나눠야
        왕복이 정확하고, 1380 은 **화면 표시** 환산이다. 통일했으면 과거 합계가
        3.6% 틀어졌다. 되돌리고 `_LEGACY_KRW_WRITE_FX` 로 이름을 갈라 회귀로
        고정했다. 일반화: **리뷰 지적도 재고 나서 채택할 것**(#12·#165) —
        같은 숫자가 두 군데 있다고 항상 중복인 건 아니다(#34).
    (b) 단가표에 없는 모델은 **100만 토큰을 쓰고도 ₩0 · 로그 한 줄 없음**이었다.
        모델 id 는 실제로 바뀐다(2.5→3.0) — 그날 비용카드가 '공짜'라고 말하고
        값이 다 '있어서' 어떤 감사도 안 걸린다(#284). 단가를 지어낼 수는 없으므로
        (#32) 0 은 두되 **모델 이름을 대서** 알리고(모델당 1회 — 늘 뜨는 경고는
        아무것도 안 재는 것과 같다 #25·#260) 원장에 `unpriced` 표식을 남긴다.
    ⚠️⚠️ **표식을 쓰기만 하고 정작 큰 화면에선 아무도 안 읽고 있었다**(독립
    리뷰가 잡음): 드리프트가 일어나는 곳은 NOAH 본체(분석·Screener·DailyByte)
    인데 배선한 건 trade 쪽 두 줄뿐이었다 — 계산해 두고 안 실으면 없는 것과
    같다(#43·#123·#189·#228). 더 나쁜 건 **원장에 쓰는 곳이 13곳인데 표식을
    붙이는 곳은 둘**이라, 표식만 믿는 판정은 나머지 11곳과 옛 레코드를 통째로
    놓친다(#24). 그래서 판정을 **읽는 쪽이 단가표에 직접 대조**하게 바꿨다
    (`is_unpriced_record`) — 원천에 물으면 쓰는 곳이 몇 곳이든 안 샌다(#86).
    ⚠️ "단일 출처로 만들었다"도 **거짓이었다**: 같은 요율이 `_PRO_IN`·
    `_FLASH_OUT` 같은 리터럴로 `bot/` 12개 모듈에 더 있다. 한 커밋에 다 옮기기엔
    회귀 위험이 커서 **기계가 대조**하게 했고(요율이 갈리면 회귀가 전 사이트를
    가리킨다), 문구를 사실대로 고쳤다 — 가드가 재는 범위를 넘어서는 주장을
    독스트링에 적으면 다음 사람이 그걸 믿는다(#55·#286).
    ⚠️ 그리고 **두 경로를 합치면 그 둘을 대조하던 검사는 죽는다**(#291 그대로):
    `cost_usd ≡ estimate_cost_usd` 가 된 순간 그 비교 테스트는 요율을 두 배로
    만드는 뮤테이션도 통과했다. 합친 뒤 남는 계약은 **표의 값 자체**다.
    ⚠️ 수치 주장도 과장이었다 — 실측 정정: 최대 상대오차 1e-20 이 아니라
    **4.30e-16**(1 ULP)이고 `round(…,6)` 기준 60만 표본 중 **25,697건**이 갈린다.
    "값이 안 바뀐다"고 쓰기 전에 **저장 정밀도로** 재 볼 것.
    ⚠️ 일반화: `X or 0` 은 **비교·나눗셈에 쓰일 때** 위험하다. 스캔은 그 두
    문맥만 보면 되고(#24 전수), 걸린 것마다 "없음이 판정을 **완화**하나 **강화**
    하나"를 물으면 대부분 안전한 방향(임계 필터·버전 게이트)으로 갈린다.
    ⚠️ 픽스처를 손으로 만들어 포맷터가 쓰는 키를 빠뜨렸다(실측 KeyError) —
    원천이 실제로 내는 모양(`collect()`)을 그대로 쓸 것(#155).

### 실수 #319

319. **확인 명령의 따옴표를 손으로 조립했다가 멀쩡한 가드가 '거짓'이라고
    보고했다**(2026-09-09 단가 미등재 판정): 겹따옴표 `-c "…"` 안에 `'"'"'`
    를 썼는데 그 idiom 은 **홑따옴표 문자열 안에서만** 성립한다 — 키가
    `model` 이 아니라 `"model"` 이 되어 `rec.get("model")` 이 None 이고 판정이
    `False` 로 나왔다. 오류가 아니라 **그럴듯한 판정값**이라 하마터면 '가드가
    눈멀었다'로 없는 버그를 쫓을 뻔했다(#252 의 따옴표판). 값을 넘기는 확인은
    겹따옴표 `-c` 대신 **quoted heredoc**(`python3 - <<'PY'`)으로 줄 것 —
    escape 가 없어 조립 사고가 성립하지 않는다.
    ⚠️ 손으로 조립한 진단은 제품 코드로 검증되지 않는다. 모델 id 는 실제로
    드리프트하므로(2.5→3.0) 이 확인은 반복된다 → 명령을 건네는 것으로 턴을
    끝내지 말고 `--check` 를 같은 턴에 심을 것(#12·#252).
    ⚠️ 그 진단이 `load_records()` 를 부르면 **운영 원장을 다시 쓴다**(읽으면서
    로테이션 + 롤업 적산) — 진단이 자기가 읽을 신호를 오염시키면 안 되므로
    (#264·#283) 읽기 전용 스캔을 따로 뒀고, 회귀가 31일 전 레코드로 그 경로를
    실제로 태운다(#91c). 그리고 대조 0건은 ✅ 가 아니라 ❓ 다(#54).
    ⚠️⚠️ **그 `--check` 가 배포전 독립 리뷰에서 여섯 군데 걸렸고, 그중 하나는
    어제 배포분(#317)에 이미 들어 있던 것이었다**: 미등재 표본으로 **실제 모델
    id**(`gemini-3.0-pro`)를 썼기 때문에, 화면이 시키는 처방("_PRICING 에
    추가")을 이행하는 순간 회귀 9건이 빨간불이 되어 §Pre-commit 6 으로
    **무관한 커밋까지 전부 막힌다**(실측). 성공 조건이 뇌관인 시한폭탄이다
    (#294·#67·#249). 표본은 요율표에 실릴 수 없는 **합성 이름**이어야 하고,
    그 표본은 제품 상수 하나(`_UNPRICED_SAMPLE`)에서 온다 — 테스트마다
    복제하면 또 갈린다(#38). 일반화: 진단이 **처방을 인쇄**한다면 "그 처방을
    이행한 뒤에도 이 검증이 green 인가"를 회귀로 못박을 것.
    ⚠️ 같은 리뷰의 나머지: 원장 판정을 `not is_priced(m)` 로 재서 **화면이 쓰는
    술어**(`is_unpriced_record`)와 갈라져 비용카드가 과소집계 중인데 CLI 가
    초록불을 줬고(#35) · 읽다 끊긴 부분 통계를 완결인 척 ✅ 로 찍었고(#41·#54) ·
    `unknown`(기록 경로 폴백)에 이행 불가능한 처방을 달아 영구 ❌ 를 만들었다
    (#82·#260) · '파일 없음'과 '비어 있음'을 같은 문구로 적었다(#82).
    ⚠️⚠️ **그리고 심자마자 실제로 뭔가 잡았다**(VM 실측: 원장 6,849콜 중 12콜이
    `model="unknown"`). 그런데 그 갈래를 `--check` 에만 만들고 **화면 4곳**
    (비용카드·`/usage`·trade 2곳)은 그대로 '단가 미등재' 라고 적고 있었다 —
    `unknown` 은 `_PRICING` 에 넣을 수 있는 이름이 아니니 운영자를 요율표로
    보내는 **이행 불가능한 처방**이다(#34 한 라벨이 두 갈래를 대표하면 한쪽은
    거짓말). 한 화면을 고쳤으면 같은 것을 말하는 화면을 즉시 grep 할 것
    (#38·#147 — 이 항목 안에서 같은 실수를 한 라운드 만에 반복했다).
    판정은 `split_unpriced` 단일 출처로 두고(trade 는 호출부가 모델명을
    명시해 그 갈래가 구조상 안 생기므로 그대로 뒀다 — 잊은 게 아니라고
    독스트링에 적었다, #55). 그리고 갈래를 밝히기만 하면 "그래서 얼마나·
    언제냐"가 바로 온다 → 토큰 합과 KST 기간을 같이 싣는다(#202) — 안 실으면
    다음 라운드에 또 손으로 명령을 조립하게 되고, 그게 이 항목의 사고다.
    ⚠️⚠️ **그 갈래 분리가 배포전 독립 리뷰에서 넷 더 걸렸고, 첫째가 가장
    나빴다**: 갈래를 나누며 '실제 비용은 더 큼' 을 **요율 갭 쪽에만** 남겨,
    원장이 `unknown` 뿐인 VM 실제 모양(12콜)에서 그 경고가 **통째로 사라졌다**
    — 배지의 존재 이유가 카드가 '공짜'라고 말하는 걸 막는 것인데(#284) 갈래를
    나누다 그걸 잃었다. **두 갈래 모두 ₩0 으로 집계되므로 그 사실은 갈래와
    무관하다** — 갈려야 하는 건 **처방**이지 사실이 아니다(#82). 일반화:
    한 문구를 둘로 나눌 땐 "**두 갈래에 공통인 사실이 어느 한쪽에만 남지
    않는가**" 를 먼저 물을 것.
    ⚠️ 나머지 셋: `split_unpriced` 가 **model 이 빈 레코드를 어느 버킷에도
    안 넣어**(`is_unpriced_record` 는 이름이 있어야 True) CLI 의 `unrecorded`
    와 수가 갈렸고(#38 → `counts_as_unpriced` 단일 술어) · 레코드 하나의 `ts`
    가 숫자가 아니면 **스캔이 통째로 중단**돼 뒤의 진짜 요율 갭이 가려졌다
    (JSON 파싱 실패는 레코드 단위로 넘기는데 숫자 변환만 루프 전체를 죽였다,
    #315) · 배지가 **재지 않은 창**('원장 30일')을 적었다 — 로테이션은
    `/usage` 를 칠 때만 도는 유일한 경로라 파일은 그보다 길 수 있다(#165).
    그 창 라벨은 **하루에 두 번** 뒤집혔다('누적'→'30일'→창 주장 금지) —
    라벨을 고칠 때마다 "이건 우리가 잰 것인가"를 물었으면 한 번에 끝났다.

### 실수 #323

323. **공용 파일의 줄 수를 전용 DB 와 비교하면 판정이 영영 한 갈래에 고인다 —
    그리고 내가 그 오진을 사용자에게 그대로 전달했다**(2026-09-10 수출입 헤더):
    `--why` 가 `⑨ ingest — inbox 엔 DB 최신 이후 438줄이 있는데 DB 에 안 들어옴` 을
    찍어 dashboard-refresh 를 보게 했는데, VM systemd 로그는 그 서비스가 **매 사이클
    정상 종료**(전 패스 `status=0/SUCCESS`)였고 ingest 계정도 한 줄을 안 흘리고
    있었다 — 실측 `7,499(main) + 387(형제 DB) = 7,886(grouped)` 로 딱 맞는다.
    원인: `inbox.jsonl` 은 관세청 BeOn + 나쁜양파 15종 **공용**인데 `store.db` 는
    관세청 전용이라, 줄 수를 그냥 세면 다른 소스 트래픽이 늘 '안 들어간 줄' 로
    잡힌다(#45 총계와 소계가 다른 모집단). 진짜 상태는 `channel_quiet` 였다.
    대응: 갈래를 ingest 가 **실제로 쓰는 게이트**(`parser.parse_caption`)로 가르고
    (#35), 화면은 두 모집단을 나란히 찍는다(#45 둘 다 말할 것). 파서를 못 부르면
    폴백하되 폴백했다고 밝힌다(#12·#165).
    ⚠️ **그리고 나는 `activating/start` 스냅샷 하나를 보고 "서비스가 물려 있다"고
    사용자에게 단정했다** — 렌더가 3분 걸려 5분 주기의 절반을 차지하니 그 상태는
    **정상 실행 중**이었다. 순간 상태로 지속 상태를 말하지 말 것: `ActiveState` 는
    표본이고, 지속 여부는 `journalctl` 의 시작·종료 쌍이 답한다(#86 아는 쪽에 물어라).
    ⚠️ 픽스처가 눈이 멀 뻔했다 — 내가 **지어낸 캡션**이라 KR 표본도 파서가 None 을
    줘서, '필터가 걸렀다' 가 아니라 '아무것도 파싱 안 됐다' 로 통과할 뻔했다
    (#155·#91c). 픽스처가 실제로 두 모집단을 가르는지 **먼저 단언**하고, 반대 증거
    (진짜 ingest 지연은 여전히 잡히는가)를 같이 둘 것(#25·#47).
    ⚠️ 갈래를 맞게 뒤집어도 **사유 문구가 거짓이면 처방이 틀린다** — 파일이 439줄
    있는데 "파일 없음/빈 파일" 이라 적어 운영자를 경로 확인으로 보낼 뻔했다(#292).
    ⚠️⚠️ **배포 직후 실측이 한 겹을 더 벗겼다** — 관세청 모집단으로 갈랐더니 `21줄`
    이 남아 판정이 여전히 `ingest` 였는데, 같은 VM 의 ingest 카운터는 `inserted:0 ·
    already_present`(= 이미 있는 행)였다. inbox 의 `date` 는 **중계 시각**이고 DB 의
    `posted_at` 은 `forward_origin_date`(원 게시 시각)라 **시계가 다르다**: 08:47 글을
    08:59 에 전달받으면 시각 비교로는 '나중 줄' 이다. ingest 의 멱등 키는
    `(source_chat_id, source_message_id)` UNIQUE + DO NOTHING 이므로 **식별자로
    세야** 한다(#35). 일반화: '아직 처리 안 됨' 을 **시각으로 재지 말 것** — 두 시각이
    같은 시계인지부터 묻고, 아니면 제품의 멱등 키로 물어라.
    ⚠️ 그리고 `message_id` 가 없는 줄은 **식별 불가**지 미적재가 아니다 — 모르는 것을
    결함으로 세면 없는 결함을 만든다(#54). 따로 세어 화면이 밝힌다.
    ⚠️ 픽스처가 또 두 번 눈이 멀었다: (a) 손으로 짠 `INSERT OR IGNORE` 가 NOT NULL
    위반을 **조용히 삼켜** 아무것도 안 심었다 → 제품 삽입 경로(`alert_to_row` +
    `upsert_alert`)를 쓰고 **심겼는지 되읽어 단언**할 것 (b) stub 만 두고 alerts 표를
    비워 둔 앞선 픽스처는 이미 적재된 행을 '미적재' 로 만들었다(#155).
    ⚠️ `sqlite3.Connection` 은 C 불변 타입이라 메서드 monkeypatch 가 안 된다(실측
    TypeError) — 래퍼로 감쌀 것. 그리고 `header_facts` 는 `open_db` 를 **함수 안에서**
    import 하므로 모듈 속성이 아니라 **원천 모듈**을 갈아끼워야 한다.
    ⚠️ 판정이 확정된 뒤(`DB 에 없는 것 0건 · channel_quiet`) 같은 출력에서 두 가지가
    더 나왔다: (a) ④ 는 `inbox 최신 08-28`(전 소스)인데 ⑨ 는 `inbox 최신이 50일 전`
    (관세청)이라 **한 화면의 두 줄이 다른 말**을 했다 — 판정 문구는 자기가 쓴 모집단을
    이름으로 밝힐 것(#34) (b) 더 중요한 건, `trade.bot` 한 프로세스가 관세청 BeOn +
    나쁜양파 15종을 **같은 채널로** 받아 inbox 에 쓰는데 **16개 소스가 13일째 전부
    조용**했다는 사실이다. 그건 원천 채널이 아니라 중계 경로 신호인데 ⑦ 이 그 중계
    리스너(beon·badonion) 둘을 **아예 안 묻고** 있었다(#316 스코프를 추측하지 말 것).
    일반화: '한쪽이 조용하다' 를 말하기 전에 **그 파일에 쓰는 다른 소스들도 조용한지**
    볼 것 — 전부 조용하면 원천이 아니라 공용 경로다.
    ⚠️⚠️ 배포전 독립 리뷰가 **내가 '읽기 전용' 이라 적은 그 진단에서 쓰기를** 찾았다:
    `header_facts` ⑧ 이 존재 확인 없이 `customs.session()` 을 열어 customs.db 와
    스키마를 만들고 있었다(#264). 형제 둘(`_load_industry_html`·`audit_provisional`)은
    이미 `exists()` 로 막고 있었는데 새 코드만 안 했다(#38). 저자 테스트가 못 잡은
    이유는 둘 다 `customs.session` 을 monkeypatch 해 **진짜 open_db 가 한 번도 안
    돌았기** 때문이다(#20 스텁은 배선을 못 잰다) — 파일이 생기는지 **파일로** 볼 것.
    그리고 그렇게 생긴 빈 DB 는 다음 날부터 `수집 시각 미기록 ❌` 를 영원히 낸다:
    키(`TRADE_DATA_GO_KR_KEY`)가 없으면 수집이 **설계상** 안 도는데도 ❌ 였다(#260)
    → 키 유무로 갈라 ⚠️ 로 사실만 적고 결함으로 세지 않는다(#41 사실은 그대로).

### 실수 #336

336. **`make test` 한 번이 바깥 원천에 409건을 내보내고 있었다 — 아무도 안 재서
    몰랐다**(2026-09-11 사용자 지적 "기존 테스트 5개는 그대로 두었습니다. 고칠까요?"
    → 실측은 **164개 테스트 · 409요청 · 22호스트**였다): stock.naver.com 84 ·
    sec.gov 70 · finance.naver.com 49 · **openapi.koreainvestment 인증 토큰 15**.
    남의 레이트리밋을 태우고 과금 경로를 밟고 운영 캐시를 오염시킨다(#30·#312 —
    그 둘이 개별 사례를 한 번씩 막았지만 **목록형 방어**라 새 테스트는 못 잡는다,
    #24). `tests/conftest.py` 가 두 전송 계층(requests + yfinance 1.6 의 curl_cffi)
    을 한 번에 막는다. 부작용 0: 이 샌드박스는 원래 전 요청이 프록시 403 이라
    3,786개가 이미 실패 경로를 견디고 있었고, 던지는 예외를 **원천 실패와 같은
    타입**(`ConnectionError`)으로 맞추면 호출부 계약이 안 바뀐다. 실행시간은
    364.7초 → 158.1초.
    ⚠️ **테스트별 fixture 로는 못 막는다** — 렌더가 띄운 daemon 스레드가 teardown
    **뒤에** 친다(164건 → 1건이 그 경로로 남았다, #312 그대로). 세션 스코프로
    한 번 걸고 **되돌리지 않는다**.
    ⚠️ 차단은 **조용하지 않아야** 한다 — 어느 테스트가 스텁을 빠뜨렸는지 예외
    문구가 말하고 `_BLOCKED` 에 남긴다(#43·#82). 단 그 목록·문구는 CI 로그에
    그대로 실리므로 **쿼리스트링을 뗀다**(키가 쿼리로 가는 원천이 여럿이다,
    §Secrets). 그 계약의 픽스처는 **키가 있는 URL** 이어야 한다 — 쿼리 없는
    URL 로 재면 strip 을 지우는 뮤테이션이 그대로 통과한다(실측, #91c).
    ⚠️ 회귀가 conftest 를 `import conftest` 로 집으면 ModuleNotFoundError 이고,
    경로로 새로 import 하면 **딴 인스턴스**라 `_BLOCKED` 기록 검사가 아무것도 안
    잰다 — 이미 로드된 그 모듈을 `__file__` 로 찾을 것(#35).
    ⚠️⚠️ **#24 를 고치는 fix 안에서 #24 를 냈다**(독립 리뷰가 배포 전 실측): 두
    패치는 `requests`·`curl_cffi` 라는 **이름 목록**이고 회귀가 그 목록을 그대로
    단언해, 세 번째 전송 계층이 들어와도 눈이 먼다 — `bot/` 엔 이미 httpx 20곳 ·
    `urllib.request` 8곳이 있고 `Session.send`·aiohttp·raw socket 은 애초에 못
    본다. 열거가 아닌 그물을 **소켓**(`socket.connect`, 루프백 예외)에 두고
    회귀는 그걸 재게 했다. 일반화: 가드를 짤 때 "이게 목록인가"를 먼저 물을 것.
    ⚠️ **비밀값은 쿼리스트링에만 있지 않다** — 이 레포는 텔레그램 토큰을 URL
    **경로**에 박는 호출부가 20곳이다(`/bot<id>:<token>/sendMessage`). `split("?")`
    로 자르면 그게 트레이스백·CI 로그에 그대로 실린다(§Secrets). 리다이렉션은
    문자열 자르기가 아니라 **구조**로(urlsplit + 조각 모양 판정, #65·#60).
    ⚠️ **"세션 스코프여야 한다" 를 세 곳에 적어 놓고 한 곳도 재지 않았다** —
    `monkeypatch.undo()` 는 스코프와 무관하게 복원하므로 그 테스트는 아무것도
    안 잰다. 리뷰가 함수 스코프 fixture 판으로 되돌려도 전부 green 임을 실측으로
    보였다(#165 주장과 측정은 다르다 · #286). 계약이 '되돌리지 않는다' 면
    **지금도 설치돼 있는가 + 복원 경로가 없는가**를 봐야 한다.
    ⚠️ 차단을 `tests/conftest.py` 에 두면 `pytest bot/tests`(선언된 testpath)가
    **무방비**다 — 지금 걸리는 건 수집 순서의 우연이다. 루트 `conftest.py` 로.
    ⚠️ `_BLOCKED` 를 **위치·개수**로 단언하면 흔들린다: 전 세션 604건 중 216건
    (36%)이 다른 스레드에서 들어온다(그 스레드들이 이 차단의 대상이다) —
    `_BLOCKED[before:]` 에 **포함**으로 볼 것(#128 순서로 재지 말 것).
    ⚠️ 그리고 이름으로 친 소켓 테스트는 DNS 가 `connect` 앞에서 먼저 실패해
    가드까지 못 간다(실측 `Errno -2`) — 리터럴 IP(RFC 5737)로 재야 **가드**를
    재는 것이다(#91b).

### 실수 #340

340. **SPA 전환을 고치며 배운 것 — 그리고 테스트가 `/nonexistent-xyz` 를 진짜로
    만들고 있었다**(2026-09-11, #338 의 결말): 실측이 경로를 확정해 업종은
    `stock.naver.com/api/domestic/market/upjong/list?pageSize=100`(79행),
    리서치는 `m.stock.naver.com/api/research/{company|industry|invest}`(각 20행)로
    갈아탔다. 규칙 넷:
    (a) **기본 페이지를 전수로 착각하지 말 것** — 무인자는 20행이고 그게 기본
        페이지 크기다. 20행으로 상위·하위 10을 매기면 화면이 조용히 틀린다(#45).
        `size`/`perPage`/`page` 는 안 먹고 `pageSize` 만 먹는다(실측). 20행이
        오면 값은 주되 **전 업종이 아니라고 말한다**(#43).
    (b) **기준시각은 원천이 찍은 것**(`thistime`)을 쓸 것 — 렌더 시각을 쓰면
        수집이 멈춘 날도 방금처럼 보인다(규칙 10b·#304).
    (c) **원천이 준 링크를 버리지 말 것** — 옛 `*_read.naver?nid=` 를 조립하면
        SPA 전환으로 죽은 주소라 사용자가 클릭해 빈 페이지를 본다. 응답의
        `endUrl` 이 이미 있었다(#150 우리가 그걸 다 쓰고 있나).
    (d) `_get2` 는 **euc-kr 강제 디코딩**이라 JSON 에 그대로 쓰면 한글이 깨진다
        — 같은 함수에 분기를 넣지 말고 `_get2_json` 을 따로 둘 것(갈래 어휘는
        공유, #38). 그리고 **빈 리스트는 실패가 아니다**(#54).
    ⚠️ **스텁은 제품이 실제로 부르는 그 함수를 겨눠야 한다** — 옛 테스트가
    `_get2`·`parse_groups` 를 스텁했는데 제품이 JSON 경로로 옮겨가 그 스텁이
    아무것도 안 막았고, **테스트가 진짜 네이버를 쳤다**(#312 차단 가드가 잡았다
    — 새 가드가 켜지자마자 일한 사례, #87a).
    ⚠️⚠️ 그리고 캐시 디렉터리를 `/nonexistent-xyz` 로 둔 테스트가 있었는데,
    쓰기 경로의 `mkdir(parents=True)` 가 **그 디렉터리를 실제로 만들어** 같은
    세션의 다음 테스트가 그 캐시를 읽고 엉뚱한 값을 받았다(#30 의 변종 —
    '없는 경로' 라는 이름이 안전을 보장하지 않는다). 임시 경로는 **`tmp_path`**
    로. 이름이 아니라 동작으로 판정할 것(#25).
    ⚠️ 진단(`--check`)도 같은 커밋에서 새 경로로 옮길 것 — 옛 경로를 계속 재면
    고친 뒤에도 영원히 ❌ 다(#35·#169).


### 실수 #343

343. **값이 없으면 카드가 조용히 사라지고, 고친 것은 캐시가 가리고, 죽은 경로는
    '갱신 중' 이라고 거짓말했다 — 한 화면의 네 축이 같은 병이었다**(2026-09-12
    사용자 "카드가 하나 빠진걸 보니 팔라듐 업데이트안됐는데 + 한국업종별 시세는
    오늘 기준이 아니라 어제기준인데? … 리서치액션, 그전에는 한국은 **일주일치**
    긁어왔어"):
    (a) **팔라듐** — `_yf_monthly_batch` 의 캐시가 **파일명만으로** 키를 잡아,
        카드 목록에 `PA=F` 를 더해도 `{DX-Y.NYB: …}` 를 그대로 조기반환했다
        (#18·#21b·#95·#124·#198·#216·#233 에 이은 여덟 번째). 그리고 값이 없으면
        `if value is None: continue` 로 **카드가 통째로 사라져** 사용자는 기능이
        삭제된 줄 안다(#43 침묵이 최악). 손 bump 는 이 레포에서 다섯 번 졌으므로
        **요청 티커 집합으로 판정**한다(#119 규율을 구조로). 카드는 계속 안 그리되
        **뺐다는 사실과 갈래**를 말한다 — 미매핑 sid 는 대조군(달러인덱스)까지
        이름을 대야 '배치 전체 실패' 와 '심볼 문제' 가 갈린다(#82·#143).
    ⚠️ 같은 자리에서 **더 나쁜 것**을 찾았다: `_yf_close` 가 요청한 컬럼이 없으면
    `sub.iloc[:, 0]` 로 **첫 컬럼**을 돌려줬다. yf 전용 티커가 하나뿐일 때만 우연히
    안전했고, 팔라듐이 더해져 둘이 되자 **달러인덱스 값이 팔라듐 카드에 앉는다** —
    사라지는 것보다 나쁜 '조용히 틀린 카드' 다(#46·#33). 요청한 게 없으면 **없는
    것**이다. 티커를 하나 더하는 변경은 '배치가 몇 개일 때만 맞는 코드' 를 깨운다.
    (b) **리서치 20건** — SPA 전환 커밋이 옛 `for page in range(1, max_pages+1)`
        루프를 통째로 지워 KR 3탭이 하루치로 줄었다. 복원할 것은 옛 URL 이 아니라
        옛 **중단 조건**이고, 그걸 갈래 이름으로 부른다(`page_ignored`/`source_end`/
        `window_end`/`limit`/`max_pages`) — **`max_pages` 만 절단**이고 나머지는
        아무 말도 안 한다(#25·#260). 어떤 페이지 파라미터를 받는지는 **안 쟀으므로**
        (#12·#165) 하나를 박지 않고 `pageSize`→`page` 순으로 시도해 **결과로 판정**
        한다(첫 행이 같으면 파라미터가 무시된 것 — 행 수만 보면 못 가른다, #25).
    ⚠️ 절단 경고의 기준을 '한 응답이 상한에 닿았나' 로 두면 쪽을 잇는 순간 **영구
    오탐**이다(100행을 정상 수집해도 "한 번에 100건만"). 기준은 **'창을 다 못 덮고
    멈췄나'** 이고, 그 사실은 행 수에서 파생할 수 없으므로 **캐시에 같이 굽는다**
    (두 층 다 — #342 의 다음 층). 쪽을 합치면 '날짜 내림차순' 이 처음으로 부담을
    지므로 **정렬 뒤에 자른다**(독스트링이 약속만 하고 정렬은 한 번도 안 했다).
    행이 20→수백이 되면 상세(HTTP 1건/행)가 그만큼 곱해진다 — **예산을 같이**(#116).
    (c) **5탭** — 다섯 중 `/theme` **하나만** 죽은 HTML 이었다(나머지 넷은 JSON).
        그런데 그 탭의 라벨이 '업종별 시세(전체)' 이고 **업종 JSON 은 이미 살아
        있었다** — 새 원천을 찾을 게 아니라 이미 부르는 호출이 무엇을 더 주는지
        보는 자리다(#150·#141). 그리고 화면의 '갱신 중' 은 거짓이었다: 그 문구가
        뜨는 경로는 **동기 수집이 이미 끝나고 빈손**인 쪽이라 진행 중인 갱신이
        없다 — 기다리면 채워질 것처럼 읽혀 사용자가 새로고침할 때마다 죽은 7쪽을
        다시 긁는다(#25). 갈래는 수집기가 싣고 화면은 따른다(#35·#136).
    ⚠️ 부제를 **리터럴**로 적어 두면 32시간 낡은 스냅샷 위에서도 '장중 30초 캐시'
    라고 주장한다(#55) — 부제와 패널 제목이 **같은 판정값**에서 나와야 한다(#38).
    ⚠️ 그 탭이 조용히 낡은 이유가 감사에도 있었다: `/health` 의 테마 점검이 제품
    파서 대신 **부분문자열**(`"type=theme" in text`)을 봐서 셸이 그 글자를 담기만
    해도 ✅ 였다(#35·#75). 그리고 `/nxt`·`/krprepost` 의 원천은 점검 목록에 **한
    줄도 없었다**(#24). 영향 문구는 어느 페이지도 안 서빙하는 '상한가' 를 지목하고
    있었다 — **안 재는 기능을 영향으로 적지 말 것**(#292), 답은 문구를 지우는 게
    아니라 **재는 것**이다(업종맵을 점검 항목으로 올렸다).
    (d) **재킥** — `kr_industry_map` 의 in-flight 가드는 `finally` 에서 즉시 풀리고
        빌드가 0건이면 캐시도 안 써져, **렌더마다** 죽은 URL 로 스레드가 나갔다.
        실패에 **백오프**를 달고(#72 차단기) 사유를 **디스크에** 남긴다 — 모듈
        전역만 보면 `--check`(별도 프로세스)에서 항상 빈 문자열이라 그 줄이 한
        번도 안 찍혔다(#123 계열의 프로세스판).
    ⚠️ `_extract_rating` 의 `except: return None` 은 로그 한 줄도 안 남겨, 의존성이
    빠진 배포가 나가면 **모든 Rating 이 조용히 'N/A'** 가 된다(#12). 그리고 그
    침묵 때문에 같은 파일의 '없으면 None' 테스트 둘이 **tautology 로 green** 이었다
    (#291). 의무 게이트(`make test`)와 도구 기본값(`pytest`)이 **다른 슈트**를 돌아
    `bot/tests` 182건이 아무것도 막지 않고 있었다. ⚠️ 그런데 **합치면 깨진다** —
    `bot/tests/conftest.py` 가 `sys.modules` 에 mock 을 **모듈 레벨로** 꽂아
    같은 세션의 `tests/` 73건이 그 mock 을 진짜로 읽는다(실측, #30 의 세션판).
    게이트는 **별도 프로세스 둘**이고, 합치지 않는 이유를 pytest.ini 에 적었다
    — 안 적으면 다음 사람이 다시 합친다(#55). 범위를 넓힐 땐 "그 둘이 한
    프로세스에서 같이 설 수 있나"를 먼저 물을 것.
    ⚠️⚠️ **독립 리뷰가 배포 전에 7건을 더 잡았다** — 셀프리뷰 green + 뮤테이션
    14종 발화로도 안 잡힌 것들이고, 넷은 **내가 이번에 넣은 가드가 스스로
    망가지는** 형태였다: (a) 백오프 판정이 "이번 프로세스가 겪었으면 `time.time()`"
    을 돌려줘 `now−now=0` → **영원히 안 풀린다**(#178 — 가드가 매번 발동하면
    계열이 멈춘다. 시각은 **기록**이 말해야 한다) · 성공에도 시각을 찍으면 그게
    '마지막 실패' 로 읽히고, 예외 경로에 기록이 없으면 백오프가 아예 안 걸린다
    (b) 캐시 커버리지를 **요청 집합**으로 판정하면 **저장도 요청 집합**으로 해야
    한다 — 영구히 안 오는 심볼 하나가 캐시를 영원한 미스로 만들어 30초마다
    download 가 나간다(막으려던 60배 트래픽 그 자체) (c) **캐시 키를 나누는 축은
    결과를 바꾸는 인자 전부**여야 한다 — 감사가 `fetch_detail=False` 로 부르는데
    그 축이 키에 없어 **상세 없는 행을 공용 캐시에 구웠고**, 라이브 탭이 비면서
    거짓 경고까지 떴다(#61 의 반대 방향 — 덜 나눠서 생긴 오염) (d) 중간 쪽의
    429·타임아웃이 `rawp=0` 이라 '원천에 그게 전부' 로 분류돼 **창을 조용히
    잘랐다** — 두 갈래는 처방이 정반대다(#82·#136).
    ⚠️ 그리고 그 fix 의 회귀도 **처음엔 눈이 멀었다** — 백오프 테스트가 전역
    사유를 지우고 재서, 버그를 복원해도 통과했다(#91c 깨지는 값까지 밀어 볼 것).
    가드를 고쳤으면 **그 가드가 실제로 발화하던 상태**를 재현해 뮤테이션을 태울 것.
    ⚠️ 뮤테이션에서 또 배웠다: 순수 함수와 렌더만 재면 **배선을 떼는 변형을 못
    잡는다** — `dropped.append(...)` 를 `pass` 로 바꾼 변형이 141건을 전부 통과했다
    (#20, 수집기를 통째로 태우자 발화). 그리고 소스에 그 글자가 있는지로 재면
    **독스트링이 대신 만족**시킨다(#59b — '값 수집' 접두가 그랬다).

### 실수 #344

344. **별표는 목록 속성인데 목록 캐시를 태웠고, 규칙 표가 결과 카드와 모순돼
    보였다**(2026-09-11 관심종목 ★ · Breadth 두 표 — 사용자 "종목앞에 별표로
    중요표시해서 내가 선택할수 있게 해주고, 위쪽에 중요표시것만 선택해서 볼수있게
    필터같은거 만들어줘" · "위에 표랑 아래 표랑 전략이 다른데. 최종 투자비중이랑
    현금비중이랑. 어떤 로직이야?"):
    (a) `market_favorites._save` 는 **추가/삭제/순서변경** 뒤 `_FAV_CACHE` 를
        지우라고 만든 choke point 다(2026-06-16 '휴지통 작동 안 함'). 별표를 그
        경로로 쓰면 별 한 번에 **139종목 가격이 통째로 `—`** 가 됐다가 데몬이
        수십 초에 걸쳐 채운다 — 기능이 아니라 고장으로 보인다. 별표는 목록·순서를
        안 바꾸는 **표시 속성**이므로 `invalidate_cache=False` 로 쓰고, 읽는
        지점(`favorites_rows_with_as_of`)에서 **디스크 정본으로 덧입힌다**
        (`apply_stars`). 그러면 메모리 캐시(3분)·디스크 스냅샷(6시간) 어느 층이
        행을 주든 화면의 별표가 정본과 같다 — 이 레포에서 캐시가 fix 를 가린
        실패가 여섯 번(#18·#21b·#95·#124·#198·#216) 반복됐고 매번 규율로 졌으므로
        **구조로** 막는다(#119).
    ⚠️ 덧입히기는 **새 dict** 로 해야 한다 — `_FAV_CACHE` 가 들고 있는 dict 를
    제자리에서 고치면 별표가 캐시에 구워져 (a)의 요점이 사라진다.
    ⚠️ 별표 write 와 데몬의 `name_kr` 영속은 둘 다 디스크를 read-modify-write
    한다 — 그 **두 지점만** 같은 락 아래 둔다(사용자가 손으로 찍은 표시가 조용히
    사라지는 것은 창이 좁아도 허용할 수 없다, #295). `Lock.locked()` 로 풀면
    **남이 잡은 락**을 풀 수 있으므로 우리가 잡았는지를 따로 들고 있을 것.
    (b) Breadth 보드의 아래 표는 **구간별 규칙**(고정 문구)이고 위 stat-grid 는
        **오늘 그 규칙을 적용한 결과**다. 회복 구간 규칙은 '총 50%' 인데
        회복조건(현재가>MA120 · 6개월 RS>0 · 20일 고점 대비 −15~−5%) 셋을 다
        채운 과거 리더가 0개면 `decide` 가 기본값(0% · 현금 100%)을 그대로 낸다
        — 둘 다 맞는데 나란히 놓이면 사용자는 한쪽이 틀렸다고 읽는다(#33·#34).
        규칙 문구는 그대로 두고 **활성 행에만** `→ 이번 판정 N%` 를 덧붙인다
        (비활성 행은 오늘 적용되지 않았으므로 '이번 판정' 이라 부를 값이 없다, #165).
    (c) 그 질문의 답(`cash_reason`)은 **2026-09-07 에 이미 만들어 놓고**(#298)
        확정 이력 표에만 배선해, 정작 사용자가 보는 라이브 카드는 `투자 대상
        없음(현금)` 까지만 적고 있었다 — #123·#129·#189·#228·#234·#292 에 이은
        일곱 번째 '계산해 두고 표시에 안 배선' 이다.
    ⚠️ 카드 줄과 표 각주는 **클래스를 공유하지 않는다** — `.si-note` 는 `_BS_CSS`
    에서 `.bs-tbl .si-note` 로만 정의돼 있어 표 밖에서 쓰면 본문 크기로 뜬다
    (#201·#273 "지금 쓰는 곳이 전부 그 번들인가").
    (d) KR 카드의 심리지표를 CNN F&G → **VKOSPI**(KRX 산출·KIS 제공)로 바꿨다.
        시장 게이트를 렌더러에 흩지 않고 `_SENTIMENT_KIND` 레지스트리 한 줄로
        두고, 값은 시장타이밍 보드가 쓰는 `market_timing.fetch_vkospi_rows` 를
        그대로 재사용한다(지수코드·타당범위 가드·1시간 캐시가 거기 있다, #38).
    ⚠️ VKOSPI 를 못 받아도 **CNN F&G 로 내려가지 않는다** — 그건 사용자가 바로
    그 이유로 빼 달라고 한 값이라, 폴백하면 고치려던 거짓말이 되살아난다(#136).
    대신 **왜 없는지**를 그 자리에 적는다(#43·#82).
    ⚠️ 한 줄짜리 곁들이가 `_market_section` 안에서 던지면 **카드가 통째로**
    사라진다(#315) — `sentiment_text` 는 숫자가 아닌 값을 받아도 던지지 않고
    사유로 말한다.
    ⚠️ 회귀 쪽 교훈 둘: 정의부터 **창을 잘라** 재는 소스 검사가 또 무너졌다
    (`_save` 에 스위치와 사유 독스트링이 붙자 무효화 줄이 600자 창 밖으로 밀려
    멀쩡한 코드가 빨간불 — #60 의 네 번째). 계약은 "목록을 바꾸면 다음 조회가
    디스크를 본다" 이므로 세 진입점을 **태워서** 값으로 고정했다(#19·#222).
    그리고 표 맨 앞에 칸을 하나 더하자 `split("</td>")[0]` 로 셀을 집던 회귀가
    **남의 칸**을 재기 시작했다 — 셀은 위치가 아니라 **내용으로** 찾을 것(#46).
    ⚠️ 관심종목 행 JS 하네스는 `starBtn` 을 스텁해야 한다 — 시그니처가 늘면
    하네스도 **같은 커밋에서** 고칠 것(#183).
    ⚠️ **운영 디스크 캐시 오염은 함수 스코프 fixture 로 못 막는다** — 이 턴의
    게이트가 `test_backoff_actually_expires` 에서 **단독 green / 전체 red** 였고,
    파 보니 원인은 테스트 오염이 아니라 `make test` 가 운영 캐시
    `~/.tradingagents/cache/naver_sector/kr_industry_fail.json` 에 **실패 도장**을
    남기는 것이었다(#30). 그 도장은 15분 백오프의 근거라 **다음 운영 실행이
    업종맵을 안 만든다** — 테스트가 프로덕션 동작을 바꾼 것이다. 함수 스코프
    fixture 로는 못 막는다: 렌더가 띄운 **daemon 스레드**가 teardown 뒤에 쓴다
    (2026-09-11 네트워크 차단을 세션 스코프로 올린 것과 **같은 이유**이고, 같은
    증상으로 확인했다 — 클래스 단독 실행에선 안 나오고 여러 클래스를 이어 돌릴
    때만 파일이 생긴다). 루트 `conftest.py` 에서 **import 시점에 갈아끼우고
    되돌리지 않는다**.
    ⚠️ 그 리다이렉트 목록은 **완전하지 않다**(`bot/` 에 `~/.tradingagents` 아래를
    가리키는 모듈 상수가 131개 · AST 실측). 전부 갈아끼우려면 conftest 가 130개
    모듈을 import 해야 해 비용이 크다 — 그러니 "테스트는 운영 캐시를 못 만진다"고
    **주장하지 않는다**(#286 지시서가 자기 자신에 대해 거짓이면 다음 사람이
    가드를 건너뛴다). 회귀는 "상수가 지금 홈 밖을 가리키는가"로 **값을** 본다
    (`setattr` 이 오타로 아무 데도 안 걸리는 것을 이름 검사로는 못 잡는다, #25·#54).
    ⚠️ 전체 실행에서만 빨간불인 테스트를 보면 "테스트 오염" 으로 단정하지 말고
    **무엇이 달라져서 그랬나**를 먼저 물을 것(#311 그대로 — 이번에도 답은
    진짜 결함이었다).
    ⚠️⚠️ **독립 리뷰가 배포 전에 Blocking 1 · High 1 · 그 밖 7을 더 잡았다**
    (셀프리뷰 green + 뮤테이션 13종 발화로도 안 잡혔다):
    (e) **Blocking** — 락은 **전원이 참여할 때만** 상호배제다. 별표와 데몬
        둘만 걸고 "추가·삭제·순서는 사용자 클릭이라 서로 직렬" 이라고 주석에
        적었는데, `ThreadingHTTPServer` 는 `/api/favorite_add` 와
        `/api/favorite_star` 를 **다른 스레드**로 처리하고 `add_favorite` 는
        `_load()` 뒤 yfinance `.info`/`.calendar` 로 **수 초**를 쓴 다음 그
        낡은 목록을 저장한다 — 그 사이 찍은 별표가 사라지고(화면은 ★ 를
        칠했다가 60초 뒤 ☆ 로 되돌아간다, 오류 한 줄 없이), 반대로 별표
        쓰기가 방금 담은 종목을 지운다. 리뷰가 둘 다 **재현**했다. 처방:
        네트워크는 락 **밖**, 쓰기 직전 락 안에서 **다시 읽는다**(중복 검사도
        다시). 회귀는 넓은 창(느린 yf 스텁)을 쓰는 `add_favorite` 는 **두
        스레드로 값**을 재고, 서브밀리초인 `remove`/`reorder` 는 **AST 전수**
        ("`_save()` 는 `with _DISK_LOCK:` 안에서만")로 못박는다 — 그 가드가
        켜지자마자 `_cli_sort_saved` 를 잡았다(#87a).
        ⚠️ 주석이 자기 자신에 대해 거짓이면 다음 사람이 그대로 믿는다(#286).
    (f) **High** — `장중` 을 `종가` 라고 적었다. 시장타이밍 보드가 같은 원천
        (`fetch_vkospi_rows`)에 대해 이미 `vol_asof_label` 로 그 판정을 갖고
        있었는데(사용자 2026-08-20 "VKOSPI 가 현지 10:26 에 08-20 종가" 가
        그 함수를 만든 이유다) 리터럴로 복제해, **한 카드가 두 말**을 했다 —
        기준일 줄은 `🕒 장중(미확정)`, VKOSPI 는 `2026-09-11 종가`(#43a·#34·#38).
        3시간 주기 재생성은 KR 장중(09:00~15:30)에도 돈다. **그리고 내 테스트가
        `note == "2026-09-10 종가"` 라는 리터럴을 못박아 버그를 축복**하고
        있었다(#19) — 라벨은 `vol_asof_label` 과 같은지로 재고, 장중 케이스를
        따로 둔다.
    (g) 그 밖: 레지스트리 단일출처 AST 단언이 **아무것도 안 쟀다**(`Compare.left`
        가 하필 그 이름일 때만 봄 — 게이트를 흩는 변형 셋이 전부 통과, #291)
        → 레지스트리에 **합성 시장**을 넣어 규약을 태운다(#85) · `acquire()` +
        플래그는 그 둘 사이의 시그널에 락을 **영구히** 남긴다 → `with` ·
        재렌더 뒤 응답이 오면 리스너가 겹쳐 클릭당 POST 가 두 번(`isConnected`
        로 빠진다) · 정렬 안 되는 `<th>` 가 `.dtbl th` 어포던스(cursor·hover·
        `::after ↕`) 때문에 정렬되는 척했다 · 마지막 별을 지우면 필터만 남아
        **빈 표**가 됐다(나라 필터처럼 자동 해제) · 빈 VKOSPI 의 세 갈래
        (크리덴셜·빈 응답·**타당범위 밖**=지수코드 변경)를 한 문구로 뭉뚱그렸다
        (#82 — `vkospi_rows_with_reason` 으로 가르고 값만 쓰는 얇은 래퍼는
        남긴다, #129).

### 실수 #345

345. **감지기가 제 사건을 못 잡고, 사다리가 요구 대신 '아무거나'로 폴백하고,
    감사가 제 계약을 바꿨다**(2026-09-12 네이버 SPA — 사용자 "최근 리서치액션좀
    잘 좀 봐줘봐....목표가/투자의견 + 한국업종별 시세탭이랑...여기 원래 테마만
    있으면 돼. … 이거 여전히 최신꺼 못가져오는데...제발 제대로 좀 해줘" ·
    "관심종목 중요표시 오류나잖아.."):

**증상 셋과 원인**
- 테마 목록이 121,896B 를 받으면서 0행 → 34시간 낡은 스냅샷 서빙.
- 리서치 상세(목표가·투자의견)가 한 건도 안 읽힘.
- 관심종목 ★ 클릭 시 `중요표시 변경 실패` — **코드가 아니라 배포 문제**였다.
  VM 실측: `ExecMainStartTimestamp=01:37:43` vs `dashboard_server.py mtime=02:58`
  → 대시보드 프로세스가 80분 낡은 코드를 들고 있어 `/api/favorite_star` 가 404.
  `market.html` 은 `stock-bot` 이 굽고 `/api/*` 는 `stock-bot-dashboard` 가
  답하는데 `auto-update.sh` 가 후자를 `sudo -n systemctl restart` 로만 재시작한다.

**셀프리뷰가 잡은 것(6)**
1. `code_freshness._STARTED = time.time()` + 호출부의 **지연 import** → 배포 직후
   첫 요청이 시각을 찍어 `시작 > 소스 mtime` → 감지하려던 그 상태에서 `stale=False`.
   `/proc/self/stat`(22번 필드) + `/proc/stat` 의 `btime` 으로 실측하게 바꿨다.
2. 일시정지 중 사다리 전멸 → 탐색이 raw `requests` 로 정지된 호스트를 두드리고,
   '못 찾음' 메모가 6시간 냉각을 걸어 정지 해제 뒤에도 진짜 탐색을 막았다.
3. `_DETAIL_RUNGS` 모듈 전역 — 풀 워커 6개의 read-modify-write 로 수를 잃고,
   시장·산업·종목 세 화면의 건수를 한 통에 섞었다. 경로를 **행에** 싣도록 변경.
4. 프로브가 `collect_themes_json()` 을 3-튜플로 풀었는데 제품이 4개가 됨 →
   `ValueError` 가 `except` 에 삼켜져 멀쩡한 사다리가 '실행 실패' 로 찍혔다.
5. 테스트 스텁 3개가 이름이 바뀐 함수를 겨눠 실제 네이버로 샜다(conftest 가 잡음).
6. 경로 배지가 정상 경로에서도 늘 떴다.

**독립 리뷰가 잡은 것(요지)**
- **Blocking**: 소켓 테스트가 `_authorize()` 를 타는데 CLAUDE.md §Stance 가 VM
  `.env` 에 요구하는 `DASHBOARD_USER`/`PASSWORD` 가 있으면 401 → `make test` 가
  무관한 커밋에서 빨간불(#294). 실측 재현 후 harness 에서 인증을 끈다.
- **High**: 테마 사다리에 형제(`_UPJONG_DEFAULT_PAGE`/`_UPJONG_MIN_GROUPS`)의
  하한 가드가 없어, 원천이 `page` 를 무시하면 100개·`pageSize` 를 무시하면
  20개만 받고 `partial=False` 로 캐시에 구웠다(266개 중). 화면은 '전체 테마 N개'.
- **High**: 상세 사다리의 폴백 조건이 `tgt is not None or rating` — 부분 성공이
  **동작하는 폴백을 죽였다**. `and` 로 바꾸고 빠진 칸만 HTML 이 채우게 합쳤다.
- **High**: `/health` 의 테마 행을 `fetch_themes()` 로 바꾼 것이 (a) 캐시를 읽어
  34시간 낡은 스냅샷에 ✅ (b) 정지를 존중해 `/naverpause` 중에도 ✅ (c) 운영
  캐시에 쓰기 (d) `finance HTML` 버킷을 JSON 성공으로 채워 '`HTML 경로만 막힘`'
  영향 문구를 **영영 못 뜨게** 만들었다. raw 줄 + 스냅샷 줄 **두 줄**로 분리.
- **High**: `wrong_resource`·`_via` 스탬프가 헬퍼 테스트만 있어 **한 줄 삭제
  뮤테이션이 전 슈트를 통과**했다. 배선 E2E 로 보강(둘 다 발화 확인).
- **Medium**: 탐색이 청크에서 읽은 경로의 **호스트를 추측**해 `✅ 탐색됨` 으로
  메모했다 — 후보 × 검증된 두 호스트를 실호출해 값이 오는 것만 채택하게 변경.

**교훈**
- #35('감사는 화면이 쓰는 그 경로를')에는 **범위**가 있다 — 그 줄의 계약이
  '원천이 지금 답하나' 면 raw 여야 한다. 화면이 뭘 들고 있나는 별도 줄(#45).
- #136('폴백 조건은 요구 충족 여부')을 한 커밋 안에서 **한쪽에만** 적용했다.
  같은 규칙을 쓰는 자리를 그 커밋에서 전부 grep 할 것(#38·#147).
- 새 수집 경로엔 **형제의 가드를 그대로** 옮길 것.
- 진단은 반환 **모양**을 제품에서 파생시켜 대조할 것(#252).
- 테스트가 **레포가 요구하는 설정에서** 깨지면 시한폭탄이다(#294·#249·#291).


### 실수 #346

346. **이득 0인 파라미터가 400 을 불렀고, 화면은 그 400 대신 마지막 후보의
    404 를 적었고, 전멸한 사다리를 클릭마다 처음부터 다시 걸었다**
    (2026-09-12 테마 사다리 — 사용자 "+ 테마별 시세 들어가는데 시간이 꽤
    걸리는데 이건 양이 많아서 그런건가?"):

**실측(VM 프로브 v6)**
```
⑦ 테마 JSON 사다리
   · domestic/theme        ❌ 원천이 HTTP 400
   · domestic/theme(무접미) ❌ 원천이 HTTP 404
   · front-api/theme       ❌ 원천이 HTTP 404
⑨ 청크 2.8MB 에 테마 API 경로 0건
⑩ 테마 목록: ❌ 전멸 — 주소를 모른다
```
한 시간 전(v5) **같은 1순위 주소**가 100행을 줬다. 즉 주소는 살아 있고
우리 요청이 거부된 것이다.

**셋을 갈라 고쳤다**
1. **이득 0인 파라미터는 거절의 후보일 뿐이다.** v5 의 ④ 가 이 API 가족은
   `page` 를 **무시하고 `pageSize` 만** 듣는다고 확정했는데(업종
   `?page=2` → 첫 행 동일) 우리는 계속 `page` 를 얹고 있었다. 이득이 0인
   파라미터를 붙여 둘 이유가 없다 — **증명된 호출 모양**(업종
   `?pageSize=100`)을 그대로 쓰고, `page` 가 듣는 형제(리서치)를 위해
   2쪽부터만 얹는다(#61 이 파라미터가 비용의 어느 단계를 줄이나).
2. **여러 후보의 실패 중 '마지막' 을 적으면 처방이 틀린다.** 화면은
   `⚠️ 원천이 HTTP 404` 라고 말해 '주소가 사라졌다 → 새 주소를 찾아라'
   로 읽혔지만, 고칠 자리는 **우리 요청**이었다. `naver_diag.reason_rank`
   가 갈래를 순위로 매기고(0 일시정지 / 1 답했는데 거절·못 읽음 / 2 주소
   없음·못 닿음 / 3 판정 불가) `pick_theme_reason` 이 가장 행동 가능한
   것을 고른다. 같은 순위면 **앞 후보**가 이긴다 — 사다리 순서가 곧
   증명된 순서다(#275·#82).
3. **전멸한 결과는 캐시에 안 굽히므로(#280) 클릭마다 전부 다시 걷는다.**
   JSON 사다리(3후보 × 한도 3) + 죽은 옛 HTML 7쪽이 요청마다 순손실이었다
   — 사용자가 느낀 '느림'은 **데이터 양이 아니라 실패 재시도**다.
   `theme_fail.json` 에 사유·단 표시를 적고 **10분만** 믿는다(#152·#161·
   #303 실패는 짧게만).

**같이 지킨 것**
- 냉각은 **제품 경로에만**(`_collect_and_store`) — `--check`·프로브는
  `collect_themes` 를 직접 부르므로 재는 일이 막히지 않는다(#35 의 경계,
  #345c). `--check` ②-e 가 남은 냉각을 적어 ③ 실측과 화면이 왜 다른지
  운영자가 짐작하지 않게 한다(#82).
- **일시정지는 기록하지 않는다** — 원천이 죽은 게 아니라 우리가 안 물어본
  것이다(#79·#143·#345). 기억하면 정지를 푼 뒤에도 10분을 더 빈 화면으로
  산다. 판정은 `reason_rank == 0` 으로 재사용한다.
- 냉각 중엔 `refreshing` 을 **False** 로 — 배경을 띄워 봐야 즉시 빈손이다
  (#25 늘 뜨는 배지는 아무것도 안 재는 것과 같다 · #345).
- 화면이 **"직전 기록 · N분 M초 뒤 재시도"** 라고 밝힌다 — 이 사유는 지금
  잰 것이 아니다(#165 안 잰 것을 단정하지 말 것 · #43).
- 성공하면 기록을 지운다 · 냉각이 식으면 **반드시 다시 시도**한다(#178
  주기적으로 발동하는 가드는 계열을 영구히 멈출 수 있다 — 회귀가 반대
  증거로 고정).
- 단마다 **소요를 싣는다**(#69·#110 재지 않으면 '느리다'는 진단이 아니다).

**뮤테이션 6종 발화 확인**: 1쪽에 `page` 재부착 · `marks[-1]` 로 되돌리기 ·
냉각 단축회로 제거 · PAUSED 가드 제거 · `fetch_themes` 냉각 분기 제거 ·
화면 냉각 문구 제거.

**교훈**
- 원천이 무시하는 파라미터는 **무해하지 않다** — 이득이 0이면 지울 것.
  "지금 동작하니 그대로 두자"가 400 을 만나면 원인 후보가 하나 늘어난다.
- 실패가 캐시되지 않는 경로는 **재시도 비용이 요청마다 곱해진다**. '부분·빈
  결과를 굽지 않는다'(#280)와 '실패를 짧게 기억한다'(#303)는 **다른 축**이고
  둘 다 필요하다.
- 소요를 안 실으면 '느리다'의 원인을 사용자가 짐작한다 — 사용자는 '양'을
  의심했고 실제 원인은 **실패 재시도**였다.

### 실수 #347

347. **가설을 반만 적용해 고치려던 증상을 그대로 남겼다 — 그리고 그걸 지킨다는
    테스트가 눈이 멀어 있었다**(2026-09-12 #346 배포 전 독립 리뷰, High 2 · Medium 1):

**H1 — 가설이 참일 때의 경로를 끝까지 안 따라갔다**
#346 은 "같은 주소가 `page` 를 얹은 요청에 **400** 을 줬다" 를 가설로 세우고
**1쪽에서만** `page` 를 뺐다. 그런데 2쪽부터는 그대로 얹는다 — 즉 **가설이
참이면 2쪽이 반드시 400** 이고, 그 자리의 코드는
```python
if raw is None:
    return ((got, why, True, False) if got else (None, why, False, False))
                          #  ↑ saturated=False
```
라 한도 사다리(100→300→1000)가 **거기서 멈춘다**. 결과는 266개 중 **100개** ·
`partial=True` → `_collect_and_store` 가 캐시 거부 → **클릭마다 재수집**.
`themes` 가 비지 않으니 (c)의 냉각도 이 경로를 전혀 안 막는다 — 사용자가 신고한
'느림'이 **그대로 남는다**. 화면은 `전체 테마 100개` 라고 적고(#45), 그 파일의
주석이 스스로 "화면이 '전체 테마 100개' 라고 적고 순위가 조용히 틀린다" 고
경고하고 있었다.

리뷰 실측(시뮬레이터: `page` 붙으면 400 · `pageSize` 는 들음 · 266개):
```
요청 [{'pageSize':100},{'pageSize':100,'page':2}] → 100개 · partial=True
     ← pageSize=300 재질의 **없음**
렌더: h2 = "전체 테마 100개" · 사유 줄 비어 있음 · 1·2회차 모두 HTTP 2건(캐시 0)
```
처방: **1쪽이 상한까지 찼는데 2쪽을 못 받았으면 포화**다 — '목록 끝'이 아니라
더 있는데 못 받은 것이므로 한도를 올려 다시 묻는다(#136). 판정은 파싱 뒤 행
수가 아니라 **원천이 준 원시 수**(`n_first`)로(#342 — 못 읽은 행 하나가 경고를
끄면 안 된다).

**H2 — 픽스처가 '이미 정답인 순서'면 정렬을 지워도 통과한다**
`pick_theme_reason` 회귀 둘이 **첫 후보가 이미 가장 행동 가능한** 픽스처였다
(400 이 1순위 / 동순위). `sorted(fails, key=…)[0]` → `fails[0]` 뮤테이션이
**42개를 전부 통과**했다(리뷰 실측 — 다른 13종은 잡혔다). 그런데 현실 배치는
정확히 그 반대다: `rungs = [("탐색됨", memo)] + _THEME_API_RUNGS` 라 **옛 탐색
메모가 0단**이므로 0단 404 · 1단(증명된 주소) 400 이면 순위가 없을 때
`원천이 HTTP 404 (탐색됨)` — **고치려던 바로 그 오보**가 나온다.
→ 그 배치를 픽스처로 만들어 값으로 고정(#20·#141·#91c).

**M1 — 꼬리표를 한 경로에만 달면 지시서가 다른 경로에서 거짓이 된다**
냉각 안내("직전 기록 · N분 뒤 재시도")를 `theme_status` 안에만 뒀는데 그 함수는
`stale`(= 쓸 수 있는 저장분이 있을 때)에만 돈다. 새 배포·캐시 비움 상태에서
원천이 죽으면 2회차부터 **저장분 없는 빈 결과 + cooldown** 이 나오고, 그 payload
엔 `stale` 이 없어 화면이 **10분 전 기록을 방금 잰 것처럼** 적었다(#165·#43).
CLAUDE.md #346 이 그 문장을 약속으로 적어 뒀으므로 **지시서가 자기 자신에 대해
사실이 아닌 것을 말한 것**이다(#286). → `theme_reason_text` 한 함수로 빼서 두
경로가 같이 쓴다(#38).

**Low 4(전부 반영)**
- `_clear_theme_fail` 독스트링의 사유가 **성립하지 않았다** — `_cache_write` 가
  tmp+`os.replace` 라 재기록마다 mtime 이 새로 잡히고, 냉각 중엔 조기반환이라
  '살아 있는 메모 + 성공' 자체가 제품 경로에 없다(#291 계열 · 가드가 재는 범위를
  넘는 주장을 독스트링에 적지 말 것, #55·#286).
- 주석의 귀속이 틀렸다("리서치 형제를 위해 2쪽부터") — `_theme_fetch_one_size`
  를 부르는 곳은 테마 사다리와 탐색 검증뿐이고 리서치는 자기 페이징을 쓴다.
  틀린 근거는 H1 을 고칠 때 "리서치가 깨진다"는 헛걱정을 만든다(#55).
- `reason_rank` 표가 429·5xx 를 "우리 요청·파서를 고친다" 로 묶었다 — **정렬
  값은 옳고 처방 문구만 거짓**이다(#82·#292 틀린 라벨).
- 픽스처가 맨 `PAUSED` 를 썼는데 제품이 만드는 문자열은 라벨이 붙은 모양이다
  (#155) — 지금은 부분문자열 검사라 통과하지만 다음 리팩터를 못 잡는다.

**리뷰가 자기 계수 오류를 자백했다**: 첫 뮤테이션 실행에 `--timeout=300`
(pytest-timeout 미설치)을 붙여 **모든 뮤테이션이 rc≠0 = '잡힘'** 으로 보였다.
꼬리 출력이 빈 것을 보고 플래그를 빼고서야 H2 가 드러났다 — 감사 도구의 계수
패턴부터 의심할 것(#47).

**교훈**
- **가설을 세웠으면 그 가설이 참일 때의 경로를 끝까지 따라가라.** "X 가 원인이다"
  로 한 자리를 고치고 나면 "그럼 X 는 다른 어디에 아직 있나"를 같은 턴에 물어야
  한다 — 반만 적용한 가설은 증상을 못 고치면서 고쳤다는 인상만 준다.
- **픽스처가 '이미 정답인 순서'면 그 정렬 코드는 무보장이다.** 순위·우선순위를
  넣었으면 **순위가 실제로 순서를 바꾸는** 배치로 재라.
- **안내 문구를 넣은 경로가 그 상태의 유일한 경로인지** 확인할 것 — 아니면
  지시서에 적은 약속이 나머지 경로에서 거짓이 된다.

### 실수 #348

348. **예산을 두면 그 예산 밖은 영원히 빈칸인지 물을 것 — 재료가 안 바뀌는 값은
    캐시가 답이다**(2026-09-12 리서치 상세, 사용자 "투자의견이랑 목표가도
    안나오는것들 최대한 모두 나오게"):

**증상** 화면이 `목표가·투자의견은 최신 35건에만 붙였습니다(상세 수집 예산 40건 ·
나머지 260건은 빈칸)` 라고 적고 그 260건이 **매 주기 같은 260건**이었다.
2026-09-12 에 창을 일주일로 넓히자(쪽 이어받기) 행이 20 → 295 가 됐는데
`_DETAIL_BUDGET = 40` 은 그대로라, 41번째부터는 **몇 주기를 돌아도** 채워질 경로가
없었다. 예산 자체는 옳다(#116 본문이 아닌 값을 본 응답 경로에서 부를 땐 예산과
캐시를 같이) — 빠진 것은 **캐시** 쪽이다.

**처방 — 예산을 키우지 말고 '이미 읽은 것을 다시 안 읽게'**
발행된 리포트의 목표가·투자의견은 **안 바뀐다**(정정본은 새 `nid` 로 온다).
그래서 `research_detail.json` 에 nid 별로 적고, 예산은 **캐시 미스에만** 쓴다:
- 값이 **전부 있는** 배치라면 1주기 40 · 2주기 80 · 3주기 100% · 그 뒤 네트워크 0
  (회귀가 네 주기를 실제로 태워 값으로 고정).
- ⚠️⚠️ **그런데 '빈 결과' 를 한 통에 담으면 수렴하지 않는다**(독립 리뷰 2026-09-12
  Blocking, 실측): 실패 TTL(15분)이 수집 주기(`_MARKET_TTL_HOURS` 1시간)보다 짧아
  음성 캐시는 **다음 주기 전에 항상 만료**된다. 그래서 값이 없는 리포트(NOT RATED·
  탐방노트·상세 404)가 매 주기 날짜 상위 40건을 영구 점유하고 그 아래 행은 **한 번도
  시도되지 않는다** — 값 있는 75건이 아래쪽에 있는 배치에서 24주기 뒤에도 화면
  **0/295 · 시도 40/295 · 누적요청 960** 이었다(갈래를 나누면 75/295 · 시도 295/295 ·
  누적요청 295). 그 상태에서 화면의 `남은 220건은 다음 주기에 채웁니다` 는 **영원히
  지켜지지 않는 약속**이고(#165·#43), 비용도 '신규 몇 건' 이 아니라 80 HTTP/주기로
  옛 판과 같다.
- 그래서 **원천이 답했나**로 가른다(#82 — 같은 '빈 결과' 인데 처방이 반대다):
  값이 있다 → 30일 · **답했는데 값이 없다 → 30일**(그 리포트에 없는 것이다) ·
  **못 닿았다 → 15분**(원천 장애 한 번이 한 달 빈칸으로 굳으면 안 된다,
  #303·#161·#152). 코드에 두 경로가 이미 있었는데(`if not html:` = 못 닿음 ·
  마지막 반환 = 원천이 답했고 값이 없음) **같은 모양으로 저장**한 것이 원인이다.
- ⚠️ 단 '값이 없다' 는 **우리 파서의 판정**이다 — 원천이 서식을 바꾸면 전 행이
  '값 없음' 으로 30일 굳는다. 그래서 그 기록에만 `client_sig()`(모듈 소스 지문)를
  실어 **파서가 바뀌면 그 판정을 버린다**(#119·#233 — 손 bump 는 일곱 번 졌다).
  값이 **있는** 기록은 원천이 준 사실이므로 지문과 무관하게 산다.
- 캐시 쓰기는 **가장 아래 계층**(`_fetch_report_detail_via`)에 둔다 — 호출부가
  셋(시장·산업·개별종목)이라 한 곳에 둬야 세 화면이 안 갈린다(#38). 반환 지점이
  셋이므로 **셋 다** 적는다(한 곳만 적으면 폴백 경로가 매번 다시 친다).
- 디스크 쓰기는 워커가 아니라 **호출부가 한 번**(6스레드가 각자 쓰면 마지막이
  나머지를 덮는다) · tmp + `os.replace`(#280).
- 캐시 독자는 **어떤 바이트가 와도 안 던진다**(#331 — 던지면 그 맵을 읽는 화면
  셋이 통째로 빈다).

**화면 문구도 같이 고쳤다** — `최신 35건` 은 **무엇 중의 35 인지** 안 말한다(#45
모집단 · #82 갈래). 이제 `목표가·투자의견 35/295건(저장분 0 · 이번 수집 40 ·
예산 40건) — 남은 220건은 다음 주기에 채웁니다` 라, **다음 주기 출력이 곧
측정**이 된다(커버리지가 오르는 게 화면에 보인다). 예산이 남았는데도 못 채운
행은 갈래가 다르다("상세에 목표가·투자의견이 없는 리포트") — 처방이 정반대이므로
이름을 나눈다(#82).

⚠️ **테스트가 이 캐시를 오염시키면 운영이 한 달을 앓는다** — 30일 TTL 이라
가짜 nid 하나가 오래 산다. 격리는 **루트 `conftest.py` 의 import 시점
리다이렉트**(`_redirect_disk_caches`, 되돌리지 않는다)에 넣고 메모리 사본은
`tests/conftest.py` 에서 같이 비운다(파일만 돌려놓으면 이미 읽어 둔 맵이 다음
테스트로 샌다, #30·#344). 실제로 격리 전 실행에서 `저장분 1` 이 찍혀 오염이
관측됐다 — conftest 주석이 "오염을 관측하면 그 모듈을 여기 한 줄로 추가할 것"
이라고 적어 둔 그 경로다.
⚠️ **나는 그 리다이렉트를 틀린 파일에 넣고 지시서에 '세션' 이라고 적었다**(독립
리뷰 Medium-1) — 동작하는 기전은 루트 conftest 의 import 시점 교체이고, 내가 고친
`tests/conftest.py` 쪽은 **함수 스코프**라 daemon 스레드도 `make test` 의 두 번째
프로세스(`pytest bot/tests`)도 못 덮는다. 그 파일의 주석이 스스로 그렇게 적어 두고
있었다. 지시서가 자기 자신에 대해 거짓이면 다음 사람이 가드를 건너뛴다(#286).

**회귀가 눈이 멀었던 자리 셋**(독립 리뷰 High·Medium — 전부 뮤테이션 실측):
- **영속을 한 번도 안 쟀다** — 3주기를 **같은 프로세스**로 돌리니 `_DETAIL_MEM` 이
  이어져, `detail_cache_flush()` 두 호출을 **모두 지워도** 4건이 green 이었다.
  주기마다 메모리 사본을 비워 **재시작을 흉내 내야** 디스크 파일만으로 커버리지가
  오르는지 잰다(#20·#91c).
- **오늘 운영에서 유일하게 도는 경로가 무가드였다** — 픽스처의 JSON 단이 목표가·
  의견을 둘 다 줘서 조기반환만 타고 **옛 HTML 경로에 한 번도 안 닿았다**. 그 경로의
  `detail_cache_put` 을 `pass` 로 바꿔도 green. 픽스처는 **오늘 실제로 도는 경로**로
  짤 것(#155).
- **단일 종목 경로의 유일한 캐시 읽기**(`_fetch_report_detail_via` 첫 줄)도 무가드
  였다 — 시장 경로는 prefill 이 대신 읽어 준다.

**그리고 감사(`--check` ④)는 캐시를 우회한다** — 그 줄이 묻는 것은 "원천이 지금
답하나" 이므로, 30일 캐시를 읽으면 원천이 죽은 날에도 옛 값으로 ✅ 가 찍힌다
(#35 의 경계 · #321·#345c). `_fetch_report_detail_via(nid, use_cache=False)` 로
읽기·쓰기를 함께 끄면 진단이 운영 캐시를 건드리지도 않는다(#264·#283).

**tmp 파일 이름은 프로세스 상수면 안 된다**(독립 리뷰 L1, 실측 재현) —
`ThreadingHTTPServer` 에서 시장 경로와 종목 경로의 flush 가 겹치면 둘이 **같은 tmp**
를 쓰고 뒤엣것의 `os.replace` 가 ENOENT 로 죽어 그 flush 가 조용히 유실된다.
`threading.get_ident()` 로도 부족하다 — **끝난 스레드의 id 가 재사용**되어 6개 중
2개가 같은 이름을 받았다(#25 이름이 아니라 실측). flush 마다 늘어나는 번호로 가른다.

⚠️ **스텁은 원천 쪽에 건다** — 첫 회귀가 제품 함수(`_fetch_report_detail_via`)를
통째로 갈아끼워 그 안의 캐시 쓰기가 빠졌고, 커버리지가 안 늘어 **fix 가 틀린
것처럼** 보였다(#155·#20). 한 층 아래(`_detail_json`)를 스텁하자 계약이 섰다.

⚠️ **정렬이 끼면 입력 슬라이스로 재지 말 것** — 2주기가 1주기와 다른 nid 를
쳤는지 `rows[:40]` 과 대조했는데, 수집기는 날짜로 정렬한 뒤 자르므로 그건 다른
집합이다. **1주기가 실제로 친 집합**과 대조해야 잰다(#91b 재는 대상이 맞나).

**교훈**
- 예산을 넣을 땐 "예산 밖은 **언젠가** 채워지나, 아니면 영원히 빈칸인가"를 먼저
  물을 것(#171 가드가 '못 만든다'로 끝나면 그 자리가 영원히 비는지 — 그 예산판).
- **재료가 안 바뀌는 값**이면 답은 예산 확대가 아니라 캐시다 — 그러면 커버리지는
  시간이 해결하고 정상 상태 비용은 신규분뿐이다.
- 수량을 적는 화면은 **분모**를 같이 적을 것. 분모가 없으면 사용자도 나도 그 수가
  좋아지는지 나빠지는지 모른다.
