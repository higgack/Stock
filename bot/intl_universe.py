"""JP/HK 전종목 상장목록 — 신고저 full-market 유니버스 (사용자 2026-06-13
'JP/HK full-market'). 공식 listing(JPX data_j.xls / HKEX ListOfSecurities.xlsx,
VM 검증 2026-06-13: 둘 다 200·openpyxl/xlrd/pandas 가용)에서 보통주 코드만
추출 → ['7203.T', ...] / ['0700.HK', ...]. 7일 디스크 캐시(상장목록 변동 느림).
실패 시 빈 리스트 → 호출측이 peer 폴백(회귀 0). 파싱은 헤더/컬럼 동적 탐지로
구조 변형에 견고. 네트워크는 백그라운드 산출 경로에서만(render 금지).
"""
from __future__ import annotations

import logging
from io import BytesIO

log = logging.getLogger("bot.intl_universe")

_JPX_URL = ("https://www.jpx.co.jp/markets/statistics-equities/misc/"
            "tvdivq0000001vg2-att/data_j.xls")
_HKEX_URL = ("https://www.hkex.com.hk/eng/services/trading/securities/"
             "securitieslists/ListOfSecurities.xlsx")
_UA = {"User-Agent": "Mozilla/5.0"}
# JPX 市場・商品区分 중 제외(ETF/REIT/출자증권/인프라펀드 등 비보통주)
_JP_EXCL = ("ETF", "ETN", "REIT", "出資", "インフラ", "PRO", "受益")


def _http_get(url: str):
    import requests
    return requests.get(url, timeout=30, headers=_UA).content


def _jp_pick(code, div) -> str | None:
    """JPX 한 행 → 'NNNN.T' 또는 None (순수·테스트 가능). 4자리 숫자 보통주,
    ETF/REIT/출자증권 등 제외."""
    if any(x in str(div or "") for x in _JP_EXCL):
        return None
    code = str(code or "").strip().split(".")[0]
    return f"{code}.T" if len(code) == 4 and code.isdigit() else None


def _hk_pick(code, category) -> str | None:
    """HKEX 한 행 → 'NNNN.HK' 또는 None (순수·테스트 가능). Category=Equity 만.
    <10000 은 4자리 zero-pad(0700.HK), 이상은 그대로(80737.HK)."""
    if str(category or "").strip() != "Equity":
        return None
    try:
        n = int(float(str(code or "").strip().replace(",", "")))
    except (ValueError, TypeError):
        return None
    if n <= 0:
        return None
    return f"{n:04d}.HK" if n < 10000 else f"{n}.HK"


def _parse_jp(content: bytes) -> list[str]:
    """JPX data_j.xls → ['7203.T', …] (내국·외국 보통주, ETF/REIT 제외)."""
    import pandas as pd
    df = pd.read_excel(BytesIO(content))
    cols = {str(c): c for c in df.columns}
    code_c = next((cols[c] for c in cols if "コード" in c), None)
    div_c = next((cols[c] for c in cols if "市場" in c or "区分" in c), None)
    if code_c is None:
        return []
    out, seen = [], set()
    for _, row in df.iterrows():
        tk = _jp_pick(row.get(code_c), row.get(div_c) if div_c is not None else "")
        if tk and tk not in seen:
            seen.add(tk)
            out.append(tk)
    return out


def _parse_hk(content: bytes) -> list[str]:
    """HKEX ListOfSecurities.xlsx → ['0700.HK', …] (Category=Equity 만)."""
    import pandas as pd
    raw = pd.read_excel(BytesIO(content), header=None)
    hdr = None                                    # 헤더 행 동적 탐지
    for i in range(min(12, len(raw))):
        if any("Stock Code" in str(v) for v in raw.iloc[i].tolist()):
            hdr = i
            break
    if hdr is None:
        return []
    df = pd.read_excel(BytesIO(content), header=hdr)
    cols = {str(c).strip(): c for c in df.columns}
    code_c = next((cols[c] for c in cols if "Stock Code" in c), None)
    cat_c = next((cols[c] for c in cols if c == "Category"), None)
    if code_c is None:
        return []
    out, seen = [], set()
    for _, row in df.iterrows():
        tk = _hk_pick(row.get(code_c), row.get(cat_c) if cat_c is not None else "Equity")
        if tk and tk not in seen:
            seen.add(tk)
            out.append(tk)
    return out


