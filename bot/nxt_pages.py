"""🇰🇷 NXT 장전·장후 외국인·기관 수급 대시보드 (nxt.html).

넥스트레이드(NXT) 연장거래의 투자자별 순매수/순매도 상위 — 네이버 trendForeignOrg
(사용자 2026-06-14). 외국인·기관 각각 순매수 Top20 / 순매도 Top20. 종목 → NOAH
분석(lookup). 무료·야후 무관. 데이터 없으면 안내."""
from __future__ import annotations

import html as _html
import logging

from bot.naver_pages import _CSS, _THEME_SCRIPT

log = logging.getLogger("bot.nxt_pages")


def _fmt_eok(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 10000:
        return f"{v / 10000:,.2f}조"
    return f"{v:,.0f}억"


def _fmt_qty(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.1f}억주"
    if a >= 1e4:
        return f"{v / 1e4:,.0f}만"
    return f"{v:,.0f}"


def _panel(title: str, rows: list, side: str) -> str:
    """순매수/순매도 패널. side='buy'(빨강 강조)|'sell'(파랑)."""
    head = ("종목 · 순매수금액 · 순매수량 · 현재가 · 등락 · 총거래량"
            if side == "buy" else
            "종목 · 순매도금액 · 순매도량 · 현재가 · 등락 · 총거래량")
    trs = []
    for i, r in enumerate(rows, 1):
        code = _html.escape(str(r.get("code") or ""))
        name = _html.escape(str(r.get("name") or code))
        href = f"lookup/{code}.KS"     # 분석가 suffix 정규화가 .KS↔.KQ 교정
        pct = r.get("pct")
        pcls = "up" if (pct or 0) > 0 else "dn" if (pct or 0) < 0 else "neu"
        psign = "+" if (pct or 0) > 0 else ""
        pct_td = (f'<td class="{pcls}">{psign}{pct:.2f}%</td>'
                  if isinstance(pct, (int, float)) else '<td class="neu">—</td>')
        amt = abs(r["net_amt"]) if r.get("net_amt") is not None else None
        qty = abs(r["net_qty"]) if r.get("net_qty") is not None else None
        price = r.get("price")
        price_td = (f'<td class="pr">{price:,.0f}</td>'
                    if isinstance(price, (int, float)) else '<td class="pr">—</td>')
        trs.append(
            f'<tr><td class="rk">{i}</td>'
            f'<td><a href="{href}">{name}</a></td>'
            f'<td class="amt {side}">{_fmt_eok(amt)}</td>'
            f'<td class="q">{_fmt_qty(qty)}</td>'
            f'{price_td}{pct_td}'
            f'<td class="q">{_fmt_qty(r.get("volume"))}</td></tr>')
    body = "".join(trs) or '<tr><td colspan="7" class="empty">—</td></tr>'
    return (f'<div class="nxt-panel"><div class="nxt-hd {side}">{title}</div>'
            f'<div class="nxt-tw"><table class="nxt-tbl"><thead><tr><th>#</th><th>종목</th>'
            f'<th>순매{"수" if side == "buy" else "도"}금액</th><th>순매{"수" if side == "buy" else "도"}량</th>'
            f'<th>현재가</th><th>등락</th><th>총거래량</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div></div>')


def _investor_block(label: str, data: dict | None) -> str:
    if not data or (not data.get("buy") and not data.get("sell")):
        return (f'<h2 class="nxt-inv">{label}</h2>'
                '<div class="empty">데이터 없음 (NXT 연장거래 시간 외이거나 '
                '미제공).</div>')
    return (f'<h2 class="nxt-inv">{label}</h2><div class="nxt-grid">'
            + _panel("🔴 순매수 상위", data.get("buy", []), "buy")
            + _panel("🔵 순매도 상위", data.get("sell", []), "sell")
            + '</div>')


_NXT_CSS = """
<style>
.nxt-inv{font-size:17px;margin:22px 0 8px;color:var(--text,#e8eaed)}
.nxt-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:860px){.nxt-grid{grid-template-columns:1fr}}
.nxt-panel{background:var(--card,#1c1f26);border:1px solid var(--border,#2a2e37);border-radius:10px;overflow:hidden}
.nxt-tw{overflow-x:auto}
.nxt-hd{padding:9px 12px;font-weight:700;font-size:14px}
.nxt-hd.buy{color:#ef5350}.nxt-hd.sell{color:#42a5f5}
.nxt-tbl{width:100%;border-collapse:collapse;font-size:11.5px}
.nxt-tbl th{padding:4px 6px;text-align:right;color:var(--muted,#9aa0aa);font-weight:500;border-bottom:1px solid var(--border,#2a2e37);white-space:nowrap}
.nxt-tbl th:nth-child(2){text-align:left}
.nxt-tbl td{padding:4px 6px;text-align:right;border-bottom:1px solid var(--border,#23262e);white-space:nowrap}
.nxt-tbl td:nth-child(2){text-align:left;max-width:100px;overflow:hidden;text-overflow:ellipsis}
.nxt-tbl td a{color:var(--text,#e8eaed);font-weight:600;text-decoration:none}
.nxt-tbl td a:hover{color:var(--accent,#3b82f6);text-decoration:underline}
.nxt-tbl .rk{color:var(--muted,#9aa0aa)}
.nxt-tbl .amt.buy{color:#ef5350;font-weight:600}.nxt-tbl .amt.sell{color:#42a5f5;font-weight:600}
.nxt-tbl .up{color:#ef5350}.nxt-tbl .dn{color:#42a5f5}.nxt-tbl .neu{color:var(--muted,#9aa0aa)}
.nxt-tbl .empty{text-align:center;color:var(--muted,#9aa0aa)}
</style>"""


def render_nxt_page() -> str:
    """NXT 외국인·기관 순매수/순매도 → nxt.html."""
    try:
        from bot.nxt_client import fetch_nxt_foreign, fetch_nxt_organ
        foreign = fetch_nxt_foreign()
        organ = fetch_nxt_organ()
    except Exception as exc:
        log.warning("nxt page fetch failed: %s", exc)
        foreign = organ = None
    date = ""
    for d in (foreign, organ):
        if d and d.get("date"):
            date = d["date"]
            break
    date_lb = (f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date)
    sub = ("넥스트레이드(NXT) 연장거래 투자자별 순매수/순매도 상위 · 장전 "
           "08:00~09:00 · 장후 15:30~20:00 KST · 네이버"
           + (f" · {date_lb} 기준" if date_lb else ""))
    body = (_investor_block("🌐 외국인", foreign)
            + _investor_block("🏦 기관", organ))
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NXT 외국인·기관 수급 — NOAH</title>{_CSS}{_NXT_CSS}</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<h1>🇰🇷 NXT 장전·장후 외국인·기관 수급</h1>
<div class="sub">{_html.escape(sub)}</div>
{body}
{_THEME_SCRIPT}
</body></html>"""
