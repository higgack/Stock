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

from bot.env_keys import env_key as _env_key

log = logging.getLogger("bot.bok_ecos")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "bok_ecos"
_CACHE_TTL_HOURS = 12
# ECOS 한 요청의 행 상한. 시계열 경로가 이미 1000 을 쓰고 있어 맞춘다 —
# 같은 원천을 두 값으로 부르면 한쪽만 잘린다(#38).
_ROW_CAP = 1000
# 시리즈 캐시 스키마 버전 — 페이지네이션 도입(2026-08-27) 전의 절단된
# 결과를 같은 날 캐시가 서빙하지 않게 파일명에 싣는다(#21b·#124).
_SERIES_CACHE_VER = 2
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
    "import_amt": {
        "table": "901Y118",   # 3.2.1 통관 수출입 총괄 (수출과 동일 표, 사용자 2026-06-23)
        "item": "T004",       # 수입금액 (ECOS item 확인 2026-06-23)
        "freq": "M",
        "label": "한국 수입",
        "unit": "억$",
        "scale": 1e-5,        # ECOS 천달러 → 억달러(÷100,000)
        "lookback_days": 400,
    },
    "fx_reserve": {
        "table": "732Y001",   # 3.5 외환보유액
        "item": "99",         # 합계 (ECOS item 확인 2026-06-23)
        "freq": "M",
        "label": "외환보유액",
        "unit": "억$",
        "scale": 1e-5,        # ECOS 천달러 → 억달러(÷100,000)
        "lookback_days": 400,
    },
    "kr_gdp": {
        "table": "902Y015",   # 9.1.4.1 국제 주요국 경제성장률 (한국=KOR, 분기 전기대비%)
        "item": "KOR",        # 한국 (ECOS item 확인 2026-06-23)
        "freq": "Q",          # 분기 — fetch_series_points 가 YYYYQn 처리(_fetch_indicator 는 Q 미지원→prompt 만 skip, 카드는 정상)
        "label": "한국 GDP",
        "unit": "%",          # 성장률 — 스케일 없음
        "lookback_days": 1500,  # 분기 → ~16개 분기(스파크라인 충분)
    },
    "m2": {
        # 유동성 보드 한국 M2 — FRED 의 OECD Korea 통화량(MANMM101KRM189S)이
        # 2023-10 이후 중단(OECD MEI 개편)이라 한국은행 원천으로 대체
        # (사용자 2026-07-04 'FRED 중단분은 우리 자원으로 대체').
        # ⚠️ 아이템 코드 하드코딩 금지 — 첫 배포의 101Y004/BBHA00 이 INFO-200
        # (구계열 1.7장, 1986~2004 종료 — VM TableList 확인 2026-07-04).
        # 현행 신계열(1.1장) = 161Y006. 이름해석(StatisticItemList 런타임
        # 매칭, CYCLE·END_TIME 신선 필터) + 계절조정(161Y005) 폴백.
        "table": "161Y006",   # 1.1.3.1.2 M2 상품별 구성내역(평잔, 원계열) — 현행
        "alt_tables": ["161Y005"],   # 평잔, 계절조정계열 폴백
        "item_name": "M2",    # 이름해석(코드 하드코딩 금지)
        "freq": "M",
        "label": "한국 M2(평잔)",
        "unit": "조원",
        "scale": 0.001,       # ECOS 십억원 → 조원(÷1,000)
        "lookback_days": 2600,  # ~7년 월간(보드 5년 창 + YoY 여유)
    },
}


