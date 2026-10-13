"""DART 🔥 중요 — 상·중 기준 추가 + 방향(호재·악재·판단) 분리.

사용자 2026-10-13: "상 중 우선순위 추가하고 호재 악재 색 나눠줘."
규칙은 `bot.dart_feed.significance_info` 한 곳, 화면(카드 색·필·범례)·
감사(dart_mcap_audit)·거버넌스 카드가 그 결과를 같이 쓴다(#38).
"""
from __future__ import annotations

import re

import pytest

from bot import dart_feed as df
from bot.dart_feed import (SIG_BAD, SIG_CRITERIA, SIG_GOOD, SIG_TONES,
                           SIG_WATCH, significance, significance_info)


def _it(rn, detail=(), cat="리스크", corp="테스트"):
    return {"report_nm": rn, "category": cat, "detail": list(detail),
            "corp_name": corp}


# ── 상 ────────────────────────────────────────────────────────────────

class TestProvisionalEarnings:
    """11 — 잠정실적 영업이익 전년동기 ±30% · 흑자/적자 전환(양방향)."""

    RN = "연결재무제표기준영업(잠정)실적(공정공시)"

    def test_form_c_up_and_down(self):
        up = significance_info(_it(self.RN, [
            "구분: 분기 잠정실적", "매출액: 1,000억원 (전년동기 900억원 · 전년동기대비 +11.1%)",
            "영업이익: 120억원 (전년동기 80억원 · 전년동기대비 +50.0%)"], "실적"))
        assert up == ("잠정 영업이익 전년동기 +50.0%", SIG_GOOD)
        dn = significance_info(_it(self.RN, [
            "영업이익: 50억원 (전년동기 100억원 · 전년동기대비 -50.0%)"], "실적"))
        assert dn == ("잠정 영업이익 전년동기 -50.0%", SIG_BAD)

    def test_form_a_shape_line_is_not_read(self):
        """Form A 의 '영업이익(당월):' 은 칸을 **모양**으로 갈라 억원 표에서
        금액·비율이 뒤바뀐다(독립 리뷰 2026-10-13) — 규칙은 읽지 않는다."""
        assert significance(_it("영업(잠정)실적(공정공시)", [
            "영업이익(당월): 120억원 (전월 +1.0% · 전년동월 +40.0%)"], "실적")) is None

    @staticmethod
    def _form_a(row: str, unit: str = "억원") -> list[str]:
        """실제 파서(Form A)를 태워 카드 줄을 만든다 — 손으로 쓴 줄이 아니라
        원문 표 → 파서 → 규칙 E2E 로 잰다(리뷰가 놓쳤다고 짚은 축)."""
        txt = (f"영업(잠정)실적(공정공시) 단위 : {unit}, % "
               f"매출액 당해실적 (26.06) 3,000 2,800 7.1 - 2,500 20.0 - "
               f"누계실적 (26.01~26.06) 5,800 - - - 5,000 16.0 - "
               f"영업이익 당해실적 (26.06) {row} "
               f"누계실적 (26.01~26.06) 2,000 - - - 1,500 33.3 - 2. 정보제공내역 IR")
        orig = df._fetch_doc_text
        df._fetch_doc_text = lambda *a, **k: txt
        try:
            return df._parse_earnings_doc("RX", "K", "영업(잠정)실적(공정공시)")["lines"]
        finally:
            df._fetch_doc_text = orig

    def test_parser_turnaround_in_eok_table_is_good(self):
        # 리뷰 재현: −50억 → +1,200억 흑자전환. 옛 판은 '전년동월 −50%' 악재.
        ls = self._form_a("1,200 900 33.3 - -50 - 흑자전환")
        assert "영업이익(전년동기): 1,200억원 (전년동기 -50억원 · 흑자전환)" in ls, ls
        assert significance_info(_it("영업(잠정)실적(공정공시)", ls, "실적")) == (
            "영업이익 흑자전환(전년동기)", SIG_GOOD)

    def test_flag_without_prior_amount(self):
        # 전년동기 금액 칸이 '-' 여도 원문이 흑자전환이라 적었으면 그걸 쓴다
        ls = self._form_a("1,200 900 33.3 - - - 흑자전환")
        assert "영업이익(전년동기): 1,200억원 (흑자전환)" in ls, ls
        assert significance_info(_it("영업(잠정)실적(공정공시)", ls, "실적")) == (
            "영업이익 흑자전환(전년동기)", SIG_GOOD)
        ls2 = self._form_a("-30 10 - 적자전환 - - 적자전환")
        assert significance_info(_it("영업(잠정)실적(공정공시)", ls2, "실적")) == (
            "영업이익 적자전환(전년동기)", SIG_BAD)

    def test_parser_small_eok_amounts_keep_position(self):
        # 리뷰 재현: 세 자리 금액(567)이 비율로 읽혀 당기가 1,234 로 밀리던 것
        ls = self._form_a("567 1,234 -54.1 - 980 -42.1 -")
        assert "영업이익(전년동기): 567억원 (전년동기 980억원 · 전년동기대비 -42.1%)" in ls
        assert significance_info(_it("영업(잠정)실적(공정공시)", ls, "실적")) == (
            "잠정 영업이익 전년동기 -42.1%", SIG_BAD)

    def test_parser_five_cell_form(self):
        ls = self._form_a("1,500 1,400 7.1 1,000 50.0", unit="백만원")
        # 5칸 서식의 전년동기 금액은 4번째 칸(1,000 백만원 = 10억원)
        assert "영업이익(전년동기): 15억원 (전년동기 10억원 · 전년동기대비 +50.0%)" in ls, ls
        assert significance_info(_it("영업(잠정)실적(공정공시)", ls, "실적")) == (
            "잠정 영업이익 전년동기 +50.0%", SIG_GOOD)

    def test_parser_unknown_shape_makes_no_line(self):
        ls = self._form_a("1,500 1,400 7.1")
        assert not any(x.startswith("영업이익(전년동기)") for x in ls)
        assert significance(_it("영업(잠정)실적(공정공시)", ls, "실적")) is None

    def test_form_c_parser_e2e(self):
        txt = ("단위 : 억원 구분 당기실적 전기실적 전년동기실적 "
               "매출액 당해실적 1,500 1,400 7.1 - 1,300 15.4 - "
               "영업이익 당해실적 300 250 20.0 - 200 50.0 - ")
        ls = df._provisional_lines(txt, 1e8)
        assert significance_info(_it("연결재무제표기준영업(잠정)실적(공정공시)",
                                     ls, "실적")) == ("잠정 영업이익 전년동기 +50.0%", SIG_GOOD)

    def test_cumulative_line_is_not_read(self):
        assert significance(_it("영업(잠정)실적(공정공시)", [
            "영업이익(누계): 120억원 (전년동기 +90.0%)"], "실적")) is None

    def test_turnarounds(self):
        assert significance_info(_it(self.RN, [
            "영업이익: -12억원 (전년동기 30억원 · 전년동기대비 -140.0%)"], "실적")) == (
            "영업이익 적자전환(전년동기)", SIG_BAD)
        assert significance_info(_it(self.RN, [
            "영업이익: 30억원 (전년동기 -12억원)"], "실적")) == (
            "영업이익 흑자전환(전년동기)", SIG_GOOD)

    def test_loss_range_pct_is_meaningless(self):
        # 적자지속 구간의 증감률(손실 축소 등)은 발화하지 않는다
        assert significance(_it(self.RN, [
            "영업이익: -12억원 (전년동기 -30억원 · 전년동기대비 +60.0%)"], "실적")) is None

    def test_annual_rule_stays_increase_only(self):
        # 연간 손익구조(규칙 1)는 사용자 2026-06-12 결정대로 증가만
        assert significance(_it("매출액또는손익구조30%(대규모법인15%)이상변경", [
            "영업이익: 10억원 (전기 50억원 · -80.0%)"], "실적")) is None


