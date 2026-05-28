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
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import date as _date, datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter
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
from bot import usage_tracker
from bot.analyzer import clear_busy, mark_busy
from bot.dashboard import regenerate_index as _dashboard_regen

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
# Accept both US-style alpha-starting tickers (NVDA, BRK.B) and
# numeric-starting tickers from foreign exchanges (005930.KS Samsung,
# 7203.T Toyota, 600519.SS Kweichow Moutai). Allowing a digit at
# position 0 was the missing piece — previously /005930.KS hit the
# 'invalid ticker' branch on the channel router.
TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.]{0,9}$")
# Korean company-name shortcut. When the body after '/' is a hangul-
# bearing word (with optional alphanumeric mix like 'SK하이닉스' or
# 'LG에너지솔루션'), resolve it via DART corp_code lookup before falling
# through to the ticker path. The lookahead requires at least one
# hangul char anywhere so a pure-ASCII US ticker like 'NVDA' stays on
# the ticker path; the start class allows English or hangul so chaebol
# brand-prefixed names (SK/LG/CJ/...) match too.
KOREAN_NAME_RE = re.compile(r"^(?=.*[가-힣])[가-힣A-Za-z][가-힣A-Za-z0-9]{0,19}$")
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

# Refcounted .busy marker. The marker means "at least one analysis is
# in flight" — auto-update / watchdog defer restarts while it exists.
# Naive mark_busy/clear_busy at handler scope races when a second request
# is queued behind a first: the first's clear_busy unlinks the file while
# the second is still about to acquire the lock. We track active requests
# with a counter and only clear the file when nothing is in flight.
_busy_state_lock = asyncio.Lock()
_busy_refcount = 0


async def _busy_acquire() -> None:
    global _busy_refcount
    async with _busy_state_lock:
        _busy_refcount += 1
        if _busy_refcount == 1:
            mark_busy()


