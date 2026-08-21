"""생산능력·생산실적·**가동률** 표 파서 회귀 (사용자 2026-08-20).

"가동률이 중요한거야. 키는 그거야." — 분기실적 탭에 DART 정기보고서 본문
표를 원본 그대로 싣는다. 아래 픽스처는 **2026-08-20 VM 실측 원문**
(마이크로컨텍솔 098120 반기보고서 20260814003233)의 구조를 그대로 옮긴 것이다
— 태그 대소문자·속성 이름·`가 동 률` 의 공백까지 실물과 같다(#54: 픽스처는
원천 형식으로).
"""
import re

import pytest

from bot.dart_production import (parse_production, production_rolling,
                                 render_html, sanitize_table)

# 실측 구조: 앵커 → (단위) 미끼 표 → 데이터 표 → 각주 → 설비 소재지 표.
REAL = '''
<P><SPAN>(1) 제품 생산능력 및 생산실적</SPAN></P>
<TABLE WIDTH="682" BORDER="0" ACLASS="NORMAL"><COLGROUP WIDTH="682"><COL WIDTH="682"></COL></COLGROUP>
<TBODY><TR ACOPY="Y" ADELETE="Y" HEIGHT="30"><TD WIDTH="673" VALIGN="MIDDLE" ALIGN="RIGHT" HEIGHT="23">(단위 : 천개)</TD></TR></TBODY></TABLE>
<TABLE WIDTH="682" BORDER="1" ACLASS="NORMAL"><COLGROUP><COL WIDTH="107"></COL></COLGROUP>
<THEAD><TR ACOPY="Y" HEIGHT="30">
<TH WIDTH="98" ACOPYCOL="Y" ADELETECOL="Y" AMOVECOL="N" HEIGHT="23">사업부문</TH>
<TH WIDTH="136" HEIGHT="23">품 목</TH><TH WIDTH="99" HEIGHT="23">구 분</TH>
<TH WIDTH="99" HEIGHT="23">제27기</TH><TH WIDTH="98" HEIGHT="23">제26기</TH>
<TH WIDTH="98" HEIGHT="23">제25기</TH></TR></THEAD>
<TBODY>
<TR ACOPY="Y" HEIGHT="30"><TD VALIGN="MIDDLE" ALIGN="CENTER" ROWSPAN="3" WIDTH="98" HEIGHT="83">세미콘</TD>
<TD VALIGN="MIDDLE" ALIGN="CENTER" ROWSPAN="3" WIDTH="136" HEIGHT="83">Burn-In Socket</TD>
<TD VALIGN="MIDDLE" ALIGN="CENTER" WIDTH="99" HEIGHT="23">생산능력</TD>
<TD VALIGN="MIDDLE" ALIGN="RIGHT" WIDTH="99" HEIGHT="23">5,172</TD>
<TD ALIGN="RIGHT">10,344</TD><TD ALIGN="RIGHT">10,344</TD></TR>
<TR HEIGHT="30"><TD ALIGN="CENTER">생산실적</TD><TD ALIGN="RIGHT">5,021</TD>
<TD ALIGN="RIGHT">7,624</TD><TD ALIGN="RIGHT">4,934</TD></TR>
<TR HEIGHT="30"><TD ALIGN="CENTER">가 동 률</TD><TD ALIGN="RIGHT">97%</TD>
<TD ALIGN="RIGHT">74%</TD><TD ALIGN="RIGHT">48%</TD></TR>
</TBODY></TABLE>
<P>* 주요 품목을 기준으로 작성하였습니다.* 생산능력은 월 평균 25일, 일 8시간 기준입니다.</P>
<TABLE BORDER="1"><TBODY><TR><TD>사업부문</TD><TD>품 목</TD><TD>소재지</TD><TD>소유형태</TD></TR>
<TR><TD>세미콘</TD><TD>아이씨소켓</TD><TD>천안시 서북구 성거읍</TD><TD>자가</TD></TR></TBODY></TABLE>
'''


class TestParse:
    def test_picks_the_data_table_not_the_unit_decoy(self):
        """앵커 직후 첫 표는 `(단위 : 천개)` 한 칸짜리 **미끼**다(실측).
        그걸 집으면 화면에 단위 문구만 나온다."""
        r = parse_production(REAL)
        assert r is not None
        t = r["table_html"]
        assert "5,172" in t and "Burn-In Socket" in t
        assert "(단위 : 천개)" not in t, "미끼 표를 데이터 표로 집었다"

    def test_does_not_grab_the_neighbouring_facility_table(self):
        """앵커 이후 8개 표 중 하나는 설비 소재지 표다(실측). 생산능력·
        생산실적을 함께 가진 표만 채택해야 한다."""
        t = parse_production(REAL)["table_html"]
        assert "소재지" not in t and "자가" not in t

    def test_utilisation_rate_is_present(self):
        """가동률이 이 기능의 핵심 — 빠지면 기능 자체가 무의미하다."""
        r = parse_production(REAL)
        t = r["table_html"]
        assert r["has_rate"] is True
        assert "가 동 률" in t
        for v in ("97%", "74%", "48%"):
            assert v in t, f"가동률 값 {v} 누락"

    def test_rate_regex_needs_the_spaces(self):
        """원문은 `가 동 률` 로 온다 — 공백 없는 패턴이면 못 잡는다.
        (이 테스트는 그 공백을 없애는 회귀를 잡는다.)"""
        from bot.dart_production import _RATE_RE
        assert _RATE_RE.search("가 동 률")
        assert _RATE_RE.search("가동률")

    def test_unit_and_notes(self):
        r = parse_production(REAL)
        assert r["unit"] == "(단위 : 천개)"
        assert r["notes"][0].startswith("주요 품목")
        # ⚠️ 각주가 **다음 표를 삼키면 안 된다** — 태그 제거 평문에서 각주
        # 한 줄이 소재지 표 셀을 통째로 먹던 실측 결함.
        assert not any("소재지" in n or "자가" in n for n in r["notes"])
        assert all(len(n) <= 120 for n in r["notes"])

    def test_absent_section_returns_none(self):
        assert parse_production("<P>다른 내용</P>") is None
        assert parse_production("") is None
        assert parse_production(None) is None

    def test_one_line_footnote_table_is_rejected(self):
        """어구 하나만 스친 **한 줄짜리** 표는 각주다 — 2026-08-21 임계값을
        3 으로 낮추며 모양 게이트(`_looks_like_a_data_table`)가 이 계약을
        이어받았다. 어구가 하나여도 격자면 채택된다(478560 참조)."""
        m = ('<P>생산능력 및 생산실적</P><TABLE><TBODY><TR>'
             '<TD>생산능력 산출근거</TD><TD>월 25일</TD></TR></TBODY></TABLE>')
        assert parse_production(m) is None


class TestSanitize:
    def test_keeps_structure_and_merges(self):
        t = parse_production(REAL)["table_html"]
        assert 'rowspan="3"' in t
        assert set(re.findall(r"<(\w+)", t)) <= {
            "table", "thead", "tbody", "tfoot", "tr", "td", "th"}

    def test_drops_noise_attributes(self):
        t = parse_production(REAL)["table_html"]
        for junk in ("width=", "height=", "acopy", "aclass", "valign",
                     "adeletecol", "amovecol"):
            assert junk not in t.lower(), f"{junk} 가 남았다"

    def test_numeric_cells_and_headers_are_centered(self):
        """원본 `ALIGN` 을 따라가면 **한 열 안에서 정렬이 갈린다** —
        한솔아이원스 실측: 같은 열의 `44,727`(ALIGN=RIGHT)은 우측,
        `55.7%`(속성 없음)는 좌측이라 숫자를 눈으로 따라갈 수 없었다.
        원본이 스스로 일관되지 않으므로 우리가 내용으로 정한다."""
        t = parse_production(REAL)["table_html"]
        assert 'class="si-table"' in t
        assert 'class="ctr"' in t
        assert 'class="num"' not in t, "원본 정렬을 아직 따라간다"

    def test_same_column_mixed_alignment_is_unified(self):
        """숫자와 비율이 같은 열에 있으면 **같은 정렬**이어야 한다."""
        import re
        from bot.dart_production import parse_production as _pp
        mk = ("생산능력 및 생산실적"
              "<TABLE><THEAD><TR><TH>구분</TH><TH>수량</TH></TR></THEAD>"
              "<TBODY><TR><TD>생산능력</TD><TD ALIGN=\"RIGHT\">44,727</TD></TR>"
              "<TR><TD>생산실적</TD><TD ALIGN=\"RIGHT\">24,937</TD></TR>"
              "<TR><TD>가동률</TD><TD>55.7%</TD></TR></TBODY></TABLE>")
        h = _pp(mk)["table_html"]
        got = {x.strip(): ("ctr" in a)
               for _t, a, x in re.findall(r"<(t[dh])([^>]*)>([^<]*)</\1>", h)}
        assert got["44,727"] and got["55.7%"], f"열 안에서 정렬이 갈림: {got}"
        assert got["수량"], "머리행이 아래 숫자와 안 맞음"
        assert not got["생산능력"] and not got["가동률"], "글자 셀까지 가운데"

    def test_center_class_exists_in_the_page_css(self):
        """클래스만 붙이고 CSS 가 없으면 아무 일도 안 일어난다(배선)."""
        css = open("bot/dashboard.py", encoding="utf-8").read()
        assert ".si-table .ctr" in css and "text-align: center" in css

    def test_strips_dangerous_markup(self):
        """원문은 외부 입력 — 스크립트·이벤트 핸들러가 DOM 에 들어가면 안 된다."""
        eviltable = (
            '<P>생산능력 및 생산실적</P><TABLE><TBODY>'
            '<TR><TD>생산능력</TD><TD onclick="alert(1)">1</TD></TR>'
            '<TR><TD>생산실적</TD><TD><SCRIPT>alert(2)</SCRIPT>2</TD></TR>'
            '<TR><TD>가 동 률</TD><TD><IMG SRC=x ONERROR="alert(3)">50%</TD></TR>'
            '</TBODY></TABLE>')
        t = parse_production(eviltable)["table_html"]
        low = t.lower()
        assert "<script" not in low and "onclick" not in low
        assert "onerror" not in low and "<img" not in low
        assert "50%" in t, "살균이 내용까지 지웠다"

    def test_bogus_span_values_dropped(self):
        m = ('<P>생산능력 및 생산실적</P><TABLE><TBODY>'
             '<TR><TD ROWSPAN="99999">생산능력</TD><TD COLSPAN="1">1</TD></TR>'
             '<TR><TD>생산실적</TD><TD>2</TD></TR>'
             '<TR><TD>가 동 률</TD><TD>3%</TD></TR></TBODY></TABLE>')
        t = parse_production(m)["table_html"]
        assert "99999" not in t          # 비정상 병합 거부
        assert 'colspan="1"' not in t    # 1 은 무의미


