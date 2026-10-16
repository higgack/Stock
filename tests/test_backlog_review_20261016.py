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
