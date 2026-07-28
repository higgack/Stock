"""Verbatim CSS/JS extracted from NOAH bot/dashboard.py.

Self-contained: _THEME_JS theme toggle is inlined. Drop into the mirror
module unchanged. Palette/theme CSS variables live at the top — override
those (not the structural rules) if you want to rebrand.
"""

_THEME_JS = """
(function() {
  function apply() {
    var h = parseInt(new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Seoul', hour: 'numeric', hour12: false
    }).format(new Date()), 10) % 24;
    var dark = (h >= 19 || h < 7);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  }
  apply();
  setInterval(apply, 60000);
})();
"""

ARCHIVE_CSS = (
    """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bottleneck Screener — Archive</title>
<script>""" + _THEME_JS + """</script>
<style>
/* Time-based light/dark theme (Asia/Seoul) — _THEME_JS toggles
   `data-theme="dark"` on the <html> element between 19:00-07:00.
   Mirrors the NOAH index/detail pages. Variables below adapt: light
   defaults at `:root`, dark overrides at `:root[data-theme="dark"]`. */
:root {
  --bg:#f8fafc; --card:#ffffff; --border:#e5e7eb;
  --text:#1f2937; --muted:#6b7280; --accent:#0ea5e9;
  --pos:#059669; --neg:#dc2626; --neu:#6b7280; --pending:#d97706;
  --accent-soft:rgba(14,165,233,0.07);
  --accent-soft2:rgba(14,165,233,0.14);
  --surface-tint:rgba(0,0,0,0.05);
  --surface-tint-strong:rgba(0,0,0,0.07);
  --row-border:rgba(0,0,0,0.05);
  --tier-l-bg:rgba(14,165,233,0.12); --tier-l-fg:#0369a1;
  --tier-m-bg:rgba(16,185,129,0.14); --tier-m-fg:#047857;
  --tier-s-bg:rgba(245,158,11,0.18); --tier-s-fg:#b45309;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.45); --mark-fg:#7c2d12;
  --mark-target-bg:rgba(245,158,11,0.7); --mark-target-fg:#1f2937;
}
:root[data-theme="dark"] {
  --bg:#0F1219; --card:#1A1F2B; --border:#2A3142;
  --text:#E8ECF4; --muted:#94A3B8; --accent:#3B82F6;
  --pos:#10B981; --neg:#EF4444; --neu:#6B7280; --pending:#F59E0B;
  --accent-soft:rgba(59,130,246,0.06);
  --accent-soft2:rgba(59,130,246,0.15);
  --surface-tint:rgba(255,255,255,0.04);
  --surface-tint-strong:rgba(255,255,255,0.06);
  --row-border:rgba(255,255,255,0.04);
  --tier-l-bg:rgba(59,130,246,0.15); --tier-l-fg:#93C5FD;
  --tier-m-bg:rgba(16,185,129,0.15); --tier-m-fg:#6EE7B7;
  --tier-s-bg:rgba(245,158,11,0.15); --tier-s-fg:#FCD34D;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.35); --mark-fg:#FCD34D;
  --mark-target-bg:rgba(245,158,11,0.55); --mark-target-fg:#fff;
}
* { box-sizing: border-box; }
body { background:var(--bg); color:var(--text); margin:0;
  font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',
    'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji','Twemoji Mozilla',sans-serif;
  line-height:1.55; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
.nav { margin-bottom:12px; }
.nav a { color:var(--accent); text-decoration:none; font-size:13px; }
.nav a:hover { text-decoration:underline; }
h1 { font-size:22px; margin:0 0 4px; }
h2.date { font-size:14px; color:var(--muted); margin:28px 0 12px;
  padding-bottom:6px; border-bottom:1px solid var(--border); }
details.month { margin:28px 0 0; }
details.month summary.month-head { cursor:pointer; font-size:16px;
  font-weight:700; padding:12px 4px; border-bottom:2px solid var(--accent-soft);
  display:flex; align-items:center; justify-content:space-between;
  list-style:none; color:var(--text); user-select:none; }
details.month summary.month-head::-webkit-details-marker { display:none; }
details.month summary.month-head::before { content:"▸"; color:var(--accent);
  margin-right:8px; transition:transform 0.15s; }
details.month[open] summary.month-head::before { content:"▾"; }
details.month summary.month-head:hover { background:var(--accent-soft); }
details.month .count { color:var(--muted); font-size:12px; font-weight:normal; }
details.month .month-body { padding-top:4px; padding-left:6px; }
details.day { margin:24px 0 0; }
details.day summary.day-head { cursor:pointer; font-size:15px;
  font-weight:600; padding:10px 4px; border-bottom:1px solid var(--border);
  display:flex; align-items:center; justify-content:space-between;
  list-style:none; color:var(--text); user-select:none; }
details.day summary.day-head::-webkit-details-marker { display:none; }
details.day summary.day-head::before { content:"▸"; color:var(--accent);
  margin-right:8px; transition:transform 0.15s; }
details.day[open] summary.day-head::before { content:"▾"; }
details.day summary.day-head:hover { background:var(--accent-soft); }
details.day .count { color:var(--muted); font-size:12px; font-weight:normal; }
details.day .day-body { padding-top:14px; }
.sub { color:var(--muted); font-size:13px; margin:0 0 24px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px; margin-bottom:24px; }
.stat { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; }
.stat-v { font-size:20px; font-weight:600; }
.stat-l { color:var(--muted); font-size:11px; margin-top:2px;
  text-transform:uppercase; letter-spacing:0.5px; }
.card, details.card { background:var(--card); border:1px solid var(--border);
  border-radius:12px; padding:16px 18px; margin-bottom:14px; }
details.card { padding:0; }
details.card summary.card-h { cursor:pointer; list-style:none;
  padding:16px 18px; user-select:none; border-radius:12px; }
details.card[open] summary.card-h {
  border-bottom:1px solid var(--border); border-radius:12px 12px 0 0; }
details.card summary.card-h::-webkit-details-marker { display:none; }
details.card .card-toggle { color:var(--accent); font-weight:600;
  margin-right:2px; transition:transform 0.15s; }
details.card[open] .card-toggle { transform:rotate(90deg); display:inline-block; }
details.card .card-body { padding:14px 18px 18px; }
.card-h { display:flex; justify-content:space-between; align-items:center;
  gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.domain { font-weight:600; font-size:15px; flex:1; min-width:200px; }
.meta { color:var(--muted); font-size:13px; font-family:'IBM Plex Mono',monospace; }
.del-btn { background:none; border:none; cursor:pointer;
  color:var(--muted); font-size:16px; padding:4px 8px; border-radius:6px;
  margin-left:auto; }
.del-btn:hover { color:var(--neg); background:rgba(239,68,68,0.10); }
.del-btn:disabled { opacity:0.5; cursor:wait; }
.search-bar { display:flex; gap:8px; margin-bottom:14px; }
.search-bar input { flex:1; background:var(--card);
  border:1px solid var(--border); border-radius:8px; padding:10px 14px;
  color:var(--text); font-size:14px; font-family:inherit; outline:none; }
.search-bar input:focus { border-color:var(--accent); }
.search-bar button { background:var(--search-btn-bg); color:white; border:none;
  border-radius:8px; padding:0 18px; font-size:13px; font-weight:600;
  cursor:pointer; transition:transform 0.05s, background 0.1s; }
.search-bar button:hover { background:var(--search-btn-hover); }
.search-bar button:active { transform:scale(0.97); }
.status-line { color:var(--muted); font-size:12px; margin:0 0 12px; }
table.picks { width:100%; border-collapse:collapse; font-size:15px; }
table.picks th { text-align:left; color:var(--muted); font-weight:500;
  padding:8px 6px; border-bottom:1px solid var(--border); font-size:12px;
  text-transform:uppercase; letter-spacing:0.3px; }
table.picks td { padding:8px 6px; border-bottom:1px solid var(--row-border); }
table.picks tr:last-child td { border-bottom:none; }
td.rank { font-weight:600; color:var(--accent); width:36px; }
td.co { color:var(--muted); }
td code { background:var(--surface-tint); padding:2px 6px;
  border-radius:4px; font-size:12px; }
td .tier-L { background:var(--tier-l-bg); color:var(--tier-l-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td .tier-M { background:var(--tier-m-bg); color:var(--tier-m-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td .tier-S { background:var(--tier-s-bg); color:var(--tier-s-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td.pos { color:var(--pos); font-weight:600; }
td.neg { color:var(--neg); font-weight:600; }
td.neu { color:var(--muted); }
.pending { color:var(--pending); }
.empty { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:30px; text-align:center; color:var(--muted);
  font-size:14px; }
.empty code { background:var(--surface-tint-strong); padding:2px 8px;
  border-radius:4px; }
details.analysis { margin:10px 0 14px; }
details.analysis summary { cursor:pointer; color:var(--accent);
  font-size:15px; padding:7px 11px; background:var(--accent-soft);
  border-radius:6px; user-select:none; list-style:none; }
details.analysis summary::-webkit-details-marker { display:none; }
details.analysis summary::before { content:"▸ "; margin-right:4px; }
details.analysis[open] summary::before { content:"▾ "; }
details.analysis summary:hover { background:var(--accent-soft2); }
.analysis-sec { margin:12px 4px 0; padding:10px 12px;
  background:var(--surface-tint); border-left:2px solid var(--border);
  border-radius:0 6px 6px 0; }
.analysis-h { color:var(--muted); font-size:13px; font-weight:600;
  margin-bottom:6px; text-transform:none; letter-spacing:0; }
/* 카드 본문 (Screener Top-3 근거·binding·bottom_line + Daily Byte 브리프).
   13px 는 모바일에서 작음 → 15px + line-height 1.65 (SV 본문 수준).
   2026-05-29 사용자 요청. */
.analysis-b { color:var(--text); font-size:14px; line-height:1.65;
  white-space:pre-wrap; }
.ticker-chip { cursor:help; border-bottom:1px dotted var(--muted); }
.ticker-chip:hover { color:var(--accent); }
details.analysis-mt { margin:12px 4px 0; }
details.analysis-mt summary { cursor:pointer; color:var(--accent);
  font-size:14px; padding:6px 10px; background:var(--accent-soft2);
  border-radius:6px; list-style:none; user-select:none; }
details.analysis-mt summary::-webkit-details-marker { display:none; }
details.analysis-mt summary::before { content:"▸ "; margin-right:4px; }
details.analysis-mt[open] summary::before { content:"▾ "; }
details.analysis-mt summary:hover { background:var(--accent-soft2); }
details.analysis-mt .analysis-sec { margin-top:8px; }
.mt-section { color:var(--accent); font-weight:700; font-size:13.5px;
  display:inline-block; padding:2px 0; margin-top:4px; }
/* Snippet-highlight search panel (SV dashboard pattern). Shown only
   when a query is active; clicking a snippet jumps to its source card. */
.snippets { display:flex; flex-direction:column; gap:8px; margin:12px 0 24px; }
.snippet { background:var(--card); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px;
  padding:10px 14px; cursor:pointer; transition:background 0.1s,
  border-color 0.1s; }
.snippet:hover { background:var(--accent-soft); border-left-color:var(--accent); }
.snippet-meta { display:flex; gap:10px; font-size:11px; color:var(--muted);
  margin-bottom:4px; align-items:center; }
.snippet-sec { background:var(--tier-l-bg); color:var(--tier-l-fg);
  padding:2px 8px; border-radius:4px; font-weight:600; }
.snippet-card { font-family:'IBM Plex Mono',monospace; }
.snippet-text { color:var(--text); font-size:13px; line-height:1.55;
  white-space:pre-wrap; word-break:break-word; }
mark { background:var(--mark-bg); color:var(--mark-fg); padding:1px 3px;
  border-radius:3px; font-weight:600; }
/* In-body highlight when a snippet is clicked — stronger than the
   snippet-panel mark so the user's eye finds the match after the
   smooth-scroll lands. Pulses briefly then settles. */
mark.snippet-target { background:var(--mark-target-bg); color:var(--mark-target-fg);
  box-shadow:0 0 0 2px rgba(245,158,11,0.4); animation:markPulse 1.8s ease-out; }
@keyframes markPulse {
  0%   { background:rgba(245,158,11,0.95); box-shadow:0 0 0 4px rgba(245,158,11,0.6); }
  100% { background:var(--mark-target-bg); box-shadow:0 0 0 2px rgba(245,158,11,0.4); }
}
/* Brief pulse when a snippet click scrolls to a card — fades after 2s
   so the user immediately sees which card the match came from. */
@keyframes hitFlash {
  0%   { box-shadow:0 0 0 2px rgba(245,158,11,0.7); }
  60%  { box-shadow:0 0 0 2px rgba(245,158,11,0.35); }
  100% { box-shadow:0 0 0 0 rgba(245,158,11,0); }
}
details.card.hit-flash { animation:hitFlash 2.4s ease-out; }
</style></head><body>
"""
)

