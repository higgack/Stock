"""Industry-level export trends from the 관세청 chapter sweep.

Phase-1 (this module): pure data — turn the per-HS-leaf monthly series
that customs_scan.build_series() already produces into per-INDUSTRY
monthly series, then compute the indicators the trend dashboard needs:

  - exp (monthly export USD, industry total = Σ member HS leaves)
  - yoy (year-over-year %, vs the same month 12 months earlier)
  - dyoy (ΔYoY = YoY 1st difference, '가속도' — this month's YoY minus
    last month's YoY, in %p)
  - ma12 (12-month moving average of exp)
  - classify (초고성장/강세 · 턴어라운드 후보 · 부진/재하락) from the
    latest YoY + ΔYoY, mirroring the reference dashboard's grouping

No new API calls: the caller passes the leaves dict from the chapter
scan it already ran. Industry membership comes from the official
HSK-MTI 연계표 (trade/mti_map.py). Leaves with no industry (or the
catch-all '기타') are dropped from the named-industry view.

Phase-2 (separate) renders these series as the SVG trend cards.
"""
from __future__ import annotations

from typing import Optional

from trade import mti_map


def aggregate_by_industry(
    leaves: dict[str, dict],
    *,
    path=mti_map.DEFAULT_PATH,
    field: str = "exp_dlr",
) -> dict[str, dict[str, int]]:
    """{industry: {ym: total}} summing each industry's member HS leaves
    per month for `field` ('exp_dlr' exports / 'imp_dlr' imports).
    Unmapped / '기타' leaves are excluded.

    leaves is customs_scan.build_series() output:
        {hs_code: {"name": str, "months": {ym: {"exp_dlr", "imp_dlr"}}}}
    """
    try:
        hsk_to_ind = mti_map.load(path)
    except mti_map.HskMtiFileMissing:
        return {}
    out: dict[str, dict[str, int]] = {}
    for hs, node in leaves.items():
        industry = hsk_to_ind.get(hs)
        if not industry or industry == mti_map.CATCH_ALL:
            continue
        bucket = out.setdefault(industry, {})
        for ym, fig in node["months"].items():
            bucket[ym] = bucket.get(ym, 0) + (fig.get(field) or 0)
    return out


def aggregate_by_mti(
    leaves: dict[str, dict],
    *,
    path=mti_map.DEFAULT_PATH,
    field: str = "exp_dlr",
) -> dict[str, dict]:
    """{mti6: {"name","industry","months":{ym:total},"wgts":{ym:kg}}}
    summing member HS leaves per month. The 하위품목 view ranks these
    (D램·낸드·웨이퍼 …) one level below industry. Unmapped/'기타' excluded.

    wgts = field 와 짝지어진 중량(kg) 합 (exp_dlr↔exp_wgt, imp_dlr↔
    imp_wgt) — 단가($/kg) 산출용 (2026-06-13). 옛 leaves(중량 미보존
    스냅샷)면 빈 dict — 렌더가 graceful 생략."""
    try:
        hsk_map = mti_map.load_mti(path)
        names = mti_map.mti_names(path)
    except mti_map.HskMtiFileMissing:
        return {}
    wgt_field = {"exp_dlr": "exp_wgt", "imp_dlr": "imp_wgt"}.get(field)
    out: dict[str, dict] = {}
    for hs, node in leaves.items():
        rec = hsk_map.get(hs)
        if not rec:
            continue
        mti6, industry, _ = rec
        if not mti6 or industry == mti_map.CATCH_ALL:
            continue
        nm, ind = names.get(mti6, (mti6, industry))
        bucket = out.setdefault(
            mti6, {"name": nm, "industry": ind, "months": {}, "wgts": {}})
        for ym, fig in node["months"].items():
            bucket["months"][ym] = bucket["months"].get(ym, 0) + (fig.get(field) or 0)
            if wgt_field:
                w = fig.get(wgt_field) or 0
                if w:
                    bucket["wgts"][ym] = bucket["wgts"].get(ym, 0) + w
    return out


_MTI_HS_CACHE: dict | None = None


def _mti_hs_members() -> dict[str, list[str]]:
    """{mti6: [구성 HS10 코드...]} — HSK-MTI 연계 역맵 (프로세스 1회
    캐시). MTI 품목은 HS10 leaf 여러 개를 묶은 것이라 자기 HS 코드가
    없고 '구성 HS' 목록이 정답 (사용자 2026-06-13 'HS Code 가 없나?').
    연계파일 부재 시 {} (graceful)."""
    global _MTI_HS_CACHE
    if _MTI_HS_CACHE is None:
        try:
            rev: dict[str, list[str]] = {}
            for hs, rec in mti_map.load_mti().items():
                m6 = rec[0]
                if m6:
                    rev.setdefault(m6, []).append(hs)
            _MTI_HS_CACHE = {k: sorted(v) for k, v in rev.items()}
        except Exception:
            _MTI_HS_CACHE = {}
    return _MTI_HS_CACHE


def _code_chip(code: str) -> str:
    """품목명 옆 (MTI 코드) 칩 — 모든 항목 식별자 표기 (사용자 2026-06-13
    '이름 옆에 코드')."""
    if not code:
        return ""
    return f" <span class='ind-code' title='MTI 코드'>{_html.escape(str(code))}</span>"


def _prev_year_month(ym: str) -> str:
    """'2026-04' → '2025-04'. Accepts 'YYYY-MM' or 'YYYYMM'."""
    s = ym.replace("-", "")
    y, m = int(s[:4]), int(s[4:6])
    return f"{y - 1:04d}-{m:02d}"


def _prev_month(ym: str) -> str:
    """'2026-04' → '2026-03', '2026-01' → '2025-12' (for MoM/전월대비).
    Accepts 'YYYY-MM' or 'YYYYMM'."""
    s = ym.replace("-", "")
    y, m = int(s[:4]), int(s[4:6])
    m -= 1
    if m == 0:
        m = 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _month_status_label(latest_ym: str, today=None) -> str:
    """최신월의 잠정/확정 라벨 (사용자 2026-06-12 '잠정/확정 구분 명시').

    관세청 월간 통계는 익월 초(전체 잠정) 적재 → 익월 ~15일 확정 정제.
    오늘이 (최신월+1)월 15일 전이면 '잠정(M/15 확정 예정)', 이후면
    '확정(익월 ~15일)'. 파싱 실패 시 보수적으로 확정 표기."""
    from datetime import date, datetime, timedelta, timezone
    try:
        s = latest_ym.replace("-", "")
        y, m = int(s[:4]), int(s[4:6])
    except (ValueError, IndexError):
        return "확정(익월 ~15일)"
    if today is None:
        today = datetime.now(timezone(timedelta(hours=9))).date()
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    if today < date(ny, nm, 15):
        return f"잠정({nm}/15 확정 예정)"
    return "확정(익월 ~15일)"


def _ma(values: list[int], window: int = 12) -> Optional[float]:
    """Moving average of the last `window` values, or None when fewer
    than `window` points exist (so a half-formed MA isn't shown)."""
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def industry_series(
    by_industry: dict[str, dict[str, int]],
) -> dict[str, list[dict]]:
    """Per industry, a month-ascending list of points:
        {ym, exp, yoy, dyoy, ma12}
    yoy is None when the year-earlier month is absent; dyoy is None when
    either this or last month's yoy is undefined; ma12 None until 12
    months exist. Pure — no I/O."""
    out: dict[str, list[dict]] = {}
    for industry, months in by_industry.items():
        yms = sorted(months)
        # normalise keys to 'YYYY-MM' for year-ago lookup
        norm = {}
        for ym in yms:
            s = ym.replace("-", "")
            norm[f"{s[:4]}-{s[4:6]}"] = months[ym]
        keys = sorted(norm)
        points: list[dict] = []
        exp_running: list[int] = []
        prev_yoy: Optional[float] = None
        ttm_hist: dict[str, float] = {}   # ym → trailing-12-mo sum (for TTM YoY)
        for k in keys:
            exp = norm[k] or 0
            exp_running.append(exp)
            ago = norm.get(_prev_year_month(k))
            yoy = ((exp - ago) / ago * 100.0) if ago else None
            dyoy = (yoy - prev_yoy) if (yoy is not None and prev_yoy is not None) else None
            # TTM = trailing 12-month export sum (only once 12 months exist).
            ttm = sum(exp_running[-12:]) if len(exp_running) >= 12 else None
            if ttm is not None:
                ttm_hist[k] = ttm
            # TTM YoY needs this TTM and the one 12 months earlier (=24 mo data)
            ttm_ago = ttm_hist.get(_prev_year_month(k))
            ttm_yoy = ((ttm - ttm_ago) / ttm_ago * 100.0) if (ttm and ttm_ago) else None
            points.append({
                "ym": k,
                "exp": exp,
                "yoy": yoy,
                "dyoy": dyoy,
                "ma12": _ma(exp_running, 12),
                "ttm": ttm,
                "ttm_yoy": ttm_yoy,
            })
            if yoy is not None:
                prev_yoy = yoy
        out[industry] = points
    return out


def _avg(vals: list) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def momentum(points: list[dict]) -> dict:
    """Latest-state momentum metrics used by the interpretation text:
    3-month average YoY / ΔYoY (the reference card shows these)."""
    last3 = points[-3:]
    return {
        "yoy3": _avg([p.get("yoy") for p in last3]),
        "dyoy3": _avg([p.get("dyoy") for p in last3]),
    }



# Classification thresholds (mirror the reference dashboard's buckets;
# env-free constants — these are display grouping, not blast-radius
# parameters). YoY in %, ΔYoY in %p.
_HIGH_GROWTH_YOY = 20.0     # 초고성장/강세: strong & (still) accelerating
_TURNAROUND_DYOY = 0.0      # 턴어라운드: YoY accelerating (ΔYoY > 0)
# 수입 모멘텀 박스: 산업 월 수입 하한($100M) — 소규모 산업 기저효과 노이즈 차단
_IMP_MIN_USD = 100_000_000

# 산업 내부 동학(세부-상대 뷰) 기준: 세부품목 월 수출 하한($50M, 신흥 엔진까지
# 잡되 노이즈 차단) · 견인 최소 격차(세부YoY−산업YoY ≥ +30%p) · 교체 승자/패자.
_SUB_REL_MIN_USD = 50_000_000
_LEAD_GAP = 30.0
_ROT_WIN, _ROT_LOSS = 20.0, -10.0

