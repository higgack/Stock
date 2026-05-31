"""부동산 Byte Monthly — 월간 부동산 종합 (매월 1일 09:00 KST).

부동산 주간 브리프(금)가 weekly cadence 이므로, Daily Byte 의 Weekly(주간
종합)에 대응하는 건 월간 종합. 이번 달 주간 아카이브 본문을 모아 Gemini
Pro 로 월간 추세 종합 → push + 아카이브(kind="monthly"). daily_kr_weekly
패턴 mirror. 수치는 주간 브리프 인용 — 월간은 재계산 없이 추세만.

systemd: realestate-byte-monthly.timer (매월 1일 09:00 KST).
수동: cd ~/stock && .venv/bin/python -m bot.realestate_monthly
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("bot.realestate_monthly")


def _this_month_briefs() -> list[dict]:
    """이번 달 주간(kind!=monthly) 부동산 아카이브 본문 (날짜 오름차순)."""
    from bot.realestate_brief import _ARCHIVE_DIR, _now_kst
    month = _now_kst().date().isoformat()[:7]
    out: list[dict] = []
    if not os.path.isdir(_ARCHIVE_DIR):
        return out
    for d in sorted(os.listdir(_ARCHIVE_DIR)):
        if not d.startswith(month):
            continue
        day_dir = os.path.join(_ARCHIVE_DIR, d)
        if not os.path.isdir(day_dir):
            continue
        for fn in sorted(os.listdir(day_dir)):
            if not fn.endswith(".json") or "monthly" in fn:
                continue
            try:
                with open(os.path.join(day_dir, fn), encoding="utf-8") as f:
                    rec = json.load(f)
                if rec.get("body"):
                    out.append({"date": rec.get("date", d), "body": rec["body"]})
            except Exception as exc:
                log.warning("re-monthly: load %s/%s failed: %s", d, fn, exc)
    return out


_PROMPT = """당신은 한국 부동산 시장 전문 애널리스트입니다. 아래는 이번 달
매주 작성된 '부동산 Byte' 주간 브리프 모음입니다(아파트 실거래가 기반).
이를 종합해 '월간 부동산 종합'을 작성하세요.

{briefs}

---
작성 규칙:
1. **수치는 위 주간 브리프 값 그대로 인용** — 재계산·창작 금지.
2. **월간 관점 종합**: 한 달간 (a) 지역별 가격 방향(상승/하락/보합),
   (b) 거래량 추세(증가/위축), (c) 수도권 vs 지방 격차 변화를 묶어 서술.
   주간 중복 나열이 아니라 **월간 스토리**로.
3. **매크로 연계 (web search)**: 기준금리·전세가율·정책(대출규제 등)을
   확인해 가격 방향성 맥락 (출처 날짜 오늘 이하, 미확인 시 명시).
4. **섹터 함의 (중립)**: 건설·부동산(리츠)·은행(부동산 PF) 월간 영향.
   투자 권유 금지.
5. **구조** (각 섹션 지정 이모지 헤더 한 줄):
   🏠 월간 시장 총평
   📍 지역별 온도차 (수도권·지방 월간 변화)
   📈 거래량·가격 추세
   💰 매크로·정책 연계
   🏗️ 섹터 함의 (중립)
   🎯 한 줄 결론
6. **본문 일반 텍스트** + 이모지 헤더. 강조 **굵게**. HTML 태그·`---` 금지.
면책: 끝에 "본 브리프는 공공데이터 관찰 (교육·정보 목적), 투자 권유 아님" 1줄.
"""


def generate() -> tuple[str, float] | None:
    import time as _time
    _t0 = _time.monotonic()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("re-monthly: GOOGLE_API_KEY missing")
        return None
    briefs = _this_month_briefs()
    if not briefs:
        log.warning("re-monthly: 이번 달 주간 브리프 0건 — skip")
        return None
    briefs_txt = "\n\n".join(f"=== {b['date']} ===\n{b['body']}" for b in briefs)
    prompt = _PROMPT.format(briefs=briefs_txt)

    from bot.screener import _call_pro
    from bot.realestate_brief import (_post_process, _log_usage, _save_archive,
                                      _now_kst, _PRO_IN, _PRO_OUT, _USD_TO_KRW)
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("re-monthly: Pro call failed: %s", exc)
        return None
    if not raw:
        return None
    now = _now_kst()
    body = _post_process(raw)
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_usage(pt, ot, cost_krw)
    ymd = now.strftime("%Y%m")
    _save_archive(body, cost_krw, ymd, _time.monotonic() - _t0,
                  kind="monthly", png_rel=None)
    span = f"{briefs[0]['date'][5:]}~{briefs[-1]['date'][5:]}"
    title = f"🏠 <b>부동산 Monthly - {now.year}.{now.month:02d}</b>"
    full = (f"{title}\n<i>월간 부동산 종합 ({len(briefs)}주 · {span}) · 생성 "
            f"{now:%m.%d %H:%M} KST</i>\n\n{body}")
    return full, cost_krw


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
        log.error("re-monthly: generation failed / no data — skipping push")
        return 1
    body, cost = result
    log.info("re-monthly: generated (₩%.1f) — pushing", cost)
    from bot.daily_kr_flow import push_telegram
    ok = push_telegram(body)
    log.info("re-monthly: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