ARCHIVE_JS = """
<script>
// Snippet-highlight search + 🗑️ delete (mirrors Bottleneck Screener UX).
// Reuses scr-* element ids + .card/.day classes from _SCREENER_CSS. Daily
// Byte cards carry a single 'brief' section, so the search indexes brief
// lines; clicking a snippet opens + scrolls to the source card.
(function() {
  const inp = document.getElementById('scr-search');
  const clr = document.getElementById('scr-clear');
  const sts = document.getElementById('scr-status');
  const emp = document.getElementById('scr-empty');
  const snp = document.getElementById('scr-snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const dayGroups = Array.from(document.querySelectorAll('details.day'));
  const monthGroups = Array.from(document.querySelectorAll('details.month'));
  if (!inp) return;
  const total = cards.length;
  const MAX_SNIPPETS = 80;

  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const t = c.querySelector('.domain');
    const titleTxt = t ? t.textContent.trim() : '';
    if (titleTxt) lines.unshift({sec: 'title', txt: titleTxt});
    return {card: c, lines: lines};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function highlight(text, q) {
    const safe = escapeHtml(text);
    if (!q) return safe;
    const lt = text.toLowerCase(), lq = q.toLowerCase();
    let out = '', last = 0, idx;
    while ((idx = lt.indexOf(lq, last)) >= 0) {
      out += escapeHtml(text.slice(last, idx));
      out += '<mark>' + escapeHtml(text.slice(idx, idx + q.length)) + '</mark>';
      last = idx + q.length;
    }
    out += escapeHtml(text.slice(last));
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none'; snp.innerHTML = ''; emp.style.display = 'none';
    for (const c of cards) { c.style.display = ''; c.open = (c.dataset.defaultOpen === 'true'); c.classList.remove('hit-flash'); }
    for (const d of dayGroups) d.style.display = '';
    for (const m of monthGroups) m.style.display = '';
    sts.textContent = '총 ' + total + '건의 Daily Byte 브리프';
  }

  function showSnippetsMode(q) {
    for (const c of cards) c.style.display = 'none';
    for (const d of dayGroups) d.style.display = 'none';
    for (const m of monthGroups) m.style.display = 'none';
    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }
    if (hits.length === 0) {
      snp.style.display = 'none'; emp.style.display = 'block';
      sts.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emp.style.display = 'none';
    const counts = new Map(); const parts = [];
    for (const h of hits) {
      const cid = h.card.id; counts.set(cid, (counts.get(cid) || 0) + 1);
      const dateAttr = h.card.dataset.date || '';
      const tEl = h.card.querySelector('.domain');
      const tTxt = tEl ? tEl.textContent.trim() : '';
      parts.push('<div class="snippet" data-target="' + cid + '">' +
        '<div class="snippet-meta"><span class="snippet-card">' +
        escapeHtml(tTxt) + ' · ' + escapeHtml(dateAttr) + '</span></div>' +
        '<div class="snippet-text">' + highlight(h.txt, q) + '</div></div>');
    }
    snp.innerHTML = parts.join(''); snp.style.display = 'block';
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건)' : '';
    sts.textContent = hits.length + '개 라인 · ' + counts.size + '개 카드 매칭' + cap + ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const q = (inp.value || '').trim();
    if (!q) { showCardsMode(); return; }
    showSnippetsMode(q);
  }

  snp.addEventListener('click', function(ev) {
    const sn = ev.target.closest('.snippet'); if (!sn) return;
    const tgt = sn.dataset.target; const card = document.getElementById(tgt); if (!card) return;
    for (const c of cards) { c.style.display = ''; c.open = false; c.classList.remove('hit-flash'); }
    for (const d of dayGroups) { d.style.display = ''; d.open = false; }
    for (const m of monthGroups) { m.style.display = ''; m.open = false; }
    const monthG = card.closest('details.month'); if (monthG) monthG.open = true;
    const dayG = card.closest('details.day'); if (dayG) dayG.open = true;
    card.open = true; snp.style.display = 'none';
    card.classList.add('hit-flash');
    const tgtEl = card.querySelector('.card-h') || card;
    setTimeout(function() { tgtEl.scrollIntoView({behavior: 'smooth', block: 'center'}); }, 50);
    setTimeout(function() { card.classList.remove('hit-flash'); }, 2400);
  });

  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() { inp.value = ''; showCardsMode(); inp.focus(); });
})();

document.querySelectorAll('.del-btn').forEach(function(btn) {
  btn.addEventListener('click', function(ev) {
    ev.stopPropagation(); ev.preventDefault();
    const card = btn.closest('.card'); if (!card) return;
    const date = card.dataset.date; const filename = card.dataset.filename;
    if (!date || !filename) return;
    if (!confirm('📰 ' + date + ' / ' + filename + ' Daily Byte 기록을 삭제할까요?')) return;
    btn.disabled = true; btn.textContent = '⏳';
    fetch('api/daily_byte_delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: date, filename: filename})
    }).then(function(r) { return r.json().then(function(d) { return {status: r.status, body: d}; }); })
      .then(function(res) {
        if (res.status === 200 && res.body && res.body.ok) {
          card.style.transition = 'opacity 0.2s'; card.style.opacity = '0';
          setTimeout(function() { card.remove(); }, 200);
        } else {
          alert('삭제 실패: ' + (res.body && res.body.error || res.status));
          btn.disabled = false; btn.textContent = '🗑️';
        }
      }).catch(function(err) {
        alert('삭제 실패: ' + err); btn.disabled = false; btn.textContent = '🗑️';
      });
  });
});
</script>
</body></html>
"""
