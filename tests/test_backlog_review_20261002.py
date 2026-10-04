"""수주잔고 격주 리뷰(2026-10-02 보고서) — 갈래 다섯을 원문 발췌로 재현한다.

보고서는 `형식미지원 25건 · 종목 14개` 를 다섯 갈래로 나눠 갈래마다 원문 발췌
1건을 실었다(#387). 발췌를 **지금 배포된 파서**에 그대로 태워 갈래마다 원인을
갈랐다 — 히스토그램만 보고 고치면 이미 고친 것을 또 고친다(#92).

| 갈래(건수) | 표본 | 원인 | 처방 |
|---|---|---|---|
| 헤더에 기초·수주총액 열 없음(9) | 391710 | **이미 고쳐진 관측** — 09-19 03:42 배포(#1285)의 4열 롤링 파서가 지금은 4,015백만원으로 읽는다. 성공해도 원장의 미스 줄을 지우는 경로가 없었다 | 성공하면 그 분기 개선 여지 줄을 지운다 · 줄에 파서 지문 · 보고서 직전 재조회 |
| 헤더에 기납품·매출 열 없음(7) | 078340 | 투자부동산 표의 `반기말금액` 속 `기말` 이 **다음 절 제목**(`4. 매출 및 수주상황`)의 `수주` 를 문맥으로 빌렸다 | 범용 라벨의 뒤쪽 문맥은 표 끝에서 자른다 |
| 합계행 3값(검산실패)(4) | 000670 | 캡션 `(단위 : M/T, 천㎡, 천개 / 백만원)` 의 `천㎡` 를 **금액 단위 천**으로 읽는다(1,000배) · 09-18 실측은 항등식은 맞는데 잔고가 **음수**(245,858 − 264,255 = −18,396 — 합계행인지 품목 행인지 기록이 말하지 않는다) | 단위 기호 차단 + 같은 캡션의 `원` 단위 우선 · `잔고0이하` 갈래 |
| 합계행 0값(검산실패)(3) | 091340 | 빈 표(전부 `-`) 판정이 **표 끝 너머**(다음 절)를 읽는다 · 실측 캡션은 `(단위 : )` | 빈 표 판정을 잘린 표로 · 단위보다 먼저 |
| 합계행 없음(2) | 195870 | "수주잔고에 대한 공개는 … **영업기밀** … 기재를 생략" — 미공시 선언을 모른다 | 분류 어구 추가(같은 문장 안) |

그리고 같이 드러난 것 — **외화 표가 앞 표의 원화 캡션을 빌린다**: `(단위 : 천USD)`
표가 `(단위 : 백만원)` 표 뒤에 오면 `_unit_mult` 가 금액이 아닌 가까운 캡션을
건너뛰고 먼 원화 캡션을 써 **틀린 값을 조용히 냈다**(재현: 6,000천USD → 60억원).
모듈 독스트링은 "매칭되지 않으면 None 을 돌려 그 표를 통째로 버린다" 라고
적고 있었다 — 먼 원화 캡션이 창 안에 있으면 거짓이다(#55).

⚠️ 픽스처 규약(#155): 발췌는 보고서 원문 그대로다. 발췌 **밖**이 판정을 가르는
곳(4,000자 앞 캡션, 표 뒤 다음 절)은 최소 구조를 덧붙였고 그 사실을 각 테스트에
적었다 — 덧붙인 부분은 측정이 아니다(#165). 영풍의 음수 잔고 숫자는 09-18 실측
(docs/tests.md)을 합계행 자리에 놓은 것이고, 개별 품목 행은 합성이다.
"""
from __future__ import annotations

import json

import pytest

# ── 보고서 원문 발췌(2026-10-02 격주 리뷰 · 앞 200자) ─────────────────────
EX_391710 = (
    "(단위 : 백만원) 주요거래처 제6기(당반기) 매출액 비중 A사 2,492 18.4% "
    "B사 830 6.1% C사 780 5.8% D사 753 5.6% E사 699 5.2% 라. 수주상황 "
    "(단위 : 백만원 ) 구분 전기말 신규계약 매출인식 당반기말 "
    "스마트팩토리로봇물류 7,058 1,718 4,761 4,015 ※ 당사는 스마트팩토리 및 "
    "로봇물류 사업부 이외에")
EX_078340 = (
    "해 사용된 자본화차입이자율은 3.30%입니다. (2) 투자부동산 1) 당반기 중 "
    "투자부동산의 변동내역은 다음과 같습니다. (단위: 백만원) 구 분(*) 당반기 "
    "기초금액 36,628 취득액 126 대체 -2,466 반기말금액 34,287 주1) 전반기 중 "
    "투자부동산의 변동내역은 없습니다. 다. 설비의 신설ㆍ매입 계획 등 "
    "해당사항이 없습니다. 4. 매출 및 수")
EX_000670 = (
    "별 발주물량을 효율적으로 관리하여 적시에 생산함으로써, 고객의 생산량 "
    "변동에 최대한 빠르게 대응하고 있습니다. 2. 수주상황 (단위 : M/T, 천㎡, "
    "천개 / 백만원) 회사명 품목 수주일자 납기 수주총액 기납품액 수주잔고 "
    "수량 금액 수량 금액 수량 금액 ㈜영풍 아연괴 2026.01.01~2026.12.31 "
    "2026.01.01~2026.12.31 55,22")
EX_091340 = (
    "13,998 내수 11,587 16,071 16,072 합계 138,863 310,238 330,070 주) "
    "연결재무제표 기준으로 작성되었습니다. 나. 수주 실적 (단위 : ) 품목 "
    "수주일자 납기 수주총액 기납품액 수주잔고 수량 금액 수량 금액 수량 금액 "
    "- - - - - - - - - - - - - - - - - - 합 계 - - - - - - 5. 위험")
EX_195870 = (
    "특성상 주요 제품 및 서비스에 대한 고객 주문을 1~2개월 내에 납품하는 "
    "형식으로 수주하고 있으며, 개별 제품의 공급물량 및가격은 고객들과의 수시 "
    "또는 월/분기별 협의를 통해 결정됩니다.다만 수주총액, 기납품액 및 "
    "수주잔고에 대한 공개는 당사 및 당사 종속회사의 영업기밀에 해당되어 "
    "영업에 현저한 손실을 초래할 수 있기에 구체적 기재를 생략합니다.또한 공시서")

# 발췌 **앞**: 「4. 매출 및 수주상황」은 매출 표(원화 캡션)로 시작한다 — 발췌가
# 표 중간에서 시작하는 091340·195870 의 원문에서 `_unit_mult` 가 빌려 간 그
# 캡션이다(관측된 갈래 `헤더 통과 …` 는 금액 단위를 찾았을 때만 나온다).
# 덧붙인 구조이지 측정이 아니다.
KRW_SALES_HEAD = ("4. 매출 및 수주상황 가. 매출실적 (단위 : 백만원) 구 분 "
                  "제29기 반기 제28기 수출 ")


def _ledger(tmp_path, monkeypatch):
    from bot import dart_backlog as bl
    log = tmp_path / "misses.jsonl"
    monkeypatch.setattr(bl, "_MISS_LOG", log)
    return bl, log


def _rows(log):
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()
            if x.strip()]


class _Dart:
    """원천만 스텁한다 — 문서 본문은 `bot.dart_feed._fetch_doc_text` 가 준다."""
    api_key = "k"

    def find_periodic_reports(self, ticker, *a, **k):
        return [{"rcept_no": str(ticker).split(".")[0]}]


def _docs(monkeypatch, texts, calls=None):
    import sys
    import types

    def _fetch(rn, key, max_bytes=0):
        if calls is not None:
            calls.append(rn)
        got = texts.get(rn, "")
        if isinstance(got, Exception):
            raise got
        return got

    # `source_has_no_document` — 원천이 '파일 없음' 이라 **답했나**(2026-10-04
    # 부터 수주잔고가 묻는다: 못 받은 원문이 있었으면 짧게만 굽는다). 이 가짜는
    # 원천 답을 흉내 내지 않으므로 늘 '아니오'. `no_document_code` 도 같은 답의
    # 코드 칸이다(그 코드 또는 None — 실수 #429 리뷰 F6 부터 `backlog_misses
    # --ticker` 가 후보마다 '원천 미제공(014)' / '못 받음' 을 가르려고 묻는다).
    # 진짜 모듈에 있는 이름을 가짜가 빠뜨리면 import 에서 죽는다(#155 — 가짜는
    # 원천이 실제로 내는 모양대로).
    monkeypatch.setitem(sys.modules, "bot.dart_feed", types.SimpleNamespace(
        _DOC_TEXT_MAX_FULL=2, _fetch_doc_text=_fetch,
        source_has_no_document=lambda rn: False,
        no_document_code=lambda rn: None))


