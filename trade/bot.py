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

from trade import cost, hs_lookup, hs_map, ignored, operator, watchlist
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
헤더에 📊 현재 잠정/확정 기간 + 다음 발표 D-N + 오늘 활동 + 🧪 미파싱 백로그 + 💰 비용·자원 + 📦 관세청 수출입 (📌내 핀 + 📈급등률·💵급증액 TOP30 <b>수출·수입 양방향</b> 발굴 + 🗄아카이브 무제한 수출·수입 각각) 자동 표시

<b>3. BeOn 발표 사이클 (KST)</b>
• 매월 11일경 — 1-10일 잠정
• 매월 21일경 — 1-20일 잠정
• 익월 1일경 — 전월 전체 잠정
• 익월 15일경 — 전월 전체 확정 (관세청 공식)

<b>4. 네 가지 뷰</b>
• <b>품목별</b> — 품목당 1 섹션 (다중 variant 섹션엔 국가 mix 막대 자동)
• <b>회사별</b> — 회사당 1 섹션(품목설명·제품명 제외), 관련 (품목·지역) 미니카드
• <b>매트릭스</b> — 품목 × 국가 heatmap (셀 = 알림 수, 셀 클릭 시 최신 alert)
• <b>📈 산업트렌드</b> — 20산업+하위(MTI6): 분류보드(초고성장/턴어라운드/부진·가속/둔화·📥수입모멘텀) + 품목 카드(수출·MA·YoY막대·TTM·8지표·해석·원자료) + 하위품목 TOP(급등률·급증액 MoM·수출≥2억) + 🔍추가신호 + 🚀🔄산업내부동학 + 🟢잠정속보(관세청10일단위·확정선행·capex·🔟10일모멘텀) + 🗄아카이브 + 🔗export. HSK-MTI 기준

<b>5. 카드 정렬</b>
같은 섹션 안에서:
① 전국 (no country) ② 전국_&lt;국가&gt; ③ 시군구
각 단계 내 게시 최신순. 새 발표가 도착하면 그 dedup 키의 최신 카드로 자동 교체, 과거는 모달 history에 누적.

<b>6. 배지</b>
🟢 수출 · 🟠 수입 · 잠정 · 확정 · 합산
🟡 확정 D-N (잠정의 예상 확정일 카운트다운)
🔴 확정 D+N 지연 (예정일 초과)
🆕 NEW — 7일내 게시/첫 등장 품목·회사

