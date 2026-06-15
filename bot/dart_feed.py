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
    # 회사분할(인적/물적) = 회사구조 (사용자 2026-06-13 — 옛 M&A 버킷에서
    # 이동). '분할합병'은 합병 성격이라 자산양수도 유지 (합병 포함 시 제외).
    if (("회사분할" in t or "인적분할" in t or "물적분할" in t)
            and "합병" not in t):
        return "회사구조"
    if any(k in t for k in _IR_KW):
        return "IR"
    if any(k in t for k in _EQUITY_KW):
        return "지분공시"
    # 최대주주 등 소유주식변동신고서 (공정거래법, 라이브 2026-06-13) — 지분율
    # 희석/집중/전량처분. report_nm 은 '소유주식변동', doc 표는 '주식보유 변동'.
    if "소유주식변동" in t or ("주식보유" in t and "변동" in t):
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
    # 유형자산 양수/양도/처분/취득(+철회) = 자산양수도 (사용자 2026-06-13
    # '유형자산처분이 왜 신규시설투자' — chart_events _CAPEX_KW 의
    # '유형자산'이 capex 로 흘러들던 것 차단). 신규시설투자는 전용
    # 양식('신규시설투자등')만.
    if "유형자산" in t and "신규시설투자" not in t:
        return "자산양수도"
    # 배당 분리 (사용자 2026-06-12 '배당은 주주환원에서 빼서 따로') —
    # chart_events shareholder KW('배당' 포함)보다 먼저. 조회공시('배당설'
    # 답변 류)는 위에서 이미 분기돼 충돌 없음.
    if "배당" in t:
        return "배당"
    from bot.chart_events import classify
    eng = classify(t)
    if eng in _CATEGORY_MAP:
        return _CATEGORY_MAP[eng]
    # 보강 후보 (사용자 2026-06-13 '보강 후보 파싱 진행') — 기타-드롭되던 실제
    # 기업 사건을 카테고리화해 카드로 노출. chart_events.classify **뒤**에 둬
    # 소송('과징금부과처분취소소송' 등)·기존 특정 분류가 우선(소송>리스크).
    # 전부 저빈도(3일 1~8건)라 홍수 위험 없음. 제목이 곧 완전한 신호인
    # 거버넌스/리스크류 — is_parse_target 의 _TITLE_ONLY_OK_KW 로 미파싱 색칠 제외.
    # 벌금/과징금/과태료 부과 = 리스크. 단 그에 대한 '소송'(과징금부과처분
    # 취소소송·청구의소 등)은 소송이므로 제외 — chart_events 가 '소송'만 잡고
    # '…청구의소/…의소'는 못 잡아 누수되던 것 가드.
    _is_lawsuit = ("소송" in t or "청구의소" in t or t.endswith("의소")
                   or "제소" in t or "가처분" in t)
    if (not _is_lawsuit
            and any(k in t for k in ("벌금", "과태료", "과징금",
                                     "중대재해", "산업재해", "재해발생"))):
        return "리스크"
    # 대표이사/대표집행임원 '변경' — '대표이사(대표집행임원)변경' 괄호 변형도
    # 잡도록 두 토큰 + '변경' 조합 (라이브 2026-06-13: 괄호형 누수 적발).
    if (("변경" in t and ("대표이사" in t or "대표집행임원" in t))
            or any(k in t for k in ("상호변경", "본점소재지변경"))
            or ("사외이사" in t and any(k in t for k in ("선임", "해임", "퇴임")))
            or "주식매수선택권" in t):       # 스톡옵션 부여 — 거버넌스 결정
        return "회사구조"
    if "기업가치제고" in t:                   # 밸류업 프로그램 — 주주환원 지향
        return "주주환원"
    # 보강 후보 2차 (사용자 2026-06-15 coverage-audit '기타 제목 분포' — 저빈도
    # 실제 기업 사건). 상장적격성 심사(기업심사위원회·정리매매·개선기간/계획·
    # 상장유지) = 상장폐지 리스크. chart 분류·소송 가드 뒤라 충돌 없음.
    if any(k in t for k in ("기업심사위원회", "상장적격성", "정리매매",
                            "개선기간", "개선계획", "상장유지")):
        return "리스크"
    if "회계처리기준위반" in t:                # 회계 위반(임원 해임권고 등) = 리스크
        return "리스크"
    # 특수관계인 거래·지주회사 자회사편입/탈퇴·대규모기업집단·동일인 계열출자/
    # 거래·자산재평가 = 회사구조(지배구조/그룹). 담보/차입은 위에서 자금조달 선점.
    if any(k in t for k in ("특수관계인", "지주회사", "대규모기업집단",
                            "동일인", "자산재평가")):
        return "회사구조"
    # 조건부자본증권 발행·교환가액 조정·주권관련 사채 취득 = 자금조달.
    if any(k in t for k in ("조건부자본증권", "교환가액", "주권관련사채")):
        return "자금조달"
    if "투자판단" in t:                        # 투자판단 주요경영사항(임상·국책 등)
        return "실적"
    if "장래사업" in t or "공정공시" in t:
        # 장래사업ㆍ경영계획/수시공시의무관련사항(공정공시) — 가이던스성
        # wrapper. chart 분류 '뒤'에 두어 '공급계약체결(공정공시)' 류가
        # 계약 등 더 특정한 종류를 유지하게 (백필 정밀화 2026-06-11).
        return "실적"
    return "지분공시" if "보고서" in t else "기타"


# 증권 발행·등록 서류 (투자설명서·일괄신고·증권신고서) — 기업 '사건'이 아니라
# 발행/등록 문서로, 펀드 투자설명서와 동류다. 상장사 명의여도 카드 대상이
# 아니므로 coverage 의 '보강 후보(미파싱)'가 아니라 '의도된 제외'로 집계해야
# 한다 (사용자 2026-06-13 '펀드 투자설명서를 정말 미파싱과 구분되게'). 실제
# 자금조달 이벤트(유상증자·CB 발행 결정 등)는 별도 결정공시로 이미 분류되므로
# 이들을 제외해도 신호 손실은 없다 (ELS/DLS 일괄신고 추가서류가 대표적 노이즈).
# '집합투자증권'(펀드 단위) = 증권발행실적보고서(집합투자증권) 류 — 펀드 발행
# 문서라 의도된 제외(사용자 2026-06-14 미파싱 정리, 투자신탁/투자회사 발행실적).
_NONCORP_DOC_KW = ("투자설명서", "일괄신고", "증권신고서", "집합투자증권")


def _is_noncorp_doc(report_nm: str) -> bool:
    """발행·등록 서류(투자설명서/일괄신고/증권신고서 — 펀드 동류 비대상)면
    True. 순수. coverage 보강후보·드롭모니터링 양쪽에서 제외용."""
    t = report_nm or ""
    return any(k in t for k in _NONCORP_DOC_KW)


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


def _elestock_lines(row: dict) -> list[str]:
    """elestock(임원ㆍ주요주주 소유상황) row → 카드 lines (순수 — 단위테스트).

    보고자/직위·관계/소유주식+증감/비율+증감. ⚠️ sp_stock_lmp_rate 가
    가끔 100.0(본인 보유분 비율)으로 와 회사 전체 지분율로 오인되는 환각
    (삼성 005930 2026-05-31) — ≥50% 는 비율 생략(수량만)."""
    parts: list[str] = []
    repror = str(row.get("repror") or "").strip()
    if repror:
        seg = f"보고자: {repror}"
        ofcps = str(row.get("isu_exctv_ofcps") or "").strip()
        main = str(row.get("isu_main_shrholdr") or "").strip()
        rel = " · ".join(x for x in (ofcps, main) if x and x != "-")
        if rel:
            seg += f" ({rel})"
        parts.append(seg)
    cnt = _to_float(row.get("sp_stock_lmp_cnt"))
    d_cnt = _to_float(row.get("sp_stock_lmp_irds_cnt"))
    if cnt is not None:
        seg = f"소유주식: {int(cnt):,}주"
        if d_cnt:
            seg += f" ({int(d_cnt):+,}주)"
        parts.append(seg)
    rate = _to_float(row.get("sp_stock_lmp_rate"))
    d_rate = _to_float(row.get("sp_stock_lmp_irds_rate"))
    if rate is not None and rate < 50.0:   # ≥50% = 본인분 비율 환각 의심
        seg = f"지분율: {rate:.2f}%"
        if d_rate:
            seg += f" ({d_rate:+.2f}%p)"
        parts.append(seg)
    return parts


def _extract_elestock(rcept_no: str, corp_code: str,
                      api_key: str) -> dict | None:
    """임원ㆍ주요주주특정증권등소유상황보고서 → elestock.json 구조화
    (지분공시 전체 파싱 — 사용자 2026-06-12 '지분공시도 다 파싱, 무료').
    majorstock 와 동일 패턴: corp_code 로 list → rcept_no 매칭. graceful."""
    try:
        r = requests.get(
            f"{_DART_BASE}/elestock.json",
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
        parts = _elestock_lines(row)
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


# 같은 프로세스 내 원문 평문 재사용 (cap 64) — 전용 파서가 12h fail 마킹
# 후에도 같은 enrich 시도 안에서 generic 폴백이 재다운로드 0·게이트 무관
# 으로 본문을 받게 (2026-06-12 '조금 다른 양식은 알아서').
_DOC_TEXT_MEM: dict[str, str] = {}


def _fetch_doc_text(rcept_no: str, api_key: str) -> str | None:
    """공시 원문(document.xml zip) → 태그 제거 평문. 실패 시 None +
    negative-cache (₩0·LLM 0·stdlib)."""
    if rcept_no in _DOC_TEXT_MEM:   # fail-mark 게이트보다 먼저 (이미 받음)
        return _DOC_TEXT_MEM[rcept_no]
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
        out = re.sub(r"\s+", " ", txt)
        if len(_DOC_TEXT_MEM) >= 64:
            _DOC_TEXT_MEM.pop(next(iter(_DOC_TEXT_MEM)))
        _DOC_TEXT_MEM[rcept_no] = out
        return out
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

# ── 리스크 — 사용자 제공 9양식 기준 (2026-06-12 '리스크 완료').
# 매매거래정지(중요공시 30분형 + 주권 정지형) / 해산사유 / 불성실공시법인
# 지정·지정예고 / 기타시장안내(상폐 심의) + 회생절차(전용 파서, 아래).

_SUSPENSION_FIELDS = [
    # 정지 양식은 '3.정지기간' 압축 표기(점 뒤 공백 없음) — stop 은
    # `\d{1,2}\s*\.` (공백 불요). 값에 점 없는 charset 이라 날짜 안전.
    ("사유",
     r"(?:매매거래)?정지\s*사유[^가-힣A-Za-z0-9]{0,12}?"
     r"([가-힣A-Za-z0-9()%,·ㆍ ]{2,60}?)\s*(?:\d{1,2}\s*\.|근거|정지기간|$)", "text"),
    ("정지",
     r"(?:매매거래정지|가\s*\.?\s*정지)\s*일시[^0-9]{0,20}?"
     r"(\d{4}-\d{1,2}-\d{1,2}(?:\s*\d{1,2}:\d{2})?)", "text"),
    # 해제/만료는 날짜('2026-06-12 11:36')뿐 아니라 자유 텍스트('법원의
    # 결정 확인시까지'·'신주권 변경상장일 전일까지') 수용 (현대사료/DH오토).
    # 값 charset 에 '.' 없음 → `\d\s*\.` stop 이 날짜를 안 가름.
    ("해제·만료",
     r"(?:해제일시|만료일시)[^가-힣A-Za-z0-9]{0,12}?"
     r"([가-힣A-Za-z0-9 :,()\-]{2,60}?)\s*(?:\d{1,2}\s*\.|근거|$)", "text"),
    ("비고",
     r"기타[^가-힣A-Za-z0-9]{0,10}?사유\s*[::]\s*"
     r"([가-힣A-Za-z0-9 ,()]{2,30})", "text"),
    # 상장폐지 사유발생 정지(SPAC 류, 리스크-2) — 기타란의 정리매매/상폐일
    ("정리매매",
     r"정리매매기간[^0-9]{0,8}?(\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2}\s*~\s*"
     r"\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2}(?:\s*\([^)]{1,12}\))?)", "text"),
    ("상장폐지일",
     r"상장폐지일[^0-9]{0,8}?(\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2})", "text"),
]

_DISSOLUTION_FIELDS = [
    # 번호 라벨 필수('1. 해산사유') — 문서 머리 '해산사유 발생' 제목이
    # '발생'을 값으로 오캡처하는 것 차단
    ("해산사유",
     r"\d\s*\.\s*해산사유[^가-힣A-Za-z0-9]{0,12}?"
     r"([가-힣A-Za-z0-9 ()]{2,40}?)\s*(?:\d{1,2}\.\s|해산내용|$)", "text"),
    ("내용",
     r"해산내용[^가-힣A-Za-z0-9]{0,12}?"
     r"([가-힣A-Za-z0-9 ()]{2,50}?)\s*(?:\d{1,2}\.\s|$)", "text"),
    ("발생일",
     r"해산사유\s*발생일[^0-9]{0,20}?"
     r"(\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}-\d{1,2}-\d{1,2})", "text"),
]

_UNFAITHFUL_FIELDS = [
    # 다건 유형('공시불이행,공시번복,공시변경' — 리스크-2) 콤마 허용
    ("유형",
     r"불성실공시\s*유형[^가-힣A-Za-z0-9]{0,12}?"
     r"([가-힣A-Za-z0-9,· ]{2,30}?)\s*(?:\d{1,2}\.\s|내\s*용|$)", "text"),
    ("내용",
     r"내\s*용[^가-힣A-Za-z0-9]{1,12}?"
     r"([가-힣A-Za-z0-9()'’‘%.,·ㆍ \-]{4,80}?)\s*(?:\d{1,2}\.\s|원공시일|지정|$)", "text"),
    ("벌점", r"부과\s*벌점[^0-9]{0,16}?([\d.]+)", "text"),
    ("누계벌점", r"누계\s*벌점[^0-9]{0,12}?([\d.]+)", "text"),
    ("제재금",
     r"공시위반\s*제재금\s*\(?원?\)?[^0-9]{0,12}?([\d,]{4,})", "won"),
    ("지정일", r"지정\s*[ㆍ·]?\s*부과일자[^0-9]{0,16}?(\d{4}-\d{1,2}-\d{1,2})", "text"),
    ("지정예고일", r"지정\s*예고일[^0-9]{0,12}?(\d{4}-\d{1,2}-\d{1,2})", "text"),
    ("결정시한", r"결정\s*시한[^0-9]{0,12}?(\d{4}-\d{1,2}-\d{1,2})", "text"),
]

_MARKET_NOTICE_FIELDS = [
    # 자유 서술 박스 — '제목 :' 행 + 결론 문장(상폐 의결/결정 예정)만 발췌.
    # 제목 stop 에 본문 시작어(거래소는/동사는/당사는) 추가 — 실질심사·
    # 관리종목우려 류는 '25. 날짜 인용이 없어 제목이 안 잡히던 것 (리스크-2)
    ("제목",
     r"제\s*목\s*[::]?\s*([가-힣A-Za-z0-9()㈜·ㆍ,.\- ]{6,70}?)\s*"
     r"(?:['‘’]\s*\d{2}\s*\.|\d{2}\.\d{2}\.\d{2}|거래소는|동사는|당사는|$)", "text"),
    # 결론은 주어 앵커(거래소는/동사는/위원회는 …)부터 — 제목 에코·중간
    # 단어 선두 bleed 차단 (리스크-2). 주어 없으면 결론 생략(제목으로 충분).
    ("결론",
     r"((?:거래소는|동사는|당사는|코스닥시장위원회는|기업심사위원회는)"
     r"[가-힣A-Za-z0-9()㈜'‘’.,%· ]{4,140}?"
     r"(?:의결하였습니다|알려드립니다|예정입니다|결정하였습니다|우려가\s*있습니다))", "text"),
    # 관리종목 지정우려 예고(SPAC) — 지정 예정일/제출기한
    ("지정예정",
     r"관리종목\s*지정일[^0-9]{0,12}?"
     r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})", "text"),
    ("제출기한",
     r"제출기한[^0-9]{0,10}?"
     r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})", "text"),
]


