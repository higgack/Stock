"""대만 월매출(종목별) 파서 회귀 — 나쁜양파 `badonion.co.kr/twse-revenue`.

사용자 2026-09-10: "이것도 수집되는건가? 이건 수집이 안된것 같아서. 나머지는
됐는데." — 같은 날 대만 수출(종목별) 6건은 회수됐고 TSMC 월매출 카드만
어느 파서도 안 받아 관련성 필터에서 드랍됐다(여섯 번째 조용한 유실).

재현(§Pre-commit 9): 이 캡션이 `is_relevant` 를 통과하지 못했다. 여기서
(a) 재구성 캡션의 변주(줄바꿈·`·` 구분·티커 괄호·`:` 유무)가 파싱되고
(b) 레지스트리가 `twr` 로 라우팅하며 (c) 형제 파서(수출 종목판·품목판)가
이 카드를 먹지 않고 그 반대도 없으며 (d) 화면이 월매출이라고 말하는지 못박는다.

⚠️ 픽스처는 **스크린샷 기반 재구성**이다(원문 마크다운 미확인). 실물 원문이
`backfill_badonion --show-irrelevant` 로 확보되면 그 원문으로 갈아끼운다(#155).
"""

import tempfile
import unittest
from pathlib import Path

from trade import badonion_sources as srcs
from trade import tw_monthly_revenue as twr
from trade import tw_stock_exports as tws

# 실물 캡션 — 2026-09-10 오후 2:53 KST 텔레그램 스크린샷 그대로(#155). 누적 블록이
# 세 줄이고 YoY 부호가 라벨 앞에 온다(`(+YoY 39.3%)`), 각주에 `대만 수출` 이 있다.
_TSMC = (
    "TSMC 월매출\n26년 8월\n\n"
    "REV 약 5,148억 1,000만 TWD\nMoM +10.1% · YoY +53.3%\n\n"
    "26년 1~8월 누적:\n3조 3,868억 7,000만 TWD\n(+YoY 39.3%)\n\n"
    "* 대만 수출이 먼저 나오기 때문에 어느 정도 예상 가능했던 일\n\n"
    "https://badonion.co.kr/twse-revenue")
# 첫 재구성(스크린샷 확인 전) — 누적이 한 줄에 다 오는 압축 형식. 관용 파싱 유지.
_COMPACT = (
    "TSMC 월매출\n26년 8월 Update\n\n"
    "REV: 약 5,148억 1,000만 TWD\nMoM: +10.1%\nYoY: +53.3%\n"
    "26년 1~8월 누적: 약 3조 2,500억 TWD (YoY +40.2%)\n\n"
    "https://badonion.co.kr/twse-revenue")
# 한 줄 `·` 구분 + 티커 괄호 + 라벨 뒤 `:` 없음
_ONE_LINE = ("TSMC (2330) 월매출 26년 8월 · REV 약 5,148억 1,000만 TWD · MoM +10.1% · "
             "YoY +53.3% · 26년 1~8월 누적 YoY +40.2% · https://badonion.co.kr/twse-revenue")
_NEG = _TSMC.replace("MoM +10.1%", "MoM -3.4%").replace("YoY +53.3%", "YoY -12.0%")


