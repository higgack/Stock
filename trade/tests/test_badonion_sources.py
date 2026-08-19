"""나쁜양파 소스 단일 레지스트리 회귀.

2026-08-16: 파서 목록이 5개 파일에 중복(49회 언급)돼 있었고, 한국 수출을
추가하며 **로그 문구가 실제로 어긋났다** — 필터는 한국을 통과시키는데
백필 로그는 '대만·중국·일본·태국·말레이시아·필리핀·멕시코 수출/미국 수입'
만 나열했다. 드리프트를 구조적으로 불가능하게 만든 뒤 그 계약을 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from trade import badonion_sources as srcs

_CAPS = {
    "tw": "6월 수출 대만\n\n▶️ 테스트\n\n26년06월: $9.1M  (+3.0% YoY)  (+1.0% MoM)",
    "us": "🇺🇸 6월 수입 미국\n\n▶️ 테스트 품목\n\n"
          "26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)",
    "krs": "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +260.2%",
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
             "uppi", "krs", "jps"])
        # 계약은 리터럴 목록이 아니라 **품목(HS) 기준이 먼저, 종목(회사)
        # 기준이 뒤**다 — 종목 파서가 더 좁은 마커라 앞서면 품목 캡션을
        # 가로챌 위험이 없고, 반대로 앞서면 순서 의존이 생긴다.
        stock_keys = {"krs", "jps"}
        self.assertEqual(set(keys[-len(stock_keys):]), stock_keys,
                         "종목(회사) 기준 소스는 뒤쪽에 몰려 있어야")

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
                "parse_jp_stock_export")
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
        self.assertEqual(srcs.NAV_ORDER[0], "jp2")
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


if __name__ == "__main__":
    unittest.main()
