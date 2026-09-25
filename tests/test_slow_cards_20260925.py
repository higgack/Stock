"""'느린 카드' 5종 점검(실수 #415, 사용자 2026-09-25 "캡쳐한 것들이 느린것들인데..이것들은
어떨까?") — 경상수지·외환보유액·한국 GDP·미국 근원PCE·미국 GDP.

다섯 다 공표 규약(`macro_cadence`) 기준 **뒤처지지 않았다**(오늘 판정 뒤짐 0). 느려 보인
원인은 셋이었다:
  1. 미국 GDP 가 분기인데 월로 적혔다 — FRED 는 분기 관측을 **분기 첫날**(2026-04-01)로
     찍어 오는데 월로 잘라 '기준 2026-04 (5개월 전)' · '2023-07 대비' 였다. 같은 화면의
     한국 GDP 는 '2026 Q2 (3개월 전)' 이다(#38 같은 계산을 두 화면이 다르게).
  2. 분기 카드의 칩이 '12개월' 이었다 — 12점은 12**분기**(3년)다.
  3. FRED 월간·분기 헤드라인 캐시가 24시간이었다 — 같은 카드의 스파크는 30초마다 새로
     받으므로 공표일엔 그래프가 새 달을 그리는데 값·기준 라벨은 최대 하루 옛 달이었다.
그리고 반복되는 질문("원천이 늦게 싣나, 우리가 늦게 받나")은 `macro_staleness_audit
--history` 가 캐시 기록으로 답한다(#252 반복 확인은 제품에 심는다).

네트워크 0 — FRED 는 `requests.get` 경계에서, ECOS 는 `_ecos_series` 에서 스텁한다.
날짜는 시계에서 파생하거나 고정한다(#249).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

import pytest

import bot.macro_snapshot as ms

_GDP = "A191RL1Q225SBEA"


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────
def test_as_quarter_reads_fred_quarter_start_dates():
    assert ms._as_quarter("2026-04-01") == "2026Q2"
    assert ms._as_quarter("2023-07-01") == "2023Q3"
    assert ms._as_quarter("2026-01-01") == "2026Q1" and ms._as_quarter("2026-12-01") == "2026Q4"
    assert ms._as_quarter("2026-04") == "2026Q2"
    # 못 읽으면 원문 그대로 — 판독 불가를 지어내지 않는다
    for raw in ("", "2026Q2", "2026-13-01", "abcd-ef-gh"):
        assert ms._as_quarter(raw) == raw, raw
    assert ms._fmt_asof(ms._as_quarter("2026-04-01")) == "2026 Q2"


def test_quarterly_ids_come_from_the_cadence_table():
    """분기 판정의 단일 출처는 공표 규약이다(#38) — 옛 판은 요청 주기용 목록을 손으로 따로
    들고 있었고(#24) 라벨·칩은 분기인지 묻지도 않았다."""
    from bot.macro_cadence import CADENCE
    assert ms._FRED_QUARTERLY == {s for s, spec in CADENCE.items() if spec[0] == "Q"}
    assert {_GDP, "kr_gdp"} <= ms._FRED_QUARTERLY
    assert not {"PCEPILFE", "T10Y2Y", "current_account"} & ms._FRED_QUARTERLY


def test_the_quarter_label_counts_lag_from_quarter_end():
    """경과는 분기 **말**부터 — 첫날로 세면 늘 두 달 부풀려진다(오늘이 언제든)."""
    assert ms._asof_lag_months("2026-04-01") - ms._asof_lag_months("2026Q2") == 2
    assert ms._asof_lag_months("2026Q2", date(2026, 9, 25)) == 3


# ── 화면 배선(수집기를 통째로 태운다, #20) ────────────────────────────────
class _Resp:
    def __init__(self, obs):
        self._obs = obs

    def raise_for_status(self):
        return None

    def json(self):
        return {"observations": self._obs}


def _quarters_desc(last_q_start: date, n: int) -> list[dict]:
    """FRED 분기 관측(내림차순) — 날짜는 **분기 첫날**, 값은 분기마다 다르게(#91c)."""
    out, d = [], last_q_start
    for i in range(n):
        out.append({"date": d.isoformat(), "value": f"{1.0 + i / 10:.1f}"})
        y, m = (d.year, d.month - 3) if d.month > 3 else (d.year - 1, d.month + 9)
        d = date(y, m, 1)
    return out


def _months_desc(last: date, n: int) -> list[dict]:
    out, d = [], last
    for i in range(n):
        out.append({"date": d.isoformat(), "value": f"{120 + n - i:.2f}"})
        d = date(d.year - (d.month == 1), 12 if d.month == 1 else d.month - 1, 1)
    return out


def _gdp_quarters():
    """한국 GDP(ECOS) — 12분기, 마지막 2026Q2."""
    out, y, q = [], 2026, 2
    for i in range(12):
        out.append((f"{y}Q{q}", 0.5 + i / 10))
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return list(reversed(out))


def _snapshot(tmp_path, monkeypatch):
    import bot.market_overview as mo
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
    monkeypatch.setattr(ms, "_ecos_series",
                        lambda key: _gdp_quarters() if key == "kr_gdp" else [])
    gdp = _quarters_desc(date(2026, 4, 1), 24)
    pce = _months_desc(date(2026, 7, 1), 24)

    def fake_get(url, params=None, timeout=None):       # `_fred_monthly` 의 경계
        sid = (params or {}).get("series_id")
        if sid == _GDP:
            assert params["frequency"] == "q", "분기 계열을 월로 물으면 FRED 가 400 이다"
            return _Resp(gdp)
        return _Resp(pce if sid == "PCEPILFE" else [])
    monkeypatch.setattr(ms.requests, "get", fake_get)
    spots = {_GDP: {"value": 1.5, "time": "2026-04-01", "change": -0.6},
             "PCEPILFE": {"value": 130.66, "time": "2026-07-01", "change": 0.32}}
    monkeypatch.setattr(mo, "_fred_fetch_series", lambda sid, lb: spots.get(sid))
    out = ms.fetch_macro_snapshot()
    return {r["key"]: r for r in list(out["domestic"]) + list(out["global"])}


def test_us_gdp_card_speaks_in_quarters(tmp_path, monkeypatch):
    """캡처 그대로의 결함 셋 — '기준 2026-04 (5개월 전)' · '2023-07 대비' · '12개월'."""
    rows = _snapshot(tmp_path, monkeypatch)
    gdp = rows["us_gdp"]
    assert gdp["asof"] == "2026 Q2", gdp["asof"]
    assert gdp["asof_lag"] == ms._asof_lag_months("2026Q2")       # 분기 말부터
    assert gdp["period_start_asof"] == "2023 Q3", gdp["period_start_asof"]
    assert gdp["spark_span"] == "12분기" and len(gdp["spark"]) == 12
    assert gdp["period_start"] == pytest.approx(2.1)              # 창 첫 분기(12번째)의 값


def test_korea_gdp_card_gets_the_same_quarter_chip(tmp_path, monkeypatch):
    """같은 병이 형제 화면(ECOS 분기)에도 있었다 — 칩만 '12개월' 이었다(#38)."""
    rows = _snapshot(tmp_path, monkeypatch)
    kr = rows["kr_gdp"]
    assert kr["asof"] == "2026 Q2" and kr["period_start_asof"] == "2023 Q3"
    assert kr["spark_span"] == "12분기", kr["spark_span"]


def test_monthly_cards_keep_their_month_labels(tmp_path, monkeypatch):
    """반대 증거(#25) — 월간 카드는 그대로 '12개월'·'YYYY-MM' 이다."""
    rows = _snapshot(tmp_path, monkeypatch)
    pce = rows["us_pce"]
    assert pce["asof"] == "2026-07" and pce["spark_span"] == "12개월"
    assert pce["period_start_asof"] == "2025-08", pce["period_start_asof"]


def test_the_card_renders_the_quarter_chip_and_start():
    from bot.dashboard import _render_macro_card
    html = _render_macro_card({
        "key": "us_gdp", "label": "미국 GDP", "unit": "%", "value": 1.5, "decimals": 1,
        "spark": [4.7, 1.5], "spark_dir": -1, "spark_span": "12분기",
        "period_start": 4.7, "period_start_asof": "2023 Q3", "period_change": -3.2,
        "change": -0.6, "pct_style": False, "asof": "2026 Q2", "asof_kind": "obs",
        "asof_lag": 3})
    assert ">12분기<" in html and "2023 Q3 대비" in html and "12개월" not in html
    assert "기준 2026 Q2" in html and "(3개월 전)" in html


# ── FRED 헤드라인 캐시 — 공표일에 스파크와 같은 기간을 말하게 ─────────────────
def _fred_cache(tmp_path, monkeypatch):
    import bot.market_overview as mo
    monkeypatch.setenv("FRED_API_KEY", "x" * 8)
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path / "mo")
    monkeypatch.setattr(mo, "_fred_fail", {})      # 실패 기억(M6)은 테스트마다 비운다
    return mo, tmp_path / "mo" / "fred" / f"PCEPILFE_{date.today().isoformat()}.json"


def test_monthly_headline_cache_is_short_and_shared_with_the_spark(tmp_path, monkeypatch):
    """헤드라인이 하루를 묵으면 공표일에 한 카드가 두 기간을 말한다(#33) — 그때 스파크
    (`_fred_monthly`)는 캐시 없이 30초마다 새로 받았다. 2차 리뷰 L6 이후엔 스파크도 같은 TTL
    함수(`_fred_ttl_h`)를 쓰므로 둘이 함께 넘어가고, 짧게 두는 이유는 **공표일 신선도**다
    (옛 이름의 전제 '스파크는 캐시가 없다' 는 뒤집혔다 — #222 지우지 않고 다시 쓴다. 스파크
    쪽 계약은 tests/test_fred_spark_cache_20260925.py). 1시간 지난 같은 날 사본은 다시 묻는다."""
    mo, f = _fred_cache(tmp_path, monkeypatch)
    assert mo._FRED_TTL_OTHER_H <= 1.0
    assert mo._fred_ttl_h("PCEPILFE") == mo._FRED_TTL_OTHER_H
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"value": 130.34, "time": "2026-06-01", "cv": mo._FRED_CACHE_VER}))
    old = time.time() - 2 * 3600
    os.utime(f, (old, old))
    calls: list = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _Resp([{"date": "2026-07-01", "value": "130.66"},
                      {"date": "2026-06-01", "value": "130.34"}])
    monkeypatch.setattr(mo.requests, "get", fake_get)
    out = mo._fred_fetch_series("PCEPILFE", 400)
    assert len(calls) == 1 and out["time"] == "2026-07-01" and out["value"] == 130.66
    # 방금 쓴 사본은 TTL 안 — 다시 묻지 않는다
    assert mo._fred_fetch_series("PCEPILFE", 400)["time"] == "2026-07-01" and len(calls) == 1


