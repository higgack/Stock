"""Telethon sync: forward Badonions(나쁜양파, t.me/Badonions) posts into the
private trade channel so trade-bot can ingest them like live forwards.

미러 of backfill_beon.py (사용자 2026-07-10 — "일본이랑 똑같은 방식으로 대만
가져와줘"). 나쁜양파는 여러 나라의 월별 품목 수출통계(품목당 관련기업 여러
개 + 최신월/과거 히스토리 동봉)와 한국 종목별 수출 데이터를 텔레그램으로
발행 — 소스마다 별도 .db 로 적재되어 동명 .html 대시보드에 렌더된다.

⚠️ 어떤 소스가 있는지는 여기에 나열하지 않는다 — `trade/badonion_sources.py`
단일 레지스트리가 유일한 출처다(옛 docstring 은 국가 목록을 하드코딩해 뒀고,
소스가 늘 때마다 실제로 stale 해졌다).

⚠️ BeOn 과의 차이점(사용자 2026-07-11, 실백필 중 애널리스트 레이팅표가 trade
채널로 넘어간 걸 확인): 나쁜양파는 위 수출 데이터 외에 애널리스트
레이팅표·Capex 비교차트 등 무관한 트레이딩 정보도 섞어 올리는 일반 채널 —
BeOn 처럼 '전부 forward 후 다운스트림에서 파싱 실패분만 버림' 방식을 쓰면
무관 콘텐츠가 전부 private trade 채널에 그대로 쌓인다. 그래서 이 스크립트는
forward 시점에 `_is_relevant()`(= `badonion_sources` 레지스트리의 파서 전부
시도)로 수출 캡션인 unit(앨범이면 멤버 중 하나라도 매칭)만 골라 보낸다 —
BeOn 은 채널 자체가 단일 목적이라 이 필터가 없음. 소스 목록은
`trade/badonion_sources.py` 한 곳에만 있다(문구 드리프트 차단).

Works both as a one-off catch-up and as a periodic sync driven by
trade-bot-badonion-sync.timer. Idempotent — scans inbox.jsonl and skips
any Badonions message_id that's already been ingested, so re-running is
safe (picks up where it left off after a FloodWait abort, disk-low
pause, or accidental Ctrl+C).

Safety features (identical to backfill_beon.py — same shared TRADE_* env
knobs, same disk guard / adaptive pacing / FloodWait hard cap):
  - Adaptive pacing: starts at 1.5 s/unit and grows by 0.1 s every 500
    messages, capped at 3.0 s.
  - FloodWait hard cap: if Telegram asks for > 600 s wait, exit
    gracefully and notify, instead of sleeping for an hour inside the
    script. User reruns next day.
  - Disk guard: every 20 units, check free space on /. If below
    TRADE_MIN_FREE_GB (default 2.0), pause and notify, poll every 60 s
    for free space to recover, then notify resume.
  - Telegram notifications: pause / resume / abort all push to the
    same channel as trade-bot deploys (TRADE_BOT_TOKEN +
    TRADE_CHANNEL_CHAT_IDS).

Uses a SEPARATE Telethon session (.badonion-session) from the BeOn
pipeline's .backfill-session — both are independent, uncoordinated
Telethon clients writing into the same private trade channel; that's
fine, ingest_inbox.py dedupes and routes by parsed content, not by
source session.

Setup (one-time): reuses the existing .backfill-venv + the same
TRADE_TELETHON_API_ID/TRADE_TELETHON_API_HASH already in .env (no new
Telegram app credentials needed — same account, second session file).

Run manually (cd ~/stock-trade — the session file is cwd-relative):
  .backfill-venv/bin/python trade/scripts/backfill_badonion.py --since 2026-05-01
  .backfill-venv/bin/python trade/scripts/backfill_badonion.py  # default window: see below
  # optional: --to 2026-05-16, --dry-run, --lookback-days N

Default window (실수 #403): 3일. 단 관련성 필터(= 레지스트리 파서들) 코드의
지문이 마지막으로 **성공한 회수**가 남긴 기록과 다르면(또는 기록이 없으면)
40일을 **한 번** 훑어, 파서가 생기기 전에 리스너가 버린 캡션을 회수한다.
실행이 실패(rc≠0)하면 기록하지 않아 다음 틱이 다시 넓게 훑는다. 포워드가
**일부** 실패했거나 **도중에 중단된** 회수는 기록 대신 **재시도 표식**을 남기고,
다음 자동 동기화가 그 표식을 보고 최근 40일을 다시 훑는다 — 지문이 이미 기록돼
있어도(명시 회수가 일부 실패한 경우). 표식은 지문별 실행 수를 세어 3회째 실행에도
실패나 중단이 남으면 기록하고 멈춘다 — 그때 실패한·시도 못 한 원본 msg id 와
사람이 돌릴 명령을 경고·알림으로 말한다. 자동 회수는 연속 실패로 끊지 않고
끝까지 시도하며(5차 리뷰 — 끊으면 실패 덩어리 뒤의 유닛이 한 번도 시도되지 않은
채 포기됐다), 도중 중단은 긴 FloodWait 만 빼고 센다(기다리면 풀린다 — 표식만
남긴다, 4차 리뷰 M2). 아무것도 포워드하지 않은 시작 실패·상한 중단은 이 판정에
오지 않고, 프로세스가 죽은 실행도 못 와 셈도 표식도 남기지 않는다 — 포워드된
유닛은 inbox 로 들어가 다음 틱 후보에서 빠진다. 중단·완료 알림은 이 판정을 붙여
한 번 간다. 자동 회수는
한 번에 100유닛까지만 포워드한다(넘으면 파서가 너무 넓게 잡았을 수 있어 멈추고
알린다). `--since`·`--lookback-days`·`--to` 를 명시하면 그 창을 쓰고(둘 다
주면 `--since` 가 이긴다), 그 창이 지금까지 40일을 덮을 때만 성공 뒤 기록한다.
판정 로직은 `badonion_sources.sync_plan`·`finish_recovery`(telethon 없이
테스트된다, #176). 포워드할 유닛(파서는 받는데 아직 inbox 에 없음)은 dry-run
이든 아니든 머리를 찍는다 — 이상 없을 때의 평소 동기화는 그 줄이 0줄이다.
캡션 하나가 **어디로** 갔는지는 `--dry-run --since YYYY-MM-DD --find TEXT`
(원문·마크다운 둘 다, 대소문자 무시)가 갈래로 말하고, **왜** 버려졌는지(원문
repr·필터 판정)는 형제 `diagnose_badonion --since … --grep …` 이 찍는다.
dry-run 류(`--dry-run`·`--show-irrelevant`·`--find`)는 라이브 세션이 있으면
그 **복사본**으로 접속해 6시간 타이머와 잠금 경합하지 않는다(원본이 없거나
복사가 실패하면 라이브 경로로 접속하고 그렇다고 경고한다 — 그때는 경합을 못
피한다). 복사본은 인증이 풀렸으면 로그인하지 않고 멈춘다(그 로그인은 복사본과
함께 지워진다). 포워드하지 않으므로 후보 상한에서도 멈추지 않고, 시작에
실패해도 알림을 보내지 않는다(로그로만 — 폰의 '동기화 시작 실패' 는 타이머
장애로 읽힌다).

클라이언트는 `trade.tg_entities.guarded_client` 로만 만든다(실수 #404): 세션
파일 형식이 이 인터프리터의 telethon 과 안 맞으면 **생성 전에** 멈추고 무엇을
깔아야 하는지 알린다. 옛 판은 생성자가 `too many values to unpack` 으로 알림
경로 밖에서 죽었다.

재게시 글(나쁜양파가 다른 채널에서 퍼 온 글)은 포워드하기 **직전에** 원래
출처를 `trade.relay_origins` 에 보증한다(실수 #411) — 텔레그램은 포워드의
포워드에도 원래 출처를 달아, 보증이 없으면 봇의 출처 게이트가 그 글을 '다른
출처' 로 버린다(2026-09-25 27건 — kri 보드가 0개 회사였던 갈래). 보증을 못
쓰면 그 유닛은 포워드하지 않는다(포워드하면 봇이 버리고 다음 틱이 또
포워드한다) — 완료 알림이 그 수와 사유를 따로 말한다. `to-forward`·`find` 줄은
재게시 유닛이면 원래 출처를 같이 찍는다.

Run by systemd (trade-bot-badonion-sync.timer):
  Invoked without --since; uses the default window above — 3 days (realtime
  listener is the primary path, this is the downtime safety net), or 40 days
  once after the relevance-filter code changed.
"""

