"""청약 Byte — 신규 아파트 분양 모집공고 daily 피드 (텔레그램, 평일 10:00 KST).

부동산 Byte(실거래·R-ONE 주간 가격 추세)와 별도 surface — 분양 모집공고는
매일 신규로 뜨고 청약 일정이 임박 이벤트라 성격이 다르다 (사용자 정책
2026-05-31 "청약은 Daily"). ticker·5거래일과 완전 독립.

수치(단지·지역·세대·일정·링크)는 청약홈 odcloud API 에서 정확 추출(환각 0),
Gemini Pro 는 신규 분양 지역 분포·공급 규모·임박 청약 1-2줄 맥락만 narrate
(중립, 투자 권유 아님). Daily Byte 인프라 mirror.

DATA_GO_KR_API_KEY 없으면 cheongyak_key_ready() gate 가 graceful skip.
systemd: cheongyak-byte.timer (평일 10:00 KST).
수동: cd ~/stock && .venv/bin/python -m bot.cheongyak_brief
"""

from __future__ import annotations

import json
import logging
import os
from bot.genai_factory import effective_key as _effective_key
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.cheongyak_brief")

_KST = timezone(timedelta(hours=9))
_HOME = os.path.expanduser("~")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "cheongyak_archive")
_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "cheongyak_usage.jsonl")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
_STATE_PATH = os.path.join(_HOME, ".tradingagents", "cheongyak_seen.json")
_USD_TO_KRW = 1330.0
_PRO_IN, _PRO_OUT = 1.25, 10.00
_RECENT_DAYS = 3   # 최초/일상 run 모두 최근 N일 내 공고만 신규 후보


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _within_recent(notice_date: str, days: int = _RECENT_DAYS) -> bool:
    """모집공고일(YYYY-MM-DD)이 최근 days일 이내인가."""
    try:
        d = datetime.strptime(notice_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return (_now_kst().date() - d).days <= days and d <= _now_kst().date() + timedelta(days=1)


def _load_seen() -> tuple[set, bool]:
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        return set(st.get("seen", [])), bool(st.get("initialized"))
    except Exception:
        return set(), False


def _save_seen(seen: set) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        # 무한 성장 방지 — 최근 4000개만 유지
        keep = list(seen)[-4000:]
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"seen": keep, "initialized": True}, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("cheongyak: state save failed: %s", exc)


def build_summary(anns: list[dict]) -> str:
    lines = [f"[신규 분양 모집공고] 총 {len(anns)}건 (청약홈 공식)"]
    for a in anns:
        when = ""
        if a.get("subscript_begin"):
            when = f" · 청약 {a['subscript_begin']}~{a.get('subscript_end','')}"
        win = f" · 당첨발표 {a['winner_date']}" if a.get("winner_date") else ""
        units = f" · {a['total_units']}세대" if a.get("total_units") else ""
        kind = f" [{a['kind']}]" if a.get("kind") else ""
        lines.append(
            f"  {a.get('notice_date','')} [{a.get('region','')}] {a.get('name','')}{kind}"
            f"{units}{when}{win}")
    return "\n".join(lines)


def build_competition_block(comps: list[dict], top: int = 8) -> str:
    """최근 마감 단지 경쟁률 (단지·주택형 단위 합산) → 텍스트 블록.
    is_short=True 면 미달 표기. 빈 리스트면 ''."""
    if not comps:
        return ""
    short_cnt = sum(1 for c in comps if c.get("is_short"))
    lines = [f"[최근 마감 청약 경쟁률] 단지·주택형 {len(comps)}건 (미달 {short_cnt}건)"]
    # 미달이 의미있으므로 미달 먼저, 그 다음 경쟁률 내림차순
    comps_sorted = sorted(
        comps,
        key=lambda c: (0 if c.get("is_short") else 1, -(c.get("rate") or 0)),
    )[:top]
    for c in comps_sorted:
        if c.get("is_short"):
            tag = f"⚠️ 미달 {c['shortage']}세대"
        else:
            tag = f"경쟁률 {c['rate']:.2f}:1"
        lines.append(
            f"  [{c.get('region','')}] {c.get('name','')} {c.get('model','')} "
            f"· 공급 {c['supply']} · 접수 {c['applicants']} · {tag}")
    return "\n".join(lines)


_PROMPT = """당신은 한국 분양·청약 시장 전문 애널리스트입니다. 아래는 청약홈
(한국부동산원) 에서 직접 추출한 **신규 아파트 분양 모집공고**입니다 (정확한
수치). 이를 '청약 Byte' daily 피드의 도입 맥락으로 요약하세요.

{summary}

---
작성 규칙:
1. **수치·단지명·일정은 위 데이터 글자 그대로** — 창작·재계산 금지.
2. 맨 위에 **1-2문장 맥락**만: 신규 공고의 지역 분포(수도권 vs 지방),
   공급 규모(총 세대), 청약 접수 임박 단지 여부. 중립 정보 서술.
3. 이어서 **신규 공고 목록을 그대로 정리** (지역·단지명·세대·청약일정
   줄단위). 목록은 위 데이터 순서 유지.
4. [최근 마감 청약 경쟁률] 블록이 있으면 그 아래에 별도 섹션으로 그대로
   정리 (단지·주택형·공급·접수·경쟁률 또는 ⚠️ 미달). 미달 다수면 수요
   위축, 두자릿수 경쟁률 다수면 수요 견조 — 1줄 중립 코멘트.
5. 본문 일반 텍스트 + 줄바꿈. 강조는 **굵게** (단지명·임박일정·미달 정도).
   HTML 태그·`---` 수평선 금지. 특정 단지 청약 권유 금지.
면책: 끝에 "본 피드는 청약홈 공공데이터 관찰 (교육·정보 목적), 청약 권유 아님" 1줄.
"""


