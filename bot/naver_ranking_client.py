"""네이버 모바일 front-api 랭킹 클라이언트 — KR 52주 신고가/신저가 (전종목·한글명
native·1콜). 사용자 2026-06-13: pykrx+yfinance 전종목 스캔(수 분·느림)을 네이버
ready 데이터로 교체 — "신고가 신저가는 네이버에 있어".

VM 실측으로 엔드포인트·필드·파라미터 전부 확인(2026-06-13, 샌드박스는 네이버
도달 불가). graceful — 실패/빈 응답 시 빈 결과 → 호출부가 pykrx 스캔으로 폴백.

국내(domestic):
  GET front-api/domestic/stock/list?sortType={high52week|low52week|up|down|...}
      &category={all|KOSPI|KOSDAQ}&page=1&pageSize=N
  → {isSuccess, result:{stocks:[{itemCode, name(한글), stockExchangeType,
     currentPrice, fluctuationsType, fluctuationsRatio, accumulatedTradingVolume,
     marketValue, stockEndType, ...}], totalCount}}
무료·무키. 해외(worldstock) 무버는 후속(별도 함수)."""
from __future__ import annotations

import logging

log = logging.getLogger("bot.naver_ranking")

_BASE = "https://m.stock.naver.com/front-api"
_H = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
}
# stockExchangeType → 우리 티커 접미사 (KIS/yfinance/대시보드 lookup 일관)
_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}


def _get_stocks(url: str) -> list | None:
    """네이버 front-api GET → result.stocks 리스트. graceful None.

    ⚠️ 봉투가 두 종류 (VM 실측 2026-06-13):
      worldstock(해외): {isSuccess, detailCode, message, result:{stocks}}
      domestic(국내):   {result:{stocks}}  ← **isSuccess 없음**
    그래서 isSuccess 는 **명시적 False 일 때만** 거부(없으면 통과) — 옛 `not
    isSuccess` 가드는 국내(isSuccess 부재)를 전부 거부해 52주/상한가가 0 이었음."""
    try:
        import requests
        r = requests.get(url, headers=_H, timeout=12)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or d.get("isSuccess") is False:
            return None
        res = d.get("result")
        if isinstance(res, dict) and isinstance(res.get("stocks"), list):
            return res["stocks"]
        if isinstance(d.get("stocks"), list):       # 최상위 stocks 폴백
            return d["stocks"]
        return None
    except Exception as exc:
        log.warning("naver_ranking GET 실패: %s", exc)
        return None


def _signed_pct(s: dict):
    """fluctuationsRatio(절대값 문자열) + fluctuationsType 으로 부호 결정.
    RISING/UPPER_LIMIT=+, FALLING/LOWER_LIMIT=-, 그 외(보합)=0."""
    try:
        v = abs(float(s.get("fluctuationsRatio") or 0))
    except (TypeError, ValueError):
        return None
    ft = str(s.get("fluctuationsType") or "")
    if "FALL" in ft or "LOWER" in ft:
        return round(-v, 2)
    return round(v, 2)


def _kr_row(s: dict) -> dict:
    """네이버 domestic stock → highlow_render 스키마 {ticker,name,price,pct,vol,mcap,ind}.
    mcap = marketValue(원) / 1e8 = 억 단위 (fmt_mcap 규약). 업종(ind) 네이버 미제공."""
    code = str(s.get("itemCode") or "")
    suf = _SUFFIX.get(str(s.get("stockExchangeType") or ""), "")
    mv = s.get("marketValue")
    try:
        mcap = round(float(mv) / 1e8, 2) if mv else None
    except (TypeError, ValueError):
        mcap = None
    tv = s.get("accumulatedTradingValue")
    try:
        value = round(float(tv) / 1e8, 2) if tv else None   # 거래대금 억(원)
    except (TypeError, ValueError):
        value = None
    return {
        "ticker": f"{code}{suf}" if code else code,
        "name": s.get("name") or code,
        "price": s.get("currentPrice"),
        "pct": _signed_pct(s),
        "vol": s.get("accumulatedTradingVolume"),
        "value": value,
        "mcap": mcap,
        "ind": None,
    }


