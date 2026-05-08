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
    first_word = body.split(None, 1)[0].lower() if body else ""

    # /start or /help in the channel — surface the usage guide. PTB's
    # CommandHandler only fires on Update.message, not channel_post, so
    # without this branch a user typing /help in the channel would either
    # silently no-op or (with our old TICKER_RE fall-through) get
    # interpreted as a 'HELP' ticker.
    if first_word in ("start", "help"):
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=_HELP_TEXT,
            parse_mode=ParseMode.HTML,
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


_HELP_TEXT = """🧠 <b>NOAH의 주식분석 봇 사용법</b>
━━━━━━━━━━━━━━
<b>【1. 명령어】</b>
아래 명령어를 탭하면 입력창에 자동 입력됩니다.

▸ 도움말 / 상태 (어디서든)
/start
/help
/usage

▸ 단일 종목 분석 (채널에서)
/NVDA
/AAPL

▸ 두 종목 비교 (채널에서)
/compare NVDA AMD
/compare GOOGL META

※ 다른 종목은 /티커 형식으로 직접 입력 (예: /PLTR, /CRM)
※ 등록된 채널 외에서는 무시됨 (보안)
※ 입력창 옆 '/' 메뉴 버튼으로도 등록 명령 확인 가능

━━━━━━━━━━━━━━
<b>【2. 분석 흐름】</b>
 1) 채널에 <code>/티커</code> 입력 → 즉시 진행 메시지
 2) 4명 분석가 단계 (~2분) — Gemini 2.5 Flash
    📈 시장 (기술적 + 거시 + 리스크 + 섹터)
    💬 감정 (소셜/뉴스 감성)
    📰 뉴스 (회사 + 거시 통합)
    💰 펀더멘털 (재무 + DCF + Comps)
 3) Bull/Bear 페르소나 토론 (~1분) — Flash-Lite
    Bull: 워런 버핏 (해자/가치) + 피터 린치 (PEG/성장)
    Bear: 벤 그레이엄 (안전마진) + 하워드 막스 (사이클)
 4) Trader 제안 → Risk 3인 토론 → Portfolio Manager 최종
    핵심 결정 3개 노드는 Gemini 2.5 Pro
 5) 채널에 요약 + [📋 전체 리포트] 버튼

━━━━━━━━━━━━━━
<b>【3. 요약 메시지 구성】</b>
 • 🎯 최종 판정 (Buy / Overweight / Hold / Underweight / Sell)
 • 📒 지난 추천 결과 (자동, 5거래일 지나면)
 • 4명 분석가 stance 한눈
 • 각 분석가 한 줄 요지
 • [📋 전체 리포트 보기] → 토론·계획·결정 풀버전

━━━━━━━━━━━━━━
<b>【4. 자동 사용 데이터】</b>
 • 가격/지표  yfinance (15년 캐시)
 • 뉴스       yfinance + Alpha Vantage
 • 펀더멘털   yfinance 재무제표 (분기 + 연간)
 • 거시       ^TNX·^VIX·DXY·CL=F·GC=F·BTC-USD (자동)
 • 섹터 매핑  XLK·SOXX·IGV·IBB·KRE·XOP·TAN·... (자동)
 • 리스크     연환산 σ / Sharpe / Sortino / VaR / Max DD / 베타
 • 윈도우     기본 28일 (분석 디렉티브)

━━━━━━━━━━━━━━
<b>【5. 메모리 피드백 루프】</b>
 • 매 분석은 추천을 pending으로 기록
 • 다음 동일 종목 분석 시 실제 수익률 자동 fetch
 • '지난 추천' 라인이 요약 상단에 자동 표시
   예: 📒 지난 추천 (2026-04-24): 매수 → +5.3% (벤치 +1.2%)
 • 결정 LLM도 같은 컨텍스트 받음 → 자기 과거 실수 반영

━━━━━━━━━━━━━━
<b>【6. 캐시 & 비용】</b>
 • 오늘 같은 종목 재분석 → 디스크 캐시 즉시 반환 (무료)
 • 새 분석 ~$0.05~0.10/회 (Flash 분석가 + Pro 결정자)
 • /compare 둘 다 새거 → ~$0.10~0.20
 • 일일 캐시는 자정 KST 만료
 • 윈도우 데이터 (15년 가격)는 영구 캐시

━━━━━━━━━━━━━━
<b>【7. 안정성 (자동 처리)】</b>
 • 분석은 별도 subprocess → 봇 메인 절대 멈추지 않음
 • 10분 wall 타임아웃 자동 발동 (토큰 낭비 차단)
 • LLM HTTP 호출 150초 cap (단발 hang 방지)
 • 분석가 빈 응답 → 무도구 모드 1회 자동 재시도
 • watchdog 12분 stale → 자동 재시작
 • auto-update 2분마다 git pull + 무중단 재배포
 • 봇 재시작 중에도 진행 메시지 자동 복구

━━━━━━━━━━━━━━
<b>【8. 채널 알림】</b>
 • 🚀 배포 시작 / ✅ 배포 완료 — auto-update
 • ⚠️ 봇 hang 감지 — watchdog 발동
 • ❌ 분석 실패 — 타임아웃 / 토큰 한도 / API 에러

━━━━━━━━━━━━━━
<b>【9. 제한 / 트러블슈팅】</b>
 • 한 번에 분석 1건 (lock으로 직렬화)
 • /compare는 2종목까지, 같은 티커 두 번 거부
 • "캐시 결과를 찾을 수 없습니다" → 자정 지나 만료, 티커 재입력
 • "분석 실패: 10분 타임아웃" → 1~2분 후 재시도
   (Gemini 503 일시 장애일 수 있음)
 • "봇 재시작으로 중단" → 자동 복구 메시지, 다시 입력
 • 미국 외 거래소: 접미사 유지
   예: /TSM (미국 ADR), /005930.KS (삼성전자)

━━━━━━━━━━━━━━
<b>【10. 차별화 포인트】</b>
 • 페르소나 토론 — Buffett/Lynch vs Graham/Marks
 • 결정 3노드만 Pro — 비용 ~$0.05 추가로 결정 품질 점프
 • 메모리 피드백 — 자기 과거 추천 결과 학습
 • 리스크 지표 결정적 계산 (LLM 추측 X)
 • 섹터 ETF 상대 강도 (서브섹터 자동 매핑)
 • 거시 스냅샷 (학습 cutoff 메우기)

━━━━━━━━━━━━━━
<b>【11. 대시보드 (웹 아카이브)】</b>
🦉 분석 기록 영구 보관 — 자정 만료 X
 • URL: <a href="http://34.64.89.160:8081/">http://34.64.89.160:8081/</a>
 • PC / 폰 / 태블릿 어느 브라우저에서든 접속
 • 새 분석마다 자동 갱신, 인증 없이 즉시 열람
 • 다크 모드 자동 (시스템 설정 따름)

▸ 화면 상단: 통계 카드 4개
   📊 총 분석   누적 분석 건수 + 활동 기간 + 종목 수
   💰 누적 비용 30일 비용 (USD/KRW) + 모델별 분포
   ⏱ 평균 시간 분석 평균 소요 + 가장 자주 분석된 종목
   🎯 정확도   Buy/Sell 추천 방향 일치율 (5거래일 후 자동 채워짐)

▸ 화면 본문: 날짜별 아코디언
   - 최근일이 위, 클릭으로 펼치기/접기
   - 종목 카드: 🎯 판정 + 4명 stance + 시간 + 지난 추천 결과
   - 카드 클릭 → 상세 페이지 (요약 + 전체 리포트)

▸ 검색: 상단 검색창에 종목 입력 → 즉시 필터
   - 매칭 안 되는 날짜 자동 숨김 + 매칭 날짜 자동 펼침
   - URL <code>#ticker=NVDA</code>로 직접 링크 가능

▸ 데이터 경로
   - 분석 본문: <code>~/.tradingagents/archive/YYYY-MM-DD/{TICKER}.json</code>
   - 비용 로그: <code>~/.tradingagents/usage.jsonl</code>
   - 메모리: <code>~/.tradingagents/memory/trading_memory.md</code>
"""


