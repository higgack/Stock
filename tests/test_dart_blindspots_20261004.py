"""실수 #428 후속(2026-10-04) — 그때 남겨 둔 세 가지를 순서대로 닫는다.

① 부분 캐시(델타 리뷰 L4): 걷는 동안 **못 물어본 곳**(목록·원문 조회의 일시
   실패)이 있었던 결과는 권위가 없다. 최신 분기 목록만 못 받고 옛 분기 문서가
   읽히면 옛 보고서 표가 최신 분기 키로 24시간 굳었다 — 그 하루 동안 최신
   보고서를 다시 찾지 않는다. 이제 30분만 믿고(`dart_client.PROVISIONAL_TTL_SEC`)
   화면이 그 사실을 표 아래 첫 각주로 말한다. 같은 걸음(후보를 순서대로 시도)을
   하는 수주잔고도 같은 규약이다(#38).
② 키 없을 때 DART 화면의 빈칸 사유 — 아래 `TestKeylessReasons`.
③ 스캐너의 못 보는 축 — `tests/test_dart_ready_20261002.py` 의 스캐너를 넓혔다.
"""
from __future__ import annotations

import pytest

_QS2 = [
    {"year": 2026, "reprt_code": "11013", "label": "26.1Q", "financials": {}},
    {"year": 2026, "reprt_code": "11012", "label": "26.2Q", "financials": {}},
]
_QS3 = [{"year": 2025, "reprt_code": "11011", "label": "25.4Q",
         "financials": {}}] + _QS2
_KEY = "k-1234567890"


def _real_tables_cache(monkeypatch, tmp_path):
    """실물 `_tables_cached`·`_tables_cache_write` 를 tmp 에 — 짧은 기록의
    나이는 `dp.time` 이 잰다(테스트가 시계를 옮긴다)."""
    import time
    import types

    import bot.dart_production as dp
    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
    clock = {"t": time.time()}
    monkeypatch.setattr(dp, "time",
                        types.SimpleNamespace(time=lambda: clock["t"]))
    return clock


def _fake_parsers(monkeypatch):
    """표 파서 대역 — 원문에 태그가 있으면 표를 낸다. 진짜 파서는
    `tests/test_dart_production.py` 가 잰다. 여기서 재는 것은 **걸음과 캐시**다."""
    import bot.dart_production as dp

    def mk(tag):
        def parse(markup):
            if markup and tag in markup:
                return {"table_html": f"<table><tr><td>{tag}</td></tr></table>",
                        "notes": ["원문 각주"]}
            return None
        return parse
    monkeypatch.setitem(dp._PARSERS, "products", mk("PRODUCTS"))
    monkeypatch.setitem(dp._PARSERS, "production", mk("PRODUCTION"))


def _wire(monkeypatch, lists: dict, docs: dict, *, no_file=()):
    """`find_periodic_reports` 는 `lists[reprt_code]`(호출 가능하면 그때그때)를,
    원문은 `docs[rcept_no]` 를 준다. `no_file` 은 원천이 status 014 로 '파일
    없음' 이라 **답한** 접수번호 — 제품의 `_fetch_doc_text` 가 그때 하는 대로
    `_DOC_NO_FILE` 에 적는다."""
    import bot.dart_client as dc
    import bot.dart_feed as df
    calls: list = []

    def fpr(self, ticker, year, rc):
        calls.append(rc)
        v = lists.get(rc, dc.PeriodicReports())
        return v() if callable(v) else v

    def fetch(rn, *a, **k):
        if rn in no_file:
            df._DOC_NO_FILE[str(rn)] = "014"
        return docs.get(rn)
    monkeypatch.setattr(dc.DartClient, "find_periodic_reports", fpr)
    monkeypatch.setattr(dc.DartClient, "find_periodic_report",
                        lambda self, *a: None)
    monkeypatch.setattr(df, "_fetch_doc_text", fetch)
    monkeypatch.setattr(df, "doc_was_truncated", lambda *a, **k: False)
    monkeypatch.setattr(df, "_DOC_NO_FILE", {})
    return calls


_DOCS = {"OLD": "<P>PRODUCTS PRODUCTION 26.1Q</P>",
         "NEW": "<P>PRODUCTS PRODUCTION 26.2Q</P>"}


class TestPeriodicReportsFailure:
    """목록 조회가 '없다' 와 '못 들었다' 를 갈라 싣는가(#82)."""

    @staticmethod
    def _client(monkeypatch, *responses):
        import bot.dart_client as dc
        seq = list(responses)
        monkeypatch.setattr(dc.DartClient, "stock_code_to_corp_code",
                            lambda self, code: "00126380")

        def get(*a, **k):
            r = seq.pop(0)
            if isinstance(r, BaseException):
                raise r
            return type("R", (), {"json": staticmethod(lambda: r)})()
        monkeypatch.setattr(dc.requests, "get", get)
        return dc.DartClient(_KEY)

    _ROW = {"rcept_no": "20260814000001", "report_nm": "반기보고서 (2026.06)",
            "rcept_dt": "20260814"}

    def test_window_exception_is_a_failure(self, monkeypatch):
        import requests

        import bot.dart_client as dc
        cli = self._client(monkeypatch, requests.ReadTimeout("t"))
        got = cli.find_periodic_reports("005930", 2026, "11012")
        assert got == [] and isinstance(got, dc.PeriodicReports)
        assert dc.list_failure(got) == "목록 조회 실패(ReadTimeout)"

    def test_status_013_is_an_answer(self, monkeypatch):
        """013 = 원천이 '조회된 데이터 없음' 이라고 **답했다** — 실패가 아니다.
        이걸 실패로 치면 보고서가 없는 분기마다 30분 기록이 되어 영원히 다시
        걷는다(#171)."""
        import bot.dart_client as dc
        cli = self._client(monkeypatch, {"status": "013", "message": "없음"})
        got = cli.find_periodic_reports("005930", 2026, "11012")
        assert got == [] and dc.list_failure(got) is None

    @pytest.mark.parametrize("st", ["020", "800", "900", ""])
    def test_other_status_is_a_failure(self, monkeypatch, st):
        import bot.dart_client as dc
        cli = self._client(monkeypatch, {"status": st})
        got = cli.find_periodic_reports("005930", 2026, "11012")
        assert got == [] and dc.list_failure(got) == f"목록 조회 status={st}"

    def test_answered_rows_carry_no_failure(self, monkeypatch):
        import bot.dart_client as dc
        cli = self._client(monkeypatch,
                           {"status": "000", "list": [self._ROW]},
                           {"status": "013"})
        got = cli.find_periodic_reports("005930", 2026, "11012")
        assert [r["rcept_no"] for r in got] == ["20260814000001"]
        assert dc.list_failure(got) is None

    def test_late_query_exception_keeps_rows_and_says_so(self, monkeypatch):
        """2차(늦게 낸 정정) 조회를 못 들었으면 1차 목록은 그대로 쓰되 그 사실을
        싣는다 — 정정본이 원본을 대신할 수 있다."""
        import requests

        import bot.dart_client as dc
        cli = self._client(monkeypatch,
                           {"status": "000", "list": [self._ROW]},
                           requests.ConnectionError("x"))
        got = cli.find_periodic_reports("005930", 2026, "11012")
        assert [r["rcept_no"] for r in got] == ["20260814000001"]
        assert dc.list_failure(got) == "후행 정정 조회 실패(ConnectionError)"

    def test_late_query_bad_status_says_so(self, monkeypatch):
        import bot.dart_client as dc
        cli = self._client(monkeypatch,
                           {"status": "000", "list": [self._ROW]},
                           {"status": "020"})
        got = cli.find_periodic_reports("005930", 2026, "11012")
        assert len(got) == 1
        assert dc.list_failure(got) == "후행 정정 조회 status=020"

    def test_list_failure_reads_only_its_own_type(self):
        """대역(테스트 가짜)의 맨 목록은 사유를 싣지 않는다 — 기존 호출부·가짜가
        그대로 돈다."""
        import bot.dart_client as dc
        assert dc.list_failure([]) is None
        assert dc.list_failure([{"rcept_no": "A"}]) is None
        assert dc.list_failure(None) is None
        assert dc.list_failure(dc.PeriodicReports()) is None
        assert dc.list_failure(dc.PeriodicReports(failed="x")) == "x"
        assert dc.PROVISIONAL_TTL_SEC == 30 * 60      # 크기도 못박는다(#66)


