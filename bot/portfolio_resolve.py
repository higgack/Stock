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

import os
import re
from pathlib import Path

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
    # 반도체 추가 (사용자 포트폴리오 2026-06-09)
    "시놉시스": ("SNPS", "US"),
    "실리콘모션테크놀로지": ("SIMO", "US"),
    "인테그리스": ("ENTG", "US"),
    "코닝": ("GLW", "US"),
    "kla": ("KLAC", "US"),
    "에호르테스트시스템스": ("AEHR", "US"),
    "델테크놀로지스": ("DELL", "US"),
    "애널로그디바이시스": ("ADI", "US"),
    "아스테라랩스": ("ALAB", "US"),
    "케이던스디자인시스템즈": ("CDNS", "US"),
    "하이맥스테크놀로지스": ("HIMX", "US"),
    "폼팩터": ("FORM", "US"),
    "gsi테크놀로지": ("GSIT", "US"),
    "아터리스": ("AIP", "US"),
    "포엣테크놀로지스": ("POET", "US"),
    "에이알엠홀딩스": ("ARM", "US"),
    "울프스피드": ("WOLF", "US"),
    "아디아": ("ADEA", "US"),
    "아데이아": ("ADEA", "US"),
    "인모드": ("INMD", "US"),
    "비보심랩스": ("VIVS", "US"),
    "비트마인이머전테크놀로지스": ("BIMI", "US"),
    # 에너지/소재
    "넥스트에라에너지": ("NEE", "US"),
    "앨버말": ("ALB", "US"),
    "알버말": ("ALB", "US"),
    "우라늄에너지": ("UEC", "US"),
    "플러그파워": ("PLUG", "US"),
    "뉴스케일파워": ("SMR", "US"),
    # 소프트웨어/클라우드/핀테크
    "오라클": ("ORCL", "US"),
    "세일스포스": ("CRM", "US"),
    "코어위브": ("CRWV", "US"),
    "유니티소프트웨어": ("U", "US"),
    "서클인터넷그룹": ("CRCL", "US"),
    # 산업재/헬스케어/기타
    "ftai에비에이션": ("FTAI", "US"),
    "아토메라": ("ATOM", "US"),
    "트립어드바이저": ("TRIP", "US"),
    "제네락홀딩스": ("GNRC", "US"),
    "렌즈테라퓨틱스": ("LENZ", "US"),
    "틸레이브랜즈": ("TLRY", "US"),
    "아이보타": ("IBTA", "US"),
    "pdl바이오파마": ("PDLI", "US"),
    "페르미": ("FRMI", "US"),          # Fermi America (NASDAQ, 운영자 확인 2026-06-19)
    # US ETF
    "firsttrustrbaamericanindustrialrena": ("AIRR", "US"),
    "roundhillmemory": ("DRAM", "US"),
    # 외국(ADR/현지) — 대만/유럽
    "tsmc": ("TSM", "US"),
    "타이완세미컨덕터": ("TSM", "US"),
    "asml": ("ASML", "US"),
    "에이에스엠엘": ("ASML", "US"),
}

_DROP_TOKENS = ("adr", "보통주", "우선주", "(주)", "㈜", "inc", "corp", "corporation",
                "co.,ltd", "co.,ltd.", "ltd", "holding", "holdings", "주식회사")

# KR ETF 브랜드 prefix — 뱅크샐러드 export 의 상품명이 이 중 하나로 시작하면 KR ETF.
_KR_ETF_BRANDS = (
    "TIGER", "KODEX", "ARIRANG", "SOL", "TIME", "KBSTAR", "HANARO", "ACE",
    "KOSEF", "SMART", "BNK", "PLUS", "WOORI", "파워", "마이티",
    "타이거", "코덱스", "아리랑", "케이비스타", "하나로", "에이스",
)


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


def _is_kr_etf_brand(name: str) -> bool:
    """상품명이 KR ETF 브랜드 prefix 로 시작하면 True."""
    upper = (name or "").strip().upper()
    norm = (name or "").strip()
    for brand in _KR_ETF_BRANDS:
        if upper.startswith(brand.upper()) or norm.startswith(brand):
            return True
    return False


def resolve_overseas(name: str) -> tuple[str, str] | None:
    """해외 한글음역 → (ticker, market) 또는 None (alias 미등록)."""
    key = _normalize(name)
    return _OVERSEAS_ALIAS.get(key)


