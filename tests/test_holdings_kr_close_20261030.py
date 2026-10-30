"""🗞️ 보유종목 News — 국내 종가를 KRX 로 먼저(사용자 2026-10-30 "장은 15:30 분에 끝났고 지금은 17:20 분인데…").

TradingView 하루치는 마감 3시간 뒤(18:30)에 받아, 보유 화면이 그때까지 어제 종가에 머물렀다. 국내만 KRX 전종목 종가
(pykrx 1콜)를 마감 30분 뒤 봇 주기가 받아 두고, 렌더는 그 세션의 하루치가 **아직 없을 때만** 그 표를 쓴다. 재는 것:
받는 시각(마감+30분 전엔 안 묻는다) · 한 번 받으면 다시 안 묻는다 · 반쪽·장전 응답은 굽지 않는다 · 자격증명이 없으면
안 묻는다 · 렌더가 KRX 종가로 세고 '시세 반영' 이 그렇게 밝힌다 · 하루치가 오면 하루치가 이긴다 · KRX 에 없는 종목(ETF)은
Yahoo 로 · 대기 문구의 시각은 봇 기록이 정한다(약속 금지, #380) · 봇 주기가 Yahoo 보충 **전에** 부른다. 원천은 가짜."""
from __future__ import annotations

import json

import pytest

from tests.test_holdings_news_20261012 import _t, hn, ws_snaps  # noqa: F401 — 픽스처는 이름으로 잡힌다

_D = "2026-10-08"            # 목요일 · 정규장 15:30 마감


def _rows(n=1200, **extra):
    """KRX 전종목 응답 흉내 — {코드: [등락%, 종가]}. `extra` 로 특정 종목 값을 심는다."""
    out = {f"{i:06d}": [0.5, 1000.0 + i] for i in range(100000, 100000 + n)}
    out.update({k: list(v) for k, v in extra.items()})
    return out


@pytest.fixture
def krx(hn, monkeypatch):
    """자격증명 있음 + 호출 기록. `krx.resp` 를 바꿔 응답을 고른다."""
    import bot.pykrx_client as pc
    monkeypatch.setattr(pc, "krx_login_ready", lambda: True)

    class K:
        calls: list = []
        resp = (_rows(**{"000001": (3.0, 51000.0)}), "")

        def __call__(self, ds):
            self.calls.append(ds)
            # 앞 세션들은 하루치를 놓친 날 보충 대상이라 같이 묻는다 — 그 세션들은 '없음' 으로 답해 이 파일이 오늘만 재게
            return self.resp if ds == _D.replace("-", "") else ({}, "다른 세션")

        def today(self):
            return self.calls.count(_D.replace("-", ""))
    k = K()
    k.calls = []
    return k


def test_asks_only_after_close_plus_margin_and_only_once(hn, ws_snaps, krx):
    hn.refresh_kr_close(_t(f"{_D}T15:55"), fetch=krx)            # 15:30 + 30분 전 — 오늘 세션은 안 묻는다
    assert krx.today() == 0
    r = hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    assert r["got"] == 1 and krx.today() == 1
    saved = json.loads((hn._kr_close_dir() / f"{_D}.json").read_text(encoding="utf-8"))
    assert saved["rows"]["000001"] == [3.0, 51000.0]
    hn.refresh_kr_close(_t(f"{_D}T16:16"), fetch=krx)            # 이미 받았다 — 다시 안 묻는다
    assert krx.today() == 1


def test_session_with_tradingview_snapshot_is_not_asked(hn, ws_snaps, krx):
    ws_snaps("KR", _D, [("KRX:000001", 1.0)])
    hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    assert krx.today() == 0


@pytest.mark.parametrize("resp, frag", [
    ((_rows(n=50), ""), "50종목뿐"),                                          # 반쪽(코스닥 누락 등)
    (({k: [0.0, v[1]] for k, v in _rows().items()}, ""), "전부 0"),          # 장전 placeholder 모양
    (({}, "get_market_ohlcv_by_ticker 응답 비어 있음"), "비어 있음"),
])
def test_partial_or_placeholder_response_is_not_baked_and_retries_later(hn, ws_snaps, krx, resp, frag):
    krx.resp = resp
    r = hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    assert r["got"] == 0 and frag in hn._kr_close_state()["days"][_D]["why"] and not (hn._kr_close_dir() / f"{_D}.json").exists()
    hn.refresh_kr_close(_t(f"{_D}T16:10"), fetch=krx)            # 15분 안 — 다시 안 묻는다
    assert krx.today() == 1
    hn.refresh_kr_close(_t(f"{_D}T16:17"), fetch=krx)
    assert krx.today() == 2


def test_without_credentials_nothing_is_asked(hn, ws_snaps, krx, monkeypatch):
    import bot.pykrx_client as pc
    monkeypatch.setattr(pc, "krx_login_ready", lambda: False)
    r = hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    assert krx.calls == [] and "자격증명" in r["why"]
    assert hn.kr_early_lag(_D) is None                            # 대기 문구는 하루치 시각 그대로


