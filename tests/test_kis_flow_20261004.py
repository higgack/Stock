"""KIS 수급 4종 — 공식 샘플 필드 · 날짜 · 금액 단위 · 단일 수집 함수 · 감사 (실수 #433).

2026-10-04 까지:
  (a) ``stock_snapshot`` 과 ``dashboard`` 의 수급 블록 두 사본이 ``KisClient`` 에 **없는**
      ``_ready()`` 를 불러 AttributeError 가 DEBUG 에 삼켜졌다 — 수급 탭의 KIS 칸은 한 번도
      채워지지 않았다.
  (b) 분석 파이프라인(``agent_utils``)은 같은 메서드를 게이트 없이 부르는데, 메서드가 공식
      샘플과 다른 필드 이름을 읽어 개인·기관 세부·대주·프로그램이 늘 None 이었고 '당일
      순매수' 는 **수량(주)** 을 만원으로 읽어 억원으로 바꿔 프롬프트에 실었다.
  (c) 시장 분류 코드로 코스닥에 공식 값이 아닌 ``Q`` 를 보냈다.

픽스처의 필드 이름은 공식 샘플 COLUMN_MAPPING 그대로다(github.com/koreainvestment/
open-trading-api ``examples_llm/domestic_stock/{inquire_investor,daily_credit_balance,
daily_short_sale,program_trade_by_stock_daily}/chk_*.py``). **값은 합성**이다 — 실제
응답의 행 수·순서·금액 단위는 개발 샌드박스에서 잴 수 없어 ``kis_flow_audit`` 가 운영에서
매일 잰다(이 파일은 그 가정이 깨졌을 때 코드가 어떻게 말하는지를 잰다).
"""
from __future__ import annotations

import ast
import logging
import os
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


# ─── 픽스처(공식 필드 이름 · 합성 값) ─────────────────────────────────────────

def _inv_row(date, i, *, unit=1_000_000, close=70_000, filled=True, drop=()):
    r = {"stck_bsop_date": date, "stck_clpr": str(close)}
    for j, p in enumerate(("frgn", "orgn", "prsn")):
        buy = (1_000_000 + 10_000 * i + 1_000 * j) if filled else 0
        sell = (900_000 + 5_000 * i + 2_000 * j) if filled else 0
        r[f"{p}_shnu_vol"] = str(buy)
        r[f"{p}_seln_vol"] = str(sell)
        r[f"{p}_shnu_tr_pbmn"] = str(round(buy * close / unit))
        r[f"{p}_seln_tr_pbmn"] = str(round(sell * close / unit))
        r[f"{p}_ntby_qty"] = str(buy - sell)
        r[f"{p}_ntby_tr_pbmn"] = str(round((buy - sell) * close / unit))
    for k in drop:
        r.pop(k, None)
    return r


_DATES = ["20260924", "20260925", "20260926", "20260929", "20260930", "20261001", "20261002"]


def _inv_payload(*, unit=1_000_000, head_unfilled=True, decoy=True):
    """오래된 날부터 준다(원천 순서에 기대지 않는지 재려고). 가장 최근(10-02)은
    장중처럼 비어 있다. ``decoy`` 면 옛 판이 읽던 이름(``indv_*``)에 **다른 값**을 둔다
    — 코드가 옛 이름으로 되돌아가면 값이 바뀌어 잡힌다."""
    rows = []
    for i, d in enumerate(_DATES):
        filled = not (head_unfilled and d == _DATES[-1])
        r = _inv_row(d, i, unit=unit, filled=filled)
        if decoy:
            r["indv_ntby_qty"] = "-999999999"
            r["indv_ntby_tr_pbmn"] = "-999999999"
            r["pnsn_ntby_tr_pbmn"] = "123"
        rows.append(r)
    return {"rt_cd": "0", "output": rows}


def _credit_payload():
    return {"rt_cd": "0", "output": [
        {"deal_date": "20260929", "whol_loan_rmnd_stcn": "1000", "whol_loan_rmnd_rate": "0.50",
         "whol_stln_rmnd_stcn": "10", "whol_stln_rmnd_rate": "0.01", "stln_rmnd_qty": "77"},
        {"deal_date": "20261001", "whol_loan_rmnd_stcn": "1200", "whol_loan_rmnd_rate": "4.20",
         "whol_stln_rmnd_stcn": "35", "whol_stln_rmnd_rate": "0.02", "stln_rmnd_qty": "77"},
    ]}


def _short_payload():
    return {"rt_cd": "0", "output1": {"stck_prpr": "70000"}, "output2": [
        {"stck_bsop_date": "20260930", "ssts_cntg_qty": "500", "ssts_vol_rlim": "3.10",
         "ssts_tr_pbmn_rlim": "3.00", "avrg_prc": "70100"},
        {"stck_bsop_date": "20261001", "ssts_cntg_qty": "800", "ssts_vol_rlim": "16.40",
         "ssts_tr_pbmn_rlim": "16.00", "avrg_prc": "70200"},
    ]}


def _prog_payload(unit=1_000_000, close=70_000):
    rows = []
    for i, d in enumerate(_DATES[:-1]):
        buy, sell = 500_000 + 1_000 * i, 300_000 + 2_000 * i
        rows.append({"stck_bsop_date": d, "stck_clpr": str(close),
                     "whol_smtn_shnu_vol": str(buy), "whol_smtn_seln_vol": str(sell),
                     "whol_smtn_shnu_tr_pbmn": str(round(buy * close / unit)),
                     "whol_smtn_seln_tr_pbmn": str(round(sell * close / unit)),
                     "whol_smtn_ntby_qty": str(buy - sell),
                     "whol_smtn_ntby_tr_pbmn": str(round((buy - sell) * close / unit)),
                     # 옛 판이 읽던(공식 목록에 없는) 이름 — 읽히면 안 된다
                     "pgtr_seln_amt1": "999", "pgtr_shnu_amt1": "1"})
    return {"rt_cd": "0", "output": list(reversed(rows))}