def test_fred_failure_serves_the_same_day_copy_not_nothing(tmp_path, monkeypatch):
    """TTL 을 줄이는 짝 — FRED 가 막힌 한 시간에 카드가 통째로 빠지면 안 된다(#394 낡은
    값이 빈 값보다 낫다). 관측일을 그대로 싣고 가므로 기준 라벨은 사실이다."""
    mo, f = _fred_cache(tmp_path, monkeypatch)
    f.parent.mkdir(parents=True)
    doc = {"value": 130.34, "time": "2026-06-01", "cv": mo._FRED_CACHE_VER}
    f.write_text(json.dumps(doc))
    old = time.time() - 5 * 3600
    os.utime(f, (old, old))

    def boom(url, timeout=None):
        raise ConnectionError("fred down")
    monkeypatch.setattr(mo.requests, "get", boom)
    assert mo._fred_fetch_series("PCEPILFE", 400) == doc
    # 실패 기억(M6)을 비워야 아래 갈래가 **실제로 원천을 탄다**(안 비우면 기억이 대신 답한다)
    mo._fred_fail.clear()
    got: list = []
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: got.append(1) or _Resp([]))
    assert mo._fred_fetch_series("PCEPILFE", 400) == doc          # 빈 응답도 같은 날 사본으로
    assert got == [1]
    # 옛 버전 사본은 믿지 않는다(#18) — 그땐 종전대로 None
    mo._fred_fail.clear()
    f.write_text(json.dumps(dict(doc, cv=mo._FRED_CACHE_VER - 1)))
    os.utime(f, (old, old))
    monkeypatch.setattr(mo.requests, "get", boom)
    assert mo._fred_fetch_series("PCEPILFE", 400) is None


