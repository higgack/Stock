"""수출입 대시보드 형제 링크의 NEW 표시(trade.link_new) 회귀.

사용자 2026-09-30 "수출입에 여기 대시보드쪽에 업데이트가 되는것이 있으면 5일간 New 라는
표시가 있어서 체크해야한다는걸 내가 알아볼수 있게해줘".

계약:
- 기준은 원천의 **원 게시 시각**(posted_at) — 같은 대시보드 알림 카드의 NEW 와 같은 축.
- 창은 KST 달력일로 게시일 포함 NEW_DAYS 일.
- 링크 줄의 모든 링크가 판정 원천을 갖는다(판정하지 않는 링크는 사유와 함께 선언).
- 판정은 렌더 경로에 실제로 배선돼 있다(헬퍼 테스트만으로는 배선을 떼는 변형을 못
  잡는다 — 실수 #20). 그래서 render_html 을 통째로 태운다.
- 픽스처는 제품 삽입 경로(upsert_*·record·_ingest_group)로 심는다(실수 #155).
- ingest 가 원 게시 시각(forward_origin_date)을 받은 시각(date)보다 먼저 쓴다 — 그래야
  재포워드·회수에 NEW 가 안 켜진다(화면 가이드가 약속한 전제, 독립 리뷰 F2).
- NEW 는 곁들이 — 조회든 배지 생성이든 한 링크가 던져도 대시보드는 그려진다(#315).
"""
from __future__ import annotations

import collections
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trade import link_new
from trade import badonion_sources as srcs

UTC = timezone.utc


@contextmanager
def _host_tz(tz: str, offset: timedelta):
    """테스트 중에만 호스트 로컬 시간대를 바꾼다. POSIX 문자열('XST+5' = UTC-5)이라
    tzdata 가 없는 호스트에서도 동작한다 — 'America/New_York' 같은 이름은 tzdata 가
    없으면 조용히 UTC 로 떨어져 테스트가 눈이 먼다(독립 리뷰 실측). 그래서 바뀐 뒤의
    로컬 오프셋을 **재서** 확인한다(#91b 재는 대상이 맞나)."""
    import os
    import time
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        assert datetime.now().astimezone().utcoffset() == offset, "시간대 전환이 안 먹었다"
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


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


def test_parse_ts_naive_is_utc_not_the_host_local_zone():
    """오프셋 없는 값을 호스트 로컬 시간으로 읽는 변형은 UTC 호스트(샌드박스·VM)에선
    티가 안 난다(리뷰 L4 생존) — 로컬 시간대를 UTC-5 로 바꿔 잰다."""
    with _host_tz("XST+5", timedelta(hours=-5)):
        assert link_new.parse_ts("2026-09-28T03:14:15") == datetime(
            2026, 9, 28, 3, 14, 15, tzinfo=UTC)


@pytest.mark.parametrize("edge", ["9999-12-31T23:59:59+00:00", "0001-01-01T00:00:00+10:00"])
def test_parse_ts_rejects_values_kst_cannot_hold(edge):
    """달력 끝 값은 KST 로 옮기다 OverflowError 를 낸다 — 판정·툴팁 단계에서 대시보드
    렌더 전체를 멈췄다(리뷰 F1 실측). 읽는 자리에서 판정할 수 없는 값으로 거른다."""
    assert link_new.parse_ts(edge) is None
    # 달력 끝이라도 KST 로 옮겨지는 값은 받는다 — 막는 기준은 '옮길 수 있나' 다.
    assert link_new.parse_ts("9999-12-31T14:59:59+00:00") == datetime(
        9999, 12, 31, 14, 59, 59, tzinfo=UTC)


def test_now_utc_is_aware_utc_whatever_the_host_zone():
    """렌더의 기본 시계 — 호스트가 KST(UTC+9)여도 시간대가 붙은 UTC 지금이어야 한다.
    `datetime.now()`(시간대 없는 로컬)로 바뀌면 is_new 가 그걸 UTC 로 읽어 9시간을
    앞당긴다(리뷰 D8 — 배지가 마지막 날 KST 15:00 에 꺼진다)."""
    import time
    with _host_tz("XST-9", timedelta(hours=9)):
        n = link_new.now_utc()
        assert n.utcoffset() == timedelta(0), n
        assert abs(n.timestamp() - time.time()) < 60, n


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
    with _host_tz("XST+5", timedelta(hours=-5)):
        # UTC 로 읽으면 KST 10-03 23:59(5일째 → NEW), UTC-5 로컬로 읽으면 KST 10-04 04:59.
        assert link_new.is_new(POSTED, datetime(2026, 10, 3, 14, 59))


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


