"""trade/ 수출 스크리닝 분석 primitive — 「5월 수출데이터 스크리닝 보고서」차용
(사용자 2026-06-17, Tier 1+2).

전부 **순수함수**: 월별 금액 ``months {ym: $}`` (+ 중량 ``wgts {ym: kg}``)만 입력,
네트워크 0 · 단위테스트 가능. ``industry.py`` 의 품목(MTI) 카드가 호출한다.

primitive (보고서 시그니처):
- ``pq_decompose``  : 수출액 = 단가(P,$/kg) × 물량(Q,kg) 분해 → YoY/MoM 각각 P·Q
                      기여 + 동력 라벨 (보고서 '판가 인상/마진 개선' vs '단가 하락세')
- ``progress_rate`` : 분기 누적 진도율 = 당해 QTD / 전년 동분기 전체 × 100, pace =
                      경과월/3 × 100 (5월=66.7%) + QoQ(전분기 전체 대비)
- ``classify_group``: 4그룹 매트릭스 (단기성장 MoM × 진도율 초과) — 결정적 규칙
- ``long_uptrend``  : ★장기 우상향 (분기 추세 — 최신 YoY>0 + 진도율 우수)
- ``cap_yoy`` / ``YOY_CAP`` : 기저(base effect) 표시 상한 (보고서 '+999.9% (기저)')

ym = 'YYYY-MM' 또는 'YYYYMM' (내부 normalize). 데이터 부재·0분모는 graceful None.
⚠️ 보고서가 진도율/그룹 공식을 명시하지 않아, 여기 정의는 보고서 개념을 차용한
**봇 자체 결정적 규칙**이다(라벨로 명확화). 정의 변경 시 이 모듈만 고치면 된다.
"""
from __future__ import annotations

from typing import Optional

YOY_CAP = 999.9   # 기저(base effect) 표시 상한 — 보고서 '+999.9% (기저)'


def _norm(months: Optional[dict]) -> dict:
    """{ym: v} 키를 'YYYY-MM' 으로 정규화 + None→0. 'YYYYMM'/'YYYY-MM' 모두 수용."""
    out: dict = {}
    for ym, v in (months or {}).items():
        s = str(ym).replace("-", "")
        if len(s) >= 6 and s[:6].isdigit():
            out[f"{s[:4]}-{s[4:6]}"] = v or 0
    return out


def _yago(ym: str) -> str:
    s = ym.replace("-", "")
    return f"{int(s[:4]) - 1:04d}-{s[4:6]}"


def _pmonth(ym: str) -> str:
    s = ym.replace("-", "")
    y, m = int(s[:4]), int(s[4:6])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def _chg(now, base) -> Optional[float]:
    """(now-base)/base × 100. base 0/None 또는 now None 이면 None."""
    if now is None or not base:
        return None
    return (now - base) / base * 100.0


def cap_yoy(yoy: Optional[float]):
    """기저 캡 — (capped_value, is_base). yoy>YOY_CAP 이면 base effect 로 간주."""
    if yoy is None:
        return None, False
    if yoy > YOY_CAP:
        return YOY_CAP, True
    return yoy, False


def pq_decompose(months: Optional[dict], wgts: Optional[dict],
                 ym: Optional[str] = None) -> Optional[dict]:
    """수출액 = 단가(P,$/kg) × 물량(Q,kg) 분해. 최신월(ym 미지정 시 max) 기준 YoY·MoM
    각각의 가치/단가/물량 변화율 + 동력 라벨. wgts 부재·0 이면 단가 산출 불가 → None.
    {ym, val_yoy, price_yoy, vol_yoy, val_mom, price_mom, vol_mom, driver}."""
    m = _norm(months)
    w = _norm(wgts)
    if not m:
        return None
    ym = ym or max(m)

    def cell(k):
        v, kg = m.get(k), w.get(k)
        if v is None or not kg:
            return None
        return {"val": v, "wgt": kg, "unit": v / kg}

    cur = cell(ym)
    if not cur:
        return None
    out: dict = {"ym": ym}
    for tag, base_k in (("yoy", _yago(ym)), ("mom", _pmonth(ym))):
        b = cell(base_k)
        out[f"val_{tag}"] = _chg(cur["val"], b["val"]) if b else None
        out[f"price_{tag}"] = _chg(cur["unit"], b["unit"]) if b else None
        out[f"vol_{tag}"] = _chg(cur["wgt"], b["wgt"]) if b else None
    out["driver"] = _driver(out.get("price_yoy"), out.get("vol_yoy"))
    return out


def _driver(price_yoy: Optional[float], vol_yoy: Optional[float]) -> str:
    """P·Q 동력 라벨 — 수출 성장이 단가(마진)·물량(수요) 중 무엇 주도인지."""
    if price_yoy is None or vol_yoy is None:
        return ""
    if price_yoy > 0 and vol_yoy > 0:
        return "P·Q 동반↑(물량+판가)"
    if price_yoy > 0 >= vol_yoy:
        return "단가 주도(판가↑·물량↓)"
    if vol_yoy > 0 >= price_yoy:
        return "물량 주도(물량↑·판가↓)"
    return "P·Q 동반↓(물량·판가↓)"


