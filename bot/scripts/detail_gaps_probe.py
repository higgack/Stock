#!/usr/bin/env python3
"""종목 상세 빈칸 진단 + 수주잔고·재고자산 가용성 프로브 (읽기 전용·₩0).

사용자 2026-08-16 4문항 확인용:
  ① 종합탭 '투자정보 요약' / 밸류에이션 탭 멀티플이 왜 '—' 인가
     → yfinance .info 가 실제로 그 키를 주는지 실측(샌드박스는 Yahoo 403).
  ② 시가총액이 어느 소스에서 오는지 + 네이버 실시간과 얼마나 다른지
  ③ DART 재무 API 가 '재고자산' 을 주는지(계정명 그대로 확인)
  ④ 정기보고서 원문에 '수주잔고' 표가 있는지 + 파싱 가능한 형태인지

실행 (⚠️ **venv 파이썬**으로 — 시스템 python3 는 yfinance 도 .env 도 없다):
    cd ~/stock && .venv/bin/python -m bot.scripts.detail_gaps_probe 039030.KQ
    (여러 개도 가능: ... 039030.KQ 005930.KS 042700.KS)

출력은 그대로 복사해서 붙여넣어 주시면 됩니다. 키·비밀값은 찍지 않습니다.
"""
from __future__ import annotations

import sys

# 종합탭·밸류에이션 탭이 읽는 키 전량 — 화면 항목과 1:1.
_INFO_KEYS = [
    "marketCap", "currentPrice", "regularMarketPrice", "currency",
    "financialCurrency", "sharesOutstanding",
    "trailingPE", "forwardPE", "priceToBook",
    "priceToSalesTrailing12Months", "enterpriseToEbitda",
    "trailingEps", "forwardEps", "bookValue",
    "dividendYield", "dividendRate", "beta",
    "fiftyDayAverage", "twoHundredDayAverage",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationKey",
]

# 수주잔고 표 헤더 후보 — 사업/분기보고서 「매출 및 수주상황」 표에 쓰이는 표현.
_BACKLOG_KW = ("수주잔고", "수주잔액", "수주총액", "수주상황", "계약잔액", "수주액")
_INV_KW = ("재고자산", "재고자산(순액)", "재고자산 순액")


def _fmt(v):
    if v is None:
        return "None"
    if isinstance(v, float):
        return f"{v:,.4f}"
    if isinstance(v, int):
        return f"{v:,}"
    return repr(v)[:80]


def probe_yfinance(ticker: str) -> None:
    print("── ① yfinance .info (종합·밸류에이션 탭 소스) " + "─" * 24)
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        print(f"  ❌ .info 실패: {exc}")
        return
    if not info:
        print("  ❌ .info 가 빈 dict — throttle 이면 잠시 후 재시도")
        return
    missing = []
    for k in _INFO_KEYS:
        v = info.get(k)
        mark = "  " if v is not None else "❌"
        if v is None:
            missing.append(k)
        print(f"  {mark} {k:34s} = {_fmt(v)}")
    print(f"  → 전체 {len(info)}개 키 중 화면용 {len(_INFO_KEYS)}개 검사, "
          f"결측 {len(missing)}개: {', '.join(missing) or '없음'}")
    # 결측을 파생으로 메울 수 있는지 — 시총·주식수·순이익·자본총계가 있으면 가능.
    sh, mc = info.get("sharesOutstanding"), info.get("marketCap")
    print(f"  → 파생 가능성: sharesOutstanding={_fmt(sh)} marketCap={_fmt(mc)}"
          f" (둘 다 있으면 DART 순이익·자본총계로 EPS/BPS/PER/PBR 산출 가능)")


