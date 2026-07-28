"""중국 수출 데이터 (나쁜양파/Badonion) — 파서 + 저장 + 대시보드 (사용자 2026-07-11).

나쁜양파(t.me/Badonions) 채널이 발행하는 *중국* 관세 수출 통계 메시지는
`trade/tw_exports.py`(대만)와 완전히 동일한 포맷 — 같은 채널, 같은 구조,
마커 단어("중국" vs "대만")만 다르다. 한국(`parser.py`)·일본(`jp_exports.py`)·
대만(`tw_exports.py`) 파서가 모두 None 을 낸 뒤의 4차 fallback:

  ingest_inbox._ingest_group → (KR None) → (JP None) → (TW None) →
  parse_cn_export() → cn.db upsert
  trade.dashboard main() (sibling-regen) → regenerate() → ~/.trade/dashboard/cn.html

저장은 별도 cn.db(한국 store.db·일본 jp.db·대만 tw.db 무영향). 대만과 동일하게
메시지 1건에 최신월 헤드라인 + 과거 히스토리가 함께 오고, 회사가 여러 개라
콤마 join 텍스트로 저장. 차트 이미지는 media/<날짜>/<uid>.jpg 상대경로.

TW/CN 이 같은 채널·같은 포맷이라 파서/저장/렌더 로직은 tw_exports.py 를
그대로 미러링(마커만 교체) — 코드베이스 기존 컨벤션(시장별 완전 독립 모듈,
`trade/CLAUDE.md`·CLAUDE_REFERENCE.md 참조)을 따라 공용 베이스로 합치지 않음.

샘플 caption(마크다운 볼드 `**` 포함, Telethon raw text):
    **🇨🇳 4월 수출 중국**

    **▶️ 전기차**

    **26년04월: $4,688.8M  (+32.0% YoY)  (+27.4% MoM)**

    관련기업: [#CATL](https://...)  [#BYD](https://...)

    최근 추이 (단위: USD M$)
    26년03월: $3,681.0M  (+63.2% YoY)  (+21.8% MoM)
    26년02월: $3,022.2M  (+98.3% YoY)  (-24.6% MoM)
"""
from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_MARKER_RE = re.compile(r"\d+\s*월\s*수출\s*중국")
_RE_ITEM = re.compile(r"▶️\s*(.+)")
_RE_MONTHLINE = re.compile(
    r"(\d{2})년(\d{2})월\s*:\s*\$([\d,]+(?:\.\d+)?)M\s*"
    r"\(([+\-]?\d+(?:\.\d+)?)%\s*YoY\)\s*"
    r"\(([+\-]?\d+(?:\.\d+)?)%\s*MoM\)"
)
_RE_COMPANY = re.compile(r"\[#([^\]]+)\]\(https?://[^)]+\)")


def parse_cn_export(caption: str) -> dict | None:
    """나쁜양파 중국 수출 메시지 → dict(item, companies, months[]).
    마커/품목/월라인 없으면 None(진짜 미인식 — KR·JP·TW 파서 다음 4차
    fallback이라 여기서 None 이면 그냥 unparseable로 버려짐)."""
    if not caption or not _MARKER_RE.search(caption):
        return None
    text = caption.replace("：", ":").replace("*", "")  # 마크다운 볼드 제거
    m_item = _RE_ITEM.search(text)
    if not m_item:
        return None
    item = m_item.group(1).strip()
    if not item:
        return None
    companies = _RE_COMPANY.findall(caption)  # 원문(캡션)에서 — '*' 제거 전 링크 보존
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
# Storage (별도 cn.db — 한국 store.db·일본 jp.db·대만 tw.db 무영향)
# ─────────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cn_exports (
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


