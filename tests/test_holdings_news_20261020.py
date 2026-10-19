"""🗞️ 보유종목 News — '새 뉴스 없음' 종목이 왜 없는지(사용자 2026-10-20 "두번째로 많은게 한솔케미칼인데 왜 News
대시보드에는 안나오지?").

카드는 그 창에 새 뉴스가 붙은 종목만 그린다. 뉴스 없는 종목은 이름만 '새 뉴스 없음' 줄에 실리는데, 그 줄이 카드 수백
장 **뒤**에 있었고 왜 없는지를 말하지 않아 큰 보유 종목이 사라진 것처럼 보였다. 재는 것: 수집이 갈래(창 밖 · 앞서
실림 · 이름 불일치)를 세어 둔다 · 화면이 그 줄을 카드 **위**에 보유 큰 순으로 두고 앞쪽 종목에 사유를 단다 · 갈래를
안 적은 옛 기록은 받은 건수만 말한다(지어내지 않는다). 원천은 가짜(네트워크 0).
"""
from __future__ import annotations

from tests.test_holdings_news_20261012 import (  # noqa: F401 — 픽스처는 이름으로 잡힌다
    _fetcher, _h, _it, _model, _t, hn)


def _rec(hn):
    model = _model(_h("이오테크닉스", "039030", "KR", 2.8e8), _h("한솔케미칼", "014680", "KR", 5e7),
                   _h("솔브레인", "357780", "KR", 2.6e7), _h("파크시스템스", "140860", "KR", 2e7),
                   _h("두산우", "000155", "KR", 1e7))
    prev = {"date": "2026-10-18", "window": {"until": "2026-10-18T07:02:00+09:00"},
            "stocks": [{"key": "KR:357780", "items": [{"k": "https://p/sb-old"}]}]}
    table = {
        "KR:039030": ([_it("이오테크닉스 상승", "2026-10-19T05:00:00+09:00", link="https://p/eo")], "naver", ""),
        # 한솔케미칼 — 받은 기사가 전부 창 밖(금요일)이거나 다른 회사 이야기
        "KR:014680": ([_it("한솔케미칼 금요일 기사", "2026-10-16T10:00:00+09:00", link="https://p/hs1"),
                       _it("한솔케미칼 금요일 기사2", "2026-10-16T11:00:00+09:00", link="https://p/hs2"),
                       _it("솔브레인 다른 회사", "2026-10-19T05:00:00+09:00", link="https://p/hs3")], "naver", ""),
        # 솔브레인 — 창 안 기사지만 앞선 기록에 이미 실림
        "KR:357780": ([_it("솔브레인 소식", "2026-10-19T04:00:00+09:00", link="https://p/sb-old")], "naver", ""),
        "KR:140860": ([], "naver", ""),                                        # 검색 0건
        "KR:000155": ([], "", "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음 · 구글 뉴스 실패(로그 참조)"),
    }
    return hn.collect(_t("2026-10-19T07:02"), model=model, records=[prev], fetch=_fetcher(table), pace=0)


def test_collect_counts_why_a_stock_got_no_new_news(hn):
    st = {s["key"]: s for s in _rec(hn)["stocks"]}
    assert st["KR:039030"]["items"] and "skip" not in st["KR:039030"]
    assert st["KR:014680"]["items"] == [] and st["KR:014680"]["skip"] == {"win": 2, "name": 1}
    assert st["KR:357780"]["skip"] == {"seen": 1}
    assert "skip" not in st["KR:140860"] and st["KR:140860"]["raw_n"] == 0


def test_quiet_line_sits_above_the_cards_in_holding_order_with_reasons(hn):
    rec = _rec(hn)
    html = hn.render_page([rec], {}, _t("2026-10-19T09:00"), gen="GEN")
    line = html.split("새 뉴스 없음 4종목: ")[1].split("</div>")[0]
    assert line == ("한솔케미칼(검색 3건: 창 밖 2 · 이름 불일치 1), 솔브레인(검색 1건: 앞서 실림 1), "
                    "파크시스템스(검색 결과 0건), 두산우(원천 실패)")
    day = html.split("새 뉴스 없음 4종목")[0]
    assert "data-q='이오테크닉스" not in day.rsplit("<details", 1)[-1]              # 카드보다 위
    assert html.index("새 뉴스 없음 4종목") < html.index("data-q='이오테크닉스")
    assert f"앞 {hn.QUIET_WHY_N}종목은 왜 없는지" in html                               # ℹ️ 가이드도 같은 commit


def test_quiet_why_old_record_and_cap(hn):
    assert hn.quiet_why({"via": "naver", "raw_n": 5}) == "검색 5건 — 이 창의 새 기사 없음"   # 갈래를 안 적은 옛 기록
    assert hn.quiet_why({"via": "naver"}) == ""                                          # 받은 건수도 없으면 침묵
    quiet = [{"key": f"KR:{i:06d}", "market": "KR", "name": f"종목{i}", "items": [], "via": "naver", "raw_n": 0,
              "status": "empty"} for i in range(hn.QUIET_WHY_N + 2)]
    html = hn._day_body({"date": "2026-10-19", "stocks": quiet})
    assert html.count("(검색 결과 0건)") == hn.QUIET_WHY_N                               # 앞쪽만 사유를 단다
