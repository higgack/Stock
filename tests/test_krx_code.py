"""KRX 영문 혼합 단축코드(그린광학 0015G0, 2025-11 코스닥) — bot 경로 회귀 + 전수 가드.

2026-11-10: 일일 DM 이 그린광학을 '시세 미매칭'으로 올렸다. 상장사인데 코드가
`0015G0` 이라 숫자만 남기는 정규화가 `000150`(두산)을 만들고, `isdigit() and
len == 6` 판정은 국내 코드가 아니라고 봤다. 판정·정규화는 `bot.krx_code` 한 곳.
"""

import json
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from bot.krx_code import KRX_CODE_RE, is_krx_code, krx_code

ROOT = Path(__file__).resolve().parents[1]


class KrxCodeUnitTests(unittest.TestCase):
    def test_alnum_kept_not_collapsed(self):
        self.assertEqual(krx_code("0015G0"), "0015G0")
        self.assertNotEqual(krx_code("0015G0"), "000150")   # 두산
        self.assertEqual(krx_code("0015g0.KQ"), "0015G0")
        self.assertEqual(krx_code("A0015G0"), "0015G0")

    def test_legacy_inputs_still_normalize(self):
        self.assertEqual(krx_code("005930.KS"), "005930")
        self.assertEqual(krx_code("A005930"), "005930")
        self.assertEqual(krx_code(5930), "005930")
        self.assertEqual(krx_code(" (005930) "), "005930")
        self.assertEqual(krx_code("12345", pad=False), "")   # 사람이 친 값은 채우지 않는다
        self.assertEqual(krx_code("0015G0", pad=False), "0015G0")

    def test_rejects_non_codes(self):
        for bad in ("", None, "AAPL", "ABCDEF", "0015G", "0015G00", "000000", "12345X7"):
            self.assertEqual(krx_code(bad), "", bad)
        self.assertFalse(is_krx_code("ABCDEF"))     # 6자 영문 = 국내 코드 아님
        self.assertFalse(is_krx_code("0015g0"))     # 판정은 정확한 모양만
        self.assertTrue(is_krx_code("0015G0"))
        self.assertTrue(is_krx_code("005930"))

    def test_derivative_and_quarter_shapes_are_not_stock_codes(self):
        """ETF 구성종목의 코스피200 선물 `101V30` 이 영문 혼합 꼴이다 — `0` 으로 시작하지
        않으면 종목코드가 아니다(넓은 꼴은 선물에 종목 링크를 걸었다, 2026-11-10 회귀가 잡음)."""
        for bad in ("101V30", "201W12", "2026Q3", "9ABCDE"):
            self.assertFalse(is_krx_code(bad), bad)
            self.assertEqual(krx_code(bad), "", bad)
        from bot.world_sector_pages import holding_href
        self.assertEqual(holding_href("KR", "101V30"), "")
        self.assertNotEqual(holding_href("KR", "0015G0"), "")


class BotPathAlnumTests(unittest.TestCase):
    def test_ticker_market_alnum_is_kr(self):
        from bot.dashboard import _ticker_market
        self.assertEqual(_ticker_market("0015G0"), "KR")
        self.assertEqual(_ticker_market("AAPL"), "US")

    def test_lookup_resolves_bare_alnum_to_kr(self):
        from bot import dashboard as d
        with mock.patch("bot.market.normalize_kr_ticker_suffix",
                        side_effect=lambda t: t[:-3] + ".KQ"):
            self.assertEqual(d.resolve_name_to_ticker("0015G0"), "0015G0.KQ")

    def test_daju_caption_with_alnum_code(self):
        from bot import daju_parse as dp
        self.assertTrue(dp._STOCK_RE.match("📌 1. 그린광학 (0015G0)"))
        self.assertTrue(dp._TOM_ITEM_RE.match("· 그린광학 (0015G0) 10:00"))

    def test_fav_alert_ticker_regex(self):
        from bot import dart_fav_alerts as fa
        self.assertEqual(fa._KR_TICKER_RE.match("0015G0.KQ").group(1), "0015G0")
        self.assertEqual(fa._KR_TICKER_RE.match("005930").group(1), "005930")

    def test_naver_autocomplete_alnum_is_kr_not_us(self):
        """네이버가 KOSDAQ 종목을 `0015G0` 으로 주면 옛 판은 '숫자가 아님 → 미국 티커'로 분류했다."""
        from bot import portfolio_resolve as pr

        class _R:
            def json(self):
                return {"items": [[{"name": "그린광학", "code": "0015G0",
                                    "typeCode": "KOSDAQ"}]]}

        with mock.patch.object(pr, "_naver_cache_load", return_value={}), \
             mock.patch.object(pr, "_naver_cache_save"), \
             mock.patch("requests.get", return_value=_R()):
            self.assertEqual(pr.resolve_via_naver("그린광학"), ("0015G0.KQ", "KR"))

    def test_alnum_code_without_market_is_not_called_us(self):
        """시장 표기가 빈 항목 — 옛 판은 '숫자가 아님 + ASCII' 라 영문 혼합 국내 코드를 미국 티커로 만들었다."""
        from bot import portfolio_resolve as pr

        class _R:
            def json(self):
                return {"items": [{"name": "그린광학", "code": "0015G0", "typeCode": ""}]}

        with mock.patch.object(pr, "_naver_cache_load", return_value={}), \
             mock.patch.object(pr, "_naver_cache_save"), \
             mock.patch("requests.get", return_value=_R()):
            self.assertIsNone(pr.resolve_via_naver("그린광학"))

    def test_naver_cache_key_is_versioned(self):
        """옛 판이 구운 `{"v": ["0015G0","US"]}` 를 7일 동안 다시 쓰지 않는다(#18)."""
        import time
        from bot import portfolio_resolve as pr
        old = {pr._normalize("그린광학"): {"ts": time.time(), "v": ["0015G0", "US"]}}

        class _R:
            def json(self):
                return {"items": [{"name": "그린광학", "code": "0015G0", "typeCode": "KOSDAQ"}]}

        with mock.patch.object(pr, "_naver_cache_load", return_value=old), \
             mock.patch.object(pr, "_naver_cache_save"), \
             mock.patch("requests.get", return_value=_R()):
            self.assertEqual(pr.resolve_via_naver("그린광학"), ("0015G0.KQ", "KR"))


