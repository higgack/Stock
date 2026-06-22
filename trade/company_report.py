"""기업 중심 보고서 — 회사 → DART 제품 구성 + 관세청 수출입 품목 노출 (사용자
2026-06-17 '별도 버튼으로 보고서 뽑기, 무료+유료').

두 모드:
  • free  : 순수 데이터 조립 — DART 매출구성(G1) + 그 회사가 연결된 관세청 품목의
            수출입 추세. **LLM 0·₩0.**
  • llm   : free 데이터에 Gemini 산문 요약(해석)을 얹음 — 사용자 명시 opt-in(클릭),
            비용 발생(trade.llm_usage 기록). 실패/키부재 시 free 로 graceful 강등.

데이터 출처(전부 기보유): 회사→제품 = trade.dart_revenue(G1, DART 사업보고서),
회사→관세청 품목 노출 = mti_companies(품목→회사 수동·채널) 역조회 + industry.load_
mti_stored(저장된 수출입 집계). 신규 fetch·매칭 비용 0. graceful(데이터 없으면 빈 섹션).
"""
from __future__ import annotations

import html as _html
import logging
import os
import re

log = logging.getLogger("trade.company_report")


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


def _eok_usd(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x / 1e8:,.1f}억$" if x else "—"


def _latest(node) -> float | None:
    """{months:{ym:usd}} → 최신월 값(없으면 None)."""
    months = (node or {}).get("months") or {}
    return months[max(months)] if months else None


def _month_metrics(months: dict | None) -> dict:
    """{ym: usd} → 최신월 지표 {yoy, dyoy, mom, dmom} — industry_series 재사용(산업
    트렌드와 동일 계산식). 데이터 부족 시 빈 dict (graceful)."""
    if not months:
        return {}
    try:
        from trade import industry
        pts = industry.industry_series({"_": months}).get("_") or []
        return pts[-1] if pts else {}
    except Exception:
        return {}


def _pct_cell(v, suf: str = "%") -> str:
    """부호·색(상승 초록/하락 빨강) pct <td>. None → 회색 '—' (period_report 미러)."""
    if v is None:
        return '<td style="padding:4px 8px;text-align:right;color:#9aa0aa">—</td>'
    color = "#26a69a" if v >= 0 else "#e2574c"
    sign = "+" if v >= 0 else ""
    return (f'<td style="padding:4px 8px;text-align:right;color:{color}">'
            f'{sign}{v:.1f}{suf}</td>')


def _dir_metrics(exp_node, imp_node) -> dict:
    """수출(exp_node)·수입(imp_node) 양방향 최신월 추세 → export_*/import_* (사용자
    2026-06-18 '수입쪽도'). 산업트렌드와 동일 계산식(_month_metrics). 노드가
    precomputed 'metrics'(히트맵 fallback, 3-포인트라 ΔYoY/ΔMoM None)를 가지면
    sparse-series 오산정 없이 그대로 사용(industry.load_mti_heatmap)."""
    em = (exp_node or {}).get("metrics") or _month_metrics((exp_node or {}).get("months"))
    im = (imp_node or {}).get("metrics") or _month_metrics((imp_node or {}).get("months"))
    return {
        "export_yoy": em.get("yoy"), "export_dyoy": em.get("dyoy"),
        "export_mom": em.get("mom"), "export_dmom": em.get("dmom"),
        "import_yoy": im.get("yoy"), "import_dyoy": im.get("dyoy"),
        "import_mom": im.get("mom"), "import_dmom": im.get("dmom"),
    }


def _exposure_table(rows: list[dict], *, title: str, subtitle: str,
                    with_companies: bool = False) -> str:
    """노출/매칭 품목 표 — 수출·수입 양방향 값 + YoY·ΔYoY·MoM·ΔMoM (2단 헤더,
    가로 스크롤). 회사 모드(노출)·품목 모드(매칭) 공용. 순수."""
    e = _html.escape

    def _row(x: dict) -> str:
        # 🔗 = 테마 HS Code 로 연계된 관세청 수출입 품목(카테고리 수준일 수 있음).
        # 품목명은 클릭 시 그 실제 품목명으로 재검색(data-rb-search) — 별칭(MLCC)으로
        # 들어와도 실제 품목명(고정식축전기)으로 한 번에 재조회(사용자 2026-06-19).
        prefix = "🔗 " if x.get("hs_linked") else ""
        nm = (f'{prefix}<span class="rb-relink" data-rb-search="{e(x["item"])}" '
              f'title="이 품목명으로 재검색" style="cursor:pointer;color:#6cb6ff;'
              f'text-decoration:underline dotted">{e(x["item"])}</span>')
        c = (f'<td style="padding:4px 8px">{nm}</td>'
             f'<td style="padding:4px 8px;color:#9aa0aa">{e(x.get("industry",""))}</td>'
             f'<td style="padding:4px 8px;text-align:right">{_eok_usd(x.get("export_usd"))}</td>'
             f'{_pct_cell(x.get("export_yoy"))}{_pct_cell(x.get("export_dyoy"), "%p")}'
             f'{_pct_cell(x.get("export_mom"))}{_pct_cell(x.get("export_dmom"), "%p")}'
             f'<td style="padding:4px 8px;text-align:right">{_eok_usd(x.get("import_usd"))}</td>'
             f'{_pct_cell(x.get("import_yoy"))}{_pct_cell(x.get("import_dyoy"), "%p")}'
             f'{_pct_cell(x.get("import_mom"))}{_pct_cell(x.get("import_dmom"), "%p")}')
        if with_companies:
            c += (f'<td style="padding:4px 8px;color:#9aa0aa">'
                  f'{e(", ".join(x.get("companies", [])[:4]))}</td>')
        return f"<tr>{c}</tr>"

    def _th(t, a="right", **attr):
        a_str = "".join(f' {k}="{v}"' for k, v in attr.items())
        return (f'<th{a_str} style="text-align:{a};padding:4px 8px;color:#9aa0aa">'
                f'{t}</th>')

    sub5 = (_th("최신월") + _th("YoY") + _th("ΔYoY") + _th("MoM") + _th("ΔMoM"))
    header = (
        '<thead><tr>'
        + _th("품목", "left", rowspan=2) + _th("산업", "left", rowspan=2)
        + _th("🚢 수출", "center", colspan=5) + _th("📥 수입", "center", colspan=5)
        + (_th("관련기업", "left", rowspan=2) if with_companies else "")
        + '</tr><tr>' + sub5 + sub5 + '</tr></thead>'
    )
    body = "".join(_row(x) for x in rows)
    return (f'<div style="font-weight:600;margin:14px 0 4px">{title} '
            f'<span style="font-weight:400;font-size:12px;color:#9aa0aa">{subtitle}'
            '</span></div>'
            '<div style="overflow-x:auto">'
            '<table style="border-collapse:collapse;font-size:13px;width:100%;min-width:780px">'
            f'{header}<tbody>{body}</tbody></table></div>')


