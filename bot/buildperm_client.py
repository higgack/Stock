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

# 활용신청 상세에서 확정된 op 이름 (건축HUB_건축인허가 17개 중 핵심 3개) —
# /getApBasisOulnInfo (기본개요·종합) · /getApHoOulnInfo (호별) ·
# /getApHsTpInfo (주택유형). service path 가 빠져 있어 후보 base 6종 brute-force.
_AUTH_OPS = ("getApBasisOulnInfo", "getApHoOulnInfo", "getApHsTpInfo")
_AUTH_BASES = (
    "https://apis.data.go.kr/1613000/ArchPmsHubService",
    "https://apis.data.go.kr/1613000/ArchPmsHubSvc",
    "https://apis.data.go.kr/1613000/ArchPmsService",
    "https://apis.data.go.kr/1613000/BldhsService",
    "https://apis.data.go.kr/1613000/ApBasisOulnInfoService",
    "https://apis.data.go.kr/1613000/getApBasisOulnInfo",  # service-as-op fallback
)
# 1613000 (국토부 건축HUB) 는 XML 응답이 기본 — sigunguCd 필수(서울 강남구=11680).
_AUTH_PARAMS = {"sigunguCd": "11680", "numOfRows": 3, "pageNo": 1, "_type": "json"}


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

    print("=== 건축HUB 인허가 엔드포인트 탐색 (sigunguCd=11680/강남) ===")
    print("    200+resultCode 00 = 확정. 'SERVICE' = base 오답, 'NODATA' = base OK·인자")
    hits = []
    for base in _AUTH_BASES:
        for op in _AUTH_OPS:
            url = f"{base}/{op}"
            status, body = _http_get(url, _AUTH_PARAMS, accept_xml=True)
            ok = (status == 200 and ("resultCode" in (body or "")
                                     or "<item>" in (body or "")
                                     or '"items"' in (body or "")))
            tag = "✅" if ok else "  "
            if ok:
                hits.append((base, op))
            print(f"{tag} [{status}] {op}\n     {base}\n     {body[:240]}\n")

    if hits:
        print(f"\n→ HIT {len(hits)}건. 첫 후보로 데이터 dump:")
        base, op = hits[0]
        s, b = _http_get(f"{base}/{op}", _AUTH_PARAMS, accept_xml=True)
        print(b[:1500])
    else:
        print("\n→ 모두 실패. base path 가 위 6 후보 밖이라는 뜻 — data.go.kr")
        print("  활용신청 상세에서 '참고문서' 또는 '미리보기' 의 example URL 한")
        print("  줄을 알려주시면 후보를 정확히 1개로 좁힙니다.")
