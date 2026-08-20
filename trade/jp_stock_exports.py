"""일본 수출 데이터 (나쁜양파, **종목별**) — 파서 + 저장 + 대시보드.

`jp2_exports.py` 는 같은 채널의 **품목(HS) 기준** 일본 수출이고, 이 모듈은
**회사(종목) 기준**이다. PK 가 `(ticker, month)` 라 스키마가 달라 분리했다
(`kr_stock_exports.py` 와 같은 이유 — 코드베이스 컨벤션상 소스별 독립 모듈).

발견 경위(2026-08-16): `diagnose_badonion.py` 로 채널을 실측하다 이 포맷이
**관련성 필터를 전부 통과 못 하고 조용히 드랍**되던 것을 확인했다. 기존
jp2 파서가 품목 기준이라 회사 헤더를 못 읽었고, 그래서 저장도 안 되고
미매칭 알림에도 안 잡히는 유실이었다(실측 8건: Lumentum·Fujikura·Kioxia·
SanDisk·FormFactor·Advantest·TESEC·Nitto Boseki).

캡션 예시(실측 원문):
    Lumentum (LITE) 일본 수출 Update
    26년 6월

    EML·DML·CW 레이저소자

    수출액: YoY +446.7% 🔻
    3M 수출액: YoY +537.3% 🔺
    단가: YoY +367.5% 🔺

⚠️ 티커가 **미·일 혼재**다 — LITE/SNDK/FORM(US) 과 5803/6857/285A(JP) 가
같은 채널에 섞여 온다. 일본에서 수출하는 기업을 국적 무관으로 다루므로
티커를 6자리 숫자로 가정하면 안 된다(한국판과 다른 점).

지표 라벨 순서도 한국판과 다르다 — 한국은 `수출액 YoY: +260.2%`, 일본은
`수출액: YoY +446.7%` 다. 둘 다 흡수하되 마커 인접성은 유지한다.

월별 rolling: PK 가 `(ticker, month)` 라 7월분이 오면 새 행이 들어가고
카드는 `MAX(month)` 로 자동 교체된다(기존 모듈과 동일 규약).
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from trade.archive_template import asof_footer, back_nav_html, max_ingest_iso
from trade.archive_template import card_html

# 헤더 = "종목명 (티커) 일본 수출 Update" **한 줄** → 다음 줄 "NN년 N월".
# ⚠️ 세 마커를 따로 찾으면 안 된다 — 이 파서는 리스너의 관련성 필터이자
# unstored_check·dashboard 의 억제 필터라, 오탐이 곧 "저장도 안 되고 미매칭
# 알림에도 안 잡히는" 조용한 유실이 된다(kr_stock_exports 와 같은 규약).
_NL = r"\n(?:[^\S\n]*\n)*"      # 줄바꿈(사이 빈 줄 허용)
# ⚠️ 헤더 **한 줄** 안에서는 `\s` 를 쓰지 않는다 — `\s` 가 개행을 먹어
# `어떤회사\n(ABCD) 일본 수출 Update` 같은 무관 조합이 통과한다(독립 리뷰
# 실측). 줄바꿈이 허용되는 지점은 오직 `_NL` 자리 하나다.
_RE_BLOCK = re.compile(
    r"^[^\S\n]*(?P<name>[^\n(]+?)[^\S\n]*\((?P<ticker>[A-Za-z0-9]{2,6})\)"
    r"[^\S\n]*일본[^\S\n]*수출[^\S\n]*Update[^\S\n]*" + _NL +
    r"[^\S\n]*(?P<yy>\d{2})[^\S\n]*년[^\S\n]*(?P<mm>\d{1,2})[^\S\n]*월",
    re.M | re.I)

# 지표 — 라벨과 YoY 사이 콜론 위치가 소스마다 달라(한국판 `수출액 YoY:`,
# 일본판 `수출액: YoY`) 양쪽을 한 패턴에서 받는다. 전부 선택적 —
# 포맷이 조금 바뀌어도 메시지 전체를 버리지 않는다.
_RE_EXP_YOY_ANY = re.compile(
    r"(?P<pfx>\d+M)?\s*수출액\s*:?\s*YoY\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)\s*%",
    re.I)
_RE_PRICE_YOY = re.compile(
    r"단가\s*:?\s*YoY\s*:?\s*([+\-]?\d+(?:\.\d+)?)\s*%", re.I)
# 본문 주석("* Kioxia와 동일한 공동생산 흐름이므로 두 지표를 합산하지
# 않습니다.") — 합산 금지 같은 **해석에 필수인 경고**라 버리지 않고 싣는다.
# ⚠️ `*` 뒤에 **공백을 필수**로 요구한다. `**` 제거 후 홀수 개가 남는
# 강조 표기(`***중요***` → `*중요*`)가 각주로 승격돼 카드의 ⚠️ 경고 슬롯을
# 차지하던 오탐 차단(독립 리뷰 실측). 실제 각주는 항상 '* ' 형태다.
_RE_NOTE = re.compile(r"^[^\S\n]*\*[^\S\n]+(?P<n>[^\n]+)$", re.M)
# 지표·URL·홍보문이 아닌 첫 줄 = 품목 설명("EML·DML·CW 레이저소자").
_SKIP_LINE = re.compile(
    r"(수출액|단가|YoY|https?://|맵핑|Update|^\s*\*|^\s*\d{2}\s*년)", re.I)


def _f(pat: re.Pattern, text: str):
    m = pat.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _item_line(text: str, header_end: int) -> str | None:
    """헤더 이후 첫 '설명' 줄. 없으면 None(품목 표기가 없는 캡션도 있다)."""
    for raw in text[header_end:].splitlines():
        s = raw.strip()
        if not s or _SKIP_LINE.search(s):
            continue
        return s[:120]
    return None


def parse_jp_stock_export(caption: str) -> dict | None:
    """캡션 → {stock_name, ticker, month, 지표…} 또는 None."""
    if not caption:
        return None
    # ⚠️ `**`(마크다운 볼드)만 걷어낸다. 한국판처럼 `*` 를 전부 지우면
    # 각주 마커('* Kioxia와 동일한 공동생산…')까지 사라져 note 가 죽는다.
    # Telethon 이 entities 로 볼드를 되붙이므로 실제 캡션에 `**` 가 들어온다
    # (diagnose_badonion 실측 — raw_text 에는 없고 text 에만 있다).
    text = caption.replace("：", ":").replace("**", "")
    heads = list(_RE_BLOCK.finditer(text))
    if not heads:
        return None
    m = heads[0]
    name = (m.group("name") or "").strip()
    if not name:
        return None
    yy, mm = int(m.group("yy")), int(m.group("mm"))
    if not 1 <= mm <= 12:
        return None
    # ⚠️ 지표는 **이 헤더의 구간 안에서만** 찾는다. 캡션 전체를 훑으면 한
    # 메시지에 두 회사가 담겼을 때 A 카드에 B 의 단가·각주가 섞여 들어간다
    # (독립 리뷰 실측 — 값이 틀린 채로 조용히 저장됨). 다음 헤더 직전까지로
    # 자르면 최소한 **틀린 숫자는 만들지 않는다**(B 는 이 계약상 못 담는다).
    seg = text[m.start():heads[1].start()] if len(heads) > 1 else text[m.start():]
    # 수출액 YoY — 접두(3M 등) 유무로 분리. 접두 없는 것이 당월치.
    exp_yoy = exp_yoy_3m = None
    for mo in _RE_EXP_YOY_ANY.finditer(seg):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        if mo.group("pfx"):
            if exp_yoy_3m is None:
                exp_yoy_3m = v
        elif exp_yoy is None:
            exp_yoy = v
    notes = [n.strip() for n in _RE_NOTE.findall(seg) if n.strip()]
    return {
        "ticker": m.group("ticker").upper(),
        "stock_name": name,
        "month": f"20{yy:02d}-{mm:02d}",
        "item": _item_line(seg, m.end() - m.start()),
        "export_yoy": exp_yoy,
        "export_yoy_3m": exp_yoy_3m,
        "price_yoy": _f(_RE_PRICE_YOY, seg),
        "note": " / ".join(notes)[:300] or None,
    }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jp_stock_exports (
  ticker TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  stock_name TEXT,
  item TEXT,
  export_yoy REAL, export_yoy_3m REAL, price_yoy REAL,
  note TEXT,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (ticker, month)
);
"""