def _load_alerts() -> list:
    """store.db 의 BeOn 알림 전체 (회사별 탭과 동일 소스). 읽기전용 open —
    report 프로세스가 스키마 마이그레이션(쓰기)을 트리거하지 않게(load_channel_pairs
    패턴). DB/테이블 부재 시 [] (graceful)."""
    import sqlite3
    from trade import mti_companies, store
    p = mti_companies._store_db_path()
    if not p.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return store.list_all_alerts(conn)
        finally:
            conn.close()
    except Exception as exc:
        log.warning("company_report alerts load: %s", exc)
        return []


def _company_alert_items(name: str, alerts: list) -> list[str]:
    """회사명 → 그 회사가 태깅된 BeOn 알림 품목명들 (회사별 탭과 동일 소스·동일
    split_names 매칭, 순서 보존 dedupe). 회사별 뷰는 alerts 를 회사로 묶는데, 기업
    보고서는 그 역(회사→품목)을 같은 데이터로 재사용(사용자 2026-06-18 '큐레이션
    말고 회사별 잘 정리된 거 써'). 순수(단위테스트)."""
    target = _norm(name)
    if not target or not alerts:
        return []
    try:
        from trade import price_provider
        _split = price_provider.split_names
    except Exception:
        _split = lambda xs: list(xs or [])  # noqa: E731
    seen: set[str] = set()
    out: list[str] = []
    for a in alerts:
        try:
            cos = _split(a.get("stocks") or [])
        except Exception:
            cos = a.get("stocks") or []
        # 회사별 섹션과 동일하게 정규화 정확일치(±접미사) — substring 과매칭 방지.
        if not any(_norm(c) == target or _norm(c).startswith(target)
                   or target.startswith(_norm(c)) for c in cos if c):
            continue
        item = (a.get("item") or "").strip()
        k = _norm(item)
        if item and k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _by_mti_name_index(by_mti: dict) -> dict:
    """by_mti → {정규화 품목명: (mti6, node)} (첫 매치 우선). 품목명으로 관세청
    수출입 시리즈를 찾기 위한 역인덱스."""
    idx: dict = {}
    for mti6, node in (by_mti or {}).items():
        nm = _norm(node.get("name") or mti6)
        if nm and nm not in idx:
            idx[nm] = (mti6, node)
    return idx


def _match_mti(item: str, name_idx: dict):
    """품목명 → (mti6, node) — 정규화 **정확일치만**. 부분일치는 '디램모듈'→'디램'
    처럼 다른 제품에 엉뚱한 수출액을 붙이고 표시명을 덮어써 중복을 만들므로 금지
    (2026-06-18 회사별 소스 통합 시 적발). 미발견 None(=관세청 수치 없는 관련 품목)."""
    k = _norm(item)
    return name_idx.get(k) if k else None


def _company_exposure(name: str, by_mti: dict, pairs: list,
                      by_imp: dict | None = None,
                      alerts: list | None = None) -> list[dict]:
    """회사명 → 연결된 관세청 품목 [{item, industry, export_usd, import_usd}]
    (수출액 내림차순). 소스 union: (A) **BeOn 알림 = 회사별 탭과 동일**(회사가 태깅된
    품목 전부, 사용자 2026-06-18) + (B) mti_companies 큐레이션·채널 역조회(보강).
    각 품목에 관세청 수출/수입(by_mti) 부착. 순수(단위테스트)."""
    from trade import mti_companies
    target = _norm(name)
    if not target:
        return []
    name_idx = _by_mti_name_index(by_mti)
    rows: dict[str, dict] = {}   # 정규화 품목명 → row (dedupe)

    def _add(item_display: str, hit=None):
        key = _norm(item_display)
        if not key or key in rows:
            return
        if hit is None:
            hit = _match_mti(item_display, name_idx)
        mti6, node = hit if hit else (None, None)
        imp_node = (by_imp or {}).get(mti6) if mti6 else None
        rows[key] = {
            "item": (node.get("name") if node else item_display) or item_display,
            "industry": (node.get("industry") if node else "") or "",
            "export_usd": _latest(node) if node else None,
            "import_usd": _latest(imp_node) if imp_node else None,
            **_dir_metrics(node, imp_node),   # export_*/import_* YoY·ΔYoY·MoM·ΔMoM
        }

    # (A) BeOn 알림 — 회사별 탭과 동일한 회사→품목 매핑(풍부)
    for item in _company_alert_items(name, alerts or []):
        _add(item)

    # (B) 큐레이션 + 채널 역조회 — by_mti 품목 중 회사 매칭(보강, 관세청 집계 보장)
    for mti6, node in (by_mti or {}).items():
        item = (node.get("name") or mti6).strip()
        cos = (set(mti_companies.companies_for(item))
               | set(mti_companies.channel_companies_for(item, pairs))
               | set(mti_companies.reinforce_approved_for(item)))
        if any(_norm(c) == target or target in _norm(c) or _norm(c) in target
               for c in cos if c):
            _add(item, (mti6, node))

    # (C) 테마 회사 → 테마 HS Code → 관세청 수출입 품목 연계 (사용자 2026-06-19
    # '안 잡힌 것들 잡히게'). MTI 품목표에 없던 테마 종목도 HS6→MTI6 로 수출입 부착.
    try:
        _tn, th_hs = mti_companies.theme_for_company(name)
    except Exception:
        _tn, th_hs = None, ""
    if th_hs:
        # 핀(_THEME_MTI_PIN) 우선 — 건기식 등 catch-all 은 정확한 MTI6 에만(2026-06-19).
        for m6 in mti_companies.theme_mti6(_tn, [th_hs])[:3]:
            node = (by_mti or {}).get(m6)
            if node:
                _add((node.get("name") or m6).strip(), (m6, node))

    out = list(rows.values())
    # 관세청 수출 있는 품목 먼저(내림차순), 그다음 노출만 있는(값 없는) 품목.
    out.sort(key=lambda x: (x["export_usd"] is None, -(x["export_usd"] or 0)))
    return out[:40]


def _hs6_to_mti6(hs_code: str) -> list[str]:
    """HS6 → MTI6 리스트 (mti_map 공용 리졸버 위임). 테마 HS → 관세청 수출입
    품목 연계용(사용자 2026-06-19). graceful → []."""
    try:
        from trade import mti_map
        return mti_map.hs6_to_mti6(hs_code)
    except Exception:
        return []


