"""종목명 옆 `(+1.23%)` 실시간 등락률 — 국가별 3보드 종목 표(사용자 2026-10-13 "() 안에 등락률은 가능한
실시간으로"). 보유종목 News 는 2026-10-14 에 종목 시세를 뺐다가(장종료가 기준 — 열어 둔 화면이 1분마다 묻던 부하)
2026-10-19 부터 **10분 간격**(`lookup(fresh=600)`)으로 장 중·종가 수집 전 세션의 종목만 다시 묻는다(사용자 "10분에
한번씩") — 원화 환율(`fx_lookup`)은 그대로 1시간.

시세는 화면들이 이미 쓰는 실시간 경로 그대로다(`chart_data` 와 같은 갈래, #38): 국내 = 네이버 polling
(`naver_quote`) · 대만 = TWSE 공식(`tw_quote`, 네이버 미지원) · 미·일·홍·중 = 네이버 해외(`world_quote`).
값은 원천이 준 **정규장** '직전 종가 대비' 등락률이다 — 장 중이면 지금 값, 장이 끝났으면 마지막 세션 값.
장전·장후 시간외 등락은 싣지 않는다(사용자 2026-06-16 "현재가 모든 곳·모든 나라 정규장 기준" — 네이버
파서도 `pct` 를 정규장에 두고 시간외는 `over_pct` 로만 낸다, 리뷰 2026-10-13). 네이버 두 갈래는
`/naverpause` 를 따른다(그동안은 서버 값이 남는다).

요청 경로는 네트워크를 부르지 않는다(SWR, #116·#280): 메모리 표를 즉시 주고 낡은 티커만 뒤에서 받는다.
화면은 `pending` 이 있으면 몇 초 뒤 다시 묻는다. 받기 실패도 시각을 남겨 같은 티커를 `FRESH_SEC` 안에
다시 두드리지 않는다(실패는 짧게만 믿는다, #303)."""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

FRESH_SEC = 60          # 화면 폴링(60초)과 같은 축 — 더 짧으면 같은 값을 두 번 받는다
MAX_TICKERS = 400       # 한 요청이 맡길 수 있는 티커 수(심리지도 한 칸을 덮는다)
WORKERS = 6             # 동시 요청 — 원천 하나에 몰리지 않게 작게
_KEEP_SEC = 6 * 3600    # 표에서 이보다 오래 안 물은 티커는 지운다(메모리 상한)

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
_LOCK = threading.Lock()
_TABLE: dict[str, tuple[float, dict | None]] = {}   # 티커 → (받은 시각, 값 또는 None)
_ASKED: dict[str, float] = {}                         # 티커 → 마지막으로 화면이 물은 시각
_PENDING: set[str] = set()
_RUNNING = False
# 갱신 스레드 — 테스트는 이 별칭만 갈아끼운다(공용 `threading.Thread` 를 바꾸면 스레드풀까지 멈춘다, #439).
_Thread = threading.Thread


def market_of(t: str) -> str:
    """접미 없는 6자리는 국내다 — `market.detect_market` 은 그걸 미국으로 본다(국가별·보유종목 링크는
    국내를 접미 없이 싣는다, #399)."""
    if re.fullmatch(r"\d[0-9A-Z]{5}", t):        # 국내 6자리(영숫자 신규 코드 `0009K0` 포함)
        return "KR"
    from bot.market import detect_market
    return detect_market(t)


