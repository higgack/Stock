"""메모(bot.memos) + 알람(bot.reminders) 서버 로직 회귀 (사용자 2026-06-26)."""
import tempfile
from pathlib import Path

import bot.memos as M
import bot.reminders as R


def _fresh_memo(d):
    M._FILE = Path(d) / "memos.json"
    return M


def _fresh_rem(d):
    R._FILE = Path(d) / "reminders.json"
    return R


def test_memo_set_get_delete():
    with tempfile.TemporaryDirectory() as d:
        m = _fresh_memo(d)
        assert m.set_memo("dart", "r1", "내 생각")["ok"] is True
        assert m.get("dart", "r1") == "내 생각"
        assert m.all_memos() == {"dart": {"r1": "내 생각"}}
        # 빈 텍스트 = 삭제, 빈 surface 정리
        assert m.set_memo("dart", "r1", "   ")["has"] is False
        assert m.all_memos() == {}


def test_memo_invalid_and_truncate():
    with tempfile.TemporaryDirectory() as d:
        m = _fresh_memo(d)
        assert m.set_memo("bogus", "x", "t")["ok"] is False     # 화이트리스트 밖
        assert m.set_memo("blog", "", "t")["ok"] is False       # 빈 id
        big = m.set_memo("blog", "b1", "x" * 99999)
        assert big["ok"] and len(m.get("blog", "b1")) <= M._MAX_TEXT


def test_reminder_set_due_sent_confirm():
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    with tempfile.TemporaryDirectory() as d:
        r = _fresh_rem(d)
        # 지정 날짜+시각 (MM.DD.HH:MM, KST) — 6/26 09:30
        assert r.set_reminder("blog", "b1", "06.26.09:30", True, memo="m", card="c")["active"]
        assert r.set_reminder("blog", "b1", "6.26.9:30", True)["time"] == "06.26.09:30"  # 0패딩 정규화
        assert r.all_reminders() == {"blog": {"b1": {"time": "06.26.09:30", "active": True}}}
        d26_0931 = datetime(2026, 6, 26, 9, 31, tzinfo=KST)
        d26_0900 = datetime(2026, 6, 26, 9, 0, tzinfo=KST)
        d25_0931 = datetime(2026, 6, 25, 9, 31, tzinfo=KST)
        d27_0931 = datetime(2026, 6, 27, 9, 31, tzinfo=KST)
        d27_0000 = datetime(2026, 6, 27, 0, 0, tzinfo=KST)
        assert [x["id"] for x in r.due(d26_0931, "2026-06-26")] == ["b1"]   # 지정일·시각 도달
        assert r.due(d26_0900, "2026-06-26") == []        # 그날 시각 전
        assert r.due(d25_0931, "2026-06-25") == []        # 지정일 전
        r.mark_sent("blog", "b1", "2026-06-26")
        assert r.due(d26_0931, "2026-06-26") == []        # 오늘 발송됨
        assert [x["id"] for x in r.due(d27_0931, "2026-06-27")] == ["b1"]   # 다음날 같은시각 재발송
        assert r.due(d27_0000, "2026-06-27") == []        # 다음날도 시각 전(00:00)엔 안 감
        item = r.due(d27_0931, "2026-06-27")[0]
        assert item["memo"] == "m" and item["card"] == "c" and item["key"]
        assert r.confirm_by_key(r.key_of("blog", "b1")) is not None
        assert r.all_reminders() == {}


def test_reminder_invalid_time_clears():
    with tempfile.TemporaryDirectory() as d:
        r = _fresh_rem(d)
        assert r.set_reminder("blog", "b1", "06.26.25:99", True)["active"] is False  # 무효=해제
        assert r.set_reminder("blog", "b1", "09:30", True)["active"] is False         # 옛 HH:MM=무효
        assert r.set_reminder("blog", "b1", "13.40.09:30", True)["active"] is False   # 월/일 범위
        assert r.all_reminders() == {}


def test_reminder_key_stable_unique():
    k1 = R.key_of("blog", "b1")
    assert k1 == R.key_of("blog", "b1") and len(k1) == 16
    assert R.key_of("blog", "b1") != R.key_of("dart", "b1")
