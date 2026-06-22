"""한국은행 ECOS (경제통계시스템) Open API client.

Adds KR-specific macro indicators that yfinance doesn't cover well:
the KR base rate (한국은행 기준금리), KR 10-year government bond
yield, and CPI year-over-year. yfinance gives us USD/KRW and the
US 10Y (^TNX) but the KR-specific rate environment was previously
absent — leaving the macro analyst to reason about "고금리 환경"
purely from US rates, which is the wrong frame for a KR equity.

ECOS docs: https://ecos.bok.or.kr/api/
Endpoint shape:
  /api/StatisticSearch/{KEY}/{TYPE}/{LANG}/{REQ_START}/{REQ_END}/
  {STAT_CODE}/{CYCLE}/{START_DATE}/{END_DATE}/{ITEM_CODE}

Auth: BOK_ECOS_API_KEY env var. Missing key ⇒ silent None and the
rest of the analysis continues without the BoK block.

Cache: per (indicator, today) at ~/.tradingagents/cache/bok_ecos/
with 12h TTL. Series update daily (rates) or monthly (CPI) so 12h
is generous and avoids re-fetching on every analysis in the same
window.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.bok_ecos")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "bok_ecos"
_CACHE_TTL_HOURS = 12
_BASE_URL = "https://ecos.bok.or.kr/api"
_TIMEOUT = 10

# Statistical-table + item codes confirmed against the ECOS catalog.
# table = STAT_CODE, item = ITEM_CODE1 (most series use only level-1).
# freq: D=일, M=월, Q=분기, A=년.
_SERIES = {
    "base_rate": {
        "table": "722Y001",   # 한국은행 기준금리
        "item": "0101000",
        "freq": "D",
        "label": "한국은행 기준금리",
        "unit": "%",
        "lookback_days": 90,
    },
    "kr10y": {
        "table": "817Y002",   # 시장금리 일별
        "item": "010210000",  # 국고채 10년
        "freq": "D",
        "label": "KR 10Y 국고채금리",
        "unit": "%",
        "lookback_days": 45,
    },
    "cpi_yoy": {
        "table": "901Y010",   # 소비자물가지수 전년동월비
        "item": "0",
        "freq": "M",
        "label": "KR CPI (전년동월비)",
        "unit": "%",
        "lookback_days": 365,
    },
    # ── Macro-snapshot extras (best-effort; graceful None if code drifts) ──
    "kr3y": {
        "table": "817Y002",   # 시장금리 일별
        "item": "010200000",  # 국고채 3년
        "freq": "D",
        "label": "국고채 3년",
        "unit": "%",
        "lookback_days": 45,
    },
    "cpi_idx": {
        "table": "901Y009",   # 소비자물가지수 (지수 레벨)
        "item": "0",
        "freq": "M",
        "label": "한국 CPI",
        "unit": "",
        "lookback_days": 400,
    },
    "current_account": {
        "table": "301Y017",   # 2.5.1.2 경상수지(계절조정)
        "item": "SA000",      # 경상수지 (ECOS item 확인 2026-06-23, 옛 000000=데이터없음)
        "freq": "M",
        "label": "경상수지",
        "unit": "억$",
        "scale": 0.01,        # ECOS 백만달러 → 억달러(÷100)
        "lookback_days": 400,
    },
    "export_amt": {
        "table": "901Y118",   # 3.2.1 통관기준 수출입 총괄 (옛 403Y014=데이터없음)
        "item": "T002",       # 수출금액 (ECOS item 확인 2026-06-23)
        "freq": "M",
        "label": "한국 수출",
        "unit": "억$",
        "scale": 1e-5,        # ECOS 천달러 → 억달러(÷100,000)
        "lookback_days": 400,
    },
}


def _fetch_indicator(key: str) -> Optional[dict]:
    """Fetch one indicator from ECOS. Returns dict or None."""
    cfg = _SERIES.get(key)
    if not cfg:
        return None

    api_key = os.getenv("BOK_ECOS_API_KEY", "").strip()
    if not api_key:
        log.warning("ecos: BOK_ECOS_API_KEY missing — %s unavailable", key)
        return None

    # Disk cache check
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"{key}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("ecos: cache read failed for %s: %s", key, exc)

    end = date.today()
    start = end - timedelta(days=cfg["lookback_days"])
    if cfg["freq"] == "D":
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
    elif cfg["freq"] == "M":
        start_str = start.strftime("%Y%m")
        end_str = end.strftime("%Y%m")
    else:
        # Q/A — unused for now; expand when needed.
        return None

    # URL: /StatisticSearch/{KEY}/json/kr/{REQ_START}/{REQ_END}/{STAT_CODE}/
    #      {CYCLE}/{START}/{END}/{ITEM}
    url = (
        f"{_BASE_URL}/StatisticSearch/{api_key}/json/kr/1/100/"
        f"{cfg['table']}/{cfg['freq']}/{start_str}/{end_str}/{cfg['item']}"
    )

    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning("ecos: HTTP fetch failed for %s: %s", key, exc)
        return None

    # ECOS error shape: {"RESULT": {"CODE": "INFO-200", "MESSAGE": "..."}}
    # Success shape:    {"StatisticSearch": {"list_total_count": N, "row": [...]}}
    if "RESULT" in payload and "StatisticSearch" not in payload:
        log.warning("ecos: API error for %s: %s", key, payload.get("RESULT"))
        return None
    rows = payload.get("StatisticSearch", {}).get("row") or []
    if not rows:
        log.info("ecos: empty rows for %s (range %s-%s)", key, start_str, end_str)
        return None

    # Sort ascending by TIME; pick latest + previous for change calc.
    rows = sorted(rows, key=lambda r: r.get("TIME", ""))
    _scale = float(cfg.get("scale", 1.0))   # ECOS 원단위 → 카드 단위(예: 백만$→억$)
    try:
        latest = rows[-1]
        latest_val = float(latest.get("DATA_VALUE", "") or "nan") * _scale
    except Exception:
        return None
    if latest_val != latest_val:  # NaN guard
        return None

    prev_val = None
    if len(rows) >= 2:
        try:
            prev = rows[-2]
            prev_val = float(prev.get("DATA_VALUE", "") or "nan") * _scale
            if prev_val != prev_val:
                prev_val = None
        except Exception:
            prev_val = None

    change = (latest_val - prev_val) if prev_val is not None else None
    result = {
        "value": latest_val,
        "unit": cfg["unit"],
        "label": cfg["label"],
        "time": latest.get("TIME", ""),
        "prev_value": prev_val,
        "change": change,
        "freq": cfg["freq"],
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        log.warning("ecos: cache write failed for %s: %s", key, exc)

    return result


def fetch_kr_macro() -> dict:
    """Return dict of {key: indicator} for all defined KR macro series.
    Missing / failed indicators are silently dropped — callers should
    check whether the dict is non-empty before rendering."""
    out: dict[str, dict] = {}
    for key in _SERIES:
        ind = _fetch_indicator(key)
        if ind:
            out[key] = ind
    return out


def fetch_series_points(key: str, lookback_days: int | None = None) -> list[tuple[str, float]]:
    """Return sorted [(TIME, value)] points for an ECOS series (sparkline use).

    Returns [] on missing key / API failure / empty rows. Daily series are
    returned at native daily resolution; the caller downsamples to monthly.
    Cached per (key, today) for 12h, same as the single-point fetch.
    """
    cfg = _SERIES.get(key)
    if not cfg:
        return []
    api_key = os.getenv("BOK_ECOS_API_KEY", "").strip()
    if not api_key:
        return []

    lb = lookback_days if lookback_days is not None else max(int(cfg["lookback_days"]), 400)
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"series_{key}_{lb}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return [(t, v) for t, v in json.loads(cache_file.read_text())]
        except Exception:
            pass

    end = date.today()
    start = end - timedelta(days=lb)
    if cfg["freq"] == "D":
        start_str, end_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    elif cfg["freq"] == "M":
        start_str, end_str = start.strftime("%Y%m"), end.strftime("%Y%m")
    elif cfg["freq"] == "Q":
        start_str = f"{start.year}Q{(start.month - 1) // 3 + 1}"
        end_str = f"{end.year}Q{(end.month - 1) // 3 + 1}"
    else:
        return []

    url = (
        f"{_BASE_URL}/StatisticSearch/{api_key}/json/kr/1/1000/"
        f"{cfg['table']}/{cfg['freq']}/{start_str}/{end_str}/{cfg['item']}"
    )
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning("ecos: series fetch failed for %s: %s", key, exc)
        return []
    if "RESULT" in payload and "StatisticSearch" not in payload:
        # silent-fail 금지(사용자 2026-06-23 '카드 빠짐 왜?'): ECOS RESULT 에러
        # (잘못된 table/item·키한도·데이터없음)를 로그로 가시화 — 카드가 조용히
        # 드롭되던 근본원인 추적용. CODE/MESSAGE 그대로 노출.
        _r = payload.get("RESULT") or {}
        log.warning("ecos: %s RESULT %s — %s (table=%s item=%s)", key,
                    _r.get("CODE"), _r.get("MESSAGE"), cfg.get("table"), cfg.get("item"))
        return []
    rows = payload.get("StatisticSearch", {}).get("row") or []
    _scale = float(cfg.get("scale", 1.0))   # ECOS 원단위 → 카드 단위(예: 천$→억$)
    points: list[tuple[str, float]] = []
    for r in rows:
        t = r.get("TIME", "")
        try:
            v = float(r.get("DATA_VALUE", "") or "nan") * _scale
        except Exception:
            continue
        if v != v:  # NaN
            continue
        points.append((t, v))
    points.sort(key=lambda p: p[0])

    if points:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(points, ensure_ascii=False))
        except Exception:
            pass
    return points


def _format_time(time_str: str, freq: str) -> str:
    """Render a TIME field as a human-readable date."""
    if not time_str:
        return ""
    if freq == "D" and len(time_str) == 8:
        return f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:]}"
    if freq == "M" and len(time_str) == 6:
        return f"{time_str[:4]}-{time_str[4:6]}"
    return time_str


def format_kr_macro_for_prompt(macro: dict) -> str:
    """Render the BoK indicator dict as a bullet list for prompt
    injection. Each line shows the value + change-from-previous (with
    + / - prefix) + reference date."""
    if not macro:
        return ""
    lines = ["KR 거시 (한국은행 ECOS):"]
    # Order matters for the prompt — rate / yield / inflation grouping.
    order = ["base_rate", "kr10y", "cpi_yoy"]
    for key in order:
        ind = macro.get(key)
        if not ind:
            continue
        value = ind.get("value", 0)
        unit = ind.get("unit", "")
        label = ind.get("label", key)
        change = ind.get("change")
        time_s = _format_time(ind.get("time", ""), ind.get("freq", "D"))
        change_part = ""
        if change is not None:
            if change > 0:
                change_part = f" (전기 대비 +{change:.2f}{unit})"
            elif change < 0:
                change_part = f" (전기 대비 {change:.2f}{unit})"
        date_part = f" — {time_s}" if time_s else ""
        lines.append(f"  • {label}: {value:.2f}{unit}{change_part}{date_part}")
    return "\n".join(lines) if len(lines) > 1 else ""