def _item_matches(query: str, by_mti: dict, pairs: list,
                  by_imp: dict | None = None) -> dict | None:
    """품목/산업 키워드 query → {mode:'item', name, items[], companies[]} (사용자
    2026-06-18 '창에 기업 말고 품목(반도체) 치면 관련기업'). 회사 노출의 역방향:
    품목 → 관련 상장사. 두 소스 union — (1) query 자체가 큐레이션 키워드,
    (2) query 와 이름/산업이 매칭되는 저장 품목들의 관련기업. 매칭 0이면 None
    (→ gather 가 회사 모드로 폴백). 순수(단위테스트)."""
    from trade import mti_companies
    qn = _norm(query)
    if not qn:
        return None

    seen: set[str] = set()
    companies: list[str] = []

    def _add(cos):
        for c in cos:
            k = _norm(c)
            if c and k and k not in seen:
                seen.add(k)
                companies.append(c)

    # (1) query 자체가 큐레이션 품목 키워드 (예: '디램'·'라면')
    _add(mti_companies.companies_for(query))
    _add(mti_companies.channel_companies_for(query, pairs))
    _add(mti_companies.reinforce_approved_for(query))   # 운영자 승인 DART 보강

    # (1b) 품목 동의어 그룹 통합 — 'PCB'·'인쇄회로'가 같은 통합 결과로 수렴
    # (사용자 2026-06-19 '같은 건데 다른 이름 다 보여줘'). 대표 품목명도 노출.
    syn_name = None
    syn = mti_companies.synonym_companies(query, pairs)
    if syn:
        syn_name, syn_cos = syn
        _add(syn_cos)

    # (1c) 큐레이션 테마 행도 검색 — SiC·MLCC·CCTV·피부과 등 MTI 품목표에 없어
    # 테마로 보강한 카테고리가 기업 보고서에도 나오게(사용자 2026-06-19 '테마로
    # 추가된 것도 나와야'). 테마명에 query 포함 시 그 테마 큐레이션 기업 노출.
    theme_name = None
    theme_hs = ""
    if len(qn) >= 2:
        try:
            for tr in mti_companies.theme_rows():
                if qn in _norm(tr["name"]):
                    theme_name = tr["name"]
                    theme_hs = (tr.get("hs") or [""])[0]
                    _add(tr["companies"])
                    break
        except Exception:
            pass

    # (2) query 와 이름/산업이 매칭되는 저장 품목들 → 행 + 그 품목들의 관련기업
    rows: list[dict] = []
    seen_mti: set[str] = set()
    for mti6, node in (by_mti or {}).items():
        item = (node.get("name") or mti6).strip()
        ind = (node.get("industry") or "").strip()
        if not (qn in _norm(item) or qn in _norm(ind)):
            continue
        cos = list(dict.fromkeys(list(mti_companies.companies_for(item))
                                 + mti_companies.channel_companies_for(item, pairs)
                                 + mti_companies.reinforce_approved_for(item)))
        imp_node = (by_imp or {}).get(mti6)
        rows.append({"item": item, "industry": ind,
                     "export_usd": _latest(node),
                     "import_usd": _latest(imp_node),
                     **_dir_metrics(node, imp_node),
                     "companies": cos})
        seen_mti.add(mti6)
        _add(cos)

    # (2b) 테마 HS Code → 관세청 수출입 품목 연계 (사용자 2026-06-19 '수출입코드랑
    # 연계돼서 안 잡힌 것들 잡히게'). 테마 HS6 → MTI6(mti_map) → by_mti 수출입 트렌드
    # 부착. 같은 HS6 가 여러 MTI 에 걸치면 모두(상위 3). ⚠️ 일부 HS6 는 광범위 MTI
    # 버킷(기타정밀화학원료 등)이라 수출입은 'HS 연계 품목'(카테고리) 수준임을 표기.
    if theme_name:
        # theme_mti6 = 핀(_THEME_MTI_PIN) 우선 → HS6 자동해석(_company_exposure 의
        # (C) 와 동일 리졸버). 핀은 건기식 등 catch-all HS6 의 과잉부착을 막는데,
        # gap-fill 로 기타 품목까지 수치가 붙으므로 여기서도 핀 존중이 필수.
        for m6 in mti_companies.theme_mti6(theme_name, [theme_hs])[:3]:
            node = (by_mti or {}).get(m6)
            if not node or m6 in seen_mti:
                continue
            seen_mti.add(m6)
            imp_node = (by_imp or {}).get(m6)
            rows.append({"item": (node.get("name") or m6).strip(),
                         "industry": (node.get("industry") or "").strip(),
                         "export_usd": _latest(node), "import_usd": _latest(imp_node),
                         **_dir_metrics(node, imp_node), "companies": [],
                         "hs_linked": True})

    if not companies and not rows:
        return None
    rows.sort(key=lambda x: -(x["export_usd"] or 0))
    label = syn_name or theme_name          # 동의어 또는 테마 카테고리명 노출
    return {"mode": "item", "query": query, "name": query,
            "synonym": label if label and _norm(label) != qn else None,
            "items": rows[:30], "companies": companies[:40]}


def _looks_like_hs(q: str) -> bool:
    """입력이 HS코드 표기인지 — 점/대시 포함 거의-숫자(8517.79·2106.90-9099) 또는
    bare 2/4/8/10자리(챕터·호·HSK8/10). **6자리 bare 는 주식코드라 제외**(HS6 는
    '8517.79' 점표기). 상위 자릿수부터 검색되게(사용자 2026-06-22 '85 치면 다'). 순수."""
    s = (q or "").strip()
    d = "".join(c for c in s if c.isdigit())
    if not d:
        return False
    if s.isdigit():
        return len(d) in (2, 4, 8, 10)              # bare HS prefix (6=주식코드 제외)
    nondigit = sum(1 for c in s if not c.isdigit() and c not in ".- ")
    return nondigit == 0 and len(d) >= 2            # 점/대시 표기 HS (숫자 외 토큰 없음)


def _pv_split(dlr_now, dlr_base, wgt_now, wgt_base) -> dict | None:
    """금액·중량 → 금액 증감의 판가/물량 분해 (사용자 2026-06-22 '판가냐 물량이냐').
    단가($/kg)=금액/중량. 금액증감 ≈ (1+판가)(1+물량)-1. 데이터 부족(0·None) → None.
    근사식이라 합이 정확히 금액%가 되진 않음(교차항) — 방향·크기 해석용."""
    try:
        dn, db = float(dlr_now), float(dlr_base)
        wn, wb = float(wgt_now), float(wgt_base)
    except (TypeError, ValueError):
        return None
    if dn <= 0 or db <= 0 or wn <= 0 or wb <= 0:
        return None
    return {"value": round((dn / db - 1) * 100, 1),       # 금액 %
            "qty": round((wn / wb - 1) * 100, 1),          # 물량(중량) %
            "price": round(((dn / wn) / (db / wb) - 1) * 100, 1),  # 단가(판가) %
            "unit_now": dn / wn}                            # 최신월 단가 $/kg