class TestMezzanine:
    """12 — CB/BW/EB 발행(잠재희석 또는 시총 5%) · 가액 조정."""

    def test_cb_dilution_line(self):
        it = _it("전환사채권발행결정", [
            "구분: 전환사채 발행 (3회차 · 사모) — 300억원",
            "전환: 전환가 10,000원 → 3,000,000주 = 주식총수의 7.5% (잠재 희석)"],
            "자금조달")
        assert significance_info(it) == ("주식총수 7.5% 잠재희석 전환사채", SIG_BAD)

    def test_small_dilution_does_not_fall_back_to_mcap(self):
        it = _it("전환사채권발행결정", [
            "구분: 전환사채 발행 — 300억원",
            "전환: 전환가 10,000원 → 300,000주 = 주식총수의 2.0% (잠재 희석)"],
            "자금조달")
        # 원문이 희석률을 밝혔으면 그게 기준이다(시총 대비가 커도)
        assert significance(it, market_cap=1e9) is None

    def test_bw_amount_vs_mcap(self):
        it = _it("신주인수권부사채권발행결정", ["권면총액: 100억원"], "자금조달")
        assert significance(it) is None            # 시총 모르면 미발화
        assert significance_info(it, market_cap=1000e8) == (
            "시총대비 10.0% 신주인수권부사채", SIG_BAD)
        assert significance(it, market_cap=10000e8) is None

    def test_eb_is_watch(self):
        it = _it("교환사채권발행결정", ["권면총액: 100억원"], "자금조달")
        assert significance_info(it, market_cap=1000e8) == (
            "시총대비 10.0% 교환사채", SIG_WATCH)

    def test_head_line_amount(self):
        it = _it("전환사채권발행결정", ["구분: 전환사채 발행 (1회차) — 1.2조원",
                                     "이자: 표면 0% / 만기 1%"], "자금조달")
        assert significance_info(it, market_cap=10e12) == (
            "시총대비 12.0% 전환사채", SIG_BAD)

    def test_subsidiary_and_self_bond_buyback_excluded(self):
        assert significance(_it("전환사채권발행결정(종속회사의주요경영사항)",
                                ["권면총액: 900억원"], "자금조달"),
                            market_cap=1000e8) is None
        assert significance(_it("자기전환사채만기전취득결정",
                                ["권면총액: 900억원"], "주주환원"),
                            market_cap=1000e8) is None
        # 제목에 '발행' 이 같이 있어도 만기전 취득(희석 제거)은 발행이 아니다
        assert significance(_it("전환사채(해외전환사채포함)발행후만기전사채취득",
                                ["권면총액: 900억원"], "주주환원"),
                            market_cap=1000e8) is None

    def test_price_adjustments(self):
        for rn, lbl in (("전환가액의조정", "전환가액 조정"),
                        ("신주인수권행사가액의조정", "행사가액 조정"),
                        ("교환가액의조정", "교환가액 조정")):
            # 원문 파서가 없어 방향(시가하락 리픽싱 ↔ 기계적·상향 조정)을 못
            # 재므로 판단(독립 리뷰 2026-10-13 — 옛 판은 악재 단정)
            assert significance_info(_it(rn, [], "자금조달")) == (lbl, SIG_WATCH), rn


