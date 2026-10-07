"""나쁜양파 **회사별 금액판** 공용 엔진 — 나라·방향(수출/수입)은 `Flow` 가 갖는다.

쓰는 곳: 한국 수출(krs, 파서만) · 한국 수입(kri) · 미국 수출(uss, 2026-10-08).
모듈 이름의 `kr_` 는 첫 소비자였던 한국의 흔적이다 — `cn_stock_flow` 가 대만
(tws)까지 받는 것과 같다. ⚠️ 그래서 **엔진 안에 나라 리터럴을 두지 않는다**:
화면 문구는 `Flow.country`·`Flow.unit` 에서, 카드 딥링크의 신원 확인 수단은
`Flow.link` 에서 온다(#330 — 엔진의 나라 리터럴이 어댑터 한 줄 추가를 막았다).

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

from trade import stock_link as _sl
from trade.archive_template import (asof_footer, back_nav_html,
                                    card_html, max_ingest_iso)

# 파서 스키마 버전. 올리면 저장된 옛 행의 파생 필드를 upsert 가 다시 채운다
# (파서를 고쳐도 이미 구운 값이 안 바뀌는 함정 차단, 실수 #18·#21b).
PARSE_VER = 1


# `Flow.link` 이 받는 값 — 카드 제목 딥링크의 신원 확인 수단(아래 Flow 주석).
LINK_RULES = frozenset({"kr_name", "hashtag"})


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
    # 화면이 행을 부르는 이름 — "회사"(한국 수입) | "종목"(미국 수출). 부제의
    # 'N별' 과 꼬리말 'N개 …' 가 같은 낱말을 쓴다(제목과 부제가 갈리지 않게).
    unit: str
    # 카드 제목 딥링크의 **신원 확인 수단**(#399·#400). 나라마다 다르다:
    #   "kr_name" — KRX 상장 목록이 회사명을 6자리로 푸는 것만(`_sl.kr_codes`)
    #   "hashtag" — 캡션이 스스로 밝힌 해시태그 심볼(`#P`). 영문 심볼만 질의가
    #               된다(`stock_link.lookup_query` 규칙 2 — 미국 상장 심볼).
    # ⚠️ 기본값 없음 — 미국 회사명을 KRX 목록에 대조하면 동명 상장사가 남의
    # 회사 화면을 연다(#34 한 규칙이 두 시장을 대표하면 한쪽은 거짓말).
    link: str
    sibling: str = ""       # 형제 페이지 파일명. 없으면 "" (억지로 걸면 404)
    sibling_label: str = ""

    def __post_init__(self):
        # 오타는 조용히 평문 카드가 된다 — 받는 값만 받는다(#24 오타 = 조용한 눈멂).
        if self.link not in LINK_RULES:
            raise ValueError(f"Flow.link 은 {sorted(LINK_RULES)} 중 하나: {self.link!r}")


def marker_re(marker: str, country: str) -> re.Pattern:
    """`8월 수출 한국` — 품목판과 **같은 어순**이다(#83 이 적은 그 함정의 반대편).

    낱말 사이만 공백을 허용하고 개행(`\\s`)은 쓰지 않는다 — 캡션 어딘가에
    우연히 흩어진 세 낱말이 통과하면 그게 곧 남의 메시지 오저장이다."""
    return re.compile(r"\d+[^\S\n]*월[^\S\n]*" + re.escape(marker) +
                      r"[^\S\n]*" + re.escape(country))


# ▶️ 줄 = "회사 — 품목". 대시가 없으면 **품목판**(회사가 아님)이라 받지 않는다 —
# 품목을 회사 이름 칸에 넣으면 화면이 스스로 거짓말한다(#34·#77). 그 나라·방향의
# 품목판 소스가 없으면(오늘 한국 수출입·미국 수출) 그런 캡션은 `--show-irrelevant`
# 에 남아 다음 라운드가 잰다(#332).
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


# 해시태그 심볼 — 미국판은 캡션 끝에 그 회사의 상장 심볼을 `#P` 로 단다(2026-10-08
# 사용자 캡처: Everpure, Inc. → `#P`). 같은 채널의 품목판도 `관련기업: #APTV …` 처럼
# 심볼을 해시태그로 적는다(us_imports·mx_exports 파서가 이미 읽는다).
# ⚠️ 앞이 **줄 시작이나 공백**일 때만 — 링크 URL 의 조각(`…/map#P`)을 심볼로
# 읽으면 남의 회사 화면이 열린다. 뒤도 **공백이나 줄 끝**이어야 한다 — 글자에 붙은
# 태그(`#AAPL#MSFT` · `#P,` · 긴 토큰)를 잘라 읽지 않는다(실측 모양은 `#P` 한 줄).
_RE_HASHTAG = re.compile(r"(?<!\S)#([A-Za-z][A-Za-z0-9.\-]{0,9})(?!\S)")
# 출처 판정(독립 리뷰): 해시태그가 **그 회사의 심볼**이라는 근거는 캡션의 모양
# 뿐이다. 실측 모양(`#P` 가 홀로 한 줄)에서 벗어난 셋은 받지 않는다 —
# (1) 라벨 줄(`관련기업: #NTAP …`) 의 태그는 **다른 회사들**의 목록일 수 있다:
#     그 줄의 태그 앞은 공백·태그뿐이어야 한다(`_tag_head_end`).
# (2) 이름 태그(`#Pure Storage` · `#AIR LIQUIDE`)는 심볼이 아니다: 태그 바로 뒤에
#     공백 + 라틴 글자가 이어지면 이름의 첫 낱말로 본다.
# (3) 받은 태그가 있는 줄에 **다른 라틴 태그**가 하나라도 있으면(`#P, #PSTG` ·
#     `#NTAP, #DELL, #HPE` · `#AAPL#MSFT #NVDA`) 그 줄은 목록이다 — 고르지 않는다.
#     (1)·(2)·붙은 태그를 **후보에서 빼기만** 하면 목록이 '하나 남은 태그' 로
#     줄어 엉뚱한 심볼이 된다(반영분 독립 리뷰가 실측: Everpure 카드가 HPE 로).
#     거르는 것은 후보이고, 경쟁자는 거르지 않는다.
_RE_NAME_TAIL = re.compile(r"[^\S\n]+[A-Za-z]")
# 경쟁자 판정용 — 글자에 붙거나 구두점이 붙은 태그(`#P,` · `#AAPL#…`)까지 본다.
# URL 조각(`…/map#P` · `?c=1#QQ`)은 앞 글자로 거른다.
_RE_TAGLIKE = re.compile(r"(?<![\w/?=&%#])#([A-Za-z][A-Za-z0-9.\-]*)")
_RE_TOKEN = re.compile(r"\S+")


def _tag_head_end(line: str) -> int:
    """줄 머리의 '공백·`#…` 토큰' 구간이 끝나는 위치(첫 비-태그 토큰의 시작,
    없으면 줄 끝). 이 앞의 태그만 라벨 줄이 아니다.

    ⚠️ 한 번만 훑는다. 첫 판은 정규식(`(공백* #비공백+)* 공백*`)으로 재서
    `#a#a#a… foo` 줄에서 **지수 역추적**이었고(셀프리뷰 실측: `#a`×14 0.002초 →
    ×22 0.43초, 두 개마다 ~4배, #71), 그다음 판은 태그마다 앞부분을 다시 갈라
    태그 수 × 줄 길이였다(반영분 리뷰 실측: 태그 16,000개 11.9초). 여기선 줄당
    한 번이다."""
    for t in _RE_TOKEN.finditer(line):
        if not t.group(0).startswith("#"):
            return t.start()
    return len(line)


def _sym(raw: str) -> str:
    """태그 글자 → 심볼. 문장 끝 마침표·대시는 떼고(`#P.` → P) 가운데 점은
    둔다(`#BRK.B`)."""
    return raw.upper().rstrip(".-")


def hashtag_symbol(seg: str) -> str | None:
    """구간 안의 해시태그 심볼 — **정확히 한 종류**일 때만 돌려준다.

    둘 이상이면 어느 것이 ▶️ 회사의 심볼인지 캡션이 말하지 않으므로 고르지 않는다
    (#165 — 재지 않은 귀속을 단정하지 않는다. 틀린 링크보다 평문이 낫다, #144).
    라벨 줄의 태그와 이름 태그는 후보가 아니고, 후보가 있는 줄에 다른 라틴 태그가
    있으면 그 줄은 목록이라 None 이다(위 (1)~(3)).
    ⚠️ **못 보는 축**(#274): 홀로 선 테마 태그(`#AI` 한 줄)는 심볼과 모양이 같아
    가를 수 없다 — 그때 심볼이 하나뿐이면 테마가 링크가 된다(오늘 실측 캡션엔
    없다). 심볼 태그와 같이 오면 둘 이상이라 평문이 된다."""
    syms: set[str] = set()
    for line in (seg or "").splitlines():
        head = _tag_head_end(line)
        acc: set[str] = set()
        for m in _RE_HASHTAG.finditer(line):
            if m.start() >= head:
                continue                    # (1) 라벨 줄의 태그
            if _RE_NAME_TAIL.match(line, m.end()):
                continue                    # (2) 이름 태그
            acc.add(_sym(m.group(1)))
        if not acc:
            continue
        if {_sym(t) for t in _RE_TAGLIKE.findall(line)} - acc:
            return None                     # (3) 같은 줄의 다른 태그 — 목록이다
        syms |= acc
    return syms.pop() if len(syms) == 1 else None


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
    """캡션 → {stock_name, item, months[], symbol} 또는 None.

    `symbol` 은 그 회사 구간의 해시태그 심볼(`hashtag_symbol`) — 없거나 모호하면
    None. 저장하지 않고 **렌더 때 원문에서 다시 읽는다**(`_card_html`) — 그래야
    규칙을 고쳐도 이미 받은 행이 그대로 따라온다(#270 렌더타임 파생 · #18)."""
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
    return {"stock_name": name, "item": item, "months": months,
            "symbol": hashtag_symbol(seg)}


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
# Render — 형제 한국 수출 페이지(kr_stock_exports)와 같은 카드 모양.
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
""" + _sl.LINK_CSS

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


def card_href(r: dict, flow: Flow, code_by_name: dict | None = None) -> str:
    """카드 제목 딥링크(`../lookup/<질의>`) — 만들 수 없으면 ""(평문).

    신원 확인 수단은 `flow.link` 가 고른다(나라마다 다르다, #400):
    - "kr_name": 회사명만 있고 코드가 없으므로 KRX 상장 목록이 푼 코드로 건다
      — 호출부가 페이지당 한 번 풀어 넘긴다(#113·#150).
    - "hashtag": 그 행을 만든 **원문**에서 그 회사 구간의 해시태그 심볼을 다시
      읽는다(렌더타임 파생, #270). 원문의 첫 회사가 이 행의 회사가 아니면 그
      심볼은 남의 것이므로 쓰지 않는다. 이름은 형제 보드(cn_stock_flow)처럼 같이
      넘긴다 — NOAH 영문 별칭표가 푸는 이름만 질의가 된다(`lookup_query` 규칙 4)."""
    raw_name = r.get("company") or ""
    if flow.link == "kr_name":
        return _sl.lookup_href(query=(code_by_name or {}).get(raw_name, ""))
    parsed = parse(r.get("raw_text") or "", flow) or {}
    sym = parsed.get("symbol") if parsed.get("stock_name") == raw_name else None
    return _sl.lookup_href(sym or "", raw_name)


def _card_html(r: dict, hist: list[dict], flow: Flow, media_prefix: str,
               code_by_name: dict | None = None) -> str:
    # 카드 제목 = 종목분석 화면 딥링크(사용자 2026-09-22) — 규칙은 `card_href`.
    raw_name = r.get("company") or ""
    name = _sl.linked_name(raw_name, card_href(r, flow, code_by_name))
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
    # 화면 문구의 나라·단위는 Flow 에서 — 엔진에 '한국' 을 박아 두면 다른 나라
    # 어댑터가 남의 나라 이름을 단 페이지가 된다(#330·#55).
    what = f"{_html.escape(flow.country)} {_html.escape(flow.marker)}"
    unit = _html.escape(flow.unit)
    if not rows:
        # 빈 상태에서도 페이지를 만들어 nav 404 를 막는다(기존 모듈 규약).
        return (head + "<div class='wrap'>" + back_nav_html() + title +
                f"<div class='empty'>아직 수집된 {what} "
                f"데이터(나쁜양파·{unit}별)가 없습니다.</div>"
                + asof_footer(0, flow.unit, None,
                              max_ingest_iso(conn, flow.table))
                + "</div></body></html>")
    # KRX 이름 대조는 그 수단을 쓰는 흐름에서만 — 미국 회사명을 KRX 목록에
    # 물으면 헛돌고(#61) 동명 상장사가 남의 화면을 연다(#34).
    _code_by_name = (_sl.kr_codes(r.get("company") or "" for r in rows)
                     if flow.link == "kr_name" else {})
    cards = [_card_html(r, history(conn, flow, r["company"]), flow,
                        media_url_prefix, _code_by_name) for r in rows]
    return (head + "<div class='wrap'>" + back_nav_html() + title +
            f"<div class='sub'>Badonions {what} 캡션을 "
            f"<b>{unit}별</b>로 정리한 페이지 · 새 월이 오면 카드가 자동 교체되고 "
            "과거 월은 히스토리 표에 남습니다" + _sibling_line(flow) + "</div>"
            "<div class='grid'>" + "".join(cards) + "</div>"
            + asof_footer(len(rows), flow.unit,
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
