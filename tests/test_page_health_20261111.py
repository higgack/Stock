"""전 대시보드 화면 경고 감사(사용자 2026-11-11 "왜 검증에서 안 잡혔어? 모든
대시보드에 검증을 달아 텔레그램 알람을").

한국 업종 등락이 엿새째 저장분인데 결산은 조용했다 — 그 위젯을 보는 감사가 없었고
`⚠️` 는 며칠을 이어져도 결산에 안 오른다. 화면이 스스로 적은 문구를 전 화면에서
읽어, 오래 가면 ❌ 로 올린다.
"""
from __future__ import annotations

import pytest

from bot import page_health as ph
from bot.scripts import page_health_audit as pha

# 2026-11-11 사용자 캡처의 두 위젯(문구 그대로)
_KR = ('<div class="section-hd"><h2>KR 한국 업종 등락 TOP 10</h2>'
       '<span class="ts">저장분 11-05 15:30 (6일 전) ⚠️ · Naver</span></div>'
       '<div class="sm-note">⚠️ 자동 탐색에서도 새 주소를 찾지 못했습니다(6시간 뒤 다시 탐색)'
       ' · 원천 주소가 없습니다</div>')
_US = ('<div class="section-hd"><h2>US 미국 업종 등락 TOP 10</h2>'
       '<span class="ts">2026-11-11 21:35 · yfinance</span></div>'
       '<div class="sm-note">⚠️ 네이버 업종(미국)·Finviz 업종을(를) 못 받아 섹터 ETF(yfinance)로'
       ' 대체했습니다 · 원천 주소가 없습니다</div>')
_PAGE = "<!doctype html><html><body>" + _KR + _US + "</body></html>"


def _kinds(html):
    return [(f["kind"], f["where"]) for f in ph.findings(html)]


def test_the_reported_screen_is_read_as_three_findings():
    assert _kinds(_PAGE) == [("stale", "KR 한국 업종 등락 TOP 10"),
                             ("warn", "KR 한국 업종 등락 TOP 10"),
                             ("warn", "US 미국 업종 등락 TOP 10")]
    assert ph.findings(_PAGE)[0]["age_h"] == 144


def test_six_day_old_saved_copy_alerts_the_first_day():
    f = ph.findings(_PAGE)[0]
    assert ph.judge(f, 0)[0] == "fail"
    # 매일 같은 알림을 반복하지 않는다 — 7일 뒤에 한 번 더(#260)
    assert [ph.judge(f, d)[0] for d in (1, 6, 7, 8)] == ["warn", "warn", "fail", "warn"]


def test_a_fresh_saved_copy_is_not_an_alert():
    f = ph.findings('<div>저장분 11-09 15:30 (2일 전) ⚠️</div>')[0]   # 주말 간격
    assert f["age_h"] == 48 and ph.judge(f, 0)[0] == "warn"
    f = ph.findings('<div>💾 저장분(28시간 전) · 실시간 아님</div>')[0]
    assert f["age_h"] == 28 and ph.judge(f, 5)[0] == "warn"


def test_a_warning_that_stays_escalates_on_the_fourth_day():
    f = ph.findings(_PAGE)[2]
    assert [ph.judge(f, d)[0] for d in (0, 1, 2, 3, 4, 10)] == \
        ["warn", "warn", "warn", "fail", "warn", "fail"]


def test_a_blank_widget_escalates_the_next_day():
    f = ph.findings('<div class="empty-msg">⚠️ 지금은 표시할 수 없습니다 — 원천 주소가 없습니다</div>')[0]
    assert f["kind"] == "blocked"
    assert [ph.judge(f, d)[0] for d in (0, 1, 2)] == ["warn", "fail", "warn"]


def test_guides_scripts_and_long_prose_are_not_findings():
    html = ('<details class="guide"><summary>ℹ️ 읽는 법</summary><p>⚠️ 저장분 (9일 전) 이란…</p></details>'
            '<details class="chart-guide"><p>⚠️ 고정 설명</p></details>'
            '<script>var s="⚠️ 표시할 수 없습니다";</script><style>.x{}</style>'
            '<p>⚠️ ' + "긴 설명문 " * 60 + '</p><p>값 ⚠️ 가운데</p>')
    assert ph.findings(html) == []


def test_key_ignores_numbers_that_change_daily():
    a = ph.findings('<div>저장분 11-05 15:30 (6일 전) ⚠️</div>')[0]
    b = ph.findings('<div>저장분 11-05 15:30 (7일 전) ⚠️</div>')[0]
    assert ph.state_key("market.html", a) == ph.state_key("market.html", b)
    assert ph.state_key("market.html", a) != ph.state_key("asia.html", a)


def test_state_counts_days_and_forgets_what_went_away():
    dd = pha._day_diff
    s = ph.roll_state({}, ["a", "b"], "2026-11-11", dd)
    s = ph.roll_state(s, ["a"], "2026-11-12", dd)
    assert s["a"] == {"first": "2026-11-11", "last": "2026-11-12"}
    assert "b" in s                                     # 하루 빠진 것은 같은 사건
    s = ph.roll_state(s, ["a"], "2026-11-14", dd)
    assert "b" not in s                                 # 사흘 안 보이면 잊는다
    s = ph.roll_state(s, ["a", "b"], "2026-11-15", dd)
    assert s["b"]["first"] == "2026-11-15" and s["a"]["first"] == "2026-11-11"


