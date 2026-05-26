#!/usr/bin/env python3
"""Read-only diagnostic — confirm Gemini cache behavior + bot/SV cost split.

Run on the bot host. Three independent sections; all are safe to re-run.

  python3 cache_cost_diagnostic.py            # read-only (no API calls)
  python3 cache_cost_diagnostic.py --live-probe   # + ~2 tiny Gemini calls

WHY THIS EXISTS
  The decision-tier Gemini context cache is created for model
  "gemini-2.5-pro" (bot/gemini_cache_manager.py), but the 4 analysts run
  on "gemini-2.5-flash" and bind that SAME cache. Gemini explicit caches
  are model-specific, so the analyst binding is suspected to be a silent
  no-op (no error, no savings). usage.jsonl cannot answer this — its
  llm_call records log full prompt_tokens with no cached-token field, and
  cost_usd is computed at the full (un-cached) rate. Only the live probe,
  which reads cached_content_token_count off a real API response, settles
  it. The read-only sections answer the separate "is SV or the bot the
  bigger cost?" question.

NOTHING is written or deleted by this script except a single temporary
Gemini cache that the live probe creates and immediately removes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

USD_KRW = 1330.0

# Correct Gemini 2.5 pricing (USD per 1M tokens, in/out). Mirrors
# bot/usage_tracker.py _PRICING — used to RECOMPUTE the SV log, which
# stores cost with a stale 0.075/0.30 (gemini-1.5) constant.
PRICE = {
    "gemini-2.5-flash":      (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro":        (1.25, 10.00),
}


def _bar(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _month(ts) -> str:
    """Accept epoch float (bot log) or ISO/explicit month (SV log)."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts).strftime("%Y-%m")
    if isinstance(ts, str) and len(ts) >= 7:
        return ts[:7]
    return "unknown"


# ───────────────────────── Section 1: bot cost ──────────────────────────

def section_bot_cost() -> None:
    _bar("1. BOT cost (~/.tradingagents/usage.jsonl)")
    path = Path(os.path.expanduser("~/.tradingagents/usage.jsonl"))
    rows = _read_jsonl(path)
    if not rows:
        print(f"  (no records at {path})")
        return

    cost_by_month_model: dict = defaultdict(lambda: defaultdict(float))
    tok_by_month_model: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    analyses = cache_hits = failures = 0

    for r in rows:
        t = r.get("type")
        if t == "llm_call":
            m = _month(r.get("ts"))
            model = r.get("model", "?")
            cost_by_month_model[m][model] += float(r.get("cost_usd", 0) or 0)
            agg = tok_by_month_model[m][model]
            agg[0] += int(r.get("prompt_tokens", 0) or 0)
            agg[1] += int(r.get("completion_tokens", 0) or 0)
        elif t == "analysis":
            analyses += 1
            if r.get("cache_hit"):
                cache_hits += 1
        elif t == "failure":
            failures += 1

    grand = 0.0
    for m in sorted(cost_by_month_model):
        month_total = sum(cost_by_month_model[m].values())
        grand += month_total
        print(f"\n  [{m}]  USD {month_total:,.2f}  ≈ ₩{month_total * USD_KRW:,.0f}")
        for model in sorted(cost_by_month_model[m], key=lambda k: -cost_by_month_model[m][k]):
            usd = cost_by_month_model[m][model]
            pt, ct = tok_by_month_model[m][model]
            print(f"      {model:<26} USD {usd:7.2f}  (in {pt:>10,} / out {ct:>9,} tok)")

    print(f"\n  TOTAL bot LLM cost: USD {grand:,.2f}  ≈ ₩{grand * USD_KRW:,.0f}")
    print(f"  analyses logged: {analyses}  (disk-cache hits: {cache_hits}, "
          f"fresh: {analyses - cache_hits})   failures: {failures}")
    print("  NOTE: cost_usd is computed at the FULL (un-cached) input rate —")
    print("        real Google bill is lower wherever the Pro cache actually hits.")


