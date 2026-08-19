# NOAH Stock Bot — Claude 운영 지침 (compact)

> 기능 구현 로그·이력·상세 전량은 **CLAUDE_REFERENCE.md** 에 보존. 이 파일은 항상
> 주입되는 **활성 행동 규칙**만 (효과↑ 위해 compact 유지, 2026-06-20 분리). 규칙
> 추가 시 여기 1~2줄, 상세는 REFERENCE. 멀티마켓·기능 작업 전 REFERENCE 관련 섹션 확인.
> 모든 규칙은 repo 전 서브프로젝트(`bot/` NOAH · `trade/` 한국 수출입)에 적용.

## 브랜치 · 배포
- dev 작업 → PR → base `claude/stock-trading-automation-xqYf7` **squash merge = 배포**.
  VM auto-update(1분 폴링)가 base 만 감시. merge 까지가 배포 — draft 만 열고 멈추지 말 것.
  GitHub 연동(MCP) 불가 시 base 로 **직접 squash-push 배포 허용**(사용자 2026-07-08 규칙삭제
  — PR 우회 OK. 단 squash 스타일 단일커밋 + push 후 base grep 검증은 동일).
- `trade/` 도 같은 base 추적. VM 체크아웃 2개(`~/stock` NOAH · `~/stock-trade` trade)
  독립 배포. 옛 `claude/export-import-dashboard-zQsi2` 브랜치는 은퇴(새 작업 금지).
- 사용자 고정 선호(2026-07-29): "운영 반영" 목적의 작업은 미커밋 상태 완료 금지.
  필수 체인 = 수정/검증 → commit → push → base 반영 확인(로컬 확인만일 때만 미커밋 허용).
- **"배포" 신호** (사용자 2026-07-29): 푸시=배포=머지 완전체.
  명령 "배포" 또는 "이것도 푸시" = 다음을 끝까지 **반드시** 실행:
  ① Syntax/회귀 검증 ② 배포전 셀프리뷰 ③ origin push ④ base 브랜치 merge ⑤ base push
  ⑥ base 반영 확인 (grep 또는 git log). 중간 스킵 금지 — 같은 턴에 끝내기.

## ⚠️ 다른 AI 에이전트와 레포 공유 (사용자 2026-07-29, GitHub Copilot 합류)
이 레포(`higgack/Stock`)는 이제 **Claude Code 뿐 아니라 GitHub Copilot 도 병행 개발**한다.
Copilot 용 온보딩 요약은 `.github/copilot-instructions.md`(+ `trade/**` 전용
`.github/instructions/trade.instructions.md`) — Claude 규칙 요약본, 상세는 여전히 이 파일들이
원본. 실무 함의: (a) 작업 시작 전 `git log origin/<base>` 로 내가 모르는 새 커밋(Copilot 작성)
있는지 확인 습관화 — base 가 두 에이전트가 각자 merge 하는 공용 배포 지점이라 동시편집/충돌
가능. (b) "내가 마지막으로 고친 상태"를 가정하지 말 것 — 매 세션 시작 시 base 최신상태 fetch.
(c) Copilot 이 만든 변경도 이 파일의 모든 규칙(UNIVERSAL CHANGES ONLY·Help/Dashboard 등록·
Pre-commit 검증) 적용 대상 — 리뷰 시 "이건 Copilot이 짰으니 기준이 다르다" 예외 금지.

## Default workflow — 배치 적재 (사용자 2026-06-12)
1. 평소 = 적재: 명시적 "커밋/푸시/배포" 없으면 구현·검증만, merge 안 함. 매 답변 끝
   `📦 누적: N개` (= `git diff --name-only origin/<base>`).
2. 중간응답 최소(1~2줄 + 카운트). 질문/리뷰만이면 카운트 무변동.
3. 적재 내구: dev 에 `[배치 보류 — merge 금지]` prefix 커밋 + push 체크포인트(컨테이너 휘발).
4. "커밋/푸시/배포" 한마디 = 일괄 flush: 검증 → 배포전 셀프리뷰 + 비자명변경 `/code-review`
   (아래 §Pre-commit 7~8) → PR ready → squash merge 1회 → 자동배포 → 최종 통합보고 + `📦 누적: 0`.
