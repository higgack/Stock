#!/usr/bin/env python3
"""피어 목록 **통화 불일치** 감사 — 읽기 전용·LLM 0·₩0.

사용자 2026-08-18: SK하이닉스 동종비교의 TSM 행 PBR 이 **89.88** 로 떴다.
같은 회사 홈상장(2330.TW)은 **9.575** 다 — yfinance 가 ADR 에 가격은 USD,
재무는 TWD 로 주기 때문이다. PBR·PSR·EV/EBITDA 는 재무제표에서 나온 분모를
거래통화 가격으로 나눈 값이라, 통화가 다르면 배수가 통째로 틀린다.

화면의 ⚠ 는 "오차가 있다"고만 알리지, 9배 틀린 숫자는 그대로 보여준다.
사용자 판단으로 **홈상장으로 교체**하기로 했는데 — 어느 종목이 그런지는
내가 외워서 적으면 안 된다(실수 #12 '사전지식 stale'). 목록 전체를 실제로
조회해서 **불일치를 실측**한다.

무엇을 하나:
  ① 전 시장 피어 목록(KR·JP·TW·CN_A·HK·US)의 모든 티커를 .info 조회
  ② `financialCurrency != currency` 인 행을 전부 보고
  ③ 레포의 영문명 별칭표(`_JP_ENGLISH_ALIAS` 등, 이미 큐레이션된 데이터)
     에서 같은 회사의 **홈상장 후보**를 찾아 함께 제시 — 후보가 실제로
     조회되고 통화가 일치하는지까지 확인한다(교체 전 근거).

⚠️ 이 스크립트는 **아무것도 고치지 않는다.** 출력이 교체의 근거다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.peer_currency_audit
    cd ~/stock && .venv/bin/python -m bot.scripts.peer_currency_audit KR US

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
⚠️ 수백 종목 조회라 수 분 걸린다. `| tee` 로 받아도 라인 버퍼링된다.
"""
from __future__ import annotations

import re
import sys
import time

# 이 스크립트의 버전. ⚠️ 시작 배너에 찍는다 — 배포 전 코드로 돌린 출력을
# 새 결과인 줄 알고 분석하면 없는 문제를 쫓게 된다(2026-08-18 실측: `↪`·
# 재시도 블록이 통째로 없는 출력을 받아 원인을 한참 찾았다).
_AUDIT_VER = 4

# 종목 간 간격(초). ⚠️ 없으면 **레이트리밋 벽**에 부딪힌다 — 671종목 무지연
# 실행에서 [623]번째부터 끝까지 49종목이 연속 실패했다(STX·TSLA·WMT·XOM
# 처럼 명백히 살아있는 종목들). 그걸 '죽은 티커'로 보고하면 목록을 망친다.
_DELAY = 0.4
# 재시도 전 대기. 레이트리밋 창이 풀릴 시간을 준다 — 벽에 부딪힌 직후
# 곧바로 재조회하면 재시도도 같이 실패해 판별이 안 된다.
_COOLDOWN = 90

_MAPS = (("KR", "_KR_INDUSTRY_PEERS"), ("JP", "_JP_INDUSTRY_PEERS"),
         ("TW", "_TW_INDUSTRY_PEERS"), ("CN", "_CN_A_INDUSTRY_PEERS"),
         ("HK", "_HK_INDUSTRY_PEERS"), ("US", "_US_INDUSTRY_PEERS"))
_ALIAS = ("_KR_ENGLISH_ALIAS", "_JP_ENGLISH_ALIAS",
          "_TW_ENGLISH_ALIAS", "_CN_ENGLISH_ALIAS")


def _tickers(market: str) -> dict[str, list[str]]:
    """{티커: [등장한 업종…]} — 같은 티커가 여러 업종에 있으면 한 번만 조회."""
    from bot import market as m
    out: dict[str, list[str]] = {}
    for label, attr in _MAPS:
        if market and label != market:
            continue
        for ind, peers in (getattr(m, attr, None) or {}).items():
            for t in peers:
                out.setdefault(t, []).append(f"{label}/{ind}")
    return out


def _home_candidates(name: str, self_ticker: str = "") -> list[str]:
    """레포의 별칭표에서 회사명이 겹치는 홈상장 티커. 내 기억이 아니라
    이미 큐레이션된 레포 데이터를 쓴다."""
    from bot import market as m
    key = re.sub(r"[^A-Z]", "", (name or "").upper())[:8]
    if len(key) < 6:
        return []
    hits: list[str] = []
    hits.extend(_EXTRA_CANDIDATES.get(self_ticker, []))
    for attr in _ALIAS:
        for alias, tk in (getattr(m, attr, None) or {}).items():
            a = re.sub(r"[^A-Z]", "", alias.upper())
            # ⚠️ 짧은 쪽이 긴 쪽의 **접두사**여야 한다. 앞 5글자만 보면
            # TAIWANSEMI 와 TAIWANMOBILE 이 같은 회사로 붙는다(실측).
            short, long = sorted((a, key), key=len)
            # ⚠️ 자기 자신은 후보가 아니다 — 별칭표가 같은 티커를 가리키면
            # "후보 부적합"만 잔뜩 찍혀 진짜 후보가 묻힌다(감사 실측).
            if (tk and tk != self_ticker and len(short) >= 6
                    and long.startswith(short) and tk not in hits):
                hits.append(tk)
    return hits[:3]


