"""**재무 원천 실측**(전 시장) — 밴드차트·분기실적이 왜 비는지 가른다.

사용자 2026-08-22 (9984.T SoftBank Group): "일본기업은 EPS 이력이 없기에
밴드차트나, 분기실적 서포트가 불가능한거야?"

⚠️ 샌드박스에서는 답할 수 없다 — Yahoo 펀더멘털도 Kabutan 도 프록시가
막는다(AAPL 조차 0열이라 '일본 데이터가 없다'는 증거가 못 된다, #86).
그래서 **VM 에서 실측**한다. 이 프로브는 값을 바꾸지 않고 **무엇이 실제로
오는지**만 찍는다.

무엇을 가르나:
  ① yfinance 분기/연간 손익 — 열 수, EPS 행 유무(현재 유일한 비-KR 경로)
  ② EDINET 자격증명 **출처**(값 금지, #23·#82) — XBRL 파싱을 붙일 수 있나
  ③ Kabutan 業績 페이지 — 접근 가능한가, 1株益(EPS) 표가 있나
  ④ 스냅샷이 실제로 EPS 를 들고 있나 + `per_band.for_ticker` 를 **스냅샷과
     함께** 불러 현재 경로의 사유(#35 — v1 은 스냅샷을 안 넘겨 세 종목이
     모두 같은 사유를 냈고, 그건 프로브가 만든 결과였다)

사용법(VM):
    cd ~/stock && .venv/bin/python -m bot.scripts.fundamentals_probe \
        --tickers 0700.HK,0522.HK,0002.HK | tee /tmp/fund_probe.txt
⚠️ `| tail` 은 프로세스가 끝나야 출력한다 — `tee` 를 쓸 것(#103).
"""

from __future__ import annotations

import argparse
import sys

_PROBE_VER = 4
_DEFAULT = "0700.HK,0002.HK,600519.SS,000002.SZ"


def _banner() -> None:
    """인터프리터·의존성을 먼저 찍는다 — venv 밖에서 돌면 결과가 통째로
    거짓이 된다(#132: 대조 0건인데 ✅ 를 찍은 사고)."""
    print(f"fundamentals_probe v{_PROBE_VER}")
    print(f"  인터프리터: {sys.executable}")
    for mod in ("yfinance", "requests"):
        try:
            __import__(mod)
            print(f"  {mod}: OK")
        except Exception as exc:                               # noqa: BLE001
            print(f"  {mod}: 없음 ({exc}) — 이 실행의 판정은 무효")


# 분기실적 표가 실제로 쓰는 항목 — `quarterly_series._ITEM_CANDIDATES` 와
# 같은 이름이라야 "원천이 없다"와 "우리 매핑이 못 잡는다"를 가를 수 있다.
_WATCH = ("Total Revenue", "Operating Revenue", "Operating Income", "EBIT",
          "Net Income", "Diluted EPS", "Basic EPS")


def _yf_cols(ticker: str, attr: str):
    """(열 수, {항목: 값이 있는 열 수}) 또는 (None, None) = 조회 자체가 실패.

    ⚠️ 열 수만 세면 "5열인데 표가 비었다"의 원인을 못 가른다 — 열은 있는데
    **우리가 찾는 항목 이름이 없는** 경우가 실재한다(2026-08-22 HK). 항목별로
    센다.
    """
    try:
        import yfinance as yf
        df = getattr(yf.Ticker(ticker), attr)
    except Exception:                                          # noqa: BLE001
        return None, None
    if df is None or getattr(df, "empty", True):
        return 0, {}
    filled = {}
    idx = {str(i) for i in df.index}
    for item in _WATCH:
        if item not in idx:
            continue
        n = 0
        for c in df.columns:
            try:
                v = df.at[item, c]
            except Exception:                                  # noqa: BLE001
                continue
            if v is not None and str(v) != "nan":
                n += 1
        filled[item] = n
    return len(df.columns), filled


def _series_shape(ticker: str) -> list[str]:
    """분기실적 표가 **실제로 쓰는 경로**를 그대로 태운다(#35).

    화면은 스냅샷 → `series_from_yfinance` → 표 순으로 간다. 여기서 끊기면
    원천이 아니라 우리 쪽이다.
    """
    try:
        from bot.quarterly_series import missing_quarters, series_from_yfinance
        from bot.stock_snapshot import collect_stock_snapshot
        snap = collect_stock_snapshot(ticker)
    except Exception as exc:                                   # noqa: BLE001
        return [f"  ③ 분기 시리즈: 준비 실패 {exc}"]
    raw = (((snap or {}).get("financials") or {})
           .get("income_statement") or {}).get("quarterly") or []
    qs = series_from_yfinance(snap, n=5)
    if not qs:
        return [f"  ③ 분기 시리즈: **없음**(스냅샷 분기 행 {len(raw)}개)"
                " — 행이 있는데 없으면 우리 항목 매핑 문제다"]
    out = [f"  ③ 분기 시리즈: {len(qs)}개 {[q.get('label') for q in qs]}"]
    for k in ("매출", "영업이익", "당기순이익"):
        got = [q.get("label") for q in qs
               if (q.get("financials") or {}).get(k) is not None]
        out.append(f"     {k}: {len(got)}/{len(qs)} {got}")
    gaps = missing_quarters(qs)
    out.append(f"     달력 결측: {gaps or '없음'}")
    return out


