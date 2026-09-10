"""대만 월매출(종목별) — 나쁜양파 `badonion.co.kr/twse-revenue` 캡션.

발견 경위(사용자 2026-09-10): 같은 날 대만 수출(종목별) 6건은 백필로 회수됐는데
`TSMC 월매출 26년 8월 … REV 약 5,148억 1,000만 TWD · MoM +10.1% · YoY +53.3% ·
26년 1~8월 누적 …` 카드만 포워드되지 않았다. 레지스트리 파서 15종 중 어느 것도
이 문법(월매출·REV·MoM·YoY·누적)을 받지 않아 관련성 필터에서 통째로 드랍된
것 — 저장도 미매칭 알림도 없는 조용한 유실의 **여섯 번째**(#83·#261·#330).

대만 상장사는 매달 10일까지 전월 매출을 공시하므로(月營收) 수출 데이터와
별개의 **월간 매출 계열**이다. 수출 종목판 엔진(`cn_stock_flow`)은 문법이
달라(YoY 라벨·상관 계열·Update 헤더) 재사용하지 않고, 저장·렌더 규약만 같은
모양으로 맞춘다(필드 보존 upsert · parse_ver 재파생 · 빈 페이지도 생성).

⚠️ 캡션 원문은 **스크린샷 기반 재구성**이다 — 원문 마크다운은 미확인.
그래서 파서는 줄바꿈/`·` 어느 구분자로 와도, 라벨 뒤 `:` 유무·`약` 접두·
티커 괄호 유무와 무관하게 받는다(형제 종목판과 같은 관용 파싱 방침).
배포 뒤 `backfill_badonion --show-irrelevant` 가 여전히 이 카드를 드랍한
캡션으로 찍으면 그 원문으로 픽스처를 갈아끼운다(#155 픽스처는 원천이 실제로
보내는 모양대로).
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trade.archive_template import (asof_footer, back_nav_html, card_html,
                                    max_ingest_iso)
from trade.cn_stock_flow import _CSS, _THEME_JS

PARSE_VER = 2
TABLE = "tw_monthly_revenue"
TITLE = "🧋 대만 월매출 데이터(종목별)"
SIBLING = "tw_stock.html"
SIBLING_LABEL = "대만 수출 데이터(종목별)"

# 이름은 **숫자로 끝나지 않는다** — `26년 8월 매출 YoY` 같은 한국 종목판 문구의
# `8월 매출` 을 헤더로 오독하면 남의 캡션이 이 DB 로 샌다(독립 리뷰 2026-09-10 #2).
_RE_HEAD = re.compile(
    r"(?P<name>[^\n·:/|]*?[^\d\s])\s*(?:\((?P<ticker>[A-Za-z0-9.\-]{1,12})\))?\s+월\s*매출"
    r"|^(?P<name2>[^\n·:/|]*?[^\d\s])\s*(?:\((?P<ticker2>[A-Za-z0-9.\-]{1,12})\))?\s*월\s*매출",
    re.M)
_RE_GATE = re.compile(r"(?:^|[^\d\s])\s*월\s*매출", re.M)
_RE_PERIOD = re.compile(r"(?P<yy>\d{2})년\s*(?P<mm>\d{1,2})월(?!\s*누적)")
_RE_PCT = re.compile(r"(?P<v>[+-]?\d+(?:\.\d+)?)\s*%")
# 부호가 라벨 **앞**에 오는 형식도 실재한다 — 실물 캡션 `(+YoY 39.3%)`(2026-09-10
# 스크린샷). 값에 부호가 없고 라벨 앞에 부호가 있으면 그 부호를 값에 적용한다.
_RE_MOM = re.compile(r"(?:(?P<s>[+-])\s*)?(?<![A-Za-z])MoM(?![A-Za-z])\s*[:：]?\s*(?P<v>[+-]?\d+(?:\.\d+)?)\s*%", re.I)
_RE_YOY = re.compile(r"(?:(?P<s>[+-])\s*)?(?<![A-Za-z])YoY(?![A-Za-z])\s*[:：]?\s*(?P<v>[+-]?\d+(?:\.\d+)?)\s*%", re.I)
_RE_REVLBL = re.compile(r"(?<![A-Za-z])REV(?:ENUE)?(?![A-Za-z])|매출(?:액)?", re.I)
_RE_HEADTOK = re.compile(r"월\s*매출")
# 한 줄에 여러 지표가 오면 구분자(`,`)나 다음 라벨 앞에서 금액을 자른다.
_RE_REVCUT = re.compile(r",(?!\d)|(?<![A-Za-z])(?:MoM|YoY)(?![A-Za-z])", re.I)   # 천단위 콤마는 자르지 않는다
_SPLIT = re.compile(r"\n|·|\s/\s|\|")
_RE_AMT = re.compile(r"(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>조|억|만)")
_RE_URL = re.compile(r"(?:https?://)?[\w\-]+(?:\.[\w\-]+)*\.(?:co\.kr|kr|com|net|io)"
                     r"(?:/\S*)?", re.I)
_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4}


def amount_twd(s: str) -> float | None:
    """`약 5,148억 1,000만 TWD` → 514,810,000,000. 단위가 하나도 없으면 None —
    통화·단위를 지어내지 않는다(#32). 원문 문자열은 따로 그대로 보관한다."""
    total, hit = 0.0, False
    for m in _RE_AMT.finditer(s or ""):
        try:
            total += float(m.group("n").replace(",", "")) * _UNIT[m.group("u")]
            hit = True
        except ValueError:
            continue
    return total if hit else None


def _pct(tok: str) -> float | None:
    m = _RE_PCT.search(tok)
    if not m:
        return None
    try:
        return float(m.group("v"))
    except ValueError:
        return None


def _labeled(rx: re.Pattern, tok: str) -> float | None:
    """`YoY +53.3%` · `YoY: 53.3%` · `(+YoY 39.3%)` · `-YoY 5%` → 부호 붙은 값."""
    m = rx.search(tok)
    if not m:
        return None
    v = m.group("v")
    if v[0] not in "+-" and m.group("s"):
        v = m.group("s") + v
    try:
        return float(v)
    except ValueError:
        return None



def _is_cum_value(tok: str) -> bool:
    """누적 토큰 뒤에 이어지는 '값만 있는' 줄인가 — 금액 또는 % 가 있고 MoM/REV
    라벨·기준월(`N년 M월`, 누적 아님)·헤더가 없다. `(+YoY 39.3%)` 는 값이다."""
    if not (_RE_AMT.search(tok) or _RE_PCT.search(tok)):
        return False
    if _RE_MOM.search(tok) or _RE_REVLBL.search(tok) or _RE_HEADTOK.search(tok):
        return False
    if _RE_PERIOD.search(tok) or _RE_URL.fullmatch(tok.rstrip(".,;")):
        return False
    return True


def parse_tw_monthly_revenue(caption: str) -> dict | None:
    """캡션 → {ticker, stock_name, month, rev_text, rev_twd, mom, yoy, cum_text,
    cum_yoy, parse_ver} 또는 None(이 문법이 아니면)."""
    if not caption:
        return None
    text = caption.replace("：", ":").replace("**", "")
    if not _RE_GATE.search(text):
        return None
    head = _RE_HEAD.search(text)
    if not head:
        return None
    name = (head.group("name") or head.group("name2") or "").strip(" -·|")
    if not name:
        return None
    tk = head.group("ticker") or head.group("ticker2") or ""
    ticker = tk.upper() or re.sub(r"\s+", "", name).upper()
    # 헤더 뒤 토큰들 — 줄바꿈·`·`·` / `·`|` 어느 구분자로 와도 같은 토큰으로 본다
    # (원문 형식 미확인 — 관용 파싱, 독립 리뷰 #3).
    toks = [t.strip() for t in _SPLIT.split(text[head.start():]) if t.strip()]
    month = None
    for t in toks:
        pm = _RE_PERIOD.search(t)              # `N월 누적` 은 정규식이 스스로 거른다
        if pm:
            yy, mm = int(pm.group("yy")), int(pm.group("mm"))
            if 1 <= mm <= 12:
                month = f"20{yy:02d}-{mm:02d}"
            break
    if not month:
        return None
    rev_text = mom = yoy = cum_text = cum_yoy = None
    # 누적 블록은 누적 토큰 + 뒤따르는 **값만 있는 토큰**(금액·% 만 있고 MoM/REV
    # 라벨·기준월이 없는 것)이다 — 실물 캡션(2026-09-10 스크린샷)은
    # `26년 1~8월 누적:` / `3조 3,868억 7,000만 TWD` / `(+YoY 39.3%)` 세 줄이고,
    # 첫 줄만 보면 금액·YoY 가 빈칸이 되며 셋째 줄의 YoY 가 당월 YoY 자리를
    # 넘본다. 기준월 줄·MoM 줄·각주·URL 은 블록을 끝낸다(한 줄 형식은 누적
    # 토큰 하나에 다 실려 있어 무해).
    i = 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if _RE_URL.fullmatch(t.rstrip(".,;")):
            continue
        if "누적" in t:
            block = [t]
            while i < len(toks) and _is_cum_value(toks[i]):
                block.append(toks[i])
                i += 1
            if cum_text is None:
                joined = " ".join(_RE_URL.sub("", b).strip() for b in block).strip()
                cum_text = joined[:200] or None
                cum_yoy = _labeled(_RE_YOY, joined)
                if cum_yoy is None:
                    cum_yoy = _pct(joined)
            continue
        if _RE_HEADTOK.search(t):
            continue                            # 헤더 토큰의 '매출' 은 라벨이 아니다
        # 한 토큰이 지표 셋을 다 실을 수 있다 — elif 로 가르지 않는다.
        if rev_text is None:
            ml = _RE_REVLBL.search(t)
            if ml:
                rest = _RE_REVCUT.split(t[ml.end():], 1)[0]
                rest = re.sub(r"^[\s:]*약\s*", "", rest.strip(" :")).strip()
                rev_text = rest[:120] or None
        if mom is None:
            mom = _labeled(_RE_MOM, t)
        if yoy is None:
            yoy = _labeled(_RE_YOY, t)
    if rev_text is None and mom is None and yoy is None:
        return None
    return {
        "ticker": ticker,
        "stock_name": name,
        "month": month,
        "rev_text": rev_text,
        "rev_twd": amount_twd(rev_text or ""),
        "mom": mom,
        "yoy": yoy,
        "cum_text": cum_text,
        "cum_yoy": cum_yoy,
        "parse_ver": PARSE_VER,
    }


COLS = ("ticker", "month", "stock_name", "rev_text", "rev_twd", "mom", "yoy",
        "cum_text", "cum_yoy", "chart_media", "source_message_id", "posted_at",
        "raw_text", "updated_at", "parse_ver")
_DERIVED = ("stock_name", "rev_text", "rev_twd", "mom", "yoy", "cum_text",
            "cum_yoy")
_REAL = ("rev_twd", "mom", "yoy", "cum_yoy")
_INT = ("source_message_id", "parse_ver")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  ticker TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  stock_name TEXT, rev_text TEXT, rev_twd REAL, mom REAL, yoy REAL,
  cum_text TEXT, cum_yoy REAL,
  chart_media TEXT, source_message_id INTEGER, posted_at TEXT,
  raw_text TEXT, updated_at TEXT, parse_ver INTEGER,
  PRIMARY KEY (ticker, month)
);
"""


def open_tw_revenue_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
    for col in COLS:                       # 스키마에서 도출한 마이그레이션(#24·#262)
        if col not in have:
            decl = "REAL" if col in _REAL else "INTEGER" if col in _INT else "TEXT"
            conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} {decl}")
    return conn


def upsert(conn: sqlite3.Connection, row: dict, *, chart_media,
           source_message_id, posted_at: str, raw_text: str) -> bool:
    tk, month = row.get("ticker"), row.get("month") or ""
    if not tk or not month:
        return False
    ex = conn.execute(f"SELECT * FROM {TABLE} WHERE ticker=? AND month=?",
                      (tk, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    incoming = {**row, "chart_media": chart_media,
                "source_message_id": source_message_id,
                "posted_at": posted_at, "raw_text": raw_text}
    stale = exd is not None and (exd.get("parse_ver") or 0) < PARSE_VER
    merged = {}
    for k in COLS:
        nv = incoming.get(k)
        if nv not in (None, ""):
            merged[k] = nv
        elif stale and k in _DERIVED:
            merged[k] = None
        else:
            merged[k] = exd.get(k) if exd else None
    merged["ticker"], merged["month"] = tk, month
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    conn.execute(f"INSERT OR REPLACE INTO {TABLE} ({','.join(COLS)}) VALUES "
                 f"({','.join(':' + c for c in COLS)})", merged)
    return True


def list_tw_revenue(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM {TABLE} t WHERE month = "
        f"(SELECT MAX(month) FROM {TABLE} WHERE ticker = t.ticker) "
        "ORDER BY month DESC, stock_name ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM {TABLE} WHERE ticker=? ORDER BY month ASC",
                        (ticker,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    parsed = parse_tw_monthly_revenue(caption)
    if parsed is None:
        return False
    return upsert(conn, parsed, chart_media=(media_paths or [None])[0],
                  source_message_id=source_message_id, posted_at=posted_at,
                  raw_text=caption)


def _metric(label: str, v) -> str:
    if v is None:
        return ""
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    arr = "▲" if v > 0 else "▼" if v < 0 else "—"
    return (f'<div class="kr-metric"><span class="kr-mlabel">{label}</span>'
            f'<span class="kr-mval {cls}">{arr}{v:+.1f}%</span></div>')


def _hist_table(hist: list[dict]) -> str:
    rows = [h for h in sorted(hist, key=lambda h: h.get("month") or "", reverse=True)
            if h.get("month")]
    if len(rows) < 2:
        return ""

    def _p(v):
        return "—" if v is None else f"{v:+.1f}%"
    trs = []
    for h in rows:
        trs.append("<tr>" + f"<td>{_html.escape(h['month'])}</td>"
                   f"<td>{_html.escape(h.get('rev_text') or '—')}</td>"
                   f"<td>{_p(h.get('mom'))}</td><td>{_p(h.get('yoy'))}</td>"
                   f"<td>{_p(h.get('cum_yoy'))}</td></tr>")
    return ('<table class="kr-htbl"><tr><th>월</th><th>매출(원문)</th><th>MoM</th>'
            '<th>YoY</th><th>누적 YoY</th></tr>' + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str) -> str:
    name = _html.escape(r.get("stock_name") or r.get("ticker") or "")
    tk = _html.escape(r.get("ticker") or "")
    mo = _html.escape(r.get("month") or "")
    summary = [f'<div class="kr-hd"><span class="kr-item">{name}</span>'
               f'<span class="kr-code">{tk}</span>'
               f'<span class="kr-mo">📅 {mo}</span></div>']
    if r.get("rev_text"):
        # 원문 그대로 — 통화·단위를 재해석하지 않는다.
        summary.append(f'<div class="kr-rev">🏦 매출 {_html.escape(r["rev_text"])}</div>')
    summary.append(_metric("📈 MoM", r.get("mom")))
    summary.append(_metric("📊 YoY", r.get("yoy")))
    if r.get("cum_text"):
        summary.append(f'<div class="kr-lead">🗓️ {_html.escape(r["cum_text"])}</div>')
    chart = ""
    media = r.get("chart_media") or ""
    if media:
        src = media if media.startswith(("http://", "https://", "/")) else media_prefix + media
        chart = (f'<div class="kr-chart"><img loading="lazy" src="{_html.escape(src)}" '
                 'alt=""></div>')
    return card_html("kr", summary, [chart, _hist_table(hist)])


_SUB = ("Badonions <b>월매출</b> 캡션(대만 상장사 月營收 공시)을 <b>종목별</b>로 정리한 "
        "별도 페이지 · 수출 데이터와는 다른 계열입니다 — 종목별 수출은 "
        f"<a href='{SIBLING}'>{SIBLING_LABEL}</a><br>"
        "🏦 매출은 원문 표기(TWD)를 <b>그대로</b> 옮기고, MoM·YoY 는 원문의 변화율입니다. "
        "누적(연초~당월) 줄은 원문 그대로 적습니다 — 재해석하지 않습니다.")


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    head = ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>대만 월매출 데이터(종목별)</title><style>" + _CSS +
            "</style></head><body>" + _THEME_JS)
    rows = list_tw_revenue(conn)
    nav = back_nav_html()
    if not rows:
        return (head + "<div class='wrap'>" + nav + f"<h1>{TITLE}</h1>"
                "<div class='empty'>아직 수집된 대만 월매출 데이터(종목별, 나쁜양파)가 "
                "없습니다.</div>"
                + asof_footer(0, "종목", None, max_ingest_iso(conn, TABLE))
                + "</div></body></html>")
    cards = [_card_html(r, history(conn, r["ticker"]), media_url_prefix) for r in rows]
    return (head + "<div class='wrap'>" + nav + f"<h1>{TITLE}</h1>"
            f"<div class='sub'>{_SUB}</div><div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), "종목",
                          max((r.get("month") or "") for r in rows) or None,
                          max_ingest_iso(conn, TABLE))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_tw_revenue_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