def _rehab_lines(txt: str) -> list[str]:
    """회생절차 개시결정/신청 원문 → 카드 lines (순수 — 단위테스트).

    사건번호·결정일·법원·관리인 + 회생계획안 제출기한. 제출기한은
    '회생계획안' 앵커 + **마지막 출현** 사용 — 정정공시는 래퍼(정정전
    stale 값)가 본문보다 앞이라 첫 출현이 옛 값 (수원 2025회합196,
    2026-06-10 정정 예시)."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)
    m = re.search(r"사건\s*번호[^0-9]{0,16}?"
                  r"(\d{4}\s*[가-힣]{1,4}\s*\d{1,8}(?:\s*회생)?)", txt)
    if m:
        parts.append(f"사건번호: {m.group(1).strip()}")
    m = re.search(r"결정일자[^0-9]{0,12}?(\d{4}-\d{1,2}-\d{1,2})", txt)
    if m:
        parts.append(f"결정일: {m.group(1)}")
    m = re.search(r"관할\s*법원[^가-힣]{0,12}?"
                  r"([가-힣 ]{2,20}?법원(?:\s*[가-힣]{1,6}지원)?)", txt)
    if m:
        parts.append(f"관할법원: {m.group(1).strip()}")
    m = re.search(r"관리인\s*성명\s*(?:\([^)]{0,20}\))?\s*"
                  r"([가-힣A-Za-z()· ]{2,30}?)\s*(?:\d{1,2}\.\s|$)", txt)
    if m:
        parts.append(f"관리인: {m.group(1).strip()}")
    ds = re.findall(r"회생계획안의?\s*제출기간[을은]?[^0-9]{0,8}?"
                    r"(\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2})", txt)
    if ds:
        parts.append(f"회생계획안 제출기한: {ds[-1].strip()}")
    return parts


def _volume_lines(txt: str) -> list[str]:
    """영업(잠정)실적(공정공시) **물량형** — 금액표가 전부 '-' 이고 월간
    판매물량(천톤·대 등)만 있는 변형 (사용자 2026-06-12 '파싱안된것들':
    도시가스 천톤 / 완성차 대수). 총계 행 + 누적 행 발췌. 순수."""
    parts: list[str] = []
    m = re.search(r"당[월기]\s*실적\s*\(\s*['’]?\s*(\d{2,4})[.년]\s*(\d{1,2})\s*월?\s*\)",
                  txt)
    month = f"{m.group(2)}월" if m else "당월"
    mu = re.search(r"구분[^가-힣A-Za-z0-9]{0,4}?단위\s*[(:]\s*([가-힣A-Za-z]{1,8})",
                   txt)
    unit = mu.group(1) if mu else ""
    mt = re.search(r"(?:총\s*계|(?<![가-힣])계)\s+([\d,]{2,})\s+([\d,]{2,})\s+"
                   r"(-?[\d.]+)\s+(?:-\s+)?([\d,]{2,})\s+(-?[\d.]+)", txt)
    if mt:
        try:
            parts.append(f"{month} 판매: 총 {mt.group(1)}{unit} "
                         f"(전월 {float(mt.group(3)):+.1f}% · "
                         f"전년동월 {float(mt.group(5)):+.1f}%)")
        except ValueError:
            pass
    mc = re.search(r"(?:총\s*계|(?<![가-힣])계)\s+([\d,]{4,})\s+(?:-\s+){1,4}"
                   r"([\d,]{4,})\s+(-?[\d.]+)", txt)
    if mc and (not mt or mc.start() != mt.start()):
        try:
            parts.append(f"누적: {mc.group(1)}{unit} "
                         f"(전년동기 {float(mc.group(3)):+.1f}%)")
        except ValueError:
            pass
    return parts


def _parse_volume(rcept_no: str, api_key: str) -> dict | None:
    """물량형 잠정실적 — 원문 fetch 후 _volume_lines. 0건이면 12h 쿨다운."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    parts = _volume_lines(txt)
    if not parts:
        _doc_fail_mark(rcept_no, hours=12.0)
        return None
    return {"lines": parts}


def _fair_disclosure_lines(txt: str) -> list[str]:
    """수시공시의무관련사항(공정공시) — 공시제목 + 첫 불릿 항목 ≤3
    (주주환원정책 발표 예시: 적용기간/목표 환원율/산식). 순수."""
    parts: list[str] = []
    m = re.search(r"공시\s*제목[^가-힣A-Za-z0-9]{0,10}?"
                  r"([\s\S]{4,60}?)\s*(?:□|관련\s*수시공시|$)", txt)
    if m:
        parts.append(f"제목: {m.group(1).strip()}")
    for b in re.findall(r"[-–]\s+([가-힣A-Za-z0-9()%:+÷,.'’ ]{6,70}?)"
                        r"(?=\s+[-–□]\s|\s*\d\s*\.\s|$)", txt)[:3]:
        parts.append(b.strip())
    return parts


def _future_plan_lines(txt: str) -> list[str]:
    """장래사업ㆍ경영계획(공정공시) — 계획 사항/예상투자금액/이사회결의일
    (네이버-엔비디아 AI 팩토리 예시). 순수."""
    parts: list[str] = []
    # 번호 라벨 필수 — 머리말 '※ 동 정보는 장래 계획사항으로서…'의
    # '으로서…' 오캡처 차단 (해산사유와 동일 클래스)
    m = re.search(r"\d\s*\.\s*장래\s*계획\s*사항[^가-힣A-Za-z0-9(\[]{0,10}?"
                  r"([\s\S]{6,80}?)\s*(?:\d\s*\.\s|목\s*적)", txt)
    if m:
        parts.append(f"계획: {m.group(1).strip()}")
    m = re.search(r"예상\s*투자금액[^가-힣A-Za-z0-9]{0,10}?"
                  r"([\s\S]{2,70}?)\s*(?:기대\s*효과|\d\s*\.\s)", txt)
    if m:
        parts.append(f"예상투자금액: {m.group(1).strip()}")
    m = re.search(r"이사회\s*결의일[^0-9]{0,16}?(\d{4}-\d{1,2}-\d{1,2})", txt)
    if m:
        parts.append(f"이사회결의일: {m.group(1)}")
    return parts


def _material_title_category(title_line: str) -> str | None:
    """투자판단 관련 주요경영사항 제목 → 카테고리 승격 (결정적, 양 경로
    공유 — fresh-parse + known-reuse)."""
    t = title_line or ""
    if any(k in t for k in ("소송", "가처분", "판결")):
        return "소송"
    if any(k in t for k in ("기술이전", "라이선스", "공급계약", "계약 체결",
                            "계약체결", "수주")):
        return "계약"
    if any(k in t for k in ("배당", "자사주", "주주환원", "소각")):
        return "주주환원"
    if any(k in t for k in ("상장폐지", "거래정지", "회생", "파산")):
        return "리스크"
    return None


def _material_mgmt_lines(txt: str) -> dict | None:
    """투자판단 관련 주요경영사항 — 유가증권 catch-all (한미약품 기술이전
    예시). 제목/계약상대방/계약금액(총+Upfront)/체결일 + 제목 키워드
    카테고리 승격. 2미만 None. 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    title = _g(r"\d\s*\.\s*제\s*목[^가-힣A-Za-z0-9(\[]{0,10}?"
               r"([\s\S]{4,80}?)\s*\d{1,2}\s*\.\s")
    if title:
        parts.append(f"제목: {title}")
    cp = _g(r"계약\s*상대방?[^A-Za-z가-힣0-9]{0,8}?"
            r"([A-Za-z가-힣0-9(][A-Za-z가-힣0-9(),.&·:\- ]{1,59}?)"
            r"\s*(?:\d\s*\)|\d\s*\.\s|$)")
    if cp:
        parts.append(f"상대방: {cp}")
    # 금액 — US$(한미)·USD(메멘토 코스닥형)·원화 + (약 N억원)/(₩ N) 환산 병기
    _PAREN = r"(?:\s*\(\s*(?:약|₩)?[\s\d,조억.]*원?\s*\))?"
    amt = _g(r"(?:계약금액|기술이전\s*금액)[^0-9]{0,12}?[::]?\s*"
             r"(총?\s*(?:US\$|USD)\s*[\d,]{4,}" + _PAREN +
             r"|총?\s*[\d,]{4,}\s*원?" + _PAREN + r")")
    if amt:
        parts.append(f"계약금액: {amt}")
    up = _g(r"(?:계약금\s*\(?\s*Upfront\s*\)?|Upfront|선급금)[^0-9]{0,12}?[::]?\s*"
            r"(총?\s*(?:US\$|USD)\s*[\d,]{4,}" + _PAREN + r"|[\d,]{4,})")
    if up:
        parts.append(f"계약금(선급/Upfront): {up}")
    d = _g(r"계약\s*체결일[^0-9]{0,10}?"
           r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    if d:
        parts.append(f"체결일: {d}")
    if len(parts) < 2:
        return None
    out: dict = {"lines": parts}
    cat = _material_title_category(title or "")
    if cat:
        out["category"] = cat
    return out


def _extract_material_mgmt(rcept_no: str, api_key: str) -> dict | None:
    """투자판단 주요경영사항 — fetch 후 _material_mgmt_lines. 12h 쿨다운."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    out = _material_mgmt_lines(txt)
    if out is None:
        _doc_fail_mark(rcept_no, hours=12.0)
    return out


# 자기주식취득결정 원문 폴백 (미파싱-2, 2026-06-12) — 구조화 API
# (tsstkAqDecsn)가 7일 윈도/필드 변형으로 비는 케이스를 표준 표에서 직접.
_BUYBACK_DOC_FIELDS = [
    ("취득예정", r"취득예정주식\s*\(주\)[^0-9]{0,16}?([\d,]{3,})", "num"),
    ("취득예정금액", r"취득예정금액\s*\(원\)[^0-9]{0,16}?([\d,]{6,})", "won"),
    ("시작", r"취득예상기간[\s\S]{0,16}?시작일[^0-9]{0,10}?"
            r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})", "text"),
    ("종료", r"취득예상기간[\s\S]{0,60}?종료일[^0-9]{0,10}?"
            r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})", "text"),
    ("목적", r"취득목적[^가-힣A-Za-z0-9]{0,10}?"
            r"([가-힣A-Za-z0-9 ]{2,40}?)\s*(?:\d{1,2}\s*\.|취득방법|$)", "text"),
    ("방법", r"취득방법[^가-힣A-Za-z0-9]{0,10}?"
            r"([가-힣A-Za-z0-9() ]{2,40}?)\s*(?:\d{1,2}\s*\.|위탁|$)", "text"),
]


def _disposal_lines(txt: str) -> list[str]:
    """자기주식 처분 결정 표준 표 (미파싱-3) — 구조화 API(tsstkDpDecsn)가
    비는 케이스 원문 폴백. 처분예정·가격·금액·기간·목적·상대방. 처분예정
    기간은 **마지막 출현**(기재정정 래퍼의 '정정전' stale 날짜가 본문보다
    앞 — 처분금지 가처분 대응 연기 예시 6/17→7/3). 순수."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    from bot.dart_detail import _won

    n = _g(r"처분예정주식\s*\(주\)[^0-9]{0,16}?([\d,]{2,})")
    if n:
        parts.append(f"처분예정: {int(n.replace(',', '')):,}주")
    pr = _g(r"처분\s*대상\s*주식가격\s*\(원\)[^0-9]{0,16}?([\d,]{3,})")
    amt = _g(r"처분예정금액\s*\(원\)[^0-9]{0,16}?([\d,]{6,})")
    if amt:
        seg = f"처분금액: {_won(amt) or amt + '원'}"
        if pr:
            seg += f" (주당 {int(pr.replace(',', '')):,}원)"
        parts.append(seg)
    ss = re.findall(r"처분예정기간[\s\S]{0,16}?시작일[^0-9]{0,10}?"
                    r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})", txt)
    es = re.findall(r"처분예정기간[\s\S]{0,60}?종료일[^0-9]{0,10}?"
                    r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})", txt)
    if ss:
        s = _clean_kdate(ss[-1])
        e = _clean_kdate(es[-1]) if es else ""
        parts.append(f"기간: {s} ~ {e}" if e else f"기간: {s} ~")
    why = _g(r"처분목적[^가-힣A-Za-z0-9]{0,10}?"
             r"([가-힣A-Za-z0-9 ]{2,40}?)\s*(?:\d{1,2}\s*\.|처분방법|$)")
    if why:
        parts.append(f"목적: {why}")
    to = _g(r"처분상대방[^가-힣A-Za-z0-9(]{0,10}?"
            r"([가-힣A-Za-z0-9() ]{2,30}?)\s*(?:\d{1,2}\s*\.|위탁|$)")
    if to:
        parts.append(f"상대방: {to}")
    return parts


def _prospectus_corr_lines(txt: str) -> list[str]:
    """투자설명서 정정신고(보고) — 펀드 정기갱신/운용역 변경 류 (미파싱-3).
    서류명·최초제출일·사유·위험지표 변경(첫→끝)·운용역 변경. 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    doc = _g(r"정정대상\s*공시서류[^가-힣]{0,6}?"
             r"([가-힣A-Za-z0-9()· ]{2,40}?)\s*(?:\d|2\s*\.|$)")
    if doc:
        parts.append(f"서류: {doc}")
    d0 = _g(r"최초제출일[^0-9]{0,8}?(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    if d0:
        parts.append(f"최초제출: {_clean_kdate(d0)}")
    why = _g(r"((?:자본시장과[\s\S]{0,50}?)?정기\s*갱신"
             r"(?:\s*\([가-힣 ,등]{2,24}\))?|운용역\s*변경)")
    if why:
        why = re.sub(r"\s+", " ", why)[:50]
        parts.append(f"사유: {why}")
    # 위험지표(표준편차/VaR) 첫→끝 변경 병기
    vals = re.findall(r"(?:표준편차|VaR)\s*[::]?\s*([\d.]+)\s*%", txt)
    if len(vals) >= 2 and vals[0] != vals[-1]:
        parts.append(f"위험지표: {vals[0]}% → {vals[-1]}%")
    mm = re.search(r"운용역\s*변경\s+([가-힣]{2,5})\s+([가-힣]{2,5})", txt)
    if mm:
        parts.append(f"운용역: {mm.group(1)} → {mm.group(2)}")
    return parts


def _related_collateral_lines(txt: str) -> list[str]:
    """특수관계인에 대한 담보제공 (공정거래법 제26조 — 미파싱-4 2건: 주식
    담보물(어센틱브랜즈) / 부동산 담보물(엘에스알스코)). 거래상대방·채권자·
    담보물·기간·한도·금액·조건. 단위 백만원 가능 → mult 적용. 순수."""
    mult = _doc_unit_mult(txt)
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    cp = _g(r"거래상대방?[^가-힣A-Za-z0-9(]{0,8}?"
            r"([가-힣A-Za-z0-9()㈜ ]{2,40}?)\s*(?:회사와의|가\s*\.|\d\s*\.|$)")
    if cp:
        parts.append(f"거래상대: {cp}")
    cred = _g(r"채권자[^가-힣A-Za-z0-9(]{0,8}?"
              r"([가-힣A-Za-z0-9()㈜ ]{2,40}?)\s*(?:다\s*\.|담보물|\d\s*\.|$)")
    if cred:
        parts.append(f"채권자: {cred}")
    # 담보물 값은 면적(60,955.8㎡) 소수점 포함 → stop 은 다음 라벨(담보기간)만
    obj = _g(r"담보물[^가-힣A-Za-z0-9(]{0,8}?"
             r"([가-힣A-Za-z0-9()㈜,.㎡\- ]{4,80}?)\s*(?:라\s*\.\s*)?담보기간")
    if obj:
        parts.append(f"담보물: {obj}")
    # 날짜 범위(2026.06.13~2027.06.13) 또는 자유 텍스트(채무전액 상환시 까지)
    period = _g(r"담보기간[^0-9가-힣]{0,8}?"
                r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2}\s*~\s*\d{4}[.\-]\d{1,2}[.\-]\d{1,2}"
                r"|[가-힣][가-힣 ]{2,28}?(?:까지|상환시\s*까지))")
    if period:
        parts.append(f"기간: {period.strip()}")
    lim = _g(r"담보한도[^0-9]{0,12}?([\d,]{2,})")
    amt = _g(r"담보금액[^0-9]{0,12}?([\d,]{2,})")
    seg = []
    if lim and _amt_won(lim, mult):
        seg.append(f"한도 {_amt_won(lim, mult)}")
    if amt and _amt_won(amt, mult):
        seg.append(f"금액 {_amt_won(amt, mult)}")
    if seg:
        parts.append("담보: " + " · ".join(seg))
    cond = _g(r"거래의?\s*조건[^가-힣A-Za-z0-9]{0,8}?"
              r"([가-힣A-Za-z0-9()㈜,.% ]{6,80}?)\s*(?:\d\s*\.|이사회|$)")
    if cond:
        parts.append(f"조건: {cond}")
    return parts