def control_ok(control: str = "AAPL") -> bool:
    """⚠️ **대조군**. 네트워크가 막히면 yfinance 는 예외 대신 빈 프레임을
    돌려준다 — 그걸 '원천 미제공'으로 읽으면 내 도구가 거짓 진단을 낸다
    (샌드박스 실측: AAPL 도 0열이었다, #86). 대조군이 0열이면 이 실행은
    **판정 불가**다."""
    n, _eps = _yf_cols(control, "quarterly_income_stmt")
    return bool(n)


def _yf_shape(ticker: str) -> list[str]:
    out = []
    ok = control_ok()
    for label, attr in (("분기", "quarterly_income_stmt"),
                        ("연간", "income_stmt")):
        n, filled = _yf_cols(ticker, attr)
        if n is None:
            out.append(f"  ① yfinance {label}: 조회 예외 — 판정 불가")
        elif n == 0:
            out.append(f"  ① yfinance {label}: 0열 — "
                       + ("**원천 미제공**(대조군 AAPL 은 정상)" if ok
                          else "⚠️ **판정 불가**(대조군 AAPL 도 0열 "
                               "= 네트워크 차단, #86)"))
        else:
            out.append(f"  ① yfinance {label}: {n}열")
            out.append(f"     항목별 채움: {filled or '**해당 항목 전무**'}")
    return out


def _alt_sources(ticker: str) -> list[str]:
    """시장별 **대안 원천**. 이름이 내용과 맞아야 한다 — JP 전용 섹션을 HK
    종목에 찍으면 화면이 거짓말한다(#34)."""
    t = (ticker or "").upper()
    if t.endswith(".T"):
        return _edinet_key() + _kabutan(t)
    if t.endswith((".HK", ".SS", ".SZ")):
        return _akshare_valuation(t)
    if t.endswith((".TW", ".TWO")):
        try:
            from bot.finmind_client import fetch_income_statement
            rows = fetch_income_statement(t, quarters=8) or []
            return [f"  ② FinMind 분기 손익: {len(rows)}행"]
        except Exception as exc:                               # noqa: BLE001
            return [f"  ② FinMind: 실패 {exc}"]
    return ["  ② 대안 원천: 이 시장은 등록된 게 없다(yfinance 단일 경로)"]


# HK 밸류에이션 이력 후보 — AKShare 버전마다 이름이 달라 **설치본에 뭐가
# 있는지** 실측한다. 이름을 추측해 파서를 짜면 이 레포에서 반복 실패한다.
_HK_VAL_FNS = ("stock_hk_valuation_baidu", "stock_hk_indicator_eniu",
               "stock_hk_index_daily_em")


def _akshare_valuation(ticker: str) -> list[str]:
    """CN 은 이미 배선된 경로를, HK 는 **설치본이 뭘 제공하는지**를 찍는다."""
    out = []
    try:
        import akshare as ak
    except Exception as exc:                                   # noqa: BLE001
        return [f"  ② AKShare: 없음({exc}) — HK/CN 대안 경로 불가"]
    out.append(f"  ② AKShare: 설치됨(v{getattr(ak, '__version__', '?')})")
    if ticker.endswith((".SS", ".SZ")):
        try:
            from bot.akshare_client import get_akshare
            rows = get_akshare().per_history(ticker, years=10)
            out.append(f"     CN 일별 PER 이력: {len(rows)}행"
                       + (f" ({rows[0][0]} ~ {rows[-1][0]})" if rows else
                          " — **배선돼 있으나 비었다**"))
        except Exception as exc:                               # noqa: BLE001
            out.append(f"     CN 일별 PER 이력: 실패 {exc}")
        return out
    # HK — `stock_a_indicator_lg` 커버리지 밖이다. 설치본의 후보 함수를 훑는다.
    have = [f for f in _HK_VAL_FNS if hasattr(ak, f)]
    out.append(f"     HK 밸류에이션 후보 함수: {have or '**없음**'}")
    code = ticker.split(".")[0].zfill(5)
    for fn in have[:1]:                       # 첫 후보만 — 느린 호출 방지
        try:
            df = getattr(ak, fn)(symbol=code)
            n = 0 if df is None else len(df)
            cols = [] if df is None else list(df.columns)[:8]
            out.append(f"     {fn}({code}) → {n}행 · 컬럼 {cols}")
        except Exception as exc:                               # noqa: BLE001
            out.append(f"     {fn}({code}) → 실패 {exc}")
    return out


