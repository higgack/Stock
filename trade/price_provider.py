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
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
_TOKEN_PATH = _DATA_DIR / ".kis_token.json"
_QUOTE_CACHE = _DATA_DIR / "kis_quotes.json"
_CODE_CACHE = _DATA_DIR / "stock_codes.json"     # 종목명→코드 durable 캐시
_QUOTE_TTL_S = int(os.environ.get("TRADE_PRICE_CACHE_TTL") or "60")
_CODE_TTL_S = int(os.environ.get("TRADE_STOCK_CODE_TTL") or str(7 * 86400))  # 7일
_MAX_SYMBOLS = int(os.environ.get("TRADE_PRICE_MAX_SYMBOLS") or "300")
_HTTP_TIMEOUT_S = 10
_KST = timezone(timedelta(hours=9))

KIS_DOMAIN = (os.environ.get("TRADE_KIS_DOMAIN")
              or "https://openapi.koreainvestment.com:9443")

# 금융위원회_주식시세정보(data.go.kr 15094808) — 종목명/코드로 EOD 종가·등락률.
# 운영자의 기존 TRADE_DATA_GO_KR_KEY로 호출(잠정치·관세청과 동일 키).
DATAPORTAL_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/"
    "GetStockSecuritiesInfoService/getStockPriceInfo")

# transport(method, url, *, headers, body) -> dict. 주입식(테스트).
Transport = Callable[..., dict]


def _kis_keys() -> tuple[str, str]:
    """(appkey, appsecret) — TRADE_KIS_APPKEY/APPSECRET 우선, 없으면 NOAH
    네이밍(KIS_APP_KEY/KIS_APP_SECRET)로 폴백. NOAH stock-bot와 같은 호스트
    에서 한 KIS 앱을 공유하면 .env 중복 없이 한 쌍이면 됨. 둘 중 어느 쪽이든
    appkey와 secret이 모두 있어야 유효."""
    ak = (os.environ.get("TRADE_KIS_APPKEY")
          or os.environ.get("KIS_APP_KEY") or "").strip()
    sk = (os.environ.get("TRADE_KIS_APPSECRET")
          or os.environ.get("KIS_APP_SECRET") or "").strip()
    return ak, sk


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
    """현재 공급자. auto = KIS 키 있으면 kis(실시간 우선 — data.go.kr EOD는
    T+1 지연이라 너무 느림), 아니면 data.go.kr 키 있으면 dataportal(EOD),
    둘 다 없으면 none(외부 호출 0). KIS는 코드 조회만 되지만 이름→코드는
    data.go.kr durable 캐시로 푸는 하이브리드(resolve_codes)."""
    p = (os.environ.get("TRADE_PRICE_PROVIDER") or "auto").strip().lower()
    if p == "auto":
        ak, sk = _kis_keys()
        if ak and sk:
            return "kis"
        if os.environ.get("TRADE_DATA_GO_KR_KEY"):
            return "dataportal"
        return "none"
    return p


