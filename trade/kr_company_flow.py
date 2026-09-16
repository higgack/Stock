"""나쁜양파 **한국 회사별 금액판** 공용 엔진 — 방향(수출/수입)은 `Flow` 가 갖는다.

2026-09-16 사용자 실측: 채널에 이런 캡션이 뜨는데 **받는 파서가 아예 없었다** —
관련성 필터가 곧 파서라 저장도 미매칭 알림도 없이 조용히 드랍됐다
(#83·#261·#330·#332 계열의 일곱 번째. "새 나라·새 판이 뜨면 또 난다"를 또 맞았다).

    **🇰🇷 8월 수출 한국**

    **▶️ LS ELECTRIC Co., Ltd. — 동관 + 대용량 유입식 변압기 + 정지형 전력변환기**

    **26년08월: $158.5M  (+105.3% YoY)  (+11.8% MoM)**

    최근 추이 (단위: USD M$)
    26년07월: $141.7M  (+25.9% YoY)  (+10.0% MoM)
    26년06월: $128.8M  (+37.3% YoY)  (+2.1% MoM)

⚠️ **기존 `kr_stock_exports`(krs) 와는 다른 문법이다.** 그쪽은
`HPSP (403870)` / `한국 수출` / `26년 7월 Update` 헤더에 단가 YoY·선행상관·
분기매출을 싣고 **금액이 없다**. 이쪽은 마커가 품목판 어순(`N월 수출 한국`)
이고 ▶️ 줄이 `회사 — 품목`, 값은 **금액·YoY·MoM** 이다. 한 파서로 합치려
들면 두 문법 중 하나가 반드시 헐거워진다(#73 원천의 서식이 한 벌이라고
가정하지 말 것) — 문법만 여기 두고, 수출분은 krs 가 이 파서를 불러 자기
테이블에 합치고(사용자 2026-09-16 "기존 종목별 페이지에 합치기"),
수입분은 `kr_stock_imports` 어댑터가 이 엔진의 저장·렌더까지 쓴다.

⚠️ 왜 복제하지 않는가 — `cn_stock_flow` 와 같은 이유다(#38·#84): 수출과
수입은 마커 한 단어와 금액 라벨만 다르고 문법이 같아, 복제하면 규약이
갈라지는 것 자체가 버그다. 소스 정체성(테이블·제목·링크)만 어댑터가 갖는다.
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trade.archive_template import (asof_footer, back_nav_html,
                                    card_html, max_ingest_iso)

# 파서 스키마 버전. 올리면 저장된 옛 행의 파생 필드를 upsert 가 다시 채운다
# (파서를 고쳐도 이미 구운 값이 안 바뀌는 함정 차단, 실수 #18·#21b).
PARSE_VER = 1


@dataclass(frozen=True)
class Flow:
    """한 방향(수출/수입)의 정체성. 문법은 공용, 이것만 다르다."""
    key: str                # "export" | "import"
    marker: str             # 캡션 마커 가운데 낱말 — "수출" | "수입"
    amount: str             # 화면 금액 라벨 — "수출액" | "수입액"
    table: str              # sqlite 테이블명
    title: str              # 페이지 h1(이모지 포함)
    # 마커 뒷낱말 · 화면 문구. ⚠️ 기본값을 두지 않는다 — 어댑터가 빠뜨리면
    # 남의 나라 캡션을 자기 DB 로 삼키는 조용한 유실이 된다(#83·cn_stock_flow
    # 가 같은 자리에서 독립 리뷰에 잡힌 규율).
    country: str
    sibling: str = ""       # 형제 페이지 파일명. 없으면 "" (억지로 걸면 404)
    sibling_label: str = ""


def marker_re(marker: str, country: str) -> re.Pattern:
    """`8월 수출 한국` — 품목판과 **같은 어순**이다(#83 이 적은 그 함정의 반대편).

    낱말 사이만 공백을 허용하고 개행(`\\s`)은 쓰지 않는다 — 캡션 어딘가에
    우연히 흩어진 세 낱말이 통과하면 그게 곧 남의 메시지 오저장이다."""
    return re.compile(r"\d+[^\S\n]*월[^\S\n]*" + re.escape(marker) +
                      r"[^\S\n]*" + re.escape(country))


# ▶️ 줄 = "회사 — 품목". 대시가 없으면 **품목판**(회사가 아님)이라 받지 않는다 —
# 품목을 회사 이름 칸에 넣으면 화면이 스스로 거짓말한다(#34·#77). 한국은 품목판
# 소스가 없으므로 그런 캡션은 `--show-irrelevant` 에 남아 다음 라운드가 잰다(#332).
_RE_HEAD = re.compile(r"▶️\s*(?P<body>[^\n]+)")
_RE_DASH = re.compile(r"\s+[—–―]\s+|\s+-\s+|(?<=\S)[—–―](?=\S)")

# `26년08월: $158.5M  (+105.3% YoY)  (+11.8% MoM)`
# ⚠️ YoY·MoM 은 **선택**이다. 옛 판은 셋을 모두 요구했는데, 그러면 원천이
# 한 칸만 빠뜨려도(첫 달이라 MoM 이 없다·`N/A`) 파서가 None 을 돌려주고
# 관련성 필터가 캡션을 **통째로 드랍**한다 — 이 커밋이 고치려던 바로 그
# 조용한 유실이다(#83 계열). 같은 모듈의 옛 지표판도 "지표는 전부 선택"이다.
# 금액(`$…M`)만 필수 — 그게 이 판의 값이다(없으면 받을 것이 없다).
_RE_MONTHLINE = re.compile(
    r"(\d{2})\s*년\s*(\d{1,2})\s*월\s*:\s*\$\s*([\d,]+(?:\.\d+)?)\s*M"
    r"(?:[^\S\n]*\(\s*(?P<yoy>[+\-]?\d+(?:\.\d+)?)\s*%\s*YoY\s*\))?"
    r"(?:[^\S\n]*\(\s*(?P<mom>[+\-]?\d+(?:\.\d+)?)\s*%\s*MoM\s*\))?")


def split_head(body: str) -> tuple[str, str] | None:
    """`회사 — 품목` → (회사, 품목). 대시가 없으면 None(품목판으로 본다)."""
    m = _RE_DASH.search(body)
    if not m:
        return None
    name = body[:m.start()].strip()
    item = body[m.end():].strip()
    if not name or not item:
        return None
    return name, item


def parse(caption: str, flow: Flow) -> dict | None:
    """캡션 → {stock_name, item, months[]} 또는 None."""
    if not caption or not marker_re(flow.marker, flow.country).search(caption):
        return None
    text = caption.replace("：", ":").replace("*", "")
    heads = list(_RE_HEAD.finditer(text))
    if not heads:
        return None
    m_head = heads[0]
    split = split_head(m_head.group("body").strip())
    if split is None:
        return None
    name, item = split
    # ⚠️ 값은 **이 헤더의 구간 안에서만** 찾는다. 캡션 전체를 훑으면 한
    # 메시지에 두 회사가 담겼을 때 A 카드에 B 의 금액이 섞여 들어가고 B 는
    # 통째로 사라진다(형제 `cn_stock_flow` 가 jp_stock 실측으로 얻은 가드 —
    # 엔진을 새로 쓰면서 안 옮겨 왔다, #38 형제의 가드를 그대로 옮길 것).
    seg = (text[m_head.start():heads[1].start()] if len(heads) > 1
           else text[m_head.start():])
    months = []
    for mm in _RE_MONTHLINE.finditer(seg):
        y, mo, val = mm.group(1), mm.group(2), mm.group(3)
        if not 1 <= int(mo) <= 12:
            continue
        yoy, mom = mm.group("yoy"), mm.group("mom")
        months.append({
            "month": f"20{y}-{int(mo):02d}",
            "value_musd": float(val.replace(",", "")),
            "value_yoy": float(yoy) if yoy is not None else None,
            "value_mom": float(mom) if mom is not None else None,
        })
    if not months:
        return None
    return {"stock_name": name, "item": item, "months": months}


# ─────────────────────────────────────────────────────────────────────────
# Storage — PK 는 (회사, 월). 이 판엔 6자리 종목코드가 오지 않는다.
# ─────────────────────────────────────────────────────────────────────────
_COLS = ("company", "month", "item", "value_musd", "value_yoy", "value_mom",
         "chart_media", "source_message_id", "posted_at", "raw_text",
         "parse_ver", "updated_at")


def _schema(flow: Flow) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {flow.table} (
  company TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  item TEXT,
  value_musd REAL, value_yoy REAL, value_mom REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  parse_ver INTEGER,
  updated_at TEXT,
  PRIMARY KEY (company, month)
);
"""


def open_db(path: str | Path, flow: Flow) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_schema(flow))
    # `CREATE TABLE IF NOT EXISTS` 는 기존 테이블을 안 건드린다 — 컬럼을
    # 더했으면 여기서 메워야 배포 후 첫 쓰기가 안 터진다(형제와 같은 규약, #262).
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({flow.table})")}
    for col, decl in (("item", "TEXT"), ("value_musd", "REAL"),
                      ("value_yoy", "REAL"), ("value_mom", "REAL"),
                      ("parse_ver", "INTEGER")):
        if col not in have:
            conn.execute(f"ALTER TABLE {flow.table} ADD COLUMN {col} {decl}")
    return conn


