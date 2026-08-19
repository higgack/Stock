"""FRED (Federal Reserve Economic Data) Open API client.

Used as a single-source macro adapter for non-US markets where yfinance
doesn't reliably surface central-bank-policy / government-bond-yield /
inflation series. Phase 3 starts with Japan (BoJ policy rate, JGB 10Y,
JP CPI YoY); future phases (EU, CN) extend via the same module.

FRED docs: https://fred.stlouisfed.org/docs/api/fred/
Endpoint shape:
  /fred/series/observations?series_id=ID&api_key=KEY&file_type=json
  &observation_start=YYYY-MM-DD&sort_order=desc&limit=N

Auth: FRED_API_KEY env var (free at https://fredaccount.stlouisfed.org/).
Missing key ⇒ silent None and the rest of the analysis continues.

Cache: per (series_id, today) at ~/.tradingagents/cache/fred/ with 12h
TTL — monthly series rarely move intra-day, daily series tolerate the
freshness gap given the 5-day analysis horizon.
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

from bot.env_keys import env_key as _env_key

log = logging.getLogger("bot.fred")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "fred"
_CACHE_TTL_HOURS = 12
_BASE_URL = "https://api.stlouisfed.org/fred"
_TIMEOUT = 10

# Per-market series catalog. Each entry is one indicator we surface in
# the macro block; the FRED series_id maps to OECD / IMF / BoJ-sourced
# data via FRED's mirror. label / unit drive the prompt rendering.
#
# Why FRED instead of direct BoJ API: BoJ's API is Japanese-only with
# inconsistent CSV formatting; FRED mirrors the same series with a
# consistent JSON shape and a single free API key works for JP/EU/CN.
_SERIES_JP = {
    "policy_rate": {
        "series_id": "IRSTCB01JPM156N",
        "label": "BoJ 정책금리",
        "unit": "%",
        "lookback_days": 365,
    },
    "jp10y": {
        "series_id": "IRLTLT01JPM156N",
        "label": "JGB 10Y 국채금리",
        "unit": "%",
        "lookback_days": 90,
    },
    "cpi_yoy": {
        "series_id": "CPALTT01JPM659N",
        "label": "JP CPI (전년동월비)",
        "unit": "%",
        "lookback_days": 365,
    },
}

# TW series: 中央銀行 (CBC) 重貼現率 (discount rate) + 10Y bond yield
# + CPI YoY. FRED has these via OECD mirror — same key works for TW
# as JP. Slightly thinner coverage than JP (some TW-specific
# indicators don't make it to FRED), but the headline policy-rate +
# yield-curve + inflation triple is enough to ground a JP-quality
# macro block.
_SERIES_TW = {
    "policy_rate": {
        "series_id": "IRSTCB01TWM156N",
        "label": "CBC 重貼現率 (TW 정책금리)",
        "unit": "%",
        "lookback_days": 365,
    },
    "tw10y": {
        "series_id": "IRLTLT01TWM156N",
        "label": "TW 10Y 公債 yield",
        "unit": "%",
        "lookback_days": 90,
    },
    "cpi_yoy": {
        "series_id": "CPALTT01TWM659N",
        "label": "TW CPI (전년동월비)",
        "unit": "%",
        "lookback_days": 365,
    },
}

_MARKETS = {
    "JP": _SERIES_JP,
    "TW": _SERIES_TW,
}


def _fetch_series(series_id: str, lookback_days: int) -> Optional[dict]:
    """Fetch latest observation from a FRED series. Returns dict with
    value / time / prev_value / change, or None on any failure."""
    api_key = _env_key("FRED_API_KEY")
    if not api_key:
        log.warning("fred: FRED_API_KEY missing — %s unavailable", series_id)
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"{series_id}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("fred: cache read failed for %s: %s", series_id, exc)

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    url = (
        f"{_BASE_URL}/series/observations"
        f"?series_id={series_id}"
        f"&api_key={api_key}"
        f"&file_type=json"
        f"&observation_start={start}"
        f"&sort_order=desc"
        f"&limit=10"
    )

    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning("fred: HTTP fetch failed for %s: %s", series_id, exc)
        return None

    obs = payload.get("observations") or []
    # FRED returns "." for missing values; filter them out.
    clean: list[tuple[str, float]] = []
    for row in obs:
        v = row.get("value", "")
        d = row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            clean.append((d, float(v)))
        except ValueError:
            continue
    if not clean:
        log.info("fred: empty observations for %s (start %s)", series_id, start)
        return None

    # desc order: clean[0] = latest, clean[1] = previous period.
    latest_date, latest_val = clean[0]
    prev_val: Optional[float] = clean[1][1] if len(clean) >= 2 else None
    change = (latest_val - prev_val) if prev_val is not None else None

    result = {
        "value": latest_val,
        "time": latest_date,
        "prev_value": prev_val,
        "change": change,
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        log.warning("fred: cache write failed for %s: %s", series_id, exc)

    return result


def fetch_history(series_id: str, start: str = "2018-01-01",
                  ttl_hours: float = 5.0) -> list[tuple[str, float]]:
    """시리즈 전체 히스토리 [(date, value)] 오름차순. FRED 보드(ppi/liquidity
    대시보드)용 — _fetch_series(최신값)와 달리 시계열 전량. 실패/키부재 → [].
    캐시 per series+today, 기본 5h — 보드는 더 짧은 TTL 을 명시로 넘긴다(fred_boards._HIST_TTL_H). 6시간 주기 시절 기본값이
    매 사이클 신선한 값을 받게(12h 면 두 사이클이 같은 캐시)."""
    api_key = _env_key("FRED_API_KEY")
    if not api_key:
        log.warning("fred: FRED_API_KEY missing — history %s unavailable", series_id)
        return []
    cache_file = _CACHE_DIR / f"hist_{series_id}_{date.today().isoformat()}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < ttl_hours:
                return [tuple(x) for x in json.loads(cache_file.read_text())]
        except Exception as exc:
            log.warning("fred: hist cache read failed for %s: %s", series_id, exc)
    url = (f"{_BASE_URL}/series/observations?series_id={series_id}"
           f"&api_key={api_key}&file_type=json&observation_start={start}"
           f"&sort_order=asc&limit=100000")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        obs = resp.json().get("observations") or []
    except Exception as exc:
        log.warning("fred: history fetch failed for %s: %s", series_id, exc)
        return []
    clean: list[tuple[str, float]] = []
    for row in obs:
        v, d = row.get("value", ""), row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            clean.append((d, float(v)))
        except ValueError:
            continue
    if clean:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            # 날짜 키 파일이라 어제 것은 재사용 불가 — 쓰기 전에 같은 시리즈의
            # 옛 hist 캐시 삭제(무한 누적 디스크 누수 방지, 리뷰 finding).
            for old in _CACHE_DIR.glob(f"hist_{series_id}_*.json"):
                if old != cache_file:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            cache_file.write_text(json.dumps(clean, ensure_ascii=False))
        except Exception as exc:
            log.warning("fred: hist cache write failed for %s: %s", series_id, exc)
    return clean


def fetch_observation_asof(series_id: str, asof: str,
                           ttl_hours: float = 24.0) -> Optional[tuple[str, float]]:
    """`asof` **당시 FRED 에 실제로 공표돼 있던** 마지막 관측 (date, value).

    FRED 의 vintage(ALFRED) 질의 — `realtime_start=realtime_end=asof` 를 주면
    그 날짜 시점의 데이터베이스를 그대로 돌려준다. 왜 필요한가(2026-08-19
    econ_actual_probe 실측): 지금 받은 시계열로 과거 발표일의 실제치를 고르면
    **그때는 아직 없던 관측**이 붙는다 — CPI 7/14 발표(6월분)에 7월 관측이
    붙어 8/12 발표와 **같은 숫자**가 나왔다. 시차 창을 손으로 맞추는 건
    지표마다 다른 규약을 사람이 외우는 일이라 매번 틀린다(#24·#27).
    여기선 원천이 스스로 답한다 — 추정 0.

    과거 시점 질의라 답이 변하지 않는다 → 캐시 기본 24h. 실패/키부재 → None
    (호출부는 기존 시차-창 경로로 폴백한다)."""
    api_key = _env_key("FRED_API_KEY")
    if not api_key:
        return None
    cache_file = _CACHE_DIR / f"asof_{series_id}_{asof}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < ttl_hours:
                got = json.loads(cache_file.read_text())
                return (got[0], got[1]) if got else None
        except Exception as exc:                               # noqa: BLE001
            log.warning("fred: asof cache read failed %s@%s: %s",
                        series_id, asof, exc)
    url = (f"{_BASE_URL}/series/observations?series_id={series_id}"
           f"&api_key={api_key}&file_type=json"
           f"&realtime_start={asof}&realtime_end={asof}"
           f"&sort_order=desc&limit=10")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        obs = resp.json().get("observations") or []
    except Exception as exc:                                   # noqa: BLE001
        log.warning("fred: asof fetch failed %s@%s: %s", series_id, asof, exc)
        return None
    out: Optional[tuple[str, float]] = None
    for row in obs:                     # desc 정렬 — 값이 있는 첫 행이 최신
        v, d = row.get("value", ""), row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            out = (d, float(v))
        except ValueError:
            continue
        break
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(list(out) if out else None))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("fred: asof cache write failed %s@%s: %s", series_id, asof, exc)
    return out


def fetch_series_meta(series_id: str) -> Optional[dict]:
    """FRED 가 스스로 보고하는 시리즈 메타 — `observation_end`(원천이 가진
    마지막 관측일) · `last_updated` · `frequency` · `title`. 실패/키부재 → None.

    ⚠️ 신선도 조사에서 **'우리 수집이 끊긴 것'과 '원천이 늦는 것'을 가르는
    유일한 증거**다(2026-08-19 FDHBFIN). 관측치만 보면 둘이 똑같이 보이므로
    지연 판정 때는 이 값을 같이 읽는다 — 캐시 없음(진단 경로, 매번 원천 확인).
    """
    api_key = _env_key("FRED_API_KEY")
    if not api_key:
        return None
    url = (f"{_BASE_URL}/series?series_id={series_id}"
           f"&api_key={api_key}&file_type=json")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("seriess") or []
    except Exception as exc:                                   # noqa: BLE001
        log.warning("fred: series meta failed for %s: %s", series_id, exc)
        return None
    return rows[0] if rows else None


def fetch_releases_catalog(ttl_hours: float = 24.0) -> list[dict]:
    """FRED 전체 release 카탈로그([{id,name}, ...]) — 24h 캐시. release_id 를
    숫자로 하드코딩하지 않고 이름 검색으로 찾기 위함(release_id 는 문서마다
    다르게 인용되어 오기 위험 — 경제캘린더(bot/econ_calendar.py)가 이 카탈로그
    에서 이름으로 조회). 키 부재/실패 → []."""
    api_key = _env_key("FRED_API_KEY")
    if not api_key:
        log.warning("fred: FRED_API_KEY missing — releases catalog unavailable")
        return []
    cache_file = _CACHE_DIR / f"releases_catalog_{date.today().isoformat()}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < ttl_hours:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("fred: releases catalog cache read failed: %s", exc)
    url = (f"{_BASE_URL}/releases?api_key={api_key}&file_type=json&limit=1000")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        releases = resp.json().get("releases") or []
    except Exception as exc:
        log.warning("fred: releases catalog fetch failed: %s", exc)
        return []
    result = [{"id": r.get("id"), "name": r.get("name", "")} for r in releases if r.get("id")]
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for old in _CACHE_DIR.glob("releases_catalog_*.json"):
            if old != cache_file:
                try:
                    old.unlink()
                except OSError:
                    pass
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        log.warning("fred: releases catalog cache write failed: %s", exc)
    return result


def find_release_id(name_substring: str) -> Optional[int]:
    """이름 부분일치(대소문자무관)로 release_id 조회 — 첫 매치 반환(카탈로그
    순서). 매치 없음/카탈로그 미가용 → None(호출부가 graceful 하게 생략)."""
    needle = name_substring.lower()
    for r in fetch_releases_catalog():
        if needle in (r.get("name") or "").lower():
            return r.get("id")
    return None


def fetch_release_dates(release_id: int, start: str, end: str,
                        ttl_hours: float = 12.0) -> list[str]:
    """release_id 의 예정/과거 발표일 목록(YYYY-MM-DD, 오름차순) — [start,end]
    구간. FRED 는 발표 몇 달~1년 전부터 예정일을 공개하므로 미래 구간도 조회
    가능(예: FOMC/CPI/고용동향). 키 부재/실패 → []."""
    api_key = _env_key("FRED_API_KEY")
    if not api_key:
        log.warning("fred: FRED_API_KEY missing — release %s dates unavailable", release_id)
        return []
    cache_file = _CACHE_DIR / f"reldates_{release_id}_{start}_{end}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < ttl_hours:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("fred: release dates cache read failed for %s: %s", release_id, exc)
    url = (f"{_BASE_URL}/release/dates?release_id={release_id}"
           f"&api_key={api_key}&file_type=json&sort_order=asc"
           f"&include_release_dates_with_no_data=true"
           f"&realtime_start={start}&realtime_end={end}")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("release_dates") or []
    except Exception as exc:
        log.warning("fred: release dates fetch failed for %s: %s", release_id, exc)
        return []
    dates = sorted({r["date"] for r in rows if r.get("date")})
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(dates, ensure_ascii=False))
    except Exception as exc:
        log.warning("fred: release dates cache write failed for %s: %s", release_id, exc)
    return dates


def fetch_macro(market: str) -> dict:
    """Return {key: indicator} for all defined series in this market.
    Missing / failed indicators are silently dropped — callers should
    check whether the dict is non-empty before rendering."""
    catalog = _MARKETS.get(market.upper())
    if not catalog:
        return {}
    out: dict[str, dict] = {}
    for key, cfg in catalog.items():
        obs = _fetch_series(cfg["series_id"], cfg["lookback_days"])
        if not obs:
            continue
        out[key] = {
            **obs,
            "label": cfg["label"],
            "unit": cfg["unit"],
        }
    return out


def format_macro_for_prompt(macro: dict, market: str) -> str:
    """Render the macro dict as a bullet list for prompt injection.
    Each line shows value + change-from-previous (+/-) + reference date."""
    if not macro:
        return ""
    market = market.upper()
    header = {
        "JP": "JP 거시 (FRED 미러: BoJ / OECD / IMF):",
        "TW": "TW 거시 (FRED 미러: CBC / OECD):",
    }.get(market, f"{market} 거시 (FRED):")

    order = {
        "JP": ["policy_rate", "jp10y", "cpi_yoy"],
        "TW": ["policy_rate", "tw10y", "cpi_yoy"],
    }.get(market, list(macro.keys()))

    lines = [header]
    for key in order:
        ind = macro.get(key)
        if not ind:
            continue
        value = ind.get("value", 0)
        unit = ind.get("unit", "")
        label = ind.get("label", key)
        change = ind.get("change")
        time_s = ind.get("time", "")
        change_part = ""
        if change is not None:
            if change > 0:
                change_part = f" (전기 대비 +{change:.2f}{unit})"
            elif change < 0:
                change_part = f" (전기 대비 {change:.2f}{unit})"
        date_part = f" — {time_s}" if time_s else ""
        lines.append(f"  • {label}: {value:.2f}{unit}{change_part}{date_part}")
    return "\n".join(lines) if len(lines) > 1 else ""
