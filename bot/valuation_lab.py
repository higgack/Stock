"""가치평가 랩 — 결정적 DCF · 역DCF · 몬테카를로 · 풋볼필드. 전 시장.

사용자 2026-10-22: 외부 레포 검토 뒤 "② 가치평가 랩 페이지를 이어서 적용".
분석 리포트의 'DCF 시나리오 (간이)' 는 LLM 이 쓴 문단이라(`fundamentals_
analyst.py` RULE 3) 검산할 수 없다. 여기서는 같은 질문을 **계산**으로 답한다
(FinRobot `operators/dcf.py`·`monte_carlo.py`·`valuation_aggregator.py` 의
아이디어를 우리 규칙으로 다시 짰다 — 코드 복사 없음, Apache-2.0).

⚠️ 설계 원칙:
  · **'적정주가 하나'를 단정하지 않는다.** 할인율·성장률은 정답이 없는
    가정이다. 그래서 (1) 할인율마다 지금 주가가 몇 % 성장을 전제하는지
    (역DCF), (2) 가정 격자에서 주당가치가 어떻게 바뀌는지(민감도), (3) 가정을
    무작위로 섞었을 때 분포(몬테카를로)를 보여 준다. 가정은 화면에 그대로
    적는다(#32 자체계산은 자체계산이라고 · #55).
  · **재료는 재무제표 탭과 같은 표**(스냅샷의 yfinance 연간 재무)다 — 전 시장
    한 경로라 시장마다 산식이 갈리지 않는다(#38). FCF 는 `bot.fcf` 단일
    산출기로 만든다. 한국 밸류에이션 탭의 FCF 는 DART 기준이라 값이 조금
    다를 수 있음을 화면이 밝힌다(#220).
  · **만들 수 없으면 사유를 말한다**(#43·#131): 금융업 · 최근 FCF 음수 ·
    재무제표 통화 ≠ 거래 통화 · 주식수/순차입금 없음 · 연간 FCF 2년 미만.
  · 순수 함수 — 네트워크 0 · LLM 0. 몬테카를로는 고정 시드라 같은 입력이면
    같은 결과(새로고침마다 숫자가 흔들리지 않는다).
"""

from __future__ import annotations

import html as _html
import math
import random

# ── 가정(화면에 그대로 적는다) ──────────────────────────────────────────
DISCOUNT_GRID = (0.07, 0.08, 0.09, 0.10, 0.11, 0.12)
GROWTH_GRID = (-0.05, 0.0, 0.05, 0.10, 0.15, 0.20)
TERMINAL_G = 0.02          # 6년차 이후 영구성장률
YEARS = 5                  # 명시적 성장 구간
MC_N = 4000
MC_SEED = 20261022
MC_G_SD = 0.05             # 성장률 표준편차(%p)
MC_G_CLIP = (-0.10, 0.30)
MC_R = (0.07, 0.12)        # 할인율 균등분포
MC_TG = (0.01, 0.03)       # 영구성장률 균등분포
G_MU_CLIP = (-0.05, 0.20)  # 과거 매출 CAGR 을 성장률 중심으로 쓸 때의 상하한
PEER_PER_OK = (0.0, 200.0)

# 재무 라인아이템 후보(앞이 없으면 뒤) — 재무제표 탭과 같은 yfinance 이름.
_REV = ("Total Revenue", "Operating Revenue")
_DEBT = ("Total Debt",)
_CASH = ("Cash Cash Equivalents And Short Term Investments",
         "Cash And Cash Equivalents",
         "Cash Financial")
_FIN_SECTORS = ("Financial Services",)
_FIN_INDUSTRY_WORDS = ("Bank", "Insurance", "Capital Markets", "Credit Services",
                       "Asset Management", "Mortgage")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and math.isfinite(f) else None


def _pick(row: dict | None, names: tuple[str, ...]):
    for nm in names:
        v = _num((row or {}).get(nm))
        if v is not None:
            return v
    return None


def _rows(snap: dict, stmt: str, kind: str) -> list[dict]:
    """스냅샷 재무 행(최신 먼저) → 기간 오름차순."""
    rows = (((snap.get("financials") or {}).get(stmt) or {}).get(kind)) or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("period")]
    return sorted(rows, key=lambda r: str(r["period"]))


