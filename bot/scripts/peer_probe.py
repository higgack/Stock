#!/usr/bin/env python3
"""동종비교 표가 비는 원인 판별 프로브 — 읽기 전용·LLM 0·₩0.

사용자 2026-08-18(이오테크닉스 039030.KQ): 동종비교 탭의 PER·PBR 이 전 행
`—` 이고, 일부 행은 시총까지 비며 회사명 자리에 `240810.KS,0P00017YB3,330568`
같은 식별자 뭉치가 찍혔다. 원인 후보가 셋이라 화면만 보고는 못 가른다:

  ① **배포 미반영** — #885(접미사 폴백·이름 정화·자체계산)가 담긴 코드로
     실제 수집이 안 돌았다(프로세스 재시작 전 렌더 / 아카이브에 구워진 옛 표).
  ② **피어 티커 오류** — 목록의 `.KS`/`.KQ` 가 틀려 조회가 통째로 빈다.
  ③ **소스 결측** — yfinance 가 KR 종목의 PER·PBR 을 안 주고, 자체계산 재료
     (`netIncomeToCommon`·`bookValue`·`sharesOutstanding`)**까지** 없다.
     이 경우는 고칠 게 없다 — 숫자를 지어낼 수는 없으므로 `—` 가 정답이다.

이 프로브는 셋을 **분리해서** 찍는다. 아카이브에 저장된 표(=라이브
프로세스가 만든 결과)와 지금 코드로 새로 수집한 표를 나란히 보여주므로
①이면 두 표가 다르고, ②·③이면 원재료 칸에서 바로 드러난다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.peer_probe 039030.KQ
    cd ~/stock && .venv/bin/python -m bot.scripts.peer_probe 039030.KQ 089030.KQ

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import sys

_RAW = ("marketCap", "trailingPE", "priceToBook", "netIncomeToCommon",
        "bookValue", "sharesOutstanding", "financialCurrency")


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.4g}"
    return str(v)


def _row(e: dict) -> str:
    return (f"    {e.get('name','?')[:18]:<18} {e.get('ticker',''):<12} "
            f"시총={_fmt(e.get('market_cap')):>10} "
            f"PER={_fmt(e.get('trailingPE')):>8} "
            f"PBR={_fmt(e.get('priceToBook')):>8} "
            f"자체계산={','.join(e.get('derived') or []) or '-'}")


def probe(ticker: str) -> None:
    print("=" * 78)
    print(f"■ {ticker}")
    print("=" * 78)
    import yfinance as yf

    from bot.dashboard import _load_stored_stock_info
    from bot.market import resolve_peer_set
    from bot.stock_snapshot import _PEER_SCHEMA_VER, _collect_peer_multiples

    # ① 아카이브에 구워진 표 = **라이브 프로세스가 실제로 만든 것**.
    #    스키마 버전이 현재보다 낮으면 그 화면은 옛 코드의 산출물이다.
    si = _load_stored_stock_info(ticker) or {}
    stored = si.get("peer_comps") or []
    print(f"  [아카이브] 행 {len(stored)}개 · 기준 "
          f"{si.get('peer_comps_asof') or '미기록'} · 스키마 "
          f"v{si.get('peer_comps_ver') or 0} (현재 v{_PEER_SCHEMA_VER})")
    if (si.get("peer_comps_ver") or 0) < _PEER_SCHEMA_VER:
        print("    ⚠️ 옛 스키마 — 이 화면은 옛 수집 코드의 결과다(원인 ①).")
    for e in stored:
        print(_row(e))

    info = yf.Ticker(ticker).info or {}
    industry = info.get("industry") or ""
    peers = resolve_peer_set(ticker, industry) or []
    print(f"  [피어목록] industry={industry!r} → {peers}")
    if not peers:
        print("    ⚠️ 피어 목록이 비었다 — 업종 매핑 문제(표가 아예 안 뜬다).")
        return

    # ②·③ 원재료. 여기서 비는 칸이 화면에서 비는 칸의 원인이다.
    print("  [원재료] yfinance .info — 자체계산 재료가 있는지가 관건")
    for pt in [ticker] + peers[:7]:
        try:
            pi = yf.Ticker(pt).info or {}
        except Exception as exc:
            print(f"    {pt:<12} ❌ {type(exc).__name__}: {exc}")
            continue
        nm = (pi.get("shortName") or "")[:20]
        print(f"    {pt:<12} {nm:<20} " +
              " ".join(f"{k}={_fmt(pi.get(k))}" for k in _RAW))
        if not pi.get("marketCap"):
            print(f"       ⚠️ 시총조차 없음 — 티커/보드 접미사 오류 의심(원인 ②)")

    # ③ 지금 코드로 새로 수집한 표. 아카이브와 다르면 원인은 ①이다.
    fresh: dict = {}
    _collect_peer_multiples(ticker, info, fresh)
    rows = fresh.get("peer_comps") or []
    print(f"  [현재코드] 행 {len(rows)}개 · 스키마 v{fresh.get('peer_comps_ver')}")
    for e in rows:
        print(_row(e))
    _blank = [e.get("ticker") for e in rows
              if e.get("trailingPE") is None and e.get("priceToBook") is None]
    if _blank:
        print("    → PER·PBR 둘 다 못 채운 행: " + ", ".join(_blank))
        print("       원재료에 순이익·자본이 없으면 **고칠 수 없다** — "
              "억지로 숫자를 만들지 않는다(원인 ③).")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    for t in (argv[1:] or ["039030.KQ"]):
        try:
            probe(t.upper())
        except Exception as exc:
            print(f"  ❌ {t} 실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
