"""신고가·신저가 / 급등·급락 / 상한가·하한가 공용 리치 렌더러 (사용자 2026-06-13
'모든 나라 미국 포맷 통일'). 미국 us_pages._stock_panel 을 전 시장 일반화:
종목(+이름)·현재가·등락률·거래량·시총·업종 + 시총 정렬 기본 + 헤더 클릭 정렬 +
업종 분포 한 줄. 통화/시총 단위는 시장별(₩/¥/NT$/HK$/$, 억/조 vs $T/$B/$M).

데이터 항목 스키마(시장 무관): {ticker, name, price, pct, vol, mcap, ind}.
- mcap 단위: US=억$(USD, Finviz), 그 외=현지통화 raw(yfinance marketCap).
- name: US=영문 longName, JP/TW/CN/HK=한글 번역(chart_translate), KR=종목명.
순수 렌더(네트워크 0) — 백필은 호출측(finviz_client._compute_highlow_from 등).
"""
from __future__ import annotations

import html as _html
import logging
import re as _re
import threading as _threading
import time as _time

log = logging.getLogger("bot.highlow_render")


def _strip_dup_ticker(ticker: str, name: str) -> str:
    """name 앞에 티커가 'TICKER | '/'TICKER - '/'TICKER:' 로 중복되면 그 뒤만
    사용 (HK '0004.HK | 구룡창' → '구룡창'). 티커가 1줄에 이미 표시되는데
    한글명 줄에 또 박혀 중복되던 것 제거(사용자 2026-06-15 '티커가 두번 겹치는
    거 수정'). 순수·universal — 전 시장 _row 공용. 구분자 없으면 원본 유지."""
    t, n = (ticker or "").strip(), (name or "").strip()
    if not t or not n:
        return n
    m = _re.match(r'^' + _re.escape(t) + r'\s*[|\-:·]\s*(.+)$', n)
    return m.group(1).strip() if m else n

from bot.naver_pages import _fmt_vol, _pct_cell

# 시장 → (통화기호, 가격 소수자릿수). CJK 통화는 정수 표기가 자연스러움.
_CUR = {"US": ("$", 2), "KR": ("₩", 0), "JP": ("¥", 0),
        "TW": ("NT$", 2), "CN_A": ("¥", 2), "HK": ("HK$", 2)}

# 정렬/업종 셀 스타일 + 헤더 클릭 정렬 (us_pages 동일 — 단일 소스로 이관).
HL_SORT_JS = """
<style>
.hl-table th.srt{cursor:pointer;user-select:none;white-space:nowrap}
.hl-table td.ind{max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted,#888);font-size:12px}
/* 비-KR 종목명: 티커 아래 한글명 별도 줄(.nk). 괄호 인라인이 좁은 셀에서 단어
   중간 줄바꿈돼 안 보이던 것 해소(사용자 2026-06-15). 작게·muted·단어 보존. */
.hl-table td.nm .nk{display:block;font-size:11.5px;color:var(--muted,#888);font-weight:400;word-break:keep-all;line-height:1.25;margin-top:1px}
/* KR/CJK(name_only) 종목명 줄바꿈 절대 금지 — 상한가 등 다컬럼에서 '원익/IPS'
   처럼 깨지던 것 방지(사용자 2026-06-14 재요청 — 두 번째). nowrap + keep-all +
   !important 로 어떤 상위/캐시 규칙도 못 덮게. td 와 내부 a 둘 다 명시. min-width
   로 좁은 컬럼이 글자를 쥐어짜지 않게. US 영문 긴 이름은 nm-nowrap 미적용. */
.hl-table.nm-nowrap td.nm,.hl-table.nm-nowrap td.nm a{
  white-space:nowrap!important;word-break:keep-all;overflow-wrap:normal}
.hl-table.nm-nowrap td.nm{min-width:84px}
.hl-table th.srt:hover{color:var(--accent,#3b82f6)}
.hl-table th.srt .arw{opacity:.45;font-size:10px;margin-left:2px}
.hl-table th.srt.on .arw{opacity:1}
</style>
<script>
(function(){
  function sortTable(tbl, key, type, asc){
    var tb=tbl.tBodies[0];
    var rows=Array.prototype.slice.call(tb.rows);
    rows.sort(function(a,b){
      var x=a.getAttribute('data-'+key), y=b.getAttribute('data-'+key);
      if(type==='num'){ x=parseFloat(x); y=parseFloat(y);
        if(isNaN(x))x=-Infinity; if(isNaN(y))y=-Infinity; return asc?x-y:y-x; }
      x=(x||'').toString(); y=(y||'').toString();
      return asc? x.localeCompare(y): y.localeCompare(x);
    });
    rows.forEach(function(r,i){ tb.appendChild(r);
      var rk=r.querySelector('.rk'); if(rk) rk.textContent=i+1; });
  }
  // window.hlBindSort — 자동 새로고침(live_refresh) 이 #live-root 를 innerHTML
  // 교체한 뒤 새 표에 정렬을 재바인드하려 호출. _srtb 가드로 중복 바인드 차단.
  window.hlBindSort=function(){
  document.querySelectorAll('table.hl-table').forEach(function(tbl){
    tbl.querySelectorAll('th.srt').forEach(function(th){
      if(th._srtb) return; th._srtb=true;
      var arw=document.createElement('span'); arw.className='arw'; arw.textContent='⇅';
      th.appendChild(arw);
      th.addEventListener('click', function(){
        var key=th.getAttribute('data-key'), type=th.getAttribute('data-type');
        var asc = th.classList.contains('on') ? !th._asc : (type!=='num');
        tbl.querySelectorAll('th.srt').forEach(function(o){
          o.classList.remove('on'); var a=o.querySelector('.arw'); if(a)a.textContent='⇅'; });
        th.classList.add('on'); th._asc=asc; arw.textContent=asc?'▲':'▼';
        sortTable(tbl, key, type, asc);
      });
    });
  });
  };
  window.hlBindSort();
})();
</script>
"""


