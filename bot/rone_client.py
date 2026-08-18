"""한국부동산원 R-ONE OpenAPI client — 월간 주택가격동향지수.

R-ONE(www.reb.or.kr/r-one) 자체 OpenAPI (data.go.kr 과 별개 인증키
REB_RONE_API_KEY). 아파트 매매가격지수·전세가격지수(지역별, 월간) =
실거래(개별 거래·소음 큼) 대비 매끄러운 추세지표 → 부동산 Byte 방향성 강화.

⚠️ R-ONE OpenAPI 는 **주간** 동향을 미개방 (보도자료 only). 월간 지역별
지수까지만 제공 → 본 클라이언트는 월간 지수 MoM 추세를 사용한다.

확정 통계표 (discovery 2026-05-31):
    A_2024_00178  (월) 지역별 매매지수_아파트   → RONE_STATBL_SALE
    A_2024_00182  (월) 지역별 전세지수_아파트   → RONE_STATBL_JEONSE

데이터 스키마 probe / 통계표 재탐색:
    cd ~/stock && .venv/bin/python -m bot.rone_client          # 데이터 probe
    cd ~/stock && .venv/bin/python -m bot.rone_client --tables # 통계표 재탐색

발급 키 없으면 rone_key_ready() gate 가 graceful skip.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("bot.rone")

_BASE = "https://www.reb.or.kr/r-one/openapi"
_TIMEOUT = 20
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_KEY_WARNED = False

# 확정 통계표 ID (월간 지역별 아파트 지수) — env override 가능.
STATBL_SALE = os.environ.get("RONE_STATBL_SALE", "A_2024_00178")    # (월) 지역별 매매지수_아파트
STATBL_JEONSE = os.environ.get("RONE_STATBL_JEONSE", "A_2024_00182")  # (월) 지역별 전세지수_아파트

# 공급 통계표 (월간, discovery 2026-05-31) — 시간축 미래 공급 band.
STATBL_PERMIT = "T235263129553687"   # 주택건설인허가실적 (선행 2-3년)
STATBL_START = "T233033129823134"    # 주택착공실적 (선행 12-18개월)
STATBL_UNSOLD = "T237973129847263"   # 미분양주택현황 (실현된 공급과잉)
STATBL_NEWSALE = "T244633134443498"  # 지역별 신규 분양세대수


def rone_key_ready() -> bool:
    global _KEY_WARNED
    from bot.env_keys import env_ready
    ready = env_ready("REB_RONE_API_KEY")
    if not ready and not _KEY_WARNED:
        log.warning(
            "rone: REB_RONE_API_KEY 미설정 — reb.or.kr R-ONE OpenAPI 키 발급 후 "
            ".env 에 추가 필요. R-ONE 블록 skip.")
        _KEY_WARNED = True
    return ready


def _get(endpoint: str, params: dict) -> dict | None:
    """R-ONE OpenAPI 호출 → JSON dict. 실패 시 None + 진단 로그."""
    import httpx
    from bot.env_keys import env_key
    key = env_key("REB_RONE_API_KEY")
    q = {"KEY": key, "Type": "json", "pIndex": 1, "pSize": 100, **params}
    try:
        r = httpx.get(f"{_BASE}/{endpoint}", params=q,
                      headers={"User-Agent": _UA}, timeout=_TIMEOUT,
                      follow_redirects=True)
        if r.status_code != 200:
            log.warning("rone: %s HTTP %d — %s", endpoint, r.status_code, r.text[:160])
            return None
        try:
            return r.json()
        except Exception:
            log.warning("rone: %s non-JSON 응답 — %s", endpoint, r.text[:200])
            return None
    except Exception as exc:
        log.warning("rone: %s 호출 실패: %s", endpoint, exc)
        return None


def _walk_rows(data, key: str = "STATBL_ID") -> list[dict]:
    """R-ONE 응답에서 `key` 보유 dict 행을 깊이 탐색해 평탄화.
    통계표 목록은 key='STATBL_ID', 데이터 조회는 key='DTA_VAL'."""
    out: list[dict] = []

    def _walk(o):
        if isinstance(o, dict):
            if o.get(key) is not None:
                out.append(o)
            for vv in o.values():
                _walk(vv)
        elif isinstance(o, list):
            for vv in o:
                _walk(vv)

    _walk(data)
    return out


def _total_count(data) -> int | None:
    try:
        head = data.get("SttsApiTbl", [{}])[0].get("head", [])
        for h in head:
            if isinstance(h, dict) and "list_total_count" in h:
                return int(h["list_total_count"])
    except Exception:
        pass
    return None


def _all_tables(page_size: int = 100, max_pages: int = 12) -> list[dict]:
    """SttsApiTbl 전체 페이지네이션 — list_total_count 까지 모든 통계표 수집."""
    rows: list[dict] = []
    total = None
    for page in range(1, max_pages + 1):
        data = _get("SttsApiTbl.do", {"pIndex": page, "pSize": page_size})
        if not data:
            break
        if total is None:
            total = _total_count(data)
        page_rows = _walk_rows(data)
        if not page_rows:
            break
        rows.extend(page_rows)
        if total is not None and len(rows) >= total:
            break
        if len(page_rows) < page_size:
            break
    return rows


def list_tables(*keywords: str) -> list[dict]:
    """통계표 목록 (SttsApiTbl, 전 페이지) 에서 모든 keyword 를 동시에 포함하는
    통계표 [{STATBL_ID, name, cycle}]. discovery 용.
    예: list_tables('아파트') / list_tables('주', '아파트')."""
    if not rone_key_ready():
        return []
    kws = keywords or ("아파트",)
    res, seen = [], set()
    for r in _all_tables():
        name = (r.get("STATBL_NM") or r.get("TBL_NM") or r.get("name") or "")
        sid = r.get("STATBL_ID")
        if not sid or sid in seen:
            continue
        if all(k in name for k in kws):
            seen.add(sid)
            res.append({"STATBL_ID": sid, "name": name,
                        "cycle": r.get("DTACYCLE_NM") or ""})
    return res


def _period_range(n_months: int) -> tuple[str, str]:
    """최근 n개월 YYYYMM 범위 (KST 기준). (start, end)."""
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=9))).date()
    end = f"{today.year}{today.month:02d}"
    y, m = today.year, today.month - (n_months - 1)
    while m <= 0:
        m += 12
        y -= 1
    return f"{y}{m:02d}", end


def fetch_index(statbl_id: str, n_months: int = 8, cycle: str = "MM") -> list[dict]:
    """StatisticSearch.do 로 통계표 시계열 조회 → 평탄화 행 리스트.
    각 행: {period, cls_id, cls_nm, itm_nm, value(float|None)}.
    필드명이 R-ONE 응답에 따라 다를 수 있어 넓게 매핑."""
    if not rone_key_ready() or not statbl_id:
        return []
    start, end = _period_range(n_months)
    out = []
    # 페이지네이션 — 큰 표(인허가/착공/미분양: 지역×유형×개월 >1000행)는
    # 단일 페이지면 잘림. pSize 미만 반환될 때까지 누적 (cap max_pages).
    page_size, max_pages = 1000, 8
    for page in range(1, max_pages + 1):
        data = _get("SttsApiTblData.do", {
            "STATBL_ID": statbl_id, "DTACYCLE_CD": cycle,
            "START_WRTTIME": start, "END_WRTTIME": end,
            "pSize": page_size, "pIndex": page,
        })
        if not data:
            break
        rows = _walk_rows(data, key="DTA_VAL")
        if not rows:
            break
        for r in rows:
            raw = r.get("DTA_VAL")
            try:
                val = float(str(raw).replace(",", "")) if raw not in (None, "") else None
            except (TypeError, ValueError):
                val = None
            out.append({
                "period": str(r.get("WRTTIME_IDTFR_ID") or r.get("WRTTIME_IDTFR") or ""),
                "grp_nm": (r.get("GRP_NM") or "").strip(),
                "grp_fullnm": (r.get("GRP_FULLNM") or "").strip(),
                "cls_id": r.get("CLS_ID") or "",
                "cls_nm": (r.get("CLS_NM") or "").strip(),
                "cls_fullnm": (r.get("CLS_FULLNM") or "").strip(),
                "itm_nm": r.get("ITM_NM") or "",
                "value": val,
            })
        if len(rows) < page_size:
            break
    return out


def _series_for(rows: list[dict], region: str) -> list[tuple[str, float]]:
    """지역(cls_nm 정확일치) 의 (period, value) 시계열 (period 오름차순)."""
    series = {}
    for r in rows:
        if r["value"] is None or not r["period"]:
            continue
        if r["cls_nm"] == region:
            series[r["period"]] = r["value"]
    return sorted(series.items())


def _trend(series: list[tuple[str, float]], region: str) -> dict | None:
    """(period, value) 시계열 → 최신·MoM·3M 변화율(%). <2pt 면 None."""
    if len(series) < 2:
        return None
    periods = [p for p, _ in series]
    vals = [v for _, v in series]
    latest, prev = vals[-1], vals[-2]
    mom = (latest / prev - 1.0) * 100.0 if prev else None
    m3 = (latest / vals[-4] - 1.0) * 100.0 if len(vals) >= 4 and vals[-4] else None
    return {
        "region": region, "latest_period": periods[-1], "latest": round(latest, 2),
        "mom_pct": round(mom, 2) if mom is not None else None,
        "m3_pct": round(m3, 2) if m3 is not None else None,
    }


def index_trend(statbl_id: str, region: str = "전국") -> dict | None:
    """단일 지역 추세 (probe/단건용). region 미존재 시 None."""
    return _trend(_series_for(fetch_index(statbl_id), region), region)


def index_trends(statbl_id: str, regions: tuple[str, ...]) -> dict:
    """표 1회 fetch 로 여러 지역 추세 한꺼번에. {region: trenddict} (≥2pt 만)."""
    rows = fetch_index(statbl_id)
    out = {}
    for reg in regions:
        t = _trend(_series_for(rows, reg), reg)
        if t:
            out[reg] = t
    return out


_DEFAULT_REGIONS = ("전국", "수도권", "지방", "서울")


def realestate_trend(regions: tuple[str, ...] = _DEFAULT_REGIONS) -> dict:
    """부동산 Byte 용 — 매매·전세 지수 추세 (지역별). 빈 항목은 빠짐.
    {'sale': {region: {...}}, 'jeonse': {region: {...}}}."""
    out = {}
    sale = index_trends(STATBL_SALE, regions)
    if sale:
        out["sale"] = sale
    jeonse = index_trends(STATBL_JEONSE, regions)
    if jeonse:
        out["jeonse"] = jeonse
    return out


# ── 공급 통계 (인허가/착공/미분양) — 지역은 cls_fullnm 첫 세그먼트 ──────
# 인허가/착공: cls_fullnm "전국>합계(가구수기준)" 등. 미분양: "서울>계" 등.
# (name, statbl_id, leaf 후보, allow_sum) — allow_sum=True 면 전국 행 부재 시
# 시도 합산으로 전국 산출 (미분양: '전국>계' 없고 '서울>계' 등 시도만 존재).
_SUPPLY_SPECS = (
    ("인허가", STATBL_PERMIT, ("합계(가구수기준)", "합계(동수기준)"), False),
    ("착공", STATBL_START, ("총계",), False),
    ("미분양", STATBL_UNSOLD, ("계",), True),
)


def _region_of(r: dict) -> str:
    """cls_fullnm 의 첫 세그먼트 = 지역 (예 '전국>합계' → '전국')."""
    full = r.get("cls_fullnm") or ""
    return full.split(">")[0].strip() if full else ""


def _supply_series(statbl_id: str, leaf_candidates: tuple[str, ...],
                   region: str = "전국", allow_sum: bool = False) -> tuple[str, list]:
    """전국(또는 region) + leaf(합계 종류) 의 (period,value) 시계열.
    leaf 후보를 순서대로 시도, 첫 ≥2pt 매치 반환. allow_sum=True 면 전국 행
    부재 시 시도(전국 외) leaf 행 합산으로 전국 산출. (leaf, series)."""
    rows = fetch_index(statbl_id, n_months=18)
    for leaf in leaf_candidates:
        series = {}
        for r in rows:
            if r["value"] is None or not r["period"]:
                continue
            if _region_of(r) == region and r["cls_nm"] == leaf:
                series[r["period"]] = r["value"]
        if len(series) >= 2:
            return leaf, sorted(series.items())
    if allow_sum:
        for leaf in leaf_candidates:
            summed = {}
            for r in rows:
                if r["value"] is None or not r["period"]:
                    continue
                if r["cls_nm"] == leaf and _region_of(r) != "전국":
                    summed[r["period"]] = summed.get(r["period"], 0) + r["value"]
            if len(summed) >= 2:
                return leaf, sorted(summed.items())
    return "", []


def supply_trend_one(statbl_id: str, leaf_candidates: tuple[str, ...],
                     allow_sum: bool = False) -> dict | None:
    """전국 공급 시계열 → 최신·MoM·YoY (호). <2pt 면 None."""
    leaf, series = _supply_series(statbl_id, leaf_candidates, allow_sum=allow_sum)
    if len(series) < 2:
        return None
    periods = [p for p, _ in series]
    vals = [v for _, v in series]
    latest, prev = vals[-1], vals[-2]
    mom = (latest / prev - 1.0) * 100.0 if prev else None
    yoy = None
    if len(vals) >= 13 and vals[-13]:
        yoy = (latest / vals[-13] - 1.0) * 100.0
    return {
        "leaf": leaf, "latest_period": periods[-1], "latest": round(latest),
        "mom_pct": round(mom, 1) if mom is not None else None,
        "yoy_pct": round(yoy, 1) if yoy is not None else None,
    }


def supply_summary() -> dict:
    """부동산 Byte 공급 band — 인허가·착공·미분양 전국 추세. 빈 항목 빠짐.
    {'인허가': {...}, '착공': {...}, '미분양': {...}}."""
    out = {}
    for name, sid, leafs, allow_sum in _SUPPLY_SPECS:
        t = supply_trend_one(sid, leafs, allow_sum=allow_sum)
        if t:
            out[name] = t
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    if not rone_key_ready():
        print("REB_RONE_API_KEY 미설정 — .env 확인")
        raise SystemExit(1)

    if "--tables" in sys.argv:
        # 통계표 재탐색 모드
        def _dump(title, rows):
            print(f"--- {title} ({len(rows)}개) ---")
            for t in rows[:40]:
                cyc = f" [{t['cycle']}]" if t.get("cycle") else ""
                print(f"  {t['STATBL_ID']}  {t['name']}{cyc}")
            print()
        allt = _all_tables()
        print(f"=== R-ONE 통계표 전체 {len(allt)}개 ===\n")
        _dump("'아파트' + '매매지수'", list_tables("아파트", "매매지수"))
        _dump("'아파트' + '전세지수'", list_tables("아파트", "전세지수"))
        # 공급 파이프라인 후보 — 미분양/인허가/착공/준공/공급
        for kw in ("미분양", "인허가", "착공", "준공", "공급", "분양"):
            _dump(f"'{kw}'", list_tables(kw))
        raise SystemExit(0)

    if "--supplyraw" in sys.argv:
        # 공급 표 행의 전체 raw 키 — 지역 차원이 어느 필드인지 확정
        import json as _jr
        for label, sid in (("주택인허가실적", STATBL_PERMIT),
                           ("미분양주택현황", STATBL_UNSOLD)):
            print(f"\n=== {label}  {sid} — raw 행 앞 4개 ===")
            start, end = _period_range(3)
            data = _get("SttsApiTblData.do", {
                "STATBL_ID": sid, "DTACYCLE_CD": "MM",
                "START_WRTTIME": start, "END_WRTTIME": end, "pSize": 8})
            for row in _walk_rows(data, key="DTA_VAL")[:4]:
                print("  " + _jr.dumps(row, ensure_ascii=False))
        raise SystemExit(0)

    if "--supply" in sys.argv:
        # 공급 통계표 스키마 — 지역(cls)·항목(itm) 차원 + 최근 값 확인
        for label, sid in (("주택인허가실적", STATBL_PERMIT),
                           ("주택착공실적", STATBL_START),
                           ("미분양주택현황", STATBL_UNSOLD),
                           ("신규분양세대수", STATBL_NEWSALE)):
            print(f"\n=== {label}  {sid} ===")
            rows = fetch_index(sid, n_months=6)
            print(f"수신 {len(rows)}행. 앞 10행 (period·grp·cls·itm·value):")
            for r in rows[:10]:
                print(f"  {r['period']:<8} grp={r['grp_nm']!r:<10} cls={r['cls_nm']!r:<14} {r['value']}")
            grp = sorted({r["grp_nm"] for r in rows if r["grp_nm"]})
            cls = sorted({r["cls_nm"] for r in rows if r["cls_nm"]})
            print(f"  그룹(grp,지역?) {len(grp)}종: {grp[:25]}")
            print(f"  분류(cls) {len(cls)}종: {cls[:25]}")
        print("\n→ 각 표의 전국 행 + 적절한 itm(부문/유형) 확인되면 공급 band 통합.")
        raise SystemExit(0)

    # 데이터 probe — 확정 통계표의 실제 응답 + 지역 라벨 + 추세
    for label, sid in (("매매지수", STATBL_SALE), ("전세지수", STATBL_JEONSE)):
        print(f"\n=== {label}  STATBL_ID={sid} — SttsApiTblData.do 샘플 ===")
        rows = fetch_index(sid)
        print(f"수신 행 {len(rows)}개. 앞 8행:")
        for r in rows[:8]:
            print(f"  {r['period']:<8} {r['cls_nm']!r:<10} {r['value']}")
        labels = sorted({r["cls_nm"] for r in rows if r["cls_nm"]})
        print(f"  지역 라벨 {len(labels)}종: {labels[:30]}")

    print("\n=== realestate_trend() — 부동산 Byte 통합 출력 ===")
    import json as _json
    print(_json.dumps(realestate_trend(), ensure_ascii=False, indent=2))
