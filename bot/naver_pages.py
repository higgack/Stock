"""테마별 시세 · 신고가/신저가 별도 페이지 (Naver 소스).

market.html '업종 등락 TOP 10' 옆 링크 → 서버 사이드 렌더(실적 캘린더 패턴).
무료·무키·graceful(데이터 없으면 안내 문구). 색=Western(상승 초록/하락 빨강).
"""
from __future__ import annotations

import html as _html
import logging

from bot.live_refresh import LIVE_REFRESH_JS as _LIVE_REFRESH_JS

log = logging.getLogger("bot.naver_pages")

_CSS = """
<style>
:root,[data-theme="dark"]{--bg:#0e1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
--muted:#8b949e;--accent:#58a6ff;--pos:#26a69a;--neg:#e2574c}
[data-theme="light"]{--bg:#fff;--card:#f6f8fa;--border:#d0d7de;--text:#1f2328;
--muted:#656d76;--accent:#0969da;--pos:#059669;--neg:#dc2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--text);padding:24px;max-width:1000px;margin:0 auto}
a.back-link{color:var(--muted);font-size:13px;text-decoration:none}
a.back-link:hover{color:var(--accent)}
h1{font-size:26px;font-weight:700;margin:6px 0 4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.toggle{display:flex;gap:8px;margin-bottom:18px}
.toggle a{background:var(--card);border:1px solid var(--border);border-radius:20px;
padding:6px 14px;color:var(--text);font-size:13px;text-decoration:none}
.toggle a.active{background:var(--text);color:var(--bg);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:720px){.grid{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.panel h2{font-size:15px;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-size:12px;font-weight:600;padding:6px 8px;
border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
td.rk{color:var(--muted);width:26px}
td.pct,td.num{text-align:right;white-space:nowrap}
td.nm a{color:var(--text);text-decoration:none}
td.nm a:hover{color:var(--accent);text-decoration:underline}
td.nm a.tnm{text-decoration:underline;text-decoration-color:var(--muted)}
td.ld{color:var(--muted);font-size:12px}
/* 주도주 링크 — 기본 파랑이 저대비(사용자 2026-06-10) → 차분한 파랑 + 약한 밑줄 */
td.ld a,td.ld a.tnm{color:#6ea8fe;text-decoration:underline;text-decoration-color:var(--muted)}
[data-theme="light"] td.ld a,[data-theme="light"] td.ld a.tnm{color:#1d6fe0}
td.ld a:hover{color:var(--accent)}
.up{color:var(--pos);font-weight:600}.dn{color:var(--neg);font-weight:600}.neu{color:var(--muted)}
.empty{color:var(--muted);font-size:13px;padding:30px 0;text-align:center}
.ts{color:var(--muted);font-size:12px;margin-left:8px}
</style>
"""

_THEME_SCRIPT = r"""
<script>
(function(){var h=parseInt(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}).format(new Date()),10)%24;document.documentElement.dataset.theme=(h>=19||h<7)?'dark':'light';})();
/* '← 홈으로' = 뒤로가기 (사용자 2026-06-15 재요청 — 06-11 직행 정책 반전):
   같은 사이트에서 왔으면 history.back() 으로 들어왔던 화면을 스크롤째 복원
   (응답이 no-store 아니라 bfcache 동작 → 새로고침 아님). 직접 진입/외부 유입은
   href 로 홈 직행. 모든 홈으로 버튼·대쉬보드 공통. */
document.addEventListener('click',function(e){
  var a=e.target.closest&&e.target.closest('a.back-link'); if(!a) return;
  try{var r=document.referrer;
    if(r&&new URL(r).origin===location.origin&&history.length>1){e.preventDefault();history.back();}
  }catch(_){}
});
</script>
"""


