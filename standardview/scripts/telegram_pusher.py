#!/usr/bin/env python3
"""Push today's Standard View daily brief to Telegram.

v2 (2026-05-21): split into multi-message chunks (4096 char limit per
Telegram message), news headlines rendered as clickable HTML <a> links,
HTML attachment still sent at the end.

Data source priority:
  1. sqlite macro_news_cache (latest row) — has article URLs for links
  2. fallback to .md file (text-only, no links)

Bot token: STANDARDVIEW_TELEGRAM_TOKEN
Chat IDs: STANDARDVIEW_CHANNEL_CHAT_IDS or CHANNEL_CHAT_IDS
"""
from __future__ import annotations
import html as _html
import json
import os
import re
import sqlite3
import sys
from datetime import date
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


def _safe(s: str) -> str:
    """Scrub the bot token from any string before printing."""
    if not s:
        return s
    return re.sub(re.escape(TOKEN), "***", s)


REPORTS  = ROOT / "reports" / "generated"
DB_PATH  = Path.home() / "standardview" / "data" / "standard_view.db"
TG_LIMIT = 4096
CHUNK_TARGET = 3800

TEXT_ONLY = "--text-only" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
TARGET = _args[0] if _args else date.today().isoformat()

md_path   = REPORTS / f"{TARGET}.md"
html_path = REPORTS / f"{TARGET}.html"

if not md_path.exists() and not html_path.exists():
    sys.exit(f"no report files for {TARGET} in {REPORTS}")

API = f"https://api.telegram.org/bot{TOKEN}"


