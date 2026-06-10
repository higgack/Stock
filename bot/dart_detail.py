"""DART 구조화 요약 — 차트 공시 마커 hover 에 실제 숫자 한 줄 (KR, ₩0·LLM 0).

차트 공시 마커(KR)에 '증자금액·자기주식 취득금액·전환사채 발행총액·주당배당금'
같은 핵심 숫자를 DART **구조화 API**에서 뽑아 붙인다. 무료·LLM 없음. rcept_no 로
마커와 매칭. 응답 변형/필드 부재/키 없음 시 graceful({} → 제목+원문 fallback).

다루는 종류(주요사항보고서 주요정보, rcept_no 매칭):
  - 유상증자결정 piicDecsn / 자기주식취득결정 tsstkAqDecsn /
    전환사채발행결정 cvbdIsDecsn / 회사합병결정 cmpMgDecsn
  - 배당: 주요사항보고서에 없음(거래소 수시공시) → 정기보고서 배당정보 alotMatter
    의 최근 연간 주당배당금·시가배당률을 '배당' 제목 마커에 context 로 부착.

⚠️ DART 구조화 응답 필드명은 라이브 검증이 필요(샌드박스 키 부재). 후보 필드명을
여러 개 시도하고, 못 찾으면 그 항목만 조용히 생략한다(전체 graceful).
"""
from __future__ import annotations

from datetime import date, timedelta

_BASE = "https://opendart.fss.or.kr/api"
_TIMEOUT = 10


def _won(v) -> str | None:
    """원 금액 → 조/억/만 약식. 0/None/비수치 → None."""
    try:
        n = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    a = abs(n)
    if a >= 1e12:
        s = f"{n / 1e12:,.1f}".rstrip("0").rstrip(".") + "조"
    elif a >= 1e8:
        s = f"{n / 1e8:,.1f}".rstrip("0").rstrip(".") + "억"
    elif a >= 1e4:
        s = f"{n / 1e4:,.0f}만"
    else:
        s = f"{n:,.0f}"
    return s + "원"


def _pick(row: dict, keys: tuple[str, ...]):
    """row 에서 후보 키들 중 첫 유효값(없거나 '-' 제외)."""
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "-"):
            return v
    return None


# (엔드포인트, 라벨, [(항목라벨, 후보 키들, 종류 'won'|'num'|'text')])
_SPECS = [
    ("piicDecsn", "유상증자", [
        ("방식", ("ic_mthn",), "text"),
        ("신주(보통)", ("nstk_ostk_cnt",), "num"),
        ("시설자금", ("fdpp_fclt",), "won"),
        ("운영자금", ("fdpp_op",), "won"),
    ]),
    ("tsstkAqDecsn", "자기주식취득", [
        ("취득금액", ("aqpln_prc_ostk", "aqpln_prc"), "won"),
        ("취득수량(보통)", ("aqpln_stk_ostk", "aqpln_stk"), "num"),
        ("목적", ("aq_pp",), "text"),
    ]),
    ("cvbdIsDecsn", "전환사채", [
        ("발행총액", ("bd_fta", "bd_tota"), "won"),
        ("표면이자율", ("bd_intr_ex",), "text"),
        ("전환가", ("cv_prc",), "won"),
    ]),
    ("cmpMgDecsn", "합병", [
        ("합병비율", ("mg_rt",), "text"),
        ("상대회사", ("mgprt_cmpnm", "cmpcmpnm"), "text"),
    ]),
]


def _summary_for(row: dict, label: str, fields) -> str | None:
    parts = []
    for lbl, keys, kind in fields:
        v = _pick(row, keys)
        if v is None:
            continue
        if kind == "won":
            w = _won(v)
            if w:
                parts.append(f"{lbl} {w}")
        elif kind == "num":
            try:
                parts.append(f"{lbl} {int(str(v).replace(',', '')):,}주")
            except (TypeError, ValueError):
                parts.append(f"{lbl} {v}")
        else:
            parts.append(f"{lbl} {str(v)[:28]}")
    return (f"{label} · " + " · ".join(parts)) if parts else None


def get_disclosure_summaries(stock_code: str, days_back: int = 400) -> dict:
    """{rcept_no: '한 줄 요약'} + 배당 context('__DIVIDEND__' 키). graceful {}."""
    try:
        import requests
        from bot.dart_client import get_dart
    except Exception:
        return {}
    d = get_dart()
    if not getattr(d, "api_key", None):
        return {}
    corp = d.stock_code_to_corp_code(stock_code)
    if not corp:
        return {}
    end = date.today()
    bgn = end - timedelta(days=min(max(days_back, 90), 1200))
    out: dict[str, str] = {}

    # ── 주요사항보고서 주요정보 (rcept_no 매칭) ──
    for ep, label, fields in _SPECS:
        try:
            r = requests.get(
                f"{_BASE}/{ep}.json",
                params={"crtfc_key": d.api_key, "corp_code": corp,
                        "bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d")},
                timeout=_TIMEOUT)
            js = r.json()
        except Exception:
            continue
        if not isinstance(js, dict) or js.get("status") != "000":
            continue
        for row in js.get("list") or []:
            rno = str(row.get("rcept_no") or "").strip()
            if not rno:
                continue
            s = _summary_for(row, label, fields)
            if s:
                out[rno] = s

    # ── 배당: 정기보고서 배당정보(alotMatter) — 최근 연간 주당배당금·시가배당률 ──
    try:
        import requests
        for yr in (end.year - 1, end.year):
            r = requests.get(
                f"{_BASE}/alotMatter.json",
                params={"crtfc_key": d.api_key, "corp_code": corp,
                        "bsns_year": str(yr), "reprt_code": "11011"},
                timeout=_TIMEOUT)
            js = r.json()
            if not isinstance(js, dict) or js.get("status") != "000":
                continue
            dps = yld = None
            for row in js.get("list") or []:
                se = str(row.get("se") or "")
                val = row.get("thstrm")
                if val in (None, "", "-"):
                    continue
                if "주당" in se and "현금배당" in se:
                    dps = val
                elif "현금배당수익률" in se:
                    yld = val
            bits = []
            if dps:
                bits.append(f"주당 {dps}원")
            if yld:
                bits.append(f"시가배당률 {yld}%")
            if bits:
                out["__DIVIDEND__"] = f"최근 결산 배당 · " + " · ".join(bits)
                break
    except Exception:
        pass
    return out
