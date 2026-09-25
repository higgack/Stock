"""trade.bot — 조용히 버리는 게이트가 흔적을 남기는가 (실수 #406, 2026-09-25).

사고: 40일 회수가 나쁜양파 27건을 비공개 채널로 포워드했다("forwarded 27 of
27")는데 32분 뒤에도 inbox 가 한 줄도 안 늘었고, trade-bot 저널의
`ingested msg=` 는 0 이었다. 그 0 은 **두 갈래**를 대표한다 — 봇이 글을 못
받았다 / 받고 채널·출처 게이트에서 버렸다. 두 게이트가 로그 한 줄 없이
`return` 했기 때문이다(2026-07-11 에 같은 게이트가 68건을 조용히 버린 전례가
`test_bot_origin` 독스트링에 있는데, 그때 목록만 넓히고 버림은 계속 조용히
뒀다). 이 파일은 "버리면 적는다" 와 "받으면 버림으로 적지 않는다" 를 **핸들러를
실제로 태워** 고정한다(헬퍼만 재면 배선을 떼는 변형이 통과한다, #20).

그리고 `run_polling` 이 allowed_updates 를 **명시**하는지 — 비워 두면 텔레그램은
마지막으로 설정된 값을 계속 써서, 누가 한 번 channel_post 를 뺀 목록을 보내면
재시작해도 채널 글을 영영 못 받는다(폴링은 200 이라 watchdog 도 못 잡는다).

python-telegram-bot 이 없으면 통째로 건너뛴다(형제 test_bot_origin 과 같은
클래스 — VM 의 .venv 에만 설치). 샌드박스에선 21.6 을 깐 격리 venv 로 돌렸다.
"""

import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("TRADE_BOT_TOKEN", "stub")

try:
    import telegram  # noqa: F401
    _HAVE_TELEGRAM = True
except Exception:  # 형제와 같은 이유 — 네이티브 확장이 깨진 환경도 건너뛴다
    _HAVE_TELEGRAM = False

_DEST = -1003715527602          # 운영 비공개 채널(백필·리스너의 dest)
_BADONION = -1003322526960      # 나쁜양파 원 채널


def _post(*, chat_id=_DEST, fwd_chat_id=None, fwd_username=None,
          text="", msg_id=77):
    """on_channel_post·_serialize 가 만지는 속성을 다 가진 가짜 Message."""
    origin = None
    if fwd_chat_id is not None or fwd_username is not None:
        origin = SimpleNamespace(
            type="channel",
            chat=SimpleNamespace(id=fwd_chat_id, username=fwd_username,
                                 title="src"),
            message_id=4242,
            date=datetime(2026, 9, 15, 6, 41, tzinfo=timezone.utc),
        )
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="trade"),
        message_id=msg_id, media_group_id=None,
        date=datetime(2026, 9, 25, tzinfo=timezone.utc),
        text=text, caption=None, photo=None, document=None,
        forward_origin=origin, forward_from_chat=None,
        forward_from_message_id=None,
    )


