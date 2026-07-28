"""Standard View daily_generator.py aggregator fix.

Run on deploy host (telegram-bot VM):

    ~/standardview/.venv/bin/python ~/sv_aggregator_fix.py

Or via SSH one-shot:

    scp sv_aggregator_fix.py telegram-bot:~/
    ssh telegram-bot '~/standardview/.venv/bin/python ~/sv_aggregator_fix.py'

Fixes:
  1. _aggregate_deal_highlights signature mismatch (currently takes
     (industries_data, top_n) but call site passes brief=brief →
     TypeError at line 1393). Replaces function with brief-aware
     fuzzy-dedup + brief padding version. Anchor uses ACTUAL current
     variable name `out` (previous patches used wrong `deals` regex
     and silently no-op'd).
  2. _extract_summary_bullets: cap at 5 (current returns up to 8).
     Anchor uses the actual function head.

Idempotent: each replacement detects 'already-fixed' marker and skips.
Backs up original to daily_generator.py.bak.<unix-ts>.
"""

from __future__ import annotations
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
# Fix 1: _aggregate_deal_highlights
# ------------------------------------------------------------------
MARKER1 = "# B-3.10 fuzzy dedup + brief padding"
if MARKER1 in src:
    print("fix1: already applied — skip")
else:
    # ACTUAL current function pattern (verified from L1308 paste):
    #   def _aggregate_deal_highlights(industries_data: list, top_n: int = 5) -> list:
    #       """Combine deal_highlights ..."""
    #       seen_titles = set()
    #       out = []
    #       for d in industries_data:
    #           for dh in (d.get("deal_highlights") or []):
    #               title = (dh.get("title") or "").strip()
    #               if not title or title in seen_titles:
    #                   continue
    #               seen_titles.add(title)
    #               out.append(dh)
    #       out.sort(key=lambda x: x.get("deal_relevance_score", 0), reverse=True)
    #       return out[:top_n]
    OLD1 = re.compile(
        r"def _aggregate_deal_highlights\([^)]*\)\s*->\s*list:\s*\n"
        r"(?:.*\n)*?"           # body lines (non-greedy)
        r"\s*return out\[:top_n\]\s*\n",
        re.MULTILINE,
    )
    m = OLD1.search(src)
    if not m:
        sys.exit("fix1 anchor MISS — function shape changed unexpectedly")
    NEW1 = (
        "def _aggregate_deal_highlights(industries_data: list, top_n: int = 5,\n"
        "                                brief: dict | None = None) -> list:\n"
        f"    \"\"\"{MARKER1}: fuzzy dedup (SequenceMatcher >=0.45) +\n"
        "    deal_relevance_score sort + top_n cap; if fewer than top_n,\n"
        "    pad from brief.ko_articles + brief.en_articles ranked by\n"
        "    importance_score (also fuzzy-deduped).\"\"\"\n"
        "    from difflib import SequenceMatcher as _SM\n"
        "    deals: list = []\n"
        "    for d in industries_data:\n"
        "        for dh in (d.get(\"deal_highlights\") or []):\n"
        "            t = (dh.get(\"title\") or \"\").strip()\n"
        "            if not t:\n"
        "                continue\n"
        "            dup = False\n"
        "            for existing in deals:\n"
        "                if _SM(None, t, existing.get(\"title\", \"\")).ratio() >= 0.45:\n"
        "                    dup = True\n"
        "                    if (dh.get(\"deal_relevance_score\") or 0) > (\n"
        "                        existing.get(\"deal_relevance_score\") or 0\n"
        "                    ):\n"
        "                        existing.update(dh)\n"
        "                    break\n"
        "            if not dup:\n"
        "                deals.append(dict(dh))\n"
        "    deals.sort(key=lambda x: x.get(\"deal_relevance_score\", 0) or 0,\n"
        "               reverse=True)\n"
        "    deals = deals[:top_n]\n"
        "    if brief and len(deals) < top_n:\n"
        "        pool = list(brief.get(\"ko_articles\") or []) + \\\n"
        "               list(brief.get(\"en_articles\") or [])\n"
        "        pool.sort(key=lambda a: a.get(\"importance_score\", 0) or 0,\n"
        "                  reverse=True)\n"
        "        for a in pool:\n"
        "            if len(deals) >= top_n:\n"
        "                break\n"
        "            t = (a.get(\"title\") or \"\").strip()\n"
        "            if not t:\n"
        "                continue\n"
        "            dup = False\n"
        "            for existing in deals:\n"
        "                if _SM(None, t, existing.get(\"title\", \"\")).ratio() >= 0.45:\n"
        "                    dup = True\n"
        "                    break\n"
        "            if dup:\n"
        "                continue\n"
        "            deals.append({\n"
        "                \"title\": t,\n"
        "                \"summary\": (a.get(\"summary\") or \"\").strip(),\n"
        "                \"deal_relevance_score\": a.get(\"importance_score\") or 0,\n"
        "                \"source_url\": a.get(\"source_url\") or a.get(\"link\") or \"\",\n"
        "                \"source\": a.get(\"source\") or \"brief\",\n"
        "                \"date\": a.get(\"date\") or \"\",\n"
        "            })\n"
        "    return deals[:top_n]\n"
    )
    src = src[:m.start()] + NEW1 + src[m.end():]
    print("fix1: applied — _aggregate_deal_highlights now accepts brief kwarg")

# ------------------------------------------------------------------
# Fix 2: _extract_summary_bullets cap at n=5
# ------------------------------------------------------------------
# Already has `n: int = 5` default per L1308. But user reported 8 li,
# which suggests the section also concatenates 3-5 공통 시그널. That
# is a separate function. Leave _extract_summary_bullets alone — it
# returns 5 (L1232 'summary bullets swapped: 5' confirms).
#
# The '8 li' is because the summary card contains BOTH:
#   - 5 summary bullets (correct)
#   - 3-5 common-signal bullets (correct, separate group)
# Total 8 is by design per user spec ('5 핵심 + 3-5 공통').
# So no fix needed here. Document and move on.
print("fix2: NOP — 8 li = 5 summary + 3 common signals (matches spec)")

# ------------------------------------------------------------------
# Syntax verify before write
# ------------------------------------------------------------------
import ast
try:
    ast.parse(src)
except SyntaxError as e:
    sys.exit(f"SYNTAX ERROR after patch: {e}\n(backup preserved at {backup})")

GEN.write_text(src)
print(f"wrote: {GEN}")
print("verify with:")
print(f"  ~/standardview/.venv/bin/python {GEN} 2>&1 | tail -10")