def _fmt_price(price, market: str, with_sym: bool = True) -> str:
    if price is None:
        return "—"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return _html.escape(str(price))
    sym, dec = _CUR.get(market, ("", 2))
    return f"{sym if with_sym else ''}{p:,.{dec}f}"


def fmt_mcap(mcap, market: str, with_sym: bool = True) -> str:
    """시총 표기. 입력 mcap = **억 단위**(현지통화 또는 USD) — finviz_client.
    _compute_highlow_from 이 yfinance marketCap / 1e8 로 저장(전 시장 공통 규약).
    US=억$→$T/$B/$M, 그 외=억(현지)→통화기호+억/조(10000억=1조).
    with_sym=False 면 통화기호 생략(헤더에만 표기 — 사용자 2026-06-13)."""
    if not mcap:
        return "—"
    try:
        v = float(mcap)            # 억 단위
    except (TypeError, ValueError):
        return "—"
    if v <= 0:
        return "—"
    if market == "US":
        s = "$" if with_sym else ""
        if v >= 10000:             # 10000억$ = $1T
            return f"{s}{v / 10000:.2f}T"
        if v >= 10:                # 10억$ = $1B
            return f"{s}{v / 10:.1f}B"
        return f"{s}{v * 100:.0f}M"
    sym = _CUR.get(market, ("", 2))[0] if with_sym else ""
    if v >= 10000:                 # 10000억 = 1조
        return f"{sym}{v / 10000:.1f}조"
    return f"{sym}{v:,.0f}억"


def ind_dist_line(items: list, top_k: int = 5) -> str:
    """패널 상단 업종 분포 한 줄 — 'Biotechnology 6 · 반도체 4 …' (순수)."""
    from collections import Counter
    cnt = Counter(str(it.get("ind")) for it in items if it.get("ind"))
    if not cnt:
        return ""
    parts = [f"{_html.escape(name)} {n}" for name, n in cnt.most_common(top_k)]
    extra = " 외" if len(cnt) > top_k else ""
    return (f'<div class="ind-dist" style="color:var(--muted);font-size:12px;'
            f'margin:2px 0 8px">업종 분포: {" · ".join(parts)}{extra}</div>')


