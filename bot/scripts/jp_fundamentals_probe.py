"""일본 종목 **재무 원천 실측** — 밴드차트·분기실적이 왜 비는지 가른다.

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
  ④ 현재 밴드 경로가 실제로 무엇을 돌려주나(`per_band.for_ticker` 사유)

사용법(VM):
    cd ~/stock && .venv/bin/python -m bot.scripts.jp_fundamentals_probe \
        --tickers 9984.T,7203.T,6758.T | tee /tmp/jp_probe.txt
⚠️ `| tail` 은 프로세스가 끝나야 출력한다 — `tee` 를 쓸 것(#103).
"""

from __future__ import annotations

import argparse
import sys

_PROBE_VER = 1
_DEFAULT = "9984.T,7203.T,6758.T,8035.T"


def _banner() -> None:
    """인터프리터·의존성을 먼저 찍는다 — venv 밖에서 돌면 결과가 통째로
    거짓이 된다(#132: 대조 0건인데 ✅ 를 찍은 사고)."""
    print(f"jp_fundamentals_probe v{_PROBE_VER}")
    print(f"  인터프리터: {sys.executable}")
    for mod in ("yfinance", "requests"):
        try:
            __import__(mod)
            print(f"  {mod}: OK")
        except Exception as exc:                               # noqa: BLE001
            print(f"  {mod}: 없음 ({exc}) — 이 실행의 판정은 무효")


def _yf_cols(ticker: str, attr: str):
    """(열 수, EPS 행) 또는 (None, None) = 조회 자체가 실패."""
    try:
        import yfinance as yf
        df = getattr(yf.Ticker(ticker), attr)
    except Exception:                                          # noqa: BLE001
        return None, None
    if df is None or getattr(df, "empty", True):
        return 0, []
    return len(df.columns), [str(i) for i in df.index if "EPS" in str(i)]


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
        n, eps = _yf_cols(ticker, attr)
        if n is None:
            out.append(f"  ① yfinance {label}: 조회 예외 — 판정 불가")
        elif n == 0:
            out.append(f"  ① yfinance {label}: 0열 — "
                       + ("**원천 미제공**(대조군 AAPL 은 정상)" if ok
                          else "⚠️ **판정 불가**(대조군 AAPL 도 0열 "
                               "= 네트워크 차단, #86)"))
        else:
            out.append(f"  ① yfinance {label}: {n}열 · EPS 행 "
                       f"{eps or '**없음**'}")
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
    """지금 화면이 타는 그 경로를 그대로 부른다 — 프로브가 다른 경로를 보면
    통계가 거짓이 된다(#35)."""
    try:
        from bot.per_band import for_ticker
        tbl, why = for_ticker(ticker)
    except Exception as exc:                                   # noqa: BLE001
        return [f"  ④ per_band.for_ticker: 예외 {exc}"]
    if tbl:
        return [f"  ④ per_band: basis={tbl.get('basis')} 관측 {tbl.get('n')}개"
                f" · 밴드 {tbl.get('band_n')}개"]
    return [f"  ④ per_band: 없음 — 사유: {why}"]


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
        for line in (_yf_shape(tk) + _edinet_key() + _kabutan(tk)
                     + _current_path(tk)):
            print(line)
        print()
    print("판정 기준: ① 0열 **이면서 대조군(AAPL)이 정상**일 때만 '원천 미제공'"
          " 이다 — 대조군도 0열이면 네트워크 차단이라 아무 결론도 못 낸다(#86).")
    print("           ②③ 중 하나라도 살아 있으면 **대안 경로가 있다**"
          "(EDINET XBRL 또는 Kabutan 業績).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
