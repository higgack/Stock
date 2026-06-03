"""관세청 10일 단위 잠정치 — '잠정 속보' 박스 (확정치 ~20일 선행).

우리 산업트렌드의 본체는 관세청 '확정'(Itemtrade/GW, 익월 ~15일) HSK-MTI
집계다. 이 모듈은 거기에 **섞지 않고**, 별도의 가벼운 '잠정 속보' 박스만
그린다(운영자 지시: "우리꺼에 굳이 집어넣지 않는 방법으로"). 잠정치는 매월
말일분이 익월 1일 공개되고, 그 달 안에도 1~10일/1~20일 누적이 먼저 나와
**확정 집계보다 한 달 가까이 앞선 선행 신호**를 준다. 특히 수입 품목 중
'반도체제조용장비'는 국내 반도체 capex의 직접 선행 지표다.

데이터 출처 — data.go.kr 1220000(관세청) 4종, XML, 인증키는 확정 API와
동일한 ``TRADE_DATA_GO_KR_KEY`` 하나로 커버(운영자가 4종 모두 활용신청·승인):
  - prlstMmUtPrviImpAcrs : 수입 주요품목별 10일 단위 잠정치
  - prlstMmUtPrviExpAcrs : 수출 주요품목별 10일 단위 잠정치
  - cntyMmUtPrviImpAcrs  : 수입 주요국가별 10일 단위 잠정치
  - cntyMmUtPrviExpAcrs  : 수출 주요국가별 10일 단위 잠정치

응답 모델(실제 응답 검증 완료, 2026-06 cntyMmUtPrviImpAcrs 미리보기):
  item.itemUsdAmt00 = 전체, itemUsdAmt01..10 = 주요 10개 품목/국가(아래 라벨),
  priodMon=YYYYMM, priodDt='01~10'/'01~20'/'01~말일'(그 달 내 **누적**),
  priodYear=YYYY. **금액 단위는 '천 달러'** → USD 환산 시 ×1000.

핵심 주의 2가지(실데이터에서 확인):
  1) 단위 천달러 → ×1000 안 하면 1000배 작게 나온다.
  2) priodDt는 10일 버킷이 아니라 '월초 누적'이다(01~10 → 01~20 → 01~말일).
     YoY는 반드시 **같은 순(decile)끼리** 비교(작년 5월 01~20 vs 올해 5월
     01~20). 부분 vs 풀월 비교는 금지(사과-오렌지).

안전: 어떤 예외·403·빈 응답이든 박스를 안 그릴 뿐(``None``/``''``) 렌더는
안 깨진다. LLM 0, 추가 비용 0(무료 OpenAPI). 산업 집계와 완전 분리.
"""

from __future__ import annotations

import os
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, Optional

BASE = "https://apis.data.go.kr/1220000"

# kind → (service path, operation). 운영자 활용신청·승인된 4종.
ENDPOINTS: dict[str, tuple[str, str]] = {
    "imp_item": ("prlstMmUtPrviImpAcrs", "getPrlstMmUtPrviImpAcrs"),
    "exp_item": ("prlstMmUtPrviExpAcrs", "getPrlstMmUtPrviExpAcrs"),
    "imp_cnty": ("cntyMmUtPrviImpAcrs", "getCntyMmUtPrviImpAcrs"),
    "exp_cnty": ("cntyMmUtPrviExpAcrs", "getCntyMmUtPrviExpAcrs"),
}