def stock_panel(title: str, items: list, tid: str, market: str,
                extra_head: str = "", name_only: bool = False,
                show_vol: bool = True, show_ind: bool = True,
                show_mcap: bool = True, show_value: bool = False,
                vol_label: str = "거래량(주)", value_label: str = "거래대금") -> str:
    """리치 종목 패널 — 종목·현재가·등락률·(거래량)·(거래대금)·(시총)·(업종),
    헤더 클릭 정렬. **통화기호는 셀이 아닌 헤더에만**(사용자 2026-06-13).
    플래그: name_only·show_vol·show_value(거래대금)·show_ind·show_mcap.
    거래대금/시총 = it['value']/it['mcap'] 억 단위(fmt_mcap 규약).
    vol_label/value_label: 거래량·거래대금 컬럼 헤더 — 미국 장전·장후 보드는
    '정규장 거래량(주)'·'정규장 거래대금'으로 명확화(네이버 worldstock 이 시간외
    거래량/대금 미제공이라 정규장 값, 사용자 2026-06-16)."""
    if not items:
        return (f'<div class="panel"><h2>{title}</h2>'
                '<div class="empty">해당 종목 없음</div></div>')
    # CN/TW/HK 종목명 = 영문 통용명 기준 한글 음역(사용자 2026-06-15 '화봉전→윈본드').
    # 티커 기반 영구 캐시(Flash, 첫 1회만·이후 ₩0). **렌더-세이프(2026-06-16)**:
    # cache_only=True 로 캐시된 번역만 적용 → 매 30초/1h 재렌더가 네트워크·LLM 을
    # 블로킹하지 않음(TW 8.2s 블록과 동일 클래스 차단). 미캐시분은 백그라운드 워밍 →
    # 다음 렌더 반영. CN/HK 는 네이버 native 한글명이라 미캐시여도 원문이 이미 한글.
    if market in ("CN_A", "TW", "HK"):
        try:
            from bot.chart_translate import translate_names_kr
            _pairs = [(it.get("ticker"), it.get("name"))
                      for it in items if it.get("ticker")]
            _knm = translate_names_kr(_pairs, cache_only=True)
            for it in items:
                _k = _knm.get(it.get("ticker"))
                if _k:
                    it["name"] = _k
            _miss = [p for p in _pairs if p[0] not in _knm]
            if _miss:
                _kick_name_fill(_miss)
        except Exception:
            pass
    sym = _CUR.get(market, ("", 2))[0]
    cur_h = f" ({sym})" if sym else ""

    def _row(i: int, it: dict) -> str:
        _raw_tk = str(it.get("ticker", ""))
        tk = _html.escape(_raw_tk)
        # name 의 'TICKER | …' 중복 prefix 제거 후 escape (HK '0004.HK | 구룡창').
        nm = _html.escape(_strip_dup_ticker(_raw_tk, it.get("name") or _raw_tk))
        if name_only:
            label = nm or tk
        else:
            # 비-KR: 티커(1줄) + 한글명(2줄, .nk) — "TICKER (한글명)" 인라인이 좁은
            # 셀에서 단어 중간 줄바꿈돼 안 보이던 것 해소(사용자 2026-06-15, 한국제외
            # 전 시장·전 자식대시보드). 괄호 제거·줄 분리·keep-all 로 가독성.
            label = f'{tk}<span class="nk">{nm}</span>' if nm and nm != tk else tk
        price, pct = it.get("price"), it.get("pct")
        vol, mcap = it.get("vol"), it.get("mcap")
        ind = _html.escape(str(it.get("ind") or ""))
        try:
            pnum = float(price) if price is not None else None
        except (TypeError, ValueError):
            pnum = None
        cells = [
            f'<td class="rk">{i}</td>',
            f'<td class="nm"><a href="lookup/{tk}">{label}</a></td>',
            f'<td class="num">{_fmt_price(price, market, with_sym=False)}</td>',
            _pct_cell(pct),
        ]
        value = it.get("value")
        if show_vol:
            cells.append(f'<td class="num">{_fmt_vol(vol)}</td>')
        if show_value:
            cells.append(f'<td class="num">{fmt_mcap(value, market, with_sym=False)}</td>')
        if show_mcap:
            cells.append(f'<td class="num">{fmt_mcap(mcap, market, with_sym=False)}</td>')
        if show_ind:
            cells.append(f'<td class="ind" title="{ind}">{ind}</td>'
                         if ind else '<td class="ind">—</td>')
        data = (f'data-sym="{tk.lower()}" '
                f'data-price="{pnum if pnum is not None else -1}" '
                f'data-pct="{pct if pct is not None else -9999}" '
                f'data-vol="{vol if vol is not None else -1}" '
                f'data-value="{value if value is not None else -1}" '
                f'data-mcap="{mcap if mcap is not None else -1}" '
                f'data-ind="{ind.lower()}"')
        return f'<tr {data}>' + "".join(cells) + '</tr>'
    rows = "".join(_row(i, it) for i, it in enumerate(items, 1))
    heads = ['<th>#</th>',
             '<th class="srt" data-key="sym" data-type="text">종목</th>',
             f'<th class="srt" data-key="price" data-type="num" style="text-align:right">현재가{cur_h}</th>',
             '<th class="srt" data-key="pct" data-type="num" style="text-align:right">등락률</th>']
    if show_vol:
        heads.append(f'<th class="srt" data-key="vol" data-type="num" style="text-align:right">{vol_label}</th>')
    if show_value:
        heads.append(f'<th class="srt" data-key="value" data-type="num" style="text-align:right">{value_label}{cur_h}</th>')
    if show_mcap:
        heads.append(f'<th class="srt" data-key="mcap" data-type="num" style="text-align:right">시총{cur_h}</th>')
    if show_ind:
        heads.append('<th class="srt" data-key="ind" data-type="text">업종</th>')
    return (
        f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
        f'{extra_head}'
        f'<table class="hl-table{" nm-nowrap" if name_only else ""}" id="{tid}"><thead><tr>'
        + "".join(heads)
        + f'</tr></thead><tbody>{rows}</tbody></table></div>'
    )


