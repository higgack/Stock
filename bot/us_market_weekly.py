"""US Weekly Byte — 주간 미국 시장 종합 (일요일 22:00 KST, KR Weekly 동일 시각).

이번 주 미국 Daily Byte 아카이브(kind=us_daily) 본문을 모아 Gemini Pro 로
주간 시장 종합을 작성 → NOAH 채널 push + 아카이브(kind="us_weekly").
daily_kr_weekly.py 패턴 mirror — 수치는 daily 브리프(yfinance 정확값)를
그대로 인용, weekly 는 재계산 없이 주간 방향/섹터 로테이션/지속 강세·약세
종목만 종합한다 (사용자 2026-06-12 'Daily Byte 주간 한국 미국 다').

systemd: daily-byte-weekly-us.timer (Sun 22:00 KST) → 동명 service.
수동: cd ~/stock && .venv/bin/python -m bot.us_market_weekly
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from bot.us_market_daily import (
    _now_kst,
    _post_process,
    _log_usage,
    push_telegram_sync,
    _ARCHIVE_DIR,
)

log = logging.getLogger("bot.us_market_weekly")


def _week_dates() -> list[str]:
    """이번 주 월요일~오늘 YYYY-MM-DD 리스트 (일요일 실행 기준 월~일)."""
    today = _now_kst().date()
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def _load_week_briefs() -> list[dict]:
    """이번 주 미국 daily(us_daily_byte, weekly 제외) 본문 로드 (날짜순)."""
    out: list[dict] = []
    for d in _week_dates():
        day_dir = os.path.join(_ARCHIVE_DIR, d)
        if not os.path.isdir(day_dir):
            continue
        for fn in sorted(os.listdir(day_dir)):
            if (not fn.endswith(".json") or "weekly" in fn
                    or "us_daily_byte" not in fn):
                continue
            try:
                with open(os.path.join(day_dir, fn), encoding="utf-8") as f:
                    rec = json.load(f)
                if rec.get("body"):
                    out.append({"date": rec.get("date", d), "body": rec["body"]})
            except Exception as exc:
                log.warning("us weekly: load %s/%s failed: %s", d, fn, exc)
    return out


_PROMPT = """당신은 미국 주식시장 전문 buy-side 애널리스트입니다. 아래는 이번 주
(월–금) 미국 장 마감 후 작성된 '미국 Daily Byte' 시장 브리프 모음입니다.
각 브리프의 지수/종목 수치는 yfinance 에서 직접 산출된 **정확한 값**입니다.
이를 종합해 '미국 Weekly Byte' 주간 시장 종합을 작성하세요.

{briefs}

---

작성 규칙:
1. **수치는 위 일별 브리프의 값을 그대로 인용** — 재계산·합산 추정·창작 금지.
   주간 흐름은 일별 값의 방향성/누적 추세로 서술 (예: "나스닥 주 초반
   하락 → 후반 반등").
2. **주간 관점 종합**: 한 주 동안의 (a) 주요 지수·금리·달러 누적 방향,
   (b) 주도 섹터 로테이션 (유출→유입), (c) 한 주 내내 강했던/약했던 종목
   (Mag-7 제외 신선 종목 우선), (d) 주중 반전 종목을 묶어 **주간 스토리**로.
3. **catalyst 맥락 (web search)**: 주간 주도주의 배경 이벤트(실적·가이던스·
   정책·금리)를 web search 로 확인. 출처 날짜는 오늘 이하, 미확인 시
   '맥락 미확인' 명시. 추측 catalyst 창작 금지.
4. **중립 표현**: "주목 종목"은 관찰일 뿐 BUY/SELL 권고 아님.
5. **구조** (각 섹션 지정 이모지로 시작하는 헤더 한 줄):
   📊 주간 시장 총평 (지수·금리·달러 한 주 누적 방향 + breadth)
   🔄 주간 섹터 로테이션 (유출 → 유입)
   🔥 한 주 강했던 섹터·종목 (지속 강세, Mag-7 제외 우선)
   📈 주목할 주간 패턴 (꾸준한 상승 / 신고가 / 반전)
   🏆 주간 주목 종목 (근거 + catalyst, 중립)
   ⚠️ 경고 시그널 (주중 상승→하락 전환 / 낙폭 가속)
   🎯 한 줄 결론
6. **본문은 일반 텍스트** + 위 이모지 헤더. 강조는 `**굵게**` 표기 (HTML
   태그·`<br>`·`<ul>` 금지 — 서식 변환은 시스템 담당). 각 항목 줄바꿈.
7. 분량: 한 주 핵심 위주 간결하게. 일별 브리프에 있는 종목/수치만 다룰 것.

면책·디스클레이머("투자 권유 아님" 등) 문구는 본문 어디에도 포함하지 말 것
— 채널·페이지 차원에서 별도 표기됨 (사용자 정책 2026-06-11).
"""


def _save_weekly_archive(body: str, cost_krw: float,
                         elapsed_sec: float = 0.0) -> str | None:
    """→ daily_byte_archive/YYYY-MM-DD/HHMMSS_us_daily_byte_weekly.json.
    파일명에 'weekly' 포함 — KR/US weekly 의 daily 로더가 둘 다 제외."""
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{now:%H%M%S}_us_daily_byte_weekly.json")
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "date": date_iso,
            "kind": "us_weekly",
            "body": body,
            "cost_krw": round(cost_krw, 4),
            "elapsed_sec": round(elapsed_sec, 1),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        return path
    except Exception as exc:
        log.warning("us weekly: archive write failed: %s", exc)
        return None


def generate() -> tuple[str, float] | None:
    """주간 브리프 로드 → Pro 종합 → guard → archive(kind=us_weekly) →
    (제목 포함 본문, cost_krw). 이번 주 us daily 0건이면 None."""
    import time as _time
    _t0 = _time.monotonic()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("us weekly: GOOGLE_API_KEY missing")
        return None

    briefs = _load_week_briefs()
    if not briefs:
        log.warning("us weekly: 이번 주 미국 daily 브리프 0건 — skip")
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
        log.exception("us weekly: Pro call failed: %s", exc)
        return None
    if not raw:
        return None

    body = _post_process(raw)
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_usage(pt, ot, cost_krw)
    _save_weekly_archive(body, cost_krw,
                         elapsed_sec=_time.monotonic() - _t0)

    try:
        from bot.dashboard import regenerate_daily_byte_index
        regenerate_daily_byte_index()
    except Exception as exc:
        log.warning("us weekly: dashboard regen failed: %s", exc)

    days = [b["date"] for b in briefs]
    span = f"{days[0][5:].replace('-', '.')}~{days[-1][5:].replace('-', '.')}"
    today = _now_kst()
    title = f"🇺🇸 <b>미국 Weekly Byte - {span}</b>"
    full = (f"{title}\n<i>주간 미국 시장 종합 ({len(briefs)}일 · 생성 "
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
        log.error("us weekly: generation failed / no data — skipping push")
        return 1
    body, cost = result
    log.info("us weekly: generated (₩%.1f) — pushing", cost)
    ok = push_telegram_sync(body)
    log.info("us weekly: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
