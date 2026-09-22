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
from trade import badonion_metrics as _metrics
from trade import stock_link as _sl
from trade import kr_company_flow as _flow
from trade.archive_template import asof_footer, back_nav_html, max_ingest_iso
from trade.archive_template import card_html

# 2026-09-16: 같은 채널이 한국 수출을 **두 문법**으로 낸다. 옛 판(아래
# `_RE_BLOCK`)은 `HPSP (403870)` / `한국 수출` / `26년 7월 Update` + 지표이고,
# 새 판은 품목판 어순(`8월 수출 한국`) + `▶️ 회사 — 품목` + 금액/YoY/MoM 이다.
# 새 판을 받는 소스가 아예 없어 LS ELECTRIC 이 조용히 드랍되고 있었다
# (#83·#261·#330·#332 계열). 사용자 결정(2026-09-16): **한 페이지에 합친다**.
# 문법만 `kr_company_flow` 가 갖고(#38·#84) 저장·렌더는 이 모듈이 계속 갖는다.
EXPORT_FLOW = _flow.Flow(key="export", marker="수출", amount="수출액",
                         table="kr_stock_exports",
                         title="🏢 한국 수출 데이터(종목별)",
                         country="한국")

# 6자리 종목코드가 없는 금액판은 **회사명**을 PK 로 쓴다. 접두를 붙여 진짜
# 코드와 섞이지 않게 하고, 화면은 접두가 붙은 키를 코드로 찍지 않는다.
_NAME_KEY = "nm:"

# 헤더 블록 = "종목명 (6자리코드)" 줄 → 곧바로 "한국 수출" → "NN년 N월 Update".
# ⚠️ 세 마커를 **따로** 찾으면 안 된다 — 캡션 어딘가에 우연히 셋이 흩어져
# 있는 무관 메시지가 통과한다. 이 파서는 unstored_check·dashboard 의 **억제
# 필터**로도 쓰여서, 오탐이 곧 "저장도 안 되고 미매칭 알림에도 안 잡히는"
# 조용한 유실이 된다(2026-08-16 독립 리뷰). 인접성을 구조로 강제한다.
_NL = r"\n(?:[^\S\n]*\n)*"      # 줄바꿈(사이 빈 줄 허용)
# ⚠️ 원천은 같은 보드도 레이아웃을 오간다 — 일본이 6월 1줄 → 7월 3줄로
# 바뀌어 통째로 드랍된 사고(2026-08-28)의 **반대 방향** 구멍이 여기 있었다:
# 한국은 3줄만 받아 1줄 형식이 오면 같은 유실이 난다. 형제 전수 회귀가
# 잡아냈다(#24). `\s*` 는 개행을 먹어 무관 조합을 통과시키므로 쓰지 않는다.
_RE_BLOCK = re.compile(
    r"^[^\S\n]*(?P<name>[^\n(]+?)[^\S\n]*\((?P<code>\d{6})\)"
    r"[^\S\n]*(?:" + _NL + r")?"
    r"[^\S\n]*한국[^\S\n]*수출[^\S\n]*(?:Update)?[^\S\n]*" + _NL +
    r"[^\S\n]*(?P<yy>\d{2})[^\S\n]*년[^\S\n]*(?P<mm>\d{1,2})[^\S\n]*월"
    r"[^\S\n]*(?:Update)?",
    re.M | re.I)

# 지표 — 전부 선택적(포맷이 조금 바뀌어도 메시지 전체를 버리지 않는다).
_RE_PRICE_YOY = re.compile(r"단가\s*YoY\s*:?\s*([+\-]?\d+(?:\.\d+)?)\s*%", re.I)
# 3M 접두 유무를 **한 패턴에서** 구분한다. 옛 구현은 `(?<!3M\s)` 룩비하인드로
# 걸렀는데 공백이 없는 '3M수출액' 을 못 막아 두 값이 같아졌다(독립 리뷰).
_RE_EXP_YOY_ANY = re.compile(
    r"(?P<pfx>\d+M)?\s*수출액\s*YoY\s*:?\s*(?P<v>[+\-]?\d+(?:\.\d+)?)\s*%", re.I)
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
        # 상관·방향 일치율 — 옛 판은 **선행 쌍만** 받아, 원천이 2026-08 에
        # 실은 `동시상관`·`방향 일치율` 이 통째로 버려졌다(형제 my·cn 은
        # 둘 다 받고 있었다, #27). 공용 파서 하나로 모은다(#38·#84).
        **_metrics.parse_corr_fields(text),
        "rev_quarter": revs[0]["quarter"] if revs else None,
        "rev_value_krw_b": revs[0]["value_krw_b"] if revs else None,
        "rev_yoy": revs[0]["yoy"] if revs else None,
    }


