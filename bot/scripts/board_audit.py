"""대시보드 전수 감사 — 보드 4종 + 홈 표면 6종(신선도·검산·교차일관성).

사용자 2026-08-20: "한국 PPI 는 여전히 6월인데 제대로 제때 받아오는지 꼼꼼히
확인해주고 … CPI, PPI, 유동성, 시장타이밍 모두 제대로 작동하는지 다시 꼼꼼히."

`liquidity_audit` 은 유동성 보드 **단위·배치**에 특화돼 있다. 이건 네 보드를
**같은 잣대**(bot/macro_cadence 공표규약)로 한 화면에서 세는 감사다 —
화면 배지와 완전히 같은 판정 경로를 태우므로 둘이 갈라질 수 없다.

각 행마다:
  · 기준일(관측기간) · 기대 관측기간 · 지연 여부
  · 규약이 없어 판정 자체를 못 하는 행(가장 위험 — 조용히 낡는다)

    cd ~/stock && .venv/bin/python -m bot.scripts.board_audit
    cd ~/stock && .venv/bin/python -m bot.scripts.board_audit ppi      # 한 보드만
    cd ~/stock && .venv/bin/python -m bot.scripts.board_audit --all    # 정상행까지 전부
    cd ~/stock && .venv/bin/python -m bot.scripts.board_audit home     # 홈 표면만

v3(사용자 2026-08-20 "글로벌 스냅샷·매크로 스냅샷·시장유동성·다가오는 실적·
최근 리서치액션·관심종목 모두 하나씩 꼼꼼히"): 신선도만으로는 못 잡는 두
부류를 추가했다.
  · **검산** — 화면에 나란히 놓인 숫자끼리 산수가 맞는가.
    (실측: 관심종목 예상 PER 이 현재가÷예상 EPS 와 안 맞았다 — 삼성전자
     247,500÷14,227=17.4 인데 화면은 3.7. 미국 종목은 맞고 국내만 틀려서
     눈으로는 안 보였다.)
  · **교차일관성** — 같은 이름의 지표가 두 화면에서 다른 값인가.
    (실측: PPI 가 글로벌 8.27%(PPIACO) vs 매크로 4.84%(PPIFIS) — 서로 다른
     시리즈를 같은 이름으로 부르고 있었다.)

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys

_PROBE_VER = 3


def _p(*a):
    print(*a, flush=True)


def _verdict(row, default):
    """화면 배지와 **같은 경로** — macro_cadence.judge."""
    from bot.macro_cadence import judge
    sid = str(row.get("cadence_id") or row.get("id") or "")
    return sid, judge(sid, str(row.get("latest_date") or ""), default=default)


def _audit_rows(title, rows, default, show_all):
    from bot.macro_cadence import CADENCE
    _p("")
    _p(f"── {title} — {len(rows)}행")
    if not rows:
        _p("   ❌ 0행 — 로더가 아무것도 못 받았다(키·네트워크·원천 확인)")
        return
    late, nojudge, ok = [], [], 0
    for r in rows:
        sid, j = _verdict(r, default)
        if j is None:
            nojudge.append((sid, r))
            continue
        if j["stale"]:
            late.append((sid, r, j))
        else:
            ok += 1
    _p(f"   ✅ 정상 {ok} · ⚠️ 지연 {len(late)} · ❓ 규약없음 {len(nojudge)}")
    for sid, r, j in late:
        _p(f"   ⚠️ {r.get('name','?')[:38]:38} {sid[:22]:22} "
           f"기준 {r.get('latest_date','—')} · 기대 {j['expected']} "
           f"· {j['behind']}기 지연 · {j['why']}")
    for sid, r in nojudge:
        _p(f"   ❓ {r.get('name','?')[:38]:38} {sid[:22]:22} "
           f"기준 {r.get('latest_date','—')} — 공표규약 미등록(조용히 낡을 수 있음)")
    if show_all:
        for r in rows:
            sid, j = _verdict(r, default)
            if j and not j["stale"]:
                _p(f"   ✅ {r.get('name','?')[:38]:38} {sid[:22]:22} "
                   f"기준 {r.get('latest_date','—')} · 기대 {j['expected']}")
    _p(f"   (개별 규약 등록분 {sum(1 for r in rows if str(r.get('cadence_id') or r.get('id') or '') in CADENCE)}행"
       f" · 나머지는 보드 그룹 기본값 {default[0] if default else '없음'}"
       f"{'/' + str(default[1]) + '일' if default else ''})")


def _audit_home_surfaces(show_all):
    """홈 대시보드 6종 — 신선도 + **검산** + **교차일관성**."""
    _p("")
    _p("══ 홈 표면 6종 ══")

    # ① 글로벌 시장 스냅샷 — FRED 지표 라벨↔시리즈, 값 유무
    _p("")
    _p("── 글로벌 시장 스냅샷 (핵심 지표)")
    try:
        import bot.market_overview as mo
        from bot.macro_cadence import judge
        rows = mo._fetch_all_fred()
        empty = 0
        for r in rows:
            d = r.get("data") or {}
            sid = r.get("series_id") or ""
            asof = str(d.get("date") or d.get("asof") or "")[:10]
            if not d:
                empty += 1
            j = judge(sid, asof) if asof else None
            mark = ("❌ 값 없음" if not d else
                    "⚠️ 지연" if j and j["stale"] else
                    "❓ 규약없음" if asof and j is None else "✅")
            _p(f"   {str(r.get('label',''))[:22]:22} {sid:22} "
               f"기준 {asof or '—':10} 값 {str(d.get('value', '—'))[:10]:>10} {mark}")
        if empty:
            _p(f"   ❌ 값이 안 온 지표 {empty}개 — 키·원천 확인")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")

    # ② 매크로 스냅샷 — 카드별 기준일 + 지연배지 + 기간 시작 라벨
    _p("")
    _p("── 매크로 스냅샷 (카드)")
    try:
        from bot.macro_snapshot import fetch_macro_snapshot
        data = fetch_macro_snapshot()
        rows = (data or {}).get("indicators") or []
        late = [r for r in rows if r.get("asof_stale")]
        noasof = [r for r in rows if not r.get("asof")]
        _p(f"   카드 {len(rows)}개 · ⚠️ 지연 {len(late)} · 기준일 없음 {len(noasof)}")
        for r in late:
            _p(f"   ⚠️ {r.get('label','?')[:24]:24} 기준 {r.get('asof','—')}")
        for r in noasof:
            _p(f"   ❓ {r.get('label','?')[:24]:24} 기준일 미표기")
        # 기간 시작 라벨이 **날짜**인가(어림 '12개월 전' 은 검산이 안 된다)
        fred_rows = [r for r in rows if r.get("period_start") is not None]
        vague = [r for r in fred_rows if not r.get("period_start_asof")
                 and r.get("spark_span") == "12개월"]
        _p(f"   기간 시작 라벨: 날짜 표기 {len(fred_rows) - len(vague)}"
           f" · 어림('N개월 전') {len(vague)}")
        # **평평한 시계열** — 값이 한 번도 안 변하면 원천이 멈춘 것이다.
        flat = [r for r in rows
                if (r.get("spark") or []) and len(set(
                    v for v in r["spark"] if v is not None)) == 1]
        if flat:
            _p(f"   ⚠️ 값이 전혀 안 변하는 카드 {len(flat)}개 — 원천 정지 의심:")
            for r in flat:
                _p(f"      {r.get('label','?')[:24]:24} {r.get('value')} "
                   f"({len(r.get('spark') or [])}점 전부 동일)")
        if show_all:
            for r in rows:
                _p(f"   ✅ {r.get('label','?')[:24]:24} {r.get('value')} "
                   f"· 기준 {r.get('asof','—')}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")

    # ③ 시장유동성 — 예탁금/신용(금투협) 기준일
    _p("")
    _p("── 시장유동성 (투자자 예탁금·신용)")
    try:
        from bot import fsc_client
        dep = fsc_client.market_deposit()
        asof = (dep or {}).get("date") or (dep or {}).get("basDt") or ""
        from datetime import date, datetime, timedelta
        age = None
        if asof:
            try:
                d = datetime.strptime(str(asof)[:10].replace(".", "-"),
                                      "%Y-%m-%d").date()
                age = (date.today() - d).days
            except ValueError:
                pass
        _p(f"   예탁금 기준일 {asof or '—'}"
           f"{f' ({age}일 전)' if age is not None else ''}"
           f"{'  ⚠️ 금투협은 통상 T+1~2 — 5일 넘으면 확인' if age and age > 5 else ''}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")

    # ④ 다가오는 실적
    _p("")
    _p("── 다가오는 실적")
    try:
        from datetime import date
        from bot import earnings_calendar as ec
        t = date.today()
        for mkt in ("kr", "us"):
            rows = ec.fetch_month(t.year, t.month) if mkt == "kr" else []
            fut = sorted(r for r in (x.get("date", "") for x in rows) if r >= t.isoformat())
            _p(f"   {mkt.upper():3} 이번달 {len(rows):>4}건 · 오늘 이후 {len(fut)}건"
               f" · 가장 가까운 {fut[0] if fut else '—'}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")

    # ⑤ 최근 리서치 액션
    _p("")
    _p("── 최근 리서치 액션")
    try:
        from bot import naver_research_client as nrc
        items = nrc.fetch_recent_research_market(limit=25)
        dates = sorted({str(i.get("date", ""))[:10] for i in items if i.get("date")})
        _p(f"   {len(items)}건 · 최신 {dates[-1] if dates else '—'}"
           f" · 가장 오래된 {dates[0] if dates else '—'}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")

    # ⑥ 관심종목 — **검산**: 예상 PER = 현재가 ÷ 예상 EPS
    _p("")
    _p("── 관심종목 (검산: 예상 PER = 현재가 ÷ 예상 EPS)")
    try:
        from bot import market_favorites as mf
        favs = mf.get_favorites_with_prices()
        bad, checked, blank = [], 0, 0
        for f in favs:
            px, eps, per = (f.get("current_price"), f.get("eps_estimate"),
                            f.get("per"))
            if per is None:
                blank += 1
                continue
            if px is None or not eps:
                bad.append((f, per, None))
                continue
            calc = px / eps
            checked += 1
            if abs(calc - per) > max(0.05 * abs(per), 0.1):
                bad.append((f, per, calc))
        _p(f"   {len(favs)}종목 · 검산 통과 {checked - len([b for b in bad if b[2]])}"
           f" · ❌ 불일치 {len(bad)} · PER 빈칸 {blank}")
        for f, per, calc in bad[:12]:
            _p(f"   ❌ {str(f.get('name_kr') or f.get('name'))[:16]:16} "
               f"{f.get('ticker',''):12} 화면 PER {per} · "
               f"현재가 {f.get('current_price')} ÷ EPS {f.get('eps_estimate')} = "
               f"{f'{calc:.1f}' if calc else '계산불가'}")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")

    # ⑦ 교차일관성 — 같은 이름의 지표가 두 화면에서 다른 값인가
    _p("")
    _p("── 교차일관성 (같은 이름 지표가 화면마다 다른가)")
    try:
        import re as _re
        mo_src = open("bot/market_overview.py", encoding="utf-8").read()
        ms_src = open("bot/macro_snapshot.py", encoding="utf-8").read()
        # 라벨은 **한글/공백/괄호**를 포함한 표기이고 시리즈 id 는 대문자·숫자다.
        # v3 초안은 둘을 구분 못 해 `'CPIAUCSL' = PPIACO` 라는 유령 행을 냈다.
        mo_ids = {lbl: sid for lbl, sid in _re.findall(
            r'\("([^"]*(?:PPI|CPI|PCE)[^"]*)",\s*"([A-Z0-9]+)"', mo_src)
            if not _re.fullmatch(r"[A-Z0-9]+", lbl)}
        ms_ids = dict((m[1], m[0]) for m in _re.findall(
            r'\("[a-z_]+",\s*"([^"]+)",\s*"[^"]*",\s*"fred",\s*"([A-Z0-9]+)"', ms_src))
        def _topic(lbl):
            for t in ("PPI", "CPI", "PCE"):
                if t in lbl.upper():
                    return t
            return ""

        def _qualifier(lbl, topic):
            """주제어·시장접두어·(YoY) 를 뺀 나머지 = 구분자."""
            out = _re.sub(r"\(YoY\)|미국|글로벌|US\b", "", lbl)
            return _re.sub(r"[\s()]+", "", out.replace(topic, ""))

        for lbl, sid in mo_ids.items():
            topic = _topic(lbl)
            twin = ms_ids.get(sid)
            rivals = [(s, l) for s, l in ms_ids.items()
                      if s != sid and _topic(l) == topic]
            if twin:
                _p(f"   ✅ '{lbl}' = {sid} — 매크로도 같은 시리즈")
                continue
            if not rivals:
                _p(f"   ✅ '{lbl}' = {sid} — 매크로엔 같은 주제 지표 없음")
                continue
            rs, rl = rivals[0]
            # 두 화면이 **다른 시리즈**를 쓰는 건 문제가 아니다. 문제는
            # 라벨만 보고 구분이 안 되는 것 — 그때만 경고한다.
            if _qualifier(lbl, topic) and _qualifier(rl, topic):
                _p(f"   ✅ '{lbl}'({sid}) vs 매크로 '{rl}'({rs}) — 라벨로 구분됨")
            else:
                _p(f"   ⚠️ '{lbl}'({sid}) vs 매크로 '{rl}'({rs}) — **같은 이름,"
                   f" 다른 시리즈**라 한쪽이 틀린 것처럼 보인다. 라벨에 기준을"
                   f" 넣을 것")
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   조회 실패 {type(exc).__name__}: {exc}")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    show_all = "--all" in args
    want = {a for a in args if not a.startswith("--")}
    from bot import fred_boards as fb
    from bot.macro_cadence import BLS_MONTHLY, GRACE_DAYS
    from bot.env_keys import env_key

    _p(f"board_audit v{_PROBE_VER} · 공표규약 여유 {GRACE_DAYS}일")
    # 실수 #23 — 프로브는 .env 를 안 읽는다. 어느 키가 살아 있는지 먼저.
    # ⚠️ 키 **이름을 틀리면** 있는 키를 '없음'으로 오보한다(v1 이 정확히
    # 그랬다: 실제 이름은 `BOK_ECOS_API_KEY` 인데 `ECOS_API_KEY` 로 물어
    # "❌ 없음" 을 찍었고, 정작 한국 행은 멀쩡히 14/12행 들어왔다).
    # 이름을 손으로 적지 말고 **클라이언트가 쓰는 그대로** 가져온다.
    import re as _re
    keys = ["FRED_API_KEY"]
    try:
        _src = open("bot/bok_ecos_client.py", encoding="utf-8").read()
        keys += sorted(set(_re.findall(r'_env_key\("([A-Z0-9_]+)"\)', _src)))
    except OSError:
        keys.append("BOK_ECOS_API_KEY")
    for k in keys:
        _p(f"   {k}: {'있음' if env_key(k) else '❌ 없음 — 해당 보드는 0행이 된다'}")

    if not want or "ppi" in want:
        rows, _margins, dropped = fb._load_ppi()
        us = [r for r in rows if not str(r.get("id", "")).startswith("ECOS:")]
        kr = [r for r in rows if str(r.get("id", "")).startswith("ECOS:")]
        _audit_rows("PPI 보드 · 미국(FRED/BLS)", us, BLS_MONTHLY, show_all)
        _audit_rows("PPI 보드 · 한국(ECOS)", kr, fb._KR_PPI_CADENCE, show_all)
        if dropped:
            _p(f"   제외 {len(dropped)}종: {', '.join(dropped[:6])}"
               f"{' …' if len(dropped) > 6 else ''}")

    if not want or "cpi" in want:
        rows, dropped = fb._load_cpi()
        us = [r for r in rows if not str(r.get("id", "")).startswith("ECOS:")]
        kr = [r for r in rows if str(r.get("id", "")).startswith("ECOS:")]
        _audit_rows("CPI 보드 · 미국(FRED/BLS)", us, BLS_MONTHLY, show_all)
        _audit_rows("CPI 보드 · 한국(ECOS)", kr, fb._KR_CPI_CADENCE, show_all)
        if dropped:
            _p(f"   제외 {len(dropped)}종: {', '.join(dropped[:6])}"
               f"{' …' if len(dropped) > 6 else ''}")

    if not want or "liq" in want:
        rows, _derived, _score = fb._load_liq()
        _audit_rows("유동성 보드", rows, None, show_all)

    if not want or "timing" in want:
        _p("")
        _p("── 시장타이밍 보드 — 지수 기준일 vs 기대 거래일")
        from bot import market_timing as mt
        for mkt, indices in mt.MARKET_INDICES.items():
            ticker, name = indices[0]
            rows = mt.fetch_index_history(ticker, days=120)
            latest = rows[-1]["date"] if rows else None
            exp, grace = mt._expected_session(mkt)
            behind = (mt._sessions_between(mkt, latest, exp)
                      if latest and exp and latest < exp else 0)
            mark = ("❌ 0행" if not rows else
                    f"⚠️ {behind}거래일 지연" if behind and behind > grace else "✅")
            _p(f"   {mkt:5} {name[:16]:16} {ticker:10} {len(rows):>4}행 · "
               f"기준 {latest or '—'} · 기대 {exp} · 여유 {grace} {mark}")
        _p("")
        _p("── 시장타이밍 보드 — 변동성 카드")
        snap = mt.fetch_volatility_snapshot()
        for key in ("vix", "vkospi", "move"):
            rec = snap.get(key)
            if not rec:
                _p(f"   {key:8} ❌ 없음 → 카드 생략")
                continue
            age = mt._vol_age_days(rec.get("date"))
            wins = ", ".join(k for k, v in (rec.get("history") or {}).items()
                             if v is not None) or "없음"
            _p(f"   {key:8} {rec['value']:.1f} · 기준 {rec.get('date') or '—'}"
               f"{f' ({age}일 전)' if age is not None else ''} · "
               f"소스 {rec.get('source') or '—'}"
               f"{' · **캐시**' if rec.get('from_cache') else ''} · 창 [{wins}]")

    if not want or "home" in want:
        _audit_home_surfaces(show_all)

    _p("")
    _p("읽는 법: ⚠️ 지연 = 원천 공표일정 대비 뒤처짐(우리 수집 또는 원천 지연).")
    _p("        ❓ 규약없음 = **판정 자체를 못 한다** — 가장 위험하다(조용히 낡는다).")
    _p("           macro_cadence.CADENCE 에 등록하거나 보드 그룹 기본값을 줄 것.")
    _p("        변동성 '창' 이 비면 시계열이 성겨 날짜 되짚기가 실패한 것(값은 유효).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
