"""미국 장전(pre-market)·장후(after-hours) 급등·급락 — **네이버 실시간**(S&P 500).

기존 급등락(`finviz_client.fetch_us_movers`, 정규장 일봉)의 **형제 표면**
(사용자 2026-06-14 '장전/장후도 별도 자식 대시보드'). 정규장 종가 대비
시간외 가격 변화율로 상·하위 산출.

소스 = **네이버 해외(worldstock) overMarketPriceInfo** (사용자 2026-06-16
'야후 안 쓰고 네이버 실시간'). 종목별 overPrice·시간외 등락%(정규장 종가
대비)를 실시간으로 받아 랭킹 — yfinance 30분봉(지연)·Yahoo 의존 제거. VM
probe 2026-06-15 로 AAPL 장후 overPrice 296.10·AFTER_MARKET 확인.

유니버스 = **전시장 정규장 무버**(`_us_movers_universe` — 네이버 worldstock
NASDAQ+NYSE up/down 랭킹 합집합, 사용자 2026-06-16 '전시장 정규장 무버' · 2026-06-17 'AMEX 제외').
시간외 급변은 뉴스주라 정규장 무버 랭킹에 직격으로 잡힘(S&P 500 대형주는
시간외 거의 미동 → 유니버스 부적합). ~480종목 ThreadPool 스캔(over-market
OPEN 인 종목만 집계). 한글명도 랭킹 행에서 native. 시간외 미개장(정규장·장
완전 종료) 시엔 0 행 → SWR 가 직전 스냅샷 서빙. 글리치(|pct|>75%)·페니 컷은
over_pct 가 정규장 종가 대비라 자연 완만(**VM 실측 확인 2026-06-16**: AAPL/TSLA/
NVDA/AMD/MSFT over_pct 가 vs정규장종가에 정확 일치·vs전일종가와 어긋남 → KR 처럼
전일종가 누적 이중계산 아님 → recompute 불요. 그대로 passthrough 정확).

SWR 백그라운드(장-인지 신선도 — 연장거래 창에서만 재스캔, ~480 네이버 콜은
pre/post 세션에만) + 무료·무키·graceful. (TW=盤後정가 고정·KR=시간외단일가는
별도 — 본 표면은 US 전용.)
"""
from __future__ import annotations

import logging
import threading as _threading
import time
from datetime import datetime, timedelta, timezone

from bot.finviz_client import _CACHE_DIR, _cache_write, _cached, _now_label

log = logging.getLogger("bot.prepost_client")

_PREPOST_CACHE = "us_prepost_movers_v2.json"   # 전시장 정규장 무버 (v2: 2026-06-16 정규장
#   거래대금 value 필드 추가 — v1 옛 스냅샷은 value 없어 '—'. 버전 bump 으로 즉시
#   fresh 재계산(30분 TTL stale-serve 대기 회피).
_PREPOST_STATUS = "us_prepost_movers_status.json"
_PREPOST_TOP_N = 30
_PREPOST_TTL = 30 * 60         # 연장거래 창에서 재산출 간격 30분 (movers 와 동일)
_MIN_PRICE = 1.0               # 페니 컷
_MIN_EXT_VOL = 1000            # 거래량 하한(유동성 — 0거래 종목 배제; 네이버는 정규장 누적거래량)
_GLITCH_PCT = 75.0             # 분할/조정 아티팩트 컷 (KLAC 클래스, CLAUDE.md 가드)
_UNIVERSE_PER_DIR = 80         # 시간외 스캔 유니버스 — 거래소·방향당 정규장 무버 상위(×3거래소×2방향≈480)


# ── 순수 함수 (단위테스트) ──────────────────────────────────────────────

def _in_extended_window(now: datetime) -> bool:
    """UTC now 가 미국 장전(4:00–9:30 ET) 또는 장후(16:00–20:00 ET) 창 안인가 —
    순수. EST/EDT 둘 다 여유 커버(±30분 무해 — 닫힌 시장 재스캔은 직전 데이터만
    재확인). 장후는 자정(UTC)을 넘으므로 다음날 새벽 tail(00:00–01:00 UTC)도 포함.

    EDT(UTC-4): 장전 08:00–13:30 · 장후 20:00–24:00 UTC
    EST(UTC-5): 장전 09:00–14:30 · 장후 21:00–01:00 UTC → 합집합으로 커버."""
    wd, h, m = now.weekday(), now.hour, now.minute
    if wd == 5:                       # 토: 금요일 장후 tail(00:00–01:00 UTC)만
        return h < 1
    if wd == 6:                       # 일: 휴장
        return False
    pre = (8, 0) <= (h, m) < (14, 30)         # 장전(여유)
    post = h >= 20                            # 장후 (당일 UTC 20:00~)
    tail = wd >= 1 and h < 1                  # 화~금 00:00–01:00 = 전일 장후 tail
    return pre or post or tail


