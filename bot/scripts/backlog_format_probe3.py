#!/usr/bin/env python3
"""수주잔고 형식수집 프로브 **3차** — 읽기 전용·LLM 0·₩0.

1·2차로 22종목 중 19종목을 지원하게 됐다. 3차 목적은 **커버리지 최대화**:

  ① **재확인** — 1차에서 '미공시'로 판정한 2종목은 그때 `_fetch_doc_text` 가
     원문을 **3MB 에서 자르고 있었다**(2026-08-17 수정). 「매출 및 수주상황」은
     대개 그 뒤에 있어서 **공시하는 회사도 0회로 보였을 수 있다**. 40MB 로
     다시 본다 — 두산에너빌리티는 원전 대장이라 특히 중요하다.
  ② **미해결 1종** — GS건설은 국내표 헤더가 2차 창에도 안 잡혔다.
  ③ **미표집 업종 확대** — 지금까지 표본은 조선·건설·방산·전력기기·반도체장비
     뿐이다. 2차전지 장비 · 디스플레이 장비 · 조선기자재 · 원전기자재 · IT/SI ·
     통신장비는 형식이 또 다를 수 있다. 음성 표본(미공시)도 커버리지 한계를
     긋는 데 쓴다.

⚠️ 티커 접미사(.KS/.KQ)는 DART 조회에서 잘려나가므로 **6자리 코드만** 쓴다.
   대신 회사명을 DART 에서 읽어 함께 찍는다 — 코드를 잘못 골랐으면 이름에서
   바로 드러난다(내가 종목코드를 외워서 적는 건 실수 #12 '사전지식 stale').

⚠️ 출력이 길어지지 않게, **현재 파서가 이미 처리하는 종목은 한 줄만** 찍는다.
   형식 수집이 필요한 건 실패한 종목뿐이다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe3
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe3 042700 036930

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import re
import sys

_CANDIDATES: list[tuple[str, str]] = [
    # ── ① 3MB 절단 피해 재확인 ───────────────────────────────────
    ("034020", "두산에너빌리티"),      # 원전·발전 — 1차 '키워드 0회'가 절단 탓일 수 있음
    ("039030", "이오테크닉스"),        # 반도체 레이저 — 동일
    # ── ② 미해결 ────────────────────────────────────────────────
    ("006360", "GS건설"),             # 국내표 헤더 미확보(계약잔액 vs 수주잔고 판정용)
    # ── ③ 반도체 장비·소재 (사용자 관심 인접) ────────────────────
    ("042700", "한미반도체"),
    ("036930", "주성엔지니어링"),
    ("084370", "유진테크"),
    ("319660", "피에스케이"),
    ("039440", "에스티아이"),
    ("281820", "케이씨텍"),
    ("348210", "넥스틴"),
    ("403870", "HPSP"),
    # ── 디스플레이 장비 ─────────────────────────────────────────
    ("056190", "에스에프에이"),
    ("265520", "AP시스템"),
    # ── 2차전지 장비 (완전 미표집 업종) ──────────────────────────
    ("222080", "씨아이에스"),
    ("259630", "엠플러스"),
    ("378340", "필에너지"),
    ("299030", "하나기술"),
    ("372170", "윤성에프앤씨"),
    # ── 조선기자재·해양 (조선 본체는 다 되는데 기자재는 미표집) ──
    ("075580", "세진중공업"),
    ("017960", "한국카본"),
    ("033500", "동성화인텍"),
    ("100090", "SK오션플랜트"),
    ("443060", "HD현대마린솔루션"),
    # ── 원전·발전 기자재 ────────────────────────────────────────
    ("083650", "비에이치아이"),
    ("105840", "우진"),
    # ── 건설 (GS건설형 계약잔액 표가 업계 관행인지 판정) ─────────
    ("375500", "DL이앤씨"),
    ("047040", "대우건설"),
    ("003070", "코오롱글로벌"),
    # ── IT·SI (현대오토에버가 되니 동종도 될 가능성) ─────────────
    ("018260", "삼성에스디에스"),
    ("022100", "포스코DX"),
    # ── 방산·통신장비 ───────────────────────────────────────────
    ("272210", "한화시스템"),
    ("032500", "케이엠더블유"),
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
