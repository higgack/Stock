"""밴드 탭이 쓰는 **단일 해석기** — 화면과 감사가 같은 경로를 탄다(#35).

⚠️ 2026-08-23 실측으로 드러난 것: 감사(`per_band_audit`)는 `per_band.for_ticker`
만 불러서 국내 종목을 `yf-a · 관측 4개` 로 판정했는데, 화면은 FnGuide 밴드를
그린다(같은 종목이 **관측 49개**). 즉 감사가 **화면이 쓰지 않는 경로**를 재고
있었고, 그 상태로 "국내는 밴드가 없다/짧다"는 통계를 냈다. 국내 경로는
`dashboard_server._handle_band_api` 안에 인라인으로만 있었던 것이 원인이다.

두 호출부가 이 함수 하나를 부르게 해서 갈라질 수 없게 만든다. 새 시장을
붙일 때도 여기 한 곳만 고치면 화면·감사가 같이 따라온다(#38).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# FnGuide 는 `cmp_cd` 가 6자리 국내코드라 KR 만 커버한다 — 그 밖의 시장은
# 우리가 가격 이력 × EPS 이력으로 만든다(`per_band.for_ticker`).
_KR_SUFFIXES = (".KS", ".KQ")


def is_kr(ticker: str) -> bool:
    return (ticker or "").strip().upper().endswith(_KR_SUFFIXES)


def resolve(ticker: str, snap: dict | None = None) -> dict:
    """티커 → 밴드 탭 재료.

    반환 `{"per", "pbr", "raw", "why", "basis"}`:
      · `per`/`pbr` — 표 payload(차트는 `per["chart"]` 또는 `raw` 안에 있다)
      · `raw`  — KR 만. FnGuide 원본 밴드(차트가 이걸 그린다)
      · `why`  — 못 만든 사유. **하나로 단정하지 않는다**(#50·#129)
      · `basis` — 어느 원천으로 만들었는지(edgar/edinet/finmind/baidu/
        yf-q/yf-a/fnguide). 감사·화면이 같은 이름을 쓴다.
    """
    tk = (ticker or "").strip().upper()
    if is_kr(tk):
        from bot.dashboard_server import _kr_band_tables
        from bot.fnguide_bandchart import fetch_band_chart
        data = fetch_band_chart(tk)
        if not data:
            return {"per": None, "pbr": None, "raw": None, "basis": None,
                    "why": "FnGuide 밴드 데이터를 받지 못했습니다."}
        per, pbr = _kr_band_tables(data, tk)
        return {"per": per, "pbr": pbr, "raw": data, "basis": "fnguide",
                "why": None if (per or pbr) else
                "FnGuide 가 이 종목의 배수를 주지 않았습니다(적자 등)."}
    from bot.per_band import for_ticker
    tbl, why = for_ticker(tk, snap)
    return {"per": tbl, "pbr": None, "raw": None,
            "basis": (tbl or {}).get("basis"), "why": why}