# ───────────────────────── Section 2: SV cost ───────────────────────────

def section_sv_cost() -> None:
    _bar("2. STANDARD VIEW cost (~/standardview/sv_usage.jsonl)")
    path = Path(os.path.expanduser("~/standardview/sv_usage.jsonl"))
    rows = _read_jsonl(path)
    if not rows:
        print(f"  (no records at {path})")
        return

    in_rate, out_rate = PRICE["gemini-2.5-flash"]
    logged_by_month: dict = defaultdict(float)
    corrected_by_month: dict = defaultdict(float)
    calls_by_month: dict = defaultdict(int)

    for r in rows:
        m = r.get("month") or _month(r.get("date") or r.get("ts"))
        pt = int(r.get("prompt_tok", 0) or 0)
        ot = int(r.get("output_tok", 0) or 0)
        logged_by_month[m] += float(r.get("cost_krw", 0) or 0)
        corrected_krw = (pt * in_rate + ot * out_rate) / 1e6 * USD_KRW
        corrected_by_month[m] += corrected_krw
        calls_by_month[m] += 1

    log_tot = corr_tot = 0.0
    for m in sorted(corrected_by_month):
        log_tot += logged_by_month[m]
        corr_tot += corrected_by_month[m]
        print(f"  [{m}]  logged ₩{logged_by_month[m]:>8,.0f}   "
              f"corrected ₩{corrected_by_month[m]:>9,.0f}   "
              f"calls {calls_by_month[m]:>4}")
    print(f"\n  TOTAL  logged ₩{log_tot:,.0f}   corrected ₩{corr_tot:,.0f}")
    print("  NOTE: 'logged' uses the stale 0.075/0.30 constant in main.py:388.")
    print("        'corrected' uses real gemini-2.5-flash 0.30/2.50 pricing.")
    print(f"  → a 50% Batch-API discount would save ≈ ₩{corr_tot * 0.5:,.0f} over this window.")


# ──────────────────── Section 3: cache log scan ─────────────────────────

