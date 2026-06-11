"""DART 전체 시장 공시 피드 — 1분 간격(준실시간) 수집 + 아카이브 + 대시보드.

DART list.json 을 corp_code 없이(전체 시장) 호출해 당일 주요 공시를 수집.
chart_events.classify 로 카테고리 분류, dart_detail 로 계약/실적/자금조달
구조화 숫자 추출. 비용 ₩0 (DART API 무료, LLM 0).

아카이브: ~/.tradingagents/dart_feed_archive/YYYY-MM-DD.json
1분마다 갱신(접수→카드 평균 ~1.5분) — 새 공시만 append (rcept_no dedup).
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
    "조회공시": "#8d6e63",
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
    # 전환청구권/주식분할·병합 — chart_events KW 에 없어 '기타'로 떨어져
    # fetch(skip_routine)에서 잘리던 것(E2E 하네스 2026-06-11 적발). #237
    # 파서가 닿도록 명시 분류.
    if "전환청구권" in t and "행사" in t:
        return "자금조달"
    if any(k in t for k in ("주식분할", "주식병합", "액면분할", "액면병합")):
        return "회사구조"
    if any(k in t for k in _IR_KW):
        return "IR"
    if any(k in t for k in _EQUITY_KW):
        return "지분공시"
    # ── DART 커버리지 감사 (사용자 2026-06-11 '놓치는 공시') — 거래소
    # 의무공시인데 키워드 부재로 '기타' 드롭되던 유형 보강. 조회공시를
    # 가장 먼저 검사 — '조회공시요구(유상증자설)에대한답변' 류가 내부
    # 키워드(유상증자 등)로 오분류되지 않게.
    if any(k in t for k in ("조회공시", "풍문", "해명")):
        return "조회공시"
    # 담보/보증/대여/차입/리픽싱 — 단 '최대주주변경 수반 주식담보계약' 류는
    # 지배구조 사건이므로 chart_events fallback(_CONTROL_KW)으로 넘김.
    if "최대주주" not in t and any(k in t for k in (
            "담보제공", "채무보증", "금전대여", "단기차입금",
            "전환가액", "교환청구권")):
        return "자금조달"
    if any(k in t for k in ("특허권취득", "기술이전", "기술수출", "기술도입")):
        return "계약"
    if any(k in t for k in ("생산재개", "화재발생")):  # 생산중단은 chart KW
        return "리스크"
    from bot.chart_events import classify
    eng = classify(t)
    if eng in _CATEGORY_MAP:
        return _CATEGORY_MAP[eng]
    if "장래사업" in t or "공정공시" in t:
        # 장래사업ㆍ경영계획/수시공시의무관련사항(공정공시) — 가이던스성
        # wrapper. chart 분류 '뒤'에 두어 '공급계약체결(공정공시)' 류가
        # 계약 등 더 특정한 종류를 유지하게 (백필 정밀화 2026-06-11).
        return "실적"
    return "지분공시" if "보고서" in t else "기타"


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


_DOC_FAIL = Path.home() / ".tradingagents" / "dart_doc_fail.json"
# 일일 DART 콜 버짓 백스톱(키당 2만/일 한도 — 사용자 2026-06-11 '블락 전에
# 미리'): listing 페이지·enrich 시도를 카운트, 초과 시 그날 enrich 중단 +
# listing 최소화. 차단당하기 전에 우리가 먼저 감속.
_BUDGET_FILE = Path.home() / ".tradingagents" / "dart_call_budget.json"
_BUDGET_HARD = 15000   # 한도 20k 대비 25% 안전 마진
_FULLSCAN_TS = Path.home() / ".tradingagents" / "dart_feed_fullscan.ts"


def _budget_today() -> int:
    try:
        d = json.loads(_BUDGET_FILE.read_text(encoding="utf-8"))
        if d.get("date") == datetime.now(_KST).date().isoformat():
            return int(d.get("n", 0))
    except Exception:
        pass
    return 0


def _budget_add(n: int = 1) -> int:
    """오늘 콜 카운트 += n, 누계 반환. 날짜 바뀌면 리셋."""
    today = datetime.now(_KST).date().isoformat()
    total = _budget_today() + n
    try:
        _BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BUDGET_FILE.write_text(json.dumps({"date": today, "n": total}),
                                encoding="utf-8")
    except Exception:
        pass
    return total
# 사이클(1분)당 신규 enrich 시도 상한 — 시간당 480(=8×60) 으로 5분×40
# 시절과 동일 처리량 유지, DART 분당 한도 보호. 최신순 소진, 나머지는
# 다음 사이클이 이어받아 점진 백필.
_ENRICH_MAX_PER_CYCLE = 8


def _doc_fail_load() -> dict:
    try:
        return json.loads(_DOC_FAIL.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _doc_fail_recent(rcept_no: str) -> bool:
    """값 = 만료 시각(expiry). 과거 포맷(실패 시각 저장)은 과거값이라 즉시
    만료로 해석돼 자연 마이그레이션 — 12h 오염 고착도 함께 해소."""
    exp = _doc_fail_load().get(rcept_no)
    try:
        return bool(exp) and time.time() < float(exp)
    except (TypeError, ValueError):
        return False


def _doc_fail_mark(rcept_no: str, hours: float = 0.5) -> None:
    """실패 negative-cache — 만료 시각 저장. 기본 30분(다운로드/네트워크/
    한도 초과 = transient, 빠른 재시도). 파싱 필드 부족 같은 형식 문제는
    호출부가 12h 로 길게. 성공 건은 detail 저장 → 재호출 0."""
    try:
        d = _doc_fail_load()
        d[rcept_no] = time.time() + hours * 3600
        if len(d) > 1500:
            d = dict(sorted(d.items(), key=lambda kv: kv[1])[-1000:])
        _DOC_FAIL.parent.mkdir(parents=True, exist_ok=True)
        _DOC_FAIL.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _fetch_doc_text(rcept_no: str, api_key: str) -> str | None:
    """공시 원문(document.xml zip) → 태그 제거 평문. 실패 시 None +
    negative-cache (₩0·LLM 0·stdlib)."""
    if not rcept_no or _doc_fail_recent(rcept_no):
        return None
    import io
    import zipfile
    try:
        r = requests.get(f"{_DART_BASE}/document.xml",
                         params={"crtfc_key": api_key, "rcept_no": rcept_no},
                         timeout=20)
        blob = r.content or b""
        if len(blob) < 200 or blob[:1] in (b"{", b"<") and b"status" in blob[:200]:
            _doc_fail_mark(rcept_no)
            return None
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = [n for n in zf.namelist() if n.lower().endswith((".xml", ".html"))]
        if not names:
            _doc_fail_mark(rcept_no)
            return None
        # 본문 = 가장 큰 엔트리 (첨부/표지가 작은 파일로 따로 옴)
        name = max(names, key=lambda n: zf.getinfo(n).file_size)
        raw = zf.read(name)[:3_000_000]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp949", errors="ignore")
        txt = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", txt)
    except Exception:
        _doc_fail_mark(rcept_no)
        return None


# 전 유형 generic 원문 필드 — (표시라벨, 라벨 regex, kind). 구조화 API 가
# 없는/빈 공시(IR·시설투자·소송·배당·회사구조 등)의 표준 신고 양식에서 핵심
# 숫자만. kind: won=원금액 약식 / pct / text / date.
_GENERIC_DOC_FIELDS = [
    ("투자금액", r"투자금액[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("자기자본대비", r"자기자본\s*대비[^0-9]{0,60}?([\d.]+)", "pct"),
    ("투자목적", r"투자\s*목적[^가-힣A-Za-z0-9]{0,40}?([가-힣A-Za-z0-9()&.,·\- ]{4,60}?)\s*(?:\d\.|투자기간|자기자본|취득|[A-Za-z0-9]{15,}|$)", "text"),
    ("취득금액", r"취득금액[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("처분금액", r"처분금액[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("청구금액", r"청구금액[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("배당금총액", r"배당금\s*총액[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("1주당 배당금", r"1주당\s*배당금[^0-9]{0,60}?([\d,]{2,})", "won"),
    ("시가배당률", r"시가\s*배당[률율][^0-9]{0,60}?([\d.]+)", "pct"),
    ("개최일시", r"개최\s*일시[^0-9]{0,40}?(\d{4}[-./년]\s?\d{1,2}[-./월]\s?\d{1,2}[일]?[^가-힣<]{0,12})", "text"),
    ("주주총회일", r"주주총회\s*(?:예정)?일[^0-9]{0,40}?(\d{4}[-./]\d{1,2}[-./]\d{1,2})", "date"),
    ("효력발생일", r"효력\s*발생\s*(?:예정)?일[^0-9]{0,40}?(\d{4}[-./]\d{1,2}[-./]\d{1,2})", "date"),
]


def _extract_doc_fields(rcept_no: str, api_key: str, fields: list,
                        min_fields: int = 1) -> dict | None:
    """원문 zip 에서 지정 라벨 세트만 추출 — 유형 전용 파서 공통 헬퍼
    (전환청구권·주식분할/병합 — 사용자 2026-06-11 추가, ₩0·LLM 0).
    미달 시 12h negative-cache(형식 문제는 곧 안 풀림)."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    from bot.dart_detail import _won
    parts: list[str] = []
    for lbl, pat, kind in fields:
        m = re.search(pat, txt)
        if not m:
            continue
        v = m.group(1).strip()
        if kind == "won":
            w = _won(v)
            if w:
                parts.append(f"{lbl}: {w}")
        elif kind == "num":
            try:
                parts.append(f"{lbl}: {int(v.replace(',', '')):,}주")
            except (TypeError, ValueError):
                parts.append(f"{lbl}: {v}")
        else:
            parts.append(f"{lbl}: {v[:60]}")
    if len(parts) < min_fields:
        _doc_fail_mark(rcept_no, hours=12.0)
        return None
    return {"lines": parts}