class TestRolling:
    class _Dart:
        api_key = "k"

        def __init__(self, have):
            self.have = have          # {(year, rc): markup}
            self.asked = []

        def find_periodic_reports(self, tk, year, rc):
            self.asked.append((year, rc))
            return [{"rcept_no": f"{year}{rc}"}] if (year, rc) in self.have else []

        def find_periodic_report(self, tk, year, rc):
            return None

    def _patch_fetch(self, monkeypatch, dart):
        import bot.dart_feed as df

        def fake(rn, key, max_bytes=0, raw_markup=False):
            assert raw_markup is True, "표 파서는 **원시 마크업**이 필요하다"
            for (y, rc), mk in dart.have.items():
                if rn == f"{y}{rc}":
                    return mk
            return None
        monkeypatch.setattr(df, "_fetch_doc_text", fake)

    def test_uses_latest_report_that_has_the_table(self, monkeypatch):
        qs = [{"year": 2025, "reprt_code": "11014", "label": "25.3Q"},
              {"year": 2025, "reprt_code": "11011", "label": "25.4Q"},
              {"year": 2026, "reprt_code": "11013", "label": "26.1Q"},
              {"year": 2026, "reprt_code": "11012", "label": "26.2Q"}]
        d = self._Dart({(2026, "11012"): REAL, (2026, "11013"): REAL})
        self._patch_fetch(monkeypatch, d)
        got = production_rolling(d, "098120", qs)
        assert got and got["basis_label"] == "26.2Q", "최신 보고서를 안 썼다"
        assert d.asked[0] == (2026, "11012"), "최신부터 조회해야 한다"

    def test_falls_back_when_latest_report_lacks_the_table(self, monkeypatch):
        """회사 재량으로 특정 분기에 표가 빠질 수 있다 — 직전 보고서로
        폴백하되 **어느 기준인지** 밝혀야 한다(#43)."""
        qs = [{"year": 2026, "reprt_code": "11013", "label": "26.1Q"},
              {"year": 2026, "reprt_code": "11012", "label": "26.2Q"}]
        d = self._Dart({(2026, "11012"): "<P>표 없음</P>",
                        (2026, "11013"): REAL})
        self._patch_fetch(monkeypatch, d)
        got = production_rolling(d, "098120", qs)
        assert got and got["basis_label"] == "26.1Q"

    def test_tries_small_cap_before_full_download(self, monkeypatch):
        """실측 4건 모두 앵커가 문서 앞부분(7만~9.7만자)이라 3MB 로 충분하다.
        FULL(40MB)부터 받으면 같은 문서를 한 번 더 내려받고 메모리 캐시까지
        부풀린다(raw 는 평문과 캐시 키가 달라 재사용이 안 된다)."""
        import bot.dart_feed as df
        caps = []

        def fake(rn, key, max_bytes=0, raw_markup=False):
            caps.append(max_bytes)
            return REAL
        monkeypatch.setattr(df, "_fetch_doc_text", fake)
        d = self._Dart({(2026, "11012"): REAL})
        got = production_rolling(d, "098120",
                                 [{"year": 2026, "reprt_code": "11012",
                                   "label": "26.2Q"}])
        assert got is not None
        assert caps == [df._DOC_TEXT_MAX], "3MB 로 찾았는데 FULL 까지 받았다"

    def test_falls_back_to_full_when_the_doc_was_truncated(self, monkeypatch):
        """목차가 긴 대형사 대비 — 3MB 에서 **잘렸으면** FULL 로 재시도.

        ⚠️ 옛 픽스처는 작은 상한에서 `<P>앞부분만</P>` 을 돌려줬다 — 실제로
        잘린 문서는 상한 길이만큼 온다. 짧은 응답으로 폴백을 검증하면 '안
        잘렸는데도 올린다'는 낭비를 테스트가 축복한다."""
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_DOC_TEXT_MAX", 200)
        monkeypatch.setattr(df, "_DOC_TEXT_MAX_FULL", 20_000)
        caps = []

        def fake(rn, key, max_bytes=0, raw_markup=False):
            caps.append(max_bytes)
            return "x" * max_bytes if max_bytes == 200 else REAL
        monkeypatch.setattr(df, "_fetch_doc_text", fake)
        # 잘림은 **원천이 알려준다**(2026-08-21) — 길이 추정을 쓰면 한글
        # 문서에서 늘 '안 잘림'이 나온다. 작은 상한에서만 잘렸다고 답한다.
        monkeypatch.setattr(df, "doc_was_truncated",
                            lambda rn, cap, raw=False: cap == 200)
        d = self._Dart({(2026, "11012"): REAL})
        got = production_rolling(d, "098120",
                                 [{"year": 2026, "reprt_code": "11012",
                                   "label": "26.2Q"}])
        assert got is not None, "FULL 폴백이 동작하지 않았다"
        assert caps == [200, 20_000]

    def test_no_escalation_when_the_doc_fits_under_the_cap(self, monkeypatch):
        """`max_bytes` 는 HTTP 다운로드를 안 줄인다 — 상한을 올린 재시도는
        **같은 zip 을 한 번 더 받는 것**이다. 안 잘렸으면 올려도 내용이 같아
        순손실이다(2026-08-21 사용자 '가장 빠르고 비용 적게')."""
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_DOC_TEXT_MAX", 200)
        monkeypatch.setattr(df, "_DOC_TEXT_MAX_FULL", 20_000)
        caps = []

        def fake(rn, key, max_bytes=0, raw_markup=False):
            caps.append(max_bytes)
            return "<P>표가 없는 짧은 보고서</P>"      # 잘리지 않은 전문
        monkeypatch.setattr(df, "_fetch_doc_text", fake)
        monkeypatch.setattr(df, "doc_was_truncated",
                            lambda rn, cap, raw=False: False)
        d = self._Dart({(2026, "11012"): REAL})
        assert production_rolling(d, "098120",
                                  [{"year": 2026, "reprt_code": "11012",
                                    "label": "26.2Q"}]) is None
        assert caps == [200], f"안 잘렸는데 상한을 올렸다: {caps}"

    def test_none_when_no_report_has_it(self, monkeypatch):
        qs = [{"year": 2026, "reprt_code": "11012", "label": "26.2Q"}]
        d = self._Dart({(2026, "11012"): "<P>표 없음</P>"})
        self._patch_fetch(monkeypatch, d)
        assert production_rolling(d, "098120", qs) is None

    def test_no_dart_or_no_quarters(self):
        assert production_rolling(None, "x", [{"year": 1, "reprt_code": "a"}]) is None
        assert production_rolling(object(), "x", []) is None


class TestRender:
    def test_render_includes_basis_and_source(self):
        r = parse_production(REAL)
        r["basis_label"] = "26.2Q"
        h = render_html(r)
        assert "26.2Q 보고서 기준" in h, "기준 보고서 미표기(#43)"
        assert "DART 정기보고서" in h
        assert "(단위 : 천개)" in h
        assert "가 동 률" in h and "97%" in h

    def test_render_empty_when_absent(self):
        assert render_html(None) == ""
        assert render_html({}) == ""

    def test_notes_are_escaped(self):
        h = render_html({"table_html": "<table></table>",
                         "notes": ["<b>주</b> & 각주"], "unit": ""})
        assert "&lt;b&gt;" in h and "&amp;" in h


