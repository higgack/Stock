"""대만 급등·급락의 **업종·시총**이 왜 비는가 — 단계별 실측.

사용자 2026-09-10: "업종이랑 시총 안나오는건 너무 작아서 그런거야?"
크기는 두 값 **어느 경로에도 들어가지 않는다** — 업종은 上市·上櫃 전종목
일괄 맵이고 시총은 티커마다 만든다. 그러니 '작아서'는 가설이 아니라 아직
안 잰 것이다(#12). 같은 질문이 세 번째라(2026-08-04 업종 · 08-19 업종 ·
오늘 업종+시총) 확인 명령을 건네는 대신 단계를 갈라 센다(#252).

  ① 대상        화면과 같은 입력(오늘 무버 TOP30) 또는 인자로 준 코드(#35).
                인자 모드도 종가를 붙인다 — FinMind 시총 폴백이 종가를 쓰므로
                None 으로 넘기면 화면과 다른 결과가 난다(#145).
  ② 캐시·회로    화면이 읽는 그 자리 — **③이 갱신하기 전에** 먼저 찍는다
  ③ 렌더-세이프  cache-only = 화면이 **지금** 받는 값(#35 화면이 쓰는 그 경로)
  ④ 소스        업종 上市/上櫃 OpenAPI 를 지금 직접 받아 코드별로 어디에 있나
                (--slow 면 ④-b: 백그라운드와 같은 yfinance/FinMind 경로)
  ⑤ 판정        갈래로(#82) — 판정 불가는 ✅ 가 아니다(#54). ④-b 를 돌렸으면
                그 결과가 판정에 **들어간다**(같은 실행이 반증한 원인을 적지 않는다)

    cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe --slow
    cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe 8227 6949

부수효과를 사실대로(#284): 기본 실행은 **화면 렌더와 같은 경로**라 렌더가
원래 하는 갱신(업종맵 24h 캐시 · finviz 업종 보조맵)은 한다. LLM 번역은
0(want_name 을 열지 않는다 — #312·#321), 시총 디스크 캐시는 쓰지 않는다.
`--slow` 만 백그라운드와 같은 경로(yfinance·FinMind)를 태워 시총 캐시를
**채운다**(= 화면을 고치는 쓰기). fast_info 레이트리밋 쿨다운은 봇
프로세스 메모리에만 있어 여기서는 **보이지 않는다** — 그래서 말하지 않는다.
"""
from __future__ import annotations

import sys
import time

from bot.finviz_client import MCAP_PERSIST_TTL
from bot.twse_client import _TW_IND_CACHE_TTL

_PROBE_VER = 5
# 문턱은 **제품에서 가져온다** — 복제하면 진단이 화면과 다른 말을 한다(#38).
_MCAP_TTL_H = MCAP_PERSIST_TTL / 3600
_IND_TTL_H = _TW_IND_CACHE_TTL / 3600


def _p(*a):
    print(*a, flush=True)


def _age_label(sec: float | None) -> str:
    if sec is None:
        return "파일 없음"
    h = sec / 3600.0
    return f"{h:.1f}시간 전" if h >= 1 else f"{sec / 60:.0f}분 전"