def _current_session(now: datetime | None = None) -> str:
    """현재 ET 시각의 연장 세션 — 장전(4:00–9:30)='pre' · 장후(16:00–20:00)=
    'post' · 그 외 ''. 순수(단위테스트). 활성 세션이면 그 세션의 오늘 봉을
    우선해 직전(스테일) 세션이 섞이지 않게 함 (사용자 2026-06-15 '장전은 집계
    안 되나' — 이른 장전에 전일 장후가 다수표로 '장후' 라벨되던 것 교정)."""
    from datetime import time as _t
    try:
        from zoneinfo import ZoneInfo
        et = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return ""
    tt = et.time()
    if _t(4, 0) <= tt < _t(9, 30):
        return "pre"
    if _t(16, 0) <= tt < _t(20, 0):
        return "post"
    return ""


def _prepost_fresh(cache_ts: float, now_ts: float | None = None) -> bool:
    """장-인지 신선도 — 연장거래 창에서만 30분 TTL(데이터 변동), 그 밖(정규장·
    완전 종료)엔 직전 스냅샷 fresh 취급(재스캔 0 — 연장 데이터 안 변함). 순수."""
    now = datetime.fromtimestamp(now_ts or time.time(), tz=timezone.utc)
    if _in_extended_window(now):
        return (now.timestamp() - cache_ts) < _PREPOST_TTL
    return True


def _rank_prepost(rows: list, top_n: int = _PREPOST_TOP_N) -> tuple[list, list]:
    """순수 랭킹(단위테스트) — 페니·박거래(연장거래량)·pct 결측·글리치 컷 후
    등락률 상위/하위 각 top_n. 글리치는 스캔 단에서도 드랍하지만 이중 방어."""
    ok = [r for r in rows
          if r.get("pct") is not None
          and abs(r.get("pct") or 0) <= _GLITCH_PCT
          and (r.get("price") or 0) >= _MIN_PRICE
          # 유동성 게이트 = reg_vol(정규장 거래량) 우선, **0/결측이면 NXT vol 로
          # 폴백**. 장전(09:00 정규장 개장 전)엔 정규장 거래량이 0 이라, 옛
          # `reg_vol` 키-default(0 을 그대로 사용)가 전 종목을 컷 → **장전 빈 보드**
          # 버그(사용자 2026-06-16). _one 의 ovol 가드가 실제 NXT 체결을 보장하므로
          # NXT vol 게이트는 안전. 장후엔 reg_vol>0 이라 동작 불변.
          and ((r.get("reg_vol") or r.get("vol")) or 0) >= _MIN_EXT_VOL]
    ups = sorted((r for r in ok if r["pct"] > 0),
                 key=lambda r: r["pct"], reverse=True)[:top_n]
    downs = sorted((r for r in ok if r["pct"] < 0),
                   key=lambda r: r["pct"])[:top_n]
    return ups, downs


def _classify_index(idx):
    """df.index(DatetimeIndex) → (kinds, dates) — 각 봉을 'reg'|'pre'|'post'|'other'
    로 분류 + ET 날짜. tz-naive 면 UTC 가정 후 ET 변환. (kinds·dates 는 ticker 공유
    인덱스라 1회 계산 후 전 종목 재사용)."""
    from datetime import time as _t
    try:
        from zoneinfo import ZoneInfo
        et_tz = ZoneInfo("America/New_York")
    except Exception:
        return [], []
    if idx.tz is None:
        et = idx.tz_localize("UTC").tz_convert(et_tz)
    else:
        et = idx.tz_convert(et_tz)
    kinds, dates = [], []
    for ts in et:
        tt = ts.time()
        if _t(9, 30) <= tt < _t(16, 0):
            kinds.append("reg")
        elif _t(4, 0) <= tt < _t(9, 30):
            kinds.append("pre")
        elif _t(16, 0) <= tt < _t(20, 0):
            kinds.append("post")
        else:
            kinds.append("other")
        dates.append(ts.date())
    return kinds, dates


