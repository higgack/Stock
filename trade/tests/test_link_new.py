"""수출입 대시보드 형제 링크의 NEW 표시(trade.link_new) 회귀.

사용자 2026-09-30 "수출입에 여기 대시보드쪽에 업데이트가 되는것이 있으면 5일간 New 라는
표시가 있어서 체크해야한다는걸 내가 알아볼수 있게해줘".

계약:
- 기준은 원천의 **원 게시 시각**(posted_at) — 같은 대시보드 알림 카드의 NEW 와 같은 축.
- 창은 KST 달력일로 게시일 포함 NEW_DAYS 일.
- 링크 줄의 모든 링크가 판정 원천을 갖는다(판정하지 않는 링크는 사유와 함께 선언).
- 판정은 렌더 경로에 실제로 배선돼 있다(헬퍼 테스트만으로는 배선을 떼는 변형을 못
  잡는다 — 실수 #20). 그래서 render_html 을 통째로 태운다.
- 픽스처는 제품 삽입 경로(upsert_*·record)로 심는다(실수 #155).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trade import link_new
from trade import badonion_sources as srcs

UTC = timezone.utc


# ── parse_ts ────────────────────────────────────────────────────────────────

def test_parse_ts_reads_offsets_and_naive_as_utc():
    want = datetime(2026, 9, 28, 3, 14, 15, tzinfo=UTC)
    assert link_new.parse_ts("2026-09-28T03:14:15+00:00") == want
    # 오프셋 없는 값은 UTC 로 본다(텔레그램 시각은 UTC) — 서버 로컬 시간대가 아니다.
    assert link_new.parse_ts("2026-09-28T03:14:15") == want
    assert link_new.parse_ts("2026-09-28T12:14:15+09:00") == want


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-date", 123, b"2026-09-28"])
def test_parse_ts_rejects_non_iso(bad):
    assert link_new.parse_ts(bad) is None


# ── is_new — KST 달력일, 게시일 = 1일째 ───────────────────────────────────────

# UTC 15:30 = KST 다음날 00:30. UTC 날짜로 세면 하루가 어긋나는 자리를 일부러 고른다.
POSTED = datetime(2026, 9, 28, 15, 30, tzinfo=UTC)          # KST 2026-09-29 00:30


def test_is_new_counts_kst_calendar_days_including_post_day():
    # KST 10-03 23:59 — 게시일(09-29)로부터 4일 뒤 = 5일째 → 아직 NEW.
    assert link_new.is_new(POSTED, datetime(2026, 10, 3, 14, 59, tzinfo=UTC))
    # KST 10-04 00:00 — 6일째 → NEW 아님.
    assert not link_new.is_new(POSTED, datetime(2026, 10, 3, 15, 0, tzinfo=UTC))
    # 게시 당일.
    assert link_new.is_new(POSTED, POSTED)


def test_is_new_edge_cases():
    now = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    assert not link_new.is_new(None, now)
    # 시간대 없는 now 는 UTC 로 본다 — 결과가 aware UTC 와 같아야 한다.
    naive = datetime(2026, 10, 3, 14, 59)
    assert link_new.is_new(POSTED, naive) == link_new.is_new(
        POSTED, naive.replace(tzinfo=UTC))
    # 게시 시각이 조금 미래여도(시계 차이) 새 글로 본다.
    assert link_new.is_new(now + timedelta(minutes=3), now)
    # days 인자를 주면 그 창을 쓴다.
    later = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)           # KST 10-04 → 6일째
    assert not link_new.is_new(POSTED, later)
    assert link_new.is_new(POSTED, later, days=7)


def test_naive_now_is_utc_not_the_host_local_zone():
    """시간대 없는 now 를 호스트 로컬 시간으로 읽으면(규칙 10a 위반) 호스트마다 날짜가
    갈린다. 샌드박스·VM 이 마침 UTC 면 그 결함이 안 보이므로 로컬 시간대를 바꿔 잰다."""
    import os
    import time
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        # UTC 로 읽으면 KST 10-03 23:59(5일째 → NEW), 뉴욕 로컬로 읽으면 KST 10-04 03:59.
        assert link_new.is_new(POSTED, datetime(2026, 10, 3, 14, 59))
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_default_window_is_read_at_call_time(monkeypatch):
    # 가이드 문구와 배지가 같은 상수를 호출 시점에 읽는다 — 기본 인자로 굳으면
    # 상수를 바꿔도 배지만 옛 창을 쓴다.
    later = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)
    monkeypatch.setattr(link_new, "NEW_DAYS", 7)
    assert link_new.is_new(POSTED, later)
    assert "게시일 포함 7일간" in link_new.badge_html(POSTED, later)


# ── badge_html ──────────────────────────────────────────────────────────────

def test_badge_html_shows_kst_post_time_in_tooltip():
    b = link_new.badge_html(POSTED, datetime(2026, 10, 1, 3, 0, tzinfo=UTC))
    assert b.startswith(" "), "라벨과 붙지 않게 앞에 공백"
    assert 'class="link-new"' in b and ">NEW</span>" in b
    m = re.search(r'title="([^"]*)"', b)
    assert m, b
    assert "2026-09-29 00:30 KST" in m.group(1), m.group(1)      # UTC 15:30 → KST
    assert f"게시일 포함 {link_new.NEW_DAYS}일간" in m.group(1)


def test_badge_html_empty_when_not_new():
    assert link_new.badge_html(None, datetime(2026, 10, 1, tzinfo=UTC)) == ""
    assert link_new.badge_html(POSTED, datetime(2026, 10, 20, tzinfo=UTC)) == ""


# ── latest_posted_at ────────────────────────────────────────────────────────

def _tw_row(conn, item: str, posted_at: str, month: str = "2026-08"):
    from trade import tw_exports
    tw_exports.upsert_tw(conn, item, {"month": month, "export_value_musd": 1.0},
                         companies="x", chart_media=None, source_message_id=1,
                         posted_at=posted_at, raw_text="x")


def test_latest_posted_at_parses_instead_of_string_max(tmp_path):
    """문자열 MAX 는 오프셋이 섞이면 늦은 시각을 못 고른다.
    '…T19:00+09:00'(= UTC 10:00)이 문자열로는 '…T12:00+00:00'(UTC 12:00)보다 크다."""
    from trade import tw_exports
    db = tmp_path / "tw.db"
    conn = tw_exports.open_tw_db(db)
    _tw_row(conn, "반도체", "2026-09-28T12:00:00+00:00")
    _tw_row(conn, "디스플레이", "2026-09-28T19:00:00+09:00")
    conn.close()
    assert link_new.latest_posted_at(db) == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def test_latest_posted_at_missing_file_is_none_and_not_created(tmp_path, caplog):
    db = tmp_path / "nope.db"
    with caplog.at_level("WARNING", logger="trade.link_new"):
        assert link_new.latest_posted_at(db) is None
    assert not db.exists(), "링크를 그리려고 빈 DB 를 만들면 안 된다"
    # 아직 데이터가 없는 소스는 정상 상태다 — 5분마다 경고가 쌓이면 진짜 경고를 가린다.
    assert not caplog.records, caplog.text


def test_latest_posted_at_across_tables_ignores_blank_and_no_column(tmp_path):
    db = tmp_path / "multi.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE a (k TEXT, posted_at TEXT)")
    conn.execute("CREATE TABLE b (k TEXT, posted_at TEXT)")
    conn.execute("CREATE TABLE meta (k TEXT, v TEXT)")           # posted_at 없음
    conn.executemany("INSERT INTO a VALUES (?, ?)",
                     [("x", "2026-09-20T00:00:00+00:00"), ("y", ""), ("z", None)])
    conn.execute("INSERT INTO b VALUES ('w', '2026-09-25T00:00:00+00:00')")
    conn.execute("INSERT INTO meta VALUES ('posted_at', '2099-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    assert link_new.latest_posted_at(db) == datetime(2026, 9, 25, tzinfo=UTC)

    only_blank = tmp_path / "blank.db"
    conn = sqlite3.connect(only_blank)
    conn.execute("CREATE TABLE a (k TEXT, posted_at TEXT)")
    conn.executemany("INSERT INTO a VALUES (?, ?)", [("x", ""), ("y", None), ("z", "쓰레기")])
    conn.commit()
    conn.close()
    assert link_new.latest_posted_at(only_blank) is None


def test_latest_posted_at_reads_while_a_wal_writer_is_open(tmp_path):
    # tw.db·jp.db 는 WAL 이다. 쓰는 연결이 열려 있어도 읽기 전용 연결이 커밋분을 본다.
    from trade import tw_exports
    db = tmp_path / "tw.db"
    conn = tw_exports.open_tw_db(db)
    try:
        _tw_row(conn, "반도체", "2026-09-28T12:00:00+00:00")
        assert link_new.latest_posted_at(db) == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    finally:
        conn.close()


def test_latest_posted_at_corrupt_file_warns_and_returns_none(tmp_path, caplog):
    db = tmp_path / "broken.db"
    db.write_bytes(b"this is not a sqlite database" * 64)
    with caplog.at_level("WARNING", logger="trade.link_new"):
        assert link_new.latest_posted_at(db) is None
    assert any("broken.db" in r.getMessage() and "NEW 를 판정하지 않습니다" in r.getMessage()
               for r in caplog.records), caplog.text


# ── latest_for ──────────────────────────────────────────────────────────────

def _archive_to(monkeypatch, tmp_path):
    from trade import report_archive
    monkeypatch.setattr(report_archive, "ARCHIVE_JSONL", tmp_path / "report_archive.jsonl")
    monkeypatch.setattr(report_archive, "SNAP_DIR", tmp_path / "report_archive_pages")
    return report_archive


def test_latest_for_resolves_every_declared_source(tmp_path, monkeypatch):
    ra = _archive_to(monkeypatch, tmp_path)
    assert link_new.latest_for(None, tmp_path) is None
    assert link_new.latest_for("report_archive", tmp_path) is None     # 아직 없음
    kst_then = datetime(2026, 9, 27, 21, 0, tzinfo=link_new.KST)
    ra.record(kind="company", title="삼성전자", html_body="<p>x</p>", summary="s",
              now=kst_then)
    assert link_new.latest_for("report_archive", tmp_path) == kst_then
    from trade import tw_exports
    conn = tw_exports.open_tw_db(tmp_path / "tw.db")
    _tw_row(conn, "반도체", "2026-09-28T12:00:00+00:00")
    conn.close()
    assert link_new.latest_for("db:tw.db", tmp_path) == datetime(
        2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["db:", "tw.db", "archive", "db"])
def test_latest_for_rejects_unknown_source(bad, tmp_path):
    with pytest.raises(ValueError):
        link_new.latest_for(bad, tmp_path)


# ── 나쁜양파 레지스트리 nav 배선 ──────────────────────────────────────────────

def test_nav_html_puts_badge_before_arrow_only_where_asked():
    nav = srcs.nav_html(badge_for=lambda href: " <b>X</b>" if href == "tw.html" else "")
    tw = next(s for s in srcs.nav_sources() if s.html_file == "tw.html")
    assert f'<a href="tw.html">{tw.nav_label} <b>X</b> →</a>' in nav
    assert nav.count("<b>X</b>") == 1
    # 배지를 안 주면 옛 모양 그대로.
    plain = srcs.nav_html()
    assert "<b>X</b>" not in plain
    assert f'<a href="tw.html">{tw.nav_label} →</a>' in plain


# ── 렌더 경로 E2E ───────────────────────────────────────────────────────────

def _links(html: str) -> dict[str, str]:
    m = re.search(r'<div class="report-archive-link">(.*?)</div>', html, re.S)
    assert m, "형제 링크 줄이 없다"
    return dict(re.findall(r'<a href="([^"]+)">(.*?)</a>', m.group(1)))


def _render_fixture(tmp_path, monkeypatch):
    """store.db 옆에 형제 DB 를 제품 삽입 경로로 심는다 — 게시 시각은 지금 시계에서
    파생(날짜 리터럴은 며칠 뒤 창 밖으로 나가 무관한 커밋에서 빨간불이 된다)."""
    from trade import cn_exports, jp_exports, tw_exports
    from trade.store import open_db
    now = datetime.now(UTC)
    open_db(tmp_path / "store.db").close()
    conn = tw_exports.open_tw_db(tmp_path / "tw.db")               # 하루 전 → NEW
    _tw_row(conn, "반도체", (now - timedelta(days=1)).isoformat())
    conn.close()
    conn = cn_exports.open_cn_db(tmp_path / "cn.db")               # 30일 전 → 아님
    cn_exports.upsert_cn(conn, "반도체", {"month": "2026-07", "export_value_musd": 1.0},
                         companies="x", chart_media=None, source_message_id=1,
                         posted_at=(now - timedelta(days=30)).isoformat(), raw_text="x")
    conn.close()
    conn = jp_exports.open_jp_db(tmp_path / "jp.db")               # 이틀 전 → NEW
    jp_exports.upsert_jp(conn, {"item": "반도체", "latest_month": "2026-08",
                                "export_value_bn": 1.0, "source_message_id": 1,
                                "posted_at": (now - timedelta(days=2)).isoformat(),
                                "raw_text": "x"})
    conn.close()
    ra = _archive_to(monkeypatch, tmp_path)                         # 사흘 전 → NEW
    ra.record(kind="company", title="삼성전자", html_body="<p>x</p>", summary="s",
              now=datetime.now(link_new.KST) - timedelta(days=3))
    from trade.dashboard import render_html
    return render_html(tmp_path / "store.db")


def test_render_puts_new_only_on_recently_posted_pages(tmp_path, monkeypatch):
    links = _links(_render_fixture(tmp_path, monkeypatch))
    new = {h for h, inner in links.items() if 'class="link-new"' in inner}
    assert new == {"tw.html", "jp.html", "report_archive.html"}, new
    # 배지는 라벨 뒤·화살표 앞, 링크 안에 있다(누르면 그 페이지로 간다).
    assert re.search(r'대만 수출 데이터\(나쁜양파\) <span class="link-new"[^>]*>NEW</span> →$',
                     links["tw.html"]), links["tw.html"]


def test_every_link_in_the_row_has_a_declared_new_source(tmp_path, monkeypatch):
    """링크 줄에 링크를 더하고 판정 원천을 안 적으면 그 링크는 영원히 NEW 가 안 붙는다
    (조용한 누락, 실수 #24). 렌더된 링크 줄과 판정 표가 같은 집합이어야 한다."""
    from trade import dashboard as td
    links = _links(_render_fixture(tmp_path, monkeypatch))
    assert set(links) == set(td._link_latest(tmp_path)), (
        sorted(set(links) ^ set(td._link_latest(tmp_path))))
    # 판정하지 않는 링크는 사유와 함께 선언한 것만 — 늘리려면 이 테스트를 고쳐야 한다.
    assert {h for h, s in td._FIXED_LINK_NEW_SRC.items() if s is None} == {"reference.html"}
    # 나쁜양파 레지스트리의 모든 페이지가 링크 줄에 있다(옛 판의 소스 문자열 검사를
    # 렌더 결과로 옮긴 것 — test_badonion_sources 참고).
    assert {s.html_file for s in srcs.nav_sources()} <= set(links)


def test_guide_and_badge_read_the_same_window(tmp_path, monkeypatch):
    # 📡 가이드 문구의 'N일' 과 배지의 창이 같은 상수에서 온다 — 한쪽만 리터럴로
    # 적으면 창을 바꾼 날 화면이 두 말을 한다(실수 #38·#55).
    monkeypatch.setattr(link_new, "NEW_DAYS", 7)
    html = _render_fixture(tmp_path, monkeypatch)
    assert "최근 7일 안" in html
    assert "게시일 포함 7일간" in html
    assert "최근 5일 안" not in html


def test_new_badge_css_is_defined_and_meets_aa(tmp_path, monkeypatch):
    from bot import css_contrast
    html = _render_fixture(tmp_path, monkeypatch)
    rule = re.search(r"\.report-archive-link \.link-new\{([^}]*)\}", html)
    assert rule, "NEW 배지를 쓰면서 CSS 를 안 정의하면 스타일이 조용히 빠진다(실수 #201)"
    assert "background:var(--new)" in rule.group(1)
    assert "color:var(--new-on)" in rule.group(1)
    # 한 화면의 NEW 배지 셋(링크·카드·섹션)이 같은 토큰을 쓴다 — 옛 리터럴 #ff3b30 은
    # 흰 글씨 대비 3.55:1 로 AA 미달이었다.
    for cls in (".link-new", ".mini-new", ".section-new"):
        m = re.search(re.escape(cls) + r"\{([^}]*)\}", html)
        assert m and "background:var(--new)" in m.group(1), cls
    src = Path("trade/dashboard.py").read_text(encoding="utf-8")
    pairs = [r for r in css_contrast.on_pairs(src)
             if r["text"] == "--new-on" and r["surface"] == "--new"]
    assert {r["selector"] for r in pairs} >= {":root", "body.dark"}, pairs
    assert all(r["ratio"] >= css_contrast.AA_MIN for r in pairs), pairs
