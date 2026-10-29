"""가치평가 랩 (2026-10-22) — 결정적 DCF·역DCF·몬테카를로·풋볼필드.

계산은 손으로 검산할 수 있는 값으로 못박고(#33), '만들 수 없는' 갈래마다
사유가 화면에 실리는지 본다(#43·#131).
"""

from __future__ import annotations

import pytest

from bot import valuation_lab as vl


def _snap(**over):
    s = {
        "currency": "USD", "financial_currency": "USD",
        "current_price": 100.0, "shares_outstanding": 1e9, "market_cap": 1e11,
        "sector": "Technology", "industry": "Semiconductors",
        "fiftyTwoWeekLow": 70.0, "fiftyTwoWeekHigh": 130.0,
        "target_low": 90.0, "target_high": 150.0, "num_analysts": 20,
        "trailingEps": 4.0,
        "peer_comps": [
            {"ticker": "SELF", "is_subject": True, "trailingPE": 25.0},
            {"ticker": "A", "trailingPE": 15.0}, {"ticker": "B", "trailingPE": 20.0},
            {"ticker": "C", "trailingPE": 30.0}, {"ticker": "D", "trailingPE": 500.0},
        ],
        "financials": {
            # 스냅샷은 최신 먼저 — 모듈이 오름차순으로 다시 정렬한다.
            "cash_flow": {"annual": [
                {"period": "2025-12-31", "Operating Cash Flow": 6e9,
                 "Capital Expenditure": -1e9},
                {"period": "2024-12-31", "Operating Cash Flow": 5e9,
                 "Capital Expenditure": -1e9},
                {"period": "2023-12-31", "Free Cash Flow": 3.5e9},
            ]},
            "income_statement": {"annual": [
                {"period": "2025-12-31", "Total Revenue": 30e9},
                {"period": "2024-12-31", "Total Revenue": 27e9},
                {"period": "2023-12-31", "Total Revenue": 25e9},
                {"period": "2022-12-31", "Total Revenue": 22e9},
            ]},
            "balance_sheet": {
                "annual": [{"period": "2025-12-31", "Total Debt": 9e9,
                            "Cash And Cash Equivalents": 1e9}],
                "quarterly": [{"period": "2026-06-30", "Total Debt": 5e9,
                               "Cash And Cash Equivalents": 2e9}],
            },
        },
    }
    s.update(over)
    return s


def test_dcf_matches_hand_calculation():
    # FCF 50억 · 성장 0 · 할인 10% · 영구 2%:
    # 5년 연금 5e9×3.790787 = 18.954e9, 영구가치 5e9×1.02/0.08 = 63.75e9 →
    # 현가 39.584e9, 합 58.538e9 − 순차입금 3e9 = 55.538e9 / 1e9주.
    assert vl.dcf_per_share(5e9, 0.0, 0.10, 0.02, 3e9, 1e9) == pytest.approx(55.538, abs=1e-3)
    assert vl.dcf_per_share(5e9, 0.0, 0.02, 0.02, 0, 1e9) is None    # r ≤ g 발산


def test_reverse_dcf_round_trips():
    g, how = vl.implied_growth(80.0, 5e9, 0.09, 0.02, 3e9, 1e9)
    assert how == "" and vl.dcf_per_share(5e9, g, 0.09, 0.02, 3e9, 1e9) == pytest.approx(80.0, rel=1e-6)
    assert vl.implied_growth(1e6, 5e9, 0.09, 0.02, 3e9, 1e9) == (None, "above")
    assert vl.implied_growth(-1e6, 5e9, 0.09, 0.02, 3e9, 1e9) == (None, "below")


