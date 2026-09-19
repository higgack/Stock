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

# ⚠️ 캐시 **경로**는 이 모듈에 없다 — `_cached`·`_cache_write`·`cache_age_sec` 가
# 각자 `finviz_client._CACHE_DIR` 를 읽는다. 그러니 테스트가 `prepost_client.
# _CACHE_DIR` 를 갈아끼워 봐야 **아무것도 리다이렉트되지 않는다**. 그래서 그 이름을
# 여기로 import 하지 않는다 — 있으면 다음 사람이 그걸 갈면 되는 줄 안다(#53·#55).
from bot.finviz_client import _cache_write, _cached, _now_label, cache_age_sec

log = logging.getLogger("bot.prepost_client")

# 저장분을 읽을 때 쓰는 "만료 없음" — 이 보드의 데이터는 **연장거래 창에서만**
# 생기고 창 밖에선 재스캔을 아예 안 한다(아래 `fetch_*` 의 `if not in_win: pass`).
# 그래서 24h TTL 은 금요일 마지막 집계를 토요일 같은 시각에 죽여 **주말·연휴 내내
# 보드를 정의상 빈칸**으로 만든다(2026-09-19 실측 재현: 금 19:52 집계 → **토 19:52
# 에 폐기** → 사용자가 본 토 22:5x 화면이 "데이터가 없습니다"). 낡은 값이 빈 값보다
# 낫고 낡았다는 사실은 화면 라벨이 말한다(사용자 2026-09-19 "장전장후 시간대가
# 아니라면 가장 최종데이터를 그대로 남겨줘" · #41 여유로 사실을 덮지 말 것 ·
# #171 가드가 '못 만든다'로 끝나면
# 그 자리가 영원히 비는지 먼저 물을 것 · #384 만료 맵은 버리지 않는다).
_STORED_FOREVER = float("inf")

# `running` 도장의 유효기간 — 재발동 백오프와 **같은 값**을 쓴다(#38). 스캔
# 프로세스가 중간에 죽으면 그 도장이 남는데, 저장분과 함께 상태 파일도 영구
# 보존하게 되면서 화면이 '진행 중'이라고 **영원히** 거짓말할 수 있다(#25 늘 뜨는
# 배지는 아무것도 안 재는 것과 같다). 이 시각을 넘기면 다음 창 접근이 어차피
# 다시 kick 하므로 두 축이 같은 값이어야 갈리지 않는다.
_RUNNING_STALE_SEC = 1800

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


def _prepost_live(age_sec: float | None, now_ts: float | None = None) -> bool:
    """이 스냅샷이 **지금 연장거래 창의 라이브**인가 — 창 안 + TTL 안일 때만 참.

    옛 이름은 `_prepost_fresh` 였고 창 **밖**을 True 로 돌려줬다. 그 True 의 뜻은
    "재스캔할 필요가 없다"(창 밖엔 새 데이터가 안 생긴다)였는데, 이름이 '신선'이라
    화면에서 '최신'으로 읽혀 저장분에 아무 라벨도 안 붙었다 — 한 이름이 두 뜻을
    대표하면 한쪽은 반드시 거짓말이다(#34). 재스캔 여부는 호출부가 `in_win` 으로
    따로 판정한다. 나이를 못 재면 라이브라고 **주장하지 않는다**(#54·#165). 순수."""
    if age_sec is None:
        return False
    now = datetime.fromtimestamp(now_ts or time.time(), tz=timezone.utc)
    return _in_extended_window(now) and age_sec < _PREPOST_TTL