def _fetch_indicator(key: str) -> Optional[dict]:
    """Fetch one indicator from ECOS. Returns dict or None."""
    cfg = _SERIES.get(key)
    if not cfg:
        return None

    api_key = _env_key("BOK_ECOS_API_KEY")
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
    def _req(lo: int, hi: int):
        url = (
            f"{_BASE_URL}/StatisticSearch/{api_key}/json/kr/{lo}/{hi}/"
            f"{cfg['table']}/{cfg['freq']}/{start_str}/{end_str}/{cfg['item']}"
        )
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("ecos: HTTP fetch failed for %s [%d-%d]: %s", key, lo, hi, exc)
            return None

    # ⚠️ **행 상한이 최신을 자를 수 있다.** 옛 코드는 `/1/100/` 로 받아
    # 정렬 후 `rows[-1]` 을 최신이라 불렀는데, 같은 원천의 다른 경로는 1000행
    # 이었다(#38 두 곳이 다르면 갈라진다). 한 표가 부가 차원(품목·국가 등)을
    # 함께 주면 100행은 쉽게 넘고, 그때 잘리는 쪽이 **최신 월**이라 카드가
    # 조용히 한 달 뒤처진다.
    payload = _req(1, _ROW_CAP)
    if payload is None:
        return None

    # ECOS error shape: {"RESULT": {"CODE": "INFO-200", "MESSAGE": "..."}}
    # Success shape:    {"StatisticSearch": {"list_total_count": N, "row": [...]}}
    if "RESULT" in payload and "StatisticSearch" not in payload:
        log.warning("ecos: API error for %s: %s", key, payload.get("RESULT"))
        return None
    _ss = payload.get("StatisticSearch", {}) or {}
    rows = _ss.get("row") or []
    # ⚠️ **원천이 총 건수를 알려주는데 세어 보지 않았다**(#173). 잘렸는지를
    # 추측하지 말고 `list_total_count` 로 판정하고, 잘렸으면 **꼬리 페이지**를
    # 다시 받는다 — 지연인지 절단인지 로그가 말하게 한다(#82).
    total = _ss.get("list_total_count")
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    if total is not None and total > len(rows):
        log.warning("ecos: %s 응답 절단 — 총 %d행 중 %d행만 받음, 꼬리 재요청",
                    key, total, len(rows))
        tail = _req(max(1, total - _ROW_CAP + 1), total)
        _trows = ((tail or {}).get("StatisticSearch", {}) or {}).get("row") or []
        if _trows:
            rows = _trows
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
    api_key = _env_key("BOK_ECOS_API_KEY")
    if not api_key:
        return []

    lb = lookback_days if lookback_days is not None else max(int(cfg["lookback_days"]), 400)
    today_str = date.today().isoformat()
    # 파일명에 버전(#124 손으로 올리는 버전은 잊는다 — 여기선 페이지네이션
    # 도입 시점 v2): 같은 날 배포돼도 절단된 옛 결과를 12h 동안 서빙하지
    # 않게(#21b 캐시가 fix 를 가린다).
    cache_file = _CACHE_DIR / f"series_v{_SERIES_CACHE_VER}_{key}_{lb}_{today_str}.json"
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

    # 표 후보 순회 — item_name 이 있으면 표별 StatisticItemList 이름해석
    # (KR PPI _match_items 재사용, 코드 하드코딩 금지 — BBHA00 INFO-200 재발
    # 방지 2026-07-04). 정적 item 시리즈는 기존 그대로 단일 표 1회.
    rows: list[dict] = []
    for _tbl in [cfg["table"]] + list(cfg.get("alt_tables", [])):
        _item = cfg.get("item", "")
        if cfg.get("item_name"):
            _all = _fetch_item_list(api_key, table=_tbl)
            # 주기 일치 + 최근까지 제공되는 항목만 — legacy(구계열) 동명
            # 항목 오매칭 차단(END_TIME 이 13개월 이내 신선한 것만).
            _cutoff = (date.today() - timedelta(days=400)).strftime("%Y%m")
            _cands = _filter_series_items(_all, cfg["freq"], _cutoff)
            _code = _match_items(_cands, [cfg["item_name"]]).get(cfg["item_name"])
            if not _code:
                log.warning("ecos: %s item '%s' unmatched in %s "
                            "(items=%d, 신선 %d)",
                            key, cfg["item_name"], _tbl, len(_all), len(_cands))
                continue
            log.info("ecos: %s resolved %s/'%s' → %s",
                     key, _tbl, cfg["item_name"], _code)
            _item = _code
        def _sreq(lo: int, hi: int, _t=_tbl, _i=_item):
            url = (
                f"{_BASE_URL}/StatisticSearch/{api_key}/json/kr/{lo}/{hi}/"
                f"{_t}/{cfg['freq']}/{start_str}/{end_str}/{_i}"
            )
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()

        try:
            payload = _sreq(1, _ROW_CAP)
        except Exception as exc:
            log.warning("ecos: series fetch failed for %s (%s): %s", key, _tbl, exc)
            continue
        if "RESULT" in payload and "StatisticSearch" not in payload:
            # silent-fail 금지(사용자 2026-06-23 '카드 빠짐 왜?'): ECOS RESULT
            # 에러(잘못된 table/item·키한도·데이터없음)를 로그로 가시화 — 카드가
            # 조용히 드롭되던 근본원인 추적용. CODE/MESSAGE 그대로 노출.
            _r = payload.get("RESULT") or {}
            log.warning("ecos: %s RESULT %s — %s (table=%s item=%s)", key,
                        _r.get("CODE"), _r.get("MESSAGE"), _tbl, _item)
            continue
        _ss = payload.get("StatisticSearch", {}) or {}
        rows = _ss.get("row") or []
        # ⚠️ **화면(스파크라인·기준월)이 쓰는 경로는 여기다** — #251 은 단일값
        # 경로(_fetch_indicator)에만 절단 검사를 넣어 카드가 그대로 뒤처질 수
        # 있었다(#35 고친 경로와 화면 경로가 다름). 원천이 총 건수를 주므로
        # 세어 보고(#173), 잘렸으면 나머지 페이지를 마저 받는다 — 스파크라인은
        # 전 구간이 필요해 꼬리만이 아니라 페이지 전체를 잇는다. 상한 5쪽 =
        # 비용 유계(#66), 로그가 절단 사실을 말한다(#82).
        try:
            _total = int(_ss.get("list_total_count"))
        except (TypeError, ValueError):
            _total = None
        _page = 1
        while _total is not None and len(rows) < _total and _page < 5:
            log.warning("ecos: %s 시리즈 응답 절단 — 총 %d행 중 %d행 수신, "
                        "%d쪽째 재요청", key, _total, len(rows), _page + 1)
            try:
                _more = _sreq(_page * _ROW_CAP + 1, (_page + 1) * _ROW_CAP)
            except Exception as exc:                           # noqa: BLE001
                log.warning("ecos: %s 추가 페이지 실패: %s", key, exc)
                break
            _mrows = ((_more or {}).get("StatisticSearch", {}) or {}).get("row") or []
            if not _mrows:
                break
            rows = list(rows) + _mrows
            _page += 1
        if rows:
            break
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


