"""trade.ignored — operator-curated msg_id skip list.

Tests cover idempotent add/remove, malformed-line tolerance,
comment / blank-line handling, and round-trip persistence."""

import tempfile
import unittest
from pathlib import Path

from trade import ignored


class TestIgnored(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "ignored.txt"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_empty_returns_empty_set(self):
        self.assertEqual(ignored.load(self.path), set())

    def test_add_returns_true_first_time_false_on_dup(self):
        self.assertTrue(ignored.add(677, self.path))
        self.assertFalse(ignored.add(677, self.path))
        self.assertEqual(ignored.load(self.path), {677})

    def test_add_appends_distinct_ids(self):
        for mid in (100, 200, 300):
            ignored.add(mid, self.path)
        self.assertEqual(ignored.load(self.path), {100, 200, 300})

    def test_remove_returns_true_when_present(self):
        ignored.add(677, self.path)
        self.assertTrue(ignored.remove(677, self.path))
        self.assertFalse(ignored.remove(677, self.path))
        self.assertEqual(ignored.load(self.path), set())

    def test_remove_preserves_other_ids(self):
        for mid in (100, 200, 300):
            ignored.add(mid, self.path)
        ignored.remove(200, self.path)
        self.assertEqual(ignored.load(self.path), {100, 300})

    def test_load_skips_comments_and_blanks(self):
        self.path.write_text(
            "# operator note\n"
            "100\n"
            "\n"
            "200\n"
            "# another comment\n"
            "300\n",
            encoding="utf-8",
        )
        self.assertEqual(ignored.load(self.path), {100, 200, 300})

    def test_load_skips_malformed_lines(self):
        self.path.write_text(
            "100\nabc\n200\nx 300\n",
            encoding="utf-8",
        )
        # 'abc' and 'x 300' don't parse; the integer lines survive.
        self.assertEqual(ignored.load(self.path), {100, 200})

    def test_remove_writes_sorted(self):
        for mid in (300, 100, 200):
            ignored.add(mid, self.path)
        ignored.remove(100, self.path)
        body = self.path.read_text(encoding="utf-8")
        # Remaining 200, 300 written in sorted order.
        self.assertEqual(body, "200\n300\n")


class TestPrefixMatching(unittest.TestCase):
    def test_beon_insight_prefix_matches(self):
        self.assertTrue(
            ignored.matches_prefix(
                "[비온 인사이트] 파두 추가 수주! 올해 최소 매출 1,900억 확보"
            )
        )
        self.assertTrue(
            ignored.matches_prefix(
                "[비온 인사이트] 파두 수주잔고 2,624억 원 기록"
            )
        )

    def test_legit_export_alert_does_not_match(self):
        # Real export/import alerts have a totally different shape —
        # never start with a bracketed promo tag.
        self.assertFalse(
            ignored.matches_prefix(
                "4월 수출입 동향 — 반도체 +35%, 자동차 +12%"
            )
        )
        self.assertFalse(
            ignored.matches_prefix("📊 5월 1-20일 잠정")
        )

    def test_leading_whitespace_does_not_bypass(self):
        # Stray newline / space before the tag must still match.
        self.assertTrue(ignored.matches_prefix("   [비온 인사이트] 종목분석"))
        self.assertTrue(ignored.matches_prefix("\n[비온 인사이트] 종목분석"))

    def test_empty_and_none_return_false(self):
        self.assertFalse(ignored.matches_prefix(""))
        self.assertFalse(ignored.matches_prefix(None))

    def test_prefix_in_middle_does_not_match(self):
        # Only a true prefix counts — body mentions don't.
        self.assertFalse(
            ignored.matches_prefix(
                "오늘의 수출입 동향 — 참고: [비온 인사이트] 글도 함께 발행"
            )
        )

    def test_prefixes_constant_is_tuple(self):
        # Guard against accidental mutation to a list.
        self.assertIsInstance(ignored.IGNORED_PREFIXES, tuple)
        self.assertIn("[비온 인사이트]", ignored.IGNORED_PREFIXES)


class TestContainsMatching(unittest.TestCase):
    """Body-substring filter for series where the line-1 ticker
    varies per post but a stable marker lives elsewhere in the
    body (DART 공시 릴레이 etc.)."""

    DART_SAMPLE = (
        "📌 파두(시가총액: 6조 1,569억) #A440110\n"
        "📁 단일판매ㆍ공급계약체결\n"
        "2026.05.22 10:59:21 (현재가 : 122,900원, +5.31%)\n"
        "\n"
        "계약상대 : 해외 Nand Flash Memory 제조사\n"
        "계약금액 : 287억\n"
        "\n"
        "공시링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260522900209\n"
        "회사정보: https://finance.naver.com/item/main.nhn?code=440110"
    )

    def test_dart_disclosure_matches(self):
        self.assertTrue(ignored.matches_contains(self.DART_SAMPLE))

    def test_dart_works_for_any_company(self):
        # Substring filter is company-agnostic — different ticker,
        # same DART relay format → still skipped.
        other = self.DART_SAMPLE.replace("파두", "삼양식품").replace(
            "440110", "003230"
        )
        self.assertTrue(ignored.matches_contains(other))

    def test_legit_export_caption_does_not_match(self):
        # Real BeOn export/import data never carries a dart.fss.or.kr
        # link, so the substring filter is safe.
        self.assertFalse(
            ignored.matches_contains(
                "4월 수출입 동향 — 반도체 +35%, 자동차 +12%"
            )
        )
        self.assertFalse(ignored.matches_contains("📊 5월 1-20일 잠정"))

    def test_empty_and_none_return_false(self):
        self.assertFalse(ignored.matches_contains(""))
        self.assertFalse(ignored.matches_contains(None))

    def test_contains_constant_is_tuple(self):
        self.assertIsInstance(ignored.IGNORED_CONTAINS, tuple)
        self.assertIn("dart.fss.or.kr", ignored.IGNORED_CONTAINS)


class TestExportCommentaryWrapper(unittest.TestCase):
    """월간 '주요 기업 수출입 확정치/잠정치 코멘트' 래퍼 (BeOn, msg
    9226·9227 2026-06-15). 실데이터는 첨부파일이고 캡션은 인트로
    narrative 라 _FINAL_LINE_RE 불일치 → 영구 store.db 미진입 →
    매일 미등록 알림. 본문 인트로 substring 으로 시리즈 전체 skip."""

    SAMPLE = (
        "26년 5월 주요 기업 수출입 확정치 코멘트\n"
        "\n"
        "이달의 주요 기업 수출데이터 확정치 공유드립니다. 섹터별 세부 동향과 "
        "전체 기업 스크리닝 데이터는 첨부된 파일을 참고해주시기 바랍니다\n"
        "\n"
        "솔브레인홀딩스 (전해액)\n"
        "일본향 고부가가치 전해액 공급 확대로 수출액이 전년 대비 큰 폭으로 "
        "증가했습니다."
    )

    def test_commentary_wrapper_matches(self):
        self.assertTrue(ignored.matches_contains(self.SAMPLE))

    def test_month_and_status_independent(self):
        # 제목의 연도·월·확정/잠정 status 가 매월 바뀌어도 본문 인트로는
        # 안정적 — 잠정치 코멘트 변종도 같은 entry 로 잡힌다.
        prov = self.SAMPLE.replace("5월", "6월").replace("확정치", "잠정치")
        self.assertTrue(ignored.matches_contains(prov))

    def test_real_confirmed_alert_does_not_match(self):
        # 진짜 파싱되는 확정치 알림은 'YYYY년 M월 확정치 수출데이터' 형태 —
        # '이달의 주요 기업 수출데이터' 인트로가 없어 안전하게 통과(미차단).
        legit = (
            "솔브레인홀딩스 (전해액)\n"
            "수출 1,234만$ (+56%)\n"
            "2026년 5월 확정치 수출데이터 입니다."
        )
        self.assertFalse(ignored.matches_contains(legit))


if __name__ == "__main__":
    unittest.main()