def _rank_prepost(rows: list, top_n: int = _PREPOST_TOP_N) -> tuple[list, list]:
    """순수 랭킹(단위테스트) — 페니·박거래(연장거래량)·pct 결측·글리치 컷 후
    등락률 상위/하위 각 top_n. 글리치는 스캔 단에서도 드랍하지만 이중 방어."""
    ok = [r for r in rows
          if r.get("pct") is not None
          and abs(r.get("pct") or 0) <= _GLITCH_PCT
          and (r.get("price") or 0) >= _MIN_PRICE
          # 유동성 게이트 = reg_vol(정규장 거래량) 우선, **0/결측이면 시간외
          # vol 로 폴백**. 장전(09:00 정규장 개장 전)엔 정규장 거래량이 0 이라, 옛
          # `reg_vol` 키-default(0 을 그대로 사용)가 전 종목을 컷 → **장전 빈 보드**
          # 버그(사용자 2026-06-16). _one 의 ovol 가드가 실제 시간외 체결을
          # 보장하므로 시간외 vol 게이트는 안전. 장후엔 reg_vol>0 이라 동작 불변.
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
    """마지막 스캔의 결과 — **나이로 버리지 않는다**(`_STORED_FOREVER`).

    재발동 백오프는 이미 `age < 300`·`age < _RUNNING_STALE_SEC` 로 **나이를 직접**
    보므로 상한을 없애도 kick 동작은 한 글자도 안 바뀐다. 바뀌는 것은 화면이다 —
    옛 24h 상한은 저장분과 함께 **실패 사실까지** 지워, 파이프라인이 깨진 채 보드가
    건강해 보이게 했다(#41 여유로 사실을 덮지 말 것 · #380 "다음 갱신에 나옵니다"가
    지킬 수 없는 약속이 되는 자리). `running` 이 영원히 남지 않게 거르는 것은
    `scan_state` 다 — 이 함수는 **파일에 있는 사실**만 돌려준다.
    """
    return _cached(_PREPOST_STATUS, ttl=_STORED_FOREVER) or {}


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
    30분). 신선도 = 장-인지(연장거래 창에서만 30분, 그 밖 재스캔 0).

    저장분은 **나이로 버리지 않는다**(`_STORED_FOREVER`) — 창 밖에선 재스캔을
    안 하므로 TTL 을 두면 주말·연휴가 정의상 빈칸이 된다. 낡았다는 사실은
    payload 의 `stale`·`stale_min`·`in_window` 가 말하고 화면이 그걸 따른다."""
    # 창 판정과 나이는 **한 번만** 재서 아래 전부가 같은 것을 본다 — 두 번 물으면
    # 그 사이 갱신된 값의 나이를 옛 값에 붙인다(#160).
    now = datetime.now(timezone.utc)
    # 연장거래 창(미국 장전 4:00–9:30 · 장후 16:00–20:00 ET) 밖이면 스캔 안 함
    # (사용자 2026-06-14 '계속 새로 시작'). 주말·휴장에 캐시 부재 시 전시장 무버
    # 스캔을 매 페이지 접근마다 kick → 무의미(직전 거래일 데이터)·무겁고, 배포
    # 재시작에 매번 살해돼 영영 미완 → no-cache → 반복. 창 안일 때만 kick.
    in_win = _in_extended_window(now)
    # 나이를 **먼저** 잰다 — 읽는 사이 재집계가 끼어도 판정이 '저장분' 쪽으로
    # 기울지 '라이브' 쪽으로 기울지 않는다(#160 을 안전한 쪽으로).
    snap_age = cache_age_sec(_PREPOST_CACHE)
    stale = _cached(_PREPOST_CACHE, ttl=_STORED_FOREVER)
    live = _prepost_live(snap_age, now.timestamp())
    # 상태도 **여기서 한 번** 읽어 저장분 경로가 같이 싣는다 — 마지막 스캔이
    # 실패했으면 화면이 "다음 창에서 자동 갱신됩니다"라고 약속하면 안 된다(#380).
    st = prepost_status()

    def _serve(d: dict) -> dict:
        """저장분이면 **나이를 같이** 싣는다 — 화면이 '최신'으로 그리지 않게
        (#43·#163 되살린 값에는 기준시각을 반드시 같이). 나이는 음수가 될 수
        없다 — 시계가 뒤로 가면 `-1분 전` 이 찍힌다(#34)."""
        return {**d, "stale": not live, "in_window": in_win, "status": st,
                "stale_min": (max(0, int(snap_age // 60))
                              if snap_age is not None else None)}

    if stale is not None and (live or not in_win):
        return _serve(stale)
    age = time.time() - (st.get("ts") or 0)
    if not in_win:
        pass                                  # 장 마감 — 스캔 안 함(stale 서빙)
    elif st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < _RUNNING_STALE_SEC:
        pass
    else:
        _kick_refresh()
    if stale is not None:
        return _serve(stale)
    # 창 밖이면 building=False → 페이지가 '연장거래 시간에 확인' 안내(스캔 표시 X).
    # 저장분이 **아예 없는** 유일한 경로 — 그래서 stale 이 아니라 '없음'이다(#82).
    # `stored_unreadable` = 파일은 있는데 내용을 못 읽었다(`_cache_write` 는
    # truncate 후 쓰기라 쓰다 만 파일이 실재한다, #379) — '한 번도 집계한 적
    # 없음'과 처방이 다르다(#82·#54).
    return {"up": [], "down": [], "ts": "", "source": "", "session": "",
            "building": in_win, "status": st, "stale": False,
            "stale_min": None, "in_window": in_win,
            "stored_unreadable": snap_age is not None}


# ── KR 장전·장후 시간외(단일가) 급등·급락 — 미국 prepost 의 KR 버전 ─────────
# (사용자 2026-06-16 'KR 시간외 가격 Top 30'). 네이버 KR domestic overMarketPriceInfo
# (시간외단일가, VM probe 2026-06-16 삼성전자 342,000 AFTER_MARKET 확인) — `/nxt`
# 수급 보드와 별개(이건 가격 등락). 유니버스 = 정규장 무버(네이버 domestic), 종목별
# fetch_kr_quote 로 시간외가·등락% 수집. 무료·무키·rate-limit 면역(네이버).

_KST9 = timezone(timedelta(hours=9))
# 시간외 보드는 **한 장**이라 캐시·상태도 한 벌이다(사용자 2026-09-17 "합치기로
# 하자 … 미국처럼 장후로"). 2026-09-16~17 에 잠깐 거래소별로 갈라 뒀던
# `kr_after_krx_*.json` 은 더 쓰지 않는다 — KRX 창이 NXT 창의 부분집합이라 그
# 보드가 한 행도 더 내놓지 못했고, 남은 건 재지 않은 거래소 라벨뿐이었다
# (§kr_session 합집합 주석 · #373b·#375).
_KR_PREPOST_CACHE = "kr_prepost_v1.json"
_KR_PREPOST_STATUS = "kr_prepost_status.json"


def venue_attribution_note() -> str:
    """이 보드가 **무엇을 재고 있는지**를 화면이 말하는 줄(순수).

    ⚠️ 2026-09-17 `kr_board_probe` VM 실측으로 **절반이 확정**됐다(#79 그
    경로가 실제로 실행됐나 — 프로브가 답했다):
    (a) 폴링 응답의 전 키 37개 어디에도 `KRX`/`NXT` 를 **이름으로 가르는
        필드가 없다**. `stockExchangeType` 은 코스피/코스닥(시장) 구분이고
        (`code: KS` · `startTime 0900` · `endTime 1530`), 시간외 블록은
        `overMarketPriceInfo` **하나**뿐이다.
    (b) `integratedPriceInfo` 는 거래소 통합이 아니라 **본체 + 시간외
        합산**이다 — 삼성전자·SK하이닉스의 거래량·거래대금 네 쌍이 원 단위
        까지 맞았다(11,438,019 + 4,948,352 = 16,386,371 등).
    ⚠️ **여전히 안 잰 것**: 그 시간외 블록이 KRX 체결인지 NXT 체결인지.
    (b) 의 산수는 '본체' 가 KRX 정규장인지 KRX 전체인지를 못 가르므로 두
    가설이 같은 수치를 낸다(#255) — 그러니 거래소는 **주장하지 않는다**
    (#165). 다른 엔드포인트도 안 쟀다(#274 못 보는 축).

    ⚠️⚠️ 그래서 2026-09-17 에 보드를 **한 장으로 합쳤다**(사용자 "합치기로
    하자 … 미국처럼 장후로"). 잠깐 두었던 거래소별 두 보드는 (a) 때문에 같은
    블록을 같은 유니버스로 읽어 **정의상 같은 목록**을 냈는데, 그게 화면에서는
    "두 시장의 시간외가 같다"는 **시장에 대한 주장**으로 읽혔다 — 우리 구현의
    결과를 시장 사실처럼 적은 것이다(#375·#34). 게다가 KRX 창(16:00–20:00)은
    NXT 창(08:00–09:00 · 15:40–20:00)의 **진부분집합**이라 KRX 보드는 한 행도
    더 내놓을 수 없었고, 고유 산출은 하루 80분의 빈 화면과 재지 않은 라벨
    뿐이었다. 한 장은 **우리가 지지할 수 있는 것만** 말한다.

    ⚠️ 거를 재료가 없어서 거래소로 거르지 못한다 — NXT 거래 종목 목록을 주는
    원천을 **아직 안 쟀다**(이름을 추측해 배선하면 죽은 경로를 배포한다,
    #151·#345). 다만 **필드가 아니라 창으로 갈리는 하한**은 지금도 있다:
    `kr_session.exclusive_venue` 가 NXT 전용 체결 창(프리 08:00–09:00 ·
    15:40–16:00 — KRX 는 그때 체결 창이 아니다)을 판정하므로, 그 구간에 시간외
    체결이 붙는 종목은 정의상 NXT 다. `kr_board_probe ⑤` 가 그걸 잰다.
    """
    from bot.kr_session import union_window_label
    return ("네이버 시간외 체결 블록을 두 거래소 시간외 창의 합집합"
            f"({union_window_label()})에서 집계합니다 — 응답에 거래소를 이름으로 "
            "가르는 필드가 없어, 그 블록이 KRX 체결인지 NXT 체결인지는 아직 "
            "재지 않았습니다. NXT 거래 종목 목록도 받아 오지 않아 이 목록은 "
            "거래소로 걸러진 것이 아닙니다.")


_KR_UNIVERSE_CACHE = "kr_prepost_universe.json"   # 직전 성공 유니버스(장전 공백 폴백)
_KR_PREPOST_TTL = 2 * 60        # 시간외 창 재산출 간격 2분 (사용자 2026-06-16, 옛
#   5분에서 단축 — 시간외단일가는 연속 갱신이라 5분은 거침). 부하 무: SWR 요청-트리거(미열람 시 0)
#   + _KR_REFRESHING 락(스캔 비중첩·stampede 차단) + 종목당 30초 캐시 + 6-worker
#   풀. 열람 중 ~200종목 네이버 2분당 1회(~100콜/분) — realtime 엔드포인트 안전.
#   ⚠️ 이 예산은 **스캔이 창당 한 번**일 때만 참이다. 2026-09-16 에 거래소별
#   보드를 둘로 늘렸을 때 겹치는 16:00~20:00 콜이 2배가 됐고, 그게 네이버
#   레이트리밋을 건드리면 `naver_paused()` 가 켜져 **전 네이버 보드**가 죽는다
#   (독립 리뷰 2026-09-16 B1 — `_shared_venues` 로 한 번만 받게 막았었다).
#   2026-09-17 에 보드를 한 장으로 합치면서 그 위험 자체가 사라졌다 — 스캔
#   경로가 하나뿐이라 나눠 쓸 상대가 없다. 시간외 보드를 또 늘리려거든 이
#   예산부터 다시 재라.
_spawn = _threading.Thread   # 테스트가 stdlib `threading` 을 프로세스 전역으로
#   갈아끼우지 않게 하는 모듈 지역 별칭(독립 리뷰 2026-09-16 L18).
_KR_LOCK = _threading.Lock()
_KR_REFRESHING = False           # 스캔 진행 중 플래그(보드 1장 = 스캔 1개)


def _in_kr_extended_window(now_kst: datetime) -> bool:
    """KST now 가 **어느 거래소든** 연장 체결 창 안인가 = 재스캔할 창.
    창은 `bot.kr_session` **단일 출처**에서 온다 — 여기 리터럴로 적으면 그 표와
    갈라진다(#38·#55, 사용자 2026-09-16 네이버증권 공지 153)."""
    from bot.kr_session import union_extended_window
    return union_extended_window(now_kst)


def _current_kr_session(now_kst: datetime | None = None) -> str:
    """현재 KST 의 시간외 세션 — 'pre'·'post'·''. 창은 `bot.kr_session`."""
    from bot.kr_session import union_session
    return union_session(now_kst)


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
    """마지막 스캔의 결과 — 나이로 버리지 않는다. 사유는 형제(`prepost_status`)."""
    return _cached(_KR_PREPOST_STATUS, ttl=_STORED_FOREVER) or {}


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
    # 폴백 — 시간외 활동 확인용 후보군이라 전일 리스트로 충분(additive·라이브가
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


def _observe_venue_universe(rows: list) -> None:
    """거래소 **전용 체결 창**이면 이 스캔이 본 종목을 하한에 누적한다.

    사용자 2026-09-17 "이것도 해줘". 이 측정은 그 창이 열려 있을 때만 할 수
    있는데 스캔은 **이미 그 창에서 돌고 있다** — 운영자가 15:40 에 맞춰 프로브를
    치게 만드는 대신 여기서 주워 담는다(§Automation-first·#252). 추가 네트워크
    콜 0. 전용 창이 아니면 `observe` 가 스스로 no-op 이므로 여기서 창을 두 번
    판정하지 않는다(#38).

    ⚠️ 스캔을 멈추게 하지 않는다 — 이건 곁들이다(#315 넓은 try 가 본체를
    지우지 않게, 실패해도 보드는 그대로 나간다).
    """
    try:
        from bot.venue_universe import observe
        res = observe([(r.get("ticker"), r.get("over_ts")) for r in rows])
        if res.get("branch") == "exclusive":
            # 운영자가 이 기능이 도는지 보는 **유일한 창**이라 수를 다 싣는다
            # (독립 리뷰 2026-09-17 H4·L2 — 통째로 지워도 회귀가 green 이었다).
            log.info("venue universe: %s 전용 창 — 넘겨받음 %d · 관측 %d종목"
                     "(신규 %d · 누적 %s) · 안 셈 미기록 %d/창밖 %d",
                     res.get("venue"), res.get("seen") or 0,
                     res.get("counted") or 0,
                     res.get("new") or 0, res.get("total"),
                     res.get("undated") or 0, res.get("outside") or 0)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("venue universe: 누적 건너뜀: %s", exc)


def _compute_kr_prepost() -> dict:
    """KR 장전·장후 시간외(단일가) 급등·급락 TOP30 — 네이버 시간외단일가 실시간.
    정규장 무버 유니버스를 종목별 fetch_kr_quote 로 스캔(over-market OPEN 만 집계).
    백그라운드 전용. yfinance 미사용·네이버만.

    ⚠️ 거래소를 인자로 받지 않는다 — 네이버 응답의 시간외 블록은 하나뿐이고
    (#373b 실측) 보드도 하나다. 창만 합집합에서 온다(§kr_session)."""
    out: dict = {"up": [], "down": [], "ts": _now_label(), "scanned": 0, "session": "",
                 "source": "KR 정규장 무버 시간외 · 네이버 실시간"}
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
            ovol = q.get("over_volume")      # 시간외 세션 거래량 = 실체결 신호
            # 보드 = **실제 시간외 체결 종목만** (사용자 2026-06-16 '장전장후
            # 거래량과 거래대금'). Naver 는 미체결이면 accumulatedTradingVolume=
            # '-'(→ _num None) → 제외. overPrice 만 있고 거래량 0 인 phantom
            # (참조가) 랭킹 차단 — 후성 093370/피에스케이홀딩스 031980 류(over '-'
            # 인데 옛 over_volume-or-volume 폴백이 정규장 풀데이 거래량으로 보드를
            # 점령하던 것)가 이 클래스. 신세계 004170(vol 54,419 실체결)만 잔존.
            # ⚠️ 이 거래량이 **어느 거래소** 체결인지는 안 쟀다(#373b·#378) —
            # 그래서 화면도 거래소를 주장하지 않는다.
            if _ses and op and reg and reg > 0 and ovol:
                # 순수 시간외 move = 시간외가 vs 정규장 종가 (사용자 2026-06-16
                # '신세계 장후가 2천원 싸면 정규장보다 내린 것 → 격차순 고하 30').
                # Naver over_pct(=fluctuationsRatio)는 KR 에서 전일종가 누적이라
                # 정규장 급등락과 중복 → 정규장 종가 대비로 재계산해 시간외
                # '추가' 움직임만 랭킹.
                pct = round((op / reg - 1) * 100, 2)
                _ov = q.get("over_value")    # 시간외 세션 거래대금(원) → 억
                return {"ticker": tk, "name": names.get(tk, tk),
                        "price": op, "pct": pct,
                        # 표시 거래량/거래대금 = 시간외 세션 전용(정규장과 별개).
                        # 정규장 폴백 금지(옛 버그) — vol 은 위 가드로 항상 실체결값.
                        "vol": ovol,
                        "value": round(_ov / 1e8, 2) if _ov else None,
                        # 유동성 게이트 전용(미표시) — 정규장 누적거래량으로 페니·
                        # 유령 컷. 표시(시간외)와 게이트(정규장) 분리.
                        "reg_vol": q.get("volume"),
                        "mcap": mcaps.get(tk),
                        "session": _ses,
                        # 랭킹엔 안 쓰고 **하한 누적 판정에만** 쓴다 — 아래에서
                        # 떼어내므로 보드 payload 계약은 그대로다.
                        "over_ts": q.get("over_ts") or ""}
        except Exception:
            pass
        return None

    rows: list = []
    try:
        log.info("kr prepost: 정규장 무버 %d종목 네이버 시간외 스캔 시작",
                 len(tks))
        with ThreadPoolExecutor(max_workers=6) as pool:
            for rec in pool.map(_one, tks):
                if rec:
                    rows.append(rec)
        _observe_venue_universe(rows)
        for r in rows:                       # 판정에만 쓴 필드 — payload 에서 제외
            r.pop("over_ts", None)
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
                             detail="시간외 행 0 — 연장 창 밖이거나 체결 없음")
    except Exception as exc:
        log.warning("kr prepost: 산출 실패: %s", exc)
        _kr_status_write("failed", detail=f"{type(exc).__name__}: {exc}")
    return out


def _kr_prepost_live(age_sec: float | None,
                     now_kst: datetime | None = None) -> bool:
    """이 스냅샷이 **지금 합집합 창의 라이브**인가 — 창 안 + 2분 TTL 안일 때만 참.
    형제(US)와 같은 규약 — 사유는 `_prepost_live` 독스트링(#38)."""
    if age_sec is None:
        return False
    return (_in_kr_extended_window(now_kst or datetime.now(_KST9))
            and age_sec < _KR_PREPOST_TTL)


def _kick_kr_refresh() -> None:
    """스캔은 한 번에 하나만 — stampede 차단(#113)."""
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

    try:
        _spawn(target=_run, daemon=True, name="kr-prepost").start()
    except Exception:                                       # noqa: BLE001
        # start() 가 던지면 finally 가 안 돌아 보드가 영구 정지한다(#280).
        with _KR_LOCK:
            _KR_REFRESHING = False
        raise


def fetch_kr_prepost_movers() -> dict:
    """KR 시간외 급등·급락 — 동기 계산 안 함(SWR, US prepost 동일):
    신선/스테일 서빙 + 합집합 창에서만 백그라운드 재계산. 재발동 백오프.

    저장분은 **나이로 버리지 않는다** — 사유는 형제(`fetch_us_prepost_movers`)."""
    now_kst = datetime.now(_KST9)               # 한 번만 잰다(#160) — 형제(US) 동일
    in_win = _in_kr_extended_window(now_kst)
    # 나이를 **먼저** 잰다 — 읽는 사이 재집계가 끼어도 판정이 '저장분' 쪽으로
    # 기울지 '라이브' 쪽으로 기울지 않는다(#160 을 안전한 쪽으로).
    snap_age = cache_age_sec(_KR_PREPOST_CACHE)
    stale = _cached(_KR_PREPOST_CACHE, ttl=_STORED_FOREVER)
    live = _kr_prepost_live(snap_age, now_kst)
    st = kr_prepost_status()                    # 사유는 형제(US)의 주석

    def _serve(d: dict) -> dict:
        return {**d, "stale": not live, "in_window": in_win, "status": st,
                "stale_min": (max(0, int(snap_age // 60))
                              if snap_age is not None else None)}

    if stale is not None and (live or not in_win):
        return _serve(stale)
    age = time.time() - (st.get("ts") or 0)
    if not in_win:
        pass
    elif st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < _RUNNING_STALE_SEC:
        pass
    else:
        _kick_kr_refresh()
    if stale is not None:
        return _serve(stale)
    # 저장분이 **아예 없는** 유일한 경로 — '없음'이지 저장분이 아니다(#82).
    # `stored_unreadable` = 파일은 있는데 내용을 못 읽었다(`_cache_write` 는
    # truncate 후 쓰기라 쓰다 만 파일이 실재한다, #379) — '한 번도 집계한 적
    # 없음'과 처방이 다르다(#82·#54).
    return {"up": [], "down": [], "ts": "", "source": "", "session": "",
            "building": in_win, "status": st, "stale": False,
            "stale_min": None, "in_window": in_win,
            "stored_unreadable": snap_age is not None}


def scan_state(status: dict | None) -> str:
    """마지막 스캔의 **지금도 참인** 상태 — `'failed'` / `'running'` / `''`.

    `running` 은 **나이로 만료**시킨다: 스캔이 중간에 죽으면 그 도장이 남는데
    상태 파일을 영구 보존하게 되면서(`prepost_status`) 화면이 '진행 중'이라고
    영원히 거짓말할 수 있다(#25·#260). 문턱은 재발동 백오프와 같은 값이라 그
    시각을 넘기면 다음 창 접근이 어차피 다시 kick 한다(#38).
    `failed` 는 만료시키지 않는다 — 다음 성공이 `done` 으로 덮을 때까지 그것이
    마지막 사실이고, 여유로 사실을 덮으면 안 된다(#41).
    """
    st = status or {}
    state = str(st.get("state") or "")
    if state == "failed":
        return "failed"
    if state == "running" and (
            time.time() - (st.get("ts") or 0)) < _RUNNING_STALE_SEC:
        return "running"
    return ""


def freshness_label(data: dict | None) -> str:
    """부제의 신선도 문구 — 저장분에 '실시간' 이라고 적지 않는다.

    본문이 "27시간 전 저장분"이라고 적는데 부제는 "네이버 실시간"이라고 적으면
    한 화면이 두 말을 한다(#34·#55). 잰 것은 **우리 스냅샷의 나이**까지다(#375).
    """
    d = data or {}
    if not d.get("stale"):
        return "네이버 실시간"
    from bot.naver_diag import stale_label
    m = d.get("stale_min")
    ago = stale_label(m * 60 if isinstance(m, int) else None)
    return "💾 저장분" + (f"({ago})" if ago else "") + " · 실시간 아님"


def stored_missing_note(data: dict | None) -> str:
    """저장분이 **아예 없음** ↔ **읽지 못함** — 처방이 다르다(#82).

    `_cache_write` 는 truncate 후 쓰기라 쓰다 만 파일을 읽을 수 있고(#379),
    깨진 파일은 내용은 못 읽어도 mtime 은 남는다. 둘을 한 문장으로 뭉치면
    "한 번도 집계한 적 없음"으로 읽혀 운영자를 엉뚱한 데로 보낸다(#54·#292).
    """
    if (data or {}).get("stored_unreadable"):
        return "(직전 집계 저장분이 있지만 읽지 못했습니다 — 다음 집계가 다시 씁니다.)"
    return "(직전 집계 저장분도 없습니다.)"


def stored_note(data: dict | None, window: str = "") -> str:
    """저장분 안내 **한 줄** — US·KR 두 화면이 같은 문구를 쓰게 한 곳에서 만든다.

    화면마다 적으면 한쪽만 고쳐져 갈린다(#38·#147). 판정은 payload 가 실어 준
    것(`stale`·`stale_min`·`in_window`)을 그대로 따르고 여기서 다시 재지 않는다
    (#136 payload 가 밝힌 원천을 화면이 따른다 · #35). 나이 라벨은 형제 위젯
    (TW 업종 #306 · 네이버 업종 #335)과 같은 `naver_diag.stale_label` 규약.

    ⚠️ 이 문장은 **우리 스냅샷에 대한 주장**이지 시장에 대한 주장이 아니다 —
    "지금 시장이 이렇다"가 아니라 "우리가 마지막으로 집계한 것이 이것이다"(#375).
    ⚠️ `in_window` 를 못 받았으면 **왜 저장분인지 단정하지 않는다**(#165).
    """
    d = data or {}
    if not d.get("stale"):
        return ""
    from bot.naver_diag import stale_label
    m = d.get("stale_min")
    ago = stale_label(m * 60 if isinstance(m, int) else None)
    ts = str(d.get("ts") or "")
    when = " · ".join(x for x in ((f"{ts} 집계" if ts else ""), ago) if x)
    iw = d.get("in_window")
    if iw is False:
        why = "지금은 장전·장후 창 밖이라 마지막 집계를 그대로 보여줍니다"
    elif iw is True:
        why = "이번 창 집계가 아직 갱신되지 않아 직전 집계를 보여줍니다"
    else:
        why = "마지막 집계를 그대로 보여줍니다"
    # 꼬리는 **지킬 수 있는 약속일 때만** 붙인다.
    #  · 마지막 스캔이 실패했으면 자동 갱신은 지킬 수 없는 약속이다 — 약속 대신
    #    그 사실을 적는다(#380). 기계 상세(예외 문자열)는 안 싣는다(#391).
    #  · 이미 창 **안**이면 '다음 창' 이 거짓이다(#55).
    #  · 창 판정을 못 받았으면(iw None) 갱신 시점도 모르는 것이므로 단정하지
    #    않는다 — 안 잰 것을 미래형으로 적으면 그게 #165 다.
    if scan_state(d.get("status")) == "failed":
        tail = " 마지막 집계 시도는 실패해 아직 갱신되지 않았습니다."
    elif window and iw is False:
        tail = f" 다음 창({window})에서 자동 갱신됩니다."
    else:
        tail = ""
    return ("💾 저장분" + (f" — {when}" if when else "") + f" · {why}."
            + tail)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = _compute_us_prepost()
    print(f"session={d.get('session')} scanned={d.get('scanned')} "
          f"up={len(d.get('up', []))} down={len(d.get('down', []))}")
    for r in (d.get("up", [])[:5] + d.get("down", [])[:5]):
        print(" ", r.get("ticker"), r.get("name"), f"{r.get('pct'):+.2f}%",
              "vol", r.get("vol"))
