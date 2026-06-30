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

import json as _json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.valuechain")

_KST = timezone(timedelta(hours=9))   # 모든 시각 KST 명시계산(서버 로컬타임 의존 금지)

_SUPPLY = ("납품", "고객")            # 회사→회사 공급 관계(A 납품/고객 B = A가 B에 공급)
_ITEM_REL = ("수출품목", "취급품목", "테마")   # 회사→품목/테마(품목 검색 대상)

# ── kg 엣지 신선도(노후화) 수명주기 (사용자 2026-06-30, hermes-agent #7816 차용) ──
# 자동발굴(블로그·DART) kg 엣지는 학습일(추출일)만 고정돼 늙지 않음 → 1년 묵은 단일
# 블로그발 추측이 신규 재확인 관계와 동급으로 NOAH 컨텍스트·페이지에 남는 문제. 학습일
# 경과로 active→stale→archived 계산(로드시·stateless, 스케줄러 0). 운영자 vouch
# (승인/등재)·관세청 trade(매 로드 live 재생성)·날짜불명 = 면제(항상 active).
# ⚠️ universal — 신선도 룰은 전시장 분석 컨텍스트에 동일 적용. kg 가 KR 소스
# (DART/블로그)인 건 데이터소스 사유지 market 게이트 아님. 큐레이션 상태축과 직교
# (등재된 엣지도 늙을 수 있으나 사람 vouch 가 노후화를 이김 → '숨겨짐' 사고 방지).
_STALE_AFTER_DAYS = int(os.environ.get("KG_STALE_AFTER_DAYS") or 180)
_ARCHIVE_AFTER_DAYS = int(os.environ.get("KG_ARCHIVE_AFTER_DAYS") or 365)
_VOUCHED_STATUS = ("승인", "등재")     # 운영자 확인분 — 노후화 면제


def _parse_date(s: str):
    """'YYYY-MM-DD…' → date(또는 None). graceful(형식 불명 → None=면제)."""
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def freshness(edge: dict, today=None) -> str:
    """kg 엣지 신선도 'active'|'stale'|'archived'(순수). trade(live 재생성)·운영자
    vouch(승인/등재)·날짜불명 → 항상 active(보수적 유지). 그 외 학습일 경과로 판정."""
    if edge.get("kind") == "trade":
        return "active"
    if (edge.get("status") or "").strip() in _VOUCHED_STATUS:
        return "active"
    d = _parse_date(edge.get("date"))
    if d is None:
        return "active"
    age = ((today or datetime.now(_KST).date()) - d).days
    if age > _ARCHIVE_AFTER_DAYS:
        return "archived"
    if age > _STALE_AFTER_DAYS:
        return "stale"
    return "active"

# 운영자가 '잘못된 매칭'으로 숨긴 엣지(🗑️, 사용자 2026-06-29). id='회사|관계|대상'.
# 자동 도출 엣지라 하드삭제 대신 영구 suppression — 모든 소비처(페이지·텔레그램·
# NOAH 컨텍스트)에서 load_edges 가 일괄 제외. dart_reinforce_rejected.json 과 동일 패턴.
_SUPPRESS_PATH = Path.home() / ".tradingagents" / "valuechain_suppressed.json"
_SUPPRESS_LOCK = threading.Lock()   # 쓰레드 서버 동시 🗑️ read-merge-write 직렬화


def _edge_id(company: str, relation: str, target: str) -> str:
    return f"{company}|{relation}|{target}"


