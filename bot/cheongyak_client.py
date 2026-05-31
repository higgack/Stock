"""청약홈 분양정보 client — 한국부동산원 청약홈 APT 분양 모집공고.

data.go.kr 의 청약홈 분양정보 서비스는 **odcloud.kr** 게이트웨이를 쓴다
(apis.data.go.kr/1613000 실거래가와 호출 방식이 다름 — page/perPage/JSON).
같은 무료 키 DATA_GO_KR_API_KEY 를 공유한다.

분양 모집공고(공급세대·지역·일정) = 부동산 Byte 의 공급(supply) 측 신호:
신규 분양 활발 = 분양시장 온기 / 위축 = 미분양 우려. 실거래(매매)·R-ONE
지수(추세)에 이어 공급 파이프라인 축을 더한다.

엔드포인트/필드명은 odcloud 응답으로 확인이 필요해 discovery-first:
    cd ~/stock && .venv/bin/python -m bot.cheongyak_client          # 데이터 probe
키 없으면 cheongyak_key_ready() gate 가 graceful skip.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("bot.cheongyak")

# odcloud 청약홈 분양정보 조회 서비스 — APT 분양정보 상세
_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
# 엔드포인트 후보 (odcloud 문서상 op 명). 첫 200 응답을 채택.
_DETAIL_ENDPOINTS = ("getAPTLttotPblancDetail",)
_TIMEOUT = 20
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_KEY_WARNED = False
_RESOLVED: dict[str, str] = {}


def cheongyak_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(os.environ.get("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("cheongyak: DATA_GO_KR_API_KEY 미설정 — 청약홈 분양정보 skip.")
        _KEY_WARNED = True
    return ready


def _get(endpoint: str, per_page: int = 100, page: int = 1,
         extra: dict | None = None, base: str | None = None) -> dict | None:
    """odcloud GET → JSON dict. serviceKey 인코딩 자동 처리, 실패 시 None.
    base 미지정 시 분양정보 서비스(_BASE)."""
    import httpx
    key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
    params = {"page": page, "perPage": per_page, "returnType": "JSON",
              **(extra or {})}
    _h = {"User-Agent": _UA, "Accept": "application/json, */*"}
    url = f"{base or _BASE}/{endpoint}"
    try:
        if "%" in key:
            from urllib.parse import urlencode
            r = httpx.get(f"{url}?serviceKey={key}&{urlencode(params)}",
                          headers=_h, timeout=_TIMEOUT, follow_redirects=True)
        else:
            r = httpx.get(url, params={"serviceKey": key, **params},
                          headers=_h, timeout=_TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            log.warning("cheongyak: %s HTTP %d — %s", endpoint, r.status_code, r.text[:160])
            return None
        try:
            return r.json()
        except Exception:
            log.warning("cheongyak: %s non-JSON — %s", endpoint, r.text[:200])
            return None
    except Exception as exc:
        log.warning("cheongyak: %s 호출 실패: %s", endpoint, exc)
        return None


def _detail_endpoint() -> str | None:
    """200 + data 반환하는 분양정보 엔드포인트 자동 선택 (캐시)."""
    if "detail" in _RESOLVED:
        return _RESOLVED["detail"]
    for ep in _DETAIL_ENDPOINTS:
        data = _get(ep, per_page=5)
        if data and isinstance(data.get("data"), list):
            _RESOLVED["detail"] = ep
            return ep
    return None


def _g(row: dict, *keys: str) -> str:
    """odcloud 필드명이 케이스별로 달라 여러 후보 중 첫 비어있지 않은 값."""
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def recent_announcements(per_page: int = 200) -> list[dict]:
    """최근 분양 모집공고 → [{name, region, addr, total_units, notice_date,
    subscript_begin, subscript_end}]. 필드명은 응답으로 확인 후 매핑."""
    if not cheongyak_key_ready():
        return []
    ep = _detail_endpoint()
    if not ep:
        log.info("cheongyak: 분양정보 엔드포인트 미해결 (활용신청/op 명 확인)")
        return []
    data = _get(ep, per_page=per_page)
    rows = (data or {}).get("data") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "pblanc_no": _g(r, "PBLANC_NO", "HOUSE_MANAGE_NO", "pblancNo"),
            "name": _g(r, "HOUSE_NM", "houseNm", "주택명"),
            "region": _g(r, "SUBSCRPT_AREA_CODE_NM", "subscrptAreaCodeNm", "공급지역명"),
            "addr": _g(r, "HSSPLY_ADRES", "hssplyAdres", "공급위치"),
            "total_units": _g(r, "TOT_SUPLY_HSHLDCO", "totSuplyHshldco", "공급규모"),
            "notice_date": _g(r, "RCRIT_PBLANC_DE", "rcritPblancDe", "모집공고일"),
            "subscript_begin": _g(r, "RCEPT_BGNDE", "SUBSCRPT_RCEPT_BGNDE", "rceptBgnde"),
            "subscript_end": _g(r, "RCEPT_ENDDE", "SUBSCRPT_RCEPT_ENDDE", "rceptEndde"),
            "winner_date": _g(r, "PRZWNER_PRESNATN_DE", "przwnerPresnatnDe"),
            "url": _g(r, "PBLANC_URL", "HMPG_ADRES", "pblancUrl"),
            "builder": _g(r, "CNSTRCT_ENTRPS_NM", "BSNS_MBY_NM", "cnstrctEntrpsNm"),
            "kind": _g(r, "HOUSE_SECD_NM", "RENT_SECD_NM", "houseSecdNm"),
        })
    return out


# ── 청약 경쟁률 (수요 측) — odcloud ApplyhomeInfoCmpetRtSvc ────────────
# 경쟁률은 청약 접수 종료 후 집계되며 분양정보(ApplyhomeInfoDetailSvc)와
# 별도 서비스. discovery 2026-05-31 로 경로 확정 (HTTP 401=경로 OK·키
# 미등록 → 별도 활용신청 필요. 신청 후 같은 DATA_GO_KR_API_KEY 로 작동).
_COMPET_CANDIDATES = (
    ("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1", "getAPTLttotPblancCmpet"),
)
_COMPET_RESOLVED: dict[str, tuple[str, str]] = {}


def _compet_combo() -> tuple[str, str] | None:
    """200 + data 반환하는 경쟁률 (base, endpoint) 자동 선택 (캐시)."""
    if "c" in _COMPET_RESOLVED:
        return _COMPET_RESOLVED["c"]
    for base, ep in _COMPET_CANDIDATES:
        data = _get(ep, per_page=3, base=base)
        if data and isinstance(data.get("data"), list) and data["data"]:
            _COMPET_RESOLVED["c"] = (base, ep)
            return base, ep
    return None


def recent_competition(per_page: int = 200) -> list[dict]:
    """최근 청약 경쟁률 → [{name, region, model, supply, applicants, rate,
    pblanc_no}]. 엔드포인트/필드 미해결 시 []."""
    if not cheongyak_key_ready():
        return []
    combo = _compet_combo()
    if not combo:
        log.info("cheongyak: 경쟁률 엔드포인트 미해결 (활용신청/op 명 확인 필요)")
        return []
    base, ep = combo
    data = _get(ep, per_page=per_page, base=base)
    rows = (data or {}).get("data") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "pblanc_no": _g(r, "PBLANC_NO", "HOUSE_MANAGE_NO"),
            "name": _g(r, "HOUSE_NM", "houseNm", "주택명"),
            "region": _g(r, "SUBSCRPT_AREA_CODE_NM", "REGION", "공급지역명"),
            "model": _g(r, "HOUSE_TY", "MODEL_NO", "주택형"),
            "supply": _g(r, "SUPLY_HSHLDCO", "공급세대수"),
            "applicants": _g(r, "REQ_CNT", "RECEPT_CNT", "청약자수", "접수건수"),
            "rate": _g(r, "CMPET_RATE", "경쟁률", "RATE"),
        })
    return out


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
    if not cheongyak_key_ready():
        print("DATA_GO_KR_API_KEY 미설정 — .env 확인")
        raise SystemExit(1)

    if "--compet" in sys.argv:
        # 경쟁률 엔드포인트 discovery — 후보별 HTTP 상태 + 본문 직접 출력
        # (404=경로오류 vs 미등록서비스=활용신청 필요 를 구분하기 위함)
        import httpx as _hx
        _key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
        print("=== 청약 경쟁률 엔드포인트 탐색 (status + body) ===")
        for base, ep in _COMPET_CANDIDATES:
            url = f"{base}/{ep}"
            try:
                if "%" in _key:
                    from urllib.parse import urlencode
                    rr = _hx.get(f"{url}?serviceKey={_key}&"
                                 f"{urlencode({'page':1,'perPage':3,'returnType':'JSON'})}",
                                 headers={"User-Agent": _UA}, timeout=_TIMEOUT,
                                 follow_redirects=True)
                else:
                    rr = _hx.get(url, params={"serviceKey": _key, "page": 1,
                                              "perPage": 3, "returnType": "JSON"},
                                 headers={"User-Agent": _UA}, timeout=_TIMEOUT,
                                 follow_redirects=True)
                print(f"\n--- {ep}\n    {base}\n    HTTP {rr.status_code} · {rr.text[:400]}")
            except Exception as _e:
                print(f"\n--- {ep}\n    {base}\n    호출 실패: {_e}")
        print("\n=== recent_competition() 파싱 결과 (앞 8건) ===")
        comp = recent_competition(per_page=50)
        print(f"총 {len(comp)}건")
        for c in comp[:8]:
            print(f"  [{c['region']:<6}] {c['name']} {c['model']} "
                  f"· 공급 {c['supply']} · 접수 {c['applicants']} · 경쟁률 {c['rate']}")
        print("\n→ 200+data 인 (base, endpoint) 와 data[0] 필드명을 붙여주세요.")
        print("  전부 SERVICE/404 면 '청약경쟁률' API 활용신청이 별도로 필요합니다.")
        raise SystemExit(0)

    for ep in _DETAIL_ENDPOINTS:
        print(f"\n=== RAW: {ep} (perPage=3) ===")
        raw = _get(ep, per_page=3)
        print(json.dumps(raw, ensure_ascii=False)[:2200] if raw else "(없음/오류)")

    print("\n=== recent_announcements() 파싱 결과 (앞 8건) ===")
    anns = recent_announcements(per_page=50)
    print(f"총 {len(anns)}건")
    for a in anns[:8]:
        print(f"  {a['notice_date']:<12} [{a['region']:<6}] {a['name']} "
              f"· {a['total_units']}세대 · 청약 {a['subscript_begin']}~{a['subscript_end']}")
    print("\n→ RAW 의 data[0] 필드명이 위 매핑과 다르면 붙여주세요 (필드 조정).")
