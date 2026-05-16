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

from trade.store import latest_per_dedup_key, list_all_alerts, open_db, stats


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
        all_alerts = list_all_alerts(conn)
        s = stats(conn)
    finally:
        conn.close()
    # list_all_alerts orders so the first row of each dedup_key block is
    # the 'latest' (newest posted_at + final-wins-tie). The remainder of
    # each block is the history rendered inline in the modal for visual
    # comparison.
    seen: set[str] = set()
    latest_ids: list[int] = []
    for a in all_alerts:
        key = a.get("dedup_key") or ""
        if key not in seen:
            seen.add(key)
            latest_ids.append(a["id"])
    return _build_html(all_alerts, latest_ids, s, media_url_prefix)


def _alert_to_payload(a: dict, media_prefix: str) -> dict:
    """Strip the alert down to the fields the client view actually uses.

    Includes dedup_key so the modal can find sibling history alerts
    without a server round-trip, plus all the extra metadata the CSV
    export needs (item_raw, multi region/country JSON, composite parts,
    title_kind, commentary). raw_text and source_* fields are
    intentionally omitted from the payload — they'd inflate it
    materially for little browser-side gain.
    """
    return {
        "id": a["id"],
        "dir": a["direction"],
        "status": a["status"],
        "item": a["item"],
        "item_raw": a.get("item_raw") or a["item"],
        "region": a.get("region") or "",
        "country": a.get("country") or "",
        "regions": a.get("regions") or [],
        "countries": a.get("countries") or [],
        "stocks": a.get("stocks") or [],
        "stocks_meta": a.get("stocks_meta") or {},
        "is_composite": bool(a.get("is_composite")),
        "composite_parts": a.get("composite_parts") or [],
        "title_kind": a.get("title_kind") or "",
        "commentary": a.get("commentary") or "",
        "posted_at": (a.get("posted_at") or "")[:10],
        "ingested_at": (a.get("ingested_at") or "")[:19],
        "period_start": a.get("period_start") or "",
        "period_end": a.get("period_end") or "",
        "period_kind": a.get("period_kind") or "",
        "media": [media_prefix + p for p in (a.get("media_paths") or [])],
        "has_etc": bool(a.get("has_etc")),
        "warnings": a.get("parse_warnings") or [],
        "dedup_key": a.get("dedup_key") or "",
    }


