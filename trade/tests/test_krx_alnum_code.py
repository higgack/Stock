"""KRX 영문 혼합 단축코드(그린광학 0015G0, 2025-11 코스닥) — trade 경로 회귀.

2026-11-10 일일 DM `시세 미매칭 신규 관련종목 — 그린광학` 의 뿌리. 이 경로는
종목코드를 **숫자만 남겨** 정규화했다: `0015G0` → `00150` → `000150`(두산).
마스터 빌드에선 두 회사가 한 코드 키로 dedup 되어 하나가 사라지고, `/map` 은
5자리라며 거부했다. 정규화는 `bot.krx_code.krx_code` 단일 출처를 쓴다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade import price_provider as pp
from trade import stock_link as sl


def _resp(items):
    return {"response": {"body": {"items": {"item": items}}}}


class FetchMasterAlnumTests(unittest.TestCase):
    def test_alnum_code_does_not_collapse_onto_doosan(self):
        """두산 000150 과 그린광학 0015G0 이 같은 페이지에 오면 둘 다 남아야 한다
        (옛 판은 0015G0 → 000150 으로 키가 겹쳐 한 회사가 빠졌다)."""
        page = [
            {"srtnCd": "000150", "itmsNm": "두산", "basDt": "20261106"},
            {"srtnCd": "0015G0", "itmsNm": "그린광학", "basDt": "20261106"},
        ]
        calls = []

        def transport(method, url, headers=None, body=None):
            calls.append(url)
            return _resp(page if len(calls) == 1 else [])

        with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"}):
            got = pp.fetch_krx_master(transport=transport, max_pages=3)
        self.assertEqual(got.get("두산"), "000150")
        self.assertEqual(got.get("그린광학"), "0015G0")

    def test_krx_a_prefix_and_bare_digits_still_normalize(self):
        page = [{"srtnCd": "A005930", "itmsNm": "삼성전자", "basDt": "20261106"},
                {"srtnCd": "5380", "itmsNm": "현대차", "basDt": "20261106"}]
        n = []

        def transport(method, url, headers=None, body=None):
            n.append(1)
            return _resp(page if len(n) == 1 else [])

        with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"}):
            got = pp.fetch_krx_master(transport=transport, max_pages=3)
        self.assertEqual(got, {"삼성전자": "005930", "현대차": "005380"})


class MasterVersionTests(unittest.TestCase):
    """옛 판(숫자만 남기기)이 구운 마스터는 7일을 기다리지 않고 다시 만든다(#21b)."""

    def setUp(self):
        from trade.scripts import build_krx_codes as bk
        self.bk = bk
        self.tmp = tempfile.TemporaryDirectory()
        self.master = Path(self.tmp.name) / "krx_codes.json"
        self._p = mock.patch.object(pp, "_KRX_MASTER", self.master)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.tmp.cleanup()

    def test_fresh_master_without_version_is_stale(self):
        self.master.write_text('{"두산": "000150"}', encoding="utf-8")   # 방금 만든 옛 판 마스터
        self.assertTrue(self.bk._is_stale())

    def test_failed_build_backs_off_an_hour(self):
        """판이 달라 재빌드하는데 원천이 0건이면 5분마다 전수 페이지를 다시 치지 않는다(#303)."""
        with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"}), \
             mock.patch.object(pp, "fetch_krx_master", return_value={}) as f:
            self.bk.run(if_stale=True)
            self.bk.run(if_stale=True)
        self.assertEqual(f.call_count, 1)
        self.assertTrue(self.master.with_suffix(".fail").exists())
        with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"}), \
             mock.patch.object(pp, "fetch_krx_master", return_value={"그린광학": "0015G0"}):
            self.bk.run()                       # 수동 실행은 백오프를 무시하고, 성공하면 실패 표식을 지운다
        self.assertFalse(self.master.with_suffix(".fail").exists())

    def test_build_writes_version_and_then_is_fresh(self):
        with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"}), \
             mock.patch.object(pp, "fetch_krx_master", return_value={"그린광학": "0015G0"}):
            self.assertEqual(self.bk.run(), 0)
        self.assertEqual(self.master.with_suffix(".ver").read_text(), str(pp.KRX_MASTER_VER))
        self.assertFalse(self.bk._is_stale())


class QuoteAndOverrideAlnumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._o = mock.patch.object(pp, "_OVERRIDES_PATH",
                                    Path(self.tmp.name) / "ov.json")
        self._o.start()

    def tearDown(self):
        self._o.stop()
        self.tmp.cleanup()

    def test_set_override_keeps_alnum_code(self):
        pp.set_override("그린광학", "0015G0")
        self.assertEqual(pp.list_overrides(), {"그린광학": "0015G0"})

    def test_set_override_rejects_non_code(self):
        pp.set_override("무명사", "12345X7")
        self.assertEqual(pp.list_overrides(), {})

    def test_dp_item_and_pick_keep_alnum(self):
        it = {"srtnCd": "0015G0", "itmsNm": "그린광학", "clpr": "30000",
              "vs": "100", "fltRt": "0.33", "basDt": "20261106"}
        q = pp._quote_from_dp_item(it)
        self.assertEqual(q.symbol, "0015G0")
        doosan = dict(it, srtnCd="000150", itmsNm="두산")
        self.assertEqual(pp._dp_pick([doosan, it], code="0015G0").symbol, "0015G0")
        self.assertEqual(pp._dp_pick([it], code="000150"), None)

    def test_norm_and_kis_url_keep_alnum(self):
        self.assertEqual(pp._norm(["0015G0", "000150", "0015g0.KQ", "", "x"]),
                         ["0015G0", "000150"])
        seen = []

        def transport(method, url, headers=None, body=None):
            seen.append(url)
            return {}

        with mock.patch.object(pp, "_kis_keys", return_value=("k", "s")):
            pp._kis_quote_kr("0015G0", "tok", transport=transport)
            pp._naver_quote_kr("0015G0", transport=transport)
        self.assertIn("fid_input_iscd=0015G0", seen[0])
        self.assertIn("0015G0", seen[1])
        self.assertNotIn("000150", "".join(seen))

    def test_krx_name_for_alnum(self):
        with mock.patch.object(pp, "_load_krx_master",
                               return_value={"두산": "000150", "그린광학": "0015G0"}):
            self.assertEqual(pp.krx_name_for("0015G0"), "그린광학")
            self.assertEqual(pp.krx_name_for("000150"), "두산")


class StockLinkAlnumTests(unittest.TestCase):
    def test_kr_board_links_alnum_code(self):
        self.assertEqual(sl.lookup_query("0015G0", "그린광학", local="KR"), "0015G0")

    def test_alnum_needs_market_or_master(self):
        """모양만으로는 KR 로 못 정한다 — 대만 액티브 ETF(00981A 류)도 같은 꼴이다."""
        self.assertEqual(sl.lookup_query("00981A", "", local="TW"), "")
        self.assertEqual(
            sl.lookup_query("0015G0", "그린광학", kr_master={"그린광학": "0015G0"}),
            "0015G0")


if __name__ == "__main__":
    unittest.main()