def test_gather_uses_latest_fcf_and_quarterly_balance_sheet():
    d = vl.gather(_snap())
    assert d["fcf0"] == 5e9 and d["fcf0_period"] == "2025-12-31"   # 6e9 − |−1e9|
    assert [p for p, _ in d["fcf_hist"]] == ["2023-12-31", "2024-12-31", "2025-12-31"]
    assert d["net_debt"] == 3e9 and d["bs_period"] == "2026-06-30"  # 분기가 더 새롭다
    yrs = (1096) / 365.25                     # 2022-12-31 → 2025-12-31 (날짜로 센다)
    assert d["rev_cagr"] == pytest.approx((30 / 22) ** (1 / yrs) - 1)
    assert d["dcf_ok"] and d["why"] == []


def test_build_wires_grid_reverse_mc_and_football():
    p = vl.build(_snap())
    assert len(p["grid"]) == len(vl.DISCOUNT_GRID)
    assert all(len(r) == len(vl.GROWTH_GRID) for r in p["grid"])
    # 격자 한 칸을 단일 함수로 검산(배선이 인자를 섞지 않았나, #20).
    i, j = vl.DISCOUNT_GRID.index(0.10), vl.GROWTH_GRID.index(0.0)
    assert p["grid"][i][j] == pytest.approx(55.538, abs=1e-3)
    assert [r for r, *_ in p["reverse"]] == list(vl.DISCOUNT_GRID)
    mc = p["mc"]
    assert mc["p10"] < mc["p50"] < mc["p90"] and mc["n"] == vl.MC_N
    labels = [b[0] for b in p["football"]]
    assert labels[0] == "52주 범위" and labels[1] == "애널리스트 목표가"
    peer = p["football"][2]
    assert peer[1] == pytest.approx(17.5 * 4) and peer[2] == pytest.approx(25 * 4)  # 500·자기 제외
    assert labels[3].startswith("DCF 몬테카를로")


def test_monte_carlo_is_deterministic():
    a = vl.monte_carlo(5e9, 3e9, 1e9, 100.0, 0.08)
    b = vl.monte_carlo(5e9, 3e9, 1e9, 100.0, 0.08)
    assert a == b
    assert vl.monte_carlo(5e9, 3e9, 1e9, 100.0, 0.08, seed=1) != a


@pytest.mark.parametrize("over,needle", [
    ({"sector": "Financial Services"}, "금융업"),
    ({"financial_currency": "TWD"}, "재무제표 통화(TWD)"),
    ({"shares_outstanding": None}, "발행주식수"),
    ({"current_price": None}, "현재가"),
])
def test_cannot_build_says_why(over, needle):
    p = vl.build(_snap(**over))
    assert "grid" not in p and "mc" not in p
    assert any(needle in w for w in p["why"])
    html = vl.render_html(p)
    assert "DCF 를 만들지 않았습니다" in html and needle in html


def test_negative_latest_fcf_and_short_history():
    s = _snap()
    s["financials"]["cash_flow"]["annual"][0]["Operating Cash Flow"] = 5e8   # 5억−10억 < 0
    assert any("0 이하" in w for w in vl.build(s)["why"])
    s2 = _snap()
    s2["financials"]["cash_flow"]["annual"] = s2["financials"]["cash_flow"]["annual"][:1]
    assert any("1개 기간뿐" in w for w in vl.build(s2)["why"])


def test_missing_debt_or_cash_names_the_missing_item():
    s = _snap()
    for kind in ("annual", "quarterly"):
        for r in s["financials"]["balance_sheet"][kind]:
            r.pop("Total Debt")
    p = vl.build(s)
    assert any("총차입금" in w and "순차입금" in w for w in p["why"])


def test_currency_mismatch_also_drops_peer_bar():
    p = vl.build(_snap(financial_currency="TWD"))
    assert [b[0] for b in p["football"]] == ["52주 범위", "애널리스트 목표가"]


def test_peer_bar_needs_three_peers_and_positive_eps():
    assert vl.peer_per_range(_snap(), -1.0) is None
    few = _snap(peer_comps=[{"trailingPE": 10.0}, {"trailingPE": 12.0}])
    assert vl.peer_per_range(few, 4.0) is None


