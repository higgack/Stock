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
from bot.translate import industry_kr as _industry_kr


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

# 시장별 장 시간(현지·KST 병기) — 대시보드 부제에 '장 시작·종료' 명시(사용자
# 2026-06-16 '각 대시보드에 장시작·종료·마지막 update·한계 명확히'). 서머타임 변동
# 큰 US 만 근사(EDT 기준, EST 면 +1h). KST=UTC+9 기준 환산.
_MKT_HOURS = {
    "KR": "장 09:00–15:30 KST",
    "JP": "장 09:00–15:00 JST(=KST)",
    "HK": "장 09:30–16:00 HKT(=KST 10:30–17:00)",
    "TW": "장 09:00–13:30 TST(=KST 10:00–14:30)",
    "CN_A": "장 09:30–15:00 CST(=KST 10:30–16:00)",
    "US": "장 09:30–16:00 ET(=KST 22:30–05:00, EDT 기준)",
}


def market_hours_label(market: str) -> str:
    """'장 09:00–15:30 KST' 류 장 시간 라벨(현지·KST). 미상 시 ''."""
    return _MKT_HOURS.get(market, "")


def _display_industry(ind) -> str:
    """업종 표시 정규화 — 영문 업종은 한글로 변환, 한글/기타는 원문 유지."""
    txt = str(ind or "").strip()
    if not txt:
        return ""
    kr = _industry_kr(txt)
    return kr if kr != txt else _industry_kr(txt.title())


def movers_freshness(market: str) -> str:
    """무버 제목 신선도 라벨 — 장중이면 '장중 30초 갱신', 장 밖·주말(토·일)이면
    '장 마감 · 마지막 거래일 종가 기준'. 사용자 2026-06-17 '무버 제목양식 토일' —
    토·일·장후에 '장중 30초'로 표기돼 라이브로 오인되던 것 정정(데이터는 마지막
    거래일 종가로 고정인데 제목만 라이브 주장). 시장시간(_SESSIONS_UTC) 단일 소스로
    판정. **네이버 라이브 무버(KR/US/JP/HK/CN) 전용** — TW 는 네이버 worldstock
    미지원이라 TWSE/TPEx 공식 EOD(종가) 소스라 라이브 30초 불가, tw_pages 가 별도
    'EOD' 라벨 사용(사용자 '대만 30초 적용가능?' → EOD 구조라 불가). _SESSIONS_UTC
    부재 시 보수적 라이브."""
    from datetime import datetime, timezone
    try:
        from bot.finviz_client import _SESSIONS_UTC
    except Exception:
        return "장중 30초 갱신"
    s = _SESSIONS_UTC.get(market)
    now = datetime.now(timezone.utc)
    is_open = (bool(s) and now.weekday() < 5
               and (s[0], s[1]) <= (now.hour, now.minute) < (s[2], s[3]))
    return "장중 30초 갱신" if is_open else "장 마감 · 마지막 거래일 종가 기준"