# ── 391710 — 이미 고쳐진 관측이 보고서에 남는다 ─────────────────────────
class TestStaleObservations:
    """보고서 갈래 ① 의 표본은 **지금 파서가 읽는다**(4,015백만원). 09-19
    03:42 배포(#1285)가 이 형태를 받았는데 원장 줄은 그대로였다 — 값이
    나와도 그 분기의 미스 줄을 지우는 경로가 `--refill`(발췌 없는 줄만)
    밖에 없었다. 같은 일이 생기면 사용자가 붙여 넣은 보고서가 **이미 고친
    것**을 고치라고 한다(#92·#18 구워진 관측)."""

    def test_the_sample_parses_with_the_current_parser(self):
        from bot.dart_backlog import parse_backlog
        got = parse_backlog(EX_391710)
        assert got and got["value"] == 4015e6, got

    def test_a_success_clears_the_stale_miss_row(self, tmp_path, monkeypatch):
        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("391710.KQ", 2026, "11012", "형식미지원",
                     "헤더에 기초·수주총액 열 없음", EX_391710)
        _docs(monkeypatch, {"391710": EX_391710})
        val, why = bl.backlog_probe(_Dart(), "391710.KQ", 2026, "11012")
        assert val == 4015e6 and why == "정상", (val, why)
        assert _rows(log) == [], "값이 나왔는데 개선 여지 줄이 남았다"

    def test_the_uncached_probe_path_also_clears_it(self, tmp_path,
                                                     monkeypatch):
        """`out=` 을 넘기는 프로브(CLI `--ticker`·재조회)는 캐시를 안 탄다 —
        그 경로의 성공도 해소 줄을 지워야 한다(리뷰 생존 뮤테이션 P4)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("391710", 2026, "11012", "형식미지원",
                     "헤더에 기초·수주총액 열 없음", EX_391710)
        _docs(monkeypatch, {"391710": EX_391710})
        val, _w = bl.backlog_probe(_Dart(), "391710", 2026, "11012", out={})
        assert val == 4015e6 and _rows(log) == [], (val, _rows(log))

    def test_a_success_keeps_rows_it_does_not_refute(self, tmp_path,
                                                     monkeypatch):
        """반대 증거(#25) — 다른 분기·다른 종목, 그리고 원문 없이 기록되는
        `시계열이상`(파싱 성공이 그 신호를 반증하지 않는다)은 남는다."""
        bl, log = _ledger(tmp_path, monkeypatch)
        # 파싱 캐시는 테스트마다 새 디렉터리다(tests/conftest.py) — 분기를
        # 위 테스트와 다르게 둔 건 캐시 때문이 아니라, 지울 분기(2026/11014)와
        # 남길 줄(다른 분기·종목)을 가르기 위해서다.
        bl._log_miss("391710.KQ", 2025, "11011", "형식미지원", "다른 분기")
        bl._log_miss("005930.KS", 2026, "11014", "형식미지원", "다른 종목")
        bl._log_miss("391710.KQ", 2026, "11014", bl.MISS_SERIES_ANOMALY)
        _docs(monkeypatch, {"391710": EX_391710})
        assert bl.backlog_probe(_Dart(), "391710", 2026, "11014")[0] == 4015e6
        left = {(r["ticker"], r["year"], r["reason"]) for r in _rows(log)}
        assert left == {("391710", 2025, "형식미지원"),
                        ("005930", 2026, "형식미지원"),
                        ("391710", 2026, bl.MISS_SERIES_ANOMALY)}, left

    def test_miss_rows_carry_the_parser_fingerprint(self, tmp_path,
                                                    monkeypatch):
        """어느 파서가 본 관측인지 줄이 말해야 보고서가 가른다(#364·#21 진단은
        어느 코드에서 나왔는지 말할 것)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("000670.KS", 2026, "11013", "형식미지원", "x", "원문")
        assert _rows(log)[0]["ps"] == bl._parse_sig()

    def test_report_says_which_rows_an_older_parser_saw(self, tmp_path,
                                                        monkeypatch):
        """지문이 다르거나 없는 줄 = 지금 파서가 아직 다시 보지 않은 관측.
        세어서 말하고(#43), 그 발췌에도 표시한다 — 안 그러면 읽는 쪽이 이미
        고친 표본을 근거로 파서를 고친다(#92)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        rows = [
            {"ticker": "391710", "year": 2026, "reprt": "11012",
             "reason": "형식미지원", "detail": "헤더에 기초·수주총액 열 없음",
             "dv": bl._DETAIL_VOCAB, "at": 1, "ex": "옛 발췌"},
            {"ticker": "091340", "year": 2026, "reprt": "11012",
             "reason": "형식미지원", "detail": "헤더는 통과 · 합계행 없음",
             "dv": bl._DETAIL_VOCAB, "at": 1, "ex": "새 발췌",
             "ps": bl._parse_sig()}]
        log.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                 for r in rows) + "\n", encoding="utf-8")
        out = bl.review_text()
        assert "막힌 조회 2건" in out, out
        assert "1건은 지금 파서가 아직 다시 보지 않은 관측" in out, out
        head = next(ln for ln in out.splitlines() if "391710" in ln
                    and ln.startswith("· ["))
        assert "옛 파서" in head, head
        head2 = next(ln for ln in out.splitlines() if "091340" in ln
                     and ln.startswith("· ["))
        assert "옛 파서" not in head2, head2

    def test_series_anomaly_rows_are_not_called_stale(self, tmp_path,
                                                      monkeypatch):
        """`시계열이상` 은 원문 없이 기록되고(`quarterly_infographic`) 재조회
        대상이 아니다. 그 줄을 '옛 파서 관측' 으로 세면 경고가 "`--refill` 이
        갱신" 이라는 **지킬 수 없는 처방**을 단다(#380·#55) — 배포 직후엔
        지문 없는 옛 줄이 전부 그렇게 세어진다. 반대 증거: 같은 원장의 지문
        없는 파싱 미스는 여전히 센다(#25)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        anomaly = {"ticker": "047810", "year": 2026, "reprt": "11012",
                   "reason": bl.MISS_SERIES_ANOMALY, "dv": bl._DETAIL_VOCAB,
                   "at": 1}
        # 전제: 재조회가 이 줄을 안 건드린다 — 그래서 처방이 거짓이 된다.
        assert bl.refill_targets([anomaly]) == []
        log.write_text(json.dumps(anomaly, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        out = bl.review_text()
        assert "막힌 조회 1건" in out, out
        assert "아직 다시 보지 않은 관측" not in out, out
        gap = {"ticker": "391710", "year": 2026, "reprt": "11012",
               "reason": "형식미지원", "detail": "헤더에 기초·수주총액 열 없음",
               "dv": bl._DETAIL_VOCAB, "at": 1, "ex": "옛 발췌"}
        log.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                 for r in (anomaly, gap)) + "\n",
                       encoding="utf-8")
        out = bl.review_text()
        assert "1건은 지금 파서가 아직 다시 보지 않은 관측" in out, out

    def test_refill_targets_include_rows_an_older_parser_saw(self):
        """발췌가 있어도 옛 파서의 관측이면 다시 본다 — 안 그러면 391710 처럼
        이미 고쳐진 줄이 영원히 남는다. 지금 파서가 본 발췌 있는 줄은 안
        본다(#61 바깥 원천을 두드리는 비용)."""
        from bot import dart_backlog as bl
        sig = bl._parse_sig()
        rows = [{"ticker": "391710", "year": 2026, "reprt": "11012",
                 "reason": "형식미지원", "ex": "옛 파서가 남긴 발췌"},
                {"ticker": "091340", "year": 2026, "reprt": "11012",
                 "reason": "형식미지원", "ex": "지금 파서", "ps": sig},
                {"ticker": "000670", "year": 2026, "reprt": "11013",
                 "reason": "형식미지원", "ex": "다른 판", "ps": "0000000000"}]
        assert [r["ticker"] for r in bl.refill_targets(rows)] == [
            "391710", "000670"]


# ── 078340 — 다음 절 제목이 표의 문맥이 됐다 ──────────────────────────
class TestGenericLabelStopsAtTheTableEnd:
    """`기말` 은 원문 어디에나 있어 **수주 문맥**을 요구한다(#109). 그런데
    뒤쪽 120자 창이 표 끝을 넘어 다음 절 제목 `4. 매출 및 수주상황` 까지
    읽었다 — 모든 정기보고서에 있는 제목이라 그 앞 표의 `기말` 은 전부
    수주 문맥을 얻는다."""

    # 발췌는 200자에서 `4. 매출 및 수` 로 잘렸다 — 원문은 정기보고서 표준 목차
    # `4. 매출 및 수주상황` 이다(덧붙인 부분, 측정 아님). 잘린 그대로 재면
    # `수주` 가 안 보여 **옛 판도 통과한다** — 그 픽스처로는 이 결함을 재현조차
    # 못 한다(#155·#91c, 처음 쓴 판이 실제로 그랬다).
    FULL = EX_078340 + "주상황 가. 매출실적 (단위 : 백만원) 구 분 매출액"

    def test_next_section_heading_is_not_order_context(self):
        from bot.dart_backlog import _balance_matches, diagnose
        assert _balance_matches(self.FULL) == [], [
            m.group(0) for m in _balance_matches(self.FULL)]
        assert diagnose(self.FULL) == "미공시", diagnose(self.FULL)

    def test_order_context_inside_the_table_still_counts(self):
        """반대 증거(#25·#57) — 표 **안**에서 뒤에 오는 수주 문맥은 그대로
        인정하고, 앞쪽 소제목 문맥(391710 `라. 수주상황`)도 그대로다."""
        from bot.dart_backlog import _balance_matches
        inside = ("(단위 : 백만원) 구분 기초 증가 감소 기말 도급공사 "
                  "A 100 50 30 120")
        assert [m.group(0) for m in _balance_matches(inside)] == ["기말"]
        assert len(_balance_matches(EX_391710)) == 2


# ── 091340 — 빈 표가 표 끝 너머를 읽었다 ──────────────────────────────
class TestEmptyTableReadsOnlyTheTable:
    """09-18 은 빈 표(전부 `-`)를 `명시적미공시` 로 돌렸는데 그 판정은
    합계 라벨 뒤 120자를 **표 끝을 안 자른 채** 읽었다 — 관문 진단
    (`_gate_stage`)은 같은 표를 잘라서 본다. 한 표를 두 눈이 다르게 보면
    '합계행 0값(검산실패)' 과 '빈 표가 아님' 이 동시에 참이 된다(#38).

    ⚠️ 발췌(`… - - 5. 위험` 에서 끝남)만 넣으면 **옛 판도 빈 표로 본다**
    (실측 — `5.` 는 값으로 안 읽힌다). 관측된 갈래(`헤더 통과 · 합계행
    0값(검산실패)`)는 다음 절의 번호 항목을 덧붙여야 글자 그대로 나온다.
    091340 의 실제 꼬리는 발췌 밖이라 재지 못했다 — 재현은 가설의 지지이지
    증명이 아니다(#165)."""

    # 덧붙인 구조(측정 아님): 앞쪽 매출 표 캡션 + 표 뒤 다음 절의 번호 항목.
    FULL = (KRW_SALES_HEAD + EX_091340
            + "관리 및 파생거래 가. 시장위험과 위험관리 (1) 환율변동위험 "
              "당사는 외화 자산과 부채를 보유하고 있습니다.")

    def test_a_number_in_the_next_section_does_not_fill_the_table(self):
        from bot.dart_backlog import _empty_backlog_table
        at = self.FULL.index("수주잔고")
        assert _empty_backlog_table(self.FULL, at) is True

    def test_the_real_excerpt_is_not_a_parser_gap(self):
        from bot.dart_backlog import diagnose, diagnose_detail
        assert diagnose(self.FULL) == "명시적미공시", diagnose(self.FULL)
        assert diagnose_detail(self.FULL) == ""

    def test_an_empty_table_is_empty_whatever_its_caption_says(self):
        """실측 캡션은 `(단위 : )` 다. 옛 판은 단위를 **먼저** 요구해, 남의
        표 캡션을 빌리면 빈 표로, 못 빌리면 `단위없음`(= 개선 여지)으로
        갔다 — 같은 빈 표가 캡션에 따라 두 갈래였다."""
        from bot.dart_backlog import diagnose
        assert diagnose(EX_091340) == "명시적미공시", diagnose(EX_091340)

    def test_a_filled_table_is_still_a_parser_gap(self):
        """반대 증거(#25·#146) — 값이 있는 표를 미공시로 내리면 진짜 개선
        여지가 원장에서 사라진다(`_log_miss` 는 미공시류를 안 남긴다).

        캡션이 `(단위 : )` 라 금액 단위가 **선언되지 않은** 표다 — 앞 표의
        원화 캡션을 빌리지 않으므로(아래 `TestCaptionIsNotBorrowedAcrossTables`)
        갈래는 `단위없음` 이고, 그건 여전히 고칠 것이다."""
        from bot.dart_backlog import NON_FIXABLE_REASONS, diagnose
        filled = self.FULL.replace("합 계 - - - - - -",
                                   "합 계 - 1,000 - 400 - 700")
        assert diagnose(filled) not in NON_FIXABLE_REASONS, diagnose(filled)
        assert diagnose(filled) == "단위없음", diagnose(filled)
        # 금액 캡션이 붙어 있으면 관문까지 간다.
        won = filled.replace("(단위 : )", "(단위 : 백만원)")
        assert diagnose(won) == "형식미지원", diagnose(won)


class TestTheTotalRowIsLookedUpInTheTable:
    """`합계없음` 판정만 **안 잘린** 원문을 봐서, 다음 절의 `합계` 가 사유를
    `형식미지원` 으로 만들고 상세는 `합계행 없음` 이라 둘이 갈렸다(리뷰 L3,
    #38 — 파서·관문 진단·빈 표 판정은 이미 잘린 표를 본다). 합성."""

    def test_reason_and_detail_agree(self):
        from bot.dart_backlog import diagnose, diagnose_detail
        t = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
             "A 1,000 400 650 5. 위험관리 가. 시장위험 (단위 : 백만원) 구분 금액 "
             "합 계 9,999")
        assert diagnose(t) == "합계없음", diagnose(t)
        assert diagnose_detail(t) == "헤더는 통과 · 합계행 없음"

    def test_a_total_outside_the_gated_table_does_not_split_them(self):
        """관문 밖 산문의 `합계` — 위 판은 잘린 표만 보게 했지만 관문을 안 지난
        자리(`당사의 수주잔고 현황 합 계 1,234`)까지 세어, 사유는 `형식미지원` ·
        상세는 `합계행 없음` 으로 또 갈렸다. 사유도 상세(`_gate_stage`)처럼
        **관문을 지난 자리**로 본다(배포 전 2차 독립 리뷰 L3 재현, 합성)."""
        from bot.dart_backlog import diagnose, diagnose_detail
        pad = "가나다라마바사 " * 40
        t = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
             "A 1,000 400 650 ※ 끝 " + pad + "당사의 수주잔고 현황 합 계 1,234")
        assert diagnose(t) == "합계없음", diagnose(t)
        assert diagnose_detail(t) == "헤더는 통과 · 합계행 없음"


