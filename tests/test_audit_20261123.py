"""2026-11-23 일일 감사 ❌ 7건 — 감사 쪽 오보와 `/earnings` 느림.

- FCF ① 4건(BDX·0162D0.KQ·2914.T·3443.TW): `.info` 빈 응답으로 스냅샷이 None.
  한 번 더 묻고, 그래도 비면 판정 불가(❓) · 다수가 비는 날만 원천 차단 ❌ (#21·#260).
- FCF ② 0162D0.KQ: yfinance 표가 없어 생긴 같은 결측을 '기간 키' ❌ 로 또 셈(#289).
- PER 밴드 KLAC: 화면에 남은 급변을 "그 이전 구간은 뺐습니다" 라고 적음(#169·#55).
- `/earnings`: 한국 화면이 미국 Finnhub 한 달치를 동기로 받았다 버림 + 단계 계측(#69).
감사·화면 함수를 통째로 태운다(#20).
"""


def _fa_run(monkeypatch, snaps, tk="AAPL"):
    import bot.scripts.fcf_audit as fa
    import bot.stock_snapshot as ss
    seq, sleeps = list(snaps), []
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(fa._time, "sleep", lambda s: sleeps.append(s))
    return fa, fa.audit_one(tk, None, years=1), sleeps


def test_empty_snapshot_retries_once_then_unknown(monkeypatch):
    fa, r, sleeps = _fa_run(monkeypatch, [None, None])
    assert sleeps == [fa._SNAP_RETRY_SEC]
    assert r["snap_empty"] is True
    assert "①재계산(yf)" in r["unknown_axes"] and "①재계산(yf)" not in r["bad_axes"]
    line = next(l for l in r["lines"] if "① 재계산" in l)
    assert "❓" in line and "빈 응답" in line, line


def test_retry_that_answers_is_used(monkeypatch):
    _fa, r, sleeps = _fa_run(monkeypatch, [None, {"current_price": 5.0}])
    assert len(sleeps) == 1 and r["snap_empty"] is False
    line = next(l for l in r["lines"] if "① 재계산" in l)
    assert "재무제표를 아직 주지 않음" in line, line


def test_answered_snapshot_is_not_retried(monkeypatch):
    _fa, r, sleeps = _fa_run(monkeypatch, [{"current_price": 5.0}])
    assert sleeps == [] and r["snap_empty"] is False


def test_dict_without_price_or_tables_stays_bad_and_names_keys(monkeypatch):
    _fa, r, _s = _fa_run(monkeypatch, [{"name": "X"}])
    assert "①재계산(yf)" in r["bad_axes"], r["lines"]
    line = next(l for l in r["lines"] if "① 재계산" in l)
    assert "받은 칸: name" in line, line


def test_block_line_only_when_most_tickers_empty():
    from bot.scripts.fcf_audit import snapshot_block_line as f
    assert f(["A"], 1) == ""                       # 한 종목은 판정 불가로 충분
    assert f(["A", "B"], 4) == ""                  # 절반은 아직 아니다
    line = f(["A", "B", "C"], 4)
    assert line.startswith("[yfinance] ❌") and "3/4종목" in line and "A, B, C" in line


def test_main_counts_block_line(monkeypatch, capsys):
    import bot.scripts.fcf_audit as fa
    monkeypatch.setattr(fa, "audit_one", lambda tk, d, years=3: {
        "lines": [f"── {tk}"], "bad": 0, "unknown": 1, "bad_axes": [],
        "unknown_axes": ["①재계산(yf)"], "snap_empty": True})
    monkeypatch.setattr(fa.sys, "argv", ["fcf_audit", "AAA", "BBB", "CCC"])
    rc = fa.main()
    out = capsys.readouterr().out
    assert "[yfinance] ❌ 스냅샷 빈 응답 3/3종목" in out and rc == 2


def test_cross_source_without_yf_quarters_is_unknown(monkeypatch):
    import bot.dart_client as dc
    import bot.dart_quarterly as dq
    monkeypatch.setattr(dc, "dart_ready", lambda d: True)
    monkeypatch.setattr(dq, "get_quarterly_series", lambda *a, **k: [
        {"label": "26.3Q", "year": 2026, "quarter": 3, "financials": {"FCF": 1e9}}])

    class _D:
        def get_normalized_financials(self, *a, **k):
            return {}
    import bot.scripts.fcf_audit as fa
    import bot.stock_snapshot as ss
    monkeypatch.setattr(ss, "collect_stock_snapshot",
                        lambda *a, **k: {"current_price": 9.0})
    r = fa.audit_one("0162D0.KQ", _D(), years=1)
    assert "②교차출처(분기)" in r["unknown_axes"], r["lines"]
    assert "②교차출처(분기)" not in r["bad_axes"], r["lines"]
    assert any("대조 상대 없음" in l and "DART 분기 1개" in l for l in r["lines"])


