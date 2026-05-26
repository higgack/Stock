"""trade.scripts.backfill_beon — dedup key extraction tests.

The bug being guarded: when BeOn re-forwards a message from another
channel (e.g. AWAKE 플러스), Telegram preserves the ORIGINAL forward
chain. The old dedup keyed on (BeOn, msg.id) but trade-bot's
inbox.jsonl records the original (AWAKE, orig_msg_id), so dedup
silently missed every relayed post and every 2-hour timer tick
re-forwarded the same content into the trade channel.

These tests cover:
  - inbox key extraction across BeOn-originated and relayed records
  - msg key extraction from a Telethon-shaped Message (with and
    without fwd_from)
  - end-to-end membership check matches relayed posts correctly

Skipped wholesale when telethon isn't available (the test runner
environment doesn't install Telethon — only the .backfill-venv on
the host does)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# backfill_beon reads these at module-import time. The values don't
# matter for these tests (we don't open a real Telethon session) but
# they must be set before the module is imported so the load doesn't
# KeyError. .setdefault keeps any operator-provided values intact.
os.environ.setdefault("TRADE_TELETHON_API_ID", "0")
os.environ.setdefault("TRADE_TELETHON_API_HASH", "stub")
os.environ.setdefault("TRADE_CHANNEL_CHAT_IDS", "-1000000000000")

try:
    import telethon  # noqa: F401
    _HAVE_TELETHON = True
except ImportError:
    _HAVE_TELETHON = False


@unittest.skipUnless(_HAVE_TELETHON, "telethon not installed")
class TestLoadExistingKeys(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmpdir.name) / "inbox.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, records):
        with self.inbox.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def _load(self):
        # Patch INBOX_PATH so we read our temp file.
        from trade.scripts import backfill_beon as bb
        with mock.patch.object(bb, "INBOX_PATH", self.inbox):
            return bb._load_existing_keys()

    def test_includes_beon_originated_records(self):
        self._write([
            {
                "forward_origin_chat_id": -1001234567890,
                "forward_origin_chat_username": "BeOn_BeClear",
                "forward_origin_message_id": 4500,
            },
        ])
        keys = self._load()
        self.assertEqual(keys, {(-1001234567890, 4500)})

    def test_includes_relayed_records_from_other_channels(self):
        # Previously these were silently filtered out — the bug we're
        # closing. AWAKE 플러스 relay must be in the set too.
        self._write([
            {
                "forward_origin_chat_id": -1009999999999,
                "forward_origin_chat_username": "AWAKE_plus",
                "forward_origin_message_id": 777,
            },
        ])
        keys = self._load()
        self.assertEqual(keys, {(-1009999999999, 777)})

    def test_mixed_records_all_present(self):
        self._write([
            {
                "forward_origin_chat_id": -1001234567890,
                "forward_origin_message_id": 1,
            },
            {
                "forward_origin_chat_id": -1009999999999,
                "forward_origin_message_id": 2,
            },
            {
                "forward_origin_chat_id": -1008888888888,
                "forward_origin_message_id": 3,
            },
        ])
        self.assertEqual(
            self._load(),
            {(-1001234567890, 1), (-1009999999999, 2), (-1008888888888, 3)},
        )

    def test_skips_records_with_no_forward_origin(self):
        # Some records (errors, malformed, non-forwarded) lack the
        # field — they shouldn't add (None, None) noise to the set.
        self._write([
            {"forward_origin_chat_id": None, "forward_origin_message_id": None},
            {"forward_origin_chat_id": -1, "forward_origin_message_id": 100},
            {},
        ])
        self.assertEqual(self._load(), {(-1, 100)})

    def test_skips_malformed_jsonl_lines(self):
        # Same fault tolerance as before — a stray non-JSON line in
        # the middle of inbox.jsonl shouldn't kill the whole scan.
        self.inbox.write_text(
            json.dumps({
                "forward_origin_chat_id": -1, "forward_origin_message_id": 1,
            }) + "\n"
            + "not-json-at-all\n"
            + json.dumps({
                "forward_origin_chat_id": -2, "forward_origin_message_id": 2,
            }) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(self._load(), {(-1, 1), (-2, 2)})


@unittest.skipUnless(_HAVE_TELETHON, "telethon not installed")
class TestMsgKey(unittest.TestCase):
    """Identity extraction from a Telethon Message-shaped object.

    Uses SimpleNamespace stand-ins so we don't need a live Telegram
    session. _tutils.get_peer_id is monkeypatched to a deterministic
    converter (channel_id N → -100…N) so the test doesn't depend on
    Telethon's internal peer-marking rules."""

    @staticmethod
    def _fake_get_peer_id(peer):
        # Mirror Telethon's marked-id convention for channels.
        cid = getattr(peer, "channel_id", None)
        if cid is not None:
            return int(f"-100{cid}")
        uid = getattr(peer, "user_id", None)
        if uid is not None:
            return int(uid)
        gid = getattr(peer, "chat_id", None)
        if gid is not None:
            return -int(gid)
        raise ValueError(f"unmappable peer: {peer!r}")

    def _msg_key(self, msg, source_chat_id):
        from trade.scripts import backfill_beon as bb
        with mock.patch.object(
            bb._tutils, "get_peer_id", side_effect=self._fake_get_peer_id
        ):
            return bb._msg_key(msg, source_chat_id)

    def test_beon_originated_uses_source_id_and_msg_id(self):
        # Vanilla BeOn post, no fwd_from → (source, msg.id).
        msg = SimpleNamespace(id=4500, fwd_from=None)
        self.assertEqual(
            self._msg_key(msg, -1001234567890),
            (-1001234567890, 4500),
        )

    def test_relayed_uses_original_chat_and_post_id(self):
        # BeOn relayed AWAKE 플러스 channel post — must dedup
        # against AWAKE's identity, not BeOn's. This is the exact
        # case that the bug missed.
        msg = SimpleNamespace(
            id=4876,  # BeOn's local id for the relay
            fwd_from=SimpleNamespace(
                from_id=SimpleNamespace(channel_id=9999999999),
                channel_post=777,
            ),
        )
        self.assertEqual(
            self._msg_key(msg, -1001234567890),
            (-1009999999999, 777),
        )

    def test_fwd_from_without_channel_post_falls_back_to_source(self):
        # Hidden user forward / legacy chat — no channel_post. Safer
        # to treat as 'new' than skip a potentially-real new post.
        msg = SimpleNamespace(
            id=10,
            fwd_from=SimpleNamespace(
                from_id=SimpleNamespace(user_id=42),
                channel_post=None,
            ),
        )
        self.assertEqual(
            self._msg_key(msg, -1001234567890),
            (-1001234567890, 10),
        )

    def test_fwd_from_without_from_id_falls_back_to_source(self):
        msg = SimpleNamespace(
            id=20,
            fwd_from=SimpleNamespace(from_id=None, channel_post=100),
        )
        self.assertEqual(
            self._msg_key(msg, -1001234567890),
            (-1001234567890, 20),
        )


