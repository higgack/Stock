# FinceptTerminal — Gemini-only 최소 비용 셋업 가이드

이 문서는 [FinceptTerminal v4.0.2](https://github.com/Fincept-Corporation/FinceptTerminal)
를 **본인 PC에 prebuilt 설치 파일로 설치**하고, **Gemini API 키 하나만 사용**해서
월 비용을 거의 0원에 가깝게 운영하는 방법을 정리한 것입니다.

이 레포(`Stock`)의 텔레그램 봇과는 **완전히 별개의 프로젝트**입니다. 빌드/설치는
본인 PC에서, 가이드만 여기 보관합니다.

---

## 0. 비용 요약

| 항목 | 비용 | 비고 |
|---|---|---|
| FinceptTerminal 본체 | $0 | AGPL-3.0, 개인용 |
| 시세/경제 데이터 (Yahoo/FRED/World Bank/IMF/AkShare) | $0 | 무료 API |
| Gemini API (gemini-2.5-flash) | ~$0~수 달러/월 | AI Studio 무료 등급으로 시작 가능 |
| 그 외 LLM (OpenAI/Anthropic/Groq/DeepSeek 등) | $0 | **추가하지 않음** |
| 프리미엄 데이터 (Polygon/Databento/Alpha Vantage 등) | $0 | **키 입력 안 함 → 비활성** |
| 브로커 연결 (16종) | $0 | **연결 안 함** |

**결론: 평소 사용 시 월 ~$0–$5 수준.** Gemini Flash 무료 등급(분당/일일 호출
한도) 안에서 쓰면 0원에 수렴.

---

## 1. 설치 (Windows prebuilt, 권장)

### 1-1. 설치 파일 다운로드

[Releases v4.0.2](https://github.com/Fincept-Corporation/FinceptTerminal/releases/tag/v4.0.2)
에서 OS에 맞는 파일을 받습니다.

| OS | 파일 | SHA256 (검증용) |
|---|---|---|
| Windows x64 | `FinceptTerminal-4.0.2-windows-x64-setup.exe` | `30fb95ca…0314c` |
| Linux x64 | `FinceptTerminal-4.0.2-linux-x64-setup.run` | `4c88df8f…c2338` |
| macOS arm64 | `FinceptTerminal-4.0.2-macos-arm64-setup.dmg` | `65e24154…f9a046` |

(전체 해시는 레포의 `updates.json` 참고)

### 1-2. 설치

- **Windows**: `.exe` 더블클릭 → 다음 → 설치 → 시작 메뉴에서 `FinceptTerminal` 실행
- **macOS**: `.dmg` 열고 `Applications` 폴더로 드래그
- **Linux**: `chmod +x *.run && ./FinceptTerminal-4.0.2-linux-x64-setup.run`

> 소스 빌드는 비추천 — Qt 6.8.3, CMake 3.27.7, Python 3.11.9 정확한 버전 강제,
> 빌드 30분~1시간+, 디스크 수 GB. prebuilt가 훨씬 빠르고 안정적임.

---

## 2. Gemini API 키 발급 (무료)

1. https://aistudio.google.com/apikey 접속 (Google 계정)
2. **Create API key** → 새 프로젝트 또는 기존 프로젝트 선택
3. 발급된 키 복사 (`AIzaSy…` 로 시작)

**무료 등급 한도** (2026년 기준 대략, 변동 가능):
- gemini-2.5-flash: 분당 ~10 RPM, 일일 ~250 요청
- 초과 시 자동으로 거부 (요금 청구되지 않음 — billing 미연결 시)

**유료로 전환 시**: Google Cloud Console에서 billing 연결. gemini-2.5-flash 기준
입력 ~$0.075 / 1M tokens, 출력 ~$0.30 / 1M tokens. 1회 분석 25k 토큰이면 ~$0.005.

---

## 3. FinceptTerminal 안에서 Gemini만 활성화

앱 내 **Settings 화면**에서 모두 처리합니다 (별도 config 파일 편집 불필요).

### 3-1. LLM 프로바이더 — Gemini만 추가

`Settings` → `LLM Config` → `PROVIDERS` 탭

1. 좌측 패널의 `+ Add Provider` 클릭
2. 드롭다운에서 **`gemini`** 선택 → OK
3. 우측 폼 입력:
   - **API Key**: 위에서 발급받은 `AIzaSy…` 키
   - **Base URL**: 비워두면 기본값 자동 (`https://generativelanguage.googleapis.com`)
   - **Model**: `gemini-2.5-flash` (저렴 + 빠름, 권장)
     - 큰 분석에는 `gemini-2.5-pro` (비싸지만 똑똑)
4. **Save**

**중요**: OpenAI, Anthropic, Groq, DeepSeek, MiniMax, OpenRouter는 **추가하지 않습니다.**
이미 추가되어 있다면 선택 후 `Delete` 로 제거. 키가 없으면 호출 시도 자체가 안 되므로
요금 발생 가능성 0.

Ollama(로컬 LLM)는 추가해도 무료 — GPU/RAM이 충분하면 백업용으로 추가 고려 가능.

### 3-2. 데이터 소스 — 무료만 활성화

`Settings` → `Data Sources`

**유지(Enabled)**:
- Yahoo Finance — 시세, 재무
- FRED — 경제 지표 (FRED 키 필요하지만 [무료](https://fred.stlouisfed.org/docs/api/api_key.html))
- World Bank, IMF, DBnomics — 경제 데이터, 키 불필요
- AkShare — 중국 시장 데이터, 키 불필요

**비활성화(Disabled)**:
- Polygon.io, Databento, Alpha Vantage — 유료/제한적 무료
- Tiingo, IEX Cloud, Finnhub, Quandl — 유료
- Adanos Sentiment — 프리미엄

토글 OFF만 하면 됨. 키가 없으면 어차피 호출 실패하므로 실비용 0이지만,
명시적으로 OFF 해두면 의도치 않은 호출 자체를 차단.

### 3-3. Credentials — 비워두기

`Settings` → `Credentials`

다음 키들은 **모두 빈 칸으로 둡니다**:
- `ALPHA_VANTAGE_API_KEY`, `POLYGON_API_KEY`, `DATABENTO_API_KEY`
- `NEWSAPI_KEY`, `IEX_CLOUD_TOKEN`, `FINNHUB_API_KEY`, `TIINGO_API_KEY`, `QUANDL_API_KEY`
- `BINANCE_*`, `KRAKEN_*`, `POLYMARKET_*` (브로커/거래소)

**입력하는 것은 단 2개**:
- `FRED_API_KEY` (선택, [무료 발급](https://fred.stlouisfed.org/docs/api/api_key.html))
- (LLM 키는 위 LLM Config에서 처리, 여기 아님)

### 3-4. 브로커 연결 — 안 함

`Settings` → 브로커 관련 화면이 있더라도 **연결하지 않음**. 연결 자체는 무료지만,
실수로 주문이 나가면 실거래 수수료 + 손실 위험.

---

## 4. 첫 실행 검증

설치 후 다음을 확인:

1. **시세 조회**: `AAPL` 또는 `005930.KS` 검색 → 차트가 뜨면 Yahoo 연결 OK
2. **AI Chat**: 대시보드의 채팅창에서 "What is NVDA's PE ratio?" 입력
   → 응답이 오면 Gemini 연결 OK
3. **에러 로그 확인**: Settings → Logging → "OpenAI key missing" 같은 에러는
   **무시 OK** (의도한 상태)

---

## 5. 비용 모니터링

매주 한 번:

1. https://aistudio.google.com/ → 좌측 `API keys` → 사용량 확인
2. 무료 등급을 넘기 시작했다면:
   - 더 자주 쓰는 분석 → 캐시 또는 결과 저장 후 재사용
   - `gemini-2.5-flash` 고수, `pro`는 정말 필요할 때만
   - billing 연결 시 [예산 알림](https://console.cloud.google.com/billing/budgets) 설정 ($5, $10 등)

---

## 6. (Phase 2) 텔레그램 원격 구동 — 한계와 대안

> 사용자 의도: "나중에 텔레그램으로 연결해서 서버에서 돌아가게"

### 한계

FinceptTerminal은 **Qt6 데스크톱 GUI 앱**입니다. 다음이 없거나 어려움:
- 외부에서 호출 가능한 REST API/CLI 모드 — 현재 버전엔 없음
- 헤드리스(서버) 실행 — Qt GUI 의존성 때문에 X11/가상 디스플레이 필요
- 분석 결과를 외부로 export 하는 표준 인터페이스 — 부재

→ "텔레그램 → FinceptTerminal 트리거 → 결과 회신" 파이프라인을 그대로 만들기는
**현재로는 불가/비현실적**.

### 대안 3가지

**A. 현재 Stock 레포의 텔레그램 봇 활용 (가장 현실적)**
이 레포에 이미 있는 `bot/telegram_bot.py` + `TradingAgents`가 사실상 같은 일을
합니다 — Gemini로 종목 분석 후 텔레그램 회신. FinceptTerminal은 본인 PC에서
"리서치 워크벤치"로 쓰고, 봇은 봇대로 운영.

**B. 화면공유 / RDP**
FinceptTerminal을 클라우드 Windows VM에 설치하고 텔레그램으로는 RDP 링크만
보내는 방식. 비용 발생(VM 시간당 과금) + 보안 이슈.

**C. (가장 큰 작업) FinceptTerminal에 헤드리스 모드 PR 제안**
`fincept --analyze NVDA --output json` 같은 CLI를 직접 추가. C++ 코드 변경
필요, 업스트림 머지 시도. 시간 投入이 큼.

→ **권장: A**. FinceptTerminal은 PC 도구로, 텔레그램 봇은 별도로 운영.

---

## 7. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| "No LLM provider configured" | Settings → LLM Config에서 gemini 추가 안 됨 |
| Gemini 응답이 끊김 | 무료 등급 분당 한도 초과. 1분 대기 또는 billing 연결 |
| Yahoo 데이터 안 옴 | 회사망 방화벽 차단 가능 — yfinance가 query2.finance.yahoo.com 접근 가능해야 함 |
| 앱이 검은 화면 | Qt 6.8.3 설치 안 됨 또는 GPU 드라이버 문제. prebuilt 설치 파일이면 보통 자동 해결 |
| FRED 키 에러 | `FRED_API_KEY` 비워뒀거나 잘못된 값. 무료 발급 후 Credentials에 입력 |

---

## 8. 참고 링크

- 레포: https://github.com/Fincept-Corporation/FinceptTerminal
- Releases: https://github.com/Fincept-Corporation/FinceptTerminal/releases
- Discord: https://discord.gg/ae87a8ygbN
- Gemini AI Studio: https://aistudio.google.com/apikey
- Gemini 가격: https://ai.google.dev/pricing
