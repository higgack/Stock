# CLAUDE.md 룰 ↔ 테스트 커버리지 매핑

> pm-ai-shipping 스킬의 "intended vs implemented" 감사 패턴 채택(2026-07-26) —
> CLAUDE.md 가 강제하는 룰이 실제로 (a) 자동 테스트로 검증되는지 (b) 관례/수동 리뷰로만
> 강제되는지 (c) 검증 자체가 안 되는지 한 표로 추적. 신규 룰 추가 시 이 표 갱신(같은 커밋).
> "추측보고 금지" 원칙을 이 문서 자체에도 적용 — 확인 못 한 항목은 "미확인"으로 명시,
> 있다고 단정하지 않는다.

## 방법론 노트 — 왜 RULE 1-15 는 대부분 "관례로만"인가

`RULE 1-15`(`TradingAgents/tradingagents/agents/analysts/fundamentals_analyst.py`)는
분석가 LLM 에게 주는 **프롬프트 지시문**(예: "RULE 1 — 기간 라벨은 연도 내림차순",
"RULE 7 — 5거래일 horizon 명시 판정 필수")이지 결정론적 코드 로직이 아니다. LLM 이
매번 지시를 정확히 따르는지는 pytest 로 단정할 수 없어(같은 프롬프트도 출력이 매번
다를 수 있음), 실제 검증은 CLAUDE.md "Per-ticker 분석 검증 7축"(숫자정확성·글일관성·
형식·논리·분석가연결·데이터vs환각·5거래일horizon)의 **수동/에이전트 리뷰**가 담당한다.
이건 설계 의도이지 커버리지 공백이 아니다 — 다만 프롬프트 문구 자체가 소스에 실재하는지
(grep 가능한지)는 회귀 테스트로 고정할 수 있고, 실제로 일부는 그렇게 돼 있다.

## RULE 1-15 (`fundamentals_analyst.py`)

| RULE | 내용(요약) | 자동 테스트 | 비고 |
|---|---|---|---|
| 1 | 기간 라벨 연도 내림차순 + 콤마 스타일 | 미확인 | 프롬프트 텍스트, 7축 리뷰 §3(형식)에서 수동 검증 |
| 2 / 2.0.1 / 2.1 | 실제 peer 비교, 실제 티커만 | 미확인 | 7축 §2(글일관성)·§6(데이터vs환각) |
| 3 | DCF 시나리오 수치 형식 | 미확인 | 7축 §1(숫자정확성) — DCF 는 reference 전용(§7) |
| 4 | Point-in-time 정확성 | 미확인 | 7축 §6 |
| 5 | 다운스트림 헤더 재발행 금지 | 미확인 | 형식 규칙, grep 가능하나 미테스트 |
| 6 | 음수 장부가치(P/B) 처리 | 미확인 | |
| 7 | 명시적 판정 필수(5거래일 horizon) | 미확인 | 7축 §7 — 결론이 장기 thesis 아닌지 |
| 8 / 8.1 / 8.2 | 분기 현금흐름·분기합 vs 연간 정합성·PER null 설명 | 미확인 | 7축 §1 "분기합 ±10% vs 연간" 이 이 룰의 실행판 |
| 9 | 재벌집단 리스크(KR 전용) | 미확인 | |
| 10-14 | 산업별 정책/매크로 dominant 변수(KR/JP/US/CN/TW) | 미확인 | universal-market 원칙 자체(코드 게이트)는 별도 |
| 15 | 실적콜 제약 추출(universal) | 미확인 | |

## Corp-action HARD GUARD (`bot/analyzer.py`)

| 대상 | 함수 | 자동 테스트 | 비고 |
|---|---|---|---|
| HARD GUARD 위반 감지·경고 삽입(감자/분할 언급 시 기술지표 인용 차단) | `_hard_guard_warn` (analyzer.py:2140) | **없음** — grep 결과 `tests/test_regression.py` 내 "HARD GUARD" 매치 2건은 전부 **다른 가드**(현재가 데이터 이상 HARD GUARD)에 대한 것이지 corp-action 가드가 아님 | 갭. corp action 키워드(감자완료/무상증자/주식분할 등) 검출 후 실제로 경고가 삽입되는지 직접 검증하는 테스트 부재 |

## 분석가 stance 추출 (`bot/analyzer.py::_extract_stance`)

