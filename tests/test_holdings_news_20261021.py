"""🗞️ 보유종목 News — 카드 괄호 등락에 기준을 박는다(사용자 2026-10-21 "이오테크닉스는 오늘 주가가 올랐는데 왜
마이너스지?").

괄호 값은 오늘 하루 등락이 아니라 **자산 기록 이후 장종료가 누적**이다. 10-20 02:46(장 전) 기록이면 기준은 10-19 종가라
10-20(−3.77%)과 10-21(+2.14%)이 함께 곱해져 −1.71% 가 됐는데, 괄호엔 기준이 없어 오늘 등락으로 읽혔다(#34). 재는 것:
누적에 '<기준 세션> 종가 대비' 를 붙인다(거래소 날짜 — 독립 리뷰) · 누적이 두 세션 이상이면 마지막 세션 하루 등락을 날짜와 제 색으로 앞에 둔다 ·
한 세션이면 두 값이 같으므로 하나만 · 화면 JS 가 실시간을 이어 붙여도 같은 규칙 · Yahoo 일봉 경로는 바로 앞 **세션**의 봉이
있을 때만 하루 등락을 낸다. 원천은 가짜(네트워크 0)."""
from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess

import pytest

from tests.test_holdings_live_20261019 import _EXTRA
from tests.test_holdings_news_20261012 import (  # noqa: F401 — 픽스처는 이름으로 잡힌다
    _PNL_HARNESS, _closes, _pm, _rec, _stock, _t, hn, ws_snaps)

_EO = "039030"


def _page(hn, now: str):
    hn.pnl_base(_t("2026-10-20T03:00"), _pm("2026-10-20T02:46", **{_EO: 280_000_000.0}), bak=None)
    rec = _rec({**_stock(f"KR:{_EO}"), "ticker": _EO, "name": "이오테크닉스", "value": 1.0})
    return hn.render_page([rec], {}, _t(now), gen="GEN", portfolio=_pm("2026-10-20T02:46", **{_EO: 280_000_000.0}))


def _card_span(html: str) -> tuple[str, dict]:
    head = html.split("data-hncard='1'")[1].split("class='hn-chg")[1]
    attrs = {k: _html.unescape(v) for k, v in re.findall(r"(data-[a-z0-9]+)='([^']*)'", head.split(">")[0])}
    return head, attrs


