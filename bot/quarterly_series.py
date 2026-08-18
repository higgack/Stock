"""분기 실적 시계열 — 시장 중립 어댑터 + 표기 헬퍼.

KR 은 DART(`bot.dart_quarterly.get_quarterly_series`)가 단일분기 값을 직접
제공하지만, 그 외 시장(US/JP/TW/CN_A/HK)엔 레포 안에 분기 손익 소스가
없다(EDINET/MOPS/AKShare 클라이언트는 전부 공시·밸류에이션 전용, EDGAR 는
연간 중심). 유일한 전 시장 공통 소스는 yfinance 분기 손익계산서이고,
이미 `stock_snapshot._collect_financials` 가 **시장 게이트 없이 8분기치**를
스냅샷에 담고 있다 → 추가 네트워크 호출 0 으로 붙는다.

이 모듈은 그 원자료를 `get_quarterly_series` 와 **같은 스키마**로 바꾸는
어댑터 + 통화 인지 금액 표기 헬퍼만 담는다(순수함수 — 네트워크·LLM 없음).
사용자 2026-08-16 '분기실적을 다른 나라도'.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 야후 라인아이템 → canonical 한글 키. 앞의 후보가 없으면 뒤를 쓴다.
# (금융·리츠는 'Total Revenue' 대신 'Operating Revenue' 로 오는 경우가 있고,
#  'Operating Income' 미제공 종목은 'EBIT' 이 실질 동의어다.)
_ITEM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "매출": ("Total Revenue", "Operating Revenue"),
    "영업이익": ("Operating Income", "EBIT"),
    "당기순이익": ("Net Income",),
}


def q_label_from_period(period: str) -> str:
    """'2026-06-30' → '26.2Q'. 파싱 실패 시 원문 앞 7자(날조 금지).

    ⚠️ **달력 분기**다. 회계연도를 인지하지 않으므로 3월 결산사(일본 다수)의
    FY 1Q(6/30 종료)는 여기서 '26.2Q' 로 나온다 — 회사 자체 표기와 어긋날 수
    있어, 12월 결산이 아닌 종목은 호출부가 `fiscal_note()` 로 그 사실을
    라벨에 명시한다(회사별 검증 없는 오프셋 보정은 더 틀리기 쉬워 안 한다).
    `dart_quarterly.quarter_label` 과 동일한 '26.2Q' 표기 규약을 공유한다.
    """
    p = str(period or "")
    try:
        y, m = int(p[:4]), int(p[5:7])
        if 1 <= m <= 12:
            return f"{y % 100:02d}.{(m - 1) // 3 + 1}Q"
    except (ValueError, IndexError):
        pass
    return p[:7]


def fiscal_note(fiscal_year_end: str | None) -> str:
    """12월 결산이 아니면 '달력분기 기준'임을 알리는 짧은 꼬리표."""
    fye = (fiscal_year_end or "").strip()
    if not fye or fye.startswith("12-"):
        return ""
    return f"분기 라벨은 달력 기준(회계연도 종료 {fye})"


def _pick(row: dict, names: tuple[str, ...]):
    for nm in names:
        v = row.get(nm)
        if v is not None:
            return v
    return None


def series_from_yfinance(snap: dict | None, n: int = 5) -> list[dict] | None:
    """스냅샷의 yfinance 분기 손익 → `get_quarterly_series` 동형 리스트.

    반환은 **오래된 → 최신** 순(렌더러가 `qs[-1]`=최신, `qs[-5]`=전년동기를
    전제한다). 데이터가 없으면 None.
    """
    fins = ((snap or {}).get("financials") or {}).get("income_statement") or {}
    rows = fins.get("quarterly") or []
    if not rows:
        return None
    # 야후는 종목마다 최신우선/과거우선을 섞어 준다 — 순서를 가정하지 않고
    # period 로 명시 정렬한 뒤 최근 n 개(정렬 전 슬라이스는 과거를 자른다).
    rows = sorted(rows, key=lambda r: str(r.get("period", "")))[-n:]
    out: list[dict] = []
    for r in rows:
        period = str(r.get("period", ""))
        fin = {k: _pick(r, names) for k, names in _ITEM_CANDIDATES.items()}
        rev = fin.get("매출")
        ratios: dict = {}
        # 매출이 0/음수면 비율은 의미가 없다 — 억지로 만들지 않는다.
        if rev and rev > 0:
            if fin.get("영업이익") is not None:
                ratios["영업이익률"] = fin["영업이익"] / rev * 100
            if fin.get("당기순이익") is not None:
                ratios["순이익률"] = fin["당기순이익"] / rev * 100
        out.append({
            "label": q_label_from_period(period),
            "period": period,
            "financials": fin,
            "ratios": ratios,
            # DART 전용 개념(연결/별도·보고서코드)은 채우지 않는다 —
            # 호출부가 None 을 보고 라벨을 분기한다.
            "fs_div": None,
            "reprt_code": None,
        })
    # 전부 빈 분기면 데이터가 없는 것과 같다.
    if not any(any(v is not None for v in q["financials"].values())
               for q in out):
        return None
    return out


def missing_quarters(qs: list | None) -> list[str]:
    """표에 **빠진 달력 분기** 라벨 — 원천에 없는 것이지 우리가 지운 게 아니다.

    ⚠️ 왜 필요한가(사용자 2026-08-18 LPK.DE): 표가 25.1Q·25.2Q·**25.4Q**·
    26.1Q·26.2Q 로 이어져 25.3Q 가 통째로 없는데 화면엔 아무 표시가 없었다 —
    원천 결측인지 우리가 흘린 건지 볼 방법이 없다. 채우지 않고 **말한다**.
    """
    idx: list[tuple[int, str]] = []
    for q in (qs or []):
        per = str(q.get("period") or "")
        if len(per) < 7 or per[4] != "-":
            return []                     # 형식을 모르면 판정하지 않는다
        try:
            y, m = int(per[:4]), int(per[5:7])
        except ValueError:
            return []
        idx.append((y * 4 + (m - 1) // 3, str(q.get("label") or "")))
    out: list[str] = []
    for (a, _la), (b, _lb) in zip(idx, idx[1:]):
        for k in range(a + 1, b):
            y, qn = divmod(k, 4)
            out.append(f"{y % 100:02d}.{qn + 1}Q")
    return out


def fmt_money(v, currency: str = "KRW") -> str:
    """통화 인지 금액 표기(조/억 · 兆/億 · T/B/M). None → '—'.

    단위 규약은 `dashboard._fmt_mcap` 을 **재사용**한다(중복 정의 금지).
    다만 그 함수는 시가총액용이라 0 이하를 '—' 로 뭉개는데, 손익계산서는
    영업손실·순손실이 정상적인 값이므로 부호를 살려서 표기한다.
    """
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    from bot.dashboard import _currency_sym, _fmt_mcap
    cur = (currency or "KRW").upper()
    if n == 0:
        return f"{_currency_sym(cur)}0"
    s = _fmt_mcap(abs(n), _currency_sym(cur), cur)
    if s == "—":
        return "—"
    return f"-{s}" if n < 0 else s


def chart_unit(currency: str, peak: float) -> tuple[float, str, int]:
    """차트 y축 스케일 → (나눗수, 단위라벨, 소수자릿수).

    억/조(KRW·JPY·CNY·TWD·HKD)와 B/M(USD 등)은 자릿수 체계가 달라 단일
    임계(옛 코드의 1e12)를 쓰면 달러 종목에서 무의미해진다. 지수 오프셋
    ('1e6')이 축 위에 그려져 제목을 가리는 것을 막는 게 이 함수의 목적.
    """
    cur = (currency or "KRW").upper()
    p = abs(peak or 0)
    if cur in ("KRW", "JPY", "CNY", "TWD", "HKD"):
        tril, bil = ("조", "억") if cur == "KRW" else ("兆", "億")
        if p >= 1e12:
            return 1e12, tril, 1
        return 1e8, bil, 0
    if p >= 1e9:
        return 1e9, "B", 1
    return 1e6, "M", 0