def _ticker_prepost(closes, vols, kinds, dates) -> dict | None:
    """단일 종목 일중봉 → 최신 연장거래 봉 vs 직전 정규장 종가 등락 (순수-ish,
    pandas Series 입력). 연장거래 없으면 None. 등락률·연장 가격·연장세션 거래량·
    세션('pre'|'post') 반환."""
    import math
    n = len(kinds)
    ext_pos = [i for i in range(n)
               if kinds[i] in ("pre", "post")
               and i < len(closes) and not _isnan(closes.iloc[i])]
    if not ext_pos:
        return None
    last = ext_pos[-1]
    sess = kinds[last]
    ext_price = float(closes.iloc[last])
    reg_pos = [i for i in range(last)
               if kinds[i] == "reg" and not _isnan(closes.iloc[i])]
    if not reg_pos:
        return None
    reg_close = float(closes.iloc[reg_pos[-1]])
    if reg_close <= 0 or ext_price <= 0 or math.isnan(ext_price):
        return None
    pct = round((ext_price / reg_close - 1) * 100, 2)
    # 연장 세션 거래량 = 최신 봉과 같은 날짜·같은 세션 연장봉 합산
    last_date = dates[last]
    ext_vol = 0.0
    for i in ext_pos:
        if dates[i] == last_date and kinds[i] == sess and not _isnan(vols.iloc[i]):
            ext_vol += float(vols.iloc[i])
    return {"price": round(ext_price, 2), "pct": pct,
            "vol": int(ext_vol), "session": sess}


def _isnan(x) -> bool:
    try:
        import math
        return x is None or math.isnan(float(x))
    except Exception:
        return False


# ── 산출 (백그라운드) ───────────────────────────────────────────────────

def _status_write(state: str, **kw) -> None:
    kw.update({"state": state, "ts": time.time(), "ts_label": _now_label()})
    _cache_write(_PREPOST_STATUS, kw)


def prepost_status() -> dict:
    return _cached(_PREPOST_STATUS, ttl=86400) or {}


def _bare_us(tk) -> str:
    """네이버 worldstock 티커 → 무접미사 US 티커(yfinance/enrich 규약). symbolCode
    는 보통 무접미사('CAST')라 그대로, reutersCode 폴백('CAST.O')만 거래소 접미사
    (.O/.N/.A/.OQ/.K/.P) 제거. BRK.B 등 클래스주 점은 보존(거래소 코드 아님)."""
    tk = str(tk or "").strip().upper()
    if "." in tk:
        base, suf = tk.rsplit(".", 1)
        if suf in ("O", "N", "A", "OQ", "K", "P", "PK"):
            return base
    return tk


def _us_movers_universe() -> tuple[list, dict, dict]:
    """시간외 스캔 유니버스 = **전시장(NASDAQ+NYSE) 정규장 무버** — 네이버 worldstock
    up/down 랭킹의 티커 합집합(사용자 2026-06-16 '전시장 정규장 무버'; 2026-06-17
    'AMEX 제외'). 시간외 급변 종목은 뉴스주라 정규장 무버 랭킹에 직격으로 잡힘
    (S&P 500 대형주는 시간외 거의 미동). 한글명도 랭킹 행에서 native 수집(미국 무버/
    신고저 표면과 동일). 반환 (tks, {ticker: 한글명}). graceful — 랭킹 전부 실패 시 빈."""
    from bot.naver_ranking_client import fetch_world_ranking
    tks: list = []
    names: dict = {}
    mcaps: dict = {}
    seen: set = set()
    for ex in ("NASDAQ", "NYSE"):          # AMEX 제외 (사용자 2026-06-17)
        for sort in ("up", "down"):
            for r in fetch_world_ranking(ex, sort, limit=_UNIVERSE_PER_DIR):
                tk = _bare_us(r.get("ticker"))
                if not tk or tk in seen:
                    continue
                seen.add(tk)
                tks.append(tk)
                nm = r.get("name")
                if nm and nm != tk:
                    names[tk] = nm
                # 네이버 랭킹 시총(marketValue/1e8 = 억$) — fast_info(rate-limit
                # 회로차단 1순위) 대신 무료 우선 소스(B 그룹, 2026-06-16).
                if r.get("mcap") is not None:
                    mcaps[tk] = r["mcap"]
    return tks, names, mcaps