# itemUsdAmt01..10 라벨. imp_cnty는 미리보기 설명문에서 1:1 확인됨. 나머지
# 3종은 활용신청 상세기능 spec 순서(미리보기로 추가 검증 가능). 라벨이 틀려도
# 구조·단위·YoY는 안 깨지고 '이름'만 어긋난다(파싱은 인덱스 기반).
LABELS: dict[str, tuple[str, ...]] = {
    # 수입 품목 — 05번이 반도체제조용장비(capex 선행)
    "imp_item": ("반도체", "원유", "기계류", "가스", "반도체제조용장비",
                 "정밀기기", "석유제품", "무선통신기기", "승용차", "석탄"),
    # 수출 품목 — 01번이 반도체
    "exp_item": ("반도체", "철강제품", "승용차", "석유제품", "무선통신기기",
                 "선박", "자동차부품", "컴퓨터주변기기", "정밀기기", "가전제품"),
    # 수입 국가 — 미리보기 설명문에서 확인
    "imp_cnty": ("중국", "미국", "유럽연합", "일본", "베트남", "호주", "대만",
                 "사우디아라비아", "러시아연방", "말레이시아"),
    # 수출 국가 — spec 순서(미리보기 추가 검증 권장)
    "exp_cnty": ("중국", "미국", "유럽연합", "베트남", "일본", "홍콩", "대만",
                 "싱가포르", "인도", "멕시코"),
}

_DECILE_LABEL = {"D1": "1~10일", "D2": "1~20일", "FULL": "전월(1~말일)"}

_HTTP_TIMEOUT_S = 20
_DEFAULT_ROWS = 999


class ProvisionalAPIError(RuntimeError):
    """잠정 API가 resultCode≠00을 줄 때 — 빈(정상) 응답과 구분해 호출자가
    조용히 에러 페이지를 데이터로 캐싱하지 않게 한다."""


# ---------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------

def _num(text: Optional[str]) -> int:
    """' 20,130,610' → 20130610. 콤마·공백 제거, 비수치 → 0."""
    s = (text or "").strip().replace(",", "")
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def _decile(priod_dt: Optional[str]) -> str:
    """누적 창 → decile 키. '01~10'→D1, '01~20'→D2, 그 외(01~28/30/31)→FULL.
    YoY를 같은 순끼리만 매칭하기 위한 정규화(연·월 다른 말일도 FULL로 묶임)."""
    s = (priod_dt or "").strip()
    if s.endswith("~10"):
        return "D1"
    if s.endswith("~20"):
        return "D2"
    return "FULL"


def parse_response(xml_text: str) -> list[dict]:
    """잠정 XML → [{ym:'YYYY-MM', priod_dt, decile, amt:[11 USD ints]}].

    amt[0]=전체, amt[1..10]=주요 10개. **천달러→USD(×1000) 환산 포함**.
    header resultCode≠00이면 ProvisionalAPIError.
    """
    root = ET.fromstring(xml_text)
    code_el = root.find(".//header/resultCode")
    code = (code_el.text or "").strip() if code_el is not None else ""
    if code and code != "00":
        msg_el = root.find(".//header/resultMsg")
        msg = (msg_el.text or "").strip() if msg_el is not None else ""
        raise ProvisionalAPIError(f"resultCode={code} resultMsg={msg!r}")

    rows: list[dict] = []
    for item in root.findall(".//body/items/item"):
        mon = (item.findtext("priodMon") or "").strip()
        if len(mon) < 6:
            continue
        amt = [_num(item.findtext(f"itemUsdAmt{i:02d}")) * 1000 for i in range(11)]
        dt = (item.findtext("priodDt") or "").strip()
        rows.append({
            "ym": f"{mon[:4]}-{mon[4:6]}",
            "priod_dt": dt,
            "decile": _decile(dt),
            "amt": amt,
        })
    return rows


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "trade-bot/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


def fetch(
    kind: str,
    start_yymm: str,
    end_yymm: str,
    *,
    key: Optional[str] = None,
    fetcher: Callable[[str], str] = _http_get,
) -> list[dict]:
    """kind(ENDPOINTS) 응답 rows. 확정 API와 동일한 키/urlencode 방식(이미
    검증됨). YoY엔 작년 동월이 필요하니 호출자가 13개월+ 창을 준다."""
    if kind not in ENDPOINTS:
        raise ProvisionalAPIError(f"unknown kind {kind!r}")
    key = key or os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        raise ProvisionalAPIError("TRADE_DATA_GO_KR_KEY not set")
    svc, op = ENDPOINTS[kind]
    qs = urllib.parse.urlencode({
        "serviceKey": key,
        "strtYymm": start_yymm,
        "endYymm": end_yymm,
        "numOfRows": _DEFAULT_ROWS,
        "pageNo": 1,
    })
    return parse_response(fetcher(f"{BASE}/{svc}/{op}?{qs}"))