@unittest.skipUnless(_HAVE_TELETHON, "telethon not installed")
class TestDedupRoundtrip(unittest.TestCase):
    """End-to-end: a relayed post in inbox.jsonl is correctly recognised
    as 'seen' on a subsequent backfill iteration. This is the regression
    that triggered the operator-visible 'same DART message keeps
    appearing every 2h' behaviour."""

    def test_relayed_post_recognised_as_existing(self):
        from trade.scripts import backfill_beon as bb

        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "inbox.jsonl"
            # Listener previously forwarded a BeOn-relay of an
            # AWAKE 플러스 post; trade-bot recorded the AWAKE
            # identity (Telegram preserves the forward chain head).
            inbox.write_text(json.dumps({
                "forward_origin_chat_id": -1009999999999,
                "forward_origin_chat_username": "AWAKE_plus",
                "forward_origin_message_id": 777,
            }) + "\n", encoding="utf-8")

            with mock.patch.object(bb, "INBOX_PATH", inbox), \
                 mock.patch.object(
                     bb._tutils, "get_peer_id",
                     side_effect=TestMsgKey._fake_get_peer_id,
                 ):
                existing = bb._load_existing_keys()
                relayed = SimpleNamespace(
                    id=4876,
                    fwd_from=SimpleNamespace(
                        from_id=SimpleNamespace(channel_id=9999999999),
                        channel_post=777,
                    ),
                )
                self.assertIn(
                    bb._msg_key(relayed, -1001234567890),
                    existing,
                    "relayed post must dedup against inbox record "
                    "(this assertion failing = the original bug is back)",
                )