# 전환청구권 행사 — 구조화 API 없음 → 원문 표준 양식 라벨 (해성옵틱스 카드)
_CONVERT_FIELDS = [
    ("행사주식수", r"행사주식수[^0-9]{0,80}?([\d,]{2,})", "num"),
    ("전환가액", r"전환가액[^0-9]{0,80}?([\d,]{2,})", "won"),
    ("신주 상장예정일",
     r"상장\s*예정일[^0-9]{0,60}?(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})",
     "text"),
    ("잔여 권면총액",
     r"(?:미행사|잔여)[\s\S]{0,40}?권면(?:총액)?[^0-9]{0,60}?([\d,]+)", "won"),
]

# 주식 분할/병합 — 비율·액면 전후·일정 (체리부로 카드). 합병(cmpMgDecsn)과
# 별개 유형이라 원문 파싱.
_SPLIT_MERGE_FIELDS = [
    ("비율", r"(\d[\d,]*\s*주[를을]?[^0-9\n]{0,14}?\d[\d,]*\s*주로)", "text"),
    ("1주 금액(전)",
     r"(?:병합|분할)\s*전[^0-9]{0,40}?1주[^0-9]{0,30}?([\d,]{2,})", "won"),
    ("1주 금액(후)",
     r"(?:병합|분할)\s*후[^0-9]{0,40}?1주[^0-9]{0,30}?([\d,]{2,})", "won"),
    ("주총예정일",
     r"주주총회[^0-9]{0,50}?(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})", "text"),
    ("매매거래정지",
     r"매매\s*거래\s*정지[^0-9]{0,60}?(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}"
     r"[^가-힣\n]{0,24}?\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})", "text"),
    ("신주상장예정일",
     r"신주[^가-힣\n]{0,8}(?:의\s*)?상장\s*예정일[^0-9]{0,50}?"
     r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})", "text"),
]

# ── #13 신규시설투자 — 투자금액·자기자본대비·목적·기간·지역 ──
_CAPEX_FIELDS = [
    ("투자금액", r"투자금액[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("자기자본대비", r"자기자본\s*대비[^0-9]{0,60}?([\d.]+)", "pct"),
    ("투자목적",
     r"투자\s*목적[^가-힣A-Za-z0-9]{0,40}?"
     r"([가-힣A-Za-z0-9()&.,·\- ]{4,60}?)\s*"
     r"(?:\d\.|투자기간|자기자본|투자지역|[A-Za-z0-9]{15,}|$)", "text"),
    ("투자기간",
     r"투자기간[^0-9]{0,40}?"
     r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}[^가-힣\n]{0,30}?"
     r"\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})", "text"),
    ("투자지역",
     r"투자\s*지역[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,40}?)\s*"
     r"(?:\d\.|투자내역|투자금|이사회|$)", "text"),
]

# ── #14 유형자산 양수/양도/취득/처분 ──
_TANGIBLE_ASSET_FIELDS = [
    ("자산명",
     r"(?:양수|양도|취득|처분)\s*(?:대상|물건|자산)\s*[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,50}?)\s*"
     r"(?:\d\.|취득|처분|양수|양도|자기자본|계약|금액|$)", "text"),
    ("금액",
     r"(?:양수|양도|취득|처분)\s*(?:가액|금액|대금)[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("자기자본대비", r"자기자본\s*대비[^0-9]{0,60}?([\d.]+)", "pct"),
    ("거래상대",
     r"(?:양수인|양도인|거래상대방|매수인|매도인)[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,30}?)\s*"
     r"(?:\d\.|관계|금액|$)", "text"),
    ("예정일",
     r"(?:양수|양도|취득|처분|계약)\s*(?:예정)?일[^0-9]{0,40}?"
     r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})", "text"),
]

# ── #15 회사분할 ──
_SPLIT_COMPANY_FIELDS = [
    ("분할방법",
     r"분할\s*방법[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{4,60}?)\s*(?:\d\.|분할비율|분할기일|$)", "text"),
    ("분할비율",
     r"분할비율[^0-9A-Za-z가-힣]{0,20}?"
     r"([0-9.:가-힣 ]{2,30}?)\s*(?:\d\.|분할기일|신설|상장|$)", "text"),
    ("분할기일",
     r"분할기일[^0-9]{0,40}?"
     r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})", "text"),
    ("신설회사",
     r"(?:신설|분할신설)\s*회사[명 ][^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,30}?)\s*(?:\d\.|분할|상장|$)", "text"),
    ("상장예정일",
     r"(?:신주|재)?\s*상장\s*예정일[^0-9]{0,50}?"
     r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})", "text"),
]

# ── #16 최대주주 양수도 계약 ──
_MAJOR_TRANSFER_FIELDS = [
    ("양수인",
     r"양수인[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,30}?)\s*(?:\d\.|양도인|주식수|$)", "text"),
    ("양도인",
     r"양도인[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,30}?)\s*(?:\d\.|양수인|주식수|$)", "text"),
    ("양수도 주식수",
     r"양수도\s*(?:하는\s*)?주식\s*(?:의?\s*)?수[^0-9]{0,40}?"
     r"([\d,]{3,})", "num"),
    ("양수도 대금",
     r"양수도\s*(?:하는\s*)?주식\s*(?:의?\s*)?(?:가격|대금|금액)"
     r"[^0-9]{0,40}?([\d,]{4,})", "won"),
    ("1주당 가격",
     r"1주당\s*(?:양수도\s*)?(?:가격|가액)[^0-9]{0,40}?([\d,]{3,})", "won"),
    ("변경예정 최대주주",
     r"변경\s*(?:예정|후)\s*(?:최대주주|대표[이자])[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,20}?)\s*(?:\d\.|$)", "text"),
]

# ── #17 담보제공 ──
_COLLATERAL_FIELDS = [
    ("담보제공 주식수",
     r"담보(?:로\s*제공|제공)\s*(?:하는\s*)?주식\s*(?:의?\s*)?수[^0-9]{0,40}?"
     r"([\d,]{3,})", "num"),
    ("담보금액",
     r"담보(?:가액|금액|평가액)[^0-9]{0,60}?([\d,]{4,})", "won"),
    ("담보권자",
     r"담보(?:권자|설정자|받는\s*자)[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,30}?)\s*(?:\d\.|담보|$)", "text"),
    ("담보사유",
     r"담보\s*(?:제공\s*)?사유[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()·, ]{2,40}?)\s*(?:\d\.|담보|$)", "text"),
]


