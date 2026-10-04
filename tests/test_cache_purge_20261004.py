"""실수 #430 — `/api/quote`·`/api/chart` 디스크 캐시의 '다시 읽힐 길이 없는' 파일 정리.

두 캐시는 이름에 버전(렌더러·payload)을 싣는다. 버전이 오르거나 이름 규약이
바뀌면(#429 의 DART 키 꼬리) 옛 이름 파일은 어느 핸들러도 다시 열지 않는데 지우는
코드가 없어 종목마다 영영 쌓였다. 남기는 기준은 '핸들러가 그 파일을 아직 서빙할 수
있나' 하나다 — 그래서 회귀도 **핸들러와 정리 루틴을 같은 나이로 태워** 둘이 같은 답을
내는지 본다(값으로, #19·#20).
"""
from __future__ import annotations

import ast
import json
import logging
import os
import time
from pathlib import Path

import pytest

_KEY = "k-1234567890"
_NOW = 1_000_000_000.0


def _touch(p: Path, age: float, now: float = _NOW, body: dict | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body if body is not None else {}), encoding="utf-8")
    t = now - age
    os.utime(p, (t, t))
    return p


def _names(d: Path) -> set[str]:
    return {f.name for f in d.iterdir()} if d.is_dir() else set()


class TestQuotePurge:
    def test_orphans_go_live_files_stay(self, tmp_path):
        import bot.dashboard as d
        import bot.dashboard_server as ds
        rv = d._RENDER_VER
        light_ttl = ds._QUOTE_TTL["light"]
        q = tmp_path / "quote_cache"
        keep = {
            # FULL 은 만료돼도 stale 로 서빙된다(SWR) — 열흘 묵어도 산다
            f"005930_KS_full_v{rv}_k1r1s1f1.json": 10 * 86400,
            f"005930_KS_full_v{rv}_k0r0s0f0.json": 10 * 86400,
            f"005930_KS_full_v{rv}_kxr1sxf0.json": 10 * 86400,
            f"005930_KS_light_v{rv}_k1r0s0f0.json": light_ttl - 5,
        }
        gone = {
            f"005930_KS_full_v{rv - 1}_k1r0s0f0.json": 1,  # 옛 렌더러 버전
            f"005930_KS_full_v{rv}.json": 1,            # #429 이전 규약(키 꼬리 없음)
            f"005930_KS_full_v{rv}_k1.json": 1,         # #429 규약(DART 꼬리만, #430 이전)
            f"005930_KS_full_v1{rv}_k1r0s0f0.json": 1,  # 버전 접미가 겹치는 이름(앵커)
            f"005930_KS_full_v{rv}_k1r0s0f0z1.json": 1,  # 모르는 축이 붙은 꼬리
            f"005930_KS_full_v{rv}_k2r0s0f0.json": 1,   # 문법 밖 값
            f"000660_KS_light_v{rv}_k1r0s0f0.json": light_ttl + 5,  # 만료 LIGHT
        }
        for n, age in {**keep, **gone}.items():
            _touch(q / n, age)
        (q / "notes.txt").write_text("x")              # json 이 아니면 손대지 않는다
        res = ds._purge_dead_caches(tmp_path, now=_NOW)
        assert _names(q) == set(keep) | {"notes.txt"}
        assert res["quote_cache"] == (len(gone), len(keep), 0)

    @pytest.mark.parametrize("state", ["ready", "keyless", "raising"])
    @pytest.mark.parametrize("kind", ["full", "light"])
    def test_every_name_the_writer_makes_survives(self, tmp_path, monkeypatch,
                                                   state, kind):
        """'지금 이름' 은 정리 루틴이 따로 적은 패턴이 아니라 **이름을 만드는 함수가
        실제로 내는 이름**이어야 한다(#337) — 축마다 상태 셋 × 종류 둘 전부(축을
        하나씩 그 상태로, 나머지는 실제 술어로)."""
        import bot.dashboard_server as ds
        real = ds._quote_axis_ready
        for _letter, axis in ds._QUOTE_ENV_AXES:
            def fake(name, _axis=axis):
                if name != _axis:
                    return real(name)
                if state == "raising":
                    raise RuntimeError("boom")
                return state == "ready"
            monkeypatch.setattr(ds, "_quote_axis_ready", fake)
            name = ds._quote_cache_name("TST_KS", kind)
            assert ds._quote_parse(name) == ("TST_KS", kind,
                                             name.rsplit("_", 1)[1][:-5]), name
            _touch(tmp_path / "quote_cache" / name, 1)
            ds._purge_dead_caches(tmp_path, now=_NOW)
            assert (tmp_path / "quote_cache" / name).exists(), name


