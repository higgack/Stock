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

import re

# US 8-K 항목 라벨(edgar_client 영문, 고정 ~21종) → 한국어. ₩0 완역(MT 불필요).
_US_8K_KR = {
    "Material Agreement": "주요계약 체결",
    "Agreement Termination": "주요계약 해지",
    "Bankruptcy/Receivership": "파산/법정관리",
    "Acquisition/Disposition of Assets": "자산 인수/처분",
    "Results of Operations (Earnings)": "실적 발표",
    "Material Financial Obligation": "주요 채무 발생",
    "Triggering Events re Obligations": "채무 트리거 사건",
    "Cost Associated with Exit": "사업 철수/구조조정 비용",
    "Asset Impairment": "자산 손상차손",
    "Exchange Delisting": "상장폐지",
    "Unregistered Sales of Equity": "비등록 주식 발행",
    "Change of Auditor": "감사인 변경",
    "Financial Statement Non-Reliance": "재무제표 신뢰성 부정",
    "Change in Control": "경영권 변경",
    "Director/Officer Departure or Appointment": "임원 선임/사임",
    "Amendments to Articles": "정관 변경",
    "Submission of Matters to Vote": "주주총회 표결",
    "Fiscal Year Change": "회계연도 변경",
    "Regulation FD Disclosure": "Reg FD 공시",
    "Other Events": "기타 사항",
    "Financial Statements and Exhibits": "재무제표·첨부",
}


def _us_label_kr(label: str) -> str:
    """'§1.01 Material Agreement' → '§1.01 주요계약 체결'. 매핑 없으면 원문 유지."""
    m = re.match(r"(§[\d.]+\s*)(.*)", label or "")
    if m:
        return m.group(1) + _US_8K_KR.get(m.group(2).strip(), m.group(2).strip())
    return label or ""


# ── 공시 종류 분류 (다국어 키워드). 색은 사실 기준(호재/악재 판단 아님). ──
# 사용자 정책 2026-06-05: 수주·계약 / 신규시설투자 / 주주환원(배당·자기주식) /
# 자본변동(증자·CB·감자) 4종만 마커로 표시. 그 외(실적·임원·소송 등)는 제외.
_TYPE_COLOR = {
    "order": "#26a69a",        # 수주·공급계약 (초록)
    "litigation": "#26a69a",   # 소송 (초록 — 사용자 요청, 수주와 동색)
    "capex": "#4c9aff",        # 신규시설투자·증설 (파랑)
    "shareholder": "#b07cff",  # 주주환원: 배당·자기주식 취득/소각 (보라)
    "capital": "#f5a623",      # 자본변동: 증자·CB·감자·분할 (주황)
    "mna": "#2dd4bf",          # M&A·지분: 합병·영업양수도·타법인주식 (청록)
    "risk": "#e2574c",         # 생존·거래 리스크: 상장폐지·거래정지·횡령 (빨강)
    "control": "#f78fb3",      # 최대주주·경영권 변경 (분홍)
    "other": "#94a3b8",        # 그 외 — 마커 미표시(필터 제외)
}
_TYPE_LABEL = {"order": "수주·계약", "litigation": "소송", "capex": "시설투자",
               "shareholder": "주주환원", "capital": "자본변동", "mna": "M&A·지분",
               "risk": "리스크", "control": "최대주주변경", "other": "공시"}

# 마커로 표시할 종류(화이트리스트). 'other' 는 제외.
_SHOW_TYPES = ("order", "litigation", "capex", "shareholder", "capital",
               "mna", "risk", "control")

_SHAREHOLDER_KW = ("자기주식", "자사주", "주식소각", "이익소각", "배당", "주주환원",
                   "treasury", "buyback", "repurchase", "dividend",
                   "自己株式", "自社株", "配当", "回購", "回购", "分紅", "分红", "現金股利")
_LITIGATION_KW = ("소송", "제소", "피소", "소장", "가처분", "손해배상청구", "특허침해", "특허소송",
                  "lawsuit", "litigation", "legal proceeding", "complaint filed",
                  "訴訟", "诉讼", "提訴", "起訴")
_RISK_KW = ("상장폐지", "관리종목", "거래정지", "매매거래정지", "감사의견거절", "의견거절",
            "한정의견", "부적정의견", "횡령", "배임", "회생절차", "파산신청", "자본잠식",
            "영업정지", "불성실공시", "투자주의", "투자경고", "투자위험", "투자주의환기",
            "단기과열", "상장적격성", "실질심사",
            "delisting", "bankruptcy", "receivership", "non-reliance", "going concern",
            "asset impairment", "上場廃止", "退市", "破産", "破产", "重整", "監理")