def _tracked_py():
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "bot", "trade"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [p for p in out if p.endswith(".py") and "/tests/" not in p]


# 국내 종목코드가 **아닌** 6자리 자리(날짜·HS코드·파일명) — 이유와 함께(#24).
_ALLOW = {
    ("bot/wisereport_earnings.py", "if len(s) == 6 and s.isdigit():"): "YYYYMM 날짜",
    ("bot/fred_boards.py", "if len(t) == 6 and t.isdigit():"): "ECOS YYYYMM",
    ("bot/macro_snapshot.py", "if len(s) == 6 and s.isdigit():             # ECOS 월 (202604)"): "ECOS YYYYMM",
    ("bot/macro_snapshot.py", "elif len(s) == 6 and s.isdigit():               # 202604"): "ECOS YYYYMM",
    ("bot/macro_cadence.py", "elif len(s) == 6 and s.isdigit():                 # 202607 (ECOS 월간)"): "ECOS YYYYMM",
    ("bot/kind_ir_client.py", '_CODE_RE = re.compile(r"\\b(\\d{6})\\b")'): "자유 텍스트 — 넓히면 2026Q3 가 잡힌다",
    ("bot/jpx_flow_client.py", "r'[^\"]+/stock_val_1_(\\d{6})\\.xls)\"'"): "JPX 파일명 날짜",
    ("trade/scripts/catalog_guard.py", 'm = re.search(r"(20\\d{2}_MTIHSK[^\\s]*\\.xlsx|v[A-Za-z]*_?\\d{6})", ln)'): "카탈로그 파일명",
    ("trade/scripts/build_hs_names.py", 'm = re.search(r"(20\\d{6})", os.path.basename(path))'): "파일명 날짜",
    ("trade/bot.py", 'c = "".join(ch for ch in data.split(":", 1)[1].strip() if ch.isdigit())'): "HS 코드",
    ("trade/bot.py", 'code = "".join(ch for ch in raw.strip() if ch.isdigit())'): "HS 코드",
}
_BAD = re.compile(
    r"(\w+)\.isdigit\(\) and len\(\1\) == 6|len\((\w+)\) == 6 and \2\.isdigit\(\)"
    r"|\\d\{6\}|if ch\.isdigit\(\)\)")


_ALLOW_PREFIX = {
    ("bot/dashboard_server.py", 'r"^\\d{6}_'): "아카이브 파일명 YYMMDD_*.json",
    ("bot/dashboard_server.py", '"realestate_archive", r"^\\d{6}_'): "아카이브 파일명",
    ("bot/dashboard_server.py", '"cheongyak_archive", r"^\\d{6}_'): "아카이브 파일명",
    ("bot/dashboard_server.py", '"screen_archive", r"^\\d{6}_'): "아카이브 파일명",
    ("bot/dashboard_server.py", 'if not _re_'): "아카이브 파일명",
}


class NoDigitOnlyKrxCodeGuard(unittest.TestCase):
    """숫자 6자리만 국내 코드로 보는 판정·숫자만 남기는 정규화를 전수로 막는다 —
    새 자리는 `bot.krx_code` 를 쓰거나, 코드가 아니면 이유와 함께 _ALLOW 에 둔다."""

    def test_no_new_digit_only_code_sites(self):
        hits, seen = [], set()
        for p in _tracked_py():
            if p == "bot/krx_code.py":
                continue   # 정의 모듈(독스트링이 옛 패턴을 인용한다)
            for ln in (ROOT / p).read_text(encoding="utf-8").splitlines():
                if _BAD.search(ln) and not ln.lstrip().startswith("#"):
                    k = (p, ln.strip())
                    if k in _ALLOW:
                        seen.add(k)
                    elif any(p == ap and ln.strip().startswith(pre) and "\\d{6}_[" in ln
                             for ap, pre in _ALLOW_PREFIX):
                        pass
                    else:
                        hits.append(f"{p}: {ln.strip()}")
        self.assertEqual(hits, [], "숫자 전용 KRX 코드 판정 — bot.krx_code 를 쓸 것")
        self.assertEqual(set(_ALLOW) - seen, set(), "낡은 allowlist 항목")

    def test_guard_fires(self):
        self.assertTrue(_BAD.search("if code.isdigit() and len(code) == 6:"))
        self.assertTrue(_BAD.search('c = "".join(ch for ch in s if ch.isdigit())'))
        self.assertTrue(_BAD.search('re.fullmatch(r"\\d{6}", t)'))

    def test_trade_js_regex_matches_python_shape(self):
        src = (ROOT / "trade/dashboard.py").read_text(encoding="utf-8")
        self.assertIn("/^" + KRX_CODE_RE + "$/.test(s)", src)


if __name__ == "__main__":
    unittest.main()