class TestFingerprintIsTakenAtImport:
    """처음 부를 때 재면 배포가 파일을 바꾼 뒤 재시작에 실패한 프로세스(옛
    코드)가 **새 지문**을 찍는다 — 옛 파서의 관측이 '지금 파서' 로 둔갑한다
    (리뷰 L1 · #365). 레포 파일은 건드리지 않는다 — 사본을 import 한 뒤 그
    사본을 바꾼다(#365 테스트가 추적 파일에 쓰면 mtime 이 거짓 drift 를 만든다)."""

    def test_a_file_changed_after_import_does_not_change_it(self, tmp_path):
        import hashlib
        import importlib.util
        from pathlib import Path

        src = Path("bot/dart_backlog.py").read_bytes()
        f = tmp_path / "bl_copy_1002.py"
        f.write_bytes(src)
        spec = importlib.util.spec_from_file_location("bl_copy_1002", f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        f.write_bytes(src + b"\n# deployed later\n")
        assert m._parse_sig() == hashlib.sha1(src).hexdigest()[:10]


class TestOneEmptyTableDoesNotHideAnother:
    """빈 표 판정은 **모든** 자리가 빈 표일 때만 미공시다 — 본사 표가 비고
    종속 표가 못 읽히면 그건 파서 갭이다(#385, 리뷰 생존 뮤테이션 D1, 합성)."""

    def test_an_empty_and_a_filled_table(self):
        from bot.dart_backlog import diagnose
        t = ("가. 수주상황(본사) (단위 : 백만원) 품목 수주일자 납기 수주총액 "
             "기납품액 수주잔고 수량 금액 수량 금액 수량 금액 - - - - - - "
             "합 계 - - - - - - 나. 수주상황(종속) (단위 : 백만원) 품목 "
             "수주총액 기납품액 수주잔고 B 1,000 400 650 합 계 1,000 400 650 "
             "※ 끝")
        assert diagnose(t) == "형식미지원", diagnose(t)

    def test_a_total_label_with_neither_values_nor_dashes_is_not_empty(self):
        """`-` 가 없으면 빈 표가 아니라 **값을 못 찾은** 것이다(생존 T2)."""
        from bot.dart_backlog import diagnose
        t = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
             "A 1,000 400 650 합 계 ※ 금액은 부가세 포함")
        assert diagnose(t) == "형식미지원", diagnose(t)


# ── 195870 — 영업기밀 생략 선언 ───────────────────────────────────────
class TestTradeSecretNonDisclosure:
    """"수주잔고에 대한 공개는 … 영업기밀에 해당되어 … 기재를 생략합니다" —
    원문이 스스로 안 쓴다고 밝힌다. 파서로 고칠 수 없으니 개선 여지가
    아니다(#93·#111). 덧붙인 앞 캡션은 관측된 갈래(`헤더는 통과 · 합계행
    없음`)를 재현하려는 것이다."""

    FULL = KRW_SALES_HEAD + EX_195870 + "류 작성기준일 현재"

    def test_it_is_classified_as_explicit_non_disclosure(self):
        from bot.dart_backlog import diagnose, diagnose_detail
        assert diagnose(self.FULL) == "명시적미공시", diagnose(self.FULL)
        assert diagnose_detail(self.FULL) == ""

    def test_the_old_fixable_row_is_cleared(self, tmp_path, monkeypatch):
        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("195870", 2026, "11012", "형식미지원",
                     "헤더는 통과 · 합계행 없음", EX_195870)
        _docs(monkeypatch, {"195870": self.FULL})
        val, why = bl.backlog_probe(_Dart(), "195870", 2026, "11012")
        assert val is None and why.startswith("명시적미공시"), why
        assert _rows(log) == []

    def test_a_trade_secret_in_another_sentence_is_not_enough(self):
        """같은 **문장** 안이어야 한다 — 다른 문장의 영업기밀 언급이 수주잔고
        표를 미공시로 내리면 진짜 파서 갭이 사라진다(#385)."""
        from bot.dart_backlog import _NO_DATA_RE
        assert not _NO_DATA_RE.search(
            "수주잔고는 아래와 같습니다. 단가 정보는 영업기밀이라 생략합니다.")

    # 독립 리뷰 H1(2026-10-02) — 표 본문엔 마침표가 없어 `[^.]` 창이 머리행의
    # `수주잔고` 부터 표 밑 **각주**까지 이어졌다. 각주가 **다른 것**(고객사명·
    # 발주처·프로젝트명)을 영업비밀이라 하면 못 읽은 표가 '미공시' 로 숨고,
    # `_log_miss` 가 그 분기의 개선 여지 줄까지 지운다(#385 숨기는 쪽이 더
    # 나쁘다). 리뷰가 재현한 세 문장이다.
    TABLE = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
             "A사 1,000 400 650 합 계 1,000 400 650 ")

    @pytest.mark.parametrize("note", [
        "※ 고객사명은 영업비밀에 해당하여 기재를 생략하였습니다",
        "※ 당사의 수주잔고는 위와 같으며, 개별 계약의 발주처 및 계약금액 등 "
        "세부 내역은 고객사와의 비밀유지 약정 및 영업비밀 보호를 위하여 기재를 "
        "생략합니다",
        "※ 수주잔고 상위 프로젝트는 발주처와 체결한 계약상 영업상 비밀 유지 "
        "조항에 따라 프로젝트명을 공개하지 않았습니다",
        # 공개·공시·기재 뒤에 주어·목적어 조사가 없다 — 공개 대상이 발주처다
        # (배포 전 2차 독립 리뷰 M2 재현, base 는 둘 다 `형식미지원`).
        "※ 수주잔고의 공시와 관련하여 발주처명은 영업상 비밀유지 의무에 따라 "
        "공개하지 않습니다",
        "※ 수주잔고 기재 기준: 계약금액 기준이며 발주처는 영업기밀로 생략"])
    def test_a_footnote_about_other_details_does_not_hide_the_table(self, note):
        from bot.dart_backlog import diagnose
        t = self.TABLE + note
        assert diagnose(t) == "형식미지원", diagnose(t)

    @pytest.mark.parametrize("sentence", [
        # 조사(의 · 에 대한 · 없음) · 공개/공시/기재 · 기밀/비밀 · 영업상 ·
        # 동사 셋을 한 번씩 — 갈래를 지우는 변형이 각자 걸리게(#91c).
        # ⚠️ 문장마다 **영업기밀 갈래만** 맞아야 한다 — 첫 판의 짧은 예문은
        # `수주잔고 … 기재하지 않`(40자 창) 갈래에도 걸려 `의`·`공시` 를
        # 지우는 변형이 살아남았다(2026-10-02 뮤테이션 실측, #91b).
        "수주잔고의 공시는 당사와 고객사 사이의 계약 조건상 영업비밀에 해당하여 "
        "이번 보고서에는 기재하지 않습니다.",
        "수주잔고에 대한 기재는 발주처와의 계약에 따라 영업상 기밀에 해당되어 "
        "공개하지 않습니다.",
        "수주잔고 공개는 당사의 영업 비밀에 해당하여 생략합니다.",
        # 조사 갈래 — `는` 말고 주어 `가`·목적어 `를` 도 공개 대상이 수주잔고다.
        "수주잔고 공개가 당사의 영업기밀 유출에 해당하여 생략합니다.",
        "수주잔고 기재를 고객과의 영업상 비밀 유지를 위하여 생략합니다."])
    def test_the_backlog_itself_declared_a_trade_secret(self, sentence):
        """반대 증거(#25) — 공개 대상이 **수주잔고 자신**이면 미공시다."""
        import re

        from bot.dart_backlog import _NO_DATA_RE, _TRADE_SECRET_PAT, diagnose
        assert diagnose(sentence) == "명시적미공시", sentence
        others = _NO_DATA_RE.pattern.replace("|" + _TRADE_SECRET_PAT, "")
        assert others != _NO_DATA_RE.pattern, "영업기밀 갈래가 정규식에 없다"
        assert not re.search(others, sentence), "다른 갈래가 맞는다"

    def test_a_trade_secret_far_from_the_disclosure_clause_does_not_count(self):
        """창은 유계다 — 같은 문장이라도 멀리 떨어진 영업기밀 언급은 그
        공개 어구의 사유가 아니다(195870 실측은 공개→영업기밀 16자 ·
        영업기밀→생략 37자)."""
        from bot.dart_backlog import diagnose
        far = "가" * 120
        t = (self.TABLE + "※ 수주잔고에 대한 공개 기준은 " + far
             + " 영업기밀 자료는 생략합니다")
        assert diagnose(t) == "형식미지원", diagnose(t)

    def test_a_verb_far_from_the_trade_secret_does_not_count(self):
        """뒤 창(영업기밀 → 생략)도 유계다 — 첫 창만 재면 뒤 창을 넓히는 변형이
        통과했다(리뷰 생존 T-win2-200, #91c)."""
        from bot.dart_backlog import diagnose
        far = "가" * 80
        t = (self.TABLE + "※ 수주잔고에 대한 공개는 영업기밀에 해당하여 " + far
             + " 생략합니다")
        assert diagnose(t) == "형식미지원", diagnose(t)


