"""테마별 시세 · 신고가/신저가 별도 페이지 (Naver 소스).

market.html '업종 등락 TOP 10' 옆 링크 → 서버 사이드 렌더(실적 캘린더 패턴).
무료·무키·graceful(데이터 없으면 안내 문구). 색=Western(상승 초록/하락 빨강).
"""
from __future__ import annotations

import html as _html
import logging
import time

from bot import naver_diag as _naver_diag

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
/* 값과 **같이** 가는 사실 줄(수집 실패·부분 수신) — 클래스를 쓰면서
   CSS 를 안 두면 각주가 본문 크기로 떠서 표보다 커 보인다(#201·#273·#299).
   같은 이름이 다른 번들에 있다고 스타일이 따라오지 않는다. */
.sm-note{color:var(--muted,#8b93a7);font-size:12px;margin:-2px 0 10px}
.ts{color:var(--muted);font-size:12px;margin-left:8px}
</style>
"""

_THEME_SCRIPT = r"""
<script>
(function(){function ap(){var h=parseInt(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}).format(new Date()),10)%24;document.documentElement.dataset.theme=(h>=19||h<7)?'dark':'light';}ap();setInterval(ap,60000);/* 60초 재체크 — 19/07시 경계에 열린 페이지도 리로드 없이 전환(메인 _THEME_JS·trade 와 통일, 2026-07-04) */})();
/* '← 홈으로' 는 일반 nav 로 market.html 직행. '원래 자리로'는 history.back()
   (bfcache 불안정·다단이동 시 엉뚱한 페이지) 대신 market.html 자체의 스크롤
   복원(sessionStorage)으로 해결 — 어떤 경로로 홈에 도착해도 마지막 위치 복귀
   (사용자 2026-06-15 '또 새로고침처럼'). 가로채기 제거. */