class TestWiring:
    def test_server_sends_production_html(self):
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"production_html": _production_html(' in src
        assert "def _production_html(" in src
        # KR 전용 게이트 — 원천이 DART 라 비-KR 은 조회 자체가 낭비다.
        assert 'detect_market(ticker) != "KR"' in src

    def test_client_renders_below_inventory_chart(self):
        """위치 계약: 인포그래픽(재고자산 차트 포함) **다음**(사용자
        2026-08-20 '재고자산 차트 밑으로')."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "if(j.production_html) h+=j.production_html;" in src
        i_img = src.index("if(j.image_url){")
        i_prod = src.index("if(j.production_html)")
        i_gr = src.index("var gr=j.growth_risk||{};")
        assert i_img < i_prod < i_gr, "생산능력 표 위치가 계약과 다름"

    def test_raw_fetch_path_exists_and_is_cache_keyed(self):
        """raw 를 캐시 키에 안 넣으면 평문 요청과 마크업 요청이 같은 항목을
        공유해 먼저 받은 쪽이 서빙된다(max_bytes 를 키에 넣게 만든 버그와
        같은 형태)."""
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "raw_markup: bool = False" in src
        assert "'raw' if raw_markup else 'txt'" in src

    def test_default_fetch_still_strips_tags(self, monkeypatch):
        """기존 호출부(성장동력·계약공시 파서)는 평문을 기대한다 —
        기본 동작이 바뀌면 그쪽이 **조용히** 깨진다.

        ⚠️ 이 테스트는 소스 grep 이 아니라 **함수를 실제로 태운다.** 처음엔
        새 파라미터를 `raw` 로 지었는데, 그 함수 안에는 zip 청크를 담는
        지역변수 `raw` 가 이미 있어서 파라미터가 덮였다 — `if raw` 가 항상
        truthy 인 바이트를 보게 돼 **평문 호출부 전부가 마크업을 받는**
        상태였다. grep 테스트는 그걸 통과시켰다(#20: 헬퍼/문자열 테스트는
        배선 결함을 못 잡는다). 실제 zip 을 만들어 왕복시킨다."""
        import io
        import zipfile
        import bot.dart_feed as df

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # 본문 아님 게이트(len<200)를 넘도록 실물 크기로 채운다.
            zf.writestr("doc.xml",
                        ("<TABLE><TR><TD>표안</TD></TR></TABLE>"
                         + "<P>여백</P>" * 60).encode("utf-8"))
        blob = buf.getvalue()
        assert len(blob) >= 200

        class _R:
            status_code = 200
            content = blob
        monkeypatch.setattr(df.requests, "get", lambda *a, **k: _R())
        df._DOC_TEXT_MEM.clear()

        plain = df._fetch_doc_text("R1", "k")
        assert plain is not None
        assert "<TABLE" not in plain and "표안" in plain, \
            "기본 호출이 마크업을 돌려준다 — 평문 호출부가 깨진다"

        markup = df._fetch_doc_text("R1", "k", raw_markup=True)
        assert "<TABLE" in markup, "raw_markup 이 태그를 보존하지 않는다"
        # 캐시 분리 — 같은 rcept_no 라도 두 모드가 서로를 덮으면 안 된다.
        assert df._fetch_doc_text("R1", "k") == plain


if __name__ == "__main__":
    pytest.main([__file__, "-q"])


class TestDiagnose:
    """스윕의 판정 분류 — **개선 여지가 있는 것과 없는 것**을 가른다.
    이 구분이 없으면 스윕 로그가 노이즈가 되어 다음 형식을 못 정한다
    (dart_backlog.diagnose 와 같은 규약)."""

    def test_verdicts(self):
        from bot.dart_production import diagnose
        assert diagnose(None) == "원문미제공"
        assert diagnose("") == "원문미제공"
        # 생산·설비 절 자체가 없다 → 파서를 고쳐도 소용없다
        assert diagnose("<P>재무제표</P>") == "섹션없음"
        # 절은 있는데 산문만 → 확장 여지 있음
        assert diagnose("<P>생산 및 설비에 관한 사항</P><P>산문</P>") == "표없음"
        # 가동률만 없는 표 → 표는 실린다(회사가 가동률 미기재)
        m = ('<P>생산능력 및 생산실적</P><TABLE><TBODY><TR>'
             '<TD>생산능력</TD><TD>생산실적</TD></TR></TBODY></TABLE>')
        assert diagnose(m) == "가동률없음"
        assert diagnose(REAL) == "정상"

    def test_normal_verdict_matches_parser(self):
        """판정과 파서가 갈라지면 스윕 통계가 거짓말이 된다 —
        '정상'인데 파싱 실패, 또는 그 반대가 없어야 한다."""
        from bot.dart_production import diagnose, parse_production
        for mk in (REAL, "<P>재무제표</P>", "", None):
            v = diagnose(mk)
            got = parse_production(mk) if mk else None
            assert (v == "정상") == bool(got and got.get("has_rate")), \
                f"판정({v})과 파서 결과가 어긋남"


class TestProbe:
    def test_probe_uses_real_apis(self):
        """⚠️ 첫 판은 `favorites.load_favorites`·`archive.list_runs` 를
        가정해 썼는데 **둘 다 없는 이름**이었다(#53). 실재하는 API 만."""
        import importlib
        from bot.scripts import production_format_probe as pr
        assert callable(pr._universe) and callable(pr.main)
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        assert "from bot.market_favorites import get_favorites" in src
        assert "load_favorites" not in src.replace("load_favorites`", "")
        # 존재 확인 — import 가 되는 이름인가
        assert hasattr(importlib.import_module("bot.market_favorites"),
                       "get_favorites")

    def test_probe_is_read_only(self):
        """스윕은 진단이다 — 쓰기·전송 경로가 있으면 안 된다."""
        import ast
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        tree = ast.parse(src)
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for banned in ("write_text", "send_message", "_notify", "save"):
            assert banned not in called, f"{banned} — 진단 도구가 쓰기를 한다"

    def test_probe_has_version_banner(self):
        """배포 전 코드로 돈 출력을 새 결과로 착각하지 않게(#21)."""
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        # ⚠️ 고정 문구를 박으면 도구 이름이 바뀔 때마다 깨진다 —
        # 계약은 "시작 줄에 **버전을 찍는다**"이지 특정 제목이 아니다.
        assert "_PROBE_VER" in src
        assert "v{_PROBE_VER}" in src, "배너가 버전을 안 찍는다"


class TestCardSplit20260820:
    """성장동력·리스크 카드를 **별도 PNG** 로 분리(사용자 2026-08-20).

    "이 생산실적 내용을 확인된 성장동력 바로 위로" — 카드가 본 인포그래픽
    안에 있으면 HTML 표는 이미지 전체 뒤, 즉 카드 **아래**로 밀린다.
    분리해서 [본 이미지] → [생산능력 표] → [카드] 순서를 만든다."""

    def test_main_image_no_longer_draws_cards(self):
        src = open("bot/quarterly_infographic.py", encoding="utf-8").read()
        body = src[src.index("def _render_locked("):]
        assert "card_col(2.5" not in body, "본 이미지가 아직 카드를 그린다"
        assert "H_CARDS = 0.0" in body

    def test_card_height_is_single_source(self):
        """높이 식이 복제되면 도화지와 상자 높이가 갈라져 카드가 잘린다(#38)."""
        src = open("bot/quarterly_infographic.py", encoding="utf-8").read()
        assert src.count("3.4 * n + 6.55") == 1, "높이 식 복제"
        body = src[src.index("def _render_cards_locked("):]
        assert "_card_height(drivers, risks)" in body[:body.index("def ", 10)]

    def test_render_version_bumped_for_the_split(self):
        """옛 캐시 PNG 는 카드를 **품고 있다** — 버전을 안 올리면 그 종목은
        카드가 두 번(이미지 안 + 별도 장) 뜬다(실수 #11 '배포 ≠ 화면')."""
        from bot.quarterly_infographic import _RENDER_VER
        assert _RENDER_VER != "v7", "카드 분리인데 렌더 버전이 그대로"

    def test_cached_hit_repairs_a_missing_cards_png(self, tmp_path,
                                                    monkeypatch):
        """본 이미지만 캐시에 남고 짝이 없으면 카드가 영구 결손이 된다 —
        캐시 키가 도는 날까지 화면에서 카드가 통째로 빠진다(실수 #11).
        ⚠️ 표현식을 테스트에 복제하면 뮤테이션이 통과한다(#19) — 진입점을
        실제로 태운다."""
        import bot.quarterly_infographic as qi
        p = tmp_path / "TK_q_v9.png"
        p.write_bytes(b"x")
        pc = p.with_name(p.stem + "_cards" + p.suffix)
        pay = {"period_key": "q", "asof": "2026-08-20",
               "growth_risk": {"ok": True, "cached": True}}
        seen = []
        monkeypatch.setattr(qi, "build_payload",
                            lambda *a, **k: pay)
        monkeypatch.setattr(qi, "cache_path", lambda *a, **k: p)
        monkeypatch.setattr(qi, "render_cards",
                            lambda payload, out: seen.append(out) or out)
        r = qi.get_or_render("TK")
        assert r["cached"] is True and r["image"] == str(p)
        assert r["cards_image"] == str(pc), "짝이 없는데 복구를 안 한다"
        assert seen == [str(pc)]
        # 짝이 이미 있으면 다시 그리지 않는다(캐시 히트가 매번 렌더 금지)
        pc.write_bytes(b"y")
        seen.clear()
        assert qi.get_or_render("TK")["cards_image"] == str(pc)
        assert seen == [], "짝이 있는데도 재렌더"

    def test_render_cards_none_without_cards(self):
        from bot.quarterly_infographic import render_cards
        assert render_cards({}, "/tmp/_x.png") is None
        assert render_cards({"growth_risk": {"ok": False}}, "/tmp/_x.png") is None

    def test_purge_keeps_the_cards_pair(self, tmp_path, monkeypatch):
        """⚠️ 실측한 함정 — _purge_stale 이 방금 만든 짝(_cards.png)까지
        지워 화면에서 카드가 통째로 사라진다."""
        import bot.quarterly_infographic as qi
        monkeypatch.setattr(qi, "_IMG_DIR", tmp_path)
        for n in ("TK_a_1.png", "TK_a_1_cards.png",
                  "TK_old_1.png", "TK_old_1_cards.png"):
            (tmp_path / n).write_bytes(b"x")
        qi._purge_stale("TK", tmp_path / "TK_a_1.png")
        left = sorted(f.name for f in tmp_path.iterdir())
        assert left == ["TK_a_1.png", "TK_a_1_cards.png"], left

    def test_server_exposes_cards_image_url(self):
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"cards_image_url"' in src
        assert 'res.get("cards_image")' in src

    def test_client_order_table_between_image_and_cards(self):
        """위치 계약 — 본 이미지 → 생산능력 표 → 카드 이미지."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i_img = src.index("if(j.image_url){")
        i_prod = src.index("if(j.production_html)")
        i_cards = src.index("if(j.cards_image_url)")
        i_gr = src.index("var gr=j.growth_risk||{};")
        assert i_img < i_prod < i_cards < i_gr, "표가 카드 위가 아니다"


class TestSweep20260821:
    """VM 스윕 실측(40종목, 2026-08-21) 후속 — 프로브가 **제품과 다른 경로**를
    봐서 통계가 거짓이었다(실수 #35). 커버리지 9/40 중 삼성전자·SK하이닉스가
    '섹션없음'으로 찍힌 게 발단."""

    def test_diagnose_never_calls_a_truncated_doc_section_missing(self):
        """판정 불가를 '없음'으로 말하지 않는다(실수 #41). 앵커가 안 보여도
        문서가 잘렸으면 절이 없다고 단정할 수 없다."""
        from bot.dart_production import diagnose
        body = "<TABLE><TR><TD>매출</TD></TR></TABLE>"
        assert diagnose(body) == "섹션없음"
        assert diagnose(body, truncated=True) == "원문잘림"
        # 앵커가 보이면 잘림 여부와 무관하게 내용으로 판정한다
        ok = ("생산능력 및 생산실적 <TABLE><TR><TD>생산능력</TD>"
              "<TD>생산실적</TD><TD>가 동 률</TD></TR></TABLE>")
        assert diagnose(ok, truncated=True) == "정상"

    def test_probe_escalates_the_cap_like_the_product_path(self):
        """v1 은 판정이 원문미제공만 아니면 곧장 break 해서 3MB 안에 앵커가
        없는 대형사를 40MB 로 **한 번도** 다시 받지 않았다."""
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        import bot.scripts.production_format_probe as _pf
        # 버전을 리터럴로 박으면 bump 마다 무관한 테스트가 깨진다.
        assert _pf._PROBE_VER >= 4, "상한 escalation 이 들어간 버전 이후여야"
        loop = src[src.index("for cap in (_DOC_TEXT_MAX"):]
        loop = loop[:loop.index("if verdict in _OK:")]
        # 잘렸으면 다음 상한으로 넘어가야 하므로, break 는 '채택' 또는
        # '안 잘림' 조건 아래에만 있어야 한다.
        assert "if v in _OK:" in loop and "if not cut:" in loop
        assert "truncated=cut" in loop, "잘림 여부를 판정에 안 넘긴다"
        # 길이 추정 금지 — 상한은 바이트, 반환은 문자열이다
        assert "len(mk) >= cap" not in src, "길이 추정이 되살아남"
        assert "dp_trunc(rn, cap, True)" in loop, "원천 플래그를 안 쓴다"

    def test_probe_preview_window_matches_the_parser(self):
        """미리보기 창이 파서 스캔창보다 좁으면, 파서가 실제로 보고 버린 표를
        못 보여준다 — v1 의 미리보기가 전부 `(단위 : …)` 캡션이던 이유."""
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        assert "m.end() + dp._SCAN_WINDOW" in src
        assert "m.end() + 6000" not in src, "좁은 고정창 잔존"

    def test_capacity_only_table_is_accepted(self):
        """사용자 2026-08-20 "최대한 다 가져오게". 478560(`품목|일일 처리량|
        월 생산능력|비고`)이 6점 미달로 통째 버려졌다."""
        from bot.dart_production import diagnose, parse_production
        mk = ("(1) 제품 생산능력 및 생산실적 "
              "<TABLE><TR><TD>(단위 : ton)</TD></TR></TABLE>"
              "<TABLE><TR><TH>품 목</TH><TH>일일 처리량</TH>"
              "<TH>월 생산능력</TH><TH>비 고</TH></TR>"
              "<TR><TD>Halon-1301</TD><TD>7.2 ton/day</TD>"
              "<TD>2,660 BTL</TD><TD>생산용 펌프기준</TD></TR></TABLE>")
        got = parse_production(mk)
        assert got and "Halon-1301" in got["table_html"]
        assert got["has_rate"] is False
        assert got["kinds"] == ["생산능력"]
        assert diagnose(mk) == "능력만"

    def test_neighbour_facility_table_still_rejected(self):
        """임계값을 3 으로 낮춰도 게이트의 목적은 유지돼야 한다 — 설비·소재지
        표는 세 어구가 하나도 없어 0 점이다."""
        from bot.dart_production import diagnose, parse_production
        mk = ("생산 및 설비에 관한 사항 "
              "<TABLE><TR><TH>센터별</TH><TH>보유 형태</TH><TH>소재지</TH>"
              "<TH>전용면적</TH></TR><TR><TD>도곡 1센터</TD><TD>자가 건물</TD>"
              "<TD>서울시 강남구</TD><TD>1,099㎡</TD></TR></TABLE>")
        assert parse_production(mk) is None
        assert diagnose(mk) == "무관표만"

    def test_title_never_promises_a_metric_the_table_lacks(self):
        """제목이 고정 문구면 가동률 없는 표에도 '가동률'이 적혀 사용자가
        없는 걸 찾는다(실수 #55 라벨↔내용 불일치)."""
        from bot.dart_production import render_html
        h = render_html({"table_html": "<table></table>",
                         "kinds": ["생산능력"]})
        assert "생산능력" in h and "가동률" not in h
        h2 = render_html({"table_html": "<table></table>",
                          "kinds": ["가동률", "생산능력", "생산실적"]})
        assert "가동률" in h2 and "생산실적" in h2

    def test_threshold_is_a_single_source(self):
        """파서와 감사가 다른 수를 보면 스윕 통계가 화면과 갈라진다(#54)."""
        src = open("bot/dart_production.py", encoding="utf-8").read()
        assert src.count("_MIN_SCORE = 3") == 1
        assert src.count("_MIN_SCORE_ITEM = 10") == 1
        # 선택도 판정도 상수를 **넘겨받아** 쓴다(리터럴 재등장 금지)
        assert "_score, _MIN_SCORE, 10)" in src
        assert "_score_products, _MIN_SCORE_ITEM, 14)" in src
        assert "best >= _MIN_SCORE" in src
        assert "best >= 3" not in src and "best >= 10 " not in src

    def test_shape_gate_only_applies_to_single_keyword_tables(self):
        """모양 게이트가 6점 이상까지 막으면 진짜 표를 놓친다 — 가동률이
        있는 표는 어구 조합만으로 충분히 특이하다."""
        from bot.dart_production import _score
        one_row_rate = ("<TABLE><TR><TD>가 동 률</TD><TD>생산능력</TD>"
                        "</TR></TABLE>")
        assert _score(one_row_rate) >= 10, "가동률 표를 모양으로 막으면 안 됨"
        grid = ("<TABLE><TR><TH>품 목</TH><TH>일일 처리량</TH>"
                "<TH>월 생산능력</TH></TR>"
                "<TR><TD>a</TD><TD>1</TD><TD>2</TD></TR></TABLE>")
        assert _score(grid) == 3, "격자인 능력표는 살아야 함"
        one_line = "<TABLE><TR><TD>생산능력 산출근거</TD><TD>월 25일</TD></TR></TABLE>"
        assert _score(one_line) == 0, "한 줄 각주 표가 통과"

    def test_title_derives_kinds_when_the_caller_omits_them(self):
        """"없으면 셋 다 적는다" 폴백은 고치려던 거짓말을 폴백에 남긴다."""
        from bot.dart_production import render_html
        h = render_html({"table_html":
                         "<table><tr><td>생산능력</td></tr></table>"})
        assert "생산능력" in h and "가동률" not in h and "생산실적" not in h
        # 아무것도 못 찾으면 중립 제목 — 없는 걸 약속하지 않는다
        h2 = render_html({"table_html": "<table><tr><td>x</td></tr></table>"})
        assert "생산 현황" in h2 and "가동률" not in h2


class TestProducts20260821:
    """「2. 주요 제품 및 서비스」 표 — 사용자 2026-08-21 "가동률 표 위에
    이렇게 표 그대로". 캡처(마이크로컨텍솔 26.2Q) 구조를 그대로 재현한
    픽스처로 고정한다."""

    MK = ("<P>2. 주요 제품 및 서비스</P>"        # ← 목차(표 없음), 첫 출현
          "<P>III. 재무에 관한 사항</P>" + "x" * 2000 +
          "<P>2. 주요 제품 및 서비스</P><P>가. 사업부문별 주요 제품 등의 현황</P>"
          "<TABLE><TR><TD>(단위 : 백만원, %)</TD></TR></TABLE>"
          "<TABLE><THEAD><TR><TH>회사명</TH><TH>사업부문</TH><TH>매출유형</TH>"
          "<TH>품 목</TH><TH>구체적용도</TH><TH>매출액</TH><TH>비율</TH></TR>"
          "</THEAD><TBODY><TR><TD ROWSPAN=\"5\" WIDTH=\"90\">㈜마이크로컨텍솔루션"
          "</TD><TD ROWSPAN=\"3\">세미콘</TD><TD>제품</TD><TD>Burn-in Socket</TD>"
          "<TD>단품 메모리 반도체 B/I Test</TD><TD ALIGN=\"RIGHT\">45,502</TD>"
          "<TD ALIGN=\"RIGHT\">65.5</TD></TR><TR><TD>상품</TD><TD>ULTEM</TD>"
          "<TD>원재료</TD><TD ALIGN=\"RIGHT\">2,551</TD>"
          "<TD ALIGN=\"RIGHT\">3.7</TD></TR></TBODY></TABLE>"
          "<P>나. 주요 제품 등의 가격변동추이</P>"
          "<TABLE><TR><TH>품 목</TH><TH>제27기</TH></TR>"
          "<TR><TD>Burn-in Socket</TD><TD>1,200</TD></TR></TABLE>")

    def test_picks_the_products_table_not_the_price_trend_table(self):
        """같은 절의 `나. 가격변동추이` 는 품목+가격만 있어 매출 열이 없다 —
        그게 판별의 핵심이다. 둘 다 '품 목' 을 갖고 있어 어구 하나로는 못 가른다."""
        from bot.dart_production import parse_products
        got = parse_products(self.MK)
        assert got and "45,502" in got["table_html"]
        assert "1,200" not in got["table_html"], "가격변동추이 표를 집었다"
        assert got["unit"] == "(단위 : 백만원, %)"

    def test_table_of_contents_occurrence_does_not_win(self):
        """정기보고서는 목차에도 같은 제목이 있다. 첫 출현만 보면 표가 없는
        창을 훑고 '없음'을 낸다 — 앵커의 모든 출현을 봐야 한다.

        ⚠️ 패딩이 `_SCAN_WINDOW` 보다 **짧으면 이 테스트는 아무것도 안 한다**
        — 목차 앵커의 창이 본문 표까지 덮어 첫-출현-only 뮤테이션이 그대로
        통과한다(실측). 창보다 확실히 길게 띄운다."""
        import bot.dart_production as dp
        far = ("<P>2. 주요 제품 및 서비스</P>"          # 목차 — 창 안에 표 없음
               + "x" * (dp._SCAN_WINDOW + 5000) + self.MK)
        got = dp.parse_products(far)
        assert got and "45,502" in got["table_html"], "목차 출현에서 멈췄다"
        # ⚠️ 위 단언만으론 부족하다 — 1차 앵커가 목차에서 멈춰도 2차 앵커
        # (`사업부문별 주요 제품`)가 구해줘서 뮤테이션이 통과했다(실측).
        # 1차 앵커 **하나만** 주고 모든 출현을 훑는지 직접 못박는다.
        one = dp._pick(far, (dp._ANCHOR_ITEM,), dp._score_products,
                       dp._MIN_SCORE_ITEM, 14)
        assert one and "45,502" in one[1], "1차 앵커가 첫 출현에서 멈춘다"
        # 목차만 있고 본문이 없으면 정직하게 None
        assert dp.parse_products("<P>2. 주요 제품 및 서비스</P>") is None

    def test_markup_is_sanitized_like_the_production_table(self):
        from bot.dart_production import parse_products
        t = parse_products(self.MK)["table_html"]
        # 정렬은 원본이 아니라 **내용**으로 정한다(2026-08-21) — 매출액 숫자는
        # 가운데, 회사명·용도 같은 글자 셀은 좌측.
        assert 'rowspan="5"' in t and 'class="ctr"' in t
        assert "WIDTH" not in t and "width" not in t, "속성이 그대로 샌다"
        assert "<script" not in t.lower()

    def test_price_trend_table_alone_is_rejected(self):
        """매출 열이 없으면 주요 제품 표가 아니다 — 빈칸이 틀린 표보다 낫다."""
        from bot.dart_production import parse_products
        mk = ("주요 제품 및 서비스 <TABLE><TR><TH>품 목</TH><TH>제27기</TH>"
              "</TR><TR><TD>Socket</TD><TD>1,200</TD></TR></TABLE>")
        assert parse_products(mk) is None

    def test_dark_panel_rethemes_si_table_without_new_css(self):
        """사용자 2026-08-21 "위에 차트와 똑같이 검정색 안에". `.si-table` 이
        읽는 CSS 변수를 감싸는 div 에서 다시 정의하면 표 CSS 를 한 줄도 안
        만들고 톤이 바뀐다 — 색을 여기 또 적으면 팔레트 변경 시 갈라진다(#38)."""
        from bot.dart_production import dark_panel
        from bot.quarterly_infographic import _BG, _LINE, _MUTED, _PANEL, _TEXT
        h = dark_panel("t", ["m"], "<table class='si-table'></table>", ["n"])
        for var, val in (("--bg", _PANEL), ("--fg", _TEXT),
                         ("--fg-soft", _MUTED), ("--border", _LINE)):
            assert f"{var}:{val}" in h, f"{var} 가 인포그래픽 팔레트와 다름"
        # 2026-08-21 배경·테두리는 **컨테이너**로 옮겼다(조각마다 카드를 두면
        # 사이에 흰 배경이 비친다) — 색이 팔레트를 벗어나지 않는지는 거기서 본다.
        from bot.dart_production import qwrap_style
        assert f"background:{_BG}" in qwrap_style(), "카드 배경이 차트와 다른 색"
        src = open("bot/dart_production.py", encoding="utf-8").read()
        assert ".si-table {" not in src, "표 CSS 를 새로 만들었다(재사용 위반)"

    def test_both_tables_use_the_same_dark_panel(self):
        """한쪽만 어두우면 한 화면에서 두 톤이 갈린다."""
        from bot.dart_production import render_html, render_products_html
        a = render_products_html({"table_html": "<table></table>"})
        b = render_html({"table_html": "<table></table>", "kinds": ["가동률"]})
        for h in (a, b):
            assert 'class="si-prod"' in h and "--fg-soft:" in h

    def test_products_renders_above_production(self):
        """사용자 2026-08-21 "가동률 표 위에"."""
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        blk = src[src.index("def _production_html("):]
        blk = blk[:blk.index("except Exception")]
        i_p = blk.index("render_products_html(")
        i_r = blk.index('render_html(got.get("production")')
        assert i_p < i_r, "생산 표가 제품 표보다 먼저 붙는다"
        # 표시 순서는 속도·비용과 무관하다(한 응답에 다 담아 보낸다) —
        # 순서 계약은 순전히 화면 요구(사용자 "가동률 표 위에")다.
        assert "tables_rolling(" in blk, "보고서를 표마다 따로 걷는다"

    def test_products_reuses_the_production_fetch_ladder(self):
        """수집 사다리를 복제하면 두 표의 기준 보고서가 갈라진다(#38).
        같은 접수번호 원문이라 캐시에 걸려 DART 호출도 안 늘어야 한다."""
        src = open("bot/dart_production.py", encoding="utf-8").read()
        body = src[src.index("def products_rolling("):]
        body = body[:body.index("def _palette(")]
        # ⚠️ 독스트링을 지우고 본다 — 이 함수의 설명이 금지 대상 이름을
        # 그대로 담고 있어, 그냥 grep 하면 주석을 재게 된다(실제로 걸렸다).
        code = re.sub(r'(?s)""".*?"""', "", body)
        assert "tables_rolling(" in code, "단일 워크를 안 쓴다"
        assert "_fetch_doc_text" not in code, "수집 경로를 복제했다"

    def test_products_rolling_actually_runs_through_the_ladder(self):
        """⚠️ grep 단언은 이걸 못 잡는다(#20). 리팩터 중 옛 `production_for`
        정의가 파일 뒤에 남아 **새 정의를 가렸고**, `parse=` 를 모르는 옛
        시그니처라 products_rolling 이 TypeError 로 죽었다 — 소스 검사 8개는
        전부 통과했다. 스텁으로 통째 태워야 보인다."""
        import bot.dart_feed as df
        import bot.dart_production as dp

        class _Dart:
            api_key = "K"

            def find_periodic_reports(self, t, y, rc):
                return [{"rcept_no": "R1"}]

        seen = []

        def _fake(rn, key, max_bytes=0, raw_markup=False):
            seen.append((rn, max_bytes))
            return TestProducts20260821.MK

        _orig = df._fetch_doc_text
        df._fetch_doc_text = _fake
        try:
            qs = [{"year": 2026, "reprt_code": "11012", "label": "26.2Q"}]
            got = dp.products_rolling(_Dart(), "098120.KQ", qs)
        finally:
            df._fetch_doc_text = _orig
        assert got and "45,502" in got["table_html"]
        assert got["basis_label"] == "26.2Q", "기준 보고서 라벨이 안 붙었다"
        assert seen, "원문을 한 번도 안 받았다"

    def test_module_has_no_shadowed_top_level_definitions(self):
        """같은 이름을 두 번 정의하면 **뒤엣것이 이긴다** — 리팩터 중 옛
        블록이 남아도 문법·import·헬퍼 테스트가 전부 통과한다. 이름 목록을
        손으로 유지하지 않고 AST 로 전수 확인한다(#24)."""
        import ast
        import collections
        tree = ast.parse(open("bot/dart_production.py", encoding="utf-8").read())
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        dup = [k for k, v in collections.Counter(names).items() if v > 1]
        assert not dup, f"중복 정의(뒤엣것이 앞을 가림): {dup}"


class TestFetchCost20260821:
    """사용자 2026-08-21 "어떤 순서로 배치해야 가장 빠르고 비용이 적게 들까".

    답: **표시 순서는 무관하다**(서버가 한 응답에 다 담아 보낸다). 비용은
    DART 원문 다운로드에 있었고, 실측 결과 표가 없는 종목에서 8건이었다."""

    QS = [{"year": 2026, "reprt_code": f"110{i}", "label": f"26.{i}Q"}
          for i in (1, 2, 3, 4)]

    class _Dart:
        api_key = "K"

        def find_periodic_reports(self, t, y, rc):
            return [{"rcept_no": f"R{rc}"}]

    PROD = ("생산능력 및 생산실적 <TABLE><TR><TD>(단위:천개)</TD></TR></TABLE>"
            "<TABLE><TR><TH>구 분</TH><TH>제27기</TH></TR>"
            "<TR><TD>생산능력</TD><TD>1</TD></TR>"
            "<TR><TD>생산실적</TD><TD>2</TD></TR>"
            "<TR><TD>가 동 률</TD><TD>97</TD></TR></TABLE>")

    def _walk(self, monkeypatch, docs):
        import bot.dart_feed as df
        import bot.dart_production as dp
        net = set()

        def fake(rn, k, max_bytes=0, raw_markup=False):
            net.add((rn, max_bytes))
            return docs.get(rn)
        monkeypatch.setattr(df, "_fetch_doc_text", fake)
        return dp.tables_rolling(self._Dart(), "X", self.QS), net

    def test_one_walk_not_one_per_table(self, monkeypatch):
        """표마다 따로 걸으면 같은 문서를 표 수만큼 다시 받는다 — 표가 없는
        종목(스윕 실측상 40 중 31)에서 분기수×표수로 곱해진다."""
        _got, net = self._walk(monkeypatch, {})
        assert len(net) == 4, f"분기당 1건을 넘었다: {sorted(net)}"

    def test_both_tables_from_one_document(self, monkeypatch):
        from tests.test_dart_production import TestProducts20260821 as T
        got, net = self._walk(monkeypatch, {"R1104": T.MK + self.PROD})
        assert got.get("products") and got.get("production")
        assert len(net) == 1, f"두 표가 문서를 따로 받았다: {sorted(net)}"

    def test_single_walk_matches_independent_walks(self, monkeypatch):
        """표별 채택 결과가 따로 걸을 때와 **같아야** 한다 — 제품은 최신
        분기, 가동률은 그보다 옛 분기에 실린 회사가 실재한다."""
        from tests.test_dart_production import TestProducts20260821 as T
        got, _net = self._walk(monkeypatch,
                               {"R1104": T.MK, "R1102": self.PROD})
        assert got["products"]["basis_label"] == "26.4Q"
        assert got["production"]["basis_label"] == "26.2Q"
        assert got["products"]["rcept_no"] == "R1104"
        assert got["production"]["rcept_no"] == "R1102"

    def test_zip_bytes_are_reused_across_caps_and_modes(self):
        """`max_bytes` 는 HTTP 다운로드를 안 줄이고 압축 해제량만 줄인다 —
        상한을 올린 재시도와 평문↔마크업 전환이 **같은 zip 을 다시 받고**
        있었다(2026-08-21 실측 4건). 바이트를 들고 있으면 재파싱으로 끝난다."""
        import io
        import types
        import zipfile
        import bot.dart_feed as df
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("doc.xml", "<P>본문</P>" * 500)
        blob = buf.getvalue()
        hits = [0]

        class _R:
            status_code = 200
            content = blob

        def _get(url, params=None, timeout=None):
            hits[0] += 1
            return _R()

        _orig_rq, _orig_fail = df.requests, df._doc_fail_recent
        df.requests = types.SimpleNamespace(get=_get)
        df._doc_fail_recent = lambda rn: False
        df._DOC_TEXT_MEM.clear()
        df._DOC_BLOB_MEM.clear()
        try:
            for cap, raw in ((3_000_000, True), (40_000_000, True),
                             (3_000_000, False), (40_000_000, False)):
                assert df._fetch_doc_text("R1", "K", max_bytes=cap,
                                          raw_markup=raw)
        finally:
            df.requests, df._doc_fail_recent = _orig_rq, _orig_fail
            df._DOC_TEXT_MEM.clear()
            df._DOC_BLOB_MEM.clear()
        assert hits[0] == 1, f"같은 zip 을 {hits[0]}번 받았다"

    def test_blob_cache_is_bounded(self):
        """상한이 없으면 정기보고서 전문이 쌓여 봇 메모리를 민다."""
        import bot.dart_feed as df
        df._DOC_BLOB_MEM.clear()
        try:
            for i in range(6):
                df._blob_put(f"R{i}", b"x" * 20_000_000)
            total = sum(len(v) for v in df._DOC_BLOB_MEM.values())
            assert total <= df._DOC_BLOB_MAX, f"{total} > {df._DOC_BLOB_MAX}"
            assert "R5" in df._DOC_BLOB_MEM, "가장 최근 것이 밀려났다"
        finally:
            df._DOC_BLOB_MEM.clear()

    def test_server_walks_the_reports_exactly_once(self, monkeypatch):
        """⚠️ 모듈 단위 테스트는 이걸 못 잡는다 — `tables_rolling` 을 직접
        부르면 서버가 그걸 **두 번** 불러도 통과한다(뮤테이션이 실제로
        통과했다). 진입점을 태워 호출 횟수를 센다(#20)."""
        import bot.dart_production as dp
        import bot.dashboard_server as ds
        calls = []

        def _fake(dart, ticker, qs, max_back=4, want=None):
            calls.append(want)
            return {}
        monkeypatch.setattr(dp, "tables_rolling", _fake)
        monkeypatch.setattr(ds, "_render_note", lambda: "", raising=False)
        ds._production_html("005930.KS", {"quarters": [{"year": 2026}]})
        assert len(calls) == 1, f"보고서를 {len(calls)}번 걷는다"
        assert calls[0] in (None, ()), "한 표만 요청해 나머지가 또 걷게 된다"


class TestCanvasGeometry20260821:
    """조각을 세로로 이어 붙이려면 **폭이 같아야** 한다(사용자 2026-08-21
    순서 재배치 검토 중 실측). `bbox_inches="tight"` 는 그려진 내용에 맞춰
    잘라내므로 조각마다 폭이 달라진다 — 본 1672px / 카드 1661px 이었고
    `width:100%` 로 놓으면 패널 왼쪽 선이 1200px 화면에서 3.3px 어긋났다."""

    @staticmethod
    def _payload():
        import bot.quarterly_infographic as qi
        qs = [{"label": f"26.{i}Q",
               "financials": {"매출": 1e12, "영업이익": 2e11, "당기순이익": 1e11,
                              "수주잔고": 5e11, "재고자산": 1.79e11},
               "ratios": {"영업이익률": 20.0, "순이익률": 10.0}}
              for i in (1, 2, 3, 4, 5)]
        return {"ticker": "T", "company": "T", "market": "KOSDAQ",
                "market_cap": 3.4e11, "quarters": qs, "ttm": qi._ttm(qs),
                "per": 12.0, "per_forward": 10.5, "per_self": True, "psr": 2.7,
                "currency": "KRW", "trade_currency": "KRW",
                "currency_mismatch": False, "fiscal_note": "",
                "anomaly_keys": [], "anomaly_labels": [],
                "component_accounts": {}, "source_label": "DART",
                "asof": "2026-08-21",
                "growth_risk": {"ok": True, "headline": "h",
                                "risk_subline": "r",
                                "growth_drivers": ["a", "b"],
                                "sustain_risks": ["c"]}}

    def test_all_pieces_share_one_pixel_width(self):
        import tempfile
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        from PIL import Image
        import bot.quarterly_infographic as qi
        _orig, qi._font_ok = qi._font_ok, lambda: True
        try:
            p = self._payload()
            with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                a = qi.render_infographic(p, f"{d}/m.png")
                b = qi.render_cards(p, f"{d}/c.png")
                wa = Image.open(a).width
                wb = Image.open(b).width
        finally:
            qi._font_ok = _orig
        assert wa == wb, f"조각 폭이 다르다: 본 {wa}px · 카드 {wb}px"

    def test_tight_bbox_is_not_used(self):
        """tight 를 쓰면 내용에 따라 폭이 흔들려 정렬 보장이 깨진다."""
        src = open("bot/quarterly_infographic.py", encoding="utf-8").read()
        import re
        code = re.sub(r'(?s)""".*?"""', "", src)
        code = "\n".join(ln for ln in code.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert "bbox_inches" not in code, "savefig 가 아직 tight 로 자른다"

    def test_canvas_geometry_has_a_single_source(self):
        """도화지 설정이 조각마다 흩어지면 여백이 갈라진다 — 실제로 본 0.15in
        / 카드 0.12in 로 달랐다(#38)."""
        import ast
        src = open("bot/quarterly_infographic.py", encoding="utf-8").read()
        tree = ast.parse(src)
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        assert "_new_canvas" in names
        # ⚠️ 문자열 세기는 **정의 줄까지 센다**(`def _new_canvas(plt,` 가 걸려
        # 2를 기대한 검사가 3을 봤다). 조각이 늘면 기대값도 바뀌어 매번
        # 손봐야 한다 — "plt.subplots 는 _new_canvas 안에서만"이라는 계약을
        # AST 로 못박는다(조각 수와 무관).
        owners = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call)
                        and getattr(n.func, "attr", "") == "subplots"):
                    owners.append(fn.name)
        assert owners == ["_new_canvas"], \
            f"도화지를 따로 만드는 함수가 있다: {owners}"

    def test_footer_line_is_not_clipped(self):
        """tight 를 버리면 축 밖으로 나간 글자가 잘린다 — 푸터 출처·면책 줄이
        이미지 맨 아래에 있어 제일 위험하다."""
        import tempfile
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        from PIL import Image
        import bot.quarterly_infographic as qi
        _orig, qi._font_ok = qi._font_ok, lambda: True
        try:
            with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                a = qi.render_infographic(self._payload(), f"{d}/m.png")
                im = Image.open(a).convert("L")
                px = im.load()
                rows = [y for y in range(im.height - 130, im.height)
                        if sum(1 for x in range(0, im.width, 3)
                               if px[x, y] > 60) > 3]
        finally:
            qi._font_ok = _orig
        assert rows, "이미지 하단 130px 에 글자가 없다 — 푸터가 잘렸다"


class TestPieceOrder20260821:
    """사용자 2026-08-21 배치: [지표·차트] → 📦 주요 제품 → 🏭 가동률 →
    [수주잔고·재고자산] → [성장동력 카드] → 출처·면책.

    공급사슬 순서(무엇을 파나 → 얼마나 만드나 → 주문 → 재고 → 전망). 표를
    차트 사이에 끼우려면 본 이미지를 두 조각으로 나눠야 한다."""

    @staticmethod
    def _payload():
        import bot.quarterly_infographic as qi
        qs = [{"label": f"26.{i}Q",
               "financials": {"매출": 1e12, "영업이익": 2e11, "당기순이익": 1e11,
                              "수주잔고": 5e11, "재고자산": 1.79e11},
               "ratios": {"영업이익률": 20.0, "순이익률": 10.0}}
              for i in (1, 2, 3, 4, 5)]
        return {"ticker": "T", "company": "T", "market": "KOSDAQ",
                "market_cap": 3.4e11, "quarters": qs, "ttm": qi._ttm(qs),
                "per": 12.0, "per_forward": 10.5, "per_self": True,
                "psr": 2.7, "currency": "KRW", "trade_currency": "KRW",
                "currency_mismatch": False, "fiscal_note": "",
                "anomaly_keys": [], "anomaly_labels": [],
                "component_accounts": {}, "source_label": "DART",
                "asof": "2026-08-21_14", "period_key": "20260630",
                "growth_risk": {"ok": True, "cached": True, "headline": "h",
                                "risk_subline": "r",
                                "growth_drivers": ["a", "b"],
                                "sustain_risks": ["c"]}}

    def test_sections_partition_without_overlap_or_gap(self):
        """조각이 섹션을 **정확히 한 번씩** 나눠 가져야 한다 — 겹치면 같은
        차트가 두 번, 빠지면 통째로 사라진다.

        ⚠️ 순서로 비교하면 안 된다 — `_render_locked` 는 섹션을 **코드
        순서**로 그리므로 조각이 어떤 섹션을 담는지만 의미가 있다.
        2026-08-21 `foot`(TTM)을 상단으로 옮기며 튜플 순서가 갈렸다."""
        import bot.quarterly_infographic as qi
        top, bot_ = list(qi._PART_TOP), list(qi._PART_BOTTOM)
        assert not (set(top) & set(bot_)), "두 조각이 같은 섹션을 그린다"
        assert set(top) | set(bot_) == set(qi._SECTIONS), "빠진 섹션이 있다"
        assert len(top) + len(bot_) == len(qi._SECTIONS), "중복 표기"
        # TTM 은 당기순이익 차트 **바로 아래** — 같은 조각의 charts 뒤여야 한다
        assert "charts" in top and "foot" in top, "TTM 이 차트에서 떨어졌다"

    def test_client_assembles_in_the_requested_order(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        blk = src[src.index("if(j.image_url){"):src.index("var gr=j.growth_risk")]
        order = [blk.index(k) for k in (
            "j.image_url", "j.production_html", "j.image_bottom_url",
            "j.cards_image_url", "j.provenance")]
        assert order == sorted(order), f"조립 순서가 어긋남: {order}"

    def test_provenance_is_html_not_baked_into_the_png(self):
        """면책이 PNG 안에 있으면 카드 조각보다 위로 올라가 마지막이 아니게
        된다 — 그래서 HTML 로 뺐다."""
        import ast
        import bot.quarterly_infographic as qi
        src = open("bot/quarterly_infographic.py", encoding="utf-8").read()
        tree = ast.parse(src)
        drawer = next(n for n in tree.body
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_render_locked")
        drawn = ast.dump(drawer)
        assert "투자 참고용" not in drawn, "면책이 아직 PNG 에 그려진다"
        line, disc = qi.provenance_line({"source_label": "DART",
                                         "asof": "2026-08-21_14"})
        assert "2026-08-21 14시 기준(KST)" in line and "투자 참고용" in disc
        srv = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert "_qi.provenance_line(payload)" in srv, "API 가 안 실어 보낸다"

    def test_three_pieces_are_produced_and_survive_purge(self, tmp_path,
                                                         monkeypatch):
        """⚠️ 조각을 추가하고 `_purge_stale` 을 잊으면 방금 만든 그림이 바로
        지워진다(2026-08-20 카드 분리 때 실측한 함정)."""
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        import bot.quarterly_infographic as qi
        pay = self._payload()
        monkeypatch.setattr(qi, "_IMG_DIR", tmp_path)
        monkeypatch.setattr(qi, "_font_ok", lambda: True)
        monkeypatch.setattr(qi, "build_payload", lambda *a, **k: pay)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = qi.get_or_render("T")
        assert r["image"] and r["image_bottom"] and r["cards_image"]
        assert len(list(tmp_path.glob("*.png"))) == 3, "조각이 지워졌다"
        with warnings.catch_warnings():          # 캐시 히트에서도 살아있어야
            warnings.simplefilter("ignore")
            r2 = qi.get_or_render("T")
        assert r2["cached"] and r2["image_bottom"] and r2["cards_image"]

    def test_bottom_piece_carries_the_extra_charts(self):
        """하단 조각이 비면 수주잔고·재고자산이 화면에서 통째로 사라진다."""
        import tempfile
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        from PIL import Image
        import bot.quarterly_infographic as qi
        _o, qi._font_ok = qi._font_ok, lambda: True
        try:
            p = self._payload()
            with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                top = qi._render_locked(p, f"{d}/a.png", qi._PART_TOP)
                bot_ = qi._render_locked(p, f"{d}/b.png", qi._PART_BOTTOM)
                ht = Image.open(top).height
                hb = Image.open(bot_).height
        finally:
            qi._font_ok = _o
        # 수주잔고+재고자산 2단(각 34 단위) — TTM 은 2026-08-21 상단으로 갔다
        assert hb > 600, f"하단 조각이 비었다({hb}px)"
        assert ht > hb, "상단(지표+차트 2단)이 하단보다 작을 수 없다"

    def test_render_version_bumped_for_the_split(self):
        """옛 캐시 PNG 는 수주잔고·재고자산을 **품고 있다** — 버전을 안 올리면
        그 종목은 같은 차트가 두 번 뜬다(실수 #11)."""
        from bot.quarterly_infographic import _RENDER_VER
        assert _RENDER_VER not in ("v7", "v8"), "조각 분할인데 버전이 그대로"

    def test_bottom_piece_is_skipped_when_empty(self):
        """TTM 이 상단으로 간 뒤 수주잔고·재고자산이 없는 종목은 하단이
        통째로 빈다 — 그대로 그리면 정체불명의 얇은 검은 띠가 남는다."""
        import tempfile
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        import bot.quarterly_infographic as qi
        p = self._payload()
        for q in p["quarters"]:                     # 수주잔고·재고자산 제거
            q["financials"].pop("수주잔고", None)
            q["financials"].pop("재고자산", None)
        _o, qi._font_ok = qi._font_ok, lambda: True
        try:
            with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                assert qi._render_locked(p, f"{d}/b.png",
                                         qi._PART_BOTTOM) is None
                # 상단은 여전히 나와야 한다(내용이 있으므로)
                assert qi._render_locked(p, f"{d}/a.png", qi._PART_TOP)
        finally:
            qi._font_ok = _o


class TestTruncationFlag20260821:
    """VM 스윕 v3 실측: 삼성전자·SK하이닉스가 여전히 '섹션없음'이었다.
    문서 2,826k**자** / 상한 3,000k**바이트** = 0.94배라 "안 잘렸다"로
    판정돼 40MB 재시도가 한 번도 안 돌았다 — 원천엔 있는데 없다고 보고."""

    @staticmethod
    def _serve(body: str):
        import io
        import types
        import zipfile
        import bot.dart_feed as df
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("doc.xml", body)

        class _R:
            status_code = 200
            content = buf.getvalue()

        df.requests = types.SimpleNamespace(get=lambda *a, **k: _R())
        df._doc_fail_recent = lambda rn: False
        for c in (df._DOC_TEXT_MEM, df._DOC_BLOB_MEM, df._DOC_TRUNC):
            c.clear()
        return df

    def test_truncation_is_measured_in_bytes_not_characters(self):
        """한글은 UTF-8 3바이트라 문자 수는 바이트 예산보다 **항상** 훨씬
        작다 — 길이 추정은 잘린 문서를 늘 '안 잘림'이라 부른다."""
        import bot.dart_feed as df
        body = "<P>앞부분</P>" + ("한글본문내용" * 200_000)
        _orig_rq, _orig_fail = df.requests, df._doc_fail_recent
        try:
            self._serve(body)
            out = df._fetch_doc_text("R1", "K", max_bytes=3_000_000,
                                     raw_markup=True)
            assert df.doc_was_truncated("R1", 3_000_000, True) is True
            # 옛 방식이었다면 놓쳤을 상황임을 함께 못박는다(뮤테이션 방지)
            assert len(out) < 3_000_000 * 0.98, "픽스처가 함정을 재현 못 함"
        finally:
            df.requests, df._doc_fail_recent = _orig_rq, _orig_fail
            for c in (df._DOC_TEXT_MEM, df._DOC_BLOB_MEM, df._DOC_TRUNC):
                c.clear()

    def test_short_document_is_not_reported_truncated(self):
        """반대 증거 — 안 잘린 문서를 잘렸다고 하면 매번 재다운로드한다."""
        import bot.dart_feed as df
        _orig_rq, _orig_fail = df.requests, df._doc_fail_recent
        try:
            self._serve("<P>짧은 보고서</P>")
            df._fetch_doc_text("R1", "K", max_bytes=3_000_000,
                               raw_markup=True)
            assert df.doc_was_truncated("R1", 3_000_000, True) is False
        finally:
            df.requests, df._doc_fail_recent = _orig_rq, _orig_fail
            for c in (df._DOC_TEXT_MEM, df._DOC_BLOB_MEM, df._DOC_TRUNC):
                c.clear()

    def test_flag_is_keyed_per_cap_and_mode(self):
        """상한마다 잘림 여부가 다르다 — 키를 뭉개면 40MB 결과가 3MB 판정을
        덮어써 재시도가 죽는다."""
        import bot.dart_feed as df
        body = "<P>x</P>" + ("한글본문내용" * 200_000)
        _orig_rq, _orig_fail = df.requests, df._doc_fail_recent
        try:
            self._serve(body)
            df._fetch_doc_text("R1", "K", max_bytes=3_000_000, raw_markup=True)
            df._fetch_doc_text("R1", "K", max_bytes=40_000_000,
                               raw_markup=True)
            assert df.doc_was_truncated("R1", 3_000_000, True) is True
            assert df.doc_was_truncated("R1", 40_000_000, True) is False
        finally:
            df.requests, df._doc_fail_recent = _orig_rq, _orig_fail
            for c in (df._DOC_TEXT_MEM, df._DOC_BLOB_MEM, df._DOC_TRUNC):
                c.clear()

    def test_rolling_escalates_when_the_source_says_truncated(self):
        """⚠️ 이 배선이 핵심이다 — 플래그가 맞아도 롤링이 안 읽으면 그대로다."""
        import bot.dart_feed as df
        import bot.dart_production as dp
        caps = []

        class _D:
            api_key = "K"

            def find_periodic_reports(self, t, y, rc):
                return [{"rcept_no": "R1"}]

        def _fake(rn, k, max_bytes=0, raw_markup=False):
            caps.append(max_bytes)
            return "<P>앞부분만</P>"          # 짧지만 **잘렸다**고 보고됨
        _o1, _o2 = df._fetch_doc_text, df.doc_was_truncated
        df._fetch_doc_text = _fake
        df.doc_was_truncated = lambda rn, cap, raw=False: cap == df._DOC_TEXT_MAX
        try:
            dp.tables_rolling(_D(), "X", [{"year": 2026, "reprt_code": "11012",
                                           "label": "26.2Q"}])
        finally:
            df._fetch_doc_text, df.doc_was_truncated = _o1, _o2
        assert caps == [df._DOC_TEXT_MAX, df._DOC_TEXT_MAX_FULL], \
            f"잘렸다는데 상한을 안 올렸다: {caps}"


class TestOneDarkContainer20260821:
    """사용자 2026-08-21 "중간에 흰색부분없이 하나의 검정테두리로 전체를
    엮어줘" + "단어나 문맥단위로 잘 만들어줘, 폭은 가능한한 넓게"."""

    def test_sections_have_no_card_of_their_own(self):
        """조각마다 배경·테두리를 두면 사이에 페이지 배경(흰색)이 비친다."""
        import re
        from bot.dart_production import dark_panel
        h = dark_panel("t", ["m"], "<table class='si-table'></table>", [])
        style = re.search(r'style="([^"]*)"', h).group(1)
        # ⚠️ 부분문자열로 재면 안 된다 — `--border:` 라는 **CSS 변수**가
        # "border:" 를 담고 있어 멀쩡한 코드를 틀렸다고 한다(실측).
        # 선언의 **속성명**을 정확히 본다(`--` 로 시작하면 변수).
        props = {d.split(":", 1)[0].strip()
                 for d in style.split(";") if ":" in d}
        for banned in ("background", "border", "border-radius", "margin-top"):
            assert banned not in props, f"섹션이 자체 {banned} 를 갖는다"
        assert "--border" in props, "표 테마 변수까지 사라졌다"

    def test_wrapper_carries_background_and_clips_corners(self):
        """컨테이너가 배경·테두리를 담당하고 overflow:hidden 이 안쪽 이미지
        모서리를 대신 깎는다(이미지에 radius 를 주면 모서리에 배경이 비친다)."""
        from bot.dart_production import _palette, qwrap_style
        st = qwrap_style()
        bg, _p, _f, _s, line = _palette()
        assert f"background:{bg}" in st and f"border:1px solid {line}" in st
        assert "overflow:hidden" in st and "border-radius" in st

    def test_korean_wraps_at_word_boundaries(self):
        """한글 기본 줄바꿈은 **낱글자**로 끊어 `반도체검사용 소/켓 제조 외`,
        `매출유/형` 처럼 단어를 가른다(사용자 캡처). keep-all 이면 어절
        경계에서만 끊긴다 — 다만 한 어절이 칸보다 길 때를 위해 폴백도 둔다."""
        from bot.dart_production import dark_panel
        h = dark_panel("t", ["m"], "<table></table>", [])
        assert "word-break:keep-all" in h, "낱글자 줄바꿈이 그대로다"
        assert "overflow-wrap:break-word" in h, "긴 어절 폴백이 없다"
        assert "word-break:break-all" not in h

    def test_client_opens_and_closes_the_wrapper_exactly_once(self):
        """열고 안 닫으면 뒤 내용이 통째로 컨테이너 안에 빨려 들어간다."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        blk = src[src.index("var h=j.wrap_style?"):src.index("if(box)box.innerHTML=h;")]
        assert blk.count("j.wrap_style?('<div") == 1
        assert blk.count("if(j.wrap_style) h+='</div>';") == 1

    def test_images_are_block_level_without_their_own_radius(self):
        """인라인 이미지는 baseline 여백이 생겨 조각 사이에 틈이 보인다."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        blk = src[src.index("var IMG="):src.index("if(box)box.innerHTML=h;")]
        assert "display:block" in blk and "margin:0" in blk
        assert "border-radius:10px" not in blk, "이미지에 개별 radius 가 남음"

    def test_server_ships_the_wrapper_style(self):
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"wrap_style": _wrap_style()' in src
        from bot.dashboard_server import _wrap_style
        assert "background:" in _wrap_style()

    def test_section_heading_fallback_scans_far_enough(self):
        """절 제목(`생산 및 설비에 관한 사항`)은 하위 항목이 여럿이라 생산능력
        표가 한참 뒤에 온다 — 40k 창으로는 설비 현황 표만 훑고 끝난다.
        (VM 스윕 '무관표만' 7건이 전부 13~21개 표를 보고도 최고점 0 이었다.)

        ⚠️ 상수 비교만 하면 값을 바꾸는 뮤테이션은 잡아도 **정말 멀리 있는
        표를 찾는지**는 모른다 — 실제로 그 거리에 놓고 태운다."""
        import bot.dart_production as dp
        far = ("생산 및 설비에 관한 사항"
               + "<TABLE><TR><TD>설비 소재지</TD><TD>본사</TD></TR></TABLE>"
               + "x" * 120_000                       # 옛 40k 창 밖
               + "<TABLE><TR><TH>구 분</TH><TH>제27기</TH></TR>"
                 "<TR><TD>생산능력</TD><TD>1</TD></TR>"
                 "<TR><TD>생산실적</TD><TD>2</TD></TR>"
                 "<TR><TD>가 동 률</TD><TD>97</TD></TR></TABLE>")
        assert dp._SCAN_WINDOW_ALT > 120_000, "폴백 창이 좁다"
        got = dp.parse_production(far)
        assert got and got["has_rate"], "멀리 있는 가동률 표를 못 찾는다"
        assert dp.diagnose(far) == "정상", "판정이 화면과 갈린다"


class TestBacklogRolling20260821:
    """사용자 2026-08-21 "이것들이 있는데 누락시키고 싶지 않아".

    수주잔고만 **최신 1회**로 판정하고 있었다 — DART 가 그 접수건 문서를 안
    주는 경우가 실재해(한화에어로 `status=014` 실측) 나머지 분기가 다 있어도
    회사 전체가 '미공시'로 처리됐다. 생산능력·제품 표는 롤링인데 여기만
    1회였다."""

    @staticmethod
    def _qs(n=4):
        return [{"year": 2026, "reprt_code": f"110{i}", "label": f"26.{i}Q",
                 "financials": {}} for i in range(1, n + 1)]

    def test_recovers_when_only_the_latest_report_is_missing(self,
                                                             monkeypatch):
        import bot.dart_backlog as bl
        import bot.quarterly_infographic as qi
        qs = self._qs()
        qs[-1]["reprt_code"] = "MISSING"

        monkeypatch.setattr(
            bl, "backlog_for",
            lambda d, t, y, rc: None if rc == "MISSING" else 1.0e11)
        qi._fill_backlog(object(), "T", qs)
        vals = [q["financials"].get("수주잔고") for q in qs]
        assert vals[:-1] == [1.0e11] * 3, f"직전 분기로 회복 못 함: {vals}"

    def test_non_publisher_costs_at_most_the_probe_budget(self, monkeypatch):
        """회복을 넣었다고 미공시 회사가 5회 대용량 다운로드를 하면 안 된다."""
        import bot.dart_backlog as bl
        import bot.quarterly_infographic as qi
        calls = []
        monkeypatch.setattr(bl, "backlog_for",
                            lambda d, t, y, rc: calls.append(rc))
        qi._fill_backlog(object(), "T", self._qs(5))
        # ⚠️ `_BACKLOG_PROBE_N` 으로 상한을 재면 그 상수를 99 로 올리는
        # 뮤테이션이 그대로 통과한다(실측) — 자기 자신으로 자기를 검증하는
        # 꼴이다. 리터럴로 못박는다: 미공시 판정에 3회 이상은 과하다.
        assert len(calls) <= 2, f"미공시인데 {len(calls)}회 받았다"
        assert qi._BACKLOG_PROBE_N <= 2, "탐색 예산이 과하다"

    def test_publisher_fills_every_quarter(self, monkeypatch):
        import bot.dart_backlog as bl
        import bot.quarterly_infographic as qi
        qs = self._qs()
        monkeypatch.setattr(bl, "backlog_for", lambda d, t, y, rc: 5.0e11)
        qi._fill_backlog(object(), "T", qs)
        assert all(q["financials"].get("수주잔고") == 5.0e11 for q in qs)

    def test_probe_reports_every_tab_item(self):
        """감사가 한 항목만 세면 나머지 누락은 영원히 안 보인다 — 분기실적
        탭에 실리는 **전 항목**을 같은 표로 센다."""
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        import bot.scripts.production_format_probe as _pf
        # 리터럴 대신 하한 — bump 마다 무관한 빨간불이 뜬다(#67)
        assert _pf._PROBE_VER >= 5, "전 항목 커버리지가 들어간 버전 이후여야"
        for k in ("재고자산", "수주잔고", "제품표", "생산표"):
            assert f'"{k}"' in src, f"{k} 커버리지를 안 센다"
        # 수주잔고도 화면과 같은 규율(최신부터 거슬러)로 봐야 통계가 맞다
        assert "_BACKLOG_PROBE_N" in src, "1회만 보면 화면과 갈라진다"


class TestDetailTiming20260821:
    """사용자 2026-08-21 "지금은 너무 로딩에 오래걸려서 그래" — 어느 블록을
    클릭 로딩으로 뗄지는 **실측**으로 정한다. 프로브가 수집을 재구현하면
    제품과 다른 걸 재므로(#35) 제품에 계측을 심고 프로브는 읽기만 한다."""

    @staticmethod
    def _stub_yf(monkeypatch, delays):
        import sys
        import time
        import types

        class _T:
            def __init__(self, tk):
                pass

            @property
            def info(self):
                time.sleep(delays.get("info", 0))
                return {"quoteType": "EQUITY", "longName": "X",
                        "currency": "USD"}

            @property
            def earnings_dates(self):
                time.sleep(delays.get("earnings", 0))
                return None

            @property
            def upgrades_downgrades(self):
                time.sleep(delays.get("upgrades", 0))
                return None

            @property
            def institutional_holders(self):
                time.sleep(delays.get("holders", 0))
                return None

            @property
            def news(self):
                time.sleep(delays.get("news", 0))
                return []
        monkeypatch.setitem(sys.modules, "yfinance",
                            types.SimpleNamespace(Ticker=_T))

    def test_every_stage_is_measured(self, monkeypatch):
        """한 단계라도 빠지면 그 블록은 영원히 후보에서 빠진다(#54)."""
        import bot.stock_snapshot as ss
        self._stub_yf(monkeypatch, {"info": 0.02, "news": 0.05})
        ss.collect_stock_snapshot("AAPL", use_cache=False)
        tm = ss.last_timing()
        assert {"yf.info", "total"} <= set(tm), f"기본 계측 누락: {tm}"
        for name in ("실적이력", "투자의견", "기관보유", "뉴스",
                     "재무제표", "동종비교"):
            assert name in tm, f"병렬 수집 '{name}' 계측 누락"
        assert any(k.startswith("enrich:") for k in tm), "시장 enrich 미계측"
        assert tm["total"] >= tm["yf.info"], "총합이 부분보다 작다"

    def test_timing_reflects_real_delay(self, monkeypatch):
        """상수를 찍기만 하면 통과하는 계측은 쓸모없다 — 실제 지연을 잰다."""
        import bot.stock_snapshot as ss
        self._stub_yf(monkeypatch, {"info": 0.12})
        ss.collect_stock_snapshot("AAPL", use_cache=False)
        assert ss.last_timing()["yf.info"] >= 0.10

    def test_instrumentation_does_not_swallow_failures(self, monkeypatch):
        """계측이 예외를 삼키면 수집 실패가 조용히 성공으로 보인다."""
        import inspect
        import bot.stock_snapshot as ss
        src = inspect.getsource(ss._collect_stock_snapshot_uncached)
        blk = src[src.index("def _timed("):]
        blk = blk[:blk.index("try:", blk.index("finally:"))]
        assert "finally:" in blk, "계측이 try/finally 가 아니다"
        assert "except" not in blk, "계측이 예외를 삼킨다"

    def test_last_timing_returns_a_copy(self):
        """호출부가 들고 있는 dict 를 다음 수집이 비우면 값이 사라진다."""
        import bot.stock_snapshot as ss
        ss._TIMING.clear()
        ss._TIMING["x"] = 1.0
        got = ss.last_timing()
        ss._TIMING.clear()
        assert got == {"x": 1.0}, "내부 dict 를 그대로 넘겼다"

    def test_probe_reads_the_product_measurements(self):
        src = open("bot/scripts/detail_timing_probe.py",
                   encoding="utf-8").read()
        assert "ss.last_timing()" in src, "제품 계측을 안 읽는다"
        assert "use_cache=False" in src, "캐시 히트를 재면 0 초가 나온다"
        # 수집을 재구현하면 제품과 다른 걸 잰다(#35)
        assert "yfinance" not in src and "yf.Ticker" not in src
        # 계측이 하나도 없으면 '이상 없음'이 아니라 실패다(#54)
        assert "눈이 멀었다" in src


class TestLegibility20260821:
    """사용자 2026-08-21 "차트의 글씨나 항목들의 글씨가 가독성이 떨어져".

    원인은 색이 아니라 **크기**였다(대비는 전부 6.2:1 이상). PNG 는 1670px 로
    그려져 1200px 화면에서 0.72배로 축소되므로 8pt 가 11.5px 밖에 안 됐다 —
    바로 옆 HTML 표는 13px. 레이아웃은 데이터 좌표라 그대로 두고 **도화지만
    좁혀** 글씨를 한 번에 키웠다(11.6 → 9.8in, ×1.18)."""

    @staticmethod
    def _payload(n=6):
        import bot.quarterly_infographic as qi
        qs = [{"label": f"26.{i}Q",
               "financials": {"매출": 1e12, "영업이익": 2e11, "당기순이익": 1e11,
                              "수주잔고": 5e11, "재고자산": 1.79e11},
               "ratios": {"영업이익률": 20.0, "순이익률": 10.0}}
              for i in (1, 2, 3, 4, 5)]
        return {"ticker": "T", "company": "(주)마이크로컨텍솔루션",
                "market": "KOSDAQ", "market_cap": 3.3e11, "quarters": qs,
                "ttm": qi._ttm(qs), "per": 12.12, "per_forward": None,
                "per_self": True, "psr": 2.71, "currency": "KRW",
                "trade_currency": "KRW", "currency_mismatch": True,
                "fiscal_note": "3월 결산", "anomaly_keys": ["매출"],
                "anomaly_labels": ["매출"], "component_accounts": {},
                "source_label": "DART", "asof": "2026-08-21_15",
                "growth_risk": {"ok": True, "headline": "반도체 부품 제조사",
                                "risk_subline": "동 가격 상승 우려",
                                # **절단폭 최대치**로 태운다 — 짧은 문구로만
                                # 재면 글씨를 키웠을 때 넘치는 걸 못 잡는다.
                                "growth_drivers": ["가" * qi._CARD_CHARS] * n,
                                "sustain_risks": ["나" * qi._CARD_CHARS] * n}}

    @staticmethod
    def _render_and_measure(sections):
        """저장 직전에 살아있는 figure 에서 잰다.

        ⚠️ figure 가 닫힌 뒤에 재려 했더니 extent 계산이 전부 예외였고 그걸
        삼켜 '오버플로 0'으로 보고했다 — 대조 대상 0건은 통과가 아니다(#54)."""
        import tempfile
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        import bot.quarterly_infographic as qi
        boxes, hits = [], []

        def _grab(fig):
            fig.canvas.draw()
            r = fig.canvas.get_renderer()
            for ax in fig.axes:
                # ⚠️ `axis("off")` 인 축도 `get_xticklabels()` 는 **자동 눈금**
                # (`−20`,`0`,`100`…)을 그대로 돌려준다 — 화면엔 안 보이는데
                # 그걸 세면 멀쩡한 렌더가 '패널 밖'으로 오보된다(실측).
                # `axison` 이 눈금이 실제로 그려지는지를 알려준다.
                arts = list(ax.texts)
                if ax.axison:
                    arts += [t for t in ax.get_xticklabels()
                             + ax.get_yticklabels() if t.get_text().strip()]
                arts = [t for t in arts if t.get_text().strip()]
                # 본 축(도화지를 꽉 채우게 set_position 한 그것)만 데이터좌표
                # 비교 대상 — inset 은 자기 좌표계라 100 을 넘는 게 정상이다.
                _p = ax.get_position()
                main = (_p.x0 == 0 and _p.y0 == 0
                        and _p.width == 1 and _p.height == 1)
                bbs = []
                for t in arts:
                    bb = t.get_window_extent(r)
                    bbs.append((bb, t.get_text()))
                    if main:
                        d = bb.transformed(ax.transData.inverted())
                        boxes.append((d.x0, d.x1, t.get_text()))
                for i in range(len(bbs)):
                    for j in range(i + 1, len(bbs)):
                        if bbs[i][0].overlaps(bbs[j][0]):
                            hits.append((bbs[i][1], bbs[j][1]))

        orig = Figure.savefig

        def spy(self, *a, **k):
            _grab(self)
            return orig(self, *a, **k)
        Figure.savefig = spy
        _o, qi._font_ok = qi._font_ok, lambda: True
        try:
            with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p = TestLegibility20260821._payload()
                if sections is None:
                    qi._render_cards_locked(p, f"{d}/x.png")
                else:
                    qi._render_locked(p, f"{d}/x.png", sections)
        finally:
            Figure.savefig, qi._font_ok = orig, _o
        return boxes, hits

    def test_no_text_escapes_its_panel(self):
        """글씨를 키우면 긴 항목이 카드 밖으로 나간다 — 눈으로 못 보니 잰다."""
        import bot.quarterly_infographic as qi
        for name, sec in (("상단", qi._PART_TOP), ("하단", qi._PART_BOTTOM),
                          ("카드", None)):
            boxes, _ = self._render_and_measure(sec)
            assert boxes, f"{name}: 잰 게 0건 — 측정이 눈이 멀었다"
            over = [b for b in boxes if b[1] > 97.6 or b[0] < 2.4]
            assert not over, f"{name} 패널 밖: {over[:3]}"

    def test_no_label_collisions(self):
        """도화지를 좁히면 글씨만 커져 막대 값 라벨·축 눈금이 서로 먹는다."""
        import bot.quarterly_infographic as qi
        for sec in (qi._PART_TOP, None):
            _boxes, hits = self._render_and_measure(sec)
            assert not hits, f"라벨 겹침: {hits[:3]}"

    def test_card_cap_and_prompt_move_together(self):
        """절단폭을 내리면 프롬프트 글자수도 내려야 매 항목이 안 끊긴다."""
        from bot.dart_growth_risk import ITEM_CHARS
        from bot.quarterly_infographic import _CARD_CHARS
        assert ITEM_CHARS < _CARD_CHARS, (ITEM_CHARS, _CARD_CHARS)

    def test_text_is_at_least_as_large_as_the_adjacent_tables(self):
        """바로 옆 HTML 표가 13px 인데 PNG 글씨가 더 작으면 눈에 띈다.
        가장 작은 글씨(8pt)가 1200px 화면에서 13px 이상이어야 한다."""
        import bot.quarterly_infographic as qi
        px = qi._FIG_W * qi._FIG_DPI
        scale = 1200.0 / px
        assert px >= 1200, f"출력이 1200px 미만이면 확대돼 흐려진다({px:.0f})"
        smallest_pt = 8.0
        css = smallest_pt / 72 * qi._FIG_DPI * scale
        assert css >= 13.0, f"최소 글씨가 {css:.1f}px — 표(13px)보다 작다"

    def test_render_version_bumped_for_the_rescale(self):
        """옛 캐시 PNG 는 작은 글씨 그대로다 — 버전을 안 올리면 안 바뀐다."""
        from bot.quarterly_infographic import _RENDER_VER
        assert _RENDER_VER not in ("v7", "v8", "v9"), "글씨를 키웠는데 버전 그대로"


class TestAnchorTagTolerance20260821:
    """VM 스윕 v4 실측: 삼성전자(893만자)·SK하이닉스(925만자)·삼성바이오
    (834만자)가 **잘리지도 않았는데** '섹션없음'이었다. 앵커는 표를 원본
    구조 그대로 뜨려고 **raw markup** 을 훑는데 평문 정규식이라, 제목이
    `생산 및 <SPAN>설비</SPAN>에 관한 사항` 처럼 태그로 쪼개지면 못 잡는다
    — 대형사 보고서일수록 제목에 서식 태그가 많다."""

    def test_anchors_survive_inline_tags(self):
        import bot.dart_production as dp
        for rx, plain in ((dp._ANCHOR_ALT, "생산 및 설비에 관한 사항"),
                          (dp._ANCHOR, "생산능력 및 생산실적"),
                          (dp._ANCHOR_ITEM, "주요 제품 및 서비스"),
                          (dp._ANCHOR_ITEM_ALT, "사업부문별 주요 제품")):
            assert rx.search(plain), f"평문도 못 잡음: {plain}"
            tagged = "".join(f"<B>{c}</B>" for c in plain if not c.isspace())
            assert rx.search(tagged), f"글자마다 태그: {plain}"
            mid = plain.replace(" ", '<SPAN CLASS="a">&nbsp;</SPAN>')
            assert rx.search(mid), f"공백 자리에 태그: {plain}"

    def test_anchor_still_requires_the_right_characters(self):
        """느슨해졌다고 엉뚱한 곳에 걸리면 안 된다 — 글자 순서는 강제된다."""
        import bot.dart_production as dp
        for bad in ("설비 및 생산에 관한 사항", "생산 및 설비", "판매 및 설비에 관한 사항"):
            assert not dp._ANCHOR_ALT.search(bad), bad

    def test_anchor_gap_is_bounded(self):
        """중첩 수량자는 역추적이 폭주한다 — 대형사 원문이 9MB 다.

        ⚠️ 시간 단언으로는 못 잡는다. 상한을 없앤 뮤테이션은 **정규식이
        영영 안 끝나** 테스트가 실패하는 대신 프로세스를 멈춰 세웠다(실측).
        멈추는 검사는 검사가 아니므로 상한을 **소스로** 못박는다."""
        import re
        import bot.dart_production as dp
        assert re.search(r"\{0,\d+\}$", dp._GAP), f"GAP 이 무제한: {dp._GAP}"
        assert ".*" not in dp._GAP and ".*?" not in dp._GAP

    def test_anchor_scan_is_fast_on_a_9mb_miss(self):
        """상한이 있어도 실제로 빠른지는 태워 봐야 안다(9MB = 삼성전자 규모)."""
        import time
        import bot.dart_production as dp
        big = ("<P>" + "가" * 200 + "</P>") * 45000        # ≈9MB, 앵커 없음
        t0 = time.time()
        assert dp._ANCHOR_ALT.search(big) is None
        assert time.time() - t0 < 3.0, "9MB 미스 스캔이 3초를 넘음"

    def test_tagged_document_yields_the_table(self):
        """앵커만 고치고 끝이 아니라 **표까지 나와야** 한다(#20 배선)."""
        import bot.dart_production as dp
        mk = ("<P><B>4.</B> 생산 및 <SPAN>설비</SPAN>에 관한 사항</P>"
              "<TABLE><TR><TD>(단위 : 천개)</TD></TR></TABLE>"
              "<TABLE><TR><TH>구 분</TH><TH>제27기</TH></TR>"
              "<TR><TD>생산능력</TD><TD>1</TD></TR>"
              "<TR><TD>생산실적</TD><TD>2</TD></TR>"
              "<TR><TD>가 동 률</TD><TD>97</TD></TR></TABLE>")
        got = dp.parse_production(mk)
        assert got and got["has_rate"], "태그 낀 제목에서 표를 못 냈다"


class TestFscBreaker20260821:
    """VM 계측: `enrich:KR` 중앙값 44.6초·최대 86.4초로 상세 로딩을 지배했다.
    원인은 금융위 API 의 `SERVICETIMEOUT_ERROR`(504) — `dilution_events` 가
    11영업일 × 2엔드포인트를 각각 20초 상한으로 두드린다. 서비스가 죽어
    있으면 그 전부가 순손실이다."""

    @staticmethod
    def _stub(status, body=None):
        import sys
        import types
        import bot.fsc_client as f
        f._FAIL.clear()
        n = [0]

        class _R:
            status_code = status
            text = "err"

            def json(self):
                return body or {}

        def _get(*a, **k):
            n[0] += 1
            return _R()
        sys.modules["httpx"] = types.SimpleNamespace(get=_get)
        f.fsc_key_ready = lambda: True
        f._env_key = lambda k: "KEY"
        return f, n

    def test_repeated_service_failure_stops_hammering(self):
        f, n = self._stub(504)
        try:
            for i in range(11):
                f._fetch("http://x", "opA", {"basDt": f"2026080{i % 9}"})
        finally:
            f._FAIL.clear()
        assert n[0] <= f._FAIL_MAX, f"죽은 서비스를 {n[0]}회 두드렸다"

    def test_client_errors_are_not_tripped(self):
        """4xx 는 파라미터·키 문제라 서비스 장애가 아니다 — 차단하면
        멀쩡한 다른 종목 조회까지 조용히 빈 결과가 된다."""
        f, n = self._stub(400)
        try:
            for _ in range(5):
                f._fetch("http://x", "opB", {})
        finally:
            f._FAIL.clear()
        assert n[0] == 5, f"4xx 로 차단됨({n[0]}회만 시도)"

    def test_success_clears_the_breaker(self):
        """냉각 뒤 살아나면 즉시 정상 동작해야 한다 — 안 그러면 API 가
        복구돼도 화면이 계속 비어 있다."""
        import time
        f, _n = self._stub(504)
        f._fetch("http://x", "opC", {})
        f._fetch("http://x", "opC", {})
        assert f._breaker_open("opC"), "연속 실패인데 차단이 안 됨"
        f2, _ = self._stub(200, {"response": {"body":
                                              {"items": {"item": [{"a": 1}]}}}})
        f2._FAIL["opC"] = (time.time() - f2._FAIL_COOL - 1, 9)   # 냉각 경과
        try:
            assert f2._fetch("http://x", "opC", {}) == [{"a": 1}]
            # ⚠️ `_breaker_open` 으로만 보면 **냉각이 이미 지나** 어차피
            # False 라 해제 여부와 무관하게 통과한다(뮤테이션이 실제로
            # 통과했다). 카운터가 실제로 지워졌는지를 본다 — 안 지우면
            # 다음 실패 1회에 곧장 차단으로 되돌아간다.
            assert "opC" not in f2._FAIL, "성공했는데 실패 카운터가 남음"
            assert not f2._breaker_open("opC")
        finally:
            f2._FAIL.clear()

    def test_breaker_is_per_operation(self):
        """한 엔드포인트가 죽었다고 다른 엔드포인트까지 막으면 안 된다."""
        f, n = self._stub(504)
        try:
            f._fetch("http://x", "dead", {})
            f._fetch("http://x", "dead", {})
            before = n[0]
            f._fetch("http://x", "alive", {})
            assert n[0] == before + 1, "다른 op 까지 차단됐다"
        finally:
            f._FAIL.clear()


class TestAnchorForms20260821:
    """DART 「II. 사업의 내용」 서식이 **두 벌**이다 — 현행 표준은
    `3. 원재료 및 생산설비`, 옛 보고서는 `생산 및 설비에 관한 사항`.
    구 서식만 알고 있어서 삼성전자·SK하이닉스·삼성바이오가 '섹션없음'이었다.

    ⚠️ 태그 문제가 아니었다는 증거: 같은 문서에서 `주요 제품 및 서비스`
    앵커는 멀쩡히 매칭됐다(스윕 v5 에 `제품` 표시). 제목이 달랐던 것이다."""

    TBL = ("<TABLE><TR><TD>(단위 : 천개)</TD></TR></TABLE>"
           "<TABLE><TR><TH>구 분</TH><TH>제27기</TH></TR>"
           "<TR><TD>생산능력</TD><TD>1</TD></TR>"
           "<TR><TD>생산실적</TD><TD>2</TD></TR>"
           "<TR><TD>가 동 률</TD><TD>97</TD></TR></TABLE>")

    def test_every_known_heading_form_is_found(self):
        import bot.dart_production as dp
        for head, want in (("3. 원재료 및 생산설비", "원재료및생산설비"),
                           ("3. 원재료 및 <B>생산설비</B>", "원재료및생산설비"),
                           ("가. 생산 및 설비에 관한 사항", "생산및설비에관한사항"),
                           ("(1) 제품 생산능력 및 생산실적", "생산능력및생산실적")):
            mk = f"<P>{head}</P>" + self.TBL
            got = dp.parse_production(mk)
            assert got, f"못 찾음: {head}"
            assert got["anchor"] == want, (head, got["anchor"])
            assert dp.diagnose(mk) == "정상", head

    def test_unrelated_heading_still_rejected(self):
        """서식을 늘렸다고 아무 절이나 걸리면 엉뚱한 표를 집는다."""
        import bot.dart_production as dp
        mk = "<P>1. 사업의 개요</P>" + self.TBL
        assert dp.parse_production(mk) is None
        assert dp.diagnose(mk) == "섹션없음"

    def test_anchor_list_is_the_single_source(self):
        """후보를 호출부마다 늘어놓으면 한 곳만 갱신돼 판정이 갈라진다 —
        `parse_production`·`diagnose`·프로브가 같은 목록을 읽어야 한다."""
        import ast
        import bot.dart_production as dp
        assert len(dp._PROD_SPECS) >= 3, "서식 후보가 줄었다"
        src = open("bot/dart_production.py", encoding="utf-8").read()
        tree = ast.parse(src)
        for fn in ("parse_production", "diagnose"):
            node = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == fn)
            body = ast.dump(node)
            assert "_PROD_SPECS" in body, f"{fn} 이 앵커 목록을 안 쓴다"
            assert "_ANCHOR_ALT2" not in body, f"{fn} 이 앵커를 직접 나열한다"

    def test_parse_reports_which_form_matched(self):
        """어느 서식이 남는지 세려면 결과가 그걸 말해야 한다."""
        import bot.dart_production as dp
        got = dp.parse_production("<P>3. 원재료 및 생산설비</P>" + self.TBL)
        assert got["anchor"] == "원재료및생산설비"
        src = open("bot/scripts/production_format_probe.py",
                   encoding="utf-8").read()
        assert 'got["anchor"]' in src, "프로브가 서식을 집계 안 한다"


class TestHeaderPrice20260821:
    """사용자 2026-08-21 "시가총액 앞에 현재가도 추가해주고 … 전 나라 공통".
    상세 페이지 상단(현재가 · 시가총액)과 같은 형태로 인포그래픽 헤더에도."""

    @staticmethod
    def _pay(price, mcap, cur="KRW", name="SK하이닉스"):
        import bot.quarterly_infographic as qi
        qs = [{"label": f"26.{i}Q",
               "financials": {"매출": 1e12, "영업이익": 2e11, "당기순이익": 1e11},
               "ratios": {"영업이익률": 20.0, "순이익률": 10.0}}
              for i in (1, 2, 3, 4, 5)]
        return {"ticker": "000660.KS", "company": name, "market": "KOSPI",
                "market_cap": mcap, "price": price, "quarters": qs,
                "ttm": qi._ttm(qs), "per": 12.0, "per_forward": None,
                "per_self": True, "psr": 2.7, "currency": cur,
                "trade_currency": cur, "currency_mismatch": False,
                "fiscal_note": "", "anomaly_keys": [], "anomaly_labels": [],
                "component_accounts": {}, "source_label": "DART",
                "asof": "2026-08-21_15", "growth_risk": {"ok": False}}

    @staticmethod
    def _header(pay):
        """헤더만 그려 (텍스트, x0, x1) 목록 + 겹침."""
        import tempfile
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        import bot.quarterly_infographic as qi
        boxes, hits = [], []
        orig = Figure.savefig

        def spy(self, *a, **k):
            self.canvas.draw()
            r = self.canvas.get_renderer()
            for ax in self.axes:
                pos = ax.get_position()
                if not (pos.x0 == 0 and pos.width == 1):
                    continue
                bbs = [(t.get_window_extent(r), t.get_text())
                       for t in ax.texts if t.get_text().strip()]
                for i in range(len(bbs)):
                    for j in range(i + 1, len(bbs)):
                        if bbs[i][0].overlaps(bbs[j][0]):
                            hits.append((bbs[i][1], bbs[j][1]))
                for t in ax.texts:
                    if not t.get_text().strip():
                        continue
                    d = t.get_window_extent(r).transformed(
                        ax.transData.inverted())
                    boxes.append((t.get_text(), d.x0, d.x1))
            return orig(self, *a, **k)
        Figure.savefig = spy
        _o, qi._font_ok = qi._font_ok, lambda: True
        try:
            with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                qi._render_locked(pay, f"{d}/x.png", ("head",))
        finally:
            Figure.savefig, qi._font_ok = orig, _o
        return boxes, hits

    def test_price_and_mcap_both_shown_without_overlap(self):
        """처음엔 간격을 글자수로 추정했다가 `₩1,730,000` 과 `₩1,263.75조`
        가 79.5 에서 **딱 붙었다**(실측) — 겹침은 픽셀로 잡는다."""
        boxes, hits = self._header(self._pay(1730000, 1.26375e15))
        txts = [b[0] for b in boxes]
        assert "현재가" in txts and "시가총액" in txts
        assert "₩1,730,000" in txts and "₩1,263.75조" in txts
        assert not hits, f"헤더 글자 겹침: {hits[:3]}"
        px = {b[0]: (b[1], b[2]) for b in boxes}
        gap = px["₩1,263.75조"][0] - px["₩1,730,000"][1]
        assert gap >= 2.0, f"현재가·시총이 너무 붙었다(간격 {gap:.1f})"

    def test_long_company_name_does_not_collide(self):
        """회사명이 길면 오른쪽 숫자와 부딪힌다."""
        _b, hits = self._header(
            self._pay(1730000, 1.26375e15, name="(주)마이크로컨텍솔루션홀딩스"))
        assert not hits, f"긴 회사명과 겹침: {hits[:3]}"

    def test_missing_price_leaves_no_hole(self):
        """현재가가 없는 종목(스냅샷 미수신)도 시총만 정상 표기."""
        boxes, hits = self._header(self._pay(None, 1.26e15))
        txts = [b[0] for b in boxes]
        assert "시가총액" in txts and "현재가" not in txts
        assert not hits

    def test_price_uses_trade_currency_not_financial(self):
        """HK 처럼 거래·재무 통화가 다른 종목에서 기호가 틀리면 안 된다."""
        p = self._pay(45.6, 1.2e11, cur="HKD")
        p["currency"] = "CNY"          # 재무통화만 다르게
        p["currency_mismatch"] = True
        txts = [b[0] for b in self._header(p)[0]]
        # ⚠️ "HK$ 로 시작하는 게 하나라도 있나" 로 재면 **시가총액**이
        # 만족시켜 뮤테이션이 통과한다(실측). 현재가 값 자체를 집는다.
        price = [t for t in txts if t.endswith("45.60")]
        assert price and price[0] == "HK$45.60", txts

    def test_payload_carries_the_price(self):
        """⚠️ 헤더 테스트는 payload 를 손으로 만들어 넘기므로 **배선을 못
        잡는다**(#20) — `build_payload` 가 price 를 안 실어도 통과했다.
        반환 dict 에 그 키가 있는지 AST 로 못박는다."""
        import ast
        src = open("bot/quarterly_infographic.py", encoding="utf-8").read()
        fn = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == "build_payload")
        keys = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                keys |= {k.value for k in node.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        assert "price" in keys, "payload 에 현재가가 안 실린다"
        assert "market_cap" in keys

    def test_price_format_is_universal(self):
        """전 나라 공통 — 통화별 기호·자리수(#UNIVERSAL CHANGES ONLY)."""
        from bot.quarterly_series import fmt_price
        assert fmt_price(1730000, "KRW") == "₩1,730,000"
        assert fmt_price(123.45, "USD") == "$123.45"
        assert fmt_price(8500, "JPY") == "¥8,500"
        assert fmt_price(45.6, "HKD") == "HK$45.60"
        assert fmt_price(None, "KRW") == "—"

    def test_stat_tile_labels_are_legible(self):
        """시장타이밍 타일 라벨이 11px·muted 라 값(18px)에 비해 흐렸다."""
        import re
        css = open("bot/fred_boards.py", encoding="utf-8").read()
        m = re.search(r"\.stat \.k\{([^}]*)\}", css)
        assert m, "타일 라벨 CSS 가 없다"
        size = float(re.search(r"font-size:([\d.]+)px", m.group(1)).group(1))
        assert size >= 12.0, f"라벨이 {size}px — 너무 작다"
        assert "var(--muted)" not in m.group(1), "가장 흐린 색 그대로"
