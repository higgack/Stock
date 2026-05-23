"""Telethon sync: forward BeOn_BeClear posts into the private trade
channel so trade-bot can ingest them like live forwards.

Works both as a one-off catch-up and as a periodic sync driven by
trade-bot-beon-sync.timer (fires on BeOn publication days: 1/11/15/21
of each month at 12:00 KST). Idempotent — scans inbox.jsonl and skips
any BeOn message_id that's already been ingested, so re-running is safe
(picks up where it left off after a FloodWait abort, disk-low pause,
or accidental Ctrl+C).

Safety features (tuned from the first 3683-message run):
  - Adaptive pacing: starts at 1.5 s/unit and grows by 0.1 s every 500
    messages, capped at 3.0 s. Avoids the giant 40-minute FloodWait we
    hit near the end of run #1 by tapering before Telegram throttles.
  - FloodWait hard cap: if Telegram asks for > 600 s wait, exit
    gracefully and notify, instead of sleeping for an hour inside the
    script. User reruns next day.
  - Disk guard: every 20 units, check free space on /. If below
    TRADE_MIN_FREE_GB (default 2.0), pause and notify, poll every 60 s
    for free space to recover (with a small hysteresis buffer), then
    notify resume. Manual cleanup helper:
        bash trade/scripts/free_disk.sh
  - Telegram notifications: pause / resume / abort all push to the
    same channel as trade-bot deploys (TRADE_BOT_TOKEN +
    TRADE_CHANNEL_CHAT_IDS), so the operator sees what's happening
    without watching the terminal.

Setup (one-time):
  cd ~/stock-trade
  python -m venv .backfill-venv
  .backfill-venv/bin/pip install -r trade/scripts/requirements.txt
  # add TRADE_TELETHON_API_ID + TRADE_TELETHON_API_HASH to .env
  # (issue them at https://my.telegram.org/apps — keep them in .env only)

Run manually:
  .backfill-venv/bin/python trade/scripts/backfill_beon.py --since 2026-05-01
  .backfill-venv/bin/python trade/scripts/backfill_beon.py  # default: 40-day lookback
  # optional: --to 2026-05-16, --dry-run, --lookback-days N

Run by systemd (trade-bot-beon-sync.timer):
  Invoked without --since; defaults to 40-day lookback which covers
  the widest gap between BeOn publication dates plus ample buffer.

After a one-off backfill you can remove the temp venv (the periodic
service reuses it, so keep it if beon-sync.timer is active):
  rm -rf .backfill-venv .backfill-session*
"""

import argparse
import asyncio
import html
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon import utils as _tutils
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("backfill")

API_ID = int(os.environ["TRADE_TELETHON_API_ID"])
API_HASH = os.environ["TRADE_TELETHON_API_HASH"]

_dest_raw = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
DEST_IDS = [int(x) for x in _dest_raw.split(",") if x.strip()]
if len(DEST_IDS) != 1:
    raise SystemExit(
        "backfill: TRADE_CHANNEL_CHAT_IDS must be a single chat ID for backfill"
    )
DEST_ID = DEST_IDS[0]

INBOX_DIR = Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))
INBOX_PATH = INBOX_DIR / "inbox.jsonl"

SOURCE_USERNAME = "BeOn_BeClear"
SESSION_PATH = ".backfill-session"  # cwd-relative; .gitignored

# --- Adaptive pacing -------------------------------------------------
# Run #1 hit a 2465 s FloodWait at message 3377/3683 with a flat 1 s
# pace. Tapering before throttle kicks in trades total runtime for a
# better chance of finishing without hitting the wall. Defaults yield
# 1.5 s → 3.0 s across a typical 3-week backfill (~6000 msgs).
PAUSE_BASE_S = float(os.environ.get("TRADE_PAUSE_BASE_S") or "1.5")
PAUSE_INCREMENT_S = float(os.environ.get("TRADE_PAUSE_INCREMENT_S") or "0.1")
PAUSE_INCREMENT_EVERY = int(os.environ.get("TRADE_PAUSE_INCREMENT_EVERY") or "500")
PAUSE_MAX_S = float(os.environ.get("TRADE_PAUSE_MAX_S") or "3.0")

# Telegram occasionally hands back a multi-hour FloodWait when an
# account has been forwarding heavily. Sleeping in-process for that
# long ties up tmux/SSH and risks the operator force-killing mid-wait.
# Exit gracefully past this threshold; rerun next day picks up where
# we left off (idempotent).
MAX_FLOOD_WAIT_S = int(os.environ.get("TRADE_MAX_FLOOD_WAIT_S") or "600")

