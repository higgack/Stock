# NOAH Stock Bot — GitHub Copilot 운영 지침

> 이 레포는 Claude Code 로도 병행 개발됩니다. **모든 규칙은 사용하는 에이전트와
> 무관하게 동일 적용**됩니다 — Copilot 이 만든 변경도 Claude 가 만든 변경과
> 똑같은 배포 리스크(실거래 트레이딩 봇 · 실제 Telegram 사용자)를 가집니다.
> 상세는 루트 `/CLAUDE.md`(compact 활성 규칙) + `/CLAUDE_REFERENCE.md`(전체
> 이력) + `/trade/CLAUDE.md`(trade/ 서브프로젝트) 를 직접 열어서 확인하세요.
> 이 파일은 Copilot 자동 컨텍스트 주입용 요약본이며, 두 문서를 대체하지 않습니다.

## 이 레포가 무엇인가
- **`bot/`** — NOAH: 다중시장(US/KR/JP/TW/CN_A/HK/EU) 주식 분석 + Telegram 봇.
  LangGraph 기반 멀티 에이전트(시장/감정/뉴스/펀더멘털/Plan/Trader/PM) 파이프라인,
  대시보드(HTML), 스크리너, 실거래 페이퍼엔진.
- **`trade/`** — 한국 수출입(HS코드) 모니터링 + 회사 매칭 봇. 같은 base 브랜치를
  추적하지만 별도 VM 체크아웃(`~/stock-trade`)에 독립 배포.
- **`TradingAgents/`** — 위 파이프라인의 원본 프레임워크 서브트리.
- 운영 VM 은 `deploy/watchdog.sh` 로 봇 프로세스를 감시·자동재시작.

## ⚠️ 배포 모델 — merge = 즉시 프로덕션 배포
- base 브랜치: **`claude/stock-trading-automation-xqYf7`**.
- VM 이 이 base 브랜치를 **1분마다 폴링**해서 자동으로 pull + 재시작합니다.
  즉 **이 브랜치에 머지되는 순간 실제 사용자에게 배포**됩니다 (draft PR 을 열어
  두는 것만으로는 배포되지 않음 — 반드시 merge 까지 확인).
- 개발은 별도 dev 브랜치에서 하고 PR 로 base 에 **squash merge**. 직접 base 에
  push 하지 마세요(권한이 있어도 리뷰 없는 직접배포는 지양).
- 라이브 장애 hotfix 가 아닌 한, **머지 전 회귀 테스트 통과 필수**:
  ```bash
  make test        # = pytest tests/  (VM 기준 ~37초)
  ```
  실패 시 원인 해결 전까지 merge 금지.
- 변경한 모든 `.py` 파일은 최소 syntax 체크:
  ```bash
  python3 -c "import ast; ast.parse(open('<file>').read())"
  ```

## ⛔ UNIVERSAL CHANGES ONLY (가장 중요한 설계 원칙)
- 모든 변경(룰·정책·데이터소스·대시보드·헬퍼함수·스키마·기본값)은
  **US+KR+JP(+TW+CN_A/HK/EU) 기본 전체 적용**이 원칙입니다.
- 시장 특정 예외는 **문서화된 데이터소스 사유**가 있을 때만 허용됩니다
  (예: DART=한국 공시, EDINET=일본 공시, pykrx=한국 시세, MOPS=대만 공시,
  AKShare=중국 시세 — 이런 시장 고유 API 제약).
- **단일 티커/시장에서 발견된 버그의 fix 는 전체 시장 공통 코드 경로에
  적용**해야 합니다. `if market == "KR":` 같은 시장별 게이트로 특정 티커만
  고치는 패치는 금지 — 항상 "다른 시장도 똑같이 겪는 문제인가?"를 먼저 물을 것.

## Help / Dashboard 등록 — 같은 커밋에서 반드시 동반 수정
- 사용자에게 보이는 변경(새 명령/데이터소스/RULE/분석가/대시보드 페이지/기능
  제거)은 **`bot/telegram_bot.py` 의 `_HELP_TEXT`** 를 같은 커밋에서 갱신해야
  합니다. 기능을 제거했으면 관련 줄도 제거.
- `_HELP_TEXT` 는 단일 텔레그램 메시지 한도(UTF-16 4096) 이내여야 합니다:
  ```python
  len(text.encode('utf-16-le')) // 2 < 4096
  ```
- 대시보드 UI 변경(차트 legend, 카드 필드, 컬럼, 헤더, 안내 문구)도 동작이
  바뀌면 설명 문구까지 정확히 동기화하세요 — 설명과 실제 동작 불일치는 버그로
  취급됩니다.

