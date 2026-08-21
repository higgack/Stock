"""글자 **폭** 계산 — 전각(한글·한자·전각기호) 1.0, 그 외 0.55.

⚠️ 왜 필요한가(사용자 2026-08-21, 두 화면에서 같은 날 각각): 글자수로
재면 라틴·숫자가 섞인 문자열이 실제보다 훨씬 넓게 계산된다.
  · 성장동력 카드 — 자리가 남는데도 둘째 줄로 넘어갔다
    ("공간이 있는데 왜 줄 바꿈을 해서 작성한거야?")
  · DART 제품 표 — 어느 열이 '긴 글' 열인지 가르는 데 같은 계산이 필요하다

두 곳이 각자 세면 판정이 갈라진다(#38) — **한 곳**에 둔다.
폭 비율 0.55 의 근거는 `quarterly_infographic._CARD_LINE_W` 주석의 VM
실측이고, 전각 판정은 표준 라이브러리에 맡긴다(코드포인트를 손으로
나열하면 목록 밖 글자가 샌다, #24).
"""
from __future__ import annotations

import unicodedata

_NARROW = 0.55


def vlen(s: str) -> float:
    """문자열의 전각 환산 폭."""
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F")
               else _NARROW for ch in s or "")


def vtrim(s: str, w: float) -> str:
    """폭 `w` 를 넘지 않게 자른다(글자수가 아니라 폭)."""
    out, acc = [], 0.0
    for ch in s or "":
        acc += vlen(ch)
        if acc > w:
            break
        out.append(ch)
    return "".join(out)