def fetch_one(t: str) -> dict | None:
    """한 티커의 실시간 {pct, ts, src}. 못 받거나 등락률이 없으면 None."""
    m = market_of(t)
    if m == "TW":
        from bot.tw_quote import fetch_tw_quote as f
        src = "TWSE"
    elif m in ("KR", "US", "JP", "HK", "CN_A"):
        if m == "KR" and not t.split(".")[0].isdigit():
            return None              # 국내 시세 원천이 숫자 코드만 받는다 — 헛된 요청을 내지 않는다
        if m == "CN_A" and t.endswith(".BJ"):
            return None              # 베이징 거래소는 네이버 해외 코드를 재 본 적이 없다(#165)
        from bot.finviz_client import naver_paused
        if naver_paused():
            return None
        if m == "KR":
            from bot.naver_quote import fetch_kr_quote as f
        else:
            from bot.world_quote import fetch_world_quote as f
        src = "네이버"
    else:
        return None
    q = f(t) or {}
    pct = q.get("pct")
    if pct is None:
        return None
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return None
    if pct != pct or abs(pct) > 1000:          # NaN·말도 안 되는 값은 싣지 않는다
        return None
    out = {"pct": round(pct, 2), "ts": q.get("ts"), "src": src}
    d = session_date(q.get("ts"))
    if d:                                      # 그 등락이 어느 세션의 것인지(보유종목 News 가 맞춰 보고 이어 붙인다)
        out["d"] = d
    try:
        px = float(q.get("price"))
        if px > 0 and px == px:
            out["price"] = px
    except (TypeError, ValueError):
        pass
    return out


def session_date(ts) -> str | None:
    """원천 체결시각 → 그 거래소 현지 세션 날짜 'YYYY-MM-DD'(못 읽으면 None — 추측하지 않는다). 형식: ISO(거래소 현지
    오프셋을 단 '..+09:00') 또는 TWSE 'YYYYMMDD'. ⚠️ 해외(네이버 worldstock)의 오프셋이 거래소 현지인지는 재지
    않았다(#165) — `chart_data._quote_date` 가 같은 가정으로 당일 봉을 붙이므로 둘이 이 함수 하나를 쓴다(#38). 틀리면
    날짜가 어긋나 보유종목 News 는 이어 붙이지 않는다(안전한 쪽)."""
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        if len(s) == 8 and s.isdigit():               # TWSE '20260615'
            return datetime.strptime(s, "%Y%m%d").date().isoformat()
        return datetime.fromisoformat(s).date().isoformat()
    except (TypeError, ValueError):
        return None


# ── 원화 환율 전일대비(보유종목 News 해외 원화 손익, 사용자 2026-10-14 "환율 반영 원화손익으로") ──────────
# 원천 = 네이버 원화 환율 목록(`naver_marketindex.fetch_kr_fx` — 매크로 카드의 원/달러·원/엔과 같은 값 풀, #38).
# 그 목록에 `FX_<통화>KRW` 가 없는 통화는 **지어내지 않고** 비운다(호출부가 '환율 미확인' 으로 센다, #165).
FX_FRESH_SEC = 60                 # 종목 등락과 같은 축
FX_MAX_AGE_SEC = 6 * 3600         # 원천이 막혀 낡은 값 풀(최대 24h)을 주면 그날의 환율 등락이 아닐 수 있다 → 버린다
_FX_RE = re.compile(r"^FX_([A-Z]{3})KRW$")
_FX: dict = {"at": 0.0, "val": {}, "running": False}


def fx_pct_map(table) -> dict[str, float]:
    """`fetch_kr_fx` 표 → {통화: 원화 환율 전일대비 %}. 순수."""
    out: dict[str, float] = {}
    for rc, rec in (table or {}).items() if isinstance(table, dict) else ():
        m = _FX_RE.match(str(rc))
        if not m or not isinstance(rec, dict):
            continue
        try:
            v = float(rec.get("pct"))
        except (TypeError, ValueError):
            continue
        if v == v and abs(v) < 50:                   # NaN·말도 안 되는 값은 싣지 않는다
            out[m.group(1)] = round(v, 4)
    return out


