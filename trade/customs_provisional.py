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
    # 전월 동순(MoM) — 같은 누적창끼리 전월과 비교 (사용자 2026-06-12
    # 'YoY 옆에 MoM, 같은 기간 기준'). 예: 6월 1-10 vs 5월 1-10.
    _y, _m = int(latest_ym[:4]), int(latest_ym[5:7])
    pm_ym = f"{_y - 1}-12" if _m == 1 else f"{_y}-{_m - 1:02d}"
    prev_mo = next(
        (r for r in rows if r["ym"] == pm_ym and r["decile"] == cur["decile"]),
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

    def _mom(idx: int) -> Optional[float]:
        if prev_mo is None:
            return None
        p = prev_mo["amt"][idx]
        c = cur["amt"][idx]
        if not p:
            return None
        return (c - p) / p * 100.0

    items = [
        {"name": name, "usd": cur["amt"][i], "yoy": _yoy(i), "mom": _mom(i)}
        for i, name in enumerate(labels, start=1)
    ]
    return {
        "ym": latest_ym,
        "decile": cur["decile"],
        "window": _DECILE_LABEL.get(cur["decile"], cur["priod_dt"]),
        "priod_dt": cur["priod_dt"],
        "total_usd": cur["amt"][0],
        "total_yoy": _yoy(0),
        "total_mom": _mom(0),
        "items": items,
    }


# ---------------------------------------------------------------------
# 10일 모멘텀 — 최신창 YoY vs 직전월 '같은 창' YoY = 가속/둔화
# ---------------------------------------------------------------------

def momentum_rows(rows: list[dict], labels: tuple[str, ...]) -> Optional[dict]:
    """전체(0)+품목/국가(1..10) 각각에 대해 최신창 YoY와 '모멘텀'을 계산.

    모멘텀(idx) = 최신창 YoY − 직전월 **같은 창**(decile) YoY (퍼센트포인트).
      · 양수 = 추세 가속(▲), 음수 = 둔화(▼).
      · 창이 10일/20일/한달(FULL) 무엇이든 항상 그 창 기준으로 통일
        (사용자 2026-07-23 '20일 vs 20일로' — 예전엔 부분누적 창일 때
        비교대상이 '직전 풀월'이라 창 기준이 안 맞았음, ΔYoY·ΔMoM 모두
        수정. 대신 확정치 대비 안정성은 낮아짐(양쪽 다 부분누적끼리
        비교라 요일효과 등 노이즈 ↑ — 트레이드오프 사용자 확인).
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

    def _pm(ym: str) -> str:
        y, m = int(ym[:4]), int(ym[5:7])
        return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"

    cur_prev = _find(_py(latest_ym), cur["decile"])
    # 전월 동순 (MoM, 사용자 2026-06-12 '표에도') — 예: 6월 1-10 vs 5월 1-10.
    cur_prev_mo = _find(_pm(latest_ym), cur["decile"])
    # ΔYoY·ΔMoM 비교기준 = 직전월의 **같은 창**(cur_prev_mo) 자체의 YoY·MoM
    # — 그 창의 전년 동월(YoY용)과 그 창의 전월(MoM용), 전부 cur 와 동일한
    # decile 로 통일(2026-07-23 재설계 — 예전엔 여기가 '직전 풀월' 이었음).
    prev_mo_py = _find(_py(_pm(latest_ym)), cur["decile"]) if cur_prev_mo else None
    prev_mo_pm = _find(_pm(_pm(latest_ym)), cur["decile"]) if cur_prev_mo else None

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
        pf = _yoy(cur_prev_mo, prev_mo_py, i)
        mom = (cy - pf) if (cy is not None and pf is not None) else None
        # ΔMoM (사용자 2026-06-13 'MoM 도 모멘텀') = 최신창 MoM − 직전월
        # 같은 창의 MoM. ⚠️ MoM 은 계절효과 미보정 — 둘 다 같은 계절 경계를
        # 건너는 비교라 부분 상쇄되지만, 해석은 ΔYoY(계절 중립)를 우선.
        mc = _yoy(cur, cur_prev_mo, i)
        pf_mom = _yoy(cur_prev_mo, prev_mo_pm, i)
        out.append({"idx": i, "name": name, "usd": cur["amt"][i],
                    "yoy": cy, "momentum": mom,
                    "mom_chg": mc,
                    "momchg_delta": (mc - pf_mom)
                    if (mc is not None and pf_mom is not None) else None})
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


def _mom2_span(mom: Optional[float]) -> str:
    """MoM 보조 표기 (YoY 옆, 전월 동순 대비 — 사용자 2026-06-12)."""
    from trade.customs import fmt_pct
    if mom is None:
        return ""
    cls = "pos" if mom >= 0 else "neg"
    # MoM 을 YoY 와 동일 크기·불투명도로 (사용자 2026-06-14 'MoM 도 YoY 랑 같은 크기')
    return (f" <span class='ind-prov-{cls}' style='font-size:.82em;"
            f"opacity:.95'>MoM {fmt_pct(mom)}</span>")


def _yoy2_span(yoy: Optional[float]) -> str:
    """헤드라인용 YoY 라벨 명시 표기 (사용자 2026-06-13 'YoY 도 포함') —
    _mom2_span 과 대칭. 표의 YoY 컬럼은 헤더가 라벨이라 무라벨 유지."""
    from trade.customs import fmt_pct
    if yoy is None:
        return ""
    cls = "pos" if yoy >= 0 else "neg"
    return (f"<span class='ind-prov-{cls}' style='font-size:.82em;"
            f"opacity:.95'>YoY {fmt_pct(yoy)}</span>")


def _metric(label: str, item: Optional[dict], *,
            lead: bool = False) -> str:
    """헤드라인 셀 — ⚠️ 상시 캐비엇은 제거(사용자 2026-06-12 '검증 완료,
    없애도 됨'). 값 = 절대액 + YoY(라벨 명시) + MoM(전월 동순)."""
    from trade.customs import fmt_usd
    if not item:
        return ""
    tag = " ⚡선행" if lead else ""
    return (
        "<div class='ind-prov-cell'>"
        f"<div class='ind-prov-k'>{label}{tag}</div>"
        f"<div class='ind-prov-v'>{fmt_usd(item.get('usd'))} "
        f"{_yoy2_span(item.get('yoy'))} {_mom2_span(item.get('mom'))}</div>"
        "</div>"
    )


def _balance_delta(exp_usd, exp_pct, imp_usd, imp_pct) -> Optional[float]:
    """전년(또는 전월) 대비 무역수지 증감(USD). YoY/MoM%로 기준시점 절대액을
    역산해 (수출−수입) 차이. 어느 한쪽 %가 없거나 역산 분모 0이면 None(생략).
    순수(테스트)."""
    if exp_pct is None or imp_pct is None or exp_usd is None or imp_usd is None:
        return None
    de, di = 1 + exp_pct / 100.0, 1 + imp_pct / 100.0
    if de == 0 or di == 0:
        return None
    return (exp_usd - imp_usd) - (exp_usd / de - imp_usd / di)


def _balance_delta_span(delta: Optional[float], label: str) -> str:
    """수지 증감 → 색 span(개선=녹 +흑자확대/적자축소, 악화=적 -). 라벨은 다른
    헤드라인 카드와 동일 YoY/MoM(사용자 2026-07-01 '똑같이'). 값은 %가 아닌 절대
    억$ — 수지에 %는 부호반전·0분모로 오해. white-space:nowrap 로 '억$' 단위가
    줄바꿈에 고아로 떨어지지 않게(줄맞춤 — 넘치면 YoY/MoM 사이에서만 개행)."""
    if delta is None:
        return ""
    from trade.customs import fmt_usd
    cls = "pos" if delta >= 0 else "neg"
    sign = "+" if delta >= 0 else "-"
    return (f"<span class='ind-prov-{cls}' style='font-size:.82em;opacity:.95;"
            f"white-space:nowrap'>{label} {sign}{fmt_usd(abs(delta))}</span>")


def _balance_cell(exp: Optional[dict], imp: Optional[dict]) -> str:
    """무역수지(전체 수출−전체 수입) 헤드라인 셀 — 절대 수지 + 전년·전월 대비
    증감(억$). 산업부 보도자료 헤드라인이지만 우리는 이미 가진 두 값의 뺄셈으로
    무료 확보(HS상세·유료 불요, 사용자 2026-07-01). 둘 다 있을 때만."""
    if not exp or not imp:
        return ""
    e, i = exp.get("total_usd"), imp.get("total_usd")
    if e is None or i is None:
        return ""
    from trade.customs import fmt_usd
    yoy = _balance_delta_span(
        _balance_delta(e, exp.get("total_yoy"), i, imp.get("total_yoy")), "YoY")
    mom = _balance_delta_span(
        _balance_delta(e, exp.get("total_mom"), i, imp.get("total_mom")), "MoM")
    return (
        "<div class='ind-prov-cell'>"
        "<div class='ind-prov-k'>무역수지</div>"
        f"<div class='ind-prov-v'>{fmt_usd(e - i)} {yoy} {mom}</div>"
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


def _mom_span_inline(m: Optional[float]) -> str:
    """모멘텀 셀(인라인 스타일판) — 아카이브 페이지처럼 dashboard CSS가 없는
    문맥에서도 색·표식이 그대로 보이게."""
    if m is None:
        return "<span style='color:#999'>—</span>"
    if m >= 0:
        return f"<span style='color:#34c759;font-weight:600'>▲ +{m:.0f}%p</span>"
    return f"<span style='color:#ff9500;font-weight:600'>▼ {m:.0f}%p</span>"


def _yoy_span_inline(yoy: Optional[float]) -> str:
    from trade.customs import fmt_pct
    if yoy is None:
        return "<span style='color:#999'>—</span>"
    cls = "#34c759" if yoy >= 0 else "#ff9500"
    return f"<span style='color:{cls};font-weight:600'>{fmt_pct(yoy)}</span>"


def _momentum_tables(rows_by_kind: dict[str, list], *, inline: bool = False
                     ) -> tuple[str, str]:
    """(win_label, tables_html). render_momentum / archive 공용 — inline=True면
    클래스 대신 인라인 스타일을 써서 dashboard CSS 없는 페이지에서도 정상."""
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
            # 데이터 속성 — 라이브 토글(절대액/모멘텀/|YoY|)에서 정렬 키로 사용.
            # 전체(합계)는 data-pin="1"로 항상 맨 위 고정. 누락값은 0/-inf 처리는
            # JS에서.
            usd_attr = str(it["usd"] or 0)
            # YoY는 부호 구분(signed) 정렬 — 성장 높은 순→하락 큰 순. None은
            # 빈 값(JS에서 -Infinity로 맨 아래). 모멘텀도 signed라 일관.
            yoy_attr = (str(it["yoy"]) if it["yoy"] is not None else "")
            mom_attr = (str(it["momentum"]) if it["momentum"] is not None else "")
            momchg = it.get("mom_chg")   # 옛 스냅샷엔 없음 → None graceful
            momchg_attr = (str(momchg) if momchg is not None else "")
            momchgd = it.get("momchg_delta")
            momchgd_attr = (str(momchgd) if momchgd is not None else "")
            pin_attr = ' data-pin="1"' if it["idx"] == 0 else ''
            data_attrs = (f' data-usd="{usd_attr}" data-mom="{mom_attr}" '
                          f'data-yoy="{yoy_attr}" data-momchg="{momchg_attr}" '
                          f'data-momchgd="{momchgd_attr}"'
                          f'{pin_attr}')
            if inline:
                tot_style = (";font-weight:700;background:#f4f4f4"
                             if it["idx"] == 0 else "")
                # 항목명(1차 식별자): 진한 본문색·굵게.
                # 절대액: 본문색·tabular-nums(자릿수 정렬)·우측 정렬.
                lbl_td = ("padding:4px 8px;border-bottom:1px solid #eee;"
                          "color:#1d1d1f;font-weight:600")
                num_td = ("padding:4px 8px;text-align:right;white-space:nowrap;"
                          "border-bottom:1px solid #eee;color:#1d1d1f;"
                          "font-variant-numeric:tabular-nums")
                yoy_html = _yoy_span_inline(it["yoy"])
                momchg_html = _yoy_span_inline(momchg)
                mom_html = _mom_span_inline(it["momentum"])
                momchgd_html = _mom_span_inline(momchgd)
                body_rows.append(
                    f"<tr style='{tot_style}'{data_attrs}>"
                    f"<td style='{lbl_td}'>{nm}</td>"
                    f"<td style='{num_td}'>{fmt_usd(it['usd'])}</td>"
                    f"<td style='{num_td}'>{yoy_html}</td>"
                    f"<td style='{num_td}'>{mom_html}</td>"
                    f"<td style='{num_td}'>{momchg_html}</td>"
                    f"<td style='{num_td}'>{momchgd_html}</td></tr>"
                )
            else:
                tr_cls = " class='ind-prov-trtot'" if it["idx"] == 0 else ""
                body_rows.append(
                    f"<tr{tr_cls}{data_attrs}><td>{nm}</td>"
                    f"<td class='ind-prov-num'>{fmt_usd(it['usd'])}</td>"
                    f"<td class='ind-prov-num'>{_yoy_span(it['yoy'])}</td>"
                    f"<td class='ind-prov-num'>{_mom_span(it['momentum'])}</td>"
                    f"<td class='ind-prov-num'>{_yoy_span(momchg)}</td>"
                    f"<td class='ind-prov-num'>{_mom_span(momchgd)}</td></tr>"
                )
        # ⚠️ 캡션 제거 (사용자 2026-06-12) — '잠정 속보' 라벨로 충분.
        cap_warn = ""
        if inline:
            tables.append(
                "<table style='width:100%;border-collapse:collapse;font-size:12px;"
                "background:#fff;border:1px solid #ddd;border-radius:8px;margin:6px 0'>"
                "<caption style='caption-side:top;text-align:left;font-weight:600;"
                "padding:6px 8px;color:#1d1d1f'>"
                f"{_esc(title)} · {_esc(win_label)}{cap_warn}</caption>"
                "<thead><tr>"
                "<th style='text-align:left;color:#666;padding:4px 8px;border-bottom:1px solid #ddd'>항목</th>"
                "<th style='text-align:right;color:#666;padding:4px 8px;border-bottom:1px solid #ddd'>절대액</th>"
                "<th style='text-align:right;color:#666;padding:4px 8px;border-bottom:1px solid #ddd'>YoY</th>"
                "<th style='text-align:right;color:#666;padding:4px 8px;border-bottom:1px solid #ddd'>ΔYoY</th>"
                "<th style='text-align:right;color:#666;padding:4px 8px;border-bottom:1px solid #ddd'>MoM</th>"
                "<th style='text-align:right;color:#666;padding:4px 8px;border-bottom:1px solid #ddd'>ΔMoM</th>"
                f"</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
            )
        else:
            tables.append(
                "<table class='ind-prov-tbl'>"
                f"<caption>{_esc(title)} · {_esc(win_label)}{cap_warn}</caption>"
                "<thead><tr><th>항목</th><th>절대액</th><th>YoY</th><th>ΔYoY</th><th>MoM</th><th>ΔMoM</th>"
                f"</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
            )
    return win_label, "".join(tables)


def momentum_archive_html(rows_by_kind: dict[str, list]) -> str:
    """아카이브 카드 본문에 넣을 모멘텀 4그룹 테이블(인라인 스타일).
    dashboard CSS 없이도 색·표식이 그대로 보임. 데이터 없으면 ''."""
    if not rows_by_kind:
        return ""
    _, tables = _momentum_tables(rows_by_kind, inline=True)
    if not tables:
        return ""
    note = ("<div style='font-size:11.5px;color:#666;line-height:1.4;margin-top:6px'>"
            "ΔYoY = 최신창 YoY − 직전월 같은 창 YoY · ΔMoM = 최신창 MoM − 직전월 같은 창 "
            "MoM (▲가속/▼둔화, 10일/20일/한달 항상 같은 창끼리) · MoM = 전월 동순"
            "(계절효과 미보정) · 절대액 큰 순</div>")
    return f"<div style='margin-top:8px'>{note}{tables}</div>"


def render_momentum(rows_by_kind: dict[str, list]) -> str:
    """🔟 10일 모멘텀 속보 — 품목·국가 전 시계열(개수 = LABELS 파생)을 절대액 큰
    순으로 펼치는 패널.

    각 그룹 테이블: 항목 | 최신창 절대액(수출 ⚠️) | 창 YoY | 모멘텀(▲가속/▼둔화).
    전체(합계)는 맨 위 고정, 나머지는 최신창 절대액 내림차순. 데이터 없으면
    '' (헤드라인 박스만 뜨고 펼치기는 사라짐). JS 없는 <details>라 자체완결.
    """
    if not rows_by_kind:
        return ""
    _, tables = _momentum_tables(rows_by_kind, inline=False)
    if not tables:
        return ""
    # 라이브 토글 — 4 테이블 동시 재정렬(JS는 dashboard._JS에). 기본=절대액.
    # 아카이브(inline=True)는 동결 기록이라 토글 없음 — 시점 기준 절대액 고정.
    sort_ui = (
        "<div class='ind-prov-sort'>정렬:"
        "<button type='button' class='ind-prov-sort-btn is-active' "
        "data-sort='usd'>절대액</button>"
        "<button type='button' class='ind-prov-sort-btn' "
        "data-sort='yoy'>YoY</button>"
        "<button type='button' class='ind-prov-sort-btn' "
        "data-sort='mom'>ΔYoY</button>"
        "<button type='button' class='ind-prov-sort-btn' "
        "data-sort='momchg'>MoM</button>"
        "<button type='button' class='ind-prov-sort-btn' "
        "data-sort='momchgd'>ΔMoM</button>"
        "</div>"
    )
    note = (
        "<div class='ind-prov-mom-note'>ΔYoY = 최신창 YoY − 직전월 같은 창 YoY · "
        "ΔMoM = 최신창 MoM − 직전월 같은 창 MoM (▲가속/▼둔화, 10일/20일/한달 "
        "항상 같은 창끼리) · MoM = 전월 동순 "
        "— MoM·ΔMoM 은 계절효과 미보정이라 추세 판단은 ΔYoY 우선 · "
        "전체 행 고정</div>"
    )
    return (
        "<details class='ind-prov-more'>"
        # 시계열 수는 LABELS 에서 파생 — OpenAPI 계약(4종 × 주요 10)이라 지금은
        # 40이지만, 하드코딩하면 라벨 추가 시 조용히 낡는다(#24).
        f"<summary>🔟 10일 모멘텀 속보 — 품목·국가 "
        f"{sum(len(v) for v in LABELS.values())}개 시계열</summary>"
        f"<div class='ind-prov-mom'>{sort_ui}{note}{tables}</div>"
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
            {"usd": exp_item.get("total_usd"), "yoy": exp_item.get("total_yoy"),
             "mom": exp_item.get("total_mom")}))
    if imp_item:
        cells.append(_metric(
            "전체 수입",
            {"usd": imp_item.get("total_usd"), "yoy": imp_item.get("total_yoy"),
             "mom": imp_item.get("total_mom")}))
    # 무역수지(수출−수입) — 산업부 보도자료 헤드라인, 가진 값 뺄셈으로 무료 확보.
    cells.append(_balance_cell(exp_item, imp_item))
    cells.append(_metric("반도체제조용장비 수입", capex, lead=True))
    cells.append(_metric("반도체 수출", semi_exp))
    body = "".join(c for c in cells if c)
    if not body:
        return ""

    # ⚠️ 상시 캐비엇 제거 (사용자 2026-06-12) — 데이터 검증 완료, 잠정의
    # 성질('잠정 속보' 라벨 + 타임라인의 잠정↔확정 대조)로 충분.
    caveat = ""

    from html import escape as _esc
    ym = _esc(ref.get("ym") or "")
    window = _esc(ref.get("window") or "")
    # 최신 잠정 창 명시 badge (사용자 2026-06-15 '현재 말고 최신으로' — 확정
    # 라벨 '최신 2026-05 …' 과 표기 통일). decile→발표시점 힌트.
    _pub = {"D1": "11일 발표", "D2": "21일 발표",
            "FULL": "월초 발표"}.get(ref.get("decile") or "", "")
    cur_label = f"{ym} · {window}" + (f" · {_pub}" if _pub else "")
    # 🗄 잠정 타임라인 — 발표 창마다 적립된 과거 잠정 스냅샷(잠정↔확정 대조용).
    # 서빙 dir(index.html 옆)에 provisional_archive.html이 있으므로 상대 링크.
    timeline = (
        "<div class='ind-prov-arch'>"
        "<a href='provisional_archive.html'>🗄 잠정 타임라인 — 발표 창별 "
        "과거 잠정 스냅샷 누적(잠정↔확정 대조) →</a></div>"
    )
    return (
        "<div class='ind-prov'>"
        f"<h3>🟢 잠정 속보 <span class='ind-prov-cur'>최신 {cur_label}</span> "
        "<span class='ind-prov-tag'>관세청 10일 단위 · 11일·21일·월초(전월 풀월) 발표</span></h3>"
        f"<div class='ind-prov-sub'>{ym} · {window} 누적 기준 · 확정치보다 "
        "최대 ~한 달 선행 · YoY=작년 동월·동순 · MoM=전월 동순 · 단위 억$ "
        "<b>(산업 집계와 분리·참고용)</b></div>"
        # 출처 명시 (사용자 2026-06-21 '채널은 1-20일인데 카드는 1-10일') — 이 박스는
        # 관세청 공개 OpenAPI(data.go.kr 순별 잠정 4종) 기준. 텔레그램 수출입 속보
        # 채널은 더 빠른 관세청 경로(보도자료·무역통계)를 relay 해 같은 旬을 1~2일
        # 먼저 싣는다 → 상단 '현재 잠정'(채널)과 이 박스 旬이 잠시 다를 수 있음.
        "<div class='ind-prov-src'>📡 출처: 관세청 공개 OpenAPI(data.go.kr 순별 잠정) "
        "· 상단 속보(텔레그램 채널)보다 1~2일 늦게 공개될 수 있어 旬(1-10/1-20)이 "
        "일시적으로 다를 수 있음 — OpenAPI 갱신 시 자동 반영</div>"
        f"<div class='ind-prov-grid'>{body}</div>"
        f"{caveat}"
        f"{momentum_html}"
        f"{timeline}"
        "</div>"
    )