def parse_kr_stock_flow(caption: str) -> dict | None:
    """새 금액판 캡션 → {stock_name, item, months[]} 또는 None."""
    return _flow.parse(caption, EXPORT_FLOW)


def parse_any(caption: str) -> dict | None:
    """레지스트리가 쓰는 **관련성 필터** — 두 문법 중 하나라도 맞으면 통과.

    ⚠️ 반환 모양이 둘이다(옛 판은 단일 월 dict, 새 판은 `months[]`). 호출부는
    `is None` 만 보므로 계약은 지켜지지만, 새 소비자가 생기면 키를 먼저 볼 것
    (#345 반환 모양을 확인하고 쓸 것). 저장은 `ingest` 가 갈라 처리한다."""
    return parse_kr_stock_export(caption) or parse_kr_stock_flow(caption)


# 법인 접미어. 원천이 같은 회사를 판마다 다르게 적는다(실측: 금액판
# `LS ELECTRIC Co., Ltd.` ↔ 옛 판 `LS ELECTRIC`).
_CORP_SUFFIX = ("coltd", "co", "ltd", "inc", "corp", "corporation",
                "limited", "plc", "llc", "ag", "sa", "주식회사")


def fold_company(name: str) -> str:
    """비교용으로 접은 회사명 — 대소문자·공백·구두점·법인 접미어를 뺀다.

    ⚠️ 접기는 **맞추기 위한 것**이지 표시용이 아니다. 너무 세게 접으면 다른
    회사가 합쳐지므로(그건 더 나쁜 오류다) 꼬리의 법인 접미어만 벗긴다."""
    t = "".join(ch for ch in (name or "").lower() if ch.isalnum())
    changed = True
    while changed and t:
        changed = False
        for suf in _CORP_SUFFIX:
            if len(t) > len(suf) and t.endswith(suf):
                t, changed = t[:-len(suf)], True
                break
    return t or "".join(ch for ch in (name or "").lower() if ch.isalnum())


# 금액판이 만드는 칸 — 파서 판이 오르면 이것만 다시 채운다.
_FLOW_DERIVED = ("item", "export_value_musd", "export_yoy", "export_mom")


def _absorb_synthetic(conn: sqlite3.Connection, *, code: str,
                      name: str) -> None:
    """합성키 행을 진짜 코드로 옮긴다 — **같은 달이 이미 있으면 병합**한다.

    ⚠️ `UPDATE OR REPLACE` 로 쓰면 충돌한 달의 **진짜 코드 행이 통째로
    지워진다**(합성 행이 그 자리를 차지한다). 그 행엔 옛 지표판만 아는 값
    (상관·분기매출)이 들어 있어 조용한 데이터 유실이 된다 — 이 모듈의
    다른 쓰기와 같은 **필드 보존 병합** 규약을 여기서도 지킨다(#45).
    """
    # ⚠️ 합성키도 **접은 이름**으로 찾는다 — 금액판이 `LS ELECTRIC Co., Ltd.`
    # 로 저장해 뒀는데 옛 판이 `LS ELECTRIC` 로 오면 정확 일치로는 못 찾아
    # 카드가 둘로 남는다(#45).
    want = fold_company(name)
    rows = [r for r in conn.execute(
        "SELECT * FROM kr_stock_exports WHERE stock_code LIKE ?",
        (_NAME_KEY + "%",)).fetchall()
        if fold_company(r["stock_name"] or "") == want]
    for r in rows:
        src = dict(r)
        synth = src["stock_code"]
        month = src.get("month") or ""
        ex = conn.execute(
            "SELECT * FROM kr_stock_exports WHERE stock_code=? AND month=?",
            (code, month)).fetchone()
        if ex is None:
            conn.execute(
                "UPDATE kr_stock_exports SET stock_code=? "
                "WHERE stock_code=? AND month=?", (code, synth, month))
            continue
        keep = dict(ex)
        for k in _COLS:
            if keep.get(k) in (None, "") and src.get(k) not in (None, ""):
                keep[k] = src[k]
        keep["stock_code"] = code
        keep["month"] = month
        conn.execute(
            f"INSERT OR REPLACE INTO kr_stock_exports ({','.join(_COLS)}) "
            f"VALUES ({','.join(f':{c}' for c in _COLS)})",
            {k: keep.get(k) for k in _COLS})
        conn.execute(
            "DELETE FROM kr_stock_exports WHERE stock_code=? AND month=?",
            (synth, month))


