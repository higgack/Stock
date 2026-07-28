"""trade.hs_map — operator-curated 품목명 → HS코드 bridge tests.

The mapping is the gate for every customs-API enrichment: a wrong or
missing pin means an item gets no official numbers (safe) — but a pin
that silently fails to round-trip would attach numbers to the wrong
item (unsafe). These tests pin the contract:

  - add / get / remove round-trip with whitespace + case normalisation
  - re-pin updates in place (one line per item, no dup)
  - invalid HS codes are rejected, not stored
  - malformed / commented lines are skipped without aborting the scan
"""

import tempfile
import unittest
from pathlib import Path

from trade import hs_map


class TestHsMap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hs_map.tsv"

    def tearDown(self):
        self.tmp.cleanup()

    # --- validation ---

    def test_valid_hs_lengths(self):
        # 2–10 digits all valid. The 관세청 file has 7/8/9-digit leaves
        # too (~1,142 of them), so odd lengths must pass — the old
        # {2,4,6,10} whitelist wrongly rejected them at button-click time.
        for good in (
            "09", "0902", "190230", "1902300000",  # even granularities
            "0207603", "01029090", "020760320",     # 7 / 8 / 9-digit leaves
        ):
            self.assertTrue(hs_map.is_valid_hs(good), good)
        for bad in ("", "abc", "1902.30", "0", "1", "LENOVO", "12345678901"):
            # empty, non-digit, dotted, 1-digit (too short), 11-digit (too long)
            self.assertFalse(hs_map.is_valid_hs(bad), bad)

    # --- add / get round-trip ---

    def test_add_then_get(self):
        self.assertTrue(hs_map.add("라면", "190230", self.path))
        self.assertEqual(hs_map.get("라면", self.path), "190230")

    def test_get_missing_returns_none(self):
        self.assertIsNone(hs_map.get("없는품목", self.path))

    def test_lookup_normalises_whitespace_and_case(self):
        hs_map.add("LiFePO4 양극재", "850760", self.path)
        # extra spaces + different case on the ASCII part still match
        self.assertEqual(
            hs_map.get("  lifepo4   양극재 ", self.path), "850760"
        )

    def test_leading_zero_hs_preserved(self):
        hs_map.add("녹차", "0902", self.path)
        self.assertEqual(hs_map.get("녹차", self.path), "0902")

    # --- idempotency / re-pin ---

    def test_add_same_mapping_is_noop(self):
        self.assertTrue(hs_map.add("라면", "190230", self.path))
        self.assertFalse(hs_map.add("라면", "190230", self.path))
        self.assertEqual(len(hs_map.entries(self.path)), 1)

    def test_repin_updates_in_place(self):
        hs_map.add("라면", "190230", self.path)
        self.assertTrue(hs_map.add("라면", "1902300000", self.path))  # changed
        self.assertEqual(hs_map.get("라면", self.path), "1902300000")
        # still exactly one line, not a duplicate
        self.assertEqual(len(hs_map.entries(self.path)), 1)

    # --- remove ---

    def test_remove_existing(self):
        hs_map.add("라면", "190230", self.path)
        self.assertTrue(hs_map.remove("라면", self.path))
        self.assertIsNone(hs_map.get("라면", self.path))

    def test_remove_absent_is_false(self):
        self.assertFalse(hs_map.remove("없음", self.path))

    def test_remove_normalises(self):
        hs_map.add("음극재 (천연흑연)", "850720", self.path)
        self.assertTrue(hs_map.remove("음극재 (천연흑연)", self.path))

    # --- validation on add ---

    def test_add_invalid_code_raises(self):
        with self.assertRaises(ValueError):
            hs_map.add("라면", "abc", self.path)
        with self.assertRaises(ValueError):
            hs_map.add("라면", "1902.30", self.path)
        # nothing was written
        self.assertEqual(hs_map.entries(self.path), [])

    def test_add_empty_item_raises(self):
        with self.assertRaises(ValueError):
            hs_map.add("", "190230", self.path)

    # --- file fault tolerance ---

    def test_malformed_and_comment_lines_skipped(self):
        self.path.write_text(
            "# 주석\n"
            "\n"
            "라면\t190230\n"
            "no-tab-line\n"
            "엉뚱\tabc\n"            # invalid code → skipped
            "양극재\t850760\n",
            encoding="utf-8",
        )
        loaded = hs_map.load(self.path)
        self.assertEqual(loaded, {"라면": "190230", "양극재": "850760"})

    def test_entries_preserves_original_spelling(self):
        hs_map.add("LiFePO4 양극재", "850760", self.path)
        self.assertEqual(hs_map.entries(self.path), [("LiFePO4 양극재", "850760")])

    def test_last_duplicate_wins_on_load(self):
        self.path.write_text(
            "라면\t190230\n라면\t1902300000\n", encoding="utf-8"
        )
        self.assertEqual(hs_map.get("라면", self.path), "1902300000")


if __name__ == "__main__":
    unittest.main()
