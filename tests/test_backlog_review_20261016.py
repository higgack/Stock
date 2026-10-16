"""2026-10-16 격주 수주잔고 리뷰 — 발췌 4건 중 셋의 원인.

픽스처는 리뷰가 실어 보낸 **원문 발췌 그대로**다(재구성 아님, #155).

· 056190 에스에프에이 — 형태 J(내수·수출 소계). 원문 합계 매출 칸의 산수가
  틀려(155,746 + 256,972 = 412,718 인데 421,718) 세 열을 다 요구하던 검산이
  표를 통째로 버렸다. 잔고 열은 맞는다.
· 376300 — `라. 수주계약 현황 … 해당사항 없습니다. 마. 공정가치평가 … 당반기말`
  의 `기말` 이 **앞 절 제목**의 `수주` 를 문맥으로 빌려 잔고 라벨이 됐다.
· 144960 — 계약자산 손상표의 `기말 계약자산` 이 주석 산문 `도급계약에` 의
  `도급` 을 문맥으로 빌렸다.
남은 하나(000670 영풍 합계행 3값)는 발췌만으로 원인을 못 가른다 — 고치지 않았다.
"""
import pytest

from bot.dart_backlog import (_NO_DATA_RE, _balance_matches, diagnose,
                              parse_backlog)

SFA_2025H1 = (
    "나. 수주에 관한 사황 (1) 사업부문별 수주/매출/수주잔고 현황 (단위 : 백만원) "
    "구 분 당기 신규수주액(제28기 상반기) 당기 매출액(제28기 상반기) "
    "기말 수주잔고액(제28기 상반기말) 이차전지솔루션 내수 3,851 45,074 12,160 "
    "수출 133,181 185,501 246,633 소계 137,032 230,575 258,793 합계 내수 115,871 "
    "155,746 283,638 수출 209,360 256,972 559,973 소계 325,231 421,718 843,611 "
    "주) 상기 수주/매출/수주잔고")

EX_376300 = (
    "라. 수주계약 현황 당사는 보고서 작성 기준일 현재 해당사항 없습니다. 마. 공정가치평가 "
    "내역 (1) 금융상품 공정가치당사의 보고서 작성 기준일 현재 회사의 금융상품 공정가치 "
    "서열체계 구분은 다음과 같습니다.① 당반기말 (단위 : 천원) 구분 수준1 수준2 수준3 "
    "합계 공정가치로 측정되는 금융자산 : 당기손익-공정가치금융자산 - - 2,900,000 2,900,000")

EX_144960 = (
    "약자산 (676,125) 전기말 (단위 : 천원) 금융상품의 종류 계약자산 장부금액 "
    "손상차손누계액 기초 계약자산 (84,984) 기대신용손실전(환)입, 계약자산 (1,163,356) "
    "제각을 통한 감소, 계약자산 0 기말 계약자산 (1,248,340) 계약자산 인식 및 회수 "
    "가능성에 대한 기술 연결기업은 제작이 완료되기 전까지는 도급계약에 대해 고객으로부터")


def test_source_typo_in_a_non_balance_column_does_not_drop_the_table():
    got = parse_backlog(SFA_2025H1)
    assert got and got["value"] == 843_611 * 1e6, got
    assert got["form"] == "내수·수출 소계"


def test_balance_column_alone_is_not_enough():
    """반대 증거 — 잔고 열만 맞고 다른 두 열이 다 틀리면 세 행이
    내수·수출·소계라는 증거가 없다."""
    bad = SFA_2025H1.replace("수출 209,360 256,972", "수출 250,000 300,000")
    assert parse_backlog(bad) is None


def test_balance_column_must_hold():
    bad = SFA_2025H1.replace("843,611", "900,000")
    assert parse_backlog(bad) is None


@pytest.mark.parametrize("text", [EX_376300, EX_144960], ids=["376300", "144960"])
def test_borrowed_context_is_not_a_balance_label(text):
    assert _balance_matches(text) == [], [m.group(0) for m in _balance_matches(text)]
    assert diagnose(text) == "미공시"


def test_section_heading_itself_still_lends_context():
    """반대 증거 — 절 머리가 곧 표의 문맥이면(`라. 수주상황`) 그대로 인정."""
    t = "라. 수주상황 (단위 : 백만원) 구분 전기말 신규 감소 당기말 A 1,000"
    assert [m.group(0) for m in _balance_matches(t)] == ["기말", "기말"]