def test_render_marks_cells_against_price_and_escapes():
    p = vl.build(_snap())
    html = vl.render_html(p)
    assert "역DCF" in html and "민감도" in html and "몬테카를로" in html and "풋볼필드" in html
    assert "font-weight:700" in html and "color:var(--fg-soft)" in html  # 위·아래 둘 다
    assert "#26a69a" not in html and "#e2574c" not in html  # 매수·매도 색 아님
    assert "확률 아님" in html and "매수·매도 신호가 아닙니다" in html
    assert "자체계산" in html and "DART" in html             # 출처·탭 간 차이 각주
    # 원천 문자열이 사유에 실려도 이스케이프된다.
    h2 = vl.render_html(vl.build(_snap(financial_currency="<x>")))
    assert "&lt;x&gt;" in h2 and "<x>" not in h2


def test_krw_formats_use_won_units():
    s = _snap(currency="KRW", financial_currency="KRW", current_price=187000.0)
    html = vl.render_html(vl.build(s))
    assert "187,000 KRW" in html and "억원" in html


# ── 배선: 상세 페이지 탭·pane·JS · /api/vlab 핸들러 ─────────────────────────

def _render(ticker="AAPL"):
    from bot.dashboard import _render_stock_info_html
    return _render_stock_info_html({"ticker": ticker, "stock_info": _snap()})


def test_detail_page_has_tab_pane_and_script():
    parts = _render()
    assert 'data-pane="si-vlab"' in parts["tabs"]
    html = parts["other_panes"]
    i = html.index('id="si-vlab"')
    pane = html[i:html.find('<div class="si-pane"', i + 1)]
    assert 'id="si-vlab-body"' in pane and "api/vlab?ticker=" in pane
    assert "NOAH_TICKER" in pane                 # 주소는 지연 pane 공통 전역(#144)


def test_vlab_js_parses_and_fetches_on_tab_click(tmp_path):
    import shutil
    import subprocess
    from bot.dashboard import _VLAB_JS
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    harness = r"""
var calls=[], els={}, listeners=[];
function mk(id){ els[id]={id:id,textContent:'',innerHTML:'',style:{},classList:{contains:function(){return false;}}}; return els[id]; }
mk('si-vlab'); mk('si-vlab-status'); mk('si-vlab-body');
var document={getElementById:function(id){return els[id]||null;},
  createElement:function(){var o={textContent:''};Object.defineProperty(o,'innerHTML',{get:function(){return String(o.textContent).replace(/</g,'&lt;');}});return o;},
  addEventListener:function(t,f){listeners.push(f);} };
var window={}; var NOAH_BASE='../'; var NOAH_TICKER='AAPL';
function setTimeout(f){ f(); }
function fetch(u){ calls.push(u);
  return Promise.resolve({json:function(){ return Promise.resolve({ok:true,html:'<b>LAB</b>'}); }}); }
""" + _VLAB_JS + r"""
listeners[0]({target:{closest:function(s){ return s.indexOf('si-vlab')>=0?{}:null; }}});
// pane 이 받는 도중 다시 주입돼도(core→full) 새 자리에 들어가야 한다.
mk('si-vlab-body'); mk('si-vlab-status');
setImmediate(function(){
  if(calls.length!==1||calls[0]!=='../api/vlab?ticker=AAPL') throw new Error('fetch '+JSON.stringify(calls));
  if(els['si-vlab-body'].innerHTML!=='<b>LAB</b>') throw new Error('body '+els['si-vlab-body'].innerHTML);
  listeners[0]({target:{closest:function(s){ return s.indexOf('si-vlab')>=0?{}:null; }}});
  if(calls.length!==1) throw new Error('refetched');   // 두 번째 클릭은 캐시
  console.log('OK');
});
"""
    f = tmp_path / "h.js"
    f.write_text(harness, encoding="utf-8")
    out = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr


def test_api_builds_from_snapshot_with_shared_derivation(monkeypatch):
    import bot.dashboard_server as ds
    import bot.stock_snapshot as ss
    import bot.dashboard as dash
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda t, **k: _snap())
    seen = []
    monkeypatch.setattr(dash, "_derive_missing_multiples",
                        lambda s: seen.append(1) or s)
    replies = []
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h.path = "/api/vlab?ticker=AAPL"
    h._reply_json = lambda status, body: replies.append((status, body))
    h._handle_vlab_api()
    assert replies[0][0] == 200 and replies[0][1]["ok"] and "역DCF" in replies[0][1]["html"]
    assert seen == [1]                       # 밸류에이션 탭과 같은 파생을 탄다(#239)


def test_api_rejects_bad_ticker_and_says_why_without_snapshot(monkeypatch):
    import bot.dashboard_server as ds
    import bot.stock_snapshot as ss
    replies = []
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h._reply_json = lambda status, body: replies.append((status, body))
    h.path = "/api/vlab?ticker=../x"
    h._handle_vlab_api()
    assert replies[-1][0] == 400
    # 스냅샷 모양이 다르면 사람 말로 갈래를 준다(예외를 'internal' 로 삼키지 않는다).
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda t, **k: {"financials": []})
    import bot.valuation_lab as vlm
    monkeypatch.setattr(vlm, "build", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    h.path = "/api/vlab?ticker=YYYY"
    h._handle_vlab_api()
    assert replies[-1][0] == 200 and "형식이 예상과 달라" in replies[-1][1]["error"]
    # 2026-10-29 계약 갱신(#222): 옛 판은 "받지 못했습니다" 만 말해 갈래가 안
    # 보였다 — 이제 사유와 '저장분도 없음' 을 같이 말한다(아래 회귀).
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda t, **k: None)
    import bot.dashboard as dash
    monkeypatch.setattr(dash, "_load_stored_stock_info_dated", lambda t: (None, ""))
    h.path = "/api/vlab?ticker=ZZZZ"
    h._handle_vlab_api()
    assert replies[-1][0] == 200 and not replies[-1][1]["ok"]
    assert "종목 스냅샷을 받지 못했습니다" in replies[-1][1]["error"]


# ── 2026-10-29 "한국도 미국도 안 돼" — 스냅샷 실패 시 갈래 + 저장분 폴백 ─────

def _vlab_reply(monkeypatch, ticker, snap_fn, stored):
    import bot.dashboard_server as ds
    import bot.stock_snapshot as ss
    import bot.dashboard as dash
    monkeypatch.setattr(ss, "collect_stock_snapshot", snap_fn)
    monkeypatch.setattr(dash, "_load_stored_stock_info_dated", lambda t: stored)
    replies = []
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h.path = f"/api/vlab?ticker={ticker}"
    h._reply_json = lambda status, body: replies.append((status, body))
    h._handle_vlab_api()
    return replies[-1]


def test_vlab_falls_back_to_stored_snapshot_and_says_so(monkeypatch):
    import bot.stock_snapshot as ss
    monkeypatch.setitem(ss._LAST_FAIL, "009150.KS", "야후 종목 정보(.info)가 비어 왔습니다")
    st, body = _vlab_reply(monkeypatch, "009150.KS", lambda t, **k: None,
                           (_snap(), "2026-10-20"))
    assert st == 200 and body["ok"], body
    head = body["html"].split("</div>", 1)[0]
    assert "2026-10-20" in head and ".info" in head and "저장된 분석 스냅샷" in head
    assert "역DCF" in body["html"]


def test_vlab_failure_names_the_branch(monkeypatch):
    import bot.stock_snapshot as ss
    monkeypatch.setitem(ss._LAST_FAIL, "AAPL", "수집 중 예외(ConnectionError: x)")
    st, body = _vlab_reply(monkeypatch, "AAPL", lambda t, **k: None, (None, ""))
    assert st == 200 and not body["ok"]
    assert "ConnectionError" in body["error"] and "저장 분석" in body["error"]


