"""변동성 카드(VIX·VKOSPI·MOVE) 원천 점검 — "왜 나올 때가 있고 안 나올 때가 있나".

사용자 2026-08-19: "MOVE 는 나올때가 있고 안나올때가 있는데 왜 그런거야?"

카드가 사라지는 경로는 하나뿐이다 — `fetch_volatility_snapshot()` 이 그
키를 못 만들면 렌더가 패널을 통째로 건너뛴다. 그래서 **어느 단계에서
비는지**를 단계별로 찍는다:

  ① 원천 fetch 행 수(0 이면 여기서 끝)
  ② last-good 캐시 유무·나이(있으면 카드는 유지되고 '저장분'으로 표시)
  ③ 스냅샷 결과 — 화면에 실제로 나갈 값

읽기 전용 · LLM 0 · ₩0.

    cd ~/stock && .venv/bin/python -m bot.scripts.vol_probe
"""
from __future__ import annotations

import sys

_PROBE_VER = 2


def _p(*a):
    print(*a, flush=True)


def main() -> int:
    from bot import market_timing as mt
    _p(f"vol_probe v{_PROBE_VER} · 캐시 최대나이 {mt._VOL_CACHE_MAX_AGE_DAYS}일")

    _p("")
    _p("① 원천 fetch")
    for tk in ("^VIX", "^MOVE"):
        try:
            rows = mt.fetch_index_history(tk, days=400)
        except Exception as exc:                               # noqa: BLE001
            _p(f"  {tk:8} 예외 {type(exc).__name__}: {exc}")
            continue
        if rows:
            age = mt._vol_age_days(rows[-1]["date"])
            flag = ""
            if age is not None and age > mt._VOL_STALE_DAYS:
                # ⚠️ v1 은 행 수만 찍어 '성공'으로 보였다 — 실제로 ^MOVE 는
                # 400행을 주면서 최신 관측이 33일 전이었다(2026-08-19).
                # **행이 있는데 멈춰 있는** 경우가 진짜 함정이다.
                flag = f"  ❌ {age}일 지연 — 원천 시계열이 멈춤(fetch 는 성공)"
            _p(f"  {tk:8} {len(rows):>4}행 · 최신 {rows[-1]['date']} "
               f"{rows[-1]['close']:.2f}{flag}")
        else:
            # 0행이면 원인이 커버리지인지 레이트리밋인지 구분이 안 된다 —
            # yfinance 를 직접 한 번 더 때려 원문 예외를 그대로 보여준다.
            _p(f"  {tk:8}    0행 — 원문 오류 확인:")
            try:
                import yfinance as yf
                h = yf.Ticker(tk).history(period="1y")
                _p(f"           yfinance 직접호출 {len(h)}행"
                   f"{' (빈 응답 = 커버리지/차단)' if h.empty else ''}")
            except Exception as exc:                           # noqa: BLE001
                _p(f"           yfinance 직접호출 예외 {type(exc).__name__}: {exc}")

    _p("")
    _p("② last-good 캐시")
    for key in ("move",):
        path = mt._vol_cache_path(key)
        if not path.exists():
            _p(f"  {key:8} 없음 — 원천이 실패하면 카드가 사라진다")
            continue
        rec = mt._vol_cache_load(key)
        if rec is None:
            _p(f"  {key:8} 있으나 너무 낡아 사용 안 함 ({path})")
        else:
            _p(f"  {key:8} 사용 가능 · 표시 라벨 '{rec.get('source')}' · "
               f"값 {rec.get('value')}")

    _p("")
    _p("③ 스냅샷(화면에 나갈 값)")
    snap = mt.fetch_volatility_snapshot()
    for key in ("vix", "vkospi", "move"):
        rec = snap.get(key)
        if not rec:
            _p(f"  {key:8} ❌ 없음 → 카드 생략")
            continue
        hist = rec.get("history") or {}
        wins = ", ".join(f"{k}={v:.1f}" for k, v in hist.items() if v is not None)
        _p(f"  {key:8} ✅ {rec['value']:.1f} · 소스 {rec.get('source') or '—'}"
           f"{' · **캐시**' if rec.get('from_cache') else ''} · 창 [{wins}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