def _capital_reduction_lines(txt: str) -> list[str]:
    """감자 결정 (미파싱-4 — 무상감자 결손보전). 감자주식수·비율·자본금
    전후·방법·사유·기준일·주총. 정정 래퍼면 헤더 + 변경 항목. 순수."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    n = _g(r"감자주식의?\s*종류와?\s*수[\s\S]{0,12}?보통주식?\s*\(주\)[^0-9]{0,10}?"
           r"([\d,]{4,})")
    rate = _g(r"감자비율[\s\S]{0,12}?보통주식?\s*\(%\)[^0-9]{0,10}?([\d.]+)")
    if n:
        seg = f"감자주식: {int(n.replace(',', '')):,}주"
        if rate:
            seg += f" (비율 {rate}%)"
        parts.append(seg)
    cb = _g(r"감자전\s*\(원\)[^0-9]{0,10}?([\d,]{6,})")
    ca = _g(r"감자후\s*\(원\)[^0-9]{0,10}?([\d,]{6,})")
    if cb and ca:
        from bot.dart_detail import _won
        parts.append(f"자본금: {_won(cb)} → {_won(ca)}")
    why = _g(r"감자사유[^가-힣A-Za-z0-9]{0,10}?"
             r"([가-힣A-Za-z0-9() ]{2,50}?)\s*(?:\d\s*\.|감자일정|$)")
    if why:
        parts.append(f"사유: {why}")
    base = _g(r"감자기준일[^0-9]{0,10}?"
              r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    if base:
        parts.append(f"감자기준일: {_clean_kdate(base)}")
    gm = _g(r"주주총회\s*예정일[^0-9]{0,10}?"
            r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    if gm:
        parts.append(f"주총예정: {_clean_kdate(gm)}")
    return parts


def _issue_price_lines(txt: str) -> list[str]:
    """[안내] 유상증자 신주 발행가액 (미파싱-4 — 1차 발행가액 27,900원).
    주당 발행가액 + 확정 공시예정일 (긴 산정방식 narrative 는 생략). 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    kind = _g(r"\d\s*\.\s*구\s*분[^가-힣A-Za-z0-9]{0,8}?"
              r"([가-힣A-Za-z0-9 ]{4,40}?)\s*(?:\d\s*\.|주당|$)")
    if kind:
        parts.append(f"구분: {kind}")
    pr = _g(r"주당\s*발행가액[\s\S]{0,16}?보통주식?\s*\(원\)[^0-9]{0,10}?([\d,]{3,})")
    if pr:
        parts.append(f"주당 발행가액: {int(pr.replace(',', '')):,}원")
    disc = _g(r"할인율\s*([\d.]+)\s*%")
    if disc:
        parts.append(f"할인율: {disc}%")
    fin = _g(r"확정\s*발행가액[^0-9]{0,40}?"
             r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})\s*일?\s*에\s*공시")
    if fin:
        parts.append(f"확정가액 공시예정: {_clean_kdate(fin)}")
    return parts