def test_latest_posted_at_skips_far_future_values_and_says_so(tmp_path, caplog):
    """지금보다 FUTURE_SLACK(1일) 넘게 미래인 값은 원 게시 시각일 수 없다 — 그런 값 하나가
    최댓값으로 굳으면 NEW 가 영원히 켜지고 진짜 마지막 게시 시각이 툴팁에서 사라진다
    (리뷰 F1 짝). 빼되 몇 건을 뺐는지 말한다(#12). 시계 차이 범위(1일 안)는 받는다."""
    from trade import tw_exports
    now = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
    db = tmp_path / "tw.db"
    conn = tw_exports.open_tw_db(db)
    _tw_row(conn, "반도체", "2026-09-28T12:00:00+00:00")
    _tw_row(conn, "디스플레이", "2099-01-01T00:00:00+00:00")
    _tw_row(conn, "배터리", (now + link_new.FUTURE_SLACK).isoformat())     # 경계 — 받는다
    conn.close()
    with caplog.at_level("WARNING", logger="trade.link_new"):
        assert link_new.latest_posted_at(db, now=now) == now + link_new.FUTURE_SLACK
    msgs = [r.getMessage() for r in caplog.records]
    assert any("tw.db" in m and "1건" in m and "2099-01-01" in m for m in msgs), msgs

    # 경계를 1초 넘으면 뺀다 — 남는 건 과거 값.
    conn = tw_exports.open_tw_db(db)
    _tw_row(conn, "배터리", (now + link_new.FUTURE_SLACK + timedelta(seconds=1)).isoformat())
    conn.close()
    assert link_new.latest_posted_at(db, now=now) == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def test_far_future_limit_reads_the_link_new_clock_by_default(tmp_path, monkeypatch):
    # now 를 안 주면 link_new.now_utc() 가 기준이다 — 렌더와 같은 시계.
    from trade import tw_exports
    db = tmp_path / "tw.db"
    conn = tw_exports.open_tw_db(db)
    _tw_row(conn, "반도체", "2099-01-01T00:00:00+00:00")
    conn.close()
    assert link_new.latest_posted_at(db) is None              # 지금 기준으론 먼 미래
    monkeypatch.setattr(link_new, "now_utc", lambda: datetime(2098, 12, 31, 12, tzinfo=UTC))
    assert link_new.latest_posted_at(db) == datetime(2099, 1, 1, tzinfo=UTC)


def test_latest_posted_at_opens_the_db_read_only(tmp_path, monkeypatch):
    """운영 DB 를 링크 하나 그리려고 쓰기 가능하게 열지 않는다 — `mode=rwc` 로 바꿔도
    다른 테스트는 전부 통과했다(리뷰 L7 생존). 연결이 실제로 쓰기를 거절하는지 잰다."""
    from trade import tw_exports
    db = tmp_path / "tw.db"
    conn = tw_exports.open_tw_db(db)
    _tw_row(conn, "반도체", "2026-09-28T12:00:00+00:00")
    conn.close()
    real = sqlite3.connect
    seen: list[str] = []

    def spy(*a, **kw):
        c = real(*a, **kw)
        try:
            c.execute("CREATE TABLE __probe (x)")
            seen.append("wrote")
        except sqlite3.OperationalError as exc:
            seen.append(str(exc))
        return c

    monkeypatch.setattr(link_new.sqlite3, "connect", spy)
    assert link_new.latest_posted_at(db) == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert seen and all("readonly" in m for m in seen), seen


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


def _seed_fixture(tmp_path, monkeypatch, *, extra_tw=()) -> dict[str, datetime]:
    """store.db 옆에 형제 DB 를 제품 삽입 경로로 심는다 — 게시 시각은 지금 시계에서
    파생(날짜 리터럴은 며칠 뒤 창 밖으로 나가 무관한 커밋에서 빨간불이 된다).
    심은 게시 시각을 돌려준다. ``extra_tw`` 는 tw.db 에 더 심을 posted_at 원문들."""
    from trade import cn_exports, jp_exports, tw_exports
    from trade.store import open_db
    now = datetime.now(UTC)
    open_db(tmp_path / "store.db").close()
    conn = tw_exports.open_tw_db(tmp_path / "tw.db")               # 하루 전 → NEW
    _tw_row(conn, "반도체", (now - timedelta(days=1)).isoformat())
    for i, raw in enumerate(extra_tw):
        _tw_row(conn, f"극단값{i}", raw)
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
    archived = datetime.now(link_new.KST) - timedelta(days=3)
    ra.record(kind="company", title="삼성전자", html_body="<p>x</p>", summary="s",
              now=archived)
    return {"tw": now - timedelta(days=1), "cn": now - timedelta(days=30),
            "jp": now - timedelta(days=2), "report_archive": archived}


