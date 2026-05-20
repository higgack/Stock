"""Standard View — install v2 telegram_pusher on live deploy host.

Reads the canonical pusher from ~/stock/standardview/scripts/
telegram_pusher.py and copies it into ~/standardview/scripts/, backing
up the old version. Run after `git pull` brings the canonical down.

Usage on deploy host:

    cd ~/stock && git pull origin claude/stock-trading-automation-xqYf7
    ~/standardview/.venv/bin/python ~/stock/sv_pusher_install.py
    # test send (uses today's brief from sqlite + .md)
    ~/standardview/.venv/bin/python ~/standardview/scripts/telegram_pusher.py
"""
from __future__ import annotations
import ast
import shutil
import sys
import time
from pathlib import Path

SRC = Path.home() / "stock" / "standardview" / "scripts" / "telegram_pusher.py"
DST = Path.home() / "standardview" / "scripts" / "telegram_pusher.py"

if not SRC.exists():
    sys.exit(
        f"canonical pusher missing at {SRC} — run "
        "'cd ~/stock && git pull' first"
    )

src_text = SRC.read_text()
try:
    ast.parse(src_text)
except SyntaxError as e:
    sys.exit(f"canonical pusher has SYNTAX ERROR: {e}")

if DST.exists():
    bak = DST.with_suffix(f".py.bak.{int(time.time())}")
    shutil.copy(DST, bak)
    print(f"backup: {bak}")

DST.write_text(src_text)
DST.chmod(0o755)
print(f"installed: {DST}")
print()
print("test send (sends today's brief to all configured chat IDs):")
print(f"  ~/standardview/.venv/bin/python {DST}")
