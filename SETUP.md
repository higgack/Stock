# TradingAgents Dry-run (Gemini)

미국 주식 1종목으로 동작 검증하는 최소 셋업.

## 1. Gemini API 키 발급

https://aistudio.google.com/apikey 에서 무료로 발급. (무료 등급으로 일일 충분히 테스트 가능)

## 2. .env 작성

```bash
cp .env.example .env
# .env 파일을 열어 GOOGLE_API_KEY=... 채우기
```

## 3. 의존성 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r TradingAgents/requirements.txt
pip install python-dotenv
```

## 4. 실행

```bash
python run_dryrun.py                  # 기본: NVDA 2025-12-01
python run_dryrun.py AAPL 2025-11-15  # 다른 종목/날짜
```

## 비용 가늠

- 본 설정: `gemini-2.5-flash` + `gemini-2.5-flash-lite` + thinking=minimal + debate=1
- 1회 실행 당 약 25k 토큰 → **Gemini Flash 기준 $0.01 미만**
- 무료 등급(분당 호출 제한)으로도 충분히 1회는 가능

## 다음 단계

- 잘 돌면: 한국 주식용 데이터 어댑터(KIS/키움/pykrx) 추가
- 결과가 마음에 들면: 종목 리스트 루프 + 결과 텔레그램 알림
