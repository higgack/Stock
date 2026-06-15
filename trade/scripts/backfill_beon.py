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

# 직접 실행(.backfill-venv/bin/python trade/scripts/backfill_beon.py)에서도 'trade'
# 패키지가 임포트되게 레포 루트를 path 에 추가 — 스크립트 직접 실행은 cwd 가 아닌
# 스크립트 디렉토리만 path 에 들어가 'from trade import' 가 ModuleNotFoundError 던짐
# (사용자 2026-06-15). -m 실행에는 무해(중복 무시).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trade import ignored as _ignored
from trade import beon_skip as _beon_skip

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
# 기본 cap 200→1000→3000 (사용자 2026-06-15 '확정 새 업데이트가 하루 천개·한번에
# 천개 이상도 가능'). 월별 확정 릴리스는 ~1000 품목 × (수출/수입 × 잠정/확정)이라
# 단일 동기화 run 의 신규 후보가 1000~2000 에 이를 수 있다 → 1000 cap 이면 정상
# 일배치를 통째 abort(아무것도 안 보냄)하던 것 해소. 포스팅은 멱등(이미 아카이브분
# 재전송 안 함)이고 실시간 안전장치(FloodWait hard cap + 디스크 가드 + adaptive
# pacing)가 진짜 홍수 방어선이라, 후보 cap 은 '비정상 대량 스캔'(수개월 다운타임
# 등)만 걸러내면 충분 → 5000 으로 정상 월배치+여러 달 백필엔 여유, 진짜 runaway 엔
# abort+notify (사용자 2026-06-15 '5천개까지 늘려 — 백필 가능하게').
# ⚠️ VM 의 beon-sync 유닛/.env 가 TRADE_MAX_CANDIDATES 로 오버라이드 중이면 그 값이
# 우선 — 이 기본값(5000)을 적용하려면 VM override 를 5000 이상으로 올리거나 제거해야 함
# (예전 854>400 abort 가 정확히 VM override 400 때문이었음).
MAX_CANDIDATES_DEFAULT = int(os.environ.get("TRADE_MAX_CANDIDATES") or "5000")

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
def _notify(text: str, buttons: list | None = None) -> None:
    """Push a short HTML message to the trade channel via the live
    trade-bot's credentials. Silent if creds are absent so the script
    still works without notifications configured.

    buttons: optional [(label, callback_data), ...] → 인라인 키보드. 봇 토큰
    으로 보내므로 인라인 버튼 부착 가능(콜백은 trade-bot 이 처리). 사용자가
    탭 한 번으로 반복 메시지를 차단하는 🚫 버튼용(사용자 2026-06-15).
    """
    import json as _json
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        return
    chat_id = chat_ids.split(",")[0].strip()
    if not chat_id:
        return
    cmd = [
        "curl", "-s", "-m", "10",
        "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "--data-urlencode", f"chat_id={chat_id}",
        "--data-urlencode", f"text={text}",
        "--data-urlencode", "parse_mode=HTML",
    ]
    if buttons:
        kb = {"inline_keyboard": [[{"text": t, "callback_data": cb}] for t, cb in buttons]}
        cmd += ["--data-urlencode", "reply_markup=" + _json.dumps(kb, ensure_ascii=False)]
    try:
        subprocess.run(cmd, timeout=15, check=False, capture_output=True)
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


# Origin labels for _msg_key_with_origin. NATIVE = the msg was posted
# by BeOn itself (no fwd_from). FORWARD = msg.fwd_from carries a clean
# (origin chat, origin post) pair so dedup is reliable. FALLBACK = the
# msg HAS fwd_from but channel_post / from_id is missing or unmappable
# (hidden user forwards, legacy chat peers without channel_post). The
# fallback path keys on (source_chat_id, msg.id), which is NOT what
# trade-bot's listener would have recorded for the same message — that
# means every backfill tick will see the message as 'new' and re-forward
# it. Rare in practice but worth measuring before we decide whether
# to skip such messages or accept the duplicate risk.
_ORIGIN_NATIVE = "native"
_ORIGIN_FORWARD = "forward"
_ORIGIN_FALLBACK = "fallback"


