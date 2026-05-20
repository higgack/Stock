"""Fix Send-to-Telegram button JS syntax error (line 1889 in dashboard).

Bug: alert 메시지의 \\n 이 4단계 escape (Python NEW_JS source →
patch script → daily_generator.py source → JS string in soup) 통과
하면서 literal newline 으로 변환됨. JS single-quote string 안에서
literal newline 은 syntax error.

Fix: alert 메시지의 newline 제거 → 한 줄로 합쳐 ' · ' separator 로
구분.

Run on deploy host:

    ~/standardview/.venv/bin/python ~/stock/sv_button_alertfix.py
    cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -5
"""
from __future__ import annotations
import ast
import re
import sys
import time
from pathlib import Path

LIVE_GEN = Path.home() / "standardview" / "scripts" / "daily_generator.py"

if not LIVE_GEN.exists():
    sys.exit(f"NOT FOUND: {LIVE_GEN}")

src = LIVE_GEN.read_text()

# Find any alert(...) call with embedded \n inside the JS string and
# rewrite to use ' · ' separator. The script.string content was
# injected by an earlier patch — we look for the specific alert line.
PATTERN = re.compile(
    r"alert\('Telegram push 실패[^']*'(?:\s*\+\s*\([^)]*\)\s*\+\s*'[^']*')+\);"
)
m = PATTERN.search(src)
if not m:
    print("alert(...) line not found — already fixed or shape changed")
    sys.exit(0)

OLD = m.group(0)
print(f"matched (len={len(OLD)}):")
print(repr(OLD[:200]))

# Replace newline-bearing alert with single-line version
NEW = (
    "alert('Telegram push 실패 · ' "
    "+ (d.error || '') + ' · ' + (d.stderr || ''));"
)

new_src = src.replace(OLD, NEW)
try:
    ast.parse(new_src)
except SyntaxError as e:
    sys.exit(f"SYNTAX ERROR after replace: {e}")

bak = LIVE_GEN.with_suffix(f".py.bak.{int(time.time())}")
bak.write_text(src)
print(f"backup: {bak}")
LIVE_GEN.write_text(new_src)
print(f"patched: {LIVE_GEN}")
print()
print("Next:")
print(f"  cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -5")
print("  Then hard-refresh dashboard (Ctrl+Shift+R) and click 'Send to Telegram'.")