async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """DM /start or /help — comprehensive bot usage guide."""
    await update.message.reply_text(_HELP_TEXT, parse_mode=ParseMode.HTML)


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

    lines = [
        "📊 <b>NOAH 봇 사용 현황</b> (KST)",
        "",
        "🔬 <b>분석 실행</b>",
        f"  • 오늘: {len(today_runs)}건  (새 {today_new}건 + 캐시 {today_cache}건)",
        f"  • 7일:  {len(week_runs)}건",
        f"  • 30일: {len(month_runs)}건",
        "",
        f"💰 <b>추정 비용</b> (Gemini API, ₩{fx}/$)",
        f"  • 오늘: {krw(today_cost)}  (${today_cost:.2f})",
        f"  • 7일:  {krw(week_cost)}  (${week_cost:.2f})",
        f"  • 30일: {krw(month_cost)}  (${month_cost:.2f})",
        "",
        "🤖 <b>모델별 (오늘)</b>",
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
    """Bot init: register the slash-command menu, then handle any orphan
    progress message left behind by a previous instance that died
    mid-analysis (otherwise it would stay 'VICR 분석 시작…' forever)."""
    # Populates the 'Menu' button beside the input area + the '/' typing
    # autocomplete in DMs. Dynamic per-ticker commands like /NVDA aren't
    # registered (the universe is too large) — Telegram still recognises
    # them as tappable commands when typed in plain text.
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "사용법 안내"),
            BotCommand("help", "사용법 안내"),
            BotCommand("usage", "사용량 / 비용 / 7일 차트"),
            BotCommand("compare", "두 종목 비교 (채널에서 사용)"),
        ])
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)

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

    log.info("bot starting — watching channels: %s", CHANNEL_CHAT_IDS or "auto-detect")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
