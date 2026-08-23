"""Collect a company info snapshot from yfinance for archive storage.

Called by ``archive.save_analysis`` — one extra yfinance ``.info`` call
per analysis (~0.5s, non-fatal). The snapshot powers the detail-page
header cards, company info section, and consensus card. All fields are
optional — missing data renders gracefully as "—".

KR tickers (.KS/.KQ) get additional data from DART + FSC (법인등록번호,
DART 대표자, CEO, 결산월, 공시, 임원지분, 소액주주, K-IFRS 재무).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import datetime, timezone

from bot.price_sanity import within_52w_range

log = logging.getLogger(__name__)

# ── 단기 스냅샷 캐시 (cold 상세 1장의 중복 수집 제거, 사용자 2026-06-29) ──────
# 측정: 한 종목 cold 페이지가 collect_stock_snapshot 을 2~3회 호출(core 렌더 →
# full 렌더 → /api/quote?full=1) — 각 호출이 yfinance .info + peers 8 + 시장
# enrich 를 통째로 다시 함(AAPL 14.7s, 삼성 8.2s). filing/일간 느린 데이터라 짧은
# 신선창이면 중복만 제거하고 정확성은 유지(라이브 가격/등락은 _QUOTE_JS 별도 갱신).
#
# ⚠️ 과거 실수(2026-06-15 '새 종목 아예 안됨')는 **공유 스냅샷을 여러 렌더가 동시
# in-place 변경**한 race 였다. 그래서 캐시는 **copy-on-read/write** — 저장도 사본,
# 읽기도 deepcopy 사본을 줘 호출부(_ensure_detail_enrichment 가 si 를 in-place
# 변경)가 절대 캐시 원본을 못 건드린다. 순차 core→full 모델은 그대로(병렬 안 함).
_SNAP_CACHE_TTL = 120.0
_SNAP_CACHE: dict[str, tuple[float, dict]] = {}
_SNAP_CACHE_LOCK = threading.Lock()


def _kr_arbiter(ticker: str) -> dict | None:
    """KR 종목의 네이버 실시간 시세 {price, mcap} — 실패하면 None(graceful).

    야후 .info 와 조정 히스토리가 크게 갈릴 때 **어느 쪽이 옳은지** 판정하는
    제3의 원천이다. 크기만으론 못 가른다 — 액면분할이면 낮은 쪽(수신가)이,
    감자·병합이면 높은 쪽(조정종가)이 맞기 때문이다."""
    if not (ticker or "").upper().endswith((".KS", ".KQ")):
        return None
    try:
        from bot.naver_quote import fetch_kr_quote
        return fetch_kr_quote(ticker)
    except Exception as exc:
        log.debug("_kr_arbiter %s: %s", ticker, exc)
        return None


def _consistent_mcap(price, shares, orig_price, orig_mcap):
    """표시가와 **같은 기준**의 시총. 못 만들면 None(그럼 시총을 비운다).

    ⚠️ 주식수가 없어도 포기하지 않는다 — 야후 marketCap 은 야후 자신의
    가격과 정합하므로 `주식수 = 시총 ÷ 수신가` 로 역산하면 보정가 기준
    시총을 만들 수 있다. 이걸 안 하면 현재가와 시총이 서로 다른 가격
    기준으로 나란히 표시된다(제닉 실측)."""
    try:
        price = float(price or 0)
        if price <= 0:
            return None
        if shares:
            return price * float(shares)
        if orig_price and orig_mcap and float(orig_price) > 0:
            return price * (float(orig_mcap) / float(orig_price))
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def collect_stock_snapshot(ticker: str, *, use_cache: bool = True) -> dict | None:
    """Company/market facts dict, or *None* on failure.

    use_cache=True(기본): 120초 copy-on-read 캐시 — cold 상세 1장의 중복 수집 제거.
    use_cache=False: 캐시 '읽기'만 건너뛰고 신선 수집(수동 🔄 force / 아카이브 저장
    시점). 단 그 신선 결과는 캐시에 '갱신' — 강제 새로고침 직후 같은-창 렌더도 신선."""
    if use_cache:
        now = time.time()
        with _SNAP_CACHE_LOCK:
            ent = _SNAP_CACHE.get(ticker)
            if ent and now - ent[0] < _SNAP_CACHE_TTL:
                return copy.deepcopy(ent[1])      # 사본 — 호출부 in-place 변경 격리
    snap = _collect_stock_snapshot_uncached(ticker)
    # 쓰기는 항상(use_cache 무관) — force/archive 의 신선 결과도 캐시 갱신해 이후
    # 읽기가 stale 을 안 보게(리뷰 finding #1). 읽기만 use_cache 로 우회.
    if snap is not None:
        now = time.time()
        with _SNAP_CACHE_LOCK:
            # 만료 항목 정리 — 장수 대시보드 프로세스에서 티커마다 누적되는 무한
            # 증가 방지(TTL 창 내 활성 티커 수로 한정). 쓰기 때만(저비용).
            for k in [k for k, (ts, _) in _SNAP_CACHE.items()
                      if now - ts >= _SNAP_CACHE_TTL]:
                del _SNAP_CACHE[k]
            _SNAP_CACHE[ticker] = (now, copy.deepcopy(snap))
    return snap


# 수집의 단계별 소요시간(초). 어느 블록을 클릭 로딩으로 뗄지 정하려면
# **추측이 아니라 실측**이 있어야 한다(사용자 2026-08-21 '로딩이 오래 걸려서').
# ⚠️ **티커별**로 가른다 — 전역 dict 하나면 탭 세 개를 열었을 때 세 종목의
# 단계가 섞여 어느 것인지 알 수 없다(2026-08-22 실측, bot/timing.py 참조).
# 스레드로컬은 못 쓴다: `kr:*`·보조 6종은 **풀 워커 스레드**가 기록한다.
from bot.timing import Stages as _Stages

_TIMING = _Stages()


def last_timing(ticker: str = "") -> dict[str, float]:
    """그 티커 수집의 단계별 초. 캐시 히트·미측정이면 빈 dict."""
    return _TIMING.snapshot(ticker)


def relay_anomaly_fields(src: dict, dst: dict) -> dict:
    """이상치 사이드채널(`_anomaly*` · `_mismatched*`)을 **전량** 릴레이.

    ⚠️ 하나라도 안 넘기면 대시보드 배지·각주가 dead code 가 되고 '—' 의
    이유가 사라진다(2026-08-16 독립 리뷰). 그래서 이름을 **열거하지 않고**
    접두어로 훑는다 — 새 플래그를 더할 때마다 목록을 고쳐야 하면 언젠가
    빠진다(#24 '목록형 가드는 새 파일을 못 잡는다' 의 필드판).
    """
    for k, v in (src or {}).items():
        if v and (k.startswith("_anomaly") or k.startswith("_mismatched")):
            dst[k] = v
    return dst


def _collect_stock_snapshot_uncached(ticker: str) -> dict | None:
    """Return a dict of company/market facts, or *None* on failure."""
    _TIMING.start(ticker)
    _t_all = time.time()
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        _t0 = time.time()
        info = t.info or {}
        # ⚠️ `.info` 는 **직렬**이다 — 보조 6종 병렬 수집보다 앞이라 이게
        # 느리면 나머지를 아무리 떼어내도 첫 화면이 안 빨라진다.
        _TIMING.set(ticker, "yf.info", time.time() - _t0)
        if not info or info.get("quoteType") is None:
            # 야후 .info 차단/실패 → None. 호출부(render_lookup_detail)가 직전 분석
            # 스냅샷으로 폴백(_load_stored_stock_info) — 야후 차단 중 상세 표시 유지.
            return None

        def _g(k, default=None):
            v = info.get(k)
            return v if v is not None else default

        snap: dict = {}

        # ── identity ────────────────────────────────────────────────
        snap["long_name"] = _g("longName") or _g("shortName") or ""
        snap["sector"] = _g("sector", "")
        snap["industry"] = _g("industry", "")
        snap["exchange"] = _g("exchange", "")
        snap["quote_type"] = _g("quoteType", "")
        snap["currency"] = _g("currency", "")
        snap["financial_currency"] = _g("financialCurrency", "")
        snap["country"] = _g("country", "")
        snap["city"] = _g("city", "")
        snap["state"] = _g("state", "")
        snap["website"] = _g("website", "")
        snap["description"] = _g("longBusinessSummary", "")
        snap["employees"] = _g("fullTimeEmployees")

        # fiscal year end — derive from lastFiscalYearEnd epoch
        fy_epoch = _g("lastFiscalYearEnd")
        if fy_epoch and isinstance(fy_epoch, (int, float)):
            try:
                dt = datetime.fromtimestamp(fy_epoch, tz=timezone.utc)
                snap["fiscal_year_end"] = f"{dt.month:02d}-{dt.day:02d}"
            except Exception:
                pass

        # listing date — from firstTradeDateEpochUtc
        ftd = _g("firstTradeDateEpochUtc")
        if ftd and isinstance(ftd, (int, float)):
            try:
                dt = datetime.fromtimestamp(ftd, tz=timezone.utc)
                snap["first_trade_date"] = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # ── market data ─────────────────────────────────────────────
        price = _g("currentPrice") or _g("regularMarketPrice")
        # 현재가 글리치 가드 (KLAC $2,411/$3.15T 2026-06-12 — yfinance 분할
        # 미조정 아티팩트가 검색 카드에 그대로 노출). 신고저 페이지의 75%
        # 드랍 가드와 동일 클래스 — CLAUDE.md price-glitch 정책: **교체
        # (직전 종가) 우선**. |1일 변동|>75% 면 직전 종가로 교체 + 시총도
        # 직전 종가×주식수로 재계산 + 보정 주석 필드.
        prev = _g("regularMarketPreviousClose") or _g("previousClose")
        shares = _g("sharesOutstanding")
        # 52주 범위 게이트 (CAST +126% / RGNT +752% 2026-06-15) — 무한도
        # 시장(US/EU/HK)의 진짜 급등은 1일 변동이 ±75%를 넘어도 52주 범위
        # 안이면 글리치가 아니다. magnitude 만으론 진짜 뉴스 무브와 분할
        # 글리치를 구분 못 하므로(CLAUDE.md in-range 신뢰 원칙), 현재가가
        # 52주 범위 안이면 글리치 가드를 통째로 건너뛴다. 52주 밖(KLAC
        # $2,411 = 52주 고가의 수배) 또는 52주 미상일 때만 magnitude·조정
        # 종가 가드를 적용 → KLAC 보호는 유지(.info 52주는 조정 기준).
        in_52w = within_52w_range(price, _g("fiftyTwoWeekLow"),
                                  _g("fiftyTwoWeekHigh"))
        # 보정 전 수신가 — 시총 역산에 쓴다(야후 marketCap 은 이 가격 기준).
        _orig_price = price
        if price and not in_52w:
            try:
                if (prev and float(prev) > 0
                        and abs(float(price) / float(prev) - 1) > 0.75):
                    snap["price_glitch_note"] = (
                        f"소스 이상치 보정 — 수신가 {float(price):,.2f} 가 직전 "
                        f"종가 대비 ±75% 초과(분할 미조정 의심) → 직전 종가로 표시")
                    price = float(prev)
                    if shares:
                        snap["market_cap"] = float(prev) * float(shares)
            except (TypeError, ValueError):
                pass
            # 2차 가드 (KLAC 잔존 2026-06-12) — info 의 price 와 previousClose
            # 가 **둘 다 같은 미조정 기준**이면 1차(상대비교)가 장님 (2,411 vs
            # 직전 2,398 → 통과). 조정 일봉 히스토리(차트와 동일 소스) 마지막
            # 종가와 교차: ±75% 초과면 조정 종가로 교체 + 시총 재산출. 차트는
            # $241 인데 헤더만 $2,411 이던 불일치의 근본 차단.
            try:
                hist = t.history(period="5d")
                if hist is not None and len(hist) and "Close" in hist:
                    hc = float(hist["Close"].dropna().iloc[-1])
                    if hc > 0 and abs(float(price) / hc - 1) > 0.75:
                        # ⚠️ KR 은 **네이버가 중재한다.** 야후 .info 와 조정
                        # 히스토리 중 어느 쪽이 옳은지 크기만으론 못 가른다 —
                        # 액면분할이면 수신가가, 감자·병합이면 조정종가가 맞다.
                        # 한국 종목은 네이버 실시간 시세가 원천이라 그쪽에
                        # 가까운 값을 채택하면 추측이 사라진다(제닉 123330:
                        # 수신 3,060 vs 조정 27,300 으로 화면에 경고가 떴다,
                        # 사용자 2026-08-17). 네이버 실패 시 기존대로 조정종가.
                        _kr = _kr_arbiter(ticker)
                        if _kr and _kr.get("price"):
                            _ref = float(_kr["price"])
                            _pick_hist = (abs(hc - _ref)
                                          <= abs(float(price) - _ref))
                            if _kr.get("mcap"):
                                snap["market_cap"] = float(_kr["mcap"])
                            price = hc if _pick_hist else float(price)
                            snap["price_glitch_note"] = (
                                f"소스 교차검증 — 야후 {float(price):,.2f} vs "
                                f"조정 종가 {hc:,.2f} 가 ±75% 초과로 갈려 "
                                f"네이버 실시간 {_ref:,.0f} 에 가까운 값 채택")
                        else:
                            snap["price_glitch_note"] = (
                                f"소스 이상치 보정 — 수신가 {float(price):,.2f} 가 "
                                f"조정 종가 {hc:,.2f} 대비 ±75% 초과(분할 미조정) "
                                f"→ 조정 종가로 표시")
                            price = hc
                        # ⚠️ 시총은 **반드시 표시가와 같은 기준**이어야 한다.
                        # 옛 코드는 `shares` 가 없으면 시총을 손대지 않아,
                        # 야후 marketCap(수신가 기준)과 화면 현재가(조정종가)가
                        # 다른 가격 기준으로 나란히 떴다 — 제닉이 현재가
                        # ₩27,300 인데 시총 ₩2,175억(≈3,060 기준)이던 원인.
                        # 주식수가 없으면 (원래 시총 ÷ 원래 가격)으로 역산한다.
                        _mc_fix = _consistent_mcap(price, shares,
                                                   float(_orig_price or 0),
                                                   _g("marketCap"))
                        if _mc_fix is not None:
                            snap["market_cap"] = _mc_fix
                            if not shares and _orig_price:
                                shares = _mc_fix / price
            except Exception:
                pass
        if price:
            snap["current_price"] = price
        if "market_cap" not in snap:
            snap["market_cap"] = _g("marketCap")
        snap["shares_outstanding"] = shares

        # ── valuation multiples ─────────────────────────────────────
        for k in ("trailingPE", "forwardPE", "priceToBook",
                  "priceToSalesTrailing12Months", "enterpriseToEbitda",
                  "trailingEps", "forwardEps", "bookValue",
                  "dividendYield", "dividendRate", "beta",
                  "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                  "fiftyDayAverage", "twoHundredDayAverage"):
            v = _g(k)
            if v is not None:
                snap[k] = v

        # ── consensus ───────────────────────────────────────────────
        snap["target_mean"] = _g("targetMeanPrice")
        snap["target_high"] = _g("targetHighPrice")
        snap["target_low"] = _g("targetLowPrice")
        snap["recommendation_key"] = _g("recommendationKey", "")
        snap["recommendation_mean"] = _g("recommendationMean")
        snap["num_analysts"] = _g("numberOfAnalystOpinions")

        # ── holdings ────────────────────────────────────────────────
        snap["held_pct_insiders"] = _g("heldPercentInsiders")
        snap["held_pct_institutions"] = _g("heldPercentInstitutions")
        snap["shares_short"] = _g("sharesShort")
        snap["short_ratio"] = _g("shortRatio")

        # ── next earnings ───────────────────────────────────────────
        for ek in ("earningsTimestampStart", "earningsTimestampEnd",
                   "earningsTimestamp"):
            ev = _g(ek)
            if ev and isinstance(ev, (int, float)) and ev > 0:
                try:
                    dt = datetime.fromtimestamp(ev, tz=timezone.utc)
                    if dt > datetime.now(tz=timezone.utc):
                        snap["next_earnings"] = dt.strftime("%Y-%m-%d")
                        snap["next_earnings_key"] = ek
                        break
                except Exception:
                    pass

        # ── 보조 블록 6종 병렬 수집 (2026-06-10 '종목검색 느린거' 사용자
        # 리스크-승인 수정) — 실적이력/투자의견변경/기관보유/뉴스/재무제표/
        # 동종비교는 서로 독립인데 직렬이라 합산 ~10-20초가 걸리던 cold-path
        # 병목. 스레드마다 **자기 전용 yf.Ticker** 를 만들고(생성은 lazy·
        # HTTP 0 — 공유 세션/인스턴스 없음) 결과를 로컬 dict 로 반환 →
        # 메인 스레드에서 병합: 스레드 간 공유 가변상태 0. 각 task 60초
        # bound(직렬일 때도 hang 리스크는 같았고, 이제 한 task 가 전체를
        # 못 끌고 감). 풀 실패 시 직렬 폴백. market_overview /
        # market_favorites 의 ThreadPool-8 yfinance 패턴과 동일 클래스.
        def _aux_earnings() -> dict:
            out: dict = {}
            ed = yf.Ticker(ticker).earnings_dates
            if ed is not None and not ed.empty:
                rows = []
                for idx, row in ed.head(8).iterrows():
                    entry: dict = {}
                    if hasattr(idx, "strftime"):
                        entry["date"] = idx.strftime("%Y-%m-%d")
                    else:
                        entry["date"] = str(idx)[:10]
                    for col in ed.columns:
                        v = row.get(col)
                        if v is not None and str(v) != "nan":
                            entry[col] = round(float(v), 4) if isinstance(v, float) else v
                    rows.append(entry)
                if rows:
                    out["earnings_history"] = rows
            return out

        def _aux_upgrades() -> dict:
            out: dict = {}
            ud = yf.Ticker(ticker).upgrades_downgrades
            if ud is not None and not ud.empty:
                rows = []
                for idx, row in ud.head(15).iterrows():
                    entry: dict = {}
                    if hasattr(idx, "strftime"):
                        entry["date"] = idx.strftime("%Y-%m-%d")
                    else:
                        entry["date"] = str(idx)[:10]
                    for col in ud.columns:
                        v = row.get(col)
                        if v is not None and str(v) != "nan":
                            entry[col] = str(v) if not isinstance(v, (int, float)) else v
                    rows.append(entry)
                if rows:
                    out["upgrades_downgrades"] = rows
            return out

        def _aux_holders() -> dict:
            out: dict = {}
            ih = yf.Ticker(ticker).institutional_holders
            if ih is not None and not ih.empty:
                rows = []
                for _, row in ih.head(10).iterrows():
                    entry: dict = {}
                    for col in ih.columns:
                        v = row.get(col)
                        if v is None or str(v) == "nan":
                            continue
                        if hasattr(v, "strftime"):
                            entry[col] = v.strftime("%Y-%m-%d")
                        elif isinstance(v, float):
                            entry[col] = round(v, 6) if abs(v) < 1 else round(v, 2)
                        else:
                            entry[col] = str(v) if not isinstance(v, (int, float)) else v
                    rows.append(entry)
                if rows:
                    out["institutional_holders"] = rows
            return out

        def _aux_news() -> dict:
            out: dict = {}
            news = yf.Ticker(ticker).news
            if news:
                rows = []
                for item in news[:10]:
                    entry = {}
                    for k in ("title", "publisher", "link",
                              "providerPublishTime"):
                        v = item.get(k)
                        if v is not None:
                            if k == "providerPublishTime" and isinstance(v, (int, float)):
                                entry["date"] = datetime.fromtimestamp(
                                    v, tz=timezone.utc
                                ).strftime("%Y-%m-%d")
                            else:
                                entry[k] = v
                    if entry.get("title"):
                        rows.append(entry)
                if rows:
                    out["news"] = rows
            return out

        def _aux_financials() -> dict:
            out: dict = {}
            _collect_financials(yf.Ticker(ticker), out)
            return out

        def _aux_peers() -> dict:
            out: dict = {}
            _collect_peer_multiples(ticker, info, out)
            return out

        # 이름을 붙여 둔다 — 어느 수집이 느린지 재려면 이름이 필요하고,
        # 프로브가 이 목록을 **복제하면** 제품과 다른 걸 재게 된다(#35).
        _aux_tasks = (("실적이력", _aux_earnings), ("투자의견", _aux_upgrades),
                      ("기관보유", _aux_holders), ("뉴스", _aux_news),
                      ("재무제표", _aux_financials), ("동종비교", _aux_peers))

        def _timed(name, fn):
            """수집 1건 + 소요시간 기록. 계측이 실패를 삼키면 안 되므로
            예외는 그대로 올려보내고 시간만 남긴다."""
            _t0 = time.time()
            try:
                return fn() or {}
            finally:
                _TIMING.set(ticker, name, time.time() - _t0)

        # ⚠️ 시장 enrichment 를 **먼저 띄운다**. 보조 6종 뒤에 직렬로 돌던
        # 것인데(아래 옛 주석이 그 사실을 적어 두고 있었다), `_enrich_*` 는
        # AST 로 확인한 결과 **snap 에서 아무것도 읽지 않고 자기 시장 키만
        # 쓴다** — 별도 dict 에 받아 뒤에서 합치면 서로 기다릴 이유가 없다.
        # (2026-08-22 `/api/lookup_detail` 60~192초 대응. 직렬 합이 최대값
        # 하나가 된다 — #127 표↔수주잔고와 같은 처방.)
        # 시장별 분기를 이름으로 열거하지 않고 접미사 표에서 고른다(#24).
        _ENRICH = ((".KS", ".KQ"), _enrich_kr, "KR"), \
                  ((".T",), _enrich_jp, "JP"), \
                  ((".TW", ".TWO"), _enrich_tw, "TW"), \
                  ((".SS", ".SZ", ".BJ", ".HK"), _enrich_cn, "CN")
        _fn, _mkt = _enrich_us, "US"
        for _sfx, _f, _m in _ENRICH:
            if ticker.endswith(_sfx):
                _fn, _mkt = _f, _m
                break
        _overlay: dict = {}

        def _run_enrich() -> None:
            _t0 = time.time()
            try:
                _fn(ticker, _overlay)
            except Exception as exc:                           # noqa: BLE001
                log.warning("stock_snapshot: %s enrich skipped for %s: %s",
                            _mkt, ticker, exc)
            finally:
                _TIMING.set(ticker, f"enrich:{_mkt}", time.time() - _t0)

        from concurrent.futures import ThreadPoolExecutor
        _enr_pool = _enr_fut = None
        try:
            # ⚠️ 공용 풀(`bot.pool`)을 쓰지 않는다 — 그건 **말단 팬아웃 전용**
            # 이고, 여기서 제출한 작업이 다시 공용 풀에 제출하면 교착한다(#110).
            _enr_pool = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="enrich")
            _enr_fut = _enr_pool.submit(_run_enrich)
            # ⚠️ 바로 shutdown(wait=False) — 제출한 작업은 그대로 돌고
            # 리소스는 끝나면 정리된다. 아래 어느 경로로 빠져나가도(예외 포함)
            # 풀이 남지 않는다(정리를 뒤에 두면 예외 경로에서 샌다).
            _enr_pool.shutdown(wait=False)
        except Exception as exc:                               # noqa: BLE001
            log.debug("stock_snapshot: enrich 병렬 실패, 직렬로: %s", exc)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futs = [pool.submit(_timed, nm, fn) for nm, fn in _aux_tasks]
                for fut in futs:
                    try:
                        snap.update(fut.result(timeout=60) or {})
                    except Exception:
                        pass
        except Exception:
            # 풀 생성 실패 등 — 직렬 폴백 (동작 동일, 속도만 원래대로)
            for nm, fn in _aux_tasks:
                try:
                    snap.update(_timed(nm, fn) or {})
                except Exception:
                    pass

        # strip None values to keep JSON compact
        snap = {k: v for k, v in snap.items() if v is not None}

        # ── 위에서 띄운 시장 enrichment 를 여기서 합친다 ──────────────
        # ⚠️ `snap` 재바인딩(None strip) **뒤에** 합쳐야 한다 — 앞에서
        # 합치면 strip 이 만든 새 dict 에 안 실린다.
        _t_w = time.time()
        if _enr_fut is not None:
            try:
                _enr_fut.result(timeout=180)
            except Exception as exc:                           # noqa: BLE001
                log.warning("stock_snapshot: %s enrich 대기 실패 %s: %s",
                            _mkt, ticker, exc)
        else:
            _run_enrich()                  # 풀 생성 실패 → 직렬 폴백
        # 대기 시간을 따로 남긴다 — 0 에 가까우면 겹치기가 실제로 먹은 것이고,
        # 크면 enrich 가 보조 6종보다 훨씬 길다는 뜻이다(#69 재는 것부터).
        _TIMING.set(ticker, "enrich.wait", time.time() - _t_w)
        snap.update(_overlay)
        _apply_share_count(ticker, snap)

        _TIMING.set(ticker, "total", time.time() - _t_all)
        return snap

    except Exception as exc:
        _TIMING.set(ticker, "total", time.time() - _t_all)
        log.warning("stock_snapshot: failed for %s: %s", ticker, exc)
        return None


def _apply_share_count(ticker: str, snap: dict) -> None:
    """발행주식수를 **등록 주식수**와 대조해 고른다 — 전 시장 공통 단계.

    ⚠️ 2026-08-23 VM 실측이 첫 판을 뒤집었다: yfinance 가 서희건설 시총
    3,967억 · 주식수 185,368,615 로 **자기들끼리는 정확히 맞았다**(항등식
    통과). 둘 다 같은 낡은 주식수 위에 있었을 뿐이고 진짜 시총은 4,442억
    이다 — `시가총액 ÷ 현재가` 만 보는 가드는 이 상태를 그냥 통과시킨다.
    그래서 거래소가 아는 사실(상장주식수)을 **대조군**으로 둔다(#143).

    주식수는 EPS·BPS 의 분모라 한 번 틀리면 주당지표가 통째로 밀린다 —
    실측: BPS 6,680.92(낡은 수) → 5,965.80(등록 수), FnGuide 5,856 과
    1.9% 차이(남는 건 비지배지분).

    ⚠️ 여기 있어야 하는 이유: `_enrich_*` 는 **snap 에서 아무것도 읽지
    않는다**는 전제로 보조 6종과 겹쳐 돈다(#128). 이 판정은 시총·현재가를
    읽어야 하므로 enrich 안에 두면 그 전제가 깨진다 — 실제로 회귀가 잡았다.
    등록 주식수 **원천**은 시장별이지만(KR = KRX/FSC) 판정은 공통이고,
    원천이 없는 시장은 값을 그대로 두고 화면이 어긋남을 밝힌다(#43).
    """
    try:
        from bot.share_count import resolve as _resolve_shares
        q = (snap.get("kr") or {}).get("krx_quote") or {}
        reg_label = "KRX 상장주식수" + (f"({q.get('date')})" if q.get("date") else "")
        r = _resolve_shares(snap.get("current_price"), snap.get("market_cap"),
                            snap.get("shares_outstanding"), "yfinance",
                            q.get("shares"), reg_label)
        if r["shares"]:
            snap["shares_outstanding"] = r["shares"]
            snap["shares_source"] = r["source"]
        if r["market_cap"]:
            snap["market_cap"] = r["market_cap"]
        if r["note"]:
            snap["shares_note"] = r["note"]
            log.info("stock_snapshot %s 주식수: %s", ticker, r["note"])
    except Exception as exc:                                   # noqa: BLE001
        log.warning("stock_snapshot %s: 주식수 검산 실패: %s", ticker, exc)


def _enrich_kr(ticker: str, snap: dict) -> None:
    """Add KR-specific data from DART + FSC to an existing snapshot dict.

    Non-fatal: each block is independent try/except. Missing API keys or
    network errors just skip that block — the detail page degrades.

    병렬화 (2026-06-10 사용자 '여전히 오래걸려' → 커밋 승인): 13개 직렬
    블록(블록당 0.3~2초, 합산 8~20초 — KR 종목이 특히 느리던 핵심)을
    ThreadPool 로 동시 실행. #186 과 동일 규율 — 각 task 는 자기 로컬
    dict 에만 쓰고(공유 가변상태 0) 메인 스레드가 **원래 블록 순서대로
    setdefault 병합**(DART 우선 corp_reg_no, FnGuide 우선 consensus 등
    기존 우선순위 보존). 진짜 의존성(Naver→한경 폴백, KIS→pykrx flow,
    DART 재무 현년+3개년)은 한 task 안에서 순차 유지. DART 동시 호출은
    엔드포인트가 다르고 12h 디스크 캐시(F3)라 rate-limit 안전. 풀 실패
    시 직렬 폴백(동작 동일)."""
    stock_code = ticker.split(".")[0]

    def _t_dart_company() -> dict:
        out: dict = {}
        from bot.dart_client import get_dart
        dart = get_dart()
        ci = dart.get_company_info(stock_code) if dart else None
        if ci and ci.get("status") == "000":
            kr = out.setdefault("kr", {})
            for src_key, dst_key in (
                ("jurir_no", "corp_reg_no"),    # 법인등록번호
                ("bizr_no", "biz_reg_no"),      # 사업자등록번호
                ("ceo_nm", "ceo"),              # 대표자
                ("corp_name", "corp_name"),      # 법인명
                ("corp_name_eng", "corp_name_eng"),
                ("induty_code", "ksic_code"),    # 한국표준산업분류
                ("est_dt", "established"),       # 설립일
                ("acc_mt", "fiscal_month"),      # 결산월
                ("adres", "address"),            # 주소
            ):
                v = ci.get(src_key)
                if v and str(v).strip() and str(v).strip() != "":
                    kr[dst_key] = str(v).strip()
        return out

    def _t_fsc_item() -> dict:
        # corp_reg_no 는 DART 가 우선 — 병합이 setdefault 라 DART(앞 순서)
        # 가 이미 채웠으면 자동으로 안 덮음 (기존 if-missing 의미 보존).
        out: dict = {}
        from bot.fsc_client import item_info as fsc_item_info, fsc_key_ready
        if fsc_key_ready():
            fi = fsc_item_info(ticker)
            if fi:
                kr = out.setdefault("kr", {})
                crno = fi.get("crno")
                if crno:
                    kr["corp_reg_no"] = crno
                mrkt = fi.get("mrktCtg")
                if mrkt:
                    kr["market_category"] = mrkt
        return out

    def _t_dart_insider() -> dict:
        out: dict = {}
        from bot.dart_client import get_dart
        dart = get_dart()
        if dart:
            holders = dart.get_insider_holdings(stock_code)
            if holders:
                out.setdefault("kr", {})["insider_holdings"] = holders[:15]
        return out

    def _t_dart_disclosures() -> dict:
        out: dict = {}
        from bot.dart_client import get_dart
        dart = get_dart()
        if dart:
            disclosures = dart.get_recent_disclosures(stock_code, days_back=365, limit=30)
            if disclosures:
                # B5(2026-06-16): dart_detail 구조화 요약(증자금액·자기주식
                # 취득금액·CB 전환가·배당 주당/시가배당률)을 rcept_no 매칭으로
                # 부착 → 공시 탭이 제목만이 아니라 핵심 숫자 한 줄을 함께 표시.
                # 실패해도 무해(제목만 표시). chart_events 가 이미 쓰는 데이터.
                try:
                    import re as _re
                    from bot.dart_detail import get_disclosure_summaries
                    summaries = get_disclosure_summaries(stock_code) or {}
                    if summaries:
                        for d in disclosures:
                            m = _re.search(r"rcpNo=(\d+)", d.get("url", "") or "")
                            if m and summaries.get(m.group(1)):
                                d["summary"] = summaries[m.group(1)]
                except Exception as exc:
                    log.debug("stock_snapshot: DART detail summaries skipped: %s", exc)
                out.setdefault("kr", {})["disclosures"] = disclosures
        return out

    def _t_dart_financials() -> dict:
        return collect_kr_financials(ticker)

    def _t_fsc_minority() -> dict:
        out: dict = {}
        from bot.fsc_client import minority_holders, fsc_key_ready
        if fsc_key_ready():
            mh = minority_holders(ticker)
            if mh:
                out.setdefault("kr", {})["minority"] = mh
        return out

    def _t_flow() -> dict:
        # KIS 4콜 + pykrx 2콜 — 둘 다 kr["flow"] 에 쓰므로 한 task 에서
        # 순차(KIS 먼저, pykrx 가 trends 를 update — 기존 의미 동일).
        out: dict = {}
        flow_data: dict = {}
        try:
            from bot.kis_client import KisClient
            kis = KisClient()
            if kis._ready():
                inv = kis.get_investor_flow(ticker)
                if inv:
                    flow_data["investor_flow"] = inv
                credit = kis.get_credit_short_balance(ticker)
                if credit:
                    flow_data["credit"] = credit
                short = kis.get_short_sale(ticker)
                if short:
                    flow_data["short_sale"] = short
                program = kis.get_program_trade(ticker)
                if program:
                    flow_data["program"] = program
        except Exception as exc:
            log.debug("stock_snapshot: KIS flow skipped: %s", exc)
        try:
            from bot.pykrx_client import (
                get_kr_foreign_ownership_trend,
                get_kr_short_balance_trend,
            )
            fo = get_kr_foreign_ownership_trend(ticker, days_back=30)
            if fo:
                flow_data["foreign_ownership"] = fo
            sb = get_kr_short_balance_trend(ticker, days_back=30)
            if sb:
                flow_data["short_trend"] = sb
        except Exception as exc:
            log.debug("stock_snapshot: pykrx trends skipped: %s", exc)
        if flow_data:
            out.setdefault("kr", {})["flow"] = flow_data
        return out

    # ⚠️ lockup·dilution 을 한 task 에 **순차로** 묶어 뒀더니 2026-08-21 VM
    # 계측에서 `kr:fsc.risk` 가 중앙값 21.26초로 `enrich:KR`(22.24초) 전체를
    # 지배했다 — 둘은 서로 독립인데 합이 벽시계에 실렸다(#92 '병렬이면 합이
    # 아니라 최대값'). 나누면 max() 가 되고, 어느 쪽이 느린지도 계측에
    # 따로 찍힌다(한 이름 아래 묶으면 다음 라운드에 또 못 짚는다, #69).
    def _t_fsc_lockup() -> dict:
        from bot.fsc_client import fsc_key_ready
        if not fsc_key_ready():
            return {}
        try:
            from bot.fsc_client import lockup_releases as fsc_lockup
            lr = fsc_lockup(ticker, lookback_days=7)
            return {"kr": {"lockup_releases": lr}} if lr else {}
        except Exception as exc:
            log.debug("stock_snapshot: FSC lockup skipped: %s", exc)
            return {}

    def _t_fsc_dilution() -> dict:
        from bot.fsc_client import fsc_key_ready
        if not fsc_key_ready():
            return {}
        try:
            from bot.fsc_client import dilution_events as fsc_dilution
            de = fsc_dilution(ticker, lookback_days=10)
            return {"kr": {"dilution_events": de}} if de else {}
        except Exception as exc:
            log.debug("stock_snapshot: FSC dilution skipped: %s", exc)
            return {}

    def _t_krx_shares() -> dict:
        """KRX 공식 **상장주식수** — 주당지표의 분모다.

        ⚠️ 2026-08-23 서희건설 실측: yfinance `sharesOutstanding` 이
        185.4M 인데 KRX/네이버는 207,588,536 이라 헤더가 자기 산수를
        못 맞췄다(시가총액 ÷ 발행주식수 ≠ 현재가, #33). `get_kr_market_cap`
        은 KRX(pykrx) → 금융위 FSC `lstgStCnt` 폴백으로 등록 주식수를
        주는데 **레포에 있으면서 아무도 안 부르고 있었다**(#150).
        """
        from bot.pykrx_client import get_kr_market_cap
        q = get_kr_market_cap(ticker)
        return {"kr": {"krx_quote": q}} if q and q.get("shares") else {}

    def _t_krx_alert() -> dict:
        out: dict = {}
        from bot.krx_alert_client import get_krx_alert
        alert = get_krx_alert()
        status = alert.get_status(ticker)
        if status and (status.get("suspended") or status.get("admin")
                       or status.get("overheating") or status.get("warning_level")):
            out.setdefault("kr", {})["market_alert"] = status
        return out

    def _t_fnguide() -> dict:
        # A3(2026-06-16): yfinance 컨센서스 유무와 무관하게 항상 수집 →
        # 대시보드가 월가(yfinance)와 현지(FnGuide) 컨센서스를 병기. 12h
        # 디스크 캐시라 라이브 오버레이 재조회 비용 ~0.
        out: dict = {}
        from bot.fnguide_consensus import fetch_consensus as fnguide_fetch
        fg = fnguide_fetch(ticker)
        if fg and fg.get("target_mean"):
            out.setdefault("kr", {})["consensus"] = {
                "source": "FnGuide",
                "target_mean": fg["target_mean"],
                "rating": fg.get("rating"),
                "n_analysts": fg.get("n_analysts"),
            }
        # 어닝서프라이즈(영업이익·당기순이익 × 컨센서스/잠정치/Surprise/전년동기)
        # — 네이버 임베드(WISEreport) 기업현황의 서버사이드 res 객체. 12h 캐시라
        # 라이브 오버레이 재조회 비용 ~0. graceful(무커버리지/실패 시 미저장).
        try:
            from bot.wisereport_earnings import fetch_earnings_surprise
            es = fetch_earnings_surprise(ticker)
            if es and es.get("periods"):
                out.setdefault("kr", {})["earnings_surprise"] = es
        except Exception as exc:
            log.debug("stock_snapshot: wisereport earnings skipped: %s", exc)
        return out

    def _t_research() -> dict:
        # Naver 1차 → 한경 폴백/머지 — 진짜 의존성이라 task 내부 순차.
        out: dict = {}
        _kr_research = None
        try:
            from bot.naver_research_client import fetch_research
            _kr_research = fetch_research(ticker)
        except Exception as exc:
            log.debug("stock_snapshot: Naver research skipped: %s", exc)
        _naver_hollow = (_kr_research and _kr_research.get("reports")
                         and not any(r.get("target") or r.get("rating")
                                     for r in _kr_research["reports"]))
        if not (_kr_research and _kr_research.get("reports")) or _naver_hollow:
            try:
                from bot.hk_consensus_client import fetch_consensus as hk_fetch
                _hk = hk_fetch(ticker)
            except Exception as exc:
                log.debug("stock_snapshot: HanKyung consensus skipped: %s", exc)
                _hk = None
            if _hk and _hk.get("reports"):
                if _naver_hollow:
                    _hk_map = {(r.get("date"), r.get("broker")): r
                               for r in _hk["reports"]}
                    for r in _kr_research["reports"]:
                        match = _hk_map.get((r.get("date"), r.get("broker")))
                        if match:
                            if not r.get("target") and match.get("target"):
                                r["target"] = match["target"]
                            if not r.get("rating") and match.get("rating"):
                                r["rating"] = match["rating"]
                    if not any(r.get("target") or r.get("rating")
                               for r in _kr_research["reports"]):
                        _kr_research = _hk
                else:
                    _kr_research = _hk
        if _kr_research:
            if _kr_research.get("reports"):
                out.setdefault("kr", {})["research_reports"] = _kr_research["reports"]
            if _kr_research.get("target_price"):
                # A3: yfinance 유무 무관 항상. consensus 는 FnGuide 우선 —
                # 병합 순서(setdefault)가 보장(FnGuide 먼저 set 되면 유지).
                out.setdefault("kr", {})["consensus"] = {
                    "source": "Naver Finance",
                    "target_mean": _kr_research["target_price"],
                    "rating": _kr_research.get("rating"),
                    "n_analysts": _kr_research.get("analyst_count"),
                    "last_report_date": _kr_research.get("last_report_date"),
                }
        return out

    def _t_dividends() -> dict:
        out: dict = {}
        _collect_dividends(ticker, out)
        return out

    # 병합 순서 = 원래 직렬 블록 순서 (corp_reg_no DART 우선, consensus
    # FnGuide 우선 등 기존 우선순위가 setdefault 병합으로 그대로 재현).
    # ⚠️ 이름을 붙인다. `enrich:KR` 이 19.1초인데(2026-08-21 실측) **어느
    # task 가 그 시간을 먹는지** 알 방법이 없었다 — 이미 병렬이라 합이
    # 아니라 **최대값 하나**가 지배하는데, 이름이 없으면 그걸 못 짚는다
    # (실수 #69: 느린 곳을 고치려면 재는 것부터).
    # ⚠️ 풀 크기(8) < task 수(12) 라 **두 물결**로 돈다 — 벽시계는
    # `max(1차 물결) + max(2차 물결)` 이다. 이것도 계측이 있어야 보인다.
    tasks = [("dart.company", _t_dart_company), ("fsc.item", _t_fsc_item),
             ("dart.insider", _t_dart_insider),
             ("dart.disclosures", _t_dart_disclosures),
             ("dart.financials", _t_dart_financials),
             ("fsc.minority", _t_fsc_minority), ("flow(KIS+pykrx)", _t_flow),
             ("fsc.lockup", _t_fsc_lockup),
             ("fsc.dilution", _t_fsc_dilution), ("krx.alert", _t_krx_alert),
             ("krx.shares", _t_krx_shares),
             ("fnguide", _t_fnguide), ("research", _t_research),
             ("dividends", _t_dividends)]

    def _run(name, fn):
        _t0 = time.time()
        try:
            return fn()
        finally:
            _TIMING.set(ticker, f"kr:{name}", time.time() - _t0)

    results: list[dict | None] = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futs = [pool.submit(_run, nm, fn) for nm, fn in tasks]
            for fut in futs:
                try:
                    results.append(fut.result(timeout=60))
                except Exception as exc:
                    log.debug("stock_snapshot: KR task skipped: %s", exc)
                    results.append(None)
    except Exception:
        results = []
        for nm, fn in tasks:   # 풀 실패 — 직렬 폴백 (동작 동일)
            try:
                results.append(_run(nm, fn))
            except Exception as exc:
                log.debug("stock_snapshot: KR task skipped: %s", exc)
                results.append(None)

    for out in results:
        if not out:
            continue
        for k, v in out.items():
            if k == "kr":
                kr = snap.setdefault("kr", {})
                for sk, sv in v.items():
                    kr.setdefault(sk, sv)
            else:
                snap.setdefault(k, v)

    # ── Naver 뉴스 폴백 — 풀 **종료 후** 직렬 (의도된 의존성: 한국어
    # 검색어를 위해 위 _t_dart_company 가 병합해 둔 kr.corp_name 을 읽음.
    # 풀 안에 넣으면 corp_name 없이 검색해 품질 저하). ~1-2초 추가일 뿐
    # 전체는 여전히 직렬 8-20초 → 병렬 max+뉴스 ~4-7초.
    _collect_news_fallback(ticker, snap)


def _enrich_us(ticker: str, snap: dict) -> None:
    """Add US-specific data from SEC EDGAR to an existing snapshot dict."""
    # ── SEC XBRL multi-year financials ────────────────────────────
    try:
        from bot.edgar_client import get_key_financials
        kf = get_key_financials(ticker)
        if kf and kf.get("metrics"):
            metrics = kf["metrics"]
            us: dict = snap.setdefault("us", {})
            financials: dict = {}
            for key, m in metrics.items():
                entry: dict = {}
                if m.get("annual"):
                    entry["annual"] = {
                        "val": m["annual"].get("val"),
                        "fy": m["annual"].get("fy"),
                        "end": m["annual"].get("end"),
                    }
                if m.get("latest"):
                    entry["latest"] = {
                        "val": m["latest"].get("val"),
                        "fy": m["latest"].get("fy"),
                        "end": m["latest"].get("end"),
                        "form": m["latest"].get("form"),
                    }
                entry["unit"] = m.get("unit", "USD")
                if entry.get("annual") or entry.get("latest"):
                    financials[key] = entry
            if financials:
                us["xbrl"] = financials
    except Exception as exc:
        log.debug("stock_snapshot: EDGAR financials skipped: %s", exc)

    # ── SEC 8-K disclosures (공시) ───────────────────────────────
    try:
        from bot.edgar_client import get_recent_8k
        filings = get_recent_8k(ticker, days=60, top_n=20)
        if filings:
            disc_rows = []
            for f in filings:
                labels = f.get("items_labels", [])
                title = " / ".join(labels) if labels else f.get("items_raw", "8-K")
                disc_rows.append({
                    "date": f.get("date", ""),
                    "title": title,
                    "url": f.get("url", ""),
                    "reporter": "SEC 8-K",
                })
            snap.setdefault("us", {})["disclosures"] = disc_rows
    except Exception as exc:
        log.debug("stock_snapshot: EDGAR 8-K skipped: %s", exc)

    # ── SEC Form 4 insider trades ─────────────────────────────────
    try:
        from bot.edgar_client import get_recent_form4
        f4 = get_recent_form4(ticker, days=60, top_n=10)
        if f4:
            snap.setdefault("us", {})["insider_trades"] = f4
    except Exception as exc:
        log.debug("stock_snapshot: EDGAR Form 4 skipped: %s", exc)

    # ── SEC 13F 기관플로우 (2026-07-26, 미국전용 — bot/edgar_13f.py 독스트링
    # 참조) — 대표 기관투자자(현재 버크셔 해서웨이)의 최근 2개 분기 13F 대비
    # 이 종목 보유변화. 회사명 substring 매칭(CUSIP 매핑 소스 없음, 문서화된
    # 한계) — SEC company_tickers.json 의 공식 title 을 매칭명으로 사용.
    try:
        from bot.edgar_13f import get_13f_flow
        from bot.edgar_client import sec_ticker_names
        name = sec_ticker_names().get(ticker.upper())
        if name:
            flow = get_13f_flow(name)
            if flow and flow.get("action") != "NOT_HELD":
                snap.setdefault("us", {})["institutional_13f"] = flow
    except Exception as exc:
        log.debug("stock_snapshot: 13F flow skipped: %s", exc)


def _enrich_jp(ticker: str, snap: dict) -> None:
    """Add JP-specific data from EDINET to an existing snapshot dict."""
    # ── EDINET disclosures (公示) ─────────────────────────────────
    try:
        from bot.edinet_client import get_edinet
        ed = get_edinet()
        if ed:
            disclosures = ed.get_recent_disclosures(ticker, days_back=60, limit=20)
            if disclosures:
                snap.setdefault("jp", {})["disclosures"] = disclosures
    except Exception as exc:
        log.debug("stock_snapshot: EDINET disclosures skipped: %s", exc)

    # ── EDINET 大量保有 (major holders / 5%+ ownership) ───────────
    try:
        from bot.edinet_client import get_edinet
        ed = get_edinet()
        if ed:
            holders = ed.get_major_holders(ticker, days_back=180)
            if holders:
                snap.setdefault("jp", {})["major_holders"] = holders[:15]
    except Exception as exc:
        log.debug("stock_snapshot: EDINET major holders skipped: %s", exc)

    # ── Kabutan consensus (yfinance 와 병기, A3 2026-06-16 무게이트) ──
    try:
        from bot.kabutan_consensus import fetch_consensus as kabutan_fetch
        kb = kabutan_fetch(ticker)
        if kb and kb.get("target_mean"):
            snap.setdefault("jp", {})["consensus"] = {
                "source": "Kabutan",
                "target_mean": kb["target_mean"],
                "rating": kb.get("rating"),
                "n_analysts": kb.get("n_analysts"),
                "last_report_date": kb.get("last_report_date"),
            }
    except Exception as exc:
        log.debug("stock_snapshot: Kabutan consensus skipped: %s", exc)

    # ── Kabutan 뉴스 폴백 (yfinance JP 뉴스 미커버) ───────────────
    _collect_news_fallback(ticker, snap)

    # ── yfinance dividends (universal) ────────────────────────────
    _collect_dividends(ticker, snap)


def _enrich_tw(ticker: str, snap: dict) -> None:
    """Add TW-specific data from MOPS to an existing snapshot dict."""
    # ── MOPS 重大訊息 (material disclosures) ──────────────────────
    try:
        from bot.mops_client import get_mops
        mops = get_mops()
        if mops:
            disclosures = mops.get_recent_disclosures(ticker, days_back=60, limit=20)
            if disclosures:
                snap.setdefault("tw", {})["disclosures"] = disclosures
    except Exception as exc:
        log.debug("stock_snapshot: MOPS disclosures skipped: %s", exc)

    # ── MOPS 內部人持股 (insider holdings) ─────────────────────────
    try:
        from bot.mops_client import get_mops
        mops = get_mops()
        if mops:
            insiders = mops.get_insider_holdings(ticker)
            if insiders:
                snap.setdefault("tw", {})["insider_holdings"] = insiders[:15]
    except Exception as exc:
        log.debug("stock_snapshot: MOPS insider holdings skipped: %s", exc)

    # ── FinMind 월매출 (TW 의무 공시, 千元) ─────────────────────
    try:
        from bot.finmind_client import fetch_monthly_revenue
        rev = fetch_monthly_revenue(ticker)
        if rev:
            snap.setdefault("tw", {})["monthly_revenue"] = rev
    except Exception as exc:
        log.debug("stock_snapshot: FinMind monthly revenue skipped: %s", exc)

    # ── FinMind PER/PBR (TWSE 원본) ──────────────────────────────
    try:
        from bot.finmind_client import fetch_per_pbr
        ppb = fetch_per_pbr(ticker)
        if ppb:
            snap.setdefault("tw", {})["per_pbr"] = ppb
    except Exception as exc:
        log.debug("stock_snapshot: FinMind PER/PBR skipped: %s", exc)

    # ── FinMind 분기 재무 요약 (B4 2026-06-16) — 손익/재무상태/현금흐름.
    # US SEC XBRL·KR DART 박스의 TW 등가물(밸류에이션 탭). 12h 캐시.
    try:
        from bot.finmind_client import fetch_tw_financials
        fin = fetch_tw_financials(ticker)
        if fin:
            snap.setdefault("tw", {})["financials"] = fin
    except Exception as exc:
        log.debug("stock_snapshot: FinMind financials skipped: %s", exc)

    # ── FinMind 외국인 보유 현황 (2026-06-16 정정) — VM probe 로 확인: FinMind
    # TaiwanStockShareholding 은 TDCC 보유구간 분산이 아니라 **외국인 투자 현황 +
    # 발행주식수**다(옛 'TDCC 분산' 가정은 오류 → 빈 표였음). 외국인 보유비율은
    # TW 핵심 수급 신호라 그대로 유용. 최신 1행만 보관.
    try:
        from bot.finmind_client import fetch_shareholding
        sh = fetch_shareholding(ticker)
        if sh and isinstance(sh, list):
            latest = max((r for r in sh if isinstance(r, dict)),
                         key=lambda r: r.get("date", ""), default=None)
            if latest and latest.get("ForeignInvestmentSharesRatio") is not None:
                snap.setdefault("tw", {})["foreign"] = {
                    "date": latest.get("date"),
                    "foreign_ratio": latest.get("ForeignInvestmentSharesRatio"),
                    "remain_ratio": latest.get("ForeignInvestmentRemainRatio"),
                    "shares_issued": latest.get("NumberOfSharesIssued"),
                }
    except Exception as exc:
        log.debug("stock_snapshot: FinMind foreign holding skipped: %s", exc)

    # ── cnyes 컨센서스 (yfinance 와 병기, A3 2026-06-16 무게이트) ──
    try:
        from bot.cnyes_consensus import fetch_consensus as cnyes_fetch
        cn = cnyes_fetch(ticker)
        if cn and cn.get("target_mean"):
            snap.setdefault("tw", {})["consensus"] = {
                "source": "鉅亨網",
                "target_mean": cn["target_mean"],
                "rating": cn.get("rating"),
                "n_analysts": cn.get("n_analysts"),
                "last_report_date": cn.get("last_report_date"),
            }
    except Exception as exc:
        log.debug("stock_snapshot: cnyes consensus skipped: %s", exc)

    # ── 鉅亨網 뉴스 폴백 (yfinance TW 뉴스 미커버) ────────────────
    _collect_news_fallback(ticker, snap)

    # ── yfinance dividends (universal) ────────────────────────────
    _collect_dividends(ticker, snap)


def _enrich_cn(ticker: str, snap: dict) -> None:
    """Add CN/HK-specific data from AKShare to an existing snapshot dict."""
    # ── AKShare 公告 (disclosures) ────────────────────────────────
    try:
        from bot.akshare_client import get_akshare
        ak = get_akshare()
        if ak:
            disclosures = ak.get_recent_disclosures(ticker, days_back=60, limit=20)
            if disclosures:
                snap.setdefault("cn", {})["disclosures"] = disclosures
    except Exception as exc:
        log.debug("stock_snapshot: AKShare disclosures skipped: %s", exc)

    # ── AKShare 主要流通股东 (major holders, A-share only) ─────────
    try:
        from bot.akshare_client import get_akshare
        ak = get_akshare()
        if ak:
            holders = ak.get_major_holders(ticker)
            if holders:
                snap.setdefault("cn", {})["major_holders"] = holders[:15]
    except Exception as exc:
        log.debug("stock_snapshot: AKShare major holders skipped: %s", exc)

    # ── AKShare ST/停牌 status ────────────────────────────────────
    try:
        from bot.akshare_client import get_akshare
        ak = get_akshare()
        if ak:
            risk: dict = {}
            if ak.is_st(ticker):
                risk["is_st"] = True
            if ak.is_suspended(ticker):
                risk["is_suspended"] = True
            if risk:
                snap.setdefault("cn", {})["risk_status"] = risk
    except Exception as exc:
        log.debug("stock_snapshot: AKShare ST/停牌 skipped: %s", exc)

    # ── AKShare 港股通 flow (market-wide) ─────────────────────────
    try:
        from bot.akshare_client import get_akshare
        ak = get_akshare()
        if ak:
            flow = ak.get_hsgt_flow_summary(days_back=5)
            if flow:
                snap.setdefault("cn", {})["hsgt_flow"] = flow
    except Exception as exc:
        log.debug("stock_snapshot: AKShare HSGT flow skipped: %s", exc)

    # ── AKShare 밸류에이션 (PER/PBR/PSR — yfinance CN/HK 미커버 보완, B3) ──
    try:
        from bot.akshare_client import get_akshare
        ak = get_akshare()
        if ak:
            val = ak.get_valuation(ticker)
            if val and (val.get("per") or val.get("pbr") or val.get("psr")):
                snap.setdefault("cn", {})["valuation"] = val
    except Exception as exc:
        log.debug("stock_snapshot: AKShare valuation skipped: %s", exc)

    # ── 东方财富 뉴스 폴백 (yfinance CN/HK 뉴스 미커버) ───────────
    _collect_news_fallback(ticker, snap)

    # ── yfinance dividends (universal) ────────────────────────────
    _collect_dividends(ticker, snap)


def _collect_news_fallback(ticker: str, snap: dict) -> None:
    """When yfinance .news is empty, route to the market's news client.

    yfinance covers US news well but barely touches KR/JP/TW/CN. Each
    market already has a news client used by the main analysis pipeline
    (Naver / Kabutan / cnyes / AKShare Eastmoney); all return the same
    {date, title, source, link, summary} schema. We map it to the
    snapshot news schema {title, publisher, link, date}. KR (Naver)
    titles are already Korean — tagged ``kr_native`` so the detail-page
    render skips the Gemini translation pass. Non-fatal: any failure
    leaves the (empty) news block untouched.
    """
    if snap.get("news"):
        return  # yfinance already populated it — nothing to backfill
    items = None
    kr_native = False
    g_query = ""   # query for the keyless Google News RSS fallback
    g_market = "US"
    try:
        if ticker.endswith((".KS", ".KQ")):
            from bot.naver_news_client import fetch_news
            from bot.market import kr_news_query_name, has_hangul
            # Korean news search needs the bare Korean brand ('네이버'),
            # not the legal suffix ('네이버(주)') or the DART English
            # registration ('NAVER' → 0 hits). Strip the corporate form;
            # prefer a Hangul name; ask DART for the Korean corp_name when
            # the snapshot only has the English one. (NAVER 0-news 2026-06-08)
            cand = kr_news_query_name(snap.get("kr", {}).get("corp_name"))
            if not has_hangul(cand):
                try:
                    from bot.dart_client import get_dart
                    dart = get_dart()
                    ko = dart.news_search_name(ticker) if dart else None
                    if ko and has_hangul(ko):
                        cand = ko
                except Exception:
                    pass
            query = (cand or kr_news_query_name(snap.get("long_name")) or "").strip()
            g_query, g_market, kr_native = query, "KR", True
            if query:
                items = fetch_news(query, days_back=28, max_items=10)
        elif ticker.endswith(".T"):
            from bot.kabutan_news import fetch_news
            items = fetch_news(ticker.split(".")[0], days_back=28, max_items=10)
            g_query, g_market = (snap.get("long_name") or ticker.split(".")[0]), "JP"
        elif ticker.endswith((".TW", ".TWO")):
            from bot.cnyes_client import fetch_news
            items = fetch_news(ticker.split(".")[0], days_back=28, max_items=10)
            g_query, g_market = (snap.get("long_name") or ticker.split(".")[0]), "TW"
        elif ticker.endswith((".SS", ".SZ", ".BJ", ".HK")):
            from bot.akshare_client import get_akshare
            ak = get_akshare()
            if ak:
                items = ak.fetch_news(ticker, days_back=28, max_items=10)
            g_query = snap.get("long_name") or ""
            g_market = "HK" if ticker.endswith(".HK") else "CN"
        else:
            g_query, g_market = (snap.get("long_name") or ticker), "US"
    except Exception as exc:
        log.debug("stock_snapshot: news fallback skipped for %s: %s", ticker, exc)
    # Keyless Google News RSS fallback — fires whenever the market-specific
    # source returned nothing (invalid Naver key / moved scrape / rate
    # limit). Surfaced 2026-06-08: NAVER's Naver-API key was auth-failing
    # (errorCode 024), so KR news was empty everywhere; this restores it
    # without depending on the broken key. Universal — every market.
    if not items and g_query:
        try:
            from bot.google_news_client import fetch_news as g_fetch, locale_for_market
            hl, gl, ceid = locale_for_market(g_market)
            items = g_fetch(g_query, days_back=28, max_items=10,
                            lang=hl, country=gl, ceid=ceid)
            # KR/JP/TW/CN Google News titles are in the local language →
            # tag kr_native only for KR so the renderer skips translation
            # for already-Korean titles (other markets still translate).
            kr_native = kr_native and g_market == "KR"
        except Exception as exc:
            log.debug("stock_snapshot: google news fallback skipped for %s: %s", ticker, exc)
    if not items:
        return
    rows: list[dict] = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        entry = {
            "title": title,
            "publisher": it.get("source", ""),
            "link": it.get("link", ""),
            "date": it.get("date", ""),
        }
        if kr_native:
            entry["kr_native"] = True
        rows.append(entry)
    if rows:
        snap["news"] = rows


def _collect_dividends(ticker: str, snap: dict) -> None:
    """Collect yfinance dividends — shared helper for all markets."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        divs = t.dividends
        if divs is not None and not divs.empty:
            rows = []
            for idx, val in divs.tail(12).items():
                if hasattr(idx, "strftime"):
                    rows.append({"date": idx.strftime("%Y-%m-%d"),
                                 "amount": round(float(val), 4)})
            if rows:
                snap["dividends"] = rows
    except Exception as exc:
        log.debug("stock_snapshot: dividends skipped for %s: %s", ticker, exc)


def _df_to_rows(df, max_periods: int = 5) -> list[dict]:
    """Convert a yfinance financial DataFrame to compact row dicts.

    Columns are fiscal period dates, index is line-item names.
    Returns [{period, item1, item2, ...}, ...] newest-first.
    """
    if df is None or df.empty:
        return []
    rows: list[dict] = []
    for col in list(df.columns)[:max_periods]:
        entry: dict = {}
        if hasattr(col, "strftime"):
            entry["period"] = col.strftime("%Y-%m-%d")
        else:
            entry["period"] = str(col)[:10]
        for item in df.index:
            v = df.at[item, col]
            if v is not None and str(v) != "nan":
                entry[item] = round(float(v), 2) if isinstance(v, float) else int(v)
        rows.append(entry)
    return rows


# ⚠️ **KR DART 재무 스키마 버전.** 아카이브에 저장된 kr.financials* 는
# 수집 당시 로직의 산물이다 — 계정 우선순위나 비율 규칙을 고쳐도 이미
# 분석한 종목 화면은 영원히 옛 값이다(실수 #18, peer_comps 와 같은
# 실패모드). 대시보드가 이 버전을 대조해 낡은 것만 다시 받는다.
#   v1 (2026-08-19) 구성요소 매출 = 비율 억제 + 계정 랭킹(표준 태그·이름 정규화)
#   v2 (2026-08-19) 총액 미공시사(증권·은행·보험) 매출을 FnGuide 총액으로 보강
#   v3 (2026-08-19) FnGuide 컬럼 매핑 수정 — v2 는 파서가 못 읽어 보강이 0건이었다
#   v9 (2026-08-21) FCF(= 영업활동현금흐름 − |CAPEX|) 추가 — 밸류에이션 표·분기 차트
_Q_TABLE = 4          # 분기 표에 보여줄 분기 수
_TTM_LEAD = 3         # TTM(4분기 합)을 첫 칸부터 채우려면 앞서 필요한 분기 수
_KR_FIN_SCHEMA_VER = 9


def collect_kr_financials(ticker: str) -> dict:
    """DART 재무(연간·시계열·분기) 수집 → {"kr": {...}}.

    ⚠️ 원래 스냅샷 빌더 안의 중첩 함수였다. **아카이브에 구워진 값을
    다시 받으려면** 밖에서도 부를 수 있어야 한다(실수 #18) — 이미
    분석한 종목은 재분석 전까지 옛 계정·옛 비율을 그대로 보여준다.
    """
    # 현년 + 3개년 시계열 — 같은 DART 재무 API 라 한 task 에서 순차.
    out: dict = {}
    from bot.dart_client import get_dart
    from datetime import datetime as _dt
    dart = get_dart()
    if not dart:
        return out
    fin = dart.get_normalized_financials(ticker)
    if fin and fin.get("financials"):
        compact = {"year": fin.get("year"), "fs_div": fin.get("fs_div")}
        for k in ("매출", "영업이익", "당기순이익", "자산총계",
                  "부채총계", "자본총계", "재고자산", "FCF"):
            v = fin["financials"].get(k)
            if v is not None:
                compact[k] = v
        ratios = fin.get("ratios", {})
        for k in ("영업이익률", "순이익률", "ROE", "ROA", "ROIC",
                  "부채비율", "유동비율"):
            v = ratios.get(k)
            if v is not None:
                compact[k] = v
        # 구성요소 계정('이자수익' 등)이 매출 승자였으면 그 사실을 실어
        # 보낸다 — 총매출이 아닌 값이 '매출'로 표기되는 것을 막는다
        # (사용자 2026-08-16 B안, VM probe 로 메리츠금융지주 확인).
        _comp = fin["financials"].get("_component_accounts")
        if _comp:
            compact["_component_accounts"] = dict(_comp)
        out.setdefault("kr", {})["financials"] = compact
    current_year = _dt.now().year
    _years = list(range(current_year - 1, current_year - 4, -1))
    # 3개년 연간 조회는 서로 독립인데 **직렬**이었다 — 병렬로 미리 받아
    # 디스크 캐시에 넣어 둔다(아래 루프는 그대로, 두 번째 호출은 0초).
    # 2026-08-21 계측: `kr:dart.financials` 가 enrich:KR 을 지배했다.
    try:
        from bot.pool import map_bounded
        map_bounded(lambda y: dart.get_normalized_financials(ticker, year=y),
                    _years)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("stock_snapshot: 연간 재무 미리받기 건너뜀: %s", exc)
    ts = []
    for yr in _years:
        fin = dart.get_normalized_financials(ticker, year=yr)
        if fin and fin.get("financials"):
            entry = {"year": fin.get("year"), "fs_div": fin.get("fs_div")}
            for k in ("매출", "영업이익", "당기순이익", "자산총계",
                      "부채총계", "자본총계", "재고자산", "FCF"):
                v = fin["financials"].get(k)
                if v is not None:
                    entry[k] = v
            _comp = fin["financials"].get("_component_accounts")
            if _comp:
                entry["_component_accounts"] = dict(_comp)
            ratios = fin.get("ratios", {})
            for k in ("영업이익률", "순이익률", "ROE", "ROA",
                      "부채비율", "유동비율"):
                v = ratios.get(k)
                if v is not None:
                    entry[k] = v
            ts.append(entry)
    if ts:
        out.setdefault("kr", {})["financials_ts"] = ts
    # 분기별 시계열(최근 4분기) — 밸류에이션 탭 "분기별 재무추이"용
    # (bot.dart_quarterly, 사용자 2026-08-16). 연도별 시계열과 동일
    # 항목만 저장(렌더 쪽 표 구성을 그대로 재사용하기 위함).
    try:
        from bot.dart_quarterly import get_quarterly_series
        # ⚠️ 표는 4분기만 보여주지만 **7분기를 받는다**(사용자 2026-08-19):
        # ROE(TTM)는 앞선 3분기가 있어야 계산되므로 4개만 받으면 **맨 오른쪽
        # 한 칸만** 값이 있고 나머지는 빈칸이 된다(실제로 그렇게 떴다).
        # 저장은 그대로 마지막 4개만 — 다른 화면(분기 추이 차트 등)의 길이를
        # 바꾸지 않으면서 표시 구간 전부에 TTM 을 채운다.
        q_series = get_quarterly_series(dart, ticker, n=_Q_TABLE + _TTM_LEAD)
        if q_series:
            q_ts = []
            for q in q_series[-_Q_TABLE:]:
                qentry = {"label": q["label"], "year": q["year"],
                         "quarter": q["quarter"], "fs_div": q["fs_div"]}
                for k in ("매출", "영업이익", "당기순이익", "자산총계",
                          "부채총계", "자본총계", "재고자산", "FCF"):
                    v = q["financials"].get(k)
                    if v is not None:
                        qentry[k] = v
                for k in ("영업이익률", "순이익률", "ROE", "ROA",
                          "부채비율", "유동비율"):
                    v = q["ratios"].get(k)
                    if v is not None:
                        qentry[k] = v
                # 이상치 플래그 전량 릴레이 — 하나라도 빠뜨리면 대시보드
                # 배지·각주가 dead code 가 되고 '—' 의 이유가 사라진다
                # (2026-08-16 독립 리뷰: 계정 불일치 플래그가 누락돼 있었음).
                relay_anomaly_fields(q["financials"], qentry)
                _c = q["financials"].get("_component_accounts")
                if _c:
                    qentry["_component_accounts"] = dict(_c)
                q_ts.append(qentry)
            if q_ts:
                out.setdefault("kr", {})["financials_q"] = q_ts
    except Exception as exc:
        log.debug("stock_snapshot: DART quarterly financials skipped: %s", exc)
    _apply_revenue_fallback(ticker, out.get("kr") or {})
    if out.get("kr"):
        out["kr"]["financials_ver"] = _KR_FIN_SCHEMA_VER
    return out


def _apply_revenue_fallback(ticker: str, kr: dict) -> None:
    """총액 계정을 안 주는 회사(증권·은행·보험)의 매출을 FnGuide 총액으로.

    ⚠️ **한 표 안에서 기준을 섞지 않는다**(사용자 2026-08-19 NH투자증권):
    옛 코드는 기간마다 독립적으로 채워, 분기 표가 `26,840억 · 48,641억 ·
    81,720억 · 5,787억` 이 됐다 — 앞 세 칸은 총액인데 마지막만 이자수익이라
    한 행에 두 기준이 섞였고, 행 이름은 '이자수익' 하나였다. 그래서 연간
    묶음과 분기 묶음을 **각각 전부-아니면-전무**로 채운다(`fill_series`).
    연간이 되고 분기가 안 되는 건 정상 — 서로 다른 표라 섞이지 않는다.

    검산은 `kr_revenue_fallback` 안에 있다 — 기간 일치·영업이익 교차 확인·
    총액>구성요소. 페이지는 묶음당 1회만 받는다(헬퍼 내부 캐시 재사용)."""
    if not kr:
        return
    groups = {
        # 연간 표는 `financials`(최신) + `financials_ts`(추이)를 함께 그린다.
        "연간": [e for e in ([kr.get("financials")]
                            + list(kr.get("financials_ts") or []))
               if isinstance(e, dict)],
        "분기": [e for e in (kr.get("financials_q") or []) if isinstance(e, dict)],
    }
    try:
        from bot.kr_revenue_fallback import fill_series
    except Exception as exc:                            # noqa: BLE001
        log.info("stock_snapshot: 매출 총액 보강 건너뜀(%s): %s", ticker, exc)
        return
    for label, entries in groups.items():
        if not entries:
            continue
        try:
            n = fill_series(ticker, [(e.get("year"), e.get("quarter"), e)
                                     for e in entries])
        except Exception as exc:                        # noqa: BLE001
            log.info("stock_snapshot: %s %s 매출 보강 실패: %s",
                     ticker, label, exc)
            continue
        if n:
            log.info("stock_snapshot: %s %s 매출 총액 %d개 기간 보강(FnGuide)",
                     ticker, label, n)


def _collect_financials(t, snap: dict) -> None:
    """Collect IS / BS / CF (annual + quarterly) from yfinance Ticker."""
    fins: dict = {}
    for label, attr_a, attr_q in (
        ("income_statement", "financials", "quarterly_financials"),
        ("balance_sheet", "balance_sheet", "quarterly_balance_sheet"),
        ("cash_flow", "cashflow", "quarterly_cashflow"),
    ):
        annual_df = getattr(t, attr_a, None)
        quarterly_df = getattr(t, attr_q, None)
        # ⚠️ 야후가 주는 **연간 5개년을 다 담는다**(예전엔 4개로 잘랐다).
        # 2026-08-22 JP 프로브 실측: 6758.T 는 원시 연간이 5열인데 스냅샷이
        # 4개만 담아 **양수 EPS 가 3개**뿐이라 PER 밴드 최소치(4점)를 못 넘었다
        # — 원천이 준 걸 우리가 버려서 화면이 빈 것이다. `_df_to_rows` 의 기본값
        # 도 5라 이게 원래 의도였던 것으로 보인다(4는 근거 주석이 없었다).
        a_rows = _df_to_rows(annual_df, max_periods=5)
        q_rows = _df_to_rows(quarterly_df, max_periods=8)
        if a_rows or q_rows:
            fins[label] = {}
            if a_rows:
                fins[label]["annual"] = a_rows
            if q_rows:
                fins[label]["quarterly"] = q_rows
    if fins:
        snap["financials"] = fins
        # ⚠️ 수집시각을 남긴다. 이 표는 스냅샷에 구워진 뒤 게으르게 갱신되는데
        # 라벨이 없어서 **몇 분기 묵은 표를 최신으로 오인**했다(사용자
        # 2026-08-18 삼양식품: 2026년 1·2분기가 나왔는데 화면은 2025-12 까지).
        # CLAUDE.md 실수기록 10-b: 데이터 위젯은 적용시각·소스 라벨 의무.
        import datetime as _dt
        snap["financials_asof"] = _dt.datetime.now(
            _dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


# 동종비교 수집기의 **스키마 버전**. 수집 로직이 달라질 때마다 올린다.
# ⚠️ 이게 없으면 옛 아카이브가 영원히 옛 결과로 굳는다: 대시보드는
# `peer_comps` 가 있으면 재수집을 건너뛰므로, 접미사 폴백·이름 정화·
# 자체계산 PER/PBR 을 넣어도(2026-08-18 #885) 이미 분석한 종목 화면은
# 그대로였다(사용자 스크린샷 — 머지 뒤에도 `240810.KS` 행이 통째로 비어
# 있었다). 필드 유무를 하나씩 냄새맡는 옛 방식은 새 필드를 넣을 때마다
# 조건을 같이 고쳐야 해서 매번 잊는다 → 버전 하나로 강제한다.
_PEER_SCHEMA_VER = 7


# 같은 시장의 **자매 보드**. 목록이 한쪽으로 쏠려 있어(실측: `.KS` 114 :
# `.KQ` 4 · `.TW` 145 : `.TWO` 10) 반대 보드 종목이 통째로 빈 행이 된다.
# ⚠️ 이 4개 시장은 종목코드가 두 보드에 **겹치지 않는다** — KRX 는 단일
# 번호체계, TWSE/TPEx 도 유일, 상하이는 6·68 로 선전은 0·3 으로 시작한다.
# 그래서 "반대쪽에서 조회되면 그게 맞다"가 성립한다(겹치는 시장이라면
# 다른 회사를 끌어올 수 있으니 이 표에 넣으면 안 된다).
# JP(.T)·HK(.HK)·US 는 단일 보드라 폴백 대상이 아니다.
_BOARD_ALT = {"KS": "KQ", "KQ": "KS", "TW": "TWO", "TWO": "TW",
              "SS": "SZ", "SZ": "SS"}


def norm_cur(c: str | None) -> str:
    """통화코드 정규화. GBp(펜스)는 GBP 와 같은 통화다.

    ⚠️ 대시보드 렌더도 이걸 쓴다(단일 출처). 복제하면 "만들지 않는 조건"과
    "⚠ 를 붙이는 조건"이 갈라져, 화면은 경고 없는데 값은 환산 오차인 상태가
    생긴다(시장타이밍 VKOSPI 복제 교훈과 같은 실패모드)."""
    return (c or "").strip().upper().replace("GBX", "GBP")


# ⚠️ 2026-08-20 제거: `_derive_peer_multiples` / `_dart_peer_multiples`
# (동종비교표용 PER·PBR·PSR 자체계산). 사용자 "억지로 없는거 계산해서 넣지마.
# ＊표시된거. 더 이상해져." — 재료를 맞춰 같은 정의로 계산해도, 비교표는 한
# 열에서 **세로로** 읽는 자리라 소스값과 나란히 놓이는 순간 기준이 섞인다
# (실측: SK하이닉스 6.6＊·삼성전자 10.8＊ 자체계산 vs TSMC 27.5·AMD 119.0
# 소스값이 같은 PER 열). ＊ 로 구분해도 눈은 세로로 비교한다 — 빈칸이 낫다.
# 되살리지 말 것. 단일 회사 화면(종합·밸류에이션 탭)의 `_derive_missing_
# multiples` 는 나란히 놓이지 않으므로 **그대로 유지**한다.


def _peer_name(pi: dict, pt: str) -> str:
    """피어 표시명. 쓸 수 없는 이름이면 티커로 떨어진다.

    ⚠️ yfinance 는 조회가 빗나가면 `shortName` 에 **식별자 나열**을 돌려준다
    — `240810.KS,0P00017YB3,330568` 이 화면에 회사명으로 찍혔다(사용자
    2026-08-18). 콤마로 이어진 토큰 뭉치는 회사명이 아니다."""
    nm = (pi.get("shortName") or pi.get("longName") or "").strip()
    code = pt.split(".")[0]
    if not nm or (("," in nm) and (code in nm or nm.count(",") >= 2)):
        return pt
    return nm[:30]


def _collect_peer_multiples(ticker: str, info: dict, snap: dict) -> None:
    """Collect peer company multiples for the comps tab.

    동종 8종 .info 를 병렬 fetch (2026-06-10 사용자 리스크-승인 속도 수정) —
    직렬 ~8-16초가 cold-path 최대 단일 병목이었음. 스레드마다 자기 전용
    yf.Ticker(공유 상태 0), 결과는 제출 순서대로 병합해 행 순서 보존
    (subject 첫 행 유지). 실패 종목은 직렬 때와 동일하게 그냥 빠짐."""
    try:
        from bot.market import resolve_peer_set
    except ImportError:
        return
    industry = info.get("industry", "")
    if not industry:
        return
    peers = resolve_peer_set(ticker, industry)
    if not peers:
        return
    import yfinance as yf

    def _fetch_one(pt: str) -> dict | None:
        try:
            pi = yf.Ticker(pt).info or {}
            # ⚠️ **보드 접미사 자동 폴백.** 피어 목록의 접미사가 틀리면 그 행이
            # 통째로 빈다 — `240810.KS`(원익IPS)·`319660.KS`(피에스케이)는
            # 실제로 코스닥이라 전 컬럼이 `—` 였다(사용자 2026-08-18 스크린샷).
            # 목록의 접미사를 손으로 고치면 또 틀린다(이번 세션에만 종목코드를
            # 두 번 잘못 적었다 — 실수 #12). 조회 결과로 판정해 반대쪽을
            # 한 번 더 시도한다: 시총조차 없으면 그 보드가 아니라는 뜻이다.
            _alt_sfx = (_BOARD_ALT.get(pt.rsplit(".", 1)[-1].upper())
                        if "." in pt else None)
            if not pi.get("marketCap") and _alt_sfx:
                alt = pt.rsplit(".", 1)[0] + "." + _alt_sfx
                alt_pi = yf.Ticker(alt).info or {}
                if alt_pi.get("marketCap"):
                    pi, pt = alt_pi, alt
            name = _peer_name(pi, pt)
            entry = {
                "ticker": pt,
                "name": name[:30],
                "currency": pi.get("currency", ""),
                # ⚠️ 재무통화 — PBR·PSR·EV/EBITDA 는 **재무제표에서 나온 분모**를
                # 거래통화 가격으로 나눈 값이라, 둘이 다르면 배수가 통째로
                # 틀린다. ASML NY ADR 이 실측 사례다(가격 USD · 재무 EUR →
                # PBR 1563.2 · EV/EBITDA 2917.0, 사용자 2026-08-16 스크린샷).
                # HK/CN 은 이 불일치가 가장 흔한 시장이라 같은 파손이 재발한다.
                # 이 필드가 없어 지금까지 가드를 걸 수조차 없었다.
                "financial_currency": pi.get("financialCurrency", ""),
                "market_cap": pi.get("marketCap"),
                "trailingPE": pi.get("trailingPE"),
                "forwardPE": pi.get("forwardPE"),
                "priceToBook": pi.get("priceToBook"),
                "priceToSalesTrailing12Months": pi.get("priceToSalesTrailing12Months"),
                "enterpriseToEbitda": pi.get("enterpriseToEbitda"),
                "dividendYield": pi.get("dividendYield"),
                "dividendRate": pi.get("dividendRate"),
                "currentPrice": pi.get("currentPrice") or pi.get("regularMarketPrice"),
            }
            # yfinance 는 KR 종목의 `trailingPE`·`priceToBook` 을 자주 안 준다
            # (2026-08-16 프로브로 확정 — 5개 키 전부 None). 그러면 동종비교
            # 표의 PER·PBR 열이 통째로 비어 비교가 불가능하다. 소스가 주는
            # 재료로 **같은 정의대로** 계산해 채우고, 자체계산분은 표시한다
            # (종합·밸류에이션 탭의 `_derive_missing_multiples` 와 동일 규약).
            # ⚠️ **DART 를 먼저** 태운다(KR 만 실제 동작). yfinance 는 KR
            # 재무를 몇 분기씩 늦게 준다 — 프로브 실측(2026-08-18 삼양식품):
            # 소스 분기가 2025-12 까지뿐이고 2026년 1·2분기가 아예 없다.
            # 늦은 재료로 만든 배수를 최신 소스값 옆에 나란히 놓으면 표 자체가
            # 거짓말이 된다. 최신(DART)이 이기고 `.info` 는 남은 칸만 채운다.
            # ⚠️ 주체 행은 제외 — 렌더가 종합·밸류에이션 탭과 같은 값으로
            # 채운다(검증 7축 ①'전섹션 일치').
            # ⚠️ 2026-08-20 사용자: "억지로 없는거 계산해서 넣지마.
            # ＊표시된거. 더 이상해져." — **비교표에는 소스값만** 싣는다.
            # 위 설명대로 재료를 맞춰 계산해도, 한 열에서 세로로 비교되는
            # 자리라 소스값과 나란히 놓이는 순간 기준이 섞여 비교가 어긋난다
            # (실측: SK하이닉스 6.6＊ vs TSMC 27.5 소스값이 같은 PER 열에).
            # 렌더도 옛 아카이브의 `derived` 칸을 빈칸으로 내리므로 이미 쌓인
            # 분석까지 함께 정리된다 — 재수집 불필요(실수 #18 의 반대 방향).
            entry = {k: v for k, v in entry.items() if v is not None}
            if pt == ticker:
                entry["is_subject"] = True
            return entry
        except Exception:
            return None

    targets = [ticker] + peers[:7]
    # ⚠️ corp_code 맵(zip 다운로드)을 **풀 밖에서** 한 번 데운다. 스레드마다
    # 처음 만나면 6개가 동시에 같은 zip 을 받으러 가고, 그게 아래 30초
    # future timeout 을 먹어 행이 통째로 빠진다.
    if any(t.upper().endswith((".KS", ".KQ")) for t in targets):
        try:
            from bot.dart_client import get_dart
            _d = get_dart()
            if _d:
                _d._load_corp_code_map()
        except Exception:
            pass
    results: list[dict | None]
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as pool:
            futs = [pool.submit(_fetch_one, pt) for pt in targets]
            results = []
            for fut in futs:
                try:
                    # DART 분기 시계열은 콜이 늘어 30초로는 모자란다
                    # — 타임아웃이 나면 그 행이 통째로 빠진다.
                    results.append(fut.result(timeout=60))
                except Exception:
                    results.append(None)
    except Exception:
        results = [_fetch_one(pt) for pt in targets]  # 직렬 폴백

    comps = [e for e in results if e]
    subject_added = any(e.get("is_subject") for e in comps)
    if comps and subject_added:
        snap["peer_comps"] = comps
        # 기준시각 — 이 표는 분석 시점 스냅샷에 구워진 뒤 FULL 오버레이가
        # 게으르게 갱신한다. 화면에 '언제 기준'이 없으면 며칠 전 값을 현재값
        # 으로 오인한다(사용자 2026-08-16 "언제 기준인거야?").
        # CLAUDE.md 실수기록 10-b: 데이터 위젯은 적용시각·소스 라벨 의무.
        import datetime as _dt
        snap["peer_comps_asof"] = _dt.datetime.now(
            _dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
        snap["peer_comps_ver"] = _PEER_SCHEMA_VER
