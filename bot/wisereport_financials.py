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
#   v3 = 헤더의 **탭별 대체 컬럼 세트**에서 연속 구간을 골라 컬럼 매핑
_PARSE_VER = 4   # v4: EV/EBITDA 행 추가(비율 — _EOK 스케일 금지)

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
# ⚠️ 비율 행 — **억원 스케일(_EOK) 금지**. 같은 dict 에 단위가 다른 키가
# 섞이므로(#34) 소비자는 이름으로 갈라 읽는다(EV/EBITDA = 배).
_RATIO_WANT = ("EV/EBITDA",)
_EOK = 1e8          # 억원 → 원


def _row_name(lbl: str):
    """행 라벨 → canonical 이름. 대상이 아니면 None.

    EV/EBITDA 는 원천이 '(배)' 류 접미를 붙일 수 있어 startswith 로 잡고
    canonical 로 통일한다 — 없으면 그냥 None(빈칸 유지)이라 안전하다."""
    n = (lbl or "").replace(" ", "")
    if n in _WANT:
        return n
    if n.startswith("EV/EBITDA"):
        return "EV/EBITDA"
    return None


def _pm(per: str) -> int:
    """'2026/03' → 절대 개월수(정렬·간격 계산용)."""
    y, m = per.split("/")
    return int(y) * 12 + int(m)


def _pick_run(periods: list[str], n: int, step: int) -> list[str] | None:
    """기간 목록에서 **간격이 정확히 `step`개월인 연속 n칸**을 찾는다.

    ⚠️ 실측(2026-08-19 005940 원문): 헤더 줄에는 **탭별 대체 컬럼 세트가 전부**
    들어 있다(헤더 20칸 vs 데이터 8칸). 숨김 속성 같은 표식은 없다 —
    전부 `class="sub line"`. 실제로 그려지는 세트는 **연속 구간**이다:
      연간 2022/12·2023/12·2024/12·2025/12 (12개월 간격)
      분기 2025/06·2025/09·2025/12·2026/03 (3개월 간격)
    나머지 세트는 2025/03→2025/12 처럼 간격이 깨져 자동으로 걸러진다.
    (교차확인: 그 매핑일 때 FY2025 영업이익 14,206억이 DART 값과 일치.)"""
    for i in range(len(periods) - n + 1):
        w = [_pm(x) for x in periods[i:i + n]]
        if all(w[j + 1] - w[j] == step for j in range(n - 1)):
            return periods[i:i + n]
    return None


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


def _group_spans(row_html: str) -> list[tuple[str, int]]:
    """그룹 헤더 줄 → [('연간', 4), ('분기', 4)]. 라벨 칸(colspan 없음)은 제외."""
    out: list[tuple[str, int]] = []
    for attrs, body in _CELL_SPAN.findall(row_html):
        m = _COLSPAN.search(attrs or "")
        if not m:
            continue                     # rowspan 라벨 칸
        out.append((_text(body), int(m.group(1))))
    return out


