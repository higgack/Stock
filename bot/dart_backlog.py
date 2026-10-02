"""DART 정기보고서 「매출 및 수주상황」 → 수주잔고(원). LLM 0·₩0·stdlib.

수주잔고는 **의무 공시 항목이 아니다.** 「II.사업의 내용 > 4.매출 및 수주상황」에
회사가 쓸지 말지 정하고, 쓰더라도 표 형태가 회사마다 다르다. 그래서 이 모듈은
2026-08-17 VM 프로브로 **실제 원문 54종목**을 3차에 걸쳐 받아본 뒤에 짰다
(추측 금지 — 실수 #12 '검증불가면 단정 금지'). 관측된 형태:

  A. 표 — 헤더에 잔고 열 + 총액/기초 열 + 납품/매출/수익인식 열.
     A-1 `합 계` 행이 있으면 그 행을 검산해서 쓴다(가장 안전).
     A-2 합계행이 **없으면** 데이터 행을 각각 검산해 잔고열을 합산한다.
     ※ 티에스이는 `(단위 : 개, 백만원)` 로 수량까지 숫자여서 6열이 나온다 —
       홀수 인덱스(금액)만 뽑는다. 안 그러면 수량 162,577개가 잔고가 된다.
  B. XBRL 주석형 — `수주잔고, 기말 N`(한전KPS) / `건설계약 수주잔고 N`(현대건설).
  C. 단일값형 — 방산 보안으로 잔액만. LIG넥스원 `구 분 수주잔액 … 245,781`.
  D. 잔고 단일열형 — 총액·납품 열이 없다. 테크윙 `구분 주요 고객 수주잔고금액`.
     항등식이 없어 **부문 합 = 합계**로 검산한다.
  G. 산문·인라인단위형 — 한국항공우주 주석 "수주잔고는 25,808,760백만원입니다".
     표(억원)와 주석(백만원)의 단위가 달라 문장에서 직접 읽는다.
  H. 전치형 — 항목이 **행**이다(`기초계약잔액 / 신규계약액 / 수익 / 기말계약잔액`).
     케이씨텍(당반기·전기 2열)·코오롱글로벌(6부문+합계 7열).
     어느 열을 쓸지는 **열 개수**로 정한다 — 2열이면 첫 열(당반기), 3열 이상이면
     마지막 열(합계). 부문별로도 항등식이 성립해 검산만으론 못 가른다.
  I. 건설 도급표 — `기본도급액 / 완성공사액 / 계약잔액` + 합계행.
     대우건설 53.4조·GS건설(국내) 48.5조.

⚠️ **평문 표 읽기의 두 함정**(3차 프로브가 드러냄 — 둘 다 실제로 값을 잃고 있었다):
  · **표의 끝을 못 잡으면 다음 표를 먹는다.** 넥스틴은 수주표 뒤에 붙은 신용위험
    표가 4열 묶음으로 잡혀 검산이 깨졌고, 그 탓에 멀쩡한 표가 버려졌다.
    → `_cut_table` 이 각주(※·주N)·다음 캡션·절번호에서 자른다.
  · **콤마를 필수로 하면 소액이 사라진다.** 억원 단위 표는 값이 세 자리라
    엠플러스 `합 계 - 1,461 - 830 - 631` 의 830·631 이 끊겼다.
    → 콤마 없는 1~4자리도 값으로 받고, 정합성은 검산에 맡긴다.

**가져오지 않는 것**(전부 관측된 실물 — 조용히 틀린 숫자를 내느니 없음이 낫다):
  · 기재 생략 선언 — HD현대건설기계·HPSP "영업비밀 … 기재는 생략".
    키워드는 있는데 값이 없다. 근처 숫자(계약금액)를 집으면 대형 오류.
  · 계약잔액이라도 **도급표가 아니면** 안 쓴다 — 한전기술은 원문이 스스로
    "회사 전체의 계약 잔액과 다릅니다"라고 밝힌다. `계약잔액` 이라는 단어는
    XBRL 주석마다 나오므로 헤더 구성(기본도급액+완성공사액)으로 가른다.
  · 외화 표기 — 씨에스윈드 "수주잔고는 1,097백만USD". 환산 없이 원화 축에
    올리면 1400배 오차. 환율 소스를 붙이기 전까진 거부.
  · 잔고가 전부 `-` 인 표 — 한미반도체·케이엠더블유는 완료 계약만 실어 잔고가
    비어 있다. 형식은 맞지만 낼 값이 없다.
  · 두산에너빌리티 — 프로젝트별 표가 **두 개**이고 행이 겹친다(UAE BNPP 등).
    회사 전체 합계가 없어 합산하면 부분값이거나 이중계상이다.
  · 에스에프에이 — `신규수주 / 매출 / 기말잔고` 3열이라 기초가 없어 항등식을
    세울 수 없다(수직 합만 성립). 검산 없이 낼 수는 없다.
  · 합계행이 **2값** — 항등식을 세울 두 번째 항이 없다. 열 뜻을 추측해
    배정하면 스케일이 아니라 **의미**가 틀리고 검산도 못 잡는다.
  · 항등식은 맞는데 잔고가 **0 이하** — 영풍(기납품이 수주총액을 넘었다).
    화면의 '잔고' 가 아니므로 안 싣고, 진단은 `잔고0이하`(고칠 것 아님)다.

⚠️ **수량·건수·비중 열이 섞이면 열 개수가 어긋난다**(2026-08-21 스윕:
`형식미지원 · 합계행 5값/4값`). 추측해 배정하지 않고 **연속 부분열**로 같은
항등식이 성립하는지 보고, 성립하는 잔고값이 **유일할 때만** 쓴다 — 둘 이상
나오면 어느 열인지 모르는 것이므로 거부한다(빈칸 > 틀린 숫자).

⚠️ **검산이 이 모듈의 핵심 안전장치다.** 원문이 계산식을 직접 밝히고 있고
(`※ 수주잔고 = 기초계약잔액 + 신규계약액 - 기납품액`), 관측된 7건 전부
정확히 맞아떨어졌다. 그래서 합계행에서 뽑은 값이 그 항등식을 만족하지 못하면
**컬럼을 잘못 집은 것으로 보고 버린다**(None). 표 구조가 바뀌어도 조용히 틀린
숫자가 나가지 않는다.
"""
from __future__ import annotations

import html as _html
import json as _json
import logging
import os
import re
import threading
from pathlib import Path as _Path

log = logging.getLogger("bot.dart_backlog")

# 단위 → 원 배수. `백만USD`·`천불` 류는 **의도적으로 없다** — 매칭되면 거부한다.
# ⚠️ `백만USD`·`천불` 류가 없는 것이 외화 차단의 **절반**이다 — 환율 소스를
# 붙이기 전까지 여기에 외화를 추가하면 원화 축에 1400배 값이 올라간다(씨에스윈드
# "수주잔고는 1,097백만USD" 실측). 나머지 절반은 `_unit_mult` 가 표 **자기
# 캡션**을 보는 것이다 — 2026-10-02 까지 이 주석은 "매칭되지 않으면 None 을
# 돌려 그 표를 버린다" 고 적었는데, `_unit_mult` 는 금액이 아닌 가까운 캡션을
# **건너뛰고 앞 표의 원화 캡션을 빌려** 썼다(재현: `(단위 : 천USD)` 표가
# `(단위 : 백만원)` 매출 표 뒤에 오면 6,000천USD → 60억원). 표 하나로는
# 가드가 안 된다(#55 설명이 코드와 어긋나면 버그).
_UNIT_MULT = {"원": 1.0, "천원": 1e3, "백만원": 1e6, "억원": 1e8,
              "십억원": 1e9, "조원": 1e12,
              # ⚠️ `원` 을 생략한 캡션이 실재한다 — `(단위 : 백만)`·
              # `(단위 : 억)`. 옛 정규식은 `원` 을 **필수**로 봐서 이런 표는
              # 단위를 못 정하고 통째로 버려졌다(2026-08-21 미수집 사유 중
              # `단위없음` 이 최대 버킷이었다).
              "조": 1e12, "십억": 1e9, "백만": 1e6, "억": 1e8, "천": 1e3}
# ⚠️ 뒤 lookahead 가 핵심이다. `(단위 : 천주)`(주식 수)·`(단위 : 백만달러)`
# 를 금액 단위로 읽으면 **스케일이 통째로 틀린다** — 검산은 열 사이 항등식만
# 보므로 스케일 오류는 그대로 통과한다(조용한 오답). 단위 뒤에 한글·영문·
# 통화기호가 붙으면 우리가 아는 단위가 아니다.
# ⚠️ 차단 규칙이 **토큰에 원이 있느냐**로 갈린다(2026-09-04 독립 리뷰):
#   · `원` 계열(`억원`·`백만 원`) = 통화가 이미 확정 → 붙은 글자만 차단.
#     공백을 넘어 차단하면 `(단위 : 억원 기준)`·`(단위 : 백만원 미만 절사)`
#     같은 **정상 캡션이 통째로 죽는다**(내가 이 커밋에서 실제로 냈다 —
#     0.35조짜리 표가 `캡션 미지원` 으로 찍혔다).
#   · 맨 스케일(`백만`·`천`) = 통화 미확정 → **공백을 넘어** 차단해야
#     `(단위 : 백만 달러)`(1,400배)·`(단위 : 천 주)` 를 금액으로 안 읽는다.
# 띄어쓴 `백만 원` 을 원 계열에 넣지 않으면 `백만` 이 차단당해 물러나며 맨
# `원` 이 잡혀 **1.0**(100만배 오차)이 된다 — 두 갈래가 한 세트다.
# ⚠️ 맨 스케일 차단은 한글·영문만 보고 **단위 기호**를 놓쳤다(2026-10-02 영풍
# 000670 실측 캡션 `(단위 : M/T, 천㎡, 천개 / 백만원)` — `천㎡` 의 `천` 을 금액
# 단위로 읽어 1,000배 작은 값이 될 뻔했다. 검산 실패로 가려져 있었을 뿐이다).
# ㎡·㎏·㎥ 류는 낱개로 늘어놓지 않고 **CJK 호환 블록**(U+3300–33FF, 사각형
# 단위 기호 전부)으로 막는다 — 목록은 다음 기호를 못 잡는다(#24).
# ⚠️ 그리고 같은 캡션에 `원` 이 붙은 금액 단위가 **뒤에** 있으면 맨 스케일은
# 수량 쪽이다(`(단위 : 천, 백만원)`) — 그 원 단위를 쓴다. 뒤를 보는 창은
# 캡션 길이로 묶는다(괄호 없는 산문에서 끝없이 훑지 않게, #71). 이건 **의미
# 규칙**이다 — 실측 캡션이 요구한 게 아니다(영풍은 기호 차단으로 풀린다, #165):
# 맨 스케일은 통화를 말하지 않고 `원` 은 말한다.
# ⚠️ 금액 토큰 앞 창은 **40자**다 — 다른 두 캡션 창(`_OWN_CAP_RE`·
# `_CAP_ANY_RE`)과 같다(#38). 20 이던 첫 판은 `(단위 : 중량-천톤, 면적-천㎡,
# 수량-천개, 금액-백만원)` 의 `백만원`(25자 뒤)을 못 읽었다(독립 리뷰 M1).
_UNIT_RE = re.compile(
    r"단위\s*[:：]?[^)\]]{0,40}?"
    r"(?:(조\s*원|십억\s*원|백만\s*원|억\s*원|천\s*원|원)(?![가-힣A-Za-z$])"
    r"|(조|십억|백만|억|천)(?!\s*[가-힣A-Za-z$\u3300-\u33ff%‰])"
    r"(?![^)\]]{0,40}?(?:조|십억|백만|억|천)\s*원))")
# 표 **자기** 캡션 — 콜론까지 있어야 캡션이다. `(단위당 원가)` 같은 낱말을
# 캡션으로 보면 머리행의 괄호 하나가 멀쩡한 원화 표를 빈칸으로 만든다(#146).
# ⚠️ 못 보는 축(#274): 콜론 없는 비금액 캡션(`(단위 천USD)`)은 여기 안 걸려
# 앞 표 캡션을 여전히 빌린다. 그리고 **앞 표의** 원화 캡션밖에 없는 표가 머리행
# 소캡션(`수량(단위 : 대)`)만 가지면 그 소캡션을 자기 캡션으로 보고 버린다 —
# 실측 사례는 없다(같은 표의 소캡션·연속 캡션은 `_unit_mult` 가 거른다).
_OWN_CAP_RE = re.compile(r"\(\s*단위\s*[:：][^)]{0,40}\)")

# 캡션 귀속 판정의 **표 경계** — 그 사이에 있으면 두 자리는 다른 표다: 합계행·
# 각주·윗 절 제목(`4. 매출` · `나. 수주`), 그리고 숫자 행(`_spans_tables`).
# 캡션 자체(`(단위`)는 경계가 아니다 — 캡션은 표의 머리이지 앞 표의 끝이
# 아니다(그래서 `_TABLE_END` 를 그대로 못 쓴다).
_TABLE_BREAK = re.compile(
    r"[※☞]|합\s*계|\d+\s*\.\s*[가-힣]{2,}|[가-하]\s*\.\s+[가-힣]")
# 아래 항목 머리(`(1) 국내` · `1) 해외`) — 표 경계는 아니지만(절 캡션이 아래
# 항목 표까지 덮는다) 캡션 묶음은 끊는다: 그 뒤 캡션은 아래 항목 표의 것이다.
# ⚠️ 앞이 공백(또는 처음)일 때만 — 머리행 각주 표식(`수주총액(주1)`·`(1)`)의
# `1)` 을 항목 머리로 읽으면 같은 표의 소캡션이 자기 캡션이 돼 표가 빈칸이 된다
# (2026-10-02 셀프리뷰 재현). `가)`·`①` 꼴 항목은 못 본다(#274).
_SUBITEM = re.compile(r"(?:^|(?<=\s))\(?\d{1,2}\)\s*[가-힣]")
_LIST_MARK = re.compile(r"^\(?\d{1,2}\)$")


def _spans_tables(seg: str) -> bool:
    """`seg` 가 표 경계를 품나 — 그러면 그 양끝은 다른 표다."""
    if _TABLE_BREAK.search(seg):
        return True
    # 숫자 행 = 앞 표의 본문. 항목 번호(`(1)`·`1)`)는 숫자가 아니다.
    return any(_NUM_TOK.match(t) and not _LIST_MARK.match(t)
               for t in seg.split())


def _unit_token(m: "re.Match") -> str:
    """`_UNIT_RE` 매치의 단위 토큰 — 두 갈래 중 잡힌 쪽에서 공백을 뗀다."""
    return re.sub(r"\s+", "", m.group(1) or m.group(2))
# 값 토큰. 콤마 묶음 또는 **콤마 없는 1~4자리**.
# ⚠️ 처음엔 콤마를 필수로 했는데(절번호·연도 차단용) 억원 단위 표에서 값이
# 네 자리 미만이면 통째로 잃는다 — 엠플러스 `합 계 - 1,461 - 830 - 631` 에서
# 830·631 이 끊겨 수주잔고를 못 읽었다(2026-08-17 3차 프로브). 비에이치아이
# `합계 69 3,812,597 …`(건수)·코오롱글로벌 수량 `1` 도 같은 이유로 막혔다.
# 이제 절번호·다음 표 차단은 콤마가 아니라 **표 끝 마커**(_TABLE_END)가 맡고,
# 값의 정합성은 어차피 검산이 최종 판정한다.
_NUM_TOK = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d{1,4})\)?$")