def _run(bot, post):
    update = SimpleNamespace(channel_post=post)
    ctx = SimpleNamespace(bot=mock.AsyncMock())
    asyncio.run(bot.on_channel_post(update, ctx))


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class OriginDropLogTests(unittest.TestCase):
    def setUp(self):
        from trade import bot
        self.bot = bot
        self._p = [
            mock.patch.object(bot, "CHANNEL_CHAT_IDS", {_DEST}),
            mock.patch.object(bot, "SOURCE_ORIGINS", {"badonions", "beon_beclear"}),
            mock.patch.object(bot, "_DROPPED_CHANNELS", set()),
            mock.patch.object(bot, "_notify_watchers", mock.AsyncMock()),
        ]
        for p in self._p:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._p])

    def test_origin_drop_is_logged_with_reason_and_not_appended(self):
        with mock.patch.object(self.bot, "_append_jsonl") as app, \
                self.assertLogs("trade-bot", level="INFO") as cm:
            _run(self.bot, _post(fwd_chat_id=-1009999, fwd_username="SomeOther",
                                 text="잡담"))
        app.assert_not_called()
        lines = [r.getMessage() for r in cm.records]
        drops = [ln for ln in lines if ln.startswith("dropped msg=77 reason=origin")]
        self.assertEqual(len(drops), 1, lines)
        # 사유는 '어느 출처였고 무엇을 허용하나' 까지 말해야 행동이 정해진다(#82)
        self.assertIn("origin_chat=-1009999", drops[0])
        self.assertIn("origin_username=SomeOther", drops[0])
        self.assertIn("allowed_origins=['badonions', 'beon_beclear']", drops[0])

    def test_non_forward_post_is_logged_as_origin_none(self):
        """운영자가 채널에 직접 쓴 글 — 출처가 아예 없다. 'none' 으로 갈라 적는다."""
        with mock.patch.object(self.bot, "_append_jsonl") as app, \
                self.assertLogs("trade-bot", level="INFO") as cm:
            _run(self.bot, _post(text="그냥 메모"))
        app.assert_not_called()
        self.assertTrue(any("reason=origin origin_type=none origin_chat=None"
                            in r.getMessage() for r in cm.records),
                        [r.getMessage() for r in cm.records])

    def test_matching_forward_is_appended_and_never_logged_as_drop(self):
        """반대 증거(#25) — 받은 글을 '버림' 으로 적으면 진단이 거꾸로 간다."""
        with mock.patch.object(self.bot, "_append_jsonl") as app, \
                self.assertLogs("trade-bot", level="INFO") as cm:
            _run(self.bot, _post(fwd_chat_id=_BADONION, fwd_username="Badonions",
                                 text="**🇰🇷 8월 수입 한국**"))
        app.assert_called_once()
        rec = app.call_args.args[0]
        self.assertEqual((rec["forward_origin_chat_id"],
                          rec["forward_origin_message_id"]), (_BADONION, 4242))
        lines = [r.getMessage() for r in cm.records]
        self.assertFalse([ln for ln in lines if ln.startswith("dropped")], lines)
        self.assertTrue([ln for ln in lines if ln.startswith("ingested msg=77")], lines)


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class ChannelDropLogTests(unittest.TestCase):
    def setUp(self):
        from trade import bot
        self.bot = bot
        self._p = [
            mock.patch.object(bot, "CHANNEL_CHAT_IDS", {_DEST}),
            mock.patch.object(bot, "SOURCE_ORIGINS", {"badonions"}),
            mock.patch.object(bot, "_DROPPED_CHANNELS", set()),
            mock.patch.object(bot, "_notify_watchers", mock.AsyncMock()),
        ]
        for p in self._p:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._p])

    def test_foreign_channel_logged_once_per_chat_through_the_handler(self):
        """핸들러를 태운다 — 헬퍼만 부르면 호출부에서 게이트를 우회해도 통과한다."""
        with mock.patch.object(self.bot, "_append_jsonl") as app, \
                self.assertLogs("trade-bot", level="INFO") as cm:
            for mid in (1, 2, 3):
                _run(self.bot, _post(chat_id=-100111, fwd_chat_id=_BADONION,
                                     fwd_username="Badonions", msg_id=mid))
            _run(self.bot, _post(chat_id=-100222, fwd_chat_id=_BADONION,
                                 fwd_username="Badonions", msg_id=4))
        app.assert_not_called()
        lines = [r.getMessage() for r in cm.records if r.getMessage().startswith(
            "dropped channel=")]
        # 바쁜 남의 채널이 저널을 덮지 않게 채널당 한 번 — 둘이면 두 줄.
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith("dropped channel=-100111 reason=channel"))
        self.assertIn(f"allowed=[{_DEST}]", lines[0])
        self.assertTrue(lines[1].startswith("dropped channel=-100222 reason=channel"))

    def test_allowed_channel_is_silent(self):
        with self.assertNoLogs("trade-bot", level="INFO"):
            self.assertTrue(self.bot._allowed_channel(_DEST))


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class DropLogIsReadByBotHealthTests(unittest.TestCase):
    """생산자(봇 로그) ↔ 소비자(`trade.bot_health` 저널 파서) — 버림·수신 줄의 형식이
    바뀌면 진단은 '버림 0건 · 수신 0건' 으로 눈이 먼다. 손으로 적은 줄 대신 **봇이 실제로
    찍은 레코드**를 봇의 logging 형식으로 저널 줄로 만들어 태운다(#155·#91b)."""

    def test_bot_records_parse_as_drops_and_ingests(self):
        import logging

        from trade import bot, bot_health
        patches = [
            mock.patch.object(bot, "CHANNEL_CHAT_IDS", {_DEST}),
            mock.patch.object(bot, "SOURCE_ORIGINS", {"badonions"}),
            mock.patch.object(bot, "_DROPPED_CHANNELS", set()),
            mock.patch.object(bot, "_notify_watchers", mock.AsyncMock()),
            mock.patch.object(bot, "_append_jsonl"),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with self.assertLogs("trade-bot", level="INFO") as cm:
            _run(bot, _post(fwd_chat_id=-1009999, fwd_username="Other", msg_id=1))
            _run(bot, _post(chat_id=-100111, fwd_chat_id=_BADONION,
                            fwd_username="Badonions", msg_id=2))
            _run(bot, _post(fwd_chat_id=_BADONION, fwd_username="Badonions",
                            msg_id=3, text="x"))
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
        lines = ["2026-09-25T07:49:12+0900 telegram-bot-usc python[4242]: " + fmt.format(r)
                 for r in cm.records]
        j = bot_health.journal_facts(lines)
        self.assertEqual(len(j["drops_origin"]), 1, lines)
        self.assertEqual(len(j["drops_channel"]), 1, lines)
        self.assertEqual(len(j["ingested"]), 1, lines)
        # 필드까지 읽혀야 판정이 '릴레이 버림' 과 '정상 버림' 을 가른다(#82)
        self.assertEqual((j["drops_origin"][0]["type"], j["drops_origin"][0]["chat"],
                          j["drops_origin"][0]["user"]), ("channel", -1009999, "Other"))
        self.assertEqual(j["drops_channel"][0]["chat"], -100111)
        self.assertIsNotNone(j["ingested"][0])       # 시각까지 읽혀야 대조에 쓴다


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class AllowedUpdatesTests(unittest.TestCase):
    def test_run_polling_names_every_update_type(self):
        """main() 을 가짜 Application 으로 태워 run_polling 인자를 본다(소스
        문자열로 재면 주석이 대신 만족시킨다, #19·#59b)."""
        from telegram import Update

        from trade import bot
        seen = {}

        class _App:
            def add_handler(self, *_a, **_k):
                pass

            def run_polling(self, **kw):
                seen.update(kw)

        class _Builder:
            def token(self, _t):
                return self

            def post_init(self, _f):
                return self

            def build(self):
                return _App()

        with mock.patch.object(bot, "Application",
                               SimpleNamespace(builder=lambda: _Builder())):
            bot.main()
        self.assertIn("allowed_updates", seen)
        self.assertEqual(list(seen["allowed_updates"]), list(Update.ALL_TYPES))
        self.assertIn("channel_post", seen["allowed_updates"])


if __name__ == "__main__":
    unittest.main()
