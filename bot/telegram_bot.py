"""Stock Analyst Telegram bot — channel mode.

Flow:
  1. Owner posts a ticker (e.g. "NVDA") in the channel
  2. Bot replies to the channel with a progress message
  3. Background analysis runs (1-3 min via TradingAgents / Gemini)
  4. Progress message is replaced with a summary + "전체 리포트" button
  5. Clicking the button sends the full report to the channel
"""

import asyncio
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.analyzer import analyze

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("stock-bot")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Comma-separated numeric channel IDs the bot should respond to.
# Example: CHANNEL_CHAT_IDS=-1001234567890
# If empty, bot will log the chat ID of every channel post so you can find it.
_raw_ids = os.environ.get("CHANNEL_CHAT_IDS", "")
CHANNEL_CHAT_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip()}

TICKER_PREFIX = "-"
TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.]{0,9}$")
TELEGRAM_LIMIT = 4000

# In-memory cache: callback_id -> full markdown report
_FULL_CACHE: dict[str, str] = {}
_executor = ThreadPoolExecutor(max_workers=1)  # one analysis at a time


def _allowed_channel(chat_id: int) -> bool:
    if not CHANNEL_CHAT_IDS:
        log.info("CHANNEL_CHAT_IDS not set — channel chat ID is %s", chat_id)
        return True  # accept any channel while discovering the ID
    return chat_id in CHANNEL_CHAT_IDS


async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return

    if not _allowed_channel(post.chat.id):
        return

    text = (post.text or "").strip()
    if not text.startswith(TICKER_PREFIX):
        return  # not a ticker request — ignore
    raw = text[len(TICKER_PREFIX):].strip().upper()
    if not TICKER_RE.match(raw):
        return  # malformed ticker after the "-" prefix

    # Immediately post a progress message to the channel
    progress = await ctx.bot.send_message(
        chat_id=post.chat.id,
        text=f"📊 <b>{raw}</b> 분석 시작… (1~3분 소요)",
        parse_mode=ParseMode.HTML,
    )

    try:
        loop = asyncio.get_running_loop()
        summary, full = await loop.run_in_executor(_executor, analyze, raw, None)
    except Exception as exc:
        log.exception("analysis failed for %s", raw)
        await ctx.bot.edit_message_text(
            text=f"❌ <b>{raw}</b> 분석 실패: {exc!s}",
            chat_id=post.chat.id,
            message_id=progress.message_id,
            parse_mode=ParseMode.HTML,
        )
        return

    cb_id = uuid.uuid4().hex[:12]
    _FULL_CACHE[cb_id] = full

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 전체 리포트 보기", callback_data=f"full:{cb_id}")
    ]])
    await ctx.bot.edit_message_text(
        text=summary,
        chat_id=post.chat.id,
        message_id=progress.message_id,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def on_full_report(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button handler — sends the full report to the channel."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("full:"):
        return

    full = _FULL_CACHE.get(data.split(":", 1)[1])
    if not full:
        await query.message.reply_text("⌛ 캐시 만료. 티커를 다시 입력해주세요.")
        return

    for chunk in _split(full, TELEGRAM_LIMIT):
        await query.message.reply_text(chunk)


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """DM /start — useful for verifying the bot is alive."""
    await update.message.reply_text(
        "✅ Stock Analyst Bot 작동 중\n\n"
        "채널에 <code>-티커</code> 형식으로 입력하면 분석합니다.\n"
        "예: <code>-NVDA</code>  <code>-AAPL</code>  <code>-TSLA</code>",
        parse_mode=ParseMode.HTML,
    )


def _split(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, buf, cur = [], [], 0
    for line in text.splitlines(keepends=True):
        if cur + len(line) > size and buf:
            chunks.append("".join(buf))
            buf, cur = [], 0
        buf.append(line)
        cur += len(line)
    if buf:
        chunks.append("".join(buf))
    return chunks


def main() -> None:
    if not CHANNEL_CHAT_IDS:
        log.warning(
            "CHANNEL_CHAT_IDS is not set — post a ticker in your channel and "
            "check the log for the chat ID, then add it to .env"
        )

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_full_report, pattern=r"^full:"))
    app.add_handler(
        MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, on_channel_post)
    )

    log.info("bot starting — watching channels: %s", CHANNEL_CHAT_IDS or "auto-detect")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