def _extract_generic_document(rcept_no: str, api_key: str) -> dict | None:
    """구조화 API 미커버 공시의 generic 원문 추출 — 라벨 매칭 최대 6줄.
    필드 0개면 negative-cache(12h 재시도 억제)."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    from bot.dart_detail import _won
    parts: list[str] = []
    for lbl, pat, kind in _GENERIC_DOC_FIELDS:
        m = re.search(pat, txt)
        if not m:
            continue
        v = m.group(1).strip()
        if kind == "won":
            w = _won(v)
            if w:
                parts.append(f"{lbl}: {w}")
        elif kind == "pct":
            parts.append(f"{lbl}: {v}%")
        else:
            parts.append(f"{lbl}: {v[:50]}")
        if len(parts) >= 6:
            break
    if not parts:
        _doc_fail_mark(rcept_no, hours=12.0)  # 형식 문제 — 곧 안 풀림
        return None
    return {"lines": parts}



# ── 배치 구현 2026-06-11 (사용자 승인 양식) — 공용 헬퍼 ──────────────────

def _doc_unit_mult(txt: str) -> float:
    """공시 표 단위 감지: 원=1 / 천원=1e3 / 백만원=1e6 (회사별 혼재 —
    미감지 시 1,000배 오류 클래스라 필수)."""
    m = re.search(r"단위\s*[:：]\s*(백만\s*원|천\s*원|원)", txt)
    if not m:
        return 1.0
    u = m.group(1).replace(" ", "")
    return 1e6 if u == "백만원" else (1e3 if u == "천원" else 1.0)


def _amt_won(raw: str, mult: float = 1.0) -> str | None:
    """'53,206,197' (+단위배수) → '532억원'. 음수 지원."""
    from bot.dart_detail import _won
    try:
        n = float(str(raw).replace(",", "").strip()) * mult
    except (TypeError, ValueError):
        return None
    return _won(str(n))


def _pct1(raw: str) -> str | None:
    try:
        return f"{float(str(raw).replace(',', '').replace('%', '')):+.1f}%"
    except (TypeError, ValueError):
        return None


def _correction_header(txt: str) -> str | None:
    """[기재정정] 공통 — '정정(날짜): 사유' 헤더 라인. 없으면 None."""
    if "정정신고" not in txt and "정정사유" not in txt:
        return None
    d = re.search(r"정정일자\s*(\d{4}-\d{2}-\d{2})", txt)
    r = re.search(r"정정사유\s*[:：]?\s*([가-힣A-Za-z0-9 ,.()·]{2,40}?)"
                  r"\s*(?:\d\.|정정사항|정정관련|$)", txt)
    if not r:
        return None
    dd = f"({d.group(1)[2:]})" if d else ""
    return f"정정{dd}: {r.group(1).strip()}"


def _tok_amt(tok, mult):
    return _amt_won(tok, mult) if tok and tok != "-" else None


# ── 실적 — 월별 잠정(Form A) / 연간 30%·15% 변동(Form B) + 정정 ──────────

def _parse_earnings_doc(rcept_no: str, api_key: str, title: str) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    mult = _doc_unit_mult(txt)
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    if "당해실적" in txt and "누계실적" in txt:
        # Form A — 월별 잠정실적. 행 = '라벨 당해실적 (YY.MM) 값들 누계실적
        # (범위) 값들'. 컬럼 수가 양식마다 달라(흑자전환여부 유무 등) 위치
        # 고정 대신: 괄호 라벨((26.05)·(%)) 제거 후 금액(콤마/4자리+)과
        # 비율(소수/3자리-) 분류 — 금액[0]=당월·누계, 비율[0]=전월比,
        # 비율[-1]=전년比.
        mlabel = re.search(r"\((\d{2}\.\d{2})\)", txt)
        parts.insert(len(parts) and 1 or 0,
                     f"구분: 월별 잠정실적{' (' + mlabel.group(1) + ')' if mlabel else ''}")

        def _amts_pcts(seg: str) -> tuple[list, list]:
            seg = re.sub(r"\([^)]*\)", " ", seg)
            amts, pcts = [], []
            for tok in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", seg):
                if "." in tok:
                    pcts.append(tok)
                elif "," in tok or len(tok.lstrip("+-")) > 3:
                    amts.append(tok)
                else:
                    pcts.append(tok)
            return amts, pcts

        for label in ("매출액", "영업이익", "당기순이익", "총매출액"):
            pre = r"(?<!총)" if label == "매출액" else ""
            mm = re.search(pre + rf"{label}\s*(?:\((?:당기|당월)실적\))?\s*당해실적"
                           rf"\s*(.{{0,160}}?)누계실적\s*(.{{0,160}}?)(?=[가-힣]{{2,}}|$)",
                           txt, re.DOTALL)
            if not mm:
                continue
            ca, cp = _amts_pcts(mm.group(1))
            ka, kp = _amts_pcts(mm.group(2))
            if ca:
                a = _tok_amt(ca[0], mult)
                if a:
                    extras = []
                    if cp:
                        extras.append(f"전월 {_pct1(cp[0])}")
                    if len(cp) > 1:
                        extras.append(f"전년동월 {_pct1(cp[-1])}")
                    parts.append(f"{label}(당월): {a}"
                                 + (f" ({' · '.join(extras)})" if extras else ""))
            if ka:
                a = _tok_amt(ka[0], mult)
                if a:
                    parts.append(f"{label}(누계): {a}"
                                 + (f" (전년동기 {_pct1(kp[0])})" if kp else ""))
        return {"lines": parts} if len(parts) >= 2 else None

    if "직전사업연도" in txt:
        # Form B — 연간 결산 변동(30%/15%)
        ftype = re.search(r"재무제표의?\s*종류\s*(개별|연결)", txt)
        s = re.search(r"시작일\s*(\d{4}-\d{2}-\d{2})", txt)
        e = re.search(r"종료일\s*(\d{4}-\d{2}-\d{2})", txt)
        head = "구분: 연간 결산"
        if corr:
            head = "구분: [기재정정] 연간 결산"
        seg = []
        if ftype:
            seg.append(ftype.group(1))
        if s and e:
            seg.append(f"FY {s.group(1)[2:]}~{e.group(1)[2:]}")
        if seg:
            head += " (" + " · ".join(seg) + ")"
        parts.append(head)
        for label in ("매출액", "영업이익", "당기순이익"):
            m = re.search(rf"-?\s*{label}\s*(?:\([^)]{{1,12}}\))?\s+([-\d,]+)\s+([-\d,]+)\s+([-\d,]+)\s+([-\d.,]+)\s*(흑자전환|적자전환|적자지속|-)?",
                          txt)
            if not m:
                continue
            cur, prev, pct = m.group(1), m.group(2), m.group(4)
            flag = m.group(5) if m.group(5) and m.group(5) != "-" else None
            a, b = _amt_won(cur, mult), _amt_won(prev, mult)
            if not a:
                continue
            try:
                neg = float(cur.replace(",", "")) < 0
                neg_prev = float(prev.replace(",", "")) < 0
            except ValueError:
                neg = neg_prev = False
            if not flag and neg:
                flag = "적자지속" if neg_prev else "적자전환"
            tail = _pct1(pct)
            extras = [x for x in (f"전기 {b}" if b else None,
                                  flag or (tail and tail) or None) if x]
            if not flag and tail:
                extras = [f"전기 {b}" if b else None, tail]
                extras = [x for x in extras if x]
            parts.append(f"{label}: {a}" + (f" ({' · '.join(extras)})" if extras else ""))
        fin = []
        for lbl, kr in (("자산총계", "자산"), ("부채총계", "부채"), ("자본총계", "자본")):
            m = re.search(rf"{lbl}\s+([-\d,]+)", txt)
            if m:
                a = _amt_won(m.group(1), mult)
                if a:
                    fin.append(f"{kr} {a}")
        if fin:
            parts.append("재무현황: " + " · ".join(fin))
        why = re.search(r"변동\s*주요\s*원인[^가-힣A-Za-z0-9]{0,20}?"
                        r"([가-힣A-Za-z0-9() ,.·%~\- ]{4,60}?)\s*(?:\d\.|이사회|기타|$)",
                        txt)
        if why:
            parts.append(f"변동 주요원인: {why.group(1).strip()}")
        return {"lines": parts} if len(parts) >= 2 else None
    return None


# ── 주주환원 — 신탁 체결/해지 · 소각 · 취득결과 · 배당 기준일 ────────────

def _parse_trust(rcept_no: str, api_key: str, cancel: bool) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    mult = _doc_unit_mult(txt)
    g = lambda pat: (lambda m: m.group(1).strip() if m else None)(re.search(pat, txt))
    parts: list[str] = []
    org = g(r"(?:계약체결기관|해지기관)\s*([가-힣A-Za-z0-9() .,&]{2,30}?)\s*(?:\(|\d\.|체결|해지|$)")
    parts.append(f"구분: 자기주식취득 신탁계약 {'해지' if cancel else '체결'}"
                 + (f" ({org})" if org else ""))
    if cancel:
        why = g(r"해지목적\s*([가-힣A-Za-z0-9() ,.·]{2,60}?)\s*(?:\d\.|해지기관|$)")
        if why:
            parts.append(f"해지사유: {why}")
        amt = g(r"해지\s*전\s*([\d,]{6,})") or g(r"계약금액[^0-9]{0,40}([\d,]{6,})")
        s = g(r"시작일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
        e = g(r"종료일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
        if amt:
            a = _amt_won(amt, mult)
            if a:
                parts.append(f"해지 전 계약: {a}"
                             + (f" ({_clean_kdate(s)} ~ {_clean_kdate(e)})" if s and e else ""))
        d = g(r"해지예정일자?\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
        ret = g(r"반환방법\s*([가-힣() 및·,]{2,30})")
        if d:
            parts.append(f"해지예정일: {_clean_kdate(d)}"
                         + (f" · 반환: {ret}" if ret else ""))
        m = re.search(r"보통주식\s*([\d,]{3,})\s*비율\s*\(%\)\s*([\d.]+)", txt)
        if m:
            parts.append(f"보유현황: {int(m.group(1).replace(',', '')):,}주 ({m.group(2)}%)")
    else:
        amt = g(r"계약금액[^0-9]{0,40}([\d,]{6,})")
        s = g(r"시작일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
        e = g(r"종료일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
        if amt:
            a = _amt_won(amt, mult)
            if a:
                line = f"계약금액: {a}"
                if s and e:
                    line += f" · 기간 {_fmt_period(_clean_kdate(s) + ' ~ ' + _clean_kdate(e))}"
                parts.append(line)
        m = re.search(r"취득예정주식[^0-9]{0,40}([\d,]{2,})", txt)
        pr = re.search(r"취득하고자\s*하는\s*주식의\s*가격[^0-9]{0,40}([\d,]{2,})", txt)
        if m:
            parts.append(f"취득예정: {int(m.group(1).replace(',', '')):,}주"
                         + (f" (기준가 {int(pr.group(1).replace(',', '')):,}원 — 주가 따라 변동)" if pr else ""))
        why = g(r"계약목적\s*([가-힣A-Za-z0-9() ,.·]{2,40}?)\s*(?:\d\.|계약체결|$)")
        if why:
            parts.append(f"목적: {why}")
    return {"lines": parts} if len(parts) >= 2 else None


def _clean_kdate(s: str | None) -> str:
    """'2026년 03월 17일' → '2026-03-17'."""
    if not s:
        return ""
    m = re.search(r"(\d{4})[년\s.-]+(\d{1,2})[월\s.-]+(\d{1,2})", s)
    if not m:
        return s.strip()
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _parse_burn(rcept_no: str, api_key: str) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    mult = _doc_unit_mult(txt)
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)
    profit_burn = ("자본금은 감소하지 않" in txt or "자본금의 감소는 없" in txt
                   or "343조" in txt)
    parts.append("구분: 주식 소각"
                 + (" (이익소각 — 자본금 감소 없음)" if profit_burn else ""))
    n = re.search(r"소각할\s*주식.{0,30}?보통주식?\s*\(주\)\s*([\d,]{3,})", txt, re.DOTALL)
    tot = re.search(r"발행주식\s*총수.{0,30}?보통주식?\s*\(주\)\s*([\d,]{4,})", txt, re.DOTALL)
    amt = re.search(r"소각예정금액[^0-9]{0,30}([\d,]{4,})", txt)
    if n:
        nn = int(n.group(1).replace(",", ""))
        line = f"소각: {nn:,}주"
        if tot:
            tt = int(tot.group(1).replace(",", ""))
            if tt > 0:
                line += f" = 발행주식의 {nn / tt * 100:.2f}%"
        if amt:
            a = _amt_won(amt.group(1), mult)
            if a:
                line += f" · {a}"
        parts.append(line)
    how = re.search(r"취득방법\s*([가-힣 ]{2,20}?)\s*(?:\d\.|소각|$)", txt)
    d = re.search(r"소각\s*예정일\s*(\d{4}-\d{2}-\d{2})", txt)
    seg = []
    if how:
        seg.append(f"방법: {how.group(1).strip()}")
    if d:
        seg.append(f"소각예정일 {d.group(1)}")
    if seg:
        parts.append(" · ".join(seg))
    return {"lines": parts} if len(parts) >= 2 else None


def _parse_buyback_result(rcept_no: str, api_key: str) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    parts: list[str] = ["구분: 자기주식 취득결과"]
    # 일자별 표의 계(합계) 행 — 컬럼 변형(단가 '-' 등)에 강건하게: 행의
    # 숫자들 중 최대 = 총액, 첫 비-최대 = 수량, 평균단가 = 총액/수량 산출.
    seg = re.search(r"(?<![가-힣])(?:합\s*)?계\s*([-\d,.\s]{6,80})", txt)
    if seg:
        try:
            nums = [int(t.replace(",", ""))
                    for t in re.findall(r"\d[\d,]{2,}", seg.group(1))]
            if nums:
                amt = max(nums)
                qty = next((n for n in nums if n != amt), None)
                if amt >= 10 ** 6 and qty and 0 < qty < amt:
                    px = round(amt / qty)
                    a = _amt_won(str(amt))
                    line = f"취득: {qty:,}주 @ {px:,}원"
                    if a:
                        line += f" = {a}"
                    parts.append(line)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    broker = re.search(r"위탁\s*투자\s*중개업자[^가-힣A-Za-z]{0,10}"
                       r"([가-힣A-Za-z0-9·, ]{2,24})", txt)
    if broker:
        nm = re.sub(r"\s*(?:주식회사|㈜)\s*", " ", broker.group(1)).strip()
        mm = re.match(r".{0,18}?(?:투자증권|금융투자|증권)", nm)
        if mm:
            nm = mm.group(0)
        if len(nm) >= 2:
            parts.append(f"위탁: {nm}")
    return {"lines": parts} if len(parts) >= 2 else None


def _parse_div_record(rcept_no: str, api_key: str, title: str) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    kind = None
    m = re.search(r"배당구분\s*(분기배당|결산배당|중간배당)", txt)
    if m:
        kind = m.group(1)
    elif "분기" in title or "중간" in title:
        kind = "분기배당"
    if not kind and "배당" not in title and "배당" not in txt:
        return None  # 주총 소집용 기준일 등 — 배당 카드 오라벨 방지
    parts = [f"구분: {kind or '배당'} 기준일 결정"]
    d = re.search(r"기준일\s*(\d{4}-\d{2}-\d{2})", txt)
    if d:
        line = f"배당기준일: {d.group(1)}"
        if "폐쇄" in txt and ("없이" in txt or "기준일만" in txt):
            line += " (권리주주 확정 — 명부폐쇄 없음)"
        parts.append(line)
    b = re.search(r"이사회결의일\s*\(?결정일?\)?\s*\(?\s*(\d{4}-\d{2}-\d{2})", txt)
    if b:
        parts.append(f"이사회결의: {b.group(1)}")
    if re.search(r"구체적인\s*사항은\s*추후|추후\s*이사회에서\s*결정", txt):
        parts.append("※ 배당금액은 추후 이사회 결정")
    return {"lines": parts} if len(parts) >= 2 else None


# ── 자금조달 — 유상/유무상 증자 doc 풀카드 + CB doc 풀카드 ────────────────

def _parse_rights_issue_doc(rcept_no: str, api_key: str, title: str) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    mult = _doc_unit_mult(txt)
    g = lambda pat: (lambda m: m.group(1).strip() if m else None)(re.search(pat, txt))
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)
    method = g(r"증자방식\s*([가-힣A-Za-z0-9 ()]{2,30}?)\s*(?:\d|\(주\)|주\)|기타주식|$)")
    sub = "종속회사" in title
    sub_nm = g(r"종속회사[명인]?\s*[:：]?\s*\(?주?\)?\s*"
               r"([가-힣A-Za-z0-9·]{2,16})") if sub else None
    if "유무상" in title:
        base = "유무상증자"
    elif "무상증자" in title:
        base = "무상증자"
    else:
        base = "유상증자"
    head = ("구분: "
            + (f"종속회사({sub_nm}) " if sub_nm else ("종속회사 " if sub else ""))
            + base)
    if method:
        head += f" ({method})"
    parts.append(head)
    # 신주 종류·수 — 보통주 우선, '-' 면 기타/종류주식(RCPS 등) 변형
    kind, n_new = "보통주", None
    m = re.search(r"보통주식?\s*\(주\)\s*([\d,]{1,15})", txt)
    if m and m.group(1) != "-":
        n_new = int(m.group(1).replace(",", ""))
    else:
        m = re.search(r"(?:종류|기타)주식?\s*\(주\)\s*([\d,]{1,15})", txt)
        if m:
            n_new = int(m.group(1).replace(",", ""))
            kind = "RCPS" if "상환전환우선주" in txt else "종류주식"
    px = g(r"발행가액?[^0-9]{0,80}?([\d,]{2,})")
    pre = re.search(r"증자전\s*발행주식\s*총?수.{0,40}?([\d,]{3,})", txt, re.DOTALL)
    if n_new:
        line = f"신주: {kind} {n_new:,}주"
        if pre:
            base = int(pre.group(1).replace(",", ""))
            if base > 0:
                line += f" (+{n_new / base * 100:.1f}%)"
        if px:
            pxn = int(px.replace(",", ""))
            line += f" @ {pxn:,}원"
            tot = _amt_won(str(n_new * pxn))
            if tot:
                line += f" = {tot}"
        parts.append(line)
    # 자금 용도
    uses = []
    for lbl, kr in (("시설자금", "시설"), ("운영자금", "운영"), ("채무상환자금", "차환"),
                    (r"타법인\s*증권\s*취득자금", "타법인취득"), ("기타자금", "기타")):
        m = re.search(rf"{lbl}\s*\(원\)\s*([\d,]{{4,}})", txt)
        if m:
            a = _amt_won(m.group(1), mult)
            if a:
                uses.append(f"{kr} {a}")
    if uses:
        parts.append("용도: " + " · ".join(uses))
    # 일정 체인
    sched = []
    bd = g(r"신주배정기준일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
    if bd:
        sched.append(f"기준일 {_clean_kdate(bd)[5:]}")
    cs = re.search(r"구주주\s*시작일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})\s*일?\s*종료일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})", txt)
    if cs:
        sched.append(f"구주주청약 {_clean_kdate(cs.group(1))[5:]}~{_clean_kdate(cs.group(2))[5:]}")
    pay = g(r"납입일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
    if pay:
        sched.append(f"납입 {_clean_kdate(pay)[5:]}")
    lst = g(r"상장\s*예정일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
    if lst:
        sched.append(f"신주상장 {_clean_kdate(lst)[5:]}")
    if sched:
        parts.append("일정: " + " → ".join(sched))
    # 주주배정 비율·주관사 (있을 때만 1줄)
    ps = g(r"1주당\s*신주배정\s*주식수\s*\(주\)\s*([\d.]{1,8})")
    lead = g(r"대표주관회사\s*[:：]?\s*([가-힣A-Za-z0-9 ]{2,20}?(?:증권|금융투자))")
    seg2 = [x for x in (f"1주당 {ps}주" if ps else None,
                        f"주관 {lead}" if lead else None) if x]
    if seg2:
        parts.append("배정: " + " · ".join(seg2))
    # 제3자배정 대상자 표 — 'NAME [계열회사] ... 배정주식수' best-effort ≤3
    if method and "제3자" in method:
        mseg = re.search(r"제3자배정\s*대상자\s*(.{0,400}?)(?=\d{1,2}\.\s|$)",
                         txt, re.DOTALL)
        if mseg:
            alloc = re.findall(
                r"([가-힣A-Za-z0-9㈜]{2,16}(?:\(주\))?)\s+"
                r"(?:(계열회사|최대주주|특수관계인)\s+)?"
                r"[가-힣A-Za-z ,·\-]{0,40}?([\d,]{3,9})\s",
                mseg.group(1))
            out = []
            for nm, rel, cnt in alloc[:3]:
                if nm in ("해당사항없음", "해당없음"):
                    continue
                try:
                    c = int(cnt.replace(",", ""))
                except ValueError:
                    continue
                out.append(f"{nm}({c:,}주{', ' + rel if rel else ''})")
            if out:
                parts.append("배정대상: " + " · ".join(out))
    return {"lines": parts} if len(parts) >= 3 else None


def _parse_cb_doc(rcept_no: str, api_key: str) -> dict | None:
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    g = lambda pat: (lambda m: m.group(1).strip() if m else None)(re.search(pat, txt))
    parts: list[str] = []
    rnd = g(r"회\s*차\s*(\d{1,3})")
    priv = "사모" if "사모" in txt[:3000] else None
    amt = g(r"전자등록\)?총액\s*\(원\)?\s*([\d,]{6,})") or g(r"권면[^0-9]{0,40}([\d,]{6,})")
    head = "구분: 전환사채 발행"
    seg = [x for x in (f"{rnd}회차" if rnd else None, priv) if x]
    if seg:
        head += f" ({' · '.join(seg)})"
    a = _amt_won(amt) if amt else None
    if a:
        head += f" — {a}"
    parts.append(head)
    cvp = g(r"전환가[액격][^0-9]{0,40}?([\d,]{2,})")
    cvn = g(r"주식수\s*([\d,]{4,})\s*주식총수\s*대비")
    dil = g(r"주식총수\s*대비\s*비?율?\s*\(?%?\)?\s*([\d.]{1,6})")
    if cvp:
        line = f"전환: 전환가 {int(cvp.replace(',', '')):,}원"
        if cvn:
            line += f" → {int(cvn.replace(',', '')):,}주"
        if dil:
            line += f" = 주식총수의 {dil}% (잠재 희석)"
        parts.append(line)
    r1 = g(r"표면이자율\s*\(%\)\s*([\d.]+)")
    r2 = g(r"만기이자율\s*\(%\)\s*([\d.]+)")
    mt = g(r"사채만기일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
    seg = []
    if r1 or r2:
        seg.append(f"표면 {r1 or '-'}% / 만기 {r2 or '-'}%")
    if mt:
        seg.append(f"만기 {_clean_kdate(mt)}")
    if seg:
        parts.append("이자: " + " · ".join(seg))
    cs = re.search(r"전환청구기간\s*시작일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})\s*일?\s*종료일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})", txt)
    feat = []
    if cs:
        feat.append(f"전환기간 {_clean_kdate(cs.group(1))} ~ {_clean_kdate(cs.group(2))}")
    if re.search(r"시가\s*하락|최저\s*조정가|리픽", txt):
        low = g(r"(70|80|85|90)\s*%?\s*(?:미만으로는|이상|에\s*해당)")
        feat.append("리픽싱 有" + (f"(최저 {low}%)" if low else ""))
    if "조기상환청구권" in txt or "풋옵션" in txt or "Put Option" in txt:
        feat.append("풋옵션 有")
    if feat:
        parts.append(" · ".join(feat))
    uses = []
    for lbl, kr in (("운영자금", "운영"), ("시설자금", "시설"), ("채무상환자금", "차환"),
                    (r"타법인\s*증권?\s*취득자금", "타법인취득(M&A)")):
        m = re.search(rf"{lbl}\s*\(원\)\s*([\d,]{{4,}})", txt)
        if m:
            aa = _amt_won(m.group(1))
            if aa:
                uses.append(f"{kr} {aa}")
    if uses:
        parts.append("용도: " + " · ".join(uses))
    pay = g(r"납입일\s*(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
    if pay:
        parts.append(f"납입일: {_clean_kdate(pay)}")
    return {"lines": parts} if len(parts) >= 3 else None


def _extract_contract_document(rcept_no: str, api_key: str) -> dict | None:
    """단일판매·공급계약 — 주요사항보고서 구조화 API 부재(거래소 수시공시)
    → 공시 원문(document.xml zip)의 표준 신고 양식 표에서 핵심 숫자 추출.
    내용 라벨 3변형(체결계약명/구분+세부내용/계약내용) + 조건부 + 공시유보
    + 정정 헤더(금액 A→B % 또는 '금액 무변') + 매출액대비·대규모법인 인라인
    — 사용자 승인 양식 2026-06-11. ₩0·LLM 0·stdlib. graceful(None)."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    from bot.dart_detail import _won

    def _grab(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    parts: list[str] = []
    _END = r"(?:\d{1,2}\.\s|계약금액|계약내역|계약기간|공시유보|시작일|비고|[A-Za-z0-9]{15,}|$)"
    _V = r"([가-힣A-Za-z0-9()\[\]_'‘’\"“”&.,·ㆍ㈜\- ]{2,80}?)"

    # 내용 — 라벨 3변형: 체결계약명(표준 상세형) / 구분+세부내용(자율공시
    # 간이형) / 판매ㆍ공급계약 내용(구형). 구체 라벨 우선.
    body = _grab(r"체결계약명[^가-힣A-Za-z0-9]{0,20}?" + _V + r"\s*" + _END)
    if not body:
        det = _grab(r"세부내용[^가-힣A-Za-z0-9]{0,20}?" + _V + r"\s*" + _END)
        if det:
            gu = _grab(r"계약\s*구분[^가-힣A-Za-z0-9]{0,20}?"
                       r"([가-힣A-Za-z0-9 ]{2,20}?)[\s\-–—·]*(?:세부|\d{1,2}\.\s|$)")
            body = f"{gu} — {det}" if gu and gu not in det else det
    if not body:
        body = _grab(r"계약\s*내용[^가-힣A-Za-z0-9]{0,40}?" + _V + r"\s*" + _END)
    if body:
        parts.append(f"계약: {body[:70]}")

    # 정정사항 표 = 라벨 뒤 (정정전, 정정후) 인접 숫자쌍 — 본문(라벨당 숫자
    # 1개)과 구분된다. 두번째 숫자가 '6.' 같은 절번호로 오인되지 않게 비율은
    # 소수형 강제 + 뒤 숫자/점 금지.
    amt2 = re.search(r"계약금액[^0-9]{0,30}?([\d,]{6,})\s+([\d,]{6,})", txt)
    rat2 = re.search(r"매출액\s*대비[^0-9]{0,30}?(\d{1,3}(?:\.\d{1,2})?)"
                     r"\s+(\d{1,3}(?:\.\d{1,2})?)(?![.\d])", txt)
    if "정정신고" in txt or "정정사유" in txt:
        segs = []
        if amt2:
            try:
                a = float(amt2.group(1).replace(",", ""))
                b = float(amt2.group(2).replace(",", ""))
                wa, wb = _won(amt2.group(1)), _won(amt2.group(2))
                if wa and wb and a > 0 and a != b:
                    segs.append(f"계약금액 {wa} → {wb} ({(b - a) / a * 100:+.0f}%)")
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if rat2 and rat2.group(1) != rat2.group(2):
            segs.append(f"매출대비 {rat2.group(1)}% → {rat2.group(2)}%")
        d = re.search(r"정정일자\s*(\d{4}-\d{2}-\d{2})", txt)
        dd = f"({d.group(1)[2:]})" if d else ""
        if segs:
            parts.insert(0, f"정정{dd}: " + " · ".join(segs))
        else:
            corr = _correction_header(txt)
            parts.insert(0, (corr or f"정정{dd}: 기재 정정") + " — 금액 무변")

    # 금액/매출대비 — 정정쌍 있으면 정정후 값이 canonical.
    amt = amt2.group(2) if amt2 else _grab(r"계약금액[^0-9]{0,60}?([\d,]{4,})")
    ratio = rat2.group(2) if rat2 else _grab(r"매출액\s*대비[^0-9]{0,60}?([\d.]+)")
    mcond = re.search(r"조건부\s*계약\s*여부\s*[^가-힣]{0,8}([가-힣]{2,8})", txt)
    is_cond = bool(mcond and mcond.group(1).startswith("해당")
                   and "없" not in mcond.group(1))
    mbig = re.search(r"대규모\s*법인\s*여부\s*[^가-힣]{0,8}([가-힣]{2,8})", txt)
    is_big = bool(mbig and mbig.group(1).startswith("해당")
                  and "없" not in mbig.group(1))
    if amt and _won(amt):
        qual = [x for x in ("조건부" if is_cond else None,
                            f"매출액대비 {ratio}%" if ratio else None,
                            "대규모법인" if is_big else None) if x]
        parts.append(f"계약금액: {_won(amt)}"
                     + (f" ({' · '.join(qual)})" if qual else ""))

    party = _grab(r"계약상대방?[^가-힣A-Za-z0-9(]{0,40}?"
                  r"([가-힣A-Za-z0-9()&.,\- ]{2,40}?)\s*" + _END)
    if party and re.search(r"[가-힣A-Za-z]{2}", party):
        parts.append(f"계약상대: {party[:40]}")
    s = _grab(r"시작일[^0-9]{0,40}?(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})")
    e = _grab(r"종료일[^0-9]{0,40}?(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})")
    cd = _grab(r"계약\s*\(?수주\)?\s*일자?[^0-9]{0,40}?(\d{4}[-./]\d{1,2}[-./]\d{1,2})")
    if s and e:
        parts.append(f"기간: {_fmt_period(f'{s} ~ {e}')}")
    elif cd:
        parts.append(f"계약일: {cd}")

    # 공시유보 — 상대방 익명("글로벌 우주항공 발사업체")의 맥락 설명.
    rto = _grab(r"유보\s*기한[^0-9]{0,30}?(\d{4}[년\s.-]+\d{1,2}[월\s.-]+\d{1,2})")
    rwhy = _grab(r"유보\s*사유[^가-힣A-Za-z0-9]{0,20}?"
                 r"([가-힣A-Za-z0-9() ,.·]{2,40}?)\s*(?:\d\.|유보|공시|$)")
    if rto or rwhy:
        parts.append("공시유보: " + " ".join(x for x in (
            f"{_clean_kdate(rto)}까지" if rto else None,
            f"({rwhy})" if rwhy else None) if x))

    if len(parts) < 2:
        _doc_fail_mark(rcept_no, hours=12.0)  # 형식 문제 — 곧 안 풀림
        return None
    return {"lines": parts}


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

    # 전환청구권 행사 / 주식 분할·병합 — 구조화 API 미확인 유형 → 검증된
    # 원문 표준 양식 파싱 (사용자 2026-06-11 추가, ₩0).
    if "전환청구권" in t and "행사" in t:
        return _extract_doc_fields(rcept_no, api_key, _CONVERT_FIELDS,
                                   min_fields=2)
    if ("주식분할" in t or "주식병합" in t or "액면분할" in t
            or "액면병합" in t):
        return _extract_doc_fields(rcept_no, api_key, _SPLIT_MERGE_FIELDS,
                                   min_fields=2)

    # ── 배치 2026-06-11 (사용자 승인 카드 양식) — 원문(document.xml) 1차 파싱.
    # 실패(None) 시 아래 구조화 API spec 폴백 (graceful).
    if "영업(잠정)실적" in t or "매출액또는손익구조" in t:
        doc = _parse_earnings_doc(rcept_no, api_key, t)
        if doc:
            return doc
    elif "공급계약" in t or "단일판매" in t or "단일공급" in t:
        # 계약은 doc 전용 — 주요사항보고서 구조화 API 부재(거래소 수시공시).
        return _extract_contract_document(rcept_no, api_key)
    elif "신탁계약" in t and "해지" in t:
        doc = _parse_trust(rcept_no, api_key, cancel=True)
        if doc:
            return doc
    elif "신탁계약" in t and ("체결" in t or "연장" in t):
        doc = _parse_trust(rcept_no, api_key, cancel=False)
        if doc:
            return doc
    elif "소각" in t:
        doc = _parse_burn(rcept_no, api_key)
        if doc:
            return doc
    elif "취득결과" in t:
        doc = _parse_buyback_result(rcept_no, api_key)
        if doc:
            return doc
    elif "주주명부폐쇄" in t or ("기준일" in t and "배당" in t):
        doc = _parse_div_record(rcept_no, api_key, t)
        if doc:
            return doc
    elif "유상증자" in t or "유무상증자" in t:
        doc = _parse_rights_issue_doc(rcept_no, api_key, t)
        if doc:
            return doc
    elif "전환사채" in t:
        doc = _parse_cb_doc(rcept_no, api_key)
        if doc:
            return doc
    # ── 배치 #13-#17 원문 파서 (2026-06-11) ──
    elif "신규시설투자" in t or "투자결정" in t:
        doc = _extract_doc_fields(rcept_no, api_key, _CAPEX_FIELDS,
                                  min_fields=2)
        if doc:
            return doc
    elif "유형자산" in t and ("양수" in t or "양도" in t
                             or "취득" in t or "처분" in t):
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _TANGIBLE_ASSET_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "회사분할" in t or "분할합병" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _SPLIT_COMPANY_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "최대주주" in t and ("양수" in t or "양도" in t):
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _MAJOR_TRANSFER_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "담보" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _COLLATERAL_FIELDS, min_fields=2)
        if doc:
            return doc

    specs: list[tuple[str, list]] = []
    if "영업(잠정)실적" in t or "매출액또는손익구조" in t:
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

    # specs 빈 유형(시설투자·IR·소송 등)도 아래 원문 폴백으로 진행.
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
    # 구조화 API 가 없거나 빈 응답인 공시 → generic 원문 라벨 추출 폴백
    # (₩0·LLM 0, 사용자 2026-06-11 "공급계약뿐 아니라 다른 종류도 다").
    # 공급계약은 위 doc-first 분기가 전담.
    return _extract_generic_document(rcept_no, api_key)


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

