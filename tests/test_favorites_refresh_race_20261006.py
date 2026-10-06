"""관심종목 ✕(삭제)가 안 먹는다 — 가격 갱신이 지운 종목을 되살렸다(실수 #436).

사용자 2026-10-06: "여기 관심종목을 삭제하고 싶은데 또 갑자기 버튼이 안먹혀...
왜 이러는거야?" (SFA 행 · 219종목 · 저장일 2026-10-06 · 캡처의 ✕ 가 hover 상태 —
마우스가 버튼 위에 있었다는 데까지가 캡처의 사실이고, 클릭이 버튼에 닿았다는 것은
추론이다).

재현한 경로: 가격 갱신(`_compute_favorites_with_prices`)은 **시작할 때** 디스크
목록을 읽고, 종목마다 바깥 원천을 부른 뒤 **끝날 때** 그 목록을 통째로 `_FAV_CACHE`
에 올린다. 그 사이 ✕ 를 누르면 `_save` 가 캐시를 비우지만(2026-06-16 '휴지통 작동
안 함' 의 fix), 갱신이 끝나며 **삭제 전 목록**을 다시 올린다 — 지운 종목이 캐시
수명(3분)과 다음 갱신 시간 동안 되살아난다. 그 동안 ✕ 를 다시 누르면 디스크엔
이미 없으니 `remove_favorite` 가 False 를 주고 `_save` 를 안 불러 캐시도 안
비운다 → 화면 그대로 → "버튼이 안 먹는다". 화면 JS 는 응답을 버리고 무조건 다시
읽기만 해서 그 사실을 한 마디도 안 했다(#43·#82). 같은 경합이 **추가**(새 종목이
사라짐)·**순서 변경**(되돌아감)에도 있다.

⚠️ 그 경로가 운영에서 실제로 일어났는지는 **재지 않았다**(샌드박스는 VM 을 못
본다, #12) — 삭제 경로가 로그를 한 줄도 안 남겨 잴 재료가 없었다. 이 커밋이 그
로그를 심는다(`favorite_remove: … → 목록에 없음`).

처방은 별표(#344)와 같은 구조다(#119): 목록의 **구성·순서**와 저장 시점 값·별표는
**읽는 시점에 디스크 정본**에서 오고, 캐시 층(메모리 3분 · 디스크 스냅샷 6시간)은
같은 티커의 **파생값만** 덧입힌다. 그러면 어느 층이 낡아도 지운 종목은 안
돌아온다. 갱신이 끝난 뒤 담긴 종목은 값이 빈 채로 보이고 갱신을 다시 건다.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import types

import pytest


# ── 공용 ────────────────────────────────────────────────────────────────
def _mf(monkeypatch, tmp_path, rows):
    """목록 파일을 tmp 에만 두고 모듈 전역을 복원되게 갈아 끼운다(#30·#130).

    갱신 킥은 **기록만** 한다 — 실제 데몬을 띄우면 이 테스트가 끝난 뒤에도 살아
    스텁이 풀린 원천을 친다(#312).
    """
    import bot.market_favorites as mf
    p = tmp_path / "market_favorites.json"
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mf, "_FAVORITES_FILE", p)
    monkeypatch.setattr(mf, "_FAV_CACHE", None, raising=False)
    monkeypatch.setattr(mf, "_FAV_CACHE_TS", 0.0, raising=False)
    monkeypatch.setattr(mf, "_FAV_REFRESHING", False, raising=False)
    kicks: list = []
    monkeypatch.setattr(mf, "_kick_fav_refresh", lambda: kicks.append(1))
    return mf, kicks


class _Tk:
    """느린 원천은 `_naver_quote_for` 스텁이 흉내 낸다 — 여기는 값만(네트워크 0)."""

    def __init__(self, t):
        self.t = t

    @property
    def info(self):
        return {"forwardEps": 2.0, "marketCap": 1000}

    @property
    def calendar(self):
        return {}

    @property
    def earnings_dates(self):
        return None

    def history(self, *a, **k):
        return None


def _stub_sources(monkeypatch, mf, gate=None, price=10.0):
    """원천을 스텁한다. `gate` 를 주면 **첫 원천 호출에서 갱신을 멈춘다** — 멈춘
    동안 = 갱신이 디스크를 이미 읽었고 아직 캐시를 올리지 않은 창(사용자가 ✕ 를
    누른 순간)."""
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Tk))
    entered = threading.Event()

    def _quote(t):
        entered.set()
        if gate is not None and not gate.wait(10):
            raise RuntimeError("갱신이 풀리지 않았다")
        return {"price": price, "mcap": None, "name": None}

    monkeypatch.setattr(mf, "_naver_quote_for", _quote)
    monkeypatch.setattr(mf, "_resolve_kr_name", lambda t, fb: fb)
    return entered


def _during_refresh(monkeypatch, mf, action, price=10.0):
    """갱신이 디스크를 읽은 **뒤**·캐시를 올리기 **전**에 `action` 을 한다.

    시간 경합에 기대지 않는다(#128) — 갱신을 이벤트로 붙잡아 둔다.
    """
    gate = threading.Event()
    entered = _stub_sources(monkeypatch, mf, gate, price=price)
    th = threading.Thread(target=mf._compute_favorites_with_prices, daemon=True)
    th.start()
    assert entered.wait(10), "갱신이 원천까지 못 갔다 — 창을 못 만들었다"
    try:
        out = action()
    finally:
        gate.set()
        th.join(10)
    assert not th.is_alive(), "갱신이 안 끝났다"
    return out


def _tickers(rows):
    return [r["ticker"] for r in rows]


# ── 재현: 사용자가 본 증상 ───────────────────────────────────────────────
class TestDeleteSurvivesAnInflightRefresh:
    ROWS = [{"ticker": "AAA", "name": "A"}, {"ticker": "SFA", "name": "SFA"},
            {"ticker": "BBB", "name": "B"}]

    def test_deleted_row_does_not_come_back(self, monkeypatch, tmp_path):
        """갱신 도중 지운 종목이 갱신이 끝난 뒤 화면에 **돌아오지 않는다**."""
        mf, _k = _mf(monkeypatch, tmp_path, self.ROWS)
        assert _during_refresh(monkeypatch, mf,
                               lambda: mf.remove_favorite("SFA")) is True
        rows, _as_of = mf.favorites_rows_with_as_of()
        assert _tickers(rows) == ["AAA", "BBB"], "지운 종목이 되살아났다"
        # 값은 살아 있다 — 목록만 디스크에서, 값은 방금 끝난 갱신에서
        assert [r.get("current_price") for r in rows] == [10.0, 10.0], rows

    def test_second_click_is_not_a_silent_noop(self, monkeypatch, tmp_path):
        """옛 판에서 사용자가 본 '무반응' — 디스크엔 이미 없어 두 번째 ✕ 는
        False 를 받고 캐시를 안 비웠다. 이제 화면 자체에 그 행이 없다."""
        mf, _k = _mf(monkeypatch, tmp_path, self.ROWS)
        _during_refresh(monkeypatch, mf, lambda: mf.remove_favorite("SFA"))
        assert mf.remove_favorite("SFA") is False          # 디스크엔 이미 없다
        rows, _ = mf.favorites_rows_with_as_of()
        assert "SFA" not in _tickers(rows)

    def test_added_row_does_not_vanish(self, monkeypatch, tmp_path):
        """갱신 도중 **담은** 종목도 사라지지 않는다 — 값이 빈 채로 보이고 갱신을
        다시 건다(지어내지 않는다, #165)."""
        mf, kicks = _mf(monkeypatch, tmp_path, [{"ticker": "AAA", "name": "A"}])
        added = _during_refresh(monkeypatch, mf, lambda: mf.add_favorite("NEW"))
        assert added and added["ticker"] == "NEW"
        kicks.clear()
        rows, _ = mf.favorites_rows_with_as_of()
        assert _tickers(rows) == ["NEW", "AAA"], "담은 종목이 사라졌다"
        assert rows[0].get("current_price") is None, rows[0]
        assert rows[1].get("current_price") == 10.0, rows[1]
        assert kicks, "갱신 결과에 없는 종목이 있는데 갱신을 다시 걸지 않는다"

    def test_reorder_is_not_undone(self, monkeypatch, tmp_path):
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": t} for t in ("AAA", "BBB", "CCC")])
        assert _during_refresh(monkeypatch, mf,
                               lambda: mf.reorder_favorite("CCC", "top")) is True
        rows, _ = mf.favorites_rows_with_as_of()
        assert _tickers(rows) == ["CCC", "AAA", "BBB"], "순서 변경이 되돌아갔다"


# ── 구조: 목록은 디스크, 캐시는 파생값만 ───────────────────────────────────
class TestDiskIsCanonicalAtRead:
    """`overlay_derived` 의 계약과 `get_favorites_with_prices` 배선 — 어느 층(신선·
    스테일 메모리 캐시·디스크 스냅샷)에서 값을 받든 같은 규약이다(#38)."""

    def test_overlay_keeps_disk_order_and_membership(self):
        import bot.market_favorites as mf
        disk = [{"ticker": "B", "saved_price": 2.0}, {"ticker": "NEW", "per": 9.9},
                {"ticker": "A", "saved_price": 1.0, "starred": True}]
        cache = [{"ticker": "A", "current_price": 11.0, "saved_price": 0.5,
                  "starred": False},
                 {"ticker": "GONE", "current_price": 1.0},
                 {"ticker": "B", "current_price": 22.0}]
        rows, missing = mf.overlay_derived(disk, cache)
        assert [r["ticker"] for r in rows] == ["B", "NEW", "A"]   # 디스크 순서·구성
        assert missing == ["NEW"]
        by = {r["ticker"]: r for r in rows}
        assert by["A"]["current_price"] == 11.0 and by["B"]["current_price"] == 22.0
        # 정본 칸은 디스크가 이긴다 — 저장가격·별표
        assert by["A"]["saved_price"] == 1.0 and by["A"]["starred"] is True
        # 캐시에 없는 행은 콜드 — 담던 날의 PER 을 '현재' 로 내보내지 않는다
        assert by["NEW"]["per"] is None

    def test_overlay_does_not_mutate_inputs(self):
        """캐시가 들고 있는 dict 를 고치면 디스크 값이 캐시에 구워진다(#344)."""
        import bot.market_favorites as mf
        disk = [{"ticker": "A", "per": 3.0}]
        cache = [{"ticker": "A", "current_price": 1.0, "per": 5.0}]
        mf.overlay_derived(disk, cache)
        assert disk == [{"ticker": "A", "per": 3.0}]
        assert cache == [{"ticker": "A", "current_price": 1.0, "per": 5.0}]

    def test_readded_row_keeps_its_new_saved_values(self):
        """지웠다가 다시 담은 종목 — 디스크엔 새 `saved_*`, 캐시엔 옛 행이 있다.
        옛 판이면 옛 저장가격이 '저장대비' 를 틀리게 만든다."""
        import bot.market_favorites as mf
        disk = [{"ticker": "A", "saved_price": 200.0, "saved_date": "2026-10-06",
                 "market_cap": 7}]
        cache = [{"ticker": "A", "saved_price": 100.0, "saved_date": "2026-09-01",
                  "current_price": 210.0}]
        r = mf.overlay_derived(disk, cache)[0][0]
        assert (r["saved_price"], r["saved_date"]) == (200.0, "2026-10-06")
        assert r["current_price"] == 210.0
        # 캐시 행에 없는 휘발성 칸은 디스크의 담던 날 값이 아니라 빈칸
        assert r["market_cap"] is None

    def test_name_kr_falls_back_to_disk_when_cache_has_none(self):
        import bot.market_favorites as mf
        r = mf.overlay_derived([{"ticker": "A", "name_kr": "에이"}],
                               [{"ticker": "A", "name_kr": None}])[0][0]
        assert r["name_kr"] == "에이"
        r = mf.overlay_derived([{"ticker": "A", "name_kr": "A"}],
                               [{"ticker": "A", "name_kr": "에이"}])[0][0]
        assert r["name_kr"] == "에이"            # 갱신이 해석한 한글명이 이긴다

    def test_next_earnings_comes_from_the_refresh(self):
        """담던 날 디스크에 굳은 실적일(이미 지났을 수 있다)이 아니라 갱신이
        고친 값 — 옛 판(캐시 통째)과 같은 결과여야 한다."""
        import bot.market_favorites as mf
        r = mf.overlay_derived([{"ticker": "A", "next_earnings": "2026-07-01"}],
                               [{"ticker": "A", "next_earnings": None}])[0][0]
        assert r["next_earnings"] is None

    def test_match_ignores_case_and_whitespace(self):
        import bot.market_favorites as mf
        rows, missing = mf.overlay_derived([{"ticker": "sfa "}],
                                           [{"ticker": "SFA", "current_price": 3}])
        assert missing == [] and rows[0]["current_price"] == 3

    def test_fresh_cache_kicks_a_refresh_only_when_rows_are_missing(
            self, monkeypatch, tmp_path):
        """신선한 캐시면 갱신을 안 건다(야후 버스트 방지) — 단, 캐시에 없는 종목
        (갱신 뒤에 담은 것)이 있으면 건다. 안 걸면 그 종목은 다음 TTL 까지 값
        없이 머문다."""
        import time
        mf, kicks = _mf(monkeypatch, tmp_path, [{"ticker": "A"}])
        monkeypatch.setattr(mf, "_FAV_CACHE", [{"ticker": "A", "current_price": 1}])
        monkeypatch.setattr(mf, "_FAV_CACHE_TS", time.time())
        mf.get_favorites_with_prices()
        assert kicks == []
        mf._save([{"ticker": "NEW"}, {"ticker": "A"}], invalidate_cache=False)
        rows = mf.get_favorites_with_prices()
        assert [r["ticker"] for r in rows] == ["NEW", "A"] and kicks == [1]

    def test_stale_cache_is_reconciled_too(self, monkeypatch, tmp_path):
        """TTL 이 지난 캐시(스테일 즉시 반환 경로)도 같은 규약 — 옛 판은 이
        경로에서도 지운 종목을 돌려줬다."""
        import time
        mf, kicks = _mf(monkeypatch, tmp_path, [{"ticker": "A"}])
        monkeypatch.setattr(mf, "_FAV_CACHE", [{"ticker": "A", "current_price": 1},
                                               {"ticker": "GONE", "current_price": 2}])
        monkeypatch.setattr(mf, "_FAV_CACHE_TS", time.time() - 9999)
        rows = mf.get_favorites_with_prices()
        assert [r["ticker"] for r in rows] == ["A"] and kicks == [1]

    def test_snapshot_path_stamps_only_rows_it_actually_filled(
            self, monkeypatch, tmp_path):
        """재시작 직후(메모리 캐시 없음) 스냅샷 경로도 같은 규약 — 그리고
        기준시각은 스냅샷 값을 **받은** 행에만 붙는다. 빈 행에 시각을 붙이면 그
        빈칸이 그 시각에 확인한 '없음' 으로 읽힌다(#43·#165)."""
        import time
        mf, kicks = _mf(monkeypatch, tmp_path,
                        [{"ticker": "NEW"}, {"ticker": "A", "per": 7.0}])
        mf._snapshot_save([{"ticker": "A", "current_price": 5.0, "per": 2.5},
                           {"ticker": "GONE", "current_price": 1.0}], time.time())
        rows = mf.get_favorites_with_prices()
        assert [r["ticker"] for r in rows] == ["NEW", "A"]
        assert rows[1]["current_price"] == 5.0 and rows[1]["per"] == 2.5
        assert rows[1].get("as_of"), "받은 행에 기준시각이 없다"
        assert "as_of" not in rows[0], "값을 못 받은 행에 기준시각을 붙였다"
        assert kicks == [1]

    def test_every_field_the_refresh_writes_is_overlaid(self):
        """갱신 본문이 `f["…"] =` 로 만드는 칸은 **전부** `_DERIVED_FIELDS` 에 있다.

        덧입히기는 목록을 쓰므로(#24) 갱신이 새 칸을 만들면서 여기 안 적으면
        화면에서 그 칸만 조용히 빈다 — 옛 판(캐시 통째)에선 없던 실패다.
        """
        import bot.market_favorites as mf
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        assert _refresh_written_keys(src) - set(mf._DERIVED_FIELDS) == set()
        # 대조 0건은 통과가 아니다(#54) — 실제로 칸을 찾고 있어야 한다
        assert {"current_price", "per", "next_earnings", "name_kr"} <= \
            _refresh_written_keys(src)

    def test_written_key_guard_fires_on_a_new_field(self):
        """반대 증거(#25) — 갱신에 새 칸을 더한 소스를 태우면 걸린다. 판정은 위
        테스트와 **같은 함수**다(#286 인라인 재구현 금지)."""
        import bot.market_favorites as mf
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        anchor = 'f["current_price"] = price\n'
        assert src.count(anchor) == 1
        mutated = src.replace(anchor, anchor + '            f["shiny_new"] = 1\n')
        assert _refresh_written_keys(mutated) - set(mf._DERIVED_FIELDS) == {"shiny_new"}


class TestColdAndKeyWiring:
    """독립 리뷰 2026-10-06 — 헬퍼만 직접 부르는 테스트가 못 잡은 배선(#20)."""

    def test_cold_path_does_not_serve_saved_day_numbers_as_current(
            self, monkeypatch, tmp_path):
        """L1 — 메모리 캐시도 스냅샷도 없는 첫 로드는 **이름만**. 담던 날 디스크에
        굳은 PER·시총·현재가를 '현재' 로 내보내면 2026-08-20 감사가 잡은 그 사고다
        (`return list(disk)` 로 되돌리는 변형이 159개 테스트를 통과했다)."""
        mf, kicks = _mf(monkeypatch, tmp_path,
                        [{"ticker": "A", "per": 6.289547, "current_price": 100.0,
                          "market_cap": 7, "saved_price": 90.0}])
        rows = mf.get_favorites_with_prices()
        assert [r["ticker"] for r in rows] == ["A"]
        r = rows[0]
        assert (r["per"], r["current_price"], r["market_cap"]) == (None, None, None), r
        assert r["saved_price"] == 90.0          # 의도적 과거값은 남긴다
        assert kicks == [1], "콜드인데 갱신을 안 건다"

    def test_snapshot_absent_row_matches_like_the_screen(self, monkeypatch, tmp_path):
        """스냅샷에 없는 행 판정도 화면과 같은 키(`_tkey`) — 디스크 티커가 소문자면
        옛 비교로는 '받은 행' 으로 보여 빈 칸에 기준시각이 붙는다(생존 변형)."""
        import time
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "new "}, {"ticker": "A"}])
        mf._snapshot_save([{"ticker": "A", "current_price": 5.0}], time.time())
        rows = mf.get_favorites_with_prices()
        assert "as_of" not in rows[0], rows[0]
        assert rows[1].get("as_of"), rows[1]


    def test_duplicate_add_does_not_call_the_source(self, monkeypatch, tmp_path):
        """이미 담긴 종목(대소문자·공백만 다름)이면 바깥 원천을 부르기 **전에**
        멈춘다 — 락 안 재검사만 남기면 정합성은 같아도 중복 클릭마다 야후를 친다
        (생존 변형 '네트워크 전 중복검사 제거', #61)."""
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "SFA "}])
        asked = []

        class _Spy(_Tk):
            def __init__(self, t):
                asked.append(t)
                super().__init__(t)

        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Spy))
        assert mf.add_favorite("sfa") is None
        assert asked == [], asked


