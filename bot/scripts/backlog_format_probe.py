#!/usr/bin/env python3
"""수주잔고 공시 **형식** 수집 프로브 — 읽기 전용·LLM 0·₩0.

목적: 파서를 짜기 전에 「매출 및 수주상황」의 **실제 평문 배열**을 종목별로
모아 온다. 형식이 회사마다 다르다는 게 이미 실측됐다(2026-08-16 5종목):

  • 표·합계행형 (한화오션) : `합 계 - 41,675,211 - (8,666,801) - 33,008,410`
  • 표·부문행형 (HD현대일렉트릭) : 합계행 없이 부문별 3열, 합산 필요
  • 산문형     (현대로템) : `수주잔고는 304,046억원을 확보하고 있습니다`
  • 미공시     (두산에너빌리티·이오테크닉스) : 키워드 0회 — 원천에 없음

⚠️ 추측으로 파서를 짜면 숫자를 지어낸다(CLAUDE.md 실수 #12·데이터 vs 환각).
이 스크립트 출력을 그대로 붙여넣어야 다음 형식을 추가할 수 있다.

⚠️ `_fetch_doc_text` 기본 3MB 로는 부족하다 — 「매출 및 수주상황」은 목차상
II.사업의 내용 뒤라 3MB 밖으로 밀리는 게 보통이고, 그러면 **공시하는 회사도
'없음'으로 오판된다**. 여기선 _DOC_TEXT_MAX_FULL 로 받는다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_format_probe 010140.KS ...

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import re
import sys

# 수주잔고를 **실제로 공시할 가능성이 높은** 업종을 형식 다양성 기준으로 배분.
# 조선/플랜트는 합계행형, 방산·전력기기는 부문행형, 건설은 산문형이 많을 것으로
# 예상되나 **예상은 예상일 뿐** — 그래서 이 프로브를 돌린다.
_CANDIDATES = [
    # 조선 — 수주산업의 원형. 합계행형 표본 확대.
    "010140.KS",   # 삼성중공업
    "009540.KS",   # HD한국조선해양
    "329180.KS",   # HD현대중공업
    # 건설·EPC — 「수주상황」 대신 「공사수주 및 진행상황」을 쓰는 곳이 많다.
    "028050.KS",   # 삼성E&A
    "000720.KS",   # 현대건설
    "006360.KS",   # GS건설
    # 방산·우주 — 장기계약이라 잔고 공시 관행이 강하다.
    "047810.KS",   # 한국항공우주
    "079550.KS",   # LIG넥스원
    "012450.KS",   # 한화에어로스페이스
    # 전력기기·에너지 — HD현대일렉트릭(부문행형) 과 같은 계열 확인.
    "298040.KS",   # 효성중공업
    "010120.KS",   # LS ELECTRIC
    "112610.KS",   # 씨에스윈드
    # 엔지니어링·원전 — 용역 수주라 표 형태가 또 다를 수 있다.
    "052690.KS",   # 한전기술
    "051600.KS",   # 한전KPS
    # SI·기계 — 수주잔고를 쓰는지 자체가 불확실(음성 표본도 가치 있다).
    "307950.KS",   # 현대오토에버
    "267270.KS",   # HD현대건설기계
]

# 표 헤더/산문 양쪽에 쓰이는 표현. 순서 = 진단 출력 순서일 뿐 우선순위 아님.
_KW = ("수주잔고", "수주잔액", "계약잔액", "수주총액", "수주상황", "수주액")
# 단위 표기 — 값 스케일이 이것 없이는 결정 불가(백만원 vs 억원 = 100배 오차).
_UNIT_RE = re.compile(r"단위\s*[:：]?\s*[^)\]]{0,24}?(백만원|천원|억원|천만원|원|백만불|천불|USD|달러)")


def _report(dart, ticker: str):
    """가장 최근의 정기보고서 1건. 사업>반기>3분기>1분기 순 — 뒤로 갈수록
    「수주상황」이 축약되거나 아예 빠진다."""
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
    # 3MB 기본 상한이었으면 놓쳤을 위치인지 = 상한 인상이 실제로 필요했는지.
    hits = {k: text.count(k) for k in _KW}
    print(f"  원문 {len(text):,}자 · 키워드 " +
          " ".join(f"{k}={v}" for k, v in hits.items() if v) or "  키워드 0")
    if not any(hits.values()):
        print("  → 미공시(원천에 없음). 파서로 해결 불가 — 음성 표본으로 기록.")
        return
    first = min((text.find(k) for k in _KW if hits.get(k)), default=-1)
    if first > 3_000_000:
        print(f"  ⚠️ 첫 히트가 {first:,}자 지점 — 옛 3MB 상한이면 놓쳤다.")

    # 단위: 히트 **앞쪽** 가장 가까운 표기를 쓴다(표 캡션이 표 위에 온다).
    units = [(m.start(), m.group(1)) for m in _UNIT_RE.finditer(text)
             if m.start() < first]
    print(f"  단위표기(직전): {units[-1][1] if units else '없음 — 확인 필요'}")

    # 실제 배열 — 가공 없이 그대로. 이게 이 프로브의 전부다.
    for k in ("수주잔고", "수주잔액", "계약잔액", "수주총액"):
        if not hits.get(k):
            continue
        i = text.find(k)
        print(f"  ── '{k}' 첫 등장 @{i:,} " + "─" * 40)
        print("  " + text[max(0, i - 300):i + 1500])
        break
    # 합계행은 파서의 1순위 앵커라 따로 찍는다(부문 합산보다 안전).
    m = re.search(r"합\s*계", text[first:first + 6000])
    if m:
        j = first + m.start()
        print(f"  ── '합 계' @{j:,} " + "─" * 46)
        print("  " + text[j:j + 500])
    else:
        print("  ── '합 계' 없음 → 부문행 합산형으로 추정")


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
        except Exception as exc:      # 한 종목 실패가 나머지를 막지 않게
            print(f"  ❌ {t} 실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