def _compute_us_prepost() -> dict:
    """전시장(정규장 무버) 시간외(장전/장후) 급등·급락 TOP30 — **네이버 실시간**
    (worldstock overMarketPriceInfo, 사용자 2026-06-16 '야후 안 쓰고 네이버 실시간').
    유니버스 = 전시장 NASDAQ+NYSE 정규장 무버(AMEX 제외, `_us_movers_universe`, 사용자
    2026-06-16 '전시장 정규장 무버'). 종목별 overPrice·시간외 등락%(정규장 종가
    대비)로 상·하위 산출. over-market OPEN 인 종목만 집계(시간외 미개장·정규장
    시간엔 0 행 → SWR 가 직전 스냅샷 서빙). 백그라운드 전용(~480종목 네이버 스캔,
    ThreadPool). yfinance 미사용."""
    out: dict = {"up": [], "down": [], "ts": _now_label(), "scanned": 0,
                 "session": "", "source": "전시장 정규장 무버 시간외 · 네이버 실시간"}
    tks, names, uni_mcaps = _us_movers_universe()   # 전시장 정규장 무버 (사용자 2026-06-16)
    if not tks:
        log.warning("prepost: universe empty")
        _status_write("failed", detail="universe 전 소스 실패")
        return out
    from concurrent.futures import ThreadPoolExecutor

    from bot.world_quote import fetch_world_quote
    _status_write("running", done=0, total=len(tks), universe=len(tks))

    def _one(tk: str):
        try:
            wq = fetch_world_quote(tk) or {}
            sess = wq.get("over_session") or ""
            op, opct = wq.get("over_price"), wq.get("over_pct")
            if sess and op and opct is not None:
                _rv = wq.get("value")      # 정규장 누적 거래대금($) → 억$
                return {"ticker": tk, "name": names.get(tk, tk),
                        "price": op, "pct": opct,
                        "vol": wq.get("volume"),   # 정규장 누적거래량(유동성 게이트·표시)
                        # 거래대금 = 정규장 누적($→억$, 사용자 2026-06-16 '정규장
                        # 기준이더라도'). 시간외 거래대금 네이버 미제공이라 정규장값.
                        "value": round(_rv / 1e8, 2) if _rv else None,
                        "session": "pre" if "PRE" in sess else "post"}
        except Exception:
            pass
        return None

    rows: list = []
    try:
        log.info("prepost: 전시장 무버 %d종목 네이버 시간외 스캔 시작", len(tks))
        with ThreadPoolExecutor(max_workers=6) as pool:
            for rec in pool.map(_one, tks):
                if rec:
                    rows.append(rec)
        # 현재 ET 세션 우선 (사용자 2026-06-15 '장전 집계 안 되나') — 활성 장전/
        # 장후 창이면 그 세션의 오늘 봉을 가진 종목만 남겨 직전(스테일) 세션을 배제.
        # 단 현재 세션 데이터가 아직 0 이면(이른 장전 등) 폴백으로 전체 유지(빈 페이지
        # 방지) — 그 경우 라벨은 자연히 직전 세션이 됨.
        cur_sess = _current_session()
        if cur_sess:
            pref = [r for r in rows if r.get("session") == cur_sess]
            if pref:
                rows = pref
        ups, downs = _rank_prepost(rows)
        # 시총·업종 백필 — hit 종목만(소수). 시총은 **네이버 랭킹값(uni_mcaps,
        # 억$, 무료) 우선 → 누락분만 fast_info 폴백** (B 그룹 2026-06-16): fast_info
        # 는 rate-limit 회로차단 1순위라 그것만 쓰면 차단 시 전 행 "—". 네이버
        # 랭킹은 유니버스 소스라 hit 대부분 이미 보유.
        try:
            from bot.finviz_client import _fetch_industries, _fetch_mcaps
            hits = [r["ticker"] for r in ups + downs]
            miss = [t for t in hits if uni_mcaps.get(t) is None]
            fi_mcaps = _fetch_mcaps(miss) if miss else {}
            inds = _fetch_industries(hits)
            for r in ups + downs:
                tk = r["ticker"]
                me = uni_mcaps.get(tk)              # 억$ (네이버 랭킹)
                if me is None:
                    mc = fi_mcaps.get(tk)
                    me = round(mc / 1e8, 2) if mc else None
                r["mcap"] = me
                r["ind"] = inds.get(tk)
        except Exception as exc:
            log.warning("prepost: 시총/업종 백필 실패: %s", exc)
        # 비-주식 가지치기 — CEF 펀드·유령티커 제거 + 이중클래스 dedupe(신고저/무버
        # 공용, 사용자 2026-06-14). enrich 후라 시총·업종 정확.
        try:
            from bot.finviz_client import prune_non_stock
            ups, downs = prune_non_stock(ups), prune_non_stock(downs)
        except Exception:
            pass
        out["up"], out["down"] = ups, downs
        out["scanned"] = len(rows)
        # 결과 다수 세션 → 페이지 부제 라벨('장전'|'장후')
        sess_votes = [r.get("session") for r in ups + downs if r.get("session")]
        out["session"] = (max(set(sess_votes), key=sess_votes.count)
                          if sess_votes else "")
        if ups or downs:
            _cache_write(_PREPOST_CACHE, out)
            _status_write("done", up=len(ups), down=len(downs),
                          session=out["session"])
        else:
            _status_write("failed", scanned=len(rows),
                          detail="시간외 행 0 — 장전/장후 미개장(정규장·장 마감) 또는 네이버 시간외 미제공")
    except Exception as exc:
        log.warning("prepost: 산출 실패: %s", exc)
        _status_write("failed", detail=f"{type(exc).__name__}: {exc}")
    return out