def test_snapshot_records_and_clears_failure_reason(monkeypatch):
    import sys, types
    import bot.stock_snapshot as ss
    fake = types.ModuleType("yfinance")
    fake.Ticker = lambda t: types.SimpleNamespace(info={})
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.delitem(ss._LAST_FAIL, "QQQQ", raising=False)
    assert ss._collect_stock_snapshot_uncached("QQQQ") is None
    assert ".info" in ss.last_failure("QQQQ")

    def _boom(t):
        raise RuntimeError("blocked")
    fake.Ticker = _boom
    assert ss._collect_stock_snapshot_uncached("QQQQ") is None
    assert "RuntimeError" in ss.last_failure("QQQQ")
    ss._LAST_FAIL.pop("QQQQ", None)



# ── 독립 리뷰 2026-10-22 재현 ───────────────────────────────────────────────

def test_multi_class_share_count_is_replaced_by_market_cap_over_price():
    # GOOGL 처럼 한 클래스 주식수만 오면 주당가치가 클래스 비율만큼 부푼다.
    s = _snap(shares_outstanding=5e8)            # 시총 1e11 / 가격 100 = 1e9 주
    d = vl.gather(s)
    assert d["shares"] == pytest.approx(1e9) and "시가총액÷현재가" in d["shares_note"]
    p = vl.build(s)
    assert p["grid"][vl.DISCOUNT_GRID.index(0.10)][vl.GROWTH_GRID.index(0.0)] == \
        pytest.approx(55.538, abs=1e-3)
    assert "복수 클래스" in vl.render_html(p)


def test_kr_preferred_ticker_refuses_dcf():
    assert vl._is_kr_preferred("005935.KS") and vl._is_kr_preferred("00680K.KS")
    assert not vl._is_kr_preferred("005930.KS") and not vl._is_kr_preferred("AAPL")
    p = vl.build(_snap(currency="KRW", financial_currency="KRW"), "005935.KS")
    assert any("우선주" in w for w in p["why"]) and "grid" not in p


def test_financial_amounts_use_statement_currency():
    h = vl.render_html(vl.build(_snap(financial_currency="TWD")))
    assert "5.00B TWD" in h and "5.00B USD" not in h       # FCF·순차입금은 재무 통화
    assert "100.00 USD" in h                               # 현재가는 거래 통화


def test_revenue_cagr_counts_years_by_date_not_rows():
    s = _snap()
    s["financials"]["income_statement"]["annual"] = [
        {"period": "2025-12-31", "Total Revenue": 40e9},
        {"period": "2024-12-31", "Total Revenue": 30e9},
        {"period": "2021-12-31", "Total Revenue": 20e9},   # 2022·2023 빔
    ]
    g = vl.gather(s)["rev_cagr"]
    assert g == pytest.approx(2 ** (1 / (1461 / 365.25)) - 1)  # 약 18.9%, 행 기준이면 41%


def test_balance_sheet_falls_back_to_a_complete_row_and_debt_free():
    s = _snap()
    s["financials"]["balance_sheet"]["quarterly"] = [
        {"period": "2026-06-30", "Cash And Cash Equivalents": 2e9},          # 차입금 빈칸
        {"period": "2026-03-31", "Total Debt": 4e9, "Cash And Cash Equivalents": 1e9},
    ]
    d = vl.gather(s)
    assert d["net_debt"] == 3e9 and d["bs_period"] == "2026-03-31"
    s2 = _snap()
    s2["financials"]["balance_sheet"] = {"quarterly": [
        {"period": "2026-06-30", "Cash And Cash Equivalents": 2e9,
         "Total Liabilities Net Minority Interest": 5e9}]}
    d2 = vl.gather(s2)
    assert d2["net_debt"] == -2e9 and "무차입" in d2["claims"]["note"]
    s3 = _snap()
    s3["financials"]["balance_sheet"] = {"quarterly": [
        {"period": "2026-06-30", "Cash And Cash Equivalents": 2e9}]}   # 부채총계도 없음
    assert vl.gather(s3)["net_debt"] is None