class TestStarKey:
    """L4 — 별표 응답의 정본 판정이 덧입히기와 같은 키(`_tkey`)."""

    def _post(self, payload):
        import io
        import bot.dashboard_server as ds

        class _Fake:
            def __init__(self, body):
                self.headers = {"Content-Length": str(len(body))}
                self.rfile = io.BytesIO(body)
                self.sent = None

            def _json_ok(self, obj):
                self.sent = obj

        f = _Fake(json.dumps(payload).encode("utf-8"))
        ds.DashboardHandler._handle_favorite_star(f)
        return f.sent

    def test_padded_disk_ticker_reports_the_written_state(self, monkeypatch, tmp_path):
        """디스크 `"SFA "`(공백 붙음)에 별을 켜면 디스크는 ★ — 응답도 ★ 여야 한다.
        옛 판(`.upper()` 비교)은 `starred: False` 를 줘 화면이 ☆ 로 되돌렸다."""
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "SFA "}, {"ticker": "AAA"}])
        out = self._post({"ticker": "SFA", "starred": True})
        assert out == {"ok": True, "changed": True, "starred": True}, out
        assert mf.is_starred("sfa") is True and mf.is_starred("AAA") is False
        # 같은 키로 화면 행에도 ★ 가 덧입혀진다(`_star_map` 의 strip 을 지우는
        # 변형이 살아남았던 자리)
        rows, _ = mf.favorites_rows_with_as_of()
        assert [bool(r.get("starred")) for r in rows] == [True, False], rows

    def test_unknown_ticker_is_not_reported_as_on(self, monkeypatch, tmp_path):
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "AAA", "starred": True}])
        assert self._post({"ticker": "ZZZ", "starred": True}) == {
            "ok": True, "changed": False, "starred": False}


