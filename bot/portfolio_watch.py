"""자산 watcher — 'Noah의 RAG' 채널의 뱅크샐러드 zip/xlsx 픽업 (자산관리 P1 증분4-b).

사용자 정책 2026-06-04: 봇 DM 에 직접 보내지 않고, 이미 운영 중인 **RAG 채널**
('Noah의 RAG' — 무엇이든 포워드하면 Second Brain 이 학습하는 그 채널)에 뱅크샐러드
export zip 을 올리면, 이 watcher 가 픽업해 파싱·대시보드 갱신한다. 봇 명령/DM
surface 는 건드리지 않음 → "봇은 깨끗하게".

설계 (reddit_insider_watch 패턴 미러):
  * 수집: Telethon **userbot**(사용자 본인 계정) — reddit_insider 와 동일 세션
    파일 재사용(TG_USER_SESSION) → **추가 로그인 0**. (oneshot·짧은 run 이라
    세션 sqlite lock 충돌은 드물고, 충돌 시 graceful 하게 다음 폴링 재시도.)
  * 대상: TG_RAG_CHANNEL 의 메시지 중 **.zip / .xlsx 문서 첨부**.
  * 처리: 다운로드 → bot.portfolio.ingest(bytes, BANKSALAD_ZIP_PW) →
    regenerate_portfolio_index() → NOAH 채널에 요약 1줄 confirm push.
  * 중복: seen-set(~/.tradingagents/portfolio_watch_seen.json, msg_id).
    첫 run 은 기존 seen 처리(폭주 방지) — reddit/blog 와 동일.
  * 비용 ₩0 (LLM 0, pykrx/yfinance 만).

.env:
  TG_USER_API_ID / TG_USER_API_HASH  — my.telegram.org (reddit_insider 와 공유)
  TG_USER_SESSION                    — 세션 경로 (기본 ~/.tradingagents/reddit_user, 공유)
  TG_RAG_CHANNEL                     — 'Noah의 RAG' 채널 @username 또는 -100…숫자 ID
  BANKSALAD_ZIP_PW                   — 뱅샐 export zip 비번
  TELEGRAM_BOT_TOKEN / CHANNEL_CHAT_IDS — confirm push 용 (이미 설정됨)

systemd: deploy/portfolio-watch.timer (2분 간격).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("bot.portfolio_watch")

_HOME_DIR = Path.home() / ".tradingagents"
_SEEN_PATH = _HOME_DIR / "portfolio_watch_seen.json"
_DEFAULT_SESSION = _HOME_DIR / "reddit_user"  # reddit_insider 와 공유(추가 로그인 0)
_FETCH_LIMIT = 20          # 첫 run seed(기존 메시지 seen 처리)용 최근 N개
_NEW_MSG_CAP = 300         # 이후 폴링: last_msg_id 이후 새 메시지 상한(고트래픽 안전)
_ZIP_EXTS = (".zip", ".xlsx")


def _load_seen() -> dict:
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
        state["ids"] = list(state.get("ids") or [])[-500:]
        _SEEN_PATH.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("seen 저장 실패: %s", exc)


def _doc_filename(msg) -> str:
    """메시지 첨부 문서의 파일명(소문자). 문서 없으면 ''."""
    try:
        if getattr(msg, "document", None) is None:
            return ""
        f = getattr(msg, "file", None)
        name = getattr(f, "name", None) if f else None
        if name:
            return name.lower()
        ext = getattr(f, "ext", "") if f else ""
        return ("file" + ext).lower() if ext else ""
    except Exception:
        return ""


def _is_portfolio_doc(msg) -> bool:
    return _doc_filename(msg).endswith(_ZIP_EXTS)


def _push_confirm(text: str) -> bool:
    """NOAH 채널에 confirm 1줄 push (Telethon 과 충돌 없게 HTTP API 직접)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.environ.get("CHANNEL_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chat_ids:
        log.warning("TELEGRAM_BOT_TOKEN / CHANNEL_CHAT_IDS 미설정 — confirm push skip")
        return False
    base = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for chat in chat_ids:
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text[:3900],
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(base, data=data, method="POST"), timeout=20
            ) as resp:
                ok = ok and (resp.status == 200)
        except Exception as exc:
            log.warning("confirm push 실패 chat=%s: %s", chat, exc)
            ok = False
    return ok


