"""대시보드 색 토큰의 WCAG 대비를 재는 순수 모듈.

왜 있나 — `DESIGN.md` §4 는 "다크/라이트 둘 다 대비 확보"라고 **적어만 두고**
아무도 재지 않았다(#55 설명이 코드와 어긋나면 버그). 2026-09-12 실측에서
라이트 모드 `--muted`(3.06:1)·`--pos`(3.54)·`--accent`(4.42)·`--tone-export`
(2.04) 가 AA(4.5) 미달이었다 — 기준시각·사유·등락 숫자처럼 #43·#123 계열이
"보이는 줄로 내라"고 여섯 번 싸워 올린 바로 그 정보가 읽기 힘든 대비로 떠
있었다. 값이 다 '있어서' 어떤 감사도 안 걸린다(#96).

판정 단위 — 토큰 이름을 **열거하지 않는다**(#24 열거형 가드는 새 항목을 못
잡는다. 실제로 손으로 적은 6개 목록은 `--fg-soft`·`--pending`·`--item`·
`--text-sub`·`--tone-*` 를 통째로 놓쳤다):
  · 팔레트 = 커스텀 속성을 3개 이상 선언하는 규칙(`:root`·`:root[data-theme=
    dark]`·`body.dark` 등 선택자 무관).
  · 표면 = `body`/`.card`/`.panel`/`.wrap` 규칙이 배경으로 쓰는 토큰(선택자에서
    파생) ∪ 이름이 표면 규약인 것(`--bg`·`--card`·`--surface`).
    ⚠️ 오늘 레포에서 **선택자 파생이 기여하는 토큰은 0개**다(이름 규약 셋이
    전부 덮는다) — 즉 그 경로는 이름 밖 표면을 위한 그물이고 실물로는 안
    발화한다. 합성 픽스처가 그 경로를 태워 가드로 남긴다(#291 발화 경로 없는
    가드는 가드가 아니다 · #286 문서가 자기 자신에 대해 거짓을 말하지 않게).
  · 텍스트 = `color:var(--X)` 로 쓰이는 토큰 − 표면 − 칩 전경(`--X-fg` 에
    짝 `--X-bg` 가 있는 것 — 그건 자기 tint 위에 놓이지 페이지 표면에 안 놓인다).

⚠️ 이 검사가 **못 보는 축**(#274 — 검사를 넣을 땐 못 보는 축을 답할 것):
  · 인라인 리터럴 색(`color:#8a8f98`)은 토큰이 아니라 안 잡힌다.
  · 비텍스트 대비(WCAG 1.4.11 — 테두리·아이콘)는 재지 않는다. `--border` 는
    `--card` 대비 1.22:1 이지만 장식 구분선은 1.4.11 대상이 아닐 수 있어
    **재지 않은 것을 단정하지 않는다**(#165).
  · 큰 글씨 예외(≥18.66px bold / 24px)를 쓰지 않고 전부 AA 4.5 로 본다 —
    이 레포의 본문·표는 11~14px 라 전부 small text 다(실측).
  · 칩 전경 × 자기 칩 배경 중 **`--X-on` 규약을 쓰지 않는 짝**(`--tier-l-fg`
    ↔`--tier-l-bg` 류)은 판정하지 않는다. `--X-on` 쌍은 `on_pairs()` 가 잰다.
"""
from __future__ import annotations

import re
from pathlib import Path

AA_MIN = 4.5

# 표면 이름 규약. body/.card 선택자에서 못 뽑은 모듈(trade/dashboard 는 카드가
# `--surface`)을 위한 보완이고, 목록 자체는 `test_every_palette_block_has_a_surface`
# 가 "모든 팔레트 블록에 표면이 하나는 있다"로 강제한다(#24 이 목록은 누가 갱신하나).
_SURFACE_NAMES = ("--bg", "--card", "--surface")
_SURFACE_SELECTORS = (r"body", r"\.card", r"\.panel", r"\.wrap")
# 선택자에 공백·따옴표 잡음이 없는 것만 — 파이썬 산문이 `{...}` 를 품어도 안 걸린다.
_SELECTOR_OK = re.compile(r"^[.#:\w\[\]=\"'>,\s-]{1,120}$")
# 마지막 선언은 `;` 없이 `}` 로 끝나는 일이 흔하다 — `…([^;]+);` 로 재면
# 그 줄이 통째로 빠져 팔레트가 선언 2개로 보이고 블록째 무검사가 된다
# (2026-09-12 합성 픽스처가 잡았다, #155 픽스처는 원천 모양대로).
_DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;\n]+)")


