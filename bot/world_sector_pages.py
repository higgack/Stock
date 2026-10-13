"""국가별 섹터 3보드 렌더 — `sector_mix.html`(국가별 섹터혼합) · `sentiment_map.html`(국가별
심리지도) · `sector_stocks.html`(국가별 섹터종목). 사용자 2026-10-12.

재료는 전부 `bot.world_sectors` 가 디스크에 남긴 하루치 집계·원자료와 `bot.etf_holdings` 의
보유 종목이다 — 렌더는 네트워크를 한 번도 부르지 않는다. 종목 표(심리지도 칸을 누르면 열리는
표 · 섹터종목 표)는 **한 JS 함수**가 그린다(같은 표를 두 곳에서 따로 만들면 갈라진다, #38).
그 표의 숫자(주간 등락·상승일 거래·거래 몫·시총 증감)는 파이썬(`world_sectors.stock_week_row`)
이 만들고 JS 는 꾸미기·정렬만 한다.
"""
from __future__ import annotations

import html as _h
import json
import logging
import os
import re
from datetime import date as _date
from datetime import datetime, timedelta

from bot import world_sectors as ws

log = logging.getLogger("bot.world_sector_pages")

_WD = "월화수목금토일"
_GEN = "\x00GEN\x00"     # 생성 시각 자리 — 내용 비교에서 빼려고 마지막에 채운다
CURRENCY = {"KR": "원", "US": "달러", "JP": "엔", "TW": "대만달러", "CN_A": "위안"}
RENDER_DAYS = 10          # 섹터혼합 — 최근 10기준일만 싣는다(그 앞은 기록 보관만)
STOCK_MARKETS = ws.HOLD_MARKETS   # 섹터종목 — Breadth 전략 섹터 ETF 가 있는 나라(단일 출처 #38)

SENT_HELP = {
    "hot_up": "거래가 몰리며 시장보다 강했다",
    "quiet_up": "거래는 평소만큼인데 시장보다 강했다",
    "calm": "거래도 등락도 평범했다",
    "churn": "등락은 평범한데 거래가 몰렸다(손이 바뀌는 중)",
    "fade": "거래가 늘지 않은 채 시장보다 약했다",
    "hot_down": "거래가 몰리며 시장보다 약했다",
    "up": "상대 거래대금 기준이 없어 등락만으로 갈랐다",
    "flat": "상대 거래대금 기준이 없어 등락만으로 갈랐다",
    "down": "상대 거래대금 기준이 없어 등락만으로 갈랐다",
}
BASIS_LABEL = {"20d": "직전 20거래일 평균", "approx": "30일 평균 거래량×종가 근사",
               "mixed": "20거래일 평균·근사 섞임"}


def market_name(m: str) -> str:
    from bot.tv_scanner import MARKETS
    _tv, label, flag = MARKETS[m]
    return f"{flag} {label}"


_SUFFIX = {"TSE": ".T", "TWSE": ".TW", "TPEX": ".TWO", "SSE": ".SS", "SZSE": ".SZ", "BSE": ".BJ"}
_US_EX = ("NYSE", "NASDAQ", "AMEX")      # `tv_scanner.EXCHANGES["US"]` 와 같은 셋


def lookup_ticker(sym: str) -> str:
    """TradingView 기호(`TSE:7203`) → 종목분석 화면 티커(yfinance 꼴, 순수). 모르는 거래소면 "".

    한국은 6자리 그대로(분석 화면 리졸버가 .KS/.KQ 를 정한다, #399). 모르는 접두(일본 나고야
    `NAG`·후쿠오카 `FSE`·삿포로 `SAPSE` 주상장 등)는 링크를 걸지 않는다 — 미국식으로 떨어뜨리면
    `lookup/7203` 같은 맨 숫자가 국내 리졸버로 풀려 남의 회사 화면을 연다(#399·#400)."""
    ex, _, code = str(sym).rpartition(":")
    ex = ex.upper()
    if ex == "KRX":
        return code
    if ex in _SUFFIX:
        return code + _SUFFIX[ex]
    if ex in _US_EX:
        return code.replace(".", "-")          # 미국 BRK.B → BRK-B
    return ""


def lookup_href(sym: str) -> str:
    lk = lookup_ticker(sym)
    return f"lookup/{lk}" if lk else ""


def _pp(v, digits: int = 2) -> str:
    """시장 대비 %p — 반올림해 0 이면 부호를 붙이지 않는다('-0.0%p' 는 없는 방향을 말한다)."""
    if v is None:
        return "—"
    if round(v, digits) == 0:
        return f"{0:.{digits}f}%p"
    return f"{v:+.{digits}f}%p"


def _cls(v) -> str:
    if v is None:
        return "ws-na"
    return "ws-up" if v > 0 else "ws-dn" if v < 0 else "ws-flat"


DAY_TEXT = {"closed": "휴장", "holiday": "휴장 추정", "pending": "대기", "missing": "수집 못 함"}


def week_tag(mon: _date, now: datetime) -> str:
    """주 머리말 꼬리 — 오늘(KST)이 든 주일 때만 '이번 주'. 마지막 저장일로 정하면 월요일 아침
    (미국은 화요일 아침까지) 지난주를 '이번 주' 라 부른다(#34)."""
    this = ws.week_monday(ws._now(now).date().isoformat())
    if mon == this:
        return " — 이번 주"
    if mon == this - timedelta(days=7):
        return " — 지난주"
    return ""


def _md(d: str) -> str:
    dd = _date.fromisoformat(d)
    return f"{_WD[dd.weekday()]} {dd.month}/{dd.day}"


_WS_CSS = """
<style>
:root{--bg:#f7f8f9;--card:#ffffff;--ws-up:#c62828;--ws-dn:#1d4ed8;--ws-cell-fg:#282a30;
 --ws-hu:#e88f8f;--ws-qu:#f7d4d4;--ws-ca:#eef0f3;--ws-ch:#eddcc4;--ws-fa:#d3def5;--ws-hd:#8fb0ec}
:root[data-theme="dark"]{--bg:#0f1117;--card:#1a1d27;--ws-up:#f87171;--ws-dn:#60a5fa;--ws-cell-fg:#e4e6ed;
 --ws-hu:#8e3a3a;--ws-qu:#4d2c30;--ws-ca:#232733;--ws-ch:#54452f;--ws-fa:#27375a;--ws-hd:#33539a}
.ws-status{background:var(--surface2);border-left:3px solid var(--accent);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--fg);margin:10px 0;line-height:1.7}
.ws-up{color:var(--ws-up)}.ws-dn{color:var(--ws-dn)}.ws-flat,.ws-na{color:var(--muted)}
.ws-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.ws-tab{padding:6px 12px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:2px solid transparent;background:var(--card);color:var(--muted);box-shadow:0 0 0 1px var(--border)}
.ws-tab.active{border-color:var(--pillbd);color:var(--fg)}
.ws-pane{display:none}.ws-pane.active{display:block}
.ws-day{background:var(--card);border:1px solid var(--border);border-radius:12px;margin:10px 0;padding:4px 14px}
.ws-day>summary,.ws-month>summary{cursor:pointer;font-weight:600;padding:8px 4px}
.ws-month{margin:12px 0}
.ws-month>summary{font-size:15px;border-bottom:1px solid var(--border)}
.ws-meta{font-weight:400;font-size:12px;color:var(--muted);margin-left:8px}
.ws-tbl{width:100%;border-collapse:collapse;font-size:12.5px;background:var(--card)}
.ws-tbl th{color:var(--muted);text-align:right;padding:7px 8px;border-bottom:1px solid var(--border);font-size:12px;white-space:nowrap}
.ws-tbl th:first-child,.ws-tbl td:first-child{text-align:left}
.ws-tbl td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;color:var(--fg);vertical-align:top}
.ws-tbl td.ws-l,.ws-tbl th.ws-l{text-align:left}
.ws-v{font-weight:700}.ws-sub{font-size:11px;color:var(--muted);margin-top:2px}
.ws-pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600;border:1px solid var(--border);color:var(--muted);white-space:nowrap}
.ws-pill.up{border-color:var(--ws-up);color:var(--ws-up)}.ws-pill.dn{border-color:var(--ws-dn);color:var(--ws-dn)}
.ws-cell{color:var(--ws-cell-fg);cursor:pointer;min-width:84px}
.ws-cell .ws-s{font-size:11.5px;font-weight:700}.ws-cell .ws-p{font-size:11px;opacity:.85}
.ws-c-hu{background:var(--ws-hu)}.ws-c-qu{background:var(--ws-qu)}.ws-c-ca{background:var(--ws-ca)}
.ws-c-ch{background:var(--ws-ch)}.ws-c-fa{background:var(--ws-fa)}.ws-c-hd{background:var(--ws-hd)}
.ws-approx{outline:1px dashed var(--muted);outline-offset:-3px}
.ws-wk{border-left:2px solid var(--border)}
.ws-map{table-layout:fixed}.ws-map th:first-child{width:19%}
.ws-off{color:var(--muted);font-size:11.5px}
.ws-n{color:var(--muted);font-size:11px;margin-left:4px}
.ws-detail{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px;margin:14px 0}
.ws-detail-h{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.ws-detail-k{font-size:12px;color:var(--accent);font-weight:600}
.ws-detail-t{font-size:18px;font-weight:700;margin:2px 0}
.ws-btn{padding:4px 12px;border-radius:999px;border:1px solid var(--border);background:var(--card);color:var(--fg);cursor:pointer;font-size:12px}
.ws-find{padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--fg);font-size:13px;min-width:220px}
.ws-scroll{overflow-x:auto}
.ws-code{color:var(--muted);font-size:11px;margin-left:4px}
.ws-nm a{color:var(--fg);text-decoration:none;font-weight:600}.ws-nm a:hover{text-decoration:underline}
.ws-ind{color:var(--muted);font-size:12px}
.js-ws-sort{cursor:pointer}
.ws-hl{box-shadow:inset 0 0 0 2px var(--accent)}
.ws-legend{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0;font-size:11.5px}
.ws-legend span{padding:3px 8px;border-radius:6px;color:var(--ws-cell-fg)}
</style>
"""