async def _run_async() -> int:
    try:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError, FloodWaitError
    except ImportError:
        log.error("telethon 미설치 — pip install telethon (또는 make install)")
        return 2

    api_id = os.environ.get("TG_USER_API_ID", "").strip()
    api_hash = os.environ.get("TG_USER_API_HASH", "").strip()
    channel = os.environ.get("TG_RAG_CHANNEL", "").strip()
    session_path = os.environ.get("TG_USER_SESSION", str(_DEFAULT_SESSION)).strip()
    if not api_id or not api_hash:
        log.error("TG_USER_API_ID / TG_USER_API_HASH 미설정")
        return 2
    if not channel:
        log.error("TG_RAG_CHANNEL 미설정 — 'Noah의 RAG' 채널 @username 또는 -100…ID")
        return 2
    try:
        api_id_int = int(api_id)
    except ValueError:
        log.error("TG_USER_API_ID 는 정수여야 함")
        return 2
    # 채널이 -100…숫자 ID 면 int 로 (Telethon 이 정확히 resolve).
    chan_arg = channel
    if channel.lstrip("-").isdigit():
        chan_arg = int(channel)
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)

    state = _load_seen()
    seen_ids = set(state.get("ids") or [])
    last_msg_id = int(state.get("last_msg_id") or 0)
    initialized = bool(state.get("initialized"))

    client = TelegramClient(session_path, api_id_int, api_hash)
    try:
        await client.start()
    except SessionPasswordNeededError:
        log.error("2단계 인증 비밀번호 필요 — 첫 실행 시 직접 prompt 응답")
        return 2
    except Exception as exc:
        # 세션 lock(reddit watcher 와 동시 실행) 등 → 다음 폴링 재시도.
        log.warning("Telethon 시작 실패(다음 폴링 재시도): %s", exc)
        return 0

    processed = 0
    try:
        msgs = []
        try:
            # 초기화 후엔 last_msg_id 이후 새 메시지를 min_id 로 전부(상한 _NEW_MSG_CAP)
            # 가져와 고트래픽 RAG 채널('뭐든지 포워드돼 쌓이는')에서도 폴링 사이 업로드를
            # 놓치지 않음 — 최근 N개 윈도 방식은 2분 내 N개 초과 유입 시 zip 을 흘림.
            # 첫 run 은 seed 라 최근 _FETCH_LIMIT 개만.
            if initialized and last_msg_id:
                it = client.iter_messages(chan_arg, min_id=last_msg_id, limit=_NEW_MSG_CAP)
            else:
                it = client.iter_messages(chan_arg, limit=_FETCH_LIMIT)
            async for m in it:
                msgs.append(m)
        except FloodWaitError as exc:
            log.warning("FloodWait %ss — 다음 폴링 연기", exc.seconds)
            return 0
        except Exception as exc:
            log.error("iter_messages 실패 (채널 %s): %s", channel, exc)
            return 1
        msgs.sort(key=lambda m: m.id)

        if not initialized:
            for m in msgs:
                seen_ids.add(int(m.id))
                last_msg_id = max(last_msg_id, int(m.id))
            state.update({"initialized": True, "ids": sorted(seen_ids), "last_msg_id": last_msg_id})
            _save_seen(state)
            log.info("첫 실행 → 기존 %d개 seen 처리(처리 없음)", len(msgs))
            return 0

        pw = os.environ.get("BANKSALAD_ZIP_PW") or None
        for m in msgs:
            mid = int(m.id)
            if mid in seen_ids or mid <= last_msg_id:
                continue
            seen_ids.add(mid)
            last_msg_id = max(last_msg_id, mid)
            if not _is_portfolio_doc(m):
                continue  # 자산 zip/xlsx 아닌 메시지는 무시(RAG 텍스트는 Second Brain 담당)
            fname = _doc_filename(m)
            log.info("자산 문서 감지: %s (msg %d)", fname, mid)
            try:
                data = await client.download_media(m, file=bytes)
            except Exception as exc:
                log.error("문서 다운로드 실패 (%s): %s", fname, exc)
                continue
            if not data:
                continue
            try:
                from bot.portfolio import ingest, format_summary_text, NotBanksaladExport
                model = ingest(bytes(data), password=pw)
            except NotBanksaladExport:
                # RAG 채널의 비-자산 .zip/.xlsx — 확장자만 같았을 뿐. 조용히
                # skip(푸시·저장 없음, portfolio.json 보존). seen 처리는 위에서
                # 이미 됐으므로 재시도 폭주도 없음.
                log.info("자산 export 아님 — RAG 문서로 무시: %s (msg %d)", fname, mid)
                continue
            except RuntimeError:
                # 비밀번호 zip 해제 실패 — 진짜 뱅샐 zip 인데 .env 비번이 stale 한
                # 실제 설정 신호라 유지(비-뱅샐 password zip 은 드묾).
                _push_confirm("🔒 자산 zip 비밀번호 오류 — .env BANKSALAD_ZIP_PW 확인 필요")
                continue
            except Exception as exc:
                # 손상/비-xlsx 등 일반 파싱 실패 — RAG 채널엔 임의 파일이 늘
                # 올라오므로 매번 '⚠️ 처리 실패' 푸시는 노이즈. 로그만 남기고 skip.
                log.info("자산 파싱 실패 — 무시 (%s): %s", fname, exc)
                continue
            try:
                from bot.dashboard import regenerate_portfolio_index
                regenerate_portfolio_index()
            except Exception as exc:
                log.warning("portfolio 대시보드 regen 실패: %s", exc)
            processed += 1
            _push_confirm("✅ 자산 업데이트 (RAG 채널 → 자산 대시보드)\n" + format_summary_text(model))

        state.update({"initialized": True, "ids": sorted(seen_ids), "last_msg_id": last_msg_id})
        _save_seen(state)
    finally:
        await client.disconnect()

    log.info("portfolio_watch: 처리 %d건", processed)
    return 0


def run() -> int:
    return asyncio.run(_run_async())


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO,
    )
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass
    return run()


if __name__ == "__main__":
    sys.exit(main())
