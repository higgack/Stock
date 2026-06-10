"""trade.scripts.reparse_store — 기존 store 행 재파싱 마이그레이션 테스트."""

import json
import os
import tempfile
import unittest
from unittest import mock

from trade.parser import parse_caption
from trade.scripts import reparse_store as rc
from trade.store import alert_to_row, open_db, upsert_alert

_RAW = ("휴스틸: 대구경 철강제 관 (서울 강남구)\n\n"
        "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.")


class ReparseStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"TRADE_DATA_DIR": self.tmp.name})
        self.env.start()
        self.db = rc._db_path()
        conn = open_db(self.db)
        # 옛 파서가 회사를 품목에 박은 상태 모사: raw는 회사접두, 저장 item은 통째.
        parsed = parse_caption(_RAW)
        row = alert_to_row(
            parsed, source_chat_id=-1, source_message_id=1, media_group_id=None,
            ingested_at="2026-04-30T00:00:00+00:00",
            posted_at="2026-04-30T00:00:00+00:00", raw_text=_RAW, media_paths=[])
        row.update(item="휴스틸: 대구경 철강제 관", item_raw="휴스틸: 대구경 철강제 관",
                   stocks="[]", title_kind="item_first", dedup_key="OLDKEY")
        upsert_alert(conn, row)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _row(self) -> dict:
        conn = open_db(self.db)
        r = dict(conn.execute("SELECT * FROM alerts").fetchone())
        conn.close()
        return r

    def test_dry_run_does_not_write(self):
        self.assertEqual(rc.run(apply=False), 0)
        self.assertEqual(self._row()["item"], "휴스틸: 대구경 철강제 관")   # 그대로

    def test_apply_reparses_and_splits(self):
        self.assertEqual(rc.run(apply=True), 0)
        r = self._row()
        self.assertEqual(r["item"], "대구경 철강제 관")
        self.assertEqual(json.loads(r["stocks"]), ["휴스틸"])
        self.assertEqual(r["title_kind"], "company_first")
        self.assertNotEqual(r["dedup_key"], "OLDKEY")                     # dedup_key 갱신
        self.assertEqual(r["raw_text"], _RAW)                            # 원본 보존

    def test_idempotent_second_apply_noop(self):
        rc.run(apply=True)
        rc.run(apply=True)                                               # 재실행 안전
        self.assertEqual(self._row()["item"], "대구경 철강제 관")


if __name__ == "__main__":
    unittest.main()
