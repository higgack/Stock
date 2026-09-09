# 🔋 Bollinger 보드 — 볼린저 밴드 상단 돌파 종목수로 보는 시장 에너지 (구현 계획 v2)

> 작성 2026-09-09 · v2 2026-09-09(사용자 피드백 3건 반영 — §1 돌파 정의 확정 · §3 KR 유니버스
> 원천 KIS/네이버 사다리 · §5 캡처 사용법·주의사항의 '가장 적절한' 적용).
> 사용자 요청: "Breadth전략 옆에 **Bollinger**(영어) 대시보드 — 캡처(볼린저 밴드 상단 돌파
> 종목수 시장 에너지 분석)를 실행. Daily 업데이트, 3시간 리프레시, 시총순위, 한국은 캡처대로,
> 미국은 웬만하면, 일본·중국·홍콩·대만도 가능하면. 시간이 오래 걸려도 꼼꼼히."
> 이 문서는 **계획**이다 — 실행 세션(한 단계 낮은 모델)이 이 순서대로 구현한다. 파일·함수·줄은
> base `63346d0`(2026-09-09) 실측. 실행 전 `git fetch origin <base>` + `git log origin/<base>` 로
> 그 뒤 변경(Copilot 포함)을 먼저 확인할 것(CLAUDE.md §다른 AI 에이전트). **이 계획서에 없는
> 결정은 실행자가 임의로 만들지 말고 §11 표에 적힌 기본값을 따른다.**

---

## 0. 결정 요약 (한 화면)

