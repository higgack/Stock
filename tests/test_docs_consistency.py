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
