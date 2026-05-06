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

## Phase 2: GCP Compute Engine 배포 (systemd 24h 운영)

기존 `텔레그램 요약` VM에 추가 배포. systemd 서비스로 등록하면:
- VM 부팅 시 자동 시작
- 봇 충돌 시 10초 후 자동 재시작
- SSH 세션이 끊겨도 계속 실행 (단, **VM 자체는 켜져 있어야 함**)
- `journalctl`로 로그 확인

### 1) 유닛 파일 설치

레포의 `deploy/stock-bot.service` 를 systemd 디렉토리에 복사:

```bash
sudo cp /home/higgack/stock/deploy/stock-bot.service /etc/systemd/system/stock-bot.service
sudo systemctl daemon-reload
```

User나 경로가 다르면 복사한 파일을 직접 수정 (`sudo nano /etc/systemd/system/stock-bot.service`).

### 2) 부팅 자동 시작 + 즉시 실행

```bash
sudo systemctl enable stock-bot      # 부팅 시 자동 시작
sudo systemctl start stock-bot       # 지금 실행
sudo systemctl status stock-bot      # 상태 확인 (active (running) 이어야 정상)
```

### 3) 로그 확인

```bash
sudo journalctl -u stock-bot -f      # 실시간 follow
sudo journalctl -u stock-bot -n 100  # 최근 100줄
```

성공 신호: `bot starting — watching channels: {...}` 로그가 보이면 OK.

### 4) 운영 명령

```bash
sudo systemctl stop stock-bot        # 정지
sudo systemctl restart stock-bot     # 재시작 (코드 변경 후)
sudo systemctl disable stock-bot     # 부팅 자동 시작 해제
```

### 5) 코드 업데이트 시 워크플로

```bash
cd /home/higgack/stock
git pull
sudo systemctl restart stock-bot
sudo journalctl -u stock-bot -n 30   # 정상 기동 확인
```

## Phase 3: 자동 배포 (선택)

서버가 매 2분마다 origin 을 fetch 하여 새 커밋 발견 시 자동으로 pull + 재시작.
한 번 설치하면 이후 push 만으로 배포 끝납니다.

### 1) 봇 재시작 sudoers 권한 부여

타이머가 비대화식으로 봇을 재시작할 수 있도록, `higgack` 계정에 무비밀번호
sudo 권한을 **단 하나의 명령** (stock-bot 재시작)에 한정해 부여합니다.

```bash
echo "higgack ALL=(ALL) NOPASSWD: /bin/systemctl restart stock-bot" | \
  sudo tee /etc/sudoers.d/stock-bot
sudo chmod 440 /etc/sudoers.d/stock-bot
```

### 2) 자동 업데이트 유닛 + 타이머 설치

```bash
sudo cp /home/higgack/stock/deploy/stock-bot-update.service /etc/systemd/system/
sudo cp /home/higgack/stock/deploy/stock-bot-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-bot-update.timer
```

### 3) 동작 확인

```bash
systemctl list-timers --all | grep stock-bot-update    # 다음 실행 시각 표시
sudo journalctl -u stock-bot-update.service -n 30      # pull 실행 로그
```

`stock-bot-update.timer` 에 `Active: active (waiting)` 가 보이고,
`Trigger: 2min N seconds left` 정도면 정상.

새 커밋이 push 되면 2분 내 로그에 `pulling abc1234 → def5678` 이 찍히고,
이어서 봇이 재시작됩니다.

### 끄고 싶다면

```bash
sudo systemctl disable --now stock-bot-update.timer
```

## 비용 (Gemini Flash 기준)

- 1회 분석 ≈ 25k 토큰 ≈ **$0.005**
- 하루 10회 ≈ 월 ~$1.5
- 무료 등급(분당 호출 제한)으로도 일 5~10회 OK

## 주의사항

- 동시에 1건만 분석 (메모리 안전, Gemini 무료 등급 한도 보호)
- 캐시는 인메모리 — 봇 재시작 시 "전체 리포트" 버튼 만료
- 현재 미국 주식만 (yfinance)
- 한국 주식: Phase 3에서 KIS/pykrx 어댑터 추가 예정