def _majorstock_doc_lines(txt: str) -> list[str]:
    """대량보유상황보고서 원문 요약정보 폴백 (미파싱-7 웰크론 — majorstock
    구조화 API 미매칭/lag 케이스). 발행회사·관계/직전→이번 보유비율/사유.
    순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    co = _g(r"발행회사명[^가-힣A-Za-z(]{0,8}?"
            r"([가-힣A-Za-z0-9()㈜ ]{2,30}?)\s*(?:발행회사와|보고구분|$)")
    rel = _g(r"발행회사와의\s*관계[^가-힣]{0,8}?([가-힣 ]{2,12}?)\s*(?:보고구분|$)")
    if co:
        parts.append(f"발행회사: {co}" + (f" ({rel})" if rel else ""))
    m = re.search(r"직전\s*보고서[^0-9]{0,12}?([\d,]{4,})\s+([\d.]+)"
                  r"[\s\S]{0,30}?이번\s*보고서[^0-9]{0,12}?([\d,]{4,})\s+([\d.]+)",
                  txt)
    if m:
        try:
            b, a = float(m.group(2)), float(m.group(4))
            arrow = "▲" if a > b else ("▼" if a < b else "–")
            parts.append(f"지분율: {b:.2f}% → {a:.2f}% ({a - b:+.2f}%p {arrow})")
            parts.append(f"보유주식: {m.group(1)} → {m.group(3)}주")
        except ValueError:
            pass
    why = _g(r"보고사유[^가-힣]{0,8}?([가-힣A-Za-z0-9 ,·]{4,40}?)\s*(?:※|$)")
    if why:
        parts.append(f"사유: {why}")
    return parts


def _major_holding_change_lines(txt: str) -> list[str]:
    """최대주주 등의 주식보유 변동 (공정거래법 — report_nm '최대주주등소유주식
    변동신고서' / doc 제목 '최대주주 등의 주식보유 변동'). 첫(최대) 주주의 관계·
    변동전→변동후 지분율 + 증감(희석/집중/전량처분). 순수. 라이브 5예시 2026-
    06-13: 계열회사 희석(고흥나로)·친족 집중(애나그램)·전량 이전(메이케이/메이
    엑스지, 변동후 '-'=0). 관계 열을 계열회사 단일→전 관계로 broaden + '-' 처리."""
    parts: list[str] = []
    # 주주명 = 관계 열(계열회사/친족/본인/임원/특수관계인) 직전 단일 토큰
    # (+(주)/㈜) — 헤더 prefix '동일인 및 …' 오캡처 차단(공백 포함 prefix 배제)
    mn = re.search(r"([가-힣A-Za-z0-9㈜]{2,20}(?:\([가-힣A-Za-z]{1,4}\))?)"
                   r"\s*(?:계열회사|친족|본인|임원|특수관계인)", txt)
    rel = re.search(r"(?:계열회사|친족|본인|임원|특수관계인)", txt)
    # 보통주 행: 변동전수 변동전율 [증감수] 증감율 변동후수 변동후율 ('-'=0)
    mr = re.search(r"보통주\s+([\d,]+|-)\s+([\d.]+|-)\s+(\S+)\s+(-?[\d.]+|-)\s+"
                   r"([\d,]+|-)\s+([\d.]+|-)", txt)

    def _f(s):
        return 0.0 if s in ("-", "–") else float(s.replace(",", ""))

    if mn and mr:
        try:
            before, after = _f(mr.group(2)), _f(mr.group(6))
            delta = (_f(mr.group(4)) if mr.group(4) not in ("-", "–")
                     else after - before)
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "–")
            who = mn.group(1).strip()
            if rel:
                who += f"({rel.group(0)})"
            parts.append(f"주주: {who}")
            note = " · 전량 처분" if after == 0 and before > 0 else (
                   " · 신규" if before == 0 and after > 0 else "")
            parts.append(f"지분율: {before:.2f}% → {after:.2f}% "
                         f"({delta:+.2f}%p {arrow}){note}")
        except ValueError:
            pass
    return parts


def _suspension_release_lines(txt: str) -> list[str]:
    """주권매매거래정지해제 (미파싱-6 — 글로벌에스엠테크 액면병합 변경상장).
    대상종목·해제사유·해제일시. 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    sym = _g(r"대상종목[^가-힣A-Za-z0-9(]{0,8}?"
             r"([가-힣A-Za-z0-9()㈜ ]{2,40}?)\s*(?:보통주|\d\s*\.|해제|$)")
    if sym:
        parts.append(f"대상: {sym}")
    why = _g(r"해제사유[^가-힣A-Za-z0-9]{0,8}?"
             r"([가-힣A-Za-z0-9() ]{2,40}?)\s*(?:\d\s*\.|해제일시|$)")
    if why:
        parts.append(f"해제사유: {why}")
    d = _g(r"해제일시[^0-9]{0,10}?(\d{4}-\d{1,2}-\d{1,2}(?:\s*\d{1,2}:\d{2})?)")
    if d:
        parts.append(f"해제일시: {d}")
    return parts


def _tender_result_lines(txt: str) -> list[str]:
    """공개매수의 결과 (미파싱-6 — 세아홀딩스 자사주 소각용 공개매수). 대상
    회사·매수가격·예정/응모/매수수량·기간·공개매수 후 보유비율. 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    co = _g(r"공개매수\s*대상\s*회사명[^가-힣A-Za-z0-9(]{0,8}?"
            r"([가-힣A-Za-z0-9()㈜ ]{2,40}?)\s*(?:\d\s*\.|공개매수|$)")
    if co:
        parts.append(f"대상: {co}")
    pr = _g(r"매수가격[^0-9]{0,12}?주당\s*([\d,]{3,})\s*원")
    if pr:
        parts.append(f"매수가: 주당 {int(pr.replace(',', '')):,}원")
    plan = _g(r"매수예정수량[^0-9]{0,12}?(?:최대\s*)?([\d,]{2,})\s*주")
    sub = _g(r"응모주식\s*수[^0-9]{0,12}?([\d,]{2,})\s*주")
    bought = _g(r"매수주식\s*수[^0-9]{0,12}?([\d,]{2,})\s*주")
    seg = []
    if plan:
        seg.append(f"예정 {int(plan.replace(',', '')):,}주")
    if sub:
        seg.append(f"응모 {int(sub.replace(',', '')):,}주")
    if bought:
        seg.append(f"매수 {int(bought.replace(',', '')):,}주")
    if seg:
        parts.append("수량: " + " · ".join(seg))
    rate = _g(r"공개매수\s*후\s*(?:주식등의\s*)?보유비율[^0-9]{0,16}?([\d.]+)")
    if rate:
        parts.append(f"공개매수 후 보유: {rate}%")
    return parts


def _major_change_lines(txt: str) -> list[str]:
    """최대주주 변경 (미파싱-6 — 은홀딩파트너스→임주주 주식매매계약). 변경전→
    변경후 최대주주(명·비율) + 변경사유. 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    # 변경내용 표: 변경전 최대주주명 ... 소유비율(%) X ; 변경후 ... Y
    bn = _g(r"변경전[\s\S]{0,16}?최대주주명?[^가-힣A-Za-z0-9(]{0,8}?"
            r"([가-힣A-Za-z0-9()㈜ ]{2,30}?)\s*(?:소유주식|\d\s*\.|변경후|$)")
    br = _g(r"변경전[\s\S]{0,90}?소유비율\s*\(%\)[^0-9]{0,8}?([\d.]+)")
    an = _g(r"변경후[\s\S]{0,16}?최대주주명?[^가-힣A-Za-z0-9(]{0,8}?"
            r"([가-힣A-Za-z0-9()㈜ ]{2,30}?)\s*(?:소유주식|\d\s*\.|$)")
    ar = _g(r"변경후[\s\S]{0,90}?소유비율\s*\(%\)[^0-9]{0,8}?([\d.]+)")
    if bn or an:
        b = f"{bn}({br}%)" if bn and br else (bn or "")
        a = f"{an}({ar}%)" if an and ar else (an or "")
        parts.append(f"최대주주: {b} → {a}")
    why = _g(r"변경사유[^가-힣A-Za-z0-9]{0,8}?"
             r"([가-힣A-Za-z0-9() ]{4,50}?)\s*(?:\d\s*\.|실권주|지분|$)")
    if why:
        parts.append(f"사유: {why}")
    return parts


def _confirmation_lines(txt: str) -> list[str]:
    """확인서 (미파싱-5 — 대표이사·신고업무담당이사 기재내용 확인 + 내부
    회계관리제도). 정형 첨부 문서라 정보 밀도 낮음 — 회사명 + 요지 2줄.
    순수. ⚠️ 수집 정책 무변경(기타→drop) — 흘러드는 경우만 파싱."""
    parts: list[str] = []
    m = re.search(r"((?:주식회사|㈜)\s*[가-힣A-Za-z0-9]{2,20}|"
                  r"[가-힣A-Za-z0-9]{2,20}\s*(?:주식회사|㈜))\s*대표이사", txt)
    if m:
        parts.append(f"회사: {m.group(1).strip()}")
    if "기재내용" in txt or "기재사항" in txt:
        seg = "대표이사·신고업무담당이사 기재내용 확인"
        if "내부회계관리제도" in txt:
            seg += " · 내부회계관리제도 운영"
        parts.append(seg)
    return parts


def _asset_complete_lines(txt: str) -> list[str]:
    """유형자산 양수도 종료보고서 (미파싱-5 — 에어레인→SK에어코어 청주공장).
    양도인/양수인/양도금액 + 거래 종료(매매대금 정산·소유권이전 완료). 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    # 표 행만 — 본문 prose '당사(양도인)와 …(양수인)' 오캡처 차단(lookbehind)
    seller = _g(r"(?<![(가-힣])양도인\s+"
                r"((?:주식회사|㈜)?\s*[가-힣A-Za-z0-9()㈜ ]{2,36}?)\s*양수인")
    buyer = _g(r"(?<![(가-힣])양수인\s+"
               r"((?:주식회사|㈜)?\s*[가-힣A-Za-z0-9()㈜ ]{2,36}?)\s*(?:양도\s*금액|\d\s*\.|$)")
    if seller or buyer:
        parts.append(" · ".join(x for x in (
            f"양도인 {seller}" if seller else None,
            f"양수인 {buyer}" if buyer else None) if x))
    amt = _g(r"양도\s*금액[^0-9]{0,12}?([\d,]{6,})\s*원")
    if amt:
        from bot.dart_detail import _won
        parts.append(f"양도금액: {_won(amt) or amt + '원'}")
    if "소유권이전" in txt or "매매대금" in txt or "종료" in txt:
        parts.append("거래 종료 (매매대금 정산·소유권이전 완료)")
    return parts


def _merger_lines(txt: str) -> list[str]:
    """회사합병 결정 (미파싱-5 — 네오위즈→뮤즈라이브 100% 자회사 흡수합병).
    합병방법/비율/상대회사/합병기일·주총·합병등기 예정. 순수."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    cp = _g(r"(?:합병상대회사|소멸회사|존속회사)[\s\S]{0,12}?회사명[^가-힣A-Za-z0-9(]{0,8}?"
            r"([가-힣A-Za-z0-9()㈜ ]{2,30}?)\s*(?:주요사업|소재지|\d\s*\.|$)")
    method = "흡수합병" if "흡수합병" in txt else ("분할합병" if "분할합병" in txt else None)
    seg = "합병방법: " + (method or "합병")
    if cp:
        seg += f" (소멸 {cp})"
    if method:
        parts.append(seg)
    rt = _g(r"합병비율[^0-9]{0,12}?(\d[\d,.]*\s*[:：]\s*\d[\d,.]*)")
    if rt:
        parts.append(f"합병비율: {rt.replace('：', ':')}")
    md = _g(r"합병기일[^0-9]{0,12}?"
            r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    gm = _g(r"주주총회\s*(?:예정일|일자)[^0-9]{0,12}?"
            r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    rd = _g(r"합병등기\s*예정일[^0-9]{0,12}?"
            r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2})")
    sched = " · ".join(x for x in (
        f"합병기일 {_clean_kdate(md)}" if md else None,
        f"주총 {_clean_kdate(gm)}" if gm else None,
        f"합병등기 {_clean_kdate(rd)}" if rd else None) if x)
    if sched:
        parts.append(f"일정: {sched}")
    return parts


def _business_transfer_lines(txt: str) -> list[str]:
    """영업양도 결정 (공정거래법 — 미파싱-5 SK/엔솔브 PV·ESS 운영사업).
    양도영업/양도가액/목적/양수법인/재무내용(백만원). 순수."""
    parts: list[str] = []

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    biz = _g(r"\d\s*\.\s*양도영업[^가-힣A-Za-z0-9(]{0,8}?"
             r"([가-힣A-Za-z0-9()· ]{4,50}?)\s*(?:\d\s*\.|양도영업\s*주요|$)")
    if biz:
        parts.append(f"양도영업: {biz}")
    amt = _g(r"양도가액\s*\(?원?\)?[^0-9]{0,12}?([\d,]{6,})")
    if amt:
        from bot.dart_detail import _won
        parts.append(f"양도가액: {_won(amt) or amt + '원'}")
    buyer = _g(r"양수법인\s*\([^)]{0,20}\)?[^가-힣A-Za-z0-9(]{0,8}?"
               r"([가-힣A-Za-z0-9()㈜ ]{2,30}?)\s*(?:\(|\d\s*\.|$)")
    if buyer:
        parts.append(f"양수법인: {buyer}")
    why = _g(r"\d\s*\.\s*양도목적[^가-힣A-Za-z0-9]{0,8}?"
             r"([가-힣A-Za-z0-9 ]{2,40}?)\s*(?:\d\s*\.|양도예정|$)")
    if why:
        parts.append(f"목적: {why}")
    return parts


def _custody_lines(txt: str) -> list[str]:
    """부동산투자회사 자산보관 위탁계약 체결 (리츠 — 미파싱-2 예시 2건:
    신영부동산신탁 정정 연장 / 한국토지신탁 오렌지센터). 보관기관·대상
    부동산·기간·계약일자. 순수."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    org = _g(r"회사명[^가-힣A-Za-z(]{0,8}?"
             r"([가-힣A-Za-z(),.· ]{2,50}?)\s*(?:자본금|\d{1,2}\s*\.\s|$)")
    if org:
        parts.append(f"보관기관: {org}")
    prop = _g(r"대상\s*부동산[^가-힣A-Za-z0-9(]{0,8}?(?:은|는|[-–])?\s*"
              r"([가-힣A-Za-z0-9()\s,.\-]{4,60}?)\s*(?:입니다|\d\s*\.\s|$)")
    if prop:
        prop = re.sub(r"^[\s\-–—·]+", "", prop)   # 선두 '- ' strip
        parts.append(f"대상 부동산: {prop}")
    # 마지막 출현 = 본문(정정 래퍼의 '정정전' stale 값이 앞에 옴 — 신영
    # 연장 정정 예시, 회생 제출기한과 동일 클래스)
    ss = re.findall(r"계약기간[\s\S]{0,12}?시작일[^0-9]{0,8}?"
                    r"(\d{4}-\d{1,2}-\d{1,2})", txt)
    es = re.findall(r"계약기간[\s\S]{0,60}?종료일[^0-9]{0,8}?"
                    r"(\d{4}-\d{1,2}-\d{1,2})", txt)
    if ss:
        parts.append(f"기간: {ss[-1]} ~ {es[-1]}" if es else f"기간: {ss[-1]} ~")
    d = _g(r"계약일자[^0-9]{0,8}?(\d{4}-\d{1,2}-\d{1,2})")
    if d:
        parts.append(f"계약일자: {d}")
    return parts


def _extract_custody(rcept_no: str, api_key: str) -> dict | None:
    """자산보관 위탁계약 — fetch 후 _custody_lines. 2미만 12h 쿨다운."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    parts = _custody_lines(txt)
    if len(parts) < 2:
        _doc_fail_mark(rcept_no, hours=12.0)
        return None
    return {"lines": parts}


def _ir_lines(txt: str) -> list[str]:
    """기업설명회(IR) 개최 원문 → 카드 lines (순수 — 단위테스트).

    표준 양식(사용자 2026-06-13 '다 비슷할거야', 1예시): 1.일시(행사일
    시작/종료 + 시간) 2.장소 3.대상자 4.실시목적 5.실시방법 6.주요내용
    7.후원기관 8.개최확정일. 핵심만 발췌 — 일시(날짜·시간), 장소, 목적,
    대상, 후원기관. 옛 정책(IR=제목만 캘린더 전담)에서 파싱 추가."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else None

    # 일시 — 시작일(+종료일 다르면 ~종료일) + 시작/종료 시간. 표 구조라
    # '시작일 종료일 시작시간 종료시간 2026-06-16 2026-06-16 15:00 18:00'.
    dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", txt)
    times = re.findall(r"\b(\d{1,2}:\d{2})\b", txt)
    sd = _g(r"행사일[\s\S]{0,40}?(\d{4}-\d{1,2}-\d{1,2})") or (dates[0] if dates else None)
    if sd:
        # 종료일 = 시작일 다음 날짜가 다르면 범위
        ed = None
        try:
            after = txt.split(sd, 1)[1]
            m2 = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", after)
            if m2 and m2.group(1) != sd:
                ed = m2.group(1)
        except Exception:
            ed = None
        when = sd + (f"~{ed}" if ed else "")
        if times:
            when += f" {times[0]}" + (f"~{times[1]}" if len(times) > 1 else "")
        parts.append(f"일시: {when}")
    for lbl, key in (("장소", r"\d\s*\.\s*장\s*소"),
                     ("대상", r"\d\s*\.\s*대상자?"),
                     ("목적", r"실시\s*목적"),
                     ("방법", r"실시\s*방법"),
                     ("내용", r"주요\s*내용"),
                     ("후원", r"후원\s*기관")):
        v = _g(key + r"[^가-힣A-Za-z0-9'\"(]{0,8}?"
               r"([가-힣A-Za-z0-9'\"()&.,·\-~ ]{2,60}?)\s*(?:\d{1,2}\s*\.|$)")
        if v and v not in ("-", "해당없음", "해당사항없음"):
            parts.append(f"{lbl}: {v[:60]}")
    return parts


def _extract_ir(rcept_no: str, api_key: str) -> dict | None:
    """기업설명회(IR) — 원문 fetch 후 _ir_lines. 2미만 12h 쿨다운."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    parts = _ir_lines(txt)
    if len(parts) < 2:
        _doc_fail_mark(rcept_no, hours=12.0)
        return None
    return {"lines": parts}


def _inquiry_lines(txt: str) -> list[str]:
    """조회공시/풍문·보도 해명 원문 → 카드 lines (순수 — 단위테스트).

    사용자 5예시 (2026-06-12 '이제 조회공시'): 요구형(제목·요구일시·답변
    시한 — 가처분 결정설), 풍문해명형(보도·매체·요지 — STX그린로지스/
    한화엔진), 시황변동 답변형(제목·요지·재공시 — 대구백화점/핀텔 정정).
    요지는 결정적 마커 문장 발췌 — 추진(선정/진행중/검토중) + 입장(사실
    아님/미확정) 분리, LLM 0."""
    parts: list[str] = []
    corr = _correction_header(txt)
    if corr:
        parts.append(corr)

    def _g(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    # 라벨-값 사이 gap 은 greedy — ' : ' 구분자형(KG파이낸셜 최종예시)에서
    # 캡처가 ': 제목텍스트' 로 시작하는 이중 콜론 차단 (값 문자는 gap
    # 클래스에서 제외라 과식 불가).
    title = _g(r"\d\s*\.\s*제\s*목[^가-힣A-Za-z0-9(\[]{0,12}"
               r"([\s\S]{4,70}?)\s*\d{1,2}\s*\.\s")
    if title:
        parts.append(f"제목: {title}")
    rep = _g(r"보도의\s*내용[^가-힣A-Za-z0-9\[('‘]{0,10}"
             r"([\s\S]{4,80}?)\s*\d{1,2}\s*\.\s*풍문")
    media = _g(r"보도의\s*매체[^가-힣A-Za-z0-9]{0,10}?"
               r"([가-힣A-Za-z0-9,· ]{2,30}?)\s*(?:\d{1,2}\s*\.\s|$)")
    if rep:
        parts.append(f"보도: {rep}" + (f" ({media})" if media else ""))
    # 일시 = 날짜 + 선택적 (오전|오후) + 선택적 HH:MM — 'YYYY-MM-DD 오후
    # 12:00까지' 변형(KG파이낸셜 2026-06-12 최종예시)까지 한 패턴으로.
    _DT = (r"(\d{4}-\d{1,2}-\d{1,2}(?:\s*(?:오전|오후))?"
           r"(?:\s*\d{1,2}:\d{2}(?:\s*까지)?)?)")
    ask = _g(r"요구일시[^0-9]{0,10}?" + _DT)
    if ask:
        parts.append(f"요구일시: {ask}")
    due = _g(r"답변시한[^0-9]{0,10}?" + _DT)
    if due:
        parts.append(f"답변시한: {due}")
    # 요지 — 추진 상황(무엇을 하고 있나) + 회사 입장(확정/부인) 분리 발췌.
    # 선두 불릿('- ') strip + lookback 90 (장문 문장 중간 잘림 완화).
    def _cl(v: str | None) -> str | None:
        if not v:
            return None
        v = re.sub(r"^[\s\-–—·:]+", "", v)
        # 라벨 인접 캡처('해명내용 - 언론에…') — 선두 16자 내 불릿 경계 컷
        return re.sub(r"^[^\-–—]{0,16}?[\-–—]\s+", "", v)

    prog = _cl(_g(r"([^.\n]{8,90}(?:선정하였|선정되었|진행\s*중|검토하고\s*있)"
                  r"[^.\n]{0,60})"))
    if prog:
        parts.append(f"추진: {prog}")
    stance = _cl(_g(r"([^.\n]{0,90}(?:사실이\s*아님|결정된\s*바는?\s*없"
                    r"|확정된\s*사항은?\s*없|확정되지\s*않았)[^.\n]{0,40})"))
    if stance:
        parts.append(f"입장: {stance}")
    askday = _g(r"조회공시요구일[^0-9]{0,10}?(\d{4}-\d{1,2}-\d{1,2})")
    ansday = _g(r"조회공시답변일[^0-9]{0,10}?(\d{4}-\d{1,2}-\d{1,2})")
    if askday or ansday:
        parts.append(" · ".join(x for x in (
            f"요구일 {askday}" if askday else None,
            f"답변일 {ansday}" if ansday else None) if x))
    redo = _g(r"재공시\s*(?:예정일|기한)[^0-9]{0,16}?(\d{4}-\d{1,2}-\d{1,2})")
    if redo:
        parts.append(f"재공시: {redo}")
    return parts


def _extract_inquiry(rcept_no: str, api_key: str) -> dict | None:
    """조회공시/풍문해명 — 원문 fetch 후 _inquiry_lines. 2미만 12h 쿨다운."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    parts = _inquiry_lines(txt)
    if len(parts) < 2:
        _doc_fail_mark(rcept_no, hours=12.0)
        return None
    return {"lines": parts}


# 생산재개/중단 (자율공시 — 리스크-2 안국약품 화성공장) — 겸용 필드셋
_PRODUCTION_FIELDS = [
    ("내용",
     r"생산(?:재개|중단)\s*내용[^가-힣A-Za-z0-9(]{0,8}?"
     r"([가-힣A-Za-z0-9()㈜ ]{2,40}?)\s*(?:\d{1,2}\s*\.|생산)", "text"),
    ("매출액대비", r"매출액\s*대비\s*\(%\)[^0-9]{0,8}?([\d.]+)", "pct"),
    ("사업",
     r"생산(?:재개|중단)\s*사업[^가-힣A-Za-z0-9]{0,8}?"
     r"([가-힣A-Za-z0-9 ]{2,40}?)\s*(?:\d{1,2}\s*\.|$)", "text"),
    ("사유",
     r"생산(?:재개|중단)\s*사유[^가-힣A-Za-z0-9]{0,8}?"
     r"([가-힣A-Za-z0-9() ]{2,50}?)\s*(?:\d{1,2}\s*\.|생산|$)", "text"),
    ("일자",
     r"생산(?:재개|중단)\s*일자[^0-9]{0,8}?(\d{4}-\d{1,2}-\d{1,2})", "text"),
    ("중단기간",
     r"생산중단기간[^0-9]{0,6}?(\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2}\s*~\s*"
     r"\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2})", "text"),
]


def _parse_rehab(rcept_no: str, api_key: str) -> dict | None:
    """회생절차 — 원문 fetch 후 _rehab_lines. 필드 2미만이면 12h 쿨다운."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    parts = _rehab_lines(txt)
    if len(parts) < 2:
        _doc_fail_mark(rcept_no, hours=12.0)
        return None
    return {"lines": parts}


# ── 소송 — 사용자 제공 5양식 기준 (2026-06-12 '소송 5개 우선, 예시로').
# 표준 양식 2종: 제기·신청(경영권 분쟁 등) / 판결·결정(일정금액 이상의
# 청구). ㆍ(U+318D)·· 변형 허용. min_fields=2.
_LAWSUIT_FILED_FIELDS = [
    ("사건",
     r"사건의\s*명칭[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()ㆍ·,\- ]{2,60}?)\s*(?:사건\s*번호|\d{1,2}\.\s|$)", "text"),
    ("사건번호",
     r"사건\s*번호[^0-9]{0,20}?(\d{4}\s*[가-힣]{1,4}\s*\d{1,8})", "text"),
    # ○●(이름 마스킹) 포함 — '권○○' 류 개인 신청인 (미파싱-7 알로이스)
    ("원고",
     r"원고\s*[ㆍ·(]?\s*신청인?\s*\)?[^가-힣A-Za-z0-9]{0,12}?"
     r"([가-힣A-Za-z0-9()㈜○●:,·ㆍ\- ]{2,60}?)\s*(?:피고|채무자|\d{1,2}\.\s|\[|$)", "text"),
    # 구분자 1자 이상 필수 — 산문 '피고가 부담한다' 조사 오캡처 차단
    # (머큐리에프엠 추가예시 surfaced), 표 셀 '피고 : (주)X' 만 매칭
    ("피고",
     r"피고[^가-힣A-Za-z0-9]{1,10}?"
     r"([가-힣A-Za-z0-9()㈜○●,·ㆍ\- ]{2,50}?)\s*(?:\d{1,2}\.\s|\[|$)", "text"),
    # 취지 라벨 변형: [청구취지]/[신청취지] 또는 라벨 없이 '청구내용' 직후
    # 번호 목록(나비프라/머큐리에프엠 2026-06-12 추가 5예시)
    # 취지 stop 의 번호목록 감지는 (?<!\d) — 본문 날짜 '2026. 6. 12.' 의
    # '26. ' 를 항번호로 오인해 절단하던 것 차단 (미파싱-7 알로이스)
    # 항번호 stop 은 '다음 문자가 한글/괄호'일 때만 — 본문 날짜
    # '2026. 6. 12.'의 ' 6. '(뒤=숫자)을 항번호로 오인 절단 차단 (미파싱-7)
    ("취지",
     r"(?:[\[(]?\s*(?:청구|신청)\s*취지\s*[\])]?[::]?|청구\s*내용)\s*"
     r"[^가-힣0-9(]{0,8}(?:\(?1\)?\s*[.)]?\s*)?"
     r"([가-힣A-Za-z0-9()ㆍ·,.%:'’‘\- ]{8,160}?)"
     r"(?:\s*(?:\d{1,2}\s*[.)]\s*(?=[가-힣(])|라는|\[|$))", "text"),
    # 일정금액 이상 청구 양식의 청구금액/자기자본대비 (제기·신청에도 존재)
    ("청구금액",
     r"청구\s*금액\s*\(?원?\)?[^0-9자]{0,16}?([\d,]{4,})", "won"),
    ("자기자본대비",
     r"자기자본\s*대비\s*\(?%?\)?[^0-9대]{0,12}?"
     r"(\d{1,3}(?:\.\d{1,2})?)(?![.)\d])", "pct"),
    ("관할법원",
     r"관할\s*법원[^가-힣]{0,12}?"
     r"([가-힣 ]{2,20}?법원(?:\s*[가-힣]{1,6}지원)?)", "text"),
    ("제기일",
     r"제기\s*[ㆍ·]?\s*신청\s*일자[^0-9]{0,20}?"
     r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})", "text"),
]

_LAWSUIT_RULING_FIELDS = [
    ("사건",
     r"사건의\s*명칭[^가-힣A-Za-z0-9]{0,20}?"
     r"([가-힣A-Za-z0-9()ㆍ·,\- ]{2,60}?)\s*(?:사건\s*번호|\d{1,2}\.\s|$)", "text"),
    ("사건번호",
     r"사건\s*번호[^0-9]{0,20}?(\d{4}\s*[가-힣]{1,4}\s*\d{1,8})", "text"),
    # ○(이름 마스킹)·콜론 포함 — 변경등기보류 가처분(디케이엠이) 류
    # '채권자(신청인) : 최○○ ...' 본문 수용
    ("판결",
     r"판결\s*[ㆍ·]?\s*결정\s*내용[^가-힣A-Za-z0-9]{0,16}?"
     r"([가-힣A-Za-z0-9()ㆍ·○●:,\- ]{2,70}?)\s*(?:\d{1,2}\.\s|\[|판결|$)", "text"),
    # [주 문] 블록 첫 문장 (씨씨에스충북 — 내용 셀이 채권자/채무자/주문
    # 구조일 때 판결 필드가 '채권자'에서 끊기는 보완, 미파싱-7)
    ("주문",
     r"\[\s*주\s*문\s*\][^가-힣]{0,8}?"
     r"([가-힣A-Za-z0-9 .,·]{4,70}?)(?:\s*(?<![\d.])\d{1,2}\s*[.)]\s|\s*\[|$)", "text"),
    # 금액 '-' 표기 시 인접 '자기자본(원)' 숫자를 오캡처하지 않게 '자' 차단
    ("판결금액",
     r"판결\s*[ㆍ·]?\s*결정\s*금액\s*\(?원?\)?[^0-9자]{0,16}?([\d,]{4,})", "won"),
    # '-' 표기 시 '대기업여부' 를 건너 섹션번호('5.') 오캡처 금지 — '대' 차단
    # + 값은 온전한 퍼센트 형태만((?![.)\d]) — '5.' 류 절번호 배제)
    ("자기자본대비",
     r"자기자본\s*대비\s*\(?%?\)?[^0-9대]{0,12}?"
     r"(\d{1,3}(?:\.\d{1,2})?)(?![.)\d])", "pct"),
    ("관할법원",
     r"관할\s*법원[^가-힣]{0,12}?"
     r"([가-힣 ]{2,20}?법원(?:\s*[가-힣]{1,6}지원)?)", "text"),
    ("판결일",
     r"판결\s*[ㆍ·]?\s*결정\s*일자[^0-9]{0,20}?"
     r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})", "text"),
]


def _misc_mgmt_lines(txt: str) -> dict | None:
    """기타경영사항(자율공시) 원문 → 소송성 내용이면 {lines, category:'소송'}.

    제목은 catch-all('상장폐지결정 효력정지 가처분 신청' ~ '신규 브랜드
    출시')이라 본문 파싱으로 판별 (사용자 2026-06-12 — 현대사료/세종메디칼
    가처분 예시). 소송 마커([사건] 블록 또는 제목의 소송/가처분/판결/
    효력정지) 없으면 None → 제목만 카드 유지(노이즈 차단). 순수(단위테스트)."""
    def _grab(pat: str) -> str | None:
        m = re.search(pat, txt)
        return m.group(1).strip() if m else None

    title = _grab(r"(?:\d{1,2}\s*\.\s*)?제목[^가-힣A-Za-z0-9]{0,12}?"
                  r"([가-힣A-Za-z0-9()ㆍ·,\- ]{2,60}?)\s*(?:\d{1,2}\.\s|$)")
    # [사건] 블록 또는 '사건명:' 콜론형 (미파싱-7 티에스넥스젠) — 값에
    # [전자] 류 브래킷 허용
    case = _grab(r"(?:\[\s*사건\s*\]|사건명\s*[::])[^가-힣0-9\[]{0,8}?"
                 r"([가-힣A-Za-z0-9\[\] ]{4,60}?)\s*(?:채권자|\[채|$)")
    if not case:
        # 블록/콜론 없는 변형(캐스텍코리아 상고제기) — 본문 인라인
        # '부산고등법원 2025나6026' 형태에서 법원+사건번호 추출
        m = re.search(r"([가-힣]{2,8}(?:고등|지방)?법원)\s+"
                      r"(\d{4}\s*[가-힣]{1,4}\s*\d{1,8})", txt)
        if m:
            case = f"{m.group(1)} {m.group(2)}"
    lawsuit_kw = ("소송", "가처분", "판결", "효력정지", "소제기", "소 제기",
                  "상고", "항소")
    if not (case or (title and any(k in title for k in lawsuit_kw))):
        return None
    parts: list[str] = []
    if title:
        parts.append(f"제목: {title}")
    if case:
        parts.append(f"사건: {case}")
    # [채권자] 블록형 + '채권자:' 콜론형 둘 다 (미파싱-7 티에스넥스젠)
    cred = _grab(r"(?:\[\s*채권자\s*\]|채권자\s*[::])[^가-힣A-Za-z0-9]{0,8}?"
                 r"([가-힣A-Za-z0-9()㈜○● ]{2,40}?)\s*(?:\[|채무자|\d{1,2}\.\s|$)")
    debt = _grab(r"(?:\[\s*채무자\s*\]|채무자\s*[::])[^가-힣A-Za-z0-9]{0,8}?"
                 r"([가-힣A-Za-z0-9()㈜○● ]{2,40}?)\s*(?:\[|\d{1,2}\.\s|$)")
    pd_seg = " · ".join(x for x in (
        f"채권자 {cred}" if cred else None,
        f"채무자 {debt}" if debt else None) if x)
    if pd_seg:
        parts.append(pd_seg)
    d = _grab(r"결정\s*\(?확인\)?\s*일자[^0-9]{0,16}?"
              r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})")
    if d:
        parts.append(f"접수(확인)일: {d}")
    if len(parts) < 2:
        return None
    return {"lines": parts, "category": "소송"}


