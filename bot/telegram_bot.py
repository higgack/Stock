"""Stock Analyst Telegram bot.

Sends a ticker like "NVDA" → bot replies with a TradingAgents analysis.
Whitelisted to a single user. Runs as long-polling, suitable for a small VM.
"""

import asyncio
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
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
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}
TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")
TELEGRAM_LIMIT = 4000  # leave headroom under the 4096 hard limit

# In-memory cache: callback_id -> full markdown report.
# Lost on restart, which is fine — user can rerun.
_FULL_CACHE: dict[str, str] = {}
_executor = ThreadPoolExecutor(max_workers=1)  # one analysis at a time


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and user.id in ALLOWED_USER_IDS


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("⛔ 허용된 사용자가 아닙니다.")
        return
    await update.message.reply_text(
        "👋 Stock Analyst Bot\n\n"
        "사용법: 티커만 보내세요 (예: <code>NVDA</code>, <code>AAPL</code>)\n"
        "1~3분 후 분석 결과가 옵니다.\n\n"
        "현재는 미국 주식만 지원합니다.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("⛔ 허용된 사용자가 아닙니다.")
        return

    raw = (update.message.text or "").strip().upper()
    if not TICKER_RE.match(raw):
        await update.message.reply_text(
            "❓ 티커 형식이 맞지 않습니다. 예: <code>NVDA</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    progress = await update.message.reply_text(
        f"📊 <b>{raw}</b> 분석 시작 (1~3분 소요)…",
        parse_mode=ParseMode.HTML,
    )
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        loop = asyncio.get_running_loop()
        summary, full = await loop.run_in_executor(_executor, analyze, raw, None)
    except Exception as exc:
        log.exception("analysis failed")
        await progress.edit_text(f"❌ 분석 실패: {exc!s}")
        return

    cb_id = uuid.uuid4().hex[:12]
    _FULL_CACHE[cb_id] = full
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 전체 리포트 보기", callback_data=f"full:{cb_id}")]
    ])
    await progress.edit_text(summary, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def on_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_authorized(update):
        return

    data = query.data or ""
    if not data.startswith("full:"):
        return

    full = _FULL_CACHE.get(data.split(":", 1)[1])
    if not full:
        await query.message.reply_text("⌛ 캐시 만료. 다시 분석해주세요.")
        return

    for chunk in _split(full, TELEGRAM_LIMIT):
        await query.message.reply_text(chunk)


def _split(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, buf = [], []
    cur = 0
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
    if not ALLOWED_USER_IDS:
        raise SystemExit("ALLOWED_USER_IDS not set in .env")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("bot starting (allowed user IDs: %s)", ALLOWED_USER_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
