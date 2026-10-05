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
timeout 모두 빈 값 + warning log 반환. Rule A guard (agent_utils)
가 데이터 미수집 시 fabrication 차단. 수급 4종은 TR 마다 차단기(전송 실패
2회 → 300초 열림)를 거치고, 토큰 발급 실패는 60초 동안 다시 묻지 않는다.

Caching: per-ticker 디스크 캐시. 현재가(``get_current_price``)는 12h — 실시간가·분봉 등은
각자 더 짧다. 수급 4종은 판이 이름에 있는
``{접두}_v{판}_{코드}.json`` 이고 12h · 잠정 행이 있던 응답 1h · 확정 행이 없던
답 30분만 믿으며, 가장 최근 KRX 하루 끝(``after_close`` 시작 20:00) 이전에 쓴
파일은 버린다 — 장 마감 뒤 채워진 값을 밤까지 못 보지 않게(``_flow_cache_get``).
오늘(KST) 행은 하루 끝 전엔 잠정이고, 하루 끝 뒤인데 오늘 행이 아직 없어도 잠정으로
센다(그래야 그 응답이 1시간만 산다 — ``_split_rows``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
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
    """FID_COND_MRKT_DIV_CODE — 코스닥 종목에도 ``J``.

    KIS 공식 샘플(github.com/koreainvestment/open-trading-api
    ``examples_llm/domestic_stock/*``, HEAD 277ec0e · 2026-09-28)은 이 값을 TR 마다
    다르게 적는다 — 수급 4종만 봐도 투자자 ``J:KRX, NX:NXT`` · 신용·공매도
    ``J: 주식`` · 프로그램 일별 ``J:KRX,NX:NXT,UN:통합`` 이다(코스피·코스닥 구분이
    아니다). 어느 샘플도 이 칸의 ``Q`` 를 코스닥으로 적지 않는다 — 이 칸에 Q 가
    나오는 곳(등락률 순위 ``chk_fluctuation``)은 Q 를 **ETF** 로 적고, 코스피 K·
    코스닥 Q 는 다른 칸(``fid_mrkt_cls_code``)의 값이다. 2026-10-04 까지 코스닥에
    ``Q`` 를 보냈다(실수 #433). J 가 코스닥을 포함한다는 실측은 52주 랭킹
    TR(FHPST01870000, 2026-06-13 — 아래 주석)뿐이고 수급 TR 에서 잰 것은 아니다:
    ``bot.scripts.kis_flow_audit`` 가 J·Q 를 나란히 물어 매일 잰다. 이 함수는 수급
    4종만이 아니라 현재가·실시간·분봉·일봉 조회도 쓴다. ``ticker`` 는 호출부
    호환으로 받는다."""
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

_TOKEN_FAIL_COOL_SEC = 60     # 발급 실패 뒤 이만큼은 다시 POST 하지 않는다(리뷰 M7)
_TOKEN_FAIL_UNTIL = 0.0


def _get_token() -> Optional[str]:
    """Return valid access_token. Disk-cached; refreshes 1h before expiry.

    발급이 실패하면 1분 동안은 다시 묻지 않는다 — 인증 서버가 죽은 동안 조회마다
    10초짜리 POST 를 다시 기다리면 상세 페이지가 TR 수만큼 붙잡힌다(리뷰 M7)."""
    global _TOKEN_FAIL_UNTIL
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

    if time.time() < _TOKEN_FAIL_UNTIL:
        return None

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
            _TOKEN_FAIL_UNTIL = 0.0
            return token
        log.warning("kis: token response missing access_token: %s", list(data.keys()))
    except Exception as exc:
        log.warning("kis: token fetch failed: %s", exc)
    _TOKEN_FAIL_UNTIL = time.time() + _TOKEN_FAIL_COOL_SEC
    return None


# ─── generic GET wrapper ─────────────────────────────────────────────────────

_TRANSPORT_FAIL = ("http5xx", "timeout", "network")


def _get_ex(path: str, tr_id: str, params: dict,
            custtype: Optional[str] = None) -> tuple:
    """원천 조회 → ``(응답 | None, 사유)``. 사유 = ``{"kind", "status", "msg"}`` —
    kind ∈ ok · token · http4xx · http5xx · timeout · network · rt_cd · json · error.

    ``_get`` 은 응답만 돌려주는 얇은 래퍼다(다른 호출부·테스트 호환). 실패를 None
    하나로 접으면 'TR 경로가 틀렸다(404)' 와 '원천 장애(5xx·타임아웃)' 와 '키
    문제(rt_cd)' 가 같은 모양이 된다 — 처방이 다르다(#82). 수급 4종의 차단기와
    일일 감사(``bot.scripts.kis_flow_audit``)가 이 사유를 쓴다(2026-10-04 리뷰 M5:
    감사가 404 를 DEBUG 로그라 못 봤다)."""
    token = _get_token()
    if not token:
        return None, {"kind": "token", "status": None,
                      "msg": "토큰 없음 — 자격증명 미설정이거나 발급 실패(bot.kis 경고 참조)"}

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
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = _error_body(exc.response)
        log_fn = log.debug if status == 404 else log.warning
        log_fn("kis: %s http %s: %s %s", tr_id, status, exc, body)
        kind = "http5xx" if isinstance(status, int) and status >= 500 else "http4xx"
        return None, {"kind": kind, "status": status,
                      "msg": f"HTTP {status}" + (f" — {body}" if body else "")}
    except requests.exceptions.Timeout as exc:
        log.warning("kis: %s timeout: %s", tr_id, exc)
        return None, {"kind": "timeout", "status": None,
                      "msg": f"응답 시간 초과({_HTTP_TIMEOUT}초)"}
    except requests.exceptions.ConnectionError as exc:
        log.warning("kis: %s 연결 실패: %s", tr_id, exc)
        return None, {"kind": "network", "status": None, "msg": "연결 실패"}
    except ValueError as exc:                                  # JSON 해석 실패
        log.warning("kis: %s JSON 아님: %s", tr_id, exc)
        return None, {"kind": "json", "status": None, "msg": "응답이 JSON 이 아님"}
    except Exception as exc:                                   # noqa: BLE001
        log.warning("kis: %s failed: %s", tr_id, exc)
        return None, {"kind": "error", "status": None, "msg": f"{type(exc).__name__}"}
    if not isinstance(data, dict):
        log.warning("kis: %s 응답이 dict 가 아님: %s", tr_id, type(data).__name__)
        return None, {"kind": "json", "status": 200, "msg": "응답 모양이 dict 가 아님"}
    rt_cd = data.get("rt_cd", "")
    if rt_cd != "0":
        msg1 = str(data.get("msg1", "") or "").strip()
        log.warning("kis: %s rt_cd=%s msg=%s", tr_id, rt_cd, msg1)
        return None, {"kind": "rt_cd", "status": 200, "msg": f"rt_cd={rt_cd} {msg1}".strip()}
    return data, {"kind": "ok", "status": 200, "msg": ""}


def _get(path: str, tr_id: str, params: dict, custtype: Optional[str] = None) -> Optional[dict]:
    return _get_ex(path, tr_id, params, custtype)[0]


def _error_body(resp) -> str:
    """HTTP 오류 응답 본문의 업무 코드·메시지(``msg_cd``·``msg1``) — 버리면 5xx 로 온 업무
    오류(예: 초당 건수 초과)가 '원천 장애' 와 같은 모양이 된다(2026-10-04 델타 리뷰 L3 —
    KIS 가 업무 오류를 5xx 로 주는지는 재지 않았다). JSON 이 아니면 빈 문자열."""
    try:
        j = resp.json() if resp is not None else None
    except Exception:                                          # noqa: BLE001
        return ""
    if not isinstance(j, dict):
        return ""
    parts = [str(j.get(k) or "").strip() for k in ("msg_cd", "msg1")]
    return " ".join(x for x in parts if x)[:120]


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
#
# 판 3(2026-10-04 독립 리뷰 H2·M2·M3): '거래량이 0 이면 원천 미제공' 으로 버리던
# 판 2 가 거래 0 인 확정일(거래 정지·프로그램 매매가 없던 날)을 창에서 빼 9거래일
# 구간을 '5거래일' 이라 불렀다 → 미제공은 **날짜로** 가른다(오늘 행만). 금액 단위는
# 응답 전체가 아니라 **화면에 싣는 행**으로 재고 행마다 대조한다(오래된 행이 다수결로
# 최근 행을 이기지 않게). 오늘 행은 신용·공매도·프로그램도 확정 전엔 잠정이다.
_FLOW_SCHEMA = 3
_FLOW_WINDOW = 5          # 'N거래일 누적' — 원천이 주는 행 수와 무관하게 최근 5거래일
_UNIT_CANDIDATES = (1, 1_000, 10_000, 1_000_000, 100_000_000)
_INV_WHO = (("foreign", "frgn"), ("institution", "orgn"), ("individual", "prsn"))
_KST = timezone(timedelta(hours=9))
_PENDING_TTL_HOURS = 1    # 오늘 행이 아직 잠정이던 응답은 1시간만 믿는다
_NONE_TTL_HOURS = 0.5     # 원천이 답했는데 값을 만들 행이 없던 응답은 30분만 믿는다
_FLOW_PURGE_AGE_SEC = 48 * 3600   # 수급 캐시 파일은 12시간 뒤엔 안 읽힌다 — 이틀 지난 건 지운다

# 요청·응답 모양 — 제품과 일일 감사(bot.scripts.kis_flow_audit)가 **같이** 쓴다.
# 감사가 요청을 따로 적으면 제품 파라미터를 고쳐도 옛 요청을 잰다(#35·#38, 리뷰 M6).
# 값 = (경로, tr_id, 행 키, 날짜 필드, 캐시 접두)
FLOW_TRS = {
    "investor": ("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
                 "output", "stck_bsop_date", "investor"),
    "credit": ("/uapi/domestic-stock/v1/quotations/daily-credit-balance", "FHPST04760000",
               "output", "deal_date", "credit"),
    "short": ("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000",
              "output2", "stck_bsop_date", "short"),
    "program": ("/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
                "FHPPG04650201", "output", "stck_bsop_date", "prog"),
}
# 기대 필드 — 공식 샘플 COLUMN_MAPPING 의 이름. ``_missing`` 이 응답과 대조해 원천이
# 이름을 바꾸면 그 사실을 말한다(파서와 감사가 같은 목록을 본다).
FLOW_FIELDS = {
    "investor": ("stck_bsop_date", "stck_clpr") + tuple(
        f"{p}_{k}" for _, p in _INV_WHO
        for k in ("ntby_qty", "ntby_tr_pbmn", "shnu_vol", "shnu_tr_pbmn",
                  "seln_vol", "seln_tr_pbmn")),
    "credit": ("deal_date", "whol_loan_rmnd_stcn", "whol_loan_rmnd_rate",
               "whol_stln_rmnd_stcn", "whol_stln_rmnd_rate"),
    "short": ("stck_bsop_date", "ssts_cntg_qty", "ssts_vol_rlim", "ssts_tr_pbmn_rlim"),
    "program": ("stck_bsop_date", "stck_clpr", "whol_smtn_ntby_qty",
                "whol_smtn_ntby_tr_pbmn", "whol_smtn_shnu_vol", "whol_smtn_shnu_tr_pbmn",
                "whol_smtn_seln_vol", "whol_smtn_seln_tr_pbmn"),
}


def flow_request(kind: str, ticker: str, *, date1: str = "") -> tuple:
    """수급 4종 요청 → ``(경로, tr_id, 파라미터)``. ``date1`` 은 신용의 결제일자만 쓴다
    (공식 샘플이 [필수]로 적는다 — 제품은 오늘(KST)을 먼저, 빈 값을 다음으로 묻는다)."""
    path, tr_id, _rk, _df, _pre = FLOW_TRS[kind]
    code = _ticker_to_code(ticker)
    p = {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_INPUT_ISCD": code}
    if kind == "credit":
        p = {"FID_COND_MRKT_DIV_CODE": _mkt_div(ticker), "FID_COND_SCR_DIV_CODE": "20476",
             "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": date1}
    elif kind == "short":
        p.update({"FID_INPUT_DATE_1": "", "FID_INPUT_DATE_2": ""})
    elif kind == "program":
        p["FID_INPUT_DATE_1"] = ""
    return path, tr_id, p


def _now_kst() -> datetime:
    """시계 — 테스트가 고정한다(오늘 행 판정이 시각에 달려 있다, #249·#425)."""
    return datetime.now(_KST)


def _krx_day_end_min() -> int:
    """KRX 의 그날 마지막 거래 국면이 끝나는 시각(자정부터 분) — ``bot.kr_session``
    단일 출처(#38). 2026 개편으로 KRX 애프터마켓이 20:00 까지라 그 전의 오늘 행은
    장중 부분값이거나 자리표시일 수 있다. 표에서 못 찾으면 하루 끝(=오늘 행은 늘
    잠정 — 보수적)으로 두고 경고한다."""
    from bot.kr_session import VENUES
    for sh, sm, _eh, _em, key, _label in VENUES.get("KRX", ()):
        if key == "after_close":
            return sh * 60 + sm
    log.warning("kis: kr_session 의 KRX 표에 after_close 국면이 없습니다 — 오늘 행을 늘 잠정으로 봅니다")
    return 24 * 60


def _last_day_end(now: datetime) -> datetime:
    """``now`` 이하의 가장 최근 'KRX 하루 끝' 시각. 그보다 먼저 쓴 캐시는 그 뒤 원천에
    새 확정 값이 생겼을 수 있어 믿지 않는다(마감 전 응답을 12시간 들고 있으면 그날
    저녁 확정 값을 밤까지 못 본다)."""
    m = _krx_day_end_min()
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=m)
    return end if now >= end else end - timedelta(days=1)


def _flow_cache_key(kind: str, code: str) -> str:
    """캐시 파일 이름에 판을 싣는다 — 배포 중 재시작 전의 옛 프로세스가 새 모양을
    읽거나 그 반대가 되지 않게(이름이 다르면 서로 못 읽는다, 리뷰 L4)."""
    return f"{FLOW_TRS[kind][4]}_v{_FLOW_SCHEMA}_{code}.json"


def _flow_cache_get(key: str, *, now: Optional[datetime] = None) -> Optional[dict]:
    """수급 캐시는 판(``schema``)까지 맞아야 쓴다(#21b). 오늘 행이 잠정이던 응답은
    1시간, 값을 못 만든 응답(``none``)은 30분만 믿는다. 그리고 마지막 'KRX 하루 끝'
    보다 먼저 쓴 것은 믿지 않는다 — 그 뒤 원천에 그날 확정 값이 생긴다."""
    f = _CACHE_DIR / key
    try:
        mtime = f.stat().st_mtime
        cached = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    if not (isinstance(cached, dict) and cached.get("schema") == _FLOW_SCHEMA):
        return None
    # 나이는 **같은 시계**(``_now_kst``)로 잰다 — 12시간 TTL 만 벽시계로 재면 오늘 행
    # 판정(주입한 시계)과 캐시 판정이 다른 '지금' 을 본다.
    now = now or _now_kst()
    age_h = (now.timestamp() - mtime) / 3600
    if age_h >= _CACHE_TTL_HOURS:
        return None
    if cached.get("pending") and age_h >= _PENDING_TTL_HOURS:
        return None
    if cached.get("none") and age_h >= _NONE_TTL_HOURS:
        return None
    if mtime < _last_day_end(now).timestamp():
        return None
    return cached


_FLOW_PURGED = False


def _flow_put(key: str, value: dict) -> None:
    """수급 캐시 쓰기. 프로세스당 한 번, 이틀 넘은 수급 캐시 파일(옛 이름·옛 판 포함)을
    지운다 — 이름에 판을 싣는 캐시는 지우는 코드가 없으면 쌓인다(#430). 다른 KIS 캐시
    (현재가·차트 등)는 접두가 달라 건드리지 않는다."""
    global _FLOW_PURGED
    if not _FLOW_PURGED:
        _FLOW_PURGED = True
        cutoff = time.time() - _FLOW_PURGE_AGE_SEC
        try:
            for pre in {v[4] for v in FLOW_TRS.values()}:
                for f in _CACHE_DIR.glob(f"{pre}_*.json"):
                    try:
                        if f.stat().st_mtime < cutoff:
                            f.unlink()
                    except OSError:
                        pass
        except OSError as exc:
            log.debug("kis: 수급 캐시 정리 실패: %s", exc)
    _cache_put(key, value)


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


def _missing(tr_id: str, row: dict, fields, *, warn: bool = True) -> list:
    """기대한 필드 중 응답에 없는 것. 원천이 이름을 바꾸면 그 칸이 조용히 None 이
    된다(2026-10-04 까지 그랬다) — 같은 조합은 프로세스당 한 번 경고한다. 감사는
    ``warn=False`` 로 부른다(진단이 제품의 경고 예산을 쓰지 않게, #264 · 리뷰 L8)."""
    miss = [f for f in fields if f not in row]
    if warn and miss and (tr_id, tuple(miss)) not in _MISSING_WARNED:
        _MISSING_WARNED.add((tr_id, tuple(miss)))
        log.warning("kis: %s 응답에 기대한 필드 %d개가 없습니다: %s — 원천이 이름을 "
                    "바꿨을 수 있습니다", tr_id, len(miss), ", ".join(miss[:8]))
    return miss


def _split_rows(rows: list, *, active, now: datetime) -> dict:
    """날짜순 행 → ``{"confirmed", "pending", "pending_note", "dropped", "dropped_note"}``.

    오늘(KST) 행은 KRX 의 그날 마지막 거래 국면이 끝나기 전엔 장중 부분값이거나
    자리표시일 수 있어 **잠정**이다. 끝난 뒤에도 그날 값이 비었거나 0 이면 잠정이다
    (원천이 아직 안 채웠는지 정말 0 인지 오늘은 못 가른다 — 다음 날엔 지난 날짜라
    확정으로 센다). 끝난 뒤인데 오늘(거래일) 행이 **아예 없어도** 잠정으로 센다 — 안
    세면 그 응답이 12시간 캐시돼 밤새 어제 값이 나간다(2026-10-04 델타 리뷰 M2. 달력을
    못 쓰면 보수적으로 센다). 오늘보다 뒤 날짜도 잠정. **지난 날짜는 확정이고 그날의
    0 은 0** 이다 — 판 2 는 거래가 0 인 날(정지일·프로그램 매매가 없던 날)을 '미제공'
    으로 버려 9거래일 구간을 '5거래일' 이라 불렀다(리뷰 H2).

    쓰지 않는 행(``dropped``): 같은 날짜가 **값이 다르게** 두 번 온 지난 날(어느 쪽이
    맞는지 모른다 — 같은 값이면 하나만 남긴다)과 달력상 거래일이 아닌 날짜의 행. 둘 다
    'N거래일' 을 거짓으로 만든다(델타 리뷰 M3). 달력을 못 쓰면 거래일 대조는 건너뛴다."""
    from bot import market_calendar as mc
    today = now.strftime("%Y-%m-%d")
    m = _krx_day_end_min()
    closed = now.hour * 60 + now.minute >= m
    by_date: dict = {}
    for d, r in rows:
        by_date.setdefault(d, []).append(r)
    confirmed, why, dropped = [], [], []
    for d, rs in by_date.items():                  # 원천 순서가 아니라 ``_dated`` 의 날짜순
        r = rs[0]
        clash = any(x != r for x in rs[1:])
        if d > today:
            why.append(f"{d}(오늘보다 뒤 날짜)")
        elif clash and d == today:
            why.append(f"{d}(오늘 — 같은 날짜 행이 값이 다르게 {len(rs)}번 옴)")
        elif clash:
            dropped.append(f"{d}(같은 날짜 행이 값이 다르게 {len(rs)}번 옴)")
        elif mc.is_trading_day("KR", d) is False:
            dropped.append(f"{d}(달력상 거래일이 아님)")
        elif d == today and not closed:
            why.append(f"{d}(오늘 — KRX 거래가 끝나는 {m // 60:02d}:{m % 60:02d} 전이라 잠정)")
        elif d == today and not active(r):
            why.append(f"{d}(오늘 — 마감 뒤에도 값이 비었거나 0 이라 확정으로 보지 않음)")
        else:
            confirmed.append((d, r))
    if closed and today not in by_date:
        sess = mc.is_trading_day("KR", today)
        if sess is not False:
            why.append(f"{today}(오늘 행이 아직 없음 — KRX 하루 끝 뒤인데 원천이 주지 않았습니다"
                       + ("" if sess else " · 달력을 못 써 오늘이 거래일인지 모름") + ")")
    return {"confirmed": confirmed, "pending": len(why), "pending_note": " · ".join(why),
            "dropped": len(dropped), "dropped_note": " · ".join(dropped)}


def _ratio(q, p, a) -> Optional[float]:
    return (q * p / a) if (q and p and a and q > 0 and p > 0 and a > 0) else None


def _calibrate_unit(samples) -> tuple:
    """원천 금액 칸의 단위(원으로 바꾸는 배수)를 응답 자체에서 잰다 → (배수|None, 사유).

    ``samples`` = (수량, 가격, 금액) 묶음 — 금액 × 단위 ≈ 수량 × 가격. 공식 샘플이
    단위를 밝히지 않아 가정하면 100배 어긋난다(옛 판은 만원이라 가정했다). **총**
    매수·매도 수량과 대금을 넘길 것 — 순매수는 매수·매도가 상쇄돼 작아지고 부호가 갈려
    비가 흔들린다. 하루 평균 체결가는 종가와 상·하한가(±30%) 안이라 비는 참 단위의
    0.5~2배 안에 들고, 후보끼리는 10배 이상 떨어져 그 창이 겹치지 않는다. 표본이 둘
    미만이거나 3분의 2 가 한 후보에 모이지 않으면 단위를 **모른다**고 돌려준다."""
    ratios = sorted(x for x in (_ratio(q, p, a) for q, p, a in samples) if x is not None)
    if len(ratios) < 2:
        return None, f"표본 {len(ratios)}개 — 금액 단위를 잴 수 없습니다"
    med = ratios[len(ratios) // 2]
    for c in _UNIT_CANDIDATES:
        if 0.5 <= med / c <= 2:
            agree = sum(1 for r in ratios if 0.5 <= r / c <= 2)
            if agree * 3 >= len(ratios) * 2:
                return c, f"표본 {len(ratios)}개 중 {agree}개가 ×{c:,}"
            return None, f"표본이 갈립니다(×{c:,} 에 {agree}/{len(ratios)}개)"
    return None, (f"중앙 비 {med:,.1f} 가 어느 후보(×1·×1천·×1만·×100만·×1억)와도 "
                  "맞지 않습니다")


def _unit_for(rows: list, samples_of) -> tuple:
    """싣는 행들로 단위를 재고 행마다 대조한다 → ``(단위|None, 사유, 어긋난 날짜들, 표본 수)``.

    응답 전체(~30행)로 재면 오래된 행이 다수결로 최근 행을 이길 수 있다 — 예: 며칠 전
    액면분할 뒤 옛 행의 종가가 수정주가가 아니면 비가 10배 갈리고, 후보 간격(10배)이
    흔한 분할 비율과 같다(2026-10-04 리뷰 M2). 그래서 화면에 싣는 행(최신 + 창)만 쓰고,
    그 행마다 자기 표본의 중앙 비가 고른 단위의 0.5~2배 안인지 본다 — 어긋나는 날의
    금액은 싣지 않는다. 표본이 없는 날(거래 0)은 대조할 것이 없고 그날 금액은 0 이다."""
    samples = [s for _d, r in rows for s in samples_of(r)]
    n = sum(1 for s in samples if _ratio(*s) is not None)
    unit, note = _calibrate_unit(samples)
    if not unit:
        return None, note, [], n
    bad = []
    for d, r in rows:
        rs = sorted(x for x in (_ratio(*s) for s in samples_of(r)) if x is not None)
        if rs and not (0.5 <= rs[len(rs) // 2] / unit <= 2):
            bad.append(d)
    if bad:
        note += f" · 행별 대조에서 {', '.join(bad)} 의 비가 이 단위와 맞지 않습니다"
    return unit, note, bad, n


def _sum_all(vals) -> Optional[int]:
    """창의 칸이 하나라도 비면 합을 만들지 않는다 — 4일 합을 '5거래일 누적'이라
    부르면 거짓말이다(실수 #99)."""
    vals = list(vals)
    if not vals or any(v is None for v in vals):
        return None
    return sum(vals)


def _won(v: Optional[int], unit: Optional[int]) -> Optional[int]:
    """원천 금액 → 원. 0 은 어느 단위로도 0 이다(거래 0 인 날은 단위 없이도 0)."""
    if v is None:
        return None
    if v == 0:
        return 0
    return v * unit if unit else None


def _session_gaps(win: list) -> Optional[list]:
    """창(최신부터)의 시작~끝 사이 KRX 거래일 중 원천이 행을 주지 않은 날. 달력을 못
    쓰면 None(대조 불가 — 그때 라벨은 '거래일' 이라 하지 않는다). 원천이 거래 0 인 날을
    0 으로 채워 주지 않고 아예 빼면 5개 행이 5거래일이 아니다(2026-10-04 리뷰 H2)."""
    if not win:
        return []
    from bot import market_calendar as mc
    sess = mc.sessions_between("KR", win[-1][0], win[0][0])
    if sess is None:
        return None
    have = {d for d, _r in win}
    return [s for s in sess if s not in have]


def _window_of(confirmed: list, start: int, unit, bad: list, value_of) -> dict:
    """확정 행 ``start`` 부터 최근 ``_FLOW_WINDOW`` 개 → 창 dict. 합을 못 만든 칸은
    None 이고 ``note`` 가 이유를 말한다(#43). ``label`` 은 달력으로 그 구간이 정말
    연속한 거래일일 때만 'N거래일' 이다 — 원천이 거래 0 인 날을 빼고 주면 5개 행이
    5거래일이 아니다(리뷰 H2). 그때는 합을 만들지 않는다(#99)."""
    win = confirmed[start:start + _FLOW_WINDOW]
    gaps = _session_gaps(win)
    days = len(win)
    blocked = ""
    if gaps:
        blocked = (f"달력상 거래일 {', '.join(gaps)} 의 행이 응답에 없습니다"
                   "(원천 누락이거나 달력이 모르는 휴장일)")
    elif bad and any(d in bad for d, _r in win):
        blocked = "그 구간에 금액 단위가 맞지 않는 날이 있습니다"

    def _sum(field):
        if blocked:
            return None
        return _sum_all(_won(_int(r.get(field)), unit) for _d, r in win)

    won = value_of(_sum)
    vals = list(won.values()) if isinstance(won, dict) else [won]
    note = blocked
    if not note and any(v is None for v in vals):
        note = ("금액 단위를 확정하지 못했습니다" if not unit
                else "그 구간에 값이 빈 날이 있어 합을 만들지 않은 칸이 있습니다")
    return {"from": win[-1][0], "to": win[0][0], "days": days,
            "label": f"{days}거래일" if gaps == [] else f"최근 {days}개 영업일 행",
            "sessions_checked": gaps is not None, "gaps": gaps or [],
            "won": won, "note": note}


def _inv_active(r: dict) -> bool:
    return any(_int(r.get(f"{p}_{s}_vol")) for _k, p in _INV_WHO for s in ("shnu", "seln"))


def _inv_valued(r: dict) -> bool:
    return any(_int(r.get(f"{p}_{k}")) is not None for _k, p in _INV_WHO
               for k in ("ntby_qty", "ntby_tr_pbmn", "shnu_vol", "seln_vol"))


def _inv_samples(r: dict) -> list:
    return [(_int(r.get(f"{p}_{s}_vol")), _int(r.get("stck_clpr")),
             _int(r.get(f"{p}_{s}_tr_pbmn"))) for _k, p in _INV_WHO for s in ("shnu", "seln")]


def _latest_pick(confirmed: list, valued) -> tuple:
    """확정 행 중 값이 하나라도 있는 가장 최근 행 → ``(위치, 그보다 최근인 빈 행 수)``."""
    for i, (_d, r) in enumerate(confirmed):
        if valued(r):
            return i, i
    return None, len(confirmed)


def parse_investor_flow(data: Optional[dict], *, now: Optional[datetime] = None,
                        warn: bool = True) -> Optional[dict]:
    """주식현재가 투자자(FHKST01010900) 응답 → 수급 dict(판 ``_FLOW_SCHEMA``).

    ``latest`` = 가장 최근 확정 거래일 하루(``qty`` 주 · ``won`` 원), ``window`` = 그날까지
    최근 ``_FLOW_WINDOW`` 거래일 순매수 합(원 · ``label`` 은 달력으로 대조한 'N거래일').
    오늘 행은 ``_split_rows`` 규칙으로 잠정이면 빼고 ``pending``·``pending_note`` 에
    적는다 — 당일 투자자별 수급은 장 종료 후 제공된다(공식 유의사항). 그래서
    ``latest.date`` 가 어제일 수 있고, 화면·프롬프트는 그 날짜를 적는다."""
    now = now or _now_kst()
    rows = _dated(_rows_of(data), "stck_bsop_date")
    sp = _split_rows(rows, active=_inv_active, now=now)
    confirmed = sp["confirmed"]
    i, blank = _latest_pick(confirmed, _inv_valued)
    if i is None:
        return None
    latest_d, latest = confirmed[i]
    calib = confirmed[i:i + _FLOW_WINDOW]
    unit, unit_note, bad, n_samples = _unit_for(calib, _inv_samples)
    lunit = None if latest_d in bad else unit
    window = _window_of(
        confirmed, i, unit, bad,
        lambda s: {k: s(f"{p}_ntby_tr_pbmn") for k, p in _INV_WHO})
    return {
        "schema": _FLOW_SCHEMA,
        "asof": latest_d,
        "unit_won": unit,
        "unit_note": unit_note,
        "unit_bad": bad,
        "unit_samples": n_samples,
        "latest": {
            "date": latest_d,
            "qty": {k: _int(latest.get(f"{p}_ntby_qty")) for k, p in _INV_WHO},
            "won": {k: _won(_int(latest.get(f"{p}_ntby_tr_pbmn")), lunit)
                    for k, p in _INV_WHO},
            "unit_ok": lunit is not None,
        },
        "window": window,
        "pending": sp["pending"],
        "pending_note": sp["pending_note"],
        "dropped": sp["dropped"],
        "dropped_note": sp["dropped_note"],
        "blank": blank,
        "missing": _missing("FHKST01010900", latest, FLOW_FIELDS["investor"], warn=warn),
    }


def _one_row(kind: str, data: Optional[dict], now: Optional[datetime]) -> dict:
    """신용·공매도 — 값이 하나라도 있는 가장 최근 확정 행(``row``). 판 3 첫 판은 최신
    확정 행을 **빈 행이어도** 골라 None 만 든 값을 12시간 캐시했다(사유도 없이 섹션이
    사라졌다 — 델타 리뷰 M1). 투자자·프로그램처럼 빈 행은 건너뛰고 ``blank`` 로 센다."""
    _path, _tr, rows_key, date_field, _pre = FLOW_TRS[kind]
    active, valued = _FLOW_PREDICATES[kind]
    rows = _dated(_rows_of(data, rows_key), date_field)
    sp = _split_rows(rows, active=active, now=now or _now_kst())
    i, blank = _latest_pick(sp["confirmed"], valued)
    sp["row"] = sp["confirmed"][i] if i is not None else None
    sp["at"] = i
    sp["blank"] = blank
    return sp


def _credit_active(r: dict) -> bool:
    return bool(_int(r.get("whol_loan_rmnd_stcn")))


def _credit_valued(r: dict) -> bool:
    return (any(_int(r.get(k)) is not None for k in ("whol_loan_rmnd_stcn", "whol_stln_rmnd_stcn"))
            or any(_float(r.get(k)) is not None for k in ("whol_loan_rmnd_rate",
                                                         "whol_stln_rmnd_rate")))


def _short_active(r: dict) -> bool:
    return bool(_int(r.get("ssts_cntg_qty")))


def _short_valued(r: dict) -> bool:
    return (_int(r.get("ssts_cntg_qty")) is not None
            or any(_float(r.get(k)) is not None for k in ("ssts_vol_rlim", "ssts_tr_pbmn_rlim")))


_CREDIT_PLACEHOLDER_MAX = 3   # 신용잔고 0 이 이만큼 이하로 이어지면 자리표시로 본다


def _credit_skip_placeholders(sp: dict) -> None:
    """신용잔고는 **저량**이라 '지난 날의 0 은 0' 이 맞지 않는다 — 수만 주 잔고가 하루
    만에 정확히 0 이 되는 일은 사실상 없으므로, 최신 확정 행부터 0 이 이어지다 그 뒤(더
    옛날)에 0 이 아닌 잔고가 있으면 그 0 들을 원천이 아직 안 채운 자리표시로 보고 쓰지
    않는다(델타 리뷰 M1 짝 — 오늘 행 자리표시를 잠정으로 뺀 규칙이 하루 밀리면 같은
    증상이다). 0 이 ``_CREDIT_PLACEHOLDER_MAX`` 를 넘게 이어지면 진짜 0 으로 둔다(신용
    불가 종목). ⚠️ 원천이 지난 날을 0 으로 채워 주는지는 재지 않았다 — 방어 규칙이다."""
    conf, i = sp["confirmed"], sp.get("at")
    if i is None or _int(conf[i][1].get("whol_loan_rmnd_stcn")) != 0:
        return
    zeros = []
    for d, r in conf[i:]:
        if not _credit_valued(r):
            continue
        bal = _int(r.get("whol_loan_rmnd_stcn"))
        if bal == 0:
            zeros.append(d)
            continue
        if bal and len(zeros) <= _CREDIT_PLACEHOLDER_MAX:
            sp["row"] = (d, r)
            note = f"{', '.join(zeros)}(신용잔고가 직전 {bal:,}주에서 0 — 자리표시로 보고 쓰지 않음)"
            sp["dropped"] += len(zeros)
            sp["dropped_note"] = " · ".join(x for x in (sp["dropped_note"], note) if x)
        return


def parse_credit_balance(data: Optional[dict], *, now: Optional[datetime] = None,
                         warn: bool = True) -> Optional[dict]:
    """신용잔고 일별추이(FHPST04760000) — 값이 있는 가장 최근 확정 매매일 한 행. 잔고
    **금액**은 화면이 쓰지 않아 싣지 않는다(단위를 재지 않은 값을 남기면 나중에 누가 추측해
    쓴다). 오늘 행은 자리표시일 수 있어 확정 전엔 잠정이다(리뷰 M3)."""
    sp = _one_row("credit", data, now)
    _credit_skip_placeholders(sp)
    if not sp["row"]:
        return None
    d, r = sp["row"]
    return {
        "schema": _FLOW_SCHEMA,
        "asof": d,
        "credit_balance_shares": _int(r.get("whol_loan_rmnd_stcn")),
        "credit_balance_pct": _float(r.get("whol_loan_rmnd_rate")),
        "credit_short_shares": _int(r.get("whol_stln_rmnd_stcn")),
        "credit_short_pct": _float(r.get("whol_stln_rmnd_rate")),
        "pending": sp["pending"],
        "pending_note": sp["pending_note"],
        "dropped": sp["dropped"],
        "dropped_note": sp["dropped_note"],
        "blank": sp["blank"],
        "missing": _missing("FHPST04760000", r, FLOW_FIELDS["credit"], warn=warn),
    }


def parse_short_sale(data: Optional[dict], *, now: Optional[datetime] = None,
                     warn: bool = True) -> Optional[dict]:
    """공매도 일별추이(FHPST04830000) — ``output2`` 의 값이 있는 가장 최근 확정 영업일 한
    행. 공매도 수량은 **흐름**이라 지난 날의 0 은 0 이다(신용잔고의 자리표시 규칙과 다르다)."""
    sp = _one_row("short", data, now)
    if not sp["row"]:
        return None
    d, r = sp["row"]
    return {
        "schema": _FLOW_SCHEMA,
        "asof": d,
        "short_qty": _int(r.get("ssts_cntg_qty")),
        "short_ratio_pct": _float(r.get("ssts_vol_rlim")),        # 공매도 거래량 비중
        "short_amt_ratio_pct": _float(r.get("ssts_tr_pbmn_rlim")),  # 공매도 거래대금 비중
        "pending": sp["pending"],
        "pending_note": sp["pending_note"],
        "dropped": sp["dropped"],
        "dropped_note": sp["dropped_note"],
        "blank": sp["blank"],
        "missing": _missing("FHPST04830000", r, FLOW_FIELDS["short"], warn=warn),
    }


def _prog_active(r: dict) -> bool:
    return bool(_int(r.get("whol_smtn_shnu_vol")) or _int(r.get("whol_smtn_seln_vol")))


def _prog_valued(r: dict) -> bool:
    return any(_int(r.get(k)) is not None for k in (
        "whol_smtn_ntby_qty", "whol_smtn_ntby_tr_pbmn", "whol_smtn_shnu_vol",
        "whol_smtn_seln_vol"))


def _prog_samples(r: dict) -> list:
    return [(_int(r.get(f"whol_smtn_{s}_vol")), _int(r.get("stck_clpr")),
             _int(r.get(f"whol_smtn_{s}_tr_pbmn"))) for s in ("shnu", "seln")]


def parse_program_daily(data: Optional[dict], *, now: Optional[datetime] = None,
                        warn: bool = True) -> Optional[dict]:
    """종목별 프로그램매매추이(일별, FHPPG04650201) — **전체 합계** 순매수. 이 TR 은
    차익·비차익을 나누지 않는다(옛 판은 공식 응답에 없는 필드로 차익·비차익을
    읽었다). 모양은 ``parse_investor_flow`` 의 ``latest``·``window`` 와 같다. 장중
    오늘 행은 부분값이라 잠정이다 — 판 2 는 그걸 '하루' 로 12시간 구웠다(리뷰 M3)."""
    now = now or _now_kst()
    rows = _dated(_rows_of(data), "stck_bsop_date")
    sp = _split_rows(rows, active=_prog_active, now=now)
    confirmed = sp["confirmed"]
    i, blank = _latest_pick(confirmed, _prog_valued)
    if i is None:
        return None
    latest_d, latest = confirmed[i]
    unit, unit_note, bad, n_samples = _unit_for(confirmed[i:i + _FLOW_WINDOW], _prog_samples)
    lunit = None if latest_d in bad else unit
    window = _window_of(confirmed, i, unit, bad, lambda s: s("whol_smtn_ntby_tr_pbmn"))
    return {
        "schema": _FLOW_SCHEMA,
        "asof": latest_d,
        "unit_won": unit,
        "unit_note": unit_note,
        "unit_bad": bad,
        "unit_samples": n_samples,
        "latest": {"date": latest_d,
                   "qty": _int(latest.get("whol_smtn_ntby_qty")),
                   "won": _won(_int(latest.get("whol_smtn_ntby_tr_pbmn")), lunit),
                   "unit_ok": lunit is not None},
        "window": window,
        "pending": sp["pending"],
        "pending_note": sp["pending_note"],
        "dropped": sp["dropped"],
        "dropped_note": sp["dropped_note"],
        "blank": blank,
        "missing": _missing("FHPPG04650201", latest, FLOW_FIELDS["program"], warn=warn),
    }


FLOW_PARSERS = {"investor": parse_investor_flow, "credit": parse_credit_balance,
                "short": parse_short_sale, "program": parse_program_daily}
# (그날 거래가 있었나, 값이 하나라도 있나) — 잠정 판정·빈 행 판정·빈 응답 사유가 같이 쓴다.
_FLOW_PREDICATES = {"investor": (_inv_active, _inv_valued),
                    "credit": (_credit_active, _credit_valued),
                    "short": (_short_active, _short_valued),
                    "program": (_prog_active, _prog_valued)}


def why_empty(kind: str, data: Optional[dict], *, now: Optional[datetime] = None) -> str:
    """원천은 답했는데 값을 못 만든 이유(파서와 같은 규칙으로 되짚는다). 판 3 첫 판은
    확정 행이 있는데 전부 빈 경우까지 '확정된 행이 없습니다(잠정 행만 왔거나…)' 라고
    적었다(델타 리뷰 L6). 감사의 판정 줄도 이 문장을 쓴다(#356 — 그 줄이 자족해야 한다)."""
    _path, _tr, rows_key, date_field, _pre = FLOW_TRS[kind]
    raw = _rows_of(data, rows_key)
    rows = _dated(raw, date_field)
    if not rows:
        return f"날짜({date_field})를 읽을 수 있는 행이 없습니다(행 {len(raw)}개)"
    active, _valued = _FLOW_PREDICATES[kind]
    sp = _split_rows(rows, active=active, now=now or _now_kst())
    if not sp["confirmed"]:
        parts = [x for x in (sp["pending_note"], sp["dropped_note"]) if x]
        return "확정 행이 없습니다 — " + (" · ".join(parts) or "사유 미상")
    return f"확정 행 {len(sp['confirmed'])}개의 값이 모두 비어 있습니다"

# ── 수급 4종 차단기(#72 — 금융위 API 차단기와 같은 규약). 죽어 있던 경로를 살렸으니
# KIS 가 느려지면 KR 상세·스냅샷이 TR 마다 10초씩 붙잡힌다(리뷰 M7). 전송 실패
# (5xx·타임아웃·연결)만 세고 4xx·rt_cd 는 세지 않는다(쉰다고 안 풀린다). 성공하면
# 카운터를 지운다. 차단은 TR 단위.
_BREAKER_TRIP = 2
_BREAKER_COOL_SEC = 300
_BREAKER: dict = {}
_BREAKER_LOCK = threading.Lock()


def _flow_ask(kind: str, ticker: str, *, date1: str = "") -> tuple:
    """수급 4종 원천 조회(차단기 경유) → ``(응답 | None, 사유)``."""
    path, tr_id, params = flow_request(kind, ticker, date1=date1)
    now = time.time()
    with _BREAKER_LOCK:
        n, until = _BREAKER.get(tr_id, (0, 0.0))
    if until > now:
        return None, {"kind": "breaker", "status": None,
                      "msg": f"연속 전송 실패 {n}회로 {int(until - now)}초 동안 묻지 않습니다"}
    data, info = _get_ex(path, tr_id, params)
    with _BREAKER_LOCK:
        if info.get("kind") == "ok":
            _BREAKER.pop(tr_id, None)
        elif info.get("kind") in _TRANSPORT_FAIL:
            n = _BREAKER.get(tr_id, (0, 0.0))[0] + 1
            opened = n >= _BREAKER_TRIP
            _BREAKER[tr_id] = (n, now + _BREAKER_COOL_SEC if opened else 0.0)
            if opened:
                log.warning("kis: %s 연속 전송 실패 %d회 — %d초 동안 묻지 않습니다(%s)",
                            tr_id, n, _BREAKER_COOL_SEC, info.get("msg"))
    return data, info


def _flow_get(kind: str, ticker: str) -> tuple:
    """수급 한 종 → ``(값 | None, 비었을 때 사유)``. 캐시(판·잠정·하루 끝 규칙) →
    원천(차단기) → 파서. 원천이 답했는데 값을 만들 행이 없으면 그 사실을 30분 기억한다
    (조회마다 같은 빈손을 다시 묻지 않게 — 전송 실패는 기억하지 않고 차단기가 맡는다)."""
    code = _ticker_to_code(ticker)
    if not code:
        return None, "국내 6자리 티커가 아닙니다"
    key = _flow_cache_key(kind, code)
    cached = _flow_cache_get(key)
    if cached is not None:
        if cached.get("none"):
            return None, f"{cached['none']} (30분 안에 받은 같은 답)"
        return cached, ""
    parse = FLOW_PARSERS[kind]
    d1s = (_now_kst().strftime("%Y%m%d"), "") if kind == "credit" else ("",)
    result, data, info = None, None, {"kind": "error", "msg": ""}
    for d1 in d1s:
        data, info = _flow_ask(kind, ticker, date1=d1)
        result = parse(data) if data else None
        if result:
            break
    if result:
        _flow_put(key, result)
        return result, ""
    if data is not None:                       # 원천은 답했다 — 만들 행이 없었다
        why = "원천이 답했는데 값을 만들 수 없습니다 — " + why_empty(kind, data)
        _flow_put(key, {"schema": _FLOW_SCHEMA, "none": why})
        return None, why
    return None, f"원천 응답 실패 — {info.get('msg') or info.get('kind')}"


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
        """외인·기관·개인 순매수 — 가장 최근 확정 거래일 하루(수량·금액) + 그날까지
        최근 5거래일 금액 합. tr_id FHKST01010900. 모양은 ``parse_investor_flow``.

        기관 세부(연기금·투신…)는 이 TR 의 응답에 없다 — 옛 판이 읽던 ``pnsn_*`` 등은
        공식 샘플 목록에 없는 이름이라 늘 None 이었다(실수 #433)."""
        return _flow_get("investor", ticker)[0]

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
        보내고, 확정 행이 없으면 빈 값으로 한 번 더 묻는다. 어느 쪽을 원천이 받는지는
        ``bot.scripts.kis_flow_audit`` 가 잰다."""
        return _flow_get("credit", ticker)[0]

    # 6. 프로그램 매매 — 종목별 일별 (program-trade-by-stock-daily)
    def get_program_trade(self, ticker: str) -> Optional[dict]:
        """종목별 프로그램매매추이(일별, FHPPG04650201) — 전체 합계 순매수, 최근 확정
        거래일 하루 + 그날까지 최근 5거래일 합. 모양은 ``parse_program_daily``.

        옛 판은 체결 TR(FHPPG04650100)을 부르면서 공식 응답 목록에 없는 필드
        (``pgtr_*``)로 차익·비차익을 읽었고, 응답(공식 샘플은 목록으로 받는다)을 dict 로
        다뤘다. 이 TR 은 차익·비차익을 나누지 않는다(실수 #433)."""
        return _flow_get("program", ticker)[0]

    # 7. 공매도 일별추이 (daily-short-sale)
    def get_short_sale(self, ticker: str) -> Optional[dict]:
        """공매도 일별추이(FHPST04830000) — 가장 최근 확정 영업일의 공매도 수량·비중.
        모양은 ``parse_short_sale``. 시작·종료일자는 공식 샘플에서도 선택이다."""
        return _flow_get("short", ticker)[0]

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


# 수집 함수가 돌려주는 키 ↔ 수급 종류. ``kis_why`` 는 빈 칸의 사유(화면이 말한다).
FLOW_KEYS = (("investor_flow", "investor"), ("credit", "credit"),
             ("short_sale", "short"), ("program", "program"))
_COLLECT_BUDGET_SEC = 25.0     # 한 종목 수급 블록 전체 — 넘으면 남은 조회는 다음 기회에


def kis_flow_present(flow: Optional[dict]) -> bool:
    """수급 dict 에 지금 판(``_FLOW_SCHEMA``)의 KIS 값이 하나라도 있나. '수급 칸이
    있으면 건너뜀' 으로 판정하면 pykrx 추세만 든 저장 스냅샷(옛 아카이브)이 KIS 칸을
    영영 못 얻는다(#18 · 리뷰 L2)."""
    return isinstance(flow, dict) and any(
        isinstance(flow.get(k), dict) and flow[k].get("schema") == _FLOW_SCHEMA
        for k, _kind in FLOW_KEYS)


def collect_kis_flow(ticker: str, *, budget: float = _COLLECT_BUDGET_SEC,
                     clock=time.monotonic) -> dict:
    """수급 탭(종목 페이지)과 스냅샷이 같이 쓰는 KIS 수급 4종 — 받은 것과, 못 받은
    칸의 **사유**(``kis_why``)를 담는다. 키: ``investor_flow``·``credit``·
    ``short_sale``·``program``·``kis_why``.

    ⚠️ 2026-10-04 까지 이 블록은 ``stock_snapshot`` 과 ``dashboard`` 에 **복제**돼
    있었고, 둘 다 ``KisClient`` 에 없는 ``_ready()`` 를 불러 AttributeError 가 DEBUG
    로그에 삼켜졌다 — 수급 탭의 KIS 칸은 한 번도 채워진 적이 없다(실수 #433).
    한 곳에 두고, 레포 클래스에 없는 메서드를 부르는 자리는 회귀(전수 스캔)가 막는다.
    한 조회의 실패가 나머지를 지우지 않게 하나씩 감싼다(실수 #315). 키가 없으면 원천을
    부르지 않고 사유만 적는다(빈 칸이 왜 비었는지 화면이 말한다 — 리뷰 L3). 블록 전체
    예산(``budget`` 초)을 넘기면 남은 조회는 건너뛰고 그렇게 적는다(리뷰 M7 — TR 마다
    10초 타임아웃이라 장애 때 상세 페이지가 수십 초 붙잡혔다)."""
    if not kis_ready():
        return {"kis_why": {k: "KIS 자격증명(KIS_APP_KEY/KIS_APP_SECRET)이 없습니다"
                            for k, _kind in FLOW_KEYS}}
    t0 = clock()
    out: dict = {}
    why: dict = {}
    for key, kind in FLOW_KEYS:
        if clock() - t0 > budget:
            why[key] = f"수급 블록 예산 {budget:.0f}초를 넘겨 이번엔 묻지 않았습니다"
            continue
        try:
            value, reason = _flow_get(kind, ticker)
        except Exception as exc:                       # noqa: BLE001
            log.warning("kis: %s %s 실패: %s", key, ticker, exc)
            why[key] = f"수집 중 예외 — {type(exc).__name__}"
            continue
        if value:
            out[key] = value
        else:
            why[key] = reason or "사유 미기록"
    skipped = [k for k, v in why.items() if "예산" in v]
    if skipped:
        log.warning("kis: %s 수급 블록 예산 %.0f초 초과 — %s 건너뜀",
                    ticker, budget, ", ".join(skipped))
    if why:
        out["kis_why"] = why
    return out


# ─── formatter ──────────────────────────────────────────────────────────────

def fmt_eok(v: Optional[int]) -> Optional[str]:
    """원 → 억 숫자 문자열(부호 포함, 소수 2자리 — 단위 글자는 호출부가 붙인다).
    화면과 프롬프트가 **같이** 쓴다(#38). 반올림해 0 이면 부호 없이 ``0.00`` —
    화면이 소수 1자리로 찍던 판은 1천만원 미만 순매수를 '+0.0억' 으로 초록 칠했다
    (2026-10-04 리뷰 M1). 파이썬이 환산한다 — LLM 에 단위 변환을 맡기면 100배
    틀린다(경동나비엔 2026-05-22: '+11,730만원' 을 PM 이 '117억원' 으로 읽었다)."""
    if v is None:
        return None
    r = round(v / 1e8, 2)
    if r == 0:
        return "0.00"
    return f"{'+' if r > 0 else ''}{r:,.2f}"


def eok_sign(v: Optional[int]) -> int:
    """``fmt_eok`` 가 찍는 값의 부호(반올림 뒤) — 화면 색이 글자와 어긋나지 않게."""
    if v is None:
        return 0
    r = round(v / 1e8, 2)
    return (r > 0) - (r < 0)


def _fmt_eok(v: Optional[int]) -> str:
    t = fmt_eok(v)
    return "N/A" if t is None else f"{t}억원"


def _fmt_shares(v: Optional[int]) -> str:
    if v is None:
        return "N/A"
    return f"{'+' if v > 0 else ''}{v:,}주"


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
        n_label = win.get("label") or f"{win.get('days')}거래일"
        if any(v is not None for v in qty.values()):
            lines.append(
                f"• 순매수 수량 ({d} 하루): 외인 {_fmt_shares(qty.get('foreign'))} /"
                f" 기관 {_fmt_shares(qty.get('institution'))} /"
                f" 개인 {_fmt_shares(qty.get('individual'))}"
            )
        if any(v is not None for v in won.values()):
            lines.append(
                f"• 순매수 금액 ({d} 하루): 외인 {_fmt_eok(won.get('foreign'))} /"
                f" 기관 {_fmt_eok(won.get('institution'))} /"
                f" 개인 {_fmt_eok(won.get('individual'))}"
            )
        if not lat.get("unit_ok") and any(v is None for v in won.values()):
            # 0 은 단위 없이도 0 이라 싣는다 — 그래서 '금액이 하나라도 빈' 때 사유를 적는다
            # (전부 빈 때만 적던 판은 'N/A / 0.00억원 / N/A' 를 사유 없이 냈다, 델타 리뷰 L5)
            lines.append(f"  (금액 단위를 확정하지 못해 그날 금액은 싣지 않습니다 —"
                         f" {flow.get('unit_note') or '사유 미상'})")
        if any(v is not None for v in wwon.values()):
            f5, i5, p5 = wwon.get("foreign"), wwon.get("institution"), wwon.get("individual")
            lines.append(
                f"• {n_label} 누적 ({win.get('from')}~{win.get('to')}):"
                f" 외인 {_fmt_eok(f5)} / 기관 {_fmt_eok(i5)} / 개인 {_fmt_eok(p5)}"
            )
            # RULE 10: ±100억 미만은 noise — dominant variable 인용 불가.
            # 파이썬이 원 단위로 미리 판정해 LLM 이 단위를 환산하지 않게 한다.
            for label_k, val_k in (("외인", f5), ("기관", i5)):
                if val_k is not None and abs(val_k) < _EOK_100:
                    lines.append(
                        f"  ⚠️ RULE 10: {label_k} {n_label} 누적 {_fmt_eok(val_k)}"
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
        if win.get("note"):
            lines.append(f"  ({n_label} 누적 중 합을 만들지 않은 칸이 있습니다 — {win['note']})")
        if flow.get("pending"):
            lines.append(f"  (아직 확정 전인 날: {flow.get('pending_note') or flow['pending']})")
        if flow.get("dropped"):
            lines.append(f"  (쓰지 않은 행: {flow.get('dropped_note') or flow['dropped']})")
        if flow.get("blank"):
            lines.append(f"  (원천이 값을 비워 둔 최근 {flow['blank']}일은 뺐습니다)")

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
        if cs.get("dropped"):
            lines.append(f"  (신용 — 쓰지 않은 행: {cs.get('dropped_note') or cs['dropped']})")

    # 프로그램 매매 — 전체 합계(이 TR 은 차익·비차익을 나누지 않는다)
    pt = data.get("program_trade") or {}
    if pt.get("schema") == _FLOW_SCHEMA:
        lat = pt.get("latest") or {}
        win = pt.get("window") or {}
        n_label = win.get("label") or f"{win.get('days')}거래일"
        if lat.get("won") is not None:
            lines.append(f"• 프로그램 순매수 ({lat.get('date')} 하루, 전체 합계 —"
                         f" 차익·비차익 구분 없음): {_fmt_eok(lat['won'])}")
        elif lat.get("qty") is not None:
            # 단위를 못 재도 수량은 싣는다 — 조용히 통째로 빼면 왜 없는지 아무도
            # 모른다(리뷰 L3).
            lines.append(f"• 프로그램 순매수 수량 ({lat.get('date')} 하루, 전체 합계):"
                         f" {_fmt_shares(lat['qty'])}")
            lines.append(f"  (금액 단위를 확정하지 못해 프로그램 금액은 싣지 않습니다 —"
                         f" {pt.get('unit_note') or '사유 미상'})")
        if win.get("won") is not None:
            lines.append(f"• 프로그램 {n_label} 누적"
                         f" ({win.get('from')}~{win.get('to')}): {_fmt_eok(win['won'])}")
        elif win.get("note"):
            lines.append(f"  (프로그램 {n_label} 누적은 싣지 않습니다 — {win['note']})")
        if pt.get("pending"):
            lines.append(f"  (프로그램 — 아직 확정 전인 날: {pt.get('pending_note') or pt['pending']})")
        if pt.get("dropped"):
            lines.append(f"  (프로그램 — 쓰지 않은 행: {pt.get('dropped_note') or pt['dropped']})")

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