def _resolve_code(conn: sqlite3.Connection, *, code: str | None,
                  name: str) -> str:
    """두 문법이 같은 회사를 가리키면 **카드가 둘이 되면 안 된다**(#45).

    · 코드가 있으면(옛 판) 그 이름으로 쌓인 합성키 행을 진짜 코드로 옮긴다.
    · 코드가 없으면(금액판) 같은 이름의 진짜 코드가 이미 있으면 그걸 쓰고,
      없을 때만 합성키를 만든다.
    """
    if code:
        _absorb_synthetic(conn, code=code, name=name)
        return code
    # ⚠️ 두 판이 회사를 **다르게 적는다** — 금액판 `LS ELECTRIC Co., Ltd.` ·
    # 옛 판 `LS ELECTRIC`. 글자 그대로 비교하면 같은 회사가 카드 둘이 된다
    # (#45, 독립 리뷰 실측). 대소문자·공백·구두점과 법인 접미어를 걷어낸
    # **접은 이름**으로 맞춘다 — 표시 이름은 원문 그대로 둔다(#74 값은 원본).
    want = fold_company(name)
    for r in conn.execute(
            "SELECT stock_code, stock_name FROM kr_stock_exports "
            "WHERE stock_code NOT LIKE ? ORDER BY month DESC",
            (_NAME_KEY + "%",)):
        if r["stock_code"] and fold_company(r["stock_name"] or "") == want:
            return r["stock_code"]
    return _NAME_KEY + name


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kr_stock_exports (
  stock_code TEXT NOT NULL,
  month TEXT NOT NULL DEFAULT '',
  stock_name TEXT,
  price_yoy REAL, export_yoy REAL, export_yoy_3m REAL,
  export_value_musd REAL, export_mom REAL, item TEXT,
  parse_ver INTEGER,
  corr REAL, dir_hit REAL, corr_basis TEXT,
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
         "export_yoy_3m", "export_value_musd", "export_mom",
         "item", "parse_ver") + _metrics.FIELDS + (
         "rev_quarter",
         "rev_value_krw_b", "rev_yoy", "chart_media", "source_message_id",
         "posted_at", "raw_text", "updated_at")


def open_kr_stock_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    # 기존 DB 에 컬럼 추가 — CREATE TABLE IF NOT EXISTS 는 기존 테이블을
    # 손대지 않아, 이게 없으면 배포 후 첫 쓰기가 터진다(형제와 같은 규약).
    have = {r["name"] for r in
            conn.execute("PRAGMA table_info(kr_stock_exports)")}
    added = dict(_metrics.FIELD_TYPES)
    added.update({"export_value_musd": "REAL", "export_mom": "REAL",
                  "item": "TEXT", "parse_ver": "INTEGER"})
    for col, decl in added.items():
        if col not in have:
            conn.execute(
                f"ALTER TABLE kr_stock_exports ADD COLUMN {col} {decl}")
    return conn


