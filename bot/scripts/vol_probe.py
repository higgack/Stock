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

_PROBE_VER = 4


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
    _p("①-b ^MOVE 가 왜 멈췄나 — 야후 원본 메타")
    # 우리 값은 yfinance(=Yahoo Finance) 의 `^MOVE` 미러다. 시계열이 멈춘
    # 이유는 '우리 코드' 밖이므로 **야후가 뭐라고 하는지**를 그대로 찍는다:
    # 마지막 거래시각·거래소·통화, 그리고 대체 심볼 후보의 상태.
    try:
        import yfinance as yf
        t = yf.Ticker("^MOVE")
        meta = {}
        try:
            meta = t.history_metadata or {}
        except Exception as exc:                               # noqa: BLE001
            _p(f"   history_metadata 실패: {type(exc).__name__}: {exc}")
        for k in ("symbol", "exchangeName", "fullExchangeName", "currency",
                  "instrumentType", "regularMarketTime", "firstTradeDate",
                  "regularMarketPrice"):
            if k in meta:
                _p(f"   {k:20} {meta[k]}")
        if not meta:
            _p("   (메타 없음 — 심볼이 야후에서 내려갔을 가능성)")
        # 대체 심볼 후보 — 있는지 없는지만 본다(값을 추측하지 않는다).
        for alt in ("^MOVE", "MOVE", "^TYVIX", "^VXTLT"):
            try:
                h = yf.Ticker(alt).history(period="1mo")
                last = h.index[-1].date() if len(h) else "—"
                _p(f"   대체후보 {alt:8} {len(h):>3}행 · 최신 {last}")
            except Exception as exc:                           # noqa: BLE001
                _p(f"   대체후보 {alt:8} 실패 {type(exc).__name__}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   yfinance import 실패: {type(exc).__name__}: {exc}")

    _p("")
    _p("①-c **묻는 방식**에 따라 결과가 갈리는가(2026-08-19 실측 함정)")
    # 같은 '1년'이라도 날짜범위 질의와 기간 키워드 질의가 다른 데이터를 준다.
    # #944 가 기간만 줄이고 같은 방식으로 물어 낡은 값을 그대로 받았다.
    try:
        from bot.chart_data import fetch_chart_payload as _fp
        for label, kw in (("날짜범위(start/end)", {}),
                          ("기간 키워드(period)", {"prefer_period": True})):
            rows = mt._payload_to_rows(
                _fp("^MOVE", interval="1d", period="1y", **kw), 400)
            last = rows[-1]["date"] if rows else "—"
            age = mt._vol_age_days(last) if rows else None
            _p(f"   {label:22} {len(rows):>4}행 · 최신 {last}"
               f"{f' (❌ {age}일 지연)' if age and age > mt._VOL_STALE_DAYS else ''}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   실패 {type(exc).__name__}: {exc}")

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