def _js(hn, html: str, resp: dict, now: str) -> dict:
    """렌더된 카드 재료·요약 JSON 을 그대로 `_PNL_JS` 에 태운다(#20). 화면의 시계는 렌더 시각에 묶는다 — 실제 시계면
    그 세션의 묻기 기한(`lx`)이 지난 날 이 테스트가 조용히 아무것도 안 잰다(#425)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    head, attrs = _card_span(html)
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    src = (f"var DATA={json.dumps(json.dumps(data))}; var RESP={json.dumps(resp)};"
           f"var BASIS={json.dumps(hn.PNL_BASIS)}; var CELL={json.dumps(attrs)};"
           f"var CLS={json.dumps('hn-chg' + head.split(chr(39))[0])};"
           + f"Date.now=function(){{return {int(_t(now).timestamp() * 1000)};}};"
           + _PNL_HARNESS + "els['hn-pnl-data'].attrs['data-poll']='600';" + _EXTRA + hn._PNL_JS
           + "\nprocess.on('exit',function(){console.log(JSON.stringify({cell:cell.textContent,"
             "kids:cell.kids.map(function(k){return [k.className,k.textContent];})}));});")
    r = subprocess.run([node, "-e", src], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_card_names_the_base_and_puts_the_last_session_first(hn, ws_snaps):
    """사용자 캡처 그대로 — 10-20 02:46 기록 · 10-20 −3.77% · 10-21 +2.14%(종가 수집 뒤 19:00). 괄호가 '10-21 +2.14%'
    (상승 색)와 '10-19 종가 대비 −1.71%' 를 함께 말한다 — 금액은 누적 쪽(−479만원)."""
    ws_snaps("KR", "2026-10-20", [(f"KRX:{_EO}", -3.77, 421000.0)])
    ws_snaps("KR", "2026-10-21", [(f"KRX:{_EO}", 2.14, 430000.0)])
    html = _page(hn, "2026-10-21T19:00")
    head, attrs = _card_span(html)
    k = (0.9623 * 1.0214 - 1) * 100
    assert head.split(">", 1)[1].startswith(
        f"(₩430,000 · <span class='hn-up'>10-21 +2.14%</span> · 10-19 종가 대비 {k:.2f}% · "
        f"{hn._signed_krw(280_000_000 * k / 100)})")
    assert head.startswith(" hn-dn")                                     # 바깥 색 = 누적(금액)의 방향
    assert attrs["data-bse"] == "2026-10-19" and attrs["data-ns"] == "2" and attrs["data-d1d"] == "2026-10-21"


def test_live_session_becomes_the_dated_day_and_cumulative_keeps_its_base(hn, ws_snaps):
    """장중(10-21 10:00) — 종가는 10-20 까지(1세션)라 서버 괄호는 누적 하나뿐이다. 화면이 10-21 실시간(+2.14%)을 이어
    붙이면 세션이 둘이 되어 '10-21 +2.14%' 가 앞에 서고 누적은 그 둘을 곱한다(#38 서버와 같은 규칙)."""
    ws_snaps("KR", "2026-10-20", [(f"KRX:{_EO}", -3.77, 421000.0)])
    html = _page(hn, "2026-10-21T10:00")
    head, _ = _card_span(html)
    assert head.split(">", 1)[1].startswith("(₩421,000 · 10-19 종가 대비 -3.77% · ")   # 한 세션 — 하루 등락을 따로 안 쓴다
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    t = data[0]["t"]
    o = _js(hn, html, {"q": {t: {"pct": 2.14, "price": 430000.0, "d": "2026-10-21", "age": 1, "src": "네이버"}},
                       "pending": 0}, "2026-10-21T10:00")
    k = (0.9623 * 1.0214 - 1) * 100
    assert o["cell"] == f"(₩430,000 · 10-21 +2.14% · 10-19 종가 대비 {k:.2f}% · {hn._signed_krw(280_000_000 * k / 100)})"
    assert o["kids"][0] == ["hn-up", "10-21 +2.14%"]


def test_live_on_a_fresh_base_shows_one_number(hn, ws_snaps):
    """기록이 10-20 장 마감 뒤(20:00)면 10-21 실시간이 유일한 세션 — 하루 등락과 누적이 같은 값이라 하나만 쓴다."""
    hn.pnl_base(_t("2026-10-20T21:00"), _pm("2026-10-20T20:00", **{_EO: 280_000_000.0}), bak=None)
    rec = _rec({**_stock(f"KR:{_EO}"), "ticker": _EO, "name": "이오테크닉스", "value": 1.0})
    html = hn.render_page([rec], {}, _t("2026-10-21T10:00"), gen="GEN",
                          portfolio=_pm("2026-10-20T20:00", **{_EO: 280_000_000.0}))
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    o = _js(hn, html, {"q": {data[0]["t"]: {"pct": 2.14, "price": 430000.0, "d": "2026-10-21", "age": 1,
                                            "src": "네이버"}}, "pending": 0}, "2026-10-21T10:00")
    assert o["cell"].startswith("(₩430,000 · 10-20 종가 대비 +2.14% · ")
    assert [c for c, _ in o["kids"] if c is not None] == []                     # 날짜 단 하루 등락 span 없음


def test_yahoo_path_day_change_needs_the_previous_session_bar(hn, ws_snaps):
    """하루치에 없는 종목(ETF)은 Yahoo 일봉 — 마지막 세션 하루 등락은 바로 앞 **세션**의 봉으로만. 그 봉이 빠졌으면
    (원천이 하루를 빠뜨림) 두 세션 등락을 하루로 적지 않고 비운다(#29)."""
    for d in ("2026-10-06", "2026-10-07", "2026-10-08"):            # 하루치는 있는데 이 ETF 만 없다
        ws_snaps("KR", d, [("KRX:005930", 1.0)])
    st = {"key": "KR:069500", "market": "KR", "ticker": "069500", "sym": "069500.KS"}
    base = _t("2026-10-05T18:00").timestamp()                       # 기준 세션 10-02 · 그 뒤 10-06·10-07·10-08
    _closes(hn, {"KR:069500": ({"2026-10-02": 100.0, "2026-10-06": 101.0, "2026-10-07": 104.0, "2026-10-08": 102.96},
                               "2026-10-12T08:00")})
    c = hn.cum_lookup(base, _t("2026-10-12T08:00"))(st)
    assert c["src"] == "yahoo" and c["ka"] == pytest.approx(1.0296)
    assert c["d1_d"] == "2026-10-08" and c["d1"] == pytest.approx(-1.0) and c["bse"] == "2026-10-02"
    _closes(hn, {"KR:069500": ({"2026-10-02": 100.0, "2026-10-06": 101.0, "2026-10-08": 102.96},   # 10-07 봉 없음
                               "2026-10-12T08:00")})
    c = hn.cum_lookup(base, _t("2026-10-12T08:00"))(st)
    assert c["ka"] == pytest.approx(1.0296) and "d1" not in c and "d1_d" not in c


def test_yahoo_day_change_uses_a_bar_on_a_session_guessed_as_holiday(hn, ws_snaps, monkeypatch):
    """휴장 추정(원천 값이 직전과 같았다)은 추정일 뿐 — 그날 Yahoo 봉이 있으면 그 봉이 직전 종가다. 건너뛰면 두 세션
    등락(+10%)을 하루(10-08)로 적는다(독립 리뷰 2026-10-21 재현 그대로)."""
    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-06", [("KRX:005930", 1.0)])
    ws_snaps("KR", "2026-10-08", [("KRX:005930", 1.0)])
    real = ws.market_day_status
    monkeypatch.setattr(ws, "market_day_status",
                        lambda mk, d, now, state=None: "holiday" if d == "2026-10-07" else real(mk, d, now, state))
    _closes(hn, {"KR:069500": ({"2026-10-02": 100.0, "2026-10-06": 101.0, "2026-10-07": 110.0, "2026-10-08": 111.1},
                               "2026-10-12T08:00")})
    st = {"key": "KR:069500", "market": "KR", "ticker": "069500", "sym": "069500.KS"}
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))(st)
    assert c["d1_d"] == "2026-10-08" and c["d1"] == pytest.approx(1.0)


def test_overseas_label_is_the_exchange_close_not_the_kst_record_date(hn, ws_snaps):
    """10-20 02:46 KST 기록은 미국 10-19 세션 **장중**이다 — 그 세션이 통째로 들어가므로 'KST 기록 날짜 이후' 로 적으면
    10-19 등락이 숨는다(독립 리뷰 M2). 라벨은 기준 무렵 마지막 종가의 거래소 날짜(10-16 금요일)."""
    ws_snaps("US", "2026-10-19", [("NASDAQ:AAPL", -1.0)])
    ws_snaps("US", "2026-10-20", [("NASDAQ:AAPL", 2.0)])
    c = hn.cum_lookup(_t("2026-10-20T02:46").timestamp(), _t("2026-10-21T09:00"))(
        {"key": "US:AAPL", "market": "US", "ticker": "AAPL"})
    assert c["bse"] == "2026-10-16" and c["sess"] == 2 and c["d1_d"] == "2026-10-20"
    assert hn._chg_html((c["ka"] - 1) * 100, c) == (
        "<span class='hn-up'>10-20 +2.00%</span> · 10-16 종가 대비 +0.98%")
