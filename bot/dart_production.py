"""DART 정기보고서 「생산능력 및 생산실적」 표 → 원본 그대로 HTML. LLM 0·₩0·stdlib.

사용자 2026-08-20: "가동률이 중요한거야. 키는 그거야." — 분기실적 탭에 생산능력·
생산실적·**가동률** 표를 원본 형태로 싣는다(차트로 만들기 어려운 형태라 표 그대로).

⚠️ 이 모듈은 `dart_backlog`(수주잔고)와 **성격이 다르다.** 수주잔고는 값 하나를
뽑느라 회사마다 다른 표 형식과 싸우고 항등식 검산까지 필요했다. 여기서는 원본 표를
**그대로 재현**할 뿐 숫자를 해석하지 않으므로, "조용히 틀린 숫자" 라는 실패 모드가
구조적으로 없다. 남는 위험은 **엉뚱한 표를 집는 것** 하나뿐이라 선택 규칙에 집중한다.

2026-08-20 VM 실측(마이크로컨텍솔 098120, 반기·3분기·1분기·사업보고서 4종) 기준:

  · 원문 zip 안 마크업은 **대문자 DART 태그** — TABLE/TR/TD/TH/THEAD/TBODY/COLGROUP.
  · 앵커 `생산능력 및 생산실적` **직후 첫 TABLE 은 미끼**다 — `(단위 : 천개)` 한 칸
    짜리 캡션 표이고, 진짜 데이터 표는 그 다음이다. 첫 표를 집으면 단위 문구만 나온다.
  · 데이터 표: THEAD 에 `사업부문 | 품 목 | 구 분 | 제27기 | 제26기 | 제25기`,
    TBODY 의 `구 분` 열이 `생산능력 / 생산실적 / 가 동 률` 3행이고 사업부문·품목은
    ROWSPAN 으로 병합된다.
  · **`가 동 률` 은 글자 사이에 공백이 있다** — `가동률` 로 찾으면 못 잡는다.
  · 표 직후에 각주(`* 주요 품목을 기준으로…`)가 붙는다.
  · 4개 보고서 유형 전부에 표가 있다 → 분기마다 자연스럽게 롤링된다.

**표 선택 규칙**: 앵커 이후 표들 중 **가동률을 가진 표**를 우선한다(사용자가 원하는
핵심 지표). 가동률이 없으면 생산능력+생산실적을 함께 가진 표. 둘 다 없으면 None —
설비 소재지 표(`사업부문|품목|소재지|소유형태`) 같은 이웃 표를 집지 않기 위한 게이트다
(실측에서 앵커 이후 8개 표 중 3번째가 그 소재지 표였다).

**살균**: ROWSPAN/COLSPAN 만 남기고 나머지 속성(WIDTH·HEIGHT·ACOPY·ACLASS·VALIGN…)은
전부 버린다. 태그도 화이트리스트만 통과시킨다 — 원문은 외부 입력이므로 그대로 DOM 에
넣으면 안 된다(실수 #7 의 연장).
"""
from __future__ import annotations

import html as _html
import logging
import re
import threading

from bot.textwidth import vlen as _vlen

log = logging.getLogger("bot.dart_production")
# ⚠️ 스캔창 상수는 앵커 목록(`_PROD_SPECS`)의 **기본 인자**로 쓰이므로
# 정의부보다 위에 있어야 한다 — 아래 두면 import 자체가 NameError 다.
_MAX_TABLE_CHARS = 120_000
_SCAN_WINDOW = 40_000       # 정확 앵커(생산능력 및 생산실적) 이후 훑을 범위
# 절 제목(`생산 및 설비에 관한 사항`)은 **하위 항목이 여럿**이라 생산능력 표가
# 한참 뒤에 온다 — 40k 로는 설비 현황·소재지 표만 훑고 끝난다(2026-08-21 VM
# 스윕: '무관표만' 7건이 전부 13~21개 표를 보고도 최고점 0 이었다).
# 창을 넓혀도 **점수 게이트가 그대로**라 어구 없는 표는 여전히 0 점이다 —
# 넓힌다고 엉뚱한 표를 집지 않는다.
_SCAN_WINDOW_ALT = 200_000


# 어구 사이에 낄 수 있는 것 — 태그·공백·nbsp.
# ⚠️ 앵커는 **raw markup** 을 훑는다(표를 원본 구조 그대로 떠야 하므로).
# 그래서 평문 정규식으로 쓰면 제목이 `생산 및 <SPAN>설비</SPAN>에 관한 사항`
# 처럼 태그로 쪼개진 순간 못 잡는다 — 2026-08-21 VM 스윕에서 삼성전자
# (893만자·**잘리지도 않음**)·SK하이닉스·삼성바이오가 전부 '섹션없음'으로
# 찍힌 원인이다. 대형사 보고서일수록 제목에 서식 태그가 많이 붙는다.
# 상한을 둬 역추적 폭주를 막는다(글자 사이에 태그가 40개 넘게 낄 일은 없다).
_GAP = r"(?:<[^>]*>|\s|&nbsp;){0,40}"


def _anchor(text: str) -> "re.Pattern":
    """평문 어구 → **태그가 끼어도 잡는** 앵커. 글자 순서는 그대로 강제되므로
    느슨해져도 엉뚱한 곳에 걸리지 않는다."""
    return re.compile(_GAP.join(re.escape(c) for c in text if not c.isspace()),
                      re.I)


# 섹션 앵커. 회사마다 `(1) 제품 생산능력 및 생산실적` / `가. 생산능력 및 생산실적`
# 처럼 번호·접두가 달라 **핵심 어구만** 잡는다.
_ANCHOR = _anchor("생산능력및생산실적")
# 앵커가 없는 회사 대비 폴백 — 절 제목.
# ⚠️ **서식이 두 벌이다.** DART 기업공시서식 「II. 사업의 내용」의 현행
# 표준 목차는 `3. 원재료 및 생산설비` 이고, 옛 보고서는 `생산 및 설비에
# 관한 사항` 을 쓴다. 구 서식만 알고 있어서 삼성전자·SK하이닉스·삼성바이오가
# '섹션없음'으로 찍혔다 — 같은 문서에서 `주요 제품 및 서비스` 앵커는 멀쩡히
# 매칭됐으므로(스윕에 `제품` 표시) 태그 문제가 아니라 **제목이 달랐던** 것이다.
_ANCHOR_ALT = _anchor("생산및설비에관한사항")
_ANCHOR_ALT2 = _anchor("원재료및생산설비")

# (이름, 정규식, 스캔창) — **순서대로** 시도한다. 정확한 소항목 제목이 먼저고,
# 절 제목은 하위 항목이 여럿이라 창이 넓다. 목록을 여기 하나만 두어야
# `parse_production`·`diagnose`·스윕 프로브가 같은 판정을 낸다(#35/#54).
_PROD_SPECS = (("생산능력및생산실적", _ANCHOR, _SCAN_WINDOW),
               ("원재료및생산설비", _ANCHOR_ALT2, _SCAN_WINDOW_ALT),
               ("생산및설비에관한사항", _ANCHOR_ALT, _SCAN_WINDOW_ALT))