# 탭 · 종목 표 · 칸 상세 — 서버가 찍은 데이터(JSON)를 읽기만 한다(#48 문구를 JS 가 만들지 않는다:
# 머리말·사유는 서버가 넣고, JS 는 숫자 꾸미기·정렬·검색만 한다).
_WS_JS = r"""
(function(){
  function each(list,fn){[].slice.call(list||[]).forEach(fn);}
  // 탭 — data-tabs 묶음 안에서 같은 data-tab 의 창만 보인다
  each(document.querySelectorAll('[data-tabs]'),function(box){
    each(box.querySelectorAll('.ws-tab'),function(b){
      b.addEventListener('click',function(){
        var g=box.getAttribute('data-tabs');
        each(box.querySelectorAll('.ws-tab'),function(x){x.classList.toggle('active',x===b);});
        each(document.querySelectorAll('.ws-pane[data-group="'+g+'"]'),function(p){
          p.classList.toggle('active',p.getAttribute('data-tab')===b.getAttribute('data-tab'));});
      });
    });
  });
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
  function money(v,cur,sign){
    if(v==null) return '—';
    var a=Math.abs(v), s;
    if(a>=1e12) s=(a/1e12).toFixed(a>=1e13?1:2)+'조';
    else if(a>=1e8) s=Math.round(a/1e8).toLocaleString('en-US')+'억';
    else if(a>=1e4) s=Math.round(a/1e4).toLocaleString('en-US')+'만';
    else s=Math.round(a).toLocaleString('en-US');
    var p=v<0?'-':(sign&&v>0?'+':'');
    return p+s+(cur?' '+cur:'');
  }
  function tint(v,scale){
    if(v==null||!isFinite(v)||v===0) return '';
    var a=Math.min(Math.abs(v)/scale,1)*0.5+0.06;
    return v>0?'background:rgba(220,38,38,'+a.toFixed(2)+')':'background:rgba(37,99,235,'+a.toFixed(2)+')';
  }
  function pct(v,d){
    if(v==null) return '—';
    var t=v.toFixed(d);
    if(+t===0) return (0).toFixed(d);          // '-0.0' 은 없는 방향을 말한다
    return (v>0?'+':'')+t;
  }
  // 종목 표 — data = {days:[...], status:[...], cur, rows:[{c,nm,ind,href,cap,chg[],wk,up,sh,dcap,w}]}
  function table(el,data,opts){
    opts=opts||{};
    var cols=[['nm','종목',1],['ind','산업',1]];
    if(opts.weight) cols.push(['w','ETF 비중',0]);
    data.days.forEach(function(d,i){cols.push(['d'+i,data.dayLabels[i],0]);});
    cols=cols.concat([['wk','주간',0],['up','상승일 거래',0],['sh','거래 몫',0],['cap','시총',0],['dcap','시총 증감',0]]);
    var key=opts.sort||'cap', dir=-1, q='';
    function val(r,k){
      if(k.charAt(0)==='d'&&k!=='dcap') return r.chg[+k.slice(1)];
      return r[k];
    }
    function draw(){
      var rows=data.rows.filter(function(r){
        return !q||(String(r.nm)+' '+r.c+' '+r.ind).toLowerCase().indexOf(q)>=0;});
      rows.sort(function(a,b){
        var x=val(a,key), y=val(b,key);
        if(typeof x==='string'||typeof y==='string') return dir*String(x).localeCompare(String(y));
        if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1;
        return dir*(x-y);
      });
      var h='<div class="ws-scroll"><table class="ws-tbl"><thead><tr>';
      cols.forEach(function(c){
        var mark=c[0]===key?(dir<0?' ▾':' ▴'):'';
        h+='<th class="js-ws-sort'+(c[2]?' ws-l':'')+'" data-k="'+c[0]+'">'+esc(c[1])+mark+'</th>';
      });
      h+='</tr></thead><tbody>';
      rows.forEach(function(r){
        h+='<tr><td class="ws-l ws-nm">'+(r.href?'<a href="'+esc(r.href)+'">'+esc(r.nm)+'</a>':esc(r.nm))
          +'<span class="ws-code">'+esc(r.c)+'</span></td><td class="ws-l ws-ind">'+esc(r.ind||'—')+'</td>';
        if(opts.weight) h+='<td>'+(r.w==null?'—':r.w.toFixed(2)+'%')+'</td>';
        r.chg.forEach(function(v,i){
          var hl=(opts.day===i)?' ws-hl':'';
          if(data.status[i]!=='ok'){h+='<td class="ws-off'+hl+'">·</td>';return;}
          h+='<td class="'+hl+'" style="'+tint(v,10)+'">'+(v==null?'·':pct(v,1))+'</td>';
        });
        h+='<td class="'+(r.wk>0?'ws-up':r.wk<0?'ws-dn':'')+'" style="'+tint(r.wk,20)+'">'
          +(r.wk==null?'—':pct(r.wk,1)+'%')+'</td>';
        h+='<td style="'+(r.up==null?'':tint(r.up-50,50))+'">'+(r.up==null?'—':Math.round(r.up)+'%')+'</td>';
        h+='<td>'+(r.sh==null?'—':r.sh.toFixed(2)+'%')+'</td>';
        h+='<td>'+money(r.cap,data.cur)+'</td>';
        h+='<td class="'+(r.dcap>0?'ws-up':r.dcap<0?'ws-dn':'')+'">'+money(r.dcap,data.cur,true)+'</td></tr>';
      });
      if(!rows.length) h+='<tr><td class="ws-l" colspan="'+cols.length+'">'+esc(opts.empty||'해당 종목 없음')+'</td></tr>';
      h+='</tbody></table></div>';
      var tb=el.querySelector('.js-ws-body');
      tb.innerHTML=h;
      each(tb.querySelectorAll('.js-ws-sort'),function(th){
        th.addEventListener('click',function(){
          var k=th.getAttribute('data-k');
          if(k===key) dir=-dir; else {key=k; dir=(k==='nm'||k==='ind')?1:-1;}
          draw();
        });
      });
      var cnt=el.querySelector('.js-ws-count');
      if(cnt) cnt.textContent=rows.length+'종목'+(q?' (찾음)':'');
    }
    var f=el.querySelector('.js-ws-find');
    if(f) f.addEventListener('input',function(){q=f.value.trim().toLowerCase();draw();});
    draw();
  }
  window.wsTable=table;
  // 섹터종목 — 서버가 실은 JSON 으로 ETF 표를 그린다
  each(document.querySelectorAll('.js-ws-etf'),function(el){
    var src=document.getElementById(el.getAttribute('data-src'));
    if(!src) return;
    table(el,JSON.parse(src.textContent),{weight:true,sort:'w',empty:el.getAttribute('data-empty')});
  });
  // 심리지도 칸 — 그 나라 종목 JSON 을 처음 누를 때 한 번 받는다(상대 경로 — 토큰 경로 보존)
  var cache={};
  function openCell(td){
    var m=td.getAttribute('data-m'), mon=td.getAttribute('data-mon');
    var box=document.getElementById('ws-detail-'+m);
    if(!box) return;
    var go=function(js){
      // 주는 순서가 아니라 월요일 날짜로 찾는다 — 열어 둔 옛 페이지에서 새 주가 들어온 뒤 누르면
      // 순서로는 다른 주 종목이 옛 머리말 아래 그려진다
      var wk=null; (js.weeks||[]).forEach(function(x){if(x.monday===mon) wk=x;});
      if(!wk){box.style.display='block';
        box.querySelector('.js-ws-body').innerHTML='<div class="ws-off">'+esc(box.getAttribute('data-gone'))+'</div>';
        return;}
      var kind=td.getAttribute('data-kind'), key=td.getAttribute('data-key');
      var day=td.getAttribute('data-day'); day=(day===null||day==='')?null:+day;
      var idx=(kind==='sec'?wk.secs:wk.inds).indexOf(key);
      var rows=wk.rows.filter(function(r){return (kind==='sec'?r[2]:r[3])===idx;}).map(function(r){
        return {c:r[0],nm:r[1],ind:wk.inds[r[3]]!=null?(wk.indLabels[r[3]]||wk.inds[r[3]]):'',
          href:r[10],cap:r[4],chg:r[5],wk:r[6],up:r[7],sh:r[8],dcap:r[9]};});
      box.querySelector('.js-ws-k').textContent=td.getAttribute('data-head');
      box.querySelector('.js-ws-t').textContent=td.getAttribute('data-title');
      box.querySelector('.js-ws-s').textContent=td.getAttribute('data-sum');
      var f=box.querySelector('.js-ws-find'); if(f) f.value='';
      box.style.display='block';
      table(box,{days:wk.days,dayLabels:wk.dayLabels,status:wk.status,cur:js.cur,rows:rows},{day:day});
      box.scrollIntoView({behavior:'smooth',block:'nearest'});
    };
    if(cache[m]){go(cache[m]);return;}
    box.style.display='block';
    box.querySelector('.js-ws-body').innerHTML='<div class="ws-off">종목을 불러오는 중…</div>';
    fetch(td.getAttribute('data-src')).then(function(r){
      if(!r.ok) throw new Error('HTTP '+r.status); return r.json();
    }).then(function(js){cache[m]=js;go(js);}).catch(function(e){
      box.querySelector('.js-ws-body').innerHTML='<div class="ws-off">종목 자료를 못 불러왔습니다('+esc(e.message)+')</div>';
    });
  }
  each(document.querySelectorAll('td.js-ws-cell'),function(td){td.addEventListener('click',function(){openCell(td);});});
  each(document.querySelectorAll('.js-ws-close'),function(b){b.addEventListener('click',function(){
    var box=b.closest('.ws-detail'); if(box) box.style.display='none';});});
  // 섹터혼합 — 신호 약한 업종 펼치기
  each(document.querySelectorAll('.js-ws-quiet'),function(b){b.addEventListener('click',function(){
    var t=document.getElementById(b.getAttribute('data-t'));
    if(!t) return; var on=t.getAttribute('data-quiet')!=='1';
    t.setAttribute('data-quiet',on?'1':'');
    each(t.querySelectorAll('tr.js-ws-q'),function(r){r.style.display=on?'':'none';});
    b.textContent=on?b.getAttribute('data-off'):b.getAttribute('data-on');
  });});
})();
"""


