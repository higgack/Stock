"""④ OECD 직접(SDMX)·IMF PortWatch 원천 시범 (2026-10-23).

외부 레포(OpenBB) 검토에서 **주소와 응답 형식만** 가져왔다. 픽스처는 OpenBB 의
녹화 응답(tests/record cassettes)과 같은 모양이다 — 열 이름에 라벨이 붙어 오고
(`TIME_PERIOD: Time period`), PortWatch 는 ArcGIS `features[].attributes` 다.
⚠️ 녹화는 과거 시점이라 지금도 같은지는 VM 의 `--check` 가 잰다.
"""

from __future__ import annotations

import json

import pytest

from bot import global_sources as gs

_OECD_CSV = (
    "STRUCTURE,STRUCTURE_ID,STRUCTURE_NAME,ACTION,REF_AREA: Reference area,"
    "FREQ: Frequency of observation,TIME_PERIOD: Time period,OBS_VALUE,"
    "OBS_STATUS: Observation status\n"
    "DATAFLOW,OECD.SDD.STES:DSD_STES@DF_CLI(4.1),Composite leading indicators,I,"
    "KOR: Korea,M: Monthly,2026-07,100.42,A: Normal value\n"
    "DATAFLOW,OECD.SDD.STES:DSD_STES@DF_CLI(4.1),Composite leading indicators,I,"
    "KOR: Korea,M: Monthly,2026-06,100.31,A: Normal value\n"
    "DATAFLOW,OECD.SDD.STES:DSD_STES@DF_CLI(4.1),Composite leading indicators,I,"
    "KOR: Korea,M: Monthly,2026-08,,M: Missing value\n"
)


class _Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code, self.text, self._p = status, text, payload

    def json(self):
        return self._p


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_CACHE_DIR", tmp_path / "gs")
    monkeypatch.setattr(gs, "_FAIL", {})
    calls = []

    def stub(resp):
        def _g(url, *, params=None, headers=None):
            calls.append({"url": url, "params": params, "headers": headers})
            return resp() if callable(resp) else resp
        monkeypatch.setattr(gs, "_get", _g)
    return calls, stub, tmp_path / "gs"


def test_parse_oecd_csv_finds_labelled_columns_and_drops_blank_values():
    assert gs.parse_oecd_csv(_OECD_CSV) == [("2026-06", 100.31), ("2026-07", 100.42)]


def test_parse_oecd_csv_without_the_columns_is_an_error_not_empty():
    with pytest.raises(ValueError):
        gs.parse_oecd_csv("A,B\n1,2\n")
    assert gs.parse_oecd_csv("") == []


def test_oecd_series_builds_the_sdmx_request_and_caches(iso):
    calls, stub, _ = iso
    stub(_Resp(200, _OECD_CSV))
    r = gs.oecd_series("cli", "KOR", start="2018-01")
    assert r["ok"] and r["rows"][-1] == ("2026-07", 100.42)
    c = calls[0]
    assert c["url"].endswith("/OECD.SDD.STES/DSD_STES%40DF_CLI/4.1/KOR.M.LI.*.*.AA.IX.*.H")
    assert c["params"]["c[TIME_PERIOD]"] == "ge:2018-01"
    assert "csv" in c["headers"]["Accept"] and "labels=both" in c["headers"]["Accept"]
    # 두 번째는 디스크 캐시 — 원천을 다시 치지 않는다
    r2 = gs.oecd_series("cli", "KOR", start="2018-01")
    assert r2["rows"] == r["rows"] and len(calls) == 1


def test_old_cache_version_is_not_trusted(iso):
    calls, stub, d = iso
    stub(_Resp(200, _OECD_CSV))
    gs.oecd_series("lt_rate", "JPN")
    p = next(d.glob("*.json"))
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["ver"] = gs._VER - 1
    p.write_text(json.dumps(rec), encoding="utf-8")
    gs.oecd_series("lt_rate", "JPN")
    assert len(calls) == 2


@pytest.mark.parametrize("resp,frag", [
    (_Resp(404, "NoRecordsFound"), "원문: NoRecordsFound"),
    (_Resp(503), "HTTP 503"),
    (_Resp(200, _OECD_CSV.split("\n")[0] + "\n"), "관측값이 없습니다"),
])
def test_oecd_failure_has_a_reason_and_is_remembered_briefly(iso, resp, frag):
    calls, stub, d = iso
    stub(resp)
    r = gs.oecd_series("cli", "USA")
    assert not r["ok"] and frag in r["why"] and r["rows"] == []
    assert not list(d.glob("*.json")) if d.exists() else True   # 실패는 굽지 않는다
    r2 = gs.oecd_series("cli", "USA")
    assert len(calls) == 1 and "(직전 기록)" in r2["why"]