async def _busy_release() -> None:
    global _busy_refcount
    async with _busy_state_lock:
        _busy_refcount = max(0, _busy_refcount - 1)
        if _busy_refcount == 0:
            clear_busy()


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

    # Parse the JSON envelope FIRST — when the worker catches an exception
    # internally it prints the real Korean error message to stdout and
    # then exits 1. The previous code raised "exited with code 1" before
    # ever looking at stdout, swallowing the actual error reason.
    payload = (stdout or b"").decode("utf-8", errors="replace").strip()
    last_line = next(
        (line for line in reversed(payload.splitlines()) if line.strip()),
        "",
    )
    result: dict | None = None
    if last_line:
        try:
            result = json.loads(last_line)
        except json.JSONDecodeError:
            result = None

    if proc.returncode != 0:
        if result and not result.get("ok") and result.get("error"):
            raise RuntimeError(result["error"])
        raise RuntimeError(
            f"analysis worker exited with code {proc.returncode}"
            + (f": {payload[:300]!r}" if payload else "")
        )

    if result is None:
        raise RuntimeError(
            f"analysis worker output not JSON: {payload[:300]!r}"
        )

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
    first_word = body.split(None, 1)[0].lower() if body else ""

    # /start or /help in the channel — surface the usage guide. PTB's
    # CommandHandler only fires on Update.message, not channel_post, so
    # without this branch a user typing /help in the channel would either
    # silently no-op or (with our old TICKER_RE fall-through) get
    # interpreted as a 'HELP' ticker.
    if first_word in ("start", "help"):
        # Channel /help — same chunking path as DM /help. Sending
        # _HELP_TEXT whole here used to blow Telegram's 4096-char cap
        # ("Message is too long") and that's the channel branch users
        # actually hit, since `cmd_help`'s CommandHandler only fires on
        # Update.message, not channel_post.
        chat_id = post.chat.id
        try:
            uname = ctx.bot.username
        except Exception:
            uname = None
        await _send_help(
            send_html=lambda t, rm=None: ctx.bot.send_message(
                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML, reply_markup=rm
            ),
            send_plain=lambda t, rm=None: ctx.bot.send_message(
                chat_id=chat_id, text=t, reply_markup=rm
            ),
            label="channel_help",
            reply_markup=_help_keyboard(uname),
        )
        return

    # /usage in channel — post the cost / activity digest.
    if first_word == "usage":
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=_build_usage_report(),
            parse_mode=ParseMode.HTML,
        )
        return

    # /sv_cost in channel — Standard View Gemini API cost (별도 process).
    if first_word == "sv_cost":
        try:
            import httpx as _httpx
            r = _httpx.get(
                "http://127.0.0.1:8002/api/sv-usage/today", timeout=5
            )
            data = r.json() if r.status_code == 200 else {}
        except Exception as exc:
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text=(
                    f"SV 비용 조회 실패: {type(exc).__name__}: {exc}\n"
                    "backend (~/standardview) 8002 가동 중인지 확인."
                ),
            )
            return
        today_krw = float(data.get("today_krw", 0) or 0)
        month_krw = float(data.get("month_krw", 0) or 0)
        today_calls = int(data.get("today_calls", 0) or 0)
        month_calls = int(data.get("month_calls", 0) or 0)
        today_pt = int(data.get("today_prompt_tok", 0) or 0)
        today_ot = int(data.get("today_output_tok", 0) or 0)
        text_out = (
            "💰 <b>Standard View 비용</b> (Gemini API, NOAH /usage 와 별개)\n"
            f"오늘: <b>₩{today_krw:,.1f}</b> · {today_calls}회\n"
            f"이번 달: <b>₩{month_krw:,.0f}</b> · {month_calls}회\n"
            f"오늘 tokens: in {today_pt:,} / out {today_ot:,}\n"
            "<i>모델: gemini-2.5-flash · 매크로/산업/코멘트 호출</i>"
        )
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=text_out,
            parse_mode=ParseMode.HTML,
        )
        return

    # /screener_cost in channel — Bottleneck Screener Pro cost (parallel
    # to /sv_cost). Reads ~/.tradingagents/screener_usage.jsonl directly.
    if first_word == "screener_cost":
        data = _read_screener_cost_today_month()
        today_krw = float(data.get("today_krw", 0) or 0)
        month_krw = float(data.get("month_krw", 0) or 0)
        today_calls = int(data.get("today_calls", 0) or 0)
        month_calls = int(data.get("month_calls", 0) or 0)
        today_pt = int(data.get("today_prompt_tok", 0) or 0)
        today_ot = int(data.get("today_output_tok", 0) or 0)
        text_out = (
            "💰 <b>Bottleneck Screener 비용</b> (Gemini Pro · 웹 검색 grounding)\n"
            f"오늘: <b>₩{today_krw:,.1f}</b> · {today_calls}회\n"
            f"이번 달: <b>₩{month_krw:,.0f}</b> · {month_calls}회\n"
            f"오늘 tokens: in {today_pt:,} / out {today_ot:,}\n"
            "<i>모델: gemini-2.5-pro · Phase β 2-pass + Phase 3 후보별 실시간 fetch</i>"
        )
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=text_out,
            parse_mode=ParseMode.HTML,
        )
        return

    # /sites in channel — external reference sites bookmark list.
    if first_word == "sites":
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=_SITES_TEXT,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # /screener in channel — Bottleneck Screener Phase α MVP (AI Data Center).
    # ~3-5 minute Pro call; send a progress note first so the channel knows
    # work is in flight before the master table arrives.
    if first_word == "screener":
        chat_id = post.chat.id
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                "📊 <b>Bottleneck Screener</b> 시작 — AI 데이터센터 도메인 "
                "(Phase β · 실시간 데이터)\n⏱ <b>5-10분 소요</b> — "
                "Phase 1·2 (Pro 후보 식별) → Phase 3 (병렬 실시간 fetch) "
                "→ Phase 4·5 (Pro 분석)\n"
                "💡 데이터 fetch hung 시 120s 후 partial 결과로 자동 진행"
            ),
            parse_mode=ParseMode.HTML,
        )
        await _run_screener_and_send(
            send=lambda t: ctx.bot.send_message(
                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            ),
            domain=body[len("screener"):].strip().lower(),
        )
        return

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

    # Bare /compare with no args (or malformed args that didn't match
    # COMPARE_RE) — show the short usage hint instead of falling into
    # the ticker branch and trying to analyze 'COMPARE' as a symbol.
    if first_word == "compare":
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text="⚠️ 사용법: <code>/compare 티커1 티커2</code>\n예: <code>/compare NVDA AMD</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Korean-name shortcut: '/삼성전자' → resolve to '005930.KS' before
    # hitting the analyzer. The resolver returns one of:
    #   resolved → continue with the resolved ticker
    #   multiple → reply with candidate list and stop (user retypes)
    #   not_found / no_price → reply with a short error and stop
    # We branch here (before body.upper()) because uppercasing Korean is
    # a no-op but the subsequent TICKER_RE check would reject hangul.
    if KOREAN_NAME_RE.match(body):
        try:
            from bot.kr_resolver import resolve_korean_name
            result = resolve_korean_name(body)
        except Exception as exc:
            log.warning("kr name resolve crashed for %r: %s", body, exc)
            result = {"status": "not_found"}
        status = result.get("status")
        if status == "resolved":
            raw = result["ticker"].upper()
            # Confirm the resolution so the user sees what we'll analyze.
            try:
                await ctx.bot.send_message(
                    chat_id=post.chat.id,
                    text=f"🔎 {result['name']} → <code>{raw}</code>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        elif status == "multiple":
            lines = [
                f"• {c['name']} → <code>/{c['ticker']}</code>"
                for c in result.get("candidates", [])
            ]
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text=(
                    f"⚠️ '{body}' 매칭이 여러 개입니다. 다음 중 하나로 다시 입력해주세요:\n"
                    + "\n".join(lines)
                ),
                parse_mode=ParseMode.HTML,
            )
            return
        elif status == "no_price":
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text=(
                    f"⚠️ '{body}' DART에는 있지만 yfinance에 상장 데이터가 없습니다."
                    " 상장폐지 / 우선주 / 신규 상장 가능성. 티커를 직접 입력해주세요."
                ),
                parse_mode=ParseMode.HTML,
            )
            return
        else:  # not_found
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text=(
                    f"⚠️ '{body}' DART에서 종목명을 찾을 수 없습니다."
                    " 정확한 종목명으로 다시 입력하거나 6자리 종목코드(.KS/.KQ)로 시도해주세요."
                ),
                parse_mode=ParseMode.HTML,
            )
            return
    else:
        raw = body.upper()
        if not TICKER_RE.match(raw):
            return  # malformed ticker after the "/" prefix

        # English alias for well-known KR companies (NAVER → 035420.KS).
        # Without this, '/NAVER' goes to yfinance as a literal ticker,
        # returns empty .info, and the pre-flight validator below
        # rejects it. The alias resolution short-circuits that and
        # gives the user a smoother path. Ambiguous aliases (LG / SK
        # / HYUNDAI) intentionally resolve to None — user must use the
        # specific Korean name or 6-digit ticker.
        try:
            from bot.market import resolve_english_alias
            resolved = resolve_english_alias(raw)
            if resolved:
                await ctx.bot.send_message(
                    chat_id=post.chat.id,
                    text=f"🔎 {raw} → <code>{resolved}</code>",
                    parse_mode=ParseMode.HTML,
                )
                raw = resolved
        except Exception as exc:
            log.warning("english alias lookup failed for %r: %s", raw, exc)

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
            f"📊 <b>{_html.escape(raw)}</b> 분석 시작…"
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
    await _busy_acquire()
    try:
        try:
            async with _analysis_lock:
                summary, full = await _run_analysis_subprocess(raw, today)
        except asyncio.TimeoutError:
            log.warning("analysis timed out for %s after %ss", raw, ANALYSIS_TIMEOUT_SEC)
            usage_tracker.log_failure(raw, "10분 타임아웃")
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
            usage_tracker.log_failure(raw, str(exc))
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
        await _busy_release()


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
    await _busy_acquire()
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
        await _busy_release()


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

    from bot.analyzer import _display_ticker
    disp_a = _display_ticker(tk_a)
    disp_b = _display_ticker(tk_b)
    parts = [
        f"⚖️ **{disp_a} vs {disp_b}** ({target_date})",
        "━━━━━━━━━━━━━━",
        "",
        f"📊 **{disp_a}**  →  🎯 {rating_a}",
    ]
    if stance_a:
        parts.append(stance_a)
    parts.append("")
    parts.append(f"📊 **{disp_b}**  →  🎯 {rating_b}")
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
    both_cached = (
        _cache.get(tk_a, today) is not None
        and _cache.get(tk_b, today) is not None
    )
    if both_cached:
        progress_text = (
            f"⚖️ <b>{_html.escape(tk_a)}</b> vs <b>{_html.escape(tk_b)}</b> "
            f"캐시된 결과 비교 중…"
        )
    else:
        progress_text = (
            f"⚖️ <b>{_html.escape(tk_a)}</b> vs <b>{_html.escape(tk_b)}</b> "
            f"비교 분석 시작…"
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
        # Send with RetryAfter handling: a long full report split into 10+
        # chunks fired back-to-back trips Telegram's per-chat flood guard.
        # Sleep briefly between chunks; on a 429, honour the server-given
        # retry_after and try the same chunk again.
        attempts = 0
        while True:
            try:
                await query.message.reply_text(
                    html_chunk, parse_mode=ParseMode.HTML
                )
                break
            except RetryAfter as ra:
                wait = max(1, int(getattr(ra, "retry_after", 5)))
                log.warning(
                    "flood control on chunk (%d chars) — waiting %ds",
                    len(chunk), wait,
                )
                await asyncio.sleep(wait + 1)
                attempts += 1
                if attempts >= 3:
                    log.warning("giving up chunk after %d retries", attempts)
                    break
            except Exception as exc:
                log.warning("chunk send failed (%d chars): %s", len(chunk), exc)
                try:
                    await query.message.reply_text(
                        f"⚠️ 일부 본문 누락: {_html.escape(str(exc))}",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
                break
        # Baseline pacing — Telegram tolerates ~1 message/sec sustained
        # to a single chat. 0.7s keeps us comfortably under that ceiling.
        await asyncio.sleep(0.7)


_HELP_TEXT = """🧠 <b>NOAH 주식분석 봇</b>
━━━━━━━━━
<b>【1. 명령어】</b> (탭 자동입력)
/start /help /usage /sv_cost /screener_cost /screener /sites — 도움말 · 비용 (통합/SV/Screener) · Bottleneck 종목 발굴 · 사이트
/NVDA /AAPL — 단일 분석 (채널에서)
/compare NVDA AMD — 두 종목 비교
※ 다른 종목은 /티커 (예: /PLTR · /005930.KS) 또는 한국은 종목명 직접 (/삼성전자)

━━━━━━━━━
<b>【2. 분석 흐름】</b> (~3분, ~₩100~150/회)
 1) 채널에 <code>/티커</code> → 즉시 진행 메시지
 2) <b>사전 fetch (Python, 결정적·스킵 불가)</b>
    매크로 9개 / 리스크 (σ·Sharpe·VaR·MDD·β) / 섹터 ETF / 컨센서스 / 공매도·내부자·기관 / 실적 일정
    ETF·뉴스 0건 → 해당 분석가 자동 스킵
 3) 분석가 4명 (~2분, Flash + <b>Gemini cache</b>) — 📈시장 💬감정 📰뉴스 💰펀더멘털
    빈/사과형 응답 → 1회 retry → 실패 시 토론 스킵 + 알림
 4) Bull/Bear 페르소나 토론 (Flash-Lite)
    Bull: 버핏(해자) + 린치(PEG) / Bear: 그레이엄(안전마진) + 막스(사이클)
 5) Trader → Risk 3인 → Portfolio Manager (Pro, thinking 4096)
    5거래일 평가 윈도 인지 → valuation-only 매도 자제
 6) 채널에 요약 + [📋 전체 리포트]

━━━━━━━━━
<b>【3. 요약 구성】</b>
🎯 최종 판정 (Buy/Overweight/Hold/Underweight/Sell) · 📒 지난 추천 결과 (5거래일 후 자동) · ⚠️/📅/📊 실적 ±10일 경고 · 📰 뉴스 스킵 알림 · 4명 stance + ⚠️ stance↔결정 mismatch (불일치 시만) · 각 분석가 한 줄 요지 · [📋 전체 리포트]

━━━━━━━━━
<b>【4. 자동 데이터 소스】</b>
yfinance (15년) · Alpha Vantage · 네이버·Kabutan 뉴스 · 분기+연간 재무 · 매크로 9종 (시장별 미·한·일·대·중) · ECOS/FRED (KR·JP·TW 금리·CPI) · 섹터 ETF (SPDR/KODEX/NEXT TOPIX-17) · 리스크 6종 · 컨센서스 (yfinance+FnGuide/Kabutan) · 공매도+DTC · 내부자/기관 · 실적 ±10일 · DART/EDINET/MOPS (공시·5%대량보유) · SEC EDGAR (8-K·Form4) · US옵션 IV·P/C비율 · KR개장전 미국선물 · TW鉅亨컨센서스 · KRX (5일 외인·공매도 30일) · KIS 7종 KR수급 (외인flow·연기금·한도소진율·신용·프로그램·공매도) · Forward EPS sanity · 컨센서스 staleness · SV 브리프 (08:00 KST)

━━━━━━━━━
<b>【5. 메모리 피드백 + 자동 평가】</b>
 • 추천은 pending 기록 → <b>12시간마다 백그라운드 자동 해소</b>
 • 5거래일 지난 항목 → raw return + <b>섹터 ETF 알파</b> (SPY 아님; PLUG↔TAN, NVDA↔SOXX 등)
 • 다음 동일 종목 분석 요약 상단에 자동 표시
   예: 📒 지난 추천 (04-24): 매수 → +5.3% (벤치 +1.2%p)
 • 결정 LLM도 같은 컨텍스트 → 과거 실수 반영

━━━━━━━━━
<b>【6. 캐시 &amp; 비용】</b>
 • 오늘 같은 종목 재분석 → 디스크 캐시 즉시 반환 (무료)
 • 새 분석 ~₩100~150 / /compare 둘 다 새거 ~₩200~300
 • <b>Gemini context cache</b>: 결정 3노드(Pro) instrument_context 공유 → input ~75%↓
 • 일일 캐시 자정 KST 만료, 15년 가격 데이터는 영구 캐시
 • /usage → 모델별 분포 + 7일 차트

━━━━━━━━━
<b>【7. 안정성 (자동)】</b>
subprocess 격리·10분·watchdog 12분·auto-update <b>1분</b> · RULE 1~14 · 재벌→RULE9 · stance 결론우선 · 섹터강도 ±50%p · 컨센서스 staleness (tgt&lt;cur+매수=⛔) · 코퍼레이트액션 HARD GUARD 3중 · SMA gap multi-signal (외부 evidence시 ⛔, 단독시 ⚠️ overbought) · 적자 PER→N/M · Peer multiple sanity · 소유구조 환각 차단 · Comps PEER SET 162 산업 · PM 코드-강제 · canonical 현재가/시총/SMA · DART KSIC override · KIS+pykrx fallback · 단위 silent 정규화 · KRX 시장경보·USD/KRW 감응도·ETF 메타·12개월 thesis 금지 · API 키 부재→DATA OFFLINE · 분석가1:1→PM보존

━━━━━━━━━
<b>【8. 채널 알림】</b>
🚀✅ 배포 시작/완료 · ⚠️ hang 감지 · ❌ 분석 실패 (타임아웃/토큰/API/응답 누락)

━━━━━━━━━
<b>【9. 트러블슈팅】</b>
 • 동시 분석 1건 (lock 직렬화), /compare 2종목까지
 • "캐시 없음" → 자정 만료, 재입력
 • "10분 타임아웃" → 1~2분 후 재시도 (Gemini 503 가능)
 • "분석가 응답 누락" → retry 후 실패. 다른 종목 시도
 • "봇 재시작으로 중단" → 자동 복구 메시지, 다시 입력

━━━━━━━━━
<b>【10. 차별화 포인트】</b>
페르소나 토론 (Buffett/Lynch vs Graham/Marks) · 결정 3노드만 Pro (~₩30 추가로 품질 점프) · 메모리 피드백 자기학습 (12h 자동) · 결정적 데이터 Python 사전 fetch (LLM 스킵 불가) · Wall Street 컨센서스 ground truth 대조 · stance↔결정 mismatch 자동 감지 · 5거래일 horizon 명시 (장기 thesis 매도 자제) · 섹터 ETF 알파 · 실패 시 hallucination 대신 명시적 abort

━━━━━━━━━
<b>【11. 대시보드】</b> 🦉
 • <b>NOAH archive</b>: <a href="http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/">8081/...</a> (ID/PW). 💰비용=NOAH+Screener+SV. 헤더에서 Screener/SV/🇰🇷수출입 이동
 • <b>Screener</b>: archive/screener.html — 날짜별 run·분석·Top-3 5/15/30d·본문 스니펫 검색(🟡하이라이트→클릭 펼침)·🗑️
 • <b>SV</b>: <a href="http://34.50.23.221:8002/dashboard">8002/dashboard</a> · 매크로·산업·Deal · 07:30/20:30 + 텔레 · 22:00 주간·섹터 · auto-deploy 1분
 • <b>🇰🇷 수출입</b>: <a href="http://34.50.23.221:8765/dashboard/">8765/dashboard</a> · 외부 보조 (자동 갱신)
 • NOAH 카드: 📊·💰·⏱·🎯알파·5/15/30d·검색·🗑️
 • 데이터: <code>~/.tradingagents/{archive,screener_archive,usage.jsonl,memory/}</code> · /sites

━━━━━━━━━
<b>【12. 예정 작업】</b>
 • Bottleneck Screener Wave 1 — AI 데이터센터 외 EV·방산·바이오·신재생 등 추가 도메인 확장 예정 (theme registry 분리)
"""


_SITES_TEXT = """🔗 <b>참고 사이트</b>

 • <a href="https://stockeasy.intellio.kr/">Stockeasy</a>
 • <a href="https://stockhub.kr/">Stockhub</a>
 • <a href="https://jusikbot.com/">Jusikbot — Real-time Stock Dashboard</a>
 • <a href="https://tebi.raoni.xyz/">트비 주식뉴스 어그리게이터 리포트</a>
 • <a href="https://www.tradeodds.io/">Stop guessing. See what history did next.</a>
 • <a href="https://aibottlenecks.app/">AI Bottlenecks</a>
 • <a href="https://analytics.blancwm.com/">Analytics Portal</a>"""


# Section divider used throughout _HELP_TEXT. Must match the literal
# string in the help body exactly — a mismatch silently disables chunking
# and the whole text goes out as a single message (the pre-2026-05-25 bug:
# the body used 9 bars but the splitter looked for 14).
_HELP_DIVIDER = "━" * 9


def _help_utf16_len(text: str) -> int:
    """Telegram counts message length in UTF-16 code units, not Python
    code points. Emoji / surrogate pairs count as 2. Use this (not len)
    when comparing against the 4096 cap."""
    return len(text.encode("utf-16-le")) // 2


def _chunk_help_text() -> list[str]:
    """Return _HELP_TEXT as Telegram-sized chunks.

    If the whole text fits inside Telegram's 4096 UTF-16 cap (with a small
    margin) it is returned as ONE chunk, so /help stays a single pinned
    announcement. Only when it grows past the cap do we split at the
    section dividers into chunks ≤3500 chars."""
    if _help_utf16_len(_HELP_TEXT) <= 4000:
        return [_HELP_TEXT]

    sections = _HELP_TEXT.split(_HELP_DIVIDER)
    chunks: list[str] = []
    current = ""
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        # The first non-empty section carries the title; later sections
        # get the divider prepended to keep the visual structure intact.
        piece = section if i == 0 else _HELP_DIVIDER + "\n" + section
        if current and len(current) + len(piece) + 2 > 3500:
            chunks.append(current)
            current = piece
        else:
            current = current + "\n" + piece if current else piece
    if current:
        chunks.append(current)
    return chunks


def _help_keyboard(bot_username: str | None) -> InlineKeyboardMarkup | None:
    """Inline button for the pinned help/announcement.

    A channel subscriber cannot post to the channel, so a plain ``/sites``
    command text isn't tap-to-run for them. A deep-link URL button is — it
    opens a 1:1 chat with the bot whose START payload (`sites`) routes to
    the external-sites list (handled in `cmd_help`). Returns None when the
    bot username isn't known yet (button is then simply omitted)."""
    if not bot_username:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🔗 외부 참조 대쉬보드사이트 모음",
            url=f"https://t.me/{bot_username}?start=sites",
        )
    ]])


