"""한국부동산원 R-ONE OpenAPI client — 주간/월간 주택가격동향지수.

R-ONE(www.reb.or.kr/r-one) 자체 OpenAPI (data.go.kr 과 별개 인증키
REB_RONE_API_KEY). 주간 아파트 매매가격지수·전세가격지수 = 실거래(lagging)
대비 추세 선행지표 → 부동산 Byte 방향성 강화.

R-ONE OpenAPI 구조는 통계표 ID(STATBL_ID) 기반이라, 정확한 ID 를 먼저
discovery 해야 한다. 본 모듈 직접 실행 시 통계표 목록을 출력 (discovery):
    cd ~/stock && .venv/bin/python -m bot.rone_client

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

# discovery 후 확정할 통계표 ID — 주간 아파트 매매/전세 가격지수.
# 미확정 상태(None)면 brief 가 R-ONE 블록 skip. discovery 로 채운다.
STATBL_SALE_WEEKLY = os.environ.get("RONE_STATBL_SALE", "")   # 매매가격지수
STATBL_JEONSE_WEEKLY = os.environ.get("RONE_STATBL_JEONSE", "")  # 전세가격지수


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


def _walk_rows(data) -> list[dict]:
    """R-ONE 응답에서 STATBL_ID 보유 dict 행을 깊이 탐색해 평탄화."""
    out: list[dict] = []

    def _walk(o):
        if isinstance(o, dict):
            if o.get("STATBL_ID"):
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


if __name__ == "__main__":
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

    allt = _all_tables()
    print(f"=== R-ONE 통계표 전체 {len(allt)}개 수집 (전 페이지) ===\n")

    def _dump(title, rows):
        print(f"--- {title} ({len(rows)}개) ---")
        for t in rows[:40]:
            cyc = f" [{t['cycle']}]" if t.get("cycle") else ""
            print(f"  {t['STATBL_ID']}  {t['name']}{cyc}")
        print()

    # 주간 아파트 (최우선 — 부동산 Byte 가 weekly cadence)
    _dump("⭐ '주' + '아파트' (주간 후보)", list_tables("주", "아파트"))
    _dump("'주간' 포함 전체", list_tables("주간"))
    # 월간 아파트 매매/전세 지수 (주간 부재 시 fallback)
    _dump("'아파트' + '매매지수'", list_tables("아파트", "매매지수"))
    _dump("'아파트' + '전세지수'", list_tables("아파트", "전세지수"))

    print("→ 위에서 주간(또는 월간) 아파트 '매매'·'전세' 가격지수 STATBL_ID 를")
    print("  알려주시면 RONE_STATBL_SALE / RONE_STATBL_JEONSE 로 확정합니다.")
