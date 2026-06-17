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


# 제품명이 아닌 헤더/구조/합계 토큰 — 1차 빌드(사용자 2026-06-17)에서 '매출액'·'비율'·
# '금액'·'영업이익'·'제N조(약관)' 등이 제품명으로 누수. G2 매칭 전 제거(정확도 우선).
import re as _re
_NONPRODUCT = frozenset({
    "매출액", "매출", "비율", "비율(%)", "비중", "매출비중", "금액", "구분", "용도",
    "용도/기능", "기능", "소계", "합계", "계", "총계", "내부매출액", "순매출액",
    "영업이익", "당기순이익", "자산", "주요제품", "주요제품및서비스", "품목",
    "사업부문", "사업구분", "대상회사", "구체적용도", "주요상표등", "회사명",
    "매출형태", "매출유형", "주요품목", "주요생산품목", "회계처리", "제조경비",
    "정부보조금", "주요회사", "주요고객", "사업영역", "주요수요처및특성",
})


def _clean_products(products: list[dict]) -> list[dict]:
    """제품 리스트에서 헤더/구조 토큰·약관 조항·기수 라벨 제거(G2 매칭 전 정제).
    순수 — 단위테스트."""
    out: list[dict] = []
    for p in products or []:
        nm = (p.get("name") or "").strip().rstrip(" 등").strip()
        if not nm or nm in _NONPRODUCT:
            continue
        if _re.match(r"^제?\s*\d+\s*[조기항](?:\b|\()", nm):   # 제7조(약관)/제19기 류
            continue
        if _re.fullmatch(r"\d{4}년.*", nm):                     # '2024년(제40기)' 류
            continue
        out.append({**p, "name": nm})                           # 정제된 이름으로 저장
    return out


def load_inventory() -> dict:
    """저장된 매출구성 인벤토리(code→{company, products}) 로드. 없으면 {}."""
    p = _data_dir() / "dart_revenue_inventory.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


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
    products = _clean_products([p for p in products_from_rows(rows) if p.get("name")])
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


def all_listed_codes() -> list[str]:
    """전 KRX 상장 6자리 코드 (DART corp_code 맵 — 상장사 전수). 실패 시 []."""
    try:
        from bot.dart_client import get_dart
        return sorted(get_dart()._load_corp_code_map().keys())
    except Exception as exc:
        log.warning("all_listed_codes: %s", exc)
        return []


def _needs_rebuild(entry: dict | None, latest_rcept: str) -> bool:
    """인벤토리 항목을 다시 파싱해야 하나 — 최신 정기보고서 rcept_no 가 바뀌었거나
    저장된 제품이 없으면 True. 같으면 False(document.xml fetch skip). 순수."""
    if not entry or not entry.get("products"):
        return True
    return (entry.get("rcept_no") or "") != (latest_rcept or "")


def refresh_inventory(codes: list[str] | None = None, api_key: str | None = None,
                      shard: tuple[int, int] | None = None, sleep: float = 0.3) -> dict:
    """전수 인벤토리 갱신 — **변경분만**(정기 파싱, 사용자 2026-06-17). 회사별 최신
    정기보고서 rcept_no(list.json·경량) 확인 → 안 바뀌면 document.xml 파싱 skip,
    바뀐 회사만 재파싱. 월말/마지막주 타이머용. shard=(i,m) 면 codes[i::m] 만(분할 실행).

    Returns {built, skipped, failed, total}. 진척 로그(silent-fail 금지·실수기록 #12d)."""
    key = (api_key if api_key is not None else os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        log.warning("refresh_inventory: DART_API_KEY 없음")
        return {"built": 0, "skipped": 0, "failed": 0, "total": 0}
    from trade.scripts.probe_dart_revenue import _fetch_report_list, pick_business_report
    from bot.dart_client import get_dart
    dart = get_dart()
    inv = load_inventory()
    codes = codes or all_listed_codes()
    if shard:
        i, m = shard
        codes = codes[i::m]
    built = skipped = failed = 0
    for n, code in enumerate(codes, 1):
        try:
            corp = dart.stock_code_to_corp_code(code)
            rep = pick_business_report(_fetch_report_list(key, corp)) if corp else None
            if not rep:
                failed += 1
                continue
            if not _needs_rebuild(inv.get(code), rep["rcept_no"]):
                skipped += 1
                continue
            r = fetch_company_products(code, key)   # 변경분만 무거운 document.xml 파싱
            if r:
                inv[code] = r
                built += 1
            else:
                failed += 1
        except Exception as exc:
            log.warning("refresh_inventory %s: %s", code, exc)
            failed += 1
        if n % 200 == 0:
            log.info("refresh_inventory 진척 %d/%d (built=%d skip=%d fail=%d)",
                     n, len(codes), built, skipped, failed)
        time.sleep(sleep)
    out = _data_dir() / "dart_revenue_inventory.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("refresh_inventory 저장 실패: %s", exc)
    res = {"built": built, "skipped": skipped, "failed": failed, "total": len(codes)}
    log.info("refresh_inventory 완료: %s", res)
    return res


def main(argv: list[str] | None = None) -> int:
    import argparse
    from trade.scripts.probe_dart_revenue import _SAMPLE_WIDE, _load_env
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="G1 DART 매출구성 인벤토리 빌더 (회사→제품)")
    p.add_argument("--codes", default="", help="쉼표구분 6자리 코드 (미지정 시 ~40 표본)")
    p.add_argument("--refresh", action="store_true",
                   help="전 KRX 상장 전수 갱신(변경분만·정기 파싱용)")
    p.add_argument("--shard", default="",
                   help="분할 실행 'i/m' — codes[i::m] 만(마지막주 날짜별 분산)")
    args = p.parse_args(argv)
    _load_env()
    key = (os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        print("⛔ DART_API_KEY 없음 — ~/stock/.env 확인.")
        return 2
    if args.refresh:
        shard = None
        if args.shard and "/" in args.shard:
            i, m = args.shard.split("/", 1)
            shard = (int(i), int(m))
        res = refresh_inventory(api_key=key, shard=shard)
        print(f"📦 전수 갱신: {res}")
        return 0
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