# ── --history: 새 기간을 처음 본 날 ──────────────────────────────────────
def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def test_first_seen_marks_what_it_cannot_know():
    from bot.scripts.macro_staleness_audit import first_seen
    daily = [("2026-07-20", "2026Q1"), ("2026-08-20", "2026Q1"), ("2026-08-21", "2026Q2"),
             ("2026-08-22", "2026Q2"), ("2026-08-23", "쓰레기")]
    assert first_seen(daily, "Q") == [("2026Q1", "2026-07-20", ""),
                                      ("2026Q2", "2026-08-21", "2026-08-20")]
    # 순서가 섞여 들어와도 날짜순으로 판다 · 못 읽은 파일은 '없었다' 의 증거가 아니다
    shuffled = [("2026-08-21", "2026Q2"), ("2026-08-19", "??"), ("2026-07-20", "2026Q1")]
    assert first_seen(shuffled, "Q") == [("2026Q1", "2026-07-20", ""),
                                         ("2026Q2", "2026-08-21", "2026-07-20")]
    assert first_seen([], "M") == []


def _write_history(tmp_path):
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    for d in _days(date(2026, 7, 20), date(2026, 9, 25)):
        gdp = [["2026Q1", 1.8]] + ([["2026Q2", 0.6]] if d >= date(2026, 8, 21) else [])
        (ecos / f"series_v2_kr_gdp_1500_{d}.json").write_text(json.dumps(gdp))
        ca = [["202606", 1.0]] + ([["202607", 2.0]] if d >= date(2026, 9, 6) else [])
        (ecos / f"series_v2_current_account_400_{d}.json").write_text(json.dumps(ca))
        # 한국 수출 ECOS 대조본 — 7월분이 +34일(09-03)에야
        ex = [["202606", 1.0]] + ([["202607", 2.0]] if d >= date(2026, 9, 3) else [])
        (ecos / f"series_v2_export_amt_400_{d}.json").write_text(json.dumps(ex))
        br = [["20260701", 2.5]]
        (ecos / f"series_v2_base_rate_400_{d}.json").write_text(json.dumps(br))
    # 근원PCE — 8월 한 달 기록이 비어 있다(서버가 안 돌았다)
    for d in list(_days(date(2026, 7, 20), date(2026, 7, 31))) + \
            list(_days(date(2026, 9, 1), date(2026, 9, 25))):
        t = "2026-06-01" if d < date(2026, 7, 31) else "2026-07-01"
        (fred / f"PCEPILFE_{d}.json").write_text(json.dumps({"time": t}))
    # 이름이 겹치는 다른 계열의 파일은 섞이지 않는다 · 날짜가 아닌 이름은 건너뛴다(크래시 금지)
    (fred / "PCEPILFE2_2026-09-01.json").write_text(json.dumps({"time": "2027-01-01"}))
    (fred / "PCEPILFE_backup.json").write_text(json.dumps({"time": "2027-02-01"}))
    (ecos / "series_v2_kr_gdp_extra_1500_2026-09-01.json").write_text(json.dumps([["2027Q4", 9]]))
    (ecos / "series_v2_kr_gdp_1500_2026-09-02.json").write_bytes(b"\xff{broken")
    return ecos, fred