def parse_financial_summary(html: str) -> dict:
    """{'annual': {'2025/12': {...}}, 'quarter': {'2026/03': {...}}} (원 단위).

    표 id 에 기대지 않는다 — **내용으로** 찾는다(사이트가 id 를 바꿔도 산다).

    ⚠️ 컬럼 매핑(2026-08-19 원문 실측): 헤더 줄에는 탭별 **대체 컬럼 세트가
    전부** 들어 있어 헤더 20칸 vs 데이터 8칸이다. 숨김 표식은 없다. 대신
    그룹 헤더가 `연간 colspan=4 · 분기 colspan=4` 로 **몇 칸씩인지**를 주고,
    실제 그려지는 세트는 간격이 일정한 **연속 구간**이다. 둘을 합치면
    유일하게 결정된다(`_pick_run`). 결정 못 하면 그 표는 버린다 —
    잘못 맞춘 숫자를 내보내느니 아무것도 안 내보낸다."""
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
        data_rows = [r for r in rows[hdr_i + 1:]
                     if _row_name(r[0]) is not None]
        labels = {(r[0] or "").replace(" ", "") for r in rows[hdr_i + 1:]}
        if "매출액" not in labels or "영업이익" not in labels:
            continue                      # Financial Summary 표가 아니다
        all_per = [p for p in periods if p]
        ncols = max((len(r) - 1) for r in data_rows) if data_rows else 0

        # ── (A) 그룹 헤더가 칸 수를 알려주는 경우 = 실제 사이트 구조 ──
        col_map: list[tuple[str, str]] = []      # [(period, kind)] 데이터 순서대로
        spans = _group_spans(raw_rows[hdr_i - 1]) if hdr_i > 0 else []
        spans = [(g, n) for g, n in spans if "연간" in g or "분기" in g]
        if spans and sum(n for _g, n in spans) == ncols:
            for g, n in spans:
                kind = "annual" if "연간" in g else "quarter"
                run = _pick_run(all_per, n, 12 if kind == "annual" else 3)
                if run is None:
                    col_map = []
                    break
                col_map.extend((per, kind) for per in run)
        # ── (B) 그룹 헤더가 **아예 없는** 단순 표(한 종류) ──
        # ⚠️ 그룹 헤더가 있는데 (A) 가 실패한 경우엔 여기로 내려오면 안 된다 —
        # 칸 수가 우연히 맞으면 틀린 매핑을 조용히 내보낸다.
        if not spans and not col_map and len(all_per) == ncols:
            months = {p.split("/")[1] for p in all_per}
            kind = "annual" if months <= {"12"} else "quarter"
            col_map = [(per, kind) for per in all_per]
        if not col_map:
            continue                     # 못 맞추면 버린다(틀린 매핑 금지)

        for r in data_rows:
            name = _row_name(r[0])
            for (per, kind), cell in zip(col_map, r[1:]):
                v = _num(cell)
                if v is not None:
                    out[kind].setdefault(per, {})[name] = (
                        v * _EOK if name in _WANT else v)
    return out


def latest_ev_ebitda(summary):
    """가장 최근 EV/EBITDA — (값, '2025/12 기준') 또는 None.

    연간 우선(FnGuide 산정 기준이 연간), 없으면 분기. 값이 FnGuide 산정
    시점 기준이라 화면은 이 라벨을 반드시 같이 싣는다(#34)."""
    for kind in ("annual", "quarter"):
        bucket = (summary or {}).get(kind) or {}
        for per in sorted(bucket, key=_pm, reverse=True):
            v = bucket[per].get("EV/EBITDA")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v), f"{per} 기준"
    return None


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


def ev_row_dump(html: str) -> list:
    """EV/EBITDA 행이 있는 표마다 (헤더 원문 셀, EV 행 원문 셀) — **프로브
    전용**(값 가공 없음). (E)/(P) 칸을 **거르지 않고 그대로** 찍는다.

    ⚠️ 왜(사용자 2026-08-27 "네이버꺼 실시간을 가져오면 거의 정확할 텐데"):
    본 파서는 매출 보강용이라 추정 칸((E))을 구조적으로 배제한다 — 그래서
    EV/EBITDA 도 최신 확정 연도에 묶였을 수 있다. 어느 칸이 존재하고 어느
    칸이 '네이버가 최신으로 갱신하는 값'인지는 **원문만 답한다**(#109·#155).
    """
    out = []
    for tbl in _TABLE.findall(html or ""):
        rows = _ROW.findall(tbl)
        texts = [[_text(c) for c in _CELL.findall(r)] for r in rows]
        ev_i = next((i for i, r in enumerate(texts)
                     if r and (r[0] or "").replace(" ", "").startswith("EV/EBITDA")),
                    None)
        if ev_i is None:
            continue
        hdr = next((r for r in texts[:ev_i]
                    if sum(1 for c in r if _PERIOD.search(c)) >= 2), [])
        out.append((hdr, texts[ev_i]))
    return out


_PROBE_VER = 2   # v2: cF1001 에 EV 행 없음(078340 실측) → 후보 페이지 스윕