# --- Candidate cap (anti-flood) --------------------------------------
# Listener handles realtime forwarding; this script is the safety net
# for short downtime windows. If iter_messages returns more candidates
# than the cap, abort with ⚠️ notify instead of silently flooding the
# dest channel. Operator overrides with --max-candidates for explicit
# wide catch-ups they actually want.
MAX_CANDIDATES_DEFAULT = int(os.environ.get("TRADE_MAX_CANDIDATES") or "200")

# --- Disk guard -------------------------------------------------------
MIN_FREE_GB = float(os.environ.get("TRADE_MIN_FREE_GB") or "2.0")
DISK_RESUME_BUFFER_GB = float(
    os.environ.get("TRADE_DISK_RESUME_BUFFER_GB") or "0.5"
)
DISK_CHECK_EVERY_UNITS = int(os.environ.get("TRADE_DISK_CHECK_EVERY") or "20")
DISK_POLL_SECONDS = 60


class BackfillAborted(Exception):
    """Raised when the script should exit gracefully so the operator
    can resume later. Always paired with a Telegram notify."""


# ---------------------------------------------------------------------
# Notification helper (best-effort; never raises into the main loop)
# ---------------------------------------------------------------------
def _notify(text: str) -> None:
    """Push a short HTML message to the trade channel via the live
    trade-bot's credentials. Silent if creds are absent so the script
    still works without notifications configured.
    """
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        return
    chat_id = chat_ids.split(",")[0].strip()
    if not chat_id:
        return
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


# ---------------------------------------------------------------------
# Disk guard
# ---------------------------------------------------------------------
def _disk_free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


async def _maybe_pause_for_disk(forwarded: int, total: int) -> None:
    free = _disk_free_gb()
    if free >= MIN_FREE_GB:
        return

    log.warning(
        "disk free %.1fGB < %.1fGB threshold — pausing", free, MIN_FREE_GB
    )
    _notify(
        f"⏸ <b>백필 일시정지</b>\n"
        f"디스크 잔여: {free:.1f}GB (임계점 {MIN_FREE_GB:.1f}GB)\n"
        f"진행: {forwarded}/{total} msgs\n"
        f"정리하면 자동 재개: <code>bash trade/scripts/free_disk.sh</code>"
    )
    resume_threshold = MIN_FREE_GB + DISK_RESUME_BUFFER_GB
    while True:
        await asyncio.sleep(DISK_POLL_SECONDS)
        free = _disk_free_gb()
        log.info(
            "paused: disk free %.1fGB (need ≥ %.1fGB to resume)",
            free, resume_threshold,
        )
        if free >= resume_threshold:
            break
    _notify(
        f"▶ <b>백필 재개</b>\n"
        f"디스크 잔여: {free:.1f}GB\n"
        f"진행: {forwarded}/{total} msgs 부터 이어서"
    )
    log.info("resuming: disk free %.1fGB", free)


# ---------------------------------------------------------------------
# Pace
# ---------------------------------------------------------------------
def _current_pause(forwarded: int) -> float:
    bumps = forwarded // PAUSE_INCREMENT_EVERY
    return min(PAUSE_BASE_S + bumps * PAUSE_INCREMENT_S, PAUSE_MAX_S)


# ---------------------------------------------------------------------
# inbox.jsonl scan
# ---------------------------------------------------------------------
def _load_existing_keys() -> set[tuple[int, int]]:
    """Collect (chat_id, msg_id) pairs of every forward already
    represented in inbox.jsonl, regardless of whether BeOn originated
    the post or relayed it from another channel.

    Telegram preserves the ORIGINAL forward chain when a message is
    re-forwarded, so a BeOn relay of, say, AWAKE 플러스 lands in
    trade-bot's inbox.jsonl with `forward_origin_chat_id` = AWAKE's
    chat id (not BeOn's). The old version of this scanner filtered on
    `forward_origin_chat_username == "BeOn_BeClear"` and silently
    dropped every relayed entry — which made every 2-hour backfill
    tick re-forward those messages forever.

    Returning a tuple-set keyed on (chat_id, msg_id) lets the iter
    side compare against the SAME identity it has access to:
        - BeOn-originated (msg.fwd_from is None) → (source.id, msg.id)
        - BeOn-relayed    (msg.fwd_from is set)  → (orig.chat, orig.msg_id)
    Both paths now dedup correctly.

    chat_id is the Bot-API 'marked' form (-100… for channels) — that's
    what trade-bot writes, and what `telethon.utils.get_peer_id` returns
    on the iter side.
    """
    if not INBOX_PATH.exists():
        return set()
    keys: set[tuple[int, int]] = set()
    with INBOX_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("forward_origin_chat_id")
            mid = rec.get("forward_origin_message_id")
            if cid is None or mid is None:
                continue
            try:
                keys.add((int(cid), int(mid)))
            except (TypeError, ValueError):
                continue
    return keys


