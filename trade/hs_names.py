"""HS부호 → 한글품목명 사전 (전 HS 코드, 무실적 포함) — 사용자 2026-06-22.

거래실적 있는 코드는 관세청 stat_kor(라이브 customs_heatmap_leaf)가 우선·정확하고
판가/물량 분해까지 붙지만, **무실적(한 번도 안 팔린) 코드**는 그 스냅샷에 없어
이름이 안 뜬다. 이 사전이 그 빈틈을 메운다 — 관세청 공공데이터 'HS부호 단위별
품목명'(전 HSK10 + 한글품목명)을 `trade.scripts.build_hs_names` 가 변환·적재한
`data/hs_names.tsv`(code<TAB>한글명)를 읽어 폴백 제공.

파일 없으면 빈 사전(graceful — 라이브 stat_kor 만 동작, 무실적은 코드만 표시).
HS 코드 길이(10/9/.../2)는 정확 매칭 우선 → 없으면 더 짧은 prefix 로 폴백.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(__file__), "data", "hs_names.tsv")


@lru_cache(maxsize=2)
def load_hs_names(path: str | None = None) -> dict[str, str]:
    """{정규화 HS코드(자릿수 다양): 한글품목명}. 파일 부재/오류 → {} (graceful)."""
    p = path or _PATH
    out: dict[str, str] = {}
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                code = re.sub(r"\D", "", parts[0])
                name = parts[1].strip()
                if code and name:
                    out[code] = name
    except OSError:
        return {}
    return out


def hs_name(query: str, names: dict[str, str] | None = None) -> str | None:
    """질의(점·대시 포함 가능) → 한글품목명. 정확(긴 자릿수) 우선, 없으면 더 짧은
    prefix(8·6·4·2)로 폴백 — HS10 이 사전에 없어도 그 HS6/HS4 카테고리명은 뜨게."""
    d = names if names is not None else load_hs_names()
    if not d:
        return None
    bare = re.sub(r"\D", "", query or "")
    if not bare:
        return None
    for n in (10, 9, 8, 7, 6, 5, 4, 3, 2):
        if len(bare) >= n and bare[:n] in d:
            return d[bare[:n]]
    return None
