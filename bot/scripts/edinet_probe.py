"""**EDINET XBRL(CSV) 실측** — 일본 연차 재무 이력이 실제로 오는지 가른다.

⚠️ 샌드박스에서는 EDINET 이 프록시에 막혀 한 글자도 못 받는다 — 그래서
파서를 쓰기 전에 **원문 구조를 눈으로 봐야** 한다(#109 '커버리지 진단은
처음부터 표본 원문을 같이 찍을 것'). 이 프로브는 값을 바꾸지 않고 다음을
**단계별로** 찍는다 — '없음' 한 단어는 원인이 셋이라 아무것도 못 가른다(#149):

  ① 자격증명 — 출처·길이만(값 금지, §Secrets · #23·#82)
  ② 문서 탐색 — 유가증권보고서(120)를 며칠 훑어 몇 건 찾았나, 결산일은 있나
  ③ ZIP 구조 — 내부 파일 목록, CSV 인코딩, 헤더 컬럼
  ④ 요소 매칭 — 우리가 아는 요소 ID 가 몇 건 잡혔나 · **못 잡은 것 중
     「主要な経営指標」류 후보를 원문 그대로** 찍는다(이름을 추측해 늘리면
     #24 가 재발한다 — 늘릴 땐 이 출력에서 복사한다)
  ⑤ 산출 — `summary_series` 가 낸 연차 시계열 + `per_band` 가 실제로 그
     경로를 탔는지(basis == "edinet")

사용법(VM):
    cd ~/stock && .venv/bin/python -m bot.scripts.edinet_probe \
        --tickers 7203.T,6758.T,9984.T | tee /tmp/edinet_probe.txt
⚠️ `| tail` 은 프로세스가 끝나야 출력한다 — `tee` 를 쓸 것(#103).
"""

from __future__ import annotations

import argparse
import sys
import time

_PROBE_VER = 5
_DEFAULT = "7203.T,6758.T,9984.T"
# 한 종목에 문서 탐색(최대 430일) + ZIP 2건 — 넉넉히 잡는다.
_EST_S_PER_TICKER = 90


def _banner() -> None:
    """인터프리터·의존성 먼저 — venv 밖에서 돌면 판정이 통째로 거짓이다(#132)."""
    from bot.edinet_xbrl import _parse_sig
    print(f"edinet_probe v{_PROBE_VER} · 파서 지문 {_parse_sig()}")
    print(f"  인터프리터: {sys.executable}")
    for mod in ("requests",):
        try:
            __import__(mod)
            print(f"  {mod}: OK")
        except Exception as exc:                               # noqa: BLE001
            print(f"  {mod}: 없음 ({exc}) — 이 실행의 판정은 무효")
            raise SystemExit(2)


def _credentials() -> str:
    """값은 절대 안 찍는다 — **출처와 길이**까지만(§Secrets · #82)."""
    try:
        from bot.env_keys import env_key, env_source, env_why
    except Exception as exc:                                   # noqa: BLE001
        return f"① 자격증명: env_keys 로드 실패 {exc}"
    k = env_key("EDINET_API_KEY") or ""
    src = env_source("EDINET_API_KEY")
    line = f"① EDINET_API_KEY: 출처={src or '없음'} 길이={len(k)}"
    if not k:
        line += f" · 사유={env_why('EDINET_API_KEY')}"
    return line


def _unmatched_summary_ids(rows: list[dict], known: set[str]) -> list[str]:
    """못 잡은 요소 중 「主要な経営指標」절 후보를 **원문 그대로** 모은다.

    ⚠️ 이름을 추측해 매핑을 늘리면 이 레포에서 반복 실패한다(#24·#73) —
    실제 문서가 쓴 ID 를 보고 복사해 오게 한다.
    """
    seen: dict[str, str] = {}
    for r in rows:
        eid = (r.get("要素ID") or "").strip()
        if not eid or eid in known:
            continue
        if "SummaryOfBusinessResults" not in eid:
            continue
        seen.setdefault(eid, (r.get("項目名") or "").strip())
    return [f"{k}  ({v})" for k, v in sorted(seen.items())]