def _block(lines, label):
    i = next(k for k, ln in enumerate(lines) if ln.lstrip().startswith(label))
    out = [lines[i]]
    for ln in lines[i + 1:]:
        if not ln.startswith("      "):
            break
        out.append(ln)
    return out


def test_history_reports_first_seen_against_the_cadence(tmp_path):
    from bot.scripts.macro_staleness_audit import history_lines
    ecos, fred = _write_history(tmp_path)
    lines = history_lines(ms, ecos_dir=ecos, fred_dir=fred)
    gdp = _block(lines, "한국 GDP")
    assert "규약 +30일" in gdp[0] and "캐시 67일치 2026-07-20~2026-09-25" in gdp[0], gdp
    # 기간 칸은 카드와 같은 표기다(#418 — 옛 판은 원문 '2026Q1'·'202607', 계약을 다시 썼다 #222)
    assert "2026 Q1" in gdp[1] and "❓ 기록 시작 전부터 있었다" in gdp[1]
    assert "2026 Q2" in gdp[2] and "처음 본 날 2026-08-21 (기간 종료 +52일)" in gdp[2]
    assert "⚠️ 규약보다 최소 22일 늦게 실렸다" in gdp[2], gdp
    assert "2027" not in "\n".join(gdp), "다른 계열 파일이 섞였다"
    ca = _block(lines, "경상수지")
    assert "2026-07" in ca[2] and "(기간 종료 +37일)" in ca[2] and "✅ 규약 안" in ca[2], ca
    ex = _block(lines, "한국 수출")
    assert "customs:export_amt · ECOS 대조본" in ex[0]
    # 이 행은 ECOS **대조본**이다 — 카드는 관세청이라 이 지연을 안 탄다(리뷰 L13). 경고 글자를
    # 붙이면 카드가 늦다고 읽힌다(#34) — 계약 변경(#222): 옛 판은 '⚠️ 규약보다 최소 14일'.
    assert "(기간 종료 +34일)" in ex[2] and "ECOS 재게시가 규약보다 최소 14일 늦다" in ex[2], ex
    assert "⚠️" not in ex[2] and "카드 원천 관세청은 이 지연을 안 탄다" in ex[2], ex
    pce = _block(lines, "미국 근원PCE")
    assert "2027" not in "\n".join(pce), "이름이 겹치는 FRED 파일이 섞였다"
    assert "처음 본 날 2026-07-31 (기간 종료 +0일)" in pce[2] and "✅ 규약 안" in pce[2], pce
    br = _block(lines, "한국 기준금리")
    assert "⚪ 이벤트성" in br[0] and len(br) == 1
    gdp_us = _block(lines, "미국 GDP")
    assert "❓ 캐시 기록 없음" in gdp_us[0]


def test_history_does_not_claim_across_a_gap(tmp_path):
    """기록이 빈 사이에 실렸으면 규약 안인지 못 가른다 — 단정하지 않는다(#165)."""
    from bot.scripts.macro_staleness_audit import history_lines
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    (fred / "PCEPILFE_2026-07-20.json").write_text(json.dumps({"time": "2026-06-01"}))
    (fred / "PCEPILFE_2026-09-10.json").write_text(json.dumps({"time": "2026-07-01"}))
    pce = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 근원PCE")
    assert "직전 기록 2026-07-20" in pce[2] and "❓ 기록 사이가 비어" in pce[2], pce
    # 사이가 비어도 **빨라도 늦은** 경우는 늦다고 말한다
    (fred / "PCEPILFE_2026-09-08.json").write_text(json.dumps({"time": "2026-06-01"}))
    pce = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 근원PCE")
    assert "⚠️ 규약보다 최소" in pce[2], pce


