"""FRED 스파크(`macro_snapshot._fred_monthly`)가 헤드라인과 **같은 캐시 규약**을 쓴다
(PR #1304 2차 독립 리뷰 L6, 사용자 2026-09-25 "스파크라인건 이어서 처리해줘").

옛 판은 캐시·실패 기억이 없어 30초 재생성마다 FRED 카드 9장을 전부 새로 물었다(정상일 때
시리즈당 시간 120회). FRED 가 막히면 재생성마다 12초 × 9 를 줄지어 기다렸다 — 같은 카드의
헤드라인(`market_overview._fred_fetch_series`)은 #415·리뷰 M6 로 이미 1시간 캐시 + 같은 날
사본 + 10분 실패 기억을 갖고 있었다. 이제 스파크가 **같은 TTL 함수(`_fred_ttl_h`)·같은 실패
기억(`_fred_fail`)** 을 쓴다.

네트워크 0 — FRED 는 `ms.requests.get` 경계에서 스텁한다(#312·#336). 캐시 디렉터리는
테스트마다 `tmp_path` 로 갈아 끼우고 실패 기억은 비운다(#30). 날짜는 시계에서 파생한다(#249).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date

import pytest

import bot.macro_snapshot as ms
import bot.market_overview as mo

_SID = "PCEPILFE"             # 월간 카드
_DAILY = "DGS10"              # 일별 카드(월평균 스파크)
_GDP = "A191RL1Q225SBEA"      # 분기 카드


class _Resp:
    def __init__(self, obs):
        self._obs = obs

    def raise_for_status(self):
        return None

    def json(self):
        return {"observations": self._obs}


def _months_desc(n: int, last=(2026, 7)) -> list[dict]:
    """FRED 월간 관측(내림차순) — 달마다 다른 값(#91c)."""
    out, (y, m) = [], last
    for i in range(n):
        out.append({"date": f"{y}-{m:02d}-01", "value": f"{130 - i / 10:.2f}"})
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


@pytest.fixture
def spark(tmp_path, monkeypatch):
    """캐시 디렉터리·실패 기억을 테스트 것으로 — 그리고 FRED 호출을 센다. 키는 가림 최소
    길이(`env_keys._SECRET_MIN`, 16자)를 넘겨야 로그 가림 회귀가 실제로 잰다."""
    monkeypatch.setenv("FRED_API_KEY", "k" * 32)
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "snap")
    monkeypatch.setattr(mo, "_fred_fail", {})
    calls: list[dict] = []
    answer = {"obs": _months_desc(24), "raise": None}

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        if answer["raise"] is not None:
            raise answer["raise"]
        return _Resp(answer["obs"])
    monkeypatch.setattr(ms.requests, "get", fake_get)
    monkeypatch.setattr(ms, "_FRED_SPARK_START", {})
    return {"calls": calls, "answer": answer, "dir": tmp_path / "snap"}


def _cache_file(sid=_SID, freq="m", months=12):
    return ms._fred_spark_cache_file(sid, freq, months)


def _age(f, hours):
    t = time.time() - hours * 3600
    os.utime(f, (t, t))


def _failed_ago(key, seconds):
    """실패 기록을 `seconds` 초 전으로 옮긴다 — 전역 시계를 갈아 끼우지 않고 창의 경계를 잰다."""
    mo._fred_fail[key] = time.monotonic() - seconds


# ── 캐시 ────────────────────────────────────────────────────────────────
def test_the_spark_is_cached_and_not_asked_again_within_the_ttl(spark):
    first = ms._fred_monthly(_SID)
    assert len(spark["calls"]) == 1 and len(first) == 12
    assert first[-1] == 130.0 and first[0] == pytest.approx(128.9)       # 오래된→최신
    assert ms._FRED_SPARK_START[_SID] == "2025-08-01"
    ms._FRED_SPARK_START.clear()
    again = ms._fred_monthly(_SID)
    assert len(spark["calls"]) == 1, "TTL 안인데 다시 물었다"
    assert again == first
    # 창 첫 관측 기록도 사본에서 되살린다 — 안 그러면 카드가 '… 대비' 대신 '12개월 전' 이다
    assert ms._FRED_SPARK_START[_SID] == "2025-08-01"