# ⚠️ 별칭표에 없는 **제안** 후보. 내 기억이라 그 자체로는 근거가 아니다 —
# 도구가 실제로 조회해서 이름·통화를 찍고, `✅ 교체 가능` 이 떠야만 반영한다
# (2026-08-18 감사에서 DNZOY=Denso ADR 이 USD 거래·JPY 재무로 PSR 0.0039,
#  150배 오차로 잡혔는데 별칭표엔 Denso 항목이 없었다).
_EXTRA_CANDIDATES = {
    "DNZOY": ["6902.T"],       # Denso — 홈상장 도쿄 (2026-08-18 ✅ 확인 후 반영)
    "BLL": ["BALL"],           # Ball Corp — 티커 변경 추정(도구가 확인한다)
}


def _info(yf, t: str) -> tuple[dict, bool]:
    """→ (info, 심볼없음). ⚠️ 두 번째 값이 **핵심 판별자**다.

    yfinance 는 없는 심볼엔 `Quote not found for symbol: X` 를 찍고, 레이트
    리밋엔 그냥 빈 응답을 준다 — 호출 결과만 보면 둘이 똑같이 `{}` 다.
    2026-08-18 실측: 3회 연속 실패 17종목 안에 CRM·MU·MDT·EL 처럼 명백히
    살아있는 종목이 섞여 있었다. 그 목록을 믿고 지웠으면 멀쩡한 피어를
    날렸다. 출력을 가로채 404 를 본 것만 '심볼 없음'으로 못박는다."""
    import contextlib
    import io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            pi = yf.Ticker(t).info or {}
    except Exception:
        pi = {}
    txt = buf.getvalue()
    sys.stdout.write(txt)
    return pi, (f"symbol: {t}" in txt or "Quote not found" in txt)


def _info_resolved(yf, t: str) -> tuple[dict, str, bool]:
    """수집기와 **같은 보드 폴백**을 적용한 조회 → (info, 실제 티커, 심볼없음).

    ⚠️ 이게 없으면 `240810.KS`(원익IPS·실제 코스닥)처럼 수집기는 멀쩡히
    처리하는 종목이 감사에서만 '조회 실패'로 찍혀, 죽은 티커와 구분이 안 된다."""
    from bot.stock_snapshot import _BOARD_ALT
    pi, nf = _info(yf, t)
    if not pi.get("marketCap") and "." in t:
        alt_sfx = _BOARD_ALT.get(t.rsplit(".", 1)[-1].upper())
        if alt_sfx:
            alt = t.rsplit(".", 1)[0] + "." + alt_sfx
            alt_pi, alt_nf = _info(yf, alt)
            if alt_pi.get("marketCap"):
                return alt_pi, alt, False
            nf = nf and alt_nf      # 양쪽 보드 모두 404 여야 '심볼 없음'
    return pi, t, nf


