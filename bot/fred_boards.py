"""FRED 매크로 보드 — ① PPI 투자신호(ppi.html) ② 글로벌 유동성(liquidity.html).

원본(사용자 업로드 bambooinvesting 대시보드 2종, 2026-07-02)은 데이터를 HTML에
박제한 정적 스냅샷(PPI 2026-01·유동성 2026-03에 멈춤) — 우리는 같은 구성을
**자동 갱신**으로: 자정 대시보드 regen에서 FRED 히스토리(fred_client.fetch_history,
12h 캐시) → 지표 사전계산 → 페이지 재생성. LLM 0·FRED 무료(₩0, ~90콜/일).

시리즈 카탈로그 = bot/fred_boards_catalog.py (원본 시드 + 멀티마켓 관련주 +
PPI 15종·유동성 8종 확장). 카탈로그만 고치면 페이지 자동 반영.

신호(PPI)·종합점수(유동성)는 **투명한 룰**로 재정의(원본은 사전계산 임베드라
공식 불명): 아래 _signal / compute_score 참고, 회귀 테스트로 고정.
키 부재/개별 시리즈 실패 = graceful(그 시리즈만 생략, 페이지는 나머지로).
"""
from __future__ import annotations

import html as _h
import json as _json
import logging
from datetime import datetime, timedelta, timezone

from bot.fred_boards_catalog import LIQ_SERIES, PPI_SERIES

log = logging.getLogger("bot.fred_boards")

_KST = timezone(timedelta(hours=9))   # 모든 시각 KST 명시(서버 로컬타임 의존 금지)

_PPI_START = "2019-01-01"    # 원본과 동일 기준(2019-01~)
_LIQ_START = "2018-01-01"    # 점수 히스토리 2018~(원본 동일)


# ── 시계열 지표(순수 — 테스트 대상) ────────────────────────────────────────
def _pct(cur: float, base: float | None):
    if base is None or not base:
        return None
    return (cur - base) / abs(base) * 100.0


def _value_at(hist: list[tuple[str, float]], months_back: int):
    """마지막 관측 기준 months_back 개월 전(이하 가장 가까운 과거) 값."""
    if not hist:
        return None
    last_d = datetime.strptime(hist[-1][0], "%Y-%m-%d")
    y, m = last_d.year, last_d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    cut = f"{y:04d}-{m:02d}-{last_d.day:02d}"
    prev = [v for d, v in hist if d <= cut]
    return prev[-1] if prev else None


def series_metrics(hist: list[tuple[str, float]]) -> dict | None:
    """히스토리 → {latest, latest_date, mom, m3, m6, yoy, total, peak,
    peak_date, from_peak, trough_after_peak, recovery, momentum}. 원본 PPI
    분석 필드와 동일 의미(모멘텀=최근 6개월 월평균 변화율%). <8 관측 → None."""
    if len(hist) < 8:
        return None
    latest_d, latest = hist[-1]
    peak_d, peak = max(hist, key=lambda x: x[1])
    after = [x for x in hist if x[0] >= peak_d]
    trough_d, trough = min(after, key=lambda x: x[1]) if after else (latest_d, latest)
    m6 = _value_at(hist, 6)
    momentum = None
    if m6:
        momentum = ((latest / m6) ** (1 / 6) - 1) * 100.0   # 월평균 기하 변화율
    return {
        "latest": latest, "latest_date": latest_d[:7],
        "mom": _pct(latest, _value_at(hist, 1)),
        "m3": _pct(latest, _value_at(hist, 3)),
        "m6": _pct(latest, m6),
        "yoy": _pct(latest, _value_at(hist, 12)),
        "total": _pct(latest, hist[0][1]),
        "peak": peak, "peak_date": peak_d[:7],
        "from_peak": _pct(latest, peak),
        "trough_after_peak": trough if trough_d != peak_d else None,
        "recovery": _pct(latest, trough) if trough and trough_d != peak_d else None,
    } | {"momentum": momentum}