<b>7. 부가 기능</b>
• 검색: 품목/회사/국가 부분일치 (회사명 정확 일치 시 회사 뷰 자동 좁힘)
• 칩 필터: 수출/수입, 잠정/확정, 🆕 신규(최근 7일 게시 카드만)
• 📥 CSV — 탭별 풀데이터(대시보드가 담는 모든 정보): 신호알림=현재 필터 결과 풀필드(전필드+media_urls 절대경로) · 히트맵=품목×수출입 YoY·MoM 표 · 산업트렌드=품목(MTI) <b>요약</b>(품목당 1행·수출+수입 양방향 전체: 급등률 MoM%·급증액 MoMΔ$·YoY·12M MA·단가$/kg·구성HS·관련기업)+<b>월별 원시</b> 2파일 (엑셀 바로 열림)
• 모달 — 카드 클릭 시 같은 dedup 키 과거 발표 인라인 비교 (전번 확정 ↔ 이번 잠정)
  · 🔗 URL 복사 (#a/&lt;id&gt; 딥링크) · 🖼 이미지 저장
  · 합산 ↔ 개별 양방향 링크 (수산화칼륨+탄산칼륨 ↔ 각 개별)
  · 같은 품목 다른 회사 (peer chip — 클릭 시 회사 뷰 자동 필터)
  · 관련종목·회사헤더 시세 등락률(네이버 실시간; 장중 라이브·렌더 5분·장종료후 종가·미제공 공란)

<b>8. 명령어</b> (워치·ignore 명령은 봇과 <b>1:1 채팅(DM)</b>에서만 동작)
/help · /start — 이 안내 (채널·DM 둘 다)
/watch &lt;검색어&gt; — 품목+관련종목 둘 다 부분일치 시 DM (예: /watch 이오테크닉스)
/watch item &lt;검색어&gt; — 품목만 매칭하고 싶을 때 (예: /watch item 라면)
/watch company &lt;검색어&gt; — 관련종목만 매칭 (예: /watch company 삼양식품)
/watch list — 현재 워치 목록
/unwatch item|company &lt;검색어&gt; — 워치 1건 제거
/unwatch all — 내 워치 전체 해제 (알림 끄기)
/ignore &lt;msg_id&gt; — 일일 미등록 검사에서 그 msg 제외 (일회성 공지 등)
/unignore &lt;msg_id&gt; — ignore 해제
/ignored — 현재 ignore 목록
/hs &lt;검색어&gt; — 한글/숫자(prefix) HS 검색 → 버튼 클릭 즉시 핀(✅ 토글), 맨 아래 <b>완료</b> (예: /hs 반도체). 직접: /hs &lt;품목&gt; &lt;HS코드&gt;
/unhs &lt;품목&gt; · /hslist — 핀 해제 / 핀 목록 (검색은 ~/.trade/hs_codes.xlsx 필요 — 관세청 15049722 파일 다운로드, 개정 시 덮어쓰기)
/map &lt;표기&gt; &lt;코드&gt; — 시세 코드 등록(KIS확인) · /map 목록 · /unmap 해제
/customs — 관세청 수출입: 📌내 핀 + 📈급등률 TOP(≥$50M) + 💵급증액 TOP + 🗄아카이브 (수출·수입 양방향). 대쉬보드 📦 패널과 동일
/cost — 비용·자원(외부 API 무료·LLM·디스크·관세청 호출 추정)
/export — 산업트렌드 공유: 그 시점 월 스냅샷 파일 + 🔗공개 링크(인증 없음). 대쉬보드 🔗 링크(길게 눌러 복사)와 동일
※ <b>[비온 인사이트]</b> · <b>DART 공시 릴레이</b>는 자동 skip (코드 상수)

<b>9. 자동화 systemd</b>
• trade-bot — 실시간 메시지 수집
• trade-bot-update (1분) — git pull + 재배포
• trade-bot-watchdog (1분) — 폴링 hang 감지 + 자동 재시작
• trade-bot-dashboard — HTTP 서버 (포트 8765, gzip on)
• trade-bot-dashboard-refresh (5분) — ingest→purge→시세워밍→HTML→미매칭종목 일일 DM점검
• trade-bot-health (1시간) — BeOn 발표 예정일 (11/21/익월1/익월15) 기준 사이클 누락 감지 → ⚠️ 알림 (이벤트 기반, 침묵 기간엔 silent)
• trade-bot-unstored-check (매일 00:00 KST) — inbox.jsonl에 있지만 store.db에 없는 alert 감지 → ⚠️ 알림 (없으면 silent) + 미파싱 캡션을 eval_misses.jsonl에 누적 (회귀 fixture용, 키별 1회)
• trade-bot-beon-listener (상시) — 새 BeOn 글 즉시 forward (앨범 3s debounce, 🟢 가동/⚠️ 실패)
• trade-bot-beon-sync (2시간마다) — listener 다운타임 대비 safety net (2일룩백+200cap, 초과시 ⚠️abort)
• trade-bot-customs-fetch (1일 4회) — 전 chapter 스캔 → 📈급등률(+30%·≥$50M)·💵급증액 TOP30 (수출·수입 양방향) + 🗄아카이브, 신규진입 DM(첫스캔무음·cap10·수입 첫등장도 무음). 변동시 ✅갱신 DM
• trade-bot-backup (매일 03:00 KST) — store.db 일간 스냅샷 (최근 14일 보관)
신규/변경된 systemd unit은 auto-update이 install-trade-units.sh로 자동 cp + daemon-reload + enable (sudoers 1회 설정).

<b>10. API endpoints</b>
• /api/alerts.json — 전체 alert 덤프 (latest + history)
• /api/stats — 카운트 (수출/수입, 잠정/확정 등)
• /api/health — alert 수, 마지막 게시, 디스크 잔여, 대쉬보드 mtime + stale 초

<i>최종 갱신: 2026-06-06 — /map 응답에 KRX 종목명 표시 + 시세 캐시 원자쓰기</i>
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
        "/hs", "/unhs", "/hslist", "/customs", "/cost",
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
    "/unwatch item|company &lt;검색어&gt; — 워치 1건 제거\n"
    "/unwatch all — 내 워치 전체 해제 (알림 끄기)\n"
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
    # /unwatch all — 내 워치 전체 해제 (사용자 2026-06-12 '다 지워줘').
    if args and args[0].lower() == "all":
        with watchlist.session() as conn:
            n = watchlist.remove_all(conn, user_id)
        msg = (f"🗑 워치 <b>{n}</b>건 전체 해제 — 이제 알림이 오지 않습니다."
               if n else "해제할 워치가 없습니다 (이미 0건).")
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return
    if len(args) < 2 or args[0].lower() not in ("item", "company"):
        await update.message.reply_text(
            "사용법: /unwatch item &lt;검색어&gt; · /unwatch company &lt;검색어&gt; · "
            "/unwatch all (전체 해제)",
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


def _hs_search_keyboard(hits, pinned_codes: set[str]):
    """Inline keyboard for a search-result message.

    Each row is one HS-code button; clicking it toggles the pin (즉시
    등록/해제) and the keyboard is rebuilt by the callback. A trailing
    '완료' row closes the picker. Pinned codes get a ✅ prefix so the
    operator can see what's already in at a glance — the source of
    truth is hs_map.tsv, re-read each time the keyboard rebuilds, so
    pins/unpins made elsewhere stay consistent.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for h in hits:
        # h.label puts the most specific material first — a non-generic
        # ancestor (황산/인산/포토레지스트), else HS chapter, else nature —
        # so '반도체 제조용' rows become e.g. '황산 · 반도체 제조용'.
        label = h.label
        if len(label) > 38:
            label = label[:37] + "…"
        prefix = "✅ " if h.hs_code in pinned_codes else ""
        rows.append([InlineKeyboardButton(
            f"{prefix}{label} · {h.hs_code}",
            callback_data=f"hs_pin:{h.hs_code}",
        )])
    # Trailing 'done' row — closes the picker without un-doing anything
    # (toggling already wrote to hs_map). callback_data 'hs_done' has its
    # own handler / pattern so it doesn't collide with 'hs_pin:'. The
    # counter is informational, NOT a cap — the operator can keep
    # selecting; phrasing avoids '… 6개 핀' wording that mis-reads as a
    # 6-item limit.
    pin_n = sum(1 for h in hits if h.hs_code in pinned_codes)
    rows.append([InlineKeyboardButton(
        f"✓ 완료  ·  ✅ {pin_n}개 선택 (제한 없음)",
        callback_data="hs_done",
    )])
    return InlineKeyboardMarkup(rows)


def _format_hs_search(hits, total: int, query: str):
    """Build the search-result message + inline keyboard.

    Returns (text, InlineKeyboardMarkup). callback_data = 'hs_pin:<code>'
    so it fits Telegram's 64-byte cap with margin (item name is
    re-looked-up at click time from the same authoritative CSV)."""
    head = f"🔎 <b>'{_html.escape(query)}'</b> 검색 결과 {total}건"
    if total > len(hits):
        head += f" — 상위 {len(hits)}개만 표시 (더 좁혀보세요)"
    head += (
        "\n버튼을 누를 때마다 즉시 핀 등록/해제 (✅ = 핀됨). "
        "<b>선택 개수 제한 없음</b> — 원하는 만큼 누르고 맨 아래 <b>완료</b>."
    )
    pinned = {code for _item, code in hs_map.entries()}
    return head, _hs_search_keyboard(hits, pinned)


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


def _codes_from_keyboard(reply_markup) -> list[str]:
    """Recover the search hits' HS codes from a message's keyboard so we
    can rebuild it after a toggle WITHOUT re-running the search.
    `callback_data` carries the code as 'hs_pin:<code>' for every hit
    row; the trailing 'hs_done' row is skipped."""
    codes: list[str] = []
    if reply_markup is None:
        return codes
    for row in reply_markup.inline_keyboard:
        for btn in row:
            data = btn.callback_data or ""
            if data.startswith("hs_pin:"):
                c = "".join(ch for ch in data.split(":", 1)[1].strip() if ch.isdigit())
                if c:
                    codes.append(c)
    return codes


async def on_hs_pin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-keyboard click handler for hs_pin:<code> buttons.

    Toggles the pin (즉시 핀 등록/해제) and rebuilds the keyboard so the
    operator can keep selecting more items from the same message. The
    pin name uses hs_lookup's authoritative label (예: '황산 · 반도체
    제조용'), never the truncated button label.
    """
    q = update.callback_query
    if q is None or q.data is None:
        return
    await q.answer()
    if not q.data.startswith("hs_pin:"):
        return
    # Defensive parse: strip + digits-only. Belt-and-braces against any
    # stray character that ever sneaks into callback_data; the real fix
    # was widening is_valid_hs to 2~10 digits (02539b0).
    raw = q.data.split(":", 1)[1]
    code = "".join(ch for ch in raw.strip() if ch.isdigit())
    if not hs_map.is_valid_hs(code):
        log.warning("hs_pin callback rejected: raw=%r parsed=%r", q.data, code)
        await q.answer("잘못된 코드", show_alert=True)
        return
    if update.effective_user is not None:
        operator.remember(update.effective_user.id)

    # Resolve the rich label. Fall back to the bare code if the CSV
    # vanished between search and click (a pin still lands).
    name = code
    try:
        for r in hs_lookup.load():
            if r.hs_code == code:
                name = r.label
                break
    except hs_lookup.HsCodeFileMissing:
        pass

    # TOGGLE: if already pinned at this code, unpin; else pin. Find the
    # existing entry by code (not name) so the toggle is symmetric even
    # if labels differ between two release years.
    pinned_by_code = {c: it for it, c in hs_map.entries()}
    already = code in pinned_by_code
    try:
        if already:
            hs_map.remove(pinned_by_code[code])
            toast = f"해제: {code}"
        else:
            hs_map.add(name, code)
            toast = f"핀: {code}"
    except ValueError:
        await q.answer("핀 실패", show_alert=True)
        return

    # Toast on the spinner button so the operator gets confirmation
    # without losing the picker.
    try:
        await q.answer(toast)
    except Exception:
        pass

    # Rebuild the keyboard against the same hits + the now-updated pin
    # set so the toggled row visibly flips its ✅ marker.
    codes = _codes_from_keyboard(q.message.reply_markup if q.message else None)
    hits: list = []
    try:
        ref = hs_lookup.load()
        by_code = {r.hs_code: r for r in ref}
        hits = [by_code[c] for c in codes if c in by_code]
    except hs_lookup.HsCodeFileMissing:
        pass
    pinned_now = {c for _it, c in hs_map.entries()}
    try:
        await q.edit_message_reply_markup(
            reply_markup=_hs_search_keyboard(hits, pinned_now)
        )
    except Exception as exc:
        # 'Message is not modified' from Telegram when nothing visible
        # changed (e.g. the keyboard ended up identical) — ignore.
        log.debug("hs_pin keyboard edit skipped: %s", exc)


async def on_hs_done_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """'완료' button handler — closes the picker by replacing the message
    body with a summary of what landed in hs_map during this session.
    Pins themselves are already written (toggle handler did it), so this
    is purely a UI close + recap."""
    q = update.callback_query
    if q is None:
        return
    await q.answer()
    codes = _codes_from_keyboard(q.message.reply_markup if q.message else None)
    pinned_codes = {c for _it, c in hs_map.entries()}
    selected = [c for c in codes if c in pinned_codes]
    if not selected:
        body = (
            "✓ 완료 — 이번 화면에서 새로 핀한 항목 없음.\n"
            "<code>/hslist</code>로 전체 핀 목록 확인."
        )
    else:
        # Re-look-up labels for a tidy summary.
        labels: dict[str, str] = {}
        try:
            for r in hs_lookup.load():
                if r.hs_code in selected:
                    labels[r.hs_code] = r.label
        except hs_lookup.HsCodeFileMissing:
            pass
        lines = [f"✅ <b>핀 등록: {len(selected)}건</b>"]
        for c in selected:
            lab = labels.get(c, c)
            lines.append(f"• <code>{_html.escape(lab)}</code> → <code>{c}</code>")
        lines.append("")
        lines.append("<i>다음 customs-fetch(매일 01:30 KST)부터 관세청 월 금액 수집</i>")
        body = "\n".join(lines)
    try:
        await q.edit_message_text(body, parse_mode=ParseMode.HTML)
    except Exception as exc:
        log.debug("hs_done edit skipped: %s", exc)


async def on_beon_skip_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline 'beon_skip:<id,id,...>' 버튼 — 반복 재포워드되는 BeOn 메시지를
    탭 한 번으로 영구 차단(사용자 2026-06-15 '형식 안 맞는 거 두 시간마다 계속
    옴'). callback 의 BeOn 소스 msg_id 들을 beon_skip set 에 추가 → backfill_beon
    이 다음 동기화 틱부터 후보에서 제외 → 재포워드 루프 종료. 버튼 제거로 확인."""
    q = update.callback_query
    if q is None or q.data is None or not q.data.startswith("beon_skip:"):
        return
    raw = q.data.split(":", 1)[1]
    ids = [x.strip() for x in raw.split(",") if x.strip().isdigit()]
    if not ids:
        await q.answer("차단할 메시지가 없습니다", show_alert=True)
        return
    try:
        from trade import beon_skip
        total = beon_skip.add(ids)
        await q.answer(f"🚫 {len(ids)}건 차단 — 다시 받지 않습니다 (총 {total})")
        try:                                  # 버튼 제거 + 차단 표시
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    except Exception as exc:
        log.warning("beon_skip callback failed: %s", exc)
        try:
            await q.answer("차단 실패 — 다시 시도", show_alert=True)
        except Exception:
            pass


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


_MAP_USAGE = (
    "사용법: <code>/map &lt;관련종목 표기&gt; &lt;6자리코드&gt;</code>\n"
    "예: <code>/map 하나코스 226340</code> · 목록 <code>/map</code> · "
    "해제 <code>/unmap &lt;표기&gt;</code>"
)


async def cmd_map(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """관련종목 시세 코드 즉석 등록 — 대시보드에 안 뜨는 회사를 ssh·커밋 없이
    텔레그램에서 코드 매핑. KIS로 코드를 확인한 뒤 ~/.trade/stock_overrides.json에
    저장(resolve_codes가 _DIRECT_CODES보다 먼저 조회) → 다음 워밍부터 표시.
    인자 없거나 'list'면 현재 등록 목록."""
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    from trade import price_provider as pp
    args = list(ctx.args or [])
    if not args or args[0].lower() == "list":
        ov = pp.list_overrides()
        if not ov:
            await update.message.reply_text(
                "등록된 시세 오버라이드 없음.\n" + _MAP_USAGE,
                parse_mode=ParseMode.HTML)
            return
        lines = "\n".join(
            f"• <code>{_html.escape(k)}</code> → <code>{_html.escape(v)}</code>"
            for k, v in sorted(ov.items()))
        await update.message.reply_text(
            f"📌 <b>시세 코드 오버라이드 {len(ov)}개</b>\n{lines}\n"
            f"<i>해제: /unmap &lt;표기&gt;</i>", parse_mode=ParseMode.HTML)
        return
    if len(args) < 2:
        await update.message.reply_text(_MAP_USAGE, parse_mode=ParseMode.HTML)
        return
    name = " ".join(args[:-1]).strip()
    code = "".join(ch for ch in args[-1] if ch.isdigit())
    if len(code) != 6:
        await update.message.reply_text(
            f"<code>{_html.escape(args[-1])}</code>는 6자리 종목코드가 아님 "
            f"(예: <code>226340</code>).", parse_mode=ParseMode.HTML)
        return
    # KIS로 실제 시세 확인 — 오등록(틀린 코드) 방지. 블로킹 HTTP(urllib)라
    # to_thread로 — 이벤트루프를 막으면 BeOn 채널 실시간 수신까지 정지함.
    try:
        q = await asyncio.to_thread(pp.get_quote, code)
    except Exception:
        q = None
    if q is None:
        await update.message.reply_text(
            f"⚠️ <code>{code}</code> KIS 시세 없음 — 저장 안 함. "
            f"코드가 맞는지(상장·거래중) 확인해줘.", parse_mode=ParseMode.HTML)
        return
    pp.set_override(name, code)
    # 종목명: KIS가 이름을 안 주면(name==code) KRX 로컬 마스터 역조회 — 운영자가
    # '맞는 회사인지' 응답에서 바로 확인(오등록 방지의 핵심).
    nm = q.name if (q.name and q.name != code) else pp.krx_name_for(code)
    suffix = f" {_html.escape(nm)}" if nm else ""
    await update.message.reply_text(
        f"✅ 등록: <code>{_html.escape(name)}</code> → <code>{code}</code>"
        f"{suffix} · {q.price:,.0f}원\n"
        f"<i>다음 시세 워밍(≤5분)부터 대시보드 표시 · 해제 /unmap "
        f"{_html.escape(name)}</i>", parse_mode=ParseMode.HTML)


async def cmd_unmap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    from trade import price_provider as pp
    args = list(ctx.args or [])
    if not args:
        await update.message.reply_text(_MAP_USAGE, parse_mode=ParseMode.HTML)
        return
    name = " ".join(args).strip()
    if pp.remove_override(name):
        msg = f"🗑 시세 오버라이드 해제: <code>{_html.escape(name)}</code>"
    else:
        msg = f"오버라이드에 없음: <code>{_html.escape(name)}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


def _format_customs() -> str:
    """Body for the /customs DM — DM twin of the dashboard 관세청 패널.
    📌 내 핀(수동, 영구) + 📈 급등률 / 💵 급증액 (자동 발굴, 매일 갱신). Reads
    the same customs.db the dashboard panel uses so the two never drift."""
    from trade import customs, customs_scan

    db = customs.DEFAULT_DB
    parts: list[str] = []

    pins = hs_map.entries()
    if pins and db.exists():
        rows: list[dict] = []
        try:
            with customs.session(db) as conn:
                rows = customs.summary_rows(conn, pins)
        except Exception:
            rows = []
        pin_lines = []
        for r in rows:
            if not r.get("has_data"):
                pin_lines.append(f"• <b>{r['item']}</b>: 수집 대기")
                continue
            pin_lines.append(
                f"• <b>{r['item']}</b>  수출 {customs.fmt_usd(r.get('exp_dlr'))} "
                f"({customs.fmt_pct(r.get('exp_mom'))})  "
                f"· 수입 {customs.fmt_usd(r.get('imp_dlr'))} "
                f"· 무역수지 {customs.fmt_usd(r.get('bal_payments'))}"
            )
        if pin_lines:
            parts.append("📌 <b>내 핀</b> (%d개)" % len(rows))
            parts.extend(pin_lines)

    try:
        surge = customs_scan.render_surge_text(db)
    except Exception:
        surge = ""
    if surge:
        if parts:
            parts.append("")
        parts.append(surge)

    if not parts:
        return (
            "📦 관세청 수출입\n\n아직 수집된 데이터가 없습니다.\n"
            "매일 01:30 KST 전체 스캔 후 +30% 급변 품목이 자동 등록됩니다 "
            "(첫 스캔은 baseline 무음). 수동 추적은 /hs 로 품목을 핀하세요."
        )
    return "📦 <b>관세청 수출입</b>\n" + "\n".join(parts)


async def cmd_customs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    await update.message.reply_text(
        _format_customs(), parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_cost(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/cost — honest cost/resource view. Every external dep is free;
    shows that plus disk growth + customs API-quota headroom (the real
    things to watch). No dollar figures because there are none."""
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    await update.message.reply_text(
        cost.format_telegram(), parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/export — 산업트렌드 공유용 단일 HTML을 문서 파일로 전송. 받은 파일을
    지인에게 전달(forward)하거나 공유 시트로 카톡/메일로 넘기면 됨(랩탑 불필요).
    내용은 산업트렌드 데이터뿐이라 외부 공유 안전. LLM 0콜(저장 카드 재사용)."""
    if update.message is None or update.effective_user is None:
        return
    operator.remember(update.effective_user.id)
    from io import BytesIO

    html = None
    purl = None
    try:
        from trade import customs, industry_archive, insights
        with customs.session() as conn:
            try:
                cards = json.loads(insights.get_state(conn, "llm_insight_cards") or "[]")
            except Exception:
                cards = []
            cards = cards if isinstance(cards, list) else []
            html = industry_archive.render_export(conn, cards)
            ym = industry_archive.write_export(conn, cards)   # 월별 스냅샷 동결
            purl = industry_archive.public_share_url(ym)
    except Exception as exc:
        log.warning("/export failed: %s", exc)
    if not html:
        await update.message.reply_text(
            "아직 산업트렌드 데이터가 없습니다 — 다음 확정월(매월 ~15일) 유입 후 다시 시도하세요.")
        return
    bio = BytesIO(html.encode("utf-8"))
    bio.name = "한국수출_산업트렌드.html"
    # 공개 스냅샷 URL(인증 없음·그 시점 동결)이 있으면 같이 안내.
    extra = (f"\n\n🔗 또는 이 공개 링크(그 시점 스냅샷·인증 없음):\n{purl}"
             if purl else "")
    await update.message.reply_document(
        document=bio, filename="한국수출_산업트렌드.html",
        caption="📈 산업트렌드 공유용 — 이 파일을 지인에게 전달하면 그대로 열립니다"
                "(서버·인터넷·로그인 불필요)." + extra,
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
    # 히트맵 첫 스냅샷 즉시 kick (사용자 2026-06-12 '반영 너무 느려') —
    # 스냅샷이 비어 있을 때만 전 품목 스캔을 백그라운드 subprocess 로 1회
    # 실행해, 배포 직후 '다음 타이머(하루 4회, 최대 ~6h)' 대기를 제거.
    # 스냅샷이 한 번 채워지면 영구 no-op (평소 재시작 영향 0). 스캔은
    # 자체 분당-호흡/콜버짓 가드 보유 — bot 폴링과 무관한 별도 프로세스.
    try:
        from trade import customs, customs_scan
        with customs.session(customs.DEFAULT_DB) as conn:
            customs_scan.init_db(conn)
            empty = not customs_scan.load_heatmap(conn)
        if empty:
            # 중복 킥 가드 (사용자 2026-06-12 '아직 안된듯') — 13개월×97챕터
            # 자가스로틀 스윕은 수 분~수십 분. auto-update 가 잦으면 스캔이
            # 끝나기 전 재시작마다 heatmap 이 여전히 empty → 경쟁 스캔이 중복
            # 기동, API 분당한도/FloodWait 로 둘 다 coverage 미달 → 저장 0 →
            # heatmap 영원히 empty 로 고착. 마커 30분 가드로 단일 스캔 보장
            # (정상 완료분보다 길게; 실패 시 30분 후 자연 재시도).
            import time as _t
            mk = customs.DEFAULT_DB.parent / ".heatmap_kick.ts"
            recent = False
            try:
                recent = mk.exists() and (_t.time() - mk.stat().st_mtime) < 1800
            except Exception:
                recent = False
            if recent:
                log.info("heatmap empty but a scan was kicked <30m ago — "
                         "skip duplicate (in-flight 스윕 보호)")
            else:
                import subprocess
                import sys as _sys
                try:
                    mk.parent.mkdir(parents=True, exist_ok=True)
                    mk.write_text(str(_t.time()), encoding="utf-8")
                except Exception:
                    pass
                # 출력을 파일로 — DEVNULL 이면 스캔이 죽어도 흔적 0 이라
                # 'heatmap 영원히 empty' 원인 추적 불가 (사용자 2026-06-12
                # '하루종일 안 나옴'). tail ~/.trade/scan_customs_kick.log
                _klog_path = customs.DEFAULT_DB.parent / "scan_customs_kick.log"
                try:
                    _klog = open(_klog_path, "ab")
                except Exception:
                    _klog = subprocess.DEVNULL
                subprocess.Popen(
                    [_sys.executable, "-m", "trade.scripts.scan_customs"],
                    stdout=_klog, stderr=subprocess.STDOUT)
                log.info("heatmap snapshot empty — kicked one-off customs "
                         "scan (log: %s)", _klog_path)
    except Exception as e:
        log.warning("heatmap startup kick failed (timer will cover): %s", e)


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
    app.add_handler(CommandHandler("map", cmd_map))
    app.add_handler(CommandHandler("unmap", cmd_unmap))
    app.add_handler(CommandHandler("customs", cmd_customs))
    app.add_handler(CommandHandler("cost", cmd_cost))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CallbackQueryHandler(on_hs_pin_callback, pattern=r"^hs_pin:"))
    app.add_handler(CallbackQueryHandler(on_hs_done_callback, pattern=r"^hs_done$"))
    app.add_handler(CallbackQueryHandler(on_beon_skip_callback, pattern=r"^beon_skip:"))
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
