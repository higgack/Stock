"""나쁜양파 동기화 — 파서가 생기기 전에 버려진 캡션의 **자동 회수**(실수 #403).

2026-09-16 사용자가 채널에서 본 한국 수입 회사별 캡션(텔레칩스, 스크린샷
첨부 — `kr_stock_imports` 독스트링)이 09-24 까지 보드에 없었다. 관련성 필터
= 파서라 파서 배포 전 캡션은 리스너가 버리고, 6시간 동기화는 3일만 본다.
나는 09-22 에 그걸 '원천 미게시' 로 오판했다 — 그 dry-run 이 **파서는 받는데
아직 inbox 에 없는 유닛**을 찍지 않았기 때문이다.

⚠️ 왜 가짜 telethon 으로 태우나: 형제 `test_backfill_beon` 은 telethon 이
없으면 **통째로 skip** 이라 게이트(`make test`)에선 한 번도 안 돈다(못 보는
축). 판정은 `badonion_sources.sync_plan` 이 순수하게 잰다 — 여기서는 그
판정이 `main()` 에서 **창·기록·출력으로 이어지는지**를 값으로 본다(#20
헬퍼 테스트는 배선을 못 잰다). 스크립트는 sys.modules 에 **등록하지 않는
사설 모듈**로 읽고, 가짜는 `monkeypatch` 로만 꽂는다(#397 누수 가드).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trade import badonion_sources as srcs

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_badonion.py"

# kri 캡션 — `kr_stock_imports` 독스트링의 사용자 스크린샷 재구성(#155: 실물
# 바이트가 아니다. 같은 채널의 품목판 파서 9개가 같은 `▶️` 로 운영 중이다).
_KRI = ("**🇰🇷 8월 수입 한국**\n\n**▶️ 텔레칩스 — 차량용 AP·프로세서**\n\n"
        "**26년08월: $2,175.2M  (+49.4% YoY)  (+7.3% MoM)**")
_CHATTER = "AMD 신고가 돌파"


class _Peer:
    def __init__(self, pid):
        self.id = pid


class _Msg:
    def __init__(self, mid, text, date):
        self.id, self.text, self.date = mid, text, date
        self.fwd_from = None
        self.grouped_id = None


class _Client:
    """TelegramClient 의 이 스크립트가 쓰는 표면만 — 읽기·포워드를 기록한다."""
    messages: list = []
    instances: list = []
    fail_start = False

    def __init__(self, session, api_id, api_hash):
        self.forwarded: list = []
        self.offset_date = None
        _Client.instances.append(self)

    async def start(self):
        if _Client.fail_start:
            raise RuntimeError("세션 없음(테스트)")

    async def disconnect(self):
        return None

    async def get_input_entity(self, ref):
        return _Peer(-100111 if ref == "Badonions" else -100222)

    async def iter_messages(self, source, offset_date=None, reverse=False):
        self.offset_date = offset_date
        for m in sorted(_Client.messages, key=lambda m: m.date):
            if offset_date is None or m.date >= offset_date:
                yield m

    async def forward_messages(self, dest, ids, from_peer=None):
        self.forwarded.append(list(ids))


def _fake_telethon() -> dict:
    tl = types.ModuleType("telethon")
    tl.TelegramClient = _Client
    utils = types.ModuleType("telethon.utils")
    utils.get_peer_id = lambda p: getattr(p, "id", 0)
    tl.utils = utils
    errors = types.ModuleType("telethon.errors")

    class FloodWaitError(Exception):
        seconds = 0

    errors.FloodWaitError = FloodWaitError
    message = types.ModuleType("telethon.tl.custom.message")
    message.Message = _Msg
    return {"telethon": tl, "telethon.utils": utils,
            "telethon.errors": errors,
            "telethon.tl": types.ModuleType("telethon.tl"),
            "telethon.tl.custom": types.ModuleType("telethon.tl.custom"),
            "telethon.tl.custom.message": message}


@pytest.fixture
def backfill(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_TELETHON_API_ID", "1")
    monkeypatch.setenv("TRADE_TELETHON_API_HASH", "stub")
    monkeypatch.setenv("TRADE_CHANNEL_CHAT_IDS", "-100222")
    monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TRADE_BOT_TOKEN", raising=False)
    for name, mod in _fake_telethon().items():
        monkeypatch.setitem(sys.modules, name, mod)
    # 스크립트가 import 시점에 하는 일 셋을 격리한다: 실제 `.env` 를 읽어
    # 환경을 바꾸고(#294 — 운영 토큰이 다음 테스트로 샌다), sys.path 에 레포
    # 루트를 끼우고, 의존 모듈을 처음 import 한다(사설 모듈 밖에 남는다).
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    from trade import ignored, tg_entities  # noqa: F401 — 먼저 진짜로 올린다
    spec = importlib.util.spec_from_file_location(
        "_backfill_badonion_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)            # sys.modules 에 등록하지 않는다
    notes: list = []
    monkeypatch.setattr(mod, "_notify", notes.append)
    monkeypatch.setattr(mod, "_current_pause", lambda n: 0)

    async def _no_disk_pause(*a, **k):     # 샌드박스 디스크 할당량에 안 멈추게
        return None

    monkeypatch.setattr(mod, "_maybe_pause_for_disk", _no_disk_pause)
    monkeypatch.setattr(_Client, "messages", [])
    monkeypatch.setattr(_Client, "instances", [])
    monkeypatch.setattr(_Client, "fail_start", False)
    mod._test_notes = notes
    mod._test_state = tmp_path / srcs.SYNC_STATE_NAME
    return mod


def _run(mod, monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["backfill_badonion.py", *argv])
    with pytest.raises(SystemExit) as ex:
        mod.main()
    return ex.value.code


def _seed(days_old_kri=20):
    now = datetime.now(timezone.utc)
    _Client.messages = [
        _Msg(501, _KRI, now - timedelta(days=days_old_kri)),
        _Msg(777, _CHATTER, now - timedelta(days=1)),
    ]


def test_first_sync_recovers_a_caption_the_default_window_cannot_reach(
        backfill, monkeypatch, caplog):
    """기록이 없으면(= 이 배포 뒤 첫 실행) 40일을 훑어 20일 전 kri 캡션을
    포워드하고, 성공했으니 지문을 기록한다. 다음 실행은 기본 3일로 돌아가
    같은 캡션에 **닿지도 않는다** — 그게 09-16~24 에 일어난 일이다."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch) == 0
    client = _Client.instances[-1]
    assert client.forwarded == [[501]], "배포 전에 버려진 캡션을 못 주웠다"
    text = caplog.text
    assert f"using {srcs.RECOVERY_LOOKBACK_DAYS}-day lookback" in text
    assert "기록 없음" in text
    # 포워드할 유닛은 **어느 소스인지**까지 한 줄로 말한다(#356·#403).
    line = next(l for l in text.splitlines() if "to-forward unit" in l)
    assert "[한국 수입(회사별)]" in line and "텔레칩스" in line
    assert "AMD" not in text.split("to-forward unit")[1].split("\n")[0]
    rec = json.loads(backfill._test_state.read_text(encoding="utf-8"))
    assert rec["relevance_fp"] == srcs.relevance_fingerprint()

    caplog.clear()
    assert _run(backfill, monkeypatch) == 0
    client = _Client.instances[-1]
    assert f"using {srcs.DEFAULT_LOOKBACK_DAYS}-day lookback" in caplog.text
    assert client.forwarded == [], "기본 창이 20일 전 캡션까지 훑었다"
    assert "to-forward unit" not in caplog.text        # 평소엔 0줄
    assert (datetime.now(timezone.utc) - client.offset_date) < timedelta(days=4)


