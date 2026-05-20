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
# daily_generator writes timestamped HTML files (e.g. 2026-05-21_0324.html)
# and maintains latest.html as the symlink/copy of the most recent run.
# Use latest.html for dashboard-card extraction (산업 / Deal / Comments
# / Sentiment / Signals) since the un-timestamped {TARGET}.html doesn't
# exist on disk.
html_path = REPORTS / "latest.html"

if not md_path.exists() and not html_path.exists():
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


def _find_card_by_title(soup, *needles):
    """Return the first <section class='card ...'> whose panel-title /
    h3 / h2 contains any of the needles (case-sensitive substring)."""
    for sec in soup.find_all("section"):
        cls = " ".join(sec.get("class") or [])
        if "card" not in cls:
            continue
        h = sec.select_one(".panel-title, h3, h2, h4")
        if not h:
            continue
        t = _text(h)
        for needle in needles:
            if needle in t:
                return sec
    return None


def _strip_title_prefix(body: str, title: str) -> str:
    if title and body.startswith(title):
        return body[len(title):].lstrip(" \n·-—")
    return body


if html_path.exists():
    soup = BeautifulSoup(html_path.read_text(), "html.parser")

    # ----- 산업 트렌드 8개 — 각 산업별로 별도 메시지 -----
    # Body 패턴 per industry:
    #   '{name} (뉴스 KO=N / EN=M) {summary} — {b1} · {b2} · {b3}
    #    — {b1} · {b2} · {b3}'
    # — 로 split: [summary, section1 bullets, section2 bullets]
    # · 로 split section1/2 each into bullet list.
    ind_card = _find_card_by_title(soup, "산업 트렌드", "🏭")
    if ind_card:
        # Header card
        dashboard_parts.append("<b>🏭 산업 트렌드 (8개)</b>")
        headers = ind_card.find_all(["h4", "h5"])
        # If h4/h5 미존재 → 전체 body 를 단일 메시지로
        if headers:
            for h in headers[:8]:
                title_raw = _text(h)
                body_parts = []
                node = h.next_sibling
                while node is not None and (
                    getattr(node, "name", None) not in ("h4", "h5")
                ):
                    if hasattr(node, "get_text"):
                        t = node.get_text(separator=" ", strip=True)
                        if t:
                            body_parts.append(t)
                    elif isinstance(node, str):
                        s = node.strip()
                        if s:
                            body_parts.append(s)
                    node = node.next_sibling
                body = _html.unescape(" ".join(body_parts))
                # 산업명 + metadata 분리
                m = re.match(
                    r"^\s*(?:•\s*)?([^()]+?)\s*\(([^)]*)\)\s*(.*)$",
                    body or title_raw,
                    re.DOTALL,
                )
                if m:
                    name = m.group(1).strip()
                    meta = m.group(2).strip()
                    rest = m.group(3).strip()
                else:
                    name = title_raw.strip("• \n")
                    meta = ""
                    rest = body.strip()
                # rest 를 ' — ' 또는 ' - ' 로 split → [summary, sec1, sec2]
                sections = [
                    s.strip() for s in re.split(r"\s+[—–-]\s+", rest)
                    if s.strip()
                ]
                lines = [f"<b>🏭 {_esc(name)}</b>"]
                if meta:
                    lines.append(f"<i>{_esc(meta)}</i>")
                if sections:
                    summary = sections[0]
                    bullet_sections = sections[1:]
                    if summary:
                        lines.append("")
                        lines.append(_esc(summary))
                    for i, sec in enumerate(bullet_sections):
                        bullets = [
                            b.strip() for b in re.split(r"\s+·\s+|\s+•\s+", sec)
                            if b.strip()
                        ]
                        if not bullets:
                            continue
                        lines.append("")
                        for b in bullets:
                            lines.append(f"• {_esc(b)}")
                dashboard_parts.append("\n".join(lines))
        else:
            body = _strip_title_prefix(_text(ind_card), "🏭 산업 트렌드 (8개 산업)")
            if len(body) > 3500:
                body = body[:3400] + "…"
            dashboard_parts.append(_esc(body))

    # ----- Deal Highlights — 각 deal 별 메시지 -----
    deal_card = _find_card_by_title(soup, "Deal Highlights", "💼")
    if deal_card:
        dashboard_parts.append("<b>💼 Deal Highlights</b>")
        headers = deal_card.find_all(["h4", "h5"])
        if headers:
            for h in headers[:5]:
                title = _text(h)
                # Body 직후 sibling 들에서 텍스트 추출 (다음 h4/h5 까지)
                body_parts = []
                node = h.next_sibling
                while node is not None and (
                    getattr(node, "name", None) not in ("h4", "h5")
                ):
                    if hasattr(node, "get_text"):
                        t = node.get_text(separator=" ", strip=True)
                        if t:
                            body_parts.append(t)
                    elif isinstance(node, str):
                        s = node.strip()
                        if s:
                            body_parts.append(s)
                    node = node.next_sibling
                body = _html.unescape(" ".join(body_parts))
                # score 패턴 추출
                score = ""
                m = re.search(r"score\s*[:=]?\s*(\d+)", body, re.I)
                if m:
                    score = f"score {m.group(1)}"
                    body = re.sub(r"score\s*[:=]?\s*\d+", "", body, flags=re.I)
                body = body.strip().strip("·-—")
                msg = f"<b>💼 {_esc(title)}</b>"
                if score:
                    msg += f"\n<i>{_esc(score)}</i>"
                if body:
                    msg += f"\n\n{_esc(body)}"
                dashboard_parts.append(msg)
        else:
            body = _strip_title_prefix(_text(deal_card), "Deal Highlights")
            if body:
                items = re.split(r"score\s*\d+", body, flags=re.I)
                for it in items[:5]:
                    it = it.strip().strip("·-—")
                    if it:
                        dashboard_parts.append(f"<b>💼 Deal</b>\n{_esc(it[:280])}")

    # ----- Comments — 각 코멘트별 별도 메시지 -----
    comment_cards = [
        sec for sec in soup.find_all("section")
        if "comment-card" in " ".join(sec.get("class") or [])
    ]
    if comment_cards:
        dashboard_parts.append("<b>💬 전문가 코멘트</b>")
        for c in comment_cards[:4]:
            title_n = c.select_one(".panel-title, h3, h4")
            title = _text(title_n) or "(unknown)"
            body = _strip_title_prefix(_text(c), title)
            if not body:
                continue
            if len(body) > 400:
                body = body[:380] + "…"
            dashboard_parts.append(f"<b>💬 {_esc(title)}</b>\n\n{_esc(body)}")

    # ----- 시장 센티먼트 (gauge-card) -----
    gauge = None
    for sec in soup.find_all("section"):
        if "gauge-card" in " ".join(sec.get("class") or []):
            gauge = sec
            break
    if gauge is None:
        gauge = _find_card_by_title(soup, "센티먼트", "Sentiment")
    if gauge:
        body = _text(gauge)
        score = ""
        label = ""
        # Match '59 Neutral+' / '47 Fear' / '88 Extreme Greed' etc.
        m = re.search(
            r"(\d{1,3})\s+(Extreme [A-Za-z]+|Neutral\+?|Greed|Fear|Optimism|Caution)",
            body,
        )
        if m:
            score, label = m.group(1), m.group(2)
        else:
            m2 = re.search(r"\b(\d{1,3})\b", body)
            if m2:
                score = m2.group(1)
        if score:
            line = f"<b>📊 시장 센티먼트</b>: {_esc(score)}"
            if label:
                line += f" · {_esc(label)}"
            dashboard_parts.append(line)

    # ----- 국내외 공통 시그널 (small card with title) -----
    common = _find_card_by_title(soup, "공통 시그널")
    if common:
        body = _strip_title_prefix(_text(common), "국내외 공통 시그널")
        if body:
            if len(body) > 600:
                body = body[:580] + "…"
            dashboard_parts.append(
                f"<b>🔄 국내외 공통 시그널</b>\n{_esc(body)}"
            )


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
