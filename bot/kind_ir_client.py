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


_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.I)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.I)


def parse_calendar(html: str, year: int, month: int) -> list[dict]:
    """KIND IR 달력 HTML(#calBig 표) → [{name, code, date, title, time}].

    구조(실측 2026-06-10): 각 <td> = 하루(셀 머리에 날짜 숫자) + <ul><li> 이벤트.
    날짜는 selYear/selMonth + 셀 일(day)로 구성. 빈 셀/빈 li 건너뜀."""
    out: list[dict] = []
    seen: set = set()
    for td in _TD_RE.findall(html):
        head = td.split("<ul", 1)[0]          # <ul> 앞 = 날짜 숫자
        dm = re.search(r"(\d{1,2})", head)
        if not dm:
            continue                          # 날짜 없는 빈 셀(달 앞뒤 여백)
        day = int(dm.group(1))
        if not (1 <= day <= 31):
            continue
        d_iso = _iso(year, month, day)
        ul = td[td.find("<ul"):] if "<ul" in td else ""
        for li in _LI_RE.findall(ul):
            txt = _clean(li)
            if not txt:
                continue                      # 빈 li
            # 회사명: anchor 텍스트 우선, 없으면 li 평문 첫 토큰
            name = ""
            am = _ANCHOR_RE.search(li)
            if am:
                name = _clean(am.group(1))
            if not name or name.isdigit() or _TIME_RE.fullmatch(name):
                for t in re.split(r"\s{2,}|\||·|,", txt):
                    t = t.strip()
                    if (t and len(t) <= 20 and not t.isdigit()
                            and not _TIME_RE.fullmatch(t)
                            and not any(k in t for k in _NAME_STOP)):
                        name = t
                        break
            if not name:
                continue
            name = name[:30]
            cm = _CODE_HREF_RE.search(li) or _CODE_RE.search(li)
            code = cm.group(1) if cm else ""
            tm = _TIME_RE.search(txt)
            key = (code, d_iso, name)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "code": code, "date": d_iso,
                        "title": "기업설명회(IR)",
                        "time": tm.group(1) if tm else ""})
    return out


def fetch_kind_ir_month(year: int, month: int) -> list[dict]:
    """[year-month] KIND IR일정(실제 개최일) → [{name, code, date, title, time}].

    실측 검증(2026-06-10): POST irschedule.do · method=searchIRScheduleCalendar
    · selYear/selMonth. 세션 쿠키를 위해 메인 페이지 GET 선행. 실패/빈 → []
    (호출부가 DART 폴백). 12h 캐시(월별)."""
    tag = f"{year:04d}-{month:02d}"
    _CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE / f"{tag}.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _TTL:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    data = {
        "method": "searchIRScheduleCalendar",
        "paxreq": "", "outsvcno": "", "forward": "", "gubun": "",
        "searchFromDate": "", "searchToDate": "",
        "selYear": str(year), "selMonth": f"{month:02d}",
    }
    rows: list[dict] = []
    try:
        sess = requests.Session()
        try:  # 세션 쿠키(JSESSIONID) 확보
            sess.get(_HEADERS["Referer"], headers=_HEADERS, timeout=15)
        except Exception:
            pass
        r = sess.post(_URL, data=data, headers=_HEADERS, timeout=15)
        if r.status_code == 200 and r.content:
            try:                              # 응답 헤더는 ISO-8859-1 오표기
                text = r.content.decode("utf-8")
            except UnicodeDecodeError:
                text = r.content.decode("euc-kr", errors="replace")
            rows = parse_calendar(text, year, month)
    except Exception as exc:
        log.warning("kind_ir %s: post failed: %s", tag, exc)

    rows.sort(key=lambda x: x.get("date", ""))
    log.info("kind_ir %s → %d IR 일정", tag, len(rows))
    try:
        cache_file.write_text(json.dumps(rows, ensure_ascii=False))
    except Exception:
        pass
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    t = date.today()
    res = fetch_kind_ir_month(t.year, t.month)
    print(f"KIND IR {t.year}-{t.month}: {len(res)} events")
    for e in res[:12]:
        print(" ", e["date"], e.get("time", ""), e["name"], e["code"], "—", e["title"][:30])
