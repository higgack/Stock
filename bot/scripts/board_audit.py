"""보드 4종 전수 신선도 감사 — PPI · CPI · 유동성 · 시장타이밍.

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

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys

_PROBE_VER = 2


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

    _p("")
    _p("읽는 법: ⚠️ 지연 = 원천 공표일정 대비 뒤처짐(우리 수집 또는 원천 지연).")
    _p("        ❓ 규약없음 = **판정 자체를 못 한다** — 가장 위험하다(조용히 낡는다).")
    _p("           macro_cadence.CADENCE 에 등록하거나 보드 그룹 기본값을 줄 것.")
    _p("        변동성 '창' 이 비면 시계열이 성겨 날짜 되짚기가 실패한 것(값은 유효).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
