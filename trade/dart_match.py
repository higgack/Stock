"""G2 — DART 제품명 ↔ 관세청 품목명 매칭 (회사맵핑 후보 생성).

G1 인벤토리(회사→제품)의 제품명을 관세청 품목명과 규칙 기반으로 매칭해, **미매핑
품목에 대한 회사 후보**를 제안한다(operator 승인 전 후보일 뿐 — 정확도 우선, 자동
노출 안 함). 별칭(영문/한글·표기차)은 `_ALIAS` 한 줄로 확장.

규칙(보수적 — 오매핑이 신뢰를 깎으므로):
  1) 정규화 후 완전일치 (또는 별칭 통일 후 일치)
  2) 별칭 통일 후 한쪽이 다른쪽의 부분문자열 (길이 ≥3 가드, 노이즈 차단)
LLM 0·₩0·순수(단위테스트).
"""
from __future__ import annotations

# 제품명/품목명 표기차 별칭 — 좌(정규화 키) → 우(통일 형태). 양방향 적용.
# G1 --wide 출력에서 본 실제 차이 위주. 확장 = 한 줄 추가.
_ALIAS = {
    "dram": "디램", "d램": "디램",
    "nand": "낸드", "nandflash": "낸드", "플래시메모리": "낸드",
    "oled": "유기발광", "유기발광다이오드": "유기발광",
    "lcd": "액정",
    "ssd": "보조기억장치",
    "mlcc": "적층세라믹콘덴서",
    "pcb": "인쇄회로기판",
    "tv": "텔레비전", "티브이": "텔레비전",
}

# 부분일치 함정 차단 — 짧거나 일반적이라 오매핑 위험 큰 토큰(mti_companies._DENY 정신).
_GENERIC = frozenset({"제품", "부품", "기타", "용역", "상품", "서비스", "소재",
                      "장비", "기계", "부분품", "원료", "재료"})


def _norm(s: str) -> str:
    """소문자 + 공백/괄호내용/꼬리('등') 제거 — 매칭 키."""
    import re
    n = (s or "").lower()
    n = re.sub(r"\([^)]*\)", "", n)           # 괄호 설명 제거
    n = re.sub(r"\s+", "", n)
    for tail in ("등", "외", "류"):
        if n.endswith(tail):
            n = n[:-len(tail)]
    return n.strip()


def _canon(s: str) -> str:
    """정규화 + 별칭 통일."""
    n = _norm(s)
    return _ALIAS.get(n, n)


def product_matches_item(product: str, item: str) -> bool:
    """DART 제품명이 관세청 품목명과 매칭되나 (보수적). 순수."""
    p, i = _canon(product), _canon(item)
    if not p or not i or len(p) < 2 or len(i) < 2:
        return False
    if p in _GENERIC or i in _GENERIC:
        return False
    if p == i:
        return True
    # 부분일치는 길이 ≥3 + 일반어 아님일 때만(노이즈 차단)
    if len(p) >= 3 and len(i) >= 3 and (p in i or i in p):
        return True
    return False


def matched_items(products: list, item_names: list) -> list[str]:
    """제품 리스트 → 매칭되는 관세청 품목명들(중복 제거, 순서 보존). 순수.
    products = [{'name':..}] 또는 [str]."""
    names = [(p.get("name") if isinstance(p, dict) else p) for p in (products or [])]
    out, seen = [], set()
    for item in item_names or []:
        if item in seen:
            continue
        if any(product_matches_item(nm, item) for nm in names if nm):
            seen.add(item)
            out.append(item)
    return out


def _canon_match(pc: str, ic: str) -> bool:
    """정규화된 제품키 pc ↔ 품목키 ic 매칭(정규화·일반어 필터는 호출부가 선처리).
    완전일치 또는 길이≥3 부분일치. 순수 문자열 연산(정규식 없음 — 핫루프용)."""
    if pc == ic:
        return True
    return len(pc) >= 3 and len(ic) >= 3 and (pc in ic or ic in pc)


def suggest_companies_for_items(inventory: dict, item_names: list) -> dict:
    """G1 인벤토리(code→{company, products}) + 관세청 품목명 → {품목명: [회사명…]}
    매칭 후보. 회사의 DART 제품이 품목과 매칭되면 그 품목 후보에 회사 추가. 순수.

    operator 가 이 후보를 보고 mti_companies._MAP 에 승인 추가(자동 노출 아님).

    ⚠️ 성능: 전 품목(1,294)×전 상장사(~2,700) 매칭이라 _canon(정규식)을 **품목·제품
    당 1회만** 미리 계산(품목당 재호출 제거) → 핫루프는 순수 문자열 비교. 단순 구현
    (품목당 _canon 재호출) 대비 ~수십배 빠름(운영자 2026-06-19 '되게 오래걸리네')."""
    out: dict[str, list] = {}
    # 품목 canon 1회 — 빈값/일반어/2자 미만 제외(매칭 자격 없음).
    item_canon = [(it, _canon(it)) for it in (item_names or [])]
    valid = [(it, ic) for it, ic in item_canon
             if ic and len(ic) >= 2 and ic not in _GENERIC]
    for _code, rec in (inventory or {}).items():
        company = (rec.get("company") or "").strip()
        if not company:
            continue
        # 제품 canon 1회/회사 (품목 루프 밖) — 중복·일반어·2자 미만 제거.
        pcs = set()
        for p in (rec.get("products") or []):
            nm = p.get("name") if isinstance(p, dict) else p
            pc = _canon(nm) if nm else ""
            if pc and len(pc) >= 2 and pc not in _GENERIC:
                pcs.add(pc)
        if not pcs:
            continue
        for it, ic in valid:
            if any(_canon_match(pc, ic) for pc in pcs):
                lst = out.setdefault(it, [])
                if company not in lst:
                    lst.append(company)
    return out


def additional_candidates(inventory: dict, item_current: dict) -> dict:
    """{품목명: 현재 관련사(정규화 set)} + G1 인벤토리 → {품목명: [DART 매출구성상
    추가 후보 회사…]} — **현재 목록에 없는 회사만**. 미매핑뿐 아니라 **이미 매핑된
    품목도 포함**해 각 품목에 더 많은 상장사를 발굴(운영자 2026-06-19 'DART로 더 많이
    붙여'). canon_company 로 표기변형·오타 통일 후 비교(중복 후보 방지). operator
    승인 전 후보 — 자동 큐레이션 아님(오매핑이 신뢰를 깎으므로). 순수."""
    from trade import mti_companies
    canon = mti_companies.canon_company
    sugg = suggest_companies_for_items(inventory, list(item_current.keys()))
    out: dict[str, list] = {}
    for item, cos in sugg.items():
        cur = item_current.get(item) or set()
        seen = set(cur)
        new: list[str] = []
        for c in cos:
            k = canon(c).replace(" ", "").lower()
            if k and k not in seen:
                seen.add(k)
                new.append(c)
        if new:
            out[item] = new
    return out