def load_suppressed() -> set[str]:
    """숨긴 엣지 id 집합('회사|관계|대상'). 부재/실패 → set() (graceful)."""
    try:
        if _SUPPRESS_PATH.exists():
            data = _json.loads(_SUPPRESS_PATH.read_text("utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
    except Exception as exc:
        log.warning("valuechain: suppressed load failed: %s", exc)
    return set()


def add_suppressed(edge_id: str) -> bool:
    """엣지 id 영구 숨김 추가. 반환 저장 여부. read-merge-write(멀티프로세스 관대)
    + 원자적 교체. id 형식('회사|관계|대상', 파이프 ≥2) 아니면 거부."""
    edge_id = (edge_id or "").strip()
    if not edge_id or edge_id.count("|") < 2:
        return False
    # read-merge-write 를 락으로 직렬화 — ThreadingHTTPServer 동시 🗑️ 클릭이
    # 서로의 추가분을 덮어써 silent 유실되는 race 차단(리뷰 finding A). 쓰기 자체는
    # tmp+replace 로 원자적이라 동시 읽기(load_edges)는 항상 완전한 파일을 본다.
    try:
        with _SUPPRESS_LOCK:
            cur = load_suppressed()
            if edge_id in cur:
                return True                   # 멱등
            cur.add(edge_id)
            _SUPPRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _SUPPRESS_PATH.with_suffix(".json.tmp")
            tmp.write_text(_json.dumps(sorted(cur), ensure_ascii=False), "utf-8")
            tmp.replace(_SUPPRESS_PATH)
        return True
    except Exception as exc:
        log.warning("valuechain: suppress add failed: %s", exc)
        return False


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


def _uniq(seq) -> list:
    """순서 보존 dedup(정규화 기준). 빈 값 제외."""
    out, seen = [], set()
    for x in seq:
        k = _norm(x)
        if x and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def load_edges(include_archived: bool = False) -> list[dict]:
    """다소스 엣지 집합. 각 {company,relation,target,evidence,source,status,kind,
    freshness}. kind='kg'(블로그·DART) / 'trade'(관세청). graceful(한 소스 실패해도
    나머지). 각 엣지에 freshness('active'|'stale'|'archived') 부착 — archived 는
    기본 제외(include_archived=True 면 포함, 페이지 카운트·토글용)."""
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
                                      "date": row[5],   # 추출일=학습한 날짜(YYYY-MM-DD)
                                      "kind": "kg"})
    except Exception as exc:
        log.warning("valuechain: kg edges load failed: %s", exc)
    # ② 관세청 수출입 레퍼런스북(통합 회사↔품목)
    try:
        from trade import reference_book
        for row in reference_book.build_rows():
            item = (row.get("name") or "").strip()
            industry = (row.get("industry") or "").strip()   # 업종 검색용
            hs = row.get("hs") or []
            hs_lbl = (f"HS {hs[0]}" + ("…" if len(hs) > 1 else "")) if hs else ""
            for co in (row.get("companies") or []):
                if co and item:
                    edges.append({"company": co, "relation": "수출품목",
                                  "target": item, "evidence": hs_lbl,
                                  "source": "관세청", "status": "", "kind": "trade",
                                  "industry": industry})
    except Exception as exc:
        log.warning("valuechain: trade refbook edges load failed: %s", exc)
    # 운영자 숨김(🗑️ 잘못된 매칭) 일괄 제외 — 페이지·텔레그램·NOAH 컨텍스트 공통.
    sup = load_suppressed()
    if sup:
        edges = [e for e in edges
                 if _edge_id(e.get("company", ""), e.get("relation", ""),
                             e.get("target", "")) not in sup]
    # 신선도 부착 + archived 기본 제외(자동발굴 노후 관계 — 페이지/NOAH/텔레그램 위생).
    today = datetime.now(_KST).date()   # KST 명시(학습일도 KST wall-clock 기준)
    out: list[dict] = []
    for e in edges:
        fr = freshness(e, today)
        if fr == "archived" and not include_archived:
            continue
        e["freshness"] = fr
        out.append(e)
    return out


def active_edges(edges: list[dict]) -> list[dict]:
    """active 만(stale/archived 제외) — NOAH 분석 컨텍스트 주입용. freshness 미부착
    엣지(테스트/외부 호출)는 active 로 간주(하위호환)."""
    return [e for e in edges if e.get("freshness", "active") == "active"]


def resolve_company(query: str, edges: list[dict]) -> str | None:
    """부분 일치 관대 해석(사용자 2026-06-24) — 정확(norm) 우선, 없으면 norm 포함
    중 연결수(degree) 최대. 표준 회사명 반환(없으면 None). 표기 조금 달라도 매칭."""
    nq = _norm(query)
    if not nq:
        return None
    deg: dict[str, int] = {}
    for e in edges:
        c = e.get("company")
        if c:
            deg[c] = deg.get(c, 0) + 1
        t = e.get("target")
        if t and e.get("kind") != "trade" and e.get("relation") in _SUPPLY + ("계열",):
            deg[t] = deg.get(t, 0) + 1
    ents = list(deg.keys())
    exact = [n for n in ents if _norm(n) == nq]
    if exact:
        return max(exact, key=lambda n: deg[n])
    part = [n for n in ents if nq in _norm(n)]
    if part:
        return max(part, key=lambda n: deg[n])
    return None


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


def _degree_maps(edges: list[dict]) -> tuple[dict, dict, dict]:
    """(회사, 품목, 업종) → 빈도. 검색 타입 판별(회사>품목>업종)용."""
    co: dict[str, int] = {}
    it: dict[str, int] = {}
    ind: dict[str, int] = {}
    for e in edges:
        c, t, r = e.get("company"), e.get("target"), e.get("relation")
        if c:
            co[c] = co.get(c, 0) + 1
        if t and r in ("납품", "고객", "계열") and e.get("kind") != "trade":
            co[t] = co.get(t, 0) + 1
        if t and r in _ITEM_REL:
            it[t] = it.get(t, 0) + 1
        g = e.get("industry")
        if g:
            ind[g] = ind.get(g, 0) + 1
    return co, it, ind


def resolve_kind(query: str, edges: list[dict]) -> tuple:
    """검색어 → ('company'|'item'|'industry', 표준명) 또는 (None, None).
    수출입 대시보드처럼 품목·업종 검색도 회사 검색처럼 동작(사용자 2026-06-24).
    우선순위 회사 > 품목 > 업종(정확 norm 우선 → 부분 포함 중 빈도 최대)."""
    nq = _norm(query)
    if not nq:
        return (None, None)
    co, it, ind = _degree_maps(edges)
    order = (("company", co), ("item", it), ("industry", ind))

    def _exact(d):
        ex = [n for n in d if _norm(n) == nq]
        return max(ex, key=lambda n: d[n]) if ex else None

    def _part(d):
        pa = [n for n in d if nq in _norm(n)]
        return max(pa, key=lambda n: d[n]) if pa else None
    # 정확 매칭이 부분 매칭을 가로지름('반도체'=업종 정확 > '메모리반도체' 품목 부분).
    for kind, d in order:
        r = _exact(d)
        if r:
            return (kind, r)
    for kind, d in order:
        r = _part(d)
        if r:
            return (kind, r)
    return (None, None)


