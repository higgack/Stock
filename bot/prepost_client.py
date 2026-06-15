"""미국 장전(pre-market)·장후(after-hours) 급등·급락 — yfinance prepost 일중봉.

기존 급등락(`finviz_client.fetch_us_movers`, 정규장 일봉)의 **형제 표면**
(사용자 2026-06-14 '장전/장후도 별도 자식 대시보드'). 정규장 종가 대비
연장거래 가격 변화율로 상·하위 산출.

왜 yfinance 인가: 연장거래 가격은 `yf.download(prepost=True, interval=…)` =
**history API**(우리가 겪는 fast_info quote-API rate-limit 과 무관, /health 에서
download ✅ 확인). 따라서 fast_info 회로차단과 독립적으로 작동.

⚠️ US 전용 — 한국 시간외단일가는 yfinance 가 거의 미커버(Naver 시간외
엔드포인트 검증 후 별도 추가 예정). 연장거래는 유동성이 얇아 대부분 종목은
거래 0 → 실제 결과는 뉴스 나온 소수 종목(정상). 글리치(|pct|>75%)·박거래·
페니 컷.

SWR 백그라운드(전미국 스캔 수 분 = 페이지 hang 금지) + 장-인지 신선도
(연장거래 창에서만 재스캔, 장 완전 종료 시 직전 스냅샷 서빙). 무료·무키·graceful.
"""
from __future__ import annotations

import logging
import threading as _threading
import time
from datetime import datetime, timezone

from bot.finviz_client import (_CACHE_DIR, _cache_write, _cached, _now_label,
                               _us_full_universe, yf_paused)

log = logging.getLogger("bot.prepost_client")

_PREPOST_CACHE = "us_prepost_v1.json"
_PREPOST_STATUS = "us_prepost_status.json"
_PREPOST_TOP_N = 30
_PREPOST_TTL = 30 * 60         # 연장거래 창에서 재산출 간격 30분 (movers 와 동일)
_MIN_PRICE = 1.0               # 페니 컷
_MIN_EXT_VOL = 1000            # 연장거래량 하한(유동성 — 0거래 종목 배제)
_GLITCH_PCT = 75.0             # 분할/조정 아티팩트 컷 (KLAC 클래스, CLAUDE.md 가드)


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
          and (r.get("vol") or 0) >= _MIN_EXT_VOL]
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


def _compute_us_prepost() -> dict:
    """전미국 2일 30분봉(prepost) 벌크 → 정규장 종가 대비 연장거래 등락 상·하위
    TOP30. 백그라운드 전용(배치 ~50회·수 분). period=2d 는 장전 시 전일 정규장
    종가 기준 필요(당일 정규장 봉 아직 없음). interval=30m 으로 데이터량 절감."""
    out: dict = {"up": [], "down": [], "ts": _now_label(), "scanned": 0,
                 "session": "", "source": "전 미국 상장 연장거래(yfinance · 30분봉)"}
    if yf_paused():
        return _cached(_PREPOST_CACHE, ttl=86400) or out
    tks, names = _us_full_universe()
    if not tks:
        log.warning("prepost: universe empty")
        _status_write("failed", detail="universe 전 소스 실패")
        return out
    rows: list = []
    _CHUNK = 120
    n_batches = (len(tks) + _CHUNK - 1) // _CHUNK
    _status_write("running", done=0, total=n_batches, universe=len(tks))
    try:
        import yfinance as yf
        log.info("prepost: universe %d, 30분봉(prepost) 배치 시작", len(tks))
        for ci in range(0, len(tks), _CHUNK):
            chunk = tks[ci:ci + _CHUNK]
            bi = ci // _CHUNK + 1
            if bi % 5 == 0:
                _status_write("running", done=bi, total=n_batches,
                              universe=len(tks), rows=len(rows))
            try:
                df = yf.download(chunk, period="2d", interval="30m",
                                 prepost=True, group_by="ticker", threads=True,
                                 progress=False, auto_adjust=False)
            except Exception:
                continue
            if df is None or df.empty:
                continue
            kinds, dates = _classify_index(df.index)
            if not kinds:
                continue
            time.sleep(0.2)            # 벌크 사이 호흡 (yfinance 보호)
            for tk in chunk:
                try:
                    if tk not in df.columns.get_level_values(0):
                        continue
                    sub = df[tk]
                    rec = _ticker_prepost(sub["Close"], sub["Volume"], kinds, dates)
                    if not rec:
                        continue
                    rec.update({"ticker": tk, "name": names.get(tk, tk)})
                    rows.append(rec)
                except Exception:
                    continue
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
        # 시총·업종 백필 — hit 종목만(소수). movers 와 동일 패턴(429 내성 벌크
        # 캐시 우선). fast_info 회로차단 시 mcap None graceful.
        try:
            from bot.finviz_client import _fetch_industries, _fetch_mcaps
            hits = [r["ticker"] for r in ups + downs]
            mcaps = _fetch_mcaps(hits)
            inds = _fetch_industries(hits)
            for r in ups + downs:
                mc = mcaps.get(r["ticker"])
                r["mcap"] = round(mc / 1e8, 2) if mc else None
                r["ind"] = inds.get(r["ticker"])
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
                          detail="연장거래 행 0 — 장 마감·연장 미개장 또는 yfinance 제한")
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
    """장전/장후 급등·급락 — **동기 계산 절대 안 함**(전미국 배치 = 페이지 hang
    금지, movers/highlow SWR 와 동일): 신선 서빙 / stale 서빙 + 백그라운드
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
    # (사용자 2026-06-14 '계속 새로 시작'). 주말·휴장에 캐시 부재 시 전미국 47배치
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = _compute_us_prepost()
    print(f"session={d.get('session')} scanned={d.get('scanned')} "
          f"up={len(d.get('up', []))} down={len(d.get('down', []))}")
    for r in (d.get("up", [])[:5] + d.get("down", [])[:5]):
        print(" ", r.get("ticker"), r.get("name"), f"{r.get('pct'):+.2f}%",
              "vol", r.get("vol"))