def _msg_key_with_origin(
    msg: Message, source_chat_id: int
) -> tuple[tuple[int, int], str]:
    """Like _msg_key but also reports which path produced the key.

    Returned origin label is used by run() to count fallback hits per
    backfill cycle — see _ORIGIN_FALLBACK above for why that's worth
    tracking. The key itself matches _msg_key exactly so dedup
    semantics are unchanged.
    """
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return (source_chat_id, msg.id), _ORIGIN_NATIVE
    from_peer = getattr(fwd, "from_id", None)
    channel_post = getattr(fwd, "channel_post", None)
    if from_peer is not None and channel_post is not None:
        try:
            return (
                (_tutils.get_peer_id(from_peer), int(channel_post)),
                _ORIGIN_FORWARD,
            )
        except (TypeError, ValueError):
            pass
    return (source_chat_id, msg.id), _ORIGIN_FALLBACK


def _msg_key(msg: Message, source_chat_id: int) -> tuple[int, int]:
    """Return (chat_id, msg_id) identity for dedup vs. inbox.jsonl.

    Thin wrapper kept for backwards compatibility with existing tests.
    New call sites should use _msg_key_with_origin so they can count
    fallback hits and react if the rate is non-trivial.
    """
    key, _origin = _msg_key_with_origin(msg, source_chat_id)
    return key


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

    # Session startup + entity resolution is the ONLY reliable signal
    # of a healthy Telethon session / channel access. A real expiry or
    # access loss raises here (start() can't re-auth non-interactively,
    # get_entity() fails on a lost channel) — that's when the operator
    # genuinely needs to act, so it's the right place for the ⚠️.
    # A successful resolve followed by an empty iter is NOT a failure:
    # it just means the channel was quiet since the last tick, which is
    # the normal case for a 2-hourly safety net behind the realtime
    # listener.
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    try:
        await client.start()
        log.info("Telethon session ready")
        source = await client.get_entity(SOURCE_USERNAME)
        dest = await client.get_entity(DEST_ID)
        # Marked form (-100… for channels) so it matches what trade-bot
        # records via the Bot API for forward_origin_chat_id.
        source_chat_id = _tutils.get_peer_id(source)
    except Exception as exc:
        log.exception("session/access failure during startup")
        _notify(
            "⚠️ <b>BeOn 동기화 — 세션/접근 실패</b>\n"
            f"{html.escape(type(exc).__name__)}: "
            f"{html.escape(str(exc)[:200])}\n"
            "Telethon 세션 만료 또는 채널 접근 불가 — 재인증 확인."
        )
        try:
            await client.disconnect()
        except Exception:
            pass
        return 1

    try:
        log.info(
            "source=%s (marked=%s) dest=%s",
            source.id, source_chat_id, dest.id,
        )

        # reverse=True means iterate oldest-first starting from
        # offset_date. Caps with our own date check on `until`.
        #
        # IGNORED filter at forward-time (not just ingest-time):
        # trade-bot's is_from_beon() rejects messages whose
        # forward_origin ≠ BeOn. So when BeOn relays AWAKE 플러스
        # (DART 공시 등), our forward lands in the trade channel with
        # forward_origin = AWAKE, trade-bot drops it, inbox.jsonl never
        # records it → next backfill tick sees the BeOn-side message
        # again and re-forwards → infinite loop. Filtering here breaks
        # the loop at the source: the relay never enters the trade
        # channel in the first place. Same constants as ingest /
        # purge so the three layers stay in sync.
        candidates: list[Message] = []
        skipped_existing = 0
        skipped_ignored = 0
        skipped_skip = 0                 # 🚫 사용자가 알림 버튼으로 영구 차단한 메시지
        fallback_ids: list[int] = []     # 재포워드-위험(fallback) 후보 id — 알림 버튼용
        # fwd_fallback_count: msgs whose fwd_from chain is present but
        # missing channel_post / from_id, so we fell back to keying on
        # (source_chat_id, msg.id). The listener wouldn't have recorded
        # the same key, so each fallback message is at non-zero risk of
        # being re-forwarded every cycle. We're measuring only — a
        # later commit decides whether to skip such messages outright,
        # based on the observed rate in journal.
        fwd_fallback_count = 0
        iterated = 0
        async for msg in client.iter_messages(
            source, offset_date=since, reverse=True
        ):
            if until is not None and msg.date > until:
                break
            iterated += 1
            # 🚫 사용자가 '동기화 완료' 알림의 버튼으로 영구 차단한 메시지 →
            # 후보에서 제외해 재포워드 루프 종료(사용자 2026-06-15 '두 시간마다
            # 계속 옴'). 유저-포워드 등 dedup 불가 메시지의 단일 종료 수단.
            if _beon_skip.contains(msg.id):
                skipped_skip += 1
                continue
            key, origin = _msg_key_with_origin(msg, source_chat_id)
            is_fallback = (origin == _ORIGIN_FALLBACK)
            if is_fallback:
                fwd_fallback_count += 1
                log.warning(
                    "fwd fallback msg=%d — fwd_from present but "
                    "channel_post/from_id missing; keying on "
                    "(source, msg.id) — listener-recorded key may "
                    "differ → potential re-forward on next tick",
                    msg.id,
                )
            if key in existing:
                skipped_existing += 1
                continue
            caption = msg.text or ""
            if (
                _ignored.matches_prefix(caption)
                or _ignored.matches_contains(caption)
            ):
                skipped_ignored += 1
                log.info(
                    "skip ignored msg=%d caption=%r",
                    msg.id, caption[:60],
                )
                continue
            candidates.append(msg)
            if is_fallback:
                fallback_ids.append(msg.id)   # 재포워드 루프 주범 → 알림 버튼에 실음

        until_label = (until or datetime.now(timezone.utc)).date().isoformat()
        log.info(
            "range %s → %s — candidates=%d skipped_existing=%d "
            "skipped_ignored=%d fwd_fallback=%d",
            since.date().isoformat(),
            until_label,
            len(candidates),
            skipped_existing,
            skipped_ignored,
            fwd_fallback_count,
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
            _note = (
                f"✅ <b>BeOn 동기화 완료</b>\n"
                f"신규 forwarded: {forwarded_msgs}/{total_msgs} msgs\n"
                f"units: {len(units)}"
            )
            _buttons = None
            # 소량 동기화(≤5건) = 반복 straggler(형식·출처 불명 문서가 매 틱 재포워드)
            # 가능성 → 방금 받은 메시지 전부에 🚫 차단 버튼(사용자 2026-06-15 '취소
            # 버튼 안 달려있어' — fallback 만 달던 것이 정상 채널포워드 .html 을 놓침).
            # 대량 일배치엔 미부착(오탭 mass-block 방지). 탭 → trade-bot 콜백이 그
            # BeOn msg_id 를 beon_skip 에 add → 다음 틱부터 후보 제외. callback 64B cap 6.
            _cand_ids = [m.id for m in candidates][:6]
            if _cand_ids and forwarded_msgs <= 5:
                _buttons = [("🚫 이 메시지 그만 받기 (반복 시)",
                             "beon_skip:" + ",".join(str(i) for i in _cand_ids))]
            if fallback_ids:
                _note += f"\n⚠️ 출처 불명 {len(fallback_ids)}건 포함"
            _notify(_note, buttons=_buttons)
        else:
            # Empty / all-already-ingested / all-ignored window — the
            # normal quiet state for a safety net behind the realtime
            # listener. Silent: session health was already proven by the
            # successful startup above, and a genuine failure would have
            # raised there. No ⚠️ here (that was a false-positive source).
            log.info(
                "sync: nothing new (iterated=%d skipped_existing=%d "
                "skipped_ignored=%d fwd_fallback=%d)",
                iterated, skipped_existing, skipped_ignored,
                fwd_fallback_count,
            )
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