# ── 국내 수동 alias (뱅크샐러드 상품명 ≠ KRX 공식명, DART/pykrx 미스 보완) ──
_KR_ALIAS: dict[str, tuple[str, str]] = {
    "피엠티5r": ("147760", "KR"),
}


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
    """국내 종목명 → (종목코드, 'KR') 또는 None.

    0순위 KR 수동 alias (뱅크샐러드 상품명 ≠ KRX 공식명).
    1순위 **DART corp_code**(이름→6자리 종목번호) — KRX 로그인 불필요, corp_code
    맵 1회 다운로드 후 30일 디스크 캐시, per-ticker 네트워크 0, **₩0**. 357종목도
    dict 조회라 즉시·무료. (yfinance suffix probe 는 357회 rate-limit·negative-
    cache 위험이라 안 씀 — 시장 suffix 는 호출부가 NOAH 매칭 시 .KS/.KQ 둘 다 시도.)
    2순위 pykrx 역맵(KRX creds 있을 때, 보통 'CODE.KS' 형태) — DART 미스 시만."""
    ka = _KR_ALIAS.get(_normalize(name))
    if ka:
        return ka
    try:
        from bot.dart_client import get_dart
        hits = get_dart().find_by_name(name)
        if hits and hits[0].get("stock_code"):
            return (hits[0]["stock_code"], "KR")
    except Exception:
        pass
    m = _load_kr_name_map()
    t = m.get(_normalize(name))
    return (t, "KR") if t else None


# ── KR ETF pykrx 역맵 (ETF 종목명 → 'CODE.KS'). ──
_KR_ETF_MAP: dict[str, str] | None = None


def _load_kr_etf_map() -> dict[str, str]:
    """KRX ETF 상장목록 {정규화 종목명: 'CODE.KS'}. pykrx/creds 부재 시 {}."""
    global _KR_ETF_MAP
    if _KR_ETF_MAP is not None:
        return _KR_ETF_MAP
    out: dict[str, str] = {}
    try:
        from pykrx import stock
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        for code in stock.get_market_ticker_list(today, market="ETF"):
            nm = stock.get_market_ticker_name(code)
            if nm:
                out.setdefault(_normalize(nm), f"{code}.KS")
    except Exception:
        pass
    _KR_ETF_MAP = out
    return out


def _kr_etf_meta_reverse() -> dict[str, str]:
    """_KR_ETF_METADATA 의 name_ko → ticker 역맵 (import-time 1회)."""
    try:
        from bot.kr_etf_metadata import _KR_ETF_METADATA
        return {_normalize(v["name_ko"]): k
                for k, v in _KR_ETF_METADATA.items() if v.get("name_ko")}
    except Exception:
        return {}


_ETF_META_REV: dict[str, str] | None = None


def resolve_kr_etf(name: str) -> tuple[str, str] | None:
    """KR ETF 상품명 → (ticker, 'KR') 또는 None.

    1순위: kr_etf_metadata 역맵 (name_ko 매칭, 하드코딩 ~50종)
    2순위: pykrx ETF 전수 역맵 (KRX 상장 ETF 전체, creds 필요)
    3순위: 브랜드 prefix 매칭 → ticker None 이지만 market='KR' 보장
    """
    global _ETF_META_REV
    if _ETF_META_REV is None:
        _ETF_META_REV = _kr_etf_meta_reverse()

    key = _normalize(name)

    t = _ETF_META_REV.get(key)
    if t:
        return (t, "KR")

    em = _load_kr_etf_map()
    t2 = em.get(key)
    if t2:
        return (t2, "KR")

    if _is_kr_etf_brand(name):
        return (None, "KR")

    return None


# ── 네이버 종목 자동완성 → 코드 자동 해석 (사용자 2026-06-20 '자동 분류 + 네이버로
#    국내도'). 큐레이션 alias/pykrx 가 못 잡은 종목을 ac.stock.naver.com 자동완성으로
#    국내(KOSPI/KOSDAQ → .KS/.KQ) + 해외(NASDAQ/NYSE/AMEX → US 티커) 한 번에 해석.
#    7일 디스크 캐시. 네트워크/포맷 불일치 시 graceful None('이름만 표시' 유지).
_NAVER_AC = "https://ac.stock.naver.com/ac"
_NAVER_CACHE = (Path(os.environ.get("TRADINGAGENTS_DATA_DIR")
                     or (Path.home() / ".tradingagents")) / "naver_stock_ac.json")
_KR_MKT = {"KOSPI": ".KS", "KOSDAQ": ".KQ", "KONEX": ".KQ"}
_US_MKT = {"NASDAQ", "NYSE", "AMEX", "NYSE ARCA", "NYSEARCA", "OTC"}


