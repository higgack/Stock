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

    # (계열, 항목) — 그 계열 항목을 여럿 인용하지만 **그 계열의 사례는 아닌** 것.
    _NOT_MEMBER = {  # noqa: N806 — 테스트 지역 allowlist(#24)
        # #286 은 지시서 감사 자체의 기록이다. #119(규율을 구조로)·#216·#233 을
        # 인용하지만 그건 '손 bump 실패'와 '서수 드리프트'를 예로 든 것이지
        # 캐시가 fix 를 가린 사례가 아니다.
        ("캐시가 fix 를 가림", "286"),
    }

    sec, ent = _mistake_entries()
    themes = _themes(sec)
    missing = []
    for name, listed in themes.items():
        fam = {_REF_RE.fullmatch(r).group(1) for r in listed}
        for n, body in ent.items():
            if n in fam or (name, n) in _NOT_MEMBER:
                continue
            cites = set(_REF_RE.findall(body)) - {n}
            if len(cites & fam) >= 3:
                missing.append((name, n, sorted(cites & fam)))
    assert not missing, (
        "이 계열을 3개 이상 인용하는데 색인에 없다 — 색인 줄에 번호를 추가할 것: "
        f"{missing}")


def test_family_rule_actually_fires():
    """가드가 '작동함'을 보이려면 실제로 깨지는 입력까지 밀어 볼 것(#91c).
    위 테스트가 초록인 게 '색인이 최신이라서'인지 '규칙이 아무것도 안 세서'인지
    이 테스트가 가른다."""
    fam = {"1", "2", "3"}
    body = "…#1·#2·#3 에 이은 같은 실패다."
    cites = set(_REF_RE.findall(body))
    assert len(cites & fam) >= 3, "3개 인용을 못 센다"
    assert len({"1", "2"} & fam) < 3, "2개 인용까지 잡으면 오탐이 난다"


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
    pointed = sorted(set(re.findall(r'→ REFERENCE §실수 #(\d+[a-z]?)', txt)))
    assert len(pointed) >= 20, f"REFERENCE 포인터가 {len(pointed)}건뿐 — 눈먼 가드"
    dangling = [n for n in pointed if f"### 실수 #{n}\n" not in ref]
    assert not dangling, f"CLAUDE.md 가 없는 REFERENCE 절을 가리킨다: {dangling}"


def test_injected_rules_file_stays_within_budget():
    """CLAUDE.md 는 매 턴 전량 주입된다 — 파일 스스로 "compact 유지" 를 정해 뒀다.

    2026-09-06 감사 실측: 항목 평균이 319자(첫 60개)에서 1,090자(마지막 60개)로
    3.4배 자랐고, 6~8주마다 파일이 배가되는 궤적이었다. 상한을 두면 다음에 넘길 때
    "접을 것을 접었나"를 묻게 된다(넘으면 §주제 색인 기준으로 서사를 REFERENCE 로).
    상한은 현재값(179k)에 여유를 준 값이며, 늘릴 땐 왜 늘리는지 같이 적을 것."""
    n = len(_CLAUDE.read_text(encoding="utf-8"))
    assert n <= 195_000, (
        f"CLAUDE.md 가 {n:,}자 — 매 턴 주입되는 예산을 넘었다. "
        "긴 항목의 서사를 CLAUDE_REFERENCE.md 로 접을 것(규칙 문장은 남긴다).")
