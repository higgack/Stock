#!/usr/bin/env python3
"""Push Friday weekly Standard View brief to Telegram.

Sections pushed:
  1. 주간 매크로 요약 — Gemini synthesis of Mon-Fri daily .md briefs
  2. NOAH 워치리스트 성과 — this week's memory log entries (pending + resolved)
  3. 주간 딜 하이라이트 — top articles from daily briefs aggregated

Runs every Friday 08:00 KST via standardview-weekly.service.
Applies universally: US + KR + JP + TW + CN_A + HK analyses all surface here.
"""
from __future__ import annotations
import html as _html
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(Path.home() / "stock" / ".env", override=False)

TOKEN = os.environ.get("STANDARDVIEW_TELEGRAM_TOKEN", "").strip()
CHAT_RAW = (
    os.environ.get("STANDARDVIEW_CHANNEL_CHAT_IDS", "").strip()
    or os.environ.get("CHANNEL_CHAT_IDS", "").strip()
)
CHAT_IDS = [c.strip() for c in CHAT_RAW.split(",") if c.strip()]

if not TOKEN:
    sys.exit("STANDARDVIEW_TELEGRAM_TOKEN missing in .env")
if not CHAT_IDS:
    sys.exit("CHANNEL_CHAT_IDS missing")

REPORTS = ROOT / "reports" / "generated"
API = f"https://api.telegram.org/bot{TOKEN}"
TG_LIMIT = 4096
CHUNK_TARGET = 3800

# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _send(chat_id: str, text: str) -> None:
    text = text.strip()
    if not text:
        return
    try:
        r = httpx.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"send failed ({chat_id}): {e}")


def _chunk(text: str) -> list[str]:
    if len(text) <= CHUNK_TARGET:
        return [text]
    parts = re.split(r"(<b>[^<]+</b>)", text)
    chunks, cur = [], ""
    for p in parts:
        if len(cur) + len(p) <= CHUNK_TARGET:
            cur += p
        else:
            if cur.strip():
                chunks.append(cur.rstrip())
            cur = p
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks or [text[:CHUNK_TARGET]]


# ── 1. This week's daily .md files ───────────────────────────────────────────

def _this_week_dates() -> list[date]:
    """Mon–Fri of the current week up to today."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [
        monday + timedelta(days=i)
        for i in range(5)
        if (monday + timedelta(days=i)) <= today
    ]


def _load_weekly_mds() -> dict[str, str]:
    """Return {YYYY-MM-DD: md_text} for available daily briefs this week."""
    result = {}
    for d in _this_week_dates():
        p = REPORTS / f"{d.isoformat()}.md"
        if p.exists():
            result[d.isoformat()] = p.read_text(encoding="utf-8")
    return result


# ── 2. NOAH Watchlist — memory log parser ────────────────────────────────────

# Tag format (resolved):  [YYYY-MM-DD | TICKER | RATING | +2.3% | -0.5% | 5d]
# Tag format (pending):   [YYYY-MM-DD | TICKER | RATING | pending]
_TAG_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2})\s*\|\s*([A-Z0-9.^=\-]+)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\]$"
)
_SEP = "<!-- ENTRY_END -->"


def _parse_memory_log() -> list[dict]:
    """Parse trading_memory.md without importing TradingAgents package."""
    default = Path.home() / ".tradingagents" / "memory" / "trading_memory.md"
    ml_path = Path(os.environ.get("TRADINGAGENTS_MEMORY_LOG_PATH", str(default)))
    if not ml_path.exists():
        return []
    text = ml_path.read_text(encoding="utf-8")
    entries = []
    for block in text.split(_SEP):
        block = block.strip()
        if not block:
            continue
        tag_line = block.splitlines()[0].strip()
        m = _TAG_RE.match(tag_line)
        if not m:
            continue
        trade_date = m.group(1)
        ticker = m.group(2)
        rating = m.group(3).strip()
        rest = m.group(4).strip()

        if rest == "pending":
            entry = {
                "date": trade_date, "ticker": ticker, "rating": rating,
                "pending": True, "raw": None, "alpha": None, "holding": None,
            }
        else:
            parts = [p.strip() for p in rest.split("|")]
            entry = {
                "date": trade_date, "ticker": ticker, "rating": rating,
                "pending": False,
                "raw": parts[0] if parts else None,
                "alpha": parts[1] if len(parts) > 1 else None,
                "holding": parts[2] if len(parts) > 2 else None,
            }
        entries.append(entry)
    return entries


def _week_entries(entries: list[dict], lookback_days: int = 7) -> list[dict]:
    """Filter to entries within the last `lookback_days` calendar days."""
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    return [e for e in entries if e["date"] >= cutoff]


# ── 3. Gemini weekly summary ──────────────────────────────────────────────────

def _gemini_weekly_summary(mds: dict[str, str]) -> str:
    """Synthesize a Korean weekly macro summary from daily .md texts.

    Feeds up to 5 × 1500-char excerpts from daily briefs into Gemini Flash.
    Returns empty string on failure (caller falls back to date listing).
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not mds:
        return ""

    combined = ""
    for d in sorted(mds):
        # Strip the header table (indicator rows) — keep narrative sections
        text = mds[d]
        # Find first ## section header and take from there
        sec_m = re.search(r"^##\s+\d+\.", text, re.MULTILINE)
        if sec_m:
            text = text[sec_m.start():]
        combined += f"\n\n=== {d} ===\n{text[:1500]}"
    combined = combined.strip()[:8000]

    prompt = (
        "아래는 이번 주(월~금) 일간 매크로 브리프 요약입니다.\n"
        "이를 바탕으로 주간 요약을 한국어로 작성하세요.\n\n"
        "출력 형식 (Telegram HTML: <b>bold</b>만 허용):\n"
        "<b>📊 주간 매크로 요약</b>\n"
        "• [핵심 변화 1 — 1-2문장]\n"
        "• [핵심 변화 2]\n"
        "• [핵심 변화 3]\n\n"
        "<b>🔮 다음 주 Watchpoint</b>\n"
        "• [주요 관찰 변수 1]\n"
        "• [주요 관찰 변수 2]\n\n"
        "400자 이내. 위 형식만 반환 (다른 설명 X).\n\n"
        f"=== 일간 브리프 ===\n{combined}"
    )
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return (resp.text or "").strip()
    except Exception as e:
        print(f"gemini weekly summary failed: {e}")
        return ""


