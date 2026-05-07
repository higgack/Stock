# Claude for Financial Services - PoC

## 설치 상태
- 마켓플레이스: `claude-for-financial-services` (user scope)
- 플러그인: `financial-analysis@claude-for-financial-services` v0.1.0 (enabled)
- 확인 명령: `claude plugin list`

## 사용 가능한 슬래시 커맨드

| 명령 | 동작 | 산출물 |
|------|------|--------|
| `/dcf [티커]` | comps로 멀티플 잡고 → DCF 모델 빌드 → 교차검증 | `.xlsx` (DCF + sensitivity) |
| `/comps [티커]` | 동종업계 4-6개사 트레이딩 멀티플 비교 | `.xlsx` (operating + valuation 통계) |
| `/lbo [티커]` | LBO 모델 (자본구조, IRR, MOIC) | `.xlsx` |
| `/3-statement-model [템플릿]` | IS/BS/CF 3대 재무제표 모델 채움 | `.xlsx` (수정된 템플릿) |
| `/competitive-analysis [티커]` | 경쟁환경/포지셔닝 분석 | 분석 노트 |
| `/debug-model [파일]` | 기존 Excel 모델의 순환참조/오류 디버깅 | 수정된 `.xlsx` |
| `/ppt-template` | 피치덱 템플릿 생성 | `.pptx` |

## 자동 로드되는 스킬 (수동으로 불러도 됨)
- `dcf-model`, `comps-analysis`, `3-statement-model`, `lbo-model`
- `xlsx-author`, `pptx-author` (Excel/PPT 작성 엔진)
- `audit-xls`, `clean-data-xls` (모델 검증/클리닝)
- `competitive-analysis`, `deck-refresh`, `ib-check-deck`
- `ppt-template-creator`, `skill-creator`

## 빠른 시작 (AAPL DCF)

새 Claude Code 세션을 열고:

```
/dcf AAPL
```

에이전트가 단계별로 묻습니다:
1. 데이터 소스 → "공개 자료 + 첨부 파일" 이라고 답하고 `poc/data/AAPL_inputs.md` 첨부
2. 가정값 (WACC, 터미널 성장률, 예측기간) → `AAPL_inputs.md` 하단의 "Suggested DCF assumptions" 그대로 쓰라고 지시
3. 시나리오 (Bear/Base/Bull) → 베이스만 빠르게
4. 산출 → `.xlsx` 모델이 현재 디렉토리에 떨어집니다

## 빠른 시작 (Comps)

```
/comps AAPL
```

피어 그룹은 `AAPL_inputs.md`에 적어둔 MSFT/GOOGL/META를 쓰라고 안내.
각 피어의 시가총액·EV·EBITDA·매출은 본인이 직접 알려줘야 합니다 (데이터 커넥터 미설치 상태).
시간이 아까우면 피어 데이터도 미리 노트에 채워두세요.

## 주의사항

### 데이터 소스
유료 MCP 커넥터(FactSet, S&P 등) 미설치. 에이전트가 "live data 가져올게" 라고 하면 막고
**수동 입력 또는 파일 첨부**로 가도록 유도해야 합니다. 그렇지 않으면 hallucinate 가능성 있음.

### 토큰 비용
- DCF 1건: 보통 50-150K 토큰 (입력 데이터가 작을수록 적음)
- 10-K 풀텍스트 첨부 시: 200-500K 토큰
- 현재 구독에서 차감, 별도 과금 없음

### 한국 종목
플러그인이 USD/미국 회계 기준으로 만들어져서 K-IFRS 처리는 약함.
한국 종목 PoC는 일단 비추, 미국 종목으로 검증 후 확장 권장.

## 알려진 이슈 (적용된 패치)

- 플러그인의 `hooks/hooks.json`이 빈 배열 `[]`로 출고되어 현재 Claude Code(v2.1.132)
  스키마 검증 실패. 캐시 파일을 `{ "hooks": {} }`로 패치함:
  `/root/.claude/plugins/cache/claude-for-financial-services/financial-analysis/0.1.0/hooks/hooks.json`
- `claude plugin update` 시 다시 깨질 수 있음. 그땐 같은 위치에 같은 내용 재기록.

## 다음 단계 후보
1. `/comps AAPL` 한 번 돌려보고 산출물 품질 평가
2. 만족스러우면 다른 plugin 추가: `claude plugin install equity-research@claude-for-financial-services`
3. 데이터 입력이 너무 번거로우면 무료 데이터 소스용 MCP 커넥터 직접 작성 (예: Yahoo Finance, FRED)