import argparse
import asyncio
import contextlib
import html
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon import utils as _tutils
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

# 직접 실행에서도 'trade' 패키지가 임포트되게 레포 루트를 path 에 추가
# (backfill_beon.py 와 동일 사유, 2026-06-15).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trade import badonion_sources as _srcs
from trade import ignored as _ignored
from trade import relay_origins as _relay


def _is_relevant(text: str) -> bool:
    """나쁜양파 채널의 무관 콘텐츠(애널리스트 레이팅표 등) 필터.

    소스 목록은 `trade/badonion_sources.py` 단일 레지스트리 — 리스너·
    백필·ingest·unstored_check·dashboard 가 모두 그걸 본다. 옛 구현은
    5개 파일에 같은 or-체인을 복붙해 뒀고, 한국 수출을 추가할 때 실제로
    로그 문구가 어긋났다(2026-08-16).
    """
    return _srcs.is_relevant(text)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("backfill_badonion")

API_ID = int(os.environ["TRADE_TELETHON_API_ID"])
API_HASH = os.environ["TRADE_TELETHON_API_HASH"]

_dest_raw = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
DEST_IDS = [int(x) for x in _dest_raw.split(",") if x.strip()]
if len(DEST_IDS) != 1:
    raise SystemExit(
        "backfill_badonion: TRADE_CHANNEL_CHAT_IDS must be a single chat ID"
    )
DEST_ID = DEST_IDS[0]

INBOX_DIR = Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))
INBOX_PATH = INBOX_DIR / "inbox.jsonl"

SOURCE_USERNAME = "Badonions"
SESSION_PATH = ".badonion-session"  # cwd-relative; .gitignored; separate from BeOn

# --- Adaptive pacing (same TRADE_* knobs as backfill_beon.py — shared,
# trade-wide safety tuning, not per-source) --------------------------
PAUSE_BASE_S = float(os.environ.get("TRADE_PAUSE_BASE_S") or "1.5")
PAUSE_INCREMENT_S = float(os.environ.get("TRADE_PAUSE_INCREMENT_S") or "0.1")
PAUSE_INCREMENT_EVERY = int(os.environ.get("TRADE_PAUSE_INCREMENT_EVERY") or "500")
PAUSE_MAX_S = float(os.environ.get("TRADE_PAUSE_MAX_S") or "3.0")

MAX_FLOOD_WAIT_S = int(os.environ.get("TRADE_MAX_FLOOD_WAIT_S") or "600")

# 대만 관세청 월간 발행은 품목 ~15개 내외(사용자 제공 스크린샷 기준)라 BeOn 대비
# 볼륨이 훨씬 작음 — 5000 cap 은 순수 안전장치(비정상 대량 스캔만 차단), 정상
# 월배치엔 절대 걸리지 않음. 같은 TRADE_MAX_CANDIDATES 를 공유(전역 안전 knob).
MAX_CANDIDATES_DEFAULT = int(os.environ.get("TRADE_MAX_CANDIDATES") or "5000")

# --- Disk guard (shared knobs) ----------------------------------------
MIN_FREE_GB = float(os.environ.get("TRADE_MIN_FREE_GB") or "2.0")
DISK_RESUME_BUFFER_GB = float(
    os.environ.get("TRADE_DISK_RESUME_BUFFER_GB") or "0.5"
)
DISK_CHECK_EVERY_UNITS = int(os.environ.get("TRADE_DISK_CHECK_EVERY") or "20")
DISK_POLL_SECONDS = 60

# 연속 forward 실패 cap — 개별 메시지 삭제/포워드불가는 드문드문 발생하는 게
# 정상이지만, 연속으로 여러 건 실패하면 세션/권한/네트워크 등 시스템 문제일
# 가능성이 높음 → 조용히 수천 건을 다 스킵하기 전에 크게 abort(2026-07-11).
MAX_CONSECUTIVE_FAILURES = int(
    os.environ.get("TRADE_MAX_CONSECUTIVE_FWD_FAILURES") or "5"
)


class BackfillAborted(Exception):
    """Raised when the script should exit gracefully so the operator
    can resume later. Always paired with a Telegram notify.

    `kind` 는 회수 재시도 판정이 읽는다(4차 리뷰 M2 — `badonion_sources.
    recovery_attempt_counts`): "flood" = 긴 FloodWait(기다리면 풀린다 — 횟수에
    세지 않는다) · "failures" = 연속 포워드 실패."""

    def __init__(self, msg: str, *, kind: str = "failures"):
        super().__init__(msg)
        self.kind = kind


def _dry_run_session(tmpdir: str) -> str:
    """dry-run(`--dry-run`·`--show-irrelevant`·`--find`)은 라이브 세션의
    **복사본**으로 접속한다(#403 2차 리뷰).

    `.badonion-session` 은 6시간 타이머가 쓰는 SQLite 라, 사람이 진단을 돌리는
    동안 타이머가 겹치면 한쪽이 `database is locked` 로 죽는다 — 형제
    `diagnose_badonion` 도 이 함수를 쓴다(#38).
    복사본은 auth key 를 그대로 가져, 인증된 세션이면 재로그인이 필요 없다.
    인증이 풀린 세션이면 `tg_entities.start_client` 가 복사본에 로그인하지 않고
    멈춘다(로그인이 복사본에 저장됐다가 실행 끝에 지워진다 — 3차 리뷰 L2).
    ⚠️ 원본이 없으면 라이브 경로를 쓴다(처음 로그인은 원본에 남아야 한다) —
    그리고 그 사실을 말한다. 복사가 실패해도 라이브로 돌아가되 말한다.
    이 두 경우엔 잠금 경합을 피하지 못한다."""
    src = Path(f"{SESSION_PATH}.session")
    if not src.exists():
        # 조용히 라이브 경로로 가면 '다른 디렉터리에서 돌렸다' 가 안 보인다 —
        # 세션 경로는 cwd 상대다(3차 리뷰 L3, 형제 diagnose 와 같은 규약 #38).
        log.warning("세션 파일 %s 없음(cwd=%s) — 라이브 경로로 접속한다(처음이면 "
                    "로그인 흐름을 타고, 그 로그인은 원본에 저장된다)",
                    src, Path.cwd())
        return SESSION_PATH
    dst = Path(tmpdir) / "dry-run.session"
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        log.warning("세션 복사 실패(%s: %s) — 라이브 세션으로 접속한다",
                    type(exc).__name__, exc)
        return SESSION_PATH
    log.info("세션 복사본으로 접속: %s → %s (원본 미변경 — 타이머와 잠금 경합 "
             "없음)", src, dst)
    return str(dst.with_suffix(""))


