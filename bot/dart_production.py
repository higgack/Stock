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

log = logging.getLogger("bot.dart_production")

# 섹션 앵커. 회사마다 `(1) 제품 생산능력 및 생산실적` / `가. 생산능력 및 생산실적`
# 처럼 번호·접두가 달라 **핵심 어구만** 잡는다.
_ANCHOR = re.compile(r"생산\s*능력\s*(?:및|and)?\s*생산\s*실적", re.I)
# 앵커가 없는 회사 대비 폴백 — 「생산 및 설비」 절 자체.
_ANCHOR_ALT = re.compile(r"생산\s*및\s*설비에?\s*관한\s*사항")

# 「2. 주요 제품 및 서비스」 — 사용자 2026-08-21 "가동률 표 위에 이렇게 표
# 그대로". 회사마다 `가. 사업부문별 주요 제품 등의 현황` 처럼 접두가 달라
# 핵심 어구만 잡는다.
_ANCHOR_ITEM = re.compile(r"주요\s*제품\s*(?:및|·|,|과)?\s*서비스")
_ANCHOR_ITEM_ALT = re.compile(r"사업\s*부문별\s*주요\s*제품")

_TABLE_RE = re.compile(r"(?is)<TABLE[^>]*>.*?</TABLE>")
# ⚠️ 글자 사이 공백 필수 — 원문이 `가 동 률` 로 온다(실측).
_RATE_RE = re.compile(r"가\s*동\s*률|가동율")
_CAP_RE = re.compile(r"생산\s*능력")
_ACT_RE = re.compile(r"생산\s*실적")
_UNIT_RE = re.compile(r"\(\s*단위\s*[:：][^)]*\)")
# 주요 제품 표의 열 이름들. **매출 열이 있는지**가 판별의 핵심 —
# 같은 절의 `나. 주요 제품 등의 가격변동추이` 는 품목+가격만 있어 걸러진다.
_ITEM_RE = re.compile(r"품\s*목|제품\s*명|주요\s*제품")
_SALES_RE = re.compile(r"매출\s*액|매출\s*비중|비\s*율|비\s*중")
_USE_RE = re.compile(r"구체\s*적?\s*용도|용\s*도")
_SEG_RE = re.compile(r"사업\s*부문|매출\s*유형")

# 살균 화이트리스트 — 표 구조에 필요한 것만.
_KEEP_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th"}
_KEEP_ATTRS = ("rowspan", "colspan")
_ATTR_RE = re.compile(r'(?i)\b(ROWSPAN|COLSPAN)\s*=\s*"?(\d{1,3})"?')
# 숫자 열의 **우측정렬만** 원본에서 살린다 — 기존 `.si-table .num` 재사용
# (새 CSS 0줄). 나머지 정렬(CENTER)은 기본 좌측으로 두어도 가독성 손실이 없다.
_ALIGN_RIGHT_RE = re.compile(r'(?i)\bALIGN\s*=\s*"?RIGHT"?')
_TAG_RE = re.compile(r"(?is)<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)([^>]*)>")