@pytest.fixture
def kc(monkeypatch, tmp_path):
    """KIS 클라이언트 — 캐시는 이 테스트 전용 디렉터리, 자격증명은 있는 것으로."""
    from bot import kis_client
    monkeypatch.setattr(kis_client, "_CACHE_DIR", tmp_path / "kis")
    monkeypatch.setattr(kis_client, "_app_key", lambda: "k")
    monkeypatch.setattr(kis_client, "_app_secret", lambda: "s")
    kis_client._MISSING_WARNED.clear()
    return kis_client


def _router(monkeypatch, kc, table):
    calls: list = []

    def _get(path, tr_id, params, custtype=None):
        calls.append((path, tr_id, dict(params)))
        v = table.get(tr_id)
        return v(params) if callable(v) else v

    monkeypatch.setattr(kc, "_get", _get)
    return calls


def _ok_table():
    return {"FHKST01010900": _inv_payload(), "FHPST04760000": _credit_payload(),
            "FHPST04830000": _short_payload(), "FHPPG04650201": _prog_payload()}


# ─── 파서 ────────────────────────────────────────────────────────────────────

def test_investor_reads_official_fields_by_date_and_skips_the_unfilled_head(kc):
    r = kc.parse_investor_flow(_inv_payload())
    assert r["schema"] == kc._FLOW_SCHEMA
    # 원천은 오래된 날부터 줬다 — 날짜로 정렬해 가장 최근 **채워진** 날을 쓴다
    assert r["asof"] == "2026-10-01" and r["latest"]["date"] == "2026-10-01"
    assert r["pending"] == 1, r["pending"]                       # 10-02(장중 빈 행)
    i = _DATES.index("20261001")
    want = _inv_row("20261001", i)
    # 개인은 공식 이름 prsn_* — 옛 이름(indv_*)의 미끼 값이 읽히면 안 된다
    assert r["latest"]["qty"]["individual"] == int(want["prsn_ntby_qty"])
    assert r["latest"]["qty"]["foreign"] == int(want["frgn_ntby_qty"])
    # 금액 = 원천 값 × 단위(원). 단위는 항등식으로 잰 ×100만
    assert r["unit_won"] == 1_000_000, r["unit_note"]
    assert r["latest"]["won"]["foreign"] == int(want["frgn_ntby_tr_pbmn"]) * 1_000_000
    # 창 = 채워진 최근 5거래일(09-25~10-01), 원천이 준 행 수(6개)와 무관
    w = r["window"]
    assert (w["from"], w["to"], w["days"]) == ("2026-09-25", "2026-10-01", 5), w
    exp = sum(int(_inv_row(d, _DATES.index(d))["orgn_ntby_tr_pbmn"])
              for d in _DATES[1:6]) * 1_000_000
    assert w["won"]["institution"] == exp
    assert "inst_breakdown" not in r and r["missing"] == []


def test_window_refuses_a_partial_sum(kc):
    """창의 한 날이라도 비면 그 칸의 합을 만들지 않는다 — 4일 합을 5거래일 누적이라
    부르면 거짓말이다(#99). 다른 칸은 그대로."""
    p = _inv_payload(head_unfilled=False)
    p["output"][-2].pop("frgn_ntby_tr_pbmn")                     # 10-01 의 외인 금액만 빠짐
    r = kc.parse_investor_flow(p)
    assert r["window"]["won"]["foreign"] is None
    assert r["window"]["won"]["institution"] is not None
    assert "frgn_ntby_tr_pbmn" not in r["missing"]               # 최신(10-02)엔 있다


def test_missing_official_field_is_reported_once(kc, caplog):
    p = _inv_payload(head_unfilled=False)
    for row in p["output"]:
        row.pop("prsn_ntby_qty")
    with caplog.at_level(logging.WARNING, logger="bot.kis"):
        r1 = kc.parse_investor_flow(p)
        kc.parse_investor_flow(p)
    assert r1["missing"] == ["prsn_ntby_qty"] and r1["latest"]["qty"]["individual"] is None
    warns = [x for x in caplog.records if "prsn_ntby_qty" in x.getMessage()]
    assert len(warns) == 1, [w.getMessage() for w in warns]


@pytest.mark.parametrize("unit", [1, 1_000, 10_000, 1_000_000, 100_000_000])
def test_unit_calibration_finds_each_candidate(kc, unit):
    samples = [(1_000_000 + 7 * i, 70_000, 1_000_000 * 70_000 / unit * (0.8 + 0.05 * i))
               for i in range(6)]
    got, note = kc._calibrate_unit(samples)
    assert got == unit, note


def test_unit_calibration_refuses_rather_than_guesses(kc):
    # 표본 부족
    assert kc._calibrate_unit([(10, 100, 1)])[0] is None
    # 반반 갈림 → 모른다
    mixed = [(1000, 100, 100)] * 3 + [(1000, 100, 0.1)] * 3
    assert kc._calibrate_unit(mixed)[0] is None
    # 0.5~2배 창의 경계 — 비(수량×가격÷금액)가 1.9·0.5 면 ×1, 2.5·0.4 면 어느 후보와도
    # 안 맞아 거절(후보끼리 10배 이상 떨어져 창이 겹치지 않는다)
    assert kc._calibrate_unit([(10, 19, 100)] * 3)[0] == 1
    assert kc._calibrate_unit([(10, 5, 100)] * 3)[0] == 1
    for p in (25, 4):
        far = kc._calibrate_unit([(10, p, 100)] * 3)
        assert far[0] is None and "맞지 않습니다" in far[1], (p, far)
    # 0 · 음수 · None 은 표본이 아니다
    assert kc._calibrate_unit([(0, 1, 1), (None, 1, 1), (-5, 1, 1), (5, 1, -1)])[0] is None


