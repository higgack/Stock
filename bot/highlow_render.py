"""신고가·신저가 / 급등·급락 / 상한가·하한가 공용 리치 렌더러 (사용자 2026-06-13
'모든 나라 미국 포맷 통일'). 미국 us_pages._stock_panel 을 전 시장 일반화:
종목(+이름)·현재가·등락률·거래량·시총·업종 + 시총 정렬 기본 + 헤더 클릭 정렬 +
업종 분포 한 줄. 통화/시총 단위는 시장별(₩/¥/NT$/HK$/$, 억/조 vs $T/$B/$M).

데이터 항목 스키마(시장 무관): {ticker, name, price, pct, vol, mcap, ind}.
- mcap 단위: US=억$(USD, Finviz), 그 외=현지통화 raw(yfinance marketCap).
- name: US=영문 longName, JP/TW/CN/HK=한글 번역(chart_translate), KR=종목명.
순수 렌더(네트워크 0) — 백필은 호출측(finviz_client._compute_highlow_from 등).
"""
from __future__ import annotations

import html as _html

from bot.naver_pages import _fmt_vol, _pct_cell

# 시장 → (통화기호, 가격 소수자릿수). CJK 통화는 정수 표기가 자연스러움.
_CUR = {"US": ("$", 2), "KR": ("₩", 0), "JP": ("¥", 0),
        "TW": ("NT$", 2), "CN_A": ("¥", 2), "HK": ("HK$", 2)}

# 정렬/업종 셀 스타일 + 헤더 클릭 정렬 (us_pages 동일 — 단일 소스로 이관).
HL_SORT_JS = """
<style>
.hl-table th.srt{cursor:pointer;user-select:none;white-space:nowrap}
.hl-table td.ind{max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted,#888);font-size:12px}
.hl-table th.srt:hover{color:var(--accent,#3b82f6)}
.hl-table th.srt .arw{opacity:.45;font-size:10px;margin-left:2px}
.hl-table th.srt.on .arw{opacity:1}
</style>
<script>
(function(){
  function sortTable(tbl, key, type, asc){
    var tb=tbl.tBodies[0];
    var rows=Array.prototype.slice.call(tb.rows);
    rows.sort(function(a,b){
      var x=a.getAttribute('data-'+key), y=b.getAttribute('data-'+key);
      if(type==='num'){ x=parseFloat(x); y=parseFloat(y);
        if(isNaN(x))x=-Infinity; if(isNaN(y))y=-Infinity; return asc?x-y:y-x; }
      x=(x||'').toString(); y=(y||'').toString();
      return asc? x.localeCompare(y): y.localeCompare(x);
    });
    rows.forEach(function(r,i){ tb.appendChild(r);
      var rk=r.querySelector('.rk'); if(rk) rk.textContent=i+1; });
  }
  document.querySelectorAll('table.hl-table').forEach(function(tbl){
    tbl.querySelectorAll('th.srt').forEach(function(th){
      var arw=document.createElement('span'); arw.className='arw'; arw.textContent='⇅';
      th.appendChild(arw);
      th.addEventListener('click', function(){
        var key=th.getAttribute('data-key'), type=th.getAttribute('data-type');
        var asc = th.classList.contains('on') ? !th._asc : (type!=='num');
        tbl.querySelectorAll('th.srt').forEach(function(o){
          o.classList.remove('on'); var a=o.querySelector('.arw'); if(a)a.textContent='⇅'; });
        th.classList.add('on'); th._asc=asc; arw.textContent=asc?'▲':'▼';
        sortTable(tbl, key, type, asc);
      });
    });
  });
})();
</script>
"""


def _fmt_price(price, market: str, with_sym: bool = True) -> str:
    if price is None:
        return "—"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return _html.escape(str(price))
    sym, dec = _CUR.get(market, ("", 2))
    return f"{sym if with_sym else ''}{p:,.{dec}f}"


def fmt_mcap(mcap, market: str, with_sym: bool = True) -> str:
    """시총 표기. 입력 mcap = **억 단위**(현지통화 또는 USD) — finviz_client.
    _compute_highlow_from 이 yfinance marketCap / 1e8 로 저장(전 시장 공통 규약).
    US=억$→$T/$B/$M, 그 외=억(현지)→통화기호+억/조(10000억=1조).
    with_sym=False 면 통화기호 생략(헤더에만 표기 — 사용자 2026-06-13)."""
    if not mcap:
        return "—"
    try:
        v = float(mcap)            # 억 단위
    except (TypeError, ValueError):
        return "—"
    if v <= 0:
        return "—"
    if market == "US":
        s = "$" if with_sym else ""
        if v >= 10000:             # 10000억$ = $1T
            return f"{s}{v / 10000:.2f}T"
        if v >= 10:                # 10억$ = $1B
            return f"{s}{v / 10:.1f}B"
        return f"{s}{v * 100:.0f}M"
    sym = _CUR.get(market, ("", 2))[0] if with_sym else ""
    if v >= 10000:                 # 10000억 = 1조
        return f"{sym}{v / 10000:.1f}조"
    return f"{sym}{v:,.0f}억"