_LOCK = _threading.Lock()
_REFRESHING = False


def _kick_refresh() -> None:
    """백그라운드 재계산 — 1개만(stampede 방지)."""
    global _REFRESHING
    with _LOCK:
        if _REFRESHING:
            return
        _REFRESHING = True

    def _run():
        global _REFRESHING
        try:
            _compute_us_prepost()
        except Exception as exc:
            log.warning("prepost: 백그라운드 재계산 실패: %s", exc)
        finally:
            with _LOCK:
                _REFRESHING = False

    _threading.Thread(target=_run, daemon=True, name="us-prepost").start()


def fetch_us_prepost_movers() -> dict:
    """장전/장후 급등·급락 — **동기 계산 절대 안 함**(전시장 무버 ~480 스캔 =
    페이지 hang 금지, movers/highlow SWR 와 동일): 신선 서빙 / stale 서빙 + 백그라운드
    재계산 / 캐시 부재 시 kick 후 'building'. 재발동 백오프(실패 5분·running
    30분). 신선도 = 장-인지(연장거래 창에서만 30분, 그 밖 재스캔 0)."""
    stale = _cached(_PREPOST_CACHE, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / _PREPOST_CACHE).stat().st_mtime
        except OSError:
            mt = 0.0
        if _prepost_fresh(mt):
            return stale
    # 연장거래 창(미국 장전 4:00–9:30 · 장후 16:00–20:00 ET) 밖이면 스캔 안 함
    # (사용자 2026-06-14 '계속 새로 시작'). 주말·휴장에 캐시 부재 시 전시장 무버
    # 스캔을 매 페이지 접근마다 kick → 무의미(직전 거래일 데이터)·무겁고, 배포
    # 재시작에 매번 살해돼 영영 미완 → no-cache → 반복. 창 안일 때만 kick.
    in_win = _in_extended_window(datetime.now(timezone.utc))
    st = prepost_status()
    age = time.time() - (st.get("ts") or 0)
    if not in_win:
        pass                                  # 장 마감 — 스캔 안 함(stale 서빙)
    elif st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        _kick_refresh()
    if stale is not None:
        return stale
    # 창 밖이면 building=False → 페이지가 '연장거래 시간에 확인' 안내(스캔 표시 X).
    return {"up": [], "down": [], "ts": "", "source": "", "session": "",
            "building": in_win, "status": st}