class TestChartPurge:
    def test_only_fresh_current_files_stay(self, tmp_path):
        import bot.dashboard_server as ds
        cv = ds._CHART_CACHE_VER
        ttl = ds._CHART_TTL
        c = tmp_path / "chart_cache"
        keep = {
            ds.chart_cache_name("005930_KS", "1d", "1y"): ttl - 5,
            ds.chart_cache_name("005930_KS", "1d", "1y", True): ttl - 5,
            ds.chart_cache_name("A", "1wk", "5y"): 1,
        }
        gone = {
            ds.chart_cache_name("000660_KS", "1d", "1y"): ttl + 5,   # 만료 — 안 읽힌다
            f"035420_KS_1d_1y_v{cv - 1}.json": 1,                    # 옛 payload 버전
            f"035720_KS_1d_1y_v1{cv}.json": 1,                       # 접미가 겹치는 이름
            f"035720_KS_1d_1y_v{cv - 1}_lite.json": 1,
        }
        for n, age in {**keep, **gone}.items():
            _touch(c / n, age)
        res = ds._purge_dead_caches(tmp_path, now=_NOW)
        assert _names(c) == set(keep)
        assert res["chart_cache"] == (len(gone), len(keep), 0)


class TestPurgeBoundaries:
    def test_missing_dirs_and_other_dirs(self, tmp_path):
        import bot.dashboard_server as ds
        assert ds._purge_dead_caches(tmp_path, now=_NOW) == {
            "quote_cache": (0, 0, 0), "chart_cache": (0, 0, 0)}
        other = _touch(tmp_path / "lookup_cache" / "x_full_v1.json", 1)
        arch = _touch(tmp_path / "archive" / "y_v1.json", 1)
        ds._purge_dead_caches(tmp_path, now=_NOW)
        assert other.exists() and arch.exists(), "다른 디렉터리는 손대지 않는다"

    def test_unlink_failure_is_counted_not_raised(self, tmp_path, monkeypatch):
        import bot.dashboard as d
        import bot.dashboard_server as ds
        rv = d._RENDER_VER
        bad = _touch(tmp_path / "quote_cache" / f"A_full_v{rv - 1}_k1.json", 1)
        ok = _touch(tmp_path / "quote_cache" / f"B_full_v{rv - 1}_k1.json", 1)
        real = Path.unlink

        def _unlink(self, *a, **k):
            if self.name == bad.name:
                raise PermissionError("locked")
            return real(self, *a, **k)
        monkeypatch.setattr(Path, "unlink", _unlink)
        res = ds._purge_dead_caches(tmp_path, now=_NOW)
        assert res["quote_cache"] == (1, 0, 1)
        assert bad.exists() and not ok.exists()

    def test_startup_wrapper_reports(self, tmp_path, monkeypatch, caplog):
        """결과를 남긴다 — 지운 수는 info, 지우지 못한 파일·정리 실패는 경고(#12)."""
        import bot.dashboard_server as ds
        caplog.set_level(logging.INFO, logger="bot.dashboard_server")
        monkeypatch.setattr(ds, "_purge_dead_caches", lambda root: {
            "quote_cache": (3, 4, 0), "chart_cache": (2, 1, 1)})
        monkeypatch.setattr(ds, "_owner_purges", lambda: (
            ("dart_tables", lambda: (lambda: (5, 6, 0))),))
        res = ds._startup_cache_purge(tmp_path)
        assert res == {"quote_cache": (3, 4, 0), "chart_cache": (2, 1, 1),
                       "dart_tables": (5, 6, 0)}
        msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
        assert any(lv == logging.INFO and "quote_cache 지움 3 · 남김 4" in m
                   and "dart_tables 지움 5 · 남김 6" in m for lv, m in msgs), msgs
        assert any(lv == logging.WARNING and "'chart_cache': 1" in m
                   for lv, m in msgs), msgs
        caplog.clear()

        def _boom(root):
            raise RuntimeError("disk gone")
        monkeypatch.setattr(ds, "_purge_dead_caches", _boom)
        assert ds._startup_cache_purge(tmp_path) == {"dart_tables": (5, 6, 0)}, \
            "한 캐시의 정리 실패가 다른 캐시의 정리를 막았다(#315)"
        assert any(r.levelno == logging.WARNING and "disk gone" in r.getMessage()
                   for r in caplog.records)
        caplog.clear()

        def _owner_boom():
            raise OSError("owner gone")
        monkeypatch.setattr(ds, "_owner_purges", lambda: (
            ("dart_tables", lambda: _owner_boom),
            ("dart_backlog", lambda: (lambda: (1, 0, 0)))))
        assert ds._startup_cache_purge(tmp_path) == {"dart_backlog": (1, 0, 0)}
        assert any(r.levelno == logging.WARNING and "dart_tables" in r.getMessage()
                   and "owner gone" in r.getMessage() for r in caplog.records)
        monkeypatch.setattr(ds, "_owner_purges", lambda: (
            ("dart_tables", lambda: _owner_boom),))
        assert ds._startup_cache_purge(tmp_path) is None, "전부 실패면 결과 없음"

    def test_owner_purges_are_the_dart_modules(self):
        """정리 규칙은 이름을 짓는 모듈 것이다 — 표시명과 실제 함수가 짝이 맞는다."""
        import bot.dart_backlog as bl
        import bot.dart_production as dp
        import bot.dashboard_server as ds
        got = {name: get() for name, get in ds._owner_purges()}
        assert got == {"dart_tables": dp.purge_dead_tables,
                       "dart_backlog": bl.purge_dead_backlog}

    def test_main_runs_it_before_serving(self):
        """배선 — main 이 서버를 만들기 **전에** 아카이브 부모 디렉터리로 부른다."""
        src = Path("bot/dashboard_server.py").read_text(encoding="utf-8")
        main = next(n for n in ast.parse(src).body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        purge = [c for c in ast.walk(main) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Name)
                 and c.func.id == "_startup_cache_purge"]
        assert len(purge) == 1, "main 이 정리를 부르지 않는다"
        assert ast.unparse(purge[0].args[0]) == "_ARCHIVE_ROOT.parent"
        server = [c for c in ast.walk(main) if isinstance(c, ast.Call)
                  and isinstance(c.func, ast.Name)
                  and c.func.id == "ThreadingHTTPServer"]
        assert server and purge[0].lineno < server[0].lineno


