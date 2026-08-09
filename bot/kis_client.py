"""한국투자증권 KIS Open API 클라이언트 (Step 2B A1).

7종 endpoint wrap:
 1. 현재가 (inquire-price) — yfinance 보다 정확한 KRX 공식 현재가
 2. 외인/기관/개인 순매수 (inquire-investor) — 당일 + 5일 누적
 3. 기관 주체별 순매수 (연기금/투신/은행/보험 분리)
 4. 외인 한도소진율 (inquire-foreign-investor)
 5. 신용잔고 + 대차잔고
 6. 프로그램 매매 (program-trade-by-stock)
 7. 공매도 현황 (short-sale)

인증: POST /oauth2/tokenP → access_token (24h). 토큰 disk cache
(~/.tradingagents/cache/kis_token.json) — 만료 1h 전 자동 갱신.

Rate limit: 초당 20건 / 일 10,000건 (무료 기준). 분석 1회당 5–7
호출 기준 일 1,000–2,000 분석 커버.

Graceful degradation: KIS_APP_KEY / KIS_APP_SECRET 미설정, 401,
timeout 모두 빈 dict + warning log 반환. Rule A guard (agent_utils)
가 데이터 미수집 시 fabrication 차단.

Caching: per-ticker 12h disk cache. 장 마감 후 수급 데이터는 당일
불변이므로 12h 충분.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.kis")

_BASE_PROD = "https://openapi.koreainvestment.com:9443"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "kis"
_TOKEN_CACHE = Path.home() / ".tradingagents" / "cache" / "kis_token.json"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 10
_TOKEN_REFRESH_MARGIN_SEC = 3600  # 만료 1h 전 갱신


# ─── helpers ────────────────────────────────────────────────────────────────

def _ticker_to_code(ticker: str) -> Optional[str]:
    """'005930.KS' / '035720.KQ' → '005930'. Non-KR → None."""
    if not ticker:
        return None
    t = ticker.upper()
    if not (t.endswith(".KS") or t.endswith(".KQ")):
        return None
    code = t.split(".")[0]
    return code if (len(code) == 6 and code.isdigit()) else None


def _mkt_div(ticker: str) -> str:
    """FID_COND_MRKT_DIV_CODE: 'J'=KOSPI, 'Q'=KOSDAQ."""
    return "Q" if ticker.upper().endswith(".KQ") else "J"


# ─── 해외주식 거래소 코드 매핑 (차트 라이브 현재가용) ──────────────────────────
# KIS 해외시세 EXCD. ⚠️ 대만(.TW)은 KIS 해외주식 미지원 → 폴백에서 제외.
_OVERSEAS_EXCD = {
    ".T":  "TSE",   # 도쿄
    ".HK": "HKS",   # 홍콩
    ".SS": "SHS",   # 상해
    ".SZ": "SZS",   # 심천
}


def _overseas_excd_symb(ticker: str) -> tuple[Optional[list], Optional[str]]:
    """ticker → (EXCD 후보 리스트, SYMB) 또는 (None, None)=미지원.

    JP/HK/CN 은 suffix 로 거래소가 정해지므로 단일 EXCD. 미국은 ticker 만으론
    상장 거래소(NAS/NYS/AMS)를 알 수 없어 후보 리스트 — world_quote 가 발견·
    캐시한 reutersCode suffix(.O=NASDAQ · .N/.K=NYSE · .A/.P=AMEX)로 우선순위
    조정, 없으면 NAS→NYS→AMS 순차. 호출부(_validate_live_price)가 직전 종가
    대비 밴드 검증하므로 잘못된 거래소 매칭값은 자동 reject 된다(무해)."""
    t = (ticker or "").strip().upper()
    if not t:
        return (None, None)
    for suf, excd in _OVERSEAS_EXCD.items():
        if t.endswith(suf):
            return ([excd], t[: -len(suf)])
    if "." not in t:   # 순수 심볼 = 미국
        order = ["NAS", "NYS", "AMS"]
        try:
            from bot import world_quote
            rc = (world_quote._rc_cache.get(t) or "").upper()
            if rc.endswith(".N") or rc.endswith(".K"):
                order = ["NYS", "NAS", "AMS"]
            elif rc.endswith(".A") or rc.endswith(".P"):
                order = ["AMS", "NAS", "NYS"]
        except Exception:
            pass
        return (order, t)
    return (None, None)   # .TW 등 미지원


def _cache_get(key: str, ttl_hours: float = _CACHE_TTL_HOURS) -> Optional[dict]:
    f = _CACHE_DIR / key
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h < ttl_hours:
            return json.loads(f.read_text())
    except Exception as exc:
        log.debug("kis cache read fail %s: %s", key, exc)
    return None


def _cache_put(key: str, value: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / key).write_text(json.dumps(value, ensure_ascii=False, default=str))
    except Exception as exc:
        log.debug("kis cache write fail %s: %s", key, exc)


def _app_key() -> str:
    return os.environ.get("KIS_APP_KEY", "").strip()


def _app_secret() -> str:
    return os.environ.get("KIS_APP_SECRET", "").strip()


# ─── OAuth2 token ────────────────────────────────────────────────────────────

def _get_token() -> Optional[str]:
    """Return valid access_token. Disk-cached; refreshes 1h before expiry."""
    app_key = _app_key()
    app_secret = _app_secret()
    if not app_key or not app_secret:
        log.warning("kis: KIS_APP_KEY or KIS_APP_SECRET not set")
        return None

    # Load cached token
    try:
        if _TOKEN_CACHE.exists():
            cached = json.loads(_TOKEN_CACHE.read_text())
            expires_at = cached.get("expires_at", 0)
            if time.time() < expires_at - _TOKEN_REFRESH_MARGIN_SEC:
                return cached.get("access_token")
    except Exception:
        pass

    # Issue new token
    try:
        resp = requests.post(
            f"{_BASE_PROD}/oauth2/tokenP",
            headers={"Content-Type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret,
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 86400))
        if token:
            payload = {
                "access_token": token,
                "expires_at": time.time() + expires_in,
            }
            _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_CACHE.write_text(json.dumps(payload))
            log.info("kis: token issued, expires_in=%ds", expires_in)
            return token
        log.warning("kis: token response missing access_token: %s", list(data.keys()))
    except Exception as exc:
        log.warning("kis: token fetch failed: %s", exc)
    return None


# ─── generic GET wrapper ─────────────────────────────────────────────────────

def _get(path: str, tr_id: str, params: dict, custtype: Optional[str] = None) -> Optional[dict]:
    token = _get_token()
    if not token:
        return None

    def _hdrs(tok: str) -> dict:
        h = {
            "authorization": f"Bearer {tok}",
            "appkey": _app_key(),
            "appsecret": _app_secret(),
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
        }
        if custtype:                       # 해외시세 등 일부 TR 은 custtype='P' 필요
            h["custtype"] = custtype
        return h

    try:
        resp = requests.get(
            f"{_BASE_PROD}{path}",
            headers=_hdrs(token),
            params=params,
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code == 401:
            # Token may have expired — clear cache and retry once
            try:
                _TOKEN_CACHE.unlink()
            except Exception:
                pass
            token2 = _get_token()
            if token2:
                resp = requests.get(
                    f"{_BASE_PROD}{path}",
                    headers=_hdrs(token2),
                    params=params,
                    timeout=_HTTP_TIMEOUT,
                )
        resp.raise_for_status()
        data = resp.json()
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            log.warning("kis: %s rt_cd=%s msg=%s", tr_id, rt_cd, data.get("msg1", ""))
            return None
        return data
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        log_fn = log.debug if status == 404 else log.warning
        log_fn("kis: %s http %s: %s", tr_id, status, exc)
        return None
    except Exception as exc:
        log.warning("kis: %s failed: %s", tr_id, exc)
        return None


# ─── KisClient ──────────────────────────────────────────────────────────────

class KisClient:
    """Per-ticker KIS data lookup. Instantiate once via get_kis()."""

    # 1. 현재가
    def get_current_price(self, ticker: str) -> Optional[dict]:
        """현재가 + 전일 대비. tr_id FHKST01010100.

        Returns: {price: int, change_pct: float, volume: int, high_52w: int, low_52w: int}
        """
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"price_{code}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code},
        )
        if not data:
            return None
        out = data.get("output") or {}
        # 2026-05-23 (010140.KS): expose 시가총액 / EPS / BPS / 상장주수
        # so the D1 Phase 3 yfinance-fallback chain in agent_utils can
        # use KIS as a 3rd fallback after yfinance + pykrx miss. KIS
        # `hts_avls` is 시가총액 in 억 원 (string); convert to KRW (int).
        # Rule applies to all KR analyses going forward — surfaced by
        # 010140 펀더 박스 '시가총액 N/A, PER N/A, PBR N/A' cascade.
        try:
            mc_eok = _float(out.get("hts_avls"))
            market_cap_krw = int(mc_eok * 1e8) if mc_eok else None
        except Exception:
            market_cap_krw = None
        result = {
            "price":      _int(out.get("stck_prpr")),
            "change_pct": _float(out.get("prdy_ctrt")),
            "volume":     _int(out.get("acml_vol")),
            "high_52w":   _int(out.get("w52_hgpr")),
            "low_52w":    _int(out.get("w52_lwpr")),
            "per":        _float(out.get("per")),
            "pbr":        _float(out.get("pbr")),
            "eps":        _float(out.get("eps")),
            "bps":        _float(out.get("bps")),
            "market_cap": market_cap_krw,
            "shares":     _int(out.get("lstn_stcn")),
            # 종목별 당일 상·하한가 (2026-06-05). price_sanity 의 glitch 임계를
            # 시장 하드코딩(KR ±35%) 대신 실제 일일 한도로 정밀화하는 데 사용.
            "upper_limit": _int(out.get("stck_mxpr")),
            "lower_limit": _int(out.get("stck_llam")),
        }
        _cache_put(cache_key, result)
        return result

    # 1b. 장중 실시간 현재가 (짧은 캐시 — 차트 라이브 현재가용)
    def get_realtime_price(self, ticker: str) -> Optional[int]:
        """장중 현재가(KRW int). 12h get_current_price 와 달리 **2분 캐시**라
        차트 라이브 현재가에 쓸 만큼 신선하다. yfinance fast_info(~15분 지연·
        KR 은 종종 EOD)보다 KR 에서 정확. KR 전용 — 비-KR ticker/creds 부재 시
        None(graceful). 호출 빈도는 차트층 5분 캐시 + 이 2분 캐시로 이중 bound."""
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"rtprice_{code}.json"
        cached = _cache_get(cache_key, ttl_hours=2 / 60.0)   # 2분
        if cached is not None:
            return cached.get("price")
        data = _get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code},
        )
        if not data:
            return None
        price = _int((data.get("output") or {}).get("stck_prpr"))
        if price is None:
            return None
        _cache_put(cache_key, {"price": price})
        return price

    # 1c. 해외주식(미국/일본/홍콩/중국) 장중 현재가 (차트 라이브용)
    def get_overseas_realtime_price(self, ticker: str) -> Optional[float]:
        """해외주식 현재가(float). KIS 해외시세 HHDFS00000300, 2분 캐시.
        미지원 시장(대만 등)/creds 부재/실패 시 None(graceful → Yahoo 폴백).

        ⚠️ KIS 무료 해외시세는 거래소·계정에 따라 **지연(~15분)** 일 수 있다
        (국내는 실시간). 실시간 여부는 VM 실측 필요 — 그래도 Yahoo 와 달리 IP
        서킷브레이커에 안 걸려 Yahoo 차단 시 신선한 값을 주는 이점이 있다.
        호출부(_validate_live_price)가 직전 종가 대비 밴드 검증하므로 잘못된
        거래소/심볼 매칭은 자동 reject 된다."""
        excds, symb = _overseas_excd_symb(ticker)
        if not excds or not symb:
            return None
        cache_key = f"rtovs_{ticker.upper().replace('.', '_')}.json"
        cached = _cache_get(cache_key, ttl_hours=2 / 60.0)   # 2분
        if cached is not None:
            return cached.get("price")
        for excd in excds:
            data = _get(
                "/uapi/overseas-price/v1/quotations/price",
                "HHDFS00000300",
                {"AUTH": "", "EXCD": excd, "SYMB": symb},
                custtype="P",
            )
            if not data:
                continue
            px = _float((data.get("output") or {}).get("last"))
            if px and px > 0:
                _cache_put(cache_key, {"price": px})
                return px
        return None

    # 1d. 해외주식 현재가상세 (PER/PBR/EPS/BPS/52주 고저 — 비-KR 펀더 fallback 원천)
    def get_overseas_price_detail(self, ticker: str) -> Optional[dict]:
        """해외주식 현재가상세(KIS HHDFS76200200), 12h 캐시. 미지원 시장(대만
        등)/creds 부재/실패 시 None(graceful).

        KR 은 D1 Phase 3(get_current_price 의 hts_avls/per/pbr/eps/bps +
        pykrx 백필, agent_utils.py)로 yfinance .info 결측 시 대체하는데,
        US/JP/HK/CN(SS/SZ) 은 이런 비-yfinance fallback 이 없음 — 이 함수가
        그 원천이 될 수 있음(2026-08-09 사용자 제공 KIS 문서 확인, 필드명
        전부 문서 그대로: h52p/h52d/l52p/l52d=52주 고저+일자, perx/pbrx/
        epsx/bpsx=PER/PBR/EPS/BPS, tomv=시가총액).
        ⚠️ 무료 해외시세라 미국은 무지연, HK/CN/JP 는 15분 지연(문서 명시).
        ⚠️ 문서 대조만 완료 — VM 실호출로 실제 응답 미검증. agent_utils.py
        펀더 파이프라인에 배선 전 VM 확인 선행 필요(라이브 미배선, 원천
        함수만 제공)."""
        excds, symb = _overseas_excd_symb(ticker)
        if not excds or not symb:
            return None
        cache_key = f"ovsdetail_{ticker.upper().replace('.', '_')}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        for excd in excds:
            data = _get(
                "/uapi/overseas-price/v1/quotations/price-detail",
                "HHDFS76200200",
                {"AUTH": "", "EXCD": excd, "SYMB": symb},
                custtype="P",
            )
            if not data:
                continue
            out = data.get("output") or {}
            last = _float(out.get("last"))
            if not last or last <= 0:
                continue
            result = {
                "price":         last,
                "open":          _float(out.get("open")),
                "high":          _float(out.get("high")),
                "low":           _float(out.get("low")),
                "prev_close":    _float(out.get("base")),
                "market_cap":    _float(out.get("tomv")),
                "per":           _float(out.get("perx")),
                "pbr":           _float(out.get("pbrx")),
                "eps":           _float(out.get("epsx")),
                "bps":           _float(out.get("bpsx")),
                "shares":        _int(out.get("shar")),
                "currency":      out.get("curr") or None,
                "high_52w":      _float(out.get("h52p")),
                "high_52w_date": out.get("h52d") or None,
                "low_52w":       _float(out.get("l52p")),
                "low_52w_date":  out.get("l52d") or None,
                "sector":        out.get("e_icod") or None,
                "volume":        _int(out.get("tvol")),
            }
            _cache_put(cache_key, result)
            return result
        return None

    # 2+3. 외인/기관/개인 + 기관 주체별
    def get_investor_flow(self, ticker: str) -> Optional[dict]:
        """외인/기관/개인 당일 + 5일 누적 순매수 + 기관 주체별.
        tr_id FHKST01010900.

        Returns:
        {
          today:   {foreign, institution, individual},   # 당일 (만원)
          5d:      {foreign, institution, individual},   # 5일 누적 (만원)
          inst_breakdown: {pension, trust, bank, insurance, etc_inst},  # 기관 주체별 5일
        }
        """
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"investor_{code}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code},
        )
        if not data:
            return None
        # FHKST01010900 output is a LIST of daily rows: [0]=today, up to 5 days
        raw_out = data.get("output")
        if not raw_out:
            return None
        rows = raw_out if isinstance(raw_out, list) else [raw_out]
        if not rows:
            return None
        today_r = rows[0]

        def _sum_rows(field: str) -> Optional[int]:
            vals = [_int(r.get(field)) for r in rows]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None

        result = {
            "today": {
                "foreign":     _int(today_r.get("frgn_ntby_qty")),
                "institution": _int(today_r.get("orgn_ntby_qty")),
                "individual":  _int(today_r.get("indv_ntby_qty")),
            },
            "5d": {
                "foreign":     _sum_rows("frgn_ntby_tr_pbmn"),
                "institution": _sum_rows("orgn_ntby_tr_pbmn"),
                "individual":  _sum_rows("indv_ntby_tr_pbmn"),
            },
            "inst_breakdown": {
                "pension":    _sum_rows("pnsn_ntby_tr_pbmn"),   # 연기금
                "trust":      _sum_rows("itrn_ntby_tr_pbmn"),   # 투신
                "bank":       _sum_rows("bank_ntby_tr_pbmn"),   # 은행
                "insurance":  _sum_rows("insu_ntby_tr_pbmn"),   # 보험
                "etc_inst":   _sum_rows("etcg_ntby_tr_pbmn"),   # 기타기관
                "private_eq": _sum_rows("samo_ntby_tr_pbmn"),   # 사모
            },
        }
        _cache_put(cache_key, result)
        return result

    # 4. 외인 한도소진율 — KIS Open API 미제공
    def get_foreign_limit(self, ticker: str) -> Optional[dict]:
        """외국인 보유 한도 + 소진율.

        KIS Open API 는 외국인 한도소진율 endpoint 를 제공하지 않음 (2026-05-22
        확인). 해당 데이터는 KRX (한국거래소) 데이터마켓에서 직접 조회 가능.
        대안: pykrx 또는 KRX MDC API 별도 통합 필요 (Step 2C 후속 작업).

        Returns: None (KIS API 미제공)
        """
        return None

    # 5. 신용잔고 일별추이 (daily-credit-balance)
    def get_credit_short_balance(self, ticker: str) -> Optional[dict]:
        """신용잔고 일별추이. tr_id FHPST04760000.

        Returns: {credit_balance_shares: int, credit_balance_amt: int,
                  credit_balance_pct: float, loan_balance_shares: int}
        """
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"credit_{code}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(
            "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
            "FHPST04760000",
            {
                "FID_COND_MRKT_DIV_CODE": _mkt_div(ticker),
                "FID_COND_SCR_DIV_CODE": "20476",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": "",
            },
        )
        if not data:
            return None
        raw_out = data.get("output")
        if not raw_out:
            return None
        rows = raw_out if isinstance(raw_out, list) else [raw_out]
        if not rows:
            return None
        today_r = rows[0]
        result = {
            "credit_balance_shares": _int(today_r.get("whol_loan_rmnd_stcn")),
            "credit_balance_amt":    _int(today_r.get("whol_loan_rmnd_amt")),
            "credit_balance_pct":    _float(today_r.get("whol_loan_rmnd_rate")),
            "loan_balance_shares":   _int(today_r.get("stln_rmnd_qty")),
        }
        _cache_put(cache_key, result)
        return result

    # 6. 프로그램 매매
    def get_program_trade(self, ticker: str) -> Optional[dict]:
        """당일 프로그램 매매 (차익 + 비차익). tr_id FHPPG04650100.

        Returns: {arb_buy: int, arb_sell: int, nonarb_buy: int, nonarb_sell: int,
                  arb_net: int, nonarb_net: int}  (만원)
        """
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"prog_{code}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(
            "/uapi/domestic-stock/v1/quotations/program-trade-by-stock",
            "FHPPG04650100",
            {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code},
        )
        if not data:
            return None
        out = data.get("output") or {}
        arb_buy  = _int(out.get("pgtr_seln_amt1"))   # 차익 매도 (역설적으로 seln=sell amt)
        arb_sell = _int(out.get("pgtr_shnu_amt1"))
        nb_buy   = _int(out.get("pgtr_seln_amt2"))
        nb_sell  = _int(out.get("pgtr_shnu_amt2"))

        def _net(a: Optional[int], b: Optional[int]) -> Optional[int]:
            if a is None and b is None:
                return None
            return (a or 0) - (b or 0)

        result = {
            "arb_buy":    arb_buy,
            "arb_sell":   arb_sell,
            "arb_net":    _net(arb_buy, arb_sell),
            "nonarb_buy":  nb_buy,
            "nonarb_sell": nb_sell,
            "nonarb_net":  _net(nb_buy, nb_sell),
        }
        _cache_put(cache_key, result)
        return result

    # 7. 공매도 일별추이 (daily-short-sale)
    def get_short_sale(self, ticker: str) -> Optional[dict]:
        """공매도 일별추이. tr_id FHPST04830000.

        Response output1 = 단일 요약 row, output2 = 일별 list.

        Returns: {short_qty: int, short_amt: int, short_ratio_pct: float,
                  avg_price: float}
        """
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"short_{code}.json"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(
            "/uapi/domestic-stock/v1/quotations/daily-short-sale",
            "FHPST04830000",
            {
                "FID_COND_MRKT_DIV_CODE": _mkt_div(ticker),
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_DATE_2": "",
            },
        )
        if not data:
            return None
        # output2 = 일별 행 list; [0] = 가장 최근 영업일
        raw_out2 = data.get("output2")
        if not raw_out2:
            return None
        rows = raw_out2 if isinstance(raw_out2, list) else [raw_out2]
        if not rows:
            return None
        today_r = rows[0]
        result = {
            "short_qty":          _int(today_r.get("ssts_cntg_qty")),
            "short_amt":          _int(today_r.get("ssts_tr_pbmn")),
            "short_ratio_pct":    _float(today_r.get("ssts_vol_rlim")),
            "avg_price":          _float(today_r.get("avrg_prc")),
        }
        _cache_put(cache_key, result)
        return result

    # 8. 당일 분봉 차트 (국내주식 당일분봉조회 FHKST03010200)
    def get_minute_chart(self, ticker: str, interval_min: int = 5) -> Optional[list]:
        """당일 분봉 OHLCV. interval_min: 1/5/10/15/30/60.

        Returns list of {time: 'HHMMSS', open, high, low, close, volume}
        sorted ascending (09:00→15:30). 5분 disk cache. KIS creds 부재/
        비-KR ticker → None (graceful).
        """
        code = _ticker_to_code(ticker)
        if not code:
            return None
        iv = str(interval_min)
        cache_key = f"minchart_{code}_{iv}.json"
        cached = _cache_get(cache_key, ttl_hours=5 / 60)
        if cached is not None:
            return cached

        all_bars: list[dict] = []
        cursor = "160000"
        for _ in range(10):
            data = _get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                "FHKST03010200",
                {
                    "FID_COND_MRKT_DIV_CODE": _mkt_div(ticker),
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_HOUR_1": cursor,
                    "FID_PW_DATA_INQR_DVSN": iv,
                    "FID_ETC_CLS_CODE": "",
                },
            )
            if not data:
                break
            rows = data.get("output2") or []
            if not rows:
                break
            for r in rows:
                t_str = (r.get("stck_cntg_hour") or "").strip()
                cl = _int(r.get("stck_prpr"))
                if not t_str or not cl:
                    continue
                all_bars.append({
                    "time": t_str,
                    "open": _int(r.get("stck_oprc")) or cl,
                    "high": _int(r.get("stck_hgpr")) or cl,
                    "low": _int(r.get("stck_lwpr")) or cl,
                    "close": cl,
                    "volume": _int(r.get("cntg_vol")) or 0,
                })
            last_t = rows[-1].get("stck_cntg_hour", "")
            if not last_t or last_t <= "090000" or last_t >= cursor:
                break
            cursor = last_t

        if len(all_bars) < 2:
            return None
        all_bars.sort(key=lambda x: x["time"])
        _cache_put(cache_key, all_bars)
        return all_bars

    # 9. 일봉 차트 (기간) — 차트 폴백용 (야후 미제공 종목, 사용자 2026-06-17
    #    '야후→네이버→KIS 순'). 국내 inquire-daily-itemchartprice FHKST03010100 /
    #    해외 dailyprice HHDFS76240000. ⚠️ tr_id/필드는 KIS 문서 기준이며 VM 실측
    #    전이라 graceful(실패·미지원 시 None → 호출부가 기존 '데이터 없음' 유지).
    #    단일 콜이라 국내 ~100영업일/해외 ~100건 — 폴백 용도엔 충분(MA200 부족분은
    #    _series_payload 가 자동 생략). 12h 디스크 캐시.
    def get_daily_chart(self, ticker: str, days: int = 366) -> Optional[list]:
        """일봉 OHLCV. Returns list of {date:'YYYY-MM-DD', open, high, low, close,
        volume} 오름차순. creds 부재/미지원/실패 → None (graceful)."""
        from datetime import date as _date, timedelta as _td
        code = _ticker_to_code(ticker)
        end = _date.today()
        start = end - _td(days=int(days) + 7)
        ck = f"daily_{(code or ticker).replace('.', '_')}.json"
        cached = _cache_get(ck, ttl_hours=12)
        if cached is not None:
            return cached.get("bars")
        bars: list = []
        try:
            if code:                                    # 국내
                data = _get(
                    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    "FHKST03010100",
                    {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code,
                     "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                     "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                     "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
                )
                for r in ((data or {}).get("output2") or []):
                    ds = (r.get("stck_bsop_date") or "").strip()
                    cl = _float(r.get("stck_clpr"))
                    if len(ds) != 8 or cl is None:
                        continue
                    bars.append({
                        "date": f"{ds[:4]}-{ds[4:6]}-{ds[6:]}",
                        "open": _float(r.get("stck_oprc")) or cl,
                        "high": _float(r.get("stck_hgpr")) or cl,
                        "low": _float(r.get("stck_lwpr")) or cl,
                        "close": cl, "volume": _int(r.get("acml_vol")) or 0,
                    })
            else:                                       # 해외
                excds, symb = _overseas_excd_symb(ticker)
                for excd in (excds or []):
                    data = _get(
                        "/uapi/overseas-price/v1/quotations/dailyprice",
                        "HHDFS76240000",
                        {"AUTH": "", "EXCD": excd, "SYMB": symb, "GUBN": "0",
                         "BYMD": end.strftime("%Y%m%d"), "MODP": "1"},
                        custtype="P",
                    )
                    rows = (data or {}).get("output2") or []
                    for r in rows:
                        ds = (r.get("xymd") or "").strip()
                        cl = _float(r.get("clos"))
                        if len(ds) != 8 or cl is None:
                            continue
                        bars.append({
                            "date": f"{ds[:4]}-{ds[4:6]}-{ds[6:]}",
                            "open": _float(r.get("open")) or cl,
                            "high": _float(r.get("high")) or cl,
                            "low": _float(r.get("low")) or cl,
                            "close": cl, "volume": _int(r.get("tvol")) or 0,
                        })
                    if bars:
                        break
        except Exception as exc:
            log.warning("kis daily_chart %s: %s", ticker, exc)
            return None
        if len(bars) < 2:
            return None
        bars.sort(key=lambda x: x["date"])
        _cache_put(ck, {"bars": bars})
        return bars

    # 10. 국내 업종/지수 현재지수 (VKOSPI 등 — 사용자 2026-08-08). KIS 공식문서
    #     ([국내주식] 업종/기타 카테고리, "국내업종 현재지수[v1_국내주식-063]")
    #     확인 완료: URL /uapi/domestic-stock/v1/quotations/inquire-index-price,
    #     tr_id FHPUP02100000, FID_COND_MRKT_DIV_CODE="U"(업종), FID_INPUT_ISCD=
    #     지수코드(코스피 0001/코스닥 1001/코스피200 2001 등). 응답 output.
    #     bstp_nmix_prpr = 업종 지수 현재가(문서 확인 필드명, 추측 아님).
    #     모의투자 미지원 — 반드시 실전 KIS_APP_KEY/SECRET 필요.
    #     ⚠️ VKOSPI 자체 지수코드는 아직 미확인(포탈 업종코드 다운로드 확인 중) —
    #     index_code 인자화해 graceful(무응답/무데이터 시 None). 5분 캐시(스냅샷).
    def get_domestic_index_price(self, index_code: str) -> Optional[float]:
        """국내 업종/지수 현재가(포인트, float). creds 부재/미지원 코드/실패 시
        None (graceful)."""
        ck = f"idxprice_{index_code}.json"
        cached = _cache_get(ck, ttl_hours=5 / 60.0)
        if cached is not None:
            return cached.get("value")
        data = _get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code},
        )
        out = (data or {}).get("output") or {}
        value = _float(out.get("bstp_nmix_prpr"))
        if value is None:
            return None
        _cache_put(ck, {"value": value})
        return value

    # 11. 국내 업종/지수 기간별시세(일봉) — VKOSPI 차트용. KIS 공식문서
    #     ("국내주식업종기간별시세(일/주/월/년)[v1_국내주식-021]") 확인 완료:
    #     URL /uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice,
    #     tr_id FHKUP03500100(실전/모의 동일), FID_COND_MRKT_DIV_CODE="U",
    #     FID_PERIOD_DIV_CODE="D". 응답 output2[].stck_bsop_date/bstp_nmix_prpr.
    #     ⚠️ 문서에 "한 번의 호출에 최대 50건까지" 명시 — 200일치는 날짜구간
    #     분할 페이지네이션 필요(70일 단위 청크, 50영업일 캡 안전마진).
    #     1h 캐시(2026-08-08 '시장유동성 섹션 전체 1시간 단위로').
    def get_domestic_index_daily(self, index_code: str, days: int = 200) -> Optional[list]:
        """국내 업종/지수 일봉. Returns list of {date:'YYYY-MM-DD', close} 오름차순
        (중복일자 제거). creds 부재/미지원 코드/실패 시 None (graceful)."""
        from datetime import date as _date, timedelta as _td
        end = _date.today()
        ck = f"idxdaily_{index_code}.json"
        cached = _cache_get(ck, ttl_hours=1)
        if cached is not None:
            return cached.get("bars")
        by_date: dict[str, float] = {}
        chunk_end = end
        remaining = int(days) + 7
        while remaining > 0:
            chunk_days = min(remaining, 70)
            chunk_start = chunk_end - _td(days=chunk_days)
            try:
                data = _get(
                    "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                    "FHKUP03500100",
                    {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code,
                     "FID_INPUT_DATE_1": chunk_start.strftime("%Y%m%d"),
                     "FID_INPUT_DATE_2": chunk_end.strftime("%Y%m%d"),
                     "FID_PERIOD_DIV_CODE": "D"},
                )
            except Exception as exc:
                log.warning("kis index_daily %s: %s", index_code, exc)
                data = None
            rows = (data or {}).get("output2") or []
            for r in rows:
                ds = (r.get("stck_bsop_date") or "").strip()
                cl = _float(r.get("bstp_nmix_prpr"))
                if len(ds) != 8 or cl is None:
                    continue
                by_date[f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"] = cl
            if not rows:
                break
            chunk_end = chunk_start - _td(days=1)
            remaining -= chunk_days
        if len(by_date) < 2:
            return None
        bars = [{"date": d, "close": c} for d, c in sorted(by_date.items())]
        _cache_put(ck, {"bars": bars})
        return bars

    def get_all(self, ticker: str) -> dict:
        """7종 모든 데이터를 한 dict로. 각 필드가 None이면 해당 endpoint 실패."""
        return {
            "price":          self.get_current_price(ticker),
            "investor_flow":  self.get_investor_flow(ticker),
            "foreign_limit":  self.get_foreign_limit(ticker),
            "credit_short":   self.get_credit_short_balance(ticker),
            "program_trade":  self.get_program_trade(ticker),
            "short_sale":     self.get_short_sale(ticker),
        }


# ─── singleton ──────────────────────────────────────────────────────────────

_instance: Optional[KisClient] = None


def get_kis() -> KisClient:
    global _instance
    if _instance is None:
        _instance = KisClient()
    return _instance


# ─── formatter ──────────────────────────────────────────────────────────────

def format_kis_block(data: dict) -> str:
    """KIS 7종 데이터 → instrument_context 주입용 텍스트 블록."""
    lines: list[str] = []

    # 현재가
    price = data.get("price") or {}
    if price.get("price"):
        p = price["price"]
        chg = price.get("change_pct")
        chg_str = f" ({'+' if chg and chg >= 0 else ''}{chg:.2f}%)" if chg is not None else ""
        lines.append(f"• KIS 현재가: ₩{p:,}{chg_str}")
        if price.get("high_52w"):
            lines.append(f"  52주 고가: ₩{price['high_52w']:,} / 저가: ₩{price.get('low_52w', 0):,}")
        if price.get("per"):
            per_str = f"  PER {price['per']:.1f}배"
            if price.get("pbr"):
                per_str += f" / PBR {price['pbr']:.2f}배"
            lines.append(per_str)

    # 외인/기관/개인 flow
    flow = data.get("investor_flow") or {}
    if flow:
        today = flow.get("today") or {}
        five_d = flow.get("5d") or {}
        inst_bd = flow.get("inst_breakdown") or {}

        def _fmt_wanwon(v) -> str:
            # Always render in 억원 to prevent LLM unit-conversion errors.
            # Raw API values are in 만원; divide by 100,000 to get 억원.
            # 경동나비엔 2026-05-22: "+11,730만원" was misread as "117억원"
            # (100x error) by the PM because 만원 unit was left for the LLM
            # to convert. Now Python does it: 11,730만원 → +0.12억원.
            if v is None:
                return "N/A"
            sign = "+" if v >= 0 else ""
            return f"{sign}{v / 100_000:.2f}억원"

        if any(today.values()):
            lines.append(
                f"• 당일 순매수: 외인 {_fmt_wanwon(today.get('foreign'))} /"
                f" 기관 {_fmt_wanwon(today.get('institution'))} /"
                f" 개인 {_fmt_wanwon(today.get('individual'))}"
            )
        if any(five_d.values()):
            five_d_foreign    = five_d.get("foreign") or 0
            five_d_inst       = five_d.get("institution") or 0
            five_d_individual = five_d.get("individual") or 0
            lines.append(
                f"• 5일 누적: 외인 {_fmt_wanwon(five_d.get('foreign'))} /"
                f" 기관 {_fmt_wanwon(five_d.get('institution'))} /"
                f" 개인 {_fmt_wanwon(five_d.get('individual'))}"
            )
            # RULE 10 threshold check: ±100억 미만은 noise — dominant variable 인용 불가.
            # Python pre-computes this so the LLM doesn't have to convert units.
            for label_k, val_k in (("외인", five_d_foreign), ("기관", five_d_inst)):
                if abs(val_k) < 10_000_000:  # < 100억 (10,000,000만원)
                    lines.append(
                        f"  ⚠️ RULE 10: {label_k} 5일 누적"
                        f" {_fmt_wanwon(val_k)} — ±100억 미만이므로"
                        f" dominant variable 인용 불가 (noise level)"
                    )
            # Step 2C: 개인 떠받침 패턴 (개인 +100억 + 외인/기관 한쪽 -100억).
            # 한국 시장 classic 약세 동학 — Python 계산으로 LLM 누락 방지.
            if (five_d_individual >= 10_000_000 and
                    (five_d_foreign <= -10_000_000 or five_d_inst <= -10_000_000)):
                contrast = []
                if five_d_foreign <= -10_000_000:
                    contrast.append(f"외인 {_fmt_wanwon(five_d_foreign)}")
                if five_d_inst <= -10_000_000:
                    contrast.append(f"기관 {_fmt_wanwon(five_d_inst)}")
                lines.append(
                    f"  ⚠️ RULE 10 [Step 2C]: Retail 떠받침 패턴 — 개인"
                    f" {_fmt_wanwon(five_d_individual)} vs {' + '.join(contrast)}."
                    f" 5거래일+α 하방 risk dominant"
                )
        # 기관 주체별 — 의미있는 값만 출력
        bd_parts = []
        label_map = {
            "pension":    "연기금",
            "trust":      "투신",
            "bank":       "은행",
            "insurance":  "보험",
            "private_eq": "사모",
            "etc_inst":   "기타기관",
        }
        for key, label in label_map.items():
            v = inst_bd.get(key)
            if v:
                bd_parts.append(f"{label} {_fmt_wanwon(v)}")
        if bd_parts:
            lines.append("• 기관 주체별 5일: " + " / ".join(bd_parts))
            # Step 2C: 투신 5일 -50억 = 펀드 환매 신호 (외인 매도 5일 선행 패턴).
            trust_val = inst_bd.get("trust") or 0
            if trust_val <= -5_000_000:  # ≤ -50억 (5,000,000만원)
                lines.append(
                    f"  ⚠️ RULE 10 [Step 2C]: 투신 5일 {_fmt_wanwon(trust_val)} —"
                    f" 펀드 환매 dominant, 외인 sell-off 선행 leading indicator"
                )

    # 외인 한도소진율
    fl = data.get("foreign_limit") or {}
    if fl.get("exhaustion_pct") is not None:
        pct = fl["exhaustion_pct"]
        warn = ""
        if pct >= 95:
            warn = " ⚠️ 한도 거의 소진 — 외인 추가 매수 불가"
        elif pct >= 80:
            warn = " (한도 여유 적음)"
        lines.append(f"• 외인 한도소진율: {pct:.1f}%{warn}")

    # 신용 + 대차
    cs = data.get("credit_short") or {}
    if cs.get("credit_balance_pct") is not None:
        cr_pct = cs["credit_balance_pct"]
        warn = " ⚠️ 신용 과열 — 청산 압력 주의" if cr_pct >= 4.0 else ""
        lines.append(f"• 신용잔고율: {cr_pct:.2f}%{warn}")
    if cs.get("loan_balance_shares"):
        lines.append(f"• 대차잔고: {cs['loan_balance_shares']:,}주")

    # 프로그램 매매
    pt = data.get("program_trade") or {}
    if pt.get("arb_net") is not None or pt.get("nonarb_net") is not None:
        def _fmt_prog(v) -> str:
            if v is None:
                return "N/A"
            sign = "+" if v >= 0 else ""
            return f"{sign}{v/100_000:.1f}억원" if abs(v) >= 100_000 else f"{sign}{v:,}만원"
        lines.append(
            f"• 프로그램: 차익 {_fmt_prog(pt.get('arb_net'))} /"
            f" 비차익 {_fmt_prog(pt.get('nonarb_net'))}"
        )
        # Step 2C: 비차익 ±200억 = 알고리즘 systematic flow dominant.
        # 차익(arb)은 선물-현물 베이시스 hedging 으로 단기 방향 신호 약함 —
        # 비차익(nonarb)이 외인/기관 systematic buy/sell 의 합산.
        nonarb = pt.get("nonarb_net")
        if isinstance(nonarb, (int, float)) and abs(nonarb) >= 20_000_000:  # ±200억
            direction = "매수" if nonarb > 0 else "매도"
            lines.append(
                f"  ⚠️ RULE 10 [Step 2C]: 프로그램 비차익 {_fmt_prog(nonarb)} —"
                f" 알고리즘 systematic {direction} dominant, 단기 momentum"
                f" {'강화' if nonarb > 0 else '가속'}"
            )

    # 공매도
    ss = data.get("short_sale") or {}
    if ss.get("short_ratio_pct") is not None:
        sr = ss["short_ratio_pct"]
        warn = " ⚠️ 공매도 압력 높음" if sr >= 15 else ""
        lines.append(f"• 공매도 비율: {sr:.1f}%{warn}")
        if ss.get("short_qty"):
            lines.append(f"• 공매도 거래량: {ss['short_qty']:,}주")

    return "\n".join(lines)


# ─── interpretation guide ────────────────────────────────────────────────────

KIS_INTERP_GUIDE = """\
KIS 단기 수급 해석 가이드 (5거래일 horizon):
• 외인 5일 누적 순매수 방향이 단기 가격 방향의 최우선 예측 변수.
  +100억 이상 = 단기 매수 압력 dominant / -100억 이하 = 단기 매도 압력.
