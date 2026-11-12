"""한국 수출 데이터 (나쁜양파, **카드뉴스**) — 파서 + 저장 + 대시보드.

사용자 2026-11-12: 나쁜양파가 품목별 **카드뉴스 앨범**(이미지 최대 10장 +
요약 캡션)을 내기 시작했다. "한국 수출 데이터" 라는 제목으로 새 대시보드를
만들어 **그 포스트의 모든 그래프와 아래 코멘트까지** 보여 달라.

캡션 예시(사용자 캡처):
    📊 26.10 반도체 카드뉴스

    1. DRAM  YoY +529% · MoM +7%
    금액 🔺 7% 중량🔽 4% 단가 🔺 11%

    5. 프로세서·컨트롤러 IC  YoY +40% · MoM +7%
    금액 🔺 7% 중량 ➖  단가 🔺 6%

이미지 1장째는 표지(`KOREA EXPORTS · SEMICONDUCTORS · FINAL`, 출처: 관세청
10월 수출 확정치)이고 나머지가 품목별 차트다. 캡션은 **앨범에 붙어** 온다고
가정한다(캡처가 한 말풍선이다) — 앨범 멤버 이미지가 `media_paths` 로 전부
들어온다(`ingest_inbox._ingest_group`).

⚠️ 캡션의 수치는 **원문 그대로** 옮긴다(재계산 금지, #165). 품목 줄을 못
읽어도 포스트는 버리지 않는다 — 원문 전체를 화면이 그대로 싣는다(#43).
⚠️ 관련성 마커는 첫 줄의 `YY.MM … 카드뉴스` 하나다. 이 파서는 리스너·백필의
**관련성 필터**이기도 해서(`badonion_sources.is_relevant`), 마커를 느슨하게
잡으면 무관 글이 들어오고 좁게 잡으면 조용히 유실된다(#83).
"""

from __future__ import annotations

import html as _html
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trade.archive_template import asof_footer, back_nav_html, max_ingest_iso
from trade.cn_stock_flow import _CSS as _BASE_CSS, _THEME_JS

TABLE = "kr_cardnews"
TITLE = "📰 한국 수출 데이터"
SIBLING = "kr_stock.html"
SIBLING_LABEL = "한국 수출 데이터(종목별)"

# 첫 줄(앞 빈 줄 허용) = `📊 26.10 반도체 카드뉴스`. 이모지는 선택.
_RE_HEAD = re.compile(
    r"\A\s*[^\w\s]{0,4}[^\S\n]*(?P<yy>\d{2})[^\S\n]*[.\-/][^\S\n]*(?P<mm>\d{1,2})"
    r"[^\S\n]+(?P<topic>[^\n]*?)[^\S\n]*카드[^\S\n]*뉴스[^\S\n]*$", re.M)
_NUM = r"[+\-−]?\d+(?:\.\d+)?"
# `1. DRAM  YoY +529% · MoM +7%` — MoM 은 선택.
_RE_ITEM = re.compile(
    r"^[^\S\n]*(?P<rank>\d{1,2})[^\S\n]*[.)][^\S\n]*(?P<name>[^\n]+?)[^\S\n]+"
    r"YoY[^\S\n]*(?P<yoy>" + _NUM + r")[^\S\n]*%"
    r"(?:[^\S\n]*[·•|,][^\S\n]*MoM[^\S\n]*(?P<mom>" + _NUM + r")[^\S\n]*%)?",
    re.M | re.I)
_UP = "🔺▲↑⬆️📈"
_DOWN = "🔽🔻▼↓⬇️📉"
_FLAT = "➖—–"
# `금액 🔺 7%` · `중량🔽 4%` · `중량 ➖` (보합 = 숫자 없음)
_RE_MOVE = re.compile(
    r"(?P<k>금액|중량|단가)[^\S\n]*(?P<arr>[" + _UP + _DOWN + _FLAT + r"]*)"
    r"[^\S\n]*(?P<v>[+\-−]?\d+(?:\.\d+)?)?[^\S\n]*%?")


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace("−", "-"))
    except ValueError:
        return None


def _move(arr: str, v: str | None) -> float | None:
    """화살표 + 크기 → 부호 있는 %. 보합(➖)은 0. 화살표가 없으면 숫자에
    **명시된 부호**만 믿고, 부호도 없으면 None(방향을 추측하지 않는다, #165)."""
    a = arr or ""
    x = _num(v)
    if any(c in a for c in _FLAT):
        return 0.0
    if x is None:
        return None
    if any(c in a for c in _DOWN):
        return -abs(x)
    if any(c in a for c in _UP):
        return abs(x)
    return x if v and v[0] in "+-−" else None


