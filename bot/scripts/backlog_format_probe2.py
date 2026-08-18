#!/usr/bin/env python3
"""수주잔고 형식수집 프로브 **2차** — 읽기 전용·LLM 0·₩0.

1차(`backlog_format_probe.py`)가 16종목으로 3형식을 확정해 파서를 만들었다.
2차의 목적은 두 가지다:

  ① **1차에서 못 짠 4종목**의 표를 헤더까지 보이게 다시 뜬다. 1차는 창 앵커를
     '아무 키워드나 첫 히트'로 잡아서 엉뚱한 표(연결대상 종속회사 현황 등)의
     `합 계` 를 찍었고, 정작 필요한 표의 **헤더가 창 밖**이었다.
     → 삼성E&A·GS건설·효성중공업(합계행 없는 표) · 한국항공우주(기초/기말 2열).
  ② **사용자 관심 반도체장비 6종목**(2026-08-17 지정). 장비업은 수주산업이라
     공시 가능성이 있으나 1차 표본(조선·건설·방산·전력기기)에 없던 업종이라
     형식이 또 다를 수 있다. 음성 표본(미공시)도 커버리지 한계를 긋는 데 쓴다.

⚠️ 1차 대비 바뀐 것 — **앵커를 표 헤더로 잡는다.** `수주잔고|수주잔액` 이
`단위` 캡션 뒤에 오는 지점을 표의 시작으로 보고 **캡션부터** 출력하므로
헤더·행·합계가 한 창에 들어온다. 히트가 여러 개면 전부 찍는다(1차는 첫 개만
찍어서 본 표를 놓친 종목이 있었다).

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe2
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe2 095610.KQ

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import re
import sys

# ① 1차 미해결 — 파서가 지원 못 하는 형식. 헤더가 보여야 짤 수 있다.
_UNRESOLVED = [
    "028050.KS",   # 삼성E&A       — 합계행 없음, 행별 3열
    "006360.KS",   # GS건설        — 국내/해외 표 분리, 헤더 미확인
    "298040.KS",   # 효성중공업     — 합계행 없음, 단위 미확인
    "047810.KS",   # 한국항공우주   — 기초/기말 2열(검산할 항등식 없음)
]
# ② 사용자 관심 반도체 장비·검사 (2026-08-17). 1차에 없던 업종.
_USER = [
    "095610.KQ",   # 테스
    "240810.KQ",   # 원익IPS
    "140860.KQ",   # 파크시스템스
    "089030.KQ",   # 테크윙
    "089790.KQ",   # 제이티
    "131290.KQ",   # 티에스이
]
_CANDIDATES = _UNRESOLVED + _USER

_BAL = re.compile(r"기말수주잔고|수주잔고|수주잔액|계약잔액")
_UNIT = re.compile(r"\(\s*단위\s*[:：][^)]{0,30}\)")
# 표가 아니라 산문에 섞인 경우(씨에스윈드형)도 놓치지 않게 별도 수집.
_PROSE = re.compile(r"[^.]{0,80}수주(?:잔고|잔액)[는은]?\s*[^.]{0,80}?[\d,]{4,}[^.]{0,30}")


def _report(dart, ticker: str):
    import datetime as _dt
    year = _dt.date.today().year
    for yr, rc, nm in ((year, "11012", "반기"), (year, "11014", "3분기"),
                       (year - 1, "11011", "사업"), (year, "11013", "1분기"),
                       (year - 1, "11012", "반기(전년)")):
        rep = dart.find_periodic_report(ticker, yr, rc)
        if rep and rep.get("rcept_no"):
            return rep, f"{yr}/{rc}({nm})"
    return None, ""


def probe(ticker: str) -> None:
    print("=" * 78)
    print(f"■ {ticker}")
    print("=" * 78)
    from bot.dart_backlog import parse_backlog
    from bot.dart_client import get_dart
    from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
    dart = get_dart()
    if not dart:
        print("  ❌ DART_API_KEY 없음 — .env 확인")
        return
    rep, tag = _report(dart, ticker)
    if not rep:
        print("  ❌ 정기보고서 미확인")
        return
    print(f"  보고서 {tag} rcept_no={rep['rcept_no']} {rep.get('report_nm','')}")
    text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                           max_bytes=_DOC_TEXT_MAX_FULL)
    if not text:
        print("  ❌ 원문 수신 실패")
        return

    # 현재 파서가 이미 처리하는지 먼저 본다 — 되면 형식 수집이 불필요하다.
    got = parse_backlog(text)
    print(f"  원문 {len(text):,}자 · 현재 파서: "
          + (f"✅ {got['value']/1e12:.2f}조 [{got['form']}]" if got else "❌ 미지원"))

    hits = [m.start() for m in _BAL.finditer(text)]
    if not hits:
        print("  → 키워드 0회. **미공시**(원천에 없음) — 파서로 해결 불가.")
        return
    print(f"  잔고 키워드 {len(hits)}회 @ " +
          ", ".join(f"{h:,}" for h in hits[:12]) + (" …" if len(hits) > 12 else ""))

    # ⚠️ 앵커 = **직전 `(단위 …)` 캡션**. 표 캡션은 항상 표 위에 오므로
    # 거기서부터 뜨면 헤더·데이터행·합계가 한 창에 들어온다(1차의 실패 지점).
    shown = 0
    for h in hits:
        if shown >= 3:
            break
        caps = [m for m in _UNIT.finditer(text, 0, h)]
        start = caps[-1].start() if caps and h - caps[-1].end() < 3000 else h - 200
        start = max(0, start)
        seg = text[start:h + 1400]
        print(f"  ── 표 @{start:,} (키워드 @{h:,}) " + "─" * 34)
        print("  " + seg)
        shown += 1
        # 같은 표 안의 다음 히트는 건너뛴다(중복 출력 방지).
        hits = [x for x in hits if x > h + 1400]
        if not hits:
            break

    for m in list(_PROSE.finditer(text))[:2]:
        print(f"  ── 산문형 후보 @{m.start():,} " + "─" * 38)
        print("  " + m.group(0).strip())


def main(argv: list[str]) -> int:
    # ⚠️ **파이프로 태우면 stdout 이 블록 버퍼링된다.** 이 스크립트들은
    # 수십 분 도는 진단이라 `| tee` 로 받는 게 정상 사용인데, 그러면 버퍼가
    # 찰 때까지 아무것도 안 보이고 Ctrl-C 하면 **결과가 통째로 사라진다**
    # (사용자 2026-08-18 실측). 라인 버퍼링으로 되돌린다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    for t in (argv[1:] or _CANDIDATES):
        try:
            probe(t)
        except Exception as exc:
            print(f"  ❌ {t} 실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
