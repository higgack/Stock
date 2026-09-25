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
| 재무부 판정 줄이 **시리즈·갈래 근거**를 실어 셋이 구별된다(#356·#114·#292) | ✅ 자동 | `TestFlowTrendDiagnosis20260818::test_treasury_verdict_names_the_series_and_carries_its_reason` |
| `src=UST` 면 '보강이 걸리지 않았다'고 적지 않는다(#356·#55·#165) | ✅ 자동 | `…::test_treasury_verdict_does_not_claim_enrichment_never_ran` |
| 블로그 2026-09-13 3건 등록(표시명·전체 글) | ✅ 자동 | `TestBlogWatchMultiBlog::test_blogs_config_has_the_20260913_batch` |
| `/blog` 카테고리 꼬리표가 str 을 낱글자로 쪼개지 않는다(#357·#34) | ✅ 자동 | `…::test_category_label_does_not_split_a_string_into_letters` |
| 그 꼬리표를 렌더가 **단일 출처로 부르고 쓴다**(#20·#120) | ✅ 자동 | `…::test_blog_list_renders_the_label_through_the_single_source` |
| drift 배너가 **단일 출처** + 형제 페이지(`_shell`)에도 실린다(#359·#38) | ✅ 자동 | `TestBlogWatchMultiBlog::test_drift_banner_is_a_single_source_on_sibling_pages` |
| 테마 부제에 정렬 기여 상세(`fallCnt+56` 류)가 없다 — 렌더(#359) | ✅ 자동 | `…::test_theme_subtitle_has_no_per_sort_breakdown` |
| 기여 상세는 **로그로** 간다 — 버리지 않는다(#43) | ✅ 자동 | `…::test_sort_contributions_go_to_the_log_not_the_note` |
| 그 계약을 **값으로** 잰다 — 수집기를 통째로 태워(#19·#20) | ✅ 자동 | `…::test_sweep_note_carries_no_breakdown_measured_by_value` |
| 신선도 부제가 표와 **같이** 갱신된다(`#live-sub`, #360a·#43) | ✅ 자동 | `TestBlogWatchMultiBlog::test_freshness_subtitle_refreshes_with_the_data_it_describes` |
| drift 배너가 **세 shell 전부**에 있다(#360b·#38) | ✅ 자동 | `…::test_drift_banner_is_on_every_page_shell` |
| 배너가 **못 보는 축**이 코드에 적혀 있다(#360c·#274) | ✅ 자동 | `…::test_banner_blind_spot_is_written_down` |
| 블로그 RSS 도달 이력 — '한 번도 안 돎' ≠ '실패'(#360d·#54) | ✅ 자동 | `…::test_blog_rss_health_separates_never_ran_from_failure` |
| 그 달을 **못 받은 것**을 '원천 표에 없다' 로 찍지 않는다(#361b·#82) | ✅ 자동 | `TestFlowTrendDiagnosis20260818::test_month_fetch_failure_is_not_called_missing_from_the_source` |
| 대조 실패를 `✅ 전부 최선` 이 덮지 않는다(#361c·#41) | ✅ 자동 | `…::test_why_does_not_bury_a_failed_probe_under_a_green_verdict` |
| **감사도** 같은 자리에서 덮지 않는다(#361c·#38 형제) | ✅ 자동 | `…::test_audit_also_reports_a_failed_probe_under_a_green_line` |
| 실측으로 반증된 blogId 는 빠지고, 안 잰 후보는 안 넣는다(#361a·#12) | ✅ 자동 | `…::test_blog_registry_drops_the_measured_wrong_id` |
| 대조 실패 통지는 **그 주장이 참일 수 있는 자리**에만(#361 리뷰 a·#165) | ✅ 자동 | `…::test_probe_failure_notice_only_speaks_where_it_can_be_true` |
| '대조 실패' 는 열거가 아니라 **여집합**이고 두 표면이 같은 술어(#361 리뷰 b·#24·#38) | ✅ 자동 | `…::test_failed_comparison_is_a_complement_not_a_list` |
| 그 술어가 **감사에 배선**돼 `mismatch` 도 잡힌다(#361 리뷰 b·#20) | ✅ 자동 | `…::test_audit_calls_a_mismatch_a_failed_comparison_too` |
| 전역 `last_fail` 은 **이번에 조회한 달**일 때만 믿는다(#361 리뷰 c·#82) | ✅ 자동 | `…::test_month_failed_needs_the_month_to_have_been_queried` |
| 정체성 대조는 **실측 `channel`** 기준 · 없으면 판정 불가(#362·#54·#165) | ✅ 자동 | `…::test_check_measures_identity_against_the_recorded_channel` |
| 필명 블로그(`hempty`)는 등재 · 실측 반증된 `hempt` 는 미등재(#362·#222) | ✅ 자동 | `…::test_blog_registry_keeps_the_pen_name_blog` |
| 제목 축 필터가 사유까지 돌려준다(#363·#123 계열) | ✅ 자동 | `…::test_title_gate_filters_by_title_and_says_why` |
| 그 게이트가 **수집기에 배선**돼 제외분도 seen 처리(#363·#20) | ✅ 자동 | `…::test_collector_applies_the_title_gate_and_counts_it_apart` |
| 카테고리외·제목외 계수가 서로의 라벨을 빌리지 않는다(#363·#292·#45) | ✅ 자동 | `…::test_skip_counters_do_not_borrow_each_others_label` |
| `/blog` 꼬리표가 **두 축**을 다 부른다(#363·#20·#274 AST) | ✅ 자동 | `TestBlogWatchMultiBlog::test_blog_command_wired` |
| `--check` 제목 판정이 제목마다 찍히고 0건은 ❌ 가 아니다(#363·#260) | ✅ 자동 | `…::test_check_shows_the_title_verdict_per_item` |
| `--check` 가 **어느 코드가 돌았는지** 말한다 — 소스 지문(#364·#21·#119) | ✅ 자동 | `…::test_check_banner_says_which_code_ran` |
| 그 배너가 **두 갈래**(무인자 표·개별 진단) 모두에 실린다(#364·#359·#38·#20) | ✅ 자동 | `…::test_check_banner_says_which_code_ran` |
| 너쟁이 채널은 **실측값**으로 박고 요청 없는 필터는 없다(#364·#222) | ✅ 자동 | `TestBlogWatchMultiBlog::test_blogs_config_has_the_20260913_batch` |
| 지문을 못 구하면 **'지문불가'라고 말한다** — 침묵 금지(#364·#291·#54) | ✅ 자동 | `…::test_banner_says_it_cannot_read_the_fingerprint` |
| `--check` 경로가 다른 `bot.*` 를 안 읽는다 = 지문이 전부를 덮는다(#364·#274) | ✅ 자동 | `…::test_check_banner_covers_everything_check_reads` |
| 의존성 부재를 '도달 실패'가 아니라 **갈래로** 말한다(#364·#82·#132) | ✅ 자동 | `…::test_check_names_the_dependency_branch` |
| 아침 결산이 **어느 코드에서 나왔는지** 말한다 — 두 표면 모두(#365·#359·#38) | ✅ 자동 | `…::test_audit_digest_says_which_code_ran` |
| 그 지문이 감사 모듈·**한 단계 의존** 변경에 반응한다(#365·#91b·#364d) | ✅ 자동 | `…::test_audit_digest_says_which_code_ran` |
| 못 읽은 소스가 있으면 `?` 로 밝힌다 — 조용한 부분 지문 금지(#365·#54) | ✅ 자동 | `…::test_audit_fingerprint_marks_partial_coverage` |
| `sweep()` 이 싣는 지문이 **진짜 지문**이다 — 배선(#365·#20) | ✅ 자동 | `…::test_audit_digest_says_which_code_ran` |
| 지문이 **주기에 안 흔들린다**(월요일 주간 감사 포함해도 동일, #365) | ✅ 자동 | `…::test_audit_digest_says_which_code_ran` |
| 배너가 **돈 개수를 사실대로** 적는다(`7/10종`, #365·#55) | ✅ 자동 | `…::test_audit_digest_says_which_code_ran` |
| 반응성 측정이 **레포 파일을 안 건드린다**(mtime 부작용 금지, #365·#328) | ✅ 자동 | `…::test_audit_digest_says_which_code_ran` |
| `--check` 가 **모듈 레벨**에서도 다른 `bot.*` 를 안 읽는다(#365·#286) | ✅ 자동 | `…::test_check_banner_covers_everything_check_reads` |
| ECOS 원천 최신월이 **화면 asof 형식**으로 온다 — 형식 어긋나면 전부 오판(#366·#34) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 절단이면 값 대신 **사유**를 준다(1쪽 최댓값 ≠ 원천 끝, #366·#54) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 감사가 `ecos:` 를 그 함수로 보내고 **호출부가 `src` 를 넘긴다**(#366·#20) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 표면 파생이 **이름 규약 밖**에서도 돈다(오늘 기여 0 — 픽스처로 경로를 태운다, #291·#286) | ✅ 자동 | `…::test_page_surface_is_derived_from_body_selector_not_only_names` |
| 칩 배경 위 글씨 `--X-on` × `--X` 가 AA 이상 — 전 모듈 전수(#34·#274) | ✅ 자동 | `…::test_accent_on_pairs_meet_wcag_aa` |
| 그 가드가 발화한다 + 짝 없는 `--X-on` 은 재지 않는다(#25·#47) | ✅ 자동 | `…::test_on_pair_guard_fires_and_needs_a_real_partner` |
| `--accent-on` 을 페이지 배경과 대조하지 않는다 — 오탐 금지(#50) | ✅ 자동 | `…::test_accent_on_is_not_measured_against_the_page_background` |
| `_SELECTOR_OK` 가 버린 팔레트 블록 0건 — 버려지면 어떤 가드도 못 본다(#54) | ✅ 자동 | `…::test_no_palette_block_is_silently_rejected` |
| 태그 표가 **전수**다 — 수집기가 대입하는 리터럴 ⊆ `_VAL_TAG_INFO`(#367·#24·#38) | ✅ 자동 | `…::test_every_tag_the_collector_assigns_is_in_the_one_table` |
| 원천 라벨은 **모르는 태그를 지어내지 않는다**(#367·#165) | ✅ 자동 | `…::test_source_label_is_pure_and_admits_what_it_does_not_know` |
| 네이버 미매핑 카드가 `change`/`change_pct` 를 싣는다 — 파생 **위치**까지(#367·#123) | ✅ 자동 | `…::test_naver_unmapped_card_now_carries_its_previous_value` |
| 값·차트 끝점이 **같은 계열**이다(#367·#33) | ✅ 자동 | `…::test_naver_unmapped_card_now_carries_its_previous_value` |
| 카드가 **어느 원천**이 채웠는지 말한다 + 발표지표엔 안 붙인다(#367·#34·#25) | ✅ 자동 | `…::test_the_card_says_which_source_filled_it` |
| 나이를 **값이 온 그 캐시 파일**에서 잰다(월간↔일봉 혼동 금지, #367·#35) | ✅ 자동 | `…::test_age_is_measured_on_the_file_that_actually_filled_the_value` |
| 일봉 결측 시 **월간 꼬리 폴백이 살아 있다**(#367·#148) | ✅ 자동 | `…::test_monthly_tail_still_rescues_a_card_with_no_daily_bars` |
| ℹ️ 가이드가 카드와 같은 말을 한다(설명 out-of-sync = 버그, #367·#55) | ✅ 자동 | `…::test_guide_explains_the_differing_collection_times` |
| ECOS 메타가 **화면 asof 모양**으로 대조된다(`_ecos_iso`, #366·B1·#35) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 진단이 화면과 **같은 표/아이템 후보**로 질의한다(빈 ITEM 금지, #366·H1) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 감사가 화면과 **같은 조회창**을 넘긴다(`_ALT_LOOKBACK`, #366·#35) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 절단 사유가 증거 줄에 **값으로** 실린다 + 배선(#366·H2·#176·#291) | ✅ 자동 | `…::test_ecos_source_end_splits_the_two_verdicts` |
| 파생을 옮겨도 **네이버 20장의 직전**은 그대로(#367·M1·#91b) | ✅ 자동 | `…::test_naver_unmapped_card_now_carries_its_previous_value` |
| 나이 '미기록' 카드에도 원천 라벨이 실린다(#367·L1·#291) | ✅ 자동 | `…::test_the_card_says_which_source_filled_it` |
| 장단기금리차도 재무부 값으로 당긴다 — 만기만 보강하면 10Y−2Y 가 카드와 안 맞는다(#368·#33) | ✅ 자동 | `…::test_treasury_overrides_fred_only_when_it_checks_out` |
| 파생 스프레드는 **재료가 둘 다** 있을 때만 · 역전은 보존(#368·#88·#29) | ✅ 자동 | `…::TestTreasurySpreadAndRetry20260914::test_spread_needs_both_legs_and_keeps_inversion` |
| 곡선이 스프레드를 싣고 겹치는 날 검산이 여전히 문다(#368·#20) | ✅ 자동 | `…::test_curve_carries_the_spread_and_the_overlap_guard_still_bites` |
| 일별 캐시 집합은 `macro_cadence` 표에서 파생(#368·#24·#38) | ✅ 자동 | `…::test_daily_cache_set_comes_from_the_cadence_table` |
| 재시도는 **답이 바뀔 갈래**에만 — 4xx 제외(#368·#82·#279) | ✅ 자동 | `…::test_retry_is_only_for_kinds_that_can_change_their_answer` |
| 렌더 1회 · 배치만 재시도, 4xx 는 즉시 중단(#368·#116·#128) | ✅ 자동 | `…::test_render_asks_once_and_batch_retries_then_stops_on_4xx` |
| 진단은 실패 캐시를 안 믿고 성공 캐시는 존중(#368·#35·#54) | ✅ 자동 | `…::test_diagnostics_do_not_trust_a_stale_failure_cache` |
| 배치 표면 둘이 `attempts` 를 **인자로** 넘긴다(#368·#20·#141) | ✅ 자동 | `…::test_batch_surfaces_actually_pass_attempts` |
| 네이버 프로브 판정은 3-상태 — 한쪽을 못 재면 ✅ 도 ❌ 도 아니다(#368·#54·#143) | ✅ 자동 | `…::test_naver_probe_verdict_never_says_ok_without_both_sides` |
| 프로브 배너 지문이 **제 소스에 반응**한다(#368·#364·#91b) | ✅ 자동 | `…::test_naver_probe_banner_reacts_to_its_own_source` |
| 합리성 가드는 **같은 날**끼리 — 시장 필드가 끊긴 구간에 통째 드롭 금지(#369·#45) | ✅ 자동 | `…::TestCreditSplitWhyAndDateAlignment20260914::test_a_partially_filled_latest_row_no_longer_drops_everything` |
| 빈 결과는 갈래를 이름으로(키·냉각·요청실패·0건·필드미발견·가드)(#369·#82) | ✅ 자동 | `…::test_every_empty_result_names_its_branch` |
| 빈 결과는 **짧게만** 믿는다 — 성공은 1시간 그대로(#369·#152·#303) | ✅ 자동 | `…::test_empty_results_are_believed_only_briefly` |
| 값만 필요한 자리용 얇은 래퍼 유지(#369·#129) | ✅ 자동 | `…::test_thin_wrapper_still_returns_only_the_value` |
| 사유가 릴레이되고 카드가 **사라지는 대신 말한다**(#369·#20·#43·#335) | ✅ 자동 | `…::test_reason_is_relayed_and_the_card_speaks_instead_of_vanishing` |
| 각주 클래스는 **그 페이지 번들**에 정의돼 있다(#369·#201·#273·#299) | ✅ 자동 | `…::test_the_note_uses_a_class_this_page_bundle_defines` |
| FCF ❌ 줄이 재료를 **같은 줄에** 싣는다(분기·연간 둘 다)(#369·#356·#38) | ✅ 자동 | `…::TestFcfFindingLineCarriesMaterials20260914::test_materials_ride_on_the_finding_line_not_the_next_one` |
| 커밋된 판 대비 **조용히 사라진** 공개 심볼을 센다(§Pre-commit 7e·#210) | ✅ 자동 | `…::TestPublicSurfaceCheck20260916::test_guard_fires_on_a_silently_deleted_public_symbol` |
| 커밋된 판 대비 **줄어든 `def test_`** 를 센다(§Pre-commit 7e) | ✅ 자동 | `…::TestPublicSurfaceCheck20260916::test_guard_fires_on_a_dropped_test` |
| 기준이 **둘**(base·HEAD)이다 — base 만 보면 신규 심볼 삭제가 조용하다 | ✅ 자동 | `…::test_both_baselines_are_scanned` |
| 테스트 **이름 변경**·private 변동은 소음이라 세지 않는다(#25·#260) | ✅ 자동 | `…::test_renaming_a_test_is_not_a_finding` · `…::test_private_churn_is_not_a_finding` |
| `public_surface_check` **진입점**이 두 기준을 다 찍는다(#20·#54) | ✅ 자동 | `…::test_the_entry_point_reports_both_baselines` |
| base 기준은 **merge-base** 다 — 공용 base 가 앞서가도 오탐 없음 | ✅ 자동 | `…::test_base_baseline_uses_the_merge_base` |
| 중복 정의 스캔이 **모든 스코프**(클래스 본문 포함)를 본다(#59·#68) | ✅ 자동 | `…::TestShadowedTopLevelDefs20260906::test_guard_also_fires_inside_a_class_body` |
| 중복 정의 스캔 범위에 **`tests/`** 가 들어 있다(#24·#91b) | ✅ 자동 | `…::test_the_scan_actually_covers_the_test_tree` |
| if/else 폴백의 같은 이름은 오탐이 아니다(#25·#260) | ✅ 자동 | `…::test_if_else_branches_defining_the_same_name_are_not_flagged` |
| §Help "제거 시 해당 줄도 제거" — HELP_TEXT 에만 있는 **은퇴 명령** 금지 | ✅ 자동 | `…::TestCpiBoardWiring20260724::test_help_text_has_no_retired_commands` |
| 두 파티 방향이 **다른 추출**을 쓴다(합치면 한쪽이 눈먼다)(#47) | ✅ 자동 | `…::test_both_help_parity_directions_use_different_extraction` |
| 한국 회사별 금액판 파서가 회사·품목·**전 개월**을 읽는다(#370·#83) | ✅ 자동 | `…::TestKoreaCompanyFlowBoards20260916::test_flow_parser_reads_company_item_and_every_month` |
| 방향 마커가 수출↔수입을 가른다 — 남의 DB 로 안 들어간다(#370·#83) | ✅ 자동 | `…::test_direction_marker_keeps_the_two_boards_apart` |
| 마커 세 낱말은 **한 줄** 안에 있어야 한다(줄 넘으면 남의 글이 샌다) | ✅ 자동 | `…::test_marker_words_must_be_on_one_line` |
| 한 캡션에 두 회사면 **헤더 구간만** 훑는다(#38 형제 가드 이식) | ✅ 자동 | `…::test_two_companies_in_one_caption_do_not_bleed` |
| YoY·MoM 은 선택 — 한 칸이 없다고 캡션을 드랍하지 않는다(#83·#43) | ✅ 자동 | `…::test_missing_yoy_or_mom_still_stores_the_amount` |
| 회사명 표기가 갈려도 카드가 둘이 되지 않는다(양방향·반대 증거)(#45·#146) | ✅ 자동 | `…::test_company_name_spelling_does_not_split_the_card` |
| `PARSE_VER` 리셋이 **실제로 칸을 비운다**(옛 회귀는 무가드였다, #291) | ✅ 자동 | `…::test_parse_ver_bump_clears_a_field_the_new_parse_leaves_empty` |
| 합친 수출판도 `parse_ver` 을 싣는다 — 옛 지표판 칸은 안 건드린다 | ✅ 자동 | `…::test_export_board_also_carries_the_parser_version` |
| 레지스트리에서 **한 캡션의 주인은 하나**(SOURCES 순서 = 폴백 순서) | ✅ 자동 | `…::test_exactly_one_registry_source_claims_each_caption` |
| ▶️ 줄에 대시가 없으면 품목판 — 품목을 회사 칸에 넣지 않는다(#34·#77) | ✅ 자동 | `…::test_item_only_caption_is_not_stored_as_a_company` |
| 수출 금액판이 **기존 종목별 페이지**에 실린다(사용자 2026-09-16 결정) | ✅ 자동 | `…::test_export_rows_land_on_the_existing_page` |
| 합성키(`nm:`)가 같은 회사를 두 카드로 쪼개지 않는다(양방향)(#45) | ✅ 자동 | `…::test_synthetic_key_never_duplicates_a_company_card` |
| 합성키 흡수는 **병합**이다 — `OR REPLACE` 는 옛 판 값을 지운다(#45·#291) | ✅ 자동 | `…::test_absorbing_a_synthetic_key_merges_instead_of_replacing` |
| 금액판엔 상관·단가가 없다 — 없는 것을 지어내지 않는다(#32·#43) | ✅ 자동 | `…::test_flow_rows_do_not_fabricate_correlation_fields` |
| 수입 페이지 **카드**가 수출이라고 말하지 않는다(#55·#34) | ✅ 자동 | `…::test_import_page_speaks_its_own_direction` |
| 빈 수입 페이지도 렌더된다 — nav 404 차단 | ✅ 자동 | `…::test_empty_import_page_still_renders` |
| `PARSE_VER` 를 올리면 구운 행이 **다시 파생**된다(#18·#21b) | ✅ 자동 | `…::test_parse_ver_bump_rederives_baked_rows` |
| nav 순서가 새 소스를 **형제 옆**에 놓는다(레지스트리 파생) | ✅ 자동 | `…::test_registry_places_the_new_source_next_to_its_sibling` |
| 모든 소스가 **캡션 문법**을 밝힌다 — 안 밝히면 형제 계약 밖(#370·#24·#54) | ✅ 자동 | `…::test_every_source_declares_its_caption_grammar` |
| 상관 4지표 계약의 **대상 집합이 조용히 줄지 않는다**(하한 리터럴, #66) | ✅ 자동 | `…::test_the_corr_contract_scope_cannot_silently_shrink` |
| `test_*.py` 를 담은 **모든 트리**가 `make test` 안에 있다(#370·#24·#54) — 트리는 **커밋될 파일**(git 추적 + add 전 새 파일, 무시 목록 밖)에서 센다: 디스크 전체를 훑던 옛 판은 무시된 서브에이전트 worktree(`.claude/worktrees/…`, 레포 사본)를 게이트 밖 트리로 세 리뷰가 도는 동안 거짓 빨간불이었다(#412) | ✅ 자동 | `…::test_every_test_tree_is_inside_the_commit_gate` · `…::test_the_gate_scope_is_what_git_would_commit`(임시 저장소 — 무시·가상환경·최상위 파일은 빼고 추적·add 전 새 트리는 센다 · 하위 트리는 상위가 덮는다 — 단 **'/' 경계로만**(형제 접두 `tests_e2e` 는 `tests` 에 안 접힌다) · `.py` 가 아닌 `test_*` 는 트리가 아니다 · 임시 저장소의 git 은 바깥 `GIT_*` 환경을 물려받지 않는다(훅·`rebase --exec` 가 넘긴 인덱스를 고치지 않는다 — 흉내 낸 바깥 인덱스가 안 생기는지 잰다) · 뮤테이션 9종 전부 잡힘) |

### 거래량 상위 보드 + KRX/NXT 세션 창 (2026-09-16, 실수 #371)
`tests/test_regression.py::TestKrVolumeAndSessions20260916`

| 계약 | 상태 | 테스트 |
|---|---|---|
| 공지 153 창이 **단일 출처** — KRX 애프터 16:00, NXT 15:40(20분 차) | ✅ 자동 | `…::test_notice153_windows_are_the_single_source` |
| KRX 엔 프리마켓이 없다 — 없는 것을 있는 척하지 않는다(#43) | ✅ 자동 | 〃 |
| NXT 보드 창이 옛 리터럴과 **동작 동일** + 소스에 리터럴 잔존 금지(AST, #38) | ✅ 자동 | `…::test_nxt_board_windows_come_from_the_single_source` |
| 거래량 정렬 키는 **거래대금이 아니다**(#34·#221) | ✅ 자동 | `…::test_volume_sort_key_is_learned_not_invented` |
| 정렬 키를 **원천에게 배우고** 디스크에 적어 재요청을 안 쏜다(#350·#353) | ✅ 자동 | `…::test_learn_sort_type_asks_the_origin_and_caches` |
| 허용값에 거래량이 없으면 빈 화면이 아니라 **사유**(#43·#82) | ✅ 자동 | `…::test_origin_without_a_volume_sort_says_so_instead_of_emptying` |
| 배운 키가 400 이면 **다시 배운다**(#24 — 원천 enum 드리프트) | ✅ 자동 | `…::test_rejected_learned_key_is_relearned` |
| 고가·저가가 안 오면 **말한다**, 오면 조용하다(#43·#25·#260) | ✅ 자동 | `…::test_missing_high_low_is_stated_not_silently_blank` |
| 네이버 칼럼 8종 + 사유가 **보이는 줄**(#228) | ✅ 자동 | `…::test_page_draws_naver_columns_and_shows_the_reason` |
| nav 순서 = 거래량 상위 → 시간외 보드(단일 출처·폴백 동일) | ✅ 자동 | `…::test_nav_order_puts_volume_before_the_nxt_movers_board` |
| 라우트·no-cache·라이브 폴링 배선(#20) | ✅ 자동 | `…::test_route_and_live_polling_are_wired` |
| 서버 TTL < 화면 폴링 주기(#36 — 아니면 '2분 갱신' 이 거짓) | ✅ 자동 | `…::test_volume_cache_ttl_is_shorter_than_the_poll_interval` |
| 네이버증권은 **nav 전용**이고 그 축이 조용히 커지지 않는다 | ✅ 자동 | `…::test_naver_stock_is_nav_only` |
| KR 자식 링크 수 = nav 레지스트리 탭 수(옛 `== 5` 스냅샷 대체, #222) | ✅ 자동 | `TestNaverWidgetSilence20260911::test_reason_branch_keeps_the_child_page_links` |

⚠️ 못 보는 축(#274): **시간외 블록의 체결 귀속**은 여전히 미측정이다 —
네이버 `overMarketPriceInfo` 가 어느 거래소 체결인지 모른다. 단 "시간외 블록이
하나뿐이고 응답에 거래소를 **이름으로** 가르는 필드가 없다"는 2026-09-17 프로브
실측으로 확정됐다 → 아래 §venue 축 실측 절. 화면은 그 절반만 사실로 적는다.
그래서 2026-09-17 에 거래소별 두 보드를 **한 장으로 합쳤다**(실수 #378) —
KRX 창이 NXT 창의 진부분집합이라 KRX 보드는 한 행도 더 내놓을 수 없었다.

### KR 시간외 보드 — 거래소 중립 한 장 (2026-09-17, 실수 #378)
`tests/test_regression.py::TestKrOverBoardMerged20260917`

2026-09-16 에 공지 153(KRX 애프터마켓 신설)을 보고 `/krafter`(KRX)·`/krprepost`
(NXT) 두 장을 뒀는데, 창 표를 보면 KRX 창(16:00–20:00)이 NXT 창(08:00–09:00 ·
15:40–20:00)의 **진부분집합**이고 네이버 시간외 블록은 하나뿐이다(#373b) — 두
보드는 정의상 같은 목록을 냈고, 화면에선 그게 "두 시장의 시간외가 같다"는 시장
주장으로 읽혔다(#375). 사용자 2026-09-17 "합치기로 하자 … 미국처럼 장후로".

| 계약 | 상태 | 테스트 |
|---|---|---|
| 합집합 창이 **어느 거래소 창이든** 덮는다(5분 격자 전수·이름 열거 금지, #24·#171) | ✅ 자동 | `…::test_union_window_covers_every_venue_window` |
| 오늘은 합집합 = NXT 창 → 이 변경은 **동작을 안 바꾼다**(값으로 못박음) | ✅ 자동 | `…::test_union_window_is_todays_nxt_window_and_says_so` |
| 부분집합이 **아닌** 세계에서 합집합이 실제로 넓어진다(#91c 발화 경로) | ✅ 자동 | `…::test_union_window_widens_when_a_venue_widens` |
| 합집합 세션이 pre/post 를 말한다(`pre_close` 포함) | ✅ 자동 | `…::test_union_session_names_pre_and_post` |
| 수집기를 태워 **캐시 1 + 상태 1** 에 쓰고 옛 거래소 파일은 안 만든다(#20·#53) | ✅ 자동 | `…::test_scan_writes_one_cache_and_one_status` |
| 스캔은 한 번에 하나(stampede, #113) | ✅ 자동 | `…::test_only_one_scan_runs_at_a_time` |
| `start()` 가 던져도 플래그가 안 선다 — 영구 정지 금지(#280) | ✅ 자동 | `…::test_kick_releases_the_flag_when_the_thread_cannot_start` |
| 화면이 **재지 않은 귀속**을 보이는 줄로 밝힌다(#43·#165·#228) | ✅ 자동 | `…::test_page_states_what_it_is_actually_measuring` |
| 제목·부제·패널 제목이 **거래소를 주장하지 않는다**(#375·#55 표면별) | ✅ 자동 | `…::test_page_never_claims_a_venue` |
| 부제 창이 `kr_session` 합집합 단일 출처에서 온다(#55·#38) | ✅ 자동 | `…::test_page_says_both_windows_in_its_subtitle` |
| 집계 실패 배너도 거래소를 말하지 않는다(#91b 배너 블록만 잘라서) | ✅ 자동 | `…::test_banner_does_not_name_a_venue` |
| 실패 배너가 **없는 스냅샷**을 '아래에 있다' 고 말하지 않는다(#55·#43 · 반대 증거 포함) | ✅ 자동 | `…::test_banner_does_not_promise_a_snapshot_that_is_not_there` |
| 패널 제목 세션 라벨이 원천 세션을 따른다(pre→장전 · post→장후 · 미상→둘 다) | ✅ 자동 | `…::test_panel_titles_follow_the_session` |
| `union_session` 의 "충돌 시 pre 가 이긴다" 규약(합성 거래소로 실제 발화, #291) | ✅ 자동 | `…::test_pre_wins_when_two_venues_disagree` |
| `union_session` 이 거래소를 **열거하지 않는다**(합성 거래소 전용 창으로 발화, #24·#291) | ✅ 자동 | `…::test_union_session_is_derived_not_enumerated` |
| 한 구간이 두 국면에 걸치면 '시간외' 라고만 적는다(독스트링 규약을 가드로, #286) | ✅ 자동 | `…::test_a_span_covering_both_phases_is_named_시간외` |
| `_run` 의 `global` — 스캔이 끝나면 플래그가 내려간다(정상·예외 두 분기, #280) | ✅ 자동 | `…::test_the_flag_is_released_after_the_scan_finishes` · `…::test_the_flag_is_released_when_the_scan_raises` |
| 배너가 거래소를 말하지 않는다 — **스냅샷 유무 × 실패/진행 4분기 전부**(#343) | ✅ 자동 | `…::test_banner_does_not_name_a_venue` |
| 워머 게이트도 수집기와 **같은 술어**(`union_extended_window`)에서 온다(#38·#24) | ✅ 자동 | `…::test_route_and_polling_are_wired` |
| 폴링 기본값을 리터럴로 안 박고 `live_refresh` 에서 파생해 서버 TTL 과 비교(#66·#36) | ✅ 자동 | 〃 |
| KRX 보드가 **모든 표면에서** 사라졌다 — 렌더러·헬퍼·nav·라우트·폴링·워머(#286 양방향) | ✅ 자동 | `…::test_the_krx_board_is_gone_everywhere` |
| 탭 라벨이 **미국 보드와 같다**(사용자 "미국처럼") + 거래량 상위 뒤 순서 유지 | ✅ 자동 | `…::test_nav_has_one_after_hours_tab_named_like_the_us_board` |
| 라우트·no-cache·폴링·워머 배선 + `_HELP_TEXT` 에 사라진 탭이 없다(#20·§Help) | ✅ 자동 | `…::test_route_and_polling_are_wired` |

⚠️ 못 보는 축(#274): **네이버 시간외 블록의 체결 귀속**(KRX인가 NXT인가)은
여전히 미측정이다. 2026-09-17 프로브가 답한 것은 **절반**(같은 블록을 본다)이고
그건 화면 문구에 반영됐다 → 아래 §venue 축 실측 절. 화면은 남은 절반을
**보이는 줄**로 밝힌다(#43·#165) — 라벨을 지어내지 않는다. 그리고 "KRX 창이
NXT 창의 부분집합" 은 **원천이 밝힌 창**(공지 153)에서 나온 연역이지 우리가
체결을 잰 것이 아니다 — 원천이 창을 바꾸면 이 근거도 같이 바뀐다(#165).
⚠️ 거래소별 두 보드 시절의 계약(캐시 분리·공유 스캔·KRX 전용 문구)은 지운
것이 아니라 **다시 쓴 것**이다(#222) — 어느 것이 왜 바뀌었는지는 위 테스트
독스트링에 남아 있다.

### KR 보드 독립 리뷰 반영분 (2026-09-16, 실수 #371)
`tests/test_regression.py::TestKrBoardsReviewFixes20260916`

| 계약 | 상태 | 테스트 |
|---|---|---|
| B1 스캔 경로가 **하나**이고 보드를 읽어도 또 스캔하지 않는다(#61·#113·#222) | ✅ 자동 | `…::test_one_scan_path_means_no_double_polling` |
| H2 필터 **전에** 넉넉히 받아 요청한 수를 채운다 + 제외 수를 말한다(#148·#45) | ✅ 자동 | `…::test_volume_board_overfetches_so_the_filter_does_not_shrink_it` |
| H3 원천이 sortType 을 검증 안 하면 **미끼 응답 행을 쓰고** partial 로 밝힌다 | ✅ 자동 | `…::test_probe_rows_are_used_instead_of_a_blank_board` |
| H4/M6 배너·부제의 창이 **단일 출처**에서 온다(리터럴 금지, #38·#55·#222) | ✅ 자동 | `…::test_banner_and_subtitle_come_from_the_single_source` |
| H5 창 문구가 프리마켓 08:00–09:00 을 **안 잘라먹는다**(#43) | ✅ 자동 | `…::test_window_label_matches_the_scanner_window` |
| M7 네이버 일시정지는 실패가 아니라 **판정 보류**(#279·#345) | ✅ 자동 | `…::test_health_row_is_registered_and_pause_is_not_a_failure` |
| M8 원천이 막히면 **직전 저장분**을 💾 로 밝혀 내보낸다(#306·#335) | ✅ 자동 | `…::test_fetch_failure_falls_back_to_the_snapshot_and_says_so` |
| M9 부분·실패 결과는 **캐시하지 않는다** + 사유를 모아 잇는다(#280·#207) | ✅ 자동 | `…::test_partial_scan_is_not_cached_and_names_its_own_reason` |
| L14 칼럼 순서가 네이버와 같다(거래량·거래대금 → 고가·저가) | ✅ 자동 | `TestKrVolumeAndSessions20260916::test_page_draws_naver_columns_and_shows_the_reason` |
| L15 거래대금·체결강도 류를 거래량 정렬로 고르지 않는다(#34) | ✅ 자동 | `…::test_volume_sort_rejects_lookalike_keys` |
| L16 재학습은 **옛 키를 먼저 지운다**(죽은 키가 30일 살아남지 않게) | ✅ 자동 | `…::test_relearn_clears_the_dead_key` |
| L17 시간외 보드 폴링(30초)이 서버 재집계 TTL(120초)보다 짧다(#36) | ✅ 자동 | `TestKrOverBoardMerged20260917::test_route_and_polling_are_wired` |
| L18 킥 스텁은 stdlib 가 아니라 모듈 로컬 `_spawn` 에(#30) + 스레드 시작 실패가 보드를 영구 잠그지 않는다 | ✅ 자동 | `…::test_failed_thread_start_does_not_wedge_the_venue` |

### KR 보드 프로브 실측 반영 (2026-09-16, 실수 #372)
`tests/test_regression.py::TestKrBoardProbeMeasured20260916`

| 계약 | 상태 | 테스트 |
|---|---|---|
| 오류 봉투 **세 번째 형태**(파이프 따옴표 목록)를 읽고 옛 괄호 목록도 유지(#73·#350·#352·#222) | ✅ 자동 | `…::test_third_error_envelope_is_read` |
| 실측 허용값에 `volume` 이름이 **없다** — 이름 기반 판정은 발화 경로가 없다(#291) | ✅ 자동 | `…::test_no_allowed_value_contains_volume` |
| 시험 예산(#116) 안에 랭킹 키(`…Top`) 셋이 들어온다 | ✅ 자동 | `…::test_ranking_candidates_fit_the_trial_budget` |
| 정렬 키는 **실호출 실측**으로 고른다 — 이름만으론 채택 금지(#46·#25·#151) | ✅ 자동 | `TestKrVolumeAndSessions20260916::test_name_alone_never_decides_the_sort_key` |
| 프로브가 dict·list 값을 **접지 않는다**(#156·#338·#350 자르는 자리가 결정을 가림) | ✅ 자동 | `…::test_probe_does_not_fold_dict_values` |
| 섹션을 건너뛰면 **건너뛴 사실을 적고** 마지막 줄은 돈 것만 말한다(#54·#286) | ✅ 자동 | `…::test_probe_says_when_it_skipped_a_section` |
| 학습 실패는 **10분만** 믿고(#303·#152) 남은 시간을 말한다(#202) | ✅ 자동 | `…::test_repeated_failures_are_cooled_down_not_retried_every_render` · `…::test_cooldown_note_says_remaining_not_the_cap` |
| 확정하면 냉각을 푼다 — 영구 정지 금지(#178) | ✅ 자동 | `…::test_successful_relearn_clears_the_cooldown` |
| 일시정지(rank 0)는 실패가 아니라 **판정 보류** — 도장을 안 찍는다(#79·#143·#345) | ✅ 자동 | `…::test_a_transient_failure_is_named_and_not_mistaken_for_no_volume_sort` |
| **M2** 5행으로는 시총순과 거래량순이 안 갈린다 — `min_rows` 발화(#91·#291) | ✅ 자동 | `…::test_five_rows_cannot_decide_volume_order` |
| **M2** 짝: `_TRIAL_ROWS ≥ 판정 하한` + `size=` 배선(#20·#171) | ✅ 자동 | `…::test_trial_asks_for_enough_rows_to_decide` |
| **M4** 동시 학습은 하나만 — 탭 N 개가 7N 콜을 쏘지 않는다(#113) | ✅ 자동 | `…::test_concurrent_learns_run_the_body_once` |
| 파이프 경로의 미끼 배제·1항목 거부에 **발화 경로**를 준다(#291·#38) | ✅ 자동 | `…::test_piped_list_that_echoes_our_junk_is_not_the_allowed_list` · `…::test_piped_pair_with_a_blank_side_is_not_a_list` |

⚠️ 못 보는 축(#274): **KRX/NXT 체결 귀속은 여전히 미측정**이다. 단 2026-09-16
실측이 못 본 축 유무(`stockExchangeType`·`integratedPriceInfo` 가 접혀 찍혔다)는
프로브를 고친 뒤 2026-09-17 실행이 답했다 → 아래 §venue 축 실측 절. 화면 문구는
그 절반을 반영해 갱신됐다(같은 블록 = 사실 · 체결 귀속 = 미측정).

### venue 축 실측 · 미파싱 캡션 가시화 (2026-09-17, 실수 #373)
`tests/test_regression.py::TestVenueAxisAndUnparsed20260917`

| 계약 | 상태 | 테스트 |
|---|---|---|
| `통합 = 본체 + 시간외` 항등식(실측 원 단위) — 어긋나면 ❌, 블록 없으면 판정 불가(#54) | ✅ 자동 | `…::test_integrated_is_regular_plus_after_hours` · `…::test_composition_check_fires_when_the_identity_breaks` |
| 이 응답엔 거래소를 **이름으로** 가르는 필드가 없다 + 있으면 잡힌다(#25 반대 증거) | ✅ 자동 | `…::test_response_has_no_venue_named_field` |
| 화면은 **확정된 절반**(같은 블록)만 적고 거래소는 주장하지 않는다(#165·#222) | ✅ 자동 | `…::test_screen_states_the_measured_half_and_claims_no_venue` |
| 그 줄이 **렌더까지** 실린다 — 헬퍼만 재면 배선을 못 잡는다(#20) | ✅ 자동 | `…::test_the_note_reaches_the_rendered_page` |
| 어느 소스도 안 받은 캡션을 **머리까지** 찍는다(#82·#332) · 0건도 말한다(#274) | ✅ 자동 | `…::test_unparsed_captions_are_named_not_just_counted` · `…::test_zero_unparsed_still_says_zero` |
| **B1** 테스트가 운영 `~/.trade/ignored.txt` 를 읽지·만들지 않는다(#30·#294) | ✅ 자동 | `…::_isolate_ignore_list`(두 CLI 테스트가 호출) |
| **H1** ④ 의 두 줄이 **찍히는지**까지 — 순수 테스트만으론 print 삭제가 통과(#20·#313) | ✅ 자동 | `…::test_section_venue_prints_the_axis_and_the_composition` |
| **M4** 창 밖(시간외 블록 없음)은 '없음' 이 아니라 **판정 불가**(#41·#54) | ✅ 자동 | `…::test_outside_the_window_the_axis_is_unjudged_not_absent` |
| **L6** 구성 검산 ❌ 가 마지막 줄·rc 에 실린다(#123 계열) | ✅ 자동 | `…::test_composition_mismatch_reaches_the_summary_and_rc` |
| **M1** `_n` 콤마 경로에 발화 경로 — 출력의 콤마는 포매터가 만든다(#75·#291) | ✅ 자동 | `…::test_comma_only_payload_is_parsed` |
| **L1·M2** 반대 증거는 KRX·NXT **둘 다** + 리스트 중첩(#25) | ✅ 자동 | `…::test_response_has_no_venue_named_field` |
| **L2·M3** 머리 160자 자르기 + 형제와 **같은 시각 포맷**(`… UTC · msg N ·`) | ✅ 자동 | `…::test_long_caption_head_is_cut` |
| **L3** 플래그 off 면 조용하고 적재 계수는 그대로(#291) | ✅ 자동 | `…::test_flag_off_keeps_quiet_and_does_not_change_ingest` |
| **H5** 미측정 절반을 **두 줄 모두** + 거래소 주장 금지는 denylist 가 아니다(#19·#75) | ✅ 자동 | `…::test_screen_states_the_measured_half_and_claims_no_venue` |

⚠️ 못 보는 축(#274): **시간외 블록의 체결 귀속은 여전히 미측정**이다. 산수
(`본체 + 시간외 = 통합`)는 '본체' 가 KRX 정규장인지 KRX 전체인지 못 가르고 두
가설이 같은 수치를 낸다(#255). 다른 엔드포인트도 안 쟀다 — `venue_axis_paths`
는 **이름**으로만 보므로 값으로만 가르는 필드(`marketSessionType: "NXT_AFTER"`
류)가 있으면 놓친다(리스트 중첩은 `_walk` 가 재귀하므로 본다).
그리고 `--show-unparsed` 는 **`unparseable` 분기만** 본다 — 형제 파서가 먼저
가져간 캡션과 `ingest` 가 조용히 `stored=False` 로 끝난 건은 이 플래그에도
`unstored_check` 에도 안 잡힌다(다음 라운드가 낭비되지 않게 help 에도 적었다).

## 거래량 보드 칸·업종 + FRED 선택기 + 백필 소스별 계수 (2026-09-17, #374)

| 계약 | 상태 | 테스트 |
|---|---|---|
| 원천이 고가·저가를 안 주면 **칸 자체가 사라진다**(각주 아님, #25·#260) | ✅ 자동 | `tests/test_regression.py::TestKrVolumeIndustryAndHl20260917::test_high_low_columns_vanish_when_the_source_does_not_give_them` |
| '원천이 안 준다' 와 '내 파서가 이름을 모른다' 를 가른다(#372) · 52주 류는 ❌ 아님(#34·#260) | ✅ 자동 | `…::test_hl_verdict_splits_source_gap_from_parser_gap` · `…::test_hl_key_candidates_marks_the_names_we_do_not_read` |
| 업종은 형제 보드와 **같은 맵**(#38) + 분포 줄 + 빈 사유는 보이는 줄(#43) | ✅ 자동 | `…::test_industry_column_and_distribution_are_wired` · `…::test_empty_industry_says_why_on_a_visible_line` |
| 부제가 업종 **출처**를 말한다(규칙 10b) | ✅ 자동 | `…::test_subtitle_names_the_industry_source` |
| `--why` 가 배너·②③④ 를 **찍는다**(#20·#364) · 행 0 이면 rc=1(#54) | ✅ 자동 | `…::test_why_prints_the_verdicts_and_the_fingerprint` · `…::test_why_returns_one_when_there_are_no_rows` |
| ③ 은 키 이름이 아니라 **갈래 문구**를 찍는다(#75·#313) | ✅ 자동 | `…::test_why_prints_the_verdict_itself_not_just_the_key_names` |
| ④ 업종 섹션이 실제로 찍힌다(붙은 수 · 0이면 사유) — 리뷰 H3 실측 무가드였다 | ✅ 자동 | `…::test_why_reports_the_industry_section` |
| 지문이 **소스에 반응**하고, 못 구하면 '지문불가' 라고 말한다(#291·#365) | ✅ 자동 | `…::test_banner_fingerprint_reacts_and_says_when_it_cannot` |
| 의존성 없는 인터프리터는 **갈래로** 말하고 원시 트레이스백으로 죽지 않는다(#82·#132) | ✅ 자동 | `test_why_names_the_dependency_branch_instead_of_dying` — 수집을 `ImportError` 로 태워 rc=1 과 처방 문구를 값으로 본다 |
| 냉각 되돌리기가 **본문 예외를 삼키지 않는다**(#291·#315) | ✅ 자동 | `test_cooldown_restore_does_not_swallow_the_body_exception` — `finally` 안의 `return` 이 위 갈래를 도달 불가로 만들었던 그 자리(배포전 셀프리뷰 실측) |
| 진단이 **새 학습 냉각을 심지 않는다** — 내용·mtime 둘 다(#264·#283) + `why()` 가 두르는지(#20) | ✅ 자동 | `…::test_why_does_not_plant_a_new_learn_cooldown` |
| 각주에 마크다운 볼드 금지 — 화면은 escape 한다(#298) | ✅ 자동 | `…::test_notes_carry_no_markdown_bold` |
| YoY 카드 4종은 **화면과 같은 창**(730일)으로 재어진다(#35·#176) | ✅ 자동 | `…::TestFredIndicatorSelector20260917::test_yoy_cards_take_the_yoy_path` · `…::test_audit_asks_the_same_selector` |
| `_fetch_all_fred` 배선은 결과로 본다(#20·#141) | ✅ 자동 | `…::test_fetch_all_fred_uses_the_shared_selector` |
| '관측 없음' 갈래 셋을 `observation_end` **와 요청 창의 시작일 대조**로 가른다(#82·#86·#260) | ✅ 자동 | `…::test_empty_observation_splits_source_gap_from_ours` · `…::test_audit_wires_empty_diag_into_the_buckets` |
| 행마다 **그 표면의 화면이 쓰는 선택기**(매크로=spot · 글로벌=YoY 디스패치)(#35·#45) | ✅ 자동 | `…::test_audit_asks_each_surface_with_its_own_selector`(값으로 — `audit_rows`) |
| 백필이 **소스별 계수 + 0건 소스 이름**을 찍는다(#82·#54·#290) · 0건 줄은 **두 갈래를 대칭으로**(#165) | ✅ 자동 | `trade/tests/test_badonion_sources.py::TestRelevanceBreakdown20260917`(4건) |
| `matching_keys` 는 **받는 소스 전부**를 돌려준다 — 합성 소스 둘로 발화(#45·#91c) | ✅ 자동 | `…::test_matching_keys_returns_every_source_that_takes_it` |

⚠️ 못 보는 축(#274): (a) `hl_key_candidates` 는 **이름에 high/low 가 든 키**만 본다 —
원천이 `dayRange` 처럼 다른 이름으로 주면 '원천이 안 준다' 로 찍힌다(그 판정을
`--why` 가 사람에게 보여주는 이유다). (b) 업종 백필의 **실제 커버리지**(몇 종목에
붙나)는 샌드박스에서 못 잰다 — 네이버가 막힌다. `--why ④` 가 VM 에서 답한다.
(c) `empty_diag` 의 `src_lag` 갈래가 **계열 중단인지 그냥 공표 지연인지**는 안
가른다 — 둘 다 "우리가 고칠 게 없다" 까지만 참이고, 카탈로그를 바꾸는 것은
그 사실을 재고 나서 할 일이다(#165). (d) 백필 계수는 `--show-irrelevant` 의
드랍 목록과 **다른 모집단**이다(관련 유닛만 센다). (e) 업종은 **전 행이 빌
때만** 사유를 적는다 — 부분 커버리지는 빈칸이 조용하고, `kr_industry_map()` 은
TTL 이 지나도 옛 맵을 서빙한다(형제 보드와 공유하는 선재 결함, #43·#163).
(f) `fetch_series_meta` 는 캐시가 없고 타임아웃 10초라, FRED 전면 장애면
감사에 최대 `관측 없음 행 수 × 10초` 가 붙는다(#116 — 오늘 그 행은 0건).

## 다음 우선순위 (갭 메우기 후보)
1. `_hard_guard_warn` — 감자/분할 키워드 존재 시 실제로 경고 텍스트가 삽입되는지 직접 단위테스트.
2. `_has_pm_override_trigger`/`_check_pm_override_required` — RSI 경계값(74.9/75.0/25.0/25.1), catalyst
   D-day 경계, data-availability 케이스별 순수 유닛테스트.
3. RULE 1-15 는 근본적으로 LLM 출력 검증이라 pytest 화가 부적합 — 대신 "프롬프트 텍스트가 여전히
   소스에 있는지"(orphan 참조 방지, §Pre-commit 4) 만이라도 grep 회귀로 고정하는 게 현실적 다음 단계.
4. `_extract_stance` Pass 0-3 폴백(정규식/키워드) — sentinel 도입(2026-07-26) 이후에도 구버전
   아카이브·sentinel 미준수 케이스의 안전망이라 여전히 살아있는 코드. 코드 주석에 인용된 실제
   회귀 티커(009450.KS/9988.HK/2382.TW/ALAB/300750.SZ)별로 최소 1개씩 순수 유닛테스트 고정 권장.

## NXT/KRX 겹침 문구 + 전용 창 하한 (2026-09-17, #375)

사용자 "이 NXT 랑 KRX 애프터랑 안겹치는것도 많을텐데. NXT 에 등록안된 기업들도
많기 때문에" — 화면이 **우리 구현의 결과**("겹치는 창엔 같은 값")를 시장 사실처럼
적고 있었고, 미측정 축이 하나 더 있었다(NXT 거래 종목 목록 미수신).

⚠️ 2026-09-17 에 **보드를 합치면서 이 계약이 한 번 더 바뀌었다**(#378·#222) —
"두 보드가 같은 목록을 낸다" 는 문장 자체가 사라졌으므로 남는 보장은 **거래소를
주장하지 않는다**는 것이다. 창은 `kr_session` 합집합 단일 출처에서 온다.

| 계약 | 강제 | 테스트 |
|---|---|---|
| 겹침은 **우리 한계**로 적고 "엔 같은 값"(시장 단정)은 금지 | ✅ 자동 | `test_overlap_is_stated_as_our_limit_not_a_market_fact` |
| 거래소 무필터 · 귀속 미측정 · 창은 단일 출처 | ✅ 자동 | 같은 테스트 |
| 체결 귀속은 여전히 미측정이라고 말한다 | ✅ 자동 | `test_screen_states_the_measured_half_and_claims_no_venue`(#373 계약 유지) |
| 각주가 렌더 페이지에 실린다(배선, #20) | ✅ 자동 | `test_the_note_reaches_the_rendered_page` |
| 전용 창 판정 3갈래(exclusive/overlap/closed) | ✅ 자동 | `test_exclusive_venue_splits_the_three_branches` |
| 그 판정이 거래소를 **열거하지 않는다**(#24) | ✅ 자동 | `test_exclusive_venue_is_derived_not_enumerated` — 합성 거래소로 발화(#91c) |
| 프로브 ⑤ 판정 4갈래가 서로 다른 문장 | ✅ 자동 | `test_probe_nxt_lower_bound_names_every_branch` |
| ⑤ 가 `main` 에서 **불린다** + 판정을 찍는다 | ✅ 자동 | `test_probe_main_runs_the_nxt_universe_section`(AST) |

**이 검사들이 못 보는 축**(#274):
- (a) **NXT 거래 종목 목록 자체는 아무도 안 잰다** — 원천을 아직 안 쟀으므로
  보드를 거래소로 거르지 못한다. 화면이 그 사실을 말하는 것까지가 이 라운드다.
- (b) 전용 창 하한은 **원천이 밝힌 창**(네이버 공지 153) 위에 선 연역이다 —
  원천이 창을 또 바꾸면 `kr_session` 표와 함께 이 판정도 틀린다(#165).
- (c) ⑤ 의 실제 호출 경로(네트워크)는 값으로 못 태운다 — AST 로 호출만 센다.
  인자를 넘기되 엉뚱한 값을 넘기는 변형은 이 검사 밖이다(#366 과 같은 축).
- (d) 표본이 3종목이라 "전용 창인데 체결 0" 은 NXT 미거래의 증거가 아니다
  (그래서 ❌ 가 아니라 ❓ 로 찍는다, #54). ⚠️ 그리고 이 프로브 한 방은 **그 창에
  사람이 맞춰 쳐야** 답한다 — 그래서 같은 날 자동 누적을 심었다(아래 절, #379).

## 전용 창 하한 **자동** 누적 (2026-09-17, #379)

사용자 "이것도 해줘". 위 절의 측정은 **그 창이 열려 있을 때만** 할 수 있는데
프로브는 사람이 15:40 에 맞춰 쳐야 답한다 — 잘못된 fix 다(§Automation-first·#252).
시간외 보드 스캔이 **이미 그 창에서** 2분 주기로 ~200종목을 돌고 있으므로
(`prepost_client._compute_kr_prepost`) 거기서 주워 담는다(`bot/venue_universe.py`,
추가 네트워크 콜 0). 워머가 180초마다 그 창에서 보드를 데우므로 사람이 안 들어와도
쌓인다.

| 계약 | 강제 | 테스트 |
|---|---|---|
| 전용 창을 **표에서 파생**한다(리터럴 금지, #38·#55) + 거래소 미열거(#24) | ✅ 자동 | `test_exclusive_spans_are_derived_not_enumerated` — 합성 거래소 ZZZ 로 발화(#91c) |
| 전용 창이 없으면 그렇게 말한다(#43) | ✅ 자동 | `test_exclusive_label_says_every_span_and_says_none_when_there_is_none` |
| 겹치는 창·창 밖에서는 **세지도 저장하지도** 않는다 | ✅ 자동 | `test_nothing_is_counted_when_the_windows_overlap` · `…_outside_any_window` |
| 못 센 실행에 `total: 0` 을 적지 않는다(#54·#34) | ✅ 자동 | `test_total_is_absent_when_nothing_could_be_counted` |
| **낡은 블록 가드** — 체결 **시각**도 전용 창 안이어야 센다 | ✅ 자동 | `test_a_stale_print_is_not_attributed_to_this_window` |
| 시각을 못 읽으면 세지 않고 **못 센 수를 말한다**(#54·#82·#123) | ✅ 자동 | `test_a_print_with_no_timestamp_is_counted_as_unmeasured_not_as_nxt` |
| 가드 기준은 '오늘' 이 아니라 '전용 창' 이다 | ✅ 자동 | `test_a_print_from_todays_pre_window_still_counts_in_the_after_window` |
| 시각 파서가 두 형태를 읽고 **모르면 None**(추측 금지) | ✅ 자동 | `test_parse_ts_reads_the_shapes_we_might_get_and_gives_up_loudly` |
| 형식 가정을 반증할 **원문 표본**을 남긴다(#109·#165) | ✅ 자동 | `test_the_raw_timestamp_sample_is_kept_so_the_assumption_can_be_refuted` |
| 스캔·날짜를 누적한다 | ✅ 자동 | `test_the_store_accumulates_across_scans_and_days` |
| 한 스캔의 중복이 **안 센 계수를 부풀리지 않는다** | ✅ 자동 | `test_a_ticker_seen_twice_in_one_scan_counts_once` |
| 상한으로 버렸으면 **말한다**(#45) | ✅ 자동 | `test_the_cap_is_spoken_not_silent` |
| 언제나 **하한**이라고 적는다(목록 주장 금지, #165) | ✅ 자동 | `test_it_always_says_it_is_a_lower_bound` |
| 빈 상태가 사유와 창을 말한다 | ✅ 자동 | `test_the_empty_state_says_why_and_where_from_the_window_table` |
| 스캔이 관측에 **체결시각까지** 넘긴다(#20·#366) + payload 계약 불변 | ✅ 자동 | `test_the_scan_hands_the_observation_the_execution_timestamp` |
| 스캔이 저장소를 **실제로 채운다**(스파이가 아니라 결과, #313) | ✅ 자동 | `test_the_scan_actually_fills_the_store_in_an_exclusive_window` |
| 관측이 던져도 **보드는 나간다**(곁들이, #315) | ✅ 자동 | `test_the_observation_never_breaks_the_board` |
| 프로브 ⑤ 가 누적분을 **읽어 찍는다** | ✅ 자동 | `test_the_probe_prints_the_accumulated_lower_bound` |
| 누적 파일을 못 읽으면 **덮어쓰지 않는다**(기록 유실 방지, #331 의 쓰는 쪽) | ✅ 자동 | `test_a_broken_store_is_not_silently_overwritten` |
| **길이 0** 도 '빈 상태' 가 아니다(쓰다 만 것 — #280 truncate 창·크래시) | ✅ 자동 | `test_a_zero_length_store_is_not_treated_as_empty`(리뷰 B1 실측: 4종목→1종목 리셋) |
| '못 읽음' 을 '빈 상태' 로 접지 않는다(갈래는 이름으로, #82) | ✅ 자동 | `test_an_unreadable_store_is_never_reported_as_empty` |
| 쓰기는 **원자 교체**(tmp→`os.replace`) — reader 가 찢어진 파일을 안 본다 | ✅ 자동 | `test_the_write_replaces_the_file_instead_of_truncating_it`(열린 핸들이 옛 내용을 본다) |
| 가드는 시각뿐 아니라 **거래소**도 못박는다 | ✅ 자동 | `test_the_guard_also_pins_the_venue_not_just_the_time` — 합성 거래소로 발화(리뷰 H1·#91c) |
| 하나도 못 센 실행도 **스캔 사실과 원문 표본**을 남긴다(#54·#109) | ✅ 자동 | `test_the_unmeasured_run_keeps_the_sample_that_could_refute_it` — 0종목엔 ✅ 금지 |
| 관측일은 **체결 시각**에서 온다(스캔일 아님 — 평일 공휴일 구멍도 닫힌다) | ✅ 자동 | `test_the_date_comes_from_the_execution_not_the_scan` |
| 관측일 상한은 **최신**을 남긴다 | ✅ 자동 | `test_the_dates_cap_keeps_the_newest_not_the_oldest`(리뷰 M3) |
| 이미 아는 티커는 `new` 가 아니다 | ✅ 자동 | `test_a_ticker_seen_twice_in_one_scan_counts_once` |
| 운영 로그가 **수를 싣는다**(유일한 가시 창, #20) | ✅ 자동 | `test_the_log_line_carries_the_numbers` — `caplog`(#373) |
| 프로브가 전용 창을 **표에서 읽는다**(리터럴 금지) | ✅ 자동 | `test_the_probe_reads_the_window_from_the_table_not_a_literal`(리뷰 M1) |
| 그 장애를 '아직 안 쌓임' 으로 위장하지 않는다(#54·#82) | ✅ 자동 | `test_a_broken_store_is_reported_not_shown_as_empty` |
| CLI 가 실행 안내(#278)와 같은 줄을 찍는다 | ✅ 자동 | `test_the_cli_prints_the_run_hint_and_the_lines` |

모두 `tests/test_regression.py::TestVenueExclusiveWindowLowerBound20260917`.
뮤테이션 16종(내가 돌린 것) + 독립 리뷰가 찾은 7종 + 리뷰 fix 를 되돌리는 6종이 전부 발화한다. 처음엔 눈먼 가드가 **아홉**이었다(내 둘 + 리뷰 일곱) — §CLAUDE.md #379.

**이 검사들이 못 보는 축**(#274):
- (a) `localTradedAt` 의 **실제 형식을 재지 않았다** — naive 면 KST 로 읽는다는
  가정 위에 선다. 첫 실측이 `ts_sample` 로 그 가정을 반증할 수 있게 해 뒀고,
  가정이 틀리면 `undated`/`outside` 수가 대신 커진다(#82 다음 출력이 곧 측정).
- (b) 창이 열려도 **스캔이 돌아야** 쌓인다 — 워머(180초)나 방문이 없거나
  `/naverpause` 중이면 그 창은 통째로 빈다. `scans` 는 (리뷰 H2 fix 로) 한 건도
  못 센 스캔에도 오르므로 '안 돌았다' 와 '돌았는데 0건' 이 구별된다.
- (b2) 쓰는 프로세스가 **둘**이다(봇 워머 · 대시보드 방문). 파일 락으로 lost
  update 를 막지만 `fcntl` 이 없는 플랫폼에선 락 없이 진행한다(로그로 밝힌다) —
  그때는 드물게 한 스캔의 계수가 유실될 수 있다(하한 자체는 다음 스캔이 줍는다).
- (c) 하한은 **귀속을 풀지 않는다** — '이 블록이 KRX 체결인가' 는 여전히 미측정
  이라 보드 각주는 그대로다(#375).
- (d) 유니버스가 '정규장 무버 ~200종목' 이라 전 상장이 아니다.

## 구워진 번역 캐시의 되읊기 접두 (2026-09-17, #381)

위 진단(#380)을 VM 에서 돌리자 캐시 값 자체가 오염돼 있었다 — `1709.TW | 호팍스`
(~20/60건) · `9. 레트로닉스`(번호 접두) · `3296.TWO | 승덕`. `clean_answer` 는
**쓰기 경로**에만 있었고 번호 접두는 패턴에 아예 없었다. 벗기기를 **읽는 경계**로
옮겨 구워진 값도 렌더타임에 따라오게 했다(#18·#270).

| 계약 | 강제 | 테스트 |
|---|---|---|
| 번호 접두(`9. `·`2) `)를 벗긴다 | ✅ 자동 | `test_clean_answer_strips_a_numbered_prefix` |
| 번호 뒤 **공백을 요구**한다(`3.5인치` 를 안 자른다, #146) | ✅ 자동 | `test_a_number_without_a_space_is_left_alone` |
| 되읊기가 겹쳐도(`9. 1709.TW \| 호팍스`) 벗긴다 | ✅ 자동 | `test_clean_answer_strips_a_numbered_prefix` |
| **구워진 캐시**를 읽을 때 벗긴다(제목 관문) | ✅ 자동 | `test_the_baked_cache_is_cleaned_on_read` |
| 티커 관문도 같은 규율(#38 — 안 하면 화면마다 이름이 다르다) | ✅ 자동 | `test_the_ticker_gate_is_cleaned_too` |
| 벗겨서 비거나 번호 찌꺼기·원문 그대로면 **없는 것으로**(#54·#171) | ✅ 자동 | `test_a_value_that_is_only_an_echo_is_treated_as_missing` |
| 프로브가 **어느 캐시**가 풀었는지 적는다(#82) | ✅ 자동 | `test_the_probe_says_which_cache_resolved_it` |
| 버린 구운 값을 **다시 한 번 묻는다**(안 그러면 영구 빈칸 #171) | ✅ 자동 | `test_a_dropped_baked_value_is_asked_again_exactly_once`(리뷰 Blocking) |
| 또 junk 면 miss 로 남아 **두 번째부터는 안 묻는다**(수렴 #348) | ✅ 자동 | 같은 테스트 |
| 더 **행동 가능한** 사유가 남는다(폴백이 안 덮는다, #275·#109) | ✅ 자동 | 같은 테스트 |
| **쓰기 관문 = 읽기 관문**(갈리면 영구 stuck, #38) | ✅ 자동 | `test_the_write_gate_matches_the_read_gate` |
| names 관문도 같은 수렴(형제 비대칭 금지 #38) | ✅ 자동 | `test_the_names_gate_converges_the_same_way` |
| 버린 값은 **키 자체가 없다**(`{tk: ""}` 는 '해소됨'으로 읽힌다, #136) | ✅ 자동 | `test_the_ticker_gate_is_cleaned_too` |
| `clean_answer` 는 **멱등**(렌더가 한 번 더 적용한다, 리뷰 H2) | ✅ 자동 | `test_clean_answer_is_idempotent` |
| 번호는 **배치 줄 번호 범위**일 때만 벗긴다(연도·큰 수 보호) | ✅ 자동 | `test_the_number_must_look_like_a_batch_index`(리뷰 M3·M4) |
| 숫자로 시작하는 **실제 상호**는 안 자른다(#146) | ✅ 자동 | `test_real_company_names_survive` |
| ⑥ 이 **값이 값다운가**를 잰다(새 모양의 되읊기, #119) | ✅ 자동 | `test_the_verdict_flags_a_value_that_is_not_a_name`(리뷰 M2) |
| 관심종목의 **영속된 되읊기**도 재해석한다(#38·#18) | ✅ 자동 | `test_a_persisted_echo_in_favorites_is_reinterpreted`(리뷰 M5) |

모두 `tests/test_regression.py::TestCachedTranslationEchoPrefix20260917`. 뮤테이션
7종 + 리뷰 fix 되돌리기 16종 전부 발화(넷은 처음에 눈멀어 다시 썼다 — 형제 관문
미측정 둘 · 부분문자열이 대신 만족 둘).

**이 검사들이 못 보는 축**(#274):
- (a) **번역이 맞는 이름인지**는 안 잰다 — `6870.TW 騰雲 → 레트로닉스` 가 맞는
  회사명인지 확인할 원천이 없다(미측정, #165).
- (b) 정화는 **읽을 때** 한다 — 디스크 파일은 다음 쓰기가 덮을 때까지 오염된
  채다. 이제 그 쓰기는 온다(버린 값이 `todo` 에 들어가므로).
- (c) 되읊기의 **다른 모양**(따옴표·괄호 접두 등)은 안 막는다 — 대신 ⑥ 의 값
  온전성 축이 **보이게** 한다(막는 것과 보이는 것은 다르다).
- (d) 이 캐시는 회사명뿐 아니라 **공시 제목**도 담는다(`chart_events`·
  `market_overview`). 번호 벗기기는 `_MAX_BATCH` 이하만 하지만, 제목이 정말
  `N. ` 로 시작하면(N ≤ 40) 그 번호는 잘린다 — 관측된 적은 없다.
- (e) 값을 **복사해 영속**하는 표면은 읽는 경계를 안 탄다 — 관심종목은 재해석
  게이트를 넓혔지만, 볼린저 `rows_<M>.json` 의 옛 세션 행은 창(5세션)이 밀릴
  때까지 옛 값이다(#325).
- (f) `translate_names_en`/`translate_industries_en` 은 같은 프롬프트 모양인데
  정화가 **없다** — 오늘은 호출부가 0건이라 피해가 없다(§작업 원칙 죽은 경로).

## 대만 한글명 — "왜 아직 한자인가" 를 종목별로 (2026-09-17, #380)

사용자 "최대한 한글화 한것 맞지? 5번은 넘게 이거 돌리는듯하네". 거부된 번역은
`translate_miss.json` 에 **프롬프트 지문**과 함께 남아 같은 프롬프트로는 다시 묻지
않는데(#348 비용 유계), 진단은 갈래와 무관하게 "3시간 빌드의 LLM 번역이 채운다" 고
말해 왔다(#55) — 그래서 기다려도 안 바뀌고 같은 화면을 다시 돌리게 된다.

| 계약 | 강제 | 테스트 |
|---|---|---|
| `miss_diag` 가 **이 프롬프트로 재시도하나**를 말한다 | ✅ 자동 | `test_miss_diag_tells_whether_this_prompt_would_retry` |
| 기록이 없는 것을 지어내지 않는다 · 진단이 기록을 **안 건드린다**(#264) | ✅ 자동 | 같은 테스트 |
| 지문은 **인자로 준 프롬프트**에서 뜬다(#38) | ✅ 자동 | `test_miss_diag_reads_the_prompt_it_is_asked_about` |
| 갈래를 이름으로(거부/재시도/미시도/영문/이름미확인/세 캐시) | ✅ 자동 | `test_every_branch_gets_its_own_sentence` |
| **관문이 둘**이다 — 이름 키(titles)·티커 키(names), 지문도 다르다 | ✅ 자동 | `test_the_two_gates_have_different_keys_and_different_prompts`(리뷰 B1) |
| 다른 관문에 막힌 종목을 '아직 안 물었다' 로 말하지 않는다 | ✅ 자동 | `test_a_name_blocked_by_the_other_gate_is_not_called_never_asked` |
| 한 관문이라도 다시 물으면 "다시 묻는다"(#82) | ✅ 자동 | `test_one_gate_still_retrying_means_it_can_still_change` |
| 화면처럼 **longName 을 먼저** 본다(캐시만 · yfinance 0) | ✅ 자동 | `test_the_longname_path_is_measured_like_the_screen` · `test_the_name_section_feeds_the_longname_cache_into_the_branches`(배선, #20) |
| 요약의 **수**를 집는다(소계를 빼면 거짓 ✅ 가 된다) | ✅ 자동 | `test_the_counts_are_not_just_labels`(리뷰 H3 R2) |
| 종목별 줄이 실제로 찍힌다(⑥ 의 존재 이유) | ✅ 자동 | `test_the_name_section_prints_a_line_per_ticker_and_the_counts`(리뷰 H3 R9) |
| 거부 사유에 **지문**을 적는다(#364) | ✅ 자동 | `test_a_name_blocked_by_the_other_gate_is_not_called_never_asked` |
| 몇 번 기다릴지 — 한 빌드 `_MAX_BATCH` 개 상한을 적는다 | ✅ 자동 | `test_the_verdict_separates_permanent_from_waitable`(리뷰 L4) |
| 총계·소계가 **같은 모집단**(#45) + 자른 사실을 말한다 | ✅ 자동 | `test_the_why_counts_every_stuck_name_not_just_the_first_eight` · `test_the_why_says_how_many_rejected_names_it_did_not_list`(리뷰 M2) |
| 볼린저 ⑦ 도 관문 둘을 본다 | ✅ 자동 | `test_the_why_reads_both_gates` |
| 어느 **캐시**가 풀었는지 구분(캐시가 둘이던 사고 #330) | ✅ 자동 | `test_the_ticker_cache_wins_over_the_title_cache` |
| 판정이 **영구**와 **기다리면 됨**을 가른다(#82·#260) | ✅ 자동 | `test_the_verdict_separates_permanent_from_waitable` |
| 한자가 남아 있으면 ✅ 를 찍지 않는다 | ✅ 자동 | `test_the_verdict_says_ok_only_when_no_han_is_left` |
| 대조 0행은 ✅ 가 아니다(#54) | ✅ 자동 | `test_zero_rows_is_not_a_pass` |
| `name_diag` 가 miss 기록을 **싣는다**(#123 계열) | ✅ 자동 | `test_name_diag_carries_the_miss_record` |
| `--why ⑦` 이 거부 건에 "다음 빌드가 채운다" 를 **안 적는다** | ✅ 자동 | `test_the_why_stops_promising_the_next_build_for_a_rejected_name` |
| 기록을 못 읽으면 **판정 불가**라고 말한다 | ✅ 자동 | `test_the_why_says_it_cannot_judge_when_the_record_is_unreadable` |
| 한자가 없으면 줄을 안 만든다(늘 뜨는 경고 금지 #25·#260) | ✅ 자동 | `test_no_stuck_name_means_no_line` |
| `_why` 배선 + 옛 무조건 문구 제거(주석 걷어내고, #59b) | ✅ 자동 | `test_the_why_section_is_wired_to_the_branch_helper` |
| 프로브 ⑥ 배선 + **모든** 번역 호출이 `cache_only=True`(#312·#321) | ✅ 자동 | `test_the_probe_section_is_wired` — 부분문자열로 재면 옆 호출이 만족시킨다(#75, 실측) |
| 인자 모드의 코드를 **영문 폴백으로 오분류하지 않는다**(#35·#292) | ✅ 자동 | `test_the_argument_mode_name_is_not_mistaken_for_an_english_fallback` — 배포전 셀프리뷰가 잡았다 |
| 진단이 **돈을 안 쓴다** + ⑥ 이 캐시를 실제로 조회한다(반대 증거 #25·#291) | ✅ 자동 | `TestTwEnrichProbe20260910::test_default_run_never_translates_never_slow_never_network` — 옛 계약(번역 함수 호출 0)을 #222 로 다시 썼다 |

모두 `tests/test_regression.py::TestTwKoreanNameStuckDiag20260917`(프로브 `main` 을 태우는 셋은 `TestTwEnrichProbe20260910`). 내가 돌린 뮤테이션 18종 + **독립 리뷰가 찾은 통과 뮤테이션 10종** + 리뷰 fix 되돌리기 18종이 지금은 전부 발화한다 — 커버리지 주장이 아니라 그때까지 재 본 목록이다(#286).

**이 검사들이 못 보는 축**(#274):
- (a) **모델이 왜 거부했는지**는 안 잰다 — 기록엔 답 24자만 남는다. 프롬프트를
  고치는 결정은 그 표본을 사람이 보고 한다.
- (b) longName 은 **영구 캐시에 있는 것만** 본다(`allow_slow=False`) — 캐시에
  없는 티커는 화면(백그라운드 enrich)이 나중에 채울 수 있고 ⑥ 은 그때까지
  한자로 센다. 과소가 아니라 **과대** 방향이므로 ❌ 가 없으면 화면도 깨끗하다.
- (c) 갈래는 **원천 native 명 + 캐시**로 정한다 — 렌더가 또 다른 경로로 이름을
  바꾸면(오늘은 longName·native·티커 셋이 전부다) 이 표와 갈린다.
- (d) `miss_diag` 는 LLM·네트워크를 안 쓰고 기록을 **의미상** 바꾸지 않지만,
  파일이 깨진 UTF-8 이면 공용 캐시 독자가 정리본을 쓴다(#331·#284, 리뷰 M1).
- (e) `latin` 갈래(원천 이름이 애초에 라틴)는 TWSE/TPEx 가 中文만 주므로 **무인자
  경로에선 발화한 적이 없다** — 인자 모드·다른 원천에 대비한 갈래다(#291 미측정).

## 대만 종목명 한글화 — 번역 판정·실패 기록 (2026-09-17, #376)

사용자 "대만 급등급락이랑 신고가에 한글화 안된것들 처리해줘" — `百達-KY`·
`昶瑞機電`·`三商電` 이 한자 그대로였고, 19행은 `3296.TW` 인데 이름줄이
`3296.TWO | 승덕` 이었다. 원인은 넷 다 "있으면 됐다" 판정(#25).

| 계약 | 강제 | 테스트 |
|---|---|---|
| 되읊은 `티커 \| ` 접두를 벗긴다 | ✅ 자동 | `test_clean_answer_strips_an_echoed_ticker` |
| 한자 그대로인 답은 번역이 아니다 | ✅ 자동 | `test_han_answer_is_not_accepted_as_a_translation` |
| 멀쩡한 답은 그대로 캐시(반대 증거, #25) | ✅ 자동 | `test_translator_caches_a_real_translation` |
| 거부한 항목을 다시 묻지 않는다 + 프롬프트 바뀌면 재시도 | ✅ 자동 | `test_translator_records_the_miss_so_it_stops_repaying` |
| **응답에 없던 줄**도 기록한다 | ✅ 자동 | `test_a_line_the_model_never_answered_is_also_recorded` — 뮤테이션 M3 가 이 테스트 전엔 통과했다(#291) |
| 백필이 번역 실패 시 native 로 내려간다 | ✅ 자동 | `test_tw_backfill_falls_through_to_the_native_name`(수집기 E2E, #20) |
| 둘 다 실패면 한자보다 영문 | ✅ 자동 | `test_tw_backfill_prefers_english_over_han` |
| 렌더가 접미사 어긋난 되읊기를 벗긴다 | ✅ 자동 | `test_render_strips_a_suffix_drifted_echo` |
| 한자뿐이면 `name_kr` 을 비워 워밍이 걸리게 | ✅ 자동 | `test_enrich_leaves_untranslated_open_for_warming` |

**이 검사들이 못 보는 축**(#274):
- (a) **모델이 실제로 무엇을 돌려주는지는 안 쟀다** — 샌드박스는 LLM 을 못
  부른다. 프롬프트에 "통용 한글명이 없으면 공식 영문명" 퇴로를 열었지만 그게
  `百達-KY` 를 실제로 풀지는 **다음 VM 수집이 답한다**(#12·#79·#82).
- (b) **이미 굳은 캐시는 이 변경이 못 고친다**(#18) — `names_kr.json`·
  `chart_title_kr.json` 에 한자로 들어간 항목은 그대로다. 렌더의 되읊기 벗기기만
  소급 적용된다. 지우고 다시 받으려면 그 키를 손으로 빼야 한다.
- (c) 실패 기록은 **프롬프트 지문**에만 묶인다 — 모델·모델 버전이 바뀌어도
  지문은 그대로라 재시도가 없다.

## 산업트렌드 YoY 라벨 위치 (2026-09-17, #377)

사용자 "숫자가 그래프에 가지잖아. 이런거 위치를 조정해서 보여지게 해줘.
되는게 있게 안되는것도 있고 그러네" — `_yoy_bar_svg` 가 최신값 라벨 baseline 을
`max(y(v) - 4, 9)` 리터럴로 잡았다. `y(v)` 는 음수 막대에선 **아래끝**이라
라벨이 막대 안에 박혔고, 양수라도 왼쪽 옆 막대가 더 높으면 가려졌다.
실측: 24개월 픽스처 6종 중 **5종이 겹쳤다**(옛 판).

| 계약 | 강제 | 테스트 |
|---|---|---|
| 라벨이 어떤 막대에도 겹치지 않는다(6종) | ✅ 자동 | `test_label_never_sits_on_a_bar` — M1(옛 리터럴)에서 5/6 발화 |
| 라벨이 도화지 안에 남는다(#100·#112 상한) | ✅ 자동 | `test_label_stays_inside_the_canvas` |
| X축 날짜 라벨과 안 겹친다 | ✅ 자동 | `test_label_does_not_collide_with_the_date_axis` |
| y 가 **막대 상자에서 파생**된다(부호·이웃 높이에 반응) | ✅ 자동 | `test_placement_is_derived_from_the_bar_boxes` — M1·M2 발화 |
| 점유된 자리가 있으면 물러난다 | ✅ 자동 | `test_bar_label_y_flips_when_a_text_box_already_sits_there` + `test_axis_label_avoidance_fires_from_the_real_chart`(호출부) |
| 상·하한이 도화지를 지킨다 | ✅ 자동(합성 기하) | `test_bar_label_y_clamps_inside_the_canvas` |
| `_est_w` 가 9px sans 실폭보다 **좁지 않다** | ✅ 자동 | `test_est_w_is_not_narrower_than_the_rendered_text` — 독립 대조표(#66 동어반복 회피) |
| 겹침 판정이 **반올림한 y**(SVG 에 실리는 값)로 돈다 | ✅ 자동 | `test_rounding_is_applied_before_the_overlap_check` — 60,000 차트를 쓸어 찾은 픽스처(3건) |

**이 검사들이 못 보는 축**(#274):
- (a) **상·하한만** 합성 기하로 태운다 — 20,000 차트를 쓸어 호출부에서
  0번 걸렸다. 축라벨 회피는 **도달한다**(같은 쓸기에서 1건, 그게
  `test_axis_label_avoidance_fires_from_the_real_chart` 픽스처다). 처음엔
  둘 다 '도달 불가' 로 적었는데 재 보니 아니었다 — **"도달 불가"는 재고
  나서 쓸 것**(#291·#165). 같은 쓸기가 죽은 후보 `_PAD_T - 3.0`(값이 clamp
  하한과 같은 9.0)과 죽은 폴백 `or [own]` 도 0/20,000 으로 드러내 지웠다.
- (b) **실제 렌더 폭**은 안 잰다 — `_est_w` 는 근사이고 샌드박스는 브라우저가
  없다(#14). 다만 옛 판의 문자당 3.4 는 9px sans 실폭(숫자 5.0 · `%` 8.0)의
  1/1.5 라 가드가 눈이 멀어 있었다(파생 배치를 넣고도 3,000 차트 **78.3%**
  겹침 → 실폭 표로 바꿔 **0%**). 대조표는 테스트가 따로 들고 있다.
- (c) **아카이브는 렌더된 HTML 을 동결**하므로(`industry_archive`) 과거 월
  스냅샷은 옛 위치 그대로다 — 새 스냅샷부터 적용된다(#18).
- (d) 형제 선차트(`_monthly_chart`·`_ttm_chart`·`_ttm_yoy_chart`)의
  **콜아웃↔폴리라인** 겹침은 이번에 고치지 않았다(실측 83.2% · 39.1% · 0%).
  `_place_labels` 후보가 서로와 축라벨만 피하고 선은 안 본다. 미루는 근거는
  CSS 기제다 — `.ind-cl`·`.ind-cl-ma` 의 `paint-order:stroke · stroke-width:3px`
  헤일로(좌우 1.5px)가 2.4px/2px 선을 덮는데, 막대는 **면 채움**이라 같은
  헤일로가 아무 일도 못 했다. ⚠️ 그 기제는 **CSS 를 읽어 세운 것이고 렌더로
  재지 않았다**(#165) — 선차트에서도 민원이 오면 미룰 근거가 없다.
  ✅ 반면 `_est_w` 수정은 형제에도 그대로 듣는다: `_monthly_chart` 의
  **콜아웃↔축라벨 겹침 7.0%(633/9000) → 0%**(옛 폭으로 렌더한 것과 대조).

## NXT 거래소 축 파라미터 후보 (2026-09-17, #377 같은 커밋)

사용자 결정 대기였던 "NXT 종목만으로 거르려면 목록 원천이 필요" 를 **재는**
자리다(`kr_board_probe` ⑥). 이름을 지어내 배선하면 죽은 경로를 배포하므로
(#151·#345) zod 에 일부러 틀린 값을 넣어 원천이 스스로 말하게 한다(#64·#86).

| 계약 | 강제 | 테스트 |
|---|---|---|
| 후보마다 실호출하고 이름을 전부 찍는다(#156 자르지 않는다) | ✅ 자동 | `test_probe_venue_param_section_measures_instead_of_guessing` |
| `main` 이 실제로 부른다 | ✅ 자동(AST) | `test_probe_venue_param_section_is_wired_into_main` — M5 발화 |
| 한 건도 못 재면 ❌(‘후보에 없다’ 로 단정 금지) | ✅ 자동 | `test_probe_venue_param_section_says_it_measured_nothing` — M7 이 이 테스트 전엔 통과했다(#291) |
| 같은 상태 공유 보정이 **한 함수**(형제 복제 금지, #38) | ✅ 자동 | `test_shared_status_demotion_is_one_function_not_two` — M6 발화 |
| 원천이 밝힌 허용값을 그대로 찍는다 | ✅ 자동 | `test_probe_venue_param_section_prints_what_the_source_declared` |
| 대조군이 죽으면 '판정 불가' 라고 먼저 말한다(#143) | ✅ 자동 | `test_probe_venue_param_section_flags_a_dead_control_group` |
| 같은 상태가 여럿이면 '환경 의심' 으로 내린다 | ✅ 자동 | `test_probe_venue_param_section_demotes_a_shared_status` |
| 200·0행을 '도달 실패' 로 찍지 않는다 | ✅ 자동 | `test_probe_venue_param_section_reads_200_with_zero_rows` |
| 하나도 없으면 물은 수·잰 수를 둘 다 적는다(#45) | ✅ 자동 | `test_probe_venue_param_section_says_none_in_schema` |
| 읽기 전용 — 캐시·냉각을 안 건드린다 | ✅ 자동 | `test_probe_venue_param_section_writes_nothing` |
| 판정 불가가 연속 3개면 **멈추고 건너뛴 것을 말한다**(#279·#346·#354) | ✅ 자동 | `test_sweep_stops_after_a_run_of_unjudgeable_failures` · `test_both_param_sweeps_stop_and_say_what_they_skipped`(형제 둘 다) · `test_sweep_says_nothing_when_it_asked_every_candidate`(늘 뜨는 경고 금지, #25·#260) |

**못 보는 축**(#274): 후보 이름 9종은 **우리가 적은 것**이라 원천이 쓰는
이름이 그 밖일 수 있다(#24) — ④ 의 전 키 덤프가 짝이다. 그리고 '있음' 이
떠도 그 키로 **목록이 실제로 줄어드는지**는 그다음 측정이 답한다.

**같은 커밋에서 잡은 시한폭탄**(2026-09-16 당시): 그때의
`test_scan_writes_to_its_own_venue_files` 가 실제 `now` 로 창을 판정해 **매일
16:00~20:00 KST 에만** 빨간불이었다 — KRX 체결 창은 NXT 창 안에 통째로
들어가므로 그 시간엔 공유 저장(리뷰 B1)이 정상 동작하고, 단언 "NXT 캐시를
덮어썼다" 가 **운영 상시 경로에서 거짓**이 된다. 창 판정을 시계에서 떼어내고
계약을 둘로 나눴다(당시의 `test_overlapping_windows_share_one_scan_into_two_files`,
M8 발화 확인).
⚠️ 두 테스트 다 **2026-09-17 보드 합침(#378)으로 사라졌다** — 남는 보장은
§KR 시간외 보드 절의 `test_scan_writes_one_cache_and_one_status` ·
`test_one_scan_path_means_no_double_polling` 이고, 창 판정을 시계에서
떼어내는 규율은 `_kr_scan_env` 가 그대로 든다(#222 지운 것이 아니라 다시 쓴 것).
⚠️ `docs/tests.md` 가 **인용한 테스트 이름이 실재하는지 검사하는 가드는
없다**(#274 못 보는 축) — 그래서 이런 이름은 조용히 썩는다. 지울 땐 인용처를
같이 볼 것.

## 진단이 제품과 같은 캐시를 보는가 (2026-09-17 · 실수 #382)

`tw_enrich_probe` 가 업종 캐시를 `tw_industry_map`(옛 이름)으로 읽고 있었다 —
제품은 그때 `_TW_IND_CACHE_KEY = "tw_industry_map_v2"` 였다(지금은 `…_v3`
— 아래 §부분 캐시 절에서 봉투로 바뀌며 다시 올렸다). 프로브는 바로 위 줄에서
TTL 을 제품 상수로 import 하면서 **이름만 리터럴로 복제**해(#38) rename 을 못
따라갔고, ② 가 `항목 0종목` · ⑤ 가 거짓 ❌ `곧 채워진다` 를 7.7일째 찍었다.
같은 실행의 ③(cache-only)은 업종 59/60 을 채우고 있었다(화면은 멀쩡, #35·#53).

| 계약 | 상태 | 테스트 |
|---|---|---|
| `bot/scripts/**` 가 읽는 캐시 이름이 제품 집합 밖이면 실패(이름 열거 아님, #24) | ✅ 자동 | `test_no_script_reads_a_cache_name_the_product_never_writes` |
| 프로브 둘이 캐시 이름을 **스스로 만들지 않는다**(제품 판정 함수를 부른다, #176) | ✅ 자동 | `test_tw_probes_do_not_build_the_industry_cache_name_themselves` |
| ② 가 **제품이 쓰는 그 파일**을 재고, 옛 이름만 있으면 '없다' 다(값으로) | ✅ 자동 | `TestTwIndCacheProbeMeasuresTheProductFile20260917::test_it_reads_the_product_key_not_the_pre_v2_name` |
| 형제 프로브의 대만 로더가 **twse dir** 을 읽는다 + finviz dir 은 안 읽는다(#25) | ✅ 자동 | `TestIndustryKrProbeTwLoaderReadsTheTwseDir20260917` |
| 그 스캔이 실제로 드리프트를 잡는다 + 고치면 조용해진다(#291·#25) | ✅ 자동 | `test_the_scan_fires_on_a_renamed_product_key` |
| 수집 분기마다 발화 경로가 있다(CACHE_DIR BinOp · 평문 변수 · 튜플+루프) | ✅ 자동 | `test_the_scan_fires_on_each_way_scripts_name_a_cache` |
| `pkey = f"enrich_mcap_{market}.json"` 류 정상 이름을 오보하지 않는다(#25·#260) | ✅ 자동 | `test_variable_fstring_keys_are_not_reported_as_drift` |
| 제품이 캐시 키를 **모듈 상수**로 두면 그 이름은 드리프트가 아니다(#292) | ✅ 자동 | `test_a_module_constant_key_in_the_product_is_not_drift` |
| `map_n == 0` 을 상태별로 가른다(없음/판정불가/파손/dict아님/봉투아님/빈 맵, #82) | ✅ 자동 | `TestTwIndustryMapVerdictBranches20260917` 4건 |
| 파손·dict아님을 '빈 dict' 라 단정하지 않고 처방(파일 삭제)을 댄다(#331·#165) | ✅ 자동 | `test_corrupt_and_wrong_type_are_not_called_an_empty_dict` |
| 같은 실행(③)이 채웠으면 ❌ '캐시 파일이 없다' 로 적지 않는다(#55·#79) | ✅ 자동 | `test_same_run_refill_is_not_reported_as_a_missing_cache` |
| 재지 않은 '곧 채워진다' 를 어느 갈래에서도 약속하지 않는다(#380·#165) | ✅ 자동 | `test_verdict_never_promises_soon_without_measuring` |
| 판정에 `ind_cache_probe()` **그 결과**를 넘긴다(이름만 맞는 다른 값 금지, #91b) | ✅ 자동 | `test_probe_passes_the_cache_probe_result_into_the_verdict` |

**못 보는 축**(#274): 이 스캔은 **이름**만 본다 — 모듈 안의 `이름 = "…"` ·
`이름 = f"…"` 대입과 `*.json` 문자열 상수(경로·URL 제외)를 위치 무관으로 모으지만,
런타임에 조립하는 이름(`"_".join(...)`·하위 dir `cache_dir / f"…"`)과 **맞는
파일을 열고도 엉뚱한 키로 조회**하는 경우는 밖이다. 대상은 `bot/` 뿐이다 —
`trade/scripts` 는 스크립트 전용 캐시(`resolve_check` 등)를 자기가 쓰고 자기가
읽어 여집합 규칙으로는 전부 오탐이 된다(실측 5건, #25·#260).

## 부분 캐시를 완전본으로 굽지 않는가 (2026-09-17 · 실수 #384)

#382 를 배포한 직후 같은 프로브 출력이 한 층 아래를 드러냈다 — 업종 캐시
**1084종목 = ④ 의 上市 개수와 정확히 같고** 上櫃 892 는 0. `fetch_tw_industry_map`
이 두 소스를 한 dict 로 합쳐 `if out:` 로 쓰는데, 한쪽이 실패한 부분 맵이
완전본과 **같은 파일·같은 24h TTL** 로 구워지고 무엇이 빠졌는지 아무 데도
안 남았다(#280·#45). 그 사이 上櫃 무버 30개는 이 맵이 애초에 없애려던 느린
yfinance 개별조회가 채우고 있었다.

| 계약 | 상태 | 테스트 |
|---|---|---|
| 한 소스가 실패하면 `partial` + 빠진 소스 **이름**을 남긴다(완전본으로 안 굽는다) | ✅ 자동 | `test_a_failed_source_is_not_baked_as_a_complete_map` |
| 쿨다운 안에선 아무것도 안 두드리고, 지나면 **실패한 소스만** 다시 시도(#61·#303) | ✅ 자동 | `test_only_the_failed_source_is_retried_and_only_after_the_cooldown` |
| 받은 적 있는 소스가 실패해도 그 행을 **버리지 않는다** + 로그로 말한다(#148·#12) | ✅ 자동 | `test_a_source_that_fails_later_keeps_its_previous_rows` |
| 만료 맵도 버리지 않는다(원천이 다 죽어도 화면이 빈칸이 되지 않는다, #148·#42a) | ✅ 자동 | `test_an_expired_map_is_served_even_when_the_refetch_fails` |
| 첫 **전멸**에서도 재시도 시각을 남긴다(빈 봉투는 `empty` 라는 이름, #54·#303) | ✅ 자동 | `test_the_first_total_failure_still_records_the_attempt` |
| `force=True` 는 TTL·쿨다운 둘 다 무시한다 | ✅ 자동 | `test_force_ignores_both_the_ttl_and_the_cooldown` |
| 동시 렌더 두 스레드가 서로 다른 소스만 받아도 **한쪽을 지우지 않는다**(#110·#344) | ✅ 자동 | `test_a_concurrent_writer_does_not_lose_the_other_source` |
| 그 재읽기는 **더 새 쪽**을 남긴다(무조건 남의 것 채택 금지, #91b) | ✅ 자동 | `test_the_reread_keeps_the_newer_rows_not_the_last_write` |
| 나이는 **소스별 `fetched`** 가 말한다 — 파일 mtime 은 재시도로도 바뀐다(#304) | ✅ 자동 | `test_expired_map_is_called_stale_but_not_thrown_away` |
| 봉투가 아닌 dict(옛 형식·손편집)를 '빈 맵' 으로 접지 않는다(#82) | ✅ 자동 | `test_states_are_measured_not_folded_into_empty` |
| ⑤ 가 '부분' 과 '낡음' 을 갈라 말한다(처방이 다르다, #82·#380) | ✅ 자동 | `test_main_prints_a_verdict_derived_from_what_it_measured` · `test_a_complete_but_old_map_is_called_old_not_partial` |
| ⑤ 가 **맵 밖을 무엇이 채웠는지** 댄다(같은 실행의 ③ 과 모순 금지, #382b·#43) | ✅ 자동 | `test_the_verdict_names_what_filled_beyond_the_map` |
| ② 가 소스별로 찍는다(한 줄로 합치면 '한쪽 없음' 이 '조금 낡음' 으로 보인다) | ✅ 자동 | `test_main_prints_a_verdict_derived_from_what_it_measured` |
| 캐시 저장 모양이 소스별 봉투다(병합 결과는 그대로) | ✅ 자동 | `test_fetch_tw_industry_map_merges_both_sources_and_caches` |
| 읽기(렌더) 경로는 **쿨다운 안에서만** 캐시를 다시 쓰지 않는다 | ✅ 자동 | `test_cached_numeric_codes_are_healed` |
| 재시도 간격이 리터럴로 못박혀 있고 연속 실패면 배로, 6h 에서 멎는다(#66·#116) | ✅ 자동 | `test_the_retry_interval_is_pinned_by_literals_not_by_itself` |
| 계속 실패하는 소스는 백오프에 걸리고, **성공하면 단이 지워진다**(#72) | ✅ 자동 | `test_a_source_that_keeps_failing_backs_off` · `test_a_failure_raises_the_backoff_step` |
| 남의 시도 시각을 채택하면 그 시도의 **실패 단**도 같이 온다(#45) | ✅ 자동 | `test_the_concurrent_tried_merge_carries_that_attempts_result` |
| 모르는 라벨은 봉투에서 걷어낸다(소스 목록이 바뀌면 옛 라벨이 영원히 남는다, #24) | ✅ 자동 | `test_unknown_labels_are_pruned_on_write` |
| 쓰다 만 파일을 '빈 상태' 로 읽지 않는다 + 우리 쓰기는 **원자 교체**(#379) | ✅ 자동 | `test_a_partially_written_file_is_not_read_as_empty` · `test_cache_write_uses_atomic_replace` |
| 나이는 **가장 낡은 소스**가 말한다(min 이면 만료가 숨는다, #91c) | ✅ 자동 | `test_expired_map_is_called_stale_but_not_thrown_away` |
| 부분이면서 만료면 **부분**이 이름이 된다(처방이 더 행동 가능하다, #275) | ✅ 자동 | `test_partial_beats_stale_when_both_are_true` |
| 손편집·찢어진 파일의 이상값이 정상 소스를 영구 '없음' 으로 만들지 않는다 | ✅ 자동 | `test_foreign_input_does_not_become_a_permanent_missing_source` |
| ⑤ 는 ② 가 아니라 **③ 뒤의 스냅샷**으로 판정한다(#114 루프의 잔여 상태) | ✅ 자동 | `test_probe_passes_the_cache_probe_result_into_the_verdict` |
| '언제 다시 시도하나' 는 약속이 아니라 **기록된 사실**로 적는다(#380·#165) | ✅ 자동 | `test_the_retry_note_states_recorded_facts_not_a_promise` |
| `not_envelope` 갈래가 옛 형식이라고 이름을 대고 파일 삭제를 처방한다(#291) | ✅ 자동 | `test_the_not_envelope_arm_names_the_old_format` |
| 제품이 내는 상태 갈래 전부가 위 회귀 목록에 등재돼 있다(#24) | ✅ 자동 | `test_every_state_the_product_assigns_is_covered_here` |
| conftest 리다이렉트는 **선언한 전 대상**이 실제로 걸린다(이름 변경 시 빨간불) | ✅ 자동 | `test_production_disk_caches_are_redirected` |

⚠️ 뮤테이션 14종 중 둘이 생존했다 — '실패한 소스의 행을 유지한다' 는 **암묵
동작**이라(그냥 `by[label]` 을 안 건드린다) 그 자리의 `elif` 는 로그뿐이었다.
`caplog` 로 재야 발화한다(#20·#373). 그리고 재읽기 병합의 **더 새 쪽** 비교는
두 스레드가 서로 다른 소스만 가진 픽스처에선 한 번도 안 태워진다 — 같은 소스를
옛 타임스탬프로 들고 있는 경쟁자를 만들어야 발화한다(#91c).

⚠️ 그리고 이 변경은 **쓰기를 늘린다**(실패도 재시도 시각을 굽는다) — 개발기에서
프로브를 한 번 돌리면 그 쿨다운이 회귀로 새어 `TestTwIndustryMapTpexEnglishKeys`
가 원천을 못 타고 조용히 빈 맵을 받았다(**전체 실행에서만** 빨간불, #30·#312·#344).
루트 `conftest.py` 리다이렉트 목록에 `bot.twse_client._CACHE_DIR` 을 더했고, 그
테스트 자신도 `tmp_path` 를 써 순서에 기대지 않는다.

⚠️ **독립 리뷰(2026-09-17)가 잡은 축**: (a) `_TW_IND_RETRY_SEC` 을 **아무도
못박지 않아** 테스트가 그 상수로 그 상수를 검증하고 있었다(#66) (b) `data_age`
의 `max` 와 `min` 이 구별되지 않았다 — 두 픽스처가 **같은 시각**을 썼다(#91c)
(c) 15분 고정 쿨다운은 원천이 오래 죽었을 때 렌더 경로의 바깥 요청을 하루
1회에서 **96회**로 늘린다(한 번에 최대 15초 블로킹, singleflight·백그라운드
워머 없음 — #116 예산 · #110 요청마다 스레드). 지수 백오프(15m→30m→1h→2h→4h,
상한 6h)로 **하루 8~9회**로 유계가 됐고, 두 상수는 리터럴로 못박혔다.

**못 보는 축**(#274): 이 계약들은 캐시 **경계**만 잰다 — 원천이 필드명을 바꿔
`_fetch_one_industry_source` 가 `{}` 를 주는 경우와 원천이 진짜로 비어 있는
경우는 여기서 안 갈린다(그건 ④ 가 원문 표본으로 말한다, #109). 그리고 15분
쿨다운이 **적절한 값인지**는 재지 않았다 — 원천 복구 시간을 측정한 적이
없다(#165). **동시 버스트**도 안 잰다: 백오프는 *한 스레드가 반복해서* 두드리는
것을 막지만, 콜드 캐시에 요청 N개가 동시에 들어오면 singleflight 가 없어 N번
나간다(#113) — 오늘은 첫 성공이 24h TTL 을 채우므로 그 창이 짧다. 그리고
`test_a_partially_written_file_is_not_read_as_empty` 는 쓰기가 **끝난 뒤**를
보므로 원자성 자체가 아니라 그 결과만 잰다 — 원자성은 AST 로 못박는다.

### 그 사실이 화면까지 가는가 (2026-09-17 · 사용자 "어 띄워주고")

리뷰 L5 의 공백을 닫았다 — `twse_client.industry_source_note()` **단일 출처**가
문장을 내고 대만 급등락·52주 **부제**가 그걸 싣는다(#38·#43).

| 계약 | 상태 | 테스트 |
|---|---|---|
| 완전본이면 **한 글자도 안 붙는다**(늘 뜨는 배지 금지, #25·#260) | ✅ 자동 | `test_complete_map_says_nothing` |
| 부분이면 **빠진 소스 이름**과 그 소스의 마지막 시도를 적는다(#43·#45) | ✅ 자동 | `test_partial_names_the_missing_source_and_the_last_attempt` |
| 쓸 수 없는 갈래(없음/손상/옛형식/빈맵)를 **이름으로** 가른다(#82) | ✅ 자동 | `test_unusable_map_names_the_branch_not_one_lumped_phrase` |
| 만료는 "갱신이 실패하고 있다" 로 말한다 | ✅ 자동 | `test_stale_says_the_refresh_is_failing` |
| **두 TW 페이지 모두** 싣고 형제 JP 페이지엔 안 붙는다(#359·#34) | ✅ 자동 | `test_both_tw_panels_carry_it_and_kr_does_not` |
| 상태 조회가 던져도 페이지는 산다(#315) | ✅ 자동 | `test_a_broken_state_read_does_not_kill_the_page` |
| 그 실패가 **로그로 남는다**(유일한 흔적 — 감사·프로브가 이 함수를 안 부른다) | ✅ 자동 | `test_a_broken_state_read_is_logged_not_just_swallowed` |
| 완전본이면 **부제가 한 글자도 안 얻는다**(빈 문자열이 아니라 화면 축) | ✅ 자동 | `test_a_complete_map_adds_nothing_to_the_subtitle` |
| 낡음은 **실패 기록이 있을 때만** 실패라 말한다(재지 않은 인과 금지) | ✅ 자동 | `test_stale_does_not_claim_a_failure_it_did_not_measure` |
| `data_age` 미기록 갈래가 문장을 얻는다(가드가 죽으면 경고가 사라진다) | ✅ 자동 | `test_stale_without_a_fetch_timestamp_says_so` |
| 다음 재시도까지의 **백오프**를 적는다(마지막 시도만 적으면 '곧 된다'로 읽힌다) | ✅ 자동 | `test_partial_states_the_backoff_not_just_the_last_attempt` |
| 시도 기록이 없는 소스도 **침묵하지 않는다** | ✅ 자동 | `test_a_missing_source_with_no_attempt_record_is_not_silent` |
| 제품이 내는 모든 '쓸 수 없음' 상태에 **자기 문구**가 있다(#24) | ✅ 자동 | `test_every_unusable_state_has_its_own_phrase` |
| 부제는 **본문을 만든 뒤** 조립된다 — 그 실행이 채운 캐시를 본다(#114) | ✅ 자동 | `test_the_note_is_built_after_the_body_not_before` |

⚠️ 회귀가 실제 결함 하나를 잡았다 — 첫 판의 `_tried_suffix` 가 **전 소스의 가장
최근 시도**를 적어, 上櫃 가 30분째 못 들어오는데 上市 기준으로 `0분 전` 이라는
거짓 안심을 냈다(#45 두 모집단). 빠진 소스를 말할 땐 **그 소스의** 기록을 적는다.

⚠️ 뮤테이션 13종 전부 발화(독립 리뷰가 첫 판에서 **6종 생존**을 실측했다 — 배선은 잡혔고 문구·계수 축이 뚫려 있었다). 그중 하나(`stale` 침묵)는 첫 시도가 `"" or (…)` 라
**아무것도 안 바꾼 no-op** 이었고 그 상태로 '생존' 처럼 보였다 — 통과·실패 어느
쪽이든 그 자리를 실제로 쳤는지 볼 것(#267·#384).

**못 보는 축**(#274): 이 문장은 **우리 맵에 대한 주장**까지다(#375) — "그 종목에
업종이 없다" 가 아니다. 맵 밖은 느린 yfinance 개별조회가 채우므로 실제로 몇 개가
비는지는 이 문장이 말하지 않는다(그건 `tw_enrich_probe ③` 이 센다). 그리고 부제는
`#live-sub` 라 표와 같이 갈아끼워지지만 **이 두 페이지는 `live_refresh.SLOW` 라
1시간 주기**이고 `isOpen()` 이 TW 를 평일 KST 10:00–14:40 으로 잡는다 — 장 밖에서는
사용자가 새로고침할 때까지 문장이 안 바뀐다(첫 판은 여기에 '30초 갱신' 이라고 적었다,
#55·#286). 그리고 `industry_cache_state()` 를 렌더당 1회 더 읽는다(웜 캐시 기준 순증
1회) — 값 단위 메모로 9.3ms → 1ms 대로 줄였지만 0 은 아니다(#116).
같은 화면의 `업종 분포` 줄(`highlow_render.ind_dist_line`)은 **여전히 모집단을 안
밝힌다** — 업종이 빈 행을 조용히 버리고 분모 없이 집계하므로 上櫃 가 빠진 날 그
분포는 上市 편향 표본이다(#45). 이 문장은 빈 칸의 사유만 말하고 그 집계는 손대지
않았다(독립 리뷰 2026-09-17 M7 — 미조치).

## ⑥ 한글명 계수는 갈래가 아니라 값으로 (2026-09-17 · 실수 #382)

VM 실측에서 무버 60종목이 전부 `티커 캐시가 풂 → …` 였는데 그중 11종목이
로마자(`KAI WEI TECHNOLOGY`·`Longzhong`·`U-CHEM` …)인데도 요약이 `한글 60 ·
영문 0` 이었다. 갈래는 '어느 관문이 풀었나'(= 어디를 고칠지)이고 계수는 '화면에
무엇이 보이나'라 한 축이 둘을 대신할 수 없다(#45·#34·#91b). #376 이 "통용
한글명이 없으면 영문" 퇴로를 연 직후라 그 규약이 얼마나 쓰이는지 아무도 못 봤다.

| 계약 | 상태 | 테스트 |
|---|---|---|
| 캐시가 풀어 준 로마자 값은 영문으로 센다 + 규약대로임을 말한다(#43) | ✅ 자동 | `test_latin_value_from_cache_counts_as_english_not_korean` |
| 캐시가 원문을 그대로 주면 '한자 잔존' 이고 ✅ 가 아니다(#54·#376b) | ✅ 자동 | `test_han_value_from_cache_is_counted_as_han_not_korean` |
| 혼합(한글+한자) 값은 **한자로** 세고 ✅ 를 막는다 + 처방이 프롬프트라고 말한다 | ✅ 자동 | `test_mixed_script_value_counts_as_han_and_blocks_the_pass` |
| 혼합은 한자의 부분집합 — 소계 합이 총계와 같다(#45) | ✅ 자동 | `test_mixed_count_is_a_subset_of_han_not_a_fourth_bucket` |
| 거부·대기·미시도 갈래는 value 가 정의상 한자다(전제 고정) | ✅ 자동 | `test_stuck_branches_always_carry_a_han_value` |
| ⚠️ 바로 아래에 ✅ 를 붙이지 않는다(#41) | ✅ 자동 | `test_pass_line_is_not_printed_under_a_warning` |
| 수상한 값 판정은 라벨이 아니라 **값**을 읽는다(#46·#19) | ✅ 자동 | `test_suspicious_values_read_the_value_not_the_label` |
| `name_rows_diag` 가 해소된 값을 싣는다(배선, #20) | ✅ 자동 | `test_rows_carry_the_resolved_value` |

⚠️ ✅ 를 막는 조건에 갈래(`stuck`)를 같이 걸었다가 **뮤테이션이 통과**해 지웠다
— 그 갈래들은 `not has_han(nm)` 검사를 먼저 지나므로 도달 불가였다(#291).
위 세 번째 줄이 그 **전제**(value 가 한자다)를 생산자로 태워 지킨다.
⚠️ 다만 전제는 거기까지다 — "그래서 계수만으로 ✅ 가 막힌다" 는 **한글을 먼저
세면 거짓**이었다(혼합 값이 '한글' 로 잡혀 `한자 잔존 0 · ✅`). 그래서 분류를
**한자 먼저**로 두고 그 순서 자체를 위 두 줄이 값으로 잰다(#91b).

**못 보는 축**(#274): 계수는 스크립트(한글/한자/그 밖)만 본다 — 값이 *맞는
회사명인지* 는 재지 않는다(그건 `suspicious_values` 가 모양으로만 거른다).

### DART 「투자유의안내」 미파싱 — 값 창·부분문자열 (2026-09-17, 실수 #385)
`tests/test_regression.py::TestDartInvestmentNoticeUnparsed20260917`

| 계약 | 상태 | 테스트 |
|---|---|---|
| generic 값 창이 긴 본문 행(`2. 내용`)을 **드랍하지 않는다** — 한 줄 길이는 종전대로 60자 | ✅ 자동 | `test_the_body_row_is_not_dropped_for_being_long` |
| 이미 구워진 1줄 카드에 **회수 경로**가 있다 + 줄이 줄면 교체 안 함(리뷰 H1) | ✅ 자동 | `test_a_baked_one_line_card_is_re_extracted` · `test_a_shorter_re_extraction_does_not_replace` |
| 시총 접두는 **단일 상수** — 미파싱 판정과 중복 방지가 같은 것을 본다(리뷰 M2) | ✅ 자동 | `test_the_mcap_prefixes_have_one_source` |
| 화면 경로 E2E — 배지·`df-mcap` 분류·시총 부착 셋을 렌더로(리뷰 H3) | ✅ 자동 | `TestDartCardFormats::test_dashboard_no_eq_prefix_and_mcap_attach` |
| 원문에서 뽑은 줄은 '시가총액' 을 품어도 **의미있는 파싱**이다 | ✅ 자동 | `test_a_parsed_line_mentioning_mcap_is_meaningful` |
| 우리가 붙인 보강 줄(`시가총액: … / 현재가: …`·`주요사업:`)은 여전히 빠진다(#155 생산부 모양) | ✅ 자동 | `test_the_enrichment_line_is_still_excluded` |
| 화면 경로 — detail 이 있으면 ⚠️미파싱 배지가 안 붙는다 | ✅ 자동 | `test_the_card_no_longer_shows_detail_and_the_badge_together` |
| 옛 부분문자열 리터럴이 두 파일에 재등장하지 않는다(#38 — **소스 검사**) | ✅ 자동 | `test_every_meaningful_filter_goes_through_the_one_predicate` |
| 옛 계약(보강 줄만 있으면 미파싱)은 지우지 않고 **다시 썼다**(#222) | ✅ 자동 | `TestDartFeedBackfill::test_meaningful_detail_gate` |

⚠️ 같은 부분문자열이 **세 곳**에서 같은 병이었다 — 미파싱 배지 · `df-mcap`
스타일(정당한 제목 줄이 리스트 뷰의 muted 시총 슬롯에 앉았다) · 시총 줄 중복
방지(그 카드에만 시총/현재가가 영영 안 붙는다). 가드는 두 파일에서 `"시가총액"
in`/`not in` 부분문자열 판정 자체를 금지한다(독스트링·주석은 걷어내고 본다, #59b).

**못 보는 축**(#274): 값 창 800·`max_lines` 8 이 *다른* 양식의 generic 추출을
어떻게 바꾸는지는 회귀 픽스처 범위에서만 잰다(실서버 전 양식 스윕은 없다).
독립 리뷰가 잰 **방향성**(레포 문자열 1,512건 코퍼스 — 생산 분포 아님): 창을
넓히면 generic 이 0건→≥1건이 되는 텍스트 64건(4.2%)이라 **'미파싱/파싱' 경계가
전 피드 규모로 이동**하고, 새로 생긴 줄 일부는 표 덤프의 오분할 라벨이다. 그리고
값이 **800자도 넘는** 행은 여전히 조용히 드랍된다(4000 으로 넓히면 +11행) —
드랍은 로그도 안 남긴다(#42a). `max_lines` 6→8 은 그 창이 밀어내던 꼬리 정보 행
(합성 스윕 46.1%)을 0% 로 되돌린 것이고, 대신 카드가 최대 2줄 길어진다.
그리고 티케이지애강 022220 「공개매수에관한의견표명서」(detail 0줄)는 이 fix 와
**다른 갈래**다 — 파서 갭인지 원문 미수신(`status=014`)인지는
`cd ~/stock && .venv/bin/python -m bot.dart_feed --why 티케이지애강` 이
답한다(#264·#265 — 조회 키는 **접수번호 또는 회사명**이지 종목코드가 아니다).

### 연계표 정합 가드 — 고아의 **출처**를 가른다 (2026-09-18, 실수 #386)
`trade/tests/test_reference_book.py::CatalogGuardTests`

| 계약 | 상태 | 테스트 |
|---|---|---|
| repo 큐레이션 CSV 키는 카탈로그에 전부 있다 — 운영 HOME 격리 후(#30·#294) | ✅ 자동 | `test_curation_keys_in_catalog` |
| `scan()` 이 고아를 repo/오버레이로 가르고 둘은 **배타·전수**(#45) — 수집기 E2E(#20) | ✅ 자동 | `test_scan_splits_orphans_by_source` |
| 큐레이션 CSV 를 못 읽으면 한쪽으로 분류하지 말고 **판정 불가**(#54·#291 발화 경로) | ✅ 자동 | `test_scan_marks_source_unknown_when_curation_csv_is_unreadable` |
| '반영' 적재분만 고아면 **연계표 rename 처방을 안 적는다**(#292 틀린 라벨) | ✅ 자동 | `test_message_overlay_orphans_do_not_blame_the_linkage_table` |
| 큐레이션 CSV 고아면 종전 처방(키 교정)만 적는다(#82 갈래마다 처방) | ✅ 자동 | `test_message_repo_orphans_ask_for_key_correction` |
| 출처 불명이면 머리줄에 **모르는 수를 안 적는다** | ✅ 자동 | `test_message_marks_unknown_source_instead_of_guessing` |
| 목록은 30개에서 자르고 **자른 수를 말한다** + 키를 escape 한다(규칙 7) | ✅ 자동 | `test_message_truncates_long_lists_and_says_how_many_it_cut` |

⚠️ 불릿 단언은 `_bullet()` 로 **그 한 줄만 잘라서** 잰다 — 메시지 전체에서 재면
머리줄(`reinforce 456품목(큐레이션 CSV 166 + …)`)·처방줄이 불릿 단언을 대신
만족시켜 뮤테이션이 통과한다(#55·#75, 독립 리뷰 실측 3종).

**못 보는 축**(#274): `_notify` 의 전송 실패 로그는 값으로 재지 않는다(curl 을
태우지 않는다). 그리고 출처 분류의 정확성은 **repo CSV 를 읽을 수 있을 때만**
참이다 — 못 읽으면 판정 불가로 빠지지, 오버레이 쪽으로 분류되지 않는다.

### 수주잔고 파서 — 첫 실물 라운드 (2026-09-18, 발췌가 실제로 일했다)
`tests/test_regression.py::TestBacklogRollingTable20260918`

발췌를 싣자 **첫 라운드에서 형태가 갈렸다** — 9건이 한 형태(4열 롤링)로 묶였고
나머지 7건은 원문을 더 봐야 한다(아래 ⚠️). 두 수의 합을 '전부' 라고 적지
않는다 — 보고서의 총계와 이 소계는 다른 모집단이다(#45). 픽스처는 VM 실측
발췌 그대로다(#155).

| 계약 | 상태 | 테스트 |
|---|---|---|
| 4열 롤링 표(`전기말·신규계약·매출인식·당반기말`)를 받는다 — 391710 실측 | ✅ 자동 | `test_rolling_four_column_table_is_parsed` |
| 항등식(기초+신규−인식=기말)이 **유일한 열 배정 가드**(#106) | ✅ 자동 | `test_a_rolling_row_that_fails_the_identity_is_refused` |
| 합계행이 있으면 그 행만 — 부문 행과 같이 더하면 두 배 | ✅ 자동 | `test_rolling_total_row_is_not_double_counted` |
| 합계행이 없으면 검산 통과 행의 기말을 **합한다** | ✅ 자동 | `test_rows_without_a_total_are_summed` |
| 4열 아닌 줄은 건너뛴다(안 그러면 파서가 통째로 죽고 조용히 미수집) | ✅ 자동 | `test_rows_that_are_not_four_columns_are_skipped` |
| 기말 ≤ 0 은 안 받는다 | ✅ 자동 | `test_a_non_positive_closing_balance_is_refused` |
| 인식 열이 헤더에 없으면 안 집는다(#57·#76) | ✅ 자동 | `test_a_table_without_an_opening_column_is_not_taken` |
| **시작 열**이 없으면 안 집는다 — 머리는 3열인데 행이 4값인 표(= 엉뚱한 표를 읽는 중) | ✅ 자동 | `TestBacklogReviewFollowups20260918::test_rolling_table_requires_an_opening_column` |
| 원천이 **빈 표**(전부 `-`)를 내면 파서 개선 여지가 아니다(#93·#111) | ✅ 자동 | `test_an_empty_backlog_table_is_not_a_parser_gap` |
| 값이 있는 표는 여전히 개선 여지다 — 반대 증거(#25·#146) | ✅ 자동 | `test_a_table_with_numbers_is_still_a_parser_gap` |
| 새 형식을 더해도 옛 형식이 안 깨진다(#59·#80 선택기 순서) | ✅ 자동 | `test_existing_formats_still_parse` |

⚠️ **표본은 갈래마다 1건이다.** 9건을 한 형태로 묶은 건 발췌 하나의 근거이고,
나머지 8건이 같은 형태인지는 다음 보고서가 말한다 — 표본 하나로 "갈래를 다
고쳤다" 고 말하지 않는다(#111·#165).

⚠️ 남은 7건은 **원문을 더 봐야 한다**: 000670(영풍) 4건은 `수주총액·기납품액·
수주잔고` 각각에 수량/금액 2단 헤더가 붙은 6열 표이고 잔고가 **음수**다(실측
245,858 − 264,255 = −18,396). 078340·144960 3건은 발췌에 `매출실적` 만
보여 원천 부재일 수 있다. 결정적인 `합 계` 행이 발췌 창 밖이라 `_EXCERPT_CAP`
을 600 으로 넓혔다(#156·#350 자르는 자리가 다음 결정을 가리면 안 된다).

### 수주잔고 격주 보고서 — **원문 발췌**를 싣는다 (2026-09-18)
`tests/test_regression.py::TestBacklogMissExcerpt20260918`

2026-09-18 보고서가 `형식미지원 18건` + 관문 히스토그램만 주고 "이 목록을
Claude 에게 그대로 붙여넣으면 파서를 확장합니다" 라고 적었다. 그런데 **같은
보고서**가 범위(#105)·관문(#107)·어휘(#109)·창(#275) 넷을 연달아 오진하게
만든 이유가 '원문이 없어서' 이고, #111 은 "낮은 커버리지를 보면 파서를 더
짜기 전에 **표본 원문부터** 볼 것" 으로 끝난다. `backlog_excerpt` 는 이미
미스 시점에 계산되는데 `_log_miss` 가 그걸 버려, 보고서를 받을 때마다
운영자가 `--ticker` 를 종목 수만큼 돌려야 했다(§Automation-first).

| 계약 | 상태 | 테스트 |
|---|---|---|
| `_log_miss` 가 발췌를 `ex` 로 남긴다 — 상한은 있되 헤더를 안 자른다(#156·#350) | ✅ 자동 | `test_log_miss_persists_the_excerpt` |
| 필드를 더해도 같은 미스가 **두 줄로 안 쌓인다** — 신원은 필드로(#45) | ✅ 자동 | `test_adding_the_excerpt_does_not_double_count_an_existing_miss` |
| 파서를 고친 뒤 재조회하면 **새 관측이 옛 발췌를 대체**하고, 사유가 다르면 따로 남는다(#18) | ✅ 자동 | `test_a_reparsed_miss_replaces_the_stale_excerpt` |
| `backlog_probe` 가 계산한 발췌를 **기록에 넘긴다**(#20 배선) | ✅ 자동 | `test_probe_hands_the_excerpt_to_the_log` |
| 보고서가 갈래마다 1건을 싣고 `<`/`>`/`&` 를 escape · 발췌 없는 갈래는 **블록이 없다** · 앞 200자만 싣고 그 사실을 말한다 | ✅ 자동 | `test_report_shows_one_excerpt_per_kind_and_escapes_it` |
| 상세 히스토그램 줄도 escape 한다 — 한 메시지에서 한쪽만 raw 면 `<` 하나에 보고서가 통째로 안 간다(규칙 7·#38) | ✅ 자동 | `test_detail_histogram_is_escaped_too` |
| 발췌 없는 줄은 **사유별로 세어** 말하고(원인 단정 금지, #82·#165) 0 이면 침묵(#25·#260) | ✅ 자동 | `test_report_says_how_many_rows_have_no_excerpt` |
| 예산은 **보낼 메시지 전체**(머리말·생략줄·꼬리말 포함)를 잰다 | ✅ 자동 | `test_budget_counts_the_whole_message_it_will_send` |
| 갈래 **개수로는 안 자른다** — 예산이 남으면 전부 싣는다(#45) | ✅ 자동 | `test_report_does_not_cap_kinds_when_the_budget_allows` |
| 예산이 자른 갈래 수를 사실대로 말한다(#45) | ✅ 자동 | `test_report_stays_within_the_telegram_limit` |
| 발췌 블록이 **기록 날짜**(KST 명시계산, 규칙 10a)를 적는다 — 옛 관측인지 구별(#43·#114) | ✅ 자동 | `test_excerpt_block_says_when_it_was_recorded` |
| 원장은 tmp+`os.replace` 로 갈아끼운다 — truncate 쓰기는 읽는 쪽에 찢긴 파일을 준다(#379·#384) | ✅ 자동 | `test_ledger_is_replaced_atomically` |
| 인쇄되는 명령은 **그대로 붙여넣어 돈다**(#278) + `&&` escape(규칙 7) | ✅ 자동 | `test_printed_commands_are_runnable_as_is` |
| 공개 선택기가 묘비를 안 세고, 길이는 UTF-16 으로 잰다 | ✅ 자동 | `test_public_selectors_skip_tombstones_and_count_utf16` |
| CLI 가 보고서와 **같은 선택기**로 같은 발췌를 **전부**, 큰 갈래부터 찍는다(#38) | ✅ 자동 | `test_cli_prints_the_same_excerpts_the_report_shows` |
| `--refill` 이 발췌 없는 줄만, **신원으로 중복 없이** 고른다(#61) | ✅ 자동 | `test_refill_targets_are_deduped_and_exclude_rows_that_have_one` |
| 되메워 **값이 나오면 그 줄을 지운다** — 안 지우면 '막힌 조회 N건' 이 영원히 부푼다(#45) | ✅ 자동 | `test_drop_miss_removes_only_that_row` · `test_refill_fills_excerpts_and_drops_resolved_rows` |
| 상한으로 자른 사실·키 없음(rc=1)을 말한다(#45·#54·#82) | ✅ 자동 | `test_refill_says_what_it_did_not_do` |
| `시계열이상` 사유는 **단일 출처**라 되메우기가 그 신호를 안 지운다(#38) | ✅ 자동 | `test_series_anomaly_reason_has_one_source` |
| 모든 경로가 **코드 지문**을 먼저 찍고, 모르는 플래그는 거절한다(rc=2) | ✅ 자동 | `test_cli_says_which_build_it_is_and_rejects_unknown_flags` |
| 지문이 **출력을 만드는 두 파일**을 덮고 소스가 바뀌면 값이 바뀐다(#364·#286) | ✅ 자동 | `test_build_fingerprint_covers_the_sources_that_make_the_output` |
| `make test` 가 **운영 미스 원장**을 안 건드린다 — conftest 리다이렉트(#30·#312·#344) | ✅ 자동 | `test_production_disk_caches_are_redirected` |
| 되메우기는 **원문을 읽었을 때만** 옛 줄을 지운다 — 조회 실패·원문 미수신은 보존 | ✅ 자동 | `test_refill_keeps_the_old_row_when_it_could_not_read_the_document` |
| 되메우기가 **캐시를 우회**한다(#35) — 캐시 히트면 새 줄이 안 써진다 | ✅ 자동 | `test_refill_bypasses_the_parse_cache` |
| 원문 없는 줄은 **뒤로 밀고 따로 센다**(상한 선점 금지, #171) | ✅ 자동 | `test_refill_puts_rows_without_a_document_last_and_counts_them_apart` |
| 상한은 **문구가 아니라 호출 수**로 지켜진다(#313·#66) | ✅ 자동 | `test_refill_cap_is_enforced_by_behaviour_not_just_the_wording` |
| `--refill <비정수·음수·0>` 을 갈래로 거절(#82·#132) | ✅ 자동 | `test_refill_rejects_a_bad_cap_and_says_why` |
| 아는 플래그인데 **인자가 모자라도** 거절 — 옛 판은 조용히 요약을 찍었다 | ✅ 자동 | `test_known_flags_with_missing_arguments_are_rejected` |
| 되메울 게 없으면 **왜 없는지**(옛 어휘) 말한다(#38·#82) | ✅ 자동 | `test_refill_says_when_only_legacy_rows_remain` |
| 발췌가 **하나도 없어도** 메시지가 한도 안이고 줄인 사실을 말한다 | ✅ 자동 | `test_report_fits_even_when_no_excerpt_exists` |

⚠️ 2026-09-18 독립 리뷰가 잡은 두 축이 여기 들어 있다. (a) 옛 판은 갈래를
`_EX_SAMPLE_N=6` 으로 **조용히** 잘랐다 — 실측 10갈래 → 6개 표시, 생략 문구
없음, 메시지는 1294/4096 으로 여유 만만이었다. 갈래가 6을 넘는 건 예외가
아니라 기본값이다(`_far_msg` 가 캡션 원문과 `{gap}자` 를 detail 에 박는다).
(b) 예산을 따로 계산해 두면 그 식의 피연산자(머리말)를 빼는 변형이 안 잡히고
실제로 **4,106 u16** 가 나갔다 — 이제 `trial` 이 보낼 메시지 전체를 재고,
테스트는 4096 이 아니라 **`_DM_LIMIT`** 으로 재며 잘린 뒤 남는 여유가 한
블록보다 작은지까지 본다(안 그러면 40~90 u16 과소평가가 슬랙에 흡수된다).

⚠️ `html.escape` 는 기본이 `quote=True` 라 `'` → `&#x27;`(**6배**)다. 최악
픽스처는 `<`(4배)가 아니라 따옴표다.

⚠️ CLI 계약은 **출력으로** 잰다 — 호출 여부(AST)로만 재면 `if False:` 로
게이트만 끄는 변형이 통과한다(#141, 실측 SURVIVED 1건 → 출력 단언으로 교체).
정렬 계약도 **크기 순서와 이름 순서가 어긋나는** 픽스처여야 발화한다(#91c).

⚠️ 뮤테이션이 문법을 깨면 전 테스트가 빨간불이라 '잡힘' 처럼 보인다 —
실측 1건(`sorted(...)` 앵커가 `):` 를 삼켰다). 통과·실패 어느 쪽이든 그
자리를 실제로 쳤는지 볼 것(#267).

⚠️ 2026-09-18 실측 — 배포 **전에** `--refill` 을 안내했더니 VM 의 옛
체크아웃이 그 플래그를 무시하고 `summarize()` 로 떨어져, 출력이 옛 판과 한
글자도 다르지 않았다(#371 "진단 도구를 심어 놓고 돌려 달라고 말하려면 그게
VM 에 도달하는 경로가 있는지부터 답할 것" 의 재발). 그래서 모든 경로가
**코드 지문**을 먼저 찍고 모르는 플래그는 rc=2 로 거절한다 — 옛 판에는 배너
자체가 없으므로 그 부재가 곧 신호다(#11·#364).

⚠️ `--refill` 은 이 스크립트의 **유일한 쓰기 경로**다(원장에 발췌를 되메운다)
— 모듈 독스트링의 "읽기 전용" 을 전체에 대한 주장으로 두면 거짓이 된다(#55·#286).
발췌 없는 줄은 그 종목·분기를 누군가 다시 열 때까지 영원히 근거가 없고 격주
보고서는 2주에 한 번이므로, 운영자가 한 번에 되메울 수 있어야 한다
(§Automation-first). 사유가 달라지면 옛 줄은 **방금 다시 재서 반증된 관측**
이므로 지운다.

⚠️⚠️ 2026-09-18 **2차 독립 리뷰가 배포를 막았다**(Blocking 1). 첫 판의
`--refill` 은 "사유가 달라졌으면 옛 줄을 지운다" 였는데, `backlog_probe` 는
자기 예외를 삼켜 `오류:…` 를 돌려주고(그 경로에선 새 줄이 **안** 써진다)
DART 일일한도는 예외 없이 빈 문서를 준다. 그래서 장애 한 번이 게이트 분류를
지웠다 — 실측 원장 3줄 → **0줄**, 화면은 그동안 "되메움 3건 · 이제 발췌를 볼
수 있다". 이제 프로브가 `doc_len` 으로 "이번에 읽었나" 를 싣고, 읽었을 때만
옛 줄이 물러난다. 갈래는 되메움 / 원문 여전히 없음 / 조회 실패 셋으로 나뉜다.

⚠️ 2026-09-18 배포전 스모크가 잡은 것 — `make test` 가 `backlog_probe` 를
태우면서 운영 원장(`~/.tradingagents/backlog_misses.jsonl`)에 **가짜 티커**를
남기고 있었다(`X`·`005930` 이 픽스처 본문 "수주잔고 없음" 과 함께). 그 원장이
곧 격주 DM 이라 운영자는 그걸 진짜 미스로 읽고 없는 버그를 쫓는다 — 발췌를
싣게 된 이번 변경이 그 오염을 '파서를 고칠 유일한 근거' 로 **승격**시켜 더
나빠졌다. 루트 `conftest.py` 의 import 시점 리다이렉트에 한 줄 더했다(함수
스코프 fixture 로는 daemon 스레드·`pytest bot/tests` 프로세스를 못 덮는다).

**못 보는 축**(#274): 발췌 **내용이 파서를 고치기에 충분한가**는 기계가
못 잰다 — 창(앞 120 + 뒤 240)이 헤더를 담는지는 실제 원문에서만 확인된다.
원자 교체 단언은 **구조**만 재지 동시 쓰기의 read-modify-write 유실(선재)은
재지 않는다. 그리고 발췌가 없는 갈래는 둘이다 — `형식미지원` 류는 발췌
도입 전에 쌓인 줄이라 그 종목·분기를 **다시 조회할 때**(24h 캐시 만료 후)
붙지만, `시계열이상`(`quarterly_infographic` 이 조립된 시계열만 보고 남긴다)은
**원문 없이** 기록되므로 영원히 없다. 그래서 문구가 원인을 단정하지 않고
사유를 이름으로 센다(#55·#82·#165).

### 거래량 상위·급등급락 — 허용값 키 귀속 · 사유 채널 (2026-09-18)
`tests/test_regression.py::TestSortKeyAttribution20260918` ·
`TestFieldErrorsKeepEveryMessage20260918` · `TestVolumeLearnFailBranches20260918` ·
`TestMoversPageStatesTheReason20260918`

| 계약 | 상태 | 테스트 |
|---|---|---|
| 남의 키(`dividendSortType`)에 붙은 목록을 `sortType` 으로 배우지 않는다(#34) | ✅ 자동 | `test_other_keys_list_is_not_learned_as_ours` |
| 옛 봉투(키가 구간 **안**)도 여전히 읽힌다 — 키 귀속이 테마 보드 경로를 죽였다(#73) | ✅ 자동 | `test_old_bracket_envelope_still_resolves_for_the_key` |
| 키 매칭은 **토큰 경계** — 접두 형제(`sort` ⊂ `sortType`)로 태운다(#46·#91b) | ✅ 자동 | `test_key_match_is_token_bounded_not_substring` |
| `fieldErrors[key]` 의 **모든** 사유를 남기고 열거를 앞세운다(#156·#350) | ✅ 자동 | `TestFieldErrorsKeepEveryMessage20260918` 5건 |
| 학습 실패 사유가 갈래 넷을 **이름으로** 말한다(#82) | ✅ 자동 | `TestVolumeLearnFailBranches20260918` 4건 |
| 배선 — 학습이 남의 키 값을 후보로 **불러 보지 않는다**(#20) | ✅ 자동 | `test_learner_does_not_send_another_keys_values` |
| 급등·급락 빈 화면이 일시정지·HTTP·구조변경을 **갈래로** 적는다(#43·#82) | ✅ 자동 | `TestMoversPageStatesTheReason20260918` 3건 |
| 사유는 HTML escape 를 거친다(규칙 7) | ✅ 자동 | `test_reason_is_escaped` |
| **부분 수신은 실패가 아니다** — 2쪽에서 끊겨도 사유를 안 싣는다(#25·#260) | ✅ 자동 | `test_partial_page_is_not_reported_as_failure` |
| 사유가 여럿이면 **가장 행동 가능한** 것을 싣는다(#275) | ✅ 자동 | `test_movers_reason_prefers_the_actionable_one` |
| 성공 실행엔 사유가 안 실린다(반대 증거, #25) | ✅ 자동 | `test_success_carries_no_reason` |
| 사유 **접두**(`… — 원천: `) 뒤의 첫 키도 구간 머리다 — 아니면 지목한 키를 '안 했다'고 말한다(#292) | ✅ 자동 | `test_the_first_key_head_survives_the_reason_prefix` |
| 배선 — 학습 실패 문구가 **리터럴 갈래**를 말한다(부재가 아니라, #82·#20) | ✅ 자동 | `test_volume_learner_names_the_literal_branch_not_absence` |

⚠️ 토큰 경계 가드는 **두 번 뮤테이션을 통과했다** — `dividendSortType` 은
대문자 `S` 라 부분문자열로 바꿔도 `sortType` 과 안 겹치고, 구간 **머리**로만
재는 픽스처는 본문 매칭 경로를 아예 안 탄다. 접두가 같은 형제(`sort`)와 키가
구간 안에 있는 옛 봉투, 둘 다 있어야 발화한다(#91c).

⚠️ 그리고 **손으로 적은 사유 픽스처 열둘이 전부 눈이 멀어 있었다** — 제품의
사유는 `http_reason` 이 앞에 `원천이 HTTP 400 — 원천: ` 을 붙여 만드는데,
구간 분해가 `·`·문두만 경계로 봐서 그 **첫 키**를 통째로 놓쳤다. 옛 봉투는
허용값이 0종이 되고(테마 커버리지 계약 5건이 빨간불로 잡았다) 새 봉투는
원천이 지목한 키를 "지목하지 않았습니다" 라고 말했다. 사유 픽스처는 **그
접두를 붙이는 함수를 태워** 만들 것(#155·#20).


#### 독립 리뷰 후속 (2026-09-18, 같은 날 2차)
`tests/test_regression.py::TestReasonChannelFollowups20260918` ·
`TestBacklogReviewFollowups20260918`

리뷰가 뮤테이션 38종 중 **13종 생존**을 실측했다 — 값은 흐르는데 그 값을 쓰는
자리(화면 문구·폴백 라벨·프로브 인자)를 아무도 안 잰 형태다(#20·#291).

| 계약 | 상태 | 테스트 |
|---|---|---|
| 사유가 **통째로** 잘리면 '우리가 잘랐다' 로 말한다 — 원천 탓 금지(#54·#165) | ✅ 자동 | `test_a_fully_truncated_reason_is_blamed_on_us_not_the_source` |
| 온전한 사유엔 '잘렸다' 를 안 붙인다(반대 증거, #25·#260) | ✅ 자동 | `test_an_intact_reason_is_not_called_truncated` |
| 같은 키가 두 번 오면 **이어 붙인다** — 목록이 실린 뒤엣것을 버리지 않는다(#45) | ✅ 자동 | `test_the_same_key_twice_keeps_the_segment_that_has_the_list` |
| `isSuccess=false` 는 원천이 적어 보낸 문구를 그대로 싣는다(#325) | ✅ 자동 | `test_isSuccess_false_carries_the_sources_own_wording` |
| 비-dict 응답은 **계약 변경**으로 이름 붙인다(#82) | ✅ 자동 | `test_a_non_dict_response_is_named_as_a_contract_change` |
| 행을 받았는데 전부 걸러지면 **우리 필터**를 지목한다 — 0건과 다른 갈래(#82·#292) | ✅ 자동 | `test_all_rows_filtered_out_points_at_our_filter_not_the_source` · `test_zero_rows_from_the_source_is_named_separately` |
| 저장분 폴백이 **왜** 낡았는지 같이 적는다(#43·#306) | ✅ 자동 | `test_stale_fallback_still_states_why_it_is_stale` |
| 렌더 경로 예외 문구도 마스킹을 거친다(§Secrets) | ✅ 자동 | `test_render_path_exception_is_masked` |
| 리터럴 갈래 문구가 **반대 증거**를 같이 적는다 — 같은 주소가 다른 값으로는 행을 준다(#165·#292) | ✅ 자동 | `test_the_literal_branch_states_the_counter_evidence` |
| 프로브가 **우리 키로** 허용값을 읽는다 — 남의 목록을 후보로 적지 않는다(#35·#352) | ✅ 자동 | `test_probe_reads_the_allowed_values_with_our_key` · `test_probe_venue_section_attributes_the_list_to_the_probed_key` |
| 각주 붙은 합계행(`합 계 (*) - 10,000 …`)은 여전히 **파서 갭**이다(#93·#111) | ✅ 자동 | `test_a_total_row_with_footnote_and_dashes_is_still_a_parser_gap` |
| 미공시류로 재분류되면 **이미 쌓인 개선 여지 줄**을 지운다(§Automation-first) | ✅ 자동 | `test_reclassifying_to_undisclosed_clears_the_old_fixable_line` |
| 그 정리는 신원(종목·연도·보고서)으로만 — 남의 줄을 안 지운다(#45) | ✅ 자동 | `test_reclassification_does_not_touch_other_tickers_or_quarters` |
| 롤링 검산은 **형제 항등식**을 그대로 부른다 — 괄호 음수 인식을 받는다(#38) | ✅ 자동 | `test_rolling_check_accepts_parenthesised_negative_recognition` |
| 발췌 창이 `합 계` 행에 닿는다 — 리터럴이 아니라 동작으로(#19·#156) | ✅ 자동 | `test_excerpt_window_reaches_the_total_row` |

⚠️ **픽스처를 두 번 고쳐야 발화한 가드가 둘이다**(#91c): 시작 열 요구는 행이
3값이면 `_roll_ok` 의 `len == 4` 가 대신 막아 '머리 3열 · 행 4값' 픽스처가
필요했고, 프로브 키 귀속은 **키를 아무도 안 지목한** 봉투에선 `key=` 가
무의미해 다른 키를 지목하는 봉투가 필요했다.

**못 보는 축**(#274): (a) `expected_literal` 의 파이프 목록 가드는 유일한
호출부(`learn_fail_reason`)가 `allowed_values` 가 이미 `()` 를 낸 뒤에만 도는
탓에 **발화 경로가 없다** — 방어용으로만 남긴다(#291·#373). (b) 키 귀속은
`k: v` 부분이 하나라도 있으면 **귀속 없는 열거를 버린다** — 원천이 키 이름을
안 적기 시작하면 테마 프로브가 조용히 0종이 된다(의도한 #32 맞교환이고
`learn_fail_reason` 이 그 사실을 말하지만, 그게 이 축의 한계다). (c)
`size_cap_from` 의 `_LE_RE` 창(200자)은 열거를 앞세운 뒤 긴 사유가 앞에 오면
상한을 못 읽는다 — 오늘 봉투는 필드당 사유가 하나라 잠복이다. (d)
`_parse_rolling` 은 `_balance_matches` 의 **수주 문맥 게이트**(#109)를 안 거치고
`_parse_xbrl` 보다 먼저 시도된다 — 신원·단위·헤더 3토큰을 다 요구하지만 우선순위
변경의 표본은 하나다.

**못 보는 축**(#274): (a) 한 구간이 우리 키와 남의 키를 **함께** 담고 그
구간에 남의 목록만 있으면 `_key_scope` 는 못 가른다 — 실측 봉투 둘은 그
모양이 아니다. (b) 거래량 보드가 **왜** 그 봉투를 받게 됐는지(원천 스키마가
유니온으로 바뀌었나 · `fieldErrors` 둘째 사유를 우리가 버렸나)는 아직 재지
못했다. 두 fix 가 어느 쪽이든 다음 VM 실행이 사유로 답한다(#82·#165). (c) 사유
채널은 **급등·급락에만** 배선했다 — 52주 신고저·상한가 보드는 여전히
`_get_stocks`(값만)를 쓰므로 빈 화면이 갈래를 말하지 않는다.

### 정리매매 뱃지 — 랭킹 보드가 "왜 ±30% 밖인가"에 답한다 (2026-09-19)

사용자 질문: "코스나인이나, 원풍물산 코다코같은건 맞는거야? 이거 정리매매
종목이야? 30% 가 상하한룰로 알고 있는데." 화면은 `1원 · -50.00%` 만 적고
있었다 — 값은 KRX 가 준 그대로라 행마다 산수도 맞고(#33) 값이 다 '있어서'
어떤 감사도 안 걸렸는데(#96), 화면이 답을 못 해 사용자가 물어야 알았다(#43).

원천은 **새로 찾지 않았다**. 이 레포가 볼린저 유니버스용으로 이미 받는 KIS
마스터(공개 zip·인증 불필요)의 컬럼 목록에 `정리매매`·`거래정지`·`관리종목`
이 들어 있었고 `_kis_master_rows` 가 그 여섯 칸만 남기고 버리고 있었다 —
"이미 부르는 호출이 무엇을 더 주나"(#150·§작업 원칙)를 먼저 물은 자리다.

| 축 | 테스트 | 왜 |
|---|---|---|
| 책마다 다른 컬럼명 | `test_master_carries_the_risk_columns_for_both_books` | KOSPI `정리매매` ↔ KOSDAQ `정리매매 여부` — 한쪽만 맞으면 그 시장이 영영 뱃지를 못 받는다(#27·#34) |
| 덧붙인 키 | `test_existing_callers_are_untouched` | `risk_raw` 는 추가다 — 유니버스 빌드가 바뀌면 안 된다 |
| 독스트링 ↔ 코드 | `test_field_slice_raises_value_error_not_key_error` | `list.index` 는 ValueError 인데 독스트링이 오래 KeyError 라 적고 있었다(#55) |
| 드리프트를 말한다 | `test_a_missing_risk_column_is_counted_not_silent` | 조용히 빠지면 뱃지가 영영 안 붙는데 아무도 모른다(#43·#54) |
| 3-상태 | `test_parse_flag_is_three_state` · `test_unknown_encoding_is_counted_with_a_raw_sample` | 인코딩을 **재지 않았다** — 모름을 False 로 접으면 '정상'과 구별되지 않는다(#54·#82·#165) |
| 반쪽 금지 | `test_a_half_map_is_never_baked` · `test_zero_rows_is_not_a_clean_map` | 코스닥 zip 만 실패한 맵을 구우면 12시간 동안 뱃지가 없다(#280·#384) |
| 배선(값으로) | `test_the_badge_reaches_the_rendered_row` · `test_both_boards_the_user_named_get_it` | 순수 함수만 재면 배선을 떼는 변형을 못 잡는다(#20) |
| 안 그리는 것 | `test_unknown_is_never_drawn_as_a_badge` · `test_only_the_key_that_waives_the_price_limit_is_badged` | 모름을 '정리매매'라 적으면 거짓말이고(#165), 관리종목·거래정지는 ±30% 를 면제하지 않는다(#25·#260) |
| 범례 | `test_the_legend_appears_only_when_a_badge_was_drawn` · `..._age_label_reads_as_a_time_not_a_blank` | 늘 있는 각주는 아무것도 안 재는 것과 같다(#25·#260) |
| 비용 | `test_non_kr_boards_never_pay_for_the_lookup` · `test_the_lookup_is_once_per_panel_not_per_row` | 행마다 부르면 30행이면 캐시를 30번 읽는다(#113·#61) |
| 렌더-세이프 | `test_render_path_never_downloads` · `test_a_cold_cache_kicks_the_background_warm` | 콜드면 zip 2개 × 30초 상한이다 — 렌더가 기다리면 화면 블록(#116·#312) |
| 워밍 | `test_the_warm_dedup_is_per_source_not_global` · `..._never_parks_the_source_forever` | `start()` 가 던지면 그 원천이 영구 정지한다(#371) |
| 파손 내성 | `test_a_torn_or_junk_cache_never_raises` | 한 바이트가 보드 셋을 비운 적이 있다(#331) |
| 표면 | `test_badge_css_travels_with_the_panel` · `test_the_badge_passes_aa_in_both_themes` | 페이지 번들의 `.sm-note` 를 빌리면 그 번들을 안 쓰는 보드에서 미정의(#273). 채움형은 다크 3.68:1 로 AA 미달(#355) |
| CLI | `test_run_hint_includes_cd` · `test_the_cli_dispatches_why` | 광고한 CLI 가 죽은 채 배포될 수 있다(#252·#278·#351) |

뮤테이션 14종 전부 잡힘(0 통과) — 복원은 백업 파일 + md5 대조(#358).

**못 보는 축**(#274): (a) **플래그 값의 인코딩을 재지 않았다** — 샌드박스에서
KIS 마스터에 도달할 수 없다. Y/N·0/1 을 둘 다 받고 그 밖은 '모름'으로 세어
`--why` 가 표본과 함께 말한다. 첫 VM 실행이 이 가정을 스스로 반증한다(#82·#165).
(b) **이 세 종목이 실제로 정리매매인지도 재지 않았다** — 같은 이유다. 뱃지는
KIS 가 그렇다고 말할 때만 뜬다. (c) 뱃지는 `stock_panel` 안에서 붙으므로
`_RISK_SOURCES` 에 원천이 있는 시장의 **모든** 보드(52주·장전장후·NXT 포함)에
같이 나간다 — 사용자가 짚은 둘만 고르려면 게이트를 **더** 넣어야 해서
그러지 않았다(§UNIVERSAL). (d) 뱃지 키는 정리매매 하나다. `거래정지`·
`관리종목` 은 같은 파싱에서 공짜로 와 맵에는 실리지만 그리지 않는다 —
그 둘은 ±30% 를 면제하지 않으므로 사용자가 물은 질문의 답이 아니다.

#### 독립 리뷰가 배포 전에 잡은 것 (2026-09-19 · 5건 · 전부 실행으로 재현)

| # | 무엇이 | 왜 나쁜가 | 고친 뒤 축 |
|---|---|---|---|
| F1 | `why()` 의 ② '원천' 이 12시간 캐시에 걸려 **캐시를 원천이라 불렀다**(원천 호출 0, rc=0) | ① 과 ② 가 같은 것을 읽으면 대조군이 성립하지 않는다 — 바로 #392 가 고친 그 병을 내 진단이 그대로 냈다(#143) | `snapshot(force=True)` · `test_the_probe_source_section_actually_hits_the_source` |
| F2 | `_collect` 이 성공 경로에서 원천 note 를 버려 **'상태 컬럼 미발견' 이 어디에도 안 닿았다** | KIS 가 컬럼명을 바꾸면 빈 맵이 12시간 구워지고 화면은 '오늘 해당 종목 없음' 이라고 거짓을 말한다(#43·#54) | `test_a_column_rename_is_never_read_as_zero_stocks` |
| F3 | 실패에 **도장을 안 남겨** 렌더마다 KIS zip 2개를 다시 받았다(실측 5렌더 = 10다운로드) | `/highlow` 는 `no-cache` + 30초 폴링이다 — 원천이 죽은 하루가 곧 연속 다운로드 루프(#116·#303·#384) | 지수 백오프 15분→6시간 · `test_a_dead_source_backs_off_instead_of_looping` · `test_backoff_grows_and_is_capped` |
| F4 | 원천 불가가 '해당 종목 없음' 과 **글자 하나 안 다르게** 보였다 | 이 기능이 없애려던 그 침묵이 그대로 돌아온다(#43·#45) | `snapshot.state` 를 렌더까지 · `test_an_unavailable_source_is_never_silent` |
| F5 | 킥이 `_fetching_pages` 에서만 막혀 다른 KR 렌더 테스트가 daemon 을 띄웠다 | 형제의 `_cache_write` 패치 안에 착지하면 **단독 green / 전체 red**(#30·#128·#312) | 픽스처(`_cache`)가 구조로 막는다 · `test_the_fixture_blocks_the_background_kick` |

뮤테이션 F1~F5 + 백오프·낡은 맵까지 11종 전부 잡힘(0 통과).

**여기서 새로 못 보는 축**(#274): (e) F5 의 경합은 **결정적으로 재현할 수
없다** — 스레드가 형제 테스트의 패치 창 안에 착지해야 터진다. 그래서 축이
구조 검사(픽스처가 킥을 막는가) 하나뿐이고, 그게 이 가드의 한계다.
(f) 백오프 간격이 **적절한지**는 재지 않았다 — "원천이 오래 죽으면 하루 몇
번인가"를 세어 정한 값(6시간 상한 → 하루 8~9회)이지 실측이 아니다(#384 와
같은 근거). (g) `stale` 을 계속 서빙하는 상한이 없다 — 원천이 며칠 죽으면
며칠 된 플래그를 뱃지로 그린다. 상장 상태는 하루 단위로 바뀌므로 낡은 값이
빈 값보다 낫다는 판단이고(#41), 며칠이 지나면 잘못된 뱃지가 될 수 있다.

#### 첫 VM 실측이 답한 것과, 그 출력이 못 답한 것 (2026-09-19)

배포 직후 `bot.kr_stock_flags --why` VM 실측:

```
② 원천 — ✅ 3,940종목 · 플래그 있는 종목 220 · kospi 2,583행 · kosdaq 1,823행
③ 정리매매 — ✅ 8종목: 006380, 008290, 046070, 082660, 084180, 121850, 465320, 472220
④ 모름 — ✅ 0건(전 행이 Y/N 또는 0/1 로 읽혔습니다)
```

**재지 않고 3-상태로 둔 것이 실측으로 닫혔다** — 인코딩은 Y/N 또는 0/1 이고
모름 0건이다(#165 재지 않은 것을 단정하지 않은 것이 옳았고, #82 갈래를 남겨
둔 덕에 한 번에 확인됐다).

**그런데 그 출력으로는 사용자 질문에 답이 안 된다.** ③ 이 **코드만** 찍어서
"008290 이 원풍물산 맞나"를 사용자가 따로 찾아야 했다. `_kis_master_rows` 는
이름을 **이미 주는데** 진단이 버리고 있었다 — F2(원천 note 버림)와 같은
자리이고 #123·#129·#189·#228·#292 에 이은 계열이다. 그리고 `플래그 있는
종목 220` 은 정리매매·거래정지·관리종목을 한 수로 뭉갠 것이라 무엇이 몇인지
알 수 없었다(#45).

⚠️ 레포로는 코드↔이름을 풀 수 없다 — 그 코드들이 나오는 곳은 **내가 쓴
테스트 픽스처와 독스트링뿐**이다. 내 가정을 내가 되읽는 것이라 근거가 아니다
(#19 소스 문자열로 자기 값을 축복하는 것 · #66).

| 축 | 테스트 |
|---|---|
| ③ 이 코드와 이름을 같은 줄에 · ② 가 갈래별로 센다 | `test_the_probe_names_the_stocks_it_lists` |
| 코드가 회사명인 척하지 않는다(`(이름 미확보)`) | `test_a_name_we_could_not_get_is_never_invented` |
| 모름은 소계에 안 들어가되 **사실은 말한다** | `test_unreadable_rows_are_never_counted_as_confirmed` |
| 소계 합 ≠ 총계(한 종목이 여러 갈래) — 그 차이를 화면이 말한다 | `test_overlapping_flags_are_reconciled_on_screen` |
| `names` 키는 **네 경로 모두** 싣는다(독스트링 계약) | `test_every_path_carries_the_names_key` |
| 이름은 **진단 전용** — 캐시에 안 굽는다(스키마 불변 → 배포가 12시간 무효화를 유발하지 않는다) | `test_names_are_diagnostic_only_and_never_baked` |

##### 두 번째 독립 리뷰가 또 6건을 잡았다 (2026-09-19)

첫 판의 "뮤테이션 6종 전부 잡힘" 은 **내가 고른 6종**에 대해서만 참이었다 —
리뷰가 같은 diff 에 6종을 더 태우자 전부 살아남았다. 계수는 커버리지가
아니다(#286 지시서가 자기 자신에 대해 사실 아닌 것을 말한다).

| # | 무엇이 | 왜 나쁜가 |
|---|---|---|
| G1 | `_kis_master_rows` 는 이름이 비면 **코드**를 넣는다(`head[21:].strip() or code`) — 그걸 그대로 실어 `008290  008290` 이 찍혔다 | **코드가 회사명인 척**한다(#34·#165). 그리고 `(이름 미확보)` 갈래는 **도달 불가**였다(#291) — 그걸 '덮는다'던 내 테스트는 `force=True` 가 낼 수 없는 상태를 스텁했다 |
| G2 | 갈래별 소계가 `is True` 만 세서 모름이 **어느 소계에도 안 들어갔다** | 인코딩 드리프트한 날 `정리매매 0` 옆에 `플래그 있는 종목 220` 이 붙어 원천이 0종목처럼 읽힌다(#45) |
| G3 | 그 `is True` 계약이 무가드 | 모름을 '확인된 정리매매' 로 세는 변형이 통과 |
| G4 | "`names` 는 네 경로 모두 싣는다" 는 독스트링 약속이 **무가드** | 키가 빠지면 KeyError 를 렌더 except 가 삼켜 **엉뚱한 이유로** 뱃지가 사라진다(#54·#55) |
| G5 | `snapshot()` 독스트링 반환 키 목록에 `names` 누락 | #55 드리프트 |
| G6 | 이 문서가 "뮤테이션 6종 전부 잡힘" 이라 적고 `(이름 미확보)` 를 덮인 축으로 열거 | 둘 다 거짓이었다(#286) |

⚠️ **그리고 그 fix 의 첫 뮤테이션에서 3종이 또 살아남았고 셋 다 내
테스트 결함이었다**(#91 ①돌긴 했나 ②재는 대상이 맞나 ③픽스처가 센가):
(a) `is not None` 은 **모름도 `None`** 이라 no-op 이었다 — 의미 있는 변형은
`k in v` 이고 그걸로 다시 쟀다 (b) 실패 경로 단언이 앞 단계가 심은 캐시에
걸려 **그 경로를 한 번도 안 탔다**(`force=True` 누락, #91b) (c)
`"names" in __doc__` 을 **뒤 설명 문장이 대신 만족**시켜 반환 키 목록에서
빼도 통과했다(#75) — 그 줄 하나를 잘라서 본다(#55).
정정 후 재측정: **G1·G2·G3'·G4a·G4b·G4c·G5 전부 잡힘**.

⚠️⚠️ **F5 가 경고한 경합이 실제로 났다.** `test_a_torn_or_junk_cache_never_raises`
는 `self._cache` 를 안 쓰므로(일부러 쓰레기를 심는다) 킥이 안 막혀, daemon 이
`tmp_path` 에 백오프 도장을 써서 새 단언(`_read_envelope() == {}`)이 **단독
green / 전체 red** 가 됐다(#128). 킥을 손으로 막아 해소하고 **10회 반복
실행**으로 확인했다 — 한 번 green 은 경합의 증거가 아니다.

**여기서도 못 보는 축**(#274): (h) 8종목이 **어느 회사인지**는 이 레포가
자체적으로 확인할 수 없다 — 다음 VM 실행이 이름과 함께 찍는다. (i) 정리매매가
**아닌데** ±30% 를 넘은 종목의 사유는 이 진단이 답하지 않는다(변경상장·
재상장 등 다른 예외). 필요하면 종목코드 단건 조회 축을 따로 둬야 한다.

---

## 장전·장후 저장분 (`TestPrepostStoredSnapshot20260919`, 27건)

사용자 2026-09-19(토 22:5x KST) "장전장후 시간대가 아니라면 가장 최종데이터를
그대로 남겨줘 … 금요일 장후데이터가 되겠지" — 화면은 `데이터가 없습니다` 였다.
원인은 `ttl=86400` 인데, 이 보드는 **창 밖에서 재스캔을 안 하므로** 파일 mtime
이 얼어붙고 24h 뒤 캐시가 죽어 **주말·연휴가 정의상 빈칸**이 된다(#394).

| 축 | 무엇을 재나 |
|---|---|
| ① 만료 없음 | 27h(KR)·30h(US)·7일 된 저장분이 **버려지지 않는다** + `_cached(ttl=)` 이 `_STORED_FOREVER` 인지 AST 로 |
| ② 저장분 아님 | 저장분이 **아예 없으면** `stale=False`·`stale_min=None` — 낡은 것과 없는 것은 다른 사실(#82) |
| ③ 라이브 | 창 안 + TTL 안이면 라벨이 **안 붙는다**(#25 늘 뜨는 배지 금지) |
| ④ 창 안·뒤처짐 | 스캔이 멀쩡하면 본문 줄이 **없고**, 실패면 '다음 창' 이 아니라 '이번 창' 이라 적는다(#55) |
| ⑤ 문구 단일 출처 | 두 렌더가 `stored_note` 를 부르는지 AST + 나이를 숫자로(#202) + 재시도 시점은 창 판정이 있을 때만(#165, 창 라벨이 비면 빈 괄호 대신 침묵) + **정상 경로에선 빈 문자열**(사용자 2026-09-20) + 배너·줄이 `retry_clause` **한 함수**에서 나온다 |
| ⑥ 화면 E2E | 표가 그려지고 **부제**가 `💾 저장분(N시간 전)`·기준시각을 말한다(KR·US **둘 다**) + 평상시 본문 `sm-note` 엔 되풀이 줄이 **없다** + **실패+행** 경로는 두 페이지가 줄을 내고 그 줄이 `다음 창(<창>)` 을 싣는다(창 인자 배선) + `.sm-note` CSS 정의(#201·#273) |
| ⑦ 옛 이름 | `_*_prepost_fresh` 가 되살아나지 않는다(창 밖을 '신선'이라 부르던 그 이름, #34) |
| ⑧ 재스캔 | 창 **안** + 뒤처짐이면 저장분을 주되 `_kick_*` 이 **걸린다**(보드가 저장분에 영원히 얼지 않는다) + 창 밖이면 안 건다(#25 반대 증거) |
| ⑨ 상태도 만료 없음 | 26h 된 `failed` 도장이 **살아 있고**(상태 파일), `stored_note` 가 **그때만** 한 줄을 낸다 — `자동 갱신`·`자동 재집계` 는 줄에도 **KR 배너에도** 금지어이고 '다시 시도합니다' 만(#380·#375) + 기계 상세는 그 줄에 안 샌다(#391) |
| ⑩ `running` 만료 | 죽은 스캔의 도장이 '진행 중'을 영원히 말하지 않는다(순수 함수 **와** 두 화면, #20) + 방금 찍힌 도장이면 뜬다 |
| ⑪ 없음 ↔ 못 읽음 | 쓰다 만 파일(#379)은 `stored_unreadable=True` 이고 화면 문구가 갈린다 — KR·US **둘 다**(#38) |
| ⑫ 나이 순서·부호 | 내용을 읽는 사이 재집계가 끝나도 '라이브'로 안 뒤집힌다(#160) + mtime 이 미래면 음수 나이를 안 찍는다(#34) |

뮤테이션 **34종 전부 잡힘**(만료 24h 되살리기[저장분·상태] · 창안 단락 제거 ·
나이/내용 순서 뒤집기 · `running` 만료 제거 · 실패 꼬리 제거 · `unreadable`
갈래 제거 · `freshness_label` 상수화 · `stale_min` 음수 허용 · 두 페이지의
부제·빈페이지 문구 하드코딩 · 두 페이지의 `running` 술어 복귀 · 저장분 줄
배선 제거 · 백오프 리터럴 복귀 · **평상시 줄 되살리기**[조기반환 제거] ·
실패 사실 제거 · 창 안인데 '다음 창' 이라 말하기 · 창 판정 미상인데 시점 단정 ·
나이·기준시각 떼기 · **독립 리뷰 뒤 11종**[KR·US 호출부의 창 인자 누락 ·
KR 호출 남기고 결과 버림 · `retry_clause` 의 window 가드 제거 · KR 배너 retry
절 제거 · KR 배너 옛 약속(`자동 재집계`) 복귀 · US 부제 기준시각 제거 ·
`stale_min` 음수 허용]). 베이스라인 green 확인 + 치환 건수 단언 + md5
복원 확인을 같이 했다(#328·#358·#383). 옛 코드에서는 14건 중 13건이
빨간불이었다(재현 먼저, §Pre-commit 9).

⚠️ **첫 판의 뮤테이션 10종은 이 축들을 안 봤다** — 창안 단락·나이 순서·상태
만료는 독립 리뷰가 각각 506개 전부 통과하는 변형으로 짚었다. 그중 창안 단락
변형(`stale is not None`)은 **이 커밋의 목적을 정반대로** 뒤집는다(보드가 저장분에
영원히 언다). 가드를 넣었으면 그게 실제로 발화하는 경로로 재야 한다(#91·#291).

⚠️ **2026-09-20 사용자가 본문 줄을 뺐다** — "이 코멘트는 대시보드에 없어도
될것 같아. 당연한거니까." 부제가 이미 `💾 저장분(28시간 전) · 실시간 아님 ·
<창> · <기준시각>` 을 말하므로 본문의 같은 문장은 소음이다(#25·#45). **지우지
않고 좁혔다**: `stored_note` 는 마지막 집계가 **실패**했을 때만 한 줄을 낸다.
그 갈래를 남긴 근거는 둘이다 — (a) 실패 사실은 부제의 어느 칸도 말하지 않고
그때 "자동 갱신된다"는 지킬 수 없는 약속이다(#380·#43) (b) US 는 빨간 실패
배너가 `building` 분기 **안**이라 표가 있으면 도달 불가라서 이 줄이 유일한
통로다(2026-09-19 독립 리뷰 H2). 옛 계약 6건은 지우지 않고 **새 계약으로 다시
썼다**(#222) — 남는 보장(나이는 숫자 · 창 안에서 '다음 창' 금지 · 안 잰 시점
단정 금지 · 기계 상세 누출 금지)은 그대로 재고, 나이·기준시각 보장은 **부제로
자리를 옮겨** 잰다. ⚠️ 그 과정에서 음수 나이 테스트가 **동어반복이 됐다**(줄이
빈 문자열이면 `"".split()` 에 `-` 가 없다) — 줄이 실제로 뜨는 실패 갈래로
태워 되살렸다(#291).

⚠️⚠️ **배포전 독립 리뷰가 Blocking 0 · High 2 · Medium 4 · Low 4 를 냈고 전부
반영했다**(2026-09-20). 둘이 이 표에 직접 걸린다. (a) **음수 나이 가드가 고친
뒤에도 동어반복**이었다 — `note.split("집계")[-1]` 이 새 꼬리의 `마지막 **집계**
시도가` 를 집어 나이가 `[1]` 에 살았고, 옛 사유 문구도 "집계" 를 품어 **base
에서도** 통과했다(리뷰가 `-120분 전` 으로 재현). 즉 #395 가 처음 적은 원인
진단이 틀렸고 지금은 나이 절 하나를 잘라 본다(#55·#75·#91b). (b) **재작성이
축 둘을 떨어뜨렸다** — 옛 KR 테스트의 `assert "다음 창(" in hit[0]` 이 창 인자
배선을 화면에서 재고 있었는데 부정 단언으로 바꾸며 사라져, 호출부 인자 누락과
`if False:` 결과 버림이 둘 다 통과했다(base 에선 잡혔다, #222 는 '줄인다' 가
아니다 · #378·#20·#313). KR 실패+행 E2E 를 새로 두어 한꺼번에 복구했다.
그리고 같은 화면 두 줄 위 **KR 배너**가 `자동 재집계됩니다` 라고 적고 있었다 —
줄에서 금지한 약속의 동의어라 `retry_clause` 단일 출처로 합쳤다(#38·#147).

⚠️ **단언 함정(실측)**: 페이지 전체에서 `저장분` 을 찾으면 shell 의
`live_refresh` **JS 주석**(#360a)이 대신 만족시킨다 — `<div class="sm-note">`
칸 하나를 잘라서 본다(#55·#75·#91b).

⚠️ **`grep` 으로 '다른 호출부 없음' 을 판정하다 두 라운드에 걸쳐 셋을 놓쳤다** —
① `head -25` 에 `TestUsPrepost::test_prepost_fresh` 가 잘려 안 보였고 ②
`prepost_client._CACHE_DIR` 패턴이 **별칭으로 patch 하는**(`monkeypatch.setattr(pp,
"_CACHE_DIR", …)`) 픽스처 둘을 못 봤다. 둘 다 `-k` 선택 실행에선 green 이었고
**전체 게이트**가 잡았다(#363·#366 선택 실행은 그 선택 밖을 못 본다). 자르는
자리·좁히는 패턴이 다음 결정을 가리지 않는지 먼저 물을 것(#156·#338·#350 —
같은 함정 네 번째·다섯 번째). 옛 계약은 지우지 않고 **새 계약으로 다시
썼다**(#222): `prepost_client._CACHE_DIR` 는 아무것도 리다이렉트하지 않는 죽은
이름이라 제품에서 지웠고(#53), 회귀가 `not hasattr(...)` 로 부활을 막는다.

**못 보는 축**(#274): (a) 스냅샷이 **몇 세션 뒤처졌는지**는 재지 않는다 —
신호는 **부제**의 "N일 전" 나이 라벨과, 실패 시 KR=배너·US=`stored_note` 줄이다
(⚠️ 첫 판은 그 둘째를 "`failed` 배너" 라고 적었는데 **US 에선 거짓**이다 —
배너가 `building` 분기 안이라 표가 있으면 도달 불가이고, 그게 이 커밋이 실패
갈래를 남긴 근거다. 한 문서가 두 말을 하고 있었다, #55·#286). 달력 기반 세션
격차 판정은 장전 창에서 상시 경고가 될 위험이 있어 안 넣었다.
⚠️ (a2) **"창 안 + 저장분 + 스캔 정상"** 은 이제 아무 줄도 안 낸다 — 처방이
다른 "창 밖 + 저장분"(다음 창까지 안 바뀐다)과 같은 침묵이 됐다(#82, 독립 리뷰
M2). KR 은 킥이 `running` 도장을 찍으면 다음 로드에서 파란 배너로 부분
자가회복하지만 **US 는 `running` 배너도 `building` 안**이라 행이 있으면 안 뜬다.
사용자가 빼 달라고 한 것이 정확히 그 '당연한' 줄이라 되살리지 않았다 — 고치려면
US 배너를 `building` 밖으로 빼는 별도 변경이고, 그러면 실패 갈래의 근거 (b) 도
같이 다시 물어야 한다. ⚠️ 첫 판은 그 `failed`
배너를 보상 신호로 적어 놓고 **정작 배너가 24h 뒤 사라지게** 두고 있었다
(상태 리더도 `ttl=86400` 이었다) — 지금은 상태도 만료 없이 읽으므로 그 문장이
참이다. (b) `ttl=86400` 을 쓰는 **이 파일 밖** 20여 곳은 그대로다 — 그쪽은 창
밖에서도 kick 하므로 mtime 이 갱신돼 같은 구멍이 없다(형제 `fetch_us_movers`·
`fetch_jp_stop` 으로 확인). "재스캔 안 함 + TTL" 조합이 또 생기면 같은 사고가
난다. 전수로 재는 가드는 없다 — '누가 이 캐시를 다시 쓰나'는 호출 그래프
질문이라 기계로 세려면 오탐이 크다. (c) `running` 만료 문턱(30분)이 백오프와
같은 값인지는 AST 로 보지만, **그 값이 옳은지**는 재지 않는다.

---

## #396 — FCF 교차출처 ② 가 '정의 차이'를 결함으로 찍었다 (2026-09-21)

**증상** 일일 감사(`코드 739de2ec2a`)의 ❌ 8건이 전부 `181710.KS` 의
`② 교차출처(DART ↔ yfinance)` 였다(분기 5 · 연간 3).

**측정** ❌ 줄이 달고 있던 재료(#356)를 산수로 맞추자 8기간 전부:

| | |
|---|---|
| `\|yfinance CAPEX\|` | `= DART 유형자산취득 + 무형자산취득` |
| `DART FCF − yfinance FCF` | `= 무형자산취득` |

표시 반올림 0.1억 안쪽. 즉 값이 아니라 **CAPEX 정의**가 갈렸다 — 제품은
FnGuide 기준(유형만, #215 사용자 결정)이고 yfinance 는 무형을 묶는다.
FY2024 의 94.07% 는 분모(−9.9억)가 작아 생긴 것이다.

**고친 것**

| 자리 | 무엇 |
|---|---|
| `fcf_audit.bundled_intangible` | `\|yfCAPEX\| = 유형+무형` **항등식이 설 때만** 무형 크기를 돌려준다(#106) |
| `fcf_audit.basis_note` | **원차가 허용치를 넘을 때만** 재고, `DART FCF − 무형` 이 yfinance FCF 와 `_GAP_OK` 안이면 (설명됨, 사유) — 정상 행에 문구가 붙으면 소음이다(#25·#260) |
| `fcf_audit._mark` | 설명 축 추가 — 허용치를 넘어도 ✅ 이고 **차이 크기는 그대로** 찍는다(#41·#182) |
| 분기·연간 호출부 | 같은 판정을 쓴다 — 한쪽만 고치면 연간 ❌ 만 남는다(#38·#147) |
| `fcf_audit._AUDIT_VER` | 3→4 · 배너가 새 축(CAPEX 구성 항등식)을 말한다 — 판정 의미가 바뀌면 판을 올린다(#21) |
| `bot/fcf.py`·`bot/dart_quarterly.py` | #215 가 덧붙인 "yfinance 와도 정의가 같다" 를 실측으로 정정(#165·#55) |

**왜 ✅ 인가** 우리가 고칠 것이 없다 — 산식은 사용자가 정한 FnGuide 기준이고
원천은 무형을 따로 주지 않는다. 화면은 이미 두 탭이 서로를 가리키며 원천·산식을
밝힌다(#102·#186·#220). 못 고칠 ❌ 가 매일 오면 진짜 ❌ 를 가린다(#260·#182).

**왜 tautology 가 아닌가**(#291·#293) 좌변 입력은 DART(OCF·유형·무형), 우변은
yfinance(OCF·CAPEX)다. 잔차가 0 이 되려면 두 OCF 가 같고 **동시에** yfinance
CAPEX 구성이 유형+무형이어야 하므로 옛 판보다 CAPEX 구성까지 더 잰다.

**회귀** `TestFcfCapexBasisGap20260921` 14건 — 순수 판정 10(반대 증거 포함) +
`audit_one` E2E 3 + 화면 각주 1. 뮤테이션 **두 라운드 16종** 전부 잡힘(파일 셋에
걸쳐 · 베이스라인 green·치환 건수 단언·md5 복원 확인). ⚠️ M10(분기 call site 의
재료 제거)이 **처음엔 생존**했다 — `"↳ DART OCF" in body` 를 **연간 줄이 대신
만족**시켰다(#75). 분기·연간을 잘라서 각각 본다(#55). ⚠️ 픽스처는 셋: DART `FCF`
를 손으로 넣으면 `_attach_fcf` 가 산식을 정하는 실제 경로를 안 타고(#155), 무형 0
케이스는 **항등식이 서는** CAPEX 라야 `not intan` 가드가 발화하며(#91c), yfinance
행에 `Free Cash Flow` 가 없으면 제품이 실제로 가는 길(`fcf_from_row` 가 원천 직접
값을 먼저 쓴다)을 한 번도 안 탄다(독립 리뷰, #79).

**독립 리뷰 11건**(전부 반영) — 셋이 이 델타의 핵심을 무력화하고 있었다:
(a) 사유의 왼쪽 숫자가 `유형 + 무형`(전부 DART 파생)인데 라벨은 `yf CAPEX` 라
등식이 **구조상 항상 맞았다**(리뷰 실측: 504.9억을 500.0억으로) — 원천 CAPEX 를
그대로 찍고 항등식 허용치 안에서 어긋난 픽스처로 잰다(#292·#202·#91c)
(b) ❌ 를 내리는 근거였던 "화면이 이미 말한다" 가 **과대 주장**이었다(각주는 '값이
다를 수 있습니다' 까지만) — `bot.fcf.CAPEX_BASIS_NOTE` 단일 출처를 두 탭이 싣고,
반대 증거(비-KR 탭엔 안 붙는다)까지 값으로 잰다(#38·#25)
(c) 반증된 문장이 **네 곳**에 더 있었다 — `test_fcf_needs_capex_in_the_dart_path`
계약 독스트링(#222 로 다시 씀) · `dart_client._DART_CODE_MAP` 위 '둘을 합산해야'
주석 · CLAUDE.md·REFERENCE 의 #215 본문(대체 표시, #287).
나머지: 잔차 문턱이 19.6% 픽스처 하나뿐이라 1%→5% 완화가 전 슈트를 통과했고(2.27%
픽스처로 메움, #372a) · `not both` 가드와 `or 0.0` 폴백은 **도달 불가**(#291) ·
무형 계정명만 리터럴이라 `_DART_INTANGIBLE_KEY` 로 올림(#214) · `fa` 에 없는
이름을 `raising=False` 로 patch 해 절반이 조용한 no-op(#25).

**못 보는 축**(#274): (a) 주석이 다시 "정의가 같다" 고 적는 것은 **막지 않는다**
— 문구 denylist 는 표현만 바꾸면 통과하므로(#373) 가드를 측정 쪽에 뒀다. 새
E2E 가 두 정의를 실제로 가를 때만 통과한다. (b) 이 면제는 **DART↔yfinance 축
하나**다. `|yfCAPEX|` 가 유형도 무형도 아닌 제3의 구성일 때는 항등식이 안 서서
종전대로 ❌ 다 — 그게 옳지만, 그런 원천이 나오면 갈래 이름이 하나 더 필요하다.
(c) 이 종목 말고 몇 종목이 같은 정의차를 갖는지는 재지 않았다 — 감사 표본이
도는 대로 다음 실행이 스스로 말한다(#82).

## #397 — `sys.modules` 오염 가드 (2026-09-21)

**증상**: `tests/` 전체 실행이 **9건 빨간불**인데 그 9건만 골라 돌리면 green.
배포 때마다 base 와 대조해 "같으니 무관" 으로 넘기며 몇 주를 보냈다.

**원인 하나**: `tests/test_dart_production.py::TestFscBreaker20260821._stub` 가
`sys.modules["httpx"]` 를 `SimpleNamespace` 로 **직접 대입**하고 복원하지 않았다
(`finally` 는 `_FAIL` 만 비웠다). 알파벳순으로 그 파일이 앞이라, 뒤에서
`telegram`·`langgraph` 를 **처음** import 하는 테스트가 전부
`AttributeError: … has no attribute 'Proxy'/'HTTPStatusError'` 로 죽었다.
`importorskip` 은 **ImportError 만** skip 하므로 그게 skip 이 아니라 **실패**였고,
`importorskip("telegram")` 옆의 "샌드박스엔 없다" 라는 **거짓 주석**(실측:
telegram·langgraph·httpx 모두 설치돼 있다)이 그 오진을 굳혔다(#55·#25).

| 자리 | 무엇 |
|---|---|
| `test_dart_production.py::TestFscBreaker20260821._stub` | `monkeypatch.setitem/setattr` — httpx·`_FAIL`·`fsc_key_ready`·`_env_key` |
| `test_regression.py::_mock_news_clients` | 진짜 뉴스 클라이언트를 **영구 대체**하던 것 → `monkeypatch.setitem` |
| `test_regression.py` `bot.world_quote` | `finally: pop` 이 원래 있던 진짜 모듈까지 지웠다 → `MonkeyPatch.context()` |
| `TestDartInvestmentNoticeUnparsed20260917` ×2 | `bot.dart_feed` pop+import 가 **tmp HOME 으로 구운 모듈**을 남겼다 → `monkeypatch.delitem` + **패키지 속성** `monkeypatch.setattr(bot, "dart_feed", …)` |
| `conftest.py` | `pytest_sessionstart` 기준선 + autouse fixture 오염 감지 |
| `importorskip("telegram")` ×5 | 거짓 주석 정정(안전망 자체는 유지, #139) |

**가드 설계** — 이름을 **열거하지 않는다**(#24). 세션 시작 스냅샷 대비 네 축:
① 새로 나타난 값이 **모듈이 아니다**(SimpleNamespace·MagicMock) ② 기준선에 있던
이름이 **다른 객체로 교체**됐다 ③ 손으로 만든 **빈 `ModuleType`**(`__spec__ is
None`)이 **레포 패키지** 자리를 차지했다 ④ 기준선에 있던 이름이 `sys.modules`
에서 **사라졌다**(`pop` 후 미복원). 기준선을 `pytest_sessionstart` 에 뜨므로
`bot/tests/conftest.py` 가 모듈 레벨에서 꽂는 MagicMock 은 **자동 면제**다(면제
목록을 손으로 적으면 반드시 새 항목을 놓친다).

**가드 자신의 경쟁**(2026-09-25 `make test` 실측): 가드가 살아 있는 `sys.modules` 를
`list(now.items())` 로 순회하는 사이 테스트가 띄운 백그라운드 스레드가 import 해
`dictionary changed size during iteration` — **가드가** `TestIntlHighLow52` 를 error 로
만들었다(앞선 두 실행은 통과 = 타이밍 경쟁). `sys.modules.copy()`(C 에서 한 번에 복사)
를 순회하게 고쳤다. 회귀는 시간·스레드가 아니라 순회 **도중에** 크기가 바뀌는 가짜
사전으로 결정적으로 재현한다(#128) — 옛 판에서 실패함을 확인했다
(`test_regression.py::TestSysModulesLeakGuard20260921::test_the_guard_snapshots_sys_modules_instead_of_iterating_it_live`).

⚠️ **③ 은 레포 패키지 안에서만 결함이다.** `__spec__ is None` 을 전역에 걸면
정상 C-확장·lazy 모듈이 줄줄이 걸린다(실측 오탐: `_cython_3_*` ·
`xml.parsers.expat.*` · `_openssl` · 디스크 소스 확인을 더한 뒤에도
`cryptography.hazmat.primitives.*`). 내가 "그런 모듈은 `__main__` 뿐" 이라고
적은 것은 **한 스냅샷을 전수인 양** 옮긴 것이었다(#286·#25). 경계는
`bot`·`tests`·`trade` — 이름을 적지 않고 **`__init__.py` 유무로 파일
시스템에서 파생**시킨다(#24).

⚠️ `importlib.util.find_spec` 으로는 못 잰다 — 그 함수는 `sys.modules` 를 먼저
보고 캐시된 모듈의 `__spec__` 이 `None` 이면 `ValueError` 를 던진다. 그게 바로
재려는 그 상태다(`PathFinder.find_spec` 으로 우회했다가 레포 경계 축으로
바꾸면서 통째로 걷어냈다).

⚠️ **`pytest_runtest_teardown` 훅에서 raise 하면 안 된다** — pytest 의 SetupState
가 정리를 못 마쳐 뒤 테스트가 `previous item was not torn down properly` 로 연쇄로
깨진다(실측). autouse fixture 는 요청형보다 **먼저** setup 되므로 teardown 이
나중이라, `monkeypatch` 복원을 오염으로 오인하지도 않는다.

**검증**: `tests/` 4,720 passed + 9 failed → **4,739 passed + 0 failed** ·
`bot/tests` 183 · `trade/tests` 1,275. 뮤테이션 **8종 전부 잡힘**(축 4개 · autouse ·
레포패키지 파생 · `_stub` 직접대입 되돌리기 · 패키지속성 복원 제거) — 특히
**`_stub` 을 옛 직접 대입으로 되돌리는 변형이 ERROR 로 잡혔다**(이 가드가 있었다면
그 9건 사고는 애초에 나지 않았다는 뜻). 회귀 **10건**은 루트 conftest 를
`-p conftest` 로 주입해 **레포 밖** 임시 파일을 서브프로세스로 돌린다(임시 테스트가
수집에 섞이지 않는다, #383) — 적발·범인 지목·오탐 없음·연쇄 없음·가드 위치를 각각
값으로 잰다.

⚠️ **패키지 속성 복원은 리뷰 지적으로 넣은 두 줄인데 무가드였다**(M8 생존, #291).
프로브가 그 복원 패턴을 **스스로 흉내내면** 제품에서 지워도 통과하므로(#19),
하네스에 "앞서 돌릴 노드" 인자를 더해 **제품 노드를 실제로 앞에 태운 뒤**
`bot.dart_feed is sys.modules['bot.dart_feed']` 를 본다. 재실행에서 M8 이 잡혔고,
빨간불이 된 것은 새로 넣은 그 테스트 하나였다(#267·#384 — 빨간불이 곧 '잡힘'은
아니므로 **실패한 테스트 이름**까지 확인).

⚠️ 그 회귀를 쓰다 **같은 병을 한 번 더 밟았다**: 연쇄 검사를
`"not torn down properly" not in out` 으로 썼는데 fixture **독스트링**이 왜 훅이
아니라 fixture 인지 설명하며 그 문구를 인용하고 있었고, 실패 출력에 독스트링이
통째로 실린다(#59b·#250·#268). 주 계약을 **결과**(뒤 테스트가 돌았나)로 옮기고
증상 이름은 `AssertionError:` 접두까지 집는다.

⚠️ 같이 드러난 옛 결함: `test_production_disk_caches_are_redirected` 가
`src[src.index("def _redirect_disk_caches"):]` 로 **파일 끝까지**를 함수 본문이라
보고 `yield` 를 찾았다 — conftest 뒤에 무관한 fixture 가 붙자 멀쩡한 코드를
틀렸다고 했다(#60·#174). AST 로 그 함수만 잘라 `Yield` 노드를 본다.

**못 보는 축**(#274): (a) `importlib.reload` 는 **같은 객체를 제자리 재실행**하므로
객체 동일성이 안 바뀌어 이 가드가 못 본다 — 모듈 전역 상태는 바뀐 채 남는다(레포에
6자리). (b) conftest import 시점에 **이미** 오염된 것은 기준선에 편입되어 면제된다.
(c) 보고 뒤 기준선을 **현재 값으로 갱신**하므로 같은 이름은 한 테스트당 한 번만
보고된다 — 첫 범인이 정확히 지목되고 **두 번째 누출도 그다음 테스트에서 잡힌다**
(옛 판의 `_MOD_REPORTED` 는 이름 단위로 영구 침묵시켜 두 번째 누출을 가렸다 —
독립 리뷰 지적으로 삭제). (d) `__spec__` 축은 **레포 패키지 밖**을 안 본다 —
서드파티끼리 서로를 가짜로 덮는 경우는 여기서 안 걸린다(오탐 비용이 그 감도보다
크다, #25·#260).

⚠️ **가드를 더 얹으려다 뺐다**: 배선 테스트에 "프로브가 실제로 돌았나" 마커
파일과 대소문자 무시 오류 검사를 얹었는데, 뮤테이션으로 재니 **발화 경로가
없었다**(실측 M10 생존). 제품 노드가 사라지면 pytest 가 `no tests ran` 을 내
기존 `"passed" in out` 이 잡고, 프로브 수집 실패는 `-q` 요약에 **소문자**
`1 error` 로 나오며(대문자 `ERROR: not found` 는 앞 경우뿐이다), 임시 파일이
수집에서 빠지면 같은 하네스를 쓰는 나머지 9건이 먼저 깨진다. 되돌리고 그
측정을 코드 주석에 남겼다 — 가드는 늘릴수록 강해지지 않는다(#291·#373).

## #398 — 사유 한 줄의 경계를 **조립부가 찍는다** (2026-09-21)

**증상**(사용자 캡처, 거래량 상위 `/krvolume`): ℹ️ 배너 한 줄에 pykrx 함수명·
예외 클래스(`get_market_price_change_by_ticker 실패(IndexError)`), zod 리터럴
(`expected "dividend"`), 우리 내부 추론("판별 유니온의 한 갈래로 보이지만 그건
재지 않았습니다"), 그리고 **값이 잘려 나간 라벨**(`원문: ·`)이 그대로 떴다.
#391 이 급등·급락에서 고친 그 증상인데 이 보드엔 안 닿았다.

**재현이 먼저**(§Pre-commit 9): 제품 생산부로 그 사유를 다시 만들자 **잘린 것은
63자뿐**이었다(회귀 픽스처 실측: 옛 판 719자 · `machine_detail_at` = **656**).
⚠️ 총 길이는 재구성한 메모 묶음에 따라 달라지므로(첫 재현은 541자였다) 이 문서의
수치는 전부 **커밋된 회귀 픽스처**(`TestVolumeBannerAudience20260921._reason`)로
다시 잰 것이다 — 재현 못 하는 수를 적으면 다음 사람이 그걸 기준으로 삼는다(#286·#362).

**원인 둘**:

| # | 자리 | 무엇 |
|---|---|---|
| 1 | `naver_diag.public_reason` | 경계를 `_MACHINE_MARKS` **세 어구로 되짚었다**(#24). 목록 밖 상세(zod 해설)는 머리에 남고, 목록 안 `원천이 HTTP ` 가 하필 그 해설의 꼬리 `원문: {…}` **안**에서 걸려 라벨만 남았다(#54·#292) |
| 2 | `kr_bulk_rank._kr_bulk_rows_uncached` | 덤프가 **사람 문장 안**에 있었다 — 상태 기록 → `attempt_note` → `stale_note` 를 타고 들어오므로 꼬리만 자르는 어떤 방식으로도 못 닿는다 |

**설계** — `compose_reason` 은 경계를 **알면서 버리고** 있었다(#123 계열).
아는 쪽이 적는다(#86):

- `naver_diag._DETAIL_MARK = " ⟪상세⟫ "` — `mark_detail` 이 **유일하게** 찍고,
  `compose_reason` 은 **덤프를 품은 기계 상세에만**(`carries_dump`) 그걸 부른다.
- `compose_reason` 은 **이어 붙이지 않고 가른다** — 조각마다 표식에서 잘라
  머리는 머리끼리·상세는 상세끼리 모으고 경계를 **하나만** 긋는다.
- `machine_detail_at` 은 표식이 있으면 **그 자리**를 돌려준다. 표식이 여럿이면
  **가장 이른 것**이 이긴다(사람 문장 안에 끼운 벌크 상세가 꼬리보다 앞선다).
  표식이 없는 사유(`http_reason` 하나짜리)는 옛 열거로 되짚는다 — 하위 호환.
- `kr_bulk_rank._bulk_fail_reason(days, tried)` — 사람 문장(`KRX 벌크가 N거래일
  에서 행을 못 냈습니다`) + 경계 뒤 함수·예외 덤프. 표본 3건을 자르면서
  **자른 건수**(`외 N건`)를 말한다(#45). `compose_reason` 이 아니라
  `mark_detail` 을 직접 부른다 — 이 덤프엔 `_MACHINE_MARKS` 어구가 없어
  `carries_dump` 가 못 알아본다(덤프인 줄 아는 쪽이 긋는다, #86).

⚠️⚠️ **첫 판은 표식을 무조건 찍어 급등·급락을 다시 침묵시켰다.** `det` 슬롯엔
`http_reason` 의 덤프만 오는 게 아니라 `shape_reason`·`parse_reason` 이 만드는
**덤프 없는 짧은 사람 문장**("… 엔드포인트 계약 변경 의심(우리가 고칠 것)")도
온다 — 무조건 자르면 그 갈래가 통째로 사라진다(#395 지우지 말고 좁힐 것 · #43).
세 클래스만 `-k` 로 돌렸을 땐 전부 green 이었고 **전체 게이트**가
`TestMoversPageStatesTheReason20260918` 두 건으로 잡았다(#363·#366 선택 실행은
그 선택 밖을 못 본다).

⚠️ **표식을 경계 '자리'에 두고 뒤쪽에 남긴 이유**: 첫 판은 표식 **뒤**를
시작점으로 돌려주고 자르는 쪽이 `endswith` 로 벗겼다. 그 한 줄을 지우는 변형이
`⟪상세⟫` 를 화면에 흘린다 — 분기 자체를 없애는 쪽이 구조다(#119).

⚠️ **사적 문자라 원천 본문이 이걸 담을 일은 없고**, 설령 담아도 그 표본은 언제나
기계 슬롯(= 실제 경계보다 뒤)이라 **더 자르는** 쪽이지 새는 쪽이 아니다.

**결과**: 같은 픽스처로 **722자 → 175자**. 화면에 남는 것 = 무엇으로 대체했나 · 기준일 ·
갱신 주체 · 지난 시도의 실패 시각 · `원천이 우리 요청을 거절했습니다`(갈래).
감사·로그·`--why` 는 전문 그대로(#45 청중이 둘).

⚠️⚠️ **둘째 판은 조립부가 이어 붙여 사람 문장을 먹었다.** 벌크 실패 사유는
표식을 품은 채 `attempt_note` 를 타고 **사람 슬롯**으로 들어오는데, 기계 상세를
그 뒤에 이어 붙이자 사이의 사람 문장이 '가장 이른 표식' 뒤로 밀려 통째로
잘렸다(급등·급락의 `엔드포인트 계약 변경 의심` 이 사라졌다). **이번엔 `-k` 로
재현되지 않았다** — 세션 앞쪽 테스트가 `KRX_ID` 를 넣어 둬야 pykrx 가 소켓
가드(#336)까지 가서 `ConnectionError` 로 끝나고, 그래야 표식을 품은 memo 가
만들어진다(#128·#311 단독 green · 전체 red). 프로브 플러그인으로 그 자리의
상태를 찍어 확정했고(#118 가설을 코드로 태울 것), 회귀는 그 상태를 **값으로**
재현해 세션 순서에 기대지 않는다(#20·#91c).

⚠️⚠️ **배포 전 독립 리뷰가 같은 누출을 다른 갈래에서 둘 더 찾았다.** 경계를
`_bulk_fail_reason`(전 거래일 실패)에만 그어, ① 행을 **찾은** 경로의 메모
(`_rows_on` pykrx 덤프 · `_kis_names` KIS 덤프)와 ② `_run_attempt` 의 예외 사유가
그대로 배너에 실렸다 — ②는 `mask_secrets` 도 안 거쳐 **URL 속 토큰**이 공개
화면에 떴다(§Secrets · 형제 사유 채널은 전부 마스킹을 거치는데 이 한 곳만).
**한 채널을 고칠 땐 거기 쓰는 생산부를 전부 세고 시작할 것**(#38·#45).

| 자리 | 고침 |
|---|---|
| `_rows_on` | `_note(m, dev=True)` — 방문자 문장과 운영자 덤프를 **생산 시점에** 가른다 |
| `_kis_names` | why 는 전부 운영자(방문자 결과는 `_rows_on` 이 적는다) → 머리 없는 상세 |
| `_run_attempt` | `mask_secrets` + `mark_detail("수집 중 예외가 났습니다", …)` |
| `stale_note` · `compose_reason` · 메모 합치기 | `naver_diag.join_notes` **한 결합기** — `" · ".join` 이면 표식 뒤의 사람 문장이 먹힌다 |

⚠️ 고치다 둘이 더 드러났다: `mark_detail` 이 **머리가 없으면 표식을 안 찍어**
머리를 호출부가 대는 조각이 그대로 샜다(전제 변경 #222 — 긋는 것은 구조이고
화면을 안 비울 책임은 `public_reason` 이 진다). 그리고 표식을 **앞뒤 공백까지**
찾는 바람에 `public_reason` 첫 줄의 `.strip()` 한 번에 경계가 통째로 안 잡혔다 —
`_MARK_CORE`(공백 없는 핵)로 찾는다(#65 문자열이 아니라 구조로).

**검증**: `TestVolumeBannerAudience20260921` 12건 + 기존
`TestPublicReason20260919` 8건 + `TestMoversPageStatesTheReason20260918` 8건.
뮤테이션 **13종 잡힘 · 1종은 앵커 소멸로 건너뜀**(#267·#383 치환 0건을
'생존' 으로 세지 않는다 — 그 축은 M14 가 덮는다) — 구분자 되돌리기 · 표식 무시 · `min`→`max` · 벌크
덤프를 사람 채널에 · `외 N건` 제거 · **렌더가 `public_reason` 을 안 부름** ·
갈래 이름 제거 · 표식 없는 사유를 통째로 비움 · `carries_dump` 늘 True ·
늘 False · `mark_detail` 이 표식 대신 ` · ` · 머리 없이 표식.

⚠️ 1차 배터리에서 **둘이 살아남았고 이유가 서로 달랐다**(#91 순서대로):
(a) 구분자 되돌리기는 배너 픽스처에 벌크 표식이 **더 앞**에 있어 거기서 잘렸고,
`http_reason` 모양은 옛 열거가 대신 잡았다 — 열거가 눈머는 자리는 그 어구가
`원문: {…}` **안**에 있을 때뿐이라 **벌크 층 없는 픽스처**를 따로 둬야 발화한다
(#75 옆 픽스처가 대신 만족시킨다) (b) '머리 없으면 표식 안 찍음' 은
`mark_detail` **독스트링이 적어 둔 규약**인데 가드가 없었다(#286·#291).
그리고 렌더를 태우는 테스트가 없었으면 여섯째도 살아남았다(#20).

⚠️ **이 가드가 못 보는 축**(#274): 표식을 쓰는 곳은 `mark_detail` 하나지만
**부르는 곳**은 둘이다(`compose_reason` 조건부 · `_bulk_fail_reason` 직접).
세 번째 생산부가 생겨 열거 어구 없는 덤프를 사람 문장 안에 넣고 `mark_detail`
을 안 부르면 그건 화면에 그대로 실리고, 여기서 빨간불이 나지 않는다 — 즉
`carries_dump` 의 열거 절반은 **여전히 열거**다(#24). 그리고 `naver_sector_
client`·`naver_research_client` 의 `reason` 은 `compose_reason` 을 안 쓰므로 이
경계 규약 밖이다(그 화면들은 원래 덤프를 안 싣는다).

⚠️ **같은 실행이 잰 것 하나 더 — 원인은 재지 못했다**: 그 배너가 말하는 pykrx
벌크 갱신이 **6거래일에서** 행을 못 냈다(`get_market_price_change_by_ticker`
`IndexError`). 보드는 19:56 저장분을 계속 서빙하므로 **실패한 것은 갱신**이다.
샌드박스는 KRX 에 닿을 수 없어 원인은 단정하지 않는다(#12·#165) — 다음 VM
실행의 `--why` 출력이 갈래를 말한다(#82).

## #399 — 종목 딥링크 (`trade/tests/test_stock_link.py`, 43건 + 회귀 5건)

사용자 2026-09-22 "종목제목을 클릭하면 캡쳐된 종목화면으로 … 종목이 있는
대시보드에 종목들은 모두 적용 … 회사별도 똑같이". 거는 자리가 열한 곳이라
규칙을 `trade/stock_link.py` **한 곳**에 두고 각 표면이 그걸 부른다(#38).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 질의 규칙 | 규칙마다 **그 규칙을 지우면 깨지는** 값 하나씩(#91) | `QueryRuleTests` 12건 |
| ①-b 시장 선언 | 맨 숫자 티커는 **보드가 선언한 시장**에서만 질의가 된다(#34) | `…::test_bare_six_digits_only_on_a_board_that_declares_kr` · `…::test_tokyo_code_width_is_exactly_four` · `BoardRenderTests::test_bare_numeric_tickers_do_not_borrow_another_market` |
| ①-c 이스케이프 | 캡션에서 온 이름·아무 href 나 받는 헬퍼 둘 다(#291) | `…::test_linked_name_escapes_the_href_too` · `DashboardModalStocksTests::test_caption_names_are_escaped_in_both_branches` · `…::test_sllink_escapes_the_href_attribute` |
| ② 보드 E2E | 파서·DB·렌더를 **통째로** 태워 href 값을 본다(#20) | `BoardRenderTests` 8보드 |
| ③ 회사별 탭 | `buildCompaniesView` 를 node 로 **실행**(#253) | `DashboardCompanyViewTests` |
| ④ 모달 칩 | `renderModalCard` 실행 — 이름만 링크, 시세 칩은 그대로 | `DashboardModalStocksTests` |
| ⑤ 레퍼런스북 | `render_page` 통째 + 페이지당 1회 해석(#113) | `ReferenceBookTests` 6건 |
| ⑧ 리졸버 격리 | 스텁을 **경계**(`resolve_codes`)에 걸어 운영자 홈 상태를 안 본다(#30) | `_stub_resolver` 를 쓰는 `BoardRenderTests`·`ReferenceBookTests` |
| ⑥ 프록시 계약 | trade 접두 ↔ NOAH 프록시 접두가 **같은 값**(#38) | `ProxyContractTests` |
| ⑦ 목적지 리졸버 | `/lookup/005930` 이 KR 로 해석되나(KS/KQ 판정 포함) | `tests/test_regression.py::TestTradeStockDeepLink20260922` |

⚠️ **뮤테이션에서 배운 것.** 내가 돌린 8종은 전부 잡혔지만 **그 집합이 너무
좁았다** — 독립 리뷰가 52종을 돌려 **9종 생존**을 실측했다(2026-09-22). 생존분은
전부 "이번에 새로 만든 축에 발화 경로가 없다"(#20·#291)의 변주다: `local=`
배선 넷(제거·뒤집기·게이트 제거 ×2) · `esc(text)`/`esc(href)` 둘(캡션에서 온
이름의 **유일한** 관문인데 벗겨도 전 슈트 통과 — 리뷰가 원시 마크업 주입을
재현했다) · payload 여분 키 · 도쿄 코드 폭 · 재키잉. 열 개를 전부 잡게 고쳤고
**한 번은 치환 0건으로 '건너뜀'** 이었다(앵커 들여쓰기가 틀렸다 — #267 통과·
건너뜀 어느 쪽이든 그 자리를 실제로 쳤는지 먼저 본다).
1차에서 **`stockHref` 6자리 가드**가 살아남았다 — 서버가 이미 합성키를
거르므로 그 가드에 **도달하는 픽스처가 없었다**(#291). `s:"nm:회사"` 를
실은 payload 로 렌더를 태워 발화시켰다(#139 2차 그물).

⚠️ **못 보는 축**(#274): trade 백엔드(:8765)에 직접 붙으면 `/lookup` 이
없다 — 상대 접두를 절대로 바꾸는 것은 NOAH 프록시이고, §Stance 상 그쪽이
정식 접근 경로다. 그리고 링크가 **실제로 열리는지**(NOAH 가 그 티커를
그리는지)는 여기서 안 잰다 — 리졸버 해석까지가 이 슈트의 범위다.

⚠️ 붙이지 않은 자리 **셋**: 레퍼런스북 **미매칭 후보**(아직 그 품목의 상장사로
확정되지 않았고 칩의 주 동작이 '반영' 버튼) · **DART 보강 후보**(셀이 회사명을
콤마로 이은 한 문자열이라 칩으로 쪼개면 그 셀 textContent 를 읽는 CSV 경로가
바뀐다) · 회사별 섹션 **부제**(`stocksSubtitle` — `관련종목: A · B 외 3개` 는
잘린 요약 줄이고, **같은 종목이 바로 아래 모달 칩에서 링크된다**). 셋 다 값이
아니라 **범위** 판단이므로 여기 적어 둔다(#43). 처음에 '둘' 이라 적어 부제를
빠뜨렸다 — 독립 리뷰가 잡았다(#45 총계와 소계가 다른 모집단을 세면 갈라진다).

⚠️ **못 보는 축 하나 더**(#274): `esc(href)`·`_html.escape(href)` 는 오늘의
유일한 생산자(`lookup_href`·`stockHref`)가 퍼센트 인코딩만 내보내므로 **제품
경로에서는 no-op** 이다. 그래서 두 헬퍼(`linked_name`·`slLink`)를 **직접**
태워 계약을 잰다 — 시그니처가 "아무 href 나 받는다" 인 이상 그 계약은 실재하고,
도달 경로 없는 가드는 가드가 아니다(#291·#373 없는 계약을 지어내지도 말 것).

## #400 — 혼합시장 보드의 한국 상장사 (`trade/tests/test_stock_link.py`)

#399 의 `local=` 은 **보드가 선언하는** 축인데, 보드의 나라는 교역 상대국이지
상장 시장이 아니다 — 말레이시아 보드에 `삼성SDI (006400)`, 중국 보드에
`Taiyo Yuden (6976)` 이 온다(2026-09-22 실측 57장 중 3장이 미링크).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ⑨ 거래소 대조 | **이름·코드가 둘 다** 맞을 때만 링크(#25) — 코드 불일치 · 이름 부재 · 마스터 부재 셋을 전부 음성으로 | `BoardRenderTests::test_mixed_market_board_links_a_korean_listing_when_the_exchange_confirms` |
| ⑨-c 오라클 | 신원은 **거래소 목록**이 답한다 — 시세 사다리(`resolve_codes`)를 **일부러 살려 두고** 결과가 마스터 값인지 본다 | `QueryRuleTests::test_the_identity_oracle_is_the_exchange_list_not_the_price_ladder` |
| ⑨-d 이름 정규화 | 표기 변형(`Sk하이닉스`)·내부 `\xa0`·내부 공백을 마스터 키와 맞춘다 | `::test_kr_codes_answers_with_the_caller_s_own_key` |
| ⑨-b 전시장 | 그 케이스를 **네 보드 전수**(my·cn·jp·twr)로 — 배선을 한 곳만 떼는 변형이 나머지를 통과한다(#291 실측 M6·M7 생존) | 같은 테스트의 `boards` 루프 |
| ⑩ 대조 수단 부재 | 일본 코드는 KRX 축에 닿지 않는다 = 확인 축이 KR 전용(#165 지어내지 않는다) | `::test_a_tokyo_code_outside_the_japan_board_stays_plain` |
| ⑪ 조회 범위·횟수 | 맨 6자리 행만, 페이지당 1회 — **네 보드 전수**, 결과가 아니라 **인자**로(#61·#113·#91b) | `::test_mixed_board_asks_the_exchange_only_about_bare_six_digit_rows` |
| ⑫ 리졸버 격리 | `setUp` 이 **클래스 전체**를 경계 격리하는가 — 모양이 아니라 결과로(#313) | `::test_the_resolver_is_boundary_isolated_in_this_class` |

⚠️ **왜 `setUp` 인가.** 보드 넷이 리졸버를 타게 되면서 이 클래스의 **모든**
테스트가 운영자 `~/.trade` 상태를 읽게 됐다 — `_render` 헬퍼에만 넣으면
`render_html` 을 직접 부르는 테스트(일본 보드)가 샌다. 규율로 네 번 진
자리라 클래스 진입점에 둔다(#30·#312·#344·#384·#399·#119).

⚠️ **못 보는 축**(#274): 다른 시장 회사가 KRX 상장사와 이름이 **정확히** 같고
캡션 코드까지 그 KRX 코드와 같으면 통과한다. 6자리·이름 **동시 완전일치**라
관측된 적은 없지만 재지 않았으므로 없다고 하지 않는다(#165). 그리고 이
보드들에 한국 상장사가 얼마나 자주 오는지도 안 쟀다 — 오늘 실측이 3장이다.

⚠️ **오라클을 왜 바꿨나**(독립 리뷰 2026-09-22 Blocking). 처음엔 `kr_codes` 가
`price_provider.resolve_codes` 를 탔다 — 그 사다리 1단 `_DIRECT_CODES` 가
`코오롱플라스틱 → 120110`(코오롱인더스트리) 처럼 상장사를 **모회사로** 바꾼다.
시세엔 옳은 대용이지만 신원엔 양방향으로 틀린다(실측): 캡션이 진짜 코드
138490 이면 대조가 어긋나 멀쩡한 상장사가 평문이 되고, 대용 코드 120110 이면
확인을 통과해 **남의 회사 분석 화면**이 열린다. 지금은 `_load_krx_master` +
`_NAME_ALIASES`(표기 변형이라 신원을 안 바꾼다)만 본다. 운영자 `/map` 은
의도(신원 ↔ 시세대용)를 가를 수 없어 보지 않는다(#165).

⚠️ 테스트 스텁도 **신원 경계**(`_stub_master`)로 옮겼고 시세 사다리는 같이
막는다 — 오라클을 되돌리는 변형이 운영자 홈 상태로 우연히 통과하는 길을
없앤다(#139 2차 그물). 뮤테이션 8종 전부 CAUGHT.

## #401 — 일본 마스터 프로브 + 자식 프로세스 그물 (`trade/tests/test_jp_master_probe.py` 33건 · `tests/test_regression.py` 2건)

#400 이 남긴 자리 — 일본 상장사는 대조할 목록이 없어 평문이다. 어떤 원천이
**코드→이름**을 주는지 **재기만** 하는 프로브를 먼저 심었다(#151·#345 이름을
추측해 배선하면 죽은 경로를 배포한다). 샌드박스는 yahoo·jpx 를 프록시가 막아
외부를 한 줄도 못 재므로 판정은 VM 실행이 한다.

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 제품 술어 | 코드 모양을 **복제하지 않고 import** 한다 — 제품은 `285A` 를 링크하는데 `^\d{4}$` 를 복제하면 못 센다(#35·#38) | `BoardScanTests::test_scan_uses_the_product_code_shape_not_a_narrower_copy` |
| ② 보드 전수 | 표 이름을 박지 않고 `sqlite_master` 에서 고른다(#24) | `::test_scan_reads_every_company_board_not_one_hardcoded_table` |
| ③ 읽기 실패 | 삼키면 '보드에 없다' 와 '못 읽었다' 가 같은 화면이 된다(#82·#143) | `::test_an_unreadable_board_is_reported_not_swallowed` |
| ④ 시장 선언 | `local="XX"` 를 **AST 키워드 인자**로 — 소스 문자열은 독스트링이 대신 만족시킨다(#59b) | `DeclaringMarketTests::test_a_docstring_mentioning_local_does_not_count_as_a_declaration` |
| ⑤ 계수 정직성 | 전부 실패한 단계를 '쟀다' 로 세면 rc 가 거짓 ✅ 가 된다(#54) | `EntrypointTests::test_a_successful_step_four_adds_exactly_one_measured_step` · `::test_rc_is_one_when_nothing_could_be_measured` |
| ⑥ 자른 사실 | 상한에 걸리면 **몇 개를 뺐는지** 말한다(#45) | `::test_the_cap_says_how_many_it_left_out` |
| ⑦ HTTP vs 파싱 | 403 을 '표본 파싱 불가' 라 적으면 원천을 고치러 간다(#82) | `::test_step_six_blames_http_not_parsing_when_the_file_is_not_200` |
| ⑧ 표본 범위 | 후보 페이지마다 하나씩 — 日 한 쪽만 보면 "영문 목록이 있나"가 안 닫힌다(#143·#156) | `::test_step_six_samples_one_file_per_candidate_page` |
| ⑨ 쓰기 경계 | DB **내용·mtime** 불변. SQLite 곁파일(`-shm`/`-wal`)은 예외로 **명시**한다(#264·#286) | `::test_the_probe_leaves_every_db_byte_and_mtime_untouched` |
| ⑩ 자식 그물 | 루트 conftest 의 소켓 패치는 **이 프로세스만** 덮는다 — 자식도 막히는가(리터럴 IP 로) | `tests/test_regression.py::TestNoOutboundHttpInTests20260911::test_child_processes_are_blocked_too` |
| ⑪ 과잉 차단 금지 | 루프백은 통과 · 기존 `sitecustomize` 를 **가리지 않는다** — 실행한 경로를 **값으로**(#25·#91b) | `::test_child_guard_keeps_loopback_and_the_original_sitecustomize` |

⚠️ **왜 값으로 재나(⑪).** 첫 판은 `'sitecustomize' in sys.modules` 로 쟀는데
**우리 것**이 들어와도 참이라 체이닝을 지워도 통과했다(뮤테이션 생존 실측).
자식이 실행한 원본 경로를 `sitecustomize.ORIGINAL` 로 남겨 그 값을 본다.

⚠️ **못 보는 축**(#274): 파이썬이 아닌 자식(curl·git), `-I`/`-E`/`-S` 로 띄운
파이썬, 그리고 `env=` 를 직접 조립해 넘기는 호출은 `PYTHONPATH`·`site` 를 안
읽어 ⑩ 의 그물 밖이다. 그리고 프로브의
지문(`banner`)은 v2 에선 **이 파일 하나**를 쟀다 — v3 에서 ⑦ 의 판정이 제품
모듈로 가며 범위를 같이 넓혔다(아래 JPX 마스터 절 ⑪).

## JPX 상장 마스터 — 혼합 보드의 도쿄 상장사 딥링크 (`trade/tests/test_jpx_master.py` 37건 · `test_stock_link.py` +9 · `test_jp_master_probe.py` +9)

`jp_master_probe` v2 VM 실측(2026-09-23)으로 원천을 골랐다: JPX 영문
「その他統計資料」 `data_e.xlsx`(`Local Code` · `Name (English)` · `Effective Date`).
④ yfinance 도 12/12 이름을 줬지만 종목마다 1콜이고 트레이드 venv 엔 없다(②).
`build_jpx_codes --if-stale` 가 **제 유닛**(`trade-bot-jpx-codes`, 6시간 점검)에서
주 1회 `~/.trade/jpx_codes.json` 을 만들고, 혼합 보드(cns·cni·tws·mys)는
`stock_link.jp_names` → `jp_confirms` 로 **코드와 이름이 둘 다** 맞을 때만
`<코드>.T` 링크를 건다(4자리는 대만 코드와 모양이 같다, #34).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① Excel 모양 | 공유 문자열 · rels 가 가리키는 **비기본 시트**(옆에 미끼 `sheet1.xml`) | `ParserTests::test_the_sheet_is_the_one_the_workbook_points_at` |
| ② 후리가나 | 일본 Excel 의 `<rPh>` 읽기가 이름에 안 붙는다 — 공유·인라인 **둘 다** | `::test_furigana_runs_do_not_leak_into_the_name` · `::test_inline_strings_written_as_runs_are_read` |
| ③ 셀 모양 | 리치 텍스트 런 · openpyxl 의 inlineStr · 깨진 XML 은 이름 붙은 `FetchError`(#82) | `::test_rich_text_runs_are_joined` · `::test_inline_strings_as_openpyxl_writes_them` · `::test_broken_sheet_xml_is_a_named_fetch_error` |
| ④ 기준일 | **가장 늦은** `Effective Date` — 전 행 같은 날 픽스처로는 안 잡혔다(M8 생존, #91c) | `::test_effective_date_is_the_latest_row_not_the_first` |
| ⑤ 파일 고르기 | 변경분(`jyoujyou(updated)_e.xlsx`)을 전 목록으로 집지 않는다(#45) | `FetchTests::test_the_updates_file_is_not_mistaken_for_the_full_list` |
| ⑥ 폴백 고지 | 페이지에서 못 찾으면 실측 주소로 가되 **그렇다고 말한다**(#42a) | `::test_missing_link_falls_back_to_the_measured_url_and_says_so` |
| ⑦ 시간 예산 | 소켓 타임아웃은 끊긴 시간만 잰다 — 흘러오는 몸통을 **총 30초**에서 끊는다 · 크기 상한 · HTTP 오류는 상태로(#116) | `HttpBudgetTests::*` |
| ⑧ 빌더 안전 | 하한(#280) · 실패 뒤 6시간 쉼 후 재시도(#303·#178) · **시도 전** '시도 중' 기록(강제 종료도 쉬게) · 쓰기 실패는 실제 사유로 덮는다 · **쓸 수 없는** 신선 파일은 재빌드(#25) · 어떤 예외든 rc 0 + 경고(#116·#12) | `BuilderTests::*` |
| ⑨ 배선 | **제 유닛·타이머**(refresh 와 시간 예산을 안 나눈다, #116) · 설치 직후 첫 실행(`OnActiveSec`) · 트레이드 venv · **표준 라이브러리만** import(전수, #24) | `DeployWiringTests::*` |
| ⑩ 확인 규칙 | 법인형태를 떼고 **같거나** 실측한 `holdings` 꼬리만 더 붙음 — `Tokyo`≠`Tokyo Electron`, `SoftBank Corp.`≠`SoftBank Group`, 캡션이 더 김, 글자 단위, 재지 않은 약어 | `QueryRuleTests::test_jp_confirms_prefix_rule` · `::test_name_tokens_keep_group_and_holdings` |
| ⑪ 키·가시성 | 키는 정규화한 코드(`'6976 '` 행) · 목록을 못 불러 평문이 되면 **경고 한 번** — KRX 형제도(#12·#38) | `::test_jp_names_keys_are_normalized_codes` · `::test_a_failing_identity_list_is_logged_not_silent` · `::test_a_failing_krx_list_is_logged_too` |
| ⑫ 네 보드 E2E | cns·cni·tws·mys — 확인/이름 불일치/코드 불일치/목록 없음, 그리고 페이지당 1회 | `BoardRenderTests::test_mixed_market_board_links_a_tokyo_listing_when_the_jpx_list_confirms` · `::test_the_jpx_list_is_read_once_per_page` |
| ⑬ TWSE 월매출 제외 | 발행사가 전부 대만 상장이라 도쿄 확인은 **틀린 링크만** 낼 수 있다 — 목록을 열지도 않는다(데이터 소스 사유) | `::test_the_twse_revenue_board_never_asks_the_jpx_list` |
| ⑭ 프로브 ⑦ | 마스터로 **제품 규칙 그대로**(#35) · 대조 0건은 잰 것이 아니다(#54) · 못 읽은 파일을 '안 돌았다' 로 말하지 않는다 · 지문이 판정 모듈까지 덮는다(#364) | `StepSevenTests::*` · `FingerprintScopeTests::*` |

⚠️ **픽스처는 재구성이다**(#155·#334): Excel 모양 xlsx 는 OOXML 명세로 만든
것이고, 목록 값의 대소문자·법인형태(`TAIYO YUDEN CO.,LTD.`)는 JPX 실측 행
(`KYOKUYO CO.,LTD.`)의 모양을 따랐다. 실물 첫 대조는 VM 빌드 로그(`버림 N`)와
프로브 ⑦ 이 한다.
⚠️ **못 보는 축**(#274): 다른 거래소 회사가 **같은 4자리**를 쓰고 법인형태를 뗀
영문명까지 도쿄 회사와 같으면 통과한다(관측 없음, 재지 않음). JPX 는 월 1회
갱신이라 그 사이 신규 상장은 평문이다(틀린 링크가 아니라 없는 링크).

## #403 — 파서가 생기기 전에 버려진 캡션의 자동 회수 (`trade/tests/test_badonion_sources.py::TestSyncRecoveryWindow20260924` 31건 · `trade/tests/test_backfill_badonion_sync.py` 71건 — 그중 #404 몫 7건 · 2026-09-24 5차 리뷰 뒤 실측)

사용자가 2026-09-16 채널에서 본 한국 수입 회사별 캡션(텔레칩스)이 09-24 까지
보드에 없었다. 관련성 필터 = 파서라 파서 배포 전 캡션은 리스너가 버리고, 6시간
동기화는 최근 3일만 본다. 나는 09-22 에 그걸 '원천 미게시' 로 오판했다 — dry-run
이 **파서는 받는데 아직 inbox 에 없는 유닛**을 계수로만 셌기 때문이다. 원인 갈래
(포워드된 적 없음 / 파서가 실물을 거부 / 창·게시일)는 배포 뒤
`--dry-run --since 2026-08-20 --find 텔레칩스` 가 **어디로** 갔는지 말하고, irrelevant
면 그 출력이 찍는 `diagnose_badonion --since … --grep …` 이 **왜** 버려졌는지 찍는다
(2차 리뷰 — `--find` 하나로 원인이 갈린다는 첫 판 서술은 과대 주장이었다).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 창 판정 | 기록 없음·지문 변경 → 40일(`record=True`) · 같은 지문 → 3일 · 회수 한 번 뒤 기본 창으로 수렴(#171) · 3/40/3회/100유닛은 **고른 값**이라 리터럴로(#66) | `TestSyncRecoveryWindow20260924::test_first_run_without_a_record_scans_the_recovery_window` · `::test_recovery_happens_once_then_settles_to_the_default` · `::test_default_and_recovery_windows_are_the_chosen_contract` |
| ② 사유 | 기록이 없으면 '바뀌었다' 를 주장하지 않는다(#165) · 못 읽은 기록은 갈래를 이름으로(#82) · 지문을 못 재면 판정 불가라고 말하고 기록하지 않는다(#54) | `::test_an_unreadable_record_is_named_and_not_trusted` · `::test_an_unknown_fingerprint_is_said_and_never_recorded` |
| ③ 명시 창 | `--since`·`--lookback-days`(0 포함)·`--to` 는 그 창 그대로 · 둘 다 주면 **`--since` 가 이긴다**(백필과 같은 우선순위, 리뷰 M2) · 지금까지 40일을 **덮을 때만** 성공 뒤 기록(#171) · 안 덮는 창엔 지문도 안 잰다 · 시계는 주입(#249) | `::test_an_explicit_narrow_window_neither_recovers_nor_records` · `::test_an_explicit_window_that_covers_the_recovery_window_records` · `::test_since_wins_over_lookback_like_the_backfill_does` · `::test_the_plan_computes_the_real_fingerprint_when_not_given` |
| ④ 지문 범위 | **전이 폐포**(어댑터 → 엔진 → 두 단계 아래, #365) · 무관 모듈은 안 흔든다 · 상대 import · `import a.b` 문 · 패키지 `__init__.py` 코드 · 뿌리 누락·문법 오류면 "" (#364) · 뿌리는 레지스트리에서 파생(#24) | `::test_the_fingerprint_follows_imports_transitively` · `::test_relative_imports_are_followed` · `::test_plain_import_statements_are_followed` · `::test_package_init_code_is_covered` · `::test_a_missing_or_broken_module_means_no_fingerprint` · `::test_roots_are_derived_from_the_registry` |
| ⑤ 설명만 고친 배포 | 주석·독스트링 변경은 지문을 안 바꾼다 — 코드 변경은 바꾼다(#266) | `::test_the_fingerprint_ignores_comments_and_docstrings` |
| ⑥ 포워드할 유닛 줄 | 어느 소스가 받는지 레지스트리 순서로 · 앨범의 캡션 없는 멤버 · 무관 캡션은 `(없음)` | `::test_unit_labels_names_every_source_in_registry_order` |
| ⑦ 일부 실패한 회수 | 포워드가 실패한 회수는 기록 안 함(일시 장애도 같은 경로, 리뷰 M1) · 옛 지문 보존 → 다음 틱이 다시 40일 · 사유가 **실제 횟수**를 말한다(2차 S17) · 같은 지문 3회째 실행에도 남으면 기록 — 실행별로 세므로 '영구 실패' 로 단정하지 않고 '재시도 상한' 이라 말하며, 호출부가 준 **못 보낸 id·수동 명령**을 싣는다(4차 리뷰 M2 — 옛 판이 가리킨 로그 줄 둘은 FloodWait 중단엔 없었다, #292·#371) · 재시도가 남은 실행엔 싣지 않는다 · 지문이 바뀌면 횟수 새로 · **지문이 이미 기록돼 있어도** 이 지문의 재시도 표식이 있으면 40일을 다시 연다 — 다른 지문의 표식으론 안 연다(2차 P2) · 옛 지문의 표식만 남으면 그 갈래를 이름으로(#82) · 횟수가 깨져도(`"x"`·null·Infinity·음수·배열) 안 던지고 **재시도 사실은 버리지 않는다**(2차 P6) · 교체가 실패하면 옛 기록이 한 바이트도 안 바뀐다 — 원자성을 **잰다**(2차 S11; 옛 테스트는 .tmp 잔존만 봤다) · 다른 모듈 안 빌림(리뷰 L3) · **3차 리뷰**: 포워드 **도중 중단된** 회수도 횟수에 센다 — 사유는 '포워드 실패 N건' 이 아니라 중단 사유(FloodWait 중단은 실패 0건일 수 있다, #292) → **4차 리뷰 M2**: 긴 FloodWait 중단은 표식만 남기고 안 센다(저장 횟수가 상한 이상이어도 포기 안 함 · 그 뒤 센 실행은 이어서 센다, #279) → **5차 리뷰 B**: 4차 반영분이 같이 안 세던 '진전 있는 중단' 을 센다 — 포워드된 사본이 inbox 에 안 닿으면 같은 유닛을 매 틱 다시 보내며 영영 안 셌다(그 인자를 지웠다 — 되살리면 TypeError) · 표식에 중단 여부를 싣고 사유가 '중단돼'·'실패가 남아'·옛 표식(단정 안 함)과 센 적 없는 0·못 읽은 횟수를 가른다 — 셈과 사유가 **같은 판정**(`_count_readable`)으로 읽는다(5차 리뷰 — 옛 판은 `"0"` 을 셈에선 0 으로, 사유에선 '못 읽었다' 로 읽었다 · bool 은 못 읽은 값) · 기록 모양이 다르면(`relevance_fp` 가 문자열 아님 · `retry` 가 dict 아님) '형식이 다르다' 고 말하고 옛 지문·재시도로 읽지 않는다(S11·S29) | `::test_an_aborted_run_is_counted_and_named_by_its_reason` · `::test_a_foreign_shaped_record_is_named_not_quoted` · `::test_a_partly_failed_recovery_is_retried_a_bounded_number_of_times` · `::test_retries_count_per_fingerprint_and_a_clean_run_records_at_once` · `::test_a_retry_marker_reopens_the_window_even_when_the_fp_is_recorded` · `::test_a_stale_marker_of_another_fingerprint_is_named` · `::test_a_broken_retry_count_neither_crashes_nor_drops_the_retry` · `::test_a_failed_replace_leaves_the_old_state_intact` · `::test_the_state_write_is_atomic_and_needs_no_other_module` · `::test_the_give_up_message_carries_what_was_not_forwarded` · `::test_which_runs_count_toward_the_retry_limit` · `::test_an_uncounted_abort_keeps_the_retry_but_not_the_count` · `::test_the_retry_reason_says_how_the_last_run_ended` |
| ⑧ 자동 회수 상한 | 자동 회수만 100유닛에서 멈추고 알린다 — 명시 창은 상한 밖(리뷰 L5) | `::test_only_the_automatic_recovery_is_marked_as_one` |
| ⑨ `main()` E2E | 가짜 telethon(monkeypatch)·사설 모듈로 **실제 `main()`** 을 태운다: 첫 실행 40일 회수 + 기록(+ 기록했다는 로그) → 둘째 실행 3일(20일 전 캡션에 안 닿음) · dry-run·`--show-irrelevant` 단독은 **기록·포워드 없음**(#264) · 시작 실패·후보 상한(rc 2)은 기록 안 함 + 알림이 멈추는 법을 적음 · 일부 포워드 실패 → 재시도 2회 뒤 3회째 기록 · 좁은 명시 창(`--since`·`--to`·둘 다)·`--lookback-days 0` 은 기록 안 함 · `--since ""` 는 기본 창(리뷰 L1) · 40일을 덮는 명시 창은 기록 · 자동 회수 상한과 명시 창(알림의 명시 실행 안내가 회수 창 일수인지, 2차 B32) · 기록 쓰기 실패는 경고 + rc 0 — OSError 밖의 예외도(2차 P6) — 판정 뒤로 미룬 알림은 그래도 가고 판정을 못 붙인 이유를 적는다(4차 리뷰 뒤 셀프리뷰, #43) · 지문이 바뀌면 다시 회수 · **2차 리뷰**: 빈 `--find`(`""`·공백)는 접속 전에 거부(P1 — 옛 판은 실제 동기화) · 지문이 기록된 뒤 명시 회수가 일부 실패하면 다음 자동 실행이 40일을 다시 훑는다(P2) · dry-run 은 후보 상한·회수 상한에서 멈추지도 알리지도 않고, 시작 실패도 폰에 알리지 않는다(P11·B5b — 타이머 장애로 읽힌다, #82) · dry-run 로그는 '기록한다' 를 약속하지 않는다(P12) · 상한은 '넘으면'(B4) · 사람이 명시한 창의 중단 알림엔 '자동 회수' 안내가 없다(B6) · `--to ""` 는 기본 창(B18) · 회수 상한 알림의 라벨은 HTML 이스케이프(B23, 실수 #7) · dry-run 류는 라이브 세션의 **복사본**으로 접속하고 끝나면 지운다 — 원본이 없거나 복사가 실패하면 라이브 경로 + 경고(가짜 클라이언트가 세션 파일을 실제로 건드려 반대 증거까지 잰다, #25) · **3차 리뷰**: 첫 회수가 흩어진 실패로 rc 0 → 남은 실패만 연달아 와도 상한 안에 기록된다(Medium 재현 — 옛 판은 6시간마다 40일 재스캔 + 중단 알림이 끝없이 · 5차 리뷰부터 자동 회수는 연속 실패로 끊지 않고 rc 0 으로 끝까지 시도한다) · 후보 상한 알림은 도중 중단 중 **긴 FloodWait 만** 안 센다고 적는다(4·5차) · 인증이 풀린 **복사본에는 로그인하지 않는다**(L2 — 라이브는 로그인 흐름) · 원본이 없으면 경로·cwd 를 경고(L3) · 복사본 접속 줄은 어느 파일인지(B06) · 형제 `diagnose_badonion` 도 **같은 함수**로 복사하고, 인증이 풀린 복사본에선 처방과 함께 rc 1 (트레이스백 아님, #38) · 상한과 **같은** 수는 넘은 게 아니다(B16·B43) · dry-run 단서는 기록을 약속할 때만(B34·B35) · `--find` 단독도 dry-run 강제를 말한다(B37) · dry-run 시작 실패의 **판정 줄**이 갈래를 말한다 — 트레이스백 줄이 아니라(B44) · **4차 리뷰**: FloodWait 중단을 몇 번 해도 회수를 포기하지 않고 풀리면 전부 보낸다(R2) · 실행마다 조금씩 나아가다 FloodWait 에 끊기는 회수는 포기하지 않는다(R8 — FloodWait 을 안 세는 규약이 지킨다) · 사람이 연 창이 연속 실패로 끊긴 3회째에 포기하면 그 줄이 '중단' 이라 말하고 **실패한** id 와 **시도 못 한** id 를 갈라(5차 리뷰 C) 명령과 함께 싣고, 그 명령을 **dry-run 으로** 돌려 그 캡션들이 포워드 후보에 드는지 잰다 — 날짜를 서로 다른 날로 벌려 가장 이른 날인지 잰다(S04·#91c) · 포기는 경고 · 알림은 실행당 **한 번**, 판정(N/3·세지 않음·포기)을 붙여서(L6) · 자동 회수의 중단 알림엔 '같은 명령으로 재실행' 이 없다(판정이 말한다) · FloodWait 중단 알림엔 'systemic' 해명이 없다(5차 리뷰에서 그 해명 자체를 지웠다 — 자동 회수가 연속 실패로 끊지 않는다) · 지문이 기록된 뒤의 명시 회수가 FloodWait 에 끊기면 다음 자동 동기화가 이어 받는다 · `run()` → `main()` stats 계약(FloodWait 은 그 유닛부터, 연속 실패는 다음 유닛부터 못 보낸 것) · 두 목록은 각각 20개에서 자르고 나머지 수를 말한다 — 딱 20개면 '외 0건' 을 안 붙인다(#45) · 처방 예외의 시작 실패는 처방을 한 번만 싣고(L4) 트레이스백 없이 저널에 한 줄 남긴다 — 예상 못 한 예외는 여전히 트레이스백(L5·반대 증거) · 형제 진단은 원본이 없으면 라이브 경로로 로그인한다(D02) · **5차 리뷰**: 자동 회수는 연속 실패로 끊지 않는다 — 실패 덩어리 뒤의 유닛도 같은 실행에서 시도하고, 포기할 때는 실제로 실패한 것만 적는다(C 재현) · 포워드된 사본이 inbox 에 끝내 안 닿아도 재시도 상한 안에서 멈추고 다시 보낸 횟수도 유계다(B 재현) · 사람이 연 창은 여전히 연속 실패에서 끊는다(반대 증거) · `finally` 의 disconnect 가 던져도 rc 와 미룬 알림을 지킨다(E) · 기록 쓰기가 실패하면 다음 창을 단정하지 않는다 — 로그·알림이 같은 두 갈래 문장이다(F) · 완료 알림은 스킵된 유닛을 '영구실패' 로 단정하지 않는다 — 재시도 판정(N/3)이 같은 통에 붙는다(배포전 셀프리뷰 — L6 이 판정을 붙인 뒤 한 통이 두 말을 했다, #165) | `trade/tests/test_backfill_badonion_sync.py::*` |
| ⑩ `--find` | 캡션 **전문**에서 찾아 to-forward·irrelevant·already-in-inbox·ignored 네 갈래를 한 번에 — 갈래마다 **어느 글**이 실렸는지까지(2차 B8·B9) · 한 유닛은 한 갈래에 한 번(B16) · 찾았으면 '없다' 줄이 없다(B30) · 없으면 창과 **훑은 수**(후보 수가 아니다)를 정확히(B15 — 무시 목록 글·`--to` 뒤 글로 픽스처를 세게, #91c) · 머리 160자 너머도(B10) · 원문 `raw_text` 와 `text` 둘 다 대소문자 무시(형제 `diagnose_badonion._matches` 와 같은 규약) · 앨범 모든 멤버(B27) · 어느 갈래에서 찾았든 '없다' 고 안 한다(B29) · irrelevant 면 형제 진단 명령을 찍되 **가장 이른 irrelevant 유닛의 날짜**부터(그 도구는 `--since` 부터 1000개만 훑는다 — 3차 B22), 셸 인용, 플래그는 형제 argparse 에 실제로 있는 것만(AST, #371) · **3차 리뷰**: 안내 명령을 형제 `main()` 으로 **그대로 실행**해 그 캡션을 찍는지 잰다 — `cd ~/stock-trade &&` · 운영 유닛 ExecStart 와 같은 venv · 대시로 시작하는 검색어는 `--grep=-KY` 로 붙인다(L1·B45~47) · 무시 목록·inbox 에서만 찾았으면 안내 없음(B23) · 링크 엔티티처럼 `text` 에만 있는 글자(B09) · 두 텍스트의 이음매에서 생긴 낱말은 안 찾는다(B10) · 갈래별 수를 한 줄로(L4) · 읽기 전용 | `test_backfill_badonion_sync.py::test_find_names_where_each_caption_went` · `::test_find_reads_past_the_head_and_both_texts_case_insensitively` · `::test_find_searches_every_album_member` · `::test_a_hit_in_any_branch_is_not_reported_as_missing` · `::test_a_miss_names_the_exact_window_and_how_many_it_read` · `::test_find_points_at_the_sibling_that_says_why_a_caption_was_dropped` · `::test_the_pointer_is_a_command_that_runs_and_reaches_the_caption` · `::test_the_pointer_starts_at_the_earliest_dropped_caption` · `::test_no_pointer_when_the_filter_dropped_nothing` · `::test_find_sees_what_only_the_markdown_text_carries` · `::test_find_does_not_match_across_the_seam_of_the_two_texts` |

⚠️ **못 보는 축**(#274): 지문은 **디스크의 코드**를 잰다 — 실제로 캡션을 받는지(파서
의미)는 안 잰다. 그래서 필터가 쓰는 모듈 **어디든**(렌더·시세 조회 포함) 바뀐 배포도
40일 스캔을 한 번 부른다(값싸다: 채널 히스토리 몇 쪽, 포워드는 새로 걸리는 것만).
파이썬 마이너 버전을 올려도 `ast.dump` 모양이 바뀌어 회수가 한 번 더 돈다(무해).
반대로 파서가 **같은 코드로 다른 캡션을 받게 되는 경우**(외부 데이터 변화)는 지문이
안 바뀌어 회수가 안 걸린다 — 그때는 `--since` 로 사람이 연다. E2E 의 kri 캡션은
스크린샷 재구성이다(#155). 포워드 실패의 일시/영구는 갈라 재지 않는다 — 횟수로
묶는다(예외 이름 열거는 새 오류를 못 잡는다, #24). 세션 복사본 테스트의 가짜
클라이언트는 세션 파일을 **덧쓰는 흉내**만 낸다(로그인도 파일 쓰기로 흉내) — 진짜
SQLite 잠금 경합은 재지 않는다. 형제 `diagnose_badonion` 은 같은 가짜 telethon 위에서
실제로 태운다(3차 리뷰 — 옛 판은 플래그 **존재**만 AST 로 쟀다). 다만 진짜 telethon 의
`iter_messages`(limit·reverse) 동작은 가짜의 흉내다. 수렴은 끝낸 실행과 FloodWait 이
아닌 중단을 센다 — 긴 FloodWait 중단만 세지 않는다. 프로세스가 죽은 실행(systemd 타임아웃
kill — 유닛 `TimeoutStartSec=600` 과 FloodWait 상한 600초가 겹친다, 4차 리뷰 L8 미반영)은
`finish_recovery` 에 닿지 못해 셈도 표식도 남기지 않는다 — 기록 전의 자동 회수면 다음 틱이
다시 넓게 훑지만, 지문이 이미 기록된 뒤의 명시 회수가 죽으면 다음 자동 동기화는 기본 창으로
돌아간다. FloodWait 중단이 끝없이 반복되지 않는다는 근거(제한은 풀린다)는 재지 않은
가정이다 — 텔레그램이 매 틱 긴 제한을 거는 동안엔 6시간마다 중단 알림이 온다(조용하지는
않다). 자동 회수는 연속 실패로 끊지 않으므로 체계적 장애(쓰기 권한 등)에서도 한 실행이
최대 100유닛을 전부 시도한다 — 그 비용은 유닛 상한이 묶고, 장애는 '전체 실패' 완료 알림과
재시도 상한이 잡는다.

## #404 — 세션 형식 가드: 옛 telethon 이 새 형식 세션을 생성자에서 조용히 못 열었다 (`trade/tests/test_tg_session_format.py` 35건 · `trade/tests/test_backfill_badonion_sync.py` 7건 · 2026-09-24 5차 리뷰 뒤 실측)

VM 실측(2026-09-24): 운영 `.backfill-venv`(telethon 1.36.0 = 세션 DB v7)가 v8 로 올라간
`.badonion-session` 을 열다 `TelegramClient(...)` **생성자**에서 `too many values to unpack
(expected 5)` 로 죽었다 — 생성이 알림 `try` 밖이라 6시간 동기화는 트레이스백으로만 끝났다.
더 새 telethon(상한 없는 `.venv`)이 한 번 연 것이 유력한 경로다(그 판은 아직 안 쟀다).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 형식 측정 | 세션 DB 버전을 **읽기 전용**(`mode=ro`)으로 · 없는 파일·쓰레기·빈 파일·**version 행이 없는 표**는 None · **핫 저널을 되돌리지 않는다** — 자식 프로세스로 쓰다 죽은 트랜잭션을 만들어 파일 바이트·`-journal` 이 그대로인지 잰다(4차 리뷰 T05·T06) · `.session` 이름의 **다른** SQLite 는 version 칸이 정수가 아니어도 던지지 않고 못 잰 사유를 말한다(5차 리뷰 T05) · 설치된 telethon 판·형식은 가정이 아니라 측정 | `test_tg_session_format.py::test_the_session_version_is_read_read_only` · `::test_a_missing_or_foreign_file_is_not_a_version` · `::test_an_empty_version_table_is_not_a_version` · `::test_a_foreign_version_table_is_not_a_version` · `::test_measuring_never_rolls_back_a_hot_journal` · `::test_the_installed_telethon_format_is_measured_not_assumed` |
| ② 고정판 | 핀의 단일 출처는 운영 venv 를 까는 `trade/scripts/requirements.txt` · 실제 핀이 VM 에서 본 형식(v8)을 읽는다 | `::test_the_pin_is_read_from_the_file_the_production_venv_installs` · `::test_the_pin_reads_the_session_format_we_found_on_the_vm` |
| ③ 판정 | 파일 형식 > 라이브러리 형식이면 막고 **깔 판**을 말한다 — 운영 venv 면 그 venv 에 고정판을(L1 — 이미 있는 사람에게 '그 venv 로 돌려라' 는 처방이 아니다), 아니면 운영 venv 를, 고정판도 못 읽으면 핀을 올리라고, 핀을 못 읽으면 `None` 을 고정판처럼 적지 않는다(L2) · **운영** 세션을 올리는 것은 **운영 venv 가 고정판일 때만** — 운영 venv 가 아니면 판이 같아도 막는다(4차 리뷰 M1 · 판별은 판이 아니라 인터프리터 경로) · 복사본은 허용 · 같거나 못 재면 막지 않되 **경고로 말한다**(L3 — 파일 못 읽음·telethon 판 못 잼·운영 venv 인데 핀 없음) · 파일이 없으면(첫 인증) 조용히(#54 는 '말하라' 이지 '멈추라' 가 아니다) · **5차 리뷰 A**: 단 **못 잰 운영 세션**(핫 저널 등)을 **운영 venv 가 아닌** 인터프리터가 열려 하면 막는다 — 운영 venv·복사본·파일 없음은 그대로 통과 · **5차 리뷰 D**: 판은 숫자로 비교한다 — 핀 `1.45` 와 설치판 `1.45.0` 은 같은 판이라 운영 venv 는 올리기를 허용하고, 처방 두 갈래(운영·비운영) 다 이미 깔린 판을 다시 깔라고 하지 않는다 · 숫자가 아닌 판(rc 등)은 문자열 그대로(모르면 다르다) · 운영 venv 판별은 경로의 디렉터리 이름 **전체**(`.backfill-venv-old` 는 운영 아님 · 역슬래시 경로도, T10·T11) | `::test_a_session_newer_than_this_telethon_names_the_fix` · `::test_only_the_production_venv_on_the_pin_may_upgrade_a_live_session` · `::test_matching_or_unmeasurable_formats_never_block_but_say_so` · `::test_an_unmeasurable_live_session_is_closed_to_foreign_interpreters` · `::test_releases_compare_by_number_not_by_spelling` · `::test_a_short_pin_lets_the_production_venv_upgrade` · `::test_the_production_venv_is_recognised_by_its_path_segment` |
| ④ 생성 | 막히면 클라이언트를 **만들지 않는다** · 통과면 인자를 그대로 넘긴다 · 알림 사유는 처방 문장 그대로 · 시작 실패 알림 본문은 처방을 **한 번만**(200자에서 잘라 한 번 더 싣지 않는다, 4차 리뷰 L4) | `::test_the_guard_refuses_before_building_and_passes_through_otherwise` · `::test_the_failure_note_carries_the_prescription` · `::test_the_failure_text_says_a_prescription_once` |
| ⑤ 메커니즘 | 실물 telethon 이 v7 파일을 열면 제 형식으로 올린다 — 가정이 아니라 사고의 메커니즘 · 실물 telethon 을 **고정판으로 둔 채** 운영 venv 가 아닌 경로에서 v7 운영 세션을 가드로 열면 안 올라간다(4차 리뷰 M1 재현 — 옛 판은 판이 같아 통과했다) · **핫 저널이 남은** v7 운영 세션을 비운영 venv 가 가드로 열면 못 잰 채 막히고 파일·저널이 그대로다(5차 리뷰 A 재현 — 옛 판은 '못 잼 → 통과' 라 v8 로 올렸다) | `::test_a_newer_telethon_upgrades_an_old_session_on_open` · `::test_a_same_version_foreign_venv_cannot_upgrade_the_live_session_for_real` · `::test_a_hot_journal_does_not_let_a_foreign_venv_upgrade_the_session_for_real` |
| ⑥ 배선 전수 | 트레이드 운영 코드에서 `TelegramClient` 의 **어떤 별칭·속성 참조도** `guarded_client` 첫 인자 밖으로 안 샌다(디렉터리 전수, #24 — 우회 픽스처로 검사 자체를 잰다, 4차 리뷰) · 가드가 말하는 venv 가 telethon 유닛 ExecStart 의 venv | `::test_the_client_guard_scan_catches_every_evasion` · `::test_no_production_code_builds_a_telegram_client_directly` · `::test_every_telethon_unit_runs_the_venv_the_guard_names` |
| ⑦ 리스너 | 형식 불일치는 알리고 **EX_CONFIG(78)** 로 끝나 재시작 루프를 막는다(두 리스너) · `--auth` 도 가드를 거친다 · 운영 세션을 `live=True` 로 잰다(스파이 — 옛 스텁은 그 인자를 무시해 복사본처럼 재도 통과했다, 4차 리뷰 T19·L03·N02) | `::test_a_listener_with_an_unreadable_session_stops_cleanly_and_says_why` · `::test_interactive_auth_also_goes_through_the_guard` · `::test_a_listener_measures_its_session_as_live` |
| ⑧ 동기화 | 생성자가 던져도 알림 `try` 안(시작 실패 알림 + rc 1, 기록 없음) · dry-run 은 알리지 않는다 · 가드는 **생성 전에** 돈다 · 라이브 경로만 live 로 잰다(형제 진단도, BeOn 동기화·dedup 진단은 운영 세션을 live 로 — E03·Q02) · 처방 예외는 처방을 한 번만 싣고 트레이스백 없이 저널에 한 줄(L4·L5) · BeOn 동기화도 같은 규약 · 동기화 E2E 픽스처는 인터프리터를 **운영 venv 경로**로 둔다 — 가짜 세션 파일은 형식을 못 재므로, 비운영이면 A 가 그 파일을 막는다(픽스처는 운영이 실제로 도는 모양대로, #155) | `test_backfill_badonion_sync.py::test_a_session_this_telethon_cannot_open_pages_instead_of_dying_silently · `::test_a_dry_run_that_cannot_open_the_session_logs_but_does_not_page` · `::test_the_format_guard_runs_before_the_client_is_built` · `::test_only_the_live_session_is_guarded_as_live` · `::test_the_sibling_diagnose_tool_guards_the_live_path_as_live` · `::test_a_prescribed_startup_failure_says_the_prescription_once` · `::test_a_prescribed_startup_failure_logs_no_traceback` · `test_tg_session_format.py::test_the_beon_backfill_pages_on_a_session_it_cannot_open` · `::test_the_beon_backfill_logs_a_prescribed_failure_without_a_traceback` · `::test_the_beon_backfill_measures_its_session_as_live` · `::test_the_dedup_diagnostic_measures_the_live_session_as_live` |
| ⑨ 복사본 로그인 | 복사본(dry-run·진단)은 인증이 풀렸으면 로그인하지 않고 멈춘다 — 라이브는 로그인 흐름을 탄다(처음 인증하는 유일한 길) | `test_tg_session_format.py::test_a_session_copy_is_never_logged_into` |

⚠️ **못 보는 축**(#274): 핀이 **운영 venv 에 깔렸는지**는 안 잰다 — 자동 배포
(`deploy/trade-auto-update.sh`)는 pip 를 돌리지 않는다. 그 사이엔 가드가 설치 명령과
함께 알린다. ⑤ 의 세 테스트만 실물 telethon 이고(없는 환경이면 skip) 나머지는 가짜
모듈 위라 진짜 생성자·잠금은 안 잰다. 운영 venv 판별은 **경로 이름**(`/.backfill-venv/`)
이다 — 같은 이름의 다른 디렉터리에서 돌리면 운영으로 본다(그 이름은 운영 유닛만
쓴다는 가정 · ⑥ 이 유닛 ExecStart 와 대조한다). 파이썬이 아닌 자식이나 다른 라이브러리의 상태 파일은 이 가드 밖이다.
형식 측정의 `mode=ro` 는 **핫 저널**(쓰다 죽은 트랜잭션의 `-journal`)이 있을 때만
관측 차이가 난다 — 읽기·쓰기 연결이면 측정이 그 저널을 되돌려 파일을 바꾼다. 3차 라운드엔
회귀가 그 상태를 만들지 않아 `mode=ro` 를 지우는 뮤테이션이 생존했고, 나는 그걸 '동등' 으로
분류했다 — 재지 않은 분류였다(4차 리뷰). 이제 ① 이 핫 저널을 만들어 잰다. **A 는 비운영 인터프리터에서만 막는다** — 운영 venv 는 못 잰
운영 세션을 여전히 경고하고 연다(운영을 멈추지 않는다). 그래서 운영 venv 의 telethon 이
고정판이 **아니면서** 핫 저널이 남은 세션을 여는 조합은 이 가드가 못 막는다 — 그 조합은
재지 않았다(핀이 깔렸는지 안 재는 것과 같은 축이다).

## #406 — '수신 0건' 은 두 갈래다: 봇이 못 받았나, 받고 버렸나 (`trade/tests/test_bot_health.py` 122건 · `trade/tests/test_bot_drop_log.py` 17건 · 2026-09-25)

VM 실측(2026-09-25): 40일 회수가 `forwarded 27 of 27` 인데 32분 뒤에도 inbox 는 그대로였고
trade-bot 저널의 `ingested` 는 0 이었다. 채널·출처 게이트가 로그 없이 버려 그 0 은 '못
받았다' 와 '받고 버렸다' 를 가르지 못했다(원인은 아직 안 쟀다 — 운영 봇이 옛 판이다).
그 뒤 독립 리뷰(a814a77..6f407d9)가 진단 자체의 결함 넷(H1·H2·M1·M2)과 비밀값 넷(M3a~d)·
알림 키(M4)·문서(M5)·Low 여섯·생존 뮤테이션 13종을 짚었다 — 아래 ⑪~⑯ 이 그 반영이다.
2차 독립 리뷰(6b005d9..e5b528a)는 넷을 더 짚었다 — 수 대조가 무관한 수신·버림에 덮여 예외로
놓친 글을 ✅ 로 보냈고(H1), 재시작을 걸친 백필의 옛 프로세스 몫을 버렸고(M2), 매시간 표식이
창을 따라 바뀌어 같은 포워드를 두 번씩 알렸고(L4), 수신용으로 넓힌 저널로 판정까지 했다(L5).
④·⑤·⑧·⑪~⑮ 를 고쳐 적고 ⑰ 을 더했다.
배포 직전 3차 독립 리뷰(e5b528a..abf0836)는 Blocking·High 없이 Medium 넷을 짚었다 — 다른 출처
포워드 버림을 '게이트가 제 일을 한 것' 으로 **단정**했고(M2: 나쁜양파가 재게시한 관련 글도 같은
모양으로 온다), 예외 ❌ 가 스스로 처방한 재포워드로 안 풀렸고(M1: 새 번호), 손으로 돌린 백필이
안 잡히는 메모가 같은 처방을 되풀이하게 했고(M3), 전달 실패한 알림을 '알렸다' 로 기록했다(M4).
Low 로는 원자적 쓰기·번호 상한·재시작 전 예외 필터가 무가드였다(L3·L4) — ④·⑤·⑧·⑫·⑭·⑰ 에 더했다.

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 버림 기록 | 출처 게이트는 건마다 사유(origin 종류·chat·username·허용 목록), 채널 게이트는 채널마다 한 번 · **핸들러를 태워** 잰다(헬퍼만 재면 배선을 떼도 통과, #20) · 받은 글은 버림으로 안 적는다(반대 증거) · 진단 파서가 그 줄의 **필드**(종류·chat·username)까지 읽는다 | `test_bot_drop_log.py::OriginDropLogTests` · `::ChannelDropLogTests` |
| ② 생산자↔소비자 | 봇이 **실제로 찍은 레코드**를 봇의 **실제 포매터**(`_TokenRedactFormatter(_LOG_FORMAT)`)로 저널 줄로 만들어 진단 파서에 태운다 · 봇 시작 줄은 소스의 log.info 형식을 AST 로 꺼내 채운다 · 릴레이 넷의 포워드·접속 줄은 **로깅 형식(`basicConfig(format=…)`)·로거 이름(`getLogger(…)`)·메시지 형식을 전부 소스에서** 꺼낸다(옛 판은 형식·로거 이름을 손으로 적어 구분자·이름 변경이 살아남았다 — 리뷰 P01·P03, #19·#155) | `test_bot_drop_log.py::DropLogIsReadByBotHealthTests` · `test_bot_health.py::test_start_line_parser_reads_the_real_bot_format` · `::test_start_line_old_build_and_unreadable_sets` · `::test_relay_forward_parser_reads_every_real_relay_format` · `::test_relay_run_start_comes_from_the_same_pid_connect_line` |
| ③ 수신 종류·에러 핸들러 | `run_polling` 이 allowed_updates 를 **명시**하고 에러 핸들러를 건다 — `main()` 을 가짜 Application 으로 태워 인자·등록을 본다 | `test_bot_drop_log.py::AllowedUpdatesTests` |
| ④ 대조 | 받음 = 수신 줄 + 출처 게이트 버림(아래 ⑪) · ok · total · partial · 기다릴 만큼 안 지남 · 시각 못 읽음(판정 불가) · 여러 사건이 섞이면 **가장 이른 시작**부터 센다 · 경계(시작과 같은 시각의 수신 · 딱 grace 만큼 지난 포워드) · 사건이 스스로 아는 시작(접속 줄)부터 센다(30분보다 긴 실행 — 2차 리뷰 B07) · 진단의 판정은 **지금 프로세스의 몫**으로만 하고 그 전 누락은 ⚠️ 메모 — 다시 포워드해 받으면 사라진다 · 끝이 재시작과 같은 시각이면 지금 몫(경계 — V04) · **재시작을 걸친 실행**은 잰 시작부터 두 프로세스의 수신을 같이 센다(2차 리뷰 M2 재현: 27건이 다 inbox 에 있는데 '텔레그램이 안 줬다' 로 끝났다) · 30분 **짐작한** 시작은 재시작을 넘지 않는다(재시작 전 누락이 지금 누락으로 둔갑한다) · 그 전 누락 메모는 손으로 돌린 백필이 '포워드' 수에 안 든다는 것과 어느 갈래인지 보는 명령(`FIND_CMD`)을 말한다 — 다시 포워드한 뒤에도 메모가 그대로 남아 같은 처방을 되풀이하게 했다(3차 리뷰 M3) | `test_bot_health.py::test_delivery_gap_branches` · `::test_delivery_gap_counts_ingest_before_a_backfill_done_line` · `::test_delivery_gap_counts_from_the_earliest_start_among_mixed_forwards` · `::test_delivery_gap_counts_from_the_events_own_start` · `::test_delivery_gap_boundaries` · `::test_split_at_restart_boundary_measured_start_guess_and_moved_events` · `::test_backfill_straddling_a_restart_counts_both_processes` · `::test_losses_before_the_running_process_are_a_note_not_the_verdict` · `::test_pre_restart_shortfall_note_says_manual_backfills_are_invisible` |
| ⑤ 판정 | 원인마다 ❌ 한 줄(미가동·401·웹훅·수신 종류·관리자·409·폴링 멈춤·릴레이 목적지의 채널 버림·릴레이 포워드의 출처 버림 — 이름 **또는** ID(inbox·getChat·릴레이 접속 줄)로 알아본다 · 허용 채널·출처 목록·inbox 경로·원천 사용자명 변경) · 일부 누락도 ❌ 이고 받은 수·빠진 수를 숫자로(옛 '마지막 포워드 뒤 수신' 은 백필 'done' 사건엔 구조적으로 0 이라 뺐다 — 2차 리뷰 L8) · 못 잰 것은 ❓(rc 2, ✅ 안 찍음) · 원인 없이 증상만 있으면 **남은 사실대로** 말한다(전부/일부 · 버림이 있었나 · 표식 없는 예외 · 짐작한 시작) · 다른 채널·직접 쓴 글의 버림은 ⚠️ 메모('제 일') · **다른 출처 포워드**의 버림도 ⚠️(rc 0)지만 '제 일' 이라 단정하지 않고 출처 이름과 가르는 명령을 댄다(3차 리뷰 M2 — 나쁜양파의 재게시면 손실이다) · 그 명령의 인터프리터·플래그는 동기화 유닛·argparse 에서 잰다 · 번호 목록은 8개에서 자르고 자른 수를 말한다(L4) · 대기 업데이트 메모 · 409 면 이 서버의 다른 프로세스를 짚는다 · 창 안 시작이 잦으면 메모 | `::test_each_cause_is_a_red_line` · `::test_what_we_could_not_measure_is_not_green` · `::test_origin_accepts_mirrors_the_bot_gate` · `::test_unexplained_gap_says_which_branch_is_left` · `::test_unexplained_partial_with_third_party_drops_does_not_claim_no_drops` · `::test_partial_gap_is_a_red_symptom_with_the_numbers` · `::test_all_good_is_green` · `::test_benign_drops_are_notes_not_failures` · `::test_unexplained_gap_with_unmeasured_conditions_says_measure_first` · `::test_source_checks_are_skipped_when_telegram_is_unreachable` · `::test_running_as_root_does_not_compare_inbox_paths` · `::test_relay_drop_is_recognised_by_inbox_id_even_when_telegram_is_unreachable` · `::test_relay_drop_is_recognised_by_the_getchat_id_when_the_inbox_has_none` · `::test_pending_updates_409_where_and_inbox_24h_count` · `::test_restart_count_note_when_the_bot_keeps_starting` · `::test_other_origin_drops_are_not_called_benign_and_say_how_to_tell` · `::test_find_cmd_uses_the_sync_units_interpreter_and_real_flags` · `::test_msgs_caps_the_list_and_says_how_many_were_cut` |
| ⑥ 읽기 전용·비밀값 | getUpdates 거부 · 오류 문구의 토큰 제거 · 웹훅 URL 은 호스트만 · 텔레그램엔 목록 안 메서드만(머리말도 그 목록에서 — `getChat` 은 `getChatMember` 의 부분문자열이라 포함 검사로는 빠져도 통과했다, #75) · 전달 오류 문구도 가린다 · 저널 실패 문구도 가린다 | `::test_tg_call_refuses_get_updates` · `::test_tg_call_scrubs_the_token_from_errors` · `::test_journal_facts_counts_polls_ingest_drops_and_hides_the_token` · `::test_webhook_url_path_never_reaches_the_output` · `::test_telegram_facts_asks_only_read_only_methods` · `::test_read_journal_names_each_branch` · `::test_render_details_low_findings` |
| ⑦ 수집 배선 | 시작 줄은 **지금 PID** 의 것(창 안에 없으면 `_PID=` 로 찾는다 · 상한까지만 읽는다 · 줄은 있는데 형식을 못 읽으면 그렇게 말한다) · systemd 에 못 물었으면 'PID 없음' 으로 단정하지 않는다 · 읽히는데 0줄 = 빈 사실 · 권한 없음 = 판정 불가 · 두 저널을 대조해 gap 을 싣는다 · 자기 자신과 지금 봇은 '다른 프로세스' 가 아니다 · `.env` 가 환경변수보다 먼저 · root 표식 | `::test_collect_uses_the_running_pid_start_line_and_falls_back_to_pid_lookup` · `::test_find_start_line_reads_the_head_and_terminates` · `::test_find_start_line_stops_at_the_cap_and_names_an_unparseable_line` · `::test_running_err_says_systemd_was_not_asked` · `::test_collect_rotated_journal_is_an_empty_fact_not_unreadable` · `::test_collect_wires_the_gap_from_both_journals` · `::test_collect_excludes_itself_and_the_running_bot_from_other_processes` · `::test_trade_env_prefers_the_env_file_and_flags_root` · `::test_inbox_facts_per_source` · `::test_delivery_check_relay_journal_unreadable_is_unknown` |
| ⑧ 매시간 알림 | 누락이면 알린다(일부 누락 포함) · **릴레이 원천의** 버림·예외로 놓친 글도 알리되 다른 출처의 버림은 무음 · 같은 **사실**은 한 번(아래 ⑰ — 옛 판의 누락마다 표식은 **같은 릴레이**의 새 누락을 못 가르고도 두 릴레이 픽스처로 통과했다, 2차 리뷰 B21) · 진짜 `delivery_check` 를 가짜 저널로 태워 기록 파일 배선까지 잰다 · ok·none·unknown 은 무음(저널을 못 읽으면 경고 로그) · 시각을 못 읽은 포워드가 있어도 예외·버림 알림은 나간다 · 한 신호가 던져도 다른 신호는 돈다 · 매시간 경로는 저널 둘과 레포의 릴레이 선언만 본다 · 알린 사실은 **전달된 뒤에만** 기록한다(3차 리뷰 M4 재현: 429 인데 기록돼 다시 안 알렸다) — `_notify` 는 ok:true 일 때만 True(건너뜀·거절·예외는 False) · 예외 문구는 안 찍는다(TimeoutExpired 는 토큰이 든 명령줄을 싣는다) | `::test_health_check_alerts_once_on_a_gap` · `::test_health_check_alerts_a_new_gap_even_right_after_another` · `::test_health_check_alerts_relay_gate_drops_but_not_third_party` · `::test_health_check_is_quiet_unless_a_gap` · `::test_health_check_undated_forwards_do_not_swallow_other_alerts` · `::test_health_check_main_runs_both_signals_and_fails_loudly` · `::test_delivery_check_reads_journals_only` · `::test_hourly_alert_is_recorded_only_when_delivered` · `::test_notify_reports_delivery_and_never_logs_the_command` |
| ⑨ 릴레이 전수 | 릴레이 원천은 `SOURCE_USERNAME` 에서 — `forward_messages` 를 **호출**하는 스크립트는 전부 그 상수를 둔다 · `RELAY_UNITS` 는 `deploy/*.service` 중 그 스크립트를 띄우는 유닛과 **양방향**으로 같다(하나를 빼도 통과했다 — 리뷰 Q01) | `::test_every_forwarding_script_declares_source_username` · `::test_relay_units_match_the_deploy_units_that_run_relay_scripts` |
| ⑩ 리스너 판정 일반화 | 유닛 이름은 인자에서 · `systemctl status` 를 권하지 않는다 · 세션 없는 유닛의 exit 78 은 재인증이 아니다 · `systemd_facts(extra=…)` | `::test_listener_verdict_for_trade_bot_names_the_right_unit` · `::test_exit_78_is_not_reauth_for_a_unit_without_a_session` · `::test_systemd_facts_extra_properties` |
| ⑪ 버림 = 받음(리뷰 H2) | BeOn 은 다른 채널(AWAKE 플러스 등)의 글을 되포워드하고 텔레그램은 원래 출처를 단다 — 봇이 그 글을 버린 것을 '못 받음' 으로 세면 매번 거짓 누락이다. 버린 포워드는 받음으로 세고(직접 쓴 글은 포워드가 아니라 안 센다 — 옛 판이 '도달 불가' 로 지웠던 `type != "none"` 조건이 여기서 다시 필요해졌다) · 대조 창 앞의 버림으로 지금 누락을 가리지 않는다 · 릴레이 원천의 버림은 판정·매시간 알림이 **버림 줄에서** 따로 센다(이미 알린 버림은 다시 안 알린다) · 수집기·매시간 경로를 통째로 태운다 | `::test_gate_drops_are_received_not_lost` · `::test_forward_drops_classifies_relay_by_name_or_id_and_skips_direct_posts` · `::test_third_party_origin_drops_through_collect_are_not_a_gap` · `::test_delivery_check_classifies_relay_drops_by_name_and_by_journal_id` |
| ⑫ 지금 프로세스(리뷰 H1) | 409·폴링·버림·예외 판정은 **MainPID 의 줄**로 — 재시작 전 프로세스의 것은 ⚠️ 메모(예외로 놓친 글 메모는 2차 리뷰 L6 이 '독스트링만 약속하고 없다' 고 짚어 더했다 · 그 메모에 다른 출처 글의 예외는 안 든다 — 무가드였다, 3차 리뷰 L4) · 대조군: 같은 줄이 지금 프로세스 것이면 ❌ · 막 뜬 프로세스는 첫 폴링 전이라 멈춤과 같은 창만큼 봐 준다(대조군: 오래 떠 있는데 0회면 ❌) | `::test_verdict_judges_the_running_process_and_notes_the_previous_one` · `::test_losses_to_exceptions_before_the_restart_are_a_note` · `::test_a_just_started_process_is_not_called_stalled_before_its_first_poll` · `::test_pre_restart_exceptions_of_other_origin_posts_are_not_called_lost_relay_data` |
| ⑬ 창 경계(리뷰 M1) | 백필의 수신은 같은 PID 의 접속 줄(`source(marked)=`)부터 센다 — 그 줄이 창 밖이면 짐작하고 표시한다 · 리스너는 포워드 시각 − 30초 · 매시간 경로는 봇 저널을 30분 앞부터 읽는다(리뷰 재현: 창 첫머리에 끝난 실행을 누락으로 알렸다) · 진단은 봇 저널을 **두 번** 읽는다 — 판정은 사용자의 `--since` 창, 수신 대조는 30분 앞부터(2차 리뷰 L5 재현: 창으로 뺀 07:45 의 409 가 '지금' 의 ❌ 가 됐다 · 모르는 꼴이나 넓혀 읽기 실패는 같은 창으로 세고 머리말에 밝힌다) · 매시간 알림도 예외·버림은 **창 안**의 것만 · 봇 수신을 실제로 센다(리뷰 N01) | `::test_relay_run_start_comes_from_the_same_pid_connect_line` · `::test_delivery_check_reads_the_bot_journal_earlier_than_the_relay_window` · `::test_widen_since_moves_back_only_forms_it_understands` · `::test_collect_reads_the_bot_journal_with_the_widened_window` · `::test_judgment_uses_the_since_window_and_receipts_use_the_wide_one` · `::test_collect_counts_receipts_before_since_for_a_run_that_ended_inside_it` · `::test_hourly_alerts_only_facts_inside_its_window` |
| ⑭ 처리 중 예외(리뷰 M2 · 2차 H1) | 봇 에러 핸들러가 핸들러 예외(업데이트 종류·메시지 번호·**포워드 출처** — 버림 줄과 같은 규약)와 폴링 오류를 한 줄 표식으로 가르고 트레이스백을 남긴다 — 그 줄 형식을 **소스에서** 꺼내 진단 파서로 읽는다 · 수신 줄이 없는 번호의 예외는 손실의 **직접 증거** — 수 대조(gap)와 **무관하게** 릴레이 원천 ❌ · 출처를 적지 않는 판 ❓ · 직접 쓴 글·다른 출처 ⚠️(2차 리뷰 재현: 남의 글 버림·기다리는 새 포워드의 수신이 덮어 ✅ 에 '빠진 것이 없다' 메모 · 매시간 무음) · 기록된 번호의 예외는 메모 · 채널 글이 아닌 업데이트(DM)의 예외는 대조 밖(B12) · 표식 없는 옛 판 예외는 **대조 창 안**의 것만 원인을 못 짚은 갈래에서 '표본부터'(B13) · 다시 포워드한 글은 **새 번호**라 그 ❌ 는 줄이 창에 남는 동안 안 풀린다 — ❌ 줄이 그 사실과 `--since` 로 좁히는 법을 말한다(3차 리뷰 M1 재현) | `test_bot_drop_log.py::ErrorHandlerTests` · `test_bot_health.py::test_handler_exceptions_in_the_gap_window_are_the_cause` · `::test_journal_facts_reads_exception_kinds_and_ingested_ids` · `::test_exception_line_from_the_bot_source_parses_with_its_origin` · `::test_a_masked_loss_is_red_and_alerted_once` · `::test_dm_exception_is_not_a_lost_channel_post` · `::test_unlabeled_exception_outside_the_gap_window_is_not_blamed` · `::test_exception_red_line_says_reforwarded_posts_get_new_ids` |
| ⑮ 비밀값(리뷰 M3) | 봇 로그의 토큰을 **포매터**에서 가린다(메시지·URL 객체 인자·트레이스백 — 필터와 달리 공유 레코드를 안 바꾼다) · 가린 뒤에도 watchdog 이 세는 'getUpdates' 와 진단의 폴링 파서는 산다 · **별도 프로세스**로 import 해 운영의 루트 핸들러가 가리는 그것인지 잰다 · 잡히지 않은 예외(토큰 거절 문구)도 진입점이 받아 같은 포매터로 · ⑦ 명령줄은 가린 **뒤** 자른다(토큰이 자르는 경계에 걸친 픽스처 — 첫 판은 잘린 조각이 6자라 눈멀었다) · 인터프리터가 실행하는 명령만 봇으로 센다(`vim trade/bot.py` 등은 아니다) · 한 줄에 토큰이 둘이면 둘 다 가린다(2차 리뷰 T07) · `systemctl status trade-bot` 권고 금지(디렉터리 전수 · 독스트링 제외) · `journalctl` 권고엔 **토큰 모양을 지우는 sed** 가 붙어 있어야 한다(낱말 'sed' 를 부분문자열로 찾던 옛 검사는 'used' 에 속았다 — 2차 리뷰 KF02) | `test_bot_drop_log.py::TokenRedactionTests` · `::EntrypointTests` · `test_bot_health.py::test_other_bot_processes_scrubs_before_truncating_and_needs_an_interpreter` · `::test_other_bot_processes_ignores_itself_and_siblings` · `::test_no_trade_code_recommends_systemctl_status_for_the_bot_unit` |
| ⑯ rc | `main` 이 판정의 rc(1·2)를 그대로 돌려준다 — 늘 0 이면 셸 `&&` 가 ❌·❓ 에서도 재포워드를 돌린다 · 버림을 적지 않는 **옛 판**이 돌면 증상이 없어도 ❓(rc 2) — ⚠️ 메모(rc 0)였을 땐 배포 직후 `bot_health && 백필` 이 재시작 **전** 옛 봇에 다시 포워드를 흘렸다(실수 #409) · 같은 사실의 새 판은 ✅ rc 0(반대 증거) · `main` 종료코드까지 태운다 · 증상이 있으면 옛 판 ❓ 는 더 구체적인 한 줄이 대신하되(#395) 다른 ❓ 가 있으면 둘 다 남는다 | `::test_main_returns_the_verdict_rc` · `::test_main_prints_every_section_and_returns_the_verdict_rc` · `::test_old_build_is_undecidable_even_without_a_gap` · `::test_old_build_with_a_gap_and_another_unknown_keeps_both` |
| ⑰ 알림은 사실마다 한 번(2차 리뷰 L4) | 알린 사실(포워드 사건·버림·예외로 놓친 글)의 신원을 `delivery-alerted.json` 에 적고 다음 실행은 **아직 안 알린 사건만으로** 다시 대조한다 — 끊김이 이어지면 새로 빠진 포워드만 알리고 각 포워드는 **정확히 한 알림**에만 실린다(리뷰 재현: 옛 표식은 창 안 첫 포워드라 매시간 바뀌어 포워드마다 두 통) · 포워드 사건은 **누락을 알렸을 때만** 적는다(버림 때문에 나간 알림에 같이 적으면 기다리는 새 포워드의 수신이 잠시 덮은 누락이 영영 안 알려진다) · 2일 지난 기록은 읽을 때 빼고 다음 쓰기에서 사라진다 · 쓰기는 원자적(교체가 실패하면 옛 기록이 그대로 · 임시 파일 이름에 PID — 3차 리뷰 L3: 문서만 주장하고 무가드였다) · 못 읽으면 빈 기록 + 경고 | `::test_a_sustained_outage_alerts_each_forward_exactly_once` · `::test_hourly_marks_forwards_only_when_the_alert_is_about_them` · `::test_health_check_alert_record_prunes_and_survives_garbage` · `::test_delivery_check_classifies_relay_drops_by_name_and_by_journal_id` · `::test_save_alerted_is_atomic_and_per_process` |

뮤테이션(이번 라운드 68종 — 리뷰가 남긴 생존 13종의 재현 포함): 67종이 잡혔다. 첫 실행에서
셋이 살아남았고 셋 다 **테스트가 엉뚱한 것을 재던** 경우였다 — 대조가 정상인 픽스처라 예외
판정까지 안 갔고(M2b), `getChat` 이 `getChatMember` 의 부분문자열이었고(L7), 명령줄 픽스처의
토큰이 `KEY=` 꼴이라 다른 패턴이 자르기와 무관하게 가렸다(M3a). 픽스처를 고쳐 잡았다. 남은
하나(60자에서 자른 뒤 가리기)는 두 픽스처 모두 60자 안에 토큰 조각이 없어 **누설이 없는**
변형이다 — 실제 한도(160자)에서 같은 변형은 잡힌다. 하네스는 `-x` 없이 돌리고 실패한 테스트
이름을 기록했으며(#384), 백업은 green 뒤 떴고 복원은 md5 로 확인했다(#358).

2차 리뷰 반영분 뮤테이션(50종): 처음에 셋이 살아남았다. 하나는 치환이 코드를 **바꾸지 않은**
내 명세 실수였고(빈 문자열을 앞에 붙였을 뿐 — 통과하면 그 자리를 실제로 쳤는지부터, #267),
둘은 픽스처가 눈이 멀어 있었다 — 짐작한 시작의 옛 사건이 옮겨 오는 경우를 안 태웠고(N17),
`_collect` 가짜가 두 번의 읽기에 같은 줄을 돌려줘 수신을 좁은 창으로 세도 통과했다(N20, #91b).
픽스처를 더해 셋 다 잡았다(50/50). 리뷰 표의 P01(`-c` 를 플래그로)은 동등 변형이고, T08
(`_LOG_FORMAT` 구분자)은 주석이 없는 계약을 주장하던 것이라 주석을 고쳐 해당 없음이 됐다 —
진단은 메시지 본문만 읽는다.

#409 반영분 뮤테이션(7종 — 옛 판을 다시 메모로 · 증상 갈래의 '다른 못 잰 조건' 에 옛 판을
넣기/아예 안 보기 · 대신한 뒤 안 지우기 · `main` 이 ❓ 를 0 으로 · 옛 판 판정 끄기 · 시작 줄
표식 무시): 7종 전부 잡혔다. 새 테스트 둘은 고치기 **전** 코드에서 실패함을 먼저 확인했다
(§Pre-commit 9). 그 전엔 증상이 없는 옛 판을 재는 테스트가 **하나도 없었다** — 증상이 있는
옛 판만 쟀다(#20·#291).

3차 리뷰 반영분 뮤테이션(12종 — 다른 출처 메모를 옛 단정으로 · `FIND_CMD` 플래그 바꾸기 · 예외 ❌ 의
'새 번호' 문구 빼기 · 재시작 전 메모의 '손으로 돌린 백필' 빼기 · 전달 실패에도 기록 · 거절을
전달로 · 예외 문구 그대로 찍기 · 비원자적 쓰기 · 임시 파일 이름에서 PID 빼기 · 번호 상한 없애기 ·
재시작 전 예외 메모에 다른 출처 넣기 · 건너뜀을 전달로): 12종 전부 **겨냥한 그 테스트**가 잡았다
(`-x` 없이 · 실패 테스트 이름 기록 · green 뒤 백업 · md5 복원 확인). 리뷰가 남긴 생존 5종 중 셋
(M25·M28·M33)은 위 테스트로 잡히고, 둘(M22·M23 — 사실 신원에서 번호·시각 빼기)은 번호가 유일하고
같은 초의 사실은 함께 알려져 **관측 가능한 차이가 없는** 변형이라 가드를 만들지 않았다(#373).

⚠️ **못 보는 축**(#274): 텔레그램 쪽 사실(웹훅·수신 종류·관리자·원천 사용자명)은 **가짜
응답 위**에서만 잰다 — 실물 Bot API 는 VM 에서 첫 실행이 잰다. 손으로 돌린 백필은 저널에
안 남아 대조에 안 잡힌다(systemd 유닛 실행만 센다). 백필의 접속 줄이 창 밖이면 시작을
짐작한다(30분) — 판정 줄이 그렇다고 밝힌다. 채널 게이트 버림은 채널마다 한 번만 적혀 **건수로
못 센다**. 운영 봇이 옛 판이면 게이트 버림·예외 표식이 저널에 안 남는다 — 진단이 그 사실을
말한다. 재시작 전 누락 메모는 **수로** 센다(어느 글인지 모른다). 수 대조는 번호로 짝을 짓지
않는다 — 같은 창의 다른 글(운영자가 직접 포워드한 글·손으로 돌린 백필·기다리는 중인 새 포워드)의
수신·버림이 **예외 없는** 손실(텔레그램이 안 준 글)을 덮을 수 있다(예외로 끝난 글은 그 줄이
증거라 이 한계 밖이다). 끊김이 이어지면 매시간 새로 빠진 포워드를 알린다 — 12시간 끊김이면
12통이지만 같은 포워드는 한 통에만 실린다. 매시간 알림은 재시작 전후를
**가르지 않고**, 원천이 사용자명을 바꿨는데 창 안에 그 릴레이의 접속 줄이 없으면 그 버림을
릴레이 것으로 못 알아본다(받음으로만 센다 — 진단은 inbox·getChat 으로 잡는다). 토큰 가림은
**이 판부터** 찍는 줄에만 걸린다 — 가리기 전 줄은 저널 보존기간 동안 평문으로 남는다.

## #407 — 시크릿 스캐너의 범위는 '커밋될 것' (`tests/test_no_secrets_committed.py` 2건 · 2026-09-25)

`git ls-files`(추적 파일)만 보던 스캐너가 아직 `git add` 안 한 새 파일을 못 봐서, 새 테스트의
가짜 토큰 리터럴이 커밋 전 `make test` 를 통과했다. 이제 추적 파일 + 무시 목록 밖 새 파일을
본다. 새 파일 중 **가상환경**(조상 디렉터리에 `pyvenv.cfg`) 안의 것은 뺀다 — 운영
`.backfill-venv/` 는 무시 목록 밖이라 그대로면 그 site-packages 의 예시 키가 게이트를 빨갛게
만든다(이름 열거가 아니라 구조, #24). 추적 중인 파일은 가상환경 안이어도 본다(이미 커밋된
것이다).

| 축 | 계약 | 테스트 |
|---|---|---|
| 범위 | **임시 저장소**에서: 추적 파일 + add 전 새 파일은 보고 · 무시 목록 · 최상위·하위 가상환경의 새 파일은 안 본다 · 가상환경이라도 추적 중이면 본다(반대 증거, #25) — 레포 트리엔 안 쓴다(#365) · 임시 저장소의 git 은 바깥 `GIT_*` 환경을 물려받지 않는다(배포 전 독립 리뷰 L2 — 훅이 넘긴 인덱스를 고쳤다) | `::test_scope_is_what_would_be_committed` |

뮤테이션 5종(추적만 · 가상환경 안 뺌 · 무시 목록 안 따름 · 추적 파일까지 가상환경으로 뺌 ·
최상위만 가상환경 판정)이 전부 잡혔다 — 마지막 것은 첫 픽스처(최상위 가상환경뿐)에서 살아남아
하위 가상환경 픽스처를 더했다(#91c).

## #411 — 재게시 글은 원래 출처로 온다: 릴레이가 그 글을 보증한다 (`trade/tests/test_relay_origins.py` 34건 · `test_listen_badonion_vouch.py` 9건 · `test_backfill_badonion_sync.py` +9 · `test_bot_drop_log.py` +5 · `test_bot_health.py` +18 · 2026-09-25 배포 전 독립 리뷰까지 반영한 뒤 실측)

VM 실측(2026-09-25 `trade.bot_health`): 봇은 40일 회수 27건을 **다 받았고** 출처 게이트가
`다른 출처 포워드 27: -1003901069327` 로 버렸다 — 나쁜양파가 다른 채널에서 퍼 온 글이라
텔레그램이 원래 출처를 달았다(#406 이 심은 버림 줄이 답했다). 채널을 `.env` 에 더하지 않고
릴레이가 포워드 **전에** (원래 채널, 원래 글번호)를 `trade/relay_origins.py` 에 적고 봇이 그 짝만
받는다 — 채널 단위로 열면 BeOn 이 같은 채널의 무관 글을 되포워드할 때 그것까지 받는다.

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 보증 기록 | 없으면 정상(사유 "") · 빈 파일·깨진 JSON·비UTF-8·목록 뿌리·`chats` 없음·모르는 판·디렉터리 자리는 **안 던지고** 사유를 돌려준다(#331 — 봇 게이트가 부른다) · 못 읽은 항목만 빼고 나머지는 살려 그 수를 말한다 · 시간대 없는 시각은 None · 처음 보증한 시각·릴레이를 지킨다(진단이 '보증 **뒤의** 버림' 을 가르는 기준) · 제목은 채널 단위 · 안 바뀌면 안 쓴다(쓰기 횟수로 잰다 — inode 는 지우고 다시 만든 파일이 같은 번호를 받아 눈이 멀었다, 뮤테이션 R2) · 보증할 것이 없으면 디렉터리도 안 만든다 · `KEEP_DAYS` 지난 보증은 쓸 때 걷어낸다(경계는 남긴다) · 못 읽은 파일 위에 쓰면 경고하고 새로 쓴다 · 쓰기 실패는 **던진다**(부르는 쪽이 포워드하지 않는다) · 원자적 교체(교체가 실패하면 원본 그대로 · **임시 파일도 안 남는다**) · 임시 파일 이름에 PID · **락을 잡은 뒤에** 다시 읽어 합친다(락 순간에 다른 쓰기가 끝난 상태를 결정적으로 만든다 — 시간·스레드 금지, #128) · 실제 프로세스 둘이 30건씩 번갈아 써도 60건 · 락을 못 걸면 진행하되 경고 · 락 대기는 **상한**이 있다(쥔 채 멈춘 프로세스가 리스너 이벤트 루프를 세우면 안 된다 — 스레드로 돌려 끝나는지 잰다) · 깊게 중첩된 JSON(`RecursionError`)도 안 던지고 형식 오류다 · **일시적 읽기 실패·모르는 판이면 덮어쓰지 않고 던진다**(옛 판은 EIO 한 번에 봇이 아직 안 받은 보증을 지웠다 — 깨진 기록만 새로 쓴다) · 판은 **구조보다 먼저** 본다(`chats` 가 없는 새 판을 '깨졌다' 로 읽어 덮어쓰지 않는다) · 판 표시(`v`)가 **없는** 파일은 우리가 쓴 적 없는 모양이라 깨진 것으로 새로 쓴다(모르는 판으로 두면 보증이 영영 막힌다) | `test_relay_origins.py` 전부 |
| ② 재게시 판정 | `fwd_from.from_id` 가 **채널**이고 `channel_post` 가 있을 때만 — 원천 채널 자신의 글은 보증할 것이 없고 개인 계정·출처를 숨긴 포워드·번호 없는 포워드는 센다 · 키는 **부르는 쪽의** `get_peer_id`(백필 중복 제거 키와 같은 함수, #38) · 그 함수가 못 읽으면 지어내지 않고 센다 · 제목 조회가 던져도 안 죽는다 | `::test_repost_pairs_takes_only_channel_origins_and_counts_the_rest` · `::test_repost_pairs_uses_the_callers_peer_id` · `::test_a_title_lookup_that_raises_is_not_fatal` |
| ③ 백필 배선 | 가짜 클라이언트의 포워드가 불리는 **순간** 보증 기록에 그 글이 있다(앨범은 멤버 전부) · 원천 채널 자신의 글은 파일을 안 만든다 · dry-run 은 보증도 안 쓴다(#264) — 대신 `to-forward`·`find` 줄이 원래 출처를 말한다 · 보증을 못 쓰면 그 유닛만 포워드하지 않고 알림이 삭제·포워드 실패와 **갈라** 센다(#82) · 둘이 섞여도 각자 센다 · 사람이 연 창의 연속 실패 중단 사유가 보증 실패를 이름으로 대되 **그 연속 구간 안의** 것만 센다(보증 실패만 이어졌으면 '포워드 실패' 라 부르지 않는다 · 사이의 성공이 구간을 끊는다) · 보증할 수 없는 포워드는 옛 동작 그대로 포워드하고 봇이 버린다고 적는다 | `test_backfill_badonion_sync.py::test_a_repost_is_vouched_before_it_is_forwarded` · `::test_an_album_vouches_every_repost_member_before_the_one_forward` · `::test_a_native_post_writes_no_vouch` · `::test_a_dry_run_names_the_repost_origin_but_vouches_nothing` · `::test_a_repost_that_cannot_be_vouched_is_not_forwarded` · `::test_a_forward_failure_and_a_vouch_failure_are_counted_apart` · `::test_consecutive_vouch_failures_abort_and_name_the_cause` · `::test_the_abort_reason_counts_only_the_vouch_failures_in_the_streak` · `::test_an_unvouchable_forward_is_named_and_forwarded_as_before` |
| ④ 리스너 배선 | 리스너를 **실제 이벤트로** 태운다(가짜 클라이언트가 핸들러를 받아 두고 글을 흘린다) — 단일·앨범 모두 포워드 순간 보증돼 있다 · 원천 글은 보증 없이 · 무관한 재게시는 보증도 포워드도 안 한다 · 보증을 못 쓰면 큐에 안 넣고 알리며(같은 사유는 **전달이 확인되면** 프로세스당 한 번 — 건마다는 로그 · 못 간 알림은 '알렸다' 로 적지 않고 `_VOUCH_ALERT_RETRY_S` 뒤 같은 사유가 다시 나면 다시 알린다 · 전달은 텔레그램 응답의 `ok` 로 잰다, `curl -s` 는 429 에도 종료코드 0) 같은 흐름의 다른 글은 간다 · 보증할 수 없는 포워드는 옛 동작 · 리스너 재시작 트리거(`deploy/trade-auto-update.sh`)가 리스너의 `trade.*` import **폐포 전부**를 덮는다(소스에서 폐포를 따라가 잰다 — 진입점이 실행하는 **위 패키지의 `__init__.py`** 포함 · 옛 트리거 `listen_badonion.py` 만으로 되돌리면 실패함을 확인 · 폐포 밖 파일·NOAH 쪽으론 재시작하지 않는다) | `test_listen_badonion_vouch.py` 전부 |
| ⑤ 봇 게이트 | 핸들러를 태운다 — 보증된 그 글은 받고 `accepted … reason=relay_vouch` 줄을 남긴다 · **같은 채널의 다른 글**은 버린다(글 단위 — 채널 단위로 열리는 변형을 잡는다) · 못 읽은 기록이면 버리고 건마다 `vouch=unreadable`, 사유 경고는 한 번 · 목록·BeOn 머리글로 받는 글은 파일을 안 읽고 원래 채널이 없는 글은 `vouch=n/a` · 시작 줄에 `relay_vouch=on` · 버림 줄은 원래 글번호·`vouch=`·줄 끝 `origin_title` 을 싣는다 · 봇 데이터 디렉터리는 테스트 임시 경로(운영 `~/.trade` 를 안 읽는다, #373) | `test_bot_drop_log.py::RelayVouchGateTests` · `::OriginDropLogTests` |
| ⑥ 생산자↔소비자 | 버림·수신 줄을 봇 소스의 **실제 형식**으로 채워 진단이 읽는다 — 제목에 따옴표·가짜 칸(`vouch=…`)이 섞여도 안 뒤틀린다 · **분류는 봇이 쓴 칸까지만** 본다 — 채널 제목(남이 쓴 글)에 수신 줄·정상 폴링·수용 줄·버림 줄 문구가, 예외 문구에 수신 줄 문구가 들어 있어도 다른 사실이 되지 않는다 · 옛 줄은 새 칸이 None · 봇이 실제로 찍은 레코드를 실제 포매터로(보증 수용 줄 포함) · 예외 줄도 원래 글번호를 싣는다 | `test_bot_health.py::test_drop_and_accept_lines_from_the_bot_source_parse_every_new_field` · `::test_a_channel_title_cannot_become_another_journal_fact` · `test_bot_drop_log.py::DropLogIsReadByBotHealthTests` · `::ErrorHandlerTests` · `test_bot_health.py::test_exception_line_from_the_bot_source_parses_with_its_origin` |
| ⑦ 진단 판정 | 보증 **뒤의** 버림 = ❌(처방: 보증 기록·데이터 디렉터리 — .env 아님) — 단 **그 뒤** 같은 원래 글의 수용 줄이 있으면 받은 것이라 메모(같은 초·다른 글은 회복이 아니다 — 안 풀면 고친 뒤에도 `&&` 가 막힌다) · 보증 **전의** 버림 = ⚠️(보증 수용 줄로 다시 받았는지까지 · 누가 포워드했는지는 단정하지 않는다) · 저널 시각은 초로 잘린다(같은 초는 '전') · 모르면 None · 원천 이름 버림이 먼저 · 보증을 모르는 판 봇은 ❓(rc 2 — `&&` 를 막는다) · 더 옛 판은 그 ❓ 하나만 · 못 읽은 보증 기록은 ⚠️(❓ 면 그 기록을 새로 쓸 재포워드를 막는다) · ③ 에 보증 받음/모름 · ④ 에 보증 수용 수 · 보증 기록 한 줄 · 수집기가 봇과 같은 디렉터리에서 같은 함수로 읽는다(#35) · 다른 출처 메모는 옛 처방(.env 에 채널 더하기)을 권하지 않는다 | `test_bot_health.py::test_vouch_order_is_by_time_with_the_journals_one_second_grain` · `::test_a_drop_after_the_vouch_is_red_with_the_bots_own_reason` · `::test_a_vouched_drop_that_came_back_later_is_a_note_not_red` · `::test_an_accept_of_the_same_number_in_another_channel_does_not_heal`(글번호는 채널마다 따로 — 다른 채널의 같은 번호는 회복이 아니다) · `::test_a_healed_drop_now_does_not_hide_the_previous_process_relay_drop`(치유된 버림은 ❌ 가 아니므로 재시작 전 프로세스의 릴레이 버림 메모를 가리지 않고, 그 메모는 재시작 전 몫만 센다) · `::test_a_drop_before_the_vouch_is_a_note_that_says_whether_it_came_back` · `::test_a_bot_that_does_not_know_vouches_blocks_the_reforward_chain` · `::test_an_unreadable_vouch_record_is_a_note_not_a_block` · `::test_render_names_the_vouch_state_and_the_accepted_count` · `::test_collect_reads_the_vouch_record_where_the_bot_does` · `::test_other_origin_drops_are_not_called_benign_and_say_how_to_tell` · `::test_start_line_parser_reads_the_real_bot_format` · `::test_an_exception_on_a_vouched_repost_is_a_lost_relay_post`(보증된 재게시 글을 예외로 놓치면 버림과 **같은 규칙**으로 릴레이 글의 손실 ❌) · `::test_the_deployed_bot_lets_the_reforward_chain_through`(배포 **직후**엔 옛 프로세스가 버린 27건이 창에 있어도 rc 0 — 막으면 그 글을 돌려받을 재포워드가 영영 안 나간다 · 대조군: 옛 판이 돌면 rc 2) |
| ⑧ 매시간 알림 | `health_check` 가 봇과 같은 데이터 디렉터리의 보증 기록을 넘긴다 — 보증 뒤의 버림은 알리고(처방 .env 아님), 기록이 없으면 같은 버림은 무음(#260) · 그 뒤 같은 원래 글을 받았으면 안 알린다(판정과 같은 규칙) · 기록을 못 읽으면 그렇다고 경고하고 나머지 대조는 그대로 한다 · 한 번만 · 테스트는 `DATA_DIR` 을 임시로(운영 기록을 읽으면 무관한 판정이 바뀐다, #373) | `test_bot_health.py::test_the_hourly_check_alerts_a_dropped_vouched_repost_with_its_own_advice` · `::test_the_hourly_check_skips_a_vouched_drop_that_came_back` · `::test_the_hourly_check_does_not_heal_with_another_channels_same_number` · `::test_the_hourly_check_says_when_it_cannot_read_the_vouch_record` |

⚠️ 못 보는 축(#274): 원래 출처가 **개인 계정**이거나 원문이 출처를 숨기면 채널 글번호가
없어 보증하지 못한다(릴레이가 세고 봇은 계속 버린다) · 봇과 릴레이가 **다른 호스트**로 갈리면
봇도 봇 호스트의 진단도 기록을 못 봐 '보증 없는 다른 출처' ⚠️ 메모로만 남는다(알림 없음 — 지원하는
배치가 아니다: 백필의 중복 제거도 같은 호스트의 inbox 를 읽는다. 같은 호스트에서 봇만 다른 데이터
디렉터리를 쓰면 ③ 의 inbox 경로 불일치 ❌ 가 잡는다 — 독립 리뷰 #411 L4 가 옛 문구의 '❌ 로 말한다'를
반증했다) · 보증하기 **전의** 판이 포워드한
재게시 글의 버림은 매시간 알림에 안 잡힌다(보증 없는 다른 출처와 같은 모양이다 — 진단 메모가
가르는 명령을 건넨다).

뮤테이션 41종(보증 기록 R 9 · 백필 B 7 · 리스너 L 6 · 봇 게이트 G 7 · 진단 H 12) 첫 실행에서
40종이 잡혔고 둘이 **겨냥한 테스트를 비껴갔다**: R2(안 바뀌어도 늘 쓴다)는 inode 비교가 눈이
멀어 **살았고**(→ 쓰기 횟수), H2(예외 쪽이 보증을 무시)는 헬퍼 테스트만 잡아 판정 배선이
무가드였다(→ 판정 수준 테스트). 둘 다 고친 뒤 다시 돌려 겨냥한 테스트가 잡음을 확인했다(#91).
따로 둘: 재시작 트리거를 옛 규칙으로 되돌리면 폐포 테스트가 실패하고, 다른 출처 버림을 릴레이
글로 세는 변형은 배포 직후 사슬 테스트가 잡는다.

**독립 리뷰(80c0e6c..26d8b7c, 사본 worktree)**: Blocking·High 없음 · Low 7건 전부 반영(위 표의 굵은
항목) · 리뷰 뮤테이션 48종 중 생존 2종(HC2 매시간 기록 못 읽음 경고 · RO7 임시 파일 PID)은 테스트를
더해 잡았다. 반영분 뮤테이션 — `bot_health` 8종(꼬리 안 뗌 · exc 꼬리 안 뗌 · 회복 판정 늘 거짓 · 같은
초도 회복 · 글번호 안 봄 · 매시간/판정에서 회복 안 거름 · 회복 메모 없음) · `relay_origins` 7종
(`RecursionError` 못 잡음 · 읽기 실패·모르는 판도 덮어씀 · 임시 파일 안 치움 · PID 없음 · 락 무한 대기 ·
상한 무시)이 전부 잡혔다. 재시작 규칙은 폐포보다 넓다(`trade/*.py` — 지난 한 달 base 커밋 66개 실측:
옛 규칙 1회 → 새 규칙 15회, 9일) — 재시작은 몇 초라 그 사이 올라온 글은 주기 sync 가 회수한다.

## #413 — 한국 수출·수입 카드 원천을 관세청으로 (`tests/test_customs_trade_20260925.py` 65건 — 12건은 #416 절 · 2026-09-25)

카드의 공표 규약은 '관세청 통관 확정 익월 15일 전후' 인데 원천은 그 확정치를 **재게시**하는
ECOS(901Y118)였다. VM 캐시 30개를 날짜별로 잰 결과 ECOS 는 7월분을 2026-09-03(관측월 종료
+34일)에야 실어 매달 열흘가량 ⚠ 지연이 떴다(사용자 "관세청으로 바꿔줘 원천을"). 원천을
`bot/customs_trade_client.py`(data.go.kr 15102108 `getNewtradeList`)로 옮기고 ECOS 는 **계열 통째**
폴백이다. 같은 날 카드의 '12개월 전' 이 실제로는 11개월 전(스파크라인 첫 관측)이었던 것도
기간 라벨(`period_start_asof`)로 바꿨다 — ECOS 카드 전부 같이(#38).
처음엔 경로 대소문자·조회기간 상한·쪽 크기를 재지 못해 형제 선례로 좁혔는데, 같은 날 VM 프로브가
답했다(실수 #415): 경로는 `Newtrade`(대문자) 하나 — 소문자는 **HTTP 400 + resultCode 12** · 조회기간은
**1년 이내**(13개월 창 → 200 + resultCode 99 '조회기간은 1년이내') · body 엔 `items` 뿐(쪽 크기 칸 없음)
· 금액은 USD 원값 · **진행 중인 달도 부분 누계로 준다**(그래서 지난달분이 익월 1일부터 오고, 확정인
익월 15일 전후까지는 **잠정**이다 — 카드가 `관세청 잠정` 으로 적는다). 그 결과 경로 사다리를 걷어내고
(죽은 후보 삭제), 비-200 본문의 결과코드로 갈래를 정하고, 99 를 일시 오류에서 뺐다.

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 파싱 | 달 모양(`YYYY.MM`·`YYYY-MM`·`YYYYMM`)만 받고 '총계' 등은 건너뛴다 · 오류 봉투 **두 벌**(서비스 `resultCode` · 게이트웨이 `cmmMsgHeader`, #352) · 결과코드가 갈래를 정한다 — 03(NODATA)은 오류가 아니라 **빈 창**, 22(한도)·05(시간초과)는 기다리면 풀리고(조회 실패), 10·11·30 은 우리가 고칠 것(응답 오류, #82) · XML·JSON 둘 다 | `::test_norm_ym_takes_only_month_shapes` · `::test_parse_reads_fields_and_skips_non_month_rows` · `::test_parse_names_both_error_envelopes` · `::test_result_codes_split_no_data_transient_and_ours` · `::test_parse_accepts_json_with_either_item_shape` |
| ② 수집기 | 창은 6개월씩('총계' 까지 7행 — data.go.kr 기본 쪽 크기에 안 잘린다, #280 · 실측 상한 1년 안) · 경로는 실측한 **하나**(`Newtrade`) — 소문자 후보는 되살아나지 않는다(#222 옛 사다리 계약을 다시 썼다) · 창마다 한 번, 키를 싣는다 · 한 창이 실패하면 멈춘다 · HTTP 429·5xx 는 조회 실패, 4xx 는 응답 오류 — 단 **본문에 결과코드가 있으면 그 코드가 갈래를 정한다**(실측 400 + 12 → '경로 없음', 원천 문장과 물은 경로를 싣는다, #82·#352) · 99('조회기간 1년이내')는 기다려도 안 풀리는 **우리 것**(응답 오류, #260 거꾸로) · 404 면 물은 경로 이름 · 당월은 버린다(#40 — 실측 부분 누계 숫자 그대로 끼워도 달 단위로 한 번 센다) · **금액 0 인 달은 미확정**이라 값이 아니고 버린 수를 센다(전부 0 이면 '행 없음' + 그 사유) · 한 칸만 0 이면 그 카드에서만 빠진다 · 03 을 받은 창이 있어도 앞 창들의 값은 산다 · 키가 없으면 원천을 부르지 않는다 · 사유에서 키를 가린다(§Secrets) · 여러 줄 오류 본문은 **한 줄로** 접는다(결산은 ❌ 줄 하나만 올린다, #356) · 실패는 10분만 기억하고 `--check` 는 기억을 건너뛴다 · 성공은 6시간 캐시, `--check` 는 읽지도 쓰지도 않는다(#264) · 깨진 캐시는 미스(#331) | `::test_windows_split_into_chunks_that_fit_a_default_page` · `::test_only_the_measured_path_is_asked` · `::test_every_window_asks_the_one_path_with_the_key` · `::test_a_failed_window_stops_the_run` · `::test_status_kinds_split_transient_from_ours` · `::test_a_no_service_code_is_path_missing_whatever_the_status` · `::test_status_reason_reads_the_body_code` · `::test_a_result_code_outside_the_header_still_decides` · `::test_code_99_is_ours_not_a_wait` · `::test_a_404_names_the_path` · `::test_only_complete_months_inside_the_window_are_kept` · `::test_the_measured_current_month_row_is_dropped` · `::test_zero_prefilled_unconfirmed_months_are_not_values` · `::test_a_zero_in_one_field_is_not_a_value_for_that_card` · `::test_a_no_data_window_is_empty_not_a_failure` · `::test_missing_key_never_calls_the_source` · `::test_a_multiline_error_body_becomes_one_line` · `::test_the_key_never_appears_in_a_failure_reason` · `::test_a_failure_is_remembered_briefly_but_check_bypasses_it` · `::test_success_is_cached_and_check_neither_reads_nor_writes_it` · `::test_a_broken_cache_file_is_a_miss_not_a_crash` |
| ③ 카드 계열 | 겹치는 달 비 중앙값으로 단위를 검산(3-상태 — 겹치는 달이 없으면 판정 불가지 불일치가 아니다, #54) · 일치하면 관세청 · 못 쟀으면 관세청을 쓰되 `verified=None` 으로 **못 쟀다고** 남긴다 · 1000배 어긋나면 **ECOS 계열 통째**(#240·#139) · 원천 실패는 갈래를 적어 폴백 · 결과코드 22 는 '조회 실패' 갈래 · 둘 다 비면 빈 계열 + 사유 · 관세청 모듈이 던지고 ECOS 도 비면 원천 칸을 비운다('ECOS 로 그렸다' 는 거짓) | `::test_cross_check_three_states` · `::test_card_uses_customs_when_ecos_agrees` · `::test_a_unit_mismatch_falls_back_to_the_whole_ecos_series` · `::test_a_source_failure_falls_back_and_says_which_kind` · `::test_customs_is_used_when_ecos_cannot_check_it` · `::test_a_transient_result_code_is_a_wait_not_ours` · `::test_both_sources_empty_is_an_empty_series_with_a_reason` · `::test_customs_series_exception_path_names_no_source_when_ecos_is_empty` |
| ④ 화면 배선 | 수집기를 통째로 태운다(#20) — 값·기준(`YYYY-MM · 관세청`)·시작 기간·지연 배지 없음 · 폴백하면 기준 줄이 `ECOS(관세청 응답 오류)` 로 말한다 · ECOS 카드(경상수지 등)도 시작 기간을 싣는다 · 렌더가 `2025-09 대비` 를 찍고 '12개월 전' 을 안 찍는다 · 확정 공표일(익월 15일) 당일까지의 최신 달은 `관세청 잠정`(해를 넘기는 12월 포함 · ECOS 폴백엔 안 붙는다) — 날짜를 고정해 두 갈래를 다 태운다(#42) | `::test_macro_card_draws_customs_with_its_start_period` · `::test_macro_card_says_when_it_fell_back_to_ecos` · `::test_every_ecos_card_names_its_start_period_too` · `::test_the_card_prints_the_start_period_not_twelve_months_ago` · `::test_provisional_until_the_confirm_day` · `::test_card_note_says_provisional_only_for_customs` · `::test_macro_card_marks_the_provisional_month` |
| ⑤ 감사 | 화면과 같은 함수(`ms._customs_series`)로 묻는다(#35) · 폴백 줄은 갈래로 기호를 가른다(조회 실패 ⚠️ · 나머지 ❌, #260) · 폴백한 원천의 지연은 폴백과 같은 원인이라 **한 번만** 센다(#45·#250) · 두 원천 다 비어 카드가 빠지면 갈래와 무관하게 ❌ **한 줄**에 관세청 사유를 싣고 ⚪ 줄을 덧붙이지 않는다 · 감사 행에 관세청 카드가 들어 있다 | `::test_fallback_line_marks_transient_and_ours_apart` · `::test_audit_rows_include_the_customs_cards` · `::test_a_fallback_and_its_staleness_count_once` · `::test_an_empty_card_is_counted_once_even_when_the_cause_is_transient` |
| ⑥ 진단 CLI | `--check` 를 진입점으로 태운다(#252) — 코드 지문·인터프리터 배너 · 두 계열 원천 · rc(0 관세청 · 1 폴백) · 창마다 받은 달 행 수와 **빠진 달 이름**(쪽 잘림인지 미확정인지는 읽는 쪽이 가른다) · 단위를 못 잰 채 관세청을 쓰면 ✅ 가 아니라 ❓ 단위 미대조 · 키를 안 찍는다 · 운영 캐시를 안 쓴다 · 최신 달이 확정 전이면 카드와 같은 판정으로 '잠정' 이라 적는다(날짜 고정, #35) | `::test_check_cli_reports_the_source_and_exits_by_it` · `::test_check_cli_says_when_the_latest_month_is_provisional` |

⚠️ 못 보는 축(#274): 픽스처는 VM 프로브가 **파싱해서 찍은 칸**을 재현한 것이다(원문 바이트는 없다 —
소문자 경로의 400 본문이 서비스 봉투였는지 게이트웨이 봉투였는지 몰라 둘 다 재현한다). 6개월 창이
잘리지 않는지 · 지난달분이 익월 1일 몇 시부터 온전한지는 VM 의 `--check` 출력이 답한다. 단위가 틀리면 ECOS 대조가 막아 카드는 ECOS 로 남고
`단위 불일치` 로 말한다(조용히 틀린 값을 올리지 않는다). 매크로 스냅샷은 30초 주기 갱신 루프가
부르므로 캐시가 빈 주기엔 그 루프가 관세청 창 3개를 차례로 기다린다 —
요청마다 상한 10초, 성공은 6시간 캐시, 실패는 첫 실패에서 멈추고 10분 기억한다.

뮤테이션 29종(경로 사다리 · 당월 · 단위 불일치 수용 · 0 달 유지 · 칸별 0 · 03 오류화 · 22 비일시화 ·
시작 기간 배선 · 빈 카드 continue · 폴백 지연 이중 계수 · 예외 경로 원천 칸 · 진단의 캐시 쓰기 · 키
가림 · 게이트웨이 봉투 · 429/5xx · 실패 기억 · 창 크기 · 빠진 달 출력 · 빈 카드 줄 · 기준 줄 원천 ·
경로 순서 · 허용 오차 · 미확정 계수 · 미대조를 ✅ 로 · 사유 한 줄 · 12 사다리 · 12 를 일반 오류로 ·
verified 늘 참 · 시도한 경로 이름)이 전부 겨냥한 테스트에 잡혔고 복원 md5 가 일치했다. 키 가림
뮤테이션은 사유를 한 줄로 접으며 앵커가 바뀌어 하네스가 '건너뜀'으로 스스로 말했고, 새 앵커로
다시 겨냥해 잡혔다(#267 그 자리를 실제로 쳤나).

## #415 — '느린 카드' 5종 점검: 분기 표기 · FRED 헤드라인 캐시 · 첫 등장 기록 (`tests/test_slow_cards_20260925.py` 21건 — 6건은 #416 절 · 2026-09-25)

사용자가 경상수지·외환보유액·한국 GDP·미국 근원PCE·미국 GDP 를 '느린 것들' 로 짚었다. 공표 규약
(`macro_cadence`)으로 재면 다섯 다 **뒤처지지 않았다**(2026-09-25 판정 뒤짐 0) — 느려 보인 원인은
표시였다: 미국 GDP 가 분기인데 FRED 관측일(분기 **첫날** 2026-04-01)을 월로 잘라 '기준 2026-04
(5개월 전)' · '2023-07 대비' 로 적었고(같은 화면의 한국 GDP 는 '2026 Q2 (3개월 전)', #38), 분기 카드
칩이 '12개월' 이었다(12분기다). 그리고 FRED 월간·분기 헤드라인 캐시가 24시간이라, 30초마다 새로
받는 스파크와 공표일에 다른 기간을 말했다(#33). '원천이 늦게 싣나, 우리가 늦게 받나' 는 반복되는
질문이라(#413 에서 한 번, 이번에 또) `macro_staleness_audit --history` 로 제품에 심었다(#252).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 분기 판정·라벨 | FRED 분기 관측일 → `YYYYQn`(못 읽으면 원문 그대로) · 분기 판정의 단일 출처는 공표 규약(`CADENCE` freq Q, #38·#24) · 경과는 분기 **말**부터(첫날로 세면 늘 2개월 부풀려진다 — 오늘이 언제든) | `::test_as_quarter_reads_fred_quarter_start_dates` · `::test_quarterly_ids_come_from_the_cadence_table` · `::test_the_quarter_label_counts_lag_from_quarter_end` |
| ② 화면 배선 | 수집기를 통째로 태운다(#20) — 미국 GDP 가 `2026 Q2` · 분기 말 경과 · `2023 Q3 대비` · `12분기` · 창 첫 분기의 값 · FRED 에 분기를 **분기 주기로** 묻는다(월로 물으면 400) · 한국 GDP(ECOS)도 같은 칩 · 반대 증거로 월간 카드(근원PCE)는 `12개월`·`YYYY-MM` 그대로(#25) · 렌더가 칩·`… 대비`·`(3개월 전)` 을 찍는다 | `::test_us_gdp_card_speaks_in_quarters` · `::test_korea_gdp_card_gets_the_same_quarter_chip` · `::test_monthly_cards_keep_their_month_labels` · `::test_the_card_renders_the_quarter_chip_and_start` |
| ③ FRED 헤드라인 캐시 | 월간·분기도 1시간(옛 24시간 — 스파크는 캐시 없이 30초마다 부른다) · 1시간 지난 같은 날 사본은 다시 묻고 새 사본은 재사용 · FRED 가 막히거나 빈 응답이면 **같은 날 사본**을 준다(#394 — TTL 을 줄이는 짝) · 옛 버전 사본은 안 믿는다(#18) | `::test_monthly_headline_cache_is_short_because_the_spark_is_not_cached` · `::test_fred_failure_serves_the_same_day_copy_not_nothing` · `tests/test_regression.py::…::test_daily_rate_series_are_not_cached_for_a_day`(옛 계약 `≥12h` 를 다시 썼다, #222) |
| ④ `--history` | 새 기간을 **처음 본 날**만 남기고 · 기록 시작 전부터 있던 기간은 '모른다'(#54) · 못 읽은 파일은 '없었다' 의 증거가 아니다 · 규약 +유예 안이면 ✅, 빨라도 늦으면 ⚠️(최소 N일), 기록이 비어 못 가르면 ❓(#165) · 이름이 겹치는 다른 계열 파일(`PCEPILFE2`·`kr_gdp_extra`)이 안 섞인다 · 이벤트성은 판정 안 함 · 관세청 카드는 **ECOS 대조본** 기록이라고 밝힌다(#34) · 매일 감사(`main([])`)는 이걸 안 돌린다(사람이 부르는 플래그, #283) · 진입점이 인자를 넘긴다(#252 — 서브프로세스로 태운다) · rc 는 기록을 잰 계열이 하나라도 있으면 0, 0개면 1(대조 0건은 통과가 아니다, #54) · 끝줄이 `기록을 잰 계열 M/N개` 를 센다 | `::test_first_seen_marks_what_it_cannot_know` · `::test_history_reports_first_seen_against_the_cadence` · `::test_history_does_not_claim_across_a_gap` · `::test_history_is_a_flag_and_the_daily_sweep_does_not_run_it` · `::test_history_cli_entrypoint_passes_argv` · `::test_history_rc_is_zero_once_anything_was_measured` |

⚠️ 못 보는 축(#274): 캐시 파일 날짜는 서버 로컬 날짜이고 내용은 **그날 마지막 수집본**이라 첫 등장은
하루 오차가 있다. ECOS 한국 GDP 가 국제비교 표(902Y015)라 한은 속보보다 늦게 실리는지는 이 도구의
VM 출력이 답한다 — 재기 전에는 원천을 바꾸지 않는다(#151·#345).

뮤테이션 29종(관세청 9 · 매크로 스냅샷 6 · FRED 캐시 4 · `--history` 10)이 전부 겨냥한 테스트에
잡혔고 복원 md5 가 일치했다. 처음엔 하나(FRED 캐시 파일명 정규식을 느슨하게)가 살아남았다 —
glob 의 `{sid}_` 가 이미 다른 계열을 막아 정규식이 따로 가르는 경우가 없었다(#291 발화 경로 없는
가드). 이름을 구조로 잘라 가운데가 날짜인지만 보게 하고, 날짜가 아닌 이름(`PCEPILFE_backup.json`)
픽스처로 그 검사가 실제로 발화하게 했다.

## #416 — 배포 전 독립 리뷰 반영: 비밀값 로그 가림 · 달 누락 · 단위 미대조 · 형제 화면 (`tests/test_log_redaction_20260925.py` 10건 + 관세청 12건 + 느린 카드 6건 · 2026-09-25)

`2a0b3da..217aace` 독립 리뷰가 High 1 · Medium 6 · Low 13 을 냈다(Blocking 0). High 는 비밀값이었다 —
관세청 클라이언트는 **사유 문자열**에서 키를 지웠지만 httpx 가 매 요청 URL 을 INFO 로 찍어
(`HTTP Request: GET …?serviceKey=…`) 6시간마다 봇 저널로 갔고, 옛 테스트는 사유만 재서 통과했다(#54).
FRED(`raise_for_status` 예외 문구의 URL 속 `api_key=`)·ECOS(경로 속 키)도 같았다. 키를 건네는 단일
헬퍼(`bot.env_keys.env_key`)가 값을 기억하고 **레코드 팩토리 하나**가 모든 로그 레코드에서 가린다 —
로거마다 필터를 다는 목록은 다음 클라이언트를 못 잡는다(#24). 로그 레벨은 안 건드린다(#2 watchdog).

| 축 | 무엇을 재나 | 테스트 |
|---|---|---|
| ① 비밀값 로그 가림(H1·L12) | `env_key` 가 건넨 값(16자 이상)을 두 반환 경로(환경·`.env`) 모두 기억 · 모든 로거의 메시지에서 원문·퍼센트 인코딩(대·소문자)·`quote` 모양을 가림 · httpx 가 **스스로 찍는** 요청 줄을 전송 계층 스텁으로 실제 경로로 태우고 그 줄이 실제로 찍혔음도 본다(#20·#54) · 이미 인코딩된 키(`%`)는 URL 에 그대로 실려 그대로 가려진다 · 트레이스백(`log.exception`)도 · FRED 실패 로그 배선 · 비밀값 없는 레코드는 `msg`·`args` 까지 그대로(가림이 다른 로깅을 안 바꾼다) · 16자 미만은 기억 안 함 · 팩토리는 한 겹 | `tests/test_log_redaction_20260925.py` 전체 10건 |
| ② 관세청 클라이언트(M1·M4·M5·L1~L3·L6·L7) | 본문은 **자르기 전에** 가린다 — 키가 160·60자 경계에 걸쳐도, 다른 인코딩으로 되읊은 `serviceKey=` 가 5자만 남아도(#350) · **가운데·앞이 빈 계열은 완전본이 아니다** — 굽지 않고 `달 누락` 으로 ECOS 통째 폴백, 꼬리(아직 안 나온 최신 달)만 빈 건 정상(#280·#25) · ECOS 와 대조 못 한 날 카드가 `· ECOS 대조 못 함`, 대조된 날은 안 붙는다(#54·#25) · `SCALE`(USD→억$)·`RATIO_TOL`(3% — 5% 거부·2% 수용)을 리터럴로(#66) · `_http_get` 의 파라미터 이름·창·인코딩 키 분기 · 관세청 모듈의 예외는 `내부 오류`(❌, 옛 `조회 실패` 계약을 다시 썼다 #222) · 카드가 빠질 때 두 원천 이름 · 폴백 경고는 (계열, 갈래)마다 한 번 | `tests/test_customs_trade_20260925.py::test_the_body_is_masked_before_it_is_cut` · `::test_a_hole_in_the_middle_is_not_a_complete_series` · `::test_a_leading_hole_is_a_hole_too` · `::test_the_card_says_when_the_unit_was_not_cross_checked` · `::test_scale_is_usd_to_eok_literally` · `::test_the_ratio_tolerance_is_three_percent` · `::test_http_get_sends_the_window_and_the_key` · `::test_customs_series_exception_path_names_no_source_when_ecos_is_empty` · `::test_drop_reason_names_both_sources_for_customs` · `::test_the_fallback_warning_is_said_once_per_kind` · `::test_cadence_stale_covers_customs_like_ecos` |
| ③ 감사 버킷(M1·L5) | 단위 미대조는 ⚠️ 줄 · **일시 상태** 버킷(관세청 조회 실패·ECOS 대조 불가 — '원천 공표 지연' 과 다른 사실, #34·#292) · 폴백이 아니므로 관세청 계열의 지연은 따로 ❌ 로 센다(#45) · 처방 줄엔 판정 글자가 없다(#289) | `::test_fallback_line_marks_transient_and_ours_apart` · `::test_an_unchecked_unit_is_a_wait_and_the_staleness_still_counts` · `::test_a_transient_fallback_is_a_wait_not_a_publication_delay` |
| ④ 형제 화면·FRED(M2·M3·M6·L9·L11·L13·L7) | 글로벌 스냅샷 '핵심 지표' 도 분기 계열을 `2026 Q2` 로(한 픽스처에서 두 표면이 같은 말, #38) · YoY 경로도 헤드라인과 같은 TTL·같은 날 사본·실패 기억이고 실패가 조용하지 않다(#12) · FRED 실패는 10분 기억 — 만료되면 다시 묻고 성공하면 지운다(#178), 기억은 **캐시 파일** 단위라 다른 캐시 디렉터리는 남의 실패를 안 물려받는다(#30) · ECOS·관세청 칩은 그린 점 수(`7개월`) · 헤드라인 칩 0 은 `변동 없음`('전월 동일' 은 재지 않은 비교였다) · `--history` 의 관세청 행은 ECOS 재게시 지연을 경고 글자 없이 · 규약+유예 **당일**은 규약 안(`hi <= limit`) | `tests/test_slow_cards_20260925.py::test_the_global_fred_card_speaks_in_quarters_too` · `::test_the_yoy_path_shares_the_ttl_the_stale_copy_and_the_memory` · `::test_a_fred_failure_is_remembered_briefly` · `::test_the_ecos_chip_counts_what_was_drawn` · `::test_a_zero_span_change_says_no_change_not_previous_month` · `::test_history_reports_first_seen_against_the_cadence` · `::test_the_history_boundary_is_inclusive` |

⚠️ 못 보는 축(#274): 16자 미만 비밀값(짧은 비밀번호 — 흔한 글자를 가리는 오탐의 대가) · `print()` 출력
(진단은 `redact()` 를 거치면 같은 규칙이 된다) · `env_key` 를 거치지 않은 값(텔레그램 토큰은
`telegram_bot._TokenRedactFilter` 가 가린다) · 이 레포 밖 프로세스(`trade/` 는 urllib 이라 요청 URL 을
INFO 로 안 찍고 자체 가림 헬퍼가 있다). 관세청 지난달분이 익월 1일 몇 시부터 온전한지는 여전히
`--check` 가 잰다(리뷰 L10 — 재기 전엔 TTL 을 안 바꾼다, #307).

뮤테이션 40종(가림 10 · 관세청 11 · 매크로 스냅샷 5 · 대시보드 2 · FRED 7 · 감사 5 — 리뷰 때 살아남은 10종
포함)이 전부 **겨냥한 테스트**에 잡혔고 복원 md5 가 일치했다. 두 가림 층(자르기 전 · 사유 전체)이 겹쳐
한 층만 끄면 다른 층이 대신 막았으므로, 되읊은 조각이 5자만 남는 모양(뒤 층은 8자 이상만 본다)으로
앞 층만 막는 자리를 만들었다(#91c). 실패 기억 테스트는 캐시 디렉터리를 **바꿔서** 재야 '계열 이름으로
기억' 변형이 잡힌다.

