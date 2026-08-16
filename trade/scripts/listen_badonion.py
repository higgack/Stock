"""Live Badonions(나쁜양파) → trade channel forwarder (Telethon listener).

미러 of listen_beon.py (사용자 2026-07-10). 배포 주의: 이 파일 변경 시
trade-auto-update 가 trade-bot-badonion-listener 서비스를 재시작해야 새
코드가 로드된다(install-trade-units.sh 의 "active service content changed
→ restart" 규칙, beon-listener 와 동일 패턴).

Long-running Telethon client that subscribes to NewMessage events on
the Badonions channel and forwards each new post (or album) to the
private trade channel within ~1-3 s of publication. The periodic timer
(backfill_badonion.py) stays as a safety net for listener downtime —
both write idempotently and trade-bot's ingest deduplicates by
message_id, so duplicates are harmless.

Uses its OWN session file (.badonion-listener-session), separate from
both the BeOn listener's .beon-listener-session and this pipeline's own
.badonion-session (used by the periodic backfill) — a long-running
process can't share a session file with a periodic one without lock
contention. One-time interactive auth:

  cd ~/stock-trade
  .backfill-venv/bin/python -m trade.scripts.listen_badonion --auth

This prompts for phone + login code + 2FA once (같은 계정 재사용 가능 —
BeOn 리스너와 동일 로그인, 세션 파일만 별도). Afterwards the systemd
service runs unattended.

Album handling: identical to listen_beon.py — buffer by grouped_id for
ALBUM_DEBOUNCE_S seconds, then forward as a single forward_messages([...])
call so trade-bot's media_group_id grouping still works.

Relevance filter (differs from listen_beon.py, 사용자 2026-07-11 — 실백필 중
애널리스트 레이팅표 등 무관 콘텐츠가 trade 채널로 넘어간 걸 확인):
나쁜양파는 수출 데이터 외 애널리스트 레이팅표 등 무관한 트레이딩 정보도
섞어 올리는 일반 채널이라, 캡션이 나쁜양파 소스 파서 중 하나로 파싱되는
메시지(앨범이면 멤버 중 하나라도)만 forward 한다. BeOn 은 채널 자체가 단일
목적이라 이 필터가 없음.

⚠️ 소스 목록을 여기에 나열하지 않는다 — `trade/badonion_sources.py` 단일
레지스트리가 유일한 출처다. 옛 문서는 국가명을 하드코딩해 뒀고, 2026-08-16
한국 수출을 추가할 때 실제로 로그 문구가 어긋났다(필터는 통과시키는데
로그엔 '한국'이 없었다).

Lifecycle alerts (best-effort, never raise):
  🟢 <b>나쁜양파 리스너 가동</b>
  ⚠️ <b>나쁜양파 리스너 forward 실패</b>
  ❌ <b>나쁜양파 리스너 종료</b>: 세션 미인증 — operator must rerun --auth

Exits with status 78 (EX_CONFIG) when the session is missing, paired
with `RestartPreventExitStatus=78` in the unit so systemd doesn't
hot-loop on a config error.
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

from trade import badonion_sources as _srcs

load_dotenv()


def _is_relevant(text: str) -> bool:
    """나쁜양파 채널의 무관 콘텐츠(애널리스트 레이팅표 등) 필터.

    소스 목록은 `trade/badonion_sources.py` 단일 레지스트리 — 리스너·
    백필·ingest·unstored_check·dashboard 가 모두 그걸 본다. 옛 구현은
    5개 파일에 같은 or-체인을 복붙해 뒀고, 한국 수출을 추가할 때 실제로
    로그 문구가 어긋났다(2026-08-16).
    """
    return _srcs.is_relevant(text)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("listen_badonion")

API_ID = int(os.environ["TRADE_TELETHON_API_ID"])
API_HASH = os.environ["TRADE_TELETHON_API_HASH"]

_dest_raw = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
DEST_IDS = [int(x) for x in _dest_raw.split(",") if x.strip()]
if len(DEST_IDS) != 1:
    raise SystemExit(
        "listen_badonion: TRADE_CHANNEL_CHAT_IDS must be a single chat ID"
    )
DEST_ID = DEST_IDS[0]

SOURCE_USERNAME = "Badonions"
SESSION_PATH = ".badonion-listener-session"  # separate from backfill_badonion

ALBUM_DEBOUNCE_S = float(
    os.environ.get("TRADE_LISTENER_ALBUM_DEBOUNCE_S") or "3.0"
)
PACE_S = float(os.environ.get("TRADE_LISTENER_PACE_S") or "2.5")
FORWARD_RETRY_MAX = 8
FLOOD_DROP_S = 600  # 이보다 긴 FloodWait 요구 시 해당 건 드랍(주기 sync 가 회수)
EX_CONFIG = 78  # /usr/include/sysexits.h — matches unit's RestartPreventExitStatus


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


async def _forward_ids(client, source, dest, msg_ids: list[int]) -> bool:
    """Forward IDs with FloodWait + retry. True on success.

    ⚠️ 단일 워커에서만 호출할 것 (직렬화 전제) — listen_beon.py 의 thundering-herd
    교훈 그대로(2026-06-11): 버스트 시 동시 forward 를 큐+단일워커로 직렬화."""
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
    """단일 직렬 워커 — 큐에서 forward 단위(list[int])를 꺼내 pacing 발송."""
    while True:
        ids = await queue.get()
        try:
            ok = await _forward_ids(client, source, dest, ids)
            if ok:
                log.info("forwarded %d msg(s): %s", len(ids), ids[:3])
            else:
                log.error("forward failed ids=%s", ids)
                _notify(
                    f"⚠️ <b>나쁜양파 리스너 forward 실패</b>\n"
                    f"msgs={len(ids)} (주기 sync 가 회수 예정)"
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
            "❌ <b>나쁜양파 리스너 시작 실패</b>\n"
            "세션 미인증. 한 번만 수동 인증 필요:\n"
            "<code>cd ~/stock-trade && .backfill-venv/bin/python "
            "-m trade.scripts.listen_badonion --auth</code>\n"
            "인증 후: <code>sudo systemctl restart "
            "trade-bot-badonion-listener</code>"
        )
        await client.disconnect()
        return EX_CONFIG

    source = await client.get_entity(SOURCE_USERNAME)
    dest = await client.get_entity(DEST_ID)
    log.info("connected: source=%s dest=%s", source.id, dest.id)

    album_buf: dict[int, list[int]] = {}
    album_relevant: dict[int, bool] = {}
    album_tasks: dict[int, asyncio.Task] = {}
    fwd_q: asyncio.Queue = asyncio.Queue()
    worker_task = asyncio.create_task(
        _forward_worker(client, source, dest, fwd_q))

    async def flush_album(gid: int) -> None:
        try:
            await asyncio.sleep(ALBUM_DEBOUNCE_S)
            ids = album_buf.pop(gid, [])
            relevant = album_relevant.pop(gid, False)
            album_tasks.pop(gid, None)
            if not ids:
                return
            if relevant:
                fwd_q.put_nowait(sorted(ids))
                log.info("queued album gid=%d (%d msgs, q=%d)",
                         gid, len(ids), fwd_q.qsize())
            else:
                log.info("dropped irrelevant album gid=%d (%d msgs) — "
                          "no [%s] caption", gid, len(ids), _srcs.labels())
        except Exception as e:
            log.exception("flush_album error: %s", e)

    @client.on(events.NewMessage(chats=source))
    async def on_new(event):
        msg = event.message
        relevant_msg = _is_relevant(msg.text or "")
        gid = getattr(msg, "grouped_id", None)
        if gid is None:
            if relevant_msg:
                fwd_q.put_nowait([msg.id])
                log.info("queued msg=%d (q=%d)", msg.id, fwd_q.qsize())
            else:
                log.info("dropped irrelevant msg=%d — no [%s] caption",
                         msg.id, _srcs.labels())
            return
        album_buf.setdefault(gid, []).append(msg.id)
        if relevant_msg:
            album_relevant[gid] = True
        if gid not in album_tasks:
            album_tasks[gid] = asyncio.create_task(flush_album(gid))

    _notify(
        "🟢 <b>나쁜양파 리스너 가동</b>\n"
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
    """Interactive one-time authentication. Writes .badonion-listener-session."""
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    log.info("session authorized as @%s (id=%s)", me.username, me.id)
    await client.disconnect()
    print(f"OK — session saved at {SESSION_PATH}")
    print("Next: sudo systemctl enable --now trade-bot-badonion-listener")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Live Badonions(나쁜양파) → trade forwarder (Telethon NewMessage listener)."
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
            "❌ <b>나쁜양파 리스너 종료</b>\n"
            f"세션 인증 만료: {html.escape(str(e))}\n"
            "재인증: <code>.backfill-venv/bin/python "
            "-m trade.scripts.listen_badonion --auth</code>"
        )
        sys.exit(EX_CONFIG)
    sys.exit(rc)


if __name__ == "__main__":
    main()