def upsert_kr_stock(conn: sqlite3.Connection, row: dict, *, chart_media,
                    source_message_id, posted_at: str, raw_text: str) -> bool:
    """필드 보존 병합 upsert — 부분 재전송이 기존 good 필드를 null 로 덮지
    않게 한다(기존 나쁜양파 모듈과 동일 규약)."""
    month = row.get("month") or ""
    name = (row.get("stock_name") or "").strip()
    if not month or not (row.get("stock_code") or name):
        return False
    code = _resolve_code(conn, code=row.get("stock_code"), name=name)
    ex = conn.execute(
        "SELECT * FROM kr_stock_exports WHERE stock_code=? AND month=?",
        (code, month)).fetchone()
    exd = dict(ex) if ex is not None else None
    # 금액판 파생 필드는 **파서 판이 올라가면 비우고 다시 채운다**(#18) —
    # 필드 보존 병합은 새 파서가 그 칸을 비워도 옛 값을 남기기 때문이다
    # (`value_mom` 은 원천이 안 주면 실제로 None 이 된다, 리뷰 실측).
    # ⚠️ 옛 지표판(상관·분기매출)은 건드리지 않는다 — 그건 다른 문법이다.
    if (exd is not None and row.get("parse_ver")
            and (exd.get("parse_ver") or 0) < row["parse_ver"]):
        for k in _FLOW_DERIVED:
            exd[k] = None
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
    """두 문법을 모두 받는다 — 옛 지표판은 1행, 새 금액판은 **메시지 안의 전
    개월**(최신 + 최근 추이)을 각각 한 행으로 넣는다(형제 품목판과 같은 규약)."""
    chart = (media_paths or [None])[0]
    parsed = parse_kr_stock_export(caption)
    if parsed is not None:
        return upsert_kr_stock(conn, parsed, chart_media=chart,
                               source_message_id=source_message_id,
                               posted_at=posted_at, raw_text=caption)
    flow = parse_kr_stock_flow(caption)
    if flow is None:
        return False
    saved = False
    for mrow in flow["months"]:
        ok = upsert_kr_stock(
            conn,
            {"stock_code": None, "stock_name": flow["stock_name"],
             "item": flow["item"], "month": mrow["month"],
             "export_value_musd": mrow["value_musd"],
             "export_yoy": mrow["value_yoy"],
             "export_mom": mrow["value_mom"],
             "parse_ver": _flow.PARSE_VER},
            chart_media=chart, source_message_id=source_message_id,
            posted_at=posted_at, raw_text=caption)
        saved = saved or ok
    return saved


_CSS = """
:root{--bg:#f7f8f9;--card:#ffffff;--border:#e8e8ea;--text:#282a30;
  --item:#16171a;--muted:#696e78;--accent:#5a66d1;--row:#eef0f2;--chartbd:#e8e8ea;--up:#d61e24;--down:#0b63f2}
body.dark{--bg:#0b0c0e;--card:#141518;--border:#26272b;--text:#e2e3e6;
  --item:#f7f8f8;--muted:#8a8f98;--accent:#9aa2f0;--row:#1f2023;--chartbd:#26272b;--up:#e75458;--down:#3e84f6}
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
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--muted)}
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
""" + _sl.LINK_CSS

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
        amt = h.get("export_value_musd")
        amt_s = f"${amt:,.1f}M" if amt is not None else "—"
        trs.append(f"<tr><td>{_html.escape(h['month'])}</td>"
                   f"<td>{amt_s}</td>"
                   f"<td>{c('export_yoy')}</td><td>{c('export_mom')}</td>"
                   f"<td>{c('export_yoy_3m')}</td>"
                   f"<td>{c('price_yoy')}</td></tr>")
    # 두 문법이 한 표에 섞이므로 **없는 칸은 '—'** 로 둔다(빈칸은 0 으로
    # 읽힌다, #43·#181). 어느 열이 어느 판에서 오는지는 카드가 말한다.
    return ('<table class="kr-htbl"><tr><th>월</th><th>수출액</th>'
            '<th>수출액 YoY</th><th>MoM</th>'
            '<th>3M 수출액 YoY</th><th>단가 YoY</th></tr>'
            + "".join(trs) + "</table>")


