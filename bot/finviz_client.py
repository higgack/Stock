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
import threading as _threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.finviz_client")

_BASE = "https://finviz.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 브라우저 수준 헤더 (2026-06-10 — VM 데이터센터 IP 에서 Finviz 1차가
# 폴백으로 빠지던 건). 얇은 헤더(UA+Accept-Language 만)면 Finviz/앞단
# 앤티봇이 403/챌린지로 거른다. 실 브라우저 헤더 셋 + sec-ch-ua client
# hint 로 통과율 ↑. ⚠️ Accept-Encoding 은 **수동 설정 안 함** — httpx 가
# 설치된 코덱(gzip/deflate)만 자동 광고(브라우저처럼 br 광고했다가 못
# 풀면 깨짐 — VM 에 brotli 없을 수 있음).
_HEADERS = {
    "User-Agent": _UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# 차단/챌린지 페이지 시그니처 (200 인데 데이터 대신 안티봇 페이지인 경우 —
# 파싱 0행 으로 빠지기 전에 '차단'으로 구분해 진단 로그 + 재시도).
_BLOCK_MARKERS = ("access denied", "are you a robot", "/cdn-cgi/challenge",
                  "cf-browser-verification", "captcha", "request blocked",
                  "unusual traffic")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "finviz"
_CACHE_TTL_SEC = 5 * 60         # 5분 — market.html 재생성 주기와 일치(사용자
                                # 2026-06-10 '10분이 최선인가'). #195 헤더로 통과
                                # 확인됨 → 5분 스크랩도 단일 채널·캐시로 안전.
# S&P500 1y 주봉 폴백 — 6h 는 장중 stale(20:04 산출이 새벽까지 서빙,
# 사용자 2026-06-11) → 30분. 배치 다운로드 비용은 30분당 ~1분 수준 허용.
_FALLBACK_TTL_SEC = 30 * 60

# 위키/스크린너 universe 가 전부 실패할 때의 최후 코어 (~40 대형주) — 신고저
# 폴백이 빈 화면 안 되게 최소 보장(2026-06-10 VM '데이터 없음' 케이스).
_CORE_US = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B",
    "LLY", "JPM", "V", "XOM", "UNH", "MA", "JNJ", "PG", "HD", "COST", "ORCL",
    "MRK", "ABBV", "CVX", "KO", "PEP", "WMT", "BAC", "CRM", "AMD", "NFLX",
    "ADBE", "MCD", "WFC", "DIS", "CSCO", "INTC", "QCOM", "TXN", "IBM", "GE",
    "CAT", "BA", "PFE", "T", "VZ",
)


def _get(url: str) -> Optional[str]:
    """Finviz HTML fetch — 브라우저 헤더 + 최대 2회 시도(백오프). 차단/
    챌린지 페이지는 None 으로 반환해 폴백이 깨끗이 작동하게 + WARNING 진단.
    데이터센터 IP 403 vs 마크업변경(_BLOCK_MARKERS 부재 200)을 로그로 구분."""
    short = url.split("?")[0]
    try:
        import httpx
    except Exception:
        return None
    for attempt in range(2):
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=15,
                          follow_redirects=True)
            if r.status_code == 200 and r.text:
                head = r.text[:800].lower()
                if any(m in head for m in _BLOCK_MARKERS):
                    log.warning("finviz: %s → 200 but anti-bot/challenge page "
                                "(데이터센터 IP 차단 의심) — attempt %d", short, attempt + 1)
                else:
                    return r.text
            else:
                log.warning("finviz: %s → HTTP %d (attempt %d) — %s",
                            short, r.status_code, attempt + 1, r.text[:120])
        except Exception as exc:
            log.warning("finviz: fetch failed %s (attempt %d): %s",
                        short, attempt + 1, exc)
        if attempt == 0:
            time.sleep(1.2)   # 일시적 차단/혼잡 — 1회 백오프 후 재시도
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

