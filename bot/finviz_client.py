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


# ── yfinance 일시정지 kill-switch (사용자 2026-06-14 '야후 콜 멈춰봐 — 안 돌아와')
# 마커 파일 존재 = yfinance 부하 작업(52주/무버 스캔·시총·업종·실적 .calendar)
# 일괄 skip → 레이트리밋 회복. 봇 밖에서도 touch/rm 가능(TRADING_HALT 패턴).
# 파일이라 **재시작에도 유지** — 정지 중 재시작해도 스캔이 재발화 안 함(사용자
# 질문 '재시작하면 첨부터 다시 도나?' → 정지 마커 있으면 NO). 네이버 경로는
# yfinance 무관이라 정상 동작(무버·업종등락·신고저 표시숫자·홈 등). /yfpause 토글.
_YF_PAUSE_MARKER = Path.home() / ".tradingagents" / "YF_PAUSE"


def yf_paused() -> bool:
    """yfinance 호출 일시정지 상태(마커 파일 존재)? graceful False."""
    try:
        return _YF_PAUSE_MARKER.exists()
    except OSError:
        return False


def set_yf_pause(on: bool) -> bool:
    """yfinance 정지 토글 — 마커 생성/삭제. 반환 = 결과 상태(on=True)."""
    try:
        if on:
            _YF_PAUSE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _YF_PAUSE_MARKER.write_text(_now_label())
        elif _YF_PAUSE_MARKER.exists():
            _YF_PAUSE_MARKER.unlink()
    except OSError:
        pass
    return yf_paused()


# 네이버 정지 kill-switch (사용자 2026-06-14 '네이버도 야후처럼 끄고 키는 명령')
# — yf_paused 와 동형. 정지 시 네이버 HTTP fetch(국내·해외·업종·sector) skip →
# 안티봇 차단 회복. 캐시는 그대로 서빙. 마커라 재시작에도 유지. /naverpause 토글.
_NAVER_PAUSE_MARKER = Path.home() / ".tradingagents" / "NAVER_PAUSE"


def naver_paused() -> bool:
    """네이버 호출 일시정지 상태(마커 파일 존재)? graceful False."""
    try:
        return _NAVER_PAUSE_MARKER.exists()
    except OSError:
        return False


def set_naver_pause(on: bool) -> bool:
    """네이버 정지 토글 — 마커 생성/삭제. 반환 = 결과 상태(on=True)."""
    try:
        if on:
            _NAVER_PAUSE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _NAVER_PAUSE_MARKER.write_text(_now_label())
        elif _NAVER_PAUSE_MARKER.exists():
            _NAVER_PAUSE_MARKER.unlink()
    except OSError:
        pass
    return naver_paused()


# ── fast_info 회로차단기 (사용자 2026-06-14 /health: fast_info 만 IP 단위로
# 끈끈하게 rate-limit — 정지 중 회복→재개 즉시 재차단, 단일 콜도 실패). 1회라도
# rate-limit 에러가 나면 _FAST_INFO_COOLDOWN 동안 fast_info 전면 skip → download/
# Naver/persist 폴백, yahoo 가 회복하게 둠. 자동 적응(수동 정지 불요). 프로세스
# 내 메모리(재시작 시 리셋 — 첫 실패가 다시 trip). download(history API)·Naver 무관.
_FAST_INFO_COOLDOWN = 1800          # 30분
_FAST_INFO_COOLDOWN_UNTIL = 0.0


def fast_info_ok() -> bool:
    """fast_info 호출 가능?(회로차단 쿨다운 아님). source_health 진단은 우회(직접 yf)."""
    return time.time() >= _FAST_INFO_COOLDOWN_UNTIL


def fast_info_trip(reason: str = "") -> None:
    """fast_info rate-limit 감지 → 쿨다운 발동(전 소비처 skip)."""
    global _FAST_INFO_COOLDOWN_UNTIL
    _FAST_INFO_COOLDOWN_UNTIL = time.time() + _FAST_INFO_COOLDOWN
    log.warning("fast_info rate-limit → %ds 쿨다운 (%s)", _FAST_INFO_COOLDOWN, reason)