class TestUnreadableListIsNotEmpty:
    """L5 — 목록 파일을 **못 읽은 것**은 **빈 것**이 아니다(#82·#43).

    옛 `_load` 는 둘 다 `[]` 로 접어 화면이 '저장한 종목이 없다' 를 그렸고, 그
    상태에서 담기·삭제·별표를 누르면 빈 목록 위에 써서 **파일을 덮었다**(남은 목록이
    통째로 사라진다). 이제 쓰기는 멈추고, 화면은 사유를 말하고, 갱신은 캐시를
    덮지 않는다.
    """
    GOOD = [{"ticker": "SFA"}, {"ticker": "AAA"}]

    def _broken(self, monkeypatch, tmp_path, raw=b'[{"ticker": "SFA"}, {"tic'):
        mf, kicks = _mf(monkeypatch, tmp_path, self.GOOD)
        mf._FAVORITES_FILE.write_bytes(raw)
        return mf, kicks

    @pytest.mark.parametrize("raw, frag", [
        (b'[{"ticker": "SFA"}, {"tic', "내용을 읽지 못했습니다(JSONDecodeError"),
        (b'{"ticker": "SFA"}', "목록이 아닙니다(dict)"),
        # 목록인데 종목이 아닌 항목 — 옛 문구 '목록 모양이 아닙니다(list)' 는
        # 목록을 두고 목록이 아니라고 말했다(델타 리뷰). 몇 번째인지 댄다.
        (b'["SFA", "AAA"]', "종목이 아닌 항목이 섞여 있습니다(1번째: str)"),
        (b'[{"ticker": "A"}, "B"]', "종목이 아닌 항목이 섞여 있습니다(2번째: str)"),
    ])
    def test_strict_load_names_the_branch(self, monkeypatch, tmp_path, raw, frag):
        """내용이 깨진 갈래는 '고치거나 되돌리라' — 처방은 갈래가 정한다(#82)."""
        mf, _k = self._broken(monkeypatch, tmp_path, raw)
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        r = ei.value.reason
        assert frag in r, r
        assert "쓰기를 멈췄습니다" in r and "고치거나 백업이 있으면 되돌리세요" in r, r

    @pytest.mark.parametrize("raw, frag", [
        (b'[{"ticker": "SFA"}, {"tic', "JSONDecodeError"),
        (b'{"ticker": "SFA"}', "목록이 아닙니다(dict)"),
        (b'[{"ticker": "A"}, "B"]', "2번째: str"),
    ])
    def test_lenient_warning_carries_the_reason(self, monkeypatch, tmp_path, caplog,
                                                raw, frag):
        """너그러운 읽기도 **어느 갈래인지** 경고에 적는다 — '빈 목록으로 읽는다'
        한 마디로는 무엇을 고칠지 모른다(#82). 그 경고는 화면 문장과 같은 사유다
        (갈래가 늘 때 두 곳이 갈라지지 않게, #38)."""
        import logging
        mf, _k = self._broken(monkeypatch, tmp_path, raw)
        caplog.set_level(logging.WARNING, logger="bot.market_favorites")
        assert mf._load() == []
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        msgs = [r.getMessage() for r in caplog.records]
        assert any(frag in m and ei.value.reason in m for m in msgs), msgs

    def test_bom_file_is_read(self, monkeypatch, tmp_path):
        """편집기가 붙인 BOM 은 '못 읽음' 이 아니다 — 그걸 깨진 파일로 보면 멀쩡한
        목록이 쓰기를 막는다(델타 리뷰). 우리 writer 는 BOM 을 안 쓴다."""
        mf, _k = _mf(monkeypatch, tmp_path, [])
        mf._FAVORITES_FILE.write_bytes(
            b"\xef\xbb\xbf" + json.dumps(self.GOOD).encode("utf-8"))
        assert mf._load(strict=True) == self.GOOD
        assert mf.remove_favorite("AAA") is True        # 쓰기도 막히지 않는다
        assert mf._load(strict=True) == [{"ticker": "SFA"}]

    def test_undecodable_bytes_are_a_content_problem(self, monkeypatch, tmp_path):
        """UTF-8 이 아닌 바이트는 **내용**이 깨진 것이다 — `read_text` 가 던지는
        UnicodeDecodeError 를 열기 갈래로 보내면 '데이터는 그대로일 수 있다 —
        권한·경로를 먼저' 라는 **틀린 처방**이 붙는다(배포전 셀프리뷰, #82 · #292
        틀린 라벨은 라벨이 없는 것보다 나쁘다). 쓰기는 막히고 파일은 그대로다."""
        mf, _k = _mf(monkeypatch, tmp_path, [])
        raw = b'[{"ticker": "SF\xff"}]'
        mf._FAVORITES_FILE.write_bytes(raw)
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        r = ei.value.reason
        assert r.startswith("관심종목 파일 내용을 읽지 못했습니다(UnicodeDecodeError"), r
        assert "되돌리세요" in r and "권한·경로" not in r, r
        assert mf._load() == []                         # 관대한 읽기는 빈 목록
        with pytest.raises(mf.FavoritesUnreadable):
            mf.remove_favorite("SFA")
        assert mf._FAVORITES_FILE.read_bytes() == raw   # 한 바이트도 안 바뀐다

    def test_permission_denied_is_unreadable_with_its_own_prescription(
            self, monkeypatch, tmp_path):
        """디렉터리 권한(EACCES)이면 `exists()` 조차 원시 PermissionError 를 던진다
        — 옛 판은 `exists()` 를 먼저 물어 이 판정을 건너뛰었다(델타 리뷰 L3b).
        처방도 다르다: 데이터는 멀쩡할 수 있어 '되돌리라' 는 최근 변경을 잃는
        처방이다(L6) — 권한·경로를 말한다. 그리고 서버 경로는 안 싣는다."""
        import errno
        import os
        from pathlib import Path as _P
        mf, _k = _mf(monkeypatch, tmp_path, self.GOOD)
        target = mf._FAVORITES_FILE
        real_exists, real_read = _P.exists, _P.read_text

        def _denied(path):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))

        def _exists(self, *a, **k):
            return _denied(self) if self == target else real_exists(self, *a, **k)

        def _read(self, *a, **k):
            return _denied(self) if self == target else real_read(self, *a, **k)

        monkeypatch.setattr(_P, "exists", _exists)
        monkeypatch.setattr(_P, "read_text", _read)
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        r = ei.value.reason
        assert r.startswith("관심종목 파일을 열지 못했습니다(PermissionError: "), r
        assert "권한·경로" in r and "되돌리세요" not in r, r
        assert str(tmp_path) not in r, r
        # 너그러운 자리(읽기 전용 소비자)는 던지지 않는다
        assert mf._load() == [] and mf.starred_tickers() == []

    def test_missing_file_is_empty_not_unreadable(self, monkeypatch, tmp_path):
        mf, _k = _mf(monkeypatch, tmp_path, [])
        mf._FAVORITES_FILE.unlink()
        assert mf._load(strict=True) == []

    def test_lenient_load_warns_instead_of_silently_emptying(
            self, monkeypatch, tmp_path, caplog):
        import logging
        mf, _k = self._broken(monkeypatch, tmp_path)
        caplog.set_level(logging.WARNING, logger="bot.market_favorites")
        assert mf._load() == []
        assert any("빈 목록으로 읽는다" in r.getMessage() for r in caplog.records), \
            [r.getMessage() for r in caplog.records]

    def test_os_error_reason_does_not_carry_the_server_path(self, monkeypatch, tmp_path):
        """화면에 갈 문장엔 사유만 — OSError 의 str 은 서버 경로를 싣는다."""
        import bot.market_favorites as mf
        d = tmp_path / "market_favorites.json"
        d.mkdir()
        monkeypatch.setattr(mf, "_FAVORITES_FILE", d)
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        assert "열지 못했습니다(IsADirectoryError" in ei.value.reason, ei.value.reason
        assert str(tmp_path) not in ei.value.reason, ei.value.reason

    def test_writers_refuse_and_leave_the_file_alone(self, monkeypatch, tmp_path):
        mf, _k = self._broken(monkeypatch, tmp_path)
        before = mf._FAVORITES_FILE.read_bytes()
        asked = []

        class _Spy(_Tk):
            def __init__(self, t):
                asked.append(t)
                super().__init__(t)

        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Spy))
        monkeypatch.setattr(mf, "_resolve_kr_name", lambda t, fb: fb)
        for call in (lambda: mf.remove_favorite("SFA"),
                     lambda: mf.set_favorite_star("SFA", True),
                     lambda: mf.reorder_favorite("AAA", "top"),
                     lambda: mf.add_favorite("NEW")):
            with pytest.raises(mf.FavoritesUnreadable):
                call()
        assert mf._FAVORITES_FILE.read_bytes() == before, "못 읽은 목록 위에 썼다"
        # 담기는 네트워크 **전**에 멈춘다 — 못 쓸 목록을 위해 바깥 원천을 부르는 건
        # 순손실이다(#61). 락 안 재검사(아래 테스트)만 남기는 변형이 여기서 걸린다.
        assert asked == [], asked

    def test_add_rechecks_inside_the_lock(self, monkeypatch, tmp_path):
        """담기는 네트워크 **전**에 한 번, 락 안에서 **다시** 읽는다 — 그 사이 파일이
        깨지면 두 번째 읽기가 막아야 한다(앞 검사만 strict 면 빈 목록 위에 쓴다)."""
        mf, _k = _mf(monkeypatch, tmp_path, self.GOOD)

        class _Breaks(_Tk):
            @property
            def info(self):
                mf._FAVORITES_FILE.write_bytes(b"{not json")
                return {"regularMarketPrice": 1.0, "currency": "USD"}

        monkeypatch.setitem(sys.modules, "yfinance",
                            types.SimpleNamespace(Ticker=_Breaks))
        monkeypatch.setattr(mf, "_resolve_kr_name", lambda t, fb: fb)
        with pytest.raises(mf.FavoritesUnreadable):
            mf.add_favorite("NEW")
        assert mf._FAVORITES_FILE.read_bytes() == b"{not json"

    def test_screen_get_says_why_instead_of_an_empty_list(self, monkeypatch, tmp_path):
        """신선 캐시가 있어도 — 옛 판은 `[]` 위에 덧입혀 ok:true 빈 목록이었고 화면은
        '⭐ 저장 버튼을 눌러주세요' 안내를 그렸다(리뷰 재현)."""
        import io
        import time
        import bot.dashboard_server as ds
        mf, _k = self._broken(monkeypatch, tmp_path)
        monkeypatch.setattr(mf, "_FAV_CACHE", [{"ticker": "SFA", "current_price": 1}])
        monkeypatch.setattr(mf, "_FAV_CACHE_TS", time.time())

        class _Fake:
            sent = None

            def _json_ok(self, obj):
                self.sent = obj

        f = _Fake()
        ds.DashboardHandler._handle_favorites_get(f)
        assert f.sent["ok"] is False and f.sent["favorites"] == [], f.sent
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        # 화면 문장 = 목록 읽기의 사유 그대로(사람 문장 — 클래스 이름 없이)
        assert f.sent["error"] == ei.value.reason, f.sent
        assert f.sent["error"].startswith("관심종목 파일 내용을 읽지 못했습니다"), f.sent

    def test_other_get_failures_are_named_too(self, monkeypatch):
        import bot.dashboard_server as ds
        import bot.market_favorites as mf

        def _boom():
            raise KeyError("ticker")
        monkeypatch.setattr(mf, "favorites_rows_with_as_of", _boom)

        class _Fake:
            sent = None

            def _json_ok(self, obj):
                self.sent = obj

        f = _Fake()
        ds.DashboardHandler._handle_favorites_get(f)
        assert f.sent == {"ok": False, "favorites": [], "error": "KeyError: 'ticker'"}

    def test_refresh_does_not_overwrite_cache_or_snapshot(self, monkeypatch, tmp_path):
        """갱신이 못 읽은 목록을 빈 목록으로 알고 진행하지 않는다 — 감사가 '0종목 ✅'
        를 찍지 않게 던지고, 캐시·스냅샷은 그대로 둔다."""
        import time
        mf, _k = self._broken(monkeypatch, tmp_path)
        cache = [{"ticker": "SFA", "current_price": 1}]
        monkeypatch.setattr(mf, "_FAV_CACHE", cache)
        ts = time.time() - 50
        monkeypatch.setattr(mf, "_FAV_CACHE_TS", ts)
        mf._snapshot_save(cache, ts)
        snap_before = mf._snap_path().read_bytes()
        with pytest.raises(mf.FavoritesUnreadable):
            mf._compute_favorites_with_prices()
        assert mf._FAV_CACHE is cache and mf._FAV_CACHE_TS == ts
        assert mf._snap_path().read_bytes() == snap_before