def _render_fixture(tmp_path, monkeypatch, **kw):
    _seed_fixture(tmp_path, monkeypatch, **kw)
    from trade.dashboard import render_html
    return render_html(tmp_path / "store.db")


def _badge_ok(inner: str, label: str) -> bool:
    """배지가 라벨 바로 뒤·화살표 바로 앞, 링크 안에 있는가(누르면 그 페이지로 간다)."""
    return re.fullmatch(re.escape(label) + r' <span class="link-new" title="[^"]*">NEW</span> →',
                        inner) is not None


def _labels() -> dict[str, str]:
    from trade import dashboard as td
    out = {href: label for href, label, _src in td._FIXED_LINKS}
    out.update({s.html_file: s.nav_label for s in srcs.nav_sources()})
    return out


def test_render_puts_new_only_on_recently_posted_pages(tmp_path, monkeypatch):
    links = _links(_render_fixture(tmp_path, monkeypatch))
    new = {h for h, inner in links.items() if 'class="link-new"' in inner}
    assert new == {"tw.html", "jp.html", "report_archive.html"}, new
    # 배지는 라벨 뒤·화살표 앞, 링크 안에 있다(누르면 그 페이지로 간다) — 고정 링크도
    # 레지스트리 링크도. 한 링크만 재면 나머지의 위치가 틀어져도 모른다(리뷰 D14).
    labels = _labels()
    for href in new:
        assert _badge_ok(links[href], labels[href]), (href, links[href])


def test_every_link_in_the_row_has_a_declared_new_source(tmp_path, monkeypatch):
    """링크 줄에 링크를 더하고 판정 원천을 안 적으면 그 링크는 영원히 NEW 가 안 붙는다
    (조용한 누락, 실수 #24). 렌더된 링크 줄과 판정 표가 같은 집합이어야 한다."""
    from trade import dashboard as td
    links = _links(_render_fixture(tmp_path, monkeypatch))
    assert set(links) == set(td._link_latest(tmp_path)), (
        sorted(set(links) ^ set(td._link_latest(tmp_path))))
    # 판정하지 않는 링크는 사유와 함께 선언한 것만 — 늘리려면 이 테스트를 고쳐야 한다.
    assert {h for h, s in td._FIXED_LINK_NEW_SRC.items() if s is None} == {"reference.html"}
    # _link_latest 는 링크마다 예외를 삼키므로(곁들이가 본체를 죽이면 안 된다) 판정 원천
    # 표의 오타는 렌더로는 안 보인다 — 표의 값을 직접 불러 잡는다.
    for src in td._FIXED_LINK_NEW_SRC.values():
        link_new.latest_for(src, tmp_path)
    # 나쁜양파 레지스트리의 모든 페이지가 링크 줄에 있다(옛 판의 소스 문자열 검사를
    # 렌더 결과로 옮긴 것 — test_badonion_sources 참고).
    assert {s.html_file for s in srcs.nav_sources()} <= set(links)


def test_one_failing_link_does_not_break_the_dashboard(tmp_path, monkeypatch, caplog):
    """NEW 는 곁들이다 — 한 링크의 판정이 던져도 그 링크만 배지를 잃고, 대시보드와
    나머지 링크의 NEW 는 그대로 그려져야 한다(실수 #315 곁들이 하나가 본체를 지운다)."""
    real = link_new.latest_posted_at

    def flaky(db_path, **kw):
        if Path(db_path).name == "tw.db":
            raise RuntimeError("boom")
        return real(db_path, **kw)

    monkeypatch.setattr(link_new, "latest_posted_at", flaky)
    with caplog.at_level("WARNING", logger="trade-dashboard"):
        links = _links(_render_fixture(tmp_path, monkeypatch))
    new = {h for h, inner in links.items() if 'class="link-new"' in inner}
    assert new == {"jp.html", "report_archive.html"}, new
    assert any("tw.html" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records), caplog.text


