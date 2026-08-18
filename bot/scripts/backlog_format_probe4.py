#!/usr/bin/env python3
"""수주잔고 형식수집 프로브 **4차** — 읽기 전용·LLM 0·₩0.

3차까지 54종목을 봤고 파서가 대부분을 잡는다. 4차 목적은 **마지막 사각 확인**:

  ① **파서 개선 후 재확인** — 3차에서 보류한 두산에너빌리티·SFA 가 표 끝 마커
     수정(2026-08-17)으로 달라졌는지. 이미 되던 한전KPS·파크시스템스도 회귀
     확인용으로 넣는다(형식 확장이 기존 종목을 깨뜨리지 않았는지).
  ② **완전 미표집 업종** — 철도·인프라 · 환경/수처리 · 항공우주 부품 ·
     의료기기/계측 · 소재. 지금 표본은 조선·건설·방산·전력기기·반도체장비·
     2차전지장비에 몰려 있다.
  ③ **음성 대조군** — GS리테일처럼 수주업이 아닌 종목을 일부러 넣는다.
     '미공시'가 정상으로 나와야 파서가 아무 표나 집지 않는다는 확인이 된다.

⚠️ 이 목록의 종목코드는 내가 적은 것이라 **틀릴 수 있다.** 그래서 프로브가
   DART 가 아는 회사명을 함께 찍는다 — 3차에서 실제로 `222080` 을 씨아이에스로
   적었다가 DART 가 'SFA넥셀' 이라고 답해 즉시 드러났다(실수 #12).
   이름이 다르면 그 줄은 버리고 정확한 코드를 알려주면 된다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe4
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe4 045390

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import re
import sys

_CANDIDATES: list[tuple[str, str]] = [
    # ── 미해결 재확인 (파서 개선 후 잡히는지) ────────────────────
    ("034020", "두산에너빌리티"),      # 프로젝트별 표 2개 중복 — 여전히 보류 예상
    ("056190", "SFA"),                # 신규수주/매출/기말잔고 3열(기초 없음)
    # ── 철도·인프라 (완전 미표집) ────────────────────────────────
    ("045390", "대아티아이"),
    ("007070", "GS리테일"),           # 대조군 — 비수주업이면 미공시가 정상
    ("000210", "DL"),
    ("294870", "HDC현대산업개발"),
    ("051600", "한전KPS"),            # 재확인(주석형이 계속 잡히는지)
    # ── 환경·수처리·플랜트 ──────────────────────────────────────
    ("079980", "휴비스"),
    ("017040", "광명전기"),
    ("082740", "HSD엔진"),
    ("267250", "HD현대"),
    ("010060", "OCI홀딩스"),
    # ── 항공·우주·부품 ──────────────────────────────────────────
    ("012280", "영화금속"),
    ("099320", "쎄트렉아이"),
    ("064960", "SNT모티브"),
    ("003570", "SNT다이내믹스"),
    ("010820", "퍼스텍"),
    # ── 조선기자재 잔여 ─────────────────────────────────────────
    ("071970", "HD현대마린엔진"),
    ("009730", "삼영이엔씨"),
    ("092790", "넥스틸"),
    ("104540", "코렌스"),
    # ── 반도체·디스플레이 장비 잔여 ─────────────────────────────
    ("058610", "에스피지"),
    ("140860", "파크시스템스"),        # 재확인
    ("108320", "LX세미콘"),
    ("232140", "와이아이케이"),
    ("179900", "유티아이"),
    ("089980", "상아프론테크"),
    # ── 2차전지 장비 잔여 ───────────────────────────────────────
    ("357780", "솔브레인"),
    ("340570", "티앤엘"),
    ("336370", "솔루스첨단소재"),
    ("348370", "엔켐"),
    # ── 의료기기·계측 ───────────────────────────────────────────
    ("100660", "서암기계공업"),
    ("041830", "인바디"),
    ("214150", "클래시스"),
]

_BAL = re.compile(r"기말수주잔고|수주잔고금액|수주잔고|수주잔액|계약잔액|수주잔")
_UNIT = re.compile(r"\(\s*단위\s*[:：][^)]{0,30}\)")


def _report(dart, code: str):
    import datetime as _dt
    year = _dt.date.today().year
    for yr, rc, nm in ((year, "11012", "반기"), (year, "11014", "3분기"),
                       (year - 1, "11011", "사업"), (year, "11013", "1분기"),
                       (year - 1, "11012", "반기(전년)")):
        rep = dart.find_periodic_report(code, yr, rc)
        if rep and rep.get("rcept_no"):
            return rep, f"{yr}/{rc}({nm})"
    return None, ""


def probe(code: str, expect_name: str = "") -> str:
    """→ 'OK' | 'NONE'(미공시) | 'MISS'(공시하는데 미지원) | 'ERR'"""
    from bot.dart_backlog import parse_backlog
    from bot.dart_client import get_dart
    from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
    dart = get_dart()
    if not dart:
        print("  ❌ DART_API_KEY 없음 — .env 확인")
        return "ERR"
    # ⚠️ DART 가 아는 이름을 찍는다 — 내가 코드를 잘못 적었으면 여기서 드러난다.
    name = dart.stock_code_to_name(code) or "?"
    tag_name = f"{code} {name}" + (f"  (기대: {expect_name})"
                                   if expect_name and expect_name != name else "")
    rep, tag = _report(dart, code)
    if not rep:
        print(f"■ {tag_name} — ❌ 정기보고서 미확인")
        return "ERR"
    text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                           max_bytes=_DOC_TEXT_MAX_FULL)
    if not text:
        print(f"■ {tag_name} — ❌ 원문 수신 실패")
        return "ERR"

    got = parse_backlog(text)
    if got:
        # 이미 되는 종목은 한 줄만 — 형식 수집이 불필요하다.
        print(f"■ {tag_name} — ✅ {got['value']/1e12:.3f}조 [{got['form']}] {tag}")
        return "OK"

    hits = [m.start() for m in _BAL.finditer(text)]
    if not hits:
        print(f"■ {tag_name} — ⚪ 미공시(키워드 0회) {tag} {len(text):,}자")
        return "NONE"

    print("=" * 78)
    print(f"■ {tag_name} — ❌ 미지원 {tag} {len(text):,}자 · 키워드 {len(hits)}회")
    print("=" * 78)
    if min(hits) > 3_000_000:
        print(f"  ⚠️ 첫 히트 @{min(hits):,} — 옛 3MB 상한이면 놓쳤다.")
    shown = 0
    while hits and shown < 2:
        h = hits[0]
        caps = [m for m in _UNIT.finditer(text, 0, h)]
        start = caps[-1].start() if caps and h - caps[-1].end() < 3000 else h - 250
        print(f"  ── 표 @{max(0, start):,} (키워드 @{h:,}) " + "─" * 30)
        print("  " + text[max(0, start):h + 1200])
        shown += 1
        hits = [x for x in hits if x > h + 1200]
    return "MISS"


def main(argv: list[str]) -> int:
    # ⚠️ **파이프로 태우면 stdout 이 블록 버퍼링된다.** 이 스크립트들은
    # 수십 분 도는 진단이라 `| tee` 로 받는 게 정상 사용인데, 그러면 버퍼가
    # 찰 때까지 아무것도 안 보이고 Ctrl-C 하면 **결과가 통째로 사라진다**
    # (사용자 2026-08-18 실측). 라인 버퍼링으로 되돌린다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    args = argv[1:]
    items = [(a, "") for a in args] if args else _CANDIDATES
    tally: dict[str, list[str]] = {"OK": [], "NONE": [], "MISS": [], "ERR": []}
    for code, name in items:
        try:
            tally[probe(code, name)].append(name or code)
        except Exception as exc:
            print(f"■ {code} {name} — ❌ {type(exc).__name__}: {exc}")
            tally["ERR"].append(name or code)
    print("\n" + "=" * 78)
    print("■ 요약")
    print("=" * 78)
    for k, label in (("OK", "✅ 파서 지원"), ("MISS", "❌ 공시하는데 미지원"),
                     ("NONE", "⚪ 미공시"), ("ERR", "⚠️ 조회 실패")):
        if tally[k]:
            print(f"  {label} {len(tally[k])}종목: {' · '.join(tally[k])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