# ── 한국 생산자물가(PPI) 히스토리 — FRED 보드 KR PPI 섹션용 (2026-07-02) ────
# 404Y014(생산자물가지수, 월간). 아이템 코드는 하드코딩하지 않고
# StatisticItemList 로 목록을 받아 **이름 부분일치**로 해석 — 코드 오타/개편에
# 강건(무매칭 = 그 항목만 생략, graceful). 캐시 12h(다른 지표와 동일).
_KR_PPI_TABLE = "404Y014"


def _filter_series_items(rows: list[dict], freq: str,
                         min_end_yyyymm: str) -> list[dict]:
    """시리즈 이름해석용 아이템 필터 — CYCLE == freq 이고 END_TIME 이 최근
    (min_end_yyyymm 이상)인 항목만. legacy 표의 동명 항목(예: 101Y004 'M2'
    가 CYCLE A/M/Q 로 있지만 전부 END 2004 — 2026-07-04 실사례)이 이름만
    같아 매칭돼 INFO-200 을 내던 것 차단. END_TIME 포맷 다양('2003'/'200409'/
    '2004Q3'/'20040930') — 숫자만 추출해 YYYYMM 로 정규화(연도만이면 12월
    간주) 후 비교. 순수(테스트)."""
    import re as _re
    out = []
    for r in rows:
        if (r.get("CYCLE") or "").strip() != freq:
            continue
        raw = str(r.get("END_TIME") or "")
        qm = _re.match(r"^(\d{4})Q([1-4])$", raw.strip())
        if qm:   # 'YYYYQn' — 숫자추출('20043', len 5)로는 버려지던 분기
                 # 포맷 명시 처리(리뷰 2026-07-04 #4, 미래 Q 시리즈 대비)
            end = f"{qm.group(1)}{int(qm.group(2)) * 3:02d}"
        else:
            digits = _re.sub(r"\D", "", raw)
            if len(digits) >= 6:
                end = digits[:6]
            elif len(digits) == 4:
                end = digits + "12"
            else:
                continue
        if end >= min_end_yyyymm:
            out.append(r)
    return out