class TestSurvivalRisks:
    """13·14·15 — 감사의견 비적정 · 횡령/배임 · 회생/부도/해산/파산."""

    @pytest.mark.parametrize("rn,lbl", [
        ("감사보고서제출(감사의견거절)", "감사의견 비적정"),
        ("주권매매거래정지(감사의견 한정)", "감사의견 비적정"),
        ("횡령ㆍ배임혐의발생", "횡령·배임"),
        ("회생절차개시신청", "회생절차"),
        ("부도발생", "부도 발생"),
        ("해산사유발생", "해산사유 발생"),
        ("파산신청", "파산"),
        ("채권은행등의관리절차개시", "채권은행 관리절차"),
    ])
    def test_fires_bad(self, rn, lbl):
        assert significance_info(_it(rn)) == (lbl, SIG_BAD)

    def test_audit_from_reason_line(self):
        assert significance_info(_it("주권매매거래정지", ["사유: 감사의견 거절"])) == (
            "감사의견 비적정", SIG_BAD)

    def test_adequate_opinion_does_not_fire(self):
        assert significance(_it("감사보고서제출", ["사유: 감사의견 적정"])) is None

    def test_rehab_closed_is_good(self):
        assert significance_info(_it("회생절차종결결정")) == ("회생절차 종결", SIG_GOOD)

    def test_free_text_lines_do_not_fire(self):
        assert significance(_it("기타경영사항(자율공시)", ["비고: 횡령 사실 없음"])) is None
        # 자율공시 '제목:' 자유 서술은 남의 사건일 수 있다
        assert significance(_it("기타경영사항(자율공시)",
                                ["제목: 파산한 거래처에 대한 대손 반영"], "기타")) is None

    @pytest.mark.parametrize("rn,detail", [
        ("조회공시요구(풍문또는보도)(부도설)", []),
        ("조회공시요구(부도설)에대한답변(부인)", []),
        ("조회공시요구(감사의견거절설)에대한답변", []),
        ("풍문또는보도에대한해명", ["제목: 횡령설"]),
    ])
    def test_rumor_titles_are_not_events(self, rn, detail):
        """확인 요청·부인은 사건이 아니다(독립 리뷰 2026-10-13 재현)."""
        assert significance(_it(rn, detail, "조회공시")) is None

    def test_background_reason_on_other_report_does_not_fire(self):
        # 리뷰 재현: 최대주주변경의 '사유: 회생절차에 따른 출자전환'
        assert significance_info(_it("최대주주변경", ["사유: 회생절차에 따른 출자전환"],
                                     "회사구조")) == ("최대주주 변경", SIG_WATCH)


