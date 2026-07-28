---
applyTo: "trade/**"
---

# trade/ (한국 수출입 모니터링) 작업 시 추가 규칙

루트 `.github/copilot-instructions.md` 의 규칙 전부 적용 위에, `trade/` 특화
매칭 로직을 건드릴 때만 아래를 추가로 확인하세요. 상세 매핑 표·핀 목록·DART
파서 구현은 `/CLAUDE_REFERENCE.md` 의 "트레이드 레퍼런스북" 섹션 참고.

## HS ↔ 수출입 ↔ 회사 매칭
- 회사명 오타 교정은 **두 곳에 동시 반영**해야 합니다: 매칭용
  `mti_companies._COMPANY_TYPO` + 가격조회용 `price_provider._NAME_ALIASES`.
  자동 fuzzy 매칭으로 교정하지 말 것 — 운영자가 확인한 건만 등록.
- 같은 회사의 표기가 여러 개면 `_COMPANY_CANON` 에 정규화 매핑.
- HS6 코드가 너무 광범위해서 여러 회사에 잘못 붙는 catch-all 문제는
  `_THEME_MTI_PIN` 에 정확한 MTI6 핀을 등록해 해결.
- 회사명에 콤마/공백이 포함된 단일 회사명은 `price_provider._JOINED_COMPANY`
  에 등록. 결합된 토큰을 분리해야 하는 경우는 `split_names` 사용(회사뷰와
  미매칭 목록 양쪽에서 동일하게 적용됨).
- DART 데이터 보강 후보는 `reinforce_approved.csv` 에 승인된 것만 반영됩니다.
  거절된 후보는 자동으로 거절 메모리에 쌓이고, `_build_reinforce` 가 승인 CSV
  해시 변경을 감지하면 패널에서 승인분만 자동 반영합니다. 미매칭/보강 후보는
  운영자 확인 없이 임의로 등재하지 마세요.

## 이 서브프로젝트 전용 배포 참고
- `trade/` 도 루트와 같은 base 브랜치(`claude/stock-trading-automation-xqYf7`)
  를 추적하지만, VM 상에서는 별도 체크아웃(`~/stock-trade`)으로 독립 배포되어
  두 봇이 서로 다른 프로세스로 재시작됩니다. 한쪽만 고쳤다고 다른 쪽도 배포됐다고
  가정하지 마세요.
