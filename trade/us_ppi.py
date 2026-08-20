"""미국 PPI (나쁜양파) — 파서 + 저장 + 대시보드.

사용자 2026-08-19: "수출입에 나쁜양파에 미국 PPI 를 추가해줘. 다른것과 같이
rolling 하고 앞으로도 지속 수집하게." 나쁜양파 채널이 월별 미국 생산자물가
(PPI)를 **품목별**로 발행한다 — 캡션 구조는 수출/수입 소스와 같은 계열이지만
값이 금액(USD M$)이 아니라 **지수(index)** 라 별도 컬럼·라벨을 쓴다.

수집·라우팅·nav·재생성은 전부 `trade/badonion_sources.py` 레지스트리가
공통으로 몰아주므로, 이 파일은 다른 소스와 **같은 3계약**만 지키면 된다:
    parse_us_ppi(caption) -> dict | None
    open_us_ppi_db(path)  -> sqlite3.Connection
    ingest(conn, caption, *, source_message_id, posted_at, media_paths) -> bool
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trade.archive_template import (asof_footer, back_nav_html, card_html,
                                    max_ingest_iso)

# ⚠️ 마커는 '미국 PPI' — us_imports 의 `N월 수입 미국` 과 겹치지 않는다
# (레지스트리 폴백은 순차 시도라 겹치면 조용한 오저장이 된다).
_MARKER_RE = re.compile(r"미국\s*PPI")
_RE_ITEM = re.compile(r"▶️\s*(.+)")
# 값만 필수, YoY·MoM 은 선택 — 원천이 한쪽을 빠뜨린 달에 캡션 전체를 버리면
# 그 달이 통째로 유실된다(빈칸으로 남기는 편이 낫다).
_RE_MONTHLINE = re.compile(
    r"(\d{2})년\s*(\d{2})월\s*:\s*PPI\s*([\d,]+(?:\.\d+)?)"
    r"(?:\s*\(([+\-]?\d+(?:\.\d+)?)%\s*YoY\))?"
    r"(?:\s*\(([+\-]?\d+(?:\.\d+)?)%\s*MoM\))?"
)
_RE_COMPANY = re.compile(r"\[#([^\]]+)\]\(https?://[^)]+\)")
_RE_COMPANY_PLAIN = re.compile(r"#([A-Za-z][A-Za-z0-9 .&\-]*[A-Za-z0-9])")


def parse_us_ppi(caption: str) -> dict | None:
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
            "ppi_index": float(val.replace(",", "")),
            "ppi_yoy": float(yoy) if yoy is not None else None,
            "ppi_mom": float(mom) if mom is not None else None,
        })
    if not months:
        return None
    return {"item": item, "companies": companies, "months": months}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS us_ppi (
  item TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  companies TEXT,
  ppi_index REAL, ppi_yoy REAL, ppi_mom REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (item, month)
);
"""

_COLS = ("item", "month", "companies", "ppi_index", "ppi_yoy", "ppi_mom",
         "chart_media", "source_message_id", "posted_at", "raw_text",
         "updated_at")