5. 라이브 장애 fix 는 즉시 merge 판단 가능. 적재 과대 시 중간 flush 제안.
6. 변경은 항상 universal (아래).

## ⛔ 과거 실수 — 반복 금지 (먼저 읽을 것)
1. 배포 = merge 까지 (draft 만 ≠ 배포; merge 확인 후 "배포함").
2. watchdog: `deploy/watchdog.sh` 가 "bot starting" 로그(180초 윈도)로 startup skip 판단
   — 봇 startup 경로 변경 시 검증. httpx/httpcore 로거 억제 금지(getUpdates 로그 사라짐→오탐 재시작).
3. URL 템플릿 이중 prefix 주의(`CIK{cik}` 중복 → 404).
4. auto-update 는 base 만 배포 — 다른 브랜치 push 후 "배포됨" 금지.
5. .env 키 등록 후 `grep` 확인 코드 제공(한글자모 오타 주의).
6. 봇 restart loop 중엔 알림 불가 — 수동 restart 안내.
7. `parse_mode=HTML` 텍스트의 사용자입력·조건식 `<`/`>` 는 `&lt;`/`&gt;` escape 필수.
8. f-string `{{cards}}`→`{cards}` 이므로 `.replace("{cards}",..)` (이중브레이스 금지).
9. 알림 '신규' 판정 = 영구 seen-set + 첫활성 seed + 날짜가드. 무차별 전종목 푸시 금지(타겟 한정).
10. 전역 표기 자동적용(새 surface 도): (a) 모든 시각 = KST 명시계산(서버 로컬타임 의존 금지)
    (b) 데이터 위젯 = 적용시각·소스 라벨(렌더시각 아님) (c) 명령 = 텔레그램·대시보드 단일 레지스트리.
11. '배포완료' ≠ '화면에 보임' — 기능 보고 시 화면 체크포인트(출처·카운트·기준시각) 제시 +
    기대와 다르면 소스→캐시→예산/윈도→렌더 전경로 실데이터 추적해 끊긴 지점 특정.
12. 추측보고 금지: 검증불가면 단정 금지 → 정확 확인명령 1블록 요청(샌드박스서 VM 안 보임).
    같은증상 2회+ = 추측종료 → 가시성(로그파일화·상태라벨·silent-except 경고) 심고 근본원인
    특정까지 "됐다" 금지. 헬퍼 만들면 호출부 배선 grep E2E. silent-fail(DEVNULL·except-pass) 금지.
13. UI '안됨' = 브라우저 접속 URL(포트·프록시·토큰경로) 맨 먼저 물어라. curl 200 ≠ 브라우저
    정상(프록시). DOM 측정 ≠ 스크린샷. 같은증상 반복 = 즉시 가시정보 요청. 실수기록은 같은 턴에.
14. 대시보드 CSS 디버깅(샌드박스서 렌더 불가, 2026-06-20 trade 차트표 ~10턴 삽질): 추측 반복 금지
    → 새 대시보드/CSS 작업 전 `DESIGN.md`(디자인 토큰·컴포넌트 패턴) 참조.
    생성 HTML 먼저 찍어 구조 확인 + CSS 상속함정 의심 — `.ind-table td{text-align:right}` 가
    colspan 상세셀로 누수(코멘트 우측정렬·좌측잘림), grid 를 `<td>` 직속으로 두면 트랙 깨짐(블록
    div 래퍼로 해결), `direction:rtl`(표 최신-우측 디폴트)는 JS scrollLeft 와 충돌(JS 제거),
    자식표 max-content 가 부모표 폭 늘려 스크롤바 엉뚱한 위치(직속표만 width:100%). 2회+면 DOM 요청.