# ── 000670 — 혼합 단위 캡션 · 음수 잔고 ──────────────────────────────
class TestMixedUnitCaption:
    """캡션 `(단위 : M/T, 천㎡, 천개 / 백만원)` — 수량 단위 셋 뒤에 금액
    단위. 옛 정규식은 `천㎡` 의 `천` 을 **금액 단위**로 읽었다(뒤 글자 ㎡ 가
    차단 목록 밖). 지금은 검산 실패로 값이 안 나와 가려져 있지만, 표가 읽히는
    순간 1,000배 작은 값이 조용히 나간다."""

    CAP = "(단위 : M/T, 천㎡, 천개 / 백만원)"

    def test_the_money_unit_is_the_won_token(self):
        from bot.dart_backlog import _unit_mult
        t = EX_000670
        assert _unit_mult(t, t.index("수주잔고")) == 1e6

    @pytest.mark.parametrize("cap, mult", [
        ("(단위 : 백만원)", 1e6), ("(단위 : 백만)", 1e6), ("(단위 : 억, %)", 1e8),
        ("(단위 : 백만, 천주)", 1e6), ("(단위 : 천원)", 1e3),
        ("(단위 : 개, 백만원)", 1e6), ("(단위 : 천주, 백만)", 1e6),
        ("(단위 : 천, 백만원)", 1e6),
        # 뒤의 `원` 토큰이 이긴다 — 띄어 쓴 `백만 원` 도, 사이가 먼 것도.
        # 이건 **의미 규칙**이다(맨 스케일은 통화를 말하지 않는다) — 실측
        # 캡션이 요구한 게 아니므로 픽스처도 합성이다(#165).
        ("(단위 : 천, 백만 원)", 1e6), ("(단위 : 수량 천, 금액 백만원)", 1e6),
        ("(단위 : 백만, 천원)", 1e3)])
    def test_money_captions_still_read(self, cap, mult):
        """반대 증거(#25·#57) — 기호 차단이 정상 캡션을 죽이지 않는다."""
        from bot.dart_backlog import _unit_mult
        t = cap + " 구분 수주총액 기납품액 수주잔고"
        assert _unit_mult(t, t.index("수주잔고")) == mult, cap

    @pytest.mark.parametrize("cap", ["(단위 : 천㎡)", "(단위 : 백만㎥)",
                                     "(단위 : 천㎏)", "(단위 : 백만%)",
                                     "(단위 : 천‰)"])
    def test_a_scale_glued_to_a_unit_symbol_is_not_money(self, cap):
        from bot.dart_backlog import _cap_kind, _unit_mult
        t = cap + " 구분 수주총액 기납품액 수주잔고"
        assert _unit_mult(t, t.index("수주잔고")) is None, cap
        assert _cap_kind(cap) != "금액캡션", (cap, _cap_kind(cap))

    def test_a_won_token_outside_the_caption_does_not_veto_its_scale(self):
        """뒤의 `원` 을 찾는 창은 **캡션 안**이다 — 괄호를 넘어 머리행의
        `(천원 미만 절사)` 를 보면 맨 `백만` 캡션이 통째로 죽는다(합성)."""
        from bot.dart_backlog import _unit_mult
        t = ("(단위 : 백만) 구분 수주총액(천원 미만 절사) 기납품액 수주잔고")
        assert _unit_mult(t, t.index("수주잔고")) == 1e6

    # 합성 표 — 품목·수량 행과 합계 숫자 모두 지어낸 것이다(양수 잔고).
    ROWS_POS = ("㈜영풍 아연괴 2026.01.01~2026.12.31 2026.01.01~2026.12.31 "
                "55,220 120,000 30,000 65,000 25,220 55,000 "
                "합 계 - 120,000 - 65,000 - 55,000 ※ 수주총액은 연간 계약")

    def test_a_readable_table_is_read_in_millions(self):
        """합성 — 표가 읽히는 순간 단위가 맞는지. 옛 판은 55,000 × 1e3 (백만원
        대신 천원)으로 1,000배 작게 냈다."""
        from bot.dart_backlog import parse_backlog
        t = EX_000670[:EX_000670.index("㈜영풍")] + self.ROWS_POS
        got = parse_backlog(t)
        assert got and got["value"] == 55_000e6, got


class TestNonPositiveBalance:
    """09-18 실측(docs/tests.md): 영풍 표는 `수주총액 − 기납품액 = 수주잔고` 가
    **맞는데** 잔고가 음수다(245,858 − 264,255 = −18,396 — 기납품이 수주총액을
    넘었다. 그 수치가 합계행인지 품목 행인지는 기록이 말하지 않는다).
    파서는 0 이하를 일부러 안 받는다(항등식이 맞아도 '잔고' 로 실을 값이
    아니다). 그건 파서 갭이 아니라 **원천 값**이다 — 개선 여지로 세면 다음
    라운드가 고칠 수 없는 것을 고치러 간다(#93·#260).

    ⚠️ 4건 전부가 음수인지는 재지 않았다 — 판정을 진단에 심어 줄마다
    스스로 말하게 한다(#165·#82)."""

    @staticmethod
    def _yp(total: str) -> str:
        head = EX_000670[:EX_000670.index("㈜영풍")]
        return (head + "㈜영풍 아연괴 2026.01.01~2026.12.31 "
                "2026.01.01~2026.12.31 55,220 120,000 60,000 130,000 "
                "-4,780 -10,000 " + total + " ※ 기납품액은 실제 판매가 기준")

    def test_a_trailing_zero_column_does_not_fake_a_zero_identity(self):
        """`1,000 (400) 600 0` — 기납품을 괄호로 적은 3열 표 + 비고 `0`. 4열
        항등식이 1,000 + (−400) − 600 = 0 으로 **우연히** 맞아, 파서가 부분열
        탐색을 멈추고 진단이 `잔고0이하`(원장에 안 남음)로 숨겼다 — base 는
        600 을 읽었다(배포 전 2차 독립 리뷰 L1 재현). 더하는 열(총액·기초·신규)이
        음수면 그 항등식은 표가 말하는 게 아니다."""
        from bot.dart_backlog import parse_backlog
        t = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 비고 "
             "A 1,000 (400) 600 0 합 계 1,000 (400) 600 0 ※ 끝")
        got = parse_backlog(t)
        assert got and got["value"] == 600e6, got

    def test_a_plausible_zero_identity_still_stops_the_search(self):
        """반대 증거(#25) — `1,000 0 1,000 0`(기초 1,000 + 신규 0 − 납품 1,000
        = 0)은 표가 말하는 항등식이 0 으로 맞은 것이다. 부분열 `[1,000, 0,
        1,000]` 이 잔고 1,000 을 지어내지 않는다(L8). 3열도 더하는 열(총액)이
        음수면 진단 판정에서 빠진다."""
        from bot.dart_backlog import _verify, _verify_exact
        assert _verify([1000.0, 0.0, 1000.0, 0.0]) is None
        assert _verify([1000.0, 0.0, 1000.0, 0.0], positive=False) == 0.0
        assert _verify_exact([-100.0, 50.0, -150.0], positive=False) is None
        assert _verify_exact([100.0, 250.0, -150.0], positive=False) == -150.0

    @pytest.mark.parametrize("total", [
        "합 계 - 245,858 - 264,255 - -18,396",
        "합 계 - 245,858 - 264,255 - (18,396)"])
    def test_identity_holds_but_the_balance_is_negative(self, total):
        from bot.dart_backlog import (MISS_NON_POSITIVE, diagnose,
                                      diagnose_detail, parse_backlog)
        t = self._yp(total)
        assert parse_backlog(t) is None
        assert diagnose(t) == MISS_NON_POSITIVE, diagnose(t)
        assert diagnose_detail(t) == ""

    def test_quantity_amount_pairs_in_the_total_row(self):
        """합계행에 수량까지 6값이 오는 표(티에스이 형)도 같은 판정 — 금액
        열(홀수)만 본다. 수량 단위가 하나뿐인 표는 합계에 수량을 싣는다(합성)."""
        from bot.dart_backlog import MISS_NON_POSITIVE, diagnose
        t = self._yp("합 계 55,220 245,858 60,000 264,255 -4,780 -18,396")
        assert diagnose(t) == MISS_NON_POSITIVE, diagnose(t)

    def test_a_total_that_breaks_the_identity_is_still_a_parser_gap(self):
        """반대 증거(#25) — 항등식이 안 맞는 3값은 여전히 '열 모델이 틀렸다'
        이고 고칠 것이다."""
        from bot.dart_backlog import diagnose
        t = self._yp("합 계 - 245,858 - 100,000 - -18,396")
        assert diagnose(t) == "형식미지원", diagnose(t)

    def test_it_is_not_recorded_and_clears_the_old_row(self, tmp_path,
                                                       monkeypatch):
        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("000670", 2026, "11013", "형식미지원",
                     "헤더 통과 · 합계행 3값(검산실패)", EX_000670)
        t = self._yp("합 계 - 245,858 - 264,255 - -18,396")
        _docs(monkeypatch, {"000670": t})
        val, why = bl.backlog_probe(_Dart(), "000670.KS", 2026, "11013")
        assert val is None and why == bl.MISS_NON_POSITIVE, why
        assert _rows(log) == []
        assert bl.MISS_NON_POSITIVE in bl.NON_FIXABLE_REASONS


    def test_another_unreadable_table_keeps_it_a_parser_gap(self):
        """음수 표 **옆에** 못 읽은 표가 하나라도 있으면 그건 개선 여지다 — 한
        표의 원천 값이 다른 표의 진짜 갭을 원장에서 지우면 안 된다(#385)."""
        from bot.dart_backlog import diagnose
        other = ("나. 수주상황 (단위 : 백만원) 구 분 수주총액 기납품액 수주잔고 "
                 "A 1,000 400 700 합 계 1,000 400 700 ")
        t = other + self._yp("합 계 - 245,858 - 264,255 - -18,396")
        assert diagnose(t) == "형식미지원", diagnose(t)

    def test_a_table_the_parser_would_not_enter_is_not_called_negative(self):
        """관문(시작·납품 열)을 **파서와 같게** 걷는다 — 진단만 느슨하면 파서가
        들어가지도 않는 표의 음수가 '원천 값' 으로 둔갑한다(#35·#385)."""
        from bot.dart_backlog import diagnose
        t = ("나. 수주상황 (단위 : 백만원) 구 분 주요 고객 수주잔고 "
             "A사 100 200 -100 합 계 100 200 -100 ※ 주석")
        assert diagnose(t) == "형식미지원", diagnose(t)

    @pytest.mark.parametrize("head", [
        "구 분 수주총액 비고 수주잔고",        # 납품 열이 없다(생존 N5)
        "구 분 계약금액 기납품액 수주잔고"])   # 시작 열이 없다(생존 N4)
    def test_each_half_of_the_header_gate(self, head):
        """관문 둘 중 **하나만** 빠져도 파서는 안 들어간다 — 기존 픽스처는
        둘 다 없어 한쪽을 지우는 변형이 살아남았다(#91c)."""
        from bot.dart_backlog import diagnose
        t = ("나. 수주상황 (단위 : 백만원) " + head
             + " A 100 300 -200 합 계 100 300 -200 ※ 끝")
        assert diagnose(t) == "형식미지원", diagnose(t)

    def test_a_foreign_unit_table_is_not_called_negative(self):
        """단위 관문도 같다 — 외화 표의 음수는 원화 잔고의 원천 값이 아니다
        (생존 N2, 합성)."""
        from bot.dart_backlog import diagnose
        t = ("가. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
             "A 100 300 -200 합 계 100 300 -200 ※ 끝 나. 수주상황(해외) "
             "(단위 : 천USD) 품목 수주총액 기납품액 수주잔고 B 50 80 -30 "
             "합 계 50 80 -30 ※ 끝")
        assert diagnose(t) == "형식미지원", diagnose(t)

    def test_extra_count_columns_do_not_hide_a_negative_balance(self):
        """건수·수량 열이 끼어 5값이면 파서가 쓰는 **부분열 탐색**으로 같은
        항등식을 본다(생존 V3, 합성)."""
        from bot.dart_backlog import MISS_NON_POSITIVE, diagnose
        t = self._yp("합 계 12 3 245,858 264,255 -18,396")
        assert diagnose(t) == MISS_NON_POSITIVE, diagnose(t)

    def test_a_fully_delivered_pair_row_is_not_given_a_spurious_balance(self):
        """금액쌍 항등식이 **0 으로** 성립하면 그게 답이다 — 부분열 탐색으로
        넘어가면 `[1,000, 5, 1,000]` 이 1% 안에 맞아 잔고 1,000(10억원)을
        지어냈다(리뷰 L8, base 부터 있던 결함 · 합성)."""
        from bot.dart_backlog import (MISS_NON_POSITIVE, diagnose,
                                      parse_backlog)
        t = ("나. 수주상황 (단위 : 개, 백만원) 품목 수주총액 기납품액 수주잔고 "
             "수량 금액 수량 금액 수량 금액 A 5 1,000 5 1,000 0 0 "
             "합 계 5 1,000 5 1,000 0 0 ※ 끝")
        assert parse_backlog(t) is None
        assert diagnose(t) == MISS_NON_POSITIVE, diagnose(t)

    def test_a_zero_balance_is_also_not_a_parser_gap(self):
        """다 납품해 잔고가 0 인 표도 같은 결론 — 파서는 0 이하를 안 싣는다."""
        from bot.dart_backlog import MISS_NON_POSITIVE, diagnose
        t = self._yp("합 계 - 264,255 - 264,255 - 0")
        assert diagnose(t) == MISS_NON_POSITIVE, diagnose(t)

