"""전체 기간(월) 수출입 시장 보고서 — 특정 월(기본 최신월)의 관세청 수출입 전체를
산업·품목 단위로 집계 + **수출↔수입 관계** 분석 (사용자 2026-06-17 '하나씩 검색도
좋은데 현재 기준 전체를 다뽑을수 있게, 5월전체 식. 수출과 수입간의 관계 고려').

company_report.py 패턴 미러 — free(₩0 데이터)/llm(Gemini 산문 opt-in)/telegram/channel.
데이터: industry.load_mti_stored(수출)+load_mti_imports(수입) — 신규 fetch 0. graceful.

수출입 관계가 핵심: 중간재·자본재 수입 ↔ 완제품 수출 연결(가공무역 구조), 무역수지
동인, 산업별 net 포지션, 수출입 동시 품목, MoM/YoY 가속·둔화 선행/후행 신호.
"""
from __future__ import annotations

import html as _html
import logging
import os
from collections import defaultdict
from datetime import date

log = logging.getLogger("trade.period_report")

_MIN_SWING_USD = 3e7   # 급변동 후보 최소 규모($30M) — 소액 품목 MoM 노이즈 배제
_TOP = 15


def _usd(v) -> str:
    """USD → 억$ (부호 보존). 0/비수치 → —."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if not x:
        return "—"
    sign = "-" if x < 0 else ""
    return f"{sign}{abs(x) / 1e8:,.1f}억$"


def _pct(cur, prev):
    """(cur-prev)/|prev|*100 또는 None(기준 0/부재)."""
    try:
        cur = float(cur or 0)
        prev = float(prev or 0)
    except (TypeError, ValueError):
        return None
    if not prev:
        return None
    return (cur - prev) / abs(prev) * 100.0


def _shift_month(ym: str, delta: int) -> str:
    """'YYYY-MM' + delta(월) → 'YYYY-MM'. 실패 시 ''."""
    try:
        y, m = ym.split("-")
        idx = int(y) * 12 + (int(m) - 1) + delta
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}"
    except Exception:
        return ""


def _release_stage(period: str, today: date | None = None) -> str:
    """그 달(period='YYYY-MM')이 관세청 발표 캘린더상 '확정'인지 '잠정'인지 추정
    (사용자 2026-06-17 '1일 잠정 vs 15일 확정 구분'). 확정 공식 발표 = 익월 ~15일 →
    오늘이 그 이후면 '확정', 전이면 '잠정'(전월 풀월). mti_series 에 status 컬럼이
    없어(INSERT OR REPLACE) 캘린더+오늘 기준 추정 — 일일 갱신이 발표를 따라가므로
    실용적으로 정확. 임시(11/21일)는 HS 분해가 없어 mti 월값엔 애초에 안 섞임."""
    try:
        y, m = int(period[:4]), int(period[5:7])
    except Exception:
        return ""
    fy, fm = (y + 1, 1) if m == 12 else (y, m + 1)   # 익월
    today = today or date.today()
    return "확정" if today >= date(fy, fm, 15) else "잠정"


def _months_of(node: dict) -> dict:
    return (node or {}).get("months") or {}


def gather_period(ym: str | None = None, top: int = _TOP) -> dict:
    """특정 월(기본 최신월)의 수출입 전체 집계 → 보고서 데이터. 순수 집계(저장된
    mti 시리즈만, 신규 fetch 0). graceful — 데이터 없으면 period=None."""
    exp: dict = {}
    imp: dict = {}
    prov_sigs: dict = {}
    try:
        from trade import customs, customs_provisional, industry
        with customs.session() as conn:
            exp = industry.load_mti_stored(conn)
            imp = industry.load_mti_imports(conn)
            try:
                prov_sigs = customs_provisional.load_signals(conn)   # 잠정 속보(선행)
            except Exception:
                prov_sigs = {}
    except Exception as exc:
        log.warning("period_report load: %s", exc)

    months_all: set[str] = set()
    for node in list(exp.values()) + list(imp.values()):
        months_all |= set(_months_of(node).keys())
    if not months_all:
        return {"period": None, "prev": "", "yoy": "", "status": "", "provisional": None,
                "totals": {}, "top_export": [], "top_import": [],
                "both_traded": [], "industries": [], "swings": [], "n_items": 0}

    period = ym if (ym and ym in months_all) else max(months_all)
    prev = _shift_month(period, -1)
    yoy = _shift_month(period, -12)

    def _u(node_map: dict, code: str, m: str) -> float:
        return float(_months_of(node_map.get(code)).get(m) or 0)

    codes = set(exp) | set(imp)
    rows: list[dict] = []
    for c in codes:
        meta = exp.get(c) or imp.get(c) or {}
        ex, im = _u(exp, c, period), _u(imp, c, period)
        rows.append({
            "mti6": c,
            "name": (meta.get("name") or c).strip(),
            "industry": (meta.get("industry") or "").strip(),
            "export": ex, "import": im, "net": ex - im,
            "export_mom": _pct(ex, _u(exp, c, prev)),
            "export_yoy": _pct(ex, _u(exp, c, yoy)),
            "import_mom": _pct(im, _u(imp, c, prev)),
            "import_yoy": _pct(im, _u(imp, c, yoy)),
        })

    tot_ex = sum(r["export"] for r in rows)
    tot_im = sum(r["import"] for r in rows)
    tot_ex_p = sum(_u(exp, c, prev) for c in codes)
    tot_im_p = sum(_u(imp, c, prev) for c in codes)
    tot_ex_y = sum(_u(exp, c, yoy) for c in codes)
    tot_im_y = sum(_u(imp, c, yoy) for c in codes)

    top_export = sorted(rows, key=lambda r: -r["export"])[:top]
    top_import = sorted(rows, key=lambda r: -r["import"])[:top]
    # 수출입 동시 품목 — 중간재/가공무역 신호(수입+수출 둘 다 유의미)
    both = [r for r in rows if r["export"] > 0 and r["import"] > 0]
    both.sort(key=lambda r: -(r["export"] + r["import"]))
    both_traded = both[:top]

    # 산업 롤업
    ind_ex: dict = defaultdict(float)
    ind_im: dict = defaultdict(float)
    ind_ex_p: dict = defaultdict(float)   # 직전월(MoM)
    ind_ex_y: dict = defaultdict(float)   # 전년동월(YoY)
    for r in rows:
        ind_ex[r["industry"]] += r["export"]
        ind_im[r["industry"]] += r["import"]
    for c in codes:
        meta = exp.get(c) or imp.get(c) or {}
        ind = (meta.get("industry") or "").strip()
        ind_ex_p[ind] += _u(exp, c, prev)
        ind_ex_y[ind] += _u(exp, c, yoy)
    industries = [{
        "industry": ind or "(미분류)",
        "export": ind_ex[ind], "import": ind_im[ind],
        "net": ind_ex[ind] - ind_im[ind],
        "export_mom": _pct(ind_ex[ind], ind_ex_p[ind]),
        "export_yoy": _pct(ind_ex[ind], ind_ex_y[ind]),
    } for ind in (set(ind_ex) | set(ind_im))]
    industries.sort(key=lambda x: -x["export"])

    # 급변동 — 수출 MoM 절대값 큰 순(유의미 규모만)
    movers = [r for r in rows if r["export"] >= _MIN_SWING_USD and r["export_mom"] is not None]
    movers.sort(key=lambda r: abs(r["export_mom"]), reverse=True)

    # 잠정 속보(선행) — 관세청 10일 단위(11/21일/월초). 확정 품목 집계와 별개(섞지
    # 않음, 운영자 정책). exp_item/imp_item 의 전체+주요10만(HS 분해 없음). 자체 최신월.
    prov = None
    pe, pi = prov_sigs.get("exp_item"), prov_sigs.get("imp_item")
    if pe or pi:
        prov = {"ym": (pe or pi).get("ym"), "window": (pe or pi).get("window"),
                "export": pe, "import": pi}

    return {
        "period": period, "prev": prev, "yoy": yoy,
        "status": _release_stage(period), "provisional": prov,
        "totals": {
            "export": tot_ex, "import": tot_im, "balance": tot_ex - tot_im,
            "export_mom": _pct(tot_ex, tot_ex_p), "import_mom": _pct(tot_im, tot_im_p),
            "export_yoy": _pct(tot_ex, tot_ex_y), "import_yoy": _pct(tot_im, tot_im_y),
        },
        "top_export": top_export, "top_import": top_import,
        "both_traded": both_traded, "industries": industries[:12],
        "swings": movers[:top], "n_items": len(rows),
    }


# ─── 렌더(HTML, ₩0) ──────────────────────────────────────────────────────
def _pct_cell(p) -> str:
    if p is None:
        return '<td style="padding:4px 8px;text-align:right;color:#9aa0aa">—</td>'
    color = "#26a69a" if p >= 0 else "#e2574c"
    sign = "+" if p >= 0 else ""
    return (f'<td style="padding:4px 8px;text-align:right;color:{color}">'
            f'{sign}{p:.1f}%</td>')


def _usd_cell(v) -> str:
    return f'<td style="padding:4px 8px;text-align:right">{_usd(v)}</td>'


def render_free(data: dict) -> str:
    """데이터 → 자체완결 HTML 보고서(인라인 스타일·₩0). 순수."""
    e = _html.escape
    period = data.get("period")
    if not period:
        return ('<div style="line-height:1.5">'
                '<div style="font-size:18px;font-weight:700">전체 수출입 보고서</div>'
                '<div style="color:#9aa0aa;font-size:13px;margin-top:8px">'
                '수출입 집계 데이터 미확보 — 관세청 수집(mti_series) 후 이용 가능.</div></div>')
    t = data.get("totals") or {}
    status = data.get("status") or ""
    badge = ""
    if status:
        bc = "#26a69a" if status == "확정" else "#ff9500"
        badge = (f' <span style="font-size:12px;font-weight:600;color:{bc};'
                 f'border:1px solid {bc};border-radius:6px;padding:1px 7px;'
                 f'vertical-align:middle">{status}</span>')
    stage_note = ("관세청 ~익월15일 공식 확정" if status == "확정"
                  else "전월 풀월 잠정(익월1일~) · 15일 확정으로 갱신 예정")
    head = (f'<div style="font-size:18px;font-weight:700;margin-bottom:2px">'
            f'{e(period)} 수출입 시장 보고서{badge}</div>'
            '<div style="font-size:12px;color:#9aa0aa;margin-bottom:12px">'
            f'관세청 품목 집계({data.get("n_items", 0):,}개 품목) · {stage_note} · '
            '수출↔수입 관계 · 직전월 대비 MoM</div>')

    # 🟢 잠정 속보(선행) — 확정 품목 집계와 별개(섞지 않음). 전체+주요10만(HS 분해 없음).
    prov = data.get("provisional")
    prov_html = ""
    if prov and (prov.get("export") or prov.get("import")):
        def _prov_line(label, sig):
            if not sig:
                return f'<div style="color:#9aa0aa">{label} — 데이터 없음</div>'
            yoy = sig.get("total_yoy")
            yoy_txt = ""
            if yoy is not None:
                yc = "#26a69a" if yoy >= 0 else "#e2574c"
                yoy_txt = (f' <span style="color:{yc}">'
                           f'(YoY {"+" if yoy >= 0 else ""}{yoy:.1f}%)</span>')
            tops = [it for it in (sig.get("items") or []) if it.get("usd")][:3]
            top3 = ", ".join(f'{e(it["name"])} {_usd(it["usd"])}' for it in tops)
            return (f'<div style="margin-top:2px"><b>{label}</b> {_usd(sig.get("total_usd"))}{yoy_txt}'
                    + (f'<div style="font-size:12px;color:#9aa0aa;margin-left:10px">상위: {top3}</div>'
                       if top3 else '') + '</div>')
        prov_html = (
            '<div style="margin:6px 0 4px;padding:10px 12px;background:#10231a;'
            'border:1px solid #1f5132;border-radius:8px">'
            '<div style="font-weight:600;color:#3ddc84;margin-bottom:4px">🟢 잠정 속보 (선행)</div>'
            '<div style="font-size:12px;color:#9aa0aa;margin-bottom:8px">'
            f'관세청 10일 단위 · {e(prov.get("ym") or "")} {e(prov.get("window") or "")} 누적 · '
            '주요 10개 품목만(HS 전체분해 없음) · <b>확정 품목 집계와 별개의 선행 신호</b></div>'
            + _prov_line("수출", prov.get("export"))
            + _prov_line("수입", prov.get("import")) + '</div>')

    def _kpi(label, val, mom):
        mtxt = ""
        if mom is not None:
            mc = "#26a69a" if mom >= 0 else "#e2574c"
            mtxt = (f' <span style="font-size:12px;color:{mc}">'
                    f'{"+" if mom >= 0 else ""}{mom:.1f}%</span>')
        return (f'<div style="flex:1;min-width:120px;background:#1c1f26;border:1px solid '
                f'#2a2e37;border-radius:8px;padding:8px 12px">'
                f'<div style="font-size:11px;color:#9aa0aa">{label}</div>'
                f'<div style="font-size:16px;font-weight:700">{val}{mtxt}</div></div>')
    bal = t.get("balance", 0)
    bal_c = "#26a69a" if bal >= 0 else "#e2574c"
    summary = ('<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">'
               + _kpi("총수출", _usd(t.get("export")), t.get("export_mom"))
               + _kpi("총수입", _usd(t.get("import")), t.get("import_mom"))
               + f'<div style="flex:1;min-width:120px;background:#1c1f26;border:1px solid '
                 f'#2a2e37;border-radius:8px;padding:8px 12px">'
                 f'<div style="font-size:11px;color:#9aa0aa">무역수지</div>'
                 f'<div style="font-size:16px;font-weight:700;color:{bal_c}">{_usd(bal)}</div></div>'
               + '</div>')

    def _table(title, rows, cols):
        if not rows:
            return ''
        head_cells = "".join(
            f'<th style="text-align:{a};padding:4px 8px;color:#9aa0aa">{e(h)}</th>'
            for h, a, _ in cols)
        body = ""
        for r in rows:
            tds = ""
            for _, a, fn in cols:
                tds += fn(r)
            body += f'<tr>{tds}</tr>'
        return (f'<div style="font-weight:600;margin:14px 0 4px">{title}</div>'
                '<table style="border-collapse:collapse;font-size:13px;width:100%">'
                f'<thead><tr>{head_cells}</tr></thead><tbody>{body}</tbody></table>')

    def _nm(r):
        return f'<td style="padding:4px 8px">{e(r["name"])}</td>'

    def _ind(r):
        return f'<td style="padding:4px 8px;color:#9aa0aa">{e(r.get("industry",""))}</td>'

    ind_tbl = _table(
        "🏭 산업별 수출입 (수출액순)", data.get("industries") or [],
        [("산업", "left", lambda r: f'<td style="padding:4px 8px">{e(r["industry"])}</td>'),
         ("수출", "right", lambda r: _usd_cell(r["export"])),
         ("수입", "right", lambda r: _usd_cell(r["import"])),
         ("수지", "right", lambda r: _usd_cell(r["net"])),
         ("수출YoY", "right", lambda r: _pct_cell(r.get("export_yoy"))),
         ("수출MoM", "right", lambda r: _pct_cell(r["export_mom"]))])
    exp_tbl = _table(
        "🚢 수출 상위 품목", data.get("top_export") or [],
        [("품목", "left", _nm), ("산업", "left", _ind),
         ("수출", "right", lambda r: _usd_cell(r["export"])),
         ("MoM", "right", lambda r: _pct_cell(r["export_mom"])),
         ("YoY", "right", lambda r: _pct_cell(r["export_yoy"]))])
    imp_tbl = _table(
        "📥 수입 상위 품목", data.get("top_import") or [],
        [("품목", "left", _nm), ("산업", "left", _ind),
         ("수입", "right", lambda r: _usd_cell(r["import"])),
         ("MoM", "right", lambda r: _pct_cell(r["import_mom"])),
         ("YoY", "right", lambda r: _pct_cell(r["import_yoy"]))])
    both_tbl = _table(
        "🔁 수출입 동시 품목 (중간재·가공무역 신호 · 수출+수입순)",
        data.get("both_traded") or [],
        [("품목", "left", _nm), ("산업", "left", _ind),
         ("수출", "right", lambda r: _usd_cell(r["export"])),
         ("수입", "right", lambda r: _usd_cell(r["import"])),
         ("수지", "right", lambda r: _usd_cell(r["net"]))])
    sw_tbl = _table(
        "⚡ 급변동 품목 (수출 MoM 큰 순)", data.get("swings") or [],
        [("품목", "left", _nm), ("산업", "left", _ind),
         ("수출", "right", lambda r: _usd_cell(r["export"])),
         ("MoM", "right", lambda r: _pct_cell(r["export_mom"]))])
    return (f'<div style="line-height:1.5">{head}{summary}{prov_html}{ind_tbl}'
            f'{exp_tbl}{imp_tbl}{both_tbl}{sw_tbl}</div>')


# ─── LLM 산문 요약(opt-in, 유료) ─────────────────────────────────────────
def _llm_digest(data: dict) -> str:
    """LLM 입력용 압축 텍스트(데이터만 — 환각 차단)."""
    t = data.get("totals") or {}

    def _m(p):
        return f"(MoM {p:+.1f}%)" if p is not None else ""
    lines = [f"기간: {data.get('period')} ({data.get('status', '')}, 직전월 "
             f"{data.get('prev')}, 전년동월 {data.get('yoy')})",
             f"총수출 {_usd(t.get('export'))} {_m(t.get('export_mom'))}, "
             f"총수입 {_usd(t.get('import'))} {_m(t.get('import_mom'))}, "
             f"무역수지 {_usd(t.get('balance'))}"]
    if data.get("industries"):
        lines.append("산업별(수출/수입/수지): " + " | ".join(
            f"{r['industry']} {_usd(r['export'])}/{_usd(r['import'])}/{_usd(r['net'])}"
            for r in data["industries"][:8]))
    if data.get("top_export"):
        lines.append("수출상위: " + ", ".join(
            f"{r['name']} {_usd(r['export'])}{_m(r['export_mom'])}"
            for r in data["top_export"][:10]))
    if data.get("top_import"):
        lines.append("수입상위: " + ", ".join(
            f"{r['name']} {_usd(r['import'])}{_m(r['import_mom'])}"
            for r in data["top_import"][:10]))
    if data.get("both_traded"):
        lines.append("수출입 동시(중간재/가공무역): " + ", ".join(
            f"{r['name']}(수출 {_usd(r['export'])}·수입 {_usd(r['import'])})"
            for r in data["both_traded"][:8]))
    if data.get("swings"):
        lines.append("급변동: " + ", ".join(
            f"{r['name']}({r['export_mom']:+.1f}%)" for r in data["swings"][:8]))
    prov = data.get("provisional")
    if prov and (prov.get("export") or prov.get("import")):
        def _pl(sig):
            if not sig:
                return "—"
            y = sig.get("total_yoy")
            return _usd(sig.get("total_usd")) + (f"(YoY {y:+.1f}%)" if y is not None else "")
        lines.append(f"잠정 속보(선행 · {prov.get('ym')} {prov.get('window')} 누적 · "
                     f"확정과 별개): 수출 {_pl(prov.get('export'))}, "
                     f"수입 {_pl(prov.get('import'))}")
    return "\n".join(lines)


_LLM_SYS = (
    "너는 한국 수출입 거시·산업 애널리스트다. 아래 '데이터'(특정 월 관세청 수출입 "
    "집계: 총수출/총수입/무역수지, 산업별, 수출·수입 상위 품목, 수출입 동시 품목, "
    "급변동)만 근거로 8~12문장 한국어 분석을 써라. 규칙: (1) 주어진 수치/품목만 사용, "
    "새 숫자·사건 날조 금지 (2) **수출과 수입의 관계를 핵심 축으로** — 중간재·자본재 "
    "수입 ↔ 완제품 수출 연결(가공무역 구조), 무역수지 동인, 산업별 net 포지션의 의미 "
    "(3) MoM/YoY 가속·둔화로 선행/후행 신호 해석 (4) '잠정 속보(선행)'가 있으면 "
    "관세청 10일 단위 누적치로 확정보다 앞선 신호이니 방향(가속/둔화) 참고로만 — "
    "확정 품목 집계와 섞지 말 것 (5) 투자 권유 금지·교육 목적·중립. 단정 대신 "
    "데이터가 보여주는 범위로 서술. 마크다운 없이 평문."
)


def _note(msg: str) -> str:
    return (f'<div style="font-size:12px;color:#ff9500;margin-top:12px">'
            f'{_html.escape(msg)}</div>')


def _run_llm(data: dict, model: str | None = None) -> tuple[str, dict]:
    """Gemini 산문 요약 → (text, meta). 키부재/실패 시 ('', {used:False})."""
    try:
        from trade import llm_insights, llm_usage
        if not llm_insights._api_key():
            return "", {"used": False, "reason": "no_key"}
        from langchain_google_genai import ChatGoogleGenerativeAI
        mdl = model or llm_insights.DEFAULT_MODEL
        resp = ChatGoogleGenerativeAI(
            model=mdl, temperature=0.3,
            google_api_key=llm_insights._api_key()).invoke(
                [("system", _LLM_SYS), ("human", _llm_digest(data))])
        um = getattr(resp, "usage_metadata", None) or {}
        in_tok, out_tok = um.get("input_tokens", 0), um.get("output_tokens", 0)
        try:
            llm_usage.record(mdl, in_tok, out_tok)
        except Exception:
            pass
        return ((getattr(resp, "content", "") or "").strip(),
                {"used": True, "model": mdl, "in_tok": in_tok, "out_tok": out_tok})
    except Exception as exc:
        log.warning("period_report LLM: %s", exc)
        return "", {"used": False, "reason": exc.__class__.__name__}


def render_llm(data: dict, model: str | None = None) -> tuple[str, dict]:
    """free 보고서 + Gemini 산문 분석. (html, meta). 키부재/실패 시 free 로 graceful."""
    free = render_free(data)
    if not data.get("period"):
        return free, {"used": False}
    txt, meta = _run_llm(data, model)
    if not txt:
        reason = meta.get("reason")
        msg = ("⚠️ AI 분석 생략 — GOOGLE_API_KEY 없음(무료 보고서만)."
               if reason == "no_key" else "⚠️ AI 분석 실패 — 무료 보고서만.")
        return free + _note(msg), {"used": False}
    ai = ('<div style="font-weight:600;margin:14px 0 4px">🤖 AI 분석 (Gemini · 유료)</div>'
          f'<div style="font-size:13px;background:#1c1f26;border:1px solid #2a2e37;'
          f'border-radius:8px;padding:10px 12px;white-space:pre-wrap">{_html.escape(txt)}</div>')
    html = free + ai
    # 유료 산출물 → 대시보드 아카이브 적립 (사용자 2026-06-18, company_report 미러).
    try:
        from trade import llm_usage, report_archive
        cost_krw = round(llm_usage.cost_usd(meta.get("model") or "",
                                            meta.get("in_tok") or 0,
                                            meta.get("out_tok") or 0)
                         * llm_usage.KRW_PER_USD, 1)
        report_archive.record(
            kind="🗂️ 전체", title=f"{data.get('period')} 수출입 시장 보고서",
            html_body=html, summary=_archive_summary(data, txt),
            cost_krw=cost_krw or None, tg=render_telegram(data, txt))
        report_archive.regenerate()
    except Exception as exc:
        log.warning("period_report archive: %s", exc)
    return html, meta


def _archive_summary(data: dict, ai_text: str) -> str:
    """아카이브 색인 카드 본문(검색용 평문) — 데이터 핵심 + AI 분석."""
    t = data.get("totals") or {}
    parts = [f"🗂️ {data.get('period')} 수출입 시장 보고서 ({data.get('status', '')})",
             f"총수출 {_usd(t.get('export'))} · 총수입 {_usd(t.get('import'))} · "
             f"무역수지 {_usd(t.get('balance'))}"]
    if data.get("top_export"):
        parts.append("수출상위: " + ", ".join(r["name"] for r in data["top_export"][:10]))
    if data.get("top_import"):
        parts.append("수입상위: " + ", ".join(r["name"] for r in data["top_import"][:10]))
    if ai_text:
        parts.append("🤖 " + ai_text)
    return "\n".join(parts)


def render_telegram(data: dict, ai_text: str = "") -> str:
    """보고서 → 텔레그램 HTML(≤4096, 표 대신 줄 목록). 채널 전송용. 순수."""
    e = _html.escape
    period = data.get("period")
    if not period:
        return "🗂️ 전체 수출입 보고서 — 데이터 미확보(관세청 수집 후 이용)."
    t = data.get("totals") or {}

    def _m(p):
        return f" ({'+' if p >= 0 else ''}{p:.1f}%)" if p is not None else ""
    status = data.get("status") or ""
    lines = [f"🗂️ <b>{e(period)} 수출입 시장 보고서</b>{f' ({status})' if status else ''}",
             f"총수출 {_usd(t.get('export'))}{_m(t.get('export_mom'))} · "
             f"총수입 {_usd(t.get('import'))}{_m(t.get('import_mom'))}",
             f"무역수지 <b>{_usd(t.get('balance'))}</b>", ""]
    prov = data.get("provisional")
    if prov and (prov.get("export") or prov.get("import")):
        pe, pi = prov.get("export") or {}, prov.get("import") or {}
        lines.append(f"🟢 잠정 속보(선행 · {e(prov.get('ym') or '')} "
                     f"{e(prov.get('window') or '')}): 수출 {_usd(pe.get('total_usd'))} · "
                     f"수입 {_usd(pi.get('total_usd'))}")
        lines.append("")
    # 전문(全文) — 대시보드 보고서의 전 섹션·전 행을 그대로(send_long 이 4096 분할).
    if data.get("industries"):
        lines.append("🏭 <b>산업별 수출입</b> (수출액순)")
        for r in data["industries"]:
            lines.append(f"• {e(r['industry'])} — 수출 {_usd(r['export'])} · "
                         f"수입 {_usd(r['import'])} · 수지 {_usd(r['net'])}"
                         f"{_m(r.get('export_yoy'))}YoY{_m(r.get('export_mom'))}MoM")
        lines.append("")
    if data.get("top_export"):
        lines.append("🚢 <b>수출 상위 품목</b>")
        for r in data["top_export"]:
            lines.append(f"• {e(r['name'])} — {_usd(r['export'])}"
                         f"{_m(r.get('export_mom'))}MoM{_m(r.get('export_yoy'))}YoY")
        lines.append("")
    if data.get("top_import"):
        lines.append("📥 <b>수입 상위 품목</b>")
        for r in data["top_import"]:
            lines.append(f"• {e(r['name'])} — {_usd(r['import'])}"
                         f"{_m(r.get('import_mom'))}MoM{_m(r.get('import_yoy'))}YoY")
        lines.append("")
    if data.get("both_traded"):
        lines.append("🔁 <b>수출입 동시 품목</b> (중간재·가공무역)")
        for r in data["both_traded"]:
            lines.append(f"• {e(r['name'])} — 수출 {_usd(r['export'])} · "
                         f"수입 {_usd(r['import'])} · 수지 {_usd(r['net'])}")
        lines.append("")
    if data.get("swings"):
        lines.append("⚡ <b>급변동(수출 MoM)</b>")
        for r in data["swings"]:
            lines.append(f"• {e(r['name'])} — {r['export_mom']:+.1f}% "
                         f"({_usd(r['export'])})")
    if ai_text:
        lines += ["", "🤖 <b>AI 분석</b>", e(ai_text)]
    return "\n".join(lines)              # 전문(전송 시 send_long 이 4096 단위 분할)


def send_to_channel(ym: str | None = None, mode: str = "free") -> dict:
    """보고서를 trade 텔레그램 채널로 전송. free=₩0, llm=AI 분석 포함(유료). {ok, sent}."""
    data = gather_period(ym)
    ai_text = ""
    if mode == "llm" and data.get("period"):
        ai_text, _ = _run_llm(data)
    body = render_telegram(data, ai_text)
    ids = [int(x) for x in (os.environ.get("TRADE_CHANNEL_CHAT_IDS") or "").split(",")
           if x.strip()]
    if not ids:
        return {"ok": False, "error": "TRADE_CHANNEL_CHAT_IDS 미설정"}
    sent = 0
    try:
        from trade.scripts import customs_alert
        for cid in ids:
            if customs_alert.send_long(cid, body) > 0:   # 4096 단위 분할 전송(전체)
                sent += 1
    except Exception as exc:
        log.warning("period_report send_to_channel: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": sent > 0, "sent": sent}


def build(ym: str | None = None, mode: str = "free") -> str:
    """ym + mode('free'|'llm') → 보고서 HTML."""
    data = gather_period(ym)
    if mode == "llm":
        return render_llm(data)[0]
    return render_free(data)


def main(argv: list[str] | None = None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="전체 기간(월) 수출입 시장 보고서")
    p.add_argument("--ym", help="YYYY-MM (기본: 최신월)")
    p.add_argument("--llm", action="store_true", help="AI 산문 분석 추가(유료)")
    args = p.parse_args(argv)
    try:
        from pathlib import Path

        from dotenv import load_dotenv
        for c in (Path.home() / "stock" / ".env", Path.cwd() / ".env"):
            if c.exists():
                load_dotenv(c, override=False)
    except Exception:
        pass
    data = gather_period(args.ym)
    print(f"기간: {data.get('period')} | 품목 {data.get('n_items', 0)} | "
          f"총수출 {_usd((data.get('totals') or {}).get('export'))} | "
          f"총수입 {_usd((data.get('totals') or {}).get('import'))}")
    html = render_llm(data)[0] if args.llm else render_free(data)
    out = os.path.join("/tmp", f"period_report_{data.get('period') or 'x'}.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"💾 {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
