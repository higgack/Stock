"""2026-11-13 격주 수주잔고 보고서 — 원문이 스스로 밝힌 미공시 2건.

437730 · 043260 은 '헤더에 … 열 없음' 으로 고칠 목록에 올랐지만, 발췌는 원문이
수주잔고를 '비공개 사항' 이라 하거나 '발생하지 않는다' 고 밝힌다. 파서로 고칠
것이 없으므로 `명시적미공시` 다. 단 그 선언은 한 부문만 말할 수 있어, 잔고 표
머리행이 있는 문서에서는 개선 여지로 남긴다(#385).
"""
import pytest

from bot.dart_backlog import _NO_DATA_RE, _NO_TABLE_DECL_RE, diagnose

# 보고서 발췌 그대로(437730 2026/11012).
_437730 = ("합 계 국 내 29,514 74,330 77,797 수 출 10,246 20,712 22,651 합 계 39,760 "
           "95,042 100,448 (5) 수주현황 수주잔고에 대한 상세 내용은 거래 상대방과의 "
           "영업과 관련된 기밀 또는 고객사의 신규 프로젝트 등 비공개 사항으로 관련 "
           "내용을 기재하지 않았습니다.")
# 보고서 발췌 그대로(043260 2026/11012).
_043260 = ("마. 수주상황 ① 콘덴서 및 파워: 가전·산업장비 OEM 고객 대상 반복 공급(Run-rate) "
           "거래 및 단기 발주(Forecast 기반) 거래로 운영되어, 장기매출계약 또는 장기용역 "
           "제공계약 형태의 수주잔고가 발생하지 않습니다. ③ 건설: 건설사 발주 기반의 단기 "
           "납품 거래로, 수주~납품 리드타임이 짧아 별도 수주잔고 관리 대상이 아닙니다.")
# 같은 문서에 관문을 지나는 잔고 표가 있으면 선언은 한 부문의 것일 수 있다.
_TABLE = (" (단위 : 백만원) 구분 수주일자 납기 수주총액 기납품액 수주잔고 "
          "A사 2026.01 2027.01 1,000 400")


def test_declarations_are_soft_only():
    """이 어구는 표가 없을 때만 믿는다 — 강한 정규식에 들어가면 안 된다."""
    for t in (_437730, _043260):
        assert _NO_TABLE_DECL_RE.search(t), t
        assert not _NO_DATA_RE.search(t), t


def test_no_table_declarations_are_explicit_nondisclosure():
    assert diagnose(_437730) == "명시적미공시"
    assert diagnose(_043260) == "명시적미공시"


def test_each_branch_alone():
    """갈래마다 그 갈래만 맞는 예문(#91b)."""
    assert diagnose("수주잔고가 발생하지 않습니다.") == "명시적미공시"
    assert diagnose("별도 수주잔고 관리 대상이 아닙니다.") == "명시적미공시"
    assert diagnose("수주잔고의 내용은 비공개 사항입니다.") == "명시적미공시"


def test_gated_table_keeps_the_gap():
    """반대 증거 — 관문을 지난 잔고 표가 있으면 선언이 있어도 개선 여지다."""
    for decl in (_437730, _043260):
        assert diagnose(decl + _TABLE) != "명시적미공시", decl


def test_footnote_about_other_things_is_not_declaration():
    assert not _NO_TABLE_DECL_RE.search("수주잔고 산정 시 발주처명은 비공개입니다.")


# ── 448280 — 머리열이 `수주총액` 이 아니라 `수주금액` ────────────────────
# `--ticker 448280` VM 출력 그대로(26.2Q). 수량·금액 쌍 6칸이라 금액만 쓴다.
_448280 = ("라. 수주상황 [탄소배출권 이전계약 현황] (기준일: 2026년 06월 30일) (단위 : 톤, 백만원) "
           "사업유치국 사업유형 품목 수주일자 납 기 수주금액 기납품액 수주잔고 비고 수 량 금 액 "
           "수 량 금 액 수 량 금 액 미얀마 쿡스토브 i-KOC 2018-09-27 i-KOC 이전일 150,000 1,698 "
           "100,000 1,132 50,000 566 - 미얀마 쿡스토브 i-KOC 2019-04-30 i-KOC 이전일 30,000 363 "
           "19,565 237 10,435 126 - 합계 2,825,303 15,479 1,078,335 6,999 1,746,968 8,480 - "
           "마. 판매경로")


