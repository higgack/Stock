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
    """스냅샷 재무 행(최신 먼저) → 기간 오름차순. 모양이 다르면 빈 목록(던지지 않는다)."""
    fins = snap.get("financials")
    st = fins.get(stmt) if isinstance(fins, dict) else None
    rows = st.get(kind) if isinstance(st, dict) else None
    if not isinstance(rows, list):
        return []
    rows = [r for r in rows if isinstance(r, dict) and r.get("period")]
    return sorted(rows, key=lambda r: str(r["period"]))


def _years_between(p0: str, p1: str):
    """'YYYY-MM-DD' 두 기간 끝 사이 연수 — 행 개수가 아니라 날짜로 센다(중간 해가
    비면 행 개수는 연수를 줄여 CAGR 을 부풀린다, 독립 리뷰 2026-10-22)."""
    from datetime import date
    try:
        a = date.fromisoformat(str(p0)[:10])
        b = date.fromisoformat(str(p1)[:10])
    except ValueError:
        return None
    y = (b - a).days / 365.25
    return y if y > 0.5 else None


def _cagr(first: float, last: float, years):
    if first is None or last is None or first <= 0 or last <= 0 or not years:
        return None
    return (last / first) ** (1.0 / years) - 1.0


# 한국 우선주 코드(6자리 끝이 0 이 아님 — 005935·00680K) — 회사 전체 FCF 를 한
# 클래스 주식수로 나누면 주당가치가 틀린다.
def _is_kr_preferred(ticker: str) -> bool:
    t = (ticker or "").upper()
    code, _, sfx = t.partition(".")
    return sfx in ("KS", "KQ") and len(code) == 6 and code[:5].isdigit() and code[-1] != "0"


_DEBT_HINTS = ("Total Debt", "Long Term Debt", "Current Debt",
               "Long Term Debt And Capital Lease Obligation",
               "Current Debt And Capital Lease Obligation",
               "Capital Lease Obligations", "Long Term Capital Lease Obligation",
               "Current Capital Lease Obligation")
_LIAB = ("Total Liabilities Net Minority Interest", "Total Liabilities")
_MINORITY = ("Minority Interest",)


def _net_claims(snap: dict) -> dict:
    """가장 최근 재무상태표에서 총차입금·현금·비지배지분 → 주주 몫 바깥 청구액.

    분기를 최신부터 내려가며 차입금·현금이 **둘 다** 있는 첫 행을 쓰고, 없으면
    연간으로 내려간다(최신 한 행만 보면 그 열이 비는 날 DCF 를 통째로 버린다).
    무차입은 재무상태표 **계열 전체**(분기·연간 모든 행)에 차입·리스 항목이 하나도
    없고 그 행에 부채총계는 있을 때만 본다 — yfinance 는 빈 칸을 키에서 빼므로
    한 행에 차입 키가 없다는 건 '그 열이 비었다'일 수도 있다(델타 리뷰: 최신
    분기만 비면 무차입으로 읽어 과대평가했다). 빈칸을 0 으로 읽지 않는다(#235)."""
    rows = list(reversed(_rows(snap, "balance_sheet", "quarterly"))) + \
        list(reversed(_rows(snap, "balance_sheet", "annual")))
    debt_anywhere = any(k in r for r in rows for k in _DEBT_HINTS)
    for r in rows:
        cash = _pick(r, _CASH)
        if cash is None:
            continue
        debt = _pick(r, _DEBT)
        note = ""
        if debt is None:
            if debt_anywhere or _pick(r, _LIAB) is None:
                continue
            debt, note = 0.0, "차입금 항목이 없어 무차입으로 봄"
        mi = _pick(r, _MINORITY) or 0.0
        return {"period": str(r["period"])[:10], "debt": debt, "cash": cash,
                "minority": mi, "net": debt - cash + mi, "note": note}
    return {}


