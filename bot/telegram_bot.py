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


# httpx 는 매 요청 URL 을 INFO 로 찍는다 — api.telegram.org/bot<TOKEN>/...
# 에 봇 토큰이 평문 노출된다. 그러나 stock-bot-watchdog.sh 는 journald 의
# 'getUpdates' INFO 로그 유무로 polling 생존을 감지하므로 httpx 를 WARNING
# 으로 **억제하면 watchdog 가 매 사이클 오탐 → 봇 무한 재시작**(2026-05-29
# 회귀). 따라서 레벨은 INFO 유지하되, 토큰 문자열만 마스킹하는 필터를 httpx/
# httpcore 로거에 부착 → 'getUpdates' 는 보존(watchdog 정상) + 토큰 누출 차단.
class _TokenRedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx 는 URL 을 record.args 에 **httpx.URL 객체**(str 아님)로 넣어
        # 'HTTP Request: %s %s ...' 로 찍는다. 기존 isinstance(a,str) 필터는
        # URL 객체를 건너뛰어 토큰이 journald 에 평문 노출됐다 (2026-06-01
        # surfaced). 해결: record.getMessage() 로 args 까지 합쳐 완성된
        # 문자열을 만든 뒤 토큰 마스킹, args 를 비워 재포맷 방지. 객체 타입
        # 무관하게 최종 출력이 마스킹된다. getUpdates 텍스트는 보존(watchdog).
        try:
            if TOKEN:
                msg = record.getMessage()
                if TOKEN in msg:
                    record.msg = msg.replace(TOKEN, "BOT_TOKEN")
                    record.args = None
        except Exception:
            pass
        return True


# httpx/httpcore 로거에 부착 — 레코드가 이 로거에서 발생하므로 filter()가
# 실행돼 마스킹 후 root 핸들러로 전파된다 (propagate 시 상위 로거 필터는
# 재실행 안 되므로 발생 로거에 다는 게 정답). 토큰 URL 을 찍는 라이브러리는
# 사실상 httpx 뿐 — google-genai/telegram 도 내부 httpx 사용.
_redact = _TokenRedactFilter()
for _ln in ("httpx", "httpcore"):
    logging.getLogger(_ln).addFilter(_redact)

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


