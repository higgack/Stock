"""BeOn 재포워드 차단 — 반복 메시지 한 번 탭으로 영구 차단(사용자 2026-06-15
'형식 안 맞는 거 두 시간마다 계속 옴'). 유저-포워드(dedup 불가) 메시지가 매 틱
재포워드되던 루프를, '동기화 완료' 알림의 🚫 버튼 → beon_skip set → backfill
후보 제외로 종료."""
import importlib
import os
import tempfile
import unittest


class BeonSkipModuleTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._old = os.environ.get("TRADE_DATA_DIR")
        os.environ["TRADE_DATA_DIR"] = self.d
        import trade.beon_skip as bs
        self.bs = importlib.reload(bs)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TRADE_DATA_DIR", None)
        else:
            os.environ["TRADE_DATA_DIR"] = self._old

    def test_load_add_contains(self):
        self.assertEqual(self.bs.load(), set())
        self.assertFalse(self.bs.contains(123))
        self.assertEqual(self.bs.add(["111", "222"]), 2)
        self.assertTrue(self.bs.contains(111))
        self.assertTrue(self.bs.contains("222"))
        self.assertFalse(self.bs.contains(999))

    def test_dedup_and_single(self):
        self.bs.add(111)                 # 단일 int
        self.assertEqual(self.bs.add(111), 1)   # 중복 → 그대로
        self.assertEqual(self.bs.add([222]), 2)

    def test_graceful_corrupt(self):
        self.bs.add([1])
        open(self.bs._path(), "w").write("not json")   # 손상
        self.assertEqual(self.bs.load(), set())          # graceful 빈 set
        self.assertFalse(self.bs.contains(1))


class BeonSkipWiringTests(unittest.TestCase):
    """배선 E2E(추측 #12c): backfill 후보 제외 + 버튼 emit + 봇 콜백 등록."""

    def test_backfill_checks_skip_and_emits_button(self):
        src = open("trade/scripts/backfill_beon.py", encoding="utf-8").read()
        assert "from trade import beon_skip as _beon_skip" in src
        assert "_beon_skip.contains(msg.id)" in src        # 후보에서 제외
        assert "beon_skip:" in src                          # 버튼 callback_data
        assert "fallback_ids" in src                        # 재포워드-위험만 버튼에

    def test_bot_registers_callback(self):
        src = open("trade/bot.py", encoding="utf-8").read()
        assert "async def on_beon_skip_callback" in src
        assert 'pattern=r"^beon_skip:"' in src              # 핸들러 등록
        assert "beon_skip.add(ids)" in src                  # set 에 추가


if __name__ == "__main__":
    unittest.main()