class TestHandlerAndPurgeAgree:
    """핸들러가 서빙하는 파일 = 정리 루틴이 남기는 파일. 같은 나이로 둘 다 태운다 —
    한쪽 수명만 바꾸면(리터럴로 되돌리기 등) 여기서 갈라진다."""

    @pytest.fixture
    def srv(self, monkeypatch, tmp_path):
        import bot.chart_data as cd
        import bot.dart_client as dc
        import bot.dashboard as d
        import bot.dashboard_server as ds
        monkeypatch.setattr(ds, "_ARCHIVE_ROOT", tmp_path / "archive")
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient(_KEY))
        st = {"quote": 0, "chart": 0}

        def build(ticker, full=False, force_fresh=False):
            st["quote"] += 1
            return {"pane": "fresh"}
        monkeypatch.setattr(d, "build_live_quote", build)

        def chart(ticker, interval="1d", period="1y", lite=False):
            st["chart"] += 1
            return {"bars": [1]}
        monkeypatch.setattr(cd, "fetch_chart_payload", chart)

        def call(path, method):
            h = ds.DashboardHandler.__new__(ds.DashboardHandler)
            h.path = path
            got = {}
            h._reply_json = lambda status, body: got.update(body)
            getattr(h, method)()
            return got
        st["call"] = call
        st["root"] = tmp_path
        return st

    @pytest.mark.parametrize("delta,served", [(-5, True), (5, False)])
    def test_light_quote(self, srv, delta, served):
        import bot.dashboard_server as ds
        age = ds._QUOTE_TTL["light"] + delta
        p = _touch(srv["root"] / "quote_cache" / ds._quote_cache_name("005930_KS", "light"),
                   age, now=time.time(), body={"ok": True, "quote": {"pane": "cached"}})
        got = srv["call"]("/api/quote?ticker=005930.KS", "_handle_quote_api")
        assert (got["quote"]["pane"] == "cached") is served
        assert srv["quote"] == (0 if served else 1)
        _touch(p, age, now=time.time())  # 핸들러가 다시 썼으면 같은 나이로 되돌린다
        ds._purge_dead_caches(srv["root"])
        assert p.exists() is served, "핸들러와 정리 루틴의 수명이 갈렸다"

    def test_stale_full_is_served_and_kept(self, srv):
        import bot.dashboard_server as ds
        p = _touch(srv["root"] / "quote_cache" / ds._quote_cache_name("005930_KS", "full"),
                   10 * 86400, now=time.time(), body={"ok": True, "quote": {"pane": "old"}})
        got = srv["call"]("/api/quote?ticker=005930.KS&full=1", "_handle_quote_api")
        assert got.get("stale") is True and got["quote"]["pane"] == "old"
        assert srv["quote"] == 0
        ds._purge_dead_caches(srv["root"])
        assert p.exists(), "핸들러가 stale 로 서빙하는 파일을 정리 루틴이 지웠다"

    @pytest.mark.parametrize("delta,served", [(-5, True), (5, False)])
    def test_chart(self, srv, delta, served):
        import bot.dashboard_server as ds
        age = ds._CHART_TTL + delta
        p = _touch(srv["root"] / "chart_cache" / ds.chart_cache_name("005930_KS", "1d", "1y"),
                   age, now=time.time(), body={"ok": True, "chart": {"bars": ["cached"]}})
        got = srv["call"]("/api/chart?ticker=005930.KS&interval=1d&range=1y",
                          "_handle_chart_api")
        assert (got["chart"]["bars"] == ["cached"]) is served
        assert srv["chart"] == (0 if served else 1)
        _touch(p, age, now=time.time())
        ds._purge_dead_caches(srv["root"])
        assert p.exists() is served, "핸들러와 정리 루틴의 수명이 갈렸다"


