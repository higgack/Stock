"""레퍼런스북 관계 후보 자동발굴 (kg-gen 패턴, 사용자 2026-06-23 검토 후 채택).

DART 공시·블로그·회사 설명 텍스트 → LLM(Gemini)로 (회사)-(관계)-(대상) 트리플
**후보**를 추출해 **운영자 승인 CSV 큐**(trade/data/kg_candidates.csv)로만 출력.
⛔ 자동 등재 없음 — 운영자가 검토·승인해 reinforce_approved.csv / 테마 설정에
손으로 옮김(CLAUDE.md '운영자 확인분만 등재' · fuzzy 자동교정 금지 정합).

kg-gen(stair-lab, DSPy) 의 핵심 아이디어(LLM 구조화추출 → 트리플 → dedup)만 차용,
의존성(DSPy) 없이 기존 trade Gemini 인프라(llm_insights.make_chat) 재사용 → ₩0 추가
인프라. 일 호출상한·킬스위치는 llm_insights 와 공유. graceful(키없음/실패 → []).

관계 어휘(레퍼런스북에 실제 actionable 한 것만):
  취급품목  = 회사가 생산/취급하는 품목 (→ 그 품목 회사목록 보강 후보)
  테마      = 회사가 속한 투자 테마
  납품      = 회사 A 가 B 에 납품/공급 (공급망 맥락)
  고객      = 회사 A 의 주요 고객 B
  계열      = 회사 A 의 자회사/계열 B
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("trade.kg_candidates")

# 추출 기본 모델 = flash (엔티티/관계 추출엔 충분·pro 대비 ~10배 저렴, 사용자
# 2026-06-23 비용). 봇 분석모델(TRADE_LLM_MODEL=pro)과 **독립** — env KG_LLM_MODEL
# 또는 --model 로 override. 비용 = limit × 텍스트토큰, flash 라 limit 5 ≈ 수 원.
_DEFAULT_KG_MODEL = os.environ.get("KG_LLM_MODEL") or "gemini-2.5-flash"

_RELATIONS = ("취급품목", "테마", "납품", "고객", "계열")

_SYS_PROMPT = (
    "너는 한국 주식·수출입 도메인의 지식그래프 추출기다. 주어진 텍스트에서 "
    "**상장사(또는 명확한 기업)** 중심의 관계만 (회사, 관계, 대상) 트리플로 뽑아라.\n"
    "- 관계는 정확히 다음 중 하나: " + " / ".join(_RELATIONS) + "\n"
    "- '회사'는 텍스트에 실제 등장한 기업명(추측·일반명사 금지). '대상'은 품목/테마/"
    "다른 기업.\n"
    "- 근거(evidence)는 텍스트에서 그 관계를 뒷받침하는 짧은 인용/요약 1문장.\n"
    "- 확실하지 않으면 넣지 마라(환각·과잉추출 금지). 없으면 빈 배열.\n"
    '- 출력은 JSON 배열만: [{"company":"","relation":"","target":"","evidence":""}]'
)


def build_human_prompt(text: str, source: str = "") -> str:
    """추출 대상 텍스트 → human 프롬프트. 길면 절단(토큰 bound)."""
    body = (text or "").strip()[:6000]
    src = f"[출처: {source}]\n" if source else ""
    return f"{src}다음 텍스트에서 관계 트리플을 추출하라:\n\n{body}"


def parse_triples(raw: str) -> list[dict]:
    """LLM 출력(JSON 배열, 코드펜스 허용) → [{company,relation,target,evidence}].
    관계 어휘 밖·필수필드 결손은 버림. 순수(단위테스트)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1 and s[:nl].strip().lower() in ("json", ""):
            s = s[nl + 1:]
    data = None
    try:
        data = json.loads(s)
    except Exception:
        i, j = s.find("["), s.rfind("]")
        if i != -1 and j > i:
            try:
                data = json.loads(s[i:j + 1])
            except Exception:
                data = None
    out: list[dict] = []
    for it in (data if isinstance(data, list) else []):
        if not isinstance(it, dict):
            continue
        co = str(it.get("company") or "").strip()
        rel = str(it.get("relation") or "").strip()
        tgt = str(it.get("target") or "").strip()
        if not (co and rel and tgt) or rel not in _RELATIONS or co == tgt:
            continue
        out.append({"company": co, "relation": rel, "target": tgt,
                    "evidence": str(it.get("evidence") or "").strip()[:200]})
    return out


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


