"""Naver 증권 업종별 시세 + 야간선물 스크래퍼.

- fetch_sector_movers(): finance.naver.com/sise/sise_group.naver?type=upjong
  에서 업종별 등락률 → 상승 TOP / 하락 TOP 위젯용. 매일 갱신(10분 캐시).
- fetch_night_futures(): 코스피200 야간선물(EUREX/CME 연계) best-effort.
  ⚠️ Naver 정확한 엔드포인트 실측 검증 필요 — 실패 시 None(카드 미표시).

무료·무키. euc-kr 디코딩. graceful — 실패/빈 결과 시 빈값 반환.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.naver_sector")

_UPJONG_URL = "https://finance.naver.com/sise/sise_group.naver"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_sector"
_CACHE_TTL_SEC = 10 * 60  # 업종 등락률은 장중 변동 → 10분

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://finance.naver.com/sise/",
}

_DETAIL_RE = re.compile(
    r'sise_group_detail\.naver\?type=upjong[^"]*?no=\d+"[^>]*>([^<]+)</a>', re.I)
_PCT_RE = re.compile(r'([+\-]?)(\d{1,2}\.\d{1,2})\s*%')


def _get(url: str, **kwargs) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, **kwargs)
        resp.encoding = "euc-kr"
        if resp.status_code != 200 or not resp.text:
            return None
        return resp.text
    except Exception as exc:
        log.warning("naver_sector: fetch failed %s: %s", url, exc)
        return None


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&middot;", "·")
    return " ".join(s.split()).strip()


def parse_sectors(html: str) -> list[dict]:
    """업종별 시세 표 → [{name, pct}] (등락률 %). 부호는 텍스트 또는 색 클래스."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        a = _DETAIL_RE.search(row)
        if not a:
            continue
        name = _clean(a.group(1))
        if not name or name in seen:
            continue
        # 등락률 셀 — 부호 포함 숫자 우선. 텍스트에 부호 없으면 색 클래스로 추정.
        pct = None
        m = _PCT_RE.search(_clean(row))
        if m:
            num = float(m.group(2))
            sign = m.group(1)
            if sign == "-":
                num = -num
            elif sign != "+":
                # 부호 미표기 → 색 클래스(red=상승, nv/blue=하락)로 추정
                if re.search(r"(nv01|nv02|blue|down|red04|red05)", row, re.I):
                    num = -num
            pct = num
        if pct is None:
            continue
        seen.add(name)
        out.append({"name": name, "pct": round(pct, 2)})
    return out


def fetch_sector_movers(top_n: int = 10) -> dict:
    """업종 등락률 → {'up': [...], 'down': [...], 'ts': iso}.

    up = 상승 TOP n(등락률 내림차순), down = 하락 TOP n(오름차순). 10분 캐시."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / "upjong.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _CACHE_TTL_SEC:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    html = _get(_UPJONG_URL, params={"type": "upjong"})
    sectors = parse_sectors(html) if html else []
    if not sectors:
        return {"up": [], "down": [], "ts": ""}

    ups = sorted([s for s in sectors if s["pct"] > 0],
                 key=lambda x: x["pct"], reverse=True)[:top_n]
    downs = sorted([s for s in sectors if s["pct"] < 0],
                   key=lambda x: x["pct"])[:top_n]
    out = {"up": ups, "down": downs,
           "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime())}
    try:
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    return out


# ──────────────────────────────────────────────────────────────────
# 코스피200 야간선물 (best-effort) — Naver 실시간 지수 API 후보 다중 시도
# ⚠️ 실 엔드포인트/코드 VM 검증 필요. 실패 시 None → 카드 미표시(graceful).
# ──────────────────────────────────────────────────────────────────

# Naver 실시간 폴링 API(도메스틱 인덱스) 후보 — 야간선물 코드 미상이라
# 여러 후보를 순차 시도. 첫 plausible 값 채택. VM 로그로 어느 것이 맞는지 확인.
_NF_CANDIDATES = [
    "https://polling.finance.naver.com/api/realtime/domestic/index/KSF",
    "https://api.stock.naver.com/index/KSF/basic",
    "https://api.stock.naver.com/futures/nightFutures/basic",
]
_NF_CACHE_TTL = 3 * 60


def _plausible_kospi200(v: float) -> bool:
    # 코스피200 지수/선물 대략 200~2000 범위 sanity (오파싱 방어)
    return 100.0 <= v <= 5000.0


def fetch_night_futures() -> Optional[dict]:
    """코스피200 야간선물 현재가·등락 best-effort. {label, value, change, pct} | None.

    ⚠️ Naver 야간선물 정확 엔드포인트 미검증 — 후보 순차 시도, 실패 시 None."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / "night_futures.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _NF_CACHE_TTL:
                return json.loads(cache_file.read_text()) or None
        except Exception:
            pass

    for url in _NF_CANDIDATES:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200 or not resp.text:
                continue
            data = resp.json()
        except Exception:
            continue
        node = data[0] if isinstance(data, list) and data else data
        if not isinstance(node, dict):
            continue
        # Naver 인덱스 JSON 키 후보(closePrice/now/nv) — 다양성 방어 파싱
        raw_v = (node.get("closePrice") or node.get("now")
                 or node.get("nv") or node.get("currentPrice"))
        raw_c = (node.get("compareToPreviousClosePrice")
                 or node.get("changeValue") or node.get("cv"))
        try:
            value = float(str(raw_v).replace(",", "")) if raw_v is not None else None
        except (TypeError, ValueError):
            value = None
        if value is None or not _plausible_kospi200(value):
            continue
        try:
            change = float(str(raw_c).replace(",", "")) if raw_c is not None else None
        except (TypeError, ValueError):
            change = None
        pct = round(change / (value - change) * 100, 2) if (change and value != change) else None
        out = {"label": "코스피200 야간선물", "value": value,
               "change": change, "pct": pct, "src": url}
        try:
            cache_file.write_text(json.dumps(out, ensure_ascii=False))
        except Exception:
            pass
        log.info("naver_sector: night_futures via %s = %s", url, value)
        return out

    log.info("naver_sector: night_futures unavailable (all candidates failed)")
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mv = fetch_sector_movers()
    print(f"상승 {len(mv['up'])} / 하락 {len(mv['down'])}")
    for s in mv["up"][:5]:
        print("  ▲", s["name"], s["pct"])
    for s in mv["down"][:5]:
        print("  ▼", s["name"], s["pct"])
    print("야간선물:", fetch_night_futures())
