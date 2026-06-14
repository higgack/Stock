"""야후/네이버 데이터 소스 헬스체크 — `python -m bot.source_health`.

각 소스를 직접 1콜씩 테스트해 OK/FAIL + 샘플을 출력(사용자 2026-06-14 '잘 받아
오는지 검토하는 코드'). 정지 상태(YF_PAUSE/NAVER_PAUSE)도 표기. 정지 게이트를
**우회**해 소스 자체의 건강을 진단(정지와 무관하게 raw fetch 가 되는지 확인).

읽기 전용·진단 전용. 무료. 텔레그램 /health 도 이 함수들을 호출."""
from __future__ import annotations

import json
import time


def _yf_check() -> tuple[bool, str]:
    """yfinance fast_info 직접 1콜(정지 게이트 우회) — AAPL last_price."""
    try:
        import yfinance as yf
        t0 = time.time()
        fi = yf.Ticker("AAPL").fast_info
        lp = getattr(fi, "last_price", None) or (fi.get("last_price") if hasattr(fi, "get") else None)
        dt = (time.time() - t0) * 1000
        if lp:
            return True, f"AAPL last_price={float(lp):,.2f} ({dt:.0f}ms)"
        return False, f"last_price 빈값 ({dt:.0f}ms) — rate-limit/차단 의심"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _yf_batch_check() -> tuple[bool, str]:
    """yfinance 배치 다운로드(홈 스냅샷 경로) — ^GSPC 1콜."""
    try:
        import yfinance as yf
        t0 = time.time()
        df = yf.download("^GSPC", period="5d", progress=False, threads=False, timeout=20)
        dt = (time.time() - t0) * 1000
        if df is not None and not df.empty:
            return True, f"^GSPC {len(df)}봉 ({dt:.0f}ms)"
        return False, f"빈 df ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _naver_get(url: str, headers: dict | None = None, want: str = "json") -> tuple[bool, object, float]:
    import requests
    h = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json", "Referer": "https://m.stock.naver.com/",
    }
    t0 = time.time()
    r = requests.get(url, headers=h, timeout=12)
    dt = (time.time() - t0) * 1000
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}", dt
    if want == "json":
        return True, r.json(), dt
    return True, r.text, dt


def _naver_domestic() -> tuple[bool, str]:
    """네이버 국내 front-api(코스피 등락 랭킹) — 1페이지."""
    try:
        ok, data, dt = _naver_get(
            "https://m.stock.naver.com/front-api/domestic/stock/list"
            "?sortType=up&category=all&page=1&pageSize=5")
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        rows = (data or {}).get("result") or (data or {}).get("stocks") or data
        n = len(rows) if isinstance(rows, list) else (
            len(rows.get("stocks", [])) if isinstance(rows, dict) else 0)
        return (n > 0), f"국내 랭킹 {n}건 ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _naver_world() -> tuple[bool, str]:
    """네이버 해외 worldstock(나스닥 등락) — 1페이지."""
    try:
        ok, data, dt = _naver_get(
            "https://m.stock.naver.com/front-api/worldstock/exchange/stock/list"
            "?stockExchangeType=NASDAQ&stockPriceSortType=up&page=1&pageSize=5")
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        rows = (data or {}).get("result") or (data or {}).get("stocks") or data
        n = len(rows) if isinstance(rows, list) else (
            len(rows.get("stocks", [])) if isinstance(rows, dict) else 0)
        return (n > 0), f"NASDAQ 랭킹 {n}건 ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _naver_upjong() -> tuple[bool, str]:
    """네이버 데스크탑 업종 API(USA 업종 목록)."""
    try:
        ok, data, dt = _naver_get(
            "https://stock.naver.com/api/foreign/market/USA/upjong/list",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                     "Referer": "https://stock.naver.com/"})
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        n = len(data) if isinstance(data, list) else 0
        return (n > 0), f"USA 업종 {n}개 ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _naver_sector() -> tuple[bool, str]:
    """네이버 finance 업종 시세(국내 업종 등락) — HTML."""
    try:
        ok, text, dt = _naver_get(
            "https://finance.naver.com/sise/sise_group.naver?type=upjong",
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR",
                     "Referer": "https://finance.naver.com/sise/"}, want="text")
        if not ok:
            return False, f"{text} ({dt:.0f}ms)"
        has = isinstance(text, str) and "sise_group_detail" in text
        return has, (f"업종 표 {'있음' if has else '없음(구조 변경?)'} "
                     f"({len(text) if isinstance(text, str) else 0}B, {dt:.0f}ms)")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _nv_api(url: str) -> tuple[bool, object, float]:
    return _naver_get(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json",
        "Referer": "https://stock.naver.com/"})


