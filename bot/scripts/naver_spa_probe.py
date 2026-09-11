"""네이버 finance SPA 전환 — **어디서 데이터를 받아야 하는지 재는** 프로브.

2026-09-11 VM 실측: `finance.naver.com/sise/sise_group.naver` 가 121,364B 를
돌려주는데 `<table` 이 **0건**이고 스타일시트가 `.../pc/_next/st…` 였다 —
Next.js SPA 로 갈아엎혀 서버 렌더 표가 통째로 사라졌다. 업종 등락 TOP·리서치
액션 세 목록이 전부 같은 이유로 0건이다.

⚠️ 엔드포인트 **이름을 추측해 파서를 짜면 죽은 경로를 배포**한다 — 이 레포에서
반복 실패한 방식이다(#151 akshare 이름이 grep 은 통과하고 import 는 실패).
그래서 여기서는 **재기만** 한다:
  ① SPA 가 HTML 안에 데이터를 실어 보내나(App Router 의 `self.__next_f.push`)
     → 실려 있으면 새 엔드포인트가 필요 없다. 그 자리에서 파싱한다.
  ② 아니면 후보 JSON 엔드포인트를 **실호출**해 상태·모양을 찍는다(#25 능력은
     이름이 아니라 실측). 후보는 레포가 이미 동작을 증명한 패턴에서 뽑았다.

읽기 전용 — 캐시를 쓰지 않고 운영 상태를 건드리지 않는다(#264·#283·#321).
실행: cd ~/stock && .venv/bin/python -m bot.scripts.naver_spa_probe
"""
from __future__ import annotations

import json
import re
import sys

_PROBE_VER = 5        # 4 = 리서치 페이징 · 5 = 테마·상세 사다리 + 청크 발굴

_H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124.0 Safari/537.36"),
      "Accept": "application/json, text/plain, */*"}

# 화면이 죽은 그 페이지들
_PAGES = [
    ("업종 등락", "https://finance.naver.com/sise/sise_group.naver?type=upjong"),
    ("리서치 종목", "https://finance.naver.com/research/company_list.naver"),
]

# 후보 = 레포가 **이미 동작을 증명한** 호스트·모양에서 파생(추측 최소화).
#   stock.naver.com/api/foreign/market/{NAT}/upjong/list  ← CN·HK·JP 가 이걸로 산다
#   stock.naver.com/api/polling/domestic/index            ← 국내 지수가 이걸로 산다
#   api.stock.naver.com/marketindex                        ← 매크로가 이걸로 산다
# v1 VM 실측(2026-09-11)으로 **살아 있는 것 둘**이 확정됐다:
#   업종  stock.naver.com/api/domestic/market/upjong/list  → list[20]
#   리서치 m.stock.naver.com/api/research/company           → list[20]
# 나머지 넷은 400/404 로 죽었다. 여기 목록은 그 실측 결과를 반영한 것이고,
# v2 는 **이 둘의 전체 모양**(전 키·표본 행)과 형제·페이징을 잰다.
_CANDIDATES = [
    ("업종 목록", "https://stock.naver.com/api/domestic/market/upjong/list"),
    ("리서치 종목", "https://m.stock.naver.com/api/research/company"),
]

# 형제 후보 — 옛 HTML 경로 이름(company_list/industry_list/invest_list)에서
# 파생. 살아 있는 `/api/research/company` 와 **같은 자리**만 바꾼 것이라
# 지어낸 호스트가 아니다. 어느 게 실재하는지는 응답이 말한다(#25·#151).
_SIBLINGS = [
    # 국내 업종 **멤버 목록** — `_build_kr_industry_map`(종목코드→업종 한글)이
    # 아직 SPA 로 죽은 `sise_group_detail.naver` 를 훑는다. 해외판이
    # `/api/foreign/market/{NAT}/upjong/{code}/list` 로 사는 것과 **같은 자리**
    # 이므로 지어낸 주소가 아니다(#151 추측 금지 — 실재 여부는 응답이 말한다).
    # ⚠️ `{code}` 는 업종 목록 응답이 주는 값이라 프로브가 런타임에 채운다.
    ("업종 멤버(국내)", "https://stock.naver.com/api/domestic/market/upjong/"
                       "{code}/list?pageSize=5"),
    ("리서치 산업", "https://m.stock.naver.com/api/research/industry"),
    ("리서치 전략", "https://m.stock.naver.com/api/research/invest"),
    ("리서치 시황", "https://m.stock.naver.com/api/research/market"),
    ("리서치 경제", "https://m.stock.naver.com/api/research/economy"),
    ("리서치 채권", "https://m.stock.naver.com/api/research/bond"),
]