# ── 외화 표가 원화 캡션을 빌린다 ──────────────────────────────────────
class TestCaptionIsNotBorrowedAcrossTables:
    """`_unit_mult` 는 `at` 앞 **마지막 금액 캡션**을 썼다. 그래서 표 자기
    캡션이 외화·수량·빈 캡션이면 그걸 건너뛰고 **앞 표의 원화 캡션**을
    빌렸다 — 검산은 열 사이 항등식만 보므로 스케일 오류를 못 잡는다."""

    @staticmethod
    def _doc(cap: str) -> str:
        return (KRW_SALES_HEAD + "합 계 9,999 나. 수주상황 " + cap
                + " 구 분 수주총액 기납품액 수주잔고 A 10,000 4,000 6,000 "
                  "합 계 10,000 4,000 6,000")

    @pytest.mark.parametrize("cap", ["(단위 : 천USD)", "(단위 : 백만불)",
                                     "(단위 : 척, 백만USD)", "(단위 : 천톤)",
                                     "(단위 : )"])
    def test_a_non_won_caption_is_not_read_as_won(self, cap):
        from bot.dart_backlog import parse_backlog
        assert parse_backlog(self._doc(cap)) is None, cap

    def test_the_diagnosis_names_the_foreign_caption(self):
        """빌리지 않으면 진단도 **표 자기 캡션**을 본다 — 처방이 '환율'
        이라는 사실이 보인다(#82)."""
        from bot.dart_backlog import diagnose, diagnose_detail
        t = self._doc("(단위 : 천USD)")
        assert diagnose(t) == "단위없음", diagnose(t)
        assert diagnose_detail(t).startswith("캡션 외화"), diagnose_detail(t)

    def test_its_own_won_caption_still_reads(self):
        """반대 증거 — 표 자기 캡션이 원화면 그대로 읽는다(#25)."""
        from bot.dart_backlog import parse_backlog
        got = parse_backlog(self._doc("(단위 : 백만원)"))
        assert got and got["value"] == 6000e6, got

    # ── 독립 리뷰 M1(2026-10-02) — 첫 판은 금액 캡션과 라벨 **사이의 아무
    # 콜론 캡션**이나 '자기 캡션' 으로 봐서 base 가 맞게 읽던 표를 빈칸으로
    # 만들었다. 금액 캡션과 **한 묶음**인 캡션(머리행 소캡션·연속 캡션)만
    # 건너뛴다. 묶음 밖 캡션은 이 표의 것인지 사이 표의 것인지 못 가르므로
    # 빌리지 않는다(배포 전 2차 독립 리뷰 H1 — 아래 `…intermediate…`).
    ROW = (" 수주총액 기납품액 수주잔고 A 10,000 4,000 6,000 "
           "합 계 10,000 4,000 6,000")

    @pytest.mark.parametrize("t, want", [
        # 머리행의 소캡션 — 같은 표의 수량 열 단위다(합성).
        ("나. 수주상황 (단위 : 백만원) 품목 수량(단위 : 대) 수주총액 기납품액 "
         "수주잔고 A 10 10,000 4,000 6,000 합 계 10 10,000 4,000 6,000", 6000e6),
        # 띄워 적힌 소캡션(셀 안 `<br>` 은 원문에서 공백이 된다) — 묶음 경계가
        # 없으면 같은 묶음이다. '붙은 캡션만' 으로 좁히면 이게 빈칸이 된다.
        ("나. 수주상황 (단위 : 백만원) 품목 수량 (단위 : 대) 수주총액 기납품액 "
         "수주잔고 A 10 10,000 4,000 6,000 합 계 10 10,000 4,000 6,000", 6000e6),
        # 연속 캡션 — 금액·수량 단위를 따로 적은 한 표(합성).
        ("나. 수주상황 (단위 : 백만원)(단위 : 대) 구 분" + ROW, 6000e6),
        ("나. 수주상황 (단위 : 천원) 구 분 건수(단위 : 건) 수주총액 기납품액 "
         "수주잔고 A 3 10,000 4,000 6,000 합 계 3 10,000 4,000 6,000", 6000e3),
        # 머리행 각주 표식(`(주1)`·`(1)`)은 아래 항목 머리가 아니다 — 첫 판의
        # `_SUBITEM` 은 앞 글자를 안 봐서 `수주총액(주1)` 의 `1)` 에 걸렸다
        # (2026-10-02 셀프리뷰 재현, 합성).
        ("나. 수주상황 (단위 : 백만원) 구분 수주총액(주1) 수량(단위 : 대) 기납품액 "
         "수주잔고 A 10,000 4,000 6,000 합 계 10,000 4,000 6,000", 6000e6),
        ("나. 수주상황 (단위 : 백만원) 구분 수주총액(1) 수량(단위 : 대) 기납품액 "
         "수주잔고 A 10,000 4,000 6,000 합 계 10,000 4,000 6,000", 6000e6),
    ])
    def test_captions_of_the_same_table_do_not_veto(self, t, want):
        from bot.dart_backlog import parse_backlog
        got = parse_backlog(t)
        assert got and got["value"] == want, (got, t[:60])

    @pytest.mark.parametrize("body", ["국내 60 해외 40", "국내 - 해외 -",
                                      "국내 ― 해외 ―"])
    def test_an_intermediate_tables_caption_blocks_the_borrow(self, body):
        """옛 계약을 다시 씀(#222): '사이 표의 비금액 캡션은 그 표의 것 — 자기
        캡션이 없는 수주 표는 앞 원화 캡션을 쓴다(base 규약)'.

        그 갈래는 사이 표를 **라벨 표의 자기 캡션**과 가르지 못했다. 본문 칸으로
        가른 판은 라벨이 자기 표 본문 **뒤**에 오는 주석형(`수주잔고, 기말`)에서
        자기 표 행을 사이 표로 읽어 앞 표의 원화를 빌렸고(배포 전 2차 독립 리뷰
        H1 — 아래 `…note_form…`), 절 제목으로 가르면 자기 캡션이 아래 절을 덮는
        표(`가. 국내 … 나. 해외`)와 모양이 같다. 녹화 입력 184개 중 이 빌리기에
        기대는 입력은 0건이었다(실측) — 빈칸 > 틀린 숫자. 진단은 막은 그 캡션을
        상세에 싣는다(다음 라운드가 실물로 판단하게, #82)."""
        from bot.dart_backlog import diagnose, diagnose_detail, parse_backlog
        t = ("가. 매출실적 (단위 : 백만원) 매출 합계 9,999 나. 판매경로 (단위 : %) "
             + body + " 다. 수주상황 구 분" + self.ROW)
        assert parse_backlog(t) is None, body
        assert diagnose(t) == "단위없음", diagnose(t)
        assert "(단위 : %)" in diagnose_detail(t), diagnose_detail(t)

    SALES = "가. 매출실적 (단위 : 백만원) 구분 매출액 제품 1,000 합 계 1,000 "
    NOTE = ("고객과의 계약에서 생기는 계약잔액 및 변동에 대한 공시 {cap} 공시금액 "
            "장부금액 합계 수주잔고, 기초 2,377 2,377 증가(감소), 수주잔고 588 588 "
            "공사수익 (788) (788) 수주잔고, 기말 2,177 2,177 누적공사수익")

    @pytest.mark.parametrize("cap", ["(단위 : 천USD)", "(단위 : 대)", "(단위 : )"])
    def test_a_note_form_label_after_its_own_rows_does_not_borrow(self, cap):
        """주석형(`_parse_xbrl`) — 라벨 `수주잔고, 기말` 은 **자기 표 행 뒤**에
        온다. 본문 칸으로 사이 표를 가르던 판은 자기 표 행(2,377 · 588 …)을 사이
        표로 읽고 앞 표의 백만원을 빌려 2,177천USD 를 21억 7,700만원으로 냈다
        (배포 전 2차 독립 리뷰 H1 재현 — base 도 같은 값이었다)."""
        from bot.dart_backlog import parse_backlog
        assert parse_backlog(self.SALES + self.NOTE.format(cap=cap)) is None, cap

    def test_a_note_form_with_its_own_won_caption_still_reads(self):
        """반대 증거(#25) — 주석형 자기 캡션이 원화면 그대로 읽는다."""
        from bot.dart_backlog import parse_backlog
        got = parse_backlog(self.SALES + self.NOTE.format(cap="(단위 : 백만원)"))
        assert got and got["value"] == 2177e6 and got["form"] == "주석·건설계약", got

    TAIL = (" 구 분 수주총액 기납품액 수주잔고 A 10,000 4,000 6,000 "
            "합 계 10,000 4,000 6,000")

    @pytest.mark.parametrize("domestic", [
        "구 분 수주총액 기납품액 수주잔고",     # `수주\s*잔`
        "구 분 계약금액 계약잔액",               # `계약\s*잔`
        "구 분 기초 기말잔고"])                  # `기말\s*잔`
    @pytest.mark.parametrize("mark", ["[해외]", "□ 해외", "<해외>", "○ 해외",
                                      "(해외)", "가) 해외", "② 해외"])
    def test_a_header_only_table_then_an_unknown_heading_does_not_borrow(
            self, domestic, mark):
        """머리행만 있는 빈 국내표 뒤에 모르는 제목(`[해외]`·`□`·`가)`·`②` …)과
        자기 외화 캡션 — 경계 낱말이 하나도 없어 같은 묶음으로 읽혀 앞 표의
        원화를 빌렸다(배포 전 2차 독립 리뷰 H1, 일곱 꼴 전부). 제목 꼴을 늘어놓는
        대신 **앞 표의 잔고 라벨**이 묶음을 끊는다(목록은 다음 꼴을 못 잡는다,
        #24). 라벨 꼴 셋은 각자 그것 하나뿐인 픽스처다(#91c)."""
        from bot.dart_backlog import parse_backlog
        t = ("나. 수주상황 [국내] (단위 : 백만원) " + domestic + " " + mark
             + " (단위 : 천USD)" + self.TAIL)
        assert parse_backlog(t) is None, (domestic, mark)

    @pytest.mark.parametrize("sub", ["수주잔량(단위 : 톤)", "수주잔량 (단위 : 톤)"])
    def test_a_quantity_balance_sub_caption_stays_in_the_group(self, sub):
        """잔고 라벨 경계는 **앞 표의 금액 잔고 라벨**을 보려는 것이다 — 같은 표
        머리행의 수량 열 `수주잔량(단위 : 톤)` 까지 끊으면 base·e9a7fc4 가 60억원으로
        읽던 표가 빈칸이 되고, 사유도 `합계없음`(수주잔량 자리의 잘린 표)으로 틀리게
        적힌다(배포전 셀프리뷰 재현). `잔량` 은 수량이라 경계에서 뺀다."""
        from bot.dart_backlog import parse_backlog
        t = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 " + sub + " 수주잔고 "
             "A 10 10,000 4,000 6,000 합 계 10 10,000 4,000 6,000")
        got = parse_backlog(t)
        assert got and got["value"] == 6000e6, got

    @pytest.mark.parametrize("qty,label", [
        ("수주잔량", "수주잔고"), ("계약잔량", "계약잔액"), ("기말잔량", "기말잔고"),
        ("수주잔 량", "수주잔고")])
    def test_each_quantity_balance_label_is_not_a_group_break(self, qty, label):
        """세 잔고 라벨 꼴마다 `잔량` 예외가 걸리는지 — 파서 경로가 있는 건
        `수주잔고` 뿐이라 나머지 둘은 `_unit_mult` 로 직접 잰다(#91c 각 꼴 하나씩).
        좁은 셀은 낱말을 띄어 쓴 채 온다(`수주잔 량`, #94) — 예외의 `\\s*` 를 그
        꼴이 잰다(뮤테이션 J4 가 그 픽스처 없이 살아남았다)."""
        from bot.dart_backlog import _unit_mult
        t = ("나. 수주상황 (단위 : 백만원) 구 분 수주총액 기납품액 " + qty
             + "(단위 : 대) " + label + " A 1 2 3")
        assert _unit_mult(t, t.index(label)) == 1e6, qty

    def test_a_long_mixed_caption_reads_its_won_token(self):
        """금액 토큰 앞이 20자를 넘는 혼합 캡션 — 옛 창(20)은 못 읽어 그
        캡션을 '비금액 자기 캡션' 으로 보고 빈칸을 냈다. 콜론 캡션은 40자(합성)."""
        from bot.dart_backlog import parse_backlog
        t = ("나. 수주상황 (단위 : 중량-천톤, 면적-천㎡, 수량-천개, 금액-백만원) "
             "품목 수주총액 기납품액 수주잔고 A 1,000 400 600 "
             "합 계 1,000 400 600")
        got = parse_backlog(t)
        assert got and got["value"] == 600e6, got

    @pytest.mark.parametrize("mid", [
        "품목 단위 수주수량 기납품수량 수주잔량 계약일자 단가(천원)",
        "판매 단위가 다른 품목은 금액 기준으로 합산하였으며 천원 미만은 절사 품목"])
    def test_a_unit_word_without_a_colon_does_not_reach_a_far_won(self, mid):
        """콜론 없는 `단위`(머리행의 단위 열 이름 · 산문)는 base 의 20자만 본다 —
        40자로 넓힌 판은 21~40자 뒤의 `천원` 을 집어 표 캡션을 덮었다(6,000백만원
        → 6,000천원, 배포 전 2차 독립 리뷰 M1 재현)."""
        from bot.dart_backlog import parse_backlog
        t = ("나. 수주상황 (단위 : 백만원) " + mid + " 수주총액 기납품액 수주잔고 "
             "A 10,000 4,000 6,000 합 계 10,000 4,000 6,000")
        got = parse_backlog(t)
        assert got and got["value"] == 6000e6, got

    def test_a_caption_straddling_the_window_edge_still_reads(self):
        """훑는 범위의 **여유**(창 + 200자)가 재는 것 — 캡션이 창 경계에 걸치면
        (`단위` 는 창 밖, 단위 토큰은 창 안) 창 끝에서 훑기 시작한 판은 `단위` 를
        못 보고 빈칸을 낸다(리뷰 생존 S-noslack, #91c)."""
        from bot.dart_backlog import _CAP_WINDOW, _unit_mult
        cap = "(단위 : 백만원)"
        t = cap + " " + "가" * (_CAP_WINDOW - 2) + "수주잔고"
        at = t.index("수주잔고")
        best_end = cap.index("원") + 1
        assert at - best_end == _CAP_WINDOW and at - _CAP_WINDOW > cap.index("단")
        assert _unit_mult(t, at) == 1e6

    @pytest.mark.parametrize("mid", [
        # 절 캡션이 아래 항목 표를 덮는다 — `(1) 국내` 는 표 경계가 아니다.
        "나. 수주상황 (단위 : 천USD) (1) 국내 구 분",
        # 원화 캡션 바로 뒤 아래 항목이 자기 외화 캡션을 단다.
        "나. 수주상황 (단위 : 백만원) (1) 해외 (단위 : 천USD) 구 분",
        # 앞 표가 빈 표(전부 `-`)라도 합계행이 표를 끝낸다.
        "나. 수주상황 (단위 : 백만원) 구 분 수주총액 기납품액 수주잔고 - - - "
        "합 계 - - - (단위 : 천USD) 구 분",
        # 경계가 **하나뿐**인 픽스처 — 갈래를 하나 지우는 변형이 각자 걸리게.
        # (앞 표 본문은 중립 낱말 `기타` — `없음` 을 쓰면 갈래가 둘이 된다.)
        "가. 국내 (단위 : 백만원) 기타 나. 해외 (단위 : 천USD) 구 분",
        "1. 국내 (단위 : 백만원) 기타 2. 해외 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 기타 ※ 해외분 (단위 : 천USD) 구 분",
        # 앞 표가 '없음' 뿐이고 절 제목도 없다 — 2026-10-02 셀프리뷰 재현:
        # 경계가 안 보여 같은 묶음으로 읽고 원화를 빌렸다(50억원). 문장 끝·
        # '없음/없습니다' 도 묶음을 끊는다(갈래마다 하나씩).
        "나. 수주상황 (단위 : 백만원) 해당사항 없음 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 실적이 없습니다 해외 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 실적 미발생. 해외 (단위 : 천USD) 구 분",
        # 자기 외화 캡션과 머리행 **사이**의 각주·문장·절 제목·합계 머리칸·연도·
        # 항목 번호 — 첫 판은 그걸 '사이 표' 증거로 읽어 앞 표의 원화를 빌렸다
        # (5,000천USD → 50억원, 셀프리뷰·2차 독립 리뷰 H1 재현). 묶음 밖
        # 캡션이면 무엇이 그 아래 오든 빌리지 않는다.
        "나. 수주상황 (단위 : 천USD) 외화 계약 기준입니다. 구 분",
        "나. 수주상황 (단위 : 천USD) ※ 환율 미적용 구 분",
        "나. 수주상황 (단위 : 천USD) ※ 적용환율 1,350 구 분",
        "나. 수주상황 (단위 : 천USD) ※ 환율 1,350 · 1,400 구 분",
        "나. 수주상황 (단위 : 천USD) 가. 국내 구 분",
        "나. 수주상황 (단위 : 천USD) 구 분 국내 해외 합계",
        "나. 수주상황 (단위 : 천USD) 구 분 2026 2025",
        "나. 수주상황 (단위 : 천USD) 구분 2026",
        "나. 수주상황 (단위 : 천USD) (1) 국내 (2) 해외 구 분",
        "나. 수주상황 (단위 : 천USD) 1) 국내 2) 해외 구 분",
        # 붙여 쓴 아래 항목 머리(`1)해외`·`(1)해외`) — 낱말에 붙어 본문 칸이
        # 아니므로 이 픽스처의 앞 표 경계는 `_SUBITEM` 하나뿐이다(괄호를 요구하는
        # 변형·항목 머리를 지우는 변형이 각자 걸리게, #91c).
        "나. 수주상황 (단위 : 백만원) 기타 1)해외 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 기타 (1)해외 (단위 : 천USD) 구 분",
        # 묶음 경계의 나머지 갈래 — 각 픽스처의 앞 표 본문은 그 갈래 **하나**
        # 뿐이다: `☞` · 합계 머리칸 · 본문 칸 하나(숫자 · `-` · `―` · 연도 꼴 —
        # 넓게) · 캡션 바로 앞 문장 끝(`.(단위`).
        "나. 수주상황 (단위 : 백만원) 기타 ☞ 해외분 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 구 분 금액 합 계 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 기타 10 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 국내 - (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 국내 ― (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 기준 2025 (단위 : 천USD) 구 분",
        "나. 수주상황 (단위 : 백만원) 실적 미발생.(단위 : 천USD) 구 분"])
    def test_its_own_foreign_caption_still_blocks_the_borrow(self, mid):
        """반대 증거(#25) — 6,000천USD → 60억원 재현은 그대로 막힌다."""
        from bot.dart_backlog import parse_backlog
        t = KRW_SALES_HEAD + "합 계 9,999 " + mid + self.ROW
        assert parse_backlog(t) is None, mid

    def test_a_caption_word_without_a_colon_is_not_a_caption(self):
        """`(단위당 원가)` 같은 낱말은 캡션이 아니다 — 캡션으로 보면 머리행의
        괄호 하나가 멀쩡한 원화 표를 빈칸으로 만든다(#146)."""
        from bot.dart_backlog import parse_backlog
        t = ("나. 수주상황 (단위 : 백만원) 구 분 단가(단위당 원가) 수주총액 "
             "기납품액 수주잔고 A 10,000 4,000 6,000 합 계 10,000 4,000 6,000")
        got = parse_backlog(t)
        assert got and got["value"] == 6000e6, got


