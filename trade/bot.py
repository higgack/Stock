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

import html as _html

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from trade import hs_lookup, hs_map, ignored, operator, watchlist
from trade.parser import parse_caption

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

# Cached at startup via Application.post_init. Used to build the
# 'open bot DM' inline keyboard buttons we attach to channel /help
# and /watch hint messages so the operator can tap straight from
# the channel into a 1:1 chat with the bot.
_BOT_USERNAME: str | None = None


def _dm_keyboard() -> InlineKeyboardMarkup | None:
    """Inline keyboard with a single 'open bot chat' URL button.

    Returns None when the bot username hasn't been cached yet (e.g.
    if Application.post_init failed); the caller drops the keyboard
    and sends a plain text message.
    """
    if not _BOT_USERNAME:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🤖 봇과 1:1 채팅 열기 (/watch 등록)",
            url=f"https://t.me/{_BOT_USERNAME}",
        )
    ]])


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
헤더에 📊 현재 잠정/확정 기간 + 다음 발표 D-N + 오늘 활동 (신규/확정 도착/첫 등장 품목) + 🧪 미파싱 백로그 + 📦 관세청 핀 품목 비교 패널 (핀 있을 때만) 자동 표시

<b>3. BeOn 발표 사이클 (KST)</b>
• 매월 11일경 — 1-10일 잠정
• 매월 21일경 — 1-20일 잠정
• 익월 1일경 — 전월 전체 잠정
• 익월 15일경 — 전월 전체 확정 (관세청 공식)

<b>4. 세 가지 뷰</b>
• <b>품목별</b> — 품목당 1 섹션 (다중 variant 섹션엔 국가 mix 막대 자동)
• <b>회사별</b> — 회사당 1 섹션, 관련 (품목·지역) 미니카드
• <b>매트릭스</b> — 품목 × 국가 heatmap (셀 = 알림 수, 셀 클릭 시 최신 alert)

<b>5. 카드 정렬</b>
같은 섹션 안에서:
① 전국 (no country) ② 전국_&lt;국가&gt; ③ 시군구
각 단계 내 게시 최신순. 새 발표가 도착하면 그 dedup 키의 최신 카드로 자동 교체, 과거는 모달 history에 누적.

<b>6. 배지</b>
🟢 수출 · 🟠 수입 · 잠정 · 확정 · 합산
🟡 확정 D-N (잠정의 예상 확정일 카운트다운)
🔴 확정 D+N 지연 (예정일 초과)
🆕 NEW — 카드: 오늘 게시된 alert / 섹션 헤더: 7일 이내 첫 등장 품목·회사

