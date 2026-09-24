"""나쁜양파 소스 단일 레지스트리 회귀.

2026-08-16: 파서 목록이 5개 파일에 중복(49회 언급)돼 있었고, 한국 수출을
추가하며 **로그 문구가 실제로 어긋났다** — 필터는 한국을 통과시키는데
백필 로그는 '대만·중국·일본·태국·말레이시아·필리핀·멕시코 수출/미국 수입'
만 나열했다. 드리프트를 구조적으로 불가능하게 만든 뒤 그 계약을 고정한다.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from trade import badonion_sources as srcs

_CAPS = {
    "tw": "6월 수출 대만\n\n▶️ 테스트\n\n26년06월: $9.1M  (+3.0% YoY)  (+1.0% MoM)",
    "us": "🇺🇸 6월 수입 미국\n\n▶️ 테스트 품목\n\n"
          "26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)",
    "krs": "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +260.2%",
    # 말레이시아 종목별 — 헤더가 **세 줄**이고 이 소스에만 있는 수준값
    # 2종(동시상관·방향 일치율) + 관련 매출 줄이 온다(사용자 2026-08-20 캡처).
    "mys": "Texas Instruments Incorporated (TXN)\n말레이시아 수출\n"
           "26년 7월 Update\n\n수출액 YoY: +170.4%\n3M 수출액 YoY: +127.1%\n\n"
           "동시상관: 0.93\n방향 일치율: 67%\n\n"
           "- CY26Q2 매출 $5.46B(+22.8% YoY)",
    # 일본 종목별 — 헤더가 **한 줄**이고 볼드(`**`)가 붙어 온다(실측 원문).
    "jps": "**Kioxia (285A) 일본 수출 Update**\n**26년 6월**\n\n"
           "**Yokkaichi NAND 웨이퍼**\n\n수출액: YoY +95.1%\n"
           "3M 수출액: YoY +103.9%\n\n"
           "* SanDisk와 동일한 공동생산 흐름이므로 두 지표를 합산하지 않습니다.",
}


class RegistryTests(unittest.TestCase):
    def test_order_is_the_ingest_fallback_contract(self):
        # ⚠️ _ingest_group 은 순차 fallback — 순서가 바뀌면 앞선 파서가
        # 캡션을 먼저 가져가 **조용한 오저장**이 된다.
        keys = [s.key for s in srcs.SOURCES]
        self.assertEqual(
            keys,
            ["tw", "cn", "jp2", "th", "my", "ph", "mx", "us",
             # 2026-08-19 미국 PPI — 품목 기준이라 종목 기준 앞.
             "uppi", "krs",
             # 2026-09-16 한국 수입(회사별) — 수출 금액판과 같은 문법 엔진,
             # 마커 한 낱말만 다르다(#370). krs 바로 뒤여야 한국 캡션을
             # 수출 파서가 먼저 보고 아니면 수입이 받는다.
             "kri", "jps", "mys",
             # 2026-08-21 중국 수출·수입(종목별).
             "cns", "cni",
             # 2026-09-10 대만 수출(종목별) — 파서가 없어 드랍되던 것(#83 다섯 번째).
             "tws",
             # 2026-09-10 대만 월매출(종목별) — 같은 날 여섯 번째(TSMC 월매출 카드).
             "twr"])
        # ⚠️ 위 목록은 **손으로 갱신하는 핀**이라, 규약 자체도 같이 못박는다
        # (핀만 있으면 "지금 순서를 그대로 적은" 테스트가 된다, 실수 #19).
        # 계약: 품목(HS) 기준 파서가 **전부** 종목(회사) 기준보다 앞.
        basis = [s.basis for s in srcs.SOURCES]
        first_company = basis.index("company")
        self.assertNotIn("item", basis[first_company:],
                         "종목 기준 뒤에 품목 기준이 섞였다 — 라우팅이 갈린다")
        # ⚠️ 옛 판은 `stock_keys = {"krs","jps","mys"}` 라고 **열거**했다 —
        # 새 종목 소스를 더할 때마다 손으로 고쳐야 하고, 안 고치면 규약을
        # 어긴 게 아니라 목록이 낡은 것뿐인데 빨간불이 뜬다(실수 #24).
        # 위 `basis` 검사가 같은 계약을 목록 없이 지킨다.

    def test_every_source_has_a_complete_contract(self):
        seen_keys, seen_dbs = set(), set()
        for s in srcs.SOURCES:
            self.assertTrue(s.key and s.label, s)
            self.assertTrue(callable(s.parse) and callable(s.open_db)
                            and callable(s.ingest), s.key)
            self.assertTrue(s.db_file.endswith(".db"), s.key)
            self.assertNotIn(s.key, seen_keys, "key 중복")
            self.assertNotIn(s.db_file, seen_dbs, "DB 파일 충돌 = 스키마 깨짐")
            seen_keys.add(s.key)
            seen_dbs.add(s.db_file)

    def test_is_relevant_matches_each_source_and_rejects_noise(self):
        for key, cap in _CAPS.items():
            self.assertTrue(srcs.is_relevant(cap), key)
        for noise in ("", "애널리스트 레이팅표: 목표주가 상향",
                      "2026.08.05 17:20:03\n기업명: SK하이닉스 A000660"):
            self.assertFalse(srcs.is_relevant(noise), repr(noise))

    def test_each_caption_claimed_by_exactly_one_parser(self):
        # 두 소스가 같은 캡션을 먹으면 순서에 따라 저장처가 달라진다.
        for key, cap in _CAPS.items():
            hits = [s.key for s in srcs.SOURCES if s.parse(cap) is not None]
            self.assertEqual(hits, [key], f"{key} 캡션을 {hits} 가 주장")

    def test_labels_derive_from_registry(self):
        # 로그 문구가 목록에서 조립돼야 드리프트가 불가능해진다.
        lbl = srcs.labels()
        for s in srcs.SOURCES:
            self.assertIn(s.label, lbl)
        self.assertIn("한국", lbl, "한국 누락 = 옛 드리프트 재발")

    def test_open_db_creates_usable_schema(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            for s in srcs.SOURCES:
                conn = s.open_db(Path(tmp.name) / s.db_file)
                try:
                    conn.execute("SELECT name FROM sqlite_master "
                                 "WHERE type='table'").fetchall()
                finally:
                    conn.close()
        finally:
            tmp.cleanup()


class ConsumersUseRegistryTests(unittest.TestCase):
    """5개 소비처가 전부 레지스트리를 보는지 — 하나라도 옛 하드코딩이
    남으면 판정이 갈려 오탐/누락이 난다."""

    _FILES = (
        "trade/scripts/listen_badonion.py",
        "trade/scripts/backfill_badonion.py",
        "trade/scripts/ingest_inbox.py",
        "trade/scripts/unstored_check.py",
        "trade/dashboard.py",
    )
    _PARSERS = ("parse_tw_export", "parse_cn_export", "parse_jp2_export",
                "parse_th_export", "parse_my_export", "parse_ph_export",
                "parse_mx_export", "parse_us_import", "parse_kr_stock_export",
                "parse_jp_stock_export", "parse_my_stock_export")
    # ⚠️ 호출 **형태**를 잡는다 — 그냥 "badonion_sources" substring 은 주석·
    # docstring 만으로도 통과해서, 실제 가드를 지워도 초록이 된다(순 커버리지
    # 후퇴). 각 소비처가 레지스트리를 실제로 **쓰는** 지점을 고정한다.
    _USES = {
        "trade/scripts/listen_badonion.py": ("_srcs.is_relevant(",
                                             "_srcs.labels()"),
        "trade/scripts/backfill_badonion.py": ("_srcs.is_relevant(",
                                               "_srcs.labels()"),
        "trade/scripts/ingest_inbox.py": ("for _src in _srcs.SOURCES:",),
        "trade/scripts/unstored_check.py": ("_srcs.is_relevant(",),
        "trade/dashboard.py": ("_srcs.is_relevant(",
                               "for _s in _srcs.SOURCES:"),
    }

    def test_no_consumer_lists_parsers_directly(self):
        for f in self._FILES:
            src = Path(f).read_text(encoding="utf-8")
            for use in self._USES[f]:
                self.assertIn(use, src, f"{f}: 레지스트리 호출 {use!r} 미배선")
            for p in self._PARSERS:
                self.assertNotIn(p, src, f"{f}: {p} 하드코딩 잔존")

    def test_no_consumer_hardcodes_the_country_list(self):
        # 옛 버전은 이미 지워진 정확한 한 문장(`태국·말레이시아·필리핀·멕시코
        # 수출/미국 수입`)만 금지해서 **다시는 실패할 수 없는** 묘비였다.
        # 드리프트는 문구가 아니라 '한 줄에 국가를 여러 개 나열하는 행위'
        # 자체이므로 그 형태를 금지한다 — 새 소스를 추가하면 그런 줄은
        # 반드시 낡는다(한국 누락이 정확히 그 사고였다).
        names = {s.label for s in srcs.SOURCES} | {
            "대만", "중국", "일본", "태국", "말레이시아", "필리핀", "멕시코", "미국"}
        for f in self._FILES:
            for i, ln in enumerate(
                    Path(f).read_text(encoding="utf-8").splitlines(), 1):
                hits = sorted(n for n in names if n in ln)
                self.assertLess(
                    len(hits), 3,
                    f"{f}:{i} 국가 나열 하드코딩({hits}) — labels()/nav_html() 사용")

    def test_systemd_units_do_not_list_countries_either(self):
        # 파이썬 소비처만 검사하던 가드의 사각 — 나쁜양파 유닛 Description 이
        # '대만·중국·일본' 3개로 굳어 소스 12개 중 9개가 빠져 있었다
        # (2026-08-20 말레이시아 종목별 추가 중 발견). 유닛도 같은 계약.
        names = {"대만", "중국", "일본", "태국", "말레이시아", "필리핀",
                 "멕시코", "미국", "한국"}
        for f in ("deploy/trade-bot-badonion-listener.service",
                  "deploy/trade-bot-badonion-sync.service"):
            for i, ln in enumerate(
                    Path(f).read_text(encoding="utf-8").splitlines(), 1):
                hits = sorted(n for n in names if n in ln)
                self.assertLess(len(hits), 3,
                                f"{f}:{i} 국가 나열({hits}) — 새 소스마다 낡는다")

    def test_nav_covers_every_page_bearing_source(self):
        # nav 를 빠뜨리면 페이지는 생성되는데 **도달 불가**가 되고,
        # is_relevant 가 미매칭 알림까지 눌러 조용한 유실이 된다.
        paged = {s.key for s in srcs.SOURCES if s.html_file}
        self.assertEqual(set(srcs.NAV_ORDER), paged, "nav 누락/유령 키")
        nav = srcs.nav_html()
        for s in srcs.SOURCES:
            if not s.html_file:
                continue
            self.assertIn(f'href="{s.html_file}"', nav, s.key)
            self.assertIn(s.nav_label, nav, s.key)
        # 일본(나쁜양파)은 일본(비온) 옆이어야 한다(사용자 2026-07-11).
        # ⚠️ 옛 판은 `NAV_ORDER[0] == "jp2"` 였는데 그건 **규약이 아니라
        # 결과**다 — 일본이 유일하게 3페이지였을 때만 참이었고, 중국이
        # 2026-08-21 수출·수입 종목판으로 3페이지가 되며 동률이 나자 깨졌다
        # (실수 #19). 계약은 "같은 나라가 붙어 있다" 이다.
        order = list(srcs.NAV_ORDER)
        by_key = {x.key: x for x in srcs.SOURCES}
        for country in {x.country for x in srcs.SOURCES if x.html_file}:
            pos = [i for i, k in enumerate(order)
                   if by_key[k].country == country]
            self.assertEqual(pos, list(range(min(pos), max(pos) + 1)),
                             f"{country} 소스가 nav 에서 흩어졌다: {order}")
        self.assertIn("_srcs_nav_html()", Path("trade/dashboard.py").read_text(
            encoding="utf-8"), "대시보드가 nav 를 레지스트리에서 안 받음")

    def test_no_flag_emoji_in_display_labels(self):
        """국기 이모지(regional indicator)는 **표시 문자열에 금지**.

        flag-sequence 는 폰트가 없으면 두 글자 코드로 폴백한다 — Windows
        Chrome 에서 실제로 그렇게 렌더됐다(사용자 2026-07-11 '🇹🇼 가 tw 로',
        2026-08-17 '🇺🇸 만 us 로'). 두 번 다 스크린샷을 받고서야 알았으니
        규칙을 주석이 아니라 테스트로 고정한다.

        ⚠️ **표시 문자열만** 검사한다 — 텔레그램 캡션(파서 입력)의 국기는
        원문 마커라 지우면 ingest 가 통째로 깨진다."""
        import re
        flag = re.compile("[\U0001F1E6-\U0001F1FF]")
        for s in srcs.SOURCES:
            self.assertIsNone(flag.search(s.nav_label),
                              f"{s.key} nav_label 국기 이모지: {s.nav_label}")
        # 각 페이지 <h1> 도 같은 표시 계열 — nav 만 고치면 페이지에 남는다.
        for p in sorted(Path("trade").glob("*.py")):
            for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if "<h1>" in ln and flag.search(ln):
                    self.fail(f"{p}:{i} 페이지 h1 국기 이모지: {ln.strip()}")

    def test_ingest_routes_via_registry_loop(self):
        src = Path("trade/scripts/ingest_inbox.py").read_text(encoding="utf-8")
        self.assertIn("for _src in _srcs.SOURCES:", src, "순회 라우팅 미배선")
        self.assertIn("badonion_conns", src)
        self.assertIn('f"{_src.key}_inserted"', src)


class RoutingRegressionTests(unittest.TestCase):
    """레지스트리 전환이 실제 저장처를 바꾸지 않았는지 — 소스 grep 이 아니라
    `_ingest_group` 을 실제로 통과시켜 확인한다."""

    def _run(self, caption: str):
        import sys
        # ingest_inbox 는 import 시점에 argparse 를 타므로 pytest 인자를
        # 가린다. 인터프리터 전역이라 **반드시 되돌린다** — 안 그러면 뒤에
        # 오는 argv 읽는 테스트가 실행순서에 따라 깨진다.
        _saved = sys.argv
        self.addCleanup(setattr, sys, "argv", _saved)
        sys.argv = ["x"]
        from trade.scripts import ingest_inbox as ii
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conns = {s.key: s.open_db(Path(tmp.name) / s.db_file)
                 for s in srcs.SOURCES}
        for c in conns.values():
            self.addCleanup(c.close)
        counters = {"unparseable": 0}
        grp = [{"caption_present": True, "text": caption, "message_id": 1,
                "chat_id": -100, "date": "2026-08-16T00:00:00",
                "media_group_id": None}]
        ii._ingest_group(None, grp, Path(tmp.name), counters, set(), None, conns)
        return counters, conns

    def test_each_caption_lands_in_its_own_db(self):
        for key, cap in _CAPS.items():
            counters, _ = self._run(cap)
            hit = [k for k, v in counters.items()
                   if k.endswith("_inserted") and v]
            self.assertEqual(hit, [f"{key}_inserted"], (key, counters))
            self.assertEqual(counters["unparseable"], 0, key)

    def test_jp_stock_row_actually_persisted(self):
        _, conns = self._run(_CAPS["jps"])
        rows = conns["jps"].execute(
            "SELECT ticker, month, export_yoy, note FROM jp_stock_exports"
        ).fetchall()
        self.assertEqual(
            [dict(r) for r in rows],
            [{"ticker": "285A", "month": "2026-06", "export_yoy": 95.1,
              "note": "SanDisk와 동일한 공동생산 흐름이므로 두 지표를 "
                      "합산하지 않습니다."}])

    def test_my_stock_row_actually_persisted(self):
        # 이 소스에만 있는 수준값 2종 + 관련 매출 원문이 실제로 저장되는지.
        _, conns = self._run(_CAPS["mys"])
        rows = conns["mys"].execute(
            "SELECT ticker, month, export_yoy, export_yoy_3m, corr, dir_hit,"
            " revenue FROM my_stock_exports").fetchall()
        self.assertEqual(
            [dict(r) for r in rows],
            [{"ticker": "TXN", "month": "2026-07", "export_yoy": 170.4,
              "export_yoy_3m": 127.1, "corr": 0.93, "dir_hit": 67.0,
              "revenue": "CY26Q2 매출 $5.46B(+22.8% YoY)"}])

    def test_kr_row_actually_persisted(self):
        _, conns = self._run(_CAPS["krs"])
        rows = conns["krs"].execute(
            "SELECT stock_code, month FROM kr_stock_exports").fetchall()
        self.assertEqual([dict(r) for r in rows],
                         [{"stock_code": "403870", "month": "2026-07"}])

    def test_unrelated_caption_counts_as_unparseable(self):
        counters, _ = self._run("애널리스트 레이팅표: 목표주가 상향")
        self.assertEqual(counters["unparseable"], 1)


class DiagnoseScriptTests(unittest.TestCase):
    """조회 전용 계약 — 이 스크립트가 실수로 전송하면 안 된다."""

    def test_no_send_symbols_in_executable_code(self):
        import ast
        src = Path("trade/scripts/diagnose_badonion.py").read_text(
            encoding="utf-8")
        banned = {"forward_messages", "_forward_unit", "_notify",
                  "send_read_acknowledge"}
        hits = []
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Attribute) and n.attr in banned:
                hits.append(n.attr)
            elif isinstance(n, ast.Name) and n.id in banned:
                hits.append(n.id)
        self.assertEqual(hits, [], f"전송 심볼이 실행 코드에 있음: {hits}")
        # 계약이 docstring 에 명시돼 있어야(다음 사람이 깨뜨리지 않게)
        self.assertIn("Read-only", src)
        self.assertIn("forward_messages", src, "금지 목록이 문서화돼야")

    def test_greps_full_text_not_truncated(self):
        # diagnose_dedup 는 캡션을 자른 뒤 grep 해 뒤쪽 히트를 놓친다.
        # ⚠️ docstring 은 그 함정을 **설명**하느라 같은 문자열을 담고 있으니
        # 실행 코드만 검사한다(주석/문서를 소스 grep 으로 재는 함정).
        import ast
        src = Path("trade/scripts/diagnose_badonion.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        # grep 을 하는 함수 하나만 본다 — 파일 전체에 슬라이스를 금지하면
        # 무관한 `msgs[:limit]` 이 엉뚱한 메시지로 실패한다.
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_matches"),
                  None)
        self.assertIsNotNone(fn, "_matches 가 사라짐 — grep 경로가 바뀐 것")
        sliced = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
        ]
        self.assertEqual(sliced, [], "grep 대상 텍스트를 자르면 안 됨")
        self.assertIn("raw_text", src, "서버 원문도 찍어야")


class TestNavOrderRule20260820(unittest.TestCase):
    """nav 표시 순서 규약(사용자 2026-08-20).

    ① 나라별로 묶고 나라는 대시보드 개수 내림차순 ② 나라 안에서 품목별 →
    회사별 ③ 그 다음 수출 → 수입 → 지수.

    ⚠️ 현행 12개 순서를 그대로 적는 테스트는 쓰지 않는다 — 그건 "지금 값"을
    축복할 뿐 규약을 되돌리는 변경을 못 잡는다(실수 #19). 대신 (a) 규약을
    불변식으로 검사하고 (b) **합성 소스**로 계산 자체를 태운다.
    """

    def _mk(self, key, country, basis, flow):
        return srcs.Source(
            key, key, lambda t: None, lambda p: None, lambda *a, **k: False,
            lambda *a, **k: None, f"{key}.db", f"{key}.html", f"{key} nav",
            country=country, basis=basis, flow=flow,
            # nav 규약은 문법 축과 무관하지만 **필수 필드**다(#370) —
            # 기본값을 두면 새 소스가 안 밝히고 지나간다.
            grammars=("hs",))

    def test_countries_are_contiguous_and_by_page_count_desc(self):
        nav = srcs.nav_sources()
        seen: list[str] = []
        for s in nav:
            if not seen or seen[-1] != s.country:
                self.assertNotIn(s.country, seen,
                                 f"{s.country} 가 nav 에서 쪼개짐 — 나라별 묶기 위반")
                seen.append(s.country)
        counts = [
            sum(1 for s in nav if s.country == c)
            + srcs._EXTRA_COUNTRY_PAGES.get(c, 0) for c in seen
        ]
        self.assertEqual(counts, sorted(counts, reverse=True),
                         f"나라 순서가 대시보드 개수 내림차순이 아님: {list(zip(seen, counts))}")

    def test_within_country_item_then_company_then_export_then_import(self):
        """⚠️ `_source_rank` 로 정렬성만 보면 **동어반복**이다 — 랭크표를
        뒤집는 뮤테이션이 통과한다(실측). 의미를 리터럴로 못박는다."""
        nav = srcs.nav_sources()
        for c in {s.country for s in nav}:
            grp = [s for s in nav if s.country == c]
            bases = [s.basis for s in grp]
            self.assertEqual(bases, sorted(bases, key="item company".split().index),
                             f"{c}: 품목별이 회사별보다 앞이어야: {bases}")
            for basis in ("item", "company"):
                flows = [s.flow for s in grp if s.basis == basis]
                # 2026-09-10 `revenue`(월매출) 추가 — 수출·수입·지수 뒤(#222 계약 확장).
                self.assertEqual(
                    flows, sorted(flows, key="export import index revenue".split().index),
                    f"{c}/{basis}: 수출→수입→지수→매출 순이어야: {flows}")

    def test_rule_is_computed_not_transcribed(self):
        """합성 레지스트리로 규약 자체를 태운다 — 하드코딩 튜플로 되돌리면
        여기서 죽는다(현행 12개만 보는 테스트는 통과해 버린다)."""
        syn = (
            self._mk("solo", "혼자", "item", "export"),
            self._mk("bi_co", "둘", "company", "export"),
            self._mk("bi_it", "둘", "item", "export"),
            self._mk("imp", "수입국", "item", "import"),
            self._mk("exp", "수출국", "item", "export"),
        )
        got = srcs._nav_order(syn)
        # '둘'(2개) 이 1개짜리들보다 앞. 그 안에서는 품목 → 회사.
        self.assertEqual(got[:2], ("bi_it", "bi_co"), got)
        # 1개짜리 동률 — 수출이 수입보다 앞(③의 나라 단위 적용).
        self.assertLess(got.index("exp"), got.index("imp"), got)
        self.assertLess(got.index("solo"), got.index("imp"), got)

    def test_extra_country_pages_matches_dashboard_hardcoded_link(self):
        """일본은 레지스트리 밖 jp.html(비온)까지 3개다. dashboard.py 에서
        그 링크가 사라지면 계수가 조용히 어긋나므로 함께 고정한다."""
        dash = Path("trade/dashboard.py").read_text(encoding="utf-8")
        self.assertIn('href="jp.html"', dash,
                      "비온 일본 링크가 사라짐 — _EXTRA_COUNTRY_PAGES 갱신 필요")
        self.assertEqual(srcs._EXTRA_COUNTRY_PAGES, {"일본": 1})
        # ⚠️ 옛 판은 "그 결과 일본이 1순위" 였는데, 중국이 3페이지가 되며
        # 동률이 나자 깨졌다 — 1순위는 규약의 **결과**일 뿐이다(#19).
        # 계약은 "레지스트리 밖 페이지가 나라 계수에 반영된다" 이다.
        #
        # ⚠️ 현행 데이터로는 이 보정이 순서를 **안 바꾼다**(일본은 2페이지
        # 로 세어도 동률에서 이긴다). 실물로 재면 보정을 지우는 뮤테이션이
        # 그대로 통과한다 — 레지스트리가 그러라고 열어 둔 `sources=` 로
        # **합성 소스**를 태워 규약 자체를 본다.
        S = srcs.Source
        def _mk(key, country):
            return S(key, key, lambda _t: None, lambda _p: None,
                     lambda *_a, **_k: False, lambda *_a, **_k: None,
                     f"{key}.db", f"{key}.html", key,
                     country=country, basis="item", flow="export",
                     grammars=("hs",))
        # 가상: '갑' 1개 · '을' 1개 + 레지스트리 밖 1개 → 을이 앞서야 한다.
        synth = (_mk("a1", "갑"), _mk("b1", "을"))
        _orig = srcs._EXTRA_COUNTRY_PAGES
        try:
            srcs._EXTRA_COUNTRY_PAGES = {}
            self.assertEqual(srcs._nav_order(synth)[0], "a1",
                             "동률이면 SOURCES 순서가 갈라야 한다")
            srcs._EXTRA_COUNTRY_PAGES = {"을": 1}
            self.assertEqual(srcs._nav_order(synth)[0], "b1",
                             "레지스트리 밖 페이지가 계수에 안 실린다")
        finally:
            srcs._EXTRA_COUNTRY_PAGES = _orig

    def test_every_source_declares_ordering_axes(self):
        for s in srcs.SOURCES:
            self.assertIn(s.basis, srcs._BASIS_RANK, s.key)
            self.assertIn(s.flow, srcs._FLOW_RANK, s.key)
            self.assertTrue(s.country.strip(), s.key)


class TestSiblingAsofAndFace20260820(unittest.TestCase):
    """2026-08-20 새끼 대시보드 13종 전수 감사에서 나온 두 결함의 회귀.

    ① 13개 형제 페이지 전부 적재·생성 시각이 없어 "이거 최신이야?" 에 화면이
       답하지 못했다(실수 #43·#52). 이제 asof_footer 가 전 페이지 공통이다.
       레지스트리 **전체를 순회**하며 빈 DB 렌더에서 footer 를 단언하므로
       새 소스가 배선을 빠뜨리면 여기서 죽는다(#24 — 열거 고정 금지).
    ② mys 페이지 h1 이 🐯(품목판 my 의 얼굴)로 붙어 nav 의 🐆 와 어긋났고,
       두 페이지가 같은 얼굴을 갖게 됐다 — nav↔h1 첫 이모지 일치를 전 소스에
       기계적으로 강제한다.
    """

    @staticmethod
    def _first_emoji(s):
        for ch in s:
            if ord(ch) > 0x2600:
                return ch
        return None

    def test_every_sibling_page_carries_asof_footer(self):
        import re
        pat = re.compile(r"페이지 생성 \d{4}-\d{2}-\d{2} \d{2}:\d{2} KST")
        with tempfile.TemporaryDirectory() as td:
            for s in srcs.SOURCES:
                if not s.html_file:
                    continue
                db = Path(td) / s.db_file
                s.open_db(db)                       # 빈 스키마
                out = Path(td) / s.html_file
                s.regenerate(db, out, media_url_prefix="../m/")
                html = out.read_text(encoding="utf-8")
                self.assertRegex(html, pat,
                                 f"{s.key}: asof_footer 미배선(빈 DB 렌더)")
                self.assertIn("0개 ", html, f"{s.key}: 0건 카운트 미표시")

    def test_jp_beon_page_carries_asof_footer_too(self):
        """jp.html 은 레지스트리 밖(BeOn)이라 순회에 안 잡힌다 — 별도 고정."""
        import re
        from trade import jp_exports as jp
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "jp.db"
            jp.open_jp_db(db)
            out = Path(td) / "jp.html"
            jp.regenerate(db, out, media_url_prefix="../m/")
            self.assertRegex(out.read_text(encoding="utf-8"),
                             re.compile(r"페이지 생성 \d{4}-\d{2}-\d{2} "
                                        r"\d{2}:\d{2} KST"))

    def test_footer_count_derives_from_rendered_rows(self):
        """footer 의 'N개' 는 렌더에 쓴 리스트의 len — 별도 쿼리로 다시 세면
        총계/소계가 갈라진다(실수 #45). mys 픽스처 1건 → '1개 종목'."""
        from trade import my_stock_exports as mys
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "my_stock.db"
            conn = mys.open_my_stock_db(db)
            mys.ingest(conn, _CAPS["mys"], source_message_id=1,
                       posted_at="2026-08-20T00:00:00Z", media_paths=[])
            out = Path(td) / "my_stock.html"
            mys.regenerate(db, out, media_url_prefix="../m/")
            html = out.read_text(encoding="utf-8")
            self.assertIn("1개 종목", html)
            self.assertIn("최신 2026-07", html)
            self.assertIn("마지막 적재 ", html)

    def test_nav_and_h1_share_the_same_face(self):
        with tempfile.TemporaryDirectory() as td:
            import re
            for s in srcs.SOURCES:
                if not s.html_file:
                    continue
                db = Path(td) / s.db_file
                s.open_db(db)
                out = Path(td) / s.html_file
                s.regenerate(db, out, media_url_prefix="../m/")
                m = re.search(r"<h1[^>]*>(.*?)</h1>",
                              out.read_text(encoding="utf-8"), re.S)
                self.assertIsNotNone(m, s.key)
                self.assertEqual(self._first_emoji(s.nav_label),
                                 self._first_emoji(m.group(1)),
                                 f"{s.key}: nav 라벨과 페이지 h1 의 첫 이모지가 "
                                 "다르다 — 사용자가 다른 페이지로 착각한다")


class TestRelevanceBreakdown20260917(unittest.TestCase):
    """백필 로그가 "N/M units are [레지스트리 전체]" 만 적어, 그 N 건이
    **어느 소스**였는지 말하지 않았다(2026-09-17 사용자 실행 9/402).

    그러면 "한국 수입 회사별이 안 들어온다" 를 물어도 '채널에 그런 글이
    없었다' 와 '우리 파서가 떨어뜨렸다' 가 안 갈린다 — 처방이 정반대다
    (#82·#143). 숫자만 세는 원장은 다음 라운드를 추측으로 만든다(#290).
    """

    class _M:
        def __init__(self, text):
            self.text = text

    _KR_EXPORT = ("8월 수출 한국\n▶️ 삼성전자 — 반도체\n"
                  "26년08월: $158.5M (+105.3% YoY) (+11.8% MoM)")

    def test_breakdown_names_the_source_and_the_zero_ones(self):
        from trade import badonion_sources as srcs
        out = srcs.relevance_breakdown([[self._M(self._KR_EXPORT)]])
        head = out[0]
        self.assertIn("한국 수출(종목별) 1", head)
        # 0건 소스도 **이름을 댄다** — 침묵하면 '검사가 돌았나' 를 모른다(#54).
        zero = " ".join(out[1:])
        self.assertIn("한국 수입(회사별)", zero)
        # ⚠️ 0건은 갈래가 둘이고 **둘째가 이 도구의 존재 이유**다(새 형식을
        # 파서가 못 받은 것 — #83·#261·#330·#332·#370). 주절로 한쪽을
        # 사실처럼 적으면 다른 쪽을 안 보게 만든다(#165·#82, 리뷰 M4).
        self.assertIn("파서가 못 받은", zero)
        self.assertNotIn("없었다는 뜻", zero)
        self.assertNotIn("한국 수출(종목별)", zero)
        # 전 소스가 소계 **또는** 0건 목록 중 하나에 정확히 한 번 나온다(#45).
        # ⚠️ 부분문자열로 세면 '대만' 이 '대만 수출(종목별)' 에 걸려 3 이 된다
        # (#75) — 라벨을 **토큰으로 갈라** 집합으로 잰다.
        got_labels = {t.rsplit(" ", 1)[0]
                      for t in head.split(": ", 1)[1].split(" · ")[0].split(", ")}
        zero_labels = set(out[1].split(": ", 1)[1].split(" — ")[0].split(", "))
        all_labels = {s.label for s in srcs.SOURCES}
        self.assertEqual(all_labels, got_labels | zero_labels)
        self.assertEqual(set(), got_labels & zero_labels, "같은 소스가 양쪽에")

    def test_empty_input_is_said_not_silent(self):
        """대조 0건은 빈 출력이 아니다(#274 빈 출력이 정답인 도구는 없다)."""
        from trade import badonion_sources as srcs
        out = srcs.relevance_breakdown([])
        self.assertIn("(없음)", out[0])
        self.assertTrue(any("0건 소스" in l for l in out))

    def test_matching_keys_returns_every_source_that_takes_it(self):
        """한 캡션이 둘 이상에 걸릴 수 있으므로 **전부** 돌려준다 — 먼저
        걸린 하나만 세면 소계 합이 총계와 어긋난다(#45).

        ⚠️ 오늘 레지스트리에선 이 캡션이 **한 소스에만** 걸려, 첫 매치만
        돌려주는 변형이 그대로 통과했다(리뷰 M3 실측 · #91c 픽스처가
        충분히 센가). 계약이 발화하려면 **둘이 받는 상태**가 있어야 하므로
        합성 소스 둘로 태운다(#291 발화 경로 없는 가드는 가드가 아니다).
        """
        from unittest import mock
        from trade import badonion_sources as srcs
        got = srcs.matching_keys(self._KR_EXPORT)
        self.assertTrue(got, "관련 캡션인데 키가 0개다")
        self.assertEqual(bool(got), srcs.is_relevant(self._KR_EXPORT))
        self.assertEqual((), srcs.matching_keys("오늘 점심 뭐 먹지"))
        self.assertFalse(srcs.is_relevant("오늘 점심 뭐 먹지"))

        class _S:                       # 합성 — 원천 소스가 아니다(#165)
            def __init__(self, key):
                self.key, self.label = key, f"합성 {key}"

            def parse(self, text):
                return {"ok": 1} if "겹침" in text else None

        with mock.patch.object(srcs, "SOURCES", [_S("a"), _S("b")]):
            self.assertEqual(("a", "b"), srcs.matching_keys("겹침"))
            out = srcs.relevance_breakdown([[self._M("겹침")]])
            self.assertIn("합성 a 1", out[0])
            self.assertIn("합성 b 1", out[0])
            # 소계 합(2) > 유닛 수(1) 이면 그 사실을 적는다(#45).
            self.assertIn("중복 계수", out[0])
            self.assertEqual(1, len(out), "0건 소스가 없는데 줄이 붙었다")

    def test_backfill_logs_the_breakdown(self):
        """배선은 존재가 아니라 **호출**이다(#20·#120). telethon 이 없어
        import 를 못 하므로 AST 로 본다 — 그게 이 검사가 못 보는 축이다
        (호출은 하되 로그를 안 찍는 변형은 못 잡는다, #274)."""
        import ast
        import pathlib
        tree = ast.parse(pathlib.Path(
            "trade/scripts/backfill_badonion.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "relevance_breakdown"]
        self.assertTrue(calls, "백필이 소스별 계수를 안 부른다")


class TestSyncRecoveryWindow20260924(unittest.TestCase):
    """파서가 생기기 전에 버려진 캡션을 동기화가 **스스로** 회수한다(실수 #403).

    2026-09-16 사용자가 채널에서 본 한국 수입 회사별 캡션(텔레칩스)이 09-24
    까지 보드에 없었다. 관련성 필터 = 파서라 파서 배포 전 캡션은 리스너가
    버리고, 6시간 동기화는 3일만 보므로 영영 못 줍는다. "파서 배포는 백필
    수동 회수까지가 한 세트"(#261·#330)를 사람 손에 맡겨 두 번 실패했다
    (#370·#371) → 필터 코드 지문이 바뀌면 다음 동기화가 한 번 넓게 훑는다.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.state = self.dir / srcs.SYNC_STATE_NAME

    def tearDown(self):
        self._tmp.cleanup()

    # ── 창 판정 ──────────────────────────────────────────────────────────
    def test_first_run_without_a_record_scans_the_recovery_window(self):
        plan = srcs.sync_plan(self.state, fp="aaaaaaaaaa")
        self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"])
        self.assertTrue(plan["record"])
        self.assertIn("기록 없음", plan["reason"])
        # 기록이 없을 땐 바뀌었는지조차 모른다 — '바뀌었다' 를 주장하지
        # 않는다(#165). 대신 무엇을 하는지는 말한다.
        self.assertNotIn("바뀌었다", plan["reason"])
        self.assertIn(f"{srcs.RECOVERY_LOOKBACK_DAYS}일", plan["reason"])

    def test_recovery_happens_once_then_settles_to_the_default(self):
        """수렴 — 회수가 성공해 기록되면 다음부터는 기본 창이다(#171)."""
        self.state.write_text('{"relevance_fp": "oldoldold0"}', encoding="utf-8")
        plan = srcs.sync_plan(self.state, fp="newnewnew0")
        self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"])
        self.assertTrue(plan["record"])
        self.assertIn("oldoldold0 → newnewnew0", plan["reason"])
        self.assertIn("필터가 쓰는 모듈이 바뀌었다", plan["reason"])
        self.assertTrue(srcs.record_sync(self.state, plan["fp"]))
        again = srcs.sync_plan(self.state, fp="newnewnew0")
        self.assertEqual(srcs.DEFAULT_LOOKBACK_DAYS, again["days"])
        self.assertFalse(again["record"])

    def test_default_and_recovery_windows_are_the_chosen_contract(self):
        # 리터럴로 못박는다 — 상수로 상수를 검증하면 동어반복이다(#66).
        # ⚠️ 측정값이 아니라 **고른 값**이다(리뷰 L6): 3일 = 옛 기본 창(리스너
        # 다운타임 안전망, 타이머 6시간보다 넓다) · 40일 = 월간 발행의 가장
        # 최근 한 회가 들도록 31일 + 여유 · 재시도 3회 · 자동 회수 상한 100유닛.
        self.assertEqual(3, srcs.DEFAULT_LOOKBACK_DAYS)
        self.assertEqual(40, srcs.RECOVERY_LOOKBACK_DAYS)
        self.assertEqual(3, srcs.RECOVERY_MAX_ATTEMPTS)
        self.assertEqual(100, srcs.RECOVERY_MAX_UNITS)

    def test_since_wins_over_lookback_like_the_backfill_does(self):
        """백필은 둘 다 받으면 `--since` 로 훑는다 — 판정이 `--lookback-days`
        를 먼저 보면 4일만 훑고도 '덮었다' 로 기록해 회수가 영영 사라진다
        (독립 리뷰 M2)."""
        narrow = srcs.sync_plan(self.state, fp="g" * 10, now=self._NOW,
                                since="2026-09-20", lookback_days=45)
        self.assertIsNone(narrow["days"])           # 창은 --since 다
        self.assertFalse(narrow["record"])
        wide = srcs.sync_plan(self.state, fp="g" * 10, now=self._NOW,
                              since="2026-08-01", lookback_days=5)
        self.assertIsNone(wide["days"])
        self.assertTrue(wide["record"])

    def test_only_the_automatic_recovery_is_marked_as_one(self):
        """포워드 상한·알림 문구는 **자동** 회수에만 — 사람이 명시한 창은 그 사람의
        결정이다(리뷰 L5)."""
        self.assertTrue(srcs.sync_plan(self.state, fp="h" * 10)["recovery"])
        for kw in ({"lookback_days": 45}, {"lookback_days": 3},
                   {"since": "2026-08-01"}):
            self.assertFalse(srcs.sync_plan(self.state, fp="h" * 10,
                                            now=self._NOW, **kw)["recovery"], kw)
        srcs.record_sync(self.state, "h" * 10)
        self.assertFalse(srcs.sync_plan(self.state, fp="h" * 10)["recovery"])
        self.assertFalse(srcs.sync_plan(self.state, fp="")["recovery"])

    def test_a_partly_failed_recovery_is_retried_a_bounded_number_of_times(self):
        """일부 실패한 회수는 기록하지 않는다(일시 장애도 같은 경로로 온다) —
        그러나 정말 지워진 메시지는 매번 실패하므로 3회째엔 기록한다(#171).
        옛 지문은 그대로 둬야 다음 틱이 다시 넓게 훑는다(리뷰 M1)."""
        self.state.write_text('{"relevance_fp": "oldoldold0"}', encoding="utf-8")
        for n in range(1, srcs.RECOVERY_MAX_ATTEMPTS):
            done, why = srcs.finish_recovery(self.state, "newnewnew0",
                                             failed_units=2)
            self.assertFalse(done, n)
            self.assertIn(f"{n}/{srcs.RECOVERY_MAX_ATTEMPTS}", why)
            # 무엇을 다시 훑는지 정확히 — 자동 재시도는 최근 회수 창만 본다.
            # 더 넓은 명시 창의 그 밖 실패까지 '다시 시도한다' 고 하면 거짓이다.
            self.assertIn(f"최근 {srcs.RECOVERY_LOOKBACK_DAYS}일", why)
            rec = __import__("json").loads(self.state.read_text(encoding="utf-8"))
            self.assertEqual("oldoldold0", rec["relevance_fp"])   # 옛 지문 보존
            self.assertEqual({"fp": "newnewnew0", "count": n},
                             {k: rec["retry"][k] for k in ("fp", "count")})
            plan = srcs.sync_plan(self.state, fp="newnewnew0")
            self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"])
            # 사유가 **실제 횟수**를 말한다(2차 리뷰 S17 — 늘 1회째라고 해도
            # 창 판정은 맞아 이 줄만 거짓말한다).
            self.assertIn(f"재시도 {n}회째", plan["reason"])
        done, why = srcs.finish_recovery(self.state, "newnewnew0", failed_units=2)
        self.assertTrue(done)
        # 횟수는 유닛별이 아니라 실행별이다 — 3회째에 처음 실패한 유닛도 여기서
        # 멈추므로 '영구 실패' 로 단정하지 않는다(#165, 2차 리뷰). 몇 회째
        # 실행이었는지와 msg id 를 어디서 보는지만 말한다.
        self.assertIn("재시도 상한", why)
        self.assertIn(f"회수 {srcs.RECOVERY_MAX_ATTEMPTS}회째", why)
        # 실패한 msg id 를 찾을 **두** 줄을 다 가리킨다 — 포워드 실패는 즉시
        # 실패(`permanent forward failure`)와 FloodWait 5회 소진(`giving up on
        # msgs`)으로 끝난다(`backfill_badonion._forward_unit`). 한쪽만 적으면
        # 다른 갈래의 id 를 못 찾는다.
        self.assertIn("permanent forward failure", why)
        self.assertIn("giving up on msgs", why)
        self.assertNotIn("영구 실패", why)
        rec = __import__("json").loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual({"relevance_fp", "recorded_at"}, set(rec))  # 표식 정리

    def test_the_give_up_message_points_at_log_lines_that_exist(self):
        """상한 문구가 가리키는 로그 줄이 백필에 **실제로** 있어야 한다 — 로그
        문구를 바꾸면 안내가 없는 줄을 가리킨다(#371 지어낸 안내 · #55). 백필은
        telethon 없이 import 할 수 없어 AST 의 문자열 상수로 본다."""
        import ast
        import re
        self.state.write_text(json.dumps(
            {"retry": {"fp": "samesame00",
                       "count": srcs.RECOVERY_MAX_ATTEMPTS - 1}}),
            encoding="utf-8")
        done, why = srcs.finish_recovery(self.state, "samesame00",
                                         failed_units=1)
        self.assertTrue(done)
        pointers = re.findall(r"'([^']+)'", why)
        self.assertGreaterEqual(len(pointers), 2, why)
        tree = ast.parse(Path("trade/scripts/backfill_badonion.py").read_text(
            encoding="utf-8"))
        consts = [n.value for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for ptr in pointers:
            self.assertTrue(any(c.startswith(ptr) for c in consts),
                            f"백필에 '{ptr}' 로 시작하는 로그 줄이 없다")

    def test_a_retry_marker_reopens_the_window_even_when_the_fp_is_recorded(self):
        """명시 회수(`--lookback-days 40`)가 **이미 기록된 지문**으로 일부
        실패하면 재시도 표식만 남는다. 옛 판은 기록된 지문을 먼저 보고 표식을
        안 읽어 다음 자동 동기화가 3일로 돌아갔다 — "다음 동기화가 다시
        시도한다" 는 로그가 거짓이었고, 3일보다 오래된 실패는 영영 안
        돌아왔다(2차 리뷰 P2)."""
        srcs.record_sync(self.state, "samesame00")
        done, _ = srcs.finish_recovery(self.state, "samesame00", failed_units=1)
        self.assertFalse(done)
        plan = srcs.sync_plan(self.state, fp="samesame00")
        self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"])
        self.assertTrue(plan["record"])
        self.assertTrue(plan["recovery"])      # 자동으로 넓힌 창이다
        self.assertIn("재시도 1회째", plan["reason"])
        self.assertIn("포워드 실패가 남아", plan["reason"])
        # 반대 증거(#25) — **다른** 지문의 표식은 이 지문의 재시도가 아니다.
        # 그걸로 창을 열면 옛 배포의 실패가 새 기록을 영원히 흔든다.
        self.state.write_text(json.dumps(
            {"relevance_fp": "samesame00",
             "retry": {"fp": "otherother", "count": 2}}), encoding="utf-8")
        plan = srcs.sync_plan(self.state, fp="samesame00")
        self.assertEqual(srcs.DEFAULT_LOOKBACK_DAYS, plan["days"])
        self.assertFalse(plan["record"])

    def test_a_stale_marker_of_another_fingerprint_is_named(self):
        """기록 없이 옛 지문의 표식만 남았을 때 '기록 형식이 다르다' 고 하면
        파일이 깨진 줄 알고 지우러 간다(#82 — 갈래는 이름으로)."""
        self.state.write_text(json.dumps(
            {"retry": {"fp": "otherother", "count": 1}}), encoding="utf-8")
        plan = srcs.sync_plan(self.state, fp="newnewnew0")
        self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"])
        self.assertIn("다른 지문의 재시도 표식만 남았다", plan["reason"])
        self.assertNotIn("재시도 1회째", plan["reason"])

    def test_a_broken_retry_count_neither_crashes_nor_drops_the_retry(self):
        """상태 파일은 사람이 고칠 수도 깨질 수도 있다. 옛 판은 `int("x")` 가
        `finish_recovery` 에서 새 **포워드를 끝낸** 동기화가 트레이스백으로
        끝났다(2차 리뷰 P6). 그렇다고 횟수를 못 읽었다는 이유로 '재시도가
        남았다' 는 사실까지 버리면 그 회수가 조용히 사라진다(#54)."""
        for count in ('"x"', "null", "Infinity", "-2", "[]", '{"a": 1}'):
            with self.subTest(count=count):
                self.state.write_text(
                    '{"relevance_fp": "samesame00", "retry": {"fp": '
                    f'"samesame00", "count": {count}}}}}', encoding="utf-8")
                plan = srcs.sync_plan(self.state, fp="samesame00")
                self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"])
                self.assertIn("횟수를 못 읽었다", plan["reason"])
                done, why = srcs.finish_recovery(self.state, "samesame00",
                                                 failed_units=1)
                self.assertFalse(done)
                self.assertIn(f"1/{srcs.RECOVERY_MAX_ATTEMPTS}", why)
                rec = json.loads(self.state.read_text(encoding="utf-8"))
                self.assertEqual(1, rec["retry"]["count"])   # 새로 센다
                self.assertEqual("samesame00", rec["relevance_fp"])

    def test_a_failed_replace_leaves_the_old_state_intact(self):
        """원자 쓰기를 **잰다**(2차 리뷰 S11 — 옛 이름의 테스트는 .tmp 가 안
        남는지만 봐, 제자리 쓰기로 바꿔도 통과했다). 교체가 실패하면 옛 기록이
        한 바이트도 안 바뀌어야 한다 — 쓰다 만 파일은 '기록 못 읽음' 이 되어
        40일 회수를 한 번 더 부른다(#379)."""
        from unittest import mock
        srcs.record_sync(self.state, "oldoldold0")
        before = self.state.read_bytes()
        with mock.patch.object(Path, "replace",
                               side_effect=OSError("교체 실패(테스트)")):
            with self.assertRaises(OSError):
                srcs.record_sync(self.state, "newnewnew0")
            with self.assertRaises(OSError):
                srcs.finish_recovery(self.state, "newnewnew0", failed_units=1)
        self.assertEqual(before, self.state.read_bytes())

    def test_retries_count_per_fingerprint_and_a_clean_run_records_at_once(self):
        srcs.finish_recovery(self.state, "aaaaaaaaa1", failed_units=1)
        srcs.finish_recovery(self.state, "aaaaaaaaa1", failed_units=1)
        # 지문이 또 바뀌면(다른 배포) 횟수는 새로 센다 — 옛 실패를 끌어오지 않는다.
        done, why = srcs.finish_recovery(self.state, "bbbbbbbbb2",
                                         failed_units=1)
        self.assertFalse(done)
        self.assertIn(f"1/{srcs.RECOVERY_MAX_ATTEMPTS}", why)
        # 기록이 아직 없으면 사유가 재시도 중이라고 말한다(#82).
        self.assertIn("재시도 1회째",
                      srcs.sync_plan(self.state, fp="bbbbbbbbb2")["reason"])
        done, _ = srcs.finish_recovery(self.state, "bbbbbbbbb2", failed_units=0)
        self.assertTrue(done)
        self.assertFalse(srcs.finish_recovery(self.state, "", failed_units=0)[0])

    def test_the_state_write_is_atomic_and_needs_no_other_module(self):
        """price_provider 를 빌려 쓰면 그 모듈의 ImportError 가 `except OSError`
        밖으로 새 성공한 동기화가 트레이스백으로 끝난다(리뷰 L3)."""
        from unittest import mock
        from trade import price_provider as pp

        def _boom(*a, **k):
            raise RuntimeError("빌려 쓰면 안 된다")

        with mock.patch.object(pp, "_atomic_write_json", _boom):
            self.assertTrue(srcs.record_sync(self.state, "cccccccccc"))
        self.assertEqual("cccccccccc",
                         srcs.sync_plan(self.state, fp="cccccccccc")["fp"])
        self.assertEqual([self.state.name],
                         sorted(p.name for p in self.dir.iterdir()))  # .tmp 없음

    def test_an_unreadable_record_is_named_and_not_trusted(self):
        for body, why in (("{broken", "JSONDecodeError"),
                          ("[]", "기록 형식이 다르다"),
                          ('{"relevance_fp": ""}', "기록 형식이 다르다")):
            self.state.write_text(body, encoding="utf-8")
            plan = srcs.sync_plan(self.state, fp="bbbbbbbbbb")
            self.assertEqual(srcs.RECOVERY_LOOKBACK_DAYS, plan["days"], body)
            self.assertIn(why, plan["reason"], body)

    # 시계는 **주입**한다 — 실제 오늘로 재면 `since="2026-09-01"` 이 11월엔
    # 40일을 넘어 판정이 뒤집힌다(#249·#291 날짜 리터럴 시한폭탄).
    _NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

    def test_an_explicit_narrow_window_neither_recovers_nor_records(self):
        """좁을 수 있는 창을 회수로 치면 다 된 줄 알고 다시 안 훑는다."""
        cases = (({"since": "2026-09-01"}, None),       # 23일
                 ({"lookback_days": 7}, 7),
                 ({"lookback_days": 39}, 39),
                 ({"to": "2026-09-10"}, srcs.DEFAULT_LOOKBACK_DAYS),
                 ({"since": "2026-01-01", "to": "2026-09-10"}, None),
                 ({"since": "nonsense"}, None))
        for kw, days in cases:
            plan = srcs.sync_plan(self.state, fp="cccccccccc", now=self._NOW, **kw)
            self.assertEqual(days, plan["days"], kw)
            self.assertFalse(plan["record"], kw)
            self.assertIn("명시", plan["reason"], kw)
            self.assertIn("덮지 않아", plan["reason"], kw)
        # ⚠️ `--lookback-days 0` 도 명시다 — 0 을 '없음' 으로 읽으면 사람이
        # 고른 창을 회수 창이 덮는다(#235·#297 `or` 가 값을 지운다).
        self.assertEqual(0, srcs.sync_plan(self.state, fp="c" * 10, now=self._NOW,
                                           lookback_days=0)["days"])

    def test_an_explicit_window_that_covers_the_recovery_window_records(self):
        """수렴 — 자동 회수가 후보 상한에 걸려 중단되면 알림이 명시 실행을
        시킨다. 그 넓은 실행까지 무시하면 6시간마다 같은 중단이 영원히 반복된다
        (#171). 덮는 창이 성공하면 기록한다."""
        for kw, days in (({"lookback_days": 40}, 40),
                         ({"lookback_days": 90}, 90),
                         ({"since": "2026-08-15"}, None)):   # 40일
            plan = srcs.sync_plan(self.state, fp="eeeeeeeeee", now=self._NOW, **kw)
            self.assertEqual(days, plan["days"], kw)
            self.assertTrue(plan["record"], kw)
            self.assertEqual("eeeeeeeeee", plan["fp"], kw)
            self.assertIn("덮으므로", plan["reason"], kw)
        # 지문을 못 재면 덮어도 기록하지 않는다(#54).
        plan = srcs.sync_plan(self.state, fp="", now=self._NOW, lookback_days=40)
        self.assertFalse(plan["record"])
        self.assertIn("기록 불가", plan["reason"])

    def test_an_unknown_fingerprint_is_said_and_never_recorded(self):
        plan = srcs.sync_plan(self.state, fp="")
        self.assertEqual(srcs.DEFAULT_LOOKBACK_DAYS, plan["days"])
        self.assertFalse(plan["record"])
        self.assertIn("판정 불가", plan["reason"])
        self.assertFalse(srcs.record_sync(self.state, ""))
        self.assertFalse(self.state.exists(), "못 잰 지문을 기록했다")

    def test_the_plan_computes_the_real_fingerprint_when_not_given(self):
        """배선 — `fp` 를 안 주면 제품 지문을 잰다(백필은 인자 없이 부른다)."""
        from unittest import mock
        with mock.patch.object(srcs, "relevance_fingerprint",
                               return_value="dddddddddd") as fp:
            plan = srcs.sync_plan(self.state)
        fp.assert_called_once_with()
        self.assertEqual("dddddddddd", plan["fp"])
        with mock.patch.object(srcs, "relevance_fingerprint") as fp:
            srcs.sync_plan(self.state, since="2026-09-01", now=self._NOW)
        fp.assert_not_called()      # 회수 창을 안 덮는 명시 창엔 지문이 필요 없다
        with mock.patch.object(srcs, "relevance_fingerprint",
                               return_value="ffffffffff") as fp:
            plan = srcs.sync_plan(self.state, lookback_days=40, now=self._NOW)
        fp.assert_called_once_with()
        self.assertEqual("ffffffffff", plan["fp"])

    # ── 지문 ─────────────────────────────────────────────────────────────
    def _pkg(self, **files):
        base = self.dir / "fakepkg"
        base.mkdir(exist_ok=True)
        (base / "__init__.py").write_text("", encoding="utf-8")
        for name, body in files.items():
            (base / f"{name}.py").write_text(body, encoding="utf-8")
        return base

    def _fp(self, base, roots=("fakepkg.adapter",)):
        return srcs.relevance_fingerprint(roots=roots, base_dir=base)

    def test_the_fingerprint_follows_imports_transitively(self):
        """kri 의 파서 함수는 어댑터에 있지만 문법은 엔진에 산다 — 엔진만
        고친 배포를 못 보면 이 장치가 막으려던 유실이 재발한다(#365)."""
        base = self._pkg(
            adapter="from fakepkg import engine\n\ndef parse(t):\n"
                    "    return engine.parse(t)\n",
            engine="from fakepkg.util import MARK\n\ndef parse(t):\n"
                   "    return MARK in t\n",
            util="MARK = '수입'\n",
            unrelated="X = 1\n")
        fp1 = self._fp(base)
        self.assertRegex(fp1, r"^[0-9a-f]{10}$")
        (base / "util.py").write_text("MARK = '수출'\n", encoding="utf-8")
        fp2 = self._fp(base)
        self.assertNotEqual(fp1, fp2, "두 단계 아래 모듈의 변경을 못 봤다")
        (base / "unrelated.py").write_text("X = 2\n", encoding="utf-8")
        self.assertEqual(fp2, self._fp(base), "필터와 무관한 모듈이 지문을 흔든다")

    def test_the_fingerprint_ignores_comments_and_docstrings(self):
        """설명만 고친 배포가 40일 회수를 부르면 안 된다(#266)."""
        base = self._pkg(
            adapter='"""옛 설명."""\nfrom fakepkg import engine\n\n'
                    'def parse(t):\n    """옛."""\n    return engine.parse(t)\n',
            engine="def parse(t):\n    return '수입' in t\n")
        fp1 = self._fp(base)
        (base / "adapter.py").write_text(
            '"""새 설명 — 훨씬 길다."""\nfrom fakepkg import engine  # 주석\n\n'
            'def parse(t):\n    """새."""\n    # 주석 한 줄\n'
            '    return engine.parse(t)\n', encoding="utf-8")
        self.assertEqual(fp1, self._fp(base))
        (base / "engine.py").write_text(
            "def parse(t):\n    return '수출' in t\n", encoding="utf-8")
        self.assertNotEqual(fp1, self._fp(base), "코드 변경을 못 봤다")

    def test_relative_imports_are_followed(self):
        """지금 trade 엔 상대 import 가 없다 — 생기는 날 조용히 새지 않게."""
        base = self._pkg(
            adapter="from . import engine\n\ndef parse(t):\n"
                    "    return engine.parse(t)\n",
            engine="def parse(t):\n    return 1\n")
        fp1 = self._fp(base)
        (base / "engine.py").write_text("def parse(t):\n    return 2\n",
                                        encoding="utf-8")
        self.assertNotEqual(fp1, self._fp(base))

    def test_plain_import_statements_are_followed(self):
        """`import fakepkg.util` 형태 — 지금 trade 엔 없지만 생기면 샌다(리뷰 S11)."""
        base = self._pkg(
            adapter="import fakepkg.util\n\ndef parse(t):\n"
                    "    return fakepkg.util.MARK in t\n",
            util="MARK = '수입'\n")
        fp1 = self._fp(base)
        (base / "util.py").write_text("MARK = '수출'\n", encoding="utf-8")
        self.assertNotEqual(fp1, self._fp(base))

    def test_package_init_code_is_covered(self):
        """`from fakepkg import MARK` 의 MARK 가 패키지 `__init__.py` 에 산다 —
        오늘 `trade/__init__.py` 는 비어 있어 이 경로가 안 걸렸다(리뷰 S15)."""
        base = self._pkg(
            adapter="from fakepkg import MARK\n\ndef parse(t):\n"
                    "    return MARK in t\n")
        (base / "__init__.py").write_text("MARK = '수입'\n", encoding="utf-8")
        fp1 = self._fp(base)
        (base / "__init__.py").write_text("MARK = '수출'\n", encoding="utf-8")
        self.assertNotEqual(fp1, self._fp(base))

    def test_a_missing_or_broken_module_means_no_fingerprint(self):
        """덜 덮은 지문을 전부 덮은 것처럼 내지 않는다(#364·#365·#54)."""
        base = self._pkg(adapter="from fakepkg import engine\n",
                         engine="def parse(t):\n    return 1\n")
        self.assertTrue(self._fp(base))
        self.assertEqual("", self._fp(base, roots=("fakepkg.adapter",
                                                   "fakepkg.nope")))
        (base / "engine.py").write_text("def parse(t)\n", encoding="utf-8")
        self.assertEqual("", self._fp(base))

    def test_roots_are_derived_from_the_registry(self):
        """이름 열거가 아니라 레지스트리에서 — 소스를 더하면 저절로 덮인다(#24)."""
        from unittest import mock
        roots = srcs.filter_roots()
        self.assertIn("trade.badonion_sources", roots)
        self.assertIn("trade.kr_stock_imports", roots)      # kri 어댑터
        self.assertIn("trade.kr_company_flow",
                      srcs.filter_closure(roots))            # 그 엔진
        self.assertTrue(srcs.relevance_fingerprint())
        with mock.patch.object(srcs, "SOURCES", []):
            self.assertEqual(("trade.badonion_sources",), srcs.filter_roots())

    # ── 포워드할 유닛 줄 ─────────────────────────────────────────────────
    def test_unit_labels_names_every_source_in_registry_order(self):
        _M = TestRelevanceBreakdown20260917._M
        cap = TestRelevanceBreakdown20260917._KR_EXPORT
        self.assertEqual("한국 수출(종목별)", srcs.unit_labels([_M(cap)]))
        self.assertEqual("(없음)", srcs.unit_labels([_M("오늘 점심 뭐 먹지")]))
        # 앨범 — 캡션 없는 사진 멤버가 섞여도 캡션 멤버가 정한다.
        self.assertEqual("한국 수출(종목별)",
                         srcs.unit_labels([_M(""), _M(cap)]))
        from unittest import mock

        class _S:                       # 합성 — 원천 소스가 아니다(#165)
            def __init__(self, key):
                self.key, self.label = key, f"합성 {key}"

            def parse(self, text):
                return {"ok": 1} if "겹침" in text else None

        with mock.patch.object(srcs, "SOURCES", [_S("b"), _S("a")]):
            self.assertEqual("합성 b, 합성 a", srcs.unit_labels([_M("겹침")]))



if __name__ == "__main__":
    unittest.main()