def enrich_verdict(*, n: int, render_ok: bool, mcap_filled: int, ind_filled: int,
                   mcap_age_sec: float | None, map_n: int, in_map: int,
                   src_in: int, yf_pause: bool,
                   slow: tuple[int, int] | None = None) -> list[str]:
    """②③④ 실측 → 사람이 읽는 판정 줄(순수 함수 — 값으로 검증, #41·#176).

    모집단을 섞지 않는다: `map_n`·`in_map` 은 **화면이 읽는 캐시 맵** 하나에서
    세고, `src_in` 은 ④가 지금 직접 받은 원천에서 센다(둘이 갈리면 그 자체가
    '캐시가 낡았다'는 판정이다). `slow` 는 ④-b 를 돌렸을 때 (시총, 업종) 채움
    수 — 같은 실행이 채웠는데 '캐시 파일이 없다'를 원인으로 적으면 거짓이다.
    갈래마다 처방이 다르므로 뭉뚱그리지 않는다(#82). 잴 게 없거나 재는
    경로가 죽었으면 ✅ 도 ❌ 도 아니라 판정 불가다(#54)."""
    if n <= 0:
        return ["❓ 대조할 종목이 0개 — 무버 목록을 못 받았다(원천/네트워크). "
                "업종·시총 판정 불가."]
    if not render_ok:
        return ["❓ 렌더 경로(③)가 예외로 끝나 화면 값을 못 쟀다 — 판정 불가. "
                "위 ③의 예외가 먼저다(캐시·원천 판정은 그 뒤에)."]
    out: list[str] = []

    # ── 시총
    if mcap_filled >= n:
        out.append(f"✅ 시총 {mcap_filled}/{n} — 화면이 채운다")
    elif slow is not None and slow[0] > mcap_filled:
        out.append(f"❌ 시총 {mcap_filled}/{n} (지금 화면) — 캐시 콜드였다: "
                   f"--slow 가 {slow[0]}/{n} 을 채워 디스크에 썼으니 다음 렌더부터 뜬다")
    elif slow is not None:
        out.append(f"❌ 시총 {mcap_filled}/{n} — --slow 로도 {slow[0]}/{n}: "
                   "원천(yfinance fast_info · FinMind 발행주식수)이 이 종목들을 못 준다")
    else:
        if mcap_age_sec is None:
            why = "디스크 캐시 파일이 없다(백그라운드 enrich 가 한 번도 못 썼다)"
        elif mcap_age_sec >= MCAP_PERSIST_TTL:
            why = (f"디스크 캐시가 {_age_label(mcap_age_sec)}이라 만료"
                   f"(TTL {_MCAP_TTL_H:.0f}시간) — 통째로 버려진다")
        else:
            why = (f"캐시는 살아 있는데({_age_label(mcap_age_sec)}) 이 티커들이 "
                   "그 안에 없다 — 백그라운드가 아직 못 채웠다")
        out.append(f"❌ 시총 {mcap_filled}/{n} — {why}")
    if mcap_filled < n and yf_pause:
        out.append("   ↪ yfinance 정지 마커가 켜져 있다 — 백그라운드가 "
                   "시총을 채울 수 없다(마커를 끄기 전엔 안 채워진다)")

    # ── 업종
    if ind_filled >= n:
        out.append(f"✅ 업종 {ind_filled}/{n} — 화면이 채운다")
    elif map_n <= 0 and src_in <= 0:
        out.append(f"❌ 업종 {ind_filled}/{n} — 캐시 맵도 원천(上市·上櫃 OpenAPI)도 "
                   "0종목 = 소스 장애. 이 맵은 전종목 일괄이라 종목 크기와 무관하다")
    elif map_n <= 0:
        out.append(f"❌ 업종 {ind_filled}/{n} — 캐시 맵이 없는데 원천엔 {src_in}/{n} 있다: "
                   "③이 방금 받아 썼거나 다음 렌더가 받는다 — 곧 채워진다")
    elif in_map >= n:
        out.append(f"❗ 업종 {ind_filled}/{n} — 캐시 맵({map_n}종목)엔 {in_map}/{n} 이 "
                   "다 있는데 화면 값이 비었다 = 배선 문제")
    elif src_in > in_map:
        out.append(f"⚠️ 업종 {ind_filled}/{n} — 캐시 맵({map_n}종목)엔 {in_map}/{n}, "
                   f"지금 원천엔 {src_in}/{n}: 맵이 낡았다(TTL {_IND_TTL_H:.0f}시간 "
                   "안이라 화면이 옛 맵을 읽는다 — 만료 뒤 첫 렌더가 갱신)")
    else:
        gap = n - max(in_map, src_in)
        out.append(f"⚠️ 업종 {ind_filled}/{n} — 上市·上櫃 파싱 맵 어디에도 없는 코드 "
                   f"{gap}개: 興櫃·ETF·신규상장이거나 원천이 그 코드를 안 실은 경우다 "
                   "— 어느 쪽인지는 ④ 원문으로(단정 안 함). yfinance `.TWO` 폴백은 "
                   "백그라운드에서만 돈다")
    if slow is not None and slow[1] > ind_filled:
        out.append(f"   ↪ --slow 가 업종을 {slow[1]}/{n} 까지 채웠다 — 다음 렌더부터 뜬다")

    # 사용자 질문에 대한 답은 **재서** 말한다(#165 안 잰 것을 단정하지 말 것).
    if map_n > 0 or src_in > 0:
        out.append(f"↪ '너무 작아서?' 에 대한 답: 일괄 맵(캐시 {map_n}종목)에 이 표의 "
                   f"{in_map}/{n}, 지금 받은 원천엔 {src_in}/{n} 이 실제로 들어 있다 — "
                   "크기가 이유라면 맵에도 없어야 한다")
    return out


