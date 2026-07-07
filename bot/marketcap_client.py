"""companiesmarketcap.com 순위 스크레이퍼 — Market cap 대시보드 (다축 임베드).

사용자 2026-07-06: "이 사이트 자체를 카피 — 우리 대시보드에 이식" +
2026-07-08: "원본 양식 그대로(로고·Today·30일 차트·국가) + Earnings/Revenue/
P/E ratio/MC gain/MC loss 도 Market Cap 처럼 박아서".

전략: 각 축의 **HTML 페이지를 원본 그대로 파싱**(로고 img·메트릭 셀 원문·
주가·Today %·30일 스파크라인 img·국가) — CSV 는 Today/차트가 없어 Market Cap
축의 최후 폴백만. 로고·스파크라인은 원본 이미지 URL 을 그대로 hot-link
(사설 대시보드 — 브라우저가 직접 원본에서 받아옴).

축당 3h 디스크 캐시(마지막 성공분 보존 — 일시 차단에도 최근 데이터 유지),
수집 실패는 WARNING 로그 + stale 플래그(silent-fail 금지). 축 간 1.5초
간격(예의상 rate-limit). 샌드박스 프록시는 403 이라 실검증은 VM 런타임.
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
_BASE = "https://companiesmarketcap.com/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_CACHE_DIR = Path.home() / ".tradingagents"
_TTL = 3 * 3600
_AXIS_GAP_SEC = 1.5     # 축 간 수집 간격(안티봇 예의)

# 임베드 축 (사용자 2026-07-08 지정 6축) — key, 필 라벨, slug, 메트릭 컬럼 라벨.
# slug 는 2026-07-06 웹검색 전수 검증분(추측 slug 금지).
EMBED_AXES = (
    ("marketcap", "Market Cap", "", "Market Cap"),
    ("earnings", "Earnings", "most-profitable-companies/", "Earnings"),
    ("revenue", "Revenue", "largest-companies-by-revenue/", "Revenue"),
    ("pe", "P/E ratio", "top-companies-by-pe-ratio/", "P/E ratio"),
    ("mc_gain", "MC gain", "top-companies-by-market-cap-gain/", "MC gain"),
    ("mc_loss", "MC loss", "top-companies-by-market-cap-loss/", "MC loss"),
)


def _cache_path(axis: str) -> Path:
    return _CACHE_DIR / f"marketcap_{axis}.json"


def _cache_read(axis: str) -> dict:
    try:
        with open(_cache_path(axis), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _cache_write(axis: str, payload: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path(axis).with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _cache_path(axis))
    except Exception as exc:
        log.warning("marketcap cache write failed (%s): %s", axis, exc)


def _get(url: str) -> str:
    import requests
    r = requests.get(url, timeout=25, headers={
        "User-Agent": _UA, "Accept-Language": "en",
        "Referer": _BASE})
    r.raise_for_status()
    return r.text


def _abs_url(src: str) -> str:
    """이미지 src → 절대 URL (원본 hot-link)."""
    s = (src or "").strip()
    if not s:
        return ""
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("/"):
        return _BASE.rstrip("/") + s
    return s


_MONEY_RE = re.compile(r"^-?\$[\d.,]+\s*[TBM]?$")          # $4.792 T / $197.85
_NUM_RE = re.compile(r"^-?[\d.,]+$")                        # 12.34 (P/E 등)
_PCT_RE = re.compile(r"^[▲▼+-]?\s*[\d.,]+%$")               # 1.18% / -1.60%


def _parse_rank_rows(html: str) -> list[dict]:
    """순위 페이지 HTML → 원본 양식 행 목록.

    행: {rank, name, ticker, logo, metric(원문), price(원문), chg_pct,
    chg_dir(+1/-1/0), spark(30일 차트 img URL), country(원문 'USA' 등)}.
    메트릭/주가는 **원문 그대로**(단위 재해석 없음 — 원본 양식 유지 + 파싱
    버그 원천 차단). 구조 변경 시 빈 리스트 + 경고."""
    rows: list[dict] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        name_m = re.search(r'class="company-name"[^>]*>([^<]+)<', tr)
        if not name_m:
            continue
        code_m = re.search(r'class="company-code"[^>]*>([^<]+)<', tr)
        _nm = name_m.group(1).strip()
        _cd = code_m.group(1).strip() if code_m else ""
        # 이미지 분류는 src 내용으로만(위치 추정 금지 — 2026-07-08 실화면서
        # 즐겨찾기 ★ 아이콘이 첫 img 라 로고 자리에 별이 찍힘): 로고 = 'logo'
        # 포함 src, 30일 차트 = 'spark'/'chart' 포함 src. 그 외(별/국기 등) 무시.
        imgs = re.findall(r'<img[^>]+(?:src|data-src)="([^"]+)"', tr)
        logo = spark = ""
        for src in imgs:
            low = src.lower()
            if not logo and "logo" in low:
                logo = _abs_url(src)
            elif not spark and re.search(r"spark|chart", low):
                spark = _abs_url(src)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        import html as _h
        txt = [re.sub(r"<[^>]+>", " ", t) for t in tds]
        # HTML 엔티티/NBSP 정규화 — 원본 시총 셀 '$4.792&nbsp;T' 가 매칭 안
        # 되면서 주가가 시총 컬럼으로 밀리던 실버그(2026-07-08 실화면).
        txt = [re.sub(r"\s+", " ", _h.unescape(t).replace(" ", " ")).strip()
               for t in txt]
        vals: list[str] = []          # 메트릭·주가류(원문) — 등장 순서 유지
        chg_pct = None
        chg_dir = 0
        country_cands: list[str] = []
        for i, t in enumerate(txt):
            if _MONEY_RE.match(t) or (_NUM_RE.match(t) and len(t) <= 12
                                      and t not in (str(len(rows) + 1),)
                                      and not re.fullmatch(r"\d{1,4}", t)):
                vals.append(t)
            elif chg_pct is None and _PCT_RE.match(t):
                try:
                    chg_pct = float(re.sub(r"[^\d.]", "", t))
                except ValueError:
                    continue
                low = tds[i].lower()
                if "-" in t or "▼" in t or "down" in low or "red" in low:
                    chg_dir = -1
                    chg_pct = -chg_pct
                else:
                    chg_dir = 1
            elif (t and len(t) < 30 and not re.search(r"[\d$%]", t)
                  and _nm not in t and (not _cd or _cd not in t)):
                country_cands.append(t)
        if not vals:                   # 메트릭 없는 행(헤더 등) skip
            continue
        metric, price = vals[0], (vals[1] if len(vals) >= 2 else "")
        # 값이 1개뿐이고 단위(T/B/M) 없는 $금액이면 십중팔구 '주가만 잡힌'
        # 행 — 시총 컬럼에 주가를 넣지 말고 주가 슬롯으로(재발 방지 가드).
        if not price and re.match(r"^-?\$[\d.,]+$", metric):
            metric, price = "", metric
        rows.append({
            "rank": len(rows) + 1,
            "name": _nm, "ticker": _cd, "logo": logo,
            "metric": metric,
            "price": price,
            "chg_pct": chg_pct, "chg_dir": chg_dir,
            "spark": spark,
            "country": country_cands[-1] if country_cands else "",
        })
    if not rows:
        log.warning("marketcap html: 파싱 0행 — 사이트 구조 변경 의심")
    return rows


def _parse_csv_fallback(text: str) -> list[dict]:
    """Market Cap 축 최후 폴백 — CSV(?download=csv). Today/차트/로고 없음."""
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
        except (ValueError, TypeError):
            continue
        if mcap <= 0:
            continue
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if mcap >= div:
                metric = f"${mcap / div:,.3f} {unit}"
                break
        else:
            metric = f"${mcap:,.0f}"
        _ps = str(r.get(c_price, "") or "").replace(",", "").strip() if c_price else ""
        try:
            price = f"${float(_ps):,.2f}" if _ps else ""
        except ValueError:
            price = ""
        rows.append({
            "rank": len(rows) + 1,
            "name": (r.get(c_name) or "").strip(),
            "ticker": (r.get(c_sym) or "").strip() if c_sym else "",
            "logo": "", "metric": metric, "price": price,
            "chg_pct": None, "chg_dir": 0, "spark": "",
            "country": (r.get(c_ctry) or "").strip() if c_ctry else "",
        })
    return rows


def fetch_axis(axis: str, slug: str, limit: int = 100) -> dict:
    """한 축 수집 {rows, fetched_at, source, stale}. 3h 캐시 · 실패 시 마지막
    성공분(stale=True)."""
    c = _cache_read(axis)
    now = time.time()
    if c.get("rows") and now - (c.get("ts") or 0) < _TTL:
        return {"rows": c["rows"][:limit], "fetched_at": c.get("fetched_at", ""),
                "source": c.get("source", ""), "stale": False}
    rows: list[dict] = []
    source = ""
    try:
        rows = _parse_rank_rows(_get(_BASE + slug))
        source = "html"
    except Exception as exc:
        log.warning("marketcap %s html fetch failed: %s", axis, exc)
    if not rows and axis == "marketcap":
        try:
            rows = _parse_csv_fallback(_get(_BASE + "?download=csv"))
            source = "csv"
        except Exception as exc:
            log.warning("marketcap csv fallback failed: %s", exc)
    if rows:
        fetched_at = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
        _cache_write(axis, {"rows": rows, "ts": now,
                            "fetched_at": fetched_at, "source": source})
        return {"rows": rows[:limit], "fetched_at": fetched_at,
                "source": source, "stale": False}
    if c.get("rows"):
        return {"rows": c["rows"][:limit], "fetched_at": c.get("fetched_at", ""),
                "source": c.get("source", ""), "stale": True}
    return {"rows": [], "fetched_at": "", "source": "", "stale": True}


def fetch_all_axes(limit: int = 100) -> dict:
    """임베드 6축 일괄 수집 {axis_key: {rows,...}}. 실수집(캐시 미스) 사이
    _AXIS_GAP_SEC 대기(안티봇 예의). graceful — 축별 독립 실패."""
    out: dict = {}
    for key, _lbl, slug, _mcol in EMBED_AXES:
        fresh_cache = (_cache_read(key).get("rows")
                       and time.time() - (_cache_read(key).get("ts") or 0) < _TTL)
        out[key] = fetch_axis(key, slug, limit)
        if not fresh_cache:
            time.sleep(_AXIS_GAP_SEC)
    return out


def fetch_top_companies(limit: int = 100) -> dict:
    """(하위호환) Market Cap 축 단독."""
    return fetch_axis("marketcap", "", limit)