| 대상 | 자동 테스트 | 비고 |
|---|---|---|
| `<<STANCE:BUY\|HOLD\|SELL>>` sentinel(2026-07-26 신규, Pass -1 최우선) | ✅ `TestStanceSentinel20260726` (7개) | 각 방향·대소문자무관·마지막occurrence우선·프로즈와 충돌시 sentinel 승·sentinel 부재 시 폴백 무변화·형식오류 무시·배선(4 분석가 전부 `get_analyst_directive()` 호출) 검증 |
| 정규식/키워드 다단계 폴백(Pass 0-3 — 인용등급 중화·false-friend 중화·conclusion-zone 우선 등, 각각 실제 프로덕션 회귀로 도입됨: 009450.KS/9988.HK/2382.TW/ALAB/300750.SZ 등 코드 주석 참조) | **없음** | 이번 배치 전까지 이 함수 전체가 **자동 테스트 0건**이었음(순수 결정론적 Python 함수인데도) — 발견 자체가 이 문서의 존재 이유. sentinel 도입으로 향후 신규 분석은 대부분 Pass -1 에서 끝나 폴백 의존도가 줄지만, 폴백 코드 자체의 회귀 고정은 여전히 갭 |

## PM override discipline (`bot/analyzer.py`)

| 대상 | 함수 | 자동 테스트 | 비고 |
|---|---|---|---|
| 강제-HOLD 배너 문구·라우팅(in-graph 센티넬 + override_rating 이중경로) | `_detect_discipline_forced_hold_banner` | ✅ `TestPMOverrideDisciplineBanner` (3개) | 배선 존재·오해문구 미사용·1차등급 복원 파싱 검증 |
| PM 원문 등급 마스킹(강제 HOLD 시 비-Hold 등급 취소선) | `_mask_overridden_pm_rating` | ✅ `TestPmOverrideRatingMask` (4개+) | ast 로 함수 추출 후 exec — yfinance 의존 모듈 통 import 없이 유닛테스트 |
| **override 트리거 자체**(RSI≥75/≤25, ±5일 임박 catalyst, data-availability HOLD 판정 로직) | `_has_pm_override_trigger`, `_check_pm_override_required` (analyzer.py:882, 943) | **없음** | 갭 — 위 두 항목은 트리거가 이미 있다고 가정한 뒤의 **표시 레이어**만 검증. 트리거 판정 자체(예: RSI 74.9 는 트리거 안 됨, 75.0 은 됨 같은 경계값)는 미검증 |

## 리스크게이트 (`bot/risk_gate.py`, 참고 — 2026-07-26 배치에서 강화)

| 대상 | 자동 테스트 | 비고 |
|---|---|---|
| 일일/주간/월간 손실한도, 연속손실 쿨다운, 복수매매 차단, `side=="sell"` 조기허용 | ✅ `TestRiskGateExpansion20260726` 외 다수 | 이 파일은 이번 배치에서 신규 게이트 추가 시 전부 테스트 동반 — RULE 1-15/override 트리거와 달리 결정론적 코드라 pytest 로 경계값까지 고정 가능했던 사례 |

## 다음 우선순위 (갭 메우기 후보)
1. `_hard_guard_warn` — 감자/분할 키워드 존재 시 실제로 경고 텍스트가 삽입되는지 직접 단위테스트.
2. `_has_pm_override_trigger`/`_check_pm_override_required` — RSI 경계값(74.9/75.0/25.0/25.1), catalyst
   D-day 경계, data-availability 케이스별 순수 유닛테스트.
3. RULE 1-15 는 근본적으로 LLM 출력 검증이라 pytest 화가 부적합 — 대신 "프롬프트 텍스트가 여전히
   소스에 있는지"(orphan 참조 방지, §Pre-commit 4) 만이라도 grep 회귀로 고정하는 게 현실적 다음 단계.
4. `_extract_stance` Pass 0-3 폴백(정규식/키워드) — sentinel 도입(2026-07-26) 이후에도 구버전
   아카이브·sentinel 미준수 케이스의 안전망이라 여전히 살아있는 코드. 코드 주석에 인용된 실제
   회귀 티커(009450.KS/9988.HK/2382.TW/ALAB/300750.SZ)별로 최소 1개씩 순수 유닛테스트 고정 권장.