@unittest.skipUnless(_HAVE_TELETHON, "telethon not installed")
class TestSyncOutcome(unittest.TestCase):
    """The ⚠️ '후보 0건 / 세션 만료' alert must fire ONLY when
    iter_messages returned nothing at all — never just because the
    candidate list is empty. Regression: the forward-time IGNORED
    filter routes DART 공시 / [비온 인사이트] posts to skipped_ignored,
    so a quiet window of all-off-topic posts yields zero candidates yet
    a perfectly healthy session. _sync_outcome keys on iterated, not
    candidate count, to keep that case silent."""

    def _outcome(self, forwarded, iterated):
        from trade.scripts import backfill_beon as bb
        return bb._sync_outcome(forwarded, iterated)

    def test_forwarded_when_any_relayed(self):
        self.assertEqual(self._outcome(3, 10), "forwarded")
        self.assertEqual(self._outcome(1, 1), "forwarded")

    def test_empty_iter_only_when_nothing_iterated(self):
        # Truly empty iteration → session/access alert.
        self.assertEqual(self._outcome(0, 0), "empty_iter")

    def test_all_ignored_window_is_quiet_not_alert(self):
        # The exact false-positive case: messages existed (iterated>0)
        # but all were filtered out (forwarded=0, candidates=0). Must
        # be 'quiet', NOT 'empty_iter' — no ⚠️ session alarm.
        self.assertEqual(self._outcome(0, 12), "quiet")

    def test_all_already_ingested_is_quiet(self):
        # Window where every post was already in inbox → quiet.
        self.assertEqual(self._outcome(0, 5), "quiet")


class TestForwardTimeIgnoredFilter(unittest.TestCase):
    """Backfill must skip IGNORED-matching messages BEFORE forwarding,
    not just at ingest. Reason: trade-bot's is_from_beon() filter drops
    messages whose forward_origin ≠ BeOn (e.g. BeOn relays of AWAKE
    플러스 DART notices land in trade channel as AWAKE-origin and get
    rejected), so the inbox never records them — every subsequent
    backfill tick sees the BeOn-side message as 'new' and re-forwards
    it. Filtering at forward-time breaks that loop.

    These tests use the helper directly (no telethon needed) — they
    pin the contract: prefix-match or contains-match → skip."""

    def test_dart_link_caption_is_filtered(self):
        from trade import ignored
        cap = (
            "📌 파두(시가총액: 6조 1,569억) #A440110\n"
            "📁 단일판매ㆍ공급계약체결\n"
            "공시링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=..."
        )
        self.assertTrue(
            ignored.matches_contains(cap),
            "DART link must trigger contains filter — exact case that "
            "caused the re-forward loop on 2026-05-22 파두 message",
        )

    def test_beon_insight_prefix_is_filtered(self):
        from trade import ignored
        cap = "[비온 인사이트] 파두 수주잔고 분석"
        self.assertTrue(ignored.matches_prefix(cap))

    def test_plain_beon_alert_is_not_filtered(self):
        # Sanity: a regular BeOn export/import alert must pass through.
        from trade import ignored
        cap = "📊 4월 수출입 동향 — 화장품 정상 잠정"
        self.assertFalse(ignored.matches_prefix(cap))
        self.assertFalse(ignored.matches_contains(cap))


if __name__ == "__main__":
    unittest.main()
