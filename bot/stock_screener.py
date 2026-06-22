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
# tech(추세) 메트릭은 종목별 일봉 fetch(pykrx ~1-2초/종목, 사실상 직렬). 시총 상위
# N개만 스캔(비용 bound, 일봉당 daily 캐시) + 데드라인으로 행 방지(사용자 2026-06-23
# 10분+ 행). 미완분은 daily 캐시에 적재돼 같은 날 재실행 시 누적 완성.
_TECH_SCAN_CAP = 150
_TECH_BUDGET_SEC = 75

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
        # df_cap 을 정규화 코드(zfill6 str) dict 로 — df_fund/df_cap 인덱스 포맷
        # 불일치 시 'code in df_cap.index' 가 전부 False → 시총/거래량 전멸하던
        # 근본원인 fix (사용자 2026-06-23 'bulk 시총보유 0'). 양쪽 zfill6 통일.
        cap_by_code = {}
        for _ci in df_cap.index:
            try:
                cap_by_code[str(_ci).zfill(6)] = df_cap.loc[_ci]
            except Exception:
                continue
        for code in df_fund.index:
            ckey = str(code).zfill(6)
            row_f = df_fund.loc[code]
            row_c = cap_by_code.get(ckey)
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
            if row_c is not None and hasattr(row_c, "get"):
                entry["시가총액"] = _safe_float(row_c.get("시가총액"))
                entry["종가"] = _safe_float(row_c.get("종가"))
                entry["거래량"] = _safe_float(row_c.get("거래량"))

            if entry.get("시가총액"):
                entry["시가총액"] = entry["시가총액"] / 1e8

            result[ckey] = entry

        # 장전(새벽) 호출 시 오늘자 df_cap 이 0값 placeholder 로 와 시총/거래량이
        # 전멸하던 근본원인 fix (사용자 2026-06-23 'bulk 시총보유 0'). 시총 보유 0
        # 이면 그 날짜 거부 → 직전 영업일로 폴백(실거래 데이터 확보까지).
        _cap_n = sum(1 for v in result.values() if v.get("시가총액"))
        if result and _cap_n > 0:
            log.info("stock_screener: bulk fetched %d tickers for %s (시총보유 %d)",
                     len(result), date_str, _cap_n)
            break
        log.debug("stock_screener: %s 시총 전무(장전 0값?) — 직전 영업일 폴백", date_str)
        result = None

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


# 스크린 결과 캐시 TTL — 가격/펀더는 EOD 갱신이라 영구캐시는 옛 결과 고착(사용자
# 2026-06-23 '또 0종목·💾캐시'). 6h 면 같은 조회 반복은 캐시, 하루 뒤·세션후엔 신선.
# 즉시 무시는 'fresh' 키워드(force_fresh).
_SCREEN_CACHE_TTL = 6 * 3600