@pytest.mark.parametrize("head", ["기본도급액", "도급금액", "도급공사", "도급계약액",
                                  "도급계약금액", "총도급", "도급계약 현황"])
def test_construction_header_words_still_lend_context(head):
    t = f"(단위 : 백만원) 구분 {head} 기초 증가 감소 기말 A 100 50 30 120"
    assert [m.group(0) for m in _balance_matches(t)][-1:] == ["기말"], head


def test_explicit_none_declaration_with_long_subject():
    t = ("4. 매출 및 수주상황 라. 수주계약 현황 당사는 보고서 작성 기준일 현재 해당사항 "
         "없습니다. (단위 : 백만원) 구분 수주잔고 A 1,000")
    assert _NO_DATA_RE.search(t)
    assert diagnose(t) == "명시적미공시"


def test_prose_dogeup_contract_is_not_context():
    t = ("(단위 : 천원) 구분 계약자산 기말 계약자산 (1,248,340) "
         "연결기업은 도급계약에 대해 고객으로부터")
    assert _balance_matches(t) == []


_FORM_A = ("(단위 : 백만원) 구분 기초 수익인식 기말 A사 1,000 400 600 "
           "합 계 1,000 400 600")


def test_lower_level_heading_under_the_order_section_keeps_context():
    """독립 리뷰 재현 — `나. 수주상황 … 가. 국내` 의 `가.` 는 하위 항목."""
    got = parse_backlog("나. 수주상황 현황은 다음과 같습니다. 가. 국내 " + _FORM_A)
    assert got and got["value"] == 600 * 1e6, got


def test_sibling_heading_without_spaces_still_cuts():
    t = ("라. 수주계약 현황 당사는 해당사항 없습니다.마.공정가치평가 내역 "
         "(단위 : 천원) 구분 당반기말 수준1 2,900,000")
    assert _balance_matches(t) == []


@pytest.mark.parametrize("decl", [
    "수주상황 당사는 보고서 작성 기준일 현재 해당사항이 없습니다.",
    "수주계약 현황 당사는 보고서 작성 기준일 현재 해당사항 없습니다.",
])
def test_whole_company_none_declarations(decl):
    assert _NO_DATA_RE.search(decl), decl


@pytest.mark.parametrize("part", [
    "나. 수주상황 (단위 : 백만원) 구분 수주총액 기납품액 수주잔고 해외 해당사항 없음 "
    "국내 1,000 400 합 계 1,000 400 7 8 9",
    "수주현황 해당사항 없는 부문은 제외하였습니다.",
    "수주현황 중 방산부문은 해당사항이 없습니다.",
    # 주어는 회사여도 문장이 아니라 **표 칸**(`없음`)이면 한 부문만 빈 것이다.
    "수주상황 당사는 다음과 같습니다 구분 해외 해당사항 없음 국내 1,000 400 600",
])
def test_one_segment_none_is_not_a_whole_company_declaration(part):
    assert not _NO_DATA_RE.search(part), part


# ── 000670 영풍 — 잔고에 기초잔고가 포함돼 가로 항등식이 안 선다 ─────────
# 원문(`--explain 000670` VM 출력) 그대로. 각주가 사유를 밝힌다.
def _yp(rows, total):
    return ("(단위 : M/T, 천㎡, 천개 / 백만원) 회사명 품목 수주일자 납기 수주총액 "
            "기납품액 수주잔고 수량 금액 수량 금액 수량 금액 " + rows + " 합 계 "
            + total + " ※ 수주총액은 사업계획등을 기준으로 작성되며, 수주잔고에는 "
            "기초잔고가 포함되어 있습니다. 5. 위험관리 및 파생거래")


YP_26Q1 = _yp(
    "㈜영풍 아연괴 2026.01.01~2026.12.31 2026.01.01~2026.12.31 55,226 245,858 "
    "52,732 264,255 2,494 -18,396 영풍전자㈜ FPCB 3,156 3,126 2,722 3,207 -2,843 "
    "-1,438 ㈜코리아써키트 PCB 249 374,713 163 228,513 86 146,200 ㈜인터플렉스 FPCB "
    "241 62,667 197 52,254 44 10,414 ㈜테라닉스 PCB 81 12,738 70 12,097 34 5,541",
    "- 699,103 - 560,326 - 142,321")