# 자본재(설비·장비) 키워드 — 수입 드라이버가 자본재면 진짜 capex '선행'. 완제품/
# 원자재 풀 분류는 품목명만으론 오분류가 많아 보류, 고정확·고가치인 자본재만 태깅.
_CAPITAL_KW = ("장비", "기계", "설비", "장치", "제조용", "공작")

# 산업부 수출입동향 보도자료(매월 1일 잠정) 바로가기 — 구조화 잠정 소스가 없어
# 데이터는 안 긁고 공식 원문 링크만 얹는다. URL 변경 시 env로 덮어쓰기.
import os as _os
_MOTIE_URL = (_os.environ.get("TRADE_MOTIE_URL")
              or "https://www.motie.go.kr/kor/article/ATCL3f49a5a8c")


def _is_capital_good(name: str) -> bool:
    return any(k in (name or "") for k in _CAPITAL_KW)


def classify(points: list[dict]) -> str:
    """Bucket an industry by its latest YoY + ΔYoY:

      초고성장/강세   — latest YoY ≥ +20%
      턴어라운드 후보 — YoY < +20% but ΔYoY > 0 (accelerating off a low base)
      부진/재하락     — everything else (decelerating / negative)

    Returns '데이터부족' when the latest point has no YoY yet."""
    if not points:
        return "데이터부족"
    latest = points[-1]
    yoy, dyoy = latest.get("yoy"), latest.get("dyoy")
    if yoy is None:
        return "데이터부족"
    if yoy >= _HIGH_GROWTH_YOY:
        return "초고성장/강세"
    if dyoy is not None and dyoy > _TURNAROUND_DYOY:
        return "턴어라운드 후보"
    return "부진/재하락"


def interpret(points: list[dict]) -> dict:
    """Plain-language reading of an industry's latest state, mirroring the
    reference dashboard's two lines:

      summary    — overall level (3-month avg YoY based)
      signal     — momentum label + ΔYoY explanation (가속/둔화/반등 …)

    Sentences are taken verbatim from the reference so the tone matches.
    Returns {summary, signal_label, signal_text} (signal_* '' when N/A)."""
    if not points:
        return {"summary": "", "signal_label": "", "signal_text": ""}
    latest = points[-1]
    yoy = latest.get("yoy")
    dyoy = latest.get("dyoy")
    m = momentum(points)
    yoy3 = m["yoy3"]

    # summary — level (matches reference phrasing)
    if yoy is None:
        summary = "YoY 계산에 필요한 전년동월 데이터가 아직 부족합니다."
    elif yoy3 is not None and yoy3 >= 50:
        summary = "최근 3개월 YoY 평균이 매우 높고 최신월도 압도적입니다."
    elif yoy >= 0 and yoy3 is not None and yoy3 >= 10:
        summary = "양의 성장률이 이어지고 최근 평균도 두 자릿수입니다."
    elif yoy >= 0 and (dyoy is not None and dyoy > 0):
        summary = "최근 마이너스 구간을 지나 양의 성장률로 돌아섰습니다."
    elif yoy >= 0:
        summary = "성장은 유지되지만 직전월 대비 속도가 둔화했습니다."
    else:
        summary = "최근 성장률이 마이너스에 머물러 있습니다."

    # signal — ΔYoY (1차 미분) momentum
    label, text = "", ""
    if dyoy is not None:
        if yoy is not None and yoy >= _HIGH_GROWTH_YOY and dyoy < 0:
            label = "고성장 둔화"
            text = "절대 성장률은 높지만 YoY의 1차 미분이 마이너스로 꺾였습니다."
        elif dyoy >= 5:
            label = "가속 확대"
            text = "YoY의 1차 미분이 큰 폭의 플러스입니다."
        elif dyoy > 0:
            label = "가속 유지"
            text = "YoY가 전월보다 더 높아졌습니다."
        elif dyoy < 0 and yoy is not None and yoy < 0:
            label = "재하락"
            text = "직전월 플러스 이후 다시 마이너스로 내려왔습니다."
        else:
            label = "둔화"
            text = "YoY가 전월보다 낮아졌습니다."
    return {"summary": summary, "signal_label": label, "signal_text": text}


# ───────────────────────── rendering (SVG trend cards) ─────────────────────────
# Self-contained SVG (no chart lib), mirroring the reference dashboard:
# export-value line + 12-month MA line + markers + grid, one card per
# industry with a stat row, grouped by classification. Same pattern as
# dashboard.py's other panels.

import html as _html
import json as _json

# Pixel geometry of one chart (matches the reference's 380×132 viewBox).
_VW, _VH = 380, 132
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 34, 10, 12, 22

# Classification → (badge label, CSS class). Order = display order.
_GROUPS = [
    ("초고성장/강세", "hot"),
    ("턴어라운드 후보", "turn"),
    ("부진/재하락", "down"),
    ("데이터부족", "na"),
]


def _eokusd(n) -> str:
    """USD → '억 달러' label (e.g. 11.18B → '112억$', TTM 131B → '1,310억$').
    1억$ = 1e8 USD = $100M. '$' 접미사로 억 원이 아니라 억 '달러'임을 모든
    셀에서 명확히 한다(과거 단위 오해 방지). 원본처럼 조 단위 없이 억만 쓰며,
    큰 TTM 합계는 천단위 구분만 붙인다."""
    if n is None:
        return "—"
    eok = n / 1e8
    a = abs(eok)
    if a >= 100:
        return f"{eok:,.0f}억$"
    if a >= 1:
        return f"{eok:.1f}억$"
    return f"{eok:.2f}억$"


def _pct(p, suffix="%") -> str:
    return f"{p:+.1f}{suffix}" if p is not None else "—"


def _dot_ym(ym: str) -> str:
    """'2026-04' → '2026.4' (drops month leading zero), matching the
    reference axis-label style."""
    if "-" not in ym:
        return ym
    y, m = ym.split("-", 1)
    return f"{y}.{int(m)}"


_LBL_H = 11.0   # approx text line height (viewBox units) for label de-collision


def _est_w(text: str) -> float:
    """Rough SVG text width (viewBox units) — CJK/심볼은 넓게, ASCII는 좁게.
    콜아웃이 겹치는지 판정할 때만 쓰는 근사값."""
    w = 0.0
    for ch in text:
        w += 6.6 if ord(ch) > 0x2000 else 3.4
    return w


def _place_labels(cands_per_label, occupied):
    """각 라벨의 후보 y들 중, 이미 점유된 박스(occupied: [(xl,xr,y)])와
    x겹침+y근접(<_LBL_H)이 없는 첫 y를 골라 배치. 못 고르면 첫 후보.
    반환=각 라벨의 최종 y. occupied는 진행하며 갱신(뒤 라벨이 앞 라벨 회피)."""
    out = []
    for xl, xr, cands in cands_per_label:
        chosen = None
        for cy in cands:
            cy = min(max(cy, 9.0), _VH - 3.0)
            if all(not (xl < pr and pl < xr and abs(cy - py) < _LBL_H)
                   for pl, pr, py in occupied):
                chosen = cy
                break
        if chosen is None:
            chosen = min(max(cands[0], 9.0), _VH - 3.0)
        occupied.append((xl, xr, chosen))
        out.append(chosen)
    return out


def _line_svg(series: list[tuple[int, float]], titles: list[str], *,
              dashed_second=None, label="",
              x_first="", x_last="", y_hi_lbl="", y_lo_lbl="",
              callouts: list[tuple[int, float, str, str]] | None = None,
              main_class: str = "ind-value-line",
              dot_class: str = "ind-latest-dot") -> str:
    """Generic 1-or-2 line SVG with axis labels.

    series       — [(i, value)] PRIMARY line (solid).
    dashed_second— optional [(i, value)] secondary line (dashed; 12M MA).
    titles       — hover <title> per primary point.
    x_first/x_last — X-axis date labels (e.g. '2024.1' / '2026.5').
    y_hi_lbl/y_lo_lbl — Y-axis top/bottom tick labels (e.g. '394' / '72').
    callouts     — [(i, value, text, cls)] in-chart text labels anchored at
                   a point (e.g. (last, exp, '최신 372억', 'ind-cl')).
    Returns '' if < 2 points."""
    if len(series) < 2:
        return ""
    n_max = max(i for i, _ in series)
    allv = [v for _, v in series] + [v for _, v in (dashed_second or [])]
    lo, hi = min(allv), max(allv)
    span = (hi - lo) or 1
    plot_w = _VW - _PAD_L - _PAD_R
    plot_h = _VH - _PAD_T - _PAD_B

    def x(i):
        return _PAD_L + (plot_w * i / n_max) if n_max else _PAD_L
    def y(v):
        return _PAD_T + plot_h * (1 - (v - lo) / span)

    grid = "".join(
        f'<line x1="{_PAD_L}" y1="{_PAD_T + plot_h*f:.1f}" '
        f'x2="{_VW-_PAD_R}" y2="{_PAD_T + plot_h*f:.1f}" class="ind-grid"/>'
        for f in (0.0, 0.5, 1.0)
    )
    main = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in series)
    main_line = f'<polyline points="{main}" class="{main_class}"/>'
    second = ""
    if dashed_second and len(dashed_second) >= 2:
        sp = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in dashed_second)
        second = f'<polyline points="{sp}" class="ind-ma-line"/>'
    li, lv = series[-1]
    dot = f'<circle cx="{x(li):.1f}" cy="{y(lv):.1f}" r="3.2" class="{dot_class}"/>'
    tip = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5" fill="transparent">'
        f'<title>{_html.escape(titles[k])}</title></circle>'
        for k, (i, v) in enumerate(series) if k < len(titles)
    )
    # axis tick labels
    axes = ""
    if y_hi_lbl:
        axes += f'<text x="2" y="{_PAD_T+4:.0f}" class="ind-axis">{_html.escape(y_hi_lbl)}</text>'
    if y_lo_lbl:
        axes += f'<text x="2" y="{_PAD_T+plot_h:.0f}" class="ind-axis">{_html.escape(y_lo_lbl)}</text>'
    if x_first:
        axes += f'<text x="{_PAD_L}" y="{_VH-3}" class="ind-axis">{_html.escape(x_first)}</text>'
    if x_last:
        axes += f'<text x="{_VW-_PAD_R}" y="{_VH-3}" class="ind-axis" text-anchor="end">{_html.escape(x_last)}</text>'
    # in-chart callouts (최신/저점/MA) — 서로/축라벨과 겹치지 않게 y를 분산.
    # 값이 비슷해 라벨이 한 점에 몰리면(예: 최신 16.2억$ vs MA 15.8억$) 뒤
    # 라벨을 점 위→아래→더 위/아래로 밀어 가독성 확보.
    co = ""
    cos = callouts or []
    if cos:
        # 축 라벨 박스를 먼저 점유시켜 콜아웃이 이를 피하도록.
        occupied: list[tuple[float, float, float]] = []
        if y_hi_lbl:
            occupied.append((2, 2 + _est_w(y_hi_lbl), _PAD_T + 4))
        if y_lo_lbl:
            occupied.append((2, 2 + _est_w(y_lo_lbl), _PAD_T + plot_h))
        if x_first:
            occupied.append((_PAD_L, _PAD_L + _est_w(x_first), _VH - 3))
        if x_last:
            occupied.append((_VW - _PAD_R - _est_w(x_last), _VW - _PAD_R, _VH - 3))
        meta = []          # (cx, anchor, text, cls)
        cands_per_label = []
        for ci, cv, ctext, ccls in cos:
            cx = x(ci); cy = y(cv)
            anchor = "end" if cx > _VW * 0.6 else "start"
            w = _est_w(ctext)
            xl, xr = (cx - w, cx) if anchor == "end" else (cx, cx + w)
            base = cy - 4
            cands_per_label.append((xl, xr, [
                base, cy + _LBL_H, base - _LBL_H, cy + 2 * _LBL_H, base - 2 * _LBL_H,
            ]))
            meta.append((cx, anchor, ctext, ccls))
        ys = _place_labels(cands_per_label, occupied)
        for (cx, anchor, ctext, ccls), ty in zip(meta, ys):
            co += (f'<text x="{cx:.0f}" y="{ty:.0f}" class="{ccls}" '
                   f'text-anchor="{anchor}">{_html.escape(ctext)}</text>')
    return (
        f'<svg viewBox="0 0 {_VW} {_VH}" role="img" aria-label="{label}" '
        f'class="ind-chart">{grid}{axes}{main_line}{second}{dot}{co}{tip}</svg>'
    )


