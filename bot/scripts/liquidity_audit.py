"""유동성 대시보드 전 항목 점검 — 배치·단위·신선도.

사용자 2026-08-19: "유동성대시보드에 있는 항목들 모두 확인해서 제대로
배치되었는지, 최신의 데이터를 제대로 그때그때 불러오는지도 다 점검."

세 가지를 한 화면에서 본다:
  ① **배치** — 분류(category)와 단위 표기
  ② **단위** — 화면 표기값이 상식 범위 안인지. TGA 가 `$963.95T`(실제
     $963.95B, 1000배)로 떠 있었다 — FRED `WTREGEN` 이 백만$ 인데 카탈로그가
     십억$ 로 잡고 있었다. 같은 실수를 눈이 아니라 범위로 잡는다.
  ③ **신선도** — `bot/macro_cadence` 규약 대비 지연 여부.

    cd ~/stock && .venv/bin/python -m bot.scripts.liquidity_audit

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys

_PROBE_VER = 3

# 표기 단위 → 배수(화면 JS `UM` 과 같은 규약).
_MULT = {"M USD": 1e6, "B USD": 1e9, "M EUR": 1e6, "100M JPY": 1e8}

# ⚠️ **상식 범위**(화면 표기 기준, 절대값). 단위를 틀리면 1000배로 벗어나므로
# 넉넉히 잡아도 잡힌다. 여기 없는 항목은 범위 검사를 하지 않는다(추측 금지).
_SANE = {
    "M2SL": (1e13, 5e13, "M2 는 10~50조 달러"),
    "M1SL": (5e12, 4e13, "M1 은 5~40조 달러"),
    "RMFSL": (5e11, 1e13, "리테일 MMF 는 0.5~10조 달러"),
    "WALCL": (2e12, 1.2e13, "Fed 총자산은 2~12조 달러"),
    "WTREGEN": (2e10, 3e12, "TGA 는 200억~3조 달러"),
    "WRESBAL": (5e11, 5e12, "지준잔고는 0.5~5조 달러"),
    "RRPONTSYD": (0, 3e12, "ON RRP 는 0~3조 달러"),
    "BOGMBASE": (2e12, 1e13, "본원통화는 2~10조 달러"),
    "TOTBKCR": (5e12, 3e13, "상업은행 총자산은 5~30조 달러"),
    "BUSLOANS": (1e12, 5e12, "상업·산업대출은 1~5조 달러"),
    "DPSACBW027SBOG": (5e12, 3e13, "은행 예금은 5~30조 달러"),
}


def _p(*a):
    print(*a, flush=True)


def _fmt(v: float, unit: str) -> str:
    """화면 JS 와 같은 규약으로 사람이 읽는 문자열."""
    a = v * _MULT.get(unit, 1.0)
    for lim, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(a) >= lim:
            return f"{a / lim:,.2f}{suf}"
    return f"{a:,.1f}"


def _series_meta(sid: str):
    """FRED 시리즈 메타(원천의 마지막 관측일). FRED 시리즈가 아니면 None."""
    if ":" in sid:                      # ECOS:/AK: 등 비-FRED 소스
        return None
    try:
        from bot.fred_client import fetch_series_meta
        return fetch_series_meta(sid)
    except Exception:                                          # noqa: BLE001
        return None


def summary_lines(bad_unit, late, no_rule) -> list:
    """요약 줄. ⚠️ **세기만 한다** — 이름은 위 표가 이미 댔다.

    옛 코드는 지연 항목을 다시 나열해 같은 결함이 두 번 세어졌다(2026-08-31
    실측: 지연 7건이 sweep 에서 14건). 총계와 소계가 다른 모집단을 세면
    갈라진다(#45·#250).
    """
    out = [f"── 요약: 단위 의심 {len(bad_unit)} · 지연 {len(late)} · "
           f"규약 없음 {len(no_rule)}"]
    if bad_unit or late:
        out.append(f"   위 표의 ❌ 표시 항목을 볼 것(단위 {len(bad_unit)} · "
                   f"지연 {len(late)})")
    return out


def main() -> int:
    from bot.fred_boards_catalog import LIQ_SERIES
    from bot.macro_cadence import CADENCE, judge
    _p(f"liquidity_audit v{_PROBE_VER} · 항목 {len(LIQ_SERIES)}개")

    # 화면이 쓰는 **그 로더**로 받는다(별도 경로를 만들면 화면과 어긋난다).
    try:
        from bot import fred_client
        from bot.fred_boards import _LIQ_START, _alt_history
        hist = {}
        for s in LIQ_SERIES:
            try:
                hist[s["id"]] = (_alt_history(s["src"]) if s.get("src")
                                 else fred_client.fetch_history(s["id"],
                                                                _LIQ_START))
            except Exception as exc:                           # noqa: BLE001
                _p(f"   {s['id']}: 수집 실패 {type(exc).__name__}: {exc}")
                hist[s["id"]] = []
    except Exception as exc:                                   # noqa: BLE001
        hist = None
        _p(f"⚠️ 로더 import 실패({type(exc).__name__}) — 배치·규약만 점검")

    bad_unit, late, no_rule = [], [], []
    for s in LIQ_SERIES:
        sid, unit = s["id"], s.get("unit", "")
        cat = s.get("category", "—")
        pts = (hist or {}).get(sid) or []
        latest = pts[-1] if pts else None
        val = latest[1] if latest else None
        asof = latest[0] if latest else ""

        # ② 단위 상식 범위
        umsg = ""
        if val is not None and sid in _SANE:
            lo, hi, why = _SANE[sid]
            scaled = abs(val * _MULT.get(unit, 1.0))
            if not (lo <= scaled <= hi):
                umsg = f"  ❌ 단위 의심({why}) — 표기 {_fmt(val, unit)}"
                bad_unit.append(f"{sid} {_fmt(val, unit)} ({why})")

        # ③ 신선도
        if sid not in CADENCE:
            verdict = "⚪ 규약 없음"
            no_rule.append(sid)
        elif not asof:
            verdict = "⚪ 값 없음(수집 실패·키 없음)"
        else:
            j = judge(sid, asof)
            if j is None or j["freq"] == "E":
                verdict = "⚪ 이벤트성"
            elif j["stale"]:
                verdict = f"❌ 지연(기대 {j['expected']})"
                late.append(f"{sid} {asof}")
            else:
                verdict = "✅ 최신"

        _p(f"  {sid:20} {cat:12} {unit:9} "
           f"{(_fmt(val, unit) if val is not None else '—'):>12} "
           f"{asof:10} {verdict}{umsg}")
        # ⚠️ 문제 있는 항목은 **판단 재료를 그 자리에** 남긴다 — 다음 턴에
        # 또 물어보지 않아도 되게(실수 #12: 확인 명령 왕복 줄이기).
        if umsg:
            _p(f"       ↪ 원시값 {val:,} · 배수 {_MULT.get(unit, 1):g} "
               f"(원시가 다른 단위면 카탈로그 unit 을 고쳐야 한다)")
        if verdict.startswith("❌ 지연"):
            _p(f"       ↪ 최근 관측 {[d for d, _v in pts[-4:]]}")
            # ⚠️ 관측치만 보면 '우리 수집이 끊긴 것'과 '원천이 늦는 것'이
            # 똑같이 보인다 — FRED 가 스스로 보고하는 observation_end 로
            # 가른다(2026-08-19 FDHBFIN. 추측 대신 원천 자백).
            meta = _series_meta(sid)
            if meta is None:
                _p("       ↪ 원천 메타 조회 실패(키 부재·네트워크) — 판정 보류")
            else:
                oe = meta.get("observation_end", "?")
                lu = (meta.get("last_updated") or "?")[:19]
                same = oe == asof
                _p(f"       ↪ 원천 observation_end={oe} · last_updated={lu}"
                   f" → {'**원천이 여기까지밖에 없다**(규약을 늘린다)' if same else '**우리 수집이 뒤처졌다**(캐시·수집 경로 점검)'}")

    _p("")
    for _ln in summary_lines(bad_unit, late, no_rule):
        _p(_ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