def _msg_key(msg: Message, source_chat_id: int) -> tuple[int, int]:
    """Return (chat_id, msg_id) identity for dedup vs. inbox.jsonl.

    Mirrors what trade-bot would record for the SAME message when
    Telegram forwards it to the trade channel:
      - msg.fwd_from set → original sender's (chat_id, channel_post)
        because Telegram preserves the head of the forward chain.
      - msg.fwd_from None → BeOn's own (source_chat_id, msg.id) since
        BeOn is the originator.

    Falls back to (source_chat_id, msg.id) when fwd_from is present
    but the chat or post-id can't be extracted (hidden user forwards,
    legacy chat peers without channel_post, etc.). That fallback may
    miss true dedup in rare hidden-source cases but is strictly safer
    than skipping a real new BeOn-originated post.
    """
    fwd = getattr(msg, "fwd_from", None)
    if fwd is not None:
        from_peer = getattr(fwd, "from_id", None)
        channel_post = getattr(fwd, "channel_post", None)
        if from_peer is not None and channel_post is not None:
            try:
                return (_tutils.get_peer_id(from_peer), int(channel_post))
            except (TypeError, ValueError):
                pass
    return (source_chat_id, msg.id)


def _group_by_album(messages: list[Message]) -> list[list[Message]]:
    """Walk chronological messages and return a list of send-units:
    each unit is one standalone message OR one full album (same
    grouped_id, contiguous in time). Forwarding an album as a list
    preserves its album-ness in the destination, which the trade-bot's
    media_group_id grouping relies on.
    """
    units: list[list[Message]] = []
    pending: list[Message] = []
    pending_gid: int | None = None
    for m in messages:
        gid = getattr(m, "grouped_id", None)
        if gid is None:
            if pending:
                units.append(pending)
                pending = []
                pending_gid = None
            units.append([m])
        elif gid == pending_gid:
            pending.append(m)
        else:
            if pending:
                units.append(pending)
            pending = [m]
            pending_gid = gid
    if pending:
        units.append(pending)
    return units


async def _forward_unit(client, source, unit: list[Message], dest) -> bool:
    """Forward one send-unit (single message or album) with FloodWait
    retry. Raises BackfillAborted if Telegram demands a wait longer
    than MAX_FLOOD_WAIT_S so the caller can exit gracefully.
    """
    msg_ids = [m.id for m in unit]
    delay = 0
    for attempt in range(5):
        if delay > 0:
            log.info("flood wait %ds before retry %d", delay, attempt + 1)
            await asyncio.sleep(delay)
        try:
            await client.forward_messages(dest, msg_ids, from_peer=source)
            return True
        except FloodWaitError as e:
            if e.seconds > MAX_FLOOD_WAIT_S:
                raise BackfillAborted(
                    f"Telegram FloodWait {e.seconds}s exceeds "
                    f"{MAX_FLOOD_WAIT_S}s threshold"
                )
            delay = e.seconds + 1
    log.error("giving up on msgs=%s after 5 attempts", msg_ids)
    return False


