"""한국투자증권 KIS Open API 클라이언트 (Step 2B A1).

7종 endpoint wrap:
 1. 현재가 (inquire-price) — yfinance 보다 정확한 KRX 공식 현재가
 2. 외인/기관/개인 순매수 (inquire-investor) — 최근 거래일 하루 + 5거래일 누적
 3. (기관 주체별 — 이 TR 에 없음. 옛 판이 읽던 이름은 공식 응답에 없었다, #433)
 4. 외인 한도소진율 — KIS 미제공(항상 None)
 5. 신용(융자)잔고 + 대주잔고 (daily-credit-balance)
 6. 프로그램 매매 — 종목별 일별, 전체 합계 (program-trade-by-stock-daily)
 7. 공매도 일별추이 (daily-short-sale)

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from bot.env_keys import env_key as _env_key

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
    """FID_COND_MRKT_DIV_CODE — KIS 공식 샘플은 국내 시세·수급 TR 전부에서 이 값을
    ``J:KRX, NX:NXT, UN:통합`` 으로만 적는다(코스피·코스닥 구분이 아니라 거래소 구분 —
    github.com/koreainvestment/open-trading-api ``examples_llm/domestic_stock/*``,
    2026-10-04 확인). 2026-10-04 까지 코스닥에 공식 값이 아닌 ``Q`` 를 보냈다
    (실수 #433). 원천이 실제로 J 를 코스닥에 받는지는 ``bot.scripts.kis_flow_audit``
    가 J·Q 를 나란히 물어 매일 잰다. ``ticker`` 는 호출부 호환으로 받는다."""
    return "J"


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


# ⚠️ 공용 헬퍼로 읽는다(실수 #23). raw `os.environ` 이면 `load_dotenv()` 를
# 부르는 **봇 엔트리포인트에서만** 자격증명이 보이고, 진단·크론(`python -m
# bot.scripts.…`)에서는 키가 `.env` 에 **있는데도** '미설정'이 된다 —
# 2026-08-19 vol_probe 가 "KIS_APP_KEY not set" 을 6번 찍으면서 드러났다
# (VKOSPI 값은 디스크 캐시로 나와 있어 더 헷갈렸다).
def _app_key() -> str:
    return _env_key("KIS_APP_KEY")


def _app_secret() -> str:
    return _env_key("KIS_APP_SECRET")


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


# ─── 수급 4종(투자자·신용·공매도·프로그램) 공통 ──────────────────────────────
# ⚠️ 2026-10-04 까지 이 네 메서드는 KIS 공식 샘플과 다른 필드 이름을 읽었다 —
# 개인 ``indv_*`` 와 기관 세부 ``pnsn_*``·``itrn_*``… 는 주식현재가 투자자 TR 의
# 응답 목록에 없고, 대주 잔고는 ``stln_rmnd_qty`` 가 아니라 ``whol_stln_rmnd_stcn``
# 이며, 프로그램매매는 TR·필드 이름이 모두 달랐다. 그래서 그 칸들은 늘 None 이었고
# (분석 프롬프트의 '개인 N/A'), '당일 순매수' 는 **수량(주)** 을 만원으로 읽어 억원으로
# 바꿔 싣고 있었다. 필드 이름은 공식 샘플의 COLUMN_MAPPING 에서 왔다 —
# github.com/koreainvestment/open-trading-api ``examples_llm/domestic_stock/
# {inquire_investor,daily_credit_balance,daily_short_sale,
# program_trade_by_stock_daily}/chk_*.py`` (2026-10-04 확인).
# 금액 칸의 단위는 그 샘플이 밝히지 않으므로 응답 자체의 항등식으로 잰다
# (``_calibrate_unit``). 실제 응답이 이 가정과 맞는지는 매일
# ``bot.scripts.kis_flow_audit`` 가 잰다(실수 #433).
_FLOW_SCHEMA = 2
_FLOW_WINDOW = 5          # 'N거래일 누적' — 원천이 주는 행 수와 무관하게 최근 5거래일
_UNIT_CANDIDATES = (1, 1_000, 10_000, 1_000_000, 100_000_000)
_INV_WHO = (("foreign", "frgn"), ("institution", "orgn"), ("individual", "prsn"))
_KST = timezone(timedelta(hours=9))
_PENDING_TTL_HOURS = 1    # 원천이 아직 안 채운 날(장중 당일)이 있던 응답은 1시간만 믿는다


def _flow_cache_get(key: str) -> Optional[dict]:
    """수급 캐시는 판(``schema``)까지 맞아야 쓴다 — 옛 모양 캐시(최대 12시간)가 새
    소비자에게 가면 키·단위가 틀린 채로 실린다(실수 #21b). 원천이 아직 안 채운 날이
    있던 응답(``pending``)은 1시간만 믿는다 — 12시간을 믿으면 장 마감 뒤 채워진 당일
    값을 밤까지 못 본다."""
    cached = _cache_get(key)
    if not (isinstance(cached, dict) and cached.get("schema") == _FLOW_SCHEMA):
        return None
    if cached.get("pending"):
        try:
            age_h = (time.time() - (_CACHE_DIR / key).stat().st_mtime) / 3600
        except OSError:
            return None
        if age_h >= _PENDING_TTL_HOURS:
            return None
    return cached


def _rows_of(data: Optional[dict], key: str = "output") -> list:
    raw = (data or {}).get(key)
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _dated(rows: list, field: str) -> list:
    """``(YYYY-MM-DD, 행)`` 을 최신 날짜부터. 원천의 행 순서에 기대지 않는다 —
    옛 판의 '[0] = 오늘' 은 문서에 없는 가정이었다. 날짜를 못 읽는 행은 버린다."""
    out = []
    for r in rows:
        s = str(r.get(field) or "").strip()
        if len(s) == 8 and s.isdigit():
            out.append((f"{s[:4]}-{s[4:6]}-{s[6:]}", r))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


_MISSING_WARNED: set = set()


def _missing(tr_id: str, row: dict, fields) -> list:
    """기대한 필드 중 응답에 없는 것. 원천이 이름을 바꾸면 그 칸이 조용히 None 이
    된다(2026-10-04 까지 그랬다) — 같은 조합은 프로세스당 한 번 경고한다."""
    miss = [f for f in fields if f not in row]
    if miss and (tr_id, tuple(miss)) not in _MISSING_WARNED:
        _MISSING_WARNED.add((tr_id, tuple(miss)))
        log.warning("kis: %s 응답에 기대한 필드 %d개가 없습니다: %s — 원천이 이름을 "
                    "바꿨을 수 있습니다", tr_id, len(miss), ", ".join(miss[:8]))
    return miss


def _calibrate_unit(samples) -> tuple:
    """원천 금액 칸의 단위(원으로 바꾸는 배수)를 응답 자체에서 잰다 → (배수|None, 사유).

    ``samples`` = (수량, 가격, 금액) 묶음 — 금액 × 단위 ≈ 수량 × 가격. 공식 샘플이
    단위를 밝히지 않아 가정하면 100배 어긋난다(옛 판은 만원이라 가정했다). **총**
    매수·매도 수량과 대금을 넘길 것 — 순매수는 매수·매도가 상쇄돼 작아지고 부호가 갈려
    비가 흔들린다. 하루 평균 체결가는 종가와 상·하한가(±30%) 안이라 비는 참 단위의
    0.5~2배 안에 들고, 후보끼리는 10배 이상 떨어져 그 창이 겹치지 않는다. 표본이 둘
    미만이거나 3분의 2 가 한 후보에 모이지 않으면 단위를 **모른다**고 돌려준다."""
    ratios = []
    for q, p, a in samples:
        if q and p and a and q > 0 and p > 0 and a > 0:
            ratios.append(q * p / a)
    if len(ratios) < 2:
        return None, f"표본 {len(ratios)}개 — 금액 단위를 잴 수 없습니다"
    ratios.sort()
    med = ratios[len(ratios) // 2]
    for c in _UNIT_CANDIDATES:
        if 0.5 <= med / c <= 2:
            agree = sum(1 for r in ratios if 0.5 <= r / c <= 2)
            if agree * 3 >= len(ratios) * 2:
                return c, f"표본 {len(ratios)}개 중 {agree}개가 ×{c:,}"
            return None, f"표본이 갈립니다(×{c:,} 에 {agree}/{len(ratios)}개)"
    return None, (f"중앙 비 {med:,.1f} 가 어느 후보(×1·×1천·×1만·×100만·×1억)와도 "
                  "맞지 않습니다")


def _sum_all(vals) -> Optional[int]:
    """창의 칸이 하나라도 비면 합을 만들지 않는다 — 4일 합을 '5거래일 누적'이라
    부르면 거짓말이다(실수 #99)."""
    vals = list(vals)
    if not vals or any(v is None for v in vals):
        return None
    return sum(vals)


def _won(v: Optional[int], unit: Optional[int]) -> Optional[int]:
    return v * unit if (v is not None and unit) else None


def parse_investor_flow(data: Optional[dict]) -> Optional[dict]:
    """주식현재가 투자자(FHKST01010900) 응답 → 수급 dict(판 ``_FLOW_SCHEMA``).

    ``latest`` = 원천이 채운 가장 최근 거래일 하루(``qty`` 주 · ``won`` 원),
    ``window`` = 최근 ``_FLOW_WINDOW`` 거래일 순매수 합(원). 당일 행은 장 종료 후에야
    채워지므로(공식 유의사항) 장중엔 그 행이 비어 있다 — 매수·매도 거래량이 하나도 없는
    행은 건너뛰고, 가장 최근에 채워진 날보다 **뒤**의 빈 행 수를 ``pending`` 에 센다.
    그래서 ``latest.date`` 가 어제일 수 있고, 화면·프롬프트는 그 날짜를 적는다."""
    rows = _dated(_rows_of(data), "stck_bsop_date")

    def _filled(r: dict) -> bool:
        return any(_int(r.get(f"{p}_{s}_vol"))
                   for _, p in _INV_WHO for s in ("shnu", "seln"))

    filled = [(d, r) for d, r in rows if _filled(r)]
    if not filled:
        return None
    unit, unit_note = _calibrate_unit(
        (_int(r.get(f"{p}_{s}_vol")), _int(r.get("stck_clpr")),
         _int(r.get(f"{p}_{s}_tr_pbmn")))
        for _, r in filled for _, p in _INV_WHO for s in ("shnu", "seln"))
    latest_d, latest = filled[0]
    win = filled[:_FLOW_WINDOW]
    miss = _missing("FHKST01010900", latest,
                    ["stck_clpr"] + [f"{p}_{k}" for _, p in _INV_WHO
                                     for k in ("ntby_qty", "ntby_tr_pbmn",
                                               "shnu_vol", "shnu_tr_pbmn",
                                               "seln_vol", "seln_tr_pbmn")])
    return {
        "schema": _FLOW_SCHEMA,
        "asof": latest_d,
        "unit_won": unit,
        "unit_note": unit_note,
        "latest": {
            "date": latest_d,
            "qty": {k: _int(latest.get(f"{p}_ntby_qty")) for k, p in _INV_WHO},
            "won": {k: _won(_int(latest.get(f"{p}_ntby_tr_pbmn")), unit)
                    for k, p in _INV_WHO},
        },
        "window": {
            "from": win[-1][0], "to": win[0][0], "days": len(win),
            "won": {k: _sum_all(_won(_int(r.get(f"{p}_ntby_tr_pbmn")), unit)
                                for _, r in win)
                    for k, p in _INV_WHO},
        },
        "pending": sum(1 for d, _ in rows if d > latest_d),
        "missing": miss,
    }


def parse_credit_balance(data: Optional[dict]) -> Optional[dict]:
    """신용잔고 일별추이(FHPST04760000) — 가장 최근 매매일 한 행. 잔고 **금액**은 화면이
    쓰지 않아 싣지 않는다(단위를 재지 않은 값을 남기면 나중에 누가 추측해 쓴다)."""
    rows = _dated(_rows_of(data), "deal_date")
    if not rows:
        return None
    d, r = rows[0]
    miss = _missing("FHPST04760000", r, ("whol_loan_rmnd_stcn", "whol_loan_rmnd_rate",
                                          "whol_stln_rmnd_stcn", "whol_stln_rmnd_rate"))
    return {
        "schema": _FLOW_SCHEMA,
        "asof": d,
        "credit_balance_shares": _int(r.get("whol_loan_rmnd_stcn")),
        "credit_balance_pct": _float(r.get("whol_loan_rmnd_rate")),
        "credit_short_shares": _int(r.get("whol_stln_rmnd_stcn")),
        "credit_short_pct": _float(r.get("whol_stln_rmnd_rate")),
        "missing": miss,
    }


def parse_short_sale(data: Optional[dict]) -> Optional[dict]:
    """공매도 일별추이(FHPST04830000) — ``output2`` 의 가장 최근 영업일 한 행."""
    rows = _dated(_rows_of(data, "output2"), "stck_bsop_date")
    if not rows:
        return None
    d, r = rows[0]
    miss = _missing("FHPST04830000", r, ("ssts_cntg_qty", "ssts_vol_rlim",
                                          "ssts_tr_pbmn_rlim"))
    return {
        "schema": _FLOW_SCHEMA,
        "asof": d,
        "short_qty": _int(r.get("ssts_cntg_qty")),
        "short_ratio_pct": _float(r.get("ssts_vol_rlim")),        # 공매도 거래량 비중
        "short_amt_ratio_pct": _float(r.get("ssts_tr_pbmn_rlim")),  # 공매도 거래대금 비중
        "missing": miss,
    }


def parse_program_daily(data: Optional[dict]) -> Optional[dict]:
    """종목별 프로그램매매추이(일별, FHPPG04650201) — **전체 합계** 순매수. 이 TR 은
    차익·비차익을 나누지 않는다(옛 판은 공식 응답에 없는 필드로 차익·비차익을
    읽었다). 모양은 ``parse_investor_flow`` 의 ``latest``·``window`` 와 같다."""
    rows = _dated(_rows_of(data), "stck_bsop_date")
    filled = [(d, r) for d, r in rows
              if _int(r.get("whol_smtn_shnu_vol")) or _int(r.get("whol_smtn_seln_vol"))]
    if not filled:
        return None
    unit, unit_note = _calibrate_unit(
        (_int(r.get(f"whol_smtn_{s}_vol")), _int(r.get("stck_clpr")),
         _int(r.get(f"whol_smtn_{s}_tr_pbmn")))
        for _, r in filled for s in ("shnu", "seln"))
    latest_d, latest = filled[0]
    win = filled[:_FLOW_WINDOW]
    miss = _missing("FHPPG04650201", latest,
                    ("stck_clpr", "whol_smtn_ntby_qty", "whol_smtn_ntby_tr_pbmn",
                     "whol_smtn_shnu_vol", "whol_smtn_shnu_tr_pbmn",
                     "whol_smtn_seln_vol", "whol_smtn_seln_tr_pbmn"))
    return {
        "schema": _FLOW_SCHEMA,
        "asof": latest_d,
        "unit_won": unit,
        "unit_note": unit_note,
        "latest": {"date": latest_d,
                   "qty": _int(latest.get("whol_smtn_ntby_qty")),
                   "won": _won(_int(latest.get("whol_smtn_ntby_tr_pbmn")), unit)},
        "window": {"from": win[-1][0], "to": win[0][0], "days": len(win),
                   "won": _sum_all(_won(_int(r.get("whol_smtn_ntby_tr_pbmn")), unit)
                                   for _, r in win)},
        "pending": sum(1 for d, _ in rows if d > latest_d),
        "missing": miss,
    }


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

    # 2. 외인/기관/개인 순매수 (inquire-investor)
    def get_investor_flow(self, ticker: str) -> Optional[dict]:
        """외인·기관·개인 순매수 — 원천이 채운 가장 최근 거래일 하루(수량·금액) +
        최근 5거래일 금액 합. tr_id FHKST01010900. 모양은 ``parse_investor_flow``.

        기관 세부(연기금·투신…)는 이 TR 의 응답에 없다 — 옛 판이 읽던 ``pnsn_*`` 등은
        공식 샘플 목록에 없는 이름이라 늘 None 이었다(실수 #433)."""
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"investor_{code}.json"
        cached = _flow_cache_get(cache_key)
        if cached is not None:
            return cached
        data = _get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code},
        )
        result = parse_investor_flow(data)
        if result:
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
        """신용잔고 일별추이(FHPST04760000) — 신용(융자)·대주 잔고 주수와 비율.
        모양은 ``parse_credit_balance``.

        공식 샘플은 결제일자(``FID_INPUT_DATE_1``)를 [필수]로 적는다 — 오늘(KST)을 먼저
        보내고, 행이 없으면 옛 판처럼 빈 값으로 한 번 더 묻는다. 어느 쪽을 원천이 받는지는
        ``bot.scripts.kis_flow_audit`` 가 잰다."""
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"credit_{code}.json"
        cached = _flow_cache_get(cache_key)
        if cached is not None:
            return cached
        result = None
        for d1 in (datetime.now(_KST).strftime("%Y%m%d"), ""):
            data = _get(
                "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
                "FHPST04760000",
                {
                    "FID_COND_MRKT_DIV_CODE": _mkt_div(ticker),
                    "FID_COND_SCR_DIV_CODE": "20476",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": d1,
                },
            )
            result = parse_credit_balance(data)
            if result:
                break
        if result:
            _cache_put(cache_key, result)
        return result

    # 6. 프로그램 매매 — 종목별 일별 (program-trade-by-stock-daily)
    def get_program_trade(self, ticker: str) -> Optional[dict]:
        """종목별 프로그램매매추이(일별, FHPPG04650201) — 전체 합계 순매수, 최근 거래일
        하루 + 최근 5거래일 합. 모양은 ``parse_program_daily``.

        옛 판은 체결 TR(FHPPG04650100)을 부르면서 공식 응답 목록에 없는 필드
        (``pgtr_*``)로 차익·비차익을 읽었고, 응답(공식 샘플은 목록으로 받는다)을 dict 로
        다뤘다. 이 TR 은 차익·비차익을 나누지 않는다(실수 #433)."""
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"prog_{code}.json"
        cached = _flow_cache_get(cache_key)
        if cached is not None:
            return cached
        data = _get(
            "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
            "FHPPG04650201",
            {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code,
             "FID_INPUT_DATE_1": ""},
        )
        result = parse_program_daily(data)
        if result:
            _cache_put(cache_key, result)
        return result

    # 7. 공매도 일별추이 (daily-short-sale)
    def get_short_sale(self, ticker: str) -> Optional[dict]:
        """공매도 일별추이(FHPST04830000) — 가장 최근 영업일의 공매도 수량·비중.
        모양은 ``parse_short_sale``. 시작·종료일자는 공식 샘플에서도 선택이다."""
        code = _ticker_to_code(ticker)
        if not code:
            return None
        cache_key = f"short_{code}.json"
        cached = _flow_cache_get(cache_key)
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
        result = parse_short_sale(data)
        if result:
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
        # ⚠️ 캐시 키에 **days 포함** — 없으면 200봉 요청과 400봉 요청이 같은
        # 파일을 공유해 먼저 쓴 쪽 길이가 상대에게 서빙된다(메인 대시보드는
        # 200, 시장타이밍은 1년 창이 필요해 400). 그러면 '1년' 칸이 조용히
        # 사라지거나 차트가 갑자기 길어진다(2026-08-16 독립 조사).
        ck = f"idxdaily_{index_code}_{int(days)}.json"
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


def kis_ready() -> bool:
    """KIS 자격증명이 있나 — 수급 블록이 키 없이 조회마다 '미설정' 경고를 찍지 않게
    먼저 본다."""
    return bool(_app_key() and _app_secret())


def collect_kis_flow(ticker: str) -> dict:
    """수급 탭(종목 페이지)과 스냅샷이 같이 쓰는 KIS 수급 4종 — 받은 것만 담는다
    (키 없음 = 빈 dict). 키: ``investor_flow``·``credit``·``short_sale``·``program``.

    ⚠️ 2026-10-04 까지 이 블록은 ``stock_snapshot`` 과 ``dashboard`` 에 **복제**돼
    있었고, 둘 다 ``KisClient`` 에 없는 ``_ready()`` 를 불러 AttributeError 가 DEBUG
    로그에 삼켜졌다 — 수급 탭의 KIS 칸은 한 번도 채워진 적이 없다(실수 #433).
    한 곳에 두고, 레포 클래스에 없는 메서드를 부르는 자리는 회귀(전수 스캔)가 막는다.
    한 조회의 실패가 나머지를 지우지 않게 하나씩 감싼다(실수 #315)."""
    if not kis_ready():
        return {}
    kis = get_kis()
    out: dict = {}
    for key, fn in (("investor_flow", kis.get_investor_flow),
                    ("credit", kis.get_credit_short_balance),
                    ("short_sale", kis.get_short_sale),
                    ("program", kis.get_program_trade)):
        try:
            value = fn(ticker)
        except Exception as exc:                       # noqa: BLE001
            log.warning("kis: %s %s 실패: %s", key, ticker, exc)
            continue
        if value:
            out[key] = value
    return out


# ─── formatter ──────────────────────────────────────────────────────────────

def _fmt_eok(v: Optional[int]) -> str:
    """원 → 억원 문자열. 파이썬이 환산한다 — LLM 에 단위 변환을 맡기면 100배 틀린다
    (경동나비엔 2026-05-22: '+11,730만원' 을 PM 이 '117억원' 으로 읽었다)."""
    if v is None:
        return "N/A"
    return f"{'+' if v >= 0 else ''}{v / 1e8:,.2f}억원"


def _fmt_shares(v: Optional[int]) -> str:
    if v is None:
        return "N/A"
    return f"{'+' if v >= 0 else ''}{v:,}주"


_EOK_100 = 10_000_000_000          # 100억원(원 단위) — RULE 10 noise 경계


def format_kis_block(data: dict) -> str:
    """KIS 데이터 → instrument_context 주입용 텍스트 블록.

    수급 4종은 판(``_FLOW_SCHEMA``)이 맞는 것만 싣는다 — 옛 모양은 수량을 만원으로
    읽던 판이라 그대로 실으면 거짓 숫자다. 줄마다 그 값의 **기준일**을 적는다 —
    당일 투자자별 수급은 장 종료 후에야 나오므로 장중엔 전 거래일 값이다."""
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

    # 외인/기관/개인 순매수
    flow = data.get("investor_flow") or {}
    if flow.get("schema") == _FLOW_SCHEMA:
        lat = flow.get("latest") or {}
        win = flow.get("window") or {}
        qty = lat.get("qty") or {}
        won = lat.get("won") or {}
        wwon = win.get("won") or {}
        d = lat.get("date") or "?"
        if any(v is not None for v in qty.values()):
            lines.append(
                f"• 순매수 수량 ({d} 하루): 외인 {_fmt_shares(qty.get('foreign'))} /"
                f" 기관 {_fmt_shares(qty.get('institution'))} /"
                f" 개인 {_fmt_shares(qty.get('individual'))}"
            )
        if flow.get("unit_won"):
            if any(v is not None for v in won.values()):
                lines.append(
                    f"• 순매수 금액 ({d} 하루): 외인 {_fmt_eok(won.get('foreign'))} /"
                    f" 기관 {_fmt_eok(won.get('institution'))} /"
                    f" 개인 {_fmt_eok(won.get('individual'))}"
                )
            if any(v is not None for v in wwon.values()):
                n = win.get("days")
                f5, i5, p5 = wwon.get("foreign"), wwon.get("institution"), wwon.get("individual")
                lines.append(
                    f"• {n}거래일 누적 ({win.get('from')}~{win.get('to')}):"
                    f" 외인 {_fmt_eok(f5)} / 기관 {_fmt_eok(i5)} / 개인 {_fmt_eok(p5)}"
                )
                # RULE 10: ±100억 미만은 noise — dominant variable 인용 불가.
                # 파이썬이 원 단위로 미리 판정해 LLM 이 단위를 환산하지 않게 한다.
                for label_k, val_k in (("외인", f5), ("기관", i5)):
                    if val_k is not None and abs(val_k) < _EOK_100:
                        lines.append(
                            f"  ⚠️ RULE 10: {label_k} {n}거래일 누적 {_fmt_eok(val_k)}"
                            f" — ±100억 미만이므로 dominant variable 인용 불가 (noise level)"
                        )
                # Step 2C: 개인 떠받침 패턴 (개인 +100억 + 외인/기관 한쪽 -100억).
                contrast = [f"{lbl} {_fmt_eok(v)}" for lbl, v in (("외인", f5), ("기관", i5))
                            if v is not None and v <= -_EOK_100]
                if p5 is not None and p5 >= _EOK_100 and contrast:
                    lines.append(
                        f"  ⚠️ RULE 10 [Step 2C]: Retail 떠받침 패턴 — 개인"
                        f" {_fmt_eok(p5)} vs {' + '.join(contrast)}."
                        f" 5거래일+α 하방 risk dominant"
                    )
        else:
            lines.append(f"  (금액 단위를 확정하지 못해 금액·누적은 싣지 않습니다 —"
                         f" {flow.get('unit_note') or '사유 미상'})")
        if flow.get("pending"):
            lines.append(f"  (원천이 아직 채우지 않은 최근 {flow['pending']}일은 뺐습니다 —"
                         f" 당일 투자자별 수급은 장 종료 후 제공)")

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

    # 신용·대주 잔고
    cs = data.get("credit_short") or {}
    if cs.get("schema") == _FLOW_SCHEMA:
        d = cs.get("asof") or "?"
        if cs.get("credit_balance_pct") is not None:
            cr_pct = cs["credit_balance_pct"]
            warn = " ⚠️ 신용 과열 — 청산 압력 주의" if cr_pct >= 4.0 else ""
            lines.append(f"• 신용잔고율 ({d}): {cr_pct:.2f}%{warn}")
        if cs.get("credit_short_shares"):
            lines.append(f"• 대주잔고(신용 매도) ({d}): {cs['credit_short_shares']:,}주")

    # 프로그램 매매 — 전체 합계(이 TR 은 차익·비차익을 나누지 않는다)
    pt = data.get("program_trade") or {}
    if pt.get("schema") == _FLOW_SCHEMA and pt.get("unit_won"):
        lat = pt.get("latest") or {}
        win = pt.get("window") or {}
        if lat.get("won") is not None:
            lines.append(f"• 프로그램 순매수 ({lat.get('date')} 하루, 전체 합계 —"
                         f" 차익·비차익 구분 없음): {_fmt_eok(lat['won'])}")
        if win.get("won") is not None:
            lines.append(f"• 프로그램 {win.get('days')}거래일 누적"
                         f" ({win.get('from')}~{win.get('to')}): {_fmt_eok(win['won'])}")

    # 공매도
    ss = data.get("short_sale") or {}
    if ss.get("schema") == _FLOW_SCHEMA and ss.get("short_ratio_pct") is not None:
        d = ss.get("asof") or "?"
        sr = ss["short_ratio_pct"]
        warn = " ⚠️ 공매도 압력 높음" if sr >= 15 else ""
        lines.append(f"• 공매도 비율 (거래량 대비, {d}): {sr:.1f}%{warn}")
        if ss.get("short_qty"):
            lines.append(f"• 공매도 거래량 ({d}): {ss['short_qty']:,}주")

    return "\n".join(lines)


# ─── interpretation guide ────────────────────────────────────────────────────

KIS_INTERP_GUIDE = """\
KIS 단기 수급 해석 가이드 (5거래일 horizon):
• 각 줄의 괄호 안 날짜가 그 값의 기준일이다 — 당일 투자자별 수급은 장 종료 후에야
  나오므로 장중엔 전 거래일 값이다. 기준일을 '오늘'이라고 바꿔 쓰지 말 것.
• 외인 5거래일 누적 순매수 방향이 단기 가격 방향의 최우선 예측 변수.
  +100억 이상 = 단기 매수 압력 dominant / -100억 이하 = 단기 매도 압력.
• 신용잔고율 4% 이상 → 반대매매 청산 압력 risk 5거래일 내 현실화 가능.
• 공매도 비율(거래량 대비) 15% 이상 → 숏 압력 dominant, 결론에 명시 의무.
• [Step 2C] 개인 +100억 동시에 외인/기관 -100억 → Retail 떠받침 패턴,
  외인/기관 차익실현 vs 개인 매수, 한국 시장 classic 약세 동학.
• 프로그램 순매수는 **전체 합계**다(이 데이터는 차익·비차익을 나누지 않는다) —
  비차익 규칙의 근거로 쓰지 말 것.
• 이 블록에 없는 것: 기관 세부(연기금·투신 등)·외인 한도소진율 — KIS 가 이 조회로
  주지 않는다. 다른 블록(KRX 등)에도 그 수치가 없으면 언급하지 말 것.
이 데이터는 KIS API에서 직접 수신한 수치이므로 그대로 인용하라.
다음 패턴 금지: 수치가 없으면 'KR 외국인 매수세 지속' 같은 generic 추측 금지.\
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
    # 캐시 먼저(형제 함수들과 동일 순서) — creds 일시 부재/토큰 발급 실패에도
    # 신선한 캐시가 있으면 서빙. 토큰 확인은 _get() 내부에 이미 있어 중복 불요.
    cache_key = (f"ovshl_{excd}_{'h' if is_high else 'l'}"
                 f"_{nday}_{gubn2}_{vol_rang}.json")
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
