#!/usr/bin/env python3
"""배포전 셀프리뷰 보조 — **조용히 사라진** 공개 심볼·테스트를 base 대비로 센다.

왜 이게 필요한가(§Pre-commit 7 은 오늘 "base 대비 전체 diff 재독" 이라고만 적어
사람 눈에 맡긴다):

  · #210(2026-08-23) — `_derived_desc` 본문을 문자열 replace 로 갈아끼우다 범위를
    잘못 잡아 뒤따르던 **252줄(함수 셋)이 함께 지워졌다**. `ast.parse` 통과,
    `import` 통과, 회귀 2,785개 중 **하나**만 우연히 잡았다.
  · #278(2026-09-04)·#358(2026-09-13) — 뮤테이션 복원이 **커밋된 판으로 되돌려**
    그 뒤 자란 것을 날렸다. ⚠️ **이 도구는 그 둘을 못 잡는다**(아래 "못 보는 축").

기존 가드가 못 보는 축이다: symtable 전수 검사(#210)는 **정의되지 않은 참조**를
잡지 그 정의와 호출부가 **같이** 지워진 경우는 못 잡고, AST 중복정의 검사(#59)는
**늘어난** 이름만 본다. 여기서 보는 것은 **줄어든** 이름이다.

판정 규약
  · **파일이 통째로 삭제된 것은 세지 않는다** — 그건 보이는 결정이다(#222).
    양쪽에 다 있는 파일이 조용히 **작아진** 경우만 본다.
  · 공개(`_` 로 시작하지 않는) top-level def/class/모듈상수 + 공개 메서드.
    private 는 리팩터로 자주 바뀌므로 소음이 된다(#25·#260 늘 뜨는 경고는
    아무것도 안 재는 것과 같다).
  · `tests/**` 는 `def test_` 개수가 **줄면** 잡는다(이름이 바뀌는 리팩터가
    흔해 이름이 아니라 수로 본다).

이 도구는 **읽기 전용**이다(#264·#283·#321) — `git show` 로 base 판을 읽고
작업트리를 읽을 뿐 아무것도 쓰지 않는다.

    cd ~/stock && .venv/bin/python scripts/public_surface_check.py
    cd ~/stock && make surface          # 같은 것

기준이 둘이다 — `base`(브랜치 전체의 순손실)와 `HEAD`(마지막 커밋 이후 작업트리).
⚠️ **못 보는 축**(#274 — 실측으로 확인했고, 과대 주장하지 않는다 #286):
  · **미커밋 상태에서 자란 것이 날아간 경우는 못 본다** — `git checkout <file>` 은
    작업트리를 HEAD 와 **같게** 만들므로 개수 차가 0 이다. 즉 #278·#358 은 여전히
    §Pre-commit 의 백업 규율(`*.FIXED` + `cmp`)이 막는 것이지 이 도구가 아니다.
  · 중간 커밋 사이(A→B)의 손실도 안 본다. base·HEAD 두 기준만 본다.
잡는 것은 **커밋된 판에 있던 것이 지금 없는** 경우 — #210 이 정확히 그 모양이고,
실측으로 `derive_spreads` 삭제를 HEAD 기준이 잡는 것을 확인했다.

rc: 0 = 사라진 것 없음 / 1 = 사라진 것 있음(사람이 판단) / 2 = 판정 불가.
'사라진 것 있음' 이 곧 결함은 아니다 — **의도한 삭제면 그렇다고 말하고 넘어가는**
자리다(#43 침묵이 최악).
"""
from __future__ import annotations

import ast
import subprocess
import sys

BASE_DEFAULT = "origin/claude/stock-trading-automation-xqYf7"
_SCAN_DIRS = ("bot/", "trade/", "tests/", "scripts/")


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def public_surface(src: str) -> set[str]:
    """소스 한 벌의 **공개 심볼** 집합 — 순수 함수(값으로 검증된다, #41·#176).

    top-level `def`/`class`/대문자 상수 + 클래스의 공개 메서드(`C.m`).
    `_` 로 시작하는 것은 세지 않는다.
    """
    tree = ast.parse(src)
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue
            out.add(node.name)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not sub.name.startswith("_")):
                        out.add(f"{node.name}.{sub.name}")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper() and not t.id.startswith("_"):
                    out.add(t.id)
    return out


def test_count(src: str) -> int:
    """`def test_` 개수 — 이름이 아니라 **수**로 본다(이름 리팩터는 정상이다)."""
    tree = ast.parse(src)
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("test_"))


