"""건축HUB 건축인허가/착공 통계 client — 부동산 공급 선행지표.

국토교통부 건축HUB(`apis.data.go.kr/1613000` 또는 별도 base) 의 건축
인허가·착공 통계. 동일 무료 키 DATA_GO_KR_API_KEY 공유 (활용신청 별도).

선행지표 의미:
  인허가(BLD_PRMS) — 2-3년 후 입주 (가장 빠른 leading)
  착공(STRT) — 12-18개월 후 입주 (확정 공급 신호)
  R-ONE 가격지수(현재 추세) + MOLIT 실거래(현재 거래) + 인허가/착공(미래
  공급) → 부동산 Byte 의 시간축 3-band 완성.

엔드포인트/필드 경로는 discovery 필요 — odcloud 형(page/perPage/JSON)과
apis.data.go.kr 형(serviceKey/numOfRows/XML) 둘 다 후보:
    cd ~/stock && .venv/bin/python -m bot.buildperm_client      # probe

키 없으면 buildperm_key_ready() gate 가 graceful skip.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("bot.buildperm")

_TIMEOUT = 20
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_KEY_WARNED = False

# 후보 (base, endpoint, kind) — discovery 가 200+data 첫 매치 채택.
# 'kind'=auth(인허가)/strt(착공), 'shape'=odcloud(JSON page/perPage)/xml(numOfRows)
_CANDIDATES = (
    # odcloud 패턴
    ("https://api.odcloud.kr/api/HouseLicensingService/v1",
     "getHouseLicensing", "auth", "odcloud"),
    ("https://api.odcloud.kr/api/BldPrmsService/v1",
     "getBldPrms", "auth", "odcloud"),
    ("https://api.odcloud.kr/api/HouseStrtService/v1",
     "getHouseStrt", "strt", "odcloud"),
    # apis.data.go.kr 패턴 (1613000 = 국토부)
    ("https://apis.data.go.kr/1613000/HouseLicensingService/v1",
     "getHouseLicensing", "auth", "xml"),
    ("https://apis.data.go.kr/1613000/HouseStrtService/v1",
     "getHouseStrt", "strt", "xml"),
)


def buildperm_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(os.environ.get("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("buildperm: DATA_GO_KR_API_KEY 미설정 — 건축인허가/착공 skip.")
        _KEY_WARNED = True
    return ready


def _http_get(url: str, params: dict, accept_xml: bool = False):
    """공통 GET. serviceKey 인코딩 자동 처리. (status, text) 반환."""
    import httpx
    key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
    h = {"User-Agent": _UA,
         "Accept": ("application/xml, text/xml, */*" if accept_xml
                    else "application/json, */*")}
    try:
        if "%" in key:
            from urllib.parse import urlencode
            r = httpx.get(f"{url}?serviceKey={key}&{urlencode(params)}",
                          headers=h, timeout=_TIMEOUT, follow_redirects=True)
        else:
            r = httpx.get(url, params={"serviceKey": key, **params},
                          headers=h, timeout=_TIMEOUT, follow_redirects=True)
        return r.status_code, r.text
    except Exception as exc:
        log.warning("buildperm: %s 호출 실패: %s", url, exc)
        return None, ""


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    if not buildperm_key_ready():
        print("DATA_GO_KR_API_KEY 미설정 — .env 확인")
        raise SystemExit(1)

    print("=== 건축HUB 인허가/착공 엔드포인트 탐색 (status + body) ===")
    print("    (활용신청 미승인이면 HTTP 401 '인증키' / op 오류면 'SERVICE')")
    for base, ep, kind, shape in _CANDIDATES:
        url = f"{base}/{ep}"
        if shape == "odcloud":
            params = {"page": 1, "perPage": 3, "returnType": "JSON"}
        else:
            params = {"numOfRows": 3, "pageNo": 1, "type": "json"}
        status, body = _http_get(url, params, accept_xml=(shape == "xml"))
        print(f"\n--- [{kind}/{shape}] {ep}\n    {base}\n    HTTP {status} · {body[:400]}")

    print("\n→ HTTP 200 + 'data'/'response' 가 보이는 (base, endpoint) 와 첫 행")
    print("  필드명을 붙여주세요. 전부 401/SERVICE 면 활용신청이 별도 필요합니다.")
    print("  (data.go.kr 검색어: '건축HUB 인허가' / '주택 착공').")
