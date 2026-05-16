"""Periodic health checks for the trade-bot pipeline.

Runs from systemd timer (trade-bot-health.timer) hourly. Surfaces two
classes of silent-failure that the dashboard alone wouldn't catch:

  - Dormancy: no new BeOn forward has arrived in the last N hours
    (default 72) → likely the upstream userbot died or BeOn paused.
    Posts a ⚠️ Telegram alert to the trade channel once per dormancy
    window (de-duplicated via a marker file).

  - Cycle gap: today's KST date is past an expected BeOn publication
    date but no alert for that period has landed yet (e.g. it's
    5월 23일 KST, BeOn should have published 5월 1-20일 잠정 around
    5월 21일, but no alert in inbox.jsonl carries period_end >= 5/20
    posted within 2 days of 5/21). Posts a ⚠️ alert once.

Both alerts are best-effort — missing TRADE_BOT_TOKEN /
TRADE_CHANNEL_CHAT_IDS silently skips so the script also works in
dev environments without notifications configured.

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

DORMANCY_HOURS = int(os.environ.get("TRADE_DORMANCY_HOURS") or "72")
CYCLE_GAP_DAYS = int(os.environ.get("TRADE_CYCLE_GAP_DAYS") or "2")


def _notify(text: str) -> None:
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        log.info("notify skipped: no TRADE_BOT_TOKEN / TRADE_CHANNEL_CHAT_IDS")
        return
    chat_id = chat_ids.split(",")[0].strip()
    try:
        subprocess.run(
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


def _last_post_dt() -> datetime | None:
    if not STORE_PATH.exists():
        return None
    conn = open_db(STORE_PATH)
    try:
        row = conn.execute(
            "SELECT MAX(posted_at) FROM alerts"
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    raw = row[0]
    # posted_at is stored as UTC ISO or KST ISO depending on source;
    # treat naive strings as UTC and add 9h offset for KST comparison.
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1]).replace(tzinfo=timezone.utc)
        if "+" in raw or raw.count("-") > 2:
            return datetime.fromisoformat(raw)
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def check_dormancy() -> None:
    """⚠️ if no new BeOn forward in the last DORMANCY_HOURS."""
    last = _last_post_dt()
    if last is None:
        log.info("dormancy: store empty, skipping")
        return
    now = datetime.now(timezone.utc)
    age_hours = (now - last).total_seconds() / 3600
    if age_hours < DORMANCY_HOURS:
        log.info("dormancy: last post %.1fh ago, ok", age_hours)
        return
    if not _alert_once_per_window("dormancy", DORMANCY_HOURS * 3600):
        log.info("dormancy: alert already sent in this window, skipping")
        return
    msg = (
        f"⚠️ <b>BeOn dormancy 감지</b>\n"
        f"마지막 게시: {last.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"경과: {age_hours:.1f}시간 (임계 {DORMANCY_HOURS}h)\n"
        f"포워더 / BeOn 채널 상태 점검 필요."
    )
    log.warning("dormancy detected: %s", msg.replace("\n", " "))
    _notify(msg)


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


def main() -> int:
    check_dormancy()
    check_cycle_gap()
    return 0


if __name__ == "__main__":
    sys.exit(main())