class TestSaveTmpIsPerWrite:
    """`_save` 의 tmp 이름 — 상수면 두 프로세스(봇의 5분 갱신 · 대시보드의 클릭)가
    같은 파일을 쓴다(독립 리뷰 (b) 선재 결함)."""

    def test_each_write_uses_its_own_tmp_and_leaves_none(self, monkeypatch, tmp_path):
        from pathlib import Path as _P
        import os
        mf, _k = _mf(monkeypatch, tmp_path, [])
        names = []
        real = _P.write_text

        def spy(self, *a, **k):
            if self.suffix == ".tmp":
                names.append(self.name)
            return real(self, *a, **k)

        monkeypatch.setattr(_P, "write_text", spy)
        mf._save([{"ticker": "A"}])
        mf._save([{"ticker": "B"}])
        assert len(names) == 2 and len(set(names)) == 2, names
        assert all(f".{os.getpid()}." in n for n in names), names
        assert not list(tmp_path.glob("*.tmp")), list(tmp_path.iterdir())
        assert [f["ticker"] for f in mf._load()] == ["B"]

    def test_failed_replace_cleans_up_and_keeps_the_old_list(self, monkeypatch, tmp_path):
        from pathlib import Path as _P
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "OLD"}])

        def boom(self, target):
            raise OSError("replace failed")

        monkeypatch.setattr(_P, "replace", boom)
        with pytest.raises(OSError):
            mf._save([{"ticker": "NEW"}])
        monkeypatch.undo()
        assert not list(tmp_path.glob("*.tmp")), list(tmp_path.iterdir())
        assert json.loads((tmp_path / "market_favorites.json").read_text("utf-8")) == [
            {"ticker": "OLD"}]


def _refresh_written_keys(src: str) -> set:
    """`_compute_favorites_with_prices` 본문이 `f["X"] = …` 로 쓰는 키 전부(AST)."""
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef)
              and n.name == "_compute_favorites_with_prices")
    keys = set()
    for node in ast.walk(fn):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign))
                   else [])
        for t in targets:
            for sub in ast.walk(t):
                if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                        and sub.value.id == "f" and isinstance(sub.slice, ast.Constant)
                        and isinstance(sub.slice.value, str)):
                    keys.add(sub.slice.value)
    return keys