class _BusyKeepalive:
    """장기 실행(Screener 6-12분 등) 동안 .busy marker 를 주기적으로 re-touch
    → mtime 항상 fresh → watchdog 의 12분 stale 체크가 발화 안 함.

    ⚠️ asyncio task 가 아니라 **OS 데몬 스레드** 로 동작한다. Screener 는
    run_in_executor 워커가 무거운 Python 연산 구간에서 GIL 을 장시간 점유
    → 같은 이벤트 루프의 asyncio keepalive 가 starve 되어 touch 못 함
    (2026-06-01 .busy 4.5분 고정 surfaced). 별도 스레드는 threading.Event
    .wait() 중 GIL 을 놓으므로 워커가 GIL 을 쥐어도 정확히 interval 마다
    깨어나 touch 한다. stop() 은 멱등."""

    def __init__(self, interval: float = 45.0):
        import threading
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        from bot.analyzer import mark_busy
        while not self._stop.wait(self._interval):
            try:
                mark_busy()  # mtime refresh (단순 touch, refcount 무관)
            except Exception:
                pass

    def start(self) -> "_BusyKeepalive":
        self._thread.start()
        return self

    def stop(self) -> None:
        # set + join → 스레드가 release 이후 touch 해서 .busy 가 orphan 으로
        # 되살아나는 race 차단. wait() 가 즉시 깨어나 loop 종료하므로 빠름.
        self._stop.set()
        try:
            self._thread.join(timeout=2.0)
        except Exception:
            pass


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

    # /screener_list in channel — auto-generated registry listing.
    # Single source of truth = bot.screener_themes; _HELP_TEXT only
    # carries a link here so future domain additions don't pressure
    # the 4096 UTF-16 cap (CLAUDE.md rule, 2026-05-29).
    if first_word == "screener_list":
        async def _send_ch(t, rm=None):
            await ctx.bot.send_message(
                chat_id=post.chat.id,
                text=t,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=rm,
            )
        await _send_screener_domains_list(_send_ch, _screener_list_keyboard())
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

    # /daily_byte_cost in channel — Daily Byte Pro cost (parallel to
    # /screener_cost · /sv_cost). Reads daily_byte_usage.jsonl directly.
    if first_word == "daily_byte_cost":
        data = _read_daily_byte_cost_today_month()
        today_krw = float(data.get("today_krw", 0) or 0)
        month_krw = float(data.get("month_krw", 0) or 0)
        today_calls = int(data.get("today_calls", 0) or 0)
        month_calls = int(data.get("month_calls", 0) or 0)
        today_pt = int(data.get("today_prompt_tok", 0) or 0)
        today_ot = int(data.get("today_output_tok", 0) or 0)
        text_out = (
            "💰 <b>Daily Byte 비용</b> (Gemini Pro · 웹 검색 grounding)\n"
            f"오늘: <b>₩{today_krw:,.1f}</b> · {today_calls}회\n"
            f"이번 달: <b>₩{month_krw:,.0f}</b> · {month_calls}회\n"
            f"오늘 tokens: in {today_pt:,} / out {today_ot:,}\n"
            "<i>모델: gemini-2.5-pro · 한국거래일 19:00 Daily + 일 22:00 Weekly</i>"
        )
        await ctx.bot.send_message(
            chat_id=post.chat.id, text=text_out, parse_mode=ParseMode.HTML,
        )
        return

    # /realestate_cost in channel
    if first_word == "realestate_cost":
        data = _read_realestate_cost_today_month()
        text_out = (
            "💰 <b>부동산 Byte 비용</b> (Gemini Pro · 웹 검색 grounding)\n"
            f"오늘: <b>₩{float(data.get('today_krw',0)):,.1f}</b> · {int(data.get('today_calls',0))}회\n"
            f"이번 달: <b>₩{float(data.get('month_krw',0)):,.0f}</b> · {int(data.get('month_calls',0))}회\n"
            "<i>모델: gemini-2.5-pro · 금 09:00 주간 + 매월 1일 Monthly</i>"
        )
        await ctx.bot.send_message(
            chat_id=post.chat.id, text=text_out, parse_mode=ParseMode.HTML,
        )
        return

    # /cheongyak_cost in channel
    if first_word == "cheongyak_cost":
        data = _read_cheongyak_cost_today_month()
        text_out = (
            "💰 <b>청약 Byte 비용</b> (Gemini Pro · 신규 분양 피드)\n"
            f"오늘: <b>₩{float(data.get('today_krw',0)):,.1f}</b> · {int(data.get('today_calls',0))}회\n"
            f"이번 달: <b>₩{float(data.get('month_krw',0)):,.0f}</b> · {int(data.get('month_calls',0))}회\n"
            "<i>모델: gemini-2.5-pro · 평일 10:00·14:00 신규 분양 모집공고</i>"
        )
        await ctx.bot.send_message(
            chat_id=post.chat.id, text=text_out, parse_mode=ParseMode.HTML,
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

    # /screen in channel — generic conditional screener (비용 ₩0, pykrx bulk).
    if first_word == "screen":
        _scid = post.chat.id
        _scargs = body.split()[1:]

        async def _scsend(t):
            await ctx.bot.send_message(chat_id=_scid, text=t,
                                       parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
        await _handle_screen(_scargs, _scsend)
        return

    # /paper in channel — E0 페이퍼 트레이딩. PTB CommandHandler 가 channel_post
    # 에 안 fire 하므로 여기서 라우팅(없으면 'PAPER' 를 티커로 오인). DM cmd_paper
    # 와 동일 로직(_handle_paper) 공유.
    if first_word == "paper":
        _pid = post.chat.id

        async def _psend(t):
            await ctx.bot.send_message(chat_id=_pid, text=t,
                                       parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
        await _handle_paper(body.split()[1:], _psend,
                            idem=f"tg:{post.message_id}")
        return

    # /watch · /watchlist · /unwatch in channel — PTB CommandHandler doesn't
    # fire on channel_post, so without this branch '/watch' falls through to
    # ticker analysis and treats 'WATCH' as a symbol (2026-06-04 bug).
    if first_word in ("watch", "watchlist", "unwatch"):
        chat_id = post.chat.id
        rest = body.split()[1:]  # args after the command word

        async def _wsend(t):
            await ctx.bot.send_message(
                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True)

        if first_word == "watch":
            if len(rest) < 2:
                await _wsend(_WATCH_HELP)
                return
            from bot.watchlist import add_watch, parse_conditions
            ticker = rest[0].strip().upper()
            if not TICKER_RE.match(ticker):
                await _wsend(f"⚠️ 잘못된 티커: {ticker}\n\n{_WATCH_HELP}")
                return
            valid, invalid = parse_conditions(" ".join(rest[1:]))
            if not valid:
                await _wsend("⚠️ 유효한 조건이 없습니다.\n\n" + _WATCH_HELP)
                return
            try:
                w = add_watch(ticker, chat_id, valid)
            except ValueError as exc:
                await _wsend(f"⚠️ {exc}")
                return
            msg = f"✅ <b>{ticker}</b> 감시 등록\n조건: <code>{' '.join(w['conditions'])}</code>"
            if invalid:
                msg += f"\n⚠️ 무시된 조건: <code>{' '.join(invalid)}</code>"
            await _wsend(msg)
            try:
                from bot.dashboard import regenerate_watchlist_index
                regenerate_watchlist_index()
            except Exception:
                pass
            return
        if first_word == "watchlist":
            from bot.watchlist import list_watches
            watches = list_watches(chat_id)
            if not watches:
                await _wsend("감시 중인 종목 없음.\n\n" + _WATCH_HELP)
                return
            lines = ["🔔 <b>워치리스트</b>"]
            for w in watches:
                lines.append(
                    f"• <b>{w['ticker']}</b> <code>{' '.join(w.get('conditions') or [])}</code>"
                    f"  (id {w['id']})")
            lines.append("\n삭제: /unwatch TICKER (또는 id) · 전체 /unwatch all")
            await _wsend("\n".join(lines))
            return
        # unwatch
        if not rest:
            await _wsend("사용법: /unwatch TICKER (또는 id, 또는 all)")
            return
        from bot.watchlist import remove_watch
        n = remove_watch(chat_id, rest[0])
        await _wsend(f"🗑️ {n}개 삭제됨" if n else "삭제할 항목 없음 (티커/id 확인)")
        try:
            from bot.dashboard import regenerate_watchlist_index
            regenerate_watchlist_index()
        except Exception:
            pass
        return

    # /screener_<slug> shortcut in channel — single-tap per-domain
    # commands (registered dynamically for DM via _register_dynamic_
    # screener_handlers). Same dispatch path so behavior matches.
    # Excludes 'screener_cost' and 'screener_list' which have dedicated
    # branches above.
    if first_word.startswith("screener_") and first_word not in (
        "screener_cost", "screener_list",
    ):
        from bot.screener_themes import resolve as _scr_resolve, available_summary as _scr_avail
        chat_id = post.chat.id
        raw_domain = first_word[len("screener_"):]
        theme = _scr_resolve(raw_domain)
        if theme is None:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ '<code>{raw_domain}</code>' 도메인을 찾을 수 없습니다.\n사용 가능: <code>{_scr_avail()}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📊 <b>Bottleneck Screener</b> 시작 — <b>{theme['domain']}</b> "
                f"({theme.get('horizon','')} 관점, Phase β · 실시간 데이터)\n"
                "⏱ <b>5-10분 소요</b> · Phase 1·2 → Phase 3 (병렬 fetch) → Phase 4·5"
            ),
            parse_mode=ParseMode.HTML,
        )
        await _run_screener_and_send(
            send=lambda t: ctx.bot.send_message(
                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            ),
            domain=raw_domain,
        )
        return

    # /screener [domain] in channel — static registry (6 L1 + 11 L2 + 48
    # L3 = 65 domains) + free-text Phase 0 fallback (2026-05-29). Unknown
    # alias → Pro generates theme dict on-the-fly. Resolver/progress logic
    # shared with the DM /screener path via `_resolve_screener_target`.
    if first_word == "screener":
        chat_id = post.chat.id
        raw_domain = body[len("screener"):].strip().lower()
        async def _ch_send(t: str) -> None:
            await ctx.bot.send_message(
                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        resolved = await _resolve_screener_target(_ch_send, raw_domain)
        if resolved.get("mode") == "error":
            return
        await _run_screener_and_send(
            send=_ch_send,
            theme=resolved.get("theme"),
            cache_key=resolved.get("cache_key"),
            force_fresh=resolved.get("force_fresh", False),
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
    await _analyze_ticker_and_post(ctx.bot, post.chat.id, raw)


async def _analyze_ticker_and_post(bot, chat_id: int, raw: str) -> None:
    """Resolved-ticker 분석 본체 — 진행 메시지 → 분석 subprocess → 요약 edit
    (+ 📋 전체 리포트 버튼) → 페이퍼 auto-signal. on_channel_post 의 티커
    분석 블록을 그대로 분리한 것(동작 동일) — 대시보드 '분석' 버튼 폴러
    (_periodic_dashboard_requests)와 공유하기 위함. ``raw`` 는 TICKER_RE 를
    통과한 resolved 티커여야 한다."""
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
    progress = await bot.send_message(
        chat_id=chat_id,
        text=progress_text,
        parse_mode=ParseMode.HTML,
    )

    # Track the in-flight analysis so a future bot restart can edit the
    # orphaned progress message instead of leaving it stuck on '분석 시작…'.
    # Touch .busy in the same breath: analyze() will also write it, but
    # there's a race window before the worker thread picks the job up
    # where auto-update / watchdog could see no marker and restart us
    # mid-analysis. Writing it here on the asyncio loop closes that gap.
    _recovery.write(chat_id, progress.message_id, raw)
    await _busy_acquire()
    try:
        try:
            async with _analysis_lock:
                summary, full = await _run_analysis_subprocess(raw, today)
        except asyncio.TimeoutError:
            log.warning("analysis timed out for %s after %ss", raw, ANALYSIS_TIMEOUT_SEC)
            usage_tracker.log_failure(raw, "10분 타임아웃")
            try:
                await bot.edit_message_text(
                    text=(
                        f"❌ <b>{_html.escape(raw)}</b> 분석 실패: 10분 타임아웃\n\n"
                        f"분석이 강제 중단되어 추가 토큰 소비가 멈춥니다. "
                        f"잠시 후 다시 시도하거나 다른 종목으로 시도해주세요."
                    ),
                    chat_id=chat_id,
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
            await bot.edit_message_text(
                text=_format_failure(raw, exc),
                chat_id=chat_id,
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
        await bot.edit_message_text(
            text=_md_to_html(summary),
            chat_id=chat_id,
            message_id=progress.message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        # E0.5b: NOAH 판정 → 페이퍼 자동 주문(auto on 일 때만). 분석≠실행 분리 —
        # paper_signals 레이어가 신호를 주문으로 변환. 알림이 있으면 채널에 전송.
        # graceful: 어떤 실패도 분석 결과 전송엔 영향 없음(이미 위에서 보냄).
        try:
            from bot.paper_signals import on_analysis as _paper_on_analysis
            _r = _SUMMARY_RATING_RE.search(summary or "")
            # summary(컨빅션 게이트) + full(결정 체인 audit: RM/트레이더 추출) 전달.
            _note = _paper_on_analysis(raw, _r.group(1).strip() if _r else "",
                                       summary or "", full or "")
            if _note:
                await bot.send_message(chat_id=chat_id, text=_note)
                _regen_paper()
        except Exception as _pexc:
            log.debug("paper auto-signal skipped for %s: %s", raw, _pexc)
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
/help /usage /portfolio /screener_list /sites — 비용: /screener·daily_byte·cheongyak·realestate_cost
/screen [us] [조건 | 프리셋] — 조건부 스크리너 (KR/US, ₩0). /screen list
/screener [도메인 | 자유어] — Bottleneck (65 도메인+자유어 즉석). 전체 → /screener_list. 분석·스크리너는 대시보드 실행 버튼으로도 가능
/NVDA /AAPL — 단일 분석 (채널에서)
/compare NVDA AMD — 두 종목 비교
/watch NVDA rsi&lt;30 price&gt;950 — 조건 충족 시 알림 (rsi/price/sma/52w/earnings·KR수급). 목록 /watchlist · 삭제 /unwatch
/paper — 페이퍼 모의매매(돈0). 전체 /paper help
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
🎯 최종판정(Buy~Sell) · 📒 지난추천 결과(5거래일 자동) · ⚠️ 실적±10일·뉴스스킵 알림 · 4명 stance+mismatch · 한 줄 요지 · [📋 전체 리포트]

━━━━━━━━━
<b>【4. 자동 데이터 소스】</b>
yfinance·네이버·Kabutan 뉴스 · 재무(분기+연간) · 매크로9종 · ECOS/FRED · 섹터ETF·리스크6종 · 컨센서스·공매도·내부자·기관 · 실적±10일 · DART/EDINET/MOPS/EDGAR공시+XBRL · US옵션·KRX수급·KIS7종

━━━━━━━━━
<b>【5. 메모리 피드백 + 자동 평가】</b>
 • pending → <b>12h 자동 해소</b> → 5거래일 raw return + <b>섹터 ETF α</b>
 • 다음 동일 종목 요약 상단에 자동 표시 · 결정 LLM 과거 실수 반영

━━━━━━━━━
<b>【6. 조건부 스크리너 /screen】</b> ₩0 · LLM 미사용
정량 조건으로 KR 전 종목(KOSPI+KOSDAQ) 또는 US S&amp;P 500 필터.
 • <code>/screen PER&lt;15 PBR&lt;1 배당수익률&gt;3</code> — KR 자유 조건
 • <code>/screen us PER&lt;15 DIV&gt;2</code> — US S&amp;P 500
 • <code>/screen 매출QoQ&gt;10 영업이익QoQ&gt;5</code> — QoQ 성장 필터
 • <code>/screen valueup</code> — 프리셋 · <code>/screen list</code>
 • Phase 1 pykrx 벌크 → Phase 2 yfinance+QoQ(분기 재무 7종) 생존만
 • 24h 캐시 · 대시보드 통합(실행 버튼 + 📖설명서) · /screen list 전체 지표

━━━━━━━━━
<b>【7. 캐시 &amp; 비용】</b>
 • 같은 종목 재분석 → 캐시 즉시(무료) · 새 분석 ~₩100~150 · /compare ~₩200~300
 • Gemini cache: 결정 3노드 context 공유 · 자정 만료 · /usage 차트

━━━━━━━━━
<b>【8. 채널 알림】</b>
🚀✅ 배포 · ⚠️ hang · ❌ 실패 · 📊 Daily Byte(한국평일19:00·미국07:30·주간일22:00) · 🎟️ 청약(평일10·14시) · 🏠 부동산(금09:00·1일) · 📝 블로그(30분) · 📨 레딧(1분·₩0)

━━━━━━━━━
<b>【9. 대시보드】</b> 3개 entry — 나머지(Screener·레딧·Daily Byte·부동산·청약·수출입)는 🌍Main nav, 워치·도메인목록은 Screener nav 에서
 🌍 <b>Main</b> — 글로벌스냅샷·Macro(금리·물가·환율·센티먼트) · 다가오는실적(한국yfinance+미국Finnhub) · 리서치액션(한국네이버목표가/원문+미국TP) · 관심종목(시총·PER·등락·정렬/필터/순서) · 📋DART공시 · 종목검색 · 5분 갱신
   http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/market.html
 🦉 <b>NOAH 주식분석 아카이브</b> — 분석카드(📊·💰·⏱·🎯알파·5/15/30d) · 차트 · 스니펫검색(🟡클릭→분석) · 🗑️ · <b>분석버튼</b>(대시보드에서 /티커 실행→채널 게시)
   http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/index.html
 💼 <b>자산</b> — 뱅샐 전계좌·증권사·손익·NOAH판정 오버레이
   http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/portfolio.html
 • 데이터: <code>~/.tradingagents/</code> · 외부참고: /sites

━━━━━━━━━
<b>【10. 진행 중 / 예정】</b>
 • Screener 65도메인+자유어+24h캐시 · 분기GICS · 실거래 E1 KIS모의+자동신호+RiskGate · 예정: IBKR·실전(E2)
"""


_SITES_TEXT = """🔗 <b>참고 사이트</b>

 • <a href="http://34.50.23.221:8002/dashboard">Standard View — 매크로·산업·Deal 브리프</a>
 • <a href="https://stockeasy.intellio.kr/">Stockeasy</a>
 • <a href="https://stockhub.kr/">Stockhub</a>
 • <a href="https://jusikbot.com/">Jusikbot — Real-time Stock Dashboard</a>
 • <a href="https://tebi.raoni.xyz/">트비 주식뉴스 어그리게이터 리포트</a>
 • <a href="https://www.tradeodds.io/">Stop guessing. See what history did next.</a>
 • <a href="https://aibottlenecks.app/">AI Bottlenecks</a>
 • <a href="https://analytics.blancwm.com/">Analytics Portal</a>
 • <a href="https://reports.blueming.net/dashboard">report summary</a>
 • <a href="https://junresearch.com/jensenHuangKRTracker">젠슨황의 발자취</a>
 • <a href="https://www.ooooo.law/">ooooo.law</a>
 • <a href="https://karpathy.ai/jobs/">US Job market</a>
 • <a href="https://haebom.dev/daily_arxiv">Arxiv daily</a>
 • <a href="https://whale-insight.com/">국민연금 현황</a>
 • <a href="https://www.tessie.com/">Tesla tracking</a>
 • <a href="https://www.obf.md/app">키워드트래커</a>"""


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
    """Inline buttons for the pinned help/announcement.

    A channel subscriber cannot post to the channel, so a plain ``/sites``
    or ``/screener_list`` command text isn't tap-to-run for them — and
    even in DM channels, Telegram's auto-detection of inline ``/cmd``
    text into a clickable bot command is inconsistent across mobile
    client versions (2026-05-29 user feedback: `/screener_list` in
    section 11 still rendered as plain text on mobile despite the
    `set_my_commands` registration fix). Inline keyboard URL buttons
    bypass that — Telegram guarantees the tap behavior on all clients.

    Each button uses a deep link `https://t.me/<bot>?start=<payload>`
    routed via `cmd_help`'s `/start <payload>` argument handling.
    Returns None when the bot username isn't known yet."""
    if not bot_username:
        return None
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Screener 도메인 목록",
                url=f"https://t.me/{bot_username}?start=screener_list",
            ),
            InlineKeyboardButton(
                "🔗 외부 참조 사이트",
                url=f"https://t.me/{bot_username}?start=sites",
            ),
        ],
    ])


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
    if ctx.args:
        arg = ctx.args[0].lower()
        if arg == "sites":
            await update.message.reply_text(
                _SITES_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            return
        if arg == "screener_list":
            # Deep-link from the help message's '📊 Screener 도메인 목록'
            # button. Same content + button keyboard as DM /screener_list.
            async def _send_dl(t, rm=None):
                await update.message.reply_text(
                    t,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=rm,
                )
            await _send_screener_domains_list(_send_dl, _screener_list_keyboard())
            return
        if arg.startswith("screener_"):
            # Deep-link from the screener_list panel's per-domain button
            # (`?start=screener_<slug>`). Routes to the screener with
            # that domain in the DM (the destination chat for deep links).
            await _screener_dispatch(update, ctx, arg[len("screener_"):])
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
    # Daily Byte (subsystem='daily_byte') — break out so it isn't folded
    # into NOAH 분석 (which is total − screener − daily_byte − ...).
    today_cost_daily_byte = sum(
        r.get("cost_usd", 0) for r in today_calls
        if r.get("subsystem") == "daily_byte"
    )
    month_cost_daily_byte = sum(
        r.get("cost_usd", 0) for r in calls if r.get("subsystem") == "daily_byte"
    )
    # 부동산(청약 포함) + 블로그 (subsystem='cheongyak'/'realestate'/'blog') break out.
    month_cost_realestate = sum(
        r.get("cost_usd", 0) for r in calls if r.get("subsystem") in ("cheongyak", "realestate"))
    month_cost_blog = sum(
        r.get("cost_usd", 0) for r in calls if r.get("subsystem") == "blog")
    today_cost_analysis = (today_cost - today_cost_screener - today_cost_daily_byte
                           - sum(r.get("cost_usd", 0) for r in today_calls
                                 if r.get("subsystem") in ("cheongyak", "realestate", "blog")))
    month_cost_analysis = (month_cost - month_cost_screener - month_cost_daily_byte
                           - month_cost_realestate - month_cost_blog)

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

    # 한국 수출입(trade) cost — 별도 repo usage.jsonl (사용자 정책 2026-06-02).
    # $TRADE_DATA_DIR/usage.jsonl, 미설정 시 ~/.trade/usage.jsonl. cost_usd /
    # cost_krw + date / ts 양쪽 tolerant (dashboard._compute_stats 와 동일 로직).
    tr_today_usd = tr_month_usd = 0.0
    try:
        import json as _j_tr
        import os as _os_tr
        _tdir = _os_tr.environ.get("TRADE_DATA_DIR", "").strip()
        tr_path = (Path(_tdir) / "usage.jsonl" if _tdir
                   else Path.home() / ".trade" / "usage.jsonl")
        if tr_path.exists():
            today_str_kst = datetime.now(_KST).date().isoformat()
            month_str_kst = today_str_kst[:7]
            with open(tr_path, encoding="utf-8") as _tf:
                for _line in _tf:
                    try:
                        _r = _j_tr.loads(_line)
                    except Exception:
                        continue
                    _cu = _r.get("cost_usd")
                    if _cu is not None and _cu > 0:
                        _usd = float(_cu)
                    else:
                        _ck = _r.get("cost_krw", 0) or 0
                        _usd = float(_ck) / fx if _ck > 0 else 0.0
                    if _usd <= 0:
                        continue
                    _rd = _r.get("date")
                    if not (isinstance(_rd, str) and _rd):
                        _ts2 = _r.get("ts")
                        if not _ts2:
                            continue
                        try:
                            _rd = datetime.fromtimestamp(
                                float(_ts2), _KST).date().isoformat()
                        except Exception:
                            continue
                    if _rd.startswith(month_str_kst):
                        tr_month_usd += _usd
                        if _rd == today_str_kst:
                            tr_today_usd += _usd
    except Exception as _exc:
        log.warning("usage: trade cost read failed: %s", _exc)

    today_total_usd = today_cost + sv_today_usd + tr_today_usd
    month_total_usd = month_cost + sv_month_usd + tr_month_usd

    lines = [
        "📊 <b>NOAH 봇 사용 현황</b> (KST)",
        "",
        "🔬 <b>분석 실행</b>",
        f"  • 오늘: {len(today_runs)}건  (새 {today_new}건 + 캐시 {today_cache}건)",
        f"  • 7일:  {len(week_runs)}건",
        f"  • 30일: {len(month_runs)}건",
        "",
        f"💰 <b>총 비용 (전체 surface 합산)</b> (₩{fx}/$)",
        f"  • 오늘: <b>{krw(today_total_usd)}</b>  (${today_total_usd:.2f})",
        f"  • 30일: <b>{krw(month_total_usd)}</b>  (${month_total_usd:.2f})",
        "",
        "📐 <b>월간 subsystem 분포</b>",
        f"  • NOAH 분석:        {krw(month_cost_analysis)}",
        f"  • Bottleneck Screener: {krw(month_cost_screener)}  ← /screener_cost",
        f"  • Daily Byte:        {krw(month_cost_daily_byte)}  ← /daily_byte_cost",
        f"  • 부동산:            {krw(month_cost_realestate)}  ← /realestate_cost",
        f"  • 블로그:            {krw(month_cost_blog)}",
        f"  • 한국 수출입:       {krw(tr_month_usd)}",
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


def _read_daily_byte_cost_today_month() -> dict:
    """Aggregate Daily Byte cost from ~/.tradingagents/daily_byte_usage.jsonl
    (KST date-tagged). Same shape as _read_screener_cost_today_month."""
    from pathlib import Path as _P
    import json as _j
    path = _P.home() / ".tradingagents" / "daily_byte_usage.jsonl"
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
        log.warning("daily_byte_cost: read failed: %s", exc)
    return out


def _read_realestate_cost_today_month() -> dict:
    """Aggregate 부동산 Byte cost from ~/.tradingagents/realestate_usage.jsonl."""
    from pathlib import Path as _P
    import json as _j
    path = _P.home() / ".tradingagents" / "realestate_usage.jsonl"
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
        log.warning("realestate_cost: read failed: %s", exc)
    return out


def _read_cheongyak_cost_today_month() -> dict:
    """Aggregate 청약 Byte cost from ~/.tradingagents/cheongyak_usage.jsonl."""
    from pathlib import Path as _P
    import json as _j
    path = _P.home() / ".tradingagents" / "cheongyak_usage.jsonl"
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
        log.warning("cheongyak_cost: read failed: %s", exc)
    return out


def _format_screener_domains_list() -> list[str]:
    """Render the current screener domain registry as one OR MORE Telegram
    HTML chunks (each ≤ 4096 UTF-16 cap). Reads from
    ``bot.screener_themes.list_domains()`` so the list auto-updates when
    modules are added — no _HELP_TEXT touches needed.

    Per CLAUDE.md 'Screener 도메인 목록은 _HELP_TEXT inline 금지' rule
    (2026-05-29): future Wave 2-B / Wave 3 / Wave ∞ 도메인 추가는 모듈
    drop 만으로 본 함수 출력 + dashboard `screener_domains.html` 양쪽
    자동 갱신.

    Output strategy (2026-05-29 cap fix): L1 + L2 detailed (별칭 포함)
    in chunk 1, L3 compact (1 line per slug) in chunk 2 — 65 도메인
    × 별칭 포함 형식은 단일 메시지 4096 UTF-16 cap 을 항상 초과하므로
    layer-by-layer chunk packing. Callers send each chunk as a separate
    message, attaching the inline keyboard to the FIRST chunk only.
    """
    from collections import defaultdict
    from bot.screener_themes import list_domains
    ds = list_domains()
    by_layer: dict[str, list[dict]] = defaultdict(list)
    for d in ds:
        by_layer[d.get("layer") or "L1_TREND"].append(d)
    n_l1 = len(by_layer.get("L1_TREND", []))
    n_l2 = len(by_layer.get("L2_SECTOR", []))
    n_l3 = len(by_layer.get("L3_INDUSTRY", []))
    n_adhoc = len(by_layer.get("AD_HOC", []))

    def _utf16(s: str) -> int:
        return len(s.encode("utf-16-le")) // 2

    chunks: list[str] = []

    # Chunk 1 — header + L1 trend + L2 sector + AD_HOC promoted detailed
    # (with aliases). AD_HOC = '/screener <자유어>' 5회+ 사용 후 자동
    # promoted 모듈 (commit 37138cd). 빈 layer 면 자동 skip — 0 promoted
    # 일 때는 chunk 1 에 줄이 추가되지 않음.
    head = [
        f"📊 <b>Bottleneck Screener — 도메인 목록</b> ({len(ds)}개)",
        "",
        f"L1 trend {n_l1} + L2 sector {n_l2} + L3 industry {n_l3}"
        + (f" + 🆕 AD_HOC {n_adhoc}" if n_adhoc else ""),
        ".",
        "하단 버튼: L1+L2+AD_HOC 즉시 클릭. L3 는 <code>/screener_&lt;슬러그&gt;</code>"
        " 직접 타이핑 또는 별칭 사용 (<code>/screener &lt;별칭&gt;</code>).",
        "",
    ]
    for layer_key, layer_label, layer_desc in [
        ("L1_TREND", "📈 L1 Trend", "Cross-cutting cycle 베팅"),
        ("L2_SECTOR", "🏢 L2 Sector", "11 공식 sector (미국 GICS-like)"),
        ("AD_HOC",    "🆕 자유어 promoted",
                      "/screener &lt;자유어&gt; 5회+ 사용 → 자동 모듈"),
    ]:
        items = by_layer.get(layer_key, [])
        if not items:
            continue
        head.append(f"━━━ <b>{layer_label}</b> ({len(items)}개) — {layer_desc} ━━━")
        head.append("")
        for d in items:
            slug = d["slug"]
            domain = d["domain"]
            aliases = [a for a in d["aliases"] if a.lower() != slug]
            alias_str = ""
            if aliases:
                alias_str = f"\n   별칭: {', '.join(aliases[:6])}" + (
                    f" 외 {len(aliases) - 6}개" if len(aliases) > 6 else ""
                )
            head.append(f"/screener_{slug} — <b>{domain}</b>{alias_str}")
            head.append("")
    chunks.append("\n".join(head).rstrip())

    # Chunk(s) 2+ — L3 industry compact (1 line per slug). Pack into
    # ≤ 3800 UTF-16 units per chunk for headroom under the 4096 cap.
    l3_items = by_layer.get("L3_INDUSTRY", [])
    if l3_items:
        l3_header = f"━━━ <b>🔬 L3 Industry</b> ({len(l3_items)}개) — 각 L2 아래 sub-industry ━━━"
        footer = (
            "\n\n📜 변경 이력: <code>archive/screener_domains.html</code>"
            " 페이지 하단 footer"
        )
        cur = [l3_header, ""]
        cur_len = _utf16("\n".join(cur))
        footer_len = _utf16(footer)
        cap = 3800
        for d in l3_items:
            slug = d["slug"]
            domain = d["domain"]
            line = f"/screener_{slug} — {domain}"
            add_len = _utf16(line) + 1  # +1 for the join '\n'
            if cur_len + add_len + footer_len > cap and len(cur) > 2:
                chunks.append("\n".join(cur).rstrip())
                cur = [f"{l3_header} (계속)", ""]
                cur_len = _utf16("\n".join(cur))
            cur.append(line)
            cur_len += add_len
        # Attach footer only to the final L3 chunk.
        cur.append(footer)
        chunks.append("\n".join(cur).rstrip())

    return chunks


async def _send_screener_domains_list(
    send_html,
    keyboard,
) -> None:
    """Send the screener domains list as multiple messages, attaching the
    inline keyboard to the FIRST chunk only. ``send_html`` is an async
    callable ``(text, reply_markup=None) -> awaitable`` — caller-side
    bound to the right destination (DM reply / channel post / etc.).
    """
    chunks = _format_screener_domains_list()
    for i, ch in enumerate(chunks):
        await send_html(ch, keyboard if i == 0 else None)


def _screener_list_keyboard() -> InlineKeyboardMarkup | None:
    """Inline keyboard with URL buttons — limited to L1 trend (6) + L2
    sector (11) = 17 buttons (4 rows of 3 + last row of 2). L3 industry
    48개는 키보드에서 제외 — 65 버튼 22 행은 모바일 렌더 무거움 (사용자
    보고 2026-05-29 "스크리너 list 안나오는 수준"). L3 도메인은 텍스트
    본문 의 `/screener_<slug>` 직접 입력 또는 별칭 사용. 별도 명령
    `/screener_list_l3` 가 L3 만 별도 페이지로 노출 가능 (추후 add 시).

    Returns None when the bot username isn't yet known (button URLs
    require it). In that case the text body alone is sent."""
    try:
        from bot.screener_themes import list_domains
    except Exception:
        return None
    uname = _BOT_USERNAME_CACHE.get("name")
    if not uname:
        return None
    # Keyboard 에는 L1 + L2 + AD_HOC promoted 만 (자주 쓰이는 broad lens
    # + 사용자가 직접 promote 한 자유어). L3 는 본문 텍스트 안내. 65 →
    # 17 + AD_HOC 버튼으로 줄여 모바일 렌더 즉시. AD_HOC 가 다수 누적되어
    # 100 cap 압박 시 후속 fix 검토 (현재 expected 소수).
    short_list = [
        d for d in list_domains()
        if d.get("layer") in ("L1_TREND", "L2_SECTOR", "AD_HOC")
    ]
    rows: list[list[InlineKeyboardButton]] = []
    cur: list[InlineKeyboardButton] = []
    for d in sorted(short_list, key=lambda x: x["slug"]):
        slug = d["slug"]
        cur.append(InlineKeyboardButton(
            f"/{slug}",
            url=f"https://t.me/{uname}?start=screener_{slug}",
        ))
        if len(cur) == 3:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    return InlineKeyboardMarkup(rows) if rows else None


# Bot username is only available after Application.initialize() — cache
# it at _on_startup so synchronous helpers (called from sync render
# paths) can read it without hitting the API every time.
_BOT_USERNAME_CACHE: dict[str, str] = {}


async def cmd_screener_list(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/screener_list — auto-generated list of registered screener domains.
    Sourced from bot.screener_themes registry; _HELP_TEXT only carries
    a link to this command so domain additions don't pressure the
    4096 UTF-16 cap.

    Output: HTML body + inline keyboard with one button per domain.
    Telegram client auto-detection of `/screener_<slug>` text inside
    messages is inconsistent on mobile, so the keyboard guarantees
    tap-to-fire behavior across all clients."""
    if update.message is None:
        return
    async def _send_dm(t, rm=None):
        await update.message.reply_text(
            t,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=rm,
        )
    await _send_screener_domains_list(_send_dm, _screener_list_keyboard())


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


async def cmd_daily_byte_cost(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/daily_byte_cost — show Daily Byte Gemini Pro cost (parallel to
    /screener_cost · /sv_cost). Reads ~/.tradingagents/daily_byte_usage.jsonl."""
    if update.message is None:
        return
    data = _read_daily_byte_cost_today_month()
    today_krw = float(data.get("today_krw", 0) or 0)
    month_krw = float(data.get("month_krw", 0) or 0)
    today_calls = int(data.get("today_calls", 0) or 0)
    month_calls = int(data.get("month_calls", 0) or 0)
    today_pt = int(data.get("today_prompt_tok", 0) or 0)
    today_ot = int(data.get("today_output_tok", 0) or 0)
    text = (
        "💰 <b>Daily Byte 비용</b> (Gemini Pro · 웹 검색 grounding)\n"
        f"오늘: <b>₩{today_krw:,.1f}</b> · {today_calls}회\n"
        f"이번 달: <b>₩{month_krw:,.0f}</b> · {month_calls}회\n"
        f"오늘 tokens: in {today_pt:,} / out {today_ot:,}\n"
        "<i>모델: gemini-2.5-pro · 한국거래일 19:00 Daily + 일 22:00 Weekly · "
        "수치는 pykrx 정확값, Pro 는 섹터/로테이션/catalyst narrative</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_realestate_cost(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/realestate_cost — show 부동산 Byte Gemini Pro cost."""
    if update.message is None:
        return
    data = _read_realestate_cost_today_month()
    text = (
        "💰 <b>부동산 Byte 비용</b> (Gemini Pro · 웹 검색 grounding)\n"
        f"오늘: <b>₩{float(data.get('today_krw',0)):,.1f}</b> · {int(data.get('today_calls',0))}회\n"
        f"이번 달: <b>₩{float(data.get('month_krw',0)):,.0f}</b> · {int(data.get('month_calls',0))}회\n"
        f"오늘 tokens: in {int(data.get('today_prompt_tok',0)):,} / out {int(data.get('today_output_tok',0)):,}\n"
        "<i>모델: gemini-2.5-pro · 금 09:00 주간 + 매월 1일 Monthly · "
        "수치는 MOLIT 실거래 정확값</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_cheongyak_cost(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/cheongyak_cost — show 청약 Byte Gemini Pro cost."""
    if update.message is None:
        return
    data = _read_cheongyak_cost_today_month()
    text = (
        "💰 <b>청약 Byte 비용</b> (Gemini Pro · 신규 분양 피드)\n"
        f"오늘: <b>₩{float(data.get('today_krw',0)):,.1f}</b> · {int(data.get('today_calls',0))}회\n"
        f"이번 달: <b>₩{float(data.get('month_krw',0)):,.0f}</b> · {int(data.get('month_calls',0))}회\n"
        f"오늘 tokens: in {int(data.get('today_prompt_tok',0)):,} / out {int(data.get('today_output_tok',0)):,}\n"
        "<i>모델: gemini-2.5-pro · 평일 10:00·14:00 신규 분양 모집공고 (청약홈)</i>"
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


_WATCH_HELP = (
    "🔔 <b>워치리스트 — 조건 충족 시 자동 알림</b> (LLM 0 · 비용 ₩0)\n"
    "\n"
    "<b>등록</b>: <code>/watch TICKER 조건1 조건2 …</code>\n"
    "한 종목에 조건 여러 개(최대 8개) 가능. 같은 종목 다시 /watch 하면 조건 병합.\n"
    "\n"
    "<b>📋 조건 종류</b>\n"
    "• <code>rsi&lt;30</code> / <code>rsi&gt;70</code> — RSI(14)가 값 아래/위로\n"
    "• <code>price&gt;950</code> / <code>price&lt;800</code> — 현재가가 값 위/아래로\n"
    "• <code>&gt;sma50</code> / <code>&lt;sma200</code> — 현재가가 이동평균 위/아래로\n"
    "• <code>52whigh</code> / <code>52wlow</code> — 52주 신고가/신저가 근접\n"
    "• <code>earnings</code> — 다음 실적 발표 5일 이내(D-5)\n"
    "• <code>foreignbuy</code> / <code>foreignsell</code> — 외국인 5일 순매수/순매도 전환 (KR .KS/.KQ)\n"
    "• <code>instbuy</code> / <code>instsell</code> — 기관 5일 순매수/순매도 전환 (KR)\n"
    "\n"
    "<b>📝 예시</b>\n"
    "• <code>/watch NVDA rsi&lt;30 price&gt;950 earnings</code>\n"
    "• <code>/watch 005930.KS foreignbuy instbuy</code> (외인·기관 순매수 전환 시)\n"
    "• <code>/watch AAPL &lt;sma200 52wlow</code> (200일선 이탈 + 신저가)\n"
    "\n"
    "<b>⚙️ 작동</b>\n"
    "• 30분마다 yfinance로 자동 체크 (LLM 안 씀)\n"
    "• 조건이 <b>거짓→참으로 바뀌는 순간 1회만</b> 알림 (계속 참이어도 도배 안 함; 다시 거짓 됐다 참 되면 재알림)\n"
    "• 충족 시 알림 + <code>/TICKER</code> 정밀 분석 권유\n"
    "\n"
    "<b>📌 관리</b>: 목록 <code>/watchlist</code> · 삭제 <code>/unwatch TICKER</code> (또는 id, 또는 <code>all</code>)\n"
    "대시보드(활성 워치 + 알림 이력): NOAH archive → 🔔 워치리스트"
)


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watch TICKER cond… — register a condition-based alert."""
    if update.message is None:
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(_WATCH_HELP, parse_mode=ParseMode.HTML)
        return
    from bot.watchlist import add_watch, parse_conditions
    ticker = args[0].strip().upper()
    if not TICKER_RE.match(ticker):
        await update.message.reply_text(f"⚠️ 잘못된 티커: {ticker}")
        return
    valid, invalid = parse_conditions(" ".join(args[1:]))
    if not valid:
        await update.message.reply_text(
            "⚠️ 유효한 조건이 없습니다.\n\n" + _WATCH_HELP,
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        w = add_watch(ticker, update.effective_chat.id, valid)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    msg = f"✅ <b>{ticker}</b> 감시 등록\n조건: <code>{' '.join(w['conditions'])}</code>"
    if invalid:
        msg += f"\n⚠️ 무시된 조건: <code>{' '.join(invalid)}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    try:
        from bot.dashboard import regenerate_watchlist_index
        regenerate_watchlist_index()
    except Exception:
        pass


async def cmd_watchlist(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/watchlist — list this chat's active watches."""
    if update.message is None:
        return
    from bot.watchlist import list_watches
    watches = list_watches(update.effective_chat.id)
    if not watches:
        await update.message.reply_text(
            "감시 중인 종목 없음.\n\n" + _WATCH_HELP, parse_mode=ParseMode.HTML)
        return
    lines = ["🔔 <b>워치리스트</b>"]
    for w in watches:
        lines.append(
            f"• <b>{w['ticker']}</b> "
            f"<code>{' '.join(w.get('conditions') or [])}</code>"
            f"  (id {w['id']})"
        )
    lines.append("\n삭제: /unwatch TICKER (또는 id) · 전체 /unwatch all")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unwatch TICKER|id|all — remove watches."""
    if update.message is None:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("사용법: /unwatch TICKER (또는 id, 또는 all)")
        return
    from bot.watchlist import remove_watch
    n = remove_watch(update.effective_chat.id, args[0])
    await update.message.reply_text(
        f"🗑️ {n}개 삭제됨" if n else "삭제할 항목 없음 (티커/id 확인)")
    try:
        from bot.dashboard import regenerate_watchlist_index
        regenerate_watchlist_index()
    except Exception:
        pass


_PAPER_HELP = (
    "🧪 <b>페이퍼 트레이딩</b> (모의 · 돈 0 · 교육)\n"
    "🔗 KIS 모의투자키 설정 시 <b>KR+US 주문은 KIS 서버 체결</b>"
    "(슬리피지·장운영시간·부분체결 반영), 미설정 시 내부 즉시체결\n"
    "<code>/paper</code> — 계좌·포지션·손익\n"
    "<code>/paper buy TICKER 수량 [@지정가]</code> — 매수(시장가/지정가)\n"
    "<code>/paper sell TICKER 수량 [@지정가]</code> — 매도\n"
    "<code>/paper close TICKER</code> — 전량 매도\n"
    "<code>/paper pending</code> · <code>/paper cancel id|TICKER|all</code> — 지정가 대기\n"
    "<code>/paper halt</code> / <code>/paper resume</code> — 거래 중지/재개(kill-switch)\n"
    "<code>/paper auto on|off</code> — NOAH 판정 자동매매(매수 자본5%·5거래일 청산)\n"
    "<code>/paper reset yes</code> — 전체 초기화 · <code>/paper reset TICKER</code> — 개별 종목만\n"
    "예: <code>/paper buy AAPL 10</code> · <code>/paper sell 005930.KS 5</code>"
)


def _regen_paper() -> None:
    try:
        from bot.dashboard import regenerate_paper_index
        regenerate_paper_index()
    except Exception:
        pass


def _won(v) -> str:
    try:
        return f"₩{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/screen [조건|프리셋|list] — generic conditional screener (DM)."""
    if update.message is None:
        return

    async def _send(t):
        await update.message.reply_text(t, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)

    await _handle_screen(context.args or [], _send)


async def _handle_screen(args: list[str], send) -> None:
    """Shared screen handler for DM + channel."""
    raw = " ".join(args).strip()

    if not raw or raw.lower() == "help":
        from bot.stock_screener import format_list_message
        await send(format_list_message())
        return

    if raw.lower() == "list":
        from bot.stock_screener import format_list_message
        await send(format_list_message())
        return

    from bot.stock_screener import (
        PRESETS, parse_conditions, run_screen,
        format_result_message, save_screen_archive,
    )

    market = "KR"
    tokens = raw.split(None, 1)
    if tokens and tokens[0].lower() == "us":
        market = "US"
        raw = tokens[1] if len(tokens) > 1 else ""
        if not raw:
            await send("⚠️ 조건을 입력하세요. 예: <code>/screen us PER&lt;15 DIV&gt;2</code>")
            return

    preset = PRESETS.get(raw.lower())
    if preset:
        cond_text = preset["conditions"]
        _esc = cond_text.replace("<", "&lt;").replace(">", "&gt;")
        label = "🇺🇸 S&amp;P 500" if market == "US" else "🇰🇷 KR"
        await send(
            f"📊 <b>조건부 스크리너</b> ({label}) — {preset['name']}\n"
            f"조건: <code>{_esc}</code>\n⏱ 실행 중..."
        )
    else:
        cond_text = raw

    try:
        conditions = parse_conditions(cond_text)
    except ValueError as exc:
        await send(f"⚠️ {exc}")
        return

    cond_display = " · ".join(c.display() for c in conditions)
    if not preset:
        label = "🇺🇸 S&amp;P 500" if market == "US" else "🇰🇷 KR"
        wait_note = " (yfinance 개별 조회, ~1-2분 소요)" if market == "US" else ""
        await send(
            f"📊 <b>조건부 스크리너</b> ({label})\n"
            f"조건: <code>{cond_display}</code>\n⏱ 실행 중...{wait_note}"
        )

    import asyncio
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_screen(conditions, market=market)
        )
    except Exception as exc:
        log.warning("screen failed: %s", exc)
        await send(f"⚠️ 스크리너 실행 실패: {exc}")
        return

    chunks = format_result_message(result)
    for chunk in chunks:
        await send(chunk)

    if not result.was_cached:
        try:
            save_screen_archive(result, cond_text)
        except Exception:
            pass

    try:
        from bot.dashboard import regenerate_screen_index
        regenerate_screen_index()
    except Exception:
        pass


async def cmd_paper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/paper [buy|sell|close|reset] — E0 모의 매매 (DM). 채널은 on_channel_post."""
    if update.message is None:
        return

    async def _send(t):
        await update.message.reply_text(t, parse_mode=ParseMode.HTML)

    # 멱등 토큰 = 텔레그램 message_id (메시지마다 고유, 재전송만 같음) → 서로
    # 다른 주문은 각각 체결, 중복 전달만 1회.
    await _handle_paper(context.args or [], _send, idem=f"tg:{update.message.message_id}")


def _paper_summary_text(summ: dict) -> str:
    """계좌 요약 → 텔레그램 HTML. DM·채널 공유."""
    part = "" if summ.get("priced_all") else " ⚠️부분"
    lines = [
        "🧪 <b>페이퍼 트레이딩</b> (모의)",
        f"총자산 {_won(summ['total_equity_krw'])}{part} "
        f"(시작 {_won(summ['starting_capital_krw'])} · {summ['total_return_pct']:+.2f}%)",
        f"현금 {_won(summ['cash_krw'])} · 포지션 {_won(summ['positions_value_krw'])}",
        f"실현 {_won(summ['realized_pnl_krw'])} · 미실현 {_won(summ['unrealized_pnl_krw'])}",
    ]
    rows = summ.get("rows", [])
    if rows:
        lines.append(f"— 포지션 ({summ['n_positions']}) —")
        for r in rows:
            cur = r.get("currency")
            sym = {"KRW": "₩", "USD": "$"}.get(cur, "")
            dec = 2 if cur == "USD" else 0
            cur_px = (f"{sym}{r['cur_native']:,.{dec}f}" if r.get("cur_native") is not None else "—")
            ret = (f"{r['ret_pct']:+.1f}%" if r.get("ret_pct") is not None else "—")
            lines.append(
                f"• <b>{r['ticker']}</b> {r['qty']:g}주 @ {sym}{r['avg_cost_native']:,.{dec}f} "
                f"→ {cur_px} ({ret})")
    else:
        lines.append("보유 포지션 없음 — <code>/paper buy TICKER 수량</code>")
    st = summ.get("stats") or {}
    if st.get("n_closes"):
        wr = st.get("win_rate")
        avg = st.get("avg_realized_krw")
        lines.append(f"— 거래 {st['n_closes']}회"
                     + (f" · 승률 {wr:.0f}% · 평균 {_won(avg)}" if wr is not None else ""))
    try:
        from bot import paper_trading
        from bot.risk_gate import status_line
        lines.append(f"— 🤖 자동매매 {'ON' if paper_trading.auto_enabled() else 'OFF'} "
                     f"· 사이징 {paper_trading.auto_size_pct():.0%} "
                     "(<code>/paper auto on|off|size N</code>)")
        lines.append("— " + status_line())
        e1 = summ.get("e1_mode")
        if e1:
            lines.append("— 🔗 KIS 모의투자 연결 (KR+US 서버 체결)")
    except Exception:
        pass
    return "\n".join(lines)


async def _handle_paper(args, send, idem=None) -> None:
    """공유 /paper 핸들러 — DM(cmd_paper) + 채널(on_channel_post) 동일 로직.
    `args` = 'paper' 뒤 토큰 리스트. `send(text)` = async HTML 전송 콜러블.
    `idem` = 텔레그램 message_id 기반 멱등 토큰(재전송 dedup, 별개 주문은 통과)."""
    from bot import paper_trading
    sub = args[0].lower() if args else ""

    if sub in ("help", "도움말", "?"):
        await send(_PAPER_HELP)
        return

    if sub in ("buy", "sell"):
        # @price = 지정가(limit). 토큰 어디에 와도 파싱(예: /paper buy AAPL 10 @300).
        limit = None
        toks = []
        for a in args[1:]:
            if a.startswith("@"):
                try:
                    limit = float(a[1:])
                except ValueError:
                    pass
            else:
                toks.append(a)
        if len(toks) < 2:
            await send(_PAPER_HELP)
            return
        ticker = toks[0].strip().upper()
        if not TICKER_RE.match(ticker):
            await send(f"⚠️ 잘못된 티커: {ticker}")
            return
        try:
            qty = float(toks[1])
        except ValueError:
            await send("⚠️ 수량은 숫자여야 합니다 (예: 10)")
            return
        fn = paper_trading.buy if sub == "buy" else paper_trading.sell
        ok, msg = fn(ticker, qty, idem=idem, limit=limit)
        await send(("✅ " if ok else "⚠️ ") + msg)
        if ok:
            _regen_paper()
        return

    if sub == "pending":
        pend = paper_trading.list_pending()
        if not pend:
            await send("⏳ 지정가 대기 주문 없음")
            return
        lines = ["⏳ <b>지정가 대기</b>"]
        for p in pend:
            sym = {"KRW": "₩", "USD": "$"}.get(p.get("currency"), "")
            lines.append(f"• {'매수' if p['side'] == 'buy' else '매도'} {p['ticker']} "
                         f"{p['qty']:g}주 @ {sym}{p['limit']:,.2f} (id {p['id']})")
        lines.append("취소: <code>/paper cancel &lt;id|TICKER|all&gt;</code>")
        await send("\n".join(lines))
        return

    if sub == "cancel":
        if len(args) < 2:
            await send("사용법: <code>/paper cancel &lt;id|TICKER|all&gt;</code>")
            return
        ok, msg = paper_trading.cancel_pending(args[1])
        await send(("✅ " if ok else "⚠️ ") + msg)
        if ok:
            _regen_paper()
        return

    if sub == "close":
        if len(args) < 2:
            await send("사용법: /paper close TICKER")
            return
        ok, msg = paper_trading.close_position(args[1].strip().upper(), idem=idem)
        await send(("✅ " if ok else "⚠️ ") + msg)
        if ok:
            _regen_paper()
        return

    if sub == "reset":
        arg = args[1] if len(args) >= 2 else ""
        if arg.lower() in ("yes", "확인", "y"):
            ok, msg = paper_trading.reset()
            await send("✅ " + msg)
            _regen_paper()
        elif arg and TICKER_RE.match(arg.upper()):
            # 개별 종목 리셋(purge) — 전체 reset 보다 약하므로 확인 불요.
            ok, msg = paper_trading.purge_ticker(arg)
            await send(("✅ " if ok else "⚠️ ") + msg)
            if ok:
                _regen_paper()
        else:
            await send("⚠️ 전체 초기화: <code>/paper reset yes</code> · "
                       "개별 종목: <code>/paper reset TICKER</code>")
        return

    if sub in ("halt", "resume"):
        from bot.risk_gate import set_halt
        set_halt(sub == "halt")
        await send("⛔ 거래 중지(kill-switch) — 신규 매수 차단(매도/청산은 허용)"
                   if sub == "halt" else "✅ 거래 재개 — kill-switch 해제")
        _regen_paper()
        return

    if sub == "auto":
        if len(args) >= 3 and args[1].lower() == "size":
            try:
                pct = float(args[2]) / 100.0
            except ValueError:
                await send("⚠️ 예: <code>/paper auto size 10</code> (자본의 10%)")
                return
            newpct = paper_trading.set_auto_size(pct)
            await send(f"🤖 자동매수 사이징 = 자본의 <b>{newpct:.0%}</b>")
            _regen_paper()
            return
        if len(args) >= 2 and args[1].lower() in ("on", "off"):
            paper_trading.set_auto(args[1].lower() == "on")
            from bot.paper_signals import HORIZON_DAYS
            if args[1].lower() == "on":
                await send(f"🤖 자동매매 <b>ON</b> — NOAH 분석 판정대로 페이퍼 주문 "
                           f"(매수=자본 {paper_trading.auto_size_pct():.0%}·{HORIZON_DAYS}거래일 "
                           f"자동청산, 매도=보유 시 청산). Risk Gate 동일 적용.")
            else:
                await send("🤖 자동매매 <b>OFF</b> — 수동 명령만.")
            _regen_paper()
        else:
            cur = "ON" if paper_trading.auto_enabled() else "OFF"
            await send(f"🤖 자동매매 현재 <b>{cur}</b> · 사이징 "
                       f"{paper_trading.auto_size_pct():.0%}. 변경: "
                       "<code>/paper auto on|off</code> · <code>/paper auto size N</code>")
        return

    await send(_paper_summary_text(paper_trading.summary()))


async def _run_screener_and_send(send, *, domain: str | None = None,
                                  theme: dict | None = None,
                                  cache_key: str | None = None,
                                  force_fresh: bool = False) -> None:
    """Run the screener in a thread (it's a synchronous Pro call that
    blocks for ~3-5 minutes) and stream chunks back through `send`.
    Used by both DM cmd_screener and the channel /screener path.

    Pass exactly one of ``domain`` (legacy static-registry alias) or
    ``theme`` (pre-resolved dict — used by free-text Phase 0 path).

    24h 디스크 캐시: ``cache_key`` 가 주어지면 today's KST cache 를 우선
    조회. ``force_fresh=True`` 면 캐시 무시 + 새 실행 + save.
    """
    import asyncio
    from functools import partial
    from bot.screener import run_screener, run_screener_with_theme, format_for_telegram
    from bot.screener_themes import available_summary as _scr_avail
    loop = asyncio.get_running_loop()
    # .busy marker — Screener 는 5-10분 blocking 이라 watchdog 의 180초
    # polling-hang 체크가 실행 중 재시작 → 분석 살해(2026-06-01 Hospitality
    # & Leisure run 유실). /ticker 분석과 동일하게 _busy_acquire 로 보호하면
    # watchdog 가 .busy fresh(<12분) 동안 재시작 skip. release 는 finally.
    await _busy_acquire()
    # keepalive: OS 데몬 스레드로 .busy 를 주기적 refresh (asyncio task 는
    # 워커 GIL 점유 시 starve 됨 — 2026-06-01 surfaced). Screener 가 12분
    # 넘어도 watchdog stale-restart 원천 차단. 종료 시 stop() 후 release.
    _keepalive = _BusyKeepalive().start()
    try:
        if theme is not None:
            fn = partial(run_screener_with_theme, theme,
                         cache_key=cache_key, force_fresh=force_fresh)
        else:
            fn = partial(run_screener, domain or "bottleneck",
                         force_fresh=force_fresh)
        result = await loop.run_in_executor(None, fn)
    except Exception as exc:
        log.exception("screener: orchestrator threw: %s", exc)
        await send(f"⚠️ Screener 오류 — {exc.__class__.__name__}")
        return
    finally:
        _keepalive.stop()
        await _busy_release()
    if result is None:
        await send(
            "⚠️ Screener 실행 실패 — GOOGLE_API_KEY 누락 / 미상 도메인 / "
            f"Pro 응답 없음.\n사용 가능 도메인: <code>{_scr_avail()}</code>"
        )
        return
    # chunk별 실패 격리 — 한 chunk 가 HTML 400 (이스케이프 안 된 < / & 등)
    # 으로 실패해도 나머지 chunk 는 계속 전송 (Tobacco 2026-05-31: 6969.HK
    # 다음 chunk 의 '< $1M' 가 400 → 이후 전부 누락됐던 버그). 실패 시
    # 태그 제거한 plain-text 로 1회 재시도.
    import re as _re_scr
    for chunk in format_for_telegram(result):
        try:
            await send(chunk)
        except Exception as exc:
            log.warning("screener: chunk HTML send 실패 (%s) — plain-text 재시도",
                        exc.__class__.__name__)
            try:
                await send(_re_scr.sub(r"<[^>]+>", "", chunk))
            except Exception as exc2:
                log.warning("screener: chunk plain-text 재시도도 실패: %s", exc2)


def _strip_fresh_flag(raw_domain: str) -> tuple[str, bool]:
    """Detect and strip 'fresh' override flag from raw_domain. Returns
    (cleaned_domain, force_fresh). Recognized forms (case-insensitive):
    'bottleneck fresh' / 'ev fresh' / 'carbon nanotube fresh' / standalone
    'fresh' (= bottleneck fresh)."""
    parts = (raw_domain or "").strip().split()
    if parts and parts[-1].lower() == "fresh":
        return " ".join(parts[:-1]).strip().lower(), True
    return raw_domain, False


async def _resolve_screener_target(send, raw_domain: str):
    """Common resolver for /screener arg paths — handles alias hit /
    fuzzy redirect to existing domain / free-text Phase 0 generation.
    Sends the appropriate progress message via ``send`` callable.

    Returns dict: {mode, theme?, cache_key?, force_fresh}
      • mode='theme' — caller passes theme + cache_key + force_fresh to
        _run_screener_and_send
      • mode='error' — already sent error message; caller aborts
    """
    from bot.screener_themes import resolve as _scr_resolve, resolve_slug as _scr_slug

    # Parse 'fresh' override flag (e.g. '/screener bottleneck fresh').
    raw_domain, force_fresh = _strip_fresh_flag(raw_domain)
    fresh_note = " · ⏭️ fresh flag (캐시 무시)" if force_fresh else ""

    # 1) Empty input → default bottleneck (back-compat).
    if not raw_domain:
        theme = _scr_resolve("")
        if theme is not None:
            await send(_screener_start_banner(theme) + fresh_note)
            return {"mode": "theme", "theme": theme,
                    "cache_key": "bottleneck", "force_fresh": force_fresh}
        return {"mode": "error"}  # shouldn't happen — bottleneck always registered

    # 2) Static registry hit.
    theme = _scr_resolve(raw_domain)
    if theme is not None:
        slug = _scr_slug(raw_domain) or raw_domain.strip().lower()
        await send(_screener_start_banner(theme) + fresh_note)
        return {"mode": "theme", "theme": theme,
                "cache_key": slug, "force_fresh": force_fresh}

    # 3) Free-text path — fuzzy redirect or Phase 0 generation.
    from bot.screener_freetext import (
        resolve_freetext as _resolve_freetext,
        count_today_freetext as _ft_count_today,
        count_text_uses as _ft_count_uses,
        _cache_key as _ft_cache_key,
    )

    # Daily soft-cap notice (non-blocking — user can /screener_cost to monitor).
    used_today = _ft_count_today()
    soft_cap_note = ""
    if used_today >= 5:
        soft_cap_note = (
            f"\n⚠️ 오늘 자유어 {used_today + 1}회째 — 비용 누적 주의."
            " /screener_cost 로 확인 가능."
        )

    await send(
        f"🔍 도메인 '<code>{raw_domain}</code>' 자동 생성 중...\n"
        f"Phase 0: Pro 가 binding layer + catalyst + 지역 분포 식별 (~30초, ~₩50-80)"
        f"{soft_cap_note}"
    )

    import asyncio
    loop = asyncio.get_running_loop()
    ft_theme, err, cost_krw, was_cached = await loop.run_in_executor(
        None, _resolve_freetext, raw_domain,
    )

    # 3a) Fuzzy redirect to existing static domain.
    if err and err.startswith("__REDIRECT__:"):
        target_slug = err[len("__REDIRECT__:"):]
        target_theme = _scr_resolve(target_slug)
        if target_theme is not None:
            await send(
                f"💡 입력 '<code>{raw_domain}</code>' 이 기존 도메인 "
                f"<b>{target_theme['domain']}</b> 와 일치 — 자동 라우팅."
            )
            await send(_screener_start_banner(target_theme) + fresh_note)
            return {"mode": "theme", "theme": target_theme,
                    "cache_key": target_slug, "force_fresh": force_fresh}

    # 3b) Phase 0 reject / hard error.
    if ft_theme is None:
        await send(
            f"⚠️ 자유텍스트 도메인 생성 실패 — {err or 'Unknown'}\n"
            f"기존 65 도메인 목록: /screener_list"
        )
        return {"mode": "error"}

    # 3c) Success — show generated theme summary + start banner.
    cache_hint = "(24h 캐시 사용)" if was_cached else f"(Phase 0 비용 ₩{cost_krw:.1f})"
    uses = _ft_count_uses(raw_domain)
    promote_hint = ""
    if uses >= 5:
        # Auto-promote to static module on 5th+ use. First-time hit writes
        # the file; subsequent calls return None (already promoted) so
        # the message degrades gracefully. Module activates next bot
        # restart (auto-update timer picks up within 1 min of any push).
        from bot.screener_freetext import promote_to_module as _ft_promote
        promoted_slug = _ft_promote(raw_domain, ft_theme)
        if promoted_slug:
            promote_hint = (
                f"\n📌 누적 {uses}회 사용 — 정식 모듈로 promote 완료: "
                f"<code>bot/screener_themes/{promoted_slug}.py</code>. "
                f"다음 봇 재시작 후 <code>/screener {promoted_slug}</code> 직접 라우팅."
            )
        else:
            promote_hint = (
                f"\n📌 누적 {uses}회 사용 — 정식 모듈 이미 promoted 됨 "
                f"(같은 slug 의 모듈 존재). 봇 재시작 후 정적 도메인으로 라우팅."
            )
    layer_count = len(ft_theme.get("binding_layer_taxonomy", []))
    catalyst_count = len(ft_theme.get("catalyst_types", []))
    region_count = len(ft_theme.get("regional_concentration", {}))
    await send(
        f"✅ <b>{ft_theme['domain']}</b> 도메인 즉석 생성 완료 {cache_hint}\n"
        f"  • binding layer {layer_count}개 / catalyst {catalyst_count}개 / "
        f"region {region_count}개\n"
        f"  • ⚠️ 자유텍스트 도메인 — Pro 즉석 생성, 정적 도메인 대비 깊이 ±20%."
        f"{promote_hint}"
    )
    await send(_screener_start_banner(ft_theme) + fresh_note)
    # 자유어는 freetext cache_key (sha256[:12]) 로 캐싱 — 같은 자유어 24h 내
    # 재호출 시 본 분석 (5-phase) 도 skip + ₩0. promote 직후의 freetext
    # 입력도 정식 모듈로 라우팅되기 전까지는 freetext key 캐시 활용.
    return {"mode": "theme", "theme": ft_theme,
            "cache_key": _ft_cache_key(raw_domain), "force_fresh": force_fresh}


def _screener_start_banner(theme: dict) -> str:
    """Common Phase β progress banner — emitted right before the long
    Pro pipeline so the user sees which theme + horizon is in flight."""
    return (
        f"📊 <b>Bottleneck Screener</b> 시작 — <b>{theme['domain']}</b> "
        f"({theme.get('horizon','')} 관점, Phase β · 실시간 데이터 + 웹 검색)\n"
        "⏱ <b>6-12분 소요</b> — Phase 1·2 (Pro+웹 검색 후보 식별) → "
        "Phase 3 (병렬 실시간 fetch) → Phase 4·5 (Pro+웹 검색 분석)\n"
        "💡 fetch hung 시 120s 후 partial 결과로 자동 진행"
    )


async def _screener_dispatch(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw_domain: str,
) -> None:
    """Common implementation for /screener arg-based + /screener_<slug>
    per-domain shortcut commands. Resolves the theme (static registry +
    fuzzy redirect + free-text Phase 0), sends a progress message, then
    dispatches to ``_run_screener_and_send``."""
    if update.message is None:
        return
    async def _send(t: str) -> None:
        await update.message.reply_text(
            t, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
    resolved = await _resolve_screener_target(_send, raw_domain)
    if resolved.get("mode") == "error":
        return
    chat_id = update.message.chat_id
    send_chunk = lambda t: ctx.bot.send_message(
        chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    await _run_screener_and_send(
        send=send_chunk,
        theme=resolved.get("theme"),
        cache_key=resolved.get("cache_key"),
        force_fresh=resolved.get("force_fresh", False),
    )


async def cmd_screener(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/screener [domain] — Bottleneck Screener.

    Per-domain shortcuts (`/screener_bottleneck`, `/screener_healthcare`
    etc.) registered separately via ``_register_dynamic_screener_
    handlers`` so clicks fire single-tap from /screener_list output.
    Theme registry in bot/screener_themes/. See CLAUDE.md 'Bottleneck
    Screener' section for full design.
    """
    raw_domain = (" ".join(ctx.args).strip().lower() if ctx.args else "")
    await _screener_dispatch(update, ctx, raw_domain)


def _register_dynamic_screener_handlers(app) -> None:
    """Register one CommandHandler per discovered screener domain so
    `/screener_<slug>` works as a single-tap command in Telegram (matches
    the /find_all / /papers_guide pattern the user referenced 2026-05-29).
    Re-runs at bot boot; new domains added between restarts won't show
    up until the next deploy cycle (~1 min via stock-bot-update.service)."""
    try:
        from bot.screener_themes import list_domains
        for d in list_domains():
            slug = d["slug"]

            # Bind slug into the closure via default argument — without
            # this all handlers would capture the last-iterated slug.
            async def _h(update, ctx, _slug=slug):
                await _screener_dispatch(update, ctx, _slug)

            app.add_handler(CommandHandler(f"screener_{slug}", _h))
        log.info(
            "screener: registered %d per-domain shortcut commands",
            len(list_domains()),
        )
    except Exception as exc:
        log.warning("screener: dynamic handler registration failed: %s", exc)


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


async def _periodic_paper_pending(application=None) -> None:
    """지정가 대기 주문을 ~30분마다 체결 확인(E0.5d limit orders). 가격 도달 시
    시장가 체결(Risk Gate 포함) + 채널 알림 + 페이지 갱신. 페이퍼라 비용 0.
    E1 활성 시 KIS 잔고 reconcile 도 같이 수행(30분마다)."""
    while True:
        await asyncio.sleep(1800)   # 30분
        try:
            from bot import paper_trading
            filled = paper_trading.fill_pending()
            if filled:
                try:
                    from bot.dashboard import regenerate_paper_index
                    regenerate_paper_index()
                except Exception:
                    pass
                if application is not None and CHANNEL_CHAT_IDS:
                    for _m in filled:
                        for _cid in CHANNEL_CHAT_IDS:
                            try:
                                await application.bot.send_message(
                                    chat_id=_cid, text="⏳→✅ 지정가 체결: " + _m)
                            except Exception:
                                pass
            # E1 reconcile: KIS 잔고 ↔ ledger 비교 (30분마다, 비용 0)
            try:
                drift = paper_trading.run_reconcile()
                if drift and drift.get("position_drifts") and application and CHANNEL_CHAT_IDS:
                    drifts = drift["position_drifts"]
                    lines = ["⚠️ <b>KIS↔ledger 포지션 불일치 감지</b>"]
                    for d in drifts[:5]:
                        lines.append(f"• {d['code']}: 우리 {d['ours']}주 / KIS {d['kis']}주")
                    for _cid in CHANNEL_CHAT_IDS:
                        try:
                            await application.bot.send_message(
                                chat_id=_cid, text="\n".join(lines),
                                parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
            except Exception:
                log.debug("paper reconcile skipped")
        except Exception:
            log.exception("paper pending fill failed")


async def _periodic_market_refresh() -> None:
    """market.html(글로벌 스냅샷 + 매크로 + 리서치)을 5분마다 재생성 — 페이지의
    '5분 주기 갱신' 라벨을 실제 충족. 무거운 fetch 는 thread 로 오프로드해 폴링
    루프 비차단(watchdog getUpdates 영향 0). research/macro/earnings 는 자체
    캐시(1h+)라 5분마다 실제 재fetch 되는 건 주로 지수/섹터 스냅샷.
    관심종목은 api/favorites 로 별도 라이브 fetch 라 무관."""
    while True:
        await asyncio.sleep(300)   # 5분
        try:
            from bot.dashboard import regenerate_market_index
            await asyncio.to_thread(regenerate_market_index)
        except Exception:
            log.exception("periodic market.html refresh failed")


async def _periodic_dashboard_requests(application) -> None:
    """대시보드 '분석/실행' 버튼 요청 스풀 폴러 (5초 간격).

    dashboard_server(별도 프로세스)가 ~/.tradingagents/dashboard_requests/
    에 떨어뜨린 요청을 집어 **텔레그램 채널 명령과 동일 경로**로 실행 —
    결과는 채널 + 대시보드 아카이브에 똑같이 게시 (파이프라인 중복 0):
      • analyze  → _analyze_ticker_and_post (채널 /TICKER 와 동일)
      • screener → _resolve_screener_target + _run_screener_and_send
      • screen   → _handle_screen (조건부, ₩0)
    CHANNEL_CHAT_IDS 미설정이면 결과를 게시할 곳이 없으므로 요청을 소비만
    하고 버린다(스풀 stale 폐기가 어차피 30분에 정리). 요청별 try/except —
    한 요청 실패가 폴러를 죽이지 않는다."""
    from bot.dashboard_requests import take_all
    while True:
        await asyncio.sleep(5)
        try:
            reqs = await asyncio.to_thread(take_all)
        except Exception:
            log.exception("dashboard request poll failed")
            continue
        if not reqs:
            continue
        if not CHANNEL_CHAT_IDS:
            log.warning("dashboard requests dropped — CHANNEL_CHAT_IDS unset: %s", reqs)
            continue
        chat_id = next(iter(CHANNEL_CHAT_IDS))
        bot = application.bot
        for req in reqs:
            kind = req.get("kind")
            query = (req.get("query") or "").strip()
            log.info("dashboard request: kind=%s query=%r", kind, query)
            try:
                if kind == "analyze":
                    raw = query.upper()
                    if not TICKER_RE.match(raw):
                        log.warning("dashboard analyze: bad ticker %r", raw)
                        continue
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"🌐 대시보드 요청 — <code>/{_html.escape(raw)}</code> 분석",
                        parse_mode=ParseMode.HTML,
                    )
                    await _analyze_ticker_and_post(bot, chat_id, raw)
                elif kind == "screener":
                    async def _send(t: str) -> None:
                        await bot.send_message(
                            chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    await _send(
                        "🌐 대시보드 요청 — Bottleneck Screener"
                        + (f" <code>{_html.escape(query)}</code>" if query else " (기본)")
                    )
                    resolved = await _resolve_screener_target(_send, query.lower())
                    if resolved.get("mode") == "error":
                        continue
                    await _run_screener_and_send(
                        send=_send,
                        theme=resolved.get("theme"),
                        cache_key=resolved.get("cache_key"),
                        force_fresh=resolved.get("force_fresh", False),
                    )
                elif kind == "screen":
                    async def _send2(t: str) -> None:
                        await bot.send_message(
                            chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    await _send2(
                        f"🌐 대시보드 요청 — 조건부 스크리너 <code>{_html.escape(query)}</code>"
                    )
                    await _handle_screen(query.split(), _send2)
            except Exception:
                log.exception("dashboard request failed: %s", req)


async def _periodic_dashboard_refresh(application=None) -> None:
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
            from bot.dashboard import (regenerate_index, regenerate_daily_byte_index,
                                       regenerate_realestate_index,
                                       regenerate_cheongyak_index,
                                       regenerate_gics_candidates_index,
                                       regenerate_reddit_insider_index,
                                       regenerate_watchlist_index,
                                       regenerate_paper_index,
                                       regenerate_market_index,
                                       regenerate_dart_feed_index)
            regenerate_index()
            regenerate_daily_byte_index()
            regenerate_realestate_index()
            regenerate_cheongyak_index()
            regenerate_gics_candidates_index()
            regenerate_reddit_insider_index()
            regenerate_watchlist_index()
            regenerate_market_index()
            regenerate_dart_feed_index()
            # 페이퍼(E0.5b): 5거래일 horizon 도래 자동 포지션 청산 + 페이지 갱신.
            # E0.5c: 청산 시 채널 알림(설정된 채널 있을 때) — 조용히 닫히지 않게.
            try:
                from bot import paper_trading
                _closed = paper_trading.close_due_positions()
                if _closed:
                    log.info("paper: %d horizon auto-close(s)", len(_closed))
                    if application is not None and CHANNEL_CHAT_IDS:
                        for _m in _closed:
                            for _cid in CHANNEL_CHAT_IDS:
                                try:
                                    await application.bot.send_message(
                                        chat_id=_cid,
                                        text="🤖 자동청산(5거래일 만기): " + _m)
                                except Exception:
                                    pass
            except Exception:
                log.exception("paper horizon close failed")
            regenerate_paper_index()
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
    # Orphan .busy 청소 — 직전 인스턴스가 분석/Screener 도중 watchdog 에
    # 살해되면 clear_busy 가 안 불려 .busy 가 stale 로 남는다. 그러면
    # watchdog 가 '분석 중' 으로 오인해 진짜 hang 일 때도 재시작을 skip
    # (2026-06-01 surfaced). 재시작된 이 인스턴스는 분석을 들고 있지
    # 않으므로 startup 시 무조건 청소 (refcount 도 0 으로 fresh).
    try:
        global _busy_refcount
        _busy_refcount = 0
        clear_busy()
        log.info("startup: orphan .busy marker cleared")
    except Exception as exc:
        log.warning("startup: .busy cleanup failed: %s", exc)
    try:
        from bot.dashboard import regenerate_screener_index
        regenerate_screener_index()
        log.info("startup: screener.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: screener.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_daily_byte_index
        regenerate_daily_byte_index()
        log.info("startup: daily_byte.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: daily_byte.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_realestate_index
        regenerate_realestate_index()
        log.info("startup: realestate.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: realestate.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_cheongyak_index
        regenerate_cheongyak_index()
        log.info("startup: cheongyak.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: cheongyak.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_gics_candidates_index
        regenerate_gics_candidates_index()
        log.info("startup: gics_candidates.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: gics_candidates.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_watchlist_index
        regenerate_watchlist_index()
        log.info("startup: watchlist.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: watchlist.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_reddit_insider_index
        regenerate_reddit_insider_index()
        log.info("startup: reddit_insider.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: reddit_insider.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_market_index
        regenerate_market_index()
        log.info("startup: market.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: market.html regen failed: %s", exc)
    try:
        from bot.dashboard import regenerate_dart_feed_index
        regenerate_dart_feed_index()
        log.info("startup: dart_feed.html regenerated with current code")
    except Exception as exc:
        log.warning("startup: dart_feed.html regen failed: %s", exc)
    # DART 공시 즉시 채움 — 타이머(30분)를 기다리지 않고 startup 직후 백그라운드
    # 1회 fetch → 재배포 시 수 초 내 공시 표시(빈 '전체 0' 방지). 무료·LLM 0.
    try:
        import threading as _dt_thr

        def _dart_initial_fetch():
            try:
                from bot.dart_feed import run_once
                items = run_once()
                from bot.dashboard import regenerate_dart_feed_index as _rg
                _rg()
                log.info("startup: DART feed initial fetch %d items", len(items))
            except Exception as exc:
                log.warning("startup: DART initial fetch failed: %s", exc)

        _dt_thr.Thread(target=_dart_initial_fetch, daemon=True).start()
    except Exception as exc:
        log.warning("startup: DART initial fetch thread failed: %s", exc)

    # 실적 캘린더 과거 월(IR) 캐시 워밍 — 아카이브가 닿지 않는 직전 2개월을
    # 백그라운드로 미리 fetch·캐시 → 사용자가 과거 월 이동 시 즉시 표시
    # (firehose 페이지네이션 cold load 25~40초를 시작 시점에 미리 처리).
    # 12h 캐시라 재시작 내 반복 안 함. 무료·LLM 0·graceful.
    try:
        import threading as _ir_thr

        def _ir_month_warm():
            try:
                from datetime import date as _d
                t = _d.today()

                def _shift(delta):
                    y, m = t.year, t.month + delta
                    while m < 1:
                        m += 12
                        y -= 1
                    while m > 12:
                        m -= 12
                        y += 1
                    return y, m

                # KIND(실제 IR 개최일) — 이번 달 + 다음 달(미래 일정)
                try:
                    from bot.kind_ir_client import fetch_kind_ir_month
                    for delta in (0, 1):
                        fetch_kind_ir_month(*_shift(delta))
                except Exception as exc:
                    log.warning("startup: KIND warm failed: %s", exc)
                # DART 폴백 — 직전 2개월(과거 IR)
                from bot.dart_feed import fetch_kr_ir_month
                for delta in (-1, -2):
                    fetch_kr_ir_month(*_shift(delta))
                log.info("startup: IR 캘린더 캐시 워밍 완료(KIND 0/+1 · DART -1/-2)")
            except Exception as exc:
                log.warning("startup: IR-month warm failed: %s", exc)

        _ir_thr.Thread(target=_ir_month_warm, daemon=True).start()
    except Exception as exc:
        log.warning("startup: IR-month warm thread failed: %s", exc)
    # One-time price-chart backfill for pre-chart (schema v1) archive
    # entries. Marker-gated so it runs once per install, not every restart.
    # Background thread — ~N yfinance fetches shouldn't block startup. Free
    # (yfinance only). After filling, regenerate detail pages so the old
    # analyses render their new charts.
    try:
        import threading
        from pathlib import Path as _Path

        _bf_marker = _Path.home() / ".tradingagents" / ".charts_backfilled"
        if not _bf_marker.exists():
            def _run_backfill():
                try:
                    from bot.archive import backfill_price_charts
                    from bot.dashboard import regenerate_index
                    n = backfill_price_charts()
                    _bf_marker.parent.mkdir(parents=True, exist_ok=True)
                    _bf_marker.write_text("done", encoding="utf-8")
                    if n:
                        regenerate_index()
                    log.info("startup: price-chart backfill filled %d entries", n)
                except Exception as exc:
                    log.warning("startup: price-chart backfill failed: %s", exc)

            threading.Thread(target=_run_backfill, daemon=True).start()
            log.info("startup: price-chart backfill thread launched")
    except Exception as exc:
        log.warning("startup: backfill launch failed: %s", exc)
    # One-time stock_info backfill for pre-v3 archive entries.
    # Same pattern as chart backfill — marker-gated, background thread, free.
    try:
        import threading
        from pathlib import Path as _Path

        _si_marker = _Path.home() / ".tradingagents" / ".stock_info_backfilled"
        if not _si_marker.exists():
            def _run_stock_info_backfill():
                try:
                    from bot.archive import backfill_stock_info
                    from bot.dashboard import regenerate_index
                    n = backfill_stock_info()
                    _si_marker.parent.mkdir(parents=True, exist_ok=True)
                    _si_marker.write_text("done", encoding="utf-8")
                    if n:
                        regenerate_index()
                    log.info("startup: stock_info backfill filled %d entries", n)
                except Exception as exc:
                    log.warning("startup: stock_info backfill failed: %s", exc)

            threading.Thread(target=_run_stock_info_backfill, daemon=True).start()
            log.info("startup: stock_info backfill thread launched")
    except Exception as exc:
        log.warning("startup: stock_info backfill launch failed: %s", exc)
    # One-time detail-tab backfill: fill empty news/research/consensus tabs
    # in existing archives using our market-specific data sources (Naver, 한경,
    # cnyes, Kabutan). Same pattern as chart/stock_info backfill.
    try:
        import threading
        from pathlib import Path as _Path

        _dt_marker = _Path.home() / ".tradingagents" / ".detail_tabs_backfilled"
        if not _dt_marker.exists():
            def _run_detail_tabs_backfill():
                try:
                    from bot.archive import backfill_detail_tabs
                    from bot.dashboard import regenerate_index
                    n = backfill_detail_tabs()
                    _dt_marker.parent.mkdir(parents=True, exist_ok=True)
                    _dt_marker.write_text("done", encoding="utf-8")
                    if n:
                        regenerate_index()
                    log.info("startup: detail-tab backfill filled %d entries", n)
                except Exception as exc:
                    log.warning("startup: detail-tab backfill failed: %s", exc)

            threading.Thread(target=_run_detail_tabs_backfill, daemon=True).start()
            log.info("startup: detail-tab backfill thread launched")
    except Exception as exc:
        log.warning("startup: detail-tab backfill launch failed: %s", exc)
    # Populates the 'Menu' button beside the input area + the '/' typing
    # autocomplete in DMs. Dynamic per-ticker commands like /NVDA aren't
    # registered (the universe is too large) — Telegram still recognises
    # them as tappable commands when typed in plain text.
    #
    # 2026-05-29 fix: mobile 클라이언트가 `/screener_list` + `/screener_
    # <slug>` 같은 미등록 명령을 messages body 안에서 클릭-hyperlink
    # 자동 인식 안 함 (데스크탑은 하지만 mobile 보장 X). set_my_commands
    # 로 등록하면 모든 클라이언트에서 자동 hyperlink + autocomplete +
    # menu 노출. Telegram cap 은 scope 당 100개 → 정적 7 + 동적 도메인
    # 10 = 17개, Wave 2-B/3 까지 확장해도 안전.
    # Cache bot username — _screener_list_keyboard etc need it to build
    # deep-link URLs but they're called from sync render paths.
    try:
        me = await application.bot.get_me()
        if me and me.username:
            _BOT_USERNAME_CACHE["name"] = me.username
            log.info("bot username cached: @%s", me.username)
    except Exception as exc:
        log.warning("get_me failed (deep-link URLs unavailable): %s", exc)

    try:
        commands = [
            BotCommand("start", "사용법 안내"),
            BotCommand("help", "사용법 안내"),
            BotCommand("usage", "사용량 / 통합 비용 / 7일 차트"),
            BotCommand("sv_cost", "Standard View 비용"),
            BotCommand("screener_cost", "Bottleneck Screener 비용 (Pro)"),
            BotCommand("daily_byte_cost", "Daily Byte 비용 (KR 수급 브리프)"),
            BotCommand("cheongyak_cost", "청약 Byte 비용 (신규 분양 피드)"),
            BotCommand("realestate_cost", "부동산 Byte 비용 (실거래 브리프)"),
            BotCommand("screener_list", "Screener 도메인 목록 (전체)"),
            BotCommand("sites", "참고 사이트"),
            BotCommand("watch", "종목 조건 감시 알림 (rsi/price/sma/52w/earnings)"),
            BotCommand("watchlist", "감시 목록 보기"),
            BotCommand("unwatch", "감시 삭제 (TICKER/id/all)"),
            BotCommand("paper", "페이퍼 트레이딩 (모의 매매·돈 0)"),
            BotCommand("screen", "조건부 스크리너 (PER<15 PBR<1 등 자유 조건)"),
            BotCommand("screener", "Bottleneck 종목 발굴 (기본=AI 데이터센터)"),
            BotCommand("compare", "두 종목 비교 (채널에서 사용)"),
            BotCommand("portfolio", "💼 자산 (뱅크샐러드 zip 업로드)"),
        ]
        # Per-domain shortcut commands. Description = theme display name
        # (capped at 100 chars to leave headroom under Telegram's 256-char
        # description limit). Sorted by slug for stable ordering in the
        # client autocomplete menu.
        try:
            from bot.screener_themes import list_domains
            for d in sorted(list_domains(), key=lambda x: x["slug"]):
                desc = (d.get("domain") or d["slug"])[:100]
                commands.append(BotCommand(f"screener_{d['slug']}", desc))
        except Exception as exc:
            log.warning("set_my_commands: dynamic domain registration failed: %s", exc)
        await application.bot.set_my_commands(commands)
        log.info("set_my_commands: registered %d commands", len(commands))
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)

    # Kick off the background pending-entry resolver. fire-and-forget —
    # the task runs forever, sleeping 12h between cycles. Stored on the
    # application so it stays referenced (otherwise the GC could collect it).
    application._auto_resolve_task = asyncio.create_task(_periodic_auto_resolve())
    application._dashboard_refresh_task = asyncio.create_task(_periodic_dashboard_refresh(application))
    application._paper_pending_task = asyncio.create_task(_periodic_paper_pending(application))
    application._market_refresh_task = asyncio.create_task(_periodic_market_refresh())
    # 대시보드 '분석/실행' 버튼 요청 스풀 폴러 (5초) — dashboard_server 가
    # 떨어뜨린 요청을 채널 명령과 동일 경로로 실행.
    application._dashboard_requests_task = asyncio.create_task(
        _periodic_dashboard_requests(application))

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


_PF_DASH_BASE = os.getenv(
    "NOAH_DASHBOARD_BASE",
    "http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/",
)


def _pf_link() -> str:
    return _PF_DASH_BASE.rstrip("/") + "/portfolio.html"


async def cmd_portfolio(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/portfolio — 저장된 자산 요약 + 대시보드 링크. 없으면 업로드 안내."""
    try:
        from bot.portfolio import load, format_summary_text
    except Exception as exc:
        await update.message.reply_text(f"자산 모듈 로드 실패: {exc}")
        return
    m = load()
    if not m or not m.get("holdings"):
        await update.message.reply_text(
            "아직 업로드된 자산이 없습니다.\n뱅크샐러드 → 데이터 내보내기 → 메일로 받은 "
            "<b>zip 파일을 이 대화에 그대로 보내</b>주세요. 자동으로 파싱·정리해 "
            f"통합 자산 대시보드를 만듭니다.\n📊 {_pf_link()}",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text(
        format_summary_text(m) + f"\n\n📊 대시보드: {_pf_link()}"
    )


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
    app.add_handler(CommandHandler("daily_byte_cost", cmd_daily_byte_cost))
    app.add_handler(CommandHandler("cheongyak_cost", cmd_cheongyak_cost))
    app.add_handler(CommandHandler("realestate_cost", cmd_realestate_cost))
    app.add_handler(CommandHandler("screener_cost", cmd_screener_cost))
    app.add_handler(CommandHandler("screener_list", cmd_screener_list))
    app.add_handler(CommandHandler("sites", cmd_sites))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("paper", cmd_paper))
    app.add_handler(CommandHandler("screen", cmd_screen))
    app.add_handler(CommandHandler("screener", cmd_screener))
    # Per-domain shortcut commands — `/screener_bottleneck`, `/screener_
    # healthcare` 등. Telegram client 가 자동 hyperlink → 클릭으로 입력
    # prefill, 엔터 1회로 실행. 사용자 ref 예시 (/find_all / /papers_
    # guide 패턴) 와 동일 UX. 등록은 boot 시 1회, 새 도메인 추가 시
    # 봇 재시작 (auto-deploy 1분) 후 자동 노출.
    _register_dynamic_screener_handlers(app)
    # Catch /compare typed in DM and redirect — actual compare runs only
    # via on_channel_post inside the registered channel.
    app.add_handler(CommandHandler("compare", cmd_compare_hint))
    # /portfolio = 저장된 자산 요약 조회(읽기 전용). 업로드(ingest)는 봇 DM 이
    # 아니라 'Noah의 RAG' 채널 watcher(bot.portfolio_watch)가 담당 — 봇 깨끗하게
    # (사용자 정책 2026-06-04).
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
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

    # Emit "bot starting" early so the watchdog sees it during the
    # potentially long startup regen phase and doesn't false-restart.
    log.info("bot starting — watching channels: %s", CHANNEL_CHAT_IDS or "auto-detect")

    # Refresh the static dashboards once at startup (so an auto-update
    # deploy — which restarts the bot — immediately reflects dashboard.py
    # changes). Run it in a DAEMON THREAD, never inline: a synchronous
    # startup regen that ran long (e.g. cold-cache fetches) used to outlast
    # the watchdog's 180s "bot starting" grace → false-restart → restart
    # loop. Backgrounding it lets app.run_polling() (getUpdates) start
    # immediately, so the watchdog can never trip on startup regen no matter
    # how long it takes. Each regen already swallows its own errors; at
    # startup nothing else regenerates concurrently, so this is race-free.
    def _startup_regen() -> None:
        from bot.dashboard import (regenerate_index, regenerate_portfolio_index,
                                   regenerate_screen_index, regenerate_paper_index)
        for label, fn in (("dashboard", regenerate_index),
                          ("portfolio", regenerate_portfolio_index),
                          ("screen", regenerate_screen_index),
                          ("paper", regenerate_paper_index)):
            try:
                fn()
            except Exception as exc:
                log.warning("startup %s regen failed: %s", label, exc)
        log.info("bot startup regen complete (background)")

    import threading
    threading.Thread(target=_startup_regen, name="startup-regen",
                     daemon=True).start()

    log.info("bot entering polling loop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
