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

## 지시서 자체의 룰 (`tests/test_docs_consistency.py`, 2026-09-12 접기 도구 추가)

| 룰 | 검증 | 테스트 |
|---|---|---|
| CLAUDE.md 는 매 턴 주입되므로 예산 안 | ✅ 자동 | `test_injected_rules_file_stays_within_budget`(330,000자 — 넘으면 접을 게 남았는지까지 말한다) |
| 접기는 **과거 사건 보고만** 버린다(규칙 문장 유실 금지, #287) | ✅ 자동 | `test_folding_only_drops_incident_narrative` — REFERENCE 사본에서 **독립 구현**으로 다시 뽑아 대조(제품 함수를 부르면 동어반복, #292) |
| `N. ` 로 시작하는 줄은 전부 제 항목 | ✅ 자동 | `test_every_numbered_line_parses_as_its_own_entry`(#23 유실 사고의 회귀) |
| 접기가 계열 색인의 인용 코퍼스를 깎지 않는다 | ✅ 자동 | `test_folding_does_not_thin_the_citation_corpus` |
| 오래된 항목이 안 접힌 채 쌓이지 않는다 | ✅ 자동 | `test_old_entries_are_folded_not_left_to_grow`(20개 초과 시 실패 + 실행 명령 인쇄) |
| 손 접기 예외는 레거시 39건뿐 | ✅ 자동 | `test_hand_fold_allowlist_stays_small_and_resolves`(크기 단언) |
| 접기 도구의 단위 계약(전문 사본·멱등·창·표시폭·번호 순서) | ✅ 자동 | `tests/test_regression.py::TestClaudeMdFold20260912` 7건 |

## 네이버 경유 위젯 — 사다리·가시성 (2026-09-12, #349)

| 룰 | 검증 | 테스트 |
|---|---|---|
| 큰 한도가 거절돼도 **직전 한도로 받은 행을 버리지 않는다**(#136·#148) | ✅ 자동 | `TestThemeSizeRejectionKeepsRows20260912::test_larger_limit_rejected_keeps_the_smaller_limits_rows` (+ 반대 증거: 첫 한도부터 실패하면 실패로 보고) |
| 부분은 **냉각으로 기억하지 않는다**(모집단이 오가지 않는다·수렴한다) · 일시정지도 기억하지 않는다(#79) | ✅ 자동 | `…::test_partial_is_not_remembered_and_stays_consistent` · `…::test_paused_partial_is_still_not_remembered` |
| 부분이 싼 **이유**(1단에서 끝나고 옛 HTML 7쪽을 안 걷는다, #61) | ✅ 자동 | `…::test_partial_walk_stops_at_the_first_rung`(호출 수·HTML 미주행을 값으로) |
| 실패 사유는 원인을 **단정하지 않는다**(#165) | ✅ 자동 | `…::test_larger_limit_rejected_keeps_the_smaller_limits_rows`('거절' 어휘 금지 단언) |
| 상한을 알면 **더 큰 크기를 안 묻는다** · 상한을 안 말하면 **절반으로 물러난다**(#171) | ✅ 자동 | `TestParamSchemaProbe20260912::test_known_cap_stops_the_ladder` · `…::test_unexplained_rejection_halves_instead_of_giving_up` |
| 오류 봉투가 **두 벌**(zod `message` · RFC7807 `result`) — 둘 다 파싱한다(#73) | ✅ 자동 | `TestSecondErrorEnvelopeShape20260912` 4건(구조화 출력·형제 봉투 무손상·차이 판정·배선) |
| **차이 자체가 측정** — 같은 요청에서 이 키만 4xx 면 있음(5xx 제외, #165) | ✅ 자동 | `…::test_status_differential_is_itself_the_measurement` |
| 절반 물러나기는 **4xx(크기 거절)일 때만** — 타임아웃·429·404·일시정지엔 안 한다 | ✅ 자동 | `…::test_halving_only_on_size_rejections` |
| 뒷단이 더 짧게 주면 **앞단의 부분 증거가 남는다**(완전본으로 캐시 금지) | ✅ 자동 | `…::test_shorter_later_size_does_not_erase_partial_evidence` |
| 키 매칭은 **토큰 경계** · 행 수 변화 = 있음 · 일시정지 ≠ 도달 실패 · 0건이면 실패 고지 | ✅ 자동 | `…::test_page_candidate_is_not_matched_inside_pagesize` 외 3건 |
| 광고한 CLI 플래그가 **실제로 디스패치**된다(#252) | ✅ 자동 | `…::test_cli_flag_actually_dispatches`(main 을 태운다) |
| 파라미터 이름은 **지어내지 않고 원천에 묻는다**(읽기 전용·후보 전수·갈래 셋) | ✅ 자동 | `…::test_three_verdicts_are_distinguished` · `…::test_probe_is_read_only_and_covers_every_candidate` |
| 그 요약이 **제품 진입점(`get_json`)을 통과**한다(중복제거 최적화가 자료형을 바꿔 기능이 죽었다, #20·#79) | ✅ 자동 | `TestZodBriefSurvivesRealGetJson20260912::test_cap_survives_the_real_call_path` |
| 상한은 **`pageSize` 것만** 읽는다(`page` 상한에 납치되지 않는다) | ✅ 자동 | `…::test_page_bound_does_not_hijack_the_pagesize_cap` |
| 요약도 태그·`<`/`>` 제거를 거치고 **마스킹이 자르기보다 먼저**(§Secrets) | ✅ 자동 | `…::test_brief_is_sanitized_and_masked`(경계가 값 한가운데) |
| 네이버 zod 오류 봉투는 **자르기 전에 구조로 요약**(결정적 숫자가 잘리지 않는다, #156) | ✅ 자동 | `TestNaverZodErrorIsReadable20260912` 3건(실측 바이트 픽스처 · 상한 읽기 · 비-JSON 폴백) |
| 한도는 추측이 아니라 **원천이 말한 값**으로 재시도(비교 대상 = 성공한 최대 한도) | ✅ 자동 | `TestThemeLadderUsesDeclaredCap20260912::test_rejected_size_retries_at_the_declared_cap` |
| 사유를 합칠 땐 **더 행동 가능한 쪽이 앞**이고 둘 다 남는다 · 실패한 쪽 번호를 적는다(#275·#82) | ✅ 자동 | `…::test_prior_reason_is_not_overwritten_by_the_cap_rejection` · `…::test_failing_page_number_is_named` |
| non-200 에서 **원천이 적어 보낸 거절 사유**를 사유에 싣는다(#325·#82) — 단 비밀값 마스킹·HTML 안전·euc-kr 까지 | ✅ 자동 | `TestNaverErrorBodyIsCaptured20260912` 5건(왕복 `reason_rank`·HTML 안전·마스킹 단일 출처·euc-kr·`get_json` 배선) |
| '요청 모양 오류' 4xx 는 **(400·413·414·422) 만** — 429·401·403·404 는 키 존재의 증거가 아니다(#82) | ✅ 자동 | `TestSecondErrorEnvelopeShape20260912::test_rate_limit_and_auth_are_not_presence` |
| 여러 후보가 **같은 상태**로 거절되면 키가 아니라 **환경 변화**로 내린다(#45·#165) | ✅ 자동 | `…::test_shared_status_is_environment_not_a_key`(프로브 전체를 태운다) |
| RFC7807 `detail` 은 **맨 앞에** 싣고 한도는 300 — 자르기는 늘 꼬리를 먹는다(#156·#350) | ✅ 자동 | `…::test_long_rfc7807_detail_is_not_truncated_away`(머리말 길이로 순서를 강제) |
| `message` 와 `result` 가 같은 문구면 **두 번 싣지 않는다** | ✅ 자동 | `…::test_string_result_is_not_duplicated` |
| 허용값 목록은 **어구가 아니라 구조로** 읽고(미끼 되읊기 제외·문장 배제·비-미끼 괄호가 둘이면 긴 쪽) 우리가 열거하지 않는다(#24·#65) | ✅ 자동 | `TestSortCoverageProbe20260912::test_allowed_values_reads_the_real_rejection` 외 3건(실측 바이트 픽스처·#91 발화 확인) |
| 허용값을 못 읽은 것이 **우리가 잘라서**면 '거절되지 않음' 이라 말하지 않는다 — 갈래가 다르면 처방도 다르다(#82·#156·#350) | ✅ 자동 | `…::test_truncated_list_is_not_called_unreadable`(실제 바이트로 `_sanitize` 파이프라인을 태운다) |
| 합집합 커버리지는 **화면이 그리는 행**만 센다 — 키만 베끼면 모집단이 갈린다(#35·#45·#38) | ✅ 자동 | `…::test_theme_ids_count_only_what_the_screen_draws`(`parse_theme_json` 위임 + 반대 증거) |
| 정렬이 **순서만 바꾸는지 천장을 넘는지**를 재서 말한다 — 배선 전에 측정(#12·#79·#351) | ✅ 자동 | `…::test_union_growth_is_reported_when_sorts_differ` · `…::test_order_only_sorts_are_called_out` |
| **일부만 재고 '천장을 못 넘는다'고 단정하지 않는다** — '자란다'만 부분 측정에서 참(#165·#54·#274) · 실패가 같은 상태로 몰리면 환경(#45) | ✅ 자동 | `…::test_partial_measurement_never_claims_the_ceiling` 외 3건(0종 ❌ · 부분 ❓ · 부분이어도 성장은 단정) |
| 첫 크기가 거절되면 **원천이 말한 상한**으로 다시 묻고(#350) 상한을 안 적으면 **레포가 이미 쓰는 더 작은 크기**로(#351·§작업 원칙) · 상한은 **줄일 때만** 상한(#91) · 읽기 전용(#264) | ✅ 자동 | `…::test_declared_cap_is_used_when_the_first_size_is_rejected` 외 4건(캐시 디렉터리 비었음 단언 포함) |
| 광고한 `--probe-sorts` 가 **실제로 디스패치**된다(#252) | ✅ 자동 | `…::test_cli_flag_actually_dispatches`(main 을 태운다) |
| 정렬을 섞어 **한 요청 천장 너머**를 모은다 — 실측 200→266개이고 완전본이면 캐시된다(#353) | ✅ 자동 | `TestThemeSortSweep20260912::test_sorts_recover_the_whole_universe_and_it_is_cached`(수집기 E2E + 캐시 파일) |
| 훑기가 **한 종목도 못 늘리면** 완전본이라 말하지 않는다(#165·#341) | ✅ 자동 | `…::test_barren_sort_sweep_is_not_called_complete` |
| `partial`(완전본 금지)과 `lost`(훑기로 못 메움)는 **다른 사실** — 훑기의 성공이 손실을 못 덮는다(#351b) | ✅ 자동 | `…::test_ladder_data_loss_is_not_erased_by_a_clean_sweep` · `…::test_page_cap_loss_is_not_erased_either` |
| 훑기는 **이긴 단의 크기**로 묻고 `page` 를 안 얹는다 · 연속 0종·연속 실패면 멈춘다(#61) | ✅ 자동 | `…::test_sweep_asks_with_the_winning_page_size` 외 3건 |
| 거절당한 정렬은 **이름을 사유에** 적고 캐시를 막는다(열거형 드리프트 가시화, #24·#43) | ✅ 자동 | `…::test_failed_sort_names_the_value_and_blocks_caching` |
| 완전본 수집은 부제에 **경고를 띄우지 않는다**(사다리 경위는 로그로, #25·#260) | ✅ 자동 | `…::test_sorts_recover_…`(`via` 에 ⚠️·'한도' 없음) |
| 쪽 요청 **4xx 거절은 천장**(훑기가 넘는다)이고 429·5xx 는 손실이다(#82) | ✅ 자동 | `…::test_page_rejected_with_4xx_is_a_ceiling_not_a_loss` · `…::test_ladder_data_loss_is_not_erased_by_a_clean_sweep` |
| 마지막 정렬이 **아직 새 항목을 주면** 완전본이 아니다(#280·#341) | ✅ 자동 | `…::test_still_growing_union_is_never_called_complete` |
| `partial=False` 인데 사유가 있는 갈래(하한 미만·버린 행)도 **⚠️ 로 표시**(#34·#43) | ✅ 자동 | `…::test_anomaly_without_partial_still_warns`(네 번째 값 `warn`) |
| 훑기도 형제의 **`wrong_resource` 가드·dict 언랩**을 쓴다(#38·#96) | ✅ 자동 | `…::test_sweep_refuses_a_different_resource` · `…::test_sort_response_wrapped_in_a_dict_is_accepted` |
| 훑기는 **전경 경로 예산**(8초)에서 멈추고, 탐색 검증은 훑지 않는다(#116·#61) | ✅ 자동 | `…::test_sweep_stops_at_its_time_budget`(시계 주입) · `…::test_discovery_validation_does_not_sweep`(진입점을 태운다) |
| 여러 정렬이 **같은 상태**로 거절되면 환경 변화로 말한다(#45·#82) | ✅ 자동 | `…::test_same_status_on_many_sorts_reads_as_environment` |
| 부제에서 뺀 사다리 경위는 **로그로 남긴다** · 죽은 가드 둘은 실제로 태운다(#43·#291) | ✅ 자동 | `…::test_complete_path_logs_the_ladder_story` · `…::test_dead_guards_actually_guard` |
| 저장분이 있으면 **전경에서 수집을 기다리지 않는다**(SWR = KR 한 세션, #116·#163) | ✅ 자동 | `…::test_a_stale_snapshot_is_served_without_making_the_user_wait` |
| 정렬 기여 상세는 부제가 아니라 **로그**에(사용자 2026-09-12 · #43·#222) | ✅ 자동 | `…::test_sort_contribution_goes_to_the_log_not_the_subtitle` |
| 팔레트 전 토큰이 페이지 표면 대비 **WCAG AA 4.5:1** 이상(#355·#96) | ✅ 자동 | `TestPaletteContrastAndDesignDrift20260912::test_every_palette_meets_wcag_aa` |
| 대비 가드가 **실제로 발화**한다 + 반대 증거(#47·#25) | ✅ 자동 | `…::test_contrast_guard_fires_on_a_low_contrast_palette` |
| 텍스트 토큰은 **이름 열거가 아니라 용법**에서 파생(#24) | ✅ 자동 | `…::test_text_tokens_come_from_usage_not_a_hand_list` |
| 칩 전경(짝 배경 보유)은 페이지 표면과 대조하지 않는다 — 오탐 금지(#50) | ✅ 자동 | `…::test_chip_foregrounds_are_not_paired_with_page_surfaces` |
| `body.dark{}` 처럼 `:root` 밖 팔레트도 스캔(#24) | ✅ 자동 | `…::test_dark_variants_on_non_root_selectors_are_scanned` |
| `--text` 가 `-text` 접미로 칩 전경 오인되지 않는다(#47) | ✅ 자동 | `…::test_body_text_token_is_not_mistaken_for_a_chip_foreground` |
| `;` 없는 마지막 선언도 파싱 — 블록째 무검사 금지(#155·#54) | ✅ 자동 | `…::test_last_declaration_without_a_semicolon_is_parsed` |
| 모든 팔레트 블록에 표면이 하나는 있다(#54) | ✅ 자동 | `…::test_every_palette_block_has_a_surface` |
| `DESIGN.md` 색상표 = `bot/dashboard.py` 실값(#55 문서↔코드 드리프트) | ✅ 자동 | `…::test_design_md_palette_matches_dashboard_css` |
| 드리프트 판정이 발화한다 — 본 테스트와 **같은 함수**로(#286) | ✅ 자동 | `…::test_drift_guard_fires_when_the_doc_lies` |
| 표면 파생이 **이름 규약 밖**에서도 돈다(오늘 기여 0 — 픽스처로 경로를 태운다, #291·#286) | ✅ 자동 | `…::test_page_surface_is_derived_from_body_selector_not_only_names` |
| 칩 배경 위 글씨 `--X-on` × `--X` 가 AA 이상 — 전 모듈 전수(#34·#274) | ✅ 자동 | `…::test_accent_on_pairs_meet_wcag_aa` |
| 그 가드가 발화한다 + 짝 없는 `--X-on` 은 재지 않는다(#25·#47) | ✅ 자동 | `…::test_on_pair_guard_fires_and_needs_a_real_partner` |
| `--accent-on` 을 페이지 배경과 대조하지 않는다 — 오탐 금지(#50) | ✅ 자동 | `…::test_accent_on_is_not_measured_against_the_page_background` |
| `_SELECTOR_OK` 가 버린 팔레트 블록 0건 — 버려지면 어떤 가드도 못 본다(#54) | ✅ 자동 | `…::test_no_palette_block_is_silently_rejected` |

## 다음 우선순위 (갭 메우기 후보)
1. `_hard_guard_warn` — 감자/분할 키워드 존재 시 실제로 경고 텍스트가 삽입되는지 직접 단위테스트.
2. `_has_pm_override_trigger`/`_check_pm_override_required` — RSI 경계값(74.9/75.0/25.0/25.1), catalyst
   D-day 경계, data-availability 케이스별 순수 유닛테스트.
3. RULE 1-15 는 근본적으로 LLM 출력 검증이라 pytest 화가 부적합 — 대신 "프롬프트 텍스트가 여전히
   소스에 있는지"(orphan 참조 방지, §Pre-commit 4) 만이라도 grep 회귀로 고정하는 게 현실적 다음 단계.
4. `_extract_stance` Pass 0-3 폴백(정규식/키워드) — sentinel 도입(2026-07-26) 이후에도 구버전
   아카이브·sentinel 미준수 케이스의 안전망이라 여전히 살아있는 코드. 코드 주석에 인용된 실제
   회귀 티커(009450.KS/9988.HK/2382.TW/ALAB/300750.SZ)별로 최소 1개씩 순수 유닛테스트 고정 권장.