# ── 서버: 삭제 응답의 갈래 + 로그 ────────────────────────────────────────
class TestRemoveEndpoint:
    def _post(self, payload):
        import io
        import bot.dashboard_server as ds

        class _Fake:
            def __init__(self, body):
                self.headers = {"Content-Length": str(len(body))}
                self.rfile = io.BytesIO(body)
                self.sent = None

            def _json_ok(self, obj):
                self.sent = obj

        f = _Fake(json.dumps(payload).encode("utf-8"))
        ds.DashboardHandler._handle_favorite_remove(f)
        return f.sent

    def test_removed_vs_not_found_vs_error(self, monkeypatch, tmp_path, caplog):
        """'목록에 없음' 은 **실패가 아니다**(error 없음 — 화면은 다시 읽기만 한다).
        진짜 실패만 `error` 를 싣는다. 그리고 갈래를 **로그로** 남긴다 — 옛 판은
        한 줄도 안 남겨 "✕ 가 안 먹는다" 를 잴 재료가 없었다(#82)."""
        import logging
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "SFA"}, {"ticker": "AAA"}])
        caplog.set_level(logging.INFO, logger="bot.dashboard_server")
        assert self._post({"ticker": "SFA"}) == {"ok": True}
        assert self._post({"ticker": "SFA"}) == {"ok": False, "reason": "not_found"}
        msgs = [r.getMessage() for r in caplog.records]
        assert "favorite_remove: 'SFA' → 삭제" in msgs, msgs
        assert "favorite_remove: 'SFA' → 목록에 없음" in msgs, msgs
        out = self._post({"ticker": ""})
        assert out["ok"] is False and out.get("error"), out

        def _boom(t):
            raise OSError("disk full")
        monkeypatch.setattr(mf, "remove_favorite", _boom)
        out = self._post({"ticker": "AAA"})
        # 사유 문장 규칙은 `error_text` 한 곳 — 종류와 내용(조회와 같다, #38)
        assert out == {"ok": False, "error": "OSError: disk full"}, out

    def test_concurrent_adds_of_one_ticker_store_it_once(self, monkeypatch, tmp_path):
        """두 탭이 같은 종목을 **동시에** 담아도 한 줄 — 네트워크 전 검사만으론
        둘 다 '없음' 을 본다. 락 안에서 **다시** 보는 검사가 막는다(#344). 비교는
        화면과 같은 키(`_tkey`)라 대소문자가 달라도 같은 종목이다."""
        import time
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "AAA"}])

        class _Slow(_Tk):
            @property
            def info(self):
                time.sleep(0.3)
                return {"regularMarketPrice": 1.0, "currency": "USD"}

        monkeypatch.setitem(sys.modules, "yfinance",
                            types.SimpleNamespace(Ticker=_Slow))
        monkeypatch.setattr(mf, "_resolve_kr_name", lambda t, fb: fb)
        got = []
        ths = [threading.Thread(target=lambda t=t: got.append(mf.add_favorite(t)))
               for t in ("NVDA", "nvda")]
        for th in ths:
            th.start()
        for th in ths:
            th.join(10)
        assert sum(1 for g in got if g) == 1, got
        keys = [mf._tkey(f["ticker"]) for f in mf._load()]
        assert keys.count("NVDA") == 1, keys

    def test_remove_matches_like_the_screen(self, monkeypatch, tmp_path):
        """화면이 그린 행은 지울 수 있어야 한다 — 비교 규약이 하나다(`_tkey`)."""
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "SFA "}, {"ticker": "aaa"}])
        assert mf.remove_favorite("sfa") is True
        assert mf.reorder_favorite("AAA", "top") is False      # 하나뿐 = 이동 없음
        assert mf.set_favorite_star(" AAA", True) is True
        assert [f["ticker"] for f in mf._load()] == ["aaa"]
        # 빈 키는 아무것도 안 건드린다(티커 없는 행을 통째로 지우지 않는다)
        mf._save([{"ticker": ""}, {"ticker": "B"}])
        assert mf.remove_favorite("  ") is False
        assert mf.set_favorite_star("", True) is False
        # ⚠️ "bottom" 이어야 가드를 잰다 — 빈 키 행이 맨 앞이라 "top" 은 가드
        # 없이도 '이동 없음' 이다(#91c 발화 경로가 있는 픽스처).
        assert mf.reorder_favorite("", "bottom") is False
        assert [f["ticker"] for f in mf._load()] == ["", "B"]


# ── 화면: 쓰기 응답을 읽는다(JS 를 제품 그대로 태운다) ─────────────────────
_JS_PRE = r"""
var __els = {};
function __mk(id){
  var o = {id:id,_t:'',value:'',checked:false,disabled:false,
    style:{},dataset:{},
    querySelectorAll:function(){return [];},
    querySelector:function(){return null;},
    addEventListener:function(){},
    closest:function(){return null;},
    getAttribute:function(){return '';},setAttribute:function(){}};
  Object.defineProperty(o,'textContent',{get:function(){return o._t;},set:function(v){o._t=(v==null?'':String(v));}});
  Object.defineProperty(o,'innerHTML',{get:function(){return o._t;},set:function(v){o._t=(v==null?'':String(v));}});
  return o;
}
function __attrs(s){ var d={},re=/data-([a-z]+)="([^"]*)"/g,m; while((m=re.exec(s))) d[m[1]]=m[2]; return d; }
function __parseTable(){
  var html = (__els['fav-body']||{})._t || '';
  if (html.indexOf('<tbody>') < 0) return null;
  var body = html.split('<tbody>')[1].split('</tbody>')[0];
  var rows = [], re=/<tr ([^>]*)>/g, m;
  while((m=re.exec(body))) rows.push({dataset:__attrs(m[1]), style:{display:''}});
  var head = (html.split('<thead>')[1]||'').split('</thead>')[0];
  var cells = [], hre=/<th([^>]*)>/g, h;
  while((h=hre.exec(head))) cells.push({dataset:__attrs(h[1]),
    addEventListener:function(){}, querySelector:function(){return null;}});
  return {tHead:{rows:[{cells:cells}]}, tBodies:[{rows:rows}]};
}
var document = {
  getElementById:function(id){
    if (id === 'fav-tbl') return __parseTable();
    if (!__els[id]) __els[id] = __mk(id);
    return __els[id];
  },
  querySelector:function(){return null;},
  hidden:false
};
var window = {};
var __calls = [], __alerts = [], __reqs = [];
var __replies = JSON.parse(process.argv[2]);
/* 원천 흉내 — URL 마다 응답 큐(마지막 것은 반복). net=브라우저 네트워크 오류,
   body 가 없으면 JSON 이 아닌 본문(r.json() 이 던진다). */
function fetch(url, opts){
  __calls.push(url);
  /* 요청 쪽도 기록한다 — URL 만 보면 본문을 비우거나 메서드를 바꾸는 변형이 통과한다
     (독립 리뷰 M1: 별표 `starred: !want` 는 서버가 '이미 그 상태' 로 답해 화면이 아무
     말 없이 그대로 — 이번에 고치려던 '무반응' 이 그대로 CI 를 통과했다). */
  __reqs.push({url: url, method: (opts && opts.method) || 'GET',
               body: (opts && opts.body != null) ? JSON.parse(opts.body) : null});
  var q = __replies[url];
  if (!q || !q.length) return Promise.reject(new Error('no reply for ' + url));
  var rep = q.length > 1 ? q.shift() : q[0];
  if (rep.net) return Promise.reject(new TypeError('Failed to fetch'));
  return Promise.resolve({status: rep.status || 200, json: function(){
    return ('body' in rep) ? Promise.resolve(rep.body)
                           : Promise.reject(new SyntaxError('Unexpected token <'));}});
}
function setTimeout(){} function setInterval(){}
function alert(m){ __alerts.push(String(m)); }
__els['fav-body'] = __mk('fav-body');
__els['fav-cnt'] = __mk('fav-cnt');
/* ✕ 버튼은 렌더된 HTML 에서 만든다 — 클릭 배선(리스너가 무엇을 넘기나)까지
   제품 그대로 탄다. 다른 선택자는 종전처럼 빈 목록. */
var __btns = {};
__els['fav-body'].querySelectorAll = function(sel){
  if (sel !== '.fav-del') return [];
  var out = [], re = /<button class="fav-del" data-ticker="([^"]*)"/g, m;
  while ((m = re.exec(__els['fav-body']._t))) {
    var b = {disabled:false, dataset:{ticker:m[1]}, _l:[],
             addEventListener:function(ev, fn){ this._l.push(fn); }};
    __btns[m[1]] = b; out.push(b);
  }
  return out;
};
"""

_JS_POST = r"""
var __btn = {disabled:false, dataset:{ticker:'SFA'}, isConnected:true,
             getAttribute:function(){return 'false';},
             closest:function(){return null;}};
var __scenario = process.argv[3];
setImmediate(function(){
  var __initial = __els['fav-body']._t;
  var __cntInitial = __els['fav-cnt']._t;
  __calls.length = 0; __alerts.length = 0; __reqs.length = 0;
  if (__scenario === 'remove') removeFav('SFA', __btn);
  else if (__scenario === 'reorder') reorderFav('SFA', 'top');
  else if (__scenario === 'star') toggleStar(__btn);
  else if (__scenario === 'reload') loadFavs();
  else if (__scenario === 'click') {
    /* 사용자가 SFA 행의 ✕ 를 누른다 — 리스너가 버튼을 넘겨야 잠긴다 */
    __btn = __btns['SFA'];
    __btn._l.forEach(function(fn){ fn(); });
  }
  var __during = __btn.disabled;
  setImmediate(function(){
    console.log(JSON.stringify({calls:__calls, reqs:__reqs, alerts:__alerts, during:__during,
      after:__btn.disabled, html:__els['fav-body']._t, initial:__initial,
      cnt_initial:__cntInitial, cnt:__els['fav-cnt']._t}));
  });
});
"""

_SFA = {"ticker": "SFA", "name": "SFA", "country": "US"}
_AAA = {"ticker": "AAA", "name": "AAA", "country": "US"}


def _favs(*rows):
    return {"body": {"ok": True, "favorites": list(rows), "as_of": {}}}