_COLS = ("ticker", "month", "stock_name", "item", "export_yoy",
         "export_yoy_3m", "price_yoy", "note", "chart_media",
         "source_message_id", "posted_at", "raw_text", "updated_at")


def open_jp_stock_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_jp_stock(conn: sqlite3.Connection, row: dict, *, chart_media,
                    source_message_id, posted_at: str, raw_text: str) -> bool:
    """필드 보존 병합 upsert — 부분 재전송이 기존 good 필드를 null 로 덮지
    않게 한다(기존 나쁜양파 모듈과 동일 규약)."""
    tk, month = row.get("ticker"), row.get("month") or ""
    if not tk or not month:
        return False
    ex = conn.execute(
        "SELECT * FROM jp_stock_exports WHERE ticker=? AND month=?",
        (tk, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    incoming = {**row, "chart_media": chart_media,
                "source_message_id": source_message_id,
                "posted_at": posted_at, "raw_text": raw_text}
    merged = {}
    for k in _COLS:
        nv = incoming.get(k)
        merged[k] = nv if nv not in (None, "") else (exd.get(k) if exd else None)
    merged["ticker"] = tk
    merged["month"] = month
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO jp_stock_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_jp_stock(conn: sqlite3.Connection) -> list[dict]:
    """종목별 **최신월** 1행 — 새 월이 오면 카드가 자동 교체된다."""
    rows = conn.execute(
        "SELECT * FROM jp_stock_exports t WHERE month = "
        "(SELECT MAX(month) FROM jp_stock_exports WHERE ticker = t.ticker) "
        "ORDER BY month DESC, stock_name ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jp_stock_exports WHERE ticker=? ORDER BY month ASC",
        (ticker,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    parsed = parse_jp_stock_export(caption)
    if parsed is None:
        return False
    return upsert_jp_stock(conn, parsed, chart_media=(media_paths or [None])[0],
                           source_message_id=source_message_id,
                           posted_at=posted_at, raw_text=caption)


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
.kr-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.kr-card[open]{border-color:var(--accent)}
.kr-sum{list-style:none;padding:13px 16px;
  display:flex;flex-direction:column;gap:7px}
.kr-sum::-webkit-details-marker{display:none}
details.kr-card > .kr-sum{cursor:pointer}
details.kr-card > .kr-sum::after{content:"▸ 펼치기(차트·월별)";color:var(--muted);font-size:11px;margin-top:2px}
.kr-card[open] .kr-sum::after{content:"▾ 접기"}
.kr-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.kr-item{font-weight:680;font-size:15px;color:var(--item)}
.kr-code{font-size:11.5px;color:var(--muted)}
.kr-mo{font-size:12px;color:var(--muted);margin-left:auto}
.kr-metric{display:flex;align-items:baseline;gap:8px;font-size:13px;flex-wrap:wrap}
.kr-mlabel{color:var(--muted);min-width:88px}
.kr-mval{font-weight:600;color:var(--text)}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.kr-lead{font-size:11.5px;color:var(--muted);line-height:1.6}
.kr-rev{font-size:12px;color:var(--text);margin-top:2px}
.kr-detail{padding:0 16px 14px;border-top:1px solid var(--border)}
.kr-chart{margin:12px 0;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.kr-chart img{display:block;width:100%;height:auto}
.kr-htbl{width:100%;border-collapse:collapse;font-size:12px}
.kr-htbl th,.kr-htbl td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--row)}
.kr-htbl th{color:var(--muted);font-weight:500}
.kr-htbl td:first-child,.kr-htbl th:first-child{text-align:left;color:var(--text)}
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
    return (f'<div class="kr-metric"><span class="kr-mlabel">{label}</span>'
            f'<span class="kr-mval {cls}">{arr}{v:+.1f}{unit}</span></div>')


def _hist_table(hist: list[dict]) -> str:
    rows = [h for h in sorted(hist, key=lambda h: h.get("month") or "",
                              reverse=True) if h.get("month")]
    if len(rows) < 2:
        return ""
    trs = []
    for h in rows:
        def c(k):
            v = h.get(k)
            return f"{v:+.1f}%" if v is not None else "—"
        trs.append(f"<tr><td>{_html.escape(h['month'])}</td>"
                   f"<td>{c('export_yoy')}</td><td>{c('export_yoy_3m')}</td>"
                   f"<td>{c('price_yoy')}</td></tr>")
    return ('<table class="kr-htbl"><tr><th>월</th><th>수출액 YoY</th>'
            '<th>3M 수출액 YoY</th><th>단가 YoY</th></tr>'
            + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    name = _html.escape(r.get("stock_name") or r.get("ticker") or "")
    tk = _html.escape(r.get("ticker") or "")
    mo = _html.escape(r.get("month") or "")
    summary = [f'<div class="kr-hd">'
               f'<span class="kr-item">{name}</span>'
               f'<span class="kr-code">{tk}</span>'
               f'<span class="kr-mo">📅 {mo}</span></div>']
    if r.get("item"):
        summary.append(f'<div class="kr-lead">📦 {_html.escape(r["item"])}</div>')
    summary.append(_metric("💰 수출액 YoY", r.get("export_yoy")))
    summary.append(_metric("📊 3M 수출액", r.get("export_yoy_3m")))
    summary.append(_metric("🏷️ 단가 YoY", r.get("price_yoy")))
    if r.get("note"):
        # 합산 금지 같은 해석 경고 — 카드에 그대로 노출한다(요약·의역 없음).
        summary.append(f'<div class="kr-lead">⚠️ {_html.escape(r["note"])}</div>')
    chart = ""
    media = r.get("chart_media") or ""
    if media:
        src = (media if media.startswith(("http://", "https://", "/"))
               else media_prefix + media)
        chart = (f'<div class="kr-chart"><img loading="lazy" '
                 f'src="{_html.escape(src)}" alt=""></div>')
    return card_html("kr", summary, [chart, _hist_table(hist)])


_HEAD = ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
         "<meta name='viewport' content='width=device-width, initial-scale=1'>"
         "<title>일본 수출 데이터(종목별)</title><style>" + _CSS +
         "</style></head><body>" + _THEME_JS)

_SUB = ("Badonions 일본 수출 캡션을 <b>종목별</b>로 정리한 별도 페이지 · "
        "일본에서 수출하는 기업이라 <b>미·일 종목이 섞여 있습니다</b>"
        "(LITE·SNDK 등 미국 상장 포함) · 품목(HS) 기준은 "
        "<a href='jp2.html'>일본 수출 데이터(품목)</a>")


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    rows = list_jp_stock(conn)
    nav = f"{back_nav_html()}"
    if not rows:
        # 빈 상태에서도 페이지를 만들어 nav 404 를 막는다(기존 모듈 규약).
        return (_HEAD + "<div class='wrap'>" + nav +
                "<h1>🗼 일본 수출 데이터(종목별)</h1>"
                "<div class='empty'>아직 수집된 일본 수출 데이터(종목별, "
                "나쁜양파)가 없습니다.</div>"
                + asof_footer(0, "종목", None,
                              max_ingest_iso(conn, "jp_stock_exports"))
                + "</div></body></html>")
    cards = [_card_html(r, history(conn, r["ticker"]), media_url_prefix)
             for r in rows]
    return (_HEAD + "<div class='wrap'>" + nav +
            "<h1>🗼 일본 수출 데이터(종목별)</h1>"
            f"<div class='sub'>{_SUB}</div>"
            "<div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), "종목",
                          max((r.get("month") or "") for r in rows) or None,
                          max_ingest_iso(conn, "jp_stock_exports"))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_jp_stock_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
