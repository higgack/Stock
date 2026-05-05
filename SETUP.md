# Stock Analyst Bot

텔레그램으로 티커 보내면 TradingAgents(Gemini)가 분석해서 답하는 봇.

## 구조

```
run_dryrun.py         ← 봇 없이 1회 분석 (테스트용)
bot/
├── analyzer.py       ← TradingAgents 래퍼 (재사용)
└── telegram_bot.py   ← 텔레그램 봇 메인
TradingAgents/        ← upstream 코드 (vendoring)
```

## 사용 흐름

```
사용자 → "NVDA" 전송
   ↓
봇 → "📊 NVDA 분석 시작 (1~3분)…"
   ↓
TradingAgents 실행 (Gemini Flash)
   ↓
봇 → 요약 메시지 + [📋 전체 리포트 보기] 버튼
   ↓
사용자가 버튼 클릭 → 전체 리포트 전송
```

## Phase 1: 본인 PC에서 테스트

### 1) `.env` 작성

```bash
cp .env.example .env
```

`.env` 안에 채울 값:
- `GOOGLE_API_KEY` → https://aistudio.google.com/apikey
- `TELEGRAM_BOT_TOKEN` → @BotFather 가 발급한 `@StockAnalyst_HK_bot` 토큰
- `ALLOWED_USER_IDS=435996491` (이미 기본값으로 들어있음)

### 2) 의존성 설치

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3) 봇 실행

```bash
python -m bot.telegram_bot
```

### 4) 텔레그램에서 테스트

`@StockAnalyst_HK_bot` 대화창에서:
- `/start` 입력 → 안내 메시지
- `NVDA` 입력 → 1~3분 후 분석 결과

봇 종료: `Ctrl+C`

## Phase 2: GCP Compute Engine 배포 (다음 단계)

기존 `텔레그램 요약` VM에 추가 배포할 예정. systemd 서비스로 등록해서:
- VM 부팅 시 자동 시작
- 충돌 시 자동 재시작
- `journalctl`로 로그 확인

상세 단계는 본인의 기존 VM 환경 정보 받은 뒤 작성.

## 비용 (Gemini Flash 기준)

- 1회 분석 ≈ 25k 토큰 ≈ **$0.005**
- 하루 10회 ≈ 월 ~$1.5
- 무료 등급(분당 호출 제한)으로도 일 5~10회 OK

## 주의사항

- 동시에 1건만 분석 (메모리 안전, Gemini 무료 등급 한도 보호)
- 캐시는 인메모리 — 봇 재시작 시 "전체 리포트" 버튼 만료
- 현재 미국 주식만 (yfinance)
- 한국 주식: Phase 3에서 KIS/pykrx 어댑터 추가 예정
