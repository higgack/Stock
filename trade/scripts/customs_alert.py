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
HS 개정 판정만 (읽기 전용, 보내지 않음):  … customs_alert --hs-check
"""

import html
import json
import logging
import os
import re
import subprocess
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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

# HS reference freshness — the operator asked 'how do I even know the
# data changed?'. The judge is the REVISION, not the file's age: the
# portal page names its latest file 관세청_HS부호_YYYYMMDD (the Telegram
# preview of this very alert showed '관세청_HS부호_20260101', 2026-11-27).
# An age threshold (180 days) fired on a file downloaded in May that
# already WAS the 2026-01-01 revision — and would have kept firing every
# 30 days until the next one. 30-day re-alert lockout stays.
HS_REF_REALERT_DAYS = int(os.environ.get("TRADE_HS_REF_REALERT_DAYS") or "30")
HS_PORTAL_URL = "https://www.data.go.kr/data/15049722/fileData.do"
_HS_REV_RE = re.compile(r"관세청_HS부호_(\d{8})")
_KST = timezone(timedelta(hours=9))

_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
_HS_REF_PATHS = (_DATA_DIR / "hs_codes.xlsx", _DATA_DIR / "hs_codes.csv")
_HS_REF_MARKER = _DATA_DIR / ".hs_ref_alert_seen"
# Which revision the host file holds, recorded when we see the file change
# (the content doesn't reliably say — unmeasured — so we attribute it).
_HS_REF_STATE = _DATA_DIR / ".hs_ref_revision.json"

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


def _kst_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, _KST).date()


def portal_revision(timeout: float = 15) -> tuple[date | None, str]:
    """Latest revision the portal page names → (date, '') or (None, reason).

    Two failure branches are told apart (#82): couldn't reach the page vs.
    the page no longer carries '관세청_HS부호_YYYYMMDD' (layout change)."""
    try:
        req = urllib.request.Request(
            HS_PORTAL_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(3_000_000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — reason goes to the caller
        return None, f"포털 접속 실패({type(e).__name__})"
    dates = []
    for m in _HS_REV_RE.finditer(body):
        try:
            dates.append(datetime.strptime(m.group(1), "%Y%m%d").date())
        except ValueError:
            continue
    if not dates:
        return None, "포털 페이지에 '관세청_HS부호_날짜' 표기가 없음(구조 변경?)"
    return max(dates), ""


def _file_revision(ref: Path, portal_rev: date | None, *,
                   record: bool) -> date | None:
    """Revision the host file holds, as far as we can know — None = older
    than the portal's (or not attributable right now).

    The content doesn't reliably carry it, so we attribute: a file that
    CHANGED since our last record was downloaded from the portal, i.e. it
    is the portal's latest at that moment (we look 4×/day). A file we
    have never recorded (first run) counts as that revision only when it
    was saved on/after the revision date — so a December download of a
    pre-published January revision isn't misread as old after the first
    run. `record=False` (the read-only check) never writes the state."""
    st = ref.stat()
    key = st.st_mtime_ns
    try:
        rec = json.loads(_HS_REF_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        rec = None
    if rec is not None and rec.get("key") == key:
        try:
            return date.fromisoformat(rec["revision"])
        except (KeyError, TypeError, ValueError):
            rec = None
    if portal_rev is None:
        return None
    if rec is None and _kst_date(st.st_mtime) < portal_rev:
        return None        # first sight, saved before the revision → older
    if record:
        try:
            _HS_REF_STATE.parent.mkdir(parents=True, exist_ok=True)
            _HS_REF_STATE.write_text(json.dumps(
                {"key": key, "revision": portal_rev.isoformat()}),
                encoding="utf-8")
        except OSError as e:
            log.warning("HS revision record not written: %s", e)
    return portal_rev


def hs_reference_status(now: float | None = None, *,
                        record: bool = True) -> dict | None:
    """Judge the host HS reference against the latest revision. None when
    no file. `held` is the one sentence both the alert and --hs-check
    print, so the two can't say different things (#38)."""
    now = now if now is not None else time.time()
    ref = next((p for p in _HS_REF_PATHS if p.exists()), None)
    if ref is None:
        return None  # no file → /hs search already shows a download hint
    p_rev, p_err = portal_revision()
    f_rev = _file_revision(ref, p_rev, record=record)
    saved = _kst_date(ref.stat().st_mtime)
    if p_rev is not None:
        expected, basis = p_rev, "포털 최신판"
        stale = f_rev is None or f_rev < p_rev
    else:
        # Portal unreadable — fall back to the calendar (annual Jan 1
        # revision) and say so; the message names the reason.
        expected = date(_kst_date(now).year, 1, 1)
        basis = f"달력 기준(1월 1일 개정) — {p_err}"
        stale = (f_rev or saved) < expected
    if f_rev is not None:
        held = f"{f_rev:%Y-%m-%d} 판"
    elif stale:
        held = "그보다 앞선 판"
    else:   # calendar fallback, never attributed: judged by the save date
        held = "판 기록 없음 — 저장일이 개정일 뒤라 최신판으로 봄"
    return {"file": ref.name, "saved": saved, "file_rev": f_rev,
            "expected": expected, "basis": basis, "stale": stale,
            "held": held}


def _check_hs_reference_freshness(now: float | None = None) -> str | None:
    """Alert body when the host file holds an older revision than the
    latest — None when current, missing, or re-alerted within
    HS_REF_REALERT_DAYS. The marker is touched by the caller only after
    a successful send (same timing as the ±30% path)."""
    now = now if now is not None else time.time()
    st = hs_reference_status(now)
    if st is None:
        return None
    log.info("HS reference: file=%s saved=%s rev=%s expected=%s (%s) stale=%s",
             st["file"], st["saved"], st["file_rev"], st["expected"],
             st["basis"], st["stale"])
    if not st["stale"]:
        return None
    if _HS_REF_MARKER.exists():
        since = (now - _HS_REF_MARKER.stat().st_mtime) / 86400.0
        if since < HS_REF_REALERT_DAYS:
            return None
    return (
        "📅 <b>관세청 HS부호 개정 확인</b>\n"
        f"{html.escape(st['basis'])}: <b>{st['expected']:%Y-%m-%d}</b> 개정\n"
        f"호스트 파일(<code>{html.escape(st['file'])}</code>): "
        f"{st['saved']:%Y-%m-%d} 저장 → {st['held']}\n"
        "새 파일을 받아 같은 경로에 덮어쓰면 다음 검색부터 자동 반영.\n"
        f"<a href=\"{HS_PORTAL_URL}\">dataset 15049722</a>"
    )


def _touch_hs_marker() -> None:
    try:
        _HS_REF_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _HS_REF_MARKER.touch()
    except OSError:
        pass


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


def _u16(s: str) -> int:
    """UTF-16 code-unit 길이 (텔레그램 4096 한도 기준 — 한글 1·이모지 2)."""
    return len(s.encode("utf-16-le")) // 2


def split_telegram(text: str, limit: int = 4000) -> list[str]:
    """긴 텍스트를 줄 경계로 ≤limit(UTF-16) 청크들로 분할 (사용자 2026-06-18 '전체
    내용 다 보내기'). 보고서 줄은 <b>…</b> 가 한 줄 안에서 닫히므로 줄 경계 분할이
    HTML-안전. 한 줄이 limit 초과면 하드 분할. 순수 함수 — 단위테스트."""
    out: list[str] = []
    cur = ""
    for ln in (text or "").split("\n"):
        while _u16(ln) > limit:                 # 단일 초장문 줄 하드 분할
            lo, hi = 1, len(ln)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _u16(ln[:mid]) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            if cur:
                out.append(cur)
                cur = ""
            out.append(ln[:lo])
            ln = ln[lo:]
        cand = (cur + "\n" + ln) if cur else ln
        if _u16(cand) <= limit:
            cur = cand
        else:
            if cur:
                out.append(cur)
            cur = ln
    if cur:
        out.append(cur)
    return out or [""]


def send_long(chat_id: int, text: str) -> int:
    """긴 텍스트를 텔레그램 한도(4096) 내 여러 메시지로 분할 전송. 보낸 메시지 수
    반환(0 = 전부 실패/토큰 없음). 청크 일부 실패해도 나머지는 계속."""
    sent = 0
    for chunk in split_telegram(text):
        if _send(chat_id, chunk):
            sent += 1
    return sent


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

    # HS reference freshness check — independent of the ±30% path and
    # of whether the operator has pinned anything yet. Marker is updated
    # only on a successful send so a notify outage retries next tick.
    fresh_msg = _check_hs_reference_freshness()
    if fresh_msg:
        chat = operator.get()
        if chat and notify(chat, fresh_msg):
            _touch_hs_marker()
            log.info("HS reference staleness alert sent")
        else:
            log.info(
                "HS reference stale but operator chat missing or send "
                "failed — will retry next tick"
            )

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


def hs_check() -> int:
    """`--hs-check`: read-only verdict of the HS reference reminder — no
    send, no marker, no revision record (#264). Prints a line on every
    branch, including the healthy one (#274)."""
    st = hs_reference_status(record=False)
    if st is None:
        print(f"❓ HS부호 참조 파일 없음 ({_HS_REF_PATHS[0]}) — 알림 대상 아님")
        return 0
    print(f"HS부호 참조 파일: {st['file']} ({st['saved']:%Y-%m-%d} 저장)")
    print(f"{st['basis']}: {st['expected']:%Y-%m-%d} 개정")
    print(f"파일이 가진 판(판단): {st['held']}")
    if not st["stale"]:
        print("✅ 최신판 — 개정 알림 안 보냄")
        return 0
    lock = ""
    if _HS_REF_MARKER.exists():
        since = (time.time() - _HS_REF_MARKER.stat().st_mtime) / 86400.0
        if since < HS_REF_REALERT_DAYS:
            lock = f" (직전 알림 {since:.0f}일 전 — {HS_REF_REALERT_DAYS}일 안엔 다시 안 보냄)"
    print(f"⚠️ 새 판을 받아 덮어쓸 것 — {HS_PORTAL_URL}{lock}")
    return 1


def main() -> int:
    if "--hs-check" in sys.argv[1:]:
        return hs_check()
    return run()


if __name__ == "__main__":
    sys.exit(main())
