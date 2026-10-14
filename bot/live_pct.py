"""종목명 옆 `(+1.23%)` 실시간 등락률 — 보유종목 News 카드 · 국가별 3보드 종목 표(사용자 2026-10-13
"() 안에 등락률은 가능한 실시간으로").

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

log = logging.getLogger(__name__)

FRESH_SEC = 60          # 화면 폴링(60초)과 같은 축 — 더 짧으면 같은 값을 두 번 받는다
MAX_TICKERS = 400       # 한 요청이 맡길 수 있는 티커 수(심리지도 한 칸·보유 종목 하루치를 덮는다)
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
    return {"pct": round(pct, 2), "ts": q.get("ts"), "src": src}


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


def lookup(tickers, now: float | None = None, *, kick: bool = True) -> dict:
    """화면용 {q: {티커: {pct, ts, src, age}}, pending, capped} — 네트워크를 부르지 않는다."""
    global _RUNNING
    now = time.time() if now is None else now
    ts, capped = normalize(tickers)
    q: dict[str, dict] = {}
    stale: list[str] = []
    start = False
    with _LOCK:
        for t in ts:
            _ASKED[t] = now
            hit = _TABLE.get(t)
            if hit is None or now - hit[0] >= FRESH_SEC:
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


# 화면 쪽 — `.js-live-pct[data-t]` 칸을 1분마다 채운다. 못 받은 칸은 서버가 그린 것을 그대로 둔다(보유종목
# News 는 전날 등락 · 국가별 표는 빈칸). 색 클래스는 칸이 `data-cu`/`data-cd` 로 고른다(페이지마다 토큰이
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
    // `data-v`(보유 평가금액)가 있는 칸은 금액도 같이(보유종목 News — 평가금액 × 등락률 추정)
    var dv=parseFloat(el.getAttribute('data-v')||''), am='';
    if(dv&&window.hnAmt) am=' · '+window.hnAmt(dv*p/100);
    el.textContent='('+(p>0?'+':'')+p.toFixed(2)+'%'+am+')';
    if(cu) el.classList.toggle(cu,p>0);
    if(cd) el.classList.toggle(cd,p<0);
    var when=kst(v.ts);
    el.setAttribute('title','실시간 등락률(정규장 · 직전 종가 대비)'+(am?' · 금액 = 보유 평가금액 × 등락률(추정)':'')+' · '+v.src
      +(when?' · 기준 '+when:'')+' · 1분마다 갱신');
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