<b>7. 부가 기능</b>
• 검색: 품목/회사/국가 부분일치 (회사명 정확 일치 시 회사 뷰 자동 좁힘)
• 칩 필터: 수출/수입, 잠정/확정
• 📥 CSV — 현재 필터 결과 풀필드 다운로드 (id·dedup_key·item·item_raw·title_kind·is_composite·composite_parts·region(s)·country(s)·stocks·stocks_meta·has_etc·period_*·expected_final_date·days_to_final·posted_at·ingested_at·commentary·parse_warnings·media_urls 절대경로)
• 모달 — 카드 클릭 시 같은 dedup 키 과거 발표 인라인 비교 (전번 확정 ↔ 이번 잠정 시각 비교)
  · 🔗 URL 복사 (#a/&lt;id&gt; 딥링크) · 🖼 이미지 저장
  · 합산 ↔ 개별 양방향 링크 (수산화칼륨+탄산칼륨 ↔ 각 개별)
  · 같은 품목 다른 회사 (peer chip — 클릭 시 회사 뷰 자동 필터)

<b>8. 명령어</b> (워치·ignore 명령은 봇과 <b>1:1 채팅(DM)</b>에서만 동작)
/help · /start — 이 안내 (채널·DM 둘 다)
/watch &lt;검색어&gt; — 품목+관련종목 둘 다 부분일치 시 DM (예: /watch 이오테크닉스)
/watch item &lt;검색어&gt; — 품목만 매칭하고 싶을 때 (예: /watch item 라면)
/watch company &lt;검색어&gt; — 관련종목만 매칭 (예: /watch company 삼양식품)
/watch list — 현재 워치 목록
/unwatch item|company &lt;검색어&gt; — 워치 제거
/ignore &lt;msg_id&gt; — 일일 미등록 검사에서 그 msg 제외 (일회성 공지 등)
/unignore &lt;msg_id&gt; — ignore 해제
/ignored — 현재 ignore 목록
/hs &lt;검색어&gt; — 한글/숫자(prefix) HS 검색 → 버튼 클릭으로 핀 (예: /hs 반도체, /hs 8542). 직접 등록도 가능: /hs &lt;품목&gt; &lt;HS코드&gt;
/unhs &lt;품목&gt; · /hslist — 핀 해제 / 핀 목록 (검색은 ~/.trade/hs_codes.xlsx 필요 — 관세청 15049722 파일 다운로드, 개정 시 덮어쓰기)
/customs — 핀 품목 관세청 월 금액 비교 (수출·전월비·무역수지). 대쉬보드 헤더 📦 패널과 동일
※ <b>[비온 인사이트]</b> · <b>DART 공시 릴레이</b>는 자동 skip (코드 상수)

<b>9. 자동화 systemd</b>
• trade-bot — 실시간 메시지 수집
• trade-bot-update (1분) — git pull + 재배포
• trade-bot-watchdog (1분) — 폴링 hang 감지 + 자동 재시작
• trade-bot-dashboard — HTTP 서버 (포트 8765, gzip on)
• trade-bot-dashboard-refresh (5분) — ingest → purge_ignored (filter 회귀 정리) → HTML 재생성
• trade-bot-health (1시간) — BeOn 발표 예정일 (11/21/익월1/익월15) 기준 사이클 누락 감지 → ⚠️ 알림 (이벤트 기반, 침묵 기간엔 silent)
• trade-bot-unstored-check (매일 00:00 KST) — inbox.jsonl에 있지만 store.db에 없는 alert 감지 → ⚠️ 알림 (없으면 silent) + 미파싱 캡션을 eval_misses.jsonl에 누적 (회귀 fixture용, 키별 1회)
• trade-bot-beon-listener (상시) — 새 BeOn 글 즉시 forward (앨범 3s debounce, 🟢 가동/⚠️ 실패)
• trade-bot-beon-sync (2시간마다) — listener 다운타임 대비 safety net (2일 룩백 + 200개 cap, 초과 시 ⚠️ abort)
• trade-bot-customs-fetch (매일 01:30 KST) — 핀된 품목의 관세청 월 확정 금액 수집(12개월, 알림 0) → 이어서 급변 평가: 새 달 수출 전월비 ±30% 초과 시 <b>운영자 DM</b> 1건 (첫 실행·신규 핀 baseline 무음, cap 10건·초과 시 요약, 채널 X). HS부호 파일(~/.trade/hs_codes.xlsx)이 180일 이상 묵으면 ⚠️ 개정 안내 DM (30일 lockout)
• trade-bot-backup (매일 03:00 KST) — store.db 일간 스냅샷 (최근 14일 보관)
신규/변경된 systemd unit은 auto-update이 install-trade-units.sh로 자동 cp + daemon-reload + enable (sudoers 1회 설정).

<b>10. API endpoints</b>
• /api/alerts.json — 전체 alert 덤프 (latest + history)
• /api/stats — 카운트 (수출/수입, 잠정/확정 등)
• /api/health — alert 수, 마지막 게시, 디스크 잔여, 대쉬보드 mtime + stale 초

<i>최종 갱신: 2026-05-31 — /hs xlsx 검색·버튼등록 (15049722) + HS부호 180일 묵음 시 개정 안내 DM · 관세청 급변 알림</i>
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
            reply_markup=_dm_keyboard(),
        )
        return
    if first_word in (
        "/watch", "/unwatch",
        "/ignore", "/unignore", "/ignored",
        "/hs", "/unhs", "/hslist", "/customs",
    ):
        # Per-user / per-operator state has no place in a channel post.
        # Reply once with the DM-only hint and a one-tap keyboard
        # button into the bot DM.
        await ctx.bot.send_message(
            chat_id=post.chat.id,
            text=(
                "⚠️ <b>워치 · ignore 명령은 봇과 1:1 채팅(DM)에서만 동작</b>합니다.\n"
                "아래 버튼을 누르면 봇과의 채팅이 열립니다 → <b>[START]</b> 누르고 "
                "<code>/watch 이오테크닉스</code> 또는 "
                "<code>/ignore 677</code> 같이 입력."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_dm_keyboard(),
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

    # Best-effort watchlist DM notify on the captioned (primary) member
    # of an album. Photo-only siblings carry no text so parse_caption
    # returns None and we skip them.
    caption_text = post.text or post.caption or ""
    if caption_text.strip():
        asyncio.create_task(_notify_watchers(ctx, caption_text))


# ---------------------------------------------------------------------
# Watchlist commands + DM notify
# ---------------------------------------------------------------------

_WATCH_USAGE = (
    "사용법:\n"
    "/watch &lt;검색어&gt; — 품목+관련종목 둘 다 매칭 (가장 편함)\n"
    "/watch item &lt;검색어&gt; — 품목명만\n"
    "/watch company &lt;검색어&gt; — 관련종목만\n"
    "/watch list — 현재 워치 목록\n"
    "/unwatch item|company &lt;검색어&gt; — 워치 제거\n"
    "검색은 부분일치 (대소문자 무시). 예: '라면'은 '라면 (전국)'·'라면 + 기타 소스'에 모두 매칭."
)


def _format_watch_list(watches: list[dict]) -> str:
    if not watches:
        return "📋 현재 워치 없음.\n\n" + _WATCH_USAGE
    by_kind: dict[str, list[str]] = {}
    for w in watches:
        by_kind.setdefault(w["kind"], []).append(w["pattern"])
    lines = ["📋 <b>현재 워치</b>"]
    for kind in ("item", "company"):
        if kind in by_kind:
            label = "품목" if kind == "item" else "회사"
            for p in by_kind[kind]:
                lines.append(f"• {label}: <code>{_html.escape(p)}</code>")
    lines.append("")
    lines.append(_WATCH_USAGE)
    return "\n".join(lines)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    args = list(ctx.args or [])
    if not args or args[0].lower() == "list":
        with watchlist.session() as conn:
            watches = watchlist.list_for(conn, user_id)
        await update.message.reply_text(
            _format_watch_list(watches), parse_mode=ParseMode.HTML
        )
        return
    sub = args[0].lower()
    if sub in ("item", "company"):
        if len(args) < 2:
            await update.message.reply_text(_WATCH_USAGE, parse_mode=ParseMode.HTML)
            return
        pattern = " ".join(args[1:]).strip()
        with watchlist.session() as conn:
            added = watchlist.add(conn, user_id, sub, pattern)
        label = "품목" if sub == "item" else "회사"
        if added:
            msg = f"✅ <b>{label}</b> 워치 추가: <code>{_html.escape(pattern)}</code>"
        else:
            msg = f"이미 등록됨: {label} <code>{_html.escape(pattern)}</code>"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return
    # No 'item' / 'company' keyword — treat the whole input as a
    # pattern and register it under BOTH kinds so '/watch 이오테크닉스'
    # matches whether 이오테크닉스 shows up as the item name or in
    # 관련종목. /unwatch can still narrow to one side later.
    pattern = " ".join(args).strip()
    with watchlist.session() as conn:
        added_item = watchlist.add(conn, user_id, "item", pattern)
        added_co = watchlist.add(conn, user_id, "company", pattern)
    flavors = []
    if added_item:
        flavors.append("품목")
    if added_co:
        flavors.append("회사")
    if not flavors:
        msg = f"이미 등록됨: <code>{_html.escape(pattern)}</code> (품목+회사 둘 다)"
    else:
        msg = (
            f"✅ 워치 추가: <code>{_html.escape(pattern)}</code> "
            f"({' · '.join(flavors)})\n"
            f"<i>품목 또는 관련종목 중 어디든 들어가면 DM</i>"
        )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    args = list(ctx.args or [])
    if len(args) < 2 or args[0].lower() not in ("item", "company"):
        await update.message.reply_text(
            "사용법: /unwatch item &lt;검색어&gt; 또는 /unwatch company &lt;검색어&gt;",
            parse_mode=ParseMode.HTML,
        )
        return
    sub = args[0].lower()
    pattern = " ".join(args[1:]).strip()
    with watchlist.session() as conn:
        removed = watchlist.remove(conn, user_id, sub, pattern)
    label = "품목" if sub == "item" else "회사"
    if removed:
        msg = f"🗑 <b>{label}</b> 워치 제거: <code>{_html.escape(pattern)}</code>"
    else:
        msg = f"못 찾음: {label} <code>{_html.escape(pattern)}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------
# Ignore-list commands (`/ignore`, `/unignore`, `/ignored`)
# Operator marks msg_ids that aren't export/import data (BeOn 인사이트
# promos etc.) so the daily 00:00 KST unstored-check stops listing
# them. inbox.jsonl and media files are never touched — reversal via
# /unignore is always safe.
# ---------------------------------------------------------------------

_IGNORE_USAGE = (
    "사용법:\n"
    "/ignore &lt;msg_id&gt; — 무관 자료(BeOn 인사이트 등) 일일 검사에서 제외\n"
    "/unignore &lt;msg_id&gt; — 복구\n"
    "/ignored — 현재 ignore 목록\n"
    "예: 일일 검사 알림에서 'msg=677' 보이면 /ignore 677"
)


def _format_ignored_list() -> str:
    ids = sorted(ignored.load())
    if not ids:
        return "📋 현재 ignore 목록: 0건\n\n" + _IGNORE_USAGE
    header = f"📋 현재 ignore 목록: <b>{len(ids)}</b>건"
    recent = ids[-10:]
    lines = [
        header,
        "최근 10건: " + ", ".join(f"<code>{x}</code>" for x in recent),
        "",
        _IGNORE_USAGE,
    ]
    return "\n".join(lines)


async def cmd_ignore(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    args = list(ctx.args or [])
    if not args or args[0].lower() == "list":
        await update.message.reply_text(
            _format_ignored_list(), parse_mode=ParseMode.HTML
        )
        return
    try:
        mid = int(args[0])
    except ValueError:
        await update.message.reply_text(
            f"<code>{_html.escape(args[0])}</code>는 숫자가 아님. "
            f"msg_id (정수) 형식으로 입력하세요.",
            parse_mode=ParseMode.HTML,
        )
        return
    added = ignored.add(mid)
    if added:
        msg = (
            f"✅ msg <code>{mid}</code> ignore — "
            f"다음 일일 검사부터 알림에서 제외"
        )
    else:
        msg = f"이미 ignore 됨: <code>{mid}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_unignore(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    args = list(ctx.args or [])
    if not args:
        await update.message.reply_text(
            _IGNORE_USAGE, parse_mode=ParseMode.HTML
        )
        return
    try:
        mid = int(args[0])
    except ValueError:
        await update.message.reply_text(
            f"<code>{_html.escape(args[0])}</code>는 숫자가 아님. "
            f"msg_id (정수) 형식으로 입력하세요.",
            parse_mode=ParseMode.HTML,
        )
        return
    removed = ignored.remove(mid)
    if removed:
        msg = f"🗑 msg <code>{mid}</code> ignore 해제 — 다음 ingest 사이클에 다시 처리"
    else:
        msg = f"ignore 목록에 없음: <code>{mid}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_ignored(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    await update.message.reply_text(
        _format_ignored_list(), parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------
# HS-code pin commands (`/hs`, `/unhs`, `/hslist`)
# Operator maps a BeOn 품목명 → 관세청 HS코드 so trade-bot-customs-fetch
# can pull official monthly figures for it. Opt-in: only pinned items are
# fetched. Mirrors the /ignore family (plain-file state, DM-only). See
# trade/hs_map.py.
# ---------------------------------------------------------------------

_HS_USAGE = (
    "사용법:\n"
    "/hs &lt;검색어&gt; — 한글 또는 숫자(prefix)로 HS코드 검색 → 버튼 클릭으로 핀\n"
    "  예: <code>/hs 반도체</code>, <code>/hs 메모리</code>, <code>/hs 8542</code>\n"
    "/hs &lt;품목&gt; &lt;HS코드&gt; — 직접 핀 (검색 없이)\n"
    "  예: <code>/hs 라면 1902301010</code>\n"
    "/unhs &lt;품목&gt; — 핀 해제\n"
    "/hslist — 현재 핀 목록\n"
    "HS코드는 2·4·6·10자리 숫자. 정확한 10자리가 가장 깔끔."
)

_HS_SEARCH_LIMIT = 20
_HS_CODES_DOWNLOAD_HINT = (
    "🔎 HS코드 검색 데이터가 없습니다.\n"
    "관세청 HS부호 파일(<a href=\"https://www.data.go.kr/data/15049722/fileData.do\">"
    "dataset 15049722</a>, .xlsx)을 받아 호스트의 "
    "<code>~/.trade/hs_codes.xlsx</code> 경로에 저장 후 다시 시도. "
    "(개정 시 같은 경로에 덮어쓰면 자동 반영)\n"
    "검색 없이 직접 등록은 가능: <code>/hs 라면 1902301010</code>"
)


def _format_hs_search(hits, total: int, query: str):
    """Build the search-result message + inline keyboard.

    Returns (text, InlineKeyboardMarkup). Truncates each row's 한글품목명 to
    keep the button label under Telegram's ~64-char hard limit and readable
    on mobile. callback_data = 'hs_pin:<10-digit>' — HS-code-only so it
    fits Telegram's 64-byte cap with margin (item name is re-looked-up at
    click time from the same authoritative CSV)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    head = f"🔎 <b>'{_html.escape(query)}'</b> 검색 결과 {total}건"
    if total > len(hits):
        head += f" — 상위 {len(hits)}개만 표시 (더 좁혀보세요)"
    head += "\n클릭하면 그 코드로 핀 등록됩니다."

    rows = []
    for h in hits:
        label = h.ko_name
        if len(label) > 32:
            label = label[:31] + "…"
        rows.append([InlineKeyboardButton(
            f"{label}  · {h.hs_code}",
            callback_data=f"hs_pin:{h.hs_code}",
        )])
    return head, InlineKeyboardMarkup(rows)


def _format_hs_list() -> str:
    pins = hs_map.entries()
    if not pins:
        return "📋 현재 HS 핀: 0건\n\n" + _HS_USAGE
    lines = [f"📋 <b>현재 HS 핀: {len(pins)}건</b>"]
    for item, code in pins:
        lines.append(f"• <code>{_html.escape(item)}</code> → <code>{code}</code>")
    lines.append("")
    lines.append(_HS_USAGE)
    return "\n".join(lines)


async def cmd_hs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    args = list(ctx.args or [])
    if not args or args[0].lower() == "list":
        await update.message.reply_text(
            _format_hs_list(), parse_mode=ParseMode.HTML
        )
        return

    # Two modes:
    #   1) /hs <품목> <HS코드>   — direct pin (last token is digits AND is a
    #      valid 2/4/6/10-digit HS code). Preserves the original UX so
    #      muscle memory keeps working.
    #   2) /hs <검색어>           — anything else: 한글 keyword, mixed text,
    #      or a numeric prefix (e.g. '8542'). Searches the reference CSV
    #      and returns inline buttons; clicking one pins via the callback.
    if len(args) >= 2 and hs_map.is_valid_hs(args[-1]):
        code = args[-1]
        item = " ".join(args[:-1]).strip()
        try:
            changed = hs_map.add(item, code)
        except ValueError:
            await update.message.reply_text(
                f"<code>{_html.escape(code)}</code>는 올바른 HS코드가 아님 "
                f"(2·4·6·10자리 숫자만). 예: <code>1902301010</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if changed:
            msg = (
                f"✅ 핀: <code>{_html.escape(item)}</code> → <code>{code}</code>\n"
                f"<i>다음 customs-fetch(매일 01:30 KST)부터 관세청 월 금액 수집</i>"
            )
        else:
            msg = f"이미 동일 핀: <code>{_html.escape(item)}</code> → <code>{code}</code>"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    # Search mode.
    query = " ".join(args).strip()
    try:
        hits, total = hs_lookup.search(query, limit=_HS_SEARCH_LIMIT)
    except hs_lookup.HsCodeFileMissing:
        await update.message.reply_text(
            _HS_CODES_DOWNLOAD_HINT,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    if not hits:
        await update.message.reply_text(
            f"🔎 '<b>{_html.escape(query)}</b>' 검색 결과 0건.\n"
            "다른 키워드(예: 메모리·DRAM·8542) 또는 직접 등록: "
            "<code>/hs 라면 1902301010</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    text, keyboard = _format_hs_search(hits, total, query)
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard,
    )


async def on_hs_pin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-keyboard click handler for hs_pin:<10-digit> buttons.

    Re-looks-up the Korean name from hs_lookup so the pin name is always
    the authoritative 한글품목명, never whatever was in the button label
    (which gets truncated for display). Acknowledges via callback_query
    first so Telegram clears the spinner."""
    q = update.callback_query
    if q is None or q.data is None:
        return
    await q.answer()
    if not q.data.startswith("hs_pin:"):
        return
    code = q.data.split(":", 1)[1]
    if not hs_map.is_valid_hs(code):
        await q.edit_message_text("⚠️ 잘못된 콜백 데이터.")
        return
    if update.effective_user is not None:
        operator.remember(update.effective_user.id)
    # Resolve the official name. If the CSV vanished between search and
    # click, fall back to the bare code so the pin still lands.
    name = code
    try:
        rows = hs_lookup.load()
        for r in rows:
            if r.hs_code == code:
                name = r.ko_name
                break
    except hs_lookup.HsCodeFileMissing:
        pass
    try:
        changed = hs_map.add(name, code)
    except ValueError:
        await q.edit_message_text(
            f"⚠️ 핀 실패: <code>{_html.escape(code)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if changed:
        body = (
            f"✅ 핀: <code>{_html.escape(name)}</code> → <code>{code}</code>\n"
            f"<i>다음 customs-fetch(매일 01:30 KST)부터 수집</i>"
        )
    else:
        body = (
            f"이미 동일 핀: <code>{_html.escape(name)}</code> → <code>{code}</code>"
        )
    await q.edit_message_text(body, parse_mode=ParseMode.HTML)


async def cmd_unhs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    args = list(ctx.args or [])
    if not args:
        await update.message.reply_text(_HS_USAGE, parse_mode=ParseMode.HTML)
        return
    item = " ".join(args).strip()
    removed = hs_map.remove(item)
    if removed:
        msg = f"🗑 핀 해제: <code>{_html.escape(item)}</code>"
    else:
        msg = f"핀 목록에 없음: <code>{_html.escape(item)}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_hslist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    await update.message.reply_text(
        _format_hs_list(), parse_mode=ParseMode.HTML
    )


def _format_customs() -> str:
    """Text comparison of pinned items' latest 관세청 monthly figures —
    the DM twin of the dashboard panel. Reads the same customs.db +
    hs_map, so DM and dashboard never drift."""
    from trade import customs

    pins = hs_map.entries()
    if not pins:
        return (
            "📦 핀된 품목 없음.\n\n핀을 추가하면 관세청 월 수출입금액을 비교합니다:\n"
            + _HS_USAGE
        )
    db = customs.DEFAULT_DB
    if not Path(db).exists():
        rows = [{"item": it, "hs_code": hs, "has_data": False} for it, hs in pins]
    else:
        with customs.session(db) as conn:
            rows = customs.summary_rows(conn, pins)

    latest = max(
        (r.get("year_month", "") for r in rows if r.get("has_data")),
        default="",
    )
    head = f"📦 <b>관세청 수출입</b> (핀 {len(rows)}개"
    head += f" · 최신 {latest})" if latest else ")"
    lines = [head, "<i>월 확정 금액(공식) · BeOn과 별개</i>", ""]
    for r in rows:
        item = _html.escape(r.get("item", ""))
        if not r.get("has_data"):
            lines.append(f"• {item} — <i>수집 대기</i>")
            continue
        lines.append(
            f"• <b>{item}</b>  수출 {customs.fmt_usd(r.get('exp_dlr'))} "
            f"({customs.fmt_pct(r.get('exp_mom'))})  "
            f"· 수입 {customs.fmt_usd(r.get('imp_dlr'))} "
            f"· 무역수지 {customs.fmt_usd(r.get('bal_payments'))}"
        )
    return "\n".join(lines)


async def cmd_customs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    await update.message.reply_text(
        _format_customs(), parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _notify_watchers(ctx: ContextTypes.DEFAULT_TYPE, caption_text: str) -> None:
    """Parse the caption with the live trade parser and DM every user
    whose watch pattern matches the resulting item/stocks. Failures
    swallowed so DM hiccups can't break the ingest hot path.
    """
    try:
        parsed = parse_caption(caption_text)
    except Exception as e:
        log.warning("watchlist parse failed: %s", e)
        return
    if parsed is None:
        return
    try:
        with watchlist.session() as conn:
            users = watchlist.matching_users(conn, parsed.item, parsed.stocks)
    except Exception as e:
        log.warning("watchlist lookup failed: %s", e)
        return
    if not users:
        return

    where = []
    if parsed.region:
        where.append(parsed.region)
    if parsed.country:
        where.append(parsed.country)
    where_str = " → ".join(where)
    dir_label = "수출" if parsed.direction == "export" else "수입"
    status_label = "잠정" if parsed.status == "preliminary" else "확정"
    title = _html.escape(parsed.item)
    if where_str:
        title += f" ({_html.escape(where_str)})"
    lines = [f"🔔 <b>{title}</b>", f"{dir_label} · {status_label}"]
    if parsed.stocks:
        lines.append(
            "관련종목: "
            + " · ".join(_html.escape(s) for s in parsed.stocks[:6])
            + (" 등" if parsed.has_etc or len(parsed.stocks) > 6 else "")
        )
    msg = "\n".join(lines)

    for uid in users:
        try:
            await ctx.bot.send_message(
                chat_id=uid, text=msg, parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.warning("watch DM failed user=%s err=%s", uid, e)


async def _post_init(app: Application) -> None:
    """Cache the bot's username once at startup so the inline-keyboard
    button on channel /help / /watch hints can point at
    https://t.me/<bot_username> without a getMe round-trip per reply.
    """
    global _BOT_USERNAME
    try:
        me = await app.bot.get_me()
        _BOT_USERNAME = me.username
        log.info("bot username cached: @%s", _BOT_USERNAME)
    except Exception as e:
        log.warning("could not fetch bot username at startup: %s", e)


def main() -> None:
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(_post_init)
        .build()
    )
    # Commands fire in private chats (DM the bot directly).
    app.add_handler(CommandHandler(["help", "start"], cmd_help))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("ignore", cmd_ignore))
    app.add_handler(CommandHandler("unignore", cmd_unignore))
    app.add_handler(CommandHandler("ignored", cmd_ignored))
    app.add_handler(CommandHandler("hs", cmd_hs))
    app.add_handler(CommandHandler("unhs", cmd_unhs))
    app.add_handler(CommandHandler("hslist", cmd_hslist))
    app.add_handler(CommandHandler("customs", cmd_customs))
    app.add_handler(CallbackQueryHandler(on_hs_pin_callback, pattern=r"^hs_pin:"))
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