def test_render_counts_the_session_with_krx_close_and_says_so(hn, ws_snaps, krx):
    ws_snaps("KR", "2026-10-06", [("KRX:000001", 1.0)])
    ws_snaps("KR", "2026-10-07", [("KRX:000001", 2.0)])
    base = _t("2026-10-05T18:00").timestamp()
    st = {"market": "KR", "ticker": "000001"}
    before = hn.cum_lookup(base, _t(f"{_D}T16:05"))(st)
    assert before["asof"] == "2026-10-07" and before["wait"] == [_D] and "krx" not in before
    hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    c = hn.cum_lookup(base, _t(f"{_D}T16:05"))(st)
    assert c["asof"] == _D and c["wait"] == [] and c["krx"] == [_D]
    assert c["ka"] == pytest.approx(1.01 * 1.02 * 1.03) and c["px"] == 51000.0 and c["d1"] == 3.0
    ca = hn.close_asof([{"market": "KR", "key": "k"}], {"k": c})
    assert hn.asof_line(ca) == "한국 10-08(KRX 종가)" and hn.asof_wait_note(ca) == ""
    assert "KRX 종가로 셈" in hn._close_tip({**c, "at": "10-05 18:00"})
    # 하루치가 오면 하루치가 이긴다 — 원천 표기도 빠진다
    ws_snaps("KR", _D, [("KRX:000001", 4.0)])
    c2 = hn.cum_lookup(base, _t(f"{_D}T18:40"))(st)
    assert c2["ka"] == pytest.approx(1.01 * 1.02 * 1.04) and "krx" not in c2
    assert hn.asof_line(hn.close_asof([{"market": "KR", "key": "k"}], {"k": c2})) == "한국 10-08"


def test_previous_day_change_also_reads_the_krx_close(hn, ws_snaps, krx):
    """'전날 등락' 칸도 같은 표를 쓴다 — 한쪽만 KRX 를 읽으면 같은 화면이 두 날을 말한다(#38)."""
    hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    look = hn.prev_changes("2026-10-09", _t("2026-10-09T08:00"))
    assert look({"market": "KR", "ticker": "000001"}) == (3.0, _D, "")
    assert look.px({"market": "KR", "ticker": "000001"}) == (51000.0, _D)


def test_stock_missing_from_krx_falls_back_to_yahoo_at_the_same_time(hn, ws_snaps, krx):
    """KRX 주식 표에 없는 종목(ETF 등)은 Yahoo 일봉으로 같은 세션까지 — 받을 시각은 마감 30분 뒤."""
    ws_snaps("KR", "2026-10-07", [("KRX:000001", 2.0)])
    hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)
    c = hn.cum_lookup(_t("2026-10-06T18:00").timestamp(), _t(f"{_D}T16:05"))({"market": "KR", "ticker": "069500"})
    assert c["src"] == "yahoo" and c["need"]["to"] == _D
    assert c["need"]["ready"] == _t(f"{_D}T16:00").timestamp()


def test_wait_note_promises_the_krx_time_only_when_the_bot_can_keep_it(hn, ws_snaps, krx):
    ws_snaps("KR", "2026-10-07", [("KRX:000001", 2.0)])
    base, st = _t("2026-10-06T18:00").timestamp(), {"market": "KR", "ticker": "000001"}

    def note(now):
        c = hn.cum_lookup(base, _t(now))(st)
        return hn.asof_wait_note(hn.close_asof([{"market": "KR", "key": "k"}], {"k": c}))
    assert "마감 3시간 뒤" in note(f"{_D}T15:45")                 # 봇 기록이 아직 없다(배포 직후) — 하루치 시각
    hn.refresh_kr_close(_t(f"{_D}T15:40"), fetch=krx)              # 자격증명 확인만(아직 묻지 않음)
    assert "마감 30분 뒤" in note(f"{_D}T15:45")
    krx.resp = ({}, "응답 비어 있음")
    hn.refresh_kr_close(_t(f"{_D}T16:01"), fetch=krx)              # 실패 — 이제 하루치가 채운다는 게 사실
    assert "마감 3시간 뒤" in note(f"{_D}T16:05")


def test_bot_tick_asks_krx_before_the_yahoo_topup(hn, monkeypatch):
    order = []
    monkeypatch.setattr(hn, "tick", lambda now=None: {})
    monkeypatch.setattr(hn, "refresh_kr_close", lambda now=None: order.append("krx") or {"asked": 0, "got": 0, "why": ""})
    monkeypatch.setattr(hn, "refresh_closes", lambda now=None: order.append("yahoo") or {"asked": 0, "got": 0, "why": ""})
    monkeypatch.setattr(hn, "regenerate", lambda now=None: order.append("render"))
    hn.tick_and_render(_t(f"{_D}T16:01"))
    assert order == ["krx", "yahoo", "render"]


def test_krx_frame_parser_reads_close_and_change_and_drops_placeholders(monkeypatch):
    """`_krx_closes` 를 실제 pandas 프레임으로 태운다(#155 — 손으로 만든 dict 는 Index 진리값 함정을 못 본다)."""
    pd = pytest.importorskip("pandas")
    import bot.holdings_news as m
    import bot.kr_bulk_rank as kb
    df = pd.DataFrame({"시가": [1, 0], "종가": [51000, 0], "등락률": [3.0, 0.0]}, index=["000001", "000002"])
    monkeypatch.setattr(kb, "_pykrx", lambda: (type("S", (), {"get_market_ohlcv_by_ticker":
                                                         staticmethod(lambda d, market=None: df)})(), ""))
    rows, why = m._krx_closes("20261008")
    assert why == "" and rows == {"000001": [3.0, 51000.0]}