def _post_process(text: str) -> str:
    try:
        from bot.daily_kr_flow import _post_process as _pp
        return _pp(text, _now_kst().date().isoformat())
    except Exception:
        import re
        return re.sub(r"(?m)^[^\w\n]*[-*_]{2,}[^\w\n]*$", "", text).strip()


def _log_usage(pt: int, ot: int, cost_krw: float) -> None:
    import time as _time
    for path, rec in (
        (_USAGE_LOG, {"ts": _now_kst().isoformat(timespec="seconds"),
                      "date": _now_kst().date().isoformat(),
                      "month": _now_kst().date().isoformat()[:7],
                      "prompt_tok": pt, "output_tok": ot, "cost_krw": round(cost_krw, 4)}),
        (_NOAH_USAGE_LOG, {"ts": _time.time(), "type": "llm_call",
                           "model": "gemini-2.5-pro", "prompt_tokens": pt,
                           "completion_tokens": ot,
                           "cost_usd": round(cost_krw / _USD_TO_KRW, 6),
                           "subsystem": "cheongyak"}),
    ):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("cheongyak: usage log failed: %s", exc)


def _save_archive(body: str, cost_krw: float, count: int, elapsed: float) -> None:
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{now:%H%M%S}_cheongyak.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": now.isoformat(timespec="seconds"), "date": date_iso,
                       "count": count, "body": body, "cost_krw": round(cost_krw, 4),
                       "elapsed_sec": round(elapsed, 1)}, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("cheongyak: archive write failed: %s", exc)
        return
    try:
        from bot.dashboard import regenerate_cheongyak_index
        regenerate_cheongyak_index()
    except Exception as exc:
        log.warning("cheongyak: dashboard regen failed: %s", exc)


def generate() -> tuple[str, float, int] | None:
    import time as _time
    _t0 = _time.monotonic()
    api_key = _effective_key()
    if not api_key:
        log.error("cheongyak: GOOGLE_API_KEY missing")
        return None
    from bot.cheongyak_client import (recent_announcements,
                                       recent_competition_enriched,
                                       cheongyak_key_ready)
    if not cheongyak_key_ready():
        return None
    anns = recent_announcements(per_page=200)
    if not anns:
        log.warning("cheongyak: 분양 공고 0건 — skip")
        return None
    # 경쟁률 (별도 활용신청 — 미등록이면 빈 리스트 graceful skip)
    try:
        comps = recent_competition_enriched(per_page_compet=200, per_page_anns=300)
    except Exception as exc:
        log.warning("cheongyak: 경쟁률 fetch 실패: %s", exc)
        comps = []

    seen, initialized = _load_seen()
    # 신규 = 미열람 + 최근 N일 공고 (최초 run 도 최근 N일 batch 만 push)
    new = [a for a in anns if a.get("pblanc_no") and a["pblanc_no"] not in seen
           and _within_recent(a.get("notice_date", ""))]
    # seen 갱신은 전체 fetch 대상으로 (이전 공고 재평가 방지)
    seen |= {a["pblanc_no"] for a in anns if a.get("pblanc_no")}
    _save_seen(seen)
    if not new:
        log.info("cheongyak: 신규 공고 없음 (initialized=%s) — skip push", initialized)
        return None
    new.sort(key=lambda a: a.get("notice_date", ""), reverse=True)

    from bot.screener import _call_pro
    summary = build_summary(new)
    comp_block = build_competition_block(comps)
    if comp_block:
        summary = f"{summary}\n\n{comp_block}"
    prompt = _PROMPT.format(summary=summary)
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=False)
    except Exception as exc:
        log.exception("cheongyak: Pro call failed: %s", exc)
        return None
    if not raw:
        return None
    body = _post_process(raw)
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_usage(pt, ot, cost_krw)
    _save_archive(body, cost_krw, len(new), _time.monotonic() - _t0)

    title = f"🎟️ <b>청약 Byte - {_now_kst():%Y.%m.%d}</b>"
    full = (f"{title}\n<i>신규 분양 모집공고 {len(new)}건 · "
            f"생성 {_now_kst():%m.%d %H:%M} KST</i>\n\n{body}")
    return full, cost_krw, len(new)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    result = generate()
    if result is None:
        log.info("cheongyak: 신규 공고 없음 / 데이터 없음 — push skip")
        return 0
    body, cost, n = result
    log.info("cheongyak: generated (%d건, ₩%.1f) — pushing", n, cost)
    from bot.daily_kr_flow import push_telegram
    ok = push_telegram(body)
    log.info("cheongyak: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