def _cagr(first: float, last: float, years: float):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def gather(snap: dict) -> dict:
    """스냅샷 → 계산 재료 + 출처. 못 만드는 칸은 None + 사유(`why`). 순수."""
    from bot.fcf import fcf_from_row
    why: list[str] = []
    out: dict = {"why": why}
    ccy = str(snap.get("currency") or "")
    fccy = str(snap.get("financial_currency") or "")
    out["currency"] = ccy
    out["price"] = _num(snap.get("current_price"))
    out["shares"] = _num(snap.get("shares_outstanding"))
    out["market_cap"] = _num(snap.get("market_cap"))

    cf = _rows(snap, "cash_flow", "annual")
    fcf_hist = []
    for r in cf:
        v = fcf_from_row(r)
        if v is not None:
            fcf_hist.append((str(r["period"])[:10], v))
    out["fcf_hist"] = fcf_hist

    inc = _rows(snap, "income_statement", "annual")
    rev_hist = [(str(r["period"])[:10], _pick(r, _REV)) for r in inc]
    rev_hist = [(p, v) for p, v in rev_hist if v is not None]
    out["rev_hist"] = rev_hist
    g_rev = None
    if len(rev_hist) >= 3:
        g_rev = _cagr(rev_hist[0][1], rev_hist[-1][1], len(rev_hist) - 1)
    out["rev_cagr"] = g_rev

    # 순차입금 — 가장 최근 재무상태표(분기가 있으면 분기, 더 새로우므로).
    bs = _rows(snap, "balance_sheet", "quarterly") or _rows(snap, "balance_sheet", "annual")
    debt = cash = None
    bs_period = None
    if bs:
        last = bs[-1]
        bs_period = str(last["period"])[:10]
        debt = _pick(last, _DEBT)
        cash = _pick(last, _CASH)
    out["debt"], out["cash"], out["bs_period"] = debt, cash, bs_period
    out["net_debt"] = (debt - cash) if (debt is not None and cash is not None) else None

    # ── DCF 를 만들 수 있나 ────────────────────────────────────────
    sector = str(snap.get("sector") or "")
    industry = str(snap.get("industry") or "")
    if sector in _FIN_SECTORS or any(w in industry for w in _FIN_INDUSTRY_WORDS):
        why.append("금융업은 영업현금흐름에 예금·대출·보험료 흐름이 섞여 FCF 기반"
                   " DCF 가 성립하지 않습니다")
    if ccy and fccy and ccy != fccy:
        why.append(f"재무제표 통화({fccy})와 거래 통화({ccy})가 달라, 환산 기준"
                   " 없이 주당가치를 만들지 않습니다")
    if len(fcf_hist) < 2:
        why.append(f"연간 FCF 가 {len(fcf_hist)}개 기간뿐이라(2개 이상 필요)"
                   " 기준값을 정할 수 없습니다")
    elif fcf_hist[-1][1] <= 0:
        why.append(f"최근 연간 FCF({fcf_hist[-1][0]})가 0 이하라 성장 할인 모형이"
                   " 성립하지 않습니다")
    if not out["shares"]:
        why.append("발행주식수가 없어 주당가치로 나눌 수 없습니다")
    if not out["price"]:
        why.append("현재가가 없어 비교할 수 없습니다")
    if out["net_debt"] is None:
        miss = [n for n, v in (("총차입금", debt), ("현금", cash)) if v is None]
        why.append(f"최근 재무상태표에 {'·'.join(miss) or '항목'}이 없어 순차입금을"
                   " 정할 수 없습니다")
    out["fcf0"] = fcf_hist[-1][1] if fcf_hist else None
    out["fcf0_period"] = fcf_hist[-1][0] if fcf_hist else None
    out["dcf_ok"] = not why
    return out


def dcf_per_share(fcf0: float, g: float, r: float, tg: float,
                  net_debt: float, shares: float, years: int = YEARS):
    """2단계 DCF 주당가치. r ≤ tg 면 영구가치가 발산하므로 None. 순수."""
    if r <= tg or shares <= 0:
        return None
    pv = 0.0
    f = fcf0
    for t in range(1, years + 1):
        f *= 1.0 + g
        pv += f / (1.0 + r) ** t
    tv = f * (1.0 + tg) / (r - tg)
    pv += tv / (1.0 + r) ** years
    return (pv - net_debt) / shares


def implied_growth(price: float, fcf0: float, r: float, tg: float,
                   net_debt: float, shares: float, lo: float = -0.5,
                   hi: float = 1.0):
    """현재가를 정당화하는 5년 FCF 성장률(역DCF). 범위 밖이면 (None, 방향). 순수.

    주당가치는 g 에 대해 단조 증가라 이분법이 성립한다."""
    def f(g):
        return dcf_per_share(fcf0, g, r, tg, net_debt, shares)
    vlo, vhi = f(lo), f(hi)
    if vlo is None or vhi is None:
        return None, "undefined"
    if price < vlo:
        return None, "below"
    if price > vhi:
        return None, "above"
    a, b = lo, hi
    for _ in range(80):
        m = (a + b) / 2.0
        if f(m) < price:
            a = m
        else:
            b = m
    return (a + b) / 2.0, ""