def test_contract_amount_header_opens_the_gate():
    from bot.dart_backlog import parse_backlog
    got = parse_backlog(_448280)
    assert got and got["value"] == 8_480 * 1e6, got   # 15,479 − 6,999 = 8,480


# ── 452280 — 원문이 스스로 밝힌 미공시, 옛 어휘 밖의 두 꼴 ─────────────
# `--ticker 452280` VM 출력 그대로. 25.3Q 는 위 `내용은 … 기밀` 갈래가 받는다.
_452280_253 = ("다. 수주상황 수주잔고에 대한 내용은 당사 및 고객사의 영업과 관련된 기밀 등의 "
               "사유로 기재를 생략합니다. 5. 위험관리 및 파생거래")


def test_452280_q3_is_explicit_nondisclosure():
    assert diagnose(_452280_253) == "명시적미공시"


def test_hard_to_estimate_is_explicit_nondisclosure():
    """`산정하기 곤란` 갈래만 맞는 예문(#91b)."""
    t = "장래 매출로 확정된 수주금액 및 수주잔고를 합리적으로 산정하기 곤란합니다."
    assert _NO_DATA_RE.search(t), t
    assert diagnose(t) == "명시적미공시"


def test_formal_not_written_is_explicit_nondisclosure():
    """`기재하지 아니합니다` 갈래만 맞는 예문(#91b)."""
    t = "이에 따라 수주잔고 금액은 별도로 기재하지 아니합니다."
    assert _NO_DATA_RE.search(t), t
    assert diagnose(t) == "명시적미공시"


# ── 021240 — K-IFRS 1115 계약잔액 주석이 `기말` 에 문맥을 빌려 줬다 ────────
# `--ticker 021240` VM 출력 그대로(26.2Q). 수취채권·계약자산·계약부채 표라 잔고가 아니다.
_021240 = ("기업회계기준서 제1116호에 해당하는 리스와 관련된 수익입니다. 고객과의 계약에서 생기는 "
           "계약잔액 및 변동에 대한 공시 당반기말 (단위 : 천원) 공시금액 수취채권 179,464,001 "
           "계약자산 11,796,539 유동 계약부채 224,380,883 비유동 계약부채 65,061,687 환불부채 "
           "216,003,691 계약부채에 대한 설명 초과청구공사, 선수금, 선수수익 등이 포함되어 있으며")


@pytest.mark.parametrize("gap", [" ", ""])
def test_contract_balance_note_title_is_not_order_context(gap):
    """DART 평문은 띄어쓰기가 붙기도 한다 — 두 꼴 다 문맥이 아니다(#91c)."""
    from bot.dart_backlog import _balance_matches
    t = _021240.replace("생기는 계약잔액", "생기는" + gap + "계약잔액")
    assert _balance_matches(t) == []
    assert diagnose(t) == "미공시"


def test_contract_balance_word_still_lends_context_elsewhere():
    """반대 증거(#25) — 도급표의 `계약잔액` 은 여전히 문맥이다."""
    from bot.dart_backlog import _balance_matches
    t = "구 분 계약금액 계약잔액 (단위 : 백만원) 구분 기초 증가 기말 A 100 50 120"
    assert [m.group(0) for m in _balance_matches(t)] == ["기말"]


# ── 021240 25.4Q — 건설계약 주석의 한 줄 변동표만 있다(사용자 결정: 받지 않는다) ──
# `--ticker 021240` VM 출력 그대로(사업보고서 2025.12).
_021240_NOTE = ("매출원가 및 판매비와관리비 합계 3,514,767,833 33. 건설계약 (연결) 수주 계약 변동 "
                "내역에 대한 공시 당기 (단위 : 천원) 공사계약 당기초 158,008,926 증감액, 수주 계약 "
                "173,968,354 공사수익인식, 수주계약 82,020,483 당기말 249,956,797 전기 (단위 : 천원) "
                "공사계약 당기초 139,981,661 증감액, 수주 계약 85,158,289 공사수익인식, 수주계약 "
                "67,131,024 당기말 158,008,926 누적공사수익과 비용 등의 세부 내역에 대한 공시")