# ── 중 ────────────────────────────────────────────────────────────────

class TestMiddleTier:
    def test_major_holder_change_is_watch(self):
        assert significance_info(_it("최대주주변경", [], "회사구조")) == (
            "최대주주 변경", SIG_WATCH)
        assert significance_info(_it("최대주주변경을수반하는주식양수도계약체결",
                                     [], "회사구조")) == ("최대주주 변경", SIG_WATCH)
        # '변경' 없이 지분 양수도 계약만 적힌 제목도 같은 사건이다
        assert significance_info(_it("최대주주의주식양수도계약체결", [],
                                     "회사구조")) == ("최대주주 변경", SIG_WATCH)
        # 공정거래법 소유주식 변동 신고는 지분 변동 보고일 뿐 — 미발화
        assert significance(_it("최대주주등소유주식변동신고서", [], "지분공시")) is None

    def test_physical_split(self):
        assert significance_info(_it("회사분할결정", ["분할방법: 물적분할 방식"],
                                     "회사구조")) == ("물적분할", SIG_BAD)
        assert significance_info(_it("회사분할결정", ["물적분할 철회", "사유: 시장 상황"],
                                     "회사구조")) == ("물적분할 철회", SIG_GOOD)
        assert significance(_it("회사분할결정", ["분할방법: 인적분할"], "회사구조")) is None
        assert significance(_it("회사분할합병결정", ["분할방법: 물적분할"],
                                "자산양수도")) is None

    def test_tender_offer(self):
        assert significance_info(_it("공개매수의결과", ["대상: (주)테스트",
                                                    "매수가: 주당 10,000원"],
                                     "회사구조", corp="테스트")) == (
            "공개매수(대상 회사)", SIG_GOOD)
        assert significance_info(_it("공개매수신고서", ["대상: 다른회사"],
                                     "회사구조", corp="테스트")) == (
            "공개매수", SIG_WATCH)
        assert significance_info(_it("공개매수에관한의견표명서", [], "회사구조")) == (
            "공개매수(대상 회사)", SIG_GOOD)

    def test_bonus_issue(self):
        assert significance_info(_it("무상증자결정", ["1주당 배정: 1"], "자금조달")) == (
            "무상증자 1주당 1주", SIG_GOOD)
        assert significance_info(_it("무상증자결정", ["구분: 무상증자",
                                                  "신주: 보통주 5,000,000주 (+50.0%)"],
                                     "자금조달")) == ("무상증자 1주당 0.5주", SIG_GOOD)
        assert significance(_it("무상증자결정", ["1주당 배정: 0.1"], "자금조달")) is None
        assert significance(_it("유무상증자결정", ["1주당 배정: 1"], "자금조달")) is None

    def test_capital_reduction_and_merge(self):
        assert significance_info(_it("감자결정", ["사유: 결손보전을 위한 무상감자"],
                                     "자금조달")) == ("무상감자", SIG_BAD)
        assert significance_info(_it("감자결정", ["사유: 결손금 보전"], "자금조달")) == (
            "무상감자", SIG_BAD)
        # 델타 리뷰 재현: 무상감자 뒤 유상증자 — '유상' 낱말에 속지 않는다
        assert significance_info(_it("감자결정",
                                     ["사유: 결손금 보전 및 유상증자를 위한 재무구조 개선"],
                                     "자금조달")) == ("무상감자", SIG_BAD)
        # 방식을 모르면 단정하지 않는다
        assert significance_info(_it("감자결정", [], "자금조달")) == (
            "감자 결정(방식 원문 확인)", SIG_WATCH)
        # 감자 파서는 방식을 '사유:' 로만 남긴다 — 실제 줄 모양으로
        assert significance_info(_it("감자결정", ["사유: 주주가치 제고를 위한 유상감자"],
                                     "자금조달")) == ("유상감자", SIG_WATCH)
        # 정지 공시는 감자·병합 '결정' 이 아니다
        assert significance(_it("주권매매거래정지(감자)")) is None
        assert significance(_it("주권매매거래정지(주식병합)")) is None
        assert significance_info(_it("주식병합결정", [], "회사구조")) == (
            "주식병합", SIG_WATCH)

    def test_halt(self):
        assert significance_info(_it("주권매매거래정지",
                                     ["사유: 횡령 혐의 관련 확인"])) is not None
        assert significance_info(_it("주권매매거래정지",
                                     ["사유: 상장적격성 검토"])) == (
            "매매거래정지", SIG_BAD)
        # 시장경보(급등 과열) 하루 정지는 판단
        assert significance_info(_it("주권매매거래정지",
                                     ["사유: 투자경고종목 지정"])) == (
            "매매거래정지(시장경보)", SIG_WATCH)
        # 합병·교환·감자 정지는 기계적
        for why in ("사유: 회사합병", "사유: 주식의 포괄적 교환",
                    "사유: 자본감소(감자)"):
            assert significance(_it("주권매매거래정지", [why])) is None, why
        # 시장 꼬리표 괄호는 사유가 아니다
        assert significance(_it("주권매매거래정지(코스닥)")) is None
        # 기간 변경 양식은 '변경사유:' 로 온다
        assert significance_info(_it("주권매매거래정지기간변경",
                                     ["변경사유: 상장적격성 검토 연장"])) == (
            "매매거래정지", SIG_BAD)
        # 기계적 정지는 악재가 아니다
        for why in ("사유: 주식병합(액면병합)으로 인한 구주권 제출",
                    "사유: 중요내용 공시", "사유: 조회공시 요구"):
            assert significance(_it("주권매매거래정지", [why])) is None, why
        assert significance(_it("주권매매거래정지(중요내용공시)")) is None
        # 사유를 모르면 기계적 정지일 수 있어 발화하지 않는다
        assert significance(_it("주권매매거래정지")) is None
        assert significance(_it("주권매매거래정지해제", ["사유: 투자경고 해제"])) is None

    def test_review_lows(self):
        # 다른 해제가 같이 실린 정지 공시는 관리종목 해제가 아니다
        assert significance_info(_it("매매거래정지및정지해제(관리종목지정)")) == (
            "관리종목 관련", SIG_BAD)
        assert significance_info(_it("기타시장안내(감사의견 비적정)")) is not None
        assert significance(_it("최대주주변경을수반하는주식담보제공계약체결",
                                [], "회사구조")) is None
        assert significance(_it("불성실공시법인지정유예")) is None

    def test_bonus_issue_prefers_stated_ratio(self):
        # 리뷰 재현: '신주 (+47.5%)'(분모에 자사주)가 '1주당 0.5주' 를 이겼다
        assert significance_info(_it("무상증자결정", [
            "구분: 무상증자", "신주: 보통주 4,750,000주 (+47.5%)",
            "배정: 1주당 0.5주"], "자금조달")) == ("무상증자 1주당 0.5주", SIG_GOOD)

    def test_tender_offer_target_by_filer(self):
        it = _it("공개매수신고서", [], "회사구조", corp="대상회사")
        it["flr_nm"] = "매수자홀딩스"
        assert significance_info(it) == ("공개매수(대상 회사)", SIG_GOOD)
        it2 = _it("공개매수신고서", [], "회사구조", corp="매수자홀딩스")
        it2["flr_nm"] = "매수자홀딩스"
        assert significance_info(it2) == ("공개매수", SIG_WATCH)

    def test_krx_notice_lines_and_listing_review_halt(self):
        # 기타시장안내는 '기타' 로 분류돼도 제목·결론 줄이 사건이다
        assert significance_info(_it("기타시장안내", ["제목: 회생절차개시 신청 관련"],
                                     "기타")) == ("회생절차", SIG_BAD)
        assert significance_info(_it("기타시장안내",
                                     ["결론: 거래소는 감사의견 거절에 따라 조치"],
                                     "기타")) == ("감사의견 비적정", SIG_BAD)
        # 정지 사유 횡령은 그대로 잡힌다(리스크 분류)
        assert significance_info(_it("주권매매거래정지",
                                     ["사유: 횡령ㆍ배임 혐의 발생"])) == (
            "횡령·배임", SIG_BAD)
        # 우회상장 심사 정지는 '합병' 이 있어도 기계적이 아니다
        assert significance_info(_it("주권매매거래정지",
                                     ["사유: 우회상장 관련 합병 심사"])) == (
            "매매거래정지", SIG_BAD)

    def test_unfaithful(self):
        assert significance_info(_it("불성실공시법인지정")) == (
            "불성실공시법인 지정", SIG_BAD)
        assert significance_info(_it("불성실공시법인지정예고")) == (
            "불성실공시법인 지정예고", SIG_BAD)
        assert significance(_it("불성실공시법인지정취소")) is None