def _signal(m: dict) -> tuple[str, str, str]:
    """(key, label, note) — 원본 4분류를 투명한 임계값 룰로 재정의.
    strong: YoY≥5% & 3M≥1.5% / reversal: 고점 -5%↓에서 저점대비 +1.5%↑ 반등 &
    3M>0 / decline: YoY<0 & 3M<0 / moderate: YoY≥2% or 3M≥0.7% / 나머지 mild."""
    yoy, m3 = m.get("yoy"), m.get("m3")
    fp, rec = m.get("from_peak"), m.get("recovery")
    if yoy is not None and m3 is not None and yoy >= 5 and m3 >= 1.5:
        return ("strong", "🔴 강한 상승", "가격 전가력 강함 — 관련주 마진 개선 국면.")
    if (fp is not None and fp <= -5 and rec is not None and rec >= 1.5
            and m3 is not None and m3 > 0):
        return ("reversal", "🟡 바닥 반등", "저점 확인 후 반등 초기 — 침체 종료 후보.")
    if yoy is not None and m3 is not None and yoy < 0 and m3 < 0:
        return ("decline", "🔵 하락", "가격 하락 지속 — 마진 압박·수요 둔화 주의.")
    if (yoy is not None and yoy >= 2) or (m3 is not None and m3 >= 0.7):
        return ("moderate", "🟠 중간 상승", "완만한 상승 — 추세 강화 여부 관찰.")
    return ("mild", "⚪ 중립", "뚜렷한 방향성 없음 — 촉매 대기.")


# ── 유동성 파생·점수(순수 — 테스트 대상) ──────────────────────────────────
def net_liquidity(walcl, tga, rrp) -> list[tuple[str, float]]:
    """Fed 순유동성(B USD) = WALCL(M$)/1000 − TGA(M$)/1000 − RRP(B$).
    주간(WALCL) 날짜축 기준, TGA/RRP는 해당일 이하 최근값 매칭."""
    def at(hist, d):
        prev = [v for dd, v in hist if dd <= d]
        return prev[-1] if prev else None
    out = []
    for d, w in walcl:
        t, r = at(tga, d), at(rrp, d)
        if t is None or r is None:
            continue
        out.append((d, w / 1000.0 - t / 1000.0 - r))
    return out


def _pct_rank(hist_vals: list[float], v: float) -> float:
    """v의 백분위(0~100) — 5년 트레일링 분포 내 위치."""
    if not hist_vals:
        return 50.0
    below = sum(1 for x in hist_vals if x <= v)
    return below / len(hist_vals) * 100.0


def compute_score(components: dict[str, tuple[float, bool]]) -> float | None:
    """종합 유동성 점수 0~100 = 구성요소 백분위 평균. components =
    {name: (percentile_0_100, invert)}. invert=True(스프레드·VIX·NFCI 등
    높을수록 긴축)는 100-p로 뒤집음. 빈 입력 → None. 공식은 문서화·고정
    (원본 점수는 임베드라 재현 불가 — 투명 재정의)."""
    vals = [(100.0 - p if inv else p) for p, inv in components.values()
            if p is not None]
    return sum(vals) / len(vals) if vals else None


def score_verdict(score: float | None) -> tuple[str, str]:
    if score is None:
        return ("—", "데이터 부족")
    if score >= 70:
        return ("🟢 유동성 풍부", "위험자산 우호 국면 — 완화적 환경.")
    if score >= 50:
        return ("🟡 중립(완화 기울기)", "혼조 — 방향 지표(순유동성·스프레드) 주시.")
    if score >= 30:
        return ("🟠 중립(긴축 기울기)", "유동성 역풍 — 위험 관리 강화.")
    return ("🔴 긴축", "유동성 위축 — 방어적 포지셔닝 권고.")


# ── 데이터 수집 ────────────────────────────────────────────────────────────
def _load_ppi() -> list[dict]:
    from bot import fred_client
    rows = []
    for s in PPI_SERIES:
        hist = fred_client.fetch_history(s["id"], _PPI_START)
        m = series_metrics(hist)
        if not m:
            continue
        key, label, note = _signal(m)
        rows.append({**s, **m, "sig": key, "sig_label": label, "note": note,
                     "hist": [(d[:7], v) for d, v in hist]})
    return rows