def shrinkage(before: str, after: str, *, is_test: bool) -> tuple[list[str], int]:
    """(사라진 공개 심볼, 줄어든 테스트 수) — 순수 함수.

    ⚠️ `tests/**` 는 **수만** 본다. 테스트 이름을 바꾸는 리팩터는 정상인데
    `test_a → test_x` 를 심볼 소실로 세면 매번 뜨는 경고가 되고, 그러면
    아무것도 안 재는 것과 같다(#25·#260). 첫 판이 정확히 그랬다(실측).
    """
    dropped = 0
    if is_test:
        dropped = max(0, test_count(before) - test_count(after))
        return [], dropped
    lost = sorted(public_surface(before) - public_surface(after))
    return lost, dropped


def _merge_base(ref: str) -> str:
    """`ref` 와 HEAD 의 분기점. 못 구하면 `ref` 그대로.

    ⚠️ base 는 **Copilot 과 공유하는 배포 지점**이다(CLAUDE.md §다른 AI
    에이전트와 레포 공유). base 를 그대로 대조하면 *남이 base 에 더한* 공개
    심볼이 내 브랜치에선 '사라짐' 으로 읽힌다(2026-09-16 독립 리뷰가 base 를
    40커밋 되감아 재현: `parse_groups 1개 사라짐`). 분기점과 비교하면 내가
    지운 것만 남는다."""
    rc, out = _git("merge-base", ref, "HEAD")
    sha = out.strip()
    return sha if rc == 0 and sha else ref


def _changed_files(base: str) -> list[str]:
    # ⚠️ `base...HEAD` 가 아니라 `base`(= **작업트리** 대 base) 다. 이 도구가 도는
    # 자리는 §Pre-commit 7 = **커밋 전**이라, 커밋된 것만 보면 #278·#358 처럼
    # 미커밋 상태에서 날아간 테스트를 정확히 못 본다.
    rc, out = _git("diff", "--name-only", base)
    if rc != 0:
        return []
    names = [n for n in out.splitlines() if n.endswith(".py")]
    return [n for n in names if n.startswith(_SCAN_DIRS)]


def _scan(ref: str) -> tuple[list[str], int]:
    """(findings, 대조한 파일 수) — `ref` 판과 **작업트리**를 비교한다."""
    findings: list[str] = []
    compared = 0
    for path in _changed_files(ref):
        rc_b, before = _git("show", f"{ref}:{path}")
        if rc_b != 0:
            continue                      # 그 판에 없던 새 파일 — 늘어난 것은 안 본다
        try:
            with open(path, encoding="utf-8") as fh:
                after = fh.read()
        except FileNotFoundError:
            continue                      # 통째로 삭제 = 보이는 결정이라 세지 않는다
        try:
            lost, dropped = shrinkage(before, after, is_test=path.startswith("tests/"))
        except SyntaxError as exc:         # 파싱 실패는 다른 가드의 몫(#210)
            findings.append(f"❓ {path} — 파싱 실패({exc.__class__.__name__}), 판정 불가")
            continue
        compared += 1
        if lost:
            findings.append(f"❌ {path} — 공개 심볼 {len(lost)}개 사라짐: {', '.join(lost[:12])}"
                            + (f" 외 {len(lost) - 12}" if len(lost) > 12 else ""))
        if dropped:
            findings.append(f"❌ {path} — `def test_` 가 {dropped}개 줄었다")
    return findings, compared


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 and argv[1] else BASE_DEFAULT
    rc, _ = _git("rev-parse", "--verify", base)
    if rc != 0:
        print(f"❓ 판정 불가 — base ref 를 못 찾았다: {base}")
        print("   cd ~/stock && git fetch origin <base-branch>")
        return 2

    # 기준이 **둘**이다. base 는 브랜치 전체의 순손실을, HEAD 는 마지막 커밋 이후
    # 작업트리에서 날아간 것을 본다 — 후자가 #278·#358(미커밋 테스트 유실)의 축이고,
    # base 하나만 보면 이번 브랜치에서 **새로 더한** 것을 지워도 조용하다(실측).
    worst, total_cmp = 0, 0
    for label, ref in (("base", _merge_base(base)), ("HEAD", "HEAD")):
        findings, compared = _scan(ref)
        total_cmp += compared
        print(f"# public_surface_check · {label}={ref} · 대조 {compared}개 파일")
        if findings:
            for line in findings:
                print("  " + line)
            worst = max(worst, 1)
        elif compared:
            print("  ✅ 조용히 사라진 공개 심볼·테스트 없음")
        else:
            print("  ⚠️ 대조한 파일이 0개 — 이 기준으로는 판정하지 않는다(#54)")

    if total_cmp == 0:
        print("❓ 판정 불가 — 어느 기준으로도 대조할 파일이 없었다")
        return 2
    if worst:
        print("\n↪ 의도한 삭제면 그렇다고 보고에 한 줄 적고 넘어갈 것(#43). "
              "의도하지 않았으면 #210 이 그 사고다.")
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