def test_minority_interest_is_subtracted_and_labelled():
    s = _snap()
    s["financials"]["balance_sheet"]["quarterly"][0]["Minority Interest"] = 1e9
    d = vl.gather(s)
    assert d["net_debt"] == 4e9
    assert "비지배지분" in vl.render_html(vl.build(s))


def test_cash_item_priority():
    s = _snap()
    q = s["financials"]["balance_sheet"]["quarterly"][0]
    q["Cash Cash Equivalents And Short Term Investments"] = 3e9      # 앞 후보가 이긴다
    assert vl.gather(s)["net_debt"] == 2e9


def test_odd_snapshot_shapes_do_not_raise():
    for bad in ({"financials": []}, {"financials": {"cash_flow": []}},
                {"num_analysts": "x", "target_low": 1, "target_high": 2},
                {"num_analysts": float("nan"), "target_low": 1, "target_high": 2},
                {"peer_comps": "x"}, None, "x"):
        p = vl.build(bad)
        vl.render_html(p)
    s = _snap(shares_outstanding=-5.0, market_cap=None)
    assert any("0 이하" in w for w in vl.build(s)["why"])
    s2 = _snap()
    s2["financials"]["cash_flow"]["annual"][0]["Operating Cash Flow"] = float("inf")
    assert vl.gather(s2)["fcf0_period"] == "2024-12-31"           # inf 행은 버린다


def test_currency_text_is_escaped():
    h = vl.render_html(vl.build(_snap(currency="<i>", financial_currency="<i>")))
    assert "<i>" not in h and "&lt;i&gt;" in h


def test_football_axis_is_capped_and_negative_clipped():
    p = vl.build(_snap(target_low=10.0, target_high=2000.0))
    h = vl._football_html(p["football"], 100.0, "USD")
    assert "▶ 2,000.00" in h and "현재가 4배" in h
    h2 = vl._football_html([("DCF", -50.0, 80.0, "x")], 100.0, "USD")
    assert "음수는 0 에서 자름" in h2


def test_growth_center_clamps_and_monte_carlo_uses_all_draws():
    assert vl.growth_center(0.5)[0] == vl.G_MU_CLIP[1]
    assert vl.growth_center(-0.5)[0] == vl.G_MU_CLIP[0]
    mc = vl.monte_carlo(5e9, 3e9, 1e9, 100.0, 0.08)
    # 손으로 세기 — 같은 시드로 다시 뽑아 현재가 초과 비율과 분위수를 검산한다.
    import random
    rng = random.Random(vl.MC_SEED)
    vals = []
    for _ in range(vl.MC_N):
        g = min(max(rng.gauss(0.08, vl.MC_G_SD), vl.MC_G_CLIP[0]), vl.MC_G_CLIP[1])
        r = rng.uniform(*vl.MC_R)
        tg = rng.uniform(*vl.MC_TG)
        vals.append(vl.dcf_per_share(5e9, g, r, tg, 3e9, 1e9))
    vals.sort()
    assert mc["p_above"] == pytest.approx(sum(v > 100.0 for v in vals) / len(vals))
    k = (len(vals) - 1) * 0.10
    assert mc["p10"] == pytest.approx(vals[int(k)] + (vals[int(k) + 1] - vals[int(k)]) * (k - int(k)))


def test_industry_only_bank_is_refused():
    p = vl.build(_snap(sector="", industry="Banks - Regional"))
    assert any("금융업" in w for w in p["why"])


