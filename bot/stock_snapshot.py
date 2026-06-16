"""Collect a company info snapshot from yfinance for archive storage.

Called by ``archive.save_analysis`` — one extra yfinance ``.info`` call
per analysis (~0.5s, non-fatal). The snapshot powers the detail-page
header cards, company info section, and consensus card. All fields are
optional — missing data renders gracefully as "—".

KR tickers (.KS/.KQ) get additional data from DART + FSC (법인등록번호,
DART 대표자, CEO, 결산월, 공시, 임원지분, 소액주주, K-IFRS 재무).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bot.price_sanity import within_52w_range

log = logging.getLogger(__name__)


def collect_stock_snapshot(ticker: str) -> dict | None:
    """Return a dict of company/market facts, or *None* on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
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
                        snap["price_glitch_note"] = (
                            f"소스 이상치 보정 — 수신가 {float(price):,.2f} 가 "
                            f"조정 종가 {hc:,.2f} 대비 ±75% 초과(분할 미조정) "
                            f"→ 조정 종가로 표시")
                        price = hc
                        if shares:
                            snap["market_cap"] = hc * float(shares)
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

        _aux_tasks = (_aux_earnings, _aux_upgrades, _aux_holders,
                      _aux_news, _aux_financials, _aux_peers)
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as pool:
                futs = [pool.submit(fn) for fn in _aux_tasks]
                for fut in futs:
                    try:
                        snap.update(fut.result(timeout=60) or {})
                    except Exception:
                        pass
        except Exception:
            # 풀 생성 실패 등 — 직렬 폴백 (동작 동일, 속도만 원래대로)
            for fn in _aux_tasks:
                try:
                    snap.update(fn() or {})
                except Exception:
                    pass

        # strip None values to keep JSON compact
        snap = {k: v for k, v in snap.items() if v is not None}

        # Market-specific enrichment — additive overlay per market
        if ticker.endswith((".KS", ".KQ")):
            try:
                _enrich_kr(ticker, snap)
            except Exception as exc:
                log.warning("stock_snapshot: KR enrich skipped for %s: %s", ticker, exc)
        elif ticker.endswith(".T"):
            try:
                _enrich_jp(ticker, snap)
            except Exception as exc:
                log.warning("stock_snapshot: JP enrich skipped for %s: %s", ticker, exc)
        elif ticker.endswith((".TW", ".TWO")):
            try:
                _enrich_tw(ticker, snap)
            except Exception as exc:
                log.warning("stock_snapshot: TW enrich skipped for %s: %s", ticker, exc)
        elif ticker.endswith((".SS", ".SZ", ".BJ", ".HK")):
            try:
                _enrich_cn(ticker, snap)
            except Exception as exc:
                log.warning("stock_snapshot: CN enrich skipped for %s: %s", ticker, exc)
        else:
            try:
                _enrich_us(ticker, snap)
            except Exception as exc:
                log.warning("stock_snapshot: US enrich skipped for %s: %s", ticker, exc)

        return snap

    except Exception as exc:
        log.warning("stock_snapshot: failed for %s: %s", ticker, exc)
        return None


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
                      "부채총계", "자본총계"):
                v = fin["financials"].get(k)
                if v is not None:
                    compact[k] = v
            ratios = fin.get("ratios", {})
            for k in ("영업이익률", "순이익률", "ROE", "ROA",
                      "부채비율", "유동비율"):
                v = ratios.get(k)
                if v is not None:
                    compact[k] = v
            out.setdefault("kr", {})["financials"] = compact
        current_year = _dt.now().year
        ts = []
        for yr in range(current_year - 1, current_year - 4, -1):
            fin = dart.get_normalized_financials(ticker, year=yr)
            if fin and fin.get("financials"):
                entry = {"year": fin.get("year"), "fs_div": fin.get("fs_div")}
                for k in ("매출", "영업이익", "당기순이익", "자산총계",
                          "부채총계", "자본총계"):
                    v = fin["financials"].get(k)
                    if v is not None:
                        entry[k] = v
                ratios = fin.get("ratios", {})
                for k in ("영업이익률", "순이익률", "ROE", "ROA",
                          "부채비율", "유동비율"):
                    v = ratios.get(k)
                    if v is not None:
                        entry[k] = v
                ts.append(entry)
        if ts:
            out.setdefault("kr", {})["financials_ts"] = ts
        return out

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

    def _t_fsc_risk() -> dict:
        # lockup + dilution — 같은 FSC 키 게이트, 한 task 순차.
        out: dict = {}
        from bot.fsc_client import fsc_key_ready
        if not fsc_key_ready():
            return out
        try:
            from bot.fsc_client import lockup_releases as fsc_lockup
            lr = fsc_lockup(ticker, lookback_days=7)
            if lr:
                out.setdefault("kr", {})["lockup_releases"] = lr
        except Exception as exc:
            log.debug("stock_snapshot: FSC lockup skipped: %s", exc)
        try:
            from bot.fsc_client import dilution_events as fsc_dilution
            de = fsc_dilution(ticker, lookback_days=10)
            if de:
                out.setdefault("kr", {})["dilution_events"] = de
        except Exception as exc:
            log.debug("stock_snapshot: FSC dilution skipped: %s", exc)
        return out

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
    tasks = [_t_dart_company, _t_fsc_item, _t_dart_insider,
             _t_dart_disclosures, _t_dart_financials, _t_fsc_minority,
             _t_flow, _t_fsc_risk, _t_krx_alert, _t_fnguide, _t_research,
             _t_dividends]

    results: list[dict | None] = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(fn) for fn in tasks]
            for fut in futs:
                try:
                    results.append(fut.result(timeout=60))
                except Exception as exc:
                    log.debug("stock_snapshot: KR task skipped: %s", exc)
                    results.append(None)
    except Exception:
        results = []
        for fn in tasks:   # 풀 실패 — 직렬 폴백 (동작 동일)
            try:
                results.append(fn())
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
        a_rows = _df_to_rows(annual_df, max_periods=4)
        q_rows = _df_to_rows(quarterly_df, max_periods=8)
        if a_rows or q_rows:
            fins[label] = {}
            if a_rows:
                fins[label]["annual"] = a_rows
            if q_rows:
                fins[label]["quarterly"] = q_rows
    if fins:
        snap["financials"] = fins


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
            name = pi.get("shortName") or pi.get("longName") or pt
            entry = {
                "ticker": pt,
                "name": name[:30],
                "currency": pi.get("currency", ""),
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
            entry = {k: v for k, v in entry.items() if v is not None}
            if pt == ticker:
                entry["is_subject"] = True
            return entry
        except Exception:
            return None

    targets = [ticker] + peers[:7]
    results: list[dict | None]
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as pool:
            futs = [pool.submit(_fetch_one, pt) for pt in targets]
            results = []
            for fut in futs:
                try:
                    results.append(fut.result(timeout=30))
                except Exception:
                    results.append(None)
    except Exception:
        results = [_fetch_one(pt) for pt in targets]  # 직렬 폴백

    comps = [e for e in results if e]
    subject_added = any(e.get("is_subject") for e in comps)
    if comps and subject_added:
        snap["peer_comps"] = comps
