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

# ⚠️ **기업현황(c1010001) 이 아니라 그 안의 프래그먼트**를 받는다(2026-08-19
# VM 실측). c1010001 은 81KB·표 20개인데 Financial Summary 는 AJAX 라 HTML 에
# 없다 — '매출액' 은 산식 설명("영업이익/매출액(수익)")으로만 등장해 파싱이
# 늘 빈손이었다. cF1001 은 22KB·표 1개로 그 표 자체를 준다.
_URL_TMPL = (
    "https://navercomp.wisereport.co.kr/v2/company/cF1001.aspx"
    "?cmp_cd={code}&fin_typ=0&freq_typ={freq}"
)
# 분기/연간 프래그먼트. VM 실측에서 freq_typ=Y 는 Q 와 같은 응답이라, 연간은
# 다른 값으로 한 번 더 시도하고 **내용(헤더 월)으로** 분류한다.
# ⚠️ 값·이름을 바꿔 여러 번 시도한다 — 잘못 붙어도 **내용(헤더 월)으로**
# 분기/연간을 가르므로 오분류 위험은 없다. 둘 다 얻으면 즉시 멈춘다.
_FREQS = ("Q", "A", "Y", "0", "1")
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
# ⚠️ **파싱 버전.** 12h 디스크 캐시는 코드를 고쳐도 안 바뀐다 — 그룹 헤더
# 분리(#923)를 배포하고도 스윕 출력이 **한 글자도 안 변했다**(2026-08-19).
# 캐시된 옛 파싱 결과가 그대로 서빙됐기 때문이다(실수 #18 의 세 번째 재발).
# 파서를 고치면 이 숫자를 올린다 — 옛 캐시는 즉시 무효.
#   v1 = 표 전체를 한 종류로 분류 · v2 = 그룹 헤더(연간/분기)로 컬럼별 분리
_PARSE_VER = 2

# ⚠️ 실패 이유를 남긴다 — "표 없음" 한 마디로는 (a) 요청 실패 (b) 표가 AJAX
# 라 HTML 에 없음 (c) 파싱 규칙 문제를 못 가른다(실수 #12 silent-fail).
_LAST_REASON: dict[str, str] = {}

_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
# colspan 까지 보는 판(그룹 헤더 '연간'/'분기' 를 컬럼별로 펼치려면 필요).
_CELL_SPAN = re.compile(r"<t[hd]([^>]*)>(.*?)</t[hd]>", re.S | re.I)
_COLSPAN = re.compile(r'colspan\s*=\s*["\']?(\d+)', re.I)
_TAG = re.compile(r"<[^>]+>")
_PERIOD = re.compile(r"\b(\d{4})/(\d{2})\b")

# 표에서 가져올 항목(왼쪽 라벨). 들여쓴 하위 항목(당기순이익(지배) 등)은
# 이름이 달라 자동으로 걸러진다.
_WANT = ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계")
_EOK = 1e8          # 억원 → 원


