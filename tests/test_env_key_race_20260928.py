"""키 조회 경합 — 병렬 워커가 같은 키를 동시에 물으면 빈 값을 받았다(실수 #422).

2026-09-28 VM 실측(`python -m bot.scripts.fcf_audit 181710.KS`):
`pykrx: KRX_ID/KRX_PW 미설정 — KRX_PW: …/.env: 값 있음(길이 11) — ⚠️ 지금 다시 읽으면
있다(첫 조회가 실패해 캐시됨 …)` 바로 뒤에 라이브러리가 `KRX 로그인 완료` 를 찍었다.
2026-09-07 에도 같은 모양이었다(#294). 원인은 cwd 도 `.env` 생성 시점도 아니었다 —
`env_key` 가 `_TRIED` 에 **먼저 표시**하고 `.env` 를 읽어, 그 사이 같은 키를 물은
스레드가 '이미 조회했는데 없었다' 로 읽고 "" 를 받았다. 샌드박스 재현: 16스레드 동시
호출 20회 전부 15개가 빈 값 · 경고 15번(1회 경고 플래그도 같은 경합). 형제 둘도 같았다
— `dart_client._dart_key_from_env_file` 16개 중 15개 빈 값 · `get_dart()` 싱글턴이
20회 중 8회 **키 없이** 굳음 · `dart_feed._dart_api_key` 16개 중 14개 None.

운영 유닛은 `EnvironmentFile=` · `load_dotenv()` 로 환경이 먼저 차 있어 이 경로에 안
온다 — 사람이 돌리는 진단·프로브(`python -m bot.scripts.…`)에서 났다.

두 종류로 잰다:
  · **결정적** — 첫 조회가 도는 **도중에** 두 번째 호출을 끼워 넣는다(시간 경합에
    기대지 않는다, #128). 고친 판에선 두 번째가 락에서 기다렸다 값을 받고, 옛 판에선
    즉시 "" 로 끝난다. 기다림 상한(0.5초)은 옛 판을 잡는 속도에만 쓰이고 고친 판의
    결과는 바꾸지 않는다(고친 판은 늘 값을 받는다).
  · **증상** — 배리어로 16스레드를 **동시에** 출발시킨다(차례로 띄우면 경합이 안
    생긴다 — `test_log_redaction_20260925` 의 선례, #91c). 고친 판에선 늘 통과하고,
    옛 판에선 샌드박스 재현 20회 전부 실패했다.
"""
from __future__ import annotations

import logging
import sys
import threading

import pytest

_N = 16
_KRX_ENV = "KRX_ID=fakeid\nKRX_PW=fakepw12345\n"
_DART_ENV = "DART_API_KEY=dk-fake-0123\n"        # 16자 미만 — 가림 목록에 안 남는다(#416)