class ParseTests(unittest.TestCase):
    def test_reconstructed_caption(self):
        d = twr.parse_tw_monthly_revenue(_TSMC)
        self.assertEqual(d["ticker"], "TSMC")
        self.assertEqual(d["stock_name"], "TSMC")
        self.assertEqual(d["month"], "2026-08", "누적 줄의 '1~8월' 을 기준월로 읽으면 안 된다")
        self.assertEqual(d["rev_text"], "5,148억 1,000만 TWD")
        self.assertEqual(d["rev_twd"], 514_810_000_000.0)
        self.assertEqual(d["mom"], 10.1)
        self.assertEqual(d["yoy"], 53.3, "누적 YoY(+39.3) 를 당월 YoY 로 읽으면 안 된다")
        self.assertEqual(d["cum_yoy"], 39.3, "누적 블록 셋째 줄 `(+YoY 39.3%)` 를 읽어야 한다")
        self.assertIn("1~8월 누적", d["cum_text"])
        self.assertIn("3조 3,868억 7,000만 TWD", d["cum_text"], "누적 금액은 다음 줄에 온다")
        self.assertNotIn("badonion", d["cum_text"])
        self.assertNotIn("대만 수출", d["cum_text"], "각주는 누적 블록이 아니다(문단이 갈린다)")
        self.assertEqual(d["parse_ver"], twr.PARSE_VER)

    def test_compact_single_line_cumulative(self):
        d = twr.parse_tw_monthly_revenue(_COMPACT)
        self.assertEqual((d["month"], d["mom"], d["yoy"], d["cum_yoy"]), ("2026-08", 10.1, 53.3, 40.2))
        self.assertIn("3조 2,500억", d["cum_text"])

    def test_sign_before_label_is_applied(self):
        """`(-YoY 5.0%)` 는 -5.0 · `(+YoY 39.3%)` 는 +39.3 — 값에 부호가 있으면 그게 이긴다."""
        d = twr.parse_tw_monthly_revenue(_TSMC.replace("(+YoY 39.3%)", "(-YoY 5.0%)"))
        self.assertEqual(d["cum_yoy"], -5.0)
        self.assertEqual(d["yoy"], 53.3)
        d = twr.parse_tw_monthly_revenue(_TSMC.replace("YoY +53.3%", "-YoY +2.0%"))
        self.assertEqual(d["yoy"], 2.0, "값 자체의 부호가 라벨 앞 부호보다 우선")

    def test_cumulative_yoy_does_not_become_monthly_yoy_when_monthly_is_absent(self):
        """당월 YoY 줄이 없어도 누적 블록의 YoY 가 당월 칸에 앉으면 안 된다 —
        빈칸이 낫다(#29)."""
        cap = _TSMC.replace("MoM +10.1% · YoY +53.3%", "MoM +10.1%")
        d = twr.parse_tw_monthly_revenue(cap)
        self.assertIsNone(d["yoy"])
        self.assertEqual(d["cum_yoy"], 39.3)

    def test_one_line_variant_with_ticker(self):
        d = twr.parse_tw_monthly_revenue(_ONE_LINE)
        self.assertEqual(d["ticker"], "2330")
        self.assertEqual(d["stock_name"], "TSMC")
        self.assertEqual(d["month"], "2026-08")
        self.assertEqual(d["rev_twd"], 514_810_000_000.0)
        self.assertEqual((d["mom"], d["yoy"], d["cum_yoy"]), (10.1, 53.3, 40.2))

    def test_other_separators_and_labels_are_tolerated(self):
        """원문 형식이 미확인이라 관용 파싱이 요점이다(독립 리뷰 2026-09-10 #3):
        ` / `·`|` 구분, 한 줄에 콤마로 이어진 지표, `Revenue`/`매출` 라벨."""
        slash = ("TSMC 월매출 / 26년 8월 Update / REV: 약 5,148억 TWD / MoM: +10.1% / "
                 "YoY: +53.3% / https://badonion.co.kr/twse-revenue")
        d = twr.parse_tw_monthly_revenue(slash)
        self.assertEqual((d["month"], d["rev_text"], d["mom"], d["yoy"]),
                         ("2026-08", "5,148억 TWD", 10.1, 53.3))
        comma = "TSMC 월매출\n26년 8월\nREV: 약 5,148억 TWD, MoM: +10.1%, YoY: +53.3%"
        d = twr.parse_tw_monthly_revenue(comma)
        self.assertEqual((d["rev_text"], d["mom"], d["yoy"]), ("5,148억 TWD", 10.1, 53.3),
                         "천단위 콤마에서 금액을 자르거나 elif 로 지표를 잃으면 안 된다")
        for lbl in ("Revenue: 5,148억 TWD", "매출: 5,148억 TWD", "매출액 5,148억 TWD"):
            d = twr.parse_tw_monthly_revenue(f"TSMC 월매출\n26년 8월\n{lbl}\nMoM: -1.0%")
            self.assertEqual(d["rev_text"], "5,148억 TWD", lbl)
            self.assertEqual(d["mom"], -1.0)
        pipe = "TSMC 월매출 | 26년 8월 | YoY: +5.0%"
        self.assertEqual(twr.parse_tw_monthly_revenue(pipe)["yoy"], 5.0)

    def test_korean_stock_board_phrase_is_not_a_revenue_header(self):
        """`26년 8월 매출 YoY` 류(한국 종목판 문구)를 헤더로 오독하면 남의 캡션이
        이 DB 로 새고, 새 형식이 --show-irrelevant 에서 숨는다(독립 리뷰 #2)."""
        kr = ("삼성전자 (005930)\n한국 수출\n26년 8월 매출 YoY: +23.2%\n"
              "수출액 YoY: +1.0%\n\nbadonion.co.kr")
        self.assertIsNone(twr.parse_tw_monthly_revenue(kr))
        self.assertIsNone(twr.parse_tw_monthly_revenue("8월 매출액이 늘었다\n26년 8월\nYoY +3%"))
        hits = [s.key for s in srcs.SOURCES if s.parse(kr) is not None]
        self.assertNotIn("twr", hits)

    def test_cumulative_month_before_header_month_is_not_the_period(self):
        """`26년 7월 누적` 이 기준월 줄보다 앞에 와도 기준월은 8월이다 —
        `_RE_PERIOD` 의 `(?!\\s*누적)` 이 그 가드다(독립 리뷰 #8: 픽스처 없이는
        지워도 통과했다)."""
        cap = ("TSMC 월매출\n26년 7월 누적: 약 3조 TWD (YoY +40.2%)\n26년 8월 Update\n"
               "MoM: +1.0%")
        d = twr.parse_tw_monthly_revenue(cap)
        self.assertEqual(d["month"], "2026-08")
        self.assertEqual(d["cum_yoy"], 40.2)

    def test_negative_keeps_sign(self):
        d = twr.parse_tw_monthly_revenue(_NEG)
        self.assertEqual((d["mom"], d["yoy"]), (-3.4, -12.0))

    def test_amount_units(self):
        self.assertEqual(twr.amount_twd("약 3조 2,500억 TWD"), 3.25e12)
        self.assertEqual(twr.amount_twd("1,000만 TWD"), 1e7)
        self.assertIsNone(twr.amount_twd("N/A"), "단위 없으면 지어내지 않는다(#32)")

    def test_rejects_non_revenue_and_bad_month(self):
        self.assertIsNone(twr.parse_tw_monthly_revenue(""))
        self.assertIsNone(twr.parse_tw_monthly_revenue("TSMC 월매출\n26년 13월"))
        self.assertIsNone(twr.parse_tw_monthly_revenue("TSMC 월매출 리포트 안내"))

    def test_registry_routes_here_and_siblings_do_not_claim_it(self):
        """이 파서가 곧 관련성 필터다 — 없으면 조용히 드랍(#83)."""
        for cap in (_TSMC, _ONE_LINE):
            self.assertTrue(srcs.is_relevant(cap))
            hits = [s.key for s in srcs.SOURCES if s.parse(cap) is not None]
            self.assertEqual(hits, ["twr"], hits)
        self.assertIsNone(tws.parse_tw_stock_export(_TSMC))

    def test_does_not_eat_the_export_card(self):
        asx = ("ASE Technology Holding (ASX)\n대만 수출\n26년 8월 Update\n\n"
               "단가 YoY: +23.2%\n수출액 YoY: +33.1%\n\nbadonion.co.kr")
        self.assertIsNone(twr.parse_tw_monthly_revenue(asx))
        self.assertEqual(next(s.key for s in srcs.SOURCES if s.parse(asx)), "tws")