def _expand_group(row_html: str) -> list[str]:
    """그룹 헤더 줄을 **컬럼 수만큼 펼친다**(colspan 반영).

    ⚠️ 왜(2026-08-19 실측): 이 표는 '전체' 탭이면 **연간 컬럼과 분기 컬럼이
    한 표에 섞여** 있고, 어느 쪽인지는 위 줄의 그룹 헤더(연간/분기)가 말한다.
    월(12월)만 보고 가르면 연간 2025/12 와 4분기 2025/12 를 구분 못 하고,
    실제로 연간 추정치가 '분기 2026/12' 로 들어갔다."""
    out: list[str] = []
    for attrs, body in _CELL_SPAN.findall(row_html):
        m = _COLSPAN.search(attrs or "")
        out.extend([_text(body)] * max(1, int(m.group(1)) if m else 1))
    return out


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
        raw_rows = _ROW.findall(tbl)
        rows = [[_text(c) for c in _CELL.findall(r)] for r in raw_rows]
        keep = [i for i, r in enumerate(rows) if r]
        raw_rows = [raw_rows[i] for i in keep]
        rows = [rows[i] for i in keep]
        # 헤더 = 기간이 2개 이상 있는 줄
        hdr_i = periods = None
        for i, r in enumerate(rows):
            # ⚠️ 추정치 컬럼((E))은 **실적이 아니다** — 받지 않는다.
            per = [None if ("(E)" in c or "(P)" in c) else
                   (_PERIOD.search(c).group(0) if _PERIOD.search(c) else None)
                   for c in r]
            if sum(1 for p in per if p) >= 2:
                hdr_i, periods = i, per
                break
        if periods is None:
            continue
        labels = {(r[0] or "").replace(" ", "") for r in rows[hdr_i + 1:]}
        if "매출액" not in labels or "영업이익" not in labels:
            continue                      # Financial Summary 표가 아니다
        # 컬럼별 종류 — 그룹 헤더(연간/분기)가 있으면 **그걸 따른다**.
        cols_kind: list[str] = []
        if hdr_i > 0:
            grp = _expand_group(raw_rows[hdr_i - 1])
            if any("연간" in g or "분기" in g for g in grp):
                # 기간 줄에 라벨 칸이 없을 수 있으니 오른쪽 정렬로 맞춘다.
                # ⚠️ 그룹 줄이 컬럼 수를 못 채우면(colspan 해석 실패 등)
                # **부분 적용하지 않는다** — 일부만 맞으면 조용히 어긋난다.
                if len(grp) >= len(periods):
                    tail = grp[-len(periods):]
                    cols_kind = ["annual" if "연간" in g else
                                 "quarter" if "분기" in g else "" for g in tail]
        months = {p.split("/")[1] for p in periods if p}
        # 그룹 헤더가 없으면 월로 추정 — 연간 표는 12월만 있다.
        default_kind = "annual" if months <= {"12"} else "quarter"
        # ⚠️ **절대 인덱스로 맞추면 한 칸씩 밀린다**(2026-08-19 실측: 2026/03
        # 자리에 2025/12 값이 들어갔다). 실제 표는 헤더가 2행이라 기간 줄에는
        # 라벨 칸이 없는데 데이터 줄에는 있기 때문이다. 기간 목록과 '라벨을
        # 뺀 값들'을 순서대로 맞춘다 — 헤더가 1행이든 2행이든 같은 결과.
        cols = [p for p in periods if p]
        col_at = [i for i, p in enumerate(periods) if p]
        kinds = [(cols_kind[i] if i < len(cols_kind) and cols_kind[i]
                  else default_kind) for i in col_at]
        for r in rows[hdr_i + 1:]:
            name = (r[0] or "").replace(" ", "")
            if name not in _WANT:
                continue
            vals = r[1:]
            if len(r) == len(periods):
                # 헤더와 데이터의 칸 수가 같다 = 헤더에도 라벨 칸이 있다.
                vals = [r[i] for i in col_at]
            for per, cell, kd in zip(cols, vals, kinds):
                v = _num(cell)
                if v is not None:
                    out[kd].setdefault(per, {})[name] = v * _EOK
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
        if isinstance(c, dict) and c.get("_ver") == _PARSE_VER:
            return c
    out: dict = {"annual": {}, "quarter": {}}
    seen_rev = False
    req_err = ""          # 요청 실패는 파싱 사유로 덮이면 안 된다
    for freq in _FREQS:
        try:
            resp = requests.get(_URL_TMPL.format(code=code, freq=freq),
                                headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            # 이 페이지는 EUC-KR 계열을 내보낼 때가 있다 — 잘못 읽으면 라벨이
            # 깨져 '매출액' 매칭이 조용히 실패한다.
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
        except Exception as exc:                        # noqa: BLE001
            req_err = req_err or f"요청 실패({type(exc).__name__})"
            log.info("wisereport_fin: %s freq=%s 실패: %s", code, freq, exc)
            continue
        seen_rev = seen_rev or ("매출액" in resp.text)
        part = parse_financial_summary(resp.text)
        for kind in ("annual", "quarter"):
            for per, row in (part.get(kind) or {}).items():
                out[kind].setdefault(per, {}).update(row)
        if out["annual"] and out["quarter"]:
            break                       # 둘 다 얻었으면 더 두드리지 않는다
    if not (out.get("annual") or out.get("quarter")):
        _LAST_REASON[code] = (
            req_err if req_err else
            "HTML 에 '매출액' 은 있으나 표 파싱 실패" if seen_rev
            else "HTML 에 Financial Summary 없음(AJAX 의심)")
        log.info("wisereport_fin: %s — %s", code, _LAST_REASON[code])
        return None
    _LAST_REASON.pop(code, None)
    out["_ver"] = _PARSE_VER
    if _cache_write:
        _cache_write(ck, out)
    return out
