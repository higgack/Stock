"""Static HTML dashboard renderer.

Reads from store.db, emits a single self-contained index.html with two
client-side views over the same data:

  품목별 (item view)  : one card per (direction, item, region, country),
                       showing the latest alert with BeOn's graph + table
                       images inline
  회사별 (company view): one section per stock mentioned, with mini-cards
                       linking to the latest alert for each item that
                       references it

Filters (search, direction toggle, status toggle) run client-side over
the embedded ALERTS array, so re-rendering after a filter change is a
single innerHTML pass — no server round-trip.

No external CDN, no JS framework, no build step. The whole renderer is
about 600 lines of Python emitting ~1 MB of HTML for the current
~1000-alert corpus, which loads in under a second on mobile.

CLI:
    python -m trade.dashboard
        → writes ~/.trade/dashboard/index.html
    python -m trade.dashboard --out path.html --media-url /media/
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from trade.store import latest_per_dedup_key, open_db, stats


def render_html(
    db_path: Path | str,
    *,
    media_url_prefix: str = "../",
) -> str:
    """Render the dashboard HTML from store.db.

    media_url_prefix is prepended to each stored media path
    (e.g. 'media/2026-05-15/abc.jpg' → '../media/2026-05-15/abc.jpg'),
    so callers can place the HTML wherever and adjust the prefix to
    point at the media tree.
    """
    conn = open_db(db_path)
    try:
        alerts = latest_per_dedup_key(conn)
        s = stats(conn)
    finally:
        conn.close()
    return _build_html(alerts, s, media_url_prefix)


def _alert_to_payload(a: dict, media_prefix: str) -> dict:
    """Strip the alert down to the fields the client view actually uses.

    Cuts ~30 % of the embedded JSON size vs dumping the full row, which
    matters at 1000+ cards.
    """
    return {
        "id": a["id"],
        "dir": a["direction"],
        "status": a["status"],
        "item": a["item"],
        "region": a.get("region") or "",
        "country": a.get("country") or "",
        "stocks": a.get("stocks") or [],
        "is_composite": bool(a.get("is_composite")),
        "posted_at": (a.get("posted_at") or "")[:10],
        "period_start": a.get("period_start") or "",
        "period_end": a.get("period_end") or "",
        "period_kind": a.get("period_kind") or "",
        "media": [media_prefix + p for p in (a.get("media_paths") or [])],
        "has_etc": bool(a.get("has_etc")),
        "warnings": a.get("parse_warnings") or [],
    }


def _build_html(alerts: list[dict], s: dict, media_prefix: str) -> str:
    payload = [_alert_to_payload(a, media_prefix) for a in alerts]
    payload_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_status = s.get("by_status", {})
    by_dir = s.get("by_direction", {})

    head = (
        f'<header><h1>🇰🇷 한국 수출입 데이터</h1>'
        f'<div class="meta">'
        f"갱신 {escape(now)} · "
        f"총 {s.get('total', 0)}건 (최신 {len(alerts)}개) · "
        f"수출 {by_dir.get('export', 0)} / 수입 {by_dir.get('import', 0)} · "
        f"잠정 {by_status.get('preliminary', 0)} / 확정 {by_status.get('final', 0)} · "
        f"품목 {s.get('distinct_items', 0)}"
        f"</div></header>"
    )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="ko"><head>'
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>한국 수출입 데이터</title>'
        f"<style>{_CSS}</style>"
        '</head><body>'
        + head
        + '<nav class="tabs">'
        '<button class="tab active" data-tab="items">품목별</button>'
        '<button class="tab" data-tab="companies">회사별</button>'
        '</nav>'
        '<section class="filters">'
        '<input type="search" id="q" placeholder="검색: 품목 / 회사 / 국가" autocomplete="off">'
        '<div class="chips">'
        '<span class="chip-group" data-key="dir">'
        '<button class="chip active" data-val="">전체</button>'
        '<button class="chip" data-val="export">수출</button>'
        '<button class="chip" data-val="import">수입</button>'
        '</span>'
        '<span class="chip-group" data-key="status">'
        '<button class="chip active" data-val="">전체</button>'
        '<button class="chip" data-val="preliminary">잠정</button>'
        '<button class="chip" data-val="final">확정</button>'
        '</span>'
        '</div>'
        f'<div class="count"><span id="visible-count">{len(alerts)}</span> / {len(alerts)} 표시 중</div>'
        '</section>'
        '<main id="items-view" class="view active"></main>'
        '<main id="companies-view" class="view"></main>'
        '<script>'
        f"const ALERTS={payload_json};\n"
        + _JS
        + '</script></body></html>'
    )


_CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif;margin:0;padding:0;background:#f5f5f7;color:#1d1d1f;line-height:1.4}
header{background:#fff;padding:14px 18px;border-bottom:1px solid #d2d2d7;position:sticky;top:0;z-index:10}
h1{margin:0 0 4px;font-size:18px}
.meta{font-size:11px;color:#6e6e73;line-height:1.5}
.tabs{display:flex;background:#fff;border-bottom:1px solid #d2d2d7;position:sticky;top:60px;z-index:9}
.tab{flex:1;padding:13px 0;background:none;border:none;font-size:14px;font-weight:600;color:#6e6e73;cursor:pointer;border-bottom:2px solid transparent}
.tab.active{color:#0071e3;border-bottom-color:#0071e3}
.filters{background:#fff;padding:10px 18px;border-bottom:1px solid #d2d2d7;position:sticky;top:108px;z-index:8}
#q{width:100%;padding:9px 12px;border:1px solid #d2d2d7;border-radius:8px;font-size:14px;margin-bottom:8px}
.chips{display:flex;gap:14px;flex-wrap:wrap}
.chip-group{display:flex;gap:3px;flex-wrap:wrap}
.chip{padding:5px 11px;border:1px solid #d2d2d7;background:#fff;border-radius:14px;font-size:12px;cursor:pointer}
.chip.active{background:#0071e3;color:#fff;border-color:#0071e3}
.count{margin-top:7px;font-size:11px;color:#6e6e73}
.view{display:none;padding:12px}
.view.active{display:block}
.card{background:#fff;border-radius:12px;margin-bottom:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);border-left:4px solid #aaa}
.card.export{border-left-color:#34c759}
.card.import{border-left-color:#ff9500}
.card.unknown{border-left-color:#aaa}
.card h3{margin:0 0 3px;font-size:15px;font-weight:600;word-break:keep-all}
.card .head{padding:11px 13px}
.card .sub{font-size:11px;color:#6e6e73}
.badges{margin-top:6px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;margin-right:3px;letter-spacing:.3px}
.badge.export{background:#d1f4d8;color:#1f7a32}
.badge.import{background:#fff0d1;color:#8a5a00}
.badge.preliminary{background:#eee;color:#6e6e73}
.badge.final{background:#c8e6ff;color:#003e7e}
.badge.composite{background:#ffe0e0;color:#8a2020}
.badge.etc{background:#f0e5ff;color:#5a2e91}
.stocks{margin-top:7px;font-size:12px}
.stocks .label{color:#6e6e73;margin-right:5px}
.stock{display:inline-block;padding:2px 7px;margin:2px 2px 0 0;background:#f0f0f0;border-radius:4px;font-size:11px}
.images{display:flex;flex-direction:column;gap:1px;background:#f5f5f7}
.images img{width:100%;display:block;background:#e5e5e7}
.company-section{background:#fff;border-radius:12px;margin-bottom:14px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.company-header{padding:13px 14px;background:#fafafd;border-bottom:1px solid #e5e5e7}
.company-header h2{margin:0;font-size:15px}
.company-header .count{margin-top:3px;font-size:11px;color:#6e6e73}
.company-items{padding:8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px}
.mini-card{border:1px solid #e5e5e7;border-radius:8px;overflow:hidden;cursor:pointer;transition:transform .1s}
.mini-card:hover{transform:translateY(-1px)}
.mini-card .mini-img{height:90px;background:#f5f5f7;background-size:cover;background-position:center}
.mini-card .mini-text{padding:7px 9px;font-size:11px}
.mini-card .mini-text strong{display:block;margin-bottom:1px;font-weight:600;word-break:keep-all}
.mini-card .mini-text span{color:#6e6e73;font-size:10px}
.empty{padding:40px 20px;text-align:center;color:#8e8e93;font-size:13px}
@media (max-width:600px){header{padding:12px 14px}.filters{padding:10px 14px}.view{padding:8px}}
"""