def _extract_misc_mgmt(rcept_no: str, api_key: str) -> dict | None:
    """기타경영사항(자율공시) — 원문 fetch 후 _misc_mgmt_lines. 소송성
    아니면 None(12h 쿨다운으로 재시도 억제 — 내용은 안 바뀜)."""
    txt = _fetch_doc_text(rcept_no, api_key)
    if not txt:
        return None
    out = _misc_mgmt_lines(txt)
    if out is None:
        _doc_fail_mark(rcept_no, hours=12.0)
    return out


def _numbered_rows_lines(txt: str, max_lines: int = 6) -> list[str]:
    """표준 신고 양식 'N. 라벨 값' 행 generic 발췌 — 전용 파서가 없거나
    변형 양식에 실패했을 때의 최종 폴백 (사용자 2026-06-12 '조금 다른
    건 니가 판단해서' — 새 예시 없이도 카드에 핵심 줄 표시).

    배치에서 학습한 클래스 재사용 (노이즈 차단):
    - 번호 라벨 필수(머리말/주석 오캡처 차단) + 라벨 = 한글 시작 2~24자
    - 다음 항 stop = 번호 + 라벨형 한글 run + 구분자 lookahead — 값 속
      날짜('2026. 6. 12.')의 'N.' 이 stop 으로 오인되지 않음
    - 값: '-'/해당없음 류 스킵, 정보성(숫자 포함) 또는 짧은 텍스트만,
      60자 컷, 최대 6줄. 순수(단위테스트)."""
    _LBL = r"[가-힣][가-힣A-Za-z()%·ㆍ\s]{1,24}?"   # '(%)'·'(원)' 보조 포함
    # 항목 머리 = 숫자(1.) 또는 한글 가나다 enumeration(가. 나. — DART 하위항목
    # 다수가 이 형식, 사용자 2026-06-14 미파싱 변형 대응). 한글은 실제 열거문자
    # (가~하)만 + **앞이 한글이 아닐 때만**(lookbehind) — '입니다. 라벨' 의 '다.'
    # 같은 문장 종결을 enumeration 으로 오인하던 것 차단(test_prose 회귀).
    _PFX = r"(?:\d{1,2}|(?<![가-힣])[가나다라마바사아자차카타파하])"
    _stop = r"(?=" + _PFX + r"\s*\.\s*" + _LBL + r"[\s:：]|$)"
    rows = re.findall(
        _PFX + r"\s*\.\s*(" + _LBL + r")\s*[:：]?\s+"
        r"([\s\S]{1,120}?)\s*" + _stop, txt)
    parts: list[str] = []
    seen: set[str] = set()
    for lbl, val in rows:
        lbl = re.sub(r"\s+", " ", lbl).strip()
        val = re.sub(r"\s+", " ", val).strip(" -–—:·")
        if not lbl or lbl in seen:
            continue
        # 비정보 값 스킵: 빈/해당없음/순수 구분자 + 긴 산문(숫자 없는 40자+)
        if (not val or val in ("-", "해당없음", "해당사항없음", "없음")
                or (len(val) > 40 and not re.search(r"\d", val))):
            continue
        seen.add(lbl)
        parts.append(f"{lbl}: {val[:60]}")
        if len(parts) >= max_lines:
            break
    return parts


def _extract_generic_document(rcept_no: str, api_key: str) -> dict | None:
    """구조화 API 미커버 공시의 generic 원문 추출 — 고정 라벨 매칭 →
    부족하면 번호 항목 폴백(_numbered_rows_lines). 둘 다 빈손이면
    negative-cache(12h 재시도 억제)."""
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
    if len(parts) < 2:
        # 번호 항목 generic 폴백 — 변형/신규 양식도 최소 핵심 줄 확보
        generic = _numbered_rows_lines(txt)
        if len(generic) > len(parts):
            parts = generic
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
    """[기재정정] 공통 — '정정(날짜): 사유' 헤더 라인. 없으면 None.

    표 헤더행('항목 정정사유 정정 전 정정 후') 인접 오캡처 가드 —
    캡처값이 '정정 전/후'·'항목'·'관련 여부'면 다음 출현으로 skip
    (미파싱-3 자기주식처분 정정·투자설명서 정정 surfaced)."""
    if "정정신고" not in txt and "정정사유" not in txt:
        return None
    d = re.search(r"정정일자\s*(\d{4}-\d{2}-\d{2})", txt)
    dd = f"({d.group(1)[2:]})" if d else ""
    for m in re.finditer(r"정정사유\s*[:：]?\s*([가-힣A-Za-z0-9 ,.()·]{2,40}?)"
                         r"\s*(?:\d\.|정정사항|정정관련|$)", txt):
        val = m.group(1).strip()
        if re.search(r"정정\s*[전후]|항\s*목|관련\s*여부", val):
            continue
        return f"정정{dd}: {val}"
    return None


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
    _END = (r"(?:\d{1,2}\.\s|계약금액|계약내역|계약기간|공시유보|시작일|비고"
            r"|[-–]\s*회사와|[A-Za-z0-9]{15,}|$)")   # 유가 표준형 '- 회사와의 관계' 꼬리 컷
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
        # 유가 표준형 '판매ㆍ공급계약 구분'(공사수주 등) 병기 — VLCC 예시
        gu2 = _grab(r"공급계약\s*구분[^가-힣A-Za-z0-9]{0,10}?"
                    r"([가-힣A-Za-z0-9 ]{2,20}?)\s*(?:[-–]|\d{1,2}\s*\.|체결계약명|$)")
        if gu2 and gu2 not in body:
            parts.append(f"구분: {gu2}")
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
    """전용 파서 dispatch + **generic 원문 폴백 일괄 보장** (사용자 2026-06-14
    '미파싱 9개 끝내'). 옛 구조는 전용 분기 일부가 실패 시 early `return None`
    으로 generic 폴백(line 끝 _extract_generic_document)을 건너뛰어 미파싱이
    남았음 — 전환청구권/IR/대량보유/소유상황/분할·병합 등. 래퍼가 어느 분기든
    None/빈 결과면 generic 원문 폴백을 보장(이미 doc text 는 _DOC_TEXT_MEM
    재사용 → 추가 fetch 0). 전용 분기의 category 승격은 보존."""
    r = _extract_detail_specific(report_nm, rcept_no, corp_code, api_key)
    if r and r.get("lines"):
        return r
    g = _extract_generic_document(rcept_no, api_key)
    if g and g.get("lines"):
        if r and r.get("category"):
            g["category"] = r["category"]
        return g
    return r