def _head(title: str) -> str:
    from bot.fred_boards import _BOARD_CSS, _theme_head
    return (f'<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_h.escape(title)}</title>{_BOARD_CSS}{_WS_CSS}{_theme_head()}"
            f'</head><body><div class="wrap">')


# 세 보드 뒤에 시장 흐름 보드를 사용자가 정한 순서로(사용자 2026-10-12: 유동성 → 시장타이밍 →
# Breadth전략 → Bollinger)
_NAV = ('<div class="nav"><a href="market.html">🌍 홈</a> · <a href="index.html">🦉 종목분석</a> · '
        '<a href="sector_mix.html">🌐 국가별 섹터혼합</a> · '
        '<a href="sentiment_map.html">🗺️ 국가별 심리지도</a> · '
        '<a href="sector_stocks.html">📑 국가별 섹터종목</a> · '
        '<a href="liquidity.html">💧 유동성</a> · '
        '<a href="market_timing.html">🚦 시장타이밍</a> · '
        '<a href="breadth_strategy.html">🧭 Breadth전략</a> · '
        '<a href="bollinger.html">🔋 Bollinger</a></div>')


def _foot(name: str) -> str:
    return (f'<div class="footer">{_h.escape(name)} — TradingView(FactSet 분류) 관찰(교육·정보), '
            f'투자 권유 아님 · NOAH</div></div><script>{_WS_JS}</script></body></html>')


# ── 언제 받나(세 보드 공용) ──────────────────────────────────────────────────
def when_line(now: datetime, markets=None) -> str:
    """나라마다 그 세션을 받기 시작하는 KST 시각 — 세 보드의 가이드가 같은 문장을 쓴다(#38).

    시각은 달력의 실제 마감 + `READY_MIN` 에서 파생한다(미국은 서머타임에 따라 08:00/09:00).
    봇은 `TICK_MIN` 분마다 확인하므로 실제 반영은 그만큼 늦을 수 있다 — 그 사실도 같이 적는다(#43)."""
    parts = []
    for m in markets or ws.MARKETS:
        r = ws.ready_kst(m, now)
        if not r:
            parts.append(f"{market_name(m)} 시각 미확인")
            continue
        t, ds = r
        nxt = "이튿날 " if t.date().isoformat() > ds else ""
        parts.append(f"{market_name(m)} {nxt}{t.strftime('%H:%M')}")
    return (f"나라마다 장 마감 {ws.READY_MIN // 60}시간 뒤에 받습니다(한국 시각 · "
            + " · ".join(_h.escape(x) for x in parts)
            + f"). 봇이 {ws.TICK_MIN}분마다 확인하므로 화면 반영은 그보다 보통 {ws.TICK_MIN}분 안쪽으로 늦습니다")


# ── 수집 상태 줄(세 보드 공용) ───────────────────────────────────────────────
def status_lines(state: dict, now: datetime, markets=None) -> list[str]:
    """나라마다 한 줄 — `world_sectors.market_verdict`(감사·`--why` 와 같은 판정, #35)."""
    out = []
    for m in markets or ws.MARKETS:
        kind, line = ws.market_verdict(m, state, now)
        out.append(f"{market_name(m)} {ws.VERDICT_MARK.get(kind, '❓')} {line}")
    return out


# ── 국가별 섹터혼합 ────────────────────────────────────────────────────────────
def _mix_cell(m: str, c: dict | None, win: str, rec: dict) -> str:
    st = (rec.get("markets") or {}).get(m, {})
    if c is None:
        if win == "day" and st.get("day") in DAY_TEXT:
            return f"<td class='ws-off'>{DAY_TEXT[st['day']]}</td>"
        if win == "week" and not st.get("sessions"):
            return "<td class='ws-off'>휴장</td>"
        if win == "week" and not st.get("got"):
            return "<td class='ws-off'>받은 날 없음</td>"
        return (f"<td class='ws-off' title='그 나라에 이 업종 종목이 {ws.MIN_N}개 미만이거나 없음'>—</td>")
    v = c.get(win)
    if v is None:
        txt = DAY_TEXT.get(st.get("day"), "—")
        return f"<td class='ws-off'>{txt if win == 'day' else '—'}</td>"
    if win == "week":
        sub = f"이긴 날 {c.get('wins', 0)}/{c.get('N', 0)}"
        if st.get("got") is not None and st.get("sessions") and st["got"] < st["sessions"]:
            sub += f" · {st['got']}/{st['sessions']}일 받음"
    else:
        sub = f"{c.get('n', 0)}종목"
    tip = f"{c.get('n', 0)}종목 · 시장 대비 {_pp(v)}"
    return (f"<td title='{_h.escape(tip)}'><div class='ws-v {_cls(v)}'>{_pp(v)}</div>"
            f"<div class='ws-sub'>{sub}</div></td>")


