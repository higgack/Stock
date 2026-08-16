"""한국 수출 데이터 (나쁜양파, **종목별**) — 파서 + 저장 + 대시보드.

기존 8개 나쁜양파 모듈(tw/cn/jp2/th/my/ph/mx/us)은 전부 **품목(HS)** 기준이라
PK 가 `(item, month)` 다. 이 모듈만 **회사(종목)** 기준이라 PK 가
`(stock_code, month)` 이고 지표 컬럼도 다르다(단가 YoY·선행상관·분기 매출 등).
그래서 기존 모듈을 확장하지 않고 분리했다 — 코드베이스 컨벤션(시장/소스별
완전 독립 모듈, 공용 베이스로 합치지 않음, `mx_exports.py` 참조)과도 일치.

캡션 예시:
    HPSP (403870)
    한국 수출
    26년 7월 Update

    단가 YoY: -6.4%
    수출액 YoY: +260.2%
    3M 수출액 YoY: +103.8%

    선행상관: 0.70
    선행 방향 일치율: 80%

    - CY26Q2 매출 ₩29.9B(-20.6% YoY)

월별 rolling: 한 메시지가 한 달치라, 8월분이 오면 `(403870, '2026-08')` 로
새 행이 들어가고 카드는 `MAX(month)` 로 자동 교체된다(기존 모듈과 동일 규약).
과거 월은 히스토리 표에 그대로 남는다 — 별도 rolling 로직 불필요.
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from trade.archive_template import back_nav_html
from trade.archive_template import card_html

# 헤더 블록 = "종목명 (6자리코드)" 줄 → 곧바로 "한국 수출" → "NN년 N월 Update".
# ⚠️ 세 마커를 **따로** 찾으면 안 된다 — 캡션 어딘가에 우연히 셋이 흩어져
# 있는 무관 메시지가 통과한다. 이 파서는 unstored_check·dashboard 의 **억제
# 필터**로도 쓰여서, 오탐이 곧 "저장도 안 되고 미매칭 알림에도 안 잡히는"
# 조용한 유실이 된다(2026-08-16 독립 리뷰). 인접성을 구조로 강제한다.
_NL = r"\n(?:[^\S\n]*\n)*"      # 줄바꿈(사이 빈 줄 허용)
_RE_BLOCK = re.compile(
    r"^[^\S\n]*(?P<name>[^\n(]+?)\s*\((?P<code>\d{6})\)[^\S\n]*" + _NL +
    r"[^\S\n]*한국\s*수출[^\S\n]*" + _NL +
    r"[^\S\n]*(?P<yy>\d{2})\s*년[^\S\n]*(?P<mm>\d{1,2})\s*월[^\S\n]*Update",
    re.M | re.I)

# 지표 — 전부 선택적(포맷이 조금 바뀌어도 메시지 전체를 버리지 않는다).
_RE_PRICE_YOY = re.compile(r"단가\s*YoY\s*:?\s*([+\-]?\d+(?:\.\d+)?)\s*%", re.I)
# 3M 접두 유무를 **한 패턴에서** 구분한다. 옛 구현은 `(?<!3M\s)` 룩비하인드로
# 걸렀는데 공백이 없는 '3M수출액' 을 못 막아 두 값이 같아졌다(독립 리뷰).
_RE_EXP_YOY_ANY = re.compile(
    r"(?P<pfx>\d+M)?\s*수출액\s*YoY\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)\s*%", re.I)
_RE_LEAD_CORR = re.compile(r"선행\s*상관\s*:?\s*([+\-]?\d+(?:\.\d+)?)")
_RE_LEAD_HIT = re.compile(r"선행\s*방향\s*일치율\s*:?\s*([+\-]?\d+(?:\.\d+)?)\s*%")
# "- CY26Q2 매출 ₩29.9B(-20.6% YoY)" — 여러 분기가 올 수 있어 finditer.
_RE_REV = re.compile(
    r"(CY\d{2}Q\d)\s*매출\s*₩?\s*([\d,]+(?:\.\d+)?)\s*B"
    r"\s*\(\s*([+\-]?\d+(?:\.\d+)?)\s*%\s*YoY\s*\)", re.I)


def _f(pat: re.Pattern, text: str):
    m = pat.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_kr_stock_export(caption: str) -> dict | None:
    """캡션 → {stock_name, stock_code, month, 지표…} 또는 None.

    헤더(종목명+코드)·'한국 수출'·'NN년 N월 Update' 3종이 모두 있어야 한다 —
    나쁜양파는 무관 콘텐츠가 섞인 일반 채널이라 마커를 느슨하게 잡으면
    리스너가 엉뚱한 메시지를 통과시킨다(listen_badonion 의 관련성 필터 규약).
    """
    if not caption:
        return None
    text = caption.replace("：", ":").replace("*", "")
    m = _RE_BLOCK.search(text)
    if not m:
        return None
    name = (m.group("name") or "").strip()
    if not name:
        return None
    yy, mm = int(m.group("yy")), int(m.group("mm"))
    if not 1 <= mm <= 12:
        return None
    # 수출액 YoY — 접두(3M 등) 유무로 분리. 접두 없는 것이 당월치.
    exp_yoy = exp_yoy_3m = None
    for mo in _RE_EXP_YOY_ANY.finditer(text):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        if mo.group("pfx"):
            if exp_yoy_3m is None:
                exp_yoy_3m = v
        elif exp_yoy is None:
            exp_yoy = v
    # 분기 매출은 여러 줄이 올 수 있다 — **가장 최근 분기**를 카드에 쓴다
    # (첫 줄을 그냥 쓰면 옛 분기가 표시된다, 2026-08-16 독립 리뷰).
    # 'CY26Q2' 포맷은 문자열 정렬이 곧 시간 정렬.
    revs = sorted(({"quarter": q.upper(),
                    "value_krw_b": float(v.replace(",", "")),
                    "yoy": float(y)} for q, v, y in _RE_REV.findall(text)),
                  key=lambda r: r["quarter"], reverse=True)
    return {
        "stock_code": m.group("code"),
        "stock_name": name,
        "month": f"20{yy:02d}-{mm:02d}",
        "price_yoy": _f(_RE_PRICE_YOY, text),
        "export_yoy": exp_yoy,
        "export_yoy_3m": exp_yoy_3m,
        "lead_corr": _f(_RE_LEAD_CORR, text),
        "lead_dir_hit": _f(_RE_LEAD_HIT, text),
        "rev_quarter": revs[0]["quarter"] if revs else None,
        "rev_value_krw_b": revs[0]["value_krw_b"] if revs else None,
        "rev_yoy": revs[0]["yoy"] if revs else None,
    }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kr_stock_exports (
  stock_code TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  stock_name TEXT,
  price_yoy REAL, export_yoy REAL, export_yoy_3m REAL,
  lead_corr REAL, lead_dir_hit REAL,
  rev_quarter TEXT, rev_value_krw_b REAL, rev_yoy REAL,
  chart_media TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (stock_code, month)
);
"""