# ── 드롭('기타') 분포 관측 — 커버리지 감사 (사용자 2026-06-11) ──────────────
# skip_routine 이 버리는 제목을 정규화해 집계, 하루 1회 INFO 로그로 상장사
# 드롭 top 패턴을 남긴다 → '놓치는 공시' 가 생기면 journald 에서 바로 보임.
_DROP_TALLY: dict[str, int] = {}
_DROP_LOG_MARKER = _ARCHIVE_DIR.parent / "dart_feed_droplog_date.txt"


def _norm_report_nm(nm: str) -> str:
    """제목 패턴 정규화 — 차수/정정 prefix 등 변형 제거해 유형별 그룹핑."""
    s = re.sub(r"\[[^\]]*\]", "", nm or "")          # [기재정정] 등 prefix
    s = re.sub(r"\(\s*제?\s*\d+\s*[차회]\s*\)", "", s)  # (제5회차)
    s = re.sub(r"\d+", "", s)                          # 잔여 숫자 변형
    return s.strip()


def _tally_drop(report_nm: str, stock_code: str) -> None:
    """상장사(6자리 코드) 드롭만 집계 — 비상장/펀드는 노이즈라 제외."""
    try:
        if not (stock_code and len(stock_code) == 6 and stock_code.isdigit()):
            return
        key = _norm_report_nm(report_nm)
        if key:
            _DROP_TALLY[key] = _DROP_TALLY.get(key, 0) + 1
            _maybe_log_drop_distribution()
    except Exception:
        pass