def contrast_ratio(a: str, b: str) -> float | None:
    """두 CSS hex 색의 WCAG 명암비(1.0~21.0). 못 읽으면 None."""
    ca, cb = _rgb(a), _rgb(b)
    if ca is None or cb is None:
        return None
    la, lb = _lum(ca), _lum(cb)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(h: str) -> tuple[float, float, float] | None:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _lum(c) -> float:
    def f(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette_blocks(src: str) -> list[tuple[str, dict[str, str]]]:
    """커스텀 속성 3개 이상을 선언하는 규칙 = 팔레트 블록."""
    out: list[tuple[str, dict[str, str]]] = []
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", src):
        decls = _DECL.findall(m.group(2))
        if len(decls) < 3:
            continue
        sel = m.group(1).strip().splitlines()[-1].strip() if m.group(1).strip() else ""
        if not sel or not _SELECTOR_OK.match(sel):
            continue
        out.append((sel, {k: v.strip() for k, v in decls}))
    return out


def page_surfaces(src: str) -> set[str]:
    """페이지 표면 토큰 — 선택자에서 파생 + 이름 규약."""
    out: set[str] = set()
    for sel in _SURFACE_SELECTORS:
        for m in re.finditer(sel + r"\s*\{([^{}]*)\}", src):
            out |= set(re.findall(
                r"background(?:-color)?\s*:\s*var\(\s*(--[\w-]+)", m.group(1)))
    out |= {n for n in _SURFACE_NAMES if re.search(re.escape(n) + r"\s*:", src)}
    return out


_FG_SUFFIXES = ("-fg", "-text", "-color")


def _is_chip_foreground(tok: str, names: set[str], bg_used: set[str]) -> bool:
    """자기 짝 배경 위에 놓이는 전경인가 — 접미 **이름**이 아니라 짝의 존재로 판정.

    `--tier-l-fg`↔`--tier-l-bg` 는 접미로 잡히지만 `--badge-text`↔`--badge` 는
    안 잡혀, 배지 글자색이 페이지 배경과 대조돼 1.00:1 오탐이 났다(2026-09-12
    실측 4건 — #24 이름 규약 열거는 다음 이름을 못 잡는다 · #50 내 가정을
    원천의 보장으로 착각하지 말 것).
    """
    # `--X-on` 은 정의상 `--X` 위에 놓이는 전경이다(`on_pairs()` 가 잰다).
    # 페이지 표면과 대조하면 다크 `--accent-on`(어두운 잉크)이 다크 배경 위로
    # 잡혀 오탐이 된다 — 짝이 실재할 때만 제외한다(#50 가정을 보장으로 쓰지 말 것).
    if tok.endswith("-on") and tok[: -len("-on")] in names:
        return True
    for suf in _FG_SUFFIXES:
        if not tok.endswith(suf):
            continue
        base = tok[: -len(suf)]
        # ⚠️ base 가 진짜 토큰 이름일 때만. `--text` 도 `-text` 로 끝나므로
        # 이 가드 없이는 base 가 `-` 가 되고 `-`+`-bg` = `--bg` 가 배경으로
        # 쓰이는 탓에 **본문 토큰이 통째로 무검사**가 된다(2026-09-12 실측:
        # 검사 쌍 364→280, #47 계수 패턴 자체가 틀릴 수 있다).
        if not base.startswith("--") or len(base) <= 2:
            continue
        if base in bg_used or base + "-bg" in bg_used or base + "-bg" in names:
            return True
    return False


def text_tokens(src: str) -> set[str]:
    """`color:var(--X)` 로 쓰이는 토큰 − 표면 − 칩 전경."""
    used = set(re.findall(r"(?<![-\w])color\s*:\s*var\(\s*(--[\w-]+)", src))
    names = set(re.findall(r"(--[\w-]+)\s*:", src))
    bg_used = set(re.findall(
        r"background(?:-color)?\s*:\s*var\(\s*(--[\w-]+)", src))
    surf = page_surfaces(src)
    return {t for t in used
            if t not in surf and not _is_chip_foreground(t, names, bg_used)}


def audit_source(src: str, label: str = "") -> list[dict]:
    """한 모듈의 (텍스트 × 표면) 쌍을 전부 재서 돌려준다 — 판정은 호출부가."""
    surf, texts = page_surfaces(src), text_tokens(src)
    rows: list[dict] = []
    for sel, tok in palette_blocks(src):
        for s in sorted(surf & tok.keys()):
            for t in sorted(texts & tok.keys()):
                if t == s:
                    continue
                r = contrast_ratio(tok[t], tok[s])
                if r is None:
                    continue
                rows.append({"module": label, "selector": sel, "text": t,
                             "text_value": tok[t], "surface": s,
                             "surface_value": tok[s], "ratio": r})
    return rows


def audit_paths(paths) -> list[dict]:
    rows: list[dict] = []
    for p in sorted(Path(x) for x in paths):
        rows.extend(audit_source(p.read_text(errors="ignore"), str(p)))
    return rows


def on_pairs(src: str) -> list[dict]:
    """`--X-on`(그 토큰 위에 놓이는 전경) × `--X` 쌍을 잰다.

    왜 별도인가 — 한 토큰이 '어두운 배경 위 글자'(밝아야 함)와 '그 위에 놓이는
    글자'(어두워야 함)를 겸할 수 없다. 2026-09-12 실측에서 활성 칩은
    `background:var(--accent)` + **리터럴 흰 글씨**였고, 라이트는 4.84~5.19 로
    통과하지만 다크는 2.53~3.65 로 전부 AA 미달이었다 — 그리고 접근성을 위해
    다크 `--accent` 를 밝히자 그 흰 글씨가 **더 나빠졌다**(3.65→3.00). 즉 토큰을
    옮기는 것만으로는 못 고치고 전경 토큰이 따로 있어야 한다(#34 한 라벨이 두
    계정을 대표하면 한쪽은 반드시 거짓말).

    이름 규약이지만 **열거가 아니다** — 선언된 `--X-on` 을 전부 훑고 짝 `--X`
    가 같은 블록에 있을 때만 잰다. 새 `--X-on` 을 만들면 다음 실행이 저절로 본다.
    """
    rows: list[dict] = []
    for sel, tok in palette_blocks(src):
        for t in sorted(tok):
            if not t.endswith("-on"):
                continue
            base = t[: -len("-on")]
            if base not in tok:
                continue
            r = contrast_ratio(tok[t], tok[base])
            if r is None:
                continue
            rows.append({"selector": sel, "text": t, "text_value": tok[t],
                         "surface": base, "surface_value": tok[base], "ratio": r})
    return rows


def rejected_selectors(src: str) -> list[str]:
    """팔레트로 보이는데 `_SELECTOR_OK` 에 걸려 **버려진** 선택자.

    버려진 블록은 `test_every_palette_block_has_a_surface` 조차 못 보므로
    조용히 무검사가 된다 — 0건인 지금이 못박을 때다(#54 대조 0건은 통과가 아니다).
    """
    out: list[str] = []
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", src):
        if len(_DECL.findall(m.group(2))) < 3:
            continue
        raw = m.group(1).strip()
        if not raw:
            continue
        sel = raw.splitlines()[-1].strip()
        if sel and not _SELECTOR_OK.match(sel):
            out.append(sel)
    return out


def failures(rows, minimum: float = AA_MIN) -> list[dict]:
    return [r for r in rows if r["ratio"] < minimum]


def describe(r: dict) -> str:
    return (f"{r['module']} [{r['selector']}] {r['text']}({r['text_value']}) on "
            f"{r['surface']}({r['surface_value']}) = {r['ratio']:.2f}:1")