def test_the_spark_reads_the_same_ttl_function_as_the_headline(spark, monkeypatch):
    """TTL 은 `market_overview._fred_ttl_h` **한 곳**이다(#38) — 셋이 각자 식을 들면 한쪽만
    바뀌어 한 카드가 두 기간을 말한다(#33·#415). 상수를 바꿔 그 값이 실제로 걸리는지 본다."""
    ms._fred_monthly(_SID)
    _age(_cache_file(), 0.5)                                    # 30분 된 사본
    monkeypatch.setattr(mo, "_FRED_TTL_OTHER_H", 1.0)
    ms._fred_monthly(_SID)
    assert len(spark["calls"]) == 1, "30분 된 사본인데 1시간 TTL 에서 다시 물었다"
    monkeypatch.setattr(mo, "_FRED_TTL_OTHER_H", 0.25)
    ms._fred_monthly(_SID)
    assert len(spark["calls"]) == 2, "헤드라인 TTL 을 줄였는데 스파크는 안 따라왔다"
    # 일별 카드는 일별 TTL 을 쓴다 — 같은 함수가 가른다
    ms._fred_monthly(_DAILY)
    _age(_cache_file(_DAILY), 0.5)
    monkeypatch.setattr(mo, "_FRED_TTL_DAILY_H", 0.25)
    monkeypatch.setattr(mo, "_FRED_TTL_OTHER_H", 24.0)
    ms._fred_monthly(_DAILY)
    assert [c["series_id"] for c in spark["calls"]].count(_DAILY) == 2


def test_the_headline_yoy_and_spark_all_consult_the_shared_ttl(spark, monkeypatch, tmp_path):
    """배선(#20) — 세 경로가 **실제로** 그 함수를 부르는지 스파이로 본다. 헤드라인·YoY 는
    인라인 식을 들고 있다가 이 커밋에서 함수로 옮겼다."""
    seen: list[str] = []

    def spy(sid):
        seen.append(sid)
        return 1.0
    monkeypatch.setattr(mo, "_fred_ttl_h", spy)
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path / "mo")
    monkeypatch.setattr(mo.requests, "get",
                        lambda url, params=None, timeout=None: _Resp(_months_desc(24)))
    ms._fred_monthly(_SID)
    mo._fred_fetch_series("CPIAUCSL", 400)
    mo._fetch_fred_yoy("PPIFIS")
    assert seen == [_SID, "CPIAUCSL", "PPIFIS"], seen


def test_the_cache_key_carries_frequency_and_length(spark):
    """결과를 바꾸는 인자는 전부 키다(#61) — 12개를 구운 사본을 6개 요청에 주면 안 된다.
    분기 계열은 분기로 묻는다(월로 물으면 FRED 400)."""
    ms._fred_monthly(_SID)
    six = ms._fred_monthly(_SID, months=6)
    assert len(spark["calls"]) == 2 and len(six) == 6
    assert [c["limit"] for c in spark["calls"]] == [24, 12]
    ms._fred_monthly(_GDP)
    assert spark["calls"][-1]["frequency"] == "q"
    names = sorted(p.name for p in (spark["dir"] / "fred_spark").iterdir())
    today = date.today().isoformat()
    assert names == sorted([f"{_SID}_m12_{today}.json", f"{_SID}_m6_{today}.json",
                            f"{_GDP}_q12_{today}.json"]), names