def _edinet_key() -> list[str]:
    """자격증명은 **출처와 길이만** — 값은 절대 안 찍는다(§Secrets)."""
    try:
        from bot.env_keys import env_key, env_source, env_why
    except Exception as exc:                                   # noqa: BLE001
        return [f"  ② EDINET_API_KEY: 확인 실패 {exc}"]
    v = env_key("EDINET_API_KEY")
    if v:
        # ⚠️ 값은 절대 안 찍는다 — 길이까지만(빈값·오타 판별에 필요, #82).
        return [f"  ② EDINET_API_KEY: 있음(출처 {env_source('EDINET_API_KEY')}"
                f" · 길이 {len(v)}) → EDINET XBRL 경로를 붙일 수 있다"]
    return [f"  ② EDINET_API_KEY: **없음**({env_why('EDINET_API_KEY')})"
            " — EDINET XBRL 경로 불가"]


def _kabutan(ticker: str) -> list[str]:
    code = ticker.split(".")[0]
    url = f"https://kabutan.jp/stock/finance?code={code}"
    try:
        import requests
        r = requests.get(url, timeout=15, headers={
            "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                           " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")})
    except Exception as exc:                                   # noqa: BLE001
        return [f"  ③ Kabutan: 접근 실패 {exc}"]
    if r.status_code != 200:
        return [f"  ③ Kabutan: HTTP {r.status_code}"]
    t = r.text
    marks = {k: t.count(k) for k in ("1株益", "１株益", "業績", "決算期")}
    return [f"  ③ Kabutan: HTTP 200 · {len(t):,}바이트 · 마커 {marks}"]


def _current_path(ticker: str) -> list[str]:
    """지금 화면이 타는 그 경로를 **같은 인자로** 부른다.

    ⚠️ v1 은 `for_ticker(ticker)` 를 스냅샷 **없이** 불렀다 — 비-KR EPS 는
    스냅샷에서 꺼내므로 세 종목 모두 똑같이 'EPS 이력이 부족' 이 나왔고,
    그건 원천이 아니라 **프로브가 만든 결과**였다(#35: 경로만 같고 인자가
    다르면 통계가 거짓이 된다). 화면은 `collect_stock_snapshot` 을 넘긴다.
    """
    out, snap = [], None
    try:
        from bot.stock_snapshot import collect_stock_snapshot
        snap = collect_stock_snapshot(ticker)
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"  ④ 스냅샷 실패: {exc}")
    # 스냅샷이 실제로 EPS 를 들고 있나 — 여기서 끊기면 원천 문제가 아니다
    try:
        from bot.per_band import _eps_rows_from_snapshot as _eps
        for kind in ("quarterly", "annual"):
            rows = _eps(snap, kind)
            pos = [v for _p, v in rows if isinstance(v, (int, float)) and v > 0]
            out.append(f"  ④ 스냅샷 {kind} EPS: {len(rows)}개"
                       f"(양수 {len(pos)}개) {[p for p, _v in rows][:6]}")
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"  ④ 스냅샷 EPS 확인 실패: {exc}")
    try:
        from bot.per_band import for_ticker
        tbl, why = for_ticker(ticker, snap)
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"  ④ per_band 호출 예외: {exc}")
        return out
    if tbl:
        out.append(f"  ④ per_band: basis={tbl.get('basis')}"
                   f" 관측 {tbl.get('n')}개 · 밴드 {tbl.get('band_n')}개")
    else:
        out.append(f"  ④ per_band: 없음 — 사유: {why}")
    return out


def main() -> int:
    from bot.scripts.probe_progress import stream_stdout
    stream_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=_DEFAULT)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if args.limit:
        tickers = tickers[:args.limit]
    _banner()
    print(f"  대상 {len(tickers)}종목 · 종목당 ~10초 예상\n")
    for i, tk in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {tk}")
        for line in (_yf_shape(tk) + _alt_sources(tk)
                     + _series_shape(tk) + _current_path(tk)):
            print(line)
        print()
    print("판정 기준:")
    print(" ① 0열 **이면서 대조군(AAPL)이 정상**일 때만 '원천 미제공' 이다 —"
          " 대조군도 0열이면 네트워크 차단이라 아무 결론도 못 낸다(#86).")
    print(" ① 열은 있는데 '항목별 채움' 이 비면 **원천이 그 항목을 안 주는 것**"
          "이고, 있는데 ③ 이 비면 **우리 매핑 문제**다 — 화면엔 둘 다 '없음'"
          "으로 보인다.")
    print(" ② 대안 원천이 살아 있으면 yfinance 한계를 넘을 길이 있다는 뜻이다.")
    print(" ③ 달력 결측이 있으면 TTM 은 정상적으로 안 만들어진다(연속 4분기 규칙).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
