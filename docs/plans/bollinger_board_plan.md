# 🔋 Bollinger 보드 — 볼린저 밴드 상단 돌파 종목수로 보는 시장 에너지 (구현 계획)

> 작성 2026-09-09 · 사용자 요청: "Breadth전략 옆에 **Bollinger**(영어) 대시보드 —
> 캡처(볼린저 밴드 상단 돌파 종목수 시장 에너지 분석)를 실행. Daily 업데이트,
> 3시간 리프레시, 시총순위, 한국은 캡처대로, 미국은 웬만하면, 일본·중국·홍콩·대만도
> 가능하면. 시간이 오래 걸려도 꼼꼼히."
> 이 문서는 **계획**이다 — 실행 세션(다른 모델)이 이 순서대로 구현한다. 코드 탐색
> 결과(파일·함수·줄)는 2026-09-09 base `63346d0` 기준 실측이다. 실행 전 `git log
> origin/<base>` 로 그 뒤 변경을 먼저 확인할 것(CLAUDE.md §다른 AI 에이전트).

---

## 0. 결정 요약 (한 화면)

| 항목 | 결정 | 근거 |
|---|---|---|
| 페이지 / nav | `bollinger.html` · nav 라벨 **`🔋 Bollinger`** · Breadth전략 **바로 뒤** | 사용자 지시(영어 명칭·위치). 이모지는 '시장 에너지' |
| 모듈 | `bot/bollinger_board.py`(수집·계산·저장·렌더·CLI) + `bot/bollinger.py`(순수 BB 산식, chart_data 와 **공유**) | #38 같은 계산은 단일 출처. 차트 탭 BB(20,2σ)와 한 자리도 안 갈리게 |
| 시장 | KR · US · JP · HK · CN_A · TW (6개, 시장타이밍과 같은 집합) | UNIVERSAL CHANGES ONLY — 시장 게이트 없이 유니버스만 시장별 |
| 유니버스 | KR = **KOSPI200 + KOSDAQ150**(캡처 그대로) · US = S&P500 · JP/HK/CN_A = 시총상위 225(스크리너 유니버스 재사용) · TW = 거래대금 상위 225(스크리너 재사용, **라벨에 '거래대금' 명시**) | 이미 있는 유니버스 함수 재사용(§작업 원칙 '선행 사례 먼저'). §3 |
| 갱신 | `_periodic_fred_boards` **3h 루프** + startup 스레드(다른 4보드와 동일) | `_BOARD_REGEN_HOURS=3`. 값은 일봉이라 **거래일마다 1회** 바뀜 → 기준일·세션 배지로 말한다 |
| 수집 | yfinance `yf.download(chunk=120, period="3mo")` 벌크(신고저 스캔 패턴) · 첫 실행만 `period="1y"` 백필 | `_compute_highlow_from` 검증된 패턴. 하루치 재계산에 3mo 면 충분(20봉+5일+여유) |
| 저장 | `~/.tradingagents/bollinger/series_<MKT>.json` — `{date: {count, scanned, basis}}` · 멱등 키 = 날짜, **최근 25세션은 매번 덮어씀** | #299 늦게 온 봉을 정정 · #22 JSON 키는 문자열 |
| 판정 | 캡처 그대로: **5일 평균 ≥20 강세 / ≤10 약세**(KR 350종목 기준). 비-KR 은 같은 수를 **유니버스 비율로 환산**(20/350=5.7%, 10/350=2.9%)하되 화면이 "KR 예시 환산 — 검증된 기준 아님"이라 밝힌다 | 캡처 자체가 "(예시)". #165 재지 않은 것을 단정하지 않는다 |
| 돌파 정의 | **종가 > 상단밴드**(당일) | HTS 조건검색 '상한선 돌파' 통용 정의. 유일한 해석 지점 — 가이드에 명시(Breadth 보드의 'RS 강도' 처럼) |
| 시총순 | 오늘 돌파 종목 표 = 시총 내림차순. KR=pykrx 벌크 시가총액 · JP/HK/US/CN=네이버 worldstock overlay · TW=`_persist_mcap_overlay`/`_fetch_mcaps`(hit 만) | 신고저 보드가 쓰는 그 경로 |
| 진단 | `python -m bot.bollinger_board --why [MKT]`(읽기 전용, 진행 출력) | #252 반복 확인은 제품에 · #264 진단은 상태를 안 바꾼다 |
| 비용 | LLM 0 · yfinance 무료. 3h 마다 ~1,750종목 / 120 = **15 벌크 요청**(신고저 :00 슬롯 9,358 의 19%) | §7 |