# ── KR 장전·장후 시간외(단일가) 급등·급락 — 미국 prepost 의 KR 버전 ─────────
# (사용자 2026-06-16 'KR 시간외 가격 Top 30'). 네이버 KR domestic overMarketPriceInfo
# (시간외단일가, VM probe 2026-06-16 삼성전자 342,000 AFTER_MARKET 확인) — NXT 수급
# 보드와 별개(이건 가격 등락). 유니버스 = 정규장 무버(네이버 domestic), 종목별
# fetch_kr_quote 로 시간외가·등락% 수집. 무료·무키·rate-limit 면역(네이버).

_KST9 = timezone(timedelta(hours=9))
_KR_PREPOST_CACHE = "kr_prepost_v1.json"
_KR_PREPOST_STATUS = "kr_prepost_status.json"
_KR_UNIVERSE_CACHE = "kr_prepost_universe.json"   # 직전 성공 유니버스(장전 공백 폴백)
_KR_PREPOST_TTL = 2 * 60        # NXT 창 재산출 간격 2분 (사용자 2026-06-16, 옛 5분
#   에서 단축 — NXT 는 연속거래라 5분은 거침). 부하 무: SWR 요청-트리거(미열람 시 0)
#   + _KR_REFRESHING 락(스캔 비중첩·stampede 차단) + 종목당 30초 캐시 + 6-worker
#   풀. 열람 중 ~200종목 네이버 2분당 1회(~100콜/분) — realtime 엔드포인트 안전.
_KR_LOCK = _threading.Lock()
_KR_REFRESHING = False


def _in_kr_extended_window(now_kst: datetime) -> bool:
    """KST now 가 NXT(넥스트레이드) 장전(08:00–09:00) 또는 장후(15:40–20:00) 창
    안인가 — 순수. 평일만(주말 휴장). 창 밖이면 직전 스냅샷 fresh(재스캔 0)."""
    wd, h, m = now_kst.weekday(), now_kst.hour, now_kst.minute
    if wd >= 5:
        return False
    pre = (8, 0) <= (h, m) < (9, 0)
    post = (15, 40) <= (h, m) < (20, 0)
    return pre or post


def _current_kr_session(now_kst: datetime | None = None) -> str:
    """현재 KST 의 NXT 세션 — 'pre'(08:00–09:00)·'post'(15:40–20:00)·''. 순수."""
    now = now_kst or datetime.now(_KST9)
    h, m = now.hour, now.minute
    if (8, 0) <= (h, m) < (9, 0):
        return "pre"
    if (15, 40) <= (h, m) < (20, 0):
        return "post"
    return ""


def _kr_over_session(sess: str) -> str | None:
    """네이버 over_session → 'pre'(PRE_MARKET)·'post'(AFTER_MARKET)·None. 순수.
    ⚠️ REGULAR_MARKET(정규장 시간 NXT 연속거래)·기타는 None → **보드 제외**(사용자
    2026-06-16: 정규장에 강제 산출 시 REGULAR 행이 '장후'로 오분류돼 새어들던 것
    원천 차단). 보드는 장전/장후 시간외만 — 정규장 데이터는 '급등·급락' 보드 소관."""
    s = sess or ""
    if "PRE" in s:
        return "pre"
    if "AFTER" in s:
        return "post"
    return None


def _kr_status_write(state: str, **kw) -> None:
    kw.update({"state": state, "ts": time.time(), "ts_label": _now_label()})
    _cache_write(_KR_PREPOST_STATUS, kw)


def kr_prepost_status() -> dict:
    return _cached(_KR_PREPOST_STATUS, ttl=86400) or {}


