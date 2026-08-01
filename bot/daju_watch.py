"""DAJU(다주) 실적 예정 알림 수집기 — Telethon 리스너 + 아카이브.

흐름 (사용자 2026-08-01):
  @daju_017_bot 이 사용자 계정으로 보낸 "3영업일 후 실적 발표 예정" 알림
    → Telethon 리스너(사용자 계정)가 수신
    → `daju_parse.parse_daju` 로 구조화
    → JSON 아카이브(~/.tradingagents/daju_archive/YYYY-MM-DD/<msg_id>.json)
    → NOAH 채널에 한 줄 요약 push + blog.html 상단 섹션 재생성

왜 Telethon 인가: DAJU 는 **봇 DM** 으로 오므로 우리 봇이 직접 읽을 수 없다.
이미 이 레포가 쓰는 방식(trade/scripts/listen_beon.py·listen_badonion.py)과
같은 패턴 — 사용자 계정 세션으로 수신해 우리 쪽으로 넘긴다.

**LLM 0 → 비용 ₩0** (원문 파싱만, 요약·재해석 없음). 출처가 "데이터 기반
추정이며 투자 권유가 아닙니다" 를 명시하므로 우리 화면도 그 고지를 그대로 노출.

설정(대부분 기존 값 재사용):
  TRADE_TELETHON_API_ID / TRADE_TELETHON_API_HASH  — 기존 리스너와 동일
  DAJU_SOURCE   — 수신 대상(기본 "daju_017_bot")
  DAJU_SESSION  — 세션 파일 경로(기본 ".daju-listener-session", 다른 리스너와
                  **반드시 별도** — 상시 프로세스가 세션을 공유하면 락 경합)

최초 1회 대화형 인증(운영자 직접):
  cd ~/stock && .venv/bin/python -m bot.daju_watch --auth
이후 systemd(daju-listener.service)가 무인 실행.

수동 점검: .venv/bin/python -m bot.daju_watch --replay <파일>  (파일 텍스트를
그대로 파싱·아카이브 — Telethon 없이 파이프라인 검증)
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.daju_watch")

_KST = timezone(timedelta(hours=9))
_ARCHIVE_DIR = Path.home() / ".tradingagents" / "daju_archive"
_SOURCE = os.environ.get("DAJU_SOURCE", "daju_017_bot")
_SESSION = os.environ.get("DAJU_SESSION", ".daju-listener-session")


def _kst_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(_KST)


def archive_daju(text: str, msg_id: int | str,
                 ts: datetime | None = None) -> dict | None:
    """파싱 → JSON 아카이브 1건. 형식 불일치면 None, 이미 있으면 기존 반환(멱등).

    파일명이 message_id 라 리스너 재시작·중복 수신에도 덮어쓰기만 발생하고
    카드가 중복되지 않는다(trade ingest 의 message_id dedup 과 같은 정책)."""
    from bot.daju_parse import parse_daju
    rec = parse_daju(text)
    if not rec:
        return None
    t = ts or _kst_now()
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(_KST)
    day = t.strftime("%Y-%m-%d")
    rec["ts"] = t.isoformat(timespec="seconds")
    rec["date"] = day
    rec["msg_id"] = str(msg_id)
    rec["source"] = "DAJU"
    d = _ARCHIVE_DIR / day
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(msg_id)) or "msg"
    f = d / f"{safe}.json"
    try:
        f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("daju: archive write failed (%s): %s", f, exc)
        return rec
    log.info("daju: archived %s (%d종목, target=%s)", f.name,
             len(rec.get("stocks") or []), rec.get("target"))
    return rec


def summarize_for_push(rec: dict) -> str:
    """NOAH 채널에 올릴 한 덩어리(HTML). 원문 재게시가 아니라 **요점 + 출처 고지**
    — 남의 서비스 콘텐츠를 통째로 재배포하지 않기 위해 종목·점수만 옮긴다.
    ⚠️ parse_mode=HTML 이라 외부 텍스트의 <,>,& 는 escape(실수#7)."""
    import html as _h
    head = _h.escape(rec.get("headline") or "실적 발표 예정")
    lines = [f"📣 <b>{head}</b>", "<i>— DAJU(다주) · 데이터 기반 추정이며 투자 권유가 아닙니다</i>"]
    for s in rec.get("stocks") or []:
        nm, cd = _h.escape(s.get("name") or ""), _h.escape(s.get("code") or "")
        bits = [f"{nm} ({cd})"]
        if s.get("score") is not None:
            bits.append(f"기대 {s['score']}점")
        if s.get("price"):
            bits.append(_h.escape(str(s["price"])))
        if s.get("move_1m") is not None:
            bits.append(f"1개월 {s['move_1m']:+.1f}%")
        lines.append("• " + " · ".join(bits))
    tom = rec.get("tomorrow") or {}
    if tom.get("items"):
        names = ", ".join(_h.escape(i.get("name") or "") for i in tom["items"][:4])
        lines.append(f"📎 {_h.escape(tom.get('label') or '내일 발표 예정')}: {names}")
    lines.append('<a href="blog.html">전체 내용 → 블로그 대시보드</a>')
    return "\n".join(lines)


def handle_text(text: str, msg_id: int | str,
                ts: datetime | None = None, *, push: bool = True) -> dict | None:
    """수신 텍스트 1건 처리 — 아카이브 + 채널 push + 대시보드 재생성.
    각 단계는 독립 try (한 곳이 죽어도 나머지는 진행). 형식 불일치면 None."""
    rec = archive_daju(text, msg_id, ts)
    if not rec:
        return None
    if push:
        try:
            from bot.daily_kr_flow import push_telegram
            push_telegram(summarize_for_push(rec))
        except Exception as exc:
            log.warning("daju: telegram push failed: %s", exc)
    try:
        from bot.dashboard import regenerate_blog_index
        regenerate_blog_index()
    except Exception as exc:
        log.warning("daju: blog regen failed: %s", exc)
    return rec


# ── Telethon 리스너 ────────────────────────────────────────────────────────
def _client():
    from telethon import TelegramClient
    api_id = int(os.environ["TRADE_TELETHON_API_ID"])
    api_hash = os.environ["TRADE_TELETHON_API_HASH"]
    return TelegramClient(_SESSION, api_id, api_hash)


def _auth() -> None:
    """최초 1회 대화형 로그인(휴대폰+코드+2FA) → 세션 파일 저장."""
    with _client() as c:
        c.start()
        print(f"OK — session saved at {_SESSION}")


def _run() -> None:
    """상시 수신. DAJU 형식이 아닌 메시지는 무시(relevance 필터)."""
    from telethon import events
    from bot.daju_parse import is_daju_earnings
    client = _client()

    @client.on(events.NewMessage(chats=_SOURCE))
    async def _on(event):        # pragma: no cover - 네트워크 경로
        body = event.message.message or ""
        if not is_daju_earnings(body):
            log.info("daju: 무관 메시지 skip (id=%s)", event.message.id)
            return
        try:
            handle_text(body, event.message.id, event.message.date)
        except Exception as exc:
            log.warning("daju: handle failed (id=%s): %s", event.message.id, exc)

    log.info("daju: listener 가동 — source=%s", _SOURCE)
    with client:
        client.run_until_disconnected()


def main(argv: list[str] | None = None) -> int:   # pragma: no cover - 진입점
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--auth", action="store_true", help="최초 1회 대화형 인증")
    p.add_argument("--replay", metavar="FILE",
                   help="파일 텍스트를 그대로 파싱·아카이브(Telethon 불요)")
    p.add_argument("--no-push", action="store_true", help="채널 push 생략")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if a.auth:
        _auth()
        return 0
    if a.replay:
        txt = Path(a.replay).read_text(encoding="utf-8")
        rec = handle_text(txt, f"replay-{int(datetime.now().timestamp())}",
                          push=not a.no_push)
        print("파싱 실패 — DAJU 형식 아님" if not rec
              else f"OK — {len(rec.get('stocks') or [])}종목 아카이브")
        return 0 if rec else 1
    _run()
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