# 「2. 주요 제품 및 서비스」 — 사용자 2026-08-21 "가동률 표 위에 이렇게 표
# 그대로". 회사마다 `가. 사업부문별 주요 제품 등의 현황` 처럼 접두가 달라
# 핵심 어구만 잡는다.
_ANCHOR_ITEM = _anchor("주요제품및서비스")
_ANCHOR_ITEM_ALT = _anchor("사업부문별주요제품")
_ITEM_SPECS = (("주요제품및서비스", _ANCHOR_ITEM, _SCAN_WINDOW),
               ("사업부문별주요제품", _ANCHOR_ITEM_ALT, _SCAN_WINDOW_ALT))

_TABLE_RE = re.compile(r"(?is)<TABLE[^>]*>.*?</TABLE>")
# ⚠️ 글자 사이 공백 필수 — 원문이 `가 동 률` 로 온다(실측).
_RATE_RE = re.compile(r"가\s*동\s*률|가동율")
_CAP_RE = re.compile(r"생산\s*능력")
_ACT_RE = re.compile(r"생산\s*실적")
_UNIT_RE = re.compile(r"\(\s*단위\s*[:：][^)]*\)")
# 주요 제품 표의 열 이름들. **매출 열이 있는지**가 판별의 핵심 —
# 같은 절의 `나. 주요 제품 등의 가격변동추이` 는 품목+가격만 있어 걸러진다.
_ITEM_RE = re.compile(r"품\s*목|제품\s*명|주요\s*제품")
_SALES_STRONG = re.compile(r"매출\s*액|매출\s*비중")
_RATIO_RE = re.compile(r"비\s*율|비\s*중")
# ⚠️ 매입 표 감지 — 삼성전자 실측(2026-08-21): 원재료 **매입** 표가
# `품목|구체적용도|매입액|비중|주요 매입처` 라 제품 표 어구를 거의 다
# 갖고 있어 12점으로 채택됐고, 화면 제목은 '주요 제품 및 서비스'인데
# 내용은 매입처였다. 매출 표가 있으면 그쪽이 이겨야 하고, 없으면 매입
# 표를 싣되 **제목이 사실을 말해야** 한다(사용자: "항목이 다르다고
# 얘기해주는거야").
_BUY_RE = re.compile(r"매입\s*액|매입\s*처|매입\s*비중")
_USE_RE = re.compile(r"구체\s*적?\s*용도|용\s*도")
_SEG_RE = re.compile(r"사업\s*부문|매출\s*유형")

# 살균 화이트리스트 — 표 구조에 필요한 것만.
# ⚠️ `br` 를 살린다. DART 는 좁은 셀에서 한 낱말을 **여러 줄**로 쪼개
# 놓는데(`<P>식 품 제</P><P>조및판 매</P><P>등</P>`), 줄 구조를 버리면
# 그 줄바꿈이 브라우저에서 **공백**이 돼 `식 품 제 조및판 매 등` 처럼
# 엉뚱한 자리에서 낱말이 갈린다(사용자 2026-08-21 농심 실측 — 원문은
# `식품제조및판매 등`). 원본 재현이 목적일 때 값뿐 아니라 **줄 구조**도
# 원본이다(#74 의 짝: 값·구조는 원본, 표시 규약만 우리 것).
_KEEP_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "br"}
# 제거하되 **줄바꿈으로 바꿔야** 하는 블록 태그.
_BLOCK_TAGS = {"p", "div", "li", "br"}
_KEEP_ATTRS = ("rowspan", "colspan")
_ATTR_RE = re.compile(r'(?i)\b(ROWSPAN|COLSPAN)\s*=\s*"?(\d{1,3})"?')
# 숫자 열의 **우측정렬만** 원본에서 살린다 — 기존 `.si-table .num` 재사용
# (새 CSS 0줄). 나머지 정렬(CENTER)은 기본 좌측으로 두어도 가독성 손실이 없다.
_ALIGN_RIGHT_RE = re.compile(r'(?i)\bALIGN\s*=\s*"?RIGHT"?')
_TAG_RE = re.compile(r"(?is)<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)([^>]*)>")

# 표 하나가 비정상적으로 크면(원문 파싱이 어긋나 문서 전체를 먹은 경우) 버린다.

# 채택 임계값 — `parse_production` 과 `diagnose` 가 **같은 수**를 봐야 스윕
# 통계가 화면과 일치한다(따로 박으면 감사가 거짓 안심을 준다, 실수 #54).
# 3 = 생산능력·생산실적 중 **하나만** 있는 표. 사용자 2026-08-20 "최대한 다
# 가져오게" + VM 스윕 실측(478560 `품목|일일 처리량|월 생산능력|비고` 이
# 6 미달로 통째 버려졌다). 이웃 설비·소재지 표는 세 어구가 하나도 없어 0 점
# 이므로 3 으로 낮춰도 걸리지 않는다 — 게이트의 목적은 유지된다.
_MIN_SCORE = 3


def _cells(table_markup: str) -> str:
    """표의 **셀 텍스트만** — 선택 판정용(속성값에 걸리지 않게)."""
    return re.sub(r"(?is)<[^>]+>", " ", table_markup)


def sanitize_table(markup: str) -> str:
    """DART 대문자 마크업 → 안전한 소문자 HTML 표. ROWSPAN/COLSPAN 만 보존.

    화이트리스트 밖 태그는 **통째로 제거**(내용은 남김)하고, 모든 속성은
    버린 뒤 병합 속성만 다시 붙인다. 원문은 외부 입력이라 그대로 DOM 에
    넣으면 안 된다."""
    out: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(markup):
        out.append(markup[pos:m.start()])
        pos = m.end()
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3) or ""
        if name in _BLOCK_TAGS:
            out.append("<br>")             # 줄 구조 보존(위 주석 참조)
            continue
        if name not in _KEEP_TAGS:
            continue                       # 태그만 제거, 내부 텍스트는 보존
        if closing:
            out.append(f"</{name}>")
            continue
        keep = ""
        if name == "table":
            keep += ' class="si-table"'        # 상세 페이지 표 스타일 재사용
        for a in _ATTR_RE.finditer(attrs):
            n, v = a.group(1).lower(), a.group(2)
            try:
                iv = int(v)
            except ValueError:
                continue
            if 1 < iv <= 100:              # 1 은 무의미, 비정상 값은 버림
                keep += f' {n}="{iv}"'
        out.append(f"<{name}{keep}>")
    out.append(markup[pos:])
    html = "".join(out)
    # 셀 사이 잉여 공백 정리(원문은 태그 사이 공백이 많다).
    html = re.sub(r">\s+<", "><", html)
    html = re.sub(r"\s{2,}", " ", html).strip()
    # 줄바꿈 정리 — 연속·셀 경계의 `<br>` 는 빈 줄만 만든다.
    html = re.sub(r"(?:<br>\s*)+", "<br>", html)
    html = re.sub(r"(?i)(<t[dh][^>]*>)\s*<br>", r"\1", html)
    html = re.sub(r"(?i)<br>\s*(</t[dh]>)", r"\1", html)
    return _add_wbr(_align_cells(html))