# ── 기존 규칙의 방향 ──────────────────────────────────────────────────

class TestExistingRuleTones:
    def test_tones(self):
        cases = [
            (_it("단일판매ㆍ공급계약체결", ["매출액대비: 25.0%"], "계약"), SIG_GOOD),
            (_it("상장폐지결정"), SIG_BAD),
            (_it("기타시장안내(관리종목지정우려종목)"), SIG_BAD),
            (_it("관리종목지정해제"), SIG_GOOD),
            (_it("주주제안", [], "회사구조"), SIG_WATCH),
            (_it("현금ㆍ현물배당결정", ["시가배당률: 5.0%"], "배당"), SIG_GOOD),
        ]
        for it, tone in cases:
            assert significance_info(it)[1] == tone, it

    def test_release_line_does_not_flip_designation(self):
        # '해제·만료:' 칸은 정지 기간이라 '해제' 를 늘 품는다 — 지정이 해제로
        # 뒤집히면 안 된다.
        assert significance_info(_it("주권매매거래정지", [
            "사유: 관리종목 지정", "해제·만료: 관리종목 지정해제 시까지"])) == (
            "관리종목 관련", SIG_BAD)

    def test_rights_issue_method(self):
        it = _it("유상증자결정", ["구분: 유상증자 (주주배정후 실권주 일반공모)",
                               "용도: 운영 100억원"], "자금조달")
        assert significance_info(it, market_cap=1000e8) == (
            "시총대비 10.0% 유상증자", SIG_BAD)
        it3 = _it("유상증자결정", ["구분: 유상증자 (제3자배정증자)",
                                "용도: 운영 100억원"], "자금조달")
        assert significance_info(it3, market_cap=1000e8) == (
            "시총대비 10.0% 유상증자(제3자배정)", SIG_WATCH)

    def test_subsidiary_rights_issue_excluded(self):
        # 리뷰 재현: 종속회사 증자 500억을 모회사 시총 1,000억 대비 50% 악재로
        it = _it("유상증자결정(종속회사의주요경영사항)",
                 ["구분: 종속회사(ABC) 유상증자 (제3자배정)", "용도: 운영 500억원"],
                 "자금조달")
        assert significance(it, market_cap=1000e8) is None

    def test_third_party_from_api_fallback(self):
        it = _it("유상증자결정", ["방식: 제3자배정증자", "운영자금: 100억원"], "자금조달")
        assert significance_info(it, market_cap=1000e8)[1] == SIG_WATCH

    def test_rights_amount_from_doc_parser_lines(self):
        """원문 파서 카드('용도:'·'신주: … =')에서도 조달액을 읽는다 — 옛 판은
        라벨형('운영자금:')만 읽어 이 카드에서 규칙이 죽어 있었다."""
        it = _it("유상증자결정", ["구분: 유상증자 (일반공모)",
                               "용도: 운영 60억원 · 시설 40억원"], "자금조달")
        assert significance(it, market_cap=1000e8) == "시총대비 10.0% 유상증자"
        it2 = _it("유상증자결정", ["구분: 유상증자 (일반공모)",
                                "신주: 보통주 1,000,000주 (+10.0%) @ 10,000원 = 100억원"],
                  "자금조달")
        assert significance(it2, market_cap=1000e8) == "시총대비 10.0% 유상증자"
        it3 = _it("유상증자결정", ["운영자금: 100억원"], "자금조달")
        assert significance(it3, market_cap=1000e8) == "시총대비 10.0% 유상증자"

    def test_significance_is_reason_of_info(self):
        it = _it("부도발생")
        assert significance(it) == significance_info(it)[0]
        assert significance(_it("기타경영사항")) is None