def fx_level_map(table) -> dict[str, float]:
    """`fetch_kr_fx` 표 → {통화: 원화 환율 수준(종가 칸)}. 순수 — 여러 날 기준의 누적 환율 변동(지금 ÷ 기준)에 쓴다."""
    out: dict[str, float] = {}
    for rc, rec in (table or {}).items() if isinstance(table, dict) else ():
        m = _FX_RE.match(str(rc))
        if not m or not isinstance(rec, dict):
            continue
        try:
            v = float(rec.get("close"))
        except (TypeError, ValueError):
            continue
        if v == v and v > 0:
            out[m.group(1)] = v
    return out


def fx_levels_cached(*, max_age: float | None = None) -> dict[str, float]:
    """디스크 값 풀에 이미 있는 원화 환율 수준 — 네트워크 0. 값 풀이 `max_age`(기본 `FX_MAX_AGE_SEC`) 보다 낡았으면 {}."""
    try:
        from bot.finviz_client import _cached
        from bot.naver_marketindex import _KRFX_CACHE, value_age_sec
        age = value_age_sec("fx")
        if age is None or age > (FX_MAX_AGE_SEC if max_age is None else min(max_age, FX_MAX_AGE_SEC)):
            return {}
        return fx_level_map(_cached(_KRFX_CACHE, ttl=int(FX_MAX_AGE_SEC)) or {})
    except Exception as exc:                                   # noqa: BLE001
        log.warning("live_pct: 원화 환율 수준을 못 읽었습니다(%s)", exc)
        return {}


def fx_changes(*, levels: dict | None = None) -> dict[str, float]:
    """네트워크를 부를 수 있다(값 풀 30초 캐시). 값 풀이 `FX_MAX_AGE_SEC` 보다 낡았으면 {} — 못 받은 것과 같다."""
    try:
        from bot.naver_marketindex import fetch_kr_fx, value_age_sec
        tbl = fetch_kr_fx()
        age = value_age_sec("fx")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("live_pct: 원화 환율을 못 받았습니다(%s: %s)", type(exc).__name__, exc)
        return {}
    if age is not None and age > FX_MAX_AGE_SEC:
        log.warning("live_pct: 원화 환율 값 풀이 %.1f시간 전 값이라 쓰지 않습니다", age / 3600)
        return {}
    if levels is not None:
        levels.update(fx_level_map(tbl))
    return fx_pct_map(tbl)


def _fx_worker() -> None:
    lv: dict = {}
    try:
        val = fx_changes(levels=lv)
    except Exception:                                          # noqa: BLE001
        val = {}
    with _LOCK:
        _FX["at"], _FX["running"] = time.time(), False
        if val:                                                # 실패는 직전 값을 지우지 않는다(나이는 age 가 말한다)
            _FX["val"] = val
            _FX["lvl"] = lv
            _FX["val_at"] = _FX["at"]


def fx_lookup(now: float | None = None, *, kick: bool = True) -> dict:
    """화면용 {통화: {pct, age}} — 네트워크를 부르지 않는다(낡았으면 뒤에서 받는다). 값이 `FX_MAX_AGE_SEC` 보다
    오래됐으면 싣지 않는다."""
    now = time.time() if now is None else now
    start = False
    with _LOCK:
        val, at, lvl = dict(_FX["val"]), _FX.get("val_at") or 0.0, dict(_FX.get("lvl") or {})
        if kick and now - _FX["at"] >= FX_FRESH_SEC and not _FX["running"]:
            _FX["running"] = start = True
    if start:
        try:
            _Thread(target=_fx_worker, name="live-fx", daemon=True).start()
        except Exception as exc:                               # noqa: BLE001
            log.warning("live_pct: 환율 갱신 스레드를 띄우지 못했습니다(%s)", exc)
            with _LOCK:
                _FX["running"] = False
    if not val or now - at > FX_MAX_AGE_SEC:
        return {}
    return {c: {"pct": p, "age": round(now - at), **({"close": lvl[c]} if c in lvl else {})}
            for c, p in val.items()}


