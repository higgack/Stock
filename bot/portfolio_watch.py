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
  * 글(텍스트) 메시지는 🗞️ 보유종목 News 가 쓴다(사용자 2026-10-12 — Second Brain 이 학습하는 그
    채널을 NOAH 도 읽는다): 여기서 이미 받은 메시지의 글만 `bot.holdings_news.TG_INBOX` 에 남긴다 →
    텔레그램 요청 추가 0. 처음 한 번만 최근 `_TG_SEED_DAYS` 일치를 거슬러 담는다(`tg_seeded`).

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.portfolio_watch")

_HOME_DIR = Path.home() / ".tradingagents"
_SEEN_PATH = _HOME_DIR / "portfolio_watch_seen.json"
_DEFAULT_SESSION = _HOME_DIR / "reddit_user"  # reddit_insider 와 공유(추가 로그인 0)
_FETCH_LIMIT = 20          # 첫 run seed(기존 메시지 seen 처리)용 최근 N개
_NEW_MSG_CAP = 300         # 이후 폴링: last_msg_id 이후 새 메시지 상한(고트래픽 안전)
_ZIP_EXTS = (".zip", ".xlsx")
_TG_SEED_LIMIT = 300       # 글 수신함 첫 채움 — 최근 N개 중
_TG_SEED_DAYS = 3          # 이 날 수 안의 것만(보유종목 News 의 창은 최대 3일)


def _capture(msgs, channel: str) -> int:
    """글 메시지를 보유종목 News 수신함에 남긴다. 실패해도 자산 처리는 계속한다 — 다만 조용히 넘기지
    않는다(#12)."""
    try:
        from bot.holdings_news import tg_append, tg_record
        return tg_append([tg_record(m, channel) for m in msgs])
    except Exception as exc:
        log.warning("보유종목 News 수신함 기록 실패(%d건): %s", len(msgs), exc)
        return 0


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
    n_txt = 0
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
        prev_last = last_msg_id
        texts = []
        for m in msgs:
            mid = int(m.id)
            if mid in seen_ids or mid <= last_msg_id:
                continue
            seen_ids.add(mid)
            last_msg_id = max(last_msg_id, mid)
            if not _is_portfolio_doc(m):
                # 자산 zip/xlsx 가 아닌 메시지 — 글은 보유종목 News 수신함으로(Second Brain 과 별도로 같은
                # 채널을 읽는다, 사용자 2026-10-12). 사진·파일만 있는 메시지는 tg_record 가 거른다.
                texts.append(m)
                continue
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
                from bot.portfolio import ingest
                from bot.portfolio import (format_summary_text,
                                           NotBanksaladExport, EmptyHoldingsExport)
                model = ingest(bytes(data), password=pw)
            except EmptyHoldingsExport as exc:
                # 진짜 뱅샐 export 인데 보유종목 0건 — 부분/손상 export 로 기존
                # 데이터 덮어쓰기 거부됨(보존). RAG 비-자산 skip 과 달리 사용자가
                # 올린 진짜 자산 파일의 이상이라 알림(2026-06-26 사고 재발방지).
                log.warning("자산 export 0건 — 기존 보존 (%s): %s", fname, exc)
                _push_confirm(
                    "⚠️ 자산 export 보유종목 0건 — 기존 데이터 보존(덮어쓰기 거부). "
                    "뱅크샐러드에서 전체 export 를 다시 받아 보내주세요.")
                continue
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

        n_txt = _capture(texts, channel)
        if not state.get("tg_seeded"):
            # 첫 채움(1회) — 이 기능 전에 이미 '본 것' 으로 넘긴 최근 글. 실패해도 다시 시도하지 않는다
            # (FloodWait 를 매 폴링 두드리지 않게 — 놓친 것은 사흘 치 과거 글뿐이다).
            try:
                cut = datetime.now(timezone.utc) - timedelta(days=_TG_SEED_DAYS)
                old = []
                async for m in client.iter_messages(chan_arg, limit=_TG_SEED_LIMIT):
                    if int(m.id) <= prev_last and m.date and m.date >= cut and not _is_portfolio_doc(m):
                        old.append(m)
                n_txt += _capture(old, channel)
                log.info("보유종목 News 수신함 첫 채움: 최근 %d일 %d건", _TG_SEED_DAYS, len(old))
            except Exception as exc:
                log.warning("보유종목 News 수신함 첫 채움 실패(다시 시도하지 않음): %s", exc)
            state["tg_seeded"] = True
        state.update({"initialized": True, "ids": sorted(seen_ids), "last_msg_id": last_msg_id})
        _save_seen(state)
    finally:
        await client.disconnect()

    log.info("portfolio_watch: 처리 %d건 · 글 %d건 → 보유종목 News 수신함", processed, n_txt)
    return 0


def _rematch() -> None:
    """저장된 자산 기록의 미매칭 종목을 다시 매칭(일시 실패가 다음 업로드까지 남지 않게, 2026-10-16). 붙은 게
    있으면 자산 대시보드를 다시 그린다. 실패는 이 주기의 일만 잃는다."""
    try:
        from bot.portfolio import rematch_unmatched
        if rematch_unmatched():
            from bot.dashboard import regenerate_portfolio_index
            regenerate_portfolio_index()
    except Exception as exc:                                   # noqa: BLE001
        log.warning("portfolio_watch: 미매칭 재매칭 실패: %s", exc)


def run() -> int:
    """텔레그램 쪽 결과와 무관하게 재매칭은 돈다 — 세션 잠금·FloodWait 로 일찍 끝나는 주기에도(독립 리뷰 M3).
    업로드가 있었으면 그 업로드(ingest)가 이미 끝난 뒤다."""
    try:
        return asyncio.run(_run_async())
    finally:
        _rematch()


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
