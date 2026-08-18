#!/usr/bin/env python3
"""미국채 금리 신선도 프로브 — 읽기 전용·LLM 0·₩0.

사용자 2026-08-18: 오늘이 8/18 인데 화면 기준일이 **2026-08-13** 이다.
"금리가 매우 중요한 상황이라 가장 최신을 빠르게" — 지연이 어디서 오는지
가른다. 후보가 셋이고 처방이 전부 다르다:

  ① **FRED 원천 지연** — DGS 시리즈는 전영업일 값을 다음날 16:15 ET 에
     올린다. 원천이 8/13 까지면 우리가 할 수 있는 건 없다(다만 미 재무부
     원자료는 **당일 15:30 ET** 라 하루 빠르다 — 소스 교체 검토 대상).
  ② **디스크 캐시** — `fred_client._CACHE_TTL_HOURS = 12`. 캐시가 최신
     관측을 담기 전에 떠졌으면 최대 12시간 더 늦어진다.
  ③ **우리 선택 로직** — 받아온 관측 중 마지막을 안 쓰고 있을 수 있다.

각 단계를 따로 찍어 셋을 가른다. ⚠️ API 키는 **설정 여부만** 출력한다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.rate_freshness_probe

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import sys

_PROBE_VER = 2
_SIDS = ("DGS2", "DGS10", "DGS30")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    import datetime as _dt
    import os
    import time

    print(f"■ 금리 신선도 프로브 v{_PROBE_VER} · 오늘 "
          f"{_dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))):%Y-%m-%d %H:%M} KST")

    from bot import fred_client as fc
    from bot.env_keys import env_key, env_source
    # ⚠️ v1 은 `os.environ` 만 봐서 `.env` 에 있는 키를 '미설정'이라 오보했다
    # (실수기록 #23 을 적어놓고 바로 다음 프로브에서 반복). 공용 헬퍼가
    # `.env` 까지 보고, 출처도 함께 찍는다 — 값은 절대 출력하지 않는다.
    _src = env_source("FRED_API_KEY")
    print(f"  [키] FRED_API_KEY="
          f"{'설정' if env_key('FRED_API_KEY') else '미설정'} · 출처={_src}"
          f" · 캐시 TTL {fc._CACHE_TTL_HOURS}h")

    for sid in _SIDS:
        # ② 캐시 파일 나이 — 여기서 몇 시간이 더 붙는다.
        hit = None
        try:
            for f in fc._CACHE_DIR.glob(f"*{sid}*"):
                age = (time.time() - f.stat().st_mtime) / 3600
                hit = (f.name, age) if hit is None or age < hit[1] else hit
        except Exception:
            pass
        print(f"\n── {sid}")
        print(f"   [캐시] " + (f"{hit[0]} · {hit[1]:.1f}시간 전" if hit else "없음"))

        # ① 원천이 실제로 주는 마지막 관측들(캐시를 안 타는 전량 조회).
        try:
            rows = fc.fetch_history(sid, start="2026-01-01") or []
        except Exception as exc:
            rows = []
            print(f"   [원천] ❌ {type(exc).__name__}: {exc}")
        tail = rows[-5:]
        print(f"   [원천 fetch_history] 관측 {len(rows)}건 · 마지막 5건: "
              + (", ".join(f"{r[0]}={r[1]}" for r in tail) or "없음"))

        # ③ 화면(매크로 카드)이 실제로 고르는 값 — 캐시 경유 경로.
        try:
            picked = fc._fetch_series(sid, 400)
            print(f"   [화면 _fetch_series] {picked}")
        except Exception as exc:
            print(f"   [화면 _fetch_series] ❌ {type(exc).__name__}: {exc}")

    print("\n■ 판정 기준")
    print("  · 원천 마지막 관측이 화면 기준일과 같다 → 원인 ①(FRED 지연).")
    print("    → 미 재무부 일별 수익률곡선(당일 15:30 ET)이 하루 빠르다 — 소스 추가 검토.")
    print("  · 원천이 더 최신인데 화면이 옛것 → 원인 ②(캐시) 또는 ③(선택 로직).")
    print("    캐시 나이가 TTL 에 가까우면 ②, 방금 받았는데도 옛것이면 ③.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