def _mix_table(rec: dict, win: str, tid: str) -> str:
    rows = [r for r in rec.get("rows") or [] if r.get(f"v_{win}") not in (None, "na")]
    rows.sort(key=lambda r: tuple(r.get(f"k_{win}") or (9, 0, 0)))
    head = "".join(f"<th>{market_name(m)}</th>" for m in ws.MARKETS)
    body, quiet = [], 0
    for r in rows:
        v = r.get(f"v_{win}")
        lab = r.get(f"l_{win}") or ""
        pill = ("up" if v in ("all_up", "k_up") else "dn" if v in ("all_down", "k_down") else "")
        hidden = v == "none"
        quiet += hidden
        cells = "".join(_mix_cell(m, r["cells"].get(m), win, rec) for m in ws.MARKETS)
        attr = " class='js-ws-q' style='display:none'" if hidden else ""
        body.append(
            f"<tr{attr}>"
            f"<td class='ws-l'><b>{_h.escape(r['label'])}</b></td>"
            f"<td class='ws-l'><span class='ws-pill {pill}'>{_h.escape(lab)}</span></td>{cells}</tr>")
    if not body:
        body.append(f"<tr><td class='ws-l' colspan='{2 + len(ws.MARKETS)}'>"
                    "두 나라 이상에 있는 업종이 아직 없습니다(받은 나라가 하나뿐이거나 기록 없음)</td></tr>")
    btn = ""
    if quiet:
        on = f"신호 약한 업종 {quiet}개 더 보기"
        btn = (f"<button class='ws-btn js-ws-quiet' data-t='{tid}' data-on='{_h.escape(on)}' "
               f"data-off='신호 약한 업종 접기'>{_h.escape(on)}</button>")
    return (f"<div class='ws-scroll'><table class='ws-tbl' id='{tid}'><thead><tr><th>산업</th>"
            f"<th>판정</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>{btn}")


# 섹터혼합 창 — 순서가 곧 탭 순서이고 첫 창이 처음 열린다(사용자 2026-10-12 "하루가 먼저").
MIX_WINDOWS = (("day", "하루"), ("week", "주간(최근 1주)"))


def _mix_day(rec: dict, *, open_: bool, idx: int) -> str:
    d = rec["date"]
    # 머리줄 계수는 처음 펼쳐지는 창(하루)부터 — 보이는 표와 같은 모집단을 먼저 말한다(#45).
    meta = []
    for win, name in MIX_WINDOWS:
        counts = {}
        for r in rec.get("rows") or []:
            counts[r.get(f"v_{win}")] = counts.get(r.get(f"v_{win}"), 0) + 1
        common = counts.get("all_up", 0) + counts.get("all_down", 0)
        multi = counts.get("k_up", 0) + counts.get("k_down", 0)
        meta.append(f"{name.split('(')[0]} 공통 {common} · 2국 이상 {multi} · 엇갈림 {counts.get('mixed', 0)}")
    st = []
    for m in ws.MARKETS:
        s = (rec.get("markets") or {}).get(m, {})
        mark = {"ok": "✅", "closed": "휴장", "holiday": "휴장 추정", "pending": "⏳",
                "missing": "✗"}.get(s.get("day"), "—")
        st.append(f"{market_name(m).split()[0]}{mark}")
    meta.append(" ".join(st))
    if not rec.get("final"):
        meta.append("아직 다 안 받음(받는 대로 바뀜)")
    g = f"mix{idx}"
    # 첫 탭이 처음 열리는 창이다 — 순서와 기본값을 한 표(`MIX_WINDOWS`)에서 같이 정한다.
    tabs = (f"<div class='ws-tabs' data-tabs='{g}'>"
            + "".join(f"<span class='ws-tab{' active' if i == 0 else ''}' data-tab='{win}'>"
                      f"{_h.escape(name)}</span>" for i, (win, name) in enumerate(MIX_WINDOWS))
            + "</div>")
    panes = "".join(f"<div class='ws-pane{' active' if i == 0 else ''}' data-group='{g}' "
                    f"data-tab='{win}'>{_mix_table(rec, win, g + win[0])}</div>"
                    for i, (win, name) in enumerate(MIX_WINDOWS))
    dd = _date.fromisoformat(d)
    return (f"<details class='ws-day'{' open' if open_ else ''}><summary>📅 {d} "
            f"({_WD[dd.weekday()]}) 기준<span class='ws-meta'>{_h.escape(' · '.join(meta))}</span>"
            f"</summary>{tabs}{panes}</details>")


def render_mix(records: list[dict], state: dict, now: datetime, *, gen: str | None = None) -> str:
    """sector_mix.html — 기준일마다 업종 × 5개국(월 → 일 접힘, 최신일만 펼침)."""
    recs = sorted(records, key=lambda r: r["date"], reverse=True)[:RENDER_DAYS]
    by_month: dict[str, list] = {}
    for r in recs:
        by_month.setdefault(r["date"][:7], []).append(r)
    blocks, i = [], 0
    for mi, (ym, rs) in enumerate(by_month.items()):
        y, mo = ym.split("-")
        days = []
        for r in rs:
            days.append(_mix_day(r, open_=(i == 0), idx=i))
            i += 1
        blocks.append(f"<details class='ws-month'{' open' if mi == 0 else ''}><summary>📆 "
                      f"{int(y)}년 {int(mo)}월 <span class='ws-meta'>{len(rs)}일</span></summary>"
                      f"{''.join(days)}</details>")
    body = "".join(blocks) or (
        "<div class='ws-status'>아직 기록이 없습니다 — 나라마다 장 마감 3시간 뒤 자동으로 받습니다.</div>")
    gen_s = now.strftime("%Y-%m-%d %H:%M KST") if gen is None else gen
    st = "<br>".join(_h.escape(x) for x in status_lines(state, now))
    return f"""{_head('국가별 섹터혼합')}
{_NAV}
<h1>🌐 <em>국가별 섹터혼합</em> — 한국·미국·일본·대만·중국 업종 강약</h1>
<div class="sub">TradingView 업종 분류(FactSet) · 업종 등락 − 그 나라 시장 등락(%p) · 두 나라 이상에 있는 업종만 ·
매일 갱신 · 내용이 바뀐 시각 {_h.escape(gen_s)}</div>
<div class="ws-status">{st}</div>
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>값</b> — 그 업종의 등락에서 <b>그 나라 시장 전체의 등락</b>을 뺀 값(%p)입니다. 업종 등락과 시장 등락은
둘 다 종목 등락률을 <b>전일 시가총액으로 가중 평균</b>한 것(업종 지수와 같은 방식)이고, 여러 날이면 날마다
누적한 뒤 뺍니다. <b>이긴 날 n/N</b> 은 그 업종이 시장보다 강했던 날 수 / 그 나라가 그 주에 거래한 날
중 <b>우리가 받은 날</b> 수입니다(휴장이 다르면 N 이 다릅니다 · 못 받은 날이 있으면 'k/n일 받음' 을 같이
적고, 하루도 못 받았으면 '받은 날 없음'). 시가총액 통화가 거래 통화와 다른 종목(해외 주식예탁증서 등)은
종목 수·거래대금엔 넣고 등락 가중에선 뺍니다 — 몇 개인지는 심리지도 주 머리에 적습니다.<br>
<b>판정</b> — 강세·약세 기준은 <b>주간 ±1%p · 하루 ±0.5%p</b>입니다. 업종이 있는 나라가 전부 같은 쪽이면
'N국 공통 강세·약세', 강세(또는 약세)가 두 나라 이상이고 반대쪽이 없으면 'K국 강세·약세', 강세와 약세가
같이 있으면 '엇갈림'입니다. 기준 안쪽인 나라는 세지 않습니다(예: 미국 −0.86%p 는 약세로 안 셉니다).
판정이 없는 업종은 '신호 약한 업종 더 보기'로 펼칩니다.<br>
<b>업종이 '있다'</b> — 그 주에 받은 마지막 날 그 나라에 그 업종 종목이 {ws.MIN_N}개 이상일 때입니다(한
종목짜리 업종은 그 종목의 등락일 뿐이라 뺍니다). 업종은 다섯 나라에 같은 이름으로 있는 TradingView 분류로
맞춥니다 — 나라별 업종표를 손으로 짝짓지 않습니다.<br>
<b>하루·주간</b> — 하루 = 기준일 하루(처음 열리는 탭) · 주간 = 기준일을 끝으로 한 7일(달력) 안의 그 나라
거래일. 원래 블로그 표는 주간이고, 매일 갱신하려고 끝날을 하루씩 민 것입니다. 날짜 줄의 계수도 하루 → 주간
순서입니다.<br>
<b>언제 갱신되나</b> — {when_line(now)}. 그래서 저녁엔 그날 미국이 '대기'입니다. 받는 창은 다음 거래일 장전 거래(미국 프리마켓·동시호가
등)가 시작되기 전까지이고, 원천은 '지금' 값만 주므로 그 창을 놓친 날은 다시 받을 수 없어 '수집 못 함'으로
남습니다. 창 내내 원천 값이 직전 거래일과 같았던 날은 '휴장 추정'(달력에 없는 휴장 — 태풍 휴장 등)입니다.<br>
<b>우리가 정한 것</b> — 블로그가 산식을 다 밝히지 않아 가중 방식·하루 기준(±0.5%p)·업종 최소 종목 수·
정렬(공통 → 2국 이상 → 엇갈림, 같은 묶음에선 신호 낸 나라들의 |%p| 평균 순)은 우리가 정했습니다.
</details>
{body}
{_foot('국가별 섹터혼합')}"""