# 다중선택 드롭다운 공유 컨트롤 (필터 — 사용자 2026-06-17 '검색 말고 선택, 중복선택').
# in-popup 검색 내장(고카디널리티 종목/테마 대응). hl-table·cflt 양쪽이 prepend 해
# window.mkMultiSelect 1회 정의(guard). values 배열 → 체크박스 다중선택, onChange(선택배열).
# 선택값은 wrap._sel(배열)·clearMs()·data-fk 로 노출(applyFilter/live_refresh 가 읽음).
_MULTISELECT_JS = """
<style>
.ms{position:relative;display:block;width:100%}
.ms-btn{width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;text-align:left;
  background:var(--bg,#111);color:var(--text,#ddd);border:1px solid var(--border,#333);
  border-radius:3px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ms-btn.on{border-color:var(--accent,#3b82f6);color:var(--accent,#3b82f6)}
/* 팝업 내부는 host 페이지 CSS 변수에 의존하지 않고 명시 색 + !important 로 고정 —
   변수 미정의/특이도 충돌/host label{color} 룰로 라벨이 안 보이던 것 차단(사용자
   2026-06-17 '필터에 글씨가 안보이잖아'). 기본 다크 + 라이트 테마 명시 오버라이드. */
.ms-pop{position:absolute;z-index:40;top:100%;left:0;min-width:150px;max-width:260px;
  background:#161b22 !important;color:#e6edf3 !important;border:1px solid #30363d;
  border-radius:4px;padding:4px;box-shadow:0 4px 14px rgba(0,0,0,.5)}
.ms-srch{width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;margin-bottom:4px;
  background:#0e1117 !important;color:#e6edf3 !important;border:1px solid #30363d;border-radius:3px}
.ms-srch::placeholder{color:#8b949e}
.ms-list{max-height:210px;overflow-y:auto}
.ms-list label{display:block;font-size:11px;padding:2px 3px;white-space:nowrap;cursor:pointer;color:#e6edf3 !important}
.ms-list label:hover{background:#30363d}
/* 체크박스는 width:auto 강제 — 필터행 규칙 '.hl-filter input{width:100%}'(아래)이
   ms-list 체크박스에도 적용돼 라벨 텍스트를 화면 밖으로 밀어내던 것 차단(사용자
   2026-06-18 '필터 여전히 안나옴', 가로 스크롤 증거). 검색창(.ms-srch)은 100% 유지. */
.ms-list input{margin-right:5px;vertical-align:middle;width:auto !important;flex:none}
.ms-list label{display:flex;align-items:center}
[data-theme="light"] .ms-pop{background:#ffffff !important;color:#1f2328 !important;border-color:#d0d7de}
[data-theme="light"] .ms-srch{background:#f6f8fa !important;color:#1f2328 !important;border-color:#d0d7de}
[data-theme="light"] .ms-srch::placeholder{color:#6e7781}
[data-theme="light"] .ms-list label{color:#1f2328 !important}
[data-theme="light"] .ms-list label:hover{background:#eaeef2}
</style>
<script>
if(!window.mkMultiSelect){
window.mkMultiSelect=function(values, onChange){
  var wrap=document.createElement('span'); wrap.className='ms'; wrap._sel=[];
  var btn=document.createElement('button'); btn.type='button'; btn.className='ms-btn'; btn.textContent='전체';
  var pop=document.createElement('div'); pop.className='ms-pop'; pop.style.display='none';
  var srch=document.createElement('input'); srch.type='search'; srch.className='ms-srch'; srch.placeholder='검색';
  var list=document.createElement('div'); list.className='ms-list';
  var sel={};
  function sync(){
    wrap._sel=Object.keys(sel);
    btn.textContent=wrap._sel.length===0?'전체':(wrap._sel.length===1?wrap._sel[0]:wrap._sel.length+'개 선택');
    btn.className='ms-btn'+(wrap._sel.length?' on':'');
    if(onChange) onChange(wrap._sel);
  }
  (values||[]).forEach(function(v){
    var lab=document.createElement('label'); lab._v=v;
    var cb=document.createElement('input'); cb.type='checkbox';
    cb.addEventListener('change',function(){ if(cb.checked) sel[v]=1; else delete sel[v]; sync(); });
    lab.appendChild(cb); lab.appendChild(document.createTextNode(' '+v));
    list.appendChild(lab);
  });
  srch.addEventListener('input',function(){
    var q=srch.value.toLowerCase();
    Array.prototype.forEach.call(list.children,function(l){
      l.style.display=l._v.toLowerCase().indexOf(q)<0?'none':'';
    });
  });
  srch.addEventListener('click',function(e){ e.stopPropagation(); });
  btn.addEventListener('click',function(e){
    e.stopPropagation();
    var open=pop.style.display==='none';
    document.querySelectorAll('.ms-pop').forEach(function(p){ p.style.display='none'; });
    pop.style.display=open?'block':'none';
    if(open) srch.focus();
  });
  pop.addEventListener('click',function(e){ e.stopPropagation(); });
  document.addEventListener('click',function(){ pop.style.display='none'; });
  pop.appendChild(srch); pop.appendChild(list);
  wrap.appendChild(btn); wrap.appendChild(pop);
  wrap.clearMs=function(){ sel={};
    Array.prototype.forEach.call(list.querySelectorAll('input'),function(c){c.checked=false;}); sync(); };
  return wrap;
};
}
</script>
"""


