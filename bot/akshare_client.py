"""Thin client for AKShare (CN A-share + HK market data).

CN/HK analogue to bot/dart_client.py (KR), bot/edinet_client.py (JP),
bot/mops_client.py (TW). AKShare aggregates many free CN data sources
under a unified Python API — 东方财富 (Eastmoney) for news + 港股通
flow + ST status, 巨潮资讯 (cninfo) for A-share disclosures, 同花顺
for fundamentals, and 国家统计局 / 中国人民银行 mirrors for LPR + CPI
+ PMI macro.

Auth: none — AKShare scrapes / proxies the underlying portals. The
~200MB dep (~50 transitive packages: bs4 + tqdm + scipy + openpyxl +
pyecharts etc.) is heavy enough that we LAZY-IMPORT inside each method
rather than at module load. The bot's startup cost stays unchanged for
non-CN analyses; CN ticker analyses pay a ~2-3s import on the first
call (cached thereafter).

Caching: per-(ticker, day) JSON at ~/.tradingagents/cache/akshare/ with
12h TTL — same pattern as DART/EDINET/MOPS. AKShare endpoints are
IP-rate-limited by the underlying portals (东方财富 / 新浪 routinely
return 403 after 5-10 quick calls from the same IP), so caching is
load-bearing here.

Design parity with EDINET/DART/MOPS:
- get_recent_disclosures(ticker, days_back, limit) — same signature
- get_major_holders(ticker) — flow shareholders + 实控人
- next_earnings_window(ticker) — CN fiscal-year-end 12/31 + 季报
  filing deadlines (Q1 4/30, H1 8/31, Q3 10/31, Annual 4/30)
- is_st(ticker) / is_suspended(ticker) — CN-specific status flags
- format_akshare_cn_block(...) — same shape as DART/EDINET/MOPS

AKShare-not-installed handling: every method returns the empty value
shape ([] / None / False) with a log warning. The agent_utils.py
DATA SOURCE OFFLINE block (Rule A) then surfaces 'AKShare 미설치 — 본
실행에서 CN 공시 / 뉴스 / 港股通 flow 데이터 미수집' to prevent the
LLM from fabricating disclosure dates or holder names.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.akshare")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "akshare"
_CACHE_TTL_HOURS = 12

# Long-cache (forever) macro endpoints — LPR / CPI / PMI publish at most
# monthly so a 12h cache is wasteful. National-statistics releases are
# immutable once published.
_MACRO_CACHE_TTL_HOURS = 24


def _import_akshare():
    """Lazy import of AKShare. Returns the module or None on ImportError.
    Heavy dep (~50 transitive packages), only loaded on first CN ticker
    analysis. Logged once per process so production logs surface the
    install state without spamming."""
    try:
        import akshare as ak  # type: ignore
        return ak
    except ImportError as exc:
        log.warning(
            "akshare not installed (%s) — CN/HK data injection skipped."
            " Install with `pip install akshare` on the bot host.",
            exc,
        )
        return None
    except Exception as exc:
        # AKShare occasionally raises non-Import errors at module load
        # (sub-package incompatibility, network init etc.). Log and
        # degrade gracefully — same shape as other client modules.
        log.warning("akshare import failed unexpectedly: %s", exc)
        return None


def _ticker_to_cn_code(ticker: str) -> tuple[Optional[str], Optional[str]]:
    """Convert a yfinance-style CN/HK ticker to (akshare_code, market_tag).

    Mapping:
     • '600519.SS' → ('600519', 'CN_A_SH')   — 上海 (mainboard 600/601/603/605/688)
     • '000002.SZ' → ('000002', 'CN_A_SZ')   — 深圳 (mainboard 000/001 + ChiNext 300/301)
     • '0700.HK'   → ('00700',  'HK')         — Hong Kong (5-digit padded)
     • anything else → (None, None)

    AKShare 5-digit HK code convention: '0700.HK' → '00700'. Numeric
    part padded with leading zeros to 5 digits. Some endpoints accept
    '0700' verbatim too; we use the padded form for consistency.
    """
    if not ticker:
        return None, None
    t = ticker.upper()
    if t.endswith(".SS"):
        return t.split(".")[0], "CN_A_SH"
    if t.endswith(".SZ"):
        return t.split(".")[0], "CN_A_SZ"
    if t.endswith(".BJ"):
        return t.split(".")[0], "CN_A_BJ"
    if t.endswith(".HK"):
        code = t.split(".")[0]
        # Pad to 5 digits (AKShare HK convention)
        if code.isdigit():
            return code.zfill(5), "HK"
        return None, None
    return None, None


def _cache_get(cache_key: str, ttl_hours: float = _CACHE_TTL_HOURS):
    """Return cached value or None. ttl_hours=0 means never-fresh
    (always refetch); negative would be invalid (caller's bug)."""
    cache_file = _CACHE_DIR / cache_key
    if not cache_file.exists():
        return None
    try:
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < ttl_hours:
            return json.loads(cache_file.read_text())
    except Exception as exc:
        log.warning("akshare: cache read failed for %s: %s", cache_key, exc)
    return None


def _cache_put(cache_key: str, value) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / cache_key).write_text(
            json.dumps(value, ensure_ascii=False, default=str)
        )
    except Exception as exc:
        log.warning("akshare: cache write failed for %s: %s", cache_key, exc)