def test_history_is_a_flag_and_the_daily_sweep_does_not_run_it(monkeypatch):
    """`audit_sweep` 은 `main([])` 을 부른다 — 기록 판독은 사람이 부르는 플래그다(#283)."""
    import bot.scripts.macro_staleness_audit as m
    called: list = []
    monkeypatch.setattr(m, "history", lambda: called.append(1) or 0)
    monkeypatch.setattr(m, "audit_rows", lambda _ms, _mo: [])
    monkeypatch.setattr(m, "_treasury_status", lambda mo: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        m.main([])
        m.main()
    assert called == [] and "요약" in buf.getvalue()
    assert m.main(["--history"]) == 0 and called == [1]


def test_history_cli_entrypoint_passes_argv(tmp_path):
    """진입점을 실제로 태운다(#252) — `__main__` 이 인자를 안 넘기면 플래그가 죽는다.
    HOME 을 비워 캐시 0 — 모든 줄이 판정 불가이고 rc 1 이다(대조 0건은 통과가 아니다, #54)."""
    env = dict(os.environ, HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "bot.scripts.macro_staleness_audit", "--history"],
                       cwd=str(Path(__file__).resolve().parents[1]), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert "--history" in r.stdout and "❓ 캐시 기록 없음" in r.stdout, r.stdout[-2000:]
    assert "기록을 잰 계열 0/" in r.stdout and "✅" not in r.stdout


def test_history_rc_is_zero_once_anything_was_measured(tmp_path, monkeypatch):
    import bot.scripts.macro_staleness_audit as m
    ecos, fred = _write_history(tmp_path)
    real = m.history_report
    monkeypatch.setattr(m, "history_report",
                        lambda _ms, **kw: real(_ms, ecos_dir=ecos, fred_dir=fred))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.main(["--history"])
    total = sum(1 for d in (ms.DOMESTIC, ms.GLOBAL) for row in d
                if row[3] in ("fred", "fred_yoy", "ecos", "customs"))
    assert rc == 0 and f"기록을 잰 계열 4/{total}개" in buf.getvalue(), buf.getvalue()


# ── 독립 리뷰(2026-09-25) 반영 ──────────────────────────────────────────
def test_the_global_fred_card_speaks_in_quarters_too():
    """리뷰 M2(#38) — 같은 market.html 의 글로벌 스냅샷 '핵심 지표' 가 미국 GDP 를 '(2026-04)' 로
    적고 매크로 카드는 '2026 Q2' 였다. 두 표면이 **같은 규칙 한 벌**(`macro_snapshot`)을 쓴다."""
    from bot.dashboard import _render_fred_card, _render_macro_card
    html = _render_fred_card([
        {"label": "미국 GDP", "series_id": _GDP, "unit": "%",
         "data": {"value": 1.5, "time": "2026-04-01", "change": -0.6}},
        {"label": "근원 PCE", "series_id": "PCEPILFE", "unit": "",
         "data": {"value": 130.66, "time": "2026-07-01", "change": 0.3}}], None)
    assert "(2026 Q2" in html and "2026-04" not in html, html
    assert "(2026-07" in html                      # 반대 증거(#25) — 월간은 그대로
    macro = _render_macro_card({
        "key": "us_gdp", "label": "미국 GDP", "unit": "%", "value": 1.5, "decimals": 1,
        "spark": [4.7, 1.5], "spark_dir": -1, "spark_span": "12분기", "period_start": 4.7,
        "period_start_asof": "2023 Q3", "period_change": -3.2, "change": -0.6,
        "pct_style": False, "asof": ms._fmt_asof(ms._as_quarter("2026-04-01")),
        "asof_kind": "obs"})
    assert "2026 Q2" in macro                     # 한 픽스처(2026-04-01)에서 두 표면이 같은 말


def test_a_fred_failure_is_remembered_briefly(tmp_path, monkeypatch):
    """리뷰 M6 — TTL 을 1시간으로 줄이자 원천 장애 동안 30초 재생성마다 시리즈별 10초
    타임아웃을 줄지어 기다렸다(옛 24시간 캐시에선 0). 실패는 10분 기억하고, 만료되면 다시 묻고,
    성공하면 기억을 지운다(#178)."""
    mo, f = _fred_cache(tmp_path, monkeypatch)
    calls: list = []

    def boom(url, timeout=None):
        calls.append(url)
        raise ConnectionError("down")
    monkeypatch.setattr(mo.requests, "get", boom)
    assert mo._fred_fetch_series("PCEPILFE", 400) is None and len(calls) == 1
    assert mo._fred_fetch_series("PCEPILFE", 400) is None and len(calls) == 1   # 기억 — 안 묻는다
    k = next(iter(mo._fred_fail))
    mo._fred_fail[k] -= mo._FRED_FAIL_TTL_S + 1
    ok = [{"date": "2026-07-01", "value": "130.66"}, {"date": "2026-06-01", "value": "130.34"}]
    monkeypatch.setattr(mo.requests, "get",
                        lambda url, timeout=None: calls.append(url) or _Resp(ok))
    assert mo._fred_fetch_series("PCEPILFE", 400)["time"] == "2026-07-01" and len(calls) == 2
    assert mo._fred_fail == {}                     # 성공이 기억을 지운다
    # 기억은 **그 캐시 파일** 단위다 — 캐시 디렉터리가 다른 실행(테스트 포함)은 남의 실패를
    # 물려받지 않는다(#30). 한 디렉터리에서 실패한 뒤 다른 디렉터리로 바꾸면 다시 묻는다.
    monkeypatch.setattr(mo.requests, "get", boom)
    f.unlink()
    assert mo._fred_fetch_series("PCEPILFE", 400) is None and len(calls) == 3
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path / "other")
    assert mo._fred_fetch_series("PCEPILFE", 400) is None and len(calls) == 4


def test_the_yoy_path_shares_the_ttl_the_stale_copy_and_the_memory(tmp_path, monkeypatch,
                                                                   caplog):
    """리뷰 M3(#38·#33) — 글로벌 '(YoY)' 행만 24시간으로 남아 공표일에 매크로 카드와 다른 달을
    말했다. 헤드라인과 같은 TTL·같은 날 사본·실패 기억을 쓰고, 실패는 조용하지 않다(#12)."""
    import logging
    mo, _f = _fred_cache(tmp_path, monkeypatch)
    y = tmp_path / "mo" / "fred" / f"CPIAUCSL_yoy_{date.today().isoformat()}.json"
    y.parent.mkdir(parents=True)
    doc = {"value": 2.7, "time": "2026-06-01", "prev_value": 2.6, "change": 0.1,
           "cv": mo._FRED_CACHE_VER}
    y.write_text(json.dumps(doc))
    old = time.time() - 2 * 3600                    # 옛 판(24h)이면 그대로 서빙했을 나이
    os.utime(y, (old, old))
    calls: list = []
    obs = [{"date": f"{yy}-{mm:02d}-01", "value": f"{300 + i}"}
           for i, (yy, mm) in enumerate([(2026, 7), (2026, 6), (2025, 7), (2025, 6)])]
    monkeypatch.setattr(mo.requests, "get",
                        lambda url, timeout=None: calls.append(url) or _Resp(obs))
    got = mo._fetch_fred_yoy("CPIAUCSL")
    assert len(calls) == 1 and got["time"] == "2026-07-01", got     # 1시간 지난 사본은 다시 묻는다
    os.utime(y, (old, old))

    def boom(url, timeout=None):
        calls.append(url)
        raise ConnectionError("down")
    monkeypatch.setattr(mo.requests, "get", boom)
    caplog.set_level(logging.WARNING)
    assert mo._fetch_fred_yoy("CPIAUCSL") == got and len(calls) == 2   # 같은 날 사본
    assert any("YoY fetch CPIAUCSL failed" in r.getMessage() for r in caplog.records)
    assert mo._fetch_fred_yoy("CPIAUCSL") == got and len(calls) == 2   # 기억 — 안 묻는다