_DROP_LOG_DONE = ""   # 프로세스 메모 — marker 파일 반복 read 방지


def _maybe_log_drop_distribution() -> None:
    """하루 1회만 INFO 로 드롭 분포 emit (1분 폴링 스팸 방지, marker 파일 gate)."""
    global _DROP_LOG_DONE
    try:
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        if _DROP_LOG_DONE == today:
            return
        if _DROP_LOG_MARKER.exists() and _DROP_LOG_MARKER.read_text().strip() == today:
            _DROP_LOG_DONE = today
            return
        if sum(_DROP_TALLY.values()) < 20:   # 분포가 어느 정도 모인 뒤에만
            return
        top = sorted(_DROP_TALLY.items(), key=lambda kv: -kv[1])[:15]
        log.info("dart_feed 기타-드롭 분포(상장사, 윈도 내): %s",
                 " · ".join(f"{k}×{v}" for k, v in top))
        _DROP_LOG_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _DROP_LOG_MARKER.write_text(today)
        _DROP_LOG_DONE = today
    except Exception:
        pass


def coverage_audit(days_back: int = 2, max_pages: int = 80) -> dict:
    """DART 풀 firehose vs 우리 분류 대조 — '어제·그제 놓친 공시' 진단
    (사용자 2026-06-11). skip_routine=False 로 전량 fetch 후:
      • kept: 카테고리별 수집 건수
      • dropped_listed: '기타' 드롭된 상장사 공시의 정규화 제목 분포
    VM 에서: .venv/bin/python -m bot.dart_feed --coverage-audit [days]"""
    items = fetch_market_disclosures(days_back=days_back,
                                     max_pages=max_pages, skip_routine=False)
    kept: dict[str, int] = {}
    dropped_listed: dict[str, int] = {}
    dropped_other = 0
    for it in items:
        cat = it.get("category", "기타")
        if cat != "기타":
            kept[cat] = kept.get(cat, 0) + 1
            continue
        sc = it.get("stock_code") or ""
        if len(sc) == 6 and sc.isdigit():
            key = _norm_report_nm(it.get("report_nm", ""))
            dropped_listed[key] = dropped_listed.get(key, 0) + 1
        else:
            dropped_other += 1
    return {"total": len(items), "kept": kept,
            "dropped_listed": dropped_listed, "dropped_other": dropped_other}


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
        _budget_add(1)
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
                _tally_drop(report_nm, stock_code)
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

    attempted = enriched = failed = skipped = 0
    ok_list: list[str] = []
    for item in items:
        cat = item.get("category", "")
        report_nm = item.get("report_nm", "")
        rcept_no = str(item.get("rcept_no", ""))
        corp_code = item.get("corp_code", "")

        if rcept_no and rcept_no in known:
            item["detail"] = known[rcept_no]
            continue

        # 원문 파서 보유 유형은 카테고리 분류와 무관하게 시도(배치 #13-#17
        # 추가 — 담보·최대주주양수도가 '기타'로 분류될 수 있음).
        _force = any(k in report_nm for k in
                     ("전환청구권", "주식분할", "주식병합", "액면분할", "액면병합",
                      "신규시설투자", "투자결정", "유형자산", "회사분할", "분할합병",
                      "담보"))
        # IR 은 enrich 제외(사용자 2026-06-11) — IR 일정은 실적 캘린더가
        # 전담, 피드 카드는 제목만. 수집·아카이브·캘린더 공급은 그대로.
        if _force or cat in ("계약", "자금조달", "주주환원", "신규시설투자",
                             "지분공시", "자산양수도", "회사구조", "소송",
                             "리스크") or "실적" in cat:
            if not corp_code:
                continue
            # 지분공시는 '대량보유'(majorstock 구조화 존재)만 시도 — 임원·
            # 주요주주 소유상황/감사보고서/자산유동화 등은 무료 구조화 소스가
            # 없어(원문도 라벨 미매칭) 시도 예산만 소모하며 계약·증자 카드를
            # 굶김(사용자 2026-06-11 '블락?' 진단). 제목만 표시가 정상.
            if (not _force) and cat == "지분공시" and "대량보유" not in report_nm:
                continue
            # 자금조달 신규 유형(커버리지 감사 2026-06-11) 중 구조화 API·원문
            # 파서가 없는 것(리픽싱/교환청구/차입/대여/보증)은 시도 예산만
            # 소모 — 제목+시총 카드가 정상. 담보제공은 _force(파서 보유).
            if (not _force) and cat == "자금조달" and any(
                    k in report_nm for k in ("전환가액", "교환청구권",
                                             "단기차입금", "금전대여", "채무보증")):
                continue
            # 사이클당 신규 시도 상한 + 스로틀 — 4일치 일괄 enrich 가
            # DART 분당 한도를 태우고 전부 negative-cache 로 고착되던 것
            # 차단(2026-06-11 surfaced). 나머지는 다음 5분 사이클이 이어감.
            if rcept_no and _doc_fail_recent(rcept_no):
                skipped += 1
                continue
            if attempted >= _ENRICH_MAX_PER_CYCLE:
                skipped += 1
                continue
            if _budget_today() >= _BUDGET_HARD:
                skipped += 1
                continue
            attempted += 1
            _budget_add(2)
            time.sleep(0.15)
            try:
                detail = _extract_detail(report_nm, rcept_no, corp_code, api_key)
                lines = list(detail.get("lines", [])) if detail else []
                sc = item.get("stock_code", "")
                if lines:
                    item["detail"] = lines
                    enriched += 1
                    ok_list.append(f"{item.get('corp_name','?')}({rcept_no})")
                elif rcept_no:
                    # 구조화 API 미매칭/원문 필드 부재 — 2h 재시도 억제
                    # (당일 지연 반영 케이스는 2h 후 자연 재시도).
                    _doc_fail_mark(rcept_no, hours=2.0)
                    failed += 1
            except Exception as exc:
                failed += 1
                log.warning("dart_feed enrich %s(%s) 실패: %s",
                            item.get("corp_name", "?"), rcept_no, exc)
                if rcept_no:
                    _doc_fail_mark(rcept_no, hours=1.0)
    log.info("dart_feed enrich: 성공 %d · 실패 %d · 보류(상한/쿨다운) %d%s",
             enriched, failed, skipped,
             (" — " + ", ".join(ok_list)) if ok_list else "")
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
    """FSC 최신 시세 → ['시가총액: X / 현재가: Y원'] 단일 라인 (승인 양식
    2026-06-11, 무료·12h 캐시). 실패 시 []."""
    try:
        from bot.fsc_client import latest_price, fsc_key_ready
        from bot.dart_detail import _won as _fw
        if not fsc_key_ready():
            return []
        p = latest_price(f"{stock_code}.KS")  # FSC 는 suffix 무시(6자리 코드)
        if not p:
            return []
        segs: list[str] = []
        mc = p.get("mrktTotAmt")
        cl = p.get("clpr")
        if mc:
            w = _fw(mc)
            if w:
                segs.append(f"시가총액: {w}")
        if cl:
            try:
                segs.append(f"현재가: {int(float(cl)):,}원")
            except (TypeError, ValueError):
                pass
        return [" / ".join(segs)] if segs else []
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
    """기존 아카이브에 새 공시 merge (rcept_no dedup) + **detail 업데이트
    반영**. 기존-유지 dedup 이 나중 사이클의 enrich 성공분을 버려 카드가
    영원히 비던 근본 버그 fix (사용자 2026-06-11 '거의 안 채워짐')."""
    existing = load_archive(d)
    by_id = {it.get("rcept_no"): it for it in existing}
    added = updated = 0
    for it in new_items:
        rno = it.get("rcept_no")
        old = by_id.get(rno)
        if old is None:
            existing.append(it)
            by_id[rno] = it
            added += 1
        elif it.get("detail") and not old.get("detail"):
            old["detail"] = it["detail"]
            updated += 1
    if added or updated:
        save_archive(d, existing)
        log.info("dart_feed: %s — %d new, %d enriched-update, %d total",
                 d, added, updated, len(existing))
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