def test_unit_unknown_keeps_quantities_and_drops_amounts(kc):
    p = _inv_payload(head_unfilled=False)
    for row in p["output"]:                                     # 대금 칸을 비워 단위를 못 재게
        for k in list(row):
            if k.endswith(("shnu_tr_pbmn", "seln_tr_pbmn")):
                row[k] = "0"
    r = kc.parse_investor_flow(p)
    assert r["unit_won"] is None and "표본" in r["unit_note"]
    assert r["latest"]["qty"]["foreign"] is not None
    assert all(v is None for v in r["latest"]["won"].values())
    assert all(v is None for v in r["window"]["won"].values())


def test_credit_reads_official_names_and_latest_deal_date(kc):
    r = kc.parse_credit_balance(_credit_payload())
    assert r["asof"] == "2026-10-01"
    assert r["credit_balance_shares"] == 1200 and r["credit_balance_pct"] == 4.2
    # 대주 잔고 = whol_stln_rmnd_stcn (옛 판의 stln_rmnd_qty=77 이 읽히면 안 된다)
    assert r["credit_short_shares"] == 35
    assert "loan_balance_shares" not in r and "credit_balance_amt" not in r


def test_short_sale_reads_output2_latest(kc):
    r = kc.parse_short_sale(_short_payload())
    assert r["asof"] == "2026-10-01" and r["short_qty"] == 800
    assert r["short_ratio_pct"] == 16.4 and r["short_amt_ratio_pct"] == 16.0


def test_program_daily_reads_whole_sum_and_no_arbitrage_split(kc):
    r = kc.parse_program_daily(_prog_payload())
    assert r["asof"] == "2026-10-01" and r["unit_won"] == 1_000_000
    i = 5                                                       # _DATES[5] = 10-01
    buy, sell = 500_000 + 1_000 * i, 300_000 + 2_000 * i
    assert r["latest"]["qty"] == buy - sell
    assert r["latest"]["won"] == round((buy - sell) * 70_000 / 1_000_000) * 1_000_000
    assert r["window"]["days"] == 5 and r["window"]["to"] == "2026-10-01"
    assert not {"arb_net", "nonarb_net", "arb_buy"} & set(r)


# ─── TR · 파라미터 · 캐시 ────────────────────────────────────────────────────

def test_market_code_is_J_for_kosdaq_too(monkeypatch, kc):
    """공식 샘플은 J:KRX·NX:NXT·UN:통합 만 적는다 — 코스닥도 J 다."""
    assert kc._mkt_div("247540.KQ") == "J" and kc._mkt_div("005930.KS") == "J"
    calls = _router(monkeypatch, kc, _ok_table())
    k = kc.KisClient()
    for fn in (k.get_investor_flow, k.get_credit_short_balance, k.get_short_sale,
               k.get_program_trade):
        fn("247540.KQ")
    assert calls and {c[2]["FID_COND_MRKT_DIV_CODE"] for c in calls} == {"J"}, calls


def test_each_method_calls_the_official_tr(monkeypatch, kc):
    calls = _router(monkeypatch, kc, _ok_table())
    k = kc.KisClient()
    assert k.get_investor_flow("005930.KS")["schema"] == kc._FLOW_SCHEMA
    assert k.get_short_sale("005930.KS")["short_qty"] == 800
    assert k.get_program_trade("005930.KS")["unit_won"] == 1_000_000
    seen = {(c[0], c[1]) for c in calls}
    assert ("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900") in seen
    assert ("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000") in seen
    assert ("/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
            "FHPPG04650201") in seen
    assert not any(c[1] == "FHPPG04650100" for c in calls)


def test_credit_tries_today_first_then_blank(monkeypatch, kc):
    class _Now:
        @staticmethod
        def now(tz=None):
            from datetime import datetime as _dt
            return _dt(2026, 10, 2, 9, 30, tzinfo=tz)

    monkeypatch.setattr(kc, "datetime", _Now)
    seq: list = []

    def credit(params):
        seq.append(params["FID_INPUT_DATE_1"])
        return None if params["FID_INPUT_DATE_1"] else _credit_payload()

    _router(monkeypatch, kc, {"FHPST04760000": credit})
    r = kc.KisClient().get_credit_short_balance("005930.KS")
    assert seq == ["20261002", ""] and r["credit_short_shares"] == 35
    # 오늘 날짜로 행이 오면 두 번 묻지 않는다
    seq.clear()
    (kc._CACHE_DIR / "credit_000660.json").unlink(missing_ok=True)
    _router(monkeypatch, kc, {"FHPST04760000": lambda p: (seq.append(p["FID_INPUT_DATE_1"])
                                                          or _credit_payload())})
    kc.KisClient().get_credit_short_balance("000660.KS")
    assert seq == ["20261002"], seq


