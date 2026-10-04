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
        """빈손 + 키 없음일 때만 적는다 — 받은 게 있으면(키 없이 캐시로 답한 것
        포함, 실수 #428) 적지 않고, 키가 있는데 빈손이면 원천이 빈 것이다."""
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        cases = [(dc.DartClient(""), [], True), (dc.DartClient(""), None, True),
                 (dc.DartClient(""), [{"x": 1}], False),
                 (dc.DartClient(_KEY), [], False), (None, [], True)]
        for dart, got, want in cases:
            out: dict = {}
            ss._note_dart_keyless(out, dart, got)
            assert bool((out.get("kr") or {}).get("dart_keyless")) is want, \
                (dart and dart.api_key, got)

    def test_snapshot_collection_records_keyless(self, keyless_dart):
        """수집기를 통째로 태운다 — 헬퍼만 재면 배선을 떼는 변형을 못 잡는다
        (#20). 소켓은 루트 conftest 가 막아 다른 원천은 조용히 실패한다."""
        import bot.stock_snapshot as ss
        snap: dict = {}
        ss._enrich_kr("018260.KS", snap)
        assert (snap.get("kr") or {}).get("dart_keyless") is True, snap.get("kr")

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
        assert (snap.get("kr") or {}).get("dart_keyless") is True, task

    def test_keyed_collection_records_nothing(self, keyed_dart, monkeypatch):
        """반대 증거 — 키가 있으면 빈손이어도 적지 않는다(원천이 빈 것이다)."""
        import bot.stock_snapshot as ss
        monkeypatch.setattr(ss, "collect_kr_financials", lambda t: {})
        snap: dict = {}
        ss._enrich_kr("018260.KS", snap)
        assert "dart_keyless" not in (snap.get("kr") or {})

    def test_financials_collector_records_keyless(self, keyless_dart):
        import bot.stock_snapshot as ss
        assert ss.collect_kr_financials("018260.KS") == {
            "kr": {"dart_keyless": True}}

    # ── 종목 상세 화면 ─────────────────────────────────────────────────
    def test_live_keyless_page_says_why_in_every_empty_dart_section(
            self, keyless_dart, monkeypatch):
        """공시 탭 · 주주 탭 · 기업 탭(DART 법인 칸 · K-IFRS 재무 요약) 이 각자
        자리에서 사유를 말한다. 주주 탭은 **한 줄**로 모은다(#395)."""
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", False)
        parts = _panes({"currency": "KRW", "kr": {"dart_keyless": True}})
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
                             "kr": {"dart_keyless": True}}), "si-holders")
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
            "dart_keyless": True, "ceo": "홍길동",
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
            "dart_keyless": True,
            "financials": {"year": 2025, "fs_div": "CFS", "매출액": 1.0e12}}}),
            "si-company")
        assert "K-IFRS 재무 요약 FY2025" in comp, comp
        assert "DART 재무제표를" not in comp

    # ── 라이브 보강 ────────────────────────────────────────────────────
    def test_enrichment_records_keyless_disclosures(self, keyless_dart):
        import bot.dashboard as d
        si = {"kr": {}}
        d._ensure_detail_enrichment("018260.KS", si)
        assert si["kr"].get("dart_keyless") is True, si["kr"]

    def test_enrichment_keyed_empty_records_nothing(self, keyed_dart):
        import bot.dashboard as d
        si = {"kr": {}}
        d._ensure_detail_enrichment("018260.KS", si)
        assert "dart_keyless" not in si["kr"]

    def test_keyless_snapshot_financials_are_fetched_once_key_returns(
            self, keyed_dart, monkeypatch):
        """키 없이 만든 스냅샷의 빈 재무는 키가 생기면 다시 받는다 — 안 그러면
        그 빈칸이 영원히 남는다(#18). 기록이 없던 빈칸은 종전대로 안 받는다."""
        import bot.dashboard as d
        import bot.stock_snapshot as ss
        calls: list = []
        monkeypatch.setattr(ss, "collect_kr_financials", lambda t: (
            calls.append(t) or {"kr": {"financials": {"매출액": 1.0}}}))
        si = {"kr": {"dart_keyless": True}}
        d._ensure_detail_enrichment("018260.KS", si)
        assert calls == ["018260.KS"]
        assert si["kr"]["financials"] == {"매출액": 1.0}
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
        d._ensure_detail_enrichment("018260.KS", {"kr": {"dart_keyless": True}})
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

    def test_chart_hint_shows_the_note(self):
        """제품 JS 의 안내 줄을 **그대로** 잘라 node 로 돌린다(재구현 금지,
        #277) — 사유가 있으면 그걸, 없으면 종전 안내를."""
        import json
        import re
        import shutil
        import subprocess

        import bot.dashboard as d
        node = shutil.which("node")
        if not node:
            pytest.skip("node 없음")
        src = next(v for k, v in vars(d).items()
                   if isinstance(v, str) and "var DISC_HINT" in v)
        m = re.search(r"var DISC_HINT = .*?;\n", src, re.S)
        assert m, "안내 줄을 못 찾았다"
        prog = ("function escTt(s){return String(s).replace(/</g,'&lt;');}\n"
                "var out=[];[{events_note:'<b>키 없음</b>'},{}].forEach("
                "function(d){" + m.group(0) + "out.push(DISC_HINT);});"
                "console.log(JSON.stringify(out));")
        r = subprocess.run([node, "-e", prog], capture_output=True, text=True,
                           timeout=30)
        got = json.loads(r.stdout)
        assert "&lt;b>키 없음&lt;/b>" in got[0], got[0]
        assert "마우스를 올리면" not in got[0]
        assert "마우스를 올리면" in got[1]

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