# 해외(worldstock) stockExchangeType — VM 실측 enum (2026-06-13).
_WORLD_EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "SHANGHAI", "SHENZHEN",
                    "HONG_KONG", "TOKYO", "HOCHIMINH", "HANOI")
# worldstock sortType enum: marketValue|up|down|top|priceTop|dividend (52주 없음).


def _world_row(s: dict) -> dict:
    """네이버 worldstock → highlow_render 스키마. ticker=symbolCode(미국 'AAPL'·
    중국 '601288'), name=한글 native. mcap/value = 현지통화/1e8 억 (fmt_mcap 규약:
    US=억$→$T/$B, 그 외=억→억/조). 업종 미제공."""
    sym = str(s.get("symbolCode") or s.get("reutersCode") or "")

    def _eok(x):
        try:
            return round(float(x) / 1e8, 2) if x else None
        except (TypeError, ValueError):
            return None
    return {
        "ticker": sym,
        "name": s.get("name") or sym,
        "price": s.get("currentPrice"),
        "pct": _signed_pct(s),
        "vol": s.get("accumulatedTradingVolume"),
        "value": _eok(s.get("accumulatedTradingValue")),   # 거래대금 억(현지통화)
        "mcap": _eok(s.get("marketValue")),                # 시총 억(현지통화)
        "currency": s.get("currencyType"),
        "ind": None,
    }


# ⚠️ 네이버 front-api pageSize 상한 (VM 실측 2026-06-13): pageSize>~50 이면 빈
# 배열 반환(0). KR 52주/상한가가 pageSize=200 으로 전부 0 이던 진짜 원인. 50 이하
# + 페이지네이션으로 전종목 수집.
_PAGE_SIZE = 50


