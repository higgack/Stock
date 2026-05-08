"""Stock Analyst Telegram bot — channel mode.

Flow:
  1. Owner posts a ticker (e.g. "NVDA") in the channel
  2. Bot replies to the channel with a progress message
  3. Background analysis runs (1-3 min via TradingAgents / Gemini)
  4. Progress message is replaced with a summary + "전체 리포트" button
  5. Clicking the button sends the full report to the channel
"""

import asyncio
import html as _html
import json
import logging
import os
import re
import sys
from datetime import date as _date

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

from bot import cache as _cache
from bot import recovery as _recovery
from bot.analyzer import clear_busy, mark_busy

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

TICKER_PREFIX = "/"
TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.]{0,9}$")
# `/compare A B` triggers a side-by-side digest of two tickers. Both
# tickers run through the same analyzer pipeline (via cache or fresh
# subprocess) and the result is condensed to verdict + stance bar.
COMPARE_RE = re.compile(r"^compare\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)
# Patterns for parsing a previously-rendered summary string back into the
# pieces we need for the compact compare view. The summary format is
# produced by `bot.analyzer._format_summary` so these are stable.
_SUMMARY_RATING_RE = re.compile(r"🎯 최종 판정:\s*\*\*([^*]+?)\*\*")
_SUMMARY_STANCE_LINE_RE = re.compile(
    r"^(?:📈|💬|📰|💰).+·.+$", re.MULTILINE
)
# Headroom under Telegram's 4096-char sendMessage cap, leaving room for the
# small per-chunk overhead added by the Markdown→HTML conversion.
TELEGRAM_LIMIT = 3500
# Stop waiting for a single analysis after 10 minutes. The actual work
# happens in a worker thread we can't cancel, but we surface the timeout
# to the user instead of leaving the progress message hanging forever.
ANALYSIS_TIMEOUT_SEC = 600

# Serializes ticker requests so we never spawn more than one analysis worker
# at a time. The previous ThreadPoolExecutor(max_workers=1) gave the same
# guarantee; this lock preserves it under the subprocess model.
_analysis_lock = asyncio.Lock()


async def _run_analysis_subprocess(
    ticker: str, target_date: str
) -> tuple[str, str]:
    """Run a single analysis in a fresh Python subprocess and return
    (summary, full).

    The subprocess fully insulates the bot's asyncio loop from the heavy
    work: GIL contention, memory growth, and langgraph stalls in the
    worker can't reach Telegram polling. On the configured timeout we
    SIGKILL the worker so even a fully wedged subprocess can't block the
    bot — `analyze_worker.py` is stateless and writes nothing the bot
    can't recover from a kill.

    Raises asyncio.TimeoutError on timeout and RuntimeError otherwise.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bot.analyze_worker",
        ticker,
        target_date,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,  # let worker logs flow straight to systemd journal
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=ANALYSIS_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        log.warning("analysis subprocess timed out — killing pid %s", proc.pid)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        # Reap so we don't leave a zombie. wait() can't deadlock here
        # because the kill we just sent makes the child exit promptly.
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            log.warning("worker pid %s did not reap within 5s", proc.pid)
        raise

    if proc.returncode != 0:
        raise RuntimeError(
            f"analysis worker exited with code {proc.returncode}"
        )

    payload = (stdout or b"").decode("utf-8", errors="replace").strip()
    # Worker may have logged unexpectedly to stdout above the JSON line.
    # Take the LAST non-empty line — that's the result envelope.
    last_line = next(
        (line for line in reversed(payload.splitlines()) if line.strip()),
        "",
    )
    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"analysis worker output not JSON: {payload[:300]!r}"
        ) from exc

    if not result.get("ok"):
        raise RuntimeError(result.get("error", "unknown analysis worker failure"))
    return result["summary"], result["full"]


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
    body = text[len(TICKER_PREFIX):].strip()

    # /compare A B → branch off to the comparison handler.
    cmp_match = COMPARE_RE.match(body)
    if cmp_match:
        tk_a = cmp_match.group(1).upper()
        tk_b = cmp_match.group(2).upper()
        if not (TICKER_RE.match(tk_a) and TICKER_RE.match(tk_b)):
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text="⚠️ 사용법: <code>/compare NVDA AMD</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if tk_a == tk_b:
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text="⚠️ 두 종목이 같습니다.",
                parse_mode=ParseMode.HTML,
            )
            return
        await _handle_compare(ctx, post, tk_a, tk_b)
        return

    raw = body.upper()
    if not TICKER_RE.match(raw):
        return  # malformed ticker after the "/" prefix

    # Peek the daily cache so the progress message can be honest about
    # whether the user is going to wait 1-3 minutes or get an instant result.
    today = _date.today().isoformat()
    is_cached = _cache.get(raw, today) is not None
    if is_cached:
        progress_text = (
            f"📊 <b>{_html.escape(raw)}</b> 캐시된 결과 불러오는 중…"
        )
    else:
        progress_text = (
            f"📊 <b>{_html.escape(raw)}</b> 분석 시작… (보통 1~3분, 큰 종목은 더 걸릴 수 있음)"
        )

    # Immediately post a progress message to the channel
    progress = await ctx.bot.send_message(
        chat_id=post.chat.id,
        text=progress_text,
        parse_mode=ParseMode.HTML,
    )

    # Track the in-flight analysis so a future bot restart can edit the
    # orphaned progress message instead of leaving it stuck on '분석 시작…'.
    # Touch .busy in the same breath: analyze() will also write it, but
    # there's a race window before the worker thread picks the job up
    # where auto-update / watchdog could see no marker and restart us
    # mid-analysis. Writing it here on the asyncio loop closes that gap.
    _recovery.write(post.chat.id, progress.message_id, raw)
    mark_busy()
    try:
        try:
            async with _analysis_lock:
                summary, full = await _run_analysis_subprocess(raw, today)
        except asyncio.TimeoutError:
            log.warning("analysis timed out for %s after %ss", raw, ANALYSIS_TIMEOUT_SEC)
            try:
                await ctx.bot.edit_message_text(
                    text=(
                        f"❌ <b>{_html.escape(raw)}</b> 분석 실패: 10분 타임아웃\n\n"
                        f"분석이 강제 중단되어 추가 토큰 소비가 멈춥니다. "
                        f"잠시 후 다시 시도하거나 다른 종목으로 시도해주세요."
                    ),
                    chat_id=post.chat.id,
                    message_id=progress.message_id,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as edit_exc:
                log.warning("could not edit timeout message: %s", edit_exc)
            # The analysis subprocess was already SIGKILLed inside
            # _run_analysis_subprocess on timeout, so nothing further is
            # needed — the bot itself stays up and keeps serving updates.
            return
        except Exception as exc:
            log.exception("analysis failed for %s", raw)
            await ctx.bot.edit_message_text(
                text=_format_failure(raw, exc),
                chat_id=post.chat.id,
                message_id=progress.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        # Callback data carries the ticker + date so we can re-fetch the report
        # from the on-disk daily cache. Survives bot restarts (the previous
        # in-memory _FULL_CACHE did not). 'today' is captured above when we
        # peeked the cache for the progress-message decision.
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📋 전체 리포트 보기",
                callback_data=f"full:{raw}:{today}",
            )
        ]])
        await ctx.bot.edit_message_text(
            text=_md_to_html(summary),
            chat_id=post.chat.id,
            message_id=progress.message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    finally:
        _recovery.clear()
        clear_busy()


async def _ensure_analysis(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    progress_msg_id: int,
    ticker: str,
    target_date: str,
) -> tuple[str, str] | None:
    """Return (summary, full) for a ticker using cache when fresh, otherwise
    by spawning the analyzer subprocess. Returns None on failure (the caller
    is responsible for editing the progress message into a user-facing error).
    """
    cached = _cache.get(ticker, target_date)
    if cached is not None:
        return cached

    # Fresh run — guard the .busy + .progress files exactly like the single-
    # ticker handler does so a deploy / watchdog kick mid-compare can't catch
    # us between subprocess spawn and completion.
    _recovery.write(chat_id, progress_msg_id, ticker)
    mark_busy()
    try:
        async with _analysis_lock:
            return await _run_analysis_subprocess(ticker, target_date)
    except asyncio.TimeoutError:
        log.warning("compare: analysis timed out for %s", ticker)
        return None
    except Exception as exc:
        log.exception("compare: analysis failed for %s: %s", ticker, exc)
        return None
    finally:
        _recovery.clear()
        clear_busy()


def _compose_compare_view(
    tk_a: str, summary_a: str, tk_b: str, summary_b: str, target_date: str
) -> str:
    """Render the compact two-ticker comparison from existing summary text.

    Pulls just the rating line and stance bar out of each per-ticker
    summary; the full reports are still available via the inline buttons
    rendered alongside this view.
    """

    def _extract(summary: str) -> tuple[str, str]:
        rating_match = _SUMMARY_RATING_RE.search(summary)
        rating = rating_match.group(1).strip() if rating_match else "?"
        stance_match = _SUMMARY_STANCE_LINE_RE.search(summary)
        stance = stance_match.group(0).strip() if stance_match else ""
        return rating, stance

    rating_a, stance_a = _extract(summary_a)
    rating_b, stance_b = _extract(summary_b)

    parts = [
        f"⚖️ **{tk_a} vs {tk_b}** ({target_date})",
        "━━━━━━━━━━━━━━",
        "",
        f"📊 **{tk_a}**  →  🎯 {rating_a}",
    ]
    if stance_a:
        parts.append(stance_a)
    parts.append("")
    parts.append(f"📊 **{tk_b}**  →  🎯 {rating_b}")
    if stance_b:
        parts.append(stance_b)
    parts.append("")
    parts.append("아래 버튼으로 각 종목 전체 리포트를 볼 수 있습니다.")
    return "\n".join(parts)


async def _handle_compare(
    ctx: ContextTypes.DEFAULT_TYPE,
    post,
    tk_a: str,
    tk_b: str,
) -> None:
    today = _date.today().isoformat()
    a_cached = _cache.get(tk_a, today) is not None
    b_cached = _cache.get(tk_b, today) is not None
    cached_count = int(a_cached) + int(b_cached)
    if cached_count == 2:
        progress_text = (
            f"⚖️ <b>{_html.escape(tk_a)}</b> vs <b>{_html.escape(tk_b)}</b> "
            f"캐시된 결과 비교 중…"
        )
    elif cached_count == 1:
        progress_text = (
            f"⚖️ <b>{_html.escape(tk_a)}</b> vs <b>{_html.escape(tk_b)}</b> "
            f"비교 분석 시작… (캐시 1개 + 새 분석 1개, 1~3분 소요)"
        )
    else:
        progress_text = (
            f"⚖️ <b>{_html.escape(tk_a)}</b> vs <b>{_html.escape(tk_b)}</b> "
            f"비교 분석 시작… (새 분석 2개, 3~6분 소요)"
        )

    progress = await ctx.bot.send_message(
        chat_id=post.chat.id,
        text=progress_text,
        parse_mode=ParseMode.HTML,
    )

    pair: dict[str, tuple[str, str]] = {}
    for tk in (tk_a, tk_b):
        result = await _ensure_analysis(ctx, post.chat.id, progress.message_id, tk, today)
        if result is None:
            try:
                await ctx.bot.edit_message_text(
                    text=(
                        f"❌ <b>{_html.escape(tk)}</b> 분석 실패로 비교를 완료하지 못했습니다. "
                        f"잠시 후 다시 시도해주세요."
                    ),
                    chat_id=post.chat.id,
                    message_id=progress.message_id,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as edit_exc:
                log.warning("compare: could not edit failure message: %s", edit_exc)
            return
        pair[tk] = result

    summary_a, _ = pair[tk_a]
    summary_b, _ = pair[tk_b]
    view = _compose_compare_view(tk_a, summary_a, tk_b, summary_b, today)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"📋 {tk_a} 전체", callback_data=f"full:{tk_a}:{today}"),
        InlineKeyboardButton(f"📋 {tk_b} 전체", callback_data=f"full:{tk_b}:{today}"),
    ]])
    await ctx.bot.edit_message_text(
        text=_md_to_html(view),
        chat_id=post.chat.id,
        message_id=progress.message_id,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def on_full_report(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button handler — sends the full report to the channel.

    Looks the report up from the on-disk daily cache by (ticker, date)
    embedded in the callback data, so 'view full report' buttons keep
    working even after a bot restart.
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("full:"):
        return

    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.message.reply_text("⌛ 잘못된 콜백입니다. 티커를 다시 입력해주세요.")
        return
    _, ticker, date_ = parts

    cached = _cache.get(ticker, date_)
    if cached is None:
        await query.message.reply_text(
            "⌛ 결과 캐시를 찾을 수 없습니다 (오래되어 만료됐거나 다른 날짜). "
            "티커를 다시 입력해주세요."
        )
        return
    _, full = cached

    # Remove the inline keyboard now that we've confirmed the report is
    # available. Two effects:
    #   1) clicking the button a second time during/after delivery does
    #      nothing — Telegram simply won't have a button to click.
    #   2) the user gets a clear visual cue that the report has been sent.
    # Failure to edit (e.g. message too old) is non-fatal.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        log.debug("could not clear keyboard: %s", exc)

    for chunk in _split(full, TELEGRAM_LIMIT):
        if not chunk.strip():
            continue  # Telegram rejects whitespace-only text
        html_chunk = _md_to_html(chunk)
        if not html_chunk.strip():
            continue  # all-table chunk that collapsed to nothing after dedup
        try:
            await query.message.reply_text(
                html_chunk, parse_mode=ParseMode.HTML
            )
        except Exception as exc:
            log.warning("chunk send failed (%d chars): %s", len(chunk), exc)
            try:
                await query.message.reply_text(
                    f"⚠️ 일부 본문 누락: {_html.escape(str(exc))}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """DM /start — useful for verifying the bot is alive."""
    await update.message.reply_text(
        "✅ Stock Analyst Bot 작동 중\n\n"
        "채널에 <code>/티커</code> 형식으로 입력하면 분석합니다.\n"
        "예: <code>/NVDA</code>  <code>/AAPL</code>  <code>/TSLA</code>\n\n"
        "두 종목 비교는 <code>/compare A B</code> 형태로:\n"
        "예: <code>/compare NVDA AMD</code>",
        parse_mode=ParseMode.HTML,
    )


def _format_failure(ticker: str, exc: Exception) -> str:
    """Translate raw analyzer exceptions into a user-readable Korean message."""
    text = str(exc)
    ticker_html = _html.escape(ticker)
    if "exceeds the maximum number of tokens" in text or "INVALID_ARGUMENT" in text:
        return (
            f"❌ <b>{ticker_html}</b> 분석 실패: 입력 데이터가 Gemini 컨텍스트 한도(1M 토큰)를 초과했습니다.\n\n"
            f"이 종목은 뉴스 양이 너무 많아서 한 번에 처리되지 않습니다. 잠시 후 다시 시도하거나, "
            f"뉴스 양이 적은 다른 종목으로 시도해보세요. (수정 작업이 진행 중입니다.)"
        )
    if "ResourceExhausted" in text or "429" in text or "quota" in text.lower():
        return (
            f"❌ <b>{ticker_html}</b> 분석 실패: Gemini API 사용량 한도에 도달했습니다.\n\n"
            f"무료 등급 분당/일별 할당량을 초과했을 가능성. 잠시 후 재시도해주세요."
        )
    if "Timed out" in text or "Timeout" in text:
        return (
            f"❌ <b>{ticker_html}</b> 분석 실패: 요청이 시간 초과됐습니다.\n\n"
            f"네트워크 또는 모델 응답 지연. 잠시 후 다시 시도해주세요."
        )
    # Fallback — show first 300 chars only, keep tidy
    short = text[:300] + ("…" if len(text) > 300 else "")
    return f"❌ <b>{ticker_html}</b> 분석 실패: {_html.escape(short)}"


def _md_to_html(text: str) -> str:
    """Render a subset of Markdown as Telegram HTML for readable formatting.

    - **bold** → <b>bold</b>
    - leading #/##/### → bold line
    - "* " or "- " bullets → "• "
    - Pipe-delimited tables → bullet lists (mobile-friendly; first cell
      becomes a bold key, the rest joined with em dashes)
    Other characters pass through after HTML escaping.
    """
    # Normalize whitespace before block detection
    text = re.sub(r"[ \t]+\n", "\n", text)  # strip trailing spaces
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse 3+ blank lines

    # Agents sometimes embed HTML-style break tags ('<br>', '<br/>') inside
    # table cells or bullets. Telegram's HTML parse mode does NOT support
    # <br>, so without this conversion they end up shown as literal text
    # ('foo<br>bar'). Convert to real newlines before any escaping.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    lines = text.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        kind = "table" if line.lstrip().startswith("|") else "prose"
        if blocks and blocks[-1][0] == kind:
            blocks[-1][1].append(line)
        else:
            blocks.append((kind, [line]))

    out: list[str] = []
    for kind, lns in blocks:
        if kind == "table":
            rendered = _table_to_bullets(lns)
            if rendered:
                out.append(rendered)
        else:
            body = "\n".join(lns)
            body = _html.escape(body)
            body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
            body = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", body)
            body = re.sub(r"(?m)^(\s*)[\*\-]\s+", r"\1• ", body)
            out.append(body)
    return "\n".join(out)


def _table_to_bullets(lines: list[str]) -> str:
    """Convert a Markdown pipe table into a Telegram-friendly bullet list.

    Drops alignment rows like '| :--- | :--- |' and the header row, then
    formats each remaining row as '• <b>{first cell}</b>: {others joined}'.
    Rows whose only content is the first cell render as a bold section line.
    """
    rows: list[list[str]] = []
    for line in lines:
        if re.match(r"^\s*\|[\s\|:\-]+\|\s*$", line):
            continue  # alignment row
        cells = [c.strip() for c in line.split("|")]
        if cells and not cells[0]:
            cells = cells[1:]
        if cells and not cells[-1]:
            cells = cells[:-1]
        if cells and any(c for c in cells):
            rows.append(cells)

    if len(rows) <= 1:
        return ""  # only header (or empty) — nothing useful

    # Skip header (row 0); render the rest as bullets.
    bullets: list[str] = []
    for row in rows[1:]:
        # Strip Markdown bold from cells before HTML-escaping; the first
        # cell gets wrapped in <b> on its own, and double-wrapping
        # ('<b><b>...</b></b>') is invalid for Telegram.
        cells = [_html.escape(re.sub(r"\*\*(.+?)\*\*", r"\1", c)) for c in row]
        rest = [c for c in cells[1:] if c]
        if not cells or not cells[0]:
            continue
        if not rest:
            bullets.append(f"<b>{cells[0]}</b>")
        else:
            bullets.append(f"• <b>{cells[0]}</b>: {' — '.join(rest)}")
    return "\n".join(bullets)


def _split(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, buf, cur = [], [], 0
    for line in text.splitlines(keepends=True):
        # Hard-split lines that alone exceed the size budget — otherwise
        # Telegram rejects them with HTTP 413.
        while len(line) > size:
            if buf:
                chunks.append("".join(buf))
                buf, cur = [], 0
            chunks.append(line[:size])
            line = line[size:]
        if cur + len(line) > size and buf:
            chunks.append("".join(buf))
            buf, cur = [], 0
        buf.append(line)
        cur += len(line)
    if buf:
        chunks.append("".join(buf))
    return chunks


async def _on_startup(application) -> None:
    """Edit any orphaned 'analysis started' progress message left behind by
    a previous bot instance that died mid-analysis. Without this, the
    message would stay 'VICR 분석 시작…' forever after a crash/restart."""
    orphan = _recovery.read()
    if orphan is None:
        return
    log.warning(
        "found orphaned progress for %s (msg %s in chat %s) — marking interrupted",
        orphan.get("ticker"),
        orphan.get("message_id"),
        orphan.get("chat_id"),
    )
    try:
        await application.bot.edit_message_text(
            text=(
                f"❌ <b>{_html.escape(orphan.get('ticker', 'Unknown'))}</b> "
                f"분석이 봇 재시작으로 중단됐습니다. 다시 시도해주세요."
            ),
            chat_id=orphan["chat_id"],
            message_id=orphan["message_id"],
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        log.warning("could not edit orphaned message: %s", exc)
    _recovery.clear()


def main() -> None:
    if not CHANNEL_CHAT_IDS:
        log.warning(
            "CHANNEL_CHAT_IDS is not set — post a ticker in your channel and "
            "check the log for the chat ID, then add it to .env"
        )

    app = Application.builder().token(TOKEN).post_init(_on_startup).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_full_report, pattern=r"^full:"))
    app.add_handler(
        MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, on_channel_post)
    )

    log.info("bot starting — watching channels: %s", CHANNEL_CHAT_IDS or "auto-detect")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
