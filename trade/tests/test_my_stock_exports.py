"""말레이시아 수출(종목별) 파서 회귀 — 나쁜양파.

2026-08-20 사용자: "말레이시아 수출 7월이 떴는데 업종+기업이 아니라 기업으로
나와서 수집이 안된것 같아." 품목 파서(`my_exports`)의 마커는 `N월 수출
말레이시아` 인데 이 포맷은 `말레이시아 수출`(어순 반대)이라 관련성 필터를
통과 못 하고 **조용히 드랍**되고 있었다 — 일본이 2026-08-16 에 겪은 것과
같은 사고.

⚠️ 아래 픽스처는 사용자가 첨부한 **렌더된 스크린샷** 기반이다(jp_stock 처럼
Telethon 실측 원문이 아님). 그래서 파서는 헤더 줄 구성을 단정하지 않고
관용 파싱하며, 여기서도 세 줄/한 줄 두 변형을 모두 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from trade import my_stock_exports as mys

# 사용자 2026-08-20 캡처(TXN) — 이 소스에만 있는 수준값 2종 + 관련 매출.
_TXN = (
    "Texas Instruments Incorporated (TXN)\n말레이시아 수출\n26년 7월 Update\n\n"
    "수출액 YoY: +170.4%\n3M 수출액 YoY: +127.1%\n\n"
    "동시상관: 0.93\n방향 일치율: 67%\n\n"
    "- CY26Q2 매출 $5.46B(+22.8% YoY)\n\n"
    "https://badonion.co.kr/trade/mapping")


class ParseTests(unittest.TestCase):
    def test_screenshot_caption(self):
        d = mys.parse_my_stock_export(_TXN)
        self.assertEqual(d["ticker"], "TXN")
        self.assertEqual(d["stock_name"], "Texas Instruments Incorporated")
        self.assertEqual(d["month"], "2026-07")
        self.assertEqual(d["export_yoy"], 170.4)
        self.assertEqual(d["export_yoy_3m"], 127.1)
        self.assertEqual(d["corr"], 0.93)
        self.assertEqual(d["dir_hit"], 67.0)
        self.assertEqual(d["revenue"], "CY26Q2 매출 $5.46B(+22.8% YoY)")
        # 매출 줄이 품목 설명으로 승격되면 안 된다(카드 📦 슬롯 오염).
        self.assertIsNone(d["item"])

    def test_revenue_line_without_dash_is_not_promoted_to_item(self):
        # `_RE_REVENUE` 는 선행 `-/•/*` 를 요구하므로 대시 없는 매출 줄은
        # revenue 로 안 잡힌다. 이때 `_SKIP_LINE` 의 `매출` 이 없으면 그 줄이
        # **품목 설명(📦)으로 승격**돼 카드가 엉뚱한 걸 보여준다.
        # ⚠️ 픽스처를 고를 때 **다른 키워드에 가리지 않게** 해야 한다 —
        # 대시가 있으면 `^[-•*]` 가, YoY 가 있으면 `YoY` 가 먼저 잡아서
        # `매출` 을 지워도 테스트가 통과했다(뮤테이션 X3 가 두 번 통과).
        # 셋 다 없는 조합이라야 이 키워드가 실제로 일한다.
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +1.0%\n\n"
            "CY26Q2 매출 $5.46B")
        self.assertIsNone(d["item"], "매출 줄이 품목으로 승격됐다")
        self.assertIsNone(d["revenue"])

    def test_bold_markdown_does_not_break_header(self):
        d = mys.parse_my_stock_export(
            "**Texas Instruments Incorporated (TXN)**\n**말레이시아 수출**\n"
            "**26년 7월 Update**\n\n수출액 YoY: +170.4%")
        self.assertEqual((d["ticker"], d["month"]), ("TXN", "2026-07"))

    def test_one_line_header_variant_also_accepted(self):
        # 원문 마크다운 미확인이라 일본식 한 줄 헤더도 받는다(관용 파싱).
        d = mys.parse_my_stock_export(
            "Foo Corp (5347) 말레이시아 수출 Update\n26년 7월\n\n수출액 YoY: +5.0%")
        self.assertEqual((d["ticker"], d["stock_name"], d["month"]),
                         ("5347", "Foo Corp", "2026-07"))

    def test_3m_prefix_is_not_confused_with_monthly(self):
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n"
            "3M 수출액 YoY: +127.1%\n수출액 YoY: +170.4%")
        self.assertEqual(d["export_yoy"], 170.4)
        self.assertEqual(d["export_yoy_3m"], 127.1)

    def test_missing_metric_is_none_not_zero(self):
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +1.0%")
        for k in ("export_yoy_3m", "price_yoy", "corr", "dir_hit", "revenue"):
            self.assertIsNone(d[k], k)

    def test_header_must_be_one_line_per_part(self):
        # `\s` 가 개행을 먹으면 무관한 조합이 통과한다(jp_stock 실측 함정).
        self.assertIsNone(mys.parse_my_stock_export(
            "어떤회사\n(ABCD) 말레이시아 수출\n26년 7월"))

    def test_scattered_markers_are_rejected(self):
        self.assertIsNone(mys.parse_my_stock_export(
            "말레이시아 수출 관련 잡담\n\n26년 7월에 좋았다"))

    def test_invalid_month_rejected(self):
        self.assertIsNone(mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 13월\n\n수출액 YoY: +1.0%"))

    def test_metrics_do_not_leak_across_companies(self):
        # 한 메시지에 두 회사가 담기면 A 카드에 B 값이 섞이면 안 된다.
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +1.0%\n\n"
            "B (BBB)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +9.9%\n동시상관: 0.11")
        self.assertEqual(d["ticker"], "AAA")
        self.assertEqual(d["export_yoy"], 1.0)
        self.assertIsNone(d["corr"], "B 의 동시상관이 A 카드로 샜다")

    def test_does_not_claim_other_sources(self):
        from trade import badonion_sources as srcs
        for other in (
            "6월 수출 말레이시아\n\n▶️ 액화천연가스\n\n"
            "26년06월: $1.0M  (+1.0% YoY)  (+1.0% MoM)",
            "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +260.2%",
            "Kioxia (285A) 일본 수출 Update\n26년 6월\n\n수출액: YoY +95.1%",
        ):
            self.assertIsNone(mys.parse_my_stock_export(other), other[:30])
        # 반대로 우리 캡션을 남이 주장하면 안 된다
        hits = [s.key for s in srcs.SOURCES if s.parse(_TXN) is not None]
        self.assertEqual(hits, ["mys"], hits)


class StoreTests(unittest.TestCase):
    def _conn(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        c = mys.open_my_stock_db(Path(tmp.name) / "my_stock.db")
        self.addCleanup(c.close)
        return c

    def test_monthly_rolling_replaces_card_and_keeps_history(self):
        c = self._conn()
        for mo, v in (("26년 7월", 170.4), ("26년 8월", 12.0)):
            mys.ingest(c, f"A (AAA)\n말레이시아 수출\n{mo}\n\n수출액 YoY: +{v}%",
                       source_message_id=1, posted_at="", media_paths=None)
        cards = mys.list_my_stock(c)
        self.assertEqual([(r["ticker"], r["month"]) for r in cards],
                         [("AAA", "2026-08")], "최신월 1장으로 교체되어야")
        self.assertEqual(len(mys.history(c, "AAA")), 2, "히스토리는 보존")

    def test_partial_resend_does_not_null_existing_fields(self):
        c = self._conn()
        mys.ingest(c, _TXN, source_message_id=1, posted_at="", media_paths=None)
        mys.ingest(c, "Texas Instruments Incorporated (TXN)\n말레이시아 수출\n"
                      "26년 7월\n\n수출액 YoY: +171.0%",
                   source_message_id=2, posted_at="", media_paths=None)
        r = mys.list_my_stock(c)[0]
        self.assertEqual(r["export_yoy"], 171.0, "새 값은 반영")
        self.assertEqual(r["corr"], 0.93, "빠진 필드는 기존 값 보존")
        self.assertEqual(r["revenue"], "CY26Q2 매출 $5.46B(+22.8% YoY)")

    def test_render_html_works_empty_and_populated(self):
        c = self._conn()
        empty = mys.render_html(c)
        self.assertIn("아직 수집된", empty)          # nav 404 방지용 빈 페이지
        mys.ingest(c, _TXN, source_message_id=1, posted_at="", media_paths=None)
        html = mys.render_html(c)
        self.assertIn("Texas Instruments Incorporated", html)
        self.assertIn("TXN", html)
        # 수준값은 부호·화살표 없이(실수 #39) — '+0.93 ▲' 로 그리면 안 된다.
        self.assertIn("0.93", html)
        self.assertNotIn("+0.93", html)
        self.assertIn("67%", html)
        self.assertNotIn("+67.0%", html)
        # 변화율은 부호·화살표 유지
        self.assertIn("▲+170.4%", html)
        # 관련 매출은 원문 그대로
        self.assertIn("CY26Q2 매출 $5.46B(+22.8% YoY)", html)


if __name__ == "__main__":
    unittest.main()