def _leaf_pv(leaf_row: dict) -> dict | None:
    """HS10 leaf 스냅샷 → 수출/수입 각각 YoY·MoM 판가/물량 분해. 중량 열이 없거나
    (구 스냅샷) 부족하면 None (graceful — 다음 풀스윕이 중량 채우면 표시)."""
    if not leaf_row:
        return None
    g = leaf_row.get
    out = {
        "exp_yoy": _pv_split(g("exp"), g("exp_py"), g("exp_wgt"), g("exp_wgt_py")),
        "exp_mom": _pv_split(g("exp"), g("exp_pm"), g("exp_wgt"), g("exp_wgt_pm")),
        "imp_yoy": _pv_split(g("imp"), g("imp_py"), g("imp_wgt"), g("imp_wgt_py")),
        "imp_mom": _pv_split(g("imp"), g("imp_pm"), g("imp_wgt"), g("imp_wgt_pm")),
    }
    return out if any(out.values()) else None


def _agg_dir_node(m6s: list, src: dict | None) -> dict:
    """검색레벨 하위 MTI품목들의 월별 금액+중량을 합산한 단일 노드 {months,wgts}
    (사용자 2026-06-22 '검색한 레벨 하나로'). by_mti(수출)·by_imp(수입) 각각에 사용.
    HS10 월별이력은 관세청 미제공 → MTI품목 단위가 시계열 바닥이라 그 합산이 최선."""
    months: dict = {}
    wgts: dict = {}
    for m6 in m6s:
        node = (src or {}).get(m6) or {}
        for ym, v in (node.get("months") or {}).items():
            try:
                months[ym] = months.get(ym, 0) + float(v or 0)
            except (TypeError, ValueError):
                pass
        for ym, w in (node.get("wgts") or {}).items():
            try:
                wgts[ym] = wgts.get(ym, 0) + float(w or 0)
            except (TypeError, ValueError):
                pass
    return {"months": months, "wgts": wgts}


def _node_pv(node: dict) -> dict | None:
    """합산 노드 {months,wgts} → 최신월 YoY·MoM 판가/물량 분해(금액=판가×물량,
    단가=금액÷중량). 해설(동력)과 동일 노드라 일관. 데이터부족 → None."""
    months = (node or {}).get("months") or {}
    wgts = (node or {}).get("wgts") or {}
    if not months:
        return None
    latest = max(months)
    try:
        y, m = int(latest[:4]), int(latest[5:7])
    except (ValueError, IndexError):
        return None
    pm = f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"
    py = f"{y-1}-{m:02d}"
    out = {"yoy": _pv_split(months.get(latest), months.get(py),
                            wgts.get(latest), wgts.get(py)),
           "mom": _pv_split(months.get(latest), months.get(pm),
                            wgts.get(latest), wgts.get(pm))}
    return out if (out["yoy"] or out["mom"]) else None


def _hs_code_search(query: str, by_mti: dict, pairs: list,
                    by_imp: dict | None = None, leaf: str | None = None,
                    hs_leaf: dict | None = None,
                    direction: str | None = None) -> dict | None:
    """HS코드 → 그 HS6 의 MTI 품목들 + 수출입 숫자 + 관련 상장사 (사용자 2026-06-19
    'HS코드로 검색하면 숫자'). HS6→MTI6(mti_map). 미해석 → None. 순수.
    정확 HS10 검색이면 그 leaf 의 한글품목명(stat_kor)+판가/물량 분해도 첨부."""
    from trade import mti_companies, mti_map
    # prefix 검색 — 2(챕터)/4(호)/6/8/10 어느 자릿수든 그 prefix 하위 전 HSK10 의
    # MTI6 (사용자 2026-06-22 '85 치면 다 나오게'). 6자리 이상은 hs6_to_mti6 와 동치.
    bare = re.sub(r"\D", "", query or "")
    try:
        m6s = mti_map.hs_prefix_to_mti6(bare)
    except Exception:
        m6s = []
    if not m6s:
        return None
    seen: set[str] = set()
    companies: list[str] = []

    def _add(cos):
        for c in cos:
            k = _norm(c)
            if c and k and k not in seen:
                seen.add(k)
                companies.append(c)

    rows: list[dict] = []
    for m6 in m6s:
        node = (by_mti or {}).get(m6)
        item = ((node.get("name") if node else None) or m6).strip()
        ind = ((node.get("industry") if node else "") or "").strip()
        imp_node = (by_imp or {}).get(m6)
        cos = list(dict.fromkeys(list(mti_companies.companies_for(item))
                                 + mti_companies.channel_companies_for(item, pairs)
                                 + mti_companies.reinforce_approved_for(item)))
        rows.append({"item": item, "industry": ind,
                     "export_usd": _latest(node), "import_usd": _latest(imp_node),
                     **_dir_metrics(node, imp_node), "companies": cos,
                     "hs_linked": True, "_m6": m6})
        _add(cos)
    rows.sort(key=lambda x: -(x["export_usd"] or 0))
    for r in rows:
        r.pop("_m6", None)
    # 검색레벨 하위 MTI품목 합산 단일 노드(수출/수입) → 판가물량·동력·진도율·요약을
    # **한 블록·한 방향**으로 (사용자 2026-06-22: 두 블록 어긋남·방향 무시 해소).
    exp_node = _agg_dir_node(m6s, by_mti)
    imp_node = _agg_dir_node(m6s, by_imp)
    # 방향: 히트맵 클릭이 준 direction 우선, 없으면(검색창) 최신월 큰 쪽 자동.
    if direction in ("exp", "imp"):
        is_imp = direction == "imp"
    else:
        is_imp = (max((imp_node["months"] or {0: 0}).values()) >
                  max((exp_node["months"] or {0: 0}).values()))
    dir_node = imp_node if is_imp else exp_node
    dir_lab = "수입" if is_imp else "수출"
    hs_pv = _node_pv(dir_node)                       # 판가물량(선택방향, 합산노드)
    hs_n = sum(1 for m6 in m6s if (by_mti or {}).get(m6) or (by_imp or {}).get(m6))
    hs_comment = ""
    try:
        from trade import industry as _ind
        hs_comment = _ind.item_comment_html(dir_node, dir_lab)   # 요약·동력·진도율
    except Exception as exc:
        log.warning("hs_code_search comment: %s", exc)
    # 한글명 — 정확 HS10 leaf stat_kor 우선, 없으면 전 HS 사전 prefix 폴백.
    hs_name = None
    if hs_leaf and bare and len(bare) == 10 and bare in hs_leaf:
        hs_name = (hs_leaf[bare].get("name") or "").strip() or None
        if hs_name == bare:
            hs_name = None
    if not hs_name:
        try:
            from trade import hs_names as _hn
            hs_name = _hn.hs_name(query)
        except Exception:
            pass
    return {"mode": "item", "query": query, "name": f"HS {query}", "hs_search": True,
            "synonym": None, "items": rows[:30], "companies": companies[:40],
            "leaf": (leaf or "").strip() or None, "hs_name": hs_name,
            "hs_pv": hs_pv, "hs_dir": dir_lab, "hs_n": hs_n,
            "hs_comment": hs_comment, "hs_level": len(bare)}