def test_flow_cache_ignores_old_shapes_and_pending_expires(monkeypatch, kc):
    calls = _router(monkeypatch, kc, _ok_table())
    kc._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = kc._CACHE_DIR / "investor_005930.json"
    # 옛 모양(판 없음)은 캐시가 아니다 — 다시 묻는다
    f.write_text('{"today": {"foreign": 1}, "5d": {"foreign": 2}}', encoding="utf-8")
    r = kc.KisClient().get_investor_flow("005930.KS")
    assert r["schema"] == kc._FLOW_SCHEMA and len(calls) == 1
    # 판이 맞으면 캐시를 쓴다
    kc.KisClient().get_investor_flow("005930.KS")
    assert len(calls) == 1
    # 원천이 아직 안 채운 날이 있던 응답(pending)은 1시간 지나면 다시 묻는다
    old = time.time() - 2 * 3600
    os.utime(f, (old, old))
    kc.KisClient().get_investor_flow("005930.KS")
    assert len(calls) == 2
    # pending 이 없으면 12시간 캐시 그대로
    p = _inv_payload(head_unfilled=False)
    _router(monkeypatch, kc, {"FHKST01010900": p})
    f.unlink()
    kc.KisClient().get_investor_flow("005930.KS")
    os.utime(f, (old, old))
    c2 = _router(monkeypatch, kc, {"FHKST01010900": p})
    kc.KisClient().get_investor_flow("005930.KS")
    assert c2 == [], c2


# ─── 단일 수집 함수 · 두 호출부 E2E ──────────────────────────────────────────

def test_collect_is_empty_without_keys_and_asks_nothing(monkeypatch, kc):
    monkeypatch.setattr(kc, "_app_key", lambda: "")
    calls = _router(monkeypatch, kc, _ok_table())
    assert kc.collect_kis_flow("005930.KS") == {} and calls == []


def test_collect_isolates_one_failure(monkeypatch, kc, caplog):
    _router(monkeypatch, kc, _ok_table())

    def boom(self, ticker):
        raise RuntimeError("short boom")

    monkeypatch.setattr(kc.KisClient, "get_short_sale", boom)
    with caplog.at_level(logging.WARNING, logger="bot.kis"):
        out = kc.collect_kis_flow("005930.KS")
    assert set(out) == {"investor_flow", "credit", "program"}, set(out)
    assert any("short_sale" in r.getMessage() and "short boom" in r.getMessage()
               for r in caplog.records)


def test_snapshot_enrich_fills_kis_flow(monkeypatch, kc):
    """스냅샷 경로(``_enrich_kr`` → ``_t_flow``)가 실제로 KIS 칸을 채운다 — 옛 판은
    없는 메서드 호출로 한 번도 안 돌았다."""
    import bot.stock_snapshot as ss
    _router(monkeypatch, kc, _ok_table())
    snap: dict = {}
    ss._enrich_kr("005930.KS", snap)
    flow = snap["kr"]["flow"]
    assert flow["investor_flow"]["schema"] == kc._FLOW_SCHEMA
    assert flow["credit"]["credit_short_shares"] == 35
    assert flow["program"]["unit_won"] == 1_000_000


def test_detail_enrichment_fills_kis_flow(monkeypatch, kc):
    import bot.dashboard as dash
    _router(monkeypatch, kc, _ok_table())
    si = {"news": [1], "kr": {"research_reports": [1]}}
    dash._ensure_detail_enrichment("005930.KS", si)
    assert si["kr"]["flow"]["investor_flow"]["asof"] == "2026-10-01"


def test_no_call_site_reaches_for_a_missing_ready_method():
    """옛 사본 둘이 부르던 ``_ready()`` 는 어디서도 다시 나오면 안 된다(이름은 아래
    전수 가드가 일반형으로 막는다)."""
    for rel in ("bot/stock_snapshot.py", "bot/dashboard.py"):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        bad = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and n.attr == "_ready"]
        assert bad == [], (rel, bad)


# ─── 화면 ────────────────────────────────────────────────────────────────────

def _flow_pane(monkeypatch, flow):
    import bot.dashboard as dash
    monkeypatch.setattr("bot.pykrx_client.get_kr_multi_period_trends", lambda t, **kw: None)
    si = {"currency": "KRW", "kr": {"flow": flow}}
    seg = dash._render_stock_info_html({"ticker": "005930.KS", "stock_info": si})["other_panes"]
    i = seg.index('id="si-flow"')
    return seg[i:seg.index("</div>\n</div>", i) + 13]


def test_flow_pane_shows_dates_units_and_no_fabricated_sections(monkeypatch, kc):
    flow = {"investor_flow": kc.parse_investor_flow(_inv_payload()),
            "credit": kc.parse_credit_balance(_credit_payload()),
            "short_sale": kc.parse_short_sale(_short_payload()),
            "program": kc.parse_program_daily(_prog_payload())}
    pane = _flow_pane(monkeypatch, flow)
    assert "투자자별 순매수 (KIS)" in pane
    assert "2026-10-01 수량" in pane and "2026-10-01 금액" in pane
    assert "5거래일 누적" in pane and "2026-09-25~2026-10-01" in pane
    assert "억</td>" in pane and "주</td>" in pane
    assert "원천이 아직 채우지 않은 최근 1일" in pane
    assert "대주잔고 (신용 매도, 주)" in pane and ">35<" in pane
    assert "프로그램 순매수 (KIS · 전체 합계)" in pane
    # 옛 판이 지어 그리던 칸은 없다 — 각주의 '차익·비차익을 나누지 않습니다' 는 그 사실을
    # 말하는 문장이라 낱말이 아니라 **행 이름**으로 잰다(#75)
    for gone in ("기관 세부", "비차익 순매수", "<td>차익 순매수", "대차잔고"):
        assert gone not in pane, gone


