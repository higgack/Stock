#!/usr/bin/env python3
"""미국채·장단기금리차를 **네이버에서 받을 수 있는지 재는** 프로브 — 읽기 전용·LLM 0·₩0.

사용자 2026-09-14: "여기 미국채나 장단기금리차같은거 재무부것도 여전히 가장
최신이 아니잖아. 이런건 네이버에 있을것 같은데 거기꺼 쓸수없어?"

⚠️ **엔드포인트 이름을 추측해 배선하면 죽은 경로를 배포한다** — 이 레포에서
반복 실패한 방식이다(#151 akshare 이름이 grep 은 통과하고 import 는 실패 ·
#345 탐색은 '찾음'을 '동작함'으로 렌더하지 말 것). 그래서 여기서는 **재기만**
한다. 배선은 이 출력을 보고 한다.

무엇을 재나
  ① **대조군** — 지금 화면이 들고 있는 FRED/재무부 기준일(#143 대조군 없이는
     '네이버에 없다' 와 '이 VM 이 아무 데도 못 닿는다' 를 못 가른다).
  ② 레포가 **이미 동작을 증명한** 네이버 API 가족
     (`securityService/marketindex/{cat}`)에 금리 계열 카테고리가 있는지 —
     후보를 실호출해 상태·행수·표본을 찍는다(#25 능력은 이름이 아니라 실측).
  ③ 그래도 못 찾으면 **원천에게 묻는다** — 네이버 시장지표 페이지가 부르는
     API 경로를 Next.js 청크에서 읽는다(`naver_spa_discover`, #338·#340).
  ④ 덤으로 `metals` 카테고리 전 항목 — 팔라듐 네이버 코드가 실재하는지
     (#367 에서 "재지 않았으므로 매핑하지 않는다" 로 남겨 둔 그 질문).

판정 기준
  네이버가 쓸모 있으려면 **더 최신**이어야 한다. 값이 있어도 기준일이
  FRED/재무부와 같거나 뒤면 갈아탈 이유가 없다 — 그래서 ①을 먼저 찍는다.

읽기 전용 — 네이버는 `requests` 로 직접 치고(운영 캐시 미사용), FRED 는
**디스크 캐시를 읽기만** 한다(새로 받지 않는다 — 진단이 자기가 읽을 신호를
오염시키면 안 된다, #30·#264·#283·#321). 재무부는 메모리 캐시만 쓴다.

실행:
    cd ~/stock && .venv/bin/python -m bot.scripts.naver_rate_probe

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import json
import re
import sys

_PROBE_VER = 1

_H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/125.0.0.0 Safari/537.36"),
      "Accept": "application/json, text/plain, */*",
      "Referer": "https://stock.naver.com/"}

# 이미 동작을 증명한 가족(`bot/naver_marketindex._CATEGORIES` 가 넷을 쓴다).
# 아래는 **후보**다 — 맞는지는 원천이 상태코드로 답한다(추측 배선 금지).
_KNOWN_CATS = ("energy", "metals", "agricultural", "transport")
_CAND_CATS = ("interest", "interestRate", "rate", "bond", "bonds",
              "treasury", "money", "financial")
_MI_BASE = "https://stock.naver.com/api/securityService/marketindex"

# 금리 낱말을 품은 API 경로를 찾을 페이지 후보. 페이지가 404 인 것도 데이터다.
_PAGES = (
    ("시장지표(모바일)", "https://m.stock.naver.com/marketindex/"),
    ("시장지표(PC)", "https://finance.naver.com/marketindex/"),
)
_KEYS = ("interest", "bond", "rate")

# 화면이 쓰는 그 시리즈(#35) — 대조군.
_SIDS = ("DGS2", "DGS10", "T10Y2Y")


def banner() -> str:
    """이 출력을 **어느 코드가 만들었는지** 한 줄로(#21·#364).

    ⚠️ **못 보는 축**(#274): 지문은 이 파일 하나를 잰다. 판정을 다른 모듈로
    빼면 배너가 덮지 않는 코드가 생긴다 — 그때는 지문 범위를 같이 넓힐 것.
    """
    import hashlib
    import pathlib
    try:
        sig = hashlib.sha1(
            pathlib.Path(__file__).read_bytes()).hexdigest()[:10]
    except Exception as exc:                                   # noqa: BLE001
        sig = f"지문불가({type(exc).__name__})"
    return (f"■ 네이버 금리 프로브 v{_PROBE_VER} · 코드 지문 {sig} · "
            f"인터프리터 {sys.executable}")


_RE_DATEISH = re.compile(r"\b(20\d{2})[-/.]?(\d{2})[-/.]?(\d{2})\b")


def row_date(rows) -> str | None:
    """표본 행들에서 **날짜꼴 값**을 찾아 가장 큰 것을 돌려준다(없으면 None).

    어느 필드가 기준일인지는 원천만 알고 우리는 아직 안 쟀다 — 이름을 찍어
    맞히는 대신(#151·#345 탐색은 '찾음'을 '동작함'으로 렌더하지 말 것) 값의
    **모양**으로 후보를 고르고, 없으면 그대로 '판정 불가' 가 된다."""
    best = None
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        for v in r.values():
            m = _RE_DATEISH.search(str(v)) if isinstance(v, (str, int)) else None
            if not m:
                continue
            iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            if best is None or iso > best:
                best = iso
    return best


def verdict(naver_asof: str | None, ours_asof: str | None) -> str:
    """(네이버 기준일, 우리 기준일) → 갈래. 순수 — 회귀가 값으로 잰다.

    ⚠️ 못 잰 쪽이 있으면 ✅ 도 ❌ 도 아니다(#54 대조 0건은 통과가 아니다).
    """
    if not naver_asof:
        return "unknown_naver"
    if not ours_asof:
        return "unknown_ours"
    if naver_asof > ours_asof:
        return "fresher"
    if naver_asof == ours_asof:
        return "same"
    return "older"


_VERDICT_TEXT = {
    "fresher": "✅ 네이버가 더 최신 — 갈아탈 값어치가 있다",
    "same":    "⚠️ 같은 기준일 — 갈아탈 이유가 없다(원천이 이미 최선)",
    "older":   "⚠️ 네이버가 더 오래됨 — 갈아타면 오히려 뒤처진다",
    "unknown_naver": "❓ 네이버 기준일을 못 읽었다 — 판정 불가",
    "unknown_ours":  "❓ 우리 기준일을 못 읽었다 — 판정 불가",
}


def _fred_disk(sid: str) -> dict:
    """오늘자 FRED 디스크 캐시(=화면이 들고 있는 값). **읽기만** 한다."""
    from datetime import date
    from bot import market_overview as mo
    f = mo._CACHE_DIR / "fred" / f"{sid}_{date.today().isoformat()}.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:                                          # noqa: BLE001
        return {}


def _get(requests, url: str, *, timeout: int = 12):
    """(객체|None, 사유) — 본문이 JSON 이 아니면 앞부분을 사유에 싣는다(#109)."""
    try:
        r = requests.get(url, headers=_H, timeout=timeout)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} · {r.text[:160]!r}"
    try:
        return r.json(), ""
    except Exception:                                          # noqa: BLE001
        return None, f"JSON 아님({len(r.content):,}B) · {r.text[:160]!r}"


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:                                          # noqa: BLE001
        pass
    print(banner())
    try:
        import requests
    except Exception as exc:                                   # noqa: BLE001
        # 의존성이 없으면 '네이버에 없다' 가 아니라 **판정 불가**다(#132).
        print(f"  ❌ requests 미설치({exc}) — .venv/bin/python 으로 돌리세요.")
        return 1

    # ── ① 대조군: 지금 화면이 들고 있는 기준일 ────────────────────────
    print("\n① 대조군 — 지금 화면의 기준일(FRED 디스크 캐시 · 재무부)")
    ours: dict[str, str] = {}
    for sid in _SIDS:
        d = _fred_disk(sid)
        if d:
            ours[sid] = str(d.get("time") or "")
            print(f"   {sid}: {d.get('time')} = {d.get('value')} "
                  f"· 원천 {d.get('src') or 'FRED'}")
        else:
            print(f"   {sid}: 디스크캐시 없음 — 대조 기준이 없다(판정 불가)")
    try:
        from bot.treasury_yield_client import _DIAG_ATTEMPTS, fetch_daily_curve
        curve = fetch_daily_curve(attempts=_DIAG_ATTEMPTS)
        if curve:
            last = sorted(curve)[-1]
            ours["_UST"] = last
            print(f"   재무부 곡선 마지막 {last}: {curve[last]}")
        else:
            from bot.treasury_yield_client import last_fail
            print(f"   재무부 곡선: ❌ 도달 실패({last_fail()}) — 이 VM 의"
                  " 바깥 도달 자체가 의심스럽다면 아래 결과도 그렇게 읽을 것")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   재무부 곡선: ❌ {type(exc).__name__}: {exc}")

    # ── ② 이미 동작을 증명한 가족에 금리 카테고리가 있나 ───────────────
    print("\n② marketindex 카테고리 — 아는 넷 + 후보들(원천이 상태로 답한다)")
    hits: list[tuple[str, list]] = []
    for cat in _KNOWN_CATS + _CAND_CATS:
        obj, why = _get(requests, f"{_MI_BASE}/{cat}")
        if obj is None:
            print(f"   · {cat:<14} ❌ {why}")
            continue
        rows = obj if isinstance(obj, list) else (obj.get("result") or obj)
        n = len(rows) if isinstance(rows, list) else "?"
        mark = "✅" if cat not in _KNOWN_CATS else "(기존)"
        print(f"   · {cat:<14} {mark} 200 · {n}행")
        if isinstance(rows, list) and rows:
            print(f"     전 키: {sorted(rows[0]) if isinstance(rows[0], dict) else type(rows[0]).__name__}")
            names = [str(r.get("name") or r.get("symbolCode"))
                     for r in rows[:12] if isinstance(r, dict)]
            print(f"     표본: {', '.join(names)}")
            if cat not in _KNOWN_CATS:
                hits.append((cat, rows))

    # ── ③ 원천에게 묻는다 — 그 페이지가 부르는 API 경로 ───────────────
    print("\n③ 청크 발굴 — 네이버 시장지표 페이지가 부르는 API 경로")
    from bot import naver_spa_discover as _disc

    # ⚠️ `discover` 를 키마다 다시 부르면 **같은 청크를 키 수만큼 다시 받는다**
    # (독립 리뷰 실측: 최대 ~78요청·18MB/실행). 프로브가 스스로 레이트리밋을
    # 불러 자기 측정을 망치는 모양이다(#61 이 파라미터가 비용의 어느 단계를
    # 줄이나 · #321). URL 단위로 한 번만 받아 세 키가 나눠 쓴다.
    _seen: dict[str, tuple] = {}

    def _fetch(u: str):
        if u in _seen:
            return _seen[u]
        try:
            r = requests.get(u, headers=_H, timeout=10)
        except Exception as exc:                               # noqa: BLE001
            out = (None, f"{type(exc).__name__}: {exc}")
        else:
            out = ((r.text, "") if r.status_code == 200
                   else (None, f"HTTP {r.status_code}"))
        _seen[u] = out
        return out

    found_any = False
    for label, page in _PAGES:
        for key in _KEYS:
            paths, why = _disc.discover(page, must_contain=key, fetch=_fetch,
                                        prefer=key)
            if paths:
                found_any = True
                print(f"   · {label}/{key}: ✅ {len(paths)}건")
                for pth in paths[:15]:
                    print(f"       {pth}")
            else:
                print(f"   · {label}/{key}: ❌ {why}")

    # ── ④ 팔라듐 — metals 전 항목(#367 이 남겨 둔 질문) ───────────────
    print("\n④ metals 전 항목 — 팔라듐(Palladium/PA) 코드가 실재하나")
    obj, why = _get(requests, f"{_MI_BASE}/metals")
    if not isinstance(obj, list):
        print(f"   ❓ 판정 불가 — {why or '리스트가 아님'}")
    else:
        for r in obj:
            if not isinstance(r, dict):
                continue
            print(f"   · {str(r.get('symbolCode')):<12} {r.get('name')}"
                  f"  close={r.get('closePrice')}"
                  f"  reuters={r.get('reutersCode')}")

    # ── 판정 — 이상 없을 때도 한 줄은 말한다(#274) ────────────────────
    #
    # ⚠️ 옛 판은 `_VERDICT_TEXT['unknown_naver']` 를 **리터럴로** 찍어
    # `verdict()` 가 어느 경로로도 안 불렸다 — 모듈 독스트링이 "판정은
    # 3-상태" 라고 적어 놓고 실제론 발화 경로가 없는 함수였다(2026-09-16
    # 독립 리뷰 실측: ⑤ 를 `pass` 로 바꿔도 전 테스트 green, #291·#53).
    # 이제 표본 행에서 **날짜꼴 값을 실제로 찾아** 우리 최신 기준일과 댄다.
    print("\n⑤ 판정")
    ours_best = max((v for k, v in ours.items() if v), default="")
    if hits:
        print(f"   · 금리 계열 카테고리 {len(hits)}건 발견 — 위 표본의 "
              "기준일 필드를 ①과 대조해 더 최신일 때만 배선한다")
        for cat, rows in hits:
            nav_asof = row_date(rows)
            code = verdict(nav_asof, ours_best or None)
            print(f"     {cat}: 네이버 기준일 후보 {nav_asof or '—'} · "
                  f"우리 {ours_best or '—'} → {_VERDICT_TEXT[code]}")
    elif found_any:
        print("   · 카테고리 가족엔 없지만 **페이지가 부르는 경로**가 나왔다 —"
              " 위 경로를 실호출해 모양을 재는 것이 다음 수다")
    else:
        print("   ❌ 네이버에서 금리 계열을 한 건도 못 찾았다.")
        print("      ↪ ①의 재무부 곡선이 정상이었다면 = 네이버에 없는 것이고,")
        print("        ①도 실패했다면 이 VM 의 바깥 도달 문제다(#143 대조군).")
    print("   ⚠️ 이 프로브는 **아무것도 배선하지 않는다** — 출력을 보고 정한다.")
    # 대조군(①)이 하나도 안 서면 '네이버에 없다' 가 아니라 **판정 불가**다 —
    # 한 건도 못 쟀는데 rc=0 을 내면 성공으로 읽힌다(#54·#143 · #351 이 형제
    # 프로브에서 겪은 그대로).
    if not ours:
        print("   ❓ 대조군이 하나도 안 섰다(FRED 디스크캐시·재무부 곡선 모두)"
              " — 이번 실행으로는 판정할 수 없다.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
