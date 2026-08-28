"""나쁜양파 종목판 공용 지표 파서 — 상관·방향 일치율.

원천은 종목판 캡션에 네 지표를 싣는다:

    동시상관: 0.96        방향 일치율: 89%
    1Q 선행상관: 0.71     선행 방향 일치율: 83%

⚠️ **왜 공용인가**(2026-08-28): 같은 정규식이 `my_stock_exports`·
`cn_stock_flow` 에 복제돼 있었고 `kr_stock_exports` 는 선행 쌍만,
`jp_stock_exports` 는 아예 없었다 — 나라마다 따로 적으면 반드시 갈라진다는
그 규칙(#38·#84)의 실물이다. 오늘 일본 7월분이 통째로 드랍된 사고(#261)도
같은 뿌리라 여기서 **한 벌**로 모은다.

⚠️ 접두 없는 맨 `상관` 은 어느 계열인지 알 수 없으므로 **받지 않는다** —
추측 저장 금지(`my_stock` 이 그렇게 선행값을 동시 칸에 흘렸던 실측 사례).
접두가 `선행`(또는 `1Q 선행`)이면 선행, `동시`면 동시다.

⚠️ 값은 **수준값**이다(상관 -1~1 · 일치율 %). 화면에서 `+0.74 ▲` 로 그리면
'0.74 상승'으로 읽히므로 부호·화살표를 붙이지 않는다(#39).
"""

from __future__ import annotations

import re

# 접두로 계열을 가른다. 맨 `상관`(접두 없음)은 매칭 자체를 안 한다.
_RE_CORR = re.compile(
    r"(?:(?P<lead>(?:\d+Q\s*)?선행)|(?P<coin>동시))\s*상관"
    r"\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)")
_RE_DIR_HIT = re.compile(
    r"(?P<lead>선행\s*)?방향\s*일치율\s*:?\s*(?P<v>\d+(?:\.\d+)?)\s*%")

FIELDS = ("corr", "dir_hit", "lead_corr", "lead_dir_hit")


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
        key = "lead_corr" if mo.group("lead") else "corr"
        if out[key] is None:
            out[key] = v
    for mo in _RE_DIR_HIT.finditer(seg or ""):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        key = "lead_dir_hit" if mo.group("lead") else "dir_hit"
        if out[key] is None:
            out[key] = v
    return out


def level_html(label: str, v, unit: str = "", digits: int = 2) -> str:
    """**수준값** 카드 한 줄 — 부호·화살표 없이 그대로(#39). 값이 없으면 ""."""
    if v is None:
        return ""
    return (f'<div class="kr-metric"><span class="kr-mlabel">{label}</span>'
            f'<span class="kr-mval">{v:.{digits}f}{unit}</span></div>')