_CONTROL_KW = ("최대주주변경", "최대주주 변경", "최대주주등의주식", "경영권", "지배구조변경",
               "change in control", "控制权", "控股股东变更", "大株主の異動")
_MNA_KW = ("합병", "분할합병", "영업양수", "영업양도", "영업양수도", "타법인주식", "타법인출자",
           "주식교환", "주식이전", "지분인수", "인적분할", "물적분할", "회사분할",
           "acquisition", "disposition of assets", "merger", "tender offer",
           "合併", "合并", "M&A", "收購", "收购", "併購", "併购", "事業譲渡")
_CAPEX_KW = ("신규시설투자", "시설투자", "설비투자", "신규투자", "유형자산", "증설",
             "공장신설", "생산능력", "공장증설",
             "capital expenditure", "capex", "new facility", "capacity", "plant expansion",
             "設備投資", "增産", "扩产", "扩建", "募投", "投资建设")
# US EDGAR 8-K 는 축약 라벨('Material Agreement'·'Unregistered Sales of Equity')을 쓰므로
# 정식 풀네임뿐 아니라 축약형도 키워드에 포함(없으면 US 8-K 가 거의 다 'other' 로 필터됨).
_ORDER_KW = ("단일판매", "공급계약", "공급ㆍ계약", "공급계약체결", "수주", "계약체결", "수주잔고",
             "material agreement", "material definitive agreement", "agreement termination",
             "supply agreement", "contract", "award", "order backlog",
             "受注", "契約", "訂單", "合約", "合同", "中標", "中标", "签订")
_CAPITAL_KW = ("유상증자", "무상증자", "전환사채", "신주인수권", "교환사채", "감자",
               "주식분할", "주식병합", "증자", "신주", "액면분할", "액면병합",
               "unregistered sales of equity", "amendments to articles",
               "offering", "convertible", "warrant", "stock split", "rights issue",
               "増資", "新株", "株式分割", "減資", "增發", "增发", "可轉債", "可转债")


def classify(title: str) -> str:
    """공시 제목 → 종류. 다국어 키워드. 화이트리스트 + 그 외('other').

    검사 순서(특정 우선): 주주환원 → 소송 → 리스크 → 최대주주 → M&A → 시설투자
    → 수주 → 자본변동. '자기주식취득'이 capex('취득')·mna 로 오분류되지 않게
    주주환원을 먼저, mna 는 '타법인주식/합병' 등 특정어만 써 '취득' 오매칭 회피.
    US 8-K 는 축약 라벨이라 각 KW 에 축약형(Material Agreement·Acquisition 등) 포함."""
    t = str(title or "")
    tl = t.lower()

    def _has(kws):
        return any((k in t) or (k.lower() in tl) for k in kws)
    if _has(_SHAREHOLDER_KW):
        return "shareholder"
    if _has(_LITIGATION_KW):
        return "litigation"
    if _has(_RISK_KW):
        return "risk"
    if _has(_CONTROL_KW):
        return "control"
    if _has(_MNA_KW):
        return "mna"
    if _has(_CAPEX_KW):
        return "capex"
    if _has(_ORDER_KW):
        return "order"
    if _has(_CAPITAL_KW):
        return "capital"
    return "other"


def _norm(items: list[dict], date_key: str, title_key: str,
          url_key: str | None = None) -> list[dict]:
    """클라이언트별 dict → 통일된 [{time,title,type,color,url}]. url 은 있으면 원문 링크."""
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
        url = (str(it.get(url_key) or "").strip() if url_key else "")
        out.append({"time": d, "title": title, "type": typ,
                    "color": _TYPE_COLOR[typ], "url": url})
    out.sort(key=lambda e: e["time"])
    return out