def fx_pending() -> bool:
    """재시작 뒤 **첫** 환율 받기가 아직 안 끝났나 — `fx_lookup` 이 빈 표를 줄 때 화면이 '못 받음' 과 '받는 중(재시작
    직후)' 을 가르게 한다(상태는 아는 쪽이 말한다, #86). 한 번이라도 끝났으면 False — 그 뒤의 빈 표는 '미확인' 이
    사실이다. 그 뒤 시도마다 True 를 주면 안 된다: `fx_lookup` 은 물을 때마다 값이 60초보다 낡았으면 새 시도를 띄우므로 원천이 빨리 실패하는 날엔
    매 요청이 '받는 중' 으로 보여 화면이 '대기' 를 영원히 약속한다(델타 리뷰 2026-10-14)."""
    with _LOCK:
        return bool(_FX["running"]) and not _FX["at"]


def normalize(raw) -> tuple[list[str], bool]:
    """쉼표 목록 → (중복 없는 티커, 잘렸나). 모양이 아닌 것은 버린다."""
    out: list[str] = []
    seen = set()
    for x in (raw or []):
        t = str(x or "").strip().upper()
        if not t or t in seen or not _TICKER_RE.match(t):
            continue
        seen.add(t)
        out.append(t)
    return out[:MAX_TICKERS], len(out) > MAX_TICKERS


def _worker() -> None:
    global _RUNNING
    from concurrent.futures import ThreadPoolExecutor
    try:
        while True:
            with _LOCK:
                batch = list(_PENDING)[:WORKERS * 8]
                if not batch:
                    _RUNNING = False
                    return
                _PENDING.difference_update(batch)
            with ThreadPoolExecutor(WORKERS) as ex:
                res = list(ex.map(_safe_fetch, batch))
            now = time.time()
            with _LOCK:
                for t, v in zip(batch, res):
                    _TABLE[t] = (now, v)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("live_pct: 실시간 등락 갱신이 멈췄습니다(%s: %s)", type(exc).__name__, exc)
        with _LOCK:
            _RUNNING = False


def _safe_fetch(t: str) -> dict | None:
    try:
        return fetch_one(t)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("live_pct %s failed: %s", t, exc)
        return None


def _prune(now: float) -> None:
    for t in [t for t, a in _ASKED.items() if now - a > _KEEP_SEC]:
        _ASKED.pop(t, None)
        _TABLE.pop(t, None)


FRESH_MAX_SEC = 3600    # 화면이 늦출 수 있는 상한(`fresh`)


def lookup(tickers, now: float | None = None, *, kick: bool = True, fresh: float = FRESH_SEC) -> dict:
    """화면용 {q: {티커: {pct, ts, src, age, d?, price?}}, pending, capped} — 네트워크를 부르지 않는다. `fresh` = 이보다
    낡은 값만 다시 받는다(보유종목 News = 10분 — 사용자 2026-10-19 "10분에 한번씩", `FRESH_SEC`~`FRESH_MAX_SEC`). 표는
    화면끼리 같이 쓰므로 탭이 몇 개든 한 티커는 그 간격에 한 번만 받는다."""
    global _RUNNING
    now = time.time() if now is None else now
    try:
        fresh = float(fresh)
    except (TypeError, ValueError):
        fresh = FRESH_SEC
    if fresh != fresh:                 # NaN — 비교가 늘 거짓이라 그 티커가 영영 다시 안 받힌다
        fresh = FRESH_SEC
    fresh = min(max(fresh, FRESH_SEC), FRESH_MAX_SEC)
    ts, capped = normalize(tickers)
    q: dict[str, dict] = {}
    stale: list[str] = []
    start = False
    with _LOCK:
        for t in ts:
            _ASKED[t] = now
            hit = _TABLE.get(t)
            if hit is None or now - hit[0] >= fresh:
                stale.append(t)
            if hit and hit[1]:
                q[t] = dict(hit[1], age=round(now - hit[0]))
        if kick and stale:
            _PENDING.update(stale)
            if not _RUNNING:
                _RUNNING = start = True
        _prune(now)
    if start:
        try:
            _Thread(target=_worker, name="live-pct", daemon=True).start()
        except Exception as exc:                               # noqa: BLE001
            log.warning("live_pct: 갱신 스레드를 띄우지 못했습니다(%s)", exc)
            with _LOCK:
                _RUNNING = False          # 그대로 두면 다음 요청도 영영 안 띄운다(#371)
    return {"q": q, "pending": len(stale), "capped": capped}


