"""미국 수입 데이터 (나쁜양파) — 파서 + 저장 + 대시보드.

Badonions 채널의 미국 수입 캡션을 tw/cn/jp2/mx 패턴과 동일하게
분리 저장하고, 별도 us.db → us.html 로 렌더한다.
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from trade.archive_template import back_nav_html
from trade.archive_template import card_html

_MARKER_RE = re.compile(r"\d+\s*월\s*수입\s*미국")
_RE_ITEM = re.compile(r"▶️\s*(.+)")
_RE_MONTHLINE = re.compile(
    r"(\d{2})년(\d{2})월\s*:\s*\$([\d,]+(?:\.\d+)?)M\s*"
    r"\(([+\-]?\d+(?:\.\d+)?)%\s*YoY\)\s*"
    r"\(([+\-]?\d+(?:\.\d+)?)%\s*MoM\)"
)
_RE_COMPANY = re.compile(r"\[#([^\]]+)\]\(https?://[^)]+\)")
_RE_COMPANY_PLAIN = re.compile(r"#([A-Za-z][A-Za-z0-9 .&\-]*[A-Za-z0-9])")


def parse_us_import(caption: str) -> dict | None:
    if not caption or not _MARKER_RE.search(caption):
        return None
    text = caption.replace("：", ":").replace("*", "")
    m_item = _RE_ITEM.search(text)
    if not m_item:
        return None
    item = m_item.group(1).strip()
    if not item:
        return None
    companies = _RE_COMPANY.findall(caption)
    if not companies:
        m_co_line = re.search(r"관련기업\s*:\s*(.+)", text)
        if m_co_line:
            companies = _RE_COMPANY_PLAIN.findall(m_co_line.group(1))
    months = []
    for mm in _RE_MONTHLINE.finditer(text):
        y, mo, val, yoy, mom = mm.groups()
        months.append({
            "month": f"20{y}-{int(mo):02d}",
            "import_value_musd": float(val.replace(",", "")),
            "import_yoy": float(yoy),
            "import_mom": float(mom),
        })
    if not months:
        return None
    return {"item": item, "companies": companies, "months": months}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS us_imports (
  item TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  companies TEXT,
  import_value_musd REAL, import_yoy REAL, import_mom REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (item, month)
);
"""

_COLS = ("item", "month", "companies", "import_value_musd", "import_yoy",
         "import_mom", "chart_media", "source_message_id", "posted_at",
         "raw_text", "updated_at")


