"""일본 수출(종목별) 파서 회귀 — 나쁜양파.

2026-08-16 `diagnose_badonion.py` 실측으로 **조용한 유실**을 발견해 만든
소스다: 이 포맷은 기존 jp2(품목 기준) 파서를 통과하지 못해 관련성 필터에서
통째로 드랍되고 있었다. 아래 픽스처는 전부 **실제 채널 캡션 원문**이다
(스크린샷 재구성본이 아님) — 파서를 고칠 때 이 원문이 기준이다.
"""

import tempfile
import unittest
from pathlib import Path

from trade import jp_stock_exports as jps

# ── 실측 원문(Telethon `.text` — 볼드가 `**` 로 되붙은 형태) ──
_LUMENTUM = (
    "Lumentum (LITE) 일본 수출 Update\n26년 6월\n\nEML·DML·CW 레이저소자\n\n"
    "수출액: YoY +446.7% 🔻\n3M 수출액: YoY +537.3% 🔺\n단가: YoY +367.5% 🔺\n\n"
    "https://badonion.co.kr/trade/mapping\n\n"
    "한국 미국 일본 홍콩 등 700개 이상 기업 맵핑 (계속 추가중)")
_KIOXIA = (
    "**Kioxia (285A) 일본 수출 Update**\n**26년 6월**\n\n"
    "**Yokkaichi·Kitakami NAND 웨이퍼·다이·패키지형 플래시 메모리**\n\n"
    "수출액: YoY +95.1% 🔻\n3M 수출액: YoY +103.9% 🔺\n단가: YoY +104.7% 🔻\n\n"
    "* SanDisk와 동일한 공동생산 흐름이므로 두 지표를 합산하지 않습니다.\n\n"
    "https://badonion.co.kr/trade/mapping")
_ADVANTEST = (
    "**Advantest (6857) 일본 수출 Update**\n**26년 6월**\n\n"
    "**반도체 테스트 시스템·핸들러·테스트 장비 부품\n**\n"
    "수출액: YoY +76.4% 🔻\n3M 수출액: YoY +75.5% 🔺\n\n"
    "https://badonion.co.kr/trade/mapping")