def _extract_detail_specific(report_nm: str, rcept_no: str, corp_code: str,
                             api_key: str) -> dict | None:
    """주요사항보고서에서 핵심 숫자 추출. None if not applicable."""
    from bot.dart_detail import _won, _pick

    t = report_nm or ""

    # 대량보유 5% 보고서 — majorstock.json(지분공시 종합정보). 주요사항보고서와
    # 응답 구조가 달라 전용 처리: 보고자·지분율 변동(직전→현재, %p)·변동주식·
    # 보고사유. (프로텍·하이브 레퍼런스 카드 대응, 무료.)
    if "대량보유" in t:
        r0 = _extract_majorstock(rcept_no, corp_code, api_key)
        if r0:
            return r0
        # majorstock API 미매칭(일반서식 lag 등, 미파싱-7 웰크론) → 원문
        # 요약정보 표 폴백 (발행회사/관계/직전→이번 비율/보고사유)
        txt0 = _fetch_doc_text(rcept_no, api_key)
        if txt0:
            parts0 = _majorstock_doc_lines(txt0)
            if len(parts0) >= 2:
                return {"lines": parts0}
            _doc_fail_mark(rcept_no, hours=2.0)
        return None
    # 임원ㆍ주요주주 소유상황 — elestock 구조화 (지분공시 전체 파싱,
    # 사용자 2026-06-12). 옛 '대량보유만' 정책 폐기.
    if "소유상황" in t:
        return _extract_elestock(rcept_no, corp_code, api_key)

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
        if not doc and "영업(잠정)실적" in t:
            # 금액표 전부 '-' 인 월간 판매물량 변형 (도시가스 천톤/완성차
            # 대수 — 2026-06-12 '파싱안된것들')
            doc = _parse_volume(rcept_no, api_key)
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
    elif ("자기주식" in t and "취득" in t and "신탁" not in t
            and "처분" not in t):
        # 표준 표 원문 우선 (미파싱-2 — 구조화 API 7일 윈도 밖이면 비던
        # 케이스). 실패 시 아래 specs(tsstkAqDecsn) 경로로 폴스루.
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _BUYBACK_DOC_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "자기주식" in t and "처분" in t and "신탁" not in t:
        # 처분 결정 표준 표 원문 우선 (미파싱-3) — 기재정정 래퍼의 stale
        # 처분기간은 마지막 출현으로 보정. 실패 시 specs(tsstkDpDecsn) 폴스루.
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _disposal_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
    elif "주주명부폐쇄" in t or ("기준일" in t and "배당" in t):
        doc = _parse_div_record(rcept_no, api_key, t)
        if doc:
            return doc
    elif "발행가액" in t:
        # 신주 발행가액 안내 (미파싱-4) — 유상증자보다 먼저 (제목에 '유상
        # 증자' 동반 가능, 발행가액 안내는 별개 양식이라 우선 라우팅).
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _issue_price_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
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
        # 분할 철회 정정(미파싱-5) 우선 — 본문에 '철회' 있으면 철회+사유
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2 and "철회" in txt2:
            parts: list[str] = []
            corr = _correction_header(txt2)
            if corr:
                parts.append(corr)
            kind = ("물적분할" if "물적분할" in txt2
                    else ("인적분할" if "인적분할" in txt2 else "회사분할"))
            parts.append(f"{kind} 철회")
            mw = re.search(r"철회\s*사유[^가-힣A-Za-z0-9]{0,8}?"
                           r"([가-힣A-Za-z0-9() .,]{4,80}?)\s*(?:\d\s*\.|향후|$)", txt2)
            if mw:
                parts.append(f"사유: {mw.group(1).strip()}")
            if len(parts) >= 2:
                return {"lines": parts}
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _SPLIT_COMPANY_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "종료보고서" in t and ("유형자산" in t or "양수도" in t
                              or "양도" in t or "양수" in t):
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _asset_complete_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "회사합병" in t or ("합병" in t and "분할" not in t):
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _merger_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
        # 실패 시 아래 specs(cmpMgDecsn) 폴스루 (early return 안 함)
    elif "영업양도" in t:
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _business_transfer_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
        # 실패 시 specs(bsnTrfDecsn) 폴스루
    elif "확인서" in t:
        # 정형 첨부(대표이사 확인서) — 수집되면 최소 파싱, 정책상 보통
        # 기타→drop (흘러드는 경우만)
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _confirmation_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=24.0)
    elif "최대주주" in t and ("양수" in t or "양도" in t):
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _MAJOR_TRANSFER_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "특수관계인" in t and "담보" in t:
        # 공정거래법 제26조 특수관계인 담보제공 (미파싱-4) — 백만원 단위·
        # 거래상대방/채권자/담보물 구조가 주요사항보고서 담보와 달라 전용.
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _related_collateral_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "담보" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _COLLATERAL_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "감자" in t:
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _capital_reduction_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    # ── 소송 표준 양식 2종 + 기타경영사항 소송성 (사용자 2026-06-12 5예시) ──
    elif "소송" in t:
        doc = _extract_doc_fields(
            rcept_no, api_key,
            _LAWSUIT_RULING_FIELDS if ("판결" in t or "결정" in t)
            else _LAWSUIT_FILED_FIELDS,
            min_fields=2)
        if doc:
            return doc
    elif "기타경영사항" in t:
        return _extract_misc_mgmt(rcept_no, api_key)
    elif "기업설명회" in t or "IR개최" in t or "IR 개최" in t:
        # IR 파싱 (사용자 2026-06-13) — 옛 '제목만 캘린더 전담' 정책에서
        # 상세(일시·장소·목적·대상·후원) 추가. 캘린더 공급은 그대로.
        return _extract_ir(rcept_no, api_key)
    # ── 리스크 9양식 (사용자 2026-06-12 '리스크 완료') ──
    elif "정지해제" in t or ("거래정지" in t and "해제" in t):
        # 주권매매거래정지해제 (미파싱-6) — 매매거래정지보다 먼저 (정지해제가
        # '매매거래정지' substring 포함). 해제사유·일시.
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _suspension_release_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "매매거래정지" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _SUSPENSION_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "공개매수" in t:
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _tender_result_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "최대주주" in t and "변경" in t:
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _major_change_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif ("주식보유" in t and "변동" in t) or "소유주식변동" in t:
        # 최대주주 등의 주식보유 변동 (공정거래법) — report_nm '주식보유변동'
        # (미파싱-6) + '최대주주등소유주식변동신고서'(소유주식변동, 라이브
        # 2026-06-13 5예시). 같은 doc 제목·표 구조라 동일 파서 라우팅.
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _major_holding_change_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "해산사유" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _DISSOLUTION_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "불성실공시" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _UNFAITHFUL_FIELDS, min_fields=2)
        if doc:
            return doc
    elif "회생절차" in t:
        doc = _parse_rehab(rcept_no, api_key)
        if doc:
            return doc
    elif "기타시장안내" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _MARKET_NOTICE_FIELDS, min_fields=1)
        if doc:
            return doc
    elif "생산재개" in t or "생산중단" in t:
        doc = _extract_doc_fields(rcept_no, api_key,
                                  _PRODUCTION_FIELDS, min_fields=2)
        if doc:
            return doc
    # ── 조회공시/풍문·보도 해명 (사용자 2026-06-12 '이제 조회공시', 5예시) ──
    elif "조회공시" in t or "풍문" in t or "해명" in t:
        doc = _extract_inquiry(rcept_no, api_key)
        if doc:
            return doc
    # ── 공정공시 변형 + 투자판단 (사용자 2026-06-12 '파싱안된것들') ──
    elif "수시공시의무관련" in t:
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _fair_disclosure_lines(txt2)
            if len(parts) >= 2:
                out = {"lines": parts}
                # 공정공시 wrapper 는 제목 기반으론 '실적'이지만 내용이
                # 주주환원정책/배당이면 본문 제목으로 승격 (사용자
                # 2026-06-12 '이건 주주환원 아님? 왜 실적이야' — KG 5사
                # 중장기 주주환원정책 케이스).
                nc = _fair_disclosure_category(parts)
                if nc:
                    out["category"] = nc
                return out
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "장래사업" in t:
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _future_plan_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)
    elif "투자판단" in t or "기술이전" in t:
        doc = _extract_material_mgmt(rcept_no, api_key)
        if doc:
            return doc
    elif "자산보관" in t:
        doc = _extract_custody(rcept_no, api_key)
        if doc:
            return doc
    elif "투자설명서" in t:
        # 펀드 정기갱신/운용역 변경 정정 (미파싱-3) — 수집 정책 변경 없음
        # (피드에 이미 흘러드는 건만 파싱, keep 예외 추가 안 함 — 홍수 방지)
        txt2 = _fetch_doc_text(rcept_no, api_key)
        if txt2:
            parts = _prospectus_corr_lines(txt2)
            if len(parts) >= 2:
                return {"lines": parts}
            _doc_fail_mark(rcept_no, hours=12.0)

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
    elif "타법인" in t and any(k in t for k in ("양수", "취득")):
        # 양식명이 '타법인주식및출자증권취득결정'(취득)으로도 옴 — dispatch 가
        # '양수'만 봐 미파싱이던 것(사용자 2026-06-15 --unparsed-audit). 취득≈양수,
        # 같은 Inh API. 처분도 동일(처분≈양도, Trf).
        specs = [("otcprStkInvscrInhDecsn", [
            ("대상회사", ("iscmp_cmpnm", "cmpnm"), "text"),
            ("양수주식", ("inhstk_cnt", "inh_stk_cnt"), "num"),
            ("양수금액", ("inh_prc", "trf_prc"), "won"),
        ])]
    elif "타법인" in t and any(k in t for k in ("양도", "처분")):
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
    """상장사(6자리 코드) 드롭만 집계 — 비상장/펀드 + 발행·등록 서류(투자
    설명서/일괄신고/증권신고서, 펀드 동류 비대상)는 노이즈라 제외, 실제
    기업 사건 보강후보만 모니터링 (사용자 2026-06-13)."""
    try:
        if not (stock_code and len(stock_code) == 6 and stock_code.isdigit()):
            return
        if _is_noncorp_doc(report_nm):
            return
        # 제목완결 거버넌스(주주총회/자율공시 결과류 등)는 보강후보 아님 —
        # 의도된 title-only 처리 (사용자 2026-06-13 A안)
        if any(k in report_nm for k in _TITLE_ONLY_OK_KW):
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
    dropped_listed: dict[str, int] = {}     # 실제 기업 사건 (보강 후보)
    dropped_noncorp: dict[str, int] = {}    # 발행·등록 서류 (의도된 제외, 펀드 동류)
    dropped_title: dict[str, int] = {}      # 제목완결 거버넌스 (의도된 제외, A안 2026-06-13)
    dropped_other = 0                       # 비상장/펀드 (의도된 제외)
    for it in items:
        cat = it.get("category", "기타")
        if cat != "기타":
            kept[cat] = kept.get(cat, 0) + 1
            continue
        sc = it.get("stock_code") or ""
        nm = it.get("report_nm", "")
        if len(sc) == 6 and sc.isdigit():
            key = _norm_report_nm(nm)
            if _is_noncorp_doc(nm):
                tgt = dropped_noncorp
            elif any(k in nm for k in _TITLE_ONLY_OK_KW):
                tgt = dropped_title             # 주주총회/자율공시 결과류 등 제목완결
            else:
                tgt = dropped_listed
            tgt[key] = tgt.get(key, 0) + 1
        else:
            dropped_other += 1
    return {"total": len(items), "kept": kept,
            "dropped_listed": dropped_listed,
            "dropped_noncorp": dropped_noncorp,
            "dropped_title": dropped_title,
            "dropped_other": dropped_other}


def unparsed_audit(days_back: int = 7) -> dict:
    """아카이브의 **진짜 미파싱**(is_parse_target 인데 의미있는 detail 부재 +
    intended-freeform 제외 — 대시보드 ⚠️ 미파싱 칩과 **동일 판정**) report_nm
    분포 + 샘플 url. '어느 양식이 미파싱인지' 진단해 전용 파서를 추가하기 위함
    (사용자 2026-06-15 '미파싱 쌓임 — 다시 보고 파싱정리'). coverage_audit 은
    '드롭'만 보여줘 미파싱(수집·파싱대상이나 detail 없음)은 안 보였음.
    VM 에서: .venv/bin/python -m bot.dart_feed --unparsed-audit [days]."""
    by_date = load_all_archives(days_back=days_back)
    dist: dict[str, int] = {}
    samples: dict[str, str] = {}      # report_nm → 원문 url (파서 작성 참조)
    total = 0
    for _ds, items in by_date.items():
        for it in items:
            meaningful = [l for l in (it.get("detail") or [])
                          if not str(l).startswith("주요사업:")
                          and "시가총액" not in str(l)]
            if not (is_parse_target(it) and not meaningful):
                continue
            if intended_freeform_unparsed(it.get("report_nm", "")):
                continue              # 의도된 미파싱(noparse — freeform/첨부정정) 제외
            total += 1
            key = _norm_report_nm(it.get("report_nm", ""))
            dist[key] = dist.get(key, 0) + 1
            samples.setdefault(key, it.get("url", ""))
    return {"total": total, "dist": dist, "samples": samples}


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
            # 기타경영사항(자율공시)·투자판단 주요경영사항은 catch-all
            # 제목이라 '기타'지만 수집 유지 — 본문 파싱으로 소송/계약 등
            # 승격 (사용자 2026-06-12 현대사료 가처분/한미 기술이전 예시).
            # 미승격분은 detail 없음 → 대시보드가 숨김(홍수 방지).
            if (skip_routine and category == "기타"
                    and not any(k in report_nm
                                for k in ("기타경영사항", "투자판단"))):
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


# ── 파싱 대상 판정 + 중요 공시 하이라이트 (사용자 2026-06-12) ──
# enrich(상세 추출 시도)와 대시보드(미파싱 카드 색상 구별 / 🔥 중요 배지)가
# **공유하는 단일 레지스트리** — 두 곳이 어긋나면 정상 카드를 미파싱으로
# 오색칠. 전부 ₩0·순수 판정(렌더타임, 과거 카드 소급).
_PARSE_FORCE_KW = ("전환청구권", "주식분할", "주식병합", "액면분할", "액면병합",
                   "신규시설투자", "투자결정", "유형자산", "회사분할", "분할합병",
                   "담보", "기타경영사항", "투자판단")
_PARSE_CATS = ("계약", "자금조달", "주주환원", "신규시설투자", "지분공시",
               "자산양수도", "회사구조", "소송", "리스크",
               "조회공시",   # 2026-06-12 '이제 조회공시' — 요구/답변·해명 파싱
               "배당",       # 2026-06-12 주주환원에서 분리 — 기존 배당 specs 파싱 유지
               "IR")         # 2026-06-13 IR 상세 파싱 (일시·장소·목적·대상·후원)
# 자금조달 중 무료 구조화 소스·원문 파서가 없는 유형(제목만이 정상).
_FUNDING_NO_PARSER = ("전환가액", "교환청구권", "단기차입금", "금전대여", "채무보증")
# 보강 후보 중 제목만으로 완결되는 사건 (사용자 2026-06-13) — 카테고리화돼
# 카드로 노출되되 '미파싱' 색칠은 안 한다(제목이 곧 완전한 신호). 벌금액·
# 스톡옵션 수량 등 구조화 detail 은 라이브 양식 확인 후 후속 — 현 단계는
# 드롭→가시 카드화가 목표. (홍수 위험 0: 전부 저빈도)
# (대표이사/대표집행임원 '변경'은 괄호 변형 때문에 아래 is_parse_target 의
# 전용 분기로 처리 — 단일 키워드로 못 잡음)
_TITLE_ONLY_OK_KW = ("벌금", "과태료", "과징금", "중대재해", "산업재해",
                     "재해발생", "상호변경", "본점소재지변경", "사외이사",
                     "주식매수선택권", "기업가치제고",
                     # 주주총회 거버넌스 + 증권발행결과(자율공시) — 제목완결
                     # (금액/수량 구조화 없음), 미파싱 색칠·보강후보 제외 (사용자
                     # 2026-06-13 A안). ⚠️ '기타경영사항(자율공시)'는 _PARSE_FORCE_
                     # KW('기타경영사항')가 force-parse → 여기 넣어도 무효 + 카브
                     # 아웃 시 소송/계약 자동승격 깨짐 → catch-all 승격 경로 유지.
                     "주주총회소집", "주주명부폐쇄", "의결권대리행사",
                     "주주총회결과", "증권발행결과",
                     # 보강 후보 2차 (사용자 2026-06-15 coverage-audit) — 제목완결
                     # 거버넌스/리스크/자금조달, 미파싱 색칠 제외. 투자판단은
                     # force-parse(_PARSE_FORCE_KW)라 여기 넣어도 무효 → 제외.
                     "기업심사위원회", "상장적격성", "정리매매", "개선기간",
                     "개선계획", "상장유지", "회계처리기준위반", "특수관계인",
                     "지주회사", "대규모기업집단", "자산재평가", "조건부자본증권",
                     "교환가액", "주권관련사채")


def is_parse_target(item: dict) -> bool:
    """이 공시가 구조화/원문 파싱 대상인가 — 제목만이 정상인 유형(IR·
    대량보유 외 지분공시·구조화 없는 자금조달 5종·보강후보 거버넌스류)은
    False → 대시보드가 미파싱으로 색칠하지 않음. enrich 시도 조건과 동일."""
    cat = item.get("category", "")
    report_nm = item.get("report_nm", "")
    _force = any(k in report_nm for k in _PARSE_FORCE_KW)
    if not (_force or cat in _PARSE_CATS or "실적" in cat):
        return False
    if not item.get("corp_code"):
        return False
    # 발행·등록 서류(펀드 투자설명서/일괄신고/증권신고서) — 기업 '사건'이 아닌
    # 발행문서라 미파싱 색칠 제외 (사용자 2026-06-13 '펀드 투자설명서를 정말
    # 미파싱과 구분되게'). coverage-audit '의도된 제외'와 대시보드 badge 일치.
    if (not _force) and _is_noncorp_doc(report_nm):
        return False
    # 제목만으로 완결되는 보강후보 거버넌스/리스크 사건 — 미파싱 색칠 제외
    if (not _force) and any(k in report_nm for k in _TITLE_ONLY_OK_KW):
        return False
    # 대표이사/대표집행임원 '변경'(괄호 변형 포함) — 제목 완결 거버넌스
    if (not _force) and "변경" in report_nm and (
            "대표이사" in report_nm or "대표집행임원" in report_nm):
        return False
    # 지분공시: 대량보유(majorstock) + 임원·주요주주 소유상황(elestock,
    # 2026-06-12 '지분공시도 다 파싱') — 둘 다 무료 구조화 API 보유.
    # 그 외('보고서' generic 분류분 — 감사보고서 등)는 구조화 소스 없음.
    if (not _force) and cat == "지분공시" and not (
            "대량보유" in report_nm or "소유상황" in report_nm
            or "소유주식변동" in report_nm
            or ("주식보유" in report_nm and "변동" in report_nm)):
        return False
    if (not _force) and cat == "자금조달" and any(
            k in report_nm for k in _FUNDING_NO_PARSER):
        return False
    return True


# '의도된 미파싱'(미파싱제외) 키워드 — force-parse(catch-all) 하지만 고정 구조가
# 없어 detail 빈 게 정상인 유형. 진짜 미파싱(파서 갭)과 분리 (사용자 2026-06-14
# '의도한 미파싱은 미파싱제외로, 진짜 미파싱과 나눠'). 2026-06-14 추가(미파싱 정리):
#  · 거래정지해제 = 변경상장(액면병합/주식병합/감자) 후 거래재개 — 제목 완결(해제는
#    항상 benign, 호재/악재 아님). '거래정지'(해제 없는 정지)는 미포함 = 리스크 유지.
#  · 유동성공급계약 체결/해지 = LP 계약 — 제목 완결(구조화 숫자 없음).
#  · 개최결과 = 기업설명회(IR) 개최결과 — IR 종료 사실 보고(향후 일시 없음, 안내공시만 파싱).
_INTENDED_FREEFORM_KW = ("기타경영사항", "투자판단", "기타시장안내",
                         "거래정지해제", "유동성공급", "개최결과")


