"""실수 #430 — #429 가 '못 보는 축' 으로 남긴 제품 쪽 둘.

① 키 있는 빈손 메모: 키 없이 만든 스냅샷의 빈 재무를 키로 다시 물었는데 빈손이면
   6시간 다시 묻지 않았다 — 그런데 `get_normalized_financials` 는 원천의 '없다'
   (013)와 수신 실패를 같은 None 으로 줬다. 일시 실패 한 번이 6시간 빈 재무였다.
   이제 None 의 **사유**를 같은 호출에 싣고(`why=`), 원천이 답한 것만 6시간 ·
   나머지는 30분 믿는다(믿는 시간 수치는 `test_dart_blindspots_20261004.py`
   `test_memo_length_follows_the_reason` 이 잰다 — 여기선 사유 채널 자체).
② 키가 생기면 법인 정보·임원 지분 칸도 다시 받는다 — 옛 판은 재무·공시만 다시
   받아, 두 칸은 재수집 전까지 '수집 당시 키 없음' 이 남았다.
"""
from __future__ import annotations

import pytest

_KEY = "k" * 40


# ── ① get_normalized_financials 의 사유 채널 ────────────────────────────────
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


@pytest.fixture
def client(monkeypatch):
    """키 있는 실물 클라이언트 — 회사 목록·디스크 캐시·HTTP 만 갈아 끼운다."""
    import bot.dart_client as dc
    cli = dc.DartClient(_KEY)
    monkeypatch.setattr(cli, "_load_corp_code_map", lambda: {"005930": "00126380"})
    monkeypatch.setattr(cli, "_disk_get", lambda key: None)
    monkeypatch.setattr(cli, "_disk_set", lambda key, val, *a, **k: None,
                        raising=False)
    return cli


def _http(monkeypatch, payload=None, exc=None):
    import bot.dart_client as dc

    def get(*a, **k):
        if exc is not None:
            raise exc
        return _Resp(payload)
    monkeypatch.setattr(dc.requests, "get", get)


class TestFinancialsWhy:
    def test_keyless(self):
        import bot.dart_client as dc
        why: list = []
        assert dc.DartClient("").get_normalized_financials("005930.KS",
                                                           why=why) is None
        assert why == ["키 없음"]

    def test_bad_ticker(self, client):
        why: list = []
        assert client.get_normalized_financials("AAPL", why=why) is None
        assert why == ["티커 형식"]

    def test_corp_map_failure(self, client, monkeypatch):
        def boom():
            raise RuntimeError("목록 못 받음")
        monkeypatch.setattr(client, "_load_corp_code_map", boom)
        why: list = []
        assert client.get_normalized_financials("005930.KS", why=why) is None
        assert why == ["회사 목록 실패: RuntimeError"]

    def test_unknown_code(self, client):
        why: list = []
        assert client.get_normalized_financials("000001.KS", why=why) is None
        assert why == ["회사 코드 없음"]

    def test_network_failure(self, client, monkeypatch):
        import requests
        _http(monkeypatch, exc=requests.ConnectionError("x"))
        why: list = []
        assert client.get_normalized_financials("005930.KS", why=why) is None
        assert why == ["수신 실패: ConnectionError"]

    @pytest.mark.parametrize("status", ["013", "020", "800"])
    def test_status_codes(self, client, monkeypatch, status):
        _http(monkeypatch, {"status": status, "message": "x"})
        why: list = []
        assert client.get_normalized_financials("005930.KS", why=why) is None
        assert why == [f"status={status}"]

    def test_report_without_known_accounts(self, client, monkeypatch):
        _http(monkeypatch, {"status": "000", "list": []})
        why: list = []
        assert client.get_normalized_financials("005930.KS", why=why) is None
        assert why == ["계정 없음"]

    def test_success_leaves_why_empty(self, client, monkeypatch):
        import bot.dart_client as dc
        _http(monkeypatch, {"status": "000", "list": [{"x": 1}]})
        monkeypatch.setattr(dc, "_extract_dart_financials",
                            lambda items: {"매출": 1.0e12})
        why: list = []
        r = client.get_normalized_financials("005930.KS", why=why)
        assert r and r["financials"]["매출"] == 1.0e12
        assert why == []

    def test_without_why_still_returns_none(self, client, monkeypatch):
        """옛 호출 모양(`why` 없이)은 그대로 — 사유를 바라지 않는 호출부."""
        _http(monkeypatch, {"status": "013"})
        assert client.get_normalized_financials("005930.KS") is None

    def test_answered_classification_uses_the_product_reasons(self, client,
                                                              monkeypatch):
        """원천이 답한 것으로 분류되는 사유 문자열이 **제품이 실제로 내는**
        문자열과 같다(상수만 재면 둘이 갈라져도 통과한다, #19·#155)."""
        import bot.dart_client as dc
        got = {}
        for name, payload in (("013", {"status": "013"}),
                              ("계정", {"status": "000", "list": []}),
                              ("020", {"status": "020"})):
            _http(monkeypatch, payload)
            why: list = []
            client.get_normalized_financials("005930.KS", why=why)
            got[name] = dc.fin_empty_answered(why)
        assert got == {"013": True, "계정": True, "020": False}, got


