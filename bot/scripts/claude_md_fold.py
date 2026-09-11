"""CLAUDE.md 의 오래된 실수 항목을 **기계적으로** CLAUDE_REFERENCE.md 로 접는다.

    cd ~/stock && .venv/bin/python -m bot.scripts.claude_md_fold            # 읽기 전용 점검
    cd ~/stock && .venv/bin/python -m bot.scripts.claude_md_fold --apply 40 # 실제로 접기

**왜 도구인가**(사용자 2026-09-12 "오래된 항목을 자동으로 접는다. 레퍼런스로"):
CLAUDE.md 는 매 턴 전량 주입되는데 실측 증가율이 ≈6,500자/일이라, 상한을 올리는
것은 문제를 미루는 것이다(`tests/test_docs_consistency.py` 예산 가드가 그렇게
적어 두고 사용자 결정을 기다렸다). 접기는 손으로 하면 매번 지는 일이므로 구조로
옮긴다(#119).

**무엇을 접나 — 그리고 무엇을 절대 안 접나**
서사(증상·실측·원인 설명)만 REFERENCE 로 옮기고 **명령형 절은 CLAUDE.md 에
그대로 남긴다**. 남기는 문장은 요약이 아니라 **원문 그대로**다(공백만 재정렬) —
#287 이 기록한 실패가 정확히 "압축이 한정어를 떨어뜨려 이미 대체된 규칙이
살아남았다" 이고, 그건 사람이 다시 쓸 때 생긴다. 그래서 이 도구는 **다시 쓰지
않는다**: 문장을 고르기만 한다.

- REFERENCE 로 가는 사본은 **항목 전문**이다(한 글자도 안 버린다).
- CLAUDE.md 에 남는 것 = 머리 문장 + 명령형·한정 절 전부 + `→ REFERENCE §실수 #N`.
- 회귀(`tests/test_regression.py`)가 "남긴 절 ⊇ 원문의 명령형 절" 과
  "남긴 절은 REFERENCE 사본에 실재" 를 값으로 못박는다(#286 의 '무엇이
  사라졌나를 명령형 절로 물을 것').

⚠️ 이 도구는 **파일을 고치므로** `--apply` 없이는 아무것도 쓰지 않는다(#264 진단은
운영 상태를 바꾸지 않는다). 접은 뒤에는 `make test` 가 게이트다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
CLAUDE = _ROOT / "CLAUDE.md"
REFERENCE = _ROOT / "CLAUDE_REFERENCE.md"

_HEAD = "## ⛔ 과거 실수 — 반복 금지 (먼저 읽을 것)"
_END = "## ⛔ UNIVERSAL CHANGES ONLY"
_REF_HEAD = "## 실수 상세 — CLAUDE.md 에서 접은 서사 (2026-09-06 지시서 감사)"
_POINTER = "→ REFERENCE §실수 #"
_ENTRY_RE = re.compile(r'(?m)^(\d+[a-z]?)\. \*\*')

# 접기 정책 — 숫자는 **측정**으로 정한다(#25·#260 근거 없는 문턱 금지).
KEEP_RECENT = 40      # 최근 N개는 손대지 않는다(막 쓴 항목은 서사째로 읽힌다)
MIN_SAVE = 150        # 이만큼 못 줄이면 포인터 줄만 늘어난다 — 접지 않는다

# 명령형·한정 절 표지. **넓게** 잡는다 — 놓치면 규칙이 조용히 약해지고(#287),
# 과하게 잡으면 그냥 덜 줄어들 뿐이다(안전한 방향으로 틀린다).
_KEEP_RE = re.compile(
    r'(할 것|볼 것|물을 것|쓸 것|둘 것|적을 것|셀 것|말 것|답할 것|확인할 것|넣을 것'
    r'|지울 것|잴 것|갈 것|read|금지|하라|말라|해야 한다|여야 한다|돼야|되어야'
    r'|안 된다|아니다|규칙:|대응:|처방:|일반화:|교훈|먼저 |단 |다만 |예외|때만)')


def split_mistakes(txt: str) -> tuple[str, str, str]:
    """(앞, 실수 섹션, 뒤) — 섹션 경계는 헤딩으로 찾는다(이름 열거 금지, #24)."""
    i = txt.index(_HEAD)
    j = txt.index(_END, i)
    return txt[:i], txt[i:j], txt[j:]


def parse_entries(section: str) -> list[tuple[str, int, int]]:
    """[(번호, 시작, 끝)] — `N. **머리**` 형태만. 1~21 의 한 줄 규칙은 대상이 아니다."""
    ms = list(_ENTRY_RE.finditer(section))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(section)
        out.append((m.group(1), m.start(), end))
    return out


def clauses(body: str) -> list[str]:
    """항목 본문을 **문장 단위**로 — 줄바꿈만 없앨 뿐 글자는 그대로다."""
    flat = re.sub(r'\s+', ' ', body).strip()
    parts = re.split(r'(?<=다\.)\s+|(?<=\.)\s+(?=[⚠✅])|\s+(?=[⚠✅])', flat)
    return [p.strip() for p in parts if p.strip()]


def keep_clauses(body: str) -> list[str]:
    """CLAUDE.md 에 남길 문장 — 머리 문장 + 명령형·한정 절(**원문 그대로**)."""
    cs = clauses(body)
    if not cs:
        return []
    return [cs[0]] + [c for c in cs[1:] if _KEEP_RE.search(c)]


def _disp(s: str) -> int:
    """표시폭 — 한글은 두 칸이다. `len()` 으로 싸면 줄이 파일 관례의 두 배가 된다
    (실측: 이 파일의 항목 줄은 len 중앙값 50 · 표시폭 중앙값 74)."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _wrap(num: str, kept: list[str], width: int = 76) -> str:
    """남긴 문장을 항목 형식으로 되싼다 — 들여쓰기 4칸(파일 관례).

    ⚠️ 글자는 안 건드린다. 줄바꿈만 다시 놓는다(#287 — 다시 쓰면 한정어가 샌다).
    """
    body = re.sub(r'^\d+[a-z]?\.\s+', '', " ".join(kept))
    indent = " " * 4
    lines, cur = [], f"{num}. "
    for word in body.split(" "):
        cand = (cur + word) if cur.endswith(" ") else f"{cur} {word}"
        if cur.strip() and _disp(cand) > width:
            lines.append(cur.rstrip())
            cur = indent + word
        else:
            cur = cand
    lines.append(cur.rstrip())
    lines.append(f"{indent}{_POINTER}{num}")
    return "\n".join(lines) + "\n"


def fold_entry(num: str, body: str) -> tuple[str, str, int] | None:
    """(새 CLAUDE.md 본문, REFERENCE 절, 절감량) — 안 줄면 None.

    REFERENCE 절은 **원문 전문**이다. 그래서 '접었다'는 곧 '옮겼다'이고,
    지운 것은 없다(#287 의 압축 손실을 구조적으로 불가능하게 만든다).
    """
    if _POINTER in body:
        return None
    kept = keep_clauses(body)
    if not kept:
        return None
    new = _wrap(num, kept)
    save = len(body) - len(new)
    if save < MIN_SAVE:
        return None
    ref = f"### 실수 #{num}\n\n{body.strip()}\n"
    return new, ref, save


def due(entries: list[tuple[str, int, int]], section: str, *,
        keep_recent: int = KEEP_RECENT) -> list[str]:
    """접을 차례인 번호 — **오래된 것부터**, 최근 `keep_recent` 개는 제외."""
    old = entries[:max(0, len(entries) - keep_recent)]
    out = []
    for num, s, e in old:
        if fold_entry(num, section[s:e]) is not None:
            out.append(num)
    return out


def _insert_ref(ref_txt: str, num: str, block: str) -> str:
    """REFERENCE 의 실수 절 블록에 **번호 순서**로 끼운다."""
    if f"### 실수 #{num}\n" in ref_txt:
        return ref_txt
    secs = list(re.finditer(r'(?m)^### 실수 #(\d+[a-z]?)$', ref_txt))
    key = int(re.sub(r'[a-z]$', '', num))
    for m in secs:
        if int(re.sub(r'[a-z]$', '', m.group(1))) > key:
            return ref_txt[:m.start()] + block + "\n" + ref_txt[m.start():]
    if secs:
        return ref_txt.rstrip("\n") + "\n\n" + block
    i = ref_txt.index(_REF_HEAD) + len(_REF_HEAD)
    return ref_txt[:i] + "\n\n" + block + ref_txt[i:]


def apply_folds(limit: int) -> tuple[int, int]:
    """가장 오래된 것부터 `limit` 개를 접는다 — (접은 수, 절감 자수)."""
    txt = CLAUDE.read_text(encoding="utf-8")
    ref = REFERENCE.read_text(encoding="utf-8")
    pre, sec, post = split_mistakes(txt)
    targets = due(parse_entries(sec), sec)[:limit]
    done = saved = 0
    for num in targets:                       # 매번 다시 파싱 — 오프셋이 밀린다
        ents = {n: (s, e) for n, s, e in parse_entries(sec)}
        s, e = ents[num]
        r = fold_entry(num, sec[s:e])
        if r is None:
            continue
        new, block, save = r
        sec = sec[:s] + new + sec[e:]
        ref = _insert_ref(ref, num, block)
        done += 1
        saved += save
    if done:
        CLAUDE.write_text(pre + sec + post, encoding="utf-8")
        REFERENCE.write_text(ref, encoding="utf-8")
    return done, saved


def check() -> int:
    txt = CLAUDE.read_text(encoding="utf-8")
    _, sec, _ = split_mistakes(txt)
    ents = parse_entries(sec)
    pend = due(ents, sec)
    save = 0
    for num, s, e in ents:
        if num in pend:
            r = fold_entry(num, sec[s:e])
            save += r[2] if r else 0
    folded = sum(1 for _, s, e in ents if _POINTER in sec[s:e])
    print(f"[claude_md_fold] CLAUDE.md {len(txt):,}자 · 실수 항목 {len(ents)}개"
          f"(접힘 {folded})")
    print(f"  접을 차례: {len(pend)}개 · 예상 절감 {save:,}자 "
          f"→ {len(txt) - save:,}자 (최근 {KEEP_RECENT}개는 제외)")
    if pend:
        print(f"  대상: {', '.join('#' + n for n in pend[:12])}"
              f"{' …' if len(pend) > 12 else ''}")
        print("  접기: cd ~/stock && .venv/bin/python -m "
              f"bot.scripts.claude_md_fold --apply {len(pend)}")
    else:
        print("  ✅ 접을 것 없음(오래된 항목이 전부 접혔거나 더 줄지 않는다)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--apply" in argv:
        i = argv.index("--apply")
        limit = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 10**9
        before = len(CLAUDE.read_text(encoding="utf-8"))
        done, saved = apply_folds(limit)
        after = len(CLAUDE.read_text(encoding="utf-8"))
        print(f"[claude_md_fold] {done}개 접음 · {before:,} → {after:,}자 "
              f"(−{before - after:,})")
        print("  이제 `make test` 로 게이트를 태울 것 — 회귀가 '명령형 절이 남았나'를 본다")
        return 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
