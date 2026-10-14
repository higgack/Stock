"""수출입 헤더 '📊 현재 잠정 X · 확정 Y' 를 관세청 기준으로(사용자 2026-10-12 —
"발표기준을 채널(텔레그램)에서 관세청으로 바꿔줘. 채널이 더 이상 업데이트를 안
하는 것 같아" · 화면은 `채널 최신 발표 07-22 (82일 전)`)."""
from datetime import date, datetime, timedelta, timezone

from trade import customs_provisional as cp


def _ref(ym="2026-10", decile="D2"):
    return {"ym": ym, "decile": decile, "window": "x"}


def test_confirmed_month_follows_the_15th_rule():
    # 9월분은 10/15 에 확정 — 그 전엔 8월이 확정이다(산업트렌드 라벨과 같은 달력).
    assert cp.confirmed_ym("2026-09", date(2026, 10, 12)) == "2026-08"
    assert cp.confirmed_ym("2026-09", date(2026, 10, 15)) == "2026-09"
    assert cp.confirmed_ym("2026-01", date(2026, 2, 3)) == "2025-12"
    assert cp.confirmed_ym("", date(2026, 10, 12)) == ""


def test_header_says_customs_windows_and_basis():
    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    h = cp.header_status_html(_ref(), "2026-09", now - timedelta(hours=2),
                              today=date(2026, 10, 12), now=now)
    assert "잠정 <strong>10월 1-20일</strong>" in h
    assert "확정 <strong>8월 전체</strong>" in h
    assert "관세청 기준" in h and "채널" not in h
    assert "2시간 전" in h and "⚠️" not in h
    for dec, win in (("D1", "1-10일"), ("FULL", "전체")):
        assert f"잠정 <strong>10월 {win}</strong>" in cp.header_status_html(
            _ref(decile=dec), None, now, now=now)


def test_header_warns_when_openapi_collection_stalls():
    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    h = cp.header_status_html(_ref(), "2026-09", now - timedelta(days=3),
                              today=date(2026, 10, 12), now=now)
    assert "hdr-warn" in h and "⚠️" in h and "새 수집 없음" in h


def test_header_empty_without_customs_data_so_channel_fallback_runs():
    assert cp.header_status_html(None, None, None) == ""
    assert cp.header_status_html({"ym": "2026-10", "decile": "??"}, None, None) == ""
    # 확정만 있어도 그린다(잠정 수집 사실은 주장하지 않는다).
    h = cp.header_status_html(None, "2026-09", None, today=date(2026, 10, 20))
    assert "확정 <strong>9월 전체</strong>" in h and "잠정" not in h


def test_render_puts_customs_line_in_header_and_js_keeps_it(tmp_path):
    """E2E — customs.db 를 실제로 채워 render_html 을 태운다(헬퍼만 재면 배선을
    떼는 변형을 못 잡는다, #20)."""
    import re
    from trade import customs, industry, store
    import trade.dashboard as td
    db = tmp_path / "store.db"
    store.open_db(db).close()
    cdb = tmp_path / "customs.db"
    with customs.session(cdb) as conn:
        cp.store_signal(conn, "exp_item", {"ym": "2026-10", "decile": "D1",
                                           "window": "1~10일", "items": []})
        industry.store(conn, {"반도체": {"2026-08": 10, "2026-09": 12}})
        conn.commit()
    html = td.render_html(db, customs_db_path=cdb)
    m = re.search(r'<div class="meta meta-status" id="meta-status"([^>]*)>(.*?)</div>',
                  html, re.S)
    assert m, "상태줄 div 없음"
    assert 'data-basis="customs"' in m.group(1)
    # 감사 ③ 은 data-src 를 lazy 파일로 대조한다 — 렌더된 페이지의 참조가 전부
    # 파일 이름이어야 'customs 미존재(nav/lazy 404)' 거짓 ❌ 가 안 난다
    # (2026-10-14 일일 감사, 실수 #451). 감사의 정규식을 그대로 태운다(#35).
    from trade.scripts import dashboard_audit as da
    refs = {x or y for x, y in da._REF_RE.findall(html) if (x or y)}
    assert all("." in r for r in refs), refs   # 이 픽스처는 lazy 없음 = 빈 집합
    assert "잠정 <strong>10월 1-10일</strong>" in m.group(2)
    assert "관세청 기준" in m.group(2)
    # JS 가 서버 줄을 채널 줄로 덮지 않는다
    i = html.index("function renderHeaderMeta(")
    body = html[i:i + 4000]
    assert "fromCustoms" in body and "if(!fromCustoms)ms.innerHTML" in body


def test_render_without_customs_db_keeps_channel_fallback(tmp_path):
    from trade import store
    import trade.dashboard as td
    db = tmp_path / "store.db"
    store.open_db(db).close()
    html = td.render_html(db, customs_db_path=tmp_path / "none.db")
    assert '<div class="meta meta-status" id="meta-status"></div>' in html


def test_confirmed_month_never_runs_ahead_of_the_calendar():
    """독립 리뷰 Medium — 저장분에 당월 부분 행이 들어와 최신월이 '10월' 이어도
    10/11 엔 9월이 아직 확정 전이다(9월분은 10/15 확정)."""
    assert cp.confirmed_ym("2026-10", date(2026, 10, 11)) == "2026-08"
    assert cp.confirmed_ym("2026-10", date(2026, 10, 15)) == "2026-09"
    assert cp.expected_confirmed_ym(date(2026, 10, 16)) == "2026-08"   # 여유 2일
    assert cp.expected_confirmed_ym(date(2026, 10, 17)) == "2026-09"


def test_header_warns_when_monthly_confirmed_stalls():
    """확정 월이 달력보다 낡으면(월간 수집 정지) 말한다 — 채널 줄이 82일 동안 말없이
    낡았던 그 실패를 관세청 줄에서 되풀이하지 않는다(#52)."""
    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    h = cp.header_status_html(_ref(), "2026-07", now, today=date(2026, 10, 12), now=now)
    assert "확정 <strong>7월 전체</strong>" in h
    assert "hdr-warn" in h and "2026-08 까지 확정됐어야" in h
    ok = cp.header_status_html(_ref(), "2026-09", now, today=date(2026, 10, 12), now=now)
    assert "hdr-warn" not in ok


def test_stale_fetch_warning_has_a_single_sign():
    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    h = cp.header_status_html(_ref(), "2026-09", now - timedelta(days=3),
                              today=date(2026, 10, 12), now=now)
    assert h.count("⚠️") == 1