def _kr_movers_universe() -> tuple[list, dict, dict]:
    """KR 시간외 스캔 유니버스 = 정규장 무버(네이버 domestic up/down). 반환
    (티커들, {티커: 한글명}, {티커: 시총억}). 시간외 급변은 뉴스주라 정규장 무버에
    잡힘(미국 board 동일 철학). graceful — 빈 랭킹이면 빈 유니버스."""
    from bot.naver_ranking_client import fetch_kr_movers
    d = fetch_kr_movers(limit=200)
    tks: list = []
    names: dict = {}
    mcaps: dict = {}
    seen: set = set()
    for r in (d.get("up") or []) + (d.get("down") or []):
        tk = str(r.get("ticker") or "").strip()    # '005930.KS'/'…​.KQ'(접미사 보존)
        if not tk or tk in seen:
            continue
        seen.add(tk)
        tks.append(tk)
        nm = r.get("name")
        if nm and nm != tk:
            names[tk] = nm
        if r.get("mcap") is not None:
            mcaps[tk] = r.get("mcap")
    # 장전(08:00–09:00, 정규장 개장 전)엔 네이버 정규장 무버 랭킹이 비거나 얇아
    # 유니버스 0 → NXT 장전 스캔이 'universe 실패'로 영영 빈 보드(사용자 'NXT
    # 장전집계 또 안 됨'의 유력 원인). 직전 성공 유니버스(전일 정규장 무버)를
    # 폴백 — NXT 활동 확인용 후보군이라 전일 리스트로 충분(additive·라이브가
    # 차면 no-op). 라이브 성공 시 캐시 갱신.
    # ⚠️ TTL 은 주말·연휴를 넘겨야 함(사용자 2026-06-22 월요일 08:56 'universe 실패'):
    # 24h 면 금요일 장후 캐시가 월요일 장전에 이미 만료(~61h) → 폴백도 빈 보드.
    # 14일로 확장 — 설·추석 최장 연휴(직전 거래일+~6일 휴장)도 커버. 후보군은
    # 매 거래일 성공 시 재기록되므로 평소엔 항상 신선, 긴 휴장 때만 stale 폴백(무해).
    if not tks:
        cached = _cached(_KR_UNIVERSE_CACHE, ttl=1209600)   # 14일(주말·연휴 생존)
        if isinstance(cached, dict) and cached.get("tks"):
            log.info("kr prepost: 라이브 유니버스 0 → 직전 성공 유니버스(%d종목) 폴백",
                     len(cached["tks"]))
            return (cached["tks"], cached.get("names") or {}, cached.get("mcaps") or {})
        return tks, names, mcaps
    _cache_write(_KR_UNIVERSE_CACHE, {"tks": tks, "names": names, "mcaps": mcaps})
    return tks, names, mcaps


def _compute_kr_prepost() -> dict:
    """KR 장전·장후 시간외(단일가) 급등·급락 TOP30 — 네이버 시간외단일가 실시간.
    정규장 무버 유니버스를 종목별 fetch_kr_quote 로 스캔(over-market OPEN 만 집계).
    백그라운드 전용. yfinance 미사용·네이버만."""
    out: dict = {"up": [], "down": [], "ts": _now_label(), "scanned": 0, "session": "",
                 "source": "KR 정규장 무버 NXT 장전·장후 · 네이버 실시간"}
    tks, names, mcaps = _kr_movers_universe()
    if not tks:
        log.warning("kr prepost: universe empty")
        _kr_status_write("failed", detail="universe 실패(네이버 무버 0)")
        return out
    from concurrent.futures import ThreadPoolExecutor

    from bot.naver_quote import fetch_kr_quote
    _kr_status_write("running", total=len(tks))

    def _one(tk: str):
        try:
            q = fetch_kr_quote(tk) or {}     # 접미사 내부 strip
            _ses = _kr_over_session(q.get("over_session") or "")   # pre/post/None(REGULAR 제외)
            op, reg = q.get("over_price"), q.get("reg_close")
            ovol = q.get("over_volume")      # NXT 세션 거래량 — 실제 NXT 체결 신호
            # NXT 보드 = 실제 NXT 체결 종목만 (사용자 2026-06-16 'NXT 장전장후
            # 거래량과 거래대금'). Naver 는 NXT 미체결이면 accumulatedTradingVolume=
            # '-'(→ _num None) → 제외. overPrice 만 있고 NXT 거래량 0 인 phantom
            # (참조가) 랭킹 차단 — 후성 093370/피에스케이홀딩스 031980 류(over '-'
            # 인데 옛 over_volume-or-volume 폴백이 정규장 풀데이 거래량으로 보드를
            # 점령하던 것)가 이 클래스. 신세계 004170(NXT vol 54,419 실체결)만 잔존.
            if _ses and op and reg and reg > 0 and ovol:
                # 순수 NXT move = NXT가 vs 정규장 종가 (사용자 2026-06-16 '신세계
                # 장후가 2천원 싸면 정규장보다 내린 것 → 격차순 고하 30'). Naver
                # over_pct(=fluctuationsRatio)는 KR 에서 전일종가 누적이라 정규장
                # 급등락과 중복 → 정규장 종가 대비로 재계산해 NXT '추가' 움직임만 랭킹.
                pct = round((op / reg - 1) * 100, 2)
                _ov = q.get("over_value")    # NXT 세션 거래대금(원) → 억
                return {"ticker": tk, "name": names.get(tk, tk),
                        "price": op, "pct": pct,
                        # 표시 거래량/거래대금 = NXT 세션 전용(정규장과 별개). 정규장
                        # 폴백 금지(옛 버그) — vol 은 위 가드로 항상 실체결값.
                        "vol": ovol,
                        "value": round(_ov / 1e8, 2) if _ov else None,
                        # 유동성 게이트 전용(미표시) — 정규장 누적거래량으로 페니·
                        # 유령 컷. 표시(NXT)와 게이트(정규장) 분리.
                        "reg_vol": q.get("volume"),
                        "mcap": mcaps.get(tk),
                        "session": _ses}
        except Exception:
            pass
        return None

    rows: list = []
    try:
        log.info("kr prepost: 정규장 무버 %d종목 네이버 시간외 스캔 시작", len(tks))
        with ThreadPoolExecutor(max_workers=6) as pool:
            for rec in pool.map(_one, tks):
                if rec:
                    rows.append(rec)
        cur = _current_kr_session()
        if cur:
            pref = [r for r in rows if r.get("session") == cur]
            if pref:
                rows = pref
        ups, downs = _rank_prepost(rows)
        out["up"], out["down"] = ups, downs
        out["scanned"] = len(rows)
        votes = [r.get("session") for r in ups + downs if r.get("session")]
        out["session"] = max(set(votes), key=votes.count) if votes else ""
        if ups or downs:
            _cache_write(_KR_PREPOST_CACHE, out)
            _kr_status_write("done", up=len(ups), down=len(downs),
                             session=out["session"])
        else:
            _kr_status_write("failed", scanned=len(rows),
                             detail="시간외 행 0 — 장전/장후 미개장(시간외단일가 시간 외)")
    except Exception as exc:
        log.warning("kr prepost: 산출 실패: %s", exc)
        _kr_status_write("failed", detail=f"{type(exc).__name__}: {exc}")
    return out