def ind_dist_line(items: list, top_k: int = 5) -> str:
    """패널 상단 업종 분포 한 줄 — 'Biotechnology 6 · 반도체 4 …' (순수)."""
    from collections import Counter
    cnt = Counter(str(it.get("ind")) for it in items if it.get("ind"))
    if not cnt:
        return ""
    parts = [f"{_html.escape(name)} {n}" for name, n in cnt.most_common(top_k)]
    extra = " 외" if len(cnt) > top_k else ""
    return (f'<div class="ind-dist" style="color:var(--muted);font-size:12px;'
            f'margin:2px 0 8px">업종 분포: {" · ".join(parts)}{extra}</div>')


def stock_panel(title: str, items: list, tid: str, market: str,
                extra_head: str = "", name_only: bool = False,
                show_vol: bool = True, show_ind: bool = True,
                show_mcap: bool = True) -> str:
    """리치 종목 패널 — 종목·현재가·등락률·(거래량)·(시총)·(업종), 헤더 클릭
    정렬. **통화기호는 셀이 아닌 현재가/시총 헤더에만**(사용자 2026-06-13).
    플래그: name_only=종목명만(티커 생략·KR), show_vol/show_ind/show_mcap."""
    if not items:
        return (f'<div class="panel"><h2>{title}</h2>'
                '<div class="empty">해당 종목 없음</div></div>')
    sym = _CUR.get(market, ("", 2))[0]
    cur_h = f" ({sym})" if sym else ""

    def _row(i: int, it: dict) -> str:
        tk = _html.escape(str(it.get("ticker", "")))
        nm = _html.escape(it.get("name") or it.get("ticker", ""))
        if name_only:
            label = nm or tk
        else:
            label = f'{tk}<span class="ts">({nm})</span>' if nm and nm != tk else tk
        price, pct = it.get("price"), it.get("pct")
        vol, mcap = it.get("vol"), it.get("mcap")
        ind = _html.escape(str(it.get("ind") or ""))
        try:
            pnum = float(price) if price is not None else None
        except (TypeError, ValueError):
            pnum = None
        cells = [
            f'<td class="rk">{i}</td>',
            f'<td class="nm"><a href="lookup/{tk}">{label}</a></td>',
            f'<td class="num">{_fmt_price(price, market, with_sym=False)}</td>',
            _pct_cell(pct),
        ]
        if show_vol:
            cells.append(f'<td class="num">{_fmt_vol(vol)}</td>')
        if show_mcap:
            cells.append(f'<td class="num">{fmt_mcap(mcap, market, with_sym=False)}</td>')
        if show_ind:
            cells.append(f'<td class="ind" title="{ind}">{ind}</td>'
                         if ind else '<td class="ind">—</td>')
        data = (f'data-sym="{tk.lower()}" '
                f'data-price="{pnum if pnum is not None else -1}" '
                f'data-pct="{pct if pct is not None else -9999}" '
                f'data-vol="{vol if vol is not None else -1}" '
                f'data-mcap="{mcap if mcap is not None else -1}" '
                f'data-ind="{ind.lower()}"')
        return f'<tr {data}>' + "".join(cells) + '</tr>'
    rows = "".join(_row(i, it) for i, it in enumerate(items, 1))
    heads = ['<th>#</th>',
             '<th class="srt" data-key="sym" data-type="text">종목</th>',
             f'<th class="srt" data-key="price" data-type="num" style="text-align:right">현재가{cur_h}</th>',
             '<th class="srt" data-key="pct" data-type="num" style="text-align:right">등락률</th>']
    if show_vol:
        heads.append('<th class="srt" data-key="vol" data-type="num" style="text-align:right">거래량</th>')
    if show_mcap:
        heads.append(f'<th class="srt" data-key="mcap" data-type="num" style="text-align:right">시총{cur_h}</th>')
    if show_ind:
        heads.append('<th class="srt" data-key="ind" data-type="text">업종</th>')
    return (
        f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
        f'{extra_head}'
        f'<table class="hl-table" id="{tid}"><thead><tr>'
        + "".join(heads)
        + f'</tr></thead><tbody>{rows}</tbody></table></div>'
    )


def sort_by_mcap(items: list) -> list:
    """시총 내림차순(없으면 뒤로) — 사용자 '시총순위대로 표시'."""
    return sorted(items, key=lambda it: (it.get("mcap") is None,
                                         -(it.get("mcap") or 0)))
