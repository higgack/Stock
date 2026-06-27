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


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files"],
        capture_output=True, text=True, check=True).stdout
    files = []
    for rel in out.splitlines():
        if rel.endswith(_EXTS) and Path(rel).name != _SELF:
            files.append(_ROOT / rel)
    return files


def test_no_hardcoded_secrets_in_tracked_files():
    """추적된 코드/스크립트/템플릿에 하드코딩 credential 이 없어야.
    노출 시 .env + os.getenv 로 이전(CLAUDE.md Secrets 규칙). 회전 권고."""
    hits: list[str] = []
    files = _tracked_files()
    assert files, "추적파일 0개 — git ls-files 실패?"

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
                    hits.append(f"{fp.relative_to(_ROOT)}:{ln_no} [{label}]")
            m = _ASSIGN.search(line)
            if m:
                val = m.group(1)
                # env 조회/플레이스홀더 제외 + 고엔트로피(무작위 키스러움)만 신고.
                if not _BENIGN.search(line) and _shannon(val) >= 3.2:
                    hits.append(f"{fp.relative_to(_ROOT)}:{ln_no} [hardcoded secret literal]")

    assert not hits, (
        "하드코딩 시크릿 의심 — .env + os.getenv 로 이전하고 노출 키 회전:\n  "
        + "\n  ".join(sorted(set(hits))))
