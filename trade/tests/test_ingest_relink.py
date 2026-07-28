"""trade.scripts.ingest_inbox — 미디어 재링크(download↔ingest 레이스) 테스트.

백필/버스트가 alert 를 포워드하면 bot 이 사진을 백그라운드(fire-and-forget)
다운로드한다. ingest 가 다운로드 완료 전에 파싱하면 _resolve_photo_path 의
.exists() 가 False → media_paths 가 빈 채로 저장된다. upsert 는 ON CONFLICT
DO NOTHING 이라 파일이 나중에 도착해도 그 행은 영영 안 채워져 카드가 회색으로
남는다 (2026-06-15 BeOn 백필 732건). 다음 ingest 주기(5분)가 파일이 도착한
행을 재링크해야 한다 — 단, 이미 채워진 행은 매 주기 다시 쓰지 않는다.
"""
import json
import tempfile
import unittest
from pathlib import Path

from trade.scripts import ingest_inbox as ii
from trade.store import open_db

_CAP = "건기식 (경기 김포시)\n\n2026년 5월 1일 ~ 31일 확정치 수출데이터 입니다."
_UID = "AQADtESTuid12345"
_DAY = "2026-06-15"
_MSG = 42


def _row() -> dict:
    return {
        "caption_present": True,
        "caption": _CAP,
        "text": None,
        "chat_id": -100123,
        "message_id": _MSG,
        "media_group_id": None,
        "ingested_at": "2026-06-15T06:00:00+00:00",
        "forward_origin_date": "2026-06-15T05:00:00+00:00",
        "date": _DAY + "T06:00:00+00:00",
        "photo_file_unique_id": _UID,
    }


def _counters() -> dict:
    return {k: 0 for k in (
        "inserted", "already_present", "skipped_no_caption", "skipped_ignored",
        "skipped_prefix", "skipped_contains", "unparseable",
        "multi_caption_album", "with_warnings", "media_relinked")}


class IngestMediaRelinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.media_root = self.root / "media"
        self.media_root.mkdir(parents=True)
        self.conn = open_db(self.root / "store.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _stored_media(self):
        r = self.conn.execute(
            "SELECT media_paths FROM alerts WHERE source_message_id=?", (_MSG,)
        ).fetchone()
        return json.loads(r[0]) if r else None

    def _make_photo(self):
        d = self.media_root / _DAY
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{_UID}.jpg").write_bytes(b"\xff\xd8\xff")  # minimal jpg-ish bytes

    def test_race_then_relink(self):
        # 1) photo not downloaded yet → ingest stores empty media (the race).
        c = _counters()
        ii._ingest_group(self.conn, [_row()], self.media_root, c, set())
        self.assertEqual(c["inserted"], 1)
        self.assertEqual(self._stored_media(), [])
        # 2) background download finishes → file lands on disk.
        self._make_photo()
        # 3) next ingest cycle re-links the now-present photo.
        ii._ingest_group(self.conn, [_row()], self.media_root, c, set())
        self.assertEqual(c["already_present"], 1)
        self.assertEqual(c["media_relinked"], 1)
        self.assertEqual(self._stored_media(), [f"media/{_DAY}/{_UID}.jpg"])

    def test_relink_is_one_time(self):
        # File present from the start → linked on insert, never re-written.
        self._make_photo()
        c = _counters()
        ii._ingest_group(self.conn, [_row()], self.media_root, c, set())
        self.assertEqual(self._stored_media(), [f"media/{_DAY}/{_UID}.jpg"])
        ii._ingest_group(self.conn, [_row()], self.media_root, c, set())
        # already-linked row must not trigger a relink every 5-min cycle.
        self.assertEqual(c["media_relinked"], 0)

    def test_no_file_no_relink(self):
        # Photo never arrives → row stays imageless, no spurious relink.
        c = _counters()
        ii._ingest_group(self.conn, [_row()], self.media_root, c, set())
        ii._ingest_group(self.conn, [_row()], self.media_root, c, set())
        self.assertEqual(c["media_relinked"], 0)
        self.assertEqual(self._stored_media(), [])


if __name__ == "__main__":
    unittest.main()