def item_neighborhood(item: str, edges: list[dict] | None = None) -> dict:
    """품목 → 그 품목 다루는 회사들. {item, customs(관세청 수출), contract(계약공시
    취급/테마), hs, industries}. 수출입 대시보드의 '품목→관련 상장사'에 대응."""
    if edges is None:
        edges = load_edges()
    ni = _norm(item)
    customs = _uniq(e["company"] for e in edges
                    if e.get("relation") == "수출품목" and _norm(e.get("target")) == ni)
    contract = _uniq(e["company"] for e in edges
                     if e.get("relation") in ("취급품목", "테마")
                     and _norm(e.get("target")) == ni)
    hs, inds = "", []
    for e in edges:
        if e.get("relation") == "수출품목" and _norm(e.get("target")) == ni:
            if e.get("evidence") and not hs:
                hs = e["evidence"]
            if e.get("industry") and e["industry"] not in inds:
                inds.append(e["industry"])
    return {"item": item, "customs": customs, "contract": contract,
            "hs": hs, "industries": inds}


def industry_companies(industry: str, edges: list[dict] | None = None) -> list:
    """업종 → 그 업종 회사들(관세청 수출입 레퍼런스북 industry 기준)."""
    if edges is None:
        edges = load_edges()
    ng = _norm(industry)
    return _uniq(e["company"] for e in edges if _norm(e.get("industry")) == ng)


def format_for_prompt(company: str, edges: list[dict] | None = None) -> str:
    """② NOAH 분석 컨텍스트 텍스트 블록. 데이터 없으면 '' (분석 영향 0).
    표기 차이 대비 관대 해석(resolve_company)."""
    if edges is None:
        edges = load_edges()
    # 노후화(stale/archived) 자동발굴 관계는 분석 컨텍스트에서 제외 — 1년 묵은 단일
    # 블로그발 추측이 '사전지식 stale 가정'(7축 6번)으로 환각을 부추기는 것 차단.
    edges = active_edges(edges)
    nb = neighborhood(resolve_company(company, edges) or company, edges)
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


def format_for_telegram(query: str, edges: list[dict] | None = None) -> str:
    """③ /valuechain <회사|품목|업종> HTML 응답. 회사뿐 아니라 품목·업종 검색도
    회사 검색처럼 결과(사용자 2026-06-24, 수출입 대시보드 동작). 부분 일치 관대."""
    import html as _h
    if edges is None:
        edges = load_edges()
    kind, name = resolve_kind(query, edges)
    if kind == "company":
        return _fmt_company_tg(name, edges, _h)
    if kind == "item":
        return _fmt_item_tg(name, edges, _h)
    if kind == "industry":
        return _fmt_industry_tg(name, edges, _h)
    return (f"🔗 <b>{_h.escape(query)}</b> — 밸류체인 데이터 없음.\n"
            "회사·품목·업종으로 검색하세요. DART·블로그·관세청 데이터가 쌓이면 채워집니다.")


def _fmt_company_tg(company: str, edges: list[dict], _h) -> str:
    nb = neighborhood(company, edges)
    co = _h.escape(company)
    out = [f"🔗 <b>{co}</b> 밸류체인 <i>(회사)</i>"]

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
    out.append("<i>대시보드: 주식분석 아카이브 → 🔗 밸류체인</i>")
    return "\n".join(out)


def _fmt_item_tg(item: str, edges: list[dict], _h) -> str:
    nb = item_neighborhood(item, edges)
    hs = f" · {_h.escape(nb['hs'])}" if nb["hs"] else ""
    out = [f"📦 <b>{_h.escape(item)}</b> <i>(품목)</i>{hs}"]
    if nb["customs"]:
        out.append("🛃 <b>수출 회사(관세청)</b>: "
                   + _h.escape(", ".join(nb["customs"][:20])))
    if nb["contract"]:
        out.append("📑 <b>취급 회사(계약공시)</b>: "
                   + _h.escape(", ".join(nb["contract"][:20])))
    if nb["industries"]:
        out.append("🏭 업종: " + _h.escape(", ".join(nb["industries"][:5])))
    out.append("<i>대시보드: 주식분석 아카이브 → 🔗 밸류체인</i>")
    return "\n".join(out)


def _fmt_industry_tg(industry: str, edges: list[dict], _h) -> str:
    cos = industry_companies(industry, edges)
    out = [f"🏭 <b>{_h.escape(industry)}</b> <i>(업종)</i>"]
    if cos:
        out.append("회사: " + _h.escape(", ".join(cos[:30])))
    out.append("<i>대시보드: 주식분석 아카이브 → 🔗 밸류체인</i>")
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