def _journal(unit: str, since: str = "14 days ago") -> str | None:
    for cmd in (["journalctl", "-u", unit, "--since", since, "--no-pager"],
                ["sudo", "journalctl", "-u", unit, "--since", since, "--no-pager"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                return out.stdout
        except Exception:
            continue
    return None


def section_cache_logs() -> None:
    _bar("3. Cache log scan (journalctl -u stock-bot, last 14 days)")
    text = _journal("stock-bot")
    if text is None:
        print("  (journalctl unavailable — run with sudo, or check the unit name)")
        return
    patterns = {
        "cache created":            "gemini cache created",
        "cache skipped (small/key)": "gemini cache skipped",
        "cache creation failed":    "gemini cache creation failed",
        "cache deleted":            "gemini cache deleted",
        "MISMATCH/INVALID hints":   "INVALID_ARGUMENT",
        "CachedContent mentions":   "CachedContent",
    }
    lo = text.lower()
    for label, needle in patterns.items():
        print(f"  {label:<28} {lo.count(needle.lower()):>5}")
    print("\n  If 'cache created' > 0 but analyses still succeed AND there are")
    print("  no INVALID_ARGUMENT/CachedContent errors, the Flash-analyst binding")
    print("  is being SILENTLY IGNORED (no-op) — confirm magnitude via --live-probe.")


# ──────────────────── Section 4: live probe (opt-in) ────────────────────

def _ensure_api_key() -> bool:
    """The interactive shell doesn't export GOOGLE_API_KEY (it lives in the
    bot's .env / systemd env). Read it from the usual .env locations so the
    probe runs with a single command. The key is loaded into this process
    only and never printed."""
    if os.getenv("GOOGLE_API_KEY", "").strip():
        return True
    here = Path(__file__).resolve().parent
    for env_path in (here / ".env",
                     Path(os.path.expanduser("~/stock/.env")),
                     Path(os.path.expanduser("~/.env"))):
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):]
                if line.startswith("GOOGLE_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["GOOGLE_API_KEY"] = val
                        print(f"  (loaded GOOGLE_API_KEY from {env_path})")
                        return True
        except Exception:
            continue
    return False


def section_live_probe() -> None:
    _bar("4. LIVE PROBE — does a Pro cache actually bind to Flash? (~2 API calls)")
    if not _ensure_api_key():
        print("  GOOGLE_API_KEY not set and not found in ~/stock/.env — cannot probe.")
        return

    # Build a >4096-token filler so cache creation clears Gemini's floor.
    filler = ("아래는 캐시 검증용 더미 instrument_context 입니다. "
              "This is filler text used only to exceed the Gemini explicit "
              "cache minimum token threshold. ") * 400  # ~24K chars

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from bot.gemini_cache_manager import GeminiContextCache
    except Exception as exc:
        print(f"  could not import bot.gemini_cache_manager: {exc}")
        return

    cache = GeminiContextCache.create_for_analysis("PROBE", filler, model="gemini-2.5-pro")
    if cache is None:
        print("  cache creation returned None (see logged reason) — cannot probe.")
        return
    print(f"  created Pro cache: {cache.cache_name}")

    # Probe via the google-genai SDK directly (already importable — the cache
    # was just created with it). The bot's analyst path uses langchain's
    # .bind(cached_content=...), which forwards this SAME cached_content to the
    # API, so the API-level result here reflects the bot's behavior. Reuse the
    # cache's authenticated client.
    try:
        from google.genai import types as gt
        client = cache._client

        def _probe(model: str) -> None:
            print(f"\n  -- referencing this Pro cache from {model} generate_content --")
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents="Reply with the single word OK.",
                    config=gt.GenerateContentConfig(cached_content=cache.cache_name),
                )
                um = getattr(resp, "usage_metadata", None)
                prompt_tok = getattr(um, "prompt_token_count", 0) or 0
                cached_tok = getattr(um, "cached_content_token_count", 0) or 0
                print(f"     SUCCEEDED. prompt_token_count={prompt_tok}, "
                      f"cached_content_token_count={cached_tok}")
                if cached_tok:
                    print(f"     ✅ {cached_tok} tokens BILLED AS CACHED → caching WORKS for {model}.")
                else:
                    print(f"     ⚠️ cached_content_token_count=0 → cache had NO effect on {model} (no-op).")
            except Exception as exc:
                print(f"     generate_content RAISED: {type(exc).__name__}: {str(exc)[:240]}")
                print(f"     → referencing a Pro cache from {model} ERRORS (not a silent no-op).")

        _probe("gemini-2.5-pro")    # control — should show cached tokens
        _probe("gemini-2.5-flash")  # the suspected mismatch (analyst path)
    finally:
        cache.delete()
        print("\n  temporary probe cache deleted.")

    print("\n  VERDICT GUIDE:")
    print("   • Pro shows cached tokens, Flash shows 0 → analyst caching is a NO-OP;")
    print("     decision-tier caching works. (Lever 5: give analysts a Flash cache.)")
    print("   • Flash RAISES → the analyst binding is actively dangerous; must guard.")
    print("   • Both show cached tokens → caching already works everywhere (rare).")


def main() -> int:
    print("Gemini cache + cost diagnostic — READ ONLY (except opt-in live probe).")
    section_bot_cost()
    section_sv_cost()
    section_cache_logs()
    if "--live-probe" in sys.argv:
        section_live_probe()
    else:
        _bar("4. LIVE PROBE — skipped")
        print("  Re-run with  --live-probe  to make ~2 tiny Gemini calls (<₩15)")
        print("  that definitively confirm whether the Flash-analyst cache binding works.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
