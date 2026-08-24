"""SEC EDGAR client — 8-K material events + Form 4 insider trades.

No API key required. SEC rate limit: 10 req/s (enforced via 0.11s sleep).
User-Agent header required per SEC policy.
Cache: 12h for submissions JSON, permanent for resolved Form 4 XMLs.

Applies universally to all US-listed equities (no ETF/fund path).
Rule applies to all analyses going forward.
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

_log = logging.getLogger("edgar_client")

CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "edgar"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_UA = "StandardViewResearchBot contact@example.com"
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": _UA,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/html, */*",
})

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBS_URL    = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCH_BASE   = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
_CONCEPT_URL = ("https://data.sec.gov/api/xbrl/companyconcept/"
                "CIK{cik}/{taxonomy}/{concept}.json")

# 8-K item number → short human label
_ITEM_LABELS: dict[str, str] = {
    "1.01": "Material Agreement",
    "1.02": "Agreement Termination",
    "1.03": "Bankruptcy/Receivership",
    "2.01": "Acquisition/Disposition of Assets",
    "2.02": "Results of Operations (Earnings)",
    "2.03": "Material Financial Obligation",
    "2.04": "Triggering Events re Obligations",
    "2.05": "Cost Associated with Exit",
    "2.06": "Asset Impairment",
    "3.01": "Exchange Delisting",
    "3.02": "Unregistered Sales of Equity",
    "4.01": "Change of Auditor",
    "4.02": "Financial Statement Non-Reliance",
    "5.01": "Change in Control",
    "5.02": "Director/Officer Departure or Appointment",
    "5.03": "Amendments to Articles",
    "5.07": "Submission of Matters to Vote",
    "5.08": "Fiscal Year Change",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# SEC Form 4 transaction code → Korean label
_TX_LABELS: dict[str, str] = {
    "P": "매수 (Purchase)",
    "S": "매도 (Sale)",
    "A": "수령 (Award/Grant)",
    "D": "처분 (Disposition)",
    "F": "세금 납부 (Tax Withholding)",
    "G": "증여 (Gift)",
    "M": "옵션 행사 (Option Exercise)",
    "X": "파생 행사 (Derivative Exercise)",
    "C": "전환 (Conversion)",
    "W": "상속 (Will/Inheritance)",
    "Z": "신탁 (Trust)",
}

# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", key)
    return CACHE_DIR / f"{safe}.json"


def _load_cache(key: str, max_age_h: float = 12.0):
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        if (datetime.utcnow() - fetched).total_seconds() < max_age_h * 3600:
            return data.get("payload")
    except Exception:
        pass
    return None


def _save_cache(key: str, payload) -> None:
    try:
        _cache_path(key).write_text(
            json.dumps({"fetched_at": datetime.utcnow().isoformat(), "payload": payload},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        _log.warning("edgar cache write failed: %s", exc)


def _get(url: str, timeout: int = 20):
    time.sleep(0.12)  # SEC: max 10 req/s
    r = _SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r


# ── CIK resolution ────────────────────────────────────────────────────────────

def _ticker_to_cik(ticker: str) -> Optional[str]:
    """Return zero-padded 10-digit CIK string for a US ticker, or None."""
    key = "company_tickers"
    payload: dict | None = _load_cache(key, max_age_h=24)
    if payload is None:
        try:
            r = _get(_TICKERS_URL, timeout=20)
            raw = r.json()
            # {idx: {cik_str, ticker, title}}
            payload = {
                v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                for v in raw.values()
                if isinstance(v, dict) and v.get("ticker")
            }
            _save_cache(key, payload)
            _log.info("edgar: ticker→CIK map loaded (%d entries)", len(payload))
        except Exception as exc:
            _log.warning("edgar: company_tickers fetch failed: %s", exc)
            return None
    base_ticker = ticker.upper().split(".")[0]
    return (payload or {}).get(base_ticker)


def sec_ticker_names() -> dict:
    """{TICKER: 회사명(title)} — SEC company_tickers.json (무료·무키, SEC
    보고 기업 ~1만, ADR/OTC 보고기업 포함). 실적 캘린더 회사명 2차 소스
    (사용자 2026-06-11 HFUS 류 — NASDAQ Trader 미수록 보완). 7d 캐시.

    ⚠️ _ticker_to_cik 의 'company_tickers' 캐시는 ticker→CIK 만 담아 title
    이 없으므로 별도 key('company_tickers_names')로 title 맵 캐시."""
    key = "company_tickers_names"
    payload: dict | None = _load_cache(key, max_age_h=24 * 7)
    if payload is not None:
        return payload
    try:
        r = _get(_TICKERS_URL, timeout=20)
        raw = r.json()
        names = {
            v["ticker"].upper(): (v.get("title") or "").strip()
            for v in raw.values()
            if isinstance(v, dict) and v.get("ticker") and v.get("title")
        }
        if len(names) > 1000:
            _save_cache(key, names)
        return names
    except Exception as exc:
        _log.warning("edgar: company_tickers names fetch failed: %s", exc)
        return {}


# ── EDGAR submissions ─────────────────────────────────────────────────────────

def _submissions(cik: str) -> Optional[dict]:
    """Fetch/cache the EDGAR submissions JSON for a CIK (zero-padded 10-digit)."""
    key = f"subs_{cik}"
    payload = _load_cache(key, max_age_h=12)
    if payload is not None:
        return payload
    try:
        url = _SUBS_URL.format(cik=cik)
        r = _get(url)
        payload = r.json()
        _save_cache(key, payload)
        return payload
    except Exception as exc:
        _log.warning("edgar submissions fetch failed CIK=%s: %s", cik, exc)
        return None


# ── Form 4 XML parser ─────────────────────────────────────────────────────────

def _strip_ns(tag: str) -> str:
    """Strip XML namespace from tag like '{http://...}tagname' → 'tagname'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_text(root: ET.Element, *tags: str) -> Optional[str]:
    """Search root for any of the given tag names (namespace-agnostic)."""
    for el in root.iter():
        if _strip_ns(el.tag) in tags and el.text:
            return el.text.strip()
    return None


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    return [el for el in root.iter() if _strip_ns(el.tag) == tag]


