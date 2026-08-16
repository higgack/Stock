"""Thin client for DART (전자공시시스템) OpenAPI.

Provides three pieces of KR-market data that yfinance either doesn't
have or returns garbage for:

1. Recent disclosures (공시) — what the company filed in the last
   N days; surfaces guidance updates, lawsuits, M&A announcements
   etc. that move the stock but never show up in English news feeds.
2. Insider / major shareholder holdings (임원·주요주주 지분) — Form 4
   equivalent for Korea. yfinance returns 0% / N/A for these on
   KRX-listed names.
3. Next-earnings window estimate — DART doesn't publish a 'next
   earnings date' field, but quarterly reports are due within 45
   days of quarter end by law, so we infer a window from the
   current date and Q-end pattern.

Design goals:
- **Graceful degradation**: if `DART_API_KEY` is unset, the network
  is down, or DART returns an error, every method returns empty / None
  and logs a warning. The rest of the bot must keep running for non-KR
  analyses, and a KR analysis with missing DART data is better than no
  analysis at all.
- **Disk-cached corp_code mapping**: DART uses an 8-digit `corp_code`
  internal ID, not the 6-digit stock code. The mapping is downloaded
  as a zipped XML from `/api/corpCode.xml` and cached for 30 days at
  `~/.tradingagents/cache/dart_corpcode.json`. ~80k entries, ~3 MB.
- **No singleton magic**: callers construct `DartClient()` once and
  reuse it. Module-level `get_dart()` returns a process-wide cached
  instance.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.dart")

_DART_BASE = "https://opendart.fss.or.kr/api"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache"
# v2 cache includes both stock_code→corp_code and normalized_name→entries.
# Old v1 file (stock_code → corp_code only) is ignored and superseded;
# anyone upgrading just re-downloads the corp_code.xml on first call.
_CORPCODE_CACHE = _CACHE_DIR / "dart_corpcode_v2.json"
_CORPCODE_TTL_DAYS = 30
_HTTP_TIMEOUT = 10  # seconds — keep tight so a slow DART doesn't stall analysis
_HOT_CACHE_TTL_HOURS = 12  # disclosures / insider holdings change at most daily


def _disk_cache_daily(fn):
    """F3 (2026-05-29 audit): per-(stock_code, today) 12h disk cache for the
    DART network methods that previously hit the network on EVERY call.

    build_instrument_context runs up to 8× per analysis + the inline KR D1
    block, so `get_recent_disclosures` / `get_insider_holdings` were each
    fetched ~8× over the network per KR analysis — while every sibling KR
    client (pykrx, naver, krx_alert, kis) is already disk-cached. DART was
    the lone uncached outlier and the most likely 429 / stall point
    (CLAUDE.md warns of 'DART 429 / rate limits').

    Only caches TRUTHY results — an empty list (key missing / corp_code
    unresolved / transient DART error) is NOT cached, so a temporary
    failure doesn't get pinned for 12h and recovery is immediate.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(self, stock_code, *args, **kwargs):
        arg_sig = "_".join(str(a) for a in args)
        if kwargs:
            arg_sig += "_" + "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        cache_path = _CACHE_DIR / (
            f"dart_{fn.__name__}_{stock_code}_{arg_sig}_{date.today().isoformat()}.json"
        )
        if cache_path.exists():
            age_h = (time.time() - cache_path.stat().st_mtime) / 3600
            if age_h < _HOT_CACHE_TTL_HOURS:
                try:
                    return json.loads(cache_path.read_text("utf-8"))
                except Exception:
                    pass
        result = fn(self, stock_code, *args, **kwargs)
        if result:  # only cache non-empty (don't pin transient failures)
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(result, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return result

    return wrapper


def _normalize_name(name: str) -> str:
    """Lowercase + strip common Korean corporate suffixes / whitespace
    so '삼성전자', '삼성전자(주)', '주식회사 삼성전자' all collapse to
    the same key. Used to make name lookup forgiving of how the user
    types it vs how DART stores it."""
    n = (name or "").strip()
    for noise in ("(주)", "㈜", "(유)", "주식회사 ", "주식회사", "유한회사 ", "유한회사"):
        n = n.replace(noise, "")
    return n.strip().lower()


# ─────────────────────────────────────────────────────────────────────
# D1 Phase 2 (2026-05-19): DART 계정과목 정규화 + 재무비율 계산기
# StandardView (StanLee5767/standardview) 의 CODE_MAP / NAME_MAP /
# calc_ratios 패턴 차용 (라이선스 동의 받음 2026-05-19).
# 목적: yfinance 의 KR 종목 totalRevenue / 영업이익 등 단위 mismatch
# (SMIC 50x / 현대차증권 47x) 또는 financialCurrency=USD glitch 시
# DART 직접 정규화 데이터로 자동 override.
# ─────────────────────────────────────────────────────────────────────

# DART API 의 account_id → 통일 용어 매핑. K-IFRS / dart_ / us-gaap
# 표준 prefix 모두 cover. 같은 항목이 회사마다 다른 account_id 로
# 보고되므로 multi-source 매핑.
_DART_CODE_MAP: dict[str, str] = {
    "ifrs-full_Revenue": "매출",
    "ifrs-full_RevenueFromContractsWithCustomers": "매출",
    "dart_Revenue": "매출",
    "ifrs-full_CostOfSales": "매출원가",
    "dart_CostOfSales": "매출원가",
    "ifrs-full_GrossProfit": "매출총이익",
    "ifrs-full_SellingGeneralAndAdministrativeExpense": "판관비",
    "dart_SellingGeneralAdministrativeExpenses": "판관비",
    "ifrs-full_AdministrativeExpense": "판관비",
    "dart_OperatingIncomeLoss": "영업이익",
    "ifrs-full_ProfitLossFromOperatingActivities": "영업이익",
    "ifrs-full_FinanceIncome": "금융수익",
    "ifrs-full_FinanceCosts": "금융비용",
    "ifrs-full_ProfitLossBeforeTax": "세전이익",
    "ifrs-full_IncomeTaxExpenseContinuingOperations": "법인세비용",
    "ifrs-full_ProfitLoss": "당기순이익",
    "dart_ProfitLoss": "당기순이익",
    "us-gaap_NetIncomeLoss": "당기순이익",
    "ifrs-full_BasicEarningsLossPerShare": "EPS",
    "ifrs-full_BasicEarningsPerShare": "EPS",
    "dart_BasicEarningsLossPerShare": "EPS",
    "ifrs-full_CurrentAssets": "유동자산",
    "ifrs-full_NoncurrentAssets": "비유동자산",
    "ifrs-full_Assets": "자산총계",
    "us-gaap_Assets": "자산총계",
    "ifrs-full_CurrentLiabilities": "유동부채",
    "ifrs-full_NoncurrentLiabilities": "비유동부채",
    "ifrs-full_Liabilities": "부채총계",
    "ifrs-full_RetainedEarnings": "이익잉여금",
    "ifrs-full_Equity": "자본총계",
    "us-gaap_StockholdersEquity": "자본총계",
    "ifrs-full_EquityAttributableToOwnersOfParent": "자본총계",
}

# account_id 가 비표준이거나 nullable 시 account_nm (한글 텍스트) 매칭
# fallback. 회사마다 분기 / 사업보고서 양식 차이로 다른 alias 사용.
_DART_NAME_MAP: dict[str, str] = {
    "매출액": "매출", "수익(매출액)": "매출", "영업수익": "매출",
    "매출": "매출", "도급공사수익": "매출", "분양수익": "매출",
    "이자수익": "매출",
    "매출원가": "매출원가", "도급공사원가": "매출원가",
    "매출총이익": "매출총이익", "매출총이익(손실)": "매출총이익",
    "매출총손익": "매출총이익",
    "판매비와관리비": "판관비", "판매비및관리비": "판관비",
    "판매비와 관리비": "판관비", "영업비용": "판관비",
    "영업이익": "영업이익", "영업이익(손실)": "영업이익",
    "영업손익": "영업이익",
    "금융수익": "금융수익", "이자및배당금수익": "금융수익",
    "금융비용": "금융비용", "이자비용": "금융비용", "금융원가": "금융비용",
    "법인세비용차감전순이익": "세전이익",
    "법인세비용차감전순이익(손실)": "세전이익",
    "법인세차감전순이익": "세전이익",
    "법인세비용차감전순손익": "세전이익",
    "법인세비용": "법인세비용", "법인세수익": "법인세비용",
    "당기순이익": "당기순이익", "당기순이익(손실)": "당기순이익",
    "분기순이익": "당기순이익", "반기순이익": "당기순이익",
    "당기순손익": "당기순이익",
    "기본주당순이익": "EPS", "기본주당이익": "EPS",
    "기본주당순이익(손실)": "EPS", "보통주기본주당이익(손실)": "EPS",
    "유동자산": "유동자산",
    "비유동자산": "비유동자산", "고정자산": "비유동자산",
    "자산총계": "자산총계", "자산 합계": "자산총계",
    "유동부채": "유동부채",
    "비유동부채": "비유동부채", "고정부채": "비유동부채",
    "부채총계": "부채총계", "부채 합계": "부채총계",
    "이익잉여금": "이익잉여금", "이익잉여금(결손금)": "이익잉여금",
    "미처분이익잉여금": "이익잉여금",
    "자본총계": "자본총계", "자본 합계": "자본총계",
}

# EPS 만 float (원 단위 소수점 가능), 나머지 absolute KRW int.
_EPS_KEYS = {"EPS"}


def _parse_dart_amount(raw: str, as_float: bool = False):
    """DART API 의 thstrm_amount (당기 금액) 파싱.

    DART 는 amount 를 콤마 포함 string 으로 반환 ('1,234,567,890').
    음수는 '(123,456)' 또는 '-123,456' 형식. 빈 string 또는 '-' 일 때
    None 반환. EPS 는 소수점 가능 (₩1,234.56) 이라 float 옵션.
    """
    s = (raw or "").strip()
    if not s or s in ("-", "—", "N/A"):
        return None
    # 괄호 음수 처리
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace(",", "").strip()
    if not s:
        return None
    try:
        if as_float:
            v = float(s)
        else:
            # 소수점 있는 case (예: EPS '1234.56') 도 안전 처리
            v = int(float(s))
    except (ValueError, TypeError):
        return None
    return -v if negative else v


def _extract_dart_financials(items: list, amount_field: str = "thstrm_amount") -> dict:
    """DART fnlttSinglAcntAll.json 응답의 list[item] → 정규화 dict.

    Item 각각 account_id (K-IFRS / dart_ / us-gaap 표준) 또는 account_nm
    (한글 텍스트) 보유. CODE_MAP 우선 매칭, 미매칭 시 NAME_MAP fallback.
    같은 canonical 키에 여러 row 매칭 시 absolute value 큰 것 선택
    (parent vs consolidated / 본사 vs 종속 등 표준 row 우선).

    amount_field(2026-08-19, VM 실측 확인 — 삼성전자 25.반기/25.3분기 원본
    응답): 분기/반기보고서(reprt_code 11012/11013/11014)의 손익계산서
    (sj_div=IS) 항목은 **thstrm_amount 자체가 이미 '당기 3개월'(단일분기)
    값**이고, thstrm_add_amount 가 '당기누적'(연초~해당분기말 누적)이다 —
    반대로 짐작하기 쉽지만(라벨이 '반기'/'3분기'라 누적처럼 보임) 실측
    결과 정반대. 사업연도(11011)엔 thstrm_add_amount 자체가 없음(전체가
    이미 연간 누적이라 구분 불요). amount_field='thstrm_add_amount' 로
    호출하면 그 누적치를 뽑을 수 있음 — 4분기(연간-9개월누적) 단독 산출
    에만 필요(bot/dart_quarterly.py)."""
    if not items:
        return {}
    res: dict = {}
    for item in items:
        acct_id = (item.get("account_id") or "").strip()
        acct_nm = (item.get("account_nm") or "").strip()
        canonical = _DART_CODE_MAP.get(acct_id) or _DART_NAME_MAP.get(acct_nm)
        if not canonical:
            continue
        v = _parse_dart_amount(
            item.get(amount_field, ""),
            as_float=(canonical in _EPS_KEYS),
        )
        if v is None:
            continue
        # absolute value 큰 row 선택 (consolidated 우선)
        if canonical not in res or abs(v) > abs(res[canonical]):
            res[canonical] = v
    return res


def calc_kr_financial_ratios(financials: dict) -> dict:
    """DART 정규화 dict → 10 재무비율 (영업이익률 / 순이익률 / ROE /
    ROA / 부채비율 / 유동비율 / 이자보상배율 / 매출총이익률 / 이익잉여금
    비율 / ROIC). 분모 0 / None 시 해당 row 만 None — graceful.

    StandardView (StanLee5767/standardview) 의 calc_ratios 차용 (license
    동의 2026-05-19). 단위는 % (백분율).
    """
    rev = financials.get("매출") or 0
    op = financials.get("영업이익")
    net = financials.get("당기순이익")
    asset = financials.get("자산총계") or 0
    eq = financials.get("자본총계") or 0
    dbt = financials.get("부채총계") or 0
    ca = financials.get("유동자산") or 0
    cl = financials.get("유동부채") or 0
    fc = financials.get("금융비용") or 0
    gp = financials.get("매출총이익") or 0
    er = financials.get("이익잉여금") or 0
    pretax = financials.get("세전이익")
    tax = financials.get("법인세비용")
    # ROIC(투하자본이익률) 근사치(2026-08-16) — DART 표준 요약 API 는
    # 이자부채/비이자부채를 구분해 주지 않아, 투하자본은 통상적 근사식
    # 자산총계-유동부채(≈자기자본+비유동부채) 사용. 실효세율은 DART 실측값
    # (법인세비용/세전이익)을 그대로 쓰고, 세전이익이 없거나 실효세율이
    # 0~1 범위를 벗어나면(적자 등으로 왜곡) 세후조정 없이 영업이익 그대로
    # 사용(고정 법인세율 가정 = 창작이라 지양, 데이터 없으면 보정 생략).
    invested_capital = (asset - cl) if asset else None
    nopat = None
    if op is not None:
        eff_tax_rate = (tax / pretax) if (tax is not None and pretax) else None
        if eff_tax_rate is not None and 0 <= eff_tax_rate <= 1:
            nopat = op * (1 - eff_tax_rate)
        else:
            nopat = op
    return {
        "영업이익률": (op / rev * 100) if (op is not None and rev) else None,
        "순이익률": (net / rev * 100) if (net is not None and rev) else None,
        "ROE": (net / eq * 100) if (net is not None and eq) else None,
        "ROA": (net / asset * 100) if (net is not None and asset) else None,
        "부채비율": (dbt / eq * 100) if eq else None,
        "유동비율": (ca / cl * 100) if cl else None,
        "이자보상배율": (op / fc) if (op is not None and fc) else None,
        "매출총이익률": (gp / rev * 100) if (gp and rev) else None,
        "이익잉여금비율": (er / asset * 100) if (er and asset) else None,
        "ROIC": (nopat / invested_capital * 100)
                if (nopat is not None and invested_capital) else None,
    }


class DartClient:
    """Single-key DART client. Cheap to instantiate; reuse across calls
    to amortize the corp_code mapping load."""

    def __init__(self, api_key: Optional[str] = None):
        # Read key lazily so a missing env var doesn't crash module import.
        self.api_key = (api_key or os.getenv("DART_API_KEY") or "").strip()
        self._corp_code_map: dict[str, str] | None = None  # stock_code → corp_code
        # normalized name → list of {name, stock_code, corp_code} entries.
        # One normalized key can map to multiple companies when a search
        # like "현대" matches several entries — caller decides what to do.
        self._name_map: dict[str, list[dict]] | None = None
        # Reverse map: stock_code → corp_name. Built lazily on first
        # stock_code_to_name() call from _name_map; not persisted to
        # disk because it's cheap to rebuild from the v2 cache.
        self._stock_to_name: dict[str, str] | None = None

    # ── corp_code mapping ───────────────────────────────────────────────
    def _load_corp_code_map(self) -> dict[str, str]:
        """Stock code (6-digit) → corp_code (8-digit). DART exposes the
        mapping as a single zipped XML; we cache it locally for 30 days
        so we don't re-download on every analysis. The cache also carries
        the reverse name→entries map so `find_by_name()` doesn't have to
        re-parse the XML."""
        if self._corp_code_map is not None and self._name_map is not None:
            return self._corp_code_map

        # Disk cache check (v2 format: dict with 'stock_to_corp' and
        # 'name_to_entries' keys).
        if _CORPCODE_CACHE.exists():
            try:
                age_days = (time.time() - _CORPCODE_CACHE.stat().st_mtime) / 86400
                if age_days < _CORPCODE_TTL_DAYS:
                    data = json.loads(_CORPCODE_CACHE.read_text())
                    self._corp_code_map = data.get("stock_to_corp", {})
                    self._name_map = data.get("name_to_entries", {})
                    return self._corp_code_map
            except Exception as exc:
                log.warning("dart: corp_code cache read failed: %s", exc)

        # Fetch fresh.
        if not self.api_key:
            log.warning("dart: DART_API_KEY missing — corp_code map unavailable")
            self._corp_code_map = {}
            self._name_map = {}
            return self._corp_code_map

        try:
            resp = requests.get(
                f"{_DART_BASE}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
            root = ET.fromstring(xml_bytes)
        except Exception as exc:
            log.warning("dart: corp_code download failed: %s", exc)
            self._corp_code_map = {}
            self._name_map = {}
            return self._corp_code_map

        stock_to_corp: dict[str, str] = {}
        name_to_entries: dict[str, list[dict]] = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            # Only KRX-listed entries have a 6-digit stock_code; skip the
            # rest (DART also tracks unlisted entities).
            if not (stock_code and len(stock_code) == 6 and corp_code):
                continue
            stock_to_corp[stock_code] = corp_code
            norm = _normalize_name(corp_name)
            if norm:
                name_to_entries.setdefault(norm, []).append({
                    "name": corp_name,
                    "stock_code": stock_code,
                    "corp_code": corp_code,
                })
        log.info(
            "dart: loaded %d corp_code / %d unique normalized names",
            len(stock_to_corp), len(name_to_entries),
        )

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CORPCODE_CACHE.write_text(json.dumps(
                {"stock_to_corp": stock_to_corp, "name_to_entries": name_to_entries},
                ensure_ascii=False,
            ))
        except Exception as exc:
            log.warning("dart: corp_code cache write failed: %s", exc)

        self._corp_code_map = stock_to_corp
        self._name_map = name_to_entries
        return stock_to_corp

    def find_by_name(self, query: str) -> list[dict]:
        """Resolve a Korean / English company name to listed-entity entries.

        Returns a list of {name, stock_code, corp_code} dicts ordered by
        match specificity:
          - Exact normalized match (e.g. '삼성전자' → 005930)
          - Prefix or substring match if no exact hit
          - Empty list when nothing matches at all

        Capped at 20 to keep ambiguous queries (e.g. '현대') tractable
        for the caller's UX."""
        norm = _normalize_name(query)
        if not norm:
            return []
        self._load_corp_code_map()
        if not self._name_map:
            return []

        # Exact-normalized match wins.
        exact = self._name_map.get(norm) or []
        if exact:
            return exact[:20]

        # Fallback: prefix match first (more specific), then substring.
        prefix: list[dict] = []
        contains: list[dict] = []
        for n, entries in self._name_map.items():
            if n.startswith(norm):
                prefix.extend(entries)
            elif norm in n:
                contains.extend(entries)
        return (prefix + contains)[:20]

    def stock_code_to_corp_code(self, stock_code: str) -> Optional[str]:
        """Resolve 6-digit KRX stock code → 8-digit DART corp_code.
        Strips a trailing `.KS`/`.KQ` for caller convenience."""
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        return self._load_corp_code_map().get(code)

    def stock_code_to_name(self, stock_code: str) -> Optional[str]:
        """Reverse lookup: 6-digit stock code → Korean corp name.
        Used by the analyzer to render '삼성전자 / 005930.KS' style
        titles. Builds a stock→name map lazily on first call from the
        cached _name_map so we don't pay the O(n) cost more than once
        per bot lifetime."""
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        self._load_corp_code_map()
        if not self._name_map:
            return None
        if self._stock_to_name is None:
            self._stock_to_name = {
                e["stock_code"]: e["name"]
                for entries in self._name_map.values()
                for e in entries
            }
        return self._stock_to_name.get(code)

    def news_search_name(self, stock_code: str) -> Optional[str]:
        """Best Korean news-search term for a KR ticker.

        Prefers the Korean corp_name from company.json ('네이버(주)') over
        the corp_code.xml name, which is sometimes the English DART
        registration ('NAVER' for 035420) that Korean news search returns
        0 hits for. Strips the corporate-form suffix so '네이버(주)' →
        '네이버'. Falls back to the corp_code.xml name when company.json
        is unavailable. Surfaced 2026-06-08 (NAVER 0-news bug)."""
        from bot.market import kr_news_query_name, has_hangul
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        # company.json corp_name is reliably Korean for almost every filer.
        try:
            ci = self.get_company_info(code) or {}
            korean = kr_news_query_name(ci.get("corp_name"))
            if korean and has_hangul(korean):
                return korean
        except Exception:
            pass
        # Fallback: corp_code.xml name (strip suffix; may be English).
        bare = kr_news_query_name(self.stock_code_to_name(code))
        return bare or None

    # ── /api/company.json — corp info incl. KSIC industry code ─────────
    def get_company_info(self, stock_code: str) -> Optional[dict]:
        """Return DART company info dict for the listed entity. The most
        useful field is `induty_code` — 5-digit KSIC (한국표준산업분류)
        code identifying the company's primary line of business. This is
        the authoritative industry classification (vs yfinance's English
        industry tag which is often wrong for KR stocks — 010140.KS
        삼성중공업 surfaced 2026-05-23 as 'Aerospace & Defense' when
        the actual KSIC is C31111 강선건조업 = Shipbuilding).

        Disk-cached 30 days at ~/.tradingagents/cache/dart_company_{corp_code}.json
        because companies rarely change industry classification.

        Returns None on key missing / network failure / corp_code
        unresolved / DART error response. Never raises — callers should
        fall back to whatever they were using before (yfinance industry)."""
        if not self.api_key:
            return None
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return None
        cache_path = _CACHE_DIR / f"dart_company_{corp_code}.json"
        if cache_path.exists():
            age_days = (time.time() - cache_path.stat().st_mtime) / 86400
            if age_days < 30:
                try:
                    return json.loads(cache_path.read_text("utf-8"))
                except Exception:
                    pass
        try:
            resp = requests.get(
                f"{_DART_BASE}/company.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: company.json for %s failed: %s", stock_code, exc)
            return None
        if payload.get("status") not in ("000",):
            return None
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            )
        except Exception:
            pass
        return payload

    # ── /api/list.json — recent disclosures ─────────────────────────────
    @_disk_cache_daily
    def get_recent_disclosures(
        self, stock_code: str, days_back: int = 30, limit: int = 20
    ) -> list[dict]:
        """Return up to `limit` most recent disclosure entries within the
        last `days_back` calendar days. Each entry has 'date', 'title',
        'reporter', 'url'. Empty list when key missing / network fails /
        corp_code unresolved — never raises."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []

        end = date.today()
        bgn = end - timedelta(days=days_back)
        # 페이지네이션 — 한 페이지 100건. limit 이 100 초과면(차트 풀히스토리) 다음
        # 페이지를 이어 받음. 안전 캡 6페이지(600건). limit≤100 이면 1페이지로 종료
        # (build_instrument_context 등 기존 호출 동작 불변).
        out: list[dict] = []
        page_no = 1
        while len(out) < limit and page_no <= 6:
            try:
                resp = requests.get(
                    f"{_DART_BASE}/list.json",
                    params={
                        "crtfc_key": self.api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn.strftime("%Y%m%d"),
                        "end_de": end.strftime("%Y%m%d"),
                        "page_no": page_no,
                        "page_count": 100,
                    },
                    timeout=_HTTP_TIMEOUT,
                )
                payload = resp.json()
            except Exception as exc:
                log.warning("dart: list.json fetch for %s failed: %s", stock_code, exc)
                break
            # DART error envelope: status "000" = success, anything else = no data.
            if payload.get("status") not in ("000",):
                break
            for r in payload.get("list") or []:
                out.append({
                    "date": r.get("rcept_dt") or "",
                    "title": (r.get("report_nm") or "").strip(),
                    "reporter": (r.get("flr_nm") or "").strip(),
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no', '')}",
                })
            try:
                if page_no >= int(payload.get("total_page") or 1):
                    break
            except (TypeError, ValueError):
                break
            page_no += 1
        return out[:limit]

    # 사업연도(year)·reprt_code → (report_nm 키워드, 검색 시작일, 검색 종료일).
    # next_earnings_window()(1052-1079행)의 법정 제출기한(분기 45일/연간 90일)
    # 을 그대로 근거로 삼되, 조기제출·소폭지연을 흡수하도록 앞뒤 여유를 둠.
    # 11013(1분기)·11014(3분기)는 report_nm 이 둘 다 "분기보고서"라 서로
    # 겹치지 않는 이 날짜창으로만 구분한다(4~5월 vs 10~11월, 안전하게 분리).
    def _periodic_report_window(self, year: int, reprt_code: str) -> tuple[str, str, str]:
        y = str(year)
        y1 = str(year + 1)
        return {
            "11013": ("분기보고서", f"{y}0401", f"{y}0531"),
            "11012": ("반기보고서", f"{y}0701", f"{y}0831"),
            "11014": ("분기보고서", f"{y}1001", f"{y}1130"),
            "11011": ("사업보고서", f"{y1}0101", f"{y1}0430"),
        }[reprt_code]

    def find_periodic_report(self, stock_code: str, year: int,
                             reprt_code: str) -> Optional[dict]:
        """분기 실적분석 대시보드(2026-08-16) — 해당 (year, reprt_code) 정기
        보고서의 rcept_no 확보(document.xml 원문 요청에 필요). list.json 을
        pblntf_ty='A'(정기공시)로 좁혀 report_nm 키워드+법정 제출기한 기반
        날짜창으로 매칭. 여러 건 매치 시(정정보고서 등) 가장 최근 접수건.
        DART 키 없음/corp_code 미상/매치 없음 시 None(graceful)."""
        if not self.api_key:
            return None
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return None
        keyword, bgn, end = self._periodic_report_window(year, reprt_code)
        try:
            resp = requests.get(
                f"{_DART_BASE}/list.json",
                params={
                    "crtfc_key": self.api_key, "corp_code": corp_code,
                    "bgn_de": bgn, "end_de": end, "pblntf_ty": "A",
                    "page_count": 20,
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("find_periodic_report: list.json fetch for %s failed: %s",
                        stock_code, exc)
            return None
        if payload.get("status") != "000":
            return None
        matches = [r for r in payload.get("list") or []
                  if keyword in (r.get("report_nm") or "")]
        if not matches:
            return None
        best = max(matches, key=lambda r: r.get("rcept_dt") or "")
        return {"rcept_no": best.get("rcept_no"), "report_nm": best.get("report_nm"),
               "rcept_dt": best.get("rcept_dt")}

    # ── /api/elestock.json — insider / major shareholder holdings ──────
    @_disk_cache_daily
    def get_insider_holdings(self, stock_code: str) -> list[dict]:
        """Return rows of officer / major shareholder current holdings.
        Each row: 'name', 'role', 'shares', 'pct', 'changed_on'. Empty
        list on any failure mode."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []

        try:
            resp = requests.get(
                f"{_DART_BASE}/elestock.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: elestock for %s failed: %s", stock_code, exc)
            return []

        if payload.get("status") not in ("000",):
            return []
        rows = payload.get("list") or []
        out: list[dict] = []
        for r in rows:
            # DART returns holding rows with rolling history; we only want
            # the latest per-person snapshot. Caller can dedupe later if
            # needed — for now expose everything and let the prompt
            # builder cap the list.
            #
            # Field names per DART /api/elestock.json spec (verified
            # against 한솔케미칼 2026-05-17 case where all 5 entries
            # showed 0.00% with the old 'stkqy' / 'stkrt' guesses —
            # those field names don't exist on this endpoint):
            #   sp_stock_lmp_cnt   — 특정증권등 소유수
            #   sp_stock_lmp_rate  — 특정증권등 소유비율 (%, e.g. "1.65")
            # Fallback to the old names just in case DART exposes them
            # for some response shapes; defensive against future schema
            # changes.
            shares_raw = str(
                r.get("sp_stock_lmp_cnt") or r.get("stkqy") or "0"
            ).replace(",", "")
            try:
                shares = int(shares_raw)
            except ValueError:
                shares = 0
            pct_raw = str(
                r.get("sp_stock_lmp_rate") or r.get("stkrt") or "0"
            ).replace(",", "")
            try:
                pct = float(pct_raw)
            except ValueError:
                pct = 0.0
            # ⚠️ pct sanity (Samsung 005930 2026-05-31 review): DART
            # elestock 의 sp_stock_lmp_rate 는 가끔 100.0 (해당 임원 본인
            # 보유분 중 특정증권 비율 = 항상 100%) 으로 와, 이를 "회사 전체
            # 지분율" 로 오인하면 "이종민 부사장 100% 지분" 같은 금융정보
            # 파괴 발생. 시총 2,000조 회사의 회사 지분 100% 를 개인이 가질
            # 수 없음. ≥50% 는 회사 지분율이 아닌 본인 보유분 비율로 판단,
            # pct 를 None 처리 (표시단에서 '회사 지분율 N/A' 로 렌더).
            pct_is_company = pct < 50.0
            out.append({
                "name": (r.get("repror") or "").strip(),
                "role": (r.get("isu_exctv_ofcps") or "").strip(),
                "shares": shares,
                "pct": pct if pct_is_company else None,
                "pct_suspect": not pct_is_company,
                "changed_on": (r.get("rcept_dt") or "").strip(),
            })
        return out

    # ── 최대주주 현황 (hyslrSttus.json) ──────────────────────────────

    def _fetch_hyslr(self, corp_code: str, bsns_year: str) -> Optional[list]:
        """Raw fetch for hyslrSttus. Returns row list or None."""
        try:
            resp = requests.get(
                f"{_DART_BASE}/hyslrSttus.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": "11011",
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: hyslrSttus %s/%s failed: %s", corp_code, bsns_year, exc)
            return None
        status = payload.get("status")
        if status != "000":
            log.info("dart: hyslrSttus %s year=%s status=%s msg=%s",
                     corp_code, bsns_year, status, payload.get("message", ""))
            return None
        rows = payload.get("list") or []
        if rows:
            log.info("dart: hyslrSttus %s year=%s rows=%d keys=%s",
                     corp_code, bsns_year, len(rows), list(rows[0].keys())[:15])
        return rows if rows else None

    _HYSLR_SHARES_CANDIDATES = [
        "trmend_posesn_stock_co", "bsis_posesn_stock_co",
        "posesn_stock_co", "trmend_posesn_stkqy", "bsis_posesn_stkqy",
        "posesn_stkqy", "stock_co", "stkqy",
    ]
    _HYSLR_PCT_CANDIDATES = [
        "trmend_posesn_stock_qota_rt", "bsis_posesn_stock_qota_rt",
        "posesn_stock_qota_rt", "trmend_posesn_stkqy_rate",
        "bsis_posesn_stkqy_rate", "posesn_stkqy_rate", "stock_qota_rt",
    ]
    _HYSLR_SKIP_KEYS = frozenset({
        "rcept_no", "corp_cls", "corp_code", "corp_name", "nm",
        "relate", "rm", "bsns_year", "reprt_code", "stock_knd",
        "se", "change_on", "change_cause",
    })

    @staticmethod
    def _hyslr_extract_int(row: dict, candidates: list) -> int:
        for key in candidates:
            v = row.get(key)
            if v is None:
                continue
            vs = str(v).replace(",", "").strip()
            if not vs or vs == "-":
                continue
            try:
                return int(vs)
            except ValueError:
                try:
                    return int(float(vs))
                except (ValueError, OverflowError):
                    continue
        return 0

    @staticmethod
    def _hyslr_extract_float(row: dict, candidates: list) -> float:
        for key in candidates:
            v = row.get(key)
            if v is None:
                continue
            vs = str(v).replace(",", "").strip()
            if not vs or vs == "-":
                continue
            try:
                return float(vs)
            except (ValueError, OverflowError):
                continue
        return 0.0

    def _parse_hyslr_rows(self, rows: list) -> list[dict]:
        if not rows:
            return []
        try:
            log.info("dart: hyslrSttus FULL first row: %s",
                     json.dumps(rows[0], ensure_ascii=False)[:600])
        except Exception:
            pass

        first_keys = set(rows[0].keys())
        extra_shares: list[str] = []
        extra_pct: list[str] = []
        for k in sorted(first_keys - self._HYSLR_SKIP_KEYS):
            if k in self._HYSLR_SHARES_CANDIDATES or k in self._HYSLR_PCT_CANDIDATES:
                continue
            kl = k.lower()
            if any(p in kl for p in ("_rt", "_rate", "qota", "pct")):
                extra_pct.append(k)
            elif any(p in kl for p in ("_co", "_qy", "stock", "stkqy", "cnt")):
                extra_shares.append(k)

        all_shares = list(self._HYSLR_SHARES_CANDIDATES) + extra_shares
        all_pct = list(self._HYSLR_PCT_CANDIDATES) + extra_pct
        log.info("dart: hyslrSttus candidates: shares=%s pct=%s",
                 all_shares[:10], all_pct[:10])

        out: list[dict] = []
        for r in rows:
            nm = (r.get("nm") or r.get("inv_prm") or r.get("aflte_nm")
                  or r.get("cmpny_nm") or "").strip()
            if not nm:
                continue
            rel = (r.get("relate") or r.get("rel_btr_at")
                   or r.get("relt") or r.get("rel_corp_nm") or "").strip()
            shares = self._hyslr_extract_int(r, all_shares)
            pct = self._hyslr_extract_float(r, all_pct)
            note = (r.get("rm") or r.get("bsn_sumry") or "").strip()
            out.append({
                "name": nm,
                "relation": rel,
                "shares": shares,
                "pct": pct,
                "note": note,
            })
        return out

    def get_major_shareholders(self, stock_code: str) -> list[dict]:
        """DART 최대주주 현황 — major shareholder list from annual report.
        Tries year-1 then year-2 fallback. Each row: {name, relation, shares, pct, note}."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []
        ck = f"majsh3_{corp_code}"
        cached = self._disk_get(ck)
        if cached is not None:
            return cached
        y = date.today().year
        rows = self._fetch_hyslr(corp_code, str(y - 1))
        if not rows:
            rows = self._fetch_hyslr(corp_code, str(y - 2))
        if not rows:
            return []
        out = self._parse_hyslr_rows(rows)
        if out:
            self._disk_set(ck, out)
        return out

    # ── 타법인 출자현황 (otrCprInvstmntSttus.json) — 계열회사/자회사 ──

    def get_affiliate_investments(self, stock_code: str) -> list[dict]:
        """DART 타법인 출자현황 — investment in other corporations (계열회사 proxy).
        Tries year-1 then year-2 fallback.
        Each row: {name, purpose, shares, pct, book_value, total_assets, note}."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []
        ck = f"affinv_{corp_code}"
        cached = self._disk_get(ck)
        if cached is not None:
            return cached
        y = date.today().year
        rows = self._fetch_otr_cpr(corp_code, str(y - 1))
        if not rows:
            rows = self._fetch_otr_cpr(corp_code, str(y - 2))
        if not rows:
            return []
        out = self._parse_otr_cpr_rows(rows)
        if out:
            self._disk_set(ck, out)
        return out

    def _fetch_otr_cpr(self, corp_code: str, bsns_year: str) -> Optional[list]:
        """Raw fetch for otrCprInvstmntSttus."""
        try:
            resp = requests.get(
                f"{_DART_BASE}/otrCprInvstmntSttus.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": "11011",
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: otrCprInvstmntSttus %s/%s failed: %s",
                        corp_code, bsns_year, exc)
            return None
        status = payload.get("status")
        if status != "000":
            log.info("dart: otrCprInvstmntSttus %s year=%s status=%s msg=%s",
                     corp_code, bsns_year, status, payload.get("message", ""))
            return None
        rows = payload.get("list") or []
        if rows:
            log.info("dart: otrCprInvstmntSttus %s year=%s rows=%d keys=%s",
                     corp_code, bsns_year, len(rows), list(rows[0].keys())[:15])
        return rows if rows else None

    def _parse_otr_cpr_rows(self, rows: list) -> list[dict]:
        out: list[dict] = []
        for r in rows:
            nm = (r.get("inv_prm") or r.get("cmpny_nm") or r.get("nm")
                  or r.get("aflte_nm") or "").strip()
            if not nm or nm == "-":
                continue
            purpose = (r.get("invstmnt_purps") or r.get("inv_purps")
                       or r.get("purps") or "").strip()
            shares_raw = str(
                r.get("trmend_blce_qy") or r.get("bsis_blce_qy")
                or r.get("posesn_stkqy") or "0"
            ).replace(",", "").strip()
            try:
                shares = int(shares_raw) if shares_raw and shares_raw != "-" else 0
            except ValueError:
                shares = 0
            pct_raw = str(
                r.get("trmend_blce_qota_rt") or r.get("bsis_blce_qota_rt")
                or r.get("qota_rt") or "0"
            ).replace(",", "").strip()
            try:
                pct = float(pct_raw) if pct_raw and pct_raw != "-" else 0.0
            except ValueError:
                pct = 0.0
            bv_raw = str(
                r.get("trmend_blce_acntbk_amount") or r.get("acntbk_amount")
                or "0"
            ).replace(",", "").strip()
            try:
                book_value = int(bv_raw) if bv_raw and bv_raw != "-" else 0
            except ValueError:
                book_value = 0
            ta_raw = str(
                r.get("recent_bsns_year_fnnr_sttus_tot_assets")
                or r.get("tot_assets") or "0"
            ).replace(",", "").strip()
            try:
                total_assets = int(ta_raw) if ta_raw and ta_raw != "-" else 0
            except ValueError:
                total_assets = 0
            note = (r.get("rm") or "").strip()
            out.append({
                "name": nm,
                "purpose": purpose,
                "shares": shares,
                "pct": pct,
                "book_value": book_value,
                "total_assets": total_assets,
                "note": note,
            })
        return out

    def _disk_get(self, key: str):
        p = Path.home() / ".tradingagents" / "cache" / "dart" / f"{key}.json"
        if not p.exists():
            return None
        try:
            age_h = (time.time() - p.stat().st_mtime) / 3600
            if age_h >= 168:  # 7 day cache (yearly report data)
                return None
            return json.loads(p.read_text())
        except Exception:
            return None

    def _disk_set(self, key: str, data):
        try:
            d = Path.home() / ".tradingagents" / "cache" / "dart"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    # ── earnings window estimate ────────────────────────────────────────
    # ── D1 Phase 2: 정규화 재무제표 + 비율 ───────────────────────────

    def get_normalized_financials(
        self,
        ticker: str,
        year: Optional[int] = None,
        fs_div: str = "CFS",
        reprt_code: str = "11011",
    ) -> Optional[dict]:
        """yfinance 의 KR 종목 재무 corruption (단위 mismatch / financial
        Currency=USD glitch / TTM vs FY divergence) 발생 시 DART 의
        K-IFRS 정기보고서에서 직접 추출한 KRW 단위 재무 데이터로
        override. D1 Phase 2 (2026-05-19). reprt_code 확장(2026-08-16,
        분기 실적분석 대시보드) — 분기 재무는 **누적치**(11012=반기누적,
        11014=3분기누적)라 단일분기 산출은 호출부(bot/dart_quarterly.py)가
        인접 reprt_code 결과를 차분해야 함(이 함수는 원자 조회만 담당).

        Returns dict like:
            {
                "year": 2025,
                "reprt_code": "11011",  # 11011=사업보고서(연간) 11012=반기
                                         # 11013=1분기 11014=3분기(모두 누적)
                "fs_div": "CFS",  # 연결 (CFS) / 별도 (OFS)
                "financials": {
                    "매출": int (원), "영업이익": int, "당기순이익": int,
                    "자산총계": int, "부채총계": int, "자본총계": int,
                    "유동자산": int, "유동부채": int, "이익잉여금": int,
                    "EPS": float (원), ...
                },
                "ratios": {
                    "영업이익률": float (%), "순이익률": float (%),
                    "ROE": float (%), "ROA": float (%), "부채비율": float (%),
                    "유동비율": float (%), "이자보상배율": float, ...
                },
            }
        또는 None (DART 키 없음 / 종목 미상장 / 보고서 미발표 등).

        Args:
            ticker: yfinance ticker (005930.KS / 035720.KQ 등).
            year: 사업연도. None 시 직전 사업연도 자동 (date.today().year - 1).
            fs_div: 'CFS' = 연결재무제표 (default), 'OFS' = 별도재무제표.
            reprt_code: '11011'=사업보고서(연간, default, 하위호환) '11012'=
                반기보고서 '11013'=1분기보고서 '11014'=3분기보고서.
        """
        if not self.api_key:
            return None
        code = (ticker or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        try:
            corp_map = self._load_corp_code_map()
        except Exception as exc:
            log.warning("get_normalized_financials: corp_code map load failed: %s", exc)
            return None
        corp_code = corp_map.get(code)
        if not corp_code:
            return None

        target_year = year or (date.today().year - 1)

        # 7일 디스크 캐시 — reprt_code 확장(최대 4종×연도)으로 분기 시계열
        # 조립 시 콜 수가 늘어나 캐시 없이는 페이지 방문마다 DART 를 수 회
        # 두드리게 됨(get_major_shareholders 등과 동일 _disk_get/_disk_set
        # 관례, 786-833행 참조). 확정된(과거) 분기는 안 바뀌므로 7일 충분 —
        # "이번 분기가 아직 미확정"인 최신분기 캐시 단축은 호출부(probe)가
        # 별도 6~12h 캐시로 처리(원자 조회 자체는 길게 캐시해도 무해).
        # ⚠️ 캐시 키 v2(2026-08-19, code-review 발견): financials_cumulative
        # 필드를 이 커밋에서 추가했는데 키를 안 바꾸면, 오늘 이미 조회돼
        # 캐시된(이 필드 없는 구버전) 엔트리가 7일간 그대로 서빙돼 4분기
        # 파생이 계속 조용히 실패한다(nine_mo=None→break). qfin→qfin2 로
        # 버저닝해 구 캐시를 자연스럽게 우회(dart_corpcode_v2.json 등 기존
        # 관례와 동일).
        ck = f"qfin2_{corp_code}_{target_year}_{reprt_code}_{fs_div}"
        cached = self._disk_get(ck)
        if cached is not None:
            return cached

        # fnlttSinglAcntAll.json — 단일 회사 전체 재무제표 (전체 항목)
        url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(target_year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        try:
            resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning(
                "get_normalized_financials: fetch failed for %s (%d, %s, %s): %s",
                code, target_year, reprt_code, fs_div, exc,
            )
            return None

        if payload.get("status") != "000":
            # 013 = 조회된 데이터 없음, 020 = 사용 한도 초과, etc.
            log.info(
                "get_normalized_financials: DART status=%s for %s (%d, %s) — skipping",
                payload.get("status"), code, target_year, reprt_code,
            )
            return None

        items = payload.get("list") or []
        financials = _extract_dart_financials(items)
        if not financials:
            return None
        ratios = calc_kr_financial_ratios(financials)
        result = {
            "year": target_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
            "financials": financials,
            "ratios": ratios,
        }
        # 분기/반기보고서는 thstrm_add_amount(당기누적)도 함께 보존 — 4분기
        # 단독 실적(연간-9개월누적)을 산출하려면 3분기보고서(11014)의 이
        # 누적치가 필요하다(bot/dart_quarterly.py, 2026-08-19 VM 실측 확인).
        # 사업보고서(11011)는 add_amount 필드 자체가 없어 None.
        if reprt_code != "11011":
            cum = _extract_dart_financials(items, amount_field="thstrm_add_amount")
            if cum:
                result["financials_cumulative"] = cum
        self._disk_set(ck, result)
        return result

    def next_earnings_window(self, stock_code: str, today: date | None = None) -> Optional[tuple[date, date]]:
        """Infer the most-likely next KR earnings disclosure window.

        Korean listed companies must file quarterly reports within 45
        days of quarter end (분기보고서) and annual/Q4 reports within
        90 days of fiscal year end (사업보고서). Assuming a Dec fiscal
        year (true for ~95% of KOSPI), the windows are:
          - Q1 (Mar-end) → due by May 15
          - Q2 (Jun-end) → due by Aug 14
          - Q3 (Sep-end) → due by Nov 14
          - Q4 + annual (Dec-end) → due by Apr 1 next year

        Returns (window_start, window_end) of the NEXT upcoming window,
        or None if computation fails."""
        today = today or date.today()
        year = today.year
        # Build the four nominal due-by dates for this fiscal year.
        candidates = [
            (date(year, 5, 15), date(year, 4, 15)),    # Q1 window 4/15-5/15
            (date(year, 8, 14), date(year, 7, 14)),    # Q2 window 7/14-8/14
            (date(year, 11, 14), date(year, 10, 14)),  # Q3 window 10/14-11/14
            (date(year + 1, 4, 1), date(year + 1, 3, 1)),  # Q4/annual 3/1-4/1
        ]
        for due, window_start in candidates:
            if today <= due:
                return (window_start, due)
        # All windows for this calendar year passed — return next year's Q1.
        return (date(year + 1, 4, 15), date(year + 1, 5, 15))


# Process-wide cached instance so the corp_code map only loads once
# per bot lifetime. Reset by restarting the bot.
_singleton: DartClient | None = None


def get_dart() -> DartClient:
    """Return the shared DartClient. Call this from analysts / pre-fetch
    helpers instead of constructing a new client per analysis."""
    global _singleton
    if _singleton is None:
        _singleton = DartClient()
    return _singleton
