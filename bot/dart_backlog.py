"""DART 정기보고서 「매출 및 수주상황」 → 수주잔고(원). LLM 0·₩0·stdlib.

수주잔고는 **의무 공시 항목이 아니다.** 「II.사업의 내용 > 4.매출 및 수주상황」에
회사가 쓸지 말지 정하고, 쓰더라도 표 형태가 회사마다 다르다. 그래서 이 모듈은
2026-08-17 VM 프로브로 **실제 원문 16종목**을 받아본 뒤에 짰다(추측 금지 —
실수 #12 '검증불가면 단정 금지'). 관측된 형태:

  A. 표·합계행형 (7/16) — 압도적 다수. 헤더에 `수주총액|기초계약잔액|이월
     수주잔액|기초수주잔액` + `기납품액` + `수주잔고`, 아래 `합 계` 행.
     예) 삼성중공업 `합 계 571,122 221,121 350,001` (억원)
         HD한국조선해양 `합 계 - 82,236,880 - 33,642,412 - 17,070,572 - 98,808,720`
     ※ 수량/금액 쌍이라 `-` 자리표시자가 섞인다.
  B. XBRL 주석형 (2/16) — 재무제표 주석의 건설계약 공시.
     예) 한전KPS `수주잔고, 기말 2,177,526,372,462` (원)
         현대건설 `건설계약 수주잔고 103,983,136` (백만원)
  C. 단일값형 (1/16) — 방산 보안으로 잔액만 공시.
     예) LIG넥스원 `구 분 수주잔액 제25기(2026년 2분기) 245,781` (억원)

**가져오지 않는 것**(전부 관측된 실물 — 조용히 틀린 숫자를 내느니 없음이 낫다):
  · 기재 생략 선언 — HD현대건설기계 "영업비밀 … 수주잔고 등의 기재는 생략".
    키워드는 있는데 값이 없다. 근처 숫자(계약금액 1,102억)를 집으면 대형 오류.
  · 계약잔액 ≠ 수주잔고 — 한전기술은 계약잔액만 쓰고 원문이 스스로
    "회사 전체의 계약 잔액과 다릅니다"라고 밝힌다. 다른 개념이므로 대입 금지.
  · 외화 표기 — 씨에스윈드 "수주잔고는 1,097백만USD". 환산 없이 원화 축에
    올리면 1400배 오차. 환율 소스를 붙이기 전까진 거부.
  · 합계행 없는 표 — 삼성E&A·GS건설·효성중공업. 행 경계가 평문에서 모호해
    합산이 위험하다(헤더가 안 보이는 표본뿐이라 근거 부족).

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
_BAL_LABELS = ("기말수주잔고", "수주잔고", "수주잔액", "기말잔고")
_OPEN_LABELS = ("기초계약잔액", "기초수주잔액", "이월 수주잔액", "이월수주잔액",
                "수주총액", "기초")
_DELIV_LABELS = ("기납품액", "기납품", "공사수익")

_TOL = 0.005      # 검산 허용오차 0.5% — 억원 단위 반올림 흡수


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


def _parse_table(text: str) -> tuple[float, str] | None:
    """형태 A — 헤더(총액/기초 + 기납품 + 잔고) 뒤 `합 계` 행."""
    for m in re.finditer("|".join(_BAL_LABELS), text):
        head = text[max(0, m.start() - 260):m.start()]
        if not any(k in head for k in _OPEN_LABELS):
            continue
        if not any(k in head for k in _DELIV_LABELS):
            continue
        # 합계행은 헤더 뒤 가까이에 있다. 멀면 다른 표의 합계를 집는다.
        seg = text[m.end():m.end() + 4000]
        tm = re.search(r"합\s*계", seg)
        if not tm:
            continue
        vals = _row_values(seg, tm.end())
        got = _verify(vals)
        if got is None:
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        return got * mult, "표·합계행"
    return None


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
    for fn in (_parse_table, _parse_xbrl, _parse_single):
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
