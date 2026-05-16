"""Korea import/export dashboard bot — ingestion (phase 1.5).

Built for BeOn's burst pattern: ~50 publication events/year, each dumping
100-300 messages (text + 2 images per item) within ~10 minutes. Design
keeps the Telegram update loop unblocked so polling never lags far enough
to false-trigger the watchdog.

Per-message flow:
  1. fast-path: serialize to dict, append one line to inbox.jsonl (<1ms)
  2. fire-and-forget asyncio task to download photo(s) to disk, throttled
     by a global semaphore to stay under Telegram's ~30 req/sec API cap

Media-group handling (BeOn posts text + graph + table as one album):
  Telegram delivers each album member as a separate Update with the same
  media_group_id. We write one JSONL row per member, tagged with
  media_group_id so phase 2's parser can join siblings without state in
  memory (no quiet-window race, survives restarts).

Forwarder fallback:
  Some forwarder tools strip forward_origin and prepend a "BeOn - 비온"
  header into the text body (copy mode). When SOURCE_ORIGIN is set we
  accept both true forwards and copy-style forwards by checking for the
  header pattern.

Out of scope (intentionally, never):
  - OCR / LLM analysis of images. BeOn's own graph is the deliverable;
    re-extracting numbers would multiply cost and not improve readability.
  - Chart re-rendering. Same reason.
  - Parsing / SQLite / dashboard. Those land in phases 2a / 2b.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("trade-bot")

TOKEN = os.environ["TRADE_BOT_TOKEN"]

_raw_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
CHANNEL_CHAT_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip()}

# Optional source-channel filter. Username (without @) or numeric -100... ID.
# When unset, every channel post is accepted — useful while discovering the
# destination channel's chat ID on first run.
SOURCE_ORIGIN = os.environ.get("TRADE_SOURCE_ORIGIN", "").lstrip("@").strip()

INBOX_DIR = Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))
INBOX_PATH = INBOX_DIR / "inbox.jsonl"
MEDIA_ROOT = INBOX_DIR / "media"

# Cap concurrent Telegram file downloads. Telegram tolerates ~30 req/sec
# bot API-wide; with bursts of 200+ photo messages an unbounded fan-out
# trips 429s. 8 keeps us well under with margin for getUpdates traffic.
DOWNLOAD_CONCURRENCY = int(os.environ.get("TRADE_DOWNLOAD_CONCURRENCY") or "8")
_download_sem: asyncio.Semaphore | None = None  # built once the loop is up

# Copy-style forwarder detection: header pattern injected into the body.
_BEON_HEADER_RE = re.compile(r"^\s*BeOn\s*-\s*비온", re.MULTILINE)


# ---------------------------------------------------------------------
# /help text
# ---------------------------------------------------------------------
# Pinned-as-spec convention (see CLAUDE.md): every user-visible change
# in trade-bot or its dashboard must update this string in the same
# commit so the operator can /help and see what's actually live. The
# trailing '최종 갱신' line records the last commit's date.
_HELP_TEXT = """🇰🇷 <b>한국 수출입 데이터 대쉬보드 봇</b>

<b>1. 무엇을 하나</b>
BeOn (<code>t.me/BeOn_BeClear</code>) 한국 수출입 알림을 비공개 채널로 자동 수집·정리. BeOn 원본 그래프·표 이미지 그대로 보존, 메타데이터(품목·지역·국가·종목·기간·잠정/확정)만 regex 파싱. OCR 안 함, 가공 0.

<b>2. 대쉬보드</b>
<a href="http://34.50.23.221:8765/dashboard/">http://34.50.23.221:8765/dashboard/</a>
모바일 OK · 5분마다 자동 갱신 · BasicAuth 보호 · 다크모드 자동 (19~07 KST)
헤더에 다음 발표 D-N + 오늘 활동 (신규/확정 도착/첫 등장 품목) 자동 표시

<b>3. BeOn 발표 사이클 (KST)</b>
• 매월 11일경 — 1-10일 잠정
• 매월 21일경 — 1-20일 잠정
• 익월 1일경 — 전월 전체 잠정
• 익월 15일경 — 전월 전체 확정 (관세청 공식)

<b>4. 두 가지 뷰</b>
• <b>품목별</b> — 품목당 1 섹션, (지역/국가) variant 미니카드
• <b>회사별</b> — 회사당 1 섹션, 관련 (품목·지역) 미니카드

