"""일본 수출 데이터 — 두 번째 소스 (나쁜양파/Badonion) — 파서+저장+대시보드
(사용자 2026-07-11 — "일본은 2개의 서로 다른 채널에서 2개의 대쉬보드로 갈거야").

기존 `trade/jp_exports.py` 는 BeOn 채널(t.me/BeOn_BeClear)의 일본 수출 포맷
("일본 수출 데이터 업데이트: ...", 단가·중량 포함, 월 1행씩 누적)을 처리한다.
나쁜양파(t.me/Badonions) 채널도 *별도로* 일본 수출 통계를 발행하는데, 포맷이
완전히 다르다 — 대만/중국과 동일한 구조("N월 수출 일본" 헤더, 관련기업 여러
개, 메시지 1건에 최신월+과거 히스토리 동봉). 두 소스는 운영자가 의도적으로
분리 유지하길 원해서(같은 나라, 다른 채널, 다른 대시보드) 기존 jp_exports.py
를 건드리지 않고 완전히 독립된 모듈+DB+페이지로 추가한다:

  ingest_inbox._ingest_group → (KR None)→(JP/BeOn None)→(TW None)→(CN None)→
  parse_jp2_export() → jp2.db upsert
  trade.dashboard main() (sibling-regen) → regenerate() → ~/.trade/dashboard/jp2.html

저장은 별도 jp2.db(store.db·jp.db·tw.db·cn.db 전부 무영향). 파서/저장/렌더
로직은 tw_exports.py/cn_exports.py 를 그대로 미러링(마커만 "일본") — 코드베이스
기존 컨벤션(시장/소스별 완전 독립 모듈)을 따라 jp_exports.py 와도 합치지 않음.

샘플 caption(Telethon raw text, 사용자 2026-07-11 스크린샷):
    🇯🇵 5월 수출 일본

    ▶️ 디젤 승용차(1500cc 초과~2500cc 이하)

    26년05월: $17.6M  (+41.7% YoY)  (+13.8% MoM)

    관련기업: #Mazda Motor

    최근 추이 (단위: USD M$)
    26년04월: $15.5M  (+14.1% YoY)  (-7.0% MoM)
    26년03월: $16.6M  (+4.2% YoY)  (-1.0% MoM)
"""
from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from trade.archive_template import asof_footer, back_nav_html, max_ingest_iso
from trade.archive_template import card_html

_MARKER_RE = re.compile(r"\d+\s*월\s*수출\s*일본")
_RE_ITEM = re.compile(r"▶️\s*(.+)")
_RE_MONTHLINE = re.compile(
    r"(\d{2})년(\d{2})월\s*:\s*\$([\d,]+(?:\.\d+)?)M\s*"
    r"\(([+\-]?\d+(?:\.\d+)?)%\s*YoY\)\s*"
    r"\(([+\-]?\d+(?:\.\d+)?)%\s*MoM\)"
)
_RE_COMPANY = re.compile(r"\[#([^\]]+)\]\(https?://[^)]+\)")
# 일부 나쁜양파 일본 메시지는 회사 링크가 마크다운 없이 평문 "#Mazda Motor"
# 로만 옴(스크린샷 확인) — 대만/중국은 항상 링크였던 것과 차이. 링크 매칭이
# 0건이면 평문 해시태그로 2차 시도.
_RE_COMPANY_PLAIN = re.compile(r"#([A-Za-z][A-Za-z0-9 .&\-]*[A-Za-z0-9])")


def parse_jp2_export(caption: str) -> dict | None:
    """나쁜양파 일본 수출 메시지 → dict(item, companies, months[]).
    마커/품목/월라인 없으면 None(진짜 미인식 — KR·JP(BeOn)·TW·CN 파서 다음
    5차 fallback이라 여기서 None 이면 그냥 unparseable로 버려짐)."""
    if not caption or not _MARKER_RE.search(caption):
        return None
    text = caption.replace("：", ":").replace("*", "")  # 마크다운 볼드 제거
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
            "export_value_musd": float(val.replace(",", "")),
            "export_yoy": float(yoy),
            "export_mom": float(mom),
        })
    if not months:
        return None
    return {"item": item, "companies": companies, "months": months}