def _load_liq() -> tuple[list[dict], dict, float | None, list]:
    """→ (rows, derived{net_liq…}, score, score_hist). rows 는 카탈로그 순."""
    from bot import fred_client
    H: dict[str, list] = {}
    for s in LIQ_SERIES:
        H[s["id"]] = fred_client.fetch_history(s["id"], _LIQ_START)
    rows = []
    for s in LIQ_SERIES:
        hist = H.get(s["id"]) or []
        m = series_metrics(hist)
        if not m:
            continue
        rows.append({**s, **m, "hist": [(d, v) for d, v in hist][-260:]})
    derived: dict = {}
    nl = net_liquidity(H.get("WALCL") or [], H.get("WTREGEN") or [],
                       H.get("RRPONTSYD") or [])
    if nl:
        derived["net_liq"] = nl[-260:]
    # 점수: 구성요소별 5년 트레일링 백분위 → 평균(compute_score 문서 참조).
    comps: dict[str, tuple[float, bool]] = {}

    def add(name, series, inv=False, transform=None):
        if not series or len(series) < 30:
            return
        vals = [v for _, v in series]
        if transform == "d13":       # 13주(관측 13개) 변화
            vals = [vals[i] - vals[i - 13] for i in range(13, len(vals))]
        elif transform == "yoy":     # 12개월 YoY%(월간 시리즈)
            vals = [_pct(vals[i], vals[i - 12]) for i in range(12, len(vals))]
            vals = [v for v in vals if v is not None]
        if len(vals) < 10:
            return
        window = vals[-260:]
        comps[name] = (_pct_rank(window[:-1] or window, window[-1]), inv)

    add("net_liq_13w", nl, transform="d13")
    add("reserves_13w", H.get("WRESBAL"), transform="d13")
    add("m2_yoy", H.get("M2SL"), transform="yoy")
    add("bank_credit_yoy", H.get("TOTBKCR"), transform="yoy")
    add("hy_oas", H.get("BAMLH0A0HYM2"), inv=True)
    add("nfci", H.get("NFCI"), inv=True)
    add("vix", H.get("VIXCLS"), inv=True)
    add("curve_10y3m", H.get("T10Y3M"))
    score = compute_score(comps)
    derived["components"] = {k: round(p, 1) for k, (p, _) in comps.items()}
    return rows, derived, score, []


# ── 렌더 ───────────────────────────────────────────────────────────────────
_BOARD_CSS = """
<style>
body{background:#0f1117;color:#e4e6ed;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;margin:0}
.wrap{max-width:1440px;margin:0 auto;padding:20px}
.nav{margin-bottom:14px;font-size:13px}.nav a{color:#8b8fa3;text-decoration:none}.nav a:hover{color:#e4e6ed}
h1{font-size:24px;margin:6px 0}h1 em{color:#4f8ff7;font-style:normal}
.sub{color:#8b8fa3;font-size:13px;margin:4px 0 14px}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.pill{padding:6px 14px;border-radius:8px;font-size:12.5px;font-weight:600;cursor:pointer;border:2px solid transparent;background:#1a1d27;color:#8b8fa3}
.pill.active{border-color:#fff;color:#e4e6ed}
.p-strong{color:#ef4444}.p-moderate{color:#f59e0b}.p-reversal{color:#eab308}.p-decline{color:#3b82f6}.p-mild{color:#8b8fa3}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:#8b8fa3;text-align:left;padding:7px 9px;border-bottom:1px solid #2e3348;font-size:11px;white-space:nowrap}
td{padding:7px 9px;border-bottom:1px solid rgba(46,51,72,.5)}
tr.row{cursor:pointer}tr.row:hover{background:#242836}tr.selected{background:rgba(79,143,247,.12)}
.pos{color:#22c55e;font-weight:600}.neg{color:#ef4444;font-weight:600}.flat{color:#8b8fa3}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;background:#242836}
.panel{background:#1a1d27;border:1px solid #2e3348;border-radius:12px;padding:18px;margin:14px 0}
.panel-title{font-size:15px;font-weight:600;margin-bottom:10px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:8px 0}
.stat{background:#242836;border-radius:8px;padding:10px}
.stat .k{font-size:11px;color:#8b8fa3}.stat .v{font-size:18px;font-weight:700;margin-top:2px}
.note{background:#242836;border-left:3px solid #4f8ff7;border-radius:8px;padding:12px;font-size:13px;color:#b9bdcc;margin-top:10px}
.chartbox{position:relative;height:320px}
.tbl-wrap{max-height:560px;overflow-y:auto}
.stocks{color:#8b8fa3;font-size:11px;max-width:340px}
.footer{color:#8b8fa3;font-size:11px;text-align:center;padding:18px 0;border-top:1px solid #2e3348;margin-top:24px}
</style>
"""

