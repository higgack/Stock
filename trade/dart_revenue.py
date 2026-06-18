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

# 손익계산서 항목 + 합계/소계 행 — 절대 제품명 아님(사용자 2026-06-18 audit: 비중합
# 200%·'이름 의심: 매출원가/매출총이익/영업이익/매출총계/연결매출액' 다수의 근본 원인).
# 로마숫자·번호 접두("II. 매출총이익")도 substring 매칭. '상품/용역/제품매출' 같은
# 매출유형은 안 잡음(G2 품목매칭 단계 자료라 유지).
_PNL_RE = _re.compile(r"매출원가|매출총(?:이익|손실)|영업(?:이익|손실)|당기순(?:이익|손실)"
                      r"|법인세|포괄손익|영업외|금융손익|총포괄")
_SUBTOTAL_RE = _re.compile(r"합\s*계|총\s*계|소\s*계|단순\s*합산|연결\s*매출|순\s*매출|내부\s*매출")


def _clean_products(products: list[dict]) -> list[dict]:
    """제품 리스트에서 헤더/구조 토큰·약관 조항·기수 라벨 제거(G2 매칭 전 정제).
    순수 — 단위테스트."""
    out: list[dict] = []
    for p in products or []:
        nm = (p.get("name") or "").strip().rstrip(" 등").strip()
        if not nm or nm in _NONPRODUCT:
            continue
        if _PNL_RE.search(nm) or _SUBTOTAL_RE.search(nm):       # 손익/합계·소계 행 제거
            continue
        if _re.match(r"^제?\s*\d+\s*[조기항](?:\b|\()", nm):   # 제7조(약관)/제19기 류
            continue
        if _re.fullmatch(r"\d{4}년.*", nm):                     # '2024년(제40기)' 류
            continue
        out.append({**p, "name": nm})                           # 정제된 이름으로 저장
    return out


def _fill_amount_based_share(products: list[dict]) -> list[dict]:
    """비중 전무 + 금액 있으면 매출액 기반 비중 산출 (C4 — 비중 컬럼 없는 표, 한미·기아
    등 사용자 2026-06-18 '비중전무 정밀화'). 비중이 하나라도 있으면 원본 유지(혼합 표는
    건드리지 않음). 금액 2개+ 필요. 순수 — 단위테스트."""
    if any(p.get("share_pct") is not None for p in products):
        return products
    amts = [p["amount"] for p in products if p.get("amount")]
    if len(amts) < 2 or sum(amts) <= 0:
        return products
    tot = sum(amts)
    for p in products:
        if p.get("amount"):
            p["share_pct"] = round(p["amount"] / tot * 100.0, 1)
    return products