def _match_items(rows: list[dict], patterns: list[str]) -> dict[str, str]:
    """아이템 목록에서 패턴별 코드 해석 {pattern: item_code}. 정확일치 우선,
    없으면 부분일치 중 **ITEM_CODE 가 가장 짧은 것**(ECOS 계층에서 짧은 코드 =
    상위 집계 — '철강선' 같은 세부 품목이 이름만 짧아 오매칭되던 최단이름
    휴리스틱을 교정, 리뷰 finding. 코드 길이 동률이면 이름 짧은 쪽). 이름 비교는
    양쪽 다 strip. 순수(테스트)."""
    out: dict[str, str] = {}
    for pat in patterns:
        exact = [r for r in rows if (r.get("ITEM_NAME") or "").strip() == pat]
        if exact:
            out[pat] = exact[0].get("ITEM_CODE", "")
            continue
        part = [r for r in rows
                if pat in (r.get("ITEM_NAME") or "").strip()]
        if part:
            best = min(part, key=lambda r: (len(r.get("ITEM_CODE") or ""),
                                            len((r.get("ITEM_NAME") or "").strip())))
            out[pat] = best.get("ITEM_CODE", "")
    return {k: v for k, v in out.items() if v}


def _fetch_item_list(api_key: str, table: str = _KR_PPI_TABLE) -> list[dict]:
    """통계표 아이템 전체 목록 — 페이지네이션(1000행 단위, 최대 5쪽). 기본분류
    품목이 500+ 개라 단일 1/500 요청은 뒤쪽 항목을 놓침(리뷰 finding).
    table 인자화(2026-07-04) — KR PPI(404Y014) 외 M2 등 이름해석 시리즈 공용."""
    items: list[dict] = []
    for page in range(5):
        s, e = page * 1000 + 1, (page + 1) * 1000
        try:
            url = (f"{_BASE_URL}/StatisticItemList/{api_key}/json/kr/{s}/{e}/"
                   f"{table}")
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            if "RESULT" in payload and "StatisticItemList" not in payload:
                log.warning("ecos: item list error (%s): %s",
                            table, payload.get("RESULT"))
                break
            rows = (payload.get("StatisticItemList") or {}).get("row") or []
        except Exception as exc:
            log.warning("ecos: item list failed (%s p%d): %s", table, page, exc)
            break
        items.extend(rows)
        if len(rows) < 1000:
            break
    return items


