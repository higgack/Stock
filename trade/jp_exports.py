"""일본 수출 데이터 (BeOn) — 파서 + 저장 + 대시보드 (사용자 2026-06-27).

BeOn 이 새로 보내는 *일본* 수출 통계 메시지는 한국 관세청 알림과 포맷이 완전히
다르다(품목별 월 수출액 십억엔·단가 천엔/KG·YoY/MoM, 한국 회사매핑 없음). 한국
파서(`trade.parser.parse_caption`)는 이 메시지를 인식 못 해 None → unparseable 로
버린다. 이 모듈이 그 fallback 을 담당:

  ingest_inbox._ingest_group → (한국 파서 None) → parse_jp_export() → jp.db upsert
  trade.dashboard main() (sibling-regen) → regenerate() → ~/.trade/dashboard/jp.html

저장은 별도 jp.db(한국 store.db 무영향). (품목, 월) 단위 월별 누적(한국 alerts 처럼
이어 저장) — 대시보드는 품목별 최신월 1행 표시, 과거월은 history() 로 보존(트렌드).
차트 PNG 는 한국 알림과 동일하게 media/<날짜>/<uid>.jpg 상대경로로 저장(렌더 '../').

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
_RE_MONTH = re.compile(r"최신\s*월\s*:\s*(\d{4})\s*[-./]\s*(\d{1,2})")
_RE_VALUE = re.compile(r"수출액\s*:\s*([\d,]+(?:\.\d+)?)\s*십억")
_RE_PRICE = re.compile(r"수출\s*단가\s*:\s*([\d,]+(?:\.\d+)?)\s*천엔")
# 'YoY 🔺 +16.5% / MoM 🔻 -1.8%' — 화살표는 ▲▼(기하) or 🔺🔻(이모지) 등 다양 →
# 숫자 앞 gap 을 비숫자(부호·/ 제외)로 흡수해 어떤 화살표든 통과. gap 의 하락
# 화살표로 부호 보강(부호숫자 우선). (옛 [▲▼]? 클래스는 🔺🔻 이모지 미매치로 누락됐음.)
_RE_YOYMOM = re.compile(
    r"YoY([^\d/+\-]*)([+\-]?\d+(?:\.\d+)?)\s*%"
    r"\s*/\s*MoM([^\d/+\-]*)([+\-]?\d+(?:\.\d+)?)\s*%"
)
_SEP_CHARS = set("─—–-_=· \t")            # 구분선 문자
_DOWN_ARROWS = ("▼", "▽", "🔻", "🔽", "↓")  # 하락 표식(부호 없을 때 폴백)


def _signed(gap: str, num: str) -> float:
    """부호숫자 우선, 없으면 gap 의 하락 화살표로 음수 판정."""
    v = float(num)
    if num.lstrip().startswith(("+", "-")):
        return v
    if any(d in (gap or "") for d in _DOWN_ARROWS):
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
        if _MARKER in line:                       # 타이틀(이미 item 추출) 스킵
            continue
        if all(c in _SEP_CHARS for c in line):    # 구분선(─────) 스킵
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
        if my:
            if cur == "export":
                out["export_yoy"] = _signed(my.group(1), my.group(2))
                out["export_mom"] = _signed(my.group(3), my.group(4))
            elif cur == "price":
                out["price_yoy"] = _signed(my.group(1), my.group(2))
                out["price_mom"] = _signed(my.group(3), my.group(4))
            cur = None
            continue                              # YoY 줄은 회사로 오인 금지
        # 최신월 전 비메트릭·비구분선 줄 = 관련회사(🏭 등 선두 이모지/기호 제거).
        if out["company"] is None and out["latest_month"] is None:
            co = re.sub(r"^[\W_]+", "", line).strip()   # \W 는 한글/영숫자 보존
            if co:
                out["company"] = co
    return out


# ─────────────────────────────────────────────────────────────────────────
# Storage (별도 jp.db — 한국 store.db 무영향)
# ─────────────────────────────────────────────────────────────────────────
# 월별 누적(한국 alerts 처럼 이어 저장): PK (item, latest_month) → 매월 새 행.
# 대시보드는 품목별 최신월 1행만 표시(list_jp). 과거월은 history() 로 보존(트렌드).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jp_exports (
  item TEXT NOT NULL,
  latest_month TEXT NOT NULL DEFAULT '',
  company TEXT,
  export_value_bn REAL, export_yoy REAL, export_mom REAL,
  price_per_kg REAL, price_yoy REAL, price_mom REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (item, latest_month)
);
"""

_COLS = ("item", "latest_month", "company", "export_value_bn", "export_yoy",
         "export_mom", "price_per_kg", "price_yoy", "price_mom", "chart_media",
         "source_message_id", "posted_at", "raw_text", "updated_at")


