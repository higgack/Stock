"""테마별 시세 · 신고가/신저가 별도 페이지 (Naver 소스).

market.html '업종 등락 TOP 10' 옆 링크 → 서버 사이드 렌더(실적 캘린더 패턴).
무료·무키·graceful(데이터 없으면 안내 문구). 색=Western(상승 초록/하락 빨강).
"""
from __future__ import annotations

import html as _html
import logging

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
/* '홈으로 = 뒤로가기' 가로채기는 제거(사용자 2026-06-11) — 테마→상한가
   다단 이동 후 홈 클릭 시 직전 탭으로 가던 오작동. 홈은 항상 메인 홈. */
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
              + _t("highlow", "🔺 상한가·하한가")
              + '</div>')
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)} — NOAH</title>{_CSS}</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<h1>{_html.escape(title)}</h1>
<div class="sub">{sub}</div>
{toggle}
{body}
{_THEME_SCRIPT}
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
                  "Naver 증권 테마별 등락률 · 상승순. 테마명·주도주 클릭 시 상세/종목분석. 4분 캐시.",
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
    """상한가·하한가 (Naver sise_upper/lower). 신고가 페이지가 불안정해 대체."""
    try:
        from bot.naver_sector_client import fetch_upper_lower
        data = fetch_upper_lower()
    except Exception as exc:
        log.warning("upper/lower page fetch failed: %s", exc)
        data = {"upper": [], "lower": [], "ts": ""}
    ts = _html.escape(data.get("ts", ""))

    def _ticker(code: str) -> str:
        # 종목분석(lookup) 링크용 — KOSPI .KS / KOSDAQ .KQ 정규화(pykrx 없으면 .KS)
        if not code:
            return ""
        try:
            from bot.market import normalize_kr_ticker_suffix
            return normalize_kr_ticker_suffix(f"{code}.KS")
        except Exception:
            return f"{code}.KS"

    def _prep(lst):
        # Naver 항목 → stock_panel 형식 (ticker·numeric price). 시총은 enrich.
        out = []
        for it in lst:
            p = it.get("price")
            try:
                pn = float(str(p).replace(",", "")) if p else None
            except (TypeError, ValueError):
                pn = None
            out.append({"ticker": _ticker(it.get("code", "")),
                        "name": it.get("name", ""), "price": pn,
                        "pct": it.get("pct"), "vol": it.get("vol")})
        return out

    up, low = _prep(data.get("upper", [])), _prep(data.get("lower", []))
    if not up and not low:
        body = ('<div class="empty">상한가·하한가 데이터를 불러올 수 없습니다.<br>'
                '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        # 미국 포맷 통일 — 종목명만·거래량·시총(사용자 2026-06-13 시총 추가) +
        # 통화 헤더. 시총은 enrich_for_panel(yfinance mcap·10분 캐시).
        from bot.highlow_render import (HL_SORT_JS, enrich_for_panel,
                                        sort_by_mcap, stock_panel)
        up = sort_by_mcap(enrich_for_panel(up, "KR"))
        low = sort_by_mcap(enrich_for_panel(low, "KR"))
        _o = dict(name_only=True, show_ind=False, show_vol=True)
        body = ('<div class="grid">'
                + stock_panel("🔺 상한가", up, "ul-up", "KR", **_o)
                + stock_panel("🔻 하한가", low, "ul-low", "KR", **_o)
                + '</div>' + HL_SORT_JS)
    sub = (f"Naver 증권 상한가·하한가 · 시총=yfinance(10분 캐시) · 시총순·헤더 "
           f"클릭 정렬. 4분 캐시. {('· ' + ts + ' 기준') if ts else ''}")
    return _shell("상한가·하한가", sub, "highlow", body)