@pytest.mark.parametrize("edge", ["9999-12-31T23:59:59+00:00", "0001-01-01T00:00:00+10:00",
                                  "2099-01-01T00:00:00+00:00"])
def test_an_extreme_posted_at_does_not_break_the_dashboard(tmp_path, monkeypatch, edge):
    """리뷰 F1 실측 재현 — tw.db 에 이 값 한 행이 있으면 render_html 이 OverflowError 로
    멈춰 index.html 도 형제 페이지도 5분마다 갱신에 실패했다. 이제 그 값만 빼고 판정한다:
    tw 는 하루 전 게시로 그대로 NEW, 나머지 링크도 그대로."""
    links = _links(_render_fixture(tmp_path, monkeypatch, extra_tw=[edge]))
    new = {h for h, inner in links.items() if 'class="link-new"' in inner}
    assert new == {"tw.html", "jp.html", "report_archive.html"}, new
    assert "2099" not in links["tw.html"], "먼 미래 값이 툴팁의 '마지막 게시 시각'이 됐다"


def test_a_failing_badge_does_not_break_the_dashboard(tmp_path, monkeypatch, caplog):
    """격리는 조회뿐 아니라 배지를 만드는 단계에도 있어야 한다 — 첫 판은 조회만 감싸
    배지 생성(is_new·badge_html)이 던지면 렌더 전체가 죽었다(리뷰 F1)."""
    from trade.dashboard import render_html
    seeded = _seed_fixture(tmp_path, monkeypatch)
    real = link_new.badge_html

    def flaky(ts, now, **kw):
        if ts == seeded["tw"]:
            raise RuntimeError("boom")
        return real(ts, now, **kw)

    monkeypatch.setattr(link_new, "badge_html", flaky)
    with caplog.at_level("WARNING", logger="trade-dashboard"):
        links = _links(render_html(tmp_path / "store.db"))
    new = {h for h, inner in links.items() if 'class="link-new"' in inner}
    assert new == {"jp.html", "report_archive.html"}, new
    assert any("tw.html" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records), caplog.text


def test_every_judged_link_gets_its_badge_right_before_the_arrow(tmp_path, monkeypatch):
    """판정 원천이 있는 링크는 **전부** 새 게시가 있으면 배지가 라벨 뒤·화살표 앞에
    붙는다. 픽스처 DB 가 있는 링크만 재면, 새 고정 링크에서 배지를 빠뜨려도 그 DB 가
    픽스처에 없으면 모른다(리뷰 F5) — 모든 원천이 방금 게시했다고 두고 줄 전체를 잰다."""
    from trade import dashboard as td
    from trade.store import open_db
    now = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
    monkeypatch.setattr(link_new, "latest_for", lambda src, data_dir, **kw:
                        None if src is None else now - timedelta(hours=1))
    open_db(tmp_path / "store.db").close()
    links = _links(td.render_html(tmp_path / "store.db", now=now))
    labels = _labels()
    assert set(links) == set(labels), sorted(set(links) ^ set(labels))
    for href, inner in links.items():
        if td._FIXED_LINK_NEW_SRC.get(href, "db") is None:
            assert inner == f"{labels[href]} →", (href, inner)       # 판정하지 않는 링크
        else:
            assert _badge_ok(inner, labels[href]), (href, inner)


def test_render_reads_the_link_new_clock_when_now_is_not_given(tmp_path, monkeypatch):
    """운영 경로(render_html 에 now 없음)는 link_new.now_utc() 를 쓴다 — 렌더가 제 시계를
    따로 읽으면(datetime.now() 등) 호스트 시간대에 따라 배지가 9시간 일찍 꺼질 수 있고
    테스트는 그걸 못 본다(리뷰 D8 — UTC·KST 호스트 둘 다에서 살아남았다)."""
    from trade.dashboard import render_html
    _seed_fixture(tmp_path, monkeypatch)
    real_now = datetime.now(UTC)
    monkeypatch.setattr(link_new, "now_utc", lambda: real_now + timedelta(days=10))
    links = _links(render_html(tmp_path / "store.db"))
    assert not {h for h, inner in links.items() if 'class="link-new"' in inner}
    monkeypatch.setattr(link_new, "now_utc", lambda: real_now)
    links = _links(render_html(tmp_path / "store.db"))
    assert {h for h, inner in links.items() if 'class="link-new"' in inner} == {
        "tw.html", "jp.html", "report_archive.html"}


