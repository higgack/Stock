"""trade.scripts.jp_master_probe — 일본 마스터 원천을 **재기만** 하는 프로브.

계약 다섯: (a) 진입점이 실제로 돈다(#252 — `main` 을 안 태우면 광고한 CLI 가
죽은 채 배포된다) (b) 보드 스캔이 **제품과 같은 코드 모양**을 쓰고 표 이름을
박지 않는다(#35·#24) (c) 시장 선언은 **AST 로** 읽는다(독스트링이 대신
만족시키면 안 된다, #59b) (d) 운영 DB 를 한 바이트도 안 바꾼다(#264·#283·
#321) (e) **바깥으로 한 번도 안 나간다** — 이 프로브는 자식 인터프리터로
yfinance 를 치므로 루트 conftest 의 소켓 그물이 **부모만** 덮는다(독립 리뷰
2026-09-22 실측: `make test` 한 번에 Yahoo ~6 · JPX 9 요청). 경계를 클래스
차원에서 막고, 그 사실 자체를 회귀로 못박는다(#30·#312·#336·#344).
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from trade.badonion_sources import SOURCES
from trade.scripts import jp_master_probe as jp


def _seed(data_dir: Path) -> None:
    """제품 ingest 경로로 심는다 — 손으로 INSERT 하면 스키마 위반을 삼킨다(#323)."""
    src = {s.key: s for s in SOURCES}
    caps = {
        "mys": ["Renesas Electronics Corporation (6723)\n말레이시아 수출\n"
                "26년 8월 Update\n\n수출액 YoY: +2.0%",
                "삼성SDI (006400)\n말레이시아 수출\n26년 8월 Update\n\n"
                "수출액 YoY: +97.0%"],
        "cns": ["Taiyo Yuden (6976)\n중국 수출\n26년 8월 Update\n\n"
                "수출액 YoY: +40.5%"],
        "jps": ["Taiyo Yuden (6976)\n일본 수출\n26년 8월 Update\n\n"
                "수출액 YoY: +3.0%"],
    }
    for key, cs in caps.items():
        s = src[key]
        conn = s.open_db(data_dir / s.db_file)
        for c in cs:
            s.ingest(conn, c)
        conn.commit()
        conn.close()


class BoardScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        _seed(self.dir)

    def test_scan_uses_the_product_code_shape_not_a_narrower_copy(self):
        """제품은 `285A` 같은 신형 JPX 코드를 링크한다 — 프로브가 더 좁으면
        화면이 거는 코드를 못 세고 '없다' 로 오보한다(#35·#38 실측)."""
        from trade import stock_link as sl
        self.assertIs(jp._JP_CODE, sl._JP_CODE)
        self.assertTrue(jp._JP_CODE.match("285A"))

    def test_scan_collects_codes_and_skips_korean_six_digit(self):
        rows, errs = jp.board_jp_candidates(self.dir)
        self.assertEqual(errs, [])
        self.assertTrue(rows, "한 건도 못 셌다 — 0건은 통과가 아니다(#54)")
        self.assertNotIn("006400", {t for _, t, _ in rows})

    def test_scan_reads_every_company_board_not_one_hardcoded_table(self):
        """표 이름을 박으면 새 보드가 조용히 빠진다(#24) — 보드가 셋은 잡혀야."""
        boards = {k for k, _, _ in jp.board_jp_candidates(self.dir)[0]}
        self.assertGreaterEqual(len(boards), 3, boards)
        self.assertIn("mys", boards)
        self.assertIn("cns", boards)

    def test_scan_carries_the_caption_name_not_just_the_code(self):
        """이름이 없으면 ④ 가 대조를 못 한다 — 값으로 못박는다(#123 계열)."""
        got = {(t, n) for _, t, n in jp.board_jp_candidates(self.dir)[0]}
        self.assertIn(("6723", "Renesas Electronics Corporation"), got)

    def test_an_unreadable_board_is_reported_not_swallowed(self):
        """삼키면 '보드에 없다' 와 '못 읽었다' 가 같은 화면이 된다(#82·#143)."""
        (self.dir / "cn_stock.db").write_bytes(b"this is not a sqlite file")
        rows, errs = jp.board_jp_candidates(self.dir)
        self.assertTrue(errs, "읽기 실패를 한 줄도 안 남겼다")
        self.assertTrue(any("cn_stock.db" in e for e in errs), errs)
        self.assertTrue(rows, "읽을 수 있는 보드까지 버렸다")


class DeclaringMarketTests(unittest.TestCase):
    def test_declaration_is_read_from_real_call_keywords(self):
        decl = jp.declaring_markets()
        self.assertEqual(decl.get("jps"), "JP")
        self.assertEqual(decl.get("krs"), "KR")

    def test_boards_that_declare_nothing_stay_out(self):
        """선언 안 한 보드를 넣으면 #400 이 막은 그 오링크가 되살아난다."""
        decl = jp.declaring_markets()
        for key in ("mys", "cns", "twr"):
            self.assertNotIn(key, decl, f"{key} 는 시장을 선언하지 않는다")

    def test_a_docstring_mentioning_local_does_not_count_as_a_declaration(self):
        """소스 문자열을 훑으면 설명이 스스로 걸린다(#59b) — **제품 함수**를
        태운다. 판정을 테스트가 다시 구현하면 동어반복이다(#286·#19)."""
        import textwrap
        src = textwrap.dedent('''
            """이 보드는 local="JP" 를 넘기지 **않는다**."""
            # local="KR" 도 주석일 뿐이다
            X = 'local="TW"'
        ''')
        self.assertIsNone(jp.declared_local(src))

    def test_a_real_keyword_argument_is_found(self):
        """반대 증거 — 진짜 선언은 잡혀야 한다(#25)."""
        self.assertEqual(
            jp.declared_local('f(a, b, local="JP", kr_master=None)'), "JP")

    def test_unparsable_source_is_none_not_a_crash(self):
        self.assertIsNone(jp.declared_local("def ("))


class LinkAndSampleTests(unittest.TestCase):
    """⑤⑥ 의 순수 부분 — 호출부가 못 잡는 자리를 값으로 못박는다(#20·#291)."""

    def test_relative_links_join_against_the_page_origin_not_a_fixed_host(self):
        links, total = jp._file_links(
            '<a href="/x/a.xls">a</a><a href="../b.csv">b</a>',
            "https://example.test/dir/page.html")
        self.assertEqual(links, ["https://example.test/x/a.xls",
                                 "https://example.test/b.csv"])
        self.assertEqual(total, 2)

    def test_absolute_links_survive_and_duplicates_collapse(self):
        links, _ = jp._file_links(
            '<a href="https://other.test/z.xlsx">z</a>'
            '<a href="/x/a.xls">a</a><a href="/x/a.xls">a again</a>',
            "https://example.test/p.html")
        self.assertEqual(links, ["https://other.test/z.xlsx",
                                 "https://example.test/x/a.xls"])

    def test_total_href_count_separates_no_files_from_unreadable_markup(self):
        """0건이 '파일 없음'인지 '정규식이 못 읽음'인지 갈려야 한다(#54·#82)."""
        links, total = jp._file_links("<a href='/a.html'>x</a>" * 3,
                                      "https://example.test/")
        self.assertEqual(links, [])
        self.assertEqual(total, 3)

    def test_single_quoted_and_uppercase_extensions_are_seen(self):
        links, _ = jp._file_links("<A HREF='/d/DATA_J.XLS'>x</A>",
                                  "https://example.test/")
        self.assertEqual(links, ["https://example.test/d/DATA_J.XLS"])

    def test_sample_says_so_when_it_cannot_parse(self):
        """못 읽었으면 빈 줄이 아니라 사유다 — 침묵은 '내용이 없다'로 읽힌다(#54·#43)."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "junk.xls"
            f.write_bytes(b"not an excel file at all")
            lines = jp._sample_rows(f)
        self.assertTrue(lines and lines[0].startswith("❌"), lines)

    def test_a_multibyte_csv_cut_mid_character_still_reads(self):
        """바이트로 자르면 멀쩡한 CSV 가 '인코딩 판정 불가' 가 된다(리뷰 실측)."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.csv"
            body = ("コード,銘柄名\n1301,極洋\n" + "あ" * 4000).encode("cp932")
            f.write_bytes(body)
            lines = jp._sample_rows(f)
        self.assertFalse(lines[0].startswith("❌"), lines)
        self.assertIn("コード", lines[0])


class BannerTests(unittest.TestCase):
    def test_banner_carries_fingerprint_and_interpreter(self):
        import sys
        b = jp.banner()
        self.assertIn(sys.executable, b)
        self.assertIn("코드 지문", b)
        self.assertNotIn("지문불가", b)

    def test_banner_says_so_when_it_cannot_fingerprint(self):
        """'지문불가' 갈래에 발화 경로를 둔다 — 없으면 빈 지문이 신선한 체크아웃과
        낡은 것을 같은 글자로 만든다(#364b·#291)."""
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("boom")):
            self.assertIn("지문불가", jp.banner())


