"""Standard View — wire 'Send to Telegram' dashboard button.

The button (in base.html template) is currently a disabled <a> with no
handler. This patch:

  1. Adds POST /api/push-to-telegram endpoint to backend/main.py.
     Subprocess-launches telegram_pusher.py with 120s timeout.

  2. Adds a post-process step to daily_generator.py that, after all soup
     transforms, finds the Send-to-Telegram <a class="btn primary"
     aria-disabled="true"> and rewrites it to a working button with
     onclick fetch handler + injects a small <script> defining
     sendToTelegram() that shows a toast on success/failure.

Run on deploy host:

    ~/standardview/.venv/bin/python ~/sv_telegram_button.py

Then restart backend + regenerate dashboard. Idempotent (marker checks).
"""

from __future__ import annotations
import ast
import sys
import time
from pathlib import Path

LIVE_BACKEND  = Path.home() / "standardview" / "backend" / "main.py"
LIVE_GEN      = Path.home() / "standardview" / "scripts" / "daily_generator.py"
STOCK_BACKEND = Path.home() / "stock" / "standardview" / "backend" / "main.py"
STOCK_GEN     = Path.home() / "stock" / "standardview" / "scripts" / "daily_generator.py"

# ------------------------------------------------------------------
# Patch 1: backend/main.py — add POST /api/push-to-telegram
# ------------------------------------------------------------------
ENDPOINT_MARKER = "# sv-telegram-button-endpoint v1"
ENDPOINT_CODE = f'''

{ENDPOINT_MARKER}
@app.post("/api/push-to-telegram")
def push_to_telegram():
    """Trigger telegram_pusher.py — sends today's brief to the
    STANDARDVIEW channel (MD text + HTML attachment). Used by the
    dashboard's 'Send to Telegram' button as an on-demand push (vs
    the scheduled standardview-daily.timer / hourly.timer)."""
    import subprocess as _sp
    from pathlib import Path as _P
    pusher = _P(__file__).resolve().parent.parent / "scripts" / "telegram_pusher.py"
    venv_py = _P.home() / "standardview" / ".venv" / "bin" / "python"
    if not pusher.exists():
        return {{"ok": False, "error": "pusher script missing"}}
    py = str(venv_py) if venv_py.exists() else "python3"
    try:
        r = _sp.run([py, str(pusher)], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return {{"ok": True, "message": "Telegram push 완료",
                    "stdout": (r.stdout or "")[-300:]}}
        return {{"ok": False, "error": "pusher failed",
                "stderr": (r.stderr or "")[-300:],
                "stdout": (r.stdout or "")[-300:]}}
    except _sp.TimeoutExpired:
        return {{"ok": False, "error": "timeout (120s)"}}
'''


def patch_backend(p: Path):
    if not p.exists():
        print(f"backend: SKIP — {p} not found")
        return False
    src = p.read_text()
    if ENDPOINT_MARKER in src:
        print(f"backend: {p} already patched — skip")
        return False
    src = src.rstrip() + "\n" + ENDPOINT_CODE + "\n"
    # validate before write
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"backend: SYNTAX ERROR after patch — {e}; SKIP {p}")
        return False
    p.write_text(src)
    print(f"backend: endpoint appended → {p}")
    return True


