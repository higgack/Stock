"""Standard View daily_generator.py — v3 patch.

Fix: brief inject 루프가 각 section body 를 (details + 다음 section
가기 전 갭) 두 번 렌더링하는 버그. 'intro' 는 첫 section 앞 텍스트
에만 적용해야 하고, 'tail' 도 마지막 section body 와 중복되므로
제거. lead-token dedup 도 강화 (전체 token set intersection).

Run on deploy host:

    ~/standardview/.venv/bin/python ~/sv_v3.py
    cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -10
"""

from __future__ import annotations
import ast
import re
import sys
import time
from pathlib import Path

GEN = Path.home() / "standardview" / "scripts" / "daily_generator.py"
if not GEN.exists():
    sys.exit(f"NOT FOUND: {GEN}")

src = GEN.read_text()
backup = GEN.with_suffix(f".py.bak.{int(time.time())}")
backup.write_text(src)
print(f"backup: {backup}")

# ------------------------------------------------------------------
# Fix A: brief inject loop — drop gap-intro + tail duplication
# ------------------------------------------------------------------
MARKER_A = "# B-3.10.2 dedup intro/tail"
if MARKER_A in src:
    print("fixA (brief loop): already applied — skip")
else:
    # Match the exact problematic block (Iterate matches + tail).
    OLD_A = re.compile(
        r"        # Iterate matches, split text into \(intro, sections\.\.\.\)\s*\n"
        r"        parts = \[\]\s*\n"
        r"        last_end = 0\s*\n"
        r"        for m in section_re\.finditer\(formatted\):\s*\n"
        r"            if last_end < m\.start\(\):\s*\n"
        r"                parts\.append\(\(\"intro\", formatted\[last_end:m\.start\(\)\]\)\)\s*\n"
        r"            parts\.append\(\(\"section\", m\.group\(1\), m\.end\(\)\)\)\s*\n"
        r"            last_end = m\.end\(\)\s*\n"
        r"        # tail\s*\n"
        r"        if last_end < len\(formatted\):\s*\n"
        r"            parts\.append\(\(\"tail\", formatted\[last_end:\]\)\)\s*\n",
        re.MULTILINE,
    )
    m = OLD_A.search(src)
    if not m:
        sys.exit("fixA anchor MISS — brief inject loop shape changed")
    NEW_A = (
        f"        {MARKER_A}: intro 는 첫 section 앞 prologue 만,\n"
        "        # tail 은 제거 (마지막 section body 는 details 본문에\n"
        "        # 이미 포함). 각 section body 가 두 번 렌더링되던 버그.\n"
        "        parts = []\n"
        "        first_match_seen = False\n"
        "        for m in section_re.finditer(formatted):\n"
        "            if not first_match_seen:\n"
        "                if m.start() > 0:\n"
        "                    intro_text = formatted[:m.start()].strip()\n"
        "                    if intro_text:\n"
        "                        parts.append((\"intro\", intro_text))\n"
        "                first_match_seen = True\n"
        "            parts.append((\"section\", m.group(1), m.end()))\n"
    )
    src = src[:m.start()] + NEW_A + src[m.end():]
    print("fixA (brief loop): applied — intro/tail 중복 제거")

# ------------------------------------------------------------------
# Fix B: stronger Deal Highlights dedup — token set intersection
# ------------------------------------------------------------------
MARKER_B = "# B-3.10.3 token-set dedup"
if MARKER_B in src:
    print("fixB (deal dedup): already applied — skip")
else:
    # Replace the _is_dup function body (currently SequenceMatcher 0.30
    # + lead_token equal). Add token-set intersection check.
    OLD_B = re.compile(
        r"    def _is_dup\(a: str, b: str\) -> bool:\s*\n"
        r"        if not a or not b:\s*\n"
        r"            return False\s*\n"
        r"        if _SM\(None, a, b\)\.ratio\(\) >= 0\.30:\s*\n"
        r"            return True\s*\n"
        r"        la, lb = _lead_token\(a\), _lead_token\(b\)\s*\n"
        r"        if la and lb and la == lb:\s*\n"
        r"            return True\s*\n"
        r"        return False\s*\n",
    )
    m = OLD_B.search(src)
    if not m:
        print("fixB anchor MISS — _is_dup shape changed; SKIP")
    else:
        NEW_B = (
            "    def _tokens(s: str) -> set:\n"
            "        return {\n"
            "            t.strip() for t in _re.split(\n"
            "                r\"[\\s,\\.·\\u2026\\(\\)\\[\\]'\\\"`~!@#$%^&*\\-_=+:;<>/\\\\|?]+\",\n"
            "                (s or \"\").strip(),\n"
            "            )\n"
            "            if len(t.strip()) >= 2 and t.strip().lower() not in _STOP\n"
            "        }\n"
            f"    def _is_dup(a: str, b: str) -> bool:  {MARKER_B}\n"
            "        if not a or not b:\n"
            "            return False\n"
            "        if _SM(None, a, b).ratio() >= 0.30:\n"
            "            return True\n"
            "        ta, tb = _tokens(a), _tokens(b)\n"
            "        if not ta or not tb:\n"
            "            return False\n"
            "        # 짧은 쪽 기준 50% 이상 토큰 공유 → dup\n"
            "        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)\n"
            "        if len(smaller & larger) / max(len(smaller), 1) >= 0.5:\n"
            "            return True\n"
            "        return False\n"
        )
        src = src[:m.start()] + NEW_B + src[m.end():]
        print("fixB (deal dedup): applied — token-set intersection 50%")

# ------------------------------------------------------------------
# Syntax verify
# ------------------------------------------------------------------
try:
    ast.parse(src)
except SyntaxError as e:
    sys.exit(f"SYNTAX ERROR: {e}\n(backup at {backup})")

GEN.write_text(src)
print(f"wrote: {GEN}")
print()
print("verify:")
print(f"  cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -10")