def probe_market_cap(ticker: str) -> None:
    print("── ② 시가총액 소스 비교 (스냅샷 vs 네이버 실시간) " + "─" * 15)
    yv = None
    try:
        import yfinance as yf
        yv = (yf.Ticker(ticker).info or {}).get("marketCap")
    except Exception as exc:
        print(f"  yfinance marketCap 실패: {exc}")
    print(f"  yfinance marketCap = {_fmt(yv)}")
    if ticker.upper().endswith((".KS", ".KQ")):
        try:
            from bot.naver_quote import fetch_kr_quote
            nq = fetch_kr_quote(ticker) or {}
            print(f"  네이버 price={_fmt(nq.get('price'))} "
                  f"mcap={_fmt(nq.get('mcap'))}")
            if yv and nq.get("mcap"):
                d = (nq["mcap"] / yv - 1) * 100
                print(f"  → 차이 {d:+.2f}% "
                      f"({'네이버가 신선' if abs(d) > 0.5 else '거의 동일'})")
        except Exception as exc:
            print(f"  네이버 조회 실패: {exc}")
    else:
        print("  (KR 이 아니라 네이버 국내 경로 없음)")


def probe_dart_accounts(ticker: str) -> None:
    print("── ③ DART 재무 API — 재고자산 계정 존재 여부 " + "─" * 20)
    if not ticker.upper().endswith((".KS", ".KQ")):
        print("  (KR 아님 — 건너뜀)")
        return
    try:
        from bot.dart_client import get_dart
        dart = get_dart()
        if not dart:
            print("  ❌ DART_API_KEY 없음")
            return
        code = dart.stock_code_to_corp_code(ticker)
        if not code:
            print("  ❌ corp_code 미확인")
            return
    except Exception as exc:
        print(f"  ❌ DART 초기화 실패: {exc}")
        return
    import datetime as _dt

    import requests
    year = _dt.date.today().year
    hits = []
    for yr, reprt in ((year, "11014"), (year, "11012"), (year - 1, "11011")):
        try:
            r = requests.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": dart.api_key, "corp_code": code,
                        "bsns_year": str(yr), "reprt_code": reprt,
                        "fs_div": "CFS"}, timeout=20)
            js = r.json()
        except Exception as exc:
            print(f"  {yr}/{reprt}: 요청 실패 {exc}")
            continue
        if js.get("status") != "000":
            print(f"  {yr}/{reprt}: status={js.get('status')} "
                  f"{js.get('message', '')[:40]}")
            continue
        items = js.get("list") or []
        found = [(i.get("account_id"), i.get("account_nm"),
                  i.get("thstrm_amount"), i.get("sj_div"))
                 for i in items
                 if any(k in (i.get("account_nm") or "") for k in _INV_KW)
                 or "Inventor" in (i.get("account_id") or "")]
        print(f"  {yr}/{reprt}: 전체 {len(items)}계정 · 재고자산 매칭 {len(found)}건")
        for aid, anm, amt, sj in found[:6]:
            print(f"      [{sj}] {anm} | id={aid} | {amt}")
        if found:
            hits.append((yr, reprt))
        if len(hits) >= 2:
            break
    print(f"  → 재고자산 확보 가능: {'예' if hits else '아니오'}")


def probe_backlog(ticker: str) -> None:
    print("── ④ 정기보고서 원문 — 수주잔고 표 존재/형태 " + "─" * 20)
    if not ticker.upper().endswith((".KS", ".KQ")):
        print("  (KR 아님 — 건너뜀)")
        return
    try:
        import datetime as _dt

        from bot.dart_client import get_dart
        from bot.dart_feed import _fetch_doc_text
        dart = get_dart()
        if not dart:
            print("  ❌ DART_API_KEY 없음")
            return
        year = _dt.date.today().year
        rep = None
        for yr, reprt in ((year, "11012"), (year, "11014"),
                          (year - 1, "11011"), (year, "11013")):
            rep = dart.find_periodic_report(ticker, yr, reprt)
            if rep and rep.get("rcept_no"):
                print(f"  보고서: {yr}/{reprt} rcept_no={rep['rcept_no']} "
                      f"{rep.get('report_nm', '')}")
                break
        if not rep or not rep.get("rcept_no"):
            print("  ❌ 정기보고서 미확인")
            return
        text = _fetch_doc_text(rep["rcept_no"], dart.api_key)
        if not text:
            print("  ❌ 원문 수신 실패")
            return
        print(f"  원문 길이: {len(text):,}자")
        for kw in _BACKLOG_KW:
            n = text.count(kw)
            print(f"    '{kw}' {n}회")
            if n:
                i = text.find(kw)
                # 표가 태그 제거로 평문화되므로 주변을 그대로 보여준다 —
                # 파서를 짜기 전에 **실제 배열**을 눈으로 확인해야 한다.
                print(f"      … {text[max(0, i - 160):i + 420]} …")
                break
    except Exception as exc:
        print(f"  ❌ 실패: {exc}")


