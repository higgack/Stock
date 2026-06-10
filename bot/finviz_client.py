"""Finviz 스크래퍼 — 미국 업종(industry) 등락 + 52주 신고가·신저가.

KR '업종 등락 TOP 10'(Naver upjong) + '상한가·하한가'의 미국 미러
(사용자 2026-06-10 — 미국엔 가격제한폭이 없으므로 신고가/신저가로 대체).
무료·무키·graceful. Finviz 차단/실패 시 키 없는 폴백:
  • 업종 → 11 섹터 ETF(yfinance) 등락
  • 신고가/신저가 → S&P 500 1년 주봉 벌크(yfinance)로 52주 고저 근접 산출
모든 표면은 데이터 부재 시 안내 문구로 우아하게 강등.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.finviz_client")

_BASE = "https://finviz.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "finviz"
_CACHE_TTL_SEC = 10 * 60        # 10분 — Naver(4분)보다 길게 (차단 회피)
_FALLBACK_TTL_SEC = 6 * 3600    # S&P500 1y 주봉 폴백은 무거워 6h


def _get(url: str) -> Optional[str]:
    try:
        import httpx
        r = httpx.get(url, headers={"User-Agent": _UA,
                                    "Accept-Language": "en-US,en;q=0.9"},
                      timeout=12, follow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
        log.warning("finviz: %s → HTTP %d", url.split("?")[0], r.status_code)
    except Exception as exc:
        log.warning("finviz: fetch failed %s: %s", url.split("?")[0], exc)
    return None


def _cached(name: str, ttl: float = _CACHE_TTL_SEC):
    f = _CACHE_DIR / name
    try:
        if f.exists() and time.time() - f.stat().st_mtime < ttl:
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _cache_write(name: str, obj) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / name).write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _now_label() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


# ── 업종(industry) 등락 ────────────────────────────────────────────────────

_GROUP_ROW_RE = re.compile(
    r'<a[^>]+href="screener\.ashx\?v=1[^"]*f=ind_[^"]*"[^>]*>([^<]{2,60})</a>(.*?)</tr>',
    re.DOTALL | re.IGNORECASE)
_PCT_RE = re.compile(r'([-+]?\d+\.\d+)%')


def fetch_groups() -> dict:
    """미국 업종별 당일 등락 → {'groups': [{name, pct}] 등락 내림차순,
    'ts', 'source'}. 1차 Finviz groups(industry ~140개), 실패 시 섹터 ETF
    11개(yfinance) 폴백. 10분 캐시."""
    c = _cached("groups.json")
    if c is not None:
        return c
    out: dict = {"groups": [], "ts": _now_label(), "source": "Finviz"}
    html = _get(f"{_BASE}/groups.ashx?g=industry&v=110&o=-change")
    if html:
        for name, tail in _GROUP_ROW_RE.findall(html):
            pcts = _PCT_RE.findall(tail)
            if not pcts:
                continue
            try:
                # v=110 행의 마지막 % 컬럼 = 당일 Change
                out["groups"].append(
                    {"name": name.strip(), "pct": float(pcts[-1])})
            except ValueError:
                continue
    if not out["groups"]:
        out = _groups_fallback_etf()
    if out.get("groups"):
        out["groups"].sort(key=lambda g: g["pct"], reverse=True)
        _cache_write("groups.json", out)
    return out


def _groups_fallback_etf() -> dict:
    """Finviz 차단 시 — 11 섹터 ETF 등락(yfinance fast_info)으로 강등."""
    out: dict = {"groups": [], "ts": _now_label(), "source": "섹터 ETF(yfinance)"}
    try:
        from bot.us_market_daily import _SECTOR_ETFS
        import yfinance as yf
        for name, tk in _SECTOR_ETFS.items():
            try:
                fi = yf.Ticker(tk).fast_info
                last = getattr(fi, "last_price", None)
                prev = getattr(fi, "previous_close", None)
                if last and prev and prev > 0:
                    out["groups"].append(
                        {"name": f"{name} ({tk})",
                         "pct": round((last - prev) / prev * 100, 2)})
            except Exception:
                continue
    except Exception as exc:
        log.warning("finviz: sector ETF fallback failed: %s", exc)
    return out


def top_movers(top_n: int = 10) -> dict:
    """업종 등락 상/하위 → {'up': [...], 'down': [...], 'ts', 'source'} —
    KR fetch_sector_movers 와 동일 shape (위젯 렌더 공유 목적).
    fetch_groups 의 정렬에 의존하지 않고 여기서 자체 정렬 (방어적)."""
    data = fetch_groups()
    gs = [g for g in data.get("groups", []) if g.get("pct") is not None]
    return {"up": sorted(gs, key=lambda g: g["pct"], reverse=True)[:top_n],
            "down": sorted(gs, key=lambda g: g["pct"])[:top_n],
            "ts": data.get("ts", ""), "source": data.get("source", "")}


# ── 52주 신고가 · 신저가 ──────────────────────────────────────────────────

_TICKER_CELL_RE = re.compile(
    r'<a[^>]+href="quote\.ashx\?t=([A-Z0-9.\-]+)[^"]*"[^>]*>\1</a>(.*?)</tr>',
    re.DOTALL)
_CELL_TXT_RE = re.compile(r'<td[^>]*>(?:<[^>]+>)*([^<]*)')


def _parse_screener_rows(html: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    for tk, tail in _TICKER_CELL_RE.findall(html):
        cells = [c.strip() for c in _CELL_TXT_RE.findall(tail) if c.strip()]
        # v=111 컬럼: Company, Sector, Industry, Country, MktCap, P/E,
        # Price, Change, Volume — 위치 가변 대비 역방향 휴리스틱:
        # 마지막 % = Change, 그 앞 숫자 = Price, 첫 텍스트 = Company.
        name = cells[0] if cells else tk
        pct = None
        price = None
        joined = " | ".join(cells)
        pcts = _PCT_RE.findall(joined)
        if pcts:
            try:
                pct = float(pcts[-1])
            except ValueError:
                pct = None
        m_price = re.findall(r'(?<![\d.%])(\d{1,5}\.\d{2})(?!\d*%)', joined)
        if m_price:
            try:
                price = float(m_price[-1])
            except ValueError:
                price = None
        rows.append({"ticker": tk, "name": name[:40], "price": price, "pct": pct})
        if len(rows) >= limit:
            break
    return rows


def _fetch_signal(signal: str, limit: int = 40) -> list[dict]:
    rows: list[dict] = []
    for offset in (1, 21):
        html = _get(f"{_BASE}/screener.ashx?v=111&s={signal}&o=-change&r={offset}")
        if not html:
            break
        page = _parse_screener_rows(html, limit - len(rows))
        if not page:
            break
        rows.extend(page)
        if len(rows) >= limit or len(page) < 20:
            break
    # dedupe (페이지 경계 중복 방어)
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            out.append(r)
    return out


def fetch_high_low() -> dict:
    """52주 신고가/신저가 → {'high': [...], 'low': [...], 'ts', 'source'}.
    1차 Finviz(ta_newhigh/ta_newlow — 전 미국 상장), 실패 시 S&P 500 1년
    주봉 벌크로 고저 1% 근접 종목 산출(yfinance, 6h 캐시). 10분 캐시."""
    c = _cached("highlow.json")
    if c is not None:
        return c
    out: dict = {"high": _fetch_signal("ta_newhigh"),
                 "low": _fetch_signal("ta_newlow"),
                 "ts": _now_label(), "source": "Finviz"}
    if not out["high"] and not out["low"]:
        out = _highlow_fallback_sp500()
    if out.get("high") or out.get("low"):
        _cache_write("highlow.json", out)
    return out


def _highlow_fallback_sp500() -> dict:
    """Finviz 차단 시 — S&P 500 1년 주봉 벌크로 52주 고저 1% 근접 산출.
    무겁기(500종목 1콜 ~20-40초)에 6h 별도 캐시."""
    c = _cached("highlow_sp500.json", ttl=_FALLBACK_TTL_SEC)
    if c is not None:
        return c
    out: dict = {"high": [], "low": [], "ts": _now_label(),
                 "source": "S&P 500 산출(yfinance)"}
    try:
        from bot.stock_screener import _get_us_universe
        import yfinance as yf
        universe = _get_us_universe()
        if not universe:
            return out
        df = yf.download(universe, period="1y", interval="1wk",
                         group_by="ticker", threads=True,
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return out
        for tk in universe:
            try:
                if tk not in df.columns.get_level_values(0):
                    continue
                closes = df[tk]["Close"].dropna()
                highs = df[tk]["High"].dropna()
                lows = df[tk]["Low"].dropna()
                if len(closes) < 10:
                    continue
                last = float(closes.iloc[-1])
                hi, lo = float(highs.max()), float(lows.min())
                if hi > 0 and last >= hi * 0.99:
                    out["high"].append({"ticker": tk, "name": tk,
                                        "price": round(last, 2), "pct": None})
                elif lo > 0 and last <= lo * 1.01:
                    out["low"].append({"ticker": tk, "name": tk,
                                       "price": round(last, 2), "pct": None})
            except Exception:
                continue
        out["high"] = out["high"][:40]
        out["low"] = out["low"][:40]
        if out["high"] or out["low"]:
            _cache_write("highlow_sp500.json", out)
    except Exception as exc:
        log.warning("finviz: S&P500 high/low fallback failed: %s", exc)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    g = fetch_groups()
    print(f"groups ({g.get('source')}): {len(g.get('groups', []))}")
    for row in g.get("groups", [])[:5]:
        print(" ", row)
    hl = fetch_high_low()
    print(f"high {len(hl.get('high', []))} / low {len(hl.get('low', []))} ({hl.get('source')})")