def _naver_cache_load() -> dict:
    import json
    try:
        return json.loads(_NAVER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _naver_cache_save(d: dict) -> None:
    import json
    try:
        _NAVER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _NAVER_CACHE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def resolve_via_naver(name: str) -> tuple[str, str] | None:
    """네이버 종목 자동완성 → (ticker, market) 또는 None. 국내=.KS/.KQ, 해외=US 티커.
    이름 정규화 정확일치 우선 매칭(부분일치 오매칭 방지). 7일 캐시 + graceful."""
    import time
    q = (name or "").strip()
    if not q:
        return None
    key = _normalize(q)
    cache = _naver_cache_load()
    hit = cache.get(key)
    if hit and (time.time() - hit.get("ts", 0)) < 7 * 86400:
        v = hit.get("v")
        return tuple(v) if v else None
    res: tuple[str, str] | None = None
    try:
        import requests
        r = requests.get(_NAVER_AC, params={"q": q, "target": "stock"},
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://m.stock.naver.com/"},
                         timeout=4)
        items = (r.json() or {}).get("items") or []
        # items 는 [[...]] 또는 [...] — dict 항목만 평탄화
        flat = []
        for x in items:
            if isinstance(x, dict):
                flat.append(x)
            elif isinstance(x, list):
                flat.extend([y for y in x if isinstance(y, dict)])
        for it in flat:
            nm = it.get("name") or it.get("nm") or ""
            code = (it.get("code") or it.get("cd") or it.get("symbolCode")
                    or it.get("reutersCode") or "").strip()
            typ = (it.get("typeCode") or it.get("type") or it.get("nationCode")
                   or it.get("market") or "").upper()
            if not code or _normalize(nm) != key:        # 정확일치만(오매칭 차단)
                continue
            if typ in _KR_MKT and code.isdigit():
                res = (f"{code.zfill(6)}{_KR_MKT[typ]}", "KR")
                break
            if typ in _US_MKT or (not code.isdigit() and code.isascii()):
                res = (code.upper(), "US")
                break
    except Exception:
        res = None
    cache[key] = {"ts": time.time(), "v": list(res) if res else None}
    _naver_cache_save(cache)
    return res


_YF_US_EXCH = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "NIM", "OEM", "OQB"}


def resolve_via_yfinance(name: str) -> tuple[str, str] | None:
    """yfinance Search → (ticker, market). 네이버 폴백 뒤 보강 (사용자 2026-06-20
    '야후도'). top EQUITY 만, 국내(.KS/.KQ)=KR · 미국거래소(점없는 심볼)=US 만 보수적
    채택(타 시장은 None — 오분류 방지). 7일 캐시(네이버와 동일 파일, yf: prefix)·
    graceful(yfinance 미설치/네트워크 시 None)."""
    import time
    q = (name or "").strip()
    if not q:
        return None
    key = "yf:" + _normalize(q)
    cache = _naver_cache_load()
    hit = cache.get(key)
    if hit and (time.time() - hit.get("ts", 0)) < 7 * 86400:
        v = hit.get("v")
        return tuple(v) if v else None
    res: tuple[str, str] | None = None
    try:
        import yfinance as yf
        quotes = getattr(yf.Search(q, max_results=6), "quotes", None) or []
        for qd in quotes:
            if (qd.get("quoteType") or "").upper() != "EQUITY":
                continue
            sym = (qd.get("symbol") or "").strip()
            if not sym:
                continue
            if sym.endswith(".KS") or sym.endswith(".KQ"):
                res = (sym, "KR")
                break
            ex = (qd.get("exchange") or "").upper()
            if "." not in sym and sym.isascii() and ex in _YF_US_EXCH:
                res = (sym.upper(), "US")
                break
    except Exception:
        res = None
    cache[key] = {"ts": time.time(), "v": list(res) if res else None}
    _naver_cache_save(cache)
    return res


def resolve_ticker(name: str) -> dict:
    """상품명 → {ticker, market, matched, source}.

    순서: 해외 alias → 국내 DART/pykrx → KR ETF → **네이버 자동완성** → **yfinance
    Search** → 미매칭(None). 미매칭이어도 스냅샷 평가금액은 표시되므로 ingest 가 계속
    진행한다('이름만 표시'). 네이버·야후가 페르미(FRMI)·pykrx 누락 국내종목을 자동 흡수."""
    ov = resolve_overseas(name)
    if ov:
        return {"ticker": ov[0], "market": ov[1], "matched": True, "source": "alias"}
    kr = resolve_kr(name)
    if kr:
        return {"ticker": kr[0], "market": kr[1], "matched": True, "source": "pykrx"}
    etf = resolve_kr_etf(name)
    if etf:
        src = "etf_meta" if etf[0] else "etf_brand"
        matched = etf[0] is not None
        return {"ticker": etf[0], "market": etf[1], "matched": matched, "source": src}
    nv = resolve_via_naver(name)
    if nv:
        return {"ticker": nv[0], "market": nv[1], "matched": True, "source": "naver"}
    yf = resolve_via_yfinance(name)
    if yf:
        return {"ticker": yf[0], "market": yf[1], "matched": True, "source": "yfinance"}
    return {"ticker": None, "market": None, "matched": False, "source": None}
