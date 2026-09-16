#!/usr/bin/env python3
"""KRX 애프터마켓 · 거래량 상위 보드를 **배선하기 전에 재는** 프로브 — 읽기 전용·₩0.

사용자 2026-09-16: "이제 장후에 NXT 뿐만 아니라 KRX 도 장이 열려 … KRX 도
'장후' 가장 많이 오른 30/떨어진 30 … 네이버와 같은 형식으로 거래량상위 …"
(근거: 네이버증권 공지 153 — KRX 애프터마켓 16:00~20:00 신설).

⚠️ **엔드포인트·필드 이름을 추측해 배선하면 죽은 경로를 배포한다**(#151·#345).
샌드박스는 네이버에 못 닿으므로(ProxyError 실측) 여기서는 **재기만** 하고
배선은 이 출력을 보고 한다.

무엇을 재나
  ① **대조군** — 이미 동작을 증명한 호출(`domestic/stock/list?sortType=up`).
     이게 죽으면 아래 0건은 '없다' 가 아니라 '이 VM 이 못 닿는다' 이다(#143).
  ② **거래량 상위의 정렬 키** — 목록을 우리가 적지 않는다. 미끼 값을 보내
     원천이 스스로 적어 보내는 **허용값 목록**을 읽는다(#350·#353 에서
     테마 보드가 같은 방식으로 상한 200 을 알아냈다). 그다음 거래량계로
     보이는 후보를 **실호출**해 행 수·표본을 찍는다.
  ③ **행이 네이버 화면의 칼럼을 다 들고 오나** — 종목명·현재가·전일대비·
     거래량·거래대금·고가·저가·시총. 키를 **자르지 않고 전부** 찍는다
     (자르는 자리가 다음 결정을 가린다, #156·#338).
  ④ **KRX 애프터마켓이 NXT 와 구별되나** — 이게 이번 라운드의 급소다.
     `overMarketPriceInfo` 는 2026-06 에 'KR 시간외 = NXT' 이던 시절 측정한
     것이고, KRX 애프터마켓이 생긴 지금 그 블록이 어느 거래소인지는 **재지
     않았다**(#165). 그래서 폴링 응답의 **전 키**와 over/nxt/market 이 든
     하위 블록을 통째로 찍어 venue 축이 있는지 본다.

⚠️ 읽기 전용 — 운영 캐시를 **읽지도 쓰지도** 않는다(진단이 자기가 읽을
신호를 오염시키면 안 된다, #30·#264·#283·#321). 네이버를 직접 친다.

실행:
    cd ~/stock && .venv/bin/python -m bot.scripts.kr_board_probe

⚠️ 반드시 `cd ~/stock` + `.venv/bin/python` — 홈에서 돌리면 `python -m` 이
패키지를 못 찾고(#278), 시스템 python3 은 의존성이 없다.
"""
from __future__ import annotations

import json
import sys

_PROBE_VER = 1

_H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/125.0.0.0 Safari/537.36"),
      "Accept": "application/json", "Referer": "https://m.stock.naver.com/"}
_LIST = "https://m.stock.naver.com/front-api/domestic/stock/list"
_POLL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
# 표본 종목 — 대형(코스피)·중형(코스닥). 애프터마켓 체결이 실제로 붙는지
# 보려면 유동성이 있는 종목이어야 한다.
_CODES = ("005930", "000660", "247540")


def _get(url, **params):
    from bot import naver_diag as nd
    import logging
    return nd.get_json(url, headers=_H, log=logging.getLogger("kr_board_probe"),
                       tag="probe", params=params or None)


