"""Periodic event-based health check for the trade-bot pipeline.

Runs from systemd timer (trade-bot-health.timer) hourly. Two signals:

  Cycle gap: today's KST date is past an expected BeOn publication
  date (11일·21일 잠정 / 익월 1일 잠정 / 익월 15일 확정) by more
  than TRADE_CYCLE_GAP_DAYS days, but no alert with the matching
  period_kind landed in the store within ±2 days of that date.
  → posts a ⚠️ Telegram alert listing the missing publications,
  de-duplicated per-day so the same gap doesn't re-fire hourly.

  Delivery gap (실수 #406, 2026-09-25): a relay unit (listener/sync)
  logged "forwarded N" into the private channel, but trade-bot's journal
  shows fewer than N `ingested` lines after it → ⚠️ alert pointing at
  `python -m trade.bot_health`. 그날 27건 포워드가 inbox 에 한 건도 안
  들어갔는데 32분 뒤 사람이 백필 dry-run 으로 알아챘다 — 두 저널을 나란히
  놓으면 기계가 알 수 있었다. 판정은 `trade.bot_health.delivery_check`
  단일 출처(#38). 판정 불가(저널 권한 등)는 경고 로그만 — '이상 없음' 으로
  접지 않는다(#54). 같은 누락은 6시간에 한 번만 보낸다.

Why no time-based dormancy: BeOn publishes only ~4 times a month,
so the ~7-10 day silence between publication dates is normal
behavior. A 'no forward for N hours' threshold would false-positive
on every quiet stretch. Tying the alert to specific expected
publication dates means alerts fire only when a publication that
should have arrived didn't — exactly what the operator cares about.

Missing TRADE_BOT_TOKEN / TRADE_CHANNEL_CHAT_IDS silently skips
notifications so the script also works in dev / restore scenarios.

Usage:
    .venv/bin/python -m trade.scripts.health_check
"""

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade.store import list_all_alerts, open_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("health-check")

DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
STORE_PATH = DATA_DIR / "store.db"
MARKER_DIR = DATA_DIR / ".health-markers"
MARKER_DIR.mkdir(parents=True, exist_ok=True)

CYCLE_GAP_DAYS = int(os.environ.get("TRADE_CYCLE_GAP_DAYS") or "2")


def _notify(text: str) -> None:
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        log.info("notify skipped: no TRADE_BOT_TOKEN / TRADE_CHANNEL_CHAT_IDS")
        return
    chat_id = chat_ids.split(",")[0].strip()
    try:
        r = subprocess.run(
            [
                "curl", "-s", "-m", "10",
                "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "--data-urlencode", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}",
                "--data-urlencode", "parse_mode=HTML",
            ],
            timeout=15,
            check=False,
            capture_output=True,
        )
        # 응답을 안 보면 4096자 초과(400 'message is too long')·잘못된 HTML 이 **조용히**
        # 사라진다(#12 silent-fail — 독립 리뷰 2026-09-10). 본문은 안 찍는다(§Secrets).
        body = (r.stdout or b"").decode("utf-8", "replace")
        if r.returncode != 0 or '"ok":true' not in body.replace(" ", ""):
            log.warning("notify not delivered: rc=%s len=%d resp=%s",
                        r.returncode, len(text), body[:200])
    except Exception as e:
        log.warning("notify failed: %s", e)


def _alert_once_per_window(marker_name: str, window_seconds: int) -> bool:
    """True if we should send the alert (no marker, or marker older than
    window). Touches the marker after returning True so consecutive runs
    don't re-fire the same alert.
    """
    marker = MARKER_DIR / marker_name
    now = time.time()
    if marker.exists() and (now - marker.stat().st_mtime) < window_seconds:
        return False
    marker.touch()
    return True


def _kst_today() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=9)


def _expected_recent_publications(today_kst: datetime) -> list[tuple[str, str]]:
    """Return [(YYYY-MM-DD, kind)] for publications whose date is in
    [today - CYCLE_GAP_DAYS - 1, today - CYCLE_GAP_DAYS]. These are the
    cycles that should have arrived by now; we look for evidence in
    the store, alert if missing.
    """
    y, m, d = today_kst.year, today_kst.month, today_kst.day
    candidates: list[tuple[str, str]] = []
    for day, kind in (
        (11, "decadal_10"),
        (21, "decadal_20"),
    ):
        candidates.append((f"{y:04d}-{m:02d}-{day:02d}", kind))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    # Previous month's publication dates fall in this month if we're
    # past the 1st / 15th.
    candidates.append((f"{y:04d}-{m:02d}-01", "monthly_preliminary"))
    candidates.append((f"{y:04d}-{m:02d}-15", "monthly_final"))
    # Hits a target publication if cutoff < today.
    target_date = (today_kst - timedelta(days=CYCLE_GAP_DAYS)).strftime("%Y-%m-%d")
    return [(d, k) for d, k in candidates if d <= target_date]