class TestCriteriaTextMatchesRules:
    """범례 문구(SIG_CRITERIA)는 규칙과 같이 고친다 — 방향마다 문구에 적힌
    대표 기준이 실제로 **그 방향**으로 발화하는지 잰다(문구만 남고 규칙이
    사라지거나, 규칙만 방향이 바뀌는 드리프트를 잡는다)."""

    def test_each_tone_has_criteria(self):
        assert set(SIG_CRITERIA) == set(SIG_TONES) == {SIG_GOOD, SIG_BAD, SIG_WATCH}

    @pytest.mark.parametrize("word,it,tone", [
        ("회생절차 종결", _it("회생절차종결결정"), SIG_GOOD),
        ("무상증자 1주당 0.5주", _it("무상증자결정", ["1주당 배정: 0.5"]), SIG_GOOD),
        ("감사의견 비적정", _it("감사의견거절"), SIG_BAD),
        ("횡령/배임", _it("횡령혐의발생"), SIG_BAD),
        ("불성실공시법인", _it("불성실공시법인지정"), SIG_BAD),
        ("주식병합", _it("주식병합결정"), SIG_WATCH),
        ("최대주주 변경", _it("최대주주변경"), SIG_WATCH),
    ])
    def test_listed_word_fires_in_that_tone(self, word, it, tone):
        assert word in SIG_CRITERIA[tone]
        assert significance_info(it)[1] == tone