# ── 실패 ────────────────────────────────────────────────────────────────
def test_a_failure_serves_the_same_day_copy_and_is_remembered_for_ten_minutes(spark):
    """원천이 막혀도 카드가 비지 않는다(#394) — 그리고 30초 재생성마다 12초를 기다리지
    않는다(실패 기억 10분, 헤드라인과 같은 `_FRED_FAIL_TTL_S`)."""
    good = ms._fred_monthly(_SID)
    _age(_cache_file(), 2)                                          # TTL 이 지난 같은 날 사본
    ms._FRED_SPARK_START.clear()
    spark["answer"]["raise"] = ConnectionError("fred down")
    assert ms._fred_monthly(_SID) == good
    assert ms._FRED_SPARK_START[_SID] == "2025-08-01"               # 사본의 창 라벨
    assert len(spark["calls"]) == 2
    key = str(_cache_file())
    assert key in mo._fred_fail, "실패를 기억하지 않았다"
    _failed_ago(key, mo._FRED_FAIL_TTL_S - 5)                       # 아직 창 안
    assert ms._fred_monthly(_SID) == good
    assert len(spark["calls"]) == 2, "10분 안에 다시 물었다"
    _failed_ago(key, mo._FRED_FAIL_TTL_S + 1)                       # 실패 기억이 풀렸다
    assert ms._fred_monthly(_SID) == good
    assert len(spark["calls"]) == 3, "실패 기억이 풀린 뒤에도 안 물었다(#178)"


def test_an_empty_answer_is_neither_cached_nor_asked_every_cycle(spark, caplog):
    """빈 답은 굽지 않는다(#280) · 30초마다 다시 묻지도 않는다 · 조용하지도 않다(#12)."""
    spark["answer"]["obs"] = [{"date": "2026-07-01", "value": "."}]
    with caplog.at_level(logging.WARNING, logger="bot.macro_snapshot"):
        assert ms._fred_monthly(_SID) == []
    assert not _cache_file().exists(), "빈 답을 사본으로 구웠다"
    assert "빈 답" in caplog.text and _SID in caplog.text
    assert ms._FRED_SPARK_START[_SID] == ""
    ms._fred_monthly(_SID)
    assert len(spark["calls"]) == 1, "빈 답인데 곧바로 다시 물었다"


def test_an_empty_answer_still_serves_the_same_day_copy(spark):
    good = ms._fred_monthly(_SID)
    _age(_cache_file(), 2)
    spark["answer"]["obs"] = []
    assert ms._fred_monthly(_SID) == good
    assert json.loads(_cache_file().read_text())["vals"] == good    # 사본을 덮지 않았다


def test_success_clears_the_failure_memory(spark):
    """성공하면 그 계열의 실패 기록을 지운다 — 기록이 쌓이지 않고(헤드라인과 같은 규약)
    다음 실패가 새로 10분을 센다."""
    spark["answer"]["raise"] = ConnectionError("fred down")
    ms._fred_monthly(_SID)
    key = str(_cache_file())
    assert key in mo._fred_fail
    _failed_ago(key, mo._FRED_FAIL_TTL_S + 1)
    spark["answer"]["raise"] = None
    assert len(ms._fred_monthly(_SID)) == 12
    assert key not in mo._fred_fail


def test_another_cache_dir_does_not_inherit_the_failure(spark, monkeypatch, tmp_path):
    """실패 기억의 키는 **캐시 파일 경로**다 — 디렉터리를 갈아 끼운 다른 실행(테스트 포함)이
    남의 실패를 물려받지 않는다(#30)."""
    spark["answer"]["raise"] = ConnectionError("fred down")
    ms._fred_monthly(_SID)
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "other")
    spark["answer"]["raise"] = None
    assert len(ms._fred_monthly(_SID)) == 12
    assert len(spark["calls"]) == 2


def test_no_copy_means_an_empty_spark_and_a_cleared_start_label(spark, monkeypatch, tmp_path):
    """사본이 없으면 빈 스파크 — 그리고 앞선 실행의 창 라벨을 **남기지 않는다**(#33 한 카드가
    두 창을 말하지 않게). 옛 판은 실패 경로가 기록을 안 건드려 옛 라벨이 살아 있었다."""
    ms._fred_monthly(_SID)
    assert ms._FRED_SPARK_START[_SID] == "2025-08-01"
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "nextday")       # 같은 날 사본 없음
    spark["answer"]["raise"] = ConnectionError("fred down")
    assert ms._fred_monthly(_SID) == []
    assert ms._FRED_SPARK_START[_SID] == ""


