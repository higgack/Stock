"""JPX 상장 마스터(`trade.jpx_master` · `build_jpx_codes`) 회귀 — 2026-09-23.

원천은 `jp_master_probe` VM 실측으로 골랐다: JPX 영문 「その他統計資料」 페이지의
`data_e.xlsx`(`Local Code` · `Name (English)` · `Effective Date` 열). 여기서
재는 것:
  ① 표준 라이브러리 xlsx 파서 — **Excel 이 쓰는 모양**(공유 문자열 · 후리가나
     `rPh` · 리치 텍스트 런 · rels 가 가리키는 시트)과 openpyxl 모양(inlineStr)
     둘 다. 트레이드 venv 엔 스프레드시트 라이브러리가 없다(② 실측).
  ② 원천 사다리 — 페이지 링크 → 실측 주소 폴백, 그리고 **폴백했다고 말하나**.
  ③ 로더 — 없음·깨짐을 갈래로(#82), mtime 메모이즈.
  ④ 빌더 — 원자적 쓰기 · 하한(#280) · 실패 뒤 6시간 쉼(#303·#384) ·
     어떤 예외든 rc 0 과 경고(#116·#12) · 유닛 배선.
⚠️ 네트워크는 전부 주입한 `get` 으로 막는다 — 루트 conftest 소켓 그물이 2차
그물이다(#336·#401).
"""

import io
import json
import logging
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from trade import jpx_master as jm
from trade.scripts import build_jpx_codes as bj

_REPO = Path(__file__).resolve().parents[2]
_HDR = ["Effective Date", "Local Code", "Name (English)", "Section/Products"]


