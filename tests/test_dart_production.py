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

    def test_capacity_only_table_is_rejected(self):
        """생산능력 단어만 스쳐도 채택하면 이웃 표를 집는다."""
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

    def test_right_align_becomes_num_class(self):
        """숫자 우측정렬만 원본에서 보존 — 기존 .si-table .num 재사용."""
        t = parse_production(REAL)["table_html"]
        assert 'class="num"' in t
        assert 'class="si-table"' in t

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

    def test_falls_back_to_full_when_anchor_is_beyond_small_cap(self, monkeypatch):
        """목차가 긴 대형사 대비 — 3MB 안에 앵커가 없으면 FULL 로 재시도."""
        import bot.dart_feed as df
        caps = []

        def fake(rn, key, max_bytes=0, raw_markup=False):
            caps.append(max_bytes)
            return "<P>앞부분만</P>" if max_bytes == df._DOC_TEXT_MAX else REAL
        monkeypatch.setattr(df, "_fetch_doc_text", fake)
        d = self._Dart({(2026, "11012"): REAL})
        got = production_rolling(d, "098120",
                                 [{"year": 2026, "reprt_code": "11012",
                                   "label": "26.2Q"}])
        assert got is not None, "FULL 폴백이 동작하지 않았다"
        assert caps == [df._DOC_TEXT_MAX, df._DOC_TEXT_MAX_FULL]

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
