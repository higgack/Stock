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

# Drop section 12 (Due Diligence Questions) — too verbose for Telegram
# push per user 2026-05-21. Keep on dashboard, skip in push only.
if report_md:
    report_md = re.sub(
        r"##\s*12\.[\s\S]*?(?=\n##\s|\Z)", "", report_md
    ).rstrip()


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
    # Markdown list bullet '*   ' → 줄바꿈만 유지, 마커 제거
    txt = re.sub(r"(?m)^\*\s+", "", txt)
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

    # ----- 산업 트렌드 8개 — 실제 구조: grid > [outer-div × 8] -----
    # Each outer-div has 2 inner divs: [0]=title, [1]=body
    ind_card = _find_card_by_title(soup, "산업 트렌드", "🏭")
    if ind_card:
        lines = ["<b>🏭 산업 트렌드 (8개)</b>"]
        # 직접 자식 div 들 (grid container 안의 outer-divs)
        grid = ind_card.find(
            "div", attrs={"style": re.compile(r"grid")},
        )
        items = []
        if grid:
            for outer in grid.find_all("div", recursive=False):
                inners = outer.find_all("div", recursive=False)
                if len(inners) >= 2:
                    items.append((inners[0], inners[1]))
        for title_div, body_div in items[:8]:
            title_raw = _text(title_div)
            body_raw = _html.unescape(_text(body_div))
            m = re.match(
                r"^\s*[•·]?\s*([^()]+?)\s*\(([^)]*)\)\s*$",
                title_raw,
            )
            if m:
                name = m.group(1).strip()
                meta = m.group(2).strip()
            else:
                name = title_raw.lstrip("• ·").strip()
                meta = ""
            sections = [
                s.strip() for s in re.split(r"\s+[—–]\s+|\s+-\s+", body_raw)
                if s.strip()
            ]
            lines.append("")
            hdr = f"▸ <b>{_esc(name)}</b>"
            if meta:
                hdr += f"  <i>{_esc(meta)}</i>"
            lines.append(hdr)
            if sections:
                summary = sections[0]
                bullet_sections = sections[1:]
                if summary:
                    lines.append(_esc(summary))
                for sec in bullet_sections:
                    bullets = [
                        b.strip() for b in re.split(r"\s+·\s+", sec)
                        if b.strip()
                    ]
                    for b in bullets:
                        lines.append(f"  • {_esc(b)}")
        if len(lines) > 1:
            dashboard_parts.append("\n".join(lines))

    # ----- Deal Highlights — 1 메시지, 항목별 줄바꿈, 마커 없음 -----
    deal_card = _find_card_by_title(soup, "Deal Highlights", "💼")
    if deal_card:
        lines = ["<b>💼 Deal Highlights</b>", ""]
        # 카드 안의 grid > outer-div > inner divs 구조 동일 추정
        grid = deal_card.find("div", attrs={"style": re.compile(r"grid")})
        items_html = []
        if grid:
            for outer in grid.find_all("div", recursive=False):
                items_html.append(outer)
        if not items_html:
            # h4/h5 boundary fallback
            headers = deal_card.find_all(["h4", "h5"])
            items_html = headers
        if items_html:
            for it in items_html[:5]:
                text = _html.unescape(_text(it))
                # 카드 title noise 필터
                if "Deal Highlights" in text and "dedup" in text:
                    continue
                if not text:
                    continue
                # score 제거
                text = re.sub(r"score\s*[:=]?\s*\d+", "", text, flags=re.I)
                text = text.strip().strip("·-—| ")
                if len(text) > 220:
                    text = text[:200] + "…"
                lines.append(_esc(text))
        else:
            body = _strip_title_prefix(_text(deal_card), "Deal Highlights")
            body = re.sub(r"\([^)]*dedup[^)]*\)", "", body).strip()
            items = re.split(r"score\s*\d+", body, flags=re.I)
            for it in items[:5]:
                it = it.strip().strip("·-—| ")
                if it and "dedup" not in it:
                    if len(it) > 220:
                        it = it[:200] + "…"
                    lines.append(_esc(it))
        if len(lines) > 2:
            dashboard_parts.append("\n".join(lines))

    # ----- Comments — 1 메시지, 4개 코멘트 줄바꿈으로 구분 -----
    comment_cards = [
        sec for sec in soup.find_all("section")
        if "comment-card" in " ".join(sec.get("class") or [])
    ]
    if comment_cards:
        lines = ["<b>💬 전문가 코멘트</b>"]
        for c in comment_cards[:4]:
            title_n = c.select_one(".panel-title, h3, h4")
            title = _text(title_n) or "(unknown)"
            body = _strip_title_prefix(_text(c), title)
            if not body:
                continue
            if len(body) > 400:
                body = body[:380] + "…"
            lines.append("")
            lines.append(f"▸ <b>{_esc(title)}</b>")
            lines.append(_esc(body))
        if len(lines) > 1:
            dashboard_parts.append("\n".join(lines))

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

    # ----- 국내외 공통 시그널 — 항목별 줄바꿈 (마커 없음) -----
    common = _find_card_by_title(soup, "공통 시그널")
    if common:
        items = [_text(li) for li in common.find_all("li") if _text(li)]
        if not items:
            body = _strip_title_prefix(_text(common), "국내외 공통 시그널")
            parts = [
                s.strip() for s in re.split(r"\s+(?=글로벌|지정학|외국인|국내|미국|중국|반도체|환율|금리)\s*", body)
                if s.strip()
            ]
            if len(parts) < 2:
                parts = [s.strip() for s in re.split(r"\s+—\s+|\s+•\s+", body) if s.strip()]
            items = parts
        if items:
            lines = ["<b>🔄 국내외 공통 시그널</b>", ""]
            for it in items[:6]:
                if len(it) > 400:
                    it = it[:380] + "…"
                lines.append(_esc(it))
                lines.append("")  # 빈 줄로 항목 구분
            dashboard_parts.append("\n".join(lines).rstrip())


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