def test_flow_pane_skips_old_shapes(monkeypatch, kc):
    """옛 모양(수량을 만원으로 읽던 판)은 그리지 않는다 — 그리면 틀린 숫자다."""
    old = {"investor_flow": {"today": {"foreign": 5}, "5d": {"foreign": 9},
                             "inst_breakdown": {"pension": 1}},
           "program": {"arb_net": 1, "nonarb_net": 2},
           "credit": {"credit_balance_pct": 1.0, "loan_balance_shares": 3}}
    pane = _flow_pane(monkeypatch, old)
    assert "투자자별 순매수 (KIS)" not in pane and "만</td>" not in pane
    assert "프로그램 순매수" not in pane and "신용·공매도 (KIS)" not in pane


def test_flow_pane_says_why_amounts_are_missing(monkeypatch, kc):
    p = _inv_payload(head_unfilled=False)
    for row in p["output"]:
        for k in list(row):
            if k.endswith(("shnu_tr_pbmn", "seln_tr_pbmn")):
                row[k] = "0"
    pane = _flow_pane(monkeypatch, {"investor_flow": kc.parse_investor_flow(p)})
    assert "금액 단위를 확정하지 못해" in pane and "5거래일 누적" not in pane


# ─── 분석 프롬프트 블록 ─────────────────────────────────────────────────────

def _flow_dict(kc, f5, i5, p5, *, unit=1_000_000):
    return {"schema": kc._FLOW_SCHEMA, "asof": "2026-10-01", "unit_won": unit, "unit_note": "",
            "latest": {"date": "2026-10-01", "qty": {"foreign": 1000, "institution": -5,
                                                     "individual": 3},
                       "won": {"foreign": 2 * 10**8, "institution": None, "individual": 0}},
            "window": {"from": "2026-09-25", "to": "2026-10-01", "days": 5,
                       "won": {"foreign": f5, "institution": i5, "individual": p5}},
            "pending": 1, "missing": []}


def test_prompt_block_dates_units_and_rule10_in_won(kc):
    txt = kc.format_kis_block({"investor_flow": _flow_dict(kc, 5 * 10**9, 2 * 10**10, 1)})
    assert "순매수 수량 (2026-10-01 하루): 외인 +1,000주" in txt
    assert "순매수 금액 (2026-10-01 하루): 외인 +2.00억원" in txt
    assert "5거래일 누적 (2026-09-25~2026-10-01): 외인 +50.00억원" in txt
    # RULE 10 noise 판정은 원 단위로 — 외인 50억은 noise, 기관 200억은 아니다
    assert "⚠️ RULE 10: 외인 5거래일 누적 +50.00억원" in txt
    assert "RULE 10: 기관" not in txt
    assert "원천이 아직 채우지 않은 최근 1일" in txt
    assert "당일 순매수" not in txt                        # 옛 라벨(날짜 없는 '당일')


def test_prompt_block_retail_support_pattern(kc):
    txt = kc.format_kis_block({"investor_flow": _flow_dict(kc, -12 * 10**9, None, 15 * 10**9)})
    assert "Retail 떠받침 패턴 — 개인 +150.00억원 vs 외인 -120.00억원" in txt
    quiet = kc.format_kis_block({"investor_flow": _flow_dict(kc, -12 * 10**9, None, 5 * 10**9)})
    assert "Retail" not in quiet


def test_prompt_block_without_unit_says_so(kc):
    d = _flow_dict(kc, 1, 1, 1, unit=None)
    d["unit_note"] = "표본 0개"
    txt = kc.format_kis_block({"investor_flow": d})
    assert "금액 단위를 확정하지 못해" in txt and "표본 0개" in txt
    assert "누적" not in txt.split("금액 단위")[0] and "억원" not in txt


def test_prompt_block_skips_old_shapes(kc):
    txt = kc.format_kis_block({
        "investor_flow": {"today": {"foreign": 11730}, "5d": {"foreign": 10_000_001}},
        "program_trade": {"arb_net": 1, "nonarb_net": 30_000_000},
        "credit_short": {"credit_balance_pct": 5.0, "loan_balance_shares": 9},
        "short_sale": {"short_ratio_pct": 20.0}})
    assert txt == "", txt


def test_prompt_block_program_credit_short(kc):
    txt = kc.format_kis_block({
        "program_trade": kc.parse_program_daily(_prog_payload()),
        "credit_short": kc.parse_credit_balance(_credit_payload()),
        "short_sale": kc.parse_short_sale(_short_payload())})
    assert "프로그램 순매수 (2026-10-01 하루, 전체 합계 — 차익·비차익 구분 없음)" in txt
    assert "프로그램 5거래일 누적 (2026-09-25~2026-10-01)" in txt
    assert "신용잔고율 (2026-10-01): 4.20% ⚠️ 신용 과열" in txt
    assert "대주잔고(신용 매도) (2026-10-01): 35주" in txt
    assert "공매도 비율 (거래량 대비, 2026-10-01): 16.4% ⚠️" in txt
    assert "비차익 " not in txt.replace("차익·비차익", "")


def test_guide_makes_no_claims_the_data_cannot_back(kc):
    g = kc.KIS_INTERP_GUIDE
    assert "fetch 완료" not in g                       # 한도소진율은 KIS 가 주지 않는다
    assert "전체 합계" in g and "기준일" in g
    for gone in ("연기금 5일 순매수 양수", "투신 5일 -50억", "프로그램 비차익 ±200억"):
        assert gone not in g, gone