def test_render_window_boundary_with_an_injected_clock(tmp_path, monkeypatch):
    """게시 KST 09-28 00:00 → 5일째 끝(KST 10-02 23:59)까지 NEW, 6일째 0시(KST 10-03
    00:00)부터 아님 — 렌더에 시각을 넣어 경계를 E2E 로 잰다(리뷰 F3: 1·2·3·30일 전
    픽스처는 5일 경계와 멀어 렌더 단계의 날짜 계산을 못 잰다)."""
    from trade import tw_exports
    from trade.dashboard import render_html
    from trade.store import open_db
    _archive_to(monkeypatch, tmp_path)
    open_db(tmp_path / "store.db").close()
    conn = tw_exports.open_tw_db(tmp_path / "tw.db")
    _tw_row(conn, "반도체", "2026-09-27T15:00:00+00:00")              # KST 09-28 00:00
    conn.close()
    kst = link_new.KST
    last = _links(render_html(tmp_path / "store.db",
                              now=datetime(2026, 10, 2, 23, 59, tzinfo=kst)))
    off = _links(render_html(tmp_path / "store.db",
                             now=datetime(2026, 10, 3, 0, 0, tzinfo=kst)))
    assert 'class="link-new"' in last["tw.html"], last["tw.html"]
    assert 'class="link-new"' not in off["tw.html"], off["tw.html"]
    # 넣은 시각은 먼 미래 값 거르기에도 쓰인다 — 한 렌더 안에서 시계가 둘이면 안 된다.
    # 그 시각보다 1일 넘게 뒤인 게시는 원 게시 시각이 아니므로 NEW 가 아니다(미래 게시를
    # '새 글'로 보는 is_new 까지 가지 않는다).
    early = _links(render_html(tmp_path / "store.db",
                               now=datetime(2026, 9, 20, 0, 0, tzinfo=kst)))
    assert 'class="link-new"' not in early["tw.html"], early["tw.html"]


def test_latest_report_ts_picks_the_latest_of_many(tmp_path, monkeypatch, caplog):
    """보고서가 여럿이면 가장 늦은 것 — 픽스처가 1건이면 가장 오래된 것을 골라도
    통과했다(리뷰 L19 생존). 먼 미래 ts 는 게시 판정과 같은 규칙으로 뺀다."""
    ra = _archive_to(monkeypatch, tmp_path)
    kst = link_new.KST
    for when in (datetime(2026, 9, 1, 9, tzinfo=kst), datetime(2026, 9, 20, 9, tzinfo=kst),
                 datetime(2026, 8, 15, 9, tzinfo=kst), datetime(2099, 1, 1, 9, tzinfo=kst)):
        ra.record(kind="company", title=f"보고서 {when:%m%d}", html_body="<p>x</p>",
                  summary="s", now=when)
    with caplog.at_level("WARNING", logger="trade.link_new"):
        got = link_new.latest_report_ts(now=datetime(2026, 9, 30, tzinfo=UTC))
    assert got == datetime(2026, 9, 20, 9, tzinfo=kst)
    assert any("AI 보고서 아카이브" in r.getMessage() and "1건" in r.getMessage()
               for r in caplog.records), caplog.text


def test_latest_report_ts_skips_lines_that_are_not_records(tmp_path, monkeypatch):
    ra = _archive_to(monkeypatch, tmp_path)
    ra.ARCHIVE_JSONL.write_text(
        '[1, 2]\n"문자열"\n{"ts": "2026-09-27T21:00:00+09:00", "title": "x"}\n',
        encoding="utf-8")
    assert link_new.latest_report_ts() == datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


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
    # 문턱은 리터럴로 — 모듈 상수를 쓰면 그 상수를 내리는 변형이 통과한다(#66·#355).
    assert all(r["ratio"] >= 4.5 for r in pairs), pairs
    # 배지는 눈에 띄어야 하는 표식이다 — 배지 면 자체가 놓이는 표면(카드·페이지 배경)과
    # 비텍스트 대비 3:1(WCAG 1.4.11)을 넘어야 한다. 다크에서 #d70015 는 2.59:1 이었다(리뷰 F11).
    checked = set()
    for sel, tok in css_contrast.palette_blocks(src):
        if "--new" not in tok:
            continue
        for surf in ("--surface", "--bg"):
            r = css_contrast.contrast_ratio(tok["--new"], tok[surf])
            assert r is not None and r >= 3.0, (sel, surf, tok["--new"], tok[surf], r)
        checked.add(sel)
    assert checked >= {":root", "body.dark"}, checked