def gather(query: str, api_key: str | None = None, leaf: str | None = None,
           direction: str | None = None) -> dict:
    """회사(이름·6자리 코드) **또는 품목**(예: 반도체) → 보고서 데이터.
    회사 모드: {mode:'company', query, code, name, products, exposure}.
    품목 모드(사용자 2026-06-18 역검색): {mode:'item', query, name, items, companies}.
    네트워크: DART(corp_code·매출표). graceful."""
    from bot.dart_client import get_dart
    dart = get_dart()
    q = (query or "").strip()

    # 관세청 저장 시리즈(customs.db) + BeOn 알림(store.db = 회사별 탭 소스) 로드.
    # 두 DB 가 다름 — mti_series 는 customs.db, alerts 는 store.db (회사별 뷰가 읽는
    # 곳). 같은 conn 으로 alerts 조회하면 'no such table' 로 silent no-op 되므로 분리.
    by_mti: dict = {}
    by_imp: dict = {}
    pairs: list = []
    hs_leaf: dict = {}
    try:
        from trade import customs, customs_scan, industry, mti_companies
        with customs.session() as conn:
            by_mti = industry.load_mti_stored(conn)
            by_imp = industry.load_mti_imports(conn)
            # '기타'(catch-all) MTI 결손분을 히트맵 leaf 로 메움 — 고정식축전기
            # (MLCC) 등 별칭 검색에서 관련기업만 나오고 수출입 숫자가 빠지던 것
            # 해소(사용자 2026-06-19). setdefault = named-industry full-series 노드는
            # 보존(ΔYoY/ΔMoM 유지), 없던 기타 품목만 보강. universal(전 catch-all 별칭).
            hm_exp, hm_imp = industry.load_mti_heatmap(conn)
            for _m6, _n in hm_exp.items():
                by_mti.setdefault(_m6, _n)
            for _m6, _n in hm_imp.items():
                by_imp.setdefault(_m6, _n)
            # HS10 leaf 스냅샷 — 정확 HS10 검색 시 한글품목명(stat_kor)+중량(물량)으로
            # 판가/물량 분해 표시용 (사용자 2026-06-22). {bare HS10: leaf row}.
            hs_leaf = {str(r.get("hs_code") or ""): r
                       for r in customs_scan.load_heatmap(conn)}
        pairs = mti_companies.load_channel_pairs()
    except Exception as exc:
        log.warning("company_report data %s: %s", q, exc)

    # HS코드 직접 검색 → 해당 품목 수출입 숫자 (사용자 2026-06-19 'HS로 치면 숫자
    # 나와야'). 점/대시 포함 or 8~10자리 bare = HS(6자리 bare 는 주식코드라 제외 —
    # HS6 는 '8517.79' 점표기로). HS6→MTI6 → 그 품목들의 수출입.
    if _looks_like_hs(q):
        hs_res = _hs_code_search(q, by_mti, pairs, by_imp, leaf=leaf,
                                 hs_leaf=hs_leaf, direction=direction)
        if hs_res:
            return hs_res

    digits = q.upper().split(".")[0]
    is_code = digits.isdigit() and len(digits) == 6

    # 이름 조회 1회 (회사 매칭 + 정확일치 판정 공유). 6자리 코드는 조회 불요.
    hits: list = []
    if q and not is_code:
        try:
            hits = dart.find_by_name(q)
        except Exception:
            hits = []
        # 품목 역검색 우선 — 정확한 상장사명이 아닐 때만 (substring 매칭은 품목
        # 키워드가 회사명 일부에 걸리는 흔한 경우라 회사 확정으로 안 봄).
        exact = bool(hits) and _norm(hits[0].get("name", "")) == _norm(q)
        if not exact:
            item = _item_matches(q, by_mti, pairs, by_imp)
            if item:
                return item

    # 회사 모드 (기존)
    code, name = None, q
    if is_code:
        code = digits
        name = dart.stock_code_to_name(code) or q
    elif hits:
        code, name = hits[0].get("stock_code"), hits[0].get("name") or q
    products = []
    if code:
        # 먼저 빌드된 인벤토리(dart_revenue_inventory.json, refresh 타이머가 적립한
        # 2700+ 상장사 매출구성) 사용 — 빠르고 안정적(라이브 DART 일시 실패 무관).
        # 미수록 종목만 라이브 fetch 폴백(사용자 2026-06-18 '다트매출표는 뭐지' →
        # 인벤토리 활용). 둘 다 실패 시 '미확보' 안내.
        try:
            from trade.dart_revenue import load_inventory
            entry = load_inventory().get(code)
            if entry and entry.get("products"):
                products = entry["products"]
        except Exception as exc:
            log.warning("company_report inventory %s: %s", code, exc)
        if not products:
            try:
                from trade.dart_revenue import fetch_company_products
                r = fetch_company_products(code, api_key)
                if r:
                    products = r.get("products", [])
            except Exception as exc:
                log.warning("company_report DART %s: %s", code, exc)
    # 회사별 탭과 동일 소스(store.db BeOn 알림) — 회사 모드에서만 필요(품목 모드는
    # 위에서 이미 반환). 풍부한 회사→품목 매핑(사용자 2026-06-18).
    exposure = _company_exposure(name, by_mti, pairs, by_imp, _load_alerts())
    return {"mode": "company", "query": q, "code": code, "name": name,
            "products": products, "exposure": exposure}