def test_timeout_is_its_own_branch_and_skips_the_rest_of_that_host(iso):
    import requests
    calls, stub, _ = iso

    def boom():
        raise requests.exceptions.ReadTimeout("t")
    stub(boom)
    assert "시간 초과" in gs.oecd_series("cli", "G20")["why"]
    # 같은 호스트의 다음 행은 10초를 또 기다리지 않는다(리뷰 M5)
    r2 = gs.oecd_series("cli", "KOR")
    assert len(calls) == 1 and "같은 원천 직전 실패" in r2["why"]


def test_proxy_error_is_a_connection_failure_not_a_format_error(iso):
    import requests
    calls, stub, _ = iso

    def boom():
        raise requests.exceptions.ProxyError("p")
    stub(boom)
    assert gs.oecd_series("cli", "G20")["why"].startswith("연결 실패(ProxyError)")


def test_format_error_keeps_the_source_message(iso):
    calls, stub, _ = iso
    stub(_Resp(200, payload={"error": {"message": "Invalid query parameters"}}))
    why = gs.chokepoint_daily("chokepoint6")["why"]
    assert "Invalid query parameters" in why and "ValueError" in why
    # 응답이 온 실패는 호스트 전체를 막지 않는다
    gs.chokepoint_daily("chokepoint5")
    assert len(calls) == 2


def test_success_cache_expires_and_failure_memory_expires(iso, monkeypatch):
    calls, stub, _ = iso
    now = [1_000_000.0]
    monkeypatch.setattr(gs.time, "time", lambda: now[0])
    stub(_Resp(200, _OECD_CSV))
    gs.oecd_series("cli", "KOR")
    now[0] += gs._TTL_OK - 1
    gs.oecd_series("cli", "KOR")
    assert len(calls) == 1
    now[0] += 2
    gs.oecd_series("cli", "KOR")
    assert len(calls) == 2
    stub(_Resp(503))
    gs.oecd_series("cli", "USA")
    now[0] += gs._TTL_FAIL - 1
    gs.oecd_series("cli", "USA")
    assert len(calls) == 3
    now[0] += 2
    gs.oecd_series("cli", "USA")
    assert len(calls) == 4


def test_parse_oecd_csv_refuses_two_series_in_one_period():
    extra = _OECD_CSV.replace("2026-07,100.42", "2026-07,100.42\nDATAFLOW,x,y,I,KOR: Korea,M: Monthly,2026-07,99.0,A")
    with pytest.raises(ValueError, match="2026-07"):
        gs.parse_oecd_csv(extra)


_PW = {"features": [
    {"attributes": {"date": "2026-10-20", "portid": "chokepoint6", "n_total": 80,
                    "n_tanker": 40, "n_container": 5, "capacity": 9.1e6}},
    {"attributes": {"date": 1760832000000, "portid": "chokepoint6", "n_total": 70,
                    "n_tanker": None, "n_container": 4, "capacity": 8e6}},
    {"attributes": {"portid": "chokepoint6", "n_total": 1}},
]}


def test_parse_portwatch_reads_string_and_epoch_dates_ascending():
    rows = gs.parse_portwatch(_PW)
    assert [r["date"] for r in rows] == ["2025-10-19", "2026-10-20"]
    assert rows[0]["n_tanker"] is None and rows[1]["n_total"] == 80.0


def test_parse_portwatch_error_payload_raises():
    with pytest.raises(ValueError, match="Invalid query"):
        gs.parse_portwatch({"error": {"message": "Invalid query"}})


def test_chokepoint_daily_asks_newest_first_and_says_when_truncated(iso):
    calls, stub, d = iso
    stub(_Resp(200, payload={**_PW, "exceededTransferLimit": True}))
    r = gs.chokepoint_daily("chokepoint6")
    p = calls[0]["params"]
    # 상한에 걸리면 오름차순은 최신이 잘린다 — 내림차순으로 받는다
    assert p["orderByFields"] == "date DESC"
    assert p["where"].startswith("portid = 'CHOKEPOINT6' AND date >= TIMESTAMP '")
    assert " AND date <= TIMESTAMP '" in p["where"] and " OR " not in p["where"]
    assert r["ok"] and "상한" in r["note"]
    # 잘림 사실은 캐시 히트에서도 남는다
    assert "상한" in gs.chokepoint_daily("chokepoint6")["note"] and len(calls) == 1
    # 캐시 이름에 날짜가 없다 — 날마다 새 파일이 쌓이지 않는다
    assert [x.name for x in d.glob("*.json")] == [f"portwatch_chokepoint6_{gs.PORTWATCH_DAYS}.json"]


