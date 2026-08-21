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
            return "<P>표가 없는 짧은 보고서</P>"      # 상한에 한참 못 미침
        monkeypatch.setattr(df, "_fetch_doc_text", fake)
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
        assert "_PROBE_VER" in src and "형식 스윕 v" in src


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
        assert "_PROBE_VER = 3" in src
        loop = src[src.index("for cap in (_DOC_TEXT_MAX"):]
        loop = loop[:loop.index("if verdict in _OK:")]
        # 잘렸으면 다음 상한으로 넘어가야 하므로, break 는 '채택' 또는
        # '안 잘림' 조건 아래에만 있어야 한다.
        assert "if v in _OK:" in loop and "if not cut:" in loop
        assert "truncated=cut" in loop, "잘림 여부를 판정에 안 넘긴다"

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
        assert 'rowspan="5"' in t and 'class="num"' in t
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
        assert f"background:{_BG}" in h, "카드 배경이 차트와 다른 색"
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
