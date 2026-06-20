"""FnGuide(네이버 임베드, WISEreport) PER/PBR 밴드차트 데이터 — KR 전용.

네이버 금융 기업현황(c1010001.aspx)이 그리는 PER/PBR 밴드차트의 원천 JSON
엔드포인트(``navercomp.wisereport.co.kr/common/BandChart3.aspx``)를 직접 받아
충실 재현한다(사용자 2026-06-20 '저 차트 그대로'). 우리 자체 EPS/BPS 보간
근사 대신 FnGuide 가 계산한 밴드선·멀티플·전망(미래 구간)까지 그대로 사용.

엔드포인트(2026-06-20 VM 실측):
  GET /common/BandChart3.aspx?cmp_cd={6digit}&gubun={0|1|2|3|4}
    gubun = 재무제표 종류: 0=주재무제표(기본)·1=K-GAAP개별·2=K-GAAP연결·
            3=K-IFRS별도·4=K-IFRS연결. 잘못된 gubun 은 접속장애 HTML 반환.
  응답 = text/html 로 오지만 본문은 JSON(그쪽도 gzip 회피용 text 로 전송):
    {"bandChart1": {  # PER
        "name": {"VAL1":"36.8배","VAL2":"26.5배","VAL3":"16.3배","VAL4":"6.0배"},
        "price":[{"x":<ms epoch>,"y":<주가 또는 null(미래)>}, ...],   # ~72점
        "val1":[{"x","y"}], ... "val4":[...]   # 멀티플별 밴드선(미래 전망 포함)
     },
     "bandChart2": {...PBR (name VAL1~4 = 3.3/2.5/1.7/0.9배)...}}
  VAL1=최고배수 … VAL4=최저배수.

설계: fnguide_consensus.py 와 동일 — 현실적 UA, 10s 타임아웃, 12h 디스크 캐시
(finviz_client 유틸 재사용), 무커버리지/실패는 graceful None. 멀티플은 숫자만
파싱(인코딩 무관). 페이로드 축소 위해 [ms, y] 쌍 배열로 정규화.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests

log = logging.getLogger("bot.fnguide_band")

_URL = "https://navercomp.wisereport.co.kr/common/BandChart3.aspx"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx",
}
_TIMEOUT = 10
# 0=주재무제표(기본) 우선, 안 되면 연결→별도→GAAP 순 폴백.
_GUBUN_ORDER = ("0", "4", "3", "2", "1")


def fetch_band_chart(stock_code: str) -> Optional[dict]:
    """PER/PBR 밴드차트 데이터(.KS/.KQ → 6자리). 12h 디스크 캐시.

    Returns {"gubun", "per": {...}, "pbr": {...}} 또는 None(무커버리지/실패).
    per/pbr = {"mult": [v1..v4 float], "price": [[ms,y],...],
               "bands": [[[ms,y],...] ×4]}. 호출부는 None 우아 처리."""
    code = (stock_code or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return None

    from bot.finviz_client import _cache_write, _cached
    ck = f"fnguide_bandchart_{code}.json"
    hit = _cached(ck, ttl=12 * 3600)
    if isinstance(hit, dict) and "data" in hit:
        return hit["data"] or None

    result = None
    for gubun in _GUBUN_ORDER:
        try:
            resp = requests.get(_URL, params={"cmp_cd": code, "gubun": gubun},
                                headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            text = resp.text.strip()
        except Exception as exc:
            log.warning("fnguide band: fetch failed %s gubun=%s: %s", code, gubun, exc)
            return None   # 네트워크 오류 — 미캐시(다음 재시도)
        if not text.startswith("{"):
            continue       # 접속장애 HTML(잘못된 gubun) — 다음 gubun 폴백
        try:
            d = json.loads(text)
        except Exception:
            continue
        parsed = _parse(d, gubun)
        if parsed:
            result = parsed
            break

    _cache_write(ck, {"data": result})   # 성공·무커버리지 둘 다 캐시
    return result


def _parse(d: dict, gubun: str) -> Optional[dict]:
    """BandChart3 JSON → {gubun, per, pbr}. 순수. price 비면 None."""
    bc1, bc2 = d.get("bandChart1"), d.get("bandChart2")
    per = _parse_one(bc1)
    if not per or not per["price"]:
        return None
    return {"gubun": gubun, "per": per, "pbr": _parse_one(bc2)}


def _parse_one(bc: Optional[dict]) -> Optional[dict]:
    if not isinstance(bc, dict):
        return None
    name = bc.get("name") or {}

    def _mult(key: str):
        m = re.search(r"-?\d+(?:\.\d+)?", str(name.get(key, "")))
        return float(m.group()) if m else None

    def _series(key: str):
        return [[p.get("x"), p.get("y")] for p in (bc.get(key) or [])
                if isinstance(p, dict)]

    return {
        "mult": [_mult("VAL1"), _mult("VAL2"), _mult("VAL3"), _mult("VAL4")],
        "price": _series("price"),
        "bands": [_series("val1"), _series("val2"),
                  _series("val3"), _series("val4")],
    }
