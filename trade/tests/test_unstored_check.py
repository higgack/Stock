"""trade.scripts.unstored_check — eval-miss log tests.

The daily integrity check now appends every unstored (un-parseable)
caption to eval_misses.jsonl so an unhandled BeOn format becomes a
durable regression fixture instead of a one-shot ⚠️ alert that
scrolls out of the channel. These tests pin the contract that makes
that backlog useful:

  - new misses are appended with the raw caption preserved verbatim
  - the log is idempotent on (chat_id, message_id): a miss that
    persists across daily runs is recorded exactly once
  - malformed lines in the log don't abort the dedup scan
"""

import json
import tempfile
import unittest
from pathlib import Path

from trade.scripts.unstored_check import (
    _load_logged_miss_keys,
    log_eval_misses,
)


def _miss(chat_id: int, msg_id: int, caption: str) -> dict:
    return {
        "chat_id": chat_id,
        "message_id": msg_id,
        "ingested_at": "2026-05-24T06:45:30Z",
        "caption": caption,
    }


class TestLogEvalMisses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "eval_misses.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_appends_new_misses_with_raw_caption(self):
        cap = "Cerebras 공동 창립자, Blackwell GPU 지연 분석\n복잡한 인터포저"
        n = log_eval_misses([_miss(1, 4932, cap)], self.path)
        self.assertEqual(n, 1)
        rows = self._read()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message_id"], 4932)
        self.assertEqual(rows[0]["caption"], cap)
        self.assertIn("detected_at", rows[0])

    def test_idempotent_on_repeat_run(self):
        # Same miss seen on three consecutive daily runs (operator
        # hasn't added a RULE yet) → logged once, not three times.
        m = _miss(1, 4932, "미파싱 캡션")
        self.assertEqual(log_eval_misses([m], self.path), 1)
        self.assertEqual(log_eval_misses([m], self.path), 0)
        self.assertEqual(log_eval_misses([m], self.path), 0)
        self.assertEqual(len(self._read()), 1)

    def test_new_miss_appended_alongside_existing(self):
        log_eval_misses([_miss(1, 100, "first")], self.path)
        n = log_eval_misses(
            [_miss(1, 100, "first"), _miss(1, 200, "second")], self.path
        )
        self.assertEqual(n, 1)  # only msg 200 is new
        mids = {r["message_id"] for r in self._read()}
        self.assertEqual(mids, {100, 200})

    def test_same_msg_id_different_chat_is_distinct(self):
        # Dedup key is the full (chat_id, message_id) pair.
        log_eval_misses([_miss(1, 50, "a")], self.path)
        n = log_eval_misses([_miss(2, 50, "b")], self.path)
        self.assertEqual(n, 1)
        self.assertEqual(len(self._read()), 2)

    def test_empty_missing_writes_nothing(self):
        self.assertEqual(log_eval_misses([], self.path), 0)
        self.assertFalse(self.path.exists())

    def test_malformed_log_lines_dont_break_dedup(self):
        self.path.write_text(
            json.dumps({"chat_id": 1, "message_id": 100, "caption": "x"})
            + "\n"
            + "not-json\n"
            + json.dumps({"chat_id": 1})  # partial, no message_id
            + "\n",
            encoding="utf-8",
        )
        keys = _load_logged_miss_keys(self.path)
        self.assertEqual(keys, {(1, 100)})
        # msg 100 already logged → skipped; msg 200 is new.
        n = log_eval_misses(
            [_miss(1, 100, "x"), _miss(1, 200, "y")], self.path
        )
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
