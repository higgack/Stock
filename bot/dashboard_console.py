"""대시보드 명령 콘솔 — 텔레그램 (update, ctx) 핸들러를 대시보드에서 실행.

대시보드 입력창에 친 '/명령'을 봇이 실행할 때, 핸들러의 reply_text /
reply_html / ctx.bot.send_message 텍스트 답변을 **채널로 보내는 대신 가로채**
리스트로 모은다. 모은 텍스트는 dashboard_requests.write_result 로 기록되고,
dashboard_server 의 api/command_result 가 브라우저에 폴링 응답한다.

사진/인포그래픽/마크업 키보드는 평문 노트로 대체(패널은 텍스트 전용).
Basic-Auth 뒤 단일 사용자라 신뢰 컨텍스트지만, 어느 핸들러를 호출할지는
telegram_bot 의 화이트리스트가 정한다(임의 핸들러 호출 차단). graceful —
핸들러가 예상 못 한 update 속성을 만지면 호출부 try/except 가 패널에 오류
한 줄로 degrade(폴러는 안 죽음)."""
from __future__ import annotations

import html as _html
import re
from types import SimpleNamespace

# 텔레그램이 지원하는 태그명만 매칭 — 'rsi<30' / 'price>950' 같은 조건식을
# 태그로 오인해 지우지 않게(CLAUDE.md 교훈 #7: <200 을 태그로 오인). 알 수 없는
# '<...>' 는 평문으로 보존. 본문이 &lt;/&gt; 로 escape 됐으면 unescape 가 복원.
_TG_TAGS = (r"b|strong|i|em|u|ins|s|strike|del|span|a|code|pre|"
            r"blockquote|tg-spoiler")
_TAG_RE = re.compile(r"</?(?:" + _TG_TAGS + r")(?:\s[^>]*)?>", re.IGNORECASE)


def strip_tg_html(text: str) -> str:
    """텔레그램 HTML(<b>/<code>/<a>…) → 대시보드 패널용 평문. 알려진 태그만
    제거하고 'rsi<30' 같은 조건식은 보존."""
    if not text:
        return ""
    t = str(text)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.IGNORECASE)
    t = _TAG_RE.sub("", t)
    return _html.unescape(t)


class _CaptureMessage:
    """update.message / effective_message 대체 — reply_* 를 lines 로 캡처."""

    def __init__(self, lines: list, text: str = ""):
        self._lines = lines
        self.text = text
        self.message_id = 0
        self.chat = SimpleNamespace(id=0, type="private")

    async def reply_text(self, text: str = "", **kw):
        s = strip_tg_html(text)
        if s:
            self._lines.append(s)
        return self

    # HTML/Markdown 변형 모두 같은 캡처(태그는 strip)
    reply_html = reply_text
    reply_markdown = reply_text
    reply_markdown_v2 = reply_text

    async def reply_photo(self, *a, **kw):
        self._lines.append("[사진/인포그래픽은 텔레그램 채널에 게시됩니다]")
        return self

    async def edit_text(self, text: str = "", **kw):
        s = strip_tg_html(text)
        if s:
            self._lines.append(s)
        return self

    async def delete(self, *a, **kw):
        return True


class _CaptureBot:
    """ctx.bot 대체 — send_message 텍스트 캡처, 그 외는 안전 no-op.

    실제 전송 메서드를 막아 콘솔 명령이 채널에 새 메시지를 흘리지 않게 한다
    (화이트리스트 명령은 텍스트 유틸리티 — 패널 전용)."""

    def __init__(self, lines: list):
        self._lines = lines

    async def send_message(self, chat_id=None, text: str = "", **kw):
        s = strip_tg_html(text)
        if s:
            self._lines.append(s)
        return _CaptureMessage(self._lines)

    async def send_photo(self, *a, **kw):
        self._lines.append("[사진/인포그래픽은 텔레그램 채널에 게시됩니다]")
        return _CaptureMessage(self._lines)

    async def send_chat_action(self, *a, **kw):
        return True

    def __getattr__(self, name):
        async def _noop(*a, **kw):
            return None
        return _noop


def build_capture(command_text: str, user_id: int = 0, chat_id: int = 0):
    """'/명령 인자' → (fake update, fake context, lines). lines 는 호출 후 채워짐.

    ctx.args 는 텔레그램과 동일하게 '명령 다음 토큰들'. chat_id 는 호출부가
    채널 id 를 넘김 — /dart_alert on 처럼 effective_chat.id 를 '알림 받을
    채팅'으로 저장하는 명령이 무효 chat 0 에 등록되지 않게 (2026-06-11)."""
    lines: list[str] = []
    msg = _CaptureMessage(lines, text=command_text)
    msg.chat = SimpleNamespace(id=chat_id, type="private")
    update = SimpleNamespace(
        message=msg,
        effective_message=msg,
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_user=SimpleNamespace(
            id=user_id, first_name="dashboard", username="dashboard",
            is_bot=False, full_name="dashboard",
        ),
    )
    parts = (command_text or "").strip().split()
    args = parts[1:] if len(parts) > 1 else []
    ctx = SimpleNamespace(
        args=args, bot=_CaptureBot(lines),
        user_data={}, chat_data={}, bot_data={},
    )
    return update, ctx, lines
