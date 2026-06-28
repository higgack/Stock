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
from unittest import mock

from trade.scripts import unstored_check as uc
from trade.scripts.unstored_check import (
    _load_logged_miss_keys,
    find_unstored,
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


class TestJpExportsNotUnstored(unittest.TestCase):
    """일본 수출(BeOn) 캡션은 별도 jp.db 로 ingest 되므로 store.db alerts 에 없는
    게 정상 — 미등록 오탐하면 안 됨(사용자 2026-06-28: 39건 JP 캡션 오탐 사건).
    JP 포맷 인식분은 find_unstored 가 제외, 비-JP 미파싱분은 계속 잡아야 한다."""

    JP_CAP = (
        "📈 일본 수출 데이터 업데이트: MLCC (적층 세라믹 콘덴서)\n"
        "━━━━━━━━━━━━━━━\n"
        "📅 최신 월: 2026-05\n"
        "💰 수출액: 323.76십억 엔"
    )
    NONJP_CAP = "랜덤 공지: 이번 주 세미나 안내 (수출입 무관)"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name) / "inbox.jsonl"
        rows = [
            {"caption_present": True, "caption": self.JP_CAP,
             "chat_id": 1, "message_id": 10326, "ingested_at": "2020-01-01T00:00:00Z"},
            {"caption_present": True, "caption": self.NONJP_CAP,
             "chat_id": 1, "message_id": 10327, "ingested_at": "2020-01-01T00:00:00Z"},
        ]
        self.inbox.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_jp_skipped_nonjp_flagged(self):
        # store.db 없음 → stored 집합 비어 모든 캡션이 후보. JP 만 제외돼야 함.
        with mock.patch.object(uc, "INBOX_PATH", self.inbox), \
             mock.patch.object(uc, "STORE_PATH", Path(self.tmp.name) / "nope.db"), \
             mock.patch.object(uc._ignored, "load", return_value=set()), \
             mock.patch.object(uc._ignored, "matches_prefix", return_value=False), \
             mock.patch.object(uc._ignored, "matches_contains", return_value=False):
            missing = find_unstored()
        mids = {r["message_id"] for r in missing}
        self.assertNotIn(10326, mids)   # JP 캡션 — jp.db 소관, 오탐 금지
        self.assertIn(10327, mids)      # 비-JP 미파싱 — 계속 잡힘


if __name__ == "__main__":
    unittest.main()