def _snap_index() -> tuple[bool, str]:
    """홈 스냅샷 지수 — worldstock/index (나스닥·S&P)."""
    try:
        ok, data, dt = _nv_api("https://stock.naver.com/api/polling/worldstock/"
                               "index?reutersCodes=.IXIC,.INX")
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        rows = (data or {}).get("datas") or []
        return (len(rows) > 0), f"지수 {len(rows)}건 ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _snap_commodity() -> tuple[bool, str]:
    """홈 스냅샷 원자재 — marketindex/metals."""
    try:
        ok, data, dt = _nv_api("https://stock.naver.com/api/securityService/"
                               "marketindex/metals")
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        n = len(data) if isinstance(data, list) else 0
        return (n > 0), f"원자재(metals) {n}건 ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _snap_coin() -> tuple[bool, str]:
    """홈 스냅샷 코인 — coin/rank/UPBIT/majors (+ 티커 목록으로 DOGE/TRX 진단)."""
    try:
        ok, data, dt = _nv_api("https://stock.naver.com/api/coin/rank/UPBIT/majors")
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        rows = data if isinstance(data, list) else []
        tickers = [x.get("nfTicker") for x in rows]
        return (len(rows) > 0), f"코인 {len(rows)}건 {tickers[:12]} ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _snap_fx() -> tuple[bool, str]:
    """홈 스냅샷 환율·달러인덱스 — marketindex/exchange."""
    try:
        ok, data, dt = _nv_api("https://api.stock.naver.com/marketindex/exchange")
        if not ok:
            return False, f"{data} ({dt:.0f}ms)"
        rows = (data or {}).get("normalList") if isinstance(data, dict) else data
        n = len(rows) if isinstance(rows, list) else 0
        return (n > 0), f"환율 {n}건 ({dt:.0f}ms)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def run() -> dict:
    """전 소스 점검 → {name: (ok, detail)} + 정지 상태."""
    try:
        from bot.finviz_client import fast_info_ok, naver_paused, yf_paused
        yfp, nvp = yf_paused(), naver_paused()
        fi_breaker = not fast_info_ok()
    except Exception:
        yfp = nvp = fi_breaker = False
    checks = {
        # 홈 스냅샷 실제 소스 (사용자 2026-06-14 '다 네이버로' 전환 경로 점검)
        "스냅샷 지수(worldstock)": _snap_index(),
        "스냅샷 원자재(marketindex)": _snap_commodity(),
        "스냅샷 코인(coin/rank)": _snap_coin(),
        "스냅샷 환율(exchange)": _snap_fx(),
        # 기존 경로 (업종 등락·movers·yf 폴백). fast_info 프로브는 제거 — 앱이
        # fast_info 를 안 쓰는데(전부 네이버·download) 진단 1콜이 오히려 yahoo
        # rate-limit 압력에 기여 + 항상 ❌ 로 불안 유발(사용자 2026-06-14).
        "yfinance batch(history)": _yf_batch_check(),
        "Naver 국내(front-api)": _naver_domestic(),
        "Naver 해외(worldstock)": _naver_world(),
        "Naver 업종(desktop API)": _naver_upjong(),
        "Naver 업종(finance HTML)": _naver_sector(),
    }
    return {"yf_paused": yfp, "naver_paused": nvp,
            "fast_info_breaker": fi_breaker, "checks": checks}


def format_report(res: dict) -> str:
    """헬스체크 결과 → 사람이 읽는 텍스트 (CLI·텔레그램 공용)."""
    lines = [
        f"정지 상태: yfinance={'⏸정지' if res['yf_paused'] else '▶️정상'} · "
        f"Naver={'⏸정지' if res['naver_paused'] else '▶️정상'}",
        ("fast_info 회로차단: "
         + ("⏸발동 — 우리 코드가 fast_info 자동 skip 중(쿨다운, download/Naver 사용)"
            if res.get("fast_info_breaker") else "▶️정상")),
        "(아래는 정지·회로차단과 무관하게 소스 raw fetch 직접 점검):",
    ]
    for name, (ok, detail) in res["checks"].items():
        lines.append(f"  {'✅' if ok else '❌'} {name} — {detail}")
    # fast_info ❌ 인데 download+Naver 정상이면 = 구조적·무해 (사용자 2026-06-14
    # '왜 계속 이런거지?'). Yahoo 가 데이터센터 IP 의 시세(quote) 엔드포인트만
    # 제한 — 우리는 download(history)+Naver 로 우회하므로 앱 영향 0. /health 의
    # fast_info 점검은 진단용 '직접' 호출이라 제한 시 항상 ❌ (앱 traffic 아님).
    ck = res.get("checks", {})
    fi_ok = ck.get("yfinance fast_info", (True,))[0]
    batch_ok = ck.get("yfinance batch", (False,))[0]
    naver_ok = any(v[0] for k, v in ck.items() if k.startswith("Naver"))
    if (not fi_ok) and batch_ok and naver_ok:
        lines.append("")
        lines.append(
            "ℹ️ fast_info ❌ 는 정상입니다 — Yahoo 가 데이터센터 IP 의 시세"
            "(quote·fast_info) 엔드포인트를 구조적으로 제한. 우리 앱은 "
            "download(history)+네이버로 우회(회로차단 자동)하며 시총·업종·52주·"
            "차트·장전장후 전부 그 경로라 영향 0. 미국 장전장후(prepost)도 "
            "history API 라 무관. 이 fast_info 점검은 진단용 직접 1콜이라 "
            "Yahoo 제한 중엔 항상 ❌ 로 뜸(앱 트래픽 아님)."
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(run()))