def _find_hay(msg) -> str:
    """`--find` 가 찾는 자리 — 파서가 받는 `text`(마크다운을 되붙인 것)와 서버
    원문 `raw_text` 둘 다, 대소문자 무시(형제 `diagnose_badonion._matches` 와
    같은 규약, #38). 마크다운 표식이 낱말을 가르면 `text` 에서만 찾을 때 놓친다."""
    return ((getattr(msg, "raw_text", None) or "") + "\n"
            + (getattr(msg, "text", None) or "")).lower()


# 회수를 포기할 때 알림에 싣는 msg id 상한 — 넘으면 수를 말한다(#45).
_UNFINISHED_SHOWN = 20


def _unfinished(failed: list, left: list) -> dict:
    """포워드 못 한 유닛의 원본 msg id — **실패한 것**과 **중단으로 시도 못 한
    것**을 가른다 — 과 둘을 통틀은 가장 이른 날짜(UTC). 회수를 포기할 때
    알림이 id 와 수동 명령을 댄다(4차 리뷰 M2: 옛 판은 '위 permanent forward
    failure 줄' 을 가리켰는데 FloodWait 중단엔 그 줄이 없었다). 둘을 한 목록에
    섞으면 한 번도 시도 안 한 유닛을 실패한 것처럼 읽는다(5차 리뷰 C)."""
    msgs = [m for u in list(failed) + list(left) for m in u]
    since = min((m.date for m in msgs), default=None)
    return {"failed_ids": [m.id for u in failed for m in u],
            "unattempted_ids": [m.id for u in left for m in u],
            "unfinished_since": since.strftime("%Y-%m-%d") if since else ""}


def _id_list(ids: list) -> str:
    """`N건: a, b, …` — 앞 `_UNFINISHED_SHOWN` 개만, 넘으면 나머지 수(#45)."""
    shown = ", ".join(str(i) for i in ids[:_UNFINISHED_SHOWN])
    more = (f" 외 {len(ids) - _UNFINISHED_SHOWN}건"
            if len(ids) > _UNFINISHED_SHOWN else "")
    return f"{len(ids)}건: {shown}{more}"


def _unfinished_text(stats: dict) -> str:
    """`_unfinished` → 사람이 읽는 한 줄(없으면 ""). 명령은 운영 유닛의
    ExecStart 인터프리터·cwd 그대로다(#371·#404 — 기억으로 적지 않는다)."""
    failed = list(stats.get("failed_ids") or [])
    left = list(stats.get("unattempted_ids") or [])
    if not failed and not left:
        return ""
    parts = []
    if failed:
        parts.append("포워드 실패한 원본 msg id " + _id_list(failed))
    if left:
        parts.append("중단으로 시도 못 한 원본 msg id " + _id_list(left))
    text = " · ".join(parts)
    since = str(stats.get("unfinished_since") or "")
    if since:
        text += (" — 사람이 다시 돌리려면: cd ~/stock-trade && "
                 ".backfill-venv/bin/python trade/scripts/backfill_badonion.py "
                 f"--since {since}")
    return text