async def _send_help(send_html, send_plain, label: str, reply_markup=None) -> None:
    """Send the chunked help via the two callables (HTML + plain fallback).
    `send_html(text, reply_markup)` and `send_plain(text, reply_markup)` are
    awaitables the caller binds to either `update.message.reply_text` or
    `bot.send_message` so this works for both DM (`cmd_help`) and channel
    post paths.

    `reply_markup`, when given, is attached to the LAST chunk only so the
    inline button rides along with the (single, pinned) help message.

    Defensive fallback: if Telegram rejects an HTML chunk (entity parse
    error or oversize) the chunk is re-sent as plain text with tags
    stripped — the user never sees nothing at all."""
    chunks = _chunk_help_text()
    log.info("%s: sending %d chunk(s)", label, len(chunks))
    last = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        rm = reply_markup if idx == last else None
        try:
            await send_html(chunk, rm)
        except Exception as exc:
            log.warning(
                "%s chunk %d/%d HTML send failed (%s) — retrying as plain text",
                label, idx, len(chunks), exc,
            )
            plain = re.sub(r"<[^>]+>", "", chunk)
            try:
                await send_plain(plain, rm)
            except Exception:
                log.exception(
                    "%s chunk %d/%d plain fallback also failed",
                    label, idx, len(chunks),
                )
        await asyncio.sleep(0.5)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """DM /start or /help — comprehensive bot usage guide. See
    `_send_help` for the chunking + fallback logic.

    Deep-link `/start sites` (the pinned-announcement button target) shows
    the external reference sites instead of the full help."""
    if update.message is None:
        return
    if ctx.args and ctx.args[0].lower() == "sites":
        await update.message.reply_text(
            _SITES_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        return
    try:
        uname = ctx.bot.username
    except Exception:
        uname = None
    await _send_help(
        send_html=lambda t, rm=None: update.message.reply_text(
            t, parse_mode=ParseMode.HTML, reply_markup=rm
        ),
        send_plain=lambda t, rm=None: update.message.reply_text(t, reply_markup=rm),
        label="cmd_help",
        reply_markup=_help_keyboard(uname),
    )


# ─── /usage ───────────────────────────────────────────────────────────

# Local-day boundaries are computed in KST regardless of server tz so
# the daily chart aligns with how the user actually thinks about days.
_KST = timezone(timedelta(hours=9))


def _kst_day_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, _KST).strftime("%Y-%m-%d")