class TestOwnerPurges:
    """DART 표·수주잔고 캐시 — 이름에 파서 지문이 실려 모듈을 고칠 때마다 옛
    이름이 고아가 된다(#430 의 형제). 읽는 쪽이 mtime 수명 안만 읽으므로 나이로만
    가른다."""

    @pytest.mark.parametrize("modname,fn,ttl_attr,prefix_attr", [
        ("bot.dart_production", "purge_dead_tables", "_TABLES_TTL", "_TABLES_PREFIX"),
        ("bot.dart_backlog", "purge_dead_backlog", "_BL_TTL", "_BL_PREFIX"),
    ])
    def test_by_age_only(self, tmp_path, modname, fn, ttl_attr, prefix_attr):
        import importlib
        mod = importlib.import_module(modname)
        ttl = getattr(mod, ttl_attr)
        pre = getattr(mod, prefix_attr)
        keep = {
            f"{pre}cursig_A.json": ttl - 5,
            f"{pre}oldsig_B.json": 10,          # 옛 지문이어도 수명 안 — 옛 코드가 아직 읽는다
        }
        gone = {f"{pre}oldsig_C.json": ttl + 5, f"{pre}cursig_D.json": ttl + 5}
        other = {"fred_DGS10_2026-01-01.json": 10 * 86400,   # 남의 캐시 — 손대지 않는다
                 f"{pre}x.txt": 10 * 86400}
        for n, age in {**keep, **gone, **other}.items():
            _touch(tmp_path / n, age)
        res = getattr(mod, fn)(now=_NOW, cache_dir=tmp_path)
        assert _names(tmp_path) == set(keep) | set(other)
        assert res == (len(gone), len(keep), 0)

    def test_writer_names_carry_the_prefix(self):
        """정리가 보는 접두는 **이름을 짓는 함수**가 실제로 내는 이름에서 맞아야
        한다(#337) — 다른 접두로 바꾸면 정리가 그 캐시를 영영 못 본다."""
        import bot.dart_backlog as bl
        import bot.dart_production as dp
        q = [{"label": "26.2Q", "reprt_code": "11012"}]
        assert dp._tables_cache_key("005930.KS", q, ("products",)).startswith(
            dp._TABLES_PREFIX + dp._parse_sig())
        assert bl._bl_key("005930.KS", 2026, "11012").startswith(
            bl._BL_PREFIX + bl._parse_sig())

    def test_reader_and_purge_agree(self, tmp_path, monkeypatch):
        """읽는 쪽이 읽는 파일 = 정리가 남기는 파일. 같은 나이로 둘 다 태운다."""
        import bot.dart_backlog as bl
        import bot.dart_production as dp
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
        q = [{"label": "26.2Q", "reprt_code": "11012"}]
        tk = dp._tables_cache_key("005930.KS", q, ("products",))
        bk = bl._bl_key("005930.KS", 2026, "11012")
        now = time.time()
        for delta, alive in ((-30, True), (30, False)):
            _touch(tmp_path / tk, dp._TABLES_TTL + delta, now=now,
                   body={"data": {"products": {"rows": [1]}}})
            _touch(tmp_path / bk, bl._BL_TTL + delta, now=now,
                   body={"v": 1.0, "why": "ok"})
            assert (dp._tables_cached(tk) is not None) is alive
            assert (bl._bl_cached(bk) is not None) is alive
            dp.purge_dead_tables()
            bl.purge_dead_backlog()
            assert (tmp_path / tk).exists() is alive
            assert (tmp_path / bk).exists() is alive