def test_reverse_dcf_out_of_range_labels():
    p = vl.build(_snap(current_price=1e6, market_cap=1e15))
    h = vl.render_html(p)
    assert "+100% 초과" in h
    p2 = vl.build(_snap(current_price=0.01, market_cap=1e7))
    assert "−50% 미만에서도 현재가보다 높음" in vl.render_html(p2)


def test_football_axis_starts_at_zero_when_a_bar_is_negative():
    # 음수 주당가치는 0 에서 자른다 — 축이 음수로 늘어나 현재가 선이 밀리지 않는다.
    h = vl._football_html([("DCF", -50.0, 80.0, "x")], 100.0, "USD")
    assert "left:95.24%;top:-3px" in h          # 축 0~105 에서 현재가 100 의 자리


# ── 델타 검증 2026-10-22 재현 ───────────────────────────────────────────────

def test_partially_empty_latest_quarter_is_not_read_as_debt_free():
    s = _snap()
    s["financials"]["balance_sheet"] = {"quarterly": [
        {"period": "2026-06-30", "Cash And Cash Equivalents": 2e9,
         "Total Liabilities Net Minority Interest": 2e10},           # 차입금 칸 빔
        {"period": "2026-03-31", "Total Debt": 5e9, "Cash And Cash Equivalents": 2e9},
    ]}
    d = vl.gather(s)
    assert d["net_debt"] == 3e9 and d["bs_period"] == "2026-03-31"
    assert "무차입" not in (d["claims"].get("note") or "")


def test_dual_listed_hk_cn_is_refused():
    p = vl.build(_snap(currency="CNY", financial_currency="CNY",
                       market_cap_note="거래 클래스 기준"), "002594.SZ")
    assert any("다른 시장에도 상장" in w for w in p["why"]) and "grid" not in p
    # 각주가 없으면(이중상장 아님) 그대로 만든다.
    assert "grid" in vl.build(_snap(currency="CNY", financial_currency="CNY"), "600519.SS")


def test_kr_common_with_listed_preferred_is_footnoted(monkeypatch):
    import bot.market as m
    monkeypatch.setattr(m, "has_kr_preferred_shares", lambda t: True)
    s = _snap(currency="KRW", financial_currency="KRW")
    p = vl.build(s, "005930.KS")
    assert "grid" in p and "우선주 주식수가 빠졌습니다" in vl.render_html(p)


@pytest.mark.parametrize("shares,ok,replaced", [
    (5e8, True, True),          # 시총÷현재가가 2배 — 한 클래스만 센 방향, 교체
    (1.08e9, False, False),     # 시총÷현재가가 8% 작음 — 낡은 시총일 수 있다, 거부
    (1e8, False, False),        # 10배 — 단위·시점 문제, 거부
    (1.02e9, True, False),      # 5% 안 — 그대로
])
def test_share_replacement_direction_and_bounds(shares, ok, replaced):
    p = vl.build(_snap(shares_outstanding=shares))
    assert ("grid" in p) == ok
    d = p["inputs"]
    assert (d["shares"] == pytest.approx(1e9)) == replaced or not replaced
    if not ok:
        assert any("어긋나" in w for w in p["why"])


def test_bar_entirely_above_axis_stays_in_track():
    h = vl._football_html([("목표가", 500.0, 600.0, "x")], 100.0, "USD")
    assert "left:99.40%;width:0.60%" in h


def test_dropped_infinite_fcf_year_is_said():
    s = _snap()
    s["financials"]["cash_flow"]["annual"][0]["Operating Cash Flow"] = float("inf")
    h = vl.render_html(vl.build(s))
    assert "FCF 값을 읽을 수 없는 해(2025-12-31)" in h


def test_52w_bar_requires_ordered_range():
    p = vl.build(_snap(fiftyTwoWeekLow=130.0, fiftyTwoWeekHigh=70.0))
    assert "52주 범위" not in [b[0] for b in p["football"]]
