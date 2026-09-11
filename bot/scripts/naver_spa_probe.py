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

_PROBE_VER = 1

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
_CANDIDATES = [
    ("업종 목록(domestic 미러)", "https://stock.naver.com/api/domestic/market/upjong/list"),
    ("업종 목록(KOR nation)", "https://stock.naver.com/api/foreign/market/KOR/upjong/list"),
    ("업종 목록(api 호스트)", "https://api.stock.naver.com/industry/list"),
    ("업종 목록(m 호스트)", "https://m.stock.naver.com/api/industry/list"),
    ("리서치(api 호스트)", "https://api.stock.naver.com/research/company"),
    ("리서치(m 호스트)", "https://m.stock.naver.com/api/research/company"),
]

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

    print("\n③ 후보 엔드포인트 실호출 — **이름이 아니라 응답으로** 판정(#25·#151)")
    for name, url in _CANDIDATES:
        try:
            r = requests.get(url, headers=_H, timeout=10)
        except Exception as exc:                           # noqa: BLE001
            print(f"   · {name:24s} ❌ {type(exc).__name__}")
            continue
        ct = (r.headers.get("content-type") or "").split(";")[0]
        note = ""
        if r.status_code == 200 and "json" in ct:
            try:
                obj = r.json()
                if isinstance(obj, list):
                    note = (f"list[{len(obj)}] 첫 키="
                            f"{sorted(obj[0])[:6] if obj and isinstance(obj[0], dict) else '—'}")
                elif isinstance(obj, dict):
                    note = f"dict 키={sorted(obj)[:6]}"
                rc = 0
            except Exception:                              # noqa: BLE001
                note = "JSON 파싱 실패"
        mark = "✅" if (r.status_code == 200 and "json" in ct) else "❌"
        print(f"   · {name:24s} {mark} HTTP {r.status_code} · {ct or '—'} · {note}")

    if rc:
        print("\n④ ❌ 인라인도 후보도 못 찾았다 — 브라우저 DevTools Network 탭에서")
        print("   업종 페이지가 실제로 부르는 XHR URL 을 하나만 알려주세요(추측 금지).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