# ── EV/EBITDA — c1010001 '주요지표' 표 (프로브 v2 실측 2026-08-27) ────
# 078340 실측: 헤더 ['주요지표','2025/12(A)','2026/12(E)'] · EV행
# ['EV/EBITDA','22.72','10.49']. (E) 칸이 네이버가 최신 주가·컨센서스로
# 갱신하는 값이다(사용자 "실시간을 가져오면 그게 거의 정확한 값일텐데").
# FnGuide 직접 접근(comp.fnguide.com)은 1,796자 스텁만 온다(봇 차단) —
# 재시도하지 말 것(#25 실측 기록). cF1001 요약표엔 이 행 자체가 없다.
_URL_C1010001 = ("https://navercomp.wisereport.co.kr/v2/company/"
                 "c1010001.aspx?cmp_cd={code}")
_PERIOD_AE = re.compile(r"\b\d{4}/\d{2}\s*\([AE]\)")
_EV_CACHE_VER = 1


def parse_key_metrics_ev(html: str):
    """'주요지표' 표의 EV/EBITDA → (값, '2026/12(E) 기준') 또는 None.

    **가장 오른쪽의 값 있는 칸**(=최신, 보통 (E))을 집는다 — (E) 가 비면
    (A) 로 자연 폴백된다. 헤더 라벨을 그대로 실어 화면이 기준을 밝힌다(#34).
    """
    for tbl in _TABLE.findall(html or ""):
        rows = [[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(tbl)]
        ev = next((r for r in rows
                   if r and (r[0] or "").replace(" ", "").startswith("EV/EBITDA")),
                  None)
        if not ev:
            continue
        hdr = next((r for r in rows
                    if sum(1 for c in r if _PERIOD_AE.search(c)) >= 1), None)
        if not hdr:
            continue
        best = None
        for label, cell in zip(hdr[1:], ev[1:]):
            v = _num(cell)
            if v is not None and _PERIOD_AE.search(label):
                best = (float(v), f"{label.strip()} 기준")
        if best:
            return best
    return None


def fetch_ev_ebitda(stock_code: str):
    """c1010001 에서 EV/EBITDA — (값, 기준라벨) 또는 None. 12h 디스크 캐시.

    ⚠️ 캐시 봉투에 `_ver` — 파서를 고치고 버전을 안 올리면 옛 결과가
    그대로 서빙된다(#216, 이 레포에서 여러 번 겪은 병)."""
    code = str(stock_code or "").split(".")[0].strip()
    if not (len(code) == 6 and code.isdigit()):
        return None
    try:
        from bot.finviz_client import _cache_write, _cached
    except Exception:
        _cache_write = _cached = None
    ck = f"wisereport_ev_{code}.json"
    if _cached:
        c = _cached(ck, ttl=_CACHE_TTL_H * 3600)
        if isinstance(c, dict) and c.get("_ver") == _EV_CACHE_VER:
            got = c.get("got")
            return tuple(got) if got else None
    try:
        resp = requests.get(_URL_C1010001.format(code=code),
                            headers=_HEADERS, timeout=_TIMEOUT)
        got = parse_key_metrics_ev(resp.text)
    except Exception as exc:                            # noqa: BLE001
        log.debug("wisereport_ev: %s 실패: %s", code, exc)
        return None
    if _cache_write:
        # 빈 결과도 캐시한다(12h) — 행이 없는 종목을 매 조회마다 76KB 씩
        # 다시 받지 않는다. 원천 장애와 구분 못 하는 비용은 12h 로 유계.
        _cache_write(ck, {"_ver": _EV_CACHE_VER, "got": list(got) if got else None})
    return got   # v2: cF1001 에 EV 행 없음(078340 실측) → 후보 페이지 스윕

# EV/EBITDA 를 실을 법한 네이버/FnGuide 표면 후보 — 존재는 **프로브 실측**으로
# 판정한다(#25 이름이 아니라 실측). 404/빈손이어도 비용은 출력 한 줄이다.
_EV_PROBE_URLS = (
    ("wisereport c1010001(기업현황)",
     "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}"),
    ("wisereport c1040001(투자지표)",
     "https://navercomp.wisereport.co.kr/v2/company/c1040001.aspx?cmp_cd={code}"),
    ("wisereport cF3002 Y",
     "https://navercomp.wisereport.co.kr/v2/company/cF3002.aspx?cmp_cd={code}&fin_typ=0&freq_typ=Y"),
    ("wisereport cF4002 Y",
     "https://navercomp.wisereport.co.kr/v2/company/cF4002.aspx?cmp_cd={code}&fin_typ=0&freq_typ=Y"),
    ("fnguide SVD_Main(Snapshot)",
     "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"),
    ("fnguide SVD_Invest(투자지표)",
     "https://comp.fnguide.com/SVO2/ASP/SVD_Invest.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=105&stkGb=701"),
    ("fnguide SVD_FinanceRatio(재무비율)",
     "https://comp.fnguide.com/SVO2/ASP/SVD_FinanceRatio.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=104&stkGb=701"),
)


def ev_snippets(html: str, n: int = 3) -> list:
    """'EV/EBITDA' 출현 지점의 원문 ±180자 — 표가 아니라 스크립트/JSON 에
    있어도 보이게. 표본이 있어야 파서를 짤 수 있다(#109·#155)."""
    out = []
    i = 0
    while len(out) < n:
        i = (html or "").find("EV/EBITDA", i)
        if i < 0:
            break
        out.append(re.sub(r"\s+", " ", html[max(0, i - 60):i + 180]))
        i += 1
    return out


def _main(argv=None):
    """`python -m bot.wisereport_financials --ev <6자리코드>` — 후보 표면을
    전부 훑어 EV/EBITDA 행(표) 또는 원문 발췌(스크립트)를 찍는다. VM 전용
    (네이버·FnGuide 접근 필요). 시작 줄에 버전(#21)."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "--ev" or len(args) < 2:
        print("사용법: python -m bot.wisereport_financials --ev <6자리코드>")
        return 2
    code = args[1].split(".")[0]
    print(f"■ EV/EBITDA 원문 프로브 v{_PROBE_VER} (파서 v{_PARSE_VER}) — {code}")
    found = 0
    for freq in _FREQS:
        try:
            resp = requests.get(_URL_TMPL.format(code=code, freq=freq),
                                headers=_HEADERS, timeout=_TIMEOUT)
            dumps = ev_row_dump(resp.text)
        except Exception as exc:                        # noqa: BLE001
            print(f"  cF1001 freq={freq}: 요청 실패 {type(exc).__name__}: {exc}")
            continue
        for hdr, cells in dumps:
            found += 1
            print(f"  cF1001 freq={freq} 헤더: {hdr}")
            print(f"  cF1001 freq={freq} EV행: {cells}")
    if not found:
        print("  cF1001: 전 freq 에 EV/EBITDA 행 없음 (v1 실측과 동일)")
    for label, tmpl in _EV_PROBE_URLS:
        try:
            resp = requests.get(tmpl.format(code=code), headers=_HEADERS,
                                timeout=_TIMEOUT)
            if resp.encoding and resp.encoding.lower() in ("iso-8859-1",):
                resp.encoding = resp.apparent_encoding
            html = resp.text
        except Exception as exc:                        # noqa: BLE001
            print(f"  {label}: 요청 실패 {type(exc).__name__}: {exc}")
            continue
        dumps = ev_row_dump(html)
        hit = "EV/EBITDA" in html
        print(f"  {label}: {resp.status_code} · {len(html):,}자 · "
              f"표 행 {len(dumps)}개 · 문자열 출현 {'있음' if hit else '없음'}")
        for hdr, cells in dumps[:2]:
            found += 1
            print(f"    헤더: {hdr}")
            print(f"    EV행: {cells}")
        if hit and not dumps:
            for sn in ev_snippets(html):
                print(f"    발췌: {sn}")
    if not found:
        print("  ❌ 표 형태의 EV/EBITDA 행은 어디에도 없다 — '발췌' 줄이"
              " 있으면 그 markup 을 보고 파서를 짠다")
    else:
        got = latest_ev_ebitda(fetch_financial_summary(code))
        print(f"  현재 파서가 집는 값: {got}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