def _parse_form4_xml(cik: str, accession: str, primary_doc: str) -> Optional[dict]:
    """Fetch + parse Form 4 XML → {name, title, transactions:[{code, label, shares, price, date}]}.

    Tries the primary document URL first; if that's not XML, fetches the filing
    index to locate the .xml file. Results cached permanently (resolved filings
    don't change).
    """
    acc_plain = accession.replace("-", "")
    key = f"form4_{acc_plain}"
    cached = _load_cache(key, max_age_h=24 * 365)
    if cached is not None:
        return cached

    cik_int = int(cik)
    base = _ARCH_BASE.format(cik=cik_int, acc=acc_plain)

    root: Optional[ET.Element] = None

    # 1) Try primary document
    if primary_doc:
        try:
            r = _get(f"{base}/{primary_doc}", timeout=20)
            content = r.content
            if b"ownershipDocument" in content or b"<?xml" in content:
                root = ET.fromstring(content)
        except Exception as exc:
            _log.debug("form4 primary_doc fetch failed: %s", exc)

    # 2) Fallback: filing index JSON → find the .xml file
    if root is None:
        try:
            idx_url = f"{base}/{accession}-index.json"
            r = _get(idx_url, timeout=15)
            idx = r.json()
            for item in (idx.get("directory", {}).get("item", []) or []):
                name = item.get("name", "")
                if name.lower().endswith(".xml"):
                    r2 = _get(f"{base}/{name}", timeout=20)
                    if b"ownershipDocument" in r2.content:
                        root = ET.fromstring(r2.content)
                        break
        except Exception as exc:
            _log.debug("form4 index fallback failed: %s", exc)

    if root is None:
        return None

    # Parse reporter name + title
    name = _find_text(root, "rptOwnerName") or "Unknown"
    title = _find_text(root, "officerTitle") or ""
    if not title:
        is_dir = _find_text(root, "isDirector")
        is_10pct = _find_text(root, "isTenPercentOwner")
        if is_dir == "1":
            title = "Director"
        elif is_10pct == "1":
            title = "10%+ Owner"

    # Parse non-derivative transactions
    transactions: list[dict] = []
    for tx in _find_all(root, "nonDerivativeTransaction"):
        code = _find_text(tx, "transactionCode") or ""
        shares_txt = _find_text(tx, "transactionShares")
        price_txt = _find_text(tx, "transactionPricePerShare")
        tx_date = _find_text(tx, "transactionDate", "value") or ""
        # Many fields are wrapped in <value> child
        if not shares_txt:
            for el in tx.iter():
                if _strip_ns(el.tag) == "transactionShares":
                    shares_txt = _find_text(el, "value") or ""
        if not price_txt:
            for el in tx.iter():
                if _strip_ns(el.tag) == "transactionPricePerShare":
                    price_txt = _find_text(el, "value") or ""
        if not tx_date:
            for el in tx.iter():
                if _strip_ns(el.tag) == "transactionDate":
                    tx_date = _find_text(el, "value") or ""
        try:
            shares = int(float(shares_txt)) if shares_txt else 0
        except Exception:
            shares = 0
        try:
            price: Optional[float] = float(price_txt) if price_txt else None
        except Exception:
            price = None
        if shares == 0:
            continue
        transactions.append({
            "code": code,
            "label": _TX_LABELS.get(code, code),
            "shares": shares,
            "price": price,
            "date": tx_date,
        })

    result = {"name": name, "title": title, "transactions": transactions}
    _save_cache(key, result)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def get_recent_8k(ticker: str, days: int = 60, top_n: int = 15) -> list[dict]:
    """Return recent 8-K filings for a US ticker.

    Each dict: {date, items_raw, items_labels:[str], accession, url}
    Returns [] for non-US tickers, CIK-miss, or network failure.
    """
    cik = _ticker_to_cik(ticker)
    if not cik:
        return []
    subs = _submissions(cik)
    if not subs:
        return []

    recent = subs.get("filings", {}).get("recent", {})
    forms    = recent.get("form", [])
    dates    = recent.get("filingDate", [])
    items_l  = recent.get("items", [])
    acc_l    = recent.get("accessionNumber", [])

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    results: list[dict] = []
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if filing_date < cutoff:
            # Submissions are date-desc; once past cutoff we can stop
            if results or filing_date:
                break
            continue
        items_raw = items_l[i] if i < len(items_l) else ""
        acc = acc_l[i] if i < len(acc_l) else ""

        item_nums = [x.strip() for x in items_raw.split(",") if x.strip()]
        labels = [f"§{n} {_ITEM_LABELS.get(n, '').strip()}" for n in item_nums if n]

        results.append({
            "date": filing_date,
            "items_raw": items_raw,
            "items_labels": [l for l in labels if l.strip() != "§"],
            "accession": acc,
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik)}&type=8-K",
        })
        if len(results) >= top_n:
            break
    return results