def progress_rate(months: Optional[dict], ym: Optional[str] = None) -> Optional[dict]:
    """분기 누적 진도율 — 당해 분기 QTD / 전년 동분기 **전체** × 100. pace = (경과월/3)
    × 100 (5월=66.7%). rate>pace 면 '우수'(전년 페이스 조기 초과). QoQ(전분기 전체
    대비)도 함께. {ym, quarter, elapsed, pace, rate_yoy, rate_qoq, status_yoy,
    status_qoq, qtd, prior_full}. 데이터/분모 부재 시 해당 rate None(graceful)."""
    m = _norm(months)
    if not m:
        return None
    ym = ym or max(m)
    s = ym.replace("-", "")
    year, mon = int(s[:4]), int(s[4:6])
    q = (mon - 1) // 3                # 0..3 (0-indexed quarter)
    q_start = q * 3 + 1
    elapsed = mon - q_start + 1       # 1..3

    def qsum(yr: int, qi: int) -> Optional[float]:
        # 분기 '전체' 분모는 3개월 모두 있을 때만 유효 — 일부만 있으면 진도율이
        # 과대(분모 과소)되므로 None(진도율 생략). 확정 과거데이터는 보통 완비.
        vals = [m.get(f"{yr:04d}-{mm:02d}") for mm in range(qi * 3 + 1, qi * 3 + 4)]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))

    def qtd(yr: int, upto: int) -> Optional[float]:
        tot, have = 0, False
        for mm in range(q_start, upto + 1):
            v = m.get(f"{yr:04d}-{mm:02d}")
            if v is not None:
                tot += v
                have = True
        return tot if have else None

    cur_qtd = qtd(year, mon)
    prior_full = qsum(year - 1, q)                      # 전년 동분기 전체
    pq_idx, pq_year = (q - 1, year) if q > 0 else (3, year - 1)
    prev_q_full = qsum(pq_year, pq_idx)                 # 전분기 전체
    pace = elapsed / 3 * 100.0
    rate_yoy = (cur_qtd / prior_full * 100.0) if (cur_qtd is not None and prior_full) else None
    rate_qoq = (cur_qtd / prev_q_full * 100.0) if (cur_qtd is not None and prev_q_full) else None

    def st(r):
        return None if r is None else ("우수" if r >= pace else "미달")

    return {"ym": ym, "quarter": q + 1, "elapsed": elapsed, "pace": pace,
            "rate_yoy": rate_yoy, "rate_qoq": rate_qoq,
            "status_yoy": st(rate_yoy), "status_qoq": st(rate_qoq),
            "qtd": cur_qtd, "prior_full": prior_full}


def classify_group(months: Optional[dict], ym: Optional[str] = None):
    """4그룹 매트릭스 (단기성장 MoM × 진도율 초과) — 보고서 개념 차용 결정적 규칙.
    G1 서프라이즈 기대(둘 다)·G2 견조한 성장/회복(하나)·G3 단기 모멘텀 관찰(성장이나
    단기·페이스 약)·G4 단기 둔화·관찰(전년比 감소). 데이터 부재 → (0,'데이터부족').
    returns (group:int, label:str)."""
    m = _norm(months)
    if not m:
        return (0, "데이터부족")
    ym = ym or max(m)
    val = m.get(ym)
    mom = _chg(val, m.get(_pmonth(ym)))
    yoy = _chg(val, m.get(_yago(ym)))
    prog = progress_rate(m, ym)
    pr_ok = bool(prog and prog["status_yoy"] == "우수")
    sg = (mom is not None and mom >= 0)          # 단기성장(전월비 유지·증가)
    if sg and pr_ok:
        return (1, "서프라이즈 기대(단기성장+진도율 초과)")
    if sg or pr_ok:
        return (2, "견조한 성장/회복(단기성장 또는 진도율)")
    if yoy is not None and yoy < 0:
        return (4, "단기 둔화·관찰(전년比 감소)")
    return (3, "단기 모멘텀 관찰(성장하나 단기·페이스 약)")


def long_uptrend(months: Optional[dict], ym: Optional[str] = None) -> bool:
    """★장기 우상향 — 분기 실적 추세(최신 YoY>0 + 진도율 우수). 보고서 '분기 실적
    기준 장기 우상향' 의 보수적 근사(데이터 충분 시만 True)."""
    m = _norm(months)
    if not m:
        return False
    ym = ym or max(m)
    yoy = _chg(m.get(ym), m.get(_yago(ym)))
    prog = progress_rate(m, ym)
    return bool(yoy is not None and yoy > 0 and prog and prog["status_yoy"] == "우수")