def check_cycle_gap() -> None:
    """⚠️ if an expected BeOn publication date is more than
    CYCLE_GAP_DAYS in the past and no matching alert is in the store.
    """
    if not STORE_PATH.exists():
        log.info("cycle_gap: store empty, skipping")
        return
    today_kst = _kst_today()
    targets = _expected_recent_publications(today_kst)
    if not targets:
        return

    conn = open_db(STORE_PATH)
    try:
        rows = list_all_alerts(conn)
    finally:
        conn.close()

    posted_dates = {(r.get("posted_at") or "")[:10] for r in rows}
    period_kinds_by_posted: dict[str, set[str]] = {}
    for r in rows:
        d = (r.get("posted_at") or "")[:10]
        period_kinds_by_posted.setdefault(d, set()).add(r.get("period_kind") or "")

    missing: list[tuple[str, str]] = []
    for pub_date, kind in targets:
        # Did any alert post within ±2 days of pub_date matching kind?
        candidates = []
        for d, kinds in period_kinds_by_posted.items():
            if not d:
                continue
            try:
                ddt = datetime.fromisoformat(d)
                pdt = datetime.fromisoformat(pub_date)
            except Exception:
                continue
            if abs((ddt - pdt).days) > CYCLE_GAP_DAYS:
                continue
            wanted = kind if not kind.startswith("monthly") else "monthly"
            if wanted in kinds:
                candidates.append(d)
        if not candidates:
            missing.append((pub_date, kind))

    if not missing:
        log.info("cycle_gap: all expected publications accounted for")
        return

    marker_key = "cycle-gap-" + "-".join(d for d, _ in missing)
    if not _alert_once_per_window(marker_key, 24 * 3600):
        log.info("cycle_gap: alert already sent today, skipping")
        return

    msg_lines = ["⚠️ <b>BeOn 사이클 누락 감지</b>"]
    for pub_date, kind in missing:
        msg_lines.append(f"• {pub_date} 예정 {kind} — 미수신")
    msg_lines.append(
        f"\n허용 grace: {CYCLE_GAP_DAYS}일. 포워더 / BeOn 발표 지연 확인."
    )
    msg = "\n".join(msg_lines)
    log.warning("cycle gap: %s", msg.replace("\n", " | "))
    _notify(msg)


# 이 타이머 주기(1h)의 두 배 — 한 번 놓쳐도 다음 실행이 본다. 같은 누락이 두 번
# 보이는 것은 표식이 막는다.
DELIVERY_WINDOW_S = 2 * 3600
DELIVERY_ALERT_EVERY_S = 6 * 3600


def check_delivery_gap() -> None:
    """릴레이가 포워드한 만큼 trade-bot 이 받았나 — 못 받았으면 ⚠️ (실수 #406)."""
    from trade import bot_health as bh

    g = bh.delivery_check(since=f"{DELIVERY_WINDOW_S} seconds ago")
    if g["kind"] == "unknown":
        log.warning("delivery_gap: 판정 불가 — %s", g.get("err"))
        return
    if g["kind"] not in ("total", "partial"):
        log.info("delivery_gap: %s (sent=%s got=%s)", g["kind"], g.get("sent"), g.get("got"))
        return
    if not _alert_once_per_window("delivery-gap", DELIVERY_ALERT_EVERY_S):
        log.info("delivery_gap: alert already sent within %ds, skipping",
                 DELIVERY_ALERT_EVERY_S)
        return
    msg = bh.gap_alert_text(g)
    log.warning("delivery gap: %s", msg.replace("\n", " | "))
    _notify(msg)


def main() -> int:
    # 한 신호의 실패가 다른 신호를 지우지 않게 둘 다 돌리되, 조용하지는 않게 —
    # 트레이스백을 남기고 rc 1 로 끝내 유닛 실패로 보이게 한다(#12).
    rc = 0
    for check in (check_cycle_gap, check_delivery_gap):
        try:
            check()
        except Exception:                                      # noqa: BLE001
            log.exception("%s failed", check.__name__)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
