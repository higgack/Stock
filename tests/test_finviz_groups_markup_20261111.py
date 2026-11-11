"""Finviz 업종표 링크 모양 변경(2026-11-11 VM: 200·237KB·0행) — 새 모양을 읽는다."""
from bot import finviz_client as f

# VM 이 찍은 원문 표본 그대로(#155 픽스처는 원천이 실제로 보내는 모양대로).
_ROW = ('<tr valign="top" class="styled-row is-bordered is-rounded is-hoverable is-striped '
        'has-color-text cursor-pointer" onclick="window.location=\'/screener/stocks?v=111&f='
        'industry_semiconductors\'">\n<td height="10" align="right" class="groups_index-cell '
        'groups_sticky-cell">3</td><td height="10" align="left" class="groups_sticky-cell">'
        '<a href="/screener/stocks?v=111&f=industry_semiconductors" class="tab-link">'
        'Semiconductors</a></td><td height="10" align="right" class="">67</td><td height="10" '
        'align="right" class="">12770.55B</td><td height="10" align="right" class="">0.72%</td>'
        '<td height="10" align="right" class="">22.18</td><td height="10" align="right" '
        'class=""><span class="color-text is-positive">2.31%</span></td></tr>')
_OLD = ('<tr><td><a href="screener.ashx?v=111&f=ind_gold" class="tab-link">Gold</a></td>'
        '<td>1.5B</td><td>-0.40%</td></tr>')


def test_new_and_old_links_both_parse():
    got = f._GROUP_ROW_RE.findall(_ROW + _OLD)
    assert [(s, n) for s, n, _t in got] == [("industry_semiconductors", "Semiconductors"),
                                             ("ind_gold", "Gold")]


def test_fetch_groups_reads_the_new_markup(monkeypatch, tmp_path):
    monkeypatch.setattr(f, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(f, "_get", lambda url: "<table>" + _ROW + "</table>")
    monkeypatch.setattr(f, "_groups_fallback_computed", lambda: {"groups": [], "source": "x"})
    monkeypatch.setattr(f, "_groups_fallback_etf", lambda: {"groups": [], "source": "y"})
    out = f.fetch_groups()
    assert out["source"] == "Finviz"
    g = out["groups"][0]
    assert g["name"] == "Semiconductors" and g["pct"] == 2.31 and g["mcap_b"] == 12770.55
    assert g["slug"] == "industry_semiconductors"


def test_us_industry_page_links_the_new_filter_to_the_new_path(monkeypatch):
    from bot import us_pages
    monkeypatch.setattr(f, "fetch_groups", lambda: {"groups": [
        {"name": "Semiconductors", "pct": 2.31, "slug": "industry_semiconductors"},
        {"name": "Gold", "pct": -0.4, "slug": "ind_gold"}], "ts": "t", "source": "Finviz"})
    html = us_pages.render_us_industry_page()
    assert "finviz.com/screener/stocks?v=111&amp;f=industry_semiconductors" in html \
        or "finviz.com/screener/stocks?v=111&f=industry_semiconductors" in html
    assert "finviz.com/screener?v=111&f=ind_gold" in html
