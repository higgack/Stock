"""KRX KIND IR일정 스크래퍼 — 실제 미래 IR 개최일(DART 공시일보다 정확).

DART 는 '기업설명회(IR)개최(안내)' 공시 **접수일**(과거)만 주는 반면, KIND
IR일정(kind.krx.co.kr)은 **실제 개최 예정일**(미래 포함)을 준다. 사용자 정책
2026-06-10: "DART 는 완료된 일정만 나오네 → KIND 써서 IR 일정을 캘린더에".

무료·무키. graceful — 실패/빈/포맷 불일치 시 [](호출부가 DART 로 폴백).
⚠️ KIND POST 파라미터/응답 구조는 실측 검증 필요(샌드박스 차단). 후보 param
세트를 순차 시도 + 휴리스틱 파서로 날짜·종목명·코드 추출 → 빗나가도 [] 회귀.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger("bot.kind_ir")

_URL = "https://kind.krx.co.kr/corpgeneral/irschedule.do"
_CACHE = Path.home() / ".tradingagents" / "cache" / "kind_ir"
_TTL = 12 * 3600
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Referer": _URL + "?method=searchIRScheduleMain&gubun=iRScheduleCalendar",
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Origin": "https://kind.krx.co.kr",
}
_DATE_RE = re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
_CODE_RE = re.compile(r"\b(\d{6})\b")
_ANCHOR_RE = re.compile(r"<a[^>]*>(.*?)</a>", re.DOTALL | re.I)
_NAME_STOP = ("투자자", "이해증진", "경영실적", "기업설명", "회의실", "온라인",
              "IR", "유가", "코스닥", "코넥스", "서울", "여의도", "현황", "개최",
              "장소", "내용", "목적", "일시", "기관", "콘퍼런스", "컨퍼런스")


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&middot;", "·"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return " ".join(s.split()).strip()


def _iso(y, m, d) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


_CODE_HREF_RE = re.compile(
    r"(?:isurCd|isuCd|srtnCd|stockCd|short|code)\D{0,3}(\d{6})", re.I)
_TITLE_KW = ("투자자", "이해증진", "경영실적", "기업설명", "설명회", "현황", "컨퍼런스")


def parse_rows(html: str) -> list[dict]:
    """IR일정 HTML(테이블/리스트) → [{name, code, date, title, time}]. 휴리스틱."""
    out: list[dict] = []
    blocks = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I)
    if len(blocks) < 2:  # 캘린더 셀(li/div) 구조일 수도
        blocks = re.findall(r"<li[^>]*>(.*?)</li>", html, re.DOTALL | re.I) or blocks
    for block in blocks:
        cells = [_clean(c) for c in
                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", block, re.DOTALL | re.I)]
        flat = _clean(block)
        dm = _DATE_RE.search(flat)
        if not dm:
            continue
        d_iso = _iso(*dm.groups())

        # 종목코드: href 의 isurCd 등(태그 제거 전 raw) 우선, 없으면 raw 6자리
        code = ""
        cm = _CODE_HREF_RE.search(block)
        if not cm:
            cm = _CODE_RE.search(re.sub(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", " ", block))
        if cm:
            code = cm.group(1)

        # 회사명: 첫 anchor 텍스트(회사 링크), 없으면 짧은 비-잡토큰 셀
        name = ""
        for a in _ANCHOR_RE.findall(block):
            t = _clean(a)
            if (t and len(t) <= 20 and not _DATE_RE.search(t)
                    and not _TIME_RE.fullmatch(t)
                    and not any(k in t for k in _NAME_STOP) and not t.isdigit()):
                name = t
                break
        if not name:
            for t in (cells or re.split(r"\s{2,}|\||·", flat)):
                t = t.strip()
                if (t and len(t) <= 20 and not _DATE_RE.search(t)
                        and not _TIME_RE.fullmatch(t)
                        and not any(k in t for k in _NAME_STOP)
                        and not re.fullmatch(r"[\d,.\-:/\s]+", t)):
                    name = t
                    break

        # 제목: IR 목적 키워드를 담은 '셀'(전체 행 아님)
        title = ""
        for c in cells:
            if c and c != name and any(k in c for k in _TITLE_KW):
                title = c[:60]
                break

        tm = _TIME_RE.search(flat)
        if not name and not code:
            continue
        out.append({"name": name, "code": code, "date": d_iso,
                    "title": title or "기업설명회(IR)",
                    "time": tm.group(1) if tm else ""})
    return out


def fetch_kind_ir_month(year: int, month: int) -> list[dict]:
    """[year-month] KIND IR일정 → [{name, code, date, title, time}] 날짜순.

    실패/빈/포맷 불일치 시 [](호출부가 DART 폴백). 12h 캐시(월별)."""
    tag = f"{year:04d}-{month:02d}"
    _CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE / f"{tag}.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _TTL:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    first = date(year, month, 1)
    last = (date(year + 1, 1, 1) if month == 12
            else date(year, month + 1, 1)) - timedelta(days=1)
    f_iso, l_iso = first.isoformat(), last.isoformat()
    f_dot, l_dot = f_iso.replace("-", "."), l_iso.replace("-", ".")
    ym = f"{year:04d}{month:02d}"

    # KIND 캘린더 AJAX param 후보(정확 키 미검증 → 다중 시도)
    candidates = [
        {"method": "searchIRScheduleList", "currentPageSize": "100",
         "pageIndex": "1", "fromDate": f_iso, "toDate": l_iso,
         "searchCorpName": "", "marketType": "", "repIsuSrtCd": "",
         "forwardYm": ym, "gubun": "iRScheduleCalendar"},
        {"method": "searchIRScheduleCalendar", "forwardYm": ym,
         "searchYm": ym, "fromDate": f_dot, "toDate": l_dot},
    ]
    rows: list[dict] = []
    for data in candidates:
        try:
            r = requests.post(_URL, data=data, headers=_HEADERS, timeout=15)
            if r.status_code == 200 and r.text:
                rows = parse_rows(r.text)
                if rows:
                    break
        except Exception as exc:
            log.warning("kind_ir %s: post failed: %s", tag, exc)

    # 월 범위 필터 + dedup
    seen: set = set()
    out: list[dict] = []
    for rrow in rows:
        d = rrow.get("date", "")
        if not (f_iso <= d <= l_iso):
            continue
        key = (rrow.get("code"), d, rrow.get("name"))
        if key in seen:
            continue
        seen.add(key)
        out.append(rrow)
    out.sort(key=lambda x: x.get("date", ""))
    log.info("kind_ir %s → %d IR 일정", tag, len(out))
    try:
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    t = date.today()
    res = fetch_kind_ir_month(t.year, t.month)
    print(f"KIND IR {t.year}-{t.month}: {len(res)} events")
    for e in res[:12]:
        print(" ", e["date"], e.get("time", ""), e["name"], e["code"], "—", e["title"][:30])