def _rows(payload):
    """front-api 응답에서 종목 배열 — 모양이 갈리므로 둘 다 본다."""
    if isinstance(payload, dict):
        r = payload.get("result")
        if isinstance(r, dict):
            for k in ("stocks", "list", "items"):
                if isinstance(r.get(k), list):
                    return r[k]
        if isinstance(r, list):
            return r
        for k in ("stocks", "list", "items"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _banner():
    print(f"kr_board_probe v{_PROBE_VER} · 인터프리터 {sys.executable}")
    try:
        import requests  # noqa: F401
    except Exception as exc:                                # noqa: BLE001
        print(f"❌ 의존성 없음: {type(exc).__name__}: {exc} — "
              "`.venv/bin/python` 으로 다시 돌려 주세요.")
        return False
    return True


def _section_control():
    print("\n① 대조군 — 이미 동작을 증명한 호출")
    d, why = _get(_LIST, sortType="up", category="all", page=1, pageSize=5)
    rows = _rows(d)
    if rows:
        print(f"   ✅ sortType=up → {len(rows)}행 (네이버 도달 정상)")
        return True
    print(f"   ❌ sortType=up 이 0행 — {why or '사유 없음'}")
    print("   ⇒ 아래 0건은 '원천에 없다' 가 아니라 **판정 불가**입니다(#143).")
    return False


def _section_sorts():
    print("\n② 거래량 상위의 정렬 키 — 허용값을 원천에게 묻는다")
    from bot.naver_sector_client import allowed_values, list_truncated
    _d, why = _get(_LIST, sortType="__probe__", category="all", page=1, pageSize=5)
    vals = allowed_values(why)
    if not vals:
        if list_truncated(why):
            print("   ❌ 허용값 목록이 사유 길이 제한에 잘렸습니다 — "
                  "`naver_diag._sanitize` 한도를 올려 다시 재야 합니다.")
        else:
            print(f"   ❌ 허용값을 못 읽었습니다 — {why or '거절되지 않음'}")
        return ()
    print(f"   ✅ 원천이 밝힌 허용값 {len(vals)}종: {', '.join(vals)}")
    return vals


def _section_rows(vals):
    print("\n③ 후보 정렬 키 실호출 — 행 수와 **전 키**")
    # 거래량/거래대금계로 **보이는** 것만 시험한다 — 어느 것이 맞는지는
    # 이름이 아니라 실호출 결과가 정한다(#25).
    cands = [v for v in vals
             if any(w in v.lower() for w in ("volume", "trade", "amount", "value"))]
    if not cands:
        print("   ⚠️ 허용값에 거래량계로 보이는 키가 없습니다 — 전 키를 시험합니다.")
        cands = list(vals)
    for v in cands[:8]:
        d, why = _get(_LIST, sortType=v, category="all", page=1, pageSize=5)
        rows = _rows(d)
        if not rows:
            print(f"   · {v:<22} 0행 — {why or '사유 없음'}")
            continue
        print(f"   · {v:<22} {len(rows)}행")
        first = rows[0] if isinstance(rows[0], dict) else {}
        print(f"       전 키: {', '.join(sorted(first))}")
        print(f"       표본: {json.dumps(first, ensure_ascii=False)[:600]}")


def _walk(obj, path=""):
    """venue 축으로 보이는 키를 전부 모은다 — 이름을 우리가 고르지 않는다.

    ⚠️ v1 은 dict·list 값을 `type(v).__name__` 으로 접어 찍었다. 2026-09-16
    VM 실측에서 그게 정확히 **다음 결정을 가렸다** — `stockExchangeType =
    dict` · `integratedPriceInfo`(이름 필터에도 안 걸림)가 접혀, venue 축이
    거기 있는지 없는지 판정할 수 없었는데 출력만 보면 '없다'로 읽혔다
    (#156·#338·#350 자르는 자리가 다음 결정을 가리지 않는가 · #165 재지 않은
    것을 단정하지 말 것). 이제 **접지 않고 통째로** 찍는다.
    """
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            lk = k.lower()
            if any(w in lk for w in ("nxt", "over", "market", "session", "venue",
                                     "exchange", "integrated", "krx")):
                hits.append((p, v if not isinstance(v, (dict, list))
                             else json.dumps(v, ensure_ascii=False)))
            hits += _walk(v, p)
    return hits


def _section_venue():
    print("\n④ KRX 애프터마켓이 NXT 와 구별되나 — 폴링 응답 전수")
    for code in _CODES:
        d, why = _get(_POLL.format(code=code))
        datas = (d or {}).get("datas") if isinstance(d, dict) else None
        if not datas:
            print(f"   · {code} 0행 — {why or '사유 없음'}")
            continue
        it = datas[0]
        print(f"   · {code} 전 키: {', '.join(sorted(it))}")
        for p, v in _walk(it):
            print(f"       {p} = {v}")
        om = it.get("overMarketPriceInfo")
        if isinstance(om, dict):
            print(f"       overMarketPriceInfo 원문: "
                  f"{json.dumps(om, ensure_ascii=False)}")
        else:
            print("       overMarketPriceInfo 없음 — 이 시각엔 시간외 블록이 "
                  "안 붙습니다(장중/장 완전 종료).")


def main() -> int:
    if not _banner():
        return 2
    from bot.kr_session import now_kst, phase, window_label
    t = now_kst()
    print(f"\n지금 {t:%Y-%m-%d %H:%M} KST · KRX {phase('KRX', t)[1]} · "
          f"NXT {phase('NXT', t)[1]}")
    print(f"   KRX 체결 창 {window_label('KRX')}")
    print(f"   NXT 체결 창 {window_label('NXT')}")
    print("   ⚠️ ④ 는 **애프터마켓 창 안**(16:00~20:00 KST)에서 돌려야 "
          "의미가 있습니다 — 창 밖이면 시간외 블록이 안 붙습니다.")
    ok = _section_control()
    vals = _section_sorts()
    if vals:
        _section_rows(vals)
    else:
        # ⚠️ 조용히 건너뛰면 마지막 줄이 **안 한 일을 했다고** 말한다
        # (#54 대조 0건은 통과가 아니다 · #286 도구가 자기 자신에 대해 사실
        # 아닌 것을 말하지 말 것). 건너뛴 사실을 그 자리에 적는다.
        print("\n③ 후보 정렬 키 실호출 — ⏭ 건너뜀(② 가 허용값을 못 읽어 "
              "시험할 후보가 없습니다)")
    _section_venue()
    done = "②③" if vals else "②"
    print(f"\n판정은 사람이 합니다 — 위 {done} 이(가) 거래량 보드의 정렬 키를, "
          "④ 가 KRX/NXT 구별 가능 여부를 정합니다.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
