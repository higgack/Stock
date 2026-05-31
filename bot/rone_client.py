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

import json
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


def list_tables(keyword: str = "아파트") -> list[dict]:
    """통계표 목록 (SttsApiTbl) 에서 keyword 매칭 통계표 [{STATBL_ID, name}].
    discovery 용 — 주간 아파트 매매/전세 가격지수의 정확한 STATBL_ID 확인."""
    if not rone_key_ready():
        return []
    out: list[dict] = []
    data = _get("SttsApiTbl.do", {})
    if not data:
        return out
    # R-ONE JSON 구조는 응답으로 확인 — 흔한 키 후보를 넓게 탐색
    rows = []
    for k, v in (data.items() if isinstance(data, dict) else []):
        if isinstance(v, list):
            for el in v:
                if isinstance(el, dict) and ("row" in el or "STATBL_ID" in el):
                    rows = el.get("row", [el]) if "row" in el else [el]
    if not rows and isinstance(data, dict):
        # fallback: 깊은 탐색
        def _walk(o):
            if isinstance(o, dict):
                if "STATBL_ID" in o:
                    out.append(o)
                for vv in o.values():
                    _walk(vv)
            elif isinstance(o, list):
                for vv in o:
                    _walk(vv)
        _walk(data)
    for r in (rows or []):
        if isinstance(r, dict) and r.get("STATBL_ID"):
            out.append(r)
    res = []
    for r in out:
        name = (r.get("STATBL_NM") or r.get("TBL_NM") or r.get("name") or "")
        if keyword in name:
            res.append({"STATBL_ID": r.get("STATBL_ID"), "name": name})
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
    print("=== R-ONE 통계표 목록 (raw 응답 head) ===")
    raw = _get("SttsApiTbl.do", {})
    print(json.dumps(raw, ensure_ascii=False)[:1500] if raw else "(응답 없음/오류)")
    print("\n=== '아파트' 매칭 통계표 (STATBL_ID 후보) ===")
    for t in list_tables("아파트")[:30]:
        print(f"  {t['STATBL_ID']}  {t['name']}")
    print("\n→ '주간아파트' '매매가격지수' '전세가격지수' 포함 행의 STATBL_ID 를")
    print("  알려주시면 RONE_STATBL_SALE / RONE_STATBL_JEONSE 로 확정합니다.")
