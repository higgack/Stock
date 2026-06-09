"""DART 전체 시장 공시 피드 — 30분 간격 수집 + 아카이브 + 대시보드.

DART list.json 을 corp_code 없이(전체 시장) 호출해 당일 주요 공시를 수집.
chart_events.classify 로 카테고리 분류, dart_detail 로 계약/실적/자금조달
구조화 숫자 추출. 비용 ₩0 (DART API 무료, LLM 0).

아카이브: ~/.tradingagents/dart_feed_archive/YYYY-MM-DD.json
30분마다 갱신 — 새 공시만 append (rcept_no dedup).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("bot.dart_feed")

_DART_BASE = "https://opendart.fss.or.kr/api"
_TIMEOUT = 15
_ARCHIVE_DIR = Path.home() / ".tradingagents" / "dart_feed_archive"
_KST = timezone(timedelta(hours=9))

# ── 카테고리 분류 (chart_events.classify 재사용하되, 대시보드용 한국어 라벨) ──

_CATEGORY_MAP = {
    "order": "계약",
    "shareholder": "주주환원",
    "capital": "자금조달",
    "capex": "신규시설투자",
    "mna": "자산양수도",
    "risk": "리스크",
    "control": "회사구조",
    "litigation": "소송",
}

_CATEGORY_COLORS = {
    "계약": "#26a69a",
    "실적": "#42a5f5",
    "주주환원": "#ab47bc",
    "자금조달": "#f5a623",
    "신규시설투자": "#2196f3",
    "자산양수도": "#009688",
    "리스크": "#ef5350",
    "소송": "#26a69a",
    "회사구조": "#ff7043",
    "지분공시": "#ec407a",
}

# DART report_nm 패턴 → 우리 카테고리 (chart_events.classify 보다 세분화)
_EARNINGS_KW = ("영업(잠정)실적", "매출액또는손익구조", "영업실적", "잠정실적")
_EQUITY_KW = ("주식등의대량보유상황보고서", "임원ㆍ주요주주특정증권등소유상황보고서",
              "주요주주특정증권등소유상황보고서", "임원·주요주주특정증권등소유상황보고서")


def _classify_report(report_nm: str) -> str:
    """DART report_nm → 한국어 카테고리."""
    t = report_nm or ""
    if any(k in t for k in _EARNINGS_KW):
        return "실적"
    if any(k in t for k in _EQUITY_KW):
        return "지분공시"
    from bot.chart_events import classify
    eng = classify(t)
    return _CATEGORY_MAP.get(eng, "지분공시" if "보고서" in t else "기타")


def _dart_api_key() -> str | None:
    return (os.environ.get("DART_API_KEY") or "").strip() or None


# ── 구조화 상세 추출 ──

def _extract_detail(report_nm: str, rcept_no: str, corp_code: str,
                    api_key: str) -> dict | None:
    """주요사항보고서에서 핵심 숫자 추출. None if not applicable."""
    from bot.dart_detail import _won, _pick

    t = report_nm or ""

    specs: list[tuple[str, list]] = []
    if "공급계약" in t or "단일판매" in t or "단일공급" in t:
        specs = [("soptTrfCntrDecsn", [
            ("계약내용", ("ctrt_cn",), "text"),
            ("계약금액", ("ctrt_amt",), "won"),
            ("매출액대비", ("sl_cmpnt_rt",), "pct"),
            ("계약상대", ("cntr_pty",), "text"),
            ("계약기간", ("cntr_pd",), "text"),
            ("공급지역", ("dlvy_rgn",), "text"),
            ("계약일", ("ctrt_de",), "date"),
        ])]
    elif "영업(잠정)실적" in t or "매출액또는손익구조" in t:
        specs = [("irdsSttus", [
            ("매출액", ("slsAmt", "sls_amt", "thmAmt"), "won"),
            ("영업이익", ("bsopPrfl", "bsop_prfl", "thmBsopPrfl"), "won"),
            ("순이익", ("thqrNtinc", "thqr_ntinc", "thmNtinc"), "won"),
        ])]
    elif "유상증자" in t:
        specs = [("piicDecsn", [
            ("방식", ("ic_mthn",), "text"),
            ("신주수", ("nstk_ostk_cnt",), "num"),
            ("시설자금", ("fdpp_fclt",), "won"),
            ("운영자금", ("fdpp_op",), "won"),
        ])]
    elif "전환사채" in t:
        specs = [("cvbdIsDecsn", [
            ("권면총액", ("bd_fta", "bd_tota"), "won"),
            ("전환가격", ("cv_prc",), "won"),
            ("이자율", ("bd_intr_ex",), "text"),
            ("만기", ("bd_mtrd",), "date"),
            ("자금용도", ("fdpp_op",), "text"),
        ])]
    elif "자기주식" in t:
        specs = [("tsstkAqDecsn", [
            ("취득예정", ("aqpln_stk_ostk", "aqpln_stk"), "num"),
            ("계약금액", ("aqpln_prc_ostk", "aqpln_prc"), "won"),
            ("목적", ("aq_pp",), "text"),
        ])]
    elif "합병" in t:
        specs = [("cmpMgDecsn", [
            ("합병비율", ("mg_rt",), "text"),
            ("상대회사", ("mgprt_cmpnm", "cmpcmpnm"), "text"),
        ])]

    if not specs:
        return None

    for ep, fields in specs:
        try:
            r = requests.get(
                f"{_DART_BASE}/{ep}.json",
                params={"crtfc_key": api_key, "corp_code": corp_code,
                        "bgn_de": (date.today() - timedelta(days=7)).strftime("%Y%m%d"),
                        "end_de": date.today().strftime("%Y%m%d")},
                timeout=_TIMEOUT)
            js = r.json()
        except Exception:
            continue
        if not isinstance(js, dict) or js.get("status") != "000":
            continue
        for row in js.get("list") or []:
            rno = str(row.get("rcept_no") or "").strip()
            if rno != rcept_no:
                continue
            parts: list[str] = []
            for lbl, keys, kind in fields:
                v = _pick(row, keys)
                if v is None:
                    continue
                if kind == "won":
                    w = _won(v)
                    if w:
                        parts.append(f"{lbl}: {w}")
                elif kind == "num":
                    try:
                        parts.append(f"{lbl}: {int(str(v).replace(',', '')):,}주")
                    except (TypeError, ValueError):
                        parts.append(f"{lbl}: {v}")
                elif kind == "pct":
                    parts.append(f"{lbl}: {v}%")
                elif kind == "date":
                    parts.append(f"{lbl}: {v}")
                else:
                    parts.append(f"{lbl}: {str(v)[:50]}")
            if parts:
                return {"lines": parts}
    return None


# ── PER 산출 (실적 공시용) ──

def _compute_per(corp_name: str, net_income_str: str) -> str | None:
    """yfinance 시총 / 순이익 → PER. graceful None."""
    try:
        import yfinance as yf
        ni = float(str(net_income_str).replace(",", ""))
        if ni <= 0:
            return None
        from bot.dart_client import get_dart
        d = get_dart()
        entries = d.find_by_name(corp_name)
        if not entries:
            return None
        code = entries[0].get("stock_code")
        if not code:
            return None
        suffix = ".KS"
        tk = yf.Ticker(f"{code}{suffix}")
        mc = getattr(tk, "fast_info", {})
        mcap = getattr(mc, "market_cap", None)
        if not mcap:
            return None
        per = mcap / ni
        if 0 < per < 500:
            return f"{per:.1f}"
    except Exception:
        pass
    return None


# ── 전체 시장 공시 fetch ──

def fetch_market_disclosures(target_date: date | None = None,
                             max_pages: int = 20,
                             days_back: int = 3,
                             skip_routine: bool = True) -> list[dict]:
    """DART list.json 전체 시장 호출 → 분류된 공시 리스트.

    days_back: target_date 기준 며칠 전부터 fetch (최근 N+1일 윈도).
        장 마감 후 공시가 몰리는 KR 특성상 당일만 보면 새벽엔 0건 →
        최근 3일 윈도로 항상 최근 공시를 보장.
    skip_routine: '기타'(정정/단순공고 등 catalyst 무관 routine) 제외해
        실적/계약/주주환원/자금조달/시설투자/지분공시 등 의미있는 공시만.
    """
    api_key = _dart_api_key()
    if not api_key:
        log.warning("dart_feed: DART_API_KEY not set")
        return []

    if target_date is None:
        target_date = datetime.now(_KST).date()

    end_ds = target_date.strftime("%Y%m%d")
    bgn_ds = (target_date - timedelta(days=max(0, days_back))).strftime("%Y%m%d")
    out: list[dict] = []
    seen_rcept: set[str] = set()

    for page_no in range(1, max_pages + 1):
        try:
            resp = requests.get(
                f"{_DART_BASE}/list.json",
                params={
                    "crtfc_key": api_key,
                    "bgn_de": bgn_ds,
                    "end_de": end_ds,
                    "page_no": page_no,
                    "page_count": 100,
                },
                timeout=_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart_feed: list.json page %d failed: %s", page_no, exc)
            break

        if payload.get("status") not in ("000",):
            break

        for r in payload.get("list") or []:
            rcept_no = r.get("rcept_no", "")
            if rcept_no in seen_rcept:
                continue
            seen_rcept.add(rcept_no)

            report_nm = (r.get("report_nm") or "").strip()
            corp_name = (r.get("corp_name") or "").strip()
            corp_code = (r.get("corp_code") or "").strip()
            stock_code = (r.get("stock_code") or "").strip()
            rcept_dt = r.get("rcept_dt") or end_ds

            category = _classify_report(report_nm)
            if skip_routine and category == "기타":
                continue

            item = {
                "rcept_no": rcept_no,
                "date": rcept_dt,
                "corp_name": corp_name,
                "corp_code": corp_code,
                "stock_code": stock_code,
                "report_nm": report_nm,
                "category": category,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                "flr_nm": (r.get("flr_nm") or "").strip(),
            }
            out.append(item)

        try:
            if page_no >= int(payload.get("total_page") or 1):
                break
        except (TypeError, ValueError):
            break

    return out


def enrich_disclosures(items: list[dict]) -> list[dict]:
    """구조화 상세 추출 + PER 계산으로 보강. in-place + return."""
    api_key = _dart_api_key()
    if not api_key:
        return items

    for item in items:
        cat = item.get("category", "")
        report_nm = item.get("report_nm", "")
        rcept_no = item.get("rcept_no", "")
        corp_code = item.get("corp_code", "")

        if cat in ("계약", "자금조달", "주주환원") or "실적" in cat:
            if corp_code:
                detail = _extract_detail(report_nm, rcept_no, corp_code, api_key)
                if detail:
                    item["detail"] = detail.get("lines", [])
    return items


# ── 아카이브 ──

def _archive_path(d: date) -> Path:
    return _ARCHIVE_DIR / f"{d.strftime('%Y-%m-%d')}.json"


def load_archive(d: date) -> list[dict]:
    p = _archive_path(d)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_archive(d: date, items: list[dict]) -> None:
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    p = _archive_path(d)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)


def merge_and_save(d: date, new_items: list[dict]) -> list[dict]:
    """기존 아카이브에 새 공시 merge (rcept_no dedup). 반환: 전체."""
    existing = load_archive(d)
    seen = {it["rcept_no"] for it in existing}
    added = 0
    for it in new_items:
        if it["rcept_no"] not in seen:
            existing.append(it)
            seen.add(it["rcept_no"])
            added += 1
    if added > 0:
        save_archive(d, existing)
        log.info("dart_feed: %s — %d new, %d total", d, added, len(existing))
    return existing


def load_all_archives(days_back: int = 30) -> dict[str, list[dict]]:
    """최근 N일 아카이브 로드. {date_str: [items]}."""
    result: dict[str, list[dict]] = {}
    today = datetime.now(_KST).date()
    for i in range(days_back):
        d = today - timedelta(days=i)
        items = load_archive(d)
        if items:
            result[d.strftime("%Y-%m-%d")] = items
    return result


# ── CLI: python -m bot.dart_feed ──

def run_once(target_date: date | None = None,
             days_back: int = 3) -> list[dict]:
    """1회 fetch(최근 days_back+1일 윈도) → enrich → 공시일별로 분배 merge.

    각 item 을 실제 접수일(rcept_dt)의 아카이브 파일에 저장(dedup)해 대시보드
    날짜 그룹과 일치. 새벽 당일 0건이어도 직전 거래일 공시가 보여짐."""
    if target_date is None:
        target_date = datetime.now(_KST).date()
    log.info("dart_feed: fetching %s (최근 %d일)", target_date, days_back + 1)
    items = fetch_market_disclosures(target_date, days_back=days_back)
    if items:
        enrich_disclosures(items)

    # 접수일(rcept_dt 'YYYYMMDD')별 그룹핑 → 각 날짜 파일에 merge
    by_day: dict[date, list[dict]] = {}
    for it in items:
        raw = str(it.get("date") or "").strip()
        try:
            d = datetime.strptime(raw[:8], "%Y%m%d").date()
        except (ValueError, TypeError):
            d = target_date
        by_day.setdefault(d, []).append(it)

    for d, day_items in by_day.items():
        merge_and_save(d, day_items)
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from pathlib import Path as _P
    env_path = _P.home() / "stock" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    items = run_once()
    print(f"dart_feed: {len(items)} disclosures archived")

    # regenerate dashboard
    try:
        from bot.dashboard import regenerate_dart_feed_index
        regenerate_dart_feed_index()
        print("dart_feed: dashboard regenerated")
    except Exception as exc:
        print(f"dart_feed: dashboard regen failed: {exc}")
