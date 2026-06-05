"""차트 공시 이벤트 마커 — 종목 상세 차트에 공시 날짜를 작은 마커로 (2026-06-05).

전 시장(KR DART / US EDGAR 8-K / JP EDINET / TW MOPS / CN·HK AKShare)의 공시를
{time:'YYYY-MM-DD', title, type, color} 로 통일해 차트 payload 의 `events` 로 싣는다.
차트 JS 가 작은 사각 마커로 렌더 + hover 시 제목 표시. **호재/악재 '판단'은 안 함**
— 공시 '종류'만 색으로 구분(수주=초록·실적=파랑·자본=주황·기타=회색), 제목은 그대로.

비용 ₩0 (전부 무료 클라이언트 + 각 클라이언트의 디스크 캐시). 시장별 fetch 는
try/except graceful(키 부재·실패 시 빈 리스트). 분류기는 순수 함수(단위테스트).

커버리지: KR(DART)·US(EDGAR)는 1년 범위 가능, JP/TW/CN 은 클라이언트가 최근
구간(일 단위 조회 비용)만 반환 → 최근 공시 위주. 차트는 보이는 구간만 필터.
"""
from __future__ import annotations

# ── 공시 종류 분류 (다국어 키워드). 색은 사실 기준(호재/악재 판단 아님). ──
_TYPE_COLOR = {
    "order": "#26a69a",     # 수주·공급계약 (초록)
    "earnings": "#4c9aff",  # 실적·정기보고서 (파랑)
    "capital": "#f5a623",   # 유증·CB·분할 등 자본변동 (주황)
    "other": "#94a3b8",     # 기타 공시 (회색)
}
_TYPE_LABEL = {"order": "수주·계약", "earnings": "실적", "capital": "자본변동", "other": "공시"}

_ORDER_KW = ("수주", "공급계약", "단일판매", "공급ㆍ계약", "공급계약체결", "계약체결", "공급",
             "material definitive agreement", "contract", "award", "order",
             "受注", "契約", "訂單", "合約", "合同", "中標", "中标", "签订")
_EARN_KW = ("실적", "분기보고서", "반기보고서", "사업보고서", "잠정실적", "영업(잠정)", "결산",
            "results of operations", "earnings", "quarterly", "annual report", "financial",
            "決算", "業績", "四半期", "財報", "季報", "年報", "业绩", "季度报告", "年度报告")
_CAPITAL_KW = ("유상증자", "무상증자", "전환사채", "신주인수권", "감자", "주식분할", "주식병합",
               "교환사채", "증자", "배정", "자기주식", "offering", "convertible", "dividend",
               "stock split", "buyback", "repurchase", "warrant",
               "増資", "新株", "株式分割", "減資", "配当", "自己株式",
               "現金股利", "減資", "增發", "增发", "回購", "回购", "分紅", "分红", "可轉債", "可转债")


def classify(title: str) -> str:
    """공시 제목 → 종류('order'|'earnings'|'capital'|'other'). 다국어 키워드.

    자본변동(증자/CB)을 실적보다 먼저 검사(분기보고서에 '증자' 언급되는 케이스
    드물지만 자본 이벤트가 더 dominant). 수주 우선."""
    t = str(title or "")
    tl = t.lower()

    def _has(kws):
        return any((k in t) or (k.lower() in tl) for k in kws)
    if _has(_ORDER_KW):
        return "order"
    if _has(_CAPITAL_KW):
        return "capital"
    if _has(_EARN_KW):
        return "earnings"
    return "other"


def _norm(items: list[dict], date_key: str, title_key: str) -> list[dict]:
    """클라이언트별 dict 리스트 → 통일된 [{time:'YYYY-MM-DD', title, type, color}]."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for it in items or []:
        raw_d = str(it.get(date_key) or "").strip()
        # 'YYYYMMDD' → 'YYYY-MM-DD', 'YYYY-MM-DD...' → 앞 10자.
        if len(raw_d) == 8 and raw_d.isdigit():
            d = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:8]}"
        else:
            d = raw_d[:10]
        title = str(it.get(title_key) or "").strip()
        if not d or not title:
            continue
        key = (d, title)
        if key in seen:
            continue
        seen.add(key)
        typ = classify(title)
        out.append({"time": d, "title": title, "type": typ, "color": _TYPE_COLOR[typ]})
    out.sort(key=lambda e: e["time"])
    return out


def fetch_disclosure_events(ticker: str, limit: int = 60) -> list[dict]:
    """티커 → 공시 이벤트 마커 리스트. 시장별 무료 클라이언트로 라우팅, 실패 시 [].

    KR DART / US EDGAR 8-K / JP EDINET / TW MOPS / CN·HK AKShare. 각 try/except.
    호출부(차트 payload)가 보이는 날짜 구간으로 다시 필터한다."""
    try:
        from bot.market import detect_market
        market = detect_market(ticker)
    except Exception:
        market = "US"
    code = ticker.split(".")[0]
    events: list[dict] = []
    try:
        if market == "KR":
            from bot.dart_client import get_dart
            raw = get_dart().get_recent_disclosures(code, days_back=400, limit=limit)
            events = _norm(raw, "date", "title")
        elif market in ("CN_A", "HK"):
            from bot.akshare_client import get_akshare
            raw = get_akshare().get_recent_disclosures(ticker, days_back=120, limit=limit)
            events = _norm(raw, "date", "subject")
        elif market == "JP":
            from bot.edinet_client import get_edinet
            raw = get_edinet().get_recent_disclosures(ticker, days_back=120, limit=limit)
            events = _norm(raw, "date", "description")
        elif market == "TW":
            from bot.mops_client import get_mops
            raw = get_mops().get_recent_disclosures(ticker, days_back=120, limit=limit)
            events = _norm(raw, "date", "subject")
        else:  # US (default)
            from bot.edgar_client import get_recent_8k
            raw = get_recent_8k(ticker, days=400, top_n=limit)
            # EDGAR: items_labels(8-K 항목 설명) 를 제목으로, 없으면 '8-K 공시'.
            us = []
            for f in raw or []:
                labels = f.get("items_labels") or []
                us.append({"date": f.get("date") or "",
                           "title": "; ".join(labels) if labels else "8-K 공시"})
            events = _norm(us, "date", "title")
    except Exception:
        events = []
    return events[-limit:] if len(events) > limit else events
