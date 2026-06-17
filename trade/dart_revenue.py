"""G1 — DART 사업보고서 매출구성 → 회사→[제품·매출비중] 인벤토리 (회사맵핑 backbone).

feasibility 프로브(`trade.scripts.probe_dart_revenue`, 사용자 2026-06-17 --wide 로
매출표 형태 수렴 확인)에서 검증한 **추출 로직을 그대로 재사용**해 정식 인벤토리를
산출한다. 주류 형태: `사업부문 | 매출유형 | 품목 | 구체적용도 | … | 매출액 | 비중(비율)`
(+ 매출액(비율) 병합셀·제N기 다기간 컬럼) — 프로브 best_revenue_table(회계 주석 표
제외·매출 신호 가중) + products_from_rows 가 처리.

⚠️ 이 모듈은 **추출만** 한다(회사 → [제품, 비중]). 관세청 품목명 매칭·대시보드 노출은
operator 승인 단계(G2/G3)에서 — 정확도 우선(오매핑이 공개 신뢰를 깎는 비용 > 빈칸).
읽기전용·₩0(무료 DART OpenAPI·LLM 0). 산출물은 인벤토리 JSON 1개(검토용).

run (VM): cd ~/stock && .venv/bin/python -m trade.dart_revenue [--codes 005930,000660]
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("trade.dart_revenue")


def _data_dir() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))


def fetch_company_products(stock_code: str, api_key: str | None = None) -> dict | None:
    """6자리 stock_code → {code, company, report, rcept_no, products:[{name,
    share_pct, amount}]} 또는 None. DART 사업보고서 매출표 1개를 골라 제품·비중 추출.

    프로브의 검증된 단계를 재사용: corp_code → 최신 정기보고서 → document.xml 원문
    → best_revenue_table(매출표 선택) → products_from_rows(제품/비중/금액). 키 부재·
    미발견·실패 시 None(graceful)."""
    key = (api_key if api_key is not None else os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        return None
    # 프로브(검증·테스트된 추출 primitives) 재사용 — 단일 소스, dup 없음.
    from trade.scripts.probe_dart_revenue import (
        _fetch_report_list, best_revenue_table, download_doc_raw,
        pick_business_report, products_from_rows,
    )
    from bot.dart_client import get_dart
    dart = get_dart()
    corp = dart.stock_code_to_corp_code(stock_code)
    if not corp:
        return None
    rep = pick_business_report(_fetch_report_list(key, corp))
    if not rep:
        return None
    markup = download_doc_raw(key, rep["rcept_no"])
    if not markup:
        return None
    best, _score = best_revenue_table(markup)
    if not best:
        return None
    _t, rows = best
    products = [p for p in products_from_rows(rows) if p.get("name")]
    if not products:
        return None
    return {
        "code": stock_code,
        "company": dart.stock_code_to_name(stock_code) or stock_code,
        "report": rep.get("report_nm"),
        "rcept_no": rep.get("rcept_no"),
        "products": products[:20],
    }


def build_inventory(stock_codes: list[str], api_key: str | None = None,
                    sleep: float = 0.4) -> dict:
    """여러 종목 → {code: fetch_company_products(...)} 인벤토리 + 디스크 저장
    (<DATA>/dart_revenue_inventory.json, read-only 검토용). 실패 종목은 생략."""
    key = (api_key if api_key is not None else os.environ.get("DART_API_KEY") or "").strip()
    inv: dict = {}
    for code in stock_codes:
        try:
            r = fetch_company_products(code, key)
        except Exception as exc:
            log.warning("dart_revenue %s: %s", code, exc)
            r = None
        if r:
            inv[code] = r
        time.sleep(sleep)
    out = _data_dir() / "dart_revenue_inventory.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("dart_revenue: 인벤토리 저장 실패: %s", exc)
    return inv


def main(argv: list[str] | None = None) -> int:
    import argparse
    from trade.scripts.probe_dart_revenue import _SAMPLE_WIDE, _load_env
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="G1 DART 매출구성 인벤토리 빌더 (회사→제품)")
    p.add_argument("--codes", default="", help="쉼표구분 6자리 코드 (미지정 시 ~40 표본)")
    args = p.parse_args(argv)
    _load_env()
    key = (os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        print("⛔ DART_API_KEY 없음 — ~/stock/.env 확인.")
        return 2
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or [c for c, _, _ in _SAMPLE_WIDE]
    inv = build_inventory(codes, key)
    print(f"\n📦 매출구성 인벤토리 — {len(inv)}/{len(codes)} 사 추출")
    print("─" * 96)
    for code, r in inv.items():
        ps = ", ".join(
            (f"{x['name']}({x['share_pct']}%)" if x.get("share_pct") is not None else x["name"])
            for x in r["products"][:5])
        print(f"  {code} {r['company'][:16]:<18} {ps[:68]}")
    miss = [c for c in codes if c not in inv]
    if miss:
        print(f"\n  미추출 {len(miss)}: {', '.join(miss)}")
    print(f"\n💾 {_data_dir() / 'dart_revenue_inventory.json'}")
    print("→ 다음(G2): 제품명 ↔ 관세청 품목명 매칭 후보 생성 → operator 승인 → 노출")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
