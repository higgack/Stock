"""Live BeOn_BeClear → trade channel forwarder (Telethon listener).

Long-running Telethon client that subscribes to NewMessage events on
the BeOn channel and forwards each new post (or album) to the private
trade channel within ~1-3 s of publication. The 2-hour timer
(backfill_beon.py) stays as a safety net for listener downtime — both
write idempotently and trade-bot's ingest deduplicates by message_id,
so duplicates are harmless.

Uses its own session file (.beon-listener-session) so it doesn't lock
the .backfill-session used by the timer. One-time interactive auth:

  cd ~/stock-trade
  .backfill-venv/bin/python -m trade.scripts.listen_beon --auth

This prompts for phone + login code + 2FA once. Afterwards the systemd
service runs unattended.

Album handling:
  events.NewMessage fires per-message, so members of the same album
  arrive separately. Buffer by grouped_id for ALBUM_DEBOUNCE_S seconds,
  then forward as a single forward_messages([...]) call — preserving
  album-ness on the destination so trade-bot's media_group_id grouping
  still works.

Lifecycle alerts (best-effort, never raise):
  🟢 <b>BeOn 리스너 가동</b>            — service started, session OK
  ⚠️ <b>BeOn 리스너 forward 실패</b>     — Telegram refused after retries
  ❌ <b>BeOn 리스너 종료</b>: 세션 미인증 — operator must rerun --auth

Exits with status 78 (EX_CONFIG) when the session is missing, paired
with `RestartPreventExitStatus=78` in the unit so systemd doesn't
hot-loop on a config error — operator gets one ❌ notify, runs --auth,
then `sudo systemctl restart trade-bot-beon-listener`.
"""

import argparse
import asyncio
import html
import logging
import os
import subprocess
import sys

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyError,
    FloodWaitError,
    SessionPasswordNeededError,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("listen_beon")

API_ID = int(os.environ["TRADE_TELETHON_API_ID"])
API_HASH = os.environ["TRADE_TELETHON_API_HASH"]

_dest_raw = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
DEST_IDS = [int(x) for x in _dest_raw.split(",") if x.strip()]
if len(DEST_IDS) != 1:
    raise SystemExit(
        "listen_beon: TRADE_CHANNEL_CHAT_IDS must be a single chat ID"
    )
DEST_ID = DEST_IDS[0]

SOURCE_USERNAME = "BeOn_BeClear"
SESSION_PATH = ".beon-listener-session"  # cwd-relative; separate from backfill

ALBUM_DEBOUNCE_S = float(
    os.environ.get("TRADE_LISTENER_ALBUM_DEBOUNCE_S") or "3.0"
)
# 발송 간 호흡 — 버스트(타채널 연속 게시)에서 FloodWait 자체를 예방
# (backfill_beon 의 3s pacing 교훈을 리스너에도, 2026-06-11).
PACE_S = float(os.environ.get("TRADE_LISTENER_PACE_S") or "2.5")
FORWARD_RETRY_MAX = 8
FLOOD_DROP_S = 600  # 이보다 긴 FloodWait 요구 시 해당 건 드랍(2h sync 가 회수)
EX_CONFIG = 78  # /usr/include/sysexits.h — matches unit's RestartPreventExitStatus