def gather(snap: dict, ticker: str = "") -> dict:
    """스냅샷 → 계산 재료 + 출처. 못 만드는 칸은 None + 사유(`why`). 순수."""
    from bot.fcf import fcf_from_row
    why: list[str] = []
    out: dict = {"why": why}
    ccy = str(snap.get("currency") or "")
    fccy = str(snap.get("financial_currency") or "")
    out["currency"] = ccy
    # 재무 금액은 재무제표 통화로 적는다(거래 통화와 다를 수 있다, #139).
    out["fin_currency"] = fccy or ccy
    out["fx_mismatch"] = bool(ccy and fccy and ccy != fccy)
    out["price"] = _num(snap.get("current_price"))
    out["market_cap"] = _num(snap.get("market_cap"))
    out["financials_asof"] = str(snap.get("financials_asof") or "")
    shares = _num(snap.get("shares_outstanding"))
    out["shares_note"] = ""
    # ⚠️ 복수 클래스(GOOGL 은 A 주만)·우선주 합산 시총에서 한 클래스 주식수로
    # 회사 전체 가치를 나누면 주당가치가 클래스 비율만큼 부푼다. 시총(전 클래스,
    # #256)과 현재가로 되짚은 주식수와 5% 넘게 갈리면 그 주식수를 쓰고 밝힌다.
    # 교체는 '한 클래스만 센' 방향(시총÷현재가가 더 큼)이고 4배 안일 때만 한다 —
    # 반대 방향이나 큰 배율은 시총이 다른 시점·단위라는 뜻일 수 있어 기준을 정하지
    # 않는다(델타 리뷰: 낡은 시총의 오차가 그대로 주식수로 옮겨 갔다, #254).
    px, mc = out["price"], out["market_cap"]
    out["shares_conflict"] = ""
    if shares and shares > 0 and px and px > 0 and mc and mc > 0:
        implied = mc / px
        ratio = implied / shares
        if 1.05 < ratio <= 4.0:
            out["shares_note"] = (f"발행주식수 {shares:,.0f}주가 시가총액÷현재가"
                                  f" {implied:,.0f}주보다 적어(복수 클래스·우선주일 수"
                                  " 있음 — 시총 기준 시각이 다르면 오차) 시가총액÷현재가를"
                                  " 썼습니다")
            shares = implied
        elif ratio > 4.0 or ratio < 1 / 1.05:
            out["shares_conflict"] = (
                f"발행주식수 {shares:,.0f}주와 시가총액÷현재가 {implied:,.0f}주가"
                f" {max(ratio, 1 / ratio):.2f}배 어긋나(시총 기준 시각·단위가 다를 수"
                " 있음) 주식수 기준을 정할 수 없습니다")
    out["shares"] = shares
    # 이중상장(A·H)은 스냅샷이 시총을 거래 클래스로 다시 계산해(#224) 위 대조가
    # 안 걸린다 — 다른 상장 클래스 주식수를 재지 않았으므로 만들지 않는다.
    out["dual_listing"] = ""
    try:
        from bot.market import detect_market
        mkt = detect_market(ticker) if ticker else ""
    except Exception:                                   # noqa: BLE001
        mkt = ""
    if mkt in ("HK", "CN_A") and snap.get("market_cap_note"):
        out["dual_listing"] = ("다른 시장에도 상장된 종목이라(시총이 이 거래 클래스"
                               " 기준) 다른 클래스 주식수를 재지 않아 주당가치를"
                               " 만들지 않습니다")
    # 한국 보통주에 상장 우선주가 있으면 주식수·시총이 보통주 것이라 주당가치가
    # 우선주 몫만큼 높게 나온다 — 만들되 그렇다고 밝힌다(#43).
    if mkt == "KR" and ticker and not _is_kr_preferred(ticker):
        try:
            from bot.market import has_kr_preferred_shares
            if has_kr_preferred_shares(ticker):
                out["shares_note"] = ((out["shares_note"] + " · ") if out["shares_note"] else "") + (
                    "상장 우선주가 있어 우선주 주식수가 빠졌습니다 — 주당가치가 우선주"
                    " 비중만큼 높게 나옵니다")
        except Exception:                               # noqa: BLE001
            pass

    cf = _rows(snap, "cash_flow", "annual")
    fcf_hist = []
    dropped = []
    for r in cf:
        v = fcf_from_row(r)
        if v is None:
            continue
        if not math.isfinite(v):
            dropped.append(str(r["period"])[:10])
            continue
        fcf_hist.append((str(r["period"])[:10], v))
    out["fcf_hist"] = fcf_hist
    out["fcf_dropped"] = dropped

    inc = _rows(snap, "income_statement", "annual")
    rev_hist = [(str(r["period"])[:10], _pick(r, _REV)) for r in inc]
    rev_hist = [(p, v) for p, v in rev_hist if v is not None]
    out["rev_hist"] = rev_hist
    g_rev = None
    if len(rev_hist) >= 3:
        g_rev = _cagr(rev_hist[0][1], rev_hist[-1][1],
                      _years_between(rev_hist[0][0], rev_hist[-1][0]))
    out["rev_cagr"] = g_rev

    claims = _net_claims(snap)
    out["claims"] = claims
    out["net_debt"] = claims.get("net")
    out["bs_period"] = claims.get("period")

    # ── DCF 를 만들 수 있나 ────────────────────────────────────────
    sector = str(snap.get("sector") or "")
    industry = str(snap.get("industry") or "")
    if sector in _FIN_SECTORS or any(w in industry for w in _FIN_INDUSTRY_WORDS):
        why.append("금융업은 영업현금흐름에 예금·대출·보험료 흐름이 섞여 FCF 기반"
                   " DCF 가 성립하지 않습니다")
    if out["dual_listing"]:
        why.append(out["dual_listing"])
    if out["shares_conflict"]:
        why.append(out["shares_conflict"])
    if _is_kr_preferred(ticker):
        why.append("우선주는 회사 전체 FCF 를 이 클래스 주식수로 나눌 수 없어 보통주"
                   " 화면에서 보십시오")
    if out["fx_mismatch"]:
        why.append(f"재무제표 통화({fccy})와 거래 통화({ccy})가 달라, 환산 기준"
                   " 없이 주당가치를 만들지 않습니다")
    if len(fcf_hist) < 2:
        why.append(f"연간 FCF 가 {len(fcf_hist)}개 기간뿐이라(2개 이상 필요)"
                   " 기준값을 정할 수 없습니다")
    elif fcf_hist[-1][1] <= 0:
        why.append(f"최근 연간 FCF({fcf_hist[-1][0]})가 0 이하라 성장 할인 모형이"
                   " 성립하지 않습니다")
    if not shares or shares <= 0:
        why.append("발행주식수가 없어(또는 0 이하) 주당가치로 나눌 수 없습니다")
    if not out["price"] or out["price"] <= 0:
        why.append("현재가가 없어 비교할 수 없습니다")
    if out["net_debt"] is None:
        why.append("재무상태표에 현금·총차입금이 함께 있는 기간이 없어 순차입금을"
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
    pcs = snap.get("peer_comps")
    for p in (pcs if isinstance(pcs, list) else []):
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


def build(snap: dict | None, ticker: str = "") -> dict:
    """스냅샷 → 랩 payload. 순수."""
    snap = snap if isinstance(snap, dict) else {}
    d = gather(snap, ticker)
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
        n = _num(snap.get("num_analysts"))
        bars.append(("애널리스트 목표가", tl, th,
                     f"yfinance 컨센서스{f' {int(n)}명' if n and n > 0 else ''}"))
    pr = None if d["fx_mismatch"] else peer_per_range(snap, _num(snap.get("trailingEps")))
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
    c = _e(ccy)
    if a >= 1e9:
        return f"{v / 1e9:,.2f}B {c}"
    if a >= 1e6:
        return f"{v / 1e6:,.1f}M {c}"
    return f"{v:,.2f} {c}"


def _px(v, ccy: str) -> str:
    if v is None:
        return "—"
    if ccy in ("KRW", "JPY"):
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%"


_NOTE = ('<div style="font-size:12px;color:var(--fg-soft);line-height:1.6;'
         'margin-bottom:10px">')


def render_html(p: dict, computed_at: str = "") -> str:
    """랩 payload → HTML 조각. `computed_at` = 계산 시각(KST, 호출부가 준다)."""
    d = p["inputs"]
    ccy = d.get("currency") or ""
    fccy = d.get("fin_currency") or ccy
    price = d.get("price")
    h: list[str] = []
    shares = d.get("shares")
    shares_s = "—" if not shares else f"{shares:,.0f}주"
    h.append(
        _NOTE + "할인율·성장률은 정답이 없는 <b>가정</b>입니다. 그래서 적정주가 하나를"
        " 정하지 않고, 가정에 따라 값이 어떻게 바뀌는지를 보여 줍니다. 모든 값은"
        " <b>자체계산</b>이며 원천은 재무제표 탭과 같은 yfinance 연간 재무입니다."
        " 매수·매도 신호가 아닙니다.</div>")
    asof = []
    if computed_at:
        asof.append(f"계산 {_e(computed_at)} (현재가는 이 탭이 받은 스냅샷 기준)")
    if d.get("financials_asof"):
        asof.append(f"재무 수집 {_e(d['financials_asof'])}")
    if asof:
        h.append(_NOTE + " · ".join(asof) + "</div>")
    cl = d.get("claims") or {}
    claim_label = "순차입금 (총차입금 − 현금"
    if cl.get("minority"):
        claim_label += " + 비지배지분"
    claim_label += f", {_e(d.get('bs_period') or '—')})"
    rows = "".join(
        f"<tr><td>{_e(pp)}</td><td class=\"num\">{_money(v, fccy)}</td></tr>"
        for pp, v in (d.get("fcf_hist") or [])[-5:])
    h.append('<div class="si-section-title" style="font-size:13px">재료</div>'
             '<table class="si-table"><tbody>'
             f'<tr><td>현재가</td><td class="num">{_px(price, ccy)} {_e(ccy)}</td></tr>'
             f'<tr><td>발행주식수</td><td class="num">{shares_s}</td></tr>'
             f'<tr><td>{claim_label}</td><td class="num">{_money(d.get("net_debt"), fccy)}</td></tr>'
             f'<tr><td>기준 FCF ({_e(d.get("fcf0_period") or "—")} 연간)</td>'
             f'<td class="num">{_money(d.get("fcf0"), fccy)}</td></tr>'
             '</tbody></table>')
    foot = []
    if d.get("shares_note"):
        foot.append(_e(d["shares_note"]))
    if cl.get("note"):
        foot.append(_e(cl["note"]))
    if d.get("fcf_dropped"):
        foot.append("FCF 값을 읽을 수 없는 해(" + ", ".join(_e(x) for x in d["fcf_dropped"])
                    + ")는 뺐습니다")
    foot.append("총차입금은 yfinance 'Total Debt'(리스부채가 포함될 수 있음)이고 장기"
                " 투자자산은 현금에 넣지 않았습니다")
    h.append(_NOTE + " · ".join(foot) + "</div>")
    if rows:
        h.append('<table class="si-table" style="margin-top:6px"><thead><tr>'
                 '<th>회계연도 말</th><th class="num">FCF (영업현금흐름 − CAPEX)</th>'
                 f'</tr></thead><tbody>{rows}</tbody></table>')
    if p.get("why"):
        h.append('<div class="si-note" style="margin-top:8px">DCF 를 만들지 않았습니다 — '
                 + " · ".join(_e(w) for w in p["why"]) + "</div>")
    else:
        neg_note = ""
        rv = "".join(
            f"<tr><td>{r * 100:.0f}%</td><td class=\"num\">"
            + (_pct(g) if g is not None else
               ("−50% 미만에서도 현재가보다 높음" if how == "below"
                else "+100% 초과" if how == "above" else "—"))
            + "</td></tr>" for r, g, how in p["reverse"])
        h.append('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
                 '역DCF — 지금 주가가 전제하는 성장률</div>'
                 + _NOTE + f"현재가 {_px(price, ccy)} 를 정당화하려면 앞으로 {YEARS}년간"
                 f" FCF 가 해마다 몇 % 늘어야 하는지입니다(그 뒤 영구성장 "
                 f"{TERMINAL_G * 100:.0f}% 가정).</div>"
                 '<table class="si-table"><thead><tr><th>할인율</th>'
                 f'<th class="num">필요 FCF 성장률(연)</th></tr></thead><tbody>{rv}</tbody></table>')
        head = "".join(f'<th class="num">{g * 100:+.0f}%</th>' for g in GROWTH_GRID)
        body = ""
        any_neg = False
        for r, row in zip(DISCOUNT_GRID, p["grid"]):
            cells = ""
            for v in row:
                if v is None:
                    cells += '<td class="num">—</td>'
                    continue
                any_neg = any_neg or v < 0
                # 중립 표기 — 현재가 이상은 굵게, 미만은 흐리게(매수·매도 색이 아니다).
                style = ("font-weight:700" if price and v >= price
                         else "color:var(--fg-soft)")
                cells += f'<td class="num" style="{style}">{_px(v, ccy)}</td>'
            body += f"<tr><td>{r * 100:.0f}%</td>{cells}</tr>"
        if any_neg:
            neg_note = " 음수 = 순차입금이 계산된 기업가치보다 큼."
        h.append('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
                 '민감도 — 주당가치</div>'
                 + _NOTE + f"행 = 할인율, 열 = 앞으로 {YEARS}년 FCF 성장률(연). 굵게 ="
                 f" 현재가 이상, 흐리게 = 미만.{neg_note}</div>"
                 f'<table class="si-table"><thead><tr><th>할인율 \\ 성장</th>{head}</tr>'
                 f"</thead><tbody>{body}</tbody></table>")
        mc = p.get("mc")
        if mc:
            pa = mc.get("p_above")
            h.append('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
                     '몬테카를로 — 가정을 섞었을 때</div>'
                     + _NOTE + f"성장률 ~ 정규분포(중심 {_e(p['g_note'])} · 표준편차"
                     f" {MC_G_SD * 100:.0f}%p · {MC_G_CLIP[0] * 100:.0f}~{MC_G_CLIP[1] * 100:.0f}%"
                     f" 로 자름), 할인율 {MC_R[0] * 100:.0f}~{MC_R[1] * 100:.0f}% ·"
                     f" 영구성장 {MC_TG[0] * 100:.0f}~{MC_TG[1] * 100:.0f}% 균등분포,"
                     f" {mc['n']:,}회 (고정 시드 — 새로고침해도 같은 값).</div>"
                     '<table class="si-table"><tbody>'
                     f'<tr><td>P10 / P50 / P90</td><td class="num">{_px(mc["p10"], ccy)} /'
                     f' {_px(mc["p50"], ccy)} / {_px(mc["p90"], ccy)}</td></tr>'
                     f'<tr><td>가정 조합 {mc["n"]:,}개 중 주당가치가 현재가보다 큰 비율'
                     f' (확률 아님)</td><td class="num">'
                     f'{"—" if pa is None else f"{pa * 100:.0f}%"}</td></tr>'
                     "</tbody></table>")
    h.append(_football_html(p.get("football") or [], price, ccy))
    h.append(_NOTE + "한국 종목의 밸류에이션 탭 FCF 는 DART 현금흐름표 기준이라 이 탭(yfinance)과"
             " 값이 조금 다를 수 있습니다. 이 탭의 값은 투자 판단의 근거가 아니라 가정의"
             " 무게를 가늠하는 도구입니다.</div>")
    return "".join(h)


_AXIS_MAX_X = 4.0   # 풋볼필드 축 상한 = 현재가 × 4 (극단 막대가 나머지를 누르지 않게)


def _football_html(bars: list, price, ccy: str) -> str:
    if not bars:
        return ""
    vals = [v for _, lo, hi, _ in bars for v in (lo, hi)]
    if price:
        vals.append(price)
    # 축: 0 아래(음수 주당가치)는 자르고, 현재가의 4배 위는 꼬리 표기로 둔다.
    lo_all = max(0.0, min(vals))
    hi_all = max(vals)
    if price and price > 0:
        hi_all = min(hi_all, price * _AXIS_MAX_X)
    if hi_all <= lo_all:
        return ""
    pad = (hi_all - lo_all) * 0.05
    lo_all, hi_all = max(0.0, lo_all - pad), hi_all + pad
    span = hi_all - lo_all

    def pos(v):
        return min(max((v - lo_all) / span * 100.0, 0.0), 100.0)
    rows = []
    clipped = False
    for label, lo, hi, src in bars:
        width = max(pos(hi) - pos(lo), 0.6)
        left = min(pos(lo), 100.0 - width)          # 막대가 트랙 밖으로 나가지 않게
        tail = ""
        if hi > hi_all:
            tail, clipped = f" ▶ {_px(hi, ccy)}", True
        if lo < 0:
            clipped = True
        rows.append(
            '<div style="display:grid;grid-template-columns:minmax(120px,32%) 1fr;'
            'gap:8px;align-items:center;margin:6px 0;font-size:12px">'
            f'<div>{_e(label)}<div style="color:var(--fg-soft);font-size:11px">{_e(src)}'
            f' · {_px(lo, ccy)}~{_px(hi, ccy)}{tail}</div></div>'
            '<div style="position:relative;height:14px;background:var(--surface-2);border-radius:4px">'
            f'<div style="position:absolute;left:{left:.2f}%;width:{width:.2f}%;top:0;bottom:0;'
            'background:var(--accent);opacity:.75;border-radius:4px"></div>'
            + (f'<div style="position:absolute;left:{pos(price):.2f}%;top:-3px;bottom:-3px;'
               'width:2px;background:var(--fg)"></div>' if price else "")
            + "</div></div>")
    cap = (f" · 축은 0 ~ 현재가 {_AXIS_MAX_X:g}배까지 — 넘는 막대는 ▶ 로 끝값 표기,"
           " 음수는 0 에서 자름" if clipped else "")
    return ('<div class="si-section-title" style="font-size:13px;margin-top:12px">'
            '풋볼필드 — 여러 잣대의 가격 범위</div>'
            '<div style="font-size:12px;color:var(--fg-soft);margin-bottom:4px">세로선 = 현재가'
            f' {_px(price, ccy)}{cap}</div>' + "".join(rows))