# 표 하나가 비정상적으로 크면(원문 파싱이 어긋나 문서 전체를 먹은 경우) 버린다.
_MAX_TABLE_CHARS = 120_000
_SCAN_WINDOW = 40_000       # 앵커 이후 훑을 범위

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
        if name not in _KEEP_TAGS:
            continue                       # 태그만 제거, 내부 텍스트는 보존
        if closing:
            out.append(f"</{name}>")
            continue
        keep = ""
        if name == "table":
            keep += ' class="si-table"'        # 상세 페이지 표 스타일 재사용
        elif name in ("td", "th") and _ALIGN_RIGHT_RE.search(attrs):
            keep += ' class="num"'
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
    return re.sub(r"\s{2,}", " ", html).strip()


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
    return rows >= 2 and cells >= rows * 3


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
    프로브가 모두 이걸 부른다. 복제하면 화면과 스윕 통계가 갈라진다(#38)."""
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


_MIN_SCORE_ITEM = 10        # 품목 열 + 매출 열 **둘 다** (5+5)


def _score_products(markup: str) -> int:
    """주요 제품 표 적합도. 매출 열이 없으면 가격변동추이 표다."""
    body = _cells(markup)
    s = 0
    if _ITEM_RE.search(body):
        s += 5
    if _SALES_RE.search(body):
        s += 5
    if _USE_RE.search(body):
        s += 2
    if _SEG_RE.search(body):
        s += 2
    if 0 < s < _MIN_SCORE_ITEM and not _looks_like_a_data_table(markup):
        return 0
    return s


def _pick(markup: str, anchors: tuple, score_fn, min_score: int,
          stop_at: int) -> tuple[int, str, str, str] | None:
    """앵커 뒤 창에서 가장 점수 높은 표 → (점수, 표, 앞 텍스트, 뒤 텍스트).

    ⚠️ 앵커의 **모든 출현**을 훑는다. 정기보고서는 목차에도 같은 제목이
    있어 첫 출현만 보면 표가 하나도 없는 창을 훑고 '표없음'을 낸다.
    동점이면 **앞선 표**가 이긴다(`>` 비교) — 절 바로 밑의 표가 정답이고
    창 끝에 걸린 다음 절의 표는 우연히 같은 점수가 날 수 있다."""
    best = None
    for rx in anchors:
        for m in rx.finditer(markup):
            tail = markup[m.end(): m.end() + _SCAN_WINDOW]
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
    got = (_pick(markup, (_ANCHOR_ITEM,), _score_products, _MIN_SCORE_ITEM, 14)
           or _pick(markup, (_ANCHOR_ITEM_ALT,), _score_products,
                    _MIN_SCORE_ITEM, 14))
    if not got:
        return None
    _sc, raw, before, after = got
    return {"table_html": sanitize_table(raw), "unit": _unit_of(before),
            "notes": _notes_of(after)}


def parse_production(markup: str | None) -> dict | None:
    """원문 마크업 → {table_html, unit, notes, has_rate, kinds} 또는 None.

    None = 이 보고서에 해당 표가 없다(미기재 회사·형식 미지원). 조용히 빈
    화면을 만들지 않도록 호출부가 사유를 표시한다."""
    if not markup:
        return None
    got = (_pick(markup, (_ANCHOR,), _score, _MIN_SCORE, 10)
           or _pick(markup, (_ANCHOR_ALT,), _score, _MIN_SCORE, 10))
    if not got:
        return None
    sc, raw, before, after = got
    return {
        "table_html": sanitize_table(raw),
        "unit": _unit_of(before),
        "notes": _notes_of(after),
        "has_rate": sc >= 10,
        # 제목을 이 목록에서 만든다 — 표에 없는 지표를 제목이 약속하면
        # 사용자는 그게 있는 줄 알고 찾는다(실수 #55 라벨↔내용 불일치).
        "kinds": _kinds_in(raw),
    }


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
    m = _ANCHOR.search(markup) or _ANCHOR_ALT.search(markup)
    if not m:
        return "원문잘림" if truncated else "섹션없음"
    tail = markup[m.end(): m.end() + _SCAN_WINDOW]
    tabs = list(_TABLE_RE.finditer(tail))
    if not tabs:
        return "표없음"            # 절은 있는데 산문만(생산 관련 서술)
    best = max((_score(t.group(0)) for t in tabs), default=0)
    if best >= 10:
        return "정상"
    if best >= 6:
        return "가동률없음"        # 생산능력·실적만 있고 가동률 미기재
    if best >= _MIN_SCORE:
        return "능력만"            # 생산능력 **또는** 실적 하나만 — 채택됨
    return "무관표만"              # 표는 있는데 세 어구가 하나도 없다


def production_for(dart, ticker: str, year: int, reprt_code: str,
                   parse=None) -> dict | None:
    """해당 보고서의 생산능력·가동률 표. 없으면 None.

    ⚠️ **기본 상한(3MB)을 먼저 쓴다.** 「생산 및 설비에 관한 사항」은 실측
    4건에서 전부 문서 앞부분(7만~9.7만자)에 있었다 — dart_backlog 가 FULL
    (40MB)을 쓰는 건 「매출 및 수주상황」이 목차상 훨씬 뒤이기 때문이고, 그
    상한을 여기서 그대로 따라 하면 같은 문서를 **40MB 로 한 번 더** 받고
    메모리 캐시까지 부풀린다(raw 는 평문과 캐시 키가 달라 재사용이 안 된다).
    앵커를 못 찾은 경우에만 FULL 로 한 번 더 시도한다 — 목차가 긴 대형사
    대비 폴백이다."""
    if not dart:
        return None
    try:
        from bot.dart_feed import (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL,
                                   _fetch_doc_text)
        reps = dart.find_periodic_reports(ticker, year, reprt_code)
        if not reps:
            rep = dart.find_periodic_report(ticker, year, reprt_code)
            reps = [rep] if rep and rep.get("rcept_no") else []
        for rep in reps:
            rn = rep.get("rcept_no")
            if not rn:
                continue
            got = None
            for cap in (_DOC_TEXT_MAX, _DOC_TEXT_MAX_FULL):
                markup = _fetch_doc_text(rn, dart.api_key, max_bytes=cap,
                                         raw_markup=True)
                got = (parse or parse_production)(markup)
                if got:
                    break
            if got:
                got["rcept_no"] = rn
                return got
    except Exception as exc:                                   # noqa: BLE001
        log.warning("production_for(%s %s %s): %s", ticker, year, reprt_code, exc)
    return None


def production_rolling(dart, ticker: str, quarters: list, max_back: int = 4,
                       parse=None) -> dict | None:
    """최신 분기부터 거슬러 **표가 있는 첫 보고서**를 채택(롤링).

    새 보고서가 나오면 그 기준으로 자동 갱신된다. 특정 분기 보고서가 표를
    빠뜨려도(회사 재량) 직전 보고서로 폴백하되, **어느 보고서 기준인지**를
    함께 돌려줘 화면이 밝힐 수 있게 한다(#43 — 기준 미표기 금지)."""
    if not dart or not quarters:
        return None
    for q in reversed(quarters[-max_back:]):
        got = production_for(dart, ticker, q.get("year"), q.get("reprt_code"),
                             parse=parse)
        if got:
            got["basis_label"] = q.get("label") or ""
            return got
    return None


def products_rolling(dart, ticker: str, quarters: list, max_back: int = 4
                     ) -> dict | None:
    """「주요 제품 및 서비스」 롤링. 수집 사다리(보고서 탐색·상한 escalation·
    캐시)는 생산 표와 **똑같으므로 그대로 재사용**한다 — 복제하면 한쪽만
    고쳐져 두 표의 기준 보고서가 갈라진다(#38). 같은 접수번호의 원문은
    `_fetch_doc_text` 메모리 캐시에 걸려 DART 호출이 늘지 않는다."""
    return production_rolling(dart, ticker, quarters, max_back,
                              parse=parse_products)


def _palette() -> tuple[str, str, str, str, str]:
    """(카드 배경, 표 헤더 배경, 본문색, 흐린색, 선색).

    인포그래픽 PNG 와 **같은 상수**를 쓴다 — 사용자 2026-08-21 "위에 차트와
    똑같이 검정색 안에 들어가게". 색을 여기 또 적으면 팔레트를 바꿀 때 표만
    옛 색으로 남아 한 카드 안에서 두 톤이 갈린다(#38)."""
    from bot.quarterly_infographic import _BG, _LINE, _MUTED, _PANEL, _TEXT
    return _BG, _PANEL, _TEXT, _MUTED, _LINE


def dark_panel(title: str, meta: list[str], table_html: str,
               notes: list[str]) -> str:
    """차트 카드와 같은 톤의 어두운 카드로 표를 감싼다.

    ⚠️ 표 자체 CSS 는 **한 줄도 새로 만들지 않는다.** `.si-table` 은 이미
    `--bg/--fg/--fg-soft/--border` 를 읽으므로, 감싸는 div 에서 그 변수만
    다시 정의하면 안쪽 표가 통째로 어두운 톤으로 바뀐다(미니멀 코드 사다리
    ②③ — 있는 걸 재사용)."""
    bg, panel, fg, soft, line = _palette()
    e = _html.escape
    note_html = "".join(
        f'<div style="font-size:11px;color:{soft};margin-top:2px">'
        f'* {e(n)}</div>' for n in (notes or []))
    return (
        f'<div class="si-prod" style="--bg:{panel};--fg:{fg};'
        f'--fg-soft:{soft};--border:{line};background:{bg};color:{fg};'
        f'border:1px solid {line};border-radius:14px;'
        f'padding:14px 16px;margin-top:14px">'
        f'<div class="si-section-title" style="font-size:13px;color:{fg}">'
        f'{e(title)}</div>'
        f'<div style="font-size:11px;color:{soft};margin:2px 0 8px">'
        f'{e(" · ".join(meta))}</div>'
        f'<div style="overflow-x:auto">{table_html}</div>{note_html}</div>')


def render_products_html(prod: dict | None) -> str:
    """「주요 제품 및 서비스」 표 블록. 없으면 빈 문자열(섹션 생략)."""
    if not prod or not prod.get("table_html"):
        return ""
    meta = [f"{prod['basis_label']} 보고서 기준"] if prod.get("basis_label") else []
    if prod.get("unit"):
        meta.append(prod["unit"])
    meta.append("출처: DART 정기보고서 원문")
    return dark_panel("📦 주요 제품 및 서비스", meta, prod["table_html"],
                      prod.get("notes"))


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