def test_the_ecos_chip_counts_what_was_drawn(tmp_path, monkeypatch):
    """리뷰 L9(#29) — ECOS·관세청 카드의 칩이 점 수와 무관하게 '12개월' 이었다. 원천이 7달만
    준 날엔 '7개월' 이다(분기 칩과 같은 규칙 — 데이터 폭으로)."""
    import bot.market_overview as mo
    import bot.naver_marketindex as nm
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "snap")
    (tmp_path / "snap").mkdir()
    for name, val in (("_fetch_macro_naver_values", lambda sids: {}),
                      ("_yf_monthly_batch", lambda tk: {}), ("_yf_daily_1mo_batch", lambda tk: {}),
                      ("_fred_monthly", lambda sid, months=12, **kw: []),
                      ("_customs_series", lambda key: {"points": [], "src": ""})):
        monkeypatch.setattr(ms, name, val)
    for fn in ("fetch_commodity_spark", "fetch_naver_index_history",
               "fetch_naver_crypto_history", "fetch_naver_fx_history"):
        monkeypatch.setattr(nm, fn, lambda *a, **k: [])
    monkeypatch.setattr(mo, "_fred_fetch_series", lambda sid, lb: None)
    seven = [(f"2026{m:02d}", float(m)) for m in range(1, 8)]
    monkeypatch.setattr(ms, "_ecos_series",
                        lambda key: seven if key == "current_account" else
                        [(f"2025{m:02d}", 1.0) for m in range(1, 13)] + [("202601", 2.0)])
    out = ms.fetch_macro_snapshot()
    rows = {r["key"]: r for r in list(out["domestic"]) + list(out["global"])}
    assert rows["kr_ca"]["spark_span"] == "7개월", rows["kr_ca"]["spark_span"]
    assert rows["kr_reserve"]["spark_span"] == "12개월"


def test_a_zero_span_change_says_no_change_not_previous_month():
    """리뷰 L11 — 헤드라인 칩은 **그린 기간 전체**(첫 점→끝 점) 변화다. 0 일 때 '전월 동일' 은
    재지 않은 비교를 말했다(분기 카드면 더 틀렸다)."""
    from bot.dashboard import _macro_fmt_change, _render_macro_card
    assert "변동 없음" in _macro_fmt_change(0.0, 2) and "전월" not in _macro_fmt_change(0.0, 2)
    html = _render_macro_card({
        "key": "kr_rate", "label": "한국 기준금리", "unit": "%", "value": 2.5, "decimals": 2,
        "spark": [2.5, 2.5], "spark_dir": 0, "spark_span": "12개월", "period_start": 2.5,
        "period_start_asof": "2025-09", "period_change": 0.0, "change": 0.0,
        "pct_style": False, "asof": "2026-08", "asof_kind": "obs"})
    assert "변동 없음" in html and "전월 동일" not in html


def test_the_history_boundary_is_inclusive(tmp_path):
    """리뷰 L7 — 규약+여유 **당일**에 처음 보였으면 규약 안이다(`hi <= limit`). 근원PCE
    (월간 +30 · 여유 4): 6월분(06-30 종료)을 08-03(+34일)에 처음 봤고 직전 기록이 08-02 다."""
    from bot.scripts.macro_staleness_audit import history_lines
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    (fred / "PCEPILFE_2026-08-02.json").write_text(json.dumps({"time": "2026-05-01"}))
    (fred / "PCEPILFE_2026-08-03.json").write_text(json.dumps({"time": "2026-06-01"}))
    pce = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 근원PCE")
    assert "(기간 종료 +34일)" in pce[2] and "✅ 규약 안" in pce[2], pce
    (fred / "PCEPILFE_2026-08-03.json").write_text(json.dumps({"time": "2026-05-01"}))
    (fred / "PCEPILFE_2026-08-04.json").write_text(json.dumps({"time": "2026-06-01"}))
    pce = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 근원PCE")
    assert "(기간 종료 +35일)" in pce[2] and "⚠️ 규약보다 최소 5일" in pce[2], pce


