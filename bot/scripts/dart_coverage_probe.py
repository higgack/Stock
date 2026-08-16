"""DART 수집 커버리지 실측 — "그날 DART 에 몇 건 올라왔고 우리가 몇 건 갖고 있나".

2026-08-16 사용자 지적: "8/14 는 2Q 실적발표 마지막날인데 DART 피드에 15건뿐".
원인 하나(정기보고서가 지분공시 catch-all → 대시보드 노이즈컷이 전부 숨김)는
코드에서 확정했지만, **애초에 아카이브에 들어는 왔는지**는 페이지 상한
(`fetch_market_disclosures` 의 max_pages × page_count)에 걸렸을 수 있어
추측으로 말할 수 없다(실수 #12). 이 스크립트가 그걸 숫자로 가른다.

측정 항목:
  1. DART 원본 그날 총 접수 건수(total_count) — list.json 이 직접 알려준다
  2. **정렬 방향** — 증분 수집이 `page_no 1..3` 만 보므로, 오름차순이면
     최신 공시를 영원히 못 본다. 첫 페이지의 첫/끝 rcept_no 로 판정.
  3. 정기보고서(사업/반기/분기) 실제 건수 — 전 페이지 순회
  4. 우리 아카이브의 그날 보유 건수(카테고리별) + 정기보고서 누락 수

Read-only — DART list.json GET 만 한다. 아카이브 파일을 쓰지 않는다.

Usage on VM:
    cd ~/stock
    .venv/bin/python -m bot.scripts.dart_coverage_probe 2026-08-14
    .venv/bin/python -m bot.scripts.dart_coverage_probe 2026-08-14 --max-pages 60
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from bot.dart_feed import (
    _DART_BASE,
    _PERIODIC_KW,
    _TIMEOUT,
    _classify_report,
    _dart_api_key,
    load_archive,
)


def _fetch_day(api_key: str, d: date, max_pages: int) -> tuple[list[dict], dict]:
    """그날 접수분 전량(페이지 상한까지) + 메타. bgn=end 라 하루만."""
    ds = d.strftime("%Y%m%d")
    rows: list[dict] = []
    meta: dict = {}
    for page_no in range(1, max_pages + 1):
        resp = requests.get(
            f"{_DART_BASE}/list.json",
            params={"crtfc_key": api_key, "bgn_de": ds, "end_de": ds,
                    "page_no": page_no, "page_count": 100},
            timeout=_TIMEOUT,
        )
        payload = resp.json()
        status = payload.get("status")
        if status != "000":
            if page_no == 1:
                meta["error"] = f"status={status} {payload.get('message')}"
            break
        if page_no == 1:
            meta["total_count"] = int(payload.get("total_count") or 0)
            meta["total_page"] = int(payload.get("total_page") or 0)
        page = payload.get("list") or []
        if page_no == 1 and page:
            # 정렬 방향 — rcept_no 는 접수순 증가라 첫/끝 비교로 알 수 있다.
            first, last = page[0].get("rcept_no", ""), page[-1].get("rcept_no", "")
            meta["page1_first"] = first
            meta["page1_last"] = last
            meta["order"] = ("desc(최신먼저)" if first > last
                             else "asc(오래된먼저)" if first < last else "?")
        rows.extend(page)
        if page_no >= meta.get("total_page", 1):
            break
    meta["fetched"] = len(rows)
    return rows, meta


def main() -> int:
    ap = argparse.ArgumentParser(description="DART 수집 커버리지 실측(조회 전용)")
    ap.add_argument("day", help="YYYY-MM-DD (접수일, KST)")
    ap.add_argument("--max-pages", type=int, default=60,
                    help="순회할 최대 페이지(100건/페이지, 기본 60=6000건)")
    args = ap.parse_args()

    api_key = _dart_api_key()
    if not api_key:
        print("DART_API_KEY 없음 — .env 확인")
        return 2
    d = datetime.strptime(args.day, "%Y-%m-%d").date()

    rows, meta = _fetch_day(api_key, d, args.max_pages)
    if meta.get("error"):
        print(f"DART 응답 오류: {meta['error']}")
        return 1

    total = meta.get("total_count", 0)
    fetched = meta.get("fetched", 0)
    periodic = [r for r in rows
                if any(k in (r.get("report_nm") or "") for k in _PERIODIC_KW)]
    print(f"── DART 원본 {d} ──")
    print(f"  총 접수      : {total:,}건 ({meta.get('total_page', 0)} 페이지)")
    print(f"  이번에 조회  : {fetched:,}건"
          + ("  ⚠️ --max-pages 부족(전량 아님)" if fetched < total else ""))
    print(f"  정렬 방향    : {meta.get('order')}  "
          f"(1p 첫 {meta.get('page1_first')} → 끝 {meta.get('page1_last')})")
    print(f"  정기보고서   : {len(periodic):,}건 "
          f"(사업/반기/분기 — 사용자가 찾는 2Q 실적 원문)")

    cats: dict[str, int] = {}
    for r in rows:
        c = _classify_report(r.get("report_nm") or "")
        cats[c] = cats.get(c, 0) + 1
    print("  분류 분포    : " + " · ".join(
        f"{k} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])))

    arch = load_archive(d)
    a_cats: dict[str, int] = {}
    for it in arch:
        c = it.get("category") or "?"
        a_cats[c] = a_cats.get(c, 0) + 1
    have = {it.get("rcept_no") for it in arch}
    miss_periodic = [r for r in periodic if r.get("rcept_no") not in have]
    print(f"\n── 우리 아카이브 {d} ──")
    print(f"  보유         : {len(arch):,}건")
    print("  카테고리     : " + (" · ".join(
        f"{k} {v}" for k, v in sorted(a_cats.items(), key=lambda x: -x[1]))
        or "(없음)"))
    print(f"  정기보고서 누락: {len(miss_periodic):,} / {len(periodic):,}")
    for r in miss_periodic[:10]:
        print(f"     - {r.get('corp_name')} · {r.get('report_nm')} "
              f"· {r.get('rcept_no')}")

    print("\n── 판정 ──")
    if periodic and not miss_periodic:
        print("  ✅ 수집은 됐다 → 화면에서 안 보였던 건 **분류/노이즈컷** 문제")
        print("     (정기보고서 카테고리 분리 + 재분류 v9 로 해소)")
    elif miss_periodic:
        print("  ❌ 아카이브에 아예 없다 → **수집 단계 누락**")
        print("     fetch_market_disclosures 의 max_pages×page_count 상한 또는")
        print("     증분 사이클(3페이지=300건)이 그날 물량을 못 따라간 것")
    else:
        print("  그날 정기보고서가 0건 — 날짜를 확인할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