def test_a_dry_run_lists_what_it_would_forward_and_records_nothing(
        backfill, monkeypatch, caplog):
    """09-22 에 이 줄이 없어서 '원천 미게시' 로 오판했다(#403). 진단은
    운영 상태를 바꾸면 안 된다 — 기록도, 포워드도 없다(#264·#283)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--dry-run") == 0
    assert _Client.instances[-1].forwarded == []
    assert "to-forward unit" in caplog.text
    assert "[한국 수입(회사별)]" in caplog.text
    assert not backfill._test_state.exists(), "dry-run 이 지문을 기록했다"


def test_a_failed_sync_does_not_record_so_the_next_tick_retries(
        backfill, monkeypatch):
    """실패한 회수를 기록하면 다 된 줄 알고 다시 안 훑는다 — 수렴이 깨진다."""
    _seed()
    _Client.fail_start = True
    assert _run(backfill, monkeypatch) == 1
    assert not backfill._test_state.exists()
    assert any("시작 실패" in n for n in backfill._test_notes)


def test_a_record_write_failure_warns_but_keeps_the_sync_successful(
        backfill, monkeypatch, caplog):
    """포워드까지 끝난 동기화를 기록 실패 하나로 '실패' 로 끝내지 않는다 —
    대가는 다음 틱의 40일 스캔 한 번이다. 그렇다고 조용히 넘기지도 않는다(#12)."""
    _seed()

    def _boom(*a, **k):
        raise OSError("디스크 가득(테스트)")

    monkeypatch.setattr(srcs, "record_sync", _boom)
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch) == 0
    assert _Client.instances[-1].forwarded == [[501]]
    assert "지문 기록 실패" in caplog.text and "OSError" in caplog.text


def test_an_explicit_window_is_used_as_given_and_not_recorded(
        backfill, monkeypatch, caplog):
    """사람이 고른 창이 회수를 대신했다고 가정하지 않는다 — 좁을 수 있다."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--lookback-days", "30") == 0
    assert _Client.instances[-1].forwarded == [[501]]
    assert "using 30-day lookback" in caplog.text
    assert "명시" in caplog.text
    assert not backfill._test_state.exists()


def test_an_explicit_window_covering_the_recovery_records_so_it_converges(
        backfill, monkeypatch, caplog):
    """자동 회수가 후보 상한에 걸리면 알림이 명시 실행을 시킨다 — 그 넓은
    실행이 성공하면 기록돼야 6시간마다 같은 중단이 반복되지 않는다(#171)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--lookback-days", "45") == 0
    assert _Client.instances[-1].forwarded == [[501]]
    assert "덮으므로" in caplog.text
    rec = json.loads(backfill._test_state.read_text(encoding="utf-8"))
    assert rec["relevance_fp"] == srcs.relevance_fingerprint()


def test_a_changed_filter_fingerprint_triggers_one_more_recovery(
        backfill, monkeypatch, caplog):
    """파서(가 사는 모듈)를 고친 배포 → 다음 동기화가 다시 넓게 훑는다."""
    _seed()
    backfill._test_state.write_text(
        json.dumps({"relevance_fp": "0000000000"}), encoding="utf-8")
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch) == 0
    assert _Client.instances[-1].forwarded == [[501]]
    assert "0000000000 →" in caplog.text
    rec = json.loads(backfill._test_state.read_text(encoding="utf-8"))
    assert rec["relevance_fp"] == srcs.relevance_fingerprint()
