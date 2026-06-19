"""Daily Byte Weekly — 주간 KR 수급 종합 (일요일 22:00 KST, SV weekly 동일 시각).

이번 주(월–금) Daily Byte 아카이브 본문을 모아 Gemini Pro 로 주간 수급
종합을 작성 → NOAH 채널 push + 아카이브(kind="weekly"). SV weekly_pusher.py
패턴 mirror. 수치는 daily 브리프(pykrx 정확값)를 그대로 인용 — weekly 는
재계산 없이 누적 방향 / 섹터 로테이션 / 지속 매집·이탈 추세만 종합한다.

systemd: daily-byte-weekly.timer (Sun 22:00 KST) → daily-byte-weekly.service.
수동: cd ~/stock && .venv/bin/python -m bot.daily_kr_weekly
"""

from __future__ import annotations

import json
import logging
import os
from bot.genai_factory import effective_key as _effective_key
from datetime import timedelta

from bot.daily_kr_flow import (
    _now_kst,
    _post_process,
    push_telegram,
    _save_daily_byte_archive,
    _log_daily_byte_usage,
    _DAILY_BYTE_ARCHIVE_DIR,
)

log = logging.getLogger("bot.daily_kr_weekly")


def _week_dates() -> list[str]:
    """이번 주 월요일~오늘 YYYY-MM-DD 리스트 (일요일 실행 기준 월~일)."""
    today = _now_kst().date()
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def _load_week_briefs() -> list[dict]:
    """이번 주 KR daily 아카이브 본문 로드 (날짜 오름차순) — weekly 류 +
    미국 daily(us_daily_byte, #280 부터 같은 디렉토리 공유) 제외. US 는
    bot/us_market_weekly 가 별도 종합 (사용자 2026-06-12 '주간 한·미 다')."""
    out: list[dict] = []
    for d in _week_dates():
        day_dir = os.path.join(_DAILY_BYTE_ARCHIVE_DIR, d)
        if not os.path.isdir(day_dir):
            continue
        for fn in sorted(os.listdir(day_dir)):
            if (not fn.endswith(".json") or "weekly" in fn
                    or "us_daily_byte" in fn):
                continue
            try:
                with open(os.path.join(day_dir, fn), encoding="utf-8") as f:
                    rec = json.load(f)
                if rec.get("body"):
                    out.append({"date": rec.get("date", d), "body": rec["body"]})
            except Exception as exc:
                log.warning("weekly: load %s/%s failed: %s", d, fn, exc)
    return out


_PROMPT = """당신은 한국 주식시장 수급 전문 buy-side 애널리스트입니다. 아래는
이번 주(월–금) 매일 장 마감 후 작성된 'Daily Byte' 수급 브리프 모음입니다.
각 브리프의 수치는 pykrx 에서 직접 산출된 **정확한 투자주체별 수급 값**
입니다. 이를 종합해 'Weekly Byte' 주간 수급 종합을 작성하세요.

{briefs}

---

작성 규칙:
1. **수치는 위 일별 브리프의 값을 그대로 인용** — 재계산·합산 추정·창작 금지.
   주간 흐름은 일별 값의 방향성/누적 추세로 서술 (예: "외국인 주 초반
   순매도 → 후반 순매수 전환").
2. **주간 관점 종합**: 한 주 동안의 (a) 외인 vs 기관 누적 방향, (b) 주도
   섹터 로테이션 (유출→유입), (c) 꾸준히 매집/이탈된 종목, (d) 주중 수급
   반전 종목을 묶어서 서술. 일별 중복 나열이 아니라 **주간 스토리**로.
3. **catalyst 맥락 (web search)**: 주간 주도주의 배경 이벤트(실적·수주·
   정책)를 web search 로 확인. 출처 날짜는 오늘 이하, 미확인 시 '맥락
   미확인' 명시. 추측 catalyst 창작 금지.
4. **중립 표현**: "주목 종목"은 수급 관찰일 뿐 BUY/SELL 권고 아님.
5. **구조** (각 섹션 지정 이모지로 시작하는 헤더 한 줄):
   📊 주간 수급 총평 (외인/기관 한 주 누적 방향 + breadth)
   🔄 주간 섹터 로테이션 (유출 → 유입)
   🔥 한 주 강했던 섹터·종목 (지속 매집)
   📈 주목할 주간 수급 패턴 (꾸준한 매집 / 첫 출현 / 반전)
   🏆 주간 주목 종목 (수급 근거 + catalyst, 중립)
   ⚠️ 경고 시그널 (주중 양→음 전환 / 이탈 가속)
   🎯 한 줄 결론
6. **본문은 일반 텍스트** + 위 이모지 헤더. 강조는 `**굵게**` 표기 (HTML
   태그·`<br>`·`<ul>` 금지 — 서식 변환은 시스템 담당). 각 항목 줄바꿈.
7. 분량: 한 주 핵심 위주 간결하게. 일별 브리프에 있는 종목/수치만 다룰 것.

면책·디스클레이머("투자 권유 아님" 등) 문구는 본문 어디에도 포함하지 말 것
— 채널·페이지 차원에서 별도 표기됨 (사용자 정책 2026-06-11).
"""


def generate() -> tuple[str, float] | None:
    """주간 브리프 로드 → Pro 종합 → guard → archive(kind=weekly) →
    (제목 포함 본문, cost_krw). 이번 주 daily 브리프 0건이면 None."""
    import time as _time
    _t0 = _time.monotonic()
    api_key = _effective_key()
    if not api_key:
        log.error("weekly: GOOGLE_API_KEY missing")
        return None

    briefs = _load_week_briefs()
    if not briefs:
        log.warning("weekly: 이번 주 daily 브리프 0건 — Weekly skip")
        return None

    briefs_txt = "\n\n".join(
        f"=== {b['date']} ===\n{b['body']}" for b in briefs
    )
    prompt = _PROMPT.format(briefs=briefs_txt)

    from bot.screener import _call_pro, _USD_TO_KRW
    _PRO_IN, _PRO_OUT = 1.25, 10.00
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("weekly: Pro call failed: %s", exc)
        return None
    if not raw:
        return None

    today = _now_kst()
    body = _post_process(raw, today.date().isoformat())
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_daily_byte_usage(pt, ot, cost_krw)

    today_yyyymmdd = today.strftime("%Y%m%d")
    _save_daily_byte_archive(body, cost_krw, today_yyyymmdd,
                             elapsed_sec=_time.monotonic() - _t0, kind="weekly")

    # 주간 범위 (월~금) 제목
    days = [b["date"] for b in briefs]
    span = f"{days[0][5:].replace('-', '.')}~{days[-1][5:].replace('-', '.')}"
    title = f"📊 <b>한국 Weekly Byte - {span}</b>"
    full = (f"{title}\n<i>주간 KR 수급 종합 ({len(briefs)}일 · 생성 "
            f"{today:%m.%d %H:%M} KST)</i>\n\n{body}")
    return full, cost_krw


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
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
        log.error("weekly: generation failed / no data — skipping push")
        return 1
    body, cost = result
    log.info("weekly: generated (₩%.1f) — pushing", cost)
    ok = push_telegram(body)
    log.info("weekly: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