# ─── 분석가 프롬프트 RULE 10 보강 — 규칙이 요구하는 수치를 실제로 찍는 블록이 있나 ─────
# 규칙 줄의 머리(→ 앞) 접두 → (그 수치를 찍는 블록, 그 블록 출력의 **한 줄**에 함께 있어야
# 하는 낱말들). ⚠️ 닫힌 세계다 — 이 표에 없는 머리로 시작하는 새 줄은 실패한다. 그 수치를
# 실제로 찍는 블록을 찾아 여기 적거나(라벨은 실제 포매터 출력으로 확인된다) 규칙을 뺄 것.
# 2026-10-04 까지 연기금·투신·프로그램 비차익 세 줄은 어느 블록도 안 찍는 수치를 '명시
# 의무' 로 요구했다(KIS 옛 판은 공식 응답에 없는 필드를 읽어 늘 None 이었다 — 실수 #433).
# 문자열 금지 목록은 표현만 바꾼 줄을 못 잡는다(#373) — 그래서 대응 표다. None = 수치를
# 요구하지 않는 줄(아래 테스트가 그 줄이 말하는 '없음' 이 사실인지 따로 잰다).
_RULE10_SOURCES = {
    "외인 5거래일 누적": ("kis", ("거래일 누적", "외인")),
    "신용잔고율": ("kis", ("신용잔고율",)),
    "공매도 비율(거래량 대비)": ("kis", ("공매도 비율", "거래량 대비")),
    # ⚠️ 콜론까지 — 95% 경고 줄(`⚠️ 한도소진율 95%+ …`)도 같은 낱말을 품어, 값 줄을 지워도
    # 경고 줄이 대신 만족시켰다(실측 뮤테이션 생존, #75).
    "[Step 2C] 외인 한도소진율(외국인 보유현황 블록)": ("seibro", ("한도소진율:",)),
    "[Step 2C] 개인 5거래일": ("kis", ("거래일 누적", "개인", "기관", "외인")),
    "주입된 블록에 없는 수치": None,
}


def _rule10_bullets() -> list:
    """분석가 프롬프트의 RULE 10 보강 줄들 — 인접 문자열 리터럴은 AST 에서 상수 하나로
    합쳐지므로 그 상수를 찾아 자른다(LLM 을 부르지 않고 실제 프롬프트 문구를 읽는다)."""
    src = (_REPO / "TradingAgents/tradingagents/agents/analysts/fundamentals_analyst.py"
           ).read_text(encoding="utf-8")
    texts = [n.value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and "RULE 10 보강 — KIS" in n.value]
    assert len(texts) == 1, len(texts)
    t = texts[0]
    seg = t[t.index("RULE 10 보강 — KIS"):t.index("위 기준에 해당하지 않는 경우")]
    return [ln.strip()[1:].strip() for ln in seg.splitlines() if ln.strip().startswith("•")]


def _unbacked_rule_bullets(bullets, outputs, sources=None) -> list:
    """규칙 줄 중 그 수치를 찍는 블록이 없는 것(순수). ``outputs`` = {블록: 출력 텍스트}."""
    sources = _RULE10_SOURCES if sources is None else sources
    bad = []
    for b in bullets:
        key = next((k for k in sources if b.startswith(k)), None)
        if key is None:
            bad.append(f"대응 표에 없는 규칙 줄: {b[:60]}")
            continue
        if sources[key] is None:
            continue
        block, words = sources[key]
        if not any(all(w in ln for w in words) for ln in (outputs.get(block) or "").splitlines()):
            bad.append(f"{block} 블록의 어느 줄도 {words} 를 함께 찍지 않는다: {b[:60]}")
    return bad


def _real_block_outputs(kc) -> dict:
    """프롬프트에 실제로 실리는 두 블록의 출력 — 공식 필드 이름 픽스처를 파서·포매터에 통째로 태운다."""
    from bot.seibro_client import format_foreign_holding_block
    kis = kc.format_kis_block({
        "investor_flow": kc.parse_investor_flow(_inv_payload()),
        "credit_short": kc.parse_credit_balance(_credit_payload()),
        "short_sale": kc.parse_short_sale(_short_payload()),
        "program_trade": kc.parse_program_daily(_prog_payload())})
    seibro = format_foreign_holding_block({
        "foreign_pct": 50.0, "foreign_shares": 5, "listed_shares": 10, "date": "2026-10-01",
        "limit_exhaustion_pct": 96.0, "limit_shares": 9})
    return {"kis": kis, "seibro": seibro}


def test_rule10_kis_rules_cite_only_numbers_some_block_prints(kc):
    """분석가 프롬프트 RULE 10 보강의 줄마다 그 수치를 실제로 찍는 블록이 있어야 한다 —
    없으면 '명시 의무' 가 지어내기를 부른다(실수 #433). 그리고 '없다' 고 말하는 줄은 정말
    어느 블록도 그 수치를 안 찍어야 참이다."""
    bullets = _rule10_bullets()
    assert len(bullets) >= 5, bullets          # 추출이 깨져 0줄이면 아무것도 안 잰다(#54)
    outs = _real_block_outputs(kc)
    assert _unbacked_rule_bullets(bullets, outs) == []
    absent = [b for b in bullets if b.startswith("주입된 블록에 없는 수치")]
    assert len(absent) == 1, absent
    for word in ("연기금", "투신"):
        assert word in absent[0], word
        assert all(word not in text for text in outs.values()), word
    assert "비차익" in absent[0]
    assert all("비차익 " not in text.replace("차익·비차익", "") for text in outs.values())


def test_rule10_contract_fires(kc):
    outs = _real_block_outputs(kc)
    # ① 대응 표에 없는 새 줄(옛 연기금 줄)은 실패한다
    assert _unbacked_rule_bullets(["연기금 5일 누적 순매수 양수 → '연기금 저점 지지' 명시."], outs)
    # ② 표에 있어도 그 블록이 라벨을 안 찍으면 실패한다 — 블록 출력이 비면
    assert _unbacked_rule_bullets(
        ["[Step 2C] 외인 한도소진율(외국인 보유현황 블록) 95% 이상 → '…' 명시."],
        dict(outs, seibro=""))
    # ③ 낱말들은 **한 줄**에 함께 있어야 한다 — 다른 줄에 흩어져 있으면 그 수치를 찍은 게 아니다
    assert _unbacked_rule_bullets(["공매도 비율(거래량 대비) 15% 이상 → …"],
                                  {"kis": "• 공매도 비율: 1%\n• 거래량 대비: x"})
    # ④ 정상 줄은 통과
    assert not _unbacked_rule_bullets(["신용잔고율 4% 이상 → …"], outs)