def _parse_jp_names(content: bytes) -> dict:
    """JPX data_j.xls → {'7203.T': 'トヨタ自動車', …} (보통주, 銘柄名 칼럼)."""
    import pandas as pd
    df = pd.read_excel(BytesIO(content))
    cols = {str(c): c for c in df.columns}
    code_c = next((cols[c] for c in cols if "コード" in c), None)
    name_c = next((cols[c] for c in cols if "銘柄名" in c), None)
    div_c = next((cols[c] for c in cols if "市場" in c or "区分" in c), None)
    if code_c is None or name_c is None:
        return {}
    out: dict = {}
    for _, row in df.iterrows():
        tk = _jp_pick(row.get(code_c), row.get(div_c) if div_c is not None else "")
        nm = str(row.get(name_c) or "").strip()
        if tk and nm and tk not in out:
            out[tk] = nm
    return out


def _parse_hk_names(content: bytes) -> dict:
    """HKEX ListOfSecurities → {'0700.HK': 'TENCENT', …} (Equity, Name 칼럼)."""
    import pandas as pd
    raw = pd.read_excel(BytesIO(content), header=None)
    hdr = None
    for i in range(min(12, len(raw))):
        if any("Stock Code" in str(v) for v in raw.iloc[i].tolist()):
            hdr = i
            break
    if hdr is None:
        return {}
    df = pd.read_excel(BytesIO(content), header=hdr)
    cols = {str(c).strip(): c for c in df.columns}
    code_c = next((cols[c] for c in cols if "Stock Code" in c), None)
    name_c = next((cols[c] for c in cols if "Name of Securities" in c), None)
    cat_c = next((cols[c] for c in cols if c == "Category"), None)
    if code_c is None or name_c is None:
        return {}
    out: dict = {}
    for _, row in df.iterrows():
        tk = _hk_pick(row.get(code_c), row.get(cat_c) if cat_c is not None else "Equity")
        nm = str(row.get(name_c) or "").strip()
        if tk and nm and tk not in out:
            out[tk] = nm
    return out


_SPEC = {"JP": (_JPX_URL, _parse_jp), "HK": (_HKEX_URL, _parse_hk)}
_NAME_SPEC = {"JP": (_JPX_URL, _parse_jp_names), "HK": (_HKEX_URL, _parse_hk_names)}


def full_universe_names(market: str) -> dict:
    """{ticker: native 銘柄名/Name} — JP/HK 공식 상장목록 종목명 (사용자 2026-06-14
    '소형주까지 한글명'). 네이버 worldstock 미커버 소형주의 티커 노출 해소용 —
    호출부가 Flash 번역. 7일 디스크 캐시. 실패/미지원 → {} (호출부 graceful)."""
    spec = _NAME_SPEC.get(market)
    if not spec:
        return {}
    try:
        from bot.finviz_client import _cache_write, _cached
    except Exception:
        _cached = _cache_write = None
    cache_name = f"full_universe_names_{market}.json"
    if _cached:
        c = _cached(cache_name, ttl=7 * 86400)
        if isinstance(c, dict) and len(c) > 100:
            return c
    url, parser = spec
    try:
        names = parser(_http_get(url))
    except Exception as exc:
        log.warning("full_universe_names %s 실패: %s", market, exc)
        return {}
    if len(names) > 100 and _cache_write:
        try:
            _cache_write(cache_name, names)
        except Exception:
            pass
    return names


def full_universe(market: str) -> list[str]:
    """JP/HK 전종목 보통주 티커. 7일 디스크 캐시. 실패/미지원 → [](호출측 peer 폴백)."""
    spec = _SPEC.get(market)
    if not spec:
        return []
    try:
        from bot.finviz_client import _cache_write, _cached
    except Exception:
        _cached = _cache_write = None
    cache_name = f"full_universe_{market}.json"
    if _cached:
        c = _cached(cache_name, ttl=7 * 86400)
        if isinstance(c, list) and len(c) > 100:
            return c
    url, parser = spec
    try:
        tickers = parser(_http_get(url))
    except Exception as exc:
        log.warning("full_universe %s fetch/parse 실패: %s", market, exc)
        return []
    if len(tickers) > 100 and _cache_write:
        try:
            _cache_write(cache_name, tickers)
        except Exception:
            pass
    return tickers