def intended_freeform_unparsed(report_nm: str) -> bool:
    """is_parse_target True + detail 빈 항목 중 '의도된 미파싱'인가 —
    catch-all freeform(기타경영사항/투자판단/기타시장안내) · 기계적 거래정지해제 ·
    LP 유동성공급계약 · IR 개최결과 · [첨부정정](본문 없는 첨부 정정) ·
    합병/분할 종료보고서 · 대량보유 약식(원문·전용 API 부재, 제목이 곧 내용)이면
    True. 대시보드가 '미파싱제외'(회색·설계상 정상)로 분리. 나머지 빈 항목은 '진짜
    미파싱'(파서 갭). 순수·테스트."""
    rn = report_nm or ""
    if any(k in rn for k in _INTENDED_FREEFORM_KW):
        return True
    if rn.startswith("[첨부정정]"):
        return True
    # 원문(document.xml) 미제공·구조화 API 부재 유형 — 제목이 곧 내용(사용자
    # 2026-06-14 '그냥 타이틀 온리처리'). 합병/분할 등 종료보고서(합병·분할 완료
    # 사실 보고, _fetch_doc_text=없음·전용 API 없음) + 대량보유 약식(간이 5% 보고,
    # 원문 미제공). detail 빈 게 정상 → 진짜 미파싱 아닌 미파싱제외. 유형자산
    # 양수도 종료(_asset_complete_lines 파싱)·대량보유 일반(majorstock API)은 미해당.
    if "종료보고서" in rn and ("합병" in rn or "분할" in rn):
        return True
    if "대량보유" in rn and "약식" in rn:
        return True
    return False


def _fair_disclosure_category(detail_lines: list) -> str | None:
    """공정공시 본문 제목 → 의미 카테고리 (배당 > 주주환원, 그 외 None=실적
    유지). 가이던스/장래계획성은 실적이 맞으므로 명시 마커만 승격."""
    tl = next((str(l) for l in detail_lines
               if str(l).startswith("제목:")), "")
    if not tl:
        return None
    if "배당" in tl:
        return "배당"
    if "주주환원" in tl or "자기주식" in tl or "자사주" in tl:
        return "주주환원"
    return None


def _upgrade_category(item: dict) -> None:
    """detail 로부터 결정적 카테고리 승격 — catch-all 제목(기타경영사항·
    투자판단 주요경영사항·공정공시 wrapper)의 카드를 본문 기반으로 소송/
    계약/주주환원/배당 등으로 복원. 재fetch 사이클이 제목 기준으로 재분류
    해도 known-reuse 경로에서 복원 (멱등·순수)."""
    try:
        rn = item.get("report_nm", "")
        det = [str(l) for l in (item.get("detail") or [])]
        if "기타경영사항" in rn and item.get("category") != "소송":
            if any(l.startswith("사건:") for l in det) or any(
                    k in l for l in det if l.startswith("제목:")
                    for k in ("소송", "가처분", "판결", "효력정지")):
                item["category"] = "소송"
            return
        if "투자판단" in rn and item.get("category") in ("기타", "", None):
            tl = next((l for l in det if l.startswith("제목:")), "")
            cat = _material_title_category(tl)
            if cat:
                item["category"] = cat
            return
        # 공정공시 wrapper (수시공시의무관련사항 등) — 본문이 주주환원
        # 정책/배당이면 승격 (사용자 2026-06-12 '왜 실적이야').
        if "공정공시" in rn or "수시공시" in rn:
            nc = _fair_disclosure_category(det)
            if nc and item.get("category") != nc:
                item["category"] = nc
    except Exception:
        pass


def _won_str_to_float(s: str) -> float | None:
    """'532억원'/'1.2조원'/'8,051억원'/'3,500만원' → 원 단위 float.
    (_won 약식 표기의 역파서 — 유상증자 시총대비 판정용, 순수)."""
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(조|억|만)?\s*원", str(s))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v * {"조": 1e12, "억": 1e8, "만": 1e4}.get(m.group(2), 1.0)


def _detail_amount_sum(detail: list, labels: tuple) -> float:
    """detail 라인 중 'label: X억원' 형태의 금액 합 (원). 라벨 prefix 매칭."""
    total = 0.0
    for dl in detail:
        s = str(dl)
        if any(s.startswith(lb) for lb in labels):
            v = _won_str_to_float(s)
            if v:
                total += v
    return total


def _market_cap_won(stock_code: str) -> float | None:
    """FSC 시가총액(원) numeric — 유상증자 시총대비 5% 판정용. 시총 워밍과
    같은 12h 디스크 캐시 공유(steady-state 추가 네트워크 0). 실패 None."""
    try:
        from bot.fsc_client import latest_price, fsc_key_ready
        if not fsc_key_ready():
            return None
        p = latest_price(f"{stock_code}.KS")
        if not p:
            return None
        v = p.get("mrktTotAmt")
        return float(str(v).replace(",", "")) if v else None
    except Exception:
        return None


def _sig_pct(detail: list, label_pat: str) -> float | None:
    """detail 라인에서 'label N[%]' 의 N 추출 (계약 매출액대비·시설
    자기자본대비). 라벨 뒤 구분자 ':'/공백 모두 허용. **% 는 선택** —
    capex 파서가 '자기자본대비: 39.00' 처럼 % 없이 기록해 규칙 5 가
    영영 미발화하던 버그 fix (종근당/웨이비스 2026-06-13, 과거 카드
    소급). '%p'(지분 증감 단위)는 계속 제외."""
    for dl in detail:
        m = re.search(label_pat + r"[:\s]*([\d.]+)\s*(%p|%)?", str(dl))
        if m and m.group(2) != "%p":
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def significance(item: dict, shares_outstanding: float | None = None,
                 market_cap: float | None = None) -> str | None:
    """중요 공시 8규칙 판정(사용자 2026-06-12) → 발화 사유 문자열 or None.
    전부 렌더타임 순수 판정(과거 아카이브 카드 소급, 파서 개선 즉시 반영).
    detail 의존 규칙(2·4·5·6·8)은 enrich 후에만 발화 — 미파싱 카드는 ⚠️.

    1. 손익구조 변동 — 매출액 +20%↑ OR 영업이익 +30%↑ OR 영업이익
       흑자전환 (사용자 2026-06-12 — Or 기준·**증가만**·적자전환 제외,
       정정 제외, detail 필요)
    2. 단일판매·공급계약 매출액대비 10%↑ (기재정정 제외)
    3. 주식소각 발행주식 3%↑ (사용자 2026-06-12 '소각·자사주 3% 이상만' —
       detail 의 '발행주식의 X%' 우선, 없으면 소각주식수÷shares 계산)
    4. 자기주식취득결정 발행주식 3%↑ (기재정정 제외, shares 필요)
    5. 신규시설투자 자기자본대비 15%↑ (사용자 2026-06-13 20→15)
    6. 지분공시(대량보유) 신규 5%↑ (직전<5%≤현재)
    7. 상장폐지 관련 (사용자 2026-06-12 '중요에 상장폐지도') — 제목 또는
       파싱된 제목/사건 라인에 상장폐지 (효력정지 가처분 포함)
    8. 유상증자 시총대비 5%↑ (사용자 2026-06-12, 기재정정 제외, market_cap
       필요 — 증자금액/시설·운영·채무상환·타법인 자금 합 ÷ FSC 시총)
    9. 배당 시가배당률 3%↑ (사용자 2026-06-13, detail 의 '시가배당률 N%')
    """
    rn = item.get("report_nm", "")
    cat = item.get("category", "")
    detail = item.get("detail") or []
    correction = "정정" in rn   # [기재정정] 등

    # 7 — 상장폐지 (최우선: 생존 이벤트). 발화 라인 화이트리스트 —
    # 제목/사건(소송·자율공시) + 사유/해제·만료/결론(매매거래정지·
    # 기타시장안내) — '비고: 상장폐지 아님' 류 자유 서술 오발 차단.
    # '실질심사'(상장적격성 실질심사 대상 = 상폐 전단계)도 포함 (리스크-2).
    # ⚠️ '상장폐지시까지' 는 기간 boilerplate — 병합/분할 전자등록 변경의
    # 기계적 거래정지(에코마케팅 230360, 2026-06-12 오발)가 '구주권
    # 상장폐지시까지 정지' 문구만으로 발화하던 것 차단. 실제 상폐
    # 신호(상장폐지결정/상장폐지일/상장폐지 우려/사유의 상장폐지)는 유지.
    def _delist_hit(s: str) -> bool:
        s = s.replace("상장폐지시까지", "").replace("상장폐지 시까지", "")
        return "상장폐지" in s or "실질심사" in s
    if ("상장폐지" in rn or "실질심사" in rn or any(
            _delist_hit(s)
            for dl in detail
            if (s := str(dl)).startswith(("제목:", "사건:", "사유:",
                                          "해제·만료:", "결론:")))):
        return "상장폐지 관련"
    # 1 — 손익구조 변동: 제목만으로 무조건 발화하던 것을 본문 변동률
    # 게이트로 (사용자 2026-06-12): **매출액 |20%|↑ OR 영업이익 |30%|↑**
    # (And 아님 Or, 증가·감소 모두 — 급감도 중요). 영업이익 흑자/적자전환
    # = 부호 반전이라 항상 발화. detail 미파싱이면 미발화(⚠️ 대기 —
    # 규칙 2·4·5·8 과 동일 정책).
    if "매출액또는손익구조" in rn and not correction:
        def _line_pct(prefix: str) -> float | None:
            for dl in detail:
                s = str(dl)
                if s.startswith(prefix):
                    m = re.search(r"([+\-][\d,.]+)\s*%", s)
                    if m:
                        try:
                            return float(m.group(1).replace(",", ""))
                        except ValueError:
                            return None
                    return None
            return None
        sp = _line_pct("매출액:")
        op = _line_pct("영업이익:")
        # +(증가)만 발화 (사용자 2026-06-12 3차 — '손익 30%는 + 일때만')
        if sp is not None and sp >= 20.0:
            return f"매출액 {sp:+.1f}%"
        if op is not None and op >= 30.0:
            return f"영업이익 {op:+.1f}%"
        # 흑자전환만 (사용자 2026-06-12 2차 — 적자전환 제외; 적자전환의
        # 급감은 위 영업이익 |30%| 게이트가 % 있으면 잡음)
        if any(str(dl).startswith("영업이익:") and "흑자전환" in str(dl)
               for dl in detail):
            return "영업이익 흑자전환"
    # 2
    if ("공급계약" in rn or "단일판매" in rn) and not correction:
        p = _sig_pct(detail, r"매출액\s*대비")
        if p is not None and p >= 10.0:
            return f"매출대비 {p:g}% 계약"
    # 3 — 소각도 발행주식 3%↑만 (신도기연 1.21% 류 미발화, 2026-06-12)
    if "소각" in rn:
        for dl in detail:
            s = str(dl)
            m = re.search(r"발행주식의\s*([\d.]+)\s*%", s)
            if m:
                try:
                    pct = float(m.group(1))
                    if pct >= 3.0:
                        return f"발행주식 {pct:g}% 소각"
                except ValueError:
                    pass
                break  # % 명시 케이스 — 3% 미만이면 미발화 확정
            m2 = re.search(r"소각[^0-9]*([\d,]+)\s*주", s)
            if m2 and shares_outstanding:
                try:
                    pct = (int(m2.group(1).replace(",", ""))
                           / shares_outstanding * 100)
                    if pct >= 3.0:
                        return f"발행주식 {pct:.1f}% 소각"
                except (ValueError, ZeroDivisionError):
                    pass
                break
        return None  # detail 부재/미달 — 미발화 (3%↑ 확인된 것만)
    # 4
    if ("자기주식" in rn and "취득" in rn and "결정" in rn
            and "처분" not in rn and not correction and shares_outstanding):
        for dl in detail:
            m = re.search(r"취득예정[^0-9]*([\d,]+)\s*주", str(dl))
            if m:
                try:
                    n = int(m.group(1).replace(",", ""))
                    pct = n / shares_outstanding * 100
                    if pct >= 3.0:
                        return f"발행주식 {pct:.1f}% 취득"
                except (ValueError, ZeroDivisionError):
                    pass
    # 5
    if "신규시설투자" in rn or "투자결정" in rn:
        p = _sig_pct(detail, r"자기자본\s*대비")
        if p is not None and p >= 15.0:
            return f"자기자본대비 {p:g}% 투자"
    # 8 — 유상증자 시총대비 5%↑ (유무상증자 포함, 무상은 자금 0 이라 자연 미발화)
    if "유상증자" in rn and not correction and market_cap:
        amt = _detail_amount_sum(
            detail, ("증자금액", "시설자금", "운영자금", "채무상환", "타법인"))
        if amt > 0:
            pct = amt / market_cap * 100
            if pct >= 5.0:
                return f"시총대비 {pct:.1f}% 유상증자"
    # 9 — 배당 시가배당률 3%↑ (사용자 2026-06-13). 보통주/우선주 복수
    # 라인 시 첫 매칭(_sig_pct) — 통상 보통주가 먼저.
    if "배당" in rn and not correction:
        p = _sig_pct(detail, r"시가\s*배당[률율]")
        if p is not None and p >= 3.0:
            return f"시가배당률 {p:g}%"
    # 6
    if cat == "지분공시" and "대량보유" in rn:
        new_reason = any("신규" in str(dl) for dl in detail if "보고사유" in str(dl))
        for dl in detail:
            s = str(dl)
            if "지분율" not in s:
                continue
            nums = re.findall(r"([\d.]+)\s*%(?!p)", s)
            if "→" in s and len(nums) >= 2:
                try:
                    before, after = float(nums[0]), float(nums[1])
                    if before < 5.0 <= after:
                        return f"신규 {after:g}% 대량보유"
                except ValueError:
                    pass
            elif nums and new_reason:
                try:
                    if float(nums[0]) >= 5.0:
                        return f"신규 {float(nums[0]):g}% 대량보유"
                except ValueError:
                    pass
    return None


def _shares_outstanding(stock_code: str) -> float | None:
    """FSC 상장주식수(lstgStCnt) — 자기주식 3% 비율 판정용. 시총 워밍과
    같은 12h 디스크 캐시 공유(steady-state 추가 네트워크 0). 실패 None."""
    try:
        from bot.fsc_client import latest_price, fsc_key_ready
        if not fsc_key_ready():
            return None
        p = latest_price(f"{stock_code}.KS")
        if not p:
            return None
        v = p.get("lstgStCnt")
        return float(str(v).replace(",", "")) if v else None
    except Exception:
        return None


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
            _upgrade_category(item)   # 재fetch 가 '기타'로 재분류한 소송성 복원
            continue

        # 파싱 대상 판정 = is_parse_target (대시보드 미파싱 색칠과 단일 소스,
        # 2026-06-12). IR·대량보유 외 지분공시·구조화 없는 자금조달 5종 제외 +
        # corp_code 부재 제외 + _force(원문 파서 보유 유형)는 카테고리 무관 시도.
        if is_parse_target(item):
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
                    nc = detail.get("category") if isinstance(detail, dict) else None
                    if nc:
                        item["category"] = nc   # 기타경영사항 소송성 → 소송 승격
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


# ── v3 — 당월 정리 백필 (사용자 2026-06-11 '5월치 삭제 + 6월 풀백필') ──────

_BACKFILL_MARKER_V3 = _ARCHIVE_DIR.parent / ".dart_feed_backfilled_v3"


