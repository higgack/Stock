"""Generic conditional stock screener.

Finviz-style quantitative filter: accepts arbitrary metric conditions,
screens against the full KR (KOSPI+KOSDAQ) universe using pykrx bulk
data + yfinance per-ticker refinement.

Usage (Telegram):
  /screen PER<15 PBR<1 배당수익률>3   — custom conditions
  /screen valueup                      — preset
  /screen list                         — available metrics + presets

Two-phase architecture (KR):
  Phase 1 — pykrx bulk (PER/PBR/DIV/시총/거래량/EPS/BPS) for ALL tickers
  Phase 2 — yfinance per-ticker (배당성향/부채비율/ROE/etc.) for survivors

Universal-by-default: KR ships first (pykrx bulk = instant), other markets
can extend with market-specific bulk sources.  Cost ₩0 (no LLM).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/.tradingagents/screen_cache"))
_ARCHIVE_DIR = Path(os.path.expanduser("~/.tradingagents/screen_archive"))
# tech(추세) 메트릭은 종목별 일봉 fetch 라 시총 상위 N개만 스캔(비용 bound,
# 일봉당 daily 캐시). Minervini 는 유동 대형주 대상이라 상위 스캔이 적합.
_TECH_SCAN_CAP = 300

# ── Metric definitions ─────────────────────────────────────────────


@dataclass
class MetricDef:
    key: str
    name: str
    aliases: tuple
    source: str        # 'pykrx' or 'yfinance'
    yf_field: str      # yfinance .info key (or pykrx column)
    unit: str = ""
    scale: float = 1.0
    desc: str = ""


METRICS: dict[str, MetricDef] = {}


def _reg(key, name, aliases, source, yf_field, unit="", scale=1.0, desc=""):
    m = MetricDef(key, name, aliases, source, yf_field, unit, scale, desc)
    METRICS[key] = m


# pykrx bulk (Phase 1 — instant for ALL tickers)
_reg("per", "PER", ("PER", "주가수익비율", "pe", "p/e"), "pykrx",
     "PER", "배", desc="주가수익비율")
_reg("pbr", "PBR", ("PBR", "주가순자산비율", "pb", "p/b"), "pykrx",
     "PBR", "배", desc="주가순자산비율")
_reg("eps", "EPS", ("EPS", "주당순이익"), "pykrx",
     "EPS", "원", desc="주당순이익")
_reg("bps", "BPS", ("BPS", "주당순자산"), "pykrx",
     "BPS", "원", desc="주당순자산")
_reg("div", "배당수익률", ("배당수익률", "DIV", "dividend_yield", "배당률",
                        "dividend", "yield"), "pykrx",
     "DIV", "%", desc="배당수익률")
_reg("mcap", "시총", ("시총", "시가총액", "market_cap", "marketcap"),
     "pykrx", "시가총액", "억원", desc="시가총액 (억원)")
_reg("volume", "거래량", ("거래량", "volume", "vol"), "pykrx",
     "거래량", "주", desc="거래량")
_reg("price", "주가", ("주가", "종가", "price", "close"), "pykrx",
     "종가", "원", desc="현재 종가")

# yfinance per-ticker (Phase 2 — slower, survivors only)
_reg("payout", "배당성향", ("배당성향", "payout", "payout_ratio", "배당지급률"),
     "yfinance", "payoutRatio", "%", 100.0, "배당성향 (순이익 대비 배당)")
_reg("debt", "부채비율", ("부채비율", "debt", "debt_ratio", "debt_to_equity",
                        "der"), "yfinance",
     "debtToEquity", "%", 1.0, "부채비율 (자본 대비 부채)")
_reg("roe", "ROE", ("ROE", "자기자본이익률", "return_on_equity"), "yfinance",
     "returnOnEquity", "%", 100.0, "자기자본이익률")
_reg("roa", "ROA", ("ROA", "총자산이익률", "return_on_assets"), "yfinance",
     "returnOnAssets", "%", 100.0, "총자산이익률")
_reg("psr", "PSR", ("PSR", "주가매출비율", "ps", "p/s"), "yfinance",
     "priceToSalesTrailing12Months", "배", desc="주가매출비율")
_reg("revenue_growth", "매출성장률",
     ("매출성장률", "revenue_growth", "매출증가율", "revgrowth"), "yfinance",
     "revenueGrowth", "%", 100.0, "매출 YoY 성장률")
_reg("op_margin", "영업이익률",
     ("영업이익률", "operating_margin", "opm", "영업마진"), "yfinance",
     "operatingMargins", "%", 100.0, "영업이익률")
_reg("net_margin", "순이익률",
     ("순이익률", "profit_margin", "npm", "net_margin", "순이익마진"), "yfinance",
     "profitMargins", "%", 100.0, "순이익률")
_reg("current_ratio", "유동비율",
     ("유동비율", "current_ratio", "cr"), "yfinance",
     "currentRatio", "배", desc="유동비율")
_reg("beta", "베타", ("베타", "beta"), "yfinance",
     "beta", "", desc="베타 (시장 대비 변동성)")
_reg("ev_ebitda", "EV/EBITDA",
     ("EV/EBITDA", "ev_ebitda", "evebitda"), "yfinance",
     "enterpriseToEbitda", "배", desc="EV/EBITDA")
_reg("fwd_per", "선행PER",
     ("선행PER", "fwd_per", "forward_pe", "forwardpe"), "yfinance",
     "forwardPE", "배", desc="선행 PER (Forward P/E)")
_reg("div_yield_yf", "배당수익률(YF)",
     ("배당수익률yf",), "yfinance",
     "dividendYield", "%", 100.0, "yfinance 배당수익률 (pykrx 와 다를 수 있음)")

# QoQ metrics (computed from yfinance quarterly statements)
_reg("rev_qoq", "매출QoQ", ("매출QoQ", "매출qoq", "revqoq", "revenue_qoq",
                            "매출전분기"), "qoq",
     "_qoq_revenue", "%", 1.0, "매출 QoQ 성장률 (전분기 대비)")
_reg("gp_qoq", "매출총이익QoQ", ("매출총이익QoQ", "매출총이익qoq", "gpqoq",
                               "grossprofit_qoq"), "qoq",
     "_qoq_gross_profit", "%", 1.0, "매출총이익 QoQ 성장률")
_reg("op_qoq", "영업이익QoQ", ("영업이익QoQ", "영업이익qoq", "opqoq",
                              "opincome_qoq"), "qoq",
     "_qoq_op_income", "%", 1.0, "영업이익 QoQ 성장률")
_reg("ebitda_qoq", "EBITDAQoQ", ("EBITDAQoQ", "ebitdaqoq", "ebitda_qoq"),
     "qoq", "_qoq_ebitda", "%", 1.0, "EBITDA QoQ 성장률")
_reg("ni_qoq", "순이익QoQ", ("순이익QoQ", "순이익qoq", "niqoq",
                            "netincome_qoq"), "qoq",
     "_qoq_net_income", "%", 1.0, "순이익 QoQ 성장률")
_reg("eps_qoq", "EPSQoQ", ("EPSQoQ", "epsqoq", "eps_qoq"), "qoq",
     "_qoq_eps", "%", 1.0, "주당순이익 QoQ 성장률 (희석)")
_reg("ocf_qoq", "영업현금흐름QoQ", ("영업현금흐름QoQ", "영업현금흐름qoq",
                                  "ocfqoq", "ocf_qoq"), "qoq",
     "_qoq_ocf", "%", 1.0, "영업현금흐름 QoQ 성장률")

# 기술적 추세 (Phase 3 — pykrx 일봉, 시총 상위 N개만 스캔, ₩0·yfinance 무관).
# Minervini SEPA 추세 템플릿 (finance-skills 개념 재구현, 사용자 2026-06-22).
_reg("tt", "트렌드템플릿", ("트렌드템플릿", "tt", "minervini", "미너비니",
                          "추세템플릿", "trend_template"), "tech",
     "tt", "", desc="Minervini 추세 템플릿 7조건 충족=1 (이평정렬·52주위치)")
_reg("off_low", "52주저점대비", ("52주저점대비", "off_low", "저점대비"), "tech",
     "off_low", "%", desc="52주 최저가 대비 상승률 (Minervini ≥30)")
_reg("to_high", "52주고점대비", ("52주고점대비", "to_high", "고점대비"), "tech",
     "to_high", "%", desc="52주 최고가 대비 위치 (음수=아래, Minervini ≥-25)")
_reg("rs6m", "6개월수익률", ("6개월수익률", "rs6m", "rs", "상대강도6개월"), "tech",
     "rs6m", "%", desc="6개월 가격수익률 (상대강도 참고치)")

_QOQ_STMT_MAP: dict[str, tuple[str, str]] = {
    "_qoq_revenue": ("income_stmt", "Total Revenue"),
    "_qoq_gross_profit": ("income_stmt", "Gross Profit"),
    "_qoq_op_income": ("income_stmt", "Operating Income"),
    "_qoq_ebitda": ("income_stmt", "EBITDA"),
    "_qoq_net_income": ("income_stmt", "Net Income"),
    "_qoq_eps": ("income_stmt", "Diluted EPS"),
    "_qoq_ocf": ("cashflow", "Operating Cash Flow"),
}

# Alias lookup table
_ALIAS_MAP: dict[str, str] = {}
for _k, _m in METRICS.items():
    _ALIAS_MAP[_k] = _k
    _ALIAS_MAP[_m.name.lower()] = _k
    for _a in _m.aliases:
        _ALIAS_MAP[_a.lower()] = _k


# ── Condition parsing ──────────────────────────────────────────────


@dataclass
class Condition:
    metric_key: str
    operator: str
    value: float
    metric: MetricDef = field(init=False, repr=False)

    def __post_init__(self):
        self.metric = METRICS[self.metric_key]

    def test(self, raw_value) -> bool:
        if raw_value is None:
            return False
        try:
            v = float(raw_value)
        except (TypeError, ValueError):
            return False
        if math.isnan(v) or math.isinf(v):
            return False
        v *= self.metric.scale
        if self.operator == ">":
            return v > self.value
        if self.operator == "<":
            return v < self.value
        if self.operator == ">=":
            return v >= self.value
        if self.operator == "<=":
            return v <= self.value
        if self.operator == "=":
            return abs(v - self.value) < 0.01
        return False

    def display(self) -> str:
        op = self.operator.replace("<", "&lt;").replace(">", "&gt;")
        return f"{self.metric.name}{op}{self.value:g}{self.metric.unit}"

    def display_raw(self) -> str:
        return f"{self.metric.name}{self.operator}{self.value:g}{self.metric.unit}"


# 지표명에 숫자 허용(52주저점대비·rs6m·6개월수익률 등) — 연산자(>·<…)가 클래스에
# 없어 greedy 라도 값과 안 섞임(최장일치 = 접두 별칭 모호성 차단). 값은 음수 허용
# (52주고점대비>=-25 = 고점 25% 이내 등).
_COND_RE = re.compile(
    r"([가-힣A-Za-z0-9/_]+)\s*(>=|<=|!=|>|<|=)\s*(-?[0-9]+(?:\.[0-9]+)?)"
)


def parse_conditions(text: str) -> list[Condition]:
    text = text.replace(",", " ").replace("·", " ").replace("&", " ")
    conditions = []
    for m in _COND_RE.finditer(text):
        name, op, val = m.group(1), m.group(2), float(m.group(3))
        key = _ALIAS_MAP.get(name.lower().replace("/", "_"))
        if key is None:
            raise ValueError(
                f"알 수 없는 지표: '{name}'\n"
                f"/screen list 로 사용 가능한 지표를 확인하세요."
            )
        conditions.append(Condition(metric_key=key, operator=op, value=val))
    if not conditions:
        raise ValueError(
            "조건을 파싱할 수 없습니다.\n"
            "예시: /screen PER<15 PBR<1 배당수익률>3"
        )
    return conditions


# ── Presets ─────────────────────────────────────────────────────────

PRESETS: dict[str, dict] = {
    "valueup": {
        "name": "밸류업 수혜주",
        "desc": "조특법§104의27 고배당기업 (배당성향≥40%, 부채비율<200%)",
        "conditions": "배당성향>=40 부채비율<200 배당수익률>0",
    },
    "valueup2": {
        "name": "밸류업 성장형",
        "desc": "배당성향≥25% + 부채비율<200% (이익배당 성장 기업)",
        "conditions": "배당성향>=25 부채비율<200 배당수익률>0",
    },
    "highdiv": {
        "name": "고배당",
        "desc": "배당수익률 3%+, PER 합리적",
        "conditions": "배당수익률>3 PER>0 PER<30",
    },
    "value": {
        "name": "저평가 가치주",
        "desc": "PER<10, PBR<1, 배당 유",
        "conditions": "PER<10 PER>0 PBR<1 PBR>0 배당수익률>0",
    },
    "growth": {
        "name": "성장주",
        "desc": "매출성장률 20%+, 영업이익률 10%+",
        "conditions": "매출성장률>20 영업이익률>10",
    },
    "quality": {
        "name": "퀄리티",
        "desc": "ROE 15%+, 부채비율<100, 영업이익률>10%",
        "conditions": "ROE>15 부채비율<100 영업이익률>10",
    },
    "lowpbr": {
        "name": "저PBR",
        "desc": "PBR<0.5 (청산 가치 대비 저평가)",
        "conditions": "PBR<0.5 PBR>0 시총>500",
    },
    "minervini": {
        "name": "Minervini 추세",
        "desc": "SEPA 추세 템플릿(이평 정렬+52주 위치) · 시총 상위 스캔",
        "conditions": "트렌드템플릿>=1 시총>500 거래량>0",
    },
}


# ── Bulk data fetching (Phase 1 — KR) ──────────────────────────────

def _fetch_kr_bulk() -> Optional[dict]:
    """Fetch PER/PBR/DIV/EPS/BPS + 시총/종가/거래량 for ALL KR tickers.

    Returns {ticker_code: {PER, PBR, EPS, BPS, DIV, 시가총액, 종가, 거래량, 종목명}}
    or None on failure.  Uses pykrx bulk calls — 2 HTTP requests total.
    """
    try:
        from bot.pykrx_client import krx_login_ready
        if not krx_login_ready():
            log.warning("stock_screener: KRX creds not available")
            return None
    except ImportError:
        return None

    try:
        from pykrx import stock
    except ImportError:
        log.warning("stock_screener: pykrx not installed")
        return None

    today = datetime.now()
    result = None

    for days_back in range(0, 10):
        target = today - timedelta(days=days_back)
        date_str = target.strftime("%Y%m%d")
        try:
            df_fund = stock.get_market_fundamental_by_ticker(date_str)
            df_cap = stock.get_market_cap_by_ticker(date_str)
        except Exception as exc:
            log.debug("stock_screener bulk fetch %s failed: %s", date_str, exc)
            continue

        if df_fund is None or len(df_fund) == 0:
            continue
        if df_cap is None or len(df_cap) == 0:
            continue

        result = {}
        for code in df_fund.index:
            row_f = df_fund.loc[code]
            row_c = df_cap.loc[code] if code in df_cap.index else {}
            name = ""
            if hasattr(row_f, "get"):
                name = row_f.get("종목명", "")
            elif "종목명" in df_fund.columns:
                name = row_f["종목명"] if "종목명" in row_f.index else ""

            entry = {
                "PER": _safe_float(row_f.get("PER") if hasattr(row_f, "get") else row_f["PER"]),
                "PBR": _safe_float(row_f.get("PBR") if hasattr(row_f, "get") else row_f["PBR"]),
                "EPS": _safe_float(row_f.get("EPS") if hasattr(row_f, "get") else row_f["EPS"]),
                "BPS": _safe_float(row_f.get("BPS") if hasattr(row_f, "get") else row_f["BPS"]),
                "DIV": _safe_float(row_f.get("DIV") if hasattr(row_f, "get") else row_f["DIV"]),
                "종목명": str(name) if name else "",
            }
            if hasattr(row_c, "get"):
                entry["시가총액"] = _safe_float(row_c.get("시가총액"))
                entry["종가"] = _safe_float(row_c.get("종가"))
                entry["거래량"] = _safe_float(row_c.get("거래량"))
            elif len(row_c) > 0:
                entry["시가총액"] = _safe_float(row_c["시가총액"] if "시가총액" in row_c.index else None)
                entry["종가"] = _safe_float(row_c["종가"] if "종가" in row_c.index else None)
                entry["거래량"] = _safe_float(row_c["거래량"] if "거래량" in row_c.index else None)

            if entry.get("시가총액"):
                entry["시가총액"] = entry["시가총액"] / 1e8

            result[code] = entry

        if result:
            log.info("stock_screener: bulk fetched %d tickers for %s", len(result), date_str)
            break

    return result


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _compute_qoq_values(ticker_obj, qoq_map: dict, result: dict):
    """Compute QoQ growth rates from quarterly statements, store in result."""
    need_income = any(s == "income_stmt" for s, _ in qoq_map.values())
    need_cf = any(s == "cashflow" for s, _ in qoq_map.values())
    stmts: dict = {}
    try:
        if need_income:
            qs = ticker_obj.quarterly_income_stmt
            if qs is not None and not qs.empty:
                stmts["income_stmt"] = qs
        if need_cf:
            qc = ticker_obj.quarterly_cashflow
            if qc is not None and not qc.empty:
                stmts["cashflow"] = qc
    except Exception:
        return
    for key, (stmt_type, row_name) in qoq_map.items():
        df = stmts.get(stmt_type)
        if df is None or len(df.columns) < 2:
            continue
        if row_name not in df.index:
            continue
        try:
            curr = float(df.loc[row_name].iloc[0])
            prev = float(df.loc[row_name].iloc[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isnan(curr) or math.isnan(prev) or prev == 0:
            continue
        result[key] = round((curr - prev) / abs(prev) * 100, 2)


def _fetch_yfinance_batch(tickers: list[str], fields: list[str],
                          max_workers: int = 8,
                          qoq_fields: Optional[dict] = None) -> dict[str, dict]:
    """Fetch yfinance .info for a batch of tickers. Returns {ticker: {field: value}}.

    If qoq_fields is provided ({computed_key: (stmt_type, row_name)}),
    also fetch quarterly statements and compute QoQ growth rates.
    """
    results = {}
    _qoq = qoq_fields or {}

    def _fetch_one(tk: str) -> tuple[str, dict]:
        try:
            import yfinance as yf
            t = yf.Ticker(tk)
            info = t.info or {}
            data = {f: _safe_float(info.get(f)) for f in fields}
            if _qoq:
                _compute_qoq_values(t, _qoq, data)
            return tk, data
        except Exception as exc:
            log.debug("yfinance fetch %s failed: %s", tk, exc)
            return tk, {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, tk): tk for tk in tickers}
        for future in as_completed(futures):
            try:
                tk, data = future.result(timeout=30)
                results[tk] = data
            except Exception:
                pass

    return results


# ── Cache ───────────────────────────────────────────────────────────

def _cache_key(conditions_text: str) -> str:
    import hashlib
    h = hashlib.sha256(conditions_text.strip().lower().encode()).hexdigest()[:12]
    return h


def _cache_path(key: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return _CACHE_DIR / f"{key}_{today}.json"


def _load_cache(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None


def _save_cache(key: str, data: dict):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        p = _cache_path(key)
        p.write_text(json.dumps(data, ensure_ascii=False, default=str),
                     encoding="utf-8")
    except Exception as exc:
        log.warning("screen cache save failed: %s", exc)


# ── Core screening engine ──────────────────────────────────────────


@dataclass
class ScreenResult:
    conditions: list[Condition]
    hits: list[dict]  # [{code, name, ticker, ...metric values}]
    total_universe: int
    elapsed_sec: float
    was_cached: bool = False
    market: str = "KR"
    note: str = ""     # 진단 라벨(예: 추세 스캔 N·데이터 M·통과 K) — 0건 원인 가시화


def run_screen(conditions: list[Condition],
               market: str = "KR",
               force_fresh: bool = False) -> ScreenResult:
    """Execute a screen against the specified market."""
    t0 = time.time()

    cond_text = " ".join(c.display() for c in conditions)
    ck = _cache_key(f"{market}:{cond_text}")

    if not force_fresh:
        cached = _load_cache(ck)
        if cached:
            hits = cached.get("hits", [])
            if market == "KR":
                missing = [h["code"] for h in hits
                           if not h.get("name") and h.get("code")]
                if missing:
                    name_map = _resolve_kr_names(missing)
                    for h in hits:
                        if not h.get("name") and h.get("code"):
                            h["name"] = name_map.get(h["code"], "")
            return ScreenResult(
                conditions=conditions,
                hits=hits,
                total_universe=cached.get("total_universe", 0),
                elapsed_sec=cached.get("elapsed_sec", 0),
                was_cached=True,
                market=market,
                note=cached.get("note", ""),
            )

    if market == "KR":
        result = _screen_kr(conditions)
    elif market == "US":
        result = _screen_us(conditions)
    else:
        log.warning("stock_screener: market %s not yet supported", market)
        result = ScreenResult(conditions=conditions, hits=[], total_universe=0,
                              elapsed_sec=time.time() - t0, market=market)

    result.elapsed_sec = time.time() - t0

    _save_cache(ck, {
        "hits": result.hits,
        "total_universe": result.total_universe,
        "elapsed_sec": result.elapsed_sec,
        "conditions": cond_text,
        "market": market,
        "note": result.note,
        "ts": datetime.now().isoformat(),
    })

    return result


def _screen_kr(conditions: list[Condition]) -> ScreenResult:
    """Screen KR market using pykrx bulk + yfinance refinement."""
    bulk = _fetch_kr_bulk()
    if not bulk:
        return ScreenResult(conditions=conditions, hits=[],
                            total_universe=0, elapsed_sec=0)

    total = len(bulk)

    pykrx_conds = [c for c in conditions if c.metric.source == "pykrx"]
    yf_conds = [c for c in conditions if c.metric.source == "yfinance"]
    qoq_conds = [c for c in conditions if c.metric.source == "qoq"]

    survivors = {}
    for code, data in bulk.items():
        if not data.get("종가"):
            continue
        passed = True
        for c in pykrx_conds:
            raw = data.get(c.metric.yf_field)
            if not c.test(raw):
                passed = False
                break
        if passed:
            survivors[code] = data

    log.info("stock_screener: Phase 1 pykrx bulk — %d/%d survive (%d conditions)",
             len(survivors), total, len(pykrx_conds))

    if (yf_conds or qoq_conds) and survivors:
        needed_fields = list({c.metric.yf_field for c in yf_conds})
        qoq_map = {c.metric.yf_field: _QOQ_STMT_MAP[c.metric.yf_field]
                    for c in qoq_conds if c.metric.yf_field in _QOQ_STMT_MAP}
        tickers_to_fetch = [f"{code}.KS" for code in survivors]
        kosdaq_codes = _get_kosdaq_codes()
        ticker_map = {}
        for code in survivors:
            suffix = ".KQ" if code in kosdaq_codes else ".KS"
            tk = f"{code}{suffix}"
            ticker_map[code] = tk

        batch_size = min(len(ticker_map), 200)
        all_tickers = list(ticker_map.values())[:batch_size]

        log.info("stock_screener: Phase 2 — fetching yfinance for %d tickers (%s%s)",
                 len(all_tickers), ", ".join(needed_fields),
                 f" + QoQ {len(qoq_map)}" if qoq_map else "")

        yf_data = _fetch_yfinance_batch(all_tickers, needed_fields,
                                         qoq_fields=qoq_map or None)

        reverse_map = {v: k for k, v in ticker_map.items()}
        final = {}
        for tk, yfd in yf_data.items():
            code = reverse_map.get(tk)
            if not code or code not in survivors:
                continue
            passed = True
            for c in yf_conds + qoq_conds:
                raw = yfd.get(c.metric.yf_field)
                if not c.test(raw):
                    passed = False
                    break
            if passed:
                merged = {**survivors[code], **yfd}
                final[code] = merged

        log.info("stock_screener: Phase 2 yfinance — %d/%d survive (%d conditions)",
                 len(final), len(survivors), len(yf_conds) + len(qoq_conds))
        survivors = final
    elif (yf_conds or qoq_conds) and not survivors:
        pass

    tech_note = ""
    tech_conds = [c for c in conditions if c.metric.source == "tech"]
    if tech_conds and survivors:
        from bot import pykrx_client as _pk
        # 일봉 fetch 비용 bound — 시총 상위 _TECH_SCAN_CAP 개만 스캔(시총순 정렬 후 캡).
        ranked = sorted(survivors.items(),
                        key=lambda kv: kv[1].get("시가총액", 0) or 0, reverse=True)
        scan = ranked[:_TECH_SCAN_CAP]
        log.info("stock_screener: Phase 3 tech — top %d by mcap (%d conditions)",
                 len(scan), len(tech_conds))

        def _tt(kv):
            try:
                return kv[0], _pk.get_kr_trend_template(kv[0])
            except Exception:
                return kv[0], None

        final = {}
        loaded = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for code, td in pool.map(_tt, scan):
                if not td:
                    continue
                loaded += 1
                if all(c.test(td.get(c.metric.yf_field)) for c in tech_conds):
                    final[code] = {**survivors[code], **td}
        # 가시성(사용자 2026-06-23 '0종목 원인 불명') — 스캔/데이터로드/통과 분리표시.
        # 데이터로드 << 스캔 이면 일봉 fetch 실패(데이터 문제), 통과만 0이면 추세부재.
        tech_note = (f"추세 스캔 상위 {len(scan)} · 일봉데이터 {loaded} · 통과 {len(final)}"
                     f" (시총상위 {_TECH_SCAN_CAP}캡)")
        log.info("stock_screener: Phase 3 tech — %s", tech_note)
        survivors = final

    name_map = _resolve_kr_names(list(survivors.keys()))

    hits = []
    for code, data in survivors.items():
        kosdaq = code in _get_kosdaq_codes()
        suffix = ".KQ" if kosdaq else ".KS"
        hit = {
            "code": code,
            "ticker": f"{code}{suffix}",
            "name": name_map.get(code, data.get("종목명", "")),
            "market": "KOSDAQ" if kosdaq else "KOSPI",
        }
        for c in conditions:
            raw = data.get(c.metric.yf_field)
            if raw is not None:
                hit[c.metric.key] = round(raw * c.metric.scale, 2)
        mcap = data.get("시가총액")
        if mcap:
            hit["mcap"] = round(mcap, 0)
        hit["price"] = data.get("종가")
        hits.append(hit)

    hits.sort(key=lambda x: x.get("mcap", 0) or 0, reverse=True)

    return ScreenResult(
        conditions=conditions, hits=hits,
        total_universe=total, elapsed_sec=0, note=tech_note,
    )


_KOSDAQ_CODES: Optional[set] = None


def _get_kosdaq_codes() -> set:
    global _KOSDAQ_CODES
    if _KOSDAQ_CODES is not None:
        return _KOSDAQ_CODES
    try:
        from pykrx import stock
        codes = set(stock.get_market_ticker_list(market="KOSDAQ"))
        _KOSDAQ_CODES = codes
        return codes
    except Exception:
        _KOSDAQ_CODES = set()
        return _KOSDAQ_CODES


def _resolve_kr_names(codes: list[str]) -> dict[str, str]:
    """Resolve KR ticker codes to Korean company names via pykrx."""
    names = {}
    try:
        from pykrx import stock
        for code in codes:
            try:
                n = stock.get_market_ticker_name(code)
                if n:
                    names[code] = n
            except Exception:
                pass
    except ImportError:
        pass
    return names


# ── US market (S&P 500) ───────────────────────────────────────────

_PYKRX_TO_YF_INFO: dict[str, tuple[str, float]] = {
    "PER": ("trailingPE", 1.0),
    "PBR": ("priceToBook", 1.0),
    "EPS": ("trailingEps", 1.0),
    "BPS": ("bookValue", 1.0),
    "DIV": ("dividendYield", 100.0),
    "시가총액": ("marketCap", 1e-6),
    "거래량": ("volume", 1.0),
    "종가": ("regularMarketPrice", 1.0),
}

_US_UNIVERSE_CACHE = _CACHE_DIR / "us_sp500.json"


def _get_us_universe() -> list[str]:
    if _US_UNIVERSE_CACHE.exists():
        try:
            data = json.loads(_US_UNIVERSE_CACHE.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) < 7 * 86400:
                return data.get("tickers", [])
        except Exception:
            pass
    tickers = _fetch_sp500_tickers()
    if tickers:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _US_UNIVERSE_CACHE.write_text(
            json.dumps({"tickers": tickers, "ts": time.time()}),
            encoding="utf-8")
    return tickers


def _fetch_sp500_tickers() -> list[str]:
    try:
        import pandas as pd
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"})
        tickers = tables[0]["Symbol"].tolist()
        return [t.replace(".", "-") for t in tickers if isinstance(t, str)]
    except Exception as exc:
        log.warning("stock_screener: S&P 500 list fetch failed: %s", exc)
        return []


def _screen_us(conditions: list[Condition]) -> ScreenResult:
    """Screen US S&P 500 using yfinance for all metrics."""
    universe = _get_us_universe()
    if not universe:
        return ScreenResult(conditions=conditions, hits=[],
                            total_universe=0, elapsed_sec=0, market="US")
    total = len(universe)

    yf_fields: set[str] = set()
    qoq_map: dict[str, tuple[str, str]] = {}
    cond_info: list[tuple[Condition, str, float]] = []
    for c in conditions:
        if c.metric.source == "qoq":
            yf_field = c.metric.yf_field
            prescale = 1.0
            if yf_field in _QOQ_STMT_MAP:
                qoq_map[yf_field] = _QOQ_STMT_MAP[yf_field]
        elif c.metric.source == "pykrx":
            mapping = _PYKRX_TO_YF_INFO.get(c.metric.yf_field)
            if not mapping:
                continue
            yf_field, prescale = mapping
            yf_fields.add(yf_field)
        else:
            yf_field = c.metric.yf_field
            prescale = 1.0
            yf_fields.add(yf_field)
        cond_info.append((c, yf_field, prescale))

    yf_fields.update(["marketCap", "shortName", "regularMarketPrice"])

    log.info("stock_screener: US — fetching yfinance for %d tickers%s",
             total, f" (+ QoQ {len(qoq_map)})" if qoq_map else "")
    yf_data = _fetch_yfinance_batch(universe, list(yf_fields), max_workers=20,
                                     qoq_fields=qoq_map or None)

    hits = []
    for ticker, data in yf_data.items():
        if not data:
            continue
        passed = True
        for c, yf_field, prescale in cond_info:
            raw = data.get(yf_field)
            if raw is None:
                passed = False
                break
            test_val = raw * prescale if prescale != 1.0 else raw
            if not c.test(test_val):
                passed = False
                break
        if passed:
            mcap_raw = data.get("marketCap")
            mcap_m = round(mcap_raw / 1e6, 0) if mcap_raw else None
            hit = {
                "code": ticker,
                "ticker": ticker,
                "name": data.get("shortName", "") or "",
                "market": "US",
                "mcap": mcap_m,
            }
            for c, yf_field, prescale in cond_info:
                raw = data.get(yf_field)
                if raw is not None:
                    hit[c.metric.key] = round(raw * prescale * c.metric.scale, 2)
            hit["price"] = data.get("regularMarketPrice")
            hits.append(hit)

    hits.sort(key=lambda x: x.get("mcap", 0) or 0, reverse=True)
    log.info("stock_screener: US — %d/%d survive", len(hits), total)
    return ScreenResult(conditions=conditions, hits=hits,
                        total_universe=total, elapsed_sec=0, market="US")


# ── Telegram formatting ────────────────────────────────────────────

def format_list_message() -> str:
    """Format /screen list output."""
    lines = ["📊 <b>조건부 스크리너 (/screen)</b>\n"]
    lines.append("<b>사용법:</b>")
    lines.append("  <code>/screen PER&lt;15 PBR&lt;1 배당수익률&gt;3</code> — KR")
    lines.append("  <code>/screen us PER&lt;15 DIV&gt;2</code> — US S&amp;P 500")
    lines.append("  <code>/screen 매출QoQ&gt;10 영업이익QoQ&gt;5</code> — QoQ 성장")
    lines.append("  <code>/screen valueup</code> (프리셋)")
    lines.append("  <code>/screen list</code> (이 목록)\n")

    lines.append("<b>📋 사용 가능 지표:</b>")
    lines.append("<b>Phase 1 (즉시, pykrx 전 종목):</b>")
    for k, m in METRICS.items():
        if m.source == "pykrx":
            aliases = "/".join(m.aliases[:3])
            lines.append(f"  <code>{m.name}</code> ({aliases}) — {m.desc}")

    lines.append("\n<b>Phase 2 (yfinance, 생존 종목만):</b>")
    for k, m in METRICS.items():
        if m.source == "yfinance":
            aliases = "/".join(m.aliases[:2])
            lines.append(f"  <code>{m.name}</code> ({aliases}) — {m.desc}")

    lines.append("\n<b>QoQ (전분기 대비 성장률, yfinance 분기 재무제표):</b>")
    for k, m in METRICS.items():
        if m.source == "qoq":
            aliases = "/".join(m.aliases[:2])
            lines.append(f"  <code>{m.name}</code> ({aliases}) — {m.desc}")

    lines.append("\n<b>📈 추세 (pykrx 일봉, 시총 상위 스캔 · KR):</b>")
    for k, m in METRICS.items():
        if m.source == "tech":
            aliases = "/".join(m.aliases[:2])
            lines.append(f"  <code>{m.name}</code> ({aliases}) — {m.desc}")

    lines.append("\n<b>🎯 프리셋:</b>")
    for slug, p in PRESETS.items():
        _d = p['desc'].replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"  <code>/screen {slug}</code> — {p['name']}: {_d}")

    lines.append(
        "\n<b>연산자:</b> <code>&gt; &lt; &gt;= &lt;= =</code>"
        "\n<b>시장:</b> KR (KOSPI+KOSDAQ) 기본, <code>us</code> 붙이면 US S&amp;P 500."
        "\n  KR: Phase 1 pykrx 전 종목 → Phase 2 yfinance 생존만"
        "\n  US: yfinance 개별 조회 (~1-2분, Phase 1 지표도 yfinance)"
        "\n  QoQ: 전분기 대비 성장률 (yfinance 분기 재무제표 기반)"
        "\n비용 ₩0. 24h 캐시."
    )
    return "\n".join(lines)


def format_result_message(result: ScreenResult) -> list[str]:
    """Format screen result for Telegram (chunked for 4096 limit)."""
    cond_str = " · ".join(c.display() for c in result.conditions)
    cache_tag = " 💾캐시" if result.was_cached else ""
    is_us = result.market == "US"
    market_tag = " (US S&amp;P 500)" if is_us else " (KR)"
    sort_note = "시가총액 내림차순 ($M, 대형주 우선)" if is_us else "시가총액 내림차순 (대형주 우선)"

    header = (
        f"📊 <b>조건부 스크리너 결과{market_tag}</b>{cache_tag}\n"
        f"조건: <code>{cond_str}</code>\n"
        f"결과: <b>{len(result.hits)}종목</b> / {result.total_universe:,}종목"
        f" · {result.elapsed_sec:.1f}초 · ₩0\n"
        f"정렬: {sort_note}\n"
        + (f"🔎 {result.note}\n" if result.note else "")
    )

    if not result.hits:
        return [header + "\n조건에 맞는 종목이 없습니다."]

    metric_keys = []
    for c in result.conditions:
        if c.metric_key not in metric_keys and c.metric_key not in ("mcap", "price"):
            metric_keys.append(c.metric_key)

    lines = [header, ""]
    for i, h in enumerate(result.hits, 1):
        name = h.get("name", "")
        ticker = h.get("ticker", "")
        mcap = h.get("mcap")
        mcap_str = (_fmt_mcap_us(mcap) if is_us else _fmt_mcap(mcap)) if mcap else ""

        vals = []
        for mk in metric_keys:
            v = h.get(mk)
            if v is not None:
                m = METRICS.get(mk)
                vals.append(f"{m.name}:{v:g}")
            else:
                vals.append(f"{METRICS[mk].name}:N/A")

        val_str = " · ".join(vals)
        label = f"<b>{name}</b> ({ticker})" if name else f"<b>{ticker}</b>"
        line = f"{i}. {label} {mcap_str}"
        if val_str:
            line += f"\n   {val_str}"
        lines.append(line)

    full = "\n".join(lines)
    return _chunk_html(full, 4000)


def _fmt_mcap(v: float) -> str:
    if v >= 10000:
        return f"[{v/10000:.1f}조]"
    return f"[{v:,.0f}억]"


def _fmt_mcap_us(v_millions: float) -> str:
    if v_millions >= 1000:
        return f"[${v_millions/1000:.1f}B]"
    return f"[${v_millions:,.0f}M]"


def _chunk_html(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ── Archive (dashboard) ────────────────────────────────────────────

def save_screen_archive(result: ScreenResult, conditions_text: str):
    """Save screen result to archive for dashboard display."""
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now()
    fname = f"{ts.strftime('%H%M%S')}_{_cache_key(conditions_text)}.json"
    day_dir = _ARCHIVE_DIR / ts.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "conditions": conditions_text,
        "conditions_display": " · ".join(c.display_raw() for c in result.conditions),
        "hit_count": len(result.hits),
        "total_universe": result.total_universe,
        "elapsed_sec": result.elapsed_sec,
        "market": result.market,
        "note": result.note,
        "hits": result.hits[:200],
        "ts": ts.isoformat(),
        "was_cached": result.was_cached,
    }
    (day_dir / fname).write_text(
        json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8"
    )
    log.info("screen archive saved: %s/%s", day_dir.name, fname)


def load_screen_archives() -> list[dict]:
    """Load all screen archives sorted by date desc."""
    if not _ARCHIVE_DIR.exists():
        return []
    items = []
    for day_dir in sorted(_ARCHIVE_DIR.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.iterdir(), reverse=True):
            if not f.name.endswith(".json"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["_file"] = f.name
                data["_date"] = day_dir.name
                items.append(data)
            except Exception:
                pass
    return items
