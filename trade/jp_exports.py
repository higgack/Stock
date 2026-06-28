"""일본 수출 데이터 (BeOn) — 파서 + 저장 + 대시보드 (사용자 2026-06-27).

BeOn 이 새로 보내는 *일본* 수출 통계 메시지는 한국 관세청 알림과 포맷이 완전히
다르다(품목별 월 수출액 십억엔·단가 천엔/KG·YoY/MoM, 한국 회사매핑 없음). 한국
파서(`trade.parser.parse_caption`)는 이 메시지를 인식 못 해 None → unparseable 로
버린다. 이 모듈이 그 fallback 을 담당:

  ingest_inbox._ingest_group → (한국 파서 None) → parse_jp_export() → jp.db upsert
  trade.dashboard main() (sibling-regen) → regenerate() → ~/.trade/dashboard/jp.html

저장은 별도 jp.db(한국 store.db 무영향). 품목(item) 단위 최신 스냅샷 1행(재포워드 =
최신월 기준 갱신, 과거월 재포워드는 무시). 차트 PNG 는 한국 알림과 동일하게
media/<날짜>/<uid>.jpg 상대경로로 저장(렌더 시 '../' prefix).

샘플 caption:
    📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)
    🏭 디스코 (DISCO)
    ─────
    📅 최신 월: 2026-05
    💰 수출액: 27.6십억 엔
       YoY ▲ +16.5% / MoM ▲ +1.8%
    📦 수출 단가: 19.3천엔/KG
       YoY ▼ -5.2% / MoM ▼ -11.6%
"""
from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_MARKER = "일본 수출 데이터 업데이트"

_RE_ITEM = re.compile(r"일본\s*수출\s*데이터\s*업데이트\s*:\s*(.+)")
_RE_COMPANY = re.compile(r"^\s*🏭\s*(.+?)\s*$")
_RE_MONTH = re.compile(r"최신\s*월\s*:\s*(\d{4})\s*[-./]\s*(\d{1,2})")
_RE_VALUE = re.compile(r"수출액\s*:\s*([\d,]+(?:\.\d+)?)\s*십억")
_RE_PRICE = re.compile(r"수출\s*단가\s*:\s*([\d,]+(?:\.\d+)?)\s*천엔")
# 'YoY ▲ +16.5% / MoM ▼ -1.8%' — 화살표(선택) + 부호숫자. 화살표/부호 양쪽 허용.
_RE_YOYMOM = re.compile(
    r"YoY\s*([▲▼△▽]?)\s*([+\-]?\d+(?:\.\d+)?)\s*%"
    r"\s*/\s*MoM\s*([▲▼△▽]?)\s*([+\-]?\d+(?:\.\d+)?)\s*%"
)


def _signed(arrow: str, num: str) -> float:
    """부호숫자 우선, 없으면 화살표(▼=하락)로 부호 결정."""
    v = float(num)
    if num.lstrip().startswith(("+", "-")):
        return v
    if arrow in ("▼", "▽"):
        return -abs(v)
    return abs(v)