_NAV = ('<div class="nav"><a href="market.html">🌍 홈</a> · '
        '<a href="index.html">🦉 종목분석</a> · <a href="ppi.html">🏭 PPI</a> · '
        '<a href="liquidity.html">💧 유동성</a></div>')


def _fmt_pct_cell(v, digits=1):
    if v is None:
        return "<span class='flat'>—</span>"
    cls = "pos" if v >= 0 else "neg"
    return f"<span class='{cls}'>{v:+.{digits}f}%</span>"


def render_ppi_page(rows: list[dict], now: datetime | None = None) -> str:
    """ppi.html — 시리즈 테이블 + 클릭 상세(Chart.js CDN, 원본 UX 동일).
    rows 비면 데이터 없음 배너(키 부재도 페이지는 생성 — silent drop 금지)."""
    now = now or datetime.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M KST")
    payload = _json.dumps({"rows": rows}, ensure_ascii=False).replace("<", "\\u003c")
    cats = sorted({r["cat"] for r in rows})
    empty = ("" if rows else
             "<div class='note'>⚠️ FRED 데이터 없음 — FRED_API_KEY 확인 필요. "
             "키 등록 후 자정 재생성(또는 봇 재시작) 시 채워집니다.</div>")
    sig_counts = {k: sum(1 for r in rows if r["sig"] == k)
                  for k in ("strong", "moderate", "reversal", "decline", "mild")}
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PPI 투자신호</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>🏭 <em>PPI</em> 투자신호 보드</h1>
<p class="sub">미 생산자물가(FRED) 산업별 가격 추세 → 관련주 신호 · {len(rows)}개 시리즈(2019-01~) ·
신호 룰: 🔴 YoY≥5%&amp;3M≥1.5% / 🟡 고점-5%↓서 저점+1.5%↑ 반등 / 🔵 YoY&amp;3M 음수 / 🟠 YoY≥2% or 3M≥0.7% ·
관련주 US·KR·JP·TW · 데이터 적용시각 {ts} · 소스 FRED API(일 1회 자동 갱신)</p>
{empty}
<div class="pills" id="pills" data-counts='{_json.dumps(sig_counts)}'></div>
<div class="pills" id="cats" data-cats='{_json.dumps(cats, ensure_ascii=False).replace("<", "&lt;")}'></div>
<div class="panel"><div class="panel-title">시리즈 개요 <span style="color:#8b8fa3;font-size:11px">(행 클릭 = 상세 차트)</span></div>
<div class="tbl-wrap"><table><thead><tr>
<th>시리즈</th><th>카테고리</th><th>신호</th><th>MoM</th><th>3M</th><th>6M</th><th>YoY</th><th>고점比</th><th>관련주</th>
</tr></thead><tbody id="tb"></tbody></table></div></div>
<div class="panel" id="detail" style="display:none">
  <div class="panel-title" id="d-title"></div>
  <div class="stat-grid" id="d-stats"></div>
  <div class="chartbox"><canvas id="d-chart"></canvas></div>
  <div class="note" id="d-note"></div>
