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
import time
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
# 기업설명회(IR) — 한국 기업은 IR 일정을 공시로 미리 발표(다가오는 실적/IR 신호)
_IR_KW = ("기업설명회", "IR개최", "IR 개최", "기업설명회(IR)")
_EQUITY_KW = ("주식등의대량보유상황보고서", "임원ㆍ주요주주특정증권등소유상황보고서",
              "주요주주특정증권등소유상황보고서", "임원·주요주주특정증권등소유상황보고서")


def _classify_report(report_nm: str) -> str:
    """DART report_nm → 한국어 카테고리."""
    t = report_nm or ""
    if any(k in t for k in _EARNINGS_KW):
        return "실적"
    if any(k in t for k in _IR_KW):
        return "IR"
    if any(k in t for k in _EQUITY_KW):
        return "지분공시"
    from bot.chart_events import classify
    eng = classify(t)
    return _CATEGORY_MAP.get(eng, "지분공시" if "보고서" in t else "기타")


def fetch_kr_earnings_ir(days_back: int = 10) -> list[dict]:
    """DART 아카이브에서 한국 IR(기업설명회) 공시만 추출 — 캘린더용.

    사용자 정책 2026-06-09: 캘린더에는 IR 일정만. 실적공시는 DART 피드
    페이지가 커버하므로 캘린더에서 제외. 이미 5분 타이머가 채운 아카이브를
    재활용 → 추가 fetch·비용 0. 반환 [{name, code, date, type='IR', title,
    url}] 날짜 내림차순."""
    out: list[dict] = []
    by_date = load_all_archives(days_back=days_back)
    for date_str, items in by_date.items():
        for it in items:
            cat = it.get("category")
            # IR(기업설명회)만. 실적/매출손익구조 등은 캘린더에서 제외.
            if cat != "IR":
                continue
            raw = str(it.get("date") or "").strip()
            try:
                d_iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 else date_str
            except Exception:
                d_iso = date_str
            out.append({
                "name": it.get("corp_name", ""),
                "code": it.get("stock_code", ""),
                "date": d_iso,
                "type": cat,
                "title": it.get("report_nm", ""),
                "url": it.get("url", "#"),
            })
    out.sort(key=lambda x: x.get("date", ""), reverse=True)
    return out


_IR_MONTH_CACHE = Path.home() / ".tradingagents" / "cache" / "dart_ir_month"
_IR_MONTH_TTL = 12 * 3600
_IR_CHUNK_DAYS = 7          # 주 단위 윈도로 쪼개 firehose 깊이를 분산
_IR_CHUNK_MAX_PAGES = 20    # 청크당 페이지 상한(과거 월 1회 cold load 시간 bound)


