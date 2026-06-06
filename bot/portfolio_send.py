"""자산 스냅샷 CSV '보내기' — 텔레그램 채널 + 이메일 (사용자 요청 2026-06-06).

대시보드의 '보내기' 버튼이 렌더된 표(손익변동·NOAH판정 포함)에서 CSV 를
만들어 `/api/portfolio_send` 로 POST → 이 모듈이 NOAH 채널 또는 이메일로
전송한다.

텔레그램: CHANNEL_CHAT_IDS 첫 번째 채널로 전송 (사용자 정책 2026-06-06
"텔레그램채널은 현재 우리채널에 보내면돼"). PORTFOLIO_TG_CHAT_ID 가 있으면
해당 DM 우선.

graceful: 미설정/실패 시 (False, 한국어 사유) 반환 — 대시보드가 그대로 표시.
순수 헬퍼(_target_chat_id 등)는 단위테스트.

필요 env:
  텔레그램(즉시): TELEGRAM_BOT_TOKEN(이미 있음) + CHANNEL_CHAT_IDS(채널 id)
    또는 PORTFOLIO_TG_CHAT_ID(본인 DM 우선).
  이메일(설정 후): SMTP_USER(gmail) + SMTP_PASS(앱비밀번호 — 일반 비번 아님)
    + 선택 SMTP_HOST(기본 smtp.gmail.com)/SMTP_PORT(기본 587)/PORTFOLIO_EMAIL_TO.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Tuple

import requests

_HTTP_TIMEOUT = 20


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _target_chat_id() -> str:
    """전송 대상 chat id — PORTFOLIO_TG_CHAT_ID(DM) 우선, 없으면
    CHANNEL_CHAT_IDS 첫 채널 (사용자 정책 2026-06-06)."""
    cid = _env("PORTFOLIO_TG_CHAT_ID")
    if cid:
        return cid
    ids = _env("CHANNEL_CHAT_IDS")
    return ids.split(",")[0].strip() if ids else ""


def _email_to() -> str:
    return _env("PORTFOLIO_EMAIL_TO") or _env("SMTP_USER")


def send_telegram(csv_bytes: bytes, filename: str, caption: str = "자산") -> Tuple[bool, str]:
    """NOAH 채널(또는 DM)로 sendDocument. (ok, 한국어 메시지)."""
    token = _env("TELEGRAM_BOT_TOKEN")
    chat = _target_chat_id()
    if not token:
        return False, "텔레그램 미설정 (TELEGRAM_BOT_TOKEN 없음)"
    if not chat:
        return False, ("텔레그램 미설정 — .env 에 CHANNEL_CHAT_IDS "
                       "또는 PORTFOLIO_TG_CHAT_ID 필요")
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat, "caption": f"📊 {caption} 스냅샷 (대시보드 보내기)"},
            files={"document": (filename, csv_bytes, "text/csv")},
            timeout=_HTTP_TIMEOUT,
        )
        j = r.json()
        if j.get("ok"):
            return True, "텔레그램 DM 전송 완료"
        desc = str(j.get("description", ""))[:120]
        # 흔한 원인: 봇과 DM 시작 안 함(chat not found) → 안내
        if "chat not found" in desc or "can't" in desc.lower():
            return False, ("텔레그램 전송 실패 — 봇과 DM 을 먼저 시작하세요 "
                           "(/start). 사유: " + desc)
        return False, f"텔레그램 거부: {desc}"
    except Exception as exc:
        return False, f"텔레그램 전송 실패: {exc}"


def send_email(csv_bytes: bytes, filename: str, caption: str = "자산") -> Tuple[bool, str]:
    """SMTP(기본 Gmail)로 본인 메일에 CSV 첨부 전송. (ok, 한국어 메시지)."""
    host = _env("SMTP_HOST") or "smtp.gmail.com"
    try:
        port = int(_env("SMTP_PORT") or "587")
    except ValueError:
        port = 587
    user = _env("SMTP_USER")
    pw = _env("SMTP_PASS")
    to = _email_to()
    if not user or not pw:
        return False, ("이메일 미설정 — .env 에 SMTP_USER + SMTP_PASS(Gmail "
                       "앱비밀번호, 일반 비번 아님) 등록 필요")
    if not to:
        return False, "이메일 미설정 — 수신 주소 없음 (PORTFOLIO_EMAIL_TO/SMTP_USER)"
    try:
        msg = EmailMessage()
        msg["Subject"] = f"NOAH {caption} 스냅샷"
        msg["From"] = user
        msg["To"] = to
        msg.set_content(f"첨부 파일: {caption} 대시보드 스냅샷 CSV (Excel 호환).")
        msg.add_attachment(csv_bytes, maintype="text", subtype="csv",
                           filename=filename)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=_HTTP_TIMEOUT) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)
        return True, f"이메일 전송 완료 ({to})"
    except Exception as exc:
        return False, f"이메일 전송 실패: {exc}"


def send(csv_bytes: bytes, filename: str, to: str, *, caption: str = "자산") -> Tuple[bool, str]:
    """대상별 디스패치. to ∈ {'telegram','email'}."""
    if to == "telegram":
        return send_telegram(csv_bytes, filename, caption)
    if to == "email":
        return send_email(csv_bytes, filename, caption)
    return False, f"알 수 없는 전송 대상: {to}"