def filter_candidates(triples: Iterable[dict], *,
                      known_companies: Optional[set] = None,
                      existing_pairs: Optional[set] = None,
                      source: str = "") -> list[dict]:
    """후보 정제 — (a) '회사'가 알려진 상장사일 때만(known_companies 주어지면),
    (b) 이미 레퍼런스북에 있는 (회사,대상)은 제외(existing_pairs), (c) 트리플 dedup.
    출처 부착. 순수(단위테스트). known_companies=None → 회사 필터 생략(전수)."""
    kc = {_norm(c) for c in known_companies} if known_companies else None
    ep = existing_pairs or set()
    seen: set = set()
    out: list[dict] = []
    for t in triples:
        co, rel, tgt = t.get("company", ""), t.get("relation", ""), t.get("target", "")
        if not (co and rel and tgt):
            continue
        if kc is not None and _norm(co) not in kc:
            continue                              # 미상장/추측 기업 제외(노이즈↓)
        if (_norm(co), _norm(tgt)) in ep:
            continue                              # 이미 레퍼런스북 수록
        k = (_norm(co), rel, _norm(tgt))
        if k in seen:
            continue
        seen.add(k)
        out.append({**t, "source": source})
    return out


def _candidates_csv_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "kg_candidates.csv"


_CSV_HEADER = ["회사", "관계", "대상", "근거", "출처", "추출일", "상태"]


