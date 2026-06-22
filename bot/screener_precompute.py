"""KR 추세 템플릿 사전계산 — Minervini 스크리너 on-demand 행(hang) 해소.

문제(사용자 2026-06-23): `/screen minervini` 가 시총 상위 종목의 일봉을 on-demand
로 pykrx fetch(종목당 ~1-2초) → 수백종목이면 수분~행. CLAUDE.md automation-first
원칙대로 무거운 반복작업은 스케줄로 사전수행:

  매 KR 장마감 후(systemd timer) 시총 상위 N개의 get_kr_trend_template 을 미리
  호출 → 종목별 daily 캐시(trend_tmpl_{code}_{today}.json) 데움. 그러면 같은 날
  `/screen minervini` 는 전부 캐시 히트 → 즉시·완전(데드라인 거의 미발동).

비용 ₩0(pykrx 무료), LLM 0. 실패해도 graceful — /screen 은 데드라인 폴백 보유.
systemd: trend-precompute.timer (Mon-Fri 16:30 KST) → oneshot.
수동: cd ~/stock && .venv/bin/python -m bot.screener_precompute
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("bot.screener_precompute")


def top_by_mcap(bulk: dict, n: int, min_mcap: float = 500.0) -> list[str]:
    """bulk({code:{시가총액(억),거래량,...}}) → 시총>min_mcap & 거래량>0 중 시총
    상위 n개 코드(시총 내림차순). 순수(단위테스트). /screen minervini 의 스캔 선택과
    동일 규칙(시총>500·거래량>0·시총상위캡)이라 캐시 워밍 대상이 정확히 일치."""
    elig = [(c, d) for c, d in (bulk or {}).items()
            if (d.get("시가총액") or 0) > min_mcap and (d.get("거래량") or 0) > 0]
    elig.sort(key=lambda cd: cd[1].get("시가총액", 0) or 0, reverse=True)
    return [c for c, _ in elig[:n]]


def precompute_kr_trend(top_n: int | None = None) -> dict:
    """시총 상위 top_n 개의 KR 추세 템플릿을 미리 계산(캐시 워밍). top_n 미지정 시
    stock_screener._TECH_SCAN_CAP 와 정합(+버퍼). {scanned, warmed, elapsed} 반환."""
    t0 = time.time()
    try:
        from bot.stock_screener import _fetch_kr_bulk, _TECH_SCAN_CAP
        from bot import pykrx_client as _pk
    except Exception as exc:
        log.warning("precompute: import failed: %s", exc)
        return {"scanned": 0, "warmed": 0, "elapsed": 0.0}

    cap = top_n if top_n is not None else (_TECH_SCAN_CAP + 50)  # 스캔캡 + 여유
    bulk = _fetch_kr_bulk()
    if not bulk:
        log.warning("precompute: bulk empty — KRX creds/장전 0값?")
        return {"scanned": 0, "warmed": 0, "elapsed": time.time() - t0}

    codes = top_by_mcap(bulk, cap)
    log.info("precompute: warming %d KR trend templates (bulk %d)", len(codes), len(bulk))
    warmed = 0
    for code in codes:
        try:
            if _pk.get_kr_trend_template(code):   # 성공 시 daily 캐시 적재
                warmed += 1
        except Exception as exc:
            log.debug("precompute: %s failed: %s", code, exc)
    elapsed = time.time() - t0
    log.info("precompute: warmed %d/%d in %.1fs", warmed, len(codes), elapsed)
    return {"scanned": len(codes), "warmed": warmed, "elapsed": elapsed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    r = precompute_kr_trend()
    print(f"precompute done: warmed {r['warmed']}/{r['scanned']} in {r['elapsed']:.1f}s")