# 정렬/업종 셀 스타일 + 헤더 클릭 정렬 + 컬럼별 필터 (us_pages 동일 — 단일 소스로 이관).
HL_SORT_JS = _MULTISELECT_JS + """
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
/* 컬럼별 필터행 (사용자 2026-06-17 '업종 등 모든 항목 필터로 좁혀보기'). */
.hl-table tr.hl-filter th{padding:2px 4px}
.hl-table tr.hl-filter input,.hl-table tr.hl-filter select{
  width:100%;box-sizing:border-box;font-size:11px;padding:2px 3px;
  background:var(--bg,#111);color:var(--fg,#ddd);
  border:1px solid var(--border,#333);border-radius:3px}
.hl-table tr.hl-filter .rng{display:flex;gap:2px}
.hl-table tr.hl-filter .rng input{width:50%;min-width:0}
.hl-table tr.hl-filter input::placeholder{color:var(--muted,#777)}
.hl-flt-clear{cursor:pointer;font-size:11px;line-height:1;padding:2px 5px;
  background:var(--bg,#111);color:var(--muted,#888);
  border:1px solid var(--border,#333);border-radius:3px}
.hl-flt-clear:hover{color:var(--accent,#3b82f6);border-color:var(--accent,#3b82f6)}
</style>
<script>
(function(){
  // 보이는 행만 1..N 재번호 (정렬·필터 공용). 숨김(display:none) 행 건너뜀.
  function renumber(tbl){
    var tb=tbl.tBodies[0]; if(!tb) return; var i=0;
    Array.prototype.forEach.call(tb.rows,function(r){
      if(r.style.display==='none') return;
      var rk=r.querySelector('.rk'); if(rk) rk.textContent=(++i);
    });
  }
  function sortTable(tbl, key, type, asc){
    var tb=tbl.tBodies[0];
    var rows=Array.prototype.slice.call(tb.rows);
    rows.sort(function(a,b){
      var x=a.getAttribute('data-'+key), y=b.getAttribute('data-'+key);
      if(type==='num'){ x=parseFloat(x); y=parseFloat(y);
        if(isNaN(x))x=-Infinity; if(isNaN(y))y=-Infinity;
        var d=asc?x-y:y-x;
        // 등락률 동률(상·하한가 다수)이면 시총 큰 순 tiebreak (사용자 2026-06-22).
        if(d===0 && key==='pct'){
          var mx=parseFloat(a.getAttribute('data-mcap')), my=parseFloat(b.getAttribute('data-mcap'));
          if(isNaN(mx))mx=-Infinity; if(isNaN(my))my=-Infinity; return my-mx;
        }
        return d; }
      x=(x||'').toString(); y=(y||'').toString();
      return asc? x.localeCompare(y): y.localeCompare(x);
    });
    rows.forEach(function(r){ tb.appendChild(r); });
    renumber(tbl);   // 필터로 숨겨진 행 제외하고 재번호
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

  // ── 컬럼별 필터 ─────────────────────────────────────────────────────
  // 헤더(th[data-key])를 읽어 필터행을 자동 생성 → stock_panel 의 모든 변형
  // (show_vol/value/mcap/ind 조합)·전 시장 hl-table 에 동일 적용(추가 Python 0).
  // 정렬과 같은 data-* 속성 공유: 텍스트=부분검색, 숫자=min~max 범위, 업종(ind)=
  // 현재 행에서 만든 드롭다운(정확 매칭). 다중 컬럼 동시 = AND.
  function colIndexMap(tbl){
    var m={}, hr=tbl.tHead.rows[0];
    Array.prototype.forEach.call(hr.cells,function(th,ci){
      var k=th.getAttribute('data-key'); if(k) m[k]=ci;
    });
    return m;
  }
  function _hlDistinct(tbl, ci){       // 컬럼 ci 의 고유 셀텍스트(정렬) — 다중선택 옵션
    var seen={}, out=[];
    Array.prototype.forEach.call(tbl.tBodies[0].rows,function(r){
      var t=r.cells[ci]?r.cells[ci].textContent.replace(/\\s+/g,' ').trim():'';
      if(!t||t==='—'||seen[t]) return; seen[t]=1; out.push(t);
    });
    out.sort(function(a,b){return a.localeCompare(b,'ko');});
    return out;
  }
  function applyFilter(tbl){
    var tb=tbl.tBodies[0]; if(!tb) return;
    var fr=tbl.tHead.querySelector('tr.hl-filter'); if(!fr) return;
    var cmap=colIndexMap(tbl);
    var nums=Array.prototype.slice.call(fr.querySelectorAll('input[data-bound]'));
    var mses=Array.prototype.slice.call(fr.querySelectorAll('.ms[data-fk]'));
    var total=tb.rows.length, vis=0;
    Array.prototype.forEach.call(tb.rows,function(r){
      var ok=true;
      for(var i=0;i<nums.length && ok;i++){      // 숫자 범위(현재가/등락률/거래량/시총…)
        var c=nums[i], v=c.value; if(v===''||v==null) continue;
        var fv=parseFloat(v); if(isNaN(fv)) continue;
        var num=parseFloat(r.getAttribute('data-'+c.getAttribute('data-fk')));
        if(isNaN(num)){ ok=false; }
        else if(c.getAttribute('data-bound')==='min'){ if(num<fv) ok=false; }
        else if(num>fv){ ok=false; }
      }
      for(var j=0;j<mses.length && ok;j++){       // 종목·업종 = 다중선택 멤버십(OR)
        var ms=mses[j]; if(!ms._sel||!ms._sel.length) continue;
        var ci=cmap[ms.getAttribute('data-fk')];
        var cell=(ci!=null && r.cells[ci])? r.cells[ci].textContent.replace(/\\s+/g,' ').trim():'';
        if(ms._sel.indexOf(cell)<0) ok=false;
      }
      r.style.display = ok?'':'none';
      if(ok) vis++;
    });
    renumber(tbl);
    var panel=tbl.closest && tbl.closest('.panel');
    var ts=panel && panel.querySelector('h2 .ts');
    if(ts){
      if(ts.getAttribute('data-base')==null) ts.setAttribute('data-base', ts.textContent);
      ts.textContent = (vis<total)? (vis+'/'+total+'종목 표시') : ts.getAttribute('data-base');
    }
  }
  function refreshIndOptions(tbl){
    var fr=tbl.tHead.querySelector('tr.hl-filter'); if(!fr) return;
    var sel=fr.querySelector('select.hl-flt[data-fk="ind"]'); if(!sel) return;
    var ci=colIndexMap(tbl)['ind']; if(ci==null) return;
    var prev=sel.value, seen={}, opts=[];
    Array.prototype.forEach.call(tbl.tBodies[0].rows,function(r){
      var val=r.getAttribute('data-ind')||'';
      var lab=r.cells[ci]?r.cells[ci].textContent.trim():'';
      if(!val||!lab||lab==='—'||seen[val]) return;
      seen[val]=1; opts.push([val,lab]);
    });
    opts.sort(function(a,b){return a[1].localeCompare(b[1],'ko');});
    sel.innerHTML='<option value="">업종 전체</option>'+opts.map(function(o){
      return '<option value="'+o[0].replace(/"/g,'&quot;')+'">'+o[1]+'</option>';
    }).join('');
    if(prev) sel.value=prev;   // 선택 보존(여전히 존재하면)
  }
  function mkNum(tbl,key,bound,ph){
    var inp=document.createElement('input');
    inp.type='number'; inp.className='hl-flt'; inp.placeholder=ph;
    inp.setAttribute('data-fk',key); inp.setAttribute('data-bound',bound);
    inp.addEventListener('input',function(){applyFilter(tbl);});
    return inp;
  }
  function clearFilters(tbl){
    var fr=tbl.tHead.querySelector('tr.hl-filter'); if(!fr) return;
    fr.querySelectorAll('input[data-bound]').forEach(function(c){c.value='';});
    fr.querySelectorAll('.ms').forEach(function(m){ if(m.clearMs) m.clearMs(); });
    applyFilter(tbl);
  }
  // window.hlBindFilter — hlBindSort 와 동일하게 live_refresh swap 후 재호출.
  window.hlBindFilter=function(){
    document.querySelectorAll('table.hl-table').forEach(function(tbl){
      var thead=tbl.tHead; if(!thead||!thead.rows[0]) return;
      if(!thead.querySelector('tr.hl-filter')){
        var fr=document.createElement('tr'); fr.className='hl-filter';
        Array.prototype.forEach.call(thead.rows[0].cells,function(th){
          var cell=document.createElement('th'); cell.className='fcell';
          var key=th.getAttribute('data-key'), type=th.getAttribute('data-type');
          if(!key){                                    // # 컬럼 → 필터 해제 버튼
            var b=document.createElement('button'); b.type='button';
            b.className='hl-flt-clear'; b.textContent='✕'; b.title='필터 해제';
            b.addEventListener('click',function(){clearFilters(tbl);});
            cell.appendChild(b);
          } else if(type==='num'){                     // 숫자 → min/max 범위
            var wrap=document.createElement('div'); wrap.className='rng';
            wrap.appendChild(mkNum(tbl,key,'min','≥'));
            wrap.appendChild(mkNum(tbl,key,'max','≤'));
            cell.appendChild(wrap);
          } else {                                     // 종목·업종 등 텍스트 → 다중선택 드롭다운
            var ci2=colIndexMap(tbl)[key];
            var ms=window.mkMultiSelect(ci2!=null?_hlDistinct(tbl,ci2):[],
                                        function(){applyFilter(tbl);});
            ms.setAttribute('data-fk',key);
            cell.appendChild(ms);
          }
          fr.appendChild(cell);
        });
        thead.appendChild(fr);
      }
      applyFilter(tbl);
    });
  };
  window.hlBindSort();
  window.hlBindFilter();
})();
</script>
"""