def fetch_kr_ir_month(year: int, month: int) -> list[dict]:
    """특정 월의 한국 IR(기업설명회) 공시 — 캘린더 과거 월 채움.

    아카이브(최근 30일)가 닿지 않는 과거 월을 채운다. **June 가 정상 채워지는
    것과 동일 경로**(fetch_market_disclosures 전체 firehose + _classify_report
    분류)를 주(7일) 단위로 쪼개 월 전체를 훑어 category=='IR' 만 추린다 —
    pblntf_ty 필터 미사용(검증된 archive 경로와 동일, 이전 'I' 필터가 IR 을
    걸러 5월이 비던 문제 해소). 공시 접수일에 배치. 월별 12h 디스크 캐시.
    실패/키부재 → [] (호출부가 archive 폴백). 미래 월은 [].

    ⚠️ 과거 월 첫 호출은 firehose 페이지네이션으로 느릴 수 있다(이후 캐시).
    페이지 상한 때문에 매우 바쁜 월의 가장 오래된 날은 일부 누락될 수 있다 —
    부분이라도 채우는 게 목적(완전 백필은 별도)."""
    api_key = _dart_api_key()
    if not api_key:
        return []
    today = datetime.now(_KST).date()
    first = date(year, month, 1)
    if first > today:
        return []
    last = (date(year + 1, 1, 1) if month == 12
            else date(year, month + 1, 1)) - timedelta(days=1)
    end = min(last, today)

    tag = f"{year:04d}-{month:02d}"
    _IR_MONTH_CACHE.mkdir(parents=True, exist_ok=True)
    # v2 = firehose+classify(no pblntf_ty) 경로. 이전 'I' 필터판 빈 캐시 무효화.
    cache_file = _IR_MONTH_CACHE / f"{tag}_v2.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _IR_MONTH_TTL:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    first_iso, end_iso = first.isoformat(), end.isoformat()
    out: dict[str, dict] = {}  # rcept_no → item (dedup)
    chunk_end = end
    while chunk_end >= first:
        chunk_start = max(first, chunk_end - timedelta(days=_IR_CHUNK_DAYS - 1))
        span = (chunk_end - chunk_start).days
        try:
            items = fetch_market_disclosures(
                target_date=chunk_end, days_back=span,
                max_pages=_IR_CHUNK_MAX_PAGES, skip_routine=True)
        except Exception as exc:
            log.warning("dart_feed: ir_month %s chunk %s failed: %s",
                        tag, chunk_end, exc)
            items = []
        for it in items:
            if it.get("category") != "IR":
                continue
            raw = str(it.get("date") or "").strip()
            d_iso = (f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8
                     else chunk_end.isoformat())
            if not (first_iso <= d_iso <= end_iso):
                continue
            rid = (it.get("rcept_no")
                   or f"{it.get('stock_code')}-{d_iso}-{it.get('report_nm')}")
            out[rid] = {
                "name": it.get("corp_name", ""),
                "code": it.get("stock_code", ""),
                "date": d_iso,
                "type": "IR",
                "title": it.get("report_nm", ""),
                "url": it.get("url", "#"),
            }
        chunk_end = chunk_start - timedelta(days=1)

    result = sorted(out.values(), key=lambda x: x.get("date", ""), reverse=True)
    log.info("dart_feed: ir_month %s → %d IR 공시", tag, len(result))
    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


def _dart_api_key() -> str | None:
    return (os.environ.get("DART_API_KEY") or "").strip() or None


# ── 구조화 상세 추출 ──

_DATE_RE = re.compile(r"(\d{4})[.\-/]?\s?(\d{1,2})[.\-/]?\s?(\d{1,2})")


def _fmt_period(s: str) -> str:
    """'2026-06-08 ~ 2026-12-31' 류 계약기간 → '2026-06-08 ~ 2026-12-31
    (7개월)'. 두 날짜 파싱해 개월수(round(일수/30.44)) 부기. 실패 시 원문.
    레퍼런스 봇 '일정 (N개월)' 형식(사용자 2026-06-10)."""
    if not s:
        return s
    found = _DATE_RE.findall(s)
    if len(found) < 2:
        return s.strip()
    try:
        from datetime import date as _d
        (y1, m1, d1), (y2, m2, d2) = found[0], found[1]
        start = _d(int(y1), int(m1), int(d1))
        end = _d(int(y2), int(m2), int(d2))
        days = (end - start).days
        if days <= 0:
            return s.strip()
        months = max(1, round(days / 30.44))
        return f"{start.isoformat()} ~ {end.isoformat()} ({months}개월)"
    except (ValueError, TypeError):
        return s.strip()