def _run_fav_js(replies: dict, scenario: str) -> dict:
    """관심종목 IIFE 를 **제품 그대로** 태운다(재구현 금지 — #277).

    `fetch` 가 응답을 실제로 돌려주는(Promise) 하네스다 — 형제 하네스
    (`TestFavoriteStar20260911._run_js`)의 fetch 는 영영 안 풀리는 thenable 이라
    쓰기 경로를 못 잰다. 그래서 그 경로가 응답을 버려도 아무 테스트가 몰랐다(#20).
    """
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node 없음 — JS 실행 검증 skip")
    from bot.dashboard import _render_market_page
    html = _render_market_page({})
    seg = html.split("/* ── Favorites CRUD ── */", 1)[1]
    mark = "setInterval(function() { if (!document.hidden) loadFavs(); }, 60000);"
    assert mark in seg, "관심종목 IIFE 끝 앵커가 바뀌었다 — 하네스 갱신 필요"
    body = seg[:seg.index(mark) + len(mark)].split("(function() {", 1)[1]
    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/fav_write_harness.js"
        with open(path, "w", encoding="utf-8") as f:
            f.write(_JS_PRE + body + _JS_POST)
        r = subprocess.run([node, path, json.dumps(replies, ensure_ascii=False),
                            scenario], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


class TestWriteRepliesAreRead:
    def test_successful_delete_reloads_and_stays_quiet(self):
        out = _run_fav_js({"api/favorites": [_favs(_SFA, _AAA), _favs(_AAA)],
                           "api/favorite_remove": [{"body": {"ok": True}}]}, "remove")
        assert out["calls"] == ["api/favorite_remove", "api/favorites"], out["calls"]
        # 요청 쪽도 잰다 — URL 만 보면 본문을 비우거나 메서드를 바꾸는 변형이
        # 통과한다(독립 리뷰 M1, #20·#91b).
        assert out["reqs"][0] == {"url": "api/favorite_remove", "method": "POST",
                                  "body": {"ticker": "SFA"}}, out["reqs"]
        assert out["alerts"] == [], out["alerts"]
        assert out["during"] is True, "요청 중 버튼을 안 잠갔다(두 번 눌린다)"
        assert 'data-ticker="SFA"' in out["initial"]
        assert 'data-ticker="SFA"' not in out["html"]

    def test_click_wiring_hands_the_button_over(self):
        """배선 — 헬퍼를 직접 부르는 테스트는 리스너가 버튼을 안 넘기는 변형을
        못 잡는다(#20). 렌더된 ✕ 를 실제로 눌러 잠기는지 본다."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA, _AAA), _favs(_AAA)],
                           "api/favorite_remove": [{"body": {"ok": True}}]}, "click")
        assert out["calls"] == ["api/favorite_remove", "api/favorites"], out["calls"]
        assert out["reqs"][0] == {"url": "api/favorite_remove", "method": "POST",
                                  "body": {"ticker": "SFA"}}, (
            "클릭이 그 행의 티커를 안 보낸다", out["reqs"])
        assert out["during"] is True, "클릭이 버튼을 안 넘겨 요청 중에 안 잠긴다"
        assert out["alerts"] == []

    def test_already_gone_is_not_a_failure(self):
        """다른 탭·앞선 클릭이 이미 지웠다 — 다시 읽은 목록에도 없으면 조용하다."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA, _AAA), _favs(_AAA)],
                           "api/favorite_remove": [{"body": {"ok": False,
                                                             "reason": "not_found"}}]},
                          "remove")
        assert out["alerts"] == [] and out["calls"][-1] == "api/favorites", out

    def test_row_still_there_after_reload_is_said_out_loud(self):
        """사용자가 본 그 '무반응' — 지웠다는데(혹은 없다는데) 다시 읽은 목록에
        그대로 있으면 화면이 **말한다**(#43). 버튼도 다시 풀어 준다."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA, _AAA)],
                           "api/favorite_remove": [{"body": {"ok": False,
                                                             "reason": "not_found"}}]},
                          "remove")
        assert len(out["alerts"]) == 1, out["alerts"]
        assert out["alerts"][0].startswith("삭제 실패 — ")
        assert "다시 읽은 목록에 그대로 있습니다" in out["alerts"][0]
        assert out["after"] is False, "실패했는데 버튼이 잠긴 채"

    @pytest.mark.parametrize("reply, want", [
        ({"body": {"ok": False, "error": "disk full"}}, "삭제 실패 — disk full"),
        ({"status": 401, "body": None}, "인증이 만료됐습니다"),
        ({"status": 403}, "인증이 만료됐습니다"),
        ({"status": 404}, "/api/favorite_remove"),
        ({"status": 500}, "서버가 HTTP 500 로 답했습니다"),
        # 4xx·5xx 인데 본문이 JSON — `error` 가 없으면 상태가 말한다, 있으면 그 사유
        ({"status": 502, "body": {"ok": True}}, "서버가 HTTP 502 로 답했습니다"),
        ({"status": 500, "body": {"ok": False, "error": "boom"}}, "삭제 실패 — boom"),
        ({"net": True}, "서버에 닿지 못했습니다"),
    ])
    def test_each_failure_branch_is_named(self, reply, want):
        """갈래마다 처방이 다르다 — 옛 판은 서버가 실패를 말해도 다시 읽기만 했고
        (무반응), 네트워크 오류만 '삭제 실패' 한 마디였다(#82)."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA, _AAA)],
                           "api/favorite_remove": [reply]}, "remove")
        assert len(out["alerts"]) == 1 and want in out["alerts"][0], out["alerts"]
        assert out["alerts"][0].startswith("삭제 실패 — ")
        assert out["calls"] == ["api/favorite_remove"], "실패했는데 다시 읽었다"
        assert out["after"] is False

    def test_404_names_http_status(self):
        out = _run_fav_js({"api/favorites": [_favs(_SFA)],
                           "api/favorite_remove": [{"status": 404}]}, "remove")
        assert "HTTP 404" in out["alerts"][0], out["alerts"]

    def test_reorder_no_change_is_quiet_but_failure_is_named(self):
        base = {"api/favorites": [_favs(_SFA, _AAA)]}
        out = _run_fav_js({**base, "api/favorite_reorder": [{"body": {"ok": False}}]},
                          "reorder")
        assert out["alerts"] == [] and out["calls"] == [
            "api/favorite_reorder", "api/favorites"], out
        assert out["reqs"][0] == {"url": "api/favorite_reorder", "method": "POST",
                                  "body": {"ticker": "SFA", "direction": "top"}}, out["reqs"]
        out = _run_fav_js({**base, "api/favorite_reorder": [
            {"body": {"ok": False, "error": "invalid direction"}}]}, "reorder")
        assert out["alerts"] == ["순서 변경 실패 — invalid direction"], out["alerts"]
        assert out["calls"] == ["api/favorite_reorder"]

    def test_star_shares_the_same_verdict(self):
        """별표도 **같은 판정**을 쓴다(#38) — 2026-09-12 의 404 사고 문구가 여기서
        이어진다(API 이름 · HTTP 404 · 재시작)."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA)],
                           "api/favorite_star": [{"status": 404}]}, "star")
        assert len(out["alerts"]) == 1, out["alerts"]
        a = out["alerts"][0]
        assert a.startswith("중요표시 변경 실패 — ")
        assert "/api/favorite_star" in a and "HTTP 404" in a and "재시작" in a, a
        out = _run_fav_js({"api/favorites": [_favs(_SFA)],
                           "api/favorite_star": [{"body": {"ok": False}}]}, "star")
        assert out["alerts"] == ["중요표시 변경 실패 — 서버가 처리하지 못했다고 답했습니다"]

    def test_star_success_sends_the_wanted_state_and_stays_quiet(self):
        """성공 경로 — 꺼진 별을 누르면 `starred: true` 를 보낸다. 옛 하네스는
        URL 만 기록해 `starred: !want` 를 보내는 변형이 통과했다: 서버가 '이미
        그 상태' 로 답하면 화면은 **아무 말 없이 그대로** — 이번에 고치려던
        '무반응' 그 자체다(독립 리뷰 M1)."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA)],
                           "api/favorite_star": [{"body": {"ok": True, "changed": True,
                                                           "starred": True}}]}, "star")
        assert out["reqs"] == [{"url": "api/favorite_star", "method": "POST",
                                "body": {"ticker": "SFA", "starred": True}}], out["reqs"]
        assert out["alerts"] == [], out["alerts"]
        assert out["during"] is True, "요청 중 별 버튼을 안 잠갔다"

    def test_server_reason_is_shown_and_escaped(self):
        """L5 — 서버가 실어 보낸 사유를 그대로 말한다(#82). 원천 문구가 섞일 수
        있으니 이스케이프한다."""
        out = _run_fav_js({"api/favorites": [{"body": {
            "ok": False, "favorites": [],
            "error": "관심종목 파일 내용을 읽지 못했습니다(JSONDecodeError: <b>x</b>)"}}]},
            "none")
        html = out["initial"]
        assert ("관심종목을 불러올 수 없습니다. — 관심종목 파일 내용을 읽지 못했습니다"
                in html), html
        assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html, html
        assert "저장 버튼을 눌러주세요" not in html

    @pytest.mark.parametrize("reply", [
        # 실서버 401 은 text/plain(Basic Auth 거절) — 본문이 JSON 이 아니다. 옛 판은
        # 여기서 `r.json()` 이 던져 공용 판정을 아예 안 탔고 '불러올 수 없습니다'
        # 한 마디였다(델타 리뷰 M2). 하네스가 실물 모양을 내야 그 경로를 잰다(#155).
        {"status": 401},
        {"status": 401, "body": None},
    ])
    def test_get_401_names_the_branch(self, reply):
        """목록 읽기도 쓰기와 **같은** 판정(`favReplyError`, #38) — 401 은 인증 만료."""
        out = _run_fav_js({"api/favorites": [reply]}, "none")
        assert "인증이 만료됐습니다" in out["initial"], out["initial"]

    def test_get_404_names_the_api_without_a_guessed_prescription(self):
        """목록 API 의 404 는 쓰기의 404 처방('옛 코드라 재시작')을 빌리지 않는다 —
        그 처방은 새 API 가 옛 프로세스에 없던 사건에서 왔고, 목록 API 는 오래된
        라우트라 원인을 모른다(#165 재지 않은 처방 금지). 본문 없는 HTML 404 모양."""
        out = _run_fav_js({"api/favorites": [{"status": 404}]}, "none")
        html = out["initial"]
        assert "서버가 목록 API(/api/favorites)에 HTTP 404 로 답했습니다." in html, html
        assert "재시작" not in html and "옛 코드" not in html, html

    def test_get_200_that_is_not_json_is_named(self):
        """200 인데 JSON 이 아니다(앞단의 로그인·오류 페이지) — 'HTTP 200 으로
        답했다' 는 성공처럼 읽힌다(#34). 쓰기도 같은 판정이다(#38)."""
        out = _run_fav_js({"api/favorites": [{"status": 200}]}, "none")
        assert "서버 응답을 읽지 못했습니다(JSON 아님 · HTTP 200)" in out["initial"], \
            out["initial"]
        out = _run_fav_js({"api/favorites": [_favs(_SFA)],
                           "api/favorite_remove": [{"status": 200}]}, "remove")
        assert out["alerts"] == [
            "삭제 실패 — 서버 응답을 읽지 못했습니다(JSON 아님 · HTTP 200)"], out["alerts"]

    def test_get_network_failure_names_no_server_reason(self):
        """네트워크 오류는 서버가 한 말이 없다 — 브라우저 메시지를 서버 사유처럼
        싣지 않는다(서버 사유는 `favServer` 표시가 있을 때만)."""
        out = _run_fav_js({"api/favorites": [{"net": True}]}, "none")
        assert out["initial"] == (
            '<div class="md-empty">관심종목을 불러올 수 없습니다.</div>'), out["initial"]

    def test_failed_reload_clears_the_old_count(self):
        """다시 읽기가 실패하면 옛 개수를 지운다 — 남기면 오류 문장 옆에 '2종목' 이
        그대로 보인다(델타 리뷰 L7)."""
        out = _run_fav_js({"api/favorites": [_favs(_SFA, _AAA),
                                             {"status": 500, "body": None}]}, "reload")
        assert out["cnt_initial"] == "2종목", out
        assert "서버가 HTTP 500 로 답했습니다" in out["html"], out["html"]
        assert out["cnt"] == "", out

    def test_get_5xx_with_a_json_body_is_not_drawn_as_a_list(self):
        """4xx·5xx 는 본문이 JSON(ok:true)이어도 성공이 아니다 — 쓰기의 ≥400 규칙과
        같다(#38). 상태를 안 보면 앞단이 준 본문을 목록으로 그린다."""
        out = _run_fav_js({"api/favorites": [{"status": 502,
                                              "body": {"ok": True, "favorites": [_SFA]}}]},
                          "none")
        assert "서버가 HTTP 502 로 답했습니다" in out["initial"], out["initial"]
        assert 'data-ticker="SFA"' not in out["initial"]

    def test_server_side_read_failure_is_not_drawn_as_an_empty_list(self):
        """서버가 목록을 못 읽었다(ok:false)를 '저장한 종목이 없다' 로 그리면
        거짓말이다(#43) — 옛 판은 `d.favorites || []` 로 빈 목록 안내를 그렸다."""
        out = _run_fav_js({"api/favorites": [{"body": {"ok": False, "favorites": []}}]},
                          "none")
        assert "관심종목을 불러올 수 없습니다" in out["initial"], out["initial"]
        assert "저장 버튼을 눌러주세요" not in out["initial"]
        # 반대 증거 — 진짜 빈 목록은 여전히 안내를 그린다(#25)
        out = _run_fav_js({"api/favorites": [_favs()]}, "none")
        assert "저장 버튼을 눌러주세요" in out["initial"], out["initial"]