def fetch_disclosure_events(ticker: str, limit: int = 250, days: int = 400) -> list[dict]:
    """티커 → 공시 이벤트 마커 리스트. 시장별 무료 클라이언트로 라우팅, 실패 시 [].

    `days` = 차트가 보여줄 기간(일). 시장별 호출 비용·데이터 한계로 캡을 다르게:
    - KR DART / US EDGAR: 차트 범위 풀히스토리(캡 ~11년). DART 는 페이지네이션,
      EDGAR 는 submissions 1콜 → 싸고 안전.
    - JP EDINET: **하루씩 호출하는 구조**라 180일 캡(첫 1회만 호출, 과거일 영구
      캐시). 1년(365콜)은 첫 로딩 지연·차단 위험이라 회피 — 리스크-프리.
    - TW MOPS / CN·HK AKShare: 호출 1~2번이라 1년까지 싸게(데이터는 API 가 주는
      최근 만큼). 각 try/except, 호출부가 보이는 날짜 구간으로 다시 필터."""
    try:
        from bot.market import detect_market
        market = detect_market(ticker)
    except Exception:
        market = "US"
    code = ticker.split(".")[0]
    kr_us_days = min(max(days + 30, 400), 4000)   # KR/US 풀히스토리(캡 ~11년)
    jp_days = min(max(days, 120), 180)            # JP 안전 캡(일단위 호출 비용)
    tw_cn_days = min(max(days, 365), 400)         # TW/CN 1년(호출 1~2회, 싸다)
    events: list[dict] = []
    try:
        if market == "KR":
            from bot.dart_client import get_dart
            raw = get_dart().get_recent_disclosures(code, days_back=kr_us_days, limit=limit)
            events = _norm(raw, "date", "title", "url")
            # DART 구조화 요약(₩0): 증자·자기주식·전환사채·합병 금액 + 배당 context.
            # rcept_no(url 의 rcpNo)로 매칭. 실패 시 graceful(요약 없음).
            try:
                import re as _re
                from bot.dart_detail import get_disclosure_summaries
                summ = get_disclosure_summaries(code, days_back=kr_us_days)
                if summ:
                    for e in events:
                        m = _re.search(r"rcpNo=(\d+)", e.get("url") or "")
                        s = summ.get(m.group(1)) if m else None
                        if not s and "배당" in e.get("title", ""):
                            s = summ.get("__DIVIDEND__")
                        if s:
                            e["summary"] = s
            except Exception:
                pass
        elif market in ("CN_A", "HK"):
            from bot.akshare_client import get_akshare
            raw = get_akshare().get_recent_disclosures(ticker, days_back=tw_cn_days, limit=limit)
            events = _norm(raw, "date", "subject", "url")
        elif market == "JP":
            from bot.edinet_client import get_edinet
            raw = get_edinet().get_recent_disclosures(ticker, days_back=jp_days, limit=limit)
            events = _norm(raw, "date", "description", "url")
        elif market == "TW":
            from bot.mops_client import get_mops
            raw = get_mops().get_recent_disclosures(ticker, days_back=tw_cn_days, limit=limit)
            events = _norm(raw, "date", "subject", "url")
        else:  # US (default)
            from bot.edgar_client import get_recent_8k
            raw = get_recent_8k(ticker, days=kr_us_days, top_n=limit)
            # 분류는 영문 라벨('Material Agreement' 등)로(키워드가 영문). 표시 제목만
            # 그 뒤 한국어로 번역 — 번역 먼저 하면 분류가 깨짐.
            us = []
            for f in raw or []:
                labels = f.get("items_labels") or []
                us.append({"date": f.get("date") or "", "url": f.get("url") or "",
                           "title": " · ".join(labels) if labels else "8-K filing"})
            events = _norm(us, "date", "title", "url")
            for e in events:  # 표시용 제목 한국어화(분류는 위에서 영문으로 이미 확정)
                e["title"] = " · ".join(_us_label_kr(p.strip())
                                        for p in (e.get("title") or "").split(" · "))
    except Exception:
        events = []
    # 화이트리스트(사용자 2026-06-05): 수주·계약 / 시설투자 / 주주환원 / 자본변동만.
    # 그 외(실적·임원·소송·매출손익구조변경 등)는 마커 미표시.
    events = [e for e in events if e.get("type") in _SHOW_TYPES]
    events = events[-limit:] if len(events) > limit else events
    # CN/JP/TW 제목 한국어 번역(Flash, 영구 캐시 → 반복 ₩0). 분류는 위에서 원문으로
    # 이미 확정 → 표시 제목만 교체. KR=원래 한국어, US=고정 사전(위)이라 제외.
    if market in ("CN_A", "HK", "JP", "TW") and events:
        try:
            from bot.chart_translate import translate_titles_kr
            kr = translate_titles_kr([e["title"] for e in events])
            for e in events:
                if kr.get(e["title"]):
                    e["title"] = kr[e["title"]]
        except Exception:
            pass
    return events
