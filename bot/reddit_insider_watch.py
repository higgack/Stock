"""미국 레딧 게시물 분석 Watcher — t.me/insidertracking 채널 폴링.

t.me/insidertracking 의 "미국 레딧 게시물 분석" 제목 메시지만 우리 NOAH
채널로 forward + 대시보드 archive. 사용자 요청 2026-06-03.

설계:
  * 수집: Telethon userbot (사용자 본인 계정으로 그 채널을 '구독'). 봇은
    남의 채널 멤버가 될 수 없으므로 봇 API 불가 → user client API 사용.
  * 필터: 메시지 첫 줄(또는 본문 시작) 에 '미국 레딧 게시물 분석' 포함 여부.
  * 중복: seen-set (~/.tradingagents/reddit_insider_seen.json) — 채널
    message_id 저장. 첫 run 은 기존 메시지 seen 처리(폭주 방지) — blog
    watch 와 동일 패턴.
  * forward: 우리 NOAH 채널 (CHANNEL_CHAT_IDS) 에 원본 본문 그대로 push.
    LLM 가공 0, 비용 ₩0.
  * archive: ~/.tradingagents/reddit_insider_archive/YYYY-MM-DD/HHMMSS_
    {msg_id}.json — 본문/타임스탬프/채널 source 저장. 대시보드가 읽음.
  * 통합: regenerate_reddit_insider_index() 호출 → archive/reddit_insider.
    html 갱신 (Daily Byte 패턴).

사전 준비물 (.env):
  TG_USER_API_ID       — my.telegram.org 발급 (숫자)
  TG_USER_API_HASH     — my.telegram.org 발급 (32자 hex)
  TG_INSIDER_CHANNEL   — 'insidertracking' (또는 -100... 숫자 ID)
  TG_USER_SESSION      — 세션 파일 경로, 기본 ~/.tradingagents/reddit_user
  TELEGRAM_BOT_TOKEN   — 우리 NOAH 봇 토큰 (forward 용)
  CHANNEL_CHAT_IDS     — 우리 NOAH 채널 ID (comma-separated)

첫 실행:
  .venv/bin/python -m bot.reddit_insider_watch
  → 본인 전화번호 + Telegram 앱 받은 인증코드 입력 (1회). 이후 .session
    파일로 무인 운영.

systemd timer (deploy/reddit-insider-watch.timer): 5분 간격 폴링.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.reddit_insider_watch")

# ── 경로/상수 ──────────────────────────────────────────────────────────────
_HOME_DIR = Path.home() / ".tradingagents"
_SEEN_PATH = _HOME_DIR / "reddit_insider_seen.json"
_ARCHIVE_DIR = _HOME_DIR / "reddit_insider_archive"
_DEFAULT_SESSION = _HOME_DIR / "reddit_user"

# 필터: 제목/본문 첫 줄에 이 phrase 포함 시만 수집.
_TITLE_MATCH = "미국 레딧 게시물 분석"

# 1회 폴링 시 최근 N개 메시지만 스캔 (대량 backfill 방지).
_FETCH_LIMIT = 30

_KST = timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    return datetime.now(_KST)


# ── seen-set (중복 차단) ──────────────────────────────────────────────────
def _load_seen() -> dict:
    """{'initialized': bool, 'ids': [int...], 'last_msg_id': int}"""
    if not _SEEN_PATH.exists():
        return {"initialized": False, "ids": [], "last_msg_id": 0}
    try:
        return json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("seen 로드 실패 → 초기화: %s", exc)
        return {"initialized": False, "ids": [], "last_msg_id": 0}


def _save_seen(state: dict) -> None:
    try:
        _HOME_DIR.mkdir(parents=True, exist_ok=True)
        # ids 가 무한 누적되지 않게 최근 500개만 유지 (메시지 ID 가 단조
        # 증가하므로 last_msg_id 가 진짜 중복 차단 기준, ids 는 보조).
        ids = list(state.get("ids") or [])[-500:]
        state["ids"] = ids
        _SEEN_PATH.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("seen 저장 실패: %s", exc)


# ── 메시지 필터 ───────────────────────────────────────────────────────────
def _matches_filter(text: str) -> bool:
    """첫 줄(또는 본문 시작 부분)에 '미국 레딧 게시물 분석' 포함 시 True."""
    if not text:
        return False
    head = text.strip().split("\n", 1)[0]
    return _TITLE_MATCH in head


def _extract_short_title(text: str) -> str:
    """카드 제목용 — 본문 첫 줄 200자 cap. 원본 그대로."""
    if not text:
        return _TITLE_MATCH
    first = text.strip().split("\n", 1)[0]
    return first[:200]


def _strip_md_bold(text: str) -> str:
    """소스 채널의 '**bold**' 마크다운 마커 제거 (사용자 요청 2026-06-04).
    우리 forward 는 plain text 라 '**' 가 렌더 안 되고 그대로 노출됨 →
    제목/본문에서 '**' 만 제거 (내용·단일 '*'·'·' 불릿은 보존). forward +
    archive 양쪽에 동일 적용해 텔레그램·대시보드 표시 일관성 유지."""
    if not text:
        return text
    return text.replace("**", "")


# ── archive ───────────────────────────────────────────────────────────────
def _save_archive(msg_id: int, ts_iso: str, text: str) -> None:
    """ingest 저장 — 대시보드 + 향후 재참조용. body 는 원본 그대로 보관."""
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = _ARCHIVE_DIR / date_iso
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{now:%H%M%S}_{msg_id}.json"
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "_date": date_iso,
            "msg_id": int(msg_id),
            "source_ts": ts_iso,
            "title": _extract_short_title(text),
            "body": text,
            "source": "t.me/insidertracking",
        }
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        log.info("archive saved: %s", path.name)
    except Exception as exc:
        log.warning("archive write failed: %s", exc)


# ── Telegram push (우리 봇 → 우리 채널) ─────────────────────────────────
def _push_to_noah_channel(text: str) -> bool:
    """우리 NOAH 봇으로 우리 채널에 forward. sendMessage HTTP API 직접 호출
    (python-telegram-bot 의존 안 함 — Telethon 과 라이브러리 충돌 회피)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.environ.get("CHANNEL_CHAT_IDS", "")
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    if not token or not chat_ids:
        log.error("TELEGRAM_BOT_TOKEN / CHANNEL_CHAT_IDS 미설정")
        return False
    # Telegram 4096 char cap — split on blank lines.
    chunks: list[str] = []
    cap = 3900  # leave headroom
    cur = ""
    for para in text.split("\n\n"):
        candidate = (cur + "\n\n" + para) if cur else para
        if len(candidate.encode("utf-16-le")) // 2 > cap:
            if cur:
                chunks.append(cur)
            cur = para
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    if not chunks:
        chunks = [text[:cap]]

    base = f"https://api.telegram.org/bot{token}/sendMessage"
    ok_all = True
    for chat in chat_ids:
        for chunk in chunks:
            data = urllib.parse.urlencode({
                "chat_id": chat,
                "text": chunk,
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req = urllib.request.Request(base, data=data, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    if resp.status != 200:
                        log.warning("sendMessage HTTP %d chat=%s",
                                    resp.status, chat)
                        ok_all = False
            except Exception as exc:
                log.warning("sendMessage 실패 chat=%s: %s", chat, exc)
                ok_all = False
    return ok_all


# ── 메인 ──────────────────────────────────────────────────────────────────
async def _run_async() -> int:
    """Telethon 으로 채널 폴링 → 필터 → forward + archive."""
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            SessionPasswordNeededError, FloodWaitError,
        )
    except ImportError:
        log.error("telethon 미설치 — pip install telethon (또는 make install)")
        return 2

    api_id = os.environ.get("TG_USER_API_ID", "").strip()
    api_hash = os.environ.get("TG_USER_API_HASH", "").strip()
    channel = os.environ.get("TG_INSIDER_CHANNEL", "insidertracking").strip()
    session_path = os.environ.get(
        "TG_USER_SESSION", str(_DEFAULT_SESSION)).strip()
    if not api_id or not api_hash:
        log.error("TG_USER_API_ID / TG_USER_API_HASH 미설정 — "
                  "https://my.telegram.org 에서 발급 후 .env 추가")
        return 2
    try:
        api_id_int = int(api_id)
    except ValueError:
        log.error("TG_USER_API_ID 는 정수여야 함")
        return 2

    Path(session_path).parent.mkdir(parents=True, exist_ok=True)

    state = _load_seen()
    seen_ids = set(state.get("ids") or [])
    last_msg_id = int(state.get("last_msg_id") or 0)
    initialized = bool(state.get("initialized"))

    client = TelegramClient(session_path, api_id_int, api_hash)
    try:
        await client.start()  # 첫 실행 시 인증 코드 prompt (CLI)
    except SessionPasswordNeededError:
        log.error("2단계 인증 비밀번호 필요 — 첫 실행 시 직접 prompt 응답")
        return 2
    except Exception as exc:
        log.error("Telethon 클라이언트 시작 실패: %s", exc)
        return 2

    new_pushed = 0
    new_archived = 0
    try:
        # 최근 N개 메시지 (newest first)
        msgs = []
        try:
            async for msg in client.iter_messages(channel, limit=_FETCH_LIMIT):
                msgs.append(msg)
        except FloodWaitError as exc:
            log.warning("FloodWait %ss — 다음 폴링으로 연기", exc.seconds)
            return 0
        except Exception as exc:
            log.error("iter_messages 실패 (채널 %s): %s", channel, exc)
            return 1

        # 시간순(오래된→최근) 정렬해 순서대로 push (사용자가 시간 순으로
        # 보게 — newest_first 로 push 하면 채널에서 뒤집힘).
        msgs.sort(key=lambda m: m.id)

        # 첫 실행: 모두 seen 처리만 (폭주 방지) — blog_watch 와 동일 정책.
        if not initialized:
            log.info("첫 실행 → 기존 메시지 %d개 seen 처리(push 없음)", len(msgs))
            for m in msgs:
                seen_ids.add(int(m.id))
                if int(m.id) > last_msg_id:
                    last_msg_id = int(m.id)
            state.update({
                "initialized": True, "ids": sorted(seen_ids),
                "last_msg_id": last_msg_id,
            })
            _save_seen(state)
            return 0

        # 통상 폴링: 새 메시지만 필터 → forward + archive
        for m in msgs:
            mid = int(m.id)
            if mid in seen_ids or mid <= last_msg_id:
                continue
            text = _strip_md_bold((m.text or m.message or "").strip())
            # 모든 메시지 ID 를 seen 에 추가 (필터 통과 못한 것도) →
            # 다음 폴링에서 다시 검사 안 함.
            seen_ids.add(mid)
            if mid > last_msg_id:
                last_msg_id = mid
            if not _matches_filter(text):
                continue
            ts_iso = (m.date or _now_kst()).isoformat() if m.date else ""
            # archive 먼저 (실패해도 push 는 시도)
            _save_archive(mid, ts_iso, text)
            new_archived += 1
            if _push_to_noah_channel(text):
                new_pushed += 1

        state.update({
            "initialized": True, "ids": sorted(seen_ids),
            "last_msg_id": last_msg_id,
        })
        _save_seen(state)
    finally:
        await client.disconnect()

    log.info("reddit_insider: 신규 push %d / archive %d",
             new_pushed, new_archived)

    # 대시보드 페이지 갱신 (있을 때만 trigger)
    if new_archived > 0:
        try:
            from bot.dashboard import regenerate_reddit_insider_index
            regenerate_reddit_insider_index()
        except Exception as exc:
            log.warning("reddit_insider dashboard regen 실패: %s", exc)

    return 0


def run() -> int:
    """동기 진입점 (systemd / CLI 용)."""
    return asyncio.run(_run_async())


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO,
    )
    # .env 로드 (blog_watch / daily_kr_flow 패턴 mirror)
    try:
        from dotenv import load_dotenv
        repo_root = Path(__file__).resolve().parent.parent
        env_path = repo_root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass
    return run()


if __name__ == "__main__":
    sys.exit(main())