def test_note_only_construction_rollforward_is_partial_disclosure():
    from bot.dart_backlog import (MISS_NOTE_PARTIAL, SOURCE_HAS_NO_VALUE,
                                  parse_backlog)
    assert parse_backlog(_021240_NOTE) is None          # 받지 않는다
    assert diagnose(_021240_NOTE) == MISS_NOTE_PARTIAL
    assert MISS_NOTE_PARTIAL in SOURCE_HAS_NO_VALUE     # 고칠 목록에 안 오른다


def test_body_spot_without_total_stays_fixable():
    """반대 증거(#385) — 본문 수주상황 표가 합계 없이 있으면 진짜 갭일 수 있다."""
    t = ("라. 수주상황 (단위 : 백만원) 구분 기초 수주 공사수익인식 기말 A 100 50 30 120 "
         "마. 판매경로 " + _021240_NOTE)
    assert diagnose(t) == "합계없음"


# ── 독립 리뷰 반영(2026-11-13): 부분공시 판정의 반대 증거 ────────────────
@pytest.mark.parametrize("body", [
    # 관문을 못 지난 본문 표(H1 ①)
    "나. 수주상황 (단위 : 백만원) 구분 프로젝트 발주처 수주잔고 A 1,000 B 2,000 다. 판매경로 ",
    # 단위를 못 읽는 본문 표(H1 ②)
    "나. 수주상황 (단위 : 대) 구분 수주총액 기납품액 수주잔고 A 10 4 6 다. 판매경로 ",
])
def test_body_spot_after_or_before_note_keeps_it_fixable(body):
    from bot.dart_backlog import MISS_NOTE_PARTIAL
    assert diagnose(body + _021240_NOTE) != MISS_NOTE_PARTIAL


def test_note_with_backlog_label_is_a_parser_gap_not_partial():
    """`수주잔고, 당기말` 은 `_parse_xbrl` 의 갭이다(H2) — 고칠 목록에 남는다."""
    from bot.dart_backlog import MISS_NOTE_PARTIAL
    t = ("33. 건설계약 수주계약 변동 내역에 대한 공시 (단위 : 백만원) 공시금액 수주잔고, 기초 "
         "2,377 증가(감소), 수주잔고 588 공사수익 (788) 수주잔고, 당기말 2,177 누적공사수익")
    assert diagnose(t) != MISS_NOTE_PARTIAL


def test_a_later_numbered_note_is_not_under_the_heading():
    """주석 머리 뒤 `34.` 의 별개 표는 그 머리 아래가 아니다(M1)."""
    from bot.dart_backlog import MISS_NOTE_PARTIAL
    t = _021240_NOTE + (" 34. 수주 현황 (단위 : 천원) 구분 기초 공사수익인식 "
                        "기말 A현장 3,000 1,000 2,000")
    from bot.dart_backlog import _balance_matches
    assert len(_balance_matches(t)) == 3, "34. 표의 `기말` 이 잔고 자리여야 이 갈래를 잰다"
    assert diagnose(t) != MISS_NOTE_PARTIAL


def test_a_spot_beyond_the_window_is_not_under_the_heading():
    """창(600자) 밖의 자리는 주석 머리 아래로 치지 않는다(M2)."""
    from bot.dart_backlog import MISS_NOTE_PARTIAL, _NOTE_HEAD_WINDOW
    pad = " 설명 문장이 이어집니다" * (_NOTE_HEAD_WINDOW // 10)
    t = _021240_NOTE.replace("당기 (단위 : 천원)", pad + " 당기 (단위 : 천원)", 1)
    assert diagnose(t) != MISS_NOTE_PARTIAL