def test_klac_line_reports_what_screen_kept():
    from bot.scripts.per_band_audit import screen_eps_jump
    rows = [{"period": "2022-06-30", "eps": 20.0}, {"period": "2023-06-30", "eps": 25.41},
            {"period": "2024-06-30", "eps": 2.13}, {"period": "2025-06-30", "eps": 2.4}]
    why = screen_eps_jump(rows, "EPS", "edgar-a")
    assert "25.41 → 2024-06-30 2.13(11.9배)" in why and "edgar-a" in why, why
    assert "뺐습니다" not in why                      # 화면은 아무것도 안 뺐다
    assert screen_eps_jump(rows[:2], "EPS") is None


def test_kr_earnings_page_does_not_fetch_us(monkeypatch):
    import bot.earnings_calendar as ec
    called = []
    monkeypatch.setattr(ec, "us_month", lambda *a: called.append(a) or ([], ""))
    monkeypatch.setattr(ec, "kr_month", lambda y, m: [
        {"symbol": "005930.KS", "name": "삼성", "date": f"{y:04d}-{m:02d}-10",
         "hour": "", "market": "kr", "url": "#", "ir_type": "IR"}])
    html = ec.render_page(2026, 11, "kr")
    assert called == [], "한국 화면이 미국 원천을 불렀다"
    assert "<!-- render-timing " in html and "events:kr=" in html and "total=" in html
    # 형제: 두 시장을 같이 원하는 호출부는 여전히 둘 다 받는다
    ec.fetch_month(2026, 11)
    assert called == [(2026, 11)]


def test_timing_comment_cannot_break_html_comment():
    from bot.earnings_calendar import render_timing_comment as f
    c = f([("a--b", 1.25), ("c", 0.5)])
    assert c.count("--") == 2 and "a__b=1.2s" in c and "total=1.8s" in c, c


def test_slow_warning_carries_timing():
    from bot.scripts.page_health_audit import live_pages
    calls = []

    def get(url, auth, timeout=25):
        calls.append(timeout)
        if len(calls) == 1:
            return None, "ReadTimeout"
        return 200, "<html><!-- render-timing names=0.1s events:kr=0.2s total=0.3s --></html>"
    pages, probs, why = live_pages(["earnings"], get=get, base_auth=("http://x/", None))
    assert why == "" and len(pages) == 1
    t = probs[0][1]["text"]
    assert "25초 안에 안 열림" in t and "events:kr=0.2s" in t and "첫 요청" in t, t


def test_klac_wiring_through_audit_rows():
    from bot.scripts.per_band_audit import audit_rows
    rows = [{"period": p, "eps": e, "price": e * 20, "per": 20.0}
            for p, e in (("2022-06-30", 20.0), ("2023-06-30", 25.41),
                         ("2024-06-30", 2.13), ("2025-06-30", 2.4))]
    ax = {a: (m, d) for m, a, d in audit_rows({"rows": rows, "basis": "edgar-a"})}
    m, d = ax["분할"]
    assert m == "❌" and "그대로 실렸다" in d and "edgar-a" in d, d


def test_lookup_detail_failure_says_reason(monkeypatch):
    """기본 정보 실패 문구가 사유와 '서버가 물은 결과' 를 말한다(2026-11-23 499790.KS)."""
    import bot.stock_snapshot as ss
    import bot.dashboard as d

    def _fail(t, **k):
        ss._LAST_FAIL[t] = "야후 종목 정보(.info)가 비어 왔습니다 — 시험"
        return None

    monkeypatch.setattr(ss, "collect_stock_snapshot", _fail)
    monkeypatch.setattr(d, "_load_stored_stock_info", lambda t: None)
    h = d.render_lookup_detail("0162D0.KQ")
    assert "사유: 야후 종목 정보(.info)가 비어 왔습니다" in h
    assert "인터넷과 무관" in h
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda t, **k: None)
    ss._LAST_FAIL.pop("ZZZ.KS", None)
    assert "사유:" not in d.render_lookup_detail("ZZZ.KS")
