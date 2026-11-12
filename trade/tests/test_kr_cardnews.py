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


def test_each_topic_folds_and_old_months_group_by_month(tmp_path):
    """업종(포스트)마다 접히고, 지난 달은 달별 묶음으로 남는다(사용자 2026-11-12)."""
    import re
    conn = krc.open_kr_cardnews_db(tmp_path / "c.db")
    for mo, topic in (("26.10", "반도체"), ("26.10", "소비재"),
                      ("26.09", "반도체"), ("26.08", "반도체")):
        cap = CAP.replace("26.10 반도체", f"{mo} {topic}")
        assert krc.ingest(conn, cap, posted_at="", media_paths=["m/a.jpg"])
    html = krc.render_html(conn)
    # 포스트 하나 = 접히는 블록 하나(4건), 기본은 접힘.
    posts = re.findall(r'<details class="cn-post"( open)?>', html)
    assert len(posts) == 4 and not any(posts)
    # 접힌 머리에 상위 품목이 보인다.
    assert re.search(r'<summary class="cn-hd">.*?상위: DRAM \+529%', html, re.S)
    # 달 → 업종 2단(사용자 2026-11-12): 달마다 묶음 하나, 최신 달만 펼침.
    months = re.findall(
        r'<details class="cn-month"( open)?><summary>📁 (\S+) · 업종 (\d+)건', html)
    assert months == [(" open", "2026-10", "2"), ("", "2026-09", "1"), ("", "2026-08", "1")]
    # 업종 포스트는 제 달 묶음 **안**에 있다.
    seg = html[html.index("📁 2026-10"):html.index("📁 2026-09")]
    assert "26.10 소비재" in seg and "26.10 반도체" in seg
    # 같은 (달, 업종) 재게시는 덮는다 — 행 수 그대로.
    assert krc.ingest(conn, CAP, posted_at="", media_paths=[])
    assert conn.execute("select count(*) from kr_cardnews").fetchone()[0] == 4


def test_prelim_is_replaced_by_final_of_same_month_and_topic(tmp_path):
    """사용자 2026-11-12: 잠정치는 그달 확정치가 오면 지우고, 그 전엔 둔다."""
    assert krc.topic_stage("잠정치 · 소비재") == ("prelim", "소비재")
    assert krc.topic_stage("확정치 · 소비재") == ("final", "소비재")
    assert krc.topic_stage("반도체") == ("final", "반도체")
    conn = krc.open_kr_cardnews_db(tmp_path / "c.db")
    pre = lambda t, mo="26.10": f"📊 {mo} 잠정치 · {t} 카드뉴스\n\n1. A  YoY +1%"
    fin = lambda t, mo="26.10": f"📊 {mo} {t} 카드뉴스\n\n1. A  YoY +2%"
    for t in ("소비재", "반도체"):
        assert krc.ingest(conn, pre(t), posted_at="", media_paths=["m/a.jpg"])
    assert krc.ingest(conn, pre("소비재", "26.09"), posted_at="", media_paths=[])
    topics = lambda: sorted((p["month"], p["topic"]) for p in krc.list_posts(conn))
    assert len(topics()) == 3                       # 확정치 전: 잠정치 그대로
    assert krc.ingest(conn, fin("소비재"), posted_at="", media_paths=[])
    assert topics() == [("2026-09", "잠정치 · 소비재"), ("2026-10", "소비재"),
                        ("2026-10", "잠정치 · 반도체")]   # 다른 달·다른 업종은 남는다
    n = conn.execute("SELECT COUNT(*) FROM kr_cardnews").fetchone()[0]
    assert n == 3                                   # 삭제는 DB 에서(화면만 아님)
    # 확정치 뒤에 다시 온 잠정치는 싣지 않는다
    assert krc.ingest(conn, pre("소비재"), posted_at="", media_paths=[])
    assert ("2026-10", "잠정치 · 소비재") not in topics()
    # 이미 둘 다 있는 옛 DB 도 화면에선 잠정치를 뺀다
    conn.execute("INSERT INTO kr_cardnews (month, topic) VALUES ('2026-10', '잠정 반도체')")
    conn.execute("INSERT INTO kr_cardnews (month, topic) VALUES ('2026-10', '확정치 · 반도체')")
    assert ("2026-10", "잠정 반도체") not in topics()
    assert "잠정치 · 반도체" not in krc.render_html(conn)