def clean_source(src: str) -> str:
    """캐시에 박힌 옛 산출-기준 라벨 정규화 — '52주 고저 1% 근접'(옛)을 현재
    기준 '당일 52주 고저 갱신'(진짜 신고가/신저가)으로 렌더 시점 치환. stale
    캐시도 재스캔 대기 없이 즉시 올바른 라벨 표시 (사용자 2026-06-13 '진짜
    신고가 신저가 아냐' — 기준은 이미 당일 갱신, 라벨만 stale 였음). idempotent."""
    return (src or "").replace("52주 고저 1% 근접", "당일 52주 고저 갱신")


def sort_by_mcap(items: list) -> list:
    """시총 내림차순(없으면 뒤로) — 사용자 '시총순위대로 표시'. 52주 신고저용."""
    return sorted(items, key=lambda it: (it.get("mcap") is None,
                                         -(it.get("mcap") or 0)))


def sort_by_pct(items: list, gainers: bool = True) -> list:
    """등락률 순 — 급등(gainers)은 높은 %, 급락은 낮은(음수 큰) % 먼저. pct None 은
    뒤로. 사용자 2026-06-15 '모든 나라 급등락 기본화면을 시총순 아닌 등락률순으로'.
    (헤더 클릭으로 시총 등 재정렬은 그대로 — 기본 정렬만 변경.)"""
    return sorted(items, key=lambda it: (
        it.get("pct") is None,
        -(it.get("pct") or 0.0) if gainers else (it.get("pct") or 0.0)))


# (filter_min_mcap 시총 필터는 사용자 2026-06-14 '다시 생각' 으로 제거 — 시총
#  컬럼 표시·정렬은 유지. 향후 재도입 시 데이터-인지 버전을 git 이력에서 복원.)


_ENRICH_CACHE: dict = {}
_ENRICH_TTL = 600   # 10분 — render 비용 amortize
_ENRICH_REFRESHING: set = set()   # 백그라운드 full-enrich 진행 중 key (dedup)
_ENRICH_LOCK = _threading.Lock()

_NAME_FILLING: set = set()         # 백그라운드 종목명 번역 워밍 진행 중 (dedup)
_NAME_FILL_LOCK = _threading.Lock()


def _kick_name_fill(pairs: list) -> None:
    """미캐시 종목명을 백그라운드 daemon 으로 번역·캐시 워밍(렌더 블로킹 0, 2026-06-16
    T1). 다음 렌더에 cache_only 로 반영. dedup·graceful."""
    key = tuple(sorted(str(p[0]) for p in pairs if p and p[0]))
    if not key:
        return
    with _NAME_FILL_LOCK:
        if key in _NAME_FILLING:
            return
        _NAME_FILLING.add(key)

    def _run() -> None:
        try:
            from bot.chart_translate import translate_names_kr
            translate_names_kr(pairs)   # full(캐시 워밍) — 이후 렌더는 cache_only 히트
        except Exception:
            pass
        finally:
            with _NAME_FILL_LOCK:
                _NAME_FILLING.discard(key)

    _threading.Thread(target=_run, daemon=True, name="name-fill").start()