def _closes_by_code(tw) -> dict[str, float]:
    """인자 모드용 종가 — 화면 경로(fetch_tw_movers)가 싣는 그 값(#145)."""
    try:
        rows, _ = tw._tw_all_common()
        return {str(r.get("code")): r.get("close") for r in rows
                if r.get("code") and r.get("close")}
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   ⚠️ 종가 조회 실패 {type(exc).__name__}: {exc} — 종가 없이 진행"
           "(FinMind 시총 폴백은 종가가 없으면 안 돈다)")
        return {}


def main() -> int:
    argv = list(sys.argv[1:])
    slow = "--slow" in argv
    codes = [a.split(".")[0] for a in argv if not a.startswith("--")]

    from bot import twse_client as tw
    from bot import finviz_client as fv

    _p(f"🇹🇼 tw_enrich_probe v{_PROBE_VER} · 업종·시총이 왜 비는가 · "
       + ("--slow(yfinance·FinMind 호출 + 시총 캐시 기록)" if slow
          else "렌더와 같은 경로(업종맵 갱신 가능) · LLM 0 · 시총 캐시 기록 없음"))
    _p(f"① 인터프리터: {sys.executable}")

    items: list[dict] = []
    if codes:
        px = _closes_by_code(tw)
        items = [{"ticker": f"{c}.TW", "name": c, "price": px.get(c)} for c in codes]
        nopx = [c for c in codes if px.get(c) is None]
        _p(f"   대상: 인자 {len(codes)}종목"
           + (f" · 종가 미확인 {len(nopx)}개({', '.join(nopx[:5])})" if nopx else " · 종가 붙임"))
    else:
        try:
            mv = tw.fetch_tw_movers()
            for it in (mv.get("up") or []) + (mv.get("down") or []):
                items.append({"ticker": f"{it.get('code','')}.TW",
                              "name": it.get("name") or it.get("code"),
                              "price": it.get("close")})
            codes = [it["ticker"].split(".")[0] for it in items]
            _p(f"   대상: 화면과 같은 입력 — 오늘 무버 {len(items)}종목"
               f"{(' · ' + mv.get('date', '')) if mv.get('date') else ''}")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 무버 목록 실패 {type(exc).__name__}: {exc}")
    tickers = [it["ticker"] for it in items]
    n = len(tickers)

    _p("")
    _p("② 캐시·회로 상태 (③ 이 갱신하기 전의 값)")
    mcap_age = fv.cache_age_sec("enrich_mcap_TW.json")
    expired = mcap_age is not None and mcap_age >= MCAP_PERSIST_TTL
    _p(f"   시총 캐시  enrich_mcap_TW.json  {_age_label(mcap_age)}"
       f" · TTL {_MCAP_TTL_H:.0f}시간{'  ❌ 만료 — 통째로 버려진다' if expired else ''}")
    persist = fv._cached("enrich_mcap_TW.json", ttl=MCAP_PERSIST_TTL) or {}
    have_mc = sum(1 for t in tickers if persist.get(t) is not None)
    _p(f"              항목 {len(persist)}종목 · 이 표의 {have_mc}/{n}")
    try:
        fp = tw._CACHE_DIR / "tw_industry_map.json"
        ind_age = (time.time() - fp.stat().st_mtime) if fp.exists() else None
    except OSError:
        ind_age = None
    ind_cached = tw._cached_stale("tw_industry_map", max_age_sec=_TW_IND_CACHE_TTL) or {}
    map_n = len(ind_cached)
    in_map = sum(1 for c in codes if ind_cached.get(c))
    _p(f"   업종 캐시  tw_industry_map.json  {_age_label(ind_age)}"
       f" · TTL {_IND_TTL_H:.0f}시간 · 항목 {map_n}종목 · 이 표의 {in_map}/{n}")
    yf_pause = fv.yf_paused()
    _p(f"   yfinance   정지마커 {'🚫 켜짐' if yf_pause else '꺼짐'}"
       " · fast_info 쿨다운은 봇 프로세스 안에서만 보여 여기서 판정 불가")

    _p("")
    _p("③ 렌더-세이프(cache-only) = 화면이 지금 받는 값")
    mcap_filled = ind_filled = 0
    render_ok = False
    if n:
        try:
            from bot.highlow_render import _enrich_compute
            meta = _enrich_compute(tickers, items, "TW", True, False, False)
            render_ok = True
            for it in items:
                m = meta.get(it["ticker"], {}) or {}
                mcap_filled += 1 if m.get("mcap") is not None else 0
                ind_filled += 1 if m.get("ind") else 0
            _p(f"   시총 {mcap_filled}/{n} · 업종 {ind_filled}/{n}")
            for it in items[:10]:
                m = meta.get(it["ticker"], {}) or {}
                mc = m.get("mcap")
                _p(f"   {it['ticker']:10} 시총 {(mc if mc is not None else '— (빈칸)'):>12}"
                   f"   업종 {m.get('ind') or '— (빈칸)'}")
            if n > 10:
                _p(f"   … 앞 10종목만 표시(전체 {n}종목은 위 집계)")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 실패 {type(exc).__name__}: {exc}")

    _p("")
    _p("④ 소스 갈라 세기 — 지금 직접 받는다")
    per_src: dict[str, dict] = {}
    for url, label in ((tw._OPENAPI_LISTED_INFO, "上市(TWSE)"),
                       (tw._OPENAPI_OTC_INFO, "上櫃(TPEx)")):
        try:
            m = tw._fetch_one_industry_source(url, label)
        except Exception as exc:                               # noqa: BLE001
            _p(f"   {label:12} 예외 {type(exc).__name__}: {exc}")
            m = {}
        per_src[label] = m
        _p(f"   {label:12} {len(m):>5}종목"
           f"{'  ❌ 비었다 — 이 대역이 통째로 빈다' if not m else ''}")
    where = {c: next((lb for lb, m in per_src.items() if m.get(c)), None) for c in codes}
    src_in = sum(1 for c in codes if where[c])
    _p(f"   이 표의 {src_in}/{n} 이 원천에 있음"
       + (f" · 上市 {sum(1 for v in where.values() if v and v.startswith('上市'))}"
          f" · 上櫃 {sum(1 for v in where.values() if v and v.startswith('上櫃'))}" if n else ""))
    missing = [c for c in codes if not where[c]]
    if missing:
        _p(f"   어디에도 없는 코드 {len(missing)}개: {' '.join(missing[:20])}"
           f"{' …' if len(missing) > 20 else ''}")
    if not per_src.get("上櫃(TPEx)"):
        _p("   ↪ 上櫃 원문 2행 — 어느 필드가 코드/업종인가(추측 금지, #155)")
        try:
            import requests
            r = requests.get(tw._OPENAPI_OTC_INFO, headers=tw._HDRS, timeout=15)
            rows = r.json() if r.status_code == 200 else None
            if not isinstance(rows, list) or not rows:
                _p(f"      HTTP {r.status_code} · 리스트 아님 — 응답 앞 200자:")
                _p(f"      {str(r.text)[:200]}")
            else:
                _p(f"      {len(rows)}행 · 키 {list(rows[0])}")
                for row in rows[:2]:
                    _p("      " + " · ".join(f"{k}={row[k]!r}" for k in list(row)[:8]))
        except Exception as exc:                               # noqa: BLE001
            _p(f"      실패 {type(exc).__name__}: {exc}")

    slow_res: tuple[int, int] | None = None
    if slow and n:
        _p("")
        _p("④-b 백그라운드와 같은 경로(--slow) — yfinance·FinMind, 시총 캐시 기록")
        try:
            from bot.highlow_render import _enrich_compute
            meta2 = _enrich_compute(tickers, items, "TW", True, False, True)
            s_mc = sum(1 for t in tickers
                       if (meta2.get(t, {}) or {}).get("mcap") is not None)
            s_ind = sum(1 for t in tickers if (meta2.get(t, {}) or {}).get("ind"))
            slow_res = (s_mc, s_ind)
            _p(f"   시총 {s_mc}/{n} · 업종 {s_ind}/{n}")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 실패 {type(exc).__name__}: {exc}")

    _p("")
    _p("⑤ 판정")
    for line in enrich_verdict(n=n, render_ok=render_ok, mcap_filled=mcap_filled,
                               ind_filled=ind_filled, mcap_age_sec=mcap_age,
                               map_n=map_n, in_map=in_map, src_in=src_in,
                               yf_pause=yf_pause, slow=slow_res):
        _p(f"   {line}")
    if not slow and render_ok and (mcap_filled < n or ind_filled < n):
        _p("   ↪ 캐시 콜드인지 원천 부재인지 가르려면(시총 캐시를 채우는 쓰기):")
        _p("      cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe --slow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
