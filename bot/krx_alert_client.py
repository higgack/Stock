"""KRX 시장경보 + 거래정지 + 관리종목 + 상한가/하한가 client.

KR analogue to bot/akshare_client.py 의 is_st / is_suspended HARD GUARD —
KR 시장의 비정상 변동 분류를 자동 detect 해 build_instrument_context
의 HARD GUARD banner 로 surface.

자료 출처: KRX 정보데이터시스템 (data.krx.co.kr) 의 JSON endpoint.
무료 + API 키 불필요. pykrx 가 일부 endpoint 만 wrap 하므로 직접
HTTP POST 호출이 더 broad coverage.

분류 4종 (KR 시장경보 + 관리 4단계):
 1. **거래정지 (suspended)** — administrative halt / corp action / 풍문.
    yfinance silent fail — 차트 freeze. 가장 critical, HARD GUARD priority 1.
 2. **관리종목 (administrative issue)** — 자본잠식 / 5년 적자 / 회계감리
    의견 거절 등. 退市 위험 신호. HARD GUARD priority 2.
 3. **단기과열 (short-term overheating)** — 거래량 + 가격 + 변동성 종합
    이 normal 범위 outside. 5거래일 직전 정상 가격 분석 보류 권고.
    HARD GUARD priority 3.
 4. **투자경고/투자주의/투자위험** — 시장경보 3단계. 1단계 (주의) 약함 →
    3단계 (위험) 매우 강함. 위험 단계는 5거래일 가격 정상 분석 보류.

각 endpoint 의 KRX bld 코드:
 • 단기과열 / 시장경보: dbms/MDC/STAT/standard/MDCSTAT09301
 • 관리종목: dbms/MDC/STAT/standard/MDCSTAT09001
 • 거래정지: dbms/MDC/STAT/standard/MDCSTAT07901

Caching: per-day disk cache 12h. KRX 시장경보는 일별 갱신 (거래소 운영
종료 후) 이라 intraday change 적음 — 캐시 길어도 무관.

Failure 처리: KRX 403 / timeout 등 transient error 시 각 endpoint 별
빈 결과 반환 + warning log. build_instrument_context 의 Rule A guard 가
'KR 시장경보 데이터 미수집 → 단기과열 / 거래정지 fabricate 금지'
directive 처리.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.krx_alert")

_BASE = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "krx_alert"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 10

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "http://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd",
}


def _ticker_to_isin_or_code(ticker: str) -> Optional[str]:
    """Convert yfinance KR ticker ('005930.KS' / '035720.KQ') to KRX 6-digit
    종목코드 ('005930' / '035720'). Returns None for non-KR tickers."""
    if not ticker:
        return None
    t = ticker.upper()
    if not (t.endswith(".KS") or t.endswith(".KQ")):
        return None
    code = t.split(".")[0]
    if len(code) == 6 and code.isdigit():
        return code
    return None


def _cache_get(cache_key: str, ttl_hours: float = _CACHE_TTL_HOURS):
    cache_file = _CACHE_DIR / cache_key
    if not cache_file.exists():
        return None
    try:
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < ttl_hours:
            return json.loads(cache_file.read_text())
    except Exception as exc:
        log.warning("krx_alert: cache read failed for %s: %s", cache_key, exc)
    return None


def _cache_put(cache_key: str, value) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / cache_key).write_text(
            json.dumps(value, ensure_ascii=False, default=str)
        )
    except Exception as exc:
        log.warning("krx_alert: cache write failed for %s: %s", cache_key, exc)


def _fetch_krx_json(bld: str, params: dict) -> Optional[dict]:
    """Generic KRX JSON endpoint fetcher. Returns parsed JSON dict or
    None on failure. KRX endpoints use POST form-encoded with a 'bld'
    parameter identifying the data slice."""
    payload = {"bld": bld, **params}
    try:
        resp = requests.post(
            _BASE, headers=_HEADERS, data=payload, timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("krx_alert: KRX %s fetch failed: %s", bld, exc)
        return None


def _today_yyyymmdd() -> str:
    return date.today().strftime("%Y%m%d")


def _fetch_all_classifications() -> dict:
    """Pull all 4 KR market warning lists in one go. Cached 12h.

    Returns dict:
        {
            "suspended": set[str],         # 거래정지 종목코드
            "admin": set[str],             # 관리종목 종목코드
            "overheating": set[str],       # 단기과열 종목코드
            "warning_level": dict[str, str],  # code → '주의' / '경고' / '위험'
        }

    Empty sets on fetch failure — caller checks if ticker is in set
    (membership-only lookup, no metadata needed beyond classification).
    """
    cache_key = f"all_classifications_{_today_yyyymmdd()}.json"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {
            "suspended": set(cached.get("suspended", [])),
            "admin": set(cached.get("admin", [])),
            "overheating": set(cached.get("overheating", [])),
            "warning_level": cached.get("warning_level", {}),
        }

    result = {
        "suspended": set(),
        "admin": set(),
        "overheating": set(),
        "warning_level": {},
    }

    # 거래정지 — bld MDCSTAT07901. Output rows have 종목코드 + 정지사유.
    halt_data = _fetch_krx_json(
        "dbms/MDC/STAT/standard/MDCSTAT07901",
        {"mktId": "ALL", "trdDd": _today_yyyymmdd()},
    )
    if halt_data and isinstance(halt_data.get("OutBlock_1"), list):
        for row in halt_data["OutBlock_1"]:
            code = (row.get("ISU_SRT_CD") or "").strip()
            if code and code.isdigit() and len(code) == 6:
                result["suspended"].add(code)

    # 관리종목 — bld MDCSTAT09001.
    admin_data = _fetch_krx_json(
        "dbms/MDC/STAT/standard/MDCSTAT09001",
        {"mktId": "ALL", "trdDd": _today_yyyymmdd()},
    )
    if admin_data and isinstance(admin_data.get("OutBlock_1"), list):
        for row in admin_data["OutBlock_1"]:
            code = (row.get("ISU_SRT_CD") or "").strip()
            if code and code.isdigit() and len(code) == 6:
                result["admin"].add(code)

    # 시장경보 (단기과열 + 투자주의/경고/위험) — bld MDCSTAT09301.
    # Single endpoint returns all 4 sub-categories; per-row column
    # identifies which (시장경보종목 or 단기과열 or 주의/경고/위험).
    warn_data = _fetch_krx_json(
        "dbms/MDC/STAT/standard/MDCSTAT09301",
        {"mktId": "ALL", "trdDd": _today_yyyymmdd()},
    )
    if warn_data and isinstance(warn_data.get("OutBlock_1"), list):
        for row in warn_data["OutBlock_1"]:
            code = (row.get("ISU_SRT_CD") or "").strip()
            if not (code and code.isdigit() and len(code) == 6):
                continue
            # 시장경보구분 — '투자주의' / '투자경고' / '투자위험'
            # 단기과열구분 — '단기과열' / '단기과열예고' (or '단기과열지정')
            warn_kind = (row.get("ALRT_KND_NM") or row.get("MKT_ALRT_NM") or "").strip()
            heat_kind = (row.get("HEAT_KND_NM") or "").strip()
            if "단기과열" in (warn_kind + heat_kind):
                result["overheating"].add(code)
            for level in ("위험", "경고", "주의"):
                if level in warn_kind:
                    # Higher level wins if both present (위험 > 경고 > 주의)
                    existing = result["warning_level"].get(code, "")
                    if not existing or _level_rank(level) > _level_rank(existing):
                        result["warning_level"][code] = level
                    break

    _cache_put(cache_key, {
        "suspended": sorted(result["suspended"]),
        "admin": sorted(result["admin"]),
        "overheating": sorted(result["overheating"]),
        "warning_level": result["warning_level"],
    })
    return result


def _level_rank(level: str) -> int:
    """Map 시장경보 level to numeric rank for max-comparison."""
    return {"주의": 1, "경고": 2, "위험": 3}.get(level, 0)


class KrxAlertClient:
    """Per-ticker classification lookup. Single fetch_all() per process
    (cached) for the 4 lists, then membership-only lookup per ticker."""

    def __init__(self):
        self._classifications: Optional[dict] = None

    def _ensure_loaded(self) -> dict:
        if self._classifications is None:
            self._classifications = _fetch_all_classifications()
        return self._classifications

    def get_status(self, ticker: str) -> dict:
        """Return all classifications for one ticker.

        Each field independent — a ticker can be 관리종목 + 단기과열 +
        투자경고 simultaneously. Caller decides which HARD GUARD level
        to surface (priority: 거래정지 > 관리 > 위험 > 경고 > 단기과열 >
        주의).

        Returns dict:
            {
                "suspended": bool,
                "admin": bool,
                "overheating": bool,
                "warning_level": str,  # '' / '주의' / '경고' / '위험'
            }
        """
        code = _ticker_to_isin_or_code(ticker)
        if not code:
            return {
                "suspended": False, "admin": False,
                "overheating": False, "warning_level": "",
            }

        c = self._ensure_loaded()
        return {
            "suspended": code in c["suspended"],
            "admin": code in c["admin"],
            "overheating": code in c["overheating"],
            "warning_level": c["warning_level"].get(code, ""),
        }


_krx_alert_singleton: KrxAlertClient | None = None


def get_krx_alert() -> KrxAlertClient:
    """Process-wide cached client. Reuse keeps the 4-list fetch to once
    per process — every subsequent ticker check is in-memory lookup."""
    global _krx_alert_singleton
    if _krx_alert_singleton is None:
        _krx_alert_singleton = KrxAlertClient()
    return _krx_alert_singleton


def format_krx_alert_block(status: dict) -> str:
    """Render KR 시장경보 status as a prompt-ready HARD GUARD banner.
    Empty string when ticker is normal (no classification). Priority:
    거래정지 > 관리 > 위험 > 경고 > 단기과열 > 주의 — highest priority
    determines the dominant banner, but all matching classifications
    listed in body for transparency.
    """
    if not status:
        return ""
    suspended = status.get("suspended", False)
    admin = status.get("admin", False)
    overheating = status.get("overheating", False)
    warning_level = status.get("warning_level", "")

    if not (suspended or admin or overheating or warning_level):
        return ""

    classifications: list[str] = []
    if suspended:
        classifications.append("거래정지")
    if admin:
        classifications.append("관리종목")
    if warning_level == "위험":
        classifications.append("투자위험")
    elif warning_level == "경고":
        classifications.append("투자경고")
    elif warning_level == "주의":
        classifications.append("투자주의")
    if overheating:
        classifications.append("단기과열")

    primary = classifications[0]  # priority order
    body = " + ".join(classifications)

    if suspended:
        return (
            f"⛔ KR 시장경보 (HARD GUARD — primary: {primary})\n"
            f"분류: {body}\n"
            "이 종목은 KRX 거래정지 상태이다. yfinance 가격 freeze + 일별"
            " close 미갱신. 다음 사용 금지:\n"
            "  • 10 EMA / 50 SMA / 200 SMA / MACD / RSI / Bollinger\n"
            "  • 5거래일 수익률 / momentum / 추세 분석\n"
            "  • Comps 표 multiples (stale price 기준)\n"
            "허용 분석: (1) 거래정지 사유 정성 분석 (사건 / 풍문 / 회계"
            " / corp action), (2) 거래재개 시 가격 갭 시나리오, (3) 펀더"
            "멘털은 지난 분기 数据 기준만."
        )

    if admin or warning_level == "위험":
        return (
            f"⛔ KR 시장경보 (HARD GUARD — primary: {primary})\n"
            f"분류: {body}\n"
            "이 종목은 관리종목 (자본잠식 / 5년 적자 / 회계 의견거절 등)"
            " 또는 투자위험 단계로 退市 (상장폐지) 가능성 + 비정상 가격"
            " 변동 매우 큰 상태. 5거래일 정상 가격 분석 보류 + 다음 명시:\n"
            "  (1) 관리종목 분류 / 투자위험 단계 자체를 결론에 한 줄"
            " 언급\n"
            "  (2) 펀더멘털 결론에 자본잠식 / 退市 timing 변수 추가\n"
            "  (3) 일반 ±30% 가격 한도 가정 + 정상 PER/PBR multiples"
            " 인용 금지 — 데이터 의미 transitional"
        )

    # 단기과열 또는 투자경고/주의 만 — soft HARD GUARD
    return (
        f"⚠️ KR 시장경보 (분류: {body})\n"
        "이 종목은 단기과열 (거래량 + 가격 + 변동성 종합 normal 범위"
        " outside) 또는 투자경고/주의 (시장경보 1-2단계). 5거래일 가격"
        " 정상 분석 시 다음 인지:\n"
        "  (1) 단기과열 직전 5-10거래일 가격 surge 가 펀더멘털 reflect"
        " 가 아닌 매매심리 spike 일 가능성\n"
        "  (2) KRX 의 매매정지 / 거래정지 escalation 가능성 (단기과열"
        " → 거래정지 1일)\n"
        "  (3) RSI / 모멘텀 지표가 정상 범위 outside 일 가능성 큼 — RULE"
        " 7 보강의 단기 기술적 extreme trigger 와 같이 검토.\n"
        "결론에 본 분류 한 줄 명시 + verdict 작성 시 normal pattern 가정"
        " 자제."
    )
