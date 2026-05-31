"""관세청 수출 급변 알림 — 핀 품목의 새 달 전월비가 ±임계% 넘으면 운영자 DM.

Runs right after fetch_customs (same systemd service, 2nd ExecStart) so it
evaluates the months that fetch just cached. This is the ONLY Telegram
output of the whole customs feature — and it is heavily gated:

  - Operator DM only (never the channel). Target chat from trade.operator
    (auto-captured when the operator uses /hs, /customs, …). No operator
    chat recorded yet → skip silently.
  - Baseline-silent PER pin: a pin with no 'seen' history yet (first ever
    run, or a freshly-added pin) has ALL its cached months marked seen
    WITHOUT alerting. So enabling the feature on 12 months of backfill, or
    pinning a new item, never blasts historical months.
  - Only genuinely new months (not seen before) are evaluated.
  - Hard cap (TRADE_CUSTOMS_ALERT_MAX, default 10): ≤cap candidates →
    one DM listing all biggest-swing-first; >cap → one DM with the top 3
    plus '외 N건 — /customs'. Either way, exactly one message.
  - Marks months 'seen' only AFTER a successful send (or when there's
    nothing to alert), so a transient send failure retries next tick
    instead of losing the alert.

Threshold (TRADE_CUSTOMS_ALERT_PCT, default 30) is on the export-value
month-over-month change — the stock-relevant signal (Korean exporters).

Schedule: trade-bot-customs-fetch.service (daily, after fetch).
Run by hand:  .venv/bin/python -m trade.scripts.customs_alert
"""

import html
import logging
import os
import subprocess
import sqlite3
import sys

from dotenv import load_dotenv

from trade import customs, hs_map, operator

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("customs-alert")

THRESHOLD_PCT = float(os.environ.get("TRADE_CUSTOMS_ALERT_PCT") or "30")
CAP = int(os.environ.get("TRADE_CUSTOMS_ALERT_MAX") or "10")
TEASER = 3  # how many biggest movers to show when over cap

_SEEN_SQL = """
CREATE TABLE IF NOT EXISTS customs_alert_seen (
  hs_code TEXT NOT NULL,
  year_month TEXT NOT NULL,
  seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (hs_code, year_month)
);
"""


def _ensure_seen(conn: sqlite3.Connection) -> None:
    conn.executescript(_SEEN_SQL)


def _seen_set(conn: sqlite3.Connection, hs_code: str) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT year_month FROM customs_alert_seen WHERE hs_code = ?",
            (hs_code,),
        )
    }


def _mark(conn: sqlite3.Connection, hs_code: str, ym: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO customs_alert_seen (hs_code, year_month) "
        "VALUES (?, ?)",
        (hs_code, ym),
    )


def _send(chat_id: int, text: str) -> bool:
    """DM the operator. Returns True only when Telegram confirms ok:true,
    so a failed send doesn't get marked seen (it retries next tick).
    Missing bot token → False (no creds, can't send)."""
    token = os.environ.get("TRADE_BOT_TOKEN")
    if not token:
        log.warning("no TRADE_BOT_TOKEN — cannot send")
        return False
    try:
        out = subprocess.run(
            [
                "curl", "-s", "-m", "10",
                "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "--data-urlencode", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}",
                "--data-urlencode", "parse_mode=HTML",
            ],
            timeout=15, check=False, capture_output=True, text=True,
        )
        return '"ok":true' in (out.stdout or "")
    except Exception as e:
        log.warning("send failed: %s", e)
        return False


def _format(candidates: list[dict]) -> str:
    """One DM body. candidates already sorted biggest-|pct|-first."""
    n = len(candidates)
    thr = f"{THRESHOLD_PCT:.0f}"

    def _line(c: dict) -> str:
        return (
            f"• <b>{html.escape(c['item'])}</b> {c['pct']:+.1f}% — "
            f"수출 {customs.fmt_usd(c['prev'])}→{customs.fmt_usd(c['curr'])} "
            f"({c['year_month']})"
        )

    if n <= CAP:
        lines = [f"⚠️ <b>관세청 수출 급변</b> (전월비 ±{thr}% 초과)", ""]
        lines += [_line(c) for c in candidates]
    else:
        lines = [f"⚠️ <b>관세청 수출 급변 {n}건</b> (cap {CAP} 초과)", "", "변동 상위:"]
        lines += [_line(c) for c in candidates[:TEASER]]
        lines.append(f"… 외 {n - TEASER}건 — <code>/customs</code> 로 전체 확인")
    return "\n".join(lines)


def run(db_path=None, notify=None) -> int:
    notify = notify or _send
    pins = hs_map.entries()
    if not pins:
        log.info("no HS pins — nothing to evaluate")
        return 0

    db = db_path if db_path is not None else customs.DEFAULT_DB
    conn = customs.open_db(db)
    try:
        _ensure_seen(conn)
        candidates: list[dict] = []
        to_mark: list[tuple[str, str]] = []  # marked only after a clean send
        baselined = 0

        for item, hs in pins:
            series = customs.get_series(conn, hs)
            if not series:
                continue
            seen = _seen_set(conn, hs)
            if not seen:
                # Baseline this pin: record every cached month, alert none.
                for r in series:
                    _mark(conn, hs, r["year_month"])
                baselined += 1
                continue
            for i, r in enumerate(series):
                ym = r["year_month"]
                if ym in seen:
                    continue
                to_mark.append((hs, ym))           # new month
                if i == 0:
                    continue                        # no prior month to compare
                prev = series[i - 1]["exp_dlr"] or 0
                curr = r["exp_dlr"] or 0
                if prev == 0:
                    continue
                pct = (curr - prev) / prev * 100.0
                if abs(pct) > THRESHOLD_PCT:
                    candidates.append({
                        "item": item, "hs_code": hs, "year_month": ym,
                        "pct": pct, "prev": prev, "curr": curr,
                    })

        candidates.sort(key=lambda c: abs(c["pct"]), reverse=True)

        sent_ok = True
        if candidates:
            chat = operator.get()
            if not chat:
                log.warning(
                    "%d candidate(s) but no operator chat recorded — "
                    "skipping send, will retry next tick (use a DM command "
                    "or set TRADE_OPERATOR_CHAT_ID)", len(candidates),
                )
                sent_ok = False
            else:
                sent_ok = notify(chat, _format(candidates))
                log.info(
                    "sent=%s to operator chat (%d candidate(s))",
                    sent_ok, len(candidates),
                )

        # Mark new months seen only when nothing needs retrying.
        if not candidates or sent_ok:
            for hs, ym in to_mark:
                _mark(conn, hs, ym)

        log.info(
            "done: pins=%d baselined=%d new_months=%d candidates=%d sent_ok=%s",
            len(pins), baselined, len(to_mark), len(candidates), sent_ok,
        )
        return 0
    finally:
        conn.close()


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