def open_jp_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    info = conn.execute("PRAGMA table_info(jp_exports)").fetchall()
    pk_cols = [r for r in info if r[5] > 0]   # r[5] = pk position(0=비PK)
    if info and len(pk_cols) < 2:
        # 옛 단일 PK(item, 최신만) → 월별 누적 PK(item,latest_month) 마이그레이션.
        # 기존 행(현재월 스냅샷) 보존 — 새 스키마로 복사(히스토리는 현재월부터 시작).
        old_cols = [r[1] for r in info]
        common = [c for c in _COLS if c in old_cols]
        # crash-safe: 직전 마이그레이션이 RENAME~DROP 사이 죽어 잔존하면(컨테이너
        # 휘발) 다음 RENAME 이 'table already exists'로 영구 brick → 먼저 정리.
        conn.execute("DROP TABLE IF EXISTS jp_exports_old")
        conn.execute("ALTER TABLE jp_exports RENAME TO jp_exports_old")
        conn.executescript(_SCHEMA)
        conn.execute(
            f"INSERT OR IGNORE INTO jp_exports ({','.join(common)}) "
            f"SELECT {','.join(common)} FROM jp_exports_old")
        conn.execute("DROP TABLE jp_exports_old")
    else:
        conn.executescript(_SCHEMA)
    return conn


