"""품목 레퍼런스북 — MTI 품목 ↔ HS10 코드 ↔ 산업 ↔ 관련 상장사 통합 참조표
(사용자 2026-06-18 '관련기업까지 총 매치해서 레퍼런스북으로 보게').

데이터는 전부 기보유 — 신규 fetch·LLM·비용 0:
  • 품목(MTI)·HS10·산업 = trade.mti_map (무역협회 HSK-MTI 연계표, 1,295 품목)
  • 관련 상장사 = trade.mti_companies (수동 큐레이션 + 채널 알림 조인)

검색 가능한 단일 자체완결 HTML 페이지. dashboard_server 가 ~/.trade/ 를 정적 서빙하므로
/trade/reference.html 로 노출. dashboard.main 이 매 렌더 regenerate (데이터 정적이라
저렴) + ensure_exists 로 404 방지. 순수 조립 — build_rows/render_page 단위테스트.
"""
from __future__ import annotations

import html as _html
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
PAGE = _DATA_DIR / "dashboard" / "reference.html"


def build_rows() -> list[dict]:
    """전 품목 [{mti6, name, industry, hs:[HS10...], companies:[상장사...]}]
    (산업·품목명 정렬). 연계표 부재 시 [] (graceful). 순수에 가까움(파일·store.db 읽기)."""
    from trade import mti_companies, mti_map
    try:
        names = mti_map.mti_names()            # {mti6: (품목명, 산업)}
        hsk = mti_map.load_mti()               # {HSK10: (mti6, 산업, 품목명)}
    except Exception:
        return []
    hs_by_mti: dict[str, list[str]] = {}
    for hs, rec in hsk.items():
        m6 = rec[0] if rec else ""
        if m6:
            hs_by_mti.setdefault(m6, []).append(str(hs))
    try:
        pairs = mti_companies.load_channel_pairs()
    except Exception:
        pairs = []
    rows: list[dict] = []
    for mti6, meta in sorted(names.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        name, industry = meta
        try:
            cos = list(dict.fromkeys(list(mti_companies.companies_for(name))
                                     + mti_companies.channel_companies_for(name, pairs)))
        except Exception:
            cos = []
        rows.append({"mti6": mti6, "name": name, "industry": industry,
                     "hs": sorted(hs_by_mti.get(mti6, [])), "companies": cos})
    return rows


# 테마 = 대시보드와 동일 시간기반(KST 19-07 = body.dark). 라이트 기본 + body.dark 오버라이드
# (사용자 2026-06-18 'light/black 시간에 맞게 안 변해' — 기존 prefers-color-scheme OS기반 폐기).
_CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;
 background:#fff;color:#1f2328;margin:0;padding:0;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
.nav{font-size:13px;margin-bottom:10px}.nav a{color:#0969da;text-decoration:none;margin-right:10px}
h1{font-size:20px;margin:6px 0 2px}.sub{font-size:12px;color:#656d76;margin:0 0 12px}
.bar{position:sticky;top:0;background:#fff;padding:8px 0;z-index:5;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#q{flex:1;min-width:220px;padding:9px 12px;border:1px solid #d0d7de;background:#f6f8fa;color:#1f2328;border-radius:8px;font-size:14px}
.chip{padding:5px 10px;border:1px solid #d0d7de;background:#f6f8fa;color:#1f2328;border-radius:14px;font-size:12px;cursor:pointer}
.chip.on{background:#2f81f7;border-color:#2f81f7;color:#fff}
.cnt{font-size:12px;color:#656d76;margin:6px 2px}
table{border-collapse:collapse;width:100%;font-size:13px}
thead th{position:sticky;top:52px;background:#f6f8fa;color:#656d76;text-align:left;padding:8px;border-bottom:1px solid #d0d7de;z-index:4}
td{padding:7px 8px;border-bottom:1px solid #eaeef2;vertical-align:top}
tr:hover td{background:#f6f8fa}
.nm{font-weight:600}.mti{color:#656d76;font-variant-numeric:tabular-nums;font-size:12px}
.ind{color:#656d76;white-space:nowrap}
.hs{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#656d76;word-break:break-all}
.co{color:#1f2328}.co .x{display:inline-block;background:#eef1f4;border:1px solid #cdd4dc;border-radius:10px;padding:1px 8px;margin:1px;font-size:12px;color:#1f2328}
.co .none{color:#8b949e}
.dl{padding:8px 12px;border:1px solid #1f883d;background:#238636;color:#fff;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
body.dark{background:#0d1117;color:#e6edf3}
body.dark .nav a{color:#58a6ff}
body.dark .bar{background:#0d1117}
body.dark #q,body.dark .chip{background:#161b22;border-color:#2a2e37;color:#e6edf3}
body.dark .sub,body.dark .cnt,body.dark .mti,body.dark .ind,body.dark .hs,body.dark .co .none{color:#9aa0aa}
body.dark thead th{background:#161b22;color:#9aa0aa;border-color:#2a2e37}
body.dark td{border-color:#1c222b}body.dark tr:hover td{background:#11161d}
body.dark .co{color:#e6edf3}body.dark .co .x{background:#21262d;border-color:#3a414b;color:#e6edf3}
"""

_JS = """
(function(){
 var q=document.getElementById('q'),rows=[].slice.call(document.querySelectorAll('tbody tr')),
  cnt=document.getElementById('cnt'),ind='';
 function apply(){
  var t=(q.value||'').trim().toLowerCase().split(/\\s+/).filter(Boolean),n=0;
  rows.forEach(function(r){
   var s=r.getAttribute('data-s');
   var ok=(!ind||r.getAttribute('data-i')===ind)&&t.every(function(w){return s.indexOf(w)>=0;});
   r.style.display=ok?'':'none';if(ok)n++;});
  cnt.textContent=n+' / '+rows.length+' 품목';}
 q.addEventListener('input',apply);
 [].slice.call(document.querySelectorAll('.chip')).forEach(function(c){
  c.addEventListener('click',function(){
   if(c.classList.contains('on')){c.classList.remove('on');ind='';}
   else{document.querySelectorAll('.chip.on').forEach(function(o){o.classList.remove('on');});
    c.classList.add('on');ind=c.getAttribute('data-i');}
   apply();});});
 // 📥 CSV — 현재 보이는(검색·산업 필터 반영) 품목을 CSV 로 (사용자 2026-06-18).
 function cell(s){return '"'+String(s==null?'':s).replace(/"/g,'""')+'"';}
 document.getElementById('csv').addEventListener('click',function(){
  var out=['품목,MTI,산업,구성HS10,관련상장사'];
  rows.forEach(function(r){
   if(r.style.display==='none')return;
   var td=r.querySelectorAll('td');
   var nm=(td[0].querySelector('.nm')||{}).textContent||'';
   var mti=(td[0].querySelector('.mti')||{}).textContent||'';
   var ind2=td[1].textContent||'';
   var hs=td[2].textContent||'';
   var co=[].map.call(td[3].querySelectorAll('.x'),function(x){return x.textContent;}).join('; ');
   out.push([nm,mti,ind2,hs,co].map(cell).join(','));});
  var blob=new Blob(['\\ufeff'+out.join('\\n')],{type:'text/csv;charset=utf-8'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='품목_레퍼런스북.csv';document.body.appendChild(a);a.click();a.remove();});
 // 테마 = 대시보드와 동일 시간기반(KST 19시~07시 다크). prefers-color-scheme(OS) 아님.
 function applyDark(){var h=(new Date().getUTCHours()+9)%24;
  document.body.classList.toggle('dark',h>=19||h<7);}
 applyDark();setInterval(applyDark,60000);
 apply();
})();
"""


def render_page(rows: list[dict], *, now: datetime | None = None) -> str:
    """검색 가능한 자체완결 HTML. 순수."""
    from trade.archive_template import SCROLL_RESTORE_JS  # 뒤로가기 스크롤 복원(공용)
    e = _html.escape
    now = now or datetime.now(_KST)
    inds = sorted({r["industry"] for r in rows if r.get("industry")})
    chips = "".join(f'<span class="chip" data-i="{e(i)}">{e(i)}</span>' for i in inds)
    body = []
    for r in rows:
        hs = ", ".join(r.get("hs") or [])
        cos = r.get("companies") or []
        co_html = ("".join(f'<span class="x">{e(c)}</span>' for c in cos)
                   if cos else '<span class="none">—</span>')
        search = " ".join([r["name"], r["mti6"], r["industry"], hs,
                           " ".join(cos)]).lower()
        body.append(
            f'<tr data-s="{e(search)}" data-i="{e(r["industry"])}">'
            f'<td><span class="nm">{e(r["name"])}</span> '
            f'<span class="mti">{e(r["mti6"])}</span></td>'
            f'<td class="ind">{e(r["industry"])}</td>'
            f'<td class="hs">{e(hs) or "—"}</td>'
            f'<td class="co">{co_html}</td></tr>')
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>📖 품목 레퍼런스북</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        "<div class='nav'><a href='./'>← 대시보드</a></div>"
        "<h1>📖 품목 레퍼런스북</h1>"
        f"<p class='sub'>MTI 품목 ↔ 구성 HS10 코드 ↔ 산업 ↔ 관련 상장사 · "
        f"무역협회 HSK-MTI 연계표 + 큐레이션·채널 관련기업 · 총 {len(rows):,}품목 · "
        f"기준 {now.strftime('%Y-%m-%d %H:%M')} KST</p>"
        "<div class='bar'><input id='q' type='search' "
        "placeholder='검색: 품목명 / HS코드 / 산업 / 기업명' autocomplete='off'>"
        "<button id='csv' class='dl' type='button' "
        "title='현재 보이는 품목을 CSV로 내려받기'>📥 CSV</button>"
        f"{chips}</div>"
        "<p id='cnt' class='cnt'></p>"
        "<table><thead><tr><th>품목 (MTI)</th><th>산업</th>"
        "<th>구성 HS10 코드</th><th>관련 상장사</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        f"<script>{_JS}</script></div>{SCROLL_RESTORE_JS}</body></html>")


def regenerate(out_path: Path | None = None) -> Path:
    """레퍼런스북 HTML 생성. 데이터 없으면 빈 안내 페이지."""
    rows = build_rows()
    html = render_page(rows)
    out = out_path or PAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def ensure_exists() -> None:
    """대시보드 링크 404 방지 — 없으면 생성. best-effort."""
    try:
        if not PAGE.exists():
            regenerate()
    except Exception:
        pass
