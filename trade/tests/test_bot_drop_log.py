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
import logging
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
        # 봇의 **실제** 포매터로 — 손으로 적은 형식은 형식이 바뀌어도 축복한다(#155·#91b)
        fmt = bot._TokenRedactFormatter(bot._LOG_FORMAT)
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
        errs = []

        class _App:
            def add_handler(self, *_a, **_k):
                pass

            def add_error_handler(self, cb, *_a, **_k):
                errs.append(cb)

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
        # 에러 핸들러가 실제로 걸린다 — 안 걸리면 PTB 기본 문구만 남아 어느 글이었는지
        # 모른다(독립 리뷰 M2)
        self.assertEqual(errs, [bot._on_error])


def _ctx(err):
    return SimpleNamespace(error=err, bot=mock.AsyncMock())


def _journal_lines(bot, records, pid=4242):
    """봇 로그 레코드 → 저널 줄 — 봇의 실제 포매터로(생산자 = 소비자 계약, #155)."""
    fmt = bot._TokenRedactFormatter(bot._LOG_FORMAT)
    return [f"2026-09-25T07:49:12+0900 telegram-bot-usc python[{pid}]: " + fmt.format(r)
            for r in records]


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class ErrorHandlerTests(unittest.TestCase):
    """독립 리뷰 M2 — 받은 채널 글을 처리하다 예외가 나면 그 글은 수신 줄도 버림 줄도 없이
    사라진다. 옛 판(에러 핸들러 없음)은 PTB 기본 문구만 남겨 `bot_health` 가 그 누락을
    '텔레그램이 안 줬다' 로 읽었다. 봇이 찍은 줄을 진단 파서로 태워 갈래가 읽히는지 본다."""

    def setUp(self):
        from trade import bot, bot_health
        self.bot, self.bh = bot, bot_health

    def _log(self, update, err):
        with self.assertLogs("trade-bot", level="ERROR") as cm:
            asyncio.run(self.bot._on_error(update, _ctx(err)))
        return cm.records

    def test_channel_post_exception_is_a_parseable_handler_line_with_traceback(self):
        try:
            raise OSError(28, "No space left on device")
        except OSError as exc:
            err = exc
        recs = self._log(SimpleNamespace(channel_post=_post(
            msg_id=77, fwd_chat_id=_BADONION, fwd_username="Badonions")), err)
        self.assertEqual(len(recs), 1)
        self.assertIsNotNone(recs[0].exc_info)          # 트레이스백은 그대로 남긴다
        j = self.bh.journal_facts(_journal_lines(self.bot, recs))
        # 포워드 출처도 버림 줄과 같은 규약으로 실린다 — 릴레이 글인지 가른다(2차 리뷰 H1)
        self.assertEqual([(e["kind"], e["update"], e["msg"], e["type"], e["chat"], e["user"])
                          for e in j["exceptions"]],
                         [("handler", "channel_post", 77, "channel", _BADONION, "Badonions")], j)
        # 직접 쓴 글(포워드 아님)은 type=none — 릴레이 글이 아니다
        recs2 = self._log(SimpleNamespace(channel_post=_post(msg_id=78)), err)
        j2 = self.bh.journal_facts(_journal_lines(self.bot, recs2))
        self.assertEqual([(e["type"], e["chat"], e["user"]) for e in j2["exceptions"]],
                         [("none", None, "")], j2)

    def test_polling_error_is_not_called_a_handler_error(self):
        recs = self._log(None, RuntimeError("Conflict: terminated by other getUpdates request"))
        j = self.bh.journal_facts(_journal_lines(self.bot, recs))
        self.assertEqual([e["kind"] for e in j["exceptions"]], ["polling"], j)

    def test_other_update_kinds_are_named_and_not_counted_as_channel_posts(self):
        dm = SimpleNamespace(channel_post=None, effective_message=SimpleNamespace(message_id=5))
        recs = self._log(dm, ValueError("x"))
        j = self.bh.journal_facts(_journal_lines(self.bot, recs))
        self.assertEqual([(e["kind"], e["update"], e["msg"]) for e in j["exceptions"]],
                         [("handler", "SimpleNamespace", 5)], j)

    def test_a_lost_post_is_read_as_the_cause_not_as_telegram(self):
        """E2E(순수 쪽): 봇이 찍은 예외 줄 → 저널 사실 → 대조 → 판정이 '예외로 놓쳤다'.
        릴레이 원천(나쁜양파)의 포워드라야 ❌ 다 — 출처를 봇이 실제로 찍은 줄에서 읽는다."""
        from datetime import timedelta

        bh = self.bh
        recs = self._log(SimpleNamespace(channel_post=_post(
            msg_id=77, fwd_chat_id=_BADONION, fwd_username="Badonions")), OSError("disk"))
        j = bh.journal_facts(_journal_lines(self.bot, recs))
        now = datetime(2026, 9, 25, 8, 30, tzinfo=timezone(timedelta(hours=9)))
        fwd = [{"ts": now - timedelta(minutes=41), "n": 1, "who": "listen_badonion",
                "done": False}]
        gap = bh.delivery_gap(fwd, j["ingested"], now)
        self.assertEqual(gap["kind"], "total")
        run = bh.parse_start_line(
            "2026-09-24T23:53:10+0900 h python[4242]: 2026-09-24 23:53:10,000 [INFO] trade-bot"
            f" — trade-bot starting — inbox=/x/inbox.jsonl media=/x/media allowed={{{_DEST}}} "
            "origin={'badonions', 'beon_beclear'} concurrency=8 drop_log=on")
        f = {"now": now, "env": {"dest": _DEST, "inbox": "/x/inbox.jsonl"},
             "unit": {"kind": "running", "text": "살아 있다"}, "gap": gap,
             "tg": {"token": False}, "journal": j, "running": run,
             "relays": {"Badonions": ["listen_badonion"]}, "inbox_expected": "/x/inbox.jsonl",
             "inbox": {}, "others": []}
        f["journal"]["last_ok"] = now                     # 폴링은 정상 — 원인은 예외다
        _rc, lines = bh.verdict(f)
        out = "\n".join(lines)
        self.assertIn("❌ 봇이 릴레이 원천의 채널 글 1건을 처리하다 예외로 놓쳤다(번호 77)", out)
        self.assertNotIn("텔레그램이 전달하지 않았다", out)


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class TokenRedactionTests(unittest.TestCase):
    """독립 리뷰 M3d — httpx 가 요청 URL(`bot<TOKEN>`)을 INFO 로 찍어 trade-bot 저널의
    getUpdates 줄마다 토큰이 평문이었다. NOAH 봇의 선례(`_TokenRedactFilter`)를 포매터로
    옮겼다. 가린 뒤에도 watchdog 이 세는 'getUpdates' 와 진단의 폴링 파서는 살아야 한다."""

    TOK = "123456789" + ":" + "AAH" + "x" * 32              # 소스에 토큰 모양을 두지 않는다(#407)

    def setUp(self):
        from trade import bot, bot_health
        self.bot, self.bh = bot, bot_health
        p = mock.patch.object(bot, "TOKEN", self.TOK)
        p.start()
        self.addCleanup(p.stop)

    def test_url_object_args_and_tracebacks_are_masked_and_polls_still_parse(self):
        class _URL:                                        # httpx 는 URL **객체**를 인자로 넘긴다
            def __init__(self, s):
                self.s = s

            def __str__(self):
                return self.s
        url = _URL(f"https://api.telegram.org/bot{self.TOK}/getUpdates")
        rec = logging.LogRecord("httpx", logging.INFO, "x", 1, 'HTTP Request: %s %s "%s"',
                                ("POST", url, "HTTP/1.1 200 OK"), None)
        fmt = self.bot._TokenRedactFormatter(self.bot._LOG_FORMAT)
        out = fmt.format(rec)
        self.assertNotIn(self.TOK, out)
        self.assertNotIn(self.TOK.split(":")[1], out)
        self.assertIn("/botBOT_TOKEN/getUpdates", out)      # watchdog 이 세는 낱말은 산다
        line = "2026-09-25T07:49:12+0900 h python[4242]: " + out
        self.assertEqual(self.bh.journal_facts([line])["polls"], {"200": 1})
        try:
            raise RuntimeError(f"The token `{self.TOK}` was rejected by the server.")
        except RuntimeError:
            import sys
            rec2 = logging.LogRecord("trade-bot", logging.ERROR, "x", 1, "crashed", None,
                                     sys.exc_info())
        tb = fmt.format(rec2)
        self.assertIn("Traceback", tb)
        self.assertNotIn(self.TOK, tb)
        self.assertNotIn(self.TOK.split(":")[1], tb)

    def test_every_occurrence_of_the_token_is_masked(self):
        """2차 리뷰 생존 뮤테이션 T07 — 한 줄에 토큰이 두 번 나오면(URL 과 예외 문구가 같이 찍힌
        줄) 첫 번째만 가리는 변형이 통과했다. 전부 가려야 한다."""
        rec = logging.LogRecord("httpx", logging.INFO, "x", 1, "a=%s b=%s", (self.TOK, self.TOK),
                                None)
        out = self.bot._TokenRedactFormatter(self.bot._LOG_FORMAT).format(rec)
        self.assertEqual(out.count("BOT_TOKEN"), 2, out)
        self.assertNotIn(self.TOK.split(":")[1], out)

    def test_the_formatter_does_not_mutate_the_shared_record(self):
        """필터와 달리 포매터는 레코드를 제자리에서 고치지 않는다 — 같은 레코드를 받는 다른
        핸들러(테스트 러너의 캡처 등)가 바뀐 글을 보지 않는다."""
        rec = logging.LogRecord("httpx", logging.INFO, "x", 1, "u=%s", (self.TOK,), None)
        self.bot._TokenRedactFormatter(self.bot._LOG_FORMAT).format(rec)
        self.assertIn(self.TOK, rec.getMessage())

    def test_production_root_handler_is_the_masking_one(self):
        """운영과 같은 조건(루트에 핸들러 없음)의 **별도 프로세스**로 import 해, 루트 핸들러가
        봇 것이고 그 출력에 토큰이 없는지 잰다 — 다른 모듈이 먼저 로깅을 설정하면
        basicConfig 가 아무것도 안 해 가림이 조용히 빠진다(#12)."""
        import os
        import subprocess
        import sys
        import textwrap
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        code = textwrap.dedent("""
            import logging, sys
            import trade.bot as b
            assert logging.getLogger().handlers == [b._LOG_HANDLER], logging.getLogger().handlers
            logging.getLogger("httpx").info(
                'HTTP Request: POST %s "HTTP/1.1 200 OK"',
                "https://api.telegram.org/bot" + b.TOKEN + "/getUpdates")
            try:
                raise RuntimeError("boom " + b.TOKEN)
            except RuntimeError:
                logging.getLogger("telegram.ext.Application").exception("x")
            print("DONE", file=sys.stderr)
        """)
        env = {**os.environ, "TRADE_BOT_TOKEN": self.TOK,
               "PYTHONPATH": os.pathsep.join(
                   [str(repo), *[x for x in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                                 if x]])}
        r = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("DONE", r.stderr)
        self.assertIn("/botBOT_TOKEN/getUpdates", r.stderr)
        self.assertNotIn(self.TOK.split(":")[1], r.stderr + r.stdout)