def _notify(text: str) -> None:
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        return
    chat_id = chat_ids.split(",")[0].strip()
    if not chat_id:
        return
    try:
        subprocess.run(
            [
                "curl", "-s", "-m", "10",
                "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "--data-urlencode", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}",
                "--data-urlencode", "parse_mode=HTML",
            ],
            timeout=15,
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def _disk_free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


async def _maybe_pause_for_disk(forwarded: int, total: int) -> None:
    free = _disk_free_gb()
    if free >= MIN_FREE_GB:
        return

    log.warning(
        "disk free %.1fGB < %.1fGB threshold — pausing", free, MIN_FREE_GB
    )
    _notify(
        f"⏸ <b>나쁜양파 동기화 일시정지</b>\n"
        f"디스크 잔여: {free:.1f}GB (임계점 {MIN_FREE_GB:.1f}GB)\n"
        f"진행: {forwarded}/{total} msgs\n"
        f"정리하면 자동 재개: <code>bash trade/scripts/free_disk.sh</code>"
    )
    resume_threshold = MIN_FREE_GB + DISK_RESUME_BUFFER_GB
    while True:
        await asyncio.sleep(DISK_POLL_SECONDS)
        free = _disk_free_gb()
        log.info(
            "paused: disk free %.1fGB (need ≥ %.1fGB to resume)",
            free, resume_threshold,
        )
        if free >= resume_threshold:
            break
    _notify(
        f"▶ <b>나쁜양파 동기화 재개</b>\n"
        f"디스크 잔여: {free:.1f}GB\n"
        f"진행: {forwarded}/{total} msgs 부터 이어서"
    )
    log.info("resuming: disk free %.1fGB", free)


def _current_pause(forwarded: int) -> float:
    bumps = forwarded // PAUSE_INCREMENT_EVERY
    return min(PAUSE_BASE_S + bumps * PAUSE_INCREMENT_S, PAUSE_MAX_S)


def _load_existing_keys() -> set[tuple[int, int]]:
    """Collect (chat_id, msg_id) of every forward already represented in
    inbox.jsonl — source-agnostic (keyed on forward origin, not which
    upstream channel produced it), so this scan is shared verbatim with
    the BeOn pipeline. See backfill_beon.py's docstring for the native
    vs. forward vs. fallback origin-key rationale (identical here)."""
    if not INBOX_PATH.exists():
        return set()
    keys: set[tuple[int, int]] = set()
    with INBOX_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("forward_origin_chat_id")
            mid = rec.get("forward_origin_message_id")
            if cid is None or mid is None:
                continue
            try:
                keys.add((int(cid), int(mid)))
            except (TypeError, ValueError):
                continue
    return keys


_ORIGIN_NATIVE = "native"
_ORIGIN_FORWARD = "forward"
_ORIGIN_FALLBACK = "fallback"


def _msg_key_with_origin(
    msg: Message, source_chat_id: int
) -> tuple[tuple[int, int], str]:
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return (source_chat_id, msg.id), _ORIGIN_NATIVE
    from_peer = getattr(fwd, "from_id", None)
    channel_post = getattr(fwd, "channel_post", None)
    if from_peer is not None and channel_post is not None:
        try:
            return (
                (_tutils.get_peer_id(from_peer), int(channel_post)),
                _ORIGIN_FORWARD,
            )
        except (TypeError, ValueError):
            pass
    return (source_chat_id, msg.id), _ORIGIN_FALLBACK


def _unit_head(unit: list[Message], width: int = 160) -> tuple[str, str]:
    """(시각, 캡션 머리) — 진단 출력용. 앨범이면 캡션이 있는 첫 멤버, 없으면
    `(캡션 없음)`. 드랍된 유닛과 이미 포워드된 유닛이 **같은 모양**으로 찍혀야
    한 출력에서 대조된다(2026-09-10 TSMC 월매출 — 드랍 목록에 없어서 어디로
    갔는지 한 라운드를 더 썼다)."""
    first = next((m for m in unit if (m.text or "").strip()), None)
    head = " ".join((first.text or "").split())[:width] if first else "(캡션 없음)"
    when = (first or unit[0]).date.strftime("%Y-%m-%d %H:%M UTC")
    return when, head


def _repost_note(unit: list[Message]) -> str:
    """재게시 유닛이면 ` · 재게시 원래 출처 …` — `to-forward`·`find` 줄에 붙인다.

    봇은 재게시 글을 **원래 출처**로 받는다 — 어느 유닛이 보증을 타는지 dry-run 이
    미리 말해야, 실제 실행 뒤 봇 쪽 줄(`accepted … reason=relay_vouch`)과 대조된다
    (실수 #411). 보증할 수 없는 포워드(개인 계정 글 등)도 센다 — 봇이 버린다."""
    pairs, blind = _relay.repost_pairs(unit, _tutils.get_peer_id)
    note = f" · 재게시 원래 출처 {_relay.describe(pairs)}" if pairs else ""
    return note + (f" · 보증 못 하는 포워드 {blind}건(봇이 버린다)" if blind else "")


def _vouch_reposts(unit: list[Message]) -> str:
    """이 유닛의 재게시 글을 봇이 받도록 **포워드 전에** 보증한다 → 실패 사유("" = 성공
    또는 보증할 것 없음). 실수 #411.

    ⚠️ 실패하면 부르는 쪽이 그 유닛을 **포워드하지 않는다** — 포워드하면 봇이 원래
    출처로 받아 출처 게이트에서 버리고, inbox 에 없으니 다음 동기화가 같은 글을 또
    포워드해 또 버린다. 보증할 수 없는 포워드(원래 출처가 채널 글이 아님)는 막지
    않고 센다 — 그건 봇이 버리는 옛 동작 그대로다(`relay_origins` 못 보는 축)."""
    pairs, blind = _relay.repost_pairs(unit, _tutils.get_peer_id)
    ids = [m.id for m in unit]
    if blind:
        log.warning("재게시 %d건은 원래 출처가 채널 글이 아니라 보증할 수 없다 msgs=%s — 봇의 "
                    "출처 게이트가 버린다", blind, ids)
    if not pairs:
        return ""
    try:
        added = _relay.vouch(pairs, by="backfill", path=_relay.path_in(INBOX_DIR))
    except Exception as exc:                                   # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"[:200]
    log.info("vouched repost msgs=%s origin=%s (새로 %d건)", ids, _relay.describe(pairs), added)
    return ""


def _group_by_album(messages: list[Message]) -> list[list[Message]]:
    """Walk chronological messages and return a list of send-units:
    each unit is one standalone message OR one full album."""
    units: list[list[Message]] = []
    pending: list[Message] = []
    pending_gid: int | None = None
    for m in messages:
        gid = getattr(m, "grouped_id", None)
        if gid is None:
            if pending:
                units.append(pending)
                pending = []
                pending_gid = None
            units.append([m])
        elif gid == pending_gid:
            pending.append(m)
        else:
            if pending:
                units.append(pending)
            pending = [m]
            pending_gid = gid
    if pending:
        units.append(pending)
    return units


async def _forward_unit(client, source, unit: list[Message], dest) -> bool:
    msg_ids = [m.id for m in unit]
    delay = 0
    for attempt in range(5):
        if delay > 0:
            log.info("flood wait %ds before retry %d", delay, attempt + 1)
            await asyncio.sleep(delay)
        try:
            await client.forward_messages(dest, msg_ids, from_peer=source)
            return True
        except FloodWaitError as e:
            if e.seconds > MAX_FLOOD_WAIT_S:
                raise BackfillAborted(
                    f"Telegram FloodWait {e.seconds}s exceeds "
                    f"{MAX_FLOOD_WAIT_S}s threshold",
                    kind="flood",
                )
            delay = e.seconds + 1
        except Exception as e:
            # Permanent per-message failure (e.g. MessageIdInvalidError —
            # source message deleted/edited-to-service between candidate
            # scan and forward time). Not a FloodWait, retrying won't help
            # and re-raising would crash the entire multi-thousand-message
            # backfill over one bad message (2026-07-11, surfaced by a wide
            # Badonion catch-up run). Skip this unit, keep going.
            log.warning(
                "permanent forward failure msgs=%s (%s: %s) — skipping unit",
                msg_ids, type(e).__name__, e,
            )
            return False
    log.error("giving up on msgs=%s after 5 attempts", msg_ids)
    return False


async def run(
    since: datetime,
    until: datetime | None,
    dry_run: bool,
    max_candidates: int,
    *,
    show_irrelevant: bool = False,
    find: str | None = None,
    recovery: bool = False,
    stats: dict | None = None,
    session: str | None = None,
    defer_notify: bool = False,
) -> int:
    """`recovery` = 필터 지문이 바뀌어 **자동으로** 넓힌 창(#403) — 포워드 상한과
    알림 문구가 달라진다. `stats` 를 주면 포워드 결과를 채운다(호출부가 기록
    여부를 정한다 — 일부 실패한 회수는 기록하지 않는다). `session` 은 dry-run
    이면 복사본이다(`_dry_run_session`). 기본값은 **호출 시점**의 `SESSION_PATH`
    — 정의 시점에 굳히면 경로를 바꾼 테스트·호출이 옛 경로(cwd 의 운영
    파일)를 연다(3차 리뷰).

    `defer_notify`(`stats` 가 있을 때만) = 포워드를 끝냈거나 중단된 뒤의 알림을
    보내지 않고 `stats["note"]` 에 둔다 — 호출부가 회수 기록 판정(재시도 N/3 ·
    세지 않음 · 포기)을 붙여 **한 번** 보낸다(4차 리뷰 L6: 옛 판은 판정 전에
    '이 중단도 재시도 횟수에 센다' 를 약속했다)."""
    defer = defer_notify and stats is not None
    session = session or SESSION_PATH
    existing = _load_existing_keys()
    log.info("already ingested: %d forward keys", len(existing))

    # ⚠️ 생성도 이 try 안이다 — 세션 파일 형식이 이 telethon 과 안 맞으면
    # `TelegramClient(...)` **생성자**가 던지는데(1.36.0 이 v8 세션을 열면
    # `too many values to unpack`), 옛 판은 그게 try 밖이라 6시간 타이머가
    # 알림 없이 트레이스백으로 죽었다(실수 #404 · #12). 형식은 생성 전에
    # 재서 처방을 말한다(`guarded_client`). 복사본은 올라가도 무해하다.
    client = None
    try:
        from trade.tg_entities import guarded_client, start_client
        client = guarded_client(TelegramClient, session, API_ID, API_HASH,
                                live=(session == SESSION_PATH))
        # 복사본(dry-run)은 로그인하지 않는다 — 로그인이 복사본과 함께 지워진다
        # (3차 리뷰 L2). 형제 `diagnose_badonion` 과 같은 함수다(#38).
        await start_client(client, copy=(session != SESSION_PATH))
        log.info("Telethon session ready")
        # ⚠️ get_entity(username) 은 매 실행 ResolveUsernameRequest — 계정
        # 단위 강한 제한에 걸린다(FloodWait 8073s 실측 2026-08-27, #258).
        # resolve_peer = 세션 캐시 우선, 첫 1회만 네트워크 해석.
        from trade.tg_entities import resolve_peer
        source = await resolve_peer(client, SOURCE_USERNAME)
        dest = await resolve_peer(client, DEST_ID)
        source_chat_id = _tutils.get_peer_id(source)
    except Exception as exc:
        from trade.tg_entities import (prescribed_failure, startup_failure_note,
                                       startup_failure_text)
        # 우리가 처방을 담아 던진 예외(세션 형식 · 복사본 미인증)는 그 문장이
        # 곧 진단이다 — 트레이스백에 묻히면 무엇을 해야 하는지 안 보인다(4차
        # 리뷰 L5). 예상 못 한 예외만 트레이스백을 남긴다.
        if not prescribed_failure(exc):
            log.exception("session/access failure during startup")
        if dry_run:
            # 사람이 터미널에서 돌린 진단이다 — 로그로 충분하고, 폰에 '동기화 —
            # 시작 실패' 를 보내면 6시간 타이머의 장애로 읽힌다(#82). 후보 상한과
            # 같은 원리다(#403 2차 리뷰 P11·#264 진단은 운영 신호를 건드리지 않는다).
            log.error("dry-run 시작 실패 — 진단 실행이라 알리지 않는다: %s",
                      startup_failure_note(exc))
        else:
            if prescribed_failure(exc):
                log.error("session/access failure during startup: %s", exc)
            _notify("⚠️ <b>나쁜양파 동기화 — 시작 실패</b>\n"
                    + html.escape(startup_failure_text(exc)))
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        return 1

    try:
        log.info(
            "source(marked)=%s dest(marked)=%s",
            source_chat_id, _tutils.get_peer_id(dest),
        )

        candidates: list[Message] = []
        skipped_existing = 0
        skipped_ignored = 0
        fwd_fallback_count = 0
        iterated = 0
        existing_msgs: list[Message] = []
        ignored_msgs: list[Message] = []
        async for msg in client.iter_messages(
            source, offset_date=since, reverse=True
        ):
            if until is not None and msg.date > until:
                break
            iterated += 1
            key, origin = _msg_key_with_origin(msg, source_chat_id)
            is_fallback = (origin == _ORIGIN_FALLBACK)
            if is_fallback:
                fwd_fallback_count += 1
                log.warning(
                    "fwd fallback msg=%d — fwd_from present but "
                    "channel_post/from_id missing; keying on "
                    "(source, msg.id) — potential re-forward on next tick",
                    msg.id,
                )
            if key in existing:
                skipped_existing += 1
                if show_irrelevant or find:
                    existing_msgs.append(msg)
                continue
            caption = msg.text or ""
            if (
                _ignored.matches_prefix(caption)
                or _ignored.matches_contains(caption)
            ):
                skipped_ignored += 1
                log.info(
                    "skip ignored msg=%d caption=%r",
                    msg.id, caption[:60],
                )
                if find:
                    ignored_msgs.append(msg)
                continue
            candidates.append(msg)

        until_label = (until or datetime.now(timezone.utc)).date().isoformat()
        log.info(
            "range %s → %s — candidates=%d skipped_existing=%d "
            "skipped_ignored=%d fwd_fallback=%d",
            since.date().isoformat(),
            until_label,
            len(candidates),
            skipped_existing,
            skipped_ignored,
            fwd_fallback_count,
        )

        if len(candidates) > max_candidates and dry_run:
            # 상한은 **포워드 홍수**를 막는 장치다 — dry-run 은 포워드하지
            # 않으니 멈출 이유가 없고, 멈추면 넓은 창의 `--find`·`--show-
            # irrelevant` 가 아무것도 못 말한 채 읽기 전용 진단이 사람 폰에
            # '동기화 중단' 알림까지 보낸다(#403 2차 리뷰 P11·#264).
            log.warning(
                "candidates %d > max_candidates %d — dry-run 이라 멈추지 "
                "않는다(실제 실행이면 여기서 중단·알림)",
                len(candidates), max_candidates,
            )
        elif len(candidates) > max_candidates:
            log.error(
                "candidates %d > max_candidates %d — aborting to prevent flood",
                len(candidates), max_candidates,
            )
            _note = (
                f"⚠️ <b>나쁜양파 동기화 중단 (안전장치)</b>\n"
                f"후보 {len(candidates)}개가 cap {max_candidates}개 초과.\n"
                f"의도된 wide 백필이면 명시 실행:\n"
                f"<code>--since YYYY-MM-DD --max-candidates {len(candidates)+100}</code>"
            )
            if recovery:
                # 이 중단은 6시간마다 반복된다 — 무엇을 돌려야 멈추는지 적는다
                # (명시 창이 회수 창을 덮어야 기록된다, 독립 리뷰 L2·#171).
                # ⚠️ 그 명시 실행의 포워드가 **일부** 실패하거나 도중에 중단되면
                # 기록하지 않으므로 이 알림은 다시 온다 — '한 번이면 멈춘다' 고
                # 적으면 거짓이다(2차 리뷰). 이 상한 중단 자체는 재시도 횟수에
                # 세지 않는다 — 사람에게 묻는 장치라, 세면 3틱 뒤 사람 대신
                # 기록해 버린다. 도중 중단은 긴 FloodWait 만 빼고 센다(4·5차 리뷰).
                _note += (
                    f"\n자동 회수({_srcs.RECOVERY_LOOKBACK_DAYS}일) 중이다 — "
                    f"<code>--lookback-days {_srcs.RECOVERY_LOOKBACK_DAYS} "
                    f"--max-candidates {len(candidates)+100}</code> 로 돌리면 "
                    f"성공 뒤 기록돼 이 알림이 멈춘다(포워드가 일부 실패하거나 "
                    f"도중에 중단되면 기록하지 않아 다시 온다 — 긴 FloodWait "
                    f"중단을 뺀 그런 실행이 {_srcs.RECOVERY_MAX_ATTEMPTS}회째가 "
                    f"되면 기록된다)."
                )
            _notify(_note)
            return 2

        units_all = _group_by_album(candidates)
        log.info(
            "grouped into %d send units (singles + albums)", len(units_all)
        )

        # 관련성 필터 — 나쁜양파는 수출 데이터 외 콘텐츠도 섞인 일반 채널
        # (사용자 2026-07-11, 애널리스트 레이팅표가 trade 채널로 넘어간 걸
        # 확인). 앨범이면 멤버 중 하나라도 대상 캡션이면 유닛 전체(사진
        # 포함) 유지. 대상 소스 목록은 badonion_sources 레지스트리.
        units = [
            u for u in units_all
            if any(_is_relevant(m.text or "") for m in u)
        ]
        skipped_irrelevant = len(units_all) - len(units)
        total_msgs = sum(len(u) for u in units)
        # 소스 나열은 레지스트리에서 조립 — 하드코딩 문자열이 실제로
        # 어긋났었다(한국 추가 후에도 '한국' 이 안 나왔다, 2026-08-16).
        log.info(
            "relevance filter: %d/%d units are [%s] 데이터 "
            "(%d irrelevant skipped, %d candidate msgs total)",
            len(units), len(units_all), _srcs.labels(),
            skipped_irrelevant, len(candidates),
        )
        # ⚠️ 위 줄은 **레지스트리 전체**를 나열하므로 그 N건이 어느 소스인지
        # 말하지 않는다 — "한국 수입 회사별이 안 들어온다" 를 물어도 '채널에
        # 그런 글이 없었다' 와 '우리 파서가 떨어뜨렸다' 가 안 갈린다(#82).
        # 0건인 소스도 **이름을 대서** 말한다(#54 대조 0건은 침묵이 아니다).
        for line in _srcs.relevance_breakdown(units):
            log.info("%s", line)
        # 이 실행이 포워드할 유닛(= 파서는 받는데 아직 inbox 에 없음)은 dry-run
        # 이든 아니든 **머리를 찍는다**. 09-22 dry-run 은 이 갈래를 위 계수로만
        # 세어, 사용자가 이미 본 kri 캡션을 '원천 미게시' 로 오판하게 했다(#403).
        # 리스너가 실시간으로 받으므로 평소 동기화에선 0줄이다.
        for u in units:
            when, head = _unit_head(u)
            log.info("to-forward unit %s [%s]: %s%s", when,
                     _srcs.unit_labels(u), head, _repost_note(u))

        if skipped_irrelevant and not show_irrelevant:
            # 새 형식은 늘 '드랍된 쪽'에 숨는다(2026-09-10 TSMC 월매출 — 여섯
            # 번째 조용한 유실). 다음 라운드가 원문을 손으로 찾지 않게 안내.
            log.info("드랍된 캡션 머리를 보려면 --show-irrelevant")
        if show_irrelevant:
            kept = {id(u) for u in units}
            for u in units_all:
                if id(u) in kept:
                    continue
                when, head = _unit_head(u)
                log.info("irrelevant unit %s: %s", when, head)
            # 창 안인데 드랍 목록에 없는 글은 여기 있다 — '이미 inbox 에 있음'
            # 은 dest 채널에 도착했다는 뜻이고(리스너·이전 백필·수동 포워드
            # 어느 쪽이든), 저장 여부는 ingest 가 다음 주기에 정한다.
            for u in _group_by_album(existing_msgs):
                when, head = _unit_head(u)
                log.info("already-in-inbox unit %s: %s", when, head)
        if find:
            # 캡션 하나가 **어디로 갔는지** 갈래로 말한다(#403). 09-22 에 grep
            # 을 손으로 조립했다가 어순이 반대라 결정적인 줄을 걸렀다 — 반복되는
            # 확인은 제품에 심는다(#252). 머리 160자가 아니라 **전문**에서 찾는다.
            kept = {id(u) for u in units}
            groups = (("to-forward", units),
                      ("irrelevant", [u for u in units_all if id(u) not in kept]),
                      ("already-in-inbox", _group_by_album(existing_msgs)),
                      ("ignored", _group_by_album(ignored_msgs)))
            needle = find.lower()
            hits: dict = {}
            first_irrelevant = None
            for kind, group in groups:
                for u in group:
                    if any(needle in _find_hay(m) for m in u):
                        hits[kind] = hits.get(kind, 0) + 1
                        when, head = _unit_head(u)
                        if kind == "irrelevant" and first_irrelevant is None:
                            first_irrelevant = when[:10]
                        log.info("find %s unit %s [%s]: %s%s", kind, when,
                                 _srcs.unit_labels(u), head, _repost_note(u))
            if hits:
                # 갈래별 수를 한 줄로 — 여러 건이 걸리면 어디에 몰렸는지가 곧
                # 다음 수다(3차 리뷰 L4 — 세어 놓고 안 쓰던 계수).
                log.info("find: 갈래별 %s", " · ".join(
                    f"{k} {v}" for k, v in hits.items()))
            if not hits:
                # 대조 0건도 말한다(#54) — 창과 훑은 수를 같이 적어야 '창 밖'
                # 인지 '원천에 없음' 인지 사람이 가른다.
                log.info("find: %r 를 담은 글이 이 창(%s → %s, 메시지 %d개)에 없다",
                         find, since.date().isoformat(), until_label, iterated)
            if first_irrelevant:
                # 이 도구는 **어디로** 갔는지까지다 — **왜** 버려졌는지(원문 repr·
                # 변형 선택자 같은 바이트)는 형제 진단이 찍는다(#403 2차 리뷰, #38).
                # ⚠️ 창 시작일이 아니라 **첫 irrelevant 유닛의 날짜**부터 — 그
                # 도구는 --since 부터 --limit(기본 1000)개만 훑어, 넓은 창의
                # 시작일을 주면 그 캡션에 닿기 전에 멈출 수 있다. 찍은 명령이
                # 그 캡션에 못 닿으면 안내가 거짓이 된다(#371).
                log.info("find: irrelevant 가 왜 버려졌는지(원문 repr·필터 판정)는 "
                         "cd ~/stock-trade && .backfill-venv/bin/python -m "
                         "trade.scripts.diagnose_badonion --since %s --grep=%s",
                         first_irrelevant, shlex.quote(find))
        # 자동 회수의 포워드 상한(#403 L5) — 새 파서가 너무 넓게 잡으면 40일치
        # 무관 글이 비공개 채널에 한 번에 쏟아진다. 사람에게 묻고 멈춘다.
        over_cap = recovery and len(units) > _srcs.RECOVERY_MAX_UNITS
        if over_cap:
            log.error("recovery cap: 포워드할 유닛 %d개 > 상한 %d — 포워드하지 "
                      "않는다", len(units), _srcs.RECOVERY_MAX_UNITS)
        if dry_run:
            log.info("dry-run: not forwarding")
            return 0
        if over_cap:
            _notify(
                f"⚠️ <b>나쁜양파 자동 회수 중단 (안전장치)</b>\n"
                f"포워드할 유닛 {len(units)}개가 상한 "
                f"{_srcs.RECOVERY_MAX_UNITS}개 초과 — 새 파서가 너무 넓게 잡았을 "
                f"수 있다.\n"
                + html.escape(" / ".join(_srcs.relevance_breakdown(units)))
                + f"\n정상이면 <code>--lookback-days "
                  f"{_srcs.RECOVERY_LOOKBACK_DAYS}</code> 명시 실행(성공 뒤 "
                  f"기록돼 이 알림이 멈춘다)."
            )
            return 2

        forwarded_msgs = 0
        skipped_units = 0
        consecutive_failures = 0
        failed_units: list = []
        vouch_failed = 0                  # 재게시 보증을 못 써 포워드하지 않은 유닛(#411)
        vouch_err = ""
        i = 0
        try:
            for i, unit in enumerate(units, 1):
                if i == 1 or i % DISK_CHECK_EVERY_UNITS == 0:
                    await _maybe_pause_for_disk(forwarded_msgs, total_msgs)

                verr = _vouch_reposts(unit)
                if verr:
                    # 포워드하지 않는다 — 봇이 원래 출처로 받아 버린다(실수 #411)
                    ok = False
                    vouch_failed += 1
                    vouch_err = verr
                    log.error("재게시 출처 보증 실패 msgs=%s (%s) — 포워드하지 않는다(봇이 "
                              "버린다 · 다음 동기화가 다시 시도)", [m.id for m in unit], verr)
                else:
                    ok = await _forward_unit(client, source, unit, dest)
                if ok:
                    forwarded_msgs += len(unit)
                    consecutive_failures = 0
                else:
                    skipped_units += 1
                    consecutive_failures += 1
                    failed_units.append(unit)
                    # ⚠️ 자동 회수(`recovery`)는 여기서 끊지 않는다(5차 리뷰
                    # B·C). 유닛 상한(RECOVERY_MAX_UNITS) 안이라 아래 가드가
                    # 막으려는 '수천 건을 조용히 갈아 넘기기' 가 없고, 끊으면
                    # 실패 덩어리 뒤의 유닛은 한 번도 시도되지 않은 채 재시도
                    # 셈만 올라 포기됐다. 체계적 장애(쓰기 권한 등)는 '전체
                    # 실패' 완료 알림과 재시도 상한이 잡는다.
                    if (consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                            and not recovery):
                        # A per-message error (deleted/service message) is
                        # isolated and rare; a run of these back-to-back is
                        # a signal of something systemic (dest write access
                        # revoked, source/dest entity gone stale, etc.) —
                        # that must abort loudly, not get ground through as
                        # thousands of individually "skipped" units (2026-07-11
                        # review of the per-unit skip fix above).
                        raise BackfillAborted(
                            f"{consecutive_failures} consecutive forward "
                            f"failures — likely systemic (session/permission/"
                            f"network), not isolated per-message errors"
                            + (f" · 그중 재게시 출처 보증 실패 {vouch_failed}건"
                               f"({vouch_err})" if vouch_failed else "")
                        )
                if i % 20 == 0:
                    pace = _current_pause(forwarded_msgs)
                    log.info(
                        "progress: %d/%d units (%d msgs, pace %.1fs)",
                        i, len(units), forwarded_msgs, pace,
                    )
                await asyncio.sleep(_current_pause(forwarded_msgs))
        except BackfillAborted as exc:
            log.error("aborted: %s", exc)
            # 중단도 회수 시도다 — 호출부가 **세는지** 정한다(긴 FloodWait 만
            # 안 센다, 4·5차 리뷰). 자동 회수는 연속 실패로 끊지 않으므로 여기
            # 오는 자동 회수는 FloodWait 뿐이다. FloodWait 은 그 유닛(i번째)을
            # 시도하다 끊겼고, 연속 실패는 i번째가 실패로 이미 세어졌다 — 남은
            # 유닛의 시작이 다르다.
            left = units[i - 1:] if exc.kind == "flood" else units[i:]
            if stats is not None:
                stats.update(forwarded=forwarded_msgs,
                             skipped_units=skipped_units, aborted=str(exc),
                             abort_kind=exc.kind,
                             **_unfinished(failed_units, left))
            _note = (
                f"❌ <b>나쁜양파 백필 중단</b>\n"
                f"사유: {html.escape(str(exc))}\n"
                f"진행: {forwarded_msgs}/{total_msgs} msgs"
            )
            # 자동 회수면 '다음에 무엇이 일어나는지' 는 호출부의 판정(재시도
            # N/3 · 세지 않음 · 포기)이 말한다 — 여기서 약속하면 판정과 어긋난다
            # (4차 리뷰 L6). 사람이 명시한 창은 그 명령을 다시 돌리면 된다.
            if not (defer and recovery):
                _note += "\n같은 명령으로 재실행하면 이어서 진행 (idempotent)."
            if defer:
                stats["note"] = _note
            else:
                _notify(_note)
            return 1

        log.info(
            "done: forwarded %d of %d candidate messages (skipped_units=%d)",
            forwarded_msgs,
            total_msgs,
            skipped_units,
        )
        if stats is not None:
            stats.update(forwarded=forwarded_msgs, skipped_units=skipped_units,
                         **_unfinished(failed_units, []))
        if forwarded_msgs > 0 or skipped_units > 0:
            # skipped_units>0 도 notify 게이트 포함(2026-07-11 리뷰) — 전부 실패해도
            # forwarded_msgs=0 이라 조용히 "변경없음" 취급되던 걸 fix. 실패가
            # 반복되면 operator 가 로그 대신 이 알림으로 알아채야 함.
            _title = ("✅ <b>나쁜양파 동기화 완료</b>" if forwarded_msgs > 0
                      else "⚠️ <b>나쁜양파 동기화 — 전체 실패</b>")
            _note = (
                f"{_title}\n"
                f"신규 forwarded: {forwarded_msgs}/{total_msgs} msgs\n"
                f"units: {len(units)}"
            )
            if fwd_fallback_count:
                _note += f"\n⚠️ 출처 불명 {fwd_fallback_count}건 포함"
            if skipped_units - vouch_failed:
                # '영구' 를 단정하지 않는다 — 자동 회수는 이 알림 뒤에 '다시 훑는다
                # (N/3)' 판정을 붙여 한 통으로 보낸다(L6). '영구실패' 옆에 재시도를
                # 적으면 한 알림이 두 말을 한다(#165 · 2차 리뷰가 로그에서 뺀 단정).
                _note += (f"\n⚠️ 포워드 실패로 스킵된 unit {skipped_units - vouch_failed}건"
                          "(삭제·포워드 불가 등일 수 있다)")
            if vouch_failed:
                # 보증 실패는 처방이 다르다(#82) — 삭제된 글을 찾으러 가게 하지 않는다.
                _note += (f"\n❌ 재게시 출처 보증을 못 써 포워드하지 않은 unit {vouch_failed}건 "
                          f"— {html.escape(vouch_err)} (데이터 디렉터리 쓰기 확인 · 다음 "
                          "동기화가 다시 시도)")
            if defer:
                stats["note"] = _note
            else:
                _notify(_note)
        else:
            log.info(
                "sync: nothing new (iterated=%d skipped_existing=%d "
                "skipped_ignored=%d fwd_fallback=%d)",
                iterated, skipped_existing, skipped_ignored,
                fwd_fallback_count,
            )
        return 0
    finally:
        # 끊기가 던져도 run() 은 제 rc 로 끝나야 한다 — 새면 판정 뒤로 미룬
        # 알림(stats["note"])이 통째로 사라진다(5차 리뷰 E; 옛 판은 끊기 전에
        # 알렸다).
        try:
            await client.disconnect()
        except Exception as exc:                       # noqa: BLE001
            log.warning("disconnect 실패(%s: %s) — 무시한다",
                        type(exc).__name__, exc)


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill Badonions(나쁜양파) → private trade channel via Telethon."
    )
    ap.add_argument(
        "--since",
        default=None,
        help="YYYY-MM-DD (UTC), inclusive lower bound. Default: today minus --lookback-days.",
    )
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Days to look back when --since is omitted. Default: 3 "
            "(listener handles realtime; this is the safety net for short "
            "downtime windows) — or 40 once after the relevance-filter code "
            "changed, to recover captions dropped before their parser "
            "existed (실수 #403). An explicit N is used as given and "
            "records only if it covers those 40 days. For wider historical "
            "catch-ups use --since."
        ),
    )
    ap.add_argument(
        "--to", help="YYYY-MM-DD (UTC), inclusive upper bound. Default: now."
    )
    ap.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Abort with ⚠️ notify if candidates exceed N (default: "
            f"TRADE_MAX_CANDIDATES env = {MAX_CANDIDATES_DEFAULT}). "
            f"Set high for intentional wide catch-ups."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help=("enumerate candidates without forwarding — 라이브 세션이 있으면 그 "
              "복사본으로 접속하고(타이머와 잠금 경합 없음 · 인증이 풀렸으면 로그인"
              "하지 않고 멈춘다) 후보 상한에서도 멈추지 않는다"),
    )
    ap.add_argument(
        "--show-irrelevant",
        action="store_true",
        help=("관련성 필터가 드랍한 유닛과 이미 inbox 에 있는 유닛의 캡션 머리"
              "(160자)를 나란히 찍는다 — 새 카드 형식이 파서 없이 버려지고 있는지, "
              "창 안의 글이 어디로 갔는지 보는 용도. 셋째 갈래(포워드할 유닛)는 "
              "이 플래그 없이도 늘 찍힌다(#403). --dry-run 을 강제한다(포워드 0)"),
    )
    ap.add_argument(
        "--find",
        metavar="TEXT",
        default=None,
        help=("캡션 **전문**(원문·마크다운 둘 다, 대소문자 무시)에 TEXT 가 든 "
              "글이 창 안에서 어디로 갔는지 갈래로 찍는다: to-forward(파서가 "
              "받는데 아직 안 받아 옴 — 실제 실행이 회수) · irrelevant(어느 "
              "파서도 안 받음 — 왜인지는 diagnose_badonion --grep) · "
              "already-in-inbox(이미 받음 — 안 보이면 ingest 쪽) · ignored(무시 "
              "목록) · 없으면 그렇다고. 창은 --since 로 넓힐 것(안 주면 기본 창 "
              "— 평소 3일). --dry-run 을 강제한다(#403 — 손으로 조립한 grep 이 결정적인 "
              "줄을 걸렀다)"),
    )
    args = ap.parse_args()
    # 빈 `--find` 는 거부한다 — 옛 판은 `bool("")` 이 False 라 dry-run 을
    # 강제하지 않고 **실제 동기화**(포워드·기록)를 돌렸다(`--find "$X"` 에서 X 가
    # 비면 그렇게 된다, #403 2차 리뷰 P1). 빈 문자열은 모든 글과 맞으니 뜻도 없다.
    if args.find is not None and not args.find.strip():
        ap.error("--find 에 빈 값은 받지 않는다(찾을 글자를 줄 것) — 빈 값은 "
                 "모든 글과 맞고, 옛 판은 이걸 '안 줌' 으로 읽어 실제 동기화를 돌렸다")
    max_candidates = (
        args.max_candidates
        if args.max_candidates is not None
        else MAX_CANDIDATES_DEFAULT
    )

    # 진단 플래그는 운영 상태를 바꾸면 안 된다(#264·#283) — 드랍 목록을 보려던
    # 실행이 포워드까지 하면 '읽기 전용' 이 거짓이 된다(독립 리뷰 2026-09-10 #1).
    # ⚠️ 한 식으로 둔다 — 중간 변수로 가르면 형제 계약(`test_tw_monthly_revenue`
    # 의 AST 검사: 이 대입이 show_irrelevant 를 직접 본다)이 깨진다(실측).
    dry_run = bool(args.dry_run or args.show_irrelevant or args.find is not None)

    # 창은 레지스트리가 정한다 — 필터 지문이 바뀌었으면 한 번 넓게(#403).
    # 판정을 여기 두면 telethon 없는 환경에서 회귀가 통째로 스킵된다(#176).
    state_path = INBOX_DIR / _srcs.SYNC_STATE_NAME
    # 빈 문자열은 '안 줌' 이다 — 옛 판은 `if args.since:` 로 기본 창에 떨어졌고,
    # 명시로 읽으면 창이 None 이 돼 TypeError 로 죽는다(독립 리뷰 L1).
    since_arg = args.since or None
    to_arg = args.to or None
    plan = _srcs.sync_plan(state_path, since=since_arg,
                           lookback_days=args.lookback_days, to=to_arg)
    reason = plan["reason"]
    if plan["record"] and dry_run:
        # 사유가 '성공하면 기록한다' 로 끝나도 dry-run 은 기록하지 않는다 —
        # 로그가 안 할 일을 약속하지 않게(#403 2차 리뷰 P12·#55).
        reason += " (dry-run 이라 포워드·기록하지 않는다)"
    if since_arg:
        since_date = _parse_date(since_arg)
        # 사유는 두 갈래 모두 찍는다 — 명시 실행이 기록하는지(= 6시간마다 반복되던
        # 회수가 멈추는지) 운영자가 알아야 한다(독립 리뷰 L2).
        log.info("--since %s — %s", since_arg, reason)
    else:
        since_date = datetime.now(timezone.utc) - timedelta(
            days=plan["days"]
        )
        log.info(
            "--since not given; using %d-day lookback → %s — %s",
            plan["days"],
            since_date.date().isoformat(),
            reason,
        )

    if dry_run and not args.dry_run:
        log.info("--show-irrelevant/--find: dry-run 강제(포워드하지 않음)")
    stats: dict = {}
    with contextlib.ExitStack() as stack:
        session = SESSION_PATH
        if dry_run:
            session = _dry_run_session(stack.enter_context(
                tempfile.TemporaryDirectory(prefix="badonion-dry-run-")))
        rc = asyncio.run(
            run(
                since_date,
                _parse_date(to_arg) if to_arg else None,
                dry_run,
                max_candidates,
                show_irrelevant=args.show_irrelevant,
                find=args.find,
                recovery=bool(plan.get("recovery")),
                stats=stats,
                session=session,
                defer_notify=bool(plan["record"] and not dry_run),
            )
        )
    # 기록은 **성공한 실제 실행**만 한다 — dry-run 은 운영 상태를 바꾸면 안
    # 되고(#264·#283), 실패한 회수를 기록하면 다 된 줄 알고 다시 안 훑는다.
    # 포워드가 일부 실패했으면 기록하지 않고 재시도한다 — 일시 장애도 같은
    # 경로로 오기 때문이다(상한은 레지스트리가 정한다, 독립 리뷰 M1).
    # 포워드 도중 **중단된** 회수는 긴 FloodWait 만 빼고 센다(4·5차 리뷰 —
    # FloodWait 을 세면 제한 창 하나에 세 번 재실행해 캡션 하나 시도하지 않고
    # 포기한다). 자동 회수는 연속 실패로 끊지 않는다(run() 주석). 시작 실패(rc
    # 1, stats 비어 있음)는 아무것도 시도하지 않았으니 부르지 않는다. 도중에 죽은
    # 실행(systemd 타임아웃 kill)은 여기 못 와 셈도 표식도 남기지 않는다.
    aborted = str(stats.get("aborted") or "")
    # run() 이 판정 뒤로 미룬 알림(중단·완료) — 판정을 붙여 한 번 보낸다(L6).
    note = str(stats.get("note") or "")
    if plan["record"] and not dry_run and (rc == 0 or aborted):
        failed = int(stats.get("skipped_units") or 0)
        try:
            done, why = _srcs.finish_recovery(
                state_path, plan["fp"], failed_units=failed, aborted=aborted,
                abort_kind=str(stats.get("abort_kind") or ""),
                left=_unfinished_text(stats))
            # 포기는 조용히 넘기지 않는다 — 포워드 못 한 캡션을 더는 자동으로
            # 훑지 않는다는 뜻이다(4차 리뷰 M2: 옛 판은 이걸 info 로 찍었다).
            # 포기는 실패·중단이 있을 때만이고 그때 run() 은 늘 알림을 미뤄
            # 뒀다(완료 알림은 실패가 있으면 간다) — 알림 없는 포기는 없다.
            gave_up = done and bool(failed or aborted)
            (log.info if done and not gave_up else log.warning)("%s", why)
            if note:
                note += "\n" + html.escape(why)
        except Exception as exc:                         # noqa: BLE001
            # 포워드는 이미 끝났거나 중단됐다 — 기록 실패 하나로
            # 트레이스백으로 끝내지 않는다. OSError 만 잡으면 깨진 상태
            # 파일의 ValueError 가 새 매 틱 같은 자리에서 죽었다(2차 리뷰 P6).
            # 대가는 다음 틱이 회수 창을 한 번 더 쓰는 것뿐이고, 조용히
            # 넘기지는 않는다(#12).
            # 다음 창은 **남아 있는 기록**이 정한다 — 기록 전이면 회수 창을 다시
            # 쓰지만, 지문이 이미 기록돼 있고 표식이 없으면 기본 창이다. '한 번
            # 더 쓴다' 로 단정하면 두 번째 갈래에서 거짓이다(5차 리뷰 F).
            after = (f"다음 동기화 창은 남은 기록대로 — 지문 기록 전이면 "
                     f"{_srcs.RECOVERY_LOOKBACK_DAYS}일을 다시, 이미 기록된 지문이면 "
                     f"기본 {_srcs.DEFAULT_LOOKBACK_DAYS}일")
            log.warning("관련성 필터 지문 기록 실패(%s: %s) — %s",
                        type(exc).__name__, exc, after)
            # 미룬 알림은 판정 없이 가게 된다 — 판정을 못 붙인 이유를 그 자리에
            # 적는다(#43: 빠진 줄은 '아직 모른다' 가 아니라 '실패했다' 다).
            if note:
                note += ("\n⚠️ 회수 판정을 기록하지 못했다("
                         + html.escape(type(exc).__name__) + ") — "
                         + html.escape(after))
    if note:
        _notify(note)
    sys.exit(rc)


if __name__ == "__main__":
    main()