---

## 1. 원 전략(캡처) → 구현 규칙 매핑

| 캡처 문장 | 구현 | 비고 |
|---|---|---|
| 볼린저 밴드 = 중간선 ±2σ(20일) | `bot/bollinger.py: bands(close, n=20, k=2.0)` — `rolling(20).mean()` / `rolling(20).std()`(pandas 기본 ddof=1). **chart_data.py:253-258 을 이 함수 호출로 교체** | 차트 탭과 값이 같아야 사용자가 눈으로 검산(#33·#38) |
| 상단 돌파 = 2.3% 확률의 강한 상승 | `close_t > upper_t` | 종가 기준(해석 지점, 가이드 명시) |
| 코스피200·코스닥150 대상 | KR 유니버스 = 지수 구성종목(§3 KR) | 폴백 = 시총상위 200/150(라벨에 '폴백' 명시) |
| 매일 집계 | 세션마다 1행 `{date, count, scanned}` 누적 | 3h 루프는 재확인일 뿐 — 값은 거래일당 1회 |
| 일별 종목수 + 5일 평균 | `count`, `avg5 = 최근 5세션 count 평균`(5행 미만이면 None + 사유) | #54 대조 부족은 판정 불가 |
| 강세 ≥20 · 약세 ≤10 (5일 평균, 예시) | `energy_label(avg5, scanned, market)` → 강세/중립/약세 + 문턱 값 표기 | 비-KR 비율 환산(§5) |
| 약세 두 달 연속 → 현금 60~70% | **처방하지 않는다.** '약세 연속 거래일 수'만 사실로 적는다 | Breadth 보드의 F&G 처럼 판정에 안 쓰는 표시 |
| 다른 지표와 보조 사용 | 시장타이밍(분산일·FTD)·Breadth 보드 링크를 가이드에 둔다 | |
| 선행지표 아님 · 추이가 중요 | `trend = avg5 vs 5세션 전·20세션 전`(↑/→/↓ + 차이) · 차트(일별 막대 + 5일선) | 절댓값 라벨보다 추이 줄을 위에 |
| HTS 조건검색으로 찾을 수 있다 | 오늘 돌파 종목 표(시총순) | |

---

## 2. 파일 · 변경 지점 (전수)

### 신규
- `bot/bollinger.py` — 순수 산식만. `bands(close: pd.Series, n=20, k=2.0) -> (mid, upper, lower)` · `upper_breakouts_by_date(closes_by_ticker: dict[str, pd.Series]) -> dict[date, dict(count, scanned)]`. **네트워크·디스크 없음**(단위테스트·뮤테이션 대상).
- `bot/bollinger_board.py` — 유니버스 → 벌크 다운로드 → 일별 카운트 → 시계열 병합 → payload → 렌더 → `regenerate()` · CLI `--why`.
- `tests/test_regression.py` 에 클래스 `TestBollingerBoard20260909`(§9).

### 수정 (같은 커밋 의무 — CLAUDE.md §Help/Dashboard 등록)
| 파일 | 지점 | 변경 |
|---|---|---|
| `bot/chart_data.py` | 253-258 BB 인라인 | `from bot.bollinger import bands` 로 교체(값 동일 — 회귀로 고정) |
| `bot/telegram_bot.py` | `_periodic_fred_boards`(4188 근처 Breadth try 블록 뒤) | `from bot.bollinger_board import regenerate as _regen_bb` try 블록 추가(try 분리) |
| `bot/telegram_bot.py` | startup 스레드(4452 근처 `_breadth_strategy_initial` 뒤) | `_bollinger_initial` 스레드 추가(배포 직후 404 방지 — 실수 #11) |
| `bot/telegram_bot.py` | `_HELP_TEXT` 1102 줄 📈차트보드 그룹 | `🧭Breadth 전략(…)` 뒤에 ` · 🔋Bollinger(볼린저 상단 돌파 종목수·5일평균·시장 에너지)` — 현재 3,357/4,096 UTF-16 (여유 739) |
| `bot/dashboard.py` | 16028(Market cap 페이지 nav) · 18757(홈 `_render_market_page` nav) | `<a href="bollinger.html">🔋 Bollinger</a>` 를 Breadth전략 **바로 뒤**에 |
| `bot/fred_boards.py` | 846 `_NAV` | 같은 위치 |
| `bot/scripts/board_audit.py` | 492 Breadth 섹션 뒤 | `── Bollinger 보드 — 시장별 기준일` 섹션(§8) |
| `docs/automation.md` | 22행 FRED 계열 보드 | "PPI/CPI/유동성/시장타이밍/Breadth/경제캘린더/**Bollinger**" — ⚠️ 그 행의 주기가 아직 **6시간**으로 적혀 있다(실제 `_BOARD_REGEN_HOURS=3`, 2026-08-20 변경). 같은 커밋에서 3시간으로 고칠 것(설명 out-of-sync = 버그, #36·#55) |
| `.env.example` | 71-74 근처 | `# BOLLINGER_UNIVERSE_CAP_<MKT>=` 주석(선택) |
| `tests/test_regression.py` | `test_all_four_boards_share_the_three_hour_loop`(30030 근처) | 다섯 보드로 **다시 쓴다**(#222, 지우지 않음) · `test_regen_period_text_matches_the_schedule` 파일 목록에 추가 · CSS 페이지 컬렉터(43621 근처 `breadth_strategy` 항목 뒤)에 `("bollinger", …)` 추가(**돌파 표·차트가 실제로 그려지는 픽스처**, #91c·#299) |

### 건드리지 않는 것
- `bot/dart_feed.py` 정책 · 신고저 스캔 슬롯(`highlow_scan`) — 부하를 거기에 얹지 않는다(별도 3h 루프).

---

## 3. 유니버스 — 시장별 원천 (전부 기존 함수)

| 시장 | 유니버스 | 원천 함수 | 종목명 | 시총(표 정렬) | 크기 |
|---|---|---|---|---|---|
| KR | **KOSPI200 + KOSDAQ150** | ① pykrx `stock.get_index_portfolio_deposit_file("1028")` / `("2203")` — ⚠️ **실호출로 존재 확인**(`hasattr`, #151 — 샌드박스엔 pykrx 없음, VM 에서 `--why KR` 이 찍는다) ② 폴백: `stock_screener._fetch_kr_bulk()` + `screener_precompute.top_by_mcap` 를 시장별(.KS 200 / .KQ 150)로 → 라벨 "지수 구성종목 조회 실패 — 시총상위 폴백". ⚠️ 벌크 키는 **접미사 없는 6자리** — yfinance 티커(.KS/.KQ)는 `intl_highlow._kr_full_universe()`(시장별 코드+한글명, 7일 캐시)로 붙인다(`market.normalize_kr_ticker_suffix` 도 가능) | `_kr_full_universe()` 한글명(벌크 `종목명` 폴백) | 벌크 `시가총액`(억) | 350 |
| US | S&P500 | `finviz_client._us_universe_robust()` | `_sp500_names()` | `_naver_worldstock_overlay(rows,"US",overlay_name=False)` | ~503 |
| JP | 시총상위 225 | `stock_screener._get_jp_universe()`(JPX 상장목록 → worldstock 시총 캡, 7일 캐시) | `intl_universe.full_universe_names("JP")` + worldstock | overlay `"JP"` | 225 |
| HK | 시총상위 225 | `_get_hk_universe()` | `full_universe_names("HK")` | overlay `"HK"` | 225 |
| CN_A | 시총상위 225 (CSI300+500 중) | `_get_cn_universe()` | `akshare_client.list_csi300_500()` 값 | overlay `"CN_A"` | 225 |
| TW | **거래대금 상위 225**(시총 아님 — 라벨 명시) | `_get_tw_universe()` → (tickers, names) | 같은 호출 | hit 만 `finviz_client._fetch_mcaps` (fast_info, `fast_info_ok()` 존중) → 실패면 `_persist_mcap_overlay` | 225 |

- 캡: `BOLLINGER_UNIVERSE_CAP_<MKT>`(선택) — `intl_highlow._universe_cap` 과 같은 규칙(시장별 키 > 공용 > 기본). 기본은 위 표.
- 유니버스가 비면 그 시장 카드에 **사유**를 적고(❌ 아님 — 원천 부재는 우리가 못 고친다 #260) 다른 시장은 그대로 만든다(Breadth `build_all` 패턴).
- ⚠️ KR 지수 구성종목 API 이름은 **추측이다** — 실행자는 VM 에서 `python -c "from pykrx import stock; print([n for n in dir(stock) if 'portfolio' in n])"` 결과를 먼저 보고 확정할 것(#25 능력은 실측). 없으면 ② 폴백을 1차로 채택하고 화면 라벨을 그에 맞춘다.

---

## 4. 수집 · 계산 · 저장

### 4.1 벌크 다운로드 (신고저 패턴 그대로)
```
for chunk in chunks(universe, 120):
    df = yf.download(chunk, period=period, interval="1d", group_by="ticker",
                     threads=True, progress=False, auto_adjust=False)
    time.sleep(0.2)
# 벌크 누락 재시도: _seen 에 없는 티커를 40개 배치로 1회 (finviz_client 1288-1305 그대로)
```
- `period`: 평소 `"3mo"`(≈60봉: 20봉 BB + 5일 평균 + 재계산 창 25세션). **시계열 파일이 없거나 60행 미만이면 `"1y"` 백필**(그 실행 1회).
- `yf_paused()` 면 스킵하고 기존 페이지 유지(신고저와 동일).
- 분모(`scanned`) = 그 날 BB 를 만들 수 있고(20봉 이상) **최근 2세션 안에 봉이 있는** 티커(정지·휴면 제외 — `recent_ref` 가드 그대로). 유니버스 대비 scanned 가 70% 미만이면 `partial=True` → 그 실행은 **시계열을 덮어쓰지 않고** 화면에 '부분 스캔' 배지(#280 부분을 완전본으로 굽지 않는다).
- ⚠️ 컬럼별 dropna 금지 — 행 단위(`dropna(subset=["Close"])`)로(신고저 주석 그대로).

### 4.2 일별 카운트 (순수 함수, `bot/bollinger.py`)
```
upper_breakouts_by_date(closes_by_ticker) ->
    {date: {"count": n_close_gt_upper, "scanned": n_with_valid_band}}
```
- 티커마다 `bands()` → `close > upper` 불리언 시리즈 → 날짜별 합. 벡터화(DataFrame concat 후 `sum(axis=1)`) — 1,750종목 × 60행이면 밀리초.
- **분모는 날짜마다 다르다**(신규상장·결측) → 날짜별 scanned 를 같이 저장. `pct = count/scanned`.

### 4.3 시계열 저장 · 병합
- 파일 `~/.tradingagents/bollinger/series_<MKT>.json` = `{"2026-09-08": {"count": 17, "scanned": 348, "basis": "live"|"backfill"}, …}`.
- 병합 규칙(`merge_series(stored, fresh, *, partial)` 순수 함수):
  1. `fresh` 의 **최근 25세션은 덮어쓴다**(늦게 들어온 봉 정정 — #299) · 그보다 오래된 날짜는 stored 우선(유니버스가 바뀌어도 과거를 다시 쓰지 않는다).
  2. `partial=True` 면 아무것도 쓰지 않고 stored 를 돌려준다.
  3. 백필로 만든 행은 `basis="backfill"` — 화면 가이드가 "백필 구간은 **오늘 유니버스**로 과거를 계산한 것(생존편향)"이라 밝힌다(#43).
  4. 상한 400행(약 1.5년) 유지.
- ⚠️ int 키 금지(#22). 원자적 쓰기(임시파일 + `os.replace`, #280).

### 4.4 파생값 (순수)
- `avg5`: 최근 5세션 count 평균. 5행 미만 → None + `avg5_reason="세션 5개 미만(N개)"`.
- `trend`: `avg5 − avg5[−5세션]`, `avg5 − avg5[−20세션]` (행이 부족하면 그 칸만 None + 사유).
- `weak_streak`: avg5 ≤ 약세 문턱이 연속된 세션 수(캡처의 '두 달 연속' 판단 재료 — 처방은 안 한다).
- `energy`: §5.

### 4.5 오늘 돌파 종목 표
- 최신 세션에서 `close > upper` 인 티커: `{ticker, name, close, pct_chg, upper, over_pct=(close/upper−1)*100, mcap}`.
- 시총 overlay(§3) 후 **시총 내림차순**, 없으면 맨 뒤 + '시총 —'. 상위 표시 50행, 초과분은 "외 N종목"(#45).
- 한글명: `_backfill_korean_names(rows, tag)`(신고저와 같은 헬퍼 — JP/HK/TW/CN 은 번역 캐시가 없으면 원어 그대로, LLM 과금은 그 헬퍼 규약을 따른다. ⚠️ 테스트에서는 그 킥을 스텁 — #312).

---

## 5. 판정 라벨 — 캡처 예시를 그대로, 비-KR 은 환산이라고 밝힌다

```
_KR_STRONG, _KR_WEAK, _KR_UNIVERSE = 20, 10, 350        # 캡처 원문
strong_th(scanned) = round(20/350 * scanned)            # KR 350 이면 정확히 20
weak_th(scanned)   = round(10/350 * scanned)            # KR 350 이면 정확히 10
energy_label(avg5, scanned) -> ("강세" | "중립" | "약세" | "판정 불가", 사유)
```
- 카드에 문턱 값을 **숫자로** 같이 적는다: `강세 ≥ 29 · 약세 ≤ 14 (S&P500 503종목 · KR 예시 20/10 을 비율 환산)` — #202 '다르다'만 말하지 말고 숫자로.
- KR 카드: `강세 ≥ 20 · 약세 ≤ 10 (캡처 예시 기준)`.
- 가이드: "문턱은 원문의 **예시**이고 비-KR 환산은 검증된 기준이 아니다. 절댓값보다 추이(5일선 방향)를 볼 것(원문)."

---

## 6. 화면 (`render_page(data, now=None)` — Breadth 보드 골격 복제 금지, 같은 공용 재료)

- 헤드: `fred_boards._BOARD_CSS` + `_BB_CSS`(이 보드 전용 — **쓰는 클래스는 전부 여기 정의**, #201·#273) + `_theme_head()` + `_NAV` + Chart.js(`fred_boards._ensure_chartjs()` 벤더링, `_BOARD_JS_COMMON`).
- 제목 `🔋 Bollinger — 상단 돌파 종목수로 보는 시장 에너지` · 갱신 시각(KST) · "3시간 주기 재계산 · 값은 거래일당 1회 갱신".
- ℹ️ 가이드(`<details class="guide">`): 캡처 요지 + 우리 해석 지점 3개(종가 기준 돌파 · 비-KR 문턱 환산 · 백필 생존편향) + TW 유니버스가 거래대금 기준인 사실 + 시장 간 count 직접 비교 금지(유니버스 크기 다름 — Breadth 보드 경고문과 같은 결).
- 시장 섹션(6개, 순서 KR·US·JP·HK·CN_A·TW) — 각 `panel`:
  - 카드 줄: `오늘 N종목 (scanned 중 p%)` · `5일 평균 A` · 라벨(강세/중립/약세 + 문턱) · `추이 ↑/→/↓ (5세션 대비 ±x · 20세션 대비 ±y)` · `약세 연속 K세션` · **기준일 YYYY-MM-DD** + 세션 배지(`market_timing._idx_stale(market, asof)` — Breadth `_session_badge` 와 같은 판정 재사용, 복제 금지) · 유니버스 라벨(`KOSPI200+KOSDAQ150 · 348/350 스캔` / `S&P500` / `시총상위 225` / `거래대금 상위 225`) · 부분 스캔 배지(있을 때만).
  - 차트: 일별 count 막대 + 5일 평균 선(최근 120세션) · 문턱 2개 수평선. 백필 구간은 색을 옅게(basis).
  - 표: 오늘 돌파 종목(시총순) — 순위 · 종목 · 종가 · 등락 · 상단밴드 · 돌파폭% · 시총. 0건이면 "오늘 상단 돌파 없음(스캔 N종목)" — **빈 표 대신 문장**(#43).
  - 비어 있으면 사유 한 줄(유니버스 실패 / 다운로드 전멸 / yfinance 정지 등 — 갈래 이름으로, #82).
- 푸터: "볼린저 상단 돌파 종목수 — 참고용(투자 판단 아님) · NOAH".
- 표기 규칙: 모든 시각 KST 명시 · 데이터 위젯은 기준일 표기(규칙 10) · 원시 float 노출 금지(포맷 헬퍼).

---

## 7. 스케줄 · 부하

- 루프: `_periodic_fred_boards`(3h) 에 try 블록 추가(다른 보드 실패와 독립). startup 스레드 1회. `asyncio.to_thread` — 다른 보드와 동일하게 `.busy` 마커 없음(그 루프 전체가 그렇다 — 바꾸지 않는다).
- 부하 실측 근거: 유니버스 합 ≈ 350+503+225×4 = 1,753 → 120 배치 15회 × 3mo. 신고저 :00 슬롯(US+JP 9,358 × 1y)의 약 1/5 · 3시간에 1회. 첫 실행 백필(1y × 15 배치)은 1회.
- 시장별 순차 실행 + 배치 간 0.2s(신고저와 같은 예의). 병렬화하지 않는다(#110 요청마다 새 풀 금지 — 이 루프는 이미 다른 보드와 직렬).
- yfinance 정지(`YF_PAUSE`) 존중.
- "Daily": 값은 그 시장의 마지막 완결 세션 종가에서만 바뀐다. 장중 재계산이면 부분봉이 잡히므로 **기준일 옆 배지가 '장중'이라 말한다**(#40 세 상태). 필요하면 장중엔 마지막 행을 시계열에 쓰지 않는 규칙(→ 실행자 판단: 배지로 충분하면 생략, 단 `--why` 가 그 사실을 찍어야 한다).

---

## 8. 진단 · 감사

### `python -m bot.bollinger_board --why [MKT]` (읽기 전용 · 진행 출력)
1. 배너: `bollinger_board --why v1 · 인터프리터 · 시계열 파일 경로`(#21·#132)
2. 유니버스: 원천 함수 · 종목 수 · KR 이면 지수구성 API 존재 여부(`hasattr` 결과) · 폴백 여부
3. 다운로드: 배치 수 · 실패 배치 · `_seen` 누락 재시도 수 · scanned/universe · partial 판정
4. 최근 10세션 표: date · count · scanned · pct · avg5 · basis
5. 기준일 vs `market_timing._expected_session(MKT)` · 세션 배지 · 장중 여부
6. 판정: 라벨·문턱·추이 — **이상 없을 때도 한 줄 ✅ 와 실측값**(#274 빈 출력은 정답이 아니다)
- **시계열 파일을 쓰지 않는다**(`merge_series` 호출 금지 — AST 회귀로 고정, #264·#283). 진행 출력은 `bot/scripts/probe_progress.stream_stdout()`(#103) — `--limit` 를 받는 스크립트 가드에 걸리지 않게 `--limit` 를 두지 않거나 allowlist 규약을 따른다.
- 인쇄하는 실행 안내는 `cd ~/stock && .venv/bin/python -m …` 형태(#278 가드).

### `bot/scripts/board_audit.py`
- `── Bollinger 보드 — 시장별 기준일` 섹션: 6시장 `asof` vs `_expected_session` → Breadth 섹션과 **같은 마킹 규칙**(❌ 기준일 없음 / 🕒 장중 / ⚠️ N거래일 지연 / ✅). 대조 0건이면 ❌(#54). 판정 글자는 판정에만(#250·#268·#303 — sweep 이 세지 않는 글자 금지).
- 렌더 캐시 없음(페이지 파일을 매 실행 다시 씀) → #233 류 무효화 이슈 없음.

---

## 9. 테스트 (회귀 — `TestBollingerBoard20260909`) · 뮤테이션 fail-before 목록

**순수 산식**
- `bands()` 가 chart_data 의 옛 인라인 결과와 **한 자리도 안 다르다**(고정 픽스처 60봉 → bb_u/bb_m/bb_l 값 비교) · chart_data 가 `bot.bollinger.bands` 를 **호출**한다(AST) — 뮤테이션: chart_data 인라인 복원.
- `upper_breakouts_by_date`: 픽스처(3티커 × 30봉, 특정 날 2개만 상단 돌파) → 그 날 count=2 · scanned=3 · 20봉 미만 티커는 분모에서 제외 · NaN 봉 제외 — 뮤테이션: `>=` 로 바꾸기, 분모에 20봉 미만 포함.
- `avg5` 5행 미만 → None + 사유(문구 포함) · 5행이면 값 — 뮤테이션: 4행 평균 허용.
- `strong_th/weak_th`: scanned=350 → 20/10 정확히 · 503 → 29/14 · 라벨 경계(=20 강세, =10 약세, 사이 중립) — 뮤테이션: 비율 상수 변경.
- `trend`: 행 부족 시 None+사유 · 정상 시 부호.

**저장·병합**
- 멱등: 같은 fresh 두 번 병합 = 한 번과 동일 · 최근 25세션 덮어쓰기(늦게 온 봉 값이 이긴다) · 25세션 밖은 stored 유지 · `partial=True` 면 파일 불변 · 키가 전부 `str` · 400행 상한 · 원자적 쓰기(임시파일) — 뮤테이션: partial 무시, 오래된 날짜 덮어쓰기.
- 백필 행 `basis="backfill"` · live 가 같은 날짜를 덮으면 `basis="live"` 로 바뀐다.

**수집기 E2E(네트워크 0 — `yf.download` 스텁, #312)**
- 스텁 df(멀티레벨 컬럼 · 단일 티커 flat 컬럼 둘 다) → payload 의 `count/scanned/asof/rows` 가 픽스처와 일치 · `_seen` 누락 재시도가 호출된다(호출 횟수) · yf_paused 면 다운로드 0회 · 시계열 파일이 없으면 `period="1y"`, 있으면 `"3mo"`(스텁이 받은 인자로 확인) — #20 배선은 태워야 보인다.
- 시총 overlay 가 시장별로 **그 함수**를 부르고 표가 시총 내림차순(시총 없는 행이 맨 뒤).
- 한 시장 유니버스 실패 → 그 시장만 사유, 나머지 렌더.

**렌더**
- 픽스처(돌파 3종목·시계열 30행·partial=False)로 HTML: 카드 숫자 · 문턱 숫자(`강세 ≥ 20`) · 기준일 · 세션 배지 호출(스파이로 `_idx_stale(market, asof)` 인자 확인 — #249) · 표 시총순(태그 걷은 텍스트 순서, #315) · 0건 문장 · partial 배지 · 사유 줄 · 백필 라벨 · `**`·원시 float 없음 · HTML escape(종목명 `<b>` 픽스처).
- CSS: 쓰는 클래스 전부 정의(페이지 컬렉터 등록 — 픽스처는 표·차트·각주가 **실제로 그려지는** 것, #91c).

**배선(AST/구조)**
- 3h 루프에 `bollinger_board import regenerate` · startup 스레드 · nav 3곳(`fb._NAV`, `dashboard.py` count ≥ 2) 에서 **Breadth전략 바로 뒤** · `_HELP_TEXT` 차트보드 그룹에 `🔋Bollinger` · 길이 < 4096 · `board_audit` 섹션 존재 · `--why` 가 `merge_series` 를 부르지 않는다(AST) · `test_all_four_boards_share_the_three_hour_loop` → 다섯 보드로 다시 씀(docstring 에 무엇이 왜 바뀌었는지, #222) · `test_regen_period_text_matches_the_schedule` 목록에 파일 추가.
- 전수 가드 통과 확인: `TestShadowedTopLevelDefs`, symtable 미정의 전역, dict 중복 키, `TestPrintedRunCommandsIncludeCd`, keyword-only bool, `or 0` 비교/나눗셈 문맥(#317 — `scanned or 0` 로 나누지 말 것), 감사 글리프, 소스 문자열 단언 금지(#19 — 값·AST 로).

**뮤테이션 fail-before(최소)**: ① `>` → `>=` ② 분모에 20봉 미만 포함 ③ partial 무시 ④ 25세션 밖 덮어쓰기 ⑤ 비율 상수 ⑥ avg5 4행 허용 ⑦ 시총 정렬 제거 ⑧ 배지 인자 `asof` 만 ⑨ chart_data 인라인 복원 ⑩ 3h 루프에서 제거. 각각 **테스트가 실제로 돌았는지**(#68) · 재는 대상이 맞는지(#91b) 확인. 복원은 백업 파일로(#139·#278 — `git checkout` 금지).

---

## 10. 검증 · 배포 절차 (실행자)

1. 착수: `git fetch origin <base>` → dev 를 base 로 동기화(#17). `git log origin/<base>` 로 Copilot 커밋 확인.
2. 구현 순서: `bot/bollinger.py`(+chart_data 교체 + 회귀 green) → `bot/bollinger_board.py` 순수 부분(병합·라벨·추이) + 회귀 → 수집기(스텁 E2E) → 렌더 + CSS 컬렉터 → 배선(루프·startup·nav·help·audit·docs) → `--why` → 뮤테이션 10종 → `python3 -m pytest tests/ -q`(샌드박스는 `make test` 불가 — `.venv` 없음) → 배포전 셀프리뷰(§Pre-commit 7) → `/code-review`(다파일·새 모듈 = 독립 리뷰 의무, §Pre-commit 8).
3. 커밋 body: "Rule applies to all analyses going forward / US+KR+JP+TW+CN_A+HK 적용" + 시장 게이트 없음(유니버스만 시장별 — 사유: 원천이 시장별) 명시.
4. 배포 = 사용자 "배포" 신호 후: ① syntax/회귀 ② 셀프리뷰 ③ push ④ PR ⑤ squash merge ⑥ base grep(`bollinger.html`).
5. **VM 실측(사용자에게 부탁할 확인 1블록 — 이상 없을 때의 출력을 같이 적는다, #274·#281)**:
   ```
   cd ~/stock && .venv/bin/python -m bot.bollinger_board --why KR | tee /tmp/bb_why_kr.txt
   ```
   기대: 배너 v1 · `유니버스 KOSPI200+KOSDAQ150 350종목 (지수구성 API: 있음)` 또는 `(… 없음 → 시총상위 폴백)` · `scanned 34x/350 · partial=False` · 최근 10세션 표 · `기준일 = 마지막 완결 세션 ✅`. 다른 시장은 `--why US` 등.
   그 뒤 화면 체크포인트(#11): `bollinger.html` 에 6장 카드 · 기준일이 각 시장 마지막 완결 세션 · KR 카드 문턱 `20/10` · 차트 탭에서 돌파 종목 하나 열어 BB 상단 값이 표의 '상단밴드'와 같은지(같은 산식이므로 **같아야** 한다).
6. 배포 다음 날 board_audit 결산에서 Bollinger 섹션 ✅ 확인(❌ 가 뜨면 감사가 켜자마자 잡은 것 — 정상, #87a).

---

## 11. 미확정 가정 — 실행 중 사용자에게 밝히거나 결정할 것

| # | 가정 | 기본 결정 | 대안 |
|---|---|---|---|
| A | 돌파 = **종가 > 상단** | 채택(가이드에 명시) | 고가 > 상단(장중 터치) — 더 많이 잡힌다. 사용자가 HTS 조건식으로 무엇을 쓰는지 알면 그대로 |
| B | KR 지수 구성종목 pykrx API 존재 | VM 실측 후 확정 | 폴백 = 시총상위 200/150(라벨 명시) |
| C | 비-KR 문턱 = KR 예시의 비율 환산 | 채택 + "검증된 기준 아님" | 시장별 문턱 없음(count·추이만 표시) |
| D | 백필 = 오늘 유니버스로 과거 계산(생존편향) | 채택 + `basis` 라벨 | 백필 없이 첫날부터 누적(첫 5세션은 avg5 None) |
| E | TW 유니버스 = 거래대금 상위(기존 스크리너) | 채택 + 라벨 | `_fetch_mcaps` 로 225종목 시총 조회(fast_info 225콜 — 레이트리밋 위험, 비권장) |
| F | 3h 루프 장중 재계산 시 마지막 행(부분봉) 처리 | 배지로 말하고 시계열엔 쓴다(다음 실행이 덮어씀) | 장중이면 마지막 행 저장 생략 |

---

## 12. 이 계획이 지키는 CLAUDE.md 항목 (실행자 체크리스트)

- UNIVERSAL: 시장 게이트 없음 — 유니버스·시총 원천만 시장별, 사유 = 원천이 시장별(commit body).
- #38·#147: BB 산식 단일 출처(chart_data 와 공유) · 세션 배지 재사용(복제 금지) · 문턱 함수 하나.
- #43·#82·#131: 비면 사유, 갈래 이름으로 · 0건은 문장으로.
- #45: 표 50행 초과 시 "외 N" · 총계=소계 같은 리스트.
- #54·#274: 진단은 대조 0건 → ❌/❓ · 이상 없을 때도 실측값과 ✅.
- #22·#280·#299: 시계열 str 키 · 원자적 쓰기 · 부분 결과 저장 금지 · 늦은 봉 정정.
- #20·#91·#312: 수집기 E2E(스텁) · 뮤테이션 fail-before · 테스트 네트워크·과금 0.
- #201·#273·#299: CSS 정의 + 페이지 컬렉터 등록(그려지는 픽스처).
- #201/#11: startup 재생성(배포 직후 404 방지) · nav 3곳 + help 같은 커밋.
- #165·#12: 문턱 환산·API 존재·돌파 정의를 단정하지 않고 라벨/실측으로.
- #278·#103: 인쇄 명령에 `cd` · 오래 걸리는 진단은 진행 출력.