def _fnum(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def recommended_ttl(now: Optional[datetime] = None) -> int:
    """장중(평일 09:00–15:30 KST)이면 짧게(라이브 폴링·기본 90s), 그 외(마감
    후·주말)엔 길게(종가 고정·기본 6h). KIS 실시간 폴링이 장 마감 후엔
    재호출 0이 되게 해 콜 수를 자동 절감. dataportal(EOD)에도 무해."""
    now = now or datetime.now(_KST)
    live = int(os.environ.get("TRADE_PRICE_LIVE_TTL") or "90")
    eod = int(os.environ.get("TRADE_PRICE_EOD_TTL") or "21600")
    if now.weekday() >= 5:                 # 토/일
        return eod
    mins = now.hour * 60 + now.minute
    return live if (9 * 60 <= mins <= 15 * 60 + 30) else eod


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
    appkey, appsecret = _kis_keys()
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
    appkey, appsecret = _kis_keys()
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
    # KIS 실전 초당 ~20건 제한 → 호출 간 간격(기본 60ms, <17/s)으로 안전.
    throttle = _fnum(os.environ.get("TRADE_KIS_THROTTLE_MS") or "60") / 1000.0
    out: dict[str, Quote] = {}
    for i, sym in enumerate(symbols):
        if i and throttle > 0:
            time.sleep(throttle)
        q = _kis_quote_kr(sym, token, transport=transport)
        if q:
            out[q.symbol] = q
    return out


# ---------------------------------------------------------------------
# data.go.kr provider (금융위 주식시세정보) — EOD, 기존 키 재사용
# ---------------------------------------------------------------------

def _items_list(resp: dict) -> list[dict]:
    """data.go.kr body.items.item을 항상 list로. dict(1건)·list(N)·없음 모두."""
    try:
        item = ((resp.get("response") or resp).get("body") or {}).get("items")
    except AttributeError:
        return []
    if not item:
        return []
    item = item.get("item") if isinstance(item, dict) else item
    if item is None:
        return []
    return item if isinstance(item, list) else [item]


def _quote_from_dp_item(it: dict) -> Optional[Quote]:
    """주식시세정보 item → Quote. clpr(종가)·vs(대비)·fltRt(등락률)·srtnCd."""
    code = "".join(ch for ch in str(it.get("srtnCd") or "") if ch.isdigit())
    code = code.zfill(6)[:6] if code else ""
    price = _fnum(it.get("clpr"))
    if not code or price <= 0:
        return None
    change = _fnum(it.get("vs"))                 # 대비(부호 포함)
    change_pct = _fnum(it.get("fltRt"))          # 등락률(부호 포함)
    return Quote(
        symbol=code,
        name=(it.get("itmsNm") or code).strip(),
        price=price, change=change, change_pct=change_pct,
        prev_close=price - change, currency="KRW",
        ts=datetime.now(timezone.utc).isoformat(),
    )


def _dp_fetch(param_key: str, value: str, *,
              transport: Transport = _default_transport) -> list[dict]:
    """주식시세정보 1쿼리(최근 ~10영업일 창) → item 리스트. 키 없으면 []."""
    key = os.environ.get("TRADE_DATA_GO_KR_KEY")
    if not key:
        return []
    begin = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y%m%d")
    qs = urllib.parse.urlencode({
        "serviceKey": key, "resultType": "json",
        "numOfRows": 50, "pageNo": 1,
        "beginBasDt": begin, param_key: value,
    })
    try:
        resp = transport("GET", f"{DATAPORTAL_ENDPOINT}?{qs}",
                         headers={"User-Agent": "trade-bot/1.0"}, body=None)
    except Exception:
        return []
    return _items_list(resp)


def _dp_pick(items: list[dict], *, exact_name: Optional[str] = None,
             code: Optional[str] = None) -> Optional[Quote]:
    """후보 item들에서 1건 선택: 이름 정확일치(우선주 오매칭 방지) 또는 코드,
    그중 최신 basDt. 정확 매칭 없으면 None(틀린 종목 붙이느니 생략)."""
    def _norm_nm(s):
        return (s or "").replace(" ", "")
    cand = []
    for it in items:
        if exact_name is not None and _norm_nm(it.get("itmsNm")) != _norm_nm(exact_name):
            continue
        if code is not None:
            c = "".join(ch for ch in str(it.get("srtnCd") or "") if ch.isdigit()).zfill(6)[:6]
            if c != code:
                continue
        cand.append(it)
    if not cand:
        return None
    cand.sort(key=lambda it: str(it.get("basDt") or ""), reverse=True)  # 최신 영업일
    return _quote_from_dp_item(cand[0])


def _dataportal_get_quotes(symbols: list[str], *,
                           transport: Transport = _default_transport) -> dict[str, Quote]:
    """코드(6자리) 기준 조회 — likeSrtnCd로 가져와 코드 정확일치 + 최신일."""
    out: dict[str, Quote] = {}
    for code in symbols:
        q = _dp_pick(_dp_fetch("likeSrtnCd", code, transport=transport), code=code)
        if q:
            out[q.symbol] = q
    return out


def _dataportal_get_quotes_by_name(names: list[str], *,
                                   transport: Transport = _default_transport
                                   ) -> dict[str, Quote]:
    """종목명 기준 조회 — likeItmsNm로 가져와 이름 정확일치(우선주 제외) + 최신일.
    BeOn 관련종목(회사명)을 코드 변환 없이 바로 시세에 연결하는 경로."""
    out: dict[str, Quote] = {}
    for nm in names:
        nm = (nm or "").strip()
        if not nm:
            continue
        q = _dp_pick(_dp_fetch("likeItmsNm", nm, transport=transport), exact_name=nm)
        if q:
            out[nm] = q                          # 이름 키(BeOn 매칭용)
    return out


def resolve_codes(names: Iterable[str], *,
                  transport: Transport = _default_transport,
                  fetch: bool = True) -> dict[str, str]:
    """{종목명: 6자리코드} — data.go.kr 종목명→단축코드. KIS처럼 코드 조회만
    되는 공급자가 BeOn 회사명을 쓰게 하는 다리. 코드는 거의 안 변하므로
    durable 캐시(_CODE_CACHE, 기본 7일). 정확일치만(우선주 오매칭 방지),
    미일치는 음성 캐시(1일). fetch=False면 외부 호출 없이 캐시만 반환(렌더용).
    DATA_GO_KR_KEY 없으면 {}."""
    uniq = [n.strip() for n in names if n and str(n).strip()]
    if not uniq or not os.environ.get("TRADE_DATA_GO_KR_KEY"):
        return {}
    now = time.time()
    # 양성(코드 확정)은 7일, 음성(미일치)은 1일 — 비상장/표기불일치 종목의 매
    # 렌더 재조회를 막되, 일시적 API 오류로 막힌 이름은 하루 안에 self-heal.
    neg_ttl = int(os.environ.get("TRADE_STOCK_CODE_NEG_TTL") or "86400")
    try:
        cache = json.loads(_CODE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    # 리졸버 스키마 버전 불일치(=alias/직접코드 매핑 갱신) → 캐시 무효화.
    # 옛 음성 캐시가 새 매핑을 가려서 종목이 영원히 안 뜨던 회귀 방지.
    if cache.get("_v") != _RESOLVER_VERSION:
        cache = {"_v": _RESOLVER_VERSION}
    out: dict[str, str] = {}
    dirty = False
    for n in uniq:
        # 1) 직접 코드 매핑 — data.go.kr 미지원 종목 즉시(외부 호출 0).
        if n in _DIRECT_CODES:
            out[n] = _DIRECT_CODES[n]
            continue
        # 2) 캐시 — 양성/음성 TTL 적용
        rec = cache.get(n)
        if rec:
            ttl = _CODE_TTL_S if rec.get("code") else neg_ttl
            if (now - rec.get("_cached_at", 0)) < ttl:
                if rec.get("code"):
                    out[n] = rec["code"]
                continue
        if not fetch:                          # 캐시 전용(렌더): 외부 호출 안 함
            continue
        # 3) data.go.kr 조회 — alias가 있으면 KRX 표기로 변환해 질의.
        query = _NAME_ALIASES.get(n, n)
        q = _dp_pick(_dp_fetch("likeItmsNm", query, transport=transport),
                     exact_name=query)
        if q:
            out[n] = q.symbol
            cache[n] = {"code": q.symbol, "_cached_at": now}
        else:
            cache[n] = {"code": "", "_cached_at": now}      # 음성 캐시(짧은 TTL)
        dirty = True
    if dirty:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _CODE_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                                   encoding="utf-8")
        except OSError:
            pass
    return out


_PROVIDERS: dict[str, Callable[..., dict[str, Quote]]] = {
    "kis": _kis_get_quotes,
    "dataportal": _dataportal_get_quotes,
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


def split_names(stocks: Iterable[str]) -> list[str]:
    """BeOn 관련종목 문자열들 → 개별 종목명(중복 제거, 순서 유지).

    BeOn stocks 필드엔 가끔 제품 설명 프로즈가 섞인다(예: '보툴리눔 톡신
    (강원 횡성_중국) … 90%이상'). 종목명만 추리도록:
      · '관련종목 :' 접두사 제거
      · '/' '·' ',' 로 분리
      · 후행 '등' 제거
      · 공백 다수 토큰은 모두 종목스러우면(괄호·%·치수 없음) 각각 등록
        (예: '삼아알미늄 DI동일 동원시스템즈'); 마침표 포함(JYP Ent.)은 통째로
      · _looks_like_stock 통과한 것만 — 구조적 garbage(괄호·%·+·치수·kVA·
        3자리+숫자) 제거.
    """
    out, seen = [], set()

    def _add(name: str) -> None:
        name = name.strip()
        if name.endswith(" 등"):
            name = name[:-2].strip()
        if name and name not in seen and _looks_like_stock(name):
            seen.add(name)
            out.append(name)

    for s in stocks or []:
        s = _PARSER_PREFIX_RE.sub("", str(s)).strip()
        for part in _PRIMARY_SPLIT_RE.split(s):
            p = part.strip()
            if p.endswith(" 등"):
                p = p[:-2].strip()
            toks = p.split()
            if (len(toks) >= 2 and not any("." in t for t in toks)
                    and all(_looks_like_stock(t) for t in toks)):
                for t in toks:
                    _add(t)
            else:
                _add(p)
    return out


# 파서 누수 ('관련종목 : LG전자') 접두사
_PARSER_PREFIX_RE = re.compile(r"^관련종목\s*:\s*")
# 1차 분리자: 슬래시·중점(·)·콤마 — 한국 본문에서 종목 나열에 자주 쓰임
_PRIMARY_SPLIT_RE = re.compile(r"[/·,]")
# 종목 후보에서 제외할 구조적 마커(제품설명·치수·괄호 등 프로즈 조각).
_STRUCT_REJECT = re.compile(r'[()%+"」「\[\]]|\d{3,}|kVA')


def _looks_like_stock(t: str) -> bool:
    """프로즈 조각(제품 설명·치수·괄호 등)을 종목 후보에서 제외. 구조적
    마커(괄호·%·+·따옴표·대괄호·3자리+ 연속숫자·kVA)나 길이 이탈(2~14자 밖)
    이면 종목명이 아닌 것으로 본다. 통과해도 data.go.kr에서 안 잡히면 어차피
    음성 캐시 — 여기선 명백한 garbage만 거르는 게 목적."""
    t = (t or "").strip()
    if not (2 <= len(t) <= 14):
        return False
    return not _STRUCT_REJECT.search(t)


# ---------------------------------------------------------------------
# Resolver — 표기 alias + 직접 코드 매핑
# ---------------------------------------------------------------------

# data.go.kr의 KRX 정식 표기와 운영자 데이터(BeOn)의 표기 차이 보정.
# 'BeOn 표기 → KRX 표기'로 적되, data.go.kr 응답을 따른다.
_NAME_ALIASES: dict[str, str] = {
    "Sk하이닉스": "SK하이닉스",
    "HD현대미포조선": "HD현대미포",
    "한국타이어": "한국타이어앤테크놀로지",
    "코오롱인더스트리": "코오롱인더",
    "롯데칠성음료": "롯데칠성",
    "금호석유": "금호석유화학",
    "한국수출포장공업": "한국수출포장",
    "코스맥스NBT": "코스맥스엔비티",
    "제이엔케이히터": "JNK히터",
    "조광ILI": "조광아이엘아이",
}

# KRX엔 상장돼 있지만 data.go.kr likeItmsNm으로 안 잡히는 종목은 코드를
# 직접 매핑(외부 호출 없이 즉시 KIS로). 이름은 BeOn 표기 기준.
_DIRECT_CODES: dict[str, str] = {
    "HD현대건설기계": "267270",
    "LIG넥스원": "079550",
    "휴켐스": "069260",
    "효성첨단소재": "298050",
    "라온테크": "232680",
    "와이아이케이": "232140",
    "네온테크": "306620",
    "무림제지": "009200",
    "세하": "027970",
    "백광산업": "001340",
    "코오롱플라스틱": "138490",
    "현대에너지솔루션": "322000",
    "제이오": "418550",
    "나노신소재": "121600",
    # alias 질의가 불안정한 종목은 코드 직접(data.go.kr 표기 의존 X)
    "HD현대미포조선": "010620",   # KRX: HD현대미포
    "조광ILI": "044060",          # 조광아이엘아이
    "제이엔케이히터": "126880",   # JNK히터
}

# 코드 캐시 스키마 버전 — 매핑/분리 로직이 바뀌면 bump → 기존 캐시(특히 음성
# 캐시) 자동 무효화 → 새 매핑이 즉시 효과. 운영자가 캐시 파일 삭제 불필요.
_RESOLVER_VERSION = 3


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
               ttl_s: int = _QUOTE_TTL_S, fetch: bool = True) -> dict[str, Quote]:
    """{6자리코드: Quote}. 공급자 off/키 없음/예외 → {} (호출자는 가격 줄 생략).

    심볼별 TTL 캐시. fetch=False면 캐시 신선분만 반환하고 외부 호출 안 함
    (렌더는 이걸로 즉시 끝내고, 실제 API는 워머 fetch_quotes가 백그라운드에서)."""
    codes = _norm(symbols)[:_MAX_SYMBOLS]
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
    if stale and fetch:
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


def get_quotes_by_name(names: Iterable[str], *,
                       transport: Transport = _default_transport,
                       ttl_s: int = _QUOTE_TTL_S,
                       fetch: bool = True) -> dict[str, Quote]:
    """{종목명: Quote} — BeOn 관련종목(회사명)을 코드 변환 없이 바로 시세에 연결.

    이름 정확일치(우선주 오매칭 방지)로만 붙이고, 못 찾으면 그 이름은 생략(틀린
    종목 안 붙임). fetch=False면 캐시만 읽고 외부 호출 0(렌더는 이걸로 즉시
    끝냄 — 실제 API는 워머 fetch_quotes가 백그라운드에서 채움)."""
    uniq = []
    seen = set()
    for n in names:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    uniq = uniq[:_MAX_SYMBOLS]              # 콜 폭주 방지 상한
    prov = _provider()
    if not uniq or prov == "none":
        return {}

    # KIS 등 코드-기반 공급자: data.go.kr로 이름→코드(durable 캐시) 변환 후
    # 활성 공급자로 시세. 종목명 검색이 없는 KIS가 BeOn 회사명을 쓰는 경로.
    if prov != "dataportal":
        codes_by_name = resolve_codes(uniq, transport=transport, fetch=fetch)
        if not codes_by_name:
            return {}
        code_q = get_quotes(sorted(set(codes_by_name.values())),
                            transport=transport, ttl_s=ttl_s, fetch=fetch)
        return {n: code_q[c] for n, c in codes_by_name.items() if c in code_q}

    # dataportal: 이름 질의 한 번에 코드+종가(빠른 경로). 이름별 TTL 캐시.
    now = time.time()
    cache = _load_cache()
    fresh: dict[str, Quote] = {}
    stale: list[str] = []
    for n in uniq:
        rec = cache.get("nm:" + n)
        if rec and (now - rec.get("_cached_at", 0)) < ttl_s:
            try:
                d = {k: v for k, v in rec.items() if k != "_cached_at"}
                fresh[n] = Quote(**d)
                continue
            except Exception:
                pass
        stale.append(n)
    if stale and fetch:
        try:
            got = _dataportal_get_quotes_by_name(stale, transport=transport)
        except Exception:
            got = {}
        for n, q in got.items():
            fresh[n] = q
            cache["nm:" + n] = {**q.as_dict(), "_cached_at": now}
        if got:
            _save_cache(cache)
    return fresh


def provider_active() -> bool:
    """현재 시세 공급자가 켜져 있는지(키 설정 여부). UI가 '가격 미설정' 안내용."""
    return _provider() in _PROVIDERS