def probe_vkospi() -> None:
    """VKOSPI(코스피200 변동성지수) 소스 **진단**.

    후보 코드를 바꿔가며 두 번 실패했다(2026-08-16). 더 추측하지 않고
    엔드포인트 여러 형태를 실제로 때려 **status 와 응답 앞부분을 그대로**
    찍는다 — 이 출력 하나로 어떤 URL·코드가 맞는지 확정한다
    (CLAUDE.md 실수 #12: 같은 증상 2회+ = 추측 종료, 가시성 심기)."""
    print("── ⑤ VKOSPI 소스 진단 (시장타이밍 보드) " + "─" * 22)
    import datetime as _dt

    import requests
    try:
        from bot.naver_quote import _HDRS
    except Exception:
        _HDRS = {"User-Agent": "Mozilla/5.0"}
    end = _dt.date.today()
    start = end - _dt.timedelta(days=400)
    ymd = "%Y%m%d"
    cands = []
    # (a) 국내지수 차트 API — 현재 코드가 쓰는 형태
    for code in ("VKOSPI", "VKOSPI200", "KOSPI"):   # KOSPI = 대조군(형태 검증)
        cands.append((
            f"chart/domestic/index/{code}/day",
            f"https://api.stock.naver.com/chart/domestic/index/{code}/day",
            {"startDateTime": start.strftime(ymd) + "0000",
             "endDateTime": end.strftime(ymd) + "0000"}))
    # (b) 지수 basic — 현재값만이라도 잡히는지
    for code in ("VKOSPI", "VKOSPI200"):
        cands.append((f"index/{code}/basic",
                      f"https://api.stock.naver.com/index/{code}/basic", {}))
    # (c) siseJson — 많은 라이브러리가 지수 일봉에 쓰는 경로
    for sym in ("VKOSPI", "VKOSPI200"):
        cands.append((
            f"siseJson {sym}",
            "https://api.finance.naver.com/siseJson.naver",
            {"symbol": sym, "requestType": "1", "timeframe": "day",
             "startTime": start.strftime(ymd), "endTime": end.strftime(ymd)}))
    for label, url, params in cands:
        try:
            r = requests.get(url, headers=_HDRS, params=params, timeout=10)
            body = (r.text or "")[:220].replace("\n", " ")
            print(f"  [{r.status_code}] {label}")
            print(f"        {body}")
        except Exception as exc:
            print(f"  [ERR] {label}: {exc}")
    print("  → 200 이면서 숫자 시계열이 보이는 줄의 label 을 알려주시면 "
          "그대로 배선합니다.")
    print("     (KOSPI 대조군이 200 이면 URL 형태 자체는 맞고 코드만 다른 것)")


def main(argv: list[str]) -> int:
    tickers = argv[1:] or ["039030.KQ"]
    for t in tickers:
        print("=" * 74)
        print(f"■ {t}")
        print("=" * 74)
        probe_yfinance(t)
        probe_market_cap(t)
        probe_dart_accounts(t)
        probe_backlog(t)
        print()
    probe_vkospi()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