class TestTablesRollingPartial:
    """① 의 본 갈래 — 표 롤링(분기실적 탭 '주요 제품'·'생산능력·가동률')."""

    def test_latest_list_failure_is_believed_only_briefly(
            self, monkeypatch, tmp_path):
        """재현(델타 리뷰 L4): 최신 분기(26.2Q) 목록을 못 받고 26.1Q 문서가
        읽히면 옛 표가 최신 분기 키로 **24시간** 굳었다. 30분 안에는 다시 걷지
        않고(미리받기도 데울 것이 없다), 지나면 다시 걸어 최신 표로 바뀐다."""
        import types

        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        state = {"ok": False}
        calls = _wire(monkeypatch, {
            "11012": lambda: (dc.PeriodicReports([{"rcept_no": "NEW"}])
                              if state["ok"] else
                              dc.PeriodicReports(failed="목록 조회 실패(T)")),
            "11013": dc.PeriodicReports([{"rcept_no": "OLD"}]),
        }, _DOCS)
        clock = _real_tables_cache(monkeypatch, tmp_path)
        cli = dc.DartClient(_KEY)
        got = dp.tables_rolling(cli, "005930.KS", _QS2)
        assert got["production"]["basis_label"] == "26.1Q", got
        n = len(calls)
        clock["t"] += dc.PROVISIONAL_TTL_SEC - 60
        assert dp.tables_rolling(cli, "005930.KS", _QS2) == got
        assert len(calls) == n, "짧은 기록 안인데 목록을 다시 걸었다"
        made: list = []
        monkeypatch.setattr(dp, "_PREFETCH", set())
        monkeypatch.setattr(dp, "threading", types.SimpleNamespace(
            Thread=lambda *a, **k: made.append(k) or types.SimpleNamespace(
                start=lambda: None)))
        dp.prefetch_tables(cli, "005930.KS", _QS2)
        assert made == [], "짧은 기록이 살아 있는데 미리받기가 또 걸으러 갔다"
        state["ok"] = True
        clock["t"] += 120                      # 이제 30분을 넘겼다
        got2 = dp.tables_rolling(cli, "005930.KS", _QS2)
        assert got2["production"]["basis_label"] == "26.2Q", \
            "못 물어본 곳이 있던 결과가 30분을 넘겨 살아 있다(24시간 굽기)"
        assert "stale_note" not in got2["production"]
        m = len(calls)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        dp.tables_rolling(cli, "005930.KS", _QS2)
        assert len(calls) == m, "온전한 답인데 30분 만에 다시 걸었다"

    def test_screen_says_which_report_was_not_fetched(
            self, monkeypatch, tmp_path):
        """화면이 그 사실을 **보이는 줄로** 말한다(#43·#228) — 두 표 다, 원문
        각주보다 앞에. '26.1Q 보고서 기준' 만 보면 26.2Q 표가 원래 없는 건지 못
        받은 건지 모른다."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        _wire(monkeypatch, {
            "11012": dc.PeriodicReports(failed="목록 조회 실패(T)"),
            "11013": dc.PeriodicReports([{"rcept_no": "OLD"}]),
        }, _DOCS)
        _real_tables_cache(monkeypatch, tmp_path)
        got = dp.tables_rolling(dc.DartClient(_KEY), "005930.KS", _QS2)
        want = "26.2Q 보고서를 받지 못해 이 표가 최신이 아닐 수 있습니다"
        for html in (dp.render_html(got["production"]),
                     dp.render_products_html(got["products"])):
            assert want in html, html
            assert "26.1Q 보고서 기준" in html
            assert html.index(want) < html.index("원문 각주"), \
                "못 받은 사실이 원문 각주 뒤에 묻혔다"
            assert "30분 뒤 조회부터 다시 받습니다" in html

    def test_unreceived_document_is_a_miss(self, monkeypatch, tmp_path):
        """원문 갈래 — 26.2Q 목록은 왔는데 원문을 못 받았다(원천이 '파일 없음'
        이라고 답한 게 아니다) → 옛 표에 사유를 달고 짧게."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        calls = _wire(monkeypatch, {
            "11012": dc.PeriodicReports([{"rcept_no": "NEWX"}]),
            "11013": dc.PeriodicReports([{"rcept_no": "OLD"}]),
        }, _DOCS)
        clock = _real_tables_cache(monkeypatch, tmp_path)
        cli = dc.DartClient(_KEY)
        got = dp.tables_rolling(cli, "005930.KS", _QS2)
        assert got["production"]["basis_label"] == "26.1Q"
        assert "26.2Q 보고서를" in got["production"]["stale_note"]
        n = len(calls)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        dp.tables_rolling(cli, "005930.KS", _QS2)
        assert len(calls) > n, "원문을 못 받은 결과가 30분을 넘겨 살아 있다"

    def test_source_said_no_file_is_an_answer(self, monkeypatch, tmp_path):
        """반대 증거 — 원천이 status 014 로 '파일 없음' 이라고 **답했으면**
        그건 답이다. 그걸 못 물어본 것으로 치면 정정·첨부 접수건(원래 파일이
        없다)이 있는 회사는 30분마다 영원히 다시 걷는다(#171)."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        calls = _wire(monkeypatch, {
            "11012": dc.PeriodicReports([{"rcept_no": "NEWX"}]),
            "11013": dc.PeriodicReports([{"rcept_no": "OLD"}]),
        }, _DOCS, no_file={"NEWX"})
        clock = _real_tables_cache(monkeypatch, tmp_path)
        cli = dc.DartClient(_KEY)
        got = dp.tables_rolling(cli, "005930.KS", _QS2)
        assert got["production"]["basis_label"] == "26.1Q"
        assert "stale_note" not in got["production"], got
        n = len(calls)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        dp.tables_rolling(cli, "005930.KS", _QS2)
        assert len(calls) == n, "원천이 답한 결과를 30분 만에 다시 걸었다"

    def test_same_quarter_correction_is_named_as_such(
            self, monkeypatch, tmp_path):
        """같은 분기의 다른 접수본 갈래 — 앞 후보(정정본)를 못 받고 원본을
        읽었다. '26.2Q 보고서 기준' 표 아래 '26.2Q 보고서를 받지 못해' 라고
        적으면 화면이 제 말을 뒤집는다(#34)."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        _wire(monkeypatch, {
            "11012": dc.PeriodicReports([{"rcept_no": "NEWC"},
                                         {"rcept_no": "NEW"}]),
        }, _DOCS)
        _real_tables_cache(monkeypatch, tmp_path)
        got = dp.tables_rolling(dc.DartClient(_KEY), "005930.KS", _QS2)
        note = got["production"]["stale_note"]
        assert got["production"]["basis_label"] == "26.2Q"
        assert "26.2Q의 다른 접수본(정정 등)을 받지 못해" in note, note
        assert "26.2Q 보고서를" not in note, note

    def test_late_correction_query_failure_marks_the_quarter(
            self, monkeypatch, tmp_path):
        """늦게 낸 정정의 목록을 못 들었다 — 원본 표는 맞게 읽었어도 정정본이
        대신할 수 있으니 권위가 없다(짧게 · 같은 분기 문구)."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        calls = _wire(monkeypatch, {
            "11012": dc.PeriodicReports([{"rcept_no": "NEW"}],
                                        failed="후행 정정 조회 status=020"),
        }, _DOCS)
        clock = _real_tables_cache(monkeypatch, tmp_path)
        cli = dc.DartClient(_KEY)
        got = dp.tables_rolling(cli, "005930.KS", _QS2)
        assert "26.2Q의 다른 접수본" in got["production"]["stale_note"]
        n = len(calls)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        dp.tables_rolling(cli, "005930.KS", _QS2)
        assert len(calls) > n

    def test_miss_after_a_table_was_found_does_not_taint_it(
            self, monkeypatch, tmp_path):
        """제품 표는 26.2Q 에서 찾았고, 가동률 표를 찾느라 26.1Q 목록을 못 받은
        뒤 25.4Q 에서 찾았다 — 의심은 **그 뒤에 찾은 표**에만 붙는다. 앞의 표에
        사유를 달면 멀쩡한 표가 '최신이 아닐 수 있다' 가 된다."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        _wire(monkeypatch, {
            "11012": dc.PeriodicReports([{"rcept_no": "NEW"}]),
            "11013": dc.PeriodicReports(failed="목록 조회 status=020"),
            "11011": dc.PeriodicReports([{"rcept_no": "Y25"}]),
        }, {"NEW": "<P>PRODUCTS only</P>", "Y25": "<P>PRODUCTION 25</P>"})
        _real_tables_cache(monkeypatch, tmp_path)
        got = dp.tables_rolling(dc.DartClient(_KEY), "005930.KS", _QS3)
        assert got["products"]["basis_label"] == "26.2Q"
        assert "stale_note" not in got["products"], got["products"]
        assert got["production"]["basis_label"] == "25.4Q"
        assert "26.1Q 보고서를 받지 못해" in got["production"]["stale_note"]

    def test_clean_walk_writes_a_long_record(self, monkeypatch, tmp_path):
        """반대 증거 — 아무것도 놓치지 않았으면 종전대로 24시간이다."""
        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        calls = _wire(monkeypatch, {
            "11012": dc.PeriodicReports([{"rcept_no": "NEW"}])}, _DOCS)
        clock = _real_tables_cache(monkeypatch, tmp_path)
        cli = dc.DartClient(_KEY)
        got = dp.tables_rolling(cli, "005930.KS", _QS2)
        assert "stale_note" not in got["production"]
        n = len(calls)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        dp.tables_rolling(cli, "005930.KS", _QS2)
        assert len(calls) == n

    def test_log_names_the_misses(self, monkeypatch, tmp_path, caplog):
        """운영자용 상세는 로그로 — 화면에는 갈래 이름만(#391 청중이 둘)."""
        import logging

        import bot.dart_client as dc
        import bot.dart_production as dp
        _fake_parsers(monkeypatch)
        _wire(monkeypatch, {
            "11012": dc.PeriodicReports(failed="목록 조회 status=020"),
            "11013": dc.PeriodicReports([{"rcept_no": "OLD"}]),
        }, _DOCS)
        _real_tables_cache(monkeypatch, tmp_path)
        with caplog.at_level(logging.INFO, logger=dp.log.name):
            dp.tables_rolling(dc.DartClient(_KEY), "005930.KS", _QS2)
        assert "못 물어본 곳 1" in caplog.text, caplog.text
        assert "26.2Q 목록 조회 status=020" in caplog.text, caplog.text
        assert "30분만 믿고" in caplog.text


class TestStaleNote:
    @pytest.mark.parametrize("missed,basis,want", [
        ([("26.2Q", "x")], "26.1Q",
         "⚠️ 이번 조회에서 26.2Q 보고서를 받지 못해"),
        ([("26.2Q", "x"), ("26.2Q", "y")], "26.1Q",
         "⚠️ 이번 조회에서 26.2Q 보고서를 받지 못해"),
        ([("26.2Q", "x"), ("26.1Q", "y")], "25.4Q",
         "⚠️ 이번 조회에서 26.2Q, 26.1Q 보고서를 받지 못해"),
        ([("26.2Q", "x")], "26.2Q",
         "⚠️ 이번 조회에서 26.2Q의 다른 접수본(정정 등)을 받지 못해"),
        ([("26.2Q", "x"), ("26.1Q", "y")], "26.1Q",
         "⚠️ 이번 조회에서 26.2Q 보고서 · 26.1Q의 다른 접수본(정정 등)을 받지 못해"),
        ([("", "x")], "26.1Q", "⚠️ 이번 조회에서 일부 보고서를 받지 못해"),
    ])
    def test_wording(self, missed, basis, want):
        import bot.dart_production as dp
        got = dp.stale_note(missed, basis)
        assert got.startswith(want), got
        assert got.endswith("30분 뒤 조회부터 다시 받습니다")
        assert "최신이 아닐 수 있습니다" in got     # 단정하지 않는다(#165)