15. PR 생성 전 push 필수(2026-06-30 #696 리뷰 fix 유실): 로컬 commit 만 하고 push 안 한 채
    PR 만들면 그 커밋이 PR/merge 에서 빠지고 reset --hard 로 영구 유실 → "반영했다" 보고가 거짓.
    flush 순서 = ① commit ② **push** ③ `git log origin/<dev> -1` 로 원격 반영 확인 ④ PR ⑤ merge
    ⑥ merge 후 `grep` 로 base 에 변경 실재 확인(merge=배포 검증, 실수 #1 연장).
16. 검증 스크립트는 커밋과 한 체인(2026-07-06 재발): heredoc 스모크 뒤 별개 라인의
    git commit 은 스모크 실패에도 실행됨 — `&&` 로 묶거나 스모크 먼저 단독 실행 후 커밋.
    assert 도 오작성 주의(URL+라벨 substring 이중카운트류) — 실패 시 원인부터.
17. squash-merge 반복 후 dev 미동기화 → base merge 시 내용 5중복(2026-07-29,
    Copilot 병행 세션서 실측): dev 에서 여러 PR 을 연달아 squash-merge 해도 dev
    로컬 브랜치는 원본(비squash) 커밋을 그대로 들고 있음 — squash 커밋은 base 에
    새 해시로 남아 dev 원본과 "다른 커밋"으로 보이므로, 나중에 `git merge origin/
    <base>` 로 동기화하면 같은 텍스트가 여러 번 반복 삽입될 수 있음(git 이 동일
    내용을 서로 다른 삽입으로 인식). 대응: PR 여러 개 연달아 squash-merge 후에는
    다음 작업 전 **매번** dev 를 base 로 동기화(`git merge origin/<base>` 또는
    rebase) — 실수#17 방지+ 실수 규칙(a) "매 세션 시작 시 base fetch" 의 실제
    발동 사례. merge 직후 파일 diff 를 훑어 반복 삽입 없는지 확인 습관화.
18. 아카이브에 **구워진** 데이터는 코드를 고쳐도 안 바뀐다(2026-08-18 동종비교):
    `if not si.get("peer_comps")` 식 '있으면 skip' 게이트는 수집 로직을 개선해도
    이미 분석한 종목 화면엔 영원히 안 붙는다 — 필드 유무 냄새맡기도 새 필드마다 조건을
    같이 고쳐야 해 매번 잊는다. 수집기에 **스키마 버전**을 찍고 게이트가 대조하게 할 것
    (peer_comps 는 `_PEER_SCHEMA_VER` 로 구조화 완료 — news/financials/kr.flow 등
    나머지 skip-if-present 키엔 아직 규율로 적용).
19. 소스 **문자열**을 단언하는 테스트는 틀린 값을 축복한다(2026-08-18 접미사 폴백):
    `assert 'alt = pt[:-3] + ("KQ" ...' in src` 가 green 이었지만 점이 빠져 `240810KQ`
    로 조회 → 항상 404, 폴백은 배포된 채 한 번도 동작 안 함(VM 프로브의 404 원문으로
    발각). 배선 확인용 grep 단언은 유지하되, **값이 걸린 곳은 반드시 동작 테스트**
    (스텁으로 호출해 결과를 본다). mutation 도 문자열만 바꾸면 같이 통과한다.
20. 헬퍼 단위테스트는 **배선 변형을 못 잡는다**(2026-08-18 동종비교 기준 혼재):
    `_fetch_one` 에서 yfinance 파생값을 지우고 DART 로 덮는 뮤테이션이 헬퍼 테스트
    30개를 전부 통과했다 — 헬퍼는 그대로였으니까. 두 출처가 한 결과에 합쳐지는
    지점은 **수집기를 통째로 태우는 E2E 1개**를 반드시 같이 둘 것(실수#12 배선 grep 의
    실행형 버전 — grep 은 존재만, E2E 는 순서·우선순위까지 본다).
21. 진단 스크립트에 **버전 배너**를 찍어라(2026-08-18 감사도구): 배포 전 코드로 돈
    출력을 새 결과로 착각해 이미 고친 문제를 다시 쫓았다(`↪`·재시도 블록이 통째로
    없는데 그걸 못 알아봄). 시작 줄에 스크립트 버전 + 관련 스키마 버전을 찍을 것.
    같은 실행에서 배운 것: 외부 API 대량조회는 **간격+냉각 재시도** 없이는
    레이트리밋 실패를 '죽은 데이터'로 오보한다(671종목 중 끝 49개 연속 실패).
21b. **파싱/수집 결과를 디스크에 캐시하면 코드를 고쳐도 안 바뀐다**(#18 의
    반복 — 2026-08-19 하루에 세 번: peer_comps·FRED·FnGuide 스크레이퍼).
    파서를 고치고 배포했는데 프로브 출력이 **한 글자도 안 변하면** 캐시부터
    의심할 것. 규율로 기억하지 말고 **결과에 파서/스키마 버전을 찍고 읽을 때
    대조**할 것(`_PARSE_VER`·`_FRED_CACHE_VER`·`_KR_FIN_SCHEMA_VER` 패턴).
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
24. **목록형 가드는 새 파일을 못 잡는다**(2026-08-18 env 헬퍼 재발): #23 을 고치며
    검사 대상 13개 파일을 **이름으로 열거**했는데, 목록 밖 `macro_snapshot._fred_monthly`
    는 여전히 raw `os.getenv` 라 봇 밖(크론·진단)에선 스파크라인이 조용히 사라졌다.
    가드는 **디렉터리 전체를 훑고 예외만 allowlist** 로 명시할 것 — 그렇게 바꾸자마자
    `naver_news_client`·`dashboard` 2건이 더 나왔다. 열거형 검사를 쓸 땐 "이 목록은
    누가 갱신하나"를 먼저 답할 것.
25. **능력은 이름이 아니라 실측으로 판정**(2026-08-18 한글 폰트): 인포그래픽이
    `NanumGothic.ttf` 경로 3개 + 이름에 "Nanum" 포함만 보고 "서버 한글 폰트
    미설치"로 단정해, Noto CJK 가 깔린 서버에서도 이미지를 통째로 포기했다(#24
    목록형 판정의 재발). 폰트 파일을 열어 **U+AC00 글리프 유무**로 바꿨다.
    ⚠️ 같이 배운 함정: matplotlib 동봉 `LastResortHE` 는 **모든** 코드포인트에
    글리프를 줘서 한글 검사만 하면 통과하는데 실제론 두부만 그린다 — 미할당
    코드포인트(U+0E5C·U+FFFFE)에도 글리프가 있으면 폴백 폰트로 보고 배제.
    "있다"를 묻는 검사에는 항상 **반대 증거**(없어야 할 것도 확인)를 같이 둘 것.
26. **생성한 JS/HTML 은 파이썬이 문법을 안 봐준다**(2026-08-19 보드 3종 동시
    백지): 비-raw `"""` 안에서 `\"` 로 쓴 속성 따옴표를 파이썬이 먹어 JS 가
    `title=""+…+""`(문자열 3개 연접) SyntaxError 가 됐고, 인라인 스크립트가
    통째로 죽어 PPI·CPI·유동성 보드의 표·필터·차트가 전부 빈칸이 됐다.
    헤더("63개 시리즈")는 정상이라 **데이터 문제로 보였다**. 렌더 스모크는
    문자열 길이만 봐서 green — 생성물이 JS/CSS/JSON 이면 **파서에 태울 것**
    (`node --check`). 같은 파일 다른 3곳은 `\\"` 로 맞게 쓰여 있었다 = 규율로는
    못 막는다. 회귀: 인라인 JS 파싱 + 비-raw 삼중따옴표의 먹힌 이스케이프 금지.
27. **형제 설정은 한쪽만 고치면 다른 쪽이 조용히 빈칸**(2026-08-19 경제캘린더
    실제치): 같은 FRED 릴리스를 보는 PCE·Core PCE 중 Core 에만 발표↔관측 시차가
    붙어 있어, PCE 는 45일 창에 ~60일 간격 관측이 **구조적으로** 못 들어와
    영구 빈칸이었다(#24 열거 누락의 설정판). 대응: 창 상한처럼 **정확도에
    기여하지 않는 경계는 넉넉하게**(상한 확대는 이미 잡히던 값을 못 바꾸고
    빈칸만 채운다 — 회귀로 고정), 그리고 형제 묶음은 이름 열거가 아니라
    **공통 키(릴리스)로 그룹핑해 설정 불일치를 테스트가 잡게** 할 것.
   (새 실수 = 날짜 + 한 줄 추가 의무. 항목이 구조적으로 막히면[코드가 그 실패모드
   자체를 불가능하게 바꾼 경우 — 규율로 매번 기억하는 게 아니라] "#N SUPERSEDED by
   <커밋/PR>" 태그 추가, 2026-08-09 Cerebras 지식베이스 블로그 검토 — age-decay
   아이디어 차용. 단 대부분 항목은 여전히 규율성이라 해당 없음, 억지 태깅 금지.)

## ⛔ UNIVERSAL CHANGES ONLY (가장 중요)
모든 변경(룰·폴리시·데이터소스·대시보드·헬퍼·스키마·기본값)은 **US+KR+JP(+TW+CN_A/HK/EU)
기본 universal**. 시장특정은 예외 — 문서화된 데이터소스 사유 필요(DART/EDINET/Naver/Kabutan/
ECOS/FRED/pykrx/MOPS/AKShare) 또는 한·일 언어출력.
- 단일시장이 노출한 버그도 fix 는 전시장 코드패스에 (`if market==` 게이트 금지).
- per-ticker 리뷰 fix = 시스템 전체 룰 (티커는 commit body 추적용 인용, ticker-specific 패치 금지).
- 매 commit body: "Rule applies to all analyses going forward / US+KR+JP 적용" 문구(= 구조 강제).
- 커밋마다 cross-market parity 자문: "전시장 동일?" yes→게이트없음, no→사유 commit body.

## Per-ticker 분석 검증 7축 (리뷰 시 전부 워크 — 스킵 금지)
1. **숫자정확성**: canonical 현재가/시총 전섹션 일치, PER/PSR/PBR 교차일치, 분기합 ±10% vs 연간
   (>50x 격차=단위drop OMIT), 베타 라벨(90일 vs 5년월간 구분), 콤마·백만·% ·통화prefix.
2. **글 일관성**: 분석가간 회사명/산업/시총/멀티플/베타 일치, peer = yfinance longName, horizon 일관.
3. **형식**: 빈 표헤더 strip, inline표 newline, RULE1 기간라벨(연도 내림차순), 통화+단위 정합.
4. **논리**: RULE 1~15 발화, corp action HARD GUARD(감자/분할→기술지표 차단), PM override discipline,
   DATA OFFLINE 가드(키 부재 시 공시 형식 fabrication 차단).
5. **분석가 연결**: 시장→감정→뉴스→펀더→Plan→Trader→PM 사실 활용, stance↔PM 방향, Trader=Plan 값 유지.
6. **데이터 vs 환각**: 회사명/티커/날짜/수치 출처 일치(API=절대사실, 사전지식 stale 가정),
   peer/내부자/공시 paraphrase·날조 차단, 단위-통화 leak 차단.
7. **5거래일 horizon**: 결론이 5거래일 방향성(장기 thesis 아님), DCF=reference 만.
결과: ✅작동 / ❌문제(Critical/Major/Minor) / universal fix 제안. 커밋은 "커밋" 신호 시만.

## Pre-commit 검증 — 의무 (skip = 명시적-커밋-요청 위반 동급)
1. **syntax**: `python3 -c "import ast; ast.parse(open('<f>').read())"` 모든 touch 파일.
2. **logic smoke**: 새 parser/classifier/mapper/formatter = happy + edge 인라인 테스트.
3. `_HELP_TEXT` 길이 체크(§Help). 4. 룰 이동 시 `grep -rn` orphan 참조 확인.
5. 다단계: 항목별 검증 후 다음("OK/다음" 전 batch 금지).
6. **회귀**: `make test`(=pytest tests/, VM ~37초) commit 전 의무. fail 시 commit 금지(누구든).
   새 회귀패턴 = `tests/test_regression.py` 영구추가(ad-hoc 1회용 금지). `make syntax`/`make help-len` 단축.
   - **rtk opt-in 토큰절감**(`command -v rtk` 있을 때만): noisy pass/fail 출력 = `rtk` 래핑
     (`rtk pytest …`/`rtk make test`/lint — 실패만+전체는 tee 보존). ⛔ `git diff`(배포전 셀프리뷰)·
     수치 정확검증(DART/관세청)·진입점 스모크는 **원본**(전역 auto-rewrite 훅 금지 — 압축이 검증 가림).
7. **배포전 셀프리뷰**(테스트 green 만으론 부족 — 사용자 '같은 거 두 번 힘들어'): base 대비 전체 diff
   재독 + (a) 기존함수 시그니처/반환형 정의부 확인 (b) 타임존(UTC↔KST)·단위·포맷 실기록부서 확인 +
   경계테스트 (c) 호출부 배선 E2E grep (d) 진입점 1회 실행 스모크(NameError/ImportError류).
8. **독립 리뷰 게이트**(비자명 변경만): 다파일·구조변경·공용헬퍼·새 모듈이면 배포 전 `/code-review`
   1회 독립 패스(셀프리뷰 사각 보완, 설치·비용 0). 한줄 라벨변경 등 자명한 건 스킵.

## Help / Dashboard 등록 — 의무 (같은 commit)
- user-visible 변경(명령/소스/RULE/분석가/대시보드/기능제거)은 `_HELP_TEXT`(bot/telegram_bot.py)
  **같은 commit** 갱신. 제거 시 해당 줄도 제거. 공개 spec(pin) 취급.
- `_HELP_TEXT` ≤4096 UTF-16 단일메시지, 모든 슬래시명령 보존, 현재상태만(deprecated/aspirational 금지).
  초과 시 압축 시도 후 안되면 STOP·사용자 보고(기능 silent drop / 분할 / 한도초과 commit 금지).
  검증: `len(text.encode('utf-16-le'))//2 < 4096`.
- screener 도메인 inline 금지 → `/screener_list` + 대시보드 페이지. §명령은 "/screener [도메인]" 1줄.
- 대시보드 표면(차트 legend + ℹ️가이드 둘 다·카드필드·outcome컬럼·페이지헤더·범례)도 같은 commit.
  동작 바뀌면 설명도 정확히(out-of-sync = 버그).
- nav: `market.html` = 홈(hub). 그룹1 자산(💼·가계부) / 그룹2 분석(NOAH·Screener·도메인·워치·
  레딧·DailyByte·SV·수출입) / 그룹3 부동산(청약). 새 대시보드 = 해당그룹 nav + 홈 nav.
- 비용합산: 메인 cost = nav 비용surface 합산(분석/Screener/DailyByte/청약/부동산/블로그/trade수출입).
  새 비용surface = `_compute_stats` + `cmd_usage` 동시갱신. 비용카드 표기 = 오늘/이번달/누적
  3창(KST) 전 대시보드 통일(사용자 2026-07-05).
- 외부 third-party 사이트는 `/sites`(`_SITES_TEXT`)만, 이모지 없는 plain text. 메인 nav 추가 금지.

## 미니멀 코드 · 토큰 절약 (ponytail/codex-first 요지 이식, 2026-07-10)
- 코드 작성 전 사다리: ①불필요하면 스킵 ②코드베이스에 이미 있으면 재사용 ③표준lib/기존
  의존성이면 사용 ④한 줄로 되면 한 줄 ⑤그래야만 최소 완전 구현. diff = 요청 범위만
  (관성 리팩토링·방어코드 부풀리기 금지). 같은 아웃풋이면 항상 더 적은 코드/출력.
- 대규모 탐색·기계적 다파일 작업 = 서브에이전트 위임(메인 컨텍스트에 파일 덤프 금지).
  noisy 검증 출력 = rtk 규칙(§Pre-commit 6). 중간응답 최소는 §배치 워크플로 2 그대로.

## Automation-first
모든 반복작업 = 자동화(asyncio task / systemd timer / cron). 수동 SSH·반복 명령 금지. fix 가
운영자 반복명령 요구하면 잘못된 fix — 런타임 자가구동으로 재설계. 우선순위: in-process scheduler
> systemd timer > cron > (1회성만)수동. 무거운 long-running 핸들러는 watchdog `.busy` marker wrap 필수.
(현 자동화 인벤토리 전량 = REFERENCE.)

## PM override discipline
분석가 stance 가 일관(실행된 분석가 전원 동일방향)이면 PM 은 반대로 override 가능 — rationale 에
트리거 명시 시만: RSI>75(매도반전)/<25(매수반전), ±5일내 임박 catalyst, stance-mismatch 경고문,
data-availability HOLD. 트리거 없으면 분석가 방향 따름. 전 voter 조합(4/4·3/4·2/2·3/3) 동일 바.
corp action HARD GUARD 시 기술트리거(RSI/MACD/SMA) 무효 — catalyst/data HOLD 만. 시스템 강제 HOLD
배너는 "시스템 강제보유(PM override discipline)" 정확표기(enum 하드코딩 우회 금지).

## Stance/RULE 카운팅 · /start · Secrets · 인증
- "분석가 N명"/"RULE 1~N" 수정 전 코드 확인: analyst=`setup.py` add_node, RULE=`fundamentals_analyst.py` grep.
- /start·/help = 단일 `cmd_help` + `_HELP_TEXT` (fork 금지).
- Secrets: .env 공유 시 `cat ~/stock/.env | sed 's/=.*$/=***REDACTED***/'`. 키값 echo 금지. 노출 시 회전 권고.
- 대시보드 인증: 모든 대시보드 HTTP Basic Auth 기본(`DASHBOARD_USER=higgack` + `DASHBOARD_PASSWORD`
  .env 전용, literal 코드·git 금지). NOAH archive 서버가 단일 자격으로 전 surface 일괄 보호.

## 트레이드 레퍼런스북 · DART 피드
- DART 공시피드(`bot/dart_feed.py`) 카테고리·🔥중요 판정·칩 순서 정책은 고정 — 임의 변경 금지(REFERENCE).
- HS↔수출입↔회사 매칭 세부(오타교정·MTI핀·`split_names`·DART 보강후보) = **`trade/CLAUDE.md`**
  (trade/ 작업 시 로드) + 상세는 REFERENCE.

## 개발 도구 (Claude Code)
- **code-review-graph** (Tree-sitter 콜그래프 MCP, 2026-07-26 채택) — 배포전 셀프리뷰의
  "호출부 배선 grep E2E"/blast-radius 확인을 구조적으로 보강(722파일 규모라 grep 사각 존재).
  `.claude/skills/`(review-changes 등 4종) + `.claude/settings.json` hooks 는 repo 커밋(툴 미설치
  환경에선 hook 이 자동 no-op). `.mcp.json` 은 머신별 절대경로라 `.gitignore` 유지(커밋 안 함) —
  환경별 최초 1회 `pip install code-review-graph && code-review-graph install --platform
  claude-code --no-instructions -y` 실행 필요(venv 권장 — 시스템 패키지 충돌 시 `--user` 는
  `-I` isolated-mode 서브프로세스 프로브와 충돌해 그래프가 0노드로 빌드되는 함정 있음).
- **engram** (Gentleman-Programming/engram, 2026-07-29 외부레포 리뷰 채택 검토) — Claude Code
  세션간 연속성용 MCP 메모리 서버(Go 바이너리, SQLite+FTS5, 임베딩/디케이 없음). 채택 시
  환경별 최초 1회 `engram setup claude-code` 실행(code-review-graph 와 동일 패턴 — 머신별
  개별 설치, repo 커밋 불요). 세션종료 시 Goal/Discoveries/Accomplished/Next Steps/Files
  템플릿으로 자동요약 저장 → 다음 세션 시작에 자동주입.
- **Skill_Seekers** (yusufkaraaslan/Skill_Seekers, 2026-07-29 검토) — 외부 API 문서(yfinance/
  FRED 등)를 스캔해 `.claude/skills/` 용 SKILL.md 를 자동생성하는 파이프라인. "사전지식 stale
  가정 금지"(실수#12) 대응용 grounded 레퍼런스 생성에 유용하나 AI 보강 단계가 API 키 과금 또는
  로컬 Claude Code 구독 소모를 유발 — 사용자가 본인 머신에서 직접 실행 판단(에이전트가 임의
  과금 유발 금지). 생성물은 다른 티커분석과 동일하게 "데이터 vs 환각" 검증 후 커밋.
- 자동화 인벤토리·CLAUDE.md 룰↔테스트 커버리지 매핑 = `docs/automation.md` / `docs/tests.md`.

## 멀티마켓 · 기능 상세 → CLAUDE_REFERENCE.md
US/KR/JP/TW/CN_A/HK 확장 · 실거래 E0 페이퍼+RiskGate · 차트 Phase · Daily Byte · Bottleneck
Screener(운영+설계) · 부동산/청약/블로그/레딧/자산/가계부 · Gemini AI-Studio↔Vertex 토글 ·
휴장일 캘린더 · Universal guard/screener guard 카탈로그 · 모델 audit(3-tier) · KR roadmap ·
API-blocked tasks · SV(은퇴) · TODO — **전부 REFERENCE**. 해당 영역 작업 전 관련 섹션 확인.
