"""중국 수출(종목별) 파서 회귀 — 나쁜양파.

2026-08-21 사용자: "나쁜 양파에 7월 중국 수출(기업별)도 나왔어." 품목
파서(`cn_exports`)의 마커는 `N월 수출 중국` 인데 이 포맷은 `중국 수출`
(어순 반대)이라 관련성 필터를 통과 못 하고 **조용히 드랍**되고 있었다 —
일본 2026-08-16 · 말레이시아 2026-08-20 과 같은 사고의 **네 번째**.

⚠️ 아래 픽스처는 사용자가 첨부한 **렌더된 스크린샷** 기반이다(Telethon
실측 원문이 아님). 그래서 파서는 헤더 줄 구성을 단정하지 않고 관용
파싱하며, 여기서도 변형을 함께 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from trade import badonion_sources as srcs
from trade import cn_exports as cn
from trade import cn_stock_exports as cns

# 사용자 2026-08-21 캡처 ① — 단가까지 있고 동시·선행 **네 계열이 다 온다**.
_TTM = (
    "TTM Technologies (TTMI)\n중국 수출\n26년 7월 Update\n\n"
    "단가 YoY: +45.2%\n수출액 YoY: +55.8%\n3M 수출액 YoY: +84.7%\n\n"
    "동시상관: 0.74\n방향 일치율: 87%\n"
    "선행상관: 0.85\n선행 방향 일치율: 93%\n\n"
    "- AI 서버·네트워크용 고다층 PCB 수요와 신규 생산능력 램프가 겹치며 "
    "데이터센터 제품 믹스가 개선되는 구간입니다.\n\n"
    "https://badonion.co.kr/trade/mapping")

# 캡처 ② — 단가가 **없고**, 코멘트 안에 `매출` 이라는 단어가 들어 있다.
_DELL = (
    "Dell Technologies (DELL)\n중국 수출\n26년 7월 Update\n\n"
    "수출액 YoY: +299.5%\n3M 수출액 YoY: +262.5%\n\n"
    "동시상관: 0.89\n방향 일치율: 78%\n"
    "선행상관: 0.88\n선행 방향 일치율: 94%\n\n"
    "- 부품·재공품 재고가 완제품보다 먼저 늘어 AI 서버 생산 준비 성격이 "
    "강하며, 백로그의 매출 전환 속도가 다음 확인 포인트입니다.")


class ParseTests(unittest.TestCase):
    def test_screenshot_caption(self):
        d = cns.parse_cn_stock_export(_TTM)
        self.assertEqual(d["ticker"], "TTMI")
        self.assertEqual(d["stock_name"], "TTM Technologies")
        self.assertEqual(d["month"], "2026-07")
        self.assertEqual(d["amount_yoy"], 55.8)
        self.assertEqual(d["amount_yoy_3m"], 84.7)
        self.assertEqual(d["price_yoy"], 45.2)
        self.assertEqual(d["corr"], 0.74)
        self.assertEqual(d["dir_hit"], 87.0)
        self.assertEqual(d["lead_corr"], 0.85)
        self.assertEqual(d["lead_dir_hit"], 93.0)
        self.assertIn("고다층 PCB", d["comment"])
        # 코멘트가 품목 설명(📦)이나 매출(🏦) 칸으로 새면 안 된다.
        self.assertIsNone(d["revenue"])
        self.assertIsNone(d["item"])

    def test_comment_with_the_word_revenue_is_not_a_revenue_line(self):
        """DELL 실측 — 코멘트가 `매출` 이라는 단어를 품는다. 단어로 가르면
        문장이 금액 칸에 앉는다. 금액 토큰 유무로 가른다(구조 판정)."""
        d = cns.parse_cn_stock_export(_DELL)
        self.assertIsNone(d["revenue"], "코멘트가 매출 칸으로 샜다")
        self.assertIn("백로그의 매출 전환 속도", d["comment"])

    def test_revenue_and_comment_can_coexist(self):
        cap = (_DELL + "\n- CY26Q2 매출 $5.46B(+22.8% YoY)")
        d = cns.parse_cn_stock_export(cap)
        self.assertEqual(d["revenue"], "CY26Q2 매출 $5.46B(+22.8% YoY)")
        self.assertIn("백로그", d["comment"])

    def test_missing_metric_is_none_not_zero(self):
        d = cns.parse_cn_stock_export(_DELL)
        self.assertIsNone(d["price_yoy"], "없는 단가가 0 으로 저장되면 오보")

    def test_3m_prefix_is_not_confused_with_monthly(self):
        d = cns.parse_cn_stock_export(_TTM)
        self.assertNotEqual(d["amount_yoy"], d["amount_yoy_3m"])

    def test_bold_markdown_does_not_break_header(self):
        d = cns.parse_cn_stock_export("**TTM Technologies (TTMI)**\n" +
                                      _TTM.split("\n", 1)[1])
        self.assertEqual(d["ticker"], "TTMI")

    def test_bare_correlation_is_kept_but_not_claimed_coincident(self):
        """접두 없는 맨 `상관` — 버리지 않고 계열만 '미표기'로 밝힌다.

        ⚠️ 계약 정정(2026-08-29, #222). 옛 이름은
        `test_bare_correlation_is_not_guessed` 였고 "받지 않는다"를 못박고
        있었다 — VM 실측이 그 전제를 반증했다(원천이 실제로 접두 없이
        보낸다: ROHM 6963 · Tokyo Electron 8035 7월분). 버리면 그 자리가
        영원히 빈다(#171). 지키는 것은 그대로 — 선행 칸 오염 금지 + 동시라고
        단정 금지(#165).
        """
        cap = _TTM.replace("동시상관: 0.74", "상관: 0.74")
        d = cns.parse_cn_stock_export(cap)
        self.assertEqual(d["corr"], 0.74)
        self.assertEqual(d["corr_basis"], "미표기")
        # 이 픽스처엔 선행 계열도 함께 온다 — 미표기 값이 그 칸을 밀어내면
        # 안 된다(선행은 자기 값 0.85 를 그대로 지킨다).
        self.assertEqual(d["lead_corr"], 0.85, "미표기 상관이 선행 칸을 덮었다")
        self.assertEqual(d["dir_hit"], 87.0)
        self.assertEqual(d["lead_dir_hit"], 93.0)

    def test_metrics_do_not_leak_across_companies(self):
        """한 메시지에 두 회사가 담기면 A 카드에 B 값이 섞이면 안 된다.

        ⚠️ 순서가 중요하다. TTM 을 **앞**에 두면 TTM 이 모든 지표를 갖고
        있어 구간 제한을 없애는 뮤테이션이 그대로 통과한다(실측). 뒷 회사
        에만 있는 값이 앞 카드로 새는지 봐야 한다 — DELL 은 단가가 없고
        TTM 은 있으므로 DELL 을 앞에 둔다."""
        d = cns.parse_cn_stock_export(_DELL + "\n\n" + _TTM)
        self.assertEqual(d["ticker"], "DELL")
        self.assertEqual(d["amount_yoy"], 299.5)
        self.assertIsNone(d["price_yoy"], "뒷 회사의 단가가 앞 카드로 샜다")
        self.assertIn("백로그", d["comment"], "뒷 회사의 코멘트가 샜다")

    def test_invalid_month_rejected(self):
        self.assertIsNone(cns.parse_cn_stock_export(
            _TTM.replace("26년 7월", "26년 13월")))

    def test_header_must_be_one_line_per_part(self):
        """`\\s` 로 개행을 먹으면 무관 조합이 통과한다(jp_stock 실측 함정)."""
        self.assertIsNone(cns.parse_cn_stock_export(
            "어떤회사\n(ABCD) 중국 수출\n26년 7월"))

    def test_does_not_claim_other_sources(self):
        """형제 소스의 캡션을 가로채면 조용한 오저장이다."""
        for other in ("Texas Instruments Incorporated (TXN)\n말레이시아 수출\n"
                      "26년 7월 Update\n\n수출액 YoY: +170.4%",
                      "**🇨🇳 4월 수출 중국**\n\n**▶️ 전기차**\n\n"
                      "**26년04월: $4,688.8M  (+32.0% YoY)  (+27.4% MoM)**"):
            self.assertIsNone(cns.parse_cn_stock_export(other), other[:30])

    def test_item_parser_does_not_claim_the_company_caption(self):
        """이 사고의 원인 그 자체 — 어순이 반대라 품목 파서가 못 잡았다."""
        self.assertIsNone(cn.parse_cn_export(_TTM))

    def test_registry_routes_the_caption_here(self):
        """드랍되던 캡션이 이제 관련성 필터를 통과하고 cns 로 간다."""
        self.assertTrue(srcs.is_relevant(_TTM))
        first = next(s.key for s in srcs.SOURCES if s.parse(_TTM) is not None)
        self.assertEqual(first, "cns")


class StoreTests(unittest.TestCase):
    def _db(self, tmp):
        return cns.open_cn_stock_db(Path(tmp) / "cn_stock.db")

    def test_monthly_rolling_replaces_card_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            cns.ingest(conn, _TTM.replace("26년 7월", "26년 6월"))
            cns.ingest(conn, _TTM)
            rows = cns.list_cn_stock(conn)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["month"], "2026-07")
            self.assertEqual(len(cns.history(conn, "TTMI")), 2)

    def test_partial_resend_does_not_null_existing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            cns.ingest(conn, _TTM, media_paths=["media/a.png"])
            cns.ingest(conn, "TTM Technologies (TTMI)\n중국 수출\n"
                             "26년 7월 Update\n\n수출액 YoY: +55.8%")
            r = cns.list_cn_stock(conn)[0]
            self.assertEqual(r["chart_media"], "media/a.png")
            self.assertEqual(r["lead_corr"], 0.85, "부분 재전송이 값을 지웠다")

    def test_migration_adds_columns_to_existing_db(self):
        """배포 후 첫 쓰기가 터지지 않게 — CREATE TABLE IF NOT EXISTS 는
        기존 테이블을 손대지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cn_stock.db"
            import sqlite3
            c = sqlite3.connect(str(p))
            c.execute("CREATE TABLE cn_stock_exports (ticker TEXT NOT NULL, "
                      "month TEXT NOT NULL DEFAULT '', "
                      "PRIMARY KEY (ticker, month))")
            c.commit()
            c.close()
            conn = cns.open_cn_stock_db(p)
            have = {r["name"] for r in
                    conn.execute("PRAGMA table_info(cn_stock_exports)")}
            self.assertIn("comment", have)
            self.assertIn("parse_ver", have)


class RenderTests(unittest.TestCase):
    def test_render_html_works_empty_and_populated(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = cns.open_cn_stock_db(Path(tmp) / "cn_stock.db")
            empty = cns.render_html(conn)
            self.assertIn("아직 수집된", empty)
            self.assertIn("<!DOCTYPE html>", empty)
            cns.ingest(conn, _TTM, media_paths=["media/a.png"])
            h = cns.render_html(conn, media_url_prefix="../")
            self.assertIn("TTM Technologies", h)
            self.assertIn("../media/a.png", h)

    def test_comment_is_rendered_on_the_card(self):
        """사용자 2026-08-21 "이런 코멘트도 포함하면 좋겠어" — 화면까지
        가는지 본다. 파서만 보면 렌더에서 떨어뜨리는 변형을 못 잡는다(#20)."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = cns.open_cn_stock_db(Path(tmp) / "cn_stock.db")
            cns.ingest(conn, _TTM)
            h = cns.render_html(conn)
            self.assertIn("고다층 PCB", h, "코멘트가 카드에 안 실린다")

    def test_levels_have_no_sign_or_arrow(self):
        """상관·방향일치율에 `+0.74 ▲` 를 붙이면 '0.74 상승'으로 읽힌다."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = cns.open_cn_stock_db(Path(tmp) / "cn_stock.db")
            cns.ingest(conn, _TTM)
            h = cns.render_html(conn)
            self.assertIn("0.74", h)
            self.assertNotIn("+0.74", h)
            self.assertNotIn("▲0.74", h)

    def test_card_labels_distinguish_the_two_series(self):
        """동시·선행을 같은 이름으로 내면 정의가 섞인다(실수 #34)."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = cns.open_cn_stock_db(Path(tmp) / "cn_stock.db")
            cns.ingest(conn, _TTM)
            h = cns.render_html(conn)
            self.assertIn("동시상관", h)
            self.assertIn("선행상관", h)
            self.assertIn("선행 방향 일치율", h)

    def test_history_table_columns_follow_the_series_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = cns.open_cn_stock_db(Path(tmp) / "cn_stock.db")
            cns.ingest(conn, _DELL.replace("26년 7월", "26년 6월"))
            cns.ingest(conn, _DELL)
            h = cns.render_html(conn)
            self.assertIn("선행상관", h)
            # DELL 은 단가가 없다 — 빈 열을 만들면 안 된다.
            self.assertNotIn("단가 YoY", h)


if __name__ == "__main__":
    unittest.main()