def is_rate_limit_error(exc) -> bool:
    """예외가 yfinance rate-limit 인가 (버전 무관 문자열·타입 매칭)."""
    s = str(exc)
    return ("Too Many Requests" in s or "Rate limit" in s
            or "RateLimit" in type(exc).__name__)


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
# 업종 행의 시가총액 셀 — v=110 의 Name 다음 첫 B/M 수치 ("16942.94B").
# P/E 등 뒤 컬럼엔 B/M suffix 가 없어 첫 매치 = Market Cap (시총가중용).
_MCAP_CELL_RE = re.compile(r'>\s*([\d,]+\.?\d*)\s*([BM])\s*<')


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
                row = {"name": _h.unescape(name.strip()),
                       "pct": float(pcts[-1]), "slug": slug}
                # 업종 시가총액 (십억$ 환산) — L3 시총가중 평균용 (사용자
                # 2026-06-11). 파싱 실패 시 생략(가중에서 단순평균 폴백).
                m = _MCAP_CELL_RE.search(tail)
                if m:
                    v = float(m.group(1).replace(",", ""))
                    row["mcap_b"] = v if m.group(2) == "B" else v / 1000.0
                out["groups"].append(row)
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
    if yf_paused() or not fast_info_ok():
        return out      # 회로차단/정지 — fast_info ETF 폴백 0콜(yahoo IP 냉각)
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
    if yf_paused() or not fast_info_ok():
        # 회로차단/정지 — fast_info ETF 0콜(yahoo IP 냉각) → stale 캐시/빈 서빙.
        # 자동 dashboard regen 이 쿨다운 중 yahoo 를 재차단하던 회복-차단 루프 차단.
        return _cached("sectors.json", ttl=86400) or out
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
    _L3_BUCKETS 키워드로 묶어 **업종 시가총액 가중 평균** (사용자 2026-06-11
    '시총가중방식'). 시총 파싱 안 된 소스(ETF/자체산출 폴백)는 단순평균
    폴백 + 라벨로 구분. 세부 144는 /usindustry. 매칭 0인 L3 는 생략."""
    data = fetch_groups()
    groups = [g for g in data.get("groups", [])
              if g.get("pct") is not None and g.get("name")]
    if not groups:
        return {"up": [], "down": [], "ts": data.get("ts", ""),
                "source": data.get("source", "")}
    buckets: list[dict] = []
    weighted_any = False
    for disp, kws in _L3_BUCKETS:
        rows = [g for g in groups
                if any(kw in g["name"].lower() for kw in kws)]
        if not rows:
            continue
        wsum = sum(g.get("mcap_b") or 0.0 for g in rows)
        if wsum > 0:
            pct = sum(g["pct"] * (g.get("mcap_b") or 0.0) for g in rows) / wsum
            weighted_any = True
        else:
            pct = sum(g["pct"] for g in rows) / len(rows)
        buckets.append({"name": disp, "pct": round(pct, 2), "n": len(rows)})
    ups = [b for b in buckets if b["pct"] > 0]
    downs = [b for b in buckets if b["pct"] < 0]
    src = data.get("source", "")
    src += " · L3 시총가중" if weighted_any else " · L3 단순평균"
    return {"up": sorted(ups, key=lambda b: b["pct"], reverse=True)[:top_n],
            "down": sorted(downs, key=lambda b: b["pct"])[:top_n],
            "ts": data.get("ts", ""), "source": src}


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
        # Volume(거래량, 사용자 2026-06-13) = 행 마지막 정수(콤마구분) 셀.
        # Change% 뒤 컬럼이라 rightmost. MktCap 은 'B/M' 접미사·Price 는
        # 소수라 미충돌. 큰 정수(5+자리)도 허용(콤마 없는 표기 대비).
        vol = None
        m_vol = re.findall(r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d.%])', joined)
        if m_vol:
            try:
                vol = int(m_vol[-1].replace(",", ""))
            except ValueError:
                vol = None
        rows.append({"ticker": tk, "name": name[:40], "price": price,
                     "pct": pct, "vol": vol})
        if len(rows) >= limit:
            break
    return rows


def _fetch_signal(signal: str) -> list[dict]:
    """Finviz screener signal 전량 fetch — 20행/페이지 자동 페이지네이션.

    URL 변형 폴백 (2026-06-11 진단): 같은 VM 에서 groups(clean URL)는
    성공하는데 screener 만 0행 — clean `/screener` 가 JS-렌더 신판으로
    응답할 수 있어, **레거시 `screener.ashx`(서버렌더 표·장기 안정)를
    1차**로, 실패 시 clean URL 을 2차로 시도. 첫 페이지에서 성공한
    변형을 나머지 페이지에 재사용."""
    rows: list[dict] = []
    offset = 1
    max_pages = 30  # 안전 상한 (600종목)
    path = None     # 첫 페이지에서 결정된 변형 ('screener.ashx' | 'screener')
    for _ in range(max_pages):
        page: list[dict] = []
        variants = (path,) if path else ("screener.ashx", "screener")
        for var in variants:
            html = _get(f"{_BASE}/{var}?v=111&s={signal}&o=-change&r={offset}")
            if not html:
                continue
            page = _parse_screener_rows(html, 9999)
            if page:
                if path is None:
                    path = var
                    log.info("finviz: %s — '%s' 변형으로 파싱 성공", signal, var)
                break
            if offset == 1:
                log.warning("finviz: %s '%s' HTML fetched but parsed 0 rows "
                            "(markup change?) — head: %r", signal, var, html[:200])
        if not page:
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


def _drop_amex_hl(d: dict) -> dict:
    """{high,low} dict 에서 AMEX(NYSE American) 종목 제거 — 모든 미국 대시보드에서
    AMEX 제외 (사용자 2026-06-17 'AMEX 포함 안 함'). Finviz 1차·전미국·S&P500 전
    티어 출력에 공통 적용(소스 무관 단일 시드). us_amex_symbols 빈 set(네트워크
    부재 등) 시 no-op — graceful(크래시 0)."""
    try:
        from bot.us_symbol_names import drop_amex_rows
    except Exception:
        return d
    if isinstance(d, dict):
        for k in ("high", "low"):
            if d.get(k):
                d[k] = drop_amex_rows(d[k])
    return d


def fetch_high_low(force: bool = False) -> dict:
    """52주 신고가/신저가 → {'high': [...], 'low': [...], 'ts', 'source'}.

    3-tier (사용자 2026-06-11 '전체 신고저' — Finviz 의존 제거):
      1차 Finviz(ta_newhigh/ta_newlow — 당일 신고/신저, 마크업 깨지면 0행)
      2차 전 미국 상장 산출(nasdaqtrader ~7천 + yfinance — 백그라운드
          전용, 첫 빌드 전엔 3차로 폴스루)
      3차 S&P 500 산출(빠른 최후 폴백)
    신선도 = 시장-인지(_session_fresh US, 장중 1h / 장 밖 마지막 마감 이후 재스캔 0
    — 사용자 2026-06-14 '미국 신고저도 장중 1h'). 옛 플랫 5분 대체.

    force=True 면 신선도 게이트를 건너뛰고 즉시 재산출(장중 시간대별 슬롯 스캔이
    1시간 경계의 sub-초 jitter 로 skip 되지 않게 — 사용자 2026-06-16 '내가 안
    들어가도 1시간단위로 최신'). 장 밖 슬롯은 force 없이 호출 → EOD 1회 후 freeze."""
    stale = _cached("highlow.json", ttl=86400)
    if not force and stale is not None:
        try:
            mt = (_CACHE_DIR / "highlow.json").stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh("US", mt, _HL_US_INTRA_TTL):   # US 30분(사용자 2026-06-19)
            # 업종 누락 행 치유 (stale 캐시가 ind 필드 도입 전이거나 .info
            # 실패로 비었던 경우 — 벌크 맵으로 채워지면 캐시도 갱신).
            if _backfill_industries(stale):
                _cache_write("highlow.json", stale)
                # ⚠️ 신선도 클록 보존 (사용자 2026-06-18 '왜 아직 22:00') — 업종 치유
                # rewrite 가 mtime 을 올리면 _session_fresh 가 영구 fresh → 진짜
                # 재산출(ts 갱신)이 1h 마다 발동 못 함(180초 워머가 매번 건드려 1h
                # 경계 못 넘김). 원래 mtime 복원 → 데이터 산출 시각 기준으로 신선도
                # 판정 → 1h 후 재산출 정상 발동(라벨 stale 22:00 고정 해소).
                try:
                    import os as _os
                    _os.utime(_CACHE_DIR / "highlow.json", (mt, mt))
                except OSError:
                    pass
            # AMEX 제외 (사용자 2026-06-17) — 옛 캐시(필터 이전)도 서빙 시점에 정제.
            return _drop_amex_hl(_prune_cached(stale, ("high", "low"), "highlow.json"))
    out: dict = {"high": _fetch_signal("ta_newhigh"),
                 "low": _fetch_signal("ta_newlow"),
                 "ts": _now_label(), "source": "Finviz(전 미국 상장 · 당일 신고/신저)"}
    if not out["high"] and not out["low"]:
        log.info("finviz: high/low primary empty → 전미국 산출 티어")
        out = _highlow_full_us(force=force)   # 장중 슬롯 force → 30분마다 전량 재산출(B′)
    if not out.get("high") and not out.get("low"):
        log.info("finviz: 전미국 캐시 미준비 → S&P500 폴백 (백그라운드 빌드 중)")
        out = _highlow_fallback_sp500()
    _backfill_industries(out)   # SWR stale 티어 캐시(ind 부재)도 렌더 전 치유
    out["high"] = prune_non_stock(out.get("high", []))   # CEF·유령·이중클래스
    out["low"] = prune_non_stock(out.get("low", []))     # (Finviz 1차 티어 포함)
    out = _drop_amex_hl(out)   # AMEX 제외 — 모든 미국 대시보드 (사용자 2026-06-17)
    log.info("finviz: high/low result — high=%d low=%d source=%s",
             len(out.get("high", [])), len(out.get("low", [])),
             out.get("source"))
    if out.get("high") or out.get("low"):
        _cache_write("highlow.json", out)
    return out


def _fetch_sp500_csv() -> tuple[list[str], dict, dict]:
    """GitHub raw CSV → (티커 리스트, {티커: 회사명}, {티커: GICS 업종}).
    캐시 없음(호출부가). 업종은 GICS Sub-Industry 우선(위키 미러 스키마),
    구 스키마면 Sector 컬럼 폴백 — 신고저 업종 매칭의 1콜 벌크 소스
    (2026-06-12, yfinance .info 가 VM 에서 비어 '업종 —' 였던 건)."""
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
            inds: dict = {}
            for row in rows:
                sym = (row.get("Symbol") or row.get("symbol") or "").replace(".", "-").strip()
                if not sym:
                    continue
                tks.append(sym)
                nm = (row.get("Security") or row.get("Name")
                      or row.get("security") or row.get("name") or "").strip()
                if nm:
                    names[sym] = nm
                ind = (row.get("GICS Sub-Industry") or row.get("GICS Sector")
                       or row.get("Sector") or row.get("sector") or "").strip()
                if ind and ind.lower() != "nan":
                    inds[sym] = ind
            if len(tks) > 100:
                return tks, names, inds
        log.warning("finviz: GitHub S&P500 CSV → HTTP %d", r.status_code)
    except Exception as exc:
        log.warning("finviz: GitHub S&P500 fetch failed: %s", exc)
    return [], {}, {}


def _github_sp500() -> list[str]:
    """GitHub raw CSV 로 S&P500 티커 (위키 403 우회 — 2026-06-10 VM 확인:
    Wikipedia 가 데이터센터 IP 403). 7일 캐시. '.'→'-'(BRK.B→BRK-B).
    회사명(sp500_names.json)·업종(sp500_inds_github.json) 맵도 같은 fetch."""
    c = _cached("sp500_github.json", ttl=7 * 86400)
    if c:
        return c
    tks, names, inds = _fetch_sp500_csv()
    if tks:
        _cache_write("sp500_github.json", tks)
        if names:
            _cache_write("sp500_names.json", names)
        if inds:
            _cache_write("sp500_inds_github.json", inds)
        log.info("finviz: GitHub S&P500 universe %d종목 (위키 우회)", len(tks))
    return tks


def _sp500_names() -> dict:
    """{티커: 회사명} — 신고가/신저가 폴백 표기용(사용자 2026-06-11
    'KO(Coca-Cola)'). 캐시 없으면 1회 fetch 해 캐시들 채움."""
    c = _cached("sp500_names.json", ttl=7 * 86400)
    if c:
        return c
    tks, names, inds = _fetch_sp500_csv()
    if tks:
        _cache_write("sp500_github.json", tks)
    if names:
        _cache_write("sp500_names.json", names)
    if inds:
        _cache_write("sp500_inds_github.json", inds)
    return names or {}


def _sp500_inds_github() -> dict:
    """{티커: GICS 업종} — GitHub S&P500 CSV (universe 와 같은 fetch, 1콜).
    7일 캐시. S&P500 멤버는 폴백 티어 전체를 100% 커버."""
    c = _cached("sp500_inds_github.json", ttl=7 * 86400)
    if c:
        return c
    tks, names, inds = _fetch_sp500_csv()
    if tks:
        _cache_write("sp500_github.json", tks)
    if names:
        _cache_write("sp500_names.json", names)
    if inds:
        _cache_write("sp500_inds_github.json", inds)
    return inds or {}


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
    """S&P 500 1년 일봉 벌크로 당일 52주 고저 갱신 산출 — 빠른 3차 폴백."""
    return _compute_highlow_from(
        _us_universe_robust(), _sp500_names(), "highlow_sp500.json",
        "S&P 500 산출(yfinance · 당일 52주 고저 갱신)", "S&P500")


def _compute_highlow_full_us() -> dict:
    """전 미국 상장(nasdaqtrader 공식 심볼 디렉토리 ~7천) 산출 — Finviz
    의존 없는 '전량' 2차 (사용자 2026-06-11 '전체 신고저'). 배치 ~60회·
    수 분 — 백그라운드 전용(_highlow_full_us 가 동기 호출 금지)."""
    tks, names = _us_full_universe()
    return _compute_highlow_from(
        tks, names, "highlow_full_us_v5.json",
        "전 미국 상장 산출(yfinance · 당일 52주 고저 갱신)", "전미국")


def _industries_for(tickers: list, market: str | None) -> dict:
    """업종 enrich — CN/JP/US/HK 는 네이버 업종맵(reutersIndustryName, reliable·
    fast_info 우회) 우선, 미스만 yfinance(GICS, breaker-gated 라 쿨다운 중 무호출).
    KR/TW 는 _fetch_industries. 사용자 2026-06-14 '홍콩도 다른것처럼 네이버+야후'
    — HK 를 yfinance-first 에서 Naver-first 로 통일(fast_info rate-limit 회피,
    미스는 yfinance 가 메움). HK 는 5자리↔4자리 zfill 정수 정규화."""
    # CN/JP/US — 네이버 업종맵 우선(reliable·fast_info 우회). US 도 네이버 USA
    # 업종(141개·VM probe 2026-06-14)으로 라우팅 → fast_info rate-limit 시에도
    # 업종 안 빔(미스만 _fetch_industries, breaker-gated 라 쿨다운 중 무호출).
    if market in ("CN_A", "JP", "US", "HK"):
        try:
            from bot.naver_ranking_client import world_industry_map
            m = world_industry_map(market)
            if m:
                # bare 6자리 코드 정규화 매칭 — HK(5↔4자리 zfill) + CN_A(T8
                # 2026-06-16): 업종맵 키는 _upjong_ticker(코드 6→.SS else .SZ
                # 휴리스틱)인데 movers/52w 티커는 _suffix_ticker(거래소 기준
                # .SS/.SZ)라, 상하이 9xx(B주) 등 휴리스틱이 빗나가는 코드는
                # 접미사 불일치로 직접 매칭 실패 → '업종 —'. CN 코드는 SH/SZ
                # 전역 고유라 bare-code 폴백 안전(충돌 0).
                norm: dict = {}
                if market in ("HK", "CN_A"):
                    for k, v in m.items():
                        c = str(k).split(".")[0]
                        if c.isdigit():
                            norm.setdefault(str(int(c)), v)

                def _look(tk, _m=m, _norm=norm):
                    v = _m.get(tk)
                    if v is None and _norm:     # 접미사 불일치 → bare 코드 폴백
                        c = str(tk).split(".")[0]
                        if c.isdigit():
                            v = _norm.get(str(int(c)))
                    return v

                got = {tk: _look(tk) for tk in tickers}
                miss = [tk for tk in tickers if not got.get(tk)]
                if miss:
                    yf = _fetch_industries(miss)
                    for tk in miss:
                        if yf.get(tk):
                            got[tk] = yf[tk]
                return got
        except Exception as exc:
            log.warning("naver 업종맵 (%s) → yfinance 폴백: %s", market, exc)
        return _fetch_industries(tickers)
    return _fetch_industries(tickers)


# ── 비-주식 가지치기 (CEF 펀드 vehicle · 유령티커 · 이중클래스 dedupe) ──────
# 52주 신고저·급등락·장전장후 는 '주식' 표면 — 운용사 펀드(폐쇄형 CEF)·상장폐지
# 유령티커·같은 회사 A/B주 중복을 솎아냄 (사용자 2026-06-14 'CEF·정지·이중클래스
# dedupe'). 유니버스(nasdaqtrader)가 ETF/SPAC/워런트/채권형은 이미 거르나 CEF 는
# 빠져나옴. enrich(시총·업종) 후 호출 — 순수·테스트. US 네이밍 기반이라 비-US
# (JP/HK CJK명)는 자연 무발화(no-op).
_CEF_INDUSTRY = "Trusts Except Educational Religious and Charitable"
_FUND_WORD_RE = re.compile(r"\b(Fund|Fd)\b", re.I)
_CLOSED_END_RE = re.compile(r"closed[- ]?end", re.I)
_INCOME_TRUST_RE = re.compile(r"\b(Income|Municipal)\s+Trust\b", re.I)
_CLASS_MARK_RE = re.compile(r"\b(?:Class|Cl)\s+[A-Z]\b", re.I)
_CLASS_STRIP_RE = re.compile(r"\s+(?:Class|Cl)\s+[A-Z]\b.*$", re.I)
# 보통주 아닌 파생/특수 증권 — 권리(Rights)·워런트·유닛·우선주·CVR(Contingent
# Value Rights). 무버/신고저는 보통주만(사용자 2026-06-15 'LGL Rights·M3 Brigade
# Units 같은 종목 아닌 것들 빼'). \b 워드바운더리로 Wright/United/Bright 오탐 차단.
_NON_COMMON_RE = re.compile(
    r"\b(rights?|warrants?|units?|preferred)\b|contingent\s+value", re.I)


def _is_noncommon_security(name) -> bool:
    """이름이 권리/워런트/유닛/우선주/CVR 이면 True(보통주 아님). 순수.
    CJK명(JP/HK/CN)·정상 보통주는 무발화(no-op)."""
    return bool(_NON_COMMON_RE.search(name or ""))


def _is_fund_vehicle(name, ind) -> bool:
    """폐쇄형 펀드(CEF) 등 펀드 vehicle 인가 — 신호: 업종=나스닥 CEF 분류코드 OR
    종목명 Fund/Fd/Closed-End OR (Income/Municipal Trust + 비-부동산 업종 — OPI 류
    REIT 오탐 방지). 순수. Northern Trust(은행)·TrustCo·REIT 는 'Trust' 만으론 안
    잡힘(Fund/Fd/Closed-End 단어 또는 CEF 업종코드 필수)."""
    n = name or ""
    i = ind or ""
    if i == _CEF_INDUSTRY:
        return True
    if _FUND_WORD_RE.search(n) or _CLOSED_END_RE.search(n):
        return True
    if _INCOME_TRUST_RE.search(n) and "Real Estate" not in i and "REIT" not in i:
        return True
    return False


def _is_frozen_row(r: dict) -> bool:
    """유령/정지 티커 — 시총·업종·거래량 삼중 공백(yfinance 회사정보 전무 →
    상장폐지/장기정지, SVA 시노백 류). 순수. enrich 후만 호출(전부 None 단계 금지)."""
    return (r.get("mcap") is None and r.get("ind") is None
            and r.get("vol") is None)


def _class_base(name):
    """'X Class A'/'X Cl B' → 'x' (이중클래스 dedupe 키, 소문자). 순수."""
    if not name:
        return None
    base = _CLASS_STRIP_RE.sub("", name).strip().lower()
    return base or None


def _dedupe_dual_class(rows: list) -> list:
    """같은 회사 A/B주 중복 제거 — 유동성(거래량) 큰 쪽 1개만(없으면 시총).
    CENT/CENTA·SENEA/SENEB 류. class-strip 동명 그룹 중 'Class/Cl X' 마커가
    하나라도 있어야 dual-class 로 간주(우연 동명 비-클래스 그룹은 보존). 첫 등장
    위치에 best 1개 삽입, 순서 보존. 순수."""
    groups: dict = {}
    for idx, r in enumerate(rows):
        groups.setdefault(_class_base(r.get("name")), []).append(idx)
    drop: set = set()
    for b, idxs in groups.items():
        if b is None or len(idxs) < 2:
            continue
        if not any(_CLASS_MARK_RE.search(rows[i].get("name") or "") for i in idxs):
            continue
        best_i = max(idxs, key=lambda i: ((rows[i].get("vol") or 0),
                                          (rows[i].get("mcap") or 0)))
        drop.update(i for i in idxs if i != best_i)
    return [r for i, r in enumerate(rows) if i not in drop]


def prune_non_stock(rows: list) -> list:
    """CEF 펀드 vehicle + 유령(삼중 공백) + 비보통주(권리·워런트·유닛·우선주·
    CVR) 제거 후 이중클래스 dedupe — enrich 후 호출(시총·업종·거래량 채워진
    상태). 신고저·무버·장전장후 공용. 순수."""
    kept = [r for r in rows
            if not _is_fund_vehicle(r.get("name"), r.get("ind"))
            and not _is_noncommon_security(r.get("name"))
            and not _is_frozen_row(r)]
    return _dedupe_dual_class(kept)


def _prune_cached(data, keys, cache_name):
    """stale 캐시 serve-time 가지치기(멱등) — 가지치기 도입 전 옛 캐시도 렌더 전
    즉시 정리(주말 stale 에도 적용, 사용자 2026-06-14 '바로 보이게'). 변하면
    재캐시. keys=('high','low')|('up','down'). _backfill_industries 동류 패턴."""
    if not isinstance(data, dict):
        return data
    changed = False
    for k in keys:
        rows = data.get(k)
        if isinstance(rows, list) and rows:
            pruned = prune_non_stock(rows)
            if len(pruned) != len(rows):
                data[k] = pruned
                changed = True
    if changed:
        try:
            _cache_write(cache_name, data)
        except Exception:
            pass
    return data


def _compute_highlow_from(universe: list, names: dict, cache_name: str,
                          source: str, tag: str) -> dict:
    """1년 일봉 벌크로 **당일 52주 고저 갱신**(진짜 신고가/신저가) 산출 (universe 일반화).

    2026-06-10 VM surfaced: 단일 yf.download 통째 실패 시 빈 결과
    → 120종목 배치 분할 + 배치별 try/except + 진단 로그."""
    out: dict = {"high": [], "low": [], "ts": _now_label(), "source": source}
    if yf_paused():
        log.info("finviz: %s highlow — yfinance 정지(YF_PAUSE) → 스캔 skip, 캐시 유지", tag)
        return _cached(cache_name, ttl=86400) or out
    try:
        import yfinance as yf
        _names = names or {}
        if not universe:
            log.warning("finviz: %s highlow — universe empty", tag)
            return out
        log.info("finviz: %s highlow — universe %d, 배치 다운로드 시작",
                 tag, len(universe))
        _CHUNK = 120
        scanned = 0
        _seen: set = set()   # yfinance 가 데이터를 반환한 티커 (벌크 누락 재시도 판별)
        _baseline: dict = {}  # {tk:{h52,l52,name}} 오늘 제외 52주 고저 — 네이버 live 비교용

        # 일봉 1년 — **당일 intraday 고가/저가가 직전 251일 극값을 갱신**한 종목만
        # (진짜 52주 신고/신저, 사용자 2026-06-13 '1% 근접 말고 진짜'). 장중 한 번
        # 갱신하면 종가 무관 신고가 = 시장 통용 정의(사용자 2026-06-16 재확정).
        def _proc(df, chunk) -> None:
            """한 배치 df → 신고/신저 판정·append. 단일 티커는 yfinance 가 flat 컬럼
            을 줘 멀티레벨 분기. yfinance 가 준 티커만 _seen 에 기록(누락=재시도 대상)."""
            nonlocal scanned
            multi = getattr(df.columns, "nlevels", 1) > 1
            lv0 = set(df.columns.get_level_values(0)) if multi else set()
            for tk in chunk:
                try:
                    if multi:
                        if tk not in lv0:
                            continue            # yfinance 가 이 티커를 안 줌(drop) → 재시도
                        sub = df[tk]
                    else:
                        sub = df                # 단일 티커 flat 컬럼
                    _seen.add(tk)
                    closes = sub["Close"].dropna()
                    highs = sub["High"].dropna()
                    lows = sub["Low"].dropna()
                    if len(closes) < 20 or len(highs) < 2 or len(lows) < 2:
                        continue
                    scanned += 1
                    last = float(closes.iloc[-1])
                    prev = float(closes.iloc[-2])
                    pct = round((last / prev - 1) * 100, 2) if prev > 0 else None
                    vols = sub["Volume"].dropna()
                    vol = int(float(vols.iloc[-1])) if len(vols) else None
                    # 비거래(거래량 0/None) 제외 — HK ADR/HDR·휴면 종목이 평평한
                    # 가격으로 거짓 52주 고저에 잡히는 것 차단(사용자 2026-06-14).
                    if not vol:
                        continue
                    value = round(last * vol / 1e8, 2)    # 거래대금 ≈ 종가×거래량(억)
                    rec = {"ticker": tk, "name": _names.get(tk, tk),
                           "price": round(last, 2), "pct": pct,
                           "vol": vol, "value": value}
                    # 당일 intraday 고가/저가가 직전 251일 극값 갱신(동률 포함) = 신고/신저
                    # (사용자 2026-06-16 — 장중 한 번이라도 찍으면 종가 무관, 시장 통용 정의).
                    h52 = float(highs.iloc[:-1].max())   # 오늘 제외 52주 최고(baseline)
                    l52 = float(lows.iloc[:-1].min())     # 오늘 제외 52주 최저(baseline)
                    _baseline[tk] = {"h52": round(h52, 4), "l52": round(l52, 4),
                                     "name": _names.get(tk, tk)}
                    if float(highs.iloc[-1]) >= h52:
                        out["high"].append(rec)
                    elif float(lows.iloc[-1]) <= l52:
                        out["low"].append(rec)
                except Exception:
                    continue

        for ci in range(0, len(universe), _CHUNK):
            chunk = universe[ci:ci + _CHUNK]
            try:
                df = yf.download(chunk, period="1y", interval="1d",
                                 group_by="ticker", threads=True,
                                 progress=False, auto_adjust=False)
            except Exception as exc:
                log.warning("finviz: %s 배치 %d 다운로드 실패: %s",
                            tag, ci // _CHUNK + 1, exc)
                continue
            if df is None or df.empty:
                log.warning("finviz: %s 배치 %d 빈 응답", tag, ci // _CHUNK + 1)
                continue
            time.sleep(0.2)   # 대형 universe 벌크 사이 호흡 (yfinance 보호)
            _proc(df, chunk)

        # 벌크 누락 재시도 (사용자 2026-06-19 '키옥시아 같은 미싱') — yfinance batch 가
        # 일부 티커를 통째 드롭(285A 단일 다운로드는 성공·flag True 인데 120-배치에선
        # 빠져 신고가 보드에서 누락)하는 간헐 현상. 누락분만 작은 배치(40)로 **1회**
        # 재시도 → 미싱 방지. 누락은 통상 소수라 부하 bound(전수 재스캔 아님).
        dropped = [t for t in universe if t not in _seen]
        if dropped:
            log.info("finviz: %s highlow — 벌크 누락 %d종목 작은 배치 재시도",
                     tag, len(dropped))
            for ci in range(0, len(dropped), 40):
                sub = dropped[ci:ci + 40]
                try:
                    df = yf.download(sub, period="1y", interval="1d",
                                     group_by="ticker", threads=True,
                                     progress=False, auto_adjust=False)
                except Exception:
                    continue
                if df is None or df.empty:
                    continue
                time.sleep(0.3)
                _proc(df, sub)
        # SPAC 신탁가 백스톱 — 2026-06-12 보강: 옛 밴드($9.5~10.6·|pct|
        # <0.05%)가 신탁 이자 드리프트(+0.1~0.3%)와 $10.68 류를 통과시킴
        # (ALDF/CEPV/NOEM 실사례). 밴드 $9.4~11.0 + |pct|<1.0% 로 확대,
        # 신저가에도 적용(평평한 신탁가는 52주 저가 1% 이내이기도 함 —
        # BDCI/CEPS 실사례). 진짜 소형주가 $10 부근에서 <1% 움직이며
        # 신고/신저 찍는 희귀 케이스를 잃는 비용 < SPAC 무더기 노이즈.
        def _spac_band(r: dict) -> bool:
            return (9.4 <= (r.get("price") or 0) <= 11.0
                    and abs(r.get("pct") or 0) < 1.0)
        out["high"] = [r for r in out["high"] if not _spac_band(r)]
        out["low"] = [r for r in out["low"] if not _spac_band(r)]
        # 분할 미조정 아티팩트 드랍 — 일봉 리필의 |당일 등락|>75% 는 무제한
        # 시장이라도 분할/조정 불일치 신호(KLAC +1029% 2026-06-12 실사례,
        # 전일 $213→당일 $2,411 물리 불가). 가격·시총 신뢰 불가 → 행 제외
        # (CLAUDE.md price-glitch 가드 클래스).
        def _split_artifact(r: dict) -> bool:
            p = r.get("pct")
            if p is not None and abs(p) > 75.0:
                log.warning("finviz: %s highlow 분할 아티팩트 의심 드랍 — "
                            "%s pct=%+.1f%%", tag, r.get("ticker"), p)
                return True
            return False
        out["high"] = [r for r in out["high"] if not _split_artifact(r)]
        out["low"] = [r for r in out["low"] if not _split_artifact(r)]
        # 시가총액 — hit 종목만 fast_info 병렬 fetch (백그라운드 산출이라
        # 페이지 hang 0). 종목옆 시총 표시용 (사용자 2026-06-11). 억$ 단위.
        # ⚠️ HK/JP/US/CN 은 아래 _naver_worldstock_overlay 가 시총을 네이버로
        # 채우므로 fast_info 호출이 **낭비** — 게다가 fast_info(quote API)는 yahoo
        # 가 자주 rate-limit(2026-06-14 /health: download OK, fast_info 만 Too Many
        # Requests). US/CN 도 worldstock 시총 확인(VM probe 2026-06-14: NASDAQ
        # 엔비디아·NYSE TSMC·상하이·선전 marketValue ✓) → fast_info 생략(레이트리밋
        # 트리거 0). TW 만 worldstock 미지원이라 _fetch_mcaps 유지(디스크 persist 폴백).
        hits2 = [r["ticker"] for r in out["high"] + out["low"]]
        mcaps = {} if tag in ("HK", "JP", "US", "CN_A") else _fetch_mcaps(hits2)
        inds = _industries_for(hits2, tag)   # CN/HK/JP=네이버 업종, 그외 GICS/yfinance
        for r in out["high"] + out["low"]:
            mc = mcaps.get(r["ticker"])
            r["mcap"] = round(mc / 1e8, 2) if mc else None  # 억$
            r["ind"] = inds.get(r["ticker"])
        # 종목명 한글 (사용자 2026-06-13 '한국·미국 제외 모든 나라 괄호 한국어')
        # — 공용 헬퍼. 옛 코드는 (1) `market` 미정의(파라미터는 tag) NameError 로
        # 번역+cache_write 까지 통째로 건너뛰고 (2) name==ticker 일 때만 번역해
        # JP 銘柄名·TWSE 약칭(南亞科 류)을 스킵했음 — 헬퍼가 둘 다 해소(네이티브명
        # 직접 번역). US/KR 은 헬퍼가 no-op (영문/pykrx 한글 유지).
        _backfill_korean_names(out["high"] + out["low"], tag)
        # HK/JP/US/CN — 거래량/거래대금/시총을 **네이버 worldstock** 으로 채움
        # (사용자 2026-06-14). yfinance vol/시총 빈약·fast_info rate-limit 보완.
        # 52주 검출은 yfinance download 유지(네이버 52주 sort 부재), 표시 필드만
        # 네이버 overlay. US/CN 은 종목명을 기존 파이프라인(한글 quote_map·번역)
        # 보존(overlay_name=False) — HK/JP 는 worldstock 명 유지.
        if tag in ("HK", "JP"):
            _naver_worldstock_overlay(out["high"] + out["low"], tag)
        elif tag in ("US", "CN_A"):
            _naver_worldstock_overlay(out["high"] + out["low"], tag,
                                      overlay_name=False)
        # TW — 네이버 worldstock 미지원 → 시총만 디스크 persist(yfinance 일시
        # 장애 시 직전 성공값 유지, 사용자 2026-06-14 'TW 52주 시총 안 떠').
        elif tag == "TW":
            _persist_mcap_overlay(out["high"] + out["low"], "TW")
        # 비-주식 가지치기 — CEF 펀드·유령티커 제거 + 이중클래스 dedupe (enrich 후,
        # 사용자 2026-06-14). 시총·업종·거래량 채워진 상태라 정확. 비-US 는 무발화.
        out["high"] = prune_non_stock(out["high"])
        out["low"] = prune_non_stock(out["low"])
        log.info("finviz: %s highlow — scanned %d → high %d / low %d (mcap %d)",
                 tag, scanned, len(out["high"]), len(out["low"]), len(mcaps))
        if out["high"] or out["low"]:
            _cache_write(cache_name, out)
        # 네이버 live 비교용 baseline 저장(오늘 제외 52주 고저, 전 스캔 유니버스).
        # JP/CN_A/HK intl_highlow live 경로가 현재가와 비교(EOD 1회 산출 → 장중 저부하).
        if _baseline:
            try:
                _cache_write(f"highlow_baseline_{tag}.json", _baseline)
            except Exception as _bexc:
                log.warning("finviz: %s baseline 저장 실패: %s", tag, _bexc)
    except Exception as exc:
        log.warning("finviz: %s highlow 산출 실패: %s", tag, exc)
    return out


def _compute_movers_from(universe: list, names: dict, cache_name: str,
                         source: str, market: str, top_n: int = 30) -> dict:
    """급등/급락 TOP — 유니버스 일봉 당일 등락률 상·하위 top_n (HK 무제한 시장,
    사용자 2026-06-13 '홍콩도 미국처럼'). _compute_highlow_from 의 일봉/백필 패턴
    재사용. {up, down, ts, source, scanned}. 분할 아티팩트(|등락|>75%) 드롭.
    mcap/업종/한글명 백필(상·하위 hit 만). 백그라운드 산출 — 종목 적은 hit 만 enrich."""
    out: dict = {"up": [], "down": [], "ts": _now_label(), "source": source,
                 "scanned": 0}
    if yf_paused():
        log.info("finviz: %s movers — yfinance 정지(YF_PAUSE) → 스캔 skip, 캐시 유지", market)
        return _cached(cache_name, ttl=86400) or out
    try:
        _CHUNK = 120
        rows: list = []
        for ci in range(0, len(universe), _CHUNK):
            chunk = universe[ci:ci + _CHUNK]
            try:
                dfd = yf.download(chunk, period="5d", interval="1d",
                                  group_by="ticker", threads=True,
                                  progress=False, auto_adjust=False)
            except Exception:
                continue
            if dfd is None or dfd.empty:
                continue
            time.sleep(0.2)
            for tk in chunk:
                try:
                    sub = dfd if len(chunk) == 1 else dfd[tk]
                    closes = sub["Close"].dropna()
                    if len(closes) < 2:
                        continue
                    prev, last = float(closes.iloc[-2]), float(closes.iloc[-1])
                    if prev <= 0 or last <= 0:
                        continue
                    pct = round((last / prev - 1) * 100, 2)
                    if abs(pct) > 75.0:        # 분할/조정 아티팩트 드롭
                        continue
                    vols = sub["Volume"].dropna()
                    vol = int(float(vols.iloc[-1])) if len(vols) else None
                    if not vol:                # 비거래(ADR/HDR·휴면) 제외 (사용자
                        continue               # 2026-06-14 '홍콩 미국주식 ADR 제거')
                    rows.append({"ticker": tk, "name": names.get(tk, tk),
                                 "price": round(last, 2), "pct": pct, "vol": vol})
                except Exception:
                    continue
        out["scanned"] = len(rows)
        ups = sorted([r for r in rows if r["pct"] > 0],
                     key=lambda r: r["pct"], reverse=True)[:top_n]
        downs = sorted([r for r in rows if r["pct"] < 0],
                       key=lambda r: r["pct"])[:top_n]
        hits = [r["ticker"] for r in ups + downs]
        mcaps, inds = _fetch_mcaps(hits), _fetch_industries(hits)
        for r in ups + downs:
            mc = mcaps.get(r["ticker"])
            r["mcap"] = round(mc / 1e8, 2) if mc else None
            r["ind"] = inds.get(r["ticker"])
        _backfill_korean_names(ups + downs, market)   # 네이티브명 → 한글(HK 등)
        out["up"], out["down"] = ups, downs
        log.info("finviz: %s movers — scanned %d → up %d / down %d",
                 source, out["scanned"], len(ups), len(downs))
        if ups or downs:
            _cache_write(cache_name, out)
    except Exception as exc:
        log.warning("finviz: %s movers 산출 실패: %s", source, exc)
    return out


def _compute_jp_stop(universe: list, names: dict, cache_name: str,
                     source: str) -> dict:
    """일본 ストップ高/安(상한가/하한가) — 유니버스 일봉 당일 변동이 TSE 制限値幅
    (price_sanity.jp_price_limit, 가격대별 tiered)에 도달한 종목 (사용자 2026-06-13
    'JP 상하한가'). 결정적(공개 표·스크래핑 불요). {upper, lower, ts, source,
    scanned}. mcap/업종/한글명 백필(hit 만)."""
    from bot.price_sanity import jp_price_limit
    out: dict = {"upper": [], "lower": [], "ts": _now_label(),
                 "source": source, "scanned": 0}
    try:
        _CHUNK = 120
        scanned = 0
        for ci in range(0, len(universe), _CHUNK):
            chunk = universe[ci:ci + _CHUNK]
            try:
                dfd = yf.download(chunk, period="5d", interval="1d",
                                  group_by="ticker", threads=True,
                                  progress=False, auto_adjust=False)
            except Exception:
                continue
            if dfd is None or dfd.empty:
                continue
            time.sleep(0.2)
            for tk in chunk:
                try:
                    sub = dfd if len(chunk) == 1 else dfd[tk]
                    closes = sub["Close"].dropna()
                    if len(closes) < 2:
                        continue
                    prev, last = float(closes.iloc[-2]), float(closes.iloc[-1])
                    lim = jp_price_limit(prev)
                    if not lim or prev <= 0:
                        continue
                    scanned += 1
                    chg = last - prev
                    if abs(chg) < lim * 0.99:        # 제한폭 미도달 → 통과
                        continue
                    pct = round((last / prev - 1) * 100, 2)
                    vols = sub["Volume"].dropna()
                    rec = {"ticker": tk, "name": names.get(tk, tk),
                           "price": round(last, 2), "pct": pct,
                           "vol": int(float(vols.iloc[-1])) if len(vols) else None}
                    (out["upper"] if chg > 0 else out["lower"]).append(rec)
                except Exception:
                    continue
        out["scanned"] = scanned
        hits = [r["ticker"] for r in out["upper"] + out["lower"]]
        mcaps, inds = _fetch_mcaps(hits), _fetch_industries(hits)
        for r in out["upper"] + out["lower"]:
            mc = mcaps.get(r["ticker"])
            r["mcap"] = round(mc / 1e8, 2) if mc else None
            r["ind"] = inds.get(r["ticker"])
        _backfill_korean_names(out["upper"] + out["lower"], "JP")  # 銘柄名 → 한글
        log.info("finviz: %s jp-stop — scanned %d → 상한 %d / 하한 %d",
                 source, scanned, len(out["upper"]), len(out["lower"]))
        if out["upper"] or out["lower"]:
            _cache_write(cache_name, out)
    except Exception as exc:
        log.warning("finviz: %s jp-stop 산출 실패: %s", source, exc)
    return out


def _naver_worldstock_overlay(rows: list, market: str,
                              overlay_name: bool = True) -> None:
    """HK/JP/US/CN 신고저 행에 네이버 worldstock 거래량·거래대금·시총·(종목명)
    overlay (yfinance 빈약·fast_info rate-limit 보완, 사용자 2026-06-14). HK 는
    5자리↔4자리 zfill 정수 정규화(Tencent 00700↔0700), JP/US/CN 은 직접 매칭
    (7203.T·NVDA·600519.SS). overlay_name=False(US/CN)면 기존 종목명 파이프라인
    (한글명 quote_map·번역) 보존하고 시총·거래량만 채움. in-place·graceful."""
    try:
        from bot.naver_ranking_client import world_stock_map
        wsm = world_stock_map(market)
    except Exception as exc:
        log.warning("finviz: %s worldstock overlay 실패: %s", market, exc)
        return
    if not wsm:
        return
    norm: dict = {}
    if market == "HK":
        for k, v in wsm.items():
            c = str(k).split(".")[0]
            if c.isdigit():
                norm.setdefault(str(int(c)), v)
    for r in rows:
        if market == "HK":
            code = str(r.get("ticker")).split(".")[0]
            w = (wsm.get(r.get("ticker"))
                 or (norm.get(str(int(code))) if code.isdigit() else None) or {})
        else:
            w = wsm.get(r.get("ticker")) or {}
        if w.get("vol") is not None:
            r["vol"] = w["vol"]
        if w.get("value") is not None:
            r["value"] = w["value"]
        if w.get("mcap") is not None:
            r["mcap"] = w["mcap"]
        if overlay_name and w.get("name"):
            r["name"] = w["name"]


def _persist_mcap_overlay(rows: list, market: str) -> None:
    """시총 디스크 persist — yfinance 가 일시적으로 시총 None 줘도 직전 성공값 유지
    (사용자 2026-06-14 'TW 시총 안 떠'). enrich_for_panel persist 와 같은 파일
    (enrich_mcap_<market>.json) 공유 → 52주·무버 시총 일관. in-place·graceful."""
    try:
        pkey = f"enrich_mcap_{market}.json"
        persist = _cached(pkey, ttl=12 * 3600)
        persist = dict(persist) if isinstance(persist, dict) else {}
        changed = False
        for r in rows:
            tk = r.get("ticker")
            mc = r.get("mcap")
            if mc:
                if persist.get(tk) != mc:
                    persist[tk] = mc
                    changed = True
            elif persist.get(tk) is not None:
                r["mcap"] = persist[tk]
        if changed:
            _cache_write(pkey, persist)
    except Exception as exc:
        log.warning("finviz: %s mcap persist 실패: %s", market, exc)


def _fetch_mcaps(tickers: list) -> dict:
    """hit 종목 시가총액(USD) {ticker: mcap}. yfinance fast_info 병렬
    (백그라운드 전용 — 종목당 1 HTTP). 실패/부재 시 누락(graceful)."""
    if not tickers or yf_paused() or not fast_info_ok():
        return {}                      # YF_PAUSE 또는 fast_info 쿨다운 → skip(persist 폴백)
    out: dict = {}
    try:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor

        def _one(tk: str):
            try:
                fi = yf.Ticker(tk).fast_info
                mc = None
                try:
                    mc = fi.market_cap          # 속성 접근
                except Exception:
                    mc = fi.get("market_cap") if hasattr(fi, "get") else None
                if not mc:
                    return tk, None
                mc = float(mc)
                # 글리치 가드 (KLAC $3.15T 클래스): market_cap = 글리치
                # last_price × 주식수 — 직전 종가 비율로 역보정.
                # 2차 — last·prev 둘 다 같은 미조정 기준이면 1차가 장님 →
                # 조정 일봉 종가와 교차 (백그라운드 전용 경로라 호출 OK).
                try:
                    from bot.price_sanity import quote_glitch_gap
                    lp = getattr(fi, "last_price", None)
                    pc = getattr(fi, "previous_close", None)
                    if quote_glitch_gap(lp, pc):
                        mc = mc * float(pc) / float(lp)
                    elif lp:
                        hist = yf.Ticker(tk).history(period="5d")
                        if hist is not None and len(hist) and "Close" in hist:
                            hc = float(hist["Close"].dropna().iloc[-1])
                            if hc > 0 and quote_glitch_gap(lp, hc):
                                mc = mc * hc / float(lp)
                except Exception:
                    pass
                return tk, mc
            except Exception as exc:
                if is_rate_limit_error(exc):   # rate-limit → 회로차단 발동
                    fast_info_trip("_fetch_mcaps")
                return tk, None

        with ThreadPoolExecutor(max_workers=12) as ex:
            for tk, mc in ex.map(_one, tickers):
                if mc:
                    out[tk] = mc
    except Exception as exc:
        log.warning("finviz: 시총 fetch 실패: %s", exc)
    return out


def _parse_nasdaq_screener(payload: dict) -> dict:
    """api.nasdaq.com screener JSON → {티커: 업종}. download=true 응답의
    data.rows (구형 data.table.rows 도 수용). industry 우선, 없으면 sector.
    심볼은 universe 와 동일 정규화('.'/'/'→'-'). 순수 함수(단위테스트)."""
    out: dict = {}
    try:
        data = payload.get("data") or {}
        rows = data.get("rows") or (data.get("table") or {}).get("rows") or []
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            if not sym or "^" in sym or len(sym) > 6:
                continue
            ind = (row.get("industry") or "").strip() or (row.get("sector") or "").strip()
            if not ind:
                continue
            out[sym.replace(".", "-").replace("/", "-")] = ind
    except Exception:
        return out
    return out


def _nasdaq_screener_industries() -> dict:
    """전 미국 상장 {티커: 업종} — NASDAQ 공식 screener API 1콜 (sector+
    industry 포함, NYSE/AMEX 포함, 무키). 7일 캐시(분류는 사실상 불변).
    실패 시 {} (graceful)."""
    c = _cached("nasdaq_industries.json", ttl=7 * 86400)
    if c:
        return c
    try:
        import httpx
        r = httpx.get(
            "https://api.nasdaq.com/api/screener/stocks"
            "?tableonly=true&limit=25&offset=0&download=true",
            headers={"User-Agent": _UA,
                     "Accept": "application/json, text/plain, */*",
                     "Accept-Language": "en-US,en;q=0.9"},
            timeout=25, follow_redirects=True)
        if r.status_code == 200:
            out = _parse_nasdaq_screener(r.json())
            if len(out) > 1000:
                _cache_write("nasdaq_industries.json", out)
                log.info("finviz: NASDAQ screener 업종맵 %d종목", len(out))
                return out
            log.warning("finviz: NASDAQ screener 업종맵 %d행 — 스키마 변경?",
                        len(out))
        else:
            log.warning("finviz: NASDAQ screener → HTTP %d", r.status_code)
    except Exception as exc:
        log.warning("finviz: NASDAQ screener fetch 실패: %s", exc)
    return {}


_BULK_IND_FAIL_TS = 0.0   # 벌크 소스 전멸 시 10분 재시도 억제 (렌더 hang 방지)


def _bulk_industry_maps() -> dict:
    """벌크 업종 맵 — per-ticker 호출 0, 1콜짜리 소스 2개 레이어 머지:
    NASDAQ screener(전 상장, base) ← GitHub S&P500 GICS Sub-Industry(우선,
    granular). 둘 다 7일 디스크 캐시라 렌더 경로에서도 안전. 전멸 시
    10분 백오프(요청 경로 반복 타임아웃 방지)."""
    global _BULK_IND_FAIL_TS
    if time.time() - _BULK_IND_FAIL_TS < 600:
        return {}
    m: dict = {}
    m.update(_nasdaq_screener_industries())
    m.update(_sp500_inds_github())
    if not m:
        _BULK_IND_FAIL_TS = time.time()
    return m


def _fetch_industries(tickers: list, allow_slow: bool = True) -> dict:
    """hit 종목 업종분류 {ticker: industry} — 영구 디스크 캐시 + 3-레이어
    (사용자 2026-06-12 '핀비즈 기준으로 해주던 적절히 매치'):
      1) 영구 캐시 (us_industry_cache.json, 분류는 사실상 불변)
      2) 벌크 맵 — GitHub S&P500 GICS + NASDAQ screener (각 1콜·7일 캐시)
      3) yfinance .info — allow_slow=True(백그라운드 산출)일 때만, 잔여
         소수만. (.info 가 데이터센터 IP 에서 비어 '업종 —' 전멸이던 건
         2026-06-12 — 벌크 레이어가 1차가 되어 .info 의존 제거.)
    실패 종목은 누락(graceful), 해소되면 영구 캐시에 적재."""
    if not tickers:
        return {}
    if yf_paused():               # YF_PAUSE → .info 레이어 skip(캐시·벌크맵 유지)
        allow_slow = False
    cache = _cached("us_industry_cache.json", ttl=365 * 86400) or {}
    changed = False
    missing = [t for t in tickers if t not in cache]
    if missing:
        bulk = _bulk_industry_maps()
        for t in missing:
            ind = bulk.get(t)
            if ind:
                cache[t] = str(ind)
                changed = True
        missing = [t for t in tickers if t not in cache]
    if missing and allow_slow:
        try:
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor

            def _one(tk: str):
                try:
                    info = yf.Ticker(tk).info or {}
                    return tk, (info.get("industry") or info.get("sector"))
                except Exception:
                    return tk, None

            with ThreadPoolExecutor(max_workers=12) as ex:
                for tk, ind in ex.map(_one, missing[:250]):
                    if ind:
                        cache[tk] = str(ind)
                        changed = True
        except Exception as exc:
            log.warning("finviz: 업종 fetch 실패: %s", exc)
    if changed:
        _cache_write("us_industry_cache.json", cache)
    return {t: cache[t] for t in tickers if t in cache}


def _fetch_display_names(tickers: list, allow_slow: bool = True) -> dict:
    """{ticker: longName(영문)} — 영구 캐시(이름 불변) + yfinance .info 병렬.
    .info 느림 → allow_slow=False(렌더 경로) 면 .info skip·영구 캐시만(사용자
    2026-06-16 TW 렌더 8.2s 블록 제거 — 느린 .info 는 백그라운드 enrich 로 이전).
    실패분은 캐시 안 함(다음 스캔 재시도). VM 검증: .info longName 전 시장 정상."""
    if not tickers:
        return {}
    cache = _cached("highlow_names.json", ttl=365 * 86400) or {}
    missing = [t for t in tickers if t not in cache]
    if yf_paused() or not allow_slow:   # YF_PAUSE 또는 렌더-세이프 → .info skip, 캐시만
        missing = []
    if missing:
        from concurrent.futures import ThreadPoolExecutor  # _fetch_mcaps 패턴

        def _one(tk):
            try:
                info = yf.Ticker(tk).info
                return tk, str(info.get("longName") or info.get("shortName") or "")
            except Exception:
                return tk, ""
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for tk, nm in pool.map(_one, missing):
                    if nm:                       # 실패('')는 미캐시 → 재시도
                        cache[tk] = nm
            _cache_write("highlow_names.json", cache)
        except Exception as exc:
            log.warning("finviz: display name fetch 실패: %s", exc)
    return {t: cache.get(t, "") for t in tickers}


def _backfill_industries(out: dict) -> bool:
    """이미 산출/캐시된 신고저 행에 업종 누락분을 채움 (렌더 경로 —
    yfinance .info 금지, 캐시+벌크만). `ind` 필드 도입 전 stale 캐시
    (highlow.json / highlow_sp500.json SWR)가 '업종 —' 로 서빙되던 것
    치유 (2026-06-12). 변경 여부 반환."""
    try:
        rows = [r for r in (out.get("high") or []) + (out.get("low") or [])
                if not r.get("ind")]
        if not rows:
            return False
        inds = _fetch_industries([r["ticker"] for r in rows], allow_slow=False)
        changed = False
        for r in rows:
            ind = inds.get(r.get("ticker"))
            if ind:
                r["ind"] = ind
                changed = True
        return changed
    except Exception as exc:
        log.warning("finviz: 업종 backfill 실패: %s", exc)
        return False


# ── 전 미국 상장 universe (nasdaqtrader 공식 심볼 디렉토리) ────────────────

def _parse_symdir(text: str, symcol: str) -> tuple[list, dict]:
    """nasdaqtrader symdir 파이프 구분 텍스트 → (티커 리스트, {티커:회사명}).
    ETF/Test Issue/워런트·우선주·유닛 류 제외, BRK.B→BRK-B 정규화. 순수
    함수(단위테스트)."""
    tks: list = []
    names: dict = {}
    lines = text.splitlines()
    if not lines:
        return tks, names
    hdr = lines[0].split("|")
    idx = {h.strip(): i for i, h in enumerate(hdr)}
    si = idx.get(symcol, 0)
    ni = idx.get("Security Name", 1)
    etf_i = idx.get("ETF")
    test_i = idx.get("Test Issue")
    # 비-보통주 + SPAC 제외 (2026-06-11 검증 + 06-12 보강: 'Acquisition
    # II Corp'/'Acquisitions'/'KRAKacquisition'/'SPAC' 변형이 옛 정확일치
    # 필터를 통과 — 대소문자 무시 substring 으로 확대. 채권형(ZONES/
    # Repackaged/Bonds) 혼입도 차단).
    _SKIP_NAME_CI = ("warrant", "right", "preferred", " unit", "units",
                     "notes", "debenture", "acquisition", "spac",
                     "merger corp", "bond", "zones", "repackaged")
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        cols = ln.split("|")
        if len(cols) <= max(si, ni):
            continue
        sym = cols[si].strip()
        nm = cols[ni].strip()
        if not sym or "$" in sym or len(sym) > 6:
            continue
        if etf_i is not None and len(cols) > etf_i and cols[etf_i].strip() == "Y":
            continue
        if test_i is not None and len(cols) > test_i and cols[test_i].strip() == "Y":
            continue
        nml = nm.lower()
        if any(k in nml for k in _SKIP_NAME_CI):
            continue
        # NASDAQ 5번째 자리 suffix 관행: W=워런트, U=유닛 (이름에 'Warrant'
        # 가 빠진 행이 실재 — AIMDW/NCPLW 2026-06-12 surfaced).
        if len(sym) == 5 and sym[-1] in ("W", "U"):
            continue
        sym = sym.replace(".", "-").replace("/", "-")
        tks.append(sym)
        # 표기용 — ' - Common Stock' 류 suffix 정리
        names[sym] = nm.split(" - ")[0].strip() or sym
    return tks, names


def _us_full_universe() -> tuple[list, dict]:
    """전 미국 상장 보통주 — nasdaqlisted.txt + otherlisted.txt (공식·무키·
    일 갱신, NYSE/AMEX 포함). 7일 캐시. 실패 시 ([], {})."""
    c = _cached("us_universe_v3.json", ttl=7 * 86400)
    cn = _cached("us_universe_names_v3.json", ttl=7 * 86400)
    if c and cn:
        return c, cn
    tks: list = []
    names: dict = {}
    try:
        import httpx
        for url, symcol in (
            ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
             "Symbol"),
            ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
             "ACT Symbol"),
        ):
            r = httpx.get(url, timeout=25, follow_redirects=True)
            if r.status_code == 200 and r.text:
                t, n = _parse_symdir(r.text, symcol)
                tks.extend(t)
                names.update(n)
            else:
                log.warning("finviz: symdir %s → HTTP %d", url, r.status_code)
    except Exception as exc:
        log.warning("finviz: 전미국 universe fetch 실패: %s", exc)
    # dedupe (양쪽 파일 중복 방어)
    seen: set = set()
    uniq = [t for t in tks if not (t in seen or seen.add(t))]
    if len(uniq) > 1000:
        _cache_write("us_universe_v3.json", uniq)
        _cache_write("us_universe_names_v3.json", names)
        log.info("finviz: 전미국 universe %d종목 (nasdaqtrader)", len(uniq))
        return uniq, names
    return [], {}


_FULL_HL_LOCK = _threading.Lock()
_FULL_HL_REFRESHING = False


def _kick_full_us_refresh() -> None:
    """전미국 highlow 백그라운드 재계산 — 1개만 (수 분 소요)."""
    global _FULL_HL_REFRESHING
    with _FULL_HL_LOCK:
        if _FULL_HL_REFRESHING:
            return
        _FULL_HL_REFRESHING = True

    def _run():
        global _FULL_HL_REFRESHING
        try:
            _compute_highlow_full_us()
        except Exception as exc:
            log.warning("finviz: 전미국 highlow 재계산 실패: %s", exc)
        finally:
            with _FULL_HL_LOCK:
                _FULL_HL_REFRESHING = False

    _threading.Thread(target=_run, daemon=True,
                      name="highlow-full-us").start()


def _highlow_full_us(force: bool = False) -> dict:
    """전미국 산출 티어 — **동기 계산 절대 안 함** (~수 분이라 페이지 hang
    금지). 장-인지 신선도(_session_fresh US, 장중 1h / 장 밖 마지막 마감 이후
    산출본이면 재스캔 0 — 사용자 2026-06-13 '모두 장중에만 1h'). stale
    (≤24h) 서빙+백그라운드 재계산 / 캐시 부재 시 kick 후 빈 dict → 호출부가
    S&P500 티어로 폴스루 (다음 방문부터 전량 표시)."""
    stale = _cached("highlow_full_us_v5.json", ttl=86400)
    if stale is not None and not force:    # force(장중 슬롯)=게이트 우회 → 항상 재산출 kick
        try:
            mt = (_CACHE_DIR / "highlow_full_us_v5.json").stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh("US", mt, _HL_US_INTRA_TTL):   # US 30분(타 시장 1h)
            return _prune_cached(stale, ("high", "low"), "highlow_full_us_v5.json")
    _kick_full_us_refresh()   # _FULL_HL_REFRESHING 가드로 중복 0 — 동시 1개, 동기 계산 안 함
    return stale if stale is not None else {}


# ── 가장 많이 오른/내린 TOP 30 (전 미국 상장 — 사용자 2026-06-12) ─────────
# 신고가/신저가 형제 표면: 당일 등락률 상·하위 30. 전미국 universe 5d
# 일봉(분할조정) 벌크 산출 — 신고저 전미국 티어와 동일하게 **백그라운드
# 전용** SWR (수 분 배치가 페이지 렌더를 hang 못 시키게).

_MOVERS_MIN_PRICE = 1.0     # $1 미만 페니 — 절대가 푼돈에 % 만 큰 노이즈 컷
_MOVERS_MIN_DOLLAR_M = 0.5  # 당일 거래대금 $0.5M 미만 — 호가 공백 노이즈 컷
_MOVERS_TOP_N = 30
_MOVERS_CACHE = "us_movers_v1.json"
_MOVERS_STATUS = "us_movers_status.json"   # 산출 상태 마커 (가시성 — 실수 #12d)


def _movers_status_write(state: str, **kw) -> None:
    """산출 상태 기록 — '산출 중' 영구 표시 클래스 차단: 실패도 흔적을
    남겨 페이지가 진행률/실패 사유를 보여줄 수 있게 (2026-06-13 surfaced
    — 총실패 시 캐시 미작성 → 영구 building + 매 방문 재발동)."""
    kw.update({"state": state, "ts": time.time(), "ts_label": _now_label()})
    _cache_write(_MOVERS_STATUS, kw)


def movers_status() -> dict:
    """현재 산출 상태 {state: running|done|failed, ts, ...} — 부재 시 {}."""
    return _cached(_MOVERS_STATUS, ttl=86400) or {}


def _rank_us_movers(rows: list, top_n: int = _MOVERS_TOP_N) -> tuple[list, list]:
    """순수 랭킹 (단위테스트) — 페니(<$1)·박거래(<$0.5M)·pct 결측 컷 후
    등락률 상위/하위 각 top_n. 글리치(|pct|>75%)는 스캔 단에서 이미 드랍."""
    ok = [r for r in rows
          if r.get("pct") is not None
          and (r.get("price") or 0) >= _MOVERS_MIN_PRICE
          and (r.get("dollar_m") or 0) >= _MOVERS_MIN_DOLLAR_M]
    ups = sorted((r for r in ok if r["pct"] > 0),
                 key=lambda r: r["pct"], reverse=True)[:top_n]
    downs = sorted((r for r in ok if r["pct"] < 0),
                   key=lambda r: r["pct"])[:top_n]
    return ups, downs


def _compute_us_movers() -> dict:
    """전 미국 상장 5d 일봉 벌크 → 당일 등락 상·하위 TOP30. 백그라운드
    전용 (배치 ~60회·수 분). universe = nasdaqtrader(_parse_symdir 가
    SPAC·워런트·채권형 제외), 폴백 S&P500. **auto_adjust=True** — 분할
    자체를 소스에서 중화(highlow 의 raw 봉과 달리 등락률 비교라 필수),
    잔존 |pct|>75% 는 글리치 드랍 (KLAC 클래스, CLAUDE.md 가드)."""
    # 네이버 우선 (NASDAQ+NYSE+AMEX 등락·한글명·거래대금/시총·1콜씩, 사용자
    # 2026-06-13 '미국등급급락은 네이버'). 성공 시 전미국 yfinance 스캔(수 분) 생략.
    try:
        from bot.naver_ranking_client import fetch_us_movers as _nv_us_movers
        nv = _nv_us_movers()
        if nv.get("up") or nv.get("down"):
            # 업종(+업종분포)은 네이버 미제공 → 기존 GICS/NASDAQ/yfinance 업종 로직
            # 으로 enrich (사용자 2026-06-13 '급등급락에 업종·업종분포 야후로').
            # 벌크맵(S&P500 GICS·NASDAQ) 우선이라 429 내성, ~60종목만 stragglers .info.
            try:
                hits = [r["ticker"] for r in nv["up"] + nv["down"] if r.get("ticker")]
                inds = _fetch_industries(hits)
                # 야후 벌크(S&P500 GICS·NASDAQ)는 NYSE/AMEX 소형주를 자주 비워
                # '업종 —' → 네이버 USA 업종맵(전 거래소·4700+)으로 빈 것 보강
                # (사용자 2026-06-14 'US 급등락 업종 제발'). 야후 우선·네이버 폴백.
                nv_ind = {}
                try:
                    from bot.naver_ranking_client import world_industry_map
                    nv_ind = world_industry_map("US")
                except Exception:
                    pass
                for r in nv["up"] + nv["down"]:
                    if not r.get("ind"):
                        r["ind"] = inds.get(r["ticker"]) or nv_ind.get(r["ticker"])
            except Exception as exc:
                log.warning("US movers 업종 enrich 실패: %s", exc)
            nv["up"] = prune_non_stock(nv["up"])       # CEF·유령·이중클래스 가지치기
            nv["down"] = prune_non_stock(nv["down"])
            try:                                        # AMEX 제외 (사용자 2026-06-17)
                from bot.us_symbol_names import drop_amex_rows
                nv["up"] = drop_amex_rows(nv["up"])     # 네이버 거래소-드랍 belt-and-
                nv["down"] = drop_amex_rows(nv["down"]) # suspenders + 옛 캐시 정제
            except Exception:
                pass
            _cache_write(_MOVERS_CACHE, nv)
            _movers_status_write("done", up=len(nv["up"]), down=len(nv["down"]), src="naver")
            return nv
    except Exception as exc:
        log.warning("naver US movers 실패 → yfinance 스캔 폴백: %s", exc)
    out: dict = {"up": [], "down": [], "ts": _now_label(), "scanned": 0,
                 "source": "전 미국 상장 산출(yfinance · 당일 등락)"}
    if yf_paused():               # YF_PAUSE → yfinance 폴백 스캔 skip(캐시 유지)
        return _cached(_MOVERS_CACHE, ttl=86400) or out
    tks, names = _us_full_universe()
    if not tks:
        tks = _us_universe_robust()
        names = _sp500_names()
        out["source"] = "S&P 500 산출(yfinance · 당일 등락)"
    if not tks:
        log.warning("finviz: movers — universe empty")
        _movers_status_write("failed", detail="universe 전 소스 실패")
        return out
    try:                       # AMEX 제외 (사용자 2026-06-17) — yfinance 폴백 유니버스
        from bot.us_symbol_names import us_amex_symbols
        _amex = us_amex_symbols()
        if _amex:
            tks = [t for t in tks if str(t).upper() not in _amex]
    except Exception:
        pass
    rows: list[dict] = []
    _CHUNK = 120
    n_batches = (len(tks) + _CHUNK - 1) // _CHUNK
    empty_batches = 0
    _movers_status_write("running", done=0, total=n_batches,
                         universe=len(tks))
    try:
        import yfinance as yf
        log.info("finviz: movers — universe %d, 배치 다운로드 시작", len(tks))
        for ci in range(0, len(tks), _CHUNK):
            chunk = tks[ci:ci + _CHUNK]
            bi = ci // _CHUNK + 1
            if bi % 5 == 0:   # 진행률 — 페이지가 '배치 N/총' 표시
                _movers_status_write("running", done=bi, total=n_batches,
                                     universe=len(tks), rows=len(rows))
            try:
                df = yf.download(chunk, period="5d", interval="1d",
                                 group_by="ticker", threads=True,
                                 progress=False, auto_adjust=True)
            except Exception as exc:
                log.warning("finviz: movers 배치 %d 실패: %s", bi, exc)
                empty_batches += 1
                continue
            if df is None or df.empty:
                empty_batches += 1
                continue
            last_day = df.index[-1]
            time.sleep(0.2)   # 벌크 사이 호흡 (highlow 스캔과 동일)
            for tk in chunk:
                try:
                    if tk not in df.columns.get_level_values(0):
                        continue
                    sub = df[tk]
                    closes = sub["Close"].dropna()
                    # 당일 봉 없는 종목(정지·스테일) — 옛 날짜의 등락이
                    # phantom 무버로 오르는 것 차단
                    if len(closes) < 2 or closes.index[-1] != last_day:
                        continue
                    c0, c1 = float(closes.iloc[-1]), float(closes.iloc[-2])
                    if c1 <= 0:
                        continue
                    pct = (c0 / c1 - 1.0) * 100.0
                    if abs(pct) > 75.0:
                        log.warning("finviz: movers 분할/조정 글리치 의심 드랍 "
                                    "— %s %+.0f%%", tk, pct)
                        continue
                    vols = sub["Volume"].dropna()
                    v0 = float(vols.iloc[-1]) if len(vols) else 0.0
                    rows.append({
                        "ticker": tk, "name": (names or {}).get(tk, tk),
                        "price": round(c0, 2), "pct": round(pct, 2),
                        "dollar_m": round(c0 * v0 / 1e6, 2),
                    })
                except Exception:
                    continue
        out["scanned"] = len(rows)
        ups, downs = _rank_us_movers(rows)
        # 시총·업종 부착 — 랭킹에 든 ≤60 종목만 (백그라운드 전용이라 OK)
        hits = [r["ticker"] for r in ups + downs]
        mcaps = _fetch_mcaps(hits)
        inds = _fetch_industries(hits)
        for r in ups + downs:
            mc = mcaps.get(r["ticker"])
            r["mcap"] = round(mc / 1e8, 2) if mc else None   # 억$
            r["ind"] = inds.get(r["ticker"])
        out["up"], out["down"] = prune_non_stock(ups), prune_non_stock(downs)
        log.info("finviz: movers — scanned %d → up %d / down %d (빈 배치 %d/%d)",
                 len(rows), len(ups), len(downs), empty_batches, n_batches)
        if ups or downs:
            _cache_write(_MOVERS_CACHE, out)
            _movers_status_write("done", scanned=len(rows),
                                 up=len(ups), down=len(downs))
        else:
            # 전 배치 빈 응답 = yfinance 레이트리밋/차단 의심 — 캐시 미작성
            # 이더라도 실패 흔적은 남겨 페이지가 사유 표시 + 재발동 백오프
            _movers_status_write(
                "failed", scanned=len(rows),
                detail=f"행 0 (빈 배치 {empty_batches}/{n_batches} — "
                       "yfinance 일시 제한 의심)")
    except Exception as exc:
        log.warning("finviz: movers 산출 실패: %s", exc)
        _movers_status_write("failed", detail=f"{type(exc).__name__}: {exc}")
    return out


_MOVERS_LOCK = _threading.Lock()
_MOVERS_REFRESHING = False


def _kick_us_movers_refresh() -> None:
    """movers 백그라운드 재계산 — 1개만 (stampede 방지)."""
    global _MOVERS_REFRESHING
    with _MOVERS_LOCK:
        if _MOVERS_REFRESHING:
            return
        _MOVERS_REFRESHING = True

    def _run():
        global _MOVERS_REFRESHING
        try:
            _compute_us_movers()
        except Exception as exc:
            log.warning("finviz: movers 백그라운드 재계산 실패: %s", exc)
        finally:
            with _MOVERS_LOCK:
                _MOVERS_REFRESHING = False

    _threading.Thread(target=_run, daemon=True, name="us-movers").start()


# 시장별 정규장 창 (UTC) — 장-인지 신선도(_session_fresh). (open_h,open_m,close_h,close_m).
# KST=UTC+9, JST=UTC+9, TST/CST/HKT=UTC+8, US=ET(EST/EDT 여유 커버). 단일 UTC 일(자정
# 미교차)이라 (h,m) 튜플 비교로 충분. 점심 휴장은 무시(장중 취급 — 추가 스캔 무해).
_SESSIONS_UTC = {
    "US":   (13, 0, 21, 30),   # 09:30–16:00 ET (여유)
    "KR":   (0, 0, 6, 30),     # 09:00–15:30 KST
    "JP":   (0, 0, 6, 0),      # 09:00–15:00 JST
    "TW":   (1, 0, 5, 30),     # 09:00–13:30 TST(+8)
    "CN_A": (1, 30, 7, 0),     # 09:30–15:00 CST(+8)
    "HK":   (1, 30, 8, 0),     # 09:30–16:00 HKT(+8)
}
_HL_INTRA_TTL = 1 * 3600       # 장중 52주 신고저 재산출 간격 (사용자 2026-06-13). 장 밖 0.
# US 전용 30분(사용자 2026-06-19 '미국장은 새벽이라 부하 적다 → 30분 전량 갱신'). 타
# 시장(JP/HK/CN/TW)은 _HL_INTRA_TTL 1h 유지. 슬롯(:00·:30)이 장중 force 로 30분마다
# 전체 5,625 재산출, 종가엔 EOD 1회 후 freeze (B′ — 샤딩 대신 전량 더 자주, 새벽 저부하).
_HL_US_INTRA_TTL = 30 * 60
_MOVERS_INTRA_TTL = 30         # 장중 무버(급등락) 재산출 간격 — 30초 (사용자 2026-06-15
#  '급등락 30초, 대만제외'). US/JP/CN/HK/KR 무버는 네이버 랭킹(fast_info·야후 무관, 컴퓨트당
#  ~2-6콜) + SWR(페이지 접근 시만·백그라운드 킥) + graceful(네이버 throttle 시 stale 서빙)
#  이라 30초여도 야후 rate-limit 리스크 0. TW 무버는 별도 경로(TWSE 전종목 대용량 CSV,
#  _HL_INTRA_TTL 1h 유지 — 30초면 대용량 fetch 낭비·throttle 위험). 52주 신고저는 1h 그대로.


def _session_fresh(market: str, cache_ts: float, intra_ttl: float,
                   now_ts: float | None = None) -> bool:
    """시장-인지 신선도 (사용자 2026-06-13 '장종료후 굳이 안 돌려도·나라별 시간
    체크해 부하없이') — 순수. 정규장 중엔 intra_ttl TTL(데이터 계속 변함), 장
    밖(야간·주말)엔 **마지막 마감 이후 산출본이면 fresh → 재스캔 0**.

    플랫 TTL 보다 **부하 낮음**(닫힌 시장 재스캔 제거) + 장중 더 신선 — 2h 가
    6h 플랫보다 가볍다(장 밖 0회). 시장 미상은 플랫 TTL 폴백. 휴일은 평일 창
    취급(추가 스캔이 무해해 거래소 캘린더 의존 안 둠)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.fromtimestamp(now_ts or time.time(), tz=timezone.utc)
    sess = _SESSIONS_UTC.get(market)
    if sess is None:
        return (now.timestamp() - cache_ts) < intra_ttl
    oh, om, ch, cm = sess
    in_session = (now.weekday() < 5
                  and (oh, om) <= (now.hour, now.minute) < (ch, cm))
    if in_session:
        return (now.timestamp() - cache_ts) < intra_ttl
    # 마지막 마감(가장 최근의 평일 close ≤ now) 이후 산출본이면 fresh
    d = now
    for _ in range(8):          # 주말+연휴 스팬 커버 (유한 보장)
        if d.weekday() < 5:
            cand = d.replace(hour=ch, minute=cm, second=0, microsecond=0)
            if cand <= now:
                return cache_ts >= cand.timestamp()
        d = (d - timedelta(days=1)).replace(hour=23, minute=59)
    return False


def _movers_cache_is_fresh(cache_ts: float, now_ts: float | None = None) -> bool:
    """US 무버 장-인지 신선도 — 장중 1분 / 장 밖 마지막 마감 이후 재스캔 0
    (사용자 2026-06-14 '급등급락은 30분단위로 모두' — 52주 1h 와 분리)."""
    return _session_fresh("US", cache_ts, _MOVERS_INTRA_TTL, now_ts)


def _backfill_korean_names(rows: list, market: str) -> None:
    """JP/TW/CN/HK hit 종목의 표시명을 **한글**로 (사용자 2026-06-13 '한국·미국
    제외 모든 나라 괄호 안 한국어'). 네이티브명(universe 가 준 CJK/영문)을
    chart_translate Flash(영구캐시)로 **직접 번역** — 옛 'name==ticker 일 때만'
    로직은 JP/TW(銘柄名/약칭 보유)를 통째로 건너뛰어 南亞科 류가 그대로 노출됐음.
    네이티브명 없는 항목(name==ticker)만 yfinance longName 폴백 번역. KR(pykrx
    한글)·US(영문)는 호출 안 함. in-place·graceful(실패 시 원본 유지)."""
    if not market or market in ("US", "KR"):
        return
    if market == "TW":
        # 대만 () 안을 **한국어**로 (사용자 2026-06-14 '대만도 한글로' — 옛 영문 정책
        # 번복). yfinance longName(영문)·TWSE 中文 native 명 → 한국어 번역
        # (translate_titles_kr) → '티에스엠씨·미디어텍' 류. JP/HK 와 통일, 소형주까지.
        try:
            from bot.chart_translate import translate_titles_kr
            tickers = [r["ticker"] for r in rows]
            en = _fetch_display_names(tickers)            # yfinance longName(영문)
            ue = sorted({n for n in en.values() if n})
            ke = translate_titles_kr(ue) if ue else {}
            for r in rows:
                e = en.get(r["ticker"], "")
                if e:
                    r["name"] = ke.get(e) or e
            # longName 미스 종목 — 中文 native 명(_tw_universe names) 한국어 번역
            miss = [r for r in rows if not en.get(r["ticker"])
                    and r.get("name") and r["name"] != r["ticker"]]
            nat = sorted({r["name"] for r in miss})
            if nat:
                kr = translate_titles_kr(nat)
                for r in miss:
                    if kr.get(r["name"]):
                        r["name"] = kr[r["name"]]
        except Exception as exc:
            log.warning("finviz: TW 한글명 백필 실패: %s", exc)
        return
    # JP/CN/HK: 네이버 worldstock 이름맵 우선 (사용자 2026-06-14 — yfinance .info
    # 가 자주 막혀 52주 이름이 비던 것 해소, world_name_map 2000개/시장 확인됨).
    # 7d 캐시라 ₩0. 맵 미스만 chart_translate(네이티브명)+yfinance 폴백.
    naver_named: set = set()
    try:
        from bot.naver_ranking_client import world_name_map
        nmap = world_name_map(market)
        for r in rows:
            nm = nmap.get(r.get("ticker"))
            if nm:
                r["name"] = nm
                naver_named.add(r["ticker"])
    except Exception as exc:
        log.warning("finviz: %s 네이버 이름맵 실패: %s", market, exc)
    rest = [r for r in rows if r.get("ticker") not in naver_named]
    if not rest:
        return
    # 공식 상장목록 銘柄名/Name 주입 (네이버 worldstock 미커버 소형주가 티커로만
    # 노출되던 것 해소, 사용자 2026-06-14 (나)). JP/HK 만(JPX/HKEX 파일에 종목명).
    # name==ticker(네이티브명 부재) 항목에만 채워 아래 Flash 번역이 한글화.
    if market in ("JP", "HK"):
        try:
            from bot.intl_universe import full_universe_names
            offmap = full_universe_names(market)
            if offmap:
                for r in rest:
                    if ((not r.get("name") or r["name"] == r["ticker"])
                            and offmap.get(r["ticker"])):
                        r["name"] = offmap[r["ticker"]]
        except Exception as exc:
            log.warning("finviz: %s 공식 종목명 주입 실패: %s", market, exc)
    try:
        from bot.chart_translate import translate_titles_kr
        # 1) 네이버 미스 중 네이티브명(CJK) 직접 번역 (위 주입으로 소형주도 포함)
        native = sorted({r["name"] for r in rest
                         if r.get("name") and r["name"] != r["ticker"]})
        kr = translate_titles_kr(native) if native else {}
        for r in rest:
            nm = r.get("name")
            if nm and kr.get(nm):
                r["name"] = kr[nm]
        # 2) 네이티브명 없던 항목 → yfinance longName 폴백 후 번역
        need = [r for r in rest if not r.get("name") or r["name"] == r["ticker"]]
        if need:
            en = _fetch_display_names([r["ticker"] for r in need])
            ue = sorted({n for n in en.values() if n})
            ke = translate_titles_kr(ue) if ue else {}
            for r in need:
                e = en.get(r["ticker"], "")
                if e:
                    r["name"] = ke.get(e) or e
    except Exception as exc:
        log.warning("finviz: %s 한글명 백필 실패: %s", market, exc)


def fetch_us_movers() -> dict:
    """가장 많이 오른/내린 TOP30 — **동기 계산 절대 안 함** (전미국 배치
    수 분 = 페이지 hang 금지, 신고저 전미국 티어와 동일 SWR): 신선(30분)
    서빙 / stale(≤24h) 서빙 + 백그라운드 재계산 / 캐시 부재 시 백그라운드
    kick 후 'building' 반환 → 페이지가 상태(진행률/실패 사유) 안내.

    재발동 백오프 (2026-06-13): 직전 산출이 5분 내 실패였으면 kick 생략 —
    yfinance 일시 제한 중 매 방문 재스캔이 제한을 연장하는 악순환 차단.
    진행 중(running 30분 내)에도 중복 kick 생략 (프로세스 재시작으로
    in-process 플래그가 사라진 경우 대비).

    신선도 = 장-인지(_movers_cache_is_fresh): 장중 1h / 장 밖(야간·
    주말)은 마지막 마감 이후 산출본이면 재스캔 0 (사용자 2026-06-13
    '모두 장중에만 1h')."""
    stale = _cached(_MOVERS_CACHE, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / _MOVERS_CACHE).stat().st_mtime
        except OSError:
            mt = 0.0
        if _movers_cache_is_fresh(mt):
            return _prune_cached(stale, ("up", "down"), _MOVERS_CACHE)
    st = movers_status()
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass        # 백오프 — 5분 내 재시도 안 함 (상태만 표시)
    elif st.get("state") == "running" and age < 1800:
        pass        # 이미 산출 중 (다른 워커/재시작 전 프로세스)
    else:
        _kick_us_movers_refresh()
    if stale is not None:
        return stale
    return {"up": [], "down": [], "ts": "", "source": "",
            "building": True, "status": st}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    g = fetch_groups()
    print(f"groups ({g.get('source')}): {len(g.get('groups', []))}")
    for row in g.get("groups", [])[:5]:
        print(" ", row)
    hl = fetch_high_low()
    print(f"high {len(hl.get('high', []))} / low {len(hl.get('low', []))} ({hl.get('source')})")
    mv = _compute_us_movers()
    print(f"movers up {len(mv.get('up', []))} / down {len(mv.get('down', []))} ({mv.get('source')})")
