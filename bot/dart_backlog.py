"""DART 정기보고서 「매출 및 수주상황」 → 수주잔고(원). LLM 0·₩0·stdlib.

수주잔고는 **의무 공시 항목이 아니다.** 「II.사업의 내용 > 4.매출 및 수주상황」에
회사가 쓸지 말지 정하고, 쓰더라도 표 형태가 회사마다 다르다. 그래서 이 모듈은
2026-08-17 VM 프로브로 **실제 원문 22종목**을 2차에 걸쳐 받아본 뒤에 짰다
(추측 금지 — 실수 #12 '검증불가면 단정 금지'). 관측된 형태:

  A. 표 (다수) — 헤더에 잔고 열 + 총액/기초 열 + 납품/매출 열.
     A-1 `합 계` 행이 있으면 그 행을 검산해서 쓴다(가장 안전).
         삼성중공업·HD한국조선해양·HD현대중공업·한화에어로스페이스·
         LS ELECTRIC·현대오토에버·한화오션·테스·원익IPS·파크시스템스.
         티에스이는 `(단위 : 개, 백만원)` 로 수량까지 숫자여서 6열이 나온다.
     A-2 합계행이 **없으면** 데이터 행을 각각 검산해 잔고열을 합산한다.
         제이티(1행)·삼성E&A(3행)·효성중공업(3행).
  B. XBRL 주석형 — `수주잔고, 기말 N`(한전KPS) / `건설계약 수주잔고 N`(현대건설).
  C. 단일값형 — 방산 보안으로 잔액만. LIG넥스원 `구 분 수주잔액 … 245,781`.
  D. 잔고 단일열형 — 총액·납품 열이 없다. 테크윙 `구분 주요 고객 수주잔고금액`.
     항등식이 없어 **부문 합 = 합계**로 검산한다.
  G. 산문·인라인단위형 — 한국항공우주 주석 "수주잔고는 25,808,760백만원입니다".
     표(억원)와 주석(백만원)의 단위가 달라 문장에서 직접 읽는다.

**가져오지 않는 것**(전부 관측된 실물 — 조용히 틀린 숫자를 내느니 없음이 낫다):
  · 기재 생략 선언 — HD현대건설기계 "영업비밀 … 수주잔고 등의 기재는 생략".
    키워드는 있는데 값이 없다. 근처 숫자(계약금액 1,102억)를 집으면 대형 오류.
  · 계약잔액 ≠ 수주잔고 — 한전기술은 계약잔액만 쓰고 원문이 스스로
    "회사 전체의 계약 잔액과 다릅니다"라고 밝힌다. 다른 개념이므로 대입 금지.
  · 외화 표기 — 씨에스윈드 "수주잔고는 1,097백만USD". 환산 없이 원화 축에
    올리면 1400배 오차. 환율 소스를 붙이기 전까진 거부.
  · GS건설 — 국내/해외가 별도 표이고 헤더가 `계약잔액`(≠수주잔고)이다.
    두 표를 합쳐야 하는데 한전기술 선례가 있어 계약잔액을 잔고로 볼 수 없다.
    **22종목 중 유일한 미지원.**

⚠️ **검산이 이 모듈의 핵심 안전장치다.** 원문이 계산식을 직접 밝히고 있고
(`※ 수주잔고 = 기초계약잔액 + 신규계약액 - 기납품액`), 관측된 7건 전부
정확히 맞아떨어졌다. 그래서 합계행에서 뽑은 값이 그 항등식을 만족하지 못하면
**컬럼을 잘못 집은 것으로 보고 버린다**(None). 표 구조가 바뀌어도 조용히 틀린
숫자가 나가지 않는다.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("bot.dart_backlog")

# 단위 → 원 배수. `백만USD`·`천불` 류는 **의도적으로 없다** — 매칭되면 거부한다.
# ⚠️ `백만USD`·`천불` 류가 **없는 것이 곧 외화 차단**이다 — 매칭되지 않으면
# `_unit_mult` 가 None 을 돌려 그 표를 통째로 버린다. 환율 소스를 붙이기
# 전까지 여기에 외화를 추가하면 원화 축에 1400배 값이 올라간다(씨에스윈드
# "수주잔고는 1,097백만USD" 실측). 별도 가드 함수를 두면 죽은 코드가 되므로
# **이 표 자체가 가드**다.
_UNIT_MULT = {"원": 1.0, "천원": 1e3, "백만원": 1e6, "억원": 1e8,
              "십억원": 1e9, "조원": 1e12}
_UNIT_RE = re.compile(r"단위\s*[:：]?[^)\]]{0,20}?(조원|십억원|백만원|억원|천원|원)")
# 값 토큰: **콤마 필수**. `합 계 571,122 221,121 350,001 5. 위험관리` 에서
# 뒤따르는 절 번호 `5.` 나 연도 `2026` 을 값으로 오인하지 않기 위한 장치다.
_NUM_TOK = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})+\)?$")

# 헤더 판정 — 잔고 컬럼 라벨 / 시작잔고 라벨 / 납품 라벨.
# ⚠️ 긴 것부터. `수주잔` 은 효성중공업이 헤더를 `전기말 수주잔(2025.12.31)` 로
# 줄여 쓰기 때문에 필요하다(2026-08-17 2차 프로브).
_BAL_LABELS = ("기말수주잔고", "수주잔고금액", "수주잔고", "수주잔액",
               "기말잔고", "수주잔")
_OPEN_LABELS = ("기초계약잔액", "기초수주잔액", "이월 수주잔액", "이월수주잔액",
                "수주총액", "수주액", "수주잔", "기초")
# `매출액` 은 효성중공업이 기납품액 대신 쓰는 열이다. 넓어 보이지만 헤더에
# 잔고 라벨이 함께 있어야만 도달하고, 최종 관문은 어차피 **검산**이다.
_DELIV_LABELS = ("기납품액", "기납품", "납품액", "공사수익", "매출액")

# 검산 허용오차 1%. 0.5% 였는데 효성중공업이 0.78% 벗어난다 — 원문이 사유를
# 직접 밝힌다: "수주잔액은 수주 취소, 계약금액 변경 등으로 … 단순 가감 결과와
# 차이가 있을 수 있습니다". 컬럼을 잘못 집으면 오차가 %가 아니라 자릿수
# 단위라, 1% 로 넓혀도 가드는 그대로 유효하다.
_TOL = 0.01


def _to_num(tok: str) -> float | None:
    """`(9,538,147)` → -9538147.0. 괄호 = 회계식 음수."""
    t = tok.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    if not t or not t.lstrip("-").isdigit():
        return None
    v = float(t)
    return -v if neg else v


def _unit_mult(text: str, at: int) -> float | None:
    """`at` **앞쪽** 가장 가까운 `(단위 : X)`. 표 캡션은 항상 표 위에 온다.

    없으면 None — 단위 없이 스케일을 가정하면 100배 오차가 난다(백만원 vs
    억원). 못 정하면 값을 버리는 쪽이 옳다."""
    best = None
    for m in _UNIT_RE.finditer(text, 0, at):
        best = m
    if not best or at - best.end() > 4000:
        return None
    return _UNIT_MULT.get(best.group(1))


def _row_values(text: str, at: int, limit: int = 400) -> list[float]:
    """`at` 부터 이어지는 표 한 행의 숫자들. `-`(수량 자리표시자)는 건너뛰고,
    숫자도 `-` 도 아닌 토큰이 나오면 행이 끝난 것으로 본다."""
    out: list[float] = []
    for tok in text[at:at + limit].split():
        if tok == "-" or tok == "―":
            continue
        if not _NUM_TOK.match(tok):
            break
        v = _to_num(tok)
        if v is not None:
            out.append(v)
    return out


def _verify(vals: list[float]) -> float | None:
    """합계행 숫자들이 원문이 명시한 항등식을 만족하면 잔고를 반환.

    · 3열: 수주총액 − 기납품액 = 수주잔고
    · 4열: 기초잔액 + 신규 − 기납품액 = 수주잔고
    (기납품액이 `(12,248,487)` 처럼 이미 음수로 적힌 회사가 있어 **절대값**으로
    뺀다 — HD현대중공업 실측.)

    ⚠️ 이 검산이 유일한 컬럼 정합성 보증이다. 표 구조가 바뀌어 엉뚱한 열을
    집으면 산수가 안 맞고, 그때는 값을 내지 않는다(조용한 오답 방지)."""
    # 6열 = (수량, 금액) 쌍 3벌. 티에스이가 `(단위 : 개, 백만원)` 으로 수량까지
    # 숫자로 적어 6개가 나온다 — 홀수 인덱스(금액)만 뽑아 3열과 같게 본다.
    if len(vals) == 6:
        vals = vals[1::2]
    if len(vals) == 3:
        a, b, c = vals
        exp, got = a - abs(b), c
    elif len(vals) == 4:
        a, b, c, d = vals
        exp, got = a + b - abs(c), d
    else:
        return None
    if exp <= 0 or got <= 0:
        return None
    if abs(exp - got) > _TOL * max(abs(exp), abs(got)):
        return None
    return got


def _runs(seg: str) -> list[list[float]]:
    """평문 표에서 **연속된 숫자 묶음**(= 행 하나)들을 뽑는다.

    태그가 지워진 표는 행 구분자가 없어서 `품목명 … 숫자 숫자 숫자 다음품목명`
    처럼 이어진다. 숫자(또는 `-` 자리표시자)가 이어지는 동안을 한 행으로 보고,
    그 외 토큰이 나오면 행을 닫는다. 콤마 없는 토큰은 숫자로 치지 않으므로
    연도·건수·절번호에서 자연히 끊긴다."""
    out, cur = [], []
    for tok in seg.split():
        if tok in ("-", "―"):
            continue                      # 수량 자리표시자 — 행을 끊지 않는다
        if _NUM_TOK.match(tok):
            v = _to_num(tok)
            if v is not None:
                cur.append(v)
            continue
        if cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _parse_table(text: str) -> tuple[float, str] | None:
    """형태 A — 헤더(총액/기초 + 기납품 + 잔고)가 있는 표.

    ① `합 계` 행이 있으면 그 행을 검산해서 쓴다(가장 안전).
    ② 합계행이 **아예 없으면** 데이터 행들을 각각 검산해 잔고열을 합산한다
       — 제이티(1행)·삼성E&A(3행)·효성중공업(3행)이 이 형태다.
       ⚠️ 합계행이 **있는데 검산에 실패한 경우엔 행 합산으로 넘어가지 않는다.**
       합계가 있는데 못 맞췄다는 건 내 컬럼 모델이 틀렸다는 신호라, 거기서
       행을 더 파면 틀린 값을 그럴듯하게 만들어낸다."""
    for m in re.finditer("|".join(_BAL_LABELS), text):
        head = text[max(0, m.start() - 260):m.start()]
        if not any(k in head for k in _OPEN_LABELS):
            continue
        if not any(k in head for k in _DELIV_LABELS):
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        # 표는 헤더 뒤 가까이에서 끝난다. 넓게 잡으면 다음 표를 먹는다.
        seg = text[m.end():m.end() + 2500]
        tm = re.search(r"합\s*계", seg)
        if tm:
            got = _verify(_row_values(seg, tm.end()))
            if got is not None:
                return got * mult, "표·합계행"
            continue
        # ② 합계행 없음 — 행별 검산 후 합산. 3열 이상 묶음은 **전부** 통과해야
        #    한다(하나라도 어긋나면 표를 잘못 읽고 있다는 뜻).
        rows = [r for r in _runs(seg) if len(r) in (3, 4, 6)]
        if not rows:
            continue
        bals = [_verify(r) for r in rows]
        if any(b is None for b in bals):
            continue
        return sum(bals) * mult, "표·행합산"
    return None


def _parse_balance_column(text: str) -> tuple[float, str] | None:
    """형태 D — 잔고 **한 열만** 있는 표(테크윙).

        구분 주요 고객 수주잔고금액
        반도체 장비 Micron, SK hynix 등 55,508
        디스플레이장비 삼성디스플레이 등 4,760
        합계 - 60,268

    총액·납품액 열이 없어 A 의 항등식을 쓸 수 없다. 대신 **부문 합 = 합계**가
    검산이 된다 — 이게 맞으면 열을 제대로 집은 것이다."""
    for m in re.finditer(r"수주잔고금액|수주잔고\s*금액", text):
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = text[m.end():m.end() + 1200]
        tm = re.search(r"합\s*계", seg)
        if not tm:
            continue
        parts = [r[0] for r in _runs(seg[:tm.start()]) if len(r) == 1]
        total = _row_values(seg, tm.end())
        if not parts or len(total) != 1 or total[0] <= 0:
            continue
        if abs(sum(parts) - total[0]) > _TOL * total[0]:
            continue
        return total[0] * mult, "표·잔고열"
    return None


# 형태 G — 산문에 **단위가 값에 붙어** 나온다(한국항공우주 재무제표 주석:
# "고객과의 계약 관련 수주잔고는 25,808,760백만원입니다"). 표가 억원인데 주석은
# 백만원이라, 단위를 문장에서 직접 읽어야 한다.
# ⚠️ 통화를 `원` 으로 끝나게 강제하는 것이 외화 차단이다 — 씨에스윈드
# "1,097백만USD" 는 `백만원` 에 매칭되지 않아 자동으로 걸러진다.
_PROSE_RE = re.compile(
    r"수주잔(?:고|액)[^.]{0,20}?([\d]{1,3}(?:,\d{3})+)\s*"
    r"(조원|십억원|백만원|억원|천원|원)")


def _parse_prose(text: str) -> tuple[float, str] | None:
    m = _PROSE_RE.search(text)
    if not m:
        return None
    v = _to_num(m.group(1))
    mult = _UNIT_MULT.get(m.group(2))
    if v is None or v <= 0 or not mult:
        return None
    return v * mult, "산문·인라인단위"


def _parse_xbrl(text: str) -> tuple[float, str] | None:
    """형태 B — 재무제표 주석의 건설계약 공시. 라벨 바로 뒤가 값이다."""
    for pat in (r"수주잔고,\s*기말", r"건설계약\s*수주잔고"):
        m = re.search(pat, text)
        if not m:
            continue
        vals = _row_values(text, m.end(), limit=80)
        if not vals or vals[0] <= 0:
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        return vals[0] * mult, "주석·건설계약"
    return None


def _parse_single(text: str) -> tuple[float, str] | None:
    """형태 C — 방산 보안 등으로 잔액 한 칸만 공시.

    `구 분 수주잔액 제25기(2026년 2분기) 245,781` — 라벨과 값 사이에 기수
    표기가 끼므로 **첫 값 토큰까지 건너뛴다**. 다만 건너뛰는 구간에 다른 표가
    끼어들지 않도록 좁게(120자) 본다."""
    m = re.search(r"구\s*분\s*(?:수주잔액|수주잔고)", text)
    if not m:
        return None
    seg = text[m.end():m.end() + 120]
    nums = [t for t in seg.split() if _NUM_TOK.match(t)]
    if not nums:
        return None
    v = _to_num(nums[0])
    if v is None or v <= 0:
        return None
    mult = _unit_mult(text, m.start())
    if mult is None:
        return None
    return v * mult, "단일값"


def parse_backlog(text: str) -> dict | None:
    """정기보고서 평문 → {value(원), unit_src, form} 또는 None.

    None 의 의미는 **'없거나 확신할 수 없음'** 이며 둘을 구분하지 않는다 —
    호출부는 어느 쪽이든 패널을 생략해야 하기 때문이다(날조 금지)."""
    if not text:
        return None
    # ⚠️ '기재 생략' 선언을 따로 검사하지 않는다. 형식 파서 3종이 이미 전부
    #    거부하고(mutation 으로 확인), 별도 검사를 두면 **종속회사 한 곳의 생략
    #    문구가 지배회사 값을 죽이는** 오탐이 생긴다(한국항공우주 본문에 자회사
    #    '한국표면처리' 의 생략 문구가 있다). 값의 안전은 검산이 보장한다.
    # 순서 = 신뢰도 순. 검산 가능한 표가 먼저이고, 산문은 검산할 항등식이
    # 없으므로 **마지막**이다(파크시스템스는 표 112,509백만원과 주석의 "약
    # 1,023억원"이 기준일이 달라 다른데, 표가 먼저 잡혀 기준일 값이 이긴다).
    for fn in (_parse_table, _parse_balance_column, _parse_xbrl,
               _parse_single, _parse_prose):
        try:
            got = fn(text)
        except Exception as exc:            # 한 형태의 실패가 나머지를 막지 않게
            log.debug("dart_backlog: %s 실패: %s", fn.__name__, exc)
            continue
        if not got:
            continue
        value, form = got
        return {"value": value, "form": form}
    return None


def backlog_for(dart, ticker: str, year: int, reprt_code: str) -> float | None:
    """해당 분기 정기보고서의 수주잔고(원). 없으면 None.

    ⚠️ 원문을 40MB 로 받는다 — 「매출 및 수주상황」은 목차상 II.사업의 내용
    뒤라 기본 3MB 상한 밖으로 밀리고, 그러면 **공시하는 회사도 '없음'으로
    오판된다**(2026-08-17 프로브로 확인)."""
    if not dart:
        return None
    try:
        from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
        rep = dart.find_periodic_report(ticker, year, reprt_code)
        if not rep or not rep.get("rcept_no"):
            return None
        text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                               max_bytes=_DOC_TEXT_MAX_FULL)
        got = parse_backlog(text or "")
        return got["value"] if got else None
    except Exception as exc:
        log.debug("dart_backlog: %s %s/%s: %s", ticker, year, reprt_code, exc)
        return None