def get_recent_form4(ticker: str, days: int = 30, top_n: int = 5) -> list[dict]:
    """Return recent Form 4 insider trades for a US ticker.

    Each dict: {filing_date, reporter_name, title, transactions:[{code, label, shares, price, date}]}
    Returns [] on CIK-miss, network failure, or non-US ticker.
    """
    cik = _ticker_to_cik(ticker)
    if not cik:
        return []
    subs = _submissions(cik)
    if not subs:
        return []

    recent = subs.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    dates   = recent.get("filingDate", [])
    acc_l   = recent.get("accessionNumber", [])
    docs    = recent.get("primaryDocument", [])

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    results: list[dict] = []
    for i, form in enumerate(forms):
        if form not in ("4", "4/A"):
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if filing_date < cutoff:
            if results or filing_date:
                break
            continue
        acc = acc_l[i] if i < len(acc_l) else ""
        doc = docs[i] if i < len(docs) else ""

        parsed = _parse_form4_xml(cik, acc, doc)
        if not parsed:
            continue
        if not parsed.get("transactions"):
            continue

        results.append({
            "filing_date": filing_date,
            "reporter_name": parsed["name"],
            "title": parsed["title"],
            "transactions": parsed["transactions"],
        })
        if len(results) >= top_n:
            break
    return results


