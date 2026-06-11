"""미국 업종별 시세 · 52주 신고가/신저가 별도 페이지 (Finviz 소스).

KR naver_pages(테마별 시세 · 상한가/하한가)의 미국 미러 — market.html
'미국 업종 등락 TOP 10' 옆 링크 → 서버 사이드 렌더. 미국엔 가격제한폭이
없으므로 상한가/하한가 대신 52주 신고가/신저가 (사용자 2026-06-10).
무료·무키·graceful. 종목 클릭 → 우리 종목분석(lookup)."""
from __future__ import annotations

import html as _html
import logging

from bot.naver_pages import _CSS, _THEME_SCRIPT, _pct_cell

log = logging.getLogger("bot.us_pages")


def _shell(title: str, sub: str, active: str, body: str) -> str:
    def _t(key: str, label: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a{cls} href="{key}">{label}</a>'
    toggle = ('<div class="toggle">'
              + _t("usindustry", "🏭 업종별 시세")
              + _t("ushighlow", "📈 신고가·신저가")
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


def render_us_industry_page() -> str:
    """미국 업종별 시세 — Finviz industry 전체(~140), 등락 내림차순."""
    try:
        from bot.finviz_client import fetch_groups
        data = fetch_groups()
    except Exception as exc:
        log.warning("us industry page fetch failed: %s", exc)
        data = {"groups": [], "ts": "", "source": ""}
    groups = data.get("groups", [])
    ts = _html.escape(data.get("ts", ""))
    src = _html.escape(data.get("source", "Finviz"))

    if not groups:
        body = ('<div class="empty">업종 데이터를 불러올 수 없습니다.<br>'
                '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        def _name_cell(g: dict) -> str:
            # 업종 클릭 → Finviz 해당 업종 종목 목록(사용자 2026-06-10, KR
            # 테마→Naver 상세 미러). slug 있는 행(Finviz 출처)만 링크, 폴백
            # (GICS 산출/ETF)은 슬러그 없어 일반 텍스트.
            nm = _html.escape(g.get("name", ""))
            slug = g.get("slug", "")
            if slug and slug.replace("ind_", "").replace("_", "").isalnum():
                url = f"https://finviz.com/screener?v=111&f={_html.escape(slug)}&o=-change"
                return (f'<a href="{url}" target="_blank" rel="noopener">{nm}</a>'
                        ' <span class="ts">↗</span>')
            return nm
        rows = "".join(
            f'<tr><td class="rk">{i}</td>'
            f'<td class="nm">{_name_cell(g)}</td>'
            f'{_pct_cell(g.get("pct"))}</tr>'
            for i, g in enumerate(groups, 1))
        _clickable = any(g.get("slug") for g in groups)
        _hint = " · 업종 클릭 → 종목 목록" if _clickable else ""
        body = (f'<div class="panel"><h2>🏭 업종별 등락 '
                f'<span class="ts">{len(groups)}개 · 등락 내림차순{_hint}</span></h2>'
                f'<table><thead><tr><th>#</th><th>업종</th>'
                f'<th style="text-align:right">등락률</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')
    sub = f"미국 업종(industry) 당일 등락 · 출처 {src} · 5분 캐시" + (f" · {ts} 기준" if ts else "")
    return _shell("미국 업종별 시세", sub, "usindustry", body)


def render_us_highlow_page() -> str:
    """미국 52주 신고가·신저가 — Finviz ta_newhigh/ta_newlow (전 미국 상장).
    폴백(S&P 500 산출) 시에도 동일 표. 종목 → 우리 종목분석(lookup)."""
    try:
        from bot.finviz_client import fetch_high_low
        data = fetch_high_low()
    except Exception as exc:
        log.warning("us high/low page fetch failed: %s", exc)
        data = {"high": [], "low": [], "ts": "", "source": ""}
    ts = _html.escape(data.get("ts", ""))
    src = _html.escape(data.get("source", "Finviz"))

    def _panel(title: str, items: list) -> str:
        if not items:
            return (f'<div class="panel"><h2>{title}</h2>'
                    '<div class="empty">해당 종목 없음</div></div>')
        def _name_cell(it: dict) -> str:
            tk = _html.escape(it.get("ticker", ""))
            nm = _html.escape(it.get("name") or it.get("ticker", ""))
            # 'KO(Coca-Cola)' 형식 — 티커 + (회사명) (사용자 2026-06-11).
            # 회사명==티커(이름 미확보)면 티커만.
            label = f'{tk}<span class="ts">({nm})</span>' if nm != tk else tk
            return f'<td class="nm"><a href="lookup/{tk}">{label}</a></td>'
        rows = "".join(
            f'<tr><td class="rk">{i}</td>'
            f'{_name_cell(it)}'
            f'<td class="num">{("$" + format(it["price"], ",.2f")) if it.get("price") is not None else "—"}</td>'
            f'{_pct_cell(it.get("pct"))}</tr>'
            for i, it in enumerate(items, 1))
        return (f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
                f'<table><thead><tr><th>#</th><th>종목</th>'
                f'<th style="text-align:right">현재가</th>'
                f'<th style="text-align:right">등락률</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')

    hi, lo = data.get("high", []), data.get("low", [])
    if not hi and not lo:
        body = ('<div class="empty">신고가·신저가 데이터를 불러올 수 없습니다.<br>'
                '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        body = ('<div class="grid">'
                + _panel("🔺 52주 신고가", hi) + _panel("🔻 52주 신저가", lo) + '</div>')
    sub = (f"미국 52주 신고가·신저가 (가격제한폭이 없는 시장 — 상한가/하한가 대응 지표) · "
           f"출처 {src} · 5분 캐시" + (f" · {ts} 기준" if ts else ""))
    return _shell("미국 신고가·신저가", sub, "ushighlow", body)