def test_live_routes_come_from_the_pages_own_links():
    html = ('<a href="./theme">t</a><a href="usindustry">u</a><a href="trade/">x</a>'
            '<a href="market.html">m</a><a href="https://ex.com/a">e</a>'
            '<a href="api/build">a</a><a href="lookup/005930">l</a><a href="#top">h</a>'
            '<a href="../etc">up</a><a href="theme">dup</a>')
    assert pha.live_routes([("market.html", html)]) == ["theme", "usindustry", "trade/"]


def test_live_pages_reads_through_the_server_and_names_broken_ones():
    def get(url, auth):
        if url.endswith("/theme"):
            return 200, _PAGE
        if url.endswith("/boom"):
            return 500, "x"
        return None, "ReadTimeout"
    pages, probs, why = pha.live_pages(["theme", "boom", "slow"], get=get,
                                       base_auth=("http://h/", None))
    assert [n for n, _ in pages] == ["/theme"] and why == ""
    assert [(n, f["kind"]) for n, f in probs] == [("/boom", "blocked"), ("/slow", "blocked")]
    assert "HTTP 500" in probs[0][1]["text"]


def test_unreachable_or_locked_server_is_said_once_not_per_page():
    pages, probs, why = pha.live_pages(["a", "b"], get=lambda u, a: (None, "ConnectionError"),
                                       base_auth=("http://h/", None))
    assert pages == [] and probs == [] and "못 닿음" in why
    pages, probs, why = pha.live_pages(["a", "b"], get=lambda u, a: (401, ""),
                                       base_auth=("http://h/", None))
    assert probs == [] and "인증" in why


@pytest.fixture
def arch(tmp_path, monkeypatch):
    import bot.archive as ar
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(ar, "ARCHIVE_ROOT", root)
    monkeypatch.setattr(pha, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(pha, "live_pages", lambda routes, **k: ([], [], ""))
    return root


def _run(capsys, monkeypatch, day):
    import datetime as _dt

    class _D(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.fromisoformat(day + "T08:00:00").replace(tzinfo=tz)
    monkeypatch.setattr(pha, "datetime", _D)
    rc = pha.main([])
    return rc, capsys.readouterr().out


def test_main_alerts_on_the_stale_widget_and_escalates_the_warning(arch, capsys, monkeypatch):
    (arch / "market.html").write_text(_PAGE, encoding="utf-8")
    (arch / "frag.html").write_text("<div>⚠️ 조각</div>", encoding="utf-8")   # 조각은 페이지가 아니다
    rc, out = _run(capsys, monkeypatch, "2026-11-11")
    fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
    assert rc == 1 and len(fails) == 1, out
    # 결산에 오르는 그 한 줄이 화면·위젯·사유·원문을 다 싣는다(#356)
    assert "market.html › KR 한국 업종 등락 TOP 10" in fails[0]
    assert "수집이 멈췄습니다" in fails[0] and "6일 전" in fails[0]
    assert "조각" not in out
    # 이튿날 — 같은 저장분은 다시 알리지 않고, 미국 폴백 경고는 나흘째에 오른다
    rc, out = _run(capsys, monkeypatch, "2026-11-12")
    assert rc == 0 and "❌" not in out
    _run(capsys, monkeypatch, "2026-11-13")
    rc, out = _run(capsys, monkeypatch, "2026-11-14")
    fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
    assert rc == 1 and len(fails) == 2 and any("US 미국 업종" in x for x in fails), out


def test_main_says_it_cannot_judge_when_no_page_was_read(arch, capsys, monkeypatch):
    rc, out = _run(capsys, monkeypatch, "2026-11-11")
    assert rc == 0 and "❓" in out and "판정 불가" in out


def test_clean_pages_say_so_without_verdict_glyphs_in_the_summary(arch, capsys, monkeypatch):
    (arch / "market.html").write_text("<!doctype html><h2>ok</h2><div>값</div>", encoding="utf-8")
    rc, out = _run(capsys, monkeypatch, "2026-11-11")
    assert rc == 0 and "❌" not in out and "⚠" not in out and "문구 없음" in out


def test_the_sweep_runs_this_audit_daily_and_reports_its_line(arch, monkeypatch):
    """배선 — 결산이 이 감사를 돌려 ❌ 줄을 텔레그램 본문에 싣는다(#20)."""
    import bot.audit_sweep as sw
    reg = [a for a in sw.AUDITS if a[1] == "bot.scripts.page_health_audit"]
    assert reg and reg[0][2] == "daily"
    (arch / "market.html").write_text(_PAGE, encoding="utf-8")
    monkeypatch.setattr(sw, "AUDITS", tuple(reg))
    body = sw.report_text(None, False)
    assert "KR 한국 업종 등락 TOP 10" in body and "수집이 멈췄습니다" in body


def test_main_reports_a_server_rendered_page_that_is_broken(arch, capsys, monkeypatch):
    """서버가 그리는 화면(테마·업종 …)이 500 이면 그 화면 이름으로 오른다."""
    (arch / "market.html").write_text('<!doctype html><a href="theme">t</a>', encoding="utf-8")
    seen = {}

    def _live(routes, **k):
        seen["routes"] = list(routes)
        return ([("/usindustry", "<!doctype html>" + _KR)],
                [("/theme", {"kind": "blocked", "where": "", "age_h": None,
                             "text": "화면이 HTTP 500 를 냅니다"})], "")
    monkeypatch.setattr(pha, "live_pages", _live)
    _run(capsys, monkeypatch, "2026-11-11")
    rc, out = _run(capsys, monkeypatch, "2026-11-12")
    assert seen["routes"] == ["theme"]
    fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
    assert rc == 1 and any(ln.startswith("❌ /theme: ") and "HTTP 500" in ln for ln in fails), out
    assert "/usindustry › KR 한국 업종 등락 TOP 10" in out      # 서버 화면의 문구도 읽는다
