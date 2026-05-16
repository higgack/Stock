"""Korea import/export dashboard bot — ingestion entry point.

Phase 1: discovery + inbox.
  - Listens to a private Telegram channel where messages are auto-forwarded
    from t.me/BeOn_BeClear by an external forwarder tool.
  - Logs every channel post (with full forward metadata) to a JSONL inbox
    so the parser can be developed against real samples without re-fetching.
  - Prints chat IDs to the journal in discovery mode (TRADE_CHANNEL_CHAT_IDS
    unset) so the operator can grab the private channel's ID on first run.

Parsing / aggregation / dashboard rendering land in later phases.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("trade-bot")

TOKEN = os.environ["TRADE_BOT_TOKEN"]

_raw_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
CHANNEL_CHAT_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip()}

# Optional: restrict to forwards originating from this source channel
# (BeOn_BeClear). Accepts a username (without @) or a numeric ID. When
# unset, every forward is accepted — useful while discovering the origin
# chat ID for the first time.
SOURCE_ORIGIN = os.environ.get("TRADE_SOURCE_ORIGIN", "").lstrip("@").strip()

INBOX_DIR = Path(os.environ.get("TRADE_DATA_DIR", str(Path.home() / ".trade")))
INBOX_PATH = INBOX_DIR / "inbox.jsonl"


def _allowed_channel(chat_id: int) -> bool:
    if not CHANNEL_CHAT_IDS:
        log.info("TRADE_CHANNEL_CHAT_IDS not set — channel chat ID is %s", chat_id)
        return True
    return chat_id in CHANNEL_CHAT_IDS


def _origin_matches(post) -> bool:
    """True if the post is a forward from the configured source channel.

    With SOURCE_ORIGIN unset we accept everything (discovery). Otherwise we
    match against forward_origin.chat for channel-origin forwards, falling
    back to the legacy forward_from_chat field.
    """
    if not SOURCE_ORIGIN:
        return True

    origin = getattr(post, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin else None
    if chat is None:
        chat = getattr(post, "forward_from_chat", None)
    if chat is None:
        return False

    if SOURCE_ORIGIN.lstrip("-").isdigit():
        return chat.id == int(SOURCE_ORIGIN)
    return (chat.username or "").lower() == SOURCE_ORIGIN.lower()


def _serialize(post) -> dict:
    """Capture every field we might need for parsing later.

    Errors on the side of including too much — disk is cheap, re-fetching
    Telegram history is not.
    """
    origin = getattr(post, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    legacy_chat = getattr(post, "forward_from_chat", None)

    return {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "chat_id": post.chat.id,
        "chat_title": post.chat.title,
        "message_id": post.message_id,
        "date": post.date.isoformat() if post.date else None,
        "text": post.text,
        "caption": post.caption,
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
        "has_photo": bool(post.photo),
        "has_document": bool(post.document),
    }


async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return
    if not _allowed_channel(post.chat.id):
        return
    if not _origin_matches(post):
        return

    record = _serialize(post)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    with INBOX_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    log.info(
        "ingested msg_id=%s origin=%s text_len=%s",
        post.message_id,
        record["forward_origin_chat_username"]
        or record["forward_origin_chat_id"],
        len(post.text or post.caption or ""),
    )


def main() -> None:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, on_channel_post))
    log.info(
        "trade-bot starting — inbox=%s allowed_channels=%s source_origin=%s",
        INBOX_PATH,
        CHANNEL_CHAT_IDS or "<discovery>",
        SOURCE_ORIGIN or "<any>",
    )
    app.run_polling()


if __name__ == "__main__":
    main()
