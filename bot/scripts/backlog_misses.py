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
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_misses --list 012450.KS 2026 11013
        → 그 분기 전후의 정기공시 원시 목록을 넓은 창으로 찍는다(창 밖 정정 확인).
    cd ~/stock && .venv/bin/python -m bot.scripts.backlog_misses --sweep
        → 공시 확정 종목 전체(형식 8종 전부 포함)를 한 줄씩 훑는다. 분기별
          빈칸 유무 + **분기간 급변**(파싱 오류 의심)까지 본다. 종목 지정 가능.
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
        reps = dart.find_periodic_reports(ticker, y, rc)
        if not reps:
            print(f"  {label:8s} {y}/{rc}  ❌ 정기보고서 미확인 "
                  f"— 이 분기는 원문 자체가 없다")
            continue
        # 후보를 전부 보여준다 — 어떤 접수건이 뽑혔고 왜 문서가 없는지가
        # 여기서 갈린다(정정·첨부 계열은 자체 문서가 없다).
        text, used = "", None
        for rep in reps:
            text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                                   max_bytes=_DOC_TEXT_MAX_FULL) or ""
            mark = "✔" if text else "✗문서없음"
            print(f"      후보 {rep['rcept_no']} {rep.get('rcept_dt','')} "
                  f"{mark}  {rep.get('report_nm','')}")
            if text:
                used = rep
                break
        rep = used or reps[0]
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


def list_probe(ticker: str, year: str, reprt: str) -> int:
    """해당 분기 전후의 **정기공시 원시 목록**을 넓은 창으로 그대로 찍는다.

    `후보 … ✗문서없음` 인데 대안이 없을 때, 창 밖에 다른 접수건이 있는지를
    눈으로 확인하는 용도다. 추측 대신 목록을 본다(실수 #12)."""
    import datetime as dt

    import requests
    from bot.dart_client import _DART_BASE, get_dart
    dart = get_dart()
    if not dart:
        print("❌ DART_API_KEY 없음")
        return 1
    corp = dart.stock_code_to_corp_code(ticker)
    if not corp:
        print(f"❌ {ticker} corp_code 미상")
        return 1
    kw, bgn, end = dart._periodic_report_window(int(year), reprt)
    wide_b = (dt.datetime.strptime(bgn, "%Y%m%d") - dt.timedelta(days=60)
              ).strftime("%Y%m%d")
    wide_e = (dt.datetime.strptime(end, "%Y%m%d") + dt.timedelta(days=300)
              ).strftime("%Y%m%d")
    print(f"■ {ticker} {year}/{reprt}  키워드='{kw}'")
    print(f"  기본 창 {bgn}~{end} · 확대 창 {wide_b}~{wide_e}")
    print("=" * 74)
    r = requests.get(f"{_DART_BASE}/list.json",
                     params={"crtfc_key": dart.api_key, "corp_code": corp,
                             "bgn_de": wide_b, "end_de": wide_e,
                             "pblntf_ty": "A", "page_count": 100}, timeout=30)
    pay = r.json()
    if pay.get("status") != "000":
        print(f"  ❌ list.json status={pay.get('status')} {pay.get('message')}")
        return 0
    rows = pay.get("list") or []
    print(f"  정기공시 {len(rows)}건 (★=키워드 매치, ●=기본 창 안)")
    for x in sorted(rows, key=lambda z: z.get("rcept_dt") or ""):
        nm, no, dtv = (x.get("report_nm") or ""), x.get("rcept_no"), x.get("rcept_dt")
        mark = ("★" if kw in nm else " ") + ("●" if bgn <= (dtv or "") <= end else " ")
        print(f"    {mark} {no} {dtv}  {nm}")
    return 0


# 수주잔고를 **공시한다고 확정된** 종목들(1~4차 프로브 88건 중). 형식 8종을
# 모두 덮도록 골랐다 — 이 스윕이 곧 형식 회귀 테스트다.
# ⚠️ 미공시 종목은 넣지 않는다. 빈칸이 정상이라 신호가 되지 않는다.
_SWEEP: list[tuple[str, str]] = [
    ("010140", "삼성중공업"), ("009540", "HD한국조선해양"),
    ("329180", "HD현대중공업"), ("042660", "한화오션"),
    ("012450", "한화에어로스페이스"), ("010120", "LS ELECTRIC"),
    ("307950", "현대오토에버"), ("095610", "테스"), ("240810", "원익IPS"),
    ("140860", "파크시스템스"), ("131290", "티에스이"), ("259630", "엠플러스"),
    ("083650", "비에이치아이"), ("036930", "주성엔지니어링"),
    ("039440", "에스티아이"), ("265520", "AP시스템"), ("378340", "필에너지"),
    ("299030", "하나기술"), ("372170", "윤성에프앤씨"), ("017960", "한국카본"),
    ("033500", "동성화인텍"), ("100090", "SK오션플랜트"),
    ("443060", "HD현대마린솔루션"), ("071970", "HD현대마린엔진"),
    ("105840", "우진"), ("045390", "대아티아이"), ("017040", "광명전기"),
    ("082740", "한화엔진"), ("267250", "HD현대"), ("099320", "쎄트렉아이"),
    ("232140", "와이씨"), ("214150", "클래시스"), ("018260", "삼성에스디에스"),
    ("022100", "포스코DX"), ("272210", "한화시스템"),
    # 합계행 없는 표(행합산)
    ("028050", "삼성E&A"), ("298040", "효성중공업"), ("089790", "제이티"),
    ("348210", "넥스틴"), ("075580", "세진중공업"), ("084370", "유진테크"),
    # 주석·단일값·잔고열·산문·전치·건설
    ("051600", "한전KPS"), ("000720", "현대건설"), ("079550", "LIG넥스원"),
    ("010820", "퍼스텍"), ("089030", "테크윙"), ("047810", "한국항공우주"),
    ("281820", "케이씨텍"), ("003070", "코오롱글로벌"), ("047040", "대우건설"),
    ("006360", "GS건설"), ("375500", "DL이앤씨"), ("056190", "SFA"),
]