def _kst_today_start_ts() -> float:
    now = datetime.now(_KST)
    midnight = datetime(now.year, now.month, now.day, tzinfo=_KST)
    return midnight.timestamp()


def _format_seconds(sec: float) -> str:
    sec = int(sec)
    if sec >= 60:
        return f"{sec // 60}분 {sec % 60}초"
    return f"{sec}초"


def _count_watchdog_restarts_24h() -> int:
    """Best-effort count of watchdog-triggered restarts in the last day.

    Reads the watchdog timer's journal; returns 0 if journalctl isn't
    available or the unit doesn't exist on this host.
    """
    try:
        out = subprocess.run(
            ["journalctl", "-u", "stock-bot-watchdog", "--since", "24 hours ago",
             "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10,
        )
        # `restarting` appears in our notify line when watchdog actually fires
        return out.stdout.count("restarting")
    except Exception:
        return 0


def _build_usage_report() -> str:
    records = usage_tracker.load_records(window_days=30)
    today_start = _kst_today_start_ts()
    week_start = today_start - 6 * 86400  # 7 days inclusive of today
    now = time.time()

    # Counts per window
    def in_window(rec: dict, since: float) -> bool:
        return rec.get("ts", 0) >= since

    analyses = [r for r in records if r.get("type") == "analysis"]
    today_runs  = [r for r in analyses if in_window(r, today_start)]
    week_runs   = [r for r in analyses if in_window(r, week_start)]
    month_runs  = analyses  # already 30d window

    today_cache = sum(1 for r in today_runs if r.get("cache_hit"))
    today_new   = len(today_runs) - today_cache

    # Costs by window
    calls = [r for r in records if r.get("type") == "llm_call"]
    today_calls = [r for r in calls if in_window(r, today_start)]
    week_calls  = [r for r in calls if in_window(r, week_start)]
    today_cost  = sum(r.get("cost_usd", 0) for r in today_calls)
    week_cost   = sum(r.get("cost_usd", 0) for r in week_calls)
    month_cost  = sum(r.get("cost_usd", 0) for r in calls)

    # Per-model breakdown for today
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "tokens": 0})
    for r in today_calls:
        model = r.get("model") or "unknown"
        by_model[model]["calls"] += 1
        by_model[model]["cost"] += r.get("cost_usd", 0)
        by_model[model]["tokens"] += (
            r.get("prompt_tokens", 0) + r.get("completion_tokens", 0)
        )

    # 7-day daily series — costs + analysis count keyed by KST day
    daily: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "runs": 0})
    for r in calls:
        if in_window(r, week_start):
            daily[_kst_day_str(r["ts"])]["cost"] += r.get("cost_usd", 0)
    for r in analyses:
        if in_window(r, week_start):
            daily[_kst_day_str(r["ts"])]["runs"] += 1

    last_7_days = []
    for offset in range(6, -1, -1):
        d_ts = today_start - offset * 86400
        last_7_days.append(_kst_day_str(d_ts))

    max_daily_cost = max(
        (daily[d]["cost"] for d in last_7_days), default=0.0
    )

    # Most-analyzed tickers (7d, fresh runs only)
    ticker_counter: Counter[str] = Counter()
    for r in week_runs:
        if not r.get("cache_hit"):
            ticker_counter[r.get("ticker", "?")] += 1

    # Average elapsed for fresh runs in last 24h
    elapsed = [
        r.get("elapsed_sec", 0) for r in analyses
        if not r.get("cache_hit")
        and r.get("elapsed_sec", 0) > 0
        and now - r.get("ts", 0) < 86400
    ]
    avg_elapsed = sum(elapsed) / len(elapsed) if elapsed else 0.0

    # Failures in last 24h
    failure_24h = sum(
        1 for r in records
        if r.get("type") == "failure" and now - r.get("ts", 0) < 86400
    )
    watchdog_24h = _count_watchdog_restarts_24h()

    fx = usage_tracker.KRW_PER_USD

    def krw(usd: float) -> str:
        return f"₩{int(round(usd * fx)):,}"

    # Subsystem cost split (분석 / Screener) — from usage.jsonl's
    # subsystem='screener' tag. Plus SV cost (separate file).
    today_cost_screener = sum(
        r.get("cost_usd", 0) for r in today_calls
        if r.get("subsystem") == "screener"
    )
    month_cost_screener = sum(
        r.get("cost_usd", 0) for r in calls if r.get("subsystem") == "screener"
    )
    today_cost_analysis = today_cost - today_cost_screener
    month_cost_analysis = month_cost - month_cost_screener

    # Standard View cost — read sv_usage.jsonl directly (KST date tagged).
    sv_today_krw = sv_month_krw = 0.0
    try:
        import json as _j_sv
        sv_path = Path.home() / "standardview" / "sv_usage.jsonl"
        if sv_path.exists():
            today_str_kst = datetime.now(_KST).date().isoformat()
            month_str_kst = today_str_kst[:7]
            with open(sv_path, encoding="utf-8") as _sf:
                for _line in _sf:
                    try:
                        _r = _j_sv.loads(_line)
                    except Exception:
                        continue
                    if _r.get("date") == today_str_kst:
                        sv_today_krw += _r.get("cost_krw", 0) or 0
                    if _r.get("month") == month_str_kst:
                        sv_month_krw += _r.get("cost_krw", 0) or 0
    except Exception as _exc:
        log.warning("usage: SV cost read failed: %s", _exc)
    # Express SV in USD for combined display arithmetic (then back to KRW
    # via krw() for consistency with other rows).
    sv_today_usd = sv_today_krw / fx
    sv_month_usd = sv_month_krw / fx

    today_total_usd = today_cost + sv_today_usd
    month_total_usd = month_cost + sv_month_usd

    lines = [
        "📊 <b>NOAH 봇 사용 현황</b> (KST)",
        "",
        "🔬 <b>분석 실행</b>",
        f"  • 오늘: {len(today_runs)}건  (새 {today_new}건 + 캐시 {today_cache}건)",
        f"  • 7일:  {len(week_runs)}건",
        f"  • 30일: {len(month_runs)}건",
        "",
        f"💰 <b>총 비용 (NOAH 분석 + Screener + SV)</b> (₩{fx}/$)",
        f"  • 오늘: <b>{krw(today_total_usd)}</b>  (${today_total_usd:.2f})",
        f"  • 30일: <b>{krw(month_total_usd)}</b>  (${month_total_usd:.2f})",
        "",
        "📐 <b>월간 subsystem 분포</b>",
        f"  • NOAH 분석:        {krw(month_cost_analysis)}",
        f"  • Bottleneck Screener: {krw(month_cost_screener)}  ← /screener_cost",
        f"  • Standard View:     {krw(sv_month_usd)}  ← /sv_cost",
        "",
        f"💰 <b>NOAH 분석 단독 (참고)</b>",
        f"  • 7일:  {krw(week_cost)}  (${week_cost:.2f})",
        "",
        "🤖 <b>모델별 (오늘 · NOAH 분석)</b>",
    ]
    if by_model:
        for model in sorted(by_model, key=lambda m: -by_model[m]["cost"]):
            stats = by_model[model]
            short = model.replace("gemini-2.5-", "") if model.startswith("gemini-2.5-") else model
            tokens_k = stats["tokens"] / 1000.0
            lines.append(
                f"  {short:<11} {krw(stats['cost']):>7}  "
                f"({stats['calls']}콜, {tokens_k:.1f}K 토큰)"
            )
            purpose = usage_tracker.MODEL_PURPOSE.get(model)
            if purpose:
                lines.append(f"     ↳ {purpose}")
    else:
        lines.append("  (오늘 호출 없음)")
    lines.append("")

    lines.append("📅 <b>최근 7일 일별</b>")
    for day in last_7_days:
        cost = daily[day]["cost"]
        runs = daily[day]["runs"]
        if max_daily_cost > 0 and cost > 0:
            bar_len = max(1, int(cost / max_daily_cost * 20))
        else:
            bar_len = 0
        bar = "█" * bar_len if bar_len else "·"
        bar = bar.ljust(20)
        # MM-DD slice from YYYY-MM-DD
        lines.append(f"  {day[5:]}  {bar}  {krw(cost):>7}  ({runs}건)")
    lines.append("")

    lines.append("📈 <b>자주 분석된 종목 (7일)</b>")
    if ticker_counter:
        top = ", ".join(f"{t} × {n}" for t, n in ticker_counter.most_common(7))
        lines.append(f"  {top}")
    else:
        lines.append("  (분석 없음)")
    lines.append("")

    if avg_elapsed > 0:
        lines.append(f"⏱ 평균 분석 시간 (24h): {_format_seconds(avg_elapsed)}")
    lines.append(f"🛡 Watchdog 재시작 (24h): {watchdog_24h}건")
    lines.append(f"❌ 분석 실패 (24h): {failure_24h}건")

    return "\n".join(lines)