# ─────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────
def parse_jp_export(caption: str) -> dict | None:
    """BeOn 일본 수출 메시지 → dict. 마커/항목 없으면 None(한국 파서로 폴백 안 함 —
    이미 한국 파서가 None 낸 뒤 호출되므로 여기서 None = 진짜 미인식)."""
    if not caption or _MARKER not in caption:
        return None
    text = caption.replace("：", ":")
    m_item = _RE_ITEM.search(text)
    if not m_item:
        return None
    item = m_item.group(1).strip()
    if not item:
        return None
    out: dict = {
        "item": item, "company": None, "latest_month": None,
        "export_value_bn": None, "export_yoy": None, "export_mom": None,
        "price_per_kg": None, "price_yoy": None, "price_mom": None,
    }
    cur = None  # 직전 메트릭(export/price) — 다음 YoY/MoM 줄을 귀속
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        mc = _RE_COMPANY.match(line)
        if mc and out["company"] is None and out["latest_month"] is None:
            out["company"] = mc.group(1).strip()
            continue
        mm = _RE_MONTH.search(line)
        if mm:
            out["latest_month"] = f"{int(mm.group(1)):04d}-{int(mm.group(2)):02d}"
            continue
        mv = _RE_VALUE.search(line)
        if mv:
            out["export_value_bn"] = float(mv.group(1).replace(",", ""))
            cur = "export"
            continue
        mp = _RE_PRICE.search(line)
        if mp:
            out["price_per_kg"] = float(mp.group(1).replace(",", ""))
            cur = "price"
            continue
        my = _RE_YOYMOM.search(line)
        if my and cur:
            yoy = _signed(my.group(1), my.group(2))
            mom = _signed(my.group(3), my.group(4))
            if cur == "export":
                out["export_yoy"], out["export_mom"] = yoy, mom
            else:
                out["price_yoy"], out["price_mom"] = yoy, mom
            cur = None
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────
# Storage (별도 jp.db — 한국 store.db 무영향)
# ─────────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jp_exports (
  item TEXT PRIMARY KEY,
  company TEXT,
  latest_month TEXT,
  export_value_bn REAL, export_yoy REAL, export_mom REAL,
  price_per_kg REAL, price_yoy REAL, price_mom REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT
);
"""

_COLS = ("item", "company", "latest_month", "export_value_bn", "export_yoy",
         "export_mom", "price_per_kg", "price_yoy", "price_mom", "chart_media",
         "source_message_id", "posted_at", "raw_text", "updated_at")


def open_jp_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_jp(conn: sqlite3.Connection, row: dict) -> bool:
    """품목 단위 최신 스냅샷 저장(필드 보존 병합). 반환: 저장했으면 True.

    - 기존에 월이 있는데 새 메시지가 더 과거이거나 월이 없으면 skip(최신 보존).
    - 그 외엔 병합: 새 값 우선, 새 값이 없는 필드는 기존 값 유지 → 부분/truncated
      재포워드(예 차트·단가 누락)가 good 데이터를 null 로 클로버하지 않음
      (INSERT OR REPLACE 는 전체 행 치환이므로 병합 후 1회 치환)."""
    item = (row.get("item") or "").strip()
    if not item:
        return False
    new_month = row.get("latest_month") or ""
    ex = conn.execute(
        "SELECT * FROM jp_exports WHERE item=?", (item,)).fetchone()
    exd = dict(ex) if ex is not None else None
    if exd is not None:
        ex_month = exd.get("latest_month") or ""
        if ex_month and (not new_month or new_month < ex_month):
            return False  # 더 과거이거나 월 미상 — 기존 최신 보존(클로버 차단)
    merged = {}
    for k in _COLS:
        nv = row.get(k)
        if nv is None or nv == "":
            merged[k] = exd.get(k) if exd else None
        else:
            merged[k] = nv
    merged["item"] = item
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO jp_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_jp(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jp_exports ORDER BY latest_month DESC, item ASC").fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    """parse + upsert. 차트는 media_paths[0]. 반환: 저장 여부."""
    parsed = parse_jp_export(caption)
    if parsed is None:
        return False
    parsed["chart_media"] = (media_paths or [None])[0]
    parsed["source_message_id"] = source_message_id
    parsed["posted_at"] = posted_at
    parsed["raw_text"] = caption
    return upsert_jp(conn, parsed)


# ─────────────────────────────────────────────────────────────────────────
# Dashboard render (jp.html)
# ─────────────────────────────────────────────────────────────────────────
_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0b0c0e;color:#e2e3e6;
  font-family:'Inter',-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;
  line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 64px}
.nav{font-size:13px;margin-bottom:14px}
.nav a{color:#7c84e8;text-decoration:none}.nav a:hover{text-decoration:underline}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-0.014em}
.sub{color:#8a8f98;font-size:13px;margin:0 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
.jp-card{background:#141518;border:1px solid #26272b;border-radius:10px;
  padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.jp-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.jp-item{font-weight:680;font-size:15px;color:#f7f8f8}
.jp-co{font-size:12px;color:#8a8f98}
.jp-mo{font-size:12px;color:#8a8f98;margin-left:auto}
.jp-metric{display:flex;align-items:baseline;gap:8px;font-size:13px}
.jp-mlabel{color:#8a8f98;min-width:62px}
.jp-mval{font-weight:600;color:#e2e3e6}
.jp-delta{font-size:12px}
.up{color:#e5484d}.down{color:#5c9ce6}.flat{color:#8a8f98}
.jp-chart{margin-top:6px;border:1px solid #26272b;border-radius:8px;overflow:hidden}
.jp-chart img{display:block;width:100%;height:auto}
.empty{color:#8a8f98;font-size:14px;padding:40px 0;text-align:center}
"""


