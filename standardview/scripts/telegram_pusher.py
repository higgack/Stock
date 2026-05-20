#!/usr/bin/env python3
"""Push today's Standard View daily brief to Telegram (v3, 2026-05-21).

v3 changes vs v2:
  • HTML attachment REMOVED — mobile UX makes HTML files hard to read.
  • Dashboard cards (산업 트렌드 8개 / Deal Highlights / Comments 4개
    / MMI Top 5 / SV 비용 카드) parsed from latest.html + sent as
    structured TEXT messages — same format vocabulary as the existing
    brief push (bullets, bold section headers, HTML <a> links).
  • News headlines keep clickable URLs (from sqlite cache).

Data sources:
  1. sqlite macro_news_cache — brief report + article URLs
  2. .md file — header (macro 동향 indicators)
  3. latest.html (BeautifulSoup) — dashboard cards (industry, deals,
     comments, MMI, cost)

Bot token: STANDARDVIEW_TELEGRAM_TOKEN
Chat IDs:  STANDARDVIEW_CHANNEL_CHAT_IDS or CHANNEL_CHAT_IDS
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
from bs4 import BeautifulSoup
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
    if not s:
        return s
    return re.sub(re.escape(TOKEN), "***", s)


REPORTS  = ROOT / "reports" / "generated"
DB_PATH  = Path.home() / "standardview" / "data" / "standard_view.db"
TG_LIMIT = 4096
CHUNK_TARGET = 3800

TEXT_ONLY = "--text-only" in sys.argv  # kept for back-compat, no effect now
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
TARGET = _args[0] if _args else date.today().isoformat()

md_path   = REPORTS / f"{TARGET}.md"
html_path = REPORTS / f"{TARGET}.html"

if not html_path.exists() and not md_path.exists():
    sys.exit(f"no report files for {TARGET} in {REPORTS}")

API = f"https://api.telegram.org/bot{TOKEN}"


# ==================================================================
# 1. Brief (from sqlite, with article URLs)
# ==================================================================
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
    print(f"sqlite load: {_safe(str(exc))}")

if not report_md and md_path.exists():
    report_md = md_path.read_text()


# ==================================================================
# 2. Header (macro indicators) from .md file
# ==================================================================
header_html = ""
if md_path.exists():
    raw = md_path.read_text()
    end = len(raw)
    for marker in ("🔹 매크로 브리프", "🔹 <b>매크로 브리프</b>",
                   "## 1.", "## 1 "):
        idx = raw.find(marker)
        if idx > 0 and idx < end:
            end = idx
    header_html = raw[:end].rstrip()


# ==================================================================
# 3. Markdown → Telegram HTML conversion
# ==================================================================
def md_to_tg_html(md: str) -> str:
    if not md:
        return ""
    txt = _html.unescape(md)
    txt = _html.escape(txt, quote=False)
    txt = re.sub(r"(?m)^##\s*(\d+\.[^\n]+)$", r"<b>\1</b>", txt)
    txt = re.sub(r"(?m)^#\s+([^\n]+)$",       r"<b>\1</b>", txt)
    txt = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", txt)
    txt = re.sub(r"(?m)^\*\s+", "• ", txt)
    return txt


report_html = md_to_tg_html(report_md)


# ==================================================================
# 4. Chunker (split at section boundaries, ≤ CHUNK_TARGET)
# ==================================================================
def chunk_html(text: str, max_len: int = CHUNK_TARGET) -> list:
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"(<b>\d+\.[^<]+</b>)", text)
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


# ==================================================================
# 5. News block (clickable links)
# ==================================================================
def news_block(label: str, articles: list, top_n: int = 8) -> str:
    if not articles:
        return ""
    lines = [f"<b>🔹 {label}</b>"]
    seen = set()
    count = 0
    for a in articles:
        title = (a.get("title") or "").strip()
        url   = (a.get("url") or a.get("source_url") or "").strip()
        if not title:
            continue
        key = re.sub(r"\s+", " ", title)[:60]
        if key in seen:
            continue
        seen.add(key)
        t_clean = _html.escape(_html.unescape(title), quote=False)
        if url:
            u_esc = _html.escape(url, quote=True)
            lines.append(f"• <a href=\"{u_esc}\">{t_clean}</a>")
        else:
            lines.append(f"• {t_clean}")
        count += 1
        if count >= top_n:
            break
    return "\n".join(lines)


news_parts = []
ko = news_block("주요 한국 헤드라인", ko_articles)
en = news_block("주요 글로벌 헤드라인", en_articles)
if ko: news_parts.append(ko)
if en: news_parts.append(en)
news_html_msg = "\n\n".join(news_parts)


# ==================================================================
# 6. Dashboard cards from latest.html (산업/Deal/Comments/MMI/Cost)
# ==================================================================
def _text(node) -> str:
    """Whitespace-collapsed text extraction with unescape."""
    if node is None:
        return ""
    raw = node.get_text(separator=" ", strip=True)
    return _html.unescape(raw)


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


dashboard_parts: list = []

if html_path.exists():
    soup = BeautifulSoup(html_path.read_text(), "html.parser")

    # ----- 산업 트렌드 8개 -----
    industries = []
    for sec in soup.find_all("section"):
        cls = " ".join(sec.get("class") or [])
        if "industry-trend" in cls.lower() or "industry-card" in cls.lower():
            industries.append(sec)
    # Fallback: search by h3 title nearby '산업 트렌드' container
    if not industries:
        container = None
        for h in soup.find_all(["h2", "h3"]):
            if "산업 트렌드" in (h.get_text() or ""):
                container = h.find_parent("section")
                break
        if container:
            industries = container.find_all("section") or [container]
    if industries:
        lines = ["<b>🏭 산업 트렌드 (8개)</b>"]
        for sec in industries[:8]:
            title_node = sec.select_one(".panel-title, h3, .industry-title, h4")
            title = _text(title_node) or "(이름 미상)"
            # Summary text (first paragraph or first 200 chars of body)
            body_text = _text(sec)
            # Trim title from body if leading
            if body_text.startswith(title):
                body_text = body_text[len(title):].strip()
            # Cap body to ~200 chars
            if len(body_text) > 220:
                body_text = body_text[:200] + "…"
            lines.append(f"\n<b>{_esc(title)}</b>")
            if body_text:
                lines.append(_esc(body_text))
        dashboard_parts.append("\n".join(lines))

    # ----- Deal Highlights -----
    deals = None
    for sec in soup.find_all("section"):
        h = sec.select_one(".panel-title, h3, h2")
        if h and "Deal Highlights" in _text(h):
            deals = sec
            break
    if deals:
        lines = ["<b>💼 Deal Highlights</b>"]
        # Each deal item is usually in a card div / article with category
        # badge + title + score. Try multiple selectors.
        items = (deals.select(".deal-item, article, .deal-card")
                 or [c for c in deals.find_all("div")
                     if c.get("class") and "deal" in " ".join(c.get("class")).lower()])
        if not items:
            # Fallback: split by h4/h5 within
            items = deals.find_all(["h4", "h5"]) or []
        for item in items[:5]:
            title_n = (
                item.select_one(".deal-title, h4, h5, .title")
                or item
            )
            title = _text(title_n)
            badge = _text(item.select_one(".badge, .category, .tag"))
            score = _text(item.select_one(".score, .deal-score, .relevance"))
            if not title:
                continue
            prefix = f"[{badge}] " if badge else ""
            suffix = f" ({score})" if score else ""
            lines.append(f"• {_esc(prefix + title + suffix)}")
        if len(lines) > 1:
            dashboard_parts.append("\n".join(lines))

    # ----- Comments 4개 -----
    comments = None
    for sec in soup.find_all("section"):
        h = sec.select_one(".panel-title, h3, h2")
        if h and ("Comment" in _text(h) or "코멘트" in _text(h)
                  or "Expert" in _text(h)):
            comments = sec
            break
    if comments:
        lines = ["<b>💬 전문가 코멘트</b>"]
        items = comments.select(".comment, .comment-card, article")
        if not items:
            items = comments.find_all("div", class_=re.compile(r"comment", re.I))
        for item in items[:4]:
            author = _text(
                item.select_one(".author, .comment-author, .name, h4")
            )
            body = _text(
                item.select_one(".body, .comment-body, p")
                or item
            )
            if body.startswith(author):
                body = body[len(author):].strip()
            if not body:
                continue
            if len(body) > 280:
                body = body[:260] + "…"
            head = f"<b>{_esc(author)}</b>" if author else "•"
            lines.append(f"\n{head}\n{_esc(body)}")
        if len(lines) > 1:
            dashboard_parts.append("\n".join(lines))

    # ----- MMI Top 5 -----
    mmi = None
    for sec in soup.find_all("section"):
        h = sec.select_one(".panel-title, h3, h2")
        if h and ("MMI" in _text(h) or "Market Mover" in _text(h)):
            mmi = sec
            break
    if mmi:
        lines = ["<b>📈 MMI Top 5</b>"]
        rows = mmi.select("tr, .mmi-row, li")[:6]
        # Skip header row if any
        for r in rows:
            txt = _text(r)
            if not txt or txt.lower().startswith("name") or txt.lower().startswith("rank"):
                continue
            if len(txt) > 200:
                txt = txt[:180] + "…"
            lines.append(f"• {_esc(txt)}")
            if len(lines) >= 6:  # header + 5
                break
        if len(lines) > 1:
            dashboard_parts.append("\n".join(lines))

    # ----- Sentiment gauge -----
    gauge = None
    for sec in soup.find_all("section"):
        h = sec.select_one(".panel-title, h3, h2")
        if h and ("센티먼트" in _text(h) or "Sentiment" in _text(h)):
            gauge = sec
            break
    if gauge:
        score_n = gauge.select_one(".score, .gauge-score, .value")
        label_n = gauge.select_one(".label, .gauge-label, .mood")
        score = _text(score_n)
        label = _text(label_n)
        body = _text(gauge)
        # Try to extract '59 Neutral+' style from full text if selectors missed
        if not score:
            m = re.search(r"(\d{1,3})\s+(Neutral\+?|Greed|Fear|Extreme [A-Za-z]+)", body)
            if m:
                score, label = m.group(1), m.group(2)
        if score:
            line = f"<b>📊 시장 센티먼트</b>: {_esc(score)}"
            if label:
                line += f" · {_esc(label)}"
            dashboard_parts.append(line)


# ==================================================================
# 7. Assemble + send (skip HTML attachment)
# ==================================================================
messages = []
if header_html:
    messages.append(header_html)
messages.extend(report_chunks)
messages.extend(dashboard_parts)
if news_html_msg:
    messages.extend(chunk_html(news_html_msg))

# Enforce TG_LIMIT (final safety)
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
    sys.exit("nothing to send")


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
                print(
                    f"sendMessage [{i+1}/{len(final_messages)}] HTML →"
                    f" {r.status_code} {_safe(r.text)[:240]}"
                )
                # Fallback: plain text
                params.pop("parse_mode", None)
                params["text"] = re.sub(
                    r'<a [^>]*>([^<]*)</a>', r'\1', params["text"]
                )
                params["text"] = re.sub(r'<[^>]+>', '', params["text"])
                r = httpx.post(
                    f"{API}/sendMessage", json=params, timeout=20,
                )
            r.raise_for_status()
        print(f"OK → {chat}  ({len(final_messages)} messages, no HTML attach)")
    except Exception as e:
        fail += 1
        print(f"FAIL {chat}: {_safe(str(e))}")

sys.exit(1 if fail else 0)