</script>
"""


def _shell(title: str, sub: str, active: str, body: str) -> str:
    def _t(key: str, label: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a{cls} href="{key}">{label}</a>'
    # KR 자식 nav = tw_pages._MARKET_NAV 단일 소스 (사용자 2026-06-16 'NXT 등
    # 다른 페이지엔 시간외 탭이 없음' — 하드코딩 nav 가 _MARKET_NAV 와 drift,
    # 시간외 급등·급락 신설 누락). lazy import 로 모듈 순환(tw_pages →
    # naver_pages) 회피. 실패 시 폴백도 동일 탭 구성(시간외 포함)으로 유지.
    try:
        from bot.tw_pages import _market_nav
        toggle = _market_nav("KR", active)
    except Exception:
        toggle = ('<div class="toggle">'
                  + _t("theme", "🎭 테마별 시세")
                  + _t("kr52", "📈 신고가·신저가")
                  + _t("highlow", "🚀 급등·급락")
                  + _t("krprepost", "🌙 NXT 급등·급락")
                  + _t("nxt", "📊 NXT 수급")
                  + '</div>')
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)}</title>{_CSS}</head><body>
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


def theme_status(data: dict) -> tuple[str, str]:
    """저장분 테마에 붙일 (짧은 라벨, 사유 줄) — 순수.

    ⚠️ 옛 판은 `stale` 이면 무조건 `N분 전 스냅샷 · 갱신 중` 이라고 적었다.
    그런데 그 문구가 뜨는 경로는 **동기 수집이 이미 끝나고 빈손인** 쪽이라
    진행 중인 갱신은 없다 — 기다리면 채워질 것처럼 읽혀 사용자가 새로고침을
    반복하고, 그때마다 죽은 7페이지를 다시 긁는다(#25 늘 뜨는 배지 · #43).
    갈래는 수집기가 `refreshing` 으로 싣는다(화면이 재계산하면 갈라진다, #35).
    """
    if not data.get("stale"):
        return "", ""
    age = int(data.get("stale_age") or 0)
    if age < 120:
        return "", ""
    ago = f"{age // 3600}시간 전" if age >= 3600 else f"{age // 60}분 전"
    if data.get("refreshing"):
        return f" · {ago} 스냅샷 · 갱신 중", ""
    why = str(data.get("reason") or "")
    return (f" · {ago} 스냅샷 · 갱신 실패",
            why or "테마 수집이 0건으로 끝났습니다 — 사유를 기록하지 못했습니다")


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
    # ⚠️ `note` 는 아래 else 안에서만 세워지는데 부제가 그걸 읽는다 — 테마가
    # 0건인 날 NameError 로 페이지가 통째로 죽는다(#63a 게이트 안에 갇힌 변수).
    note, _why0 = theme_status(data)
    if not themes:
        _w = str(data.get("reason") or "")
        body = ('<div class="empty">테마 시세를 불러올 수 없습니다'
                + (f' — {_html.escape(_w)}' if _w else '') + '</div>')
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
        from bot.highlow_render import GENERIC_FILTER_JS as _gfjs
        # 낡은 스냅샷을 즉시 내준 경우 그 사실을 **행 위에서** 말한다 —
        # 침묵하면 사용자가 그걸 '지금'으로 읽는다(#43·#163).
        # ⚠️ 단 SWR 창(10분)이 캐시 TTL(30초)보다 넓어 정상 조회의 대부분이
        # `stale` 이다 — 늘 뜨는 배지는 아무것도 안 재는 것과 같다(#25·#260).
        # 정말 뒤처졌을 때(TTL 의 4배)만, 그것도 **잰 값**으로 말한다.
        _why = _why0
        body = ((f'<div class="sm-note">⚠️ {_html.escape(_why)}</div>' if _why else "")
                + f'<div class="panel"><h2>전체 테마 {len(themes)}개 '
                f'<span class="ts">{ts} 기준{note}</span></h2>'
                f'<table id="thm-tbl" class="cflt"><thead><tr><th>#</th>'
                f'<th class="js-th-sort" data-k="name">테마</th>'
                f'<th class="js-th-sort" data-k="pct" style="text-align:right">등락률</th>'
                f'<th class="js-th-sort" data-k="pct3" style="text-align:right">최근3일</th>'
                f'<th>주도주</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>{_THEME_SORT_JS}{_gfjs}')
    # 부제는 **data 에서 파생**한다 — 리터럴로 '장중 30초 캐시' 라고 적어 두면
    # 32시간 낡은 스냅샷 위에서도 그렇게 주장한다(#55 설명이 코드와 어긋나면
    # 버그). 부제와 패널 제목이 **같은 판정값**에서 나와야 갈리지 않는다(#38).
    # ⚠️ 이 페이지는 **테마만** 그린다(사용자 2026-09-12 "여기 원래 테마만
    # 있으면 돼. 업종별 시세는 메인대시보드에 있으면 되는거야"). 옛 판은 탭
    # 라벨이 '업종별 시세(전체)' 인데 내용은 테마였고, 그걸 맞추려고 업종
    # 패널을 같이 그렸다 — 사용자는 그 패널을 원하지 않았다. 라벨·제목·내용을
    # 한 가지(테마)로 맞춘다(#34 라벨에 기준을 박을 것).
    _via = str(data.get("via") or "")
    _sub = ("Naver 증권 · 테마"
            + (" 저장분(수집 실패)" if (note and not data.get("refreshing"))
               else " 장중 30초 캐시")
            + (f" · 원천 {_html.escape(_via)}" if _via else "")
            + ". 이름 클릭 시 상세/종목분석.")
    return _shell("테마별 시세", _sub, "theme", body)


_THEME_SORT_JS = """<script>
(function(){
var tbl=document.getElementById('thm-tbl');if(!tbl)return;
var dir={};
/* `js-` 접두 = CSS 가 없는 게 정상인 **JS 훅**(#273) — 커서·정렬은 여기서
   붙인다. 접두가 없으면 CSS 미정의 가드가 '스타일이 조용히 빠졌다'로
   오보한다(2026-09-08 그 가드를 이 페이지에 켜자마자 잡혔다, #87a). */
tbl.querySelectorAll('th.js-th-sort').forEach(function(th){
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
                # fetch 빈/실패 → 직전 산출본 유지(블랭크 방지). ⚠️ 옛 판은 그
                # **나이를 한 마디도 안 했다** — front-api 가 막힌 날 화면이
                # 어제 종가 랭킹 위에 '장중 30초 갱신' 이라고 적었고, 값이 다
                # '있어서' 사용자도 감사도 못 잡는다(#96·#43·#52). 형제 위젯
                # (TW 업종·업종 등락)이 쓰는 규약을 그대로 쓴다(#38·#306).
                nv = dict(stale, stale=True,
                          stale_min=int(max(0.0, time.time() - _mt) // 60))
        if nv and (nv.get("up") or nv.get("down")):
            data = nv
    except Exception as exc:
        log.warning("naver KR movers: %s", exc)

    if data is not None:
        up = sort_by_pct(data["up"], gainers=True)      # 기본 등락률순(사용자 2026-06-15)
        down = sort_by_pct(data["down"], gainers=False)
        # KR 업종(한글) 백필 — 네이버 업종 그룹 멤버맵 (사용자 2026-06-14 'KR
        # 급등락에 업종 추가, 그냥 한글로'). SWR·graceful(빌드 중이면 —).
        _ind_why = ""
        try:
            from bot.naver_sector_client import (apply_kr_industry,
                                                 kr_industry_fail_reason)
            apply_kr_industry(up)
            apply_kr_industry(down)
            if not any(x.get("ind") for x in (up + down)):
                # 업종 열을 그려 놓고 전 행이 비면 사용자는 수집 실패로 읽는다
                # — `--check` 는 이미 사유를 말하는데 **페이지는 안 말했다**
                # (#123·#129·#189·#228 계열 · #43).
                _ind_why = kr_industry_fail_reason()
        except Exception:
            pass
        ts = _html.escape(data.get("ts", ""))
        _o = dict(name_only=True, show_ind=True, show_vol=True, show_value=True)
        body = ((f'<div class="sm-note">⚠️ 업종 칸이 빈 이유: '
                 f'{_html.escape(_ind_why)}</div>' if _ind_why else "")
                + '<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", "KR",
                              ind_dist_line(up), **_o)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", "KR",
                              ind_dist_line(down), **_o)
                + '</div>' + HL_SORT_JS)
        # ⚠️ `movers_freshness` 는 **캐시 나이를 보지 않는다** — 장중이면 무조건
        # '장중 30초 갱신' 이라고 적는다. 저장분을 그리는 날 그 문구는 거짓이다
        # (#55 설명이 코드와 어긋나면 버그). 저장분이면 **잰 나이**를 적는다.
        from bot.highlow_render import movers_freshness as _mf
        if data.get("stale"):
            _m = data.get("stale_min")
            _ago = _naver_diag.stale_label(_m * 60 if isinstance(_m, int) else None)
            _fresh_txt = f'저장분{f" ({_ago})" if _ago else ""} ⚠️'
        else:
            _fresh_txt = _mf("KR")
        sub = ("네이버 증권 급등/급락 · 업종=네이버 · "
               f"{_fresh_txt}{(' · ' + ts + ' 기준') if ts else ''}")
        return _shell("급등·급락", sub, "highlow", body)

    body = ('<div class="empty">급등·급락 데이터를 불러올 수 없습니다.<br>'
            '(잠시 후 다시 시도해 주세요.)</div>')
    return _shell("급등·급락", "네이버 증권 급등/급락", "highlow", body)