# 표의 끝. ⚠️ 이게 없으면 **다음 표의 숫자까지 행으로 읽는다** — 넥스틴은
# 수주표 바로 뒤 신용위험 표(현금및현금성자산 20,205,538,777 …)가 4열 묶음으로
# 잡혀 검산에 실패했고, 그 탓에 멀쩡한 표가 통째로 버려졌다(3차 프로브 실측).
# 세진중공업도 동일. 삼성E&A·효성중공업은 픽스처가 짧아 우연히 통과했을 뿐
# 전문에서는 같은 위험이 있었다.
_TABLE_END = re.compile(
    r"[※☞]|주\s*\d+\s*\)|\(\s*주\s*\d|\(\s*단위|\(\s*기준일"
    r"|\(\d+\)\s*[가-힣]|\d+\s*\.\s*[가-힣]{2,}|[가-하]\s*\.\s+[가-힣]")


# ⚠️ `기말` 은 원문 **어디에나** 있는 낱말이다 — 현금흐름표(`기말 현금및
# 현금성자산`)·유형자산 증감(`기말장부가액`)·자기주식(`기말수량`)·채무보증
# (`기초 증감 기말`). 2026-08-21 스윕에서 미수집 7건이 전부 그런 표였고,
# 진단이 "잔고 라벨이 있다"고 오판해 **개선 여지 8건**이라는 거짓 숫자를
# 냈다(실제로는 원천에 수주잔고 공시가 없는 종목들). 범용 낱말로 잡힌
# 자리는 주변에 **수주 문맥**이 있어야 인정한다.
_GENERIC_BAL = {"기말"}
_ORDER_CTX = re.compile(r"수주|도급|계약잔액")


def _balance_matches(text: str) -> list:
    """잔고 라벨 매치 전량 — **파서와 진단이 같은 것을 본다**(#105).

    한쪽만 문맥을 요구하면 통계와 화면이 갈라진다.

    ⚠️ 뒤쪽 문맥은 **이 표 안에서만** 본다(2026-10-02 078340 실측). 투자부동산
    변동표의 `반기말금액` 속 `기말` 이 120자 뒤 **다음 절 제목**
    `4. 매출 및 수주상황` 의 `수주` 를 문맥으로 빌려 잔고 라벨이 됐다 — 그
    제목은 모든 정기보고서에 있으므로 그 앞 표의 `기말` 은 전부 같은 일을
    당한다. 표 끝은 파서와 같은 `_cut_table` 이 정한다(#38).
    ⚠️ 앞쪽은 자르지 않는다 — 소제목(`라. 수주상황`)과 캡션이 표의 문맥이고
    그 둘이 `_TABLE_END` 에 걸리므로, 자르면 391710 롤링 표가 통째로 빠진다.
    """
    out = []
    for m in re.finditer("|".join(_BAL_LABELS), text or ""):
        if m.group(0) in _GENERIC_BAL:
            near = (text[max(0, m.start() - 260):m.start()]
                    + _cut_table(text[m.start():m.start() + 120]))
            if not _ORDER_CTX.search(near):
                continue
        out.append(m)
    return out


def _cut_table(seg: str) -> str:
    """표 본문만 남긴다 — 각주(※·주1)·다음 표 캡션·다음 절 번호에서 자른다."""
    m = _TABLE_END.search(seg)
    return seg[:m.start()] if m else seg

# 헤더 판정 — 잔고 컬럼 라벨 / 시작잔고 라벨 / 납품 라벨.
# ⚠️ 긴 것부터. `수주잔` 은 효성중공업이 헤더를 `전기말 수주잔(2025.12.31)` 로
# 줄여 쓰기 때문에 필요하다(2026-08-17 2차 프로브).
_BAL_LABELS = ("기말수주잔고", "수주잔고금액", "수주잔고", "수주잔액",
               "기말잔고", "수주잔", "기말")
_OPEN_LABELS = ("기초계약잔액", "기초수주잔액", "이월 수주잔액", "이월수주잔액",
                "수주총액", "수주액", "수주잔", "기초")
# `매출액` 은 효성중공업이 기납품액 대신 쓰는 열이다. 넓어 보이지만 헤더에
# 잔고 라벨이 함께 있어야만 도달하고, 최종 관문은 어차피 **검산**이다.
_DELIV_LABELS = ("기납품액", "기납품", "납품액", "공사수익", "수익인식",
                 "매출액")

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


# 캡션 역탐색 창. **파서와 진단이 같은 값을 써야** 한다 — 진단이 더 멀리
# 보면 파서가 거리 때문에 거부한 캡션을 '미지원단위' 라고 오보한다(#38·#105).
_CAP_WINDOW = 4000
# 금액 여부를 안 가리는 캡션 매치 — 무엇을 봤는지 말하기 위한 것(#82).
_CAP_ANY_RE = re.compile(r"\(\s*단위[^)]{0,40}\)")
# 외화 캡션은 무관표가 아니다 — 환율이 필요할 뿐이라 처방이 다르다(#82).
# ⚠️ `불`·`엔` 을 빠뜨리면 `(단위 : 천불)` 이 '미지원' 으로 찍혀 다음
# 사람이 `_UNIT_MULT` 에 넣는다 — 그게 1,400배 오차다(_UNIT_MULT 주석이
# 바로 그 사례를 지목하고 있었다).
_FX_CAP_RE = re.compile(
    r"달러|USD|\$|엔화|유로|위안|EUR|JPY|CNY|파운드|GBP"
    r"|(?:천|백만|십억|억|조)\s*(?:불|엔)\s*\)", re.I)
# 수량 단위 = 그 라벨이 **다른 표**에 있다는 신호(협력사 수·주식 수·중량).
# 화폐가 아닌 걸 `_UNIT_MULT` 에 넣으면 개수를 금액으로 읽는다.
# ⚠️ 앞 글자를 막으면(`(?<![가-힣])`) 실제 원문 형태인 `천톤`·`천개`·`회사`
# 가 통째로 빠진다 — 캡션의 **끝 토큰**만 본다. 금액 캡션은 원/억/조로
# 끝나므로 이 목록과 겹치지 않는다.
_COUNT_CAP_RE = re.compile(
    r"(사|주|개|대|명|건|매|장|톤|Ton|kg|KG|㎏|㎡|평|호)\s*\)", re.I)


# 진단 상세가 쓰는 **갈래 어휘** — `_cap_kind` 의 우선순위 목록이자
# `_DETAIL_VOCAB` 의 재료다. 두 곳에 따로 적으면 갈래를 더할 때 한쪽만
# 바뀐다(#38).
_CAP_KINDS = ("금액캡션", "캡션 외화", "캡션 비금액", "캡션 미지원")
# ⚠️ 관문 문구도 어휘다 — 단위를 찾은 행(상세 히스토그램의 **다수**)은 전부
# `_gate_stage` 가 문구를 만든다. 여기서 빠뜨리면 그 문구를 바꿔도 판이 그대로라
# 옛 줄이 다음 보고서를 계속 지배한다(2026-09-04 독립 리뷰).
# ⚠️ 문구를 여기 **한 곳**에 두고 `_gate_stage` 가 읽는다 — 문구와 어휘
# 목록을 따로 적으면 문구만 바꿨을 때 판이 안 바뀌어 옛 줄이 살아남는다(#38).
_GATE_LABELS = ("헤더에 기초·수주총액 열 없음", "헤더에 기납품·매출 열 없음",
                "헤더는 통과 · 합계행 없음", "헤더 통과 · 합계행 {n}값(검산실패)")
_DETAIL_KINDS = _CAP_KINDS + ("멀다", "캡션이 라벨 뒤", "캡션없음") + _GATE_LABELS


def _vocab_sig(kinds: tuple[str, ...]) -> str:
    import hashlib
    return hashlib.sha1("|".join(kinds).encode("utf-8")).hexdigest()[:8]


# 진단 어휘가 바뀌면 **이미 쌓인 기록이 다음 보고서를 지배한다** — 옛
# `미지원단위 (단위 : 사)` 줄이 최상단에 남아 이번 fix 가 화면에 한 글자도
# 안 닿는다(#21b·#216 캐시가 fix 를 가리는 형태). 손으로 올리는 버전 상수는
# 이 레포에서 다섯 번 졌으므로 **어휘 자체에서 파생**시킨다(#119 규율을
# 구조로): 갈래를 더하거나 이름을 바꾸면 값이 자동으로 바뀐다.
_DETAIL_VOCAB = _vocab_sig(_DETAIL_KINDS)


def _cap_kind(full: str) -> str:
    """캡션 하나의 갈래 — 표시 문구의 머리말. far/near 가 **같은 판정**을
    써야 한다(far 를 전부 '금액캡션' 이라 부르면 '창을 넓혀라' 로 읽히는데
    실제 처방은 무관표·환율일 수 있다, 2026-09-04 독립 리뷰).
    """
    if _UNIT_RE.search(full):
        return "금액캡션"
    if _FX_CAP_RE.search(full):
        return "캡션 외화"
    if _COUNT_CAP_RE.search(full):
        return "캡션 비금액"
    return "캡션 미지원"


def _unit_mult(text: str, at: int) -> float | None:
    """`at` **앞쪽** 가장 가까운 `(단위 : X)`. 표 캡션은 항상 표 위에 온다.

    없으면 None — 단위 없이 스케일을 가정하면 100배 오차가 난다(백만원 vs
    억원). 못 정하면 값을 버리는 쪽이 옳다.

    ⚠️ **표 자기 캡션이 금액이 아니면 None** 이다(2026-10-02). 옛 판은 금액
    캡션만 훑어 마지막 것을 썼다 — 그래서 `(단위 : 천USD)`·`(단위 : 천톤)`·
    `(단위 : )` 처럼 표 자기 캡션이 원화가 아니면 그걸 **건너뛰고 앞 표의
    원화 캡션을 빌렸다**. 검산은 열 사이 항등식만 보므로 스케일이 통째로
    틀려도 통과한다(재현: 6,000천USD → 60억원 — 조용한 오답, 빈칸 > 틀린 숫자).

    ⚠️⚠️ 그런데 '자기 캡션' 은 **이 표에 붙은** 캡션이다(독립 리뷰 M1). 첫 판은
    금액 캡션과 라벨 사이의 아무 콜론 캡션이나 자기 캡션으로 봐서 base 가 맞게
    읽던 표를 빈칸으로 만들었다 — 머리행 소캡션(`수량(단위 : 대)`) · 연속
    캡션(`(단위 : 백만원)(단위 : 대)`) · **사이 표**의 캡션(`판매경로 (단위 :
    %)`). 귀속은 표 경계(`_spans_tables`)로 가른다: 그 캡션이 ① 금액 캡션과
    다른 묶음이고(사이에 표 경계나 아래 항목 머리) ② 라벨과 같은 표일 때만
    자기 캡션이다. 금액 단위로 **읽혔다면** 그 캡션이 마지막 금액 매치였을
    것이므로 금액이 아니다 — 읽지 못한 금액 캡션(창 밖 토큰)도 여기서 막히고,
    상세가 `캡션 미지원` 으로 그 캡션을 보여 준다(빌린 단위보다 빈칸이 낫다).

    ⚠️ 훑는 범위는 **창 + 여유**다 — 처음부터 훑으면 라벨 자리마다 문서 전체를
    다시 읽는다(리뷰 L2: 4M자 41자리에서 진단 비용의 대부분). 창 밖 매치는
    어차피 None 이고, 매치 길이는 공백이 접힌 원문에서 수십 자다.
    """
    best = None
    for m in _UNIT_RE.finditer(text, max(0, at - _CAP_WINDOW - 200), at):
        best = m
    if not best or at - best.end() > _CAP_WINDOW:
        return None
    for c in _OWN_CAP_RE.finditer(text, best.end(), at):
        gap = text[best.end():c.start()]
        if not (_spans_tables(gap) or _SUBITEM.search(gap)):
            continue        # 같은 캡션 묶음(소캡션·연속 캡션) — 금액 캡션이 이 표의 것
        if not _spans_tables(text[c.end():at]):
            return None     # 이 표에 붙은 자기 캡션이 금액이 아니다 — 빌리지 않는다
    return _UNIT_MULT.get(_unit_token(best))


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


def _verify_exact(vals: list[float], positive: bool = True) -> float | None:
    """정확히 3열/4열일 때의 항등식.

    · 3열: 수주총액 − 기납품액 = 수주잔고
    · 4열: 기초잔액 + 신규 − 기납품액 = 수주잔고
    (기납품액이 `(12,248,487)` 처럼 이미 음수로 적힌 회사가 있어 **절대값**으로
    뺀다 — HD현대중공업 실측.)

    `positive=False` 는 **진단 전용**이다 — 항등식은 맞는데 잔고가 0 이하인
    표(영풍 실측 245,858 − 264,255 = −18,396)를 '열을 잘못 집었다' 와 가르려고
    부호 조건만 뺀다. 파서는 늘 기본값으로 부른다(0 이하는 화면에 안 싣는다)."""
    if len(vals) == 3:
        a, b, c = vals
        exp, got = a - abs(b), c
    elif len(vals) == 4:
        a, b, c, d = vals
        exp, got = a + b - abs(c), d
    else:
        return None
    if positive and (exp <= 0 or got <= 0):
        return None
    if abs(exp - got) > _TOL * max(abs(exp), abs(got)):
        return None
    return got


def _verify(vals: list[float], positive: bool = True) -> float | None:
    """합계행 숫자들이 원문이 명시한 항등식을 만족하면 잔고를 반환.

    ⚠️ 이 검산이 유일한 컬럼 정합성 보증이다. 표 구조가 바뀌어 엉뚱한 열을
    집으면 산수가 안 맞고, 그때는 값을 내지 않는다(조용한 오답 방지).

    ⚠️ 열 개수가 3·4·6 이 아니면 통째로 버리고 있었다(2026-08-21 VM 스윕:
    `형식미지원 · 합계행 5값`·`4값`). 표에 **수량·비중 열이 섞이면** 개수가
    어긋난다 — 그렇다고 열 뜻을 추측해 배정하면 스케일이 아니라 **의미**가
    틀리고, 그건 검산도 못 잡는다. 그래서 추측 대신 **연속 부분열**로
    같은 항등식이 성립하는지 보고, 성립하는 잔고값이 **유일할 때만** 쓴다
    (여러 개면 어느 열인지 모르는 것이므로 거부 — 빈칸 > 틀린 숫자).
    """
    # 6열 = (수량, 금액) 쌍 3벌. 티에스이가 `(단위 : 개, 백만원)` 으로 수량까지
    # 숫자로 적어 6개가 나온다 — 홀수 인덱스(금액)만 뽑아 3열과 같게 본다.
    v = list(vals or [])
    # ⚠️ 표가 말하는 항등식(6열 금액쌍 · 3/4열)이 **잔고 0 이하로** 성립하면
    # 그게 이 행의 답이다 — 부분열 탐색으로 넘어가면 `5 1,000 5 1,000 0 0`
    # (다 납품한 수량·금액 쌍)에서 `[1,000, 5, 1,000]` 이 1% 안에 맞아 잔고
    # 1,000 을 지어냈다(2026-10-02 독립 리뷰 L8 — 빈칸 > 틀린 숫자). 진단은
    # 같은 항등식을 `positive=False` 로 보고 `잔고0이하` 로 가른다.
    exact = [v[1::2]] if len(v) == 6 else []
    exact.append(v)
    for cols in exact:
        got = _verify_exact(cols, positive)
        if got is not None:
            return got
        if positive and _verify_exact(cols, False) is not None:
            return None
    # 부분열 탐색. 상한을 두는 이유: 열이 아주 많은 표는 우연히 맞는 조합이
    # 생길 여지가 커지고, 애초에 수주표가 아닐 가능성이 높다.
    if not (3 <= len(v) <= 8):
        return None
    cands: set = set()
    for w in (3, 4):
        for i in range(0, len(v) - w + 1):
            r = _verify_exact(v[i:i + w], positive)
            if r is not None:
                cands.add(round(r, 6))
    return cands.pop() if len(cands) == 1 else None


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


