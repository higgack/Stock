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

_TABLE_RE = re.compile(r"(?is)<TABLE[^>]*>.*?</TABLE>")
# ⚠️ 글자 사이 공백 필수 — 원문이 `가 동 률` 로 온다(실측).
_RATE_RE = re.compile(r"가\s*동\s*률|가동율")
_CAP_RE = re.compile(r"생산\s*능력")
_ACT_RE = re.compile(r"생산\s*실적")
_UNIT_RE = re.compile(r"\(\s*단위\s*[:：][^)]*\)")

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


def _score(markup: str) -> int:
    """표 적합도 — 가동률이 최우선(사용자가 원하는 핵심 지표)."""
    body = _cells(markup)
    s = 0
    if _RATE_RE.search(body):
        s += 10
    if _CAP_RE.search(body):
        s += 3
    if _ACT_RE.search(body):
        s += 3
    return s


def parse_production(markup: str | None) -> dict | None:
    """원문 마크업 → {table_html, unit, notes, has_rate} 또는 None.

    None = 이 보고서에 해당 표가 없다(미기재 회사·형식 미지원). 조용히 빈
    화면을 만들지 않도록 호출부가 사유를 표시한다."""
    if not markup:
        return None
    m = _ANCHOR.search(markup) or _ANCHOR_ALT.search(markup)
    if not m:
        return None
    tail = markup[m.end(): m.end() + _SCAN_WINDOW]
    best, best_score = None, 0
    for t in _TABLE_RE.finditer(tail):
        raw = t.group(0)
        if len(raw) > _MAX_TABLE_CHARS:
            continue
        sc = _score(raw)
        # 가동률 표를 만나면 즉시 채택(앞에 있는 미끼·소재지 표를 건너뛴다).
        if sc > best_score:
            best, best_score = t, sc
            if sc >= 10:
                break
    if best is None or best_score < 6:
        # 6 = 생산능력+생산실적 둘 다. 하나만 걸린 표는 이웃 표일 수 있어 버린다.
        return None
    raw = best.group(0)
    # 단위 캡션 — 데이터 표 **앞**의 미끼 표에 들어 있다(실측).
    unit = ""
    um = _UNIT_RE.search(_cells(tail[:best.start()])[-400:] or "")
    if um:
        unit = um.group(0).strip()
    # 각주 — 표 직후 텍스트에서 `*` 로 시작하는 줄들.
    # ⚠️ **다음 표까지 먹지 않게 끊는다.** 원문은 각주 뒤에 곧바로 다른 표
    # (설비 소재지 등)가 붙어서, 태그를 지운 평문만 보면 각주 한 줄이 그
    # 표의 셀들을 통째로 삼킨다(2026-08-20 실측 — `일 8시간 기준입니다.
    # 사업부문 품목 소재지 자가…`). 문장 종결·다음 절 번호에서 자른다.
    after = _cells(tail[best.end(): best.end() + 600])
    notes = []
    for n in re.findall(r"\*\s*([^*(]{4,160})", after)[:4]:
        n = n.strip()
        # 첫 문장까지만(각주는 한 문장이 관례) — 종결부호가 없으면 절 번호에서.
        cut = re.search(r"(?<=니다\.)|(?<=함\.)|\s\(\d+\)\s|\s[가-힣]\.\s", n)
        if cut:
            n = n[:cut.end() if cut.group(0).endswith(".") else cut.start()]
        n = n.strip()
        if n:
            notes.append(n[:120])
    return {
        "table_html": sanitize_table(raw),
        "unit": unit,
        "notes": [n for n in notes if n],
        "has_rate": best_score >= 10,
    }


def production_for(dart, ticker: str, year: int, reprt_code: str) -> dict | None:
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
                got = parse_production(markup)
                if got:
                    break
            if got:
                got["rcept_no"] = rn
                return got
    except Exception as exc:                                   # noqa: BLE001
        log.warning("production_for(%s %s %s): %s", ticker, year, reprt_code, exc)
    return None


def production_rolling(dart, ticker: str, quarters: list, max_back: int = 4
                       ) -> dict | None:
    """최신 분기부터 거슬러 **표가 있는 첫 보고서**를 채택(롤링).

    새 보고서가 나오면 그 기준으로 자동 갱신된다. 특정 분기 보고서가 표를
    빠뜨려도(회사 재량) 직전 보고서로 폴백하되, **어느 보고서 기준인지**를
    함께 돌려줘 화면이 밝힐 수 있게 한다(#43 — 기준 미표기 금지)."""
    if not dart or not quarters:
        return None
    for q in reversed(quarters[-max_back:]):
        got = production_for(dart, ticker, q.get("year"), q.get("reprt_code"))
        if got:
            got["basis_label"] = q.get("label") or ""
            return got
    return None


def render_html(prod: dict | None) -> str:
    """표 블록 HTML. prod 가 None 이면 빈 문자열(호출부가 섹션을 생략)."""
    if not prod or not prod.get("table_html"):
        return ""
    e = _html.escape
    head = "🏭 생산능력 · 생산실적 · 가동률"
    meta = []
    if prod.get("basis_label"):
        meta.append(f"{e(prod['basis_label'])} 보고서 기준")
    if prod.get("unit"):
        meta.append(e(prod["unit"]))
    meta.append("출처: DART 정기보고서 원문")
    notes = "".join(
        f'<div style="font-size:11px;color:var(--fg-soft);margin-top:2px">'
        f'* {e(n)}</div>' for n in (prod.get("notes") or []))
    return (
        '<div class="si-prod" style="margin-top:14px">'
        f'<div class="si-section-title" style="font-size:13px">{head}</div>'
        f'<div style="font-size:11px;color:var(--fg-soft);margin:2px 0 6px">'
        f'{" · ".join(meta)}</div>'
        '<div style="overflow-x:auto">' + prod["table_html"] + '</div>'
        + notes + '</div>')