## 자주 반복된 실수 (수정 전 확인)
1. "배포함" = merge 까지 완료된 상태만. draft PR 만 열어둔 상태는 배포 아님.
2. URL/설정 템플릿에 prefix 를 이중으로 넣지 말 것(예: 이미 prefix 가 붙은
   변수에 또 prefix 를 붙이면 404).
3. `parse_mode=HTML` 로 보내는 텔레그램 텍스트에 사용자 입력이나 `<`/`>` 부호가
   들어가면 `&lt;`/`&gt;` 로 escape 해야 합니다(안 하면 파싱 에러/깨짐).
4. Python f-string 안에서 리터럴 중괄호는 `{{`/`}}` 로 이스케이프됩니다 —
   f-string 렌더링 후 문자열에 `.replace()` 를 적용할 때 이중 브레이스로 다시
   감싸지 마세요.
5. 알림/신호의 "신규" 판정은 영구 seen-set + 첫 활성 seed + 날짜가드 조합이어야
   합니다 — 조건에 걸리는 전 종목을 무차별로 즉시 푸시하지 마세요(타겟 한정).
6. 모든 시각 표시는 KST 로 명시 계산(서버 로컬 타임존에 의존 금지). 데이터
   위젯에는 "데이터 적용 시각·소스"를 표기(렌더링 시각이 아님).
7. 새 헬퍼 함수를 추가했으면 실제 호출부까지 연결됐는지 `grep` 으로 E2E 확인할
   것. silent-fail 패턴(예외를 조용히 삼키는 `except: pass`, 로그 없는
   `DEVNULL`)은 금지 — 실패는 반드시 로그로 드러나야 합니다.
8. 커밋 전 검증 스크립트(smoke test)와 `git commit` 은 하나의 실행 체인으로
   묶으세요(`&&` 등). 별개 라인으로 분리하면 검증 실패해도 커밋이 그대로
   실행되는 사고가 남.
9. PR 을 만들기 전에 반드시 **push 가 먼저 완료**됐는지 확인하세요. 로컬
   커밋만 하고 push 를 빼먹은 채 PR 을 만들면 그 커밋이 PR/merge 대상에서
   빠지고, 이후 원격 상태로 되돌리는 작업에서 영구 유실될 수 있습니다.
   순서: ① commit → ② push → ③ `git log origin/<branch> -1` 로 원격 반영
   확인 → ④ PR 생성 → ⑤ merge → ⑥ merge 후 base 에서 `grep` 등으로 변경이
   실제로 들어갔는지 확인.

## 시크릿 / 인증
- `.env` 파일 내용을 출력할 때는 값을 절대 그대로 노출하지 말 것:
  ```bash
  cat .env | sed 's/=.*$/=***REDACTED***/'
  ```
- 모든 대시보드는 HTTP Basic Auth 로 보호됩니다(`DASHBOARD_USER` /
  `DASHBOARD_PASSWORD`, `.env` 전용 — 코드나 git 에 literal 값 커밋 금지).
- API 키·토큰을 코드나 커밋 메시지에 하드코딩하지 마세요.

## 코드 스타일 원칙
- 필요 이상으로 만들지 않기: 요청 범위를 벗어난 리팩토링·방어코드·추상화 추가
  금지. 같은 결과라면 항상 더 적은 코드.
- 코드 작성 전 우선순위: ①불필요하면 스킵 → ②코드베이스 재사용 → ③표준
  라이브러리/기존 의존성 사용 → ④한 줄로 되면 한 줄 → ⑤그래야만 최소 구현.
- 트레이딩 분석가/RULE/PM override 로직처럼 도메인 특화된 부분을 건드릴 때는
  `/CLAUDE_REFERENCE.md` 의 "Per-ticker analysis verification framework"
  (숫자정확성/글일관성/형식/논리/분석가연결/데이터vs환각/5거래일 horizon 7축)
  섹션을 반드시 참고하세요 — 리뷰 없이 임의로 로직을 바꾸면 실거래 신호
  품질에 영향을 줍니다.

## 이 문서로 부족하면
- 분석가 수/RULE 번호 등은 항상 코드에서 직접 확인
  (`bot/setup.py` 의 add_node = 분석가 수, `bot/fundamentals_analyst.py` 의
  RULE 주석 grep = RULE 번호) — 문서 숫자를 암기해서 답하지 말 것.
- 멀티마켓 확장, 실거래 엔진, 스크리너, 부동산/청약, DART 피드 등 도메인별
  상세는 전부 `/CLAUDE_REFERENCE.md` 에 있습니다. 작업 전에 관련 섹션을 찾아
  읽으세요(레퍼런스 상단에 목차 역할을 하는 헤더가 있습니다).