class TestPlaceholderSetIsShared:
    """`―`(U+2015) 자리표시자 — 캡션 판정의 본문 칸과 행 리더가 **같은 집합**
    (`_DASHES`)을 쓴다(#38). 행 리더 쪽은 지금까지 `―` 를 재는 테스트가
    없었다 — 집합을 하나로 모으면서 그 배제를 같이 못박는다."""

    def test_row_readers_skip_the_bar(self):
        from bot.dart_backlog import _row_values, _runs
        # 숫자 **사이**의 `―` — 앞에 두면 끊어도 결과가 같아 못 잰다(#91c).
        assert _runs("A 1,000 ― 2,000 B") == [[1000.0, 2000.0]]
        assert _row_values("1,000 ― 2,000 끝", 0) == [1000.0, 2000.0]


# ── 보고서가 지금 파서의 판정을 싣는다 — 발송 전 재조회 ────────────────
class TestRefreshBeforeTheReport:
    """원장 줄은 그 종목을 누가 다시 열 때까지 옛 파서의 관측이다. 격주
    보고서는 발송 직전에 그 줄들을 **지금 파서로 다시 보고** 요약한다 —
    운영자가 `--refill` 을 기억해 돌리는 건 이 보고서가 막으려던 '기억해야
    하는 일' 이다(§Automation-first)."""

    def _stale(self, log, ticker, ex="옛 발췌", **extra):
        from bot import dart_backlog as bl
        rec = {"ticker": ticker, "year": 2026, "reprt": "11012",
               "reason": "형식미지원", "detail": "헤더에 기초·수주총액 열 없음",
               "dv": bl._DETAIL_VOCAB, "at": 1, "ex": ex, **extra}
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_a_stale_row_that_now_parses_leaves_the_report(self, tmp_path,
                                                          monkeypatch):
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "391710")                       # 지금 파서는 읽는다
        self._stale(log, "777777", ex="다른 표")          # 여전히 막힌다
        stuck = ("가나다 " * 30 + "구 분 주요 고객 수주잔고 합 계 1,234,567 "
                 + "라마바 " * 30)
        calls: list = []
        _docs(monkeypatch, {"391710": EX_391710, "777777": stuck}, calls)
        monkeypatch.setattr("bot.dart_client.get_dart", lambda *a, **k: _Dart())
        out = bl.review_with_refresh()
        assert sorted(calls) == ["391710", "777777"], calls
        assert "391710" not in out, out
        assert "777777" in out and "막힌 조회 1건" in out, out
        assert "발송 전 지금 파서로 다시 조회 2건 — 해소 1" in out, out
        # 다시 본 줄은 이제 지금 파서의 관측이다 — '옛 파서' 표시가 사라진다.
        assert "옛 파서" not in out, out
        assert "아직 다시 보지 않은 관측" not in out, out

    def test_a_same_reason_refresh_keeps_the_row_and_marks_it_current(
            self, tmp_path, monkeypatch):
        """사유가 그대로면 `_log_miss` 가 같은 신원의 줄을 **대체**했다 —
        사유와 상세를 가르는 ` · ` 를 안 떼면 `형식미지원 · 상세` ≠
        `형식미지원` 이라 방금 쓴 줄을 '반증된 옛 줄' 로 지워 보고서가
        통째로 빈다(리뷰 M2, 생존 F8)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "123456")
        stuck = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
                 "A 1,000 400 650 합 계 1,000 400 650 ※ 끝")
        _docs(monkeypatch, {"123456": stuck})
        monkeypatch.setattr("bot.dart_client.get_dart", lambda *a, **k: _Dart())
        out = bl.review_with_refresh()
        rows = _rows(log)
        assert len(rows) == 1 and rows[0]["ps"] == bl._parse_sig(), rows
        assert rows[0]["detail"] == "헤더 통과 · 합계행 3값(검산실패)", rows
        assert "막힌 조회 1건" in out and "그대로 1" in out, out

    def test_a_no_document_row_that_now_parses_is_dropped(self, tmp_path,
                                                          monkeypatch):
        """`원문미제공` 줄은 개선 여지가 아니라 `drop_fixable` 이 안 지운다 —
        재조회가 값을 내면 **그 줄 자체**를 지워야 한다(생존 F1)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "391710", ex="", reason=bl.MISS_NO_DOC)
        _docs(monkeypatch, {"391710": EX_391710})
        c = bl.refill_rows(_Dart(), bl.refill_targets(_rows(log)),
                           say=lambda m: None)
        assert c["solved"] == 1 and _rows(log) == [], (c, _rows(log))

    def test_a_probe_crash_keeps_the_old_row(self, tmp_path, monkeypatch):
        """조회가 던지면 옛 관측을 반증할 근거가 없다 — 지우지 않는다
        (독립 리뷰 B1 규율, 생존 F6)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "391710")

        def boom(*a, **k):
            raise RuntimeError("x")
        monkeypatch.setattr(bl, "backlog_probe", boom)
        c = bl.refill_rows(_Dart(), bl.refill_targets(_rows(log)),
                           say=lambda m: None)
        assert c["failed"] == 1 and len(_rows(log)) == 1, (c, _rows(log))

    def test_the_skip_reason_is_escaped(self):
        """사유는 HTML 메시지에 실린다 — `<` 하나에 보고서 전체가 안 간다
        (규칙 7, 생존 M8)."""
        from bot.dart_backlog import refresh_note
        assert refresh_note({"skipped": "a <b> & c"}).endswith(
            "a &lt;b&gt; &amp; c")

    def test_nothing_to_refresh_calls_no_source(self, tmp_path, monkeypatch):
        """다시 볼 줄이 없으면 DART 를 **아예** 안 부른다(#61) — 키 조회조차."""
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "777777", ps=bl._parse_sig())    # 지금 파서의 관측
        monkeypatch.setattr("bot.dart_client.get_dart",
                            lambda *a, **k: pytest.fail("DART 를 불렀다"))
        out = bl.review_with_refresh()
        assert "막힌 조회 1건" in out and "다시 조회" not in out, out

    def test_without_a_key_the_report_says_it_could_not_refresh(
            self, tmp_path, monkeypatch):
        """못 했으면 **못 했다고** 싣는다 — 침묵하면 '옛 파서' 줄이 왜 남았는지
        모른다(#43·#82). 그리고 보고서는 그대로 나간다.

        ⚠️ 2026-10-02 셀프리뷰로 다시 씀(#222): 첫 판은 `get_dart` 가 `None` 을
        돌려주는 스텁으로 이 갈래를 태웠는데, 운영의 `get_dart()` 는 키가 없어도
        **항상** 클라이언트를 돌려준다(`__bool__` 없음) — 그래서 그 갈래는 운영에서
        도달 불가였고, 키 없는 재조회는 전부 '원문 없음' 으로 찍혔다(#155 픽스처는
        원천이 실제로 내는 모양대로). 실물 `DartClient(api_key="")` 로 태운다."""
        from bot.dart_client import DartClient
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "391710")
        calls: list = []
        _docs(monkeypatch, {}, calls)
        monkeypatch.setattr("bot.dart_client.get_dart",
                            lambda *a, **k: DartClient(api_key=""))
        out = bl.review_with_refresh()
        assert "발송 전 재조회 못 함 — DART_API_KEY 없음" in out, out
        assert "원문 없음" not in out, out
        assert "1건은 지금 파서가 아직 다시 보지 않은 관측" in out, out
        assert calls == [], calls

    @pytest.mark.parametrize("argv", [
        ["--refill"], ["--ticker", "391710"], ["--doc", "20260319000633"],
        ["--list", "391710", "2026", "11012"], ["--explain", "391710"],
        ["--sweep", "391710"]])
    def test_every_cli_subcommand_says_a_missing_key(self, argv, monkeypatch,
                                                     capsys):
        """CLI 의 키 검사 여섯 곳도 같은 이유로 도달 불가였다 — 키 없이 돌리면
        각 서브커맨드가 원문을 못 받은 채 '원문미제공'·빈 결과를 성공처럼
        찍었다. 재조회와 **같은 술어**(`dart_ready`)로 말하고 rc=1(#38·#82)."""
        from bot.dart_client import DartClient
        from bot.scripts import backlog_misses as bm
        calls: list = []
        _docs(monkeypatch, {}, calls)
        monkeypatch.setattr("bot.dart_client.get_dart",
                            lambda *a, **k: DartClient(api_key=""))
        rc = bm.main(["backlog_misses", *argv])
        out = capsys.readouterr().out
        assert rc == 1, out
        assert "DART_API_KEY 없음" in out, out
        assert calls == [], calls

    def test_a_refresh_crash_does_not_eat_the_report(self, tmp_path,
                                                     monkeypatch):
        """재조회는 곁들이다 — 그게 던져도 보고서는 나가고 사유가 실린다
        (#315 곁들이 하나가 본체를 지운다)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        self._stale(log, "391710")

        def boom(*a, **k):
            raise RuntimeError("x")
        monkeypatch.setattr(bl, "refresh_misses", boom)
        out = bl.review_with_refresh()
        assert "막힌 조회 1건" in out, out
        assert "재조회 중 오류(RuntimeError)" in out, out

    def test_the_cap_bounds_the_downloads_and_says_what_is_left(
            self, tmp_path, monkeypatch):
        """상한은 **비용 계약**이다(미스 1건 = 정기보고서 1건). 호출 수로 재고,
        남긴 수를 말한다(#45·#313)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        for t in ("111111", "222222", "333333"):
            self._stale(log, t)
        calls: list = []
        _docs(monkeypatch, {}, calls)                     # 원문 없음
        monkeypatch.setattr("bot.dart_client.get_dart", lambda *a, **k: _Dart())
        out = bl.review_with_refresh(cap=2)
        assert len(calls) == 2, calls
        assert "상한으로 1건은 다음 회차" in out, out
        assert "원문 없음 2" in out, out

    def test_the_bot_task_refreshes_before_it_sends(self):
        """배선(#20) — 봇 태스크가 **재조회하는 진입점**을 부른다. 텔레그램
        미설치 환경에서도 재도록 소스를 AST 로 본다(기존 스케줄 테스트와 같은
        규약)."""
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "bot" / "telegram_bot.py"
               ).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_periodic_backlog_review")
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        assert "review_with_refresh" in names, names
        assert "review_text" not in names, "재조회 없이 요약만 보낸다"
        # ⚠️ 별칭이 이름 검사를 속인다(생존 G1: `import review_text as
        # review_with_refresh`) — import 의 **원래 이름**을 본다.
        got = {(a.name, a.asname) for n in ast.walk(fn)
               if isinstance(n, ast.ImportFrom) and n.module == "bot.dart_backlog"
               for a in n.names}
        assert got == {("review_with_refresh", None)}, got
        # ⚠️ 받을 사람이 없으면 재조회도 안 한다 — 정기보고서 최대 40건을
        # 받는 일이다(리뷰 L5). 수신자 조회가 재조회 **앞**이어야 한다.
        lines = {n.func.id if isinstance(n.func, ast.Name) else
                 getattr(n.func, "attr", ""): n.lineno
                 for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert lines["_dfa_status"] < lines["to_thread"], lines
        # ⚠️ 호출 순서만 보면 `if not chat_id: continue` 를 재조회 **뒤로** 옮기는
        # 변형이 통과한다(배포 전 2차 독립 리뷰 L4) — 건너뛰는 **분기**가 앞이어야.
        guard = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.If)
                 and isinstance(n.test, ast.UnaryOp)
                 and isinstance(n.test.op, ast.Not)
                 and isinstance(n.test.operand, ast.Name)
                 and n.test.operand.id == "chat_id"
                 and any(isinstance(b, ast.Continue) for b in n.body)]
        assert guard and guard[0] < lines["to_thread"], (guard, lines)

    def test_cli_refill_uses_the_same_cap_as_the_report(self, tmp_path,
                                                         monkeypatch, capsys):
        """기본 상한을 두 곳에 리터럴로 적으면 갈린다(#38) — 소스 문자열이
        아니라 **호출 수**로 잰다(#19): 모듈 상수를 1 로 내리면 CLI 기본
        실행도 1건만 받아야 한다."""
        from bot.scripts import backlog_misses as bm
        bl, log = _ledger(tmp_path, monkeypatch)
        for t in ("111111", "222222"):
            self._stale(log, t)
        calls: list = []
        _docs(monkeypatch, {}, calls)
        monkeypatch.setattr("bot.dart_client.get_dart", lambda *a, **k: _Dart())
        monkeypatch.setattr(bl, "REFRESH_CAP", 1)
        assert bm.main(["x", "--refill"]) == 0
        assert len(calls) == 1, calls
        assert "상한 1 로 1건은 이번에 안 한다" in capsys.readouterr().out