YP_25Q2 = _yp(
    "㈜영풍 아연괴 2025.01.01~2025.06.30 2025.01.01~2025.06.30 59,562 275,639 "
    "56,573 241,552 2,989 34,087 영풍전자㈜ FPCB 37,267 47,026 39,155 50,149 640 "
    "1,617 ㈜코리아써키트 PCB 431 498,700 330 361,325 101 137,375 ㈜인터플렉스 FPCB "
    "404,702 101,291 354,445 91,605 50,256 9,685 ㈜테라닉스 PCB 270 48,810 199 "
    "35,903 112 22,603", "- 971,466 - 780,534 - 205,367")
YP_25Q4 = _yp(
    "㈜영풍 아연괴 2025.01.01~2025.12.31 2025.01.01~2025.12.31 195,500 876,737 "
    "187,194 824,272 8,306 52,465 영풍전자㈜ FPCB 59,894 72,914 65,699 79,011 "
    "-3,277 -1,357 ㈜코리아써키트 PCB 811 1,005,296 690 818,583 121 186,713 "
    "㈜인터플렉스 FPCB 930 228,816 857 211,579 73 17,237 ㈜테라닉스 PCB 470 82,023 "
    "487 86,818 23 4,900", "- 2,265,786 - 2,020,264 - 259,958")


@pytest.mark.parametrize("text,want", [(YP_26Q1, 142_321), (YP_25Q2, 205_367),
                                       (YP_25Q4, 259_958)],
                         ids=["26.1Q", "25.2Q", "25.4Q"])
def test_opening_balance_included_table_reads_by_column_sums(text, want):
    got = parse_backlog(text)
    assert got and got["value"] == want * 1e6, got
    assert got["form"] == "표·합계행(세로합)"


@pytest.mark.parametrize("old,new", [
    ("10,414 ㈜테라닉스", "99,414 ㈜테라닉스"),        # 잔고 열 행 하나
    ("374,713 163 228,513", "374,713 163 300,000"),     # 기납품 열 행 하나
    ("- 699,103 - 560,326", "- 750,000 - 560,326"),     # 합계 총액
])
def test_column_sums_must_hold_in_every_column(old, new):
    assert YP_26Q1.count(old) == 1
    assert parse_backlog(YP_26Q1.replace(old, new)) is None, new


def test_a_row_that_is_not_three_or_six_cells_rejects():
    """표를 다 못 읽은 행이 있으면 세로 합은 우연히라도 믿지 않는다."""
    bad = YP_26Q1.replace("241 62,667 197 52,254 44 10,414",
                          "241 62,667 197 52,254 10,414")
    assert parse_backlog(bad) is None


def test_single_data_row_is_not_column_evidence():
    from bot.dart_backlog import _verify_columns
    assert _verify_columns([[10.0, 4.0, 9.0]], [10.0, 4.0, 9.0]) is None


def test_a_stray_short_run_is_not_silently_dropped():
    """반대 증거 — 3·6칸이 아닌 행을 **빼고** 더하면 세로 합이 우연히 맞는다.
    행 하나를 못 읽었다는 신호를 지우는 것이므로 거부해야 한다."""
    bad = YP_26Q1.replace("㈜테라닉스 PCB", "기타 7 8 ㈜테라닉스 PCB")
    assert parse_backlog(bad) is None


def test_six_cell_rows_without_a_quantity_amount_header_are_not_read_as_pairs():
    """독립 리뷰 재현 — 당기·전기 3열 두 벌을 나란히 둔 6칸 표. 세로 합은 어느
    열이든 서므로, (수량, 금액) 쌍이라는 근거(머리행)가 없으면 받지 않는다."""
    t = ("(단위 : 백만원) 구분 당기 수주총액 기납품액 수주잔고 전기 수주총액 기납품액 "
         "수주잔고 A 1,000 300 900 2,000 1,500 800 B 500 200 400 1,000 700 400 "
         "합 계 1,500 500 1,300 3,000 2,200 1,200 5. 위험관리")
    assert parse_backlog(t) is None


def test_nonpositive_total_balance_is_not_a_value():
    bad = (YP_26Q1.replace("- 142,321", "- -1,000")
           .replace("5,541", "-137,780"))
    assert parse_backlog(bad) is None


def test_pairs_need_the_quantity_amount_header_three_times():
    """`수량 금액` 이 한 번뿐이면 6칸이 세 쌍이라는 근거가 없다."""
    t = YP_26Q1.replace("수량 금액 수량 금액 수량 금액", "수량 금액")
    assert t != YP_26Q1 and parse_backlog(t) is None
