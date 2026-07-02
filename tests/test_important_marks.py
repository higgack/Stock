"""중요 마크 서버 저장 — toggle/marks/all_marks 순수 로직 회귀 (사용자 2026-06-26)."""
import importlib
import tempfile
from pathlib import Path

import bot.important_marks as im


def _fresh(tmp):
    im._FILE = Path(tmp) / "important_marks.json"
    return im


def test_toggle_on_off_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        m = _fresh(d)
        assert m.toggle("dart", "rcept_001", True) is True
        assert m.marks("dart") == ["rcept_001"]
        assert m.all_marks() == {"dart": ["rcept_001"]}
        # off → 제거, 빈 surface 는 키째 정리
        assert m.toggle("dart", "rcept_001", False) is False
        assert m.marks("dart") == []
        assert m.all_marks() == {}


def test_multi_surface_and_persist():
    with tempfile.TemporaryDirectory() as d:
        m = _fresh(d)
        m.toggle("blog", "2026-06-26|a.json", True)
        m.toggle("daily_byte", "2026-06-25|kr.json", True)
        m.toggle("blog", "2026-06-26|b.json", True)
        # 디스크 재로드(새 인스턴스 시뮬) — 영속 확인
        m._FILE = Path(d) / "important_marks.json"
        am = m.all_marks()
        assert set(am["blog"]) == {"2026-06-26|a.json", "2026-06-26|b.json"}
        assert am["daily_byte"] == ["2026-06-25|kr.json"]


def test_invalid_surface_and_id_ignored():
    with tempfile.TemporaryDirectory() as d:
        m = _fresh(d)
        assert m.toggle("bogus_surface", "x", True) is False   # 화이트리스트 밖
        assert m.toggle("dart", "", True) is False             # 빈 id
        assert m.toggle("dart", "x" * 300, True) is False      # 과대 id
        assert m.all_marks() == {}


def test_toggle_idempotent_on():
    with tempfile.TemporaryDirectory() as d:
        m = _fresh(d)
        m.toggle("reddit", "r1", True)
        m.toggle("reddit", "r1", True)        # 중복 on
        assert m.marks("reddit") == ["r1"]    # 1건만


def test_surfaces_cover_all_dashboards():
    # 8개 대시보드 + 청약 surface 가 화이트리스트에 모두 존재(누락 방지).
    for s in ("analysis", "screener", "screen", "dart", "valuechain",
              "daily_byte", "reddit", "blog", "realestate", "cheongyak"):
        assert s in im.SURFACES


def test_group_pages_inject_important_block():
    # 공용 JS 공유 5개 표면(daily_byte·reddit·blog·realestate·cheongyak)에
    # 중요-마크 블록 + 표면별 IMP_CFG 가 주입되는지(회귀 — 향후 편집 시 silent drop 방지).
    import bot.dashboard as d
    sample = [{"_date": "2026-06-26", "_filename": "x.json", "title": "T",
               "markdown": "## A\nbody", "body": "b", "cost_usd": 1.0,
               "elapsed_s": 5, "ts": "19:00", "_kind": "realestate"}]
    cases = {
        "_render_daily_byte_page": "daily_byte",
        "_render_reddit_insider_page": "reddit",
        "_render_blog_page": "blog",
        "_render_realestate_page": "realestate",
        "_render_cheongyak_page": "cheongyak",
    }
    for fn, surf in cases.items():
        html = getattr(d, fn)(sample)
        assert "__impInit" in html, fn               # 공용 블록
        assert "api/important" in html, fn           # 서버 저장 배선
        assert f'"surface": "{surf}"' in html, fn    # 표면별 cfg
        assert html.count("</body>") == 1 and html.count("</html>") == 1, fn


def test_bespoke_pages_inject_important_block():
    # 개별 JS 표면(종목분석·Bottleneck스크리너·조건부·DART·밸류체인)도 IMP 주입.
    import bot.dashboard as d
    cases = [
        ("analysis", d._render_index([])[0]),
        ("screener", d._render_screener_page([], {}, [])),
        ("screen", d._render_screen_page([])),
        ("dart", d._render_dart_feed_page({})),
        ("valuechain", d._render_valuechain_page([])),
    ]
    for surf, html in cases:
        assert "__impInit" in html, surf
        assert "api/important" in html, surf
        assert f'"surface": "{surf}"' in html, surf
        assert html.count("</body>") == 1 and html.count("</html>") == 1, surf
