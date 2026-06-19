"""JP/CN/HK 52주 신고가/신저가 — TW 패턴 일반화 (사용자 2026-06-13 '다른 나라도
모두 적용'). 유니버스 = bot.market 의 _*_INDUSTRY_PEERS curated 주요종목(시장당
~50-100, 이미 검증된 리스트). finviz_client._compute_highlow_from(US 신고저
스캐너) 재사용 + SWR(신선 30분/스테일+백그라운드 킥). **동기 계산 안 함**.

TW 는 별도(twse STOCK_DAY_ALL ~1000 전종목). JP/CN/HK 는 peer 주요종목 universe
— 풀시장은 인덱스 구성종목 소스 추가 시 확장(향후). VM yfinance 검증(10/10).
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# market → (peer 맵 속성명, 캐시명, 상태명, 위젯 라벨, native 접미사 필터)
# 접미사 필터 = peer 맵에 섞인 해외 비교군 제외(KR 반도체에 TSM/NVDA 등).
# JP/CN/HK 는 맵이 이미 native-only 라 필터가 no-op(검증됨).
_CFG = {
    "JP": ("_JP_INDUSTRY_PEERS", "highlow_jp_v3.json",
           "jp_highlow_status.json", "일본 주요종목", (".T",)),
    # CN_A 52주 재도입 (사용자 2026-06-17 '중국도 같은 방식으로 → CSI300+500') —
    # 옛 2026-06-14 제거 번복. **CSI 300 + CSI 500**(대형+중형 ~800): AKShare
    # list_csi300_500 → yfinance 1y 스캔(_universe CN_A 분기). 전 A주(~5천)는
    # 소형주 yfinance 1년 커버 빈약해 제외 — CSI300+500 은 커버리지 양호(dense).
    # AKShare 미설치/실패 시 peer(_CN_A_INDUSTRY_PEERS ~64) 폴백. 슬롯 :30 분산.
    "CN_A": ("_CN_A_INDUSTRY_PEERS", "highlow_cn_v1.json",
             "cn_highlow_status.json", "중국 CSI300+500", (".SS", ".SZ")),
    "HK": ("_HK_INDUSTRY_PEERS", "highlow_hk_v4.json",
           "hk_highlow_status.json", "홍콩 주요종목", (".HK",)),
    # KR — 사용자 2026-06-13 '한국도 신고가신저가'. KIS 신고저 순위 엔드포인트
    # (1콜·더 쌈)는 VM 검증 대기 → 우선 검증된 yfinance 유니버스 스캔으로.
    "KR": ("_KR_INDUSTRY_PEERS", "highlow_kr_v2.json",
           "kr_highlow_status.json", "한국 주요종목", (".KS", ".KQ")),
}
# v3 (2026-06-14): 네이버 이름맵 + 거래대금(value) 반영 — stale 장-밖 캐시 강제
# 재스캔(옛 v2 는 이름/거래대금 비어있던 포맷).
# 신선도는 시장-인지(finviz_client._session_fresh, 장중 1h / 장 밖 마지막 마감
# 이후 재스캔 0)로 통일 — 사용자 2026-06-13 '모두 장중에만 1h'.
_running: dict[str, bool] = {}
_lock = threading.Lock()


def _cap_by_liquidity(full: list[str], cap: int, market: str) -> list[str]:
    """전종목을 네이버 worldstock 시총 상위 N 으로 캡 — yfinance 1y 스캔 부하↓.
    HK 코드(5자리 '00700')↔yfinance(4자리 '0700')·JP 4자리는 선행 0 제거 정수로
    매칭. world_stock_map 부재 시 원본 앞 N(저번호=대개 메인보드 대형주) 폴백."""
    try:
        from bot.naver_ranking_client import world_stock_map
        wsm = world_stock_map(market)
    except Exception:
        wsm = {}
    if not wsm:
        return full[:cap]
    mc: dict = {}
    for k, v in wsm.items():
        c = str(k).split(".")[0].upper()
        if not c:
            continue
        cap_v = (v or {}).get("mcap") or 0.0
        mc[c] = cap_v                       # 원 코드(영숫자 285A 키옥시아 포함)
        if c.isdigit():
            mc[str(int(c))] = cap_v         # 선행0 정규화 별칭(JP 4자리·HK pad)

    def _rank(t: str) -> float:
        # 영숫자 코드(285A)도 시총으로 랭크 — 옛 isdigit-only 는 신규 대형주를 0
        # (최하위)로 떨궈 캡 적용 시 탈락시켰음(사용자 2026-06-19).
        c = str(t).split(".")[0].upper()
        key = str(int(c)) if c.isdigit() else c
        return -(mc.get(key) or mc.get(c) or 0.0)
    return sorted(full, key=_rank)[:cap]


def _cap_by_liquidity_hk(full: list[str], cap: int) -> list[str]:
    """HK 캡 — _cap_by_liquidity(market='HK') 하위호환 별칭."""
    return _cap_by_liquidity(full, cap, "HK")


def _universe(market: str) -> tuple[list[str], dict]:
    """peer 맵의 unique 티커(주요종목). names 는 ticker 기본(맵에 명칭 없음)."""
    cfg = _CFG.get(market)
    if not cfg:
        return [], {}
    # JP/HK: 공식 상장목록 전종목 우선 (사용자 2026-06-13 full-market), 실패 시
    # peer 폴백. 이름=ticker(번역 백필이 한글명 채움). CN_A 는 의도적 peer-only
    # (재도입 2026-06-17 — 전종목 yfinance 1y 는 커버리지·부하 리스크라 주요종목만).
    if market in ("JP", "HK"):
        try:
            from bot.intl_universe import full_universe
            full = full_universe(market)
            if len(full) > 100:
                if market in ("HK", "JP"):
                    # 캡 = env HIGHLOW_UNIVERSE_CAP, **기본 5000 = 사실상 전종목**
                    # (사용자 2026-06-16 '전시장 다'). JP~2500·HK~2000 이라 기본값이면
                    # 무캡(전종목). ⚠️ yfinance 1y full 스캔은 ~14분(JP)·rate-limit
                    # 위험이나, 이제 EOD 백그라운드(장 마감 후 1회·stagger·circuit-
                    # breaker 보호)라 감내. 문제 시 env 로 하향(예 900 = 시총 상위만,
                    # 네이버 worldstock 시총 정규화 캡). CN_A 는 차단으로 peer-only.
                    import os as _os
                    _cap = int(_os.getenv("HIGHLOW_UNIVERSE_CAP", "5000"))
                    if len(full) > _cap:
                        full = _cap_by_liquidity(full, _cap, market)
                return full, {t: t for t in full}
        except Exception as exc:
            log.warning("intl full_universe %s: %s", market, exc)
    if market == "CN_A":
        # CN = CSI 300 + CSI 500 (사용자 2026-06-17 'CSI300+500') — 대형+중형 ~800.
        # 전 A주(~5천)는 소형주를 yfinance 가 1년 일봉으로 잘 안 줘 신고저가 듬성듬성
        # 했음 → yfinance 커버리지 양호한 대형·중형만(dense·가벼움). AKShare 미설치/
        # 실패 시 아래 peer 폴백(주요종목 ~64, 회귀 0). 캡=HIGHLOW_UNIVERSE_CAP.
        try:
            from bot.akshare_client import list_csi300_500
            csi = list_csi300_500()              # {ticker: 中文명}, AKShare
            if len(csi) > 100:
                full = list(csi.keys())
                import os as _os
                _cap = int(_os.getenv("HIGHLOW_UNIVERSE_CAP", "5000"))
                if len(full) > _cap:
                    full = _cap_by_liquidity(full, _cap, "CN_A")
                return full, {t: csi.get(t, t) for t in full}
        except Exception as exc:
            log.warning("intl CN CSI300+500 universe (AKShare): %s", exc)
        # AKShare 부재/실패 → 아래 peer 폴백으로 진행(회귀 0)
    try:
        from bot import market as mkt
        peers = getattr(mkt, cfg[0], {}) or {}
    except Exception as exc:
        log.warning("intl highlow universe error (%s): %s", market, exc)
        return [], {}
    suffix = cfg[4] if len(cfg) > 4 else None   # native 시장 접미사(해외 비교군 제외)
    seen, uni, names = set(), [], {}
    for vals in peers.values():
        for x in (vals if isinstance(vals, (list, tuple)) else [vals]):
            t = str(x[0] if isinstance(x, (list, tuple)) else x).strip()
            if not t or t in seen:
                continue
            if suffix and not t.endswith(suffix):
                continue
            seen.add(t)
            uni.append(t)
            names[t] = t
    return uni, names


def _status_write(market: str, state: str, **kw) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_CFG[market][2], {"state": state, "ts": time.time(), **kw})
    except Exception:
        pass


def intl_highlow_status(market: str) -> dict:
    try:
        from bot.finviz_client import _cached
        return _cached(_CFG[market][2], ttl=86400) or {}
    except Exception:
        return {}


def _kr_full_universe() -> tuple[list[str], dict]:
    """KR 전종목(KOSPI+KOSDAQ 주식) 코드+한글명 — pykrx(get_market_price_change
    가 종목명 컬럼·시장당 1콜). ETF 미포함(주식만), SPAC(스팩) 제외. 7d 디스크
    캐시. creds/pykrx 부재 시 ([],{}) → 호출부 폴백. (사용자 2026-06-13 — KIS
    near-highlow 캡 ~30이 ETF/SPAC에 잠식돼 실종목 1~3개뿐 → 전종목 스캔으로)."""
    from bot.finviz_client import _cached, _cache_write
    cache_name = "kr_full_universe.json"
    c = _cached(cache_name, ttl=7 * 86400)
    if isinstance(c, list) and len(c) > 500:
        return [x[0] for x in c], {x[0]: x[1] for x in c}
    try:
        from bot.pykrx_client import _quiet_pykrx_logging, krx_login_ready
        if not krx_login_ready():
            return [], {}
        _quiet_pykrx_logging()
        from datetime import datetime, timedelta
        from pykrx import stock as _pk
        d = datetime.now()
        ds, ds_prev = d.strftime("%Y%m%d"), (d - timedelta(days=10)).strftime("%Y%m%d")
        pairs: list = []
        for mkt, suf in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
            df = _pk.get_market_price_change(ds_prev, ds, market=mkt)
            if df is None or "종목명" not in df:
                continue
            for code in df.index:
                nm = str(df.loc[code, "종목명"]).strip()
                if not nm or "스팩" in nm:        # SPAC 제외
                    continue
                pairs.append([f"{code}{suf}", nm])
        if len(pairs) > 500:
            _cache_write(cache_name, pairs)
            return [p[0] for p in pairs], {p[0]: p[1] for p in pairs}
    except Exception as exc:
        log.warning("kr full universe (pykrx): %s", exc)
    return [], {}


def _compute_kr_full() -> None:
    """KR 52주 신고저 — **네이버 우선**(전종목·한글명·1콜씩, 사용자 2026-06-13
    '신고가 신저가는 네이버에 있어'). 네이버 front-api 가 ready 52주 최고/최저를
    주므로 pykrx+yfinance 전종목 스캔(수 분) 불필요. 네이버 실패 시 pykrx 스캔
    폴백(회귀 0). 한글명 둘 다 native(번역 불요)."""
    from bot.finviz_client import _cache_write, _compute_highlow_from
    # 1) 네이버 — 빠름(1콜씩)·한글명·ETF/ETN/스팩 제외
    try:
        from bot.naver_ranking_client import fetch_kr_highlow
        nv = fetch_kr_highlow()
        if nv.get("high") or nv.get("low"):
            _cache_write(_CFG["KR"][1], nv)
            _status_write("KR", "done", high=len(nv["high"]),
                          low=len(nv["low"]), src="naver")
            return
    except Exception as exc:
        log.warning("naver KR 52w 실패 → pykrx 폴백: %s", exc)
    # 2) 폴백: pykrx 전종목 → yfinance 당일 52주 갱신 스캔
    uni, names = _kr_full_universe()
    if not uni:                                   # pykrx 부재 → peer-83 폴백
        uni, names = _universe("KR")
        label = "한국 주요종목(yfinance · pykrx 폴백)"
        src = "peer"
    else:
        label = "한국 전종목(pykrx 목록·yfinance 당일 52주 신고가/신저가 갱신)"
        src = "krx-full"
    if not uni:
        _status_write("KR", "failed", detail="pykrx + peer empty")
        return
    _status_write("KR", "running", total=len(uni))
    out = _compute_highlow_from(uni, names, _CFG["KR"][1], label, "KR")
    _status_write("KR", "done", high=len(out.get("high", [])),
                  low=len(out.get("low", [])), src=src)


def _compute_kr_kis() -> None:
    """(레거시 — _compute_kr_full 로 대체, 2026-06-13) KR 52주 신고저 = KIS
    near-new-highlow + yfinance mcap/ind 백필. KIS 캡 ~30이 ETF/SPAC에 잠식돼
    실종목이 1~3개뿐이라 전종목 스캔(_compute_kr_full)으로 교체. 보존(미사용)."""
    from bot import kis_client
    from bot.finviz_client import (_cache_write, _compute_highlow_from,
                                   _fetch_industries, _fetch_mcaps, _now_label)
    raw = kis_client.fetch_kr_new_highlow()
    if not raw.get("high") and not raw.get("low"):
        # KIS 빈 결과(creds 부재 등) → 기존 peer-83 yfinance 스캔 폴백
        uni, names = _universe("KR")
        if uni:
            _status_write("KR", "running", total=len(uni))
            out = _compute_highlow_from(
                uni, names, _CFG["KR"][1],
                "한국 주요종목 산출(yfinance · KIS 폴백)", "KR")
            _status_write("KR", "done", high=len(out.get("high", [])),
                          low=len(out.get("low", [])), src="peer")
        else:
            _status_write("KR", "failed", detail="KIS empty + peer empty")
        return
    try:
        from bot.market import normalize_kr_ticker_suffix as _norm
    except Exception:
        _norm = None
    # ETF/ETN 견고 제외 — pykrx 주식 코드셋(get_market_ticker_list = 주식만,
    # ETF/ETN 미포함)과 교차. WON/KoAct 등 브랜드 키워드 누락분까지 제거.
    # SPAC 은 주식이라 셋에 있음 → KIS _nhl_is_etf_bond '스팩' 필터가 별도 제거.
    # creds/pykrx 부재로 셋이 비면 교차 생략(키워드 필터에 의존, no-op).
    _codes: set = set()
    try:
        from bot.market import _kr_market_code_sets
        _ks, _kq = _kr_market_code_sets()
        _codes = _ks | _kq
    except Exception:
        _codes = set()
    if _codes:
        raw["high"] = [o for o in raw["high"] if str(o.get("code") or "") in _codes]
        raw["low"] = [o for o in raw["low"] if str(o.get("code") or "") in _codes]

    def _conv(lst):
        items = []
        for o in lst:
            code = str(o.get("code") or "")
            tk = f"{code}.KS"
            if _norm:
                try:
                    tk = _norm(tk)
                except Exception:
                    pass
            items.append({"ticker": tk, "name": o.get("name") or tk,
                          "price": o.get("price"), "pct": o.get("pct"),
                          "vol": o.get("vol")})
        return items
    high, low = _conv(raw["high"]), _conv(raw["low"])
    allt = [it["ticker"] for it in high + low]
    try:
        mcaps, inds = _fetch_mcaps(allt), _fetch_industries(allt)
        for it in high + low:
            mc = mcaps.get(it["ticker"])
            it["mcap"] = round(mc / 1e8, 2) if mc else None   # 억원
            it["ind"] = inds.get(it["ticker"])
    except Exception as exc:
        log.warning("intl highlow KR mcap/ind 백필: %s", exc)
    out = {"high": high, "low": low, "ts": _now_label(),
           "source": "KIS 신고가/신저가 근접(전 시장 스캔)"}
    _cache_write(_CFG["KR"][1], out)
    _status_write("KR", "done", high=len(high), low=len(low), src="kis")


def _compute(market: str) -> None:
    try:
        if market == "KR":
            _compute_kr_full()
            return
        from bot.finviz_client import _compute_highlow_from
        uni, names = _universe(market)
        if not uni:
            _status_write(market, "failed", detail="universe empty")
            return
        _status_write(market, "running", total=len(uni))
        out = _compute_highlow_from(
            uni, names, _CFG[market][1],
            f"{_CFG[market][3]} 산출(yfinance · 당일 52주 고저 갱신)", market)
        _status_write(market, "done", high=len(out.get("high", [])),
                      low=len(out.get("low", [])))
    except Exception as exc:
        log.warning("intl highlow compute (%s): %s", market, exc)
        _status_write(market, "failed", detail=str(exc)[:80])
    finally:
        with _lock:
            _running[market] = False


def _kick(market: str) -> None:
    with _lock:
        if _running.get(market):
            return
        _running[market] = True
    threading.Thread(target=_compute, args=(market,), daemon=True,
                     name=f"intl-highlow-{market}").start()


def fetch_intl_highlow(market: str) -> dict:
    """JP/CN_A/HK/KR 52주 신고가/신저가 — **동기 계산 안 함**. 시장-인지 신선도
    (KR=장중 30초[네이버] / JP·HK=장중 1h[yfinance 스캔] / 장 밖 마지막 마감 이후
    재스캔 0) 즉시 / 스테일+백그라운드 킥 / 캐시부재 building. 실패 5분 백오프·
    진행중 30분 dedup. {high,low,ts,source,building,status}."""
    if market not in _CFG:
        return {"high": [], "low": [], "ts": "", "source": "", "building": False}
    from bot.finviz_client import (_CACHE_DIR, _HL_INTRA_TTL, _MOVERS_INTRA_TTL,
                                   _cached, _session_fresh)
    cache = _CFG[market][1]
    # 시장-인지 신선도: KR=네이버(fetch_kr_highlow 1콜씩, 야후 무관) → **장중 30초**
    # (사용자 2026-06-15 '한국 신고저 네이버니 실시간'). JP/CN/HK=yfinance 유니버스
    # 스캔(heavy·수 분) → 장중 1h 유지. 장 밖 마지막 마감 이후 재스캔 0(전 시장 공통).
    _intra = _MOVERS_INTRA_TTL if market == "KR" else _HL_INTRA_TTL
    stale = _cached(cache, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / cache).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh(market, mt, _intra):
            return stale
    st = intl_highlow_status(market)
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        from bot.finviz_client import yf_paused
        if not yf_paused():        # YF_PAUSE → 재산출 kick 안 함(스테일 유지)
            _kick(market)
    if stale is not None:
        return {**stale, "building": st.get("state") == "running"}
    return {"high": [], "low": [], "ts": "", "source": "",
            "building": True, "status": st}