def _first_run(seg: str) -> list[float]:
    """첫 숫자 묶음. ⚠️ `_row_values` 와 달리 **앞의 비숫자 토큰을 건너뛴다** —
    전치표는 라벨에 각주 표시가 붙는다(케이씨텍 `기말공사계약잔액(*) 34,500,592`).
    `_row_values` 는 `(*)` 에서 즉시 끊겨 빈 행을 돌려준다."""
    runs = _runs(seg)
    return runs[0] if runs else []


def _parse_table(text: str) -> tuple[float, str] | None:
    """형태 A — 헤더(총액/기초 + 기납품 + 잔고)가 있는 표.

    ① `합 계` 행이 있으면 그 행을 검산해서 쓴다(가장 안전).
    ② 합계행이 **아예 없으면** 데이터 행들을 각각 검산해 잔고열을 합산한다
       — 제이티(1행)·삼성E&A(3행)·효성중공업(3행)이 이 형태다.
       ⚠️ 합계행이 **있는데 검산에 실패한 경우엔 행 합산으로 넘어가지 않는다.**
       합계가 있는데 못 맞췄다는 건 내 컬럼 모델이 틀렸다는 신호라, 거기서
       행을 더 파면 틀린 값을 그럴듯하게 만들어낸다."""
    for m in _balance_matches(text):
        head = text[max(0, m.start() - 260):m.start()]
        if not any(k in head for k in _OPEN_LABELS):
            continue
        if not any(k in head for k in _DELIV_LABELS):
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        # 표는 헤더 뒤 가까이에서 끝난다. 넓게 잡으면 다음 표를 먹는다.
        seg = _cut_table(text[m.end():m.end() + 2500])
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


# ── 형태 H: 전치형(세로) 계약잔액 표 ────────────────────────────────
# 항목이 **행**이고 기간·부문이 열이다. 케이씨텍(당반기/전기 2열)·코오롱글로벌
# (부문 6개 + 합계 7열) 실측. 라벨 사이에 공백이 끼어 있어(`신 규 계 약 액`)
# 글자마다 `\s*` 를 허용한다.
def _sp(word: str) -> str:
    return r"\s*".join(word)


_TRANS_STEPS = (
    (r"기초\s*(?:공사)?\s*계약잔액", "기초"),
    (rf"{_sp('신규계약액')}|{_sp('신규계약')}|당기\s*계약액|{_sp('신규수주')}", "신규"),
    (rf"당기\s*공사수익금액|{_sp('수익인식')}|{_sp('수익')}", "수익"),
    (r"기말\s*(?:공사)?\s*계약잔액", "기말"),
)


def _parse_transposed(text: str) -> tuple[float, str] | None:
    """형태 H — `기초계약잔액 / 신규계약액 / 수익 / 기말계약잔액` 이 **행**으로
    쌓인 표. 각 행에서 같은 자리의 숫자를 뽑아 A + B − |C| = D 로 검산한다.

    ⚠️ 어느 열을 쓸지는 **열 개수**로 정한다 — 2열이면 (당반기, 전기)라 첫 열이
    당기이고(케이씨텍), 3열 이상이면 (부문…, 합계)라 마지막이 전사 합계다
    (코오롱글로벌 6부문+합계). 부문별로도 항등식이 성립해서 검산만으로는
    둘을 못 가른다 — 첫 열을 집으면 코오롱은 국내토목 부문만 나온다."""
    for m0 in re.finditer(_TRANS_STEPS[0][0], text):
        pos, rows, ok = m0.end(), [], True
        for pat, _lbl in _TRANS_STEPS[1:]:
            mm = re.compile(pat).search(text, pos, pos + 900)
            if not mm:
                ok = False
                break
            rows.append(_first_run(text[pos:mm.start()]))
            pos = mm.end()
        if not ok:
            continue
        rows.append(_first_run(text[pos:pos + 300]))
        if not rows[0]:
            continue
        n = len(rows[0])
        if n < 2 or any(len(r) != n for r in rows):
            continue
        idx = 0 if n == 2 else n - 1
        got = _verify([r[idx] for r in rows])
        if got is None:
            continue
        mult = _unit_mult(text, m0.start())
        if mult is None:
            continue
        return got * mult, "전치·계약잔액"
    return None


# ── 형태 I: 건설 도급 표 ─────────────────────────────────────────
# 헤더 `기본도급액 / 완성공사액 / 계약잔액` + 합계행. 대우건설·GS건설 실측.
# ⚠️ 한전기술은 `계약잔액` 을 쓰지만 헤더가 `사업건수 / 수익인식액 / 계약잔액 /
# 원도급액` 이라 이 게이트를 **통과하지 못한다** — 원문이 스스로 "회사 전체의
# 계약 잔액과 다릅니다"라고 밝힌 표라 배제되는 게 맞고, 별도 문구 감지 없이
# 헤더 구성만으로 갈린다.
_CONTRACT_HEAD = re.compile(r"기본\s*도급\s*금?액.{0,40}?완성공사액.{0,40}?계약잔액",
                            re.S)


def _parse_contract_table(text: str) -> tuple[float, str] | None:
    for m in _CONTRACT_HEAD.finditer(text):
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = text[m.end():m.end() + 12000]      # 프로젝트가 수백 줄일 수 있다
        tm = re.search(r"합\s*계", seg)
        if not tm:
            continue
        got = _verify(_row_values(seg, tm.end()))
        if got is None:
            continue
        return got * mult, "건설·계약잔액"
    return None


_OPEN_CLOSE_HEAD = re.compile(
    r"기\s*초\s*\([^)]{4,20}\).{0,40}?기\s*말\s*\([^)]{4,20}\)")


def _parse_open_close(text: str) -> tuple[float, str] | None:
    """형태 L — `기 초(YYYY.MM.DD) / 기 말(YYYY.MM.DD)` **2열**형(한국항공우주).

    방산은 보안상 수주액·납품액을 안 쓰고 기초·기말 잔고만 적는다. 그래서
    `_verify` 의 3·4열 항등식(총액−납품=잔고)이 성립하지 않아 2차 프로브
    때부터 미해결로 남아 있었다 — 그 결과 파서가 **자회사 표**를 대신 잡아
    26조를 0.08조로 보고했다(2026-08-18 `--explain 047810`).

    ⚠️ 검산은 **부문 합 = 합계**(형태 D 와 같은 규약)로 세운다. 실측:
    83,618+52,919+102,948+7,509 = 246,994(기초),
    99,437+59,935+100,415+6,946 = 266,733(기말) — 두 열 모두 맞아야 한다.
    한 열만 보면 열을 잘못 집어도 우연히 맞을 수 있다."""
    for m in _OPEN_CLOSE_HEAD.finditer(text):
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = _cut_table(text[m.end():m.end() + 6000])
        tm = re.search(r"합\s*계", seg)
        if not tm:
            continue
        total = _row_values(seg, tm.end())
        rows = [r for r in _runs(seg[:tm.start()]) if len(r) == 2]
        if len(total) != 2 or len(rows) < 2:
            continue
        ok = True
        for col in (0, 1):
            exp = sum(r[col] for r in rows)
            if exp <= 0 or abs(exp - total[col]) > _TOL * max(exp, total[col]):
                ok = False
                break
        if not ok:
            continue
        return total[1] * mult, "표·기초기말2열"
    return None


# 형태 M — `전기말 / 신규계약 / 매출인식 / 당반기말` **4열 롤링** 표.
# 2026-09-18 원문 실측(391710 씨아이에스): 격주 리뷰가 '헤더에 기초·수주총액
# 열 없음' 으로 9건을 묶어 냈고, 발췌를 보니 헤더가 `구분 전기말 신규계약
# 매출인식 당반기말` 이었다 — `_BAL_LABELS` 의 `기말` 이 **전기말·당반기말
# 양쪽에** 걸려 잔고 열은 찾았는데 시작 열(`기초`·`수주총액`)이 없어 1번
# 관문에서 막혔다. 어구를 늘리는 대신 **항등식이 있는 형태**로 받는다.
_ROLL_NEW = re.compile(r"신\s*규\s*(?:계약|수주)")
_ROLL_OPEN = re.compile(r"전\s*(?:반|분)?\s*기\s*말|기\s*초")
_ROLL_RECOG = re.compile(r"(?:매출|수익)\s*인식|매\s*출\s*액|납\s*품\s*액?")
_ROLL_CLOSE = re.compile(r"당\s*(?:반|분)?\s*기\s*말|기\s*말|수주잔고|수주잔액")


def _roll_ok(r: list) -> bool:
    """기초 + 신규 − 인식 ≈ 기말. **열을 잘못 집는 것을 막는 유일한 가드**다
    (#106 열 뜻을 추측해 배정하면 스케일이 아니라 의미가 틀리고, 그건 검산도
    못 잡는다). 실측 391710: 7,058 + 1,718 − 4,761 = 4,015 (정확히 일치).

    ⚠️ 항등식은 **형제(`_verify_exact`)를 그대로 부른다** — 처음엔 여기에
    `a + b - c` 를 따로 적었는데, 형제는 이미 `a + b - abs(c)` 이고 그 이유를
    적어 두고 있었다(기납품액을 `(12,248,487)` 처럼 괄호 음수로 적는 회사가
    있다 — HD현대중공업 실측). 같은 항등식을 두 곳에 적으면 한쪽만 고쳐져
    그런 회사가 이 형태에서만 거부된다(#38, 독립 리뷰 2026-09-18 M4 —
    뮤테이션이 그 갈라짐을 증명했다: 어느 쪽으로 바꿔도 회귀가 통과했다).
    """
    return len(r) == 4 and _verify_exact(r) is not None


def _parse_rolling(text: str) -> tuple[float, str] | None:
    """형태 M — 기초·신규·인식·기말 4열. 검산은 행마다 `_roll_ok`.

    ⚠️ 합계행이 있으면 **그 행만** 쓴다 — 부문 행과 같이 더하면 두 배가
    된다. 합계가 없으면 검산을 통과한 행들의 기말을 합한다(형태 K 와 같은
    규약). 통과한 행이 하나도 없으면 표가 아니라고 보고 포기한다.
    """
    for m in _ROLL_NEW.finditer(text):
        head = text[max(0, m.start() - 80):m.start()]
        tail = text[m.end():m.end() + 80]
        cm = _ROLL_CLOSE.search(tail)
        if not _ROLL_OPEN.search(head) or not _ROLL_RECOG.search(tail) or not cm:
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = _cut_table(text[m.end() + cm.end():][:6000])
        tm = re.search(r"합\s*계", seg)
        if tm:
            total = _row_values(seg, tm.end())[:4]
            if not _roll_ok(total):
                continue
            return total[3] * mult, "표·기초신규인식기말"
        good = [r for r in _runs(seg) if _roll_ok(r)]
        if not good:
            continue
        return sum(r[3] for r in good) * mult, "표·기초신규인식기말"
    return None


def _parse_project_rows(text: str) -> tuple[float, str] | None:
    """형태 K — `기본도급액·완성공사액·계약잔액` 3열이 **합계행 없이** 사업
    구분별로 나열되는 표(한전KPS).

    실측(2026-08-18 `--explain 051600`): 화력/원자력/송변전/대외/해외 5행에
    합계가 없다. `_parse_contract_table` 은 `합 계` 를 요구해 첫 행 하나만
    잡거나(0.064조) 아예 놓쳤다 — 실제 잔고는 5행 합인 3.17조다.

    ⚠️ 검산: 각 행이 **기본도급액 − 완성공사액 ≈ 계약잔액** 을 만족해야
    한다(원문 주석: 초과준공으로 차이가 날 수 있어 관대한 허용오차).
    이 항등식이 열을 잘못 집는 것을 막는다 — 만족하는 행이 2개 미만이면
    표가 아니라고 보고 포기한다."""
    for m in _CONTRACT_HEAD.finditer(text):
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = text[m.end():m.end() + 12000]
        if re.search(r"합\s*계", seg[:2000]):
            continue                        # 합계행이 있으면 그쪽 파서 소관
        rows = [r for r in _runs(seg) if len(r) == 3]
        good = [r[2] for r in rows
                if r[0] > 0 and abs((r[0] - r[1]) - r[2]) <= r[0] * 0.35]
        if len(good) < 2:
            continue
        return sum(good) * mult, "건설·행별계약잔액"
    return None


