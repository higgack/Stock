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


def _cache_get(key: str) -> Optional[dict]:
    f = _CACHE_DIR / key
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h < _CACHE_TTL_HOURS:
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

def _get(path: str, tr_id: str, params: dict) -> Optional[dict]:
    token = _get_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"{_BASE_PROD}{path}",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": _app_key(),
                "appsecret": _app_secret(),
                "tr_id": tr_id,
                "Content-Type": "application/json; charset=utf-8",
            },
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
                    headers={
                        "authorization": f"Bearer {token2}",
                        "appkey": _app_key(),
                        "appsecret": _app_secret(),
                        "tr_id": tr_id,
                        "Content-Type": "application/json; charset=utf-8",
                    },
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
        }
        _cache_put(cache_key, result)
        return result

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