def test_rolling_mean_skips_missing_and_drops_partial_window():
    rows = [{"date": f"2026-10-{i:02d}", "n_total": float(i)} for i in range(1, 10)]
    rows[3]["n_total"] = None
    out = gs.rolling_mean(rows, window=3)
    assert out[0] == ("2026-10-03", 2.0)
    assert out[1] == ("2026-10-05", (2 + 3 + 5) / 3)
    assert len(out) == 6


def test_check_bypasses_cache_and_writes_nothing(iso, capsys):
    calls, stub, d = iso
    gs._FAIL["oecd_cli_KOR_2024-01"] = (gs.time.time(), "옛 실패")

    def resp():
        url = calls[-1]["url"]
        return _Resp(200, _OECD_CSV) if "sdmx" in url else _Resp(200, payload=_PW)
    stub(resp)
    assert gs.check() == 0
    out = capsys.readouterr().out
    assert "✅ 전부 응답" in out and "옛 실패" not in out
    assert not d.exists() or not list(d.glob("*.json"))
    # 대상은 보드 행에서 파생(리뷰 M4) — 보드가 쓰는 독일 장기금리를 재고
    # 보드가 안 쓰는 G20 장기금리는 안 잰다, 조회 시작월도 보드와 같다
    from bot.fred_boards import _OECD_START
    assert len(calls) == 9 and "대상: 보드 행 OECD 5 · PortWatch 4" in out
    urls = [c["url"] for c in calls]
    assert any(u.endswith("/DEU.M.IRLT.PA._Z._Z._Z") for u in urls)
    assert not any(u.endswith("/G20.M.IRLT.PA._Z._Z._Z") for u in urls)
    assert all(c["params"]["c[TIME_PERIOD]"] == f"ge:{_OECD_START}"
               for c in calls if "sdmx" in c["url"])


# ── 유동성 보드 배선 ────────────────────────────────────────────────────────
def test_alt_history_maps_oecd_months_and_portwatch_rolling(monkeypatch):
    from bot import fred_boards as fb
    monkeypatch.setattr(gs, "oecd_series", lambda p, a, start: {
        "ok": True, "rows": [("2026-06", 1.0), ("2026-07", 2.0), ("2026-Q3", 9.0)],
        "why": "", "source": ""})
    assert fb._alt_history("oecd:cli:KOR") == [("2026-06-01", 1.0), ("2026-07-01", 2.0)]
    rows = [{"date": f"2026-10-{i:02d}", "n_total": 7.0} for i in range(1, 9)]
    monkeypatch.setattr(gs, "chokepoint_daily", lambda cid: {
        "ok": True, "rows": rows, "why": "", "source": "", "note": ""})
    out = fb._alt_history("pw:chokepoint6")
    assert out[0] == ("2026-10-07", 7.0) and len(out) == 2


def test_load_liq_says_why_an_alt_row_is_missing(monkeypatch):
    """헬퍼만 재면 사유를 버리는 배선 변형을 못 잡는다(#20) — 로더를 통째로."""
    import unittest.mock as mock
    from bot import fred_boards as fb, fred_client
    monkeypatch.setattr(fb, "_ALT_WHY", {})
    monkeypatch.setattr(gs, "oecd_series", lambda p, a, start: {
        "ok": False, "rows": [], "why": "주소 없음(HTTP 404)", "source": ""})
    monkeypatch.setattr(gs, "chokepoint_daily", lambda cid: {
        "ok": False, "rows": [], "why": "응답 시간 초과", "source": "", "note": ""})
    real = fb._alt_history

    def alt(src):
        return real(src) if src.split(":")[0] in ("oecd", "pw") else []
    with mock.patch.object(fred_client, "fetch_history", return_value=[]), \
         mock.patch.object(fb, "_alt_history", side_effect=alt):
        _, derived, _ = fb._load_liq()
    dropped = " | ".join(derived.get("dropped") or [])
    assert "OECD:CLI:KOR) — 데이터 없음(주소 없음(HTTP 404))" in dropped
    assert "PW:HORMUZ) — 데이터 없음(응답 시간 초과)" in dropped


def test_new_rows_have_a_cadence_rule_and_a_known_source():
    from bot.fred_boards_catalog import LIQ_SERIES
    from bot.macro_cadence import CADENCE
    new = [s for s in LIQ_SERIES if str(s.get("src", "")).split(":")[0] in ("oecd", "pw")]
    assert len(new) == 9
    for s in new:
        assert s["id"] in CADENCE, s["id"]
        kind, *rest = s["src"].split(":")
        if kind == "oecd":
            assert rest[0] in gs.OECD_PRESETS and rest[1] in gs.OECD_AREAS
        else:
            assert rest[0] in gs.CHOKEPOINTS
