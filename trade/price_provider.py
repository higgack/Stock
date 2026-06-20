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
  - 시세 캐시: ~/.trade/kis_quotes.json, 심볼별 TTL(미지정시 recommended_ttl:
    장중 90s/마감·주말 6h, 렌더와 동일)로 5분 렌더가 반복 호출 안 하게.

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
_KRX_MASTER = _DATA_DIR / "krx_codes.json"       # KRX 상장 전종목 이름→코드(로컬 마스터)
_OVERRIDES_PATH = _DATA_DIR / "stock_overrides.json"  # 운영자 /map 즉석 등록(코드보다 우선)
_CODE_TTL_S = int(os.environ.get("TRADE_STOCK_CODE_TTL") or str(7 * 86400))  # 7일
_MAX_SYMBOLS = int(os.environ.get("TRADE_PRICE_MAX_SYMBOLS") or "300")
_HTTP_TIMEOUT_S = 10
_KST = timezone(timedelta(hours=9))

# KIS 토큰 강제 재발급(self-heal) 쿨다운 — 죽은 KIS 토큰 엔드포인트에 매 청크
# 재발급 POST를 쏘지 않게(KIS 토큰 발급 1/분 제한 미러). 성공한 재발급은 캐시에
# 새 토큰을 써서 다음 호출이 그걸 읽으므로 자연 종료된다.
_KIS_REISSUE_COOLDOWN_S = 60.0
_KIS_REISSUE_COOLDOWN_UNTIL = 0.0

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
    ts: str                # UTC ISO (조회 시각)
    source: str = "kis"    # "kis"(현재가/실시간) | "eod"(data.go.kr 종가)
    as_of: str = ""        # EOD 기준영업일(YYYYMMDD). KIS(현재가)는 "". 기본값이라
                           #   source/as_of 없는 구캐시도 Quote(**d)로 그대로 로드됨.

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


