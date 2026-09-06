"""문서 정합성 — CLAUDE.md(활성 규칙) ↔ 코드/REFERENCE 참조 깨짐 방지 (lat.md `lat check`
영감, 사용자 2026-06-26). CLAUDE.md 가 가리키는 파일·섹션이 실제 존재하는지 자동 검증
→ 파일 rename·섹션 이동 시 문서 drift 를 회귀로 차단(기존 '룰 이동 시 grep orphan' 규칙 자동화).

⚠️ CLAUDE_REFERENCE.md 는 '이력 전량 아카이브'라 과거(삭제·rename된) 파일 참조가 정상적으로
남으므로 파일-존재 검사 대상 아님 — CLAUDE.md(현재 활성 규칙)만 참조 유효성 강제."""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE = _ROOT / "CLAUDE.md"
_REFERENCE = _ROOT / "CLAUDE_REFERENCE.md"

# 활성 문서가 가리키는 소스 경로 토큰(런타임 데이터·플레이스홀더 제외).
_PATH_RE = re.compile(
    r'(?:bot|trade|deploy|tests|TradingAgents)/[\w./-]+\.(?:py|sh|md|tsv|csv|html)')


def test_claude_md_file_refs_exist():
    """CLAUDE.md(활성 규칙) 가 backtick 등으로 가리키는 소스 파일은 실제 존재해야.
    파일 rename/삭제 시 활성 규칙의 포인터가 끊기는 것을 회귀로 차단."""
    txt = _CLAUDE.read_text(encoding="utf-8")
    refs = sorted({m.group(0) for m in _PATH_RE.finditer(txt)
                   if "..." not in m.group(0)})       # 생략기호(...) 플레이스홀더 제외
    missing = [r for r in refs if not (_ROOT / r).exists()]
    assert not missing, f"CLAUDE.md 가 가리키는 파일 없음(문서 drift): {missing}"


def test_claude_md_section_markers_resolve():
    """CLAUDE.md 내부 §섹션 마커(§Help·§Pre-commit)가 실제 헤딩으로 해석돼야.
    섹션 rename/이동 시 dangling §참조 차단(사용자 'orphan 참조 확인' 자동화)."""
    txt = _CLAUDE.read_text(encoding="utf-8")
    headings = [ln for ln in txt.splitlines() if ln.lstrip().startswith("#")]
    head_blob = "\n".join(headings)
    for marker in ("Help", "Pre-commit"):
        assert f"§{marker}" in txt, f"§{marker} 마커가 CLAUDE.md 에 없음(테스트 가정 갱신 필요)"
        assert marker in head_blob, f"§{marker} 가 가리킬 헤딩이 없음(섹션 이동/삭제 — 문서 drift)"


def test_reference_archive_present():
    """CLAUDE.md 가 상세를 위임하는 CLAUDE_REFERENCE.md 가 존재·비어있지 않아야
    (지식 베이스 유실 방지). CLAUDE.md 본문도 REFERENCE 를 명시 참조."""
    assert _REFERENCE.exists(), "CLAUDE_REFERENCE.md 누락 — 상세 지식 베이스 유실"
    assert _REFERENCE.stat().st_size > 5000, "CLAUDE_REFERENCE.md 가 비정상적으로 작음"
    assert "CLAUDE_REFERENCE.md" in _CLAUDE.read_text(encoding="utf-8")


# ── 실수 목록 주제 색인 (2026-09-06 지시서 감사) ─────────────────────────────
_MISTAKE_HEAD = "## ⛔ 과거 실수 — 반복 금지 (먼저 읽을 것)"
_MISTAKE_END = "## ⛔ UNIVERSAL CHANGES ONLY"
_THEME_RE = re.compile(r'(?m)^- ([^:\n]+): ((?:#\d+[a-z]?\s*)+)$')
_ENT_RE = re.compile(r'(?m)^(\d+[a-z]?)\. ')
_REF_RE = re.compile(r'#(\d+[a-z]?)')