@pytest.mark.parametrize("content", [
    "not json {",
    json.dumps([1, 2, 3]),                                              # dict 가 아니다
    json.dumps({"cv": ms._FRED_SPARK_CACHE_VER - 1, "vals": [1.0], "start": ""}),   # 옛 판
    json.dumps({"cv": ms._FRED_SPARK_CACHE_VER, "vals": [], "start": ""}),
    json.dumps({"cv": ms._FRED_SPARK_CACHE_VER, "vals": ["x"], "start": ""}),
    json.dumps({"cv": ms._FRED_SPARK_CACHE_VER, "vals": [1.0], "start": None}),
])
def test_a_copy_we_cannot_trust_is_not_served(spark, content):
    """못 읽은·옛 판·모양이 틀린 사본은 값이 아니다(#18·#331) — 신선해도 다시 묻고, 원천이
    막혀도 그걸 주지 않는다."""
    f = _cache_file()
    f.parent.mkdir(parents=True)
    f.write_text(content)
    assert len(ms._fred_monthly(_SID)) == 12
    assert len(spark["calls"]) == 1
    f.write_text(content)
    _age(f, 2)
    mo._fred_fail.clear()
    spark["answer"]["raise"] = ConnectionError("fred down")
    assert ms._fred_monthly(_SID) == []


def test_the_failure_log_does_not_leak_the_key(spark, caplog):
    """예외 문구엔 `api_key=` 가 든 URL 이 실린다 — `bot.env_keys` 레코드 팩토리가 가린다
    (#416). 이 커밋이 그 로그 줄을 고쳤으니 여기서도 잰다."""
    key = os.environ["FRED_API_KEY"]
    spark["answer"]["raise"] = ConnectionError(
        f"HTTPSConnectionPool: /fred/series/observations?series_id={_SID}&api_key={key}")
    with caplog.at_level(logging.WARNING, logger="bot.macro_snapshot"):
        ms._fred_monthly(_SID)
    assert "monthly failed" in caplog.text
    assert key not in caplog.text, caplog.text


def test_a_failed_cache_write_is_not_fatal_and_leaves_no_debris(spark, monkeypatch, caplog):
    """쓰기는 통째로 갈아 끼운다(#379 — 다른 프로세스가 쓰다 만 파일을 읽지 않게). 갈아 끼우기가
    실패해도 값은 화면에 가고, 반쯤 쓴 사본도 임시파일도 남지 않고, 조용하지 않다(#12)."""
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(ms.os, "replace", boom)
    with caplog.at_level(logging.WARNING, logger="bot.macro_snapshot"):
        assert len(ms._fred_monthly(_SID)) == 12
    d = spark["dir"] / "fred_spark"
    assert not _cache_file().exists(), "갈아 끼우기 전에 사본이 생겼다(통째 쓰기가 아니다)"
    assert not list(d.glob("*.tmp")), f"임시파일이 남았다: {list(d.iterdir())}"
    assert "캐시를 못 썼다" in caplog.text


def test_two_threads_writing_the_same_copy_use_separate_temp_files(spark, monkeypatch):
    """대시보드는 요청마다 스레드라 같은 프로세스의 두 재생성이 같은 사본을 **동시에** 쓸 수
    있다 — 임시파일 이름이 pid 만이면 한쪽이 다른 쪽의 반쯤 쓴 파일을 옮긴다. 두 스레드를
    갈아 끼우기 직전에 `Barrier` 로 붙잡아(둘 다 살아 있어야 스레드 id 가 안 겹친다) 결정적으로
    잰다(#128 시간·순서가 아니라 구조로)."""
    import threading
    gate = threading.Barrier(2, timeout=10)
    srcs: list[str] = []
    real_replace = os.replace

    def replace(src, dst):
        srcs.append(os.path.basename(src))
        gate.wait()
        real_replace(src, dst)
    monkeypatch.setattr(ms.os, "replace", replace)
    out: list = []
    ths = [threading.Thread(target=lambda: out.append(ms._fred_monthly(_SID))) for _ in range(2)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=20)
    assert len(out) == 2 and all(len(o) == 12 for o in out), out
    assert len(srcs) == 2 and srcs[0] != srcs[1], f"두 스레드가 같은 임시파일을 썼다: {srcs}"
    assert json.loads(_cache_file().read_text())["vals"] == out[0]