# ---------------------------------------------------------------------
# Signal — 최신 누적창 + 작년 동창 YoY
# ---------------------------------------------------------------------

_DECILE_ORDER = {"D1": 1, "D2": 2, "FULL": 3}


def latest_signal(rows: list[dict], labels: tuple[str, ...]) -> Optional[dict]:
    """최신월의 '가장 진행된 누적창'을 잡고, 작년 동월·동순 YoY를 붙인다.

    반환: {ym, decile, window, priod_dt, total_usd, total_yoy,
           items:[{name, usd, yoy}]} — 데이터 없으면 None.
    """
    if not rows:
        return None
    latest_ym = max(r["ym"] for r in rows)
    month_rows = [r for r in rows if r["ym"] == latest_ym]
    # 가장 진행된 창(FULL>D2>D1, 동률이면 전체액 큰 쪽=더 누적).
    cur = max(month_rows, key=lambda r: (_DECILE_ORDER.get(r["decile"], 0), r["amt"][0]))
    py_ym = f"{int(latest_ym[:4]) - 1}-{latest_ym[5:7]}"
    prev = next(
        (r for r in rows if r["ym"] == py_ym and r["decile"] == cur["decile"]),
        None,
    )

    def _yoy(idx: int) -> Optional[float]:
        if prev is None:
            return None
        p = prev["amt"][idx]
        c = cur["amt"][idx]
        if not p:
            return None
        return (c - p) / p * 100.0

    items = [
        {"name": name, "usd": cur["amt"][i], "yoy": _yoy(i)}
        for i, name in enumerate(labels, start=1)
    ]
    return {
        "ym": latest_ym,
        "decile": cur["decile"],
        "window": _DECILE_LABEL.get(cur["decile"], cur["priod_dt"]),
        "priod_dt": cur["priod_dt"],
        "total_usd": cur["amt"][0],
        "total_yoy": _yoy(0),
        "items": items,
    }


# ---------------------------------------------------------------------
# 10일 모멘텀 — 최신창 YoY vs 직전 풀월 YoY = 가속/둔화
# ---------------------------------------------------------------------

def momentum_rows(rows: list[dict], labels: tuple[str, ...]) -> Optional[dict]:
    """전체(0)+품목/국가(1..10) 각각에 대해 최신창 YoY와 '모멘텀'을 계산.

    모멘텀(idx) = 최신창 YoY − 직전 '풀월' YoY (퍼센트포인트).
      · 양수 = 추세 가속(▲), 음수 = 둔화(▼).
      · 최신창이 부분누적(01~10/01~20)이면 확정보다 ~20일 빠른 선행 가속신호,
        풀월이면 ΔYoY(전월 풀 대비 가속도).
    반환 items는 입력 순서 그대로(정렬은 렌더에서). 데이터 없으면 None.
    """
    if not rows:
        return None
    latest_ym = max(r["ym"] for r in rows)
    month_rows = [r for r in rows if r["ym"] == latest_ym]
    cur = max(month_rows, key=lambda r: (_DECILE_ORDER.get(r["decile"], 0), r["amt"][0]))

    def _find(ym: str, decile: str) -> Optional[dict]:
        return next((r for r in rows if r["ym"] == ym and r["decile"] == decile), None)

    def _py(ym: str) -> str:
        return f"{int(ym[:4]) - 1}-{ym[5:7]}"

    cur_prev = _find(_py(latest_ym), cur["decile"])
    # 최신월보다 앞선 가장 최근 '풀월'(직전 마감월)과 그 작년 동월 풀월.
    full_months = sorted({r["ym"] for r in rows
                          if r["decile"] == "FULL" and r["ym"] < latest_ym})
    pf_ym = full_months[-1] if full_months else None
    prev_full = _find(pf_ym, "FULL") if pf_ym else None
    prev_full_py = _find(_py(pf_ym), "FULL") if pf_ym else None

    def _yoy(base: Optional[dict], prev: Optional[dict], i: int) -> Optional[float]:
        if base is None or prev is None:
            return None
        p = prev["amt"][i]
        if not p:
            return None
        return (base["amt"][i] - p) / p * 100.0

    out = []
    for i, name in enumerate(["전체", *labels]):
        cy = _yoy(cur, cur_prev, i)
        pf = _yoy(prev_full, prev_full_py, i)
        mom = (cy - pf) if (cy is not None and pf is not None) else None
        out.append({"idx": i, "name": name, "usd": cur["amt"][i],
                    "yoy": cy, "momentum": mom})
    return {
        "ym": latest_ym,
        "decile": cur["decile"],
        "window": _DECILE_LABEL.get(cur["decile"], cur["priod_dt"]),
        "items": out,
    }