def _monthly_chart(pts: list[dict]) -> str:
    """수출액 + 12M MA, with axis labels + 최신/저점/MA callouts (reference)."""
    vs = [(i, p["exp"]) for i, p in enumerate(pts) if p.get("exp") is not None]
    ma = [(i, p["ma12"]) for i, p in enumerate(pts) if p.get("ma12") is not None]
    titles = [f"{p['ym']} | {_eokusd(p['exp'])} | YoY {_pct(p.get('yoy'))}" for p in pts]
    if len(vs) < 2:
        return ""
    exps = [v for _, v in vs]
    hi, lo = max(exps), min(exps)
    # callouts: latest value, trough, and the latest MA
    li, lv = vs[-1]
    lo_i, lo_v = min(vs, key=lambda t: t[1])
    callouts = [
        (li, lv, f"최신 {_eokusd(lv)}", "ind-cl"),
        (lo_i, lo_v, f"저점 {_eokusd(lo_v)}", "ind-cl"),
    ]
    if ma:
        mi, mv = ma[-1]
        callouts.append((mi, mv, f"MA {_eokusd(mv)}", "ind-cl-ma"))
    return _line_svg(
        vs, titles, dashed_second=ma, label="수출액 추이와 12개월 이동평균",
        x_first=_dot_ym(pts[0]["ym"]), x_last=_dot_ym(pts[-1]["ym"]),
        y_hi_lbl=_eokusd(hi), y_lo_lbl=_eokusd(lo), callouts=callouts,
    )


def _ttm_chart(pts: list[dict]) -> str:
    """12M TTM 수출액 line (only where TTM defined)."""
    vs = [(i, p["ttm"]) for i, p in enumerate(pts) if p.get("ttm") is not None]
    if len(vs) < 2:
        return ""
    tps = [p for p in pts if p.get("ttm") is not None]
    titles = [f"{p['ym']} | TTM {_eokusd(p['ttm'])} | TTM YoY {_pct(p.get('ttm_yoy'))}"
              for p in tps]
    vals = [v for _, v in vs]
    li, lv = vs[-1]
    return _line_svg(
        vs, titles, label="12개월 TTM 수출액 추이",
        x_first=_dot_ym(tps[0]["ym"]), x_last=_dot_ym(tps[-1]["ym"]),
        y_hi_lbl=_eokusd(max(vals)), y_lo_lbl=_eokusd(min(vals)),
        callouts=[(li, lv, f"최신 {_eokusd(lv)}", "ind-cl")],
    )


def _ttm_yoy_chart(pts: list[dict]) -> str:
    """12M TTM YoY 성장률 line (violet, matches reference). Needs ≥24 mo."""
    vs = [(i, p["ttm_yoy"]) for i, p in enumerate(pts) if p.get("ttm_yoy") is not None]
    if len(vs) < 2:
        return ""
    tps = [p for p in pts if p.get("ttm_yoy") is not None]
    titles = [f"{p['ym']} | TTM YoY {_pct(p.get('ttm_yoy'))}" for p in tps]
    vals = [v for _, v in vs]
    li, lv = vs[-1]
    return _line_svg(
        vs, titles, label="12개월 TTM YoY 성장률",
        x_first=_dot_ym(tps[0]["ym"]), x_last=_dot_ym(tps[-1]["ym"]),
        y_hi_lbl=f"{max(vals):.0f}%", y_lo_lbl=f"{min(vals):.0f}%",
        callouts=[(li, lv, f"{_pct(lv)}", "ind-cl")],
        main_class="ind-ttm-yoy-line", dot_class="ind-ttm-yoy-dot",
    )


def _stat_row(points: list[dict], lab: str = "수출액") -> str:
    """<dl>: 최신월·수출/수입액·YoY·ΔYoY·3M평균·12M MA·MA대비
    (mirrors the reference card). lab = 방향 라벨 (수입 카드 2026-06-13)."""
    latest = points[-1]
    exp, ma = latest["exp"], latest.get("ma12")
    ma_rel = ((exp - ma) / ma * 100.0) if ma else None
    m = momentum(points)
    def cls(v): return "pos" if (v or 0) > 0 else "neg"
    # 최신월 잠정/확정 — 상세 패널에도 () 표기(사용자 2026-06-12 '5월부분에
    # () 잠정인지 확정인지'). 짧은 단어만 셀에, 전체 안내는 title 툴팁.
    _st = _month_status_label(latest["ym"])
    _st_short = _st.split("(")[0]   # 잠정 / 확정
    _prov = (f" <span class='ind-mstatus' title='{_html.escape(_st)}'>"
             f"({_html.escape(_st_short)})</span>")
    return (
        "<dl class='ind-stats'>"
        f"<div><dt>최신월</dt><dd>{_html.escape(_dot_ym(latest['ym']))}{_prov}</dd></div>"
        f"<div><dt>{lab}</dt><dd>{_eokusd(exp)}</dd></div>"
        f"<div><dt>YoY</dt><dd class='{cls(latest.get('yoy'))}'>{_pct(latest.get('yoy'))}</dd></div>"
        f"<div><dt>ΔYoY</dt><dd class='{cls(latest.get('dyoy'))}'>{_pct(latest.get('dyoy'),'%p')}</dd></div>"
        f"<div><dt>3개월 평균 YoY</dt><dd class='{cls(m['yoy3'])}'>{_pct(m['yoy3'])}</dd></div>"
        f"<div><dt>3개월 평균 ΔYoY</dt><dd class='{cls(m['dyoy3'])}'>{_pct(m['dyoy3'],'%p')}</dd></div>"
        f"<div><dt>12M MA</dt><dd>{_eokusd(ma)}</dd></div>"
        f"<div><dt>MA대비</dt><dd>{_pct(ma_rel)}</dd></div>"
        "</dl>"
    )


def _yoy_bar_svg(pts: list[dict]) -> str:
    """YoY growth-rate bar chart (green up / red down), with a zero line —
    mirrors the reference's 2nd chart. Bars only where YoY is defined."""
    ys = [(i, p["yoy"]) for i, p in enumerate(pts) if p.get("yoy") is not None]
    if len(ys) < 2:
        return ""
    n_max = len(pts) - 1
    vals = [v for _, v in ys]
    hi = max(vals + [0]); lo = min(vals + [0])
    span = (hi - lo) or 1
    plot_w = _VW - _PAD_L - _PAD_R
    plot_h = _VH - _PAD_T - _PAD_B
    def x(i): return _PAD_L + (plot_w * i / n_max) if n_max else _PAD_L
    def y(v): return _PAD_T + plot_h * (1 - (v - lo) / span)
    zero_y = y(0)
    bw = max(2.0, plot_w / (n_max + 1) * 0.7)
    bars = []
    for i, v in ys:
        bx = x(i) - bw / 2
        if v >= 0:
            by, bh = y(v), zero_y - y(v)
            cls = "ind-bar-pos"
        else:
            by, bh = zero_y, y(v) - zero_y
            cls = "ind-bar-neg"
        p = pts[i]
        bars.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" '
            f'height="{max(bh,0.6):.1f}" class="{cls}">'
            f'<title>{_html.escape(p["ym"])} | YoY {_pct(p.get("yoy"))} | '
            f'ΔYoY {_pct(p.get("dyoy"),"%p")}</title></rect>'
        )
    zero = (f'<line x1="{_PAD_L}" y1="{zero_y:.1f}" x2="{_VW-_PAD_R}" '
            f'y2="{zero_y:.1f}" class="ind-zero-line"/>')
    # axis labels: top=+hi%, bottom=lo%, X dates, latest YoY callout
    li, lv = ys[-1]
    axes = (
        f'<text x="2" y="{_PAD_T+4:.0f}" class="ind-axis">+{hi:.0f}%</text>'
        f'<text x="2" y="{_PAD_T+plot_h:.0f}" class="ind-axis">{lo:.0f}%</text>'
        f'<text x="{_PAD_L}" y="{_VH-3}" class="ind-axis">{_html.escape(_dot_ym(pts[0]["ym"]))}</text>'
        f'<text x="{_VW-_PAD_R}" y="{_VH-3}" class="ind-axis" text-anchor="end">'
        f'{_html.escape(_dot_ym(pts[-1]["ym"]))}</text>'
        f'<text x="{x(li):.0f}" y="{max(y(lv)-4,9):.0f}" class="ind-cl" '
        f'text-anchor="end">{_pct(lv)}</text>'
    )
    return (f'<svg viewBox="0 0 {_VW} {_VH}" role="img" aria-label="전년동월 대비 성장률" '
            f'class="ind-chart">{zero}{axes}{"".join(bars)}</svg>')


