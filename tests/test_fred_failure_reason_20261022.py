"""FRED 가 값을 못 줄 때 화면이 **왜** 못 받았는지 말한다(2026-10-22, 사용자 "FED 쪽 데이터
받아오는거 갑자기 문제생긴것 같은데").

매크로 카드 9장·핵심 지표 10행이 한꺼번에 빠졌는데 화면은 'FRED 관측을 못 받았습니다' /
'—' 뿐이었다 — 키가 없는지·키가 거부됐는지·한도인지·원천 장애인지·연결 실패인지를 가를 수
없었다(#82 처방이 갈래마다 다르다). 샌드박스는 FRED 에 못 닿아 원인을 재지 못했으므로 단정하지
않고(#12), 갈래를 화면·로그·`python -m bot.fred_client --check` 가 말하게 했다(#252).

네트워크 0 — `requests.get` 경계에서 스텁한다.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import date

import pytest
import requests

import bot.fred_client as fc


class _R:
    def __init__(self, status: int, body: dict | None = None, obs: list | None = None):
        self.status_code = status
        self._body = body if body is not None else {"observations": obs or []}
        self.text = str(self._body)
        self.content = self.text.encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            e = requests.HTTPError(f"{self.status_code} Client Error")
            e.response = self
            raise e


def _http_err(status: int, msg: str) -> requests.HTTPError:
    try:
        _R(status, {"error_code": status, "error_message": msg}).raise_for_status()
    except requests.HTTPError as e:
        return e
    raise AssertionError


_BAD_KEY = ("Bad Request.  The value for variable api_key is not registered.  "
            "Read https://fred.stlouisfed.org/docs/api/api_key.html")


# ── 순수 판정 ─────────────────────────────────────────────────────────────
def test_each_failure_branch_names_its_own_remedy():
    r = fc.failure_reason(_http_err(400, _BAD_KEY))
    assert "API 키를 거부" in r and "HTTP 400" in r and "not registered" in r
    assert "한도" in fc.failure_reason(_http_err(429, "Too Many Requests"))
    assert "서버 오류" in fc.failure_reason(_http_err(503, "Service Unavailable"))
    other = fc.failure_reason(_http_err(400, "Bad Request. Variable series_id is not set."))
    assert "거절" in other and "API 키" not in other        # 키 문제로 오보하지 않는다
    for st in (401, 403):
        assert "API 키를 거부" in fc.failure_reason(_http_err(st, "Invalid api_key")), st
    assert "연결하지 못했" in fc.failure_reason(requests.exceptions.ProxyError("p"))
    assert "연결하지 못했" in fc.failure_reason(requests.exceptions.SSLError("s"))
    assert "시간 초과" in fc.failure_reason(requests.exceptions.ReadTimeout("t"))
    assert "연결하지 못했" in fc.failure_reason(requests.exceptions.ConnectionError("c"))
    assert "FRED_API_KEY" in fc.failure_reason(no_key=True)
    assert "관측이 없" in fc.failure_reason(empty=True)
    assert fc.failure_reason() == ""
    # 다섯 갈래의 문장이 서로 다르다(한 문구가 두 처방을 대표하지 않는다)
    rs = {fc.failure_reason(_http_err(400, _BAD_KEY)), fc.failure_reason(_http_err(429, "")),
          fc.failure_reason(_http_err(500, "")), fc.failure_reason(requests.exceptions.ReadTimeout()),
          fc.failure_reason(requests.exceptions.ConnectionError()), fc.failure_reason(no_key=True)}
    assert len(rs) == 6


def test_the_key_never_reaches_the_reason():
    """사유는 화면에 실린다 — 원천 본문·예외 문구에 키가 되읊어져도 가린다(§Secrets)."""
    leak = "https://api.stlouisfed.org/fred/series/observations?series_id=X&api_key=abcd0123ef&x=1"
    r1 = fc.failure_reason(_http_err(500, f"error at {leak}"))
    r2 = fc.failure_reason(ValueError(f"bad {leak}"))
    for r in (r1, r2):
        assert "abcd0123ef" not in r and "api_key=***" in r, r


# ── 배선 — 헤드라인 조회가 갈래를 남기고, 성공하면 지운다 ───────────────────────
def _mo(tmp_path, monkeypatch):
    import bot.market_overview as mo
    monkeypatch.setenv("FRED_API_KEY", "x" * 8)
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path / "mo")
    monkeypatch.setattr(mo, "_fred_fail", {})
    monkeypatch.setattr(mo, "_fred_why", {})
    return mo


@pytest.mark.parametrize("fn_name, sid", [("_fred_fetch_series", "UNRATE"),
                                          ("_fetch_fred_yoy", "CPIAUCSL")])
def test_headline_and_yoy_record_the_branch_then_clear_on_success(tmp_path, monkeypatch,
                                                                  fn_name, sid):
    mo = _mo(tmp_path, monkeypatch)
    fn = getattr(mo, fn_name)
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: _R(400, {
        "error_code": 400, "error_message": _BAD_KEY}))
    key = sid if fn_name == "_fred_fetch_series" else mo._yoy_key(sid)
    assert (fn(sid, 400) if fn_name == "_fred_fetch_series" else fn(sid)) is None
    assert "API 키를 거부" in mo._fred_why[key]
    # 성공하면 지운다 — 옛 사유가 다음 실패 전까지 남아 화면을 속이지 않는다
    mo._fred_fail.clear()
    obs = [{"date": f"{y}-{m:02d}-01", "value": str(100 + i)}
           for i, (y, m) in enumerate((y, m) for y in (2026, 2025, 2024) for m in range(12, 0, -1))]
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: _R(200, obs=obs))
    assert (fn(sid, 400) if fn_name == "_fred_fetch_series" else fn(sid)) is not None
    assert key not in mo._fred_why


def test_missing_key_is_a_named_branch(tmp_path, monkeypatch):
    mo = _mo(tmp_path, monkeypatch)
    monkeypatch.setattr("bot.env_keys.env_key", lambda name: "")
    assert mo._fred_fetch_series("DGS10", 400) is None
    assert "FRED_API_KEY" in mo._fred_why["DGS10"]
    assert mo._fetch_fred_yoy("CPIAUCSL") is None
    assert "FRED_API_KEY" in mo._fred_why[mo._yoy_key("CPIAUCSL")]


def test_yoy_empty_answer_is_a_named_branch(tmp_path, monkeypatch):
    mo = _mo(tmp_path, monkeypatch)
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: _R(200, obs=[]))
    assert mo._fetch_fred_yoy("CPIAUCSL") is None
    assert "관측이 없" in mo._fred_why[mo._yoy_key("CPIAUCSL")]


def test_level_success_does_not_erase_the_yoy_reason(tmp_path, monkeypatch):
    """CPIAUCSL 은 YoY 행(글로벌)과 수준 경로(매크로 카드)가 같이 묻는다 — 수준 경로의 성공이
    YoY 행의 실패 사유를 지우면 그 행이 사유 없는 '—' 가 된다(독립 리뷰 L2)."""
    mo = _mo(tmp_path, monkeypatch)
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: _R(503, {
        "error_message": "Service Unavailable"}))
    assert mo._fetch_fred_yoy("CPIAUCSL") is None
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: _R(200, obs=[
        {"date": "2026-09-01", "value": "320"}, {"date": "2026-08-01", "value": "319"}]))
    assert mo._fred_fetch_series("CPIAUCSL", 400) is not None
    rows = {r["series_id"]: r for r in mo._fetch_all_fred()}   # YoY 는 10분 기억 → 사본 없음
    assert "서버 오류" in rows["CPIAUCSL"]["why"], rows["CPIAUCSL"]


# ── 화면 둘이 같은 사유를 말한다(#38) ─────────────────────────────────────────
def test_core_indicator_table_says_why_rows_are_empty(tmp_path, monkeypatch):
    mo = _mo(tmp_path, monkeypatch)
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: _R(429, {
        "error_code": 429, "error_message": "Too Many Requests.  Exceeded Rate Limit"}))
    rows = mo._fetch_all_fred()
    assert rows and all(r["data"] is None for r in rows)
    assert all("한도" in r["why"] for r in rows)
    from bot.dashboard import _render_fred_card
    html = _render_fred_card(rows, None)
    assert f"FRED {len(rows)}행을 못 받았습니다" in html and "요청 한도 초과" in html
    # 다 받은 날은 아무 말도 안 한다(늘 뜨는 경고는 아무것도 안 잰다, #25)
    ok = [{"label": "US 미국채 10년", "unit": "%", "series_id": "DGS10",
           "data": {"value": 4.1, "time": "2026-10-20", "change": 0.01}}]
    assert "못 받았습니다" not in _render_fred_card(ok, None)


def test_macro_dropped_note_carries_the_branch(tmp_path, monkeypatch):
    """매크로 카드 '값을 못 받아 N장을 뺐습니다 … — FRED 관측을 못 받았습니다' 뒤에 갈래."""
    import bot.market_overview as mo
    import bot.macro_snapshot as ms
    import bot.naver_marketindex as nm
    monkeypatch.setenv("FRED_API_KEY", "x" * 8)
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "snap")
    (tmp_path / "snap").mkdir()
    monkeypatch.setattr(ms, "_fetch_macro_naver_values", lambda sids: {})
    monkeypatch.setattr(ms, "_yf_monthly_batch", lambda tk: {})
    monkeypatch.setattr(ms, "_yf_daily_1mo_batch", lambda tk: {})
    for fn in ("fetch_commodity_spark", "fetch_naver_index_history",
               "fetch_naver_crypto_history", "fetch_naver_fx_history"):
        monkeypatch.setattr(nm, fn, lambda *a, **k: [])
    monkeypatch.setattr(ms, "_customs_series", lambda key: {"points": [], "src": ""})
    monkeypatch.setattr(ms, "_ecos_series", lambda key: [])
    monkeypatch.setattr(ms.requests, "get", lambda *a, **k: _R(400, {
        "error_code": 400, "error_message": _BAD_KEY}))
    why: dict = {}
    monkeypatch.setattr(mo, "_fred_why", why)

    def spot(sid, lb):                       # 헤드라인 경계 — 실패하며 갈래를 남긴다
        why[sid] = fc.failure_reason(_http_err(400, _BAD_KEY))
        return None
    monkeypatch.setattr(mo, "_fred_fetch_series", spot)
    out = ms.fetch_macro_snapshot()
    fred = [d for d in out["dropped"] if d["why"].startswith("FRED")]
    assert fred, out["dropped"]
    for d in fred:
        assert d["why"].startswith("FRED 관측을 못 받았습니다: FRED 가 API 키를 거부했습니다"), d
    # 이 픽스처는 모든 원천을 비웠다 — 가장 많은 갈래가 각주에 장 수와 함께 실린다
    assert "장: " in (out.get("dropped_note") or "")


def test_dropped_note_names_the_largest_branch_when_reasons_differ():
    """옛 판은 사유가 둘 이상이면 아무 사유도 안 적었다 — FRED 9장의 사유가 다른 카드 하나에
    묻힌다. 가장 많은 갈래를 장 수와 함께 적는다."""
    import bot.macro_snapshot as ms
    d = [{"label": f"F{i}", "why": "FRED: 키 거부"} for i in range(3)] + [
        {"label": "N", "why": "네이버 실패"}]
    note = ms.dropped_note(d)
    assert "— 3장: FRED: 키 거부 (그 밖 사유 1갈래)" in note, note
    assert ms.dropped_note(d[:3]).endswith("— FRED: 키 거부")      # 한 갈래면 옛 문구 그대로


def test_check_cli_reports_ok_and_failure(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "y" * 32)
    monkeypatch.setattr(fc.requests, "get", lambda *a, **k: _R(200, obs=[
        {"date": "2026-10-20", "value": "4.12"}]))
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert fc.check() == 0
    assert "✅ FRED 응답 정상 — DGS10 2026-10-20 = 4.12" in buf.getvalue()
    monkeypatch.setattr(fc.requests, "get", lambda *a, **k: _R(400, {
        "error_code": 400, "error_message": _BAD_KEY}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert fc.check() == 1
    out = buf.getvalue()
    assert "❌ FRED 가 API 키를 거부" in out and "y" * 32 not in out