def write_candidates_csv(cands: list[dict], path=None) -> int:
    """후보 → 승인 큐 CSV append (헤더 없으면 생성). 같은 (회사,관계,대상) 이미 있으면
    skip(중복 누적 방지). 상태 기본 '후보'. 반환=새로 적은 건수. 운영자 검토 전용."""
    if not cands:
        return 0
    p = Path(path) if path else _candidates_csv_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: set = set()
    if p.exists():
        try:
            with open(p, encoding="utf-8-sig", newline="") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) >= 3:
                        existing.add((_norm(row[0]), row[1].strip(), _norm(row[2])))
        except Exception:
            existing = set()
    today = datetime.now().strftime("%Y-%m-%d")
    new_rows = []
    for c in cands:
        key = (_norm(c.get("company", "")), c.get("relation", "").strip(),
               _norm(c.get("target", "")))
        if key in existing:
            continue
        existing.add(key)
        new_rows.append([c.get("company", ""), c.get("relation", ""),
                         c.get("target", ""), c.get("evidence", ""),
                         c.get("source", ""), today, "후보"])
    if not new_rows:
        return 0
    write_header = not p.exists() or p.stat().st_size == 0
    with open(p, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(_CSV_HEADER)
        w.writerows(new_rows)
    return len(new_rows)


def _existing_pairs_from_refbook() -> set:
    """레퍼런스북의 (회사,품목) 기존 쌍 — 이미 수록된 후보 제외용. graceful(set())."""
    pairs: set = set()
    try:
        from trade import reference_book
        for row in reference_book.build_rows():
            item = _norm(row.get("name", ""))
            for co in row.get("companies", []):
                if item and co:
                    pairs.add((_norm(co), item))
    except Exception as exc:
        log.warning("kg_candidates: refbook pairs load failed: %s", exc)
    return pairs


def _known_companies() -> set:
    """알려진 상장사 집합 — 후보 회사 필터(노이즈↓). graceful(빈 set → 필터 생략)."""
    try:
        from trade import reference_book
        out: set = set()
        for row in reference_book.build_rows():
            out.update(row.get("companies", []))
        return out
    except Exception as exc:
        log.warning("kg_candidates: known companies load failed: %s", exc)
        return set()


def extract_candidates(texts: list[dict], *, model: Optional[str] = None,
                       max_calls: Optional[int] = None,
                       use_company_filter: bool = True) -> list[dict]:
    """텍스트 배치 → 관계 후보(필터·dedup 적용). texts=[{"text":..,"source":..}].
    Gemini(llm_insights 인프라) 호출 — 키없음/킬스위치/상한/실패 시 graceful([]).
    ⛔ 자동 등재 안 함 — 호출부가 write_candidates_csv 로 승인 큐에만 적재."""
    from trade import llm_insights, llm_usage
    if not llm_insights._llm_ready():
        log.info("kg_candidates: no Gemini backend — skip")
        return []
    model = model or _DEFAULT_KG_MODEL    # flash 기본(저렴) — 봇 분석모델과 독립
    cap = max_calls if max_calls is not None else llm_insights._max_calls()
    known = _known_companies() if use_company_filter else None
    existing = _existing_pairs_from_refbook()
    out: list[dict] = []
    used = 0
    try:
        llm = llm_insights.make_chat(model, temperature=0.2)
    except Exception as exc:
        log.warning("kg_candidates: make_chat failed: %s", exc)
        return []
    for item in texts:
        if used >= cap:
            log.warning("kg_candidates: call cap %d reached — partial", cap)
            break
        text = (item or {}).get("text") or ""
        src = (item or {}).get("source") or ""
        if not text.strip():
            continue
        try:
            resp = llm.invoke([("system", _SYS_PROMPT),
                               ("human", build_human_prompt(text, src))])
            um = getattr(resp, "usage_metadata", None) or {}
            llm_usage.record(model, um.get("input_tokens", 0),
                             um.get("output_tokens", 0), kind="kg_candidate")
            used += 1
            triples = parse_triples(getattr(resp, "content", "") or "")
            out.extend(filter_candidates(
                triples, known_companies=known,
                existing_pairs=existing, source=src))
        except Exception as exc:
            log.warning("kg_candidates: extract failed (%s): %s", src, exc)
            continue
    # 전체 배치 교차 dedup
    return filter_candidates(out, known_companies=None, existing_pairs=existing)


if __name__ == "__main__":   # pragma: no cover
    import argparse
    import os
    ap = argparse.ArgumentParser(description="레퍼런스북 관계 후보 발굴(승인 큐)")
    ap.add_argument("--limit", type=int, default=5, help="처리할 텍스트 수(소배치)")
    ap.add_argument("--source", default="blog",
                    choices=["blog", "dart", "file"],
                    help="blog=블로그 아카이브 / dart=DART 매출제품 / file")
    ap.add_argument("--file", help="--source file 일 때 텍스트 파일 경로")
    ap.add_argument("--no-filter", action="store_true",
                    help="known_companies 필터 끔(원시 후보 확인용)")
    ap.add_argument("--model", default=None,
                    help=f"Gemini 모델(기본 {_DEFAULT_KG_MODEL}=저렴). pro 원하면 명시.")
    args = ap.parse_args()

    # .env 키 로드 — 수동 셸은 봇 서비스와 달리 .env 미로드(GOOGLE/GEMINI 키 부재 →
    # _llm_ready False → 0건). dotenv 모듈 없이 직접 파싱(ECOS probe 동일 패턴).
    _envp = Path.home() / "stock" / ".env"
    if _envp.exists():
        for line in _envp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k and k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")

    from trade import llm_insights
    ready = llm_insights._llm_ready()

    batch: list[dict] = []
    if args.source == "file" and args.file:
        batch = [{"text": Path(args.file).read_text(encoding="utf-8"),
                  "source": Path(args.file).name}]
    elif args.source == "dart":
        # DART 매출제품 구성 인벤토리 — 회사·제품 텍스트(블로그보다 dense·관계 풍부).
        try:
            from trade.dart_revenue import load_inventory
            inv = load_inventory()
            for code, entry in list(inv.items())[:args.limit]:
                prods = entry.get("products") or []
                nm = entry.get("name") or code
                txt = f"{nm} 제품 구성: " + ", ".join(
                    str(p.get("name") or p) for p in prods[:30])
                if prods:
                    batch.append({"text": txt, "source": f"dart:{nm}"})
        except Exception as exc:
            print(f"DART 인벤토리 로드 실패: {exc}")
    else:
        try:
            from bot import blog_watch
            p = Path(blog_watch._ARCHIVE_DIR)
            files = sorted(p.glob("*/*.json"), reverse=True)[:args.limit]
            for fp in files:
                rec = json.loads(fp.read_text(encoding="utf-8"))
                batch.append({"text": rec.get("desc") or rec.get("title") or "",
                              "source": f'blog:{rec.get("blog_title") or rec.get("blog_id")}'})
        except Exception as exc:
            print(f"블로그 아카이브 로드 실패: {exc}")

    batch = [b for b in batch[:args.limit] if (b.get("text") or "").strip()]
    # 진단(funnel) — 0건 원인 즉시 가시화(실수 12 silent-fail 금지).
    print(f"[진단] Gemini 백엔드 준비: {ready} · 텍스트 로드: {len(batch)}건 "
          f"· 소스: {args.source} · 회사필터: {'OFF' if args.no_filter else 'ON'}")
    if batch:
        _avg = sum(len(b['text']) for b in batch) // max(len(batch), 1)
        print(f"[진단] 평균 텍스트 길이 ~{_avg}자 · 예시 출처: "
              f"{', '.join(b['source'] for b in batch[:3])}")
    if not ready:
        print("⚠️ Gemini 키/백엔드 미설정 — .env 의 GOOGLE_API_KEY/GEMINI_API_KEY "
              "(또는 Vertex) 확인. 0건의 원인.")
    if not batch:
        print("⚠️ 로드된 텍스트 0건 — 블로그 아카이브가 비었을 수 있음(새 블로그는 "
              "첫 사이클에 기존글 seen 처리·아카이브 안 함). '--source dart' 로 시도.")

    cands = extract_candidates(batch, model=args.model,
                               use_company_filter=not args.no_filter)
    n = write_candidates_csv(cands)
    print(f"후보 {len(cands)}건 추출 → 승인 큐 CSV 신규 {n}건 적재 "
          f"({_candidates_csv_path()})")
    if not cands and ready and batch:
        print("ℹ️ 텍스트·키는 정상인데 0건 → 회사필터 과다 가능. '--no-filter' 로 "
              "원시 후보 확인 권장.")
    for c in cands[:20]:
        print(f"  {c['company']} -[{c['relation']}]-> {c['target']}  ({c.get('evidence','')[:50]})")