class TestPurgeExpired:
    def test_missing_dir_and_unlink_failure(self, tmp_path, monkeypatch):
        import bot.finviz_client as fc
        assert fc.purge_expired("p_", 10, now=_NOW,
                                cache_dir=tmp_path / "nope") == (0, 0, 0)
        bad = _touch(tmp_path / "p_bad.json", 100)
        ok = _touch(tmp_path / "p_ok.json", 100)
        real = Path.unlink

        def _unlink(self, *a, **k):
            if self.name == bad.name:
                raise PermissionError("locked")
            return real(self, *a, **k)
        monkeypatch.setattr(Path, "unlink", _unlink)
        assert fc.purge_expired("p_", 10, now=_NOW, cache_dir=tmp_path) == (1, 0, 1)
        assert bad.exists() and not ok.exists()

    def test_default_dir_is_the_module_cache(self, tmp_path, monkeypatch):
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
        _touch(tmp_path / "p_old.json", 100)
        assert fc.purge_expired("p_", 10, now=_NOW) == (1, 0, 0)


# ── 실수 #430 — 시세 본문 캐시 이름의 환경 축 ──────────────────────────────
class TestQuoteEnvAxes:
    """무거운 본문은 DART 키뿐 아니라 KRX 로그인 자격증명(수급 다기간추이 사유) ·
    DATA_GO_KR(외국인보유) · Finnhub(내부자 심리·추천) 유무에 따라 문장·표가
    갈린다. 옛 판은 DART 하나만 이름에 실어, 다른 키를 넣어도 본문이 4시간(그
    뒤엔 stale) '미설정' 을 말했다."""

    @staticmethod
    def _states(ds, monkeypatch, states: dict):
        def fake(name):
            v = states[name]
            if v == "raise":
                raise RuntimeError("boom")
            return v
        monkeypatch.setattr(ds, "_quote_axis_ready", fake)

    def test_each_axis_flips_only_its_letter(self, monkeypatch):
        import bot.dashboard_server as ds
        base = {n: True for _l, n in ds._QUOTE_ENV_AXES}
        self._states(ds, monkeypatch, base)
        n0 = ds._quote_cache_name("X_KS", "full")
        assert n0.endswith("_" + "".join(l + "1" for l, _n in ds._QUOTE_ENV_AXES)
                           + ".json"), n0
        for letter, name in ds._QUOTE_ENV_AXES:
            self._states(ds, monkeypatch, {**base, name: False})
            n1 = ds._quote_cache_name("X_KS", "full")
            assert n1 != n0, name
            t0, t1 = n0.rsplit("_", 1)[1], n1.rsplit("_", 1)[1]
            diff = [i for i, (a, b) in enumerate(zip(t0, t1)) if a != b]
            assert len(diff) == 1 and t0[diff[0] - 1] == letter, (name, t0, t1)

    def test_axes_call_the_render_predicates(self, monkeypatch):
        """축 판정은 렌더가 그 자리에서 쓰는 술어 그대로다(#38) — 대역 없이 실제
        술어를 갈아 끼워 이름이 따라오는지 본다(헬퍼 대역만 재면 배선을 못 본다,
        #20)."""
        import bot.dart_client as dc
        import bot.dashboard_server as ds
        import bot.finnhub_client as fh
        import bot.pykrx_client as pk
        import bot.seibro_client as sb
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient("k" * 40))
        monkeypatch.setattr(pk, "krx_login_ready", lambda: True)
        monkeypatch.setattr(sb, "seibro_key_ready", lambda: False)
        monkeypatch.setattr(fh, "finnhub_key_ready", lambda: True)
        assert ds._quote_env_tag() == "k1r1s0f1"
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient(""))
        monkeypatch.setattr(pk, "krx_login_ready", lambda: False)
        monkeypatch.setattr(sb, "seibro_key_ready", lambda: True)
        monkeypatch.setattr(fh, "finnhub_key_ready", lambda: False)
        assert ds._quote_env_tag() == "k0r0s1f0"

    def test_axis_failure_is_x_and_warned(self, monkeypatch, caplog):
        import logging

        import bot.dashboard_server as ds
        states = {n: True for _l, n in ds._QUOTE_ENV_AXES}
        states["krx"] = "raise"
        self._states(ds, monkeypatch, states)
        with caplog.at_level(logging.WARNING, logger=ds.log.name):
            tag = ds._quote_env_tag()
        assert tag == "k1rxs1f1", tag
        assert any("krx" in r.getMessage() and "판정 실패" in r.getMessage()
                   for r in caplog.records), caplog.text

    def test_unknown_axis_name_raises(self):
        import bot.dashboard_server as ds
        with pytest.raises(ValueError):
            ds._quote_axis_ready("kis")

    @pytest.mark.parametrize("name", [
        "005930_KS_full_v{rv}_k1.json",            # #429 규약(DART 꼬리만)
        "005930_KS_full_v{rv}_k1r0s0.json",        # 축이 모자란 꼬리
        "005930_KS_full_v{rv}_r0k1s0f0.json",      # 축 순서가 다른 꼬리
        "005930_KS_mid_v{rv}_k1r0s0f0.json",       # 모르는 종류
        "005930_KS_full_v{rv}_k1r0s0f0.json.tmp",  # 쓰다 만 임시 파일
        "_full_v{rv}_k1r0s0f0.json",               # safe 가 빈 이름
    ])
    def test_parse_rejects_names_outside_the_grammar(self, name):
        import bot.dashboard as d
        import bot.dashboard_server as ds
        assert ds._quote_parse(name.format(rv=d._RENDER_VER)) is None, name

    def test_tag_axis_reader(self):
        """축 글자는 하나씩이고 값 글자(1·0·x)와 겹치지 않는다 — 그래야 꼬리에서
        첫 등장 자리가 그 축이다(`_quote_tag_axis` 가 기대는 전제)."""
        import bot.dashboard_server as ds
        assert [ds._quote_tag_axis("k1r0sxf1", c) for c in "krsfz"] == [
            "1", "0", "x", "1", None]
        letters = [c for c, _n in ds._QUOTE_ENV_AXES]
        assert all(len(c) == 1 for c in letters)
        assert len(set(letters)) == len(letters)
        assert not set(letters) & set("01x")

    # 이 술어들이 본문을 가르지만 축으로 두지 않는 이유(#24 — 열거가 아니라 사유).
    _NOT_AN_AXIS = {
        "_env_ready": "`?debug=1` 진단 응답의 env 칸 — 본문(캐시되는 HTML)이 아니다",
        "_ready": "KIS `KisClient._ready` — 존재하지 않는 이름이라 늘 예외로 빠진다"
                  "(별도 과제). 살아나면 이 줄을 지우고 축을 더할 것",
    }
    _AXIS_OF = {"dart_ready": "dart", "krx_login_ready": "krx",
                "seibro_key_ready": "seibro", "finnhub_key_ready": "finnhub"}

    def test_every_env_predicate_in_the_render_has_an_axis(self):
        """본문을 그리는 `bot/dashboard.py` 가 부르는 `*_ready` 술어마다 축이 있다
        — 새 키에 따라 본문이 갈리기 시작하면 이 테스트가 그 술어 이름을 댄다(#24
        이름 열거는 다음 술어를 못 잡는다 → 소스에서 파생). 거꾸로 축마다 그 술어가
        실제로 쓰인다(안 쓰이는 축은 이름만 쪼개는 죽은 축이다, #291)."""
        import ast
        import pathlib

        import bot.dashboard_server as ds
        root = pathlib.Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "bot" / "dashboard.py").read_text(encoding="utf-8"))
        seen = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if nm and nm.endswith("_ready"):
                    seen.add(nm)
        axes = {n for _l, n in ds._QUOTE_ENV_AXES}
        unknown = seen - set(self._AXIS_OF) - set(self._NOT_AN_AXIS)
        assert not unknown, (f"본문이 축 없는 환경 술어를 쓴다: {sorted(unknown)} — "
                             "`_QUOTE_ENV_AXES` 에 축을 더하거나 사유와 함께 "
                             "`_NOT_AN_AXIS` 에")
        assert {self._AXIS_OF[p] for p in seen if p in self._AXIS_OF} == axes, seen
        assert set(self._AXIS_OF.values()) == axes
        assert len(self._NOT_AN_AXIS) == 2      # 늘리려면 이 테스트를 고친다(#286)