def _excel_like(rows, *, phonetic=None, rich=None, sheet="worksheets/data.xml",
                decoy=True) -> bytes:
    """**Excel 이 쓰는 모양**의 xlsx — 문자열은 공유 문자열 표(`t="s"`), 숫자는
    `<v>`, 시트는 rels 가 가리키는 **비기본 경로**, 그리고 그 옆에 다른 내용의
    `sheet1.xml`(= 기본 경로로 떨어지는 변형이 여기서 잡힌다, #91c).
    `phonetic` = {문자열: 읽기} — 그 공유 문자열에 `<rPh>` 를 단다(일본 Excel
    파일이 싣는 후리가나). `rich` = {문자열: [런...]} — 리치 텍스트로 쪼갠다.
    ⚠️ 이건 OOXML 명세를 따른 **재구성**이다 — 실물 첫 대조는 VM 빌드 로그와
    프로브 ⑦ 이 한다(#155·#334)."""
    phonetic, rich = phonetic or {}, rich or {}
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared: list[str] = []

    def sidx(text):
        if text not in shared:
            shared.append(text)
        return shared.index(text)

    def esc(t):
        return (str(t).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    xrows = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, v in enumerate(row):
            ref = f"{col(ci)}{ri}"
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>')
        xrows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet_xml = (f'<?xml version="1.0" encoding="UTF-8"?><worksheet {ns}>'
                 f'<sheetData>{"".join(xrows)}</sheetData></worksheet>')
    sis = []
    for t in shared:
        if t in rich:
            body = "".join(f"<r><rPr><b/></rPr><t>{esc(p)}</t></r>"
                           for p in rich[t])
        else:
            body = f'<t xml:space="preserve">{esc(t)}</t>'
        if t in phonetic:
            body += (f'<rPh sb="0" eb="1"><t>{esc(phonetic[t])}</t></rPh>'
                     '<phoneticPr fontId="1"/>')
        sis.append(f"<si>{body}</si>")
    sst = (f'<?xml version="1.0" encoding="UTF-8"?><sst {ns} count="{len(sis)}" '
           f'uniqueCount="{len(sis)}">{"".join(sis)}</sst>')
    wb = ('<?xml version="1.0" encoding="UTF-8"?><workbook ' + ns +
          ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
          'relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId7"/>'
          '</sheets></workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://'
            'schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId3" Type="x" Target="styles.xml"/>'
            f'<Relationship Id="rId7" Type="y" Target="{sheet}"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/" + sheet, sheet_xml)
        if decoy and sheet != "worksheets/sheet1.xml":
            z.writestr("xl/worksheets/sheet1.xml",
                       f'<worksheet {ns}><sheetData><row r="1"><c r="A1" '
                       f't="inlineStr"><is><t>decoy</t></is></c></row>'
                       f'</sheetData></worksheet>')
    return buf.getvalue()


def _listing(n=3, extra=()):
    rows = [_HDR,
            [20260831, 1301, "KYOKUYO CO.,LTD.", "Prime Market (Domestic)"],
            [20260831, 6976, "TAIYO YUDEN CO.,LTD.", "Prime Market (Domestic)"],
            [20260831, "285A", "Kioxia Holdings Corporation",
             "Prime Market (Domestic)"]][:n + 1]
    rows.extend(extra)
    return rows


class ParserTests(unittest.TestCase):
    def test_excel_shaped_file_parses_codes_names_and_date(self):
        m, st = jm.parse_listing(_excel_like(_listing()))
        self.assertEqual(m, {"1301": "KYOKUYO CO.,LTD.",
                             "6976": "TAIYO YUDEN CO.,LTD.",
                             "285A": "Kioxia Holdings Corporation"})
        self.assertEqual(st["effective"], "2026-08-31")
        self.assertEqual((st["rows"], st["rejected"], st["dupes"]), (3, 0, 0))

    def test_the_sheet_is_the_one_the_workbook_points_at(self):
        """rels 를 따라간다 — 기본 경로(`sheet1.xml`)의 미끼를 읽으면 헤더를 못
        찾는다(#91c 픽스처가 그 변형을 실제로 깨뜨려야 한다)."""
        m, _ = jm.parse_listing(_excel_like(_listing(), decoy=True))
        self.assertIn("6976", m)

    def test_furigana_runs_do_not_leak_into_the_name(self):
        """일본 Excel 은 공유 문자열에 읽기(`<rPh>`)를 싣는다 — 전부 이으면 이름
        뒤에 읽기가 붙어 대조가 조용히 실패한다."""
        data = _excel_like(_listing(), phonetic={"TAIYO YUDEN CO.,LTD.": "タイヨウ"})
        m, _ = jm.parse_listing(data)
        self.assertEqual(m["6976"], "TAIYO YUDEN CO.,LTD.")

    def test_effective_date_is_the_latest_row_not_the_first(self):
        """행마다 기준일이 다를 수 있다(영문 페이지의 변경분 파일은 2022-04-28
        행을 싣는다, ⑥ 실측) — 목록의 기준일은 **가장 늦은** 날이다. 전 행이
        같은 날인 픽스처로는 이 축이 안 잡힌다(M8 생존 실측, #91c)."""
        extra = [[20260915, 7203, "LATER CO.,LTD.", "x"]]
        rows = _listing(extra=extra)
        rows[1][0] = 20220428
        _, st = jm.parse_listing(_excel_like(rows))
        self.assertEqual(st["effective"], "2026-09-15")

    def test_rich_text_runs_are_joined(self):
        data = _excel_like(_listing(),
                           rich={"KYOKUYO CO.,LTD.": ["KYOKUYO ", "CO.,LTD."]})
        self.assertEqual(jm.parse_listing(data)[0]["1301"], "KYOKUYO CO.,LTD.")

    def test_inline_strings_as_openpyxl_writes_them(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl 없음 — Excel 모양 픽스처가 주 경로를 잰다")
        wb = openpyxl.Workbook()
        for r in _listing():
            wb.active.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        m, _ = jm.parse_listing(buf.getvalue())
        self.assertEqual(m["285A"], "Kioxia Holdings Corporation")

    def test_header_is_found_below_a_title_row(self):
        rows = [["Listed Issues (as of 2026-08-31)"]] + _listing()
        self.assertIn("6976", jm.parse_listing(_excel_like(rows))[0])

    def test_missing_header_is_an_error_that_quotes_the_first_rows(self):
        rows = [["Code", "Name"], [1301, "KYOKUYO"]]
        with self.assertRaises(jm.FetchError) as cm:
            jm.parse_listing(_excel_like(rows))
        self.assertIn("Local Code", str(cm.exception))
        self.assertIn("Code", str(cm.exception).split("첫 행", 1)[-1])

    def test_broken_sheet_xml_is_a_named_fetch_error(self):
        """깨진 XML 이 원문 파서 예외로 새면 빌더 사유가 갈래를 잃는다(#82)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData><row>")
        with self.assertRaises(jm.FetchError) as cm:
            jm.parse_listing(buf.getvalue())
        self.assertIn("sheet1.xml XML 파싱 실패", str(cm.exception))

    def test_inline_strings_written_as_runs_are_read(self):
        """인라인 문자열도 런으로 올 수 있다 — 공유 문자열만 런을 읽으면 셀
        모양에 따라 이름이 빈다(같은 함수를 쓴다, #38). 후리가나도 뺀다."""
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'

        def cell(ref, text=None, runs=None, num=None):
            if num is not None:
                return f'<c r="{ref}"><v>{num}</v></c>'
            body = (f"<t>{text}</t>" if runs is None else
                    "".join(f"<r><t>{r}</t></r>" for r in runs))
            return (f'<c r="{ref}" t="inlineStr"><is>{body}'
                    f'<rPh sb="0" eb="1"><t>ヨミ</t></rPh></is></c>')
        rows = ("<row r=\"1\">" + cell("A1", "Local Code")
                + cell("B1", "Name (English)") + "</row>"
                + "<row r=\"2\">" + cell("A2", num=6976)
                + cell("B2", runs=["TAIYO ", "YUDEN CO.,LTD."]) + "</row>")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml",
                       f"<worksheet {ns}><sheetData>{rows}</sheetData></worksheet>")
        self.assertEqual(jm.parse_listing(buf.getvalue())[0],
                         {"6976": "TAIYO YUDEN CO.,LTD."})

    def test_not_a_zip_says_so(self):
        with self.assertRaises(jm.FetchError) as cm:
            jm.parse_listing(b"\xd0\xcf\x11\xe0legacy-xls")
        self.assertIn("xlsx(zip)가 아니다", str(cm.exception))

    def test_codes_are_normalized_and_bad_rows_are_counted_not_kept(self):
        extra = [[20260831, "1305.0", "iFreeETF TOPIX", "ETFs/ ETNs"],
                 [20260831, "285a", "dup lower", "x"],
                 [20260831, 13050, "five digits", "x"],
                 [20260831, 7203, "", "x"],
                 [20260831, 1301, "second KYOKUYO", "x"]]
        m, st = jm.parse_listing(_excel_like(_listing(extra=extra)))
        self.assertEqual(m["1305"], "iFreeETF TOPIX")
        self.assertEqual(m["285A"], "Kioxia Holdings Corporation",
                         "소문자 코드는 대문자로 접히고, 먼저 온 행이 이긴다")
        self.assertEqual(m["1301"], "KYOKUYO CO.,LTD.")
        self.assertNotIn("13050", m)
        self.assertNotIn("7203", m, "이름이 빈 행은 버린다")
        self.assertEqual(st["rejected"], 2)
        self.assertEqual(st["dupes"], 2)
        self.assertIn(("13050", "five digits"), st["rejected_sample"])


def _page(href):
    return f'<html><a href="{href}">Listed</a></html>'.encode()


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.seen: list[str] = []
        self.file = _excel_like(_listing())

    def _get(self, responses):
        def get(url):
            self.seen.append(url)
            r = responses[url] if url in responses else responses.get("*")
            if isinstance(r, Exception):
                raise r
            return r
        return get

    def test_file_url_comes_from_the_list_page(self):
        rel = "/english/markets/statistics-equities/misc/NEWDIR-att/data_e.xlsx"
        want = "https://www.jpx.co.jp" + rel
        m, st = jm.fetch(get=self._get({jm.LIST_PAGE: (200, _page(rel)),
                                        want: (200, self.file)}))
        self.assertEqual(self.seen, [jm.LIST_PAGE, want])
        self.assertEqual(st["via"], "목록 페이지 링크")
        self.assertEqual(st["url"], want)
        self.assertIn("6976", m)

    def test_the_updates_file_is_not_mistaken_for_the_full_list(self):
        """영문 페이지엔 `jyoujyou(updated)_e.xlsx`(변경분)도 걸린다(⑤ 실측) —
        그걸 집으면 전 목록이 아니라 수십 행이다(#45)."""
        html = (b'<a href="a/jyoujyou(updated)_e.xlsx">u</a>'
                b'<a href="a/data_e.xlsx">d</a>')
        self.assertTrue(jm.find_file_url(html.decode()).endswith("/a/data_e.xlsx"))

    def test_missing_link_falls_back_to_the_measured_url_and_says_so(self):
        m, st = jm.fetch(get=self._get({jm.LIST_PAGE: (200, b"<html></html>"),
                                        jm.KNOWN_FILE: (200, self.file)}))
        self.assertEqual(self.seen[-1], jm.KNOWN_FILE)
        self.assertIn("링크 없음", st["via"])
        self.assertIn("실측 주소", st["via"])

    def test_page_error_falls_back_and_names_the_status(self):
        _, st = jm.fetch(get=self._get({jm.LIST_PAGE: (403, b""),
                                        jm.KNOWN_FILE: (200, self.file)}))
        self.assertIn("HTTP 403", st["via"])
        _, st = jm.fetch(get=self._get({jm.LIST_PAGE: OSError("dns"),
                                        jm.KNOWN_FILE: (200, self.file)}))
        self.assertIn("요청 실패(OSError)", st["via"])

    def test_file_failure_is_an_error_carrying_the_route(self):
        with self.assertRaises(jm.FetchError) as cm:
            jm.fetch(get=self._get({jm.LIST_PAGE: (500, b""),
                                    jm.KNOWN_FILE: (404, b"")}))
        msg = str(cm.exception)
        self.assertIn("HTTP 404", msg)
        self.assertIn("목록 페이지 HTTP 500", msg, "어떤 길로 왔는지도 말한다(#82)")


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(jm, "PATH", Path(self.tmp.name) / "jpx_codes.json")
        p.start()
        self.addCleanup(p.stop)

    def _write(self, obj, *, mtime=None):
        jm.PATH.write_text(json.dumps(obj), encoding="utf-8")
        if mtime is not None:
            os.utime(jm.PATH, (mtime, mtime))

    def test_missing_file_is_empty_with_a_reason(self):
        self.assertEqual(jm.load_envelope(), ({}, {"error": "없음"}))
        self.assertEqual(jm.load(), {})

    def test_envelope_codes_and_meta(self):
        self._write({"codes": {"6976": "TAIYO YUDEN CO.,LTD.", "bad!": "x",
                               "1301": ""},
                     "effective": "2026-08-31", "via": "목록 페이지 링크"},
                    mtime=1000)
        codes, meta = jm.load_envelope()
        self.assertEqual(codes, {"6976": "TAIYO YUDEN CO.,LTD."})
        self.assertEqual(meta["effective"], "2026-08-31")

    def test_a_rewrite_is_seen_through_the_memo(self):
        self._write({"codes": {"6976": "OLD"}}, mtime=1000)
        self.assertEqual(jm.load()["6976"], "OLD")
        self._write({"codes": {"6976": "NEW"}}, mtime=2000)
        self.assertEqual(jm.load()["6976"], "NEW")

    def test_corrupt_and_wrong_shape_are_named_not_swallowed(self):
        jm.PATH.write_bytes(b"\xff{not json")
        os.utime(jm.PATH, (3000, 3000))
        with self.assertLogs(jm.log, level="WARNING"):
            codes, meta = jm.load_envelope()
        self.assertEqual(codes, {})
        self.assertIn("못 읽음", meta["error"])
        self._write({"6976": "flat dict (KRX 모양)"}, mtime=4000)
        self.assertIn("codes 없음", jm.load_envelope()[1]["error"])


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(jm, "PATH", Path(self.tmp.name) / "jpx_codes.json")
        p.start()
        self.addCleanup(p.stop)
        self.calls = 0
        many = [[20260831, 1000 + i, f"CO{i} CO.,LTD.", "x"]
                for i in range(bj.MIN_CODES)]
        self.big = _excel_like(_listing(extra=many))

    def _get(self, body=None, exc=None, page=b"<html></html>"):
        def get(url):
            self.calls += 1
            if exc:
                raise exc
            return (200, page) if url == jm.LIST_PAGE else (200, body)
        return get

    def test_builds_an_envelope_atomically_and_logs_the_count(self):
        with self.assertLogs("build-jpx-codes", level="INFO") as cm:
            rc = bj.run(get=self._get(self.big), now=5000.0)
        self.assertEqual(rc, 0)
        env = json.loads(jm.PATH.read_text(encoding="utf-8"))
        self.assertEqual(env["codes"]["6976"], "TAIYO YUDEN CO.,LTD.")
        self.assertEqual(env["effective"], "2026-08-31")
        self.assertIn("실측 주소", env["via"])
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()],
                         ["jpx_codes.json"], "임시파일·실패 곁파일이 남으면 안 된다")
        self.assertTrue(any("JPX master built:" in m for m in cm.output))

    def test_a_failure_keeps_the_existing_master_and_records_why(self):
        jm.PATH.write_text('{"codes": {"6976": "KEEP"}}', encoding="utf-8")
        before = jm.PATH.read_bytes()
        with self.assertLogs("build-jpx-codes", level="WARNING") as cm:
            rc = bj.run(get=self._get(exc=OSError("proxy 403")), now=5000.0)
        self.assertEqual(rc, 0)
        self.assertEqual(jm.PATH.read_bytes(), before)
        rec = json.loads(jm.fail_path().read_text(encoding="utf-8"))
        self.assertIn("proxy 403", rec["reason"])
        self.assertTrue(any("keep existing" in m for m in cm.output))

    def test_below_the_floor_is_not_written(self):
        small = _excel_like(_listing())
        with self.assertLogs("build-jpx-codes", level="WARNING"):
            bj.run(get=self._get(small), now=5000.0)
        self.assertFalse(jm.PATH.exists())
        self.assertIn("하한", json.loads(jm.fail_path().read_text())["reason"])

    def test_if_stale_skips_a_fresh_master_without_calling_out(self):
        # '신선' = 최근 **이고** 로더가 쓸 수 있다 — 빈 목록은 신선하지 않다
        # (아래 `test_a_fresh_but_unusable_master_is_rebuilt`).
        jm.PATH.write_text('{"codes": {"6976": "TAIYO YUDEN CO.,LTD."}}',
                           encoding="utf-8")
        bj.run(if_stale=True, get=self._get(self.big), now=time.time())
        self.assertEqual(self.calls, 0)

    def test_a_fresh_but_unusable_master_is_rebuilt(self):
        """mtime 만 보면 깨진 파일·옛 형식이 '신선' 이라 최대 7일 평문이다
        (독립 리뷰 2026-09-23) — 로더가 못 쓰면 다시 만든다(#25)."""
        jm.PATH.write_text('{"6976": "flat — codes 없음"}', encoding="utf-8")
        bj.run(if_stale=True, get=self._get(self.big), now=time.time())
        self.assertGreater(self.calls, 0)
        self.assertIn("6976", jm.load())

    def test_after_a_failure_if_stale_rests_then_retries(self):
        t0 = 10_000.0
        with self.assertLogs("build-jpx-codes", level="WARNING"):
            bj.run(if_stale=True, get=self._get(exc=OSError("x")), now=t0)
        n = self.calls
        bj.run(if_stale=True, get=self._get(self.big),
               now=t0 + bj.FAIL_COOLDOWN_S - 60)
        self.assertEqual(self.calls, n, "쉬는 동안엔 원천을 안 두드린다(#303)")
        bj.run(if_stale=True, get=self._get(self.big),
               now=t0 + bj.FAIL_COOLDOWN_S + 60)
        self.assertGreater(self.calls, n, "쉬는 시간이 지나면 다시 묻는다(#178)")
        self.assertTrue(jm.PATH.exists())
        self.assertFalse(jm.fail_path().exists(), "성공하면 실패 기록을 지운다")

    def test_a_manual_run_ignores_the_rest(self):
        with self.assertLogs("build-jpx-codes", level="WARNING"):
            bj.run(get=self._get(exc=OSError("x")), now=10_000.0)
        n = self.calls
        bj.run(get=self._get(self.big), now=10_060.0)
        self.assertGreater(self.calls, n)

    def test_main_never_fails_the_unit(self):
        """장식용 단계가 같은 유닛의 적재·렌더를 멈추면 안 된다(#116) —
        대신 경고로 사유를 남긴다(#12)."""
        with mock.patch.object(bj, "run", side_effect=RuntimeError("boom")):
            with self.assertLogs("build-jpx-codes", level="WARNING") as cm:
                self.assertEqual(bj.main(["--if-stale"]), 0)
        self.assertTrue(any("boom" in m for m in cm.output))


class HttpBudgetTests(unittest.TestCase):
    """`_get` — 소켓 타임아웃은 **끊긴 시간**만 잰다. 몸통이 조금씩 흘러오면
    총 상한이 끊어야 한다(독립 리뷰 2026-09-23)."""

    class _Slow:
        status = 200

        def __init__(self, clock, step, chunk=b"x" * 10, n=100):
            self.clock, self.step, self.chunk, self.left = clock, step, chunk, n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n):
            self.clock[0] += self.step
            if self.left <= 0:
                return b""
            self.left -= 1
            return self.chunk

    def _run(self, **kw):
        clock = [0.0]
        with mock.patch.object(jm.time, "monotonic", lambda: clock[0]):
            return jm._get("https://x.test/f", opener=lambda req, timeout:
                           self._Slow(clock, **kw), deadline=30)

    def test_a_trickling_body_is_cut_at_the_total_deadline(self):
        with self.assertRaises(jm.FetchError) as cm:
            self._run(step=1.0)
        self.assertIn("총 30초 상한 초과", str(cm.exception))

    def test_a_fast_body_is_returned_whole(self):
        st, body = self._run(step=0.01, n=5)
        self.assertEqual((st, body), (200, b"x" * 50))

    def test_size_cap(self):
        with mock.patch.object(jm, "_MAX_BYTES", 25):
            with self.assertRaises(jm.FetchError) as cm:
                jm._get("https://x.test/f", opener=lambda req, timeout:
                        self._Slow([0.0], 0.0, n=5), max_bytes=25)
        self.assertIn("25바이트 상한", str(cm.exception))

    def test_http_error_is_a_status_not_an_exception(self):
        import urllib.error

        def boom(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "no", {}, None)
        self.assertEqual(jm._get("https://x.test/f", opener=boom), (403, b""))