# ---------------------------------------------------------------------
# Persistence — customs.db 내 작은 스냅샷 테이블(렌더가 4×/일 fetch와 분리)
# ---------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customs_provisional (
  kind TEXT PRIMARY KEY,         -- imp_item / exp_item / imp_cnty / exp_cnty
  payload TEXT NOT NULL,         -- latest_signal() JSON (헤드라인 박스)
  series_json TEXT,              -- parse_response() 전체 rows JSON (10일 모멘텀)
  fetched_at TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    # series_json 컬럼이 생기기 전 만들어진 테이블 마이그레이션(호스트는 이미
    # 박스만 저장하는 버전으로 테이블을 만들었을 수 있음).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(customs_provisional)")]
    if "series_json" not in cols:
        conn.execute("ALTER TABLE customs_provisional ADD COLUMN series_json TEXT")


def store_signal(conn: sqlite3.Connection, kind: str, signal: dict,
                 rows: Optional[list] = None) -> None:
    """헤드라인 신호(payload) + 전체 시계열(series_json)을 함께 저장.
    rows를 주면 10일 모멘텀 뷰가 품목·국가별 월별·창별 추이를 그릴 수 있다."""
    import json as _json
    ensure_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO customs_provisional "
        "(kind, payload, series_json, fetched_at) VALUES (?, ?, ?, ?)",
        (kind, _json.dumps(signal, ensure_ascii=False),
         _json.dumps(rows, ensure_ascii=False) if rows is not None else None,
         datetime.now(timezone.utc).isoformat()),
    )


def load_signals(conn: sqlite3.Connection) -> dict[str, dict]:
    """{kind: signal} — 테이블 없거나 비면 {}."""
    import json as _json
    try:
        ensure_schema(conn)
        out: dict[str, dict] = {}
        for row in conn.execute("SELECT kind, payload FROM customs_provisional"):
            try:
                out[row[0]] = _json.loads(row[1])
            except Exception:
                continue
        return out
    except Exception:
        return {}


def load_rows(conn: sqlite3.Connection) -> dict[str, list]:
    """{kind: rows} — 저장된 전체 시계열. 없거나 비면 {} (구버전 행은 series_json
    NULL이라 다음 fetch 전까지는 빈 채로 → 모멘텀 뷰만 비고 박스는 정상)."""
    import json as _json
    try:
        ensure_schema(conn)
        out: dict[str, list] = {}
        for kind, payload in conn.execute(
                "SELECT kind, series_json FROM customs_provisional"):
            if not payload:
                continue
            try:
                out[kind] = _json.loads(payload)
            except Exception:
                continue
        return out
    except Exception:
        return {}