# ── 범용 컬럼 필터 (hl-table 이 아닌 표 — 테마·업종강도·NXT, 사용자 2026-06-17) ──
# hl-table 은 data-key/data-type 스킴이라 hlBindFilter 가 붙지만, 테마(data-k)·
# 업종강도(속성 없음)·NXT(nxt-tbl) 는 컨벤션이 제각각. 그래서 **보이는 셀 텍스트
# 기반** 범용 필터를 별도로 둔다(데이터 속성 무의존). `class="cflt"` 표에 자동
# 부착: 첫칸=✕해제, 링크/단위(억·조·만·T·B·M) 셀 컬럼=텍스트 검색, 그 외 숫자
# 컬럼(등락률·% 등)=min~max 범위. 순수 텍스트(종목/업종/사업부문)=다중선택 드롭다운
# (사용자 2026-06-17 '검색 말고 선택·중복선택'). 억/조/만 등 단위 혼재 숫자컬럼은
# 그대로 검색 유지('숫자부분은 그대로').
GENERIC_FILTER_JS = _MULTISELECT_JS + """
<style>
table.cflt tr.cflt-row th{padding:2px 4px}
table.cflt tr.cflt-row input{width:100%;box-sizing:border-box;font-size:11px;padding:2px 3px;background:var(--bg,#111);color:var(--text,#ddd);border:1px solid var(--border,#333);border-radius:3px}
table.cflt tr.cflt-row .rng{display:flex;gap:2px}
table.cflt tr.cflt-row .rng input{width:50%;min-width:0}
table.cflt tr.cflt-row input::placeholder{color:var(--muted,#777)}
.cflt-clear{cursor:pointer;font-size:11px;line-height:1;padding:2px 5px;background:var(--bg,#111);color:var(--muted,#888);border:1px solid var(--border,#333);border-radius:3px}
.cflt-clear:hover{color:var(--accent,#3b82f6);border-color:var(--accent,#3b82f6)}
</style>
<script>
(function(){
  function pnum(s){var m=(s||'').replace(/,/g,'').match(/-?\\d+(?:\\.\\d+)?/);return m?parseFloat(m[0]):NaN;}
  function firstRow(tb){for(var i=0;i<tb.rows.length;i++){var r=tb.rows[i];if(r.cells.length>1&&!r.querySelector('.empty'))return r;}return null;}
  function colType(th,idx,tb){
    var ft=th.getAttribute('data-ft');if(ft)return ft;        // 명시 우선
    if(idx===0||(th.textContent||'').trim()==='#')return 'none';
    // 데이터 행 샘플(≤12)로 판정 — 첫 행이 '—'여도 robust.
    var rows=tb.tBodies[0]?tb.tBodies[0].rows:[],nNum=0,nUnit=0,nText=0,nLink=0,seen=0;
    for(var i=0;i<rows.length&&seen<12;i++){
      var r=rows[i];if(r.querySelector('.empty')||!r.cells[idx])continue;
      var c=r.cells[idx],t=c.textContent.trim();if(t===''||t==='—')continue;seen++;
      if(c.querySelector('a')){nLink++;continue;}
      if(/[억조만]|[TBM]\\b/.test(t)){nUnit++;continue;}      // 억/조/만 단위 숫자
      if(/\\d/.test(t)&&!isNaN(pnum(t))){nNum++;continue;}
      nText++;
    }
    if(nLink>0&&nLink>=nText)return 'text';   // 링크(종목/회사) → 다중선택
    if(nUnit>nNum&&nUnit>=nText)return 'unit'; // 억/조/만 → 검색 유지(숫자부분 그대로)
    if(nNum>0&&nNum>=nText)return 'num';        // 순수 숫자 → min~max 범위
    return 'text';                              // 순수 텍스트 → 다중선택
  }
  function cdistinct(tbl,ci){          // 컬럼 ci 고유 셀텍스트(정렬) — 다중선택 옵션
    var seen={},out=[];
    Array.prototype.forEach.call(tbl.tBodies[0].rows,function(r){
      if(r.querySelector('.empty')||!r.cells[ci])return;
      var t=r.cells[ci].textContent.replace(/\\s+/g,' ').trim();
      if(!t||t==='—'||seen[t])return;seen[t]=1;out.push(t);
    });
    out.sort(function(a,b){return a.localeCompare(b,'ko');});
    return out;
  }
  function renum(tbl){var tb=tbl.tBodies[0];if(!tb)return;var i=0;
    Array.prototype.forEach.call(tb.rows,function(r){if(r.style.display==='none'||r.querySelector('.empty'))return;
      var rk=r.querySelector('.rk');if(rk)rk.textContent=(++i);});}
  function apply(tbl){
    var tb=tbl.tBodies[0];if(!tb)return;
    var fr=tbl.tHead.querySelector('tr.cflt-row');if(!fr)return;
    var ins=Array.prototype.slice.call(fr.querySelectorAll('input'));
    var mses=Array.prototype.slice.call(fr.querySelectorAll('.ms[data-ci]'));
    Array.prototype.forEach.call(tb.rows,function(r){
      if(r.querySelector('.empty'))return;
      var ok=true;
      for(var i=0;i<ins.length&&ok;i++){           // 숫자 범위 + 단위 검색
        var c=ins[i],v=c.value;if(v==='')continue;
        var ci=+c.getAttribute('data-ci'),cell=r.cells[ci];if(!cell)continue;
        var b=c.getAttribute('data-b');
        if(b){var fv=pnum(v);if(isNaN(fv))continue;var n=pnum(cell.textContent);
          if(isNaN(n)){ok=false;}else if(b==='min'){if(n<fv)ok=false;}else if(n>fv){ok=false;}}
        else if(cell.textContent.toLowerCase().indexOf(v.toLowerCase())<0){ok=false;}
      }
      for(var j=0;j<mses.length&&ok;j++){          // 다중선택 멤버십(OR)
        var ms=mses[j];if(!ms._sel||!ms._sel.length)continue;
        var mc=r.cells[+ms.getAttribute('data-ci')];
        var ct=mc?mc.textContent.replace(/\\s+/g,' ').trim():'';
        if(ms._sel.indexOf(ct)<0)ok=false;
      }
      r.style.display=ok?'':'none';
    });
    renum(tbl);
  }
  function mkNum(tbl,ci,b,ph){var i=document.createElement('input');i.type='number';i.placeholder=ph;
    i.setAttribute('data-ci',ci);i.setAttribute('data-b',b);i.addEventListener('input',function(){apply(tbl);});return i;}
  window.bindCflt=function(){
    document.querySelectorAll('table.cflt').forEach(function(tbl){
      var thead=tbl.tHead;if(!thead||!thead.rows[0]||thead.querySelector('tr.cflt-row'))return;
      var fr=document.createElement('tr');fr.className='cflt-row';
      Array.prototype.forEach.call(thead.rows[0].cells,function(th,idx){
        var cell=document.createElement('th'),ty=colType(th,idx,tbl);
        if(idx===0){var b=document.createElement('button');b.type='button';b.className='cflt-clear';
          b.textContent='✕';b.title='필터 해제';
          b.addEventListener('click',function(){
            fr.querySelectorAll('input').forEach(function(x){x.value='';});
            fr.querySelectorAll('.ms').forEach(function(m){if(m.clearMs)m.clearMs();});apply(tbl);});
          cell.appendChild(b);}
        else if(ty==='num'){var w=document.createElement('div');w.className='rng';
          w.appendChild(mkNum(tbl,idx,'min','≥'));w.appendChild(mkNum(tbl,idx,'max','≤'));cell.appendChild(w);}
        else if(ty==='unit'){var inp=document.createElement('input');inp.type='search';
          inp.setAttribute('data-ci',idx);inp.placeholder='검색';
          inp.addEventListener('input',function(){apply(tbl);});cell.appendChild(inp);}
        else{var ms=window.mkMultiSelect(cdistinct(tbl,idx),function(){apply(tbl);});
          ms.setAttribute('data-ci',idx);cell.appendChild(ms);}
        fr.appendChild(cell);
      });
      thead.appendChild(fr);
    });
  };
  window.bindCflt();
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
    inds = [_display_industry(it.get("ind")) for it in items]
    cnt = Counter(ind for ind in inds if ind)
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
                vol_label: str = "거래량(주)", value_label: str = "거래대금",
                limit_pct: float | None = None) -> str:
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
        # 상·하한가(漲停/跌停) 마커 — limit_pct 설정 시(TW=9.9) 일일 한도 도달
        # 종목 표시(T12 2026-06-16). 추가 fetch 0(기존 pct 사용). 기본 None=무표시.
        if limit_pct and isinstance(pct, (int, float)):
            if pct >= limit_pct:
                label = "🔺 " + label
            elif pct <= -limit_pct:
                label = "🔻 " + label
        vol, mcap = it.get("vol"), it.get("mcap")
        ind_txt = _display_industry(it.get("ind"))
        ind = _html.escape(ind_txt)
        ind_key = _html.escape(ind_txt.lower())
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
                f'data-ind="{ind_key}"')
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
    동률(상·하한가 다수 +10.00% 등)이면 **시총 큰 순** tiebreak (사용자 2026-06-22 —
    가나다순 아니라 시총순). (헤더 클릭 재정렬은 그대로 — 기본 정렬만.)"""
    return sorted(items, key=lambda it: (
        it.get("pct") is None,
        -(it.get("pct") or 0.0) if gainers else (it.get("pct") or 0.0),
        it.get("mcap") is None,
        -(it.get("mcap") or 0.0)))


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
    # T7(2026-06-16): TW 는 네이버 worldstock 시총 overlay 가 없어 fast_info(rate-
    # limit 회로차단 1순위) 의존이 컸음 → '시총 —' 빈출. yfinance 미스 시 FinMind
    # 발행주식수(NumberOfSharesIssued)×종가로 폴백 산출(백그라운드에서만, persist
    # 적재 → 렌더-세이프 경로가 다음 렌더에 사용). 단위: shares×종가(NT$)/1e8 = 억 NT$.
    _tw_px = {it.get("ticker"): it.get("price") for it in items} if market == "TW" else {}
    for tk in tickers:
        mc = mcaps.get(tk)
        if mc:
            eok = round(mc / 1e8, 2)
            meta.setdefault(tk, {})["mcap"] = eok
            if persist.get(tk) != eok:
                persist[tk] = eok
                changed = True
        else:                       # yfinance 미스/렌더-세이프 → 직전 성공값 폴백
            eok = persist.get(tk)
            if eok is None and allow_slow and market == "TW":
                try:
                    from bot.finmind_client import fetch_shares_outstanding
                    sh = fetch_shares_outstanding(tk)
                    px = _tw_px.get(tk)
                    if sh and px:
                        eok = round(sh * float(px) / 1e8, 2)
                        persist[tk] = eok
                        changed = True
                except Exception:
                    pass
            meta.setdefault(tk, {})["mcap"] = eok
    if changed:
        _cache_write(pkey, persist)
    if want_ind:
        from bot.finviz_client import _industries_for
        inds = _industries_for(tickers, market, allow_slow=allow_slow)
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