def upsert(conn: sqlite3.Connection, flow: Flow, company: str, item: str,
           month_row: dict, *, chart_media, source_message_id,
           posted_at: str, raw_text: str) -> bool:
    """(회사, 월) 단위 필드 보존 병합 — 부분 재전송이 기존 good 필드를 null 로
    덮지 않는다. 단 **파서 판이 올라간 행은 파생 필드를 다시 채운다**(#18)."""
    month = month_row.get("month") or ""
    if not company or not month:
        return False
    ex = conn.execute(
        f"SELECT * FROM {flow.table} WHERE company=? AND month=?",
        (company, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    if exd is not None and (exd.get("parse_ver") or 0) < PARSE_VER:
        exd = {k: (None if k in ("item", "value_musd", "value_yoy",
                                 "value_mom") else v)
               for k, v in exd.items()}
    incoming = {**month_row, "company": company, "item": item,
                "chart_media": chart_media,
                "source_message_id": source_message_id,
                "posted_at": posted_at, "raw_text": raw_text}
    merged = {}
    for k in _COLS:
        nv = incoming.get(k)
        merged[k] = nv if nv not in (None, "") else (exd.get(k) if exd else None)
    merged["company"] = company
    merged["month"] = month
    merged["parse_ver"] = PARSE_VER
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO {flow.table} ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_latest(conn: sqlite3.Connection, flow: Flow) -> list[dict]:
    """회사별 **최신월** 1행 — 새 월이 오면 카드가 자동 교체된다."""
    rows = conn.execute(
        f"SELECT * FROM {flow.table} t WHERE month = "
        f"(SELECT MAX(month) FROM {flow.table} WHERE company = t.company) "
        "ORDER BY month DESC, company ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, flow: Flow, company: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM {flow.table} WHERE company=? ORDER BY month ASC",
        (company,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, flow: Flow, caption: str, *,
           source_message_id=None, posted_at: str = "",
           media_paths: list[str] | None = None) -> bool:
    """parse + 메시지 내 전 개월(최신 + 최근 추이) upsert."""
    parsed = parse(caption, flow)
    if parsed is None:
        return False
    chart = (media_paths or [None])[0]
    saved = False
    for mrow in parsed["months"]:
        ok = upsert(conn, flow, parsed["stock_name"], parsed["item"], mrow,
                    chart_media=chart, source_message_id=source_message_id,
                    posted_at=posted_at, raw_text=caption)
        saved = saved or ok
    return saved


# ─────────────────────────────────────────────────────────────────────────
# Render — 형제 한국 페이지(kr_stock_exports)와 같은 카드 모양.
# ─────────────────────────────────────────────────────────────────────────
_CSS = """
:root{--bg:#f7f8f9;--card:#ffffff;--border:#e8e8ea;--text:#282a30;
  --item:#16171a;--muted:#5f6570;--accent:#4a55c4;--row:#eef0f2;--chartbd:#e8e8ea;--up:#c4161c;--down:#0a58d8}
body.dark{--bg:#0b0c0e;--card:#141518;--border:#26272b;--text:#e2e3e6;
  --item:#f7f8f8;--muted:#9aa0a9;--accent:#9aa2f0;--row:#1f2023;--chartbd:#26272b;--up:#e75458;--down:#5a99f8}
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
.krf-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.krf-card[open]{border-color:var(--accent)}
.krf-sum{list-style:none;padding:13px 16px;display:flex;flex-direction:column;gap:7px}
.krf-sum::-webkit-details-marker{display:none}
details.krf-card > .krf-sum{cursor:pointer}
details.krf-card > .krf-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.krf-card[open] .krf-sum::after{content:"▾ 접기"}
.krf-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.krf-item{font-weight:680;font-size:15px;color:var(--item)}
.krf-mo{font-size:12px;color:var(--muted);margin-left:auto}
.krf-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.krf-mlabel{color:var(--muted);min-width:88px}
.krf-mval{font-weight:600;color:var(--text)}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--muted)}
.krf-goods{font-size:11.5px;color:var(--muted);line-height:1.6}
.krf-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.krf-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.krf-chart img{display:block;width:100%;height:auto}
.krf-htbl{width:100%;border-collapse:collapse;font-size:12px}
.krf-htbl th,.krf-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.krf-htbl th{color:var(--muted);font-weight:500}
.krf-htbl td:first-child,.krf-htbl th:first-child{text-align:left;color:var(--text)}
.empty{color:var(--muted);font-size:14px;padding:40px 0;text-align:center}
"""

_THEME_JS = (
    "<script>function applyDarkMode(){var h=(new Date().getUTCHours()+9)%24;"
    "document.body.classList.toggle('dark',h>=19||h<7);}"
    "applyDarkMode();setInterval(applyDarkMode,60000);</script>"
)


def _metric(label: str, v, unit: str = "%") -> str:
    if v is None:
        return ""
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    arr = "▲" if v > 0 else "▼" if v < 0 else "—"
    return (f'<div class="krf-metric"><span class="krf-mlabel">{label}</span>'
            f'<span class="krf-mval {cls}">{arr}{v:+.1f}{unit}</span></div>')


def amount_text(v) -> str:
    """`$158.5M` — 원천이 적은 단위(USD M$)를 그대로 쓴다(#165 환산 금지)."""
    return "" if v is None else f"${v:,.1f}M"


def _hist_table(hist: list[dict], flow: Flow) -> str:
    rows = [h for h in sorted(hist, key=lambda h: h.get("month") or "",
                              reverse=True) if h.get("month")]
    if len(rows) < 2:
        return ""
    trs = []
    for h in rows:
        def c(k):
            v = h.get(k)
            return f"{v:+.1f}%" if v is not None else "—"
        amt = amount_text(h.get("value_musd")) or "—"
        trs.append(f"<tr><td>{_html.escape(h['month'])}</td>"
                   f"<td>{amt}</td><td>{c('value_yoy')}</td>"
                   f"<td>{c('value_mom')}</td></tr>")
    return (f'<table class="krf-htbl"><tr><th>월</th><th>{flow.amount}</th>'
            '<th>YoY</th><th>MoM</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], flow: Flow, media_prefix: str) -> str:
    name = _html.escape(r.get("company") or "")
    mo = _html.escape(r.get("month") or "")
    summary = [f'<div class="krf-hd"><span class="krf-item">{name}</span>'
               f'<span class="krf-mo">📅 {mo}</span></div>']
    amt = amount_text(r.get("value_musd"))
    if amt:
        summary.append(
            f'<div class="krf-metric"><span class="krf-mlabel">💵 {flow.amount}'
            f'</span><span class="krf-mval">{amt}</span></div>')
    summary.append(_metric(f"💰 {flow.amount} YoY", r.get("value_yoy")))
    summary.append(_metric("📈 MoM", r.get("value_mom")))
    if r.get("item"):
        summary.append(
            f'<div class="krf-goods">📦 {_html.escape(r["item"])}</div>')
    chart = ""
    media = r.get("chart_media") or ""
    if media:
        src = (media if media.startswith(("http://", "https://", "/"))
               else media_prefix + media)
        chart = (f'<div class="krf-chart"><img loading="lazy" '
                 f'src="{_html.escape(src)}" alt=""></div>')
    return card_html("krf", summary, [chart, _hist_table(hist, flow)])


def _sibling_line(flow: Flow) -> str:
    if not flow.sibling:
        return ""
    label = _html.escape(flow.sibling_label or flow.sibling)
    return (f" · <a href='{_html.escape(flow.sibling)}'>{label}</a>")


def render_html(conn: sqlite3.Connection, flow: Flow, *,
                media_url_prefix: str = "../") -> str:
    head = ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_html.escape(flow.title)}</title><style>" + _CSS +
            "</style></head><body>" + _THEME_JS)
    rows = list_latest(conn, flow)
    title = f"<h1>{_html.escape(flow.title)}</h1>"
    if not rows:
        # 빈 상태에서도 페이지를 만들어 nav 404 를 막는다(기존 모듈 규약).
        return (head + "<div class='wrap'>" + back_nav_html() + title +
                f"<div class='empty'>아직 수집된 한국 {flow.marker} "
                "데이터(나쁜양파·회사별)가 없습니다.</div>"
                + asof_footer(0, "회사", None, max_ingest_iso(conn, flow.table))
                + "</div></body></html>")
    cards = [_card_html(r, history(conn, flow, r["company"]), flow,
                        media_url_prefix) for r in rows]
    return (head + "<div class='wrap'>" + back_nav_html() + title +
            f"<div class='sub'>Badonions 한국 {flow.marker} 캡션을 "
            "<b>회사별</b>로 정리한 페이지 · 새 월이 오면 카드가 자동 교체되고 "
            "과거 월은 히스토리 표에 남습니다" + _sibling_line(flow) + "</div>"
            "<div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), "회사",
                          max((r.get("month") or "") for r in rows) or None,
                          max_ingest_iso(conn, flow.table))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, flow: Flow, *,
               media_url_prefix: str = "../") -> None:
    conn = open_db(db_path, flow)
    try:
        html = render_html(conn, flow, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