def _enrich_compute(tickers: list, items: list, market: str, want_ind: bool,
                    want_name: bool, allow_slow: bool) -> dict:
    """{ticker: {mcap,ind,name_kr}} 산출.

    allow_slow=False(렌더 경로) = **캐시/벌크맵만** — yfinance per-ticker(_fetch_
    mcaps fast_info+history / _fetch_display_names .info / _fetch_industries .info)
    + Flash 번역 **0** → 즉시 반환(사용자 2026-06-16 TW 렌더 8.2s 블록 제거).
    allow_slow=True(백그라운드 daemon) = yfinance 풀 fetch + 디스크 캐시 적재."""
    meta: dict = {}
    from bot.finviz_client import _cache_write, _cached, _fetch_mcaps
    # 시총: 렌더-세이프(allow_slow=False)는 persist(12h 디스크, 과거 산출분)만 —
    # yfinance fast_info+history per-ticker 는 백그라운드에서만(사용자 2026-06-14
    # 내구 캐시 비대칭 해소 위에 렌더-블록 제거 추가).
    mcaps = _fetch_mcaps(tickers) if allow_slow else {}
    pkey = f"enrich_mcap_{market}.json"
    persist = _cached(pkey, ttl=12 * 3600)
    persist = dict(persist) if isinstance(persist, dict) else {}
    changed = False
    for tk in tickers:
        mc = mcaps.get(tk)
        if mc:
            eok = round(mc / 1e8, 2)
            meta.setdefault(tk, {})["mcap"] = eok
            if persist.get(tk) != eok:
                persist[tk] = eok
                changed = True
        else:                       # yfinance 미스/렌더-세이프 → 직전 성공값 폴백
            meta.setdefault(tk, {})["mcap"] = persist.get(tk)
    if changed:
        _cache_write(pkey, persist)
    if want_ind:
        from bot.finviz_client import _fetch_industries
        inds = _fetch_industries(tickers, allow_slow=allow_slow)
        for tk in tickers:
            meta.setdefault(tk, {})["ind"] = inds.get(tk)
    _co = not allow_slow            # 렌더-세이프 → 번역 캐시-only(Flash 0)
    if want_name and market == "TW":
        # 대만 한국어명 (사용자 2026-06-14 '대만도 한글로' — 옛 영문 정책 번복):
        # 1차 yfinance longName(영문)·2차 中文 종목명 → 한국어 번역
        # (translate_titles_kr, 영구캐시). JP/HK 와 통일, 소형주까지.
        from bot.finviz_client import _fetch_display_names
        from bot.chart_translate import translate_titles_kr
        en = _fetch_display_names(tickers, allow_slow=allow_slow)
        ue = sorted({n for n in en.values() if n})
        ke = translate_titles_kr(ue, cache_only=_co) if ue else {}
        for tk in tickers:
            e = en.get(tk, "")
            if e:
                meta.setdefault(tk, {})["name_kr"] = ke.get(e) or e
        miss = [tk for tk in tickers
                if not meta.get(tk, {}).get("name_kr")]
        if miss:
            miss_set = set(miss)
            # 中文 native 명(STOCK_DAY_ALL Name — items 에 이미 있음) 한국어 번역.
            nat = {it.get("ticker"): it.get("name") for it in items
                   if it.get("ticker") in miss_set and it.get("name")
                   and it.get("name") != it.get("ticker")}
            uniq = sorted({v for v in nat.values() if v})
            if uniq:
                try:
                    kr = translate_titles_kr(uniq, cache_only=_co)
                    for tk, nm in nat.items():
                        if kr.get(nm):
                            meta.setdefault(tk, {})["name_kr"] = kr[nm]
                except Exception:
                    pass
    elif want_name:
        from bot.chart_translate import translate_titles_kr
        # 네이티브명(JPX 銘柄名 — items 에 이미 있음) **직접 번역**.
        # 옛 코드는 yfinance longName 만 번역해 longName 비populate 시
        # 南亞科 류가 그대로 노출됐음(사용자 2026-06-13 캡쳐). 네이티브명
        # 없는 항목만 longName 폴백.
        nat = {it.get("ticker"): it.get("name") for it in items
               if it.get("name") and it.get("name") != it.get("ticker")}
        uniq = sorted({v for v in nat.values() if v})
        kr = translate_titles_kr(uniq, cache_only=_co) if uniq else {}
        for tk, nm in nat.items():
            if kr.get(nm):
                meta.setdefault(tk, {})["name_kr"] = kr[nm]
        miss = [tk for tk in tickers if tk not in nat]
        if miss:
            from bot.finviz_client import _fetch_display_names
            en = _fetch_display_names(miss, allow_slow=allow_slow)
            ue = sorted({v for v in en.values() if v})
            ke = translate_titles_kr(ue, cache_only=_co) if ue else {}
            for tk in miss:
                e = en.get(tk, "")
                if e:
                    meta.setdefault(tk, {})["name_kr"] = ke.get(e) or e
    return meta