class ParseTests(unittest.TestCase):
    def test_real_caption_plain(self):
        d = jps.parse_jp_stock_export(_LUMENTUM)
        self.assertEqual(d["ticker"], "LITE")
        self.assertEqual(d["stock_name"], "Lumentum")
        self.assertEqual(d["month"], "2026-06")
        self.assertEqual(d["item"], "EML·DML·CW 레이저소자")
        self.assertEqual(d["export_yoy"], 446.7)
        self.assertEqual(d["export_yoy_3m"], 537.3)
        self.assertEqual(d["price_yoy"], 367.5)

    def test_bold_markdown_does_not_break_header(self):
        # Telethon 이 entities 로 볼드를 되붙여 `**` 가 실제로 들어온다.
        d = jps.parse_jp_stock_export(_KIOXIA)
        self.assertIsNotNone(d, "볼드 캡션이 통째로 드랍(실측 포맷)")
        self.assertEqual((d["ticker"], d["month"]), ("285A", "2026-06"))

    def test_footnote_marker_survives_bold_stripping(self):
        # `*` 를 전부 지우면 각주까지 사라진다 — `**` 만 걷어내야 한다.
        # 이 각주는 '두 지표를 합산하지 말라'는 **해석에 필수인 경고**다.
        d = jps.parse_jp_stock_export(_KIOXIA)
        self.assertIn("합산하지 않습니다", d["note"] or "")
        self.assertIn("SanDisk", d["note"] or "")

    def test_missing_metric_is_none_not_zero(self):
        # Advantest 캡션엔 단가가 없다 — 0 으로 채우면 '단가 0%' 라는 없는
        # 사실을 만든다.
        d = jps.parse_jp_stock_export(_ADVANTEST)
        self.assertIsNone(d["price_yoy"])
        self.assertEqual(d["export_yoy"], 76.4)

    def test_3m_prefix_is_not_confused_with_monthly(self):
        # 두 값이 같아지면 카드가 거짓말을 한다(한국판에서 실제로 났던 버그).
        d = jps.parse_jp_stock_export(_LUMENTUM)
        self.assertNotEqual(d["export_yoy"], d["export_yoy_3m"])
        # 공백 없는 '3M수출액' 변종도 당월치로 새면 안 된다
        d2 = jps.parse_jp_stock_export(
            "A (1234) 일본 수출 Update\n26년 6월\n\n3M수출액: YoY +9.9%")
        self.assertIsNone(d2["export_yoy"])
        self.assertEqual(d2["export_yoy_3m"], 9.9)

    def test_us_and_jp_tickers_both_accepted(self):
        # 일본에서 수출하는 기업이라 국적이 섞인다 — 6자리 숫자 가정 금지.
        for cap, tk in ((_LUMENTUM, "LITE"), (_KIOXIA, "285A"),
                        (_ADVANTEST, "6857")):
            self.assertEqual(jps.parse_jp_stock_export(cap)["ticker"], tk)

    def test_scattered_markers_are_rejected(self):
        # 마커가 캡션 여기저기 흩어진 무관 메시지가 통과하면, 이 파서가
        # 억제 필터로도 쓰이므로 **저장도 안 되고 알림에도 안 잡히는** 유실이
        # 된다(kr_stock_exports 와 동일한 계약).
        for bad in (
            "",
            "한국 미국 일본 홍콩 등 700개 이상 기업 맵핑 (계속 추가중)",
            "일본 수출이 늘었다는 기사\n\n어떤 기업 (ABCD) 이 언급됨\n26년 6월",
            "Kioxia (285A) 관련 잡담\n\n일본 수출 Update 라는 표현\n26년 6월",
        ):
            self.assertIsNone(jps.parse_jp_stock_export(bad), repr(bad[:40]))

    def test_header_must_be_one_line(self):
        # `\\s` 는 개행을 먹는다 — 회사명과 티커가 **다른 줄**인 무관 조합이
        # 통과하면 빈 행이 저장되고, is_relevant 가 미매칭 알림까지 눌러
        # 조용한 유실이 된다(독립 리뷰 실측).
        self.assertIsNone(jps.parse_jp_stock_export(
            "어떤회사\n(ABCD) 일본 수출 Update\n26년 6월\n\n수출액: YoY +1.0%"))
        self.assertIsNone(jps.parse_jp_stock_export(
            "어떤회사 (ABCD)\n일본 수출 Update\n26년 6월\n\n수출액: YoY +1.0%"))
        # 정상(한 줄)은 그대로 통과해야 한다
        self.assertIsNotNone(jps.parse_jp_stock_export(
            "어떤회사 (ABCD) 일본 수출 Update\n26년 6월\n\n수출액: YoY +1.0%"))

    def test_metrics_do_not_leak_across_companies(self):
        # 한 메시지에 두 회사가 담기면 A 카드에 B 의 단가·각주가 섞인다
        # (독립 리뷰 실측 — **틀린 숫자가 조용히 저장**). 헤더 구간으로 자른다.
        a = ("Alpha (AAAA) 일본 수출 Update\n26년 6월\n\n품목A\n\n"
             "수출액: YoY +10.0%")
        b = ("Beta (BBBB) 일본 수출 Update\n26년 6월\n\n품목B\n\n"
             "수출액: YoY +20.0%\n단가: YoY +99.9%\n\n* 합산하지 마세요")
        d = jps.parse_jp_stock_export(a + "\n\n" + b)
        self.assertEqual(d["ticker"], "AAAA")
        self.assertEqual(d["export_yoy"], 10.0)
        self.assertIsNone(d["price_yoy"], "B 의 단가가 A 카드로 샘")
        self.assertIsNone(d["note"], "B 의 각주가 A 카드로 샘")
        self.assertEqual(d["item"], "품목A")

    def test_odd_asterisk_run_is_not_promoted_to_note(self):
        # `**` 만 지우면 `***중요***` 가 `*중요*` 로 남아 각주로 승격돼
        # 카드의 ⚠️ 해석경고 슬롯을 차지한다(독립 리뷰 실측).
        d = jps.parse_jp_stock_export(
            "A (1234) 일본 수출 Update\n26년 6월\n\n***중요***\n수출액: YoY +1.0%")
        self.assertIsNone(d["note"])
        # 진짜 각주('* ' 형태)는 여전히 잡혀야 한다
        self.assertIn("합산", jps.parse_jp_stock_export(_KIOXIA)["note"])

    def test_invalid_month_rejected(self):
        self.assertIsNone(jps.parse_jp_stock_export(
            "A (1234) 일본 수출 Update\n26년 13월\n\n수출액: YoY +1.0%"))

    def test_does_not_claim_other_sources(self):
        # 한국 종목별·대만 품목 캡션을 가로채면 저장처가 바뀐다.
        for other in (
            "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +260.2%",
            "6월 수출 대만\n\n▶️ 테스트\n\n26년06월: $9.1M  (+3.0% YoY)",
        ):
            self.assertIsNone(jps.parse_jp_stock_export(other))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = jps.open_jp_stock_db(Path(self.tmp.name) / "jp_stock.db")
        self.addCleanup(self.conn.close)

    def test_monthly_rolling_replaces_card_and_keeps_history(self):
        # 7월분이 오면 카드는 7월로 바뀌고 6월은 히스토리에 남는다.
        jps.ingest(self.conn, _KIOXIA)
        jul = _KIOXIA.replace("**26년 6월**", "**26년 7월**").replace(
            "수출액: YoY +95.1%", "수출액: YoY +120.0%")
        jps.ingest(self.conn, jul)
        cards = jps.list_jp_stock(self.conn)
        self.assertEqual([(c["ticker"], c["month"], c["export_yoy"])
                          for c in cards], [("285A", "2026-07", 120.0)])
        self.assertEqual([h["month"] for h in jps.history(self.conn, "285A")],
                         ["2026-06", "2026-07"])

    def test_partial_resend_does_not_null_existing_fields(self):
        jps.ingest(self.conn, _KIOXIA)
        # 단가·각주가 빠진 재전송 — 기존 값이 보존돼야(공용 upsert 규약)
        jps.ingest(self.conn,
                   "**Kioxia (285A) 일본 수출 Update**\n**26년 6월**\n\n"
                   "수출액: YoY +95.1%")
        r = jps.list_jp_stock(self.conn)[0]
        self.assertEqual(r["price_yoy"], 104.7)
        self.assertIn("합산하지 않습니다", r["note"])

    def test_render_html_works_empty_and_populated(self):
        # 빈 상태에서도 페이지를 만들어야 nav 링크가 404 가 되지 않는다.
        empty = jps.render_html(self.conn)
        self.assertIn("아직 수집된", empty)
        jps.ingest(self.conn, _KIOXIA)
        html = jps.render_html(self.conn)
        self.assertIn("Kioxia", html)
        self.assertIn("285A", html)
        # 합산 금지 경고가 카드에 그대로 노출돼야 한다
        self.assertIn("합산하지 않습니다", html)
        # 품목 페이지(jp2)와 구분되는 안내가 있어야 혼동이 없다
        self.assertIn("jp2.html", html)


if __name__ == "__main__":
    unittest.main()
