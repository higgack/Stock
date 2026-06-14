"""미국 업종별 시세 · 52주 신고가/신저가 별도 페이지 (Finviz 소스).

KR naver_pages(테마별 시세 · 상한가/하한가)의 미국 미러 — market.html
'미국 업종 등락 TOP 10' 옆 링크 → 서버 사이드 렌더. 미국엔 가격제한폭이
없으므로 상한가/하한가 대신 52주 신고가/신저가 (사용자 2026-06-10).
무료·무키·graceful. 종목 클릭 → 우리 종목분석(lookup)."""
from __future__ import annotations

import html as _html
import logging

from bot.naver_pages import _CSS, _THEME_SCRIPT, _fmt_vol, _pct_cell

log = logging.getLogger("bot.us_pages")

# 연장거래 창 라벨 — ET(미국 거래 기준) + 한국시간 병기 (CLAUDE.md 시간표기 KST 정책).
# 한국시간은 동부 서머타임(EDT, +13h) 기준; 서머타임 해제(EST) 시 +1h.
_EXT_WINDOW = ("장전 4:00–9:30 · 장후 16:00–20:00 ET = 한국시간 장전 17:00–22:30 · "
               "장후 익일 05:00–09:00 (서머타임 기준 · 해제 시 +1h)")

# 신고저 테이블 헤더 클릭 정렬 (사용자 2026-06-11 '종목·현재가·등락률·시총
# 나래비'). data-* raw 값 기준, 클릭 시 desc→asc 토글 + 화살표. 외부 의존 0.
_HL_SORT_JS = """
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


def _shell(title: str, sub: str, active: str, body: str) -> str:
    def _t(key: str, label: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a{cls} href="{key}">{label}</a>'
    # 자식 링크 명칭 통일(사용자 2026-06-13): 업종별 시세(전체) →
    # 신고가·신저가 → 급등·급락 (가격제한 없는 시장).
    toggle = ('<div class="toggle">'
              + _t("usindustry", "🏭 업종별 시세(전체)")
              + _t("ushighlow", "📈 신고가·신저가")
              + _t("usmovers", "🚀 급등·급락")
              + _t("usprepost", "🌙 장전·장후")
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


def _fmt_mcap(m_eok: float | None) -> str:
    """억$ → 사람이 읽는 시총 ($T/$B/$M). 신고저·급등급락 공용."""
    if not m_eok:
        return "—"
    if m_eok >= 10000:      # ≥ $1T
        return f"${m_eok / 10000:.2f}T"
    if m_eok >= 10:         # ≥ $1B
        return f"${m_eok / 10:.1f}B"
    return f"${m_eok * 100:.0f}M"


def _stock_panel(title: str, items: list, tid: str, extra_head: str = "") -> str:
    """종목 표 패널 (종목/현재가/등락률/시총/업종, 헤더 정렬) — 신고저·
    급등급락 공용 (2026-06-12 movers 추가 때 highlow 내부에서 승격)."""
    if not items:
        return (f'<div class="panel"><h2>{title}</h2>'
                '<div class="empty">해당 종목 없음</div></div>')

    def _row(i: int, it: dict) -> str:
        tk = _html.escape(it.get("ticker", ""))
        nm = _html.escape(it.get("name") or it.get("ticker", ""))
        label = f'{tk}<span class="ts">({nm})</span>' if nm != tk else tk
        price = it.get("price")
        pct = it.get("pct")
        mcap = it.get("mcap")
        vol = it.get("vol")        # 거래량(사용자 2026-06-13)
        # 업종분류 — yfinance industry 원문 그대로 (사용자 2026-06-12)
        ind = _html.escape(str(it.get("ind") or ""))
        ind_cell = (f'<td class="ind" title="{ind}">{ind}</td>'
                    if ind else '<td class="ind">—</td>')
        # data-* = raw 정렬값 (sym/ind=text, price/pct/mcap/vol=numeric)
        return (
            f'<tr data-sym="{tk.lower()}" '
            f'data-price="{price if price is not None else -1}" '
            f'data-pct="{pct if pct is not None else -9999}" '
            f'data-vol="{vol if vol is not None else -1}" '
            f'data-mcap="{mcap if mcap is not None else -1}" '
            f'data-ind="{ind.lower()}">'
            f'<td class="rk">{i}</td>'
            f'<td class="nm"><a href="lookup/{tk}">{label}</a></td>'
            f'<td class="num">{("$" + format(price, ",.2f")) if price is not None else "—"}</td>'
            f'{_pct_cell(pct)}'
            f'<td class="num">{_fmt_vol(vol)}</td>'
            f'<td class="num">{_fmt_mcap(mcap)}</td>'
            f'{ind_cell}</tr>'
        )
    rows = "".join(_row(i, it) for i, it in enumerate(items, 1))
    # th data-key/data-type → JS 정렬. # 컬럼은 정렬 비활성.
    return (
        f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
        f'{extra_head}'
        f'<table class="hl-table" id="{tid}"><thead><tr>'
        f'<th>#</th>'
        f'<th class="srt" data-key="sym" data-type="text">종목</th>'
        f'<th class="srt" data-key="price" data-type="num" style="text-align:right">현재가</th>'
        f'<th class="srt" data-key="pct" data-type="num" style="text-align:right">등락률</th>'
        f'<th class="srt" data-key="vol" data-type="num" style="text-align:right">거래량(주)</th>'
        f'<th class="srt" data-key="mcap" data-type="num" style="text-align:right">시총</th>'
        f'<th class="srt" data-key="ind" data-type="text">업종</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></div>'
    )


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
    from bot.highlow_render import clean_source as _clean_src
    src = _html.escape(_clean_src(data.get("source", "Finviz")))  # stale '1% 근접' 정규화
    from bot.highlow_render import stock_panel as _hpanel

    def _panel(title, items, tid, extra=""):
        # 종목명=네이버 한글 + 거래량·거래대금 (사용자 2026-06-14 변경 — '네이버
        # 한글로, 거래량도 가져와'). 거래량=Finviz Overview Volume.
        return _hpanel(title, items, tid, "US", extra,
                       show_vol=True, show_value=True)

    hi, lo = data.get("high", []), data.get("low", [])
    # 종목명 네이버 한글 (사용자 2026-06-14 'US 52주 네이버 한글로') — 업종 endpoint
    # koreanCodeName(7d 캐시). + 거래대금(억$)=종가×거래량(Finviz Volume). 렌더
    # 시점이라 캐시 무관·graceful(네이버 실패 시 영문명 유지).
    try:
        from bot.naver_ranking_client import world_quote_map, world_upjong_name
        _nm = world_upjong_name("US")
        _q = world_quote_map("US")        # 거래량·거래대금 네이버(사용자 2026-06-14)
    except Exception:
        _nm, _q = {}, {}
    for r in hi + lo:
        kr = _nm.get(r.get("ticker"))
        if kr:
            r["name"] = kr
        q = _q.get(r.get("ticker"))       # 네이버 거래량/거래대금 우선
        if q:
            if q.get("vol") is not None:
                r["vol"] = q["vol"]
            if q.get("value") is not None:
                r["value"] = q["value"]
        # 네이버 미스 시 Finviz Volume 으로 거래대금 산출(폴백)
        if r.get("value") is None and r.get("price") and r.get("vol"):
            try:
                r["value"] = round(float(r["price"]) * float(r["vol"]) / 1e8, 2)
            except (TypeError, ValueError):
                pass
    # 기본 정렬 = 시총 내림차순 (사용자 2026-06-12 '처음 화면은 시총순') —
    # 시총 미확보(—) 행은 뒤로. 헤더 클릭으로 다른 기준 재정렬 가능.
    _mc_key = lambda it: (it.get("mcap") is None, -(it.get("mcap") or 0))
    hi = sorted(hi, key=_mc_key)
    lo = sorted(lo, key=_mc_key)
    if not hi and not lo:
        body = ('<div class="empty">신고가·신저가 데이터를 불러올 수 없습니다.<br>'
                '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        # 업종 분포 한 줄 추가 (사용자 2026-06-13 '업종분포 다 넣어주는걸로')
        body = ('<div class="grid">'
                + _panel("🔺 52주 신고가", hi, "hl-high", _ind_dist_line(hi))
                + _panel("🔻 52주 신저가", lo, "hl-low", _ind_dist_line(lo))
                + '</div>' + _HL_SORT_JS)
    # 부제 간결화 (사용자 2026-06-14 '쓸데없는건 빼고') + 장중 1h(다른 52주와 통일).
    sub = (f"미국 52주 신고가·신저가 · 종목명=네이버 한글 · 거래량·거래대금 · "
           f"시총순·헤더 클릭 정렬 · 업종=GICS·yfinance · 출처 {src} · 장중 1h"
           + (f" · {ts} 기준" if ts else ""))
    return _shell("미국 신고가·신저가", sub, "ushighlow", body)


def _ind_dist_line(items: list, top_k: int = 5) -> str:
    """패널 상단 업종 분포 한 줄 — 'Biotechnology 6 · 반도체 4 …' (참고
    텔레그램 채널의 섹터 카운트 미러, 순수 함수). 업종 없는 행은 제외."""
    from collections import Counter
    cnt = Counter(str(it.get("ind")) for it in items if it.get("ind"))
    if not cnt:
        return ""
    parts = " · ".join(f"{_html.escape(ind)} <b>{n}</b>"
                       for ind, n in cnt.most_common(top_k))
    return (f'<div class="ts" style="margin:2px 0 8px">업종 분포: {parts}'
            + (" 외" if len(cnt) > top_k else "") + "</div>")


def render_us_movers_page() -> str:
    """미국 당일 급등·급락 TOP30 — 전 미국 상장 일봉 산출 (신고가/신저가
    형제 표면, 사용자 2026-06-12). SWR 백그라운드 — 첫 방문이 산출 kick,
    캐시 전엔 '산출 중' 안내. 종목 → 우리 종목분석(lookup)."""
    try:
        from bot.finviz_client import fetch_us_movers
        data = fetch_us_movers()
    except Exception as exc:
        log.warning("us movers page fetch failed: %s", exc)
        data = {"up": [], "down": [], "ts": "", "source": ""}
    ts = _html.escape(data.get("ts", ""))
    src = _html.escape(data.get("source", ""))
    up, down = data.get("up", []), data.get("down", [])

    if not up and not down:
        if data.get("building"):
            # 상태 마커로 '산출 중'과 '산출 실패'를 구분 표시 — 실패가
            # 영구 '산출 중'으로 위장하던 것 차단 (2026-06-13 surfaced)
            st = data.get("status") or {}
            ts_lb = _html.escape(str(st.get("ts_label") or ""))
            if st.get("state") == "failed":
                detail = _html.escape(str(st.get("detail") or ""))
                body = (f'<div class="empty">⚠️ 최근 산출 실패'
                        + (f' ({ts_lb})' if ts_lb else '') + f' — {detail}<br>'
                        '5분 후 자동 재시도됩니다. 반복되면 yfinance 일시 '
                        '제한 가능 — 잠시 뒤 다시 확인해 주세요.</div>')
            elif st.get("state") == "running":
                done, total = st.get("done"), st.get("total")
                prog = (f" — 배치 {done}/{total}"
                        if done is not None and total else "")
                body = (f'<div class="empty">⏳ 산출 진행 중{prog}'
                        + (f' (시작 {ts_lb})' if ts_lb else '')
                        + ' — 전 미국 상장 일봉 스캔(수 분 소요). '
                          '잠시 후 새로고침해 주세요.</div>')
            else:
                body = ('<div class="empty">⏳ 첫 산출 진행 중 — 전 미국 상장 '
                        '일봉 스캔(수 분 소요). 잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">급등·급락 데이터를 불러올 수 없습니다.<br>'
                    '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        from bot.highlow_render import sort_by_mcap, stock_panel as _hpanel
        # 네이버 소스면 거래량·거래대금 표시(Naver 제공) — 사용자 2026-06-14
        # '거래량과 거래대금 모두'. 모든 자식은 기본 시총순(헤더로 등락률 재정렬).
        _is_nv = "네이버" in src
        up, down = sort_by_mcap(up), sort_by_mcap(down)
        # 업종 render-time 백필 (사용자 2026-06-14 'US 급등락 업종 안 나옴') — 무버
        # 캐시가 네이버 업종맵 생기기 전(stale 02:12)이라 ind=None. GICS/NASDAQ 벌크
        # (캐시) + 네이버 USA 업종맵(캐시 읽기만)으로 즉시 채움. render-safe(.info 안 함·
        # 네이버 fetch 안 함). 주말 session-fresh 라 재산출 대기 없이 표시.
        try:
            from bot.finviz_client import _cached, _fetch_industries
            _miss = [r.get("ticker") for r in up + down if not r.get("ind")]
            if _miss:
                _bulk = _fetch_industries(_miss, allow_slow=False)   # 벌크 캐시(빠름)
                _nv = _cached("naver_industry_US.json", ttl=7 * 86400) or {}
                for r in up + down:
                    if not r.get("ind"):
                        r["ind"] = _bulk.get(r.get("ticker")) or _nv.get(r.get("ticker"))
        except Exception:
            pass
        body = ('<div class="grid">'
                + _hpanel("🚀 가장 많이 오른 TOP 30", up, "mv-up", "US",
                          _ind_dist_line(up), show_vol=_is_nv, show_value=_is_nv)
                + _hpanel("📉 가장 많이 내린 TOP 30", down, "mv-down", "US",
                          _ind_dist_line(down), show_vol=_is_nv, show_value=_is_nv) + '</div>'
                + _HL_SORT_JS)
    # 부제 간결화 (사용자 2026-06-14 '쓸데없는건 빼고') — 무엇·출처(1회)·정렬·신선도.
    if "네이버" in src:
        sub = ("미국 당일 등락 상·하위 30 · 네이버 증권(NASDAQ+NYSE+AMEX) · "
               "시총순·헤더 클릭 정렬 · 장중 30분" + (f" · {ts} 기준" if ts else ""))
    else:
        sub = ("미국 당일 등락 상·하위 30 · 전 미국 보통주(SPAC·워런트 제외) · "
               "시총순·헤더 클릭 정렬"
               + (f" · 출처 {src}" if src else "") + " · 장중 30분"
               + (f" · {ts} 기준" if ts else ""))
    return _shell("미국 급등·급락 TOP30", sub, "usmovers", body)


def render_us_prepost_page() -> str:
    """미국 장전(pre-market)·장후(after-hours) 급등·급락 TOP30 — 정규장 종가
    대비 연장거래 등락(yfinance prepost 30분봉, 사용자 2026-06-14 '장전/장후도
    별도 자식'). 급등락(정규장)의 형제 표면. SWR 백그라운드 — 첫 방문 kick.
    연장거래는 유동성 얇아 뉴스 종목 위주(정상). 종목 → 우리 종목분석(lookup)."""
    try:
        from bot.prepost_client import fetch_us_prepost_movers
        data = fetch_us_prepost_movers()
    except Exception as exc:
        log.warning("us prepost page fetch failed: %s", exc)
        data = {"up": [], "down": [], "ts": "", "source": "", "session": ""}
    ts = _html.escape(data.get("ts", ""))
    up, down = data.get("up", []), data.get("down", [])
    sess = data.get("session") or ""
    sess_kr = "장전" if sess == "pre" else "장후" if sess == "post" else "장전·장후"

    if not up and not down:
        if data.get("building"):
            st = data.get("status") or {}
            ts_lb = _html.escape(str(st.get("ts_label") or ""))
            if st.get("state") == "failed":
                detail = _html.escape(str(st.get("detail") or ""))
                body = (f'<div class="empty">⚠️ 최근 산출 실패'
                        + (f' ({ts_lb})' if ts_lb else '') + f' — {detail}<br>'
                        f'연장거래({_EXT_WINDOW})에만 데이터가 '
                        '있습니다. 정규장 중·장 완전 종료 시에는 직전 연장 스냅샷을 '
                        '보여줍니다.</div>')
            elif st.get("state") == "running":
                done, total = st.get("done"), st.get("total")
                prog = (f" — 배치 {done}/{total}"
                        if done is not None and total else "")
                body = (f'<div class="empty">⏳ 산출 진행 중{prog}'
                        + (f' (시작 {ts_lb})' if ts_lb else '')
                        + ' — 전 미국 상장 연장거래 스캔(수 분 소요). '
                          '잠시 후 새로고침해 주세요.</div>')
            else:
                body = ('<div class="empty">⏳ 첫 산출 진행 중 — 전 미국 상장 '
                        '연장거래 스캔(수 분 소요). 잠시 후 새로고침해 주세요.</div>')
        else:
            body = (f'<div class="empty">장전·장후 급등·급락 데이터가 없습니다.<br>'
                    f'연장거래({_EXT_WINDOW}) 시간에 '
                    '확인해 주세요.</div>')
    else:
        from bot.highlow_render import sort_by_mcap, stock_panel as _hpanel
        up, down = sort_by_mcap(up), sort_by_mcap(down)
        body = ('<div class="grid">'
                + _hpanel(f"🚀 {sess_kr} 가장 많이 오른 TOP 30", up, "pp-up", "US",
                          _ind_dist_line(up), show_vol=True)
                + _hpanel(f"📉 {sess_kr} 가장 많이 내린 TOP 30", down, "pp-down", "US",
                          _ind_dist_line(down), show_vol=True) + '</div>'
                + _HL_SORT_JS)
    sub = (f"미국 {sess_kr} 연장거래 등락 상·하위 30 · 정규장 종가 대비 · "
           "yfinance 30분봉 · 시총순·헤더 클릭 정렬 · 연장거래 창에서 30분"
           + (f" · {ts} 기준" if ts else ""))
    return _shell("미국 장전·장후 급등·급락", sub, "usprepost", body)