def _render_free_item(data: dict) -> str:
    """품목 역검색 모드 → 관련 상장사 + 매칭 품목 HTML(인라인·₩0). 순수."""
    e = _html.escape
    name = e(data.get("name") or data.get("query") or "—")
    items = data.get("items") or []
    companies = data.get("companies") or []
    syn = data.get("synonym")
    syn_tag = (f' <span style="font-size:13px;color:#9aa0aa;font-weight:400">'
               f'≡ {e(syn)}</span>') if syn else ''
    head = (f'<div style="font-size:18px;font-weight:700;margin-bottom:2px">{name}{syn_tag} '
            '· 관련 기업</div>'
            '<div style="font-size:12px;color:#9aa0aa;margin-bottom:12px">'
            '품목 역검색 · 관세청 수출입 품목 ↔ 관련 상장사(큐레이션+채널) · 무료(데이터)'
            ' · 💡 아래 <b>품목명 클릭</b>으로 실제 품목명 재검색</div>')
    # HS코드 검색(히트맵 셀 클릭 등) breadcrumb — 한 HS는 여러 MTI품목으로 연계될 수
    # 있어 표가 여러 행이 됨을 명시(사용자 2026-06-20 '코팅머신 눌렀는데 기타기계류가 뜸').
    if data.get("hs_search") and items:
        n_it = len(items)
        leaf = (data.get("leaf") or "").strip()
        # 히트맵 셀에서 넘어온 클릭품목(leaf)명을 그대로 보여줌 — 사용자가 누른 게
        # '코팅머신'인데 표엔 귀속 MTI품목(기타기계류 등)만 떠 혼란하던 것(2026-06-20).
        leaf_tag = (f'클릭품목 <b>{e(leaf)}</b> · ' if leaf else '')
        # 정확 HS10 검색이면 그 leaf 의 관세청 한글품목명(stat_kor) 병기 (사용자 2026-06-22).
        hs_name = (data.get("hs_name") or "").strip()
        # 챕터/호 한글명은 매우 길 수 있어(제85류 전기기기…) 표시만 ~44자 축약.
        _hn_disp = (hs_name[:44] + "…") if len(hs_name) > 45 else hs_name
        name_tag = (f' <span style="color:#e6c97a" title="{e(hs_name)}">'
                    f'[{e(_hn_disp)}]</span>' if hs_name else '')
        _dir = data.get("hs_dir") or "수출"
        _lvl = data.get("hs_level") or 0
        _lvl_lab = (f'HS{_lvl}자리' if _lvl in (2, 4, 6, 8, 10) else 'HS')
        _ico = "📥" if _dir == "수입" else "🚢"
        head += (f'<div style="font-size:12px;color:#c8a24a;background:#2a2410;'
                 f'border:1px solid #4a3f1a;border-radius:6px;padding:6px 10px;margin-bottom:12px">'
                 f'🔎 {leaf_tag}HS <b>{e(data.get("query") or "")}</b>{name_tag} '
                 f'· {_lvl_lab} 기준 · {_ico} <b>{e(_dir)}</b> 분석 '
                 f'<span style="color:#9aa0aa">(연계 MTI품목 {n_it}개 합산 — 한 HS는 여러 '
                 f'MTI품목으로 분류돼 모두 합산)</span></div>')
        # 📊 판가 vs 물량 분해 — 선택 방향(히트맵 클릭 방향/자동), 검색레벨 합산노드
        # 기준(해설과 동일 소스 → 일관). 금액=판가×물량, 단가=금액÷중량 (사용자 2026-06-22).
        pv = data.get("hs_pv")
        if pv:
            def _pct(v):
                if v is None:
                    return '<span style="color:#9aa0aa">—</span>'
                c = "#26a69a" if v >= 0 else "#e2574c"
                return f'<span style="color:{c}">{"+" if v >= 0 else ""}{v}%</span>'

            def _line(label, sp):
                if not sp:
                    return ''
                unit = f' · 단가 ${sp["unit_now"]:,.1f}/kg' if sp.get("unit_now") else ''
                return (f'<div>{label} 금액 {_pct(sp["value"])} = '
                        f'판가 {_pct(sp["price"])} × 물량 {_pct(sp["qty"])}{unit}</div>')
            blk = (_line(f"{_ico} {_dir} YoY", pv.get("yoy"))
                   + _line(f"{_ico} {_dir} MoM", pv.get("mom")))
            if blk:
                head += (f'<div style="font-size:12px;color:#c8c8d0;background:#15181f;'
                         f'border:1px solid #2a2e37;border-radius:6px;padding:6px 10px;'
                         f'margin-bottom:12px;line-height:1.7">'
                         f'<b>📊 판가 vs 물량 분해</b> '
                         f'<span style="color:#9aa0aa">(단가=금액÷중량 · 근사식이라 '
                         f'판가×물량 합이 금액과 미세차 가능)</span>{blk}</div>')
    if companies:
        chips = "".join(
            '<span style="display:inline-block;background:#1c1f26;color:#e6edf3;'
            'border:1px solid #2a2e37;border-radius:12px;padding:3px 10px;margin:2px;'
            f'font-size:13px">{e(c)}</span>' for c in companies)
        comp_html = ('<div style="font-weight:600;margin:10px 0 4px">🏢 관련 상장사 '
                     f'({len(companies)})</div><div>{chips}</div>')
    else:
        comp_html = ('<div style="color:#9aa0aa;font-size:13px;margin:10px 0">🏢 관련 상장사 '
                     '— 큐레이션·채널 미등록</div>')
    if items:
        items_html = _exposure_table(
            items, with_companies=True, title="🚢 매칭 품목",
            subtitle="· 수출·수입 추세(YoY·ΔYoY·MoM·ΔMoM) · 최신월 수출액순")
    else:
        items_html = ''
    # 📋 품목별 해설 — 검색레벨 합산노드(선택방향) 단일 코멘트(요약·신호·단가·G그룹·
    # 동력 P/Q·분기 진도율). 산업트렌드 카드와 동일 생성기(industry.item_comment_html)
    # + 위 판가물량과 동일 소스라 일관(사용자 2026-06-22 '한 레벨·한 방향으로'). HS10
    # 단위 월별이력은 관세청 미제공 → MTI품목 단위 합산이 시계열 바닥(스코프 라벨로 명시).
    commentary = ''
    cmt = (data.get("hs_comment") or "").strip()
    if cmt:
        _dir2 = data.get("hs_dir") or "수출"
        _n2 = data.get("hs_n") or 0
        commentary = (
            '<div style="font-weight:600;margin:14px 0 4px">📋 품목별 해설 '
            '<span style="font-size:12px;color:#9aa0aa;font-weight:400">'
            f'· {e(_dir2)} 기준 · 연계 MTI품목 {_n2}개 합산(월별이력 단위) · '
            '산업트렌드 동일</span></div>'
            f'<div style="margin:6px 0;padding:8px 10px;background:var(--surface-2,#15181f);'
            f'border:1px solid var(--border-soft,#2a2e37);border-radius:8px">{cmt}</div>')
    return f'<div style="line-height:1.5">{head}{comp_html}{items_html}{commentary}</div>'


