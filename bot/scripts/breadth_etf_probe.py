"""섹터 breadth ETF 티커 실측 검증 — "이 티커가 정말 그 섹터 ETF 인가".

2026-08-16 KR breadth(KODEX 섹터 시리즈) 추가하며 만들었다. 티커 자체는
`TradingAgents/.../sector_strength_tools.py` 의 `_KR_INDUSTRY_OVERRIDES`
("Source: KRX ETF listings as of 2026")와 대조해 13개 전부 일치를 확인했고
회귀 테스트가 그 일치를 고정한다. 다만 그 표도 **작성 시점의 스냅샷**이라
상장폐지·티커변경·거래정지는 잡지 못한다 — 이 스크립트가 라이브로 가른다
(샌드박스에선 Yahoo 가 정책상 403 이라 실측 자체가 불가능했다).

검증 항목:
  1. 해석 여부 — 히스토리가 하나라도 오나
  2. 히스토리 행수 — 200일 SMA 를 계산하려면 ≥200 거래일 필요
  3. **실제 종목명**에 기대 섹터 라벨이 들어있나(KR 은 네이버 한글명).
     티커가 다른 상품으로 바뀌었으면 여기서 잡힌다
  4. 중앙 거래량 — 사실상 거래정지된 ETF 는 가격이 안 움직여 SMA 를 왜곡
마지막에 **전체 목록**을 붙여넣기용으로 출력한다(실패분은 사유와 함께 주석).

⚠️ 후보 목록을 여기 복제하지 않는다 — `market_timing._BREADTH_SECTORS` 를
그대로 읽는다(두 곳으로 갈라지면 그게 곧 드리프트 표면).

조회 전용 — yfinance GET 만 한다. 파일·DB 를 쓰지 않는다.

Usage on VM:
    cd ~/stock
    .venv/bin/python -m bot.scripts.breadth_etf_probe          # 기본 KR
    .venv/bin/python -m bot.scripts.breadth_etf_probe --market US
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bot.market_timing import _BREADTH_SECTORS, fetch_index_history

_MIN_ROWS = 200        # 200일 SMA 계산 최소치
_MIN_VOL = 1_000       # 중앙 거래량 하한(이하면 사실상 거래정지 의심)


def _name(ticker: str) -> str:
    """종목명. KR 은 **네이버 한글명**(bot/naver_quote), 그 외는 yfinance.

    ⚠️ yfinance `shortName` 은 KR ETF 도 영문/로마자라 한글 라벨('은행')과
    대조가 불가능하다 — 옛 구현은 정확한 티커도 전부 '이름 불일치'로 떨궜다
    (2026-08-16 독립 리뷰). 네이버는 이미 레포가 쓰는 한글명 소스다."""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        try:
            from bot.naver_quote import fetch_kr_quote
            q = fetch_kr_quote(ticker.split(".")[0]) or {}
            if q.get("name"):
                return str(q["name"]).strip()
        except Exception as exc:
            print(f"    (네이버 이름 조회 실패: {exc})")
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return (info.get("shortName") or info.get("longName") or "").strip()
    except Exception as exc:
        print(f"    (yfinance 이름 조회 실패: {exc})")
        return ""


def _check(ticker: str, label: str) -> tuple[bool, str, dict]:
    """(통과여부, 사유, 상세). 이름 대조가 핵심 판정."""
    hist = fetch_index_history(ticker, days=280)
    if not hist:
        return False, "❌ 해석 실패(티커 없음/데이터 없음)", {}
    rows = len(hist)
    vols = [h.get("volume") or 0 for h in hist if h.get("volume") is not None]
    med_vol = int(statistics.median(vols)) if vols else 0
    name = _name(ticker)
    detail = {"rows": rows, "last": hist[-1]["date"], "name": name,
              "med_vol": med_vol}
    if rows < _MIN_ROWS:
        return False, f"❌ 히스토리 부족({rows}일 < {_MIN_ROWS})", detail
    if not name:
        return False, "⚠️ 이름 조회 실패(수동 확인 필요)", detail
    # 이름 대조는 **한글 라벨이 가능한 시장에서만**. US 는 라벨이 한글이고
    # 종목명은 영문이라 대조가 성립하지 않는다 — 해석·히스토리·유동성만 본다.
    if ticker.endswith((".KS", ".KQ")):
        # 라벨 통째로 또는 앞 2글자(운용사 표기가 '미디어&엔터테인먼트' 처럼
        # 길거나 '기계장비'→'기계' 처럼 줄어드는 경우) 매칭.
        if label not in name and label[:2] not in name:
            return False, f"❌ 이름 불일치 — 기대 '{label}' vs 실제 '{name}'", detail
    if med_vol < _MIN_VOL:
        return False, f"⚠️ 저유동성(중앙 거래량 {med_vol:,}) — SMA 왜곡 우려", detail
    return True, "✅", detail


def main() -> int:
    ap = argparse.ArgumentParser(
        description="섹터 breadth ETF 티커 실측 검증(조회 전용)")
    ap.add_argument("--market", default="KR",
                    help=f"검증할 시장 ({'/'.join(_BREADTH_SECTORS)}) — 기본 KR")
    args = ap.parse_args()
    mkt = args.market.upper()
    sectors = _BREADTH_SECTORS.get(mkt)
    if not sectors:
        print(f"미등록 시장: {mkt} (가능: {', '.join(_BREADTH_SECTORS)})")
        return 2

    print(f"── {mkt} 섹터 breadth ETF 검증 ({len(sectors)}개) ──\n")
    ok: dict[str, str] = {}
    bad: list[tuple[str, str, str]] = []
    for ticker, label in sectors.items():
        passed, reason, d = _check(ticker, label)
        name = d.get("name", "")
        print(f"{reason:<52s} {ticker:12s} 기대={label}")
        if d:
            print(f"     실제명={name or '(없음)'} · {d.get('rows', 0)}일 "
                  f"(최근 {d.get('last', '?')}) · 중앙거래량 "
                  f"{d.get('med_vol', 0):,}")
        if passed:
            ok[ticker] = label
        else:
            bad.append((ticker, label, reason))
        print()

    print("=" * 72)
    print(f"통과 {len(ok)} / {len(sectors)}")
    if bad:
        print("\n실패 — 교체 필요:")
        for t, lb, r in bad:
            print(f"  {t} ({lb}) — {r}")
        print("\n이 줄들을 Claude 에게 그대로 알려주면 티커를 교체합니다.")
    # ⚠️ 통과분만 찍으면 오탐(false negative) 한 줄이 사람 손을 거쳐 섹터를
    # **삭제**시킨다. 전체를 찍되 실패는 주석 처리해 판단을 사람에게 남긴다.
    print("\n── 레지스트리(붙여넣기용 — 실패분은 주석) ──")
    print(f'    "{mkt}": {{')
    for t, lb in sectors.items():
        if t in ok:
            print(f'        "{t}": "{lb}",')
        else:
            why = next((r for tt, _l, r in bad if tt == t), "")
            print(f'        # "{t}": "{lb}",   # {why}')
    print("    },")
    if len(ok) < 8:
        print("\n⚠️ 통과가 8개 미만이면 백분율 해상도가 너무 거칠다 "
              "— 후보를 보강해야 한다.")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