# ── 합계행 발췌 · `--ticker` 가 원문을 찍는다 ─────────────────────────
class TestTotalRowExcerpt:
    """머리 발췌는 라벨 뒤 400자에서 끝나 행이 많은 표는 합계행이 창 밖이다
    (영풍 — 09-18 에 창을 넓혀도 또 밖이었다). 검산이 실제로 본 행을 따로
    싣는다 — '합계행 N값(검산실패)' 의 근거가 그 행이다."""

    LONG = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
            + "품목A 2026.01.01~2026.12.31 1,000 2,000 3,000 " * 14
            + "합 계 10,000 4,000 9,999 ※ 주석")

    def test_the_total_row_is_kept_even_when_the_head_window_ends_early(self):
        from bot.dart_backlog import backlog_excerpt, backlog_total_excerpt
        assert "합 계" not in backlog_excerpt(self.LONG)
        assert backlog_total_excerpt(self.LONG) == "합 계 10,000 4,000 9,999"

    def test_the_total_row_stops_at_the_table_end(self):
        """다음 절·각주는 합계행이 아니다 — 잘린 표에서만 찾는다(#38)."""
        from bot.dart_backlog import backlog_total_excerpt
        t = ("나. 수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고 "
             "A 1,000 400 600 ※ 각주 5. 위험관리 합 계 9,999")
        assert backlog_total_excerpt(t) == ""

    def test_it_uses_the_same_spot_as_the_head_excerpt(self):
        """앞 산문의 `수주잔고` 가 첫 라벨 자리여도 발췌는 **표**의 합계행이다
        — 머리 발췌와 다른 자리를 보면 한 표의 두 조각이 아니다(생존 E5)."""
        from bot.dart_backlog import backlog_excerpt, backlog_total_excerpt
        t = ("당사의 수주잔고 관리 방침은 다음과 같습니다 ※ 참고 나. 수주상황 "
             "(단위 : 백만원) 품목 수주총액 기납품액 수주잔고 A 1,000 400 650 "
             "합 계 1,000 400 650 ※ 끝")
        assert "품목 수주총액" in backlog_excerpt(t)
        assert backlog_total_excerpt(t) == "합 계 1,000 400 650"

    def test_the_ledger_keeps_it_bounded_and_the_report_escapes_it(
            self, tmp_path, monkeypatch):
        """원장 한 줄의 크기는 유계다(생존 W2) · 합계행은 DART 원문이라 `<`·`&`
        하나에 텔레그램이 메시지 전체를 거절한다(규칙 7, 생존 E3)."""
        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("123456", 2026, "11012", "형식미지원", "갈래", "머리",
                     "합 계 <1> & 2 " + "9 " * 200)
        exs = _rows(log)[0]["exs"]
        assert len(exs) == bl._TOTAL_EX_WIDTH, len(exs)
        out = bl.review_text()
        assert "합계행 <code>합 계 &lt;1&gt; &amp; 2" in out, out

    def test_the_probe_stores_it_and_the_report_shows_it(self, tmp_path,
                                                        monkeypatch):
        bl, log = _ledger(tmp_path, monkeypatch)
        _docs(monkeypatch, {"123456": self.LONG})
        val, _w = bl.backlog_probe(_Dart(), "123456", 2026, "11013")
        assert val is None
        rec = _rows(log)[0]
        assert rec["exs"] == "합 계 10,000 4,000 9,999", rec
        out = bl.review_text()
        assert "합계행 <code>합 계 10,000 4,000 9,999</code>" in out, out

    def test_ticker_cli_prints_the_excerpt_for_a_failed_quarter(
            self, monkeypatch, capsys):
        """보고서의 '원문 확인' 이 가리키는 곳 — 2026-10-02 까지 판정만 찍고
        원문은 한 글자도 안 찍어 그 안내가 거짓이었다(#55)."""
        import sys
        import types

        from bot.scripts import backlog_misses as bm
        _docs(monkeypatch, {"R1": self.LONG})

        class _D:
            api_key = "k"

            def find_periodic_reports(self, *a, **k):
                return [{"rcept_no": "R1", "rcept_dt": "20260515",
                         "report_nm": "분기보고서"}]

        monkeypatch.setattr("bot.dart_client.get_dart", lambda *a, **k: _D())
        monkeypatch.setitem(sys.modules, "bot.dart_quarterly",
                            types.SimpleNamespace(
                                get_quarterly_series=lambda *a, **k: [
                                    {"year": 2026, "reprt_code": "11013",
                                     "label": "26.1Q"}]))
        assert bm.per_quarter("123456") == 0
        out = capsys.readouterr().out
        assert "❌ 형식미지원" in out, out
        assert "원문: " in out and "품목 수주총액 기납품액 수주잔고" in out, out
        assert "합계행: 합 계 10,000 4,000 9,999" in out, out