def test_the_snapshot_caches_are_isolated_from_the_operator_home():
    """루트 conftest 가 스냅샷 경로 셋을 홈 밖으로 옮긴다 — 2026-09-25 실측으로 `snapshot.json`
    (빈 스냅샷) · `yf_batch_snapshot.json` · `fear_greed.json`(실패 도장)이 운영 홈에 구워졌다
    (#30·#312·#344). 그 줄을 지우면 여기서 걸린다(선언을 소스에서 파생하는 회귀는 **지운** 줄을
    못 본다 — 선언과 적용이 같이 사라지므로)."""
    from pathlib import Path
    import bot.fear_greed_client as fg
    home = Path.home() / ".tradingagents"
    for label, val in (("macro_snapshot._CACHE_DIR", ms._CACHE_DIR),
                       ("market_overview._CACHE_DIR", mo._CACHE_DIR),
                       ("fear_greed_client._CACHE", fg._CACHE)):
        val = Path(str(val))
        assert home not in val.parents and val != home, f"{label} → {val}"


def test_no_key_asks_nothing(spark, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr("bot.env_keys._dotenv_lookup", lambda name: (None, "없음", ""))
    assert ms._fred_monthly(_SID) == []
    assert spark["calls"] == []


# ── 화면 배선(수집기를 통째로 태운다, #20) ────────────────────────────────
def _build(monkeypatch, snap_dir):
    import bot.naver_marketindex as nm
    snap_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ms, "_CACHE_DIR", snap_dir)
    monkeypatch.setattr(ms, "_fetch_macro_naver_values", lambda sids: {})
    monkeypatch.setattr(ms, "_yf_monthly_batch", lambda tk: {})
    monkeypatch.setattr(ms, "_yf_daily_1mo_batch", lambda tk: {})
    for fn in ("fetch_commodity_spark", "fetch_naver_index_history",
               "fetch_naver_crypto_history", "fetch_naver_fx_history"):
        monkeypatch.setattr(nm, fn, lambda *a, **k: [])
    monkeypatch.setattr(ms, "_customs_series", lambda key: {"points": [], "src": ""})
    monkeypatch.setattr(ms, "_ecos_series", lambda key: [])
    monkeypatch.setattr(ms, "_build_charts", lambda *a, **k: {})
    monkeypatch.setattr(mo, "_fred_fetch_series",
                        lambda sid, lb: {"value": 130.0, "time": "2026-07-01", "change": 0.1})
    return ms.fetch_macro_snapshot()


def test_the_snapshot_rebuild_does_not_ask_fred_again(spark, monkeypatch):
    """30초 재생성(스냅샷 캐시 만료)마다 FRED 카드 9장을 새로 묻던 것이 옛 판이다. 두 번째
    재생성은 스파크를 **한 번도** 묻지 않는다 — 값이 온 계열은 사본으로, 빈 계열은 실패
    기억으로. 그리고 카드는 사본의 창 라벨을 그대로 말한다."""
    fred_cards = [sid for *_x, src, sid, _d in ms.GLOBAL if src == "fred"]
    assert len(fred_cards) >= 5, fred_cards                      # 대조 0건은 통과가 아니다(#54)
    snap = spark["dir"]
    out1 = _build(monkeypatch, snap)
    assert sorted(c["series_id"] for c in spark["calls"]) == sorted(fred_cards)
    (snap / "snapshot.json").unlink()                            # 30초 스냅샷 캐시가 지났다
    ms._FRED_SPARK_START.clear()                                 # 라벨도 사본에서 와야 한다
    out2 = _build(monkeypatch, snap)
    assert len(spark["calls"]) == len(fred_cards), "재생성이 FRED 스파크를 다시 물었다"
    r1 = {r["key"]: r for r in out1["global"]}
    r2 = {r["key"]: r for r in out2["global"]}
    assert r2["us_pce"]["spark"] == r1["us_pce"]["spark"] and len(r2["us_pce"]["spark"]) == 12
    assert r2["us_pce"]["period_start_asof"] == "2025-08", r2["us_pce"]["period_start_asof"]
