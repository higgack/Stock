#!/usr/bin/env python3
"""수주잔고 파서 미스 요약 — 읽기 전용·네트워크 0.

`bot/dart_backlog._log_miss` 가 남긴 `~/.tradingagents/backlog_misses.jsonl` 를
사유별로 묶어 보여준다. **실사용이 곧 프로브**라는 설계의 수확 도구다 —
내가 종목을 골라 프로브를 돌리는 대신, 사용자가 실제로 여는 종목에서 파서가
막힌 지점이 여기 쌓인다(CLAUDE.md Automation-first).

기록되는 건 **개선 여지가 있는 사유뿐**이다(`형식미지원`·`검산실패`·`단위없음`·
`합계없음`). 미공시·명시적미공시는 원천에 값이 없어 파서로 해결할 수 없으므로
아예 안 남긴다 — 안 그러면 로그가 노이즈가 된다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_misses
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_misses --ticker 012450.KS
        → 그 종목의 5분기를 **실제로 다시 조회**해 분기별 성공/실패 사유를 찍는다
          (차트에서 특정 분기 막대만 비어 있을 때 원인 특정용).
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_misses --doc 20260319000633
        → `본문없음 0자` 일 때 원문 수신을 해부한다(HTTP 상태·바이트수·zip 엔트리).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict


def summarize() -> int:
    from bot.dart_backlog import _MISS_LOG
    if not _MISS_LOG.exists():
        print(f"기록 없음 ({_MISS_LOG}) — 아직 막힌 종목이 없거나 조회 이력이 없다.")
        return 0
    rows = []
    for ln in _MISS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    if not rows:
        print("기록 없음")
        return 0
    by_reason: dict[str, list] = defaultdict(list)
    for r in rows:
        by_reason[r.get("reason", "?")].append(r)
    print(f"■ 수주잔고 파서 미스 {len(rows)}건 · 종목 "
          f"{len({r.get('ticker') for r in rows})}개  ({_MISS_LOG})")
    print("=" * 74)
    for reason, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        tick = Counter(i.get("ticker") for i in items)
        print(f"\n[{reason}] {len(items)}건 · 종목 {len(tick)}개")
        for t, n in tick.most_common(15):
            qs = " ".join(f"{i.get('year')}/{i.get('reprt')}"
                          for i in items if i.get("ticker") == t)
            print(f"    {t:12s} {n}회  {qs}")
    print("\n→ `형식미지원`·`검산실패` 가 새 형식 신호다. 해당 종목을 "
          "`--ticker` 로 다시 보거나 backlog_format_probe 로 원문을 뜬다.")
    return 0


def per_quarter(ticker: str) -> int:
    """한 종목의 최근 5분기를 실제 조회해 분기별 결과를 찍는다."""
    from bot.dart_backlog import diagnose, parse_backlog
    from bot.dart_client import get_dart
    from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
    from bot.dart_quarterly import get_quarterly_series
    dart = get_dart()
    if not dart:
        print("❌ DART_API_KEY 없음")
        return 1
    qs = get_quarterly_series(dart, ticker, n=5)
    if not qs:
        print(f"❌ {ticker} 분기 시리즈 없음")
        return 1
    print(f"■ {ticker} — 분기별 수주잔고 조회 (차트 막대와 1:1 대응)")
    print("=" * 74)
    for q in qs:
        y, rc, label = q["year"], q["reprt_code"], q.get("label", "?")
        rep = dart.find_periodic_report(ticker, y, rc)
        if not rep or not rep.get("rcept_no"):
            print(f"  {label:8s} {y}/{rc}  ❌ 정기보고서 미확인 "
                  f"— 이 분기는 원문 자체가 없다")
            continue
        text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                               max_bytes=_DOC_TEXT_MAX_FULL) or ""
        got = parse_backlog(text)
        if got:
            print(f"  {label:8s} {y}/{rc}  ✅ {got['value']/1e12:.3f}조 "
                  f"[{got['form']}]  원문 {len(text):,}자")
        else:
            print(f"  {label:8s} {y}/{rc}  ❌ {diagnose(text)}  "
                  f"원문 {len(text):,}자  rcept={rep['rcept_no']}")
    return 0


def doc_probe(rcept_no: str) -> int:
    """`document.xml` 수신 자체를 해부한다 — HTTP 상태·바이트수·zip 엔트리.

    `본문없음 · 원문 0자` 는 rcept_no 는 찾았는데 원문 수신이 실패했다는 뜻이고,
    옛 코드는 그 사유를 통째로 삼켰다(한화에어로 2025 사업보고서·2026 1분기,
    사용자 2026-08-17). 여기서 응답을 직접 보면 원인이 확정된다."""
    import io as _io
    import zipfile

    import requests
    from bot.dart_client import get_dart
    dart = get_dart()
    if not dart:
        print("❌ DART_API_KEY 없음")
        return 1
    r = requests.get("https://opendart.fss.or.kr/api/document.xml",
                     params={"crtfc_key": dart.api_key, "rcept_no": rcept_no},
                     timeout=30)
    blob = r.content or b""
    print(f"■ rcept_no={rcept_no}")
    print(f"  HTTP {r.status_code} · Content-Type {r.headers.get('Content-Type')}")
    print(f"  수신 {len(blob):,} bytes · 선두 {blob[:200]!r}")
    if len(blob) < 200:
        print("  → 본문이 아니다. DART 가 오류 JSON/XML 을 돌려준 것 "
              "(키 권한·일일한도·존재하지 않는 접수번호 등).")
        return 0
    try:
        zf = zipfile.ZipFile(_io.BytesIO(blob))
    except Exception as exc:
        print(f"  ❌ zip 열기 실패: {type(exc).__name__}: {exc}")
        return 0
    print(f"  zip 엔트리 {len(zf.namelist())}개:")
    for n in zf.namelist():
        print(f"    {zf.getinfo(n).file_size:>12,} bytes  {n}")
    tot = sum(zf.getinfo(n).file_size for n in zf.namelist())
    print(f"  합계 {tot:,} bytes")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) > 2 and argv[1] == "--ticker":
        return per_quarter(argv[2])
    if len(argv) > 2 and argv[1] == "--doc":
        return doc_probe(argv[2])
    return summarize()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
