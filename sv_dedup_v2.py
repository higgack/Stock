"""Standard View daily_generator.py — v2 patch (dedup + exec-summary).

Run on deploy host:

    ~/standardview/.venv/bin/python ~/sv_dedup_v2.py

Fixes:
  1. Deal Highlights 중복 (BYD audit cycle 2026-05-20 — 금양 상폐 3건):
     SequenceMatcher fuzzy threshold 0.45 → 0.30 (한국어 짧은 헤드라인
     은 단어 순서 + 표현 차이로 ratio 가 낮게 나옴) + 회사명 토큰
     공유 시 강제 dup (제목 첫 4자 또는 한국어 명사 첫 매칭 우선
     2-gram). 점수 차이 무관, 더 높은 score 가 살아남고 나머지 dropped.

  2. Executive Summary 중복 렌더링 (▸ 1. Executive Summary 접힘 +
     그 밖에 같은 5 bullet): brief 의 '1. Executive Summary' section
     이 collapsible <details> 로 한번 + _extract_summary_bullets() 로
     한번 = 같은 내용 2회 inject. Summary card 의 _extract_summary
     bullets() 호출 시 sections priority 를 [2:5] 로 shift — Executive
     Summary 본문이 아닌 다음 4 section (Macro / Domestic / Global /
     Sector) 에서 bullets 뽑음. Collapsible block 은 그대로 유지.

Idempotent: marker 'B-3.10.1' 적용 후 skip. 자동 백업.
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
# Fix 1: Stronger dedup in _aggregate_deal_highlights
# ------------------------------------------------------------------
MARKER1 = "# B-3.10.1 stronger dedup"
if MARKER1 in src:
    print("fix1 (dedup): already applied — skip")
else:
    # The function currently uses ratio >= 0.45. Replace whole function
    # body with a stronger version using:
    #   • SequenceMatcher ratio >= 0.30 (lower bar)
    #   • plus token-overlap rule: if any token of length>=2 from the
    #     shorter title appears in the longer title AND the shorter
    #     title's first 2 tokens are mostly contained — treat as dup.
    #     Catches '금양 거래정지' ↔ '금양 상장폐지 결정' ↔ "금양 KC그린
    #     홀딩스 상장폐지" — all share "금양" as the lead noun.
    OLD1 = re.compile(
        r"def _aggregate_deal_highlights\([^)]*?brief[^)]*\)\s*->\s*list:\s*\n"
        r"(?:.*\n)*?"
        r"    return deals\[:top_n\]\s*\n",
        re.MULTILINE,
    )
    m = OLD1.search(src)
    if not m:
        sys.exit("fix1 anchor MISS — function shape changed unexpectedly")
    NEW1 = (
        "def _aggregate_deal_highlights(industries_data: list, top_n: int = 5,\n"
        "                                brief: dict | None = None) -> list:\n"
        f"    \"\"\"{MARKER1}: SequenceMatcher >=0.30 + lead-token rule\n"
        "    (first non-stopword token of length>=2 shared → dup).\n"
        "    Catches '금양 거래정지' / '금양 상폐 결정' / '금양 KC그린홀딩스\n"
        "    상폐' — all share '금양' as the lead company-name noun.\n"
        "    Higher-score item survives; pads from brief.ko/en_articles\n"
        "    when fewer than top_n.\"\"\"\n"
        "    from difflib import SequenceMatcher as _SM\n"
        "    import re as _re\n"
        "    _STOP = {\"the\", \"and\", \"for\", \"with\", \"이\", \"가\", \"의\", \"는\",\n"
        "             \"을\", \"를\", \"에\", \"에서\", \"및\", \"등\"}\n"
        "    def _lead_token(title: str) -> str:\n"
        "        for t in _re.split(r\"[\\s,\\.·\\u2026\\(\\)\\[\\]'\\\"`~!@#$%^&*\\-_=+:;<>/\\\\|?]+\", (title or \"\").strip()):\n"
        "            t = t.strip()\n"
        "            if len(t) >= 2 and t.lower() not in _STOP:\n"
        "                return t\n"
        "        return \"\"\n"
        "    def _is_dup(a: str, b: str) -> bool:\n"
        "        if not a or not b:\n"
        "            return False\n"
        "        if _SM(None, a, b).ratio() >= 0.30:\n"
        "            return True\n"
        "        la, lb = _lead_token(a), _lead_token(b)\n"
        "        if la and lb and la == lb:\n"
        "            return True\n"
        "        return False\n"
        "    deals: list = []\n"
        "    for d in industries_data:\n"
        "        for dh in (d.get(\"deal_highlights\") or []):\n"
        "            t = (dh.get(\"title\") or \"\").strip()\n"
        "            if not t:\n"
        "                continue\n"
        "            dup = False\n"
        "            for existing in deals:\n"
        "                if _is_dup(t, existing.get(\"title\", \"\")):\n"
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
        "                if _is_dup(t, existing.get(\"title\", \"\")):\n"
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
    print("fix1 (dedup): applied — threshold 0.30 + lead-token rule")

# ------------------------------------------------------------------
# Fix 2: Executive Summary duplicate rendering — shift summary
# bullets to sections [2:5] (skip section 1 which is already shown
# via the collapsible <details> block above).
# ------------------------------------------------------------------
MARKER2 = "# B-3.11.1 skip section 1"
if MARKER2 in src:
    print("fix2 (exec-summary): already applied — skip")
else:
    # Find _extract_summary_bullets; the line we want is:
    #   priority = section_split[1:5] if len(section_split) >= 5 else section_split[1:]
    # Replace with [2:6] so we get sections 2-5 (Macro/Domestic/Global/
    # Sector). Mirror fallback shift.
    OLD2 = re.compile(
        r"    priority = section_split\[1:5\] if len\(section_split\) >= 5 else section_split\[1:\]\s*\n",
    )
    m2 = OLD2.search(src)
    if not m2:
        # Maybe the slice is different already — try wider match
        OLD2b = re.compile(
            r"    priority = section_split\[(\d+):(\d+)\] if len\(section_split\) >= \d+ else section_split\[\d+:\]\s*\n",
        )
        m2 = OLD2b.search(src)
        if not m2:
            print("fix2 anchor MISS — _extract_summary_bullets shape changed; SKIP")
    if m2:
        NEW2 = (
            f"    {MARKER2}: sections 2-5 (skip Executive Summary which is\n"
            "    # already rendered above as collapsible <details>; avoids\n"
            "    # the same 5 bullets appearing twice).\n"
            "    priority = section_split[2:6] if len(section_split) >= 6 else section_split[2:]\n"
        )
        src = src[:m2.start()] + NEW2 + src[m2.end():]
        print("fix2 (exec-summary): applied — bullets now from sections 2-5")

# ------------------------------------------------------------------
# Syntax verify before write
# ------------------------------------------------------------------
try:
    ast.parse(src)
except SyntaxError as e:
    sys.exit(f"SYNTAX ERROR after patch: {e}\n(backup preserved at {backup})")

GEN.write_text(src)
print(f"wrote: {GEN}")
print()
print("verify:")
print(f"  cd ~/standardview && .venv/bin/python scripts/daily_generator.py 2>&1 | tail -10")