# href 가 .ashx 폐기 + 클린 URL 로 바뀜 (2026-06-10 VM 로그: screener.ashx
# →301→screener). screener(.ashx)? + f=ind_ 만으로 매칭(v= 위치/유무 무관),
# 선행 도메인/경로 허용. 옛 .ashx 형태도 동시 매칭(.ashx)? 라 하위호환.
# 그룹1 = ind_ 슬러그(업종 클릭 → Finviz 해당 업종 종목 링크용, 사용자
# 2026-06-10 '한국처럼 업종 클릭하면 종목 보기'). 그룹2 = 업종명, 그룹3 = tail.
_GROUP_ROW_RE = re.compile(
    r'<a[^>]+href="[^"]*screener(?:\.ashx)?\?[^"]*f=(ind_[^"&]*)[^"]*"[^>]*>([^<]{2,60})</a>(.*?)</tr>',
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
    html = _get(f"{_BASE}/groups?g=industry&v=110&o=-change")
    if html:
        import html as _h
        for slug, name, tail in _GROUP_ROW_RE.findall(html):
            pcts = _PCT_RE.findall(tail)
            if not pcts:
                continue
            try:
                # v=110 행의 마지막 % 컬럼 = 당일 Change. 이름은 unescape
                # (Oil &amp; Gas → Oil & Gas) — 렌더가 다시 escape 하므로
                # 여기서 풀어둬야 이중이스케이프('&amp;amp;') 방지.
                # slug = Finviz f= 필터(업종 클릭 → 해당 업종 종목 링크).
                out["groups"].append(
                    {"name": _h.unescape(name.strip()), "pct": float(pcts[-1]),
                     "slug": slug})
            except ValueError:
                continue
        if not out["groups"]:
            # 200 인데 파싱 0행 = 마크업 변경 의심 — 차단(403)과 구분되는
            # 진단 로그 (VM journal 로 원인 즉시 판별, 2026-06-10).
            log.warning("finviz: groups HTML fetched but parsed 0 rows "
                        "(markup change?) — head: %r", html[:200])
    if not out["groups"]:
        # 2차: S&P500 을 GICS 세부업종으로 묶어 자체 산출 (~100 업종).
        out = _groups_fallback_computed()
    if not out.get("groups"):
        # 3차(최후): 11 섹터 ETF.
        out = _groups_fallback_etf()
    if out.get("groups"):
        out["groups"].sort(key=lambda g: g["pct"], reverse=True)
        _cache_write("groups.json", out)
    return out


def _sp500_industry_map() -> dict:
    """{ticker: GICS Sub-Industry} — 위키피디아 S&P500 표(universe 와 동일
    출처)에서 업종까지 파싱. 7일 캐시. yfinance .info 호출 0(표 1콜)."""
    c = _cached("sp500_industry.json", ttl=7 * 86400)
    if c:
        return c
    out: dict = {}
    try:
        import pandas as pd
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"})
        df = tables[0]
        for _, row in df.iterrows():
            sym = str(row.get("Symbol", "")).replace(".", "-").strip()
            ind = str(row.get("GICS Sub-Industry", "")).strip()
            if sym and ind and ind.lower() != "nan":
                out[sym] = ind
    except Exception as exc:
        log.warning("finviz: S&P500 industry map fetch failed: %s", exc)
    if out:
        _cache_write("sp500_industry.json", out)
    return out


def _groups_fallback_computed() -> dict:
    """2차 폴백 (사용자 2026-06-10 — Finviz 또 깨져도 11개로 추락 방지) —
    S&P500 을 GICS Sub-Industry 로 묶어 당일 등락 평균 산출. ~100 업종(11
    ETF 대비 ~9x granularity), **외부 사이트 의존 0**(위키 표 + yfinance
    벌크 — 둘 다 이미 쓰는 소스). 30분 캐시(벌크 다운로드 무거워)."""
    c = _cached("groups_computed.json", ttl=30 * 60)
    if c is not None:
        return c
    out: dict = {"groups": [], "ts": _now_label(),
                 "source": "S&P500 업종 산출(yfinance)"}
    imap = _sp500_industry_map()
    if not imap:
        return out
    try:
        import yfinance as yf
        from collections import defaultdict
        tickers = sorted(imap.keys())
        df = yf.download(tickers, period="2d", interval="1d",
                         group_by="ticker", threads=True,
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return out
        buckets: dict[str, list] = defaultdict(list)
        for tk in tickers:
            try:
                if tk not in df.columns.get_level_values(0):
                    continue
                closes = df[tk]["Close"].dropna()
                if len(closes) < 2:
                    continue
                last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                if prev > 0:
                    buckets[imap[tk]].append((last - prev) / prev * 100)
            except Exception:
                continue
        for ind, pcts in buckets.items():
            if len(pcts) >= 3:   # 1-2 종목 업종은 평균이 노이즈 — 제외
                out["groups"].append(
                    {"name": ind, "pct": round(sum(pcts) / len(pcts), 2)})
        log.info("finviz: computed industry fallback — %d industries from %d stocks",
                 len(out["groups"]), len(tickers))
        if out["groups"]:
            _cache_write("groups_computed.json", out)
    except Exception as exc:
        log.warning("finviz: computed industry fallback failed: %s", exc)
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


# ── 11 GICS 섹터 (메인 위젯 '간략' 버전 — 사용자 2026-06-10 '메인은 야후처럼
# 간략, 세부 페이지는 디테일'). yfinance 섹터 ETF, 무키·견고(스크랩 아님). ──
_SECTOR_KR = {
    "Technology": "기술", "Healthcare": "헬스케어", "Financials": "금융",
    "Consumer Discretionary": "자유소비재", "Consumer Staples": "필수소비재",
    "Industrials": "산업재", "Energy": "에너지", "Utilities": "유틸리티",
    "Materials": "소재", "Real Estate": "부동산", "Communication": "커뮤니케이션",
}


def fetch_sectors() -> dict:
    """미국 11 GICS 섹터 당일 등락 (yfinance 섹터 ETF, 한국어 라벨). 메인
    대시보드 위젯의 '간략' 버전 — Yahoo 의 섹터 개요처럼 11개만 깔끔히.
    무키·견고(스크랩 아님). 10분 캐시. (세부 140 업종은 /usindustry 페이지.)"""
    c = _cached("sectors.json")
    if c is not None:
        return c
    out: dict = {"groups": [], "ts": _now_label(), "source": "yfinance"}
    try:
        from bot.us_market_daily import _SECTOR_ETFS
        import yfinance as yf
        for name, tk in _SECTOR_ETFS.items():
            try:
                fi = yf.Ticker(tk).fast_info
                last = getattr(fi, "last_price", None)
                prev = getattr(fi, "previous_close", None)
                if last and prev and prev > 0:
                    out["groups"].append({
                        "name": _SECTOR_KR.get(name, name),
                        "pct": round((last - prev) / prev * 100, 2)})
            except Exception:
                continue
    except Exception as exc:
        log.warning("finviz: sectors fetch failed: %s", exc)
    if out["groups"]:
        _cache_write("sectors.json", out)
    return out


# ── L3 업종 (메인 위젯 — 사용자 2026-06-10 'L3 48개 버전, Finviz 세부는
# 개별 페이지'). 우리 스크리너 L3 분류 ~48개를 Finviz 144 업종명 키워드로
# 묶어 일별 등락 평균. 추가 fetch 0(이미 받은 144 그룹 재사용), 실제 Finviz
# 데이터 기반. 키워드는 Finviz 업종명(소문자) 부분일치 — 매칭 0이면 그 L3
# 생략(graceful). 한국어 표시명. ──
_L3_BUCKETS: list[tuple[str, list[str]]] = [
    # Industrials
    ("방산·우주", ["aerospace", "defense"]),
    ("항공", ["airlines"]),
    ("건축자재·제품", ["building products", "building materials"]),
    ("전기장비", ["electrical equipment"]),
    ("기계", ["machinery", "tools & accessories", "industrial distribution"]),
    ("운송·물류", ["trucking", "railroad", "integrated freight", "marine shipping"]),
    ("환경·폐기물", ["waste management", "pollution"]),
    ("산업서비스", ["specialty business services", "consulting", "staffing",
                "security & protection", "rental & leasing"]),
    # Health Care
    ("제약·바이오", ["drug manufacturers", "biotechnology", "pharmaceutical"]),
    ("의료기기", ["medical devices", "medical instruments", "diagnostics & research"]),
    ("의료서비스", ["healthcare plans", "medical care facilities",
                "health information"]),
    # Financials
    ("은행", ["banks"]),
    ("자본시장·자산운용", ["capital markets", "asset management", "financial data"]),
    ("소비자금융", ["credit services"]),
    ("보험", ["insurance"]),
    # Consumer Discretionary
    ("자동차", ["auto manufacturers", "auto parts", "recreational vehicles"]),
    ("의류·럭셔리", ["apparel", "luxury goods", "footwear"]),
    ("호텔·레저", ["restaurants", "lodging", "resorts & casinos",
              "travel services", "leisure"]),
    ("소매·유통", ["retail", "department stores"]),
    ("주택건설", ["residential construction"]),
    ("교육", ["education"]),
    # Consumer Staples
    ("음료", ["beverages"]),
    ("식품소매", ["grocery", "food distribution"]),
    ("식품·농산물", ["packaged foods", "farm products", "confectioner"]),
    ("생활·개인용품", ["household & personal products"]),
    ("담배", ["tobacco"]),
    # Energy
    ("석유·가스", ["oil & gas e&p", "oil & gas integrated", "oil & gas midstream",
              "oil & gas refining"]),
    ("에너지장비·서비스", ["oil & gas equipment", "oil & gas drilling",
                   "thermal coal", "uranium"]),
    # Basic Materials
    ("화학", ["chemicals"]),
    ("건자재·골재", ["building materials"]),
    ("포장·용기", ["packaging & containers"]),
    ("금속·광업", ["steel", "aluminum", "copper", "gold", "silver", "other industrial metals",
              "other precious metals", "coking coal"]),
    ("제지·임업", ["lumber", "paper & paper products"]),
    # Real Estate
    ("부동산서비스", ["real estate services", "real estate - development",
                "real estate - diversified"]),
    ("리츠(REITs)", ["reit"]),
    # Utilities
    ("전력유틸리티", ["utilities - regulated electric", "utilities - diversified"]),
    ("신재생·IPP", ["utilities - renewable", "utilities - independent power"]),
    ("가스·수도", ["utilities - regulated gas", "utilities - regulated water"]),
    # Communication Services
    ("인터랙티브미디어", ["internet content", "electronic gaming"]),
    ("엔터테인먼트", ["entertainment"]),
    ("통신서비스", ["telecom services"]),
    ("광고·출판", ["advertising agencies", "publishing", "broadcasting"]),
    # Technology
    ("소프트웨어", ["software"]),
    ("하드웨어·저장장치", ["computer hardware", "consumer electronics",
                  "communication equipment", "electronic components"]),
    ("반도체", ["semiconductor"]),
    ("IT서비스·핀테크", ["information technology services", "fintech"]),
]


def top_l3_movers(top_n: int = 10) -> dict:
    """메인 위젯용 — 우리 L3 ~48 업종 상/하위(부호 분리). Finviz 144 업종을
    _L3_BUCKETS 키워드로 묶어 일별 등락 평균. 세부 144는 /usindustry(top_
    movers→Finviz). 매칭 0인 L3 는 생략(graceful), 추가 fetch 0."""
    data = fetch_groups()
    groups = [g for g in data.get("groups", [])
              if g.get("pct") is not None and g.get("name")]
    if not groups:
        return {"up": [], "down": [], "ts": data.get("ts", ""),
                "source": data.get("source", "")}
    buckets: list[dict] = []
    for disp, kws in _L3_BUCKETS:
        pcts = [g["pct"] for g in groups
                if any(kw in g["name"].lower() for kw in kws)]
        if pcts:
            buckets.append({"name": disp, "pct": round(sum(pcts) / len(pcts), 2),
                            "n": len(pcts)})
    ups = [b for b in buckets if b["pct"] > 0]
    downs = [b for b in buckets if b["pct"] < 0]
    return {"up": sorted(ups, key=lambda b: b["pct"], reverse=True)[:top_n],
            "down": sorted(downs, key=lambda b: b["pct"])[:top_n],
            "ts": data.get("ts", ""), "source": data.get("source", "")}


def top_sector_movers(top_n: int = 10) -> dict:
    """11 GICS 섹터 상/하위(부호 분리). top_movers 와 동일 shape, 데이터만
    섹터(간략). (미사용 — 메인은 top_l3_movers, 세부는 top_movers.)"""
    data = fetch_sectors()
    gs = [g for g in data.get("groups", []) if g.get("pct") is not None]
    ups = [g for g in gs if g["pct"] > 0]
    downs = [g for g in gs if g["pct"] < 0]
    return {"up": sorted(ups, key=lambda g: g["pct"], reverse=True)[:top_n],
            "down": sorted(downs, key=lambda g: g["pct"])[:top_n],
            "ts": data.get("ts", ""), "source": data.get("source", "")}


def top_movers(top_n: int = 10) -> dict:
    """업종(industry, ~140) 등락 상/하위 → {'up','down','ts','source'} —
    세부 페이지(/usindustry)용. fetch_groups(Finviz→GICS산출→ETF) 기반.
    fetch_groups 의 정렬에 의존하지 않고 여기서 자체 정렬 (방어적).

    부호 필터 (2026-06-10 VM surfaced): 상승 칸은 pct>0, 하락 칸은 pct<0
    만 — 섹터 ETF 폴백(11개)처럼 모수가 top_n×2 보다 작으면 하락 칸이
    +값으로 채워져 거울처럼 보이던 것 차단. 칸이 10개 미만이어도 정직하게."""
    data = fetch_groups()
    gs = [g for g in data.get("groups", []) if g.get("pct") is not None]
    ups = [g for g in gs if g["pct"] > 0]
    downs = [g for g in gs if g["pct"] < 0]
    return {"up": sorted(ups, key=lambda g: g["pct"], reverse=True)[:top_n],
            "down": sorted(downs, key=lambda g: g["pct"])[:top_n],
            "ts": data.get("ts", ""), "source": data.get("source", "")}


# ── 52주 신고가 · 신저가 ──────────────────────────────────────────────────

# quote.ashx?t= → quote?t= (클린 URL). 앵커 텍스트==티커(>\1<) 제약은
# 리디자인에서 span 래핑 가능성 있어 제거 — href 의 t= 만 신뢰, 앵커 내부
# 텍스트는 [^<]* 로 소비(tail 이 다음 셀부터 시작 = 회사명). .ashx 하위호환.
_TICKER_CELL_RE = re.compile(
    r'<a[^>]+href="[^"]*quote(?:\.ashx)?\?t=([A-Z0-9.\-]+)[^"]*"[^>]*>.*?</a>(.*?)</tr>',
    re.DOTALL)
_CELL_TXT_RE = re.compile(r'<td[^>]*>(?:<[^>]+>)*([^<]*)')


def _parse_screener_rows(html: str, limit: int) -> list[dict]:
    import html as _h
    rows: list[dict] = []
    for tk, tail in _TICKER_CELL_RE.findall(html):
        cells = [c.strip() for c in _CELL_TXT_RE.findall(tail) if c.strip()]
        # v=111 컬럼: Company, Sector, Industry, Country, MktCap, P/E,
        # Price, Change, Volume — 위치 가변 대비 역방향 휴리스틱:
        # 마지막 % = Change, 그 앞 숫자 = Price, 첫 텍스트 = Company.
        name = _h.unescape(cells[0]) if cells else tk
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


def _fetch_signal(signal: str) -> list[dict]:
    """Finviz screener signal 전량 fetch — 20행/페이지 자동 페이지네이션."""
    rows: list[dict] = []
    offset = 1
    max_pages = 30  # 안전 상한 (600종목)
    for _ in range(max_pages):
        html = _get(f"{_BASE}/screener?v=111&s={signal}&o=-change&r={offset}")
        if not html:
            break
        page = _parse_screener_rows(html, 9999)
        if not page:
            if offset == 1:
                log.warning("finviz: %s HTML fetched but parsed 0 rows "
                            "(markup change?) — head: %r", signal, html[:200])
            break
        rows.extend(page)
        if len(page) < 20:
            break
        offset += 20
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
                 "ts": _now_label(), "source": "Finviz(전 미국 상장 · 당일 신고/신저)"}
    if not out["high"] and not out["low"]:
        log.info("finviz: high/low primary empty → S&P500 fallback")
        out = _highlow_fallback_sp500()
    log.info("finviz: high/low result — high=%d low=%d source=%s",
             len(out.get("high", [])), len(out.get("low", [])),
             out.get("source"))
    if out.get("high") or out.get("low"):
        _cache_write("highlow.json", out)
    return out


def _fetch_sp500_csv() -> tuple[list[str], dict]:
    """GitHub raw CSV → (티커 리스트, {티커: 회사명}). 캐시 없음(호출부가)."""
    try:
        import csv
        import io
        import httpx
        url = ("https://raw.githubusercontent.com/datasets/"
               "s-and-p-500-companies/main/data/constituents.csv")
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 200 and r.text:
            rows = list(csv.DictReader(io.StringIO(r.text)))
            tks: list[str] = []
            names: dict = {}
            for row in rows:
                sym = (row.get("Symbol") or row.get("symbol") or "").replace(".", "-").strip()
                if not sym:
                    continue
                tks.append(sym)
                nm = (row.get("Security") or row.get("Name")
                      or row.get("security") or row.get("name") or "").strip()
                if nm:
                    names[sym] = nm
            if len(tks) > 100:
                return tks, names
        log.warning("finviz: GitHub S&P500 CSV → HTTP %d", r.status_code)
    except Exception as exc:
        log.warning("finviz: GitHub S&P500 fetch failed: %s", exc)
    return [], {}


def _github_sp500() -> list[str]:
    """GitHub raw CSV 로 S&P500 티커 (위키 403 우회 — 2026-06-10 VM 확인:
    Wikipedia 가 데이터센터 IP 403). 7일 캐시. '.'→'-'(BRK.B→BRK-B).
    회사명 맵(sp500_names.json)도 같은 fetch 에서 캐시."""
    c = _cached("sp500_github.json", ttl=7 * 86400)
    if c:
        return c
    tks, names = _fetch_sp500_csv()
    if tks:
        _cache_write("sp500_github.json", tks)
        if names:
            _cache_write("sp500_names.json", names)
        log.info("finviz: GitHub S&P500 universe %d종목 (위키 우회)", len(tks))
    return tks


def _sp500_names() -> dict:
    """{티커: 회사명} — 신고가/신저가 폴백 표기용(사용자 2026-06-11
    'KO(Coca-Cola)'). 캐시 없으면 1회 fetch 해 양쪽 캐시 채움."""
    c = _cached("sp500_names.json", ttl=7 * 86400)
    if c:
        return c
    tks, names = _fetch_sp500_csv()
    if tks:
        _cache_write("sp500_github.json", tks)
    if names:
        _cache_write("sp500_names.json", names)
    return names or {}


def _us_universe_robust() -> list[str]:
    """US universe 다단 견고화 (2026-06-10 VM: 위키 403) — 위키(stock_
    screener) → GitHub CSV → GICS 맵 키 → 하드코딩 코어. 첫 성공값 사용."""
    try:
        from bot.stock_screener import _get_us_universe
        u = _get_us_universe() or []
        if u:
            return u
    except Exception:
        pass
    u = _github_sp500()
    if u:
        return u
    u = list(_sp500_industry_map().keys())
    if u:
        return u
    log.warning("finviz: universe 전 소스 실패 → 코어 %d종목", len(_CORE_US))
    return list(_CORE_US)


_HL_LOCK = _threading.Lock()
_HL_REFRESHING = False


def _kick_highlow_refresh() -> None:
    """백그라운드 1개만 — stampede 방지."""
    global _HL_REFRESHING
    with _HL_LOCK:
        if _HL_REFRESHING:
            return
        _HL_REFRESHING = True

    def _run():
        global _HL_REFRESHING
        try:
            _compute_highlow_sp500()
        except Exception as exc:
            log.warning("finviz: highlow 백그라운드 재계산 실패: %s", exc)
        finally:
            with _HL_LOCK:
                _HL_REFRESHING = False

    _threading.Thread(target=_run, daemon=True,
                      name="highlow-refresh").start()


def _highlow_fallback_sp500() -> dict:
    """Finviz 차단 시 — S&P 500 산출. **stale-while-revalidate**: 30분
    지난 캐시도 즉시 서빙 + 백그라운드 재계산 (동기 1분+ 배치가 페이지
    요청을 hang 시키던 것 차단 — 사용자 2026-06-11 '신고가 안 먹어').
    캐시가 아예 없을 때만 동기 계산(최초 1회)."""
    c = _cached("highlow_sp500.json", ttl=_FALLBACK_TTL_SEC)
    if c is not None:
        return c
    stale = _cached("highlow_sp500.json", ttl=86400)
    if stale is not None:
        _kick_highlow_refresh()
        return stale
    return _compute_highlow_sp500()


def _compute_highlow_sp500() -> dict:
    """S&P 500 1년 주봉 벌크로 52주 고저 1% 근접 산출 (무거움 — 배치 4-5회).

    2026-06-10 VM surfaced: 500종목 단일 yf.download 통째 실패 시 빈 결과
    → 120종목 배치 분할 + 배치별 try/except + 진단 로그."""
    out: dict = {"high": [], "low": [], "ts": _now_label(),
                 "source": "S&P 500 산출(yfinance · 52주 고저 1% 근접)"}
    try:
        import yfinance as yf
        universe = _us_universe_robust()
        _names = _sp500_names()  # 표기용 회사명(없으면 티커만)
        if not universe:
            log.warning("finviz: S&P500 fallback — universe empty (전 소스 실패)")
            return out
        log.info("finviz: S&P500 fallback — universe %d, 배치 다운로드 시작",
                 len(universe))
        _CHUNK = 120
        scanned = 0
        for ci in range(0, len(universe), _CHUNK):
            chunk = universe[ci:ci + _CHUNK]
            try:
                df = yf.download(chunk, period="1y", interval="1wk",
                                 group_by="ticker", threads=True,
                                 progress=False, auto_adjust=False)
            except Exception as exc:
                log.warning("finviz: S&P500 배치 %d 다운로드 실패: %s",
                            ci // _CHUNK + 1, exc)
                continue
            if df is None or df.empty:
                log.warning("finviz: S&P500 배치 %d 빈 응답", ci // _CHUNK + 1)
                continue
            for tk in chunk:
                try:
                    if tk not in df.columns.get_level_values(0):
                        continue
                    closes = df[tk]["Close"].dropna()
                    highs = df[tk]["High"].dropna()
                    lows = df[tk]["Low"].dropna()
                    if len(closes) < 10:
                        continue
                    scanned += 1
                    last = float(closes.iloc[-1])
                    hi, lo = float(highs.max()), float(lows.min())
                    if hi > 0 and last >= hi * 0.99:
                        out["high"].append({"ticker": tk,
                                            "name": _names.get(tk, tk),
                                            "price": round(last, 2), "pct": None})
                    elif lo > 0 and last <= lo * 1.01:
                        out["low"].append({"ticker": tk,
                                           "name": _names.get(tk, tk),
                                           "price": round(last, 2), "pct": None})
                except Exception:
                    continue
        # 캡 40/40 제거 (사용자 2026-06-11 '앞으로 채워지겠지') — 폴백도
        # 자기 universe(S&P 500) 안에서는 전량. 급락장 신저가 100+ 절단 방지.
        # 등락률 채우기 — 주봉 산출이라 일간 % 부재('—')였던 것(사용자
        # 2026-06-11). hit 종목만 5d 일봉 재다운로드로 당일 % 산출 — 캡
        # 해제로 hit 가 커질 수 있어 메인 스캔과 동일하게 배치 분할.
        hits = [r["ticker"] for r in out["high"] + out["low"]]
        by_tk = {r["ticker"]: r for r in out["high"] + out["low"]}
        for ci in range(0, len(hits), _CHUNK):
            chunk = hits[ci:ci + _CHUNK]
            try:
                dfd = yf.download(chunk, period="5d", interval="1d",
                                  group_by="ticker", threads=True,
                                  progress=False, auto_adjust=False)
            except Exception as exc:
                log.warning("finviz: fallback 등락률 배치 %d 실패: %s",
                            ci // _CHUNK + 1, exc)
                continue
            if dfd is None or dfd.empty:
                continue
            for tk in chunk:
                try:
                    if len(chunk) == 1:
                        closes = dfd["Close"].dropna()
                    else:
                        closes = dfd[tk]["Close"].dropna()
                    if len(closes) >= 2 and float(closes.iloc[-2]):
                        r = by_tk[tk]
                        r["pct"] = round((float(closes.iloc[-1])
                                          / float(closes.iloc[-2]) - 1) * 100, 2)
                        r["price"] = round(float(closes.iloc[-1]), 2)
                except Exception:
                    continue
        log.info("finviz: S&P500 fallback — scanned %d → high %d / low %d",
                 scanned, len(out["high"]), len(out["low"]))
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