# ⚠️ 이름이 아래 `_CELL_RE`(모양 판정용)와 겹치면 **뒤엣것이 이겨**
# 이 정규식이 통째로 무시된다(실측: group(1) IndexError). #59 의 짝.
_CELL_PAIR_RE = re.compile(r"(?is)<(t[dh])((?:\s[^>]*)?)>(.*?)</\1>")


# 한 열이 '긴 글' 열인지 가르는 폭(전각 환산). 디아이 실측: 사업부문
# `음향 · 영상기기`=9 · 매출유형 `제품`=2 · 매출액 `226,883`=7 인데 품목은
# `반도체검사장비(Monitoring Burn-In Tester 등)`=38 이다.
_LONG_COL_W = 14.0
_ROW_PAIR_RE = re.compile(r"(?is)<tr(?:\s[^>]*)?>.*?</tr>")
_COLSPAN_RE = re.compile(r'(?i)colspan\s*=\s*["\']?(\d+)')
_TAGS_RE = re.compile(r"(?s)<[^>]*>")


def _cell_text(inner: str) -> str:
    """셀의 **가장 긴 줄** — `<br>`(#94 로 살린 원본 줄바꿈)로 끊어 잰다."""
    best = ""
    for part in re.split(r"(?i)<br\s*/?>", inner or ""):
        t = _TAGS_RE.sub("", part).replace("&nbsp;", " ").strip()
        if len(t) > len(best):
            best = t
    return best


_ROWSPAN_RE = re.compile(r'(?i)rowspan\s*=\s*["\']?(\d+)')


def _iter_rows(html: str):
    """행마다 `(행 match, [(셀 match, 열번호, colspan)])`.

    ⚠️ **rowspan 을 반영해야 한다**(2026-08-21 케이씨씨 실측). 앞 열이
    `ROWSPAN=2` 면 다음 행은 셀이 그만큼 적게 오는데, colspan 만 세면 열
    번호가 통째로 **밀린다** — 케이씨씨 가동률 표에서 `59.0`(6열짜리 행의
    5번)은 `lft`, `88.0`(4열짜리 행의 3번)은 `ctr` 이 되어 **같은 열 안에서
    정렬이 갈렸다**. 고치려던 바로 그 증상이 축만 바꿔 세 번째로 재발한 것
    (#78 → #97 → 여기). 판정 단위를 열로 바꿔도 **열 번호가 틀리면** 소용없다.
    """
    pending: dict[int, int] = {}
    for rm in _ROW_PAIR_RE.finditer(html or ""):
        row = rm.group(0)
        ci, cells = 0, []
        for m in _CELL_PAIR_RE.finditer(row):
            attrs = m.group(2) or ""
            while pending.get(ci, 0) > 0:      # 위 행이 차지한 자리
                ci += 1
            cs = _COLSPAN_RE.search(attrs)
            span = max(int(cs.group(1)), 1) if cs else 1
            rs = _ROWSPAN_RE.search(attrs)
            rows = max(int(rs.group(1)), 1) if rs else 1
            if rows > 1:
                for c in range(ci, ci + span):
                    pending[c] = rows
            cells.append((m, ci, span))
            ci += span
        for c in list(pending):                # 이 행이 한 줄 소비
            pending[c] -= 1
            if pending[c] <= 0:
                del pending[c]
        yield rm, cells


def _long_text_cols(html: str) -> set[tuple[int, int]]:
    """긴 글이 들어 있는 **열 슬롯**(시작열, 걸친 열 수) 집합.

    ⚠️ 셀 단위가 아니라 **열 단위**로 정한다 — 셀 내용으로 정하면 한 열
    안에 긴 글과 짧은 글이 섞이는 순간 갈라진다(실수 #78 이 바로 그것).

    ⚠️ 그런데 `colspan>1` 을 **통째로 빼면** 그 열이 전부 colspan 인 표에서
    한 칸도 판정되지 않아 기본값(가운데)으로 굳는다(2026-08-22 IPARK
    현대산업개발 294870.KS 실측: `국내/해외` 하위열 때문에 품목 셀이 전부
    `colspan=2` 였고, 긴 품목명이 가운데로 들쭉날쭉했다 — #97 에서 고친
    바로 그 증상이 축만 바꿔 네 번째로 재발). 그래서 판정 단위는 열 번호가
    아니라 **슬롯**(시작열, span)이다 — 같은 슬롯의 셀은 화면에서 같은
    자리를 차지하므로 정렬도 같아야 하고, `합 계`(colspan=4)처럼 다른
    슬롯은 자기 규약을 따로 가진다.
    """
    width: dict[tuple[int, int], float] = {}
    seen: dict[tuple[int, int], int] = {}
    for _ri, (_rm, cells) in enumerate(_iter_rows(html)):
        for m, ci, span in cells:
            slot = (ci, span)
            # ⚠️ **머리행은 판정하지 않는다**(2026-08-21 케이씨씨). 케이씨씨
            # 가동률 표의 `평균 가동률 (생산실적 ÷ 생산능력)` 처럼 헤더만
            # 긴 **숫자 열**이 있다 — 헤더를 세면 숫자가 좌측으로 밀린다.
            # 열의 성격을 정하는 건 **몸통**이고, 머리행은 그 열을 따른다(#78).
            if _ri == 0 or m.group(1).lower() == "th":
                continue
            t = _cell_text(m.group(3))
            # 숫자 칸은 아무리 길어도 '긴 글'이 아니다(자릿수·부호·%).
            if not re.fullmatch(r"[\d,.\-+()%\s]*", t):
                width[slot] = max(width.get(slot, 0.0), _vlen(t))
                seen[slot] = seen.get(slot, 0) + 1
    # ⚠️ **칸이 둘 이상일 때만** 좌측이다. 좌측정렬의 이유는 같은 열에서
    # 행마다 시작 위치가 들쭉날쭉한 것인데, 칸이 하나뿐인 슬롯
    # (`제품 및 상품 등 소 계(내부거래 조정 전)` colspan=3 같은 요약 라벨)은
    # 어긋날 상대가 없다 — 가운데가 맞다(사용자 승인된 기존 동작).
    return {c for c, w in width.items()
            if w >= _LONG_COL_W and seen.get(c, 0) >= 2}


