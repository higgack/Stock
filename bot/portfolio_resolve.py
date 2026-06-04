"""뱅크샐러드 종목 한글명 → 티커 변환 (자산관리 P1 증분2).

뱅샐 export 의 '상품명'은 한글(국내) 또는 한글 음역(해외)뿐 — **종목코드가 없다.**
포트폴리오 라이브 평가·NOAH 분석 오버레이를 하려면 티커가 필요하다.

- **국내**: pykrx KRX 상장목록(종목명↔코드) 역맵 → '039030.KS'/'.KQ'. 7일 디스크
  캐시. KRX creds/pykrx 부재 시 graceful None — 스냅샷 평가금액은 티커 없이도
  표시되므로 치명적 아님.
- **해외**: 한글 음역은 깔끔한 API 가 없어 **큐레이션 alias 맵**(반도체/빅테크
  중심, 사용자 포트폴리오 반영). 미매칭은 None('이름만 표시'). _JP/_TW/_CN_
  ENGLISH_ALIAS 와 동일 철학.
- 'ADR' / '(주)' / '㈜' / 공백 정규화.

순수 함수(alias·정규화)는 단위테스트 가능. pykrx 경로는 try/except graceful.
"""
from __future__ import annotations

import re

# ── 해외 한글음역 → (티커, 시장). 사용자 export 에서 실제 등장한 종목 + 한국
#    투자자가 흔히 보유하는 미국/외국 종목. 키는 _normalize() 적용 후 형태
#    (공백·'ADR'·'(주)' 제거). ⚠️ '파마리서치'(214450 KR)·'코미코'(KR) 처럼
#    리서치/테크 가 붙어도 국내인 종목은 여기 넣지 않는다(pykrx 가 잡음).
_OVERSEAS_ALIAS: dict[str, tuple[str, str]] = {
    # 반도체 장비/소재 (사용자 보유 다수)
    "램리서치": ("LRCX", "US"),
    "어플라이드머티어리얼즈": ("AMAT", "US"),
    "온세미콘덕터": ("ON", "US"),
    "마이크로칩테크놀러지": ("MCHP", "US"),
    "앰코테크놀로지": ("AMKR", "US"),
    "비코인스트루먼츠": ("VECO", "US"),
    "에흐르테스트시스템즈": ("AEHR", "US"),
    "st마이크로일렉트로닉스": ("STM", "US"),  # ADR
    "ase테크놀로지홀딩": ("ASX", "US"),       # ADR (TW 본사)
    "klacorp": ("KLAC", "US"),
    "케이엘에이": ("KLAC", "US"),
    "테라다인": ("TER", "US"),
    "엔테그리스": ("ENTG", "US"),
    "코히런트": ("COHR", "US"),
    "마벨테크놀로지": ("MRVL", "US"),
    # 빅테크 / 반도체 대형
    "엔비디아": ("NVDA", "US"),
    "애플": ("AAPL", "US"),
    "마이크로소프트": ("MSFT", "US"),
    "테슬라": ("TSLA", "US"),
    "아마존": ("AMZN", "US"),
    "알파벳": ("GOOGL", "US"),
    "구글": ("GOOGL", "US"),
    "메타플랫폼스": ("META", "US"),
    "메타": ("META", "US"),
    "브로드컴": ("AVGO", "US"),
    "에이엠디": ("AMD", "US"),
    "amd": ("AMD", "US"),
    "인텔": ("INTC", "US"),
    "마이크론테크놀로지": ("MU", "US"),
    "마이크론": ("MU", "US"),
    "퀄컴": ("QCOM", "US"),
    "텍사스인스트루먼츠": ("TXN", "US"),
    "팔란티어": ("PLTR", "US"),
    "넷플릭스": ("NFLX", "US"),
    # 외국(ADR/현지) — 대만/유럽
    "tsmc": ("TSM", "US"),
    "타이완세미컨덕터": ("TSM", "US"),
    "asml": ("ASML", "US"),
    "에이에스엠엘": ("ASML", "US"),
}

_DROP_TOKENS = ("adr", "보통주", "우선주", "(주)", "㈜", "inc", "corp", "corporation",
                "co.,ltd", "co.,ltd.", "ltd", "holding", "holdings", "주식회사")


def _normalize(name: str) -> str:
    """비교용 정규화: 소문자(영문)·공백 제거·부수 토큰 제거.

    '램 리서치' → '램리서치', 'ST 마이크로 일렉트로닉스 ADR' →
    'st마이크로일렉트로닉스', '어플라이드 머티어리얼즈' → '어플라이드머티어리얼즈'.
    """
    s = (name or "").strip().lower()
    for tok in _DROP_TOKENS:
        s = s.replace(tok, " ")
    s = re.sub(r"\s+", "", s)            # 모든 공백 제거
    s = re.sub(r"[^0-9a-z가-힣]", "", s)  # 기호 제거
    return s


def resolve_overseas(name: str) -> tuple[str, str] | None:
    """해외 한글음역 → (ticker, market) 또는 None (alias 미등록)."""
    key = _normalize(name)
    return _OVERSEAS_ALIAS.get(key)


# ── 국내 pykrx 역맵 (종목명 → '코드.KS/.KQ'). 7일 디스크 캐시. ──
_KR_MAP: dict[str, str] | None = None


def _load_kr_name_map() -> dict[str, str]:
    """KRX 전체 상장목록 {정규화 종목명: 'CODE.KS/.KQ'}. pykrx/creds 부재 시 {}.

    KS=KOSPI, KQ=KOSDAQ. 동일 정규화명 충돌 시 먼저 들어온 것 유지(드묾)."""
    global _KR_MAP
    if _KR_MAP is not None:
        return _KR_MAP
    out: dict[str, str] = {}
    try:
        from pykrx import stock  # noqa: heavy/optional
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        for mkt, suffix in (("KOSPI", "KS"), ("KOSDAQ", "KQ")):
            try:
                for code in stock.get_market_ticker_list(today, market=mkt):
                    nm = stock.get_market_ticker_name(code)
                    if nm:
                        out.setdefault(_normalize(nm), f"{code}.{suffix}")
            except Exception:
                continue
    except Exception:
        out = {}
    _KR_MAP = out
    return out


def resolve_kr(name: str) -> tuple[str, str] | None:
    """국내 종목명 → ('CODE.KS'|'CODE.KQ', 'KR') 또는 None (미상장/pykrx 부재)."""
    m = _load_kr_name_map()
    t = m.get(_normalize(name))
    return (t, "KR") if t else None


def resolve_ticker(name: str) -> dict:
    """상품명 → {ticker, market, matched, source}.

    순서: 해외 alias → 국내 pykrx → 미매칭(None). 미매칭이어도 스냅샷 평가금액은
    표시되므로 ingest 가 계속 진행한다('이름만 표시')."""
    ov = resolve_overseas(name)
    if ov:
        return {"ticker": ov[0], "market": ov[1], "matched": True, "source": "alias"}
    kr = resolve_kr(name)
    if kr:
        return {"ticker": kr[0], "market": kr[1], "matched": True, "source": "pykrx"}
    return {"ticker": None, "market": None, "matched": False, "source": None}