class TestBacklogProvisional:
    """수주잔고 — 같은 분기의 후보를 순서대로 시도하는 같은 걸음(#38)."""

    @staticmethod
    def _setup(monkeypatch, tmp_path, reps, docs, *, no_file=(), value=True):
        import time
        import types

        import bot.dart_backlog as db
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
        clock = {"t": time.time()}
        monkeypatch.setattr(db, "time",
                            types.SimpleNamespace(time=lambda: clock["t"]))
        fetched: list = []

        def fetch(rn, *a, **k):
            fetched.append(rn)
            if rn in no_file:
                df._DOC_NO_FILE[str(rn)] = "014"
            return docs.get(rn)
        monkeypatch.setattr(df, "_fetch_doc_text", fetch)
        monkeypatch.setattr(df, "_DOC_NO_FILE", {})
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: reps)
        monkeypatch.setattr(dc.DartClient, "find_periodic_report",
                            lambda self, *a: None)
        monkeypatch.setattr(db, "parse_backlog", lambda t: (
            {"value": 1.0e12} if value and "BACKLOG" in (t or "") else None))
        monkeypatch.setattr(db, "drop_fixable", lambda *a, **k: None)
        monkeypatch.setattr(db, "_log_miss", lambda *a, **k: None)
        return clock, fetched

    def test_earlier_candidate_unreceived_is_believed_briefly(
            self, monkeypatch, tmp_path):
        """앞 후보(정정본)를 못 받고 원본을 읽었다 — 원본의 값을 24시간
        굳히지 않는다."""
        import bot.dart_backlog as db
        import bot.dart_client as dc
        clock, fetched = self._setup(
            monkeypatch, tmp_path,
            dc.PeriodicReports([{"rcept_no": "C"}, {"rcept_no": "O"}]),
            {"O": "BACKLOG original"})
        cli = dc.DartClient(_KEY)
        assert db.backlog_probe(cli, "005930.KS", 2026, "11012") == (1.0e12,
                                                                     "정상")
        n = len(fetched)
        clock["t"] += dc.PROVISIONAL_TTL_SEC - 60
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert len(fetched) == n, "짧은 기록 안인데 다시 받았다"
        clock["t"] += 120
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert len(fetched) > n, "못 물어본 곳이 있던 답이 30분을 넘겨 살아 있다"

    def test_source_said_no_file_is_an_answer(self, monkeypatch, tmp_path):
        """반대 증거 — 앞 후보가 원천이 답한 '파일 없음'(014)이면 원본이 답이다."""
        import bot.dart_backlog as db
        import bot.dart_client as dc
        clock, fetched = self._setup(
            monkeypatch, tmp_path,
            dc.PeriodicReports([{"rcept_no": "C"}, {"rcept_no": "O"}]),
            {"O": "BACKLOG original"}, no_file={"C"})
        cli = dc.DartClient(_KEY)
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        n = len(fetched)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert len(fetched) == n, "원천이 답한 결과를 30분 만에 다시 받았다"

    def test_list_failure_marks_even_a_read_answer(self, monkeypatch, tmp_path):
        """늦게 낸 정정의 목록을 못 들었다 — 원본을 읽었어도 짧게."""
        import bot.dart_backlog as db
        import bot.dart_client as dc
        clock, fetched = self._setup(
            monkeypatch, tmp_path,
            dc.PeriodicReports([{"rcept_no": "O"}],
                               failed="후행 정정 조회 실패(Timeout)"),
            {"O": "BACKLOG original"})
        cli = dc.DartClient(_KEY)
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        n = len(fetched)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert len(fetched) > n

    def test_miss_answer_is_also_brief_when_something_was_missed(
            self, monkeypatch, tmp_path):
        """값을 못 낸 답(사유)도 같은 규약 — 앞 후보를 못 받았으면 그 사유를
        24시간 믿지 않는다."""
        import bot.dart_backlog as db
        import bot.dart_client as dc
        clock, fetched = self._setup(
            monkeypatch, tmp_path,
            dc.PeriodicReports([{"rcept_no": "C"}, {"rcept_no": "O"}]),
            {"O": "본문에 수주 없음"}, value=False)
        cli = dc.DartClient(_KEY)
        v, _why = db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert v is None
        n = len(fetched)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert len(fetched) > n

    def test_clean_answer_is_long(self, monkeypatch, tmp_path):
        """반대 증거 — 놓친 곳이 없으면 종전대로 24시간."""
        import bot.dart_backlog as db
        import bot.dart_client as dc
        clock, fetched = self._setup(
            monkeypatch, tmp_path,
            dc.PeriodicReports([{"rcept_no": "O"}]), {"O": "BACKLOG"})
        cli = dc.DartClient(_KEY)
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        n = len(fetched)
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        db.backlog_probe(cli, "005930.KS", 2026, "11012")
        assert len(fetched) == n


# ──────────────────────────────────────────────────────────────────────────
# ② 키 없을 때 DART 화면들이 빈칸의 사유를 말한다
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def keyless_dart(monkeypatch):
    """키 없는 **실물** `DartClient`(None 스텁은 옛 판에서도 통과한다, #427)."""
    import bot.dart_client as dc
    cli = dc.DartClient("")
    monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
    return cli


@pytest.fixture
def keyed_dart(monkeypatch):
    """키 있는 클라이언트 — DART 메서드는 빈손(원천이 정말 비었다)."""
    import bot.dart_client as dc
    cli = dc.DartClient(_KEY)
    monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
    for m in ("get_company_info", "get_insider_holdings",
              "get_recent_disclosures", "get_major_shareholders",
              "get_affiliate_investments", "find_periodic_report"):
        monkeypatch.setattr(dc.DartClient, m, lambda self, *a, **k: None)
    return cli


def _all_keyless(when="collection"):
    """네 DART 칸 전부 키 없이 모은 스냅샷의 기록."""
    import bot.dart_client as dc
    kr: dict = {}
    for s in dc.KEYLESS_SECTIONS:
        dc.mark_keyless(kr, s, when)
    return kr


def _panes(si, ticker="018260.KS"):
    from bot.dashboard import _render_stock_info_html
    return _render_stock_info_html({"ticker": ticker, "stock_info": si})


def _pane(parts, pane_id):
    """한 pane 만 잘라 본다 — 페이지 전체 grep 은 다른 탭의 같은 낱말이 대신
    만족시킨다(#55·#75)."""
    html = parts["other_panes"]
    i = html.index(f'id="{pane_id}"')
    j = html.find('<div class="si-pane"', i + 1)
    return html[i:j if j > 0 else None]