def _notify(text: str) -> None:
    """Push a short HTML message to the trade channel. Silent if creds
    are absent so the listener still works without notifications.
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


async def _forward_ids(client, source, dest, msg_ids: list[int]) -> bool:
    """Forward IDs with FloodWait + retry. True on success.

    ⚠️ 단일 워커에서만 호출할 것 (직렬화 전제). 2026-06-11 surfaced:
    Telethon 은 NewMessage 핸들러를 메시지마다 동시 실행 → 버스트 30개
    = 동시 forward 30개 → 앞 3개만 성공 후 전원 FloodWait → 전원이
    같은 시각 재시도(thundering herd) → 대기 누적·재시도 소진·조용한
    드랍 ('3개 받고 멈춤' 실사례). FloodWait 는 attempt 미소진(서버
    혼잡 신호지 실패 아님), 단 FLOOD_DROP_S 초과 요구면 드랍하고 2h
    sync 가 회수 (backfill_beon 의 600s 캡 교훈)."""
    attempt = 0
    while attempt < FORWARD_RETRY_MAX:
        try:
            await client.forward_messages(dest, msg_ids, from_peer=source)
            return True
        except FloodWaitError as e:
            if e.seconds > FLOOD_DROP_S:
                log.error("flood wait %ds > cap %ds — drop (sync 회수)",
                          e.seconds, FLOOD_DROP_S)
                return False
            log.info("flood wait %ds (직렬 재시도)", e.seconds + 1)
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            attempt += 1
            log.error("forward error (attempt %d): %s", attempt, e)
            await asyncio.sleep(5)
    return False


async def _forward_worker(client, source, dest, queue: asyncio.Queue) -> None:
    """단일 직렬 워커 — 큐에서 forward 단위(list[int])를 꺼내 pacing 발송.
    동시 forward 제거 + 발송 간 PACE_S 호흡으로 FloodWait 예방."""
    while True:
        ids = await queue.get()
        try:
            ok = await _forward_ids(client, source, dest, ids)
            if ok:
                log.info("forwarded %d msg(s): %s", len(ids), ids[:3])
            else:
                log.error("forward failed ids=%s", ids)
                _notify(
                    f"⚠️ <b>BeOn 리스너 forward 실패</b>\n"
                    f"msgs={len(ids)} (2h sync 가 회수 예정)"
                )
        except Exception as e:
            log.exception("forward worker error: %s", e)
        finally:
            queue.task_done()
        await asyncio.sleep(PACE_S)


async def _run_listener() -> int:
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log.error("session not authorized — run with --auth interactively")
        _notify(
            "❌ <b>BeOn 리스너 시작 실패</b>\n"
            "세션 미인증. 한 번만 수동 인증 필요:\n"
            "<code>cd ~/stock-trade && .backfill-venv/bin/python "
            "-m trade.scripts.listen_beon --auth</code>\n"
            "인증 후: <code>sudo systemctl restart "
            "trade-bot-beon-listener</code>"
        )
        await client.disconnect()
        return EX_CONFIG

    source = await client.get_entity(SOURCE_USERNAME)
    dest = await client.get_entity(DEST_ID)
    log.info("connected: source=%s dest=%s", source.id, dest.id)

    album_buf: dict[int, list[int]] = {}
    album_tasks: dict[int, asyncio.Task] = {}
    # 모든 forward 는 이 큐 → 단일 워커 경유 (동시 forward 금지 — FloodWait
    # thundering herd 차단, 2026-06-11 '3개 받고 멈춤' fix). 큐는 무한
    # (버스트 전량 보존), 워커가 PACE_S 간격으로 순서대로 비움.
    fwd_q: asyncio.Queue = asyncio.Queue()
    worker_task = asyncio.create_task(
        _forward_worker(client, source, dest, fwd_q))

    async def flush_album(gid: int) -> None:
        try:
            await asyncio.sleep(ALBUM_DEBOUNCE_S)
            ids = album_buf.pop(gid, [])
            album_tasks.pop(gid, None)
            if ids:
                fwd_q.put_nowait(sorted(ids))
                log.info("queued album gid=%d (%d msgs, q=%d)",
                         gid, len(ids), fwd_q.qsize())
        except Exception as e:
            log.exception("flush_album error: %s", e)

    @client.on(events.NewMessage(chats=source))
    async def on_new(event):
        msg = event.message
        gid = getattr(msg, "grouped_id", None)
        if gid is None:
            fwd_q.put_nowait([msg.id])
            log.info("queued msg=%d (q=%d)", msg.id, fwd_q.qsize())
            return
        album_buf.setdefault(gid, []).append(msg.id)
        if gid not in album_tasks:
            album_tasks[gid] = asyncio.create_task(flush_album(gid))

    _notify(
        "🟢 <b>BeOn 리스너 가동</b>\n"
        f"source=@{SOURCE_USERNAME} → dest={DEST_ID}\n"
        f"앨범 debounce {ALBUM_DEBOUNCE_S:.0f}s · 새 글 즉시 forward"
    )
    log.info("listener up; awaiting events")

    try:
        await client.run_until_disconnected()
    finally:
        worker_task.cancel()
        await client.disconnect()
    return 0


async def _run_auth() -> int:
    """Interactive one-time authentication. Writes .beon-listener-session."""
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    log.info("session authorized as @%s (id=%s)", me.username, me.id)
    await client.disconnect()
    print(f"OK — session saved at {SESSION_PATH}")
    print("Next: sudo systemctl enable --now trade-bot-beon-listener")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Live BeOn → trade forwarder (Telethon NewMessage listener)."
    )
    ap.add_argument(
        "--auth",
        action="store_true",
        help="One-time interactive authentication (phone + code + 2FA).",
    )
    args = ap.parse_args()
    try:
        if args.auth:
            rc = asyncio.run(_run_auth())
        else:
            rc = asyncio.run(_run_listener())
    except (AuthKeyError, SessionPasswordNeededError) as e:
        log.error("auth error: %s", e)
        _notify(
            "❌ <b>BeOn 리스너 종료</b>\n"
            f"세션 인증 만료: {html.escape(str(e))}\n"
            "재인증: <code>.backfill-venv/bin/python "
            "-m trade.scripts.listen_beon --auth</code>"
        )
        sys.exit(EX_CONFIG)
    sys.exit(rc)


if __name__ == "__main__":
    main()