<b>5. 카드 정렬</b>
같은 섹션 안에서:
① 전국 (no country) ② 전국_&lt;국가&gt; ③ 시군구
각 단계 내 게시 최신순. 새 발표가 도착하면 그 dedup 키의 최신 카드로 자동 교체, 과거는 모달 history에 누적.

<b>6. 배지</b>
🟢 수출 · 🟠 수입 · 잠정 · 확정 · 합산
🟡 확정 D-N (잠정의 예상 확정일 카운트다운)
🔴 확정 D+N 지연 (예정일 초과)

<b>7. 부가 기능</b>
• 검색: 품목/회사/국가 부분일치 (회사명 정확 일치 시 회사 뷰 자동 좁힘)
• 칩 필터: 수출/수입, 잠정/확정
• 📥 CSV — 현재 필터 결과 풀필드 다운로드 (id·dedup_key·item·item_raw·title_kind·is_composite·composite_parts·region(s)·country(s)·stocks·stocks_meta·has_etc·period_*·expected_final_date·days_to_final·posted_at·ingested_at·commentary·parse_warnings·media_urls 절대경로)
• 모달 — 카드 클릭 시 같은 dedup 키 과거 발표 인라인 비교 (전번 확정 ↔ 이번 잠정 시각 비교)

<b>8. 명령어</b>
/help · /start — 이 안내

<b>9. 자동화 systemd</b>
• trade-bot — 실시간 메시지 수집
• trade-bot-update (2분) — git pull + 재배포
• trade-bot-watchdog (1분) — 폴링 hang 감지 + 자동 재시작
• trade-bot-dashboard — HTTP 서버 (포트 8765)
• trade-bot-dashboard-refresh (5분) — store + HTML 재생성

