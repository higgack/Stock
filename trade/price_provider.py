"""provider-neutral 시세 레이어 — 무역 신호에 '관련 종목 가격'을 잇기 위한
READ-ONLY 다리. 주문/매매는 절대 안 함(봇 철학: 표시 전용·blast-radius 0).

설계 의도:
  - 산업트렌드/잠정 신호(반도체장비 capex 등) 옆에 매핑된 종목의 현재가·
    등락을 '표시'만 한다. 해석·매매는 운영자(도메인 전문가) 몫.
  - **공급자 중립**: 지금은 KIS(한국투자증권 Developers, 즉시 GA)로 구현.
    Toss OpenAPI가 정식 출시되면 같은 `get_quotes()` 인터페이스 뒤에서
    `_PROVIDERS`에 한 줄 추가해 갈아끼운다(상위 코드 무변경).
  - **기본 OFF**: env `TRADE_PRICE_PROVIDER`(기본 auto)는 KIS 키가 .env에
    있을 때만 호출, 없으면 'none' → 외부 호출 0·빈 결과. 키 추가 전까진
    완전 무해(렌더·알림 안 깨짐).

인증/호출(KIS, 국내주식 현재가):
  - 토큰: POST {domain}/oauth2/tokenP {grant_type:client_credentials,
    appkey, appsecret} → access_token(24h). KIS가 토큰 발급을 1/분으로
    제한하므로 ~/.trade/.kis_token.json에 캐시해 만료 전 재사용(필수).
  - 현재가: GET {domain}/uapi/domestic-stock/v1/quotations/inquire-price
    ?fid_cond_mrkt_div_code=J&fid_input_iscd={6자리코드}
    headers: authorization Bearer, appkey, appsecret, tr_id FHKST01010100,
    custtype P. output에서 stck_prpr(현재가)·stck_sdpr(전일종가)·
    hts_kor_isnm(종목명). 등락은 price-prev로 직접 산출(부호필드 의존 X).
  - 시세 캐시: ~/.trade/kis_quotes.json, 심볼별 TTL(기본 60s)로 5분 렌더가
    반복 호출 안 하게.

안전:
  - 키 없거나 어떤 예외든 빈 결과({}/None) — 호출자는 그냥 가격 줄을 안 그림.
  - transport 주입식이라 라이브 키 없이 단위 테스트 가능(관세청 모듈과 동일).
  - 비용 0(KIS 무료). 미국주식·주문 엔드포인트는 의도적으로 미구현.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
_TOKEN_PATH = _DATA_DIR / ".kis_token.json"
_QUOTE_CACHE = _DATA_DIR / "kis_quotes.json"
_QUOTE_TTL_S = int(os.environ.get("TRADE_PRICE_CACHE_TTL") or "60")
_HTTP_TIMEOUT_S = 10

KIS_DOMAIN = (os.environ.get("TRADE_KIS_DOMAIN")
              or "https://openapi.koreainvestment.com:9443")

# transport(method, url, *, headers, body) -> dict. 주입식(테스트).
Transport = Callable[..., dict]


@dataclass
class Quote:
    symbol: str
    name: str
    price: float
    change: float          # 현재가 − 전일종가
    change_pct: float      # %
    prev_close: float
    currency: str
    ts: str                # UTC ISO

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------
# transport (기본 실HTTP, 테스트는 주입)
# ---------------------------------------------------------------------

def _default_transport(method: str, url: str, *, headers: dict,
                       body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _provider() -> str:
    """현재 공급자. auto = KIS 키 있으면 kis, 없으면 none(호출 0)."""
    p = (os.environ.get("TRADE_PRICE_PROVIDER") or "auto").strip().lower()
    if p == "auto":
        return "kis" if (os.environ.get("TRADE_KIS_APPKEY")
                         and os.environ.get("TRADE_KIS_APPSECRET")) else "none"
    return p


def _fnum(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------
# KIS provider
# ---------------------------------------------------------------------

def _kis_token(*, transport: Transport = _default_transport) -> Optional[str]:
    """캐시된 access_token 반환(만료 60s 전이면 재사용), 없으면 발급."""
    try:
        d = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
        if d.get("access_token") and float(d.get("expires_at", 0)) > time.time() + 60:
            return d["access_token"]
    except Exception:
        pass
    appkey = os.environ.get("TRADE_KIS_APPKEY")
    appsecret = os.environ.get("TRADE_KIS_APPSECRET")
    if not appkey or not appsecret:
        return None
    try:
        resp = transport(
            "POST", f"{KIS_DOMAIN}/oauth2/tokenP",
            headers={"content-type": "application/json"},
            body={"grant_type": "client_credentials",
                  "appkey": appkey, "appsecret": appsecret},
        )
    except Exception:
        return None
    tok = resp.get("access_token")
    if not tok:
        return None
    # expires_in 누락/0이면 24h 기본(KIS는 토큰 발급을 1/분 제한 → 즉시-만료
    # 취급하면 매 호출 재발급으로 막힘). 괄호로 우선순위 고정.
    expires = time.time() + (_fnum(resp.get("expires_in")) or 86400.0)
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_PATH.write_text(
            json.dumps({"access_token": tok, "expires_at": expires}),
            encoding="utf-8")
    except OSError:
        pass
    return tok


def _kis_quote_kr(code: str, token: str, *,
                  transport: Transport = _default_transport) -> Optional[Quote]:
    """국내주식 현재가 1종목. 실패/빈 응답 → None."""
    appkey = os.environ.get("TRADE_KIS_APPKEY") or ""
    appsecret = os.environ.get("TRADE_KIS_APPSECRET") or ""
    code = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)[:6]
    url = (f"{KIS_DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-price"
           f"?fid_cond_mrkt_div_code=J&fid_input_iscd={code}")
    try:
        resp = transport("GET", url, headers={
            "authorization": f"Bearer {token}",
            "appkey": appkey, "appsecret": appsecret,
            "tr_id": "FHKST01010100", "custtype": "P",
        }, body=None)
    except Exception:
        return None
    out = resp.get("output") or {}
    if not out:
        return None
    price = _fnum(out.get("stck_prpr"))
    prev = _fnum(out.get("stck_sdpr"))
    change = price - prev
    # 등락률: 전일종가 있으면 직접 산출(부호필드 의존 X), 없으면 prdy_ctrt.
    change_pct = (change / prev * 100.0) if prev else _fnum(out.get("prdy_ctrt"))
    if price <= 0:
        return None
    return Quote(
        symbol=code,
        name=(out.get("hts_kor_isnm") or code).strip(),
        price=price, change=change, change_pct=change_pct,
        prev_close=prev, currency="KRW",
        ts=datetime.now(timezone.utc).isoformat(),
    )


def _kis_get_quotes(symbols: list[str], *,
                    transport: Transport = _default_transport) -> dict[str, Quote]:
    token = _kis_token(transport=transport)
    if not token:
        return {}
    out: dict[str, Quote] = {}
    for sym in symbols:
        q = _kis_quote_kr(sym, token, transport=transport)
        if q:
            out[q.symbol] = q
    return out


_PROVIDERS: dict[str, Callable[..., dict[str, Quote]]] = {
    "kis": _kis_get_quotes,
    # "toss": _toss_get_quotes,   # 출시 후 동일 시그니처로 추가
}


# ---------------------------------------------------------------------
# 시세 캐시 (심볼별 TTL)
# ---------------------------------------------------------------------

def _load_cache() -> dict:
    try:
        return json.loads(_QUOTE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _QUOTE_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                                encoding="utf-8")
    except OSError:
        pass


def _norm(symbols: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for s in symbols:
        c = "".join(ch for ch in str(s) if ch.isdigit()).zfill(6)[:6]
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def get_quotes(symbols: Iterable[str], *,
               transport: Transport = _default_transport,
               ttl_s: int = _QUOTE_TTL_S) -> dict[str, Quote]:
    """{6자리코드: Quote}. 공급자 off/키 없음/예외 → {} (호출자는 가격 줄 생략).

    심볼별 TTL 캐시로 5분 렌더가 KIS를 반복 호출하지 않게. 캐시 신선분은
    재사용, 만료/미수집분만 공급자에서 가져와 캐시 갱신."""
    codes = _norm(symbols)
    if not codes:
        return {}
    provider = _PROVIDERS.get(_provider())
    if provider is None:
        return {}
    now = time.time()
    cache = _load_cache()
    fresh: dict[str, Quote] = {}
    stale: list[str] = []
    for c in codes:
        rec = cache.get(c)
        if rec and (now - rec.get("_cached_at", 0)) < ttl_s:
            try:
                d = {k: v for k, v in rec.items() if k != "_cached_at"}
                fresh[c] = Quote(**d)
                continue
            except Exception:
                pass
        stale.append(c)
    if stale:
        try:
            got = provider(stale, transport=transport)
        except Exception:
            got = {}
        for c, q in got.items():
            fresh[c] = q
            cache[c] = {**q.as_dict(), "_cached_at": now}
        if got:
            _save_cache(cache)
    return fresh


def get_quote(symbol: str, *,
              transport: Transport = _default_transport) -> Optional[Quote]:
    """단일 종목 편의 래퍼. 없으면 None."""
    codes = _norm([symbol])
    if not codes:
        return None
    return get_quotes(codes, transport=transport).get(codes[0])


def provider_active() -> bool:
    """현재 시세 공급자가 켜져 있는지(키 설정 여부). UI가 '가격 미설정' 안내용."""
    return _provider() in _PROVIDERS