# ── 2차 독립 리뷰(217aace..ef33923) 반영 ─────────────────────────────────────
def test_an_empty_fred_answer_is_remembered_and_a_yoy_success_clears_it(tmp_path, monkeypatch):
    """F3·F6 — 빈 답도 30초마다 다시 묻지 않는다(헤드라인·YoY 둘 다). F7 — YoY 성공은 자기
    기억을 지운다(안 지우면 다음 실패가 10분 전 기록을 이어받는다)."""
    mo, _f = _fred_cache(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(mo.requests, "get", lambda url, timeout=None: calls.append(url) or _Resp([]))
    assert mo._fred_fetch_series("PCEPILFE", 400) is None and len(calls) == 1
    assert mo._fred_fetch_series("PCEPILFE", 400) is None and len(calls) == 1
    assert mo._fetch_fred_yoy("CPIAUCSL") is None and len(calls) == 2
    assert mo._fetch_fred_yoy("CPIAUCSL") is None and len(calls) == 2
    for k in list(mo._fred_fail):
        mo._fred_fail[k] -= mo._FRED_FAIL_TTL_S + 1
    obs = [{"date": f"{yy}-{mm:02d}-01", "value": f"{300 + i}"}
           for i, (yy, mm) in enumerate([(2026, 7), (2026, 6), (2025, 7), (2025, 6)])]
    monkeypatch.setattr(mo.requests, "get",
                        lambda url, timeout=None: calls.append(url) or _Resp(obs))
    assert mo._fetch_fred_yoy("CPIAUCSL")["time"] == "2026-07-01" and len(calls) == 3
    assert [k for k in mo._fred_fail if "_yoy_" in k] == [], mo._fred_fail


# ── --history 표기·계수(2026-09-26 VM 출력이 드러냈다, 실수 #418) ─────────────────
def test_history_labels_the_period_like_the_card(tmp_path):
    """기간 칸이 원천 원문(`2026-04-01`)이면 옆의 '기간 종료 +N일' 을 **표시된 기간으로 검산할 수
    없다** — 04-01 에서 08-01 은 +122일인데 줄은 +32일(분기 말 06-30 기준)이라 적는다(#33). 카드와
    **같은 표기 함수**(`_fmt_asof`, 분기는 `_as_quarter` 경유, 일별은 날짜 그대로)로 적는다(#38)."""
    from bot.scripts.macro_staleness_audit import history_lines
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    for d, t in (("2026-07-20", "2026-01-01"), ("2026-08-01", "2026-04-01")):
        (fred / f"{_GDP}_{d}.json").write_text(json.dumps({"time": t}))
    for d, t in (("2026-08-26", "2026-06-01"), ("2026-08-27", "2026-07-01")):
        (fred / f"PCEPILFE_{d}.json").write_text(json.dumps({"time": t}))
    for d, t in (("2026-09-23", "2026-09-21"), ("2026-09-24", "2026-09-22")):
        (fred / f"DGS10_{d}.json").write_text(json.dumps({"time": t}))
    for d, pts in (("2026-09-03", [["202606", 1.0]]), ("2026-09-04", [["202607", 2.0]])):
        (ecos / f"series_v2_current_account_400_{d}.json").write_text(json.dumps(pts))
    lines = history_lines(ms, ecos_dir=ecos, fred_dir=fred)
    gdp = _block(lines, "미국 GDP")
    assert gdp[2].split()[0] == "2026" and "2026 Q2" in gdp[2], gdp       # 분기: 카드와 같은 'YYYY Qn'
    assert "(기간 종료 +32일)" in gdp[2] and "2026-04-01" not in gdp[2], gdp
    assert "2026 Q1" in gdp[1] and "2026-01-01" not in gdp[1], gdp
    pce = _block(lines, "미국 근원PCE")
    assert pce[2].split()[0] == "2026-07" and "(기간 종료 +27일)" in pce[2], pce   # 월: 'YYYY-MM'
    dgs = _block(lines, "미국 10Y")
    assert dgs[2].split()[0] == "2026-09-22", dgs                           # 일별: 날짜 그대로
    ca = _block(lines, "경상수지")
    assert ca[2].split()[0] == "2026-07" and "202607" not in ca[2], ca     # ECOS 월도 같은 표기
    # 표기는 카드 헤드라인 라벨과 같은 함수에서 나온다 — 카드가 바뀌면 같이 바뀐다
    assert ms._fmt_asof(ms._as_quarter("2026-04-01")) == "2026 Q2"


def test_history_counts_days_not_files_and_collapses_same_day_copies(tmp_path):
    """국고채 10년은 매크로 카드와 유동성 보드가 **조회 길이가 다른** ECOS 파일을 매일 하나씩
    만든다(`series_v2_kr10y_400_…` · `…_950_…`). 옛 판은 파일 수를 '캐시 N일치' 로 세어 31일
    구간에 62일치라 적었고(2026-09-26 VM 출력, #45), 같은 날 두 파일의 최신 기간이 다르면
    `직전 기록` 이 **같은 날**이 돼 하한이 상한보다 커졌다 — 규약+여유 당일에 처음 본 기간이
    '⚠️ 늦게 실렸다' 로 뒤집힌다. 같은 날 파일은 하루로 합친다(그날 본 가장 새 기간)."""
    from bot.scripts.macro_staleness_audit import history_lines, first_seen
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    # 규약 +1 · 여유 4 → 한도 5일. 09-15 분이 09-20(+5, 한도 당일)에 처음 보였다.
    days = {"2026-09-18": ("20260914", "20260914"),
            "2026-09-19": ("20260914", "20260914"),
            "2026-09-20": ("20260914", "20260915")}   # 같은 날 한 파일만 새 기간
    for d, (a, b) in days.items():
        (ecos / f"series_v2_kr10y_400_{d}.json").write_text(json.dumps([[a, 3.0]]))
        (ecos / f"series_v2_kr10y_950_{d}.json").write_text(json.dumps([[b, 3.0]]))
    kr = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "국고채 10년")
    assert "캐시 3일치 2026-09-18~2026-09-20" in kr[0], kr
    last = kr[-1]
    assert last.split()[0] == "2026-09-15" and "처음 본 날 2026-09-20 (기간 종료 +5일)" in last, kr
    assert "✅ 규약 안" in last and "⚠️" not in last, kr
    # 순수 함수도 같은 날을 하나로 — 직전 기록은 **앞선 날**이다
    rows = [("2026-09-19", "20260914"), ("2026-09-20", "20260914"), ("2026-09-20", "20260915")]
    assert first_seen(rows, "D")[-1] == ("20260915", "2026-09-20", "2026-09-19")
    # 같은 날 못 읽은 파일만 있는 날은 '직전 기록' 이 되지 않는다(없었다는 증거가 아니다)
    rows2 = rows + [("2026-09-21", "??")]
    assert first_seen(rows2, "D") == first_seen(rows, "D")