# ── 화면 ──────────────────────────────────────────────────────────────

def _render(items_by_date):
    import bot.dashboard as d
    return d._render_dart_feed_page(items_by_date)[0]


def _card(rno, rn, detail, cat="리스크"):
    return {"rcept_no": rno, "date": "20261013", "corp_name": "테스트",
            "stock_code": "123456", "url": "#", "category": cat,
            "report_nm": rn, "detail": list(detail)}


class TestDashboardTones:
    def _html(self):
        return _render({"2026-10-13": [
            _card("20261013000001", "부도발생", ["사유: 어음 부도"]),
            _card("20261013000002", "단일판매ㆍ공급계약체결",
                  ["매출액대비: 30.0%", "계약금액: 300억원"], "계약"),
            _card("20261013000003", "최대주주변경", ["최대주주: A → B"], "회사구조"),
            _card("20261013000004", "횡령ㆍ배임혐의발생", ["사유: 전 대표 횡령"]),
            _card("20261013000005", "기타경영사항(자율공시)", ["제목: 일반"], "기타"),
        ]})

    def test_cards_carry_tone_class_flag_and_badge(self):
        html = self._html()
        cards = re.findall(r'<div class="(df-card[^"]*)" data-cat="[^"]*" '
                           r'data-flag="([^"]*)" data-name="[^"]*" data-report="([^"]*)"',
                           html)
        by = {rep: (cls, flag) for cls, flag, rep in cards}
        assert "df-sig-bad" in by["부도발생"][0]
        assert by["부도발생"][1].split() == ["sig", "sig-bad"]
        assert "df-sig-good" in by["단일판매ㆍ공급계약체결"][0]
        assert by["최대주주변경"][1].split() == ["sig", "sig-watch"]
        assert "df-significant" not in by["기타경영사항(자율공시)"][0]
        assert '🚨 부도 발생</span>' in html
        assert '🔥 매출대비 30% 계약</span>' in html
        assert '⚖️ 최대주주 변경</span>' in html

    def test_pills_per_tone_and_counts(self):
        html = self._html()
        assert re.search(r'data-flag="sig-good">🔥 호재 1<', html)
        assert re.search(r'data-flag="sig-bad">🚨 악재 2<', html)
        assert re.search(r'data-flag="sig-watch">⚖️ 판단 1<', html)
        # 옛 단일 필은 없다(합계는 감사가 방향 필 합으로 잰다)
        assert 'data-flag="sig">' not in html

    def test_legend_lists_each_present_tone(self):
        html = self._html()
        leg = next(m.group(1) for m in re.finditer(
            r'<p class="sub" style="margin:-6px 0 14px">(.*?)</p>', html, re.S)
            if "df-badge-sig" in m.group(1))
        for t, (em, nm) in SIG_TONES.items():
            assert f"{em} {nm}</span>" in leg, t
        assert "빨강" in leg and "보라" in leg and "금색" in leg

    def test_absent_tone_has_no_pill_or_legend(self):
        html = _render({"2026-10-13": [
            _card("20261013000002", "단일판매ㆍ공급계약체결",
                  ["매출액대비: 30.0%"], "계약")]})
        assert 'data-flag="sig-bad"' not in html
        assert "🚨 악재</span>" not in html

    def test_css_tone_rules_follow_gold_rule(self):
        """빨강·보라 테두리는 금색(df-significant) **뒤**에 와야 이긴다 —
        같은 명시도에선 뒤엣것이 이긴다(#300)."""
        html = self._html()
        gold = html.index(".df-card.df-significant{")
        assert html.index(".df-card.df-sig-bad{") > gold
        assert html.index(".df-card.df-sig-watch{") > gold

    def test_js_flag_match_or_within_tones_and_tokens(self):
        """방향 필끼리는 OR(한 카드는 방향이 하나라 AND 면 0장), 다른 플래그와는
        AND, 부분문자열이 아니라 토큰. 렌더된 페이지의 **실제 함수**를 node 로
        태운다."""
        import shutil
        import subprocess
        html = self._html()
        m = re.search(r"function dfFlagMatch\(cf,flags\)\{.*?\n  \}", html, re.S)
        assert m, "dfFlagMatch 없음"
        assert "dfFlagMatch((c.dataset.flag||'').split(' '),activeFlags)" in html
        node = shutil.which("node")
        if not node:
            pytest.skip("node 없음")
        js = m.group(0) + """
var bad=['sig','sig-bad'], good=['sig','sig-good'], unp=['sig','sig-bad','unparsed'];
console.log([
  dfFlagMatch(bad,['sig-bad','sig-good']), dfFlagMatch(good,['sig-bad','sig-good']),
  dfFlagMatch(['x'],['sig-bad','sig-good']), dfFlagMatch(bad,['sig-bad','unparsed']),
  dfFlagMatch(unp,['sig-bad','unparsed']), dfFlagMatch(bad,[]),
  dfFlagMatch(['sig-badx'],['sig-bad'])].join(','));"""
        out = subprocess.run([node, "-e", js], capture_output=True, text=True,
                             timeout=30).stdout.strip()
        assert out == "true,true,false,false,true,true,false", out