| 항목 | 결정 | 근거 |
|---|---|---|
| 페이지 / nav | `bollinger.html` · nav 라벨 **`🔋 Bollinger`** · Breadth전략 **바로 뒤** | 사용자 지시(영어 명칭·위치) |
| 모듈 | `bot/bollinger.py`(순수 산식·판정, 네트워크 0) + `bot/bollinger_board.py`(유니버스·수집·저장·렌더·CLI) | #38 산식 단일 출처(chart_data 와 공유) · #176 판정은 순수 함수 |
| 시장 | KR · US · JP · HK · CN_A · TW | UNIVERSAL — 시장 게이트 없음, 유니버스 원천만 시장별 |
| 유니버스 | KR = **KOSPI200 + KOSDAQ150**(KIS 마스터파일 → 네이버 → pykrx → 시총폴백 사다리, §3) · US = S&P500 · JP/HK/CN_A = 시총상위 225 · TW = 거래대금 상위 225(라벨 명시) | 기존 함수 재사용 + KR 만 신규 사다리 |
| 돌파 정의 | **종가 > 상단밴드(20, 2σ) 인 '상태'**(그날 밴드 밖에 있는 종목 수). 표에는 '🆕 신규'(전일 밴드 안 → 오늘 밖) 표기 | §1 — 캡처의 2.3% 확률 진술과 정합 · 5일 평균과 정합 |
| 갱신 | `_periodic_fred_boards` **3h 루프** + startup 스레드. **완결 세션만 시계열에 기록**, 장중 값은 '잠정' 줄로만 표시 | #40 세 상태(완결/장중/지연) · 값은 거래일당 1회 |
| 수집 | yfinance `yf.download(chunk=120, period="3mo")` 벌크 · 첫 실행만 `"1y"` 백필 | 신고저 스캔 검증 패턴 · 3h 당 16요청 |
| 저장 | `~/.tradingagents/bollinger/series_<MKT>.json` `{date: {count, new, scanned, basis}}` · 최근 25세션 덮어씀 · 부분 스캔 저장 금지 | #299 늦은 봉 정정 · #280 · #22 |
| 판정 | **추이 우선**(5일선 방향) + 수준(캡처 문턱 20/10, 비-KR 은 비율 환산) + 자기 이력 백분위 → 6칸 '에너지 국면' 표 | §5 — 캡처 주의사항 "절댓값보다 추이" |
| 활용(현금비중) | **처방하지 않는다.** 약세 연속 세션 수를 재고, 40세션(≈두 달) 이상일 때만 "원문 예시: 현금 60~70%" 문구 | 보조지표(캡처) · 늘 뜨는 문구 금지(#25) |
| 시총순 | 돌파 종목 표 = 시총 내림차순(KR=KIS 마스터 시가총액 · JP/HK/US/CN=네이버 worldstock overlay · TW=`_fetch_mcaps`) | 신고저 보드 경로 |
| 진단 | `python -m bot.bollinger_board --why [MKT]` 읽기 전용 · KR 은 사다리 각 단의 실측을 찍는다 | #252 · #264 |
| 비용 | LLM 0(한글명 번역 헬퍼는 캐시 우선 — 테스트에서 스텁) · yfinance 무료 | §7 |

---

## 1. 돌파 정의 — 왜 '종가 > 상단' 상태 계수인가 (확정)

후보 셋을 캡처 문장에 대조했다.

| 후보 | 캡처와의 정합 | 판정 |
|---|---|---|
| **A. 상태: 종가 > 상단** (그날 밴드 밖에서 마감한 종목 수) | "주가의 95.4%가 밴드 안 … 상단 돌파는 2.3% 의 낮은 확률" — 이건 **종가 분포**에서 밴드 밖에 있을 확률이다. 그 확률에 대응하는 계수가 곧 상태 계수. 5일 평균을 내는 것도 매일 독립적으로 세는 상태 계수와 맞는다. HTS 조건검색의 표준 항목(볼린저밴드 상한선 **이상/상향돌파**)도 종가·현재가 기준 | **채택** |
| B. 사건: 전일 밴드 안 → 오늘 밖(교차) | 밴드에 올라탄 채 며칠 가는 강세 종목이 첫날만 세어져 **강세장에서 count 가 오히려 줄어든다**(캡처의 강세장 예 "25, 21개"와 어긋남) | 표의 **🆕 표기**로만 |
| C. 고가 > 상단(장중 터치) | 윗꼬리(거부)까지 세어 노이즈 ↑ · 2.3% 는 종가 분포 진술이라 불일치 · 야후 일봉 고가는 KR 소형주에서 불안정 | 기각 |

- 산식: `mid = close.rolling(20).mean()`, `sd = close.rolling(20).std()`(pandas 기본 ddof=1), `upper = mid + 2·sd`. **차트 탭(`bot/chart_data.py:253-258`)과 같은 식**이라 사용자가 차트에서 눈으로 검산할 수 있다(#33·#38). ⚠️ HTS 는 표준편차 정의(모집단/표본)가 다를 수 있어 종목수가 ±1~2 다를 수 있다 — **재지 않았으므로 가이드에 "다를 수 있다"까지만** 적는다(#165).
- 장중 재계산(3h 루프가 KR 09:00~15:30 에 돌 때)은 마지막 봉이 **부분봉**이다. 그 값은 화면에 '잠정(장중)' 줄로만 보여주고 시계열에는 **쓰지 않는다**(§7). 완결 판정은 `market_timing._market_closed_today(mkt)` + `_expected_session(mkt)`(단일 출처, 복제 금지).

---

## 2. 파일 · 변경 지점 (전수)

### 신규
- `bot/bollinger.py` — **순수**: `bands(close, n=20, k=2.0) -> (mid, upper, lower)` · `breakouts_by_date(closes_by_ticker) -> {date: {count, new, scanned}}` · `avg5(rows)` · `trend(rows)` · `level_thresholds(scanned)` · `energy_phase(level, trend)` · `history_pct_rank(rows)` · `weak_streak(rows)` · `merge_series(stored, fresh, *, partial)`. 네트워크·디스크 없음.
- `bot/bollinger_board.py` — 유니버스(시장별) · 벌크 다운로드 · 완결/잠정 분리 · 시계열 파일 I/O · 시총 overlay · payload · `render_page` · `build_all` · `regenerate` · CLI `--why`.
- `tests/test_regression.py` — `TestBollingerBoard20260909`(§9).

### 수정 (같은 커밋 의무 — CLAUDE.md §Help/Dashboard 등록)
| 파일 | 지점(실측) | 변경 |
|---|---|---|
| `bot/chart_data.py` | 253-258 BB 인라인 | `from bot.bollinger import bands` 호출로 교체 — 값 동일(회귀로 고정) |
| `bot/telegram_bot.py` | `_periodic_fred_boards` Breadth try 블록(4188~4198) 뒤 | `from bot.bollinger_board import regenerate as _regen_bb` try 블록(try 분리) |
| `bot/telegram_bot.py` | startup `_breadth_strategy_initial`(4452~4461) 뒤 | `_bollinger_initial` 스레드(배포 직후 404 방지, 실수 #11) |
| `bot/telegram_bot.py` | `_HELP_TEXT` 1102 줄 📈차트보드 그룹 | `🧭Breadth 전략(…)` 뒤 ` · 🔋Bollinger(볼린저 상단 돌파 종목수·5일선 추이·에너지 국면)` — 현재 3,357/4,096 UTF-16 |
| `bot/dashboard.py` | 16028(Market cap nav) · 18757(홈 nav) | `<a href="bollinger.html">🔋 Bollinger</a>` 를 Breadth전략 **바로 뒤** |
| `bot/fred_boards.py` | 846 `_NAV` | 같은 위치 |
| `bot/scripts/board_audit.py` | 492 Breadth 섹션 뒤 | `── Bollinger 보드 — 시장별 기준일` 섹션(§8) |
| `docs/automation.md` | 22행 | 보드 목록에 **Bollinger** 추가 + ⚠️ 그 행의 주기가 아직 **"6시간"** 으로 적혀 있다(실제 `_BOARD_REGEN_HOURS=3`, 2026-08-20) → "3시간"으로 같이 고친다(#36·#55) |
| `.env.example` | 71-74 근처 | `# BOLLINGER_UNIVERSE_CAP_<MKT>=`(선택) 주석 |
| `tests/test_regression.py` | `test_all_four_boards_share_the_three_hour_loop`(30030 근처) | 다섯 보드로 **다시 쓴다**(#222, docstring 에 무엇이 왜 바뀌었는지) · `test_regen_period_text_matches_the_schedule` 파일 목록에 추가 · CSS 페이지 컬렉터(43621 근처 `breadth_strategy` 항목 뒤) `("bollinger", …)` — **돌파 표·차트·각주·잠정 줄이 실제로 그려지는 픽스처**(#91c·#299) |

### 건드리지 않는 것
`bot/dart_feed.py` 정책 · 신고저 슬롯 스캐너(`highlow_scan`) · 시장타이밍/Breadth 판정 로직(재사용만).

---

## 3. 유니버스 — 시장별 원천

### 3.1 KR = KOSPI200 + KOSDAQ150 (사다리 · 요구 충족 판정)
pykrx 는 KRX 로그인이 필요하고 `LOGOUT` 장애 이력이 있다(#187c). 키·로그인 **없이** 구성종목·시총·종목명을 한 번에 주는 원천이 있으므로 그것을 1순위로 둔다. 각 단은 **'요구를 충족했나'**(#136·#191)로 다음 단으로 넘어간다 — 충족 = KOSPI200 190~210 종목 **그리고** KOSDAQ150 140~160 종목(반대 증거 #25: 정확히 200/150 부근이어야 '구성종목'이다 — 2,600 이 나오면 전 종목을 잘못 읽은 것).

| 단 | 원천 | 방법 | 주는 것 | 확인 방법 |
|---|---|---|---|---|
| ① | **KIS 종목 마스터파일**(공식 · 키·로그인 불필요 · 이미 이 레포가 `idxcode.mst` 를 실측 파싱한 선례 — `naver_sector_client.py:344`) | `https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip` · `kosdaq_code.mst.zip` 다운로드 → KIS 공식 샘플(`koreainvestment/open-trading-api` `kospi_code_mst.py`/`kosdaq_code_mst.py`)의 **고정폭 컬럼 정의 그대로** 파싱. 코스피 = `KOSPI200섹터업종` 필드가 `'0'`이 아닌 행 = KOSPI200 구성종목 · 코스닥 = `KOSDAQ150` 필드 `'Y'`. 같은 행의 `시가총액`(억)·`상장주수`·한글명·`그룹코드`(ST=주식만) 도 온다 | 구성종목 + **시가총액 + 종목명 + 우선주/스팩 판별** — KR 에 필요한 전부를 HTTP 2건으로 | 파싱 결과 개수(200/150 부근) · `--why KR` 이 각 파일의 행수·매칭수·표본 3행을 찍는다. ⚠️ 컬럼 폭은 **샘플 코드에서 복사**하고 손으로 다시 세지 말 것(#155). 7일 디스크 캐시(`bollinger_kr_universe_v1.json`, 파서 지문 포함 #119) |
| ② | **네이버 모바일 API**(무키) | `https://m.stock.naver.com/api/index/KPI200/enrollStocks?page=N&pageSize=100` · `.../index/KQ150/enrollStocks`(코드 표기가 `KOSPI200`/`KOSDAQ150` 일 수도 있어 **둘 다 시도**). 헤더·봉투 처리는 `naver_ranking_client._get_stocks` 규약(`result.stocks` 폴백 포함) 재사용 — 봉투가 다르면 `--why` 가 **원문 앞 500자**를 찍는다(#109) | 구성종목 + 시총(`marketValue`) + 한글명 | 개수 판정 동일. ⚠️ 샌드박스는 네이버가 차단(http=000 실측 2026-09-09)이라 **VM 에서만** 확인 가능 — 실행자는 `--why KR` 로 ①②를 나란히 찍어 교차 대조(두 원천이 190개 이상 겹치면 ✅) |
| ③ | pykrx | `stock.get_index_portfolio_deposit_file("1028")`(KOSPI200) · `("2203")`(KOSDAQ150) — `hasattr` 로 존재 확인 후 호출(#151) · `krx_login_ready()` 게이트 | 구성종목만(시총은 `_fetch_kr_bulk`) | 개수 판정 |
| ④ | 시총 폴백 | `stock_screener._fetch_kr_bulk()` + `screener_precompute.top_by_mcap` 를 `.KS` 200 / `.KQ` 150 로(접미사는 `intl_highlow._kr_full_universe()` 로 붙인다) | — | 라벨 **"지수 구성종목 조회 실패 — 시총상위 200/150 폴백"** (#43) |

- 결과 dict: `{ticker(.KS/.KQ): {name, mcap_eok, index: "KOSPI200"|"KOSDAQ150", rung: 1..4}}` — 화면·`--why` 가 **어느 단에서 왔는지** 적는다(#136 폴백은 알린다).
- 우선주·스팩·ETF 는 구성종목 원천이 이미 제외한다(지수 자체가 보통주). ④ 폴백만 `스팩` 이름 제외(`_kr_full_universe` 규약).

### 3.2 그 외 시장 (전부 기존 함수)
| 시장 | 유니버스 | 원천 | 종목명 | 시총 | 크기 |
|---|---|---|---|---|---|
| US | S&P500 | `finviz_client._us_universe_robust()` | `_sp500_names()` | `_naver_worldstock_overlay(rows,"US",overlay_name=False)` | ~503 |
| JP | 시총상위 225 | `stock_screener._get_jp_universe()`(JPX 상장목록 → worldstock 시총 캡, 7일 캐시) | `intl_universe.full_universe_names("JP")` | overlay `"JP"` | 225 |
| HK | 시총상위 225 | `_get_hk_universe()` | `full_universe_names("HK")` | overlay `"HK"` | 225 |
| CN_A | **CSI300 구성종목 전체(300)** — KR 처럼 '지수 구성종목' 기준 | `akshare_client.list_csi300_500()` 에 `symbols=("000300",)` 키워드 인자를 **추가**(기본값은 종전 `("000300","000905")` 그대로 → 기존 호출부 무변경, 캐시 키는 symbols 로 갈라 `csi300.json`) · 실패(AKShare 미설치·차단) 시 종전 `_get_cn_universe()`(시총상위 225) 폴백 + 라벨 | 같은 호출의 中文명 | overlay `"CN_A"` | 300 |
| TW | **거래대금 상위 225**(라벨 명시) | `_get_tw_universe()` → (tickers, names) | 같은 호출 | hit 만 `_fetch_mcaps`(`fast_info_ok()` 존중) → 실패 시 `_persist_mcap_overlay` | 225 |

- 캡 env `BOLLINGER_UNIVERSE_CAP_<MKT>`(선택) — `intl_highlow._universe_cap` 규칙(시장별 > 공용 > 기본).
- **왜 시장마다 유니버스 정의가 다른가(사유 — commit body 에도)**: 캡처의 '지수 구성종목' 원칙을 **원천이 주는 시장은 그대로**(KR KOSPI200+KOSDAQ150 · US S&P500 · CN_A CSI300), 안 주는 시장은 가장 가까운 대체로 — JP 는 니케이225 구성종목의 **검증된 무키 원천이 레포에 없어**(추측 URL 금지 #12) JPX 공식 상장목록의 시총상위 225(개수를 니케이225 에 맞춤) · HK 는 HSI 가 ~80 종목뿐이라 계수 지표로 너무 얇아 시총상위 225 · TW 는 0050 이 50 종목이라 같은 이유로 거래대금 상위 225. 유니버스 정의는 **카드 라벨이 항상 말한다**(#34). 계산·판정·저장·감사는 6시장 **완전 동일 경로**(시장 게이트 없음).
- 유니버스가 비면 그 시장 카드에 **사유**(원천 부재는 ❌ 가 아니다 #260), 다른 시장은 그대로(Breadth `build_all` 패턴).

---

## 4. 수집 · 계산 · 저장

### 4.1 벌크 다운로드(신고저 `_compute_highlow_from` 패턴 그대로)
- `yf.download(chunk, period, interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)` · 120 배치 · 배치 간 0.2s · `_seen` 누락분 40배치 1회 재시도 · `yf_paused()` 면 스킵(기존 페이지 유지).
- `period`: 평소 `"3mo"`(≈60봉 = 20봉 BB + 5일 평균 + 재계산 창 25 + 여유). **시계열 파일이 없거나 60행 미만이면 `"1y"` 백필** 1회.
- 행 단위 `dropna(subset=["Close"])`(컬럼별 dropna 금지 — 신고저 주석). 분모 `scanned` = 20봉 이상 **그리고 최근 2세션 안에 봉이 있는** 티커(`recent_ref` 가드로 정지·휴면 제외).
- `scanned/universe < 0.7` → `partial=True`: 시계열 **저장 안 함** + 화면 '부분 스캔 N/M' 배지(#280).
- ⚠️ KR yfinance 절단(#122·#136)은 `period` 키워드 질의라 덜하지만, 배치에서 20봉 미만인 KR 티커가 유니버스의 10% 를 넘으면 `--why` 가 그 티커들을 나열한다(원천 문제인지 우리 문제인지 다음 라운드가 가르게).

### 4.2 일별 계수 (`bot/bollinger.breakouts_by_date`, 순수)
- 티커별 `bands()` → `above = close > upper`(불리언) → 날짜별 `count = Σ above` · `new = Σ (above & ~above.shift(1))` · `scanned = Σ upper.notna()`. DataFrame concat 후 축 합(1,750종목 × 60행 = 밀리초).
- **분모는 날짜마다 다르다** → 날짜별 scanned 저장 · `pct = count/scanned`. `scanned == 0` 인 날은 행을 만들지 않는다(`or 0` 로 나누지 말 것 #317).

### 4.3 완결 / 잠정 분리 (§1 결정의 구현)
- `closed = _market_closed_today(mkt)` · `expected = _expected_session(mkt)[0]`. 마지막 봉 날짜 `d_last`:
  - `d_last <= expected` → 완결. 시계열에 기록.
  - `d_last > expected`(오늘이고 아직 장중) → **잠정**: 시계열엔 `d_last` 행을 쓰지 않고 payload `provisional = {date, count, new, scanned, as_of_kst}` 로만 싣는다. 카드에 "잠정(장중 HH:MM KST 기준) N종목 — 종가 확정 후 기록" 줄.
  - 판정 불가(`closed is None`) → 완결로 취급하되 배지가 '판정 불가'를 말한다(#54).

### 4.4 시계열 저장·병합 (`merge_series(stored, fresh, *, partial)` 순수 + 원자적 쓰기)
- 파일 `~/.tradingagents/bollinger/series_<MKT>.json` = `{"2026-09-08": {"count": 17, "new": 6, "scanned": 348, "basis": "live"|"backfill"}, …}`(키는 **str**, #22).
- 규칙: ① fresh 의 **최근 25세션은 덮어쓴다**(늦게 온 봉 정정 #299) · 그보다 오래된 날짜는 stored 우선(유니버스가 바뀌어도 과거를 다시 쓰지 않는다) ② `partial=True` 면 stored 그대로 ③ 백필 행 `basis="backfill"`, live 가 덮으면 `"live"` ④ 상한 400행 ⑤ 임시파일 + `os.replace`(#280).

### 4.5 오늘 돌파 종목 표
- 최신 **완결** 세션(잠정이 있으면 잠정도 별도 표 — '잠정' 제목) 의 `above` 티커: `{ticker, name, close, pct_chg, upper, over_pct=(close/upper−1)·100, new: bool, mcap}`.
- 시총 overlay(§3) → **시총 내림차순**, 없으면 맨 뒤 '—'. 표시 50행, 초과는 "외 N종목"(#45). 🆕 = 신규 돌파.
- 한글명: `finviz_client._backfill_korean_names(rows, tag)`(신고저와 같은 헬퍼 — 캐시 없는 이름의 LLM 번역 킥은 그 헬퍼 규약을 따른다. **테스트는 킥을 스텁**하고 호출 0 을 단언, #312).

---

## 5. 판정 — 캡처의 '사용법·주의사항'을 어떻게 적용하나 (확정)

캡처가 스스로 말한 우선순위를 그대로 따른다: **① 절댓값보다 추이 ② 선행지표가 아니라 현재 에너지 ③ 보조지표.** 문턱(20/10)은 "예시"다.

### 5.1 세 축 (전부 `bot/bollinger.py` 순수 함수 · 값으로 검증)
| 축 | 정의 | 화면 |
|---|---|---|
| **추이** (1순위) | `Δ5 = avg5 − avg5[−5세션]` · 문턱 `max(1, scanned·1%)`(KR 3.5·US 5) → ↑ 상승 / → 횡보 / ↓ 하락. 20세션 변화도 같이 적는다 | `추이 ↑ (+6.2 / +38% vs 5세션 전 · +2.1 vs 20세션 전)` |
| **수준** | `avg5` 를 캡처 문턱에 대조. KR = **20/10 그대로**. 비-KR = `round(20/350·scanned)`/`round(10/350·scanned)`(S&P500 503 → 29/14) + 라벨 "KR 예시 비율 환산 — 검증된 기준 아님" | `수준 강세 (5일선 23.4 ≥ 20)` |
| **자기 이력 백분위** | `history_pct_rank`: 저장된 세션(≥60 필요) 중 오늘 avg5 보다 낮은 비율 → "최근 N세션 중 하위 12%". 시장·유니버스 크기와 **무관한** 정규화(캡처 문턱을 못 믿는 비-KR 의 대조군) | `이력 백분위 88% (최근 240세션 중)` · 60세션 미만이면 "이력 부족(N)" |

### 5.2 에너지 국면 (수준 × 추이 6칸 — 카드 제목)
| | 추이 ↑ | 추이 → | 추이 ↓ |
|---|---|---|---|
| 강세 | **에너지 확장** | 강세 유지 | **과열 후 냉각** |
| 중립 | 회복 진행 | 중립 | 약화 진행 |
| 약세 | **바닥 회복 조짐** | **에너지 소진** | **에너지 소진** |
- 판정 불가(avg5 None·Δ5 None)면 국면 대신 **"판정 불가(사유)"**(#54·#82).
- 국면은 **현재 상태의 이름**이지 예측이 아니다 — 가이드 첫 줄에 캡처 주의사항 그대로 "선행지표가 아니라 현재 시장의 에너지".

### 5.3 활용(위험관리) 문구 — 처방 대신 사실
- `weak_streak_sessions` = avg5 ≤ 약세 문턱이 **연속**된 세션 수. 카드에 항상 `약세 연속 K세션`.
- K ≥ 40(≈두 달)일 때만 한 줄: "원문 예시 조건 충족(5일선 약세 두 달 연속) — 원문은 이때 현금 60~70% 를 예로 든다. 참고용." 조건 미충족이면 **아무 말도 안 한다**(늘 뜨는 문구는 안 재는 것 #25·#260).
- 캡처의 '다른 지표(5주선·외국인 현선물·풋콜)' 는 이 레포에 없다 — **지어내지 않고** 같은 시장의 🚦시장타이밍(분산일·FTD)·🧭Breadth 전략 링크를 카드 하단에 둔다("함께 볼 것").

### 5.4 가이드(ℹ️)에 적는 해석 지점 — 정확히 넷, 그 이상 늘리지 말 것
1. 돌파 = 종가 > 상단(상태) · 🆕 = 신규 교차 · HTS 와 ±1~2 종목 차이 가능(표준편차 정의 — 재지 않음).
2. 비-KR 문턱은 KR 예시의 비율 환산 — 검증된 기준 아님. 시장 간 count 직접 비교 금지(유니버스 크기·구성 다름).
3. 백필 구간(`basis=backfill`)은 **오늘 유니버스**로 과거를 계산한 것(생존편향) — 차트에 옅게.
4. TW 유니버스는 거래대금 상위(시총 아님).

---

## 6. 화면 (`render_page(data, now=None)`)

- 헤드: `fred_boards._BOARD_CSS` + `_BB_CSS`(이 보드 전용 — **쓰는 클래스는 전부 정의**, #201·#273) + `_theme_head()` + `_NAV` + Chart.js(`fred_boards._ensure_chartjs()` 벤더링 · `_BOARD_JS_COMMON`).
- 제목 `🔋 Bollinger — 상단 돌파 종목수로 보는 시장 에너지` · 갱신 KST · "3시간 주기 재계산 · 값은 거래일당 1회(종가 확정 후)".
- ℹ️ 가이드: 캡처 요지(정의·상단 돌파 뜻·집계·문턱 예시·주의사항 3줄) + §5.4 해석 지점 4개 + Breadth 보드처럼 "자동 신호이므로 참고용".
- 시장 섹션 6개(KR·US·JP·HK·CN_A·TW), 각 `panel`:
  - **국면 제목 줄**: `에너지 확장` 등(§5.2) + 판정 불가 사유.
  - 카드 줄: `기준일 YYYY-MM-DD` + 세션 배지(`market_timing._idx_stale(market, asof)` 재사용 — Breadth `_session_badge` 와 같은 판정) · `오늘 N종목 (scanned 중 p%) · 🆕 M` · `5일선 A` · 추이 줄 · 수준 줄(문턱 숫자 포함) · 백분위 줄 · `약세 연속 K세션` · 유니버스 라벨(`KOSPI200+KOSDAQ150 · 348/350 스캔 · KIS 마스터` / `S&P500 503` / `시총상위 225` / `거래대금 상위 225`) · 부분 스캔 배지(있을 때만) · **잠정 줄**(장중일 때만).
  - 차트: 일별 count 막대 + 5일선(최근 120세션) + 문턱 2개 수평선 · 백필 구간 옅게.
  - 표: 오늘 돌파 종목(시총순) — 순위 · 종목(🆕) · 종가 · 등락 · 상단밴드 · 돌파폭% · 시총. 0건이면 "오늘 상단 돌파 없음(스캔 N종목)".
  - 함께 볼 것: 🚦시장타이밍 · 🧭Breadth 링크.
  - 비어 있으면 사유 한 줄(유니버스 실패 / 다운로드 전멸 / yfinance 정지 — 갈래 이름 #82).
- 푸터: "볼린저 상단 돌파 종목수 — 참고용(투자 판단 아님) · NOAH".
- 규칙 10: 모든 시각 KST · 기준일 표기 · 원시 float 금지 · `**` 마크다운 금지(#298).

---

## 7. 스케줄 · 부하

- `_periodic_fred_boards`(3h) 안 try 블록(다른 보드와 독립) + startup 스레드 1회. `.busy` 마커는 그 루프 전체가 안 쓰므로 여기서도 안 쓴다(바꾸지 않는다).
- 부하: 유니버스 ≈ 350+503+300+225×3 = 1,828 → 120 배치 16회 × 3mo / 3h. 신고저 :00 슬롯(9,358 × 1y)의 약 1/5. 첫 실행 백필(1y × 15) 1회. KR 마스터파일 HTTP 2건/7일.
- 시장 순차 · 배치 간 0.2s · 병렬화 금지(#110). `YF_PAUSE` 존중.
- **완결 세션만 기록**(§4.3) — 3h 루프가 장중에 돌아도 시계열은 흔들리지 않고, 장 마감 후 첫 사이클(최대 3h 뒤)에 그날 행이 들어간다. 장 마감 ~3h 지연은 카드 배지가 말한다(#43).

---

## 8. 진단 · 감사

### `python -m bot.bollinger_board --why [MKT]` (읽기 전용 · 진행 출력)
1. 배너 `bollinger_board --why v1 · 인터프리터(sys.executable) · 시계열 경로`(#21·#132).
2. **유니버스**: 시장별 원천 함수·개수. **KR 은 사다리 4단을 전부 시도해 나란히** 찍는다 — ① 마스터파일 행수/KOSPI200 매칭수/KOSDAQ150 매칭수/표본 3행(코드·이름·시총) ② 네이버 두 코드 표기 각각의 HTTP·개수·봉투 키(파싱 0건이면 원문 500자) ③ pykrx `hasattr` 결과·개수 ④ 폴백 개수. 그리고 ①②의 **교집합 크기**(≥190 이면 ✅ 교차 확인). 채택된 단과 사유.
3. 다운로드: 배치 수·실패·누락 재시도·scanned/universe·partial · 20봉 미만 KR 티커 목록(10% 초과 시).
4. 최근 10세션 표: date · count · new · scanned · pct · avg5 · basis.
5. 완결/잠정: `d_last` vs `_expected_session` · `_market_closed_today` · 잠정 여부.
6. 판정: 추이·수준·백분위·국면·약세 연속 — **이상 없을 때도 ✅ 한 줄 + 실측값**(#274).
- **시계열 파일을 쓰지 않는다**(`merge_series`·파일 쓰기 호출 금지 — AST 회귀, #264·#283). 진행 출력 `bot/scripts/probe_progress.stream_stdout()`(#103). 인쇄 명령은 `cd ~/stock && .venv/bin/python -m …`(#278).

### `bot/scripts/board_audit.py`
`── Bollinger 보드 — 시장별 기준일` — 6시장 `asof` vs `_expected_session` → Breadth 섹션과 **같은 마킹**(❌ 기준일 없음 / 🕒 장중 / ⚠️ N거래일 지연 / ✅). 대조 0건 ❌(#54). 판정 글자는 판정에만(#250·#268·#303).

---

## 9. 테스트 (`TestBollingerBoard20260909`) · 뮤테이션 fail-before

**순수 산식·판정**
- `bands()` == chart_data 옛 인라인 결과(60봉 픽스처, 소수 6자리) · chart_data 가 `bot.bollinger.bands` 를 **호출**(AST) — 뮤테이션: 인라인 복원.
- `breakouts_by_date`: 3티커×30봉 픽스처(특정 날 2개 위, 그중 1개는 전일도 위) → count=2 · new=1 · scanned=3 · 20봉 미만 티커 분모 제외 · NaN 봉 제외 — 뮤테이션: `>`→`>=` · new 를 count 로 · 분모에 미달 포함.
- `avg5`: 5행 미만 None+사유 / 5행 값 — 뮤테이션: 4행 허용.
- `level_thresholds(350) == (20, 10)` · `(503) == (29, 14)` · 경계(=20 강세, =10 약세, 사이 중립) — 뮤테이션: 비율 상수.
- `trend`: 문턱 `max(1, 1%)` 양쪽 경계 · 행 부족 None+사유. `energy_phase` 6칸 전수 + 판정 불가. `history_pct_rank`: 60세션 미만 → None+사유, 이상 → 값(뮤테이션: 59 허용). `weak_streak`: 연속만 센다(중간에 끊기면 리셋 — 뮤테이션: 누적 합).
- 활용 문구: streak 39 → 문구 없음 · 40 → 있음(#25 반대 증거 둘 다).

**저장·병합**
- 멱등(두 번 = 한 번) · 최근 25세션 덮어씀(늦은 봉 값이 이김) · 25 밖 stored 유지 · `partial=True` 불변 · 키 전부 str · 400 상한 · 백필→live 전환 · 원자적 쓰기(임시파일 이름 확인) — 뮤테이션: partial 무시 · 오래된 날짜 덮어쓰기.

**KR 사다리(네트워크 0 — HTTP 스텁)**
- 마스터파일 픽스처: **KIS 샘플 컬럼 폭으로 만든 합성 3행**(KOSPI200 멤버 1·비멤버 1·스팩 1) → 멤버만 · 시총·이름 파싱 — 뮤테이션: 플래그 무시(전 종목 통과 → 개수 판정이 잡는다).
- 요구 충족 판정: 개수 189/211 → 다음 단, 200/150 → 채택 · 단 번호(`rung`)가 결과에 실린다 · ① 성공 시 ②③④ **호출 0**(#191 반대 증거) · 전부 실패 → ④ + 라벨 문구.
- 네이버 봉투 두 종류(`result.stocks` / 최상위) 둘 다 파싱 · 코드 표기 두 가지 시도.

**수집기 E2E(`yf.download` 스텁, #312 — 네트워크·과금 0, 번역 킥 스텁 + 호출 0 단언)**
- 멀티레벨/flat 컬럼 둘 다 → payload count/new/scanned/asof/rows 일치 · 누락 재시도 호출 · `yf_paused` 면 다운로드 0 · 파일 없으면 `period="1y"`, 있으면 `"3mo"`(스텁 인자로) · partial 이면 파일 불변.
- **완결/잠정**: `_market_closed_today` 를 False 로 스텁 + 오늘 봉 → 시계열에 오늘 행 없음 · `provisional` 실림 · True 면 기록(#40 세 상태 — 뮤테이션: 잠정 분기 제거).
- 시총 overlay 가 시장별 **그 함수**를 부르고 표가 내림차순(시총 없는 행 맨 뒤 — 태그 걷은 텍스트 순서 #315).
- 한 시장 유니버스 실패 → 그 시장만 사유, 나머지 렌더.

**렌더**
- 픽스처(돌파 3종목·🆕 1·시계열 70행·잠정 있음·partial=False)로: 국면 제목 · 문턱 숫자(`≥ 20`) · 추이 줄 값 · 백분위 · 기준일 · 배지 스파이 `_idx_stale(market, asof)` 인자(#249) · 잠정 줄 · 🆕 · 0건 문장 · partial 배지 · 사유 줄 · 백필 라벨 · HTML escape(종목명 `<b>`) · `**`·원시 float 없음 · 함께 볼 것 링크 2개.
- CSS: 페이지 컬렉터 등록(픽스처는 표·차트·각주·잠정 줄이 **실제로 그려지는** 것).

**배선(AST/구조)**
- 3h 루프에 `bollinger_board import regenerate` · startup 스레드 · nav 3곳에서 **Breadth전략 바로 뒤**(`fb._NAV`, `dashboard.py` count ≥ 2) · `_HELP_TEXT` 차트보드 그룹 `🔋Bollinger` · 길이 < 4096 · board_audit 섹션 · `--why` 가 `merge_series`/파일 쓰기를 부르지 않음(AST) · `test_all_four_boards…` 다섯 보드로 재작성(#222) · `test_regen_period_text…` 목록 추가 · `docs/automation.md` 행에 Bollinger + "3시간".
- 전수 가드: `TestShadowedTopLevelDefs`, symtable 미정의 전역, dict 중복 키, `TestPrintedRunCommandsIncludeCd`, keyword-only bool, `or 0` 비교/나눗셈(#317), 감사 글리프, **소스 문자열 단언 금지**(#19 — 값·AST 로).

**뮤테이션 fail-before(최소 12)**: ① `>`→`>=` ② 분모 미달 포함 ③ new=count ④ partial 무시 ⑤ 25세션 밖 덮어쓰기 ⑥ 비율 상수 ⑦ avg5 4행 ⑧ 시총 정렬 제거 ⑨ 배지 인자 asof 만 ⑩ chart_data 인라인 복원 ⑪ 3h 루프 제거 ⑫ 잠정 분기 제거 ⑬ 사다리 개수 판정 제거 ⑭ streak 누적합. 통과하면 ① 돌았나(#68) ② 재는 대상(#91b) ③ 픽스처(#91c) 순. 복원은 **백업 파일**로(#139·#278).

---

## 10. 검증 · 배포 절차 (실행자)

1. 착수: `git fetch origin <base>` → dev 를 base 로 동기화(#17) · `git log origin/<base>` 확인.
2. 구현 순서: `bot/bollinger.py` 순수 전부 + 회귀 green → chart_data 교체 + 동일값 회귀 → `bollinger_board.py` KR 사다리(스텁 회귀) → 수집기(스텁 E2E, 완결/잠정) → 시계열 I/O → 렌더 + CSS 컬렉터 → 배선(루프·startup·nav·help·audit·docs) → `--why` → 뮤테이션 14종 → `python3 -m pytest tests/ -q`(샌드박스는 `make test` 불가) → 배포전 셀프리뷰(§Pre-commit 7) → `/code-review`(새 모듈·다파일 = 의무 §Pre-commit 8) → 지적은 **재고 나서** 채택(#317).
3. 커밋 body: "Rule applies to all analyses going forward / US+KR+JP+TW+CN_A+HK 적용 — 시장 게이트 없음(유니버스·시총 원천만 시장별, 사유: 원천이 시장별)".
4. 배포 = 사용자 "배포" 신호 후 ①~⑥ 체인(base grep `bollinger.html`).
5. **VM 실측 요청 1블록**(이상 없을 때의 출력을 같이 적는다, #274·#281):
   ```
   cd ~/stock && .venv/bin/python -m bot.bollinger_board --why KR | tee /tmp/bb_why_kr.txt
   ```
   기대: 배너 v1 · ② 사다리 표 — `① KIS 마스터 kospi 2,6xx행 · KOSPI200 매칭 200 · kosdaq 1,7xx행 · KOSDAQ150 매칭 150 ✅` · `② 네이버 KPI200 200 / KQ150 150 · ①∩② 197 ✅` · `채택: ① KIS 마스터` · ③ `scanned 34x/350 · partial=False` · ④ 최근 10세션 표 · ⑤ `기준일 = 마지막 완결 세션 ✅`(장중이면 `잠정 …`) · ⑥ 국면 한 줄. ①이 0건이면 컬럼 폭이 틀린 것(표본 행 원문이 찍힌다) — ②로 교차해 어느 쪽이 맞는지 가른다.
   이어 `--why US` · `--why JP` 등.
6. 화면 체크포인트(#11): `bollinger.html` 6장 카드 · 기준일 = 각 시장 마지막 완결 세션 · KR 문턱 `20/10` · 차트 탭에서 돌파 종목 하나를 열어 BB 상단 값이 표의 '상단밴드'와 **같은지**(같은 산식이므로 같아야 한다).
7. 다음 날 일일 감사에서 Bollinger 섹션 ✅ 확인(첫 실행에 ❌ 가 뜨면 가드가 켜자마자 잡은 것 — #87a).

---

## 11. 기본값으로 확정한 결정 (실행자는 이대로 — 바꾸려면 사용자에게 먼저)

| # | 결정 | 기본값(확정) | 근거 |
|---|---|---|---|
| A | 돌파 정의 | 종가 > 상단(상태) + 🆕 신규 표기 | §1 |
| B | KR 유니버스 원천 | KIS 마스터 → 네이버 → pykrx → 시총폴백(요구 충족 사다리) | §3.1 |
| C | 비-KR 문턱 | KR 예시 비율 환산 + 자기 이력 백분위 대조군 + "검증된 기준 아님" | §5.1 |
| D | 백필 | 1y 백필, `basis=backfill` 라벨(생존편향 고지) | §4.4 |
| E | TW 유니버스 | 거래대금 상위 225(기존) + 라벨 | §3.2 |
| F | 장중 재계산 | 완결 세션만 기록, 장중 값은 잠정 줄 | §4.3 |
| G | 현금비중 문구 | 처방 없음 · streak ≥ 40 일 때만 원문 예시 인용 | §5.3 |
| H | 표준편차 ddof | 차트 탭과 동일(pandas 기본) · HTS 차이 가능성만 고지 | §1 |
| I | 비-KR 유니버스 | US S&P500 · CN_A CSI300 전체 · JP/HK 시총상위 225 · TW 거래대금 상위 225 — 사유는 §3.2 | §3.2 |

---

## 12. 이 계획이 지키는 CLAUDE.md 항목 (실행자 체크리스트)
- UNIVERSAL: 시장 게이트 없음 — 유니버스·시총 원천만 시장별(commit body 사유).
- #38·#147·#176: BB 산식·세션 배지·완결 판정 단일 출처(chart_data·market_timing 재사용) · 판정은 순수 함수.
- #43·#82·#131: 비면 사유 · 갈래 이름 · 0건은 문장.
- #45: 표 50행 "외 N" · 총계=소계 같은 리스트.
- #54·#274: 진단 대조 0건 → ❌/❓ · 이상 없을 때도 ✅+실측값.
- #22·#280·#299: str 키 · 원자적 쓰기 · 부분 저장 금지 · 늦은 봉 정정.
- #40: 완결/장중/지연 세 상태.
- #136·#191: 사다리는 '요구 충족'으로 · 폴백을 탔으면 알린다.
- #20·#91·#312: 수집기 E2E(스텁) · 뮤테이션 fail-before · 테스트 네트워크·과금 0.
- #201·#273·#299: CSS 정의 + 페이지 컬렉터(그려지는 픽스처).
- #11: startup 재생성 · nav 3곳 + help 같은 커밋.
- #165·#12: 문턱 환산·HTS 차이·원천 존재를 단정하지 않고 라벨/실측으로.
- #278·#103: 인쇄 명령에 `cd` · 오래 걸리는 진단은 진행 출력.
- #25: '있다'만 묻지 말고 반대 증거(개수 190~210 · 사다리 ① 성공 시 ②③④ 미호출).