def _align_cells(html: str) -> str:
    """열 축을 하나로 맞춘다 — **짧은 열은 가운데, 긴 글 열은 좌측**.

    ⚠️ 원본의 `ALIGN=RIGHT` 를 그대로 따라가면 한 열 안에서 정렬이 갈린다
    — 한솔아이원스 실측: 같은 열의 `44,727`(ALIGN=RIGHT)은 우측, `55.7%`
    (속성 없음)는 좌측으로 붙어 눈이 숫자를 따라가지 못했다. 원본이 스스로
    일관되지 않으므로 **우리가 정한다**.

    ⚠️ 1차 시도는 '숫자·머리행만 가운데, 글자는 좌측'이었는데 그것도
    갈렸다(뉴파워프라즈마: 머리행 `구분` 은 가운데인데 그 아래 `생산능력`
    은 좌측). 그래서 전부 가운데로 갔는데, 이번엔 긴 글이 문제였다 —
    디아이 `주요 제품 및 서비스` 의 품목 열은 셀마다 길이가 달라 가운데로
    두면 **행마다 시작 위치가 들쭉날쭉**하다(사용자 2026-08-21 "품목에
    글씨들이 이상해 … 이런식으로 정렬은 아니지 않아?").

    결론: 판정 단위는 **셀도 표도 아닌 열**이다. 한 열이 긴 글을 담으면
    그 열 전체(머리행 포함)를 좌측으로, 나머지는 전부 가운데로.
    """
    long_cols = _long_text_cols(html)
    out, last = [], 0
    for rm, cells in _iter_rows(html):
        out.append(html[last:rm.start()])
        row, rlast = rm.group(0), 0
        parts = []
        for m, ci, span in cells:
            tag, attrs, inner = m.group(1), m.group(2) or "", m.group(3)
            parts.append(row[rlast:m.start()])
            if "class=" not in attrs:
                # 같은 슬롯(시작열·span)끼리 규약을 맞춘다 — 열 하나가
                # 전부 colspan 이어도 판정된다(위 주석).
                cls = "lft" if (ci, span) in long_cols else "ctr"
                attrs += f' class="{cls}"'
            parts.append(f"<{tag}{attrs}>{inner}</{tag}>")
            rlast = m.end()
        parts.append(row[rlast:])
        out.append("".join(parts))
        last = rm.end()
    out.append(html[last:])
    return "".join(out)


# 줄바꿈 힌트를 넣을 최소 폭(전각 환산). 짧은 셀은 접힐 일이 없으므로 건드리지 않는다.
_WBR_MIN_W = 12.0
# 한글↔영문/숫자 경계. DART 원문은 좁은 셀에서 낱말을 붙여 쓰는 일이 잦아
# (라온피플 26.2Q 실측 `AI비전솔루션생성형AI, Platform` — 원문에 공백이 없다)
# 브라우저가 접을 자리를 못 찾는다. 글자는 **한 자도 바꾸지 않고** `<wbr>`
# (zero-width break opportunity)만 끼워 화면에서만 접히게 한다 — 사용자
# 2026-08-21 "화면에서만 줄 바꿈". 값은 원본, 표시 규약은 우리 것(#74).
_WBR_BOUNDARY = re.compile(r"(?<=[가-힣])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[가-힣])")


def _add_wbr(html: str) -> str:
    """긴 셀의 한글↔영문 경계에 `<wbr>` 를 끼운다(텍스트는 불변).

    ⚠️ 태그 **밖의 텍스트에만** 넣는다 — 속성값이나 태그 이름에 끼면 마크업이
    깨진다. `<wbr>` 는 렌더링 텍스트를 바꾸지 않으므로 복사해도 원문 그대로다.
    """
    def _cell(m: re.Match) -> str:
        tag, attrs, inner = m.group(1), m.group(2) or "", m.group(3)
        if _vlen(_cell_text(inner)) < _WBR_MIN_W:
            return m.group(0)
        out, last = [], 0
        for t in _TAGS_RE.finditer(inner):
            out.append(_WBR_BOUNDARY.sub("<wbr>", inner[last:t.start()]))
            out.append(t.group(0))
            last = t.end()
        out.append(_WBR_BOUNDARY.sub("<wbr>", inner[last:]))
        return f"<{tag}{attrs}>{''.join(out)}</{tag}>"

    return _CELL_PAIR_RE.sub(_cell, html or "")


_ROW_RE = re.compile(r"(?is)<TR[^>]*>")
_CELL_RE = re.compile(r"(?is)<T[DH][^>]*>")


def _looks_like_a_data_table(markup: str) -> bool:
    """머리행 + 데이터행이 있는 **격자**인가.

    어구 하나만 걸린 표(3점)를 그대로 받으면 `생산능력 산출근거 | 월 25일`
    같은 **한 줄짜리 각주 표**가 통과한다(2026-08-20 회귀가 잡아낸 실패).
    반대로 진짜 능력표는 열이 여럿이고 데이터행이 따라온다(478560:
    `품목|일일 처리량|월 생산능력|비고` + 5행). 어구 목록을 늘리는 대신
    **구조**로 가른다 — 목록형 판정은 목록 밖을 못 보기 때문(#24)."""
    rows = len(_ROW_RE.findall(markup))
    cells = len(_CELL_RE.findall(markup))
    if not (rows >= 2 and cells >= rows * 3):
        return False
    # ⚠️ **빈 셀로 격자만 채운 표**는 데이터가 아니다(사용자 2026-08-22
    # 네패스: `(2) 당해 사업년도의 가동률` + `(단위 : 장/Ton/천개)` 두 줄이
    # 각각 빈 칸 두 개를 달고 있어 `cells >= rows*3` 을 통과했고, 화면엔
    # **제목만** 떴다). #76(POSCO 캡션 표)에서 넣은 구조 게이트를 빈 칸이
    # 우회한 것 — 세는 대상을 '칸'에서 **'내용이 있는 칸'** 으로 바꾼다.
    filled = 0
    for _rm, _cells in _iter_rows(markup):
        if sum(1 for m, _ci, _sp in _cells if _cell_text(m.group(3))) >= 2:
            filled += 1
    return filled >= 2


def _kinds_in(markup: str) -> list[str]:
    """표에 실제로 담긴 지표 — 제목은 여기서만 만든다.

    호출부가 `kinds` 를 안 실어 보내도 표 자체에서 되뽑을 수 있어야 한다.
    "없으면 셋 다 적는다" 는 폴백은 곧 **없는 지표를 약속하는** 것이라
    고치려던 결함(실수 #55)을 폴백 경로에 그대로 남긴다."""
    body = _cells(markup)
    # 보고서 표의 `구 분` 열 순서대로 — 화면 제목이 원문 읽는 순서와 같아야
    # 사용자가 표와 제목을 대조하기 쉽다(캡처의 '생산능력·생산실적·가동률').
    return [k for k, rx in (("생산능력", _CAP_RE), ("생산실적", _ACT_RE),
                            ("가동률", _RATE_RE)) if rx.search(body)]