_COLS = ("stock_code", "month", "stock_name", "price_yoy", "export_yoy",
         "export_yoy_3m", "lead_corr", "lead_dir_hit", "rev_quarter",
         "rev_value_krw_b", "rev_yoy", "chart_media", "source_message_id",
         "posted_at", "raw_text", "updated_at")


def open_kr_stock_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_kr_stock(conn: sqlite3.Connection, row: dict, *, chart_media,
                    source_message_id, posted_at: str, raw_text: str) -> bool:
    """필드 보존 병합 upsert — 부분 재전송이 기존 good 필드를 null 로 덮지
    않게 한다(기존 나쁜양파 모듈과 동일 규약)."""
    code, month = row.get("stock_code"), row.get("month") or ""
    if not code or not month:
        return False
    ex = conn.execute(
        "SELECT * FROM kr_stock_exports WHERE stock_code=? AND month=?",
        (code, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    incoming = {**row, "chart_media": chart_media,
                "source_message_id": source_message_id,
                "posted_at": posted_at, "raw_text": raw_text}
    merged = {}
    for k in _COLS:
        nv = incoming.get(k)
        merged[k] = nv if nv not in (None, "") else (exd.get(k) if exd else None)
    merged["stock_code"] = code
    merged["month"] = month
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO kr_stock_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_kr_stock(conn: sqlite3.Connection) -> list[dict]:
    """종목별 **최신월** 1행 — 새 월이 오면 카드가 자동 교체된다."""
    rows = conn.execute(
        "SELECT * FROM kr_stock_exports t WHERE month = "
        "(SELECT MAX(month) FROM kr_stock_exports WHERE stock_code = t.stock_code) "
        "ORDER BY month DESC, stock_name ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, stock_code: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM kr_stock_exports WHERE stock_code=? ORDER BY month ASC",
        (stock_code,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    parsed = parse_kr_stock_export(caption)
    if parsed is None:
        return False
    return upsert_kr_stock(conn, parsed, chart_media=(media_paths or [None])[0],
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


def _pct(v, label: str = "") -> str:
    if v is None:
        return ""
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    arr = "▲" if v > 0 else "▼" if v < 0 else "—"
    return f'<span class="{cls}">{label}{arr}{v:+.1f}%</span>'


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
    name = _html.escape(r.get("stock_name") or r.get("stock_code") or "")
    code = _html.escape(r.get("stock_code") or "")
    mo = _html.escape(r.get("month") or "")
    summary = [f'<div class="kr-hd">'
               f'<span class="kr-item">{name}</span>'
               f'<span class="kr-code">{code}</span>'
               f'<span class="kr-mo">📅 {mo}</span></div>']
    summary.append(_metric("💰 수출액 YoY", r.get("export_yoy")))
    summary.append(_metric("📊 3M 수출액", r.get("export_yoy_3m")))
    summary.append(_metric("🏷️ 단가 YoY", r.get("price_yoy")))
    lead = []
    if r.get("lead_corr") is not None:
        lead.append(f"선행상관 {r['lead_corr']:.2f}")
    if r.get("lead_dir_hit") is not None:
        lead.append(f"방향 일치율 {r['lead_dir_hit']:.0f}%")
    if lead:
        summary.append(f'<div class="kr-lead">🔗 {" · ".join(lead)}</div>')
    if r.get("rev_quarter") and r.get("rev_value_krw_b") is not None:
        summary.append(
            f'<div class="kr-rev">🧾 {_html.escape(r["rev_quarter"])} 매출 '
            f'₩{r["rev_value_krw_b"]:,.1f}B {_pct(r.get("rev_yoy"))}</div>')
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
         "<title>한국 수출 데이터(종목별)</title><style>" + _CSS +
         "</style></head><body>" + _THEME_JS)


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    rows = list_kr_stock(conn)
    if not rows:
        # 빈 상태에서도 페이지를 만들어 nav 404 를 막는다(기존 모듈 규약).
        return (_HEAD + "<div class='wrap'>"
                f"{back_nav_html()}"
                "<h1>🏢 한국 수출 데이터(종목별)</h1>"
                "<div class='empty'>아직 수집된 한국 수출 데이터(나쁜양파)가 "
                "없습니다.</div></div></body></html>")
    cards = [_card_html(r, history(conn, r["stock_code"]), media_url_prefix)
             for r in rows]
    return (_HEAD + "<div class='wrap'>"
            f"{back_nav_html()}"
            "<h1>🏢 한국 수출 데이터(종목별)</h1>"
            "<div class='sub'>Badonions 한국 수출 캡션을 <b>종목별</b>로 정리한 "
            "별도 페이지 · 새 월이 오면 카드가 자동 교체되고 과거 월은 "
            "히스토리 표에 남습니다</div>"
            "<div class='grid'>" + "".join(cards) + "</div></div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_kr_stock_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