# ── 국가별 심리지도 ───────────────────────────────────────────────────────────
def _tip(label: str, cell: dict, when: str, upshare=None) -> str:
    s = cell.get("s")
    name = ws.SENTIMENT.get(s, ("—",))[0] if s else "—"
    parts = [f"{label} {when}: {name} — {SENT_HELP.get(s, '')}" if s else f"{label} {when}: 판정 불가"]
    if cell.get("rel") is not None:
        parts.append(f"시장 대비 {cell['rel']:+.2f}%p")
    if cell.get("rv") is not None:
        parts.append(f"상대 거래대금 {cell['rv']:.2f}배({BASIS_LABEL.get(cell.get('basis'), '')})")
    else:
        parts.append("상대 거래대금 기준 없음")
    if upshare is not None:
        parts.append(f"상승일 거래 {upshare:.0f}%")
    return ", ".join(parts)


def _map_cell(m: str, w: int, kind: str, row: dict, cell: dict | None, *, day: int | None,
              head: str, title: str, summ: str, src: str, label_when: str, upshare=None,
              mon: str = "") -> str:
    if cell is None or cell.get("s") is None:
        return "<td class='ws-off'>—</td>"
    name, tok = ws.SENTIMENT[cell["s"]]
    approx = " ws-approx" if cell.get("basis") in ("approx", "mixed") else ""
    wkc = " ws-wk" if day is None else ""
    tip = _tip(row["label"], cell, label_when, upshare)
    attrs = (f" data-m='{m}' data-mon='{mon}' data-kind='{kind}'"
             f" data-key='{_h.escape(row['key'])}'"
             f" data-day='{'' if day is None else day}' data-src='{_h.escape(src)}'"
             f" data-head='{_h.escape(head)}' data-title='{_h.escape(title)}'"
             f" data-sum='{_h.escape(summ)}'")
    return (f"<td class='ws-cell js-ws-cell ws-c-{tok}{approx}{wkc}' title='{_h.escape(tip)}'{attrs}>"
            f"<div class='ws-s'>{name}</div><div class='ws-p'>{_pp(cell.get('rel'), 1)}</div></td>")