def render_free(data: dict) -> str:
    """데이터 → 자체완결 HTML 보고서(인라인 스타일·₩0). 순수."""
    if data.get("mode") == "item":
        return _render_free_item(data)
    e = _html.escape
    name = e(data.get("name") or data.get("query") or "—")
    code = e(data.get("code") or "")
    products = data.get("products") or []
    exposure = data.get("exposure") or []
    head = (f'<div style="font-size:18px;font-weight:700;margin-bottom:2px">{name}'
            f'{" · " + code if code else ""}</div>'
            '<div style="font-size:12px;color:#9aa0aa;margin-bottom:12px">'
            '기업 중심 보고서 · DART 매출구성 + 관세청 수출입 품목 노출 · 무료(데이터)</div>')
    # 제품 구성 (DART)
    if products:
        prows = "".join(
            f'<tr><td style="padding:4px 8px">{e(p.get("name",""))}</td>'
            f'<td style="padding:4px 8px;text-align:right">'
            f'{(str(p["share_pct"]) + "%") if p.get("share_pct") is not None else "—"}</td></tr>'
            for p in products[:15])
        prod_html = ('<div style="font-weight:600;margin:10px 0 4px">📦 제품 구성 (DART 매출비중)</div>'
                     '<table style="border-collapse:collapse;font-size:13px;width:100%">'
                     '<thead><tr><th style="text-align:left;padding:4px 8px;color:#9aa0aa">제품</th>'
                     '<th style="text-align:right;padding:4px 8px;color:#9aa0aa">매출비중</th></tr></thead>'
                     f'<tbody>{prows}</tbody></table>')
    else:
        prod_html = ('<div style="color:#9aa0aa;font-size:13px;margin:10px 0">📦 제품 구성 — '
                     'DART 매출표 미확보(비상장·해외·미발견)</div>')
    # 관세청 노출 (수출·수입 추세 YoY·ΔYoY·MoM·ΔMoM 포함 — 산업트렌드와 동일 계산식)
    if exposure:
        exp_html = _exposure_table(
            exposure, title="🚢 관세청 수출입 품목 노출",
            subtitle="· 회사별 채널 매핑 + 관세청 수출입 · 수출·수입 추세(YoY·ΔYoY·MoM·ΔMoM)"
                     " · 최신월 수출액순")
    else:
        exp_html = ('<div style="color:#9aa0aa;font-size:13px;margin:14px 0">🚢 관세청 노출 — '
                    '매핑된 품목 없음(회사별 채널·큐레이션 미등록)</div>')
    return f'<div style="line-height:1.5">{head}{prod_html}{exp_html}</div>'


def _llm_digest(data: dict) -> str:
    """LLM 입력용 압축 텍스트(데이터만 — 환각 차단)."""
    if data.get("mode") == "item":
        lines = [f"품목 역검색: {data.get('name')}"]
        if data.get("companies"):
            lines.append("관련 상장사: " + ", ".join(data["companies"][:20]))
        if data.get("items"):
            lines.append("매칭 품목(최신월 수출|수입): " + ", ".join(
                f"{x['item']}=수출{_eok_usd(x.get('export_usd'))}/수입{_eok_usd(x.get('import_usd'))}"
                for x in data["items"][:12]))
        return "\n".join(lines)
    lines = [f"회사: {data.get('name')} ({data.get('code') or '비상장/해외'})"]
    if data.get("products"):
        lines.append("제품(매출비중): " + ", ".join(
            f"{p.get('name')}({p['share_pct']}%)" if p.get("share_pct") is not None
            else str(p.get("name")) for p in data["products"][:12]))
    if data.get("exposure"):
        lines.append("관세청 수출입 품목 노출(최신월 수출|수입): " + ", ".join(
            f"{x['item']}=수출{_eok_usd(x.get('export_usd'))}/수입{_eok_usd(x.get('import_usd'))}"
            for x in data["exposure"][:12]))
    return "\n".join(lines)


_LLM_SYS = (
    "너는 한국 수출입·기업 애널리스트다. 아래 '데이터'(회사면 DART 매출구성 + 관세청 "
    "수출입 품목 노출 / 품목이면 관련 상장사 + 그 품목 수출입)만 근거로 5~7문장 한국어 "
    "요약을 써라. 규칙: (1) 주어진 수치/품목/기업만 사용, 새 숫자·사건 날조 금지 "
    "(2) 회사면 주력 제품·매출 집중도와 수출입 품목 노출, 품목이면 그 품목의 수출입 "
    "흐름과 관련 상장사 구도(수혜/부담 방향은 데이터 범위 내 중립 서술) (3) 투자 권유 "
    "금지·교육 목적. 마크다운 없이 평문."
)


def render_llm(data: dict, model: str | None = None) -> tuple[str, dict]:
    """free 보고서 + Gemini 산문 요약. (html, meta{used, cost_note}). 키부재/실패 시
    free 로 graceful (used=False)."""
    free = render_free(data)
    try:
        from trade import llm_insights, llm_usage
        if not llm_insights._llm_ready():
            return (free + _note("⚠️ AI 요약 생략 — Gemini 백엔드 없음(무료 보고서만)."),
                    {"used": False})
        mdl = model or llm_insights.DEFAULT_MODEL
        llm = llm_insights.make_chat(mdl, temperature=0.3)
        resp = llm.invoke([("system", _LLM_SYS), ("human", _llm_digest(data))])
        um = getattr(resp, "usage_metadata", None) or {}
        in_tok, out_tok = um.get("input_tokens", 0), um.get("output_tokens", 0)
        try:
            llm_usage.record(mdl, in_tok, out_tok)
        except Exception:
            pass
        raw = (getattr(resp, "content", "") or "").strip()
        if not raw:
            return (free + _note("⚠️ AI 요약 비어있음 — 무료 보고서만."), {"used": False})
        txt = _html.escape(raw)
        ai = ('<div style="font-weight:600;margin:14px 0 4px">🤖 AI 요약 (Gemini · 유료)</div>'
              f'<div style="font-size:13px;background:#1c1f26;color:#e6edf3;border:1px solid #2a2e37;'
              f'border-radius:8px;padding:10px 12px;white-space:pre-wrap">{txt}</div>')
        html = free + ai
        # 유료 산출물 → 대시보드 아카이브 적립 (사용자 2026-06-18). best-effort.
        try:
            from trade import report_archive
            cost_krw = round(llm_usage.cost_usd(mdl, in_tok, out_tok)
                             * llm_usage.KRW_PER_USD, 1)
            report_archive.record(
                kind="🏢 기업", title=(data.get("name") or data.get("query") or "기업 보고서"),
                html_body=html, summary=_archive_summary(data, raw),
                cost_krw=cost_krw or None, tg=render_telegram(data, raw))
            report_archive.regenerate()
        except Exception as exc:
            log.warning("company_report archive: %s", exc)
        return (html, {"used": True, "model": mdl})
    except Exception as exc:
        log.warning("company_report LLM: %s", exc)
        return (free + _note(f"⚠️ AI 요약 실패({exc.__class__.__name__}) — 무료 보고서만."),
                {"used": False})