def _domestic_paged(sort_type: str, category: str = "all", max_items: int = 200) -> list:
    """domestic 랭킹 — pageSize 상한(50) 때문에 여러 페이지 합산. graceful []."""
    out: list = []
    for page in range(1, max_items // _PAGE_SIZE + 2):
        st = _get_stocks(f"{_BASE}/domestic/stock/list?sortType={sort_type}"
                         f"&category={category}&page={page}&pageSize={_PAGE_SIZE}")
        if not st:
            break
        out += st
        if len(st) < _PAGE_SIZE or len(out) >= max_items:
            break
    return out


def fetch_world_ranking(exchange: str, sort_type: str, limit: int = 30) -> list:
    """네이버 worldstock 랭킹 → rows (한글명·거래대금·시총). sort_type ∈
    {marketValue|up|down|top|priceTop|dividend}. pageSize 상한(50) 페이지네이션.
    graceful []."""
    rows: list = []
    for page in range(1, limit // _PAGE_SIZE + 2):
        st = _get_stocks(f"{_BASE}/worldstock/exchange/stock/list"
                         f"?stockExchangeType={exchange}&stockPriceSortType={sort_type}"
                         f"&page={page}&pageSize={min(limit, _PAGE_SIZE)}")
        if not st:
            break
        rows += [_world_row(s) for s in st]
        if len(st) < _PAGE_SIZE or len(rows) >= limit:
            break
    return rows[:limit]


def fetch_us_movers(top_n: int = 30) -> dict:
    """미국 급등/급락 — 네이버 worldstock NASDAQ+NYSE+AMEX up/down 병합·정렬
    (사용자 2026-06-13 '미국등급급락은 네이버·한글명'). {up,down,ts,source,scanned}.
    한글명 native·거래대금/시총 포함. graceful — 전 거래소 실패 시 빈(호출부 폴백)."""
    from bot.finviz_client import _now_label
    ups: list = []
    downs: list = []
    ok = False
    for ex in ("NASDAQ", "NYSE", "AMEX"):
        u = fetch_world_ranking(ex, "up", limit=top_n)
        d = fetch_world_ranking(ex, "down", limit=top_n)
        if u or d:
            ok = True
        ups += u
        downs += d
    if not ok:
        return {"up": [], "down": [], "ts": _now_label(), "source": "", "scanned": 0}
    ups.sort(key=lambda r: r.get("pct") if r.get("pct") is not None else -1e9, reverse=True)
    downs.sort(key=lambda r: r.get("pct") if r.get("pct") is not None else 1e9)
    return {"up": ups[:top_n], "down": downs[:top_n], "ts": _now_label(),
            "source": "네이버 증권 미국 등락(NASDAQ+NYSE+AMEX·한글명)",
            "scanned": len(ups) + len(downs)}


# 해외 무버 시장 → [(네이버 exchangeType, yfinance 접미사)] (사용자 2026-06-13
# '중국·홍콩·일본은 미국따라'). 상한가/하한가 있는 시장(JP 制限値幅·CN ±10/20%)도
# 그냥 상승/하락 TOP 로 표시. CN_A 는 상하이+선전 2거래소 병합.
_INTL_MOVER_EX = {
    "JP": [("TOKYO", ".T")],
    "HK": [("HONG_KONG", ".HK")],
    "CN_A": [("SHANGHAI", ".SS"), ("SHENZHEN", ".SZ")],
}
_INTL_MOVER_LABEL = {"JP": "도쿄(TSE)", "HK": "홍콩(HKEX)", "CN_A": "상하이+선전"}


def _suffix_ticker(sym: str, suf: str) -> str:
    """네이버 worldstock symbolCode → yfinance 접미사 티커. HK 는 4자리 zero-pad
    (700→0700.HK, yfinance 규약). JP '7203'→7203.T, CN '601288'→601288.SS."""
    sym = str(sym or "")
    if not sym:
        return sym
    if suf == ".HK":
        sym = sym.zfill(4)
    return f"{sym}{suf}"


def fetch_intl_movers_naver(market: str, top_n: int = 30) -> dict:
    """JP/CN/HK 급등·급락 — 네이버 worldstock up/down 병합·정렬 (미국 무버 미러,
    사용자 2026-06-13). 한글명·거래대금·시총 native. 업종(ind)은 호출부가 yfinance
    로 enrich. CN_A 는 상하이+선전 병합. graceful — 전 거래소 실패 시 빈(폴백)."""
    cfg = _INTL_MOVER_EX.get(market)
    if not cfg:
        return {"up": [], "down": [], "ts": "", "source": "", "scanned": 0}
    from bot.finviz_client import _now_label
    ups: list = []
    downs: list = []
    ok = False
    for ex, suf in cfg:
        u = fetch_world_ranking(ex, "up", limit=top_n)
        d = fetch_world_ranking(ex, "down", limit=top_n)
        if u or d:
            ok = True
        for r in u:
            r["ticker"] = _suffix_ticker(r.get("ticker"), suf)
        for r in d:
            r["ticker"] = _suffix_ticker(r.get("ticker"), suf)
        ups += u
        downs += d
    if not ok:
        return {"up": [], "down": [], "ts": _now_label(), "source": "", "scanned": 0}
    ups.sort(key=lambda r: r.get("pct") if r.get("pct") is not None else -1e9, reverse=True)
    downs.sort(key=lambda r: r.get("pct") if r.get("pct") is not None else 1e9)
    lbl = _INTL_MOVER_LABEL.get(market, market)
    return {"up": ups[:top_n], "down": downs[:top_n], "ts": _now_label(),
            "source": f"네이버 증권 {lbl} 등락(한글명·거래대금/시총)",
            "scanned": len(ups) + len(downs)}


def world_name_map(market: str, max_pages: int = 40) -> dict:
    """{yfinance-접미사 ticker → 한글명} — JP/CN/HK 네이버 worldstock(marketValue
    정렬 페이지네이션, 사용자 2026-06-14 '네이버에서 한글종목명'). 종목명은
    안정적이라 7d 디스크 캐시. 실적/캘린더가 티커→한글명 조회에 사용. TW 는
    네이버 worldstock 미지원 → 빈 dict(호출부가 chart_translate 폴백). graceful·
    429 면역. 대형주부터(실적 종목은 대부분 상위) 채우므로 max_pages 로 bound."""
    cfg = _INTL_MOVER_EX.get(market)
    if not cfg:
        return {}
    try:
        from bot.finviz_client import _CACHE_DIR, _cache_write, _cached  # noqa: F401
    except Exception:
        return {}
    cache = f"naver_world_names_{market}.json"
    cached = _cached(cache, ttl=7 * 86400)
    if isinstance(cached, dict) and cached:
        return cached
    out: dict = {}
    for ex, suf in cfg:
        for page in range(1, max_pages + 1):
            st = _get_stocks(f"{_BASE}/worldstock/exchange/stock/list"
                             f"?stockExchangeType={ex}&stockPriceSortType=marketValue"
                             f"&page={page}&pageSize={_PAGE_SIZE}")
            if not st:
                break
            for s in st:
                sym = _suffix_ticker(s.get("symbolCode") or s.get("reutersCode"), suf)
                nm = s.get("name")
                if sym and nm and nm != sym:
                    out[sym] = nm
            if len(st) < _PAGE_SIZE:
                break
    if out:
        _cache_write(cache, out)
    return out


# 네이버 데스크탑 업종(industry) API — nationType 별 (사용자 2026-06-14 'CN/HK/JP
# 업종 네이버'). US/KR=이미 처리, TW=네이버 미지원이라 제외. enum: USA|CHN|HKG|JPN|VNM
# (VM probe 확인). 종목 객체에 reutersIndustryName(업종 한글)·koreanCodeName 보유.
# US 도 업종 endpoint 의 koreanCodeName(한글명) 수집용으로 포함 (사용자 2026-06-14
# 'US 52주 종목명 네이버 한글로'). ⚠️ US 업종 enrich 는 여전히 yfinance(사용자 스코프
# 'US 업종 아니야') — _industries_for 가 CN_A/HK/JP 만 네이버 라우팅, US 는 NAME 만.
_UPJONG_NATION = {"CN_A": "CHN", "HK": "HKG", "JP": "JPN", "US": "USA"}
_UPJONG_BASE = "https://stock.naver.com/api/foreign/market"


def _upjong_ticker(sym: str, market: str) -> str:
    """네이버 업종 symbolCode → yfinance 티커. JP +.T · HK 4자리+.HK · CN 6xx=.SS
    그외=.SZ(상하이 600/601/603/688 / 선전 000/002/300 코드대역 휴리스틱)."""
    sym = str(sym or "").strip()
    if not sym:
        return ""
    if market == "JP":
        return f"{sym}.T"
    if market == "HK":
        return f"{sym.zfill(4)}.HK"
    if market == "CN_A":
        return f"{sym}.SS" if sym[:1] == "6" else f"{sym}.SZ"
    return sym


def world_industry_map(market: str, per_industry: int = 100) -> dict:
    """{yfinance 티커 → 업종명(한글)} — 네이버 데스크탑 업종 API(CN/HK/JP, 사용자
    2026-06-14). 전 업종(/upjong/list) 순회하며 reutersIndustryName 수집. 업종은
    안정적 → 7d 디스크 캐시. graceful·429 면역. yfinance _fetch_industries 의
    비-US 대체 — yfinance .info 가 CN A주/소형주 업종을 자주 비워 '업종 —' 이던
    것 해소. (업종 endpoint 응답은 bare list 라 _get_stocks 안 씀)."""
    nat = _UPJONG_NATION.get(market)
    if not nat:
        return {}
    from bot.finviz_client import _cache_write, _cached
    cache = f"naver_industry_{market}.json"
    cached = _cached(cache, ttl=7 * 86400)
    if isinstance(cached, dict) and cached:
        return cached
    import requests
    try:
        cr = requests.get(f"{_UPJONG_BASE}/{nat}/upjong/list", headers=_H, timeout=12)
        codes = cr.json() if cr.status_code == 200 else None
    except Exception as exc:
        log.warning("naver 업종 list (%s): %s", market, exc)
        return {}
    if not isinstance(codes, list):
        return {}
    out: dict = {}
    names: dict = {}      # {티커→koreanCodeName} 동반 캐시 (US 52주 한글명 등)
    for ic in codes:
        code = (ic or {}).get("code") if isinstance(ic, dict) else None
        if not code:
            continue
        try:
            r = requests.get(f"{_UPJONG_BASE}/{nat}/upjong/{code}/list"
                             f"?orderType=marketValue&startIdx=0&pageSize={per_industry}",
                             headers=_H, timeout=10)
            st = r.json() if r.status_code == 200 else None
        except Exception:
            st = None
        for s in (st or []):
            if not isinstance(s, dict):
                continue
            tk = _upjong_ticker(s.get("symbolCode") or s.get("reutersCode"), market)
            ind = s.get("reutersIndustryName")
            nm = s.get("koreanCodeName")
            if tk and ind:
                out[tk] = ind
            if tk and nm:
                names[tk] = nm
    if out:
        _cache_write(cache, out)
    if names:
        _cache_write(f"naver_upjong_name_{market}.json", names)
    return out


def world_upjong_name(market: str) -> dict:
    """{yfinance 티커 → koreanCodeName(한글명)} — 업종 endpoint 빌드 시 동반 캐시.
    US 52주 한글 종목명 등(사용자 2026-06-14 'US 52주 네이버 한글로'). 캐시 없으면
    world_industry_map 빌드를 1회 트리거(name 캐시도 함께 씀). 7d·graceful."""
    if market not in _UPJONG_NATION:
        return {}
    from bot.finviz_client import _cached
    cache = f"naver_upjong_name_{market}.json"
    c = _cached(cache, ttl=7 * 86400)
    if isinstance(c, dict) and c:
        return c
    try:
        world_industry_map(market)        # name 캐시도 함께 기록
    except Exception:
        return {}
    c = _cached(cache, ttl=7 * 86400)
    return c if isinstance(c, dict) else {}


def fetch_intl_sector_movers_naver(market: str, top_n: int = 10) -> dict:
    """{up:[{name,pct}], down:[...], ts, source} — 네이버 업종별 등락(시총가중 평균)
    Top/Bottom (CN/HK/JP, 사용자 2026-06-14 '업종등락 네이버'). 전 업종 순회·각
    업종 상위 종목(시총순 20) 시총가중 등락 → 랭킹. ETF 업종 제외. 1h 디스크 캐시
    (등락 intraday). graceful·429 면역 — 실패 시 빈(호출부 ETF 합성 폴백)."""
    nat = _UPJONG_NATION.get(market)
    if not nat:
        return {"up": [], "down": [], "ts": "", "source": ""}
    from bot.finviz_client import _cache_write, _cached, _now_label
    cache = f"naver_sector_movers_{market}.json"
    c = _cached(cache, ttl=3600)
    if isinstance(c, dict) and (c.get("up") or c.get("down")):
        return c
    import requests
    try:
        codes = requests.get(f"{_UPJONG_BASE}/{nat}/upjong/list", headers=_H, timeout=12).json()
    except Exception as exc:
        log.warning("naver 업종등락 list (%s): %s", market, exc)
        return {"up": [], "down": [], "ts": "", "source": ""}
    if not isinstance(codes, list):
        return {"up": [], "down": [], "ts": "", "source": ""}
    rows: list = []
    for ic in codes:
        if not isinstance(ic, dict):
            continue
        code, name = ic.get("code"), ic.get("industryGroupKor")
        if not code or not name or "ETF" in name:
            continue
        try:
            r = requests.get(f"{_UPJONG_BASE}/{nat}/upjong/{code}/list"
                             f"?orderType=marketValue&startIdx=0&pageSize=20",
                             headers=_H, timeout=8)
            st = r.json() if r.status_code == 200 else None
        except Exception:
            st = None
        if not isinstance(st, list) or not st:
            continue
        num = den = 0.0
        for s in st:
            if not isinstance(s, dict):
                continue
            try:
                mc = float(s.get("marketValue") or 0)
                pct = abs(float(s.get("fluctuationsRatio") or 0))
            except (TypeError, ValueError):
                continue
            ft = str(s.get("compareToPreviousPrice") or "")
            if "FALL" in ft or "LOWER" in ft:
                pct = -pct
            if mc > 0:
                num += mc * pct
                den += mc
        if den > 0:
            rows.append({"name": name, "pct": round(num / den, 2)})
    if not rows:
        return {"up": [], "down": [], "ts": "", "source": ""}
    rows.sort(key=lambda r: r["pct"], reverse=True)
    up = [r for r in rows if r["pct"] > 0][:top_n]
    down = sorted([r for r in rows if r["pct"] < 0], key=lambda r: r["pct"])[:top_n]
    out = {"up": up, "down": down, "ts": _now_label(),
           "source": "네이버 업종(시총가중 등락)"}
    _cache_write(cache, out)
    return out


def _is_real_stock(s: dict) -> bool:
    """실종목만 — ETF/ETN/스팩 제외 (사용자 신고저 정책). stockEndType=='stock'
    + 이름에 '스팩' 없음. (52주 최고엔 ETN/레버리지 상품이 섞여 들어옴)."""
    if str(s.get("stockEndType") or "") != "stock":
        return False
    nm = str(s.get("name") or "")
    return "스팩" not in nm


def fetch_kr_highlow(limit: int = 200) -> dict:
    """KR 52주 신고가/신저가 — 네이버 전종목(한글명 native, sortType 2콜). ETF/ETN/
    스팩 제외. {high, low, ts, source}. graceful — 두 콜 모두 실패 시 high/low 빈
    (호출부가 pykrx 스캔 폴백). 시총순 정렬은 호출부(sort_by_mcap)."""
    from bot.finviz_client import _now_label
    out = {"high": [], "low": [], "ts": _now_label(),
           "source": "네이버 증권 52주 최고/최저(전종목·한글명)"}
    hi = _domestic_paged("high52week", max_items=limit)
    lo = _domestic_paged("low52week", max_items=limit)
    if hi:
        out["high"] = [_kr_row(s) for s in hi if _is_real_stock(s)]
    if lo:
        out["low"] = [_kr_row(s) for s in lo if _is_real_stock(s)]
    return out


def fetch_kr_upper_lower(limit: int = 200) -> dict:
    """KR 상한가/하한가 — 네이버 domestic 상승/하락 랭킹에서 fluctuationsType
    UPPER_LIMIT/LOWER_LIMIT 필터 (사용자 2026-06-13 'KR 모두 네이버'). 시총·거래대금
    ·한글명 전부 native(yfinance enrich 제거 → 429 면역). ETF/ETN/스팩 제외.
    {upper, lower, ts, source}. graceful — 실패 시 빈(호출부 폴백)."""
    from bot.finviz_client import _now_label
    out = {"upper": [], "lower": [], "ts": _now_label(),
           "source": "네이버 증권 상한가/하한가(전종목·한글명·시총·거래대금)"}
    up = _domestic_paged("up", max_items=limit)
    dn = _domestic_paged("down", max_items=limit)
    if up:
        out["upper"] = [_kr_row(s) for s in up if _is_real_stock(s)
                        and "UPPER" in str(s.get("fluctuationsType") or "")]
    if dn:
        out["lower"] = [_kr_row(s) for s in dn if _is_real_stock(s)
                        and "LOWER" in str(s.get("fluctuationsType") or "")]
    return out


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = fetch_kr_highlow()
    print(f"high {len(d['high'])} / low {len(d['low'])} ({d['source']})")
    for r in d["high"][:5]:
        print(" ", json.dumps(r, ensure_ascii=False))
    u = fetch_kr_upper_lower()
    print(f"upper {len(u['upper'])} / lower {len(u['lower'])}")
