"""나쁜양파 종목판 공용 지표 파서 — 상관·방향 일치율.

원천은 종목판 캡션에 네 지표를 싣는다:

    동시상관: 0.96        방향 일치율: 89%
    1Q 선행상관: 0.71     선행 방향 일치율: 83%

⚠️ **왜 공용인가**(2026-08-28): 같은 정규식이 `my_stock_exports`·
`cn_stock_flow` 에 복제돼 있었고 `kr_stock_exports` 는 선행 쌍만,
`jp_stock_exports` 는 아예 없었다 — 나라마다 따로 적으면 반드시 갈라진다는
그 규칙(#38·#84)의 실물이다. 오늘 일본 7월분이 통째로 드랍된 사고(#261)도
같은 뿌리라 여기서 **한 벌**로 모은다.

⚠️ 접두가 `선행`(또는 `1Q 선행`)이면 선행, `동시`면 동시다. **접두가 없는
맨 `상관` 도 받되 `corr_basis='미표기'` 로 표시한다**(2026-08-29 정정) —
원천이 실제로 접두 없이 보낸다(ROHM 6963 · Tokyo Electron 8035 의 7월분이
`상관: 0.98` / `방향 일치율: 100%` 였다). 버리면 그 자리가 영원히 빈다(#171).
동시라고 단정하지도 않는다 — 값은 싣고 계열은 화면이 '미표기'라고 말한다
(#43·#165 재지 않은 귀속을 단정하지 말 것).

⚠️ 값은 **수준값**이다(상관 -1~1 · 일치율 %). 화면에서 `+0.74 ▲` 로 그리면
'0.74 상승'으로 읽히므로 부호·화살표를 붙이지 않는다(#39).
"""

from __future__ import annotations

import re

# 접두로 계열을 가른다. 접두가 없으면 **미표기**로 따로 받는다(버리지도,
# 동시라고 단정하지도 않는다 — 아래 parse_corr_fields 참조).
_RE_CORR = re.compile(
    r"(?:(?P<lead>(?:\d+Q\s*)?선행)|(?P<coin>동시))?\s*상관"
    r"\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)")
_RE_DIR_HIT = re.compile(
    r"(?P<lead>선행\s*)?방향\s*일치율\s*:?\s*(?P<v>\d+(?:\.\d+)?)\s*%")

# `corr_basis` = 동시/선행 칸에 담긴 값이 **어느 계열인지** — 화면이 그대로
# 적는다(#43·#55). 값은 있는데 계열을 모르면 '미표기'.
FIELD_TYPES = {"corr": "REAL", "dir_hit": "REAL",
               "lead_corr": "REAL", "lead_dir_hit": "REAL",
               "corr_basis": "TEXT"}
FIELDS = tuple(FIELD_TYPES)


def parse_corr_fields(seg: str) -> dict:
    """캡션 구간 → {corr, dir_hit, lead_corr, lead_dir_hit} (없으면 None).

    각 칸은 **처음 나온 값**을 쓴다 — 한 구간에 같은 계열이 두 번 나오면
    뒤엣것은 다른 회사의 값일 수 있다(호출부가 헤더 구간으로 이미 잘라
    넘기는 것이 계약이다).
    """
    out = {k: None for k in FIELDS}
    for mo in _RE_CORR.finditer(seg or ""):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        if mo.group("lead"):
            if out["lead_corr"] is None:
                out["lead_corr"] = v
        elif out["corr"] is None:
            out["corr"] = v
            out["corr_basis"] = "동시" if mo.group("coin") else "미표기"
    for mo in _RE_DIR_HIT.finditer(seg or ""):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        if mo.group("lead"):
            if out["lead_dir_hit"] is None:
                out["lead_dir_hit"] = v
            continue
        # ⚠️ 접두 없는 일치율의 계열은 **같은 캡션의 상관이 정한다**(추측이
        # 아니라 원문 구조): 동시/미표기 상관이 있으면 그 짝이고, 선행 상관만
        # 있으면 선행 짝이다(없는 계열을 지어내지 않는다).
        if out["corr"] is None and out["lead_corr"] is not None:
            if out["lead_dir_hit"] is None:
                out["lead_dir_hit"] = v
        elif out["dir_hit"] is None:
            out["dir_hit"] = v
            if out["corr_basis"] is None:
                out["corr_basis"] = "미표기"
    return out


def basis_suffix(basis) -> str:
    """계열이 '미표기' 면 화면이 그 사실을 적는다 — 동시라고 단정하지 않는다."""
    return "(계열 미표기)" if basis == "미표기" else ""


def level_html(label: str, v, unit: str = "", digits: int = 2,
               basis=None) -> str:
    """**수준값** 카드 한 줄 — 부호·화살표 없이 그대로(#39). 값이 없으면 ""."""
    if v is None:
        return ""
    label = label + basis_suffix(basis)
    return (f'<div class="kr-metric"><span class="kr-mlabel">{label}</span>'
            f'<span class="kr-mval">{v:.{digits}f}{unit}</span></div>')