def test_history_header_counts_only_days_it_could_read_and_says_what_it_merged(tmp_path):
    """독립 리뷰 Low 둘: ① '캐시 N일치' 가 기간을 **못 읽은 날**까지 세어 아래 판정의 재료보다
    많다고 말했다(`first_seen` 은 그 날을 건너뛴다, #45) ② 두 화면(매크로 400일 · 유동성 950일)의
    사본을 날짜로 합치는데 헤더가 그 사실을 안 말해, 이 줄이 '원천이 언제 실었나' 를 잰다는 것
    (한 화면의 사본이 늦은 것은 못 본다)이 안 보였다(#274 못 보는 축을 같이 말할 것)."""
    from bot.scripts.macro_staleness_audit import history_lines
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    for d, t in (("2026-08-26", "2026-06-01"), ("2026-08-27", "2026-07-01"), ("2026-08-28", "??")):
        (fred / f"PCEPILFE_{d}.json").write_text(json.dumps({"time": t}))
    pce = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 근원PCE")
    assert "캐시 2일치 2026-08-26~2026-08-27" in pce[0] and "기간을 못 읽은 1일 제외" in pce[0], pce
    assert "합침" not in pce[0], pce                              # 하루 한 사본이면 합친 게 없다
    for d in ("2026-09-18", "2026-09-19"):
        for lb in (400, 950):
            (ecos / f"series_v2_kr10y_{lb}_{d}.json").write_text(json.dumps([["20260914", 3.0]]))
    kr = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "국고채 10년")
    assert "캐시 2일치" in kr[0] and "사본 4개를 날짜로 합침" in kr[0], kr
    # 전부 못 읽었으면 판정하지 않고 그렇게 말한다(IndexError 로 죽지 않는다, #54)
    for d in ("2026-09-01", "2026-09-02"):
        (fred / f"UNRATE_{d}.json").write_text(json.dumps({"time": "??"}))
    un = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 실업률")
    assert "❓ 캐시 2일치가 있으나 기간을 하나도 못 읽었다" in un[0], un


def test_history_period_label_follows_the_cards_own_daily_list():
    """기간 칸을 날짜로 통째 쓸지는 **카드가 쓰는 목록**(`_DAILY_CADENCE_KEYS`)이 정한다 — 공표
    규약의 freq 로 따로 가르면 두 목록이 갈리는 날 칸이 카드와 다르게 찍힌다(독립 리뷰 Low ·
    #24·#38). 규약과 카드 목록이 **엇갈리는** 입력으로 누가 이기는지 잰다."""
    from bot.scripts.macro_staleness_audit import period_label
    assert "us_10y" in ms._DAILY_CADENCE_KEYS and "us_pce" not in ms._DAILY_CADENCE_KEYS
    assert period_label(ms, "2026-09-24", "M", "us_10y") == ms._fmt_asof("2026-09-24", full=True)
    assert period_label(ms, "2026-09-24", "D", "us_pce") == ms._fmt_asof("2026-09-24", full=False)
    assert period_label(ms, "2026-09-24", "D") == ms._fmt_asof("2026-09-24", full=True)   # 키 없음


def test_history_passes_the_card_key_to_the_label(tmp_path, monkeypatch):
    """배선(#20) — 두 목록이 오늘은 일치해 `history_report` 가 키를 안 넘겨도 출력이 같다(동등
    뮤테이션). 카드 목록을 **일부러 갈라** 누가 이기는지 본다."""
    from bot.scripts.macro_staleness_audit import history_lines
    ecos, fred = tmp_path / "ecos", tmp_path / "fred"
    ecos.mkdir()
    fred.mkdir()
    for d, t in (("2026-09-23", "2026-09-21"), ("2026-09-24", "2026-09-22")):
        (fred / f"DGS10_{d}.json").write_text(json.dumps({"time": t}))
    monkeypatch.setattr(ms, "_DAILY_CADENCE_KEYS", set(ms._DAILY_CADENCE_KEYS) - {"us_10y"})
    dgs = _block(history_lines(ms, ecos_dir=ecos, fred_dir=fred), "미국 10Y")
    assert dgs[2].split()[0] == "2026-09", dgs                   # 카드 목록이 이긴다