# 업종이 20개만 오는 게 **기본 페이지 크기**인지 전부인지 재야 한다 —
# 상위/하위 10 랭킹은 **전 업종**을 봐야 맞다. 20개만 보고 순위를 매기면
# 화면이 조용히 틀린다(#45 총계와 소계가 다른 모집단).
_RESEARCH_BASE = "https://m.stock.naver.com/api/research"

_PAGING = ["", "?page=1&pageSize=100", "?pageSize=100", "?size=100",
           "?page=2", "?perPage=100"]

# 업종·리서치 데이터라면 반드시 들어 있을 필드들 — 이름으로 찾지 말고
# **값이 실제로 있는지**로 본다(#25 '있다' 만 묻는 검사는 눈이 먼다).
_DATA_MARKERS = ("industryGroupKor", "fluctuationsRatio", "marketValue",
                 "업종명", "등락률", "researchId", "brokerName", "종목리서치")


def flight_payload(html: str) -> str:
    """Next.js App Router 가 HTML 에 흘려보낸 데이터 조각을 이어 붙인다(순수).

    `self.__next_f.push([1,"...json 조각..."])` 이 여러 번 나온다 — 조각 하나만
    보면 잘린 JSON 이라 마커가 경계에 걸려 안 잡힌다. 이어 붙인 뒤에 본다.
    """
    out = []
    for m in re.finditer(r'self\.__next_f\.push\(\[\d+,\s*(".*?")\]\)',
                         html, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except Exception:                                  # noqa: BLE001
            out.append(m.group(1))
    return "".join(out)


def marker_hits(text: str) -> list[tuple[str, int]]:
    """마커별 출현 수(순수). 0 건도 **찍는다** — 대조 0건은 통과가 아니다(#54)."""
    return [(k, text.count(k)) for k in _DATA_MARKERS]


def _sample(text: str, key: str, width: int = 240) -> str:
    i = text.find(key)
    if i < 0:
        return ""
    return " ".join(text[max(0, i - width // 4): i + width].split())


def _get_json(requests, url: str, timeout: int = 10):
    """(파싱된 JSON, 사유) — 실패는 **갈래를 이름으로** 말한다(#82).

    `None, 사유` 가 실패다. 빈 리스트는 실패가 아니라 '원천이 0건' 이므로
    구별해야 한다(#54 대조 0건은 통과가 아니지만 오류도 아니다).
    """
    try:
        r = requests.get(url, headers=_H, timeout=timeout)
    except Exception as exc:                               # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    ct = (r.headers.get("content-type") or "").split(";")[0]
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} · {ct or '—'}"
    if "json" not in ct:
        return None, f"JSON 아님 · {ct or '—'}"
    try:
        return r.json(), ""
    except Exception as exc:                               # noqa: BLE001
        return None, f"JSON 파싱 실패: {type(exc).__name__}"


def _paging_sweep(requests, base: str, first_key: str) -> None:
    """한 엔드포인트에 페이징 인자를 하나씩 실제로 던져 **행 수를 재서** 찍는다.

    ⚠️ 이 스윕을 업종에만 걸어 두었더니 **리서치의 창 절단을 못 쟀다**(독립
    리뷰 2026-09-11): 30일·300행을 요청해 20행을 받는데 페이징이 되는지
    아무도 재지 않았다. 같은 형제 API 면 같은 축으로 잴 것(#38·#45).
    """
    base_first = None
    for q in _PAGING:
        obj, note = _get_json(requests, base + q)
        label = q or "(무인자)"
        if obj is None:                       # 실패는 사유만 — 행수 자리에 섞지 않는다
            print(f"   · {label:24s} ❌ {note[:80]}")
            continue
        if not isinstance(obj, list):
            print(f"   · {label:24s} ⚠️ 리스트가 아님({type(obj).__name__})")
            continue
        first = (obj[0].get(first_key) if obj and isinstance(obj[0], dict) else "")
        if base_first is None:                # 무인자 기준선(_PAGING[0] == "")
            base_first = first
            print(f"   · {label:24s} → {len(obj):3d}행  첫 행={first!r}  ← 기준선")
            continue
        # ⚠️ **행 수만 보면 못 가른다** — `upjong/list` 는 `?page=2` 를 무시하고
        # 같은 20행을 돌려줬다(naver_sector_client 실측). 첫 행이 기준선과 같으면
        # 그 파라미터는 **안 먹은 것**이라고 명시적으로 찍는다(#25).
        same = bool(first) and first == base_first
        mark = "⚠️ 무시됨(첫 행 동일)" if same else "✅ 다른 쪽"
        print(f"   · {label:24s} → {len(obj):3d}행  첫 행={first!r}  {mark}")


def _first_upjong_code(requests) -> str:
    """업종 목록 응답에서 **첫 업종 코드**를 꺼낸다("" = 못 구함).

    키 이름을 추측하지 않고 후보를 훑되, 찾은 게 없으면 **빈 문자열**을 돌려
    호출부가 '판정 불가' 로 찍게 한다(#54 대조 0건은 통과가 아니다)."""
    obj, _ = _get_json(requests,
                       "https://stock.naver.com/api/domestic/market/upjong/list"
                       "?pageSize=5")
    row = obj[0] if isinstance(obj, list) and obj else None
    if not isinstance(row, dict):
        return ""
    for k in ("code", "industryCode", "upjongCode", "no", "id", "groupCode"):
        v = row.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def main(argv: list | None = None) -> int:
    import requests

    print(f"[naver_spa_probe v{_PROBE_VER}]")
    print(f"① 인터프리터: {sys.executable}")
    rc = 1
    for name, url in _PAGES:
        print(f"\n② {name} — {url}")
        try:
            r = requests.get(url, headers=_H, timeout=15)
            html = r.text
        except Exception as exc:                           # noqa: BLE001
            print(f"   ❌ 도달 실패: {type(exc).__name__}: {exc}")
            continue
        spa = "_next/" in html
        print(f"   HTTP {r.status_code} · {len(html):,}자 · "
              f"<table {html.count('<table')}건 · SPA(_next) {'예' if spa else '아니오'}")
        pay = flight_payload(html)
        print(f"   인라인 데이터(__next_f) {len(pay):,}자")
        hits = marker_hits(pay or html)
        live = [f"{k} {n}건" for k, n in hits if n]
        if live:
            print(f"   ✅ 인라인에 데이터가 있다 — {' · '.join(live)}")
            print(f"      ↪ 표본: {_sample(pay or html, next(k for k, n in hits if n))}")
            rc = 0
        else:
            print("   ❌ 인라인엔 값이 없다(껍데기만) — JSON 엔드포인트가 따로 있다")
            print(f"      ↪ 머리 200자: {(pay or html)[:200]}")

    print("\n③ 살아 있는 엔드포인트의 **전체 모양** — 키를 자르지 않는다")
    print("   (v1 은 키를 6개로 잘라 날짜 필드가 '외 N종' 에 숨었다 — #156 재발)")
    for name, url in _CANDIDATES:
        obj, note = _get_json(requests, url)
        if obj is None:
            print(f"   · {name}: ❌ {note}")
            continue
        rc = 0
        row = obj[0] if isinstance(obj, list) and obj else obj
        n = len(obj) if isinstance(obj, list) else 1
        print(f"   · {name}: ✅ {n}행")
        if isinstance(row, dict):
            print(f"     전 키({len(row)}개): {sorted(row)}")
            print(f"     표본 행: {json.dumps(row, ensure_ascii=False)[:600]}")

    print("\n④ 업종이 20개뿐인가 **페이지 크기**인가 — 랭킹은 전 업종을 봐야 맞다")
    _paging_sweep(requests, _CANDIDATES[0][1], "name")

    print("\n⑤ 리서치 형제 — 옛 company/industry/invest 세 목록에 대응하는 자리")
    for name, url in _SIBLINGS:
        if "{code}" in url:
            # 업종 코드는 **목록 응답이 주는 값**이다 — 지어내지 않는다(#151).
            code = _first_upjong_code(requests)
            if not code:
                print(f"   · {name:12s} ❓ 업종 코드를 못 구해 판정 불가 "
                      "(위 ③ 업종 목록이 실패했거나 코드 키가 없다)")
                continue
            url = url.replace("{code}", str(code))
            print(f"   · (업종 코드 {code} 로 조회)")
        obj, note = _get_json(requests, url)
        if obj is None:
            print(f"   · {name:12s} ❌ {note}")
            continue
        row = obj[0] if isinstance(obj, list) and obj else obj
        n = len(obj) if isinstance(obj, list) else 1
        keys = sorted(row) if isinstance(row, dict) else "—"
        print(f"   · {name:12s} ✅ {n}행 · 키={keys}")

    # ⑥ **리서치 페이징** — 창 절단이 실제로 여기 있다(독립 리뷰 2026-09-11 H1).
    # 화면은 30일·300행을 요청하는데 한 응답이 20행이라 창의 대부분이 빈다.
    # 페이징이 되는지 **재고 나서** 이어받기를 배선한다(#151 추측 금지).
    print("\n⑥ 리서치도 20행이 페이지 크기인가 — 30일 창의 93%가 여기 달렸다")
    # ⚠️ company 하나만 쓸면 **나머지 두 탭은 영영 안 재진다**(#24 열거형).
    # 목록은 손으로 적지 말고 제품 레지스트리에서 파생시킨다 — 새 탭이 생기면
    # 자동으로 실린다(#38 화면과 프로브가 같은 목록).
    from bot.naver_research_client import _RESEARCH_KINDS as _RK
    for _kind in _RK:
        print(f"   [{_kind}]")
        _paging_sweep(requests, f"{_RESEARCH_BASE}/{_kind}", "title")

    # ── ⑦ 테마 — **제품 사다리를 그대로** 태운다(#35 화면이 쓰는 그 경로) ──
    print("\n⑦ 테마 목록 — 제품 사다리(collect_themes_json)를 그대로 태운다")
    theme_ok = False
    try:
        from bot.naver_sector_client import collect_themes_json
        # ⚠️ 반환 **모양**을 확인하고 쓸 것 — 3개로 풀면 ValueError 가 아래
        # `except` 에 삼켜져 멀쩡한 사다리가 '실행 실패'로 찍힌다(#252 반환형을
        # 확인 안 하고 조립한 진단이 그럴듯한 거짓을 낸다).
        rows, marks, why, partial = collect_themes_json()
        for m in marks:
            print(f"   · {m}")
        if rows:
            # 부분이면 ✅ 가 아니다 — 값은 왔지만 창을 다 못 덮었다(#343·#41).
            theme_ok = not partial
            print(f"   {'✅' if not partial else '⚠️'} 테마 {len(rows)}개"
                  + (f" · 부분({why})" if partial else "") + " · 표본: "
                  f"{json.dumps(rows[0], ensure_ascii=False)[:300]}")
        else:
            print(f"   ❌ 전 후보 실패 — {why or '사유 미기록'}")
    except Exception as exc:                               # noqa: BLE001
        print(f"   ❌ 사다리 실행 실패: {type(exc).__name__}: {exc}")

    # ── ⑧ 리서치 상세(목표가·투자의견) — 목록이 준 researchId 로 실호출 ──
    print("\n⑧ 리서치 상세 — 목표가·투자의견을 어느 경로로 읽나")
    detail_ok = False
    nid = ""
    obj, note = _get_json(requests, f"{_RESEARCH_BASE}/company?pageSize=1")
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        nid = str(obj[0].get("researchId") or "")
        print(f"   · 표본 researchId={nid} · endUrl={obj[0].get('endUrl')}")
    if not nid:
        print(f"   ❓ 목록에서 researchId 를 못 구해 판정 불가 — {note or '키 없음'}")
    else:
        try:
            from bot.naver_research_client import (_DETAIL_API_RUNGS,
                                                   detail_from_json)
            for label, tmpl in _DETAIL_API_RUNGS:
                url = tmpl.format(nid=nid)
                o2, n2 = _get_json(requests, url)
                if o2 is None:
                    print(f"   · {label}: ❌ {n2}  ({url})")
                    continue
                tgt, rating = detail_from_json(o2)
                keys = sorted(o2) if isinstance(o2, dict) else "(list)"
                print(f"   · {label}: ✅ 응답 · 목표가={tgt} 투자의견={rating!r}")
                print(f"     전 키: {keys}")
                print(f"     표본: {json.dumps(o2, ensure_ascii=False)[:700]}")
                if tgt is not None or rating:
                    detail_ok = True
                    break
        except Exception as exc:                           # noqa: BLE001
            print(f"   ❌ 상세 사다리 실행 실패: {type(exc).__name__}: {exc}")

    # ── ⑨ 그래도 못 찾았으면 **원천에게 묻는다**(추측 금지, #151·#338) ──
    # Next.js 는 라우트 청크 JS 안에 API 경로를 문자열 리터럴로 담는다.
    if not (theme_ok and detail_ok):
        print("\n⑨ 청크 발굴 — 그 페이지가 부르는 API 경로를 JS 에서 읽는다")
        from bot import naver_spa_discover as _disc

        def _fetch(u: str):
            try:
                r = requests.get(u, headers=_H, timeout=10)
            except Exception as exc:                       # noqa: BLE001
                return None, f"{type(exc).__name__}: {exc}"
            return (r.text, "") if r.status_code == 200 else (None, f"HTTP {r.status_code}")

        for label, page, key in (
                ("테마", "https://finance.naver.com/sise/theme.naver", "theme"),
                ("리서치", "https://finance.naver.com/research/company_list.naver",
                 "research")):
            if label == "테마" and theme_ok:
                continue
            if label == "리서치" and detail_ok:
                continue
            paths, why = _disc.discover(page, must_contain=key, fetch=_fetch,
                                        prefer=key)
            if paths:
                print(f"   · {label}: ✅ 청크에서 {len(paths)}건 발견")
                for pth in paths[:20]:
                    print(f"       {pth}")
            else:
                print(f"   · {label}: ❌ {why}")

    # 이상 없을 때도 **한 줄은 말한다** — 빈 출력이 정답인 도구는 없다(#274).
    print("\n⑩ 판정")
    print(f"   · 업종·리서치 목록: {'✅ 살아 있음' if rc == 0 else '❌ 전멸'}")
    print(f"   · 테마 목록: {'✅' if theme_ok else '❌'}")
    print(f"   · 리서치 상세: {'✅' if detail_ok else '❌'}")
    if rc == 0 and theme_ok and detail_ok:
        print("   ✅ 이상 없음 — 세 경로 모두 값이 온다")
    else:
        print("   ↪ ❌ 인 줄의 URL·사유를 그대로 붙여 주세요(추측으로 고치지 않습니다).")
    return 0 if (rc == 0 and theme_ok and detail_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