# ── 4. Deal highlights aggregation ───────────────────────────────────────────

def _weekly_headlines(mds: dict[str, str], top_n: int = 5) -> list[str]:
    """Extract top article titles from daily briefs (simple bullet parsing)."""
    seen: set[str] = set()
    results: list[str] = []

    # Look for "## N. ..." sections in the briefs and pick bullets
    bullet_re = re.compile(r"^\*\s+(.+)$", re.MULTILINE)
    for d in sorted(mds, reverse=True):  # newest first
        text = mds[d]
        for m in bullet_re.finditer(text):
            line = m.group(1).strip()
            line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)  # strip **bold**
            key = re.sub(r"\s+", " ", line)[:60].lower()
            if key in seen or len(line) < 15:
                continue
            seen.add(key)
            results.append(line[:200])
            if len(results) >= top_n:
                return results
    return results


# ── 5. Assemble + push ────────────────────────────────────────────────────────

def main():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    week_label = f"{monday.strftime('%m/%d')} ~ {friday.strftime('%m/%d')}"

    messages: list[str] = []

    # ── Header
    messages.append(
        f"<b>📅 Standard View 주간 브리프</b>  {week_label}\n"
        f"생성: {today.isoformat()} KST"
    )

    # ── Macro weekly summary
    mds = _load_weekly_mds()
    if mds:
        summary = _gemini_weekly_summary(mds)
        if summary:
            messages.append(summary)
        else:
            days_covered = "  /  ".join(sorted(mds))
            messages.append(
                f"<b>📊 일간 브리프 커버</b>\n{days_covered}\n"
                "(주간 AI 요약 생성 실패 — Gemini 확인 필요)"
            )
    else:
        messages.append(
            f"⚠️ 이번 주 일간 브리프 미발견\n"
            f"({REPORTS} 없음)"
        )

    # ── NOAH Watchlist 성과
    entries = _parse_memory_log()
    week_ents = _week_entries(entries, lookback_days=7)
    if week_ents:
        resolved = sorted(
            [e for e in week_ents if not e["pending"]], key=lambda x: x["date"]
        )
        pending = sorted(
            [e for e in week_ents if e["pending"]], key=lambda x: x["date"]
        )

        lines = [f"<b>🎯 NOAH 워치리스트 성과  {week_label}</b>", ""]

        if resolved:
            lines.append("<b>▸ 결과 확인됨 (5거래일)</b>")
            for e in resolved:
                raw = e.get("raw") or "—"
                alpha = e.get("alpha") or "—"
                raw_clean = raw.replace("%", "")
                # Emoji by direction
                try:
                    emoji = "🟢" if float(raw_clean) > 0 else ("🔴" if float(raw_clean) < 0 else "⬜")
                except (ValueError, TypeError):
                    emoji = "⬜"
                rating = _esc(e["rating"])
                lines.append(
                    f"{emoji} <b>{_esc(e['ticker'])}</b> [{e['date']}] {rating} "
                    f"→ 5d: {_esc(raw)} | α: {_esc(alpha)}"
                )

        if pending:
            if resolved:
                lines.append("")
            lines.append("<b>▸ 결과 대기 중 (pending)</b>")
            for e in pending:
                # Days since analysis
                try:
                    delta = (today - date.fromisoformat(e["date"])).days
                    delta_str = f"{delta}일 경과"
                except Exception:
                    delta_str = ""
                lines.append(
                    f"⏳ <b>{_esc(e['ticker'])}</b> [{e['date']}] {_esc(e['rating'])}"
                    + (f" — {delta_str}" if delta_str else "")
                )

        if len(lines) > 2:
            messages.append("\n".join(lines))

    # ── Top headlines from the week (deal highlights supplement)
    headlines = _weekly_headlines(mds) if mds else []
    if headlines:
        lines = [f"<b>📰 주간 주요 이슈  {week_label}</b>", ""]
        for h in headlines:
            lines.append(f"• {_esc(h)}")
            lines.append("")
        messages.append("\n".join(lines).rstrip())

    # ── Send all chunks to all channels
    for msg in messages:
        for chunk in _chunk(msg):
            for chat_id in CHAT_IDS:
                _send(chat_id, chunk)

    print(f"weekly_pusher: sent {len(messages)} message(s) to {len(CHAT_IDS)} channel(s)")


if __name__ == "__main__":
    main()
