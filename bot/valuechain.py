"""밸류체인(공급망) 그래프 조회 — 다소스 집합 단일 소스(사용자 2026-06-24).

엣지 = ① kg 관계후보(블로그·DART 계약공시: 납품/고객/계열/취급품목/테마) +
② 관세청 수출입 레퍼런스북(reference_book.build_rows: 회사—수출품목→품목, MTI·DART
매출구성·테마·채널·운영자 보강 reinforce 병합분). 소스별 graceful.

소비처(이 모듈 위에 얹음):
  - 대시보드 valuechain.html (bot.dashboard._load_valuechain_edges → load_edges).
  - ② NOAH 분석 컨텍스트 주입 (format_for_prompt).
  - ③ 납품사 스크리너 /valuechain (format_for_telegram · top_suppliers).

⛔ LLM 추가 0 — 이미 적재된 그래프 조회만(₩0). 모든 소스 갱신은 적재 단계에서 자동.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bot.valuechain")

_SUPPLY = ("납품", "고객")            # 회사→회사 공급 관계(A 납품/고객 B = A가 B에 공급)


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


def load_edges() -> list[dict]:
    """다소스 엣지 집합. 각 {company,relation,target,evidence,source,status,kind}.
    kind='kg'(블로그·DART) / 'trade'(관세청). graceful(한 소스 실패해도 나머지)."""
    edges: list[dict] = []
    # ① kg 관계후보 큐(런타임 CSV)
    try:
        import csv
        from trade import kg_candidates
        p = kg_candidates._candidates_csv_path()
        if p.exists():
            with open(p, encoding="utf-8-sig", newline="") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) >= 7 and row[0] and row[2]:
                        edges.append({"company": row[0], "relation": row[1],
                                      "target": row[2], "evidence": row[3],
                                      "source": row[4], "status": row[6],
                                      "kind": "kg"})
    except Exception as exc:
        log.warning("valuechain: kg edges load failed: %s", exc)
    # ② 관세청 수출입 레퍼런스북(통합 회사↔품목)
    try:
        from trade import reference_book
        for row in reference_book.build_rows():
            item = (row.get("name") or "").strip()
            hs = row.get("hs") or []
            hs_lbl = (f"HS {hs[0]}" + ("…" if len(hs) > 1 else "")) if hs else ""
            for co in (row.get("companies") or []):
                if co and item:
                    edges.append({"company": co, "relation": "수출품목",
                                  "target": item, "evidence": hs_lbl,
                                  "source": "관세청", "status": "", "kind": "trade"})
    except Exception as exc:
        log.warning("valuechain: trade refbook edges load failed: %s", exc)
    return edges


def neighborhood(company: str, edges: list[dict] | None = None) -> dict:
    """회사의 밸류체인 이웃. 반환 {company, suppliers, customers, exports, products,
    themes, affiliates, peers:[(name,shared)]}. 빈 회사/무매칭 → 빈 구조(순수)."""
    empty = {"company": company or "", "suppliers": [], "customers": [],
             "exports": [], "products": [], "themes": [], "affiliates": [],
             "peers": []}
    nx = _norm(company)
    if not nx:
        return empty
    if edges is None:
        edges = load_edges()
    KG = [e for e in edges if e.get("kind") != "trade"]
    TR = [e for e in edges if e.get("kind") == "trade"]

    def _uniq(seq):
        out, seen = [], set()
        for x in seq:
            k = _norm(x)
            if x and k not in seen:
                seen.add(k)
                out.append(x)
        return out

    suppliers = _uniq(e["company"] for e in KG
                      if _norm(e.get("target")) == nx and e.get("relation") in _SUPPLY)
    customers = _uniq(e["target"] for e in KG
                      if _norm(e.get("company")) == nx and e.get("relation") in _SUPPLY)
    products = _uniq(e["target"] for e in KG
                     if _norm(e.get("company")) == nx and e.get("relation") == "취급품목")
    themes = _uniq(e["target"] for e in KG
                   if _norm(e.get("company")) == nx and e.get("relation") == "테마")
    affiliates = _uniq(
        (e["target"] if _norm(e.get("company")) == nx else e["company"])
        for e in KG if e.get("relation") == "계열"
        and nx in (_norm(e.get("company")), _norm(e.get("target"))))
    exports = _uniq(e["target"] for e in TR if _norm(e.get("company")) == nx)
    # 동종 회사(peer) = 같은 수출품목을 다루는 다른 회사
    my_items = {_norm(e.get("target")) for e in TR if _norm(e.get("company")) == nx}
    peer_cnt: dict[str, int] = {}
    for e in TR:
        if _norm(e.get("company")) != nx and _norm(e.get("target")) in my_items:
            peer_cnt[e["company"]] = peer_cnt.get(e["company"], 0) + 1
    peers = sorted(peer_cnt.items(), key=lambda kv: -kv[1])[:40]
    return {"company": company, "suppliers": suppliers, "customers": customers,
            "exports": exports, "products": products, "themes": themes,
            "affiliates": affiliates, "peers": peers}


def has_data(nb: dict) -> bool:
    return bool(nb.get("suppliers") or nb.get("customers") or nb.get("exports")
               or nb.get("products") or nb.get("themes") or nb.get("affiliates")
               or nb.get("peers"))


def format_for_prompt(company: str, edges: list[dict] | None = None) -> str:
    """② NOAH 분석 컨텍스트 텍스트 블록. 데이터 없으면 '' (분석 영향 0)."""
    nb = neighborhood(company, edges)
    if not has_data(nb):
        return ""
    L = [f"[밸류체인 — {company} (자동발굴: DART 계약공시·블로그·관세청 수출입)]"]
    if nb["suppliers"]:
        L.append("· 공급사(→이 회사 납품): " + ", ".join(nb["suppliers"][:15]))
    if nb["customers"]:
        L.append("· 고객·납품처(이 회사가 공급): " + ", ".join(nb["customers"][:15]))
    if nb["exports"]:
        L.append("· 수출품목(관세청): " + ", ".join(nb["exports"][:15]))
    if nb["products"]:
        L.append("· 취급품목(계약공시): " + ", ".join(nb["products"][:15]))
    if nb["peers"]:
        L.append("· 동종 회사(같은 수출품목): "
                 + ", ".join(n for n, _ in nb["peers"][:12]))
    if nb["affiliates"]:
        L.append("· 계열: " + ", ".join(nb["affiliates"][:10]))
    L.append("(주의: 자동발굴이라 일부 부정확 가능 — 참고 신호로만, 확정사실 단정 금지.)")
    return "\n".join(L)


def format_for_telegram(company: str, edges: list[dict] | None = None) -> str:
    """③ /valuechain <회사> HTML 응답. 데이터 없으면 안내."""
    import html as _h
    nb = neighborhood(company, edges)
    co = _h.escape(company)
    if not has_data(nb):
        return (f"🔗 <b>{co}</b> — 밸류체인 데이터 없음.\n"
                "DART 계약공시·블로그 자동발굴이 쌓이면 채워집니다(대시보드 🔗 밸류체인).")
    out = [f"🔗 <b>{co}</b> 밸류체인"]

    def _sec(emoji, title, items):
        if items:
            out.append(f"{emoji} <b>{title}</b>: " + _h.escape(", ".join(items[:15])))
    _sec("⬅️", "공급사(→이 회사 납품)", nb["suppliers"])
    _sec("➡️", "고객·납품처", nb["customers"])
    _sec("🛃", "수출품목(관세청)", nb["exports"])
    _sec("📦", "취급품목", nb["products"])
    if nb["peers"]:
        out.append("👥 <b>동종 회사</b>: "
                   + _h.escape(", ".join(n for n, _ in nb["peers"][:12])))
    _sec("🏷️", "테마", nb["themes"])
    _sec("🔗", "계열", nb["affiliates"])
    if nb["suppliers"]:
        out.append(f"💡 {co} 호재·실적 시 공급사들이 수혜 후보")
    out.append("<i>대시보드: NOAH archive → 🔗 밸류체인</i>")
    return "\n".join(out)


def top_suppliers(edges: list[dict] | None = None, limit: int = 20) -> list[tuple]:
    """③ 스크리너 — 여러 (서로 다른) 회사에 납품하는 공급사 랭킹 [(회사, 고객수)].
    고객 다변화 = 안정적 밸류체인 플레이. kg 납품/고객 엣지 기준."""
    if edges is None:
        edges = load_edges()
    cust: dict[str, set] = {}
    for e in edges:
        if e.get("kind") != "trade" and e.get("relation") in _SUPPLY:
            cust.setdefault(e["company"], set()).add(_norm(e.get("target")))
    ranked = sorted(((c, len(s)) for c, s in cust.items()), key=lambda kv: -kv[1])
    return ranked[:limit]


def top_connected(edges: list[dict] | None = None, limit: int = 20) -> list[tuple]:
    """연결수(degree) 상위 회사 [(회사, 연결수)] — 검색 진입점 힌트."""
    if edges is None:
        edges = load_edges()
    deg: dict[str, int] = {}
    for e in edges:
        if e.get("company"):
            deg[e["company"]] = deg.get(e["company"], 0) + 1
        if e.get("kind") != "trade" and e.get("relation") in ("납품", "고객", "계열") and e.get("target"):
            deg[e["target"]] = deg.get(e["target"], 0) + 1
    return sorted(deg.items(), key=lambda kv: -kv[1])[:limit]
