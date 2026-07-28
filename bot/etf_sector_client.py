"""섹터 ETF 합성 업종 등락 — JP/TW/CN/HK 메인 위젯 (사용자 2026-06-13).

KR=Naver, US=Finviz 처럼 전용 섹터 스크레이퍼가 없는 시장의 '업종 등락 TOP
10'을, 시장별 **섹터 ETF 일일 등락률**로 합성한다. ETF 는 해당 섹터를 시총
가중 보유하므로 섹터 등락의 정확한 프록시다. 비용 = ETF N개 yfinance 시세
1배치(저비용·키 불요). 데이터 없으면 빈 dict → 위젯 자동 생략(graceful).

커버리지: JP 17(TOPIX-17 완전)·CN 14 = TOP10 충분 / TW·HK 는 섹터 ETF
생태계가 희소해 4·4개(정직 라벨 '주요 업종'). TW/HK 풀 섹터는 유니버스
스캔(후속 단계) 필요. 출처 sector_strength_tools.py 의 시장별 override 맵.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# (ETF, 한국어 섹터명) — sector_strength_tools.py 의 *_INDUSTRY_OVERRIDES 에서
# distinct ETF 추출 + 광역(시장지수) 포함. JP=TOPIX-17 NEXT FUNDS 1617~1633.
_SECTOR_ETFS: dict[str, list[tuple[str, str]]] = {
    "JP": [
        ("1617.T", "식품"), ("1618.T", "에너지"), ("1619.T", "건설·자재"),
        ("1620.T", "소재·화학"), ("1621.T", "제약"), ("1622.T", "자동차·수송"),
        ("1623.T", "철강·비철"), ("1624.T", "기계"), ("1625.T", "전기·정밀"),
        ("1626.T", "IT·서비스"), ("1627.T", "전력·가스"), ("1628.T", "운수·물류"),
        ("1629.T", "상사·도매"), ("1630.T", "소매"), ("1631.T", "은행"),
        ("1632.T", "금융(은행제외)"), ("1633.T", "부동산"),
    ],
    "TW": [
        ("0053.TW", "전자"), ("0055.TW", "금융"), ("00891.TW", "반도체"),
        ("0050.TW", "TAIEX 50(시장)"),
    ],
    "CN_A": [
        ("512760.SS", "반도체"), ("512800.SS", "은행"), ("512070.SS", "보험·증권"),
        ("512170.SS", "의료"), ("512690.SS", "주류"), ("159928.SZ", "소비"),
        ("159805.SZ", "신에너지차"), ("159755.SZ", "신에너지"), ("515790.SS", "광전지"),
        ("515980.SS", "통신·5G"), ("512200.SS", "부동산"), ("159930.SZ", "에너지"),
        ("515210.SS", "철강"), ("159996.SZ", "가전"),
    ],
    "HK": [
        ("3033.HK", "항셍테크"), ("2828.HK", "H주(은행·보험·석유)"),
        ("2778.HK", "부동산"), ("2800.HK", "항셍지수(시장)"),
    ],
}

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "etf_sector"
_CACHE_TTL_SEC = 10 * 60   # 10분 — 일일 % 라 짧은 TTL 불요


def _now_kst_label() -> str:
    from datetime import datetime, timedelta, timezone
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    return kst.strftime("%Y-%m-%d %H:%M")


def _cached(market: str) -> Optional[dict]:
    try:
        fp = _CACHE_DIR / f"{market}.json"
        if fp.exists() and (time.time() - fp.stat().st_mtime) < _CACHE_TTL_SEC:
            return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _cache_write(market: str, obj: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{market}.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _rank_sector_etfs(rows: list[dict], top_n: int = 10) -> dict:
    """rows=[{name,pct}] → {up:[상승 desc], down:[하락 asc]} TOP n. 순수.
    부호로 분리(상승 업종=양수만, 하락 업종=음수만) — KR/US 위젯과 동일."""
    valid = [r for r in rows if isinstance(r.get("pct"), (int, float))]
    up = sorted([r for r in valid if r["pct"] > 0],
                key=lambda r: r["pct"], reverse=True)[:top_n]
    down = sorted([r for r in valid if r["pct"] < 0],
                  key=lambda r: r["pct"])[:top_n]
    return {"up": up, "down": down}


def _fetch_etf_pcts(etfs: list[tuple[str, str]]) -> list[dict]:
    """yfinance 배치로 각 ETF 일일 등락률(%). 2일 미만 데이터는 스킵. graceful."""
    import yfinance as yf
    name_of = dict(etfs)
    tickers = [e[0] for e in etfs]
    rows: list[dict] = []
    try:
        df = yf.download(tickers, period="5d", interval="1d",
                         progress=False, group_by="ticker", threads=True)
    except Exception as exc:
        log.warning("etf sector download error: %s", exc)
        return rows
    for tk in tickers:
        try:
            sub = df[tk] if len(tickers) > 1 else df
            closes = sub["Close"].dropna()
            if len(closes) >= 2 and closes.iloc[-2]:
                pct = (closes.iloc[-1] / closes.iloc[-2] - 1) * 100.0
                rows.append({"name": name_of[tk], "pct": round(float(pct), 2)})
        except Exception:
            continue
    return rows


def fetch_sector_movers_etf(market: str, top_n: int = 10) -> dict:
    """시장(JP/TW/CN_A/HK) 업종 등락 — 섹터 ETF 합성. {up,down,ts,source}.
    캐시(10분) → yfinance 배치 → 랭킹. 실패/미지원 시장 시 빈 dict."""
    etfs = _SECTOR_ETFS.get(market)
    if not etfs:
        return {"up": [], "down": [], "ts": "", "source": ""}
    c = _cached(market)
    if c is not None:
        return c
    rows = _fetch_etf_pcts(etfs)
    result = _rank_sector_etfs(rows, top_n)
    result["ts"] = _now_kst_label()
    result["source"] = "섹터 ETF·yfinance"
    if rows:                      # 빈 결과는 캐시 안 함(다음 호출 재시도)
        _cache_write(market, result)
    return result
