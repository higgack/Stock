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

def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    raise RuntimeError(
        f"Missing required environment variable: {name}. "
        "Set it in .env before starting the bot."
    )


TOKEN = _require_env("TELEGRAM_BOT_TOKEN")


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
        # 자연어 거버넌스 질의('하이닉스 거버넌스') — /gov 와 동일 결과
        # (사용자 2026-07-04 '자연어로 쳐도 같은 결과'). 의도 없으면 기존
        # 대로 무시(채널 일반 대화 침묵 유지).
        from bot.governance import extract_gov_query, build_gov_brief
        _gq = extract_gov_query(text)
        if _gq:
            brief = await asyncio.to_thread(build_gov_brief, _gq)
            try:
                await ctx.bot.send_message(
                    chat_id=post.chat.id, text=brief,
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except Exception:
                from bot.dashboard_console import strip_tg_html
                await ctx.bot.send_message(
                    chat_id=post.chat.id, text=strip_tg_html(brief),
                    disable_web_page_preview=True)
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

    # /sv_cost 채널 분기 제거 (2026-06-12) — SV 폐기(#148) 후 소스 삭제 동반.

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

    # /screener_cost in channel — Bottleneck Screener Pro cost.
    # Reads ~/.tradingagents/screener_usage.jsonl directly.
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
    # /screener_cost). Reads daily_byte_usage.jsonl directly.
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

    # /blog in channel — 감시 블로그 목록 (_BLOGS 자동 생성).
    if first_word == "blog":
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=_blog_list_text(),
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

    # /yfpause in channel — yfinance 일시정지 토글 (사용자 2026-06-14, 채널에서
    # 사용). PTB CommandHandler 가 channel_post 에 안 fire 하므로 여기서 라우팅
    # (없으면 'YFPAUSE' 를 티커로 오인). DM cmd_yfpause 와 _handle_yfpause 공유.
    if first_word == "yfpause":
        parts = body.split()
        text = _handle_yfpause(parts[1] if len(parts) > 1 else "")
        await ctx.bot.send_message(chat_id=post.chat.id, text=text,
                                   parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
        return

    # /naverpause · /health in channel (사용자 2026-06-14) — yfpause 패턴 미러.
    if first_word == "naverpause":
        parts = body.split()
        text = _handle_naverpause(parts[1] if len(parts) > 1 else "")
        await ctx.bot.send_message(chat_id=post.chat.id, text=text,
                                   parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
        return
    if first_word == "health":
        cid = post.chat.id
        await ctx.bot.send_message(chat_id=cid, text="🩺 점검 중…",
                                   parse_mode=ParseMode.HTML)
        text = await asyncio.to_thread(_handle_health)
        await ctx.bot.send_message(chat_id=cid, text=text,
                                   parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
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


_HELP_TEXT = """🧠 <b>주식분석 봇</b>
━━━━━━━━━
<b>【대시보드】</b> 🌍 Main 단일 entry — 그 외(분석아카이브·자산·Screener·레딧·Daily Byte·블로그·밸류체인·🏭PPI·🛒CPI·💧유동성·🚦시장타이밍·🧭Breadth전략(4구간: 역추세/회복/비추세/추세 — 섹터 MA120 상회비율 기준, 월말 확정)·📅경제캘린더(CPI·Core CPI·PPI·고용·AHE·실업률·실업수당(신규/연속)·소매·ECI·GDP·PCE·Core PCE·FOMC·JOLTS·소비자심리·산업생산, 분류: 5일변동성/정책민감/침체조기경보, 방향성: 최근발표대비·1M·3M·6M·1Y)·🏆Market cap·부동산·청약·수출입)는 Main nav, 워치·도메인목록은 Screener nav
 🌍 <b>Main</b> — 글로벌스냅샷·Macro(금리·물가·환율) · 다가오는실적(한·미·일·대·중·홍 6시장) · 리서치액션(한국 기업/산업/전략+미국TP) · 관심종목(한글명·시총·PER·등락·정렬/필터) · 📋DART공시(40+종 구조화 카드·🔥중요/⚠️미파싱 색상+카테고리 필터(정기보고서=사업/반기/분기, 반기=2Q 실적 원문)·CSV) · 업종등락 +🏯ASIA(신고저·급등락·한·미 장전·장후 시간외·NXT·헤더정렬/컬럼필터) · 새 데이터 하단알림(1분 체크·30분 자동반영, 반영은 사용자 선택) · 종목검색·스크롤복원
 ★📝⏰ <b>카드 도구</b> (카드형 대시보드 공통 · 차트보드 PPI·CPI·유동성·시장타이밍·경제캘린더 제외) — 카드마다 ★중요·📝메모·⏰알람 토글(서버 저장→모바일↔PC 동기화). 검색창 옆 ⭐중요/📝메모 필터로 표시한 것만 보기. ⏰알람=매일(시각) 또는 특정일(MM.DD.HH:MM)·KST 텔레그램 발송(메모+카드), ✅확인 시 종료·미확인 시 다음날 재발송
   http://136.115.27.77:8081/06beb08f5f4ad5515007e65f8f60b471/market.html
 📊 <b>분기실적</b> 탭 (종목 상세 · 전 시장) — 최근 5분기 매출·영업이익·순이익 추이(막대 값 라벨) + 단일분기 YoY/QoQ(이익률은 %p) + TTM·Forward PER 스코어카드(무료). 소스: KR=DART 정기보고서 · 그 외=yfinance 분기 손익. 성장동력·리스크 카드(각 최대 6개)는 DART 공시 원문 근거라 <b>한국 종목만</b>, 버튼 클릭 시 1회 생성(분기당 1회 과금, 이후 캐시)
 • 데이터: <code>~/.tradingagents/</code> · 외부참고: /sites

━━━━━━━━━
<b>【명령어】</b> (탭 자동입력 · 대시보드 검색창 '/' 모드에서도 동일)
/help /usage /portfolio /screener_list /sites /blog /valuechain — 비용: /screener_cost·/daily_byte_cost·/cheongyak_cost·/realestate_cost
/티커 — 단일 분석 (예: /NVDA · /005930.KS · 한국은 종목명 /삼성전자)
/compare NVDA AMD — 두 종목 비교
/screen [us|jp|hk|cn|tw] [조건 | 프리셋] [fresh] — 조건부 스크리너 (KR/US/JP/HK/CN/TW, ₩0) · 프리셋 minervini/valueup · fresh=캐시무시 · /screen list
/screener [도메인 | 자유어] — Bottleneck (67도메인+L4세분+자유어) · 전체 /screener_list
/watch NVDA rsi&lt;30 price&gt;950 — 조건 알림 (rsi/price/sma/52w/earnings·KR수급) · /watchlist · /unwatch
/dart_alert on|off — 관심종목(KR) 새 DART 공시 알림
/gov 종목 — 거버넌스 브리핑 (KR·DART: 대주주·임원지분·주총/활동주의 공시 + AI요약) · 자연어 OK("하이닉스 거버넌스")
/paper — 페이퍼 모의매매(돈0) · 전체 /paper help · /backtest_review — 청산이력 5축평가(Deploy/Refine/Abandon)
/health · /yfpause·/naverpause on|off — 소스 헬스/정지토글

━━━━━━━━━
<b>【채널 알림】</b>
🚀 배포 · ⚠️ hang · ❌ 실패 · 📋 관심종목 DART공시(/dart_alert) · ⏰ 카드 알람(지정 시각·KST·✅확인 시 종료) · 📰 Daily Byte(한 평일19:00·미 08:00·주간 한·미 일22:00) · 🎟️ 청약(평일10·14시) · 🏠 부동산(금09:00·1일) · 📝 블로그(30분) · 📣 DAJU 실적예정(실시간) · 📨 레딧(1분)

━━━━━━━━━
<b>【분석 &amp; 비용】</b> ~3분 · ₩100~150/회 (/compare ₩200~300)
 • <code>/티커</code> → 사전 fetch(매크로9·리스크·섹터ETF·컨센서스·공매도/내부자/기관·실적일) → 분석가 4명(📈시장 💬감정 📰뉴스 💰펀더멘털) → Bull/Bear 토론 → Trader→Risk 3인→PM(5거래일 평가 윈도)
 • 요약: 🎯판정 · 📒지난추천 5거래일 결과 · ⚠️실적±10일·뉴스스킵 · 4명 stance+mismatch · [📋 전체 리포트]
 • 같은 종목 재분석 = 캐시 즉시(무료) · /usage 비용차트
 • 데이터: yfinance·네이버·Kabutan·DART/EDINET/MOPS/EDGAR+XBRL·ECOS/FRED·US옵션·KRX수급·KIS

━━━━━━━━━
<b>【진행 중 / 예정】</b>
 • Screener 67도메인+L4세분+자유어+24h캐시 · 분기GICS · 실거래 E1 KIS모의+자동신호+RiskGate · 예정: IBKR·실전(E2)
"""


_SITES_TEXT = """🔗 <b>참고 사이트</b>

 • <a href="https://stockeasy.intellio.kr/">Stockeasy</a>
 • <a href="https://stockhub.kr/">Stockhub</a>
 • <a href="https://jusikbot.com/">Jusikbot — Real-time Stock Dashboard</a>
 • <a href="https://tebi.raoni.xyz/">트비 주식뉴스 어그리게이터 리포트</a>
 • <a href="https://www.tradeodds.io/">tradeodds</a>
 • <a href="https://aibottlenecks.app/">AI Bottlenecks</a>
 • <a href="https://analytics.blancwm.com/">Analytics Portal</a>
 • <a href="https://reports.blueming.net/dashboard">report summary</a>
 • <a href="https://junresearch.com/jensenHuangKRTracker">젠슨황의 발자취</a>
 • <a href="https://www.ooooo.law/">ooooo.law</a>
 • <a href="https://karpathy.ai/jobs/">US Job market</a>
 • <a href="https://haebom.dev/daily_arxiv">Arxiv daily</a>
 • <a href="https://whale-insight.com/">국민연금 현황</a>
 • <a href="https://www.tessie.com/">Tesla tracking</a>
 • <a href="https://www.obf.md/app">키워드트래커</a>
 • <a href="https://stocks.allreview.kr/067010">실적분석</a>
 • <a href="https://www.ant.wiki/spacex">SpaceX</a>
 • <a href="https://kospi-king.codojun.com/">Global peer comparison</a>
 • <a href="https://demoday.co.kr/">Demoday</a>
 • <a href="https://samnix.vercel.app/">삼전하닉도미넌스</a>
 • <a href="https://awareinvest.com/">어웨어</a>
 • <a href="https://news.hada.io/">GeekNews</a>
 • <a href="https://henryquant.shinyapps.io/kingspi/">만스피를 향하여!</a>
 • <a href="https://thevc.kr/">thevc</a>
 • <a href="https://www.jipfeed.com/">집피드</a>
 • <a href="https://livewiki.com/ko">livewiki</a>
 • <a href="https://easyconomics.com/">funesay board</a>
 • <a href="https://soonsal.com/">Soonsal</a>
 • <a href="https://activeholders.com/">activeholders</a>
 • <a href="https://www.kisti.re.kr/homekor">한국과학기술정보연구원</a>
 • <a href="https://badonion.co.kr/home">나쁜양파</a>
 • <a href="https://www.autoanalyst.ai.kr/">호돌이오토애널리스트</a>
 • <a href="http://www.serenityrsh.com/#link">Serenity</a>
 ? <a href="https://humanindicator.kr/ranking">human indicator</a>
 - <a href="https://newsbot-3uj.pages.dev/coverage/coverage">Daol Park Jonghyun Dashboard</a>"""


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
        [
            InlineKeyboardButton(
                "📝 감시 블로그",
                url=f"https://t.me/{bot_username}?start=blog",
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
        if arg == "blog":   # help 키보드 '📝 감시 블로그' 버튼 deep-link
            await update.message.reply_text(
                _blog_list_text(), parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
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
    # 실적분석(분기 인포그래픽, subsystem='quarterly_infographic') break out —
    # 종목분석과 별개 surface 로 보고(사용자 2026-08-16). 대시보드 비용카드의
    # '실적분석' 버킷과 동시갱신(CLAUDE.md 비용합산 규칙).
    month_cost_quarterly = sum(
        r.get("cost_usd", 0) for r in calls
        if r.get("subsystem") == "quarterly_infographic")
    today_cost_analysis = (today_cost - today_cost_screener - today_cost_daily_byte
                           - sum(r.get("cost_usd", 0) for r in today_calls
                                 if r.get("subsystem") in ("cheongyak", "realestate",
                                                           "blog", "quarterly_infographic")))
    month_cost_analysis = (month_cost - month_cost_screener - month_cost_daily_byte
                           - month_cost_realestate - month_cost_blog
                           - month_cost_quarterly)

    # Standard View 비용 reader 제거 (2026-06-12) — SV 폐기(#148, 타이머
    # 전부 disable)로 신규 비용 0. 6월 초 trailing 분은 총합에서 제외
    # (소스 트리 삭제와 함께 reader 도 정리 — 단순성 우선).

    # 한국 수출입(trade) cost — 별도 repo usage.jsonl (사용자 정책 2026-06-02).
    # $TRADE_DATA_DIR/usage.jsonl, 미설정 시 ~/.trade/usage.jsonl. cost_usd /
    # cost_krw + date / ts 양쪽 tolerant (dashboard._compute_stats 와 동일 로직).
    # kg 관계후보(블로그·DART 자동발굴)는 같은 trade usage.jsonl 이지만 kind 로
    # 수출입과 분리 집계(사용자 2026-06-24). kind 미기록 옛 레코드 → 수출입.
    tr_today_usd = tr_month_usd = 0.0
    kg_today_usd = kg_month_usd = 0.0
    tr_total_usd = 0.0   # trade 파일 전체 누적(수출입+kg, 날짜무관)
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
                    # 누적은 날짜 파싱 전 합산(dashboard._compute_stats 동일 불변식)
                    tr_total_usd += _usd
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
                    _is_kg = (_r.get("kind") or "").startswith("kg")
                    if _rd.startswith(month_str_kst):
                        if _is_kg:
                            kg_month_usd += _usd
                            if _rd == today_str_kst:
                                kg_today_usd += _usd
                        else:
                            tr_month_usd += _usd
                            if _rd == today_str_kst:
                                tr_today_usd += _usd
    except Exception as _exc:
        log.warning("usage: trade cost read failed: %s", _exc)

    today_total_usd = today_cost + tr_today_usd + kg_today_usd
    month_total_usd = month_cost + tr_month_usd + kg_month_usd
    # 누적(전체) = NOAH 롤업(30일 로테이션 유출분 적산) + NOAH 현재파일 전체
    # + trade 파일 전체 — 대시보드 메인 카드 '누적' 과 동일 정의(동시갱신 규칙).
    all_total_usd = (usage_tracker.rollup_cost_usd() + month_cost
                     + tr_total_usd)

    lines = [
        "📊 <b>봇 사용 현황</b> (KST)",
        "",
        "🔬 <b>분석 실행</b>",
        f"  • 오늘: {len(today_runs)}건  (새 {today_new}건 + 캐시 {today_cache}건)",
        f"  • 7일:  {len(week_runs)}건",
        f"  • 30일: {len(month_runs)}건",
        "",
        f"💰 <b>총 비용 (전체 surface 합산)</b> (₩{fx}/$)",
        f"  • 오늘: <b>{krw(today_total_usd)}</b>  (${today_total_usd:.2f})",
        f"  • 30일: <b>{krw(month_total_usd)}</b>  (${month_total_usd:.2f})",
        f"  • 누적: <b>{krw(all_total_usd)}</b>  (${all_total_usd:.2f})",
        "",
        "📐 <b>월간 subsystem 분포</b>",
        f"  • 분석:        {krw(month_cost_analysis)}",
        f"  • 실적분석:          {krw(month_cost_quarterly)}",
        f"  • Bottleneck Screener: {krw(month_cost_screener)}  ← /screener_cost",
        f"  • Daily Byte:        {krw(month_cost_daily_byte)}  ← /daily_byte_cost",
        f"  • 부동산:            {krw(month_cost_realestate)}  ← /realestate_cost",
        f"  • 블로그:            {krw(month_cost_blog)}",
        f"  • 관계후보(kg):      {krw(kg_month_usd)}",
        f"  • 한국 수출입:       {krw(tr_month_usd)}",
        "",
        f"💰 <b>분석 단독 (참고)</b>",
        f"  • 7일:  {krw(week_cost)}  (${week_cost:.2f})",
        "",
        "🤖 <b>모델별 (오늘 · 분석)</b>",
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
    failure."""
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
    n_l4 = len(by_layer.get("L4_SUBINDUSTRY", []))
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
        + (f" + L4 sub {n_l4}" if n_l4 else "")
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

    # L4 sub-industry — 요약만 (2026-06-14). L4 는 272+ 라 텔레그램 전수 나열은
    # 6+ 메시지 스팸 → 카운트 + 접근법만, 전체 목록은 대시보드(🎯 L4 collapsible).
    # 슬러그 직접(/screener_<L3>_<세부>) 또는 L3 별칭(/screener 반도체→L3) 접근.
    l4_items = by_layer.get("L4_SUBINDUSTRY", [])
    if l4_items:
        chunks.append(
            f"━━━ <b>🎯 L4 Sub-industry</b> ({len(l4_items)}개) ━━━\n\n"
            "각 L3 아래 GICS sub-industry 깊이 (예: Semiconductors → Memory · "
            "Foundry · Equipment · Logic AI ...).\n\n"
            "• 전체 목록 + 검색: 대시보드 <code>screener_domains.html</code> 🎯 L4 섹션\n"
            "• 직접 실행: <code>/screener_&lt;L3슬러그&gt;_&lt;세부&gt;</code> "
            "(예 <code>/screener_semiconductors_memory</code>)\n"
            "• 또는 L4 전용 별칭 <code>/screener dram</code> · <code>sic</code> · "
            "<code>wfe</code> · <code>npu</code> 등\n"
            "• <code>/screener &lt;L3별칭&gt;</code> (예 반도체)는 L3 전체 유지"
        )

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
    pattern). Reads ~/.tradingagents/screener_usage.jsonl directly —
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
    /screener_cost). Reads ~/.tradingagents/daily_byte_usage.jsonl."""
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


# /sv_cost 제거 (2026-06-12) — Standard View 폐기(#148) 후 소스 삭제와
# 함께 명령도 정리. 생성 타이머가 죽어 '오늘 비용' 이 영구 0 이라 오해만 유발.


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


def _blog_list_text() -> str:
    """감시 블로그 목록 — bot.blog_watch._BLOGS 에서 자동 생성 (블로그 추가
    시 이 명령·메뉴가 자동 반영, 수동 동기 불요 — /sites 패턴 mirror).
    사용자 2026-06-15 '블로그도 sites 처럼'."""
    import html as _h
    try:
        from bot.blog_watch import _BLOGS
    except Exception:
        _BLOGS = ()
    lines = ["📝 <b>감시 블로그</b>", "",
             "새 글을 30분마다 자동 수집 → 채널 알림 + 대시보드(blog.html, 전문·검색).",
             ""]
    for b in _BLOGS:
        bid = b.get("id", "")
        title = _h.escape(b.get("title") or bid)
        cat = b.get("categories")
        suffix = "" if cat is None else f" · {_h.escape('/'.join(cat))} 카테고리만"
        lines.append(f' • <a href="https://m.blog.naver.com/{bid}">{title}</a>{suffix}')
    lines += ["", "📊 대시보드: 주식분석 아카이브 → 📝 블로그"]
    return "\n".join(lines)


async def cmd_blog(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/blog — 감시 중인 네이버 블로그 목록(+대시보드 안내). 목록은
    blog_watch._BLOGS 에서 자동 생성 → 블로그 추가 시 자동 반영. 채널은
    on_channel_post 에서 처리(PTB CommandHandler 가 channel_post 미발화)."""
    if update.message is None:
        return
    await update.message.reply_text(
        _blog_list_text(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_valuechain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/valuechain [회사] — 밸류체인 스크리너(③). 인자=회사면 그 회사의 공급사·고객·
    수출품목·동종사, 없으면 연결 상위 + 다고객 납품사 + 대시보드 안내. LLM 0(그래프
    조회만, ₩0). bot.valuechain 공용 모듈(② NOAH 주입과 동일 소스)."""
    if update.message is None:
        return
    arg = " ".join(context.args).strip() if getattr(context, "args", None) else ""
    try:
        from bot import valuechain as _vc
        if arg:
            text = _vc.format_for_telegram(arg)
        else:
            edges = _vc.load_edges()
            top = _vc.top_connected(edges, 15)
            sup = _vc.top_suppliers(edges, 12)
            lines = ["🔗 <b>밸류체인 스크리너</b>",
                     "사용: <code>/valuechain 회사·품목·업종</code> — 예) SK하이닉스(회사)·메모리반도체(품목)·반도체(업종)",
                     ""]
            if top:
                lines.append("📌 <b>연결 상위</b>: "
                             + ", ".join(f"{_html.escape(n)}({d})" for n, d in top))
            if sup:
                lines.append("🚚 <b>다고객 납품사</b>(여러 회사에 납품): "
                             + ", ".join(f"{_html.escape(n)}({d})" for n, d in sup))
            lines += ["", "<i>대시보드: 주식분석 아카이브 → 🔗 밸류체인</i>"]
            text = "\n".join(lines)
    except Exception as exc:
        log.warning("cmd_valuechain failed: %s", exc)
        text = "밸류체인 조회 실패 — 잠시 후 다시 시도하세요."
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


_WATCH_HELP = (
    "🔔 <b>워치리스트 — 조건 충족 시 자동 알림</b> (LLM 0 · 비용 ₩0)\n"
    "\n"
    "<b>등록</b>: <code>/watch TICKER 조건1 조건2 …</code>\n"
    "한 종목에 조건 여러 개(최대 8개) 가능. 같은 종목 다시 /watch 하면 조건 병합.\n"
    "\n"
    "<b>📋 조건 종류</b>\n"
    "• <code>rsi&lt;30</code> / <code>rsi&gt;70</code> — RSI(14)가 값 아래/위로\n"
    "• <code>price&gt;950</code> / <code>price&lt;800</code> — 현재가가 값 위/아래로\n"
    "• <code>&gt;sma55</code> / <code>&lt;sma200</code> — 현재가가 이동평균 위/아래로\n"
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
    "대시보드(활성 워치 + 알림 이력): 주식분석 아카이브 → 🔔 워치리스트"
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
    "<code>/paper auto on|off</code> — 분석 판정 자동매매(매수 자본5%·5거래일 청산)\n"
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

    # 'fresh'(강제 재실행) 토큰 — 캐시 무시. 영구캐시로 옛 결과 고착되던 것 우회
    # (사용자 2026-06-23). 어디 위치하든 제거 후 force_fresh.
    force_fresh = False
    _ftoks = [t for t in raw.split() if t.lower() not in ("fresh", "강제", "새로", "!")]
    if len(_ftoks) != len(raw.split()):
        force_fresh = True
        raw = " ".join(_ftoks).strip()

    from bot.stock_screener import (
        PRESETS, parse_conditions, run_screen,
        format_result_message, save_screen_archive,
    )

    # 시장 접두 — us/jp/hk/cn/tw (cn→내부코드 CN_A). 해외는 yfinance 개별 조회.
    _PFX = {"us": "US", "jp": "JP", "hk": "HK", "cn": "CN_A", "tw": "TW"}
    _LBL = {"US": "🇺🇸 S&amp;P 500", "JP": "🇯🇵 닛케이225격", "HK": "🇭🇰 홍콩",
            "CN_A": "🇨🇳 본토", "TW": "🇹🇼 대만"}
    market = "KR"
    tokens = raw.split(None, 1)
    if tokens and tokens[0].lower() in _PFX:
        market = _PFX[tokens[0].lower()]
        raw = tokens[1] if len(tokens) > 1 else ""
        if not raw:
            await send(f"⚠️ 조건을 입력하세요. 예: <code>/screen {tokens[0].lower()} minervini</code>")
            return

    preset = PRESETS.get(raw.lower())
    if preset:
        cond_text = preset["conditions"]
        _esc = cond_text.replace("<", "&lt;").replace(">", "&gt;")
        label = _LBL.get(market, "🇰🇷 KR")
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
        label = _LBL.get(market, "🇰🇷 KR")
        wait_note = " (yfinance 개별 조회, ~1-2분 소요)" if market != "KR" else ""
        await send(
            f"📊 <b>조건부 스크리너</b> ({label})\n"
            f"조건: <code>{cond_display}</code>\n⏱ 실행 중...{wait_note}"
        )

    import asyncio
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_screen(conditions, market=market, force_fresh=force_fresh)
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
                await send(f"🤖 자동매매 <b>ON</b> — 분석 판정대로 페이퍼 주문 "
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
            # 트레이드 씨시스(2026-07-26) — MFE/MAE 30분 샘플 + 청산 감지·
            # 포스트모템 알림(청산 경로 무관, sync_closed 가 상태비교로 감지).
            try:
                from bot import trade_thesis
                trade_thesis.update_mfe_mae()
                closed = trade_thesis.sync_closed()
                if closed:
                    from bot.dashboard import regenerate_paper_index
                    regenerate_paper_index()
                    if application is not None and CHANNEL_CHAT_IDS:
                        for t in closed:
                            pm = trade_thesis.postmortem_text(t)
                            if not pm:
                                continue
                            for _cid in CHANNEL_CHAT_IDS:
                                try:
                                    await application.bot.send_message(
                                        chat_id=_cid,
                                        text=f"📔 씨시스 청산 {t['ticker']}: {pm}")
                                except Exception:
                                    pass
            except Exception:
                log.debug("trade_thesis sync skipped")
        except Exception:
            log.exception("paper pending fill failed")


async def _periodic_market_refresh() -> None:
    """market.html 30초 주기 재생성 (사용자 2026-06-15 '실시간' — 라이브 틱이
    받아갈 정적 파일을 30초 신선하게).

    주기 단축이 안전한 이유: 페이지는 각 소스의 **디스크 캐시에서 렌더**
    하고, 외부 호출 빈도는 소스별 TTL 이 상한 — Naver 업종/테마 30초 · 글로벌
    스냅샷 1분 · Macro 1분 · Finviz 5분(데이터센터 IP 안티봇 한계) · 실적/리서치
    1~12h. 루프는 sequential(await)이라 겹침 불가, to_thread 라 폴링 비차단
    (watchdog 영향 0). 효과: 위젯 최대 지연 = 소스 TTL + 30초.
    ⚠️ **라이브 틱(_MARKET_LIVE_JS)**: market.html 이 #live-sections(스냅샷·
    Macro·업종등락)를 30초마다 스스로 다시 받아 innerHTML 교체 → 사용자 새로고침
    없이 자동 갱신. 정적 파일을 30초 재생성하므로 이 틱이 30초 신선도를 본다.
    ⚠️ 홈 스냅샷의 지수·FX·VIX·원자재·코인·Macro 는 **전부 네이버**(nvi:/nvk:/
    nvx: + _MACRO_NAVER). 남은 yahoo = **33개 KR 주요종목 시세 카드**(삼성전자
    등 .KS/.KQ, _all_yf_tickers)뿐 → 그래서 스냅샷 _CACHE_TTL_SEC 은 1분 유지
    (더 줄이면 그 KR 카드 fast_info 가 차단 재유발, _LIVE_TTL=60 도 동일 경계).
    이 33종목을 네이버 domestic 시세로 옮기면 홈 야후 완전 제거 가능(진행 예정)."""
    while True:
        await asyncio.sleep(30)   # 30초 (사용자 2026-06-15 '실시간')
        try:
            from bot.dashboard import regenerate_market_index
            await asyncio.to_thread(regenerate_market_index)
        except Exception:
            log.exception("periodic market.html refresh failed")


def _prewarm_highlow() -> None:
    """전종목 신고저/급등락/상한가 캐시를 순차 재산출 — 첫 방문자가 수 분 기다리지
    않게 미리 데워둠(사용자 2026-06-13 '아침엔 항상 신선'). 순차 실행으로 yfinance
    버스트 회피. 각 시장 graceful(실패해도 다음 진행). 무거운 전종목 스캔이라
    백그라운드 thread 에서만 호출(_periodic_highlow_prewarm).

    ⚠️ yfinance 정지(YF_PAUSE) 중에도 **호출은 유지** — 내부 yfinance 컴퓨트
    (_compute_highlow_from 등)는 각자 정지 게이트로 캐시 반환·스캔 skip 하지만,
    네이버 맵 빌드(world_industry_map US·world_quote_map·world_stock_map)·Finviz
    US 52주·네이버 무버는 yfinance 무관이라 계속 데워야 업종/시총이 안 빈다(사용자
    2026-06-14 'US/HK 업종 안 나옴' — 옛 전체 skip 이 네이버 맵까지 막던 버그)."""
    seq = []
    try:
        from bot.intl_highlow import _compute as _ih
        seq += [(f"highlow {m}", (lambda m=m: _ih(m)))
                for m in ("KR", "JP", "HK", "CN_A")]   # CN 재도입(사용자 2026-06-17, peer-only)
    except Exception:
        pass
    try:
        # JP/CN/HK 무버 — 네이버 worldstock(1콜씩·가벼움, 사용자 2026-06-13
        # '중국·홍콩·일본은 미국따라'). 옛 jp_stop(yfinance 전종목 스캔)은 jpmovers
        # 로 대체돼 prewarm 제외(무거운 스캔 절약).
        from bot.intl_movers import _compute as _im
        seq += [(f"movers {m}", (lambda m=m: _im(m))) for m in ("HK", "JP", "CN_A")]
    except Exception:
        pass
    try:
        from bot.tw_highlow import _compute_tw_highlow as _tw
        seq.append(("tw_highlow", _tw))
    except Exception:
        pass
    try:
        # US 52주 한글명 맵(네이버 업종 koreanCodeName) 선빌드 — 첫 방문자가
        # 영문→한글 전환 안 보게 (사용자 2026-06-14). 7d 캐시·CN/HK/JP 업종맵 동류.
        from bot.naver_ranking_client import _build_quote_map as _bqm
        from bot.naver_ranking_client import world_industry_map as _wim
        seq.append(("us_names", lambda: _wim("US")))
        seq.append(("us_quote", lambda: _bqm("US")))   # US 52주 네이버 거래량/거래대금
    except Exception:
        pass
    try:
        # KR 업종 그룹 멤버맵(코드→업종 한글) 선빌드 — KR 신고가·급등락 업종 컬럼
        # 첫 방문자가 '—' 안 보게 (사용자 2026-06-14). 7d 캐시·네이버 업종 그룹 스캔.
        from bot.naver_sector_client import _build_kr_industry_map as _bkim
        seq.append(("kr_industry", _bkim))
    except Exception:
        pass
    # yfinance 무거운 52주 스캔(JP/HK/TW highlow, 1y 벌크)을 서로 떨어뜨려
    # 레이트리밋 회복 간격 확보 (사용자 2026-06-16 '일본·중국·홍콩 10~15분 간격
    # 으로 서로 콜 시작 시간 조정·부하 감소'). ⚠️ JP/CN/HK 무버·KR 신고저는 이미
    # 네이버(1콜·가벼움)라 무지연 — yfinance 무거운 건 highlow 3종뿐(CN highlow 는
    # 2026-06-14 제거). 스캔 끝→다음 스캔 시작 사이 HIGHLOW_STAGGER_SEC(기본 600초
    # =10분) 간격. 백그라운드 thread sleep 이라 이벤트루프·watchdog(getUpdates·busy
    # 마커) 무관 — 폴링 비차단. 0 으로 끄면 즉시 연속(옛 동작).
    _stagger = int(os.getenv("HIGHLOW_STAGGER_SEC", "600"))
    _heavy = {"highlow JP", "highlow HK", "highlow CN_A",   # yfinance 1y 벌크 스캔
              "tw_highlow"}
    _last_heavy = 0.0
    for label, fn in seq:
        if label in _heavy and _last_heavy and _stagger > 0:
            _gap = _stagger - (time.time() - _last_heavy)
            if _gap > 0:
                log.info("highlow prewarm: %s 전 %.0f초 stagger(yfinance 분산)",
                         label, _gap)
                time.sleep(_gap)
        try:
            fn()
            log.info("highlow prewarm: %s done", label)
        except Exception as exc:
            log.warning("highlow prewarm %s: %s", label, exc)
        if label in _heavy:
            _last_heavy = time.time()
    # US (Finviz·가벼움) — SWR fetch 로 warm
    try:
        from bot.finviz_client import fetch_high_low, fetch_us_movers
        fetch_high_low()
        fetch_us_movers()
    except Exception as exc:
        log.warning("highlow prewarm US: %s", exc)
    # 장전/장후 급등락 — 연장거래 창(미국 장전·장후)일 때만 warm (장 밖 무의미한
    # 전미국 스캔 회피). 07:30 KST=US 장후, 정규장 중 prewarm 은 직전 스냅샷 유지.
    try:
        from datetime import datetime as _dt, timezone as _tz
        from bot.prepost_client import (_in_extended_window,
                                        fetch_us_prepost_movers)
        if _in_extended_window(_dt.now(_tz.utc)):
            fetch_us_prepost_movers()
    except Exception as exc:
        log.warning("prepost prewarm US: %s", exc)


async def _periodic_highlow_prewarm() -> None:
    """매일 07:30·16:30 KST 전종목 신고저/급등락/상한가 캐시 pre-warm — 첫 방문자
    대기 0(아침 신선). 07:30=US 마감 후+Asia 전일종가, 16:30=Asia 마감 후. 시장-
    인지 신선도(정규장 2h·무버 30분 / 장 밖 마지막 마감 이후 재스캔 0)와 함께
    아침 첫 방문 만료를 미리 데움. to_thread(폴링 비차단)·graceful."""
    kst = timezone(timedelta(hours=9))
    while True:
        now = datetime.now(kst)
        cands = [now.replace(hour=h, minute=30, second=0, microsecond=0)
                 for h in (7, 16)]
        future = [t for t in cands if t > now]
        target = min(future) if future else (cands[0] + timedelta(days=1))
        await asyncio.sleep(max(60.0, (target - now).total_seconds()))
        # ⚠️ 데몬 thread 로 fire-and-forget (asyncio.to_thread 금지) — to_thread 의
        # 기본 executor 는 non-daemon 이라 봇 종료 시 _python_exit atexit 가 join
        # 하는데, _prewarm_highlow 의 stagger time.sleep(600)(10분)이 그 join 을
        # 최대 10분 블로킹 → 봇이 'deactivating' 에 멈춰 watchdog 재시작도 안 먹는
        # 다운 (2026-06-16 16:37 배포가 16:30 prewarm stagger sleep 중 떨어져 발생,
        # 실수 #6). 데몬 thread 는 종료 시 join 없이 killed → 블로킹 0. prewarm 은
        # 9h 간격이라 fire-and-forget 겹침 없음(소요 ~40분).
        try:
            import threading as _pwt
            _pwt.Thread(target=_prewarm_highlow, daemon=True,
                        name="highlow-prewarm").start()
        except Exception:
            log.exception("highlow prewarm thread failed")


# 시간대별 슬롯 스캔 (사용자 2026-06-16 '우리가 산출하는 것도 장중에 1시간단위로
# 돌아야돼 · 스캔부하는 시간대별 배치로 분산 · 장종료엔 종가기준으로 멈춰 부하↓').
# **비-네이버 컴퓨티드 보드**만 대상 — 네이버 직접(KR 52w/movers · JP/CN/HK movers ·
# **US movers**=_compute_us_movers 네이버 1차)은 가벼워(1콜씩) **_periodic_light_
# board_warm**(180초 워머)이 방문 없이도 장중 신선 유지하므로 여기(시간당 슬롯)서 제외.
# 무거운 Asia 52주(JP/HK/CN/TW yfinance 1년 벌크)를 시(時) 안에서 15분씩 떨어뜨려
# (:00 / :15 / :30 / :45) 동시 부하 회피 — 4개 Asia 52주 겹침창(UTC 01:30-05:30 =
# KST 10:30-14:30, JP·TW·HK·CN 모두 개장)에서도 서로 안 겹침(JP/HK/CN=peer ~50-100
# 종목 ~수분 · TW=full 上市+上櫃 ~10-14분 < 15분 간격). CN 은 2026-06-17 재도입 시
# 4번째 Asia 52주라 20분→15분 간격으로 재분산(사용자 '부하고려해서 다시 전체적으로').
# US 52주(Finviz·가벼움)는 야간장(UTC 13:00-21:30)이라 Asia 와 충돌 0 → :00 동거.
# TW 무버(TWSE OpenAPI·가벼움)는 TW 52주와 같은 :45(소스 다름 — yfinance vs OpenAPI).
#   slot 분(分) → (보드 토큰…). 토큰: US/JP/HK/CN_A/TW=52주, TWMV=TW 무버.
_HL_TIMER_ACTIVE: bool | None = None


def _highlow_timer_active() -> bool:
    """독립 highlow-scan.timer 가 설치·활성인가 — 활성이면 in-process 폴백 슬롯
    스캔을 돌리지 않는다(별 프로세스 타이머와 이중 스캔·yfinance throttle 방지).
    systemctl 부재/실패 시 False(폴백 가동). 프로세스 수명 1회 캐시."""
    global _HL_TIMER_ACTIVE
    if _HL_TIMER_ACTIVE is not None:
        return _HL_TIMER_ACTIVE
    try:
        import subprocess
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", "highlow-scan.timer"],
            timeout=5)
        _HL_TIMER_ACTIVE = (r.returncode == 0)
    except Exception:
        _HL_TIMER_ACTIVE = False
    return _HL_TIMER_ACTIVE


async def _periodic_highlow_scan() -> None:
    """52주 신고저 슬롯 스캐너 — **독립 highlow-scan.timer 가 주(主)**(봇 배포·
    재시작과 무관하게 :00/:15/:30/:45 별 프로세스로 스캔; 사용자 2026-06-19 '배포로
    중간에 변경해도 아시아·미국 신고가는 그대로 돌게'). 이 in-process 경로는 타이머
    미설치/비활성 VM 의 **폴백** — 타이머가 활성이면 매 슬롯에서 skip(이중 스캔 방지).
    슬롯 로직은 bot.highlow_scan 단일 소스. 벽시계 정렬 sleep·daemon fire(실수 #6)."""
    await asyncio.sleep(120)
    from datetime import datetime, timedelta, timezone

    from bot.highlow_scan import _HL_SCAN_SLOTS, run_slot
    slot_mins = sorted(_HL_SCAN_SLOTS.keys())
    while True:
        now = datetime.now(timezone.utc)
        nxt = None
        for sm in slot_mins:                       # 다음 :00/:15/:30/:45 경계
            cand = now.replace(minute=sm, second=0, microsecond=0)
            if cand > now:
                nxt = cand
                break
        if nxt is None:                            # 이번 시(時) 슬롯 다 지남 → 다음 시 첫 슬롯
            nxt = (now + timedelta(hours=1)).replace(
                minute=slot_mins[0], second=0, microsecond=0)
        await asyncio.sleep(max(5.0, (nxt - now).total_seconds()))
        if _highlow_timer_active():
            continue                               # 독립 타이머가 담당 → in-process skip
        slot = nxt.minute
        try:
            import threading as _slt
            _slt.Thread(target=run_slot, args=(slot,), daemon=True,
                        name=f"highlow-slot-{slot}").start()
        except Exception:
            log.exception("highlow slot thread failed")


# ── 경량 동적 보드 서버사이드 워머 (사용자 2026-06-17 '모든 대시보드가 내가 안
# 들어가도 자기 리프레쉬 주기로 갱신') ──────────────────────────────────────
# 무거운 yfinance 52주(JP/HK/CN/TW)·US 52주는 슬롯스캐너(_periodic_highlow_scan,
# 시간당)가 담당. 여기서는 **방문해야만 갱신되던 경량 동적 보드**를 데운다:
# KR 52주(네이버) · 무버(KR/JP/HK/CN/US) · 장전후(US/KR) · KR NXT · 테마/업종.
# 메커니즘 = '방문 시뮬레이션' — 페이지의 render 함수를 그대로 호출하면 그 페이지가
# 읽는 **파일 캐시**(kr_movers_v1.json·highlow_kr_v2.json·intl movers/us movers
# 캐시 등)가 채워진다. dashboard_server 는 별도 프로세스지만 같은 파일 캐시를
# 읽으므로 cross-process 로 신선해진다(렌더 HTML 자체는 폐기 — 캐시 채우기 목적).
# 각 보드 내부 fetch 는 자기 SWR TTL(무버 30초·KR 52주 30초·테마/업종 더 김)로
# 게이트되므로, 워머가 자주 깨어나도 TTL 안이면 외부 호출 0(과fetch 없음).
_LIGHT_WARM_RUNNING = False


def _warm_render(module: str, fn: str, *args) -> None:
    """페이지 render 호출(반환 HTML 폐기) — 그 페이지의 파일 캐시를 채운다."""
    import importlib
    getattr(importlib.import_module(module), fn)(*args)


def _warm_light_boards() -> None:
    """장중인 경량 동적 보드의 render 를 호출해 파일 캐시를 데움(방문 없이 신선).
    시장시간·pause(naverpause/yfpause) 게이트 → 장 밖·정지 시 skip(부하 0·freeze).
    daemon thread 에서만 호출(render 가 네이버/Finviz 동기 fetch 포함 → 이벤트루프·
    watchdog 폴링 비차단). 직전 사이클 미완 시 skip(thread 쌓임 방지)."""
    global _LIGHT_WARM_RUNNING
    if _LIGHT_WARM_RUNNING:
        return
    _LIGHT_WARM_RUNNING = True
    try:
        from datetime import datetime, timezone
        try:
            from bot.finviz_client import _SESSIONS_UTC, naver_paused, yf_paused
        except Exception:
            return
        now = datetime.now(timezone.utc)

        def _open(m: str) -> bool:
            s = _SESSIONS_UTC.get(m)
            if not s:
                return False
            oh, om, ch, cm = s
            return now.weekday() < 5 and (oh, om) <= (now.hour, now.minute) < (ch, cm)

        try:
            nav_off = naver_paused()
        except Exception:
            nav_off = False
        try:
            yf_off = yf_paused()
        except Exception:
            yf_off = False

        from datetime import timedelta as _td
        _kst = now + _td(hours=9)                          # UTC→KST (weekday·hour 모두 KST)
        # ⚠️ weekday 도 KST 로 — KST 08:xx 는 UTC 전일 23:xx 라 now.weekday()(UTC)
        # 를 쓰면 월요일 장전(UTC 일요일)이 weekday<5 에 걸려 영영 워밍 안 됨
        # (사용자 'NXT 장전집계 또 안 됨' 클래스). _kst.weekday() 로 교정.
        kr_ext = _kst.weekday() < 5 and 8 <= _kst.hour < 20   # KST 평일 08-20 (NXT 장전후)

        jobs = []   # (label, callable) — 시장시간/연장창 게이트 통과분만
        if not nav_off:                                  # 네이버 경량 보드
            if _open("KR"):
                jobs += [
                    ("kr52", lambda: _warm_render(
                        "bot.intl_pages", "render_intl_highlow52_page", "KR")),
                    ("krmovers", lambda: _warm_render(
                        "bot.naver_pages", "render_highlow_page")),
                    ("theme", lambda: _warm_render(
                        "bot.naver_pages", "render_theme_page")),
                ]
            for m in ("JP", "HK", "CN_A"):
                if _open(m):
                    jobs.append((f"{m}movers", lambda m=m: _warm_render(
                        "bot.intl_pages", "render_intl_movers_page", m)))
            if kr_ext:                                   # KR 장전후(NXT) — KST 08-20
                jobs += [
                    ("krprepost", lambda: _warm_render(
                        "bot.intl_pages", "render_kr_prepost_page")),
                    ("nxt", lambda: _warm_render("bot.nxt_pages", "render_nxt_page")),
                ]
        if _open("US"):                                  # US 무버·업종(Finviz)
            jobs += [
                ("usmovers", lambda: _warm_render(
                    "bot.us_pages", "render_us_movers_page")),
                ("usindustry", lambda: _warm_render(
                    "bot.us_pages", "render_us_industry_page")),
            ]
        try:                                             # US 장전후(연장창만)
            from bot.prepost_client import _in_extended_window
            if _in_extended_window(now) and not yf_off:
                jobs.append(("usprepost", lambda: _warm_render(
                    "bot.us_pages", "render_us_prepost_page")))
        except Exception:
            pass

        for label, fn in jobs:
            try:
                fn()
            except Exception as exc:
                log.debug("light board warm %s: %s", label, exc)
    finally:
        _LIGHT_WARM_RUNNING = False


async def _periodic_light_board_warm() -> None:
    """경량 동적 보드(무버·KR 52주·장전후·NXT·테마/업종) 서버사이드 워머 — 페이지
    방문 없이도 장중 자동 신선(사용자 2026-06-17 '모든 대시보드가 내가 안 들어가도
    자기 주기로 갱신'). LIGHT_BOARD_WARM_SEC(기본 180초) 마다 daemon thread 로
    _warm_light_boards 발화. 무거운 yfinance 52주는 슬롯스캐너(시간당)가 담당 —
    분리. 단순 간격 sleep·daemon fire(실수 #6 shutdown-join 회피)·startup 늦춤
    (슬롯스캐너 120s·prewarm 과 startup 버스트 겹침 완화). ⚠️ 무버는 on-visit
    SWR=30초지만 서버 워머는 부하(네이버 안티봇) 고려 기본 180초 — 더 조이려면
    LIGHT_BOARD_WARM_SEC 낮춤, 네이버 차단 시 /naverpause 로 워머도 자동 skip."""
    await asyncio.sleep(170)
    warm_sec = max(60, int(os.getenv("LIGHT_BOARD_WARM_SEC", "180")))
    while True:
        try:
            import threading as _lbt
            _lbt.Thread(target=_warm_light_boards, daemon=True,
                        name="light-board-warm").start()
        except Exception:
            log.exception("light board warm thread failed")
        await asyncio.sleep(warm_sec)


# 대시보드 명령 콘솔 — '/명령' → (update, ctx) 핸들러 화이트리스트.
# 런타임 호출(모든 cmd_* 정의 후)이라 forward-ref 안전. screener/screen 은
# 별도 채널 경로(아래 poller)라 제외, 티커 분석은 [분석] 버튼 전용이라 제외.
def _handle_yfpause(arg: str) -> str:
    """yfinance 정지 토글 + 상태 텍스트 (DM·채널 공유). arg=on|off|빈(상태만)."""
    from bot.finviz_client import set_yf_pause, yf_paused
    a = (arg or "").strip().lower()
    if a in ("on", "정지", "stop", "1", "true", "pause"):
        set_yf_pause(True)
    elif a in ("off", "해제", "resume", "0", "false", "재개"):
        set_yf_pause(False)
    paused = yf_paused()
    state = ("⏸ <b>정지됨</b> — yfinance 호출 skip (네이버·캐시·홈 지수만)"
             if paused else "▶️ <b>정상</b> — yfinance 호출 중")
    return (f"yfinance 상태: {state}\n\n"
            "정지하면 52주/급등락 스캔·시총·업종·실적의 yfinance 부하 작업이 멈춰 "
            "레이트리밋이 회복됩니다. 네이버 기반(무버·업종등락·홈 지수)·캐시는 그대로. "
            "<b>재시작해도 정지 유지</b>(마커 파일)라 스캔이 처음부터 다시 안 돕니다.\n\n"
            "<code>/yfpause on</code> 정지 · <code>/yfpause off</code> 재개")


async def cmd_yfpause(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """yfinance 호출 일시정지 토글 — /yfpause [on|off] (DM)."""
    arg = ""
    if update.message and update.message.text:
        parts = update.message.text.split()
        if len(parts) > 1:
            arg = parts[1]
    if update.message:
        await update.message.reply_text(_handle_yfpause(arg),
                                        parse_mode=ParseMode.HTML)


def _handle_naverpause(arg: str) -> str:
    """네이버 정지 토글 + 상태 텍스트 (DM·채널 공유, 사용자 2026-06-14 '네이버도
    야후처럼 끄고 키는 명령'). arg=on|off|빈(상태만)."""
    from bot.finviz_client import naver_paused, set_naver_pause
    a = (arg or "").strip().lower()
    if a in ("on", "정지", "stop", "1", "true", "pause"):
        set_naver_pause(True)
    elif a in ("off", "해제", "resume", "0", "false", "재개"):
        set_naver_pause(False)
    paused = naver_paused()
    state = ("⏸ <b>정지됨</b> — 네이버 호출 skip (캐시만)"
             if paused else "▶️ <b>정상</b> — 네이버 호출 중")
    return (f"네이버 상태: {state}\n\n"
            "정지하면 네이버 국내·해외 무버·업종·종목명·예탁금 등 네이버 fetch 가 "
            "멈춰 안티봇 차단이 회복됩니다. 이미 산출된 캐시는 그대로 보여요. "
            "<b>재시작해도 정지 유지</b>(마커 파일).\n\n"
            "<code>/naverpause on</code> 정지 · <code>/naverpause off</code> 재개")


async def cmd_naverpause(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """네이버 호출 일시정지 토글 — /naverpause [on|off] (DM)."""
    arg = ""
    if update.message and update.message.text:
        parts = update.message.text.split()
        if len(parts) > 1:
            arg = parts[1]
    if update.message:
        await update.message.reply_text(_handle_naverpause(arg),
                                        parse_mode=ParseMode.HTML)


def _handle_health() -> str:
    """야후/네이버 소스 헬스체크 텍스트 (DM·채널 공유). 정지 우회 raw fetch."""
    try:
        from bot.source_health import format_report, run
        return "🩺 <b>데이터 소스 헬스체크</b>\n" + _html.escape(format_report(run()))
    except Exception as exc:
        return f"헬스체크 실패: {_html.escape(str(exc)[:200])}"


async def cmd_gov(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/gov 종목 — 거버넌스 브리핑 (KR·DART). open-proxy-mcp 검토 채택
    (2026-07-04) — 네이티브 구현(키 로컬), 텔레그램·대시보드 콘솔 공용."""
    if not update.message:
        return
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text(
            "사용: <code>/gov 종목</code> — 예) /gov 삼성물산 · /gov 005930\n"
            "대주주·임원 지분·주총/활동주의 공시(90일)·AI 3줄 종합 (KR 전용)",
            parse_mode=ParseMode.HTML)
        return
    msg = await update.message.reply_text("🏛 거버넌스 조회 중… (DART)",
                                          parse_mode=ParseMode.HTML)
    from bot.governance import build_gov_brief
    text = await asyncio.to_thread(build_gov_brief, query)
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True)
    except Exception:
        # HTML 파스 거절(400)까지 겹치면 평문으로 degrade — '조회 중…'에서
        # 무출력으로 멈추던 경로 차단 (리뷰 2026-07-04 #1).
        try:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                            disable_web_page_preview=True)
        except Exception:
            from bot.dashboard_console import strip_tg_html
            await update.message.reply_text(strip_tg_html(text),
                                            disable_web_page_preview=True)


async def on_private_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """DM 자유 텍스트 — 자연어 거버넌스 질의('하이닉스 거버넌스')만 /gov 와
    동일 처리, 그 외 평문은 침묵(기존 동작 유지 — 사용자 2026-07-04)."""
    if not update.message:
        return
    from bot.governance import extract_gov_query, build_gov_brief
    q = extract_gov_query((update.message.text or "").strip())
    if not q:
        return
    msg = await update.message.reply_text("🏛 거버넌스 조회 중… (DART)",
                                          parse_mode=ParseMode.HTML)
    text = await asyncio.to_thread(build_gov_brief, q)
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True)
    except Exception:
        try:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                            disable_web_page_preview=True)
        except Exception:
            from bot.dashboard_console import strip_tg_html
            await update.message.reply_text(strip_tg_html(text),
                                            disable_web_page_preview=True)


async def cmd_health(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """야후/네이버 소스 헬스체크 — /health (DM). 잘 받아오는지 직접 점검."""
    if update.message:
        msg = await update.message.reply_text("🩺 점검 중…", parse_mode=ParseMode.HTML)
        text = await asyncio.to_thread(_handle_health)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def _periodic_reminders(application) -> None:
    """카드 알람 발송 루프 (60초, KST). dashboard_server 가 ~/.tradingagents/
    reminders.json 에 저장한 알람 중 오늘 미발송·시각 도달분을 텔레그램으로 발송
    (메모 + 카드내용 + ✅확인 버튼). 확인 시 종료, 미확인 시 다음날 같은 시각 재발송.
    한 발송 실패가 루프를 죽이지 않음(요청별 try/except)."""
    import bot.reminders as _rem
    await asyncio.sleep(20)
    while True:
        await asyncio.sleep(60)
        try:
            if not CHANNEL_CHAT_IDS:
                continue
            now = datetime.now(_KST)
            today = now.date().isoformat()
            due = await asyncio.to_thread(_rem.due, now, today)
            for d in due:
                memo = (d.get("memo") or "").strip()
                card = (d.get("card") or "").strip()
                body = (f"⏰ <b>알람</b> · {_html.escape(d.get('time',''))} (KST)\n"
                        + (f"\n📝 {_html.escape(memo)}\n" if memo else "")
                        + (f"\n━━━━━\n{_html.escape(card[:1500])}" if card else ""))
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                    "✅ 확인 (알람 종료)", callback_data=f"rmok:{d['key']}")]])
                sent_ok = False
                for cid in CHANNEL_CHAT_IDS:
                    try:
                        await application.bot.send_message(
                            chat_id=cid, text=body, parse_mode=ParseMode.HTML,
                            reply_markup=kb, disable_web_page_preview=True)
                        sent_ok = True
                    except Exception as exc:
                        log.warning("reminder send to %s failed: %s", cid, exc)
                if sent_ok:        # 발송 성공분만 오늘 발송표시(실패 시 다음 분 재시도)
                    await asyncio.to_thread(_rem.mark_sent, d["surface"], d["id"], today)
        except Exception:
            log.exception("reminder loop iteration failed")


async def on_reminder_confirm(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """✅확인 콜백 — rmok:<key> 알람 종료(삭제) + 버튼 제거."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("rmok:"):
        return
    key = data.split(":", 1)[1]
    import bot.reminders as _rem
    res = await asyncio.to_thread(_rem.confirm_by_key, key)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        log.debug("reminder confirm: clear keyboard failed: %s", exc)
    try:
        await query.message.reply_text(
            "✅ 알람을 종료했습니다." if res else "이미 종료된 알람입니다.")
    except Exception:
        pass


async def cmd_backtest_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/backtest_review — 트레이드 씨시스 청산 이력 5축 품질평가
    (Sample Size/Expectancy/Risk Management/Robustness/Execution Realism →
    Deploy/Refine/Abandon). claude-trading-skills backtest-expert 스킬 이식
    (bot/backtest_review.py)."""
    if update.message is None:
        return
    try:
        from bot import backtest_review
        result = await asyncio.to_thread(backtest_review.review_trade_history)
        text = backtest_review.review_text(result)
    except Exception as exc:
        log.exception("backtest_review failed: %s", exc)
        text = "⚠️ 백테스트 리뷰 계산 중 오류가 발생했습니다."
    await update.message.reply_text(text)


def _static_command_registry() -> dict:
    """정적 명령 단일 레지스트리 — name → (handler, 메뉴 설명).

    텔레그램과 대시보드 명령어를 구조적으로 링크 (사용자 2026-06-11
    '명령어 자체가 똑같이 링크'). 네 소비처가 전부 이 테이블에서 파생:
      (a) 텔레그램 add_handler 일괄 등록
      (b) set_my_commands 메뉴 (mobile autocomplete)
      (c) 대시보드 명령 콘솔 핸들러 dict
      (d) 콘솔 '사용 가능' 안내 목록
    새 명령 추가 = 여기 한 줄 → 네 곳 동시 반영, drift 구조적 차단
    (/dart_alert 가 콘솔 dict 누락으로 대시보드에서 '알 수 없는 명령'
    이던 클래스). screener/screen 은 콘솔에서 전용 분기(비싼·비동기
    채널 경로)가 먼저 잡음. dict 순서 = 텔레그램 메뉴 노출 순서."""
    return {
        "start": (cmd_help, "사용법 안내"),
        "help": (cmd_help, "사용법 안내"),
        "usage": (cmd_usage, "사용량 / 통합 비용 / 7일 차트"),
        "screener_cost": (cmd_screener_cost, "Bottleneck Screener 비용 (Pro)"),
        "daily_byte_cost": (cmd_daily_byte_cost, "Daily Byte 비용 (KR 수급 브리프)"),
        "cheongyak_cost": (cmd_cheongyak_cost, "청약 Byte 비용 (신규 분양 피드)"),
        "realestate_cost": (cmd_realestate_cost, "부동산 Byte 비용 (실거래 브리프)"),
        "screener_list": (cmd_screener_list, "Screener 도메인 목록 (전체)"),
        "sites": (cmd_sites, "참고 사이트"),
        "blog": (cmd_blog, "감시 블로그 목록 (네이버 자동 수집 — 현재 8곳)"),
        "valuechain": (cmd_valuechain, "밸류체인 조회/스크리너 (/valuechain 회사·품목·업종 — 공급사·고객·수출품목·동종사)"),
        "watch": (cmd_watch, "종목 조건 감시 알림 (rsi/price/sma/52w/earnings)"),
        "watchlist": (cmd_watchlist, "감시 목록 보기"),
        "unwatch": (cmd_unwatch, "감시 삭제 (TICKER/id/all)"),
        "dart_alert": (cmd_dart_alert, "관심종목 DART 공시 알림 (on/off)"),
        "gov": (cmd_gov, "거버넌스 브리핑 (KR — 대주주·지분·주총/활동주의 공시)"),
        "yfpause": (cmd_yfpause, "yfinance 호출 일시정지 토글 (on/off)"),
        "naverpause": (cmd_naverpause, "네이버 호출 일시정지 토글 (on/off)"),
        "health": (cmd_health, "야후/네이버 소스 헬스체크 (잘 받아오는지)"),
        "paper": (cmd_paper, "페이퍼 트레이딩 (모의 매매·돈 0)"),
        "backtest_review": (cmd_backtest_review, "트레이드 이력 백테스트 리뷰 (5축 Deploy/Refine/Abandon)"),
        # /screen·/screener — 콘솔은 전용 분기, 텔레그램은 이 핸들러
        "screen": (cmd_screen, "조건부 스크리너 (PER<15 PBR<1 등 자유 조건)"),
        "screener": (cmd_screener, "Bottleneck 종목 발굴 (기본=AI 데이터센터)"),
        # /compare 는 DM 힌트 (실제 비교는 채널 on_channel_post)
        "compare": (cmd_compare_hint, "두 종목 비교 (채널에서 사용)"),
        # /portfolio 조회 전용 (업로드는 RAG 채널 watcher — 정책 2026-06-04)
        "portfolio": (cmd_portfolio, "💼 자산 (뱅크샐러드 zip 업로드)"),
    }


def _dash_console_commands() -> dict:
    """콘솔 핸들러 dict — screener/screen 은 전용 분기(채널 게시 경로)."""
    return {n: f for n, (f, _d) in _static_command_registry().items()
            if n not in ("screener", "screen")}


def _dash_console_visible() -> tuple:
    """패널 안내용 명령 목록 — 레지스트리에서 자동 파생 (start 별칭 제외)."""
    return tuple(k for k in _static_command_registry() if k != "start")


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
            req_id = req.get("id", "")
            log.info("dashboard request: kind=%s query=%r", kind, query)
            try:
                # 완료 시 같은 id 로 result 기록 — 대시보드 실행 버튼이
                # '작업중(빨강)' → 원복 시점을 알 수 있게 (사용자 2026-06-11).
                from bot.dashboard_requests import write_result as _wr
                if kind == "analyze":
                    raw = query.upper()
                    if not TICKER_RE.match(raw):
                        log.warning("dashboard analyze: bad ticker %r", raw)
                        _wr(req_id, {"ok": False, "done": True,
                                     "lines": [f"⚠️ 잘못된 티커: {raw}"]})
                        continue
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"🌐 대시보드 요청 — <code>/{_html.escape(raw)}</code> 분석",
                        parse_mode=ParseMode.HTML,
                    )
                    await _analyze_ticker_and_post(bot, chat_id, raw)
                    _wr(req_id, {"ok": True, "done": True, "lines": [
                        f"✅ {raw} 분석 완료 — 채널 게시 + 아카이브 갱신(새로고침)"]})
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
                        _wr(req_id, {"ok": False, "done": True,
                                     "lines": ["⚠️ 도메인 해석 실패 — 채널 메시지 확인"]})
                        continue
                    await _run_screener_and_send(
                        send=_send,
                        theme=resolved.get("theme"),
                        cache_key=resolved.get("cache_key"),
                        force_fresh=resolved.get("force_fresh", False),
                    )
                    _wr(req_id, {"ok": True, "done": True, "lines": [
                        "✅ Screener 완료 — 채널 게시 + 이 페이지 갱신(새로고침)"]})
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
                    _wr(req_id, {"ok": True, "done": True, "lines": [
                        "✅ 조건부 스크리너 완료 — 채널 게시 + 이 페이지 갱신(새로고침)"]})
                elif kind == "command":
                    # 대시보드 명령 콘솔 — '/명령' 실행 후 텍스트 답변을 result
                    # 스풀에 기록(브라우저가 api/command_result 로 폴링).
                    from bot.dashboard_requests import write_result
                    req_id = req.get("id", "")
                    raw = (query or "").strip()
                    toks = raw.lstrip("/").split()
                    word = toks[0].lower() if toks else ""
                    arg = " ".join(toks[1:])
                    # 단일 레지스트리 exact-match 우선 (screener_list/
                    # screener_cost 가 아래 screener_* prefix 분기에 먹히지
                    # 않게) → screener/screener_<slug>/screen 전용 분기 →
                    # 그 외 안내. 모든 정적 명령 = 대시보드에서도 작동
                    # (사용자 2026-06-11).
                    handler = _dash_console_commands().get(word)
                    if handler is not None:
                        from bot.dashboard_console import build_capture
                        upd, ctx2, lines = build_capture(raw, chat_id=chat_id)
                        try:
                            await handler(upd, ctx2)
                        except Exception as exc:
                            log.exception("dashboard command failed: %s", raw)
                            lines.append(f"⚠️ 명령 실행 오류: {exc}")
                        write_result(req_id, {"ok": True, "done": True,
                                              "lines": lines or ["(출력 없음)"]})
                    elif word == "screener" or word.startswith("screener_"):
                        # 비싼·비동기 → 기존 채널 경로 재사용 + 패널엔 접수
                        # 안내. /screener_<slug> 동적 숏컷도 동일 경로.
                        q = arg if word == "screener" else (
                            word[len("screener_"):] + " " + arg).strip()
                        async def _scc(t: str) -> None:
                            await bot.send_message(
                                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True)
                        write_result(req_id, {"ok": True, "done": True, "lines": [
                            "🌐 Bottleneck Screener 실행 시작"
                            + (f" — {q}" if q else " (기본 bottleneck)"),
                            "결과는 텔레그램 채널 + Screener 대시보드에 게시됩니다(~3-5분).",
                        ]})
                        resolved = await _resolve_screener_target(_scc, q.lower())
                        if resolved.get("mode") != "error":
                            await _run_screener_and_send(
                                send=_scc, theme=resolved.get("theme"),
                                cache_key=resolved.get("cache_key"),
                                force_fresh=resolved.get("force_fresh", False))
                    elif word == "screen":
                        async def _scc2(t: str) -> None:
                            await bot.send_message(
                                chat_id=chat_id, text=t, parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True)
                        write_result(req_id, {"ok": True, "done": True, "lines": [
                            f"🌐 조건부 스크리너 실행 — {arg or '(조건 없음)'}",
                            "결과는 텔레그램 채널에 게시됩니다.",
                        ]})
                        await _handle_screen(arg.split(), _scc2)
                    else:
                        write_result(req_id, {"ok": True, "done": True, "lines": [
                            f"알 수 없는 명령: /{word or '(빈 명령)'}",
                            "· 티커 분석은 슬래시 없이 종목 입력 후 [분석] 버튼",
                            "· 사용 가능: "
                            + " ".join("/" + c for c in _dash_console_visible())
                            + " · /screener_<도메인>",
                        ]})
            except Exception:
                log.exception("dashboard request failed: %s", req)
                try:
                    if req.get("id"):
                        from bot.dashboard_requests import write_result
                        write_result(req["id"], {"ok": False, "done": True,
                                                 "lines": ["⚠️ 처리 중 오류 — 텔레그램 채널/로그 확인"]})
                except Exception:
                    pass


# #19 소송/리스크 전 종목 채널 알림은 제거(사용자 2026-06-11 — 무차별 스팸
# + 휘발성 큐가 봇 재시작마다 같은 알림 재발송). 관심종목 한정 알림으로
# 교체: bot/dart_fav_alerts.py + /dart_alert + _periodic_dart_fav_alerts.

async def cmd_dart_alert(update, context) -> None:
    """관심종목 DART 공시 알림 토글 — /dart_alert [on|off] (2026-06-11)."""
    msg = update.effective_message
    if msg is None or update.effective_chat is None:
        return
    from bot import dart_fav_alerts as dfa
    arg = (context.args[0].lower() if getattr(context, "args", None) else "")
    if arg in ("on", "켜기", "1"):
        info = await asyncio.to_thread(dfa.enable, update.effective_chat.id)
        await msg.reply_text(
            f"📋 관심종목 공시 알림 ON — KR 관심종목 {info['codes']}개 대상.\n"
            f"기존 공시 {info['seeded']}건은 기준점 등록(발송 안 함). "
            f"지금부터 새 공시만 이 채팅으로 알립니다 (수집 후 ~1-2분 내).")
    elif arg in ("off", "끄기", "0"):
        await asyncio.to_thread(dfa.disable)
        await msg.reply_text("📋 관심종목 공시 알림 OFF.")
    else:
        s = await asyncio.to_thread(dfa.status)
        state_txt = "ON ✅" if s["enabled"] else "OFF"
        await msg.reply_text(
            f"📋 관심종목 DART 공시 알림: {state_txt}\n"
            f"대상: KR 관심종목 {s['codes']}개 (US 티커는 DART 미해당)\n"
            f"피드가 수집하는 전 카테고리(실적·계약·주주환원·자금조달·"
            f"시설투자·지분·리스크·조회공시 등) 알림.\n"
            f"사용법: /dart_alert on · /dart_alert off")


async def _periodic_dart_fav_alerts(application) -> None:
    """관심종목 DART 공시 알림 폴러 (75초) — dart_fav_alerts.poll_new 소비.

    dart-feed 타이머(별도 프로세스)가 쓴 아카이브를 스캔하므로 추가 DART
    호출 0. 영구 seen-set 이라 재시작/자정에 재발송 없음 (#19 버그 클래스
    구조적 차단)."""
    import html as _h
    while True:
        await asyncio.sleep(75)
        try:
            from bot import dart_fav_alerts as dfa
            items, chat_id = await asyncio.to_thread(dfa.poll_new)
            if not items or not chat_id:
                continue
            for it in items:
                cn = _h.escape(it.get("corp_name", ""))
                rn = _h.escape(it.get("report_nm", ""))
                cat = _h.escape(it.get("category", ""))
                txt = f'📋 <b>관심종목 공시</b> — {cn} <i>[{cat}]</i>\n{rn}'
                url = it.get("url", "")
                if url:
                    txt += f'\n<a href="{_h.escape(url)}">원문 보기</a>'
                try:
                    await application.bot.send_message(
                        chat_id=chat_id, text=txt, parse_mode="HTML",
                        disable_web_page_preview=True)
                except Exception:
                    log.warning("dart_fav_alerts: send failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("dart_fav_alerts poll failed")


async def _periodic_marketcap() -> None:
    """marketcap.html 3시간 주기 재생성(사용자 2026-07-06 — companiesmarketcap
    글로벌 시총 순위 이식). 첫 생성은 부팅 5초 후(파일 부재 404 창 최소화 +
    watchdog 재시작 루프에서도 생성 보장 — 리뷰 2026-07-06), 이후 3h
    (클라이언트 디스크 캐시 TTL 과 동일 — 캐시 fresh 면 재렌더만, 외부 호출 0)."""
    first = True
    while True:
        try:
            await asyncio.sleep(5 if first else 3 * 3600)
            first = False
            from bot.dashboard import regenerate_marketcap_page
            await asyncio.to_thread(regenerate_marketcap_page)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("marketcap regen failed")


async def _periodic_fred_boards() -> None:
    """FRED 보드(ppi/liquidity.html) 6시간 주기 재생성(사용자 2026-07-02) —
    유동성 일간 지표(VIX·스프레드·커브·환율 히스토리)가 당일 반영되게. 자정
    regen(_periodic_dashboard_refresh)과 별개 태스크. 비용: FRED 무료 ~120콜
    ×4/일(캐시 5h) — 무시 가능. 첫 사이클은 6h 후(startup 스레드가 방금 생성)."""
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            from bot.fred_boards import regenerate_fred_boards
            await asyncio.to_thread(regenerate_fred_boards)
            log.info("fred boards 6h regen: ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("fred boards 6h regen failed")
        # 시장타이밍 보드(2026-07-26) — 같은 6시간 주기(분산일·FTD·매크로
        # 레짐 갱신 빈도로 충분, 크립토 스코어도 마찬가지). 실패해도 FRED
        # 보드 갱신과 독립(try 분리).
        try:
            from bot.market_timing import regenerate_market_timing
            await asyncio.to_thread(regenerate_market_timing)
            log.info("market timing 6h regen: ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("market timing 6h regen failed")
        # Breadth 4구간 전략 보드(2026-08-16 사용자 캡처 전략) — 같은 6시간
        # 주기. 매일 값은 중간점검이고 월말 종가에만 확정 신호가 기록되므로
        # 이 빈도로 충분. 실패해도 위 보드들과 독립(try 분리).
        try:
            from bot.breadth_strategy import regenerate as _regen_breadth
            await asyncio.to_thread(_regen_breadth)
            log.info("breadth strategy 6h regen: ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("breadth strategy 6h regen failed")
        # 경제캘린더 보드(2026-07-26) — CPI/Core CPI/PPI/고용/AHE/실업률/실업수당(신규·연속)/소매/ECI/GDP/PCE/Core PCE/FOMC 발표일정.
        # 같은 6시간 주기(발표일 자체가 자주 안 바뀜, FRED 캐시도 12h) —
        # 실패해도 위 두 보드 갱신과 독립(try 분리).
        try:
            from bot.econ_calendar import regenerate_econ_calendar
            await asyncio.to_thread(regenerate_econ_calendar)
            log.info("econ calendar 6h regen: ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("econ calendar 6h regen failed")


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
                                       regenerate_blog_index,
                                       regenerate_watchlist_index,
                                       regenerate_paper_index,
                                       regenerate_market_index,
                                       regenerate_dart_feed_index,
                                       regenerate_valuechain_index)
            regenerate_index()
            regenerate_daily_byte_index()
            regenerate_realestate_index()
            regenerate_cheongyak_index()
            regenerate_gics_candidates_index()
            regenerate_reddit_insider_index()
            regenerate_blog_index()
            regenerate_watchlist_index()
            regenerate_market_index()
            regenerate_dart_feed_index()
            regenerate_valuechain_index()
            # FRED 보드(PPI·유동성) — 일 1회면 충분(월간/주간 시리즈, 캐시 12h).
            # ⚠️ ~90 네트워크콜 = to_thread 필수(이벤트루프 차단 시 getUpdates
            # 침묵 → watchdog 오탐 재시작, 실수 #2 — 리뷰 finding).
            try:
                from bot.fred_boards import regenerate_fred_boards
                await asyncio.to_thread(regenerate_fred_boards)
            except Exception:
                log.exception("fred boards regen failed")
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
            # 트레이드 씨시스 주간 다이제스트(2026-07-26) — 월요일 자정에만
            # 최근 7일 청산분 승률·기대값·손익비 발송(청산 0건이면 스킵).
            if now_kst.weekday() == 0:
                try:
                    from bot import trade_thesis
                    digest = trade_thesis.weekly_digest_text(days=7)
                    if digest and application is not None and CHANNEL_CHAT_IDS:
                        for _cid in CHANNEL_CHAT_IDS:
                            try:
                                await application.bot.send_message(
                                    chat_id=_cid, text=digest)
                            except Exception:
                                pass
                except Exception:
                    log.exception("weekly trade thesis digest failed")
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

    # 사용자 2026-06-14 '야후 콜 멈춰봐 — 안 돌아와' — 이 배포에서 yfinance 1회
    # 자동 정지(레이트리밋 회복). marker 로 배포당 1회만(이미 있으면 유지). 정지
    # 중엔 startup force-recompute·prewarm·52주/무버 스캔이 skip → 재시작이 스캔을
    # 재발화 안 함(사용자 질문 '재시작하면 첨부터 다시 도나?' → NO). /yfpause off 재개.
    try:
        from bot.finviz_client import _CACHE_DIR as _FCD, set_yf_pause
        _ap = _FCD / ".yf_autopause_v1"
        if not _ap.exists():
            set_yf_pause(True)
            _ap.parent.mkdir(parents=True, exist_ok=True)
            _ap.write_text("paused")
            log.info("startup: yfinance 1회 자동 정지 (사용자 요청) — /yfpause off 로 재개")
    except Exception as exc:
        log.warning("startup: yf autopause 실패: %s", exc)
    # 2026-08-03 fix — 아래 정적 페이지 재생성(screener~dart_feed)이 이 async
    # post_init 본문에서 동기 실행되면 app.run_polling() 의 getUpdates 시작을
    # 그만큼 늦춘다. dart_feed 재생성은 FSC(data.go.kr) 시총조회 루프를 돌리는데
    # 45초 소프트예산(bot/dashboard.py _mc_deadline) + 이미 시작된 콜의 최대
    # 20초(fsc_client._TIMEOUT) 오버런까지 더해질 수 있어, FSC 가 저하(429·
    # timeout 연발)될 때 이 블록 하나로 65초+ 가 나갈 수 있다. 앞선 정적
    # 재생성들과 누적되면 watchdog 의 180초 'bot starting' 유예(deploy/
    # watchdog.sh)를 넘겨 polling 이 뜨기도 전에 재시작당하고, 재시작마다
    # 이 cascade 를 처음부터 다시 도는 자기악화 루프가 된다(2026-08-03 실측
    # — 09:07·09:11 hang 알림, dart_feed regen 직후 Stopping 로그로 확인).
    # main()의 _startup_regen 스레드(4740행)가 이미 다른 페이지 세트에 쓰던
    # 것과 동일 패턴으로 백그라운드화 — getUpdates 가 즉시 시작되므로
    # watchdog 이 startup regen 으로는 절대 트립하지 않는다.
    def _static_pages_regen():
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
            from bot.dashboard import (regenerate_blog_index,
                                       regenerate_valuechain_index)
            regenerate_blog_index()
            regenerate_valuechain_index()
            log.info("startup: blog.html + valuechain.html regenerated with current code")
        except Exception as exc:
            log.warning("startup: blog.html regen failed: %s", exc)
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

    try:
        import threading as _sp_thr
        _sp_thr.Thread(target=_static_pages_regen, name="static-pages-regen",
                       daemon=True).start()
    except Exception as exc:
        log.warning("startup: static pages regen thread failed: %s", exc)
    # FRED 보드(ppi/liquidity.html) — startup 백그라운드 무조건 재생성(파일 부재
    # 게이트 제거: 렌더 코드 배포가 다음 자정까지 화면에 안 보이는 stale 차단,
    # 실수 #11 — 리뷰 finding. 같은 날 재실행은 FRED 캐시 12h 라 사실상 무료).
    # sync 금지(~90콜 ≈ 수십 초) — 데몬 스레드.
    try:
        import threading as _fb_thr

        def _fred_boards_initial():
            try:
                from bot.fred_boards import regenerate_fred_boards
                regenerate_fred_boards()
                log.info("startup: FRED boards regenerated")
            except Exception as exc:
                log.warning("startup: FRED boards regen failed: %s", exc)
        _fb_thr.Thread(target=_fred_boards_initial, daemon=True).start()
    except Exception as exc:
        log.warning("startup: FRED boards thread failed: %s", exc)
    # 시장타이밍 보드(2026-07-26) — 같은 이유로 startup 무조건 재생성.
    try:
        import threading as _mt_thr

        def _market_timing_initial():
            try:
                from bot.market_timing import regenerate_market_timing
                regenerate_market_timing()
                log.info("startup: market timing regenerated")
            except Exception as exc:
                log.warning("startup: market timing regen failed: %s", exc)
        _mt_thr.Thread(target=_market_timing_initial, daemon=True).start()
    except Exception as exc:
        log.warning("startup: market timing thread failed: %s", exc)
    # Breadth 전략 보드(2026-08-16) — 같은 이유로 startup 무조건 재생성.
    # nav 링크는 즉시 살아나므로 여기 없으면 배포 직후 최소 6시간 404 이고,
    # watchdog 재시작이 6시간보다 잦으면 한 번도 안 만들어진다(실수 #11).
    try:
        import threading as _bs_thr

        def _breadth_strategy_initial():
            try:
                from bot.breadth_strategy import regenerate as _regen_bs
                _regen_bs()
                log.info("startup: breadth strategy regenerated")
            except Exception as exc:
                log.warning("startup: breadth strategy regen failed: %s", exc)
        _bs_thr.Thread(target=_breadth_strategy_initial, daemon=True).start()
    except Exception as exc:
        log.warning("startup: breadth strategy thread failed: %s", exc)
    # 경제캘린더 보드(2026-07-26) — 같은 이유로 startup 무조건 재생성.
    try:
        import threading as _ec_thr

        def _econ_calendar_initial():
            try:
                from bot.econ_calendar import regenerate_econ_calendar
                regenerate_econ_calendar()
                log.info("startup: econ calendar regenerated")
            except Exception as exc:
                log.warning("startup: econ calendar regen failed: %s", exc)
        _ec_thr.Thread(target=_econ_calendar_initial, daemon=True).start()
    except Exception as exc:
        log.warning("startup: econ calendar thread failed: %s", exc)
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
            # v3 당월 정리 백필 — 최초 1회만 (marker gate, 백그라운드 수십 분).
            # 당월 1일 이전(5월) 아카이브 삭제 → 당월 전체 재fetch(새 분류
            # 룰) → FSC 시총 일괄 워밍(모든 카드 '시가총액/현재가' 줄 즉시
            # 표시) (사용자 2026-06-11). 차트 .charts_backfilled 패턴.
            try:
                from bot.dart_feed import backfill_v3_once_if_needed
                st = backfill_v3_once_if_needed()
                if st:
                    from bot.dashboard import regenerate_dart_feed_index as _rg2
                    _rg2()
                    log.info("startup: DART backfill v3 — 삭제 %d개 · 재분류 %d "
                             "· 신규 %d · 시총워밍 %d코드",
                             st.get("purged", 0), st["reclassified"],
                             st["added"], st.get("warmed", 0))
            except Exception as exc:
                log.warning("startup: DART backfill v3 failed: %s", exc)
            # v4 파싱 배치(2026-06-12) 소급 — ①변경 파서 재추출(성공시만
            # 교체) ②doc_fail 클리어(신설 파서 재시도) ③당월 재fetch
            # (기타경영사항·투자판단 keep 신설분). marker gate · ₩0.
            try:
                from bot.dart_feed import backfill_v4_once_if_needed
                st4 = backfill_v4_once_if_needed()
                if st4:
                    from bot.dashboard import regenerate_dart_feed_index as _rg3
                    _rg3()
                    rp = st4.get("reparse", {})
                    log.info("startup: DART backfill v4 — doc_fail %d 클리어"
                             " · 재추출 %d/%d 교체 · 재분류 %d · 신규 %d",
                             st4.get("doc_fail_cleared", 0),
                             rp.get("replaced", 0), rp.get("checked", 0),
                             st4.get("reclassified", 0), st4.get("added", 0))
            except Exception as exc:
                log.warning("startup: DART backfill v4 failed: %s", exc)
            # v5 로컬 재분류 (배당 분리·공정공시 주주환원 승격) — API 0·수 초.
            try:
                from bot.dart_feed import reclassify_v5_once_if_needed
                st5 = reclassify_v5_once_if_needed()
                if st5:
                    from bot.dashboard import regenerate_dart_feed_index as _rg5
                    _rg5()
                    log.info("startup: DART 재분류 v5 — 제목 %d · 본문승격 %d",
                             st5.get("reclassified", 0), st5.get("upgraded", 0))
            except Exception as exc:
                log.warning("startup: DART reclassify v5 failed: %s", exc)
            # v7 — 유형자산→자산양수도 분류 fix 소급 (API 0·수 초)
            try:
                from bot.dart_feed import reclassify_v7_once_if_needed
                st7 = reclassify_v7_once_if_needed()
                if st7:
                    from bot.dashboard import regenerate_dart_feed_index as _rg7
                    _rg7()
                    log.info("startup: DART 재분류 v7 — 제목 %d · 본문승격 %d",
                             st7.get("reclassified", 0), st7.get("upgraded", 0))
            except Exception as exc:
                log.warning("startup: DART reclassify v7 failed: %s", exc)
            # v6 — 쿨다운 고착 미파싱 1회 해제 (generic 폴백과 함께 재시도)
            try:
                from bot.dart_feed import clear_doc_fail_once_v6
                n6 = clear_doc_fail_once_v6()
                if n6:
                    log.info("startup: DART doc_fail v6 — %d건 해제 "
                             "(미파싱 즉시 재시도)", n6)
            except Exception as exc:
                log.warning("startup: DART doc_fail v6 failed: %s", exc)
            # v7 — generic 폴백 래퍼(2026-06-14) 배포 후 고착 미파싱 재해제
            try:
                from bot.dart_feed import clear_doc_fail_once_v7
                n7 = clear_doc_fail_once_v7()
                if n7:
                    log.info("startup: DART doc_fail v7 — %d건 해제 "
                             "(generic 폴백 래퍼로 재시도)", n7)
            except Exception as exc:
                log.warning("startup: DART doc_fail v7 failed: %s", exc)
            # 분기 영업(잠정)실적 Form C 파서(2026-06-16 오리온) 소급 재추출 1회
            try:
                from bot.dart_feed import reparse_provisional_once_if_needed
                stp = reparse_provisional_once_if_needed()
                if stp and stp.get("replaced"):
                    log.info("startup: DART 분기 잠정실적 재추출 — 교체 %d건 "
                             "(doc_fail %d 해제)", stp.get("replaced", 0),
                             stp.get("cleared", 0))
            except Exception as exc:
                log.warning("startup: DART 분기 잠정실적 재추출 failed: %s", exc)
            # 투자판단 실적→기타 소급 재분류 1회 (2026-06-16 압타바이오 — reclassify
            # v5/v7 의 'new_cat != 기타' 가드가 다운그레이드를 막던 것 전용 패스로 보강)
            try:
                from bot.dart_feed import reclassify_tuja_once_if_needed
                ntj = reclassify_tuja_once_if_needed()
                if ntj:
                    log.info("startup: DART 투자판단 실적→기타 재분류 %d건 소급", ntj)
            except Exception as exc:
                log.warning("startup: DART 투자판단 재분류 failed: %s", exc)
            # 관리종목 지정우려/지정 파서 신설(2026-06-19) 소급 — 기타→리스크 재분류
            # + detail(사유·연속거래일·지정조건) 채움 1회.
            try:
                from bot.dart_feed import backfill_admin_issue_once_if_needed
                sta = backfill_admin_issue_once_if_needed()
                if sta and (sta.get("reclassified") or sta.get("filled")):
                    log.info("startup: DART 관리종목 백필 — 재분류 %d · detail %d건",
                             sta.get("reclassified", 0), sta.get("filled", 0))
            except Exception as exc:
                log.warning("startup: DART 관리종목 백필 failed: %s", exc)
            # 잔여 미파싱(detail 있으나 meaningful 없음 — 관리종목·조회공시 등 stale)
            # 전 카테고리 재추출 1회 (사용자 2026-06-20 '미파싱처리')
            try:
                from bot.dart_feed import backfill_unparsed_once_if_needed
                stu = backfill_unparsed_once_if_needed()
                if stu and stu.get("fixed"):
                    log.info("startup: DART 미파싱 재추출 — 교체 %d/%d건",
                             stu.get("fixed", 0), stu.get("checked", 0))
            except Exception as exc:
                log.warning("startup: DART 미파싱 재추출 failed: %s", exc)

        _dt_thr.Thread(target=_dart_initial_fetch, daemon=True).start()
    except Exception as exc:
        log.warning("startup: DART initial fetch thread failed: %s", exc)

    # 52주 캐시 강제 재산출 1회 (사용자 2026-06-14 'HK 산출중/거래량·시총 이상,
    # TW 영문') — HK 유니버스 캡 + 네이버 overlay + TW 中文→영문 변경이 주말
    # session-fresh(장 밖 마지막 마감 이후 재스캔 0)에 막혀 다음 장까지 안 보이던
    # 것을 배포 후 1회 강제 반영(_compute = freshness 우회). marker(버전)로 배포당
    # 1회만 — 버전 bump 시 재실행. 백그라운드(yfinance 스캔 수 분)·graceful.
    def _highlow_force_recompute():
        from bot.finviz_client import _CACHE_DIR
        marker = _CACHE_DIR / ".highlow_force"
        ver = "2026-06-14-audit-naver-maps-rebuild"
        try:
            if marker.read_text(encoding="utf-8").strip() == ver:
                return
        except OSError:
            pass
        # 전 시장 52주/무버 재산출 + US/CN/HK/JP 업종·시총 네이버 맵 빌드.
        # _prewarm_highlow 가 freshness 우회 _compute 직접 호출 → JP overlay·시총
        # 필터·US/CN 업종·TW 영문/시총이 주말 stale 에 막히지 않고 즉시 반영.
        try:
            _prewarm_highlow()
        except Exception as exc:
            log.warning("startup: 52주/무버 강제 재산출 실패: %s", exc)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(ver, encoding="utf-8")
        except OSError:
            pass
        log.info("startup: 전 시장 52주/무버 강제 재산출 완료 "
                 "(JP overlay·시총필터·US/CN 업종·TW 영문)")
    try:
        import threading as _hl_thr
        _hl_thr.Thread(target=_highlow_force_recompute, daemon=True,
                       name="highlow-force").start()
    except Exception as exc:
        log.warning("startup: 52주 강제 재산출 thread 실패: %s", exc)

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
        # 단일 레지스트리에서 메뉴 파생 — 새 명령이 자동으로 메뉴/autocomplete
        # 에 노출 (사용자 2026-06-11 '텔레그램·대시보드 명령어 링크').
        commands = [BotCommand(n, d)
                    for n, (_f, d) in _static_command_registry().items()]
        # Per-domain shortcut commands. Description = theme display name
        # (capped at 100 chars to leave headroom under Telegram's 256-char
        # description limit). Sorted by slug for stable ordering in the
        # client autocomplete menu.
        try:
            from bot.screener_themes import list_domains
            # L4 sub-industry 는 메뉴(autocomplete)에서 제외 — Telegram 100/scope
            # cap 보존(L4 full rollout 78+ 모듈 대비, 2026-06-14). L4 는 핸들러
            # (_register_dynamic_screener_handlers)·/screener 별칭·/screener_list
            # 로 접근 가능, 메뉴에만 미노출.
            for d in sorted(list_domains(), key=lambda x: x["slug"]):
                if d.get("layer") == "L4_SUBINDUSTRY":
                    continue
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
    # FRED 보드 6시간 주기 재생성(사용자 2026-07-02) — 유동성 일간 지표(VIX·
    # 스프레드·커브) 당일 반영. 자정 regen 과 별개, 첫 사이클은 6h 후(startup
    # 스레드가 방금 생성). to_thread(네트워크 ~120콜, 이벤트루프 차단 금지).
    application._fred_boards_task = asyncio.create_task(_periodic_fred_boards())
    application._marketcap_task = asyncio.create_task(_periodic_marketcap())
    application._paper_pending_task = asyncio.create_task(_periodic_paper_pending(application))
    application._market_refresh_task = asyncio.create_task(_periodic_market_refresh())
    application._highlow_prewarm_task = asyncio.create_task(_periodic_highlow_prewarm())
    # 컴퓨티드 보드(US/JP/HK/TW 52주 + TW 무버) 시간대별 슬롯 스캐너 (:00/:20/:40) —
    # 장중 1h force 재산출(페이지 방문 없이도 최신) + 장 밖 EOD 1회 후 freeze, 무거운
    # Asia 52주를 20분씩 분산 (사용자 2026-06-16, KR·네이버 직접 보드 제외 전 시장).
    application._highlow_scan_task = asyncio.create_task(_periodic_highlow_scan())
    # 경량 동적 보드 워머 (180초) — 무버·KR 52주·장전후·NXT·테마/업종을 방문 없이도
    # 장중 자동 신선 유지 (사용자 2026-06-17). 무거운 52주는 위 슬롯스캐너 담당.
    application._light_board_warm_task = asyncio.create_task(_periodic_light_board_warm())
    # 관심종목 DART 공시 알림 폴러 (75초, /dart_alert on 일 때만 발송)
    application._dart_fav_alerts_task = asyncio.create_task(
        _periodic_dart_fav_alerts(application))
    # 대시보드 '분석/실행' 버튼 요청 스풀 폴러 (5초) — dashboard_server 가
    # 떨어뜨린 요청을 채널 명령과 동일 경로로 실행.
    application._dashboard_requests_task = asyncio.create_task(
        _periodic_dashboard_requests(application))
    # 카드 알람(메모 리마인더) 발송 루프 (60초, KST).
    application._reminders_task = asyncio.create_task(
        _periodic_reminders(application))

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
    "http://136.115.27.77:8081/06beb08f5f4ad5515007e65f8f60b471/",
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
    # 정적 명령 — 단일 레지스트리에서 일괄 등록 (대시보드 명령 콘솔·
    # 텔레그램 메뉴와 같은 테이블, 사용자 2026-06-11). 개별 명령 주석은
    # 레지스트리 참조.
    for _cmd, (_fn, _desc) in _static_command_registry().items():
        app.add_handler(CommandHandler(_cmd, _fn))
    # Per-domain shortcut commands — `/screener_bottleneck`, `/screener_
    # healthcare` 등. Telegram client 가 자동 hyperlink → 클릭으로 입력
    # prefill, 엔터 1회로 실행. 사용자 ref 예시 (/find_all / /papers_
    # guide 패턴) 와 동일 UX. 등록은 boot 시 1회, 새 도메인 추가 시
    # 봇 재시작 (auto-deploy 1분) 후 자동 노출.
    _register_dynamic_screener_handlers(app)
    app.add_handler(CallbackQueryHandler(on_full_report, pattern=r"^full:"))
    app.add_handler(CallbackQueryHandler(on_reminder_confirm, pattern=r"^rmok:"))
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
    # DM 평문 — 자연어 거버넌스 질의만 반응(그 외 침묵). COMMAND 제외라
    # 위 핸들러들과 충돌 없음 (사용자 2026-07-04 '자연어로도 /gov').
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            on_private_text,
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
