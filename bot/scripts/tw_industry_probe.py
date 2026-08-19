"""대만 업종이 왜 비는가 — 소스별 커버리지 실측.

사용자 2026-08-19(대만 급등·급락): 업종이 절반 넘게 `—` 다. "너무 작은
소형주라서?" 가 자연스러운 가설이지만 화면엔 `6869 그린·환경`·`8070
전자유통`처럼 **같은 대역의 소형주가 채워진 행**도 섞여 있어 그 가설만으로는
설명이 안 된다. 추측 대신 소스를 갈라 센다.

  ① TWSE(上市) · TPEx(上櫃) 두 OpenAPI 각각 몇 종목을 주는가
     — 한쪽이 조용히 실패하면 그쪽 코드대역이 통째로 빈다.
  ② 지금 무버 목록의 코드들이 그 맵에 있는가(없으면 어느 대역인가)
  ③ 없는 것들을 yfinance 개별조회로 채울 수 있는가

    cd ~/stock && .venv/bin/python -m bot.scripts.tw_industry_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.tw_industry_probe 6907 4304 5351

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys

_PROBE_VER = 1

# 사용자 스크린샷(2026-08-19)의 무버 코드 — 인자를 안 주면 이걸 본다.
_SAMPLE = ["2601", "3167", "5608", "6907", "6869", "4304", "6226", "8070",
           "4416", "5351", "3489", "3625", "3259", "7730", "6419", "3006",
           "6223", "6259", "6187"]


def _p(*a):
    print(*a, flush=True)


def main() -> int:
    codes = [c.split(".")[0] for c in sys.argv[1:]] or _SAMPLE
    from bot import twse_client as tw

    _p(f"tw_industry_probe v{_PROBE_VER} · 표본 {len(codes)}종목")
    _p("")
    _p("① 소스별 커버리지(캐시 무시하고 원천 직접)")
    per_src = {}
    for url, label in ((tw._OPENAPI_LISTED_INFO, "上市(TWSE)"),
                       (tw._OPENAPI_OTC_INFO, "上櫃(TPEx)")):
        try:
            m = tw._fetch_one_industry_source(url, label)
        except Exception as exc:                               # noqa: BLE001
            _p(f"   {label:12} 예외 {type(exc).__name__}: {exc}")
            m = {}
        per_src[label] = m
        _p(f"   {label:12} {len(m):>5}종목"
           f"{'  ❌ 비었다 — 이 대역이 통째로 빈다' if not m else ''}")

    merged = {}
    for m in per_src.values():
        merged.update(m)
    _p(f"   합계 {len(merged)}종목")

    _p("")
    _p("② 표본 코드가 맵에 있나")
    miss = []
    for c in codes:
        src = next((lb for lb, m in per_src.items() if c in m), None)
        ind = merged.get(c)
        if ind:
            _p(f"   {c:6} ✅ {ind}   ({src})")
        else:
            miss.append(c)
            _p(f"   {c:6} ❌ 없음")
    _p(f"   → 미스 {len(miss)}/{len(codes)}")

    if miss:
        _p("")
        _p("③ 미스를 yfinance 개별조회로 채울 수 있나")
        try:
            from bot.finviz_client import _fetch_industries
            got = _fetch_industries([f"{c}.TW" for c in miss], allow_slow=True)
            from bot.translate import industry_kr
            for c in miss:
                v = got.get(f"{c}.TW")
                _p(f"   {c:6} {('✅ ' + industry_kr(v)) if v else '❌ yfinance 도 없음'}")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   실패 {type(exc).__name__}: {exc}")

    _p("")
    _p("읽는 법: ①에서 한쪽이 0이면 **소스 장애**(소형주 문제가 아니다).")
    _p("        ①은 정상인데 ②가 미스면 그 종목이 上市·上櫃 어디에도 없는 것")
    _p("        (興櫃·ETF·신규상장 등). ③까지 없으면 원천에 업종 자체가 없다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
