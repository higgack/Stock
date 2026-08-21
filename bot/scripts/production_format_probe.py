#!/usr/bin/env python3
"""생산능력·생산실적·**가동률** 표 형식 수집 스윕 — 읽기 전용·LLM 0·₩0.

목적(사용자 2026-08-20 "이 가동률/생산능력/생산실적을 제공하는 종목들은 최대한
가져오고 싶어. 형식들이 다 똑같지는 않을거야"): 파서를 넓히기 **전에** 실제
원문에서 형식 분포를 실측한다. 마이크로컨텍솔(098120) 한 종목만 보고 짠 현행
파서가 다른 회사에서 어떻게 깨지는지를 숫자로 안다.

⚠️ 추측으로 형식을 추가하면 엉뚱한 표를 집는다(실수 #12). `dart_backlog` 는
54종목을 3차에 걸쳐 실측한 뒤에야 형식을 확정했다 — 같은 규율을 따른다.

출력 = 종목별 판정 코드 + 미지원 종목의 **표 헤더 미리보기**. 헤더를 봐야
"어떤 형식을 추가할지"가 정해진다. 판정 코드는 dart_production.diagnose:

  정상        — 가동률까지 있는 표를 찾았다(현행 파서로 충분)
  가동률없음   — 생산능력·실적만 있는 표(회사가 가동률을 안 씀 → 표는 실림)
  형식미지원   — 단어는 스치는데 표 구조가 다르다  ← **파서 확장 대상**
  표없음      — 절은 있는데 산문만(생산 서술)      ← 확장 여지 있음
  섹션없음     — 생산·설비 절 자체가 없다(비제조업·지주사 등 — 여지 없음)
  원문미제공   — DART 가 그 접수건 문서를 안 준다(계정·원천 문제)

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.production_format_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.production_format_probe 005930 000660
    # --limit N     유니버스 상한(기본 40)
    # --show N      미지원 종목당 헤더 미리보기 글자수(기본 260)

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time

_PROBE_VER = 1          # 진단 스크립트 버전 배너(실수 #21)


def _universe(limit: int) -> list[str]:
    """실제로 우리가 보는 KR 종목 — 관심종목 + 분석 아카이브. 하드코딩된
    표본이 아니라 **화면이 쓰는 모집단**이라야 스윕이 의미가 있다."""
    out: list[str] = []
    seen: set[str] = set()

    def add(t):
        t = (t or "").strip().upper()
        code = t.split(".")[0]
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            return
        seen.add(code)
        out.append(code)

    # ⚠️ 실제 API 로만 — 처음엔 `favorites.load_favorites`·`archive.list_runs`
    # 를 가정해 썼는데 **둘 다 없는 이름**이었다(#53: 이름만 보고 판정 금지).
    # 관심종목이 곧 "우리가 실제로 보는 KR 종목"이라 모집단으로 충분하다.
    try:
        from bot.market_favorites import get_favorites
        for f in get_favorites() or []:
            add((f or {}).get("ticker") if isinstance(f, dict) else f)
    except Exception as exc:                                   # noqa: BLE001
        print(f"   (관심종목 로드 실패: {exc})")
    if not out:
        print("   ⚠️ 관심종목이 비어 있다 — 티커를 인자로 직접 넘겨라")
    return out[:limit]


def _latest_quarters(dart, ticker: str) -> list[dict]:
    """최신 보고서부터 최대 3개 — 롤링과 같은 순서로 본다."""
    try:
        from bot.dart_quarterly import get_quarterly_series
        qs = get_quarterly_series(dart, ticker, n=3) or []
        return qs
    except Exception:                                          # noqa: BLE001
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="생산능력·가동률 표 형식 스윕")
    ap.add_argument("tickers", nargs="*", help="비우면 관심종목+아카이브")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--show", type=int, default=260)
    args = ap.parse_args(argv)

    from bot.dart_client import get_dart
    from bot.dart_feed import (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL,
                               _fetch_doc_text)
    from bot import dart_production as dp

    print(f"=== 생산능력·가동률 형식 스윕 v{_PROBE_VER} "
          f"(파서 앵커 v{dp._SCAN_WINDOW//1000}k) ===")
    dart = get_dart()
    if not dart:
        print("❌ DART 키 미설정 — .env 확인")
        return 1

    tickers = [t.split(".")[0] for t in args.tickers] or _universe(args.limit)
    print(f"대상 {len(tickers)}종목\n")

    tally: dict[str, int] = {}
    unsupported: list[tuple[str, str, str]] = []
    for i, tk in enumerate(tickers, 1):
        qs = _latest_quarters(dart, tk)
        if not qs:
            tally["분기데이터없음"] = tally.get("분기데이터없음", 0) + 1
            print(f"[{i:3}/{len(tickers)}] {tk}  분기데이터없음")
            continue
        verdict, markup, basis = "원문미제공", None, ""
        for q in reversed(qs):
            for cap in (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL):
                mk = _fetch_doc_text(
                    (dart.find_periodic_reports(tk, q["year"], q["reprt_code"])
                     or [{}])[0].get("rcept_no") or "",
                    dart.api_key, max_bytes=cap, raw_markup=True)
                v = dp.diagnose(mk)
                if v != "원문미제공":
                    verdict, markup, basis = v, mk, q.get("label", "")
                    break
            if verdict in ("정상", "가동률없음"):
                break
            if verdict != "원문미제공":
                break
        tally[verdict] = tally.get(verdict, 0) + 1
        got = dp.parse_production(markup) if markup else None
        mark = "✅" if got else ("△" if verdict == "가동률없음" else "❌")
        print(f"[{i:3}/{len(tickers)}] {tk} {mark} {verdict:<8} {basis}")
        # ⚠️ 미지원만 헤더를 찍는다 — 이게 다음 형식을 정하는 유일한 근거다.
        if not got and verdict in ("형식미지원", "표없음", "가동률없음"):
            head = ""
            if markup:
                m = dp._ANCHOR.search(markup) or dp._ANCHOR_ALT.search(markup)
                if m:
                    seg = markup[m.end(): m.end() + 6000]
                    tabs = dp._TABLE_RE.findall(seg)
                    pick = max(tabs, key=dp._score, default="")
                    head = re.sub(r"\s+", " ",
                                  re.sub(r"(?is)<[^>]+>", "|", pick))[:args.show]
            unsupported.append((tk, verdict, head))
        time.sleep(0.4)          # 원천 배려(간격 — 실수 #21b)

    print("\n" + "=" * 68)
    print("판정 분포:", dict(sorted(tally.items(), key=lambda x: -x[1])))
    ok = tally.get("정상", 0)
    print(f"현행 파서 커버리지: {ok}/{len(tickers)} "
          f"({100.0 * ok / max(1, len(tickers)):.0f}%)")
    if unsupported:
        print(f"\n--- 미지원 {len(unsupported)}종목 표 헤더(형식 추가 근거) ---")
        for tk, v, head in unsupported:
            print(f"\n[{tk}] {v}\n  {head or '(표 없음)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