def open_us_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_us(conn: sqlite3.Connection, item: str, month_row: dict, *, companies: str,
              chart_media, source_message_id, posted_at: str, raw_text: str) -> bool:
    month = month_row.get("month") or ""
    if not item or not month:
        return False
    ex = conn.execute(
        "SELECT * FROM us_imports WHERE item=? AND month=?",
        (item, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    row = {**month_row, "item": item, "companies": companies,
           "chart_media": chart_media, "source_message_id": source_message_id,
           "posted_at": posted_at, "raw_text": raw_text}
    merged = {}
    for k in _COLS:
        nv = row.get(k)
        merged[k] = nv if nv not in (None, "") else (exd.get(k) if exd else None)
    merged["item"] = item
    merged["month"] = month
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO us_imports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_us(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM us_imports t WHERE month = "
        "(SELECT MAX(month) FROM us_imports WHERE item = t.item) "
        "ORDER BY month DESC, item ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, item: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM us_imports WHERE item=? ORDER BY month ASC",
        (item,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    parsed = parse_us_import(caption)
    if parsed is None:
        return False
    chart = (media_paths or [None])[0]
    companies_str = ", ".join(parsed["companies"])
    saved = False
    for mrow in parsed["months"]:
        ok = upsert_us(conn, parsed["item"], mrow, companies=companies_str,
                       chart_media=chart, source_message_id=source_message_id,
                       posted_at=posted_at, raw_text=caption)
        saved = saved or ok
    return saved


_CSS = """
:root{--bg:#f7f8f9;--card:#ffffff;--border:#e8e8ea;--text:#282a30;
  --item:#16171a;--muted:#8a8f98;--accent:#5e6ad2;--row:#eef0f2;--chartbd:#e8e8ea}
body.dark{--bg:#0b0c0e;--card:#141518;--border:#26272b;--text:#e2e3e6;
  --item:#f7f8f8;--muted:#8a8f98;--accent:#9aa2f0;--row:#1f2023;--chartbd:#26272b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:'Inter',-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;
  line-height:1.5;-webkit-font-smoothing:antialiased;transition:background .3s,color .3s}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 64px}
.nav{font-size:13px;margin-bottom:14px}
.nav a{color:var(--accent);text-decoration:none}.nav a:hover{text-decoration:underline}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-0.014em}
.sub{color:var(--muted);font-size:13px;margin:0 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
.us-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.us-card[open]{border-color:var(--accent)}
.us-sum{list-style:none;padding:13px 16px;
  display:flex;flex-direction:column;gap:7px}
.us-sum::-webkit-details-marker{display:none}
details.us-card > .us-sum{cursor:pointer}
details.us-card > .us-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.us-card[open] .us-sum::after{content:"▾ 접기"}
.us-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.us-item{font-weight:680;font-size:15px;color:var(--item)}
.us-mo{font-size:12px;color:var(--muted);margin-left:auto}
.us-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.us-mlabel{color:var(--muted);min-width:62px}
.us-mval{font-weight:600;color:var(--text)}
.us-delta{font-size:12px}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.us-co{font-size:11.5px;color:var(--muted);line-height:1.6}
.us-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.us-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.us-chart img{display:block;width:100%;height:auto}
.us-htbl{width:100%;border-collapse:collapse;font-size:12px}
.us-htbl th,.us-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.us-htbl th{color:var(--muted);font-weight:500}
.us-htbl td:first-child,.us-htbl th:first-child{text-align:left;color:var(--text)}
.empty{color:var(--muted);font-size:14px;padding:40px 0;text-align:center}
"""

_THEME_JS = (
    "<script>function applyDarkMode(){var h=(new Date().getUTCHours()+9)%24;"
    "document.body.classList.toggle('dark',h>=19||h<7);}"
    "applyDarkMode();setInterval(applyDarkMode,60000);</script>"
)


def _delta_str(yoy, mom) -> str:
    parts = []
    for tag, v in (("YoY", yoy), ("MoM", mom)):
        if v is None:
            continue
        cls = "up" if v > 0 else "down" if v < 0 else "flat"
        arr = "▲" if v > 0 else "▼" if v < 0 else "—"
        parts.append(f'<span class="us-delta {cls}">{tag} {arr}{v:+.1f}%</span>')
    return ("&nbsp; " + " · ".join(parts)) if parts else ""


def _hist_table(hist: list[dict]) -> str:
    rows = sorted(hist, key=lambda h: h.get("month") or "", reverse=True)
    rows = [h for h in rows if h.get("month")]
    if len(rows) < 2:
        return ""
    trs = []
    for h in rows:
        ev = h.get("import_value_musd")
        evs = f"{ev:,.1f}" if ev is not None else "—"
        yoy, mom = h.get("import_yoy"), h.get("import_mom")
        d = f"{yoy:+.1f}%" if yoy is not None else "—"
        m = f"{mom:+.1f}%" if mom is not None else "—"
        trs.append(f"<tr><td>{_html.escape(h['month'])}</td>"
                   f"<td>{evs}</td><td>{d}</td><td>{m}</td></tr>")
    return ('<table class="us-htbl"><tr><th>월</th><th>수입액(USD M$)</th>'
            '<th>YoY</th><th>MoM</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    item = _html.escape(r.get("item") or "")
    mo = _html.escape(r.get("month") or "")
    co = (r.get("companies") or "").strip()
    co_html = (f'<div class="us-co">🏭 {_html.escape(co)}</div>' if co else "")
    summary = [f'<div class="us-hd">'
               f'<span class="us-item">{item}</span>'
               f'<span class="us-mo">📅 {mo}</span></div>']
    ev = r.get("import_value_musd")
    if ev is not None:
        summary.append(
            f'<div class="us-metric"><span class="us-mlabel">💰 수입액</span>'
            f'<span class="us-mval">${ev:,.1f}M</span>{_delta_str(r.get("import_yoy"), r.get("import_mom"))}'
            f'</div>'
        )
    chart = ""
    media = r.get("chart_media") or ""
    if media:
        src = media if media.startswith(("http://", "https://", "/")) else media_prefix + media
        chart = f'<div class="us-chart"><img loading="lazy" src="{_html.escape(src)}" alt=""></div>'
    htbl = _hist_table(hist)
    return card_html("us", summary, [chart, co_html, htbl])


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    rows = list_us(conn)
    if not rows:
        return (
            "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>미국 수입 데이터</title><style>" + _CSS + "</style></head><body>"
            + _THEME_JS +
            # 빈 상태에도 back-link 필수 — 없으면 이 페이지에 들어온 사람이
            # 수출입 대시보드로 돌아갈 방법이 없다(2026-08-16 독립 조사).
            f"<div class='wrap'>{back_nav_html()}"
            "<h1>🇺🇸 미국 수입 데이터</h1>"
            "<div class='empty'>아직 수집된 미국 수입 데이터(나쁜양파)가 없습니다.</div>"
            "</div></body></html>"
        )
    cards = []
    for r in rows:
        cards.append(_card_html(r, history(conn, r["item"]), media_url_prefix))
    return (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>미국 수입 데이터</title><style>" + _CSS + "</style></head><body>"
        + _THEME_JS +
        f"<div class='wrap'>{back_nav_html()}"
        "<h1>🇺🇸 미국 수입 데이터</h1><div class='sub'>Badonions 미국 수입 캡션을 월별로 정리한 별도 페이지</div>"
        "<div class='grid'>" + "".join(cards) + "</div></div></body></html>"
    )


def regenerate(db_path: Path | str, out_path: Path | str, *, media_url_prefix: str = "../") -> None:
    conn = open_us_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