def parse_kr_cardnews(caption: str) -> dict | None:
    """캡션 → {month, topic, items[]} 또는 None(카드뉴스가 아니면)."""
    if not caption:
        return None
    text = caption.replace("**", "").replace("：", ":")
    m = _RE_HEAD.search(text)
    if not m:
        return None
    yy, mm = int(m.group("yy")), int(m.group("mm"))
    if not 1 <= mm <= 12:
        return None
    topic = m.group("topic").strip(" ·-") or "기타"
    body = text[m.end():]
    items = []
    starts = list(_RE_ITEM.finditer(body))
    for i, im in enumerate(starts):
        seg = body[im.end(): starts[i + 1].start() if i + 1 < len(starts) else len(body)]
        mv = {}
        for mo in _RE_MOVE.finditer(seg):
            if mo.group("k") not in mv:
                mv[mo.group("k")] = _move(mo.group("arr"), mo.group("v"))
        items.append({"rank": int(im.group("rank")),
                      "name": im.group("name").strip(),
                      "yoy": _num(im.group("yoy")), "mom": _num(im.group("mom")),
                      "amt_mom": mv.get("금액"), "wt_mom": mv.get("중량"),
                      "price_mom": mv.get("단가")})
    return {"month": f"20{yy:02d}-{mm:02d}", "topic": topic,
            "title": m.group(0).strip(), "items": items}


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  month TEXT NOT NULL,
  topic TEXT NOT NULL,
  title TEXT,
  items_json TEXT,
  media_json TEXT,
  source_message_id INTEGER,
  posted_at TEXT,
  raw_text TEXT,
  updated_at TEXT,
  PRIMARY KEY (month, topic)
);
"""
COLS = ("month", "topic", "title", "items_json", "media_json",
        "source_message_id", "posted_at", "raw_text", "updated_at")


def open_kr_cardnews_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    """(월, 주제) 한 행. 같은 포스트를 다시 받으면 덮되, **이미지가 비어 온
    재수신이 이미지를 지우지 않게** 한다(필드 보존 — 형제 규약)."""
    p = parse_kr_cardnews(caption)
    if p is None:
        return False
    media = [m for m in (media_paths or []) if m]
    ex = conn.execute(f"SELECT media_json FROM {TABLE} WHERE month=? AND topic=?",
                      (p["month"], p["topic"])).fetchone()
    if not media and ex is not None:
        try:
            media = json.loads(ex["media_json"] or "[]")
        except ValueError:
            media = []
    conn.execute(
        f"INSERT OR REPLACE INTO {TABLE} ({','.join(COLS)}) VALUES "
        f"({','.join('?' for _ in COLS)})",
        (p["month"], p["topic"], p["title"],
         json.dumps(p["items"], ensure_ascii=False),
         json.dumps(media, ensure_ascii=False), source_message_id, posted_at,
         caption, datetime.now(timezone.utc).isoformat()))
    return True


def list_posts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM {TABLE} ORDER BY month DESC, posted_at DESC, topic ASC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k, dflt in (("items_json", []), ("media_json", [])):
            try:
                d[k[:-5]] = json.loads(d.get(k) or "null") or dflt
            except ValueError:
                d[k[:-5]] = dflt
        out.append(d)
    return out


_CSS = _BASE_CSS + """
.cn-post{background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:14px 16px;margin:0 0 16px}
.cn-hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.cn-title{font-weight:700;font-size:16px;color:var(--item)}
.cn-meta{font-size:12px;color:var(--muted);margin-left:auto}
.cn-gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;
  margin:0 0 12px}
.cn-gallery a{display:block;border:1px solid var(--chartbd);border-radius:8px;overflow:hidden}
.cn-gallery img{display:block;width:100%;height:auto}
.cn-tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin:0 0 12px}
.cn-tbl th,.cn-tbl td{padding:5px 8px;text-align:right;border-bottom:1px solid var(--row);
  white-space:nowrap}
.cn-tbl th{color:var(--muted);font-weight:500}
.cn-tbl td:nth-child(2),.cn-tbl th:nth-child(2){text-align:left;white-space:normal}
.cn-tbl-wrap{overflow-x:auto}
.cn-raw{white-space:pre-wrap;font-size:12.5px;line-height:1.6;background:var(--row);
  border-radius:8px;padding:10px 12px;margin:0}