def _score(markup: str) -> int:
    """표 적합도 — 가동률이 최우선(사용자가 원하는 핵심 지표).

    ⚠️ 점수 산정은 여기 **한 곳**이다 — parse_production·diagnose·스윕
    프로브가 모두 이걸 부른다. 복제하면 화면과 스윕 통계가 갈라진다(#38).

    ⚠️ 행이 2개 미만이면 **무조건 0** — POSCO홀딩스 실측(2026-08-21):
    `(2) 당해 사업연도의 가동률 | (단위 : 천톤)` 한 줄짜리 **제목 캡션 표**가
    '가동률' 단어만으로 10점을 받아 즉시 채택됐고, 화면엔 그 캡션 텍스트만
    남고 바로 뒤의 진짜 데이터 표는 영영 안 보였다. 어구 점수가 아무리 높아도
    한 줄짜리는 데이터가 아니다."""
    if len(_ROW_RE.findall(markup)) < 2:
        return 0
    body = _cells(markup)
    s = 0
    if _RATE_RE.search(body):
        s += 10
    if _CAP_RE.search(body):
        s += 3
    if _ACT_RE.search(body):
        s += 3
    # 근거가 어구 하나뿐이면 격자 모양까지 맞아야 인정한다. 둘 이상(6점+)은
    # 어구 조합만으로 충분히 특이해 모양을 따지지 않는다.
    if 0 < s < 6 and not _looks_like_a_data_table(markup):
        return 0
    return s


_MIN_SCORE_ITEM = 10        # 품목(5) + 매출/매입 열(7) 또는 비율(5) — 둘 다 필요


def _score_products(markup: str) -> int:
    """주요 제품(판매) 표 적합도. 매출 열이 없으면 가격변동추이 표다.

    매입 어구가 있고 매출 어구가 없는 표는 **판매 표가 아니다** — 여기서
    점수를 못 받아야 매출 표가 뒤에 있을 때 그쪽이 이기고, 둘 다 없을 때만
    `_score_products_buy` 폴백이 매입 표를 집는다."""
    if len(_ROW_RE.findall(markup)) < 2:
        return 0
    body = _cells(markup)
    buy = bool(_BUY_RE.search(body))
    s = 0
    if _ITEM_RE.search(body):
        s += 5
    if _SALES_STRONG.search(body):
        s += 7
    elif not buy and _RATIO_RE.search(body):
        s += 5                      # 비율만 있는 판매 표(매입 어구 없을 때만)
    if _USE_RE.search(body):
        s += 2
    if _SEG_RE.search(body):
        s += 2
    if 0 < s < _MIN_SCORE_ITEM and not _looks_like_a_data_table(markup):
        return 0
    return s


def _score_products_buy(markup: str) -> int:
    """매입(원재료·매입처) 표 적합도 — 판매 표가 없을 때의 폴백 전용."""
    if len(_ROW_RE.findall(markup)) < 2:
        return 0
    body = _cells(markup)
    s = 0
    if _ITEM_RE.search(body):
        s += 5
    if _BUY_RE.search(body):
        s += 7
    if _USE_RE.search(body):
        s += 2
    if _SEG_RE.search(body):
        s += 2
    if 0 < s < _MIN_SCORE_ITEM and not _looks_like_a_data_table(markup):
        return 0
    return s


def _pick(markup: str, anchors: tuple, score_fn, min_score: int,
          stop_at: int, window: int = _SCAN_WINDOW
          ) -> tuple[int, str, str, str] | None:
    """앵커 뒤 창에서 가장 점수 높은 표 → (점수, 표, 앞 텍스트, 뒤 텍스트).

    ⚠️ 앵커의 **모든 출현**을 훑는다. 정기보고서는 목차에도 같은 제목이
    있어 첫 출현만 보면 표가 하나도 없는 창을 훑고 '표없음'을 낸다.
    동점이면 **앞선 표**가 이긴다(`>` 비교) — 절 바로 밑의 표가 정답이고
    창 끝에 걸린 다음 절의 표는 우연히 같은 점수가 날 수 있다."""
    best = None
    for rx in anchors:
        for m in rx.finditer(markup):
            tail = markup[m.end(): m.end() + window]
            for t in _TABLE_RE.finditer(tail):
                raw = t.group(0)
                if len(raw) > _MAX_TABLE_CHARS:
                    continue
                sc = score_fn(raw)
                if best is None or sc > best[0]:
                    best = (sc, raw, tail[:t.start()],
                            tail[t.end(): t.end() + 600])
                    if sc >= stop_at:
                        return best
    return best if best and best[0] >= min_score else None


def _pick_any(markup: str, specs: tuple, score_fn, min_score: int,
              stop_at: int) -> tuple[int, str, str, str, str] | None:
    """앵커 후보를 **순서대로** 시도 → (점수, 표, 앞, 뒤, 앵커이름).

    후보 목록을 호출부마다 늘어놓으면 한 곳만 갱신돼 판정이 갈라진다 —
    `_PROD_SPECS`/`_ITEM_SPECS` 하나에서만 읽는다(#38)."""
    for name, rx, window in specs:
        got = _pick(markup, (rx,), score_fn, min_score, stop_at, window)
        if got:
            return got + (name,)
    return None


def _unit_of(before: str) -> str:
    """표 **앞** 캡션에서 단위 — 데이터 표 앞의 미끼 표에 들어 있다(실측)."""
    um = _UNIT_RE.search(_cells(before)[-400:] or "")
    return um.group(0).strip() if um else ""


def _notes_of(after: str) -> list[str]:
    """표 직후 `*` 각주. **다음 표까지 먹지 않게 끊는다** — 원문은 각주 뒤에
    곧바로 다른 표가 붙어서, 태그를 지운 평문만 보면 각주 한 줄이 그 표의
    셀들을 통째로 삼킨다(2026-08-20 실측)."""
    body = _cells(after)
    notes = []
    for n in re.findall(r"\*\s*([^*(]{4,160})", body)[:4]:
        n = n.strip()
        cut = re.search(r"(?<=니다\.)|(?<=함\.)|\s\(\d+\)\s|\s[가-힣]\.\s", n)
        if cut:
            n = n[:cut.end() if cut.group(0).endswith(".") else cut.start()]
        n = n.strip()
        if n:
            notes.append(n[:120])
    return notes