# #19 소송/리스크 전 종목 알람 큐는 제거 (사용자 2026-06-11 — 무차별 스팸
# + 휘발성 큐 기준 '신규' 판정이 봇 재시작마다 같은 알림을 재발송하는 버그).
# 교체: bot/dart_fav_alerts.py — 관심종목 한정, 아카이브 스캔 + 영구 seen-set.


# ── 일회성 백필 — 새 분류 룰 소급 (사용자 2026-06-11) ──────────────────────
# (a) 기존 아카이브 항목 카테고리 로컬 재분류 (DART 호출 0 — merge 가
#     detail 만 갱신하고 category 는 안 건드리므로 별도 패스 필요)
# (b) 과거 N일 재fetch — 옛 룰에서 '기타'로 드롭돼 아카이브에 없던 공시
#     (조회공시/공개매수/담보/부도류) 추가. 일 단위 fetch 로 페이지 bound.
# 실행: telegram_bot startup 1회(marker gate, 백그라운드 thread) — SSH 0.
#       수동: .venv/bin/python -m bot.dart_feed --backfill [days]

_BACKFILL_MARKER = _ARCHIVE_DIR.parent / ".dart_feed_backfilled_v2"


def backfill_with_new_rules(days_back: int = 14) -> dict:
    """과거 days_back 일 백필 + 전체 아카이브 재분류. 결과 통계 반환."""
    stats = {"reclassified": 0, "added": 0, "days": 0, "skipped_budget": 0}

    # (a) 로컬 재분류 — 아카이브 전체 (호출 0, 안전)
    today = datetime.now(_KST).date()
    for i in range(60):
        d = today - timedelta(days=i)
        items = load_archive(d)
        if not items:
            continue
        changed = False
        for it in items:
            new_cat = _classify_report(it.get("report_nm", ""))
            if new_cat != "기타" and new_cat != it.get("category"):
                it["category"] = new_cat
                stats["reclassified"] += 1
                changed = True
        if changed:
            save_archive(d, items)

    # (b) 과거 재fetch — 일 단위 (예산 헤드룸 1,000 콜 보존하며 중단)
    for i in range(days_back, -1, -1):
        if _budget_today() >= _BUDGET_HARD - 1000:
            stats["skipped_budget"] += 1
            continue
        d = today - timedelta(days=i)
        try:
            fetched = fetch_market_disclosures(target_date=d, days_back=0,
                                               max_pages=60)
        except Exception as exc:
            log.warning("dart_feed backfill: %s fetch 실패: %s", d, exc)
            continue
        before = {it.get("rcept_no") for it in load_archive(d)}
        by_day: dict[date, list[dict]] = {}
        for it in fetched:
            raw = str(it.get("date") or "").strip()
            try:
                dd = datetime.strptime(raw[:8], "%Y%m%d").date()
            except (ValueError, TypeError):
                dd = d
            by_day.setdefault(dd, []).append(it)
        for dd, day_items in by_day.items():
            merge_and_save(dd, day_items)
        stats["added"] += sum(1 for it in fetched
                              if it.get("rcept_no") not in before)
        stats["days"] += 1
        time.sleep(0.3)   # 페이지 버스트 사이 호흡 (DART 분당 한도 보호)

    log.info("dart_feed backfill: 재분류 %d · 신규 %d (%d일 fetch, 예산스킵 %d)",
             stats["reclassified"], stats["added"], stats["days"],
             stats["skipped_budget"])
    return stats