def _load_cache(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        if time.time() - p.stat().st_mtime > _SCREEN_CACHE_TTL:
            return None            # 만료 → 재실행(신선)
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
    elif market == "JP":
        result = _screen_jp(conditions)
    elif market == "HK":
        result = _screen_hk(conditions)
    elif market == "CN_A":
        result = _screen_cn(conditions)
    elif market == "TW":
        result = _screen_tw(conditions)
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
    if tech_conds:
        phase1_n = len(survivors)
        if survivors:
            import concurrent.futures as _cf
            from bot import pykrx_client as _pk
            # 일봉 fetch 비용 bound — 시총 상위 _TECH_SCAN_CAP 개만 스캔(시총순 정렬 후 캡).
            ranked = sorted(survivors.items(),
                            key=lambda kv: kv[1].get("시가총액", 0) or 0, reverse=True)
            scan = ranked[:_TECH_SCAN_CAP]
            log.info("stock_screener: Phase 3 tech — top %d by mcap (%d conditions)",
                     len(scan), len(tech_conds))

            # ⏱ pykrx 일봉이 종목당 ~1-2초(사실상 직렬) → 데드라인 없으면 수백종목에
            # 10분+ 행(사용자 2026-06-23). _TECH_BUDGET_SEC 안에 완료분만 쓰고 미완은
            # 버림(부분결과 + 시간초과 표시). 미완 fetch 는 종목별 daily 캐시에 적재돼
            # 같은 날 재실행 시 누적 완성. with-블록은 종료 시 wait=True 라 행 → 수동
            # shutdown(wait=False).
            final = {}
            loaded = 0
            timed_out = False
            pool = ThreadPoolExecutor(max_workers=5)
            futmap = {pool.submit(_pk.get_kr_trend_template, code): code
                      for code, _d in scan}
            try:
                for fut in _cf.as_completed(futmap, timeout=_TECH_BUDGET_SEC):
                    code = futmap[fut]
                    try:
                        td = fut.result()
                    except Exception:
                        td = None
                    if not td:
                        continue
                    loaded += 1
                    if all(c.test(td.get(c.metric.yf_field)) for c in tech_conds):
                        final[code] = {**survivors[code], **td}
            except _cf.TimeoutError:
                timed_out = True
            pool.shutdown(wait=False, cancel_futures=True)
            # 가시성(사용자 2026-06-23) — 퍼널 전구간. 일봉데이터 << 스캔 = fetch 실패/
            # 미완, 통과만 0 = 추세부재. 시간초과면 부분결과임을 명시.
            tech_note = (f"사전필터 통과 {phase1_n} · 추세스캔 {len(scan)}"
                         f" · 일봉데이터 {loaded} · 통과 {len(final)}"
                         + (f" · ⏱{_TECH_BUDGET_SEC}s 초과(부분, 재실행 시 캐시누적)"
                            if timed_out else "")
                         + f" (시총상위 {_TECH_SCAN_CAP}캡)")
            log.info("stock_screener: Phase 3 tech — %s", tech_note)
            survivors = final
        else:
            # Phase 1 사전필터(시총·거래량 등)에서 전멸 → tech 스킵. bulk 필드 보유수로
            # '데이터 결측'(시총/거래량 None) vs '임계값 과함' 즉시 구분(사용자 2026-06-23).
            _mc = sum(1 for d in bulk.values() if d.get("시가총액"))
            _vol = sum(1 for d in bulk.values() if d.get("거래량"))
            tech_note = (f"사전필터 통과 0/{total} — 추세 스캔 전 전멸 "
                         f"(bulk 시총보유 {_mc}·거래량보유 {_vol})")
            log.warning("stock_screener: tech skipped — %s", tech_note)

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
_JP_UNIVERSE_CACHE = _CACHE_DIR / "jp_n225.json"
_HK_UNIVERSE_CACHE = _CACHE_DIR / "hk_top.json"
_CN_UNIVERSE_CACHE = _CACHE_DIR / "cn_top.json"
_TW_UNIVERSE_CACHE = _CACHE_DIR / "tw_top.json"
# 아시아 시장은 유동 부분집합으로 바운드(사용자 2026-06-23 JP 'A' → HK/CN/TW 동일):
# 전종목(JPX ~3700·TWSE ~1800) .info+일봉 전수 스캔하면 10분+ 행 → 시총/거래대금
# 상위 _ASIA_UNIVERSE_CAP 만. JP/HK/CN 은 네이버 worldstock 시총, TW 는 거래대금.
_ASIA_UNIVERSE_CAP = 225
_JP_UNIVERSE_CAP = _ASIA_UNIVERSE_CAP    # 하위호환(테스트/문서)


def _read_universe_cache(path: Path) -> Optional[list[str]]:
    """유니버스 7일 디스크 캐시 읽기 — 신선하면 tickers, 아니면 None."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) < 7 * 86400:
                return data.get("tickers", [])
        except Exception:
            pass
    return None


def _write_universe_cache(path: Path, tickers: list[str], **extra) -> None:
    if tickers:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tickers": tickers, "ts": time.time(), **extra}),
                        encoding="utf-8")


def _cap_by_liq(full: list[str], market: str) -> list[str]:
    """전종목 → 네이버 worldstock 시총상위 _ASIA_UNIVERSE_CAP (신고저 위젯과 동일
    검증 파이프 재사용). 네이버 미커버(TW 등)·실패 시 앞 N 폴백."""
    try:
        from bot.intl_highlow import _cap_by_liquidity
        return _cap_by_liquidity(full, _ASIA_UNIVERSE_CAP, market)
    except Exception:
        return full[:_ASIA_UNIVERSE_CAP]


def _get_jp_universe() -> list[str]:
    """JP 유니버스 = JPX 공식 상장목록(intl_universe.full_universe)을 시총상위 ~225
    로 바운드. 7일 캐시. 소스/네트워크 실패 시 빈 목록."""
    c = _read_universe_cache(_JP_UNIVERSE_CACHE)
    if c is not None:
        return c
    tickers: list[str] = []
    try:
        from bot.intl_universe import full_universe
        full = full_universe("JP")
        if full and len(full) > 100:
            tickers = _cap_by_liq(full, "JP")
    except Exception as exc:
        log.warning("stock_screener: JP universe(JPX) failed: %s", exc)
    _write_universe_cache(_JP_UNIVERSE_CACHE, tickers)
    return tickers


def _get_hk_universe() -> list[str]:
    """HK 유니버스 = HKEX 공식 상장목록(full_universe)을 시총상위 ~225 로 바운드
    (JP 와 완전 동일 경로). 7일 캐시. graceful."""
    c = _read_universe_cache(_HK_UNIVERSE_CACHE)
    if c is not None:
        return c
    tickers: list[str] = []
    try:
        from bot.intl_universe import full_universe
        full = full_universe("HK")
        if full and len(full) > 100:
            tickers = _cap_by_liq(full, "HK")
    except Exception as exc:
        log.warning("stock_screener: HK universe(HKEX) failed: %s", exc)
    _write_universe_cache(_HK_UNIVERSE_CACHE, tickers)
    return tickers


def _get_cn_universe() -> list[str]:
    """CN 유니버스 = CSI300+500(akshare_client.list_csi300_500, ~800)을 시총상위
    ~225 로 바운드. 7일 캐시. AKShare 미설치/실패 시 빈 목록(신고저와 동일 소스)."""
    c = _read_universe_cache(_CN_UNIVERSE_CACHE)
    if c is not None:
        return c
    tickers: list[str] = []
    try:
        from bot.akshare_client import list_csi300_500
        csi = list_csi300_500()              # {ticker(.SS/.SZ): 中文명}
        full = list(csi.keys())
        if full and len(full) > 100:
            tickers = _cap_by_liq(full, "CN_A")
    except Exception as exc:
        log.warning("stock_screener: CN universe(CSI300+500) failed: %s", exc)
    _write_universe_cache(_CN_UNIVERSE_CACHE, tickers)
    return tickers


def _get_tw_universe() -> tuple[list[str], dict]:
    """TW 유니버스 = TWSE(.TW)+TPEx(.TWO) 전 일반종목을 **거래대금(value)** 상위
    ~225 로 바운드 + 中文명 맵(네이버 worldstock 이 TW 미지원이라 STOCK_DAY_ALL 명칭
    사용). 7일 캐시. graceful — 소스 실패 시 ([], {})."""
    if _TW_UNIVERSE_CACHE.exists():
        try:
            data = json.loads(_TW_UNIVERSE_CACHE.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) < 7 * 86400:
                return data.get("tickers", []), data.get("names", {})
        except Exception:
            pass
    rows: list[tuple[str, float, str]] = []   # (ticker, value, name)
    try:
        from bot.twse_client import _is_common_stock, fetch_stock_day_all
        srcs = [("fetch_stock_day_all", ".TW")]
        import os as _os
        if _os.getenv("TW_HIGHLOW_OTC", "1").lower() not in ("0", "false", "off"):
            srcs.append(("fetch_tpex_day_all", ".TWO"))
        import bot.twse_client as _tw
        for fn_name, suf in srcs:
            fn = getattr(_tw, fn_name, None)
            if not fn:
                continue
            for s in fn().get("rows", []):
                code = str(s.get("code") or "")
                if not _is_common_stock(code):
                    continue
                tk = f"{code}{suf}"
                rows.append((tk, float(s.get("value") or 0), s.get("name") or code))
    except Exception as exc:
        log.warning("stock_screener: TW universe(TWSE/TPEx) failed: %s", exc)
    rows.sort(key=lambda r: r[1], reverse=True)
    rows = rows[:_ASIA_UNIVERSE_CAP]
    tickers = [r[0] for r in rows]
    names = {r[0]: r[2] for r in rows}
    _write_universe_cache(_TW_UNIVERSE_CACHE, tickers, names=names)
    return tickers, names


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
    # GitHub raw CSV(비차단) 우선 — 위키피디아는 VM 데이터센터 IP 가 403 차단되어
    # universe 가 비고 /screen us 가 0종목/0 이던 근본원인(사용자 2026-06-23). 위키는
    # 폴백. CSV 는 finviz_client 가 신고저 업종에 쓰는 검증된 소스 재사용.
    try:
        from bot.finviz_client import _fetch_sp500_csv
        tks, _names, _inds = _fetch_sp500_csv()
        if tks and len(tks) > 100:
            return tks
    except Exception as exc:
        log.warning("stock_screener: S&P 500 GitHub CSV failed: %s", exc)
    try:
        import pandas as pd
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"})
        tickers = tables[0]["Symbol"].tolist()
        return [t.replace(".", "-") for t in tickers if isinstance(t, str)]
    except Exception as exc:
        log.warning("stock_screener: S&P 500 wiki fallback failed: %s", exc)
        return []


def get_yf_trend_template(ticker: str) -> Optional[dict]:
    """yfinance 종목(US/JP 등) Minervini 추세 템플릿 — 일봉(2y)으로 7조건 판정. KR
    (pykrx)과 동일 순수함수 trend_template_from_series 재사용(universal). 종목별
    daily 캐시(티커 globally-unique: AAPL·7203.T 충돌 없음). yfinance history
    (download 계열)라 .info quote 보다 throttle 덜함. None=이력부족/실패(<222일).
    Rule applies to all yfinance markets going forward / US+JP 적용."""
    if not ticker:
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    cf = _CACHE_DIR / f"trend_tmpl_{ticker}_{today}.json"
    if cf.exists():
        try:
            if time.time() - cf.stat().st_mtime < 12 * 3600:
                return json.loads(cf.read_text())
        except Exception:
            pass
    try:
        import yfinance as yf
        from bot.pykrx_client import trend_template_from_series
        # period 는 yfinance 유효값만('15mo' 무효 → 빈 결과). 2y → ~500거래일,
        # trend_template 은 최근 252/≥222 사용.
        df = yf.Ticker(ticker).history(period="2y", auto_adjust=False)
    except Exception as exc:
        log.debug("yf_trend: %s fetch failed: %s", ticker, exc)
        return None
    if df is None or df.empty or len(df) < 222:
        return None
    try:
        closes = [float(x) for x in df["Close"].tolist()]
        highs = [float(x) for x in df["High"].tolist()]
        lows = [float(x) for x in df["Low"].tolist()]
        res = trend_template_from_series(closes, highs, lows)
        if not res:
            return None
        res["date"] = str(df.index[-1])[:10]
    except Exception as exc:
        log.debug("yf_trend: %s parse failed: %s", ticker, exc)
        return None
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(res))
    except Exception:
        pass
    return res


# 하위호환 별칭(옛 US 전용 이름) — 실 함수는 시장중립(get_yf_trend_template).
get_us_trend_template = get_yf_trend_template


def _screen_us(conditions: list[Condition]) -> ScreenResult:
    """Screen US S&P 500 using yfinance for all metrics (thin wrapper)."""
    universe = _get_us_universe()
    return _screen_yf(conditions, universe, "US",
                      empty_note="유니버스 비어있음 — S&P500 소스(GitHub CSV·위키 폴백) 실패")


def _screen_jp(conditions: list[Condition]) -> ScreenResult:
    """Screen JP — JPX 전종목을 시총상위 ~225 로 바운드(사용자 2026-06-23 'A').
    US 와 완전히 동일한 _screen_yf 경로(게이트 없음·universal)."""
    return _screen_yf(conditions, _get_jp_universe(), "JP",
                      empty_note="유니버스 비어있음 — JP 상장목록(JPX) 소스 실패")


def _screen_hk(conditions: list[Condition]) -> ScreenResult:
    """Screen HK — HKEX 전종목을 시총상위 ~225 로 바운드(JP 와 동일 경로)."""
    return _screen_yf(conditions, _get_hk_universe(), "HK",
                      empty_note="유니버스 비어있음 — HK 상장목록(HKEX) 소스 실패")


def _screen_cn(conditions: list[Condition]) -> ScreenResult:
    """Screen CN — CSI300+500 을 시총상위 ~225 로 바운드(AKShare, 신고저와 동일)."""
    return _screen_yf(conditions, _get_cn_universe(), "CN_A",
                      empty_note="유니버스 비어있음 — CN CSI300+500(AKShare) 소스 실패")


def _screen_tw(conditions: list[Condition]) -> ScreenResult:
    """Screen TW — TWSE/TPEx 전종목을 거래대금상위 ~225 로 바운드 + 中文명(네이버
    미커버라 STOCK_DAY_ALL 명칭)."""
    universe, names = _get_tw_universe()
    return _screen_yf(conditions, universe, "TW",
                      empty_note="유니버스 비어있음 — TW 상장목록(TWSE/TPEx) 소스 실패",
                      names=names)


def _screen_yf(conditions: list[Condition], universe: list[str], market: str,
               empty_note: str = "", names: Optional[dict] = None) -> ScreenResult:
    """yfinance 기반 시장 공용 스크리너(US/JP/HK/CN/TW). .info 사전필터 → Phase 3
    추세 템플릿(데드라인·캡·daily캐시). 시장특정 분기 없음 — universe·market(·names)
    만 주입. names = 유니버스 소스가 제공한 종목명(TW 中文 등, 네이버 미커버 보완).
    Rule applies to all yfinance markets going forward / US+JP+HK+CN+TW 적용."""
    if not universe:
        return ScreenResult(conditions=conditions, hits=[],
                            total_universe=0, elapsed_sec=0, market=market,
                            note=empty_note)
    total = len(universe)

    yf_fields: set[str] = set()
    qoq_map: dict[str, tuple[str, str]] = {}
    cond_info: list[tuple[Condition, str, float]] = []
    tech_conds = [c for c in conditions if c.metric.source == "tech"]
    for c in conditions:
        if c.metric.source == "tech":
            continue                       # tech 는 .info 아닌 일봉 — 아래 Phase 3
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

    log.info("stock_screener: %s — fetching yfinance for %d tickers%s",
             market, total, f" (+ QoQ {len(qoq_map)})" if qoq_map else "")
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
                "market": market,
                "mcap": mcap_m,
            }
            for c, yf_field, prescale in cond_info:
                raw = data.get(yf_field)
                if raw is not None:
                    hit[c.metric.key] = round(raw * prescale * c.metric.scale, 2)
            hit["price"] = data.get("regularMarketPrice")
            hits.append(hit)

    hits.sort(key=lambda x: x.get("mcap", 0) or 0, reverse=True)
    log.info("stock_screener: %s — %d/%d survive (.info)", market, len(hits), total)

    # ── Phase 3 tech (추세 템플릿) — KR 과 동일 패턴(데드라인·캡·daily캐시·순수함수).
    note = ""
    if tech_conds and hits:
        import concurrent.futures as _cf
        info_n = len(hits)
        scan = hits[:_TECH_SCAN_CAP]       # 이미 시총 내림차순
        final = []
        loaded = 0
        timed_out = False
        pool = ThreadPoolExecutor(max_workers=5)
        futmap = {pool.submit(get_yf_trend_template, h["ticker"]): h for h in scan}
        try:
            for fut in _cf.as_completed(futmap, timeout=_TECH_BUDGET_SEC):
                h = futmap[fut]
                try:
                    td = fut.result()
                except Exception:
                    td = None
                if not td:
                    continue
                loaded += 1
                if all(c.test(td.get(c.metric.yf_field)) for c in tech_conds):
                    for c in tech_conds:
                        if td.get(c.metric.yf_field) is not None:
                            h[c.metric.key] = round(td[c.metric.yf_field], 2)
                    final.append(h)
        except _cf.TimeoutError:
            timed_out = True
        pool.shutdown(wait=False, cancel_futures=True)
        note = (f".info 통과 {info_n} · 추세스캔 {len(scan)} · 일봉데이터 {loaded}"
                f" · 통과 {len(final)}"
                + (f" · ⏱{_TECH_BUDGET_SEC}s 초과(부분)" if timed_out else "")
                + f" (시총상위 {_TECH_SCAN_CAP}캡)")
        log.info("stock_screener: %s Phase 3 tech — %s", market, note)
        hits = final

    # 종목명 백필 — (1) 유니버스 소스 제공명(TW 中文 등), (2) 네이버 한글명(JP/HK/CN/US).
    if names:
        for h in hits:
            if (not h.get("name") or h.get("name") == h.get("ticker")) and names.get(h.get("ticker")):
                h["name"] = names[h["ticker"]]
    _backfill_names_naver(hits, market)
    return ScreenResult(conditions=conditions, hits=hits,
                        total_universe=total, elapsed_sec=0, market=market, note=note)


def _backfill_names_naver(hits: list[dict], market: str) -> None:
    """yfinance shortName 이 비거나 티커와 같은 종목명을 네이버 worldstock 한글명
    으로 백필 (사용자 2026-06-23 'JP 티커만 나옴 → 네이버로 종목명, 미국도·향후
    타국도'). JP/US/HK/CN universal — world_name_map(7d 캐시)라 run 당 1콜·저비용.
    기존 양호한 명칭(US 영문 등)은 보존(없을 때만 채움). graceful."""
    if not hits:
        return
    need = [h for h in hits if not h.get("name") or h.get("name") == h.get("ticker")]
    if not need:
        return
    try:
        from bot.naver_ranking_client import world_name_map
        nm = world_name_map(market)
    except Exception as exc:
        log.debug("stock_screener: %s name backfill failed: %s", market, exc)
        return
    if not nm:
        return
    for h in need:
        name = nm.get(h.get("ticker"))
        if name:
            h["name"] = name


# ── Telegram formatting ────────────────────────────────────────────

def format_list_message() -> str:
    """Format /screen list output."""
    lines = ["📊 <b>조건부 스크리너 (/screen)</b>\n"]
    lines.append("<b>사용법:</b>")
    lines.append("  <code>/screen PER&lt;15 PBR&lt;1 배당수익률&gt;3</code> — KR")
    lines.append("  <code>/screen us PER&lt;15 DIV&gt;2</code> — US S&amp;P 500")
    lines.append("  <code>/screen 매출QoQ&gt;10 영업이익QoQ&gt;5</code> — QoQ 성장")
    lines.append("  <code>/screen valueup</code> (프리셋) · <code>/screen minervini</code> (추세)")
    lines.append("  <code>/screen minervini fresh</code> — 캐시 무시 강제 재실행")
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

    lines.append("\n<b>📈 추세 (일봉, 시총 상위 스캔 · KR=pykrx / 해외=yfinance):</b>")
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
        "\n<b>시장:</b> KR(KOSPI+KOSDAQ) 기본 · <code>us</code>=S&amp;P500 ·"
        " <code>jp</code>=닛케이격 · <code>hk</code>=홍콩 · <code>cn</code>=본토 ·"
        " <code>tw</code>=대만 (해외=시총상위 ~225)."
        "\n  KR: Phase 1 pykrx 전 종목 → Phase 2 yfinance 생존만"
        "\n  해외(us/jp/hk/cn/tw): yfinance 개별 조회 (~1-2분, Phase 1 지표도 yfinance)"
        "\n  QoQ: 전분기 대비 성장률 (yfinance 분기 재무제표 기반)"
        "\n비용 ₩0. 24h 캐시."
    )
    return "\n".join(lines)


def format_result_message(result: ScreenResult) -> list[str]:
    """Format screen result for Telegram (chunked for 4096 limit)."""
    cond_str = " · ".join(c.display() for c in result.conditions)
    cache_tag = " 💾캐시" if result.was_cached else ""
    market_tag = f" ({_MKT_TAG.get(result.market, 'KR')})"
    _ccy = _MKT_CCY.get(result.market, "")
    # 결과 hits 는 전 시장 시총 내림차순(_screen_yf). TW 는 유니버스만 거래대금상위로
    # 선별(시총 컬럼은 marketCap) — 표시 정렬 라벨은 시총 기준이 정확.
    sort_note = (f"시가총액 내림차순 ({_ccy}M, 대형주 우선)" if _ccy
                 else "시가총액 내림차순 (대형주 우선)")

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
        mcap_str = fmt_mcap_display(result.market, mcap, bracket=True) if mcap else ""

        vals = []
        for mk in metric_keys:
            v = h.get(mk)
            if v is not None:
                m = METRICS.get(mk)
                vals.append(f"{m.name}:{fmt_metric_value(v)}")
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


# 시장 표시 메타 — 결과 헤더 태그 + 시총 통화기호(전 surface 단일 소스).
_MKT_TAG = {
    "US": "US S&amp;P 500", "JP": "JP 닛케이225격", "HK": "HK 홍콩 시총상위",
    "CN_A": "CN 본토 CSI300+500", "TW": "TW 대만 거래대금상위", "KR": "KR",
}
# 통화기호 — 아시아권은 현지통화 조/억(10^12/10^8) 규약. US 는 별도($B/M).
_MKT_CCY = {"JP": "¥", "HK": "HK$", "CN_A": "元", "TW": "NT$"}


def fmt_mcap_display(market: str, mcap_millions: float, bracket: bool = True) -> str:
    """시총 표기 — 시장별 통화·단위 단일 소스(텔레그램 bracket=True / 대시보드
    bracket=False). US=$B/M, JP/HK/CN/TW=현지통화 조/억(marketCap/1e6=현지백만),
    KR=조/억(mcap 단위 억원). universal — 새 시장은 _MKT_CCY 1줄."""
    if not mcap_millions:
        return "" if bracket else "—"
    v = mcap_millions
    if market == "US":
        s = f"${v/1000:.1f}B" if v >= 1000 else f"${v:,.0f}M"
    elif market in _MKT_CCY:
        sym = _MKT_CCY[market]
        s = (f"{sym}{v/1e6:.1f}조" if v >= 1e6
             else f"{sym}{v/100:,.0f}억" if v >= 100
             else f"{sym}{v:,.0f}M")
    else:                                    # KR — mcap 이 이미 억원 단위
        s = f"{v/10000:.1f}조" if v >= 10000 else f"{v:,.0f}억"
    return f"[{s}]" if bracket else s


def fmt_metric_value(v) -> str:
    """스크리너 지표값 표기 — 과학표기(1.1e+06) 금지(사용자 2026-06-23 '볼륨 이상해').
    큰 수(거래량 등)는 천단위 콤마, 정수는 정수로, 소수는 트림. 전 surface 공용
    (텔레그램·대시보드 스크리너/아카이브). Rule applies to all markets / US+KR+JP."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f) or math.isinf(f):
        return "—"
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


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
