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
| `test_*.py` 를 담은 **모든 트리**가 `make test` 안에 있다(#370·#24·#54) | ✅ 자동 | `…::test_every_test_tree_is_inside_the_commit_gate` |

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
| nav 순서 = 거래량 상위 → NXT 급등·급락(단일 출처·폴백 동일) | ✅ 자동 | `…::test_nav_order_puts_volume_before_the_nxt_movers_board` |
| 라우트·no-cache·라이브 폴링 배선(#20) | ✅ 자동 | `…::test_route_and_live_polling_are_wired` |
| 서버 TTL < 화면 폴링 주기(#36 — 아니면 '2분 갱신' 이 거짓) | ✅ 자동 | `…::test_volume_cache_ttl_is_shorter_than_the_poll_interval` |
| 네이버증권은 **nav 전용**이고 그 축이 조용히 커지지 않는다 | ✅ 자동 | `…::test_naver_stock_is_nav_only` |
| KR 자식 링크 수 = nav 레지스트리 탭 수(옛 `== 5` 스냅샷 대체, #222) | ✅ 자동 | `TestNaverWidgetSilence20260911::test_reason_branch_keeps_the_child_page_links` |

⚠️ 못 보는 축(#274): **KRX 애프터마켓 블록의 체결 귀속**은 여전히 미측정이다 —
네이버 `overMarketPriceInfo` 가 어느 거래소 체결인지 모른다. 단 "시간외 블록이
하나뿐이고 응답에 거래소를 **이름으로** 가르는 필드가 없다"는 2026-09-17 프로브
실측으로 확정됐다 → 아래 §venue 축 실측 절. 화면은 그 절반만 사실로 적는다.

### KRX 애프터마켓 보드 (2026-09-16, 실수 #371)
`tests/test_regression.py::TestKrxAfterMarketBoard20260916`

| 계약 | 상태 | 테스트 |
|---|---|---|
| 두 거래소가 캐시·상태 파일을 공유하지 않는다(#45) + 오타는 거부(#82) | ✅ 자동 | `…::test_two_venues_never_share_a_cache_file` |
| KRX 창은 NXT 보다 20분 늦게 열린다 · KRX 엔 프리마켓 없음 | ✅ 자동 | `…::test_krx_window_starts_20_minutes_after_nxt` |
| 수집기를 태워 **자기 venue 파일에만** 쓴다(#20) | ✅ 자동 | `…::test_scan_writes_to_its_own_venue_files` |
| 한 거래소 스캔이 다른 거래소 갱신을 막지 않는다 | ✅ 자동 | `…::test_one_venue_refresh_does_not_block_the_other` |
| 화면이 **재지 않은 귀속**을 보이는 줄로 밝힌다(#43·#165·#228) | ✅ 자동 | `…::test_page_states_what_it_is_actually_measuring` |
| KRX 패널 제목에 '장전' 이 없다(없는 세션 금지) | ✅ 자동 | `…::test_krx_page_never_says_pre_market` |
| 렌더러 합침 뒤에도 NXT 보드는 자기 창·제목(#222) | ✅ 자동 | `…::test_nxt_board_keeps_its_own_window_and_labels` |
| nav 순서 = 거래량 상위 → KRX 장후 → NXT 급등·급락 | ✅ 자동 | `…::test_nav_order_krx_after_sits_between_volume_and_nxt` |
| 라우트·no-cache·폴링·워머 배선(#20) | ✅ 자동 | `…::test_krx_route_and_polling_are_wired` |

⚠️ 못 보는 축(#274): **네이버 시간외 블록의 체결 귀속**(KRX인가 NXT인가)은
여전히 미측정이다. 2026-09-17 프로브가 답한 것은 **절반**(같은 블록을 본다)이고
그건 화면 문구에 반영됐다 → 아래 §venue 축 실측 절. 화면은 남은 절반을
**보이는 줄**로 밝힌다(#43·#165) — 라벨을 지어내지 않는다.

### KR 보드 독립 리뷰 반영분 (2026-09-16, 실수 #371)
`tests/test_regression.py::TestKrBoardsReviewFixes20260916`

| 계약 | 상태 | 테스트 |
|---|---|---|
| B1 겹치는 창에서 **한 번만 스캔**하고 열린 venue 전부에 쓴다(#61·#280) | ✅ 자동 | `…::test_overlapping_venues_share_one_scan` |
| B1 세션이 다른 venue 에는 안 쓴다(창이 닫혔으면 남의 결과다, #45) | ✅ 자동 | `…::test_sharing_only_when_the_session_matches` |
| H2 필터 **전에** 넉넉히 받아 요청한 수를 채운다 + 제외 수를 말한다(#148·#45) | ✅ 자동 | `…::test_volume_board_overfetches_so_the_filter_does_not_shrink_it` |
| H3 원천이 sortType 을 검증 안 하면 **미끼 응답 행을 쓰고** partial 로 밝힌다 | ✅ 자동 | `…::test_probe_rows_are_used_instead_of_a_blank_board` |
| H4 배너·창이 **venue 에서** 온다('NXT' 리터럴 금지, #38·#55) | ✅ 자동 | `…::test_krx_banners_and_window_come_from_the_venue` |
| H5 창 문구가 프리마켓 08:00–09:00 을 **안 잘라먹는다**(#43) | ✅ 자동 | `…::test_window_label_matches_the_scanner_window` |
| M7 네이버 일시정지는 실패가 아니라 **판정 보류**(#279·#345) | ✅ 자동 | `…::test_health_row_is_registered_and_pause_is_not_a_failure` |
| M8 원천이 막히면 **직전 저장분**을 💾 로 밝혀 내보낸다(#306·#335) | ✅ 자동 | `…::test_fetch_failure_falls_back_to_the_snapshot_and_says_so` |
| M9 부분·실패 결과는 **캐시하지 않는다** + 사유를 모아 잇는다(#280·#207) | ✅ 자동 | `…::test_partial_scan_is_not_cached_and_names_its_own_reason` |
| L14 칼럼 순서가 네이버와 같다(거래량·거래대금 → 고가·저가) | ✅ 자동 | `TestKrVolumeAndSessions20260916::test_page_draws_naver_columns_and_shows_the_reason` |
| L15 거래대금·체결강도 류를 거래량 정렬로 고르지 않는다(#34) | ✅ 자동 | `…::test_volume_sort_rejects_lookalike_keys` |
| L16 재학습은 **옛 키를 먼저 지운다**(죽은 키가 30일 살아남지 않게) | ✅ 자동 | `…::test_relearn_clears_the_dead_key` |
| L17 `/krafter` 폴링은 형제 `/krprepost` 와 **같은 축**(#38·#36) | ✅ 자동 | `TestKrxAfterMarketBoard20260916::test_krx_route_and_polling_are_wired` |
| L18 킥 스텁은 stdlib 가 아니라 모듈 로컬 `_spawn` 에(#30) + 스레드 시작 실패가 venue 를 영구 잠그지 않는다 | ✅ 자동 | `…::test_failed_thread_start_does_not_wedge_the_venue` |

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

| 계약 | 강제 | 테스트 |
|---|---|---|
| 겹침은 **우리 한계**로 적고 "엔 같은 값"(시장 단정)은 금지 | ✅ 자동 | `test_overlap_is_stated_as_our_limit_not_a_market_fact` |
| 거래소 무필터·부분집합 사실을 두 줄 모두에 | ✅ 자동 | 같은 테스트(KRX·NXT 순회) |
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
  (그래서 ❌ 가 아니라 ❓ 로 찍는다, #54).

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
