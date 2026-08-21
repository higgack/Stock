"""중국 수출 데이터 (나쁜양파, **종목별**) — 파서 + 저장 + 대시보드.

`cn_exports.py` 는 같은 채널의 **품목(HS) 기준** 중국 수출이고, 이 모듈은
**회사(종목) 기준**이다. PK 가 `(ticker, month)` 라 스키마가 달라 분리했다
(`kr_stock_exports.py`·`jp_stock_exports.py`·`my_stock_exports.py` 와 같은
이유 — 코드베이스 컨벤션상 소스별 독립 모듈).

발견 경위(사용자 2026-08-21): 26년 7월 중국 수출(기업별)이 채널에 떴는데
대시보드에 안 올라왔다. 품목 파서(`cn_exports`)의 마커는 `N월 수출 중국`
인데 이 포맷은 `중국 수출`(어순이 반대)이라 **관련성 필터를 통과 못 하고
조용히 드랍**됐다 — 저장도 안 되고 미매칭 알림에도 안 잡히는 유실.
일본이 2026-08-16, 말레이시아가 2026-08-20 에 겪은 것과 **같은 사고**다.
어순이 반대인 형제 포맷은 이제 네 번째다 — 새 나라의 종목판이 뜨면 같은
일이 또 난다고 보는 게 맞다(회귀가 그 짝을 강제한다).

캡션 예시(사용자 첨부 스크린샷 기반 — 렌더된 화면이라 원문 마크다운은
미확인. 형제 모듈과 같은 방침으로 포맷을 단정하지 않고 **관용 파싱**한다):
    TTM Technologies (TTMI)
    중국 수출
    26년 7월 Update

    단가 YoY: +45.2%
    수출액 YoY: +55.8%
    3M 수출액 YoY: +84.7%

    동시상관: 0.74
    방향 일치율: 87%
    선행상관: 0.85
    선행 방향 일치율: 93%

    - AI 서버·네트워크용 고다층 PCB 수요와 신규 생산능력 램프가 겹치며
      데이터센터 제품 믹스가 개선되는 구간입니다.

⚠️ 티커는 **국적 무관**이다 — 중국에서 수출하는 기업을 다루므로 TTMI·DELL
처럼 미국 상장이 섞여 온다(형제 종목판과 같은 성질).

⚠️ 이 소스는 **네 계열이 한 캡션에 같이 온다**(TTM 실측). 말레이시아판은
종목마다 동시/선행 중 하나만 왔는데 중국판은 둘 다 온다 — 그러니 열을
합치면 안 된다(정의가 다른 값이 한 열에 섞인다, 실수 #34).

⚠️ 수준값(부호·화살표 금지, 실수 #39):
  · 동시상관 0.74 / 선행상관 0.85  — 상관계수(-1~1). % 아니다.
  · 방향 일치율 87%                 — 비율 자체.

⚠️ 불릿(`- …`) 줄이 **두 종류**다(사용자 2026-08-21 "이런 코멘트도 포함하면
좋겠어"). 형제 모듈은 `매출` 이 들어간 줄만 잡았는데, 중국판 코멘트는
그냥 `매출` 이라는 단어를 품는다(DELL 실측: "백로그의 **매출** 전환 속도가
다음 확인 포인트입니다"). 단어로 가르면 코멘트가 '관련 매출' 칸에 실려
숫자 자리에 문장이 앉는다 → **금액 토큰이 같이 있는지**로 가른다(구조로
판정, 실수 #24). 둘 다 원문 그대로 싣는다 — 통화·단위를 재해석하면
파싱 버그가 곧 틀린 숫자가 된다.

월별 rolling: PK 가 `(ticker, month)` 라 8월분이 오면 새 행이 들어가고
카드는 `MAX(month)` 로 자동 교체된다(기존 모듈과 동일 규약).
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trade.archive_template import (asof_footer, back_nav_html,
                                    card_html, max_ingest_iso)

# 파서 스키마 버전. 올리면 저장된 옛 행의 파생 필드를 upsert 가 **버리고**
# 다시 채운다 — 파서를 고쳐도 이미 구운 값이 안 바뀌는 함정 차단(실수 #18).
_PARSE_VER = 1

_NL = r"\n(?:[^\S\n]*\n)*"      # 줄바꿈(사이 빈 줄 허용)
# ⚠️ 헤더 **한 줄** 안에서는 `\s` 를 쓰지 않는다 — `\s` 가 개행을 먹어
# `어떤회사\n(ABCD) 중국 수출` 같은 무관 조합이 통과한다(jp_stock 이 독립
# 리뷰에서 실측한 함정). 줄바꿈이 허용되는 지점은 `_NL` 자리뿐이다.
_RE_BLOCK = re.compile(
    r"^[^\S\n]*(?P<name>[^\n(]+?)[^\S\n]*\((?P<ticker>[A-Za-z0-9.\-]{2,10})\)"
    r"[^\S\n]*(?:" + _NL + r")?"
    r"[^\S\n]*중국[^\S\n]*수출[^\S\n]*(?:Update)?[^\S\n]*" + _NL +
    r"[^\S\n]*(?P<yy>\d{2})[^\S\n]*년[^\S\n]*(?P<mm>\d{1,2})[^\S\n]*월"
    r"[^\S\n]*(?:Update)?",
    re.M | re.I)

# 지표 — 라벨과 YoY 사이 콜론 위치가 소스마다 달라 양쪽을 한 패턴에서 받는다.
# 전부 선택적 — 포맷이 조금 바뀌어도 메시지 전체를 버리지 않는다.
_RE_EXP_YOY_ANY = re.compile(
    r"(?P<pfx>\d+M)?\s*수출액\s*:?\s*YoY\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)\s*%",
    re.I)
_RE_PRICE_YOY = re.compile(
    r"단가\s*:?\s*YoY\s*:?\s*([+\-]?\d+(?:\.\d+)?)\s*%", re.I)
# 접두 없는 맨 `상관` 은 어느 계열인지 알 수 없으므로 **받지 않는다**
# (추측 저장 금지 — my_stock 이 그렇게 선행값을 동시 칸에 흘렸다).
_RE_CORR_ANY = re.compile(
    r"(?:(?P<lead>(?:\d+Q\s*)?선행)|(?P<coin>동시))\s*상관"
    r"\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)")
_RE_DIRHIT_ANY = re.compile(
    r"(?P<lead>선행\s*)?방향\s*일치율\s*:?\s*(?P<v>\d+(?:\.\d+)?)\s*%")
# 불릿 줄 — 관련 매출과 코멘트를 **같이** 걷어 아래에서 가른다.
# `*` 는 넣지 않는다: 각주(`* …`)와 겹쳐 ⚠️ 슬롯을 뺏는다(jp_stock 실측).
_RE_BULLET = re.compile(r"^[^\S\n]*[-•][^\S\n]+(?P<t>[^\n]+)$", re.M)
# 금액 토큰 — 통화기호 + 숫자, 또는 숫자 + 규모 단위. '매출' 이라는 단어가
# 아니라 **이게 있어야** 관련 매출 줄이다(위 docstring 참조).
_RE_MONEY = re.compile(
    r"[$￥¥€₩]\s*[\d,]+(?:\.\d+)?"
    r"|[\d,]+(?:\.\d+)?\s*(?:[BMK]\b|억|조|백만|십억|천만)", re.I)
# 본문 각주("* …") — 해석에 필수인 경고라 버리지 않고 싣는다.
# ⚠️ `*` 뒤 **공백 필수**: `**` 제거 후 남는 강조(`*중요*`)가 각주로 승격돼
# 카드 ⚠️ 슬롯을 차지하던 오탐 차단(jp_stock 독립 리뷰 실측).
_RE_NOTE = re.compile(r"^[^\S\n]*\*[^\S\n]+(?P<n>[^\n]+)$", re.M)
# 지표·URL·홍보문·불릿이 아닌 첫 줄 = 품목 설명(있을 때만).
_SKIP_LINE = re.compile(
    r"(수출액|단가|YoY|상관|방향\s*일치율|https?://|맵핑|Update"
    r"|중국\s*수출|^\s*[-•*]|^\s*\d{2}\s*년)", re.I)


def _f(pat: re.Pattern, text: str):
    m = pat.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _is_revenue_line(s: str) -> bool:
    """'관련 매출' 줄인가 — `매출` **과** 금액 토큰이 같이 있어야 한다.

    단어만 보면 코멘트가 숫자 칸에 앉는다(DELL 실측: "백로그의 매출 전환
    속도가 다음 확인 포인트입니다")."""
    return bool("매출" in s and _RE_MONEY.search(s))


def _item_line(text: str, header_end: int) -> str | None:
    """헤더 이후 첫 '설명' 줄. 없으면 None(품목 표기가 없는 캡션이 기본)."""
    for raw in text[header_end:].splitlines():
        s = raw.strip()
        if not s or _SKIP_LINE.search(s):
            continue
        return s[:120]
    return None


def parse_cn_stock_export(caption: str) -> dict | None:
    """캡션 → {stock_name, ticker, month, 지표…} 또는 None."""
    if not caption:
        return None
    # ⚠️ `**`(마크다운 볼드)만 걷어낸다. `*` 를 전부 지우면 각주 마커까지
    # 사라져 note 가 죽는다(형제 모듈과 같은 규약).
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
    # 메시지에 두 회사가 담겼을 때 A 카드에 B 의 값이 섞여 들어간다
    # (jp_stock 독립 리뷰 실측 — 값이 틀린 채로 조용히 저장됨).
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
    # 상관·방향일치율 — 접두(선행) 유무로 계열을 가른다. 접두 없는 것이 동시.
    corr = lead_corr = None
    for mo in _RE_CORR_ANY.finditer(seg):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        if mo.group("lead"):
            if lead_corr is None:
                lead_corr = v
        elif corr is None:
            corr = v
    dir_hit = lead_dir_hit = None
    for mo in _RE_DIRHIT_ANY.finditer(seg):
        try:
            v = float(mo.group("v"))
        except ValueError:
            continue
        if mo.group("lead"):
            if lead_dir_hit is None:
                lead_dir_hit = v
        elif dir_hit is None:
            dir_hit = v
    # 불릿을 금액 유무로 가른다 — 코멘트를 숫자 칸에 넣지 않는다.
    revenue = comment = None
    for mo in _RE_BULLET.finditer(seg):
        s = (mo.group("t") or "").strip()
        if not s:
            continue
        if _is_revenue_line(s):
            if revenue is None:
                revenue = s[:200]
        elif comment is None:
            comment = s[:400]
    notes = [n.strip() for n in _RE_NOTE.findall(seg) if n.strip()]
    return {
        "ticker": m.group("ticker").upper(),
        "stock_name": name,
        "month": f"20{yy:02d}-{mm:02d}",
        "item": _item_line(seg, m.end() - m.start()),
        "export_yoy": exp_yoy,
        "export_yoy_3m": exp_yoy_3m,
        "price_yoy": _f(_RE_PRICE_YOY, seg),
        "corr": corr,
        "dir_hit": dir_hit,
        "lead_corr": lead_corr,
        "lead_dir_hit": lead_dir_hit,
        "parse_ver": _PARSE_VER,
        "revenue": revenue,
        "comment": comment,
        "note": " / ".join(notes)[:300] or None,
    }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cn_stock_exports (
  ticker TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  stock_name TEXT,
  item TEXT,
  export_yoy REAL, export_yoy_3m REAL, price_yoy REAL,
  corr REAL, dir_hit REAL,
  lead_corr REAL, lead_dir_hit REAL,
  parse_ver INTEGER,
  revenue TEXT,
  comment TEXT,
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
         "export_yoy_3m", "price_yoy", "corr", "dir_hit", "lead_corr",
         "lead_dir_hit", "revenue", "comment", "note", "chart_media",
         "source_message_id", "posted_at", "raw_text", "updated_at",
         "parse_ver")

# 파서가 만들어내는(= 캡션에서 다시 뽑을 수 있는) 필드. 아래 upsert 가
# 옛 버전으로 구운 값을 버릴 때 이 목록만 비운다 — chart_media·raw_text 처럼
# 파싱 산물이 아닌 것은 보존해야 한다.
_DERIVED = ("stock_name", "item", "export_yoy", "export_yoy_3m", "price_yoy",
            "corr", "dir_hit", "lead_corr", "lead_dir_hit", "revenue",
            "comment", "note")


def open_cn_stock_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    # 이미 만들어진 DB 에 컬럼을 더한다 — CREATE TABLE IF NOT EXISTS 는
    # 기존 테이블을 손대지 않으므로, 이게 없으면 배포 후 첫 쓰기가 터진다.
    have = {r["name"] for r in
            conn.execute("PRAGMA table_info(cn_stock_exports)")}
    for col, decl in (("comment", "TEXT"), ("parse_ver", "INTEGER")):
        if col not in have:
            conn.execute(
                f"ALTER TABLE cn_stock_exports ADD COLUMN {col} {decl}")
    return conn


def upsert_cn_stock(conn: sqlite3.Connection, row: dict, *, chart_media,
                    source_message_id, posted_at: str, raw_text: str) -> bool:
    """필드 보존 병합 upsert — 부분 재전송이 기존 good 필드를 null 로 덮지
    않게 한다(기존 나쁜양파 모듈과 동일 규약)."""
    tk, month = row.get("ticker"), row.get("month") or ""
    if not tk or not month:
        return False
    ex = conn.execute(
        "SELECT * FROM cn_stock_exports WHERE ticker=? AND month=?",
        (tk, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    incoming = {**row, "chart_media": chart_media,
                "source_message_id": source_message_id,
                "posted_at": posted_at, "raw_text": raw_text}
    # ⚠️ 필드 보존 병합은 **파서를 고쳐도 옛 값이 살아남게** 만든다(실수 #18·
    # #21b). 저장된 parse_ver 가 낮으면 파생 필드는 **새 파싱만** 쓴다.
    stale = exd is not None and (exd.get("parse_ver") or 0) < _PARSE_VER
    merged = {}
    for k in _COLS:
        nv = incoming.get(k)
        if nv not in (None, ""):
            merged[k] = nv
        elif stale and k in _DERIVED:
            merged[k] = None
        else:
            merged[k] = exd.get(k) if exd else None
    merged["ticker"] = tk
    merged["month"] = month
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join(f":{c}" for c in _COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO cn_stock_exports ({','.join(_COLS)}) "
        f"VALUES ({placeholders})", merged)
    return True


def list_cn_stock(conn: sqlite3.Connection) -> list[dict]:
    """종목별 **최신월** 1행 — 새 월이 오면 카드가 자동 교체된다."""
    rows = conn.execute(
        "SELECT * FROM cn_stock_exports t WHERE month = "
        "(SELECT MAX(month) FROM cn_stock_exports WHERE ticker = t.ticker) "
        "ORDER BY month DESC, stock_name ASC").fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cn_stock_exports WHERE ticker=? ORDER BY month ASC",
        (ticker,)).fetchall()
    return [dict(r) for r in rows]


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    parsed = parse_cn_stock_export(caption)
    if parsed is None:
        return False
    return upsert_cn_stock(conn, parsed, chart_media=(media_paths or [None])[0],
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
.kr-mlabel{color:var(--muted);min-width:96px}
.kr-mval{font-weight:600;color:var(--text)}
.up{color:#e5484d}.down{color:#3b82f6}.flat{color:var(--muted)}
.kr-lead{font-size:11.5px;color:var(--muted);line-height:1.6}
.kr-rev{font-size:12px;color:var(--text);margin-top:2px}
.kr-cmt{font-size:12.5px;color:var(--text);line-height:1.62;margin-top:2px;
  padding:8px 10px;background:var(--row);border-radius:7px}
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
    """변화율 지표 — 부호·화살표·색상."""
    if v is None:
        return ""
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    arr = "▲" if v > 0 else "▼" if v < 0 else "—"
    return (f'<div class="kr-metric"><span class="kr-mlabel">{label}</span>'
            f'<span class="kr-mval {cls}">{arr}{v:+.1f}{unit}</span></div>')


def _level(label: str, v, unit: str = "", digits: int = 2) -> str:
    """**수준값** 지표 — 부호·화살표 없이 그대로. 상관(-1~1)·방향 일치율(%)
    에 `+0.74 ▲` 를 붙이면 '0.74 상승'으로 읽힌다(실수 #39)."""
    if v is None:
        return ""
    return (f'<div class="kr-metric"><span class="kr-mlabel">{label}</span>'
            f'<span class="kr-mval">{v:.{digits}f}{unit}</span></div>')


def _hist_table(hist: list[dict]) -> str:
    rows = [h for h in sorted(hist, key=lambda h: h.get("month") or "",
                              reverse=True) if h.get("month")]
    if len(rows) < 2:
        return ""
    # 값이 있는 계열의 열만 낸다. 두 계열을 한 열에 합치면 세로로 읽는
    # 자리에서 정의가 갈린다(실수 #32·#34).
    cols = [("월", None, None),
            ("수출액 YoY", "export_yoy", "pct"),
            ("3M 수출액 YoY", "export_yoy_3m", "pct")]
    if any(h.get("price_yoy") is not None for h in rows):
        cols.append(("단가 YoY", "price_yoy", "pct"))
    if any(h.get("corr") is not None or h.get("dir_hit") is not None
           for h in rows):
        cols.append(("동시상관", "corr", "lv2"))
        cols.append(("방향 일치율", "dir_hit", "lv0"))
    if any(h.get("lead_corr") is not None or h.get("lead_dir_hit") is not None
           for h in rows):
        cols.append(("선행상관", "lead_corr", "lv2"))
        cols.append(("선행 방향 일치율", "lead_dir_hit", "lv0"))

    def _fmt(h, key, kind):
        v = h.get(key)
        if v is None:
            return "—"
        if kind == "pct":
            return f"{v:+.1f}%"
        return f"{v:.2f}" if kind == "lv2" else f"{v:.0f}%"

    trs = []
    for h in rows:
        tds = [f"<td>{_html.escape(h['month'])}</td>"]
        tds += [f"<td>{_fmt(h, k, kind)}</td>" for _lab, k, kind in cols[1:]]
        trs.append("<tr>" + "".join(tds) + "</tr>")
    head = "".join(f"<th>{lab}</th>" for lab, _k, _kind in cols)
    return ('<table class="kr-htbl"><tr>' + head + "</tr>"
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
    # 계열을 라벨에 박는다 — 둘을 같은 이름으로 내면 정의가 섞인다(#34).
    summary.append(_level("🔗 동시상관", r.get("corr")))
    summary.append(_level("🎯 방향 일치율", r.get("dir_hit"), "%", 0))
    summary.append(_level("🔗 선행상관", r.get("lead_corr")))
    summary.append(_level("🎯 선행 방향 일치율", r.get("lead_dir_hit"), "%", 0))
    if r.get("revenue"):
        # 원문 그대로 — 통화·단위를 재해석하지 않는다.
        summary.append(f'<div class="kr-rev">🏦 {_html.escape(r["revenue"])}</div>')
    if r.get("comment"):
        # 사용자 2026-08-21 "이런 코멘트도 포함하면 좋겠어" — 원문 그대로.
        summary.append(f'<div class="kr-cmt">💬 {_html.escape(r["comment"])}</div>')
    if r.get("note"):
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
         "<title>중국 수출 데이터(종목별)</title><style>" + _CSS +
         "</style></head><body>" + _THEME_JS)

_SUB = ("Badonions 중국 수출 캡션을 <b>종목별</b>로 정리한 별도 페이지 · "
        "중국에서 수출하는 기업이라 <b>국적이 섞여 있습니다</b>"
        "(TTMI·DELL 등 미국 상장 포함) · 품목(HS) 기준은 "
        "<a href='cn.html'>중국 수출 데이터(품목)</a><br>"
        "<b>상관</b>(-1~1)·<b>방향 일치율</b>(%)은 변화율이 아니라 "
        "<b>수준값</b>이라 부호·화살표 없이 그대로 적습니다.<br>"
        "<b>동시</b>(같은 시점 수출↔실적)와 <b>선행</b>(수출이 실적에 앞서는 "
        "관계)은 <b>정의가 달라 서로 비교할 수 없으므로</b> 칸을 나누고 "
        "라벨에 계열을 적습니다.<br>"
        "💬 는 원문 코멘트를 <b>그대로</b> 옮긴 것이고, 🏦 는 금액이 적힌 "
        "관련 매출 줄입니다 — 둘 다 재해석하지 않습니다.")


def render_html(conn: sqlite3.Connection, *, media_url_prefix: str = "../") -> str:
    rows = list_cn_stock(conn)
    nav = f"{back_nav_html()}"
    if not rows:
        # 빈 상태에서도 페이지를 만들어 nav 404 를 막는다(기존 모듈 규약).
        return (_HEAD + "<div class='wrap'>" + nav +
                "<h1>🏮 중국 수출 데이터(종목별)</h1>"
                "<div class='empty'>아직 수집된 중국 수출 데이터"
                "(종목별, 나쁜양파)가 없습니다.</div>"
                + asof_footer(0, "종목", None,
                              max_ingest_iso(conn, "cn_stock_exports"))
                + "</div></body></html>")
    cards = [_card_html(r, history(conn, r["ticker"]), media_url_prefix)
             for r in rows]
    return (_HEAD + "<div class='wrap'>" + nav +
            "<h1>🏮 중국 수출 데이터(종목별)</h1>"
            f"<div class='sub'>{_SUB}</div>"
            "<div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), "종목",
                          max((r.get("month") or "") for r in rows) or None,
                          max_ingest_iso(conn, "cn_stock_exports"))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_cn_stock_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