def _card_html(r: dict, hist: list[dict], media_prefix: str,
               code_by_name: dict | None = None) -> str:
    raw_code = r.get("stock_code") or ""
    # 합성키(`nm:회사명`)는 **코드가 아니다** — 그대로 찍으면 화면이 없는
    # 종목코드를 있다고 말한다(#34·#43). 6자리 숫자일 때만 코드로 인정한다.
    code6 = raw_code if re.fullmatch(r"\d{6}", raw_code) else ""
    code = _html.escape(code6)
    raw_name = r.get("stock_name") or code6 or ""
    # 카드 제목 = 종목분석 화면 딥링크(사용자 2026-09-22). 금액판 행은 코드가
    # 합성키라 여기 오는 `code6` 가 빈 문자열인데, 그 경우 이름→코드는 이미
    # 시세 칩이 쓰는 리졸버가 푼다 — 호출부가 페이지당 한 번 풀어 넘긴다.
    name = _sl.linked_name(raw_name, _sl.lookup_href(
        code6, raw_name, query=(code_by_name or {}).get(raw_name, "")))
    mo = _html.escape(r.get("month") or "")
    summary = [f'<div class="kr-hd">'
               f'<span class="kr-item">{name}</span>'
               f'<span class="kr-code">{code}</span>'
               f'<span class="kr-mo">📅 {mo}</span></div>']
    amt = r.get("export_value_musd")
    if amt is not None:
        # 원천이 적은 단위(USD M$)를 그대로 쓴다 — 환산하지 않는다(#165).
        summary.append('<div class="kr-metric"><span class="kr-mlabel">'
                       f'💵 수출액</span><span class="kr-mval">${amt:,.1f}M'
                       '</span></div>')
    summary.append(_metric("💰 수출액 YoY", r.get("export_yoy")))
    summary.append(_metric("📈 MoM", r.get("export_mom")))
    summary.append(_metric("📊 3M 수출액", r.get("export_yoy_3m")))
    summary.append(_metric("🏷️ 단가 YoY", r.get("price_yoy")))
    if r.get("item"):
        summary.append(
            f'<div class="kr-lead">📦 {_html.escape(r["item"])}</div>')
    coin = []
    _cb = r.get("corr_basis")
    _sfx = _metrics.basis_suffix(_cb)
    if r.get("corr") is not None:
        _lb = "상관" if _cb == "미표기" else "동시상관"
        coin.append(f"{_lb} {r['corr']:.2f}")
    if r.get("dir_hit") is not None:
        coin.append(f"방향 일치율 {r['dir_hit']:.0f}%")
    if coin and _sfx:
        coin.append(_sfx)
    if coin:
        summary.append(f'<div class="kr-lead">🔗 {" · ".join(coin)}</div>')
    lead = []
    if r.get("lead_corr") is not None:
        lead.append(f"선행상관 {r['lead_corr']:.2f}")
    if r.get("lead_dir_hit") is not None:
        # ⚠️ '방향 일치율' 로만 적으면 위 동시 지표와 **같은 이름**이 된다
        # (#34 같은 주제어면 라벨에 기준을 박을 것).
        lead.append(f"선행 방향 일치율 {r['lead_dir_hit']:.0f}%")
    if lead:
        summary.append(f'<div class="kr-lead">🔮 {" · ".join(lead)}</div>')
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
                "없습니다.</div>"
                + asof_footer(0, "종목", None,
                              max_ingest_iso(conn, "kr_stock_exports"))
                + "</div></body></html>")
    # 이름→코드는 **페이지당 한 번**(#113) — 카드마다 부르면 캐시 파일을
    # 행 수만큼 다시 읽는다. 코드가 이미 있는 행은 리졸버가 필요 없다.
    _code_by_name = _sl.kr_codes(
        r.get("stock_name") or "" for r in rows
        if not re.fullmatch(r"\d{6}", r.get("stock_code") or ""))
    cards = [_card_html(r, history(conn, r["stock_code"]), media_url_prefix,
                        _code_by_name)
             for r in rows]
    return (_HEAD + "<div class='wrap'>"
            f"{back_nav_html()}"
            "<h1>🏢 한국 수출 데이터(종목별)</h1>"
            "<div class='sub'>Badonions 한국 수출 캡션을 <b>종목별</b>로 정리한 "
            "별도 페이지 · 새 월이 오면 카드가 자동 교체되고 과거 월은 "
            "히스토리 표에 남습니다</div>"
            "<div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), "종목",
                          max((r.get("month") or "") for r in rows) or None,
                          max_ingest_iso(conn, "kr_stock_exports"))
            + "</div></body></html>")


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    conn = open_kr_stock_db(db_path)
    try:
        html = render_html(conn, media_url_prefix=media_url_prefix)
    finally:
        conn.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