• 연기금 5일 순매수 양수 = 중기 지지 신호 (연기금은 저점 분할 매수 패턴).
• 신용잔고율 4% 이상 → 반대매매 청산 압력 risk 5거래일 내 현실화 가능.
• 공매도 비율 15% 이상 → 숏 압력 dominant, 결론에 명시 의무.
• [Step 2C] 외인 한도소진율 95% 이상 → 외인 추가 매수 ceiling impact,
  buy flow +값이어도 신규 매수 여력 없음, 외인 dominant 매수 신호 무효.
• [Step 2C] 개인 +100억 동시에 외인/기관 -100억 → Retail 떠받침 패턴,
  외인/기관 차익실현 vs 개인 매수, 한국 시장 classic 약세 동학.
• [Step 2C] 투신 5일 -50억 이상 → 펀드 환매 dominant, 외인 sell-off
  선행 leading indicator (한국 mutual fund redemption cycle).
• [Step 2C] 프로그램 비차익 ±200억 → 외인/기관 알고리즘 systematic flow
  합산. 비차익(nonarb)이 방향성 신호 — 차익(arb)은 헤징이라 신호 약함.
• 프로그램 차익 매도 큰 날 (-500억 이상) → 인덱스 편입/편출 또는 파생 만기
  효과 — 펀더멘털 무관한 수급 왜곡, 단기 매도 공백 후 반등 가능성.