class TestCollectFinancialsWhy:
    def test_passes_the_reason_through(self, monkeypatch):
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        cli = dc.DartClient(_KEY)
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)

        def gnf(self, ticker, year=None, fs_div="CFS", reprt_code="11011",
                **kw):
            if kw.get("why") is not None:
                kw["why"].append("status=013")
            return None
        monkeypatch.setattr(dc.DartClient, "get_normalized_financials", gnf)
        monkeypatch.setattr(ss, "map_bounded", lambda *a, **k: None,
                            raising=False)
        why: list = []
        out = ss.collect_kr_financials("005930.KS", why=why)
        assert why[:1] == ["status=013"], why
        assert not (out.get("kr") or {}).get("financials")

    def test_keyless_says_the_key(self, monkeypatch):
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient(""))
        why: list = []
        out = ss.collect_kr_financials("005930.KS", why=why)
        assert why == ["키 없음"]
        assert out == {"kr": {"dart_keyless_financials": "collection"}}

    def test_old_call_shape_is_kept(self, monkeypatch):
        """`why` 없이 부르면 원천 메서드도 `why` 없이 부른다 — 옛 모양만 아는
        대역·래퍼가 깨지지 않는다(#183)."""
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        cli = dc.DartClient(_KEY)
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
        seen: list = []

        def gnf(self, ticker, year=None, fs_div="CFS", reprt_code="11011"):
            seen.append(year)
            return None
        monkeypatch.setattr(dc.DartClient, "get_normalized_financials", gnf)
        ss.collect_kr_financials("005930.KS")
        assert seen and seen[0] is None, seen


# ── ② 법인 정보·임원 지분 다시 받기 ───────────────────────────────────────────
_CI = {"status": "000", "ceo_nm": "홍길동", "est_dt": "19690113",
       "adres": "경기도 수원시", "acc_mt": "12", "induty_code": "264",
       "corp_name": "삼성전자", "jurir_no": "1301110006246"}
_HOLDERS = [{"name": "이재용", "shares": 1}]


@pytest.fixture
def reask(monkeypatch):
    """키 있는 실물 클라이언트 + 원천 쪽 스텁(#348 — 수집기를 통째로 바꾸면 그
    안의 배선을 못 본다). 호출 수를 센다."""
    import bot.dart_client as dc
    import bot.dashboard as d
    cli = dc.DartClient(_KEY)
    monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
    monkeypatch.setattr(d, "_KR_REASK_EMPTY", {})
    monkeypatch.setattr(d, "_KR_FIN_KEYED_EMPTY", {})
    got = {"ci": 0, "ins": 0, "ci_value": dict(_CI),
           "ins_value": list(_HOLDERS)}

    def gci(self, code):
        got["ci"] += 1
        return got["ci_value"]

    def gih(self, code):
        got["ins"] += 1
        return got["ins_value"]
    monkeypatch.setattr(dc.DartClient, "get_company_info", gci)
    monkeypatch.setattr(dc.DartClient, "get_insider_holdings", gih)
    for m in ("get_recent_disclosures", "get_major_shareholders",
              "get_affiliate_investments"):
        monkeypatch.setattr(dc.DartClient, m, lambda self, *a, **k: None)
    return got


