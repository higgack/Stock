"""한국 수출 카드뉴스(나쁜양파) — 사용자 2026-11-12 캡처 원문 기준."""

import json
import sqlite3

from trade import badonion_sources as srcs
from trade import kr_cardnews as krc

CAP = ("📊 26.10 반도체 카드뉴스\n\n"
       "1. DRAM  YoY +529% · MoM +7%\n금액 🔺 7% 중량🔽 4% 단가 🔺 11%\n\n"
       "2. DRAM 모듈  YoY +690% · MoM -4%\n금액🔽 4% 중량🔽 8% 단가 🔺 4%\n\n"
       "5. 프로세서·컨트롤러 IC  YoY +40% · MoM +7%\n금액 🔺 7% 중량 ➖  단가 🔺 6%\n\n"
       "* 메모리 단가 상승이 금액을 끌어올림")


def test_parses_header_and_items_from_caption():
    p = krc.parse_kr_cardnews(CAP)
    assert (p["month"], p["topic"]) == ("2026-10", "반도체")
    got = [(i["rank"], i["name"], i["yoy"], i["mom"], i["amt_mom"], i["wt_mom"],
            i["price_mom"]) for i in p["items"]]
    assert got == [(1, "DRAM", 529.0, 7.0, 7.0, -4.0, 11.0),
                   (2, "DRAM 모듈", 690.0, -4.0, -4.0, -8.0, 4.0),
                   # 보합(➖)은 0 — 숫자가 없다고 None 으로 버리지 않는다.
                   (5, "프로세서·컨트롤러 IC", 40.0, 7.0, 7.0, 0.0, 6.0)]


def test_unsigned_value_without_arrow_is_not_guessed():
    p = krc.parse_kr_cardnews("📊 26.10 전자부품 카드뉴스\n\n"
                              "1. 카메라모듈  YoY -7% · MoM -13%\n금액 13% 중량 -8%")
    i = p["items"][0]
    assert i["amt_mom"] is None and i["wt_mom"] == -8.0


def test_rejects_non_cardnews_and_other_grammars():
    for bad in ("", None, "반도체 카드뉴스", "📊 26.13 반도체 카드뉴스",
                "삼성 26.10 카드뉴스", "HPSP (403870)\n한국 수출\n26년 7월 Update"):
        assert krc.parse_kr_cardnews(bad) is None, bad
    # 레지스트리 교차 — 카드뉴스는 이 소스 하나만 받는다(#83 반대 방향).
    assert srcs.matching_keys(CAP) == ("krc",)


def test_ingest_keeps_every_image_and_render_shows_all(tmp_path):
    conn = krc.open_kr_cardnews_db(tmp_path / "c.db")
    media = [f"media/a{i}.jpg" for i in range(10)]
    assert krc.ingest(conn, CAP, source_message_id=7,
                      posted_at="2026-11-12T04:31:00+00:00", media_paths=media)
    # 이미지 없이 다시 받아도 이미지를 지우지 않는다.
    assert krc.ingest(conn, CAP, source_message_id=7, posted_at="", media_paths=[])
    row = conn.execute("select media_json from kr_cardnews").fetchone()
    assert json.loads(row[0]) == media
    html = krc.render_html(conn, media_url_prefix="/m/")
    for m in media:
        assert f'src="/m/{m}"' in html
    assert "그래프 10장" in html
    # 캡션 코멘트가 원문 그대로 실린다.
    assert "메모리 단가 상승이 금액을 끌어올림" in html
    assert "DRAM 모듈" in html and "+690%" in html


def test_registry_places_it_before_stock_board():
    order = srcs.NAV_ORDER
    assert order.index("krc") + 1 == order.index("krs")
    src = srcs.by_key("krc")
    assert src.html_file == "kr_cardnews.html" and src.country == "한국"