def _delta_html(label: str, yoy, mom) -> str:
    """YoY/MoM 한 줄(없으면 ''). 한국 관례: 상승=빨강▲ / 하락=파랑▼."""
    if yoy is None and mom is None:
        return ""
    def seg(tag: str, v) -> str:
        if v is None:
            return ""
        cls = "up" if v > 0 else "down" if v < 0 else "flat"
        arr = "▲" if v > 0 else "▼" if v < 0 else "—"
        return f'<span class="jp-delta {cls}">{tag} {arr} {v:+.1f}%</span>'
    parts = " · ".join(s for s in (seg("YoY", yoy), seg("MoM", mom)) if s)
    return f'<div class="jp-metric"><span class="jp-mlabel"></span>{parts}</div>'


def _card_html(r: dict, media_prefix: str) -> str:
    item = _html.escape(r.get("item") or "")
    co = r.get("company")
    co_html = f'<span class="jp-co">🏭 {_html.escape(co)}</span>' if co else ""
    mo = _html.escape(r.get("latest_month") or "")
    rows = [f'<div class="jp-hd"><span class="jp-item">{item}</span>{co_html}'
            f'<span class="jp-mo">📅 {mo}</span></div>']
    ev = r.get("export_value_bn")
    if ev is not None:
        rows.append(
            f'<div class="jp-metric"><span class="jp-mlabel">💰 수출액</span>'
            f'<span class="jp-mval">{ev:,.1f}십억 엔</span></div>')
        d = _delta_html("", r.get("export_yoy"), r.get("export_mom"))
        if d:
            rows.append(d)
    pr = r.get("price_per_kg")
    if pr is not None:
        rows.append(
            f'<div class="jp-metric"><span class="jp-mlabel">📦 수출단가</span>'
            f'<span class="jp-mval">{pr:,.1f}천엔/KG</span></div>')
        d = _delta_html("", r.get("price_yoy"), r.get("price_mom"))
        if d:
            rows.append(d)
    chart = r.get("chart_media")
    if chart:
        rows.append(f'<div class="jp-chart"><img loading="lazy" '
                    f'src="{_html.escape(media_prefix + chart)}" alt="{item}"></div>')
    return f'<div class="jp-card">{"".join(rows)}</div>'


def render_html(conn: sqlite3.Connection, media_url_prefix: str = "../") -> str:
    items = list_jp(conn)
    months = sorted({r.get("latest_month") for r in items if r.get("latest_month")})
    latest = months[-1] if months else ""
    if items:
        cards = "".join(_card_html(r, media_url_prefix) for r in items)
        body = f'<div class="grid">{cards}</div>'
    else:
        body = ('<div class="empty">아직 수집된 일본 수출 데이터가 없습니다. '
                'BeOn 채널의 "일본 수출 데이터 업데이트" 메시지를 포워드하면 표시됩니다.</div>')
    return (
        "<!doctype html><html lang='ko'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>🇯🇵 일본 수출 데이터</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        '<div class="nav"><a href="index.html">← 한국 수출입 대시보드</a></div>'
        "<h1>🇯🇵 일본 수출 데이터</h1>"
        f'<p class="sub">출처 BeOn · 품목별 월 수출액(십억 엔)·단가(천엔/KG) · '
        f'{len(items)}개 품목'
        f'{" · 최신 " + _html.escape(latest) if latest else ""}</p>'
        f"{body}</div></body></html>"
    )


def regenerate(jp_db_path: str | Path, out_path: str | Path,
               media_url_prefix: str = "../") -> None:
    """jp.db → jp.html. 데이터 없어도 빈 페이지 생성(링크 404 방지)."""
    conn = open_jp_db(jp_db_path)
    try:
        html = render_html(conn, media_url_prefix)
    finally:
        conn.close()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