def _render_unmatched(miss: list[str]) -> list[str]:
    """못 잡은 요소 ID 를 **전부** 찍는다.

    ⚠️ 자르지 않는다 — 2026-08-22 실측에서 25종에서 잘려 IFRS 매출 요소 ID 가
    '외 2종' 안에 숨었고, 그래서 매핑을 못 넓혔다. 이 목록이 매핑을 넓히는
    **유일한 근거**다(이름을 추측하지 않기 위해, #24·#73).
    """
    return [f"     ❓ {m}" for m in miss]


def _why_no_doc(ticker: str) -> list[str]:
    """0건일 때 **탐색 실패인지 원천 부재인지** 가른다(#143 대조군 규율).

    2026-08-22 6758.T(소니)가 200일을 훑고도 0건이었다 — '없다'고만 말하면
    다음 라운드를 통째로 낭비한다. 캐시된 일별 목록을 훑어 ① 그 날들에 문서가
    몇 건이나 들어 있나(0 이면 목록이 비어 캐시된 것) ② 이 티커의 문서가
    **어떤 docTypeCode 로** 있나 ③ 앞 4자리가 같은 secCode 가 실제로 뭔가를
    있는 그대로 찍는다.
    """
    import datetime as _dt
    from bot.edinet_client import _sec_code_for, get_edinet
    sec4 = (_sec_code_for(ticker) or "")[:4]
    cl = get_edinet()
    out = [f"     ↳ 진단: secCode 앞 4자리 = {sec4 or '**미상**'}"]
    if not sec4:
        return out
    today = _dt.date.today()
    total, empty_days, seen_types, codes = 0, 0, {}, set()
    for off in range(201):
        docs = cl._fetch_day(today - _dt.timedelta(days=off))
        total += len(docs)
        if not docs:
            empty_days += 1
        for d in docs:
            code = str(d.get("secCode") or "")
            if code[:4] != sec4:
                continue
            codes.add(code)
            t = str(d.get("docTypeCode") or "?")
            seen_types[t] = seen_types.get(t, 0) + 1
    out.append(f"     ↳ 최근 201일 목록: 문서 {total:,}건 · **빈 날 "
               f"{empty_days}일**(빈 날이 많으면 목록이 빈 채로 캐시된 것)")
    out.append(f"     ↳ 이 티커 문서: {seen_types or '**0건**'} "
               f"· 실제 secCode {sorted(codes) or '없음'}")
    if seen_types and "120" not in seen_types:
        out.append("     ↳ → 문서는 있는데 **유가증권보고서(120)가 없다** — "
                   "결산월/제출시기 문제이지 탐색 실패가 아니다")
    elif not seen_types and total:
        out.append("     ↳ → 목록은 채워져 있는데 이 회사 문서가 하나도 없다 "
                   "— secCode 매칭 또는 제출시기를 의심")
    return out