def is_stale(conn: sqlite3.Connection, max_age_h: float = 6.0) -> bool:
    """전체 시계열(series_json)이 비었거나(구버전·미수집) 가장 최근 수집이
    max_age_h보다 오래됐으면 True → fetch_provisional --if-stale가 한 번만
    긁어 self-heal. 4종 모두 series_json이 있고 최신 수집이 max_age_h 이내면
    False(=API 0콜 skip). 어떤 예외든 True(안전하게 갱신 쪽)."""
    try:
        ensure_schema(conn)
        recs = list(conn.execute(
            "SELECT series_json, fetched_at FROM customs_provisional"))
    except Exception:
        return True
    have = [r for r in recs if r[0]]
    if len(have) < len(ENDPOINTS):       # 4종 시계열이 다 안 차면 갱신 필요
        return True
    newest = None
    for _series, ts in have:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if newest is None or dt > newest:
            newest = dt
    if newest is None:
        return True
    age_h = (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0
    return age_h >= max_age_h


# ---------------------------------------------------------------------
# Render — 가벼운 '잠정 속보' 박스
# ---------------------------------------------------------------------

def _find(signal: Optional[dict], *keywords: str) -> Optional[dict]:
    """signal.items에서 이름에 keyword 포함된 첫 항목(없으면 None)."""
    if not signal:
        return None
    for it in signal.get("items", []):
        nm = it.get("name") or ""
        if any(k in nm for k in keywords):
            return it
    return None


def _yoy_span(yoy: Optional[float]) -> str:
    from trade.customs import fmt_pct
    if yoy is None:
        return "<span class='ind-prov-flat'>—</span>"
    cls = "pos" if yoy >= 0 else "neg"
    return f"<span class='ind-prov-{cls}'>{fmt_pct(yoy)}</span>"


def _metric(label: str, item: Optional[dict], *,
            lead: bool = False, warn: bool = False) -> str:
    from trade.customs import fmt_usd
    if not item:
        return ""
    tag = " ⚡선행" if lead else ""
    if warn:
        tag += " ⚠️"
    cls = "ind-prov-cell ind-prov-warn" if warn else "ind-prov-cell"
    return (
        f"<div class='{cls}'>"
        f"<div class='ind-prov-k'>{label}{tag}</div>"
        f"<div class='ind-prov-v'>{fmt_usd(item.get('usd'))} "
        f"{_yoy_span(item.get('yoy'))}</div>"
        "</div>"
    )


# 모멘텀 뷰 그룹: (제목, kind, 수출이라 ⚠️ 표식)
_MOM_GROUPS = (
    ("수출 품목", "exp_item", True),
    ("수입 품목", "imp_item", False),
    ("수출 국가", "exp_cnty", True),
    ("수입 국가", "imp_cnty", False),
)


def _mom_span(m: Optional[float]) -> str:
    if m is None:
        return "<span class='ind-prov-flat'>—</span>"
    if m >= 0:
        return f"<span class='ind-prov-pos'>▲ +{m:.0f}%p</span>"
    return f"<span class='ind-prov-neg'>▼ {m:.0f}%p</span>"


def render_momentum(rows_by_kind: dict[str, list]) -> str:
    """🔟 10일 모멘텀 속보 — 품목·국가 40개 시계열을 절대액 큰 순으로 펼치는 패널.

    각 그룹 테이블: 항목 | 최신창 절대액(수출 ⚠️) | 창 YoY | 모멘텀(▲가속/▼둔화).
    전체(합계)는 맨 위 고정, 나머지는 최신창 절대액 내림차순. 데이터 없으면
    '' (헤드라인 박스만 뜨고 펼치기는 사라짐). JS 없는 <details>라 자체완결.
    """
    if not rows_by_kind:
        return ""
    from html import escape as _esc
    from trade.customs import fmt_usd

    tables: list[str] = []
    win_label = ""
    for title, kind, warn in _MOM_GROUPS:
        rows = rows_by_kind.get(kind)
        if not rows:
            continue
        mv = momentum_rows(rows, LABELS[kind])
        if not mv:
            continue
        win_label = f"{mv['ym']} {mv['window']}"
        total = mv["items"][0]
        items = sorted(mv["items"][1:], key=lambda r: r["usd"] or 0, reverse=True)
        body_rows: list[str] = []
        for it in [total, *items]:
            is_capex = kind == "imp_item" and "반도체제조용장비" in it["name"]
            nm = _esc(it["name"]) + (" ⚡" if is_capex else "")
            warn_mark = " ⚠️" if warn else ""
            tr_cls = " class='ind-prov-trtot'" if it["idx"] == 0 else ""
            body_rows.append(
                f"<tr{tr_cls}><td>{nm}</td>"
                f"<td class='ind-prov-num'>{fmt_usd(it['usd'])}{warn_mark}</td>"
                f"<td class='ind-prov-num'>{_yoy_span(it['yoy'])}</td>"
                f"<td class='ind-prov-num'>{_mom_span(it['momentum'])}</td></tr>"
            )
        tables.append(
            "<table class='ind-prov-tbl'>"
            f"<caption>{_esc(title)} · {_esc(win_label)}</caption>"
            "<thead><tr><th>항목</th><th>절대액</th><th>YoY</th><th>모멘텀</th>"
            f"</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        )
    if not tables:
        return ""
    note = (
        "<div class='ind-prov-mom-note'>모멘텀 = 최신창 YoY − 직전 풀월 YoY "
        "(▲가속/▼둔화) · 절대액 큰 순 정렬 · 수출 절대액 ⚠️ 추세 참고</div>"
    )
    return (
        "<details class='ind-prov-more'>"
        "<summary>🔟 10일 모멘텀 속보 — 품목·국가 40개 시계열 (절대액순)</summary>"
        f"<div class='ind-prov-mom'>{note}{''.join(tables)}</div>"
        "</details>"
    )


def render_box(signals: dict[str, dict], *, momentum_html: str = "") -> str:
    """4종 신호 → '🟢 잠정 속보' 박스. 데이터 없으면 '' (motie 배너가 폴백).

    헤드라인: 전체 수출/수입 잠정 YoY + ⚡반도체제조용장비 수입(capex 선행)
    + 반도체 수출. 산업 집계와 분리된 독립 박스. momentum_html을 주면 박스
    안쪽에 🔟 10일 모멘텀 속보 펼치기(<details>)를 덧붙인다.

    수출 잠정 절대액(전체 수출·반도체 수출)은 과거 대비 이례적으로 높게
    나오는 경향이 있어 ⚠️ 표식 + 캡션을 단다(운영자 확인 B안): 확정 시
    조정 가능하므로 절대액보다 추세로 참고. 수입·capex는 실측 검증됨.
    """
    if not signals:
        return ""
    imp_item = signals.get("imp_item")
    exp_item = signals.get("exp_item")
    # 기준월·창은 가용한 신호 중 최신 하나로 표기(보통 4종 동일 사이클).
    ref = exp_item or imp_item or signals.get("exp_cnty") or signals.get("imp_cnty")
    if not ref:
        return ""

    capex = _find(imp_item, "반도체제조용장비", "제조용장비")
    semi_exp = _find(exp_item, "반도체")

    cells: list[str] = []
    if exp_item:
        cells.append(_metric(
            "전체 수출",
            {"usd": exp_item.get("total_usd"), "yoy": exp_item.get("total_yoy")},
            warn=True))
    if imp_item:
        cells.append(_metric(
            "전체 수입",
            {"usd": imp_item.get("total_usd"), "yoy": imp_item.get("total_yoy")}))
    cells.append(_metric("반도체제조용장비 수입", capex, lead=True))
    cells.append(_metric("반도체 수출", semi_exp, warn=True))
    body = "".join(c for c in cells if c)
    if not body:
        return ""

    # 수출 ⚠️ 캡션 — 수출 셀이 실제로 그려질 때만(데이터 있을 때) 노출.
    has_export = bool(exp_item) or bool(semi_exp)
    caveat = (
        "<div class='ind-prov-cav'>⚠️ 수출 잠정 절대액이 과거 대비 이례적으로 "
        "높게 나옴 — 확정 시 조정 가능, 절대액보다 추세로 참고</div>"
        if has_export else ""
    )

    from html import escape as _esc
    ym = _esc(ref.get("ym") or "")
    window = _esc(ref.get("window") or "")
    return (
        "<div class='ind-prov'>"
        "<h3>🟢 잠정 속보 <span class='ind-prov-tag'>관세청 10일 단위</span></h3>"
        f"<div class='ind-prov-sub'>{ym} · {window} 누적 기준 · 확정치보다 "
        "최대 ~한 달 선행 · 작년 동월·동순 YoY · 단위 억$ "
        "<b>(산업 집계와 분리·참고용)</b></div>"
        f"<div class='ind-prov-grid'>{body}</div>"
        f"{caveat}"
        f"{momentum_html}"
        "</div>"
    )