def _stored(*sections):
    """저장본 사본 — 다른 탭(뉴스·리서치·재무·수급·공시)은 이미 차 있어 그
    보강이 DART 를 따로 부르지 않는다(뉴스 폴백은 회사명을 `get_company_info` 로
    묻는다 — 그러면 이 경로의 호출 수가 섞인다)."""
    import bot.dart_client as dc
    kr: dict = {"flow": {"x": 1}, "disclosures": [{"title": "x"}],
                "financials": {"매출": 1.0}, "financials_ver": 10 ** 6,
                "research_reports": [{"title": "r", "target": 1}]}
    for s in sections:
        dc.mark_keyless(kr, s)
    return {"currency": "KRW", "kr": kr, "news": [{"title": "n"}],
            "financials": {"x": 1}, "financials_asof": "2999-01-01 00:00",
            "peer_comps": [{"ticker": "000660.KS", "is_subject": False}]}


def _enrich(si, ticker="005930.KS"):
    import bot.dashboard as d
    d._ensure_detail_enrichment(ticker, si)
    return si


class TestReaskCompanyInsiders:
    def test_keyed_render_fills_both_and_clears_records(self, reask):
        import bot.dart_client as dc
        si = _enrich(_stored("company", "insiders"))
        kr = si["kr"]
        assert (reask["ci"], reask["ins"]) == (1, 1)
        assert kr["ceo"] == "홍길동" and kr["established"] == "19690113"
        assert kr["address"] == "경기도 수원시" and kr["fiscal_month"] == "12"
        assert kr["insider_holdings"] == _HOLDERS
        assert dc.keyless_when(kr, "company") is None
        assert dc.keyless_when(kr, "insiders") is None

    def test_page_no_longer_blames_the_key(self, reask):
        """화면 끝까지 — 다시 받은 두 칸 자리에 '수집 당시 키 없음' 이 없다."""
        import bot.dashboard as d
        si = _enrich(_stored("company", "insiders"))
        parts = d._render_stock_info_html({"ticker": "005930.KS",
                                           "stock_info": si})
        html = parts["other_panes"]
        assert "수집 당시 DART_API_KEY" not in html, html[:2000]
        assert "홍길동" in html

    def test_only_recorded_sections_are_asked(self, reask):
        si = _enrich(_stored("insiders"))
        assert (reask["ci"], reask["ins"]) == (0, 1)
        assert "ceo" not in si["kr"]
        _enrich(_stored())
        assert (reask["ci"], reask["ins"]) == (0, 1), "기록 없는 칸까지 물었다"

    def test_still_keyless_asks_nothing(self, reask, monkeypatch):
        import bot.dart_client as dc
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient(""))
        si = _enrich(_stored("company", "insiders"))
        assert (reask["ci"], reask["ins"]) == (0, 0)
        assert dc.keyless_when(si["kr"], "company") == "collection"
        assert dc.keyless_when(si["kr"], "insiders") == "collection"

    def test_keyed_empty_clears_the_record_and_is_remembered(self, reask):
        """키로 물었는데 빈손 — 기록은 지우고(키는 더는 사유가 아니다) 30분
        기억한다(렌더마다 되묻지 않는다). 저장본은 렌더마다 새 사본이다."""
        import bot.dart_client as dc
        reask["ci_value"], reask["ins_value"] = None, []
        for _ in range(3):
            si = _enrich(_stored("company", "insiders"))
            assert dc.keyless_when(si["kr"], "company") is None
            assert dc.keyless_when(si["kr"], "insiders") is None
        assert (reask["ci"], reask["ins"]) == (1, 1), "렌더마다 다시 물었다"

    def test_memo_is_bounded_and_short(self, reask):
        """믿는 시간은 30분 — 두 메서드는 원천의 '없다' 와 수신 실패를 같은
        빈손으로 준다. 수치로 못박는다(#66)."""
        import time

        import bot.dart_client as dc
        import bot.dashboard as d
        assert dc.PROVISIONAL_TTL_SEC == 30 * 60
        reask["ci_value"] = None
        d._KR_REASK_EMPTY[("005930.KS", "company")] = time.time() - 30 * 60 + 60
        _enrich(_stored("company"))
        assert reask["ci"] == 0, "기억이 살아 있는데 다시 물었다"
        d._KR_REASK_EMPTY[("005930.KS", "company")] = time.time() - 30 * 60 - 1
        _enrich(_stored("company"))
        assert reask["ci"] == 1, "기억이 지났는데 다시 묻지 않았다"

    def test_memo_is_per_ticker_and_section(self, reask):
        import time

        import bot.dashboard as d
        d._KR_REASK_EMPTY[("000660.KS", "company")] = time.time()
        d._KR_REASK_EMPTY[("005930.KS", "insiders")] = time.time()
        _enrich(_stored("company", "insiders"))
        assert (reask["ci"], reask["ins"]) == (1, 0)

    def test_exception_is_not_remembered(self, reask, monkeypatch):
        """예외는 일시 장애일 수 있다 — 기억하지 않고 기록도 그대로 둔다(수집
        당시 키가 없었다는 사실은 여전히 참이다)."""
        import bot.dart_client as dc
        import bot.dashboard as d
        import bot.stock_snapshot as ss
        tries: list = []

        def boom(code):
            tries.append(code)
            raise RuntimeError("일시 장애")
        monkeypatch.setattr(ss, "collect_kr_company", boom)
        for _ in range(2):
            si = _enrich(_stored("company"))
            assert dc.keyless_when(si["kr"], "company") == "collection"
        assert tries == ["005930", "005930"]
        assert d._KR_REASK_EMPTY == {}

    def test_dart_wins_over_fsc_corp_reg_no(self, reask):
        """DART 가 우선이다 — 스냅샷 수집의 병합 순서와 같다."""
        si = _stored("company")
        si["kr"]["corp_reg_no"] = "FSC-값"
        _enrich(si)
        assert si["kr"]["corp_reg_no"] == "1301110006246"


