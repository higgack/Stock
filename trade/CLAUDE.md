# trade/ (한국 수출입) — 레퍼런스북 요점

> 루트 `CLAUDE.md` 활성 규칙 전부 적용 위에, `trade/` 작업 시에만 로드되는
> HS↔수출입↔회사 매칭 세부. 상세·핀 목록·파서는 `CLAUDE_REFERENCE.md`.
> (루트에서 지연로드 이전 2026-07-10 — dart_feed 금지 규칙만 루트 잔류.)

## HS↔수출입↔회사 매칭
- 회사명 오타교정 = `mti_companies._COMPANY_TYPO`(매칭) + `price_provider._NAME_ALIASES`(가격) 양쪽
  1줄. fuzzy 자동교정 금지(운영자 확인분만). 같은회사 다른표기 = `_COMPANY_CANON`.
- catch-all HS6 과잉부착 = `_THEME_MTI_PIN` 정확 MTI6 핀. 콤마/공백이 사명 일부인 단일회사 =
  `price_provider._JOINED_COMPANY`. 결합토큰 분리 = `split_names`(회사뷰·미매칭 동일 파이프).
- DART 보강후보: 승인=`reinforce_approved.csv`, 거절=자동 거절메모리(`_build_reinforce` 가 승인 CSV
  해시변경 시 패널−승인분 자동적재 → 운영자는 승인분만). 미매칭/보강은 운영자 확인 후 등재.
(상세 매핑·핀 목록·DART revenue 파서·feed 파서 = REFERENCE.)