def upsert_jp(conn: sqlite3.Connection, row: dict) -> bool:
    """(품목, 월) 단위 누적 저장(필드 보존 병합). 반환: 저장했으면 True.

    매월 새 (item, latest_month) 행으로 이어 저장(한국 alerts 패턴). 같은 (품목,월)
    재포워드는 병합: 새 값 우선, 없는 필드는 기존 유지 → 부분/truncated 재전송이
    차트·단가 등 good 필드를 null 로 클로버하지 않음(INSERT OR REPLACE 전체치환 대비)."""
    item = (row.get("item") or "").strip()
    if not item:
        return False
    month = row.get("latest_month") or ""
    if not month:
        # 월 미상(truncated) — 이미 실월 행이 있으면 phantom (item,'') 행 안 만듦.
        if conn.execute(
                "SELECT 1 FROM jp_exports WHERE item=? AND latest_month!='' LIMIT 1",
                (item,)).fetchone():
            return False
    ex = conn.execute(
        "SELECT * FROM jp_exports WHERE item=? AND latest_month=?",
        (item, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    merged = {}
    for k in _COLS:
        nv = row.get(k)
        if nv is None or nv == "":
            merged[k] = exd.get(k) if exd else None
        else:
            merged[k] = nv
    merged["item"] = item
    merged["latest_month"] = month
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO jp_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_jp(conn: sqlite3.Connection) -> list[dict]:
    """대시보드용 — 품목별 최신월 1행."""
    rows = conn.execute(
        "SELECT * FROM jp_exports j WHERE latest_month = "
        "(SELECT MAX(latest_month) FROM jp_exports WHERE item = j.item) "
        "ORDER BY latest_month DESC, item ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, item: str) -> list[dict]:
    """품목의 월별 전체 이력(오름차순) — 향후 트렌드 분석용."""
    rows = conn.execute(
        "SELECT * FROM jp_exports WHERE item=? ORDER BY latest_month ASC",
        (item,)).fetchall()
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
# 라이트 기본 + body.dark 오버라이드(시간대 자동전환, trade 대시보드와 동일 메커니즘).
# 토큰(CSS 변수)로 두 테마 일괄 — NOAH/trade Linear 팔레트 정합.
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
.jp-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.jp-card[open]{border-color:var(--accent)}
.jp-sum{list-style:none;cursor:pointer;padding:13px 16px;
  display:flex;flex-direction:column;gap:7px}
.jp-sum::-webkit-details-marker{display:none}
.jp-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.jp-card[open] .jp-sum::after{content:"▾ 접기"}
.jp-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.jp-item{font-weight:680;font-size:15px;color:var(--item)}
.jp-co{font-size:12px;color:var(--muted)}
.jp-mo{font-size:12px;color:var(--muted);margin-left:auto}
.jp-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.jp-mlabel{color:var(--muted);min-width:62px}
.jp-mval{font-weight:600;color:var(--text)}
.jp-delta{font-size:12px}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.jp-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.jp-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.jp-chart img{display:block;width:100%;height:auto}
.jp-htbl{width:100%;border-collapse:collapse;font-size:12px}
.jp-htbl th,.jp-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.jp-htbl th{color:var(--muted);font-weight:500}
.jp-htbl td:first-child,.jp-htbl th:first-child{text-align:left;color:var(--text)}
.empty{color:var(--muted);font-size:14px;padding:40px 0;text-align:center}
"""

# 시간대 자동 다크(19:00~07:00 KST) — trade 대시보드와 동일. body 존재 후 실행.
_THEME_JS = (
    "<script>function applyDarkMode(){var h=(new Date().getUTCHours()+9)%24;"
    "document.body.classList.toggle('dark',h>=19||h<7);}"
    "applyDarkMode();setInterval(applyDarkMode,60000);</script>"
)


def _delta_str(yoy, mom) -> str:
    """YoY/MoM 인라인(없으면 ''). 한국 관례: 상승=빨강▲ / 하락=파랑▼."""
    parts = []
    for tag, v in (("YoY", yoy), ("MoM", mom)):
        if v is None:
            continue
        cls = "up" if v > 0 else "down" if v < 0 else "flat"
        arr = "▲" if v > 0 else "▼" if v < 0 else "—"
        parts.append(f'<span class="jp-delta {cls}">{tag} {arr}{v:+.1f}%</span>')
    return ("&nbsp; " + " · ".join(parts)) if parts else ""


def _metric(emoji: str, label: str, val, unit: str, yoy, mom) -> str:
    if val is None:
        return ""
    return (f'<div class="jp-metric"><span class="jp-mlabel">{emoji} {label}</span>'
            f'<span class="jp-mval">{val:,.1f}{unit}</span>{_delta_str(yoy, mom)}</div>')


def _hist_table(hist: list[dict]) -> str:
    """월별 이력표(최신월 위). 1개월뿐이면 표 생략."""
    rows = sorted(hist, key=lambda h: h.get("latest_month") or "", reverse=True)
    rows = [h for h in rows if h.get("latest_month")]
    if len(rows) < 2:
        return ""
    trs = []
    for h in rows:
        ev = h.get("export_value_bn")
        pr = h.get("price_per_kg")
        evs = f"{ev:,.1f}" if ev is not None else "—"
        prs = f"{pr:,.1f}" if pr is not None else "—"
        trs.append(f"<tr><td>{_html.escape(h['latest_month'])}</td>"
                   f"<td>{evs}</td><td>{prs}</td></tr>")
    return ('<table class="jp-htbl"><tr><th>월</th><th>수출액(십억엔)</th>'
            '<th>단가(천엔/KG)</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    """확장 가능 카드(한국 품목뷰처럼): summary=핵심수치, 펼치면 차트+월별 이력."""
    item = _html.escape(r.get("item") or "")
    co = r.get("company")
    co_html = f'<span class="jp-co">🏭 {_html.escape(co)}</span>' if co else ""
    mo = _html.escape(r.get("latest_month") or "")
    summary = [f'<summary class="jp-sum">'
               f'<div class="jp-hd"><span class="jp-item">{item}</span>{co_html}'
               f'<span class="jp-mo">📅 {mo}</span></div>']
    summary.append(_metric("💰", "수출액", r.get("export_value_bn"), "십억 엔",
                           r.get("export_yoy"), r.get("export_mom")))
    summary.append(_metric("📦", "수출단가", r.get("price_per_kg"), "천엔/KG",
                           r.get("price_yoy"), r.get("price_mom")))
    summary.append("</summary>")
    detail = []
    chart = r.get("chart_media")
    if chart:
        detail.append(f'<div class="jp-chart"><img loading="lazy" '
                      f'src="{_html.escape(media_prefix + chart)}" alt="{item}"></div>')
    detail.append(_hist_table(hist))
    detail_html = (f'<div class="jp-detail">{"".join(d for d in detail if d)}</div>'
                   if any(detail) else "")
    return f'<details class="jp-card">{"".join(summary)}{detail_html}</details>'


def render_html(conn: sqlite3.Connection, media_url_prefix: str = "../") -> str:
    items = list_jp(conn)
    months = sorted({r.get("latest_month") for r in items if r.get("latest_month")})
    latest = months[-1] if months else ""
    if items:
        cards = "".join(
            _card_html(r, history(conn, r["item"]), media_url_prefix) for r in items)
        body = f'<div class="grid">{cards}</div>'
    else:
        body = ('<div class="empty">아직 수집된 일본 수출 데이터가 없습니다. '
                'BeOn 채널의 "일본 수출 데이터 업데이트" 메시지를 포워드하면 표시됩니다.</div>')
    return (
        "<!doctype html><html lang='ko'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>🗾 일본 수출 데이터</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        # 백링크는 ./ (= /trade/ 프록시 루트 → 수출입 대시보드). 'index.html' 은
        # 프록시 _OUR_ROOT_PAGES 와 충돌해 NOAH 메인으로 302 됨(2026-06-28 수정).
        '<div class="nav"><a href="./">← 수출입 대시보드</a></div>'
        "<h1>🗾 일본 수출 데이터</h1>"
        f'<p class="sub">출처 BeOn · 품목별 월 수출액(십억 엔)·단가(천엔/KG) · '
        f'{len(items)}개 품목'
        f'{" · 최신 " + _html.escape(latest) if latest else ""} · 카드 클릭 = 차트·월별 펼침</p>'
        f"{body}</div>{_THEME_JS}</body></html>"
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