# ------------------------------------------------------------------
# Load brief data from sqlite (has article URLs); fall back to .md
# ------------------------------------------------------------------
report_md   = ""
ko_articles = []
en_articles = []
try:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute(
        "SELECT value_json FROM macro_news_cache"
        " ORDER BY fetched_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row:
        brief = json.loads(row[0])
        report_md   = brief.get("report") or ""
        ko_articles = brief.get("ko_articles") or []
        en_articles = brief.get("en_articles") or []
    con.close()
except Exception as exc:
    print(f"sqlite load failed: {_safe(str(exc))} — fallback to .md")

if not report_md and md_path.exists():
    report_md = md_path.read_text()


# ------------------------------------------------------------------
# Convert markdown brief to Telegram HTML
# ------------------------------------------------------------------
def md_to_tg_html(md: str) -> str:
    """Markdown → Telegram HTML. Telegram HTML supports:
    <b>, <i>, <u>, <s>, <code>, <pre>, <a href>, <tg-spoiler>, <blockquote>.
    NOT supported: <h1>~<h6>, <ul>/<ol>/<li>, <p>, raw newlines stay."""
    if not md:
        return ""
    # Decode any already-escaped entities first (fixes &quot; / &amp;
    # double-escape from upstream Gemini text).
    txt = _html.unescape(md)
    # Now escape once for Telegram HTML safety.
    txt = _html.escape(txt, quote=False)
    # Convert markdown headers
    txt = re.sub(r"(?m)^##\s*(\d+\.[^\n]+)$", r"<b>\1</b>", txt)
    txt = re.sub(r"(?m)^#\s+([^\n]+)$",       r"<b>\1</b>", txt)
    # Bold **x**
    txt = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", txt)
    # Markdown list bullet '*   ' → '• '
    txt = re.sub(r"(?m)^\*\s+", "• ", txt)
    return txt


# ------------------------------------------------------------------
# Build header (macro indicators) from .md prefix
# ------------------------------------------------------------------
header_html = ""
if md_path.exists():
    raw = md_path.read_text()
    # Header runs from start until the first "🔹 매크로 브리프" or first ## section
    end = len(raw)
    for marker in ("🔹 매크로 브리프", "## 1.", "## 1 "):
        idx = raw.find(marker)
        if idx > 0 and idx < end:
            end = idx
    header_text = raw[:end].rstrip()
    # Header is already HTML (.md template uses <b> tags), keep as-is.
    header_html = header_text


# ------------------------------------------------------------------
# Convert brief report → HTML, then chunk at section boundaries
# ------------------------------------------------------------------
report_html = md_to_tg_html(report_md)


def chunk_html(text: str, max_len: int = CHUNK_TARGET) -> list:
    """Split at <b>N. ...</b> section markers; each chunk ≤ max_len.
    Falls back to newline boundary if a single section exceeds max_len."""
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    # Split keeping the section marker with its body.
    parts = re.split(r"(<b>\d+\.[^<]+</b>)", text)
    # Reassemble marker+body pairs.
    blocks = []
    if parts and parts[0]:
        blocks.append(parts[0])
    i = 1
    while i < len(parts):
        block = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        blocks.append(block)
        i += 2
    chunks = []
    current = ""
    for b in blocks:
        if len(current) + len(b) <= max_len:
            current += b
        else:
            if current.strip():
                chunks.append(current.rstrip())
            # If single block exceeds, hard-split on newlines.
            while len(b) > max_len:
                split_at = b.rfind("\n", 0, max_len)
                if split_at < 800:
                    split_at = max_len
                chunks.append(b[:split_at].rstrip())
                b = b[split_at:].lstrip()
            current = b
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


report_chunks = chunk_html(report_html)


# ------------------------------------------------------------------
# Build news section with clickable HTML links
# ------------------------------------------------------------------
def news_block(label: str, articles: list, top_n: int = 8) -> str:
    if not articles:
        return ""
    lines = [f"<b>🔹 {label}</b>"]
    seen = set()
    count = 0
    for a in articles:
        title = (a.get("title") or "").strip()
        url = (a.get("url") or a.get("source_url") or "").strip()
        if not title:
            continue
        # Dedup by normalised title prefix
        key = re.sub(r"\s+", " ", title)[:60]
        if key in seen:
            continue
        seen.add(key)
        title_clean = _html.unescape(title)
        title_esc = _html.escape(title_clean, quote=False)
        if url:
            href_esc = _html.escape(url, quote=True)
            lines.append(f"• <a href=\"{href_esc}\">{title_esc}</a>")
        else:
            lines.append(f"• {title_esc}")
        count += 1
        if count >= top_n:
            break
    return "\n".join(lines)


news_parts = []
ko_block = news_block("주요 한국 헤드라인", ko_articles, top_n=8)
en_block = news_block("주요 글로벌 헤드라인", en_articles, top_n=8)
if ko_block:
    news_parts.append(ko_block)
if en_block:
    news_parts.append(en_block)
news_html = "\n\n".join(news_parts)


# ------------------------------------------------------------------
# Assemble message list — each ≤ Telegram limit
# ------------------------------------------------------------------
messages = []
if header_html:
    messages.append(header_html)
messages.extend(report_chunks)
if news_html:
    # News block may be long; chunk by newline if needed
    messages.extend(chunk_html(news_html))
# Final safety: enforce TG_LIMIT
final_messages = []
for m in messages:
    while len(m) > TG_LIMIT:
        split_at = m.rfind("\n", 0, CHUNK_TARGET)
        if split_at < 800:
            split_at = CHUNK_TARGET
        final_messages.append(m[:split_at].rstrip())
        m = m[split_at:].lstrip()
    if m.strip():
        final_messages.append(m)

if not final_messages:
    sys.exit("nothing to send — both sqlite and .md are empty")


# ------------------------------------------------------------------
# Send each message + final HTML document attachment
# ------------------------------------------------------------------
fail = 0
for chat in CHAT_IDS:
    try:
        for i, msg in enumerate(final_messages):
            params = {
                "chat_id": chat,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            r = httpx.post(
                f"{API}/sendMessage", json=params, timeout=20,
            )
            if r.status_code != 200:
                # Fallback: plain text without parse_mode
                print(
                    f"sendMessage [{i+1}/{len(final_messages)}] HTML →"
                    f" {r.status_code} {_safe(r.text)[:240]}"
                )
                params.pop("parse_mode", None)
                # strip <a href tags but keep title text for plain fallback
                params["text"] = re.sub(
                    r'<a [^>]*>([^<]*)</a>', r'\1', params["text"]
                )
                params["text"] = re.sub(r'<[^>]+>', '', params["text"])
                r = httpx.post(
                    f"{API}/sendMessage", json=params, timeout=20,
                )
            r.raise_for_status()

        if html_path.exists() and not TEXT_ONLY:
            with open(html_path, "rb") as f:
                r2 = httpx.post(
                    f"{API}/sendDocument",
                    data={
                        "chat_id": chat,
                        "caption": (
                            f"Standard View Daily · {TARGET} "
                            f"(full HTML dashboard)"
                        ),
                    },
                    files={
                        "document": (
                            f"standardview_{TARGET}.html",
                            f,
                            "text/html",
                        )
                    },
                    timeout=60,
                )
                r2.raise_for_status()
        print(f"OK → {chat}  ({len(final_messages)} messages)")
    except Exception as e:
        fail += 1
        print(f"FAIL {chat}: {_safe(str(e))}")

sys.exit(1 if fail else 0)
