"""companiesmarketcap.com 글로벌 시가총액 순위 스크레이퍼 — Market cap 대시보드.

사용자 2026-07-06: "이 사이트 자체를 카피 — 다이렉트 연결도 OK, 우리 대시보드에
이식". 표 = 글로벌 시총 Top(홈페이지 순위) 자체 렌더, Rank by 축(Earnings/
Revenue/PE…)은 사이트 해당 페이지로 다이렉트 링크(dashboard 렌더 쪽).

전략(견고성 순):
  1) CSV 다운로드 엔드포인트(?download=csv) — 로그인 불필요, 파싱 깔끔.
     컬럼: Rank,Name,Symbol,marketcap,price (USD),country
  2) HTML 테이블 폴백 — CSV 차단 시 행 정규식 파싱(오늘 등락 % 포함).
샌드박스 프록시는 403 이라 실검증은 VM(런타임) — 실패 시 WARNING 로그 +
빈 리스트(silent-fail 금지, 페이지는 실패 상태 문구 렌더). 3h 디스크 캐시
(마지막 성공분 보존 — 일시 차단에도 페이지는 최근 데이터 유지).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.marketcap")

_KST = timezone(timedelta(hours=9))
_URL = "https://companiesmarketcap.com/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_CACHE = Path.home() / ".tradingagents" / "marketcap_global.json"
_TTL = 3 * 3600


def _cache_read() -> dict:
    try:
        with open(_CACHE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _cache_write(payload: dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _CACHE)
    except Exception as exc:
        log.warning("marketcap cache write failed: %s", exc)


def _get(url: str) -> str:
    import requests
    r = requests.get(url, timeout=25, headers={
        "User-Agent": _UA, "Accept-Language": "en",
        "Referer": "https://companiesmarketcap.com/"})
    r.raise_for_status()
    return r.text


def _parse_csv(text: str) -> list[dict]:
    """CSV(?download=csv) → rows. 컬럼명 대소문자/변형 tolerant."""
    rows: list[dict] = []
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames:
        return []
    fmap = {k.lower().strip(): k for k in rdr.fieldnames}

    def _col(*names):
        for n in names:
            if n in fmap:
                return fmap[n]
        return None

    c_rank = _col("rank")
    c_name = _col("name")
    c_sym = _col("symbol", "ticker")
    c_mcap = _col("marketcap", "market cap", "marketcap (usd)")
    c_price = _col("price (usd)", "price")
    c_ctry = _col("country")
    if not (c_name and c_mcap):
        log.warning("marketcap csv: 예상 밖 헤더 %s", rdr.fieldnames)
        return []
    for r in rdr:
        try:
            mcap = float(str(r.get(c_mcap, "")).replace(",", "") or 0)
        except ValueError:
            continue
        if mcap <= 0:
            continue
        try:
            price = float(str(r.get(c_price, "")).replace(",", "") or 0)
        except ValueError:
            price = None
        rows.append({
            "rank": int(float(r.get(c_rank) or len(rows) + 1)),
            "name": (r.get(c_name) or "").strip(),
            "ticker": (r.get(c_sym) or "").strip() if c_sym else "",
            "mcap_usd": mcap,
            "price_usd": price,
            "chg_pct": None,          # CSV 에는 당일 등락 없음(HTML 폴백만)
            "country": (r.get(c_ctry) or "").strip() if c_ctry else "",
        })
    return rows


def _parse_html(html: str) -> list[dict]:
    """HTML 테이블 폴백 — 회사행(td) 정규식. 구조 변경 시 빈 리스트+경고."""
    rows: list[dict] = []
    # 회사명+티커 블록과 뒤따르는 td 들(시총/주가/등락/국가)을 행 단위로.
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        name_m = re.search(r'class="company-name"[^>]*>([^<]+)<', tr)
        if not name_m:
            continue
        code_m = re.search(r'class="company-code"[^>]*>([^<]+)<', tr)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        txt = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        mcap = price = chg = None
        country = ""
        for t in txt:
            if mcap is None and re.match(r"^\$[\d.,]+\s*[TBM]$", t):
                num = float(re.sub(r"[^\d.]", "", t))
                unit = {"T": 1e12, "B": 1e9, "M": 1e6}[t.strip()[-1]]
                mcap = num * unit
            elif price is None and re.match(r"^\$[\d.,]+$", t):
                price = float(re.sub(r"[^\d.]", "", t))
            elif chg is None and re.match(r"^-?[\d.]+%$", t):
                chg = float(t.rstrip("%"))
            elif t and re.match(r"^[A-Za-z .()]+$", t) and len(t) < 30:
                country = t
        if mcap is None:
            continue
        rows.append({"rank": len(rows) + 1,
                     "name": name_m.group(1).strip(),
                     "ticker": (code_m.group(1).strip() if code_m else ""),
                     "mcap_usd": mcap, "price_usd": price,
                     "chg_pct": chg, "country": country})
    if not rows:
        log.warning("marketcap html: 파싱 0행 — 사이트 구조 변경 의심")
    return rows


def fetch_top_companies(limit: int = 100) -> dict:
    """글로벌 시총 순위 {rows, fetched_at, source, stale}. 3h 캐시.

    실패 시 마지막 성공 캐시 rows 에 stale=True 로 반환(빈손 방지) —
    캐시도 없으면 rows=[]."""
    c = _cache_read()
    now = time.time()
    if c.get("rows") and now - (c.get("ts") or 0) < _TTL:
        return {"rows": c["rows"][:limit], "fetched_at": c.get("fetched_at", ""),
                "source": c.get("source", ""), "stale": False}
    rows: list[dict] = []
    source = ""
    try:
        rows = _parse_csv(_get(_URL + "?download=csv"))
        source = "csv"
    except Exception as exc:
        log.warning("marketcap csv fetch failed: %s — HTML 폴백", exc)
    if not rows:
        try:
            rows = _parse_html(_get(_URL))
            source = "html"
        except Exception as exc:
            log.warning("marketcap html fetch failed: %s", exc)
    if rows:
        rows.sort(key=lambda r: -(r["mcap_usd"] or 0))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        fetched_at = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
        _cache_write({"rows": rows, "ts": now, "fetched_at": fetched_at,
                      "source": source})
        return {"rows": rows[:limit], "fetched_at": fetched_at,
                "source": source, "stale": False}
    # 실패 — 마지막 성공분이라도 (stale 표기)
    if c.get("rows"):
        return {"rows": c["rows"][:limit], "fetched_at": c.get("fetched_at", ""),
                "source": c.get("source", ""), "stale": True}
    return {"rows": [], "fetched_at": "", "source": "", "stale": True}