async def run(
    since: datetime,
    until: datetime | None,
    dry_run: bool,
    max_candidates: int,
) -> int:
    existing = _load_existing_keys()
    log.info("already ingested: %d forward keys", len(existing))

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()
    log.info("Telethon session ready")
    try:
        source = await client.get_entity(SOURCE_USERNAME)
        dest = await client.get_entity(DEST_ID)
        # Marked form (-100… for channels) so it matches what trade-bot
        # records via the Bot API for forward_origin_chat_id.
        source_chat_id = _tutils.get_peer_id(source)
        log.info(
            "source=%s (marked=%s) dest=%s",
            source.id, source_chat_id, dest.id,
        )

        # reverse=True means iterate oldest-first starting from
        # offset_date. Caps with our own date check on `until`.
        candidates: list[Message] = []
        skipped_existing = 0
        async for msg in client.iter_messages(
            source, offset_date=since, reverse=True
        ):
            if until is not None and msg.date > until:
                break
            if _msg_key(msg, source_chat_id) in existing:
                skipped_existing += 1
                continue
            candidates.append(msg)

        until_label = (until or datetime.now(timezone.utc)).date().isoformat()
        log.info(
            "range %s → %s — candidates=%d skipped_existing=%d",
            since.date().isoformat(),
            until_label,
            len(candidates),
            skipped_existing,
        )

        if len(candidates) > max_candidates:
            log.error(
                "candidates %d > max_candidates %d — aborting to prevent flood",
                len(candidates), max_candidates,
            )
            _notify(
                f"⚠️ <b>BeOn 동기화 중단 (안전장치)</b>\n"
                f"후보 {len(candidates)}개가 cap {max_candidates}개 초과.\n"
                f"의도된 wide 백필이면 명시 실행:\n"
                f"<code>--since YYYY-MM-DD --max-candidates {len(candidates)+100}</code>"
            )
            return 2

        units = _group_by_album(candidates)
        log.info(
            "grouped into %d send units (singles + albums)", len(units)
        )

        if dry_run:
            log.info("dry-run: not forwarding")
            return 0

        forwarded_msgs = 0
        total_msgs = len(candidates)
        try:
            for i, unit in enumerate(units, 1):
                if i == 1 or i % DISK_CHECK_EVERY_UNITS == 0:
                    await _maybe_pause_for_disk(forwarded_msgs, total_msgs)

                ok = await _forward_unit(client, source, unit, dest)
                if ok:
                    forwarded_msgs += len(unit)
                if i % 20 == 0:
                    pace = _current_pause(forwarded_msgs)
                    log.info(
                        "progress: %d/%d units (%d msgs, pace %.1fs)",
                        i, len(units), forwarded_msgs, pace,
                    )
                await asyncio.sleep(_current_pause(forwarded_msgs))
        except BackfillAborted as exc:
            log.error("aborted: %s", exc)
            _notify(
                f"❌ <b>백필 중단</b>\n"
                f"사유: {html.escape(str(exc))}\n"
                f"진행: {forwarded_msgs}/{total_msgs} msgs\n"
                f"같은 명령으로 재실행하면 이어서 진행 (idempotent)."
            )
            return 1

        log.info(
            "done: forwarded %d of %d candidate messages",
            forwarded_msgs,
            total_msgs,
        )
        if forwarded_msgs > 0:
            _notify(
                f"✅ <b>BeOn 동기화 완료</b>\n"
                f"신규 forwarded: {forwarded_msgs}/{total_msgs} msgs\n"
                f"units: {len(units)}"
            )
        elif len(candidates) == 0 and skipped_existing == 0:
            # 40-day window produced no messages at all — unexpected for
            # an active channel; suggests session/connectivity issue.
            _notify(
                "⚠️ <b>BeOn 동기화 — 후보 0건</b>\n"
                f"lookback {since.date().isoformat()} ~ 오늘 범위에서 메시지 없음.\n"
                "Telethon 세션 만료 또는 채널 접근 불가 확인."
            )
        else:
            log.info("sync: nothing new (all %d messages already in inbox)", skipped_existing)
        return 0
    finally:
        await client.disconnect()


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill BeOn_BeClear → private trade channel via Telethon."
    )
    ap.add_argument(
        "--since",
        default=None,
        help="YYYY-MM-DD (UTC), inclusive lower bound. Default: today minus --lookback-days.",
    )
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=2,
        metavar="N",
        help=(
            "Days to look back when --since is omitted (default: 2). "
            "Listener handles realtime; this is the safety net for short "
            "downtime windows. For wider historical catch-ups use "
            "explicit --since."
        ),
    )
    ap.add_argument(
        "--to", help="YYYY-MM-DD (UTC), inclusive upper bound. Default: now."
    )
    ap.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Abort with ⚠️ notify if candidates exceed N (default: "
            f"TRADE_MAX_CANDIDATES env = {MAX_CANDIDATES_DEFAULT}). "
            f"Set high for intentional wide catch-ups."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="enumerate candidates without forwarding",
    )
    args = ap.parse_args()
    max_candidates = (
        args.max_candidates
        if args.max_candidates is not None
        else MAX_CANDIDATES_DEFAULT
    )

    if args.since:
        since_date = _parse_date(args.since)
    else:
        since_date = datetime.now(timezone.utc) - timedelta(
            days=args.lookback_days
        )
        log.info(
            "--since not given; using %d-day lookback → %s",
            args.lookback_days,
            since_date.date().isoformat(),
        )

    rc = asyncio.run(
        run(
            since_date,
            _parse_date(args.to) if args.to else None,
            args.dry_run,
            max_candidates,
        )
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
