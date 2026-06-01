#!/usr/bin/env python3
"""Push Friday weekly Standard View brief to Telegram.

Sections pushed:
  1. 주간 매크로 요약 — Gemini synthesis of Mon-Fri daily .md briefs
  2. NOAH 워치리스트 성과 — this week's memory log entries (pending + resolved)
  3. 주간 딜 하이라이트 — top articles from daily briefs aggregated

Runs every Sunday 22:00 KST via standardview-weekly.service.
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



# ── 4. Sector rotation heatmap ───────────────────────────────────────────────

# 전 시장 가용 섹터 ETF 최대 커버 (사용자 정책 2026-06-01 "가능한 한 모든
# 비교 ETF"). NOAH sector_strength_tools.py 의 production-검증 override
# ETF 심볼을 그대로 차용 (NOAH /ticker 가 매주 쓰는 심볼이라 yfinance
# 인식 보장). 시장별 unique ticker 만 dedup. 라벨은 전부 한글 (한자/영문
# 제거 — 사용자 정책). yfinance 5d 히스토리 빈약 ETF 는 _sector_rotation_
# block 이 graceful skip (조용히 제외 — 사용자 정책 2026-06-01).
# US SPDR 11 + KR KODEX 17 + JP TOPIX-17 전체 + TW 4 + CN 16 + HK 4.
_SECTOR_ETFS: list[tuple[str, str, str]] = [
    # ── US: SPDR GICS 11 섹터 완비
    ("XLK",    "IT",          "US"),
    ("XLC",    "통신",        "US"),
    ("XLY",    "임의소비재",  "US"),
    ("XLF",    "금융",        "US"),
    ("XLV",    "헬스케어",    "US"),
    ("XLI",    "산업재",      "US"),
    ("XLE",    "에너지",      "US"),
    ("XLP",    "필수소비재",  "US"),
    ("XLRE",   "리츠",        "US"),
    ("XLU",    "유틸리티",    "US"),
    ("XLB",    "소재",        "US"),
    # ── KR: KODEX 섹터 ETF (sector_strength_tools _KR_INDUSTRY_OVERRIDES)
    ("091160.KS", "반도체",     "KR"),
    ("266370.KS", "IT",         "KR"),
    ("091180.KS", "자동차",     "KR"),
    ("091170.KS", "은행",       "KR"),
    ("140700.KS", "보험",       "KR"),
    ("102970.KS", "증권",       "KR"),
    ("244580.KS", "바이오",     "KR"),
    ("266420.KS", "헬스케어",   "KR"),
    ("117700.KS", "건설",       "KR"),
    ("117680.KS", "철강",       "KR"),
    ("102710.KS", "화학",       "KR"),
    ("305720.KS", "2차전지",    "KR"),
    ("300950.KS", "게임",       "KR"),
    ("266360.KS", "미디어",     "KR"),
    ("140710.KS", "운송",       "KR"),
    ("102960.KS", "기계",       "KR"),
    ("117460.KS", "에너지화학", "KR"),
    # ── JP: NEXT FUNDS TOPIX-17 전체 (1617~1633)
    ("1617.T", "식품",        "JP"),
    ("1618.T", "에너지",      "JP"),
    ("1619.T", "건설·자재",   "JP"),
    ("1620.T", "소재·화학",   "JP"),
    ("1621.T", "제약",        "JP"),
    ("1622.T", "자동차",      "JP"),
    ("1623.T", "철강·비철",   "JP"),
    ("1624.T", "기계",        "JP"),
    ("1625.T", "전기·정밀",   "JP"),
    ("1626.T", "IT·서비스",   "JP"),
    ("1627.T", "전력·가스",   "JP"),
    ("1628.T", "운수·물류",   "JP"),
    ("1629.T", "상사·도매",   "JP"),
    ("1630.T", "소매",        "JP"),
    ("1631.T", "은행",        "JP"),
    ("1632.T", "금융",        "JP"),
    ("1633.T", "부동산",      "JP"),
    # ── TW: Yuanta 섹터 ETF (_TW_INDUSTRY_OVERRIDES)
    ("0050.TW",  "TAIEX 50",   "TW"),
    ("0053.TW",  "전자",       "TW"),
    ("0055.TW",  "금융",       "TW"),
    ("00891.TW", "반도체",     "TW"),
    # ── CN A주: 上海/深圳 섹터 ETF (_CN_A_INDUSTRY_OVERRIDES)
    ("512760.SS", "반도체",    "CN"),
    ("512800.SS", "은행",      "CN"),
    ("512070.SS", "보험증권",  "CN"),
    ("512170.SS", "의료",      "CN"),
    ("512690.SS", "주류",      "CN"),
    ("159928.SZ", "소비",      "CN"),
    ("159805.SZ", "자동차",    "CN"),
    ("159755.SZ", "신에너지",  "CN"),
    ("515790.SS", "광전지",    "CN"),
    ("515980.SS", "통신",      "CN"),
    ("512200.SS", "부동산",    "CN"),
    ("159930.SZ", "에너지",    "CN"),
    ("515210.SS", "강철",      "CN"),
    ("159996.SZ", "가전",      "CN"),
    # ── HK: 항셍 정렬 섹터 ETF (_HK_INDUSTRY_OVERRIDES)
    ("2800.HK",  "항셍 대표",  "HK"),
    ("3033.HK",  "항셍테크",   "HK"),
    ("2828.HK",  "H주 (HSCEI)", "HK"),
    # ── EU: iShares STOXX Europe 600 섹터 ETF (Xetra .DE). 라벨은 yfinance
    # longName 기준 — 2026-06-01 VM 검증으로 18개 sector-distinct 확정
    # (EURO STOXX Banks 30-15 EXX1 은 EXV1 은행 중복이라 제외). STOXX 600
    # 이 미국 SPDR 처럼 섹터별 완비라 EU 가 '전 증시 섹터 동향' 의 큰 조각.
    ("EXV1.DE", "은행",       "EU"),
    ("EXV3.DE", "테크",       "EU"),
    ("EXV4.DE", "헬스케어",   "EU"),
    ("EXV5.DE", "자동차",     "EU"),
    ("EXV6.DE", "기초자원",   "EU"),
    ("EXV7.DE", "화학",       "EU"),
    ("EXH1.DE", "에너지",     "EU"),
    ("EXH2.DE", "금융서비스", "EU"),
    ("EXH3.DE", "음식료",     "EU"),
    ("EXH4.DE", "산업재",     "EU"),
    ("EXH5.DE", "보험",       "EU"),
    ("EXH6.DE", "미디어",     "EU"),
    ("EXH7.DE", "생활용품",   "EU"),
    ("EXH8.DE", "리테일",     "EU"),
    ("EXH9.DE", "유틸리티",   "EU"),
    ("EXI5.DE", "부동산",     "EU"),
    ("EXV8.DE", "건설자재",   "EU"),
    ("EXSA.DE", "STOXX 600",  "EU"),
]


def _sector_rotation_block(week_label: str) -> str:
    """Compute 5-trading-day % return for sector ETFs → Telegram HTML."""
    try:
        import yfinance as yf
    except ImportError:
        return ""

    from collections import defaultdict
    results: list[tuple[float, str, str, str]] = []
    for tkr, label, mkt in _SECTOR_ETFS:
        try:
            hist = yf.Ticker(tkr).history(period="10d", auto_adjust=True)
            if hist.empty or len(hist) < 2:
                continue
            close = hist["Close"]
            ref = float(close.iloc[max(0, len(close) - 6)])
            cur = float(close.iloc[-1])
            if ref == 0:
                continue
            results.append(((cur - ref) / ref * 100, tkr, label, mkt))
        except Exception:
            continue

    if not results:
        return ""

    by_market: dict[str, list] = defaultdict(list)
    for item in results:
        by_market[item[3]].append(item)
    for mkt in by_market:
        by_market[mkt].sort(key=lambda x: x[0], reverse=True)

    lines = [f"<b>📊 섹터 로테이션  {week_label}</b>", ""]
    for mkt in ("US", "EU", "KR", "JP", "TW", "CN", "HK"):
        rows = by_market.get(mkt)
        if not rows:
            continue
        lines.append(f"<b>▸ {mkt}</b>")
        for pct, tkr, label, _ in rows:
            icon = "🟢" if pct > 2 else ("🔼" if pct > 0 else ("🔽" if pct > -2 else "🔴"))
            sign = "+" if pct >= 0 else ""
            lines.append(f"  {icon} {label} ({tkr}): {sign}{pct:.1f}%")
        lines.append("")
    return "\n".join(lines).rstrip()

# ── 5. Deal highlights aggregation ───────────────────────────────────────────

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


# ── 6. Archive HTML renderer ─────────────────────────────────────────────────

_ARCHIVE_CSS = (
    ":root{--bg:#060A14;--surface:rgba(20,28,48,0.8);"
    "--border:rgba(255,255,255,0.08);--text:#E8ECF4;"
    "--text-muted:#94A3B8;--accent:#3B82F6}"
    "body{background:var(--bg);color:var(--text);"
    "font-family:-apple-system,'IBM Plex Sans','Noto Sans KR',sans-serif;"
    "max-width:700px;margin:0 auto;padding:24px 16px}"
    "h1{font-size:20px;margin-bottom:4px}"
    ".sub{color:var(--text-muted);font-size:13px;margin-bottom:24px}"
    ".blk{background:var(--surface);border:1px solid var(--border);"
    "border-radius:8px;padding:16px 20px;margin-bottom:12px;"
    "white-space:pre-wrap;font-size:14px;line-height:1.7}"
    "b{color:#93C5FD}"
    ".nav{margin-bottom:16px}"
    ".nav a{color:var(--accent);text-decoration:none;font-size:13px}"
)


def _to_archive_html(messages: list[str], title: str, subtitle: str) -> str:
    blocks = "\n".join(
        f'<div class="blk">{m.strip()}</div>'
        for m in messages if m.strip()
    )
    return (
        '<!DOCTYPE html>\n<html lang="ko"><head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        f'<title>{_esc(title)}</title>\n'
        f'<style>{_ARCHIVE_CSS}</style>\n'
        '</head><body>\n'
        '<div class="nav"><a href="/dashboard/archive">▸ 아카이브</a></div>\n'
        f'<h1>{_esc(title)}</h1>\n'
        f'<p class="sub">{_esc(subtitle)}</p>\n'
        f'{blocks}\n'
        '</body></html>'
    )


def _save_weekly_archive(
    messages: list[str], sector_block: str, today: "date", week_label: str
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    hhmm = "2200"

    brief_path = REPORTS / f"{today.isoformat()}_{hhmm}_weekly.html"
    try:
        brief_path.write_text(
            _to_archive_html(
                messages,
                title=f"Standard View 주간 브리프  {week_label}",
                subtitle=f"생성: {today.isoformat()} KST",
            ),
            encoding="utf-8",
        )
        print(f"weekly_pusher: archive saved → {brief_path.name}")
    except Exception as e:
        print(f"weekly_pusher: archive write failed: {e}")

    if sector_block:
        sec_path = REPORTS / f"{today.isoformat()}_{hhmm}_sectors.html"
        try:
            sec_path.write_text(
                _to_archive_html(
                    [sector_block],
                    title=f"섹터 로테이션 히트맵  {week_label}",
                    subtitle=(
                        f"생성: {today.isoformat()} KST"
                        "  |  US·EU·KR·JP·TW·CN·HK 5거래일 수익률"
                    ),
                ),
                encoding="utf-8",
            )
            print(f"weekly_pusher: archive saved → {sec_path.name}")
        except Exception as e:
            print(f"weekly_pusher: sectors archive write failed: {e}")


# ── 7. Assemble + push ────────────────────────────────────────────────────────

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

    # ── Sector rotation heatmap (US/KR/JP 섹터 ETF 주간 수익률)
    sector_block = _sector_rotation_block(week_label)
    if sector_block:
        messages.append(sector_block)

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

    # ── Save to dashboard archive (weekly brief + sector heatmap)
    _save_weekly_archive(messages, sector_block, today, week_label)


if __name__ == "__main__":
    main()