<i>최종 갱신: 2026-05-17 — Phase A: CSV 풀필드 + 헤더에 다음 발표 D-N + 오늘 활동 quick stats</i>
"""


def _allowed_channel(chat_id: int) -> bool:
    if not CHANNEL_CHAT_IDS:
        log.info("TRADE_CHANNEL_CHAT_IDS not set — channel chat ID is %s", chat_id)
        return True
    return chat_id in CHANNEL_CHAT_IDS


def _origin_matches(post: Message) -> bool:
    """Accept a post if it's a real forward from the configured source
    OR a copy-style forward whose body starts with the BeOn header.

    Discovery mode (SOURCE_ORIGIN unset) accepts everything.
    """
    if not SOURCE_ORIGIN:
        return True

    origin = getattr(post, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin else None
    if chat is None:
        chat = getattr(post, "forward_from_chat", None)

    if chat is not None:
        if SOURCE_ORIGIN.lstrip("-").isdigit():
            if chat.id == int(SOURCE_ORIGIN):
                return True
        elif (chat.username or "").lower() == SOURCE_ORIGIN.lower():
            return True

    body = post.text or post.caption or ""
    if _BEON_HEADER_RE.search(body):
        return True

    return False


def _serialize(post: Message) -> dict:
    """One JSONL row per Telegram message.

    media_group_id ties album siblings together; phase 2's parser joins
    them at read time so we don't need an in-memory buffer here.

    photo_file_unique_id deterministically points at the on-disk media
    path: media/<YYYY-MM-DD>/<file_unique_id>.jpg. The file may or may
    not exist by the time the parser looks (background download could
    still be in flight, or have failed); the parser checks exists().
    """
    origin = getattr(post, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    legacy_chat = getattr(post, "forward_from_chat", None)

    photo_uid = None
    photo_fid = None
    if post.photo:
        # Telegram returns multiple sizes; the last is the largest.
        largest = post.photo[-1]
        photo_uid = largest.file_unique_id
        photo_fid = largest.file_id

    return {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "chat_id": post.chat.id,
        "chat_title": post.chat.title,
        "message_id": post.message_id,
        "media_group_id": post.media_group_id,
        "date": post.date.isoformat() if post.date else None,
        "text": post.text,
        "caption": post.caption,
        "caption_present": bool(post.text or post.caption),
        "forward_origin_type": getattr(origin, "type", None),
        "forward_origin_chat_id": getattr(origin_chat, "id", None)
        or getattr(legacy_chat, "id", None),
        "forward_origin_chat_username": getattr(origin_chat, "username", None)
        or getattr(legacy_chat, "username", None),
        "forward_origin_chat_title": getattr(origin_chat, "title", None)
        or getattr(legacy_chat, "title", None),
        "forward_origin_message_id": getattr(origin, "message_id", None)
        or getattr(post, "forward_from_message_id", None),
        "forward_origin_date": (
            origin.date.isoformat()
            if origin and getattr(origin, "date", None)
            else None
        ),
        "photo_file_unique_id": photo_uid,
        "photo_file_id": photo_fid,
        "has_document": bool(post.document),
    }


def _append_jsonl(record: dict) -> None:
    """Synchronous append. Finishes in <1ms even during a 300-msg burst,
    so the update handler returns to the event loop immediately.
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    with INBOX_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _photo_path(post: Message) -> Path | None:
    """Deterministic on-disk path for a post's photo, keyed by file_unique_id
    so duplicate downloads (Telegram retry, bot restart re-receiving) just
    overwrite the same file instead of creating dupes.
    """
    if not post.photo:
        return None
    largest = post.photo[-1]
    day = (post.date or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    out_dir = MEDIA_ROOT / day
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{largest.file_unique_id}.jpg"


async def _download_photo_bg(post: Message, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the post's largest photo to disk. Runs as a fire-and-forget
    asyncio task so the on_channel_post handler returns immediately.

    Idempotent: skips if the deterministic path already exists. Honors
    Telegram's 429 RetryAfter once, then gives up (BeOn won't re-send;
    the parser handles missing files gracefully).
    """
    global _download_sem
    if _download_sem is None:
        _download_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

    out_path = _photo_path(post)
    if out_path is None or out_path.exists():
        return

    largest = post.photo[-1]

    async with _download_sem:
        try:
            tg_file = await ctx.bot.get_file(largest.file_id)
            await tg_file.download_to_drive(custom_path=str(out_path))
            log.info("downloaded msg=%s file=%s", post.message_id, out_path.name)
            return
        except RetryAfter as e:
            log.warning(
                "download throttled msg=%s retry_after=%s", post.message_id, e.retry_after
            )
            await asyncio.sleep(e.retry_after + 1)
        except TelegramError as e:
            log.error("download failed msg=%s err=%s", post.message_id, e)
            return

        try:
            tg_file = await ctx.bot.get_file(largest.file_id)
            await tg_file.download_to_drive(custom_path=str(out_path))
            log.info("downloaded (retry) msg=%s file=%s", post.message_id, out_path.name)
        except TelegramError as e:
            log.error("download retry failed msg=%s err=%s", post.message_id, e)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/help and /start in a private chat with the bot — replies with
    the pinned-as-spec _HELP_TEXT.
    """
    if update.message is None:
        return
    await update.message.reply_text(
        _HELP_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fast-path handler. Returns to the event loop within ~1ms so PTB's
    update queue keeps draining at full speed during a 300-message burst.

    Also intercepts /help and /start typed directly in the channel
    (BeOn forwards are filtered later) so the operator can pull up
    the spec from inside Telegram without DM'ing the bot.
    """
    post = update.channel_post
    if not post:
        return
    if not _allowed_channel(post.chat.id):
        return

    text = (post.text or post.caption or "").strip()
    first_word = text.split()[0].lower() if text else ""
    if first_word in ("/help", "/start"):
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=_HELP_TEXT,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if not _origin_matches(post):
        return

    record = _serialize(post)
    _append_jsonl(record)

    log.info(
        "ingested msg=%s mg=%s caption=%s photo=%s",
        post.message_id,
        post.media_group_id or "-",
        len(post.text or post.caption or ""),
        record["photo_file_unique_id"] or "-",
    )

    if post.photo:
        asyncio.create_task(_download_photo_bg(post, ctx))


def main() -> None:
    app = Application.builder().token(TOKEN).build()
    # Commands fire in private chats (DM the bot directly).
    app.add_handler(CommandHandler(["help", "start"], cmd_help))
    # Channel posts (BeOn forwards plus in-channel /help / /start).
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, on_channel_post))
    log.info(
        "trade-bot starting — inbox=%s media=%s allowed=%s origin=%s concurrency=%d",
        INBOX_PATH,
        MEDIA_ROOT,
        CHANNEL_CHAT_IDS or "<discovery>",
        SOURCE_ORIGIN or "<any>",
        DOWNLOAD_CONCURRENCY,
    )
    app.run_polling()


if __name__ == "__main__":
    main()
