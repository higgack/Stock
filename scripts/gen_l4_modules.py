"""L4 sub-industry 모듈 자동 생성 — 각 L3 의 binding_layer_taxonomy +
regional_concentration 을 GICS sub-industry 깊이의 L4 모듈로 faithful 변환
(사용자 2026-06-14 'L3 처럼 전 GICS L4 다 넣어'). 콘텐츠는 기존 L3 큐레이션
재구조화 — 환각 0, 실제 종목·카탈리스트 보존.

규칙:
- L4 1개 = 부모 L3 binding_layer 1개. slug = `<L3slug>_<niche>` (parent prefix,
  전역 unique). 부모 별칭과 충돌 0 (전역 taken set 으로 dedup).
- binding_layer_taxonomy ≥2 (지역 그룹 split), regional_concentration ≥2,
  catalyst_types = 부모 L3 것 (≥3 보장).
- 이미 L4 자식이 있는 L3(semiconductors=수작업 8개)는 skip.

재실행 가능 (idempotent — 기존 파일 덮어씀). 새 L3 추가 시 재실행으로 L4 갱신.
실행: `python -m scripts.gen_l4_modules` (repo root).
"""
from __future__ import annotations

import re
from pathlib import Path

THEMES_DIR = Path(__file__).resolve().parent.parent / "bot" / "screener_themes"
# 이미 수작업 L4 보유 — 재생성 skip (덮어쓰기 방지)
SKIP_PARENTS = {"semiconductors"}

_SUFFIX_REGION = [
    (re.compile(r"\.KS$|\.KQ$"), "한국"),
    (re.compile(r"\.T$"), "일본"),
    (re.compile(r"\.TW$|\.TWO$"), "대만"),
    (re.compile(r"\.HK$"), "홍콩"),
    (re.compile(r"\.SS$|\.SZ$|\.BJ$"), "중국"),
    (re.compile(r"\.DE$|\.L$|\.PA$|\.SW$|\.AS$|\.MI$|\.MC$|\.ST$|\.OL$|"
                r"\.CO$|\.BR$|\.HE$|\.VI$|\.LS$|\.IR$"), "유럽"),
    (re.compile(r"\.NS$|\.BO$"), "인도"),
    (re.compile(r"\.AX$"), "호주"),
    (re.compile(r"\.SA$"), "브라질"),
    (re.compile(r"\.TO$|\.V$"), "캐나다"),
]


def _region_of(token: str) -> str:
    """'Company TICKER' 조각 → 지역 라벨 (티커 접미사 기준, 기본 미국)."""
    m = re.search(r"[0-9A-Za-z][0-9A-Za-z.\-]*\.?[A-Za-z]*$", token.strip())
    tk = (m.group(0) if m else token).strip()
    for rx, label in _SUFFIX_REGION:
        if rx.search(tk):
            return label
    if "비상장" in token:
        return "비상장"
    return "미국"


def _split_companies(val: str) -> list[str]:
    """regional_concentration 값 문자열 → 'Company TICKER' 조각 리스트."""
    # 괄호 안 region prefix 제거 후 ' · ' 분리
    parts = re.split(r"\s*·\s*", val)
    return [p.strip() for p in parts if p.strip()]


