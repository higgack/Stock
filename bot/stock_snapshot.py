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

log = logging.getLogger(__name__)


def collect_stock_snapshot(ticker: str) -> dict | None:
    """Return a dict of company/market facts, or *None* on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info or info.get("quoteType") is None:
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
        if price:
            snap["current_price"] = price
        snap["market_cap"] = _g("marketCap")
        snap["shares_outstanding"] = _g("sharesOutstanding")

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

        # ── earnings history (compact — last 8 quarters) ────────────
        try:
            ed = t.earnings_dates
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
                    snap["earnings_history"] = rows
        except Exception:
            pass

        # ── analyst upgrades/downgrades (last 15) ───────────────────
        try:
            ud = t.upgrades_downgrades
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
                    snap["upgrades_downgrades"] = rows
        except Exception:
            pass

        # ── institutional holders (top 10) ──────────────────────────
        try:
            ih = t.institutional_holders
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
                    snap["institutional_holders"] = rows
        except Exception:
            pass

        # ── news (last 10) ──────────────────────────────────────────
        try:
            news = t.news
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
                    snap["news"] = rows
        except Exception:
            pass

        # ── financial statements (IS / BS / CF — annual + quarterly) ──
        try:
            _collect_financials(t, snap)
        except Exception:
            pass

        # ── peer multiples (for comps tab) ──────────────────────────
        try:
            _collect_peer_multiples(ticker, info, snap)
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
        elif ticker.endswith(".TW"):
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
    """
    stock_code = ticker.split(".")[0]

    # ── DART company info (법인등록번호·대표자·결산월·주소·산업분류) ──
    try:
        from bot.dart_client import get_dart
        dart = get_dart()
        ci = dart.get_company_info(stock_code) if dart else None
        if ci and ci.get("status") == "000":
            kr = snap.setdefault("kr", {})
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
    except Exception as exc:
        log.debug("stock_snapshot: DART company info skipped: %s", exc)

    # ── FSC item_info (법인등록번호 fallback · 시장구분) ──────────
    try:
        from bot.fsc_client import item_info as fsc_item_info, fsc_key_ready
        if fsc_key_ready():
            fi = fsc_item_info(ticker)
            if fi:
                kr = snap.setdefault("kr", {})
                crno = fi.get("crno")
                if crno and not kr.get("corp_reg_no"):
                    kr["corp_reg_no"] = crno
                mrkt = fi.get("mrktCtg")
                if mrkt:
                    kr["market_category"] = mrkt
    except Exception as exc:
        log.debug("stock_snapshot: FSC item_info skipped: %s", exc)

    # ── DART insider holdings (임원·주요주주 지분) ────────────────
    try:
        from bot.dart_client import get_dart
        dart = get_dart()
        if dart:
            holders = dart.get_insider_holdings(stock_code)
            if holders:
                snap.setdefault("kr", {})["insider_holdings"] = holders[:15]
    except Exception as exc:
        log.debug("stock_snapshot: DART insider holdings skipped: %s", exc)

    # ── DART recent disclosures (최근 공시) ───────────────────────
    try:
        from bot.dart_client import get_dart
        dart = get_dart()
        if dart:
            disclosures = dart.get_recent_disclosures(stock_code, days_back=365, limit=30)
            if disclosures:
                snap.setdefault("kr", {})["disclosures"] = disclosures
    except Exception as exc:
        log.debug("stock_snapshot: DART disclosures skipped: %s", exc)

    # ── DART normalized financials (K-IFRS 재무) ──────────────────
    try:
        from bot.dart_client import get_dart
        dart = get_dart()
        if dart:
            fin = dart.get_normalized_financials(ticker)
            if fin and fin.get("financials"):
                compact = {
                    "year": fin.get("year"),
                    "fs_div": fin.get("fs_div"),
                }
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
                snap.setdefault("kr", {})["financials"] = compact
    except Exception as exc:
        log.debug("stock_snapshot: DART financials skipped: %s", exc)

    # ── FSC minority holders (소액주주현황) ────────────────────────
    try:
        from bot.fsc_client import minority_holders, fsc_key_ready
        if fsc_key_ready():
            mh = minority_holders(ticker)
            if mh:
                snap.setdefault("kr", {})["minority"] = mh
    except Exception as exc:
        log.debug("stock_snapshot: FSC minority holders skipped: %s", exc)

    # ── DART multi-year financials (3-year time series) ───────────
    try:
        from bot.dart_client import get_dart
        from datetime import datetime as _dt
        dart = get_dart()
        if dart:
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
                snap.setdefault("kr", {})["financials_ts"] = ts
    except Exception as exc:
        log.debug("stock_snapshot: DART multi-year financials skipped: %s", exc)

    # ── KIS investor flow (수급) ──────────────────────────────────
    try:
        from bot.kis_client import KisClient
        kis = KisClient()
        if kis._ready():
            flow_data: dict = {}
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
            if flow_data:
                snap.setdefault("kr", {})["flow"] = flow_data
    except Exception as exc:
        log.debug("stock_snapshot: KIS flow skipped: %s", exc)

    # ── pykrx trends (외인·공매도 추이) ──────────────────────────
    try:
        from bot.pykrx_client import (
            get_kr_foreign_ownership_trend,
            get_kr_short_balance_trend,
        )
        trends: dict = {}
        fo = get_kr_foreign_ownership_trend(ticker, days_back=30)
        if fo:
            trends["foreign_ownership"] = fo
        sb = get_kr_short_balance_trend(ticker, days_back=30)
        if sb:
            trends["short_trend"] = sb
        if trends:
            snap.setdefault("kr", {}).setdefault("flow", {}).update(trends)
    except Exception as exc:
        log.debug("stock_snapshot: pykrx trends skipped: %s", exc)

    # ── FSC lockup releases + dilution events (리스크) ────────────
    try:
        from bot.fsc_client import lockup_releases as fsc_lockup, fsc_key_ready
        if fsc_key_ready():
            lr = fsc_lockup(ticker, lookback_days=7)
            if lr:
                snap.setdefault("kr", {})["lockup_releases"] = lr
    except Exception as exc:
        log.debug("stock_snapshot: FSC lockup skipped: %s", exc)

    try:
        from bot.fsc_client import dilution_events as fsc_dilution, fsc_key_ready
        if fsc_key_ready():
            de = fsc_dilution(ticker, lookback_days=10)
            if de:
                snap.setdefault("kr", {})["dilution_events"] = de
    except Exception as exc:
        log.debug("stock_snapshot: FSC dilution skipped: %s", exc)

    # ── KRX 시장경보 (거래정지/관리종목/투자경고/단기과열) ─────────
    try:
        from bot.krx_alert_client import get_krx_alert
        alert = get_krx_alert()
        status = alert.get_status(ticker)
        if status and (status.get("suspended") or status.get("admin")
                       or status.get("overheating") or status.get("warning_level")):
            snap.setdefault("kr", {})["market_alert"] = status
    except Exception as exc:
        log.debug("stock_snapshot: KRX alert skipped: %s", exc)

    # ── FnGuide + 한경 컨센서스 (yfinance 보완) ──────────────────
    if not snap.get("target_mean"):
        try:
            from bot.fnguide_consensus import fetch_consensus as fnguide_fetch
            fg = fnguide_fetch(ticker)
            if fg and fg.get("target_mean"):
                kr = snap.setdefault("kr", {})
                kr["consensus"] = {
                    "source": "FnGuide",
                    "target_mean": fg["target_mean"],
                    "rating": fg.get("rating"),
                    "n_analysts": fg.get("n_analysts"),
                }
        except Exception as exc:
            log.debug("stock_snapshot: FnGuide consensus skipped: %s", exc)

    # ── 리서치 리포트 (Naver Finance primary → 한경 fallback) ────────
    # Per-broker report rows populate the 리서치 액션 tab — yfinance has
    # no KR upgrade/downgrade feed. Naver Finance 종목 리서치 primary,
    # 한경 컨센서스 fallback (2026 redesign 이후 JS 렌더링으로 거의 사망).
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
            snap.setdefault("kr", {})["research_reports"] = _kr_research["reports"]
        if (not snap.get("target_mean")
                and not snap.get("kr", {}).get("consensus")
                and _kr_research.get("target_price")):
            snap.setdefault("kr", {})["consensus"] = {
                "source": "Naver Finance",
                "target_mean": _kr_research["target_price"],
                "rating": _kr_research.get("rating"),
                "n_analysts": _kr_research.get("analyst_count"),
                "last_report_date": _kr_research.get("last_report_date"),
            }

    # ── Naver 뉴스 폴백 (yfinance KR 뉴스 미커버) ─────────────────
    _collect_news_fallback(ticker, snap)

    # ── yfinance dividends (universal) ────────────────────────────
    _collect_dividends(ticker, snap)


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

    # ── Kabutan consensus (yfinance 보완) ────────────────────────
    if not snap.get("target_mean"):
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

    # ── cnyes 컨센서스 (yfinance TW 보완) ────────────────────────
    if not snap.get("target_mean"):
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
        elif ticker.endswith(".TW"):
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
    """Collect peer company multiples for the comps tab."""
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
    comps: list[dict] = []
    subject_added = False
    for pt in ([ticker] + peers[:7]):
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
                subject_added = True
            comps.append(entry)
        except Exception:
            continue
    if comps and subject_added:
        snap["peer_comps"] = comps