# ── 델타 독립 리뷰(54b1f8c..60248b6) 반영분 ──────────────────────────────
def _handler(name, payload):
    """관심종목 API 핸들러 하나를 가짜 요청으로 태운다(형제 `_post` 와 같은 모양)."""
    import io
    import bot.dashboard_server as ds

    class _Fake:
        def __init__(self, body):
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)
            self.sent = None

        def _json_ok(self, obj):
            self.sent = obj

    f = _Fake(json.dumps(payload).encode("utf-8"))
    getattr(ds.DashboardHandler, name)(f)
    return f.sent


class TestErrorTextIsOneRule:
    """M3·L3 — 오류 문장 규칙이 자리마다 갈려 있었다(#38): 조회는 `reason`, 쓰기는
    `str(exc)` 를 실었고, `_save` 의 OSError 는 tmp 파일의 **서버 경로**까지 화면
    alert 로 나갔다. 이제 `market_favorites.error_text` 한 곳이 정하고 API 다섯과
    DART 공시 알림이 그것을 쓴다."""

    def test_error_text_branches(self):
        import errno
        import os
        import bot.market_favorites as mf
        assert mf.error_text(mf.FavoritesUnreadable("사람 문장")) == "사람 문장"
        e = PermissionError(errno.EACCES, os.strerror(errno.EACCES),
                            "/srv/x/market_favorites.123.4.tmp")
        assert mf.error_text(e) == f"PermissionError: {os.strerror(errno.EACCES)}"
        assert mf.error_text(OSError("disk full")) == "OSError: disk full"  # strerror 없음
        assert mf.error_text(KeyError("ticker")) == "KeyError: 'ticker'"
        assert len(mf.error_text(ValueError("x" * 1000))) == 300
        # `.reason` 덕타이핑 금지 — UnicodeDecodeError 의 reason 은 사람 문장이 아니다
        ude = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        assert mf.error_text(ude).startswith("UnicodeDecodeError: "), mf.error_text(ude)

    def test_handler_text_survives_a_missing_module(self, monkeypatch):
        """규칙 모듈을 못 부르면(ImportError) 종류와 내용만 — 판정이 죽어 원시 예외가
        핸들러 밖으로 새면 화면은 응답조차 못 받는다."""
        import bot.dashboard_server as ds
        monkeypatch.setitem(sys.modules, "bot.market_favorites", None)
        assert ds._fav_error_text(KeyError("x")) == "KeyError: 'x'"

    @pytest.mark.parametrize("name, payload", [
        ("_handle_favorite_remove", {"ticker": "SFA"}),
        ("_handle_favorite_star", {"ticker": "SFA", "starred": True}),
        ("_handle_favorite_reorder", {"ticker": "AAA", "direction": "top"}),
        ("_handle_favorite_add", {"ticker": "NEW"}),
    ])
    def test_writes_on_an_unreadable_list_say_why_and_leave_the_file(
            self, monkeypatch, tmp_path, name, payload):
        """못 읽은 목록 위의 쓰기 — 화면 alert 가 **조회와 같은 사유**를 말하고 파일은
        그대로다. 옛 쓰기 응답은 `str(exc)` 라 조회와 문장이 갈렸다(델타 리뷰 M3)."""
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "SFA"}, {"ticker": "AAA"}])
        mf._FAVORITES_FILE.write_bytes(b'[{"ticker": "SFA"}, {"tic')
        before = mf._FAVORITES_FILE.read_bytes()
        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Tk))
        monkeypatch.setattr(mf, "_resolve_kr_name", lambda t, fb: fb)
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        out = _handler(name, payload)
        assert out == {"ok": False, "error": ei.value.reason}, out
        assert mf._FAVORITES_FILE.read_bytes() == before

    def test_save_failure_reaches_the_screen_without_the_server_path(
            self, monkeypatch, tmp_path):
        """진짜 쓰기 실패(디스크 가득) — `_save` 가 던진 OSError 는 tmp 의 서버 경로를
        싣는다. 화면엔 종류와 사유만 간다(인증 뒤라도 경로는 운영 로그로 충분하다)."""
        import errno
        import os
        from pathlib import Path as _P
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "SFA"}, {"ticker": "AAA"}])
        before = mf._FAVORITES_FILE.read_bytes()
        real = _P.write_text

        def _full(self, *a, **k):
            if self.suffix == ".tmp":
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(self))
            return real(self, *a, **k)

        monkeypatch.setattr(_P, "write_text", _full)
        out = _handler("_handle_favorite_remove", {"ticker": "SFA"})
        assert out == {"ok": False,
                       "error": f"OSError: {os.strerror(errno.ENOSPC)}"}, out
        assert str(tmp_path) not in out["error"]
        assert mf._FAVORITES_FILE.read_bytes() == before


class TestStarReachesEveryDuplicateRow:
    """L5 — 같은 키의 행이 둘이면 **전부** 고친다(`remove_favorite` 가 전부 지우는
    것과 같은 규약). 첫 행만 고치면 읽는 쪽(`_star_map` — 뒤 행이 이긴다)이 ☆ 를
    답해 화면이 되돌리고, 별표가 영영 안 먹는다(락 안 재검사 전의 동시 담기가
    남긴 중복 행)."""

    def test_star_and_unstar_reach_both_rows(self, monkeypatch, tmp_path):
        mf, _k = _mf(monkeypatch, tmp_path,
                     [{"ticker": "SFA"}, {"ticker": "AAA"}, {"ticker": "sfa "}])
        assert _handler("_handle_favorite_star", {"ticker": "SFA", "starred": True}) == {
            "ok": True, "changed": True, "starred": True}
        disk = mf._load(strict=True)
        assert [bool(f.get("starred")) for f in disk] == [True, False, True], disk
        assert _handler("_handle_favorite_star", {"ticker": "SFA", "starred": False}) == {
            "ok": True, "changed": True, "starred": False}
        assert not any(f.get("starred") for f in mf._load(strict=True))

    def test_unchanged_only_when_every_row_already_agrees(self, monkeypatch, tmp_path):
        mf, _k = _mf(monkeypatch, tmp_path,
                     [{"ticker": "SFA", "starred": True}, {"ticker": "SFA"}])
        assert mf.set_favorite_star("SFA", True) is True       # 한 행이 아직 ☆
        before = mf._FAVORITES_FILE.read_bytes()
        assert mf.set_favorite_star("SFA", True) is False      # 이제 전부 ★ — 안 쓴다
        assert mf._FAVORITES_FILE.read_bytes() == before


