"""trade.bot — _origin_matches multi-source regression (사용자 2026-07-11).

버그: `_origin_matches`(원래 `_allowed_channel`+`SOURCE_ORIGIN` 단일값)가
BeOn 하나만 허용하도록 하드코딩돼 있어서, 나쁜양파(Badonions) Telethon
릴레이가 같은 private 채널로 정상 forward 해도 이 필터에서 조용히 드랍
(inbox.jsonl 에 로그 한 줄도 안 남음) — 대만/중국 파이프라인이 Telethon
레벨에서는 "forwarded 68/68" 로 성공 보고했는데 실제로는 아무 것도 안
쌓이던 실장애의 근본 원인. `TRADE_SOURCE_ORIGIN` 을 콤마구분 다중값으로
일반화한 `SOURCE_ORIGINS`(set) 로 fix — 이 테스트는 그 계약을 고정한다.

Skipped wholesale when python-telegram-bot isn't available (테스트 러너
환경엔 미설치 — VM 의 .venv 에만 설치됨, telethon 스킵과 동일 클래스)."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("TRADE_BOT_TOKEN", "stub")

try:
    import telegram  # noqa: F401
    _HAVE_TELEGRAM = True
except Exception:
    # Broad catch, not just ImportError: some sandboxes have a broken
    # native-extension install (e.g. cryptography/_cffi_backend) that
    # raises a non-ImportError exception on import rather than a clean
    # ModuleNotFoundError — same skip-gracefully intent either way.
    _HAVE_TELEGRAM = False


def _fake_post(*, fwd_chat_id=None, fwd_username=None, text="", legacy=False):
    """SimpleNamespace shaped like a telegram.Message for _origin_matches —
    it only touches forward_origin.chat / forward_from_chat / text / caption,
    so a duck-typed stub is enough (no real Telegram objects needed)."""
    chat = None
    if fwd_chat_id is not None or fwd_username is not None:
        chat = SimpleNamespace(id=fwd_chat_id, username=fwd_username)
    if legacy:
        return SimpleNamespace(
            forward_origin=None, forward_from_chat=chat,
            text=text, caption=None,
        )
    origin = SimpleNamespace(chat=chat) if chat is not None else None
    return SimpleNamespace(
        forward_origin=origin, forward_from_chat=None,
        text=text, caption=None,
    )


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class OriginMatchesTests(unittest.TestCase):
    def test_discovery_mode_accepts_everything_when_unset(self):
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", set()):
            self.assertTrue(bot._origin_matches(_fake_post(fwd_username="anything")))
            self.assertTrue(bot._origin_matches(_fake_post()))

    def test_single_source_backward_compatible(self):
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", {"beon_beclear"}):
            self.assertTrue(
                bot._origin_matches(_fake_post(fwd_username="BeOn_BeClear")))
            self.assertFalse(
                bot._origin_matches(_fake_post(fwd_username="Badonions")))

    def test_multi_source_both_accepted(self):
        # 나쁜양파 추가 후의 실제 설정 형태(사용자 2026-07-11 fix).
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS",
                               {"beon_beclear", "badonions"}):
            self.assertTrue(
                bot._origin_matches(_fake_post(fwd_username="BeOn_BeClear")))
            self.assertTrue(
                bot._origin_matches(_fake_post(fwd_username="Badonions")))
            self.assertFalse(
                bot._origin_matches(_fake_post(fwd_username="SomeOtherChannel")))

    def test_username_match_case_insensitive(self):
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", {"badonions"}):
            self.assertTrue(
                bot._origin_matches(_fake_post(fwd_username="BADONIONS")))

    def test_numeric_id_match(self):
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", {"-1002695068357"}):
            self.assertTrue(
                bot._origin_matches(_fake_post(fwd_chat_id=-1002695068357)))
            self.assertFalse(
                bot._origin_matches(_fake_post(fwd_chat_id=-1009999999999)))

    def test_legacy_forward_from_chat_fallback(self):
        # forward_origin 없는 구형 Bot API 필드 경로.
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", {"badonions"}):
            self.assertTrue(bot._origin_matches(
                _fake_post(fwd_username="Badonions", legacy=True)))

    def test_beon_header_copy_style_fallback(self):
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", {"beon_beclear"}):
            post = _fake_post(fwd_username="SomeCopyBot",
                              text="BeOn - 비온, 가장 빠른 투자 데이터 알림\n...")
            self.assertTrue(bot._origin_matches(post))

    def test_no_origin_no_match_rejected(self):
        from trade import bot
        with mock.patch.object(bot, "SOURCE_ORIGINS", {"beon_beclear"}):
            self.assertFalse(bot._origin_matches(_fake_post(text="그냥 잡담")))


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class ParseSourceOriginsTests(unittest.TestCase):
    """독립 code-review 2026-07-11 지적 — .strip() 이 .lstrip("@") 보다
    먼저 실행돼야 "BeOn_BeClear, @Badonions" 같은(콤마 뒤 공백 자연스러운
    수기입력) 케이스에서 leading '@' 가 안 남는다. 순서 뒤바뀌면 "@badonions"
    로 파싱되어 어느 브랜치에도 안 걸려 조용히 영구 미매치(이 fix 전체가
    막으려던 바로 그 실패모드가 콤마리스트 포맷팅으로 재발)."""

    def test_leading_at_after_comma_space_stripped_correctly(self):
        from trade import bot
        result = bot._parse_source_origins("BeOn_BeClear, @Badonions")
        self.assertEqual(result, {"beon_beclear", "badonions"})
        self.assertNotIn("@badonions", result)

    def test_empty_string_yields_empty_set(self):
        from trade import bot
        self.assertEqual(bot._parse_source_origins(""), set())

    def test_single_value_no_comma(self):
        from trade import bot
        self.assertEqual(
            bot._parse_source_origins("BeOn_BeClear"), {"beon_beclear"})

    def test_numeric_id_passthrough(self):
        from trade import bot
        self.assertEqual(
            bot._parse_source_origins("-1002695068357"), {"-1002695068357"})


if __name__ == "__main__":
    unittest.main()