def purge_archives_before(cutoff: date) -> int:
    """cutoff 이전 날짜의 아카이브 파일 삭제. 삭제 수 반환."""
    removed = 0
    try:
        for f in _ARCHIVE_DIR.glob("*.json"):
            try:
                d = datetime.strptime(f.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d < cutoff:
                f.unlink()
                removed += 1
    except Exception as exc:
        log.warning("dart_feed purge: %s", exc)
    if removed:
        log.info("dart_feed purge: %s 이전 아카이브 %d개 삭제", cutoff, removed)
    return removed


def warm_market_caps(days_back: int = 31) -> int:
    """아카이브 내 상장사 코드 전체의 FSC 시세 캐시 워밍 (12h 캐시 채움).

    렌더의 콜드-페치 45초 예산은 대량 백필 직후 수십 분에 걸쳐 점진
    워밍됨 — 백필 직후 1회 일괄 워밍으로 모든 카드의 '시가총액/현재가'
    줄이 다음 regen 부터 즉시 표시되게 (사용자 2026-06-11 '무조건
    들어가야'). FSC 는 DART 콜 버짓과 무관(별도 API)·무료·12h 캐시."""
    codes: set[str] = set()
    for items in load_all_archives(days_back=days_back).values():
        for it in items:
            sc = it.get("stock_code") or ""
            if len(sc) == 6 and sc.isdigit():
                codes.add(sc)
    warmed = 0
    for i, code in enumerate(sorted(codes)):
        try:
            if _market_cap_price_lines(code):
                warmed += 1
        except Exception:
            pass
        time.sleep(0.05)
        if (i + 1) % 200 == 0:
            log.info("dart_feed warm: %d/%d 코드", i + 1, len(codes))
    log.info("dart_feed warm: 시총 캐시 %d/%d 코드 완료", warmed, len(codes))
    return warmed


def backfill_v3_once_if_needed() -> dict | None:
    """v3 1회 백필: 당월 1일 이전 아카이브 삭제(5월치 제거) → 당월 전체
    (1일~오늘) 재fetch(새 분류 룰) → FSC 시총 일괄 워밍. marker gate."""
    if _BACKFILL_MARKER_V3.exists():
        return None
    today = datetime.now(_KST).date()
    month_start = today.replace(day=1)
    purged = purge_archives_before(month_start)
    stats = backfill_with_new_rules(days_back=(today - month_start).days)
    stats["purged"] = purged
    stats["warmed"] = warm_market_caps()
    try:
        _BACKFILL_MARKER_V3.parent.mkdir(parents=True, exist_ok=True)
        _BACKFILL_MARKER_V3.write_text(datetime.now(_KST).isoformat())
    except OSError:
        pass
    return stats


# ── v4 — 파싱 배치(2026-06-12, 37+양식) 소급 적용 백필 ─────────────────────
# 사용자 '백필 시간이 많이 드니 잘 생각해서' — 3유형을 비용별로 분리:
#  ① 파서 '변경' 유형(계약 보강·자사주 doc-first): 기존 detail 을 새 파서로
#     재추출하되 **성공 시에만 교체** (실패=옛 detail 유지 → 데이터 손실 0,
#     doc_fail 오염 0). 대상 좁음(키워드 4종) → 수 분.
#  ② 파서 '신설' 유형(소송·리스크·조회공시·공정공시·확인서…): 과거 시도가
#     doc_fail negative-cache 에 고착 → 캐시 클리어만 하면 run_once 의
#     14일 대기열(detail 부재분)이 8건/분으로 자연 드레인 (~1-2시간).
#  ③ '수집 자체가 안 되던' 유형(기타경영사항·투자판단 — keep 예외 신설):
#     당월 재fetch 로 과거 날짜分 수집(+전체 재분류) → ②의 대기열로 합류.
# 전부 ₩0 (DART 무료·LLM 0)·일일 콜버짓 가드.

_BACKFILL_MARKER_V4 = _ARCHIVE_DIR.parent / ".dart_feed_backfilled_v4"

# ①의 대상 — 이번 배치에서 출력이 '변경'된 파서의 제목 키워드만 (신설
# 파서 유형은 detail 부재 → ②가 담당, 여기 나열 금지: 전수 재추출은
# 콜 낭비 + 시간).
_V4_REPARSE_KW = ("공급계약", "단일판매",            # 계약 보강(구분 병기·꼬리 컷)
                  "자기주식취득결정", "자기주식처분결정")  # doc-first 전환(필드 확장)


def clear_doc_fail_cache() -> int:
    """doc_fail negative-cache 전체 클리어 — 신설 파서 유형의 과거 실패분
    (12~24h 고착)이 즉시 재시도 가능해짐. 클리어 건수 반환."""
    try:
        n = len(_doc_fail_load())
        _DOC_FAIL.unlink(missing_ok=True)
        if n:
            log.info("dart_feed v4: doc_fail 캐시 %d건 클리어", n)
        return n
    except Exception:
        return 0


def reparse_details(days_back: int = 30,
                    kw: tuple = _V4_REPARSE_KW) -> dict:
    """파서가 변경된 유형의 기존 detail 재추출 — enrich 의 known-reuse 가
    옛 출력으로 고정(idempotent 가드)하는 것을 푸는 1회 경로.

    성공 시에만 교체: 새 파서가 옛 양식 변형에 실패해도 옛 detail 유지
    (제목만 카드로 퇴행 0), doc_fail 마킹 안 함(다음 정규 사이클 오염 0)."""
    stats = {"checked": 0, "replaced": 0, "kept": 0}
    api_key = _dart_api_key()
    if not api_key:
        return stats
    for ds, items in load_all_archives(days_back=days_back).items():
        changed = False
        for it in items:
            rn = it.get("report_nm", "")
            if not it.get("detail") or not any(k in rn for k in kw):
                continue
            if _budget_today() >= _BUDGET_HARD - 500:
                log.warning("dart_feed v4 reparse: 콜버짓 헤드룸 도달 — 중단"
                            " (남은 항목은 옛 detail 유지)")
                break
            stats["checked"] += 1
            _budget_add(2)
            time.sleep(0.15)
            try:
                detail = _extract_detail(rn, str(it.get("rcept_no", "")),
                                         it.get("corp_code", ""), api_key)
                lines = list(detail.get("lines", [])) if detail else []
                if lines and lines != it.get("detail"):
                    it["detail"] = lines
                    nc = (detail.get("category")
                          if isinstance(detail, dict) else None)
                    if nc:
                        it["category"] = nc
                    stats["replaced"] += 1
                    changed = True
                else:
                    stats["kept"] += 1
            except Exception as exc:
                stats["kept"] += 1
                log.debug("dart_feed v4 reparse %s 실패(옛 detail 유지): %s",
                          it.get("rcept_no", "?"), exc)
        if changed:
            try:
                save_archive(datetime.strptime(ds, "%Y-%m-%d").date(), items)
            except Exception as exc:
                log.warning("dart_feed v4 reparse save %s: %s", ds, exc)
    log.info("dart_feed v4 reparse: 검사 %d · 교체 %d · 유지 %d",
             stats["checked"], stats["replaced"], stats["kept"])
    return stats


def backfill_v4_once_if_needed() -> dict | None:
    """v4 1회 백필 (marker gate) — 위 ①②③ 순서로 실행, 통계 반환."""
    if _BACKFILL_MARKER_V4.exists():
        return None
    today = datetime.now(_KST).date()
    month_start = today.replace(day=1)
    stats: dict = {"doc_fail_cleared": clear_doc_fail_cache()}   # ②
    stats["reparse"] = reparse_details(days_back=30)             # ①
    st = backfill_with_new_rules(                                # ③
        days_back=(today - month_start).days)
    stats.update({k: st.get(k, 0)
                  for k in ("reclassified", "added", "days")})
    try:
        _BACKFILL_MARKER_V4.parent.mkdir(parents=True, exist_ok=True)
        # 통계 JSON 으로 저장 — 대시보드가 '백필 v4 완료 …' 라벨로 표시
        # (사용자 2026-06-12 '확실히 확인한거지' — SSH/문의 없이 화면 확인).
        rp = stats.get("reparse") or {}
        _BACKFILL_MARKER_V4.write_text(json.dumps({
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "reparse_replaced": rp.get("replaced", 0),
            "reparse_checked": rp.get("checked", 0),
            "added": stats.get("added", 0),
            "reclassified": stats.get("reclassified", 0),
        }, ensure_ascii=False))
    except OSError:
        pass
    return stats


# ── v5 — 로컬 재분류 (배당 분리·공정공시 승격, 2026-06-12) ─────────────────
# API 0·순수 로컬: 제목 재분류(_classify_report — 배당 신설) + 본문 승격
# (_upgrade_category — 공정공시 주주환원/배당). 수 초.

_RECLASS_MARKER_V5 = _ARCHIVE_DIR.parent / ".dart_feed_reclassified_v5"


def reclassify_v5_once_if_needed() -> dict | None:
    if _RECLASS_MARKER_V5.exists():
        return None
    stats = {"reclassified": 0, "upgraded": 0}
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
            before = it.get("category")
            _upgrade_category(it)
            if it.get("category") != before:
                stats["upgraded"] += 1
                changed = True
        if changed:
            save_archive(d, items)
    try:
        _RECLASS_MARKER_V5.parent.mkdir(parents=True, exist_ok=True)
        _RECLASS_MARKER_V5.write_text(datetime.now(_KST).isoformat())
    except OSError:
        pass
    log.info("dart_feed v5 재분류: 제목 %d · 본문승격 %d",
             stats["reclassified"], stats["upgraded"])
    return stats


# ── v6 — doc_fail 일회성 해제 (2026-06-12 밤) ─────────────────────────────
# v4 클리어(20:48)~generic 폴백 배포(#283, 21:23) 사이에 시도돼 12h 쿨다운
# 에 고착된 미파싱 잔여(~135)가 내일 아침까지 기다리지 않고, generic 번호
# 항목 폴백 + 이후 배치 파서들과 함께 즉시 재시도되게 1회 해제. 큐는
# 분당 8건 — ~20분 내 드레인. ₩0.

_DOC_FAIL_CLEAR_MARKER_V6 = _ARCHIVE_DIR.parent / ".dart_doc_fail_cleared_v6"


def clear_doc_fail_once_v6() -> int | None:
    if _DOC_FAIL_CLEAR_MARKER_V6.exists():
        return None
    n = clear_doc_fail_cache()
    try:
        _DOC_FAIL_CLEAR_MARKER_V6.parent.mkdir(parents=True, exist_ok=True)
        _DOC_FAIL_CLEAR_MARKER_V6.write_text(datetime.now(_KST).isoformat())
    except OSError:
        pass
    return n


_DOC_FAIL_CLEAR_MARKER_V7 = _ARCHIVE_DIR.parent / ".dart_doc_fail_cleared_v7"


def clear_doc_fail_once_v7() -> int | None:
    """generic 폴백 일괄 보장 래퍼 배포(2026-06-14 '미파싱 9개') 후 고착 미파싱
    1회 재해제 — 전환청구권/IR/대량보유 등 early-return 으로 12h 쿨다운 박힌
    항목이 새 래퍼(_extract_detail generic 폴백)로 재시도되게. v6 동일 메커니즘,
    새 마커. run_once 14일 pending 큐가 8건/분으로 자연 재드레인."""
    if _DOC_FAIL_CLEAR_MARKER_V7.exists():
        return None
    n = clear_doc_fail_cache()
    try:
        _DOC_FAIL_CLEAR_MARKER_V7.parent.mkdir(parents=True, exist_ok=True)
        _DOC_FAIL_CLEAR_MARKER_V7.write_text(datetime.now(_KST).isoformat())
    except OSError:
        pass
    return n


_RECLASS_MARKER_V7 = _ARCHIVE_DIR.parent / ".dart_feed_reclassified_v7"
# 분류 정책 버전 — 바뀔 때마다 bump 하면 startup 1회 로컬 재분류가 소급
# (marker 가 버전 문자열을 저장, 불일치 시 재실행. API 0·수 초).
_RECLASS_VER = "v8-회사분할-회사구조"


def reclassify_v7_once_if_needed() -> dict | None:
    """분류 정책 변경 소급 — 로컬 재분류 패스(API 0·수 초). marker 내용
    = _RECLASS_VER (버전 bump 만으로 재실행)."""
    try:
        if _RECLASS_MARKER_V7.read_text(encoding="utf-8").strip() == _RECLASS_VER:
            return None
    except OSError:
        pass
    stats = {"reclassified": 0, "upgraded": 0}
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
            before = it.get("category")
            _upgrade_category(it)
            if it.get("category") != before:
                stats["upgraded"] += 1
                changed = True
        if changed:
            save_archive(d, items)
    try:
        _RECLASS_MARKER_V7.parent.mkdir(parents=True, exist_ok=True)
        _RECLASS_MARKER_V7.write_text(_RECLASS_VER, encoding="utf-8")
    except OSError:
        pass
    log.info("dart_feed 재분류(%s): 제목 %d · 본문승격 %d",
             _RECLASS_VER, stats["reclassified"], stats["upgraded"])
    return stats


def backfill_v4_status() -> str:
    """대시보드 표시용 한 줄 — '✅ 완료 ts · …' / '⏳ 대기/실행 중'.
    옛 plain-isoformat marker 도 tolerant (완료 시각만)."""
    try:
        if not _BACKFILL_MARKER_V4.exists():
            return ("⏳ 파싱배치 백필 v4: 대기/실행 중 (봇 재시작 직후 자동, "
                    "이후 대기열 분당 8건 드레인)")
        raw = _BACKFILL_MARKER_V4.read_text(encoding="utf-8").strip()
        try:
            d = json.loads(raw)
            ts = str(d.get("ts", ""))[:16].replace("T", " ")
            return (f"✅ 파싱배치 백필 v4 완료 {ts} · 재추출 "
                    f"{d.get('reparse_replaced', 0)}/{d.get('reparse_checked', 0)}건 "
                    f"교체 · 신규수집 {d.get('added', 0)}건 · 재분류 "
                    f"{d.get('reclassified', 0)}건 — 미파싱 잔여분은 대기열이 "
                    f"분당 8건 자동 드레인")
        except Exception:
            return f"✅ 파싱배치 백필 v4 완료 {raw[:16].replace('T', ' ')}"
    except Exception:
        return ""


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
        # 윈도 3→14일 (2026-06-11): 풀백필이 추가한 당월 초 항목도 파서
        # 보유 유형이면 enrich 가 자연 드레인 (8건/분 cap 그대로 — 분당
        # 한도 보호, 백로그는 수 시간에 걸쳐 소진).
        for day_items in load_all_archives(days_back=14).values():
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
        _noncorp = rep.get("dropped_noncorp", {})
        if _noncorp:
            print(f"[coverage] 드롭 — 상장사 발행·등록 서류 {sum(_noncorp.values())}건 "
                  "(투자설명서/일괄신고/증권신고서 — 의도된 제외, 펀드 투자설명서 동류):")
            for k, v in sorted(_noncorp.items(), key=lambda kv: -kv[1])[:20]:
                print(f"  {v:4d} × {k}")
        _title = rep.get("dropped_title", {})
        if _title:
            print(f"[coverage] 드롭 — 상장사 제목완결 거버넌스 {sum(_title.values())}건 "
                  "(주주총회/주주명부/의결권/증권발행결과 등 — 의도된 제외, A안):")
            for k, v in sorted(_title.items(), key=lambda kv: -kv[1])[:20]:
                print(f"  {v:4d} × {k}")
        print("[coverage] 드롭 — 상장사 '기타' 제목 분포 (보강 후보 — 실제 기업 사건):")
        for k, v in sorted(rep["dropped_listed"].items(), key=lambda kv: -kv[1])[:40]:
            print(f"  {v:4d} × {k}")
        raise SystemExit(0)
    if any("unparsed-audit" in a or "unparsed_audit" in a for a in _sys.argv[1:]):
        # 진짜 미파싱(파싱대상인데 detail 없음) report_nm 분포 + 샘플 url —
        # 어느 양식에 파서가 없는지 진단(사용자 2026-06-15 '미파싱 쌓임'):
        #   .venv/bin/python -m bot.dart_feed --unparsed-audit [days]
        _days = 7
        for a in _sys.argv[1:]:
            if a.isdigit():
                _days = int(a)
        rep = unparsed_audit(days_back=_days)
        print(f"[미파싱] 윈도 {_days}일 · 진짜 미파싱 {rep['total']}건 "
              f"({len(rep['dist'])} 양식) — 아래 report_nm 으로 파서 추가")
        for k, v in sorted(rep["dist"].items(), key=lambda kv: -kv[1]):
            print(f"  {v:3d} × {k}")
            print(f"        예: {rep['samples'].get(k, '')}")
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