.cn-sec{font-size:12px;color:var(--muted);margin:4px 0 6px}
.cn-old > summary{cursor:pointer;color:var(--accent);font-size:13px;margin:6px 0 12px}
"""


def _pc(v) -> str:
    if v is None:
        return '<td class="flat">—</td>'
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    return f'<td class="{cls}">{v:+,.0f}%</td>' if abs(v) >= 100 else \
        f'<td class="{cls}">{v:+.1f}%</td>'


def _items_table(items: list[dict]) -> str:
    if not items:
        return ""
    trs = "".join(
        f"<tr><td>{i.get('rank') or ''}</td><td>{_html.escape(i.get('name') or '')}</td>"
        + _pc(i.get("yoy")) + _pc(i.get("mom")) + _pc(i.get("amt_mom"))
        + _pc(i.get("wt_mom")) + _pc(i.get("price_mom")) + "</tr>"
        for i in items)
    return ('<div class="cn-sec">캡션 요약(원문 수치 그대로) — 전월 대비 금액·중량·단가는 '
            '원문 화살표의 방향과 크기입니다</div><div class="cn-tbl-wrap">'
            '<table class="cn-tbl"><tr><th>#</th><th>품목</th><th>YoY</th><th>MoM</th>'
            '<th>금액(전월비)</th><th>중량(전월비)</th><th>단가(전월비)</th></tr>'
            + trs + "</table></div>")


def _post_html(p: dict, media_prefix: str) -> str:
    imgs = []
    for m in p.get("media") or []:
        if not isinstance(m, str) or not m:
            continue
        src = m if m.startswith(("http://", "https://", "/")) else media_prefix + m
        s = _html.escape(src)
        imgs.append(f'<a href="{s}" target="_blank" rel="noopener">'
                    f'<img loading="lazy" src="{s}" alt=""></a>')
    gallery = (f'<div class="cn-gallery">{"".join(imgs)}</div>' if imgs else
               '<div class="cn-sec">⚠️ 이 포스트의 이미지를 받지 못했습니다 — '
               '캡션만 실었습니다</div>')
    posted = (p.get("posted_at") or "")[:16].replace("T", " ")
    meta = f"📅 {_html.escape(p.get('month') or '')}"
    if posted:
        meta += f" · 게시 {_html.escape(posted)} UTC"
    meta += f" · 그래프 {len(imgs)}장"
    return (f'<section class="cn-post"><div class="cn-hd">'
            f'<span class="cn-title">{_html.escape(p.get("title") or "")}</span>'
            f'<span class="cn-meta">{meta}</span></div>'
            + gallery + _items_table(p.get("items") or [])
            + '<div class="cn-sec">💬 원문 코멘트(캡션 전체)</div>'
            f'<pre class="cn-raw">{_html.escape(p.get("raw_text") or "")}</pre>'
            "</section>")


_SUB = ("Badonions <b>한국 수출 카드뉴스</b>(품목별 차트 앨범 + 요약 캡션)를 포스트 단위로 "
        "정리한 페이지 · 포스트의 그래프 전부와 캡션 원문을 그대로 싣습니다(관세청 수출 "
        f"확정치 기준 — 카드 표지의 원문 출처) · 종목별 수출은 <a href='{SIBLING}'>"
        f"{SIBLING_LABEL}</a><br>최신 달의 포스트를 펼쳐 두고, 지난 달은 아래 접어 둡니다.")


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    head = ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>한국 수출 데이터(카드뉴스)</title><style>" + _CSS +
            "</style></head><body>" + _THEME_JS)
    posts = list_posts(conn)
    nav = back_nav_html()
    if not posts:
        return (head + "<div class='wrap'>" + nav + f"<h1>{TITLE}</h1>"
                f"<div class='sub'>{_SUB}</div>"
                "<div class='empty'>아직 수집된 한국 수출 카드뉴스(나쁜양파)가 없습니다.</div>"
                + asof_footer(0, "포스트", None, max_ingest_iso(conn, TABLE))
                + "</div></body></html>")
    latest = posts[0]["month"]
    cur = [p for p in posts if p["month"] == latest]
    old = [p for p in posts if p["month"] != latest]
    body = "".join(_post_html(p, media_url_prefix) for p in cur)
    if old:
        body += (f'<details class="cn-old"><summary>지난 달 포스트 {len(old)}건 펼치기'
                 '</summary>' + "".join(_post_html(p, media_url_prefix) for p in old)
                 + "</details>")
    return (head + "<div class='wrap'>" + nav + f"<h1>{TITLE}</h1>"
            f"<div class='sub'>{_SUB}</div>" + body
            + asof_footer(len(posts), "포스트", latest, max_ingest_iso(conn, TABLE))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_kr_cardnews_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