def load_inventory() -> dict:
    """저장된 매출구성 인벤토리(code→{company, products}) 로드. 없으면 {}."""
    p = _data_dir() / "dart_revenue_inventory.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def audit_inventory(inv: dict | None = None) -> list[dict]:
    """인벤토리에서 **파싱 의심 항목** 추출 — [{code, company, n, reasons}].
    DART 피드 coverage-audit 처럼 반복 고도화 대상 식별(사용자 2026-06-17 '파싱
    제대로 안 된 거 구분 → 너랑 같이 고도화'). 순수(단위테스트).

    의심 신호: 제품 0 · 1개만(과소추출) · 비중 전무 · 비중합 ≠100(±) · 잔여
    노이즈 이름(4자리수·매출/영업이익 토큰·기호만)."""
    inv = inv if inv is not None else load_inventory()
    out: list[dict] = []
    for code, rec in (inv or {}).items():
        prods = rec.get("products") or []
        reasons: list[str] = []
        if not prods:
            reasons.append("제품 0")
        else:
            if len(prods) == 1:
                reasons.append("제품 1개(과소추출 의심)")
            shares = [p["share_pct"] for p in prods if p.get("share_pct") is not None]
            if not shares:
                reasons.append("비중 전무")
            elif not (60 <= sum(shares) <= 140):
                reasons.append(f"비중합 {sum(shares):.0f}%(≠100)")
            noisy = [p.get("name", "") for p in prods
                     if _re.search(r"\d{4,}|매출|영업이익|손익|^\W+$", p.get("name", ""))]
            if noisy:
                reasons.append("이름 의심: " + ", ".join(noisy[:3]))
        if reasons:
            out.append({"code": code, "company": rec.get("company", code),
                        "n": len(prods), "reasons": reasons})
    return out


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
        parse_table_grid, pick_business_report, products_from_grid,
        products_from_rows,
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
    # 다기간(연도별 매출액|비율) 표는 grid(rowspan/colspan 전파)로 최신연도만 — 비율
    # 중복합산(대한항공 760%) 차단. 단일연도·비-다기간은 None → products_from_rows 폴백.
    raw = products_from_grid(parse_table_grid(_t)) or products_from_rows(rows)
    products = _clean_products([p for p in raw if p.get("name")])
    if not products:
        return None
    products = _fill_amount_based_share(products)   # C4: 비중 컬럼 없는 표 → 매출액 기반 비중
    # 비중합 sanity 폴백(사용자 2026-06-18) — 합계행 제거·다기간 보정 후에도 비중합이
    # 비현실(>130 or <70)이면 비중을 신뢰 안 함: share 전체 None(빈 비중 = 정직, G 정책
    # '빈칸 > 오매핑'). 제품명/금액은 보존(품목 매칭은 가능).
    _sh = [p["share_pct"] for p in products if p.get("share_pct") is not None]
    if _sh and not (70 <= sum(_sh) <= 130):
        for p in products:
            p["share_pct"] = None
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
                      shard: tuple[int, int] | None = None, sleep: float = 0.3,
                      force: bool = False) -> dict:
    """전수 인벤토리 갱신 — **변경분만**(정기 파싱, 사용자 2026-06-17). 회사별 최신
    정기보고서 rcept_no(list.json·경량) 확인 → 안 바뀌면 document.xml 파싱 skip,
    바뀐 회사만 재파싱. 월말/마지막주 타이머용. shard=(i,m) 면 codes[i::m] 만(분할 실행).
    force=True 면 rcept 변경 없어도 전수 재파싱(파서 개선 반영용, 사용자 2026-06-18).

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
    failures: list[dict] = []   # {code, company, reason, rcept_no} — silent-fail 금지(#12d)

    def _fail(code, reason, rcept=None):
        nonlocal failed
        failed += 1
        company = dart.stock_code_to_name(code) or code
        # 보고서는 있으나 추출 0 인데 금융·스팩·리츠류면 구조적 제외(파서 버그 아님).
        if reason == "parse_empty" and _no_revenue_breakdown(company):
            reason = "no_breakdown"
        failures.append({"code": code, "company": company,
                         "reason": reason, "rcept_no": rcept or ""})

    for n, code in enumerate(codes, 1):
        try:
            corp = dart.stock_code_to_corp_code(code)
            rep = pick_business_report(_fetch_report_list(key, corp)) if corp else None
            if not rep:
                # 정기보고서 자체 없음 = 스팩/펀드/리츠/신규·관리종목 — 파서 문제 아님.
                _fail(code, "no_report")
                continue
            if not force and not _needs_rebuild(inv.get(code), rep["rcept_no"]):
                skipped += 1
                continue
            r = fetch_company_products(code, key)   # 변경분만(또는 force 전수) document.xml 파싱
            if r:
                inv[code] = r
                built += 1
            else:
                # 보고서는 있는데 매출구성 추출 0 = 포맷 불일치(상장사 — 파서 보강 대상).
                _fail(code, "parse_empty", rep.get("rcept_no"))
        except Exception as exc:
            log.warning("refresh_inventory %s: %s", code, exc)
            _fail(code, f"error:{exc.__class__.__name__}")
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
    _save_failures(failures)
    n_parse = sum(1 for f in failures if f["reason"] == "parse_empty")
    n_nobreak = sum(1 for f in failures if f["reason"] == "no_breakdown")
    n_noreport = sum(1 for f in failures if f["reason"] == "no_report")
    res = {"built": built, "skipped": skipped, "failed": failed, "total": len(codes),
           "parse_empty": n_parse, "no_breakdown": n_nobreak, "no_report": n_noreport}
    log.info("refresh_inventory 완료: %s", res)
    log.info("  → 실패 내역: 정기보고서없음 %d · 구조적제외(금융·스팩·리츠) %d · "
             "파싱0(진짜 보강대상) %d · `--failures` 로 목록 확인",
             n_noreport, n_nobreak, n_parse)
    return res


_FAILURES_FILE = "dart_revenue_failures.json"

# 금융·투자·스팩·리츠·신탁 — 제조업식 '제품별 매출구성'이 구조적으로 없음(이자·수수료·
# 보험료·임대·투자수익 구조). 파서 버그가 아니라 의도된 제외 (사용자 2026-06-18 rescan
# 268건 중 ~절반). DART corp 업종분류 부재 시 회사명 패턴이 실용적. '생명과학'(bio,
# 실제 매출구성 있음)은 endswith('생명')로만 보험사(동양생명) 구분.
_NO_BREAKDOWN_KW = (
    "은행", "뱅크", "증권", "화재", "보험", "캐피탈", "카드", "금융", "파이낸셜",
    "인베스트", "벤처스", "기술투자", "창업투자", "ib투자", "파트너스",
    "자산운용", "리츠", "신탁", "기업인수목적", "스팩", "선박투자",
)


def _no_revenue_breakdown(name: str) -> bool:
    """회사명이 금융·투자·스팩·리츠류 → 제조업식 제품 매출구성이 구조적으로 없음
    (= 파서 보강 대상 아님, 의도된 제외). 순수 함수 — 단위테스트."""
    key = (name or "").replace(" ", "").lower()
    if not key:
        return False
    if key.endswith("생명"):                      # 생명보험사(동양생명) ↔ 생명과학(bio) 구분
        return True
    return any(k in key for k in _NO_BREAKDOWN_KW)


def _save_failures(failures: list[dict]) -> None:
    """실패 내역 저장 (가시성 — `--failures` 가 읽음). best-effort."""
    try:
        p = _data_dir() / _FAILURES_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("dart_revenue 실패내역 저장: %s", exc)


def load_failures() -> list[dict]:
    """저장된 실패 내역 [{code, company, reason, rcept_no}] (없으면 [])."""
    try:
        return json.loads((_data_dir() / _FAILURES_FILE).read_text(encoding="utf-8"))
    except Exception:
        return []


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
    p.add_argument("--audit", action="store_true",
                   help="저장된 인벤토리의 파싱 의심 항목만 출력(고도화 대상)")
    p.add_argument("--failures", action="store_true",
                   help="직전 --refresh 의 실패 목록 — 파싱0(상장사·포맷 불일치) 우선 출력")
    p.add_argument("--rescan-failures", action="store_true",
                   help="인벤토리 미수록(=직전 실패) 종목만 재시도 → 실패 분류 기록"
                        "(전수 재스캔 회피, 이미 추출된 종목은 건너뜀)")
    p.add_argument("--force", action="store_true",
                   help="--refresh 시 rcept 변경 없어도 전수 재파싱(파서 개선 반영)")
    args = p.parse_args(argv)
    if args.failures:               # 실패 목록 — DART 키 불요(저장 파일만)
        fails = load_failures()
        if not fails:
            print("실패 내역 파일 없음 — 먼저 `--refresh` 를 한 번 실행하세요 "
                  "(dart_revenue_failures.json 생성).")
            return 0
        # 파싱0 을 구조적 제외(금융·스팩·리츠) ↔ 진짜 보강 대상으로 분리. 저장된
        # reason 이 옛 'parse_empty' 뿐인 파일도 회사명으로 재분류(사용자 2026-06-18).
        pe_or_nb = [f for f in fails if f["reason"] in ("parse_empty", "no_breakdown")]
        real = [f for f in pe_or_nb
                if f["reason"] != "no_breakdown" and not _no_revenue_breakdown(f["company"])]
        no_breakdown = [f for f in pe_or_nb if f not in real]
        errors = [f for f in fails if str(f["reason"]).startswith("error")]
        no_report = [f for f in fails if f["reason"] == "no_report"]
        print(f"\n🔎 DART 파싱 실패 — 총 {len(fails)} (진짜 보강 {len(real)} · "
              f"구조적 제외[금융·스팩·리츠] {len(no_breakdown)} · 오류 {len(errors)} · "
              f"정기보고서없음 {len(no_report)})")
        print("─" * 96)
        print(f"\n■ 진짜 보강 대상 — 보고서 있고 제조업식 매출구성이 있어야 하는데 추출 실패 "
              f"(상장사·포맷 불일치) {len(real)}건")
        for f in real:
            print(f"  {f['code']} {f['company'][:18]:<20} rcept={f.get('rcept_no','')}")
        print(f"\n■ 구조적 제외 {len(no_breakdown)}건 — 은행·증권·보험·캐피탈·금융지주·"
              "스팩·리츠·신탁·VC (제품 매출구성이 구조적으로 없음, 파서 버그 아님)")
        if errors:
            print(f"\n■ 오류 (예외) {len(errors)}건")
            for f in errors:
                print(f"  {f['code']} {f['company'][:18]:<20} {f['reason']}")
        print(f"\n■ 정기보고서 없음 {len(no_report)}건 — 스팩·펀드·리츠·신규/관리종목 "
              "(파서 문제 아님, 정상)")
        print("\n→ 진짜 보강 대상 1건 원문 확인: "
              ".venv/bin/python -m trade.scripts.probe_dart_revenue --tickers <code>")
        print("  → 원문 저장 위치: <DATA>/dart_revenue_probe/raw/<code>_<rcept>.txt "
              "(표 마크업+평문 — 포맷 파악 후 파서 보강)")
        return 0
    if args.audit:                  # 인벤토리 audit — DART 키 불요(저장 파일만)
        sus = audit_inventory()
        inv = load_inventory()
        print(f"\n🔎 파싱 audit — 의심 {len(sus)}/{len(inv)} 사")
        print("─" * 96)
        for s in sus:
            print(f"  {s['code']} {s['company'][:16]:<18} 제품{s['n']} · {' | '.join(s['reasons'])[:70]}")
        print(f"\n→ 이 목록의 원문을 보고(아래) 전용 파서/별칭 보강:")
        print("   회사 1건 원문: .venv/bin/python -m trade.company_report <회사명>  # 또는 probe raw/")
        return 0
    _load_env()
    key = (os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        print("⛔ DART_API_KEY 없음 — ~/stock/.env 확인.")
        return 2
    if args.rescan_failures:
        # 직전 실패 = 전 상장사 − 인벤토리 수록분. 이것만 재시도 → 전수(3970) 스캔 회피.
        all_codes = all_listed_codes()
        failed = sorted(set(all_codes) - set(load_inventory().keys()))
        if not failed:
            print("재시도 대상 없음 — 인벤토리가 전 상장사를 커버.")
            return 0
        print(f"🔁 인벤토리 미수록 {len(failed)}종 재시도 (전체 {len(all_codes)} 중) — "
              "실패 분류 기록 중…")
        res = refresh_inventory(codes=failed, api_key=key)
        print(f"📦 실패 재스캔: {res}")
        print("→ `--failures` 로 파싱0(상장사·포맷 불일치) 목록 확인")
        return 0
    if args.refresh:
        shard = None
        if args.shard and "/" in args.shard:
            i, m = args.shard.split("/", 1)
            shard = (int(i), int(m))
        res = refresh_inventory(api_key=key, shard=shard, force=args.force)
        print(f"📦 전수 갱신{' (force 전수 재파싱)' if args.force else ''}: {res}")
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