def parse_products(markup: str | None) -> dict | None:
    """원문 마크업 → 「주요 제품 및 서비스」 표. 없으면 None.

    사용자 2026-08-21: 가동률 표 **위**에 같은 방식으로 원본 표를 싣는다.
    생산 표와 마찬가지로 숫자를 해석하지 않고 그대로 재현하므로 "조용히
    틀린 숫자" 실패모드가 없다 — 위험은 엉뚱한 표를 집는 것 하나뿐."""
    if not markup:
        return None
    # 판매 표를 **먼저** 찾는다. 없을 때만 매입 표로 폴백한다 —
    # 사용자 2026-08-21: "주요 제품 및 서비스가 없다면 이렇게 나오는것도
    # 좋아. 다만 항목이 다르다고 얘기해주는거야."
    kind = "제품"
    got = _pick_any(markup, _ITEM_SPECS, _score_products, _MIN_SCORE_ITEM, 14)
    if not got:
        got = _pick_any(markup, _ITEM_SPECS, _score_products_buy,
                        _MIN_SCORE_ITEM, 14)
        kind = "매입"
    if not got:
        return None
    _sc, raw, before, after, anchor = got
    return {"table_html": sanitize_table(raw), "unit": _unit_of(before),
            "notes": _notes_of(after), "anchor": anchor,
            # 제목이 내용과 어긋나면 사용자는 없는 걸 찾는다(실수 #55).
            "kind": kind}


def parse_production(markup: str | None) -> dict | None:
    """원문 마크업 → {table_html, unit, notes, has_rate, kinds} 또는 None.

    None = 이 보고서에 해당 표가 없다(미기재 회사·형식 미지원). 조용히 빈
    화면을 만들지 않도록 호출부가 사유를 표시한다."""
    if not markup:
        return None
    got = _pick_any(markup, _PROD_SPECS, _score, _MIN_SCORE, 10)
    if not got:
        return None
    sc, raw, before, after, anchor = got
    return {
        "table_html": sanitize_table(raw),
        "unit": _unit_of(before),
        "notes": _notes_of(after),
        "has_rate": sc >= 10,
        # 제목을 이 목록에서 만든다 — 표에 없는 지표를 제목이 약속하면
        # 사용자는 그게 있는 줄 알고 찾는다(실수 #55 라벨↔내용 불일치).
        "kinds": _kinds_in(raw),
        # 어느 서식으로 잡혔는지 — 스윕이 서식별 커버리지를 셀 수 있다.
        "anchor": anchor,
    }


def _scan_any(markup: str, specs: tuple, score_fn
              ) -> tuple[int, int, str, str]:
    """앵커 후보를 **전부** 훑어 (최고점, 본 표 수, 최고점 표, 앵커이름).

    ⚠️ `_pick_any` 로는 대신할 수 없다 — 그건 첫 앵커에서 하나라도 잡히면
    멈추므로 `min_score=0` 으로 부르면 **0점 표를 집고 멈춰** 뒤 앵커의
    진짜 표를 영영 안 본다. 2026-08-21 실측: SK(034730) 가 그렇게
    `무관표만` 으로 찍혔는데 같은 원문에서 `parse_production` 은 가동률
    표를 뽑았다 — 감사와 화면이 갈라진 것(실수 #35).

    창 크기는 스펙이 정한 값을 그대로 쓴다(정확 앵커 40k · 절 제목 200k).
    감사·진단 전용이라 여기서 표를 **채택하지 않는다**."""
    best_sc, seen, best_raw, best_name = -1, 0, "", ""
    for name, rx, window in specs:
        for m in rx.finditer(markup):
            tail = markup[m.end(): m.end() + window]
            for t in _TABLE_RE.finditer(tail):
                raw = t.group(0)
                if len(raw) > _MAX_TABLE_CHARS:
                    continue
                seen += 1
                sc = score_fn(raw)
                if sc > best_sc:
                    best_sc, best_raw, best_name = sc, raw, name
    return max(best_sc, 0), seen, best_raw, best_name


def diagnose(markup: str | None, *, truncated: bool = False) -> str:
    """표를 못 낸 이유를 짧은 코드로 — **개선 여지 판정**이 목적이다.

    `원문미제공`·`섹션없음` 은 원천에 없어 파서를 고칠 여지가 없고,
    `표없음`·`무관표만` 만 새 형식이 필요하다는 신호다. 이 구분이 없으면
    스윕 로그가 노이즈가 된다(dart_backlog.diagnose 와 같은 규약).

    ⚠️ `truncated` 를 **반드시** 넘겨라. 앵커가 안 보이는 이유는 두 가지다:
    절이 정말 없거나, 우리가 문서 앞부분만 받아서 절이 잘려 나갔거나.
    구분하지 않으면 대형사(목차·재무제표가 길다)가 전부 '섹션없음'으로
    찍혀 **원천에 있는데 없다고 보고**한다 — 2026-08-21 VM 스윕에서
    삼성전자·SK하이닉스가 그렇게 나왔고, 프로브가 상한을 안 올린 게 원인
    이었다. 판정 불가를 '없음'으로 말하지 않는다(실수 #41)."""
    if not markup:
        return "원문미제공"
    if not any(rx.search(markup) for _n, rx, _w in _PROD_SPECS):
        return "원문잘림" if truncated else "섹션없음"
    # ⚠️ **화면이 쓰는 그 선택기**를 그대로 부른다(#35). 창 크기·앵커
    # 순회·문턱을 여기 따로 적으면 스윕 통계가 화면과 갈라진다 — 실제로
    # `_SCAN_WINDOW_ALT` 를 넓히고 여기만 40k 로 두었더니 판정이 어긋났고,
    # 문턱을 먼저 물어야 한다. `min_score=0` 으로
    # 부르면 `_pick_any` 가 첫 앵커의 0점 표를 집고 멈춰, 뒤 앵커에서
    # 파서가 실제로 뽑는 표를 못 본다 — SK(034730) 가 `무관표만` 인데
    # 화면엔 가동률이 실리던 원인이다(실수 #35).
    got = _pick_any(markup, _PROD_SPECS, _score, _MIN_SCORE, 10)
    if got is None:
        _best, seen, _raw, _n = _scan_any(markup, _PROD_SPECS, _score)
        if not seen:
            return "표없음"        # 절은 있는데 산문만(생산 관련 서술)
        return "무관표만"          # 표는 있는데 세 어구가 하나도 없다
    best = got[0]
    if best >= 10:
        return "정상"
    if best >= 6:
        return "가동률없음"        # 생산능력·실적만 있고 가동률 미기재
    return "능력만"                # 생산능력 **또는** 실적 하나만 — 채택됨


def _rcept_nos(dart, ticker: str, year: int, reprt_code: str) -> list[str]:
    """해당 분기 보고서의 접수번호들(정정공시 포함)."""
    reps = dart.find_periodic_reports(ticker, year, reprt_code)
    if not reps:
        rep = dart.find_periodic_report(ticker, year, reprt_code)
        reps = [rep] if rep and rep.get("rcept_no") else []
    return [r.get("rcept_no") for r in reps if r and r.get("rcept_no")]


# 표 종류 → 파서. 새 표를 붙일 때 여기만 늘리면 수집 사다리는 그대로다.
_PARSERS = {"products": parse_products, "production": parse_production}


# 파싱 결과 디스크 캐시(#21b — 결과에 파서 지문을 찍고 읽을 때 대조).
_TABLES_TTL = 24 * 3600