def _map_table(m: str, w: int, wk: dict, kind: str, sums: dict, src: str) -> str:
    rows = (wk.get("groups") or {}).get(kind) or []
    cols = wk["cols"]
    head = "".join(f"<th>{_md(c['date'])}</th>" for c in cols) + "<th class='ws-wk'>주간</th>"
    mon = _date.fromisoformat(wk["monday"])
    kind_name = "섹터" if kind == "sec" else "업종"
    hdr = f"{market_name(m)} · {kind_name} · {mon.month}/{mon.day}(월) 주"
    body = []
    for r in rows:
        s = sums.get((kind, r["key"])) or {}
        wkcell = r["week"]
        summ = (f"주간 {_pct(wkcell.get('ret'))} · 시장 대비 {_pp(wkcell.get('rel'))} · "
                f"이긴 날 {wkcell.get('wins', 0)}/{wkcell.get('N', 0)} · 거래 몫 "
                f"{'—' if s.get('share') is None else format(s['share'], '.2f') + '%'}")
        tds = []
        for i, c in enumerate(cols):
            if c["status"] != "ok":
                txt = "·" if c["status"] == "pending" else DAY_TEXT.get(c["status"], "·")
                tds.append(f"<td class='ws-off'>{txt}</td>")
                continue
            tds.append(_map_cell(m, w, kind, r, r["cells"].get(c["date"]), day=i, head=hdr,
                                 title=r["label"], summ=summ, src=src,
                                 label_when=_md(c["date"]), mon=wk["monday"]))
        tds.append(_map_cell(m, w, kind, r, wkcell, day=None, head=hdr, title=r["label"],
                             summ=summ, src=src, label_when="주간", upshare=s.get("upshare"),
                             mon=wk["monday"]))
        body.append(f"<tr><td class='ws-l'>{_h.escape(r['label'])}<span class='ws-n'>{r['n']}</span>"
                    f"</td>{''.join(tds)}</tr>")
    if not body:
        body.append(f"<tr><td class='ws-l' colspan='{len(cols) + 2}'>이 주에 받은 날이 없습니다</td></tr>")
    return (f"<div class='ws-scroll'><table class='ws-tbl ws-map'><thead><tr><th>{kind_name}</th>{head}"
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def _pct(v) -> str:
    if v is None:
        return "—"
    return "0.00%" if round(v, 2) == 0 else f"{v:+.2f}%"


def _size_table(sz: dict | None) -> str:
    """규모와 쏠림 — 시총 순위 구간별 등락(시총 가중) · 그날 거래대금 상위 10종목 몫."""
    if not sz or not sz.get("rows"):
        return ""
    head = "".join(f"<th>{_md(d)}</th>" for d in sz["days"])
    body = []
    for r in sz["rows"]:
        tds = "".join(
            "<td class='ws-off'>·</td>" if v is None else
            f"<td class='{_cls(v)}'>{v:+.2f}</td>" for v in r["days"])
        wk = r.get("week")
        body.append(f"<tr><td class='ws-l'>{_h.escape(r['label'])}<span class='ws-n'>{r['n']}</span></td>"
                    f"<td class='{_cls(wk)}'><b>{_pct(wk)}</b></td>{tds}</tr>")
    conc = "".join("<td class='ws-off'>·</td>" if v is None else f"<td>{v:.1f}%</td>"
                   for v in sz["conc"])
    body.append(f"<tr><td class='ws-l'>쏠림(상위 10종목 거래대금 몫)</td><td>—</td>{conc}</tr>")
    return ("<h3>규모와 쏠림</h3><div class='ws-sub'>규모는 그 주 첫 거래일 시가총액 순위 기준(블로그는 연초 "
            "순위 — 우리는 연초 값을 갖고 있지 않다) · 등락은 구간 안 시총 가중 · 쏠림은 그날 거래대금 중 "
            f"상위 10종목 몫</div><div class='ws-scroll'><table class='ws-tbl'><thead><tr><th>규모</th>"
            f"<th>주간</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def _legend() -> str:
    keys = ("hot_up", "quiet_up", "calm", "churn", "fade", "hot_down")
    return "<div class='ws-legend'>" + "".join(
        f"<span class='ws-c-{ws.SENTIMENT[k][1]}' title='{_h.escape(SENT_HELP[k])}'>"
        f"{ws.SENTIMENT[k][0]}</span>" for k in keys) + "</div>"


def render_map(payloads: dict[str, dict], state: dict, now: datetime, *,
               gen: str | None = None) -> str:
    """sentiment_map.html — 나라 탭 · 이번 주(+지난주) 섹터/업종 × 요일."""
    tabs, panes = [], []
    for j, m in enumerate(ws.MARKETS):
        p = payloads.get(m) or {}
        act = " active" if j == 0 else ""
        tabs.append(f"<span class='ws-tab{act}' data-tab='{m}'>{market_name(m)}</span>")
        src = f"ws_{m}.json"
        weeks_html = []
        for w, wk in enumerate(p.get("weeks") or []):
            mon = _date.fromisoformat(wk["monday"])
            g = f"k{m}{w}"
            inner = (f"<div class='ws-tabs' data-tabs='{g}'><span class='ws-tab active' data-tab='sec'>"
                     f"섹터({len((wk.get('groups') or {}).get('sec') or [])})</span>"
                     f"<span class='ws-tab' data-tab='ind'>업종 세부"
                     f"({len((wk.get('groups') or {}).get('ind') or [])})</span></div>"
                     f"<div class='ws-pane active' data-group='{g}' data-tab='sec'>"
                     f"{_map_table(m, w, wk, 'sec', wk.get('sums') or {}, src)}</div>"
                     f"<div class='ws-pane' data-group='{g}' data-tab='ind'>"
                     f"{_map_table(m, w, wk, 'ind', wk.get('sums') or {}, src)}</div>"
                     f"{_size_table(wk.get('size'))}")
            title = f"{mon.month}/{mon.day}(월) 주" + week_tag(mon, now)
            weeks_html.append(f"<details class='ws-day'{' open' if w == 0 else ''}><summary>🗓️ "
                              f"{title}<span class='ws-meta'>{_h.escape(wk.get('meta') or '')}</span>"
                              f"</summary>{inner}</details>")
        if not weeks_html:
            weeks_html.append("<div class='ws-status'>아직 받은 날이 없습니다 — 장 마감 "
                              f"{ws.READY_MIN // 60}시간 뒤 자동으로 받습니다.</div>")
        detail = (f"<div class='ws-detail' id='ws-detail-{m}' style='display:none' "
                  f"data-gone='이 주의 종목 자료가 그 뒤 갱신됐습니다 — 페이지를 새로고침해 주세요'>"
                  f"<div class='ws-detail-h'><div><div class='ws-detail-k js-ws-k'></div>"
                  f"<div class='ws-detail-t js-ws-t'></div><div class='ws-sub js-ws-s'></div></div>"
                  f"<button class='ws-btn js-ws-close'>닫기</button></div>"
                  f"<div style='margin:8px 0'><input class='ws-find js-ws-find' placeholder='종목·산업 찾기' "
                  f"autocomplete='off'> <span class='ws-sub js-ws-count'></span></div>"
                  f"<div class='js-ws-body'></div></div>")
        panes.append(f"<div class='ws-pane{act}' data-group='mkt' data-tab='{m}'>"
                     f"{''.join(weeks_html)}{detail}</div>")
    gen_s = now.strftime("%Y-%m-%d %H:%M KST") if gen is None else gen
    st = "<br>".join(_h.escape(x) for x in status_lines(state, now))
    thr_d, vthr_d = ws.MAP_THR["day"]
    thr_w, vthr_w = ws.MAP_THR["week"]
    return f"""{_head('국가별 심리지도')}
{_NAV}
<h1>🗺️ <em>국가별 심리지도</em> — 업종 × 요일, 거래대금과 등락으로 본 그날의 심리</h1>
<div class="sub">한국·미국·일본·대만·중국 각각 · TradingView 섹터·업종(FactSet) · 매일 갱신 ·
내용이 바뀐 시각 {_h.escape(gen_s)}</div>
<div class="ws-status">{st}</div>
{_legend()}
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>칸</b> — 그날 그 업종의 <b>시장 대비 등락(%p)</b>과 <b>상대 거래대금</b>으로 여섯 갈래로 나눴습니다.
상대 거래대금 = (업종 거래대금 ÷ 업종의 직전 20거래일 평균) ÷ (같은 비율의 시장 값). 하루 칸은 시장 대비
±{thr_d:g}%p · 상대 거래대금 {vthr_d:g}배, 주간 칸은 ±{thr_w:g}%p · {vthr_w:g}배를 기준으로 갈랐습니다(하루
등락이 주간보다 작아서). 칸 아래 숫자는 시장 대비 %p, 칸에 마우스를 올리면 설명과 수치가 뜨고 <b>칸을 누르면
그 업종 종목 표</b>가 열립니다(그날 열은 테두리로 표시). '·' 는 아직 받을 때가 안 됐거나 받는 중인
날입니다.<br>
<b>여섯 갈래</b> — 거래 몰린 상승(오르며 거래 많음) · 조용한 상승(오르는데 거래 평범) · 소강(둘 다 평범) ·
손바뀜(등락은 평범한데 거래 많음) · 관심 이탈(거래 안 늘고 약함) · 거래 몰린 하락(약하며 거래 많음).<br>
<b>점선 칸</b> — 직전 20거래일 중 우리 자체 이력이 {ws.BASE_MIN}일이 안 돼 상대 거래대금을 TradingView 의
<b>30일 평균 거래량 × 그날 종가</b>로 근사한 칸입니다. 이력이 쌓이면 저절로 실측으로 바뀝니다.<br>
<b>등락 계산</b> — 업종·시장 모두 종목 등락률의 전일 시가총액 가중 평균(우리가 정함). 행 옆 숫자는 그
업종 종목 수, 정렬은 주간 시장 대비 %p 순입니다. 휴장은 '휴장', 그날을 받을 창을 놓쳤으면 '수집 못 함'
(원천이 '지금' 값만 주므로 지난날은 다시 받을 수 없습니다), 창 내내 원천 값이 직전 거래일과 같았으면
'휴장 추정'입니다.<br>
<b>언제 갱신되나</b> — {when_line(now)}.<br>
<b>종목 표</b> — 그 주 요일별 등락 · 주간(누적) · <b>상승일 거래</b>(그 주 거래대금 중 오른 날의 몫) ·
<b>거래 몫</b>(그 종목 주간 거래대금 ÷ 시장 전체) · 시총 · 시총 증감. 머리글을 누르면 정렬합니다.
</details>
<div class="ws-tabs" data-tabs="mkt">{''.join(tabs)}</div>
{''.join(panes)}
{_foot('국가별 심리지도')}"""


# ── 국가별 섹터종목 ───────────────────────────────────────────────────────────
def render_stocks(payloads: dict[str, dict], state: dict, now: datetime, *,
                  gen: str | None = None) -> str:
    """sector_stocks.html — 한·미 Breadth 섹터 ETF 마다 보유 상위 15종목의 이번 주·지난주 표."""
    tabs, panes, data_tags = [], [], []
    for j, m in enumerate(STOCK_MARKETS):
        p = payloads.get(m) or {}
        act = " active" if j == 0 else ""
        tabs.append(f"<span class='ws-tab{act}' data-tab='{m}'>{market_name(m)}</span>")
        weeks = p.get("weeks") or []
        g = f"sw{m}"
        wtabs = "".join(f"<span class='ws-tab{' active' if i == 0 else ''}' data-tab='{i}'>"
                        f"{_h.escape(w)}</span>" for i, w in enumerate(weeks))
        blocks = []
        for k, e in enumerate(p.get("etfs") or []):
            inner = []
            for i, tbl in enumerate(e.get("tables") or []):
                did = f"ws-etf-{m}-{k}-{i}"
                data_tags.append(
                    f"<script type='application/json' id='{did}'>"
                    f"{json.dumps(tbl, ensure_ascii=False).replace('</', '<' + chr(92) + '/')}</script>")
                inner.append(
                    f"<div class='ws-pane{' active' if i == 0 else ''}' data-group='{g}' data-tab='{i}'>"
                    f"<div class='js-ws-etf' data-src='{did}' data-empty='{_h.escape(e['empty'])}'>"
                    f"<div style='margin:8px 0'><input class='ws-find js-ws-find' placeholder='종목·산업 "
                    f"찾기' autocomplete='off'> <span class='ws-sub js-ws-count'></span></div>"
                    f"<div class='js-ws-body'></div></div></div>")
            if not inner:
                inner.append(f"<div class='ws-off'>{_h.escape(e['nodays'])}</div>")
            blocks.append(
                f"<details class='ws-day'{' open' if k == 0 else ''}><summary>{_h.escape(e['ticker'])} "
                f"{_h.escape(e['label'])}<span class='ws-meta'>{_h.escape(e['meta'])}</span></summary>"
                f"{''.join(inner)}</details>")
        if not blocks:
            blocks.append("<div class='ws-status'>섹터 ETF 목록이 없습니다.</div>")
        bar = f"<div class='ws-tabs' data-tabs='{g}'>{wtabs}</div>" if len(weeks) > 1 else ""
        panes.append(f"<div class='ws-pane{act}' data-group='mkt' data-tab='{m}'>"
                     f"<div class='ws-sub'>{_h.escape(p.get('head') or '')}</div>{bar}{''.join(blocks)}</div>")
    gen_s = now.strftime("%Y-%m-%d %H:%M KST") if gen is None else gen
    st = "<br>".join(_h.escape(x) for x in status_lines(state, now, STOCK_MARKETS))
    return f"""{_head('국가별 섹터종목')}
{_NAV}
<h1>📑 <em>국가별 섹터종목</em> — 섹터 ETF 보유 상위 15종목의 한 주</h1>
<div class="sub">Breadth 전략의 섹터 ETF(🇰🇷 KODEX 13 · 🇺🇸 SPDR 11)마다 보유 비중 상위 15종목 · 매일 갱신 ·
내용이 바뀐 시각 {_h.escape(gen_s)}</div>
<div class="ws-status">{st}</div>
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>종목</b> — 운용사가 공개한 ETF 보유 종목(🇺🇸 State Street 일일 보유 엑셀 · 🇰🇷 KRX ETF 구성종목) 중 비중
상위 15개입니다. 보유 목록은 하루 한 번 받고, 받지 못한 날은 마지막으로 받은 목록을 그 기준일과 함께 씁니다.
지난주 탭도 <b>오늘의 보유 목록</b>으로 그립니다(지난주 보유 목록은 따로 받지 않습니다).<br>
<b>열</b> — 요일별 등락(%) · 주간(그 주 누적 %) · <b>상승일 거래</b>(그 종목의 그 주 거래대금 중 오른 날의
몫) · <b>거래 몫</b>(그 종목 주간 거래대금 ÷ 그 나라 시장 전체 주간 거래대금) · 시총 · 시총 증감(그 주
등락으로 늘거나 준 시가총액). '산업'은 TradingView 업종 이름입니다. 머리글을 누르면 정렬합니다.<br>
<b>값의 출처</b> — 등락·거래대금·시총은 「국가별 심리지도」와 같은 하루치(TradingView)를 씁니다.
받기 시작한 날 이전 요일과 놓친 날은 비어 있습니다.<br>
<b>언제 갱신되나</b> — {when_line(now, STOCK_MARKETS)}. 보유 목록(ETF 구성종목)은 하루 한 번 따로 받습니다.
</details>
<div class="ws-tabs" data-tabs="mkt">{''.join(tabs)}</div>
{''.join(panes)}
{''.join(data_tags)}
{_foot('국가별 섹터종목')}"""


# ── 화면 재료 조립(디스크만 읽는다) ────────────────────────────────────────────
def _week_stocks(memo: dict | None, m: str, monday: _date) -> dict:
    """한 재생성 안에서 같은 주를 두 번 읽지 않는다 — 심리지도와 섹터종목이 한·미 두 주를 같이
    쓰는데, 하루치 원본 캐시(`_CACHE_MAX['snap']`)는 그 사이 다른 날로 밀려 다시 푼다(L9)."""
    if memo is None or m not in STOCK_MARKETS:
        # 섹터종목이 다시 읽지 않는 나라는 붙들지 않는다 — 미국 한 주가 메모리에서 수십 MB 라
        # 다섯 나라를 다 들고 있으면 재생성 정점이 그만큼 오른다
        return ws.week_stocks(m, monday)
    key = (m, monday.isoformat())
    if key not in memo:
        memo[key] = ws.week_stocks(m, monday)
    return memo[key]


def _hold_names(m: str) -> dict[str, str]:
    """그 나라 섹터 ETF 보유 목록의 {종목코드: 이름} — 심리지도 종목 표도 섹터종목과 같은 이름을
    쓰게(국내 ETF 구성종목은 KRX 한글명을 준다, #38). 보유 목록이 없는 나라는 빈 dict."""
    try:
        from bot import etf_holdings as eh
        out = {}
        for t in eh.etfs(m):
            for r in (eh.load(t) or {}).get("rows") or []:
                if r.get("code") and r.get("name"):
                    out.setdefault(str(r["code"]), str(r["name"]))
        return out
    except Exception:                                          # noqa: BLE001
        return {}


def _int(v):
    return None if v is None else round(v)


def _week_payload(m: str, monday: _date, now: datetime, memo: dict | None = None,
                  hold_names: dict | None = None) -> tuple[dict, dict]:
    """(화면용 주간 지도, 종목 JSON 한 주). 종목 표 숫자는 `stock_week_row` 가 만든다."""
    wk = ws.map_week(m, monday, now)
    st = _week_stocks(memo, m, monday)
    hn = hold_names or {}
    labels = st["labels"]
    secs = sorted({it["sector"] for it in st["stocks"].values() if it["sector"]})
    inds = sorted({it["industry"] for it in st["stocks"].values() if it["industry"]})
    si = {k: i for i, k in enumerate(secs)}
    ii = {k: i for i, k in enumerate(inds)}
    rows = []
    sums: dict[tuple, dict] = {}
    mtot = sum(v for v in st["mval"] if v)
    for code, it in st["stocks"].items():
        if all(v is None for v in it["chg"]):
            continue
        r = ws.stock_week_row(it, st["mval"])
        rows.append([code, display_name(it["name"], hn.get(code)), si.get(it["sector"]),
                     ii.get(it["industry"]),
                     _int(it.get("cap")), [None if v is None else round(v, 2) for v in it["chg"]],
                     None if r["week"] is None else round(r["week"], 2),
                     None if r["upshare"] is None else round(r["upshare"], 1),
                     None if r["share"] is None else round(r["share"], 3),
                     _int(r["dcap"]), lookup_href(it["sym"])])
        for kind, key in (("sec", it["sector"]), ("ind", it["industry"])):
            if not key:
                continue
            s = sums.setdefault((kind, key), {"val": 0.0, "up": 0.0})
            for chg, val in zip(it["chg"], it["val"]):
                if chg is None or not val:
                    continue
                s["val"] += val
                if chg > 0:
                    s["up"] += val
    for s in sums.values():
        s["share"] = s["val"] / mtot * 100 if mtot > 0 else None
        s["upshare"] = s["up"] / s["val"] * 100 if s["val"] > 0 else None
    rows.sort(key=lambda r: -(r[4] or 0))
    days = st["days"]
    status = [c["status"] for c in wk["cols"]]
    js = {"monday": monday.isoformat(), "days": days, "dayLabels": [_md(d) for d in days],
          "status": status, "secs": secs, "inds": inds,
          "indLabels": [labels["ind"].get(k) or k for k in inds], "rows": rows}
    wk["sums"] = sums
    wk["size"] = size_rows(m, monday, st)
    ok_days = [d for d, s in zip(days, status) if s == "ok"]
    bases = wk.get("basis") or []
    last = ws.load_agg(m, ok_days[-1]) if ok_days else None
    fx = int((last or {}).get("fx_skip") or 0)
    wk["meta"] = (f"받은 날 {len(ok_days)}일 · 종목 {len(rows):,}" +
                  (" · 상대 거래대금 근사 포함" if "approx" in bases else "") +
                  (f" · 시총 통화가 달라 등락 가중에서 뺀 종목 {fx}" if fx else ""))
    return wk, js


def size_rows(m: str, monday: _date, st: dict) -> dict | None:
    """규모와 쏠림(순수에 가까움 — 이미 읽은 주간 종목만 쓴다)."""
    first = next((i for i, ok in enumerate(st["ok"]) if ok), None)
    if first is None:
        return None
    def cap_at(it, i):
        caps = it.get("caps")
        return caps[i] if caps else it.get("cap")
    # 순위 = 그 주 **첫 거래일** 시총(화면 설명 그대로 — 마지막 날 값이면 그 주 등락이 순위에
    # 섞인다) · 그날 가중 = **그날** 시총의 전일 환산
    items = [it for it in st["stocks"].values()
             if it["chg"][first] is not None and cap_at(it, first)]
    items.sort(key=lambda it: -(cap_at(it, first) or 0))
    bands = (("초대형 30", 0, 30), ("31–100위", 30, 100), ("101–500위", 100, 500),
             ("501위 이하", 500, None))
    rows = []
    for label, lo, hi in bands:
        grp = items[lo:hi]
        if not grp:
            continue
        days, cum, used = [], 1.0, 0
        for i in range(5):
            if not st["ok"][i]:
                days.append(None)
                continue
            w = wr = 0.0
            for it in grp:
                chg = it["chg"][i]
                cap = cap_at(it, i)
                if chg is None or not cap or it.get("fx") or chg <= -100:
                    continue
                prev = cap / (1 + chg / 100)
                w += prev
                wr += prev * chg
            r = wr / w if w else None
            days.append(None if r is None else round(r, 2))
            if r is not None:
                cum *= 1 + r / 100
                used += 1
        rows.append({"label": label, "n": len(grp), "days": days,
                     "week": (cum - 1) * 100 if used else None})
    conc = []
    for i in range(5):
        vals = sorted((it["val"][i] for it in st["stocks"].values() if it["val"][i]), reverse=True)
        tot = sum(vals)
        conc.append(sum(vals[:10]) / tot * 100 if tot > 0 and st["ok"][i] else None)
    return {"days": st["days"], "rows": rows, "conc": conc}


def map_payloads(now: datetime, memo: dict | None = None) -> tuple[dict, dict]:
    """나라마다 이번 주·지난주 — (화면 재료, 나라별 종목 JSON)."""
    pages, jsons = {}, {}
    for m in ws.MARKETS:
        mon = ws.latest_monday(m)
        if mon is None:
            pages[m], jsons[m] = {"weeks": []}, None
            continue
        weeks, js_weeks = [], []
        hn = _hold_names(m)
        for mm in (mon, mon - timedelta(days=7)):
            wk, js = _week_payload(m, mm, now, memo, hn)
            if not any(c["status"] == "ok" for c in wk["cols"]):
                continue
            weeks.append(wk)
            js_weeks.append(js)
        pages[m] = {"weeks": weeks}
        jsons[m] = {"market": m, "cur": CURRENCY.get(m, ""), "weeks": js_weeks}
    return pages, jsons


_HANGUL = re.compile("[가-힣]")


def display_name(tv_name: str | None, hold_name: str | None) -> str:
    """종목 이름 — 한글 이름이 있으면 그것, 없으면 원천(TradingView) 이름, 그것도 없으면 보유 목록의
    이름. 둘 다 한글이면 **보유 목록(KRX 약칭)이 먼저** — TradingView 국내 이름은 법인 정식명
    (`삼성전자보통주`·`에스케이하이닉스보통주`, 2026-10-12 VM `--probe` 실측)이라 KRX 약칭(`삼성전자`)이
    화면에 맞다(REFERENCE 가 적은 우선순위 그대로). 나라를 묻지 않는다."""
    for nm in (hold_name, tv_name):
        if nm and _HANGUL.search(str(nm)):
            return _plain(str(nm))
    return str(tv_name or hold_name or "")


def _plain(nm: str) -> str:
    """'삼성전자보통주' → '삼성전자' — 보유 목록에 없는 종목은 TradingView 법인 정식명만 있어
    꼬리 '보통주' 를 떼야 화면 이름이 된다(그 밖의 글자는 원천 그대로)."""
    return nm[:-3] if nm.endswith("보통주") and len(nm) > 3 else nm


def _holding_rows(h: dict, st: dict | None) -> list[dict]:
    """보유 상위 종목 → 종목 표 행(그 주 하루치에서 찾는다 — 못 찾으면 이름·비중만)."""
    rows = []
    for hold in h.get("rows") or []:
        code = str(hold.get("code") or "")
        it = (st or {}).get("stocks", {}).get(code) if st else None
        if it:
            r = ws.stock_week_row(it, st["mval"])
            ind = st["labels"]["ind"].get(it["industry"]) or it["industry"] or ""
            rows.append({"c": code, "nm": display_name(it["name"], hold.get("name")), "ind": ind,
                         "href": lookup_href(it["sym"]), "cap": it.get("cap"),
                         "chg": it["chg"], "wk": r["week"], "up": r["upshare"],
                         "sh": r["share"], "dcap": r["dcap"], "w": hold.get("weight")})
        else:
            rows.append({"c": code, "nm": hold.get("name") or code, "ind": "", "href": "",
                         "cap": None, "chg": [None] * 5, "wk": None, "up": None,
                         "sh": None, "dcap": None, "w": hold.get("weight")})
    return rows


def stocks_payloads(now: datetime, memo: dict | None = None) -> dict:
    """섹터종목 — 한·미 ETF 마다 보유 상위 15종목의 이번 주·지난주 표 재료.

    보유 목록은 **오늘 것 하나**를 두 주에 같이 쓴다(지난주 보유 목록을 따로 받지 않는다 —
    화면이 그렇게 밝힌다)."""
    from bot import etf_holdings as eh
    out = {}
    state = ws.load_state()
    for m in STOCK_MARKETS:
        mon = ws.latest_monday(m)
        weeks = []
        if mon:
            for mm in (mon, mon - timedelta(days=7)):
                st = _week_stocks(memo, m, mm)
                if any(st["ok"]):
                    weeks.append((mm, st))
        etfs = []
        for t, label in eh.etfs(m).items():
            h = eh.load(t) or {}
            meta = []
            if h.get("rows"):
                tw = sum(x.get("weight") or 0 for x in h["rows"])
                meta.append(f"상위 {len(h['rows'])}종목 비중 합 {tw:.1f}%")
                meta.append(f"보유 기준 {h.get('asof') or h.get('fetched_day') or '—'}")
            if h.get("error") and h.get("tried_day") != h.get("fetched_day"):
                meta.append(f"보유 목록 갱신 실패({h['error']}) — {eh.retry_note(h, now)}")
            if not h:
                meta.append("보유 목록을 아직 받지 않음")
            # 옛 판 코드의 사유를 '지금 실패' 처럼 표 본문에 적지 않는다 — 머리줄과 같은 한 마디를 붙인다(#38).
            pnote = eh.prior_note(h, now)
            empty = ((h.get("error") or "보유 목록을 아직 받지 않았습니다") if not h.get("rows")
                     else "해당 종목 없음")
            if pnote and not h.get("rows"):
                empty += f" ({pnote})"
            nodays = (f"{empty} · 아직 받은 하루치가 없습니다" if not h.get("rows")
                      else "보유 목록은 있지만 아직 받은 하루치가 없습니다")
            tables = []
            for mm, st in weeks:
                days = st["days"]
                tables.append({"days": days, "dayLabels": [_md(d) for d in days],
                               "status": [ws.market_day_status(m, d, now, state) for d in days],
                               "cur": CURRENCY.get(m, ""), "rows": _holding_rows(h, st)})
            etfs.append({"ticker": t.split(".")[0], "label": label, "meta": " · ".join(meta),
                         "empty": empty, "nodays": nodays, "tables": tables})
        labels = [f"{mm.month}/{mm.day}(월) 주" + week_tag(mm, now) for mm, _st in weeks]
        out[m] = {"etfs": etfs, "weeks": labels, "head": eh.etf_label(m)}
    return out


# ── 쓰기(내용이 바뀔 때만) ──────────────────────────────────────────────────────
def _write_if_changed(name: str, body: str, sig_body: str | None = None) -> bool:
    import hashlib

    from bot.dashboard import ARCHIVE_ROOT
    sig = hashlib.sha1((sig_body if sig_body is not None else body).encode("utf-8")).hexdigest()
    out = ARCHIVE_ROOT / name
    sig_path = ARCHIVE_ROOT / f"{name}.sig"
    try:
        same = out.exists() and sig_path.read_text(encoding="utf-8").strip() == sig
    except Exception:                                          # noqa: BLE001
        same = False
    if same:
        return False
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVE_ROOT / f"{name}.tmp{os.getpid()}"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, out)
    sig_path.write_text(sig, encoding="utf-8")
    return True


def regenerate(now: datetime | None = None) -> list[str]:
    """세 페이지 + 나라별 종목 JSON 재생성(디스크만 읽는다). 바뀌어 쓴 파일 이름 목록.

    한 페이지가 실패해도 나머지는 쓴다(곁들이 하나가 본체를 지우지 않게, #315)."""
    from bot.dashboard import _inject_update_banner
    now = ws._now(now)
    st = ws.load_state()
    wrote: list[str] = []
    jobs = []
    memo: dict = {}
    try:
        pages, jsons = map_payloads(now, memo)
        jobs.append(("sentiment_map.html", lambda g: render_map(pages, st, now, gen=g)))
        for m, js in jsons.items():
            if js is not None:
                try:
                    if _write_if_changed(f"ws_{m}.json", json.dumps(js, ensure_ascii=False)):
                        wrote.append(f"ws_{m}.json")
                except Exception as exc:                       # noqa: BLE001
                    log.warning("world_sectors: ws_%s.json 쓰기 실패: %s", m, exc)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("world_sectors: 심리지도 재료 실패: %s", exc)
    try:
        recs = ws.mix_records(now, limit=RENDER_DAYS)
        jobs.append(("sector_mix.html", lambda g: render_mix(recs, st, now, gen=g)))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("world_sectors: 섹터혼합 재료 실패: %s", exc)
    try:
        sp = stocks_payloads(now, memo)
        jobs.append(("sector_stocks.html", lambda g: render_stocks(sp, st, now, gen=g)))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("world_sectors: 섹터종목 재료 실패: %s", exc)
    for name, fn in jobs:
        try:
            body = _inject_update_banner(fn(_GEN))
            html = body.replace(_GEN, now.strftime("%Y-%m-%d %H:%M KST"))
            if _write_if_changed(name, html, sig_body=body):
                wrote.append(name)
        except Exception as exc:                               # noqa: BLE001
            log.warning("world_sectors: %s 렌더 실패: %s", name, exc)
    return wrote