def fetch_kr_price_index_history(
        patterns: list[str], table: str = _KR_PPI_TABLE,
        cache_key: str = "kr_ppi",
        start: str = "201901") -> dict[str, list[tuple[str, float]]]:
    """{패턴: [(YYYY-MM-01, 지수), …]} — 물가지수류(품목별 이름해석) 공용
    월간 히스토리 fetcher. table 인자화(2026-07-24, KR PPI 전용이던 것을
    KR CPI 도 재사용하도록 일반화) — cache_key 로 물가종류별 캐시파일 분리
    (같은 날 두 물가지수가 파일을 덮어쓰지 않도록). 키 부재/실패 → {} 또는
    해당 항목 생략(graceful). 일 1회 재생성용(12h 캐시)."""
    api_key = _env_key("BOK_ECOS_API_KEY")
    if not api_key:
        log.info("ecos: BOK_ECOS_API_KEY missing — %s unavailable", cache_key)
        return {}
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"{cache_key}_{today_str}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                if set(patterns) <= set(cached.keys()) | set(cached.get("_missing", [])):
                    return {k: [tuple(x) for x in v] for k, v in cached.items()
                            if k != "_missing"}
        except Exception as exc:
            log.warning("ecos: %s cache read failed: %s", cache_key, exc)
    # ① 아이템 목록(페이지네이션) → 이름 해석
    items = _fetch_item_list(api_key, table=table)
    if not items:
        return {}
    codes = _match_items(items, patterns)
    if not codes:
        log.warning("ecos: %s no items matched %s", cache_key, patterns)
        return {}
    # ② 패턴별 월간 히스토리. 일시 실패(failed)는 '_missing'(진짜 무데이터)과
    # 구분해 캐시에 안 박음 — 장애가 12h 동안 '없음'으로 박제 방지 + ECOS
    # RESULT 에러 shape 는 CODE/MESSAGE 로깅(silent-fail 금지 — 리뷰 fix 2건).
    # 1/400 = 2052년까지 월간 창 여유(1/200 은 2035-09부터 최신월 잘림).
    end = date.today().strftime("%Y%m")
    out: dict[str, list[tuple[str, float]]] = {}
    failed: set[str] = set()
    for pat, code in codes.items():
        try:
            url = (f"{_BASE_URL}/StatisticSearch/{api_key}/json/kr/1/400/"
                   f"{table}/M/{start}/{end}/{code}")
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            if "RESULT" in payload and "StatisticSearch" not in payload:
                log.warning("ecos: %s API error (%s): %s",
                            cache_key, pat, payload.get("RESULT"))
                failed.add(pat)
                continue
            rows = (payload.get("StatisticSearch") or {}).get("row") or []
        except Exception as exc:
            log.warning("ecos: %s fetch failed (%s): %s", cache_key, pat, exc)
            failed.add(pat)
            continue
        pts = []
        for r in sorted(rows, key=lambda x: x.get("TIME", "")):
            t = (r.get("TIME") or "").strip()
            try:
                v = float(r.get("DATA_VALUE", "") or "nan")
            except ValueError:
                continue
            if len(t) == 6 and v == v:
                pts.append((f"{t[:4]}-{t[4:]}-01", v))
        if pts:
            out[pat] = pts
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(out)
        # 성공했지만 정말 데이터 없는 것만 _missing — failed 는 제외해 다음
        # 실행(캐시 subset 불충족)이 재시도하게.
        payload["_missing"] = [p for p in patterns
                               if p not in out and p not in failed]
        cache_file.write_text(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        log.warning("ecos: %s cache write failed: %s", cache_key, exc)
    return out


def fetch_kr_ppi_history(patterns: list[str],
                         start: str = "201901") -> dict[str, list[tuple[str, float]]]:
    """{패턴: [(YYYY-MM-01, 지수), …]} — 404Y014(한국 PPI) 월간 히스토리.
    fetch_kr_price_index_history 의 PPI 전용 얇은 래퍼(기존 호출부 시그니처
    보존 — KR PPI 보드가 이 이름으로 참조)."""
    return fetch_kr_price_index_history(patterns, table=_KR_PPI_TABLE,
                                        cache_key="kr_ppi", start=start)


# ── 한국 CPI(901Y009, 2026-07-24 'CPI 도 PPI 처럼') ────────────────────────
# 901Y009 는 이미 cpi_idx(_SERIES 총지수 item='0')가 쓰는 표 — 품목성질별
# 세부항목도 같은 표 안에 있을 것으로 보고 이름해석 재사용. 무매칭이면 그
# 항목만 생략(graceful, KR PPI 와 동일 원칙 — 코드 하드코딩 금지).
_KR_CPI_TABLE = "901Y009"


def fetch_kr_cpi_history(patterns: list[str],
                         start: str = "201901") -> dict[str, list[tuple[str, float]]]:
    """{패턴: [(YYYY-MM-01, 지수), …]} — 901Y009(한국 CPI) 월간 히스토리.
    fetch_kr_price_index_history 의 CPI 전용 얇은 래퍼."""
    return fetch_kr_price_index_history(patterns, table=_KR_CPI_TABLE,
                                        cache_key="kr_cpi", start=start)


# ── 지연 진단 CLI (2026-08-27, 사용자 "원천이 지연인지 우리문제인지") ────────
_PROBE_VER = 1


def check(keys: list[str]) -> None:
    """ECOS 카드 지연 진단 — **원천 지연**과 **우리 절단**을 가른다(#82).

    화면 판정은 화면이 쓰는 그 경로(fetch_series_points, 페이지네이션 포함)를
    그대로 태우고(#35), 원시 통계(총 건수·1쪽 수신 행수·월별 행수 = 부가 차원
    유무)는 별도 1회 조회로 찍는다 — 총 > 1쪽 수신이면 옛 코드가 절단됐던
    것이고(우리 문제, 이제 자동 복구), 절단 없이 최신 월이 뒤처져 있으면
    원천 지연이다. 키 값은 안 찍고 출처만(#23·#82)."""
    import sys
    from collections import Counter

    from bot.env_keys import env_source, env_why
    print(f"ecos_check v{_PROBE_VER} · 인터프리터 {sys.executable}")
    src = env_source("BOK_ECOS_API_KEY")
    print("자격증명 BOK_ECOS_API_KEY = " + (src or "없음")
          + ("" if src else f" ({env_why('BOK_ECOS_API_KEY')})"))
    api_key = _env_key("BOK_ECOS_API_KEY")
    for key in keys:
        cfg = _SERIES.get(key)
        print(f"\n■ {key} ({(cfg or {}).get('label', '?')})")
        if not cfg:
            print("  ❌ 미등록 시리즈")
            continue
        # ① 화면 경로 그대로(#35) — 캐시 경유 포함, 최신 월과 cadence 판정
        pts = fetch_series_points(key)
        if pts:
            print(f"  ① 화면 경로: {len(pts)}점 · 최신 {pts[-1][0]}")
            try:
                from bot.macro_cadence import judge
                j = judge(key, pts[-1][0]) or {}
                if j:
                    print("     cadence: "
                          + ("⚠️ 통상 일정보다 뒤처짐" if j.get("stale")
                             else "✅ 통상 일정 내")
                          + f" (규약: {j.get('why', '')} · 기대 {j.get('expected')}"
                          f" · 실제 {j.get('actual')})")
                else:
                    print("     cadence: 규약 미등록(판정 안 함)")
            except Exception as exc:                           # noqa: BLE001
                print(f"     cadence 판정 실패: {exc}")
        else:
            print("  ① 화면 경로: 0점 ❌ (키 없음/원천 실패/데이터 없음 — 로그 확인)")
        # ② 원시 1쪽 조회 — 총 건수·수신·월별 행수(절단/부가 차원 판정 재료)
        if not api_key:
            print("  ② 원시 조회 생략(키 없음)")
            continue
        end = date.today()
        start = end - timedelta(days=cfg["lookback_days"])
        if cfg["freq"] == "M":
            s_str, e_str = start.strftime("%Y%m"), end.strftime("%Y%m")
        elif cfg["freq"] == "D":
            s_str, e_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        else:
            s_str = f"{start.year}Q{(start.month - 1) // 3 + 1}"
            e_str = f"{end.year}Q{(end.month - 1) // 3 + 1}"
        url = (f"{_BASE_URL}/StatisticSearch/{api_key}/json/kr/1/{_ROW_CAP}/"
               f"{cfg['table']}/{cfg['freq']}/{s_str}/{e_str}/"
               f"{cfg.get('item', '')}")
        try:
            payload = requests.get(url, timeout=_TIMEOUT).json()
        except Exception as exc:                               # noqa: BLE001
            print(f"  ② 원시 조회 실패: {exc}")
            continue
        if "RESULT" in payload and "StatisticSearch" not in payload:
            _r = payload.get("RESULT") or {}
            print(f"  ② RESULT {_r.get('CODE')} — {_r.get('MESSAGE')}")
            continue
        _ss = payload.get("StatisticSearch", {}) or {}
        rows = _ss.get("row") or []
        total = _ss.get("list_total_count")
        per_t = Counter((r.get("TIME") or "") for r in rows)
        last_times = sorted(per_t)[-3:]
        print(f"  ② 원시: 총 {total}행 · 1쪽 수신 {len(rows)}행 · "
              f"조회창 {s_str}~{e_str}")
        print("     최근 기간별 행수: "
              + " · ".join(f"{t}={per_t[t]}" for t in last_times))
        try:
            truncated = int(total) > len(rows)
        except (TypeError, ValueError):
            truncated = False
        if truncated:
            print("  판정: ❌ 절단(우리 문제였음) — 총 건수 > 1쪽 수신. "
                  "화면 경로는 페이지를 이어 받아 복구한다(로그 '시리즈 응답 절단')")
        elif rows:
            print(f"  판정: 절단 없음 — 원천이 주는 최신이 {max(per_t)} 다. "
                  "이보다 새 달이 없으면 **원천(ECOS) 지연**이다")
        else:
            print("  판정: ❓ 행 0 — 조회창·테이블/아이템 코드부터 의심")


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ECOS 클라이언트 점검")
    ap.add_argument("--check", nargs="+", metavar="SERIES",
                    help="카드 지연 진단(예: --check export_amt import_amt)")
    a = ap.parse_args(argv)
    if a.check:
        check(a.check)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