def audit(market: str = "") -> int:
    import yfinance as yf

    from bot.stock_snapshot import norm_cur
    from bot.stock_snapshot import _PEER_SCHEMA_VER
    items = _tickers(market)
    # ⚠️ 배너로 **어느 코드로 돈 출력인지** 못박는다. 배포 전 스크립트의
    # 출력을 새 결과로 착각하면 이미 고친 문제를 다시 쫓는다.
    print(f"■ 감사 v{_AUDIT_VER} · 피어스키마 v{_PEER_SCHEMA_VER} · "
          f"간격 {_DELAY}s · 재시도 대기 {_COOLDOWN}s")
    print(f"■ 대상 {len(items)}종목" + (f" ({market})" if market else " (전 시장)")
          + f" — 예상 {int(len(items) * _DELAY / 60) + 1}분+")
    bad = 0
    failed: list[tuple[str, list[str]]] = []
    # 티커별로 "매번 404 를 봤는가". 한 번이라도 조용히 실패한 적이 있으면
    # 심볼 없음이라 단정하지 않는다 — 지우면 되돌릴 수 없다.
    nf_seen: dict[str, bool] = {}
    for i, (t, where) in enumerate(sorted(items.items()), 1):
        time.sleep(_DELAY)
        pi, rt, _nf = _info_resolved(yf, t)
        if not pi.get("marketCap"):
            nf_seen[t] = _nf
            # ⚠️ 1차 실패를 **그 자리에서** 찍는다. 조용히 모으기만 하면
            # 레이트리밋 벽이 몇 번째부터 시작됐는지 알 수가 없어, 나중에
            # 나오는 실패 목록이 죽은 티커인지 벽인지 판별이 안 된다
            # (2026-08-18 실측: 372종목 목록만 보고는 못 갈랐다).
            failed.append((t, where))
            print(f"[{i:3d}/{len(items)}] {t:<12} … 1차 실패(재시도 대상)")
            continue
        if rt != t:
            print(f"[{i:3d}/{len(items)}] {t:<12} ↪ {rt} (보드 폴백 — 수집기는 정상)")
        cur, fin = norm_cur(pi.get("currency")), norm_cur(pi.get("financialCurrency"))
        if not cur or not fin or cur == fin:
            continue
        bad += 1
        nm = (pi.get("shortName") or "")[:28]
        print(f"[{i:3d}/{len(items)}] {rt:<12} ⚠ {nm:<28} "
              f"거래={cur} 재무={fin} PBR={pi.get('priceToBook')} "
              f"PSR={pi.get('priceToSalesTrailing12Months')}")
        print(f"              쓰이는 곳: {', '.join(where)}")
        cands = _home_candidates(nm, rt)
        for c in cands:
            ci, _, _ = _info_resolved(yf, c)
            ccur, cfin = norm_cur(ci.get("currency")), norm_cur(ci.get("financialCurrency"))
            ok = "✅ 교체 가능" if ci.get("marketCap") and ccur == cfin else "❌ 후보 부적합"
            print(f"              후보 {c:<12} {ok} {(ci.get('shortName') or '?')[:20]:<20} "
                  f"거래={ccur or '—'} 재무={cfin or '—'} PBR={ci.get('priceToBook')}")
        if not cands:
            print("              후보 없음 — 홈상장이 미지원 시장이면 그대로 둔다"
                  "(⚠ 표시 + 범위 가드가 남는다).")

    # ⚠️ 실패는 **한 번 더** 본다. 671종목을 연달아 두드리면 yfinance 가
    # 레이트리밋으로 빈 응답을 준다 — 그걸 '죽은 티커'로 보고하면 멀쩡한
    # 종목을 목록에서 빼게 된다(HD·CRM·TGT 가 실패로 찍혔던 이유).
    # 2회차에도 실패하면 그건 진짜 안 되는 티커다.
    dead: list[tuple[str, list[str]]] = list(failed)
    for rnd in (1, 2):
        if not dead:
            break
        print(f"\n■ 재시도 {rnd}차 — {len(dead)}종목 "
              f"({_COOLDOWN}초 대기 후, 레이트리밋 vs 죽은 티커 판별)")
        time.sleep(_COOLDOWN)
        still: list[tuple[str, list[str]]] = []
        for t, where in dead:
            time.sleep(_DELAY * 2)
            pi, rt, _nf = _info_resolved(yf, t)
            nf_seen[t] = nf_seen.get(t, True) and _nf
            if pi.get("marketCap"):
                print(f"  {t:<12} ✅ 성공 — 앞선 실패는 레이트리밋"
                      + (f" (↪ {rt})" if rt != t else ""))
            else:
                still.append((t, where))
        dead = still

    print(f"\n■ 통화 불일치 {bad}종목 / {len(items)}종목")
    if not bad:
        print("  전부 일치 — 교체할 것이 없다.")
    # ⚠️ 두 통을 **갈라서** 보고한다. 섞으면 레이트리밋 종목을 지운다
    # (2026-08-18 실측: 실패 17종목 안에 CRM·MU·MDT·EL 이 섞여 있었다).
    gone = [(t, w) for t, w in dead if nf_seen.get(t)]
    unknown = [(t, w) for t, w in dead if not nf_seen.get(t)]
    print(f"■ 심볼 없음 확정 {len(gone)}종목 — 매 시도마다 404"
          " → **목록에서 빼거나 고칠 것**")
    for t, where in gone:
        print(f"  {t:<12} {', '.join(where)}")
    if not gone:
        print("  없음.")
    print(f"■ 원인 불명 {len(unknown)}종목 — 404 를 못 봤다(레이트리밋 가능)"
          " → **건드리지 말 것**")
    for t, where in unknown:
        print(f"  {t:<12} {', '.join(where)}")
    if not unknown:
        print("  없음.")
    return 0


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    global _DELAY
    args = []
    for a in argv[1:]:
        if a.startswith("--sleep="):
            _DELAY = float(a.split("=", 1)[1])
        else:
            args.append(a.upper())
    for mk in (args or [""]):
        audit(mk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