def _attach_units(pts: list[dict], wgts: dict | None) -> None:
    """pts 에 단가 'unit'($/kg) 부착 — wgts {ym: kg} (aggregate_by_mti
    산출). 키는 'YYYY-MM'/'YYYYMM' 양쪽 수용 (industry_series 가 ym 을
    정규화하므로). 중량 0/부재 달은 생략 (graceful). in-place, 순수."""
    if not wgts:
        return
    norm = {}
    for k, v in wgts.items():
        s = str(k).replace("-", "")
        if len(s) >= 6 and v:
            norm[f"{s[:4]}-{s[4:6]}"] = v
    for p in pts:
        w = norm.get(p["ym"])
        if w and p.get("exp"):
            p["unit"] = p["exp"] / w


def _unit_str(v) -> str:
    """단가 표기 — 품목별 스케일 편차가 커서 (벌크 $0.x ~ 반도체 $수천)
    크기별 자릿수: ≥$100 정수, ≥$1 소수2, 미만 소수3."""
    if v is None:
        return "—"
    if v >= 100:
        return f"${v:,.0f}/kg"
    if v >= 1:
        return f"${v:,.2f}/kg"
    return f"${v:.3f}/kg"


def _mti_extras(node: dict, pts: list[dict], amt_th: str,
                mti6: str = "", channel: list[str] | None = None) -> str:
    """품목 카드 meta 하단 부가줄 (사용자 2026-06-13 텔레그램 채널
    미러): 💲 최신월 단가 + 전년동월 대비, 🧾 구성 HS 코드(MTI 는 HS10
    여러 개의 묶음이라 자기 HS 가 없음 — 구성 목록이 답), 🏢 관련기업
    (큐레이션 + 채널 알림 두 출처 별도 라인). 데이터/매핑 없으면 해당
    줄 생략 — 빈 문자열 가능."""
    lines = []
    # 단가 — 최신 unit 있는 포인트 + 전년동월 unit 비교
    with_unit = [p for p in pts if p.get("unit")]
    if with_unit:
        latest = with_unit[-1]
        ago_ym = _prev_year_month(latest["ym"])
        ago = next((p["unit"] for p in with_unit if p["ym"] == ago_ym), None)
        yoy_html = ""
        if ago:
            chg = (latest["unit"] - ago) / ago * 100.0
            cls = "pos" if chg > 0 else ("neg" if chg < 0 else "")
            yoy_html = (f" <span class='{cls}'>{_pct(chg)}</span>"
                        f" <span class='ind-extra-sub'>(전년동월 {_unit_str(ago)})</span>")
        lines.append(
            f"<p class='ind-extra'>💲 {amt_th} 단가 <b>{_unit_str(latest['unit'])}</b>"
            f"{yoy_html}</p>")
    # 구성 HS 코드 — 이 MTI 를 구성하는 관세청 HS10 leaf 목록
    members = _mti_hs_members().get(mti6) or []
    if members:
        shown = ", ".join(members[:3])
        more = f" 외 {len(members) - 3}건" if len(members) > 3 else ""
        full = ", ".join(members[:30])
        lines.append(
            f"<p class='ind-extra'>🧾 구성 HS <span class='ind-extra-sub' "
            f"title='{_html.escape(full)}'>{_html.escape(shown)}{more}"
            f" (총 {len(members)}개 HS10)</span></p>")
    # 관련기업 ① 수동 큐레이션 (대표 상장사)
    try:
        from trade.mti_companies import companies_for
        cos = companies_for(node.get("name") or "")
    except Exception:
        cos = []
    if cos:
        chips = " · ".join(f"<b>{_html.escape(c)}</b>" for c in cos)
        lines.append(
            "<p class='ind-extra'>🏢 관련기업(큐레이션): " + chips
            + " <span class='ind-extra-sub' title='품목명 키워드 기반 수동 "
              "큐레이션 — 자동 추정 아님'>ⓘ</span></p>")
    # 관련기업 ② 채널 알림 조인 (BeOn 수출입 알림이 같은 품목명을 다룬
    # 기업들 — store.db 라이브, 큐레이션과 출처 구분 표기, 중복 제거)
    channel = [c for c in (channel or []) if c not in set(cos)]
    if channel:
        chips = " · ".join(f"<b>{_html.escape(c)}</b>" for c in channel)
        lines.append(
            "<p class='ind-extra'>📨 관련기업(채널 알림): " + chips
            + " <span class='ind-extra-sub' title='수출입 알림 채널이 이 "
              "품목명으로 다룬 기업 합집합 — 채널 큐레이션 기반'>ⓘ</span></p>")
    return "".join(lines)


def _raw_table(pts: list[dict], lab: str = "수출액") -> str:
    """월별 원자료 table (가로 스크롤): 수출/수입액·YoY·ΔYoY·12M MA·MA대비
    (+단가 $/kg — unit 부착된 품목 카드만)."""
    months = [p["ym"] for p in pts]
    def row(label, fn, cls_fn=None):
        cells = []
        for p in pts:
            val = fn(p)
            c = f" class='{cls_fn(p)}'" if cls_fn else ""
            cells.append(f"<td{c}>{val}</td>")
        return f"<tr><th>{label}</th>{''.join(cells)}</tr>"
    def cls(v):
        return "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "")
    head = "<tr><th>구분</th>" + "".join(
        f"<th>{_html.escape(_dot_ym(m))}</th>" for m in months) + "</tr>"
    body = (
        row(lab, lambda p: _eokusd(p["exp"]))
        + row("YoY", lambda p: _pct(p.get("yoy")), lambda p: cls(p.get("yoy")))
        + row("ΔYoY", lambda p: _pct(p.get("dyoy"), "%p"), lambda p: cls(p.get("dyoy")))
        + row("12M MA", lambda p: _eokusd(p.get("ma12")))
        + row("MA 대비", lambda p: _pct(
            ((p["exp"] - p["ma12"]) / p["ma12"] * 100.0) if p.get("ma12") else None),
            lambda p: cls(((p["exp"] - p["ma12"]) / p["ma12"] * 100.0)
                          if p.get("ma12") else None))
    )
    if any(p.get("unit") for p in pts):
        body += row("단가($/kg)", lambda p: _unit_str(p.get("unit")))
    # Always-visible (no details) matching the reference. Horizontal scroll
    # for the wide month range, sticky first column.
    return (f"<div class='ind-raw'><div class='ind-raw-title'>월별 원자료</div>"
            f"<div class='ind-raw-scroll'><table class='ind-table'>"
            f"<thead>{head}</thead><tbody>{body}</tbody></table></div></div>")


def _card_body(pts: list[dict], lab: str = "수출액", extra: str = "") -> str:
    """Reference layout — a flat 3-column row: meta | chart-1 | chart-2.
    Each chart cell holds a monthly panel and a ttm panel; the toggle
    swaps which is visible (chart-1: 수출액+MA ↔ TTM 수출액; chart-2:
    YoY 막대 ↔ TTM YoY). Wide charts, compact meta — matches the original
    (meta 0.95fr : chart1 1.25fr : chart2 1fr).

    extra = meta 하단 부가 HTML (품목 카드의 단가·관련기업, _mti_extras
    산출) — 산업 카드는 빈 문자열(무변경)."""
    interp = interpret(pts)
    note = ""
    if interp["signal_label"]:
        note = (f"<p class='ind-signal'><b>{_html.escape(interp['signal_label'])}</b>"
                f" · {_html.escape(interp['signal_text'])}</p>")
    summary = (f"<p class='ind-summary'>{_html.escape(interp['summary'])}</p>"
               if interp["summary"] else "")
    toggle = (
        "<div class='ind-toggle' role='group'>"
        "<button type='button' class='ind-tg-btn is-active' data-ind-view='monthly'>월별</button>"
        "<button type='button' class='ind-tg-btn' data-ind-view='ttm'>12M TTM</button>"
        "</div>"
    )
    meta = f"<div class='ind-meta'>{toggle}{_stat_row(pts, lab)}{summary}{note}{extra}</div>"

    monthly = _monthly_chart(pts)
    bars = _yoy_bar_svg(pts)
    ttm = _ttm_chart(pts)
    ttm_yoy = _ttm_yoy_chart(pts)

    def cell(title_m, svg_m, title_t, svg_t, na_t):
        """One chart column: monthly panel + ttm panel (toggle-swapped)."""
        m = (f"<div class='ind-panel ind-monthly'>"
             f"<div class='ind-chart-title'>{title_m}</div>{svg_m}</div>"
             if svg_m else "<div class='ind-panel ind-monthly'></div>")
        if svg_t:
            t = (f"<div class='ind-panel ind-ttm' hidden>"
                 f"<div class='ind-chart-title'>{title_t}</div>{svg_t}</div>")
        else:
            t = (f"<div class='ind-panel ind-ttm' hidden>"
                 f"<div class='ind-na'>{na_t}</div></div>")
        return f"<div class='ind-chart-cell'>{m}{t}</div>"

    cell1 = cell(f"{lab} 및 12개월 이동평균", monthly,
                 f"12개월 TTM {lab}", ttm,
                 "TTM은 24개월 이상 데이터가 필요합니다.")
    cell2 = cell("YoY 성장률", bars,
                 "12개월 TTM YoY 성장률", ttm_yoy,
                 "TTM YoY는 24개월 이상 데이터가 필요합니다.")
    return (f"<div class='ind-row'>{meta}{cell1}{cell2}</div>"
            f"{_raw_table(pts, lab)}")