def probe_one(ticker: str, api_key: str) -> list[str]:
    """한 종목 — 단계별로 찍는다. 예외는 삼키지 않고 **사유째로** 찍는다."""
    from bot import edinet_xbrl as ex
    out = [f"── {ticker} " + "─" * 40]
    t0 = time.time()
    try:
        docs = ex.find_annual_docs(ticker, api_key, max_docs=2,
                                   progress=lambda m: print(m, flush=True))
    except Exception as exc:                                   # noqa: BLE001
        return out + [f"  ② 문서 탐색 실패: {type(exc).__name__} {exc}"]
    out.append(f"  ② 유가증권보고서(120): {len(docs)}건 "
               f"({time.time() - t0:.1f}초)")
    for d in docs:
        out.append(f"     {d['submitted']} docID={d['doc_id']} "
                   f"결산일={d['period_end'] or '**미상**'} {d['filer']}")
    if not docs:
        return out + _why_no_doc(ticker)

    doc = docs[0]
    # ⚠️ 여기서부터 진행 출력이 없으면 "멈췄다"로 보인다(#103) — 단계마다 찍는다.
    print(f"  … ③ CSV 내려받는 중 docID={doc['doc_id']} (수 MB, 30초+ 걸릴 수 "
          f"있음)", flush=True)
    _t = time.time()
    try:
        rows = ex.fetch_doc_csv(doc["doc_id"], api_key)
    except Exception as exc:                                   # noqa: BLE001
        return out + [f"  ③ CSV 실패: {type(exc).__name__} {exc}"]
    print(f"  … ③ CSV 완료 ({time.time() - _t:.1f}초)", flush=True)
    out.append(f"  ③ CSV 행: {len(rows)}행")
    if rows:
        out.append(f"     컬럼: {list(rows[0].keys())}")
    else:
        return out + ["     → **0행**. ZIP 에 CSV 가 없거나 인코딩이 바뀌었다 "
                      "— 대조 대상이 0건이면 통과가 아니라 실패다(#54)."]

    known = {e for ids in ex.SUMMARY_ELEMENTS.values() for e in ids}
    hit = {}
    for r in rows:
        eid = (r.get("要素ID") or "").strip()
        if eid in known:
            hit[eid] = hit.get(eid, 0) + 1
    out.append(f"  ④ 아는 요소 매칭: {len(hit)}종")
    for eid, n in sorted(hit.items()):
        out.append(f"     ✅ {eid} × {n}")
    if not hit:
        # ⚠️ 매칭 0종이면 **파서가 눈이 먼 것**일 수 있다(2026-08-22 실측:
        # 필드가 큰따옴표로 감싸져 와서 컬럼 키가 통째로 어긋났다). '원천에
        # 없음'과 구별되게 **원문을 그대로** 찍는다 — 대조 0건은 통과가
        # 아니라 실패다(#54·#109).
        out.append("     ❌ **아는 요소가 하나도 안 잡혔다** — 원문 표본:")
        for r in rows[:3]:
            out.append(f"        {dict(list(r.items())[:4])}")
    miss = _unmatched_summary_ids(rows, known)
    out.append(f"     못 잡은 「主要な経営指標」요소: {len(miss)}종")
    out.extend(_render_unmatched(miss))

    bas: dict[str, int] = {}
    for r in rows:
        if (r.get("要素ID") or "").strip() in known:
            k = ex._basis_of(r)
            bas[k] = bas.get(k, 0) + 1
    out.append(f"     連結/個別 분포: {bas or '**판정 불가**'}")
    ser = ex.summary_series(rows, (doc.get("period_end") or "")[:10])
    out.append(f"  ⑤ summary_series: {len(ser)}기"
               + (f" · 기준={ser[0].get('basis')}" if ser else ""))
    for rec in ser:
        out.append("     " + rec["period"] + " " + " · ".join(
            f"{k}={rec[k]:,.2f}" for k in
            ("revenue", "net_income", "eps", "bps", "equity")
            if isinstance(rec.get(k), (int, float))))
    print("  … ⑤ 두 번째 문서(1년 전 창) 탐색 중", flush=True)
    eps = ex.eps_rows(ticker, years=10, api_key=api_key, wait=True)
    out.append(f"     EPS 행: {len(eps)}개"
               + (f" ({eps[0][0]} ~ {eps[-1][0]})" if eps else " — **없음**"))

    # 화면이 쓰는 그 경로를 그대로 태운다(#35) — 스냅샷도 같이 넘긴다(#145).
    print("  … ⑥ 스냅샷 수집 + per_band (1분 이상 걸릴 수 있음)", flush=True)
    try:
        from bot.per_band import for_ticker
        from bot.stock_snapshot import collect_stock_snapshot
        snap = collect_stock_snapshot(ticker) or {}
        tbl, why = for_ticker(ticker, snap)
        out.append(f"  ⑥ per_band: "
                   + (f"basis={tbl.get('basis')} 관측 {tbl.get('n')}개"
                      if tbl else f"없음 — {why}"))
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"  ⑥ per_band 실패: {type(exc).__name__} {exc}")
    return out


def main() -> None:
    from bot.scripts.probe_progress import fmt_eta, stream_stdout
    stream_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=_DEFAULT)
    ap.add_argument("--limit", type=int, default=0,
                    help="앞에서 N종목만(0=전부)")
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if args.limit:
        tickers = tickers[:args.limit]
    _banner()
    print(_credentials())
    print(f"  대상 {len(tickers)}종목 · 예상 "
          f"{len(tickers) * _EST_S_PER_TICKER / 60:.1f}분")
    from bot.env_keys import env_key
    key = (env_key("EDINET_API_KEY") or "").strip()
    if not key:
        print("→ 키가 없어 **판정 불가**(원천 부재가 아니다, #143)")
        raise SystemExit(2)
    t0 = time.time()
    for i, t in enumerate(tickers):
        print(fmt_eta(i, len(tickers), t0), flush=True)
        for ln in probe_one(t, key):
            print(ln, flush=True)
    print(fmt_eta(len(tickers), len(tickers), t0))


if __name__ == "__main__":
    main()
