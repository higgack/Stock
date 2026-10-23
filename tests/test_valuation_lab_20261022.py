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
    assert d["rev_cagr"] == pytest.approx((30 / 22) ** (1 / 3) - 1)
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
    assert "#26a69a" in html and "#e2574c" in html         # 현재가 위·아래 둘 다 있다
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
function fetch(u){ calls.push(u); return {then:function(f){ var r=f({json:function(){return {ok:true,html:'<b>LAB</b>'};}});
  return {then:function(g){ g({ok:true,html:'<b>LAB</b>'}); return {catch:function(){}}; }}; }}; }
""" + _VLAB_JS + r"""
listeners[0]({target:{closest:function(s){ return s.indexOf('si-vlab')>=0?{}:null; }}});
if(calls.length!==1||calls[0]!=='../api/vlab?ticker=AAPL') throw new Error('fetch '+JSON.stringify(calls));
if(els['si-vlab-body'].innerHTML!=='<b>LAB</b>') throw new Error('body '+els['si-vlab-body'].innerHTML);
listeners[0]({target:{closest:function(s){ return s.indexOf('si-vlab')>=0?{}:null; }}});
if(calls.length!==1) throw new Error('refetched');   // 두 번째 클릭은 캐시
console.log('OK');
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
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda t, **k: None)
    h.path = "/api/vlab?ticker=ZZZZ"
    h._handle_vlab_api()
    assert replies[-1] == (200, {"ok": False, "error": "종목 스냅샷을 받지 못했습니다"})