def growth_center(rev_cagr) -> tuple[float, str]:
    """몬테카를로 성장률 중심 — 과거 매출 CAGR(상하한) 또는 가정 5%."""
    if rev_cagr is None:
        return 0.05, "과거 매출 CAGR 을 만들 수 없어 가정 5%"
    c = min(max(rev_cagr, G_MU_CLIP[0]), G_MU_CLIP[1])
    note = f"과거 매출 CAGR {rev_cagr * 100:+.1f}%"
    if c != rev_cagr:
        note += f"(상하한 {G_MU_CLIP[0] * 100:.0f}~{G_MU_CLIP[1] * 100:.0f}% 로 {c * 100:+.1f}%)"
    return c, note


def monte_carlo(fcf0, net_debt, shares, price, g_mu,
                n: int = MC_N, seed: int = MC_SEED) -> dict | None:
    """가정을 섞은 주당가치 분포. 고정 시드 — 같은 입력이면 같은 결과. 순수."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        g = min(max(rng.gauss(g_mu, MC_G_SD), MC_G_CLIP[0]), MC_G_CLIP[1])
        r = rng.uniform(*MC_R)
        tg = rng.uniform(*MC_TG)
        v = dcf_per_share(fcf0, g, r, tg, net_debt, shares)
        if v is not None:
            vals.append(v)
    if len(vals) < n // 2:
        return None
    vals.sort()

    def q(p):
        k = (len(vals) - 1) * p
        i = int(k)
        j = min(i + 1, len(vals) - 1)
        return vals[i] + (vals[j] - vals[i]) * (k - i)
    above = sum(1 for v in vals if v > price) / len(vals) if price else None
    return {"p10": q(0.10), "p50": q(0.50), "p90": q(0.90),
            "p_above": above, "n": len(vals)}


def peer_per_range(snap: dict, eps):
    """동종사 후행 PER 사분위(25~75%) × 우리 EPS → 가격 범위. 3곳 미만이면 None."""
    if eps is None or eps <= 0:
        return None
    pers = []
    for p in snap.get("peer_comps") or []:
        if not isinstance(p, dict) or p.get("is_subject"):
            continue
        v = _num(p.get("trailingPE"))
        if v is not None and PEER_PER_OK[0] < v < PEER_PER_OK[1]:
            pers.append(v)
    if len(pers) < 3:
        return None
    pers.sort()

    def q(p):
        k = (len(pers) - 1) * p
        i = int(k)
        j = min(i + 1, len(pers) - 1)
        return pers[i] + (pers[j] - pers[i]) * (k - i)
    lo, hi = q(0.25), q(0.75)
    return {"lo": lo * eps, "hi": hi * eps, "per_lo": lo, "per_hi": hi,
            "n": len(pers)}


def build(snap: dict | None) -> dict:
    """스냅샷 → 랩 payload. 순수."""
    snap = snap or {}
    d = gather(snap)
    out: dict = {"inputs": d, "why": list(d["why"])}
    price = d["price"]
    if d["dcf_ok"]:
        fcf0, nd, sh = d["fcf0"], d["net_debt"], d["shares"]
        out["grid"] = [[dcf_per_share(fcf0, g, r, TERMINAL_G, nd, sh)
                        for g in GROWTH_GRID] for r in DISCOUNT_GRID]
        out["reverse"] = [(r,) + implied_growth(price, fcf0, r, TERMINAL_G, nd, sh)
                          for r in DISCOUNT_GRID]
        g_mu, g_note = growth_center(d["rev_cagr"])
        out["g_mu"], out["g_note"] = g_mu, g_note
        out["mc"] = monte_carlo(fcf0, nd, sh, price, g_mu)
    # 풋볼필드 — 재료가 있는 막대만.
    bars = []
    lo, hi = _num(snap.get("fiftyTwoWeekLow")), _num(snap.get("fiftyTwoWeekHigh"))
    if lo and hi and hi >= lo:
        bars.append(("52주 범위", lo, hi, "yfinance"))
    tl, th = _num(snap.get("target_low")), _num(snap.get("target_high"))
    if tl and th and th >= tl:
        n = snap.get("num_analysts")
        bars.append(("애널리스트 목표가", tl, th,
                     f"yfinance 컨센서스{f' {int(n)}명' if n else ''}"))
    fx_mismatch = any("통화" in w for w in d["why"])
    pr = None if fx_mismatch else peer_per_range(snap, _num(snap.get("trailingEps")))
    if pr:
        bars.append((f"동종사 PER {pr['per_lo']:.1f}~{pr['per_hi']:.1f}배 × 후행 EPS",
                     pr["lo"], pr["hi"],
                     f"자체계산 · 동종 {pr['n']}곳 PER 사분위 × 밸류에이션 탭 후행 EPS"))
    mc = out.get("mc")
    if mc:
        bars.append(("DCF 몬테카를로 P10~P90", mc["p10"], mc["p90"], "자체계산"))
    out["football"] = bars
    return out


# ── 렌더 ───────────────────────────────────────────────────────────────
def _e(s) -> str:
    return _html.escape(str(s))


def _money(v, ccy: str) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if ccy in ("KRW", "JPY"):
        unit = "원" if ccy == "KRW" else "엔"
        if a >= 1e12:
            return f"{v / 1e12:,.2f}조{unit}"
        if a >= 1e8:
            return f"{v / 1e8:,.0f}억{unit}"
        return f"{v:,.0f}{unit}"
    if a >= 1e9:
        return f"{v / 1e9:,.2f}B {ccy}"
    if a >= 1e6:
        return f"{v / 1e6:,.1f}M {ccy}"
    return f"{v:,.2f} {ccy}"


def _px(v, ccy: str) -> str:
    if v is None:
        return "—"
    if ccy in ("KRW", "JPY"):
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%"


def render_html(p: dict) -> str:
    d = p["inputs"]
    ccy = d.get("currency") or ""
    price = d.get("price")
    h: list[str] = []
    shares_s = "—" if not d.get("shares") else f"{d['shares']:,.0f}주"
    note = ('<div style="font-size:12px;color:var(--fg-soft);line-height:1.6;'
            'margin-bottom:10px">')
    h.append(
        note + "할인율·성장률은 정답이 없는 <b>가정</b>입니다. 그래서 적정주가 하나를"
        " 정하지 않고, 가정에 따라 값이 어떻게 바뀌는지를 보여 줍니다. 모든 값은"
        " <b>자체계산</b>이며 원천은 재무제표 탭과 같은 yfinance 연간 재무입니다.</div>")
    # 재료
    fh = d.get("fcf_hist") or []
    rows = "".join(
        f"<tr><td>{_e(pp)}</td><td class=\"num\">{_money(v, ccy)}</td></tr>"
        for pp, v in fh[-5:])
    h.append('<div class="si-section-title" style="font-size:13px">재료</div>'
             '<table class="si-table"><tbody>'
             f'<tr><td>현재가</td><td class="num">{_px(price, ccy)} {ccy}</td></tr>'
             f'<tr><td>발행주식수</td><td class="num">{shares_s}</td></tr>'
             f'<tr><td>순차입금 (총차입금 − 현금, {_e(d.get("bs_period") or "—")})</td>'
             f'<td class="num">{_money(d.get("net_debt"), ccy)}</td></tr>'
             f'<tr><td>기준 FCF ({_e(d.get("fcf0_period") or "—")} 연간)</td>'
             f'<td class="num">{_money(d.get("fcf0"), ccy)}</td></tr>'
             '</tbody></table>')
    if rows:
        h.append('<table class="si-table" style="margin-top:6px"><thead><tr>'
                 '<th>회계연도 말</th><th class="num">FCF (영업현금흐름 − CAPEX)</th>'
                 f'</tr></thead><tbody>{rows}</tbody></table>')
    if p.get("why"):
        h.append('<div class="si-note" style="margin-top:8px">DCF 를 만들지 않았습니다 — '
                 + " · ".join(_e(w) for w in p["why"]) + "</div>")
    else:
        # 역DCF
        rv = "".join(
            f"<tr><td>{r * 100:.0f}%</td><td class=\"num\">"
            + (_pct(g) if g is not None else
               ("−50% 미만" if how == "below" else "+100% 초과" if how == "above" else "—"))
            + "</td></tr>" for r, g, how in p["reverse"])
        h.append('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
                 '역DCF — 지금 주가가 전제하는 성장률</div>'
                 + note + f"현재가 {_px(price, ccy)} 를 정당화하려면 앞으로 {YEARS}년간"
                 f" FCF 가 해마다 몇 % 늘어야 하는지입니다(그 뒤 영구성장 "
                 f"{TERMINAL_G * 100:.0f}% 가정).</div>"
                 '<table class="si-table"><thead><tr><th>할인율</th>'
                 f'<th class="num">필요 FCF 성장률(연)</th></tr></thead><tbody>{rv}</tbody></table>')
        # 민감도
        head = "".join(f'<th class="num">{g * 100:+.0f}%</th>' for g in GROWTH_GRID)
        body = ""
        for r, row in zip(DISCOUNT_GRID, p["grid"]):
            cells = ""
            for v in row:
                if v is None:
                    cells += '<td class="num">—</td>'
                    continue
                col = ("#26a69a" if price and v >= price else "#e2574c")
                cells += f'<td class="num" style="color:{col}">{_px(v, ccy)}</td>'
            body += f"<tr><td>{r * 100:.0f}%</td>{cells}</tr>"
        h.append('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
                 '민감도 — 주당가치</div>'
                 + note + f"행 = 할인율, 열 = 앞으로 {YEARS}년 FCF 성장률(연). 초록 = 현재가"
                 " 이상, 빨강 = 미만.</div>"
                 f'<table class="si-table"><thead><tr><th>할인율 \\ 성장</th>{head}</tr>'
                 f"</thead><tbody>{body}</tbody></table>")
        mc = p.get("mc")
        if mc:
            pa = mc.get("p_above")
            h.append('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
                     '몬테카를로 — 가정을 섞었을 때</div>'
                     + note + f"성장률 ~ 정규분포(중심 {_e(p['g_note'])} · 표준편차"
                     f" {MC_G_SD * 100:.0f}%p · {MC_G_CLIP[0] * 100:.0f}~{MC_G_CLIP[1] * 100:.0f}%"
                     f" 로 자름), 할인율 {MC_R[0] * 100:.0f}~{MC_R[1] * 100:.0f}% ·"
                     f" 영구성장 {MC_TG[0] * 100:.0f}~{MC_TG[1] * 100:.0f}% 균등분포,"
                     f" {mc['n']:,}회 (고정 시드 — 새로고침해도 같은 값).</div>"
                     '<table class="si-table"><tbody>'
                     f'<tr><td>P10 / P50 / P90</td><td class="num">{_px(mc["p10"], ccy)} /'
                     f' {_px(mc["p50"], ccy)} / {_px(mc["p90"], ccy)}</td></tr>'
                     f'<tr><td>주당가치 &gt; 현재가 비율</td><td class="num">'
                     f'{"—" if pa is None else f"{pa * 100:.0f}%"}</td></tr>'
                     "</tbody></table>")
    h.append(_football_html(p.get("football") or [], price, ccy))
    h.append(note + "한국 종목의 밸류에이션 탭 FCF 는 DART 현금흐름표 기준이라 이 탭(yfinance)과"
             " 값이 조금 다를 수 있습니다. 이 탭의 값은 투자 판단의 근거가 아니라 가정의"
             " 무게를 가늠하는 도구입니다.</div>")
    return "".join(h)


def _football_html(bars: list, price, ccy: str) -> str:
    if not bars:
        return ""
    vals = [v for _, lo, hi, _ in bars for v in (lo, hi)]
    if price:
        vals.append(price)
    lo_all, hi_all = min(vals), max(vals)
    if hi_all <= lo_all:
        return ""
    pad = (hi_all - lo_all) * 0.05
    lo_all, hi_all = lo_all - pad, hi_all + pad
    span = hi_all - lo_all

    def pos(v):
        return (v - lo_all) / span * 100.0
    rows = []
    for label, lo, hi, src in bars:
        left, width = pos(lo), max(pos(hi) - pos(lo), 0.6)
        rows.append(
            '<div style="display:grid;grid-template-columns:minmax(120px,32%) 1fr;'
            'gap:8px;align-items:center;margin:6px 0;font-size:12px">'
            f'<div>{_e(label)}<div style="color:var(--fg-soft);font-size:11px">{_e(src)}'
            f' · {_px(lo, ccy)}~{_px(hi, ccy)}</div></div>'
            '<div style="position:relative;height:14px;background:var(--surface-2);border-radius:4px">'
            f'<div style="position:absolute;left:{left:.2f}%;width:{width:.2f}%;top:0;bottom:0;'
            'background:var(--accent);opacity:.75;border-radius:4px"></div>'
            + (f'<div style="position:absolute;left:{pos(price):.2f}%;top:-3px;bottom:-3px;'
               'width:2px;background:var(--fg)"></div>' if price else "")
            + "</div></div>")
    return ('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
            '풋볼필드 — 여러 잣대의 가격 범위</div>'
            '<div style="font-size:12px;color:var(--fg-soft);margin-bottom:4px">세로선 = 현재가'
            f' {_px(price, ccy)}</div>' + "".join(rows))
