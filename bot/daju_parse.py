"""DAJU(다주) 실적 발표 예정 알림 파서 — 순수 함수.

출처: 텔레그램 봇 @daju_017_bot (사용자 구독). "3영업일 후 실적 발표 예정"
메시지를 구조화해 블로그 대시보드 상단 섹션에 표로 세운다.

⚠️ **외부 서비스가 만든 포맷**이라 우리가 통제할 수 없다. 이모지·줄바꿈·항목명이
언제든 바뀔 수 있으므로 파서는 전부 best-effort 로 짜고, 실패한 필드는 None 으로
두며, 원문(`raw`)을 항상 함께 보관한다 → 파싱이 깨져도 대시보드는 원문을 그대로
보여줘 정보가 사라지지 않는다(레딧/블로그 아카이브와 같은 정책).

투자권유 아님 — 출처가 "데이터 기반 추정이며 투자 권유가 아닙니다" 를 명시하고
있고, 우리 화면도 그 고지를 그대로 노출한다(2차 가공·재해석 금지).
"""
from __future__ import annotations

import re
from typing import Optional

# 이 메시지가 DAJU 실적 예정 알림인지 — 리스너의 relevance 필터가 쓴다.
# (나쁜양파 리스너가 캡션 파싱으로 무관 콘텐츠를 거르는 것과 같은 패턴.)
_HEADLINE_RE = re.compile(r"영업일\s*후\s*실적\s*발표\s*예정")
_STOCK_RE = re.compile(r"^\s*📌\s*(\d+)\.\s*(.+?)\s*\((\d{6})\)\s*$")
_SCORE_RE = re.compile(r"기대점수\s*[:：]\s*(-?\d+)\s*점\s*/\s*(\d+)")
_WHEN_RE = re.compile(r"발표시각\s*[:：]\s*(.+?)\s*$")
_PRICE_RE = re.compile(
    r"현재가\s*[:：]\s*([\d,]+)\s*원\s*(?:\(\s*1개월\s*([🔺🔻▲▼+\-]?)\s*([\d.]+)\s*%\s*\))?")
_TOMORROW_RE = re.compile(r"^\s*📎\s*(.+?)\s*$")
# '· S-Oil (010950) 10:00 🌟'  / '· LX인터내셔널 (001120)'
_TOM_ITEM_RE = re.compile(
    r"^\s*·\s*(.+?)\s*\((\d{6})\)\s*(\d{1,2}:\d{2})?\s*(🌟)?\s*$")
_TOM_NOTE_RE = re.compile(r"^\s*└\s*(.+?)\s*$")
_MORE_RE = re.compile(r"외\s*(\d+)\s*곳")
_COUNT_RE = re.compile(r"발표\s*예정\s*(\d+)\s*곳\s*중\s*기대\s*상위\s*(\d+)\s*곳")
_BULLET_RE = re.compile(r"^\s*·\s*(.+?)\s*$")
# 근거 섹션 헤더: 이모지 + 제목 (불릿이 아닌 줄). 이모지 종류는 늘 수 있어
# '· ' 로 시작하지 않고 한글 제목이 오는 줄을 섹션으로 본다.
_SECTION_RE = re.compile(r"^\s*([^\w\s·└📌📎※-])\s*(.+?)\s*$")
_DIVIDER_RE = re.compile(r"^[━─\-]{3,}$")


def is_daju_earnings(text: str) -> bool:
    """이 텍스트가 DAJU 실적 예정 알림인가 — 리스너 relevance 필터용(순수)."""
    if not text:
        return False
    return bool(_HEADLINE_RE.search(text)) and bool(_STOCK_RE.search(text)
                                                    or "📌" in text)


def _pct(sign: str, num: str) -> Optional[float]:
    """'🔺'/'🔻'/'+'/'-' + '29.4' → +29.4 / -29.4. 부호 없으면 양수 취급."""
    try:
        v = float(num)
    except (TypeError, ValueError):
        return None
    return -v if sign in ("🔻", "▼", "-") else v