# ── Prompt formatter ──────────────────────────────────────────────────────────

def format_edgar_8k_block(filings: list[dict]) -> str:
    """Format 8-K list for agent prompt injection."""
    if not filings:
        return ""
    lines = ["=== SEC 8-K 공시 (최근 30일, EDGAR) ==="]
    for f in filings:
        labels = f.get("items_labels") or []
        items_str = " / ".join(labels) if labels else f.get("items_raw", "")
        lines.append(f"• {f['date']}: {items_str}")
    lines.append(
        "▶ 8-K 공시는 중요 사건 (Material Event) 필링이다 — 위 항목이 5거래일 가격"
        " 동인이 될 수 있다. §2.02 (Earnings) / §1.01 (계약) / §5.02 (임원 변동)"
        " 등을 결론에 인용할 것. ⚠️ yfinance 뉴스 기사가 0건이어도 위 8-K 를"
        " news/sentiment 의 primary source 로 활용 — '관련 뉴스 없음' 결론 금지"
        " (없는 내용 날조 금지)."
    )
    return "\n".join(lines)


def format_edgar_form4_block(filings: list[dict]) -> str:
    """Format Form 4 insider-trade list for agent prompt injection."""
    if not filings:
        return ""
    lines = ["=== SEC Form 4 내부자 거래 (최근 30일, EDGAR) ==="]
    for f in filings:
        name = f.get("reporter_name", "Unknown")
        title = f.get("title", "")
        title_str = f" ({title})" if title else ""
        txns = f.get("transactions") or []
        for tx in txns:
            code = tx.get("code", "")
            label = tx.get("label", code)
            shares = tx.get("shares", 0)
            price = tx.get("price")
            tx_date = tx.get("date") or f.get("filing_date", "")
            price_str = f" @ ${price:,.2f}" if isinstance(price, float) and price > 0 else ""
            lines.append(
                f"• {tx_date} {name}{title_str} — {label}"
                f" {shares:,}주{price_str}"
            )
    lines.append(
        "▶ 내부자 매수 (P/M/A) = 경영진 확신 신호. 내부자 매도 (S/D/F) = 단기"
        " 약세 또는 세금 목적. 세금 납부 (F) 는 중립 (보상 수령 후 자동 withhold)."
        " 10%+ 주주 대량 매수/매도는 특히 주목 (Block trade 수준)."
    )
    return "\n".join(lines)