class TestCollectorsKeepTheSnapshotPath:
    """`_enrich_kr` 의 두 작업이 밖으로 뺀 수집기를 부른다 — 수집 결과가
    전과 같아야 한다(빼면서 칸 이름이 갈리면 화면이 비고 아무 감사도 안 걸린다)."""

    def test_company_fields(self, reask):
        import bot.stock_snapshot as ss
        out = ss.collect_kr_company("005930")
        assert out["kr"] == {"corp_reg_no": "1301110006246", "ceo": "홍길동",
                             "corp_name": "삼성전자", "ksic_code": "264",
                             "established": "19690113", "fiscal_month": "12",
                             "address": "경기도 수원시"}

    def test_insiders_top15(self, reask):
        import bot.stock_snapshot as ss
        reask["ins_value"] = [{"name": str(i)} for i in range(20)]
        out = ss.collect_kr_insiders("005930")
        assert len(out["kr"]["insider_holdings"]) == 15

    def test_keyless_marks_each_section(self, monkeypatch):
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient(""))
        assert ss.collect_kr_company("005930") == {
            "kr": {"dart_keyless_company": "collection"}}
        assert ss.collect_kr_insiders("005930") == {
            "kr": {"dart_keyless_insiders": "collection"}}

    def test_enrich_kr_tasks_use_the_collectors(self, monkeypatch):
        """배선(#20) — `_enrich_kr` 가 두 수집기를 부른다(헬퍼만 재면 작업이
        옛 인라인 코드로 돌아가도 통과한다)."""
        import bot.stock_snapshot as ss
        seen: list = []
        monkeypatch.setattr(ss, "collect_kr_company", lambda code: (
            seen.append(("c", code)) or {"kr": {"ceo": "가"}}))
        monkeypatch.setattr(ss, "collect_kr_insiders", lambda code: (
            seen.append(("i", code)) or {"kr": {"insider_holdings": [1]}}))
        snap: dict = {}
        ss._enrich_kr("005930.KS", snap)
        assert ("c", "005930") in seen and ("i", "005930") in seen, seen
        assert snap["kr"]["ceo"] == "가"
        assert snap["kr"]["insider_holdings"] == [1]