이 데이터는 KIS API에서 직접 수신한 수치이므로 그대로 인용하라.
다음 패턴 금지: 수치가 없으면 'KR 외국인 매수세 지속' 같은 generic 추측 금지.
외인 한도소진율은 KIS API foreign_limit endpoint 로 fetch 완료 (Step 2B).\
"""


# ─── numeric helpers ─────────────────────────────────────────────────────────

def _int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        s = str(v).replace(",", "").strip()
        return int(float(s)) if s else None
    except (ValueError, TypeError):
        return None


def _float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


# ─── 국내주식 52주 신고가/신저가 근접 순위 (FHPST01870000) ───────────────────
# 라이브 검증 2026-06-13: FID_PRC_CLS_CODE 0=신고가 근접·1=신저가 근접.
# J=KOSPI+KOSDAQ 통합(아이큐어 175250=KOSDAQ 가 J 결과 포함). custtype='P' 필요.
# 전 시장 스캔 후 근접순 상위 ~30씩(API 캡). ETF/ETN/채권/리츠 제외(실종목만).
_NHL_PATH = "/uapi/domestic-stock/v1/ranking/near-new-highlow"
_NHL_TR = "FHPST01870000"
_NHL_ETF_KW = (
    "KODEX", "TIGER", "ACE", "SOL", "KBSTAR", "ARIRANG", "HANARO", "KOSEF",
    "TIMEFOLIO", "RISE", "PLUS", "KIWOOM", "히어로즈", "마이티", "파워",
    "ETN", "인버스", "레버리지", "선물", "국채", "통안", "회사채", "채권",
    "리츠", "REIT", "배당다우존스", "S&P", "STOXX", "나스닥", "단기통안채",
    "스팩", "SPAC",   # SPAC 제외 (사용자 2026-06-13 — 신탁가 고정이라 항상 '근접')
)


def _nhl_is_etf_bond(code: str, name: str) -> bool:
    """ETF/ETN/채권/리츠/SPAC 판별 — 실종목 신고저만 남김. 영문 prefix 코드
    (Q…=ETN) + 브랜드/상품/SPAC 키워드."""
    if code and not code[:1].isdigit():       # Q610056 류 = ETN/ETF
        return True
    nm = name or ""
    return any(kw in nm for kw in _NHL_ETF_KW)


def fetch_kr_new_highlow(period: int = 250, count: int = 60) -> dict:
    """국내주식 52주 신고가·신저가 근접 (KIS FHPST01870000, PRC 0/1 각 1콜).
    {high:[...], low:[...]} — 항목 {code, name, price, pct, vol, near_rate}.
    ETF/ETN/채권 제외. creds 부재/실패 시 빈 리스트(graceful). count=표시 상한."""
    tok = _get_token()
    if not tok:
        return {"high": [], "low": []}
    out = {"high": [], "low": []}
    for prc, key, rate_field in (("0", "high", "hprc_near_rate"),
                                 ("1", "low", "lwpr_near_rate")):
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20187",
            "FID_INPUT_ISCD": "0000", "FID_RANK_SORT_CLS_CODE": "0",
            "FID_INPUT_CNT_1": "1", "FID_INPUT_CNT_2": str(period),
            "FID_PRC_CLS_CODE": prc, "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "",
            "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
            "FID_DIV_CLS_CODE": "0", "FID_APLY_RANG_PRC_1": "",
            "FID_APLY_RANG_PRC_2": "", "FID_APLY_RANG_VOL": "",
        }
        headers = {
            "authorization": f"Bearer {tok}", "appkey": _app_key(),
            "appsecret": _app_secret(), "tr_id": _NHL_TR, "custtype": "P",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            r = requests.get(_BASE_PROD + _NHL_PATH, headers=headers,
                             params=params, timeout=_HTTP_TIMEOUT)
            rows = (r.json() or {}).get("output") or []
        except Exception as exc:
            log.warning("kis new-highlow %s: %s", key, exc)
            continue
        seen: set = set()
        for o in rows:
            code = str(o.get("mksc_shrn_iscd") or "").strip()
            name = str(o.get("hts_kor_isnm") or "").strip()
            if not code or code in seen or _nhl_is_etf_bond(code, name):
                continue
            seen.add(code)
            out[key].append({
                "code": code, "name": name,
                "price": _float(o.get("stck_prpr")),
                "pct": _float(o.get("prdy_ctrt")),
                "vol": _int(o.get("acml_vol")),
                "near_rate": _float(o.get(rate_field)),
            })
            if len(out[key]) >= count:
                break
    return out


# ─── 해외주식 신고/신저가 랭킹 (2026-08-09 사용자 제공 KIS 문서, 미검증) ────────

_OVSHL_PATH = "/uapi/overseas-stock/v1/ranking/new-highlow"
_OVSHL_TR = "HHDFS76300000"


def fetch_overseas_new_highlow(excd: str, is_high: bool = True,
                                nday: str = "6", vol_rang: str = "0",
                                gubn2: str = "1") -> Optional[list]:
    """해외주식 신고/신저가 랭킹 (KIS HHDFS76300000), 거래소 1개씩(excd:
    NYS/NAS/AMS/HKS/SHS/SZS/TSE 등), 30분 캐시. nday: KIS enum
    0=5일 1=10일 2=20일 3=30일 4=60일 5=120일 6=52주 7=1년(문서 그대로).
    gubn2: '1'=돌파유지(디폴트) '0'=일시돌파 포함(노이즈↑). creds 부재/실패
    시 None(graceful).

    ⚠️ 국내(KR) 동일계열 near-new-highlow 랭킹이 실사용 결과 상위 캡(~30)이
    ETF/SPAC 에 잠식돼 실종목이 1~3개뿐이라 폐기된 전례 있음
    (`intl_highlow._compute_kr_kis`, 2026-06-13 legacy 처리) — 해외판도 같은
    문제일 가능성 있어 **VM 실호출로 실종목 비율 확인 전엔 라이브 배선 금지**
    (2026-08-09 문서 대조만 완료, kis_client 미배선 상태로 원천 함수만 제공)."""
    tok = _get_token()
    if not tok:
        return None
    cache_key = f"ovshl_{excd}_{'h' if is_high else 'l'}_{nday}_{gubn2}.json"
    cached = _cache_get(cache_key, ttl_hours=0.5)
    if cached is not None:
        return cached.get("rows")
    data = _get(
        _OVSHL_PATH, _OVSHL_TR,
        {"KEYB": "", "AUTH": "", "EXCD": excd,
         "GUBN": "1" if is_high else "0", "GUBN2": gubn2,
         "NDAY": nday, "VOL_RANG": vol_rang},
        custtype="P",
    )
    if not data:
        return None
    rows = data.get("output2") or []
    out = [{
        "symbol": r.get("symb"), "name": r.get("name"),
        "price": _float(r.get("last")), "pct": _float(r.get("rate")),
        "vol": _int(r.get("tvol")), "ename": r.get("ename"),
    } for r in rows if r.get("symb")]
    _cache_put(cache_key, {"rows": out})
    return out