# ── 원장 쓰기 직렬화 ───────────────────────────────────────────────
class TestLedgerWritesAreSerialized:
    """2026-10-02 부터 **성공한** 조회도 원장을 고친다(해소 줄 지우기). 차트
    한 장이 분기 4개를 병렬로 조회하므로(`map_bounded`) read-modify-write 가
    겹치면 한 스레드의 지우기를 다른 스레드의 쓰기가 덮는다.

    ⚠️ 못 보는 축(#274): 프로세스 **사이**(대시보드 ↔ 봇)의 유실은 이 락이
    못 막는다 — 임시 파일 이름만 프로세스별이라 섞인 파일은 안 생긴다."""

    def test_a_drop_waits_for_an_inflight_write(self, tmp_path, monkeypatch):
        import threading

        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("111111", 2026, "11012", "형식미지원", "Y")   # 지울 줄
        real = bl._write_ledger
        in_write, release = threading.Event(), threading.Event()
        first = {"done": False}

        def slow(body):
            if not first["done"]:
                first["done"] = True
                in_write.set()
                release.wait(5)
            real(body)
        monkeypatch.setattr(bl, "_write_ledger", slow)
        a = threading.Thread(target=bl._log_miss,
                             args=("222222", 2026, "11012", "형식미지원", "X"))
        a.start()
        assert in_write.wait(5)
        b = threading.Thread(target=bl.drop_fixable, args=("111111", 2026, "11012"))
        b.start()
        b.join(0.5)          # 락이 없으면 B 는 여기서 이미 끝났다(옛 파일을 읽고)
        release.set()
        a.join(5)
        b.join(5)
        left = sorted(r["ticker"] for r in _rows(log))
        assert left == ["222222"], left

    def test_a_single_row_drop_also_waits(self, tmp_path, monkeypatch):
        """`drop_miss` 도 같은 락이다 — 재조회가 줄마다 부른다(생존 K4)."""
        import threading

        bl, log = _ledger(tmp_path, monkeypatch)
        bl._log_miss("111111", 2026, "11012", "형식미지원", "Y")
        real = bl._write_ledger
        in_write, release = threading.Event(), threading.Event()
        first = {"done": False}

        def slow(body):
            if not first["done"]:
                first["done"] = True
                in_write.set()
                release.wait(5)
            real(body)
        monkeypatch.setattr(bl, "_write_ledger", slow)
        a = threading.Thread(target=bl._log_miss,
                             args=("222222", 2026, "11012", "형식미지원", "X"))
        a.start()
        assert in_write.wait(5)
        b = threading.Thread(target=bl.drop_miss,
                             args=("111111", 2026, "11012", "형식미지원"))
        b.start()
        b.join(0.5)
        release.set()
        a.join(5)
        b.join(5)
        left = sorted(r["ticker"] for r in _rows(log))
        assert left == ["222222"], left

    def test_temp_files_are_per_process(self, tmp_path, monkeypatch):
        """두 프로세스가 같은 임시 파일을 쓰면 섞인 파일이 원장이 된다."""
        import os

        bl, log = _ledger(tmp_path, monkeypatch)
        seen = []
        orig = bl._Path.replace
        monkeypatch.setattr(bl._Path, "replace",
                            lambda self, target: (seen.append(self.name),
                                                  orig(self, target))[1])
        bl._log_miss("111111", 2026, "11012", "형식미지원", "Y")
        assert seen == [f"{log.name}.{os.getpid()}.tmp"], seen
        assert not list(tmp_path.glob("*.tmp")), list(tmp_path.iterdir())


class TestSamplesPreferTheCurrentParser:
    def test_a_current_sample_wins_over_an_old_one(self):
        from bot import dart_backlog as bl
        rows = [{"ticker": "111111", "year": 2026, "reprt": "11012",
                 "reason": "형식미지원", "detail": "같은 갈래", "ex": "옛 표본"},
                {"ticker": "222222", "year": 2026, "reprt": "11012",
                 "reason": "형식미지원", "detail": "같은 갈래", "ex": "새 표본",
                 "ps": bl._parse_sig()}]
        (sm,) = bl.excerpt_samples(rows)
        assert sm["ex"] == "새 표본" and sm["old_parser"] is False, sm
        assert "옛 파서" not in bl.excerpt_line(sm)
        (old,) = bl.excerpt_samples(rows[:1])
        assert old["old_parser"] is True and "옛 파서" in bl.excerpt_line(old)

    def test_cli_summary_marks_old_samples_and_prints_the_total_row(
            self, tmp_path, monkeypatch, capsys):
        bl, log = _ledger(tmp_path, monkeypatch)
        log.write_text(json.dumps(
            {"ticker": "111111", "year": 2026, "reprt": "11012",
             "reason": "형식미지원", "detail": "갈래", "dv": bl._DETAIL_VOCAB,
             "ex": "머리", "exs": "합 계 1 2 3"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        from bot.scripts import backlog_misses as bm
        assert bm.summarize() == 0
        out = capsys.readouterr().out
        assert "· 옛 파서" in out and "합계행: 합 계 1 2 3" in out, out


class TestSiblingSweepUsesTheSameList:
    """`production_format_probe` 는 '고칠 것 아님' 목록을 리터럴로 들고 있었다
    — 새 갈래(`잔고0이하`)·기존 갈래(`원문미제공`)를 그 스윕만 '개선 여지' 로
    셌다(#38·#45). 단일 출처에서 파생한다."""

    def test_non_fixable_reasons_are_not_counted_as_fixable(self):
        from bot.dart_backlog import MISS_NO_DOC, MISS_NON_POSITIVE
        from bot.scripts.production_format_probe import fixable_reasons
        got = fixable_reasons({f"{MISS_NON_POSITIVE}": 1, MISS_NO_DOC: 2,
                               "명시적미공시": 3, "미검사": 4,
                               "형식미지원 · 헤더 통과 · 합계행 3값(검산실패)": 5})
        assert got == {"형식미지원 · 헤더 통과 · 합계행 3값(검산실패)": 5}, got