def _parse_sig() -> str:
    """이 모듈 소스의 지문. 파서를 고치면 캐시가 **자동으로** 무효가 된다.

    ⚠️ 버전 상수를 손으로 올리는 방식은 이 레포에서 세 번 실패했다
    (#18 아카이브 · #21b 파싱 캐시 · #95 재무 캐시 v4·v5 를 적어 두고도
    v6 을 잊었다). 규율로 기억하지 말고 **구조로** 막는다.
    """
    global _PARSE_SIG
    if _PARSE_SIG is None:
        try:
            import hashlib
            import pathlib
            src = pathlib.Path(__file__).read_bytes()
            _PARSE_SIG = hashlib.sha1(src).hexdigest()[:10]
        except Exception:                                      # noqa: BLE001
            _PARSE_SIG = "nosig"       # 지문을 못 구하면 캐시를 안 쓴다
    return _PARSE_SIG


_PARSE_SIG: str | None = None


def _tables_cache_key(ticker: str, quarters: list, keys: tuple) -> str:
    """티커 · **최신 분기** · 원하는 표 · 파서 지문. 새 보고서가 나오면 키가
    바뀌고(분기 라벨), 파서를 고쳐도 키가 바뀐다."""
    q = (quarters[-1] or {}) if quarters else {}
    label = str(q.get("label") or "") + str(q.get("reprt_code") or "")
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", f"{ticker}_{label}")
    return f"dart_tables_{_parse_sig()}_{safe}_{'-'.join(sorted(keys))}.json"


def _tables_cached(key: str):
    if _parse_sig() == "nosig":
        return None
    try:
        from bot.finviz_client import _cached
        hit = _cached(key, ttl=_TABLES_TTL)
        return hit.get("data") if isinstance(hit, dict) and "data" in hit else None
    except Exception as exc:                                   # noqa: BLE001
        log.debug("tables cache read(%s): %s", key, exc)
        return None


def _tables_cache_write(key: str, out: dict) -> None:
    if _parse_sig() == "nosig":
        return
    try:
        from bot.finviz_client import _cache_write
        _cache_write(key, {"data": out})
    except Exception as exc:                                   # noqa: BLE001
        log.debug("tables cache write(%s): %s", key, exc)


_PREFETCH: set[str] = set()
_PREFETCH_LOCK = threading.Lock()


def prefetch_tables(dart, ticker: str, quarters: list,
                    want: tuple | None = None) -> None:
    """제품·가동률 표를 **미리** 받아 캐시를 데운다(fire-and-forget).

    ⚠️ 왜(2026-08-22 실측): `quarterly-timing 145020.KQ … bp.backlog=0.027s
    build_payload=3.181s render_png=39.15s total=42.331s
    **h.production_html=76.1s**` — 표를 걷는 76초가 `build_payload` **뒤에
    직렬로** 붙는다. 그런데 둘은 같은 정기보고서를 보므로 서로 기다릴 이유가
    없다. 시계열을 받은 직후 여기서 데워 두면 수주잔고(최대 83초 실측)와
    **동시에** 돈다.

    ⚠️ 인자를 호출부와 **똑같이** 준다 — 다르면 캐시 키가 갈려 요청만 늘고
    빨라지지 않는데 그것도 조용하다(#104 미리받기 계획이 루프와 같아야 한다).
    """
    keys = tuple(want or _PARSERS)
    if not dart or not quarters:
        return
    ck = _tables_cache_key(ticker, quarters, keys)
    if _tables_cached(ck) is not None:
        return                             # 이미 따뜻하다
    with _PREFETCH_LOCK:
        if ck in _PREFETCH:
            return                         # 같은 키가 이미 돌고 있다
        _PREFETCH.add(ck)

    def _run() -> None:
        try:
            tables_rolling(dart, ticker, quarters, want=want)
        except Exception as exc:                               # noqa: BLE001
            log.debug("prefetch_tables(%s): %s", ticker, exc)
        finally:
            with _PREFETCH_LOCK:
                _PREFETCH.discard(ck)
    threading.Thread(target=_run, daemon=True,
                     name=f"tbl-prefetch-{ticker}").start()


def tables_rolling(dart, ticker: str, quarters: list, max_back: int = 4,
                   want: tuple | None = None) -> dict:
    """보고서를 **한 번만** 걷고 원하는 표를 전부 뽑는다 → {종류: 표}.

    최신 분기부터 거슬러 각 표가 실린 **첫 보고서**를 채택한다(롤링). 표마다
    따로 걸으면 같은 문서를 표 수만큼 다시 받는다 — 표가 없는 종목(스윕
    실측상 다수)에서 그게 곱셈으로 커진다. 어느 보고서 기준인지는 표마다
    따로 실어 준다(#43 — 기준 미표기 금지). 표별 채택 결과는 따로 걸을 때와
    **동일**하다: 같은 순서로 같은 문서를 보고 처음 걸린 것을 쓴다.

    ⚠️ 상한 escalation 은 **문서가 실제로 잘렸을 때만** 한다. `max_bytes` 는
    HTTP 다운로드를 줄이지 않고 압축 해제량만 줄이므로, 상한을 올린 재시도는
    **같은 zip 을 한 번 더 내려받는 것**이다. 잘리지 않았으면 전문을 이미 다
    본 것이라 올려도 결과가 같다 — 그 경우 재시도는 순손실.
    잘림 여부는 **원천이 알려준다**(`doc_was_truncated`). 반환 문자열 길이로
    추정하면 안 된다 — 상한은 바이트, 반환은 정규화된 문자열이라 항상 더
    짧아서 판정이 늘 '안 잘림'으로 기운다(2026-08-21 삼성전자 실측)."""
    out: dict = {}
    keys = tuple(want or _PARSERS)
    if not dart or not quarters:
        return out
    # ⚠️ 파싱 결과를 캐시한다. 2026-08-22 실측: `/api/quarterly` 가 213~288초
    # 였고, 이 함수는 **2.8M자 원문을 매 요청마다 다시 정규식으로 훑는다**
    # (제품·생산·수주 파서가 각각). 순수 CPU 라 GIL 을 붙잡아 같은 시각
    # 차트의 pandas 계산까지 굶겼다(`ind.basic` 0.02초가 9.8초로).
    # 키에 **파서 소스 지문**을 넣어 파서를 고치면 자동으로 무효가 된다 —
    # 버전을 손으로 올리는 규율은 세 번 실패했다(#18·#21b·#95).
    ck = _tables_cache_key(ticker, quarters, keys)
    hit = _tables_cached(ck)
    if hit is not None:
        return hit
    try:
        from bot.dart_feed import (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL,
                                   _fetch_doc_text, doc_was_truncated)
        for q in reversed(quarters[-max_back:]):
            missing = [k for k in keys if k not in out]
            if not missing:
                break
            for rn in _rcept_nos(dart, ticker, q.get("year"),
                                 q.get("reprt_code")):
                for cap in (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL):
                    markup = _fetch_doc_text(rn, dart.api_key, max_bytes=cap,
                                             raw_markup=True)
                    for k in list(missing):
                        got = _PARSERS[k](markup)
                        if got:
                            got["basis_label"] = q.get("label") or ""
                            got["rcept_no"] = rn
                            out[k] = got
                            missing.remove(k)
                    if not missing:
                        break
                    if not doc_was_truncated(rn, cap, True):
                        break          # 안 잘렸다 = 상한을 올려도 같은 내용
                if not missing:
                    break
    except Exception as exc:                                   # noqa: BLE001
        log.warning("tables_rolling(%s): %s", ticker, exc)
        return out                     # 실패는 캐시하지 않는다 — 다음에 재시도
    _tables_cache_write(ck, out)
    return out