def _to_float(v) -> float | None:
    """콤마/공백 포함 문자열 → float. 실패 시 None."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _extract_majorstock(rcept_no: str, corp_code: str,
                        api_key: str) -> dict | None:
    """주식등의대량보유상황보고서(5%) → majorstock.json 구조화.

    보고자 / 지분율 변동(직전→현재, %p) / 변동주식 / 보고사유. majorstock 은
    bgn_de·end_de 없이 corp_code 로 해당 법인의 대량보유 보고 list 를 반환 →
    rcept_no 로 매칭. graceful None(키·네트워크·필드 부재 무해)."""
    try:
        r = requests.get(
            f"{_DART_BASE}/majorstock.json",
            params={"crtfc_key": api_key, "corp_code": corp_code},
            timeout=_TIMEOUT)
        js = r.json()
    except Exception:
        return None
    if not isinstance(js, dict) or js.get("status") != "000":
        return None
    for row in js.get("list") or []:
        if str(row.get("rcept_no") or "").strip() != str(rcept_no):
            continue
        parts: list[str] = []
        repror = str(row.get("repror") or "").strip()
        if repror:
            parts.append(f"보고자: {repror}")
        stkrt = _to_float(row.get("stkrt"))        # 보유비율(%)
        d_rt = _to_float(row.get("stkrt_irds"))    # 보유비율 증감(%p)
        if stkrt is not None and d_rt is not None:
            before = stkrt - d_rt
            arrow = "▲" if d_rt > 0 else ("▼" if d_rt < 0 else "–")
            parts.append(
                f"지분율: {before:.2f}% → {stkrt:.2f}% ({d_rt:+.2f}%p {arrow})")
        elif stkrt is not None:
            parts.append(f"지분율: {stkrt:.2f}%")
        d_qy = _to_float(row.get("stkqy_irds"))    # 보유주식등의 증감
        if d_qy:
            parts.append(f"변동주식: {int(d_qy):+,}주")
        resn = str(row.get("report_resn") or "").strip()
        if resn:
            parts.append(f"보고사유: {resn}")
        if parts:
            return {"lines": parts}
    return None


def _extract_detail(report_nm: str, rcept_no: str, corp_code: str,
                    api_key: str) -> dict | None:
    """주요사항보고서에서 핵심 숫자 추출. None if not applicable."""
    from bot.dart_detail import _won, _pick

    t = report_nm or ""

    # 대량보유 5% 보고서 — majorstock.json(지분공시 종합정보). 주요사항보고서와
    # 응답 구조가 달라 전용 처리: 보고자·지분율 변동(직전→현재, %p)·변동주식·
    # 보고사유. (프로텍·하이브 레퍼런스 카드 대응, 무료.)
    if "대량보유" in t:
        return _extract_majorstock(rcept_no, corp_code, api_key)

    specs: list[tuple[str, list]] = []
    if "공급계약" in t or "단일판매" in t or "단일공급" in t:
        specs = [("soptTrfCntrDecsn", [
            ("계약내용", ("ctrt_cn",), "text"),
            ("계약금액", ("ctrt_amt",), "won"),
            ("매출액대비", ("sl_cmpnt_rt",), "pct"),
            ("계약상대", ("cntr_pty",), "text"),
            ("계약기간", ("cntr_pd",), "period"),
            ("공급지역", ("dlvy_rgn",), "text"),
            ("계약일", ("ctrt_de",), "date"),
        ])]
    elif "영업(잠정)실적" in t or "매출액또는손익구조" in t:
        specs = [("irdsSttus", [
            ("매출액", ("slsAmt", "sls_amt", "thmAmt"), "won"),
            ("영업이익", ("bsopPrfl", "bsop_prfl", "thmBsopPrfl"), "won"),
            ("순이익", ("thqrNtinc", "thqr_ntinc", "thmNtinc"), "won"),
        ])]
    elif "유무상증자" in t:  # 유무상 먼저(무상증자⊂유무상증자 substring 충돌 방지)
        specs = [("pifricDecsn", [
            ("방식", ("ic_mthn",), "text"),
            ("신주수", ("nstk_ostk_cnt",), "num"),
            ("시설자금", ("fdpp_fclt",), "won"),
            ("운영자금", ("fdpp_op",), "won"),
        ])]
    elif "무상증자" in t:
        specs = [("fricDecsn", [
            ("신주(보통)", ("nstk_ostk_cnt",), "num"),
            ("1주당 배정", ("nstk_ascnt_ps_ostk",), "text"),
            ("배정기준일", ("nstk_asstd",), "date"),
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
    elif "신주인수권부사채" in t:
        specs = [("bdwtIsDecsn", [
            ("권면총액", ("bd_fta", "bd_tota"), "won"),
            ("행사가격", ("ex_prc", "exrow_prc"), "won"),
            ("이자율", ("bd_intr_ex",), "text"),
            ("만기", ("bd_mtrd",), "date"),
            ("자금용도", ("fdpp_op",), "text"),
        ])]
    elif "교환사채" in t:
        specs = [("exbdIsDecsn", [
            ("권면총액", ("bd_fta", "bd_tota"), "won"),
            ("교환가격", ("ex_prc", "exc_prc"), "won"),
            ("만기", ("bd_mtrd",), "date"),
            ("자금용도", ("fdpp_op",), "text"),
        ])]
    elif "자기주식" in t and "처분" in t:
        specs = [("tsstkDpDecsn", [
            ("처분수량(보통)", ("dppln_stk_ostk", "dppln_stk"), "num"),
            ("처분금액", ("dpstk_prc_ostk", "dppln_prc"), "won"),
            ("처분목적", ("dp_pp",), "text"),
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
    elif "타법인" in t and "양수" in t:
        specs = [("otcprStkInvscrInhDecsn", [
            ("대상회사", ("iscmp_cmpnm", "cmpnm"), "text"),
            ("양수주식", ("inhstk_cnt", "inh_stk_cnt"), "num"),
            ("양수금액", ("inh_prc", "trf_prc"), "won"),
        ])]
    elif "타법인" in t and "양도" in t:
        specs = [("otcprStkInvscrTrfDecsn", [
            ("대상회사", ("iscmp_cmpnm", "cmpnm"), "text"),
            ("양도주식", ("trfstk_cnt", "trf_stk_cnt"), "num"),
            ("양도금액", ("trf_prc", "inh_prc"), "won"),
        ])]
    elif "영업양수" in t:
        specs = [("bsnInhDecsn", [
            ("양수대상", ("inh_sbjc", "trf_sbjc"), "text"),
            ("양수가액", ("inh_prc", "trf_prc"), "won"),
            ("목적", ("inh_pp", "trf_pp"), "text"),
        ])]
    elif "영업양도" in t:
        specs = [("bsnTrfDecsn", [
            ("양도대상", ("trf_sbjc", "inh_sbjc"), "text"),
            ("양도가액", ("trf_prc", "inh_prc"), "won"),
            ("목적", ("trf_pp", "inh_pp"), "text"),
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
                elif kind == "period":
                    # 계약기간 텍스트 → 시작/종료일 + 개월수(레퍼런스 봇
                    # '일정 (N개월)' 형식, 사용자 2026-06-10). 파싱 실패 시 원문.
                    parts.append(f"{lbl}: {_fmt_period(str(v))}")
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

    # 멱등 가드 — run_once 가 최근 4일 윈도를 5분마다 재fetch·재enrich 하므로,
    # 아카이브에 이미 detail 이 있으면 DART 재호출 없이 그대로 재사용한다.
    # (고빈도 카테고리 '지분공시'(대량보유 5%) 추가로 호출량이 폭증하는 것을
    # 방지 + 기존 카테고리도 동일 이득. 추출 실패=detail 부재분만 재시도.)
    known: dict[str, list] = {}
    try:
        for day_items in load_all_archives(days_back=5).values():
            for it in day_items:
                rno = it.get("rcept_no")
                det = it.get("detail")
                if rno and det:
                    known[str(rno)] = det
    except Exception:
        known = {}

    for item in items:
        cat = item.get("category", "")
        report_nm = item.get("report_nm", "")
        rcept_no = str(item.get("rcept_no", ""))
        corp_code = item.get("corp_code", "")

        if rcept_no and rcept_no in known:
            item["detail"] = known[rcept_no]
            continue

        if cat in ("계약", "자금조달", "주주환원", "신규시설투자",
                   "지분공시", "자산양수도") or "실적" in cat:
            if corp_code:
                detail = _extract_detail(report_nm, rcept_no, corp_code, api_key)
                lines = list(detail.get("lines", [])) if detail else []
                # 시총·현재가 부착 (다른 봇 수준 — 사용자 2026-06-10). FSC
                # (무료·12h 캐시·T+1)로 시총·종가. 구조화 detail 있는 카드만
                # (계약/실적/자금조달/주주환원/신규시설투자) → ~소수 카드.
                sc = item.get("stock_code", "")
                if lines and sc and len(sc) == 6 and sc.isdigit():
                    # 주요사업(업종) — DART 업종코드(KSIC) → 한글 (무료·캐시).
                    # 레퍼런스 봇의 '주요사업' 대응(원문 텍스트는 아니지만
                    # 업종 분류로 사업영역 표시). 맨 앞에.
                    lines = _industry_line(sc) + lines
                    lines += _market_cap_price_lines(sc)
                if lines:
                    item["detail"] = lines
    return items


# KSIC(한국표준산업분류) 2자리 division → 한글 업종 (KR 상장사 빈출 위주).
_KSIC_DIV = {
    "01": "농업", "03": "어업", "05": "석탄·원유·천연가스 광업", "06": "금속광업",
    "10": "식료품 제조", "11": "음료 제조", "12": "담배 제조", "13": "섬유제품 제조",
    "14": "의복·의류 제조", "15": "가죽·신발 제조", "16": "목재·나무제품 제조",
    "17": "펄프·종이 제조", "18": "인쇄·기록매체 복제", "19": "코크스·석유정제품 제조",
    "20": "화학물질·화학제품 제조", "21": "의료용 물질·의약품 제조",
    "22": "고무·플라스틱 제조", "23": "비금속 광물제품 제조", "24": "1차 금속 제조",
    "25": "금속가공제품 제조", "26": "전자부품·컴퓨터·통신장비 제조",
    "27": "의료·정밀·광학기기 제조", "28": "전기장비 제조", "29": "기타 기계·장비 제조",
    "30": "자동차·트레일러 제조", "31": "기타 운송장비 제조", "32": "가구 제조",
    "33": "기타 제품 제조", "35": "전기·가스·증기 공급", "36": "수도업",
    "37": "하수·폐수 처리", "38": "폐기물 처리·재활용", "41": "종합 건설업",
    "42": "전문직별 공사업", "45": "자동차 판매업", "46": "도매·상품중개업",
    "47": "소매업", "49": "육상 운송업", "50": "수상 운송업", "51": "항공 운송업",
    "52": "창고·운송관련 서비스", "55": "숙박업", "56": "음식점·주점업",
    "58": "출판업", "59": "영상·오디오 제작·배급", "60": "방송업", "61": "통신업",
    "62": "컴퓨터 프로그래밍·시스템통합·관리업", "63": "정보서비스업",
    "64": "금융업", "65": "보험·연금업", "66": "금융·보험 관련 서비스업",
    "68": "부동산업", "70": "회사본부·경영컨설팅", "71": "건축기술·엔지니어링",
    "72": "연구개발업", "73": "광고·시장조사", "74": "전문서비스업",
    "86": "보건업", "90": "창작·예술·여가 서비스",
}


def _industry_line(stock_code: str) -> list[str]:
    """DART 업종코드(KSIC 5자리) → '주요사업: <한글 업종>' (무료·캐시). 실패/
    미매핑 시 []. 원문 텍스트가 아닌 업종 분류(코드 한계 — 사용자 2026-06-10
    A 무료 범위)."""
    try:
        from bot.dart_client import get_dart
        d = get_dart()
        ci = d.get_company_info(stock_code) if d else None
        if ci and ci.get("status") == "000":
            code = str(ci.get("induty_code") or "").strip()[:2]
            nm = _KSIC_DIV.get(code)
            if nm:
                return [f"주요사업: {nm}"]
    except Exception:
        pass
    return []


def _market_cap_price_lines(stock_code: str) -> list[str]:
    """FSC 최신 시세 → ['시가총액: X', '현재가: Y원'] (무료·12h 캐시). 실패 시 []."""
    try:
        from bot.fsc_client import latest_price, fsc_key_ready
        from bot.dart_detail import _won as _fw
        if not fsc_key_ready():
            return []
        p = latest_price(f"{stock_code}.KS")  # FSC 는 suffix 무시(6자리 코드)
        if not p:
            return []
        out: list[str] = []
        mc = p.get("mrktTotAmt")
        cl = p.get("clpr")
        if mc:
            w = _fw(mc)
            if w:
                out.append(f"시가총액: {w}")
        if cl:
            try:
                out.append(f"현재가: {int(float(cl)):,}원")
            except (TypeError, ValueError):
                pass
        return out
    except Exception:
        return []


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