def _is_transient_network_error(exc: Exception) -> bool:
    """True when the exception looks like a one-off Eastmoney / 新浪 / 同花顺
    rate-limit or transient TCP close — worth retrying. Excludes
    permanent errors (404, AttributeError) which won't change on retry.
    """
    s = str(exc)
    if "RemoteDisconnected" in s or "ConnectionResetError" in s:
        return True
    name = type(exc).__name__
    if "ConnectionError" in name or "Timeout" in name:
        return True
    return False


def _fetch_with_retry(ak_fn, label: str, max_retries: int = 2):
    """Call `ak_fn()` (a zero-arg lambda) and retry transient network
    failures with exponential backoff (2s, 4s). Permanent failures
    (AttributeError, ValueError) short-circuit on first try since they
    won't fix by retrying. Returns the call result or None on full
    failure (also logs a final warning).

    Mitigates the 'RemoteDisconnected' pattern surfaced by 茅台
    2026-05-19 first-CN-validation run — Eastmoney endpoints
    `stock_zh_a_st_em` + `stock_zh_a_stop_em` failed 100% from the
    bot host (한국 IP) on first attempt, but typically succeed within
    1-2 retries. Without retry the ST/*ST + 停牌 HARD GUARDs effectively
    never fired in production.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return ak_fn()
        except Exception as exc:
            last_exc = exc
            if not _is_transient_network_error(exc):
                break
            if attempt < max_retries:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s
                continue
    log.warning("akshare: %s fetch failed (after %d retries): %s",
                label, max_retries, last_exc)
    return None


class AkshareClient:
    """Per-call AKShare wrapper. Cheap to construct; reuse via
    `get_akshare()` for the process-wide cached instance.

    All methods degrade silently on ImportError / network failure /
    parse failure — they return the empty shape so the agent_utils
    Rule A guard can detect the missing data and forbid LLM
    fabrication.
    """

    def __init__(self):
        # No API key for AKShare. Placeholder for symmetry with DART /
        # EDINET clients in case AKShare introduces auth later.
        pass

    # ---- 公司公告 (regulatory disclosures) ------------------------------

    def get_recent_disclosures(
        self, ticker: str, days_back: int = 30, limit: int = 8,
    ) -> list[dict]:
        """Return up to `limit` recent 公告 for a CN_A / HK ticker.

        Each entry: {date, subject, doc_type_label, filer_name}.
        Uses AKShare's stock_zh_a_disclosure_announcement_cninfo for
        A-share (巨潮资讯) and stock_zh_h_disclosure_em for HK
        H-shares (东方财富 港股 list).

        CN A-share announcement types we WANT to surface (mapped to
        Korean labels in the format function):
         • 业绩快报 / 业绩预告 — earnings flash / pre-announcement
         • 重大资产重组 / 收购 / 出售 — M&A
         • 股权激励 / 限制性股票 — equity incentive plans
         • 关联交易 — related-party transactions
         • 担保 — guarantee
         • 减持 / 增持 — shareholder reduction / increase
         • 停牌 / 复牌 — suspension / resumption (CRITICAL — see is_suspended)
         • 风险提示 / 退市风险 — risk warning / delisting risk (CRITICAL — see is_st)
        """
        code, market = _ticker_to_cn_code(ticker)
        if not code:
            return []

        cache_key = f"disclosures_{code}_{market}_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached[:limit]

        ak = _import_akshare()
        if ak is None:
            return []

        results: list[dict] = []
        try:
            if market in ("CN_A_SH", "CN_A_SZ", "CN_A_BJ"):
                # 巨潮资讯 A주 公告 list (1.18.62 actual function name —
                # the older `_announcement_cninfo` was renamed pre-1.18).
                # `market="沪深京"` covers SH + SZ + BJ in one call.
                # Wrapped in _fetch_with_retry (2x backoff) — SMIC
                # 688981.SS 2026-05-19 04:22 surfaced 'Response ended
                # prematurely' transient error; 1-shot fetch was silently
                # dropping disclosure data for the whole analysis.
                cutoff_dt = date.today() - timedelta(days=days_back)
                df = _fetch_with_retry(
                    lambda: ak.stock_zh_a_disclosure_report_cninfo(
                        symbol=code,
                        market="沪深京",
                        start_date=cutoff_dt.strftime("%Y%m%d"),
                        end_date=date.today().strftime("%Y%m%d"),
                    ),
                    label=f"disclosures for {code}",
                )
            elif market == "HK":
                # HK disclosure endpoint — AKShare 1.18.62 doesn't expose
                # a clean HK-only disclosure wrapper. Best-effort try;
                # falls through to news block when unavailable.
                try:
                    df = _fetch_with_retry(
                        lambda: ak.stock_zh_h_disclosure_em(symbol=code),
                        label=f"HK disclosures for {code}",
                    )
                except AttributeError:
                    log.info(
                        "akshare: HK disclosure endpoint unavailable in"
                        " this version — Rule A guard will surface 'HK"
                        " 공시 데이터 미수집'"
                    )
                    df = None
            else:
                df = None

            if df is None or len(df) == 0:
                _cache_put(cache_key, [])
                return []

            # AKShare column conventions:
            #   '公告日期' or '日期' for filing date
            #   '公告标题' or '标题' or '公告主题' for subject
            #   '公告类型' or '类型' for doc_type (varies by endpoint)
            today = date.today()
            cutoff = today - timedelta(days=days_back)
            df_cols = list(df.columns)
            date_col = next(
                (c for c in df_cols if c in ("公告日期", "日期", "公告时间", "发布时间")),
                None,
            )
            subject_col = next(
                (c for c in df_cols if c in ("公告标题", "标题", "公告主题", "subject")),
                None,
            )
            type_col = next(
                (c for c in df_cols if c in ("公告类型", "类型")),
                None,
            )
            if not date_col or not subject_col:
                log.warning(
                    "akshare: disclosure schema unexpected for %s — cols=%s",
                    code, df_cols,
                )
                return []

            for _, row in df.iterrows():
                date_raw = row[date_col]
                try:
                    # AKShare returns datetime / str; normalise via str slice
                    d_str = str(date_raw)[:10]
                    d = datetime.strptime(d_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if d < cutoff:
                    continue
                subject = str(row[subject_col] or "").strip()
                if not subject:
                    continue
                doc_type = str(row[type_col]) if type_col else "公告"
                results.append({
                    "date": d.isoformat(),
                    "subject": subject,
                    "doc_type_label": doc_type,
                    "filer_name": code,
                })
        except Exception as exc:
            log.warning("akshare: disclosures fetch failed for %s: %s", code, exc)
            return []

        results.sort(key=lambda r: r["date"], reverse=True)
        _cache_put(cache_key, results)
        return results[:limit]

    # ---- 主要股东 / 实控人 (major holders) -----------------------------

    def get_major_holders(self, ticker: str) -> list[dict]:
        """Pull 主要股东 / 流通股东 list — CN/HK equivalent of DART
        임원·주요주주 지분 + EDINET 5%+ 대량보유 + MOPS 內部人持股.

        For A-shares: stock_circulate_stock_holder(symbol) returns top
        10 流通股东 with name + shares + pct + change vs prior period.
        For HK: limited free coverage — AKShare doesn't have a unified
        HK major-holders endpoint, return [] and let the anti-
        hallucination guard surface 'HK 소유구조 데이터 미수집'.
        """
        code, market = _ticker_to_cn_code(ticker)
        if not code:
            return []

        if market not in ("CN_A_SH", "CN_A_SZ", "CN_A_BJ"):
            # HK holder data not freely available via AKShare
            return []

        cache_key = f"holders_{code}_{market}_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        ak = _import_akshare()
        if ak is None:
            return []

        results: list[dict] = []
        try:
            # stock_circulate_stock_holder returns most recent 流通股东
            # (top 10 freely-traded shareholders), updated quarterly.
            df = ak.stock_circulate_stock_holder(symbol=code)
            if df is None or len(df) == 0:
                _cache_put(cache_key, [])
                return []
            cols = list(df.columns)
            name_col = next(
                (c for c in cols if c in ("股东名称", "名称", "name")),
                None,
            )
            shares_col = next(
                (c for c in cols if c in (
                    "持股数量", "持股股数", "股数", "持股数量(股)",
                )),
                None,
            )
            # Column needle list extended after 茅台 2026-05-19 validation:
            # AKShare 1.18.62 actual column is '占流通股比例' (NOT the
            # `_本_` longer form I'd guessed). Float-typed value (54.404)
            # so the old `str().rstrip('%')` parse returned 0.0 for all
            # 5 holders — 0.00% noise across the report.
            pct_col = next(
                (c for c in cols if c in (
                    "占流通股比例", "占流通股本持股比例",
                    "持股比例", "持股比例(%)", "流通A股比例", "比例",
                )),
                None,
            )
            # 股本性质 ('国有股' / '境外法人股' / '境内自然人股' / etc.)
            # — CN 종목의 SOE / 외국법인 / 자연인 분류 surfacing 용.
            # 'insider 62%' 가 国有股 모회사 지분이라는 사실을 라벨로
            # 노출해 reader 가 US 'insider' 컨텍스트와 혼동하지 않게.
            nature_col = next(
                (c for c in cols if c in ("股本性质", "性质")),
                None,
            )
            if not name_col:
                log.warning(
                    "akshare: holders schema unexpected for %s — cols=%s",
                    code, cols,
                )
                return []
            for _, row in df.head(10).iterrows():
                name = str(row[name_col] or "").strip()
                if not name:
                    continue
                try:
                    raw_shares = row[shares_col] if shares_col else 0
                    if isinstance(raw_shares, (int, float)):
                        shares = int(raw_shares)
                    else:
                        shares = int(float(str(raw_shares).replace(",", "") or 0))
                except Exception:
                    shares = 0
                # pct parse: handle BOTH float-typed (54.404) and str-typed
                # ("54.40%") column conventions. AKShare 1.18.62 returns
                # native float for 占流通股比例 — earlier rstrip("%") path
                # silently zeroed everything.
                pct: float = 0.0
                if pct_col is not None:
                    raw_pct = row[pct_col]
                    if isinstance(raw_pct, (int, float)) and raw_pct == raw_pct:
                        pct = float(raw_pct)
                    else:
                        try:
                            pct_str = str(raw_pct or "").strip().rstrip("%")
                            pct = float(pct_str) if pct_str else 0.0
                        except Exception:
                            pct = 0.0
                nature = ""
                if nature_col is not None:
                    nature = str(row[nature_col] or "").strip()
                results.append({
                    "name": name,
                    "shares": shares,
                    "pct": pct,
                    "nature": nature,
                })
        except Exception as exc:
            log.warning("akshare: holders fetch failed for %s: %s", code, exc)
            return []

        _cache_put(cache_key, results)
        return results

    # ---- ST / *ST 분류 + 停牌 -------------------------------------------

    def is_st(self, ticker: str) -> bool:
        """Return True when the A-share ticker carries ST or *ST status
        (2년 연속 적자 → ST 일일제한 ±5%; 3년 연속 적자 → *ST 퇴출
        위험). Critical for the fundamentals analyst: ST 종목의 일일
        ±5% 변동 한도가 RULE 13에 별도 명시되어야 하며, *ST는 퇴출
        프로세스 진행 중이므로 5거래일 분석의 의미가 매우 다르다.

        HK has no ST equivalent — returns False for HK tickers.
        """
        code, market = _ticker_to_cn_code(ticker)
        if not code or market not in ("CN_A_SH", "CN_A_SZ", "CN_A_BJ"):
            return False

        # Daily-scope cache — ST status is determined by trailing
        # earnings + 2 prior fiscal years, doesn't change intra-day.
        cache_key = f"st_status_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key, ttl_hours=12)
        if cached is None:
            ak = _import_akshare()
            if ak is None:
                return False
            df = _fetch_with_retry(ak.stock_zh_a_st_em, "ST status")
            if df is None or len(df) == 0:
                cached = []
            else:
                code_col = next(
                    (c for c in df.columns if c in ("代码", "code", "股票代码")),
                    None,
                )
                if not code_col:
                    log.warning(
                        "akshare: ST schema unexpected — cols=%s",
                        list(df.columns),
                    )
                    cached = []
                else:
                    cached = [str(c).zfill(6) for c in df[code_col].tolist()]
            _cache_put(cache_key, cached)

        return code in cached

    def is_suspended(self, ticker: str) -> bool:
        """Return True when the ticker is currently 停牌 (trading halted).
        A-share only — HK halts are extremely rare and AKShare doesn't
        wrap them. yfinance silently fails to update prices during a
        halt, so this signal is critical for detecting 'why is the
        chart frozen?'.
        """
        code, market = _ticker_to_cn_code(ticker)
        if not code or market not in ("CN_A_SH", "CN_A_SZ", "CN_A_BJ"):
            return False

        cache_key = f"suspended_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key, ttl_hours=4)  # halts change intra-day
        if cached is None:
            ak = _import_akshare()
            if ak is None:
                return False
            df = _fetch_with_retry(ak.stock_zh_a_stop_em, "停牌 status")
            if df is None or len(df) == 0:
                cached = []
            else:
                code_col = next(
                    (c for c in df.columns if c in ("代码", "code", "股票代码")),
                    None,
                )
                cached = (
                    [str(c).zfill(6) for c in df[code_col].tolist()]
                    if code_col else []
                )
            _cache_put(cache_key, cached)

        return code in cached

    # ---- 港股通 / 沪股通 / 深股통 flow -----------------------------------

    def get_hsgt_flow_summary(self, days_back: int = 5) -> Optional[dict]:
        """5거래일 港股通 northbound + southbound 净 buy. KR pykrx flow
        의 CN 등가물 — 단기 가격 동인 중 가장 강력한 단일 신호.

        Returns {northbound_5d: float (CNY 억원), southbound_5d: float
        (HKD 억원), north_direction: 'buy'|'sell', south_direction:
        'buy'|'sell', dates: [...]} or None on failure.

        Northbound flow = 海外 funds entering CN_A via Stock Connect
        (沪股通 + 深股通). Strong positive = foreign demand into 본토.
        Southbound flow = 본토 capital entering HK via 港股通. Strong
        positive = mainland demand into HK (often signals reflux when
        본토 fundamentals weak or HK names cheap).

        IP-rate-limited; cached 4h (intraday flow updates every 5 min
        but a 4h-stale read is still actionable).

        Note: AKShare's HSGT endpoints (stock_hsgt_*_em) occasionally
        return empty from non-Mainland IPs even when not GFW-blocked
        (东方财富 sometimes rate-limits foreign IPs). Empty result
        is the expected degraded mode.
        """
        cache_key = f"hsgt_flow_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key, ttl_hours=4)
        if cached is not None:
            return cached

        ak = _import_akshare()
        if ak is None:
            return None

        # AKShare 1.18.62 actual endpoint — `stock_hsgt_fund_flow_summary_em`.
        # The older `stock_hsgt_north_net_flow_em` / `_south_net_flow_em`
        # don't exist in 1.18.62 (renamed). `stock_hsgt_hist_em` exists
        # but Eastmoney stopped feeding daily flow data publicly mid-2024
        # — every flow column comes back as nan. `_fund_flow_summary_em`
        # gives a market-wide daily breakdown by 4 channels (沪股通 北/南
        # + 深股通 北/南).
        try:
            df = ak.stock_hsgt_fund_flow_summary_em()
        except Exception as exc:
            log.warning("akshare: HSGT summary fetch failed: %s", exc)
            return None

        if df is None or len(df) == 0:
            return None

        cols = list(df.columns)
        direction_col = next(
            (c for c in cols if c in ("资金方向",)), None,
        )
        amount_col = next(
            (c for c in cols if c in ("成交净买额", "净买额", "净流入")),
            None,
        )
        if not direction_col or not amount_col:
            log.warning("akshare: HSGT summary schema unexpected cols=%s", cols)
            return None

        # Each trading day produces 4 rows (沪/深 × 北/南). Take the last
        # `days_back * 4` rows to cover the requested window, then sum
        # by 资金方向. Conservative window: even if AKShare returns >4
        # rows for some day (e.g. extra '总计' row), tail() of (N×4) is
        # close enough — over-summing biases toward zero rather than
        # toward fabricated values.
        rows = df.tail(days_back * 4)
        try:
            north = float(rows.loc[rows[direction_col] == "北向", amount_col].sum() or 0)
            south = float(rows.loc[rows[direction_col] == "南向", amount_col].sum() or 0)
        except Exception as exc:
            log.warning("akshare: HSGT summary aggregation failed: %s", exc)
            return None

        # NaN sanity — if Eastmoney returns nan-only flow columns (the
        # `stock_hsgt_hist_em` regression pattern from 2024-06+), both
        # sums will be nan. Treat as 'no data' rather than emitting
        # 'nan억元' to the LLM.
        if not (north == north) or not (south == south):
            return None
        if north == 0 and south == 0:
            # Likely 휴장 / 거래 정지일 — surfacing '0 净 buy' is misleading.
            return None

        result = {
            "northbound_5d": north,
            "southbound_5d": south,
            "north_direction": ("buy" if north > 0 else "sell"),
            "south_direction": ("buy" if south > 0 else "sell"),
        }
        _cache_put(cache_key, result)
        return result

    # ---- next earnings window (CN fiscal-year-end 12/31) ----------------

    def next_earnings_window(self, ticker: str) -> Optional[str]:
        """CN A-share 季报 / 年报 filing deadlines:
          • Q1 → 4월 30일
          • H1 (半年报) → 8월 31일
          • Q3 → 10월 31일
          • Annual (年报) → 익년 4월 30일

        HK has no rigid statutory deadline like A-share but most
        HK-listed companies follow either 12/31 (mainland-style) or
        3/31 (UK colonial-style) fiscal year. AKShare doesn't expose
        per-ticker fiscal-year-end; we default to 12/31 with the
        same A-share quarterly markers and let the analyst's
        narrative correct it if needed.
        """
        code, market = _ticker_to_cn_code(ticker)
        if not code:
            return None
        today = date.today()
        quarters = [
            (date(today.year, 4, 30), "Q1 (1-3월) 보고 마감"),
            (date(today.year, 8, 31), "H1 (1-6월) 보고 마감"),
            (date(today.year, 10, 31), "Q3 (1-9월) 보고 마감"),
            (date(today.year + 1, 4, 30), "Annual (1-12월) 보고 마감"),
        ]
        for due_date, label in quarters:
            if due_date >= today:
                return f"{due_date.isoformat()} {label}"
        return None

    # ---- 중국 macro (LPR / MLF / CPI / PMI) -----------------------------

    def fetch_cn_macro(self) -> dict:
        """Return a dict of CN-specific macro indicators for CN_A / HK
        analysis context.

        Indicators:
         • LPR 1Y (Loan Prime Rate, monthly fix from PBoC — 20th)
         • LPR 5Y (mortgage-anchor rate)
         • CPI YoY (monthly, ~10th of next month)
         • PMI manufacturing (monthly, 1st)

        These are the variables RULE 13 references for 부동산 / 银行 /
        소비 + 가전 / 통신 / 항공 sectors. Cached 24h since they update
        at most monthly. Returns empty dict on failure (Rule A guard
        catches the empty case and surfaces 'CN 매크로 데이터 미수집').
        """
        cache_key = f"cn_macro_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key, ttl_hours=_MACRO_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

        ak = _import_akshare()
        if ak is None:
            return {}

        result: dict = {}

        def _latest_valid_row(df, val_col_candidates: list[str]) -> Optional[float]:
            """Walk backward from the most-recent row until a non-nan value
            is found. AKShare macro endpoints include placeholder rows
            for upcoming-release dates (e.g. 2025-09-10 CPI 今值=nan when
            release is still pending), which the old `iloc[-1]` path
            surfaced as 'nan%' raw. 茅台 2026-05-19 first-run case."""
            if df is None or len(df) == 0:
                return None
            cols = list(df.columns)
            val_col = next((c for c in cols if c in val_col_candidates), None)
            if not val_col:
                return None
            for i in range(len(df) - 1, -1, -1):
                try:
                    v = float(df.iloc[i][val_col])
                    if v == v:  # NaN check (NaN != NaN)
                        return v
                except Exception:
                    continue
            return None

        try:
            df = ak.macro_china_lpr()
            result["lpr_1y"] = _latest_valid_row(df, ["LPR1Y", "1Y_LPR", "LPR_1Y"])
            result["lpr_5y"] = _latest_valid_row(df, ["LPR5Y", "5Y_LPR", "LPR_5Y"])
        except Exception as exc:
            log.warning("akshare: LPR fetch failed: %s", exc)

        # CPI: switched from monthly (MoM, '中国CPI月率报告') to yearly
        # (YoY) — RULE 13 references 'CPI YoY' and analysts read the
        # yearly rate as headline inflation. Monthly rate (-0.1, +0.4
        # 같은 작은 값) is misleading when labeled 'YoY'.
        try:
            df = ak.macro_china_cpi_yearly()
            result["cpi_yoy"] = _latest_valid_row(df, ["今值", "数值", "value"])
        except Exception as exc:
            log.warning("akshare: CPI yearly fetch failed: %s", exc)

        try:
            df = ak.macro_china_pmi_yearly()
            result["pmi"] = _latest_valid_row(df, ["今值", "数值", "value"])
        except AttributeError:
            try:
                df = ak.macro_china_pmi()
                result["pmi"] = _latest_valid_row(df, ["今值", "数值", "value"])
            except Exception as exc:
                log.warning("akshare: PMI fetch (fallback) failed: %s", exc)
        except Exception as exc:
            log.warning("akshare: PMI fetch failed: %s", exc)

        _cache_put(cache_key, result)
        return result

    # ---- Eastmoney news (CN_A + HK) -------------------------------------

    def fetch_news(
        self, ticker: str, days_back: int = 28, max_items: int = 10,
    ) -> list[dict]:
        """Eastmoney 个股新闻 — per-ticker 簡体中文 news (CN_A) or HK
        news (HK tickers). Mirror Naver / Kabutan / cnyes news block
        shape: {date, title, source, link, summary}.

        For HK tickers Eastmoney returns Hong Kong financial press
        coverage; the language is still 简体中文 (Eastmoney is mainland-
        based) but covers HK-listed events. This is the primary news
        source for CN/HK analysis — yfinance / Alpha Vantage have
        sparse coverage.
        """
        code, market = _ticker_to_cn_code(ticker)
        if not code:
            return []

        # Per-day per-ticker cache — Eastmoney updates news intraday
        # but a 4h-stale read is still actionable for 5거래일 horizon.
        cache_key = f"news_{code}_{market}_{date.today().isoformat()}.json"
        cached = _cache_get(cache_key, ttl_hours=4)
        if cached is not None:
            return cached[:max_items]

        ak = _import_akshare()
        if ak is None:
            return []

        # Eastmoney code format for news endpoint:
        #   A-share Shanghai: 'sh' + code (e.g. 'sh600519')
        #   A-share Shenzhen: 'sz' + code
        #   A-share Beijing:  'bj' + code
        #   HK:               '0' + code (5-digit padded already), but
        #                     stock_news_em on HK is unreliable; some
        #                     versions only accept A-share. Best-effort.
        if market == "CN_A_SH":
            em_code = f"sh{code}"
        elif market == "CN_A_SZ":
            em_code = f"sz{code}"
        elif market == "CN_A_BJ":
            em_code = f"bj{code}"
        else:
            em_code = code  # HK: bare 5-digit; AKShare may or may not handle

        try:
            df = ak.stock_news_em(symbol=em_code)
            if df is None or len(df) == 0:
                _cache_put(cache_key, [])
                return []
        except Exception as exc:
            log.warning("akshare: news fetch failed for %s: %s", em_code, exc)
            return []

        cols = list(df.columns)
        date_col = next((c for c in cols if c in ("发布时间", "时间", "日期", "新闻发布时间")), None)
        title_col = next((c for c in cols if c in ("新闻标题", "标题", "title")), None)
        source_col = next((c for c in cols if c in ("文章来源", "来源", "source")), None)
        link_col = next((c for c in cols if c in ("新闻链接", "链接", "link", "url")), None)
        summary_col = next((c for c in cols if c in ("新闻内容", "内容", "摘要", "summary")), None)
        if not date_col or not title_col:
            log.warning("akshare: news schema unexpected for %s — cols=%s", em_code, cols)
            return []

        today = date.today()
        cutoff = today - timedelta(days=days_back)
        results: list[dict] = []
        for _, row in df.iterrows():
            try:
                d_str = str(row[date_col])[:10]
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except Exception:
                continue
            if d < cutoff:
                continue
            title = str(row[title_col] or "").strip()
            if not title:
                continue
            summary = str(row[summary_col] or "").strip() if summary_col else ""
            if len(summary) > 200:
                summary = summary[:197] + "..."
            results.append({
                "date": d.isoformat(),
                "title": title,
                "source": str(row[source_col]) if source_col else "东方财富",
                "link": str(row[link_col]) if link_col else "",
                "summary": summary,
            })

        results.sort(key=lambda r: r["date"], reverse=True)
        _cache_put(cache_key, results)
        return results[:max_items]


_akshare_singleton: AkshareClient | None = None


def get_akshare() -> AkshareClient:
    """Process-wide cached AKShare client. Cheap constructor; reuse keeps
    the lazy-import state consistent (one import attempt per process
    instead of N)."""
    global _akshare_singleton
    if _akshare_singleton is None:
        _akshare_singleton = AkshareClient()
    return _akshare_singleton


def has_recent_chinese_news(ticker: str) -> bool:
    """Mirror of has_recent_korean_news / has_recent_japanese_news /
    has_recent_taiwanese_news. Returns True iff Eastmoney has at
    least one article for this ticker in the last 28 days. Used by
    agent_utils.py to short-circuit yfinance-empty news cases."""
    try:
        return bool(get_akshare().fetch_news(ticker, days_back=28, max_items=1))
    except Exception:
        return False


def format_news_for_prompt(items: list[dict]) -> str:
    """Render Eastmoney news items as a prompt-ready bullet list.
    Same shape as bot.cnyes_client.format_news_for_prompt — 5 lines
    max per item (date, title, source, summary)."""
    if not items:
        return ""
    parts: list[str] = []
    for i in items:
        date_s = i.get("date") or "?"
        title = (i.get("title") or "").strip()
        source = (i.get("source") or "东方财富").strip()
        summary = (i.get("summary") or "").strip()
        parts.append(f"  • {date_s} [{source}] {title}")
        if summary:
            parts.append(f"      └─ {summary}")
    return "\n".join(parts)


def format_akshare_cn_block(
    disclosures: list[dict],
    holders: list[dict],
    earnings_window: Optional[str],
    is_st_flag: bool = False,
    is_suspended_flag: bool = False,
) -> str:
    """Render AKShare data as a prompt-ready bullet block — DART/EDINET/
    MOPS equivalent for CN_A / HK. Empty string when every slot is
    empty so the caller can omit the section header rather than emit
    a blank block.

    ST/*ST and 停牌 flags are status indicators (boolean), surfaced
    at the top of the block so the analyst can't miss them — they're
    HARD GUARDS that change the analytical regime (ST: ±5% limit;
    *ST: delisting process; 停牌: no price data).
    """
    if (not disclosures and not holders and not earnings_window
            and not is_st_flag and not is_suspended_flag):
        return ""

    parts: list[str] = []

    if is_suspended_flag:
        parts.append(
            "⛔ 停牌 (TRADING HALTED) — 이 종목은 현재 거래 정지 중이다."
            " yfinance 가격이 freeze 상태이며, 5거래일 분석은 'why halted'"
            " 정성 평가로 한정. 기술적 지표 / DCF / Comps 매핑 모두 의미"
            " 없음."
        )

    if is_st_flag:
        parts.append(
            "⚠️ ST/*ST 분류 — 이 종목은 2년 연속 적자(ST) 또는 3년 연속"
            " 적자(*ST)로 거래소의 특별처리 대상이다. 일일 변동 한도가"
            " 일반 ±10% 가 아닌 ±5% 로 제한되며, *ST 의 경우 退市 (delisting)"
            " 프로세스 진행 중. RULE 13 ST 가드에 따라 펀더멘털 / 결정"
            " 노드는 이를 명시적으로 인지 + 5거래일 분석에서 자본잠식 +"
            " 退市 timing 변수 추가."
        )

    if disclosures:
        parts.append(f"\n최근 公告 (AKShare, 최대 {len(disclosures)}건):")
        for d in disclosures:
            date_s = d.get("date") or "?"
            subject = (d.get("subject") or "").strip()
            if len(subject) > 100:
                subject = subject[:97] + "..."
            parts.append(f"  • {date_s}: {subject}")

    if holders:
        top = sorted(holders, key=lambda r: r.get("pct", 0), reverse=True)[:5]
        skipped = max(0, len(holders) - len(top))
        skipped_note = (
            f" — 총 {len(holders)}건 중 보유 비중 상위 {len(top)}만 표시"
            f" (나머지 {skipped}건은 minor stake, 신호 약함)"
            if skipped > 0 else ""
        )
        parts.append(
            f"\n主要 流通股东 / 지배주주 (상위 {len(top)}{skipped_note}):"
        )
        for r in top:
            name = r.get("name") or "?"
            pct = r.get("pct") or 0
            shares = r.get("shares") or 0
            # 股本性质 라벨: 国有股 / 境外法人股 / 境内自然人股 / etc.
            # US 'insider' 컨텍스트와 혼동 차단 — 茅台 2026-05-19 케이스:
            # 67% 가 中国贵州茅台酒厂集团 (国有股 = SOE 모회사) 인데
            # 'insider 62%' 라벨이 US 식 임원 지분처럼 읽힘.
            nature = (r.get("nature") or "").strip()
            nature_label = f" [{nature}]" if nature else ""
            parts.append(
                f"  • {name}{nature_label}: {pct:.2f}% ({shares:,} 주)"
            )

    if earnings_window:
        parts.append(f"\n다음 정기보고서 윈도: {earnings_window}")

    return "\n".join(parts)


def format_hsgt_flow_for_prompt(flow: Optional[dict]) -> str:
    """Render HSGT (港股通 + 沪股通 + 深股通) flow summary block."""
    if not flow:
        return ""
    parts: list[str] = ["港股通 / 沪股通 / 深股通 5거래일 净 buy (AKShare):"]
    nb = flow.get("northbound_5d")
    sb = flow.get("southbound_5d")
    if nb is not None:
        sign = "+" if nb > 0 else ""
        parts.append(
            f"  • Northbound (海外 → 本토): {sign}{nb:,.0f}억 元"
            f" — {flow.get('north_direction')}"
        )
    if sb is not None:
        sign = "+" if sb > 0 else ""
        parts.append(
            f"  • Southbound (本토 → HK): {sign}{sb:,.0f}억 HKD"
            f" — {flow.get('south_direction')}"
        )
    return "\n".join(parts)


def format_cn_macro_for_prompt(macro: dict) -> str:
    """Render CN macro indicators (LPR / CPI / PMI) for analyst prompt.

    Silent-skip 차단 (SMIC 688981.SS 2026-05-19 surfaced): 각 변수가
    None / nan 일 때 행을 생략하지 않고 '데이터 미수집' 명시. 분석가
    가 누락을 인지하지 못한 채 '나머지 변수만 인용'하는 패턴 방지.
    Macro 블록 자체가 비어있을 때만 빈 문자열 반환 (caller 가 섹션
    헤더 생략하도록).
    """
    if not macro:
        return ""
    parts: list[str] = ["CN 거시 지표 (AKShare 미러: PBoC / 国家统计局):"]
    lpr_1y = macro.get("lpr_1y")
    lpr_5y = macro.get("lpr_5y")
    cpi = macro.get("cpi_yoy")
    pmi = macro.get("pmi")

    if lpr_1y is not None:
        parts.append(f"  • LPR 1Y (대출우대금리): {lpr_1y:.2f}%")
    else:
        parts.append("  • LPR 1Y: 데이터 미수집 (이번 실행)")

    if lpr_5y is not None:
        parts.append(f"  • LPR 5Y (모기지 앵커 금리): {lpr_5y:.2f}%")
    else:
        parts.append("  • LPR 5Y: 데이터 미수집 (이번 실행)")

    if cpi is not None:
        parts.append(f"  • CPI YoY: {cpi:.2f}%")
    else:
        parts.append("  • CPI YoY: 데이터 미수집 (이번 실행)")

    if pmi is not None:
        parts.append(f"  • 제조 PMI: {pmi:.1f}")
    else:
        parts.append("  • 제조 PMI: 데이터 미수집 (이번 실행)")

    # 모든 변수가 미수집이면 빈 문자열 반환 (Rule A guard 가 별도
    # 'CN 매크로 데이터 미수집' 안내). 한 개라도 valid 면 block 출력.
    valid_count = sum(
        1 for v in (lpr_1y, lpr_5y, cpi, pmi) if v is not None
    )
    if valid_count == 0:
        return ""

    return "\n".join(parts)