class TestKeylessReasons:
    def test_wording_is_one_source(self):
        import bot.dart_client as dc
        assert (dc.keyless_reason("공시 목록을")
                == "DART_API_KEY 없음 — 공시 목록을 받지 못했습니다")
        assert (dc.keyless_reason("공시 목록을", at_collection=True)
                == "수집 당시 DART_API_KEY 없음 — 공시 목록을 받지 못했습니다")

    def test_quarterly_tab_uses_the_same_source(self, keyless_dart):
        """분기실적 탭의 기존 문구가 단일 출처를 거친다 — 문구가 두 벌이면 한
        쪽만 바뀐다(#38). 글자는 그대로다."""
        import bot.quarterly_infographic as qi
        assert (qi._empty_payload_reason("005930.KS")
                == "DART_API_KEY 없음 — 국내 분기 재무(DART)를 받지 못했습니다")

    # ── 스냅샷 기록 ────────────────────────────────────────────────────
    def test_note_marks_only_an_empty_keyless_answer(self):
        """빈손 + 키 없음일 때만 **그 칸에** 적는다 — 받은 게 있으면(키 없이
        캐시로 답한 것 포함, 실수 #428) 적지 않고, 키가 있는데 빈손이면 원천이
        빈 것이다."""
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        cases = [(dc.DartClient(""), [], True), (dc.DartClient(""), None, True),
                 (dc.DartClient(""), [{"x": 1}], False),
                 (dc.DartClient(_KEY), [], False), (None, [], True)]
        for dart, got, want in cases:
            out: dict = {}
            ss._note_dart_keyless(out, dart, got, "insiders")
            kr = out.get("kr") or {}
            assert (kr == {"dart_keyless_insiders": "collection"}) is want, \
                (dart and dart.api_key, got, kr)
            assert want or kr == {}, kr

    def test_snapshot_collection_records_keyless(self, keyless_dart):
        """수집기를 통째로 태운다 — 헬퍼만 재면 배선을 떼는 변형을 못 잡는다
        (#20). 소켓은 루트 conftest 가 막아 다른 원천은 조용히 실패한다.
        네 칸이 **전부** 남아야 한다 — 작업 결과를 `setdefault` 로 합치므로
        칸 기록을 중첩 dict 로 두면 첫 작업의 것만 남는다(리뷰 F7 수정의 함정)."""
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        snap: dict = {}
        ss._enrich_kr("018260.KS", snap)
        kr = snap.get("kr") or {}
        assert {s: dc.keyless_when(kr, s) for s in dc.KEYLESS_SECTIONS} == {
            s: "collection" for s in dc.KEYLESS_SECTIONS}, kr

    @pytest.mark.parametrize("task", ["company", "insider", "disclosures"])
    def test_each_dart_task_records_keyless(self, keyless_dart, monkeypatch,
                                             task):
        """세 DART 작업 각각이 기록한다 — 하나만 남기면 나머지 둘의 배선을
        지워도 통과한다(#91c). 나머지 작업과 재무 수집은 빈 결과로 고정."""
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        hit = {"company": "get_company_info", "insider": "get_insider_holdings",
               "disclosures": "get_recent_disclosures"}
        for name, meth in hit.items():
            if name != task:
                monkeypatch.setattr(dc.DartClient, meth,
                                    lambda self, *a, **k: [{"x": 1}]
                                    if "holdings" in meth or "disclosures" in meth
                                    else {"status": "000"})
        monkeypatch.setattr(ss, "collect_kr_financials", lambda t: {})
        snap: dict = {}
        ss._enrich_kr("018260.KS", snap)
        kr = snap.get("kr") or {}
        section = {"company": "company", "insider": "insiders",
                   "disclosures": "disclosures"}[task]
        assert ({k: v for k, v in kr.items() if k.startswith("dart_keyless")}
                == {f"dart_keyless_{section}": "collection"}), (task, kr)

    def test_keyed_collection_records_nothing(self, keyed_dart, monkeypatch):
        """반대 증거 — 키가 있으면 빈손이어도 적지 않는다(원천이 빈 것이다)."""
        import bot.stock_snapshot as ss
        monkeypatch.setattr(ss, "collect_kr_financials", lambda t: {})
        snap: dict = {}
        ss._enrich_kr("018260.KS", snap)
        assert not [k for k in (snap.get("kr") or {})
                    if k.startswith("dart_keyless")], snap.get("kr")

    def test_financials_collector_records_keyless(self, keyless_dart):
        import bot.stock_snapshot as ss
        assert ss.collect_kr_financials("018260.KS") == {
            "kr": {"dart_keyless_financials": "collection"}}

    # ── 종목 상세 화면 ─────────────────────────────────────────────────
    def test_live_keyless_page_says_why_in_every_empty_dart_section(
            self, keyless_dart, monkeypatch):
        """공시 탭 · 주주 탭 · 기업 탭(DART 법인 칸 · K-IFRS 재무 요약) 이 각자
        자리에서 사유를 말한다. 주주 탭은 **한 줄**로 모은다(#395)."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", False)
        parts = _panes({"currency": "KRW", "kr": _all_keyless()})
        comp = _pane(parts, "si-company")
        assert ("수집 당시 DART_API_KEY 없음 — 대표자·설립일·주소·결산월·"
                "산업분류를 받지 못했습니다") in comp, comp
        assert ("수집 당시 DART_API_KEY 없음 — DART 재무제표를 받지 "
                "못했습니다") in comp
        assert "K-IFRS 재무 요약 DART" not in comp, \
            "사유 문단을 DART 재무표인 양 출처에 적었다"
        hold = _pane(parts, "si-holders")
        want = ("DART_API_KEY 없음 — 임원·주요주주 지분·최대주주·계열회사 "
                "표를 받지 못했습니다")
        assert want in hold, hold
        assert hold.count("DART_API_KEY 없음") == 1, "같은 사유를 표마다 되풀이했다"
        assert "수집 당시" not in hold, \
            "이 렌더가 지금 키 없이 물은 표까지 '수집 당시' 라고 적었다"
        disc = _pane(parts, "si-disclosures")
        assert ("수집 당시 DART_API_KEY 없음 — 공시 목록을 받지 못했습니다"
                in disc), disc

    @pytest.mark.parametrize("broken,left", [(1, "계열회사"), (2, "최대주주")])
    def test_one_live_table_alone_still_says_now(self, broken, left,
                                                 monkeypatch):
        """두 라이브 표 중 **한쪽만** 키 없이 물었을 때도 '지금' 이라고 적는다.
        둘 다 물으면 어느 한쪽의 `_now` 가 다른 쪽을 대신 채워, 한쪽 표시를
        지우는 변형이 살아남았다(뮤테이션 K16·K16b, #91b). 한 블록은 예외로
        끊고(그 블록의 `except` 가 받는다) 남은 블록만 태운다 — 이 렌더의
        `get_dart()` 호출은 최대주주(1번째) · 계열회사(2번째) 둘뿐이다."""
        import bot.dart_client as dc
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", False)
        cli, calls = dc.DartClient(""), []

        def fake(*a, **k):
            calls.append(1)
            if len(calls) == broken:
                raise RuntimeError("이 블록은 끊는다")
            return cli
        monkeypatch.setattr(dc, "get_dart", fake)
        hold = _pane(_panes({"currency": "KRW", "kr": {}}), "si-holders")
        assert len(calls) == 2, "라이브 블록의 get_dart 호출 순서가 바뀌었다"
        assert (f"DART_API_KEY 없음 — {left} 표를 받지 못했습니다"
                in hold), hold
        assert "수집 당시" not in hold, \
            f"지금 키 없이 물은 {left} 표를 '수집 당시' 라고 적었다"

    def test_batch_page_says_at_collection_only(self, keyless_dart,
                                                monkeypatch):
        """배치 렌더는 라이브 표(최대주주·계열회사)를 안 묻는다 — 스냅샷 기록만
        있으므로 '수집 당시' 로, 그 표들은 사유 목록에 없다."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        hold = _pane(_panes({"currency": "KRW",
                             "kr": _all_keyless()}), "si-holders")
        assert ("수집 당시 DART_API_KEY 없음 — 임원·주요주주 지분 표를 받지 "
                "못했습니다") in hold, hold
        assert "최대주주" not in hold

    def test_no_record_no_reason(self, keyed_dart, monkeypatch):
        """반대 증거 — 기록이 없고 키가 있으면 키 탓을 지어내지 않는다(#165)."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", False)
        parts = _panes({"currency": "KRW", "kr": {}})
        assert "DART_API_KEY" not in parts["other_panes"]

    def test_present_data_keeps_its_section_quiet(self, keyless_dart,
                                                  monkeypatch):
        """기록이 있어도 **값이 있는 칸**은 사유를 달지 않는다 — 나중에 키 있는
        보강이 채웠을 수 있다."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        si = {"currency": "KRW", "kr": {
            **_all_keyless(), "ceo": "홍길동",
            "insider_holdings": [{"name": "A", "role": "임원", "shares": 1,
                                  "pct": 0.1, "changed_on": "2026-01-02"}],
            "disclosures": [{"date": "2026-01-02", "title": "공시"}]}}
        parts = _panes(si)
        assert "법인 정보" not in _pane(parts, "si-company")
        assert "DART_API_KEY" not in _pane(parts, "si-holders")
        assert "DART_API_KEY" not in _pane(parts, "si-disclosures")
        # 재무만 비었다 — 그 칸만 말한다
        assert "DART 재무제표를" in _pane(parts, "si-company")

    def test_present_financials_keep_the_kifrs_slot_quiet(self, keyless_dart,
                                                          monkeypatch):
        """기록이 있어도 재무표가 그려졌으면 그 자리는 표다 — 사유를 덧붙이면
        멀쩡한 표 아래 '받지 못했습니다' 가 붙는다."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        comp = _pane(_panes({"currency": "KRW", "kr": {
            **_all_keyless(),
            "financials": {"year": 2025, "fs_div": "CFS", "매출액": 1.0e12}}}),
            "si-company")
        assert "K-IFRS 재무 요약 FY2025" in comp, comp
        assert "DART 재무제표를" not in comp

    # ── 라이브 보강 ────────────────────────────────────────────────────
    def test_enrichment_records_keyless_disclosures(self, keyless_dart):
        """렌더가 방금 키 없이 물은 공시만 적는다 — **공시 칸에, '지금'** 으로.
        다른 칸에 번지면 키로 받아 정말 빈 칸까지 '키 없음' 이 된다(리뷰 F7)."""
        import bot.dashboard as d
        si = {"kr": {}}
        d._ensure_detail_enrichment("018260.KS", si)
        assert ({k: v for k, v in si["kr"].items() if k.startswith("dart_keyless")}
                == {"dart_keyless_disclosures": "now"}), si["kr"]

    def test_enrichment_keyed_empty_records_nothing(self, keyed_dart):
        import bot.dashboard as d
        si = {"kr": {}}
        d._ensure_detail_enrichment("018260.KS", si)
        assert not [k for k in si["kr"] if k.startswith("dart_keyless")], si["kr"]

    def test_keyless_snapshot_financials_are_fetched_once_key_returns(
            self, keyed_dart, monkeypatch):
        """키 없이 만든 스냅샷의 빈 재무는 키가 생기면 다시 받는다 — 안 그러면
        그 빈칸이 영원히 남는다(#18). 기록이 없던 빈칸은 종전대로 안 받는다."""
        import bot.dashboard as d
        import bot.stock_snapshot as ss
        calls: list = []
        monkeypatch.setattr(ss, "collect_kr_financials", lambda t: (
            calls.append(t) or {"kr": {"financials": {"매출액": 1.0}}}))
        si = {"kr": {"dart_keyless_financials": "collection"}}
        d._ensure_detail_enrichment("018260.KS", si)
        assert calls == ["018260.KS"]
        assert si["kr"]["financials"] == {"매출액": 1.0}
        assert "dart_keyless_financials" not in si["kr"], \
            "키로 받아 채운 칸에 '키 없음' 기록이 남았다"
        calls.clear()
        si2 = {"kr": {}}
        d._ensure_detail_enrichment("018260.KS", si2)
        assert calls == [], "기록 없는 빈칸까지 다시 받으러 갔다"

    def test_keyless_snapshot_financials_wait_while_still_keyless(
            self, keyless_dart, monkeypatch):
        import bot.dashboard as d
        import bot.stock_snapshot as ss
        calls: list = []
        monkeypatch.setattr(ss, "collect_kr_financials",
                            lambda t: calls.append(t) or {})
        d._ensure_detail_enrichment(
            "018260.KS", {"kr": {"dart_keyless_financials": "collection"}})
        assert calls == [], "키가 여전히 없는데 물어보러 갔다"

    # ── 분기실적 탭의 성장동력·리스크 카드 ───────────────────────────────
    def test_growth_card_names_the_key(self):
        import bot.dart_client as dc
        import bot.dart_growth_risk as gr
        got = gr.build_growth_risk(dc.DartClient(""), "018260.KS", 2026,
                                   "11012", {})
        assert got == {"ok": False, "error": dc.keyless_reason(
            "근거가 될 정기보고서를")}

    def test_growth_card_keyed_without_report_keeps_old_reason(
            self, monkeypatch):
        import bot.dart_client as dc
        import bot.dart_growth_risk as gr
        monkeypatch.setattr(dc.DartClient, "find_periodic_report",
                            lambda self, *a: None)
        got = gr.build_growth_risk(dc.DartClient(_KEY), "018260.KS", 2026,
                                   "11012", {})
        assert got["error"] == "정기보고서 rcept_no 미확인"

    # ── 차트 공시 마커 안내 ────────────────────────────────────────────
    @staticmethod
    def _chart(monkeypatch, ticker, events):
        import pandas as pd

        import bot.chart_data as cd
        import bot.chart_events as ce
        monkeypatch.setattr(ce, "fetch_disclosure_events",
                            lambda *a, **k: list(events))
        idx = pd.date_range("2026-06-01", periods=60, freq="B")
        close = pd.Series([100.0 + i for i in range(60)], index=idx)
        return cd._series_payload(close, "KRW", 0, ticker=ticker)

    def test_chart_note_when_keyless_and_no_markers(self, keyless_dart,
                                                    monkeypatch):
        p = self._chart(monkeypatch, "018260.KS", [])
        assert p.get("events_note") == (
            "DART_API_KEY 없음 — 공시 마커(DART 공시 목록)를 받지 못했습니다")

    @pytest.mark.parametrize("ticker,events,keyed", [
        ("018260.KS", [], True),          # 키가 있으면 원천이 빈 것
        ("AAPL", [], False),              # 비-KR 은 DART 를 안 쓴다
        ("018260.KS", [{"time": "2026-06-03", "title": "x"}], False),
    ])
    def test_chart_note_spares(self, monkeypatch, ticker, events, keyed):
        import bot.dart_client as dc
        cli = dc.DartClient(_KEY if keyed else "")
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
        p = self._chart(monkeypatch, ticker, events)
        assert "events_note" not in p

    def test_chart_storage_note_says_at_collection(self, keyless_dart,
                                                   monkeypatch):
        """아카이브에 영구 저장되는 페이로드는 '수집 당시' 로 적는다 — 키를
        넣은 뒤 그 아카이브를 봐도 문장이 거짓이 되지 않게(#165, 실수 #429
        독립 리뷰 F1)."""
        import pandas as pd

        import bot.chart_data as cd
        import bot.chart_events as ce
        monkeypatch.setattr(ce, "fetch_disclosure_events", lambda *a, **k: [])
        idx = pd.date_range("2026-06-01", periods=60, freq="B")
        close = pd.Series([100.0 + i for i in range(60)], index=idx)
        p = cd._series_payload(close, "KRW", 0, ticker="018260.KS",
                               for_storage=True)
        assert p.get("events_note") == (
            "수집 당시 DART_API_KEY 없음 — 공시 마커(DART 공시 목록)를 받지 "
            "못했습니다")

    # 실제 로드 순서(저장본 → lite 첫 응답 → 나머지 응답)를 **제품 JS 그대로**
    # 태운다 — 안내 한 줄만 떼어 돌리던 옛 테스트는 그 사유가 화면에 닿는지를
    # 못 봤다(lite 엔 공시가 없고 나머지 응답의 키 목록엔 events_note 가
    # 빠져 있었다, 실수 #429 독립 리뷰 F1). DOM·차트 라이브러리만 만능 스텁.
    _CHART_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const cfg = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const H = { get(t, p) { if (p === Symbol.toPrimitive) return () => 0;
                        if (p === 'then') return undefined;
                        if (p === 'length') return 0; return P; },
            apply() { return P; }, construct() { return P; } };
const P = new Proxy(function () {}, H);
function mkEl(id) {
  const o = { id, innerHTML: '', textContent: '', style: {}, className: '',
              clientWidth: 800, clientHeight: 300, dataset: {}, _a: {} };
  o.getAttribute = (n) => (n in o._a ? o._a[n] : null);
  o.setAttribute = (n, v) => { o._a[n] = String(v); };
  o.appendChild = (c) => c; o.removeChild = (c) => c;
  o.addEventListener = () => {};
  o.querySelector = () => null; o.querySelectorAll = () => [];
  o.classList = { add() {}, remove() {}, contains() { return false; },
                  toggle() {} };
  return new Proxy(o, { get(t, p) { return p in t ? t[p] : P; },
                        set(t, p, v) { t[p] = v; return true; } });
}
const els = {}, clicks = [], cross = [];
global.document = {
  readyState: 'complete',
  getElementById(id) {
    if (!(id in els)) {
      els[id] = mkEl(id);
      if (id === 'chart-data') els[id].textContent = JSON.stringify(cfg.initial);
      if (id === 'price-chart') els[id].setAttribute('data-ticker', cfg.ticker);
    }
    return els[id];
  },
  createElement(t) { return mkEl('new:' + t); },
  documentElement: { dataset: { theme: 'light' } },
  addEventListener(type, fn) { if (type === 'click') clicks.push(fn); },
  body: mkEl('body'),
  querySelector() { return null; }, querySelectorAll() { return []; },
};
global.window = global; global.addEventListener = () => {};
// 차트 객체는 만능 스텁이되 크로스헤어 콜백만 잡아 둔다(마커 hover 를 흉내).
const chartStub = new Proxy({ subscribeCrosshairMove(fn) { cross.push(fn); } },
                            { get(t, p) { return p in t ? t[p] : P; } });
global.LightweightCharts = new Proxy({ createChart() { return chartStub; } },
                                     { get(t, p) { return p in t ? t[p] : P; } });
global.localStorage = { getItem() { return null; }, setItem() {},
                        removeItem() {} };
global.requestAnimationFrame = (f) => setTimeout(f, 0);
global.setInterval = () => 0;
const fetched = [];
let releaseFull = null;
global.fetch = (url) => {
  fetched.push(url);
  const lite = url.indexOf('&lite=1') >= 0;
  const resp = { ok: true, status: 200,
                 json: () => Promise.resolve({ ok: true,
                                               chart: lite ? cfg.lite : cfg.full }) };
  if (lite) return Promise.resolve(resp);
  // 나머지 응답은 붙잡아 둔다 — lite 만 그린 중간 상태를 재야 하니까
  return new Promise((res, rej) => {
    releaseFull = () => (cfg.full_fails ? rej(new Error('HTTP 500')) : res(resp));
  });
};
function btn(k) {
  return { getAttribute: (n) => (n === 'data-ind' ? k : null),
           classList: { contains: (c) => c === 'chart-ind-btn' } };
}
const act = {
  release() { if (releaseFull) releaseFull(); },
  click(k) { clicks.forEach((f) => f({ target: btn(k) })); },
  hover(t) { const fn = cross[cross.length - 1];
             if (fn) fn({ time: t, point: { x: 5, y: 5 } }); },
};
const disc = () => document.getElementById('chart-disc');
const snap = () => ({ html: disc().innerHTML, hint: disc().getAttribute('data-hint') });
const out = { steps: {} };
try { eval(src); } catch (e) { out.error = String((e && e.stack) || e); }
out.steps.start = snap();
const plan = (cfg.actions || ['release']).slice();
function next() {
  if (!plan.length) {
    out.steps.final = snap(); out.fetched = fetched;
    console.log(JSON.stringify(out)); return;
  }
  const [name, arg] = plan.shift().split(':');
  try { act[name](arg); } catch (e) { out.error = String((e && e.stack) || e); }
  setTimeout(next, 30);
}
setTimeout(() => { out.steps.lite = snap(); next(); }, 60);
"""

    _NOW = "DART_API_KEY 없음 — 공시 마커(DART 공시 목록)를 받지 못했습니다"
    _THEN = "수집 당시 " + _NOW

    def _run_chart(self, tmp_path, cfg):
        import json
        import shutil
        import subprocess

        import bot.dashboard as d
        node = shutil.which("node")
        if not node:
            pytest.skip("node 없음")
        (tmp_path / "chart.js").write_text(d._CHART_JS, encoding="utf-8")
        (tmp_path / "h.js").write_text(self._CHART_HARNESS, encoding="utf-8")
        (tmp_path / "cfg.json").write_text(
            json.dumps(dict(cfg, ticker="018260.KS"), ensure_ascii=False),
            encoding="utf-8")
        r = subprocess.run([node, str(tmp_path / "h.js"),
                            str(tmp_path / "chart.js"),
                            str(tmp_path / "cfg.json")],
                           capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout)
        assert not out.get("error"), out["error"]
        # 첫 응답은 lite, 둘째는 나머지 — 그 순서를 실제로 탔는지(#79)
        assert len(out["fetched"]) == 2, out["fetched"]
        assert "&lite=1" in out["fetched"][0], out["fetched"]
        assert "&lite=1" not in out["fetched"][1], out["fetched"]
        return out["steps"]

    @staticmethod
    def _payload(**kw):
        times = [f"2026-06-{i:02d}" for i in range(1, 29)]
        return dict({"times": times, "close": [100.0 + i for i in range(28)],
                     "ticker": "018260.KS", "currency": "KRW",
                     "decimals": 0}, **kw)

    def test_chart_live_lookup_shows_keyless_note(self, tmp_path):
        """/lookup: 빈 자리표시 → lite(공시 없음) → 나머지(빈 공시 + 사유).
        옛 판은 lite 가 채운 종전 안내가 끝까지 남았다(사유가 영영 안 닿음)."""
        st = self._run_chart(tmp_path, {
            "initial": {"times": [], "close": [], "ticker": "018260.KS"},
            "lite": self._payload(lite=True),
            "full": self._payload(events=[], events_note=self._NOW)})
        assert "마우스를 올리면" in st["lite"]["html"]     # 나머지가 오기 전엔 종전 안내
        assert self._NOW in st["final"]["html"], st["final"]
        assert "마우스를 올리면" not in st["final"]["html"]
        assert st["final"]["hint"] == "1"

    def test_chart_note_is_escaped(self, tmp_path):
        """사유는 원천 문자열이 아니라 우리 문구지만 화면에 넣을 땐 escape(#7)."""
        st = self._run_chart(tmp_path, {
            "initial": {"times": [], "close": [], "ticker": "018260.KS"},
            "lite": self._payload(lite=True),
            "full": self._payload(events=[], events_note="<b>키</b> & x")})
        assert "&lt;b&gt;키&lt;/b&gt; &amp; x" in st["final"]["html"]
        assert "<b>키</b>" not in st["final"]["html"]

    def test_chart_archive_note_replaced_once_key_is_back(self, tmp_path):
        """아카이브: 저장본의 '수집 당시' 사유 → 지금은 키가 있어 공시가 왔다
        → 종전 안내로 바뀐다(옛 판은 수집 당시 사유가 지금 사실처럼 남았다)."""
        st = self._run_chart(tmp_path, {
            "initial": self._payload(events=[], events_note=self._THEN),
            "lite": self._payload(lite=True),
            "full": self._payload(events=[{"time": "2026-06-03",
                                           "title": "x", "type": "order"}])})
        assert self._THEN in st["start"]["html"]
        # 공시를 안 실은 lite 는 아무것도 모른다 — 저장본 사유를 덮지 않는다
        assert self._THEN in st["lite"]["html"], st["lite"]
        assert "마우스를 올리면" in st["final"]["html"], st["final"]
        assert "수집 당시" not in st["final"]["html"]

    def test_chart_archive_still_keyless_says_now(self, tmp_path):
        st = self._run_chart(tmp_path, {
            "initial": self._payload(events=[], events_note=self._THEN),
            "lite": self._payload(lite=True),
            "full": self._payload(events=[], events_note=self._NOW)})
        assert self._NOW in st["final"]["html"]
        assert "수집 당시" not in st["final"]["html"], st["final"]

    def test_chart_rest_failure_keeps_archive_note(self, tmp_path):
        """나머지 응답이 실패하면 지금 상태를 모른다 — 저장본 사유(과거형)가
        남는 게 정직하다(종전 안내로 덮으면 '공시가 없던 기간' 으로 읽힌다)."""
        st = self._run_chart(tmp_path, {
            "initial": self._payload(events=[], events_note=self._THEN),
            "lite": self._payload(lite=True),
            "full": self._payload(), "full_fails": True})
        assert self._THEN in st["final"]["html"], st["final"]

    def test_chart_event_detail_not_wiped_by_note(self, tmp_path):
        """사용자가 마커에 올려 그 날 공시를 띄운 패널은 안내로 덮지 않는다 —
        공시 토글 → 나머지 응답 → 마커 hover(실제 크로스헤어 콜백) → 다른
        지표 토글(재렌더). 그 재렌더가 공시를 띄운 패널을 안내로 되돌리면 안
        된다(showDisc 가 data-hint=0 을 찍어야 하는 이유)."""
        st = self._run_chart(tmp_path, {
            "initial": {"times": [], "close": [], "ticker": "018260.KS"},
            "lite": self._payload(lite=True),
            "full": self._payload(events=[{"time": "2026-06-03",
                                           "title": "단일판매공급계약",
                                           "type": "order"}]),
            "actions": ["click:events", "release", "hover:2026-06-03",
                        "click:rsi"]})
        assert "단일판매공급계약" in st["final"]["html"], st["final"]
        assert "마우스를 올리면" not in st["final"]["html"]
        assert st["final"]["hint"] == "0"

    # ── DART 공시 피드 페이지 ──────────────────────────────────────────
    def test_feed_page_says_no_key(self, monkeypatch):
        import bot.dart_feed as df
        from bot.dashboard import _render_dart_feed_page
        monkeypatch.setattr(df, "_dart_api_key", lambda: None)
        html = _render_dart_feed_page({})[0]
        assert ("DART_API_KEY 없음 — 새 공시를 받지 못했습니다 — 아래는 저장분"
                in html)

    def test_feed_page_with_key_is_quiet(self, monkeypatch):
        import bot.dart_feed as df
        from bot.dashboard import _render_dart_feed_page
        monkeypatch.setattr(df, "_dart_api_key", lambda: _KEY)
        assert "DART_API_KEY" not in _render_dart_feed_page({})[0]


# ── 리뷰 F7: '키 없음' 기록은 칸마다 ────────────────────────────────────
class TestKeylessScope:
    """스냅샷 전체에 표식 하나를 두면, 렌더가 공시 탭 하나를 키 없이 물은
    사실이 키로 받아 **정말 빈** K-IFRS 칸까지 '수집 당시 키 없음' 으로
    만든다(실수 #429 리뷰 F7 — 리뷰어 재현: 키로 모은 스냅샷 + 키 없는 렌더
    프로세스 → 기업 탭이 "수집 당시 DART_API_KEY 없음 — DART 재무제표를")."""

    # 칸 → (그 칸이 사유를 말하는 pane, 그 사유에만 있는 낱말)
    _SLOT = {"company": ("si-company", "대표자·설립일·주소·결산월·산업분류를"),
             "financials": ("si-company", "DART 재무제표를"),
             "insiders": ("si-holders", "임원·주요주주 지분"),
             "disclosures": ("si-disclosures", "공시 목록을")}

    @pytest.mark.parametrize("section", sorted(_SLOT))
    def test_each_section_speaks_only_for_itself(self, keyless_dart,
                                                 monkeypatch, section):
        """한 칸만 적힌 기록 → 사유는 그 칸 자리에만. 나머지 셋은 비어 있어도
        말하지 않는다(그 칸들을 키 없이 물었다는 기록이 없다, #165).
        배치 렌더라 라이브 표(최대주주·계열회사)는 묻지 않는다."""
        import bot.dart_client as dc
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        kr: dict = {}
        dc.mark_keyless(kr, section)
        parts = _panes({"currency": "KRW", "kr": kr})
        for other, (pane, word) in self._SLOT.items():
            said = word in _pane(parts, pane)
            assert said is (other == section), (section, other, pane)
        assert parts["other_panes"].count("DART_API_KEY 없음") == 1

    def test_keyless_render_does_not_blame_keyed_sections(self, keyless_dart,
                                                          monkeypatch):
        """리뷰어 재현 그대로 — 키로 모은 스냅샷(법인 정보 있음 · K-IFRS 는 정말
        빔 · 공시 아직 없음)을 **키 없는** 프로세스가 렌더한다. 공시 탭만
        '지금 키 없음' 이고, K-IFRS 자리는 아무 말도 하지 않는다."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        si = {"currency": "KRW", "kr": {"ceo": "홍길동", "flow": {"x": 1}}}
        d._ensure_detail_enrichment("018260.KS", si)
        parts = _panes(si)
        assert "DART_API_KEY" not in _pane(parts, "si-company"), \
            "키로 모아 정말 빈 K-IFRS 칸에 키 탓을 지어냈다"
        assert "DART_API_KEY" not in _pane(parts, "si-holders")
        disc = _pane(parts, "si-disclosures")
        assert "DART_API_KEY 없음 — 공시 목록을 받지 못했습니다" in disc, disc
        assert "수집 당시" not in disc, "이 렌더가 방금 물은 것을 '수집 당시' 라 했다"

    def test_keyed_render_clears_the_sections_it_asked(self, keyed_dart,
                                                       monkeypatch):
        """키 없이 모은 저장본(네 칸 전부 기록)을 **키 있는** 프로세스가 렌더 —
        다시 물은 칸(공시·재무)은 기록을 지우고, 다시 묻지 않는 칸(법인 정보·
        임원 지분)은 '수집 당시' 그대로 남는다(둘 다 사실이다)."""
        import bot.dart_client as dc
        import bot.dashboard as d
        import bot.stock_snapshot as ss
        monkeypatch.setattr(d, "_BATCH_REGEN", False)
        monkeypatch.setattr(d, "_KR_FIN_KEYED_EMPTY", {})
        monkeypatch.setattr(ss, "collect_kr_financials", lambda t: {})
        si = {"currency": "KRW", "kr": _all_keyless()}
        d._ensure_detail_enrichment("018260.KS", si)
        assert {s: dc.keyless_when(si["kr"], s) for s in dc.KEYLESS_SECTIONS} \
            == {"company": "collection", "insiders": "collection",
                "disclosures": None, "financials": None}, si["kr"]
        parts = _panes(si)
        comp = _pane(parts, "si-company")
        assert ("수집 당시 DART_API_KEY 없음 — 대표자·설립일·주소·결산월·"
                "산업분류를") in comp, comp
        assert "DART 재무제표를" not in comp
        assert ("수집 당시 DART_API_KEY 없음 — 임원·주요주주 지분 표를"
                in _pane(parts, "si-holders"))
        assert "DART_API_KEY" not in _pane(parts, "si-disclosures")

    def test_now_records_never_say_at_collection(self, keyless_dart,
                                                 monkeypatch):
        """기록의 시점을 **칸마다** 따른다 — '지금' 기록은 어느 자리에서도
        '수집 당시' 라 하지 않는다(임원 지분 줄의 시점 갈래 포함)."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        parts = _panes({"currency": "KRW", "kr": _all_keyless("now")})
        for pane, word in self._SLOT.values():
            body = _pane(parts, pane)
            assert word in body, (pane, word)
        assert "수집 당시" not in parts["other_panes"]
        assert parts["other_panes"].count("DART_API_KEY 없음") == 4

    def test_record_helpers(self):
        import bot.dart_client as dc
        kr: dict = {"dart_keyless_company": True,          # 옛 꼴(배포된 적 없음)
                    "dart_keyless_insiders": "yesterday"}  # 모르는 시점
        assert dc.keyless_when(kr, "company") is None
        assert dc.keyless_when(kr, "insiders") is None
        assert dc.keyless_note(kr, "company", "x를") == ""
        assert dc.keyless_when(None, "financials") is None
        dc.mark_keyless(kr, "financials", "now")
        assert dc.keyless_note(kr, "financials", "x를") == dc.keyless_reason("x를")
        dc.mark_keyless(kr, "financials")
        assert dc.keyless_note(kr, "financials", "x를") == dc.keyless_reason(
            "x를", at_collection=True)
        dc.clear_keyless(kr, "financials")
        dc.clear_keyless(kr, "financials")                 # 두 번 지워도 된다
        assert dc.keyless_when(kr, "financials") is None
        with pytest.raises(ValueError):
            dc.mark_keyless(kr, "news")
        with pytest.raises(ValueError):
            dc.mark_keyless(kr, "company", "later")


# ── 리뷰 F3: 키로 다시 물어 빈손이면 기억한다 ──────────────────────────────
class TestKeylessRecollectMemo:
    """키 없이 만든 스냅샷의 빈 재무를 키가 생긴 뒤 다시 받는 길(#18)이 **렌더
    마다** 같은 13번 조회를 되풀이했다(리뷰어 실측 13→26→39). 저장본은 렌더
    마다 디스크에서 새로 읽혀 dict 에 적은 표시는 다음 렌더에 남지 않는다 —
    그래서 이 테스트도 **렌더마다 저장본 사본**을 새로 넘긴다(같은 dict 를
    재사용하면 dict 에만 적는 고침도 통과한다, #35·#155)."""

    _STORED = {"kr": {"dart_keyless_financials": "collection",
                      "disclosures": [{"title": "x"}], "flow": {"x": 1}}}

    @pytest.fixture
    def calls(self, keyed_dart, monkeypatch):
        """원천 쪽 스텁(#348) — 수집기 함수를 통째로 바꾸면 그 안의 비용을 못 본다."""
        import bot.dart_client as dc
        import bot.dart_quarterly as dq
        import bot.dashboard as d
        import bot.naver_finance_client as nf
        got = {"fin": 0, "q": 0, "extra": 0, "fin_value": None}
        monkeypatch.setattr(d, "_KR_FIN_KEYED_EMPTY", {})

        def gnf(self, ticker, year=None, fs_div="CFS", reprt_code="11011"):
            got["fin"] += 1
            return got["fin_value"]

        def bump(key):
            def f(*a, **k):
                got[key] += 1
                return [] if key == "q" else None
            return f
        monkeypatch.setattr(dc.DartClient, "get_normalized_financials", gnf)
        monkeypatch.setattr(dq, "get_quarterly_series", bump("q"))
        monkeypatch.setattr(dc.DartClient, "get_share_totals", bump("extra"))
        monkeypatch.setattr(dc.DartClient, "get_share_totals_series",
                            bump("extra"))
        monkeypatch.setattr(nf, "get_naver_valuation", bump("extra"))
        return got

    def _render(self, ticker="456789.KQ"):
        import copy

        import bot.dashboard as d
        si = copy.deepcopy(self._STORED)      # _load_stored_stock_info 처럼 사본
        d._ensure_detail_enrichment(ticker, si)
        return si

    @staticmethod
    def _cost(c):
        return (c["fin"], c["q"], c["extra"])

    def test_keyed_empty_is_asked_once_across_renders(self, calls):
        si = self._render()
        first = self._cost(calls)
        assert first[0] >= 1 and first[1] == 1, first
        assert first[2] == 0, "재무가 없는데 선행 PER·BPS 재료를 받으러 갔다"
        assert "dart_keyless_financials" not in si["kr"], \
            "키로 다시 물어 빈 칸에 '키 없음' 기록이 남았다(F7)"
        for _ in range(2):
            si = self._render()
            assert "dart_keyless_financials" not in si["kr"]
        assert self._cost(calls) == first, \
            "렌더마다 같은 조회를 되풀이했다(리뷰어 실측 13→26→39)"

    def test_memo_is_bounded_in_time(self, calls):
        """영구로는 믿지 않는다 — 원천의 '없다' 와 수신 실패가 같은 None 이다.
        크기도 못박는다: 상수를 그대로 읽어 비교하면 1초로 줄여도 통과한다(#66)."""
        import time

        import bot.dashboard as d
        assert d._KR_FIN_KEYED_EMPTY_TTL == 6 * 3600
        d._KR_FIN_KEYED_EMPTY["456789.KQ"] = (
            time.time() - d._KR_FIN_KEYED_EMPTY_TTL + 60)
        self._render()
        assert self._cost(calls) == (0, 0, 0), "기억이 살아 있는데 다시 물었다"
        d._KR_FIN_KEYED_EMPTY["456789.KQ"] = (
            time.time() - d._KR_FIN_KEYED_EMPTY_TTL - 1)
        self._render()
        assert calls["fin"] >= 1, "기억이 지났는데 다시 묻지 않았다"

    def test_memo_is_per_ticker(self, calls):
        import time

        import bot.dashboard as d
        d._KR_FIN_KEYED_EMPTY["018260.KS"] = time.time()
        self._render("456789.KQ")
        assert calls["fin"] >= 1, "다른 종목의 기억으로 이 종목을 건너뛰었다"

    def test_found_financials_are_not_remembered_as_empty(self, calls):
        """받았으면 기억하지 않는다 — 다음 렌더는 다시 받는다(원천 답은 DART
        디스크 캐시가 7일 들고 있어 네트워크로 가지 않는다)."""
        import bot.dashboard as d
        calls["fin_value"] = {"year": 2025, "fs_div": "CFS",
                              "financials": {"매출": 1.0e12}, "ratios": {}}
        si = self._render()
        assert si["kr"]["financials"]["매출"] == 1.0e12
        assert "dart_keyless_financials" not in si["kr"]
        assert d._KR_FIN_KEYED_EMPTY == {}
        n = calls["fin"]
        self._render()
        assert calls["fin"] > n

    def test_exception_is_not_remembered(self, calls, monkeypatch):
        """예외는 일시 장애일 수 있다 — 기억하지 않고 다음 렌더가 다시 묻는다."""
        import bot.dashboard as d
        import bot.stock_snapshot as ss
        tries: list = []

        def boom(t):
            tries.append(t)
            raise RuntimeError("일시 장애")
        monkeypatch.setattr(ss, "collect_kr_financials", boom)
        self._render()
        self._render()
        assert len(tries) == 2 and d._KR_FIN_KEYED_EMPTY == {}

    def test_keyed_empty_page_does_not_blame_the_key(self, calls, monkeypatch):
        """화면 끝까지 — 키로 다시 물어 빈 재무 자리는 키 탓을 하지 않는다
        (키 있는 신선 스냅샷이 빈 재무를 그리는 것과 같은 화면)."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True)
        for _ in range(2):                    # 두 번째는 기억으로 건너뛴 렌더
            si = self._render()
            si["currency"] = "KRW"
            comp = _pane(_panes(si, "456789.KQ"), "si-company")
            assert "DART_API_KEY" not in comp, comp


# ── 리뷰 F4: 원천이 답한 013·014 는 다른 프로세스도 읽는다 ──────────────────
class TestNoDocAcrossProcesses:
    """'013·014 는 원천의 답' 이 **그 답을 들은 프로세스**에서만 참이었다
    (실수 #429 리뷰 F4). 다른 프로세스(배포 재시작 뒤의 대시보드 등)는 디스크
    쿨다운 때문에 원천에 묻지 않고 None 만 받아 그걸 '답을 못 들었다' 로
    셌고, 멀쩡한 표에 '다른 접수본을 받지 못해…' 각주와 짧은 기록이 붙었다."""

    _014 = (b"<result><status>014</status>"
            b"<message>\xed\x8c\x8c\xec\x9d\xbc\xec\x9d\xb4 \xec\xa1\xb4\xec\x9e\xac"
            b"\xed\x95\x98\xec\xa7\x80 \xec\x95\x8a\xec\x8a\xb5\xeb\x8b\x88\xeb\x8b\xa4"
            b"</message></result>")

    @pytest.fixture
    def df(self, monkeypatch, tmp_path):
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_DOC_FAIL", tmp_path / "fail" / "doc_fail.json")
        for name in ("_DOC_NO_FILE", "_DOC_TEXT_MEM", "_DOC_BLOB_MEM",
                     "_DOC_TRUNC"):
            monkeypatch.setattr(df, name, {})
        return df

    def _hear_014_in_process_a(self, df, monkeypatch, rn="C014", *,
                               raw=True):
        """프로세스 A — 제품의 `_fetch_doc_text` 가 원천의 014 를 **실제로**
        듣는다(손으로 `_DOC_NO_FILE` 에 적지 않는다, #155)."""
        class _R:
            content, text, status_code = self._014, "", 200
        monkeypatch.setattr(df.requests, "get", lambda *a, **k: _R())
        assert df._fetch_doc_text(rn, _KEY, raw_markup=raw) is None
        assert df._DOC_NO_FILE == {rn: "014"}
        # 프로세스 B — 메모리는 비었고 원천에 다시 묻지도 않는다(쿨다운)
        df._DOC_NO_FILE.clear()
        monkeypatch.setattr(df.requests, "get", lambda *a, **k: pytest.fail(
            "쿨다운 중인데 원천에 다시 물었다"))

    def test_the_answer_is_written_with_the_cooldown(self, df, monkeypatch):
        import json
        self._hear_014_in_process_a(df, monkeypatch)
        ent = json.loads(df._DOC_FAIL.read_text(encoding="utf-8"))["C014"]
        assert ent[1:] == [df._parser_sig(), "014"], ent

    def test_other_process_reads_the_answer(self, df, monkeypatch):
        self._hear_014_in_process_a(df, monkeypatch)
        assert df._fetch_doc_text("C014", _KEY, raw_markup=True) is None
        assert df.source_has_no_document("C014") is True
        assert df.no_document_code("C014") == "014"
        assert df.no_document_detail("C014") == df.no_document_reason_line("014")

    def test_only_answers_count(self, df, monkeypatch):
        """반대 증거 셋 — 코드 없는 실패 쿨다운(네트워크) · 만료된 답 · 옛 파서의
        답은 '원천이 답했다' 가 아니다(만료·옛 지문이면 다음 조회가 다시 묻는다)."""
        import json
        import time
        df._doc_fail_mark("NET")                                 # 코드 없음
        df._doc_fail_mark("OLD", code="014")
        df._doc_fail_mark("SIG", code="014")
        df._doc_fail_mark("ODD", code="020")                     # 미제공 코드 아님
        d = json.loads(df._DOC_FAIL.read_text(encoding="utf-8"))
        d["OLD"][0] = time.time() - 1
        d["SIG"][1] = "oldsig"
        df._DOC_FAIL.write_text(json.dumps(d), encoding="utf-8")
        d["RAW"] = [time.time() + 600, df._parser_sig(), "020"]   # 손으로 적힌 꼴
        df._DOC_FAIL.write_text(json.dumps(d), encoding="utf-8")
        for rn in ("NET", "OLD", "SIG", "ODD", "RAW"):
            assert df.source_has_no_document(rn) is False, rn
        assert len(d["ODD"]) == 2, "미제공이 아닌 코드를 답으로 적었다"
        assert df._doc_fail_recent("RAW") and df._doc_fail_recent("NET"), \
            "쿨다운 판정까지 바뀌었다 — 코드를 읽는 것과 쿨다운은 다른 칸이다"

    def test_tables_rolling_in_the_other_process(self, df, monkeypatch,
                                                 tmp_path):
        """리뷰어 재현 그대로 — 최신 접수본(정정)은 원천이 014, 원본은 읽힌다.
        다른 프로세스에서도 각주가 안 붙고 24시간 기록(short 없음)으로 굳는다."""
        import json

        import bot.dart_client as dc
        import bot.dart_production as dp
        self._hear_014_in_process_a(df, monkeypatch)
        _fake_parsers(monkeypatch)
        _real_tables_cache(monkeypatch, tmp_path)
        df._DOC_TEXT_MEM[df._doc_cache_key("ORIG", df._DOC_TEXT_MAX, True)] = \
            "<P>PRODUCTS PRODUCTION</P>"
        df._DOC_TRUNC[df._doc_cache_key("ORIG", df._DOC_TEXT_MAX, True)] = False
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: dc.PeriodicReports(
                                [{"rcept_no": "C014"}, {"rcept_no": "ORIG"}]))
        monkeypatch.setattr(dc.DartClient, "find_periodic_report",
                            lambda self, *a: None)
        qs = [{"year": 2026, "reprt_code": "11012", "label": "26.2Q",
               "financials": {}}]
        got = dp.tables_rolling(dc.DartClient(_KEY), "012450.KS", qs)
        assert "stale_note" not in got["production"], got["production"]
        rec = json.loads(next(tmp_path.glob("dart_tables_*.json")).read_text(
            encoding="utf-8"))
        assert not rec.get("short"), rec

    def test_backlog_in_the_other_process(self, df, monkeypatch, tmp_path):
        """수주잔고 — 같은 걸음(#38). 다른 프로세스에서도 짧은 기록이 아니다."""
        import bot.dart_backlog as db
        import bot.dart_client as dc
        import bot.finviz_client as fc
        self._hear_014_in_process_a(df, monkeypatch, raw=False)
        monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
        df._DOC_TEXT_MEM[df._doc_cache_key("ORIG", df._DOC_TEXT_MAX_FULL,
                                           False)] = "BACKLOG original"
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: dc.PeriodicReports(
                                [{"rcept_no": "C014"}, {"rcept_no": "ORIG"}]))
        monkeypatch.setattr(dc.DartClient, "find_periodic_report",
                            lambda self, *a: None)
        monkeypatch.setattr(db, "parse_backlog", lambda t: (
            {"value": 1.0e12} if "BACKLOG" in (t or "") else None))
        monkeypatch.setattr(db, "drop_fixable", lambda *a, **k: None)
        writes: list = []
        monkeypatch.setattr(db, "_bl_cache_write",
                            lambda ck, v, why, short=False: writes.append(short))
        assert db.backlog_probe(dc.DartClient(_KEY), "005930.KS", 2026,
                                "11012") == (1.0e12, "정상")
        assert writes == [False], writes

    def test_feed_enrich_writes_the_answer_with_its_cooldown(self, df,
                                                             monkeypatch):
        """공시 피드 보강(`enrich_disclosures`)을 통째로 태운다 — 들은 014 는
        코드별 쿨다운(014 = 12시간)과 **코드와 함께** 디스크에 남아야 다른
        프로세스의 표 롤링·수주잔고가 읽는다. 코드를 빼고 덮으면 그 답이 사라진다."""
        import json
        import time

        class _R:
            content, text, status_code = self._014, "", 200
        monkeypatch.setattr(df.requests, "get", lambda *a, **k: _R())
        monkeypatch.setattr(df, "_dart_api_key", lambda: _KEY)
        monkeypatch.setattr(df, "load_all_archives", lambda days_back=5: {})
        monkeypatch.setattr(df, "is_parse_target", lambda it: True)
        monkeypatch.setattr(df, "_budget_today", lambda: 0)
        monkeypatch.setattr(df, "_budget_add", lambda n=1: 0)

        def extract(report_nm, rcept_no, corp_code, api_key):
            # 전용 파서가 원문을 부르는 그 자리 — 제품의 `_fetch_doc_text` 가 014 를 듣는다
            df._fetch_doc_text(rcept_no, api_key, raw_markup=True)
            return None
        monkeypatch.setattr(df, "_extract_detail", extract)
        item = {"rcept_no": "C014", "report_nm": "x", "corp_code": "1",
                "category": "기타"}
        df.enrich_disclosures([item])
        assert item["detail"] == [df.no_document_reason_line("014")], item
        ent = json.loads(df._DOC_FAIL.read_text(encoding="utf-8"))["C014"]
        assert ent[2:] == ["014"], ent
        assert ent[0] - time.time() > 11 * 3600, "014 의 12시간 쿨다운이 아니다"


# ── 리뷰 F6: 형제 진단도 '목록을 못 받음' 을 '원문 없음' 으로 적지 않는다 ────
class TestSiblingDiagnosticsReadListFailure:
    @pytest.fixture
    def wired(self, monkeypatch):
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.dart_quarterly as dq
        cli = dc.DartClient(_KEY)
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
        monkeypatch.setattr(dq, "get_quarterly_series", lambda *a, **k: [
            {"year": 2026, "reprt_code": "11012", "label": "26.2Q",
             "financials": {}, "quarter": 2, "fs_div": "CFS", "ratios": {}}])
        monkeypatch.setattr(df, "_DOC_NO_FILE", {})
        monkeypatch.setattr(dc.DartClient, "stock_code_to_name",
                            lambda self, c: "테스트")
        state = {"reps": dc.PeriodicReports(failed="목록 조회 실패(Timeout)")}
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: state["reps"])
        monkeypatch.setattr(df, "_fetch_doc_text", lambda *a, **k: None)
        monkeypatch.setattr(df, "doc_was_truncated", lambda *a, **k: False)
        return state

    def test_per_quarter(self, wired, capsys):
        import bot.dart_client as dc
        import bot.scripts.backlog_misses as bm
        bm.per_quarter("005930.KS")
        out = capsys.readouterr().out
        assert "목록 조회 실패(목록 조회 실패(Timeout))" in out, out
        assert "없다고 답했다" not in out
        wired["reps"] = dc.PeriodicReports()           # 원천이 답한 빈 목록
        bm.per_quarter("005930.KS")
        out = capsys.readouterr().out
        assert "원천이 이 분기 보고서가 없다고 답했다" in out, out
        assert "목록 조회 실패" not in out

    def test_per_quarter_candidate_marks(self, wired, capsys, monkeypatch):
        """후보마다 — 원천이 '없다' 고 답한 것과 못 받은 것을 가른다."""
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.scripts.backlog_misses as bm
        wired["reps"] = dc.PeriodicReports([{"rcept_no": "C"},
                                            {"rcept_no": "O"}])
        df._DOC_NO_FILE["C"] = "014"
        bm.per_quarter("005930.KS")
        out = capsys.readouterr().out
        assert "C  ✗원천 미제공(014)" in out, out
        assert "O  ✗못 받음(원천의 답 없음)" in out, out

    def test_sweep_row(self, wired):
        import bot.dart_client as dc
        import bot.scripts.backlog_misses as bm
        rows = bm._one(dc.get_dart(), "005930")
        assert rows == [("26.2Q", None, "목록조회실패:목록 조회 실패(Timeout)")]
        wired["reps"] = dc.PeriodicReports()
        assert bm._one(dc.get_dart(), "005930")[0][2] == "원문미제공"

    def test_explain(self, wired, capsys):
        import bot.dart_client as dc
        import bot.scripts.backlog_misses as bm
        bm.explain("005930.KS")
        out = capsys.readouterr().out
        assert "26.2Q — 목록 조회 실패(목록 조회 실패(Timeout))" in out, out
        wired["reps"] = dc.PeriodicReports()
        bm.explain("005930.KS")
        assert "26.2Q — 원문 없음" in capsys.readouterr().out

    @pytest.mark.parametrize("reps_kind,want", [
        ("failed", "못받음"), ("answered", "원문미제공"), ("doc_unheard", "못받음"),
        ("doc_014", "원문미제공")])
    def test_production_probe_verdict(self, wired, capsys, monkeypatch,
                                      reps_kind, want):
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.scripts.production_format_probe as pf
        monkeypatch.setattr(pf, "_latest_quarters", lambda d, t: [
            {"year": 2026, "reprt_code": "11012", "label": "26.2Q",
             "financials": {}}])
        if reps_kind == "answered":
            wired["reps"] = dc.PeriodicReports()
        elif reps_kind in ("doc_unheard", "doc_014"):
            wired["reps"] = dc.PeriodicReports([{"rcept_no": "R1"}])
            if reps_kind == "doc_014":
                df._DOC_NO_FILE["R1"] = "014"
        assert pf.main(["005930", "--skip-backlog"]) == 0
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if ln.startswith("판정 분포:"))
        assert f"'{want}': 1" in line, line
        assert ("답을 못 들은 곳" in out) is (want == "못받음"), out