class TestDashboardMezzanineMcapWiring:
    def test_bond_card_gets_market_cap(self, monkeypatch):
        """메자닌 카드는 원문이 희석률을 안 밝히면 시총으로 잰다 — 화면
        주석 패스가 시총을 넘기는지(배선) 실제 렌더로 본다(#20)."""
        import bot.dashboard as d
        monkeypatch.setattr(df, "_market_cap_won", lambda code: 1000e8)
        html = _render({"2026-10-13": [
            _card("20261013000009", "신주인수권부사채권발행결정",
                  ["권면총액: 100억원", "행사가격: 10,000원"], "자금조달")]})
        assert "🚨 시총대비 10.0% 신주인수권부사채</span>" in html


class TestAuditCountsTonePills:
    def test_audit_pattern_matches_rendered_pills(self):
        """dart_mcap_audit 의 필 패턴이 실제 렌더된 필을 읽는가(#47)."""
        html = TestDashboardTones()._html()
        got = {t: int(re.search(rf'data-flag="sig-{t}">[^<]*? (\d+)<', html).group(1))
               for t in SIG_TONES}
        assert got == {SIG_GOOD: 1, SIG_BAD: 2, SIG_WATCH: 1}
        src = open("bot/scripts/dart_mcap_audit.py", encoding="utf-8").read()
        assert "rf'data-flag=\"sig-{t}\">[^<]*? (\\d+)<'" in src


class TestGovernanceUsesTone:
    def test_tone_emoji_in_governance_card(self, monkeypatch):
        import bot.governance as g
        src = open("bot/governance.py", encoding="utf-8").read()
        assert "significance_info(it)" in src and "SIG_TONES.get(tone" in src
        # 같은 표에서 이모지를 꺼낸다 — 악재는 🚨
        em, nm = SIG_TONES[significance_info(_it("횡령혐의발생"))[1]]
        assert (em, nm) == ("🚨", "악재")
        assert g is not None
