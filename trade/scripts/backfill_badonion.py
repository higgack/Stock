"""Telethon sync: forward Badonions(나쁜양파, t.me/Badonions) posts into the
private trade channel so trade-bot can ingest them like live forwards.

미러 of backfill_beon.py (사용자 2026-07-10 — "일본이랑 똑같은 방식으로 대만
가져와줘"). 나쁜양파는 대만·중국(사용자 2026-07-11 — "같은 텔레그램")·일본
(사용자 2026-07-11 — "일본은 2개의 서로 다른 채널에서 2개의 대쉬보드로
갈거야", BeOn 과 별도 두 번째 소스)에 더해 태국·말레이시아·필리핀·멕시코
(사용자 2026-07-26) 관세 월별 품목 수출통계(품목당 관련기업 여러 개 +
최신월/과거 히스토리 동봉)를 텔레그램으로 발행 — 각각 별도 tw.db/cn.db/
jp2.db/th.db/my.db/ph.db/mx.db 로 적재되어 동명 .html 대시보드에 렌더된다
(trade/{tw,cn,jp2,th,my,ph,mx}_exports.py, trade/dashboard.py).

⚠️ BeOn 과의 차이점(사용자 2026-07-11, 실백필 중 애널리스트 레이팅표가 trade
채널로 넘어간 걸 확인): 나쁜양파는 위 7개국 수출 데이터 외에 애널리스트
레이팅표·Capex 비교차트 등 무관한 트레이딩 정보도 섞어 올리는 일반 채널 —
BeOn 처럼 '전부 forward 후 다운스트림에서 파싱 실패분만 버림' 방식을 쓰면
무관 콘텐츠가 전부 private trade 채널에 그대로 쌓인다. 그래서 이 스크립트는
forward 시점에 `_is_relevant()`(7개국 파서 전부 시도)로 수출 캡션인 unit
(앨범이면 멤버 중 하나라도 매칭)만 골라 보낸다 — BeOn 은 채널 자체가
단일 목적이라 이 필터가 없음.

Works both as a one-off catch-up and as a periodic sync driven by
trade-bot-badonion-sync.timer. Idempotent — scans inbox.jsonl and skips
any Badonions message_id that's already been ingested, so re-running is
safe (picks up where it left off after a FloodWait abort, disk-low
pause, or accidental Ctrl+C).

Safety features (identical to backfill_beon.py — same shared TRADE_* env
knobs, same disk guard / adaptive pacing / FloodWait hard cap):
  - Adaptive pacing: starts at 1.5 s/unit and grows by 0.1 s every 500
    messages, capped at 3.0 s.
  - FloodWait hard cap: if Telegram asks for > 600 s wait, exit
    gracefully and notify, instead of sleeping for an hour inside the
    script. User reruns next day.
  - Disk guard: every 20 units, check free space on /. If below
    TRADE_MIN_FREE_GB (default 2.0), pause and notify, poll every 60 s
    for free space to recover, then notify resume.
  - Telegram notifications: pause / resume / abort all push to the
    same channel as trade-bot deploys (TRADE_BOT_TOKEN +
    TRADE_CHANNEL_CHAT_IDS).

Uses a SEPARATE Telethon session (.badonion-session) from the BeOn
pipeline's .backfill-session — both are independent, uncoordinated
Telethon clients writing into the same private trade channel; that's
fine, ingest_inbox.py dedupes and routes by parsed content, not by
source session.

Setup (one-time): reuses the existing .backfill-venv + the same
TRADE_TELETHON_API_ID/TRADE_TELETHON_API_HASH already in .env (no new
Telegram app credentials needed — same account, second session file).

Run manually:
  .backfill-venv/bin/python trade/scripts/backfill_badonion.py --since 2026-05-01
  .backfill-venv/bin/python trade/scripts/backfill_badonion.py  # default: 2-day lookback
  # optional: --to 2026-05-16, --dry-run, --lookback-days N

Run by systemd (trade-bot-badonion-sync.timer):
  Invoked without --since; defaults to 2-day lookback (realtime listener
  is the primary path, this is the downtime safety net).
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

# 직접 실행에서도 'trade' 패키지가 임포트되게 레포 루트를 path 에 추가
# (backfill_beon.py 와 동일 사유, 2026-06-15).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trade import cn_exports as _cn
from trade import ignored as _ignored
from trade import jp2_exports as _jp2
from trade import mx_exports as _mx
from trade import my_exports as _my
from trade import ph_exports as _ph
from trade import th_exports as _th
from trade import tw_exports as _tw
from trade import us_imports as _us


def _is_relevant(text: str) -> bool:
    """대만·중국·일본(나쁜양파 두 번째 소스)·태국·말레이시아·필리핀·멕시코·미국
    수출 데이터 캡션인지(나쁜양파 채널의 무관 콘텐츠 필터, 사용자 2026-07-11
    중국·일본 추가 + 2026-07-26 태국·말레이시아·필리핀·멕시코 추가) —
    listen_badonion.py 와 동일 필터(미러)."""
    return (_tw.parse_tw_export(text) is not None
            or _cn.parse_cn_export(text) is not None
            or _jp2.parse_jp2_export(text) is not None
            or _th.parse_th_export(text) is not None
            or _my.parse_my_export(text) is not None
            or _ph.parse_ph_export(text) is not None
            or _mx.parse_mx_export(text) is not None
            or _us.parse_us_import(text) is not None)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("backfill_badonion")

API_ID = int(os.environ["TRADE_TELETHON_API_ID"])
API_HASH = os.environ["TRADE_TELETHON_API_HASH"]

_dest_raw = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
DEST_IDS = [int(x) for x in _dest_raw.split(",") if x.strip()]
if len(DEST_IDS) != 1:
    raise SystemExit(
        "backfill_badonion: TRADE_CHANNEL_CHAT_IDS must be a single chat ID"
    )
DEST_ID = DEST_IDS[0]

INBOX_DIR = Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))
INBOX_PATH = INBOX_DIR / "inbox.jsonl"

SOURCE_USERNAME = "Badonions"
SESSION_PATH = ".badonion-session"  # cwd-relative; .gitignored; separate from BeOn

# --- Adaptive pacing (same TRADE_* knobs as backfill_beon.py — shared,
# trade-wide safety tuning, not per-source) --------------------------
PAUSE_BASE_S = float(os.environ.get("TRADE_PAUSE_BASE_S") or "1.5")
PAUSE_INCREMENT_S = float(os.environ.get("TRADE_PAUSE_INCREMENT_S") or "0.1")
PAUSE_INCREMENT_EVERY = int(os.environ.get("TRADE_PAUSE_INCREMENT_EVERY") or "500")
PAUSE_MAX_S = float(os.environ.get("TRADE_PAUSE_MAX_S") or "3.0")

MAX_FLOOD_WAIT_S = int(os.environ.get("TRADE_MAX_FLOOD_WAIT_S") or "600")

# 대만 관세청 월간 발행은 품목 ~15개 내외(사용자 제공 스크린샷 기준)라 BeOn 대비
# 볼륨이 훨씬 작음 — 5000 cap 은 순수 안전장치(비정상 대량 스캔만 차단), 정상
# 월배치엔 절대 걸리지 않음. 같은 TRADE_MAX_CANDIDATES 를 공유(전역 안전 knob).
MAX_CANDIDATES_DEFAULT = int(os.environ.get("TRADE_MAX_CANDIDATES") or "5000")

# --- Disk guard (shared knobs) ----------------------------------------
MIN_FREE_GB = float(os.environ.get("TRADE_MIN_FREE_GB") or "2.0")
DISK_RESUME_BUFFER_GB = float(
    os.environ.get("TRADE_DISK_RESUME_BUFFER_GB") or "0.5"
)
DISK_CHECK_EVERY_UNITS = int(os.environ.get("TRADE_DISK_CHECK_EVERY") or "20")
DISK_POLL_SECONDS = 60

# 연속 forward 실패 cap — 개별 메시지 삭제/포워드불가는 드문드문 발생하는 게
# 정상이지만, 연속으로 여러 건 실패하면 세션/권한/네트워크 등 시스템 문제일
# 가능성이 높음 → 조용히 수천 건을 다 스킵하기 전에 크게 abort(2026-07-11).
MAX_CONSECUTIVE_FAILURES = int(
    os.environ.get("TRADE_MAX_CONSECUTIVE_FWD_FAILURES") or "5"
)


class BackfillAborted(Exception):
    """Raised when the script should exit gracefully so the operator
    can resume later. Always paired with a Telegram notify."""


def _notify(text: str) -> None:
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
        f"⏸ <b>나쁜양파 동기화 일시정지</b>\n"
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
        f"▶ <b>나쁜양파 동기화 재개</b>\n"
        f"디스크 잔여: {free:.1f}GB\n"
        f"진행: {forwarded}/{total} msgs 부터 이어서"
    )
    log.info("resuming: disk free %.1fGB", free)


def _current_pause(forwarded: int) -> float:
    bumps = forwarded // PAUSE_INCREMENT_EVERY
    return min(PAUSE_BASE_S + bumps * PAUSE_INCREMENT_S, PAUSE_MAX_S)


def _load_existing_keys() -> set[tuple[int, int]]:
    """Collect (chat_id, msg_id) of every forward already represented in
    inbox.jsonl — source-agnostic (keyed on forward origin, not which
    upstream channel produced it), so this scan is shared verbatim with
    the BeOn pipeline. See backfill_beon.py's docstring for the native
    vs. forward vs. fallback origin-key rationale (identical here)."""
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


_ORIGIN_NATIVE = "native"
_ORIGIN_FORWARD = "forward"
_ORIGIN_FALLBACK = "fallback"


def _msg_key_with_origin(
    msg: Message, source_chat_id: int
) -> tuple[tuple[int, int], str]:
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


def _group_by_album(messages: list[Message]) -> list[list[Message]]:
    """Walk chronological messages and return a list of send-units:
    each unit is one standalone message OR one full album."""
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
        except Exception as e:
            # Permanent per-message failure (e.g. MessageIdInvalidError —
            # source message deleted/edited-to-service between candidate
            # scan and forward time). Not a FloodWait, retrying won't help
            # and re-raising would crash the entire multi-thousand-message
            # backfill over one bad message (2026-07-11, surfaced by a wide
            # Badonion catch-up run). Skip this unit, keep going.
            log.warning(
                "permanent forward failure msgs=%s (%s: %s) — skipping unit",
                msg_ids, type(e).__name__, e,
            )
            return False
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
    try:
        await client.start()
        log.info("Telethon session ready")
        source = await client.get_entity(SOURCE_USERNAME)
        dest = await client.get_entity(DEST_ID)
        source_chat_id = _tutils.get_peer_id(source)
    except Exception as exc:
        log.exception("session/access failure during startup")
        _notify(
            "⚠️ <b>나쁜양파 동기화 — 세션/접근 실패</b>\n"
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

        candidates: list[Message] = []
        skipped_existing = 0
        skipped_ignored = 0
        fwd_fallback_count = 0
        iterated = 0
        async for msg in client.iter_messages(
            source, offset_date=since, reverse=True
        ):
            if until is not None and msg.date > until:
                break
            iterated += 1
            key, origin = _msg_key_with_origin(msg, source_chat_id)
            is_fallback = (origin == _ORIGIN_FALLBACK)
            if is_fallback:
                fwd_fallback_count += 1
                log.warning(
                    "fwd fallback msg=%d — fwd_from present but "
                    "channel_post/from_id missing; keying on "
                    "(source, msg.id) — potential re-forward on next tick",
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
                f"⚠️ <b>나쁜양파 동기화 중단 (안전장치)</b>\n"
                f"후보 {len(candidates)}개가 cap {max_candidates}개 초과.\n"
                f"의도된 wide 백필이면 명시 실행:\n"
                f"<code>--since YYYY-MM-DD --max-candidates {len(candidates)+100}</code>"
            )
            return 2

        units_all = _group_by_album(candidates)
        log.info(
            "grouped into %d send units (singles + albums)", len(units_all)
        )

        # 관련성 필터 — 나쁜양파는 7개국 수출 데이터 외 콘텐츠도 섞인 일반
        # 채널(사용자 2026-07-11, 애널리스트 레이팅표가 trade 채널로 넘어간
        # 걸 확인). 앨범이면 멤버 중 하나라도 7개국 수출 캡션이면 유닛
        # 전체(사진 포함) 유지(_is_relevant, 2026-07-26 태국·말레이시아·
        # 필리핀·멕시코 추가).
        units = [
            u for u in units_all
            if any(_is_relevant(m.text or "") for m in u)
        ]
        skipped_irrelevant = len(units_all) - len(units)
        total_msgs = sum(len(u) for u in units)
        log.info(
            "relevance filter: %d/%d units are 대만·중국·일본·태국·말레이시아·"
            "필리핀·멕시코 수출 데이터 "
            "(%d irrelevant skipped, %d candidate msgs total)",
            len(units), len(units_all), skipped_irrelevant, len(candidates),
        )

        if dry_run:
            log.info("dry-run: not forwarding")
            return 0

        forwarded_msgs = 0
        skipped_units = 0
        consecutive_failures = 0
        try:
            for i, unit in enumerate(units, 1):
                if i == 1 or i % DISK_CHECK_EVERY_UNITS == 0:
                    await _maybe_pause_for_disk(forwarded_msgs, total_msgs)

                ok = await _forward_unit(client, source, unit, dest)
                if ok:
                    forwarded_msgs += len(unit)
                    consecutive_failures = 0
                else:
                    skipped_units += 1
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        # A per-message error (deleted/service message) is
                        # isolated and rare; a run of these back-to-back is
                        # a signal of something systemic (dest write access
                        # revoked, source/dest entity gone stale, etc.) —
                        # that must abort loudly, not get ground through as
                        # thousands of individually "skipped" units (2026-07-11
                        # review of the per-unit skip fix above).
                        raise BackfillAborted(
                            f"{consecutive_failures} consecutive forward "
                            f"failures — likely systemic (session/permission/"
                            f"network), not isolated per-message errors"
                        )
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
                f"❌ <b>나쁜양파 백필 중단</b>\n"
                f"사유: {html.escape(str(exc))}\n"
                f"진행: {forwarded_msgs}/{total_msgs} msgs\n"
                f"같은 명령으로 재실행하면 이어서 진행 (idempotent)."
            )
            return 1

        log.info(
            "done: forwarded %d of %d candidate messages (skipped_units=%d)",
            forwarded_msgs,
            total_msgs,
            skipped_units,
        )
        if forwarded_msgs > 0 or skipped_units > 0:
            # skipped_units>0 도 notify 게이트 포함(2026-07-11 리뷰) — 전부 실패해도
            # forwarded_msgs=0 이라 조용히 "변경없음" 취급되던 걸 fix. 실패가
            # 반복되면 operator 가 로그 대신 이 알림으로 알아채야 함.
            _title = ("✅ <b>나쁜양파 동기화 완료</b>" if forwarded_msgs > 0
                      else "⚠️ <b>나쁜양파 동기화 — 전체 실패</b>")
            _note = (
                f"{_title}\n"
                f"신규 forwarded: {forwarded_msgs}/{total_msgs} msgs\n"
                f"units: {len(units)}"
            )
            if fwd_fallback_count:
                _note += f"\n⚠️ 출처 불명 {fwd_fallback_count}건 포함"
            if skipped_units:
                _note += f"\n⚠️ 영구실패로 스킵된 unit {skipped_units}건(삭제/포워드불가 등)"
            _notify(_note)
        else:
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
        description="Backfill Badonions(나쁜양파) → private trade channel via Telethon."
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