class DeployWiringTests(unittest.TestCase):
    def _execs(self):
        txt = (_REPO / "deploy/trade-bot-dashboard-refresh.service").read_text()
        return [ln.split("=", 1)[1] for ln in txt.splitlines()
                if ln.startswith("ExecStart=")]

    def test_the_builder_runs_last_in_the_refresh_unit(self):
        """**맨 끝** — 느린 원천이 적재·렌더의 시간 예산(TimeoutStartSec 공유)을
        먹지 않게(독립 리뷰 2026-09-23 · #116). 새 마스터는 다음 틱의 렌더가 쓴다.
        `-` 접두 = 실패가 유닛을 실패로 만들지 않는다."""
        ex = self._execs()
        i = next(i for i, e in enumerate(ex) if "build_jpx_codes" in e)
        self.assertEqual(i, len(ex) - 1, ex)
        self.assertIn("--if-stale", ex[i])
        self.assertTrue(ex[i].startswith("-"))
        self.assertIn("/stock-trade/.venv/bin/python", ex[i],
                      "유닛이 도는 그 트레이드 venv")

    def test_only_the_standard_library_and_trade(self):
        """트레이드 venv 엔 pandas·openpyxl·yfinance 가 없다(② 실측) — 새
        의존성은 그 venv 를 따로 고쳐야 하고, 안 고치면 조용히 죽는다
        (#151·#42a). import 를 **전수로** 잰다(이름 열거 금지, #24)."""
        import ast
        import sys
        for f in ("trade/jpx_master.py", "trade/scripts/build_jpx_codes.py"):
            tree = ast.parse((_REPO / f).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                else:
                    continue
                for m in mods:
                    top = m.split(".")[0]
                    self.assertTrue(top in sys.stdlib_module_names
                                    or top in ("trade", "__future__"),
                                    f"{f}: {m}")


if __name__ == "__main__":
    unittest.main()