def _archive_summary(data: dict, ai_text: str) -> str:
    """아카이브 색인 카드 본문(검색용 평문) — 데이터 핵심 + AI 요약."""
    if data.get("mode") == "item":
        parts = [f"🔎 {data.get('name') or data.get('query') or ''} — 관련 기업"]
        if data.get("companies"):
            parts.append("관련 상장사: " + ", ".join(data["companies"][:15]))
        if data.get("items"):
            parts.append("매칭 품목: " + ", ".join(x["item"] for x in data["items"][:12]))
    else:
        head = (data.get("name") or data.get("query") or "")
        if data.get("code"):
            head += f" · {data['code']}"
        parts = [f"🏢 {head}"]
        if data.get("products"):
            parts.append("제품: " + ", ".join(str(p.get("name")) for p in data["products"][:12]))
        if data.get("exposure"):
            parts.append("관세청 노출: " + ", ".join(x["item"] for x in data["exposure"][:12]))
    if ai_text:
        parts.append("🤖 " + ai_text)
    return "\n".join(parts)


def _note(msg: str) -> str:
    return (f'<div style="font-size:12px;color:#ff9500;margin-top:12px">'
            f'{_html.escape(msg)}</div>')


def render_telegram(data: dict, ai_text: str = "") -> str:
    """보고서 → 텔레그램 HTML(≤4096, 표 대신 줄 목록). 채널 전송용. 순수."""
    e = _html.escape
    if data.get("mode") == "item":
        name = e(data.get("name") or data.get("query") or "—")
        lines = [f"🔎 <b>{name}</b> — 관련 기업 (품목 역검색)",
                 "관세청 수출입 품목 ↔ 관련 상장사", ""]
        companies = data.get("companies") or []
        if companies:
            lines.append(f"🏢 <b>관련 상장사</b> ({len(companies)})")
            lines.append(", ".join(e(c) for c in companies))   # 전체
        else:
            lines.append("🏢 관련 상장사 — 미등록")
        items = data.get("items") or []
        if items:
            lines += ["", "🚢 <b>매칭 품목</b> (수출/수입 · YoY·MoM)"]
            for x in items:                                    # 전체
                lines.append(_tg_item_line(x))
        if ai_text:
            lines += ["", "🤖 <b>AI 요약</b>", e(ai_text)]
        return "\n".join(lines)          # 전문(전송 시 send_long 이 4096 단위 분할)
    name = e(data.get("name") or data.get("query") or "—")
    code = e(data.get("code") or "")
    lines = [f"🏢 <b>{name}</b>{(' · ' + code) if code else ''} — 기업 보고서",
             "DART 매출구성 + 관세청 수출입 품목 노출", ""]
    products = data.get("products") or []
    if products:
        lines.append("📦 <b>제품 구성</b> (DART 매출비중)")
        for p in products:                                     # 전체
            sh = f" ({p['share_pct']}%)" if p.get("share_pct") is not None else ""
            lines.append(f"• {e(str(p.get('name','')))}{sh}")
    else:
        lines.append("📦 제품 구성 — DART 매출표 미확보")
    lines.append("")
    exposure = data.get("exposure") or []
    if exposure:
        lines.append("🚢 <b>관세청 수출입 품목 노출</b> (수출/수입 · YoY·MoM)")
        for x in exposure:                                     # 전체
            lines.append(_tg_item_line(x))
    else:
        lines.append("🚢 관세청 노출 — 매핑된 품목 없음")
    if ai_text:
        lines += ["", "🤖 <b>AI 요약</b>", e(ai_text)]
    return "\n".join(lines)              # 전문(전송 시 send_long 이 4096 단위 분할)


def _tg_item_line(x: dict) -> str:
    """노출/매칭 품목 한 줄 — 수출·수입 값 + 양방향 YoY·MoM(있을 때만)."""
    def _p(v, suf="%"):
        return f" {v:+.1f}{suf}" if isinstance(v, (int, float)) else ""
    exp = (f"수출 {_eok_usd(x.get('export_usd'))}"
           + (f"({_p(x.get('export_yoy')).strip()}YoY{_p(x.get('export_mom')).strip()}MoM)"
              if x.get("export_yoy") is not None or x.get("export_mom") is not None else ""))
    imp = (f"수입 {_eok_usd(x.get('import_usd'))}"
           + (f"({_p(x.get('import_yoy')).strip()}YoY{_p(x.get('import_mom')).strip()}MoM)"
              if x.get("import_yoy") is not None or x.get("import_mom") is not None else ""))
    return f"• {_html.escape(x['item'])} — {exp} / {imp}"


def send_to_channel(query: str, mode: str = "free", api_key: str | None = None) -> dict:
    """보고서를 trade 텔레그램 채널로 전송 (사용자 2026-06-17 '버튼→채널'). free=₩0,
    llm=AI 요약 포함(유료). {ok, sent}."""
    data = gather(query, api_key)
    ai_text = ""
    if mode == "llm":
        try:
            from trade import llm_insights
            if llm_insights._llm_ready():
                from trade import llm_usage
                mdl = llm_insights.DEFAULT_MODEL
                resp = llm_insights.make_chat(mdl, temperature=0.3).invoke(
                        [("system", _LLM_SYS), ("human", _llm_digest(data))])
                um = getattr(resp, "usage_metadata", None) or {}
                try:
                    llm_usage.record(mdl, um.get("input_tokens", 0), um.get("output_tokens", 0))
                except Exception:
                    pass
                ai_text = (getattr(resp, "content", "") or "").strip()
        except Exception as exc:
            log.warning("send_to_channel LLM: %s", exc)
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
        log.warning("send_to_channel: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": sent > 0, "sent": sent}


def build(query: str, mode: str = "free", api_key: str | None = None,
          leaf: str | None = None, direction: str | None = None) -> str:
    """query + mode('free'|'llm') → 보고서 HTML. leaf=히트맵 클릭 품목명(breadcrumb),
    direction='exp'/'imp'=히트맵 방향(해설·판가물량을 그 방향으로)."""
    data = gather(query, api_key, leaf=leaf, direction=direction)
    if mode == "llm":
        return render_llm(data)[0]
    return render_free(data)


def main(argv: list[str] | None = None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="기업 중심 보고서 (회사→제품+관세청 노출)")
    p.add_argument("query", help="회사명 또는 6자리 코드 (예: 삼성전자 / 005930)")
    p.add_argument("--llm", action="store_true", help="AI 산문 요약 추가(유료)")
    args = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        for c in (Path.home() / "stock" / ".env", Path.cwd() / ".env"):
            if c.exists():
                load_dotenv(c, override=False)
    except Exception:
        pass
    data = gather(args.query)
    print(f"회사: {data['name']} ({data.get('code') or '—'}) | 제품 {len(data['products'])} | "
          f"관세청 노출 {len(data['exposure'])}")
    html = render_llm(data)[0] if args.llm else render_free(data)
    out = os.path.join("/tmp", f"company_report_{data.get('code') or 'x'}.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"💾 {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