def _shell(title: str, sub: str, active: str, body: str) -> str:
    def _t(key: str, label: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a{cls} href="{key}">{label}</a>'
    # 자식 링크 순서·명칭 통일(사용자 2026-06-13): 업종별 시세(전체) →
    # 신고가·신저가(kr52) → 상한가·하한가. kr52 는 intl_pages(_tw_shell)
    # 렌더라 이 toggle 에선 active 안 됨(일반 링크).
    toggle = ('<div class="toggle">'
              + _t("theme", "🏭 업종별 시세(전체)")
              + _t("kr52", "📈 신고가·신저가")
              + _t("highlow", "🚀 급등·급락")
              + _t("nxt", "🌙 NXT 장전·장후")
              + '</div>')
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)} — NOAH</title>{_CSS}</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<h1>{_html.escape(title)}</h1>
<div class="sub">{sub}</div>
{toggle}
<div id="live-root">
{body}
</div>
{_THEME_SCRIPT}
{_LIVE_REFRESH_JS}
</body></html>"""


def _pct_cell(pct) -> str:
    if pct is None:
        return '<td class="pct neu">—</td>'
    cls = "up" if pct > 0 else "dn" if pct < 0 else "neu"
    sign = "+" if pct > 0 else ""
    return f'<td class="pct {cls}">{sign}{pct:.2f}%</td>'


def _fmt_vol(v) -> str:
    """거래량 표기 — 만/억(사용자 2026-06-13, Naver 급등 표 스타일). 신고저·
    급등급락 표 공용. 0/None/비수치 → '—'."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if x <= 0:
        return "—"
    if x >= 1e8:
        return f"{x / 1e8:.1f}억"
    if x >= 1e4:
        return f"{x / 1e4:,.0f}만"
    return f"{x:,.0f}"


_THEME_DETAIL = ("https://finance.naver.com/sise/sise_group_detail.naver"
                 "?type=theme&no=")


def render_theme_page() -> str:
    """테마별 시세 — 전체 테마. 테마명=네이버 상세 링크, 최근3일·주도주 포함."""
    try:
        from bot.naver_sector_client import fetch_themes
        data = fetch_themes()
    except Exception as exc:
        log.warning("theme page fetch failed: %s", exc)
        data = {"themes": [], "ts": ""}
    themes = data.get("themes", [])
    ts = _html.escape(data.get("ts", ""))
    if not themes:
        body = '<div class="empty">테마 시세를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.</div>'
    else:
        def _ld_link(ld) -> str:
            # 주도주 종목명 → 우리 종목분석(lookup). 코드 정규화(.KS/.KQ).
            nm = _html.escape(ld.get("name", "") if isinstance(ld, dict) else str(ld))
            code = ld.get("code", "") if isinstance(ld, dict) else ""
            if not code:
                return nm
            try:
                from bot.market import normalize_kr_ticker_suffix
                tk = normalize_kr_ticker_suffix(f"{code}.KS")
            except Exception:
                tk = f"{code}.KS"
            return f'<a href="lookup/{_html.escape(tk)}" class="tnm">{nm}</a>'

        rows = []
        for i, t in enumerate(themes, 1):
            no = _html.escape(str(t.get("no", "")))
            nm = _html.escape(t.get("name", ""))
            name_cell = (f'<a href="{_THEME_DETAIL}{no}" target="_blank" '
                         f'rel="noopener" class="tnm">{nm}</a>' if no else nm)
            leaders = " · ".join(_ld_link(s) for s in t.get("leaders", []))
            pct = t.get("pct")
            pct3 = t.get("pct3")
            da = (f'data-name="{nm}" data-pct="{pct if pct is not None else -999}" '
                  f'data-pct3="{pct3 if pct3 is not None else -999}"')
            rows.append(
                f'<tr {da}><td class="rk">{i}</td><td class="nm">{name_cell}</td>'
                f'{_pct_cell(pct)}{_pct_cell(pct3)}'
                f'<td class="ld">{leaders or "—"}</td></tr>')
        body = (f'<div class="panel"><h2>전체 테마 {len(themes)}개 '
                f'<span class="ts">{ts} 기준 · 헤더 클릭 정렬</span></h2>'
                f'<table id="thm-tbl"><thead><tr><th>#</th>'
                f'<th class="th-sort" data-k="name">테마</th>'
                f'<th class="th-sort" data-k="pct" style="text-align:right">등락률</th>'
                f'<th class="th-sort" data-k="pct3" style="text-align:right">최근3일</th>'
                f'<th>주도주</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>{_THEME_SORT_JS}')
    return _shell("테마별 시세",
                  "Naver 증권 테마별 등락률 · 상승순. 테마명·주도주 클릭 시 상세/종목분석. 장중 30초 캐시.",
                  "theme", body)