def _parse_domestic_export(text: str) -> tuple[float, str] | None:
    """형태 J — `신규수주 / 매출 / 기말수주잔고` 3열이 내수·수출·소계로 쌓인
    블록(에스에프에이).

    기초 잔고 열이 없어 A 의 항등식(기초+신규−납품=잔고)을 세울 수 없다.
    대신 **내수 + 수출 = 소계**가 세 열 모두에서 성립해야 하고, 이게 검산이
    된다 — 열을 잘못 집으면 세 등식이 동시에 맞을 수 없다."""
    for m in re.finditer(r"수주잔고액|기말\s*수주잔고", text):
        if "신규수주" not in text[max(0, m.start() - 200):m.start()]:
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = _cut_table(text[m.end():m.end() + 3000])
        tms = list(re.finditer(r"합\s*계", seg))
        if not tms:
            continue
        runs = [r for r in _runs(seg[tms[-1].end():]) if len(r) == 3][:3]
        if len(runs) != 3:
            continue
        dom, exp_, tot = runs
        if any(abs(dom[i] + exp_[i] - tot[i]) > _TOL * max(abs(tot[i]), 1.0)
               for i in range(3)):
            continue
        if tot[2] <= 0:
            continue
        return tot[2] * mult, "내수·수출 소계"
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
        seg = _cut_table(text[m.end():m.end() + 1200])
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

    · LIG넥스원 `구 분 수주잔액 제25기(2026년 2분기) 245,781`
    · 퍼스텍   `품목 수주잔고 금액 방산사업 944,080`
    라벨과 값 사이에 기수·부문명이 끼므로 **첫 값 토큰까지 건너뛴다**. 다만
    건너뛰는 구간에 다른 표가 끼어들지 않도록 좁게(120자) 본다."""
    m = re.search(r"(?:구\s*분|품\s*목)\s*(?:수주잔액|수주잔고)"
                  r"(?:\s*금\s*액)?", text)
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


# ── 런타임 관측 (사용자 2026-08-17) ──────────────────────────────────
# 프로브를 손으로 도는 건 확장이 안 된다 — 상장사 2,700개 중 88개를 내 감으로
# 골랐고 그 과정에서 회사명을 두 번 잘못 붙였다. 대신 **실사용이 곧 프로브**가
# 되게 한다: 파서가 값을 못 내면 사유를 남기고, 나중에 사유별로 훑어 진짜
# 파서 개선 여지(형식미지원·검산실패)만 골라낸다. 미공시는 원천에 값이 없는
# 것이라 개선 대상이 아니다. CLAUDE.md Automation-first.
_MISS_LOG = _Path.home() / ".tradingagents" / "backlog_misses.jsonl"
_MISS_CAP = 4000          # 줄 수 상한 — 장수 프로세스에서 무한 증가 방지
# 기록에 남기는 원문 발췌 길이. `backlog_excerpt` 의 기본 창(앞 120 + 뒤 400)을
# 담는다 — 여기서 더 자르면 헤더가 잘려 **고칠 근거가 사라진다**(#156·#350
# '자르는 자리가 다음 결정을 가리지 않는가').
# ⚠️ 2026-09-18 첫 실물 라운드에서 **결정적인 줄이 창 밖이었다** — 영풍
# (000670)의 `합 계` 행이 발췌 끝을 넘어가 '합계행 3값(검산실패)' 의 근거를
# 볼 수 없었다. 자르는 자리가 다음 결정을 가리면 안 된다(#156·#350).
_EXCERPT_CAP = 600
# 텔레그램 단일 메시지 한도는 4096 UTF-16 이고, 우리는 그보다 낮은 값을
# **보낼 메시지 전체**에 건다 — 넘기면 메시지가 통째로 안 가고, 그 실패는
# `_periodic_backlog_review` 의 `log.exception` 에만 남아 **화면에는 아무
# 신호도 안 온다**(로그엔 남는다 — 안 재고 '아무 데도 안 남는다' 고 적으면
# 그게 #165 다).
# ⚠️ 갈래 개수로는 자르지 않는다. 개수 상한을 따로 두면 예산이 남아도는데도
# 조용히 버리고, 그 버린 수는 '길이 한도로 생략' 계수에 안 잡혀 보고서가
# "전부 봤다"고 거짓말한다(2026-09-18 독립 리뷰 실측: 10갈래 → 6개 표시 ·
# 생략 문구 없음 · 1294/4096). 자르는 것은 **예산 하나**다(#45).
_DM_LIMIT = 4000
_DM_EX_WIDTH = 200
# 보고서에 싣는 합계행 폭 — 값이 6~8개면 100자 안이다.
_DM_TOTAL_WIDTH = 100
# 운영자에게 인쇄되는 명령은 **그대로 붙여넣어 도는 형태**여야 한다(#278).
# HTML 메시지라 `&&` 는 `&amp;&amp;` 로 써야 파싱이 안 깨진다(규칙 7).
_CLI_CMD = "cd ~/stock &amp;&amp; .venv/bin/python -m bot.scripts.backlog_misses"
# `quarterly_infographic` 이 **원문 없이** 남기는 사유 — 조립된 분기 시계열의
# 급변을 보고 찍는다. 원문 경로가 아니므로 발췌가 영영 없고, 되메우기 대상도
# 아니다(다시 조회하면 그 자리에 파싱 사유가 들어와 이 신호를 지운다).
# ⚠️ 문자열을 두 곳에 적으면 한쪽만 바뀌어 그 규칙이 조용히 죽는다(#38).
MISS_SERIES_ANOMALY = "시계열이상"
# 원문을 **못 받은** 사유. 되메우기가 이걸 옛 관측의 반증으로 쓰면 안 된다 —
# 문서를 안 읽었으므로 파싱 판정을 갱신할 근거가 없고, DART 일일한도 한 번이
# 게이트 분류(이 보고서의 존재 이유)를 통째로 지운다(2026-09-18 독립 리뷰 B1).
MISS_NO_DOC = "원문미제공"
# 표는 읽혔고 원문 항등식(수주총액 − 기납품액 = 수주잔고)도 **맞는데** 잔고가
# 0 이하다 — 영풍 000670 실측 245,858 − 264,255 = −18,396(기납품이 수주총액을
# 넘었다). 파서는 0 이하를 일부러 안 싣는다(화면의 '잔고' 가 아니다). 그건
# 파서 갭이 아니라 **원천 값**이라 개선 여지로 세면 다음 라운드가 고칠 수
# 없는 것을 고치러 간다(#93·#260). 차트 각주에는 이 이름이 그대로 실린다.
MISS_NON_POSITIVE = "잔고0이하"

# 원문이 스스로 미공시를 밝히는 문구. 실측: 영화금속 "산정은 불가능합니다" ·
# SNT모티브 "관리하고 있지 않습니다" · 상아프론테크 "수주잔고는 없습니다" ·
# HD현대건설기계·HPSP "기재는 생략". 파서로 해결 불가 — 개선 대상이 아니다.
# ⚠️ 2026-08-21 원문 발췌로 세 어구를 더 확인했다 — 미수집으로 남아 있던
# 건들이 사실은 원문이 스스로 "안 씁니다"라고 밝힌 것이었다:
#   · 라온피플 277810 "수주잔고는 의미가 없다고 판단되어 **기재하지 않습니다**"
#   · 케이아이엔엑스 093320 "장기공급계약 **수주거래는 없습니다**"
#   · LG이노텍 011070 "수주잔고 등 … 예측하고 **관리하는 것은 어려운 상황**"
# 이 정규식은 **분류 전용**이다(`diagnose` 만 쓴다) — 파서는 이걸 안 보므로
# 넓혀도 값이 죽지 않는다. 넓히는 대신 '수주' 문맥 안으로 한정한다.
# ⚠️ 2026-10-02 195870 실측: "수주총액, 기납품액 및 수주잔고에 대한 공개는 당사
# 및 당사 종속회사의 **영업기밀**에 해당되어 … 구체적 기재를 생략합니다" — 사유가
# 길어 `수주잔고 … 기재 … 생략` 이 30자 창을 넘었다. 영업기밀·영업비밀을
# 경유하는 어구로 받되 **같은 문장**(`[^.]`)으로 묶는다 — 다른 문장의 영업기밀
# 언급이 수주잔고 표를 미공시로 내리면 진짜 파서 갭이 원장에서 사라진다(#385).
# ⚠️⚠️ 그리고 **공개 대상이 수주잔고 자신**이어야 한다(독립 리뷰 H1 재현).
# 표 본문엔 마침표가 없어 `[^.]` 가 머리행의 `수주잔고` 부터 표 밑 각주까지
# 이어지고, 각주는 흔히 **다른 것**(고객사명·발주처·프로젝트명)을 영업비밀이라
# 한다 — 첫 판은 그 각주 하나로 못 읽은 표를 '미공시' 로 숨기고 그 분기의 개선
# 여지 줄까지 지웠다. `수주잔고(에 대한|의)? 공개·공시·기재` 로 묶는다.
# 창: 195870 은 공개→영업기밀 16자 · 영업기밀→생략 37자. 이 갈래는 **이름을 달아**
# 둔다 — 테스트가 이 갈래만 맞는 예문으로 재야 하는데(다른 갈래가 대신 맞으면 이
# 갈래의 변형이 안 걸린다, #91b), 정규식 문자열을 잘라 쓰면 깨지기 쉽다(#19).
_TRADE_SECRET_PAT = (
    r"수주잔고(?:에\s*대한|의)?\s*(?:공개|공시|기재)[^.]{0,60}?"
    r"영업\s*상?\s*(?:기밀|비밀)[^.]{0,60}?(?:생략|기재하지\s*않|공개하지\s*않)")
_NO_DATA_RE = re.compile(
    r"수주잔고[^.]{0,30}?(?:산정[^.]{0,10}?불가|없습니다|기재[^.]{0,10}?생략)"
    r"|(?:수주물량[^.]{0,20}?)?수주잔고[^.]{0,20}?관리하고\s*있지\s*않"
    r"|기재[는를]?\s*생략[^.]{0,40}?수주잔고"
    r"|수주잔고[^.]{0,40}?기재하지\s*않"
    r"|수주(?:거래|계약)[^.]{0,25}?없습니다"
    r"|수주잔고[^.]{0,60}?(?:예측|관리)[^.]{0,30}?어려"
    r"|" + _TRADE_SECRET_PAT)


def _balance_spots(text: str) -> list[int]:
    """잔고 라벨이 나온 **모든** 위치. 파서들이 훑는 범위와 같아야 한다."""
    return [m.start() for m in _balance_matches(text)]


def diagnose(text: str) -> str:
    """값을 못 낸 이유를 짧은 코드로 분류한다.

    ⚠️ 분류가 곧 **개선 여지 판정**이다 — `미공시`·`명시적미공시` 는 원천에
    값이 없어 파서를 고칠 여지가 없고, `형식미지원`·`검산실패`·`단위없음` 만
    새 형식이 필요하다는 신호다. 이 구분이 없으면 로그가 노이즈가 된다."""
    if not text:
        # ⚠️ 미공시와 **다른 신호**다. 원천에 값이 없는 게 아니라 DART 가 그
        # 접수건의 원문을 안 준다(`status=014 파일이 존재하지 않습니다`).
        # 한화에어로 2026 1분기 실측 — 정정도 없는 원본인데 문서가 없다
        # (`--list` 로 확대 창까지 훑어 정정 부재를 확인, 2026-08-18).
        # 여러 종목에서 몰리면 키 권한·일일한도 같은 계정 문제일 수 있으므로
        # 격주 리포트에 **보여야 한다**.
        return MISS_NO_DOC
    hits = len(_balance_matches(text))
    if not hits:
        return "미공시"
    if _NO_DATA_RE.search(text):
        return "명시적미공시"
    # ⚠️ **첫 출현만 보면 파서와 다른 자리를 진단한다**(2026-08-21). 파서들은
    # `finditer` 로 잔고 라벨 **전 출현**을 훑고 금액 단위가 없는 자리는
    # 건너뛴다 — 그런데 진단은 첫 자리만 봐서, 앞쪽 다른 표의 캡션
    # (`(단위 : 사)`·`(단위 : 주)`)을 근거로 '단위없음' 이라고 보고했다.
    # 감사의 판정은 **제품이 실제로 훑는 범위**와 같아야 한다(#80·#35).
    spots = _balance_spots(text)
    # 원천이 표 **틀만** 내고 칸을 전부 `-` 로 뒀다. 파서로 해결할 수 없으므로
    # 개선 여지가 아니다 — '형식미지원' 으로 세면 다음 작업 목록이 통째로
    # 틀린다(#93·#109·#111).
    # ⚠️ **단위보다 먼저** 본다(2026-10-02 091340 실측 캡션 `(단위 : )`). 옛
    # 판은 금액 단위를 찾은 자리만 봐서, 같은 빈 표가 캡션을 빌리면 빈 표로,
    # 못 빌리면 `단위없음`(= 개선 여지)으로 갈렸다. 빈 칸은 캡션이 뭐라든
    # 빈 칸이다. 그리고 **모든** 자리가 빈 표여야 한다 — 하나라도 다른
    # 자리가 있으면 그게 진짜 파서 갭일 수 있다(#385 숨기는 쪽이 더 나쁘다).
    if all(_empty_backlog_table(text, p) for p in spots):
        return "명시적미공시"
    with_unit = [p for p in spots if _unit_mult(text, p) is not None]
    if not with_unit:
        return "단위없음"
    # 합계행은 **잘린 표**에서 찾는다(`_total_row`) — 파서·관문 진단과 같은
    # 표를 봐야 다음 절의 `합계` 가 '합계 있음' 을 만들지 않는다(리뷰 L3: 사유
    # `형식미지원` 과 상세 `합계행 없음` 이 갈렸다, #38).
    if not any(_total_row(text, p)[1] for p in with_unit):
        return "합계없음"
    # 항등식은 맞는데 잔고가 0 이하(영풍 실측) — 원천 값이지 파서 갭이 아니다.
    # 빈 표와 섞여도 같은 결론이지만, **다른 자리가 하나라도** 있으면 그건
    # 개선 여지로 남긴다(위와 같은 이유).
    verdicts = [("empty" if _empty_backlog_table(text, p) else
                 "nonpos" if _nonpositive_backlog_table(text, p) else "")
                for p in spots]
    if "nonpos" in verdicts and all(verdicts):
        return MISS_NON_POSITIVE
    return "형식미지원"


def _total_row(text: str, at: int):
    """그 자리 표의 `합 계` 매치 — **잘린 표**(`_cut_table`) 안에서만 찾는다.

    ⚠️ 표 끝을 안 자르면 다음 절의 숫자가 합계 행 값으로 읽힌다(2026-10-02
    091340 의 갈래가 그 모양으로 재현된다 — 실제 꼬리는 발췌 밖이라 재지
    못했다, `_empty_backlog_table` 참고). 관문 진단
    (`_gate_stage`)과 빈 표 판정·발췌가 **같은 표**를 봐야 '합계행 0값' 과
    '빈 표가 아님' 이 동시에 참이 되는 일이 없다(#38).
    → (잘린 표, 매치 또는 None)
    """
    seg = _cut_table(text[at:at + 2500])
    return seg, re.search(r"합\s*계", seg)


def _nonpositive_backlog_table(text: str, at: int) -> bool:
    """그 자리 표가 **파서와 같은 관문**(시작·납품 열 + 금액 단위 + 합계행)을
    지나고, 합계행이 항등식을 만족하는데 잔고가 0 이하인가.

    ⚠️ 관문을 파서와 똑같이 걷는다 — 진단만 느슨하면 다른 표의 음수가
    '원천 값' 으로 둔갑해 진짜 갭이 원장에서 사라진다(#35·#385).
    """
    head = text[max(0, at - 260):at]
    if not (any(k in head for k in _OPEN_LABELS)
            and any(k in head for k in _DELIV_LABELS)):
        return False
    if _unit_mult(text, at) is None:
        return False
    seg, m = _total_row(text, at)
    if m is None:
        return False
    bal = _verify(_row_values(seg, m.end()), positive=False)
    return bal is not None and bal <= 0


def _empty_backlog_table(text: str, at: int) -> bool:
    """그 자리의 수주 표가 **숫자 한 칸 없이 `-` 뿐**인가.

    2026-09-18 원문 실측(091340): `품목 수주일자 납기 수주총액 기납품액
    수주잔고 수량 금액 … - - - - - - 합 계 - - - - - -` — 회사가 표 틀만
    내고 값을 안 썼다. 옛 판은 이걸 `형식미지원`(= 파서 개선 여지)으로 세어
    격주 리뷰의 작업 목록을 부풀렸다.

    ⚠️ 헤더의 연도·기수(`제29기`)나 단위 캡션이 숫자로 잡히지 않게 **합계
    라벨 뒤**만 본다 — 거기가 값이 들어갈 자리다.

    ⚠️⚠️ 숫자 유무는 `_row_values` 가 아니라 `_first_run` 으로 센다 — 전자는
    **앞의 비숫자 토큰에서 즉시 끊겨** 빈 행을 돌려주고(그 함정은 `_first_run`
    독스트링이 케이씨텍 실측으로 이미 적어 뒀다), 합계 라벨에 각주가 붙는
    회사(`합 계 (*) - 10,000 - 4,000 - 6,000`)가 실재한다. 그 조합이면 값이
    가득한 표가 '명시적미공시' 로 분류되고, 그 사유는 `_log_miss` 가 기록
    자체를 건너뛰므로 **진짜 파서 갭이 원장에서 통째로 사라진다**(#93·#109·
    #111 이 세우려 한 '개선 여지' 계수의 정반대, 독립 리뷰 2026-09-18 H1).

    ⚠️ **잘린 표**만 본다(`_total_row`) — 옛 판은 합계 뒤 120자를 표 끝을
    안 자르고 읽었다 — 빈 표 바로 뒤 다음 절에 번호 항목(`(1) …` → −1)이
    오면 그걸 값으로 센다. 2026-10-02 091340 이 `합계행 0값(검산실패)` 으로
    남은 갈래가 그 모양이다. ⚠️ 단 091340 의 실제 꼬리는 보고서 발췌(200자,
    `… - - 5. 위험` 에서 끝남) 밖이라 **재지 못했다** — 발췌만 넣으면 옛 판도
    빈 표로 본다(`5.` 는 값으로 안 읽힌다). 번호 항목을 덧붙여야 관측된 갈래가
    글자 그대로 재현된다(#165 — 재현은 가설의 지지이지 증명이 아니다).
    """
    seg, tm = _total_row(text, at)
    if tm is None:
        return False
    after = seg[tm.end():tm.end() + 120]
    return not _first_run(after) and "-" in after


def diagnose_detail(text: str) -> str:
    """`diagnose` 가 낸 사유의 **행동 가능한 상세**. 없으면 빈 문자열.

    ⚠️ 왜 필요한가(2026-08-21): 미수집 사유가 `단위없음 15 · 형식미지원 7`
    로 나왔는데, 그 15건이 캡션이 아예 없는 건지 우리가 모르는 단위(달러·
    천주)인지 알 수 없어 **다음에 뭘 고쳐야 할지 알 수 없었다**. 숫자는
    행동으로 이어질 때만 쓸모가 있다(#93).

    ⚠️ `diagnose` 의 반환값은 건드리지 않는다 — 그 코드로 집계·필터하는
    곳이 여럿이라(미공시류 스킵 등) 문자열을 바꾸면 조용히 갈라진다.
    """
    if not text:
        return ""
    # ⚠️ 원천에 값이 없는 건은 **상세가 없어야 한다**. 2026-08-21 실측에서
    # 라온피플류가 `명시적미공시 · 미지원단위 (단위:천원)` 로 찍혔다 —
    # 분류는 "원문이 안 쓴다고 밝힘" 인데 상세는 단위 얘기를 하니 읽는
    # 사람이 '단위를 더 지원하면 되나' 로 오해한다(#93 의 반대 방향:
    # 행동으로 이어지지 **않는** 상세는 노이즈다).
    # 고칠 것이 아닌 갈래 전부 — 목록을 여기 따로 적으면 새 갈래(잔고0이하)가
    # 생길 때 한쪽만 바뀐다(#38).
    if diagnose(text) in NON_FIXABLE_REASONS:
        return ""
    spots = _balance_spots(text)
    if not spots:
        return ""
    with_unit = [p for p in spots if _unit_mult(text, p) is not None]
    if not with_unit:
        # ⚠️ 여기서 무엇을 말하느냐가 **다음 라운드의 작업 목록**이 된다.
        # 셋은 처방이 완전히 다른데 예전엔 전부 '미지원단위' 였다(2026-09-04
        # 격주 리뷰가 `(단위 : 사) 30건` 을 최대 버킷으로 올렸다):
        #   · 금액 캡션이 파서 창 **밖**  → 표 경계·창 문제(단위 추가 아님)
        #   · 캡션이 **금액 단위가 아님** → 그 라벨이 다른 표에 있다(무관표)
        #   · 아예 없음                   → 캡션없음
        # `사`(회사 수)를 `_UNIT_MULT` 에 넣으면 개수를 금액으로 읽는다.
        caps: list[str] = []
        caps_full: list[str] = []
        far: tuple[str, int, str] | None = None
        for at in spots:
            cap = None
            for m in _CAP_ANY_RE.finditer(text, 0, at):
                cap = m
            if cap is None:
                continue
            full = re.sub(r"\s+", " ", cap.group(0))
            txt = full[:24]
            gap = at - cap.end()
            # ⚠️ 기준점이 달라 경계에서 갈린다(_CAP_ANY_RE 는 `)` 뒤,
            # `_unit_mult` 는 단위 토큰 뒤). 파서가 못 읽은 금액 캡션은
            # 근처여도 '멀다'(=경계) 로 보내야 '미지원' 오보를 안 만든다.
            if gap > _CAP_WINDOW or _UNIT_RE.search(full):
                # 파서가 **거리** 때문에 거부한 캡션이다. 지원하는 단위일 수
                # 있으므로 '미지원' 이라 부르면 정면으로 틀린 안내가 된다.
                if far is None or gap < far[1]:
                    far = (txt, gap, full)
                continue
            if txt not in caps:
                caps.append(txt)
                caps_full.append(full)
        # ⚠️ 먼저 `far` 를 본다 — 금액 캡션이 창 밖에 있다는 건 **고칠 수
        # 있는 사유**(표 경계·창)인데, 다른 라벨 자리의 가까운 무관표 캡션이
        # 먼저 반환되면 그 사실이 통째로 가려진다(2026-09-04 독립 리뷰 재현:
        # `캡션 비금액 (단위 : 사)` 만 나오고 4,500자 앞 `(단위 : 백만원)` 은
        # 한 번도 안 나왔다). ❌ 는 고칠 수 있는 것을 가리켜야 한다(#260).
        def _far_msg(f):
            return f"{_cap_kind(f[2])} 멀다 {f[0]} {f[1]}자(창 {_CAP_WINDOW})"

        if far is not None and _cap_kind(far[2]) == "금액캡션":
            return _far_msg(far)
        if caps:
            # ⚠️ 판정은 **온전한 캡션**(caps_full)으로 — 표시용 절단을 재면
            # 긴 캡션에서 단위 토큰이 잘려 갈래가 뒤집힌다(#91b).
            # ⚠️ 그리고 라벨을 정한 그 캡션을 **화면에 실어야** 한다 — `캡션
            # 외화` 라고 써 놓고 ㎥·% 두 개만 보이면 검산이 불가능하다(#202).
            kinds = [(_cap_kind(f), t, f) for t, f in zip(caps, caps_full)]
            # ⚠️ 루프 안에서 재대입하면 마지막 반복이 빈 리스트를 남긴다 —
            # `_cap_kind` 가 목록 밖 값을 내는 날 `picked[0]` 이 IndexError 다
            # (2026-09-04 독립 리뷰가 재현). 찾았을 때만 바꾼다.
            picked = kinds[:1]
            for want in _CAP_KINDS:
                hit = [k for k in kinds if k[0] == want]
                if hit:
                    picked = hit
                    break
            head = picked[0][0]
            shown = [k[1] for k in picked] + [k[1] for k in kinds
                                              if k[0] != head]
            rest = len(caps) - min(len(shown), 2)
            return (head + " " + " / ".join(shown[:2])
                    + (f" 외 {rest}" if rest > 0 else "")
                    + (f" (라벨 {len(spots)}곳)" if len(spots) > 1 else ""))
        if far is not None:
            # 금액캡션은 위에서 이미 반환됐다 — 여기 남는 건 외화·비금액·미지원.
            return _far_msg(far)
        fwd = _CAP_ANY_RE.search(text, spots[0], spots[0] + 1500)
        if fwd:
            return f"캡션이 라벨 뒤 {fwd.group(0)[:24]}"
        return "캡션없음"
    # ⚠️ 여기까지는 "잔고 라벨 + 금액 단위" 만 본 것이다. 표 파서(`_parse_table`)
    # 는 그 앞에 **헤더 게이트**가 하나 더 있다 — 앞 260자에 시작잔고 라벨과
    # 납품 라벨이 둘 다 있어야 표로 들어간다. 그 게이트에서 막히면 검산
    # 코드는 **한 번도 안 돈다**. 그걸 안 갈라 놓으면 "합계행 5값" 을 보고
    # 검산을 고쳤는데 커버리지가 그대로인 일이 생긴다(2026-08-21 실측 —
    # 실제로 그랬다, #20 배선은 태워야 보인다).
    return _gate_stage(text, with_unit)


# 파서가 표를 받아들이기까지 통과해야 하는 관문 — **진단은 이걸 그대로
# 따라 걷는다**(#80 감사의 모든 경로가 제품의 선택기 하나를 볼 것).
def _gate_stage(text: str, spots: list[int]) -> str:
    """잔고 라벨 위치들 중 **가장 멀리 간 단계**를 말한다.

    파서는 전 출현을 훑으므로 진단도 그래야 한다 — 첫 자리에서 막혔다고
    보고하면 뒤 자리에서 검산까지 갔다가 실패한 사실이 가려진다."""
    best, rank = _GATE_LABELS[0], 0
    for at in spots:
        head = text[max(0, at - 260):at]
        if not any(k in head for k in _OPEN_LABELS):
            continue
        if not any(k in head for k in _DELIV_LABELS):
            if rank < 1:
                best, rank = _GATE_LABELS[1], 1
            continue
        seg = _cut_table(text[at:at + 2500])
        m = re.search(r"합\s*계", seg)
        if not m:
            if rank < 2:
                best, rank = _GATE_LABELS[2], 2
            continue
        vals = _row_values(seg, m.end())
        # ⚠️ 값 자체는 넣지 않는다 — 종목마다 달라 히스토그램이 전부 1건씩
        # 으로 쪼개져 "무엇이 많은가"를 못 본다. **모양**만 센다.
        if rank < 3:
            best, rank = _GATE_LABELS[3].format(n=len(vals)), 3
    return best


def backlog_excerpt(text: str, width: int = 400) -> str:
    """미수집 종목의 **잔고 표 주변 원문** 한 조각.

    ⚠️ 사유 히스토그램은 '무엇이 많은가'까지만 말한다 — 실제로 어떤 열
    구성인지는 원문을 봐야 정해지고, 추측으로 열을 배정하면 스케일이 아니라
    **의미**가 틀린다(#106). 다음 라운드의 유일한 근거다.
    """
    at = _excerpt_spot(text)
    if at is None:
        return ""
    seg = text[max(0, at - 120):at + width]
    return re.sub(r"\s+", " ", seg).strip()


def _excerpt_spot(text: str) -> int | None:
    """발췌가 볼 자리 — 헤더 게이트를 가장 멀리 통과한 라벨(파서가 본 그 자리).
    머리 발췌와 합계행 발췌가 **같은 자리**를 써야 한 표의 두 조각이 된다."""
    spots = _balance_spots(text or "")
    if not spots:
        return None
    best, at = -1, spots[0]
    for p in spots:
        head = (text[max(0, p - 260):p])
        sc = (any(k in head for k in _OPEN_LABELS)
              + any(k in head for k in _DELIV_LABELS)
              + (1 if _unit_mult(text, p) is not None else 0))
        if sc > best:
            best, at = sc, p
    return at


# 합계행 발췌 폭. 합계 라벨 + 값 6~8개면 충분하고, 표 끝(`_cut_table`)에서
# 이미 잘린다.
_TOTAL_EX_WIDTH = 160


def backlog_total_excerpt(text: str, width: int = _TOTAL_EX_WIDTH) -> str:
    """머리 발췌와 **같은 표**의 `합 계` 행 — 없으면 빈 문자열.

    ⚠️ 머리 발췌는 라벨 뒤 400자에서 끝나 행이 많은 표는 합계행이 창 밖이다
    (2026-09-18 영풍 000670 — 그래서 `합계행 3값(검산실패)` 의 근거를 못 봤다).
    표 길이에는 상한이 없으니 창을 아무리 넓혀도 다음 표가 또 넘는다 — 대신
    **검산이 실제로 본 그 행**을 따로 싣는다. 그 판정의 근거가 그 행이다.
    """
    at = _excerpt_spot(text)
    if at is None:
        return ""
    seg, m = _total_row(text, at)
    if m is None:
        return ""
    return re.sub(r"\s+", " ", seg[m.start():m.start() + width]).strip()


# ⚠️ KONEX(`.KN`)도 국내다 — `screener` 가 `.KS/.KQ/.KN` 을 KR 로 적는다.
# 열거를 쓰되 목록이 갈리지 않게 회귀가 대조한다(#24).
_KR_SUFFIXES = ("KS", "KQ", "KN")
_KR_SUFFIX_RE = re.compile(r"^(\d{6})\.(?:" + "|".join(_KR_SUFFIXES) + r")$")


def norm_miss_ticker(ticker) -> str:
    """미스 집계용 표기 통일 — `000660.KS` 와 `000660` 은 같은 종목이다.

    ⚠️ **국내 6자리 + KS/KQ/KN 만** 뗀다. 해외는 접미가 시장을 가르므로
    (`7203.T`) 떼면 다른 나라 코드와 충돌한다.
    """
    t = str(ticker or "")
    m = _KR_SUFFIX_RE.match(t)
    return m.group(1) if m else t


# 옛 어휘를 걷어낸 사실을 남기는 **묘비**. 없으면 보고서가 299건에서 몇 건
# 으로 조용히 줄어든다 — 왜 줄었는지 말할 방법이 사라진다(#43, 2026-09-04
# 독립 리뷰). 30일이 지나면 말하지 않는다(고칠 수 없는 경고를 매번 띄우면
# 진짜 경고를 가린다, #260).
_TOMB_KEY = "legacy_dropped"
_TOMB_SAY_DAYS = 30


def parse_miss_line(line: str) -> dict | None:
    """기록 한 줄 → dict. 깨진 줄은 None(옛 어휘가 **아니다**)."""
    try:
        rec = _json.loads(line)
    except Exception:                                          # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) else None


def is_current_vocab(line: str) -> bool:
    """기록 한 줄이 **지금 어휘**로 쓰였나. 읽는 쪽·쓰는 쪽이 같은 판정을
    써야 한다(#38) — 한쪽만 걸러내면 총계와 소계가 갈린다(#45).

    ⚠️ 깨진 줄은 True 로 둔다 — False 면 '옛 어휘' 로 세어져 **틀린 사유**를
    말한다(2026-09-04 독립 리뷰). 어휘 판정과 파싱 실패는 다른 갈래다(#82).
    """
    rec = parse_miss_line(line)
    return rec is None or rec.get("dv") == _DETAIL_VOCAB


def _tomb_count(rows: list) -> int:
    return sum(int(r[_TOMB_KEY]) for r in rows
               if isinstance(r, dict) and r.get(_TOMB_KEY))


def legacy_notice(rows: list, now: float | None = None) -> str:
    """묘비를 읽어 '옛 어휘 N건 제외' 한 줄. 없거나 오래됐으면 빈 문자열."""
    import time
    now = time.time() if now is None else now
    n = _tomb_count(rows)
    at = max([float(r.get("at") or 0) for r in rows
              if isinstance(r, dict) and r.get(_TOMB_KEY)] or [0])
    if not n or now - at > _TOMB_SAY_DAYS * 86400:
        return ""
    return (f"옛 어휘 {n}건 제외 — 진단 문구가 바뀌었습니다. "
            "다음 조회부터 새 어휘로 다시 쌓입니다.")


def miss_key(rec: dict) -> tuple:
    """기록 한 줄의 **신원** — 같은 종목·분기·사유는 한 줄이다.

    ⚠️ 줄 **전체**를 비교하면(옛 `line in old`) 필드를 하나 더하는 순간
    같은 미스가 두 줄로 쌓여 총계가 부푼다(#45 총계와 소계가 다른 모집단).
    신원은 표기가 아니라 필드로 정한다.

    ⚠️ `detail`·`ex` 는 **신원이 아니라 관측**이라 빠진다. 넣으면 파서를
    고친 뒤 같은 종목·분기를 다시 조회했을 때 관문 문구가 달라져 key 가
    갈리고, **이미 고친 상태의 원문**이 '파서를 고칠 유일한 근거' 로 계속
    실린다(#18 구워진 데이터). `_parse_sig` 가 파서 변경 시 캐시를 무효화
    하므로 그 재조회는 파서를 고칠 때마다 반드시 일어난다. 사유가 다르면
    다른 미스이므로(`형식미지원` ↔ `시계열이상`) `reason` 은 신원이다.
    """
    if not isinstance(rec, dict):
        return ()
    return (norm_miss_ticker(rec.get("ticker")), rec.get("year"),
            rec.get("reprt"), rec.get("reason"))


def excerpt_samples(rows: list) -> list[dict]:
    """갈래마다 **원문 발췌 한 건**, 큰 갈래부터. 기본은 **전부**.

    ⚠️ 히스토그램만으론 파서를 못 고친다 — 같은 보고서가 범위(#105)·관문
    (#107)·어휘(#109)·창(#275) 넷을 연달아 오진하게 만든 이유가 '원문이
    없어서'다. 보고서와 CLI 가 **같은 선택기**를 써야 통계가 안 갈린다(#38).
    """
    groups: dict[str, list] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        # ⚠️ 묘비 줄은 따로 안 거른다 — `ex` 가 없어 아래 발췌 게이트가
        # 이미 버린다. 따로 두면 도달 경로가 없는 가드가 하나 는다(#291).
        groups.setdefault(r.get("detail") or r.get("reason") or "?", []).append(r)
    out = []
    for kind, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        # ⚠️ **지금 파서가 본 표본을 먼저** 고른다 — 옛 파서의 표본은 이미
        # 고쳐진 형태일 수 있다(2026-10-02 391710: 보고서가 실은 표본을 지금
        # 파서가 읽었다). 없으면 옛 것을 싣되 그렇다고 표시한다(#43).
        pick = (next((i for i in items if i.get("ex") and seen_by_current(i)),
                     None)
                or next((i for i in items if i.get("ex")), None))
        if pick is None:
            continue            # 이 갈래엔 아직 발췌가 없다 — 지어내지 않는다
        out.append({"kind": kind, "n": len(items),
                    "ticker": norm_miss_ticker(pick.get("ticker")),
                    "year": pick.get("year"), "reprt": pick.get("reprt"),
                    "at": pick.get("at"), "ex": str(pick.get("ex") or ""),
                    "exs": str(pick.get("exs") or ""),
                    "old_parser": not seen_by_current(pick)})
    return out


def seen_by_current(rec: dict) -> bool:
    """그 줄을 **지금 파서**가 남겼나 — 줄의 파서 지문(`ps`)으로 가른다.

    ⚠️ 지문이 없는 줄(2026-10-02 이전)은 옛 파서다. 파서를 고쳐도 원장 줄은
    그 종목을 누가 다시 열 때까지 그대로라, 보고서가 **이미 고쳐진 표본**을
    '고칠 근거' 로 실었다(391710 — #92 이미 고친 것을 또 고치러 간다).
    """
    return isinstance(rec, dict) and rec.get("ps") == _parse_sig()


def excerpt_line(sm: dict) -> str:
    """발췌 한 건의 머리줄(HTML). 화면·CLI 가 같은 문구를 쓴다(#38).

    기록 날짜를 같이 적는다 — 파서를 고친 뒤 남아 있는 옛 관측인지
    이번 라운드의 것인지 읽는 쪽이 구별할 수 있어야 한다(#43·#114).
    """
    when = _kst_day(sm.get("at"))
    return (f"· [{_html.escape(str(sm.get('kind')))}] "
            f"{_html.escape(str(sm.get('ticker')))} "
            f"{sm.get('year')}/{sm.get('reprt')}"
            + (f" · 기록 {when}" if when else "")
            # 지금 파서가 다시 안 본 표본 — 이미 고쳐졌을 수 있다(#43·#92).
            + (" · 옛 파서" if sm.get("old_parser") else ""))


def _kst_day(ts) -> str:
    """기록 시각 → `MM-DD`(KST 명시계산 — 서버 로컬타임 의존 금지, 규칙 10a)."""
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(
            float(ts), _dt.timezone(_dt.timedelta(hours=9))).strftime("%m-%d")
    except Exception:                                          # noqa: BLE001
        return ""                       # 옛 줄엔 `at` 이 없다 — 말하지 않는다


def excerpt_missing_note(rows: list) -> str:
    """발췌 없는 기록을 **사유별로** 센 한 줄. 없으면 빈 문자열 — 0 건이면
    말하지 않는다(늘 뜨는 경고는 아무것도 안 재는 것과 같다, #25·#260).

    ⚠️ 원인을 하나로 단정하지 않는다(#165). '발췌 도입 전에 쌓인 줄' 은
    `형식미지원` 계열엔 맞지만 `시계열이상` 은 **원문 없이** 기록되므로
    (`quarterly_infographic` 이 조립된 시계열만 보고 남긴다) 영원히 발췌가
    없다 — "다음 조회부터 붙습니다" 가 거짓이 된다(#55). 갈래를 이름으로
    말하면 읽는 쪽이 어느 쪽인지 안다(#82).
    """
    from collections import Counter
    by = Counter(r.get("reason") or "?" for r in rows
                 if isinstance(r, dict) and not r.get(_TOMB_KEY)
                 and not r.get("ex"))
    if not by:
        return ""
    inner = " · ".join(f"{k} {n}" for k, n in by.most_common())
    return f"발췌 없는 기록 {sum(by.values())}건({inner})"


def _u16len(s: str) -> int:
    """텔레그램이 세는 단위(UTF-16 코드유닛)."""
    return len(s.encode("utf-16-le")) // 2


def refetchable(rec) -> bool:
    """원문을 **다시 받아** 판정을 갱신할 수 있는 줄인가.

    `시계열이상` 은 원문 없이 기록된다(`quarterly_infographic` 이 조립된
    시계열만 보고 남긴다) — 다시 받을 원문이 없으니 재조회 대상이 아니고,
    그 줄은 차트를 다시 열 때 갱신된다. 재조회 대상(`refill_targets`)과
    보고서의 '옛 파서 관측' 계수가 **같은 술어**를 쓴다 — 따로 적으면 경고가
    "`--refill` 이 갱신" 이라는 **지킬 수 없는 처방**을 단다(#38·#380).
    """
    return isinstance(rec, dict) and rec.get("reason") != MISS_SERIES_ANOMALY


def refill_targets(rows: list) -> list[dict]:
    """다시 조회할 미스의 (종목·분기·사유) 목록 — 발췌가 없거나 **옛 파서**가
    남긴 줄.

    발췌를 남기기 전에 쌓인 줄은 그 종목·분기를 누군가 다시 열 때까지
    영원히 근거가 없다. 격주 보고서는 2주에 한 번이므로, 그때까지 기다리는
    대신 한 번에 되메울 수 있어야 한다(§Automation-first).

    ⚠️ 발췌가 **있어도** 옛 파서의 관측이면 다시 본다(2026-10-02). 그 줄은
    파서를 고친 뒤에도 원장에 그대로 남아, 391710 처럼 지금 파서가 읽는
    표가 '고칠 것' 으로 계속 실렸다(#92·#18).

    ⚠️ 신원으로 중복을 없앤다 — 같은 줄을 두 번 조회하면 그만큼 바깥
    원천을 두드린다(#61).
    """
    seen, out = set(), []
    for r in rows:
        if not isinstance(r, dict) or r.get(_TOMB_KEY):
            continue
        if r.get("ex") and seen_by_current(r):
            continue        # 지금 파서가 본 발췌가 있다 — 다시 볼 이유가 없다
        if not refetchable(r):
            continue        # 원문 없이 기록된다 — 되메울 원문이 없다
        k = miss_key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    # ⚠️ 원문을 못 받은 줄은 **뒤로 민다**. 되메울 원문이 없으니 상한(예산)을
    # 선점하면 진짜 되메울 줄이 영영 안 걸린다(2026-09-18 독립 리뷰 H1).
    # 지우지는 않는다 — 원천이 나중에 문서를 주면 그때 붙는다(#171).
    out.sort(key=lambda r: r.get("reason") == MISS_NO_DOC)
    return out


def drop_miss(ticker, year, reprt_code, reason: str) -> bool:
    """해소된 미스 한 줄을 지운다. 지웠으면 True.

    ⚠️ 다시 조회해 **값이 나오면** 그 줄은 더 이상 개선 여지가 아니다 —
    남겨 두면 다음 보고서의 '막힌 조회 N건' 이 영원히 부푼다(#45). 원장은
    `_parse_sig` 가 바뀔 때 자동으로 비워지지 않으므로(캐시만 무효화된다)
    지우는 쪽이 있어야 수렴한다.
    """
    if not _MISS_LOG.exists():
        return False
    key = miss_key({"ticker": ticker, "year": year,
                    "reprt": reprt_code, "reason": reason})
    with _LEDGER_LOCK:
        raw = _MISS_LOG.read_text(encoding="utf-8")
        kept = []
        for ln in raw.splitlines():
            r = parse_miss_line(ln)
            if r is not None and not r.get(_TOMB_KEY) and miss_key(r) == key:
                continue
            kept.append(ln)
        body = ("\n".join(kept) + "\n") if kept else ""
        if body == raw:
            return False
        _write_ledger(body)
    return True


def _write_ledger(body: str) -> None:
    """원장 갈아끼우기 — tmp+replace. `write_text` 는 truncate 후 쓰기라
    읽는 쪽이 **찢긴 파일**을 본다(#379·#384). 쓰는 곳이 둘이므로 한 곳에
    둔다(#38).

    ⚠️ 임시 파일 이름에 **프로세스 번호**를 싣는다 — 원장을 쓰는 프로세스가
    여럿이다(대시보드의 차트 조회 · CLI `--refill` · `production_format_probe`,
    2026-10-02 부터 봇의 격주 재조회도). 같은 임시 파일을 둘이 동시에 쓰면
    섞인 파일이 원장으로 옮겨진다. 이 함수를
    부르는 쪽은 `_LEDGER_LOCK` 을 쥔다(같은 프로세스의 스레드끼리).
    ⚠️ 못 보는 축(#274): 프로세스 **사이**의 read-modify-write 유실은 남는다
    — 원장은 진단용이고 다음 조회가 다시 남긴다."""
    _MISS_LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = _MISS_LOG.with_name(f"{_MISS_LOG.name}.{os.getpid()}.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(_MISS_LOG)


# 원장 read-modify-write 의 스레드 직렬화. 차트 한 장이 분기 4개를 **병렬**로
# 조회하고(`map_bounded`), 2026-10-02 부터 성공한 조회도 원장을 고친다 —
# 락이 없으면 한 스레드의 지우기를 다른 스레드의 쓰기가 덮는다. 지금은 락을
# 쥔 채 다시 잡는 경로가 없다(`_log_miss` 는 `drop_fixable` 을 락 **밖**에서
# 부른다) — RLock 은 그런 경로가 생겨도 교착하지 않게 둔 것이다.
_LEDGER_LOCK = threading.RLock()


# 원천에 **낼 값이 없다** — 원장에 남기지 않는다(`_log_miss`). 남기면 보고서가
# 고칠 수 없는 것을 '고칠 것' 으로 센다(#93·#260).
SOURCE_HAS_NO_VALUE = ("미공시", "명시적미공시", MISS_NON_POSITIVE)
# 파서로는 못 고치는 사유 — 원천에 값이 없거나(미공시류) 원문을 못 받았거나
# 원문 경로가 아니다. **여집합이 개선 여지**라, `diagnose` 가 새 관문 사유를
# 내면 자동으로 '고칠 것' 으로 분류된다(#24 목록을 우리가 들면 새 항목을 못 잡는다).
NON_FIXABLE_REASONS = SOURCE_HAS_NO_VALUE + (MISS_NO_DOC, MISS_SERIES_ANOMALY)


def drop_fixable(ticker, year, reprt_code) -> int:
    """그 종목·분기의 **개선 여지 줄**을 지운다. 지운 줄 수.

    ⚠️ `drop_miss` 는 사유까지 신원으로 보므로(`miss_key`) 재분류를 못 덮는다 —
    같은 건이 `형식미지원` 과 새 사유로 두 줄이 되거나, 새 사유가 기록되지 않는
    분류(미공시류)면 옛 줄만 남아 보고서가 계속 그걸 '고칠 수 있는 것' 으로
    센다(독립 리뷰 2026-09-18 H2 실측). 여기서는 사유를 **여집합**으로 보고
    신원(종목·연도·보고서)으로만 지운다.
    """
    if not _MISS_LOG.exists():
        return 0
    want = (norm_miss_ticker(ticker), year, reprt_code)
    with _LEDGER_LOCK:
        raw = _MISS_LOG.read_text(encoding="utf-8")
        # 성공한 조회마다 불린다(2026-10-02) — 그 종목이 원장에 없으면 줄마다
        # JSON 을 풀지 않고 바로 돌아간다(차트 경로의 비용, #119).
        if want[0] not in raw:
            return 0
        kept, n = [], 0
        for ln in raw.splitlines():
            r = parse_miss_line(ln)
            if (r is not None and not r.get(_TOMB_KEY)
                    and (norm_miss_ticker(r.get("ticker")), r.get("year"),
                         r.get("reprt")) == want
                    and r.get("reason") not in NON_FIXABLE_REASONS):
                n += 1
                continue
            kept.append(ln)
        if n:
            _write_ledger(("\n".join(kept) + "\n") if kept else "")
    return n


def _log_miss(ticker: str, year, reprt_code, reason: str,
              detail: str = "", excerpt: str = "", total: str = "") -> None:
    """미스 1건 기록. 실패는 조용히 삼킨다 — 진단 로그가 본 기능을 막으면 안 된다."""
    if reason in SOURCE_HAS_NO_VALUE:
        # ⚠️ 그냥 돌아가면 **이미 쌓인 개선 여지 줄이 그대로 남는다** —
        # `miss_key` 에 사유가 들어가므로 새 분류가 옛 줄을 덮지 않고, 격주
        # 보고서는 다음 사람이 `--refill` 을 손으로 돌릴 때까지 그 건을 계속
        # '고칠 수 있는 것' 으로 센다(§Automation-first 운영자 반복명령을
        # 요구하는 fix 는 잘못된 fix · #11 배포완료 ≠ 화면에 보임 · #45).
        # 독립 리뷰 2026-09-18 H2 가 원장 실측으로 재현했다.
        try:
            drop_fixable(ticker, year, reprt_code)
        except Exception:                                   # noqa: BLE001
            log.debug("backlog miss 재분류 정리 실패 %s", ticker, exc_info=True)
        return                      # 원천에 값이 없다 — 개선 대상 아님
    try:
        _MISS_LOG.parent.mkdir(parents=True, exist_ok=True)
        import time
        rec = {"ticker": norm_miss_ticker(ticker), "year": year,
               "reprt": reprt_code, "reason": reason, "dv": _DETAIL_VOCAB,
               # 발췌가 언제 관측된 것인지 — 파서를 고친 뒤 남은 옛 관측과
               # 이번 라운드를 구별할 방법이 없으면 근거가 거짓이 된다(#114).
               "at": int(time.time()),
               # **어느 파서**가 본 관측인지 — 날짜만으론 배포 전후를 못
               # 가른다(2026-10-02 391710 '기록 09-19' 가 고친 날과 같은
               # 날이었다, #364·#21 진단은 어느 코드에서 나왔는지 말할 것).
               "ps": _parse_sig()}
        if detail:
            # 사유만으론 뭘 고쳐야 할지 모른다(#93) — 상세를 같이 남긴다.
            rec["detail"] = detail
        if excerpt:
            # ⚠️ 상세까지 남겨도 **어떤 열 구성인지**는 원문만 답한다 — 발췌를
            # 여기서 버리면 보고서를 받을 때마다 운영자가 `--ticker` 로 다시
            # 받아야 하고(§Automation-first: 운영자 반복명령을 요구하는 fix 는
            # 잘못된 fix), 그 왕복이 없으면 히스토그램만 보고 파서를 고치게
            # 된다 — 이 보고서가 그렇게 네 번 오진을 냈다(#105·#107·#109·#275).
            rec["ex"] = excerpt[:_EXCERPT_CAP]
        if total:
            # 검산이 본 **합계행** — 머리 발췌 창 밖일 때가 많다(영풍 000670).
            rec["exs"] = total[:_TOTAL_EX_WIDTH]
        line = _json.dumps(rec, ensure_ascii=False)
        with _LEDGER_LOCK:
            raw = (_MISS_LOG.read_text(encoding="utf-8")
                   if _MISS_LOG.exists() else "")
            old = raw.splitlines()[-_MISS_CAP:]
            # 옛 어휘 줄은 **쓰는 김에 걷어낸다**. 남겨 두면 4000줄 캡이 돌 때까지
            # 보고서를 지배하고, 어휘가 달라 아래 신원 비교(`miss_key`)로도
            # 합쳐지지 않아 같은 건이 두 줄로 쌓인다. 파서 지문(`_parse_sig`)이 바뀌면 캐시가
            # 무효라 다음 조회에서 새 어휘로 다시 쌓인다 — 잃는 정보가 없다.
            keep = [ln for ln in old if is_current_vocab(ln)]
            dropped = len(old) - len(keep)
            old = keep
            if dropped:
                prev = sum(int((parse_miss_line(ln) or {}).get(_TOMB_KEY) or 0)
                           for ln in old)
                old = [ln for ln in old
                       if not (parse_miss_line(ln) or {}).get(_TOMB_KEY)]
                old.append(_json.dumps(
                    {"dv": _DETAIL_VOCAB, _TOMB_KEY: prev + dropped,
                     "at": int(time.time())}, ensure_ascii=False))
            # ⚠️ 신원이 같은 **옛 줄은 이 줄이 대신한다**. 줄 전체 비교만 두면
            # 발췌 같은 필드를 더하는 순간 같은 미스가 두 줄로 쌓여 보고서의
            # 건수가 부푼다(#45).
            key = miss_key(rec)
            kept = []
            for ln in old:
                r = parse_miss_line(ln)
                if r is not None and not r.get(_TOMB_KEY) and miss_key(r) == key:
                    continue
                kept.append(ln)
            body = "\n".join(kept + [line]) + "\n"
            # ⚠️ 남는 축: 프로세스 **사이**의 동시 쓰기 유실은 그대로다
            # (`_write_ledger` 독스트링 — 원장은 진단용이고 다음 조회가 다시 남긴다).
            _write_ledger(body)
    except Exception as exc:
        log.debug("dart_backlog: 미스 로그 실패: %s", exc)


# 「나. 수주에 관한 사항」은 **지배회사 → 종속회사** 순으로 쓴다. 종속회사
# 표가 먼저 잡히면 본사 잔고 대신 자회사 잔고가 화면에 찍힌다 — 실측
# (2026-08-18 `--explain 047810`): 한국항공우주 25.3Q 가 제노코(자회사)의
# 821억을 잡아 **26조를 0.08조로** 보고했다. 종속회사 구간을 잘라낸다.
_SUBSIDIARY_HEAD = re.compile(r"\[\s*종속회사의\s*내용\s*\]|○\s*종속회사")
_SCOPE_KW = re.compile(r"수주잔고|수주잔액|계약잔액|기말수주잔")


def _parent_scope(text: str) -> str | None:
    """종속회사 구간 앞까지 = 지배회사(본사) 영역. 마커가 없으면 None."""
    m = _SUBSIDIARY_HEAD.search(text)
    if not m or m.start() < 200:
        return None
    head = text[:m.start()]
    return head if _SCOPE_KW.search(head) else None


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
    _fns = (_parse_table, _parse_domestic_export,
            _parse_balance_column, _parse_transposed,
            _parse_open_close, _parse_contract_table, _parse_project_rows,
            _parse_rolling,
            _parse_xbrl, _parse_single, _parse_prose)
    # ⚠️ **지배회사 구간을 먼저** 훑는다. 전체를 훑으면 종속회사 표가 먼저
    # 걸려 본사 잔고 자리에 자회사 값이 들어간다(위 주석의 실측 사례).
    for scope in (_parent_scope(text), text):
        if not scope:
            continue
        for fn in _fns:
            try:
                got = fn(scope)
            except Exception as exc:        # 한 형태의 실패가 나머지를 막지 않게
                log.debug("dart_backlog: %s 실패: %s", fn.__name__, exc)
                continue
            if not got:
                continue
            value, form = got
            return {"value": value, "form": form}
    return None


def _cut_note(n: int) -> str:
    """길이 한도로 못 실은 갈래 수 — 자른 사실과 **전부 보는 법**을 말한다(#45)."""
    return (f"… 갈래 {n}개는 길이 한도로 생략 — "
            f"<code>{_CLI_CMD}</code> 가 전부 보여줍니다.")


def _fit_message(body: list, tail: list) -> str:
    """한도를 넘으면 목록 줄(`· …`)을 뒤에서부터 덜어내고 **덜어낸 수를 말한다**.

    ⚠️ 예산 검사가 발췌 루프 **안에만** 있으면 발췌가 하나도 없을 때
    상세 8줄만으로 한도를 넘긴다(독립 리뷰 실측 4,173 u16). 여기서 한 번 더 잰다.
    ⚠️ 꼬리말(안내·명령)은 안 덜어낸다 — 그게 다음 수를 정하는 줄이다.
    ⚠️ 더 못 줄이면 그대로 내보낸다 — 그때는 '줄였다' 고 말하지 않는다(#165).
    """
    def _n(rows):
        return _u16len("\n".join(rows))

    cut = 0
    while _n(body + ["", _trim_note(cut + 1)] + tail) > _DM_LIMIT:
        # 뒤에서부터 덜어낸다 — 앞쪽이 요약(건수·사유)이라 더 중요하다.
        drop = next((i for i in range(len(body) - 1, -1, -1)
                     if body[i].startswith("· ")), None)
        if drop is None:
            break
        body.pop(drop)
        cut += 1
    if not cut:
        return "\n".join(body + tail)
    return "\n".join(body + ["", _trim_note(cut)] + tail)


def _trim_note(n: int) -> str:
    return (f"… 길이 한도로 {n}줄 생략 — <code>{_CLI_CMD}</code> 가 전부 "
            "보여줍니다.")


def _ledger_rows() -> tuple[list, list, int]:
    """원장 → (현행 어휘 행, 전체 레코드, 옛 어휘 건수).

    보고서(`review_text`)·CLI(`backlog_misses`)·발송 전 재조회가 **같은
    술어**를 써야 셋이 다른 모집단을 보지 않는다(#35·#38·#45)."""
    rows, legacy, all_rec = [], 0, []
    if not _MISS_LOG.exists():
        return rows, all_rec, legacy
    for ln in _MISS_LOG.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        rec = parse_miss_line(ln)
        if rec is None:
            continue
        all_rec.append(rec)
        if rec.get(_TOMB_KEY):
            continue            # 묘비는 기록이 아니다 — 세지 않는다(#45)
        # 옛 어휘 줄은 **세지 않되 말한다**(#43). 2026-09-04 실측: 진단이
        # 파서와 다른 창을 봐 `미지원단위 (단위 : 사) 30건` 을 최대 버킷으로
        # 올렸는데, 그 줄들이 남아 있으면 fix 뒤에도 같은 보고서가 나간다.
        if rec.get("dv") != _DETAIL_VOCAB:
            legacy += 1
            continue
        rows.append(rec)
    legacy += _tomb_count(all_rec)
    return rows, all_rec, legacy


def refresh_note(rf: dict | None) -> str:
    """발송 전 재조회 결과 한 줄 — 없거나 할 게 없었으면 빈 문자열(#25·#260).

    ⚠️ 못 했으면 **못 했다고** 말한다 — 조용히 넘어가면 아래 '옛 파서' 줄이
    왜 남았는지 읽는 쪽이 모른다(#43·#82)."""
    if not rf:
        return ""
    if rf.get("skipped"):
        return f"⚠️ 발송 전 재조회 못 함 — {_html.escape(str(rf['skipped']))}"
    tried = int(rf.get("tried") or 0)
    if not tried:
        return ""
    parts = [f"해소 {rf.get('solved', 0)}", f"그대로 {rf.get('same', 0)}"]
    for k, lbl in (("changed", "사유 바뀜"), ("nodoc", "원문 없음"),
                   ("failed", "조회 실패")):
        if rf.get(k):
            parts.append(f"{lbl} {rf[k]}")
    left = int(rf.get("left") or 0)
    return (f"발송 전 지금 파서로 다시 조회 {tried}건 — " + " · ".join(parts)
            + (f" · 상한으로 {left}건은 다음 회차" if left else ""))


def review_text(refreshed: dict | None = None) -> str:
    """미스 요약 HTML — 보낼 게 없으면 빈 문자열.

    ⚠️ 기록 자체가 **개선 여지 있는 사유만** 담는다(미공시는 `_log_miss` 가
    거른다). 그래서 여기 뭔가 있다는 건 곧 '새 형식이 나타났다' 는 뜻이다.

    `refreshed` = 발송 직전 재조회(`refresh_misses`) 결과 — 그 사실을 한 줄로
    싣는다. 원장은 그 재조회가 이미 갱신해 두었다.
    """
    from collections import Counter
    if not _MISS_LOG.exists():
        return ""
    rows, all_rec, legacy = _ledger_rows()
    if not rows:
        return ""
    by = Counter(r.get("reason", "?") for r in rows)
    tick = Counter(norm_miss_ticker(r.get("ticker")) for r in rows)
    out = [f"📐 <b>수주잔고 파서 리뷰</b> (격주 금요일)",
           f"막힌 조회 {len(rows)}건 · 종목 {len(tick)}개",
           ""]
    note = legacy_notice(all_rec) if legacy else ""
    if legacy and not note:
        # 묘비가 오래됐거나 없다 — 그래도 이번 판정에서 뺀 사실은 말한다(#41).
        note = f"옛 어휘 {legacy}건 제외"
    if note:
        out += [f"⚠️ {note}", ""]
    rf_line = refresh_note(refreshed)
    if rf_line:
        out += [rf_line, ""]
    # ⚠️ 지금 파서가 다시 안 본 줄은 **이미 고쳐졌을 수 있다**(2026-10-02
    # 391710 — 보고서가 실은 표본을 지금 파서가 4,015백만원으로 읽었다).
    # 세지 않고 섞으면 읽는 쪽이 고친 것을 또 고친다(#92·#43).
    # ⚠️ 다시 받을 원문이 있는 줄만 센다(`refetchable`) — 아래 처방
    # (`--refill`)이 `시계열이상` 줄은 안 건드리기 때문이다(#380).
    stale = sum(1 for r in rows if refetchable(r) and not seen_by_current(r))
    if stale:
        # 처방은 `--refill` 하나다 — 차트를 다시 여는 건 최근 분기만 다시 보고
        # 같은 사유의 줄만 갈아 끼운다(리뷰 L3: '다시 열면' 은 부분 사실).
        out += [f"⚠️ 이 중 {stale}건은 지금 파서가 아직 다시 보지 않은 관측 — "
                "이미 고쳐졌을 수 있습니다"
                f"(<code>{_CLI_CMD} --refill</code> 이 다시 봅니다)", ""]
    out += [f"· {r}: {n}건" for r, n in by.most_common()]
    det = Counter(r.get("detail") for r in rows if r.get("detail"))
    if det:
        # 사유만 세면 "단위없음 15" 로 끝나 다음 수를 못 정한다(#93).
        out += ["", "<b>상세</b> (무엇을 고쳐야 하나)"]
        # ⚠️ `detail` 은 DART 원문(캡션 24자)을 물고 온다 — escape 를
        # 빠뜨리면 `<` 하나에 메시지 전체가 안 간다(규칙 7). 바로 아래
        # 발췌 블록은 escape 하는데 여기만 raw 였다(#38).
        out += [f"· {_html.escape(str(d))}: {n}건"
                for d, n in det.most_common(8)]
    out += ["", "<b>종목</b> (상위 10)"]
    out += [f"· {t} ×{n}" for t, n in tick.most_common(10)]
    tail = []
    no_ex = excerpt_missing_note(rows)
    if no_ex:
        # 발췌 없는 줄을 침묵으로 두면 '원문이 없는 갈래' 로 읽힌다(#43·#54).
        tail += ["", f"⚠️ {no_ex}"]
    tail += ["", "이 목록을 Claude 에게 그대로 붙여넣으면 파서를 확장합니다.",
             f"원문 확인: <code>{_CLI_CMD} --ticker &lt;코드&gt;</code>"]
    samples = excerpt_samples(rows)
    if samples:
        head = ["", f"<b>원문 발췌</b> (갈래마다 1건 · 앞 {_DM_EX_WIDTH}자 — "
                    "파서를 고칠 유일한 근거)"]
        body: list[str] = []
        for sm in samples:
            ex = _html.escape(sm["ex"][:_DM_EX_WIDTH])
            blk = f"{excerpt_line(sm)}\n<code>{ex}</code>"
            if sm.get("exs"):
                # 검산이 본 **합계행** — 머리 발췌 창 밖일 때가 많다(영풍 000670).
                blk += (f"\n합계행 <code>"
                        f"{_html.escape(sm['exs'][:_DM_TOTAL_WIDTH])}</code>")
            # ⚠️ **보낼 메시지 전체**를 잰다. 예산을 따로 빼 두면 그 식의
            # 피연산자 하나(머리말)를 빠뜨리는 변형이 안 잡히고 실제로
            # 4,106 u16 가 나갔다(2026-09-18 독립 리뷰 실측, #20·#291).
            # 잘릴 때 붙는 안내 줄도 **미리 자리를 잡아 둔다** — 마지막에
            # 붙이면 그 줄이 한도를 넘긴다.
            trial = out + head + body + [blk, _cut_note(len(samples))] + tail
            if _u16len("\n".join(trial)) > _DM_LIMIT:
                break
            body.append(blk)
        if body:
            if len(body) < len(samples):
                body.append(_cut_note(len(samples) - len(body)))
            out += head + body
    # ⚠️ 꼬리말은 따로 넘긴다 — 한도를 넘으면 목록만 덜어내고 안내는 남긴다.
    return _fit_message(out, tail)


# 발송 전 재조회 상한 — 미스 1건당 정기보고서 1건(최대 40MB)을 받는다.
# CLI `--refill` 의 기본값과 **같은 값**이다(두 곳에 적으면 갈린다, #38).
REFRESH_CAP = 40


def refill_rows(dart, todo: list, say=None) -> dict:
    """원장 줄들을 **지금 파서로** 다시 조회해 갱신한다 → 계수 dict.

    CLI `--refill` 과 격주 보고서 직전 재조회가 **같은 규율**을 쓴다(#38) —
    옛 판은 이 루프가 CLI 안에만 있어 보고서는 옛 관측을 그대로 실었다.

    ⚠️⚠️ **옛 줄은 이번에 원문을 읽었을 때만 지운다**(2026-09-18 독립 리뷰
    B1 실측): 첫 판은 "사유가 달라졌으면 지운다" 였는데, `backlog_probe` 는
    자기 예외를 삼켜 `오류:XxxError` 를 돌려주고(그 경로에선 새 줄이 **안**
    써진다) DART 일일한도는 예외 없이 빈 문서를 준다. 그래서 장애 한 번이
    게이트 분류(이 보고서의 존재 이유)를 지웠다 — 실측 3줄 → **0줄**.
    프로브가 실은 `doc_len` 으로 "읽었나" 를 가른다.
    ⚠️ 한 줄씩 즉시 말한다(`say`) — 수십 분짜리를 끝날 때까지 침묵하면
    멈춘 건지 모른다(#103).
    """
    say = say or (lambda msg: log.info("backlog refill: %s", msg))
    c = {"tried": 0, "filled": 0, "solved": 0, "same": 0, "changed": 0,
         "nodoc": 0, "failed": 0}
    for i, r in enumerate(todo, 1):
        c["tried"] += 1
        tk, yr, rc = r.get("ticker"), r.get("year"), r.get("reprt")
        was = r.get("reason")
        # ⚠️ `out=` 을 넘겨 **캐시를 우회**한다 — 이 모듈의 규율이다(#35:
        # 감사·프로브는 화면 캐시를 타지 않는다). 캐시 히트면 `_log_miss` 가
        # 아예 안 돌아, 옛 줄만 지우고 아무것도 안 쓰는 경로가 열린다.
        box: dict = {}
        try:
            val, why = backlog_probe(dart, tk, yr, rc, out=box)
        except Exception as exc:                               # noqa: BLE001
            c["failed"] += 1
            say(f"[{i:2d}/{len(todo)}] {tk} {yr}/{rc}  ❌ "
                f"{type(exc).__name__}: {exc} — 옛 줄은 그대로 둔다")
            continue
        if val is not None:
            c["solved"] += 1
            drop_miss(tk, yr, rc, was)
            say(f"[{i:2d}/{len(todo)}] {tk} {yr}/{rc}  ✅ 해소 "
                f"{val/1e12:.3f}조 — 원장에서 지웠다")
            continue
        now = (why or "").split(" · ")[0]
        if not box:
            # 프로브가 내부에서 실패했다 — 새 줄이 안 써졌으므로 옛 관측을
            # 반증할 근거가 없다. 지우지도, 되메움으로 세지도 않는다.
            c["failed"] += 1
            say(f"[{i:2d}/{len(todo)}] {tk} {yr}/{rc}  ❌ {why} — "
                "옛 줄은 그대로 둔다")
            continue
        if not box.get("doc_len") or now == MISS_NO_DOC:
            # 원문을 못 받았다(원천 장애·일일한도·`status=014`). 파싱 판정을
            # 갱신할 근거가 없으므로 **옛 줄을 지우지 않는다**.
            # ⚠️ 그런데 `_log_miss` 가 방금 `원문미제공` 줄을 **새로** 썼다 —
            # 사유가 달라 신원이 다르기 때문이다. 그대로 두면 같은 분기가 두
            # 건으로 세어지므로(#45) 이 실행이 만든 그 줄만 도로 지운다.
            if now != was:
                drop_miss(tk, yr, rc, now)
            c["nodoc"] += 1
            say(f"[{i:2d}/{len(todo)}] {tk} {yr}/{rc}  ⏳ 원문 여전히 없음 "
                f"({why}) — 옛 줄은 그대로 둔다")
            continue
        # 여기부터는 **원문을 읽었다**. 사유가 그대로면 `_log_miss` 가 같은
        # 신원의 줄을 대체했고, 달라졌으면 옛 줄은 방금 반증된 관측이다(#45).
        c["filled"] += 1
        if now == was:
            c["same"] += 1
        else:
            c["changed"] += 1
            drop_miss(tk, yr, rc, was)
        say(f"[{i:2d}/{len(todo)}] {tk} {yr}/{rc}  ↻ {why}"
            + ("" if now == was else f"  ⚠️ 사유 바뀜(옛 줄 {was} 는 지웠다)"))
    return c


def dart_ready(dart) -> bool:
    """키가 있는 DART 클라이언트인가.

    ⚠️ `get_dart()` 는 키가 없어도 **항상** 클라이언트를 돌려준다(`DartClient`
    에 `__bool__` 이 없다) — `if not dart` 는 키 부재를 못 잡는 **도달 불가**
    검사였다(2026-10-02 셀프리뷰 실측). 안 잡으면 키 없는 날의 재조회가 전부
    `원문미제공` 으로 찍혀 계정 문제와 원천 장애가 구별되지 않는다(#82).
    재조회(`refresh_misses`)와 CLI(`backlog_misses`)가 **같은 술어**를 쓴다(#38).
    """
    return bool(dart) and bool(getattr(dart, "api_key", None))


def refresh_misses(cap: int = REFRESH_CAP) -> dict:
    """격주 보고서 직전 — **옛 파서가 남긴 줄**·발췌 없는 줄을 지금 파서로
    다시 본다(2026-10-02).

    ⚠️ 왜(사용자가 붙여 넣은 보고서가 증거다): 09-19 03:42 에 4열 롤링 파서를
    배포했는데 그날 기록된 391710 줄이 2주 뒤 보고서에 '고칠 것' 으로 실렸다
    — 그 종목을 아무도 다시 안 열면 원장은 영원히 옛 파서의 관측이다. 운영자가
    `--refill` 을 기억해 돌리는 건 이 보고서가 막으려던 '기억해야 하는 일'
    이다(§Automation-first).
    ⚠️ 할 게 없으면 DART 를 아예 안 부른다(#61). 키가 없으면 **못 했다고**
    돌려준다 — 보고서가 그 사실을 싣는다(#43·#54).
    """
    rows, _all, _legacy = _ledger_rows()
    todo = refill_targets(rows)
    if not todo:
        return {"tried": 0}
    from bot.dart_client import get_dart
    dart = get_dart()
    if not dart_ready(dart):
        return {"skipped": "DART_API_KEY 없음 — 옛 관측을 다시 못 봤다",
                "todo": len(todo)}
    c = refill_rows(dart, todo[:cap])
    c["todo"] = len(todo)
    c["left"] = max(0, len(todo) - cap)
    return c


def review_with_refresh(cap: int = REFRESH_CAP) -> str:
    """격주 보고서 본문 — **먼저 다시 본 뒤** 요약한다(봇 태스크가 부른다).

    ⚠️ 재조회의 실패가 보고서를 지우면 안 된다(#315 곁들이 하나가 본체를
    지운다) — 실패는 사유로 실리고 보고서는 그대로 나간다.
    """
    try:
        rf = refresh_misses(cap)
    except Exception as exc:                                   # noqa: BLE001
        log.exception("backlog 발송 전 재조회 실패")
        rf = {"skipped": f"재조회 중 오류({type(exc).__name__})"}
    return review_text(rf)


# 파싱 결과 디스크 캐시 — `tables_rolling`(dart_production) 과 같은 규약.
# 2026-08-22 실측: `/api/quarterly` 115초 중 `build_payload` 51.5초였고, 그
# 대부분이 여기다 — 분기마다 40MB 상한으로 원문을 받아 정규식으로 훑는다.
# 순수 CPU 라 GIL 을 붙잡아 옆 요청(차트의 pandas)까지 굶긴다(#119).
_BL_TTL = 24 * 3600


def _source_sig() -> str:
    try:
        import hashlib
        import pathlib
        return hashlib.sha1(
            pathlib.Path(__file__).read_bytes()).hexdigest()[:10]
    except Exception:                                          # noqa: BLE001
        return "nosig"                 # 지문을 못 구하면 캐시를 안 쓴다


# ⚠️ **import 시점에** 잰다(2026-10-02 독립 리뷰 L1). 처음 부를 때 재면 배포가
# 파일을 바꾼 뒤 재시작에 실패한 프로세스(= 옛 코드)가 **새 지문**을 찍는다 —
# 옛 파서의 관측이 '지금 파서' 로 둔갑해 보고서의 '옛 파서' 표시가 영영 안
# 붙는다(#365 지문은 디스크가 아니라 돌고 있는 코드를 재야 한다).
_BL_SIG: str = _source_sig()


def _parse_sig() -> str:
    """이 모듈 소스의 지문 — 파서를 고치면 캐시가 **자동으로** 무효가 된다.
    버전 상수를 손으로 올리는 방식은 이 레포에서 세 번 실패했다(#18·#21b·#95)."""
    return _BL_SIG


def _bl_key(ticker: str, year: int, reprt_code: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", f"{ticker}_{year}_{reprt_code}")
    return f"dart_backlog_{_parse_sig()}_{safe}.json"


def _bl_cached(key: str):
    if _parse_sig() == "nosig":
        return None
    try:
        from bot.finviz_client import _cached
        hit = _cached(key, ttl=_BL_TTL)
        if isinstance(hit, dict) and "why" in hit:
            return hit.get("v"), hit["why"]
    except Exception as exc:                                   # noqa: BLE001
        log.debug("backlog cache read(%s): %s", key, exc)
    return None


def _bl_cache_write(key: str, value, why: str) -> None:
    if _parse_sig() == "nosig":
        return
    try:
        from bot.finviz_client import _cache_write
        _cache_write(key, {"v": value, "why": why})
    except Exception as exc:                                   # noqa: BLE001
        log.debug("backlog cache write(%s): %s", key, exc)


def backlog_probe(dart, ticker: str, year: int, reprt_code: str,
                  out: dict | None = None) -> tuple[float | None, str]:
    """해당 분기 정기보고서의 (수주잔고 원, 판정). 값이 없으면 (None, 사유).

    ⚠️ 왜 사유를 **반환**하는가(2026-08-21). 커버리지가 9/40(22%)인데
    그게 "원천에 없다"인지 "파서가 못 읽는다"인지 알 방법이 없었다 —
    사유는 `_MISS_LOG` 에만 갔고 그마저 미공시류는 건너뛴다. 감사가 개선
    여지를 세려면 **미공시까지 포함한** 전체 분포가 필요하다(#54: 대조
    대상이 0건이면 통과가 아니다).

    ⚠️ 원문을 40MB 로 받는다 — 「매출 및 수주상황」은 목차상 II.사업의 내용
    뒤라 기본 3MB 상한 밖으로 밀리고, 그러면 **공시하는 회사도 '없음'으로
    오판된다**(2026-08-17 프로브로 확인)."""
    if not dart:
        # ⚠️ 계약은 (값, 사유) 튜플이다 — None 하나를 내면 호출부의
        # `v, why = backlog_probe(...)` 가 TypeError 로 터진다.
        return None, "DART없음"
    # ⚠️ `out` 을 요구하는 호출(감사·프로브)은 캐시를 안 탄다 — 원문 발췌·
    # 상세는 매번 원문에서 다시 뽑아야 진단이 신선하다(#35).
    ck = _bl_key(ticker, year, reprt_code) if out is None else ""
    if ck:
        hit = _bl_cached(ck)
        if hit is not None:
            return hit
    try:
        from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
        # ⚠️ 후보를 **순서대로** 시도한다. 가장 최근 접수건에 문서가 없는
        # 경우가 있어(한화에어로 사업보고서·1분기보고서 실측: document.xml 이
        # `status=014 파일이 존재하지 않습니다`) 1건만 보면 원본이 가려진다.
        reps = dart.find_periodic_reports(ticker, year, reprt_code)
        if not reps:
            rep = dart.find_periodic_report(ticker, year, reprt_code)
            reps = [rep] if rep and rep.get("rcept_no") else []
        text = ""
        for rep in reps:
            if not rep.get("rcept_no"):
                continue
            text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                                   max_bytes=_DOC_TEXT_MAX_FULL) or ""
            if text:
                break
        got = parse_backlog(text)
        if got:
            if ck and text:
                _bl_cache_write(ck, got["value"], "정상")
            # ⚠️ 값이 나왔으면 그 분기의 **개선 여지 줄은 이미 해소**다
            # (2026-10-02 391710 — 09-19 에 고친 표가 원장에 남아 2주 뒤 보고서가
            # 그걸 '고칠 것' 으로 실었다). 지우는 경로가 `--refill`(발췌 없는
            # 줄만) 하나뿐이었다. 원문을 **읽고** 값을 낸 경우라 옛 관측을
            # 반증할 근거가 있다(독립 리뷰 B1 규율). `시계열이상` 은 파싱
            # 성공이 반증하지 않으므로 남는다(`drop_fixable` 이 거른다).
            if text:
                try:
                    drop_fixable(ticker, year, reprt_code)
                except Exception:                           # noqa: BLE001
                    log.debug("backlog 해소 줄 정리 실패 %s", ticker,
                              exc_info=True)
            return got["value"], "정상"
        # 실사용이 곧 프로브 — 못 낸 이유를 남긴다(미공시류는 _log_miss 가 스킵).
        why = diagnose(text or "")
        det = diagnose_detail(text or "")
        ex = backlog_excerpt(text or "")
        tot = backlog_total_excerpt(text or "")
        if out is not None:
            out["detail"] = det
            out["excerpt"] = ex
            out["total"] = tot
            # ⚠️ 되메우기는 "이번에 원문을 **읽었나**" 를 알아야 한다 —
            # 안 읽었으면 옛 관측을 반증할 근거가 없다(독립 리뷰 B1).
            out["doc_len"] = len(text or "")
        _log_miss(ticker, year, reprt_code, why, det, ex, tot)
        # 사유만 돌려주면 "단위없음 15건"에서 멈춰 다음 수를 못 정한다 —
        # 상세를 붙여 감사 히스토그램이 곧 작업 목록이 되게 한다(#93).
        _why = f"{why} · {det}" if det else why
        # ⚠️ **원문을 받아 본 경우에만** 캐시한다. 원문미제공은 원천 장애일
        # 수 있는데 그걸 24시간 믿으면 공시하는 회사가 하루 종일 빈칸이 된다.
        if ck and text:
            _bl_cache_write(ck, None, _why)
        return None, _why
    except Exception as exc:
        log.debug("dart_backlog: %s %s/%s: %s", ticker, year, reprt_code, exc)
        return None, f"오류:{type(exc).__name__}"


def backlog_for(dart, ticker: str, year: int, reprt_code: str) -> float | None:
    """수주잔고(원)만. 판정이 필요하면 `backlog_probe` 를 쓴다.

    ⚠️ 수집 사다리를 복제하지 않는다 — 두 경로가 다른 접수건을 보면
    화면과 감사 통계가 갈라진다(#35·#38)."""
    return backlog_probe(dart, ticker, year, reprt_code)[0]
