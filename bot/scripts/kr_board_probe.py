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
    print("\n③ 후보 정렬 키 실호출 — 제품과 **같은 선택기·같은 판정**")
    # ⚠️ v1 은 여기서 `("volume","trade","amount","value")` 이름 필터를 자체로
    # 갖고 있었다. 실측 허용값에서 그건 `marketValue`(='value' 포함) **하나만**
    # 고르고 — 정작 `quantTop` 은 한 번도 안 불렀다. 제품에서 #46·#291 을 이유로
    # 걷어낸 바로 그 이름 판정이 계측에만 남아 있었고, 그래서 감사와 화면이
    # 다른 선택기를 쓰고 있었다(#35 감사는 화면이 쓰는 그 선택기를 부를 것).
    # 이제 제품 함수를 그대로 부르고 판정도 제품의 `is_volume_desc` 가 한다.
    import bot.kr_volume_client as kv
    cands = list(kv.volume_sort_candidates(tuple(vals)))[:kv._TRIAL_BUDGET]
    print(f"   시험 순서(제품 volume_sort_candidates, 예산 {kv._TRIAL_BUDGET}): "
          f"{', '.join(cands)}")
    for v in cands:
        d, why = _get(_LIST, sortType=v, category="all", page=1,
                      pageSize=kv._TRIAL_ROWS)
        rows = _rows(d)
        if not rows:
            print(f"   · {v:<22} 0행 — {why or '사유 없음'}")
            continue
        # 판정을 여기서 재구현하면 제품과 갈라진다(#35·#169).
        verdict = kv.is_volume_desc(rows)
        mark = "✅ 거래량 내림차순" if verdict else "❌ 거래량 내림차순 아님"
        print(f"   · {v:<22} {len(rows)}행 — {mark}")
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
    elif isinstance(obj, list):
        # ⚠️ v2 는 dict 만 재귀해 **리스트 안**을 못 봤다(독립 리뷰 2026-09-17
        # 실측: `{"overMarketPriceInfoList":[{"nxtVenue":"NXT"}]}` → 경로 0건).
        # 이름으로 가르는 필드가 실제로 있는데 '없음'이라 찍으면 그게 바로
        # #165(재지 않은 것을 단정)이고, 같은 출력의 위쪽 덤프와도 모순된다.
        for i, v in enumerate(obj):
            hits += _walk(v, f"{path}[{i}]")
    return hits


def _n(v):
    """'16,386,371' · 16386371 → float | None (순수)."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def venue_axis_paths(item: dict) -> list:
    """응답 안에서 **거래소를 이름으로 가르는** 경로(순수). 없으면 [].

    ⚠️ 이건 '거래소 축이 없다' 의 **필요조건**일 뿐이다 — 이름 없이 **값으로만**
    가르는 필드(`marketSessionType: "NXT_AFTER"` 류)가 있을 수 있고, 다른
    엔드포인트는 재지 않았다(#165·#274 이 검사가 못 보는 축). 그래서 호출부는
    "이 응답엔 **이름으로** 가르는 필드가 없다" 까지만 말한다.
    리스트 안 중첩은 `_walk` 가 재귀하므로 본다(독립 리뷰 2026-09-17).
    """
    return [(p, v) for p, v in _walk(item)
            if "nxt" in p.lower() or "krx" in p.lower()]


def composition_check(item: dict) -> tuple[str, str]:
    """(판정키, 사람이 읽는 줄) — `통합 = 본체 + 시간외` 인가(순수).

    2026-09-17 VM 실측에서 삼성전자·SK하이닉스의 **거래량·거래대금 네 쌍이
    원 단위까지** 이 항등식을 만족했다(11,438,019 + 4,948,352 = 16,386,371 등).
    즉 `integratedPriceInfo` 는 '거래소 통합' 이 아니라 **본체 + 시간외 합산**
    으로 읽힌다.

    ⚠️ 그렇다고 거래소를 주장하지는 않는다 — '본체' 가 KRX 정규장인지 KRX
    전체인지 이 산수로는 못 가른다(두 가설이 같은 수치를 낸다, #255). 이
    함수는 **항등식 성립 여부**만 말한다.
    """
    reg = _n(item.get("accumulatedTradingVolumeRaw")
             or item.get("accumulatedTradingVolume"))
    om = item.get("overMarketPriceInfo")
    ip = item.get("integratedPriceInfo")
    if not isinstance(om, dict) or not isinstance(ip, dict):
        return ("unmeasurable", "시간외·통합 블록이 없어 구성 검산 불가(판정 불가)")
    over = _n(om.get("accumulatedTradingVolumeRaw")
              or om.get("accumulatedTradingVolume"))
    integ = _n(ip.get("accumulatedTradingVolumeRaw")
               or ip.get("accumulatedTradingVolume"))
    if reg is None or over is None or integ is None:
        return ("unmeasurable", "거래량 셋 중 하나를 못 읽어 구성 검산 불가(판정 불가)")
    got = reg + over
    mark = "✅" if abs(got - integ) < 1 else "❌"
    return (("ok" if mark == "✅" else "mismatch"),
            f"{mark} 구성 검산 본체 {reg:,.0f} + 시간외 {over:,.0f} = {got:,.0f}"
            f" vs 통합 {integ:,.0f}")


_VENUE_MISMATCH: list = []   # ④ 구성 검산이 어긋난 종목 — 마지막 줄이 말한다


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
        vp = venue_axis_paths(it)
        om = it.get("overMarketPriceInfo")
        if vp:
            print("       거래소를 이름으로 가르는 경로: "
                  + ", ".join(p for p, _v in vp))
        elif isinstance(om, dict):
            print("       거래소를 이름으로 가르는 경로: **없음**"
                  "(이 응답 기준 — 다른 엔드포인트는 안 쟀다)")
        else:
            # ⚠️ 시간외 블록이 안 붙은 응답에서 '없음' 을 같은 확신으로 찍으면
            # 대표성 없는 관측이 확인으로 읽힌다(#41 여유·우연으로 사실을 덮지
            # 말 것 · #54 대조 0건은 통과가 아니다).
            print("       거래소 축: 판정 불가 — 시간외 블록이 안 붙었다(창 밖)")
        key, line = composition_check(it)
        if key == "mismatch":
            _VENUE_MISMATCH.append(code)
        print("       " + line)
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
    if _VENUE_MISMATCH:
        # ⚠️ 판정키를 계산해 놓고 마지막 줄에 안 실으면 없는 것과 같다
        # (#123 계열) — ❌ 는 rc 에도 실린다(#54).
        print("   ❌ ④ 구성 검산 불일치: "
              + ", ".join(_VENUE_MISMATCH)
              + " — `통합 = 본체 + 시간외` 가 깨졌습니다(원천 구조 변경 의심).")
    return 0 if (ok and not _VENUE_MISMATCH) else 1


if __name__ == "__main__":
    raise SystemExit(main())