# 분기간 급변 임계. 수주잔고는 **잔고(스톡)**라 분기마다 반토막·두 배가 나긴
# 어렵다 — 그 정도 튀면 단위 오인·다른 표 채택 같은 파싱 오류를 의심해야 한다.
# 값이 '있다'는 것과 '맞다'는 건 다르므로, 스윕은 정합성까지 본다.
_JUMP = 0.60


def _one(dart, ticker: str, n: int = 5):
    """→ (분기 리스트[(label, 값|None, 사유)], ) — 조용히 수집."""
    from bot.dart_backlog import diagnose, parse_backlog
    from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
    from bot.dart_quarterly import get_quarterly_series
    qs = get_quarterly_series(dart, ticker, n=n) or []
    out = []
    for q in qs:
        y, rc, label = q["year"], q["reprt_code"], q.get("label", "?")
        text = ""
        for rep in dart.find_periodic_reports(ticker, y, rc):
            text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                                   max_bytes=_DOC_TEXT_MAX_FULL) or ""
            if text:
                break
        got = parse_backlog(text)
        out.append((label, got["value"] if got else None,
                    got["form"] if got else diagnose(text)))
    return out


def sweep(tickers: list[str]) -> int:
    """여러 종목의 5분기를 한 줄씩 훑는다 — 빈칸 유무 + 값 정합성.

    ⚠️ 종목당 5개 정기보고서 원문(각 수 MB)을 받는다. 50종목이면 수십 분
    걸릴 수 있어 **한 줄씩 즉시 출력**한다(중간에 끊어도 결과가 남는다)."""
    from bot.dart_client import get_dart
    dart = get_dart()
    if not dart:
        print("❌ DART_API_KEY 없음")
        return 1
    items = ([(t.split(".")[0], "") for t in tickers] if tickers else _SWEEP)
    full, partial, empty, jumpy = [], [], [], []
    print(f"■ 수주잔고 스윕 {len(items)}종목 × 5분기 "
          f"(종목당 원문 5건 다운로드 — 오래 걸립니다)", flush=True)
    print("=" * 96, flush=True)
    for i, (code, name) in enumerate(items, 1):
        nm = dart.stock_code_to_name(code) or name or "?"
        try:
            rows = _one(dart, code)
        except Exception as exc:
            print(f"[{i:2d}/{len(items)}] {code} {nm:16s} "
                  f"❌ {type(exc).__name__}: {exc}", flush=True)
            continue
        if not rows:
            print(f"[{i:2d}/{len(items)}] {code} {nm:16s} ❌ 분기 시리즈 없음", flush=True)
            continue
        cells, vals, forms = [], [], set()
        for label, v, why in rows:
            if v is None:
                cells.append(f"{label} —({why})")
            else:
                cells.append(f"{label} {v/1e12:.2f}조")
                vals.append((label, v))
                forms.add(why)
        got_n = len(vals)
        # 분기간 급변 — 값이 '있다'와 '맞다'는 다르다.
        warn = ""
        for (l0, v0), (l1, v1) in zip(vals, vals[1:]):
            if v0 > 0 and abs(v1 / v0 - 1) > _JUMP:
                warn = f"  ⚠️급변 {l0}→{l1} {v1/v0:.1f}배"
                jumpy.append(f"{code} {nm}{warn}")
                break
        # 형식이 분기마다 갈리면 표가 바뀐 것 — 값 신뢰도가 떨어진다.
        if len(forms) > 1:
            warn += f"  ⚠️형식혼재 {'/'.join(sorted(forms))}"
        tag = "✅" if got_n == len(rows) else ("◐" if got_n else "❌")
        (full if got_n == len(rows) else partial if got_n else empty).append(
            f"{code} {nm}")
        print(f"[{i:2d}/{len(items)}] {code} {nm:16s} {tag}{got_n}/{len(rows)}  "
              + " · ".join(cells) + warn, flush=True)
    print("=" * 96, flush=True)
    print(f"■ 요약  ✅전분기 {len(full)} · ◐일부 {len(partial)} · ❌전무 {len(empty)}")
    for label, group in (("◐ 일부 빈칸", partial), ("❌ 전무", empty),
                         ("⚠️ 분기간 급변(파싱 의심)", jumpy)):
        if group:
            print(f"  {label}: " + " · ".join(group))
    return 0


def main(argv: list[str]) -> int:
    # ⚠️ **파이프로 태우면 stdout 이 블록 버퍼링된다.** 이 스크립트들은
    # 수십 분 도는 진단이라 `| tee` 로 받는 게 정상 사용인데, 그러면 버퍼가
    # 찰 때까지 아무것도 안 보이고 Ctrl-C 하면 **결과가 통째로 사라진다**
    # (사용자 2026-08-18 실측). 라인 버퍼링으로 되돌린다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    if len(argv) > 2 and argv[1] == "--ticker":
        return per_quarter(argv[2])
    if len(argv) > 2 and argv[1] == "--doc":
        return doc_probe(argv[2])
    if len(argv) > 4 and argv[1] == "--list":
        return list_probe(argv[2], argv[3], argv[4])
    if len(argv) > 1 and argv[1] == "--sweep":
        return sweep(argv[2:])
    return summarize()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