</div>
<div class="footer">FRED PPI · 신호는 룰 기반 참고 신호(투자 판단 아님) · NOAH</div>
</div>
<script id="ppi-data" type="application/json">{payload}</script>
<script>
(function(){{
var R=JSON.parse(document.getElementById('ppi-data').textContent).rows||[];
var SIG={{strong:'🔴 강한 상승',moderate:'🟠 중간 상승',reversal:'🟡 바닥 반등',decline:'🔵 하락',mild:'⚪ 중립',all:'전체'}};
var fsig='all',fcat='all',sel=null,chart=null;
function esc(s){{var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}}
function pc(v,dg){{if(v==null)return"<span class='flat'>—</span>";var c=v>=0?'pos':'neg';return "<span class='"+c+"'>"+(v>=0?'+':'')+v.toFixed(dg==null?1:dg)+"%</span>";}}
function pills(){{var cnt=JSON.parse(document.getElementById('pills').getAttribute('data-counts'));
 var keys=['all','strong','moderate','reversal','decline','mild'];
 document.getElementById('pills').innerHTML=keys.map(function(k){{
  var n=k==='all'?R.length:(cnt[k]||0);
  return "<span class='pill p-"+k+(fsig===k?' active':'')+"' data-k="+k+">"+SIG[k]+" "+n+"</span>";}}).join('');
 var cats=JSON.parse(document.getElementById('cats').getAttribute('data-cats'));
 document.getElementById('cats').innerHTML=["<span class='pill"+(fcat==='all'?' active':'')+"' data-c='all'>전체 카테고리</span>"]
  .concat(cats.map(function(c){{return "<span class='pill"+(fcat===c?' active':'')+"' data-c=\\""+esc(c)+"\\">"+esc(c)+"</span>";}})).join('');
}}
function rows(){{return R.filter(function(r){{return (fsig==='all'||r.sig===fsig)&&(fcat==='all'||r.cat===fcat);}});}}
function table(){{document.getElementById('tb').innerHTML=rows().map(function(r,i){{
 return "<tr class='row"+(sel===r.id?' selected':'')+"' data-id='"+esc(r.id)+"'>"+
 "<td><b>"+esc(r.name)+"</b><div style='color:#8b8fa3;font-size:10px'>"+esc(r.id)+" · "+esc(r.latest_date)+"</div></td>"+
 "<td>"+esc(r.cat)+"</td><td><span class='badge p-"+r.sig+"'>"+esc(r.sig_label)+"</span></td>"+
 "<td>"+pc(r.mom,2)+"</td><td>"+pc(r.m3)+"</td><td>"+pc(r.m6)+"</td><td>"+pc(r.yoy)+"</td><td>"+pc(r.from_peak)+"</td>"+
 "<td class='stocks'>"+esc(r.stocks)+"</td></tr>";}}).join('');}}
function detail(id){{var r=R.find(function(x){{return x.id===id;}});if(!r)return;sel=id;
 document.getElementById('detail').style.display='block';
 document.getElementById('d-title').textContent=r.name+' ('+r.id+') — '+r.sig_label;
 var st=[['최신',r.latest.toFixed(1)+' ('+r.latest_date+')'],['YoY',(r.yoy==null?'—':r.yoy.toFixed(1)+'%')],
  ['2019년 이후',(r.total==null?'—':r.total.toFixed(1)+'%')],['고점 대비',(r.from_peak==null?'—':r.from_peak.toFixed(1)+'%')],
  ['저점 회복',(r.recovery==null?'—':r.recovery.toFixed(1)+'%')],['6M 모멘텀',(r.momentum==null?'—':r.momentum.toFixed(2)+'%/월')]];
 document.getElementById('d-stats').innerHTML=st.map(function(s){{return "<div class='stat'><div class='k'>"+s[0]+"</div><div class='v'>"+s[1]+"</div></div>";}}).join('');
 document.getElementById('d-note').innerHTML='💡 '+esc(r.note)+'<br>📌 관련주: '+esc(r.stocks);
 if(chart)chart.destroy();
 chart=new Chart(document.getElementById('d-chart'),{{type:'line',
  data:{{labels:r.hist.map(function(h){{return h[0];}}),datasets:[{{data:r.hist.map(function(h){{return h[1];}}),borderColor:'#4f8ff7',borderWidth:2,pointRadius:0,tension:.25}}]}},
  options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#8b8fa3',maxTicksLimit:12}},grid:{{color:'#242836'}}}},y:{{ticks:{{color:'#8b8fa3'}},grid:{{color:'#242836'}}}}}},maintainAspectRatio:false}}}});
 table();}}
document.addEventListener('click',function(ev){{
 var p=ev.target.closest('.pill');
 if(p){{if(p.hasAttribute('data-k'))fsig=p.getAttribute('data-k');
  if(p.hasAttribute('data-c'))fcat=p.getAttribute('data-c');pills();table();return;}}
 var tr=ev.target.closest('tr.row');if(tr)detail(tr.getAttribute('data-id'));}});
pills();table();if(R.length)detail(R[0].id);
}})();
</script></body></html>"""


def render_liquidity_page(rows: list[dict], derived: dict, score: float | None,
                          now: datetime | None = None) -> str:
    """liquidity.html — 종합점수 + 순유동성 차트 + 지표 테이블·상세(해설 포함)."""
    now = now or datetime.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M KST")
    v_label, v_note = score_verdict(score)
    payload = _json.dumps(
        {"rows": rows, "net_liq": derived.get("net_liq") or [],
         "components": derived.get("components") or {}},
        ensure_ascii=False).replace("<", "\\u003c")
    cats = []
    for r in rows:
        c = r.get("category") or "기타"
        if c not in cats:
            cats.append(c)
    empty = ("" if rows else
             "<div class='note'>⚠️ FRED 데이터 없음 — FRED_API_KEY 확인 필요.</div>")
    score_s = "—" if score is None else f"{score:.0f}"
    comp_rows = "".join(
        f"<div class='stat'><div class='k'>{_h.escape(k)}</div>"
        f"<div class='v'>{p:.0f}</div></div>"
        for k, p in (derived.get("components") or {}).items())
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>글로벌 유동성</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>💧 <em>글로벌 유동성</em> 보드</h1>
<p class="sub">Fed 순유동성(WALCL−TGA−RRP)·M2·중앙은행 자산·크레딧 스프레드·스트레스 지표 {len(rows)}종(FRED) ·
데이터 적용시각 {ts} · 소스 FRED API(일 1회 자동 갱신)</p>
{empty}
<div class="panel"><div class="panel-title">종합 유동성 점수</div>
<div class="stat-grid">
 <div class="stat"><div class="k">점수 (0~100)</div><div class="v" style="font-size:30px">{score_s}</div></div>
 <div class="stat"><div class="k">판정</div><div class="v" style="font-size:16px">{_h.escape(v_label)}</div></div>
 {comp_rows}
</div>
<div class="note">공식: 구성요소별 최근값의 <b>5년 트레일링 백분위</b>(0~100) 평균 — 순유동성 13주Δ ·
지준 13주Δ · M2 YoY · 은행신용 YoY · HY스프레드(역) · NFCI(역) · VIX(역) · 10Y-3M 커브.
{_h.escape(v_note)} (원본 대시보드의 점수는 공식 비공개 임베드 — 본 보드는 투명 재정의·테스트 고정)</div></div>
<div class="panel"><div class="panel-title">Fed 순유동성 (B USD) = 총자산 − TGA − 역레포</div>
<div class="chartbox"><canvas id="nl-chart"></canvas></div></div>
<div class="pills" id="cats" data-cats='{_json.dumps(cats, ensure_ascii=False).replace("<", "&lt;")}'></div>
<div class="panel"><div class="panel-title">지표 일람 <span style="color:#8b8fa3;font-size:11px">(행 클릭 = 차트·해설)</span></div>
<div class="tbl-wrap"><table><thead><tr>
<th>지표</th><th>분류</th><th>최신</th><th>1M</th><th>3M</th><th>YoY</th><th>기준일</th>
</tr></thead><tbody id="tb"></tbody></table></div></div>
<div class="panel" id="detail" style="display:none">
  <div class="panel-title" id="d-title"></div>
  <div class="chartbox"><canvas id="d-chart"></canvas></div>
  <div class="note" id="d-note"></div>
</div>
<div class="footer">FRED · 점수·해설은 참고 신호(투자 판단 아님) · Phase 2: BOK(ECOS)·중국(AKShare) — NOAH</div>
</div>
<script id="liq-data" type="application/json">{payload}</script>
<script>
(function(){{
var D=JSON.parse(document.getElementById('liq-data').textContent);
var R=D.rows||[],fcat='all',sel=null,chart=null;
function esc(s){{var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}}
function pc(v,dg){{if(v==null)return"<span class='flat'>—</span>";var c=v>=0?'pos':'neg';return "<span class='"+c+"'>"+(v>=0?'+':'')+v.toFixed(dg==null?1:dg)+"%</span>";}}
function fv(r){{var v=r.latest;if(r.is_rate)return v.toFixed(2)+'%';
 if(Math.abs(v)>=1e12)return (v/1e12).toFixed(1)+'T';if(Math.abs(v)>=1e9)return (v/1e9).toFixed(1)+'B';
 if(Math.abs(v)>=1e6)return (v/1e6).toFixed(2)+'M';if(Math.abs(v)>=1e3)return (v/1e3).toFixed(1)+'K';return v.toFixed(2);}}
if(D.net_liq&&D.net_liq.length){{new Chart(document.getElementById('nl-chart'),{{type:'line',
 data:{{labels:D.net_liq.map(function(h){{return h[0];}}),datasets:[{{data:D.net_liq.map(function(h){{return h[1];}}),borderColor:'#22c55e',borderWidth:2,pointRadius:0,tension:.25,fill:{{target:'origin',above:'rgba(34,197,94,.06)'}}}}]}},
 options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#8b8fa3',maxTicksLimit:12}},grid:{{color:'#242836'}}}},y:{{ticks:{{color:'#8b8fa3'}},grid:{{color:'#242836'}}}}}},maintainAspectRatio:false}}}});}}
function pills(){{var cats=JSON.parse(document.getElementById('cats').getAttribute('data-cats'));
 document.getElementById('cats').innerHTML=["<span class='pill"+(fcat==='all'?' active':'')+"' data-c='all'>전체</span>"]
 .concat(cats.map(function(c){{return "<span class='pill"+(fcat===c?' active':'')+"' data-c=\\""+esc(c)+"\\">"+esc(c)+"</span>";}})).join('');}}
function table(){{document.getElementById('tb').innerHTML=R.filter(function(r){{return fcat==='all'||(r.category||'기타')===fcat;}})
 .map(function(r){{return "<tr class='row"+(sel===r.id?' selected':'')+"' data-id='"+esc(r.id)+"'>"+
 "<td><b>"+esc(r.name)+"</b><div style='color:#8b8fa3;font-size:10px'>"+esc(r.id)+"</div></td>"+
 "<td>"+esc(r.category||'—')+"</td><td><b>"+fv(r)+"</b></td>"+
 "<td>"+pc(r.mom,2)+"</td><td>"+pc(r.m3)+"</td><td>"+pc(r.yoy)+"</td><td style='color:#8b8fa3'>"+esc(r.latest_date)+"</td></tr>";}}).join('');}}
function detail(id){{var r=R.find(function(x){{return x.id===id;}});if(!r)return;sel=id;
 document.getElementById('detail').style.display='block';
 document.getElementById('d-title').textContent=r.name+' ('+r.id+')';
 var n=['📖 '+(r.desc||''),'🧭 '+(r.interpret||''),'👁 '+(r.how_to_read||''),'🇰🇷 '+(r.kr_impact||'')]
  .filter(function(x){{return x.length>4;}}).map(esc).join('<br>');
 document.getElementById('d-note').innerHTML=n;
 if(chart)chart.destroy();
 chart=new Chart(document.getElementById('d-chart'),{{type:'line',
  data:{{labels:r.hist.map(function(h){{return h[0];}}),datasets:[{{data:r.hist.map(function(h){{return h[1];}}),borderColor:'#4f8ff7',borderWidth:2,pointRadius:0,tension:.25}}]}},
  options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#8b8fa3',maxTicksLimit:12}},grid:{{color:'#242836'}}}},y:{{ticks:{{color:'#8b8fa3'}},grid:{{color:'#242836'}}}}}},maintainAspectRatio:false}}}});
 table();}}
document.addEventListener('click',function(ev){{
 var p=ev.target.closest('.pill');if(p&&p.hasAttribute('data-c')){{fcat=p.getAttribute('data-c');pills();table();return;}}
 var tr=ev.target.closest('tr.row');if(tr)detail(tr.getAttribute('data-id'));}});
pills();table();if(R.length)detail(R[0].id);
}})();
</script></body></html>"""


# ── 재생성(자정 regen 훅) ─────────────────────────────────────────────────
def regenerate_fred_boards() -> None:
    """ppi.html + liquidity.html 재생성 — 자정 대시보드 regen 에서 호출.
    FRED 캐시 12h 라 재실행 무해. 실패 시 기존 파일 유지(graceful)."""
    from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
    try:
        rows = _load_ppi()
        html = _inject_update_banner(render_ppi_page(rows))
        (ARCHIVE_ROOT / "ppi.html").write_text(html, encoding="utf-8")
        log.info("fred_boards: ppi.html regenerated (%d series)", len(rows))
    except Exception:
        log.exception("fred_boards: ppi regen failed")
    try:
        rows, derived, score, _ = _load_liq()
        html = _inject_update_banner(render_liquidity_page(rows, derived, score))
        (ARCHIVE_ROOT / "liquidity.html").write_text(html, encoding="utf-8")
        log.info("fred_boards: liquidity.html regenerated (%d series, score=%s)",
                 len(rows), score)
    except Exception:
        log.exception("fred_boards: liquidity regen failed")