def _atomic_write_json(path: Path, obj) -> None:
    """tmp+replace 원자 쓰기 — bot(/map)·워머·렌더가 프로세스를 달리해 같은 JSON을
    읽고 쓰므로, plain write_text의 부분쓰기(torn read → 파싱실패 → 캐시 전체 무시)
    노출을 막는다. build_krx_codes의 확립 패턴과 동일."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _provider() -> str:
    """현재 공급자. auto = KIS 키 있으면 kis(실시간 우선 — data.go.kr EOD는
    T+1 지연이라 너무 느림), 아니면 data.go.kr 키 있으면 dataportal(EOD),
    둘 다 없으면 none(외부 호출 0). KIS는 코드 조회만 되지만 이름→코드는
    data.go.kr durable 캐시로 푸는 하이브리드(resolve_codes)."""
    p = (os.environ.get("TRADE_PRICE_PROVIDER") or "auto").strip().lower()
    if p == "auto":
        # 사용자 2026-06-15 'KIS 말고 네이버' — 네이버 폴링은 무키·실시간·데이터센터
        # IP 차단 없음. 장중 라이브, 장후 당일종가(+recommended_ttl 마감 6h=조정).
        # KIS/dataportal 은 TRADE_PRICE_PROVIDER 로 명시 시에만.
        return "naver"
    return p


def _fnum(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def recommended_ttl(now: Optional[datetime] = None) -> int:
    """시세 캐시/렌더 표시 기준 TTL. 장중(평일 09:00–15:30 KST) 기본 30분(1800s),
    그 외(마감 후·주말) 6h. 대시보드 HTML은 5분마다 정적 재생성이라 '초 단위
    라이브'는 환상 — 짧은 TTL(옛 90s)은 워머가 매 틱 전 종목을 재워밍하게 만들어
    저빈도 꼬리가 60초 예산에 잘려 영구히 안 뜨던 함정의 원인이었다. 30분이면
    워머가 라운드로빈으로 한 바퀴 도는 동안 시세가 안 빠져 전 종목이 표시된다
    (체감 동일: 어차피 5분 정적). TRADE_PRICE_LIVE_TTL로 조정 가능."""
    now = now or datetime.now(_KST)
    live = int(os.environ.get("TRADE_PRICE_LIVE_TTL") or "1800")
    eod = int(os.environ.get("TRADE_PRICE_EOD_TTL") or "21600")
    if now.weekday() >= 5:                 # 토/일
        return eod
    mins = now.hour * 60 + now.minute
    return live if (9 * 60 <= mins <= 15 * 60 + 30) else eod


def _default_quote_ttl() -> int:
    """ttl_s 미지정시 쓰는 시세 캐시 기본값. TRADE_PRICE_CACHE_TTL이 명시되면 그
    값(옛 호환), 아니면 recommended_ttl()(렌더 dashboard와 동일 기준). 옛 60s 고정
    기본이 렌더(recommended_ttl)와 달라, 순진하게 get_quotes_by_name(fetch=False)을
    부르면 60초 넘은 캐시를 다 버려 '시세 없음'으로 오도하던 함정을 제거한다."""
    env = os.environ.get("TRADE_PRICE_CACHE_TTL")
    return int(env) if env else recommended_ttl()


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
        _atomic_write_json(_TOKEN_PATH, {"access_token": tok,
                                         "expires_at": expires})
    except OSError:
        pass
    return tok


def _invalidate_token() -> None:
    """캐시된 KIS 액세스 토큰 폐기. 같은 KIS 앱을 공유하는 다른 봇(NOAH)이
    토큰을 재발급하면 우리 토큰이 서버에서 무효화되는데, expires_at만 믿고
    최대 24h 죽은 토큰을 재사용하던 회귀를 끊는다 — 다음 _kis_token()이 새로 발급."""
    try:
        _TOKEN_PATH.unlink()
    except OSError:
        pass


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


def _kis_quote_batch(symbols: list[str], token: str, *,
                     transport: Transport = _default_transport) -> dict[str, Quote]:
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


def _kis_get_quotes(symbols: list[str], *,
                    transport: Transport = _default_transport) -> dict[str, Quote]:
    global _KIS_REISSUE_COOLDOWN_UNTIL
    token = _kis_token(transport=transport)
    if not token:
        return {}
    out = _kis_quote_batch(symbols, token, transport=transport)
    # self-heal: 토큰이 (만료 전이라) 캐시에 살아있는데 배치가 전건 0이면 = 서버
    # 측에서 무효화됐을 강한 신호(공유앱이 새 토큰 발급 등). 캐시 토큰을 폐기하고
    # 즉시 1회 재발급+재시도 — 죽은 토큰을 expires_at까지 재사용하며 시세가 통째로
    # 사라지던 회귀를 끊는다. 쿨다운으로 죽은 엔드포인트에 매 청크 thrash 방지.
    if (not out and symbols
            and time.time() >= _KIS_REISSUE_COOLDOWN_UNTIL):
        _KIS_REISSUE_COOLDOWN_UNTIL = time.time() + _KIS_REISSUE_COOLDOWN_S
        _invalidate_token()
        token = _kis_token(transport=transport)
        if token:
            out = _kis_quote_batch(symbols, token, transport=transport)
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
        source="eod", as_of=str(it.get("basDt") or ""),
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


_krx_master_memo: dict = {"key": None, "data": {}}


def _load_krx_master() -> dict[str, str]:
    """{공백제거 정규화 이름: 코드} — 로컬 KRX 상장 마스터(build_krx_codes 생성).
    없거나 깨졌으면 {}. (경로, mtime) 기준 메모이즈 — 파서/reparse가 행마다 불러도
    파일이 바뀔 때만 다시 읽음(테스트가 temp 경로 바꿔도 키에 경로 포함이라 안전)."""
    try:
        key = (str(_KRX_MASTER), _KRX_MASTER.stat().st_mtime)
    except OSError:
        return {}
    if _krx_master_memo["key"] != key:
        try:
            raw = json.loads(_KRX_MASTER.read_text(encoding="utf-8"))
            data = ({str(k).replace(" ", ""): v for k, v in raw.items() if v}
                    if isinstance(raw, dict) else {})
        except Exception:
            data = {}
        _krx_master_memo.update(key=key, data=data)
    return _krx_master_memo["data"]


_overrides_memo: dict = {"key": None, "data": {}}


def _load_overrides() -> dict[str, str]:
    """{BeOn표기(strip): 6자리코드} — 운영자가 /map으로 즉석 등록한 런타임
    오버라이드. resolve_codes에서 _DIRECT_CODES보다 먼저 조회돼 커밋·배포 없이
    즉시 효과. (경로·mtime·size) 메모이즈 — 렌더·워머가 매번 불러도 파일이 바뀔
    때만 다시 읽음."""
    try:
        st = _OVERRIDES_PATH.stat()
        key = (str(_OVERRIDES_PATH), st.st_mtime, st.st_size)
    except OSError:
        _overrides_memo.update(key=None, data={})
        return {}
    if _overrides_memo["key"] != key:
        try:
            raw = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
            data = ({str(k).strip(): str(v).strip() for k, v in raw.items()
                     if str(v).strip()} if isinstance(raw, dict) else {})
        except Exception:
            data = {}
        _overrides_memo.update(key=key, data=data)
    return _overrides_memo["data"]


def list_overrides() -> dict[str, str]:
    """현재 오버라이드 전체(파일 직접 읽기 — /map 목록용)."""
    try:
        d = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_overrides(data: dict) -> None:
    _atomic_write_json(_OVERRIDES_PATH, data)


def krx_name_for(code: str) -> str:
    """6자리 코드 → KRX 종목명(공백제거 표기) 역조회. 로컬 마스터만 사용(외부콜 0).
    /map의 오등록 검증용 — KIS inquire-price가 종목명(hts_kor_isnm)을 안 주는
    환경에서 운영자가 '맞는 회사인지' 확인할 수단. 마스터에 없으면 ""."""
    c = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)[:6]
    if not c:
        return ""
    for nm, v in _load_krx_master().items():
        if v == c:
            return nm
    return ""


def set_override(name: str, code: str) -> None:
    """/map — BeOn표기→6자리코드 오버라이드 저장(원자적). name은 strip만(공백
    포함 표기 허용)."""
    name = (name or "").strip()
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if not name or len(code) != 6:
        return
    data = list_overrides()
    data[name] = code
    _write_overrides(data)


def remove_override(name: str) -> bool:
    """/unmap — 오버라이드 제거. 있었으면 True."""
    name = (name or "").strip()
    data = list_overrides()
    if name in data:
        del data[name]
        _write_overrides(data)
        return True
    return False


def fetch_krx_master(*, transport: Transport = _default_transport,
                     days: int = 10, num_rows: int = 1000,
                     max_pages: int = 60) -> dict[str, str]:
    """data.go.kr 주식시세정보를 이름필터 없이 전수 페이지네이션 → {종목명: 코드}.
    최근 days일 창의 전 종목을 코드 기준 dedup(이름은 최신 basDt 우선)해 KRX 상장
    이름→코드 마스터를 만든다(빌더 build_krx_codes용). 키 없으면 {}.

    data.go.kr의 pageNo-무시 버그 방어: 페이지 시그니처가 직전과 같으면 중단. 새
    코드가 더 안 늘면 중단. 기존 키(getStockPriceInfo)를 그대로 써 별도 승인 불필요."""
    key = os.environ.get("TRADE_DATA_GO_KR_KEY")
    if not key:
        return {}
    begin = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
    best: dict[str, tuple[str, str]] = {}      # code -> (name, basDt)
    seen_sigs: set = set()
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode({
            "serviceKey": key, "resultType": "json",
            "numOfRows": num_rows, "pageNo": page, "beginBasDt": begin,
        })
        try:
            resp = transport("GET", f"{DATAPORTAL_ENDPOINT}?{qs}",
                             headers={"User-Agent": "trade-bot/1.0"}, body=None)
        except Exception:
            break
        items = _items_list(resp)
        if not items:
            break
        sig = tuple(sorted((str(it.get("srtnCd")), str(it.get("basDt"))) for it in items))
        if sig in seen_sigs:                   # pageNo 무시 → 같은 페이지 반복 → 중단
            break
        seen_sigs.add(sig)
        added = 0
        for it in items:
            code = "".join(ch for ch in str(it.get("srtnCd") or "") if ch.isdigit()).zfill(6)[:6]
            name = (it.get("itmsNm") or "").strip()
            basdt = str(it.get("basDt") or "")
            if not code.strip("0") or not name:
                continue
            prev = best.get(code)
            if prev is None:
                added += 1
                best[code] = (name, basdt)
            elif basdt > prev[1]:              # 최신 영업일의 이름 우선
                best[code] = (name, basdt)
        if added == 0 and page > 1:            # 새 코드 없음 → 끝까지 봄
            break
    return {name: code for code, (name, _b) in best.items()}


def resolve_codes(names: Iterable[str], *,
                  transport: Transport = _default_transport,
                  fetch: bool = True) -> dict[str, str]:
    """{종목명: 6자리코드} — 코드 조회만 되는 공급자(KIS)가 BeOn 회사명을 쓰게 하는
    다리. 조회 순서: 운영자 오버라이드(/map) → 직접코드 → KRX 로컬 마스터(전 상장
    종목) → durable 캐시 → data.go.kr 정확일치(미스 폴백). 마스터 덕에 렌더
    (fetch=False)도 외부 호출 없이 대부분 해석 → 커버리지 즉시 상승. 오버라이드·
    마스터·키 다 없으면 {}."""
    uniq = [n.strip() for n in names if n and str(n).strip()]
    if not uniq:
        return {}
    overrides = _load_overrides()
    master = _load_krx_master()
    has_key = bool(os.environ.get("TRADE_DATA_GO_KR_KEY"))
    if not overrides and not master and not has_key:   # 해석 수단 전무 → {}
        return {}
    now = time.time()
    # 양성(코드 확정)은 7일, 음성(미일치)은 1일 — 비상장/표기불일치 종목의 매
    # 렌더 재조회를 막되, 일시적 API 오류로 막힌 이름은 하루 안에 self-heal.
    neg_ttl = int(os.environ.get("TRADE_STOCK_CODE_NEG_TTL") or "86400")
    try:
        cache = json.loads(_CODE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    # 리졸버 스키마 버전 불일치(=alias/직접코드/마스터 도입) → 캐시 무효화.
    # 옛 음성 캐시가 새 매핑을 가려서 종목이 영원히 안 뜨던 회귀 방지.
    if cache.get("_v") != _RESOLVER_VERSION:
        cache = {"_v": _RESOLVER_VERSION}
    out: dict[str, str] = {}
    dirty = False
    for n in uniq:
        # 0) 운영자 /map 런타임 오버라이드 — 최우선(커밋·배포 없이 즉시 효과).
        if n in overrides:
            out[n] = overrides[n]
            continue
        # 1) 직접 코드 매핑 — 즉시(외부 호출 0).
        if n in _DIRECT_CODES:
            out[n] = _DIRECT_CODES[n]
            continue
        # 2) KRX 로컬 마스터(공백제거 정규화 + alias) — 전 상장종목 로컬 즉시 해석.
        #    data.go.kr 정확일치 검색의 지연·누락·음성캐시를 우회. 렌더도 여기서 해석.
        m = (master.get(n.replace(" ", ""))
             or master.get(_NAME_ALIASES.get(n, n).replace(" ", "")))
        if m:
            out[n] = m
            continue
        # 3) 캐시 — 양성/음성 TTL 적용
        rec = cache.get(n)
        if rec:
            ttl = _CODE_TTL_S if rec.get("code") else neg_ttl
            if (now - rec.get("_cached_at", 0)) < ttl:
                if rec.get("code"):
                    out[n] = rec["code"]
                continue
        if not fetch or not has_key:           # 캐시 전용(렌더)/키 없음: 외부 호출 안 함
            continue
        # 4) data.go.kr 폴백 — 마스터에 없는 표기. alias가 있으면 KRX 표기로 질의.
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
            _atomic_write_json(_CODE_CACHE, cache)
        except OSError:
            pass
    return out


# ── 네이버 실시간 시세 (무키·IP차단 없음·장중 라이브, 사용자 2026-06-15
# 'KIS 말고 네이버·실시간·장종료후 조정') — polling.finance.naver.com 단일코드.
# 장중 closePriceRaw 라이브, 장후 당일 종가(자연 세션 인지) + recommended_ttl
# 마감 6h 로 장종료 후 재호출 억제(=조정). NOAH bot/naver_quote 와 동일 소스.
_NAVER_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
_NAVER_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json", "Referer": "https://m.stock.naver.com/",
}


def _naver_quote_kr(code: str, *,
                    transport: Transport = _default_transport) -> Optional[Quote]:
    """네이버 실시간 단일 종목 → Quote. 키 불필요. 실패/빈 응답 → None.
    pct 부호: compareToPreviousPrice.code '5'=하락. prev_close 는 price·pct 역산."""
    code6 = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)[:6]
    try:
        resp = transport("GET", _NAVER_URL.format(code=code6),
                         headers=_NAVER_HDRS, body=None)
    except Exception:
        return None
    datas = (resp.get("datas") if isinstance(resp, dict) else None) or []
    it = datas[0] if datas and isinstance(datas[0], dict) else None
    if not it:
        return None
    price = _fnum(it.get("closePriceRaw") or it.get("closePrice"))
    if price <= 0:
        return None
    pct = _fnum(it.get("fluctuationsRatioRaw") or it.get("fluctuationsRatio"))
    cmp_ = it.get("compareToPreviousPrice")
    pct = -abs(pct) if (isinstance(cmp_, dict) and str(cmp_.get("code")) == "5") else abs(pct)
    prev = price / (1.0 + pct / 100.0) if pct else price
    return Quote(symbol=code6, name=(it.get("stockName") or code6),
                 price=price, change=price - prev, change_pct=pct,
                 prev_close=prev, currency="KRW",
                 ts=datetime.now(timezone.utc).isoformat())


def _naver_get_quotes(symbols: list[str], *,
                      transport: Transport = _default_transport) -> dict[str, Quote]:
    """네이버 실시간 배치 — 단일코드 엔드포인트라 코드별 1콜 + 가벼운 throttle.
    관련종목은 보통 수십 개라 부담 작음. 실패 코드는 생략(공란)."""
    throttle = _fnum(os.environ.get("TRADE_NAVER_THROTTLE_MS") or "40") / 1000.0
    out: dict[str, Quote] = {}
    for i, sym in enumerate(symbols):
        if i and throttle > 0:
            time.sleep(throttle)
        q = _naver_quote_kr(sym, transport=transport)
        if q:
            out[q.symbol] = q
    return out


_PROVIDERS: dict[str, Callable[..., dict[str, Quote]]] = {
    "naver": _naver_get_quotes,      # 기본(auto) — 무키·실시간
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
        _atomic_write_json(_QUOTE_CACHE, cache)
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
        jc = joined_company(s)             # 콤마/공백이 사명 일부인 단일 회사 → 통째 보존
        if jc:
            _add(jc)
            continue
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


# 콤마/공백이 **사명 일부**인 단일 회사 — BeOn 이 'EEW, KHPC'·'지앤비에스, 에코'
# 처럼 한 회사를 콤마/공백으로 적어, 콤마·공백 분리기가 두 고아 토큰으로 쪼개 영구
# 미매칭이 되던 것(사용자 2026-06-20). 운영자 확인분만. 분리 전 통째 보존·정규표기로
# 수렴. 키 = 공백·콤마 제거 소문자(모든 변형 흡수). 새 케이스 = 1줄 추가.
def _join_key(s: str) -> str:
    return re.sub(r"[\s,]", "", (s or "")).lower()


_JOINED_COMPANY: dict[str, str] = {
    _join_key(k): v for k, v in {
        "EEW KHPC": "EEW KHPC",        # 비상장(이미 테마 등록) — 한 회사
        "지앤비에스 에코": "지앤비에스에코",  # 상장 — 정규표기(공백없음)로 수렴
    }.items()
}


def joined_company(s: str) -> str | None:
    """콤마/공백 결합 단일 회사명이면 정규표기 반환, 아니면 None. 순수."""
    return _JOINED_COMPANY.get(_join_key(s))


# 파서 누수 ('관련종목 : LG전자') 접두사
_PARSER_PREFIX_RE = re.compile(r"^관련종목\s*:\s*")
# 1차 분리자: 슬래시·중점(·)·콤마 — 한국 본문에서 종목 나열에 자주 쓰임
_PRIMARY_SPLIT_RE = re.compile(r"[/·,]")
# 종목 후보에서 제외할 구조적 마커(제품설명·치수·괄호 등 프로즈 조각).
_STRUCT_REJECT = re.compile(r'[()%+"」「\[\]]|\d{3,}|kVA')

# 품목·소재·일반명사가 회사명처럼 보여(구조적 마커 없는 짧은 명사) 종목/회사로
# 새는 것 차단 — 회사별 뷰에 품목 설명이 가짜 회사로 뜨던 문제. 공백제거 정확일치,
# 운영자 확인 목록(확장 가능). 정확일치라 '한미반도체' 같은 실제 회사는 안 걸림.
_PRODUCT_BLOCKLIST: frozenset = frozenset({
    "수산화리튬", "광섬유", "동박", "소주", "젤라틴", "반도체", "웨이퍼",
    "소자의", "측정", "검사용", "장비", "인터페이스보드", "프로브", "카드",
    "빙과류", "아이스크림", "변압기", "배전반", "변환기", "자동차단기",
})


def _looks_like_stock(t: str) -> bool:
    """프로즈 조각(제품 설명·치수·괄호 등)과 제품/일반명사를 종목 후보에서 제외.
    구조적 마커(괄호·%·+·따옴표·대괄호·3자리+ 연속숫자·kVA)·길이 이탈(2~14자 밖)·
    제품 블록리스트 정확일치면 종목/회사명이 아닌 것으로 본다. 통과해도 data.go.kr
    에서 안 잡히면 어차피 음성 캐시 — 여기선 명백한 garbage만 거르는 게 목적."""
    t = (t or "").strip()
    if not (2 <= len(t) <= 14):
        return False
    if t.replace(" ", "") in _PRODUCT_BLOCKLIST:
        return False
    parts = t.split()
    if len(parts) >= 2 and all(p in _PRODUCT_BLOCKLIST for p in parts):
        return False                       # 전 토큰이 제품명(예: '반도체 웨이퍼')
    return not _STRUCT_REJECT.search(t)


# ---------------------------------------------------------------------
# Resolver — 표기 alias + 직접 코드 매핑
# ---------------------------------------------------------------------

# data.go.kr의 KRX 정식 표기와 운영자 데이터(BeOn)의 표기 차이 보정.
# 'BeOn 표기 → KRX 표기'로 적되, data.go.kr 응답을 따른다.
_NAME_ALIASES: dict[str, str] = {
    "Sk하이닉스": "SK하이닉스",
    "한국타이어": "한국타이어앤테크놀로지",
    "코오롱인더스트리": "코오롱인더",
    "롯데칠성음료": "롯데칠성",
    "금호석유": "금호석유화학",
    "한국수출포장공업": "한국수출포장",
    "코스맥스NBT": "코스맥스엔비티",
    "제이엔케이히터": "JNK히터",
    "조광ILI": "조광아이엘아이",
    "에스테아이": "에스티아이",      # BeOn 오타(운영자 확인) → KRX 에스티아이
    "지앤비에스": "지앤비에스에코",  # 운영자 확인: BeOn '지앤비에스' → KRX 지앤비에스에코
    # 회사명 오타 교정 — 매칭/표시는 mti_companies._COMPANY_TYPO 가, 가격 해석은
    # 여기가 담당(같은 오타 양쪽 등재, 2026-06-19 운영자 확인).
    "SK바이오센서": "SK바이오사이언스",
    "메티바이오메드": "메타바이오메드",
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
    "현대에너지솔루션": "322000",
    "제이오": "418550",
    "나노신소재": "121600",
    # alias 질의가 불안정한 종목은 코드 직접(data.go.kr 표기 의존 X)
    "조광ILI": "044060",          # 조광아이엘아이(상폐 — 시세 없음이 정상)
    "제이엔케이히터": "126880",   # JNK히터
    # 비상장 자회사·표기조각·상폐·시세 미표시 종목 → 운영자 확인 상장 프록시(모회사/
    # 관계사). 동일 회사가 아니라, 칩엔 BeOn 표기로 뜨고 가격은 이 코드(프록시) 기준.
    # 운영자 도메인 확인분만.
    "HD현대삼호중공업": "267250",  # → HD현대(지주)
    "HD현대미포조선": "329180",    # → HD현대중공업 (010620 KIS 시세 미표시 — 운영자 확인)
    "LS전선": "006260",           # → LS(지주)
    "이녹스리튬": "088390",        # → 이녹스
    "ELECTRIC": "010120",         # 'LS ELECTRIC' 토큰분리 조각 → LS ELECTRIC
    "SPC그룹": "005610",          # → SPC삼립(상장 자회사) — 운영자 확인
    "코오롱플라스틱": "120110",    # → 코오롱인더스트리 (138490 시세 미표시 — 운영자 확인)
}

# 코드 캐시 스키마 버전 — 매핑/분리 로직이 바뀌면 bump → 기존 캐시(특히 음성
# 캐시) 자동 무효화 → 새 매핑이 즉시 효과. 운영자가 캐시 파일 삭제 불필요.
# 4: KRX 로컬 마스터 도입 — 옛 음성캐시가 마스터 매칭을 가리지 않게 무효화.
# 5: SPC그룹→SPC삼립 프록시 + 지앤비에스→지앤비에스에코 별칭 — 진단 중 생성된
#    음성캐시가 새 매핑을 가리지 않게 무효화(특히 지앤비에스가 마스터에 없을 때).
_RESOLVER_VERSION = 5


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
               ttl_s: Optional[int] = None, fetch: bool = True) -> dict[str, Quote]:
    """{6자리코드: Quote}. 공급자 off/키 없음/예외 → {} (호출자는 가격 줄 생략).

    심볼별 TTL 캐시. ttl_s 미지정(None)이면 _default_quote_ttl()(=렌더와 동일한
    recommended_ttl, 장중 90s/마감 6h)을 써, 순진하게 불러도 렌더와 결과가 일치한다
    (옛 60s 고정 기본이 렌더와 달라 진단을 오도하던 함정 제거). fetch=False면 캐시
    신선분만 반환하고 외부 호출 안 함(렌더는 이걸로 즉시 끝내고, 실제 API는 워머
    fetch_quotes가 백그라운드에서)."""
    if ttl_s is None:
        ttl_s = _default_quote_ttl()
    # 캡(_MAX_SYMBOLS)은 외부 콜 폭주 방지용 — fetch=False(렌더, 캐시만·콜 0)엔
    # 적용 안 한다. 안 그러면 관련종목 수가 캡(기본 300)보다 많을 때 렌더가 앞쪽
    # 코드만 보고 뒤 종목이 캐시에 있어도 잘려 보이던 회귀.
    codes = _norm(symbols)
    if fetch:
        codes = codes[:_MAX_SYMBOLS]
    if not codes:
        return {}
    prov = _provider()
    provider = _PROVIDERS.get(prov)
    if provider is None:
        return {}
    now = time.time()
    cache = _load_cache()
    fresh: dict[str, Quote] = {}
    stale: list[str] = []
    for c in codes:
        rec = cache.get(c)
        if rec and (now - rec.get("_cached_at", 0)) < ttl_s:
            # prov=kis면 EOD 소스 캐시는 표시하지 않는다 — KIS-or-빈칸(운영자 결정:
            # EOD는 항상 T+1 지연 '틀린 날'이라 오정보가 빈칸보다 위험). fetch=True면
            # KIS로 재시도(옛 EOD 잔존분 탈환), fetch=False(렌더)면 생략→빈칸.
            # dataportal 공급자(KIS 키 없이 EOD 전용 모드)에선 EOD가 정상 경로라 반환.
            if not (rec.get("source") == "eod" and prov == "kis"):
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
                       ttl_s: Optional[int] = None,
                       fetch: bool = True) -> dict[str, Quote]:
    """{종목명: Quote} — BeOn 관련종목(회사명)을 코드 변환 없이 바로 시세에 연결.

    이름 정확일치(우선주 오매칭 방지)로만 붙이고, 못 찾으면 그 이름은 생략(틀린
    종목 안 붙임). ttl_s 미지정(None)이면 _default_quote_ttl()(렌더와 동일).
    fetch=False면 캐시만 읽고 외부 호출 0(렌더는 이걸로 즉시 끝냄 — 실제 API는
    워머 fetch_quotes가 백그라운드에서 채움)."""
    if ttl_s is None:
        ttl_s = _default_quote_ttl()
    uniq = []
    seen = set()
    for n in names:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    if fetch:                              # 콜 폭주 방지 상한 — 렌더(fetch=False)엔 미적용
        uniq = uniq[:_MAX_SYMBOLS]         #   (캐시만 읽어 콜 0이므로 자를 이유 없음)
    prov = _provider()
    if not uniq or prov == "none":
        return {}

    # KIS 등 코드-기반 공급자: data.go.kr로 이름→코드(durable 캐시) 변환 후
    # 활성 공급자로 시세. 종목명 검색이 없는 KIS가 BeOn 회사명을 쓰는 경로.
    if prov != "dataportal":
        codes_by_name = resolve_codes(uniq, transport=transport, fetch=fetch)
        if not codes_by_name:
            return {}
        all_codes = sorted(set(codes_by_name.values()))
        code_q = get_quotes(all_codes, transport=transport, ttl_s=ttl_s, fetch=fetch)
        # KIS가 못 주면 그 종목은 생략(빈칸) — data.go.kr EOD 폴백은 쓰지 않는다.
        # 운영자 결정: EOD는 T+1 지연이라 '틀린 날 가격'(오정보)이 빈칸보다 위험.
        # (EOD 전용 모드는 KIS 키가 아예 없을 때 prov=dataportal 분기에서만)
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