# ─── 감사 ────────────────────────────────────────────────────────────────────

def test_audit_tr_verdict_branches(kc):
    from bot.scripts import kis_flow_audit as au
    kw = dict(rows_key="output", date_field="stck_bsop_date",
              expected=au._INVESTOR_FIELDS, parser=kc.parse_investor_flow)
    no = au.tr_verdict("① 투자자", None, ["kis: FHKST01010900 rt_cd=1 msg=X"], **kw)
    assert no == ["❌ ① 투자자: 원천이 답하지 않았습니다 — kis: FHKST01010900 rt_cd=1 msg=X"]
    ok = au.tr_verdict("① 투자자", _inv_payload(), [], **kw)
    assert ok[-1].startswith("✅ ① 투자자: 금액 단위 ×1,000,000"), ok
    p = _inv_payload()
    for r in p["output"]:
        r.pop("prsn_ntby_qty")
    miss = au.tr_verdict("① 투자자", p, [], **kw)
    assert any(x.startswith("❌") and "prsn_ntby_qty" in x for x in miss), miss
    empty = au.tr_verdict("① 투자자", {"rt_cd": "0", "output": []}, [], **kw)
    assert empty[0].startswith("❌") and "0개" in empty[0]


def test_audit_market_code_verdict(kc):
    from bot.scripts import kis_flow_audit as au
    assert au.market_code_verdict({"J": (100, ""), "Q": (None, "x")})[-1].startswith("✅")
    bad = au.market_code_verdict({"J": (None, "rt_cd=1"), "Q": (100, "")})[-1]
    assert bad.startswith("❌") and "_mkt_div" in bad
    assert au.market_code_verdict({"J": (None, "a"), "Q": (None, "b")})[-1].startswith("❌")


def test_audit_main_reports_missing_keys(monkeypatch, kc, capsys):
    from bot.scripts import kis_flow_audit as au
    monkeypatch.setattr(kc, "_app_secret", lambda: "")
    assert au.main([]) == 1
    out = capsys.readouterr().out
    assert "❌ KIS 자격증명이 없습니다" in out and "kis_flow_audit v" in out


def test_audit_main_end_to_end(monkeypatch, kc, capsys):
    from bot.scripts import kis_flow_audit as au
    table = _ok_table()
    table["FHKST01010100"] = lambda p: ({"rt_cd": "0", "output": {"stck_prpr": "250000"}}
                                        if p["FID_COND_MRKT_DIV_CODE"] == "J" else None)
    calls = _router(monkeypatch, kc, table)
    assert au.main([]) == 0
    out = capsys.readouterr().out
    assert "❌" not in out, out
    assert "✅ ① 투자자" in out and "✅ ④ 프로그램(일별)" in out
    assert "✅ 시장 분류 코드: 코스닥에 J 를 받습니다" in out
    # 수급 캐시를 쓰지 않는다(원천을 직접 묻는 감사)
    assert not kc._CACHE_DIR.exists() or not any(kc._CACHE_DIR.iterdir())
    assert {c[1] for c in calls} >= {"FHKST01010900", "FHPST04760000", "FHPST04830000",
                                     "FHPPG04650201", "FHKST01010100"}
    # J 를 거절하면 ❌ 로 결산에 오른다
    table["FHKST01010100"] = lambda p: ({"rt_cd": "0", "output": {"stck_prpr": "250000"}}
                                        if p["FID_COND_MRKT_DIV_CODE"] == "Q" else None)
    _router(monkeypatch, kc, table)
    assert au.main(["247540.KQ"]) == 1
    assert "❌ 시장 분류 코드" in capsys.readouterr().out


def test_audit_is_registered_daily():
    from bot.audit_sweep import AUDITS
    assert dict((m, c) for _n, m, c in AUDITS)["bot.scripts.kis_flow_audit"] == "daily"


# ─── 전수 가드: 레포 클래스 인스턴스에서 없는 메서드를 부르는 자리 ─────────────

_PKGS = ("bot", "trade", "TradingAgents/tradingagents")


