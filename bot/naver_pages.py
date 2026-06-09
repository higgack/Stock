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
.up{color:var(--pos);font-weight:600}.dn{color:var(--neg);font-weight:600}.neu{color:var(--muted)}
.empty{color:var(--muted);font-size:13px;padding:30px 0;text-align:center}
.ts{color:var(--muted);font-size:12px;margin-left:8px}
</style>
"""

_THEME_SCRIPT = """
<script>
(function(){var h=parseInt(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}).format(new Date()),10)%24;document.documentElement.dataset.theme=(h>=19||h<7)?'dark':'light';})();
</script>
"""


def _shell(title: str, sub: str, active: str, body: str) -> str:
    def _t(key: str, label: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a{cls} href="{key}">{label}</a>'
    toggle = ('<div class="toggle">'
              + _t("theme", "🎯 테마별 시세")
              + _t("highlow", "📈 상한가·하한가")
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
        rows = []
        for i, t in enumerate(themes, 1):
            no = _html.escape(str(t.get("no", "")))
            nm = _html.escape(t.get("name", ""))
            name_cell = (f'<a href="{_THEME_DETAIL}{no}" target="_blank" '
                         f'rel="noopener" class="tnm">{nm}</a>' if no else nm)
            leaders = " · ".join(_html.escape(s) for s in t.get("leaders", []))
            rows.append(
                f'<tr><td class="rk">{i}</td><td class="nm">{name_cell}</td>'
                f'{_pct_cell(t.get("pct"))}{_pct_cell(t.get("pct3"))}'
                f'<td class="ld">{leaders or "—"}</td></tr>')
        body = (f'<div class="panel"><h2>전체 테마 {len(themes)}개 '
                f'<span class="ts">{ts} 기준</span></h2>'
                f'<table><thead><tr><th>#</th><th>테마</th>'
                f'<th style="text-align:right">등락률</th>'
                f'<th style="text-align:right">최근3일</th><th>주도주</th>'
                f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    return _shell("테마별 시세",
                  "Naver 증권 테마별 등락률 · 상승순. 테마명 클릭 시 네이버 상세. 4분 캐시.",
                  "theme", body)


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

    def _panel(title: str, items: list) -> str:
        if not items:
            return (f'<div class="panel"><h2>{title}</h2>'
                    '<div class="empty">해당 종목 없음</div></div>')
        rows = "".join(
            f'<tr><td class="rk">{i}</td>'
            f'<td class="nm"><a href="lookup/{_html.escape(_ticker(it.get("code","")))}">'
            f'{_html.escape(it.get("name",""))}</a></td>'
            f'<td class="num">{_html.escape(str(it.get("price") or "—"))}</td>'
            f'{_pct_cell(it.get("pct"))}</tr>'
            for i, it in enumerate(items, 1))
        return (f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
                f'<table><thead><tr><th>#</th><th>종목</th>'
                f'<th style="text-align:right">현재가</th>'
                f'<th style="text-align:right">등락률</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')

    up, low = data.get("upper", []), data.get("lower", [])
    if not up and not low:
        body = ('<div class="empty">상한가·하한가 데이터를 불러올 수 없습니다.<br>'
                '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        body = ('<div class="grid">'
                + _panel("🔺 상한가", up) + _panel("🔻 하한가", low) + '</div>')
    sub = f"Naver 증권 상한가·하한가. 4분 캐시. {('· ' + ts + ' 기준') if ts else ''}"
    return _shell("상한가·하한가", sub, "highlow", body)
