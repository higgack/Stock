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


def rone_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(os.environ.get("REB_RONE_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning(
            "rone: REB_RONE_API_KEY 미설정 — reb.or.kr R-ONE OpenAPI 키 발급 후 "
            ".env 에 추가 필요. R-ONE 블록 skip.")
        _KEY_WARNED = True
    return ready


def _get(endpoint: str, params: dict) -> dict | None:
    """R-ONE OpenAPI 호출 → JSON dict. 실패 시 None + 진단 로그."""
    import httpx
    key = (os.environ.get("REB_RONE_API_KEY") or "").strip()
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
    data = _get("StatisticSearch.do", {
        "STATBL_ID": statbl_id, "DTACYCLE_CD": cycle,
        "START_WRTTIME": start, "END_WRTTIME": end, "pSize": 1000,
    })
    if not data:
        return []
    out = []
    for r in _walk_rows(data, key="DTA_VAL"):
        raw = r.get("DTA_VAL")
        try:
            val = float(str(raw).replace(",", "")) if raw not in (None, "") else None
        except (TypeError, ValueError):
            val = None
        out.append({
            "period": str(r.get("WRTTIME_IDTFR") or r.get("WRTTIME_DESC") or ""),
            "cls_id": r.get("CLS_ID") or r.get("CLS_FULLNM") or "",
            "cls_nm": r.get("CLS_NM") or r.get("CLS_FULLNM") or "",
            "itm_nm": r.get("ITM_NM") or "",
            "value": val,
        })
    return out


def _pick_region(rows: list[dict], region: str) -> list[tuple[str, float]]:
    """특정 지역(cls_nm 부분일치) 의 (period, value) 시계열 (period 오름차순)."""
    series = {}
    for r in rows:
        if r["value"] is None or not r["period"]:
            continue
        if region in (r.get("cls_nm") or ""):
            series[r["period"]] = r["value"]
    return sorted(series.items())


def index_trend(statbl_id: str, region: str = "전국") -> dict | None:
    """지역 지수 시계열에서 최신·전월·3개월전 + MoM/3M 변화율(%).
    region 미존재 시 None (호출측이 graceful skip)."""
    rows = fetch_index(statbl_id)
    series = _pick_region(rows, region)
    if len(series) < 2:
        return None
    periods = [p for p, _ in series]
    vals = [v for _, v in series]
    latest, prev = vals[-1], vals[-2]
    mom = (latest / prev - 1.0) * 100.0 if prev else None
    m3 = None
    if len(vals) >= 4 and vals[-4]:
        m3 = (latest / vals[-4] - 1.0) * 100.0
    return {
        "region": region, "latest_period": periods[-1],
        "latest": round(latest, 2), "mom_pct": round(mom, 2) if mom is not None else None,
        "m3_pct": round(m3, 2) if m3 is not None else None,
    }


def realestate_trend(region: str = "전국") -> dict:
    """부동산 Byte 용 — 매매·전세 지수 추세 한 번에. 실패 항목은 빠짐."""
    out = {}
    sale = index_trend(STATBL_SALE, region)
    if sale:
        out["sale"] = sale
    jeonse = index_trend(STATBL_JEONSE, region)
    if jeonse:
        out["jeonse"] = jeonse
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
        raise SystemExit(0)

    # StatisticSearch.do 원시 응답 — 필드명/에러 메시지 확인
    import json as _json
    print("=== RAW: StatisticSearch.do (기간 파라미터 포함) ===")
    raw1 = _get("StatisticSearch.do", {
        "STATBL_ID": STATBL_SALE, "DTACYCLE_CD": "MM",
        "START_WRTTIME": "202501", "END_WRTTIME": "202605", "pSize": 50})
    print(_json.dumps(raw1, ensure_ascii=False)[:1800] if raw1 else "(없음)")
    print("\n=== RAW: StatisticSearch.do (기간 파라미터 없이) ===")
    raw2 = _get("StatisticSearch.do", {"STATBL_ID": STATBL_SALE, "pSize": 50})
    print(_json.dumps(raw2, ensure_ascii=False)[:1800] if raw2 else "(없음)")
    print()

    # 데이터 probe — 확정 통계표의 실제 응답 스키마 + 추세 확인
    for label, sid in (("매매지수", STATBL_SALE), ("전세지수", STATBL_JEONSE)):
        print(f"\n=== {label}  STATBL_ID={sid} — StatisticSearch.do 샘플 ===")
        rows = fetch_index(sid)
        print(f"수신 행 {len(rows)}개. 앞 12행:")
        for r in rows[:12]:
            print(f"  period={r['period']:<8} cls_nm={r['cls_nm']!r:<14} "
                  f"itm={r['itm_nm']!r:<14} value={r['value']}")
        # 등장하는 지역(cls_nm) 라벨 — region 매핑 확인용
        labels = sorted({r["cls_nm"] for r in rows if r["cls_nm"]})
        print(f"  지역 라벨 {len(labels)}종: {labels[:25]}")
        for reg in ("전국", "서울"):
            t = index_trend(sid, reg)
            print(f"  [{reg}] trend → {t}")

    print("\n→ 위 'value' 가 채워지고 [전국]/[서울] trend 가 나오면 통합 준비 완료.")
    print("  value 가 전부 None 이거나 행 0 이면 응답 앞부분을 붙여주세요 (필드명 조정).")