def _scan_missing_methods(root: Path, pkgs=_PKGS) -> list:
    """같은 함수 안에서 ``x = Cls(...)`` 또는 ``x = factory()``(반환 주석이 레포 클래스)
    로 만든 인스턴스에 그 클래스(기반 포함)에 없는 이름을 부르는 자리.

    ⚠️ 정확성보다 **오탐 없음**을 고른다 — 이름이 같은 함수 안에서 다른 방법으로도
    묶이면(루프·다른 대입·인자·with·예외), 기반이 레포 밖이거나 ``__getattr__`` 이
    있으면 판정하지 않는다. 그래서 못 보는 축: 인자로 받은 인스턴스·속성에 담긴
    인스턴스·컨테이너를 거친 인스턴스(#274)."""
    mods: dict = {}
    for pkg in pkgs:
        base = root / pkg
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            if "/tests/" in rel or p.name.startswith("test_"):
                continue
            dotted = rel[:-3].replace("TradingAgents/", "").replace("/", ".")
            if dotted.endswith(".__init__"):
                dotted = dotted[:-9]
            try:
                mods[dotted] = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

    def resolve(mod, name, depth=0):
        tree = mods.get(mod)
        if tree is None or depth > 6:
            return None
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and n.name == name:
                return ("class", mod, n)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return ("func", mod, n)
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for a in n.names:
                    if (a.asname or a.name) == name:
                        return resolve(n.module, a.name, depth + 1)
        return None

    def members(mod, cls, depth=0):
        if depth > 8:
            return None
        out = set()
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name in ("__getattr__", "__getattribute__"):
                    return None
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                out.add(n.attr)
        for b in cls.bases:
            if isinstance(b, ast.Name) and b.id == "object":
                continue
            if not isinstance(b, ast.Name):
                return None
            r = resolve(mod, b.id)
            if not r or r[0] != "class":
                return None
            sub = members(r[1], r[2], depth + 1)
            if sub is None:
                return None
            out |= sub
        return out

    def class_of(mod, local_imports, call):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            return None
        nm = call.func.id
        r = resolve(*local_imports[nm]) if nm in local_imports else resolve(mod, nm)
        if not r:
            return None
        if r[0] == "class":
            return r
        ann = r[2].returns
        an = ann.id if isinstance(ann, ast.Name) else (
            ann.value if isinstance(ann, ast.Constant) and isinstance(ann.value, str) else None)
        if an:
            rr = resolve(r[1], an)
            if rr and rr[0] == "class":
                return rr
        return None

    def own_nodes(fn):
        """함수 자신의 스코프 노드만(중첩 함수·람다·클래스·컴프리헨션 본문 제외)."""
        stack = list(fn.body) + list(fn.args.args) + list(fn.args.kwonlyargs)
        if fn.args.vararg:
            stack.append(fn.args.vararg)
        if fn.args.kwarg:
            stack.append(fn.args.kwarg)
        while stack:
            n = stack.pop()
            yield n
            for c in ast.iter_child_nodes(n):
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                  ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp,
                                  ast.GeneratorExp)):
                    continue
                stack.append(c)

    hits = []
    for mod, tree in mods.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(own_nodes(fn))
            local_imports = {}
            for n in nodes:
                if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                    for a in n.names:
                        local_imports[a.asname or a.name] = (n.module, a.name)
            made: dict = {}
            other: set = set()
            for n in nodes:
                if isinstance(n, ast.arg):
                    other.add(n.arg)
                elif (isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name)):
                    r = class_of(mod, local_imports, n.value)
                    if r:
                        made.setdefault(n.targets[0].id, []).append(r)
                    else:
                        other.add(n.targets[0].id)
                elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    pass  # 아래에서 대입 외 묶임을 따로 센다
            for n in nodes:
                tgt = []
                if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                    tgt = [n.target]
                elif isinstance(n, ast.Assign) and not (
                        len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)):
                    tgt = n.targets
                elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                    tgt = [n.target]
                elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                    tgt = [n.optional_vars]
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    other.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    other |= {(a.asname or a.name).split(".")[0] for a in n.names}
                for t in tgt:
                    other |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
            bound = {k: v[0] for k, v in made.items()
                     if k not in other and len({id(x[2]) for x in v}) == 1}
            for n in nodes:
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name) and n.func.value.id in bound):
                    _, cm, cls = bound[n.func.value.id]
                    mem = members(cm, cls)
                    if mem is not None and n.func.attr not in mem:
                        hits.append(f"{mod}:{n.lineno} {n.func.value.id}.{n.func.attr}() "
                                    f"— {cm}.{cls.name} 에 없음")
    return sorted(set(hits))


def test_no_calls_to_methods_missing_on_repo_class_instances():
    """레포 클래스의 인스턴스에 그 클래스에 없는 메서드를 부르면 AttributeError 가
    대개 넓은 ``except`` 에 삼켜져 기능이 조용히 죽는다 — 수급 탭 KIS 칸이 그렇게 1년
    넘게 비어 있었다(실수 #433). 이름 열거가 아니라 레포 전수로 잰다(#24)."""
    hits = _scan_missing_methods(_REPO)
    assert hits == [], "\n".join(hits)


def test_missing_method_scanner_fires_and_spares(tmp_path):
    pkg = tmp_path / "bot"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text(
        "class Base:\n"
        "    def inherited(self):\n        return 1\n"
        "class Client(Base):\n"
        "    def __init__(self):\n        self.cb = None\n"
        "    def real(self):\n        return 1\n"
        "class Dyn:\n"
        "    def __getattr__(self, k):\n        return lambda: 0\n"
        "def get_client() -> Client:\n    return Client()\n", encoding="utf-8")
    (pkg / "use.py").write_text(
        "from bot.cli import Client, Dyn, get_client\n"
        "def bad():\n    c = Client()\n    return c._ready()\n"            # 발화
        "def bad_factory():\n    k = get_client()\n    return k.nope()\n"  # 발화(반환 주석)
        "def ok_inherited():\n    c = Client()\n    return c.inherited() + c.real()\n"
        "def ok_attr_callable():\n    c = Client()\n    return c.cb()\n"
        "def ok_dynamic():\n    d = Dyn()\n    return d.anything()\n"
        "def ok_rebound(xs):\n    c = Client()\n    for c in xs:\n        c.get()\n"
        "def ok_local_import():\n    from bot.cli import Client as C\n"
        "    c = C()\n    return c.real()\n"
        "def bad_local_import():\n    from bot.cli import Client as C\n"
        "    c = C()\n    return c.ghost()\n", encoding="utf-8")
    hits = _scan_missing_methods(tmp_path, ("bot",))
    names = sorted(h.split(" ")[1] for h in hits)
    assert names == ["c._ready()", "c.ghost()", "k.nope()"], hits