@unittest.skipUnless(_HAVE_TELEGRAM, "python-telegram-bot not installed")
class EntrypointTests(unittest.TestCase):
    """잡히지 않은 예외도 가림 포매터를 거친다 — PTB 는 토큰이 거절되면 예외 문구에 토큰을
    싣는다(21.6 `_bot.py`). 종료 코드는 전과 같이 1."""

    def test_crash_is_logged_through_the_bot_logger_and_exits_1(self):
        from trade import bot
        with mock.patch.object(bot, "main", side_effect=RuntimeError("boom")), \
                self.assertLogs("trade-bot", level="ERROR") as cm:
            self.assertEqual(bot._run(), 1)
        self.assertTrue(any(r.getMessage() == "trade-bot crashed" and r.exc_info
                            for r in cm.records), cm.output)
        with mock.patch.object(bot, "main", return_value=None):
            self.assertEqual(bot._run(), 0)

    def test_main_block_goes_through_run(self):
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
        blocks = [n for n in ast.parse(src).body if isinstance(n, ast.If)
                  and "__main__" in ast.unparse(n.test)]
        self.assertEqual(len(blocks), 1)
        calls = {c.func.id for c in ast.walk(blocks[0])
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        self.assertIn("_run", calls)
        self.assertNotIn("main", calls)


if __name__ == "__main__":
    unittest.main()