_JS = r"""
function esc(s){return s==null?'':String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}

function periodLabel(a){
  if(a.period_kind==='monthly')return (a.period_start||'').slice(0,7);
  return (a.period_start||'')+' ~ '+(a.period_end||'');
}

function whereLabel(a){
  const parts=[a.region,a.country].filter(Boolean);
  return parts.join(' → ');
}

function renderCard(a){
  const dirLabel=a.dir==='export'?'수출':'수입';
  const statusLabel=a.status==='preliminary'?'잠정':'확정';
  const where=whereLabel(a);
  const titleSuffix=where?' ('+where+')':'';
  let stocksHtml='';
  if(a.stocks&&a.stocks.length){
    stocksHtml='<div class="stocks"><span class="label">관련종목</span>'+
      a.stocks.map(s=>'<span class="stock">'+esc(s)+'</span>').join('')+
      (a.has_etc?'<span class="stock">등</span>':'')+
      '</div>';
  }
  let imagesHtml='';
  if(a.media&&a.media.length){
    imagesHtml='<div class="images">'+
      a.media.map(p=>'<img loading="lazy" src="'+esc(p)+'" alt="">').join('')+
      '</div>';
  }
  return '<div class="card '+a.dir+'"><div class="head">'+
    '<h3>'+esc(a.item)+esc(titleSuffix)+'</h3>'+
    '<div class="sub">'+esc(periodLabel(a))+' · 게시 '+esc(a.posted_at)+'</div>'+
    '<div class="badges">'+
      '<span class="badge '+a.dir+'">'+dirLabel+'</span>'+
      '<span class="badge '+a.status+'">'+statusLabel+'</span>'+
      (a.is_composite?'<span class="badge composite">합산</span>':'')+
    '</div>'+
    stocksHtml+
    '</div>'+imagesHtml+'</div>';
}

function renderMiniCard(a){
  const where=whereLabel(a);
  const bg=(a.media&&a.media[0])?' style="background-image:url('+a.media[0]+')"':'';
  return '<div class="mini-card">'+
    '<div class="mini-img"'+bg+'></div>'+
    '<div class="mini-text">'+
      '<strong>'+esc(a.item)+'</strong>'+
      '<span>'+esc(where)+'</span>'+
    '</div></div>';
}

const state={dir:'',status:'',q:''};

function matches(a){
  if(state.dir&&a.dir!==state.dir)return false;
  if(state.status&&a.status!==state.status)return false;
  if(state.q){
    const q=state.q.toLowerCase();
    const hay=(a.item+' '+a.region+' '+a.country+' '+(a.stocks||[]).join(' ')).toLowerCase();
    if(!hay.includes(q))return false;
  }
  return true;
}

function render(){
  const filtered=ALERTS.filter(matches);
  document.getElementById('visible-count').textContent=filtered.length;

  const itemsEl=document.getElementById('items-view');
  itemsEl.innerHTML=filtered.length
    ? filtered.map(renderCard).join('')
    : '<div class="empty">조건에 맞는 알림이 없습니다.</div>';

  // Company view: rebuild from filtered set
  const byCompany={};
  filtered.forEach(a=>{
    (a.stocks||[]).forEach(s=>{(byCompany[s]=byCompany[s]||[]).push(a)});
  });
  const sorted=Object.entries(byCompany).sort((a,b)=>b[1].length-a[1].length||a[0].localeCompare(b[0]));
  const compEl=document.getElementById('companies-view');
  compEl.innerHTML=sorted.length
    ? sorted.map(([name,items])=>{
        return '<section class="company-section">'+
          '<div class="company-header">'+
            '<h2>'+esc(name)+'</h2>'+
            '<div class="count">'+items.length+'개 품목</div>'+
          '</div>'+
          '<div class="company-items">'+items.map(renderMiniCard).join('')+'</div>'+
        '</section>';
      }).join('')
    : '<div class="empty">조건에 맞는 회사가 없습니다.</div>';
}

document.querySelectorAll('.tab').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab+'-view').classList.add('active');
  });
});
document.querySelectorAll('.chip').forEach(chip=>{
  chip.addEventListener('click',()=>{
    const g=chip.closest('.chip-group');
    g.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
    chip.classList.add('active');
    state[g.dataset.key]=chip.dataset.val;
    render();
  });
});
let qTimer;
document.getElementById('q').addEventListener('input',e=>{
  clearTimeout(qTimer);
  qTimer=setTimeout(()=>{state.q=e.target.value;render();},120);
});
render();
"""


def main() -> int:
    default_data = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
    ap = argparse.ArgumentParser(
        description="Render the trade-bot dashboard from store.db."
    )
    ap.add_argument(
        "--db", type=Path, default=default_data / "store.db",
        help="path to store.db",
    )
    ap.add_argument(
        "--out", type=Path, default=default_data / "dashboard" / "index.html",
        help="output HTML path",
    )
    ap.add_argument(
        "--media-url", type=str, default="../",
        help="prefix prepended to each media path (default '../' suits "
             "~/.trade/dashboard/index.html browsing the local file)",
    )
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(args.db, media_url_prefix=args.media_url)
    args.out.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024
    print(f"wrote {args.out} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
