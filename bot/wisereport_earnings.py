"""WISEreport(네이버 임베드, FnGuide) 어닝서프라이즈 스크레이퍼 — KR 전용.

배경: FnGuide CompanyGuide(comp.fnguide.com) 의 스냅샷에는 '실적이슈'(다음 예상
실적)만 있고, 사용자가 본 '펀더멘탈 > 어닝서프라이즈' 표(영업이익·당기순이익 ×
컨센서스/잠정치/Surprise/전년동기대비)는 네이버 금융이 임베드하는
``navercomp.wisereport.co.kr`` 의 기업현황(c1010001.aspx)에서 온다. 그 표는
Underscore 템플릿(#tmpl-list)이지만, 데이터는 **페이지에 서버사이드로 임베드된
``var res = {...}`` 자바스크립트 객체**라 추가 AJAX·encparam 없이 단발 GET 으로
획득 가능(2026-06-20 VM 실측 확인).

res 구조(실측):
  yymm  = ["202509","202512","202603"]   # 첫 항목은 템플릿이 스킵(표시는 2개월)
  data  = [op_cons, op_act, op_surp, op_yoy, op_qoq,   # 영업이익 5행
           ni_cons, ni_act, ni_surp, ni_yoy, ni_qoq]   # 당기순이익 5행
          각 행 = {"1":<yymm[0]>, "2":<yymm[1]>, "3":<yymm[2]>}  # "1" 스킵
  yymmdd = ["2025/10/14(연결)", ...]      # 잠정치 발표(예정)일/회계기준(첫 항목 스킵)
단위: 컨센서스/잠정치 = 억원, Surprise/전년동기대비 = %.

설계: fnguide_consensus.py 와 동일 — 현실적 UA, 10s 타임아웃, 12h 디스크 캐시
(finviz_client 유틸 재사용), 파싱 실패/무커버리지는 graceful None. 호출부는 None
을 반드시 우아하게 처리(날조 금지).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests

log = logging.getLogger("bot.wisereport")

_URL_TMPL = (
    "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://finance.naver.com/",
}
_TIMEOUT = 10


def fetch_earnings_surprise(stock_code: str) -> Optional[dict]:
    """네이버/WISEreport 어닝서프라이즈 수집(.KS/.KQ → 6자리). 12h 디스크 캐시.

    Returns dict(아래 _parse 형식) 또는 None(무커버리지·네트워크/파싱 실패).
    호출부는 None 을 우아하게 처리해야 한다(없으면 표 미표시)."""
    code = (stock_code or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return None

    from bot.finviz_client import _cache_write, _cached
    ck = f"wisereport_earnings_{code}.json"
    hit = _cached(ck, ttl=12 * 3600)
    if isinstance(hit, dict) and "data" in hit:
        return hit["data"] or None

    try:
        resp = requests.get(_URL_TMPL.format(code=code),
                            headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        log.warning("wisereport: fetch failed for %s: %s", code, exc)
        return None   # 네트워크 오류 — 미캐시(다음에 재시도)

    result = parse_earnings_surprise(html)
    # 성공·무커버리지(파싱 None) 둘 다 캐시 — 재스크레이프 차단
    _cache_write(ck, {"data": result})
    return result


def parse_earnings_surprise(html: str) -> Optional[dict]:
    """c1010001.aspx HTML 에서 EarnigList 의 ``var res = {...}`` 추출·정규화.

    {periods:["2025/12",...], op:{consensus,actual,surprise,yoy}, ni:{...},
     announce:["2026/01/08(연결)",...], unit:"억원"}. 실패 시 None(순수함수)."""
    # 데이터는 #earning_list 를 채우는 EarnigList 함수의 직전 var res 객체.
    k = html.find('$("#earning_list tbody")')
    if k < 0:
        return None
    j = html.rfind("var res = {", 0, k)
    if j < 0:
        return None
    start = html.index("{", j)
    # res 객체는 한 줄(내부 객체는 '},' 로 끝나고, 객체 종료만 '};')이라 '};' 가 경계.
    end = html.find("};", start)
    if end < 0:
        return None
    try:
        res = json.loads(html[start:end + 1])
    except Exception as exc:
        log.warning("wisereport: res json parse failed: %s", exc)
        return None

    yymm = res.get("yymm") or []
    data = res.get("data") or []
    if len(yymm) < 2 or len(data) < 9:
        return None
    periods = [_fmt_ym(y) for y in yymm[1:]]          # 첫 항목 스킵(템플릿과 동일)
    n = len(periods)

    def col(idx: int) -> list:
        # 행 객체 키 "1" 스킵 → "2".."(n+1)" 가 표시 컬럼.
        if idx >= len(data) or not isinstance(data[idx], dict):
            return [None] * n
        d = data[idx]
        return [d.get(str(c + 2)) for c in range(n)]

    out = {
        "periods": periods,
        "op": {"consensus": col(0), "actual": col(1),
               "surprise": col(2), "yoy": col(3)},
        "ni": {"consensus": col(5), "actual": col(6),
               "surprise": col(7), "yoy": col(8)},
        "announce": (res.get("yymmdd") or [])[1:],
        "unit": "억원",
    }
    # sanity: 컨센서스/잠정치 중 하나라도 숫자가 있어야 유효(빈 표 차단).
    flat = (out["op"]["consensus"] + out["op"]["actual"]
            + out["ni"]["consensus"] + out["ni"]["actual"])
    if not any(isinstance(v, (int, float)) for v in flat):
        return None
    return out


def _fmt_ym(ym: str) -> str:
    """'202512' → '2025/12'. 형식 이상하면 원본 반환(graceful)."""
    s = str(ym or "")
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}/{s[4:6]}"
    return s
