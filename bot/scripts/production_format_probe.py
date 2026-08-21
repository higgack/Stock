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

  정상        — 가동률까지 있는 표(현행 파서로 충분)          ← 화면에 실림
  가동률없음   — 생산능력+실적만(회사가 가동률을 안 씀)          ← 화면에 실림
  능력만      — 생산능력 **또는** 실적 하나만                   ← 화면에 실림
  무관표만     — 표는 있는데 세 어구가 하나도 없다  ← **파서 확장 대상**
  표없음      — 절은 있는데 산문만(생산 서술)      ← 확장 여지 있음
  섹션없음     — 생산·설비 절 자체가 없다(비제조업·지주사 등 — 여지 없음)
  원문잘림     — 상한까지 받아도 앵커가 안 보인다(원천/상한 문제 — '없음' 아님)
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

_PROBE_VER = 6          # 진단 스크립트 버전 배너(실수 #21)
#   v2(2026-08-21): 상한 escalation 을 제품 경로와 일치시킴 +
#   미리보기 창을 파서 스캔창과 동일하게 + 최고점수·문서길이 표기.
#   v3(2026-08-21): 「주요 제품 및 서비스」 표 커버리지 동시 집계
#   (같은 원문을 재사용 — DART 호출 0 추가).
#   v4(2026-08-21): 잘림 판정을 원천 플래그로(문자 길이 추정 금지).
#   v5(2026-08-21): 분기실적 탭 **전 항목** 커버리지 감사로 확장
#   (지표·차트 / 제품표 / 생산능력표 / 수주잔고 / 재고자산).
#   v6(2026-08-21): 어느 **서식(앵커)**로 잡혔는지 표기·집계.
#   DART 서식이 두 벌이라(`원재료 및 생산설비` 현행 /
#   `생산 및 설비에 관한 사항` 구) 어느 쪽이 남는지 봐야 한다.


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
    ap.add_argument("--skip-backlog", action="store_true",
                    help="수주잔고 검사 생략(40MB 재파싱이 느릴 때)")
    args = ap.parse_args(argv)

    from bot.dart_client import get_dart
    from bot.dart_feed import (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL,
                               _fetch_doc_text)
    from bot.dart_feed import doc_was_truncated as dp_trunc
    from bot import dart_production as dp

    print(f"=== 분기실적 탭 항목 커버리지 감사 v{_PROBE_VER} "
          f"(앵커창 {dp._SCAN_WINDOW//1000}k/{dp._SCAN_WINDOW_ALT//1000}k) ===")
    print("열: 분기수 · 재고 · 수주 · 제품 · 생산(판정)  "
          "— 원문에 있는데 못 가져오는 걸 찾는 게 목적")
    dart = get_dart()
    if not dart:
        print("❌ DART 키 미설정 — .env 확인")
        return 1

    tickers = [t.split(".")[0] for t in args.tickers] or _universe(args.limit)
    print(f"대상 {len(tickers)}종목\n")

    tally: dict[str, int] = {}
    cover = {"분기": 0, "재고자산": 0, "수주잔고": 0, "제품표": 0, "생산표": 0}
    anchors: dict[str, int] = {}          # 서식별 히트 수
    unsupported: list[tuple[str, str, str]] = []
    _OK = ("정상", "가동률없음", "능력만")     # 화면에 실리는 판정
    for i, tk in enumerate(tickers, 1):
        qs = _latest_quarters(dart, tk)
        if not qs:
            tally["분기데이터없음"] = tally.get("분기데이터없음", 0) + 1
            print(f"[{i:3}/{len(tickers)}] {tk}  분기데이터없음")
            continue
        verdict, markup, basis, dlen, cutmark = "원문미제공", None, "", 0, False
        for q in reversed(qs):
            rn = (dart.find_periodic_reports(tk, q["year"], q["reprt_code"])
                  or [{}])[0].get("rcept_no") or ""
            if not rn:
                continue
            # ⚠️ **제품 경로와 같은 순서로 상한을 올린다**(실수 #35).
            # v1 은 "판정이 원문미제공만 아니면" 곧장 break 해서, 3MB 안에
            # 앵커가 안 잡히는 대형사를 40MB 로 한 번도 다시 받지 않았다 —
            # 삼성전자·SK하이닉스가 '섹션없음'으로 찍힌 원인이다.
            # production_for 는 parse_production 이 실패하면 FULL 로 올린다.
            for cap in (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL):
                mk = _fetch_doc_text(rn, dart.api_key, max_bytes=cap,
                                     raw_markup=True)
                # ⚠️ 길이로 추정하지 않는다 — 상한은 **바이트**, 반환은
                # 정규화된 **문자열**이라 항상 더 짧아 판정이 늘 '안 잘림'
                # 으로 기운다. v3 에서 삼성전자가 2,826k자/3,000k바이트로
                # 0.94배라 재시도를 안 해 '섹션없음'으로 찍혔다.
                cut = dp_trunc(rn, cap, True)
                v = dp.diagnose(mk, truncated=cut)
                if mk:
                    verdict, markup, basis, dlen = v, mk, q.get("label", ""), len(mk)
                    cutmark = cut
                if v in _OK:
                    break
                if not cut:
                    break          # 잘리지 않았는데 못 찾으면 올려도 같다
            if verdict in _OK:
                break
        tally[verdict] = tally.get(verdict, 0) + 1
        got = dp.parse_production(markup) if markup else None
        # 제품 표는 **같은 원문**에서 뽑는다 — 추가 호출 0.
        prod = dp.parse_products(markup) if markup else None
        if prod:
            tally["제품표"] = tally.get("제품표", 0) + 1
        # ── 나머지 항목도 같이 센다 — "있는데 누락"을 찾는 게 목적이다.
        # 재고자산은 재무제표 계정이라 원문 없이 분기 데이터에서 바로 보인다.
        inv = sum(1 for q in qs
                  if (q.get("financials") or {}).get("재고자산") is not None)
        # 수주잔고는 본문 표 — 같은 접수번호라 zip 캐시에 걸려 재다운로드 0.
        # ⚠️ 화면과 **같은 규율**로 최신부터 거슬러 본다(1회만 보면 최신
        # 보고서 문서가 없는 회사가 통째로 '미공시'로 찍힌다).
        bl = None
        if not args.skip_backlog:
            try:
                from bot.dart_backlog import backlog_for
                from bot.quarterly_infographic import _BACKLOG_PROBE_N
                for q in list(reversed(qs))[:_BACKLOG_PROBE_N]:
                    bl = backlog_for(dart, tk, q["year"], q["reprt_code"])
                    if bl is not None:
                        break
            except Exception as exc:                           # noqa: BLE001
                print(f"   (수주잔고 검사 실패: {exc})")
        cover["분기"] += 1 if qs else 0
        cover["재고자산"] += 1 if inv else 0
        cover["수주잔고"] += 1 if bl is not None else 0
        cover["제품표"] += 1 if prod else 0
        cover["생산표"] += 1 if got else 0
        if got and got.get("anchor"):
            anchors[got["anchor"]] = anchors.get(got["anchor"], 0) + 1
        mark = "✅" if got else "❌"
        kinds = ",".join(got.get("kinds") or []) if got else ""
        print(f"[{i:3}/{len(tickers)}] {tk} {len(qs)}Q "
              f"{'재고' if inv else '  ·  '} "
              f"{'수주' if bl is not None else '  ·  '} "
              f"{'제품' if prod else '  ·  '} "
              f"{mark} {verdict:<8} {basis:<6}"
              f" {dlen//1000:>5}k{'✂' if cutmark else ' '}"
              f" {(got or {}).get('anchor', ''):<12} {kinds}")
        # ⚠️ 미채택만 헤더를 찍는다 — 이게 다음 형식을 정하는 유일한 근거다.
        if not got:
            head = ""
            if markup:
                m = dp._ANCHOR.search(markup) or dp._ANCHOR_ALT.search(markup)
                if m:
                    # 창을 파서와 **똑같이** 잡는다. v1 은 6000자만 봐서
                    # 파서가 실제로 보고 버린 표를 못 보여줬다(미리보기가
                    # 전부 `(단위 : …)` 캡션 표였던 이유).
                    seg = markup[m.end(): m.end() + dp._SCAN_WINDOW]
                    tabs = dp._TABLE_RE.findall(seg)
                    pick = max(tabs, key=dp._score, default="")
                    head = (f"[{len(tabs)}표 최고점 {dp._score(pick)}] " +
                            re.sub(r"\s+", " ",
                                   re.sub(r"(?is)<[^>]+>", "|", pick))
                            [:args.show])
            unsupported.append((tk, verdict, head))
        time.sleep(0.4)          # 원천 배려(간격 — 실수 #21b)

    print("\n" + "=" * 68)
    print("판정 분포:", dict(sorted(tally.items(), key=lambda x: -x[1])))
    ok = sum(tally.get(k, 0) for k in ("정상", "가동률없음", "능력만"))
    rate = tally.get("정상", 0)
    print(f"화면에 실림: {ok}/{len(tickers)} "
          f"({100.0 * ok / max(1, len(tickers)):.0f}%) "
          f"— 그중 가동률 포함 {rate}건")
    print("\n항목별 커버리지 (분기실적 탭에 실제로 실리는 것):")
    for k in ("분기", "재고자산", "수주잔고", "제품표", "생산표"):
        n = cover[k]
        print(f"  {k:<6} {n:>3}/{len(tickers)} "
              f"({100.0 * n / max(1, len(tickers)):.0f}%)")
    if anchors:
        print("\n생산 표를 잡은 서식(앵커):")
        for k, v in sorted(anchors.items(), key=lambda x: -x[1]):
            print(f"  {k:<20} {v:>3}건")
    if unsupported:
        print(f"\n--- 미지원 {len(unsupported)}종목 표 헤더(형식 추가 근거) ---")
        for tk, v, head in unsupported:
            print(f"\n[{tk}] {v}\n  {head or '(표 없음)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