class _Resp:
    def __init__(self, body=b"", status=200, ctype="text/html"):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "replace")
        self.headers = {"content-type": ctype}


class EntrypointTests(unittest.TestCase):
    """`main` 을 실제로 태운다 — 헬퍼만 재면 배선을 떼는 변형이 통과한다(#20).

    ⚠️ **경계는 클래스 차원에서** 막는다. 개별 테스트에 맡기면 새 테스트가
    하나 추가될 때 조용히 바깥을 친다(#24 열거형 방어는 새 항목을 못 잡는다).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        _seed(self.dir)
        self.env = mock.patch.dict(os.environ, {"TRADE_DATA_DIR": str(self.dir)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http: list[str] = []
        # `module_report` 도 막는다 — 안 막으면 진입점 테스트 하나마다
        # 인터프리터 수만큼 자식이 뜬다(VM 은 4개라 게이트가 눈에 띄게
        # 느려진다, #116). 진짜 동작은 `ModuleReportTests` 가 따로 잰다.
        for name, repl in (("_fetch", self._no_http),
                           ("yf_names", self._no_yf),
                           ("module_report", self._fake_modules)):
            p = mock.patch.object(jp, name, repl)
            p.start()
            self.addCleanup(p.stop)

    def _no_http(self, url):                                   # noqa: D401
        self.http.append(url)
        raise OSError("테스트에서는 바깥으로 안 나간다")

    def _no_yf(self, py, tickers, **kw):
        return {t: {"error": "stubbed"} for t in tickers}

    def _fake_modules(self, py, mods):
        return {m: "stub" for m in mods}

    def _run(self) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = jp.main([])
        return rc, buf.getvalue()

    def test_the_network_boundary_is_stubbed_for_this_class(self):
        """이 클래스가 실제로 바깥을 막고 있나 — 반대 증거로 확인한다(#25)."""
        with self.assertRaises(OSError):
            jp._fetch("https://example.test/")
        self.assertEqual(jp.yf_names("py", ["6976"]),
                         {"6976": {"error": "stubbed"}})

    def test_offline_steps_report_the_board_tickers(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("6723", out)
        self.assertIn("Renesas Electronics Corporation", out)
        self.assertIn("③-b", out)

    def test_cross_board_check_says_whether_the_jp_board_knows_the_code(self):
        """외부 원천 없이 확인되는지 — 이 줄이 다음 라운드의 설계를 정한다."""
        _, out = self._run()
        self.assertRegex(out, r"\[cns\] 6976 .*JP 보드에 있음")
        self.assertRegex(out, r"\[mys\] 6723 .*JP 보드에 없음")

    def test_step_four_is_not_counted_as_measured_when_every_ticker_failed(self):
        """전부 실패했는데 '쟀다' 로 세면 rc 가 거짓 ✅ 가 된다(#54)."""
        _, out = self._run()
        four = out.split("④ yfinance", 1)[-1].split("⑤", 1)[0]
        self.assertIn("이름을 받은 종목 0/", four)

    def test_a_successful_step_four_adds_exactly_one_measured_step(self):
        """전부 실패해도 '쟀다' 로 세면 rc 가 거짓 ✅ 가 된다(#54) — 성공했을
        때와 실패했을 때의 단계 수가 **1 만큼** 달라야 그 계수가 산다."""
        def step_count(out: str) -> int:
            import re as _re
            m = _re.search(r"쟀다: (\d+)단계", out)
            return int(m.group(1)) if m else -1

        _, failed = self._run()
        with mock.patch.object(
                jp, "yf_names",
                lambda py, ts, **kw: {t: {"long": t + " Co"} for t in ts}):
            _, ok = self._run()
        self.assertEqual(step_count(ok), step_count(failed) + 1,
                         f"{step_count(failed)} → {step_count(ok)}")

    def test_the_cap_says_how_many_it_left_out(self):
        """자른 사실을 말하지 않으면 전수를 본 것처럼 읽힌다(#45)."""
        with mock.patch.object(jp, "_YF_CAP", 1):
            _, out = self._run()
        self.assertRegex(out, r"⚠️ \d+개 중 앞 1개만 조회한다")

    def test_step_six_samples_one_file_per_candidate_page(self):
        """JA 한 쪽만 보면 "영문 목록이 있나"에 영영 답이 안 난다(#143·#156)."""
        pages = {
            "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html":
                '<a href="/ja/data_j.xls">ja</a>',
            "https://www.jpx.co.jp/english/markets/statistics-equities/misc/01.html":
                '<a href="/en/data_e.xls">en</a>',
        }

        def fake(url):
            if url in pages:
                return _Resp(pages[url])
            if url.lower().endswith((".xls", ".csv")):
                return _Resp(b"junk-bytes", ctype="application/vnd.ms-excel")
            return _Resp("", 404)

        with mock.patch.object(jp, "_fetch", fake):
            _, out = self._run()
        # ⑤ 가 이미 전 링크를 찍으므로 전체 문자열로 재면 그쪽이 대신
        # 만족시킨다(#75) — ⑥ 절만 잘라서 본다(#55).
        six = out.split("⑥ 파일 표본", 1)[-1]
        self.assertIn("/ja/data_j.xls", six)
        self.assertIn("/en/data_e.xls", six)
        self.assertEqual(six.count("받음:"), 2, six)

    def test_step_six_blames_http_not_parsing_when_the_file_is_not_200(self):
        """403 HTML 을 '표본 파싱 불가' 라 적으면 원천을 고치러 간다(#82)."""
        def fake(url):
            if url.endswith("01.html"):
                return _Resp('<a href="/x/data_j.xls">x</a>')
            return _Resp(b"<html>denied</html>", 403)

        with mock.patch.object(jp, "_fetch", fake):
            _, out = self._run()
        six = out.split("⑥ 파일 표본", 1)[-1]
        self.assertIn("HTTP 403", six)
        self.assertNotIn("표본 파싱 불가", six)

    def test_step_five_flags_markup_it_could_not_read(self):
        """링크는 있는데 파일이 0건이면 정규식을 의심할 근거를 준다(#54)."""
        with mock.patch.object(
                jp, "_fetch",
                lambda url: _Resp("<a href='/a.html'>x</a>" * 4)):
            _, out = self._run()
        five = out.split("⑤ JPX", 1)[-1].split("⑥", 1)[0]
        self.assertIn("href=4", five)
        self.assertIn("정규식이 못 읽는 것", five)

    def test_the_probe_leaves_every_db_byte_and_mtime_untouched(self):
        """진단이 제 신호를 오염시키면 안 된다(#264·#283·#321).

        ⚠️ 계약은 **DB 내용**이다. SQLite 는 WAL DB 를 `mode=ro` 로 읽을 때도
        `-shm`/`-wal` 을 만들고 읽기 연결은 그걸 못 지운다(실측 2026-09-22) —
        그걸 '안 쓴다' 로 단언하면 테스트가 거짓을 못박는다(#286).
        """
        snap = lambda: {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
                        for p in sorted(self.dir.rglob("*"))}
        before = snap()
        self._run()
        after = snap()
        for name, val in before.items():
            self.assertEqual(val, after.get(name), f"{name} 이 바뀌었다")
        extra = sorted(set(after) - set(before))
        self.assertTrue(
            all(e.endswith(("-shm", "-wal"))
                and e.rsplit(".db", 1)[0] + ".db" in before for e in extra),
            f"DB 곁파일이 아닌 것이 생겼다: {extra}")

    def test_rc_is_one_when_nothing_could_be_measured(self):
        """아무것도 못 쟀으면 ✅ 가 아니다(#54). 도달 경로가 있어야 가드다(#291)."""
        with mock.patch.object(jp, "module_report",
                               return_value={"_error": "no interpreter"}), \
             mock.patch.object(jp, "board_jp_candidates", return_value=([], [])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = jp.main([])
        self.assertEqual(rc, 1, buf.getvalue())

    def test_each_candidate_interpreter_is_asked_exactly_once(self):
        """②④ 가 같은 인터프리터를 두 번 물으면 서브프로세스가 배로 뜬다(#113)."""
        calls: list[str] = []
        real = jp.module_report          # setUp 이 건 스텁 — 횟수만 센다

        def spy(py, mods):
            calls.append(py)
            return real(py, mods)

        with mock.patch.object(jp, "module_report", spy):
            with redirect_stdout(io.StringIO()):
                jp.main([])
        self.assertEqual(len(calls), len(set(calls)) + 1,
                         f"인터프리터를 중복으로 물었다: {calls}")


class ModuleReportTests(unittest.TestCase):
    """`module_report` 의 진짜 동작 — 진입점 테스트가 스텁으로 막으므로 여기서
    한 번 태운다(#20 스텁만 두면 배선을 못 잰다). 자식 하나, 네트워크 0."""

    def test_it_reports_a_version_for_present_and_a_mark_for_missing(self):
        import sys
        got = jp.module_report(sys.executable, ("json", "no_such_module_xyz"))
        self.assertNotIn("_error", got)
        self.assertFalse(str(got["json"]).startswith("❌"), got)
        self.assertTrue(str(got["no_such_module_xyz"]).startswith("❌"), got)

    def test_a_dead_interpreter_is_an_error_not_a_silent_empty(self):
        """빈 dict 를 돌려주면 ② 가 '모듈 없음' 으로 오보한다(#54·#82)."""
        got = jp.module_report("/nonexistent/bin/python", ("json",))
        self.assertIn("_error", got)


class YfChunkTests(unittest.TestCase):
    """④ 가 조각으로 나눠 묻고 진행을 찍나 — 몰아 치면 상한에서 전부 버린다(#103·#116)."""

    def test_results_survive_when_one_chunk_dies(self):
        seen: list[list[str]] = []

        class _R:
            def __init__(self, out="", rc=0):
                self.stdout, self.stderr, self.returncode = out, "", rc

        def fake_run(cmd, **kw):
            part = cmd[3:]
            seen.append(part)
            if "B" in part:
                return _R(rc=1)
            return _R(json.dumps({t: {"long": t + " Co"} for t in part}))

        import json
        with mock.patch.object(jp.subprocess, "run", fake_run):
            buf = io.StringIO()
            with redirect_stdout(buf):
                got = jp.yf_names("py", ["A", "B", "C", "D"], chunk=2)
        self.assertEqual(len(seen), 2, seen)
        self.assertEqual(got["C"]["long"], "C Co")
        self.assertIn("error", got["B"])
        self.assertIn("조회 중", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
