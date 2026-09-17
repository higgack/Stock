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
  ⑤ **NXT 거래 종목을 창으로 가를 수 있나**(2026-09-17 추가) — 사용자가
     "NXT 에 등록안된 기업들도 많다" 고 짚었다. 응답 **필드**로는 못 가르지만
     (④ 실측) KRX 는 체결 창이 애프터마켓뿐이라 **NXT 전용 창**(프리
     08:00–09:00 · 15:40–16:00)의 시간외 체결은 정의상 NXT 다 — 그 하한을 잰다.
     ⚠️ 이 프로브 한 방은 **그 창에 사람이 맞춰 쳐야** 답한다(4종목 표본).
     그래서 같은 날 `bot.venue_universe` 를 심어 시간외 보드 스캔(이미 그
     창에서 2분 주기로 ~200종목을 돈다)이 자동 누적하게 했고, 여기서는 그
     누적분을 **읽어서** 같이 찍는다 — 지금이 전용 창이 아니어도 하한이
     보인다.
  ④ **KRX 애프터마켓이 NXT 와 구별되나** — 이게 이번 라운드의 급소다.
     `overMarketPriceInfo` 는 2026-06 에 'KR 시간외 = NXT' 이던 시절 측정한
     것이고, KRX 애프터마켓이 생긴 지금 그 블록이 어느 거래소인지는 **재지
     않았다**(#165). 그래서 폴링 응답의 **전 키**와 over/nxt/market 이 든
     하위 블록을 통째로 찍어 venue 축이 있는지 본다.
  ⑥ **거래소 축 파라미터가 스키마에 있나**(2026-09-17 추가) — NXT 거래
     종목 **목록**을 주는 파라미터가 있는지. 이름을 지어내 배선하면 죽은
     경로를 배포하므로(#151·#345) 일부러 틀린 값을 넣어 zod 가 스스로
     말하게 한다(#64·#86). '있음' 이어도 그것만으로 배선하지 않는다 —
     그 키로 목록이 실제로 줄어드는지가 그다음 측정이다.

⚠️ 운영 캐시에 **쓰지 않는다**(진단이 자기가 읽을 신호를 오염시키면 안 된다,
#30·#264·#283·#321). 네이버는 직접 친다. ⑤ 는 2026-09-17 부터 시간외 보드
스캔이 쌓아 둔 하한 기록을 **읽는다** — 읽기는 신호를 오염시키지 않고, 그
기록이 없으면 이 프로브는 그 창에 사람이 맞춰 쳐야만 답할 수 있다(옛 판이
그랬다, §Automation-first·#252). 옛 독스트링은 "읽지도 쓰지도 않는다" 였는데
이제 거짓이므로 같은 커밋에서 고쳤다(#55·#286).

실행:
    cd ~/stock && .venv/bin/python -m bot.scripts.kr_board_probe

⚠️ 반드시 `cd ~/stock` + `.venv/bin/python` — 홈에서 돌리면 `python -m` 이
패키지를 못 찾고(#278), 시스템 python3 은 의존성이 없다.
"""
from __future__ import annotations

import json
import sys

_PROBE_VER = 4

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


def nxt_lower_bound(codes_with_over: list, venue: str, branch: str) -> str:
    """⑤ 판정(순수) — 지금 창에서 NXT 거래 종목을 **확정**할 수 있나.

    사용자 2026-09-17: "이 NXT 랑 KRX 애프터랑 안겹치는것도 많을텐데. NXT 에
    등록안된 기업들도 많기 때문에." 맞는 지적이고, 보드가 거래소로 거르지 못하는
    것은 **응답에 거래소 필드가 없기 때문**이다(#373b 실측). 그래서 같은 날
    거래소별 두 보드를 한 장으로 합쳤다(#378). 필드로는 못 가르지만 **창으로는** 갈린다 — KRX 는 체결
    창이 애프터마켓뿐이라 NXT 전용 창(프리 08:00–09:00 · 15:40–16:00)에 붙는
    시간외 체결은 정의상 NXT 다. 그 구간의 종목이 'NXT 거래 종목' 의 하한이다.

    ⚠️ 하한이지 목록이 아니다 — 그 창에 체결이 없었을 뿐인 NXT 종목이 있다
    (#54·#165 잰 범위를 빼고 말하지 말 것). 그리고 이 연역은 **원천이 밝힌
    창**(공지 153) 위에 선다(#165).
    """
    if branch == "closed":
        # ⚠️ 창을 리터럴로 적으면 `kr_session` 표와 갈라진다(#38·#55, 독립 리뷰
        # 2026-09-17 M1 — 같은 커밋에서 파생 헬퍼를 만들어 놓고 여기만 옛
        # 리터럴이었다).
        from bot.kr_session import exclusive_window_label
        return (f"⏭ 지금은 체결 창 밖 — 잴 것이 없습니다(전용 창 "
                f"{exclusive_window_label()} 에 돌리면 확정됩니다).")
    if branch == "overlap":
        # ⚠️ 2026-09-17 보드 합침(#378): 옛 문구는 "두 보드가 같은 목록을 낸다"
        # 였는데 보드가 하나라 그 문장이 없는 것을 가리킨다(#55). 남는 사실은
        # "이 보드는 거래소로 거르지 않는다" 이고 그게 운영자가 알 것이다.
        return ("❓ 지금은 KRX·NXT 체결 창이 **겹치는** 구간이라 이 블록의 "
                "거래소를 못 가릅니다 — 이 보드는 거래소로 거르지 않으므로 "
                "이 목록의 귀속도 못 가립니다(시장이 같아서가 아니라).")
    if not codes_with_over:
        return (f"❓ 지금은 {venue} 전용 창인데 표본에 시간외 체결이 하나도 "
                "안 붙었습니다 — 표본이 그 창에 거래가 없었을 뿐일 수 있습니다.")
    return (f"✅ 지금은 {venue} 전용 창이라 아래 종목의 시간외 체결은 정의상 "
            f"{venue} 입니다(하한): " + ", ".join(codes_with_over))


def _section_nxt_universe() -> None:
    print("\n⑤ NXT 거래 종목을 창으로 가를 수 있나 — 전용 창 하한")
    from bot.kr_session import exclusive_venue, now_kst
    venue, branch = exclusive_venue(now_kst())
    hit = []
    if branch != "closed":
        for code in _CODES:
            d, why = _get(_POLL.format(code=code))
            datas = (d or {}).get("datas") if isinstance(d, dict) else None
            if not datas:
                print(f"   · {code} 0행 — {why or '사유 없음'}")
                continue
            om = datas[0].get("overMarketPriceInfo")
            vol = _n((om or {}).get("accumulatedTradingVolume")) if isinstance(om, dict) else None
            print(f"   · {code} 시간외 누적거래량 = "
                  f"{'없음' if vol is None else int(vol)}")
            if vol:
                hit.append(code)
    print("   " + nxt_lower_bound(hit, venue, branch))
    print("   — 누적(보드 스캔이 자동으로 쌓은 것, 네트워크 콜 0):")
    try:
        from bot.venue_universe import format_lines
        for line in format_lines():
            print("   " + line)
    except Exception as exc:                                  # noqa: BLE001
        print(f"   ❌ 누적 기록을 못 읽었습니다: {type(exc).__name__}: {exc}")
    print("   ⚠️ NXT 거래 종목 **목록**을 주는 원천은 아직 안 쟀습니다 — "
          "이름을 추측해 배선하면 죽은 경로를 배포합니다(#151·#345).")


_VENUE_PARAMS = ("stockExchangeType", "exchange", "exchangeType", "market",
                 "marketType", "venue", "tradingVenue", "nxt",
                 "alternativeExchange")


def _section_venue_params() -> None:
    """⑥ 거래소 축 파라미터 후보 — **재기만** 한다(읽기 전용).

    사용자 결정 대기 사항이었다: 보드를 NXT 거래 종목만으로 거르려면 그
    **목록 원천**이 필요한데, 샌드박스는 네이버에 못 닿아 후보를 실호출로
    못 쟀다. 이름을 지어내 배선하면 죽은 경로를 배포한다(#151·#345) —
    zod 는 **모르는 키를 조용히 버리고**(200) 아는 키에 잘못된 값이 오면
    그 키를 이름으로 지목해 4xx 를 준다. 그래서 일부러 틀린 값을 넣으면
    원천이 스스로 스키마를 말해 준다(#64·#86·#351·#353).

    ⚠️ 이 섹션이 '있음'을 찍어도 **그것만으로 배선하지 않는다** — 그다음
    측정(그 키로 받은 목록이 실제로 줄어드나)이 답한다. 그리고 여기 쓰는
    후보 목록은 우리가 적은 것이라 **원천이 쓰는 이름이 그 밖일 수 있다**
    (#24 열거는 목록 밖을 못 잡는다) — ④ 의 전 키 덤프가 짝이다.
    """
    print("\n⑥ 거래소 축 파라미터가 스키마에 있나 — 원천에게 묻는다")
    from bot.naver_sector_client import (SKIPPED_VERDICT,
                                         abort_param_sweep,
                                         classify_param_probe,
                                         demote_shared_status, allowed_values)
    from bot import naver_diag as _nd
    base, base_why = _get(_LIST, sortType="up", category="all", page=1,
                          pageSize=20)
    # ⚠️ 200 판정은 **응답(payload)이 왔는가**로 한다 — 추출된 행의
    # truthiness 로 보면 "원천이 200 인데 0행" 이 '도달 실패' 로 찍힌다
    # (독립 리뷰 실측). 처방이 정반대인 갈래이고(#82), 무엇보다
    # `base_status` 가 None 이 되면 #352 의 '이 키에만 4xx' 분기가 통째로
    # 죽어 **이 섹션이 찾으려는 그 성공 케이스**(키가 먹혀 0행으로 걸러짐)를
    # '판정 불가' 로 닫아 버린다. 형제 `probe_params` 와 같은 규약이다(#38).
    base_ok = base is not None
    base_n = len(_rows(base)) if base_ok else None
    base_status = 200 if base_ok else _nd.status_from(base_why)
    print(f"   기준선: sortType=up · pageSize=20 → "
          + (f"{base_n}행" if base_ok else f"실패({base_why})"))
    if not base_ok:
        # 대조군이 죽으면 아래 판정은 '있음/없음' 이 아니라 **판정 불가**다
        # (#143) — 그 사실을 먼저 적는다.
        print("   ⚠️ 대조군이 실패했습니다 — 아래 결과는 판정 불가로 읽을 것")
    rows: list = []
    # ⚠️ 이어진 판정 불가가 3개면 멈춘다 — 한도·차단이 걸린 뒤 남은 후보를 더
    # 치는 것은 순손실이고 한도만 더 태운다(#279·#346·#354). 요청 모양 4xx 는
    # 우리가 찾는 신호라 여기 안 걸린다. 형제 `probe_params` 와 **같은 함수**를
    # 쓴다 — 복제하면 한쪽만 고쳐진다(#38).
    stop = ""
    for i, key in enumerate(_VENUE_PARAMS):
        if stop:
            rows.append([key, None, "", None, SKIPPED_VERDICT])
            continue
        d, why = _get(_LIST, sortType="up", category="all", page=1,
                      pageSize=20, **{key: "__probe__"})
        ok = d is not None
        status = 200 if ok else _nd.status_from(why)
        n = len(_rows(d)) if ok else None
        rows.append([key, status, why, n,
                     classify_param_probe(status, why, key, n, base_n,
                                          base_status)])
        stop = abort_param_sweep(rows)
        if i + 1 >= len(_VENUE_PARAMS):
            stop = ""
    demote_shared_status(rows)
    measured = 0
    for key, status, why, n, verdict in rows:
        if not verdict.startswith("판정 불가"):
            measured += 1
        extra = f" · {n}행(기준선 {base_n})" if (
            n is not None and base_n is not None) else ""
        print(f"   {key:<20} {verdict}{extra}"
              + (f" — {why}" if why and status != 200 else ""))
        vals = allowed_values(why) if status and status != 200 else ()
        if vals:
            print(f"       원천이 밝힌 허용값 {len(vals)}종: {', '.join(vals)}")
    if stop:
        print(f"   ⚠️ {stop}")
    if not measured:
        # 한 건도 못 쟀다 — 성공으로 집계하면 "후보에 아무것도 없다"로
        # 읽힌다(#54 대조 0건은 통과가 아니다).
        print("   ❌ 한 건도 재지 못했습니다 — 위 사유를 볼 것")
    elif not any(v.startswith("있음") for *_x, v in rows):
        # 총계(물은 수)와 소계(잰 수)는 다른 모집단이다 — 둘 다 적는다(#45).
        print(f"   ⚠️ 후보 {len(rows)}종 중 {measured}종을 쟀고 스키마에 있는 "
              "것이 없습니다 — 이 엔드포인트로는 거래소를 못 거릅니다. 우리가 "
              "적은 후보 밖의 이름일 수 있으니 ④ 의 전 키 덤프를 같이 볼 것(#24).")


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
    _section_nxt_universe()
    _section_venue_params()
    done = "②③" if vals else "②"
    print(f"\n판정은 사람이 합니다 — 위 {done} 이(가) 거래량 보드의 정렬 키를, "
          "④⑤⑥ 이 KRX/NXT 구별 가능 여부를 정합니다(⑥ 이 '있음' 을 찍어도 "
          "그것만으로 배선하지 않습니다 — 목록이 실제로 줄어드는지가 다음 "
          "측정입니다).")
    if _VENUE_MISMATCH:
        # ⚠️ 판정키를 계산해 놓고 마지막 줄에 안 실으면 없는 것과 같다
        # (#123 계열) — ❌ 는 rc 에도 실린다(#54).
        print("   ❌ ④ 구성 검산 불일치: "
              + ", ".join(_VENUE_MISMATCH)
              + " — `통합 = 본체 + 시간외` 가 깨졌습니다(원천 구조 변경 의심).")
    return 0 if (ok and not _VENUE_MISMATCH) else 1


if __name__ == "__main__":
    raise SystemExit(main())
