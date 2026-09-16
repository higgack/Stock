"""금융위원회(FSC) 금융공공데이터 client — KR 증권 데이터 (KRX 로그인 불필요).

data.go.kr 금융위원회 OpenAPI, 동일 무료 키 DATA_GO_KR_API_KEY. pykrx 가
2025-12 KRX 유료화로 KRX_ID 의존이 된 뒤의 **KRX-login-free fallback 백본**
+ corp action 권리일정.

확정 엔드포인트 (discovery 2026-05-31, 전부 HTTP 200 검증):
  시세    /service/GetStockSecuritiesInfoService/getStockPriceInfo
  종목    /service/GetKrxListedInfoService/getItemInfo
  권리일정 /GetStocRighScheService_V2/getRighExerReasSche_V2 (신형 V2)

⚠️ 실시간 아님 — 기준일 익영업일 13시 갱신(금→월). 5거래일 horizon OK.
⚠️ 권리일정은 공공누리 2유형(출처표시+상업적이용금지, 출처 KSD) — NOAH
   비상업·교육 용도로 출처표시 하에 사용.

키 없으면 fsc_key_ready() gate 가 graceful skip. 12h 디스크 캐시.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from bot.env_keys import env_key as _env_key

log = logging.getLogger("bot.fsc")

_HOST = "https://apis.data.go.kr/1160100"
_PRICE = (f"{_HOST}/service/GetStockSecuritiesInfoService", "getStockPriceInfo")
_ITEM = (f"{_HOST}/service/GetKrxListedInfoService", "getItemInfo")
_RIGHT = (f"{_HOST}/GetStocRighScheService_V2", "getRighExerReasSche_V2")
_TIMEOUT = 20
_KST = timezone(timedelta(hours=9))
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_KEY_WARNED = False
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "fsc_cache")
_CACHE_TTL = 12 * 3600


def fsc_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(_env_key("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("fsc: DATA_GO_KR_API_KEY 미설정 — 금융위 증권데이터 skip.")
        _KEY_WARNED = True
    return ready


def _kr_code(ticker: str) -> str:
    """'005930.KS' / '005930' / 'A005930' → 6자리 숫자 코드."""
    t = (ticker or "").split(".")[0].strip().upper()
    if t.startswith("A") and t[1:].isdigit():
        t = t[1:]
    return t


def _now() -> datetime:
    return datetime.now(_KST)


# ── 디스크 캐시 (truthy-only, 기본 12h · 호출별 ttl 오버라이드 가능) ──────
def _cache_get(key: str, ttl: float | None = None):
    try:
        p = os.path.join(_CACHE_DIR, key + ".json")
        if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < (ttl or _CACHE_TTL):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _cache_put(key: str, val) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_CACHE_DIR, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(val, f, ensure_ascii=False)
    except Exception as exc:
        log.debug("fsc: cache put failed: %s", exc)


# ── 서비스 장애 차단기 ────────────────────────────────────────────────
# ⚠️ 2026-08-21 VM 계측: KR 상세 수집(`enrich:KR`)이 **중앙값 44.6초·최대
# 86.4초**로 전체 로딩을 지배했다. 원인은 파싱이 아니라 금융위 API 의
# `SERVICETIMEOUT_ERROR`(HTTP 504) — `dilution_events` 가 11영업일 × 2
# 엔드포인트를 각각 20초 상한으로 두드리는데, 서비스가 죽어 있으면 그
# 전부가 순손실이다. 실패는 종목별이 아니라 **서비스 전체** 상태이므로
# 연속 실패하면 그 op 을 잠시 쉰다 — 살아 있을 땐 동작이 그대로다.
_FAIL: dict[str, tuple[float, int]] = {}   # op → (마지막 실패시각, 연속 횟수)
_FAIL_MAX = 2            # 연속 2회면 죽은 것으로 본다(단발 오류는 통과)
_FAIL_COOL = 300.0       # 5분 냉각 — 그 사이 요청은 즉시 [] 로 끝난다


def _breaker_open(op: str) -> bool:
    ts, n = _FAIL.get(op, (0.0, 0))
    return n >= _FAIL_MAX and (time.time() - ts) < _FAIL_COOL


def _breaker_mark(op: str, ok: bool) -> None:
    if ok:
        _FAIL.pop(op, None)
        return
    _ts, n = _FAIL.get(op, (0.0, 0))
    _FAIL[op] = (time.time(), n + 1)


def _fetch(base: str, op: str, params: dict) -> list[dict]:
    """FSC GET → items 리스트. serviceKey 인코딩 자동, 실패 시 []."""
    return _fetch2(base, op, params)[0]


def _fetch2(base: str, op: str, params: dict) -> tuple[list[dict], bool]:
    """`(items, 요청 성공 여부)`.

    ⚠️ `[]` 는 '결과 없음'과 '서비스 장애'를 구별하지 못한다 — 둘을 같이
    다루면 죽은 서비스에 남은 날짜를 계속 쏘게 된다(#72). 호출부가
    가를 수 있게 성공 여부를 같이 돌려준다.

    ⚠️ 서비스가 연속 실패하면 냉각 동안 **네트워크를 아예 안 친다**."""
    if not fsc_key_ready():
        return [], False
    if _breaker_open(op):
        return [], False
    import httpx
    key = _env_key("DATA_GO_KR_API_KEY")
    q = {"resultType": "json", "numOfRows": params.pop("numOfRows", 100),
         "pageNo": 1, **params}
    url = f"{base}/{op}"
    h = {"User-Agent": _UA, "Accept": "application/json, */*"}
    try:
        if "%" in key:
            from urllib.parse import urlencode
            r = httpx.get(f"{url}?serviceKey={key}&{urlencode(q)}",
                          headers=h, timeout=_TIMEOUT, follow_redirects=True)
        else:
            r = httpx.get(url, params={"serviceKey": key, **q},
                          headers=h, timeout=_TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            # 5xx = 서비스 장애(우리 요청 문제 아님) → 차단기 대상.
            # 4xx 는 파라미터·키 문제라 재시도해도 같으므로 세지 않는다.
            _breaker_mark(op, r.status_code < 500)
            log.warning("fsc: %s HTTP %d — %s", op, r.status_code, r.text[:160])
            return [], False
        _breaker_mark(op, True)
        body = (r.json() or {}).get("response", {}).get("body", {}) or {}
        items = (body.get("items") or {}).get("item")
        if items is None:
            return [], True
        return (items if isinstance(items, list) else [items]), True
    except Exception as exc:
        _breaker_mark(op, False)          # 타임아웃·연결실패도 서비스 장애
        log.warning("fsc: %s 호출 실패: %s", op, exc)
        return [], False


def _f(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ── 1) 주식시세 (KRX-login-free 시총/종가/거래량 fallback) ─────────────────
def price_series(ticker: str, days: int = 15) -> list[dict]:
    """최근 days일 일별 시세 (basDt 오름차순). 캐시 12h.
    각 행: basDt, clpr, mkp, hipr, lopr, trqu, trPrc, fltRt, lstgStCnt, mrktTotAmt."""
    code = _kr_code(ticker)
    if not code:
        return []
    ck = f"price_{code}_{_now():%Y%m%d}"
    c = _cache_get(ck)
    if c is not None:
        return c
    begin = (_now().date() - timedelta(days=days + 7)).strftime("%Y%m%d")
    raw = _fetch(_PRICE[0], _PRICE[1],
                 {"likeSrtnCd": code, "beginBasDt": begin, "numOfRows": 60})
    rows = []
    for it in raw:
        if _kr_code(it.get("srtnCd", "")) != code:
            continue
        rows.append({
            "basDt": str(it.get("basDt") or ""),
            "clpr": _f(it.get("clpr")), "mkp": _f(it.get("mkp")),
            "hipr": _f(it.get("hipr")), "lopr": _f(it.get("lopr")),
            "trqu": _f(it.get("trqu")), "trPrc": _f(it.get("trPrc")),
            "fltRt": _f(it.get("fltRt")), "vs": _f(it.get("vs")),
            "lstgStCnt": _f(it.get("lstgStCnt")), "mrktTotAmt": _f(it.get("mrktTotAmt")),
        })
    rows.sort(key=lambda r: r["basDt"])
    if rows:  # truthy-only — transient 빈 결과 미캐시(fallback dormant 방지)
        _cache_put(ck, rows)
    return rows


def latest_price(ticker: str) -> dict | None:
    """가장 최근 영업일 시세 1행 (시총·종가·거래량). 없으면 None."""
    s = price_series(ticker)
    return s[-1] if s else None


# ── 2) KRX 종목정보 (코드↔법인등록번호 매핑, DART 연결키) ──────────────────
def item_info(ticker: str) -> dict | None:
    """종목 master: srtnCd, isinCd, mrktCtg, itmsNm, crno(법인등록번호), corpNm."""
    code = _kr_code(ticker)
    if not code:
        return None
    ck = f"item_{code}_{_now():%Y%m%d}"
    c = _cache_get(ck)
    if c:  # truthy-only — 빈 결과 미캐시(crno 부재 시 downstream 전체 cascade 방지)
        return c
    raw = _fetch(_ITEM[0], _ITEM[1], {"likeSrtnCd": code, "numOfRows": 10})
    best = None
    for it in raw:
        if _kr_code(it.get("srtnCd", "")) == code:
            if best is None or str(it.get("basDt", "")) > str(best.get("basDt", "")):
                best = it
    if best is None:
        return None
    out = {
        "srtnCd": best.get("srtnCd"), "isinCd": best.get("isinCd"),
        "mrktCtg": best.get("mrktCtg"), "itmsNm": best.get("itmsNm"),
        "crno": best.get("crno"), "corpNm": best.get("corpNm"),
    }
    _cache_put(ck, out)
    return out


# ── 3) 주식권리일정 (corp action — 증자/감자/분할/배당 ex-date) ────────────
# 권리일정은 ticker 직접 필터 param 이 없어(basDt/KSD고객번호/발행회사명) crno
# 로 응답을 Python 필터. 최근 basDt window 를 받아 캐시 후 crno 매칭.
def rights_by_basdt(bas_dt: str) -> list[dict]:
    """특정 기준일자(basDt, YYYYMMDD) 의 전체 권리일정 raw (캐시 12h)."""
    ck = f"rights_{bas_dt}"
    c = _cache_get(ck)
    if c is not None:
        return c
    raw = _fetch(_RIGHT[0], _RIGHT[1], {"basDt": bas_dt, "numOfRows": 2000})
    rows = []
    for it in raw:
        rows.append({
            "basDt": str(it.get("basDt") or ""), "crno": str(it.get("crno") or ""),
            "cmpyNm": it.get("stckIssuCmpyNm") or "",
            "rcdNm": it.get("stckIssuRcdNm") or "",        # 사유 (배당/무상증자/감자/임시총회…)
            "rgtNm": it.get("rgtExertRcdNm") or "",        # 권리구분 (기준일 등)
            "rgtSttgDt": str(it.get("rgtExertSttgDt") or ""),  # 권리행사 시작
            "rgtEdDt": str(it.get("rgtExertEdDt") or ""),      # 권리행사 종료
            "lckSttgDt": str(it.get("nmlsLckSttgDt") or ""),   # 명부폐쇄 시작
            "lckEdDt": str(it.get("nmlsLckEdDt") or ""),
            "parPrc": it.get("stckParPrc") or "",
        })
    if rows:  # truthy-only (transient 빈 응답 미캐시)
        _cache_put(ck, rows)
    return rows


def rights_for(ticker: str, lookback_days: int = 21) -> list[dict]:
    """ticker 의 최근 lookback_days 기준일자 권리일정 (crno 매칭). corp action
    가드용. crno 는 item_info 로 조회."""
    info = item_info(ticker)
    crno = (info or {}).get("crno")
    if not crno:
        return []
    out = []
    today = _now().date()
    # 영업일만: 주말 skip, lookback 기간 일자별 조회 (각 basDt 캐시됨)
    for i in range(lookback_days + 1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        for r in rights_by_basdt(d.strftime("%Y%m%d")):
            if r["crno"] == str(crno):
                out.append(r)
    return out


# ── 3.8) 소액주주현황 (free float·유통물량) + dilution 공시 (CB/BW/유증) ──
_CGDISC = (f"{_HOST}/GetCGDiscInfoService", "getCGSmamInfo")
_DISC_BASE = f"{_HOST}/service/GetDiscInfoService_V2"


def minority_holders(ticker: str) -> dict | None:
    """소액주주현황 — free float 근사. crno 조회, 최신 사업연도. {smam_cnt(소액
    주주수), whole_cnt(전체주주수), smam_ratio(%), hold_shares(보유주식수),
    biz_year}. 없으면 None."""
    info = item_info(ticker)
    crno = (info or {}).get("crno")
    if not crno:
        return None
    ck = f"minor_{crno}_{_now():%Y%m}"
    c = _cache_get(ck)
    if c:  # truthy-only — 빈 결과는 캐시하지 않으므로 falsy 면 재조회
        return c
    raw = _fetch(_CGDISC[0], _CGDISC[1], {"crno": crno, "numOfRows": 20})
    best = None
    for it in raw:
        if best is None or str(it.get("bizYear", "")) > str(best.get("bizYear", "")):
            best = it
    if not best:
        return None
    out = {
        "smam_cnt": _f(best.get("smamSthdCnt")),
        "whole_cnt": _f(best.get("whlSthdCnt")),
        "smam_ratio": _f(best.get("smamSthdRto")),
        "hold_shares": _f(best.get("holdStckCnt")),
        "biz_year": str(best.get("bizYear") or ""),
    }
    _cache_put(ck, out)  # truthy 만 도달
    return out


# 하루씩 **순차로** 물으면 해당 공시가 없는 종목(대다수)이 매번 영업일
# 수만큼 직렬 요청을 낸다 — 2026-08-21 VM 계측에서 `kr:fsc.risk` 가
# **중앙값 21.26초**로 `enrich:KR`(22.24초) 전체를 지배했다(2 엔드포인트
# × 8영업일 = 16회 직렬). 요청 수는 그대로 두고 벽시계만 줄인다.
_DAY_POOL = 8


def _business_days(today, lookback_days: int) -> list[str]:
    """오늘부터 거슬러 `lookback_days` 일 중 **평일**만 (최신 → 과거)."""
    out = []
    for i in range(lookback_days + 1):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out


def _newest_nonempty(days: list[str], fetch_one) -> list[dict]:
    """`days`(최신→과거) 중 **가장 최신의 non-empty** 응답.

    `fetch_one(bas)` 는 `(items, 요청 성공 여부)` 를 돌려줘야 한다.

    ⚠️ 최신 하루는 **순차로 먼저** 친다. 서비스가 죽어 있을 때 8발을
    동시에 쏘면 차단기가 뜨기 전에 다 나가 #72 에서 얻은 '죽은 API 는
    네트워크 2회로 끝난다'가 무의미해진다. 첫 요청이 **실패**하면 그 op
    은 그대로 포기한다 — 나머지 날을 쳐도 같은 서비스다(결과 없음과
    장애를 `_fetch2` 가 갈라 주기 때문에 이 구별이 가능하다).
    """
    if not days:
        return []
    items, ok = fetch_one(days[0])
    if items:
        return items
    if not ok:
        return []              # 서비스 장애 — 남은 날은 순손실
    rest = days[1:]
    if not rest:
        return []
    from bot.pool import map_bounded
    # ⚠️ 공용 풀(프로세스 상한) — 요청마다 새 풀이면 동시 조회 때 팬아웃이
    # 곱해진다. 결과는 **입력 순서**라 첫 non-empty 가 곧 최신이다.
    for r in map_bounded(fetch_one, rest):
        if r and r[0]:
            return r[0]
    return []


# dilution 공시 — (op, 주식수 field, 가격 field, kind label).
# ⚠️ 유상증자는 제외: corp-action HARD GUARD 키워드(_KR_CORP_ACTION_KEYWORDS)
# 에 '유상증자' 가 이미 있어 DART/권리일정 가드가 잡음 → 중복 배너 방지.
# CB/BW(전환사채·신주인수권)는 corp-action 키워드에 없는 별개 잠재희석이라
# 여기서만 잡는다 (사용자 '중복 없이' 원칙, 2026-05-31 review).
_DILUTION_OPS = (
    ("getCbRighIssuDiscInfo_V2", "cpbdCnvrStckCnt", "cbCnvrPrc", "전환사채(CB)"),
    ("getBwRighIssuDiscInfo_V2", "prmrIssuStckCnt", "bwrExertPrc", "신주인수권부사채(BW)"),
)


def dilution_events(ticker: str, lookback_days: int = 10) -> list[dict]:
    """CB/BW/유상증자 발행결정 공시 — 잠재 희석 이벤트. 최근 영업일 basDt
    스냅샷(첫 non-empty)·crno 필터. 각 행: {kind, date(bodRsolDt 또는 basDt),
    new_shares, price, ratio}. 없으면 []."""
    info = item_info(ticker)
    crno = (info or {}).get("crno")
    if not crno:
        return []
    days = _business_days(_now().date(), lookback_days)
    out = []
    for op, sh_f, pr_f, kind in _DILUTION_OPS:

        def _one(bas, _op=op):
            ck = f"dilu_{_op}_{crno}_{bas}"
            c = _cache_get(ck)
            if c:
                return c, True
            c, ok = _fetch2(_DISC_BASE, _op,
                            {"basDt": bas, "crno": crno, "numOfRows": 20})
            if c:  # truthy-only (transient 빈 응답 미캐시)
                _cache_put(ck, c)
            return c, ok
        for it in _newest_nonempty(days, _one):   # op 당 첫 non-empty basDt 만
            rd = str(it.get("bodRsolDt") or it.get("basDt") or "").strip()
            iso = (f"{rd[:4]}-{rd[4:6]}-{rd[6:]}"
                   if len(rd) == 8 and rd.isdigit() else rd)
            out.append({
                "kind": kind, "date": iso,
                "new_shares": _f(it.get(sh_f)), "price": _f(it.get(pr_f)),
            })
    return out


# ── 3.7) 의무보호예수 반환 (lock-up 해제 = 단기 공급 overhang) ────────────
# 금융위 주식발행정보 V3 (GetStocIssuInfoService_V3). basDt 필수 + crno 필터.
# rsrnDt=반환일(해제일), rsrnStckCnt=반환주식수(출회 물량).
_LOCKUP = (f"{_HOST}/GetStocIssuInfoService_V3", "getLockUpRetuInfo_V3")


def lockup_releases(ticker: str, lookback_days: int = 7) -> list[dict]:
    """ticker 의 의무보호예수 반환(lock-up 해제) 일정. 최근 영업일 basDt 로
    스냅샷 조회(첫 non-empty 채택) + crno 필터. 각 행: {release_date(YYYY-MM-DD),
    shares(반환주식수), remaining(반환후잔량), reason, total_shares}. 없으면 []."""
    info = item_info(ticker)
    crno = (info or {}).get("crno")
    if not crno:
        return []

    def _one(bas):
        ck = f"lockup_{crno}_{bas}"
        c = _cache_get(ck)
        if c:
            return c, True
        c, ok = _fetch2(_LOCKUP[0], _LOCKUP[1],
                        {"basDt": bas, "crno": crno, "numOfRows": 100})
        if c:  # truthy-only (transient 빈 응답 미캐시)
            _cache_put(ck, c)
        return c, ok

    out = {}
    for it in _newest_nonempty(_business_days(_now().date(), lookback_days),
                               _one):
        rd = str(it.get("rsrnDt") or "").strip()
        if len(rd) != 8 or not rd.isdigit():
            continue
        iso = f"{rd[:4]}-{rd[4:6]}-{rd[6:]}"
        out[(iso, str(it.get("rsrnStckCnt") or ""))] = {
            "release_date": iso,
            "shares": _f(it.get("rsrnStckCnt")),
            "remaining": _f(it.get("afrsRsqtCnt")),
            "reason": (it.get("stckLblHoldRcdNm") or "").strip(),
            "total_shares": _f(it.get("lblProtTsumIssuStckCnt")),
        }
    return sorted(out.values(), key=lambda r: r["release_date"])


# ── 3.6) 주식발행 공시정보 (유통주식수=free float) — crno 키 ──────────────
# Base 는 /service/ 없는 패턴 (권리일정 V2 처럼). crno(법인등록번호)로 조회
# → Phase1 item_info 의 crno 연결. 유통주식수 필드 key 는 probe 로 확정.
_STKISSU = (f"{_HOST}/GetStkIssuInfoService", "getStkIssuInfo")


def stock_issue_raw(ticker: str, biz_year: str | None = None) -> list[dict]:
    """주식발행 공시(주식총수현황) raw 행 — crno 로 조회. probe/내부용."""
    info = item_info(ticker)
    crno = (info or {}).get("crno")
    if not crno:
        return []
    params = {"crno": crno, "numOfRows": 20}
    if biz_year:
        params["bizYear"] = biz_year
    return _fetch(_STKISSU[0], _STKISSU[1], params)


# ── 3.5) 증권상품시세 (ETF/ETN) — yfinance KR ETF 빈약 보완 + 괴리율 ──────
_SECPROD = f"{_HOST}/service/GetSecuritiesProductInfoService"


def securities_product_quote(ticker: str) -> dict | None:
    """ETF/ETN 최신 시세 + 괴리율(가격 vs NAV/지표가치). ELW 제외(옵션성).
    ETF 먼저, 없으면 ETN 시도. {type, basDt, clpr, nav, premium_pct(%),
    trqu, mrktTotAmt, nav_total, bssIdx}. 일반 주식이면 None."""
    code = _kr_code(ticker)
    if not code:
        return None
    ck = f"secprod_{code}_{_now():%Y%m%d}"
    c = _cache_get(ck)
    if c:  # truthy-only — 빈 결과(일반주식/transient)는 미캐시
        return c
    out = None
    begin = (_now().date() - timedelta(days=10)).strftime("%Y%m%d")
    for op, ind_field, tot_field, kind in (
            ("getETFPriceInfo", "nav", "nPptTotAmt", "ETF"),
            ("getETNPriceInfo", "indcVal", "indcValTotAmt", "ETN")):
        raw = _fetch(_SECPROD, op,
                     {"likeSrtnCd": code, "beginBasDt": begin, "numOfRows": 20})
        rows = [r for r in raw if _kr_code(r.get("srtnCd", "")) == code]
        if not rows:
            continue
        rows.sort(key=lambda r: str(r.get("basDt") or ""))
        it = rows[-1]
        clpr, ind = _f(it.get("clpr")), _f(it.get(ind_field))
        prem = round((clpr / ind - 1) * 100, 2) if clpr and ind else None
        out = {
            "type": kind, "basDt": str(it.get("basDt") or ""),
            "clpr": clpr, "nav": ind, "premium_pct": prem,
            "trqu": _f(it.get("trqu")), "mrktTotAmt": _f(it.get("mrktTotAmt")),
            "nav_total": _f(it.get(tot_field)),
            "bssIdx": it.get("bssIdxIdxNm") or "",
        }
        break
    if out:
        _cache_put(ck, out)
    return out


# ── 4) 금융투자협회 종합통계 (시장 전체 수급·심리, ticker 무관) ───────────
# 15094809. 예탁금(dry powder) + 신용융자(레버리지) = KR 시장 internal
# sentiment. 시장 전체값 → 1회 fetch 12h 캐시로 전 KR 분석·Daily Byte 공유.
# Base URL 미확정 — env override 또는 후보 자동탐색.
# discovery 2026-05-31 확정 (Swagger Base URL).
_KOFIA_BASE = os.environ.get(
    "FSC_KOFIA_BASE", f"{_HOST}/service/GetKofiaStatisticsInfoService")
_KOFIA_BASE_CANDIDATES = (
    f"{_HOST}/service/GetKofiaStatisticsInfoService",
)
_OP_DEPOSIT = "getSecuritiesMarketTotalCapitalInfo"   # 증시자금추이
_OP_CREDIT = "getGrantingOfCreditBalanceInfo"         # 신용공여잔고추이


def _kofia_series(op: str, field: str, n: int = 30) -> list[tuple[str, float]]:
    """협회통계 op 의 (basDt, field값) 시계열 (오름차순).

    캐시 1h (사용자 2026-08-08 '시장유동성 섹션 전체 1시간 단위로' — 기존
    3h(2026-06-10, 예탁금 위젯이 Naver 라이브 대비 3일 늦게 보인 건 대응)
    보다 더 조인 재확인 주기. 키가 날짜 포함이라 자정 후 첫 호출은 어차피
    재fetch — TTL 은 당일 내 재확인 주기). 진단 로그로 원천 최신 basDt 를
    INFO 남김 — VM journal 에서 '원천 자체가 T+N 지연'인지 판별용."""
    ck = f"kofia_{op}_{field}_{n}_{_now():%Y%m%d}"
    c = _cache_get(ck, ttl=1 * 3600)
    if c is not None:
        return [tuple(x) for x in c]
    raw = _fetch(_KOFIA_BASE, op, {"numOfRows": n})
    series = {}
    for it in raw:
        d = str(it.get("basDt") or "")
        v = _f(it.get(field))
        if d and v is not None:
            series[d] = v
    out = sorted(series.items())
    if out:  # truthy-only (transient 빈 응답 미캐시)
        _cache_put(ck, out)
        log.info("kofia %s.%s: latest basDt=%s (rows=%d) — 원천 공표 지연 진단용",
                 op, field, out[-1][0], len(out))
    return out


def _trend(series: list[tuple[str, float]]) -> dict | None:
    if len(series) < 2:
        return None
    periods = [p for p, _ in series]
    vals = [v for _, v in series]
    latest, prev = vals[-1], vals[-2]
    wow = (latest / prev - 1.0) * 100.0 if prev else None
    mom = (latest / vals[-22] - 1.0) * 100.0 if len(vals) >= 22 and vals[-22] else None
    return {"date": periods[-1], "latest": latest,
            "wow_pct": round(wow, 2) if wow is not None else None,
            "mom_pct": round(mom, 2) if mom is not None else None}


def market_deposit() -> dict | None:
    """투자자예탁금(invrDpsgAmt) 최신 + WoW/MoM 추세. 대기매수 자금."""
    return _trend(_kofia_series(_OP_DEPOSIT, "invrDpsgAmt"))


def margin_balance() -> dict | None:
    """신용융자 전체잔고(crdTrFingWhl) 최신 + WoW/MoM 추세. 빚투 레버리지."""
    return _trend(_kofia_series(_OP_CREDIT, "crdTrFingWhl"))


def deposit_series_eok(n: int = 130) -> list[tuple[str, float]]:
    """투자자예탁금 시계열 [(basDt, 억원)] — 대시보드 6개월 그래프용.

    KOFIA 일별 데이터(억원 단위 환산: 원/1e8). 신용잔고와 동일 출처(공식 API)
    라 Naver 스크래핑보다 견고 — 신용잔고도 깔끔히 나옴."""
    return [(d, v / 1e8) for d, v in _kofia_series(_OP_DEPOSIT, "invrDpsgAmt", n)]


def credit_series_eok(n: int = 130) -> list[tuple[str, float]]:
    """신용융자잔고 시계열 [(basDt, 억원)] — 대시보드 6개월 그래프용."""
    return [(d, v / 1e8) for d, v in _kofia_series(_OP_CREDIT, "crdTrFingWhl", n)]


def collateral_loan_series_eok(n: int = 130) -> list[tuple[str, float]]:
    """예탁증권담보융자 잔고 시계열 [(basDt, 억원)] — 신용융자 밖의 '빚투'
    보완 레버리지(2026-07-08 실측 필드 dpsgScrtMogFing, 신용공여 응답 동거
    — 추가 API 호출 0). 대시보드 예탁금 카드 6번째 지표."""
    return [(d, v / 1e8)
            for d, v in _kofia_series(_OP_CREDIT, "dpsgScrtMogFing", n)]


# 시장별(코스피/코스닥) 신용거래융자 필드명 — data.go.kr 문서가 프록시로 확인
# 불가라 정확명 후보 + 휴리스틱 런타임 발견(2026-07-06). 발견 결과는 INFO 로그
# (VM journal 로 실제 키 확인 → 확정 시 후보 목록에 고정 추가).
_CREDIT_SPLIT_EXACT = {
    # 확정 필드명(2026-07-08 VM 실측 — 응답 keys: crdTrFingScrs/Kosdaq/Whl,
    # crdTrLndr*(대주), dpsgScrtMogFing(예탁증권담보), sbscCapLn(청약자금)):
    # 유가증권(코스피) = crdTrFingScrs ('Scrs' 축약 — Scrt 아님 주의).
    "kospi": ("crdTrFingScrs", "crdTrFingScrt", "scrtMrktCrdTrFing",
              "stexMrktCrdTrFing", "crdTrFingStex"),
    "kosdaq": ("crdTrFingKosdaq", "ksdqMrktCrdTrFing", "crdTrFingKsdq"),
}


def _classify_credit_key(key: str) -> str | None:
    """crdTrFing* 계열 키 → 'kospi'/'kosdaq'/None. 전체(Whl)·대주(Loan) 제외 +
    비잔고 파생필드(비율/일수/증감/건수 등) 제외(리뷰 2026-07-06 — 비율 필드가
    kospi 로 오분류돼 '0억' 위젯이 실데이터처럼 보이는 것 차단)."""
    lk = key.lower()
    if "crdtrfing" not in lk or "whl" in lk:
        return None
    # ⚠️ 'rt' 단독 토큰 금지 — 'scrt' 내포로 코스피 필드까지 제외돼 버림.
    if any(x in lk for x in ("rto", "rate", "ratio", "pct", "dys", "anlys",
                             "cnt", "dff", "diff", "chg")):
        return None
    if any(t in lk for t in ("kosdaq", "ksdq")):
        return "kosdaq"
    if any(t in lk for t in ("scrs", "scrt", "stex", "kospi")):
        return "kospi"
    return None


# 시장별 신용잔고가 **빈 이유** — 처방이 전부 다르다(#82). 옛 판은 전부
# `{}` 하나로 뭉뚱그렸고, 화면은 카드를 **통째로 없앴다** — 사용자 2026-09-14
# "코스피/코스닥 신용잔고 추이가 안나올때가 있어"(#335·#343 과 같은 계열:
# 값이 없으면 위젯이 사라져 기능이 삭제된 것처럼 보인다).
_CREDIT_SPLIT_WHY = {
    "key": "DATA_GO_KR_API_KEY 미설정 — 원천을 부를 수 없습니다",
    "breaker": "금융위 서비스 연속 실패로 냉각 중 — 곧 자동 재시도합니다",
    "http": "원천 요청이 실패했습니다(상태코드·타임아웃)",
    "empty": "원천이 결과를 0건으로 돌려줬습니다",
    "unparsed": "시장별 필드는 찾았지만 값을 하나도 읽지 못했습니다",
    "nofield": "응답에 시장별(코스피/코스닥) 필드가 없습니다",
    # 한쪽 시장만 온 경우 — 나머지 카드가 "사유 미기록" 으로 남으면 #369 가
    # 없애려던 그 침묵이 그대로다(2026-09-16 독립 리뷰).
    "partial": "원천이 한쪽 시장만 돌려줬습니다(코스피·코스닥 중 하나)",
    "sanity": "시장별 합계가 전체와 크게 어긋나 값을 채택하지 않았습니다",
    # 수집기가 네이버 폴백으로 내려간 경우 — 그 원천엔 시장별 분리가 없다.
    "fallback": "금융투자협회 대신 네이버 폴백으로 받아 시장별 분리가 없습니다",
}


# 빈 결과를 얼마나 믿나 — 성공(1h)보다 훨씬 짧게(#116 예산과 캐시는 한 세트).
# 일시 실패 한 번이 카드를 한 시간 지우면 사용자에겐 "가끔 안 나온다" 로만
# 보인다(#152·#161·#280·#303, 사용자 2026-09-14).
_CREDIT_TTL_SEC = 1 * 3600   # 2026-08-08 시장유동성 섹션 1h 통일
_EMPTY_KEEP_SEC = 600


def _credit_split_cache(ck: str, out: dict, why: str) -> tuple[dict, str]:
    """결과를 캐시하고 그대로 돌려준다 — **빈 결과는 수명을 줄여서**.

    `_cache_get` 의 ttl 은 호출부가 정하므로, 같은 키를 두 ttl 로 읽는 대신
    mtime 을 과거로 밀어 남은 수명만 남긴다(읽는 코드가 하나로 남는다).
    """
    _cache_put(ck, {"series": {m: list(map(list, v)) for m, v in out.items()},
                    "why": why})
    if not out:
        try:
            import os as _os
            import time as _t
            _p = _os.path.join(_CACHE_DIR, ck + ".json")
            _back = _t.time() - (_CREDIT_TTL_SEC - _EMPTY_KEEP_SEC)
            _os.utime(_p, (_back, _back))
        except Exception as exc:                               # noqa: BLE001
            log.debug("fsc: 빈 결과 캐시 수명 단축 실패: %s", exc)
    return out, why


def credit_split_reason_text(why: str) -> str:
    """사유 코드 → 사람이 읽는 한 줄. 화면·진단이 **같은 문구**를 쓴다(#38).

    모르는 코드는 지어내지 않고 그대로 보여 준다 — 새 갈래가 생기면 화면에
    낯선 낱말이 뜨는 것 자체가 신호다(#165·#290).
    """
    w = str(why or "").strip()
    if not w:
        return ""
    return _CREDIT_SPLIT_WHY.get(w, f"원천에서 값을 받지 못했습니다({w})")


def credit_split_with_reason(n: int = 130) -> tuple[dict, str]:
    """({"kospi": [(basDt, 억원)], ...}, 사유) — 빈 dict 의 **갈래를 이름으로**.

    응답 필드명 런타임 발견: 정확명 후보(_CREDIT_SPLIT_EXACT) 우선, 없으면
    crdTrFing* 키 휴리스틱(_classify_credit_key). 발견/미발견 모두 INFO 로그
    (silent-fail 금지).

    ⚠️ 얇은 래퍼가 사유를 버리면 화면이 "없는 거야?"에 답을 못 한다
    (#123·#129·#189·#228 계열) — 값만 필요한 자리는 `credit_split_series_eok`.
    """
    ck = f"kofia_credit_split_{n}_{_now():%Y%m%d}"
    # ⚠️ 읽는 ttl 과 위 mtime 되감기는 **한 상수**에서 와야 한다 —
    # 따로 적으면 읽는 쪽만 늘려도 테스트가 전부 green 인 채 "빈 결과는
    # 10분" 계약이 조용히 5시간이 된다(2026-09-16 독립 리뷰 실측, #38·#91b).
    c = _cache_get(ck, ttl=_CREDIT_TTL_SEC)
    if c is not None:
        ser = c.get("series") if isinstance(c, dict) and "series" in c else c
        return ({m: [tuple(x) for x in v] for m, v in (ser or {}).items()},
                (c.get("why", "") if isinstance(c, dict) and "series" in c else ""))
    if not fsc_key_ready():
        # 키가 없으면 네트워크를 안 친다 — 캐시할 실패도 아니다.
        log.info("kofia credit split: 키 미설정 — skip")
        return {}, "key"
    if _breaker_open(_OP_CREDIT):
        log.info("kofia credit split: 차단기 냉각 중 — skip")
        return {}, "breaker"
    raw, ok = _fetch2(_KOFIA_BASE, _OP_CREDIT, {"numOfRows": n})
    if not raw:
        # ⚠️ `[]` 는 '결과 없음'과 '서비스 장애'를 구별 못 한다 — 처방이
        # 다르므로 `_fetch2` 의 성공 플래그로 가른다(#82·#143).
        why = "empty" if ok else "http"
        log.info("kofia credit split: 빈 응답(%s) — skip", why)
        # ⚠️ 여기도 **짧게 캐시한다** — 안 하면 30초 위젯 regen 마다 죽은
        # 서비스를 다시 친다(쿼터 낭비). 아래 성공 경로와 같은 수명 규약을
        # 쓰도록 한 곳으로 모은다(#38 두 곳에 적으면 갈라진다).
        return _credit_split_cache(ck, {}, why)
    # 키 발견은 앞쪽 여러 행 union — 최신 행이 장중 일부 필드만 채워 오는
    # 케이스에서 시장 필드를 놓치지 않게(리뷰 2026-07-06).
    keys: list = []
    for it in raw[:10]:
        for k in it.keys():
            if k not in keys:
                keys.append(k)
    field: dict[str, str] = {}
    for mkt, cands in _CREDIT_SPLIT_EXACT.items():
        for cand in cands:
            if cand in keys:
                field[mkt] = cand
                break
    for k in keys:                       # 휴리스틱 보충(정확명 미적중 시장만)
        mkt = _classify_credit_key(k)
        if mkt and mkt not in field:
            field[mkt] = k
    log.info("kofia credit split: keys=%s → kospi=%s kosdaq=%s",
             [k for k in keys if "crd" in k.lower()],
             field.get("kospi"), field.get("kosdaq"))
    if not field:
        # 빈 결과도 **짧게** 캐시 — 30초 위젯 regen 마다 재fetch 하는 쿼터
        # 낭비를 막되(리뷰 2026-07-06) 한 시간을 지우지는 않는다(#369).
        return _credit_split_cache(ck, {}, "nofield")
    out: dict = {}
    for mkt, fk in field.items():
        series = {}
        for it in raw:
            d = str(it.get("basDt") or "")
            v = _f(it.get(fk))
            if d and v is not None:
                series[d] = v / 1e8      # 원 → 억원
        ser = sorted(series.items())
        if ser:
            out[mkt] = ser
    # 잔고 합리성 가드 — 오분류 필드(비율·증감 등)가 빠져나온 경우 차단:
    # 각 시장 최신값은 전체(crdTrFingWhl) 미만 & 양수, 두 시장 합은 전체의
    # ±25% 이내여야. 위반 시 WARNING + 전체 드롭(틀린 숫자 노출 금지).
    #
    # ⚠️ **같은 날끼리 비교해야 한다**(2026-09-14). 옛 판은 전체는
    # `whole[max(whole)]`, 시장은 각자의 `s[-1]` 을 써서 **두 모집단이 다른
    # 날짜**였다(#45). 전체만 계속 오고 시장 필드가 한동안 끊기면, 그 사이
    # 전체가 자란 만큼 오차가 벌어져 ±25% 룰이 **멀쩡한 시계열을 통째로**
    # 드롭한다. 셋 다 값이 있는 가장 최근 날짜에서 잰다(없으면 판정하지
    # 않는다 — #99 창이 안 맞으면 판정 자체를 하지 말 것).
    #
    # ⚠️ 이게 사용자가 본 "코스피/코스닥 신용잔고 추이가 안나올때가 있어"의
    # **원인이라고 단정하지 않는다** — 샌드박스에선 KOFIA 를 못 쳐서 재지
    # 못했다(#12·#165). 구조적으로 틀린 비교라 그 자체로 고칠 값어치가 있고,
    # 진짜 갈래는 이제 `why` 가 화면에 말하므로 **다음 발생 때 출력이
    # 답한다**(#82 다음 출력이 곧 측정).
    whole = {d: v / 1e8 for d, v in
             ((str(it.get("basDt") or ""), _f(it.get("crdTrFingWhl")))
              for it in raw) if d and v is not None}
    why = ""
    if out and whole:
        common = set(whole)
        for _m, _s in out.items():
            common &= {d for d, _v in _s}
        if common:
            day = max(common)
            w_latest = whole[day]
            latest = {m: dict(s)[day] for m, s in out.items()}
            bad = [m for m, v in latest.items() if not (0 < v < w_latest)]
            if not bad and len(latest) == 2:
                tot = sum(latest.values())
                if not (0.75 * w_latest <= tot <= 1.25 * w_latest):
                    bad = list(latest)
            if bad:
                log.warning("kofia credit split: 합리성 가드 위반 %s "
                            "(%s latest=%s, whole=%.0f) — 드롭",
                            bad, day, latest, w_latest)
                out = {}
                why = "sanity"
        else:
            # 겹치는 날이 없으면 **검산이 성립하지 않는다** — 값을 버리지도
            # 축복하지도 않는다. 그 사실을 로그로 남긴다(#54·#12).
            log.info("kofia credit split: 전체·시장이 겹치는 날이 없어 "
                     "합리성 검산 생략")
    if not out and not why:
        # '0건' 과 '필드는 있는데 값을 못 읽음' 은 처방이 다르다(#82·#292).
        why = "empty" if not raw else "unparsed"
    elif out and len(out) < 2 and not why:
        # 부분 성공도 사유를 남긴다 — 안 그러면 없는 쪽 카드가 "사유 미기록"
        # 으로 떠 #369 가 지우려던 침묵이 그대로다(#43·#45).
        why = "partial"
    return _credit_split_cache(ck, out, why)


def credit_split_series_eok(n: int = 130) -> dict:
    """값만 필요한 자리용 얇은 래퍼 — **왜 비었는지**가 화면에 필요하면
    `credit_split_with_reason` 을 쓸 것(#129)."""
    return credit_split_with_reason(n)[0]


def _fmt_jo(won) -> str:
    """원 → 조/억 한국어 단위 (LLM 에 raw 원 미노출, 환각 방지)."""
    if won is None:
        return "—"
    if abs(won) >= 1e12:
        return f"{won / 1e12:.1f}조"
    if abs(won) >= 1e8:
        return f"{won / 1e8:.0f}억"
    return f"{won:,.0f}원"


def _pct(v) -> str:
    return "—" if v is None else f"{'+' if v >= 0 else ''}{v:.1f}%"


def market_liquidity_line() -> str | None:
    """예탁금+신용융자 한 줄 요약 (조 단위 · WoW/MoM). 둘 다 실패면 None.
    Daily Byte 시장총평 + KR 시장분석가 context 공용 — 시장 전체값·12h 캐시."""
    dep, mgn = market_deposit(), margin_balance()
    parts = []
    if dep:
        parts.append(f"투자자예탁금 {_fmt_jo(dep['latest'])}"
                     f"(WoW {_pct(dep['wow_pct'])}/MoM {_pct(dep['mom_pct'])})")
    if mgn:
        parts.append(f"신용융자 {_fmt_jo(mgn['latest'])}"
                     f"(WoW {_pct(mgn['wow_pct'])}/MoM {_pct(mgn['mom_pct'])})")
    return " · ".join(parts) if parts else None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    if "--minor" in sys.argv:
        tk2 = next((a for a in sys.argv[1:] if not a.startswith("--")), "005930.KS")
        print(f"=== 소액주주 RAW 진단 — {tk2} ===")
        _info = item_info(tk2)
        _crno = (_info or {}).get("crno")
        print(f"crno = {_crno}")
        # (a) crno 필터 / (b) corpNm 필터 / (c) 무필터 — 어느 게 200+items 인지
        for label, params in (
                ("crno", {"crno": _crno, "numOfRows": 5}),
                ("corpNm", {"corpNm": (_info or {}).get("corpNm", ""), "numOfRows": 5}),
                ("bizYear+crno", {"crno": _crno, "bizYear": "2024", "numOfRows": 5}),
                ("no-filter", {"numOfRows": 3})):
            raw = _fetch(_CGDISC[0], _CGDISC[1], dict(params))
            print(f"  [{label}] {len(raw)}행"
                  + (f" · {json.dumps(raw[0], ensure_ascii=False)[:300]}" if raw else ""))
        print("\nminority_holders():", json.dumps(minority_holders(tk2), ensure_ascii=False))
        print("dilution_events():", json.dumps(dilution_events(tk2), ensure_ascii=False)[:400])
        raise SystemExit(0)

    if "--lockup" in sys.argv:
        tk2 = next((a for a in sys.argv[1:] if not a.startswith("--")), "005930.KS")
        print(f"=== 의무보호예수 반환(lock-up) — {tk2} ===")
        rel = lockup_releases(tk2)
        print(f"수신 {len(rel)}건:")
        for r in rel[:15]:
            print(f"  {r['release_date']} · {r['shares']} 주 · {r['reason']}"
                  f" · 잔량 {r['remaining']}")
        raise SystemExit(0)

    if "--issu" in sys.argv:
        # 주식발행 공시(유통주식수) raw 필드 확인 — 유통주식수/자기주식수 key 확정
        tk2 = next((a for a in sys.argv[1:] if not a.startswith("--")), "005930.KS")
        print(f"=== 주식발행 공시정보 raw — {tk2} ===")
        rows = stock_issue_raw(tk2)
        print(f"수신 {len(rows)}행.")
        for r in rows[:2]:
            print(json.dumps(r, ensure_ascii=False))
        raise SystemExit(0)

    if "--market" in sys.argv:
        # 협회 종합통계 Base URL 자동탐색 (증시자금추이 op 로 200 확인)
        print("=== 금융투자협회 종합통계 Base URL 탐색 ===")
        hit = None
        for base in _KOFIA_BASE_CANDIDATES:
            _KOFIA_BASE = base
            raw = _fetch(base, _OP_DEPOSIT, {"numOfRows": 2})
            ok = bool(raw)
            print(f"  [{'OK' if ok else '  '}] {base.split('/1160100')[-1]}"
                  f"  → {len(raw)}행")
            if ok and not hit:
                hit = base
        if hit:
            _KOFIA_BASE = hit
            print(f"\n✅ Base 확정: {hit}")
            print("예탁금 추세:", json.dumps(market_deposit(), ensure_ascii=False))
            print("신용잔고 추세:", json.dumps(margin_balance(), ensure_ascii=False))
        else:
            print("\n→ 후보 모두 실패. Swagger 맨 위 '[ Base URL: ... ]' 한 줄을")
            print("  붙여주시면 확정합니다 (또는 .env FSC_KOFIA_BASE=...).")
        raise SystemExit(0)

    tk = sys.argv[1] if len(sys.argv) > 1 else "005930.KS"
    print(f"=== {tk} ===")
    print("item_info:", json.dumps(item_info(tk), ensure_ascii=False))
    s = price_series(tk)
    print(f"price_series: {len(s)}행")
    if s:
        print("  latest:", json.dumps(s[-1], ensure_ascii=False))
    print("rights_for (최근 21영업일):", json.dumps(rights_for(tk), ensure_ascii=False)[:600])
