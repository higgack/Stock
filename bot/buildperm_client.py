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


# 공급 파이프라인 표본 법정동 (name, sigunguCd, bjdongCd) — discovery 로
# 데이터 반환 확인된 코드. 부동산 Byte 의 시군구와 정합. 코드 추가 시 확장.
_PERMIT_REGIONS = [
    ("서울 강남(역삼)", "11680", "10100"),
    ("서울 강남(논현)", "11680", "10800"),
    ("서울 서초(서초)", "11650", "10800"),
    ("서울 송파(잠실)", "11710", "10100"),
    ("서울 마포(아현)", "11440", "10100"),
    ("경기 성남분당(서현)", "41135", "11000"),
    ("인천 서구(검단)", "28245", "10300"),
    ("부산 해운대(우동)", "26350", "10500"),
]


def buildperm_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(os.environ.get("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("buildperm: DATA_GO_KR_API_KEY 미설정 — 건축인허가/착공 skip.")
        _KEY_WARNED = True
    return ready


def _get_json(op: str, sigungu: str, bjdong: str, rows: int = 200,
              page: int = 1) -> list[dict]:
    """ArchPmsHubService op 단일 페이지 호출 → item 리스트. 실패 시 []."""
    import json as _json
    status, body = _http_get(
        f"{_AUTH_BASE}/{op}",
        {"sigunguCd": sigungu, "bjdongCd": bjdong, "numOfRows": rows,
         "pageNo": page, "_type": "json"}, accept_xml=False)
    if status != 200 or not body:
        return []
    try:
        items = _json.loads(body)["response"]["body"]["items"]["item"]
    except Exception:
        return []
    if isinstance(items, dict):
        items = [items]
    return [it for it in items if isinstance(it, dict)]


def _get_total_count(op: str, sigungu: str, bjdong: str) -> int:
    """totalCount 만 빠르게 — numOfRows=1 1 call."""
    import json as _json
    status, body = _http_get(
        f"{_AUTH_BASE}/{op}",
        {"sigunguCd": sigungu, "bjdongCd": bjdong, "numOfRows": 1,
         "pageNo": 1, "_type": "json"}, accept_xml=False)
    if status != 200 or not body:
        return 0
    try:
        return int(_json.loads(body)["response"]["body"].get("totalCount", 0))
    except Exception:
        return 0


def fetch_recent_permits(sigungu: str, bjdong: str,
                         rows: int = 1000) -> list[dict]:
    """최근 인허가 행. API 가 mgmPmsrgstPk 오름차순(=대략 허가일 오름차순) 으로
    반환 → totalCount 로 마지막 페이지를 계산해 그 페이지만 가져옴
    (대부분 법정동의 최근 N개월 인허가 < rows=1000 안에 포함)."""
    op = "getApBasisOulnInfo"
    total = _get_total_count(op, sigungu, bjdong)
    if total <= 0:
        return []
    last_page = max(1, (total + rows - 1) // rows)
    return _get_json(op, sigungu, bjdong, rows=rows, page=last_page)


def _to_float(v) -> float:
    try:
        return float(str(v or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(v) -> int:
    try:
        return int(str(v or "0").replace(",", "").strip() or "0")
    except (TypeError, ValueError):
        return 0


def permits_for_region(sigungu: str, bjdong: str, months: int = 12,
                       residential_only: bool = True) -> dict:
    """한 법정동의 최근 months개월 건축인허가 집계 (공급 파이프라인).
    archPmsDay 기준 필터. 반환:
    {n_permits, hhld_sum(세대), tot_area(연면적㎡), housing_n, since}."""
    if not buildperm_key_ready():
        return {}
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=9))).date()
    y, m = today.year, today.month - (months - 1)
    while m <= 0:
        m += 12
        y -= 1
    since = f"{y}{m:02d}01"
    items = fetch_recent_permits(sigungu, bjdong, rows=1000)
    n = hhld = housing = 0
    area = 0.0
    for it in items:
        pms = str(it.get("archPmsDay") or "").strip()
        if len(pms) < 8 or pms < since:
            continue
        purps = str(it.get("mainPurpsCdNm") or "")
        is_house = any(t in purps for t in
                       ("주택", "아파트", "주거", "연립", "다세대", "도시형"))
        if residential_only and not is_house:
            continue
        n += 1
        hhld += _to_int(it.get("hhldCnt"))
        area += _to_float(it.get("totArea"))
        if is_house:
            housing += 1
    return {"n_permits": n, "hhld_sum": hhld, "tot_area": round(area),
            "housing_n": housing, "since": since}


def permits_aggregate(regions: list[tuple[str, str, str]],
                      months: int = 12) -> dict:
    """여러 (이름, sigunguCd, bjdongCd) 의 인허가 합산 → 공급 파이프라인 요약.
    반환 {total: {...}, by_region: {name: {...}}, months, since}."""
    by_region, tot = {}, {"n_permits": 0, "hhld_sum": 0, "tot_area": 0}
    since = ""
    for name, sgg, bjd in regions:
        r = permits_for_region(sgg, bjd, months=months)
        if not r:
            continue
        since = r.get("since", since)
        if r["n_permits"] > 0:
            by_region[name] = r
        for k in tot:
            tot[k] += r.get(k, 0)
    return {"total": tot, "by_region": by_region, "months": months, "since": since}


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

    if "--raw" in sys.argv:
        # 단일 법정동 item 전체 필드 덤프 (필드 재확인용)
        import json as _json
        status, body = _http_get(
            f"{_AUTH_BASE}/getApBasisOulnInfo",
            {"sigunguCd": "11680", "bjdongCd": "10100", "numOfRows": 3,
             "pageNo": 1, "_type": "json"}, accept_xml=False)
        try:
            it0 = _json.loads(body)["response"]["body"]["items"]["item"][0]
            for k in sorted(it0):
                print(f"  {k} = {it0[k]!r}")
        except Exception:
            print((body or "")[:1200])
        raise SystemExit(0)

    # 기본: 표본 법정동 공급 파이프라인 집계 (실데이터 검증)
    print("=== 건축인허가 공급 파이프라인 (표본 법정동, 최근 12개월) ===")
    # 진단 — 각 지역 totalCount + 마지막 페이지 최신 archPmsDay
    print("[진단] 지역별 totalCount + 마지막 페이지 최신 허가일")
    for name, sgg, bjd in _PERMIT_REGIONS:
        total = _get_total_count("getApBasisOulnInfo", sgg, bjd)
        latest = ""
        if total > 0:
            recent = fetch_recent_permits(sgg, bjd, rows=1000)
            days = [str(it.get("archPmsDay") or "") for it in recent]
            days = [d for d in days if len(d) >= 8]
            if days:
                latest = max(days)
        print(f"  {name:<22} total={total:>6}  latest={latest or '—'}  ({sgg}/{bjd})")
    print()
    agg = permits_aggregate(_PERMIT_REGIONS, months=12)
    tot = agg.get("total", {})
    print(f"기간: {agg.get('since','')}~  ·  합계 주거인허가 "
          f"{tot.get('n_permits',0)}건 / {tot.get('hhld_sum',0):,}세대 / "
          f"연면적 {tot.get('tot_area',0):,}㎡\n")
    for name, r in sorted(agg.get("by_region", {}).items(),
                          key=lambda kv: kv[1].get("hhld_sum", 0), reverse=True):
        print(f"  {name:<20} {r['n_permits']:>3}건 · {r['hhld_sum']:>6,}세대 · "
              f"연면적 {r['tot_area']:>10,}㎡")
    empty = [n for n, _, _ in _PERMIT_REGIONS if n not in agg.get("by_region", {})]
    if empty:
        print(f"\n  (데이터 없음/0건: {', '.join(empty)})")
    print("\n→ 숫자가 채워지면 부동산 Byte 공급 band 통합 완료. 단일 필드 재확인: --raw")
