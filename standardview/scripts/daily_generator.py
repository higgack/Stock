#!/usr/bin/env python3
"""Generate today's Standard View daily macro brief HTML + MD.

Uses the latest committed HTML in reports/daily/ as a structural template
and swaps section content with fresh data fetched from the local FastAPI
backend (uvicorn on STANDARDVIEW_BACKEND, default 127.0.0.1:8002).

Outputs:
  ~/standardview/reports/daily/{YYYY-MM-DD}.html
  ~/standardview/reports/daily/{YYYY-MM-DD}.md

License: Friend's StanLee5767/standardview HTML structure is used as the
visual template with explicit permission. Daily orchestrator written for
higgack/standardview deploy 2026-05-19.
"""
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(Path.home() / "stock" / ".env", override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("daily_gen")

BACKEND = os.environ.get("STANDARDVIEW_BACKEND", "http://127.0.0.1:8002")
REPORTS = ROOT / "reports" / "generated"  # separate from friend's reports/daily/ archive
REPORTS.mkdir(parents=True, exist_ok=True)

TODAY = date.today().isoformat()
KOREAN_DATE = datetime.now().strftime("%Y년 %m월 %d일 (%a)")
GEN_AT = datetime.now().strftime("%Y-%m-%d %H:%M KST")


_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "base.html"


def _pick_template() -> Path:
    """Fixed scaffold at scripts/templates/base.html — copied from friend's
    richest HTML (2026-05-19). Never overwritten, immune to daily regen."""
    if not _TEMPLATE_PATH.exists():
        sys.exit(
            f"base scaffold missing: {_TEMPLATE_PATH}. "
            f"Run: cp reports/daily/<rich-friend-html> {_TEMPLATE_PATH}"
        )
    return _TEMPLATE_PATH


def _fetch(path: str, method: str = "GET", json_body=None, timeout: int = 180):
    url = f"{BACKEND}{path}"
    log.info("%s %s", method, path)
    try:
        if method == "GET":
            r = httpx.get(url, timeout=timeout)
        else:
            r = httpx.post(url, json=json_body, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("fetch %s failed: %s", path, e)
        return None


_MMI_NEG_KEYWORDS = (
    "하락", "추락", "급락", "폭락", "위기", "우려", "공포", "충격",
    "리스크", "악화", "매파", "긴축", "둔화", "위축", "축소", "탈출",
    "이탈", "유출", "약세", "부진",
    "decline", "fall", "drop", "plunge", "crisis", "fear", "shock",
    "risk", "hawkish", "tighten", "slowdown", "weakness", "selloff",
    "outflow", "rout", "warn",
)
_MMI_POS_KEYWORDS = (
    "상승", "급등", "반등", "회복", "강세", "호조", "긍정", "비둘기",
    "완화", "확대", "인하", "랠리", "낙관", "유입",
    "rally", "rebound", "recover", "gain", "surge", "dovish", "ease",
    "boost", "strong", "optimi", "inflow",
)


_DEFAULT_INDUSTRIES = ["AI", "반도체", "로봇", "바이오", "2차전지", "석유화학", "건설기계", "화장품·식품"]


def _fetch_industry(industry: str, timeout: int = 240) -> dict:
    """B-2 enrichment: POST /api/industry-analysis (global scope —
    bypasses Naver 401 issue, uses NewsAPI+GDELT only). Returns the
    full response dict or {} on failure."""
    try:
        r = httpx.post(
            f"{BACKEND}/api/industry-analysis",
            json={"industry": industry, "text_input": "", "scope": "global"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("industry-analysis fetch failed %s: %s", industry, e)
        return {}


_SPARKLINE_SOURCES = {
    # idx → ('yf' | 'fred', ticker_or_series_id)
    4:  ("yf",   "KRW=X"),
    5:  ("fred", "KORCPIALLMINMEI"),
    9:  ("fred", "FEDFUNDS"),
    10: ("fred", "DGS10"),
    11: ("fred", "CPIAUCSL"),
    12: ("fred", "UNRATE"),
    14: ("yf",   "DX-Y.NYB"),
    15: ("yf",   "^GSPC"),
    16: ("yf",   "^IXIC"),
    17: ("yf",   "^VIX"),
    18: ("yf",   "CL=F"),
    19: ("yf",   "BZ=F"),
    20: ("yf",   "HG=F"),
    21: ("yf",   "GC=F"),
}


def _fetch_sparkline_12mo(source: str, ident: str) -> list:
    """Return 12 monthly close values for the given source/ident."""
    months = 12
    if source == "yf":
        return _fetch_yf_monthly(ident, months)
    elif source == "fred":
        return _fetch_fred_monthly(ident, months)
    return []


def _aggregate_deal_highlights(industries_data: list, top_n: int = 5,
                                brief: dict | None = None) -> list:
    """# B-3.10.1 stronger dedup: SequenceMatcher >=0.30 + lead-token rule
    (first non-stopword token of length>=2 shared → dup).
    Catches '금양 거래정지' / '금양 상폐 결정' / '금양 KC그린홀딩스
    상폐' — all share '금양' as the lead company-name noun.
    Higher-score item survives; pads from brief.ko/en_articles
    when fewer than top_n."""
    from difflib import SequenceMatcher as _SM
    import re as _re
    _STOP = {"the", "and", "for", "with", "이", "가", "의", "는",
             "을", "를", "에", "에서", "및", "등"}
    def _lead_token(title: str) -> str:
        for t in _re.split(r"[\s,\.·\u2026\(\)\[\]'\"`~!@#$%^&*\-_=+:;<>/\\|?]+", (title or "").strip()):
            t = t.strip()
            if len(t) >= 2 and t.lower() not in _STOP:
                return t
        return ""
    def _tokens(s: str) -> set:
        return {
            t.strip() for t in _re.split(
                r"[\s,\.·\u2026\(\)\[\]'\"`~!@#$%^&*\-_=+:;<>/\\|?]+",
                (s or "").strip(),
            )
            if len(t.strip()) >= 2 and t.strip().lower() not in _STOP
        }
    def _is_dup(a: str, b: str) -> bool:  # B-3.10.3 token-set dedup
        if not a or not b:
            return False
        if _SM(None, a, b).ratio() >= 0.30:
            return True
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            return False
        # 짧은 쪽 기준 50% 이상 토큰 공유 → dup
        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(smaller & larger) / max(len(smaller), 1) >= 0.5:
            return True
        return False
    deals: list = []
    for d in industries_data:
        for dh in (d.get("deal_highlights") or []):
            t = (dh.get("title") or "").strip()
            if not t:
                continue
            dup = False
            for existing in deals:
                if _is_dup(t, existing.get("title", "")):
                    dup = True
                    if (dh.get("deal_relevance_score") or 0) > (
                        existing.get("deal_relevance_score") or 0
                    ):
                        existing.update(dh)
                    break
            if not dup:
                deals.append(dict(dh))
    deals.sort(key=lambda x: x.get("deal_relevance_score", 0) or 0,
               reverse=True)
    deals = deals[:top_n]
    if brief and len(deals) < top_n:
        pool = list(brief.get("ko_articles") or []) + \
               list(brief.get("en_articles") or [])
        pool.sort(key=lambda a: a.get("importance_score", 0) or 0,
                  reverse=True)
        for a in pool:
            if len(deals) >= top_n:
                break
            t = (a.get("title") or "").strip()
            if not t:
                continue
            dup = False
            for existing in deals:
                if _is_dup(t, existing.get("title", "")):
                    dup = True
                    break
            if dup:
                continue
            deals.append({
                "title": t,
                "summary": (a.get("summary") or "").strip(),
                "deal_relevance_score": a.get("importance_score") or 0,
                "source_url": a.get("source_url") or a.get("link") or "",
                "source": a.get("source") or "brief",
                "date": a.get("date") or "",
            })
    return deals[:top_n]
def _extract_summary_bullets(report_text: str, n: int = 5) -> list:
    """B-3.11 (2026-05-20): sections 1-4 에서 5 bullets + fuzzy dedup
    (SequenceMatcher 0.5 — 비슷한 내용은 1개만)."""
    import re as _re
    from difflib import SequenceMatcher as _SM
    if not report_text:
        return []
    section_split = _re.split(
        r"^##\s+\d+\.\s+[^\n]+\n",
        report_text,
        flags=_re.MULTILINE,
    )
    # B-3.11.1 skip section 1: sections 2-5 (skip Executive Summary which is
    # already rendered above as collapsible <details>; avoids
    # the same 5 bullets appearing twice).
    priority = section_split[2:6] if len(section_split) >= 6 else section_split[2:]
    bullets = []
    for sec in priority:
        for line in sec.split("\n"):
            line = line.strip()
            if line.startswith("*") or line.startswith("•"):
                cleaned = line.lstrip("*•").lstrip()
                cleaned = _re.sub(r"\*\*([^*]+?)\*\*", r"\1", cleaned)
                cleaned = cleaned.strip()
                if not cleaned or len(cleaned) <= 25:
                    continue
                if "없습니다" in cleaned and len(cleaned) < 80:
                    continue
                # Fuzzy dedup
                is_dup = any(_SM(None, cleaned, b).ratio() >= 0.5 for b in bullets)
                if not is_dup:
                    bullets.append(cleaned)
                    if len(bullets) >= n:
                        return bullets
    return bullets[:n]


def _format_brief_for_telegram(text: str) -> str:
    """Convert Gemini brief MD to Telegram HTML-friendly format.

    Telegram parse_mode='HTML' supports <b>/<i>/<a> but not headers.
    Order matters: escape first, then transform headers, then bullets,
    then bold, then strip stray markdown. Examples (Sony 2026-05-20):
     • `# Macro News Brief` → DROP (we already have our own section
       header right above — redundant)
     • `## 1. Executive Summary` → `<b>1. Executive Summary</b>`
     • `## Heading` → `▸ <b>Heading</b>` (sub-section visual cue)
     • `*   text` / `* text` → `• text` (asterisk bullet → 점)
     • `**bold**` → `<b>bold</b>`
     • dangling single `*` between words → strip
    """
    import re as _re
    if not text:
        return ""
    # 1) Escape HTML special chars first (before we add our own <b> tags)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2) Drop the top-level `# Macro News Brief` (or any single-# H1) —
    #    our containing message already has a 🔹 section header so the
    #    Gemini-emitted H1 is redundant and uses an unsupported # char.
    text = _re.sub(r"^#\s+[^\n]+\n?", "", text, flags=_re.MULTILINE)
    # 3) ## with leading number → <b>1. Heading</b>
    text = _re.sub(r"^##\s+(\d+\.\s*[^\n]+)$", r"<b>\1</b>", text, flags=_re.MULTILINE)
    # 4) ## plain → ▸ <b>Heading</b> (subsection arrow)
    text = _re.sub(r"^##\s+([^\n]+)$", r"▸ <b>\1</b>", text, flags=_re.MULTILINE)
    # 5) Bullet asterisks at line start: '*   text' or '* text' → '• text'
    text = _re.sub(r"^\s*\*\s+", "• ", text, flags=_re.MULTILINE)
    # 6) **bold** → <b>bold</b>
    text = _re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", text)
    # 7) Any remaining lone `*` between text — strip (markdown italic
    #    leftover or unmatched). Conservative: only strip when whitespace
    #    or word-boundary on both sides.
    text = _re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"\1", text)
    # 8) Collapse runs of >=3 blank lines to max 2 (tighten visual spacing)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_noah_today() -> list:
    """B-2.1 (2026-05-20): Read today's NOAH analyses from /api/insights
    (SQLite persistent store, queryable from Standard View SPA) and map
    each Insight back to the rendering-record shape the NOAH section
    expects (ticker / market / verdict / stance_bar / snippet /
    dashboard_url / ts).

    Falls back to legacy JSONL read when the insights API is unreachable
    so the daily section still populates during transient outages."""
    from datetime import date as _d
    today = _d.today().isoformat()
    out = []

    # Primary: /api/insights GET, tag=noah, date_from=today
    try:
        r = httpx.get(
            f"{BACKEND}/api/insights",
            params={"tag": "noah", "date_from": today},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json() or []
        # /api/insights returns list of insight dicts
        if isinstance(data, list):
            for rec in data:
                if not isinstance(rec, dict):
                    continue
                if rec.get("created_at") != today:
                    continue
                # Map Insight schema → rendering shape. verdict is parsed
                # from source_title ("NOAH analysis: {ticker} → {verdict}")
                # since insight has no dedicated verdict column.
                src_title = rec.get("source_title", "") or ""
                verdict = ""
                if "→" in src_title:
                    verdict = src_title.split("→", 1)[1].strip()
                out.append({
                    "ticker": rec.get("company_name", "?"),
                    "market": rec.get("sector", "?"),
                    "verdict": verdict,
                    "stance_bar": rec.get("user_insight", ""),
                    "snippet": rec.get("key_content", ""),
                    "dashboard_url": rec.get("source_url", ""),
                    # ts is date-only from insights; rendering does ts[-8:-3]
                    # which would mangle a YYYY-MM-DD string — leave blank
                    # so the time chip stays empty rather than showing junk.
                    "ts": "",
                })
            log.info("noah_today via /api/insights: %d entries", len(out))
            return out
    except Exception as e:
        log.warning("/api/insights GET failed, falling back to JSONL: %s", e)

    # Fallback: legacy JSONL read (Phase B-2 path)
    import json as _json
    jsonl = Path.home() / "standardview" / "noah_analyses.jsonl"
    if not jsonl.exists():
        return out
    try:
        with jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                if rec.get("date") == today:
                    out.append(rec)
        log.info("noah_today via JSONL fallback: %d entries", len(out))
    except Exception as e:
        log.warning("noah jsonl read failed: %s", e)
    return out


def _fetch_yf_monthly(ticker: str, months: int = 7) -> list:
    """yfinance monthly close, latest N months. Returns list of floats
    or [] on failure. Used for chart datasets."""
    try:
        import yfinance as _yf
        from datetime import date as _d, timedelta as _td
        start = (_d.today() - _td(days=int(months * 31 + 30))).isoformat()
        t = _yf.Ticker(ticker)
        hist = t.history(start=start, interval="1mo")
        if hist is None or hist.empty:
            return []
        closes = hist["Close"].dropna().tolist()[-months:]
        return [round(float(c), 4) for c in closes]
    except Exception as e:
        log.warning("yf monthly fetch failed %s: %s", ticker, e)
        return []


def _fetch_fred_monthly(series_id: str, months: int = 7) -> list:
    """FRED API monthly observations, latest N. Requires FRED_API_KEY."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return []
    try:
        import requests as _rq
        r = _rq.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": months * 2,
            },
            timeout=15,
        )
        r.raise_for_status()
        obs = r.json().get("observations") or []
        vals = []
        for o in obs:
            try:
                v = float(o.get("value"))
                vals.append(v)
            except Exception:
                continue
            if len(vals) >= months:
                break
        return list(reversed(vals))
    except Exception as e:
        log.warning("FRED fetch failed %s: %s", series_id, e)
        return []


def _build_chart_labels(months: int = 7) -> list:
    """['2025-11', '2025-12', ...] for last N months including current."""
    from datetime import date as _d
    today = _d.today()
    labels = []
    y, m = today.year, today.month
    for _ in range(months):
        labels.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(labels))


def _compute_sentiment(snap: dict) -> tuple:
    """A2-3: Sentiment gauge score 0-100 + label + needle angle.

    Simple VIX-based heuristic (fallback friendly): VIX low → greed,
    high → fear. Real CNN Fear/Greed uses 7 indicators; this is v1.
    Returns (score:int 0-100, label:str, css_class:str, angle:float deg).
    """
    indi = (snap or {}).get("indicators") or {}
    vix_data = indi.get("vix") or {}
    vix = vix_data.get("value")
    if not isinstance(vix, (int, float)) or vix <= 0:
        score = 50
    elif vix <= 12:
        score = 90
    elif vix <= 18:
        # 12-18 → 75-60 linear
        score = int(75 - (vix - 12) * (15 / 6))
    elif vix <= 25:
        # 18-25 → 60-40
        score = int(60 - (vix - 18) * (20 / 7))
    elif vix <= 35:
        # 25-35 → 40-20
        score = int(40 - (vix - 25) * (20 / 10))
    else:
        score = max(10, int(20 - (vix - 35) * 0.5))
    score = max(0, min(100, score))
    if score >= 75:
        label, cls = "Greed", "greed"
    elif score >= 55:
        label, cls = "Neutral+", "neutral"
    elif score >= 45:
        label, cls = "Neutral", "neutral"
    elif score >= 25:
        label, cls = "Fear", "fear"
    else:
        label, cls = "Extreme Fear", "fear"
    # Needle: 0 → -90°, 100 → +90° (180° sweep)
    angle = -90.0 + (score / 100.0) * 180.0
    return score, label, cls, angle


def _generate_comment_cards(snap: dict, brief: dict) -> dict:
    """A2-2: Single Gemini call returning 4 thematic insights.

    Output dict keys: market / policy / sector / risk
    Each value: {"title": str, "body": str}
    Returns {} on failure — caller leaves card content unchanged.
    """
    import json as _json
    import re as _re
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return {}
    snap_text = _json.dumps(
        {
            k: {
                "value": (v or {}).get("value"),
                "unit": (v or {}).get("unit"),
            }
            for k, v in (snap.get("indicators") or {}).items()
        },
        ensure_ascii=False,
    )[:2500]
    brief_text = ((brief or {}).get("report") or "")[:3000]
    prompt = f"""아래 매크로 데이터 + 매크로 브리프 기반으로 4개 카드의 한국어 1-2문장 코멘트를 작성하세요. JSON 형식만 반환 (markdown fence X, 다른 설명 X):

{{"market": {{"title": "Market", "body": "..."}}, "policy": {{"title": "Policy", "body": "..."}}, "sector": {{"title": "Sector", "body": "..."}}, "risk": {{"title": "Risk", "body": "..."}}}}

각 카드 의도:
- market: 단기 (5거래일) 시장 핵심 동향
- policy: 중앙은행/정부 정책 변수 핵심
- sector: 가장 영향 받는 섹터/산업
- risk: 가장 큰 단기 리스크 1개

매크로 데이터:
{{snap}}

매크로 브리프:
{{brief}}
""".replace("{{snap}}", snap_text).replace("{{brief}}", brief_text)
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = _re.sub(r"^```(?:json)?\s*", "", text)
            text = _re.sub(r"\s*```$", "", text)
        return _json.loads(text)
    except Exception as e:
        log.warning("comment cards generation failed: %s", e)
        return {}


def _mmi_classify(title: str) -> tuple:
    """(direction_label, badge_class) — 한국어 + 영문 keyword heuristic."""
    t = (title or "").lower()
    neg = sum(1 for k in _MMI_NEG_KEYWORDS if k.lower() in t)
    pos = sum(1 for k in _MMI_POS_KEYWORDS if k.lower() in t)
    if neg > pos:
        return ("부정적", "dir-negative")
    if pos > neg:
        return ("긍정적", "dir-positive")
    return ("중립", "dir-neutral")


_MMI_NEG_KEYWORDS = (
    "하락", "추락", "급락", "폭락", "위기", "우려", "공포", "충격",
    "리스크", "악화", "매파", "긴축", "둔화", "위축", "축소", "탈출",
    "이탈", "유출", "약세", "부진",
    "decline", "fall", "drop", "plunge", "crisis", "fear", "shock",
    "risk", "hawkish", "tighten", "slowdown", "weakness", "selloff",
    "outflow", "rout", "warn",
)
_MMI_POS_KEYWORDS = (
    "상승", "급등", "반등", "회복", "강세", "호조", "긍정", "비둘기",
    "완화", "확대", "인하", "랠리", "낙관", "유입",
    "rally", "rebound", "recover", "gain", "surge", "dovish", "ease",
    "boost", "strong", "optimi", "inflow",
)


def _mmi_classify(title: str) -> tuple:
    """(direction_label, badge_class) — 한국어 + 영문 keyword heuristic."""
    t = (title or "").lower()
    neg = sum(1 for k in _MMI_NEG_KEYWORDS if k.lower() in t)
    pos = sum(1 for k in _MMI_POS_KEYWORDS if k.lower() in t)
    if neg > pos:
        return ("부정적", "dir-negative")
    if pos > neg:
        return ("긍정적", "dir-positive")
    return ("중립", "dir-neutral")


_MMI_NEG_KEYWORDS = (
    "하락", "추락", "급락", "폭락", "위기", "우려", "공포", "충격",
    "리스크", "악화", "매파", "긴축", "둔화", "위축", "축소", "탈출",
    "이탈", "유출", "약세", "부진",
    "decline", "fall", "drop", "plunge", "crisis", "fear", "shock",
    "risk", "hawkish", "tighten", "slowdown", "weakness", "selloff",
    "outflow", "rout", "warn",
)
_MMI_POS_KEYWORDS = (
    "상승", "급등", "반등", "회복", "강세", "호조", "긍정", "비둘기",
    "완화", "확대", "인하", "랠리", "낙관", "유입",
    "rally", "rebound", "recover", "gain", "surge", "dovish", "ease",
    "boost", "strong", "optimi", "inflow",
)


def _mmi_classify(title: str) -> tuple:
    """(direction_label, badge_class) — 한국어 + 영문 keyword heuristic."""
    t = (title or "").lower()
    neg = sum(1 for k in _MMI_NEG_KEYWORDS if k.lower() in t)
    pos = sum(1 for k in _MMI_POS_KEYWORDS if k.lower() in t)
    if neg > pos:
        return ("부정적", "dir-negative")
    if pos > neg:
        return ("긍정적", "dir-positive")
    return ("중립", "dir-neutral")


def _fmt_value(v, unit: str = "") -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.2f} {unit}".strip()
    return f"{v} {unit}".strip()






# sv-telegram-button-postprocess v1
def _wire_send_to_telegram_button(soup):
    """Find the 'Send to Telegram' anchor button (disabled by default in
    friend's template), rewrite as a working button with onclick fetch
    to /api/push-to-telegram, and inject a tiny toast helper script."""
    btn = None
    for a in soup.find_all("a", class_="btn"):
        txt = (a.get_text() or "").strip()
        if "Send to Telegram" in txt or "텔레그램" in txt:
            btn = a
            break
    if btn is None:
        return
    # Strip the aria-disabled marker, add onclick + cursor styling.
    if btn.has_attr("aria-disabled"):
        del btn["aria-disabled"]
    btn["onclick"] = "svPushTelegram(this); return false;"
    btn["href"]    = "#"
    btn["style"]   = (btn.get("style", "") + ";cursor:pointer").lstrip(";")
    # Inject the toast helper once (idempotent — check for existing).
    if not soup.find("script", id="sv-telegram-push"):
        from bs4 import Tag as _Tag
        script = soup.new_tag("script", id="sv-telegram-push")
        script.string = """
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
        alert('Telegram push 실패 · ' + (d.error || '') + ' · ' + (d.stderr || ''));
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
"""
        body = soup.find("body")
        if body is not None:
            body.append(script)

def main():
    base = _pick_template()
    log.info("base template: %s", base)
    soup = BeautifulSoup(base.read_text(), "html.parser")

    # ─── 1. Header date / title ────────────────────────────────────
    if (t := soup.find("title")):
        t.string = f"Standard View — Daily Macro Brief · {KOREAN_DATE}"
    if (sub := soup.find(class_="summary-title")):
        sub.string = f"{TODAY} 핵심 요약"
    if (meta := soup.find(class_="report-date")):
        meta.string = KOREAN_DATE
    if (gen := soup.find(class_="generated-meta")):
        gen.string = f"생성: {GEN_AT}"

    # ─── 1.5 Header date + archive link + Data Source removal ──
    # B-3 follow-up (2026-05-20): 친구 HTML 의 header 의 정적 날짜
    # ("2026년 5월 19일 (화)" 같은) 가 .report-date class 가 아닌
    # bare <span> 안 SVG + text 로 박혀있어 기존 update 실패. string
    # node 패턴 매칭으로 update + archive link 동봉. Data Source
    # Status card 도 같이 제거 (정적 OK chip 만, layout 공간 낭비).
    def header_date_update(soup_arg, korean_date, gen_at):
        import re as _re
        kr_date_re = _re.compile(r"\d{4}년\s*\d+월\s*\d+일\s*\(.\)")
        updated = 0
        for ns in list(soup_arg.find_all(string=kr_date_re)):
            new_text = korean_date + "  ·  " + gen_at[-13:]
            parent_before = ns.parent  # capture before replace_with detaches
            ns.replace_with(new_text)
            updated += 1
            if updated == 1 and parent_before is not None:
                a = soup_arg.new_tag("a", href="/dashboard/archive",
                    style=("margin-left:14px;color:var(--accent);"
                           "font-size:13px;text-decoration:none;"
                           "font-weight:500;vertical-align:middle"),
                    title="archive index")
                a.string = "📚 Archive"
                parent_before.insert_after(a)
        return updated

    n = header_date_update(soup, KOREAN_DATE, GEN_AT)
    log.info("header date+time+archive: %d node(s) updated", n)

    # Data Source Status card 제거 (정적 OK chip, misleading)
    ds_removed = 0
    for s in list(soup.find_all("section", class_="card")):
        h3 = s.select_one(".panel-title")
        if h3 and "Data Source Status" in h3.get_text():
            s.decompose()
            ds_removed += 1
    log.info("Data Source Status removed: %d section(s)", ds_removed)

    # Standard View 자동화 (deploy-card) 제거 (정적 deploy info)
    deploy_removed = 0
    for s in list(soup.find_all("section", class_="card")):
        if "deploy-card" in (s.get("class") or []):
            s.decompose()
            deploy_removed += 1
    log.info("deploy-card removed: %d section(s)", deploy_removed)

    # cost card inject 은 main() 끝 부분으로 이동 — 모든 API 호출
    # 후 호출 횟수 / 비용 정확히 반영하기 위해.

    # ─── 2. Macro Snapshot — indicator values ──────────────────────
    snap = _fetch(
        "/api/macro/analyze",
        method="POST",
        json_body={
            "scope": "both",
            "selected_indicators": [],
            "sector": "",
            "force_refresh": True,
        },
        timeout=240,
    )
    if snap and snap.get("indicators"):
        log.info("snapshot indicators: %d", len(snap["indicators"]))
        # Match by .metric-label text → swap sibling .metric-value
        for indi_id, info in snap["indicators"].items():
            label_text = (info.get("name") or "").strip()
            if not label_text:
                continue
            for card in soup.select(".metric-card, .indicator-card, .snap-card"):
                lbl = (
                    card.select_one(".metric-label")
                    or card.select_one(".indicator-label")
                    or card.select_one(".label")
                )
                if lbl and lbl.get_text(strip=True) == label_text:
                    val = (
                        card.select_one(".metric-value")
                        or card.select_one(".indicator-value")
                        or card.select_one(".value")
                    )
                    if val:
                        val.string = _fmt_value(
                            info.get("value"), info.get("unit", "")
                        )
                    break

    # ─── 2.5 핵심 요약 bullets (B-3.2) ────────────────────────────
    # Friend HTML 의 .summary-card > <ul> 안 정적 5 bullets 를
    # Gemini brief 의 Executive Summary + Market Moving Issues 에서
    # 추출한 8 bullets 로 swap. brief 가 아직 없을 수 있으니 friend
    # 정적 bullets fallback 유지 (decompose 안 함).

    # ─── 3. Macro News-brief (domestic + global) ──────────────────
    brief = _fetch(
        "/api/macro/news-brief",
        method="POST",
        json_body={
            "scope": "both",
            "topics": ["interest_rate", "fx", "inflation", "growth", "policy"],
            "date_range": "7d",
            "keywords": [],
            "max_results": 20,
            "force_refresh": True,
        },
        timeout=240,
    )
    # B-3.8: brief fetch 실패 시 2회 재시도 — 산업 과부하 시 backend
    # ThreadPoolExecutor 가 timeout 으로 500 반환 가능, retry 로 recover.
    if not brief or not brief.get("report"):
        import time as _t_brief
        for attempt in range(2):
            _t_brief.sleep(10 + attempt * 10)
            log.info("brief retry %d", attempt + 1)
            brief = _fetch(
                "/api/macro/news-brief",
                method="POST",
                json_body={
                    "scope": "both",
                    "topics": ["interest_rate", "fx", "inflation", "growth", "policy"],
                    "date_range": "7d",
                    "keywords": [],
                    "max_results": 20,
                    "force_refresh": True,
                },
                timeout=240,
            )
            if brief and brief.get("report"):
                log.info("brief retry %d succeeded", attempt + 1)
                break

    def _replace_brief_list(card_selector: str, articles: list):
        card = soup.select_one(card_selector)
        if not card:
            log.info("brief card not found: %s", card_selector)
            return
        ul = card.select_one(".macro-brief-list, ul")
        if not ul:
            return
        ul.clear()
        for a in articles[:6]:
            li = soup.new_tag("li")
            li.string = (a.get("title") or "")[:140]
            ul.append(li)

    if brief:
        _replace_brief_list(
            ".macro-brief-card.domestic", brief.get("ko_articles") or []
        )
        _replace_brief_list(
            ".macro-brief-card.global_", brief.get("en_articles") or []
        )

    # ─── 4. Macro News list ───────────────────────────────────────
    def _find_card_by_h3(text_match: str):
        for s in soup.find_all("section", class_="card"):
            h3 = s.select_one(".panel-title")
            if h3 and text_match in h3.get_text():
                return s
        return None

    news_card = _find_card_by_h3("Macro News")
    if news_card:
        # B-3.4 (2026-05-20): Macro News 도 전체 너비 (takeaway 와 동일).
        existing_style = news_card.get("style", "")
        if "grid-column" not in existing_style:
            news_card["style"] = (existing_style + ";grid-column:1/-1").lstrip(";")
    if news_card and brief:
        # Strip existing news items + headers
        for el in news_card.select(
            ".news-item, .news-section-header"
        ):
            el.decompose()

        def _append_news(parent, articles, header_label):
            hdr = soup.new_tag(
                "div", attrs={"class": "news-section-header"}
            )
            hdr.string = header_label
            parent.append(hdr)
            for a in articles[:8]:
                item = soup.new_tag(
                    "div", attrs={"class": "news-item"}
                )
                tag = soup.new_tag(
                    "span", attrs={"class": "news-tag tag-blue"}
                )
                topics = a.get("macro_topics") or []
                tag.string = topics[0] if topics else "기타"
                item.append(tag)
                link = soup.new_tag(
                    "a",
                    attrs={
                        "class": "news-headline news-link",
                        "href": a.get("url", "#"),
                        "target": "_blank",
                    },
                )
                link.string = a.get("title") or ""
                item.append(link)
                parent.append(item)

        _append_news(news_card, brief.get("ko_articles") or [], "국내")
        _append_news(news_card, brief.get("en_articles") or [], "글로벌")

    # 핵심 요약 bullets swap (B-3.2)
    if brief and brief.get("report"):
        new_bullets = _extract_summary_bullets(brief["report"], n=5)
        if new_bullets:
            sm_card = soup.find("section", class_="card summary")
            # class= 가 multi-class 라 위 query 가 partial match — 대신
            # find_all + class list 검사
            if not sm_card:
                for s in soup.find_all("section"):
                    cls = s.get("class") or []
                    if "card" in cls and "summary" in cls:
                        sm_card = s
                        break
            if sm_card:
                ul = sm_card.find("ul")
                if ul:
                    for li in ul.find_all("li"):
                        li.decompose()
                    for b in new_bullets:
                        new_li = soup.new_tag("li")
                        new_li.string = b
                        ul.append(new_li)
                    log.info("summary bullets swapped: %d", len(new_bullets))

    # ─── 5. Takeaway (투자 및 딜 관점) ─────────────────────────────
    take_card = _find_card_by_h3("투자")
    if take_card:
        existing_style = take_card.get("style", "")
        if "grid-column" not in existing_style:
            take_card["style"] = (existing_style + ";grid-column:1/-1").lstrip(";")
    if take_card and brief and brief.get("report"):
        # B-3.10 (2026-05-20): 친구 hardcoded prose (Executive Summary
        # 와 비슷한 내용) 가 brief 본문과 중복 표시되는 issue. h3
        # (panel-title) 만 유지, 나머지 children 모두 제거 후 우리
        # wrapper 만 inject.
        h3_to_keep = take_card.select_one("h3.panel-title")
        children = list(take_card.children)
        for c in children:
            if c is h3_to_keep:
                continue
            try:
                if hasattr(c, "decompose"):
                    c.decompose()
                elif hasattr(c, "extract"):
                    c.extract()
            except Exception:
                pass
        # B-3.1 (2026-05-20): MD → HTML formatter 재사용 (텔레그램에서
        # 쓴 것과 동일). ## section header 를 <details>/<summary> 로
        # 감싸 접기 가능. 전체 report (cap 제거) 표시.
        raw = brief["report"]
        formatted = _format_brief_for_telegram(raw)
        # Split by <b>N. Heading</b> sections (numbered headers from
        # formatter) — wrap each in <details>. 첫 section (Executive
        # Summary) open by default.
        import re as _re_brief
        # 'N. Heading' 라벨 매칭
        section_re = _re_brief.compile(
            r"<b>(\d+\.\s*[^<]+)</b>"
        )
        wrapper = soup.new_tag(
            "div",
            attrs={
                "class": "gen-report",
                "style": (
                    "color:var(--text);font-family:inherit;font-size:14px;"
                    "margin-top:14px;line-height:1.7"
                ),
            },
        )
        # B-3.10.2 dedup intro/tail: intro 는 첫 section 앞 prologue 만,
        # tail 은 제거 (마지막 section body 는 details 본문에
        # 이미 포함). 각 section body 가 두 번 렌더링되던 버그.
        parts = []
        first_match_seen = False
        for m in section_re.finditer(formatted):
            if not first_match_seen:
                if m.start() > 0:
                    intro_text = formatted[:m.start()].strip()
                    if intro_text:
                        parts.append(("intro", intro_text))
                first_match_seen = True
            parts.append(("section", m.group(1), m.end()))
        # Render: intro raw, sections as <details> (each section body
        # ends at the next section header)
        section_indices = [
            i for i, p in enumerate(parts) if p[0] == "section"
        ]
        for i, part in enumerate(parts):
            if part[0] in ("intro", "tail"):
                snippet = part[1].strip()
                if snippet:
                    frag = BeautifulSoup(snippet, "html.parser")
                    intro_div = soup.new_tag(
                        "div", attrs={"style": "margin-bottom:10px"}
                    )
                    for child in list(frag.contents):
                        intro_div.append(child)
                    wrapper.append(intro_div)
            else:  # section
                title = part[1]
                # Body = from this section end to next section start (or
                # end of formatted text)
                start = part[2]
                idx_in_sections = section_indices.index(i)
                next_section_i = (
                    section_indices[idx_in_sections + 1]
                    if idx_in_sections + 1 < len(section_indices)
                    else None
                )
                if next_section_i is not None:
                    body_end = parts[next_section_i][2] - len(
                        f"<b>{parts[next_section_i][1]}</b>"
                    )
                else:
                    body_end = len(formatted)
                body_html = formatted[start:body_end].strip()
                if not body_html:
                    continue
                # 첫 section (보통 Executive Summary) 만 open 기본
                is_open = True
                details = soup.new_tag(
                    "details",
                    attrs={
                        "style": (
                            "background:var(--surface-2,rgba(28,38,60,0.5));"
                            "border:1px solid var(--border);"
                            "border-radius:6px;padding:10px 14px;"
                            "margin-bottom:8px"
                        ),
                    },
                )
                if is_open:
                    details["open"] = ""
                summary_tag = soup.new_tag(
                    "summary",
                    attrs={
                        "style": (
                            "cursor:pointer;font-weight:600;"
                            "color:var(--accent);font-size:14px;"
                            "list-style:none;user-select:none;"
                            "padding:2px 0"
                        ),
                    },
                )
                summary_tag.string = "▸ " + title
                details.append(summary_tag)
                body_frag = BeautifulSoup(body_html, "html.parser")
                body_div = soup.new_tag(
                    "div", attrs={"style": "margin-top:8px"}
                )
                for child in list(body_frag.contents):
                    body_div.append(child)
                details.append(body_div)
                wrapper.append(details)
        take_card.append(wrapper)

    # ─── 6. Source articles ──────────────────────────────────────
    src = soup.select_one(".source-articles-details")
    if src and brief:
        for el in src.select(
            ".source-card, .source-row, .source-item, .source-article"
        ):
            el.decompose()
        badge = src.select_one(".source-count-badge")
        if badge:
            ko_n = len(brief.get("ko_articles") or [])
            en_n = len(brief.get("en_articles") or [])
            badge.string = f"국내 {ko_n} · 글로벌 {en_n}건"
        for a in (brief.get("ko_articles") or []) + (
            brief.get("en_articles") or []
        ):
            box = soup.new_tag(
                "div",
                attrs={
                    "class": "source-article",
                    "style": (
                        "padding:10px 14px;"
                        "border-top:1px solid var(--border)"
                    ),
                },
            )
            link = soup.new_tag(
                "a",
                attrs={
                    "href": a.get("url", "#"),
                    "target": "_blank",
                    "style": (
                        "color:var(--text);font-weight:500;"
                        "text-decoration:none"
                    ),
                },
            )
            link.string = a.get("title") or ""
            box.append(link)
            meta_div = soup.new_tag(
                "div",
                attrs={
                    "style": (
                        "color:var(--text-muted);font-size:12px;"
                        "margin-top:4px"
                    )
                },
            )
            meta_div.string = (
                f"{a.get('publisher','')} · "
                f"{a.get('pubDate','')}"
            )
            box.append(meta_div)
            src.append(box)

    # ─── 7. Market Moving Issues (A2-1) ──────────────────────────
    # news-brief 의 모든 article 을 importance_score desc 정렬 → top 5
    # heuristic direction (negative/positive/neutral) 으로 분류 후 inject.
    if brief:
        all_articles = (brief.get("ko_articles") or []) + (
            brief.get("en_articles") or []
        )
        all_articles = [
            a for a in all_articles if isinstance(a, dict) and a.get("title")
        ]
        all_articles.sort(
            key=lambda a: a.get("importance_score") or 0,
            reverse=True,
        )
        top_mmi = all_articles[:5]

        mmi_card = _find_card_by_h3("Market Moving Issues")
        if mmi_card and top_mmi:
            table = mmi_card.select_one(".mmi-table")
            if table:
                # Clear existing rows
                for row in table.select(".mmi-row"):
                    row.decompose()
                # Insert top 5
                for rank, art in enumerate(top_mmi, start=1):
                    label, badge_cls = _mmi_classify(art.get("title", ""))
                    row = soup.new_tag(
                        "div", attrs={"class": "mmi-row"}
                    )
                    rnk = soup.new_tag(
                        "span", attrs={"class": "mmi-rank"}
                    )
                    rnk.string = str(rank)
                    row.append(rnk)
                    dir_b = soup.new_tag(
                        "span",
                        attrs={
                            "class": f"mmi-dir-badge {badge_cls}"
                        },
                    )
                    dir_b.string = label
                    row.append(dir_b)
                    content = soup.new_tag(
                        "div",
                        attrs={"class": "mmi-content"},
                    )
                    # Title 을 link 로 (출처 URL 있으면)
                    if art.get("url"):
                        link = soup.new_tag(
                            "a",
                            attrs={
                                "href": art["url"],
                                "target": "_blank",
                                "style": (
                                    "color:inherit;text-decoration:none"
                                ),
                            },
                        )
                        link.string = (art.get("title") or "")[:120]
                        content.append(link)
                    else:
                        content.string = (art.get("title") or "")[:120]
                    # 부제: source · score
                    sub = soup.new_tag(
                        "div",
                        attrs={
                            "style": (
                                "color:var(--text-muted);"
                                "font-size:11px;margin-top:4px"
                            )
                        },
                    )
                    sub.string = (
                        f"{art.get('publisher') or art.get('source') or ''} "
                        f"· score {art.get('importance_score', 0)}"
                    )
                    content.append(sub)
                    row.append(content)
                    table.append(row)
            log.info("MMI rows inserted: %d", len(top_mmi))

    # ─── 8. Comment cards (A2-2) ─────────────────────────────────
    # Single Gemini call → 4 insights (market / policy / sector / risk).
    # 4 .comment-card section 의 .comment-title + .comment-body 채움.
    if snap or brief:
        comments = _generate_comment_cards(snap or {}, brief or {})
        if comments:
            cards = soup.select(".comment-card")
            keys = ["market", "policy", "sector", "risk"]
            filled = 0
            for card, key in zip(cards, keys):
                c = comments.get(key) or {}
                title_el = card.select_one(".comment-title")
                body_el = card.select_one(".comment-body")
                if title_el and c.get("title"):
                    title_el.string = c["title"]
                if body_el and c.get("body"):
                    body_el.string = c["body"]
                if c.get("body"):
                    filled += 1
            log.info("comment cards filled: %d/4", filled)

    # ─── 9. Sentiment Gauge (A2-3) ───────────────────────────────
    # SVG needle rotation + .gauge-value 숫자/class + .gauge-caption
    # 모두 VIX-derived score 기반으로 swap.
    if snap:
        score, label, cls, angle = _compute_sentiment(snap)
        gauge_card = None
        for s in soup.find_all("section", class_="card"):
            h3 = s.select_one(".chart-title")
            if h3 and "센티먼트" in h3.get_text():
                gauge_card = s
                break
        if gauge_card:
            # (a) Needle <g transform>
            svg = gauge_card.select_one("svg")
            if svg:
                for g in svg.find_all("g"):
                    tr = g.get("transform", "")
                    if "rotate" in tr:
                        g["transform"] = (
                            f"translate(120, 130) rotate({angle:.2f})"
                        )
                        break
            # (b) Score value text
            val = gauge_card.select_one(".gauge-value")
            if val:
                val.string = str(score)
                val["class"] = ["gauge-value", cls]
            # (c) Caption (Fear/Neutral/Greed)
            cap = gauge_card.select_one(".gauge-caption")
            if cap:
                cap.string = label
            log.info(
                "sentiment gauge: score=%d label=%s angle=%.1f",
                score, label, angle,
            )

    # ─── 10. Chart.js time-series (A2-4) ─────────────────────────
    # Inline <script> 안 ratesData + infData JS 변수 swap.
    # Data sources: yfinance (^TNX / KRW=X) + FRED (CPIAUCSL / KORCPI).
    # Fail-soft: 어느 series 가 fetch 실패하면 그 series 만 skip,
    # 나머지 friend 정적 데이터 그대로 유지.
    import json as _json
    import re as _re

    chart_labels = _build_chart_labels(7)
    us_10y_hist = _fetch_fred_monthly("DGS10", 7)  # daily series; pick monthly proxies
    usdkrw_hist = _fetch_yf_monthly("KRW=X", 7)
    us_cpi_hist = _fetch_fred_monthly("CPIAUCSL", 7)
    kr_cpi_hist = _fetch_fred_monthly("KORCPIALLMINMEI", 7)
    log.info(
        "chart history fetched: us10y=%d usdkrw=%d uscpi=%d krcpi=%d",
        len(us_10y_hist), len(usdkrw_hist),
        len(us_cpi_hist), len(kr_cpi_hist),
    )

    rates_series = []
    if len(us_10y_hist) == 7:
        rates_series.append({
            "axis": "y", "color": "#3B82F6",
            "data": us_10y_hist,
            "label": "미국 10Y (좌)",
        })
    if len(usdkrw_hist) == 7:
        rates_series.append({
            "axis": "y1", "color": "#A78BFA",
            "data": usdkrw_hist,
            "label": "원/달러 (우)",
        })
    # KR 기준금리: snap 의 kr_rate 현재값을 7개월 평탄선 (변경 드물어 OK)
    kr_rate_val = ((snap or {}).get("indicators") or {}).get(
        "kr_rate", {}
    ).get("value")
    if isinstance(kr_rate_val, (int, float)):
        rates_series.append({
            "axis": "y", "color": "#22D3EE",
            "data": [kr_rate_val] * 7,
            "label": "한국 기준금리 (좌)",
        })

    if rates_series:
        rates_obj = {
            "labels": chart_labels,
            "series": rates_series,
            "source": "yfinance, FRED, ECOS",
            "title": "금리·환율 추이",
            "y_left_label": "(%)",
            "y_right_label": "(원)",
        }
        cur_html = str(soup)
        pat = _re.compile(
            r"const ratesData = \{.*?\};",
            _re.DOTALL,
        )
        new_html, n = pat.subn(
            f"const ratesData = {_json.dumps(rates_obj, ensure_ascii=False)};",
            cur_html,
            count=1,
        )
        if n:
            soup = BeautifulSoup(new_html, "html.parser")
            log.info("ratesData chart updated (%d series)", len(rates_series))

    inf_series = []
    if len(us_cpi_hist) == 7:
        inf_series.append({
            "color": "#F59E0B",
            "data": us_cpi_hist,
            "label": "미국 CPI (지수)",
        })
    if len(kr_cpi_hist) == 7:
        inf_series.append({
            "color": "#10B981",
            "data": kr_cpi_hist,
            "label": "한국 CPI (지수)",
        })

    if inf_series:
        inf_obj = {
            "labels": chart_labels,
            "series": inf_series,
            "source": "FRED (CPIAUCSL / KORCPIALLMINMEI)",
            "title": "물가와 경기 모멘텀",
        }
        cur_html = str(soup)
        pat = _re.compile(
            r"const infData = \{.*?\};",
            _re.DOTALL,
        )
        new_html, n = pat.subn(
            f"const infData = {_json.dumps(inf_obj, ensure_ascii=False)};",
            cur_html,
            count=1,
        )
        if n:
            soup = BeautifulSoup(new_html, "html.parser")
            log.info("infData chart updated (%d series)", len(inf_series))

    # ─── 11. NOAH per-ticker analyses (B-2) ──────────────────────
    # Today's NOAH analyses pushed via /api/noah/analysis. New HTML
    # section inserted right after takeaway-card.
    noah_today = _load_noah_today()
    if noah_today:
        takeaway = None
        for s in soup.find_all("section", class_="card"):
            if "takeaway-card" in (s.get("class") or []):
                takeaway = s
                break
        if takeaway:
            new_sec = soup.new_tag(
                "section",
                attrs={"class": "card", "style": "padding:22px 24px"},
            )
            hdr = soup.new_tag("h3", attrs={"class": "panel-title"})
            hdr.string = f"\U0001F4CA 오늘 NOAH 분석 ({len(noah_today)}건)"
            new_sec.append(hdr)
            for rec in noah_today:
                row = soup.new_tag(
                    "div",
                    attrs={"style": "padding:12px 0;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:4px"},
                )
                top = soup.new_tag(
                    "div",
                    attrs={"style": "display:flex;gap:10px;align-items:center;font-size:14px"},
                )
                ticker_el = soup.new_tag(
                    "span",
                    attrs={"style": "color:var(--text);font-weight:600;font-family:'IBM Plex Mono',monospace"},
                )
                ticker_el.string = f"{rec.get('ticker','?')} [{rec.get('market','?')}]"
                top.append(ticker_el)
                verdict_el = soup.new_tag(
                    "span",
                    attrs={"style": "color:var(--accent);font-weight:600;background:var(--accent-soft);padding:2px 8px;border-radius:4px;font-size:12px"},
                )
                verdict_el.string = rec.get("verdict", "N/A")
                top.append(verdict_el)
                ts_el = soup.new_tag(
                    "span",
                    attrs={"style": "color:var(--text-muted);font-size:11px;margin-left:auto"},
                )
                ts = rec.get("ts", "")
                ts_el.string = ts[-8:-3] if len(ts) >= 8 else ts
                top.append(ts_el)
                row.append(top)
                if rec.get("stance_bar"):
                    sb = soup.new_tag("div", attrs={"style": "color:var(--text-muted);font-size:12px"})
                    sb.string = rec["stance_bar"]
                    row.append(sb)
                if rec.get("snippet"):
                    sn = soup.new_tag("div", attrs={"style": "color:var(--text);font-size:13px;line-height:1.5;margin-top:2px"})
                    sn.string = rec["snippet"][:300]
                    row.append(sn)
                if rec.get("dashboard_url"):
                    lnk = soup.new_tag(
                        "a",
                        attrs={"href": rec["dashboard_url"], "target": "_blank",
                               "style": "color:var(--cyan);font-size:11px;text-decoration:none;margin-top:2px"},
                    )
                    lnk.string = "\U0001F4CB dashboard \u25B8"
                    row.append(lnk)
                new_sec.append(row)
            takeaway.insert_after(new_sec)
            log.info("NOAH analyses section inserted: %d entries", len(noah_today))

    # ─── 12. Industry trends + Deal Highlights (B-2 enrichment) ──
    # Friend Standard View 의 의도 (README "통합 일일 리포트") 달성:
    # macro + industry + deal 3 영역을 daily brief 에 통합.
    # 3 산업 × Gemini 호출 (~₩200-500/day 추가 비용).
    import time as _time
    import json as _json_ind
    _CACHE = Path.home() / "standardview" / "industry_cache.json"
    industry_cache = {}
    if _CACHE.exists():
        try:
            industry_cache = _json_ind.loads(_CACHE.read_text())
        except Exception:
            industry_cache = {}
    # E-1 산업 호출 병렬화 (2026-05-21)
    # ThreadPoolExecutor 로 8 산업 동시 호출. 각 산업은 독립적으로
    # 3회 시도 (1+2 retry). 순서는 _DEFAULT_INDUSTRIES 기준으로 보존.
    # max_workers=4 — Gemini RPM (Tier 1: 1000/min) 안전 범위.
    from concurrent.futures import ThreadPoolExecutor as _TPE
    def _fetch_with_retries(indu):
        for attempt in range(3):
            d = _fetch_industry(indu)
            if d:
                return indu, d, attempt
            if attempt < 2:
                _time.sleep(5 + attempt * 5)
        return indu, None, 3
    _ind_map = {}
    with _TPE(max_workers=4) as _ex:
        for indu_, d_, n_ in _ex.map(_fetch_with_retries, _DEFAULT_INDUSTRIES):
            if d_:
                _ind_map[indu_] = d_
                industry_cache[indu_] = d_
                if n_ > 0:
                    log.info("industry retry %d succeeded: %s", n_, indu_)
            else:
                cached_ = industry_cache.get(indu_)
                if cached_:
                    _ind_map[indu_] = cached_
                    log.info("industry cache fallback used: %s", indu_)
                else:
                    log.warning("industry FINAL fail (no cache): %s", indu_)
    industries_data = [
        {"industry": indu, **_ind_map[indu]}
        for indu in _DEFAULT_INDUSTRIES
        if indu in _ind_map
    ]
    try:
        _CACHE.write_text(_json_ind.dumps(industry_cache, ensure_ascii=False))
    except Exception:
        pass
    log.info("industry fetches: %d/%d", len(industries_data), len(_DEFAULT_INDUSTRIES))

    if industries_data:
        # Insert 산업 트렌드 section AFTER takeaway-card (or after NOAH
        # section if it exists).
        anchor = None
        for s in soup.find_all("section", class_="card"):
            if "takeaway-card" in (s.get("class") or []):
                anchor = s
        # Move anchor forward past the NOAH section if present
        if anchor and anchor.find_next_sibling("section"):
            nxt = anchor.find_next_sibling("section")
            if nxt and "오늘 NOAH 분석" in (nxt.get_text() or ""):
                anchor = nxt

        if anchor:
            # B-3.5 layout: 8 industries → 2-column inner grid (4×2).
            # 부모 section 은 grid-column:1/-1 (전체 너비), 안쪽은
            # grid-template-columns: 1fr 1fr.
            indu_sec = soup.new_tag(
                "section",
                attrs={"class": "card", "style": "padding:22px 24px;grid-column:1/3"},
            )
            hdr = soup.new_tag("h3", attrs={"class": "panel-title"})
            hdr.string = f"\U0001F3ED 산업 트렌드 ({len(industries_data)}개 산업)"
            indu_sec.append(hdr)
            indu_inner = soup.new_tag(
                "div",
                attrs={"style": "display:grid;grid-template-columns:1fr 1fr;gap:14px 24px;margin-top:8px"},
            )
            indu_sec.append(indu_inner)
            for entry in industries_data:
                row = soup.new_tag(
                    "div",
                    attrs={"style": "padding:12px 0;border-bottom:1px solid var(--border)"},
                )
                name_el = soup.new_tag(
                    "div",
                    attrs={"style": "color:var(--accent);font-weight:600;margin-bottom:4px;font-size:14px"},
                )
                name_el.string = (
                    f"\u2022 {entry['industry']} "
                    f"(뉴스 KO={entry.get('news_ko_count',0)} / "
                    f"EN={entry.get('news_en_count',0)})"
                )
                row.append(name_el)
                # B-3.2 (2026-05-20): /api/industry-analysis 응답의
                # sector_overview (str) + recent_trends (list[3]) +
                # growth_drivers (list[3]) 합쳐서 표시.
                parts_text = []
                v = entry.get("sector_overview")
                if isinstance(v, str) and v.strip():
                    parts_text.append(v.strip())
                for f in ("recent_trends", "growth_drivers"):
                    v = entry.get(f)
                    if isinstance(v, list) and v:
                        parts_text.append(" · ".join(str(x) for x in v[:3]))
                summary_text = " — ".join(parts_text)
                if summary_text:
                    body = soup.new_tag(
                        "div",
                        attrs={"style": "color:var(--text);font-size:13px;line-height:1.6;margin-top:4px"},
                    )
                    body.string = summary_text[:700]
                    row.append(body)
                indu_inner.append(row)
            anchor.insert_after(indu_sec)
            log.info("산업 트렌드 section inserted: %d industries", len(industries_data))
            anchor = indu_sec  # next section goes after this

            # Deal Highlights aggregated across all industries
            deals_top = _aggregate_deal_highlights(industries_data, top_n=5, brief=brief)
            if deals_top:
                deal_sec = soup.new_tag(
                    "section",
                    attrs={"class": "card", "style": "padding:22px 24px;grid-column:3/4"},
                )
                dhdr = soup.new_tag("h3", attrs={"class": "panel-title"})
                dhdr.string = f"\U0001F4BC Deal Highlights ({len(deals_top)}건 — 산업별 dedup)"
                deal_sec.append(dhdr)
                for d in deals_top:
                    row = soup.new_tag(
                        "div",
                        attrs={"style": "padding:12px 0;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:4px"},
                    )
                    top = soup.new_tag(
                        "div",
                        attrs={"style": "display:flex;gap:10px;align-items:center;font-size:13px"},
                    )
                    type_el = soup.new_tag(
                        "span",
                        attrs={"style": "color:var(--cyan);font-weight:600;background:rgba(34,211,238,0.1);padding:2px 8px;border-radius:4px;font-size:11px"},
                    )
                    type_el.string = d.get("deal_type", "기타")[:30]
                    top.append(type_el)
                    score = d.get("deal_relevance_score", 0)
                    score_el = soup.new_tag(
                        "span",
                        attrs={"style": "color:var(--text-muted);font-size:11px;margin-left:auto"},
                    )
                    score_el.string = f"score {score}"
                    top.append(score_el)
                    row.append(top)
                    title_el = soup.new_tag(
                        "div",
                        attrs={"style": "color:var(--text);font-size:13px;line-height:1.5"},
                    )
                    if d.get("url") or d.get("link"):
                        link = soup.new_tag(
                            "a",
                            attrs={
                                "href": d.get("url") or d.get("link"),
                                "target": "_blank",
                                "style": "color:inherit;text-decoration:none",
                            },
                        )
                        link.string = (d.get("title") or "")[:150]
                        title_el.append(link)
                    else:
                        title_el.string = (d.get("title") or "")[:150]
                    row.append(title_el)
                    deal_sec.append(row)
                anchor.insert_after(deal_sec)
                log.info("Deal Highlights section inserted: %d deals", len(deals_top))

    # ─── 13. sparkData 22 sparklines (A2-5) ──────────────────────
    # Macro Snapshot card 별 12개월 sparkline 동적화. 14개 (US 금리/
    # CPI/실업률 + USD/KRW + DXY + 4 indices + 4 commodities) 만 fetch,
    # KR 정책금리/국고채/GDP/수출 6개 (ECOS 의존) + dividers 2개는
    # 친구 hardcoded 유지.
    import re as _re_spark
    import json as _json_spark
    cur_html = str(soup)
    spark_re = _re_spark.compile(r"const sparkData = (\[.*?\]);", _re_spark.DOTALL)
    m = spark_re.search(cur_html)
    if m:
        try:
            # Parse existing array — drop comments + trailing commas to JSON
            raw = m.group(1)
            existing = _json_spark.loads(raw)
            new_arr = list(existing)  # start from existing (preserves static)
            n_updated = 0
            for idx, (src_type, ident) in _SPARKLINE_SOURCES.items():
                if idx >= len(new_arr):
                    continue
                fresh = _fetch_sparkline_12mo(src_type, ident)
                if len(fresh) >= 10:  # futures (WTI/Brent/copper/gold) often return 10-11
                    new_arr[idx] = fresh
                    n_updated += 1
            new_arr_js = _json_spark.dumps(new_arr, separators=(",", ":"))
            cur_html = cur_html.replace(
                f"const sparkData = {raw};",
                f"const sparkData = {new_arr_js};",
                1,
            )
            soup = BeautifulSoup(cur_html, "html.parser")
            log.info("sparkData updated: %d/%d sparklines dynamic", n_updated, len(_SPARKLINE_SOURCES))
        except Exception as exc:
            log.warning("sparkData swap failed: %s", exc)

    # ─── SV 비용 카드 inject (B-3.5 final — 모든 API 호출 후) ───
    try:
        r = httpx.get(f"{BACKEND}/api/sv-usage/today", timeout=5)
        usage_final = r.json() if r.status_code == 200 else {}
    except Exception:
        usage_final = {}
    if usage_final and isinstance(usage_final, dict):
        tk = usage_final.get("today_krw", 0) or 0
        mk = usage_final.get("month_krw", 0) or 0
        tc = usage_final.get("today_calls", 0) or 0
        anchor_el = None
        for a in soup.find_all("a"):
            if "GitHub Report" in (a.get_text() or ""):
                anchor_el = a
                break
        if anchor_el is not None:
            card = BeautifulSoup(
                '<div style="display:inline-flex;flex-direction:column;'
                'background:rgba(20,28,48,0.6);border:1px solid rgba(255,255,255,0.08);'
                'border-radius:8px;padding:8px 14px;margin-right:8px;'
                f'font-size:12px;vertical-align:middle">'
                f'<div style="color:var(--text-muted);font-size:11px">'
                f'💰 비용 (오늘 / 이번 달)</div>'
                f'<div style="color:var(--text);font-weight:600;font-size:14px">'
                f'₩{tk:,.1f} / ₩{mk:,.0f}</div>'
                f'<div style="color:var(--text-muted);font-size:10px">'
                f'flash · {tc}회</div>'
                '</div>',
                "html.parser",
            )
            anchor_el.insert_before(card)
            log.info("SV 비용 카드 (final) inserted: today=%.1f month=%.0f calls=%d", tk, mk, tc)

    # ─── Write HTML ──────────────────────────────────────────────
    # B-3 (2026-05-20): 일자별 + 시각별 누적. 같은 시각 (HHMM) 은
    # overwrite. {date}.html 는 점진적 deprecated — 새로 만들지 않음.
    _HHMM = datetime.now().strftime("%H%M")
    timestamped = REPORTS / f"{TODAY}_{_HHMM}.html"
    _wire_send_to_telegram_button(soup)
    # A-1 unescape entities (2026-05-21)

    _html_str = str(soup)

    _html_str = re.sub(r'&amp;(amp|quot|apos|lt|gt|#\d+|nbsp);', r'&\1;', _html_str)

    timestamped.write_text(_html_str)
    log.info("wrote %s (%d bytes)", timestamped, timestamped.stat().st_size)
    # Also write 'latest.html' for the FastAPI /dashboard mount.
    latest_html = REPORTS / "latest.html"
    # A-1 entity unescape (2026-05-21)
    _html_out = str(soup)
    _html_out = re.sub(r'&amp;(amp|quot|apos|lt|gt|#\d+|nbsp);', r'&\1;', _html_out)
    latest_html.write_text(_html_out)

    # ─── Write MD for Telegram body ──────────────────────────────
    # Telegram HTML 형식 — pusher 가 parse_mode='HTML' 사용.
    # <b>로 굵게, 🔹로 섹션 구분. ** 와 ## 마크다운 모두 strip.
    md = [
        f"<b>📊 Standard View — Daily Brief</b>",
        f"<b>{TODAY}</b> · 생성 {GEN_AT}",
        "",
        "━━━━━━━━━━━━━━━━",
        "",
    ]
    if snap and snap.get("indicators"):
        md.append("🔹 <b>매크로 동향</b>")
        for iid, info in list(snap["indicators"].items())[:10]:
            v = info.get("value")
            vs = (
                f"{v:.2f}" if isinstance(v, (int, float)) else (v or "—")
            )
            unit = info.get("unit", "")
            md.append(f"• {info.get('name')}: <b>{vs}</b> {unit}".rstrip())
        md.append("")
    if brief:
        md.append("🔹 <b>매크로 브리프</b>")
        if brief.get("report"):
            md.append(_format_brief_for_telegram(brief["report"][:2000]))
        md.append("")
        ko_n = len(brief.get("ko_articles") or [])
        en_n = len(brief.get("en_articles") or [])
        md.append(
            f"📰 뉴스 수집: 국내 <b>{ko_n}</b>건 · 글로벌 <b>{en_n}</b>건"
        )
        if ko_n == 0:
            md.append(
                "  <i>(국내 뉴스 0건 = Naver API 401 인증 실패 — 새 앱"
                " 등록 필요, 영문 NewsAPI/GDELT 만 활성)</i>"
            )
        md.append("")
        if ko_n > 0:
            md.append("🔹 <b>주요 한국 헤드라인</b>")
            for a in (brief.get("ko_articles") or [])[:5]:
                md.append(f"• {(a.get('title') or '')[:100]}")
        if en_n > 0:
            md.append("🔹 <b>주요 글로벌 헤드라인</b>")
            for a in (brief.get("en_articles") or [])[:5]:
                md.append(f"• {(a.get('title') or '')[:100]}")
    md.append("")
    md.append("━━━━━━━━━━━━━━━━")
    md.append(f"<i>Standard View · {TODAY} · 08:00 KST</i>")

    out_md = REPORTS / f"{TODAY}.md"
    out_md.write_text("\n".join(md))
    log.info("wrote %s (%d bytes)", out_md, out_md.stat().st_size)
    latest_md = REPORTS / "latest.md"
    latest_md.write_text("\n".join(md))


if __name__ == "__main__":
    main()


