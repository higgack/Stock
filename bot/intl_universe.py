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


# 원천 조회 실패 시 만료된 캐시로 버틴 시간(시장 → 시간). 화면·진단이 읽는다.
# 상장목록은 느리게 바뀌므로 9일 전 목록이 빈 목록보다 낫다 — 단 그 사실을
# 말한다(#43). 2026-09-09 VM 실측: JPX 가 21KB 를 주는데 xls 가 아니어서
# ("Excel file format cannot be determined") 7일 TTL 만료 뒤 JP 유니버스가
# 통째로 비었다 — 52주 신고저·Bollinger 보드가 같이 죽었다.
_STALE_HOURS: dict = {}

_XLS_MAGIC = (b"\xd0\xcf\x11\xe0", b"PK\x03\x04")   # OLE(xls) · zip(xlsx)


def stale_hours(market: str):
    """원천 조회가 실패해 만료 캐시로 서빙 중이면 그 나이(시간), 아니면 None."""
    return _STALE_HOURS.get(market)


def _http_get(url: str) -> bytes:
    """상장목록 파일 바이트. **엑셀 시그니처가 아니면 예외** — 상태·형식·길이·
    앞머리를 예외 문장에 싣는다. 옛 판은 상태를 안 보고 본문을 파서에 넘겨,
    403/차단 HTML 이 pandas 의 "format cannot be determined" 로만 남아 원인을
    한 라운드 늦게 알았다(#82 '없음'만 말하는 진단 · #109 원문 표본)."""
    import requests
    r = requests.get(url, timeout=30, headers=_UA)
    body = r.content or b""
    ctype = (r.headers.get("Content-Type") or "?").split(";")[0].strip()
    if r.status_code != 200 or not body.startswith(_XLS_MAGIC):
        head = body[:200].decode("utf-8", "replace").replace("\n", " ")
        raise RuntimeError(
            f"HTTP {r.status_code} {ctype} {len(body):,}바이트 — 엑셀 파일이 "
            f"아닙니다 · 앞머리: {head!r}")
    return body


def _stale_cache(cache_name: str, market: str, kind):
    """만료된 캐시라도 있으면 돌려주고 나이를 기록한다. 없으면 None."""
    try:
        from bot.finviz_client import _cached, cache_age_sec
        c = _cached(cache_name, ttl=float("inf"))
        age = cache_age_sec(cache_name)
    except Exception:
        return None
    if isinstance(c, kind) and len(c) > 100 and age is not None:
        _STALE_HOURS[market] = age / 3600.0
        log.warning("full_universe %s: 원천 실패 → %.0f시간 전 캐시로 서빙",
                    market, age / 3600.0)
        return c
    return None


def _jp_pick(code, div) -> str | None:
    """JPX 한 행 → 'NNNN.T' 또는 None (순수·테스트 가능). 4자 보통주 코드,
    ETF/REIT/출자증권 등 제외. ⚠️ 2024~ TSE 신규상장은 **영숫자 코드**(예 285A
    키옥시아 — 숫자3+영문1, 130A 등)라 옛 `isdigit` 필터가 이들을 통째 제외 →
    키옥시아(시총 1위·신고가)가 52주 보드 등 전 유니버스에서 누락됐음(사용자
    2026-06-19). 4자 ASCII 영숫자 허용(ETF/REIT 은 div=_JP_EXCL 로 이미 제외)."""
    if any(x in str(div or "") for x in _JP_EXCL):
        return None
    code = str(code or "").strip().split(".")[0].upper()
    return f"{code}.T" if len(code) == 4 and code.isalnum() and code.isascii() else None


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
    cache_name = f"full_universe_names_{market}_v2.json"   # v2: 영숫자 코드 종목명 포함(2026-06-19)
    if _cached:
        c = _cached(cache_name, ttl=7 * 86400)
        if isinstance(c, dict) and len(c) > 100:
            return c
    url, parser = spec
    try:
        names = parser(_http_get(url))
    except Exception as exc:
        log.warning("full_universe_names %s 실패: %s", market, exc)
        return _stale_cache(cache_name, f"names:{market}", dict) or {}
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
    # v2 (2026-06-19): 영숫자 코드(285A 등) 포함 — 옛 캐시(숫자-only)를 즉시
    # 무효화해 키옥시아 등 신규 대형주가 7일 TTL 기다림 없이 바로 유니버스 진입.
    cache_name = f"full_universe_{market}_v2.json"
    if _cached:
        c = _cached(cache_name, ttl=7 * 86400)
        if isinstance(c, list) and len(c) > 100:
            _STALE_HOURS.pop(market, None)
            return c
    url, parser = spec
    try:
        tickers = parser(_http_get(url))
    except Exception as exc:
        log.warning("full_universe %s fetch/parse 실패: %s", market, exc)
        return _stale_cache(cache_name, market, list) or []
    if len(tickers) > 100:
        _STALE_HOURS.pop(market, None)
    if len(tickers) > 100 and _cache_write:
        try:
            _cache_write(cache_name, tickers)
        except Exception:
            pass
    return tickers