# ── XBRL structured financials (data.sec.gov/api/xbrl) ─────────────────────────
# Authoritative US financial-statement line items from the actual 10-K / 10-Q
# filings (us-gaap / dei taxonomy). This brings US fundamentals to the same
# "official filing" tier as KR(DART) / JP(EDINET) / TW(MOPS) — closing the
# US-side asymmetry where we otherwise lean on yfinance's aggregation.
# Each metric maps to an ordered list of (taxonomy, concept) fallbacks —
# filers tag the same line item differently, AND foreign private issuers
# (ADRs) file 20-F with the IFRS taxonomy (ifrs-full) instead of us-gaap.
# unit_kind: "money" | "eps" | "shares" (drives currency-unit selection).
# Tuple: (metric_key, [(taxonomy, concept), ...], unit_kind, label)
_XBRL_METRICS = [
    ("revenue", [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"), ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    ], "money", "매출"),
    ("net_income", [
        ("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss"),
        ("ifrs-full", "ProfitLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
    ], "money", "순이익"),
    ("eps_diluted", [
        ("us-gaap", "EarningsPerShareDiluted"),
        ("ifrs-full", "DilutedEarningsLossPerShare"),
    ], "eps", "EPS(희석)"),
    ("op_cash_flow", [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ], "money", "영업현금흐름"),
    ("assets", [("us-gaap", "Assets"), ("ifrs-full", "Assets")], "money", "총자산"),
    ("liabilities", [("us-gaap", "Liabilities"), ("ifrs-full", "Liabilities")],
     "money", "총부채"),
    ("equity", [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("ifrs-full", "Equity"),
        ("ifrs-full", "EquityAttributableToOwnersOfParent"),
    ], "money", "자본총계"),
    ("shares", [("dei", "EntityCommonStockSharesOutstanding")], "shares", "발행주식수"),
]

# Annual report forms across domestic (10-K) + foreign (20-F / 40-F) filers.
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def _fetch_concept(cik: str, taxonomy: str, concept: str) -> Optional[dict]:
    """Fetch + 12h-cache one XBRL companyconcept JSON. A 404 (concept not used
    by this filer) is cached as empty so we don't refetch every time."""
    key = f"xbrl_{cik}_{taxonomy}_{concept}"
    cached = _load_cache(key, max_age_h=12)
    if cached is not None:
        return cached or None
    try:
        url = _CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, concept=concept)
        r = _get(url)
        payload = r.json()
        _save_cache(key, payload)
        return payload
    except Exception:
        _save_cache(key, {})  # concept absent for this filer — remember it
        return None


def _choose_unit(units: dict, unit_kind: str) -> Optional[str]:
    """Pick the unit key. money → USD else a 3-letter currency (ADRs may
    report EUR/JPY/CNY). eps → '<cur>/shares'. shares → 'shares'."""
    keys = [k for k in units if units.get(k)]
    if not keys:
        return None
    if unit_kind == "money":
        if "USD" in keys:
            return "USD"
        cur = [k for k in keys if len(k) == 3 and k.isalpha() and k.isupper()]
        if cur:
            return max(cur, key=lambda k: len(units[k]))
    elif unit_kind == "eps":
        if "USD/shares" in keys:
            return "USD/shares"
        per = [k for k in keys if k.endswith("/shares")]
        if per:
            return max(per, key=lambda k: len(units[k]))
    else:  # shares
        if "shares" in keys:
            return "shares"
    return keys[0]


def _ord(x: str) -> int:
    """문자열을 정렬 가능한 정수로 — `min` 안에서 '최신 filed 우선'을 쓰려고."""
    return sum((ord(c) << (8 * max(0, 12 - i))) for i, c in enumerate(str(x)[:12]))


def _fact_days(f: dict) -> Optional[int]:
    """기간 팩트의 길이(일). 시점(instant) 팩트면 None."""
    import datetime as _dt
    s, e = (f or {}).get("start"), (f or {}).get("end")
    if not s or not e:
        return None
    try:
        return (_dt.date.fromisoformat(str(e))
                - _dt.date.fromisoformat(str(s))).days
    except (TypeError, ValueError):
        return None


def restore_quarter(dur: list, end: str) -> Optional[dict]:
    """`end` 에서 끝나는 **단일 분기**를 누적 팩트 차분으로 복원 → 팩트 dict
    또는 None.

    ⚠️ 대부분의 미국 제출사는 **현금흐름표를 누적(YTD)으로만** 낸다 —
    분기 길이 팩트가 회계연도 1분기에만 있어서, `_pick_facts` 의 '분기 길이
    우선' 이 그 한 분기에 붙박이가 된다. LRCX 실측(2026-08-24): 매출·순이익·
    EPS 는 `25-12-29~26-03-29`(FY2026 3분기)인데 **영업현금흐름만
    `25-06-30~25-09-28`**(1분기) — 열 헤더가 '최근 분기'인데 한 행만 두 분기
    뒤처져 있었다. 열은 **세로로** 읽히므로 행마다 '최근'이 다르면 그 열의
    정의가 갈린다(#32·#45).

    같은 `start`(회계연도 시작)를 공유하는 두 누적을 빼면 그 사이 분기가
    정확히 나온다 — DART 4분기 차분(`_undo_cumulative_cf`)·EDGAR 결산분기
    복원과 **같은 규율**이다(#96). 구조 가드 셋:
      · 두 팩트의 `start` 가 같아야 한다(같은 누적 계열)
      · 회계연도(`fy`)가 같아야 한다(해를 넘어 빼면 무의미)
      · 결과 길이가 80~100일이어야 한다(분기가 아니면 만들지 않는다)
    재작성(restatement) 위험은 각 후보에서 **최신 `filed`** 를 골라 줄인다.
    """
    import datetime as _dt
    cum = [f for f in dur
           if f.get("end") == end and (_fact_days(f) or 0) > 100
           and f.get("start")]
    if not cum:
        return None
    # 가장 짧은 누적 = 목표 분기에 가장 가까운 것(9개월 < 12개월)
    c = min(cum, key=lambda f: (_fact_days(f) or 10**6, -_ord(f.get("filed", ""))))
    prev = [f for f in dur
            if f.get("start") == c.get("start") and str(f.get("end", "")) < str(end)
            and f.get("fy") == c.get("fy")]
    if not prev:
        return None
    p = max(prev, key=lambda f: (str(f.get("end", "")), _ord(f.get("filed", ""))))
    try:
        s0 = _dt.date.fromisoformat(str(p["end"])) + _dt.timedelta(days=1)
        days = (_dt.date.fromisoformat(str(end)) - s0).days
    except (TypeError, ValueError, KeyError):
        return None
    if not (80 <= days <= 100):
        return None
    cv, pv = c.get("val"), p.get("val")
    if not isinstance(cv, (int, float)) or not isinstance(pv, (int, float)):
        return None
    # ⚠️ **빼서 만든 값은 형제와 크기를 대조한다**(#167) — 두 누적이 서로 다른
    # 제출본(재작성)에서 오면 결과가 쓰레기가 되고, 그 한 칸이 그대로 화면에
    # 실린다. 원천이 분기로 준 팩트가 하나라도 있으면 그것을 형제로 쓴다
    # (누적만 내는 제출사는 형제가 없어 구조 가드에만 의지한다).
    sib = [abs(float(f["val"])) for f in dur
           if 80 <= (_fact_days(f) or 0) <= 100
           and isinstance(f.get("val"), (int, float))]
    if sib and abs(cv - pv) > max(sib) * 5:
        return None
    return {"val": cv - pv, "fy": c.get("fy"), "fp": c.get("fp"),
            "form": c.get("form"), "filed": c.get("filed"),
            "start": s0.isoformat(), "end": end, "restored": True}


def _pick_facts(units: dict, unit_kind: str) -> Optional[dict]:
    """Pick latest annual (FY / 10-K·20-F·40-F) + latest-reported fact +
    the chosen currency unit. Restatements resolved by max `filed`.

    ⚠️ **'최근 분기' 는 분기 길이여야 한다**(사용자 2026-08-24 마이크론
    MU: 최근 분기 매출 $78.96B · EPS 41.40 — 연간 $37.38B·7.59 보다 크다).
    SEC companyconcept 는 같은 `end` 에 3·6·9·12개월 팩트를 함께 담는데,
    옛 코드는 `max(end, filed)` 로만 골라 **누적 팩트**를 분기라고 찍었다
    (#96 한 응답의 같은 필드가 표마다 의미가 다르다). 기간 팩트는 80~100일
    짜리만 '분기'로 인정하고, 없으면 **가장 짧은 것**을 쓰되 호출부가
    실제 기간을 화면에 적어 사용자가 볼 수 있게 한다(#43).
    """
    chosen = _choose_unit(units, unit_kind)
    if not chosen:
        return None
    arr = units.get(chosen) or []
    if not arr:
        return None
    fy = [f for f in arr
          if f.get("fp") == "FY" and str(f.get("form", "")).startswith(_ANNUAL_FORMS)]
    if not fy:
        fy = [f for f in arr if f.get("fp") == "FY"]
    # ⚠️ '연간' 도 **한 해 길이**여야 한다 — `fp == "FY"` 만 보면 누적 팩트가
    # 섞여 들어올 수 있다(#238 은 '최근 분기' 만 고쳤고 연간 쪽은 안 봤다).
    # 한 해 길이가 하나도 없으면 그대로 둔다(빈칸을 만들지 않는다, #171).
    _fy_yr = [f for f in fy if 350 <= (_fact_days(f) or 0) <= 380]
    annual = (max(_fy_yr or fy, key=lambda f: (f.get("end", ""), f.get("filed", "")))
              if fy else None)
    _dur = [f for f in arr if _fact_days(f) is not None]
    if _dur:
        _q = [f for f in _dur if 80 <= (_fact_days(f) or 0) <= 100]
        pool = _q or _dur
        _end = max(f.get("end", "") for f in pool)
        pool = [f for f in pool if f.get("end", "") == _end]
        # 분기 길이가 없으면 **가장 짧은** 기간을 쓴다 — 누적을 분기라고
        # 부르는 것보다 낫고, 호출부가 기간을 적으므로 거짓말이 아니다.
        latest = min(pool, key=lambda f: (_fact_days(f) or 10**6,
                                          -_ord(f.get("filed", ""))))
        # ⚠️ 분기 길이 팩트가 **옛 분기에만** 있으면 위 선택이 거기 붙박이가
        # 된다(현금흐름표를 누적으로만 내는 제출사 — LRCX 실측 두 분기 뒤처짐).
        # 전체 최신 `end` 가 아니거나 분기 길이가 아니면 누적 차분으로 복원.
        _all_end = max(f.get("end", "") for f in _dur)
        if (latest.get("end") != _all_end
                or not 80 <= (_fact_days(latest) or 0) <= 100):
            _r = restore_quarter(_dur, _all_end)
            if _r:
                latest = _r
    else:
        latest = max(arr, key=lambda f: (f.get("end", ""), f.get("filed", "")))
    return {"unit": chosen, "annual": annual, "latest": latest}


def get_key_financials(ticker: str) -> Optional[dict]:
    """Return authoritative key financials for a US/ADR ticker from SEC XBRL,
    or None. Shape: {"cik", "metrics": {metric: {concept, taxonomy, unit,
    annual, latest}}}. annual/latest are XBRL fact dicts."""
    try:
        cik = _ticker_to_cik(ticker)
    except Exception:
        cik = None
    if not cik:
        return None
    def _recency(c: dict) -> str:
        """그 후보가 가진 **가장 최근 종료일** — 후보 선택 기준."""
        return max(str((c.get(k) or {}).get("end") or "")
                   for k in ("annual", "latest"))

    metrics: dict = {}
    for metric, pairs, kind, _label in _XBRL_METRICS:
        # ⚠️ **데이터가 있는 첫 후보에서 멈추면 안 된다.** 발행사는 태그를
        # 바꾼다 — NVDA 실측(2026-08-24): 매출 첫 후보가 FY2022 까지만 있는
        # 옛 태그라 '연간 $26.91B FY2022 · 최근 분기 $3.10B(2020-01-26)' 이
        # 화면에 올랐다(다른 행은 전부 FY2026). 나머지 후보도 다 보고 **가장
        # 최근 데이터를 가진 것**을 고른다(#150 '우리가 그걸 다 쓰고 있나').
        # 동률이면 목록 순서(선호도)가 이긴다 — `>` 라 먼저 온 게 남는다.
        # 비용: 후보를 전부 조회하지만 `_fetch_concept` 는 12시간 디스크
        # 캐시이고 404 도 빈 값으로 캐시한다(#61 비용의 어느 단계인지).
        best: dict | None = None
        for tax, concept in pairs:
            data = _fetch_concept(cik, tax, concept)
            if not data:
                continue
            picked = _pick_facts(data.get("units") or {}, kind)
            if not (picked and (picked.get("annual") or picked.get("latest"))):
                continue
            cand = {"concept": concept, "taxonomy": tax, **picked}
            if best is None or _recency(cand) > _recency(best):
                best = cand
        if best:
            metrics[metric] = best
    if not metrics:
        return None
    return {"cik": cik, "metrics": metrics}


def _fmt_money(v, unit: str = "USD") -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    sym = "$" if unit == "USD" else f"{unit} "
    a = abs(v)
    if a >= 1e9:
        return f"{sym}{v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sym}{v / 1e6:,.1f}M"
    return f"{sym}{v:,.0f}"


def format_xbrl_block(financials: Optional[dict], yf_shares=None) -> str:
    """Render the SEC XBRL key financials as a Korean prompt block. Empty
    string when unavailable. `yf_shares` (yfinance sharesOutstanding) enables
    a robust divergence ⚠️ on share count (point-in-time → cleanly comparable;
    flow-metric TTM-vs-FY comparison is too noisy to auto-flag, deferred)."""
    if not financials or not financials.get("metrics"):
        return ""
    m = financials["metrics"]
    label = {k: lbl for (k, _p, _u, lbl) in _XBRL_METRICS}
    is_ifrs = any(d.get("taxonomy") == "ifrs-full" for d in m.values())
    src = "us-gaap/dei" if not is_ifrs else "IFRS/dei (ADR 20-F)"
    lines = [f"=== 📄 SEC EDGAR XBRL — 공식 제출 재무 ({src} 원본) ==="]
    order = ["revenue", "net_income", "eps_diluted", "op_cash_flow",
             "assets", "liabilities", "equity", "shares"]
    for metric in order:
        d = m.get(metric)
        if not d:
            continue
        ann = d.get("annual")
        if not ann:
            continue
        unit = d.get("unit", "USD")

        def _disp(fact):
            v = fact.get("val")
            if metric == "eps_diluted":
                sym = "$" if unit.startswith("USD") else unit.split("/")[0] + " "
                try:
                    return f"{sym}{float(v):,.2f}"
                except Exception:
                    return str(v)
            if metric == "shares":
                try:
                    return f"{float(v) / 1e6:,.0f}M주"
                except Exception:
                    return str(v)
            return _fmt_money(v, unit)

        line = (f"- {label.get(metric, metric)} (FY{ann.get('fy','')}, "
                f"{ann.get('form','')}, filed {ann.get('filed','')}): {_disp(ann)}")
        lat = d.get("latest")
        if (metric in ("revenue", "net_income", "eps_diluted", "op_cash_flow")
                and lat and lat.get("fp") not in (None, "FY")
                and lat.get("end") != ann.get("end")):
            line += f"  · 최근 {lat.get('fp','Q')}({lat.get('end','')}): {_disp(lat)}"
        lines.append(line)

    # Shares divergence (robust — point-in-time). yfinance staleness / split.
    sh = m.get("shares")
    if sh and yf_shares:
        xb = (sh.get("latest") or sh.get("annual") or {}).get("val")
        try:
            xb_f, yf_f = float(xb), float(yf_shares)
            if xb_f > 0 and abs(xb_f - yf_f) / xb_f > 0.10:
                lines.append(
                    f"⚠️ 발행주식수 불일치: yfinance {yf_f/1e6:,.0f}M vs SEC "
                    f"{xb_f/1e6:,.0f}M ({abs(xb_f-yf_f)/xb_f*100:.0f}% 차이) — "
                    f"분할/stale 의심. SEC 원본 우선."
                )
        except Exception:
            pass

    lines.append(
        "▶ 재무 수치는 이 SEC 원본 XBRL 을 **최우선 인용**. yfinance 와 "
        "다르면 SEC 제출본이 정본 — 임의 추정·재계산 금지, 위 수치를 글자 "
        "단위로 사용. (분기 ≠ 연간 비교 시 기간 라벨 명시.)"
    )
    return "\n".join(lines)
