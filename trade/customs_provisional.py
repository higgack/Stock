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
# Persistence — customs.db 내 작은 스냅샷 테이블(렌더가 4×/일 fetch와 분리)
# ---------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customs_provisional (
  kind TEXT PRIMARY KEY,         -- imp_item / exp_item / imp_cnty / exp_cnty
  payload TEXT NOT NULL,         -- latest_signal() JSON
  fetched_at TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def store_signal(conn: sqlite3.Connection, kind: str, signal: dict) -> None:
    import json as _json
    ensure_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO customs_provisional (kind, payload, fetched_at) "
        "VALUES (?, ?, ?)",
        (kind, _json.dumps(signal, ensure_ascii=False),
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


def _metric(label: str, item: Optional[dict], *, lead: bool = False) -> str:
    from trade.customs import fmt_usd
    if not item:
        return ""
    tag = " ⚡선행" if lead else ""
    return (
        "<div class='ind-prov-cell'>"
        f"<div class='ind-prov-k'>{label}{tag}</div>"
        f"<div class='ind-prov-v'>{fmt_usd(item.get('usd'))} "
        f"{_yoy_span(item.get('yoy'))}</div>"
        "</div>"
    )


def render_box(signals: dict[str, dict]) -> str:
    """4종 신호 → '🟢 잠정 속보' 박스. 데이터 없으면 '' (motie 배너가 폴백).

    헤드라인: 전체 수출/수입 잠정 YoY + ⚡반도체제조용장비 수입(capex 선행)
    + 반도체 수출. 산업 집계와 분리된 독립 박스.
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
            {"usd": exp_item.get("total_usd"), "yoy": exp_item.get("total_yoy")}))
    if imp_item:
        cells.append(_metric(
            "전체 수입",
            {"usd": imp_item.get("total_usd"), "yoy": imp_item.get("total_yoy")}))
    cells.append(_metric("반도체제조용장비 수입", capex, lead=True))
    cells.append(_metric("반도체 수출", semi_exp))
    body = "".join(c for c in cells if c)
    if not body:
        return ""

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
        "</div>"
    )
