"""Update Send-to-Telegram button UX in daily_generator.py.

v1 behavior: click → '전송 중…' (grey-ish opacity 0.7) → on success
'✓ 전송됨' → 3.5s timer revert.

v2 behavior (user request 2026-05-21):
  click → immediately switch to green background + 'Sent' text
  (acts as tracking indicator while message is in flight)
  fetch confirms delivery → revert to original blue 'Send to Telegram'

Implementation: rewrite the svPushTelegram JS function inside
_wire_send_to_telegram_button. Live file + canonical mirror.
"""
from __future__ import annotations
import ast
import re
import sys
import time
from pathlib import Path

LIVE_GEN  = Path.home() / "standardview" / "scripts" / "daily_generator.py"
STOCK_GEN = Path.home() / "stock" / "standardview" / "scripts" / "daily_generator.py"

# New JS body (without leading/trailing triple-quote — we'll wrap it in
# the file). The function will live inside daily_generator.py's
# _wire_send_to_telegram_button.
NEW_JS = '''
function svPushTelegram(el) {
  if (el.dataset.busy === '1') return;
  el.dataset.busy = '1';
  const origText = el.innerText;
  const origStyle = el.getAttribute('style') || '';
  // Snapshot the original gradient so we can restore it exactly.
  el.dataset.origStyle = origStyle;
  el.innerText = 'Sent';
  el.style.background = '#22c55e';
  el.style.borderColor = '#22c55e';
  el.style.color = '#fff';
  fetch('/api/push-to-telegram', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
      el.dataset.busy = '';
      if (d.ok) {
        el.innerText = origText;
        el.setAttribute('style', el.dataset.origStyle || '');
        el.style.cursor = 'pointer';
      } else {
        el.innerText = '✗ 실패';
        el.style.background = '#dc2626';
        el.style.borderColor = '#dc2626';
        console.error('SV push failed:', d);
        alert('Telegram push 실패\\n\\n' + (d.error || '') + '\\n' + (d.stderr || ''));
        setTimeout(() => {
          el.innerText = origText;
          el.setAttribute('style', el.dataset.origStyle || '');
          el.style.cursor = 'pointer';
        }, 4000);
      }
    })
    .catch(e => {
      el.dataset.busy = '';
      el.innerText = '✗ 네트워크 오류';
      el.style.background = '#dc2626';
      el.style.borderColor = '#dc2626';
      console.error('SV push network error:', e);
      setTimeout(() => {
        el.innerText = origText;
        el.setAttribute('style', el.dataset.origStyle || '');
        el.style.cursor = 'pointer';
      }, 4000);
    });
}
'''


def patch(p: Path) -> bool:
    if not p.exists():
        print(f"SKIP — {p} not found")
        return False
    src = p.read_text()
    # Find the script.string = """ ... """ block in
    # _wire_send_to_telegram_button. Replace JS body entirely.
    m = re.search(
        r'(script\.string\s*=\s*""")[^"]*?(""")',
        src,
        re.DOTALL,
    )
    if not m:
        # Try with different quote style
        m = re.search(
            r"(script\.string\s*=\s*r?'''.*?''')",
            src,
            re.DOTALL,
        )
    if not m:
        print(f"SKIP {p} — script.string assignment not found")
        return False
    new_src = src[:m.start(1)] + 'script.string = """' + NEW_JS + '"""' + src[m.end(2):]
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print(f"SKIP {p} — SYNTAX ERROR after patch: {e}")
        return False
    bak = p.with_suffix(p.suffix + f".bak.{int(time.time())}")
    bak.write_text(src)
    print(f"backup: {bak}")
    p.write_text(new_src)
    print(f"patched: {p}")
    return True


did = False
for p in (LIVE_GEN, STOCK_GEN):
    if patch(p):
        did = True

if did:
    print()
    print("Next:")
    print("  cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -5")
    print("  Then hard-refresh dashboard (Ctrl+Shift+R) + click 'Send to Telegram'.")
else:
    print()
    print("No changes.")
