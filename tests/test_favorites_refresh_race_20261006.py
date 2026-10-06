"""관심종목 ✕(삭제)가 안 먹는다 — 가격 갱신이 지운 종목을 되살렸다(실수 #436).

사용자 2026-10-06: "여기 관심종목을 삭제하고 싶은데 또 갑자기 버튼이 안먹혀...
왜 이러는거야?" (SFA 행 · 219종목 · 저장일 2026-10-06 · 캡처의 ✕ 는 hover 상태라
클릭은 버튼에 닿았다).

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
        assert "favorite_remove: SFA → 삭제" in msgs, msgs
        assert "favorite_remove: SFA → 목록에 없음" in msgs, msgs
        out = self._post({"ticker": ""})
        assert out["ok"] is False and out.get("error"), out

        def _boom(t):
            raise OSError("disk full")
        monkeypatch.setattr(mf, "remove_favorite", _boom)
        out = self._post({"ticker": "AAA"})
        assert out == {"ok": False, "error": "disk full"}, out

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
var __calls = [], __alerts = [];
var __replies = JSON.parse(process.argv[2]);
/* 원천 흉내 — URL 마다 응답 큐(마지막 것은 반복). net=브라우저 네트워크 오류,
   body 가 없으면 JSON 이 아닌 본문(r.json() 이 던진다). */
function fetch(url, opts){
  __calls.push(url);
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
             getAttribute:function(){return 'false';}};
var __scenario = process.argv[3];
setImmediate(function(){
  var __initial = __els['fav-body']._t;
  __calls.length = 0; __alerts.length = 0;
  if (__scenario === 'remove') removeFav('SFA', __btn);
  else if (__scenario === 'reorder') reorderFav('SFA', 'top');
  else if (__scenario === 'star') toggleStar(__btn);
  else if (__scenario === 'click') {
    /* 사용자가 SFA 행의 ✕ 를 누른다 — 리스너가 버튼을 넘겨야 잠긴다 */
    __btn = __btns['SFA'];
    __btn._l.forEach(function(fn){ fn(); });
  }
  var __during = __btn.disabled;
  setImmediate(function(){
    console.log(JSON.stringify({calls:__calls, alerts:__alerts, during:__during,
      after:__btn.disabled, html:__els['fav-body']._t, initial:__initial}));
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
