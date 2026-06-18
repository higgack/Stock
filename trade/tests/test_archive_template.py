"""trade.archive_template — 날짜그룹 아카이브 렌더러 가드.

default_open 접기 토글(사용자 2026-06-18 'AI 아카이브 접혀있게') + 뒤로가기
스크롤 위치 복원 임베드(사용자 2026-06-18 '뒤로 가면 원래 자리로'). 실제 날짜에
독립 — 렌더러 자신의 _today_kst_iso() 로 '오늘' 레코드를 만들어 토글만 검증."""

import unittest

from trade import archive_template as AT
from trade.archive_template import FieldMap, render_archive_page


def _today_run():
    today = AT._today_kst_iso()
    return [{
        "_date": today, "ts": f"{today}T14:30:00+09:00",
        "body": "<b>오늘</b> 본문 ABC", "title": "오늘 카드",
    }]


_FM = FieldMap(date="_date", ts="ts", body="body", title="title",
               cost=None, elapsed=None, kind=None)


class DefaultOpenTests(unittest.TestCase):
    def test_default_open_true_expands_today(self):
        # 기본값(True): 이번 달·오늘은 펼침 — 기존 동작 회귀 방지
        h = render_archive_page(_today_run(), title="T", subtitle="s",
                                field_map=_FM)
        self.assertIn('<details class="month" open>', h)
        self.assertIn('<details class="day" open>', h)

    def test_default_open_false_collapses_today(self):
        # default_open=False: 오늘이어도 월/일 접힌 채로 (AI 아카이브 정책)
        h = render_archive_page(_today_run(), title="T", subtitle="s",
                                field_map=_FM, default_open=False)
        self.assertIn('<details class="month">', h)       # open 속성 없음
        self.assertNotIn('<details class="month" open>', h)
        self.assertNotIn('<details class="day" open>', h)


class ScrollRestoreTests(unittest.TestCase):
    def test_archive_embeds_scroll_restore(self):
        # 뒤로가기 시 보던 위치 복원 스크립트가 본문에 임베드
        h = render_archive_page(_today_run(), title="T", subtitle="s",
                                field_map=_FM)
        self.assertIn("scrollRestoration", h)
        self.assertIn("sessionStorage", h)
        self.assertIn(AT.SCROLL_RESTORE_JS, h)

    def test_empty_page_still_renders(self):
        # 빈 목록도 안전(early return 경로) — 회귀 가드
        h = render_archive_page([], title="T", subtitle="s", field_map=_FM)
        self.assertIn("아직 기록이 없습니다.", h)


if __name__ == "__main__":
    unittest.main()
