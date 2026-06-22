"""BeOn 품목명 → 산업(MTI 21대 산업) 해석 — 산업별 탭(대시보드) 전용.

BeOn alert 의 item 은 HSK 세부 품목명(예: '기타골프용품', '니켈도금강판')이라
국가/지역처럼 alert 고유 필드가 아니다. 산업 분류는 다음 순서로 정확매칭만:

  1) `_MANUAL` — 운영자 확인 override (품목 원문 또는 정규화). fuzzy 금지 규칙상
     자동 보정은 안 하고, 운영자가 확인한 매핑만 여기 1줄로 등재(점진 확대).
  2) MTI 품목명 정확매칭 (mti_map.mti_names) — {품목명: 산업}.
  3) HSK 품목명(hs_names) → HS6 prefix → 산업 (mti_map.load_mti 의 HSK10→산업).

세 경로 모두 실패하면 None → 호출부가 '미분류' 로 분류(맨 아래 정렬).
정규화는 영숫자/한글만 남겨 공백·괄호·기호 차이를 흡수(정확매칭 보조, fuzzy 아님).

커버리지는 낮다(샘플 ~18%) — 대부분 미분류. _MANUAL 확대로만 올린다(자동 추정 금지).
"""
from __future__ import annotations

import re
from functools import lru_cache

# 운영자 확인 override (품목 원문/정규화 모두 허용). 예시 자리 — 운영자 확인분만 추가.
#   "MLCC": "전기기기",
_MANUAL: dict[str, str] = {}


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """{정규화 품목명: 산업} — MTI 품목명 + HSK 품목명(HS6 prefix→산업). 1회 빌드 캐시.
    실패해도 부분 인덱스 반환(graceful). _MANUAL 은 lookup 에서 최우선이라 여기 미포함."""
    idx: dict[str, str] = {}
    try:
        from trade import mti_map
        # (2) MTI 품목명 정확매칭
        for _m6, meta in mti_map.mti_names().items():
            nm, ind = (meta[0], meta[1]) if len(meta) >= 2 else ("", "")
            if nm and ind:
                idx.setdefault(_norm(nm), ind)
        # HS prefix(2/4/6/10)→산업 맵 (load_mti: {HSK10:(mti6,산업,품목명)})
        mti = mti_map.load_mti()
        hs_ind: dict[str, str] = {}
        for hsk, rec in mti.items():
            ind = rec[1] if len(rec) > 1 else ""
            if not ind:
                continue
            hs_ind.setdefault(hsk, ind)
            for n in (6, 4):                 # HS6·HS4 prefix (HS2 는 너무 광범위 → 제외)
                if len(hsk) >= n:
                    hs_ind.setdefault(hsk[:n], ind)
        # (3) HSK 품목명(hs_names) → HS → 산업
        try:
            from trade import hs_names
            for code, nm in (hs_names.load_hs_names() or {}).items():
                k = _norm(nm)
                if not k or k in idx:
                    continue
                ind = (hs_ind.get(code) or hs_ind.get(code[:6])
                       or hs_ind.get(code[:4]))
                if ind:
                    idx[k] = ind
        except Exception:
            pass
    except Exception:
        pass
    return idx


def industry_for(item: str) -> str:
    """품목명 → 산업명, 미해석은 '' (호출부가 '미분류'). _MANUAL 최우선 → 인덱스."""
    if not item:
        return ""
    k = _norm(item)
    if item in _MANUAL:
        return _MANUAL[item]
    if k in _MANUAL:
        return _MANUAL[k]
    return _index().get(k, "")