def production_rolling(dart, ticker: str, quarters: list, max_back: int = 4
                       ) -> dict | None:
    """생산능력·생산실적·가동률 표 롤링. 단일 워크의 얇은 래퍼."""
    return tables_rolling(dart, ticker, quarters, max_back,
                          want=("production",)).get("production")


def products_rolling(dart, ticker: str, quarters: list, max_back: int = 4
                     ) -> dict | None:
    """「주요 제품 및 서비스」 롤링. 단일 워크의 얇은 래퍼 — 수집 사다리를
    복제하면 두 표의 기준 보고서가 갈라진다(#38)."""
    return tables_rolling(dart, ticker, quarters, max_back,
                          want=("products",)).get("products")


def _palette() -> tuple[str, str, str, str, str]:
    """(카드 배경, 표 헤더 배경, 본문색, 흐린색, 선색).

    인포그래픽 PNG 와 **같은 상수**를 쓴다 — 사용자 2026-08-21 "위에 차트와
    똑같이 검정색 안에 들어가게". 색을 여기 또 적으면 팔레트를 바꿀 때 표만
    옛 색으로 남아 한 카드 안에서 두 톤이 갈린다(#38)."""
    from bot.quarterly_infographic import _BG, _LINE, _MUTED, _PANEL, _TEXT
    return _BG, _PANEL, _TEXT, _MUTED, _LINE


def dark_panel(title: str, meta: list[str], table_html: str,
               notes: list[str]) -> str:
    """표 한 덩어리를 **테두리 없는 섹션**으로 낸다.

    ⚠️ 자체 배경·테두리·라운드를 두지 않는다 — 사용자 2026-08-21 "중간에
    흰색부분없이 하나의 검정테두리로 전체를 엮어줘". 조각마다 카드를 두면
    사이에 페이지 배경(흰색)이 비친다. 감싸는 컨테이너(`si-qwrap`)가 배경과
    테두리를 담당하고 여기는 안쪽 여백만 준다.

    표 CSS 는 **한 줄도 새로 만들지 않는다.** `.si-table` 이 이미
    `--bg/--fg/--fg-soft/--border` 를 읽으므로 그 변수만 다시 정의하면 안쪽
    표가 통째로 어두운 톤이 된다(미니멀 코드 사다리 ②③).

    `word-break: keep-all` — 한글 기본값(break-all 유사)은 **낱글자로**
    끊어서 `반도체검사용 소/켓 제조 외`, `매출유/형` 처럼 단어가 갈라진다.
    keep-all 이면 공백(어절) 경계에서만 끊긴다. 그래도 한 어절이 칸보다
    길면 넘치므로 `overflow-wrap: break-word` 를 폴백으로 같이 둔다."""
    _bg, panel, fg, soft, line = _palette()
    e = _html.escape
    note_html = "".join(
        f'<div style="font-size:11px;color:{soft};margin-top:3px">'
        f'* {e(n)}</div>' for n in (notes or []))
    return (
        f'<div class="si-prod" style="--bg:{panel};--fg:{fg};'
        f'--fg-soft:{soft};--border:{line};color:{fg};'
        f'word-break:keep-all;overflow-wrap:break-word;'
        f'padding:14px 12px 4px">'
        f'<div class="si-section-title" style="font-size:13px;color:{fg}">'
        f'{e(title)}</div>'
        f'<div style="font-size:11px;color:{soft};margin:2px 0 8px">'
        f'{e(" · ".join(meta))}</div>'
        f'<div style="overflow-x:auto">{table_html}</div>{note_html}</div>')


def qwrap_style() -> str:
    """전체를 엮는 **하나의** 다크 컨테이너 인라인 스타일.

    이미지 조각과 표 섹션을 한 테두리 안에 넣어 사이에 흰 배경이 안 비치게
    한다. `overflow:hidden` 이 안쪽 이미지 모서리를 대신 깎아 주므로 이미지에
    개별 radius 를 줄 필요가 없다(주면 모서리에 배경색이 비친다)."""
    bg, _panel, fg, _soft, line = _palette()
    return (f"background:{bg};color:{fg};border:1px solid {line};"
            f"border-radius:14px;overflow:hidden")


def render_products_html(prod: dict | None) -> str:
    """「주요 제품 및 서비스」 표 블록. 없으면 빈 문자열(섹션 생략).

    ⚠️ 제목은 **실제로 실린 표**를 말한다. 삼성전자는 판매 표 대신 원재료
    매입 표가 실리는데 제목이 '주요 제품 및 서비스'면 그 회사가 그걸 파는
    걸로 읽힌다(사용자 2026-08-21 지적). 매입 표면 그렇다고 밝힌다(#55)."""
    if not prod or not prod.get("table_html"):
        return ""
    meta = [f"{prod['basis_label']} 보고서 기준"] if prod.get("basis_label") else []
    if prod.get("unit"):
        meta.append(prod["unit"])
    meta.append("출처: DART 정기보고서 원문")
    title = ("📦 주요 원재료 및 매입처" if prod.get("kind") == "매입"
             else "📦 주요 제품 및 서비스")
    if prod.get("kind") == "매입":
        meta.insert(0, "판매 표 미기재 — 매입 표로 대체")
    return dark_panel(title, meta, prod["table_html"], prod.get("notes"))


def render_html(prod: dict | None) -> str:
    """생산능력·가동률 표 블록. prod 가 None 이면 빈 문자열."""
    if not prod or not prod.get("table_html"):
        return ""
    # ⚠️ 고정 문구로 두면 가동률이 없는 표에도 "가동률"이 적혀 사용자가
    # 없는 걸 찾게 된다. 실제 담긴 지표만 제목에 올린다(실수 #55).
    kinds = prod.get("kinds") or _kinds_in(prod["table_html"])
    meta = [f"{prod['basis_label']} 보고서 기준"] if prod.get("basis_label") else []
    if prod.get("unit"):
        meta.append(prod["unit"])
    meta.append("출처: DART 정기보고서 원문")
    return dark_panel("🏭 " + (" · ".join(kinds) if kinds else "생산 현황"),
                      meta, prod["table_html"], prod.get("notes"))
