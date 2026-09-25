"""커밋된 추적파일에 하드코딩 시크릿 노출 차단 — 자동 회귀 가드(sovereign-skills
`scan_secrets` 영감, 사용자 2026-06-27). 기존엔 `.gitignore`(.env 보호) + CLAUDE.md
수동 RULE(Secrets·실수 #5)뿐 — 사람 규율에만 의존. lat.md 정합성 테스트를 회귀로
자동화했듯 시크릿 노출도 `make test` 로 영구 차단.

탐지: 제공자별 고신호 토큰(텔레그램/AWS/구글/Slack/private key) + 시크릿명 변수에
고엔트로피 literal 대입. 오탐 회피: env 조회(getenv/environ)·플레이스홀더·저엔트로피
값은 제외. ⚠️ 진짜 키 추가 시 fail → .env + os.getenv 로 옮길 것(literal 코드 금지)."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SELF = Path(__file__).name

# 스캔 대상 추적파일 확장자(코드/스크립트/템플릿 — 시크릿이 새는 표면).
_EXTS = (".py", ".sh", ".html", ".js", ".env.example")

# 제공자별 고신호 패턴(거의 무오탐 — 실제 누출 credential 형식).
_PROVIDER = [
    ("Telegram bot token", re.compile(r"\b\d{8,10}:AA[\w-]{32,}\b")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# 시크릿 의미 변수명에 literal 문자열 대입(env 조회·플레이스홀더 제외 후 엔트로피 판정).
_ASSIGN = re.compile(
    r"""(?ix)
    \b\w*                                          # DASHBOARD_ 등 식별자 prefix 허용
    (?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|
       auth[_-]?token|client[_-]?secret|private[_-]?key|bearer)\b
    \s*[:=]\s*
    ["']([^"'\n]{12,})["']
    """
)

# 명백한 비밀 아님 — 플레이스홀더/예시/리다크션/env 참조.
_BENIGN = re.compile(
    r"(?i)(redacted|your[_-]?|example|placeholder|changeme|dummy|sample|"
    r"fake|test|xxx+|\.\.\.|<[^>]+>|\$\{|%\(|\{[a-z_]+\}|os\.|getenv|environ)")


def _shannon(s: str) -> float:
    """문자 엔트로피(bits/char) — 무작위 키는 높고 영단어/반복은 낮음."""
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _in_venv(root: Path, rel: str, seen: dict) -> bool:
    """rel 의 조상 디렉터리가 가상환경(`pyvenv.cfg`)인가 — 커밋할 일이 없는 로컬 환경이다.

    이름(.venv·.backfill-venv…)을 열거하지 않고 구조로 가른다(#24) — 운영 `.backfill-venv/`
    는 무시 목록 밖이라 이걸 안 빼면 그 site-packages 의 예시 키(문서용 AWS 키 등)가
    `make test` 를 빨갛게 만든다."""
    parts = Path(rel).parts[:-1]
    for i in range(1, len(parts) + 1):
        d = Path(*parts[:i])
        if d not in seen:
            seen[d] = (root / d / "pyvenv.cfg").is_file()
        if seen[d]:
            return True
    return False


def _tracked_files(root: Path = _ROOT) -> list[Path]:
    """커밋될 파일 — 이미 추적 중인 것 **과 아직 add 안 한 새 파일**(무시 목록 밖).

    ⚠️ 추적 파일만 보면 새 파일은 `git add` 전에 돌린 `make test` 를 그대로
    통과하고 커밋에 실린다 — 2026-09-25 새 테스트의 가짜 토큰 리터럴이 그렇게
    커밋된 뒤에야 걸렸다(실수 #407). 가드의 범위는 '커밋될 것' 이어야 한다.
    새 파일 중 가상환경 안의 것은 뺀다(추적 중이면 이미 커밋된 것이라 그대로 본다)."""
    def ls(*extra: str) -> list[str]:
        return subprocess.run(
            ["git", "-C", str(root), "ls-files", *extra],
            capture_output=True, text=True, check=True).stdout.splitlines()
    seen: dict = {}
    rels = ls("--cached") + [r for r in ls("--others", "--exclude-standard")
                             if not _in_venv(root, r, seen)]
    return [root / r for r in dict.fromkeys(rels)
            if r.endswith(_EXTS) and Path(r).name != _SELF]


def _scan(files: list[Path], root: Path = _ROOT) -> list[str]:
    """파일들 → `경로:줄 [종류]` 목록."""
    hits: list[str] = []
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ln_no, line in enumerate(text.splitlines(), 1):
            if "nosec" in line.lower():            # 의도적 예외 표식 허용
                continue
            for label, pat in _PROVIDER:
                if pat.search(line):
                    hits.append(f"{fp.relative_to(root)}:{ln_no} [{label}]")
            m = _ASSIGN.search(line)
            if m:
                val = m.group(1)
                # env 조회/플레이스홀더 제외 + 고엔트로피(무작위 키스러움)만 신고.
                if not _BENIGN.search(line) and _shannon(val) >= 3.2:
                    hits.append(f"{fp.relative_to(root)}:{ln_no} [hardcoded secret literal]")
    return hits


def test_no_hardcoded_secrets_in_tracked_files():
    """추적된 코드/스크립트/템플릿에 하드코딩 credential 이 없어야.
    노출 시 .env + os.getenv 로 이전(CLAUDE.md Secrets 규칙). 회전 권고."""
    files = _tracked_files()
    assert files, "추적파일 0개 — git ls-files 실패?"
    hits = _scan(files)

    assert not hits, (
        "하드코딩 시크릿 의심 — .env + os.getenv 로 이전하고 노출 키 회전:\n  "
        + "\n  ".join(sorted(set(hits))))


def test_scope_is_what_would_be_committed(tmp_path):
    """#407 — 범위는 '커밋될 것': 추적 중인 것 + 아직 add 안 한 새 파일. 무시 목록과
    가상환경(`pyvenv.cfg`)의 새 파일은 뺀다. **임시 저장소**에서 잰다 — 레포 트리에 파일을
    쓰면 다른 가드의 mtime·추적 상태를 흔든다(#365). 토큰은 소스에 리터럴로 두지 않는다."""
    tok = "123456789" + ":" + "AA" + "x" * 34

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "clean.py").write_text("x = 1\n")
    git("add", ".gitignore", "clean.py")
    (tmp_path / "new_test.py").write_text(f'T = "{tok}"\n')        # add 전 — 잡혀야 한다
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "a.py").write_text(f'T = "{tok}"\n')   # 무시 목록 — 안 본다
    venv = tmp_path / "anyname" / "lib"
    venv.mkdir(parents=True)
    (tmp_path / "anyname" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "b.py").write_text(f'T = "{tok}"\n')                    # 가상환경 — 안 본다
    nested = tmp_path / "tools" / "env2"                           # 하위 디렉터리의 가상환경도
    (nested / "lib").mkdir(parents=True)
    (nested / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (nested / "lib" / "c.py").write_text(f'T = "{tok}"\n')
    files = _tracked_files(tmp_path)
    assert sorted(str(p.relative_to(tmp_path)) for p in files) == ["clean.py", "new_test.py"]
    assert _scan(files, tmp_path) == ["new_test.py:1 [Telegram bot token]"]
    # 반대 증거(#25) — 가상환경이라도 **추적 중**이면 이미 커밋된 것이라 본다
    git("add", "-f", "anyname/lib/b.py")
    assert "anyname/lib/b.py:1 [Telegram bot token]" in _scan(_tracked_files(tmp_path), tmp_path)