def _build_html(
    alerts: list[dict],
    latest_ids: list[int],
    s: dict,
    media_prefix: str,
) -> str:
    payload = [_alert_to_payload(a, media_prefix) for a in alerts]
    payload_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    latest_ids_json = json.dumps(latest_ids, separators=(",", ":"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_status = s.get("by_status", {})
    by_dir = s.get("by_direction", {})

    head = (
        f'<header><h1>🇰🇷 한국 수출입 데이터</h1>'
        f'<div class="meta">'
        f"갱신 {escape(now)} · "
        f"총 {s.get('total', 0)}건 (최신 {len(latest_ids)}개) · "
        f"수출 {by_dir.get('export', 0)} / 수입 {by_dir.get('import', 0)} · "
        f"잠정 {by_status.get('preliminary', 0)} / 확정 {by_status.get('final', 0)} · "
        f"품목 {s.get('distinct_items', 0)}"
        f"</div>"
        # Two extra lines populated by JS on render(): the next BeOn
        # announcement countdown and today's activity stats. Hidden via
        # CSS when empty so first paint stays clean.
        f'<div class="meta meta-next" id="meta-next"></div>'
        f'<div class="meta meta-today" id="meta-today"></div>'
        f"</header>"
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
        '<div class="top-row">'
        '<input type="search" id="q" placeholder="검색: 품목 / 회사 / 국가" autocomplete="off">'
        '<button type="button" id="csv-btn" class="csv-btn" title="현재 필터 결과를 CSV로 내려받기">📥 CSV</button>'
        '</div>'
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
        f'<div class="count"><span id="visible-count">{len(latest_ids)}</span> / {len(latest_ids)} 표시 중</div>'
        '</section>'
        '<main id="items-view" class="view active"></main>'
        '<main id="companies-view" class="view"></main>'
        '<div id="modal" class="modal" hidden>'
        '<div class="modal-backdrop"></div>'
        '<div class="modal-content">'
        '<button class="modal-close" type="button" aria-label="닫기">×</button>'
        '<div id="modal-body"></div>'
        '</div>'
        '</div>'
        '<script>'
        f"const ALERTS={payload_json};\n"
        f"const LATEST_IDS=new Set({latest_ids_json});\n"
        + _JS
        + '</script></body></html>'
    )


_CSS = """
:root{
  --bg:#f5f5f7;--surface:#fff;--surface-2:#fafafd;--text:#1d1d1f;
  --text-sub:#6e6e73;--border:#d2d2d7;--border-soft:#e5e5e7;
  --chip-bg:#f0f0f0;--accent:#0071e3;
  --tone-export:#34c759;--tone-import:#ff9500;
  --b-export-bg:#d1f4d8;--b-export-fg:#1f7a32;
  --b-import-bg:#fff0d1;--b-import-fg:#8a5a00;
  --b-prelim-bg:#eee;--b-prelim-fg:#6e6e73;
  --b-final-bg:#c8e6ff;--b-final-fg:#003e7e;
  --b-comp-bg:#ffe0e0;--b-comp-fg:#8a2020;
  --shadow:0 1px 3px rgba(0,0,0,.06);
  --img-placeholder:#e5e5e7;
}
body.dark{
  --bg:#1a1a1c;--surface:#2c2c2e;--surface-2:#252527;--text:#f5f5f7;
  --text-sub:#b8b8bd;--border:#3a3a3c;--border-soft:#3a3a3c;
  --chip-bg:#3a3a3c;--accent:#0a84ff;
  --tone-export:#30d158;--tone-import:#ff9f0a;
  --b-export-bg:#0f3a1a;--b-export-fg:#5fd778;
  --b-import-bg:#3a2807;--b-import-fg:#ffb84d;
  --b-prelim-bg:#3a3a3c;--b-prelim-fg:#98989d;
  --b-final-bg:#0a2a4d;--b-final-fg:#7ab6ff;
  --b-comp-bg:#4a1a1a;--b-comp-fg:#ff7b7b;
  --shadow:0 1px 3px rgba(0,0,0,.4);
  --img-placeholder:#1f1f21;
}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif;margin:0;padding:0;background:var(--bg);color:var(--text);line-height:1.4;transition:background .25s,color .25s}
header{background:var(--surface);padding:14px 18px;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
h1{margin:0 0 4px;font-size:18px}
.meta{font-size:11px;color:var(--text-sub);line-height:1.5}
.meta-next,.meta-today{margin-top:2px}
.meta-next:empty,.meta-today:empty{display:none}
.meta-next strong{color:var(--accent);font-weight:600}
.meta-today strong{color:var(--text);font-weight:600}
.tabs{display:flex;background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:60px;z-index:9}
.tab{flex:1;padding:13px 0;background:none;border:none;font-size:14px;font-weight:600;color:var(--text-sub);cursor:pointer;border-bottom:2px solid transparent}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.filters{background:var(--surface);padding:10px 18px;border-bottom:1px solid var(--border);position:sticky;top:108px;z-index:8}
#q{width:100%;padding:9px 12px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;font-size:14px;margin-bottom:8px}
.chips{display:flex;gap:14px;flex-wrap:wrap}
.chip-group{display:flex;gap:3px;flex-wrap:wrap}
.chip{padding:5px 11px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:14px;font-size:12px;cursor:pointer}
.chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.count{margin-top:7px;font-size:11px;color:var(--text-sub)}
.view{display:none;padding:12px}
.view.active{display:block}
.section{background:var(--surface);border-radius:12px;margin-bottom:14px;overflow:hidden;box-shadow:var(--shadow)}
.section-header{padding:13px 14px;background:var(--surface-2);border-bottom:1px solid var(--border-soft)}
.section-header h2{margin:0;font-size:15px}
.section-header .sub-line{margin-top:4px;font-size:12px;color:var(--text-sub);word-break:keep-all;line-height:1.4}
.section-items{padding:8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
.mini-card{border:1px solid var(--border-soft);border-radius:8px;overflow:hidden;cursor:pointer;transition:transform .1s;background:var(--surface);position:relative}
.mini-card:hover{transform:translateY(-1px)}
.mini-card .mini-img{height:90px;background:var(--img-placeholder);background-size:cover;background-position:center}
.mini-card .mini-text{padding:8px 10px;font-size:12px;line-height:1.35}
.mini-card .mini-text strong{display:block;margin-bottom:2px;font-size:12.5px;font-weight:600;word-break:keep-all;color:var(--text)}
.mini-card .mini-text span{color:var(--text-sub);font-size:11.5px}
.mini-card .dot{position:absolute;top:6px;right:6px;width:8px;height:8px;border-radius:4px;background:#999}
.mini-card .mini-new{position:absolute;top:5px;left:5px;padding:1px 6px;font-size:9px;font-weight:700;letter-spacing:.5px;background:#ff3b30;color:#fff;border-radius:3px;z-index:1;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.section-header .section-new{display:inline-block;margin-left:6px;padding:1px 6px;font-size:10px;font-weight:700;letter-spacing:.5px;background:#ff3b30;color:#fff;border-radius:3px;vertical-align:middle}
.mini-card.export .dot{background:var(--tone-export)}
.mini-card.import .dot{background:var(--tone-import)}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;margin-right:3px;letter-spacing:.3px}
.badge.export{background:var(--b-export-bg);color:var(--b-export-fg)}
.badge.import{background:var(--b-import-bg);color:var(--b-import-fg)}
.badge.preliminary{background:var(--b-prelim-bg);color:var(--b-prelim-fg)}
.badge.final{background:var(--b-final-bg);color:var(--b-final-fg)}
.badge.composite{background:var(--b-comp-bg);color:var(--b-comp-fg)}
.badge.sla-pending{background:var(--b-prelim-bg);color:var(--text)}
.badge.sla-due{background:var(--b-import-bg);color:var(--b-import-fg)}
.badge.sla-late{background:var(--b-comp-bg);color:var(--b-comp-fg)}
.mini-sla{display:inline-block;margin-top:4px;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;letter-spacing:.2px}
.mini-sla.pending{background:var(--b-prelim-bg);color:var(--text-sub)}
.mini-sla.due{background:var(--b-import-bg);color:var(--b-import-fg)}
.mini-sla.late{background:var(--b-comp-bg);color:var(--b-comp-fg)}
.csv-btn{display:inline-flex;align-items:center;gap:4px;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:5px 11px;border-radius:14px;font-size:12px;cursor:pointer;margin-left:auto}
.csv-btn:hover{background:var(--surface-2)}
.filters .top-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.filters .top-row #q{flex:1;margin-bottom:0}
.empty{padding:40px 20px;text-align:center;color:var(--text-sub);font-size:13px}
.modal{position:fixed;inset:0;z-index:100;display:flex;align-items:flex-start;justify-content:center}
.modal[hidden]{display:none}
.modal-backdrop{position:absolute;inset:0;background:rgba(0,0,0,.7);cursor:pointer}
.modal-content{position:relative;background:var(--surface);color:var(--text);width:100%;max-width:880px;margin:20px;border-radius:14px;overflow:hidden;display:flex;flex-direction:column;max-height:calc(100vh - 40px);box-shadow:0 10px 40px rgba(0,0,0,.3)}
.modal-close{position:absolute;top:10px;right:10px;width:34px;height:34px;background:rgba(0,0,0,.55);color:#fff;border:none;border-radius:17px;font-size:20px;line-height:1;cursor:pointer;z-index:2}
#modal-body{overflow-y:auto;flex:1}
.modal-head{padding:18px 22px;border-bottom:1px solid var(--border-soft)}
.modal-head h2{margin:0 0 8px;font-size:18px;word-break:keep-all;padding-right:40px}
.modal-head .period-label{margin-top:8px;font-size:13px;color:var(--text);font-weight:500}
.modal-head .sub{margin-top:4px;font-size:12px;color:var(--text-sub)}
.stocks{margin-top:10px;font-size:12px}
.stocks .label{color:var(--text-sub);margin-right:5px}
.stock{display:inline-block;padding:3px 8px;margin:2px 3px 0 0;background:var(--chip-bg);color:var(--text);border-radius:4px;font-size:11px}
.modal-images{display:flex;flex-direction:column;gap:1px;background:var(--bg)}
.modal-images img{width:100%;display:block;background:var(--img-placeholder)}
.modal-text{padding:14px 22px;font-size:12px;color:var(--text-sub);white-space:pre-wrap;border-top:1px solid var(--border-soft);background:var(--surface-2)}
.modal-toolbar{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}
.tool-btn{padding:5px 11px;font-size:12px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:14px;cursor:pointer}
.tool-btn:hover:not(:disabled){background:var(--surface-2)}
.tool-btn:disabled{opacity:.6;cursor:default}
.modal-links,.modal-peers{margin-top:10px;font-size:12px}
.modal-links .label,.modal-peers .label{color:var(--text-sub);margin-right:6px;font-size:11px}
.link-chip{padding:3px 9px;margin:2px 3px 0 0;background:var(--chip-bg);color:var(--text);border:1px solid var(--border-soft);border-radius:4px;font-size:11px;cursor:pointer;font-weight:500}
.link-chip:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.peer-chip{display:inline-block;padding:2px 7px;margin:2px 3px 0 0;background:var(--chip-bg);color:var(--text);border-radius:4px;font-size:11px;cursor:pointer}
.peer-chip:hover{background:var(--accent);color:#fff}
.modal-card{display:block}
.modal-card.secondary{border-top:1px solid var(--border-soft);cursor:pointer;transition:background .15s}
.modal-card.secondary:hover{background:var(--surface-2)}
.modal-card.secondary .modal-head{padding:14px 22px}
.modal-card.secondary .modal-head .sib-title{margin:0;font-size:13px;font-weight:600;color:var(--text)}
.modal-divider{padding:14px 22px 4px;font-size:11px;color:var(--text-sub);font-weight:600;text-align:center;background:var(--surface-2);border-top:1px solid var(--border-soft)}
@media (max-width:600px){
  header{padding:12px 14px}.filters{padding:10px 14px}.view{padding:8px}
  .section-items{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
  .modal-content{margin:0;border-radius:0;max-height:100vh}
}
"""


_JS = r"""
// --- helpers ---
function esc(s){return s==null?'':String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function whereLabel(a){return [a.region,a.country].filter(Boolean).join(' → ')}

// Build a one-shot dedup_key → alerts[] index so the modal can find
// sibling history alerts (and so the views don't have to repeat work).
const BY_DEDUP={};
ALERTS.forEach(a=>{(BY_DEDUP[a.dedup_key]=BY_DEDUP[a.dedup_key]||[]).push(a)});

function isLatest(a){return LATEST_IDS.has(a.id)}

// Union of stocks across a section's variants, sorted by frequency
// (so the most-mentioned company shows first). Drives the section
// subtitle in 품목별 view so the operator sees the relevant company
// names without opening every mini-card.
function unionStocks(variants){
  const counts={};
  variants.forEach(a=>{(a.stocks||[]).forEach(s=>{counts[s]=(counts[s]||0)+1})});
  return Object.entries(counts)
    .sort((x,y)=>y[1]-x[1]||x[0].localeCompare(y[0]))
    .map(([n])=>n);
}

function stocksSubtitle(variants){
  const s=unionStocks(variants);
  if(!s.length)return '';
  // '·' separator reads more softly than '/' for Korean company names
  // sitting next to each other in a single line.
  if(s.length<=3)return '관련종목: '+s.join(' · ');
  return '관련종목: '+s.slice(0,3).join(' · ')+' 외 '+(s.length-3)+'개';
}

// Expected 확정 (final) date for a 잠정 (preliminary) alert.
// Per CLAUDE.md / BeOn schedule: 관세청 publishes the previous month's
// 확정 around the 15th of the following month. So for any 잠정 whose
// period_start is in month M, the expected 확정 announcement is
// (M+1) 월 15일 KST.
function expectedFinalKst(a){
  if(a.status==='final')return null;
  const ps=a.period_start||'';
  if(!ps)return null;
  const [y,m]=ps.split('-').map(Number);
  if(!y||!m)return null;
  const ny=m===12?y+1:y;
  const nm=m===12?1:m+1;
  return ny+'-'+String(nm).padStart(2,'0')+'-15';
}

function kstTodayString(){
  const utcMs=Date.now();
  // KST = UTC + 9h. Shift the timestamp so a UTC midnight in KST
  // round-trips to the right calendar date when sliced.
  return new Date(utcMs+9*3600000).toISOString().slice(0,10);
}

// Next-up BeOn publication date. The schedule (KST):
//   매월 11일경 — 1-10일 잠정
//   매월 21일경 — 1-20일 잠정
//   익월 1일경 — 전월 전체 잠정
//   익월 15일경 — 전월 전체 확정 (관세청)
// Returns {date, kind, daysUntil} for the first one strictly after today.
function nextAnnouncement(){
  const today=kstTodayString();
  const [y,m]=today.split('-').map(Number);
  const ny=m===12?y+1:y;
  const nm=m===12?1:m+1;
  const pad=n=>String(n).padStart(2,'0');
  const cands=[
    {date:y+'-'+pad(m)+'-11', kind:m+'월 1-10일 잠정'},
    {date:y+'-'+pad(m)+'-21', kind:m+'월 1-20일 잠정'},
    {date:ny+'-'+pad(nm)+'-01', kind:m+'월 전체 잠정'},
    {date:ny+'-'+pad(nm)+'-15', kind:m+'월 전체 확정'},
  ];
  const future=cands.filter(c=>c.date>today).sort((a,b)=>a.date.localeCompare(b.date));
  if(!future.length)return null;
  const f=future[0];
  return {date:f.date, kind:f.kind, daysUntil:daysBetween(today,f.date)};
}

// 'Today's activity' summary. Counts the latest-visible alerts whose
// posted_at lands on today's KST date and separately how many of those
// are 확정 arrivals and how many bring a never-before-seen item into
// the corpus.
function quickStats(){
  const today=kstTodayString();
  let newToday=0,finalsToday=0,firstItemsToday=0;
  ALERTS.forEach(a=>{
    if(!isLatest(a))return;
    if(a.posted_at!==today)return;
    newToday++;
    if(a.status==='final')finalsToday++;
    if(EARLIEST_ITEM_DATE[a.item]===today)firstItemsToday++;
  });
  return {newToday,finalsToday,firstItemsToday};
}

// One-shot precompute used by the NEW badges and quickStats. For each
// item/company we cache the date of its first-ever alert; if that date
// is within 24 hours of today (KST), the latest card / company section
// shows a 'NEW' chip. cheap O(n) on page load.
const EARLIEST_ITEM_DATE={};
const EARLIEST_COMPANY_DATE={};
(function(){
  ALERTS.forEach(a=>{
    const pa=a.posted_at||'';
    if(!pa)return;
    if(!EARLIEST_ITEM_DATE[a.item]||pa<EARLIEST_ITEM_DATE[a.item]){
      EARLIEST_ITEM_DATE[a.item]=pa;
    }
    (a.stocks||[]).forEach(s=>{
      if(!EARLIEST_COMPANY_DATE[s]||pa<EARLIEST_COMPANY_DATE[s]){
        EARLIEST_COMPANY_DATE[s]=pa;
      }
    });
  });
})();

function withinDays(dateStr,n){
  if(!dateStr)return false;
  const today=kstTodayString();
  return daysBetween(dateStr,today)<=n;
}

// NEW badges:
//   alert-NEW   = the latest alert for this dedup_key was posted today
//                 (i.e. fresh from this cycle, < 24h old in KST)
//   item-NEW    = this item's earliest alert ever is within 7 days
//                 (the item itself debuted recently in our corpus)
//   company-NEW = same rule for a company name
function isAlertNew(a){return a.posted_at===kstTodayString()}
function isItemNew(item){return withinDays(EARLIEST_ITEM_DATE[item]||'',7)}
function isCompanyNew(name){return withinDays(EARLIEST_COMPANY_DATE[name]||'',7)}

function daysBetween(aStr,bStr){
  const a=new Date(aStr+'T00:00:00Z').getTime();
  const b=new Date(bStr+'T00:00:00Z').getTime();
  return Math.round((b-a)/86400000);
}

// SLA badge for the dashboard card. Returns {text, kind} or null when
// no SLA applies (already 확정, or period_start missing). 'pending' /
// 'due' / 'late' map to three color treatments in the CSS.
function slaBadge(a){
  const target=expectedFinalKst(a);
  if(!target)return null;
  const today=kstTodayString();
  const diff=daysBetween(today,target);
  if(diff>0)return {text:'확정 D-'+diff,kind:'pending'};
  if(diff===0)return {text:'확정 D-DAY',kind:'due'};
  return {text:'확정 D+'+(-diff)+' 지연',kind:'late'};
}

// Region tier for in-section ordering. Aggregates always rise:
//   0: 전국 (no country)                    — broadest aggregate
//   1: 전국_<country>                       — country-specific aggregate
//   2: specific region (e.g. 경기 수원시)   — site-level breakdown
function regionTier(a){
  if(a.region==='전국'&&!a.country)return 0;
  if(a.region==='전국')return 1;
  return 2;
}

// Human-friendly period label that bundles status into one phrase so
// the card / modal header reads naturally in Korean.
function niceLabel(a){
  const ps=a.period_start||'';
  const m=ps.slice(5,7).replace(/^0/,'');
  const dStart=ps.slice(8,10).replace(/^0/,'');
  const dEnd=(a.period_end||'').slice(8,10).replace(/^0/,'');
  const status=a.status==='final'?'확정':'잠정';
  if(a.period_kind==='monthly'&&a.status==='final')return m+'월 확정';
  if(a.period_kind==='monthly')return m+'월 전체 잠정';
  if(a.period_kind==='decadal_10')return m+'월 상순(1-10일) '+status;
  if(a.period_kind==='decadal_20')return m+'월 중순까지(1-20일) '+status;
  return m+'월 '+dStart+'-'+dEnd+'일 '+status;
}

// --- mini-card (used by BOTH views; click → modal) ---
function renderMiniCard(a){
  const where=whereLabel(a);
  const bg=(a.media&&a.media[0])?' style="background-image:url('+esc(a.media[0])+')"':'';
  const sla=slaBadge(a);
  const slaHtml=sla?'<span class="mini-sla '+sla.kind+'">'+esc(sla.text)+'</span>':'';
  // NEW chip — overlaid on the image corner. Visually pops without
  // taking text-line real estate. Latest-posted-today only.
  const newHtml=isAlertNew(a)?'<span class="mini-new">NEW</span>':'';
  return '<div class="mini-card '+a.dir+'" data-id="'+a.id+'">'+
    '<span class="dot" title="'+(a.dir==='export'?'수출':'수입')+'"></span>'+
    newHtml+
    '<div class="mini-img"'+bg+'></div>'+
    '<div class="mini-text">'+
      '<strong>'+esc(a.item)+'</strong>'+
      '<span>'+esc(where||'—')+'</span>'+
      slaHtml+
    '</div></div>';
}

// --- modal (full card view) ---
// Modal renders the clicked alert as 'primary' on top, then every
// other alert sharing the same dedup_key as 'secondary' inline below,
// newest-first. The operator scrolls to visually compare current
// 잠정/확정 against the previous report's graphs and tables side by
// side — the OCR-less path to '전번 확정 vs 이번 잠정' diffing.
function renderModalBody(a){
  const siblings=(BY_DEDUP[a.dedup_key]||[])
    .filter(x=>x.id!==a.id)
    .sort((x,y)=>(y.posted_at||'').localeCompare(x.posted_at||''));
  let html=renderModalCard(a,true);
  if(siblings.length){
    html+='<div class="modal-divider"><span>이전 발표 '+siblings.length+'건 — 비교 (클릭하면 그 시점이 위로)</span></div>';
    siblings.forEach(s=>{html+=renderModalCard(s,false)});
  }
  return html;
}

// Primary modal head also gets a small toolbar (URL copy + image save
// buttons) and three optional sections below the main card:
//   합산 ↔ 개별   bidirectional link between '+' composite items and
//                 their parts (when both exist in the corpus)
//   연관 종목      other companies that appear on alerts for the same
//                 item — discovery of peer stocks within a category
function findCompositeLinks(a){
  // Returns {asComposite: [...individual items], asPart: [...composite items]}
  // for the given alert's item.
  const out={asComposite:[], asPart:[]};
  if(a.is_composite&&a.composite_parts&&a.composite_parts.length){
    // For each part name, find latest alert with that item name.
    a.composite_parts.forEach(part=>{
      const partName=part.trim();
      if(!partName)return;
      const found=ALERTS.find(x=>isLatest(x)&&x.item===partName);
      if(found)out.asComposite.push(found);
    });
  }
  // Look for any composite alert whose composite_parts contain THIS item.
  ALERTS.forEach(x=>{
    if(!isLatest(x))return;
    if(!x.is_composite)return;
    if((x.composite_parts||[]).map(p=>p.trim()).includes(a.item)){
      out.asPart.push(x);
    }
  });
  return out;
}

function findPeerStocks(a){
  // Other companies mentioned on alerts of the SAME item (any
  // region/country). Dedup, drop the alert's own stocks.
  const own=new Set(a.stocks||[]);
  const peers={};
  ALERTS.forEach(x=>{
    if(x.item!==a.item)return;
    if(x.id===a.id)return;
    (x.stocks||[]).forEach(s=>{
      if(own.has(s))return;
      peers[s]=(peers[s]||0)+1;
    });
  });
  return Object.entries(peers).sort((x,y)=>y[1]-x[1]||x[0].localeCompare(y[0]));
}

function renderModalCard(a, primary){
  const where=whereLabel(a);
  const titleSuffix=where?' ('+where+')':'';
  const dirLabel=a.dir==='export'?'수출':'수입';
  const statusLabel=a.status==='preliminary'?'잠정':'확정';
  let stocksHtml='';
  if(primary&&a.stocks&&a.stocks.length){
    stocksHtml='<div class="stocks"><span class="label">관련종목</span>'+
      a.stocks.map(s=>'<span class="stock">'+esc(s)+'</span>').join('')+
      (a.has_etc?'<span class="stock">등</span>':'')+'</div>';
  }
  let imagesHtml='';
  if(a.media&&a.media.length){
    imagesHtml='<div class="modal-images">'+
      a.media.map(p=>'<img loading="lazy" src="'+esc(p)+'" alt="">').join('')+'</div>';
  }else{
    imagesHtml='<div class="empty">이미지 없음</div>';
  }
  const titleHtml=primary
    ? '<h2>'+esc(a.item)+esc(titleSuffix)+'</h2>'
    : '<h3 class="sib-title">📅 '+esc(niceLabel(a))+'</h3>';
  const sla=slaBadge(a);
  const slaBadgeHtml=sla?'<span class="badge sla-'+sla.kind+'">'+esc(sla.text)+'</span>':'';
  const klass=primary?'modal-card primary':'modal-card secondary';

  // Primary-only sections — toolbar + composite links + peer stocks.
  let extrasHtml='';
  if(primary){
    extrasHtml+='<div class="modal-toolbar">'+
      '<button type="button" class="tool-btn" data-tool="copy-url" data-id="'+a.id+'">🔗 URL 복사</button>'+
      '<button type="button" class="tool-btn" data-tool="save-image" data-id="'+a.id+'">🖼 이미지 저장</button>'+
      '</div>';

    const links=findCompositeLinks(a);
    if(links.asComposite.length||links.asPart.length){
      const chips=[];
      links.asComposite.forEach(p=>{
        chips.push('<button type="button" class="link-chip" data-id="'+p.id+'">→ '+esc(p.item)+'</button>');
      });
      links.asPart.forEach(p=>{
        chips.push('<button type="button" class="link-chip" data-id="'+p.id+'">⤴ '+esc(p.item)+' (합산)</button>');
      });
      extrasHtml+='<div class="modal-links"><span class="label">합산 ↔ 개별</span>'+chips.join('')+'</div>';
    }

    const peers=findPeerStocks(a);
    if(peers.length){
      const shown=peers.slice(0,8);
      const more=peers.length>shown.length?' 외 '+(peers.length-shown.length)+'개':'';
      extrasHtml+='<div class="modal-peers"><span class="label">같은 품목 다른 회사</span>'+
        shown.map(([n,c])=>'<span class="peer-chip" data-name="'+esc(n)+'" title="'+c+'건">'+esc(n)+'</span>').join('')+
        esc(more)+
        '</div>';
    }
  }

  return '<div class="'+klass+'" data-id="'+a.id+'">'+
    '<div class="modal-head">'+
      titleHtml+
      '<div class="badges">'+
        (primary?'<span class="badge '+a.dir+'">'+dirLabel+'</span>':'')+
        '<span class="badge '+a.status+'">'+statusLabel+'</span>'+
        (a.is_composite?'<span class="badge composite">합산</span>':'')+
        slaBadgeHtml+
      '</div>'+
      (primary?'<div class="period-label">📅 '+esc(niceLabel(a))+'</div>':'')+
      '<div class="sub">게시 '+esc(a.posted_at)+'</div>'+
      stocksHtml+
      extrasHtml+
    '</div>'+imagesHtml+'</div>';
}

function showModal(a){
  document.getElementById('modal-body').innerHTML=renderModalBody(a);
  document.getElementById('modal').hidden=false;
  document.body.style.overflow='hidden';
}
function hideModal(){
  document.getElementById('modal').hidden=true;
  document.body.style.overflow='';
}

// --- state + filter ---
const state={dir:'',status:'',q:''};
function matches(a){
  if(!isLatest(a))return false;  // views render the latest of each dedup_key
  if(state.dir&&a.dir!==state.dir)return false;
  if(state.status&&a.status!==state.status)return false;
  if(state.q){
    const q=state.q.toLowerCase();
    const hay=(a.item+' '+a.region+' '+a.country+' '+(a.stocks||[]).join(' ')).toLowerCase();
    if(!hay.includes(q))return false;
  }
  return true;
}

// --- view builders ---
// Section header can carry one or two muted subtitle lines under the
// title. 품목별 uses [stocks-union, region/country-count]; 회사별 uses
// just [items-count]. Empty entries are dropped silently. The optional
// `newBadge` flag puts a small NEW chip next to the title for sections
// whose item/company first appeared within the last 7 days.
function renderSection(title, subtitles, miniCardsHtml, newBadge){
  const lines=(subtitles||[]).filter(Boolean)
    .map(s=>'<div class="sub-line">'+esc(s)+'</div>').join('');
  const badge=newBadge?' <span class="section-new">NEW</span>':'';
  return '<section class="section">'+
    '<div class="section-header">'+
      '<h2>'+esc(title)+badge+'</h2>'+
      lines+
    '</div>'+
    '<div class="section-items">'+miniCardsHtml+'</div>'+
  '</section>';
}

function buildItemsView(filtered){
  // Group filtered alerts by item; sort sections by the most recent
  // posted_at of any variant (newest items rise to the top); within
  // each section sort variants by recency too.
  const byItem={};
  filtered.forEach(a=>{(byItem[a.item]=byItem[a.item]||[]).push(a)});
  const sorted=Object.entries(byItem).sort((x,y)=>{
    const xMax=x[1].reduce((m,a)=>(a.posted_at||'')>m?a.posted_at:m,'');
    const yMax=y[1].reduce((m,a)=>(a.posted_at||'')>m?a.posted_at:m,'');
    return yMax.localeCompare(xMax)||x[0].localeCompare(y[0]);
  });
  if(!sorted.length)return '<div class="empty">조건에 맞는 품목이 없습니다.</div>';
  return sorted.map(([name,variants])=>{
    // Within a 품목 section: 전국 aggregates first (broadest, then
    // country-specific), then site-level breakdowns. Inside each tier
    // sort by posted_at desc so the freshest report is on top.
    variants.sort((a,b)=>
      regionTier(a)-regionTier(b)||
      (b.posted_at||'').localeCompare(a.posted_at||'')
    );
    const cards=variants.map(renderMiniCard).join('');
    return renderSection(name, [
      stocksSubtitle(variants),
      variants.length+'개 (지역/국가)',
    ], cards, isItemNew(name));
  }).join('');
}

function buildCompaniesView(filtered){
  const byCompany={};
  filtered.forEach(a=>{(a.stocks||[]).forEach(s=>{(byCompany[s]=byCompany[s]||[]).push(a)})});
  let sorted=Object.entries(byCompany).sort((x,y)=>y[1].length-x[1].length||x[0].localeCompare(y[0]));
  // RULE: in 회사별 view, if the search query matches one or more
  // company names directly, narrow to those sections. Otherwise keep
  // the existing alert-content match behaviour for queries like '라면'.
  if(state.q){
    const q=state.q.toLowerCase();
    const direct=sorted.filter(([n])=>n.toLowerCase().includes(q));
    if(direct.length)sorted=direct;
  }
  if(!sorted.length)return '<div class="empty">조건에 맞는 회사가 없습니다.</div>';
  return sorted.map(([name,items])=>{
    // Within a 회사 section: keep same-item rows clustered (so '라면'
    // entries stay together, '디램' together, ...), then inside each
    // item cluster apply the same 전국-tier + posted_at-desc rule as
    // the 품목별 view.
    items.sort((a,b)=>
      (a.item||'').localeCompare(b.item||'')||
      regionTier(a)-regionTier(b)||
      (b.posted_at||'').localeCompare(a.posted_at||'')
    );
    const cards=items.map(renderMiniCard).join('');
    return renderSection(name, [items.length+'개 품목'], cards, isCompanyNew(name));
  }).join('');
}

function renderHeaderMeta(){
  const next=nextAnnouncement();
  const mn=document.getElementById('meta-next');
  if(next){
    mn.innerHTML='⏭ 다음 발표 <strong>D-'+next.daysUntil+'</strong> ('+esc(next.date)+' · '+esc(next.kind)+')';
  }else{
    mn.textContent='';
  }
  const q=quickStats();
  const mt=document.getElementById('meta-today');
  if(q.newToday>0){
    const parts=['오늘 신규 <strong>'+q.newToday+'</strong>건'];
    if(q.finalsToday)parts.push('확정 도착 <strong>'+q.finalsToday+'</strong>');
    if(q.firstItemsToday)parts.push('첫 등장 품목 <strong>'+q.firstItemsToday+'</strong>');
    mt.innerHTML='📌 '+parts.join(' · ');
  }else{
    mt.textContent='';
  }
}

function render(){
  const filtered=ALERTS.filter(matches);
  document.getElementById('visible-count').textContent=filtered.length;
  document.getElementById('items-view').innerHTML=buildItemsView(filtered);
  document.getElementById('companies-view').innerHTML=buildCompaniesView(filtered);
  renderHeaderMeta();
}

// --- tab / chip / search / modal events (delegated) ---
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
function alertShareUrl(a){
  // Hash-based deep link: #a/<id>. Hashes never round-trip to the
  // server so this works even behind BasicAuth.
  return location.origin+location.pathname+'#a/'+a.id;
}

async function copyToClipboard(text){
  try{
    await navigator.clipboard.writeText(text);
    return true;
  }catch(_){
    // Fallback for older browsers / insecure contexts.
    const ta=document.createElement('textarea');
    ta.value=text;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');return true}finally{document.body.removeChild(ta)}
  }
}

function flashTool(btn,msg){
  const orig=btn.textContent;btn.textContent=msg;btn.disabled=true;
  setTimeout(()=>{btn.textContent=orig;btn.disabled=false},1500);
}

document.addEventListener('click',e=>{
  // Composite link chips inside the modal — open the linked alert
  // as the new primary. Handled before .modal-card.secondary because
  // they live inside it.
  const link=e.target.closest('.link-chip');
  if(link&&link.dataset.id){
    const id=parseInt(link.dataset.id,10);
    const a=ALERTS.find(x=>x.id===id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  // Peer-stock chip → close modal, search by company, switch to
  // 회사별 tab (smart-search narrows to that company section).
  const peer=e.target.closest('.peer-chip');
  if(peer&&peer.dataset.name){
    state.q=peer.dataset.name;
    document.getElementById('q').value=peer.dataset.name;
    document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    document.querySelector('[data-tab="companies"]').classList.add('active');
    document.getElementById('companies-view').classList.add('active');
    render();
    hideModal();
    return;
  }
  // Toolbar buttons inside the modal: copy URL / save image.
  const tool=e.target.closest('.tool-btn');
  if(tool&&tool.dataset.tool){
    const id=parseInt(tool.dataset.id,10);
    const a=ALERTS.find(x=>x.id===id);
    if(!a)return;
    if(tool.dataset.tool==='copy-url'){
      copyToClipboard(alertShareUrl(a)).then(ok=>flashTool(tool,ok?'복사됨 ✓':'복사 실패'));
    }else if(tool.dataset.tool==='save-image'){
      const url=(a.media&&a.media[0])?absUrl(a.media[0]):null;
      if(!url){flashTool(tool,'이미지 없음');return}
      const link=document.createElement('a');
      link.href=url;link.download='trade-'+a.id+'.jpg';link.target='_blank';
      document.body.appendChild(link);link.click();document.body.removeChild(link);
    }
    return;
  }
  const card=e.target.closest('.mini-card');
  if(card&&card.dataset.id){
    const id=parseInt(card.dataset.id,10);
    const a=ALERTS.find(x=>x.id===id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  // Clicking a secondary card inside the modal swaps it to primary,
  // so the operator can drill deeper into the history (e.g. compare
  // 4월 확정 → 3월 확정 → ...) without closing and reopening.
  const sib=e.target.closest('.modal-card.secondary');
  if(sib&&sib.dataset.id){
    const id=parseInt(sib.dataset.id,10);
    const a=ALERTS.find(x=>x.id===id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  if(e.target.classList.contains('modal-close')||e.target.classList.contains('modal-backdrop')){
    hideModal();
  }
});

// Deep-link support: opening 'http://.../dashboard/#a/123' auto-opens
// the modal for alert 123 once the page has finished initial render.
function handleHashDeepLink(){
  const m=location.hash.match(/^#a\/(\d+)$/);
  if(!m)return;
  const id=parseInt(m[1],10);
  const a=ALERTS.find(x=>x.id===id);
  if(a)showModal(a);
}
window.addEventListener('hashchange',handleHashDeepLink);
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&!document.getElementById('modal').hidden)hideModal();
});

// --- CSV download (current filter) ---
// Generates the CSV client-side so there's no server round-trip; the
// '﻿' BOM keeps Korean readable when Excel opens the file. Only
// the LATEST-visible-after-filter rows are exported so the download
// matches what the operator currently sees on screen.
//
// Full field set includes everything in the alert payload plus two
// computed SLA columns and absolute media URLs (so Excel cells turn
// into clickable hyperlinks instead of unreachable relative paths).
function absUrl(p){
  try{return new URL(p, location.href).href}catch(_){return p}
}

function metaPairsToString(obj){
  if(!obj)return '';
  return Object.entries(obj).map(([k,v])=>k+'='+v).join(';');
}

function downloadCSV(){
  const filtered=ALERTS.filter(matches);
  const today=kstTodayString();
  const headers=[
    'id','dedup_key','direction','status','title_kind',
    'item','item_raw','is_composite','composite_parts',
    'region','country','regions','countries',
    'stocks','stocks_meta','has_etc',
    'period_start','period_end','period_kind',
    'expected_final_date','days_to_final',
    'posted_at','ingested_at',
    'commentary','parse_warnings','media_urls',
  ];
  const rows=[headers];
  filtered.forEach(a=>{
    const expectedFinal=expectedFinalKst(a);
    const days=expectedFinal?daysBetween(today,expectedFinal):'';
    rows.push([
      a.id,a.dedup_key,a.dir,a.status,a.title_kind,
      a.item,a.item_raw,a.is_composite?1:0,(a.composite_parts||[]).join(';'),
      a.region,a.country,(a.regions||[]).join(';'),(a.countries||[]).join(';'),
      (a.stocks||[]).join(';'),metaPairsToString(a.stocks_meta),a.has_etc?1:0,
      a.period_start,a.period_end,a.period_kind,
      expectedFinal||'',days,
      a.posted_at,a.ingested_at,
      a.commentary||'',(a.warnings||[]).join(';'),
      (a.media||[]).map(absUrl).join(';'),
    ]);
  });
  const csv=rows.map(r=>r.map(v=>{
    const s=v==null?'':String(v);
    if(/[",\n]/.test(s))return '"'+s.replace(/"/g,'""')+'"';
    return s;
  }).join(',')).join('\n');
  const blob=new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download='trade-alerts-'+today+'.csv';
  document.body.appendChild(link);link.click();document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
document.getElementById('csv-btn').addEventListener('click',downloadCSV);

// --- automatic dark mode 19:00 - 07:00 KST ---
// Computes KST hour from UTC + 9 so the page works regardless of the
// viewer's local timezone. Re-checks every 60 s so the page transitions
// without a reload at 7am / 7pm KST.
function applyDarkMode(){
  const h=(new Date().getUTCHours()+9)%24;
  document.body.classList.toggle('dark', h>=19||h<7);
}
applyDarkMode();
setInterval(applyDarkMode,60000);

render();
// Fire after initial render so the modal can locate by id.
handleHashDeepLink();
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