# ─────────────────────────────────────────────────────────────────────────
# Storage (별도 jp2.db — store.db·jp.db·tw.db·cn.db 전부 무영향)
# ─────────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jp2_exports (
  item TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  companies TEXT,
  export_value_musd REAL, export_yoy REAL, export_mom REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (item, month)
);
"""

_COLS = ("item", "month", "companies", "export_value_musd", "export_yoy",
         "export_mom", "chart_media", "source_message_id", "posted_at",
         "raw_text", "updated_at")


def open_jp2_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_jp2(conn: sqlite3.Connection, item: str, month_row: dict, *,
               companies: str, chart_media, source_message_id, posted_at: str,
               raw_text: str) -> bool:
    """(품목, 월) 단위 저장(필드 보존 병합) — tw_exports/cn_exports 와 동일
    불변식: 부분 재전송이 기존 good 필드를 null 로 덮지 않음."""
    month = month_row.get("month") or ""
    if not item or not month:
        return False
    ex = conn.execute(
        "SELECT * FROM jp2_exports WHERE item=? AND month=?",
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
        f"INSERT OR REPLACE INTO jp2_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_jp2(conn: sqlite3.Connection) -> list[dict]:
    """대시보드용 — 품목별 최신월 1행."""
    rows = conn.execute(
        "SELECT * FROM jp2_exports t WHERE month = "
        "(SELECT MAX(month) FROM jp2_exports WHERE item = t.item) "
        "ORDER BY month DESC, item ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, item: str) -> list[dict]:
    """품목의 월별 전체 이력(오름차순)."""
    rows = conn.execute(
        "SELECT * FROM jp2_exports WHERE item=? ORDER BY month ASC",
        (item,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    """parse + 메시지 내 전 개월(최신+히스토리) upsert. 차트는 media_paths[0].
    반환: 1개 이상 월 행이라도 저장했으면 True."""
    parsed = parse_jp2_export(caption)
    if parsed is None:
        return False
    chart = (media_paths or [None])[0]
    companies_str = ", ".join(parsed["companies"])
    saved = False
    for mrow in parsed["months"]:
        ok = upsert_jp2(conn, parsed["item"], mrow, companies=companies_str,
                        chart_media=chart, source_message_id=source_message_id,
                        posted_at=posted_at, raw_text=caption)
        saved = saved or ok
    return saved


# ─────────────────────────────────────────────────────────────────────────
# Dashboard render (jp2.html) — tw_exports.py 톤·CSS 미러
# ─────────────────────────────────────────────────────────────────────────
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
.jp2-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.jp2-card[open]{border-color:var(--accent)}
.jp2-sum{list-style:none;padding:13px 16px;
  display:flex;flex-direction:column;gap:7px}
.jp2-sum::-webkit-details-marker{display:none}
details.jp2-card > .jp2-sum{cursor:pointer}
details.jp2-card > .jp2-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.jp2-card[open] .jp2-sum::after{content:"▾ 접기"}
.jp2-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.jp2-item{font-weight:680;font-size:15px;color:var(--item)}
.jp2-mo{font-size:12px;color:var(--muted);margin-left:auto}
.jp2-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.jp2-mlabel{color:var(--muted);min-width:62px}
.jp2-mval{font-weight:600;color:var(--text)}
.jp2-delta{font-size:12px}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.jp2-co{font-size:11.5px;color:var(--muted);line-height:1.6}
.jp2-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.jp2-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.jp2-chart img{display:block;width:100%;height:auto}
.jp2-htbl{width:100%;border-collapse:collapse;font-size:12px}
.jp2-htbl th,.jp2-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.jp2-htbl th{color:var(--muted);font-weight:500}
.jp2-htbl td:first-child,.jp2-htbl th:first-child{text-align:left;color:var(--text)}
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
        parts.append(f'<span class="jp2-delta {cls}">{tag} {arr}{v:+.1f}%</span>')
    return ("&nbsp; " + " · ".join(parts)) if parts else ""


def _hist_table(hist: list[dict]) -> str:
    rows = sorted(hist, key=lambda h: h.get("month") or "", reverse=True)
    rows = [h for h in rows if h.get("month")]
    if len(rows) < 2:
        return ""
    trs = []
    for h in rows:
        ev = h.get("export_value_musd")
        evs = f"{ev:,.1f}" if ev is not None else "—"
        yoy, mom = h.get("export_yoy"), h.get("export_mom")
        d = f"{yoy:+.1f}%" if yoy is not None else "—"
        m = f"{mom:+.1f}%" if mom is not None else "—"
        trs.append(f"<tr><td>{_html.escape(h['month'])}</td>"
                   f"<td>{evs}</td><td>{d}</td><td>{m}</td></tr>")
    return ('<table class="jp2-htbl"><tr><th>월</th><th>수출액(USD M$)</th>'
            '<th>YoY</th><th>MoM</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    item = _html.escape(r.get("item") or "")
    mo = _html.escape(r.get("month") or "")
    co = (r.get("companies") or "").strip()
    co_html = (f'<div class="jp2-co">🏭 {_html.escape(co)}</div>' if co else "")
    summary = [f''
               f'<div class="jp2-hd"><span class="jp2-item">{item}</span>'
               f'<span class="jp2-mo">📅 {mo}</span></div>']
    ev = r.get("export_value_musd")
    if ev is not None:
        summary.append(
            f'<div class="jp2-metric"><span class="jp2-mlabel">💰 수출액</span>'
            f'<span class="jp2-mval">${ev:,.1f}M</span>'
            f'{_delta_str(r.get("export_yoy"), r.get("export_mom"))}</div>')
    summary.append(co_html)
    detail = []
    chart = r.get("chart_media")
    if chart:
        detail.append(f'<div class="jp2-chart"><img loading="lazy" '
                      f'src="{_html.escape(media_prefix + chart)}" alt="{item}"></div>')
    detail.append(_hist_table(hist))
    return card_html("jp2", summary, detail)


def render_html(conn: sqlite3.Connection, media_url_prefix: str = "../") -> str:
    items = list_jp2(conn)
    months = sorted({r.get("month") for r in items if r.get("month")})
    latest = months[-1] if months else ""
    if items:
        cards = "".join(
            _card_html(r, history(conn, r["item"]), media_url_prefix) for r in items)
        body = f'<div class="grid">{cards}</div>'
    else:
        body = ('<div class="empty">아직 수집된 일본 수출 데이터(나쁜양파)가 없습니다. '
                '나쁜양파 채널의 "N월 수출 일본" 메시지를 포워드하면 표시됩니다.</div>')
    return (
        "<!doctype html><html lang='ko'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>🎌 일본 수출 데이터 (나쁜양파)</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        f"{back_nav_html()}"
        "<h1>🎌 일본 수출 데이터 (나쁜양파)</h1>"
        f'<p class="sub">출처 나쁜양파(관세 일본, BeOn과 별도 소스) · 품목별 월 수출액(USD M$) · '
        f'{len(items)}개 품목'
        f'{" · 최신 " + _html.escape(latest) if latest else ""} · 카드 클릭 = 차트·월별 펼침</p>'
        f"{body}"
        + asof_footer(len(items), "품목", latest or None,
                      max_ingest_iso(conn, "jp2_exports"))
        + f"</div>{_THEME_JS}</body></html>"
    )


def regenerate(jp2_db_path: str | Path, out_path: str | Path,
               media_url_prefix: str = "../") -> None:
    """jp2.db → jp2.html. 데이터 없어도 빈 페이지 생성(링크 404 방지)."""
    conn = open_jp2_db(jp2_db_path)
    try:
        html = render_html(conn, media_url_prefix)
    finally:
        conn.close()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