def init_db(conn) -> None:
    """One-row-per-industry store of the aggregated monthly series (export
    + import). The 24-month chapter fetch is too heavy for render time, so
    the scan job precomputes & stores; the dashboard reads this."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS industry_series ("
        "industry TEXT PRIMARY KEY, months_json TEXT NOT NULL, "
        "imports_json TEXT, updated_ts REAL)"
    )
    # backward-compat: add imports_json to a pre-existing table
    try:
        conn.execute("ALTER TABLE industry_series ADD COLUMN imports_json TEXT")
    except Exception:
        pass
    conn.commit()


def store(conn, by_industry: dict[str, dict[str, int]],
          by_import: dict[str, dict[str, int]] | None = None,
          now: float | None = None) -> int:
    """Replace the stored series with the latest aggregation (export +
    optional import). Returns rows written. Empty export is ignored
    (never wipes a good snapshot).

    TODO(아카이브): 현재 industry_series·mti_series는 '최신 스냅샷만'
    DELETE+INSERT로 갱신해 과거 추이를 직접 보관하지 않는다(매 호출
    by_industry에 들어온 months_json이 그대로 마지막 본 24개월). 운영자
    요청(2026-06): 산업트렌드/하위품목의 '월별 변천 아카이브'를 별도
    저장해 시점별 비교(예: 5월 확정치 발표 전후 산업 순위 변화, 잠정→
    확정 보정 추이)를 가능케 할 것. 설계 스케치:
      - industry_series_archive(industry, snapshot_ym, months_json, ts)
      - mti_series_archive(mti6, snapshot_ym, payload_json, import_json, ts)
      - 디스크/쓰기 폭증 방지: refresh_signals에서 fingerprint 변동
        틱에만 append, snapshot_ym 동일 시 UPSERT(달당 1행). cost.py 디스크
        라인이 자동 반영.
      - 대시보드 산업트렌드에 '🗄 월별 변천' 탐색 UI(snapshot 선택).
    customs_surge_archive(라이브 강등 → 무제한 보관)의 산업판 — 같은 패턴."""
    import time
    if not by_industry:
        return 0
    by_import = by_import or {}
    now = now if now is not None else time.time()
    init_db(conn)
    conn.execute("DELETE FROM industry_series")
    for ind, months in by_industry.items():
        imp = by_import.get(ind)
        conn.execute(
            "INSERT INTO industry_series "
            "(industry, months_json, imports_json, updated_ts) VALUES (?,?,?,?)",
            (ind, _json.dumps(months, ensure_ascii=False),
             _json.dumps(imp, ensure_ascii=False) if imp else None, now),
        )
    conn.commit()
    return len(by_industry)


def load_stored(conn) -> dict[str, dict[str, int]]:
    """{industry: {ym: exp}} from the store, or {} when absent/empty."""
    try:
        cur = conn.execute("SELECT industry, months_json FROM industry_series")
    except Exception:
        return {}
    out: dict[str, dict[str, int]] = {}
    for ind, mj in cur.fetchall():
        try:
            out[ind] = _json.loads(mj)
        except Exception:
            continue
    return out


def store_mti(conn, by_mti: dict[str, dict],
              by_mti_import: dict[str, dict] | None = None,
              now: float | None = None) -> int:
    """Store MTI6 하위품목 export series + optional import series (each a
    JSON blob per MTI6). Separate table from industry_series. Empty export
    input ignored."""
    import time
    if not by_mti:
        return 0
    by_mti_import = by_mti_import or {}
    now = now if now is not None else time.time()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mti_series ("
        "mti6 TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
        "import_json TEXT, updated_ts REAL)"
    )
    try:
        conn.execute("ALTER TABLE mti_series ADD COLUMN import_json TEXT")
    except Exception:
        pass
    conn.execute("DELETE FROM mti_series")
    for mti6, node in by_mti.items():
        imp = by_mti_import.get(mti6)
        conn.execute(
            "INSERT INTO mti_series (mti6, payload_json, import_json, updated_ts) "
            "VALUES (?,?,?,?)",
            (mti6, _json.dumps(node, ensure_ascii=False),
             _json.dumps(imp, ensure_ascii=False) if imp else None, now),
        )
    conn.commit()
    return len(by_mti)


def load_mti_stored(conn) -> dict[str, dict]:
    """{mti6: {name,industry,months}} or {} when absent/empty."""
    try:
        cur = conn.execute("SELECT mti6, payload_json FROM mti_series")
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for mti6, pj in cur.fetchall():
        try:
            out[mti6] = _json.loads(pj)
        except Exception:
            continue
    return out


def load_mti_imports(conn) -> dict[str, dict]:
    """{mti6: {name,industry,months}} import series, or {} when absent/old
    schema (import_json NULL)."""
    try:
        cur = conn.execute("SELECT mti6, import_json FROM mti_series")
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for mti6, ij in cur.fetchall():
        if not ij:
            continue
        try:
            out[mti6] = _json.loads(ij)
        except Exception:
            continue
    return out


def load_stored_imports(conn) -> dict[str, dict[str, int]]:
    """{industry: {ym: imp}} from the store, or {} when absent/empty/old
    schema (imports_json NULL)."""
    try:
        cur = conn.execute("SELECT industry, imports_json FROM industry_series")
    except Exception:
        return {}
    out: dict[str, dict[str, int]] = {}
    for ind, mj in cur.fetchall():
        if not mj:
            continue
        try:
            out[ind] = _json.loads(mj)
        except Exception:
            continue
    return out


def _base_tag(y) -> str:
    """전년동월 ~0 기저효과 표지 — |YoY| ≥ 500% 는 수학적으로 맞아도
    (예: 에틸렌 +110,050% = 전년 ≈0) 추세 신호로 오독 위험 (사용자
    2026-06-13 '숫자 검증'). 값은 그대로 두고 맥락 배지만 부착."""
    try:
        if y is not None and abs(float(y)) >= 500.0:
            return ("<span class='ind-base-tag' title='전년동월이 ~0에 가까워 "
                    "비율이 과장됨 (기저효과) — 절대액 컬럼과 함께 볼 것'>기저</span>")
    except (TypeError, ValueError):
        pass
    return ""


def _summary_board(series: dict[str, list[dict]],
                   show_import_momentum: bool = True) -> str:
    """Top-of-tab summary board mirroring the reference header:
      - 4 classification boxes (초고성장/턴어라운드/부진 with %YoY chips,
        + a 분류 기준 explainer)
      - 2 derivative boxes (가속 확대 / 고성장 둔화·기울기 하락, ΔYoY chips)
    Pure presentation over the already-computed series. As 하위품목(B)/
    수입(C) land, their entries flow into the same series dict and appear
    here automatically — no board change needed."""
    rows = [(ind, pts[-1]) for ind, pts in series.items() if pts]

    def chip(name, val, suffix="%"):
        cl = "pos" if (val or 0) > 0 else "neg"
        return (f"<span class='ind-mini-chip'><b>{_html.escape(name)}</b> "
                f"<span class='{cl}'>{_pct(val, suffix)}</span></span>")

    # classification boxes — chips sorted by latest YoY desc
    by_cls: dict[str, list[tuple[str, float]]] = {}
    for ind, pts in series.items():
        if not pts:
            continue
        by_cls.setdefault(classify(pts), []).append((ind, pts[-1].get("yoy")))
    boxes = []
    for label, cls in _GROUPS:
        items = [t for t in by_cls.get(label, []) if t[1] is not None]
        if not items and label != "데이터부족":
            items = by_cls.get(label, [])
        if not items:
            continue
        items.sort(key=lambda t: (t[1] if t[1] is not None else -9e9), reverse=True)
        chips = "".join(chip(n, v) for n, v in items)
        boxes.append(
            f"<div class='ind-sbox ind-sbox-{cls}'><h3>{label}</h3>"
            f"<div class='ind-chip-wrap'>{chips}</div></div>"
        )
    # 분류 기준 explainer (verbatim from reference)
    boxes.append(
        "<div class='ind-sbox ind-sbox-info'><h3>분류 기준</h3>"
        "<p>최신 YoY · 3개월 평균 ΔYoY(가속도)로 분류합니다. YoY가 높아도 "
        "ΔYoY가 마이너스로 꺾이면 기대의 가속이 둔화되는 신호로 봅니다.</p></div>"
    )

    # derivative boxes — by latest ΔYoY (가속 vs 둔화)
    dyoy = [(ind, pts[-1].get("dyoy")) for ind, pts in series.items()
            if pts and pts[-1].get("dyoy") is not None]
    accel = sorted([t for t in dyoy if t[1] > 0], key=lambda t: t[1], reverse=True)[:6]
    decel = sorted([t for t in dyoy if t[1] < 0], key=lambda t: t[1])[:6]
    deriv = ""
    if accel:
        deriv += ("<div class='ind-sbox ind-sbox-accel'><h3>가속 확대</h3>"
                  f"<div class='ind-chip-wrap'>"
                  f"{''.join(chip(n,v,'%p') for n,v in accel)}</div></div>")
    if decel:
        deriv += ("<div class='ind-sbox ind-sbox-decel'><h3>고성장 둔화/기울기 하락</h3>"
                  f"<div class='ind-chip-wrap'>"
                  f"{''.join(chip(n,v,'%p') for n,v in decel)}</div></div>")
    # C: import-surge box — 수입 YoY 급증 = 생산/투자 선행신호. Each surging
    # industry is annotated with its TOP DRIVER MTI subitem (어떤 세부품목
    # 수입이 끌었나, e.g. 반도체 ← 반도체제조용장비 +120%) computed from
    # the per-MTI import series. No extra API calls — same scan leaves.
    imp_box = ""
    if _import_series:
        # top import-YoY driver MTI per industry
        driver: dict[str, tuple[str, float]] = {}
        for mti6, mpts in _import_mti_series.items():
            if not mpts or mpts[-1].get("yoy") is None:
                continue
            ind = _import_mti_industry.get(mti6, "")
            nm = _import_mti_name.get(mti6, mti6)
            y = mpts[-1]["yoy"]
            cur = mpts[-1].get("exp") or 0
            # require a non-trivial import base so a tiny line doesn't 'drive'
            if cur < 10_000_000:
                continue
            if ind not in driver or y > driver[ind][1]:
                driver[ind] = (nm, y, mti6)

        # 산업별 수출 YoY(괴리·동행/선행후보 판정용)
        exp_yoy = {ind: (pts[-1].get("yoy") if pts else None)
                   for ind, pts in series.items()}
        # 선택: 산업 수입 ≥ 하한 AND 수입 YoY ≥ +20% → 괴리(수입YoY−수출YoY)
        # 내림차순. 이미 수출 강한 산업의 수입↑(동행)보다, 수출은 약한데 수입이
        # 먼저 튀는 산업(⚡선행후보)을 위로 올린다.
        cand = []
        if not show_import_momentum:
            _import_items = {}
        else:
            _import_items = _import_series
        for ind, ipts in _import_items.items():
            if not ipts:
                continue
            iy = ipts[-1].get("yoy")
            ival = ipts[-1].get("exp") or 0
            if iy is None or iy < 20.0 or ival < _IMP_MIN_USD:
                continue
            ey = exp_yoy.get(ind)
            div = iy - (ey if ey is not None else 0.0)
            lead = (ey is None) or (ey < _HIGH_GROWTH_YOY)   # 수출 안 강함 → 선행후보
            cand.append((div, ind, iy, lead))
        cand.sort(key=lambda t: t[0], reverse=True)
        surges = cand[:8]
        if surges:
            rows_html = []
            for _div, ind, iy, lead in surges:
                tcls, ttxt = ("lead", "⚡선행후보") if lead else ("coin", "동행")
                tag = (f"<span class='ind-imp-tag ind-imp-tag-{tcls}'>{ttxt}</span>")
                drv = driver.get(ind)
                if drv:
                    cap = ("<span class='ind-imp-cap'>자본재</span>"
                           if _is_capital_good(drv[0]) else "")
                    sub = (f"<span class='ind-imp-drv'>← {_html.escape(drv[0])}"
                           f"{_code_chip(drv[2])} "
                           f"{_pct(drv[1])}{_base_tag(drv[1])}{cap}</span>")
                else:
                    sub = ""
                rows_html.append(
                    f"<div class='ind-imp-row'>{chip(ind, iy)}{tag}{sub}</div>")
            imp_box = (
                "<div class='ind-sbox ind-sbox-imp'><h3>📥 수입 모멘텀 (수출 대비 앞서가는 순)</h3>"
                "<p class='ind-sbox-sub'><b>산정</b>: 수입 YoY≥+20%·수입≥1억$ 산업을 "
                "(수입YoY − 수출YoY) 큰 순. <b>⚡선행후보</b>=수출은 아직 약한데 수입이 먼저 "
                "급증(수출 차트에 안 보이는 선행 가능). <b>동행</b>=수출이 이미 강해 현재 생산 "
                "견인(수요견인). <b>자본재</b> 배지=설비·장비 수입(진짜 capex 선행). <b>←</b>는 "
                "그 산업 수입을 가장 끌어올린 세부품목(MTI).</p>"
                f"<div class='ind-imp-list'>{''.join(rows_html)}</div></div>")
    imp_grid = (f"<div class='ind-deriv-grid'>{imp_box}</div>"
                if imp_box else "")
    return (f"<div class='ind-summary-grid'>{''.join(boxes)}</div>"
            f"<div class='ind-deriv-grid'>{deriv}</div>"
            f"{imp_grid}")


# module-level handoff for the import series (set by render_industry_html
# before calling _summary_board — keeps _summary_board's signature stable
# while B/C grow what flows into it).
_import_series: dict[str, list[dict]] = {}
# per-MTI import series + lookups, for annotating each import-surge
# industry with its top driver subitem.
_import_mti_series: dict[str, list[dict]] = {}
_import_mti_industry: dict[str, str] = {}
_import_mti_name: dict[str, str] = {}


def render_subitem_html(by_mti: dict[str, dict],
                        rate_min_usd: int = 200_000_000,
                        amt_th: str = "수출",
                        lab: str = "수출액") -> str:
    """하위품목 TOP (MTI6 단위) — one level below industry, ranked by
    MoM(전월대비, 최근 모멘텀) so 기저효과에 휘둘리지 않음:
    📈급등률(MoM% 양수 상위 30, 수출 ≥하한) + 💵급증액(전월대비 Δ$ 상위
    30, 수출 ≥하한). 두 표 모두 수출 하한 적용.
    Each row: 품목명(산업) · 수출 · 전월대비. Returns '' when no data."""
    if not by_mti:
        return ""
    # 채널 알림 조인 페어 — 렌더당 1회 로드 (store.db 부재 시 [] graceful)
    try:
        from trade.mti_companies import channel_companies_for, load_channel_pairs
        _ch_pairs = load_channel_pairs()
    except Exception:
        _ch_pairs = []
        channel_companies_for = lambda name, pairs: []  # noqa: E731
    rows = []
    pts_by_mti: dict[str, list[dict]] = {}
    extras_by_mti: dict[str, str] = {}
    for mti6, node in by_mti.items():
        months = node["months"]
        pts = industry_series({mti6: months}).get(mti6) or []
        if not pts:
            continue
        # 단가($/kg) + 구성 HS + 관련기업(큐레이션·채널) — 카드 meta
        # 부가줄. wgts 없는 옛 스냅샷/미수록 품목은 줄 생략 (graceful).
        _attach_units(pts, node.get("wgts"))
        pts_by_mti[mti6] = pts
        extras_by_mti[mti6] = _mti_extras(
            node, pts, amt_th, mti6=mti6,
            channel=channel_companies_for(node.get("name") or "", _ch_pairs))
        latest = pts[-1]
        # MoM = 최신 확정월 vs 달력상 직전월. 직전월 포인트가 없으면(데이터
        # 공백) MoM 미정의 → 두 랭킹표에서 제외. 미래 미발표월은 애초에
        # 포인트로 생기지 않으므로 latest는 항상 최신 확정월.
        prev_exp = next((p["exp"] for p in pts
                         if p["ym"] == _prev_month(latest["ym"])), None)
        mom = (((latest["exp"] - prev_exp) / prev_exp * 100.0)
               if (prev_exp and prev_exp > 0) else None)
        mom_delta = (latest["exp"] - prev_exp) if prev_exp is not None else None
        rows.append({
            "mti6": mti6,
            "name": node["name"], "industry": node["industry"],
            "exp": latest["exp"], "mom": mom, "mom_delta": mom_delta,
        })
    if not rows:
        return ""

    def chip_rows(items, metric):
        # 행 클릭 → 풀 카드 상세 토글 (사용자 2026-06-13 '텔레그램 채널처럼
        # 품목별 차트+표' — TOP10 풀카드와 동일 _card_body 를 랭킹 전 행에
        # 확장, 서버 렌더 hidden·표시된 행만이라 비용 bound).
        out = []
        for r in items:
            raw = r["mom"] if metric == "mom" else r["mom_delta"]
            val = _pct(raw) if metric == "mom" else "Δ" + _eokusd(raw)
            cl = "pos" if (raw or 0) > 0 else "neg"
            out.append(
                f"<tr class='ind-mti-row' title='클릭 → 차트·월별 상세'>"
                f"<td>{_html.escape(r['name'])}{_code_chip(r['mti6'])}"
                f" <span class='ind-mti-more'>▸</span></td>"
                f"<td class='ind-sub-ind'>{_html.escape(r['industry'])}</td>"
                f"<td>{_eokusd(r['exp'])}</td>"
                f"<td class='{cl}'>{val}</td></tr>"
                f"<tr class='ind-mti-d' hidden><td colspan='4'>"
                + _card_body(pts_by_mti[r["mti6"]], lab,
                             extra=extras_by_mti.get(r["mti6"], ""))
                + "</td></tr>")
        return "".join(out)

    # 급등률: 수출 ≥ 하한 + 전월대비(MoM) 양수 중 MoM% 내림차순 상위 30.
    # 고정 %컷(예전 +30%)은 두지 않음 — MoM에선 +30%가 과도하게 빡빡해
    # 표가 거의 비어버려서. '가장 빨리 오른 것 순'으로 항상 채움.
    rate = sorted([r for r in rows
                   if r["mom"] is not None and r["mom"] > 0
                   and (r["exp"] or 0) >= rate_min_usd],
                  key=lambda r: r["mom"], reverse=True)[:30]
    # 급증액: 수출 ≥ 하한(급등률과 동일) + 전월대비 Δ$ 내림차순 상위 30.
    # 소액 기저 품목이 큰 변화율로 끼는 것을 막기 위해 같은 하한 적용.
    amount = sorted([r for r in rows
                     if r["mom_delta"] is not None
                     and (r["exp"] or 0) >= rate_min_usd],
                    key=lambda r: r["mom_delta"], reverse=True)[:30]

    def tbl(title, items, metric):
        if not items:
            return ""
        th = "전월대비" if metric == "mom" else "증감액"
        return (f"<details class='ind-sub-card' open><summary>{title} ({len(items)})</summary>"
                f"<div class='ind-raw-scroll'><table class='ind-table'><thead>"
                f"<tr><th>품목(MTI)</th><th>산업</th><th>{amt_th}</th><th>{th}</th></tr></thead>"
                f"<tbody>{chip_rows(items, metric)}</tbody></table></div></details>")

    # TOP 10 풀 카드 (수출액 큰 순) — 산업 카드와 완전히 동일한 형식
    # (_card_body 재사용: 차트+TTM 토글+8지표+해석문+원자료표). 이름은
    # '품목 (산업)'으로 출처를 보임.
    top10 = sorted(rows, key=lambda r: r["exp"], reverse=True)[:10]
    cards = []
    for r in top10:
        pts = pts_by_mti[r["mti6"]]
        cls = {"초고성장/강세": "hot", "턴어라운드 후보": "turn",
               "부진/재하락": "down"}.get(classify(pts), "na")
        label = classify(pts)
        title = (f"{r['name']}{_code_chip(r['mti6'])} "
                 f"<small class='ind-sub-ind'>({r['industry']})</small>")
        cards.append(
            "<section class='ind-card'>"
            f"<div class='ind-head'><h3>{title}</h3>"
            f"<span class='ind-badge ind-badge-{cls}'>{label}</span></div>"
            + _card_body(pts, lab, extra=extras_by_mti.get(r["mti6"], ""))
            + "</section>"
        )
    cards_html = ("<div class='ind-cards'>" + "".join(cards) + "</div>") if cards else ""

    return (
        "<h2 class='ind-group ind-group-hot'>하위품목 (MTI 세분)</h2>"
        "<div class='ind-sub-note'>20개 산업 아래 세부 품목(D램·낸드·웨이퍼 등 "
        f"MTI 6자리 — 이름 옆 회색 칩이 MTI 코드, 카드 안 '구성 HS'가 이 품목을 "
        f"이루는 관세청 HS10 코드들). {lab} TOP 10은 풀 카드로, 전체는 급등률·"
        "증감액 랭킹표(행 클릭 = 차트·월별 상세) — 산업이 가려버리는 '산업 안의 "
        "스타 품목'을 발굴. 카드에 단가($/kg, 관세청 중량 기반)·관련기업"
        "(수동 큐레이션 + 수출입 알림 채널 조인 두 출처)은 데이터/수록 품목에 "
        "한해 표시. 📥 <b>CSV</b>(상단 버튼, "
        "산업 탭)는 품목 <b>요약</b>(품목당 1행·수출+수입 양방향 전체 지표: "
        "급등률 MoM%·급증액 MoMΔ$·YoY·12M MA·단가·구성HS·관련기업)+<b>월별 "
        "원시</b> 두 파일로 내려받아 엑셀에서 바로 열림.</div>"
        + cards_html
        + "<div class='ind-sub-wrap'>"
        + tbl("📈 급등률 (MoM↑ 상위, " + amt_th + " ≥" + _eokusd(rate_min_usd) + ")", rate, "mom")
        + tbl("💵 급증액 (전월대비, " + amt_th + " ≥" + _eokusd(rate_min_usd) + ")", amount, "amount")
        + "</div>"
    )


# 품목(MTI) 엑셀 export 컬럼 헤더 — JS 가 동일 순서로 prepend (테스트가 동기 가드).
# 사용자 2026-06-13: 'CSV 는 대시보드가 담는 모든 정보를' + '수입 급등률 TOP·
# 급증액 TOP 도 포함'. 카드 + 두 랭킹표(급등률=MoM%, 급증액=MoMΔ$)를 수출·수입
# 양방향 전체 지표로. 공통 메타 → 수출 10지표 → 수입 10지표.
_MTI_DIR_METRIC_COLS = [
    "$", "MoM%(급등률)", "MoMΔ$(급증액)", "YoY%", "ΔYoY%p",
    "3개월평균YoY%", "12M MA$", "MA대비%", "단가$/kg", "전년동월단가$/kg",
]
MTI_SUMMARY_HEADER = (
    ["품목", "MTI", "산업", "최신월", "구성HS",
     "관련기업(큐레이션)", "관련기업(채널)"]
    + [f"수출{c}" for c in _MTI_DIR_METRIC_COLS]
    + [f"수입{c}" for c in _MTI_DIR_METRIC_COLS]
)
MTI_MONTHLY_HEADER = [
    "품목", "MTI", "산업", "월", "수출$", "수입$",
    "수출중량kg", "수입중량kg", "수출단가$/kg", "수입단가$/kg",
]


def _xnum(v, nd: int = 1):
    """export 수치 정리 — None 보존(JS null→공란), 수치는 반올림. 순수."""
    return round(v, nd) if isinstance(v, (int, float)) else None


def _norm_ym_map(d: dict) -> dict:
    """{ym: v} 키를 'YYYY-MM' 로 정규화 (industry_series 와 동일 규약)."""
    out = {}
    for k, v in (d or {}).items():
        s = str(k).replace("-", "")
        if len(s) >= 6:
            out[f"{s[:4]}-{s[4:6]}"] = v
    return out


def _dir_metrics(node: dict | None) -> dict:
    """한 방향(수출 or 수입) 노드 → 카드·랭킹 지표 dict + 월별 맵. node=
    {months,wgts}. industry_series 가 값을 'exp' 로 명명하므로 수입도 동일
    경로 재사용(값 의미만 방향별). 빈/무포인트 → {}. 순수."""
    if not node:
        return {}
    months = node.get("months") or {}
    pts = industry_series({"_": months}).get("_") or []
    if not pts:
        return {}
    _attach_units(pts, node.get("wgts"))               # 'unit' = 금액/중량
    latest = pts[-1]
    prev = next((p["exp"] for p in pts
                 if p["ym"] == _prev_month(latest["ym"])), None)
    mom = ((latest["exp"] - prev) / prev * 100.0) if (prev and prev > 0) else None
    mom_d = (latest["exp"] - prev) if prev is not None else None
    m = momentum(pts)
    ma = latest.get("ma12")
    ma_pct = ((latest["exp"] - ma) / ma * 100.0) if ma else None
    ago_unit = next((p.get("unit") for p in pts
                     if p["ym"] == _prev_year_month(latest["ym"])), None)
    wnorm = _norm_ym_map(node.get("wgts"))
    return {
        "ym": latest["ym"],
        "row": [
            latest["exp"], _xnum(mom), mom_d,
            _xnum(latest.get("yoy")), _xnum(latest.get("dyoy")),
            _xnum(m.get("yoy3")), _xnum(ma, 0), _xnum(ma_pct),
            _xnum(latest.get("unit"), 3), _xnum(ago_unit, 3),
        ],
        "monthly": {p["ym"]: (p["exp"], wnorm.get(p["ym"], 0), p.get("unit"))
                    for p in pts},
    }


_EMPTY_DIR_ROW = [None] * len(_MTI_DIR_METRIC_COLS)


def mti_export_rows(by_mti: dict, by_mti_imp: dict | None = None,
                    channel_pairs: list | None = None) -> tuple[list, list]:
    """품목(MTI) 엑셀 export 2종 (사용자 2026-06-13 '이런것들 엑셀로' + 'CSV 는
    대시보드가 담는 모든 정보를·수입 급등률/급증액 TOP 포함'). channel_pairs
    주입 시 순수·결정적:
    - summary (MTI당 1행): 공통 메타(구성HS·관련기업) + 수출·수입 **양방향**
      카드/랭킹 지표 각 10종(급등률=MoM%·급증액=MoMΔ$ 포함)
    - monthly (MTI×월 long): 수출/수입 금액·중량·단가, 직접 피벗용
    수출액 큰 순 정렬. 금액은 raw 달러(기존 산업 CSV 동일). Returns
    (summary_rows, monthly_rows), 헤더 미포함."""
    from trade.mti_companies import companies_for, channel_companies_for
    channel_pairs = channel_pairs or []
    imp = by_mti_imp or {}
    hs_rev = _mti_hs_members()
    summary: list[list] = []
    monthly: list[list] = []

    # 수출∪수입 키 (수입전용 품목도 포함), 수출액 큰 순 정렬
    order = sorted(set(by_mti) | set(imp),
                   key=lambda k: sum((by_mti.get(k, {}).get("months") or {}).values()),
                   reverse=True)
    for mti6 in order:
        node = by_mti.get(mti6) or {}
        inode = imp.get(mti6) or {}
        name = node.get("name") or inode.get("name") or ""
        ind = node.get("industry") or inode.get("industry") or ""
        ex = _dir_metrics(node)
        im = _dir_metrics(inode)
        if not ex and not im:
            continue
        summary.append(
            [name, mti6, ind, ex.get("ym") or im.get("ym"),
             " ".join(hs_rev.get(mti6, [])),
             " · ".join(companies_for(name)),
             " · ".join(channel_companies_for(name, channel_pairs))]
            + (ex.get("row") or list(_EMPTY_DIR_ROW))
            + (im.get("row") or list(_EMPTY_DIR_ROW)))
        exm = ex.get("monthly") or {}
        imm = im.get("monthly") or {}
        for ym in sorted(set(exm) | set(imm)):
            e = exm.get(ym) or (None, None, None)
            i = imm.get(ym) or (None, None, None)
            monthly.append([name, mti6, ind, ym, e[0], i[0], e[1], i[1],
                            _xnum(e[2], 3), _xnum(i[2], 3)])
    return summary, monthly


def _intra_views(series: dict[str, list[dict]], by_mti: dict[str, dict] | None) -> str:
    """산업 내부 동학 — 대산업군 평균이 가리는 세부품목 신호.
      A) 🚀 견인 품목 — 세부 YoY가 소속 산업 YoY를 크게 앞섬(반등 엔진).
      B) 🔄 교체·잠식 — 같은 산업 안에서 한 세부 ↑ · 다른 세부 ↓(점유율 전환).
    industry_series로 산업/세부 YoY를 구해 비교. by_mti 없으면 ''."""
    if not by_mti:
        return ""
    ind_yoy = {ind: (pts[-1].get("yoy") if pts else None) for ind, pts in series.items()}
    ind_cls = {ind: classify(pts) for ind, pts in series.items() if pts}

    by_ind_subs: dict[str, list[tuple[str, float, float, str]]] = {}
    subs: list[tuple[str, str, float, float, str]] = []
    for mti6, node in by_mti.items():
        pts = industry_series({mti6: node["months"]}).get(mti6) or []
        if not pts:
            continue
        y = pts[-1].get("yoy")
        e = pts[-1].get("exp") or 0
        if y is None or e < _SUB_REL_MIN_USD:
            continue
        ind, nm = node.get("industry", ""), node.get("name", mti6)
        subs.append((ind, nm, y, e, mti6))
        by_ind_subs.setdefault(ind, []).append((nm, y, e, mti6))

    # A) 견인 품목: 세부 YoY − 산업 YoY ≥ 격차. 산업 부진/턴어라운드인데 세부
    #    초고성장이면 '반등엔진'.
    lead = []
    for ind, nm, y, _e, m6 in subs:
        iy = ind_yoy.get(ind)
        if iy is None:
            continue
        gap = y - iy
        if gap >= _LEAD_GAP:
            engine = (ind_cls.get(ind) != "초고성장/강세") and (y >= _HIGH_GROWTH_YOY)
            lead.append((gap, ind, nm, y, iy, engine, m6))
    lead.sort(reverse=True)
    lead = lead[:10]

    # B) 교체·잠식: 한 산업 안에서 승자(↑) + 패자(↓) 공존 → 분산 큰 순.
    rot = []
    for ind, items in by_ind_subs.items():
        wins = [t for t in items if t[1] >= _ROT_WIN]
        loss = [t for t in items if t[1] <= _ROT_LOSS]
        if wins and loss:
            w = max(wins, key=lambda t: t[1])
            l = min(loss, key=lambda t: t[1])
            rot.append((w[1] - l[1], ind, w, l))
    rot.sort(reverse=True)
    rot = rot[:8]

    if not lead and not rot:
        return ""
    boxes = ""
    if lead:
        rows = "".join(
            f"<div class='ind-imp-row'><span class='ind-mini-chip'>"
            f"<b>{_html.escape(nm)}</b>{_code_chip(m6)} <span class='pos'>{_pct(y)}</span>{_base_tag(y)}</span>"
            f"<span class='ind-imp-drv'>{_html.escape(ind)} {_pct(iy)} · Δ{_pct(gap, '%p')}</span>"
            + ("<span class='ind-imp-cap'>반등엔진</span>" if engine else "")
            + "</div>"
            for gap, ind, nm, y, iy, engine, m6 in lead)
        boxes += ("<div class='ind-sbox ind-sbox-accel'><h3>🚀 산업 내 견인 품목</h3>"
                  "<p class='ind-sbox-sub'>세부품목이 소속 산업 평균보다 크게 앞서는 순"
                  "(세부 YoY − 산업 YoY ≥ +30%p). 산업이 부진/턴어라운드인데 세부가 "
                  "초고성장이면 <b>반등엔진</b> — 산업 숫자가 가린 진짜 동력.</p>"
                  f"<div class='ind-imp-list'>{rows}</div></div>")
    if rot:
        rows = "".join(
            f"<div class='ind-imp-row'><span class='ind-mini-chip'><b>{_html.escape(ind)}</b></span>"
            f"<span class='ind-imp-drv'>{_html.escape(w[0])}{_code_chip(w[3])} <span class='pos'>{_pct(w[1])}</span> ↑ "
            f"vs {_html.escape(l[0])}{_code_chip(l[3])} <span class='neg'>{_pct(l[1])}</span> ↓</span></div>"
            for _disp, ind, w, l in rot)
        boxes += ("<div class='ind-sbox ind-sbox-decel'><h3>🔄 산업 내 교체·잠식</h3>"
                  "<p class='ind-sbox-sub'>같은 산업 안에서 한 세부품목은 강세(↑)인데 다른 "
                  "세부품목은 약세(↓) — 점유율 전환·잠식. 산업 총액이 정체여도 내부 구조가 "
                  "바뀌는 신호.</p>"
                  f"<div class='ind-imp-list'>{rows}</div></div>")
    return ("<h2 class='ind-group ind-group-turn'>산업 내부 동학 (세부품목 상대)</h2>"
            f"<div class='ind-deriv-grid'>{boxes}</div>")


def motie_banner() -> str:
    """📋 산업부 잠정 원문 링크 배너 — 더 빠른 공식 잠정(매월 1일, 산업부
    20대 품목) 안내. 대시보드는 구분선 바로 밑에 별도 배치(사용자
    2026-06-12 순서 변경), 아카이브 스냅샷은 본문 내 기본 위치 유지."""
    return (
        "<div class='ind-motie'>📋 <b>더 빠른 잠정치</b>(매월 1일·산업부 20대 품목)는 "
        f"<a href='{_MOTIE_URL}' target='_blank' rel='noopener'>산업부 수출입동향 원문 →</a>"
        "<span class='ind-motie-note'> · 본 산업트렌드는 관세청 월간 통계 기준 — "
        "익월 초 잠정 적재 → ~15일 확정 정제 자동 반영</span>"
        "</div>"
    )


def render_industry_html(by_industry: dict[str, dict[str, int]],
                         by_import: dict[str, dict[str, int]] | None = None,
                         by_mti: dict[str, dict] | None = None,
                         by_mti_import: dict[str, dict] | None = None,
                         motie: bool = True) -> str:
    """Full 산업트렌드 panel: summary board (+ 수입 급증 신호 with per-MTI
    driver) + cards grouped by classification. Returns '' when no data."""
    global _import_series, _import_mti_series, _import_mti_industry, _import_mti_name
    _import_series = industry_series(by_import) if by_import else {}
    # per-MTI import series for the 수입 급증 driver annotation
    _import_mti_series, _import_mti_industry, _import_mti_name = {}, {}, {}
    if by_mti_import:
        for mti6, node in by_mti_import.items():
            s = industry_series({mti6: node["months"]}).get(mti6)
            if s:
                _import_mti_series[mti6] = s
                _import_mti_industry[mti6] = node.get("industry", "")
                _import_mti_name[mti6] = node.get("name", mti6)
    if not by_industry:
        return ""
    series = industry_series(by_industry)
    if not series:
        return ""
    imp_series = industry_series(by_import) if by_import else {}

    def _bucketize(ser):
        b: dict[str, list[tuple[str, list[dict]]]] = {g: [] for g, _ in _GROUPS}
        for ind, pts in ser.items():
            if not pts:
                continue
            b.setdefault(classify(pts), []).append((ind, pts))
        for g in b:
            b[g].sort(key=lambda t: t[1][-1]["exp"], reverse=True)
        return b

    buckets = _bucketize(series)

    out = []
    latest_ym = ""
    for _, pts in series.items():
        if pts and pts[-1]["ym"] > latest_ym:
            latest_ym = pts[-1]["ym"]
    # Label spells out WHY the latest month lags ~1 month: this API is
    # 관세청 CONFIRMED-only, published ~the 15th of the following month, so
    # early in any month the freshest confirmed data is the month before
    # last. Daily 4× polling reflects a new confirmed month within hours of
    # 최신월 잠정/확정 동적 라벨 (사용자 2026-06-12 '잠정/확정 구분 명시
    # + 두 단계 반영 동의'): 관세청 GW 월간 테이블엔 새 달이 익월 초(전체
    # 잠정)에 선행 적재되고 ~15일 확정 때 정제된다 — 옛 '확정치' 고정
    # 라벨은 월 전반(1~14일)에 사실과 어긋났음. (옛 조사주석 'GW엔 확정만
    # /최신월은 15일 후 등장'은 6/12 실관측 — 5월이 6/15 전에 이미 적재 —
    # 으로 정정.) 매일 폴링이 잠정→확정 정제를 자동 반영.
    status = _month_status_label(latest_ym)
    # 수출/수입 방향 토글 (사용자 2026-06-13 '수입도 똑같이 하위품목까지')
    dir_toggle = (
        "<div class='ind-toggle ind-dir-toggle' role='group'>"
        "<button type='button' class='ind-tg-btn ind-dir-btn is-active' "
        "data-ind-dir='all'>전체</button>"
        "<button type='button' class='ind-tg-btn ind-dir-btn' "
        "data-ind-dir='exp'>수출</button>"
        "<button type='button' class='ind-tg-btn ind-dir-btn' "
        "data-ind-dir='imp'>수입</button></div>"
    ) if imp_series else ""
    out.append(
        "<div class='ind-topbar'>"
        f"<div class='ind-note'>산업분류별 월 수출·수입액 · YoY/ΔYoY/12M 이동평균 "
        f"(HSK-MTI 연계표 기준) · 최신 <b>{_html.escape(latest_ym)}</b> "
        f"관세청 <b>{status}</b> · 매일 갱신 · "
        f"<b>금액 단위: 억 달러(1억$ = $100M)</b></div>"
        f"{dir_toggle}"
        "<div class='ind-legend'><span><i class='ind-lg-v'></i>금액</span>"
        "<span><i class='ind-lg-m'></i>12M MA</span></div>"
        "</div>"
    )
    # 📋 산업부 잠정 원문 배너 — 대시보드는 motie=False 로 받고 구분선
    # 바로 밑에 별도 삽입(사용자 2026-06-12 순서), 아카이브는 기본 유지.
    if motie:
        out.append(motie_banner())
    def _direction_block(ser, bks, mti, lab, amt_th, dir_key, hidden):
        """한 방향(수출/수입)의 전체 섹션 — 보드·인트라·그룹 카드·하위품목.
        수입 카드 = 수출과 동일 구조 (사용자 2026-06-13). 📥 수입 모멘텀
        박스는 cross-direction 비교(수입YoY−수출YoY)라 수출 뷰 전용 —
        수입 블록에 두면 자기 자신과 비교돼 무의미 (2026-06-13 버그 fix)."""
        blk = [f"<div class='ind-dirset' data-dir='{dir_key}'"
               + (" style='display:none'" if hidden else "") + ">"]
        blk.append(_summary_board(ser, show_import_momentum=(dir_key == "exp")))
        blk.append(_intra_views(ser, mti))
        for label, cls in _GROUPS:
            items = bks.get(label) or []
            if not items:
                continue
            blk.append(f"<h2 class='ind-group ind-group-{cls}'>{label} ({len(items)})</h2>")
            blk.append("<div class='ind-cards'>")
            for ind, pts in items:
                blk.append(
                    "<section class='ind-card'>"
                    f"<div class='ind-head'><h3>{_html.escape(ind)}</h3>"
                    f"<span class='ind-badge ind-badge-{cls}'>{label}</span></div>"
                    + _card_body(pts, lab)
                    + "</section>"
                )
            blk.append("</div>")
        if mti:
            blk.append(render_subitem_html(mti, amt_th=amt_th, lab=lab))
        blk.append("</div>")
        return "".join(blk)

    out.append(_direction_block(series, buckets, by_mti,
                                "수출액", "수출", "exp", hidden=False))
    if imp_series:
        # 전체(기본) 모드: 수출 섹션(하위품목 표까지) 끝 → 구분선 → 수입
        # 섹션이 이어짐 (사용자 2026-06-13 '캡쳐 다음에 이어지는 걸로').
        out.append(
            "<div class='ind-zone-div ind-dir-divider'>"
            "<span>📥 여기부터 <b>수입</b> — 위 수출과 동일 구조 "
            "(분류 보드 · 산업 카드 · MTI 하위품목)</span></div>"
        )
        out.append(_direction_block(imp_series, _bucketize(imp_series),
                                    by_mti_import, "수입액", "수입",
                                    "imp", hidden=False))
    return "".join(out)

