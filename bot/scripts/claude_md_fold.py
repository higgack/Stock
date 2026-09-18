"""CLAUDE.md 의 오래된 실수 항목을 **기계적으로** CLAUDE_REFERENCE.md 로 접는다.

    cd ~/stock && .venv/bin/python -m bot.scripts.claude_md_fold            # 읽기 전용 점검
    cd ~/stock && .venv/bin/python -m bot.scripts.claude_md_fold --apply 40 # 실제로 접기
    cd ~/stock && .venv/bin/python -m bot.scripts.claude_md_fold --relocate 150

**두 모드의 계약이 다르다**(2026-09-18):
  `--apply N`     접기  — 서사만 REFERENCE 로, **명령형 절은 CLAUDE.md 에 남긴다**.
                          표식 `→ REFERENCE §실수 #N`.
  `--relocate C`  이관  — 번호 ≤ C 인 항목의 **전문**을 REFERENCE 로, CLAUDE.md 엔
                          **제목 절만** 남긴다. 표식 `⇒ REFERENCE §실수 #N`.
                          접기로 더 못 줄일 때(오래된 항목은 거의 전부 명령형이라
                          절감이 0 이다) 쓰는 다음 지렛대이고, 주입 파일에서 규칙을
                          빼는 일이라 게이트가 둘이다 — **뒤 항목·§주제 색인이 그
                          번호를 2회 이상 인용**(교훈이 이어졌다는 증거)하고, 굵은
                          제목 절이 있을 것(없으면 남는 게 규칙이 아니라 조각이다).

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
# ⚠️ 이관은 **접기와 계약이 다르다** — 접기는 명령형 절을 CLAUDE.md 에 남기지만
# 이관은 제목 절만 남긴다. 표식을 같이 쓰면 접기 계약 회귀(`test_folding_only_
# drops_incident_narrative`)가 이관을 '규칙이 사라졌다' 로 오보한다(#34 한 라벨이
# 두 뜻을 대표하면 한쪽은 반드시 거짓말). 읽는 사람도 한눈에 갈린다.
_RELOCATED = "⇒ REFERENCE §실수 #"
# ⚠️ `**` 를 요구하면 안 된다 — 굵게가 뒤에 오는 항목(#23 `23. 진단 스크립트는
# **…**`)이 통째로 앞 항목에 합쳐진다(독립 리뷰 Blocking: 실제로 #23 이
# CLAUDE.md 에서 사라지고 그 안의 `env_keys` 규칙이 유실됐다). 모양 관례에
# 기대지 말고 번호만 본다 — 회귀가 "모든 `N. ` 줄이 자기 항목으로 파싱되는가"
# 를 전수로 못박는다(#24).
_ENTRY_RE = re.compile(r'(?m)^(\d+[a-z]?)\. ')

# 접기 정책 — 숫자는 **측정**으로 정한다(#25·#260 근거 없는 문턱 금지).
KEEP_RECENT = 30      # 최근 N개는 손대지 않는다(막 쓴 항목은 서사째로 읽힌다)
# 40→30 (사용자 2026-09-18): 주입 예산 330,000자에 여유 60자만 남아 새 실수를
# 못 적는 상태가 됐다. 좁히자 #354·#355 두 항목이 접혔고 그 자리에 #386 이 들어갔다
# (현재 329,899자 · 여유 101 — 상한을 올리는 것은 가드가 금지).
# ⚠️ **이 지렛대는 거의 다 썼다**: 30 에서 접을 항목이 0개이고, 더 나오게 하려면
# 25(+402자)·20(+748)·10(+1,498)까지 좁혀야 한다(2026-09-18 실측). 다음에 예산이
# 막으면 창을 또 좁히기 전에 **항목당 크기 정책**을 사용자에게 물을 것.
# ⚠️ 그리고 '접기는 손실 0' 은 REFERENCE 사본에 대해서만 참이다 — CLAUDE.md 쪽엔
# **비문이 남는다**: `clauses()` 가 `✅`·`⚠️` 를 문장 경계로 보아 #354 는
# `… — 화면 체크포인트 (266개 ·` 에서, #355 는 `다크 2.53~3.65 → **5.26~7.41**.`
# 에서 끊긴 조각을 '남길 절' 로 골랐다(2026-09-18 실측 2건).
# ⚠️ **손으로 다시 쓰지 말 것** — `test_folding_only_drops_incident_narrative` 가
# "남긴 절은 REFERENCE 사본에 **원문 그대로** 실재" 를 재므로 손질은 빨간불이고,
# 그 가드가 옳다(#287 다시 쓰면 한정어가 빠진다). 고칠 자리는 **분할기**다 —
# 이모지를 경계로 쓰되 그 뒤가 문장 시작이 아니면 잇게 하고, 이미 접힌 항목은
# REFERENCE 전문에서 되살려 다시 접어야 한다(unfold 경로가 아직 없다).
MIN_SAVE = 80         # 이만큼 못 줄이면 포인터 줄만 늘어난다 — 접지 않는다
# ⚠️ 150 → 80(2026-09-12): 판정 극성을 뒤집자(아래 `is_droppable`) 안전한 절감이
# **−3,028자(24개 항목)** 로 줄었다 — 첫 판의 −42,816 은 절감이 아니라 규칙 유실
# 이었다(독립 리뷰 실측). 80 은 포인터 줄(≈25자)의 세 배라 순절감이 확실한 선이다.

# 명령형·한정 절 표지 — **열거가 아니라 구조**로 본다(#24).
# ⚠️ 첫 판은 동사 14개를 이름으로 적었다가 `쓸 것`·`봐야 한다`·`처방 둘:` 같은
# 형태를 못 잡아 **57개 항목에서 규칙 절 70개를 흘렸다**(독립 리뷰 실측).
# 한국어 명령형은 생산적이라 목록으로는 못 따라간다 — 넓게 잡아 틀리면 덜
# 줄어들 뿐이고, 좁게 잡아 틀리면 규칙이 조용히 약해진다(#287).
_OBLIGATION_RE = re.compile(
    r'[가-힣]야 한다|[가-힣]야 하고|[가-힣]야만|금지|하라|말라|해선 안|안 된다|아니다')
# ⚠️ 콜론을 요구하면 안 된다 — `규칙의 경계는 …다`(#32)처럼 콜론 없이 규칙을
# 말하는 문장이 흔하다(회귀가 켜지자마자 그걸 잡았다, #87a).
_LABEL_RE = re.compile(r'규칙|대응|처방|일반화|교훈|검산법')
# 한정절 — 이게 빠지면 남은 문장이 **원문보다 강해진다**(#287 이 정확히 그 사고).
_QUALIFIER_RE = re.compile(r'(⚠️|^단 |^다만 | 단 | 다만 |예외|때만|먼저 |그러나|하지만)')


def _has_imperative(text: str) -> bool:
    """`~할 것`·`~볼 것` 형태 — ` 것` 앞 음절의 받침이 ㄹ 인가로 본다.

    동사를 열거하지 않는다: 한국어의 `-ㄹ 것` 은 어떤 동사에도 붙는다.
    """
    for m in re.finditer(r'([가-힣]) 것', text):
        ch = ord(m.group(1)) - 0xAC00
        if 0 <= ch < 11172 and ch % 28 == 8:        # 종성 ㄹ
            return True
    return False


def is_rule_clause(text: str) -> bool:
    """이 문장이 **규칙을 말하나** — 명령형·의무·라벨·한정."""
    return bool(_has_imperative(text) or _OBLIGATION_RE.search(text)
                or _LABEL_RE.search(text) or _QUALIFIER_RE.search(text))


def is_incident_narrative(text: str) -> bool:
    """이 문장이 **그때 무슨 일이 있었나**(과거 사건 보고)인가 — 종성 ㅆ.

    `했다.`·`였다.`·`됐다.` 로 끝나면 사건 기술이다. `~한다.`·`~이다.`·`~할 것.`
    은 규칙이라 여기 안 걸린다.
    """
    m = re.search(r'([가-힣])다[.)\]"\'」』]*\s*$', text.strip())
    if not m:
        return False
    code = ord(m.group(1)) - 0xAC00
    return 0 <= code < 11172 and (code % 28) == 20        # 종성 ㅆ


def is_droppable(text: str) -> bool:
    """CLAUDE.md 에서 뺄 수 있는 문장 — **과거 사건 보고이면서 규칙이 아닌 것**.

    ⚠️ 판정의 **극성**이 중요하다. 첫 판은 "규칙처럼 보이는 것만 남긴다" 였는데,
    이 파일의 규칙은 자주 **평서문**으로 쓰여 있어(#31 "…보드 단위 그룹 기본값 +
    화면 id 와 규약 키가 다르면 `cadence_id`. 회귀는 …로 고정." ) 그 정책은
    **진짜 규칙을 흘렸다**(독립 리뷰 B2: 57개 항목 70개 절, 실측). 그래서
    "규칙을 고른다" 가 아니라 **"서사만 버린다"** 로 뒤집었다 — 애매하면 남는다.
    절감은 −42,816 → −3,028자로 줄지만 그 차액은 **절감이 아니라 유실**이었다.
    """
    return is_incident_narrative(text) and not is_rule_clause(text)


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
        if i + 1 == len(ms):
            # 섹션 끝의 정책 문단(들여쓰기 3칸)은 **항목이 아니다** — 삼키면
            # 마지막 항목의 본문이 되고 REFERENCE 에 복제된다(독립 리뷰 M1).
            tail = re.search(r'(?m)^ {1,3}\S', section[m.start():end])
            if tail:
                end = m.start() + tail.start()
        out.append((m.group(1), m.start(), end))
    return out


def clauses(body: str) -> list[str]:
    """항목 본문을 **문장 단위**로 — 줄바꿈만 없앨 뿐 글자는 그대로다."""
    flat = re.sub(r'\s+', ' ', body).strip()
    parts = re.split(r'(?<=다\.)\s+|(?<=\.)\s+(?=[⚠✅])|\s+(?=[⚠✅])', flat)
    return [p.strip() for p in parts if p.strip()]


def keep_clauses(body: str) -> list[str]:
    """CLAUDE.md 에 남길 문장 — 머리 문장 + **버릴 수 없는 것 전부**(원문 그대로).

    고르는 게 아니라 **버리는** 쪽으로 판정한다(`is_droppable` 참조).
    """
    cs = clauses(body)
    if not cs:
        return []
    return [cs[0]] + [c for c in cs[1:] if not is_droppable(c)]


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
    body = re.sub(r'\s+(?=[⚠✅])', '\n', body)      # 경고·확인 절은 줄을 바꾼다
    lines, cur = [], f"{num}. "
    for word in body.replace("\n", " \n").split(" "):
        if word == "\n":
            lines.append(cur.rstrip())
            cur = indent
            continue
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
    # 절이 하나도 없으면 **그 헤딩 바로 뒤**에 넣는다(파일 끝이 아니라 —
    # 이 섹션이 늘 마지막이라는 보장이 없다, 독립 리뷰 L4).
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



# ── 이관(relocate) — 접기로는 더 못 줄이는 오래된 번호대 ─────────────────────
# 사용자 2026-09-18 "오래된 번호대를 Reference 로 이동". 접기는 **서사만** 옮기고
# 명령형 절을 남기므로, 오래된 항목처럼 거의 전부가 명령형인 글에서는 절감이 0 이다
# (실측: `--apply` 대상 0개인 채 예산 여유 70자). 이관은 한 단계 더 나아가 항목
# **전문**을 REFERENCE 로 옮기고 CLAUDE.md 에는 머리 줄만 남긴다.
#
# ⚠️ 그건 주입되는 파일에서 규칙을 빼는 일이라, 아무 항목에나 하면 #287 의 재발이다
# (압축이 한정어를 떨어뜨려 이미 대체된 규칙이 살아남았다). 그래서 **그 항목의
# 교훈이 다른 곳에 실재하는가**를 재고 통과한 것만 옮긴다:
#   · 뒤 항목·§주제 색인이 그 번호를 **2회 이상 인용**한다(계열이 이어졌다는 증거)
#   · 머리 줄은 **원문 그대로** 남긴다(다시 쓰지 않는다 — 이 도구의 원칙)
# 인용이 없는 항목은 그 교훈을 아무도 안 이어받았다는 뜻이므로 **안 옮긴다**.
_CITE_MIN = 2


def cite_count(section: str, num: str, body: str = "") -> int:
    """이 번호가 **자기 몸통 밖에서** 인용된 횟수.

    ⚠️ `#1` 이 `#12`·`#150` 에 걸리면 안 된다(#46 위치·형태로 추정 금지 ·
    #388 토큰 경계) — 뒤에 숫자가 오지 않는 자리만 센다.
    ⚠️ 옛 판은 자기 인용을 **일괄 `-1`** 로 뺐는데, 그건 이미 접힌 항목
    (`→ REFERENCE §실수 #N` 이 자기 번호를 품는다)에만 맞고 안 접힌 항목에는
    틀린다 — 같은 게이트가 항목마다 다른 문턱이 되어 **재는 대상이 틀린다**
    (#91b). 몸통을 받아 그 안의 것만 정확히 뺀다."""
    pat = rf"#{re.escape(num)}(?![0-9a-z])"
    return max(0, len(re.findall(pat, section)) - len(re.findall(pat, body)))


def entry_title(body: str) -> str:
    """항목의 **제목 절** — `N. **규칙 한 줄**(날짜 사건):` 까지, 원문 그대로.

    ⚠️ 첫 **줄**로 자르면 안 된다 — 이 파일은 80칸에서 접히므로 줄 경계가
    문장 경계와 무관하고, 그대로 두면 `… #23 을 고치며` 같은 **비문**이 남는다
    (#354·#355 가 실측으로 기록한 그 손실). 이 파일의 모든 항목이 `**굵게**`
    로 규칙을 적고 그 뒤 괄호에 사건을 적으므로, 그 괄호를 닫는 `:` 까지를
    제목으로 본다.

    ⚠️ 그 모양이 **아니면 빈 문자열**을 돌려주고 호출부가 이관을 포기한다 —
    옛 서식(#1~#17)은 제목 절이 따로 없고 한 줄이 통째로 명령문이라, 잘라 내면
    남는 것이 규칙이 아니라 조각이다(#11 `… 제시 + …` 로 끊겼다). 줄일 수
    없으면 **안 줄이는 것**이 맞다(#32 억지로 만들지 말 것).
    """
    flat = " ".join(body.split())
    if not re.match(r'^\d+[a-z]?\.\s+\*\*', flat):
        return ""   # 굵은 제목이 없는 옛 서식(#1~#17 류)은 **옮기지 않는다**
    # ⚠️ 첫 `:` 에서 무조건 자르면 **괄호·백틱 안의 콜론**에 걸린다 — #21b
    # `(#18 의 반복 — 2026-08-19 하루에 세 번: peer_comps…)` 가 그래서 닫는
    # 괄호 없이 잘렸다(독립 리뷰 2026-09-18 실측 4건). 후보를 콜론마다 늘려
    # 가며 **괄호와 백틱이 닫힌** 첫 자리를 고르고, 끝내 안 닫히면 포기한다
    # (줄일 수 없으면 안 줄이는 것이 맞다, #32·#29 빈칸이 틀린 라벨보다 낫다).
    for m in re.finditer(r':', flat[:400]):
        cand = flat[:m.end()]
        if len(cand) < 8:
            continue
        if cand.count("(") == cand.count(")") and cand.count("`") % 2 == 0:
            return cand
    return ""


def relocate_entry(num: str, body: str, section: str) -> tuple[str, str, int] | None:
    """(새 본문, REFERENCE 절, 절감량) — 옮길 수 없으면 None."""
    if _RELOCATED in body:
        return None
    if cite_count(section, num, body) < _CITE_MIN:
        return None
    head = entry_title(body)
    if not head:
        return None
    new = f"{head} {_RELOCATED}{num}\n"
    save = len(body) - len(new)
    if save <= 0:
        return None
    ref = f"### 실수 #{num}\n\n{body.strip()}\n"
    return new, ref, save


def relocatable(section: str, cut: int) -> list[str]:
    """번호 ≤ `cut` 이면서 이관 조건을 통과하는 번호들(오래된 것부터)."""
    out = []
    for num, s, e in parse_entries(section):
        try:
            n = int(re.sub(r'[a-z]$', '', num))
        except ValueError:
            continue
        if n > cut:
            continue
        if relocate_entry(num, section[s:e], section) is not None:
            out.append(num)
    return out


def apply_relocate(cut: int) -> tuple[int, int]:
    """번호 ≤ `cut` 인 항목을 전문 이관 — (옮긴 수, 절감 자수)."""
    txt = CLAUDE.read_text(encoding="utf-8")
    ref = REFERENCE.read_text(encoding="utf-8")
    pre, sec, post = split_mistakes(txt)
    targets = relocatable(sec, cut)
    done = saved = 0
    for num in targets:                       # 매번 다시 파싱 — 오프셋이 밀린다
        ents = {n: (s, e) for n, s, e in parse_entries(sec)}
        if num not in ents:
            continue
        s, e = ents[num]
        r = relocate_entry(num, sec[s:e], sec)
        if r is None:
            continue
        new, blk, save = r
        sec = sec[:s] + new + sec[e:]
        ref = _insert_ref(ref, num, blk)
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
    if "--relocate" in argv:
        i = argv.index("--relocate")
        cut = int(argv[i + 1]) if i + 1 < len(argv) else 150
        done, saved = apply_relocate(cut)
        print(f"[claude_md_fold] 이관 {done}개 · 절감 {saved:,}자 "
              f"→ {len(CLAUDE.read_text(encoding='utf-8')):,}자 "
              f"(번호 ≤ {cut} · 인용 {_CITE_MIN}회 이상만)")
        return 0
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
