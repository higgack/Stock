"""trade.hs_lookup — 관세청 HS reference loader + search tests.

Covers both file formats (the real download is .xlsx; .csv export also
accepted), the expired-code filter (적용종료일자), the mtime cache, and
search semantics. Fixtures mirror the real 15049722 column set
(HS부호, 적용종료일자, 한글품목명, 영문품목명, …).
"""

import os
import tempfile
import time
import unittest
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from trade import hs_lookup


_HEADERS = ["HS부호", "적용종료일자", "한글품목명", "영문품목명"]
# Far-future end date so rows are 'active' under any test 'today'.
_FUTURE = str((date(2099, 1, 1) - date(1899, 12, 30)).days)
_ROWS = [
    ["1902301010", _FUTURE, "라면", "Instant noodles"],
    ["1902301090", _FUTURE, "기타", "Other"],
    ["8542310000", _FUTURE, "프로세서ㆍ제어기", "Processors and controllers"],
    ["8542320000", _FUTURE, "메모리(DRAM 등)", "Memories"],
    ["8542390000", _FUTURE, "기타 집적회로", "Other integrated circuits"],
    ["8541100000", _FUTURE, "다이오드", "Diodes"],
    ["9999999999", _FUTURE, "", "Empty"],          # blank name → skip
    ["BAD-CODE", _FUTURE, "잘못된", "Bad"],          # non-digit → skip
]


def _write_csv(path: Path, encoding="utf-8-sig"):
    lines = [",".join(_HEADERS)] + [",".join(r) for r in _ROWS]
    path.write_text("\n".join(lines) + "\n", encoding=encoding)


