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


def _home_candidates(name: str) -> list[str]:
    """레포의 별칭표에서 회사명이 겹치는 홈상장 티커. 내 기억이 아니라
    이미 큐레이션된 레포 데이터를 쓴다."""
    from bot import market as m
    key = re.sub(r"[^A-Z]", "", (name or "").upper())[:8]
    if len(key) < 6:
        return []
    hits: list[str] = []
    for attr in _ALIAS:
        for alias, tk in (getattr(m, attr, None) or {}).items():
            a = re.sub(r"[^A-Z]", "", alias.upper())
            # ⚠️ 짧은 쪽이 긴 쪽의 **접두사**여야 한다. 앞 5글자만 보면
            # TAIWANSEMI 와 TAIWANMOBILE 이 같은 회사로 붙는다(실측).
            short, long = sorted((a, key), key=len)
            if tk and len(short) >= 6 and long.startswith(short) and tk not in hits:
                hits.append(tk)
    return hits[:3]


def _info(yf, t: str) -> dict:
    try:
        return yf.Ticker(t).info or {}
    except Exception:
        return {}


def audit(market: str = "") -> int:
    import yfinance as yf

    from bot.stock_snapshot import norm_cur
    items = _tickers(market)
    print(f"■ 대상 {len(items)}종목" + (f" ({market})" if market else " (전 시장)"))
    bad = 0
    for i, (t, where) in enumerate(sorted(items.items()), 1):
        pi = _info(yf, t)
        cur, fin = norm_cur(pi.get("currency")), norm_cur(pi.get("financialCurrency"))
        if not pi.get("marketCap"):
            print(f"[{i:3d}/{len(items)}] {t:<12} ❌ 조회 실패 — {', '.join(where)}")
            continue
        if not cur or not fin or cur == fin:
            continue
        bad += 1
        nm = (pi.get("shortName") or "")[:28]
        print(f"[{i:3d}/{len(items)}] {t:<12} ⚠ {nm:<28} "
              f"거래={cur} 재무={fin} PBR={pi.get('priceToBook')} "
              f"PSR={pi.get('priceToSalesTrailing12Months')}")
        print(f"              쓰이는 곳: {', '.join(where)}")
        for c in _home_candidates(nm):
            ci = _info(yf, c)
            ccur, cfin = norm_cur(ci.get("currency")), norm_cur(ci.get("financialCurrency"))
            ok = "✅ 교체 가능" if ci.get("marketCap") and ccur == cfin else "❌ 후보 부적합"
            print(f"              후보 {c:<12} {ok} 거래={ccur or '—'} "
                  f"재무={cfin or '—'} PBR={ci.get('priceToBook')}")
        if not _home_candidates(nm):
            print("              후보 없음 — 홈상장이 미지원 시장이면 그대로 둔다"
                  "(⚠ 표시 + 범위 가드가 남는다).")
    print(f"\n■ 통화 불일치 {bad}종목 / {len(items)}종목")
    if not bad:
        print("  전부 일치 — 교체할 것이 없다.")
    return 0


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    args = [a.upper() for a in argv[1:]]
    for mk in (args or [""]):
        audit(mk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