def open_cn_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_cn(conn: sqlite3.Connection, item: str, month_row: dict, *,
              companies: str, chart_media, source_message_id, posted_at: str,
              raw_text: str) -> bool:
    """(품목, 월) 단위 저장(필드 보존 병합) — 한국 alerts/일본 jp_exports/대만
    tw_exports 와 동일 불변식: 부분 재전송이 기존 good 필드를 null 로 덮지 않음."""
    month = month_row.get("month") or ""
    if not item or not month:
        return False
    ex = conn.execute(
        "SELECT * FROM cn_exports WHERE item=? AND month=?",
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
        f"INSERT OR REPLACE INTO cn_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_cn(conn: sqlite3.Connection) -> list[dict]:
    """대시보드용 — 품목별 최신월 1행."""
    rows = conn.execute(
        "SELECT * FROM cn_exports t WHERE month = "
        "(SELECT MAX(month) FROM cn_exports WHERE item = t.item) "
        "ORDER BY month DESC, item ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, item: str) -> list[dict]:
    """품목의 월별 전체 이력(오름차순)."""
    rows = conn.execute(
        "SELECT * FROM cn_exports WHERE item=? ORDER BY month ASC",
        (item,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    """parse + 메시지 내 전 개월(최신+히스토리) upsert. 차트는 media_paths[0].
    반환: 1개 이상 월 행이라도 저장했으면 True."""
    parsed = parse_cn_export(caption)
    if parsed is None:
        return False
    chart = (media_paths or [None])[0]
    companies_str = ", ".join(parsed["companies"])
    saved = False
    for mrow in parsed["months"]:
        ok = upsert_cn(conn, parsed["item"], mrow, companies=companies_str,
                       chart_media=chart, source_message_id=source_message_id,
                       posted_at=posted_at, raw_text=caption)
        saved = saved or ok
    return saved


# ─────────────────────────────────────────────────────────────────────────
# Dashboard render (cn.html) — tw_exports.py 톤·CSS 미러
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
.cn-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.cn-card[open]{border-color:var(--accent)}
.cn-sum{list-style:none;cursor:pointer;padding:13px 16px;
  display:flex;flex-direction:column;gap:7px}
.cn-sum::-webkit-details-marker{display:none}
.cn-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.cn-card[open] .cn-sum::after{content:"▾ 접기"}
.cn-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.cn-item{font-weight:680;font-size:15px;color:var(--item)}
.cn-mo{font-size:12px;color:var(--muted);margin-left:auto}
.cn-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.cn-mlabel{color:var(--muted);min-width:62px}
.cn-mval{font-weight:600;color:var(--text)}
.cn-delta{font-size:12px}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.cn-co{font-size:11.5px;color:var(--muted);line-height:1.6}
.cn-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.cn-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.cn-chart img{display:block;width:100%;height:auto}
.cn-htbl{width:100%;border-collapse:collapse;font-size:12px}
.cn-htbl th,.cn-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.cn-htbl th{color:var(--muted);font-weight:500}
.cn-htbl td:first-child,.cn-htbl th:first-child{text-align:left;color:var(--text)}
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
        parts.append(f'<span class="cn-delta {cls}">{tag} {arr}{v:+.1f}%</span>')
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
    return ('<table class="cn-htbl"><tr><th>월</th><th>수출액(USD M$)</th>'
            '<th>YoY</th><th>MoM</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    item = _html.escape(r.get("item") or "")
    mo = _html.escape(r.get("month") or "")
    co = (r.get("companies") or "").strip()
    co_html = (f'<div class="cn-co">🏭 {_html.escape(co)}</div>' if co else "")
    summary = [f'<summary class="cn-sum">'
               f'<div class="cn-hd"><span class="cn-item">{item}</span>'
               f'<span class="cn-mo">📅 {mo}</span></div>']
    ev = r.get("export_value_musd")
    if ev is not None:
        summary.append(
            f'<div class="cn-metric"><span class="cn-mlabel">💰 수출액</span>'
            f'<span class="cn-mval">${ev:,.1f}M</span>'
            f'{_delta_str(r.get("export_yoy"), r.get("export_mom"))}</div>')
    summary.append(co_html)
    summary.append("</summary>")
    detail = []
    chart = r.get("chart_media")
    if chart:
        detail.append(f'<div class="cn-chart"><img loading="lazy" '
                      f'src="{_html.escape(media_prefix + chart)}" alt="{item}"></div>')
    detail.append(_hist_table(hist))
    detail_html = (f'<div class="cn-detail">{"".join(d for d in detail if d)}</div>'
                   if any(detail) else "")
    return f'<details class="cn-card">{"".join(summary)}{detail_html}</details>'


def render_html(conn: sqlite3.Connection, media_url_prefix: str = "../") -> str:
    items = list_cn(conn)
    months = sorted({r.get("month") for r in items if r.get("month")})
    latest = months[-1] if months else ""
    if items:
        cards = "".join(
            _card_html(r, history(conn, r["item"]), media_url_prefix) for r in items)
        body = f'<div class="grid">{cards}</div>'
    else:
        body = ('<div class="empty">아직 수집된 중국 수출 데이터가 없습니다. '
                '나쁜양파 채널의 "N월 수출 중국" 메시지를 포워드하면 표시됩니다.</div>')
    return (
        "<!doctype html><html lang='ko'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>🐼 중국 수출 데이터</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        '<div class="nav"><a href="./">← 수출입 대시보드</a></div>'
        "<h1>🐼 중국 수출 데이터</h1>"
        f'<p class="sub">출처 나쁜양파(관세 중국) · 품목별 월 수출액(USD M$) · '
        f'{len(items)}개 품목'
        f'{" · 최신 " + _html.escape(latest) if latest else ""} · 카드 클릭 = 차트·월별 펼침</p>'
        f"{body}</div>{_THEME_JS}</body></html>"
    )


def regenerate(cn_db_path: str | Path, out_path: str | Path,
               media_url_prefix: str = "../") -> None:
    """cn.db → cn.html. 데이터 없어도 빈 페이지 생성(링크 404 방지)."""
    conn = open_cn_db(cn_db_path)
    try:
        html = render_html(conn, media_url_prefix)
    finally:
        conn.close()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