def _colref(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _make_xlsx(path: Path, header, rows):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    def cell(ref, text):
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    def rowxml(rnum, values):
        cells = "".join(cell(f"{_colref(j)}{rnum}", v) for j, v in enumerate(values))
        return f'<row r="{rnum}">{cells}</row>'

    body = rowxml(1, header) + "".join(rowxml(i + 2, r) for i, r in enumerate(rows))
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{ns}"><sheetData>{body}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)


class TestLoadCsv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hs_codes.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_raises(self):
        with self.assertRaises(hs_lookup.HsCodeFileMissing):
            hs_lookup.load(self.path)

    def test_loads_valid_skips_invalid(self):
        _write_csv(self.path)
        rows = hs_lookup.load(self.path)
        codes = {r.hs_code for r in rows}
        self.assertEqual(len(rows), 6)
        self.assertIn("1902301010", codes)
        self.assertNotIn("9999999999", codes)
        self.assertNotIn("BAD-CODE", codes)

    def test_cp949_fallback(self):
        _write_csv(self.path, encoding="cp949")
        rows = hs_lookup.load(self.path)
        self.assertTrue(any(r.ko_name == "라면" for r in rows))

    def test_korean_name_intact(self):
        _write_csv(self.path)
        mem = next(r for r in hs_lookup.load(self.path) if r.hs_code == "8542320000")
        self.assertEqual(mem.ko_name, "메모리(DRAM 등)")


class TestLoadXlsx(unittest.TestCase):
    """The real download is .xlsx — read it directly (stdlib, no openpyxl)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hs_codes.xlsx"
        _make_xlsx(self.path, _HEADERS, _ROWS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_xlsx_rows(self):
        rows = hs_lookup.load(self.path)
        codes = {r.hs_code for r in rows}
        self.assertEqual(len(rows), 6)
        self.assertIn("8542320000", codes)
        self.assertNotIn("BAD-CODE", codes)

    def test_xlsx_search(self):
        hits, total = hs_lookup.search("메모리", path=self.path)
        self.assertEqual(total, 1)
        self.assertEqual(hits[0].hs_code, "8542320000")
        self.assertEqual(hits[0].ko_name, "메모리(DRAM 등)")


class TestExpiryFilter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hs_codes.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_expired_code_dropped(self):
        past = str((date(2020, 1, 1) - date(1899, 12, 30)).days)
        future = str((date(2099, 1, 1) - date(1899, 12, 30)).days)
        self.path.write_text(
            "HS부호,적용종료일자,한글품목명,영문품목명\n"
            f"1111111111,{past},옛날코드,Old\n"
            f"2222222222,{future},현행코드,Current\n",
            encoding="utf-8",
        )
        rows = hs_lookup.load(self.path, today=date(2026, 6, 1))
        codes = {r.hs_code for r in rows}
        self.assertIn("2222222222", codes)
        self.assertNotIn("1111111111", codes)   # expired → dropped

    def test_blank_enddate_kept(self):
        self.path.write_text(
            "HS부호,적용종료일자,한글품목명,영문품목명\n"
            "3333333333,,날짜없음,NoDate\n",
            encoding="utf-8",
        )
        rows = hs_lookup.load(self.path, today=date(2026, 6, 1))
        self.assertEqual({r.hs_code for r in rows}, {"3333333333"})


class TestCacheReload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hs_codes.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_replaced_file_picked_up_via_mtime(self):
        self.path.write_text(
            "HS부호,한글품목명\n1111111111,첫번째\n", encoding="utf-8"
        )
        first = hs_lookup.load(self.path)
        self.assertEqual({r.hs_code for r in first}, {"1111111111"})
        # Rewrite with new content + a strictly newer mtime so the cache
        # invalidates (mirrors the operator overwriting the file on a new
        # annual release).
        self.path.write_text(
            "HS부호,한글품목명\n2222222222,두번째\n", encoding="utf-8"
        )
        future = time.time() + 10
        os.utime(self.path, (future, future))
        second = hs_lookup.load(self.path)
        self.assertEqual({r.hs_code for r in second}, {"2222222222"})


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hs_codes.csv"
        _write_csv(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_numeric_prefix(self):
        hits, total = hs_lookup.search("8542", path=self.path)
        self.assertEqual([r.hs_code for r in hits], [
            "8542310000", "8542320000", "8542390000",
        ])
        self.assertEqual(total, 3)

    def test_korean_substring(self):
        hits, total = hs_lookup.search("메모리", path=self.path)
        self.assertEqual(total, 1)
        self.assertEqual(hits[0].hs_code, "8542320000")

    def test_korean_multiple(self):
        hits, _ = hs_lookup.search("기타", path=self.path)
        codes = {r.hs_code for r in hits}
        self.assertIn("1902301090", codes)
        self.assertIn("8542390000", codes)

    def test_english_fallback(self):
        hits, _ = hs_lookup.search("DRAM", path=self.path)
        self.assertTrue(any(r.hs_code == "8542320000" for r in hits))

    def test_case_insensitive(self):
        up, _ = hs_lookup.search("DIODE", path=self.path)
        lo, _ = hs_lookup.search("diode", path=self.path)
        self.assertEqual({r.hs_code for r in up}, {r.hs_code for r in lo})
        self.assertIn("8541100000", {r.hs_code for r in up})

    def test_empty_query(self):
        self.assertEqual(hs_lookup.search("", path=self.path), ([], 0))

    def test_no_match(self):
        self.assertEqual(hs_lookup.search("없는키워드ZZZ", path=self.path), ([], 0))

    def test_cap_preserves_total(self):
        hits, total = hs_lookup.search("기타", path=self.path, limit=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(total, 2)

    def test_sorted_by_code(self):
        hits, _ = hs_lookup.search("기타", path=self.path)
        codes = [r.hs_code for r in hits]
        self.assertEqual(codes, sorted(codes))

    def test_missing_file_propagates(self):
        with self.assertRaises(hs_lookup.HsCodeFileMissing):
            hs_lookup.search("뭐든", path=Path(self.tmp.name) / "nope.csv")


if __name__ == "__main__":
    unittest.main()