def backfill_once_if_needed(days_back: int = 14) -> dict | None:
    """marker 파일 gate — 최초 1회만 백필 (charts .charts_backfilled 패턴)."""
    if _BACKFILL_MARKER.exists():
        return None
    stats = backfill_with_new_rules(days_back=days_back)
    try:
        _BACKFILL_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _BACKFILL_MARKER.write_text(datetime.now(_KST).isoformat())
    except OSError:
        pass
    return stats


# ── CLI: python -m bot.dart_feed ──

def run_once(target_date: date | None = None,
             days_back: int = 3) -> list[dict]:
    """1회 fetch(최근 days_back+1일 윈도) → enrich → 공시일별로 분배 merge.

    각 item 을 실제 접수일(rcept_dt)의 아카이브 파일에 저장(dedup)해 대시보드
    날짜 그룹과 일치. 새벽 당일 0건이어도 직전 거래일 공시가 보여짐."""
    if target_date is None:
        target_date = datetime.now(_KST).date()
    # 스캔 모드 — 매 사이클 4일 풀 listing 은 낭비(시즌 헤드룸 잠식).
    # 시간당 1회만 4일 풀스캔(새벽 보정·정정 회수), 그 외엔 당일만(신규는
    # 어차피 오늘 접수분). 일일 버짓 초과 시 최소 모드(오늘 3p)로 감속.
    _now = time.time()
    try:
        _last_full = _FULLSCAN_TS.stat().st_mtime
    except OSError:
        _last_full = 0.0
    if _budget_today() >= _BUDGET_HARD:
        days_back, _max_pages = 0, 3
        log.warning("dart_feed: 일일 콜 버짓 %d 초과 — 최소 모드", _BUDGET_HARD)
    elif _now - _last_full >= 3600:
        _max_pages = 20
        try:
            _FULLSCAN_TS.parent.mkdir(parents=True, exist_ok=True)
            _FULLSCAN_TS.touch()
        except OSError:
            pass
    else:
        # 증분(매분): 당일 최신 3p(300건) — 시즌 피크 분당 증분(수십 건)
        # 대비 충분, listing ~4.3천/일.
        days_back, _max_pages = 0, 3
    log.info("dart_feed: fetching %s (최근 %d일, ≤%dp, 오늘 콜 %d)",
             target_date, days_back + 1, _max_pages, _budget_today())
    items = fetch_market_disclosures(target_date, days_back=days_back,
                                     max_pages=_max_pages)
    # 백필 대기열 — 최근 3일 아카이브에서 detail 없는 항목을 합쳐 enrich.
    # 증분(당일) 사이클은 fetch 가 당일분만 담아 새벽/한산 시간대에 백필이
    # 시간당 풀스캔 8건으로 기어가던 버그 fix (사용자 2026-06-11).
    fetched_ids = {it.get("rcept_no") for it in items}
    pending: list[dict] = []
    try:
        for day_items in load_all_archives(days_back=3).values():
            for it in day_items:
                if it.get("rcept_no") in fetched_ids or it.get("detail"):
                    continue
                pending.append(it)
    except Exception as exc:
        log.warning("dart_feed: 백필 대기열 로드 실패: %s", exc)
    work = items + pending
    if work:
        enrich_disclosures(work)

    # 접수일(rcept_dt 'YYYYMMDD')별 그룹핑 → 각 날짜 파일에 merge
    # (work 전체 — 아카이브 항목의 enrich 성공분도 detail 업데이트로 반영)
    by_day: dict[date, list[dict]] = {}
    for it in work:
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

    import sys as _sys
    if any("backfill" in a for a in _sys.argv[1:]):
        # 새 분류 룰 소급 백필 (재분류 + 과거 N일 재fetch):
        #   .venv/bin/python -m bot.dart_feed --backfill [days]
        _days = 14
        for a in _sys.argv[1:]:
            if a.isdigit():
                _days = int(a)
        st = backfill_with_new_rules(days_back=_days)
        print(f"[backfill] 재분류 {st['reclassified']}건 · 신규 {st['added']}건 "
              f"({st['days']}일 fetch · 예산스킵 {st['skipped_budget']})")
        try:
            from bot.dashboard import regenerate_dart_feed_index
            regenerate_dart_feed_index()
            print("[backfill] dart_feed.html regen 완료")
        except Exception as exc:
            print(f"[backfill] regen 실패: {exc}")
        raise SystemExit(0)
    if any("coverage-audit" in a or "coverage_audit" in a for a in _sys.argv[1:]):
        # DART 풀 firehose vs 우리 분류 대조 (어제·그제 놓친 공시 진단):
        #   .venv/bin/python -m bot.dart_feed --coverage-audit [days]
        _days = 2
        for a in _sys.argv[1:]:
            if a.isdigit():
                _days = int(a)
        rep = coverage_audit(days_back=_days)
        print(f"[coverage] 윈도 {_days + 1}일 · 총 {rep['total']}건")
        print("[coverage] 수집(카테고리별):")
        for k, v in sorted(rep["kept"].items(), key=lambda kv: -kv[1]):
            print(f"  {k:8s} {v:5d}")
        print(f"[coverage] 드롭 — 비상장/펀드 {rep['dropped_other']}건 (의도된 제외)")
        print("[coverage] 드롭 — 상장사 '기타' 제목 분포 (보강 후보):")
        for k, v in sorted(rep["dropped_listed"].items(), key=lambda kv: -kv[1])[:40]:
            print(f"  {v:4d} × {k}")
        raise SystemExit(0)
    if any("selftest" in a for a in _sys.argv[1:]):
        # VM 1줄 진단(쓰기 없음): 아카이브의 detail 없는 게이트 대상 5건을
        # 실제 DART 로 추출 시도, 단계별 결과 출력.
        #   .venv/bin/python -m bot.dart_feed --selftest
        print(f"[selftest] DART_API_KEY: {'있음' if _dart_api_key() else '❌ 없음'}")
        print(f"[selftest] 오늘 콜 버짓: {_budget_today()} / {_BUDGET_HARD}")
        cands = []
        for _d, _its in load_all_archives(days_back=3).items():
            for _it in _its:
                if _it.get("detail") or not _it.get("corp_code"):
                    continue
                _t = _it.get("report_nm", "")
                if any(k in _t for k in ("공급계약", "유상증자", "전환사채",
                                          "대량보유", "자기주식", "전환청구권")):
                    cands.append(_it)
        print(f"[selftest] detail 없는 대상 후보: {len(cands)}건 — 상위 5건 시도")
        for _it in cands[:5]:
            _r = _it.get("rcept_no", "")
            cd = "쿨다운중" if _doc_fail_recent(_r) else "시도가능"
            res = None
            if cd == "시도가능":
                try:
                    res = _extract_detail(_it.get("report_nm", ""), _r,
                                          _it.get("corp_code", ""),
                                          _dart_api_key() or "")
                except Exception as exc:
                    res = f"예외: {exc}"
            nm = _it.get("corp_name", "?")
            t = _it.get("report_nm", "")[:30]
            if isinstance(res, dict):
                print(f"  ✅ {nm} | {t} → {len(res['lines'])}필드: {res['lines'][:2]}")
            else:
                print(f"  ❌ {nm} | {t} → {cd if cd != '시도가능' else res or 'None(필드부족/미매칭)'}")
        print("[selftest] 끝 — ✅ 가 있으면 파이프라인 정상(다음 사이클에 저장됨)")
        raise SystemExit(0)

    items = run_once()
    print(f"dart_feed: {len(items)} disclosures archived")

    # regenerate dashboard
    try:
        from bot.dashboard import regenerate_dart_feed_index
        regenerate_dart_feed_index()
        print("dart_feed: dashboard regenerated")
    except Exception as exc:
        print(f"dart_feed: dashboard regen failed: {exc}")
