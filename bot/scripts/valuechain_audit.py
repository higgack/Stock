"""밸류체인 대시보드 전수 검증 — 숫자·로직·일치·데이터품질.

사용자 2026-08-20: "이 밸류체인 대시보드가 로직과 모든것들이 제대로 작동하는지
꼼꼼히 아주 꼼꼼히 검증해줘."

화면이 보여주는 것과 **같은 경로**(`valuechain.load_edges`)를 태워서:
  ① 카운터 산수 — 관계 = 공급망 + 관세청 · 노드 · 출처 문서
  ② 신선도 분류(active/stale/archived)와 화면 표기의 일치
  ③ **검색 해석 이중구현 대조** — 파이썬(텔레그램·NOAH) vs JS(대시보드).
     두 곳이 갈라지면 같은 검색어가 화면과 텔레그램에서 다른 답을 준다.
  ④ 데이터 품질 냄새 — 자기참조·중복·방향 의심·품목에 회사명

    cd ~/stock && .venv/bin/python -m bot.scripts.valuechain_audit
    cd ~/stock && .venv/bin/python -m bot.scripts.valuechain_audit --all

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys
from collections import Counter

_PROBE_VER = 1

# 공급관계인데 상대가 금융·용역이면 '공급망'이 아닐 가능성이 높다 —
# 자동발굴(LLM)이 문장에서 잘못 뽑는 전형적 패턴이라 **확인 대상**으로 띄운다
# (오류 단정 아님 — 실제로 납품하는 경우도 있다).
_NONSUPPLY_HINT = ("증권", "은행", "보험", "자산운용", "캐피탈", "카드",
                   "생명", "화재", "저축은행", "holdings")


def _p(*a):
    print(*a, flush=True)


def _js_like_counters(edges):
    """대시보드 JS 와 **같은 규칙**으로 카운터를 재현한다(_VALUECHAIN_JS 참조).
    파이썬 모듈이 아니라 화면 로직을 흉내내는 게 목적 — 둘이 갈라지는지 본다."""
    CO = ("납품", "고객", "계열")
    deg, comp, docs = Counter(), set(), set()
    for e in edges:
        c, t, r = e.get("company"), e.get("target"), e.get("relation")
        if c:
            deg[c] += 1
            comp.add(c)
        if r in CO and t:
            deg[t] += 1
            comp.add(t)
        if e.get("source"):
            docs.add(e["source"])
    return deg, comp, docs


def main() -> int:
    show_all = "--all" in sys.argv
    from bot import valuechain as vc

    _p(f"valuechain_audit v{_PROBE_VER}")
    all_edges = vc.load_edges(include_archived=True)
    shown = [e for e in all_edges if e.get("freshness") != "archived"]
    ok = [e for e in shown if e.get("company") and e.get("target")]

    # ── ① 카운터 산수 ────────────────────────────────────────────────
    kg = [e for e in ok if e.get("kind") != "trade"]
    tr = [e for e in ok if e.get("kind") == "trade"]
    deg, comp, docs = _js_like_counters(ok)
    _p("")
    _p("① 카운터 (화면 상단 4칸)")
    _p(f"   관계(엣지) {len(ok)}  = 공급망 {len(kg)} + 관세청 {len(tr)}"
       f"  {'✅' if len(ok) == len(kg) + len(tr) else '❌ 합이 안 맞는다'}")
    _p(f"   회사(노드) {len(comp)}")
    _p(f"   출처 문서  {len(docs)}")
    arch = sum(1 for e in all_edges if e.get("freshness") == "archived")
    stale = sum(1 for e in ok if e.get("freshness") == "stale")
    _p(f"   (보관 제외 {arch} · 오래됨 {stale} — 화면은 보관을 카운트로만 표기)")

    # ── ② 신선도 ────────────────────────────────────────────────────
    _p("")
    _p("② 신선도 분류")
    fr = Counter(e.get("freshness") for e in all_edges)
    for k in ("active", "stale", "archived"):
        _p(f"   {k:9} {fr.get(k, 0)}")
    # 관세청·승인분은 면제여야 한다 — 규약이 실제로 지켜지는지.
    bad_tr = [e for e in all_edges
              if e.get("kind") == "trade" and e.get("freshness") != "active"]
    bad_vo = [e for e in all_edges
              if (e.get("status") or "").strip() in vc._VOUCHED_STATUS
              and e.get("freshness") != "active"]
    _p(f"   관세청이 노후화된 건 {len(bad_tr)} {'✅' if not bad_tr else '❌ 면제 규약 위반'}")
    _p(f"   운영자 승인분 노후화 {len(bad_vo)} {'✅' if not bad_vo else '❌ 면제 규약 위반'}")

    # ── ③ 검색 해석 이중구현 대조 ────────────────────────────────────
    _p("")
    _p("③ 검색 해석 — 파이썬(텔레그램·NOAH) vs JS(대시보드)")
    co_map, it_map, ind_map = vc._degree_maps(ok)
    js_deg, _, _ = _js_like_counters(ok)
    # 회사 degree 가 갈라지면 칩 순서·검색 우선순위가 화면과 텔레그램에서 다르다.
    diff = [(n, co_map.get(n, 0), js_deg.get(n, 0))
            for n in set(co_map) | set(js_deg)
            if co_map.get(n, 0) != js_deg.get(n, 0)]
    _p(f"   회사 연결수 불일치 {len(diff)}종 "
       f"{'✅' if not diff else '❌ 화면 칩과 텔레그램 우선순위가 갈린다'}")
    for n, a, b in sorted(diff, key=lambda x: -abs(x[1] - x[2]))[:8]:
        _p(f"      {n[:20]:20} 파이썬 {a} · JS {b}")
    # 상위 회사 몇 개를 실제로 해석시켜 종류가 같은지.
    _p("   상위 검색어 해석(회사/품목/업종):")
    for name, _n in Counter(co_map).most_common(5):
        kind, resolved = vc.resolve_kind(name, ok)
        _p(f"      {name[:20]:20} → {kind} / {resolved}"
           f" {'✅' if kind == 'company' and resolved == name else '⚠️ 확인'}")

    # ── ④ 데이터 품질 ───────────────────────────────────────────────
    _p("")
    _p("④ 데이터 품질")
    self_loop = [e for e in ok if vc._norm(e["company"]) == vc._norm(e["target"])]
    _p(f"   자기참조(A→A) {len(self_loop)} {'✅' if not self_loop else '❌'}")
    for e in self_loop[:5]:
        _p(f"      {e['company']} {e['relation']} {e['target']}")
    dup = Counter(vc._edge_id(e["company"], e["relation"], e["target"])
                  for e in ok)
    dups = [(k, n) for k, n in dup.items() if n > 1]
    _p(f"   중복 엣지 {len(dups)}종 {'✅' if not dups else '⚠️ 같은 관계가 여러 번'}")
    for k, n in sorted(dups, key=lambda x: -x[1])[:5]:
        _p(f"      {k}  ×{n}")
    # 공급관계인데 상대가 금융·용역 — 방향/의미 확인 대상
    susp = [e for e in ok if e.get("relation") in ("납품", "고객")
            and any(h in (e.get("target") or "") for h in _NONSUPPLY_HINT)]
    _p(f"   공급관계인데 상대가 금융·지주 {len(susp)}건 "
       f"{'✅' if not susp else '⚠️ 방향·의미 확인 대상(오류 단정 아님)'}")
    for e in susp[:8]:
        _p(f"      {e['company']} {e['relation']} → {e['target']}"
           f"   [{e.get('source','')}·{e.get('status','')}] {(e.get('evidence') or '')[:40]}")
    # 품목 자리에 회사명이 들어간 경우(취급품목/수출품목 대상이 회사 목록에 존재)
    companies = set(vc._norm(c) for c in co_map)
    item_is_co = [e for e in ok if e.get("relation") in vc._ITEM_REL
                  and vc._norm(e.get("target") or "") in companies]
    _p(f"   품목 자리에 회사명 {len(item_is_co)}건 "
       f"{'✅' if not item_is_co else '⚠️ 품목/회사 혼입 확인'}")
    for e in item_is_co[:5]:
        _p(f"      {e['company']} {e['relation']} → {e['target']}")

    if show_all:
        _p("")
        _p("주요 회사 연결수 상위 20 (화면 칩과 같은 순서여야)")
        for n, c in Counter(js_deg).most_common(20):
            _p(f"   {n[:24]:24} {c}")

    _p("")
    _p("읽는 법: ❌ = 계약 위반(고쳐야 함) · ⚠️ = 사람 확인 대상(자동발굴 특성상")
    _p("        일부 부정확은 정상이라 '오류'로 단정하지 않는다).")
    _p("        ③이 불일치면 같은 검색어가 대시보드와 텔레그램에서 다른 답을 준다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