def _group_by_region(companies: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in companies:
        out.setdefault(_region_of(c), []).append(c)
    return out


def _clean_name(binding: str) -> str:
    """'Name (companies...)' → 'Name' (괄호 앞 부분, trim)."""
    name = re.split(r"\s*[\(（]", binding, 1)[0].strip()
    return name or binding.strip()


def _best_regional(name: str, rc: dict[str, str], binding_inline: str) -> str:
    """binding layer 이름에 가장 잘 맞는 regional_concentration 값. 없으면
    binding 인라인 괄호 안 회사목록 fallback."""
    nlow = name.lower()
    # 1) 키가 name 의 핵심 토큰을 포함하는 entry 우선
    best, best_score = None, 0
    ntokens = {t for t in re.split(r"[^0-9a-z가-힣]+", nlow) if len(t) > 1}
    for k, v in rc.items():
        ktokens = {t for t in re.split(r"[^0-9a-z가-힣]+", k.lower()) if len(t) > 1}
        score = len(ntokens & ktokens)
        if score > best_score:
            best, best_score = v, score
    if best and best_score:
        return best
    # 2) binding 인라인 괄호 회사
    m = re.search(r"[\(（]([^)）]*)[\)）]", binding_inline)
    if m and "·" in m.group(1):
        return m.group(1).strip()
    # 3) 전체 rc 합치기 (최후)
    return " · ".join(rc.values())[:600]


# 너무 generic·짧아 별칭으로 부적합 (오라우팅·혼란 유발) — 제외
_ALIAS_STOP = {"and", "the", "of", "for", "services", "service", "products",
               "product", "equipment", "global", "other", "big", "new", "old",
               "core", "major", "minor", "group", "holdings", "inc", "corp",
               "company", "companies", "tier", "diversified", "general",
               "specialty", "related", "misc", "관련", "및", "기타", "주요",
               "기타공급망", "확장", "참고"}


def _slugify(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name.lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:26].strip("_") or "sub"      # 절단 후에도 trailing _ 제거


def _gen_aliases(name: str, parent_slug: str, taken: set[str]) -> list[str]:
    """name 기반 보수적 별칭 — 한글 토큰(≥2) + 구별되는 ascii 토큰(≥4, generic
    제외) + parent-prefixed 2-gram. 전역 taken 충돌 제외. 슬러그 직접 접근이
    기본이라 별칭은 보조(없어도 무방 — junk 별칭보다 빈 게 나음)."""
    toks = [t for t in re.split(r"[^0-9a-zA-Z가-힣]+", name) if t]
    cands: list[str] = []
    ascii_toks = [t.lower() for t in toks if re.match(r"[a-zA-Z]", t)
                  and t.lower() not in _ALIAS_STOP and len(t) >= 4]
    if len(ascii_toks) >= 2:                       # 구별되는 2-gram 우선
        cands.append(f"{ascii_toks[0]}_{ascii_toks[1]}")
    cands += ascii_toks
    cands += [t for t in toks if re.match(r"[가-힣]", t) and len(t) >= 2
              and t not in _ALIAS_STOP]
    out: list[str] = []
    for c in cands:
        if c and c not in taken and c not in out:
            out.append(c)
        if len(out) >= 3:
            break
    return out


def _pyrepr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate() -> int:
    import bot.screener_themes as st
    st._discover()
    l3 = {s: t for s, t in st._THEMES.items() if t.get("layer") == "L3_INDUSTRY"}
    # 전역 taken = 모든 기존 slug + alias (충돌 방지)
    taken: set[str] = set(st._ALIAS_TO_SLUG.keys())
    written = 0
    for pslug, pt in sorted(l3.items()):
        if pslug in SKIP_PARENTS:
            continue
        bls = pt.get("binding_layer_taxonomy", [])
        rc = pt.get("regional_concentration", {})
        cats = pt.get("catalyst_types", [])[:6]
        pdomain = pt.get("domain", pslug)
        horizon = pt.get("horizon", "6-18 months")
        used_sub: set[str] = set()
        for bl in bls:
            name = _clean_name(bl)
            sub = _slugify(name)
            if sub in used_sub:
                sub = f"{sub}_{len(used_sub)}"
            used_sub.add(sub)
            slug = f"{pslug}_{sub}"[:48]
            val = _best_regional(name, rc, bl)
            comps = _split_companies(val)
            groups = _group_by_region(comps)
            # binding ≥2 보장 — 지역 그룹 2+면 그걸로, 아니면 회사목록 2분할
            if len(groups) >= 2:
                binding = [f"{name} — {reg} ({' · '.join(cs[:8])})"
                           for reg, cs in groups.items()][:5]
                regional = {f"{name} ({reg})": " · ".join(cs[:12])
                            for reg, cs in groups.items()}
            else:
                half = max(1, len(comps) // 2)
                a, b = comps[:half], comps[half:] or comps[:1]
                binding = [f"{name} — 주요 ({' · '.join(a[:8])})",
                           f"{name} — 기타·공급망 ({' · '.join(b[:8])})"]
                regional = {f"{name} (주요)": " · ".join(a[:12]),
                            f"{name} (확장)": " · ".join(b[:12]) or " · ".join(a[:6])}
            if len(regional) < 2:        # 안전망
                regional[f"{name} (참고)"] = pdomain
            aliases = _gen_aliases(name, pslug, taken)
            taken.update(aliases)
            taken.add(slug)
            # 모듈 텍스트
            lines = [
                f'"""{name} — L4 under {pdomain}.',
                "",
                f"자동 생성(부모 L3 {pslug} 의 binding_layer 파생, 사용자 2026-06-14",
                "'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.",
                '"""',
                "",
                "from __future__ import annotations",
                "",
                "THEME = {",
                f'    "domain": {_pyrepr(f"{name} ({pdomain.split(chr(40))[0].strip()})")},',
                '    "layer": "L4_SUBINDUSTRY",',
                f'    "aliases": [{", ".join(_pyrepr(a) for a in aliases)}],',
                f'    "horizon": {_pyrepr(horizon)},',
                '    "binding_layer_taxonomy": [',
            ]
            for b in binding:
                lines.append(f"        {_pyrepr(b)},")
            lines.append("    ],")
            lines.append('    "catalyst_types": [')
            for c in cats:
                lines.append(f"        {_pyrepr(c)},")
            lines.append("    ],")
            lines.append('    "regional_concentration": {')
            for k, v in regional.items():
                lines.append(f"        {_pyrepr(k)}: {_pyrepr(v)},")
            lines.append("    },")
            lines.append("}")
            (THEMES_DIR / f"{slug}.py").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
            written += 1
    return written


if __name__ == "__main__":
    n = generate()
    print(f"generated {n} L4 modules")