def _kr_prepost_fresh(cache_ts: float) -> bool:
    """KR 장-인지 신선도 — 시간외 창에서만 2분 TTL(_KR_PREPOST_TTL), 그 밖엔 직전 스냅샷 fresh."""
    if _in_kr_extended_window(datetime.now(_KST9)):
        return (time.time() - cache_ts) < _KR_PREPOST_TTL
    return True


def _kick_kr_refresh() -> None:
    global _KR_REFRESHING
    with _KR_LOCK:
        if _KR_REFRESHING:
            return
        _KR_REFRESHING = True

    def _run():
        global _KR_REFRESHING
        try:
            _compute_kr_prepost()
        except Exception as exc:
            log.warning("kr prepost: 백그라운드 재계산 실패: %s", exc)
        finally:
            with _KR_LOCK:
                _KR_REFRESHING = False

    _threading.Thread(target=_run, daemon=True, name="kr-prepost").start()


def fetch_kr_prepost_movers() -> dict:
    """KR 장전/장후 시간외 급등·급락 — 동기 계산 안 함(SWR, US prepost 동일):
    신선/스테일 서빙 + 시간외 창에서만 백그라운드 재계산. 재발동 백오프."""
    stale = _cached(_KR_PREPOST_CACHE, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / _KR_PREPOST_CACHE).stat().st_mtime
        except OSError:
            mt = 0.0
        if _kr_prepost_fresh(mt):
            return stale
    in_win = _in_kr_extended_window(datetime.now(_KST9))
    st = kr_prepost_status()
    age = time.time() - (st.get("ts") or 0)
    if not in_win:
        pass
    elif st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        _kick_kr_refresh()
    if stale is not None:
        return stale
    return {"up": [], "down": [], "ts": "", "source": "", "session": "",
            "building": in_win, "status": st}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = _compute_us_prepost()
    print(f"session={d.get('session')} scanned={d.get('scanned')} "
          f"up={len(d.get('up', []))} down={len(d.get('down', []))}")
    for r in (d.get("up", [])[:5] + d.get("down", [])[:5]):
        print(" ", r.get("ticker"), r.get("name"), f"{r.get('pct'):+.2f}%",
              "vol", r.get("vol"))
