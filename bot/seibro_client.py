"""세이브로(Seibro/KSD) 외국인보유 client — KRX 로그인 불필요 fallback.

한국예탁결제원(KSD) 세이브로 데이터를 data.go.kr 경유로 fetch.
pykrx 가 KRX_ID/KRX_PW 의존인 반면 Seibro 는 동일 DATA_GO_KR_API_KEY 만
필요 — pykrx 부재/장애 시 외국인 보유비율 + 한도소진율 독립 백업.

data.go.kr 활용신청 필요:
  '한국예탁결제원_주식 외국인보유비중' (무료)
같은 DATA_GO_KR_API_KEY 공유.  12h 디스크 캐시.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta, timezone, datetime
from pathlib import Path
from typing import Optional

import requests

from bot.env_keys import env_key as _env_key

log = logging.getLogger("bot.seibro")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "seibro"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 20
_KST = timezone(timedelta(hours=9))

_HOST = "https://apis.data.go.kr/1160100"
_SERVICE_NEW = f"{_HOST}/service/GetStockSecuritiesInfoService"
_SERVICE_OLD = f"{_HOST}/service/GetStocSecuritiesInfoService"
_OP = "getStockForeignStat"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}

_KEY_WARNED = False


def seibro_key_ready() -> bool:
    global _KEY_WARNED
    from bot.env_keys import env_key
    ready = bool(env_key("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("seibro: DATA_GO_KR_API_KEY 미설정 — 외국인보유 skip.")
        _KEY_WARNED = True
    return ready


def _kr_code(ticker: str) -> str:
    t = (ticker or "").split(".")[0].strip().upper()
    if t.startswith("A") and t[1:].isdigit():
        t = t[1:]
    return t


def _is_kr_ticker(ticker: str) -> bool:
    if not ticker:
        return False
    parts = ticker.split(".")
    if len(parts) < 2:
        return False
    return parts[-1].upper() in ("KS", "KQ")


def _cache_get(key: str) -> Optional[dict]:
    f = _CACHE_DIR / f"{key}.json"
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h >= _CACHE_TTL_HOURS:
            return None
        return json.loads(f.read_text())
    except Exception:
        return None


def _cache_set(key: str, data):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        pass


def _try_fetch(service_url: str, key: str, q: dict) -> Optional[list]:
    url = f"{service_url}/{_OP}"
    try:
        if "%" in key:
            from urllib.parse import urlencode
            resp = requests.get(
                f"{url}?serviceKey={key}&{urlencode(q)}",
                headers=_HEADERS, timeout=_TIMEOUT,
            )
        else:
            resp = requests.get(
                url, params={"serviceKey": key, **q},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
        if resp.status_code != 200:
            log.info("seibro: %s → HTTP %d", service_url.split("/")[-1], resp.status_code)
            return None
        body = (resp.json() or {}).get("response", {}).get("body", {}) or {}
        items = (body.get("items") or {}).get("item")
        if items is None:
            log.info("seibro: %s → no items", service_url.split("/")[-1])
            return None
        result = items if isinstance(items, list) else [items]
        log.info("seibro: %s → %d items", service_url.split("/")[-1], len(result))
        return result
    except Exception as exc:
        log.info("seibro: %s → %s", service_url.split("/")[-1], exc)
        return None


def _fetch_items(params: dict) -> list[dict]:
    if not seibro_key_ready():
        return []
    key = _env_key("DATA_GO_KR_API_KEY")
    q = {
        "resultType": "json",
        "numOfRows": params.pop("numOfRows", 100),
        "pageNo": 1,
        **params,
    }
    items = _try_fetch(_SERVICE_NEW, key, dict(q))
    if items:
        return items
    items = _try_fetch(_SERVICE_OLD, key, dict(q))
    return items or []


def _float(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_foreign_holding(ticker: str) -> Optional[dict]:
    """Fetch foreign ownership data for a KR ticker from Seibro/KSD.

    Returns:
        {
            "date": str (YYYYMMDD),
            "foreign_pct": float (외국인보유비율 %),
            "foreign_shares": int (외국인보유수량),
            "listed_shares": int (상장주식수),
            "limit_shares": int (외국인한도수량, if available),
            "limit_exhaustion_pct": float (한도소진율 %, if available),
        }
        or None on failure.
    """
    if not _is_kr_ticker(ticker):
        return None

    code = _kr_code(ticker)
    if not code:
        return None

    today = date.today().isoformat()
    ck = f"foreign_{code}_{today}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    begin = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
    items = _fetch_items({
        "likeSrtnCd": code,
        "beginBasDt": begin,
        "numOfRows": 30,
    })

    best = None
    for it in items:
        it_code = _kr_code(it.get("srtnCd", ""))
        if it_code != code:
            continue
        if best is None or str(it.get("basDt", "")) > str(best.get("basDt", "")):
            best = it

    if best is None:
        return None

    foreign_pct = _float(best.get("frnHldnRt"))
    if foreign_pct is None:
        foreign_pct = _float(best.get("frnOwnhRt"))
    if foreign_pct is None:
        return None

    foreign_shares = _float(best.get("frnHldnQty"))
    if foreign_shares is None:
        foreign_shares = _float(best.get("frnOwnhQty"))

    listed_shares = _float(best.get("lstgStCnt"))

    limit_shares = _float(best.get("frnOrdLmtQty"))
    limit_exhaust = _float(best.get("frnOrdLmtExhstRt"))

    result = {
        "date": str(best.get("basDt", "")),
        "foreign_pct": round(foreign_pct, 2),
        "foreign_shares": int(foreign_shares) if foreign_shares else 0,
        "listed_shares": int(listed_shares) if listed_shares else 0,
    }
    if limit_shares is not None:
        result["limit_shares"] = int(limit_shares)
    if limit_exhaust is not None:
        result["limit_exhaustion_pct"] = round(limit_exhaust, 2)

    _cache_set(ck, result)
    return result


def fetch_foreign_trend(ticker: str, days: int = 30) -> Optional[dict]:
    """Fetch foreign ownership trend over N days (KRX-login-free).

    Returns same shape as pykrx get_kr_foreign_ownership_trend:
        {
            "current_pct": float,
            "start_pct": float,
            "change_pp": float,
            "days": int,
            "source": "seibro",
        }
        or None.
    """
    if not _is_kr_ticker(ticker):
        return None

    code = _kr_code(ticker)
    if not code:
        return None

    today = date.today().isoformat()
    ck = f"ftrend_{code}_{today}_{days}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    begin = (date.today() - timedelta(days=days + 10)).strftime("%Y%m%d")
    items = _fetch_items({
        "likeSrtnCd": code,
        "beginBasDt": begin,
        "numOfRows": 200,
    })

    rows = []
    for it in items:
        if _kr_code(it.get("srtnCd", "")) != code:
            continue
        pct = _float(it.get("frnHldnRt"))
        if pct is None:
            pct = _float(it.get("frnOwnhRt"))
        if pct is not None:
            rows.append({
                "date": str(it.get("basDt", "")),
                "pct": pct,
            })

    if len(rows) < 2:
        return None

    rows.sort(key=lambda r: r["date"])

    result = {
        "current_pct": rows[-1]["pct"],
        "start_pct": rows[0]["pct"],
        "change_pp": round(rows[-1]["pct"] - rows[0]["pct"], 2),
        "days": len(rows),
        "source": "seibro",
    }
    _cache_set(ck, result)
    return result


def format_foreign_holding_block(data: Optional[dict]) -> str:
    """Format Seibro foreign holding into a compact text block."""
    if not data:
        return ""
    pct = data.get("foreign_pct")
    if pct is None:
        return ""

    _src_map = {"krx": "KRX", "naver": "네이버"}
    src = _src_map.get(data.get("source", ""), "세이브로/KSD")
    lines = [f"• 외국인 보유현황 ({src}, {data.get('date', '?')}):"]
    shares = data.get("foreign_shares", 0)
    listed = data.get("listed_shares", 0)
    lines.append(f"  보유비율: {pct:.2f}% ({shares:,}주 / {listed:,}주)")

    exhaust = data.get("limit_exhaustion_pct")
    if exhaust is not None:
        limit_shares = data.get("limit_shares", 0)
        lines.append(f"  한도소진율: {exhaust:.2f}% (한도 {limit_shares:,}주)")
        if exhaust >= 95:
            lines.append("  ⚠️ 한도소진율 95%+ — 추가 매수 ceiling 임박")
        elif exhaust >= 80:
            lines.append("  ⚠️ 한도소진율 80%+ — 제한 접근 중")

    return "\n".join(lines)