# ------------------------------------------------------------------
# Patch 2: daily_generator.py — post-process button rewrite + script
# ------------------------------------------------------------------
GEN_MARKER = "# sv-telegram-button-postprocess v1"
GEN_INSERT = '''

''' + GEN_MARKER + '''
def _wire_send_to_telegram_button(soup):
    """Find the 'Send to Telegram' anchor button (disabled by default in
    friend\'s template), rewrite as a working button with onclick fetch
    to /api/push-to-telegram, and inject a tiny toast helper script."""
    btn = None
    for a in soup.find_all("a", class_="btn"):
        txt = (a.get_text() or "").strip()
        if "Send to Telegram" in txt or "텔레그램" in txt:
            btn = a
            break
    if btn is None:
        return
    # Strip the aria-disabled marker, add onclick + cursor styling.
    if btn.has_attr("aria-disabled"):
        del btn["aria-disabled"]
    btn["onclick"] = "svPushTelegram(this); return false;"
    btn["href"]    = "#"
    btn["style"]   = (btn.get("style", "") + ";cursor:pointer").lstrip(";")
    # Inject the toast helper once (idempotent — check for existing).
    if not soup.find("script", id="sv-telegram-push"):
        from bs4 import Tag as _Tag
        script = soup.new_tag("script", id="sv-telegram-push")
        script.string = """
function svPushTelegram(el) {
  if (el.dataset.busy === '1') return;
  el.dataset.busy = '1';
  const origText = el.innerText;
  el.innerText = '전송 중…';
  el.style.opacity = '0.7';
  fetch('/api/push-to-telegram', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
      el.innerText = d.ok ? '✓ 전송됨' : '✗ 실패';
      el.style.opacity = d.ok ? '1' : '0.6';
      setTimeout(() => {
        el.innerText = origText;
        el.dataset.busy = '';
        el.style.opacity = '1';
      }, 3500);
      if (!d.ok) {
        console.error('SV push failed:', d);
        alert('Telegram push 실패\\n\\n' + (d.error || '') + '\\n' + (d.stderr || ''));
      }
    })
    .catch(e => {
      el.innerText = '✗ 네트워크 오류';
      el.dataset.busy = '';
      console.error('SV push network error:', e);
      setTimeout(() => { el.innerText = origText; }, 3500);
    });
}
"""
        body = soup.find("body")
        if body is not None:
            body.append(script)
'''


def patch_gen(p: Path):
    if not p.exists():
        print(f"gen: SKIP — {p} not found")
        return False
    src = p.read_text()
    if GEN_MARKER in src:
        print(f"gen: {p} already patched — skip")
        return False
    # Append function definition.
    new_src = src.rstrip() + "\n" + GEN_INSERT + "\n"

    # Also need to CALL the function. Find a stable callsite: the soup
    # object should be in scope right before the HTML is written. Look
    # for the pattern '.write_text(' on the html output (most generators
    # do `out_path.write_text(str(soup))` or similar).
    import re as _re
    # candidate: line that writes the timestamped html file
    callsite_re = _re.compile(
        r"(\n\s*)((?:[A-Za-z_][\w\.]*)\.write_text\(\s*str\(soup\))",
        _re.MULTILINE,
    )
    m = callsite_re.search(new_src)
    if not m:
        # Fallback — try html.write_text(str(soup))
        callsite_re2 = _re.compile(
            r"(\n\s*)((?:html|out|path|target).*\.write_text\([^)]*soup[^)]*\))",
            _re.MULTILINE,
        )
        m = callsite_re2.search(new_src)
    if not m:
        print(f"gen: WARN — couldn't find soup-write callsite to inject"
              f" _wire_send_to_telegram_button(soup). Function added but"
              f" not invoked. Add manually: '_wire_send_to_telegram_button(soup)'"
              f" right before the str(soup) write.")
    else:
        indent = m.group(1)
        new_src = (
            new_src[: m.start()]
            + indent
            + "_wire_send_to_telegram_button(soup)"
            + new_src[m.start():]
        )
        print(f"gen: callsite inserted before {m.group(2).strip()[:60]}")

    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print(f"gen: SYNTAX ERROR after patch — {e}; SKIP {p}")
        return False
    p.write_text(new_src)
    print(f"gen: post-process function added → {p}")
    return True


def backup_pair(*paths: Path) -> None:
    ts = int(time.time())
    for p in paths:
        if p.exists():
            bak = p.with_suffix(p.suffix + f".bak.{ts}")
            bak.write_text(p.read_text())
            print(f"backup: {bak.name}")


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------
backup_pair(LIVE_BACKEND, LIVE_GEN, STOCK_BACKEND, STOCK_GEN)

did_any = False
for p in (LIVE_BACKEND, STOCK_BACKEND):
    if patch_backend(p):
        did_any = True
for p in (LIVE_GEN, STOCK_GEN):
    if patch_gen(p):
        did_any = True

if did_any:
    print()
    print("✓ patch applied. Next steps:")
    print("  sudo systemctl restart standardview-backend")
    print("  cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -5")
    print()
    print("Then click the 'Send to Telegram' button on the dashboard.")
else:
    print()
    print("No changes — all targets already patched or missing.")