def _iso(monkeypatch, tmp_path, body):
    """`.env` 를 tmp 에만 두고 모듈 전역·환경을 복원되게 갈아 끼운다(#30·#130).

    ⚠️ `Path.home()` 을 **반드시** 막는다 — `_dotenv_lookup` 의 둘째 경로가
    `~/stock/.env`(운영자의 진짜 자격증명 파일)다(`TestEnvDiagNamesTheBranch20260907`).
    ⚠️ 환경변수는 `setenv` → `delenv` 로 **원래 상태를 기록**해 둔다 — 없는 키를
    `delenv(raising=False)` 만 하면 기록이 안 남아, `env_key` 가 채운 값이 뒤 테스트로 샌다.
    """
    import bot.env_keys as ek
    monkeypatch.setattr(ek, "_TRIED", set())
    home = tmp_path / "_home"
    (home / "stock").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: home))
    cwd = tmp_path / "_cwd"
    cwd.mkdir(exist_ok=True)
    if body is not None:
        (cwd / ".env").write_text(body, encoding="utf-8")
    monkeypatch.chdir(cwd)
    for k in ("KRX_ID", "KRX_PW", "DART_API_KEY", "RACE_K"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    return ek


@pytest.fixture()
def contention():
    """스레드 전환 간격을 좁혀 경합을 강제한다(선례와 같은 손잡이)."""
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(old)


def _all_at_once(fn, n=_N):
    """`fn` 을 n 스레드에서 **동시에** 출발시켜 결과 목록을 돌려준다."""
    gate = threading.Barrier(n)
    out = []

    def work():
        gate.wait(timeout=10)
        out.append(fn())
    ts = [threading.Thread(target=work) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not any(t.is_alive() for t in ts), "스레드가 끝나지 않았다(교착?)"
    return out


def _cut_in(seen, call):
    """첫 조회 **도중에** 다른 스레드에서 `call()` 을 부르고 0.5초까지 기다린다.

    고친 판: 끼어든 호출은 락에서 기다리므로 여기선 시간 초과로 넘어가고, 첫 조회가
    끝난 뒤 값을 받는다. 옛 판: 끼어든 호출이 즉시 끝난다(빈 값·오진).
    """
    t = threading.Thread(target=lambda: seen.__setitem__("b", call()))
    seen["t"] = t
    t.start()
    t.join(0.5)


def _finish(seen):
    seen["t"].join(10)
    assert not seen["t"].is_alive(), "끼어든 호출이 끝나지 않았다(교착?)"
    return seen["b"]


# ── env_keys.env_key · env_diag ─────────────────────────────────────────────


def test_a_caller_that_cuts_in_during_the_lookup_gets_the_value(monkeypatch, tmp_path):
    """조회 **도중**에 같은 키를 물은 스레드가 빈 값을 받으면 안 된다 — 이 경합의 본체."""
    ek = _iso(monkeypatch, tmp_path, "RACE_K=abcdef\n")
    real, seen = ek._dotenv_lookup, {}

    def slow(name):
        if "t" not in seen:
            _cut_in(seen, lambda: ek.env_key("RACE_K"))
        return real(name)
    monkeypatch.setattr(ek, "_dotenv_lookup", slow)
    assert ek.env_key("RACE_K") == "abcdef"
    assert _finish(seen) == "abcdef"


def test_diag_does_not_call_an_inflight_lookup_a_cached_failure(monkeypatch, tmp_path):
    """조회 중인 키를 '첫 조회가 실패해 캐시됨' 으로 부르면 거짓 사유다(#292·#187b).

    VM 경고가 운영자에게 'cwd·.env 생성 시점' 을 확인하라고 보냈는데 둘 다 멀쩡했다.
    `env_diag` 는 상태를 락 안에서 읽어 조회가 끝나기를 기다린다 — 끝나면 그 키는
    정상이라 **적을 게 없다**.
    """
    ek = _iso(monkeypatch, tmp_path, "RACE_K=abcdef\n")
    real, seen = ek._dotenv_lookup, {}

    def slow(name):
        if "t" not in seen:
            _cut_in(seen, lambda: ek.env_diag("RACE_K"))
        return real(name)
    monkeypatch.setattr(ek, "_dotenv_lookup", slow)
    assert ek.env_key("RACE_K") == "abcdef"
    d = _finish(seen)
    assert "캐시됨" not in d, d
    assert d == "", d


def test_a_real_cached_failure_is_still_named(monkeypatch, tmp_path):
    """⚠️ 반대 증거 — 락이 **진짜** 캐시된 실패까지 지우면 안 된다(#25).

    조회가 **끝나고** 실패한 뒤 `.env` 가 읽히게 된 경우는 여전히 그 갈래로 부른다
    (`TestEnvDiagNamesTheBranch20260907::test_stale_cache_contradiction_is_named` 와 같은
    계약 — 여기선 락을 넣은 뒤에도 그 갈래가 살아 있는지를 본다).
    """
    ek = _iso(monkeypatch, tmp_path, None)
    assert ek.env_key("RACE_K") == ""                   # 끝난 조회 — 실패가 캐시된다
    (tmp_path / "_cwd" / ".env").write_text("RACE_K=abcdef\n", encoding="utf-8")
    assert ek.env_key("RACE_K") == "", "캐시 전제가 깨졌다 — 이 테스트 무의미"
    d = ek.env_diag("RACE_K")
    assert "첫 조회가 실패해 캐시됨" in d, d


# ── pykrx 게이트 — VM 에서 본 그 증상 ─────────────────────────────────────────


def test_parallel_krx_gate_sees_keys_that_are_in_the_env_file(monkeypatch, tmp_path,
                                                               caplog, contention):
    """VM 증상 그대로 — 키가 `.env` 에 있는데 동시에 친 게이트가 '미설정' 을 냈다."""
    _iso(monkeypatch, tmp_path, _KRX_ENV)
    import bot.pykrx_client as pk
    monkeypatch.setattr(pk, "_KRX_CRED_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="bot.pykrx"):
        res = _all_at_once(pk.krx_login_ready)
    assert res == [True] * _N, f"빈 판정 {res.count(False)}/{_N}"
    assert not [r for r in caplog.records if "미설정" in r.getMessage()]


def test_missing_keys_warn_once_under_concurrency(monkeypatch, tmp_path, caplog,
                                                  contention):
    """'최초 1회' 는 확인과 표시가 한 번에 일어나야 참이다 — 옛 판은 16스레드에 16번 찍었다."""
    _iso(monkeypatch, tmp_path, "OTHER=1\n")
    import bot.pykrx_client as pk
    monkeypatch.setattr(pk, "_KRX_CRED_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="bot.pykrx"):
        res = _all_at_once(pk.krx_login_ready)
    assert res == [False] * _N                           # 키가 정말 없으면 막는 게 맞다
    warns = [r for r in caplog.records if "미설정" in r.getMessage()]
    assert len(warns) == 1, len(warns)


# ── DART 형제 ────────────────────────────────────────────────────────────────


def test_dart_key_reader_cut_in_gets_the_key(monkeypatch, tmp_path):
    """`dart_client` 는 자체 `.env` 읽기를 쓴다(빈 문자열='키 없음' 규약) — 같은 경합."""
    _iso(monkeypatch, tmp_path, _DART_ENV)
    import dotenv

    import bot.dart_client as dc
    monkeypatch.setattr(dc, "_ENV_KEY_TRIED", False)
    monkeypatch.setattr(dc, "_ENV_KEY_CACHED", "")
    real, seen = dotenv.dotenv_values, {}

    def slow(*a, **k):
        if "t" not in seen:
            _cut_in(seen, dc._dart_key_from_env_file)
        return real(*a, **k)
    monkeypatch.setattr(dotenv, "dotenv_values", slow)
    assert dc._dart_key_from_env_file() == "dk-fake-0123"
    assert _finish(seen) == "dk-fake-0123"


def test_get_dart_never_hands_out_a_keyless_client(monkeypatch, tmp_path, contention):
    """사용자가 보게 될 결과 — 키 없는 클라이언트를 받은 호출은 DART 가 `인증키 누락` 이다.

    ⚠️ **마지막에 대입된 싱글턴**만 재면 눈이 먼다(첫 판 실측: 옛 판에서도 통과) —
    키를 읽는 첫 생성이 가장 늦게 끝나 **마지막에** 대입되면 싱글턴은 멀쩡해 보이지만,
    그 사이 끼어든 호출들은 이미 키 없는 클라이언트를 받아 갔다(#91b 재는 대상이 맞나).
    호출자가 **받은 것 전부**를 잰다. 샌드박스 재현에선 최종 싱글턴까지 키 없이 굳은
    경우가 20회 중 8회였다.
    """
    _iso(monkeypatch, tmp_path, _DART_ENV)
    import bot.dart_client as dc
    for _ in range(5):
        monkeypatch.setattr(dc, "_ENV_KEY_TRIED", False)
        monkeypatch.setattr(dc, "_ENV_KEY_CACHED", "")
        monkeypatch.setattr(dc, "_singleton", None)
        got = _all_at_once(lambda: dc.get_dart().api_key)
        assert got == ["dk-fake-0123"] * _N, f"키 없는 클라이언트 {got.count('')}/{_N}"


def test_dart_feed_key_under_concurrency(monkeypatch, tmp_path, contention):
    """`dart_feed._dart_api_key` 는 `env_key` 를 먼저 부른다 — 뿌리를 고치면 따라온다."""
    _iso(monkeypatch, tmp_path, _DART_ENV)
    import bot.dart_feed as df
    monkeypatch.setattr(df, "_ENV_TRIED", False)
    res = _all_at_once(df._dart_api_key)
    assert res == ["dk-fake-0123"] * _N, f"빈 값 {sum(1 for r in res if not r)}/{_N}"