def _mistake_entries():
    """실수 섹션을 {번호: 본문} 으로."""
    txt = _CLAUDE.read_text(encoding="utf-8")
    sec = txt[txt.index(_MISTAKE_HEAD):txt.index(_MISTAKE_END)]
    parts = _ENT_RE.split(sec)
    return sec, {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _themes(sec):
    return {m.group(1).strip(): m.group(2).split() for m in _THEME_RE.finditer(sec)}


def _parent(n):
    """`#91b`·`#59b` 처럼 문자 접미로 인용하는 하위 항목을 부모 번호로 맞춘다
    (실수 섹션에 13종 실재 — `21b` 만 실제 항목이라 나머지는 오탐이 났다)."""
    return n if n in ("21b",) else re.sub(r'[a-z]$', '', n)


# 항목 번호만으로 키잉한다(라벨은 산문이라 다듬어지면 깨진다, #19·#200).
_NOT_MEMBER = {
    # #286 은 지시서 감사 자체의 기록이다. #119(규율을 구조로)·#216·#233 을
    # 인용하지만 그건 '손 bump 실패'와 '서수 드리프트'를 예로 든 것이지
    # 캐시가 fix 를 가린 사례가 아니다.
    "286",
}


def unlisted_family_members(themes, entries, allow):
    """**순수 함수** — 한 계열의 항목을 3개 이상 인용하는데 색인에 없는 항목.
    실물 파일 검사와 '작동 확인' 테스트가 **둘 다 이걸** 부른다."""
    out = []
    for name, listed in themes.items():
        fam = {_parent(_REF_RE.fullmatch(r).group(1)) for r in listed}
        for n, body in entries.items():
            if n in fam or n in allow:
                continue
            cites = {_parent(x) for x in _REF_RE.findall(body)} - {n}
            if len(cites & fam) >= 3:
                out.append((name, n, sorted(cites & fam)))
    return out


def test_topic_index_refs_resolve():
    """주제 색인이 가리키는 번호는 실제 항목이어야 한다. 그리고 대조 대상이
    0건이면 통과가 아니라 실패(#54) — 색인을 통째로 지우는 변형이 조용히
    초록이 되면 아래 계열 검사도 같이 눈이 먼다."""
    sec, ent = _mistake_entries()
    themes = _themes(sec)
    assert len(themes) >= 7, f"주제 색인이 {len(themes)}줄뿐 — 색인이 사라졌거나 형식이 깨졌다"
    refs = [r for v in themes.values() for r in v]
    assert len(refs) >= 40, f"색인 참조가 {len(refs)}건뿐 — 눈먼 색인"
    dangling = sorted({r for r in refs if _REF_RE.fullmatch(r).group(1) not in ent})
    assert not dangling, f"색인이 없는 항목을 가리킨다(번호 재배열/삭제): {dangling}"


def test_new_entry_citing_a_family_is_listed_in_the_index():
    """#24 "이 목록은 누가 갱신하나" 의 답 — 사람이 아니라 이 테스트다.

    실수 항목은 자기 계열을 인용하며 쓰인다("#123·#129·#131·#189 에 이은").
    그래서 **한 계열의 구성원을 3개 이상 인용하는 항목은 그 계열의 구성원**
    이라는 규칙이 데이터에서 파생된다. 손으로 센 서수가 어긋난 것(#195·#228 이
    둘 다 '다섯', #216·#233 이 둘 다 '일곱')이 이 색인을 만든 이유이므로,
    색인이 다시 낡는 것을 규율이 아니라 회귀가 막는다(#119).

    실측: 이 규칙을 처음 돌리자 손으로 쓴 색인에서 #119·#266·#195·#282 네 건이
    빠져 있었다(#87a — 새 가드는 켜자마자 뭔가 잡는 게 정상).

    ⚠️ 문턱을 4로 올리거나 '`·` 로 이어 적은 나열' 로 좁히면 오탐은 사라지지만
    **원래 잡던 것도 같이 죽는다**(실측: thr4 는 #282 를, 나열만은 #228 을 놓친다,
    #146·#275). 그래서 감도는 3으로 두고 **예외만 명시**한다(#24 의 처방 —
    전수로 훑고 allowlist). 예외를 늘릴 땐 왜 그 계열이 아닌지 한 줄로 적을 것.

    ⚠️ **이 가드가 못 잡는 축**(실측 확인, #274 "이 검사가 못 보는 축은 무엇인가"):
    색인에서 번호를 **빼는** 방향은 안 걸린다 — 이 규칙은 '등재 안 된 인용자'를
    찾을 뿐 멤버십의 독립 근거가 없기 때문이다(색인이 곧 단일 출처, #38). 잡는 것은
    **추가 방향**(새 항목이 계열을 인용하는데 색인에 없음)이고, 그게 실제 성장 경로다."""

    sec, ent = _mistake_entries()
    themes = _themes(sec)
    missing = unlisted_family_members(themes, ent, _NOT_MEMBER)
    assert not missing, (
        "이 계열을 3개 이상 인용하는데 색인에 없다 — 색인 줄에 번호를 추가할 것: "
        f"{missing}")


def test_index_allowlist_keys_resolve_and_stay_small():
    """allowlist 는 **항목 번호로만** 키잉한다 — 예전 판은 색인의 한글 라벨을
    키로 써서, 라벨을 한 단어 다듬자 오탐이 살아나며 "#286 을 캐시 계열에
    등재하라" 는 **틀린 수정**을 지시했다(독립 리뷰 실측, #19·#200 라벨 동등
    비교의 재발). 그리고 크기를 못박아 **우회 통로**가 되지 않게 한다 — 항목을
    넣는 것만으로 가드가 무음이 되면 안 된다(#24·#119)."""
    _, ent = _mistake_entries()
    assert len(_NOT_MEMBER) <= 2, (
        f"예외를 늘렸으면 왜 그 계열이 아닌지 적고 이 단언도 고칠 것: {_NOT_MEMBER}")
    unknown = sorted(n for n in _NOT_MEMBER if n not in ent)
    assert not unknown, f"allowlist 가 없는 항목을 가리킨다: {unknown}"


def test_family_rule_actually_fires():
    """가드가 '작동함'을 보이려면 실제로 깨지는 입력까지 밀어 볼 것(#91c).

    ⚠️ 예전 판은 판정을 **인라인으로 재구현**해서, 문턱을 99로 올리거나 루프를
    통째로 skip 시키는 뮤테이션이 전부 통과했다(독립 리뷰 실측). 감시 대상을
    안 부르는 '작동 확인'은 아무것도 확인하지 않는다 — **제품 함수를 태운다**."""
    themes = {"가짜 계열": ["#1", "#2", "#3"]}
    ent = {"9": "…#1·#2·#3 에 이은 같은 실패다.", "8": "…#1·#2 만 인용한다."}
    assert unlisted_family_members(themes, ent, set()) == [("가짜 계열", "9", ["1", "2", "3"])]
    assert unlisted_family_members(themes, ent, {"9"}) == [], "allowlist 가 안 먹는다"
    # 2건 인용은 계열이 아니다(오탐 방지) — #286 이 정확히 그 경계에서 걸렸다.
    assert all(m[1] != "8" for m in unlisted_family_members(themes, ent, set()))



def test_folded_entries_point_at_a_real_reference_section():
    """접은 항목의 `→ REFERENCE §실수 N` 이 실제 절로 해석돼야 한다.

    2026-09-06 지시서 감사: 1,200자 넘는 22개(실수 섹션의 20%)가 받는 상호참조는
    다 합쳐 35회뿐이었는데 매 턴 전량 주입되고 있었다. 규칙 문장은 CLAUDE.md 에
    남기고 서사만 CLAUDE_REFERENCE.md 로 옮겼다 — **지운 것은 없다**. 포인터가
    끊기면 그 서사는 사실상 사라진 것이므로 여기서 막는다.

    #24 대응: 접은 항목을 이름으로 열거하지 않고 **포인터가 있는 항목 전체**를
    훑는다. 그리고 대조 대상이 0건이면 통과가 아니라 실패(#54) — 포인터를 전부
    지우는 변형이 조용히 초록이 되면 이 가드는 눈이 먼다."""
    txt = _CLAUDE.read_text(encoding="utf-8")
    ref = _REFERENCE.read_text(encoding="utf-8")
    pointed = set(re.findall(r'→ REFERENCE §실수 #(\d+[a-z]?)', txt))
    secs = set(re.findall(r'(?m)^### 실수 #(\d+[a-z]?)$', ref))
    assert len(pointed) >= 20, f"REFERENCE 포인터가 {len(pointed)}건뿐 — 눈먼 가드"
    # **양방향**으로 본다. 예전 판은 dangling 만 봐서, CLAUDE.md 에서 포인터 줄을
    # 지우면 그 절이 아무도 못 찾는 **고아**가 되는데 조용히 통과했다(독립 리뷰).
    assert pointed == secs, (
        f"포인터↔절 불일치 — 고아 절(아무도 안 가리킴)={sorted(secs - pointed)}, "
        f"dangling(절이 없음)={sorted(pointed - secs)}")


def test_injected_rules_file_stays_within_budget():
    """CLAUDE.md 는 매 턴 전량 주입된다 — 파일 스스로 "compact 유지" 를 정해 뒀다.

    2026-09-06 감사 실측: 항목 평균이 319자(첫 60개)에서 1,090자(마지막 60개)로
    3.4배 자랐고, 6~8주마다 파일이 배가되는 궤적이었다. 상한을 두면 다음에 넘길 때
    "접을 것을 접었나"를 묻게 된다(넘으면 §주제 색인 기준으로 서사를 REFERENCE 로).
    ⚠️ **상한을 현재값에 바싹 붙이면 안 된다.** 처음엔 195,000 으로 잡았는데 git
    이력 실측 증가율이 **≈4,300자/일**(2026-08-26 154,624 → 09-05 199,207)이라
    여유가 **3일**뿐이었다 — 며칠 뒤 실수 항목 하나만 추가해도 `make test` 가
    빨간불이 되고, `fail 시 commit 금지(누구든)`(§Pre-commit) 때문에 **문서 크기
    단언 하나가 무관한 기능 커밋을 통째로 막는다**(#67·#275 의 무관한 빨간불).
    240,000 은 그 증가율로 **약 2주** 창이다. 늘릴 땐 그때의 증가율을 다시 재서
    같이 적을 것 — 근거 없이 올리면 이 가드는 영원히 안 걸린다(#25·#260)."""
    n = len(_CLAUDE.read_text(encoding="utf-8"))
    assert n <= 240_000, (
        f"CLAUDE.md 가 {n:,}자 — 매 턴 주입되는 예산을 넘었다. "
        "긴 항목의 서사를 CLAUDE_REFERENCE.md 로 접을 것(규칙 문장은 남긴다).")


# ── 요약 문서 ↔ CLAUDE.md 동기화 (2026-09-06 지시서 감사 (e) 회수분) ────────────
_COPILOT = _ROOT / ".github" / "copilot-instructions.md"
_TRADE_INST = _ROOT / ".github" / "instructions" / "trade.instructions.md"
# CLAUDE.md 가 **스스로** '⛔' 또는 '— 의무' 로 표시한 섹션만 의무로 본다.
_MANDATORY_RE = re.compile(r'(?m)^## (⛔ .+|.+ — 의무.*)$')


def _mandatory_section_keys():
    """의무 섹션의 **안정 키** — 헤딩에서 파생한다(손 목록 금지, #24).

    괄호 부기와 ` — ` 뒤 설명은 자주 다듬어지므로 잘라낸다. 그러지 않으면
    "(가장 중요)" 를 한 번 고치는 것만으로 회귀가 깨진다(#19·#200)."""
    txt = _CLAUDE.read_text(encoding="utf-8")
    out = []
    for m in _MANDATORY_RE.finditer(txt):
        key = re.split(r' — | \(', m.group(1))[0].strip()
        out.append(key)
    return out


def test_copilot_summary_declares_which_mandatory_sections_it_covers():
    """Copilot 이 **자동 주입받는 유일한 문서**는 `.github/copilot-instructions.md`
    다(378KB CLAUDE.md 는 자동 로드되지 않는다). 그래서 CLAUDE.md 에 새 의무
    섹션이 생겨도 Copilot 은 영영 모른다 — 2026-09-06 감사가 그 드리프트를
    지적했고(#17 이후 미반영), 실제로 이 세션에서 두 문서가 "라이브 장애" 한
    낱말에 **서로 다른 면제**를 걸고 있는 것이 발견됐다.

    커버리지 선언을 파일 안 매니페스트로 두고, 목록을 **CLAUDE.md 의 실제
    헤딩에서 파생**해 대조한다 — "이 목록은 누가 갱신하나"(#24)의 답이 사람이
    아니라 이 테스트가 되게. 대조 대상이 0건이면 통과가 아니라 실패(#54)."""
    keys = _mandatory_section_keys()
    assert len(keys) >= 3, f"CLAUDE.md 의무 섹션을 {len(keys)}개만 찾았다 — 눈먼 가드"
    txt = _COPILOT.read_text(encoding="utf-8")
    block = re.search(r'<!-- covers-claude-md-sections:(.*?)-->', txt, re.S)
    assert block, "copilot-instructions.md 에 covers-claude-md-sections 매니페스트가 없다"
    declared = {ln.strip("- ").strip() for ln in block.group(1).splitlines()
                if ln.strip().startswith("- ")}
    missing = [k for k in keys if k not in declared]
    extra = [d for d in declared if d not in keys]
    assert not missing, (
        f"CLAUDE.md 의 의무 섹션이 Copilot 요약에 선언돼 있지 않다 — 요약을 쓰고 "
        f"매니페스트에 추가할 것: {missing}")
    assert not extra, f"매니페스트가 없는 섹션을 가리킨다(이름 변경/삭제): {extra}"


def test_summary_docs_name_their_source_and_who_wins():
    """요약본이 출처와 **우선순위**를 밝히지 않으면, 요약이 낡아 더 느슨해졌을 때
    읽는 쪽이 그걸 '예외' 로 읽는다 — 이 세션에서 실제로 그렇게 됐다(copilot 의
    회귀 불릿이 CLAUDE.md 에 없는 예외를 열고 있었다).

    ⚠️ 대상은 이름 열거가 아니라 **구조**로 고른다(#24): Copilot 이 자동 주입하는
    `.github` 지침 두 개 + CLAUDE.md 의 실수 섹션을 요약하는 스킬(본문이 그렇게
    말하는 것). 새 스킬이 실수 목록을 베끼면 자동으로 이 검사에 들어온다."""
    import pathlib as _p
    targets = [_COPILOT, _TRADE_INST]
    for sk in sorted((_ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        if "과거 실수" in sk.read_text(encoding="utf-8"):
            targets.append(sk)
    assert len(targets) >= 3, f"대조 대상이 {len(targets)}개뿐 — 눈먼 가드(#54)"
    bad = []
    for f in targets:
        t = f.read_text(encoding="utf-8")
        names_source = "CLAUDE.md" in t
        # 우선순위: '이긴다' 또는 'source of truth' 를 명시
        declares_precedence = ("이깁니다" in t or "이긴다" in t
                               or "source of truth" in t.lower())
        if not (names_source and declares_precedence):
            bad.append((str(f.relative_to(_ROOT)), names_source, declares_precedence))
    assert not bad, (
        "요약본이 출처 또는 '충돌 시 누가 이기는지' 를 안 밝힌다 "
        f"(파일, 출처명시, 우선순위명시): {bad}")


def test_summary_docs_do_not_claim_to_mirror_the_full_mistake_list():
    """요약본이 '전량 반영' 을 주장하면 읽는 쪽이 거기 없는 실패모드를 **없는
    것으로** 읽는다. 스킬의 Gotchas 헤더가 `mirrors CLAUDE.md "⛔ 과거 실수"`
    라고 적어 놓고 실제로는 초기 항목만 담고 있었다(2026-09-06 감사).

    발췌라고 밝히는 것까지가 계약이다 — 264개를 복제하면 감사가 지적한 예산
    문제를 스킬 쪽에 새로 만든다."""
    claims = re.compile(r'(mirrors|전량|전부 반영|모든 실수)', re.I)
    excerpt = re.compile(r'(발췌|excerpt|subset)', re.I)
    bad = []
    for f in [_COPILOT, _TRADE_INST, *sorted((_ROOT / ".claude" / "skills").glob("*/SKILL.md"))]:
        t = f.read_text(encoding="utf-8")
        if "과거 실수" not in t:
            continue
        if claims.search(t) and not excerpt.search(t):
            bad.append(str(f.relative_to(_ROOT)))
    assert not bad, f"실수 목록을 '전량 반영' 인 것처럼 적었다(발췌라고 밝힐 것): {bad}"
