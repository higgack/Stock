"""Parser tests built from the 10 random samples the operator pulled
from inbox.jsonl after the first backfill, plus a handful of variants
the earlier sample set surfaced (multi-country, composite without
spaces, em-dash, import direction, synthesis post fallback).

Run from the repo root:
    python -m unittest discover trade/tests -v
"""

import unittest
from datetime import date
from unittest import mock

from trade import price_provider as pp
from trade.parser import parse_caption


class TestParserSamples(unittest.TestCase):
    # --- 10 random samples from the user's backfilled inbox ---

    def test_sample_1_item_first_with_placeholder_stocks(self):
        caption = (
            "2차전지 원형·각형 등 Cap Assembly, 모듈 (전국)\n"
            "관련종목: 월별 수출 데이터\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertIsNotNone(r)
        self.assertEqual(r.direction, "export")
        self.assertEqual(r.status, "preliminary")
        self.assertEqual(r.period_start, date(2026, 4, 1))
        self.assertEqual(r.period_end, date(2026, 4, 30))
        self.assertEqual(r.period_kind, "monthly")  # full-month span
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.item, "2차전지 원형·각형 등 Cap Assembly, 모듈")
        self.assertEqual(r.region, "전국")
        self.assertIsNone(r.country)
        self.assertEqual(r.stocks, [])  # placeholder → no companies
        self.assertEqual(r.parse_warnings, [])  # placeholder is legitimate
        self.assertFalse(r.is_composite)

    def test_sample_2_company_first_multi_region(self):
        caption = (
            "SK하이닉스 : 플래시 메모리 (경기 이천시 + 충북 청주시)\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "플래시 메모리")
        self.assertEqual(r.region, "경기 이천시")
        self.assertEqual(r.regions, ["경기 이천시", "충북 청주시"])
        self.assertIsNone(r.country)
        self.assertEqual(r.stocks, ["SK하이닉스"])
        self.assertEqual(r.status, "final")
        self.assertEqual(r.period_kind, "monthly")
        self.assertEqual(r.period_start, date(2026, 4, 1))
        self.assertEqual(r.period_end, date(2026, 4, 30))

    def test_sample_3_company_first_single_region(self):
        caption = (
            "SK하이닉스 : 플래시 메모리 (충북 청주시)\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "플래시 메모리")
        self.assertEqual(r.region, "충북 청주시")
        self.assertIsNone(r.country)
        self.assertEqual(r.stocks, ["SK하이닉스"])

    def test_sample_4_company_first_with_item_internal_paren(self):
        caption = (
            "포스코퓨처엠 : 음극재 (천연흑연) (세종)\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "음극재 (천연흑연)")  # inner paren preserved
        self.assertEqual(r.region, "세종")
        self.assertEqual(r.stocks, ["포스코퓨처엠"])

    def test_sample_5_company_first_long_item_paren(self):
        caption = (
            "엠아이텍 : 스텐트 (인체에 삽입되는 것) (경기 평택시)\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "스텐트 (인체에 삽입되는 것)")
        self.assertEqual(r.region, "경기 평택시")
        self.assertEqual(r.stocks, ["엠아이텍"])

    def test_sample_6_item_first_simple(self):
        caption = (
            "니켈도금강판 (전국)\n"
            "관련종목 : TCC스틸\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.item, "니켈도금강판")
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.stocks, ["TCC스틸"])
        self.assertFalse(r.has_etc)

    def test_sample_7_country_with_unlisted_markers(self):
        caption = (
            "보툴리눔 톡신 (전국_브라질)\n"
            "관련종목 : 휴젤 / 대웅제약 / 메디톡스 / 파마리서치바이오 (비상장) / 제네톡스 (비상장)\n"
            "\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.item, "보툴리눔 톡신")
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.country, "브라질")
        self.assertEqual(
            r.stocks,
            ["휴젤", "대웅제약", "메디톡스", "파마리서치바이오", "제네톡스"],
        )
        self.assertEqual(
            r.stocks_meta,
            {"파마리서치바이오": "비상장", "제네톡스": "비상장"},
        )
        self.assertEqual(r.period_kind, "decadal_10")

    def test_sample_8_company_first_region_country(self):
        caption = (
            "오뚜기 : 라면 (경기 평택시_글로벌)\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "라면")
        self.assertEqual(r.region, "경기 평택시")
        self.assertEqual(r.country, "글로벌")
        self.assertEqual(r.stocks, ["오뚜기"])

    def test_sample_9_etc_suffix(self):
        caption = (
            "체성분 분석기 (전국)\n"
            "관련종목 : 인바디 / 셀바스헬스케어 등\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.stocks, ["인바디", "셀바스헬스케어"])
        self.assertTrue(r.has_etc)

    def test_sample_10_company_first_composite_with_ampersand(self):
        caption = (
            "덕산하이메탈 : 페이스트 + SB&MSB + CSB (울산 북구)\n"
            "\n"
            "2026년 4월 확정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "페이스트 + SB&MSB + CSB")
        self.assertTrue(r.is_composite)
        # `&` is part of name 'SB&MSB', not a separator
        self.assertEqual(r.composite_parts, ["페이스트", "SB&MSB", "CSB"])
        self.assertEqual(r.region, "울산 북구")
        self.assertEqual(r.stocks, ["덕산하이메탈"])

    # --- Extra variants surfaced by the earlier sample set ---

    def test_decadal_20_period(self):
        caption = (
            "라면 (전국)\n"
            "관련종목 : 삼양식품\n"
            "\n"
            "2026년 5월 1일 ~ 20일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.period_kind, "decadal_20")
        self.assertEqual(r.period_start, date(2026, 5, 1))
        self.assertEqual(r.period_end, date(2026, 5, 20))

    def test_import_direction(self):
        caption = (
            "염화칼륨 (전국)\n"
            "관련종목 : 유니드\n"
            "\n"
            "2026년 5월 1일 ~ 10일 잠정치 수입데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.direction, "import")
        self.assertEqual(r.stocks, ["유니드"])

    def test_composite_no_spaces_around_plus(self):
        caption = (
            "수산화칼륨(가성칼륨)+탄산칼륨 (전국)\n"
            "관련종목 : 유니드\n"
            "\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertTrue(r.is_composite)
        self.assertEqual(r.composite_parts, ["수산화칼륨(가성칼륨)", "탄산칼륨"])

    def test_country_simple(self):
        caption = (
            "라면 (전국_중국)\n"
            "관련종목 : 삼양식품 / 농심 / 오뚜기 등\n"
            "\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.country, "중국")
        self.assertEqual(r.stocks, ["삼양식품", "농심", "오뚜기"])
        self.assertTrue(r.has_etc)

    def test_multi_country(self):
        caption = (
            "선박추진용 엔진 (전국_중국+베트남)\n"
            "관련종목 : HD현대중공업\n"
            "\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.country, "중국")
        self.assertEqual(r.countries, ["중국", "베트남"])

    def test_en_dash_period_separator(self):
        caption = (
            "라면 (전국)\n"
            "관련종목 : 삼양식품\n"
            "\n"
            "2026년 5월 1일 – 10일 잠정치 수출데이터 입니다."  # en-dash
        )
        r = parse_caption(caption)
        self.assertIsNotNone(r)
        self.assertEqual(r.period_start, date(2026, 5, 1))
        self.assertEqual(r.period_end, date(2026, 5, 10))

    def test_synthesis_post_lands_in_unknown(self):
        # Inverted format from the original sample set —
        # title is "<companies> (region) : <items>" with no trailing region paren.
        # We log it as title_format_unknown rather than misparsing.
        caption = (
            "한화에어로스페이스 / 현대로템 등 (전국) : 전차와 그 밖의 장갑차량  + 부분품\n"
            "\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertIsNotNone(r)
        self.assertEqual(r.title_kind, "unknown")
        self.assertIn("title_format_unknown", r.parse_warnings)

    # --- Behavioral guarantees ---

    def test_non_alert_returns_none(self):
        self.assertIsNone(parse_caption("그냥 잡담"))
        self.assertIsNone(parse_caption(""))
        self.assertIsNone(parse_caption(None))  # type: ignore[arg-type]

    # --- RULE 11~14 (variants found in the post-backfill unknown-pile) ---

    def test_rule_11_multiline_title_with_parens_overflow(self):
        # 항생제 sample — title's first line opens a paren that the
        # second line closes; region paren is on the second line.
        caption = (
            "항생제 (카바페넴계, 이미페넴(IMIPENEM / 에르타페넴(Ertapenem))\n"
            "(전국_이탈리아)\n"
            "관련종목 : 하이텍팜\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.country, "이탈리아")
        self.assertEqual(r.stocks, ["하이텍팜"])
        self.assertIn("항생제", r.item)

    def test_rule_11_multiline_title_qualifier_then_region(self):
        # '해상용 안테나 부분품\n(VSAT ...) (전국)' — qualifier paren on
        # line 2 followed by the region paren, all one logical title.
        caption = (
            "해상용 안테나 부분품\n"
            "(VSAT , FBB , 저/중궤도 관련 안테나 매출 포함) (전국)\n"
            "관련종목 : 인텔리안테크\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.stocks, ["인텔리안테크"])
        self.assertIn("해상용 안테나 부분품", r.item)
        self.assertIn("VSAT", r.item)  # qualifier preserved in item

    def test_rule_12_inline_stocks_after_region_paren(self):
        # '수산화리튬 (전국): 미래나노텍 / 강원에너지 / ...'
        caption = (
            "수산화리튬 (전국): 미래나노텍 / 강원에너지 / 하이드로리튬 / POSCO홀딩스\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.item, "수산화리튬")
        self.assertEqual(r.region, "전국")
        self.assertEqual(
            r.stocks,
            ["미래나노텍", "강원에너지", "하이드로리튬", "POSCO홀딩스"],
        )

    def test_rule_13_trailing_text_after_region(self):
        # 'H형강 (전국) 합산' — paren in the middle with a qualifier
        # word after; the qualifier is folded into the item name.
        caption = (
            "H형강 (전국) 합산\n"
            "관련종목 : 현대제철 / 동국제강\n"
            "\n"
            "전국 300미리 미만 + 300~600미리 + 600미리 초과 합산\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.region, "전국")
        self.assertEqual(r.item, "H형강 합산")
        self.assertEqual(r.stocks, ["현대제철", "동국제강"])
        self.assertIn("title_has_trailing_text", r.parse_warnings)
        # commentary captures the breakdown explanation
        self.assertIsNotNone(r.commentary)
        self.assertIn("300미리", r.commentary)

    def test_rule_14_company_first_without_region(self):
        # 'LG디스플레이 : OLED 패널 + 평판디스플레이 모듈 + ...' —
        # company-first with no region paren anywhere.
        caption = (
            "LG디스플레이 : OLED 패널 + 평판디스플레이 모듈 + 평판디스플레이 + 유기발광다이오드 OLED 제조용\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["LG디스플레이"])
        self.assertIsNone(r.region)
        self.assertIsNone(r.country)
        self.assertTrue(r.is_composite)
        self.assertEqual(len(r.composite_parts), 4)
        self.assertIn("title_no_region", r.parse_warnings)

    def test_company_prefix_no_leading_space_with_region(self):
        # '휴스틸: 대구경...' (콜론 앞 공백 없음) — 옛 RULE 5가 못 잡아 회사가 품목에
        # 박히던 케이스. 이제 회사/품목/지역 정확 분리.
        cap = ("휴스틸: 대구경 철강제 관 (서울 강남구)\n\n"
               "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.")
        r = parse_caption(cap)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["휴스틸"])
        self.assertEqual(r.item, "대구경 철강제 관")
        self.assertIsNotNone(r.region)

    def test_company_prefix_no_region(self):
        cap = ("LG화학: 수산화리튬\n\n"
               "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.")
        r = parse_caption(cap)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["LG화학"])
        self.assertEqual(r.item, "수산화리튬")

    def test_company_prefix_composite_companies(self):
        cap = ("넥스틸 / 세아제강: 중소구경 철강제 관\n\n"
               "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.")
        r = parse_caption(cap)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["넥스틸", "세아제강"])
        self.assertEqual(r.item, "중소구경 철강제 관")

    def test_company_prefix_long_composite_not_dropped(self):
        # 회귀 방지: 긴 복합사(>20자)도 회사로 분리 — {2,20} 상한이 못 잡아 회사가
        # 품목에 박히던(company_first→item_first 거꾸로) 회귀.
        cap = ("HD현대일렉트릭 / 효성중공업 / LS ELECTRIC / 일진전기 등 : "
               "초고압 변압기(10,000kVA 초과) (전국)\n\n"
               "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.")
        r = parse_caption(cap)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.item, "초고압 변압기(10,000kVA 초과)")
        self.assertEqual(r.stocks,
                         ["HD현대일렉트릭", "효성중공업", "LS ELECTRIC", "일진전기"])

    def test_inline_stocks_not_stolen_by_company_prefix(self):
        # RULE 12 '품목 (지역): 회사/회사' — 콜론이 ')' 뒤라 회사접두로 안 샘.
        cap = ("라면 (전국): 삼양식품 / 농심\n\n"
               "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.")
        r = parse_caption(cap)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.item, "라면")
        self.assertIn("삼양식품", r.stocks)

    def test_synthesis_post_is_still_unknown_after_new_rules(self):
        # Inverted shape — guard against RULE 12/13 misclassifying it
        # as inline stocks or trailing text.
        caption = (
            "한화에어로스페이스 / 현대로템 등 (전국) : 전차와 그 밖의 장갑차량  + 부분품\n"
            "\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        r = parse_caption(caption)
        self.assertEqual(r.title_kind, "unknown")
        self.assertIn("title_format_unknown", r.parse_warnings)

    def test_dedup_key_groups_same_logical_alert(self):
        # 잠정 1-10일, 잠정 1-20일, 확정 월 모두 같은 dedup_key
        cap_a = (
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        cap_b = (
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 1일 ~ 20일 잠정치 수출데이터 입니다."
        )
        cap_c = (
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 확정치 수출데이터 입니다."
        )
        keys = {parse_caption(c).dedup_key() for c in (cap_a, cap_b, cap_c)}
        self.assertEqual(len(keys), 1)


_STATUS = "\n\n2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."


class CompanyFirstInvertedTests(unittest.TestCase):
    """'회사 (별칭/지역/비상장): 품목' (RULE 12 거꾸로) 전환 + 실제 RULE 12 회귀 0."""

    def setUp(self):
        self._m = mock.patch.object(pp, "_load_krx_master", return_value={
            "HD현대일렉트릭": "267260", "POSCO홀딩스": "005490", "SKC": "011790",
            "빙그레": "005180", "송원산업": "004430", "일진전기": "103590",
            "두산에너빌리티": "034020", "서흥": "008490",
            "삼양식품": "003230", "농심": "004370",   # 실제 RULE 12 우변 회사들
        })
        self._m.start()

    def tearDown(self):
        self._m.stop()

    def test_inverted_region_in_paren_Y(self):
        r = parse_caption("HD현대일렉트릭 (경기 성남시) : 초고압 변압기(10,000kVA 초과) + 배전반" + _STATUS)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["HD현대일렉트릭"])
        self.assertEqual(r.item, "초고압 변압기(10,000kVA 초과) + 배전반")
        self.assertEqual(r.region, "경기 성남시")

    def test_inverted_alias_region_in_Z(self):
        r = parse_caption("POSCO홀딩스(포스코필바라리튬솔루션) : 수산화리튬 (전남 광양시)" + _STATUS)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["POSCO홀딩스"])
        self.assertEqual(r.item, "수산화리튬")
        self.assertEqual(r.region, "전남 광양시")

    def test_inverted_bisangjang_marker_even_if_unlisted(self):
        # KP일렉트릭은 마스터에 없지만 '(비상장)' 마커로 회사 판정.
        r = parse_caption("KP일렉트릭 (비상장) : 소형 변압기(100kVA 이하) (인천 미추홀구_미국)" + _STATUS)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["KP일렉트릭"])
        self.assertEqual(r.item, "소형 변압기(100kVA 이하)")

    def test_inverted_alias_is_listed_parent(self):
        # 젤텍은 마스터에 없지만 별칭 '서흥'(상장)으로 좌변=회사 판정.
        r = parse_caption("젤텍(서흥): 젤라틴 (부산 강서구_글로벌)" + _STATUS)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["젤텍"])
        self.assertEqual(r.item, "젤라틴")

    def test_colon_no_space_after(self):
        r = parse_caption("에이치엔에스하이텍 :이방성도전필름 (Anisotropic Conductive Film) (경기 안산시)" + _STATUS)
        self.assertEqual(r.title_kind, "company_first")
        self.assertEqual(r.stocks, ["에이치엔에스하이텍"])

    # ===== 회귀 방지: 실제 RULE 12(품목 (지역): 회사)는 절대 안 뒤집힘 =====
    def test_real_rule12_multi_company_stays_item_first(self):
        r = parse_caption("라면 (전국): 삼양식품 / 농심" + _STATUS)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.item, "라면")
        self.assertEqual(set(r.stocks), {"삼양식품", "농심"})

    def test_real_rule12_stays_even_if_stock_unlisted(self):
        # 우변 회사가 마스터에 없어도, 좌변=품목('디램')이라 item_first 유지(회귀 0의 핵심).
        r = parse_caption("디램 (경기 용인시): 비상장반도체" + _STATUS)
        self.assertEqual(r.title_kind, "item_first")
        self.assertEqual(r.item, "디램")
        self.assertEqual(r.stocks, ["비상장반도체"])


if __name__ == "__main__":
    unittest.main()