_THEME_SORT_JS = """<script>
(function(){
var tbl=document.getElementById('thm-tbl');if(!tbl)return;
var dir={};
tbl.querySelectorAll('th.th-sort').forEach(function(th){
  th.style.cursor='pointer';
  th.addEventListener('click',function(){
    var k=th.dataset.k;var d=dir[k]=-(dir[k]||1);
    var tb=tbl.tBodies[0];var rows=[].slice.call(tb.rows);
    rows.sort(function(a,b){
      var av=a.dataset[k],bv=b.dataset[k];
      if(k==='name')return d*av.localeCompare(bv,'ko');
      return d*(parseFloat(av)-parseFloat(bv));
    });
    rows.forEach(function(r,i){tb.appendChild(r);r.cells[0].textContent=i+1;});
  });
});
})();
</script>"""


def render_highlow_page() -> str:
    """KR 급등·급락 — 네이버 front-api domestic top 상승/하락 (사용자 2026-06-14
    'KR 상한가/하한가 → 급등/급락', JP/CN/HK 무버 형태). 한글명·시총·거래대금
    native·429 면역. 장중 30초(무버 신선도)·장 밖 재스캔 0."""
    from bot.highlow_render import (HL_SORT_JS, ind_dist_line, sort_by_pct,
                                    stock_panel)
    data = None
    try:
        from bot.finviz_client import (_CACHE_DIR, _MOVERS_INTRA_TTL, _cache_write,
                                       _cached, _session_fresh)
        from bot.naver_ranking_client import fetch_kr_movers
        _cf = "kr_movers_v1.json"
        stale = _cached(_cf, ttl=86400)
        fresh = False
        if stale is not None:
            try:
                _mt = (_CACHE_DIR / _cf).stat().st_mtime
            except OSError:
                _mt = 0.0
            fresh = _session_fresh("KR", _mt, _MOVERS_INTRA_TTL)
        if stale is not None and fresh:
            nv = stale
        else:
            nv = fetch_kr_movers()
            if nv.get("up") or nv.get("down"):
                _cache_write(_cf, nv)
            elif stale is not None:
                nv = stale       # fetch 빈/실패 → 직전 산출본 유지(블랭크 방지)
        if nv and (nv.get("up") or nv.get("down")):
            data = nv
    except Exception as exc:
        log.warning("naver KR movers: %s", exc)

    if data is not None:
        up = sort_by_pct(data["up"], gainers=True)      # 기본 등락률순(사용자 2026-06-15)
        down = sort_by_pct(data["down"], gainers=False)
        # KR 업종(한글) 백필 — 네이버 업종 그룹 멤버맵 (사용자 2026-06-14 'KR
        # 급등락에 업종 추가, 그냥 한글로'). SWR·graceful(빌드 중이면 —).
        try:
            from bot.naver_sector_client import apply_kr_industry
            apply_kr_industry(up)
            apply_kr_industry(down)
        except Exception:
            pass
        ts = _html.escape(data.get("ts", ""))
        _o = dict(name_only=True, show_ind=True, show_vol=True, show_value=True)
        body = ('<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", "KR",
                              ind_dist_line(up), **_o)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", "KR",
                              ind_dist_line(down), **_o)
                + '</div>' + HL_SORT_JS)
        sub = ("네이버 증권 급등/급락 · 등락률순·헤더 클릭 정렬 · 업종=네이버 · "
               f"장중 30초{(' · ' + ts + ' 기준') if ts else ''}")
        return _shell("급등·급락", sub, "highlow", body)

    body = ('<div class="empty">급등·급락 데이터를 불러올 수 없습니다.<br>'
            '(잠시 후 다시 시도해 주세요.)</div>')
    return _shell("급등·급락", "네이버 증권 급등/급락", "highlow", body)
