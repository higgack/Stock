"""단일 KR 종목 실시간 시세 — Naver polling.finance (키 불필요·VM IP 차단
없음·장중 라이브).

왜: 상세 페이지(검색 카드 + 차트 현재가)의 KR 현재가가 KIS 키 부재 시
yfinance 로 폴백하는데, yfinance 의 KR 시세는 EOD(종가)라 장중엔 직전 거래일
종가(예: 월요일 장중에 금요일 종가)를 준다(사용자 2026-06-15 'NXT 클릭→
금요일종가'). NXT·신고저·급등락 등 다른 surface 가 이미 쓰는 Naver 와 동일하게
상세도 Naver 실시간으로 채워 키·IP차단 무관하게 장중 라이브를 보장한다.

엔드포인트(2026-06-15 VM 검증):
  GET polling.finance.naver.com/api/realtime/domestic/stock/<6자리코드>
  → {datas:[{closePriceRaw(현재가), fluctuationsRatioRaw(등락%),
       compareToPreviousPrice.code(2 상승/5 하락), marketValueFullRaw(시총·원),
       localTradedAt(체결시각), marketStatus, tradeStopType, ...}]}
장중엔 closePriceRaw 가 라이브로 갱신, 장후엔 당일 종가. graceful(실패 시 None).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
    "Referer": "https://m.stock.naver.com/",
}
_TTL = 30  # 30초 SWR — 장중 준실시간 (상위 /api/quote 5분 캐시가 추가로 dedup)
_cache: dict[str, tuple[float, dict | None]] = {}


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def _parse_amt(v):
    """'4,707,022백만' / '47억' / '1.2조' / 숫자 → 원(float). 네이버 거래대금이
    단위 접미사로 와 _num 이 못 푸는 것 처리(백만 먼저 — '만'과 충돌 방지). graceful None."""
    s = str(v or "").strip()
    if not s:
        return None
    mult = 1.0
    for suf, m in (("조", 1e12), ("백만", 1e6), ("억", 1e8), ("만", 1e4)):
        if suf in s:
            s = s.split(suf)[0]
            mult = m
            break
    n = _num(s)
    return n * mult if n is not None else None


def _extract_name(item: dict) -> str | None:
    """datas[0] 의 한글 종목명 (관심종목 한글화, 사용자 2026-06-15 '네이버에서').

    네이버 응답 한글명 필드키가 국내/해외·버전별로 달라 단정하지 않고 후보를
    순서대로 시도(해외는 stockNameKor 가 한글, stockName 은 영문일 수 있어
    한글 후보 우선). 모두 부재면 None → 호출부가 yfinance 영문 fallback.
    값이 코드/티커뿐이면(실명 아님) skip. 라이브 필드키는 VM 1줄 검증."""
    for k in ("stockNameKor", "stockName", "korNm", "itemName",
              "stockNameEng", "name"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _parse_quote(item: dict) -> dict | None:
    """datas[0] 항목 → {price, pct, mcap, ts, name} (순수·단위테스트용). price 없으면 None.

    price = closePriceRaw(현재가). pct = fluctuationsRatioRaw 에 부호 적용
    (compareToPreviousPrice.code '5'=하락이면 음수). mcap = marketValueFullRaw
    (원 단위, 그대로). ts = localTradedAt(마지막 체결시각, 신선도 표시용).
    name = 한글 종목명(_extract_name, 관심종목 한글화용)."""
    if not isinstance(item, dict):
        return None
    price = _num(item.get("closePriceRaw") or item.get("closePrice"))
    if not price:
        return None
    pct = _num(item.get("fluctuationsRatioRaw") or item.get("fluctuationsRatio"))
    if pct is not None:
        cmp = item.get("compareToPreviousPrice") or {}
        falling = isinstance(cmp, dict) and str(cmp.get("code")) == "5"
        pct = -abs(pct) if falling else abs(pct)
    # 미국 시간외(장전/장후) 진행 중 (사용자 2026-06-15/16). Naver
    # overMarketPriceInfo — over-market OPEN 일 때만. KR 국내엔 이 필드 없음
    # (안전·무영향). 라이브 price/pct(차트·관심종목)는 시간외가로 교체(누적
    # 등락% vs 전일종가). 동시에 정규장 종가(reg_close)·시간외가(over_price)·
    # 시간외 등락%(over_pct, vs 정규장 종가)·세션·체결시각을 분리 노출 →
    # 상세 페이지가 Naver 식 '정규장 + 시간외' 라인을 그릴 수 있게.
    reg_close, reg_pct = price, pct
    over_price = over_pct = over_volume = over_value = None
    over_session = ""
    over_ts = ""
    _over = item.get("overMarketPriceInfo")
    if isinstance(_over, dict) and str(_over.get("overMarketStatus")) == "OPEN":
        _op = _num(_over.get("overPrice"))
        if _op:
            over_session = str(_over.get("tradingSessionType") or "")
            over_ts = str(_over.get("localTradedAt") or "")
            over_price = _op
            over_volume = _num(_over.get("accumulatedTradingVolume"))     # 시간외 거래량
            over_value = _parse_amt(_over.get("accumulatedTradingValue"))  # 시간외 거래대금(원)
            _opct = _num(_over.get("fluctuationsRatio"))
            if _opct is not None:
                _ocmp = _over.get("compareToPreviousPrice") or {}
                _ofall = isinstance(_ocmp, dict) and str(_ocmp.get("code")) == "5"
                over_pct = -abs(_opct) if _ofall else abs(_opct)
            # ⚠️ price/차트는 정규장 종가 유지 — 시간외(NXT) 가격은 over_price 로만
            # 분리 노출(시간외 라인·시간외 보드). 네이버페이도 메인=정규장종가,
            # NXT=별도 표기 (사용자 2026-06-16 '차트·현재가 모든곳·모든나라 정규장
            # 기준'). 옛 price=over_price 대체가 라이브 틱을 NXT 가로 튀게 하던 것 제거.
    return {
        "price": price,
        "pct": pct,
        "mcap": _num(item.get("marketValueFullRaw")),
        "ts": item.get("localTradedAt"),
        "name": _extract_name(item),
        "reg_close": reg_close,         # 정규장 종가 (= price, 메인)
        "reg_pct": reg_pct,             # 정규장 등락%
        "over_price": over_price,       # 시간외(NXT)가 (없으면 None) — 별도 라인/보드용
        "over_pct": over_pct,           # Naver overMarketPriceInfo.fluctuationsRatio
                                        # (NXT 세션 자체 등락 — 순수 시간외 move 는
                                        # over_price/reg_close 로 계산)
        "over_volume": over_volume,     # 시간외 세션 누적 거래량 (없으면 None)
        "over_value": over_value,       # 시간외 세션 누적 거래대금(원, 없으면 None)
        "over_session": over_session,   # "" / PRE_MARKET / AFTER_MARKET
        "over_ts": over_ts,             # 시간외 체결시각(ET ISO) — KST 변환용
        # 당일 OHLCV — 차트의 당일 일봉을 라이브로 그리는 데 사용(yahoo 가 장중
        # 당일 봉을 EOD/미제공하는 문제 해소). close 는 price 와 동일.
        "open": _num(item.get("openPriceRaw")),
        "high": _num(item.get("highPriceRaw")),
        "low": _num(item.get("lowPriceRaw")),
        "close": price,
        "volume": _num(item.get("accumulatedTradingVolumeRaw")),
        # 정규장 누적 거래대금(현지통화 원금액) — 미국 장전·장후 보드 거래대금
        # 컬럼용(사용자 2026-06-16 '거래대금, 정규장 기준이더라도'). 시간외 거래대금
        # 은 네이버 worldstock 미제공이라 정규장 누적값. graceful(부재 시 None).
        "value": _num(item.get("accumulatedTradingValueRaw")),
    }


def fetch_kr_quote(code: str) -> dict | None:
    """6자리 KR 종목코드(또는 '267260.KS') → 실시간 {price, pct, mcap, ts}.

    30초 SWR 캐시. 어떤 실패든 None 으로 degrade(상세는 yfinance 폴백 유지) —
    '오류 나면 안 된다'. 네트워크 실패 시 직전 캐시값(있으면) 유지."""
    code = (code or "").strip().upper().split(".")[0]
    if not code.isdigit() or not (4 <= len(code) <= 6):
        return None
    code = code.zfill(6)
    now = time.time()
    hit = _cache.get(code)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    out: dict | None = hit[1] if hit else None  # 실패 시 직전값 유지
    try:
        import requests
        r = requests.get(_URL.format(code=code), headers=_HDRS, timeout=8)
        if r.status_code == 200:
            j = r.json()
            datas = (j.get("datas") if isinstance(j, dict) else None) or []
            it = datas[0] if datas and isinstance(datas[0], dict) else None
            parsed = _parse_quote(it) if it else None
            if parsed:
                out = parsed
    except Exception as exc:
        log.debug("naver_quote %s failed: %s", code, exc)
    _cache[code] = (now, out)
    return out


if __name__ == "__main__":  # pragma: no cover — VM 1줄 진단
    import sys
    for _c in (sys.argv[1:] or ["267260"]):
        print(_c, "→", fetch_kr_quote(_c))
