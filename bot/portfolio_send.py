"""자산 스냅샷 CSV '보내기' — 텔레그램 DM + 이메일 (사용자 요청 2026-06-06).

대시보드의 '보내기' 버튼이 렌더된 표(손익변동·NOAH판정 포함)에서 CSV 를
만들어 `/api/portfolio_send` 로 POST → 이 모듈이 **본인에게만** 전송한다.

⛔ 프라이버시: 자산 CSV 는 민감정보 → **공개 채널(CHANNEL_CHAT_IDS)로 절대
보내지 않는다.** 텔레그램은 본인 DM(PORTFOLIO_TG_CHAT_ID 또는 ALLOWED_USER_IDS
첫 id), 이메일은 본인 메일(PORTFOLIO_EMAIL_TO 또는 SMTP_USER)로만.

graceful: 미설정/실패 시 (False, 한국어 사유) 반환 — 대시보드가 그대로 표시.
순수 헬퍼(_owner_chat_id 등)는 단위테스트.

필요 env:
  텔레그램(즉시): TELEGRAM_BOT_TOKEN(이미 있음) + PORTFOLIO_TG_CHAT_ID(본인
    텔레그램 user id) 또는 ALLOWED_USER_IDS.
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


def _owner_chat_id() -> str:
    """본인 DM chat id — PORTFOLIO_TG_CHAT_ID 우선, 없으면 ALLOWED_USER_IDS
    첫 항목. **CHANNEL_CHAT_IDS 는 절대 사용 안 함**(채널=공개, 자산 비공개)."""
    cid = _env("PORTFOLIO_TG_CHAT_ID")
    if cid:
        return cid
    ids = _env("ALLOWED_USER_IDS")
    return ids.split(",")[0].strip() if ids else ""


def _email_to() -> str:
    return _env("PORTFOLIO_EMAIL_TO") or _env("SMTP_USER")


def send_telegram(csv_bytes: bytes, filename: str) -> Tuple[bool, str]:
    """본인 DM 으로 sendDocument. (ok, 한국어 메시지)."""
    token = _env("TELEGRAM_BOT_TOKEN")
    chat = _owner_chat_id()
    if not token:
        return False, "텔레그램 미설정 (TELEGRAM_BOT_TOKEN 없음)"
    if not chat:
        return False, ("텔레그램 미설정 — 본인 DM id 필요 (.env 에 "
                       "PORTFOLIO_TG_CHAT_ID 또는 ALLOWED_USER_IDS)")
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat, "caption": "📊 자산 스냅샷 (대시보드 보내기)"},
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


def send_email(csv_bytes: bytes, filename: str) -> Tuple[bool, str]:
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
        msg["Subject"] = "NOAH 자산 스냅샷"
        msg["From"] = user
        msg["To"] = to
        msg.set_content("첨부 파일: 자산 관리 대시보드 스냅샷 CSV (Excel 호환).")
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


def send(csv_bytes: bytes, filename: str, to: str) -> Tuple[bool, str]:
    """대상별 디스패치. to ∈ {'telegram','email'}."""
    if to == "telegram":
        return send_telegram(csv_bytes, filename)
    if to == "email":
        return send_email(csv_bytes, filename)
    return False, f"알 수 없는 전송 대상: {to}"