def _enrich_incomplete(meta: dict, tickers: list, want_ind: bool,
                       want_name: bool) -> bool:
    """캐시-only 결과에 누락분(미해소 시총/업종/한글명) 있으면 True → 백그라운드
    full enrich kick 판단."""
    for tk in tickers:
        m = meta.get(tk) or {}
        if m.get("mcap") is None:
            return True
        if want_ind and not m.get("ind"):
            return True
        if want_name and not m.get("name_kr"):
            return True
    return False


def _kick_enrich(key, items: list, market: str, want_ind: bool,
                 want_name: bool) -> None:
    """백그라운드 daemon — full(allow_slow) enrich 로 디스크/모듈 캐시 적재 →
    다음 렌더 즉시 반영(SWR). dedup(_ENRICH_REFRESHING). daemon 이라 종료 블로킹 0."""
    with _ENRICH_LOCK:
        if key in _ENRICH_REFRESHING:
            return
        _ENRICH_REFRESHING.add(key)

    def _run():
        try:
            tks = [it.get("ticker") for it in items if it.get("ticker")]
            meta = _enrich_compute(tks, items, market, want_ind, want_name, True)
            _ENRICH_CACHE[key] = (_time.time(), meta)
        except Exception as exc:
            log.warning("enrich bg (%s): %s", market, exc)
        finally:
            with _ENRICH_LOCK:
                _ENRICH_REFRESHING.discard(key)

    _threading.Thread(target=_run, daemon=True, name=f"enrich-{market}").start()


def enrich_for_panel(items: list, market: str, want_ind: bool = False,
                     want_name: bool = False) -> list:
    """상한가/하한가 등 단순 fetch 항목에 mcap(+업종/한글명) 백필 — stock_panel
    리치 표시용(사용자 2026-06-13 KR 시총·TW 업종). 항목은 'ticker' 보유 가정.

    **렌더-세이프 SWR (사용자 2026-06-16 TW 렌더 8.2s→~0s)**: 렌더는 캐시/벌크만으로
    즉시 반환(yfinance per-ticker·Flash 0), 누락분이 있으면 백그라운드 daemon 이
    full enrich 해 디스크/모듈 캐시 적재 → 다음 렌더에 반영. 10분 모듈 캐시 +
    graceful(실패 시 원본 유지). mcap=억(현지통화, fmt_mcap 규약)."""
    tickers = [it.get("ticker") for it in items if it.get("ticker")]
    if not tickers:
        return items
    key = (market, want_ind, want_name, tuple(sorted(set(tickers))))
    now = _time.time()
    hit = _ENRICH_CACHE.get(key)
    if hit and now - hit[0] < _ENRICH_TTL:
        meta = hit[1]
    else:
        try:
            meta = _enrich_compute(tickers, items, market, want_ind,
                                   want_name, False)   # 렌더-세이프(캐시-only)
        except Exception as exc:
            log.warning("enrich_for_panel(%s): %s", market, exc)
            return items
        _ENRICH_CACHE[key] = (now, meta)
        # 누락분 있으면 백그라운드 full enrich(yfinance) — 렌더는 안 막고 다음에 채움.
        if _enrich_incomplete(meta, tickers, want_ind, want_name):
            _kick_enrich(key, list(items), market, want_ind, want_name)
    for it in items:
        m = meta.get(it.get("ticker"), {})
        if m.get("mcap") is not None:
            it["mcap"] = m["mcap"]
        if want_ind and m.get("ind"):
            it["ind"] = m["ind"]
        if want_name and m.get("name_kr"):
            it["name"] = m["name_kr"]
    return items