class TestCliNamesTheBranch:
    """L1 — 정리 CLI 가 목록이 아닌 JSON 을 '정상 JSON 인데 비어 있다' 로 찍었다.
    갈래는 `_load(strict=True)` 가 대고 CLI 는 그걸 옮긴다(분류기를 따로 두면
    `_load` 에 갈래가 늘 때 갈라진다, #38)."""

    @pytest.mark.parametrize("raw, frag", [
        (b'{"ticker": "SFA"}', "목록이 아닙니다(dict)"),
        (b'[{"tic', "JSONDecodeError"),
        (b'[{"ticker": "A"}, 1]', "2번째: int"),
    ])
    def test_unreadable(self, monkeypatch, tmp_path, capsys, raw, frag):
        mf, _k = _mf(monkeypatch, tmp_path, [])
        mf._FAVORITES_FILE.write_bytes(raw)
        assert mf._cli_sort_saved(True) == 1
        out = capsys.readouterr().out
        assert out.startswith("❌ ") and frag in out and "정상 JSON" not in out, out
        assert mf._FAVORITES_FILE.read_bytes() == raw          # 쓰지 않았다
        assert not list(tmp_path.glob("market_favorites.backup-*")), "백업까지 만들었다"

    def test_empty_and_missing_are_named_apart(self, monkeypatch, tmp_path, capsys):
        mf, _k = _mf(monkeypatch, tmp_path, [])
        assert mf._cli_sort_saved(False) == 1
        assert "목록이 비어 있다(정상 JSON)" in capsys.readouterr().out
        mf._FAVORITES_FILE.unlink()
        assert mf._cli_sort_saved(False) == 1
        assert "파일이 없다" in capsys.readouterr().out


class TestAuditCountsAnUnreadableList:
    """L4 — 감사가 목록 파일을 못 읽음을 ❌ 로 찍어야 일일 결산이 말한다(sweep 은 ❌
    줄만 센다). 옛 줄 '조회 실패 …' 는 결산에 안 실려 무음이었다(#303). 줄은 혼자서
    무엇인지 말한다(#356)."""

    def test_unreadable_list_is_one_finding(self, monkeypatch, tmp_path, capsys):
        import bot.scripts.board_audit as ba
        from bot import audit_sweep
        mf, _k = _mf(monkeypatch, tmp_path, [])
        _stub_sources(monkeypatch, mf)
        mf._FAVORITES_FILE.write_bytes(b'{"ticker": "SFA"}')
        ba._audit_favorites()
        out = capsys.readouterr().out
        hits = audit_sweep._findings(out)
        assert len(hits) == 1, (hits, out)
        assert "관심종목 —" in hits[0] and "목록이 아닙니다(dict)" in hits[0], hits

    def test_other_failures_stay_a_plain_line(self, monkeypatch, tmp_path, capsys):
        """반대 증거 — 일시 오류까지 ❌ 로 올리면 못 고칠 ❌ 가 매일 온다(#260)."""
        import bot.scripts.board_audit as ba
        from bot import audit_sweep
        mf, _k = _mf(monkeypatch, tmp_path, [])

        def _boom():
            raise KeyError("ticker")
        monkeypatch.setattr(mf, "_compute_favorites_with_prices", _boom)
        ba._audit_favorites()
        out = capsys.readouterr().out
        assert "조회 실패 KeyError" in out and audit_sweep._findings(out) == [], out


class TestDartAlertsSkipAnUnreadableList:
    """M1 — DART 공시 알림은 관심종목 목록으로 **상태를 고친다**(`codes`). 옛 판은
    못 읽은 목록을 빈 집합으로 접어 `codes` 를 [] 로 덮었고, 파일이 돌아오면 전
    종목을 '새로 담긴 종목' seed 로 삼아 **그 사이 공시를 영영 안 보냈다**."""

    def _setup(self, monkeypatch, tmp_path, archives):
        import bot.dart_fav_alerts as dfa
        import bot.dart_feed as df
        mf, _k = _mf(monkeypatch, tmp_path, [{"ticker": "005930.KS"}])
        monkeypatch.setattr(dfa, "_STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(dfa, "_FAV_WARNED", {})
        monkeypatch.setattr(dfa, "_LAST_FAV_ERROR", "")
        monkeypatch.setattr(df, "load_all_archives", lambda days_back=3: archives)
        return dfa, mf

    @staticmethod
    def _item(rno, td):
        return {"rcept_no": rno, "stock_code": "005930", "corp_name": "삼성전자",
                "report_nm": rno, "category": "실적", "url": "#", "date": td}

    @staticmethod
    def _today():
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")

    def test_unreadable_polls_leave_state_and_recovery_still_alerts(
            self, monkeypatch, tmp_path, caplog):
        import logging
        td = self._today()
        archives = {"d": [self._item("R1", td)]}
        dfa, mf = self._setup(monkeypatch, tmp_path, archives)
        assert dfa.enable(123)["codes"] == 1
        good = mf._FAVORITES_FILE.read_bytes()
        state_before = (tmp_path / "state.json").read_bytes()
        mf._FAVORITES_FILE.write_bytes(b'[{"ticker": "005930.KS"}, {"tic')
        caplog.set_level(logging.WARNING, logger="bot.dart_fav_alerts")
        archives["d"].append(self._item("R2", td))           # 못 읽는 동안 온 공시
        assert dfa.poll_new() == ([], 123)
        assert dfa.poll_new() == ([], 123)
        assert (tmp_path / "state.json").read_bytes() == state_before, \
            "못 읽은 폴이 상태를 고쳤다"
        warns = [r.getMessage() for r in caplog.records
                 if "이번 폴을 건너뛴다" in r.getMessage()]
        assert len(warns) == 1, warns                          # 같은 사유는 한 번만
        mf._FAVORITES_FILE.write_bytes(good)                   # 돌아오면 그 사이 공시를
        items, chat = dfa.poll_new()                           # 보낸다(옛 판은 seed 로
        assert [i["rcept_no"] for i in items] == ["R2"] and chat == 123, items  # 삼켰다)

    def test_enable_on_an_unreadable_list_does_not_turn_on(self, monkeypatch, tmp_path):
        dfa, mf = self._setup(monkeypatch, tmp_path, {})
        mf._FAVORITES_FILE.write_bytes(b'{"ticker": "005930.KS"}')
        with pytest.raises(mf.FavoritesUnreadable) as ei:
            mf._load(strict=True)
        # 사유는 화면(대시보드 API)과 같은 문장 — 클래스 이름이 앞에 붙지 않는다(#38)
        assert dfa.enable(123) == {"codes": 0, "seeded": 0, "error": ei.value.reason}
        assert not (tmp_path / "state.json").exists(), "켜지 않았는데 상태를 썼다"
        st = dfa.status()
        assert st["enabled"] is False and st["codes"] is None, st
        assert st["codes_error"] == ei.value.reason, st

    def test_status_keeps_the_chat_id_and_unknown_is_not_zero(self, monkeypatch, tmp_path):
        """결산·보고서가 `status()` 로 chat_id 를 꺼낸다 — 못 읽은 목록이 수신자를
        지우면 감사 알림까지 끊긴다. 개수는 0 이 아니라 '모름'(None)이다(#82)."""
        dfa, mf = self._setup(monkeypatch, tmp_path, {})
        dfa.enable(123)
        mf._FAVORITES_FILE.write_bytes(b'{"tic')
        st = dfa.status()
        assert st["chat_id"] == 123 and st["enabled"] is True, st
        assert st["codes"] is None and "JSONDecodeError" in st["codes_error"], st
        mf._FAVORITES_FILE.write_bytes(b"[]")                  # 반대 증거: 진짜 0
        st = dfa.status()
        assert st["codes"] == 0 and st["codes_error"] == "", st


class TestDartAlertCommandSaysWhy:
    """/dart_alert — 못 읽은 목록을 '0개 대상으로 켰다'·'대상 0개' 로 적으면
    거짓말이다(#43). 제품 경로 그대로(가짜 update 만) 태운다."""

    def _run(self, monkeypatch, args):
        import asyncio
        pytest.importorskip("telegram")
        # 봇 모듈은 import 시점에 토큰을 요구한다(형제 테스트와 같은 처리)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        import bot.telegram_bot as tb
        sent = []

        class _Msg:
            async def reply_text(self, text, **kw):
                sent.append(text)

        upd = types.SimpleNamespace(effective_message=_Msg(),
                                    effective_chat=types.SimpleNamespace(id=123))
        asyncio.run(tb.cmd_dart_alert(upd, types.SimpleNamespace(args=args)))
        return sent

    def test_on_and_status_name_the_reason(self, monkeypatch, tmp_path):
        import bot.dart_fav_alerts as dfa
        mf, _k = _mf(monkeypatch, tmp_path, [])
        monkeypatch.setattr(dfa, "_STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(dfa, "_FAV_WARNED", {})
        mf._FAVORITES_FILE.write_bytes(b'{"ticker": "005930.KS"}')
        sent = self._run(monkeypatch, ["on"])
        assert len(sent) == 1, sent
        assert sent[0].startswith("📋 관심종목 공시 알림을 켜지 못했습니다 — "
                                  "관심종목 파일이 목록이 아닙니다(dict)"), sent
        assert "ON" not in sent[0] and not (tmp_path / "state.json").exists()
        sent = self._run(monkeypatch, [])
        assert ("대상: KR 관심종목 확인 불가 — 관심종목 파일이 목록이 아닙니다(dict)"
                in sent[0]), sent
        mf._FAVORITES_FILE.write_bytes(b"[]")                  # 반대 증거: 진짜 0
        sent = self._run(monkeypatch, [])
        assert "대상: KR 관심종목 0개" in sent[0], sent