async def cmd_usage(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/usage in DM — show analysis + cost digest. Channel /usage is
    handled in on_channel_post (PTB CommandHandler doesn't fire on
    channel_post updates)."""
    if update.message is None:
        return
    await update.message.reply_text(_build_usage_report(), parse_mode=ParseMode.HTML)


def _read_screener_cost_today_month() -> dict:
    """Aggregate Bottleneck Screener cost from ~/.tradingagents/screener_
    usage.jsonl (KST date-tagged). Returns {today_krw, month_krw,
    today_calls, month_calls, today_pt, today_ot}. Empty dict on
    failure. Mirror of /api/sv-usage/today shape."""
    from pathlib import Path as _P
    import json as _j
    path = _P.home() / ".tradingagents" / "screener_usage.jsonl"
    out = {"today_krw": 0.0, "month_krw": 0.0, "today_calls": 0,
           "month_calls": 0, "today_prompt_tok": 0, "today_output_tok": 0}
    if not path.exists():
        return out
    today = datetime.now(_KST).date().isoformat()
    month = today[:7]
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _j.loads(line)
                except Exception:
                    continue
                if rec.get("date") == today:
                    out["today_krw"] += rec.get("cost_krw", 0) or 0
                    out["today_calls"] += 1
                    out["today_prompt_tok"] += rec.get("prompt_tok", 0) or 0
                    out["today_output_tok"] += rec.get("output_tok", 0) or 0
                if rec.get("month") == month:
                    out["month_krw"] += rec.get("cost_krw", 0) or 0
                    out["month_calls"] += 1
    except Exception as exc:
        log.warning("screener_cost: read failed: %s", exc)
    return out


async def cmd_screener_cost(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/screener_cost — show Bottleneck Screener Gemini Pro cost (parallel
    to /sv_cost). Reads ~/.tradingagents/screener_usage.jsonl directly —
    no backend HTTP call needed."""
    if update.message is None:
        return
    data = _read_screener_cost_today_month()
    today_krw = float(data.get("today_krw", 0) or 0)
    month_krw = float(data.get("month_krw", 0) or 0)
    today_calls = int(data.get("today_calls", 0) or 0)
    month_calls = int(data.get("month_calls", 0) or 0)
    today_pt = int(data.get("today_prompt_tok", 0) or 0)
    today_ot = int(data.get("today_output_tok", 0) or 0)
    text = (
        "💰 <b>Bottleneck Screener 비용</b> (Gemini Pro · 웹 검색 grounding)\n"
        f"오늘: <b>₩{today_krw:,.1f}</b> · {today_calls}회\n"
        f"이번 달: <b>₩{month_krw:,.0f}</b> · {month_calls}회\n"
        f"오늘 tokens: in {today_pt:,} / out {today_ot:,}\n"
        "<i>모델: gemini-2.5-pro · Phase β 2-pass 호출 + Phase 3 후보별"
        " 실시간 fetch · Top-3 5/15/30d outcome resolver feed</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_sv_cost(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/sv_cost — show Standard View Gemini API cost. /usage 는 NOAH 의
    cost 만 잡으므로 별도. SV backend (~/standardview) 가 같은 host
    에서 띄워져 있을 때 동작 — 127.0.0.1:8002/api/sv-usage/today 조회."""
    if update.message is None:
        return
    try:
        import httpx as _httpx
        r = _httpx.get(
            "http://127.0.0.1:8002/api/sv-usage/today", timeout=5
        )
        if r.status_code != 200:
            await update.message.reply_text(
                f"SV 비용 endpoint 응답 {r.status_code} — backend down?"
            )
            return
        data = r.json()
    except Exception as exc:
        await update.message.reply_text(
            f"SV 비용 조회 실패: {type(exc).__name__}: {exc}\n"
            f"backend (~/standardview) 가 8002 에서 가동 중인지 확인."
        )
        return
    today_krw = float(data.get("today_krw", 0) or 0)
    month_krw = float(data.get("month_krw", 0) or 0)
    today_calls = int(data.get("today_calls", 0) or 0)
    month_calls = int(data.get("month_calls", 0) or 0)
    today_pt = int(data.get("today_prompt_tok", 0) or 0)
    today_ot = int(data.get("today_output_tok", 0) or 0)
    text = (
        "💰 <b>Standard View 비용</b> (Gemini API, NOAH /usage 와 별개)\n"
        f"오늘: <b>₩{today_krw:,.1f}</b> · {today_calls}회\n"
        f"이번 달: <b>₩{month_krw:,.0f}</b> · {month_calls}회\n"
        f"오늘 tokens: in {today_pt:,} / out {today_ot:,}\n"
        "<i>모델: gemini-2.5-flash · 매크로 brief + 산업 분석 + 코멘트 카드 호출</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_sites(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/sites — external reference dashboards / tools bookmark list.
    Channel /sites is handled in on_channel_post (PTB CommandHandler
    doesn't fire on channel_post updates)."""
    if update.message is None:
        return
    await update.message.reply_text(
        _SITES_TEXT,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _run_screener_and_send(send, domain: str) -> None:
    """Run the screener in a thread (it's a synchronous Pro call that
    blocks for ~3-5 minutes) and stream chunks back through `send`.
    Used by both DM cmd_screener and the channel /screener path."""
    import asyncio
    from bot.screener import run_screener, format_for_telegram
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, run_screener, domain or "bottleneck")
    except Exception as exc:
        log.exception("screener: orchestrator threw: %s", exc)
        await send(f"⚠️ Screener 오류 — {exc.__class__.__name__}")
        return
    if result is None:
        await send(
            "⚠️ Screener 실행 실패 — GOOGLE_API_KEY 누락 또는 Pro 응답 없음."
            " 로그 확인 권장."
        )
        return
    for chunk in format_for_telegram(result):
        await send(chunk)


async def cmd_screener(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/screener [domain] — Bottleneck Screener Phase α MVP.

    Phase α: AI Data Center domain only. Wave 1 will extend to ev / pharma
    / solar / defense etc. via theme registry in bot/screener_themes/.
    See CLAUDE.md 'Bottleneck Screener' section for full design.
    """
    if update.message is None:
        return
    await update.message.reply_text(
        "📊 <b>Bottleneck Screener</b> 시작 — AI 데이터센터 도메인 "
        "(Phase β · 실시간 데이터 + 웹 검색)\n"
        "⏱ <b>6-12분 소요</b> — Phase 1·2 (Pro+웹 검색 후보 식별) → "
        "Phase 3 (병렬 실시간 fetch) → Phase 4·5 (Pro+웹 검색 분석)\n"
        "💡 fetch hung 시 120s 후 partial 결과로 자동 진행",
        parse_mode=ParseMode.HTML,
    )
    domain = " ".join(ctx.args).strip().lower() if ctx.args else "bottleneck"
    chat_id = update.message.chat_id
    await _run_screener_and_send(
        send=lambda t: ctx.bot.send_message(
            chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ),
        domain=domain,
    )


async def cmd_compare_hint(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/compare in DM doesn't run — analysis happens in the registered
    channel. Point the user at the right place instead of staying silent."""
    if update.message is None:
        return
    await update.message.reply_text(
        "💡 /compare 는 등록된 채널에서만 동작합니다.\n\n"
        "채널에 다음과 같이 입력하면 두 종목 비교 분석이 돌아갑니다:\n"
        "/compare NVDA AMD\n\n"
        "단일 종목 분석도 동일하게 채널에서 /NVDA, /AAPL 등으로 입력하세요.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_ticker_hint(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch random /<TICKER> typed in DM — same redirect."""
    if update.message is None or update.message.text is None:
        return
    typed = update.message.text.split()[0]  # e.g. '/NVDA'
    await update.message.reply_text(
        f"💡 종목 분석은 등록된 채널에서만 동작합니다.\n\n"
        f"채널에 {typed} 라고 입력하면 분석이 시작됩니다.",
        parse_mode=ParseMode.HTML,
    )


def _format_failure(ticker: str, exc: Exception) -> str:
    """Translate raw analyzer exceptions into a user-readable Korean message."""
    text = str(exc)
    ticker_html = _html.escape(ticker)
    if "가격 데이터를 찾을 수 없습니다" in text:
        return (
            f"❌ <b>{ticker_html}</b> 분석 실패: yfinance에 해당 종목이 없습니다.\n\n"
            f"입력하신 '{ticker_html}'는 yfinance에서 가격 데이터를 반환하지 않습니다."
            f" 회사 이름을 ticker로 입력하셨거나, 오타 / 상장폐지 가능성이 있습니다.\n\n"
            f"올바른 입력 예시:\n"
            f"• 미국: <code>/NVDA</code>, <code>/AAPL</code>\n"
            f"• 한국 (6자리 + 거래소): <code>/035420.KS</code> (네이버),"
            f" <code>/005930.KS</code> (삼성전자)\n"
            f"• 한국 (종목명 직접): <code>/네이버</code>, <code>/카카오</code>,"
            f" <code>/삼성전자</code>"
        )
    if "분석가 응답 누락" in text:
        return (
            f"❌ <b>{ticker_html}</b> 분석 실패: 분석가 응답 누락\n\n"
            f"{_html.escape(text)}\n"
            f"(자동 1회 재시도 후에도 분석가가 응답하지 않아 결정 단계로 진행하지 않았습니다. "
            f"hallucination 위험을 피하기 위해 분석을 중단합니다.)"
        )
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


async def _periodic_auto_resolve() -> None:
    """Daily background task that resolves pending memory-log entries.

    Without this, accuracy stats only grow when the user re-analyzes the
    same ticker — analyze a name once and never come back, the entry
    stays "pending" forever and never enters the 1/N denominator. The
    task runs once at startup (catches anything that matured while the
    bot was down) and then every 12h.

    Runs in a subprocess so a yfinance hang or network blip can't take
    out the bot's asyncio loop. Subprocess exit code is ignored —
    failures are logged to journal but don't propagate.
    """
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "bot.auto_resolve",
                stdout=asyncio.subprocess.PIPE,
                stderr=None,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=300
            )
            line = (stdout or b"").decode("utf-8", "replace").strip()
            if line:
                log.info("auto_resolve: %s", line)
        except asyncio.TimeoutError:
            log.warning("auto_resolve subprocess timed out — killing")
            try:
                proc.kill()
            except Exception:
                pass
        except Exception as exc:
            log.warning("auto_resolve subprocess failed: %s", exc)
        await asyncio.sleep(12 * 3600)


async def _periodic_dashboard_refresh() -> None:
    """Regenerate dashboard index.html ~1 min after each KST midnight.

    regenerate_index() otherwise only fires on (a) analysis completion,
    (b) card deletion, (c) auto_resolve. If none of those happen on a
    given day the dashboard's 'today / this month' cards still show
    yesterday's date and cost — confusing the user into thinking ₩X
    was spent today when it was actually yesterday's tally rolling over.

    Sleep until 00:01 KST, regen, repeat. The 1-minute offset gives any
    in-flight analysis straddling midnight time to write its final
    usage record before the day boundary is rendered."""
    kst = timezone(timedelta(hours=9))
    while True:
        now_kst = datetime.now(kst)
        # Next 00:01 KST. If we just crossed midnight, target tomorrow.
        target = (now_kst + timedelta(days=1)).replace(
            hour=0, minute=1, second=0, microsecond=0
        )
        sleep_secs = max(60.0, (target - now_kst).total_seconds())
        await asyncio.sleep(sleep_secs)
        try:
            from bot.dashboard import regenerate_index
            regenerate_index()
            log.info("midnight dashboard regen: ok")
        except Exception:
            log.exception("midnight dashboard regen failed")


async def _on_startup(application) -> None:
    """Bot init: register the slash-command menu, then handle any orphan
    progress message left behind by a previous instance that died
    mid-analysis (otherwise it would stay 'VICR 분석 시작…' forever).

    Also regenerates screener.html on each restart — after a dashboard
    code change ships (휴지통 / 검색창 / collapsible 등), the next bot
    restart immediately re-renders the static HTML so existing archives
    pick up the new UI without waiting for the next /screener call or
    12h auto_resolve cycle."""
    try:
        from bot.dashboard import regenerate_screener_index
        regenerate_screener_index()
        log.info("startup: screener.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: screener.html regen failed: %s", exc)
    # Populates the 'Menu' button beside the input area + the '/' typing
    # autocomplete in DMs. Dynamic per-ticker commands like /NVDA aren't
    # registered (the universe is too large) — Telegram still recognises
    # them as tappable commands when typed in plain text.
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "사용법 안내"),
            BotCommand("help", "사용법 안내"),
            BotCommand("usage", "사용량 / 통합 비용 / 7일 차트"),
            BotCommand("screener_cost", "Bottleneck Screener 비용 (Pro)"),
            BotCommand("sites", "참고 사이트"),
            BotCommand("screener", "Bottleneck 종목 발굴 (AI 데이터센터)"),
            BotCommand("compare", "두 종목 비교 (채널에서 사용)"),
        ])
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)

    # Kick off the background pending-entry resolver. fire-and-forget —
    # the task runs forever, sleeping 12h between cycles. Stored on the
    # application so it stays referenced (otherwise the GC could collect it).
    application._auto_resolve_task = asyncio.create_task(_periodic_auto_resolve())
    application._dashboard_refresh_task = asyncio.create_task(_periodic_dashboard_refresh())

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
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CommandHandler("sv_cost", cmd_sv_cost))
    app.add_handler(CommandHandler("screener_cost", cmd_screener_cost))
    app.add_handler(CommandHandler("sites", cmd_sites))
    app.add_handler(CommandHandler("screener", cmd_screener))
    # Catch /compare typed in DM and redirect — actual compare runs only
    # via on_channel_post inside the registered channel.
    app.add_handler(CommandHandler("compare", cmd_compare_hint))
    app.add_handler(CallbackQueryHandler(on_full_report, pattern=r"^full:"))
    app.add_handler(
        MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, on_channel_post)
    )
    # Last-resort DM hint for any other /<TICKER>-style command typed at
    # the bot privately. Only fires for slash-prefixed messages that none
    # of the explicit CommandHandlers above matched.
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.COMMAND, cmd_ticker_hint
        )
    )

    # Refresh the static dashboard once at startup so an auto-update
    # deploy (which restarts the bot) immediately shows any changes
    # to dashboard.py without waiting for the next analysis to fire.
    try:
        _dashboard_regen()
    except Exception as exc:
        log.warning("startup dashboard regen failed: %s", exc)

    log.info("bot starting — watching channels: %s", CHANNEL_CHAT_IDS or "auto-detect")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