def open_us_ppi_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_us_ppi(conn: sqlite3.Connection, item: str, month_row: dict, *,
                  companies: str, chart_media, source_message_id,
                  posted_at: str, raw_text: str) -> bool:
    month = month_row.get("month") or ""
    if not item or not month:
        return False
    ex = conn.execute("SELECT * FROM us_ppi WHERE item=? AND month=?",
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
    conn.execute(f"INSERT OR REPLACE INTO us_ppi ({','.join(_COLS)}) "
                 f"VALUES ({placeholders})", merged)
    return True


def list_us_ppi(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM us_ppi t WHERE month = "
        "(SELECT MAX(month) FROM us_ppi WHERE item = t.item) "
        "ORDER BY month DESC, item ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, item: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM us_ppi WHERE item=? ORDER BY month ASC",
                        (item,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    parsed = parse_us_ppi(caption)
    if parsed is None:
        return False
    chart = (media_paths or [None])[0]
    companies_str = ", ".join(parsed["companies"])
    saved = False
    for mrow in parsed["months"]:
        ok = upsert_us_ppi(conn, parsed["item"], mrow, companies=companies_str,
                           chart_media=chart,
                           source_message_id=source_message_id,
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
.ppi-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.ppi-card[open]{border-color:var(--accent)}
.ppi-sum{list-style:none;padding:13px 16px;display:flex;flex-direction:column;gap:7px}
.ppi-sum::-webkit-details-marker{display:none}
details.ppi-card > .ppi-sum{cursor:pointer}
details.ppi-card > .ppi-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.ppi-card[open] .ppi-sum::after{content:"▾ 접기"}
.ppi-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.ppi-item{font-weight:680;font-size:15px;color:var(--item)}
.ppi-mo{font-size:12px;color:var(--muted);margin-left:auto}
.ppi-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.ppi-mlabel{color:var(--muted);min-width:62px}
.ppi-mval{font-weight:600;color:var(--text)}
.ppi-delta{font-size:12px}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.ppi-co{font-size:11.5px;color:var(--muted);line-height:1.6}
.ppi-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.ppi-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.ppi-chart img{display:block;width:100%;height:auto}
.ppi-htbl{width:100%;border-collapse:collapse;font-size:12px}
.ppi-htbl th,.ppi-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.ppi-htbl th{color:var(--muted);font-weight:500}
.ppi-htbl td:first-child,.ppi-htbl th:first-child{text-align:left;color:var(--text)}
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
        parts.append(f'<span class="ppi-delta {cls}">{tag} {arr}{v:+.1f}%</span>')
    return ("&nbsp; " + " · ".join(parts)) if parts else ""


def _hist_table(hist: list[dict]) -> str:
    rows = sorted(hist, key=lambda h: h.get("month") or "", reverse=True)
    rows = [h for h in rows if h.get("month")]
    if len(rows) < 2:
        return ""
    trs = []
    for h in rows:
        iv = h.get("ppi_index")
        ivs = f"{iv:,.2f}" if iv is not None else "—"
        yoy, mom = h.get("ppi_yoy"), h.get("ppi_mom")
        d = f"{yoy:+.1f}%" if yoy is not None else "—"
        m = f"{mom:+.1f}%" if mom is not None else "—"
        trs.append(f"<tr><td>{_html.escape(h['month'])}</td>"
                   f"<td>{ivs}</td><td>{d}</td><td>{m}</td></tr>")
    return ('<table class="ppi-htbl"><tr><th>월</th><th>PPI 지수</th>'
            '<th>YoY</th><th>MoM</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    item = _html.escape(r.get("item") or "")
    mo = _html.escape(r.get("month") or "")
    co = (r.get("companies") or "").strip()
    co_html = (f'<div class="ppi-co">🏭 {_html.escape(co)}</div>' if co else "")
    summary = [f'<div class="ppi-hd">'
               f'<span class="ppi-item">{item}</span>'
               f'<span class="ppi-mo">📅 {mo}</span></div>']
    iv = r.get("ppi_index")
    if iv is not None:
        summary.append(
            f'<div class="ppi-metric"><span class="ppi-mlabel">📈 PPI</span>'
            f'<span class="ppi-mval">{iv:,.2f}</span>'
            f'{_delta_str(r.get("ppi_yoy"), r.get("ppi_mom"))}</div>'
        )
    # ⚠️ 관련기업은 **접힌 카드에서도 보여야 한다**(사용자 2026-08-19
    # "카드에 있는 내용은 최대한 반영되어야돼"). 텔레그램 원문은 값 바로
    # 아래에 티커를 노출하는데, 우리는 펼침 본문에 묻어 20장 그리드에서
    # 안 보였다 — 다른 7개 소스(tw/cn/jp2/th/my/ph/mx)는 이미 요약에
    # 넣고 있었고 미국 두 페이지만 어긋나 있었다(복사한 쪽이 틀린 쪽).
    if co_html:
        summary.append(co_html)
    chart = ""
    media = r.get("chart_media") or ""
    if media:
        src = (media if media.startswith(("http://", "https://", "/"))
               else media_prefix + media)
        chart = (f'<div class="ppi-chart"><img loading="lazy" '
                 f'src="{_html.escape(src)}" alt=""></div>')
    return card_html("ppi", summary, [chart, _hist_table(hist)])


_TITLE = "미국 PPI 데이터"
_H1 = "📈 미국 PPI 데이터"


def _head() -> str:
    return ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_TITLE}</title><style>" + _CSS + "</style></head><body>"
            + _THEME_JS)


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    rows = list_us_ppi(conn)
    if not rows:
        # 빈 상태에도 back-link 필수 — 없으면 이 페이지에 들어온 사람이
        # 수출입 대시보드로 돌아갈 방법이 없다.
        return (_head() + f"<div class='wrap'>{back_nav_html()}"
                f"<h1>{_H1}</h1>"
                "<div class='empty'>아직 수집된 미국 PPI 데이터(나쁜양파)가 "
                "없습니다.</div>"
                + asof_footer(0, "품목", None, max_ingest_iso(conn, "us_ppi"))
                + "</div></body></html>")
    cards = [_card_html(r, history(conn, r["item"]), media_url_prefix)
             for r in rows]
    return (_head() + f"<div class='wrap'>{back_nav_html()}"
            f"<h1>{_H1}</h1>"
            "<div class='sub'>Badonions 미국 PPI(생산자물가) 캡션을 품목·월별로 "
            "정리한 별도 페이지 · 지수는 원문 그대로(기준연도 환산 없음)</div>"
            "<div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), "품목",
                          max((r.get("month") or "") for r in rows) or None,
                          max_ingest_iso(conn, "us_ppi"))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_us_ppi_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
