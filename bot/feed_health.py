"""피드 수집기 생존 도장 — 대시보드의 '마지막 점검'.

대시보드가 보여주는 '마지막 새 글 / 기록 / 피드'는 **항목이 도착한** 시각이다.
며칠 조용하면 "수집기가 죽었나, 진짜 새 게 없나"를 화면이 구별해 주지 못한다
(실수 #43: 침묵이 최악). 2026-08-20 부동산 대시보드가 정확히 그 상태였다 —
'마지막 기록 08-17' 인데 그게 정상인지 장애인지 알 길이 없었다.

그래서 수집기가 **원천을 성공적으로 읽을 때마다**(새 항목이 0건이어도) 여기
도장을 찍고, 화면이 그 시각을 같이 보여준다. 새 항목 시각과 점검 시각이 함께
있으면 '조용한 것'과 '죽은 것'이 구별된다.

파일 = ~/.tradingagents/feed_health/<feed>.txt (KST ISO 문자열 1줄).
mtime 이 아니라 **내용**으로 저장한다 — 백업·복사가 mtime 을 갈아버려도 거짓말을
하지 않게(실수 #30 의 이웃: 부수적 파일조작이 진실을 바꾸면 안 된다).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.feed_health")

_KST = timezone(timedelta(hours=9))
_DIR = Path(os.environ.get("HOME") or str(Path.home())) / ".tradingagents" / "feed_health"

# 피드별 '이 정도 지나면 이상' 상한(시간). 수집 주기의 여유 배수 —
# 정확도가 아니라 **경보가 울릴 자격**을 정하는 값이라 넉넉하게(실수 #27).
#   blog 30분 폴링 / reddit 1분 폴링 / cheongyak 평일 10·14시 / realestate 금 09:00
_MAX_GAP_H: dict[str, float] = {
    "blog": 3.0,
    "reddit": 2.0,
    "cheongyak": 36.0,      # 금→월 주말을 건너도 안 울리게
    "realestate": 200.0,    # 주 1회(금) + 월 1회 → 8일 여유
    # Daily Byte — 평일 19:00(KR)·08:00(US). 금요일 밤부터 월요일 저녁까지
    # 주말을 통째로 건너도 안 울리게 넉넉히(#27 경계는 넉넉하게).
    "daily_byte_kr": 80.0,
    "daily_byte_us": 80.0,
}


def _path(feed: str) -> Path:
    return _DIR / f"{feed}.txt"


def mark(feed: str) -> None:
    """원천을 성공적으로 읽었다 — 지금 시각을 도장. 실패해도 조용히 넘어간다
    (수집 본류를 절대 막지 않는다)."""
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        _path(feed).write_text(
            datetime.now(_KST).isoformat(timespec="seconds"), encoding="utf-8")
    except Exception as exc:
        log.warning("feed_health mark(%s) failed: %s", feed, exc)


def last(feed: str) -> str:
    """마지막 점검 시각 'YYYY-MM-DD HH:MM' (기록 없으면 '')."""
    try:
        return _path(feed).read_text(encoding="utf-8").strip()[:16].replace("T", " ")
    except Exception:
        return ""


def note(feed: str) -> str:
    """화면에 붙일 한 줄 — '점검 08-20 12:40' / '점검 … ⚠️ 지연' / '점검 기록 없음'.

    ⚠️ 는 `_MAX_GAP_H` 를 넘겼을 때만. 등록 안 된 피드는 지연 판정을 못 하므로
    그 사실을 밝힌다(조용히 ✅ 처럼 보이면 안 된다 — 실수 #41)."""
    ts = last(feed)
    if not ts:
        return "점검 기록 없음"
    out = f"점검 {ts[5:]}"
    cap = _MAX_GAP_H.get(feed)
    if cap is None:
        return out + " (지연 기준 미등록)"
    try:
        gap = (datetime.now(_KST)
               - datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=_KST))
        if gap > timedelta(hours=cap):
            return f"{out} ⚠️ {gap.total_seconds() / 3600:.0f}시간째 점검 없음"
    except ValueError:
        return out + " (형식 이상)"
    return out
