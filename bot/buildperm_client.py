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
# discovery 2026-05-31 확정 base (3 op 모두 resultCode 00).
_AUTH_BASE = "https://apis.data.go.kr/1613000/ArchPmsHubService"
_AUTH_BASES = (_AUTH_BASE,)  # (legacy probe 호환)
# 건축HUB 는 sigunguCd(5) + bjdongCd(5) + 조회기간이 필요. 강남구=11680,
# 역삼동=10100. <body/> 빈 응답은 인자 부족 신호 → 파라미터 조합 탐색.
_AUTH_PARAMS = {"sigunguCd": "11680", "bjdongCd": "10100",
                "numOfRows": 5, "pageNo": 1, "_type": "json"}


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

    op = "getApBasisOulnInfo"
    print(f"=== 건축HUB {op} 파라미터 조합 탐색 (base 확정) ===")
    print(f"    {_AUTH_BASE}/{op}\n")
    # 빈 <body/> 해소 — 어떤 인자 조합에서 <item> 이 나오는지 탐색.
    # 강남구 역삼동(11680/10100) 기준 다양한 조합 + 다른 법정동.
    combos = [
        {"sigunguCd": "11680", "bjdongCd": "10100"},
        {"sigunguCd": "11680", "bjdongCd": "10100", "platGbCd": "0"},
        {"sigunguCd": "11680", "bjdongCd": "10800"},   # 대치동
        {"sigunguCd": "11650", "bjdongCd": "10800"},   # 서초구 서초동
        {"sigunguCd": "11680"},
        {"sigunguCd": "11680", "bjdongCd": "10100", "startDate": "20250101"},
    ]
    for c in combos:
        params = {**c, "numOfRows": 5, "pageNo": 1, "_type": "json"}
        status, body = _http_get(f"{_AUTH_BASE}/{op}", params, accept_xml=True)
        has_item = "<item>" in (body or "") or '"item"' in (body or "")
        cnt = ""
        import re as _re
        m = _re.search(r"<totalCount>(\d+)</totalCount>", body or "")
        if m:
            cnt = f" totalCount={m.group(1)}"
        tag = "✅" if has_item else "  "
        print(f"{tag} {c}{cnt}\n     {body[:300]}\n")
    # item 전체 필드(키) 덤프 — 연면적/세대수/허가일 정확한 키 확정
    print("\n=== item[0] 전체 필드 (강남 역삼동) ===")
    import json as _json
    status, body = _http_get(
        f"{_AUTH_BASE}/{op}",
        {"sigunguCd": "11680", "bjdongCd": "10100", "numOfRows": 5,
         "pageNo": 1, "_type": "json"}, accept_xml=False)
    try:
        items = (_json.loads(body)["response"]["body"]["items"]["item"])
        it0 = items[0] if isinstance(items, list) else items
        for k in sorted(it0.keys()):
            print(f"  {k} = {it0[k]!r}")
        # 공급 관련 후보 키만 추출
        cand = {k: it0[k] for k in it0
                if any(t in k.lower() for t in
                       ("area", "hhld", "ho", "fmly", "flr", "pms", "use",
                        "stcns", "tot", "main", "purps", "dong"))}
        print("\n  [공급 관련 후보 키]")
        for k, v in cand.items():
            print(f"    {k} = {v!r}")
    except Exception as e:
        print("JSON 파싱 실패 — body head:")
        print((body or "")[:1200])
    print("\n→ 위 키 목록에서 연면적/세대수/허가일/착공일/주용도 키를 확인했습니다.")