# 화면 쪽 — `.js-live-pct[data-t]` 칸을 1분마다 채운다. 못 받은 칸은 서버가 그린 것을 그대로 둔다(국가별 표는
# 빈칸). 색 클래스는 칸이 `data-cu`/`data-cd` 로 고른다(페이지마다 토큰이
# 다르다). 상대 경로 — 토큰 경로(`/t/<token>/…`)를 떨어뜨리지 않는다(#359). 같은 탭에서 다시 그린 표는
# 마지막 값으로 즉시 채운다(정렬·칸 열기마다 빈칸이 깜빡이지 않게).
LIVE_JS = r"""
(function(){
  var last={}, timer=null, busy=false, again=false;
  function kst(ts){
    if(!ts) return '';
    var s=String(ts);
    if(/^\d{8}$/.test(s)) return s.slice(4,6)+'-'+s.slice(6,8)+' 거래일';   // TWSE 는 날짜만 준다
    var d=new Date(s); if(isNaN(d)) return '';
    try{return d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',
      hour:'2-digit',minute:'2-digit',hour12:false})+' KST';}catch(e){return '';}
  }
  function paint(el,v){
    if(!v) return;
    var p=v.pct, cu=el.getAttribute('data-cu')||'', cd=el.getAttribute('data-cd')||'';
    el.textContent='('+(p>0?'+':'')+p.toFixed(2)+'%)';
    if(cu) el.classList.toggle(cu,p>0);
    if(cd) el.classList.toggle(cd,p<0);
    var when=kst(v.ts);
    el.setAttribute('title','실시간 등락률(정규장 · 직전 종가 대비) · '+v.src+(when?' · 기준 '+when:'')+' · 1분마다 갱신');
    el.setAttribute('data-live','1');
  }
  function fill(root){
    [].slice.call((root||document).querySelectorAll('.js-live-pct[data-t]')).forEach(function(el){
      paint(el,last[el.getAttribute('data-t')]);});
  }
  // 보이는 칸만 묻는다 — 숨은 탭·닫힌 칸까지 세면 상한(400)을 먹고 원천을 헛두드린다(리뷰 2026-10-13).
  function shown(el){return el.offsetParent!==null;}
  function poll(){
    if(busy){again=true;return;}
    var ts={}; [].slice.call(document.querySelectorAll('.js-live-pct[data-t]')).forEach(function(el){
      if(shown(el)) ts[el.getAttribute('data-t')]=1;});
    var list=Object.keys(ts).slice(0,400);
    clearTimeout(timer);
    if(!list.length){timer=setTimeout(poll,60000);return;}
    busy=true;
    fetch('api/live_pct?t='+encodeURIComponent(list.join(',')),{cache:'no-store'})
      .then(function(r){if(!r.ok) throw new Error('HTTP '+r.status); return r.json();})
      .then(function(js){
        var q=(js&&js.q)||{}; for(var k in q) last[k]=q[k];
        fill(document);
        timer=setTimeout(poll,(js&&js.pending)?5000:60000);
      }).catch(function(){timer=setTimeout(poll,60000);})
      .then(function(){busy=false; if(again){again=false; clearTimeout(timer); timer=setTimeout(poll,300);}});
  }
  window.liveFill=function(root){fill(root); clearTimeout(timer); timer=setTimeout(poll,300);};
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',poll); else poll();
})();
"""