def parse_daju(text: str) -> Optional[dict]:
    """DAJU 알림 → 구조화 dict. 형식이 아니면 None.

    반환: {headline, target, total, picked, stocks[], tomorrow{}, notes[], raw}
      stocks[i] = {rank, name, code, score, score_max, when, price, move_1m,
                   sections[{icon,title,bullets[]}]}
    모든 필드는 실패 시 None/빈값 — 원문(raw)이 최종 폴백이다."""
    if not is_daju_earnings(text):
        return None
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: dict = {"headline": "", "target": None, "total": None, "picked": None,
                 "stocks": [], "tomorrow": None, "notes": [], "raw": text}

    for ln in lines:
        if _HEADLINE_RE.search(ln):
            out["headline"] = ln.lstrip("📣 ").strip()
            m = re.search(r"\(([^)]+)\)\s*$", out["headline"])
            if m:
                out["target"] = m.group(1).strip()
            break
    m = _COUNT_RE.search(text)
    if m:
        out["total"], out["picked"] = int(m.group(1)), int(m.group(2))
    out["notes"] = [ln.lstrip("※ ").strip() for ln in lines
                    if ln.strip().startswith("※")]

    cur: Optional[dict] = None
    sec: Optional[dict] = None
    in_tomorrow = False
    tom: dict = {"label": "", "items": [], "more": None}

    for ln in lines:
        s = ln.strip()
        if not s or _DIVIDER_RE.match(s):
            continue
        mt = _TOMORROW_RE.match(ln)
        if mt and "발표 예정" in mt.group(1):
            in_tomorrow, cur, sec = True, None, None
            tom["label"] = mt.group(1).strip()
            continue
        if in_tomorrow:
            mi = _TOM_ITEM_RE.match(ln)
            if mi:
                tom["items"].append({"name": mi.group(1).strip(),
                                     "code": mi.group(2),
                                     "time": mi.group(3),
                                     "starred": bool(mi.group(4)),
                                     "note": None})
                continue
            mn = _TOM_NOTE_RE.match(ln)
            if mn and tom["items"]:
                tom["items"][-1]["note"] = mn.group(1).strip()
                continue
            mm = _MORE_RE.search(s)
            if mm and not s.startswith("🌟"):
                tom["more"] = int(mm.group(1))
            continue
        ms = _STOCK_RE.match(ln)
        if ms:
            cur = {"rank": int(ms.group(1)), "name": ms.group(2).strip(),
                   "code": ms.group(3), "score": None, "score_max": None,
                   "when": None, "price": None, "move_1m": None, "sections": []}
            out["stocks"].append(cur)
            sec = None
            continue
        if cur is None:
            continue
        if s.startswith("-") or s.startswith("−"):      # ' - 기대점수 : …'
            body = s.lstrip("-− ").strip()
            m = _SCORE_RE.search(body)
            if m:
                cur["score"], cur["score_max"] = int(m.group(1)), int(m.group(2))
                continue
            m = _WHEN_RE.search(body)
            if m and body.startswith("발표시각"):
                cur["when"] = m.group(1).strip()
                continue
            m = _PRICE_RE.search(body)
            if m:
                cur["price"] = f"{m.group(1)}원"
                if m.group(3):
                    cur["move_1m"] = _pct(m.group(2) or "", m.group(3))
                continue
            continue
        mb = _BULLET_RE.match(ln)
        if mb:
            if sec is None:                              # 섹션 없이 온 불릿
                sec = {"icon": "", "title": "", "bullets": []}
                cur["sections"].append(sec)
            sec["bullets"].append(mb.group(1).strip())
            continue
        msec = _SECTION_RE.match(ln)
        if msec:
            sec = {"icon": msec.group(1), "title": msec.group(2).strip(),
                   "bullets": []}
            cur["sections"].append(sec)
    if tom["items"] or tom["label"]:
        out["tomorrow"] = tom
    return out
