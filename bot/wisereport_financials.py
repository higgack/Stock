"""WISEreport(네이버 임베드, FnGuide) **Financial Summary** 파서 — KR 전용.

⚠️ 왜 필요한가(사용자 2026-08-19 NH투자증권). DART `fnlttSinglAcntAll` 의
손익계산서에는 증권·은행·보험사의 **총액 계정(영업수익)이 없다** — VM 프로브
실측으로 27종목 중 15종목이 그랬다(수수료수익·이자수익 등 구성요소만 공시).
그래서 화면 '매출' 자리에 이자수익이 올라가 영업이익보다 작아 보였다.

네이버 금융이 임베드하는 같은 페이지(c1010001.aspx, `wisereport_earnings`
가 이미 쓰는 URL)의 Financial Summary 표에는 **매출액 총액**이 있다
(NH투자증권 26.1Q = 81,720억 vs 우리 이자수익 5,532억).

⚠️ **여기 값을 그대로 믿고 덮지 않는다.** 호출부가 검산한다:
  · 기간 키가 정확히 일치할 때만(결산월이 12월이 아닌 회사 오매칭 방지)
  · 총액은 구성요소보다 **커야** 한다(부분 > 전체는 파싱 오류다)
  · 영업이익이 양쪽에서 일치해야 한다(다른 회사·다른 표를 붙였는지 검사)
검산에 걸리면 아무것도 바꾸지 않는다 — 옛 동작(구성요소 + 비율 비움) 유지.

단위: 표는 **억원** → 원으로 환산해 돌려준다. 12h 디스크 캐시. graceful None.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

log = logging.getLogger("bot.wisereport_fin")

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
_TIMEOUT = 12
_CACHE_TTL_H = 12

_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_PERIOD = re.compile(r"\b(\d{4})/(\d{2})\b")

# 표에서 가져올 항목(왼쪽 라벨). 들여쓴 하위 항목(당기순이익(지배) 등)은
# 이름이 달라 자동으로 걸러진다.
_WANT = ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계")
_EOK = 1e8          # 억원 → 원


def _text(html: str) -> str:
    import html as _h
    return _h.unescape(_TAG.sub(" ", html)).replace("\xa0", " ").strip()


def _num(s: str) -> Optional[float]:
    s = (s or "").strip().replace(",", "")
    if not s or s in ("-", "N/A"):
        return None
    m = re.fullmatch(r"-?\d+(\.\d+)?", s)
    return float(s) if m else None


def parse_financial_summary(html: str) -> dict:
    """{'annual': {'2025/12': {...}}, 'quarter': {'2026/03': {...}}} (원 단위).

    표 id 에 기대지 않는다 — **내용으로** 찾는다(사이트가 id 를 바꿔도 산다).
    분기/연간 구분도 헤더 월로 판정한다(12월만 있으면 연간)."""
    out: dict = {"annual": {}, "quarter": {}}
    for tbl in _TABLE.findall(html or ""):
        rows = [[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(tbl)]
        rows = [r for r in rows if r]
        # 헤더 = 기간이 2개 이상 있는 줄
        hdr_i = hdr = None
        for i, r in enumerate(rows):
            per = [(_PERIOD.search(c).group(0) if _PERIOD.search(c) else None)
                   for c in r]
            if sum(1 for p in per if p) >= 2:
                hdr_i, hdr = i, per
                break
        if hdr is None:
            continue
        labels = {(r[0] or "").replace(" ", "") for r in rows[hdr_i + 1:]}
        if "매출액" not in labels or "영업이익" not in labels:
            continue                      # Financial Summary 표가 아니다
        months = {p.split("/")[1] for p in hdr if p}
        kind = "annual" if months <= {"12"} else "quarter"
        bucket = out[kind]
        for r in rows[hdr_i + 1:]:
            name = (r[0] or "").replace(" ", "")
            if name not in _WANT:
                continue
            for ci, per in enumerate(hdr):
                if not per or ci >= len(r):
                    continue
                v = _num(r[ci])
                if v is None:
                    continue
                bucket.setdefault(per, {})[name] = v * _EOK
    return out


def fetch_financial_summary(stock_code: str) -> Optional[dict]:
    """`parse_financial_summary` 결과 또는 None(무커버리지·실패). 12h 캐시."""
    code = str(stock_code or "").split(".")[0].strip()
    if not (len(code) == 6 and code.isdigit()):
        return None
    try:
        from bot.finviz_client import _cache_write, _cached
    except Exception:
        _cache_write = _cached = None
    ck = f"wisereport_finsum_{code}.json"
    if _cached:
        c = _cached(ck, ttl=_CACHE_TTL_H * 3600)
        if isinstance(c, dict) and c:
            return c
    try:
        resp = requests.get(_URL_TMPL.format(code=code), headers=_HEADERS,
                            timeout=_TIMEOUT)
        resp.raise_for_status()
        # 이 페이지는 EUC-KR 계열을 내보낼 때가 있다 — 잘못 읽으면 라벨이
        # 깨져 '매출액' 매칭이 조용히 실패한다.
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        out = parse_financial_summary(resp.text)
    except Exception as exc:                            # noqa: BLE001
        log.info("wisereport_fin: %s 실패: %s", code, exc)
        return None
    if not (out.get("annual") or out.get("quarter")):
        log.info("wisereport_fin: %s Financial Summary 표를 못 찾음", code)
        return None
    if _cache_write:
        _cache_write(ck, out)
    return out