class StoreTests(unittest.TestCase):
    def test_ingest_history_and_rolling_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = twr.open_tw_revenue_db(Path(tmp) / "t.db")
            self.assertTrue(twr.ingest(conn, _TSMC.replace("26년 8월", "26년 7월"),
                                       source_message_id=1, posted_at="2026-08-10"))
            self.assertTrue(twr.ingest(conn, _TSMC, source_message_id=2,
                                       posted_at="2026-09-10"))
            rows = twr.list_tw_revenue(conn)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["month"], "2026-08")
            self.assertEqual(len(twr.history(conn, "TSMC")), 2)
            self.assertFalse(twr.ingest(conn, "무관한 글"))

    def test_migration_adds_missing_columns(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "old.db"
            c = sqlite3.connect(p)
            c.execute(f"CREATE TABLE {twr.TABLE} (ticker TEXT NOT NULL, month TEXT NOT NULL "
                      "DEFAULT '', PRIMARY KEY (ticker, month))")
            c.commit(); c.close()
            conn = twr.open_tw_revenue_db(p)
            self.assertTrue(twr.ingest(conn, _TSMC))
            self.assertEqual(twr.list_tw_revenue(conn)[0]["yoy"], 53.3)


class RenderTests(unittest.TestCase):
    def _html(self, caption=_TSMC):
        with tempfile.TemporaryDirectory() as tmp:
            conn = twr.open_tw_revenue_db(Path(tmp) / "t.db")
            if caption:
                twr.ingest(conn, caption)
            return twr.render_html(conn)

    def test_page_says_monthly_revenue_not_export(self):
        h = self._html()
        self.assertIn("대만 월매출 데이터(종목별)", h)
        self.assertIn("5,148억 1,000만 TWD", h)
        self.assertIn("+53.3%", h)
        self.assertIn("1~8월 누적", h)
        self.assertNotIn("수출액", h)
        self.assertIn("tw_stock.html", h)          # 형제(종목별 수출) 링크

    def test_empty_page_still_renders(self):
        h = self._html(caption="")
        self.assertIn("아직 수집된 대만 월매출 데이터", h)
        self.assertIn("<!DOCTYPE html>", h)


if __name__ == "__main__":
    unittest.main()


class BackfillShowIrrelevantTests(unittest.TestCase):
    """새 카드 형식은 늘 '드랍된 쪽'에 숨는다(여섯 번째). 백필이 드랍 캡션 머리를
    찍는 읽기 전용 플래그를 갖고 `main` 이 그걸 `run` 에 넘기는지 AST 로 본다
    (telethon 없는 환경에서도 잰다)."""

    def test_unit_head_same_shape_for_dropped_and_existing(self):
        """드랍 유닛과 이미 포워드된 유닛이 같은 헬퍼로 찍힌다 — 창 안인데 드랍
        목록에 없는 글이 어디로 갔는지 한 출력에서 갈린다(2026-09-10 TSMC)."""
        import ast
        from datetime import datetime, timezone
        from types import SimpleNamespace as NS
        # 샌드박스엔 telethon 이 없다 — 헬퍼 정의만 AST 로 떼어 실행한다.
        src = open("trade/scripts/backfill_badonion.py", encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_unit_head")
        ns = {"Message": object}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "bf", "exec"), ns)
        _unit_head = ns["_unit_head"]
        d = datetime(2026, 9, 10, 5, 53, tzinfo=timezone.utc)
        when, head = _unit_head([NS(text="", date=d), NS(text="TSMC  월매출\n26년 8월", date=d)])
        self.assertEqual((when, head), ("2026-09-10 05:53 UTC", "TSMC 월매출 26년 8월"))
        self.assertEqual(_unit_head([NS(text=None, date=d)])[1], "(캡션 없음)")
        run = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
        calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_unit_head"]
        self.assertGreaterEqual(len(calls), 2, "드랍·기존 두 목록이 같은 헬퍼를 써야 한다")
        # 기존 유닛은 `key in existing` 분기에서 모아 'already-in-inbox' 로 찍힌다
        run_src = ast.get_source_segment(src, run)
        self.assertIn("existing_msgs.append(msg)", run_src)
        self.assertIn("already-in-inbox unit", run_src)

    def test_flag_is_wired_from_main_to_run(self):
        import ast
        src = open("trade/scripts/backfill_badonion.py", encoding="utf-8").read()
        tree = ast.parse(src)
        run = next(n for n in tree.body
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
        self.assertIn("show_irrelevant", [a.arg for a in run.args.args + run.args.kwonlyargs])
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        kws = [k.arg for c in ast.walk(main) if isinstance(c, ast.Call)
               for k in c.keywords]
        self.assertIn("show_irrelevant", kws, "main 이 플래그를 run 에 안 넘긴다")
        flags = [c.args[0].value for c in ast.walk(main) if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", "") == "add_argument" and c.args
                 and isinstance(c.args[0], ast.Constant)]
        self.assertIn("--show-irrelevant", flags)
        # 진단 플래그는 포워드하지 않는다 — main 이 dry_run 을 show_irrelevant 로도
        # 켠다(독립 리뷰 #1: '읽기 전용' 이라 적고 포워드하던 것).
        forced = [n for n in ast.walk(main) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "dry_run" for t in n.targets)]
        self.assertTrue(forced, "main 에 dry_run 계산이 없다")
        names = {a.attr for n in forced for a in ast.walk(n.value) if isinstance(a, ast.Attribute)}
        self.assertTrue({"dry_run", "show_irrelevant"} <= names, names)
        call = next(c for c in ast.walk(main) if isinstance(c, ast.Call)
                    and getattr(c.func, "id", "") == "run")
        self.assertEqual(getattr(call.args[2], "id", None), "dry_run",
                         "run() 에 args.dry_run 을 그대로 넘기면 강제가 빠진다")