# ── ingest — 원 게시 시각이 먼저다(재포워드·회수에 NEW 가 안 켜지는 전제) ──────────

_TW_CAP = "6월 수출 대만\n\n▶️ 테스트\n\n26년06월: $9.1M  (+3.0% YoY)  (+1.0% MoM)"
_JP_CAP = ("📈 일본 수출 데이터 업데이트: 본딩 기기 (Bonding)\n📅 최신 월: 2026-05\n"
           "💰 수출액: 2.5십억 엔\n   YoY ▲ +12.0% / MoM ▼ -12.0%\n")
_KR_CAP = "건기식 (경기 김포시)\n\n2026년 5월 1일 ~ 31일 확정치 수출데이터 입니다."
_ORIGIN = "2026-08-01T00:00:00+00:00"          # 원래 글의 게시 시각
_RECEIVED = "2026-09-30T00:00:00+00:00"        # 봇이 (다시) 받은 시각


def _ingest_one(tmp_path, caption: str, *, origin: str | None) -> dict:
    """캡션 하나를 ingest 의 실제 라우팅(_ingest_group)으로 넣는다 — 한국 알림은
    store.db, 비온 일본은 jp.db, 나쁜양파는 레지스트리의 각 DB 로 간다."""
    from trade import jp_exports
    from trade.scripts import ingest_inbox as ii
    from trade.store import open_db
    store = open_db(tmp_path / "store.db")
    jp_conn = jp_exports.open_jp_db(tmp_path / "jp.db")
    conns = {s.key: s.open_db(tmp_path / s.db_file) for s in srcs.SOURCES}
    row = {"caption_present": True, "caption": caption, "text": None, "chat_id": -100,
           "message_id": 7, "media_group_id": None, "date": _RECEIVED}
    if origin is not None:
        row["forward_origin_date"] = origin
    counters: dict = collections.defaultdict(int)
    try:
        ii._ingest_group(store, [row], tmp_path / "media", counters, set(), jp_conn, conns)
    finally:
        for c in (store, jp_conn, *conns.values()):
            c.close()
    return counters


@pytest.mark.parametrize("caption,db_file", [
    (_TW_CAP, "tw.db"),         # 나쁜양파 레지스트리 경로
    (_JP_CAP, "jp.db"),         # 비온 일본 경로
    (_KR_CAP, "store.db"),      # 한국 알림 경로(알림 카드 NEW 의 원천)
])
def test_ingest_stores_the_original_post_time_not_the_receive_time(tmp_path, caption, db_file):
    """화면 가이드가 "같은 글을 다시 받거나 다시 파싱해도 켜지지 않는다" 고 약속하는
    근거는 ingest 가 원 게시 시각(forward_origin_date)을 받은 시각(date)보다 먼저 쓰는
    것 하나다. 그 순서를 뒤집어도 trade/tests 1,834건이 통과했다(리뷰 F2 — 그러면 모든
    재포워드·40일 회수가 5일간 거짓 NEW 를 띄운다). 세 경로 모두 ingest 를 태워 잰다."""
    counters = _ingest_one(tmp_path, caption, origin=_ORIGIN)
    latest = link_new.latest_posted_at(tmp_path / db_file)
    assert latest == datetime(2026, 8, 1, tzinfo=UTC), (db_file, latest, dict(counters))
    assert link_new.badge_html(latest, datetime(2026, 9, 30, 1, 0, tzinfo=UTC)) == ""
    assert counters.get("posted_at_from_date", 0) == 0, dict(counters)


def test_ingest_falls_back_to_the_receive_time_and_counts_it(tmp_path):
    """포워드가 아닌 글(원 게시 시각 없음)은 받은 시각이 게시 시각이다 — 그 글은 다시
    받으면 NEW 가 다시 켜질 수 있으므로 몇 건인지 센다(ingest counters 로그에 실린다)."""
    counters = _ingest_one(tmp_path, _TW_CAP, origin=None)
    assert link_new.latest_posted_at(tmp_path / "tw.db") == datetime(2026, 9, 30, tzinfo=UTC)
    assert counters["posted_at_from_date"] == 1, dict(counters)
