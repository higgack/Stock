"""나쁜양파 동기화 — 파서가 생기기 전에 버려진 캡션의 **자동 회수**(실수 #403).

2026-09-16 사용자가 채널에서 본 한국 수입 회사별 캡션(텔레칩스, 스크린샷
첨부 — `kr_stock_imports` 독스트링)이 09-24 까지 보드에 없었다. 관련성 필터
= 파서라 파서 배포 전 캡션은 리스너가 버리고, 6시간 동기화는 3일만 본다.
나는 09-22 에 그걸 '원천 미게시' 로 오판했다 — 그 dry-run 이 **파서는 받는데
아직 inbox 에 없는 유닛**을 찍지 않았기 때문이다.

⚠️ 왜 가짜 telethon 으로 태우나: 형제 `test_backfill_beon` 은 telethon 이
없으면 **통째로 skip** 이라 게이트(`make test`)에선 한 번도 안 돈다(못 보는
축). 판정은 `badonion_sources.sync_plan`·`finish_recovery` 가 순수하게 잰다 —
여기서는 그 판정이 `main()` 에서 **창·기록·출력으로 이어지는지**를 값으로
본다(#20 헬퍼 테스트는 배선을 못 잰다). 스크립트는 sys.modules 에 **등록하지
않는 사설 모듈**로 읽고, 가짜는 `monkeypatch` 로만 꽂는다(#397 누수 가드).
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
# ⚠️ env 를 바꾸기 **전에** 올린다 — `trade.ignored` 는 import 시점에
# TRADE_DATA_DIR 로 기본 경로를 굳힌다. 이 파일이 첫 importer 면 픽스처의
# tmp 경로가 프로세스 끝까지 남는다(독립 리뷰 L4).
from trade import ignored as _ignored  # noqa: F401
from trade import tg_entities as _tg  # noqa: F401

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_badonion.py"

# kri 캡션 — `kr_stock_imports` 독스트링의 사용자 스크린샷 재구성(#155: 실물
# 바이트가 아니다. 같은 채널의 품목판 파서 9개가 같은 `▶️` 로 운영 중이지만
# 금액판은 다른 템플릿이라 간접 증거다 — 실물 판정은 `--find` 가 한다).
_KRI = ("**🇰🇷 8월 수입 한국**\n\n**▶️ 텔레칩스 — 차량용 AP·프로세서**\n\n"
        "**26년08월: $2,175.2M  (+49.4% YoY)  (+7.3% MoM)**")
_KRI2 = ("**🇰🇷 8월 수입 한국**\n\n**▶️ 합성회사 — 합성 품목**\n\n"
         "**26년08월: $10.0M  (+1.0% YoY)  (+1.0% MoM)**")
_CHATTER = "AMD 신고가 돌파"


class _Peer:
    def __init__(self, pid):
        self.id = pid


class _Msg:
    """`text` = 파서 입력(마크다운 되붙임), `raw_text` = 서버 원문 — 둘이
    다를 수 있다(형제 `diagnose_badonion` 독스트링). 기본은 같게 둔다."""

    def __init__(self, mid, text, date, *, raw=None, grouped_id=None):
        self.id, self.text, self.date = mid, text, date
        self.raw_text = text if raw is None else raw
        self.fwd_from = None
        self.grouped_id = grouped_id


class _Client:
    """TelegramClient 의 이 스크립트가 쓰는 표면만 — 읽기·포워드를 기록한다."""
    messages: list = []
    instances: list = []
    fail_start = False
    fail_forward_ids: set = set()

    def __init__(self, session, api_id, api_hash):
        self.forwarded: list = []
        self.offset_date = None
        self.session = session
        f = Path(f"{session}.session")
        self.session_bytes = f.read_bytes() if f.exists() else None
        _Client.instances.append(self)

    async def start(self):
        if _Client.fail_start:
            raise RuntimeError("세션 없음(테스트)")
        # 진짜 telethon 도 접속하면 세션 SQLite 를 갱신한다 — 그래서 dry-run 이
        # 라이브 파일을 열면 6시간 타이머와 잠금 경합한다(#403 2차 리뷰).
        f = Path(f"{self.session}.session")
        if f.exists():
            with f.open("ab") as fh:
                fh.write(b"+touched")

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
        if set(ids) & _Client.fail_forward_ids:
            raise RuntimeError("포워드 실패(테스트 — 일시 장애일 수도 삭제일 수도)")
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
    # 스크립트가 import 시점에 하는 일을 격리한다: 실제 `.env` 를 읽어 환경을
    # 바꾸고(#294 — 운영 토큰이 다음 테스트로 샌다), sys.path 에 레포 루트를
    # 끼운다. 의존 모듈은 이 파일 머리에서 이미 진짜로 올렸다.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "path", list(sys.path))
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
    # 세션은 cwd 상대 경로다 — 테스트가 레포 루트에 세션 파일을 만들거나
    # 운영자의 것을 읽지 않게 tmp 로 돌린다(#30).
    monkeypatch.setattr(mod, "SESSION_PATH", str(tmp_path / ".badonion-session"))
    monkeypatch.setattr(_Client, "messages", [])
    monkeypatch.setattr(_Client, "instances", [])
    monkeypatch.setattr(_Client, "fail_start", False)
    monkeypatch.setattr(_Client, "fail_forward_ids", set())
    mod._test_notes = notes
    mod._test_state = tmp_path / srcs.SYNC_STATE_NAME
    mod._test_inbox = tmp_path / "inbox.jsonl"
    return mod


def _run(mod, monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["backfill_badonion.py", *argv])
    with pytest.raises(SystemExit) as ex:
        mod.main()
    return ex.value.code


def _ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _seed(days_old_kri=20):
    _Client.messages = [
        _Msg(501, _KRI, _ago(days_old_kri)),
        _Msg(777, _CHATTER, _ago(1)),
    ]


def _state(mod) -> dict:
    return json.loads(mod._test_state.read_text(encoding="utf-8"))


def _find_lines(text: str) -> dict:
    """`find <갈래> unit …` 줄을 갈래별로 — 갈래 이름만 세는 단언은 글이
    갈래 사이에서 뒤바뀌어도 통과한다(2차 리뷰 B8·B9)."""
    out: dict = {}
    for line in text.splitlines():
        body = line.split("find ", 1)[-1] if "find " in line else ""
        kind, sep, _rest = body.partition(" unit ")
        if sep and kind in ("to-forward", "irrelevant", "already-in-inbox",
                            "ignored"):
            out.setdefault(kind, []).append(line)
    return out


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
    fp = srcs.relevance_fingerprint()
    assert _state(backfill)["relevance_fp"] == fp
    assert f"관련성 필터 지문 {fp} 기록" in text     # 기록했다고 말한다(M14)

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


def test_show_irrelevant_alone_is_a_dry_run_and_records_nothing(
        backfill, monkeypatch, caplog):
    """`--show-irrelevant` 는 `--dry-run` 없이도 읽기 전용이다 — 기록 조건이
    `args.dry_run` 만 보면 이 진단이 지문을 기록한다(리뷰 M03 생존)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--show-irrelevant") == 0
    assert _Client.instances[-1].forwarded == []
    assert "dry-run 강제" in caplog.text
    assert not backfill._test_state.exists()


def test_a_failed_sync_does_not_record_so_the_next_tick_retries(
        backfill, monkeypatch):
    """실패한 회수를 기록하면 다 된 줄 알고 다시 안 훑는다 — 수렴이 깨진다."""
    _seed()
    _Client.fail_start = True
    assert _run(backfill, monkeypatch) == 1
    assert not backfill._test_state.exists()
    assert any("시작 실패" in n for n in backfill._test_notes)


def test_a_candidate_cap_abort_does_not_record_and_says_how_to_stop(
        backfill, monkeypatch):
    """rc 2(후보 상한)는 성공이 아니다(리뷰 M04 생존) — 그리고 이 중단은 6시간
    마다 반복되므로 알림이 **무엇을 돌려야 멈추는지** 적는다(리뷰 L2)."""
    _seed()
    assert _run(backfill, monkeypatch, "--max-candidates", "1") == 2
    assert not backfill._test_state.exists()
    note = backfill._test_notes[-1]
    assert f"--lookback-days {srcs.RECOVERY_LOOKBACK_DAYS}" in note
    # '한 번 돌리면 멈춘다' 만 적으면 그 명시 실행이 일부 실패할 때 거짓이다 —
    # 언제 기록되는지(일부 실패한 실행의 횟수)와 무엇은 안 세는지를 같이 적는다
    # (2차 리뷰 — 이 경로는 finish_recovery 에 안 닿아 횟수가 안 오른다).
    assert "일부 실패" in note and f"{srcs.RECOVERY_MAX_ATTEMPTS}회째" in note
    assert "중단된 실행은 세지 않는다" in note


def test_a_partly_failed_recovery_retries_then_gives_up_boundedly(
        backfill, monkeypatch, caplog):
    """포워드가 실패한 회수는 기록하지 않는다 — 일시 장애도 같은 경로로 오기
    때문이다(리뷰 M1: 옛 판은 기록해 다음 틱이 3일로 돌아가 캡션을 영영
    잃었다). 정말 지워진 메시지는 매번 실패하므로 3회째에는 기록해 무한히
    40일을 훑지 않는다(#171)."""
    _seed()
    _Client.fail_forward_ids = {501}
    caplog.set_level("INFO")
    for attempt in range(1, srcs.RECOVERY_MAX_ATTEMPTS):
        caplog.clear()
        assert _run(backfill, monkeypatch) == 0
        assert f"using {srcs.RECOVERY_LOOKBACK_DAYS}-day lookback" in caplog.text
        st = _state(backfill)
        assert "relevance_fp" not in st, f"{attempt}회째 실패인데 기록했다"
        assert st["retry"]["count"] == attempt
        assert "포워드 실패 1건" in caplog.text
    caplog.clear()
    assert _run(backfill, monkeypatch) == 0
    st = _state(backfill)
    assert st["relevance_fp"] == srcs.relevance_fingerprint()
    assert "retry" not in st
    # 실행별로 세므로 '영구 실패' 로 단정하지 않는다(#165, 2차 리뷰).
    assert "재시도 상한" in caplog.text and "영구 실패" not in caplog.text


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


@pytest.mark.parametrize("argv", [
    ("--since", "{d4}"),                           # 리뷰 M05 생존
    ("--to", "{d1}"),                              # 리뷰 M06 생존
    ("--since", "{d4}", "--lookback-days", "45"),  # 리뷰 M2 — since 가 이긴다
])
def test_narrow_explicit_windows_never_record(backfill, monkeypatch, caplog,
                                              argv):
    _seed()
    caplog.set_level("INFO")
    fmt = {"d4": _ago(4).date().isoformat(), "d1": _ago(1).date().isoformat()}
    assert _run(backfill, monkeypatch, *(a.format(**fmt) for a in argv)) == 0
    assert _Client.instances[-1].forwarded == []   # 20일 전 캡션엔 안 닿는다
    assert not backfill._test_state.exists()
    assert "덮지 않아" in caplog.text               # 왜 기록 안 하는지 말한다(L2)


def test_lookback_zero_is_an_explicit_window(backfill, monkeypatch, caplog):
    """0 을 '없음' 으로 읽으면 회수 창이 사람이 고른 창을 덮는다(리뷰 M08)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--lookback-days", "0") == 0
    assert "using 0-day lookback" in caplog.text
    assert not backfill._test_state.exists()


def test_an_empty_since_falls_back_to_the_default_window(
        backfill, monkeypatch, caplog):
    """`--since ""` 는 '안 줌' 이다 — 옛 판처럼 기본 창으로 가야 하고, 명시로
    읽으면 창이 None 이 돼 TypeError 로 죽는다(리뷰 L1)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--since", "") == 0
    assert f"using {srcs.RECOVERY_LOOKBACK_DAYS}-day lookback" in caplog.text


def test_an_explicit_window_covering_the_recovery_records_so_it_converges(
        backfill, monkeypatch, caplog):
    """자동 회수가 후보 상한에 걸리면 알림이 명시 실행을 시킨다 — 그 넓은
    실행이 성공하면 기록돼야 6시간마다 같은 중단이 반복되지 않는다(#171)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--lookback-days", "45") == 0
    assert _Client.instances[-1].forwarded == [[501]]
    assert "덮으므로" in caplog.text
    assert _state(backfill)["relevance_fp"] == srcs.relevance_fingerprint()


def test_the_automatic_recovery_stops_before_a_flood(
        backfill, monkeypatch, caplog):
    """새 파서가 너무 넓게 잡으면 40일치가 비공개 채널에 한 번에 쏟아진다 —
    자동 회수는 상한에서 멈추고 묻는다. 사람이 명시한 창엔 상한이 없다(L5)."""
    monkeypatch.setattr(srcs, "RECOVERY_MAX_UNITS", 1)
    _Client.messages = [_Msg(501, _KRI, _ago(20)), _Msg(502, _KRI2, _ago(19))]
    assert _run(backfill, monkeypatch) == 2
    assert _Client.instances[-1].forwarded == []
    assert not backfill._test_state.exists()
    assert "자동 회수 중단" in backfill._test_notes[-1]
    assert "한국 수입(회사별) 2" in backfill._test_notes[-1]
    # 시키는 명시 실행이 회수 창을 **덮어야** 기록돼 알림이 멈춘다(2차 리뷰 B32).
    assert (f"--lookback-days {srcs.RECOVERY_LOOKBACK_DAYS}</code>"
            in backfill._test_notes[-1])
    assert _run(backfill, monkeypatch, "--lookback-days", "40") == 0
    assert _Client.instances[-1].forwarded == [[501], [502]]
    assert _state(backfill)["relevance_fp"] == srcs.relevance_fingerprint()


@pytest.mark.parametrize("target,exc", [
    ("record_sync", OSError("디스크 가득(테스트)")),
    # OSError 만 잡던 옛 판은 깨진 상태 파일의 ValueError 가 새 **포워드를 끝낸**
    # 동기화가 트레이스백으로 끝났고, 다음 틱도 같은 자리에서 죽었다(2차 리뷰 P6).
    ("finish_recovery", ValueError("깨진 상태 파일(테스트)")),
])
def test_a_record_write_failure_warns_but_keeps_the_sync_successful(
        backfill, monkeypatch, caplog, target, exc):
    """포워드까지 끝난 동기화를 기록 실패 하나로 '실패' 로 끝내지 않는다 —
    대가는 다음 틱의 40일 스캔 한 번이다. 그렇다고 조용히 넘기지도 않는다(#12)."""
    _seed()

    def _boom(*a, **k):
        raise exc

    monkeypatch.setattr(srcs, target, _boom)
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch) == 0
    assert _Client.instances[-1].forwarded == [[501]]
    assert "지문 기록 실패" in caplog.text
    assert type(exc).__name__ in caplog.text


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
    assert _state(backfill)["relevance_fp"] == srcs.relevance_fingerprint()


def test_find_names_where_each_caption_went(backfill, monkeypatch, caplog):
    """캡션 하나가 어디로 갔는지 **갈래로** 말한다 — 09-22 에 손으로 조립한
    grep 이 어순 때문에 결정적인 줄을 걸렀다(#403, 리뷰 H1). 전문에서 찾고,
    읽기 전용이다(포워드·기록 없음)."""
    backfill._test_inbox.write_text(json.dumps(
        {"forward_origin_chat_id": -100111,
         "forward_origin_message_id": 601}) + "\n", encoding="utf-8")
    _Client.messages = [
        _Msg(501, _KRI, _ago(20)),                              # to-forward
        _Msg(600, "텔레칩스 신고가 돌파", _ago(10)),               # irrelevant
        _Msg(601, _KRI.replace("8월", "7월"), _ago(30)),        # 이미 받음
        _Msg(602, "이달의 주요 기업 수출데이터 — 텔레칩스", _ago(5)),  # ignored
    ]
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "텔레칩스") == 0
    text = caplog.text
    lines = _find_lines(text)
    # 갈래마다 **어느 글**이 실렸는지까지 — 갈래 이름만 세면 두 갈래의 글이
    # 서로 뒤바뀌어도 통과한다(2차 리뷰 B8·B9).
    want = {"to-forward": "8월 수입", "irrelevant": "신고가 돌파",
            "already-in-inbox": "7월 수입", "ignored": "이달의 주요 기업"}
    assert sorted(lines) == sorted(want)
    for kind, head in want.items():
        assert len(lines[kind]) == 1, (kind, lines[kind])
        assert head in lines[kind][0], (kind, lines[kind])
    # 포워드할 유닛이 irrelevant 로 **또** 찍히지 않는다(B16).
    assert sum(len(v) for v in lines.values()) == 4
    assert "를 담은 글이 이 창" not in text          # 찾았으면 '없다' 는 없다(B30)
    assert _Client.instances[-1].forwarded == []
    assert not backfill._test_state.exists()
    caplog.clear()
    assert _run(backfill, monkeypatch, "--find", "없는회사") == 0
    assert "'없는회사' 를 담은 글이 이 창" in caplog.text     # 대조 0건도 말한다


# ── 2차 독립 리뷰(da4e430..abf8fa6) — 동작 결함 5건 + 생존 뮤테이션 ─────────
@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_find_is_refused_before_any_sync(backfill, monkeypatch, value):
    """`--find ""` 는 `bool("")` 이 False 라 옛 판에선 dry-run 을 강제하지 않고
    **실제 동기화**(포워드·기록)를 돌렸다 — `--find "$X"` 에서 X 가 비면 그렇게
    된다(2차 리뷰 P1). 접속하기도 전에 거부한다."""
    _seed()
    assert _run(backfill, monkeypatch, "--find", value) == 2   # argparse 오류
    assert _Client.instances == [], "빈 --find 가 텔레그램에 접속했다"
    assert not backfill._test_state.exists()


def test_a_partial_failure_of_a_covering_run_is_retried_when_the_fp_was_recorded(
        backfill, monkeypatch, caplog):
    """지문이 **이미 기록된** 뒤 명시 회수가 일부 실패하면 재시도 표식만 남는다.
    옛 판은 기록된 지문을 먼저 봐 다음 자동 동기화가 3일로 돌아갔고, "다음
    동기화가 다시 시도한다" 는 로그가 거짓이 됐다 — 20일 전 캡션은 영영 안
    돌아왔다(2차 리뷰 P2)."""
    _seed()
    srcs.record_sync(backfill._test_state, srcs.relevance_fingerprint())
    _Client.fail_forward_ids = {501}
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--lookback-days",
                str(srcs.RECOVERY_LOOKBACK_DAYS)) == 0
    assert f"최근 {srcs.RECOVERY_LOOKBACK_DAYS}일을 다시 훑는다" in caplog.text
    assert _state(backfill)["retry"]["count"] == 1
    _Client.fail_forward_ids = set()
    caplog.clear()
    assert _run(backfill, monkeypatch) == 0
    assert f"using {srcs.RECOVERY_LOOKBACK_DAYS}-day lookback" in caplog.text
    assert "재시도 1회째" in caplog.text
    assert _Client.instances[-1].forwarded == [[501]]
    assert "retry" not in _state(backfill)


def test_a_dry_run_never_stops_at_the_candidate_cap_nor_notifies(
        backfill, monkeypatch, caplog):
    """상한은 **포워드 홍수**를 막는 장치다. 옛 판은 dry-run·`--find` 도 거기서
    rc 2 로 멈춰 찾은 결과를 한 줄도 못 찍고, 읽기 전용 진단이 사람 폰에
    '동기화 중단' 알림까지 보냈다(2차 리뷰 P11·B5b)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "텔레칩스",
                "--max-candidates", "1") == 0
    assert backfill._test_notes == []
    assert "dry-run 이라 멈추지 않는다" in caplog.text
    assert "to-forward" in _find_lines(caplog.text)
    assert _Client.instances[-1].forwarded == []


def test_a_dry_run_over_the_recovery_cap_does_not_notify(
        backfill, monkeypatch, caplog):
    """자동 회수의 포워드 상한도 같다 — dry-run 은 포워드하지 않으니 알릴 일이
    없다(2차 리뷰 B5b)."""
    monkeypatch.setattr(srcs, "RECOVERY_MAX_UNITS", 1)
    _Client.messages = [_Msg(501, _KRI, _ago(20)), _Msg(502, _KRI2, _ago(19))]
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--dry-run") == 0
    assert backfill._test_notes == []
    assert "recovery cap" in caplog.text


def test_a_dry_run_does_not_promise_to_record(backfill, monkeypatch, caplog):
    """사유가 '성공하면 기록한다' 로 끝나도 dry-run 은 기록하지 않는다 — 로그가
    안 할 일을 약속하면 거짓이다(2차 리뷰 P12·#55)."""
    _seed()
    caplog.set_level("INFO")
    for argv in (("--dry-run",),
                 ("--dry-run", "--lookback-days", "45"),
                 ("--show-irrelevant",)):
        caplog.clear()
        assert _run(backfill, monkeypatch, *argv) == 0
        assert "dry-run 이라 포워드·기록하지 않는다" in caplog.text, argv
    assert not backfill._test_state.exists()
    caplog.clear()
    assert _run(backfill, monkeypatch) == 0            # 반대 증거(#25)
    assert "dry-run 이라 포워드·기록하지 않는다" not in caplog.text


def test_the_candidate_cap_lets_exactly_n_through(backfill, monkeypatch):
    """상한은 '넘으면' 이다 — N 개면 포워드한다(2차 리뷰 B4)."""
    _Client.messages = [_Msg(501, _KRI, _ago(20)), _Msg(502, _KRI2, _ago(19))]
    assert _run(backfill, monkeypatch, "--max-candidates", "2") == 0
    assert _Client.instances[-1].forwarded == [[501], [502]]
    assert backfill._test_notes and "중단" not in backfill._test_notes[-1]


def test_a_manual_cap_abort_does_not_claim_an_automatic_recovery(
        backfill, monkeypatch):
    """'자동 회수 중' 안내는 자동으로 넓힌 창에만 — 사람이 3일을 명시한 실행에
    붙이면 없는 회수를 멈추라고 시킨다(2차 리뷰 B6)."""
    _Client.messages = [_Msg(501, _KRI, _ago(1)), _Msg(777, _CHATTER, _ago(1))]
    assert _run(backfill, monkeypatch, "--lookback-days", "3",
                "--max-candidates", "1") == 2
    note = backfill._test_notes[-1]
    assert "cap 1개 초과" in note
    assert "자동 회수" not in note


def test_the_recovery_cap_note_escapes_source_labels(backfill, monkeypatch):
    """알림은 `parse_mode=HTML` 이다 — 라벨의 `<`·`&` 를 그대로 싣으면 전송이
    400 으로 실패해 **알림이 통째로 사라진다**(실수 #7, 2차 리뷰 B23)."""
    monkeypatch.setattr(srcs, "RECOVERY_MAX_UNITS", 1)
    monkeypatch.setattr(srcs, "relevance_breakdown", lambda units: ["a<b&c 2"])
    _Client.messages = [_Msg(501, _KRI, _ago(20)), _Msg(502, _KRI2, _ago(19))]
    assert _run(backfill, monkeypatch) == 2
    note = backfill._test_notes[-1]
    assert "a&lt;b&amp;c 2" in note and "a<b&c" not in note


def test_an_empty_to_is_not_an_explicit_window(backfill, monkeypatch, caplog):
    """`--to ""` 는 '안 줌' 이다 — 명시로 읽으면 좁은 명시 창이 돼 자동 회수가
    사라진다(2차 리뷰 B18, `--since ""` 의 짝)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--to", "") == 0
    assert f"using {srcs.RECOVERY_LOOKBACK_DAYS}-day lookback" in caplog.text
    assert _Client.instances[-1].forwarded == [[501]]


def test_find_reads_past_the_head_and_both_texts_case_insensitively(
        backfill, monkeypatch, caplog):
    """찾는 자리는 머리 160자가 아니라 **전문**이고(2차 리뷰 B10), 파서 입력
    `text` 와 서버 원문 `raw_text` 둘 다 대소문자 무시로 본다 — 마크다운 표식이
    낱말을 가르면 `text` 에서만 찾을 때 놓친다(형제 `diagnose_badonion._matches`
    와 같은 규약, #38)."""
    _Client.messages = [
        _Msg(700, "잡담 " * 100 + "끝자락단어", _ago(3)),
        _Msg(701, "Tele**chips** 소식", _ago(2), raw="Telechips 소식"),
    ]
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "끝자락단어") == 0
    assert len(_find_lines(caplog.text).get("irrelevant", [])) == 1
    caplog.clear()
    assert _run(backfill, monkeypatch, "--find", "TELECHIPS") == 0
    lines = _find_lines(caplog.text).get("irrelevant", [])
    assert len(lines) == 1 and "Tele**chips**" in lines[0]


def test_find_searches_every_album_member(backfill, monkeypatch, caplog):
    """앨범은 아무 멤버나 캡션을 가질 수 있다 — 첫 멤버만 보면 둘째 칸에만 있는
    글자를 '없다' 고 한다(2차 리뷰 B27)."""
    _Client.messages = [
        _Msg(511, _KRI, _ago(4), grouped_id=9),
        _Msg(512, "앨범 둘째 칸의 전용단어", _ago(4), grouped_id=9),
    ]
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "전용단어") == 0
    assert len(_find_lines(caplog.text).get("to-forward", [])) == 1
    assert "를 담은 글이 이 창" not in caplog.text


@pytest.mark.parametrize("kind", ["irrelevant", "already-in-inbox", "ignored"])
def test_a_hit_in_any_branch_is_not_reported_as_missing(
        backfill, monkeypatch, caplog, kind):
    """어느 갈래에서 찾았든 찾은 것이다 — to-forward 만 세면 드랍된 캡션을
    찾아 놓고 '이 창에 없다' 고 한다. 그게 09-22 의 오판 모양이다(2차 리뷰 B29)."""
    if kind == "already-in-inbox":
        backfill._test_inbox.write_text(json.dumps(
            {"forward_origin_chat_id": -100111,
             "forward_origin_message_id": 800}) + "\n", encoding="utf-8")
    text = {"irrelevant": "갈래시험 잡담",
            "already-in-inbox": _KRI2.replace("합성 품목", "갈래시험"),
            "ignored": "이달의 주요 기업 수출데이터 — 갈래시험"}[kind]
    _Client.messages = [_Msg(800, text, _ago(2))]
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "갈래시험") == 0
    assert list(_find_lines(caplog.text)) == [kind]
    assert "를 담은 글이 이 창" not in caplog.text


def test_a_miss_names_the_exact_window_and_how_many_it_read(
        backfill, monkeypatch, caplog):
    """대조 0건은 창과 훑은 수를 **정확히** 말해야 '창 밖' 인지 '원천에 없음'
    인지 갈린다(#54, 2차 리뷰 B15). 픽스처가 약하면 눈이 먼다(#91c): 훑은 수가
    후보 수와 같으면 후보를 세는 변형이, `--to` 가 오늘이면 상한을 '오늘' 로
    바꾸는 변형이 통과한다 — 그래서 무시 목록 글(훑었지만 후보 아님)과 `--to`
    뒤의 글(안 훑음)을 같이 둔다."""
    _Client.messages = [
        _Msg(501, _KRI, _ago(20)),
        _Msg(602, "이달의 주요 기업 수출데이터 — 무시 목록", _ago(10)),
        _Msg(600, "AMD 신고가 돌파", _ago(5)),
        _Msg(777, _CHATTER, _ago(1)),                   # --to 뒤 — 안 훑는다
    ]
    since, to = _ago(45).date().isoformat(), _ago(2).date().isoformat()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "없는회사",
                "--since", since, "--to", to) == 0
    assert f"이 창({since} → {to}, 메시지 3개)에 없다" in caplog.text


def _diagnose_flags() -> set:
    """형제 진단 CLI 가 **실제로 받는** 플래그 — 안내 명령의 플래그를 지어내면
    사용자가 `unrecognized arguments` 로 한 번 더 헛돈다(#371). telethon 없이
    import 할 수 없으니 AST 로 읽는다."""
    import ast
    tree = ast.parse((_SCRIPT.parent / "diagnose_badonion.py").read_text(
        encoding="utf-8"))
    return {a.value for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add_argument"
            for a in n.args if isinstance(a, ast.Constant)
            and str(a.value).startswith("--")}


def test_find_points_at_the_sibling_that_says_why_a_caption_was_dropped(
        backfill, monkeypatch, caplog):
    """`--find` 는 **어디로** 갔는지까지다 — **왜** 버려졌는지(원문 repr·필터
    판정)는 형제 `diagnose_badonion` 이 찍는다. 그 안내가 (a) 실제로 있는
    플래그만 쓰고 (b) 그 캡션에 **닿는** 날짜에서 시작해야 한다 — 그 도구는
    `--since` 부터 `--limit`(1000)개만 훑는다(2차 리뷰, #371)."""
    dropped = _Msg(600, "텔레칩스 신고가 돌파", _ago(10))
    _Client.messages = [_Msg(501, _KRI, _ago(30)), dropped]
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--find", "신고가 돌파") == 0
    line = next(l for l in caplog.text.splitlines()
                if "diagnose_badonion" in l)
    # 창 시작일(40일 전)이 아니라 드랍된 그 글의 날짜에서 시작한다.
    assert f"--since {dropped.date.date().isoformat()} " in line
    assert "--grep '신고가 돌파'" in line                      # 셸 인용
    flags = {t for t in line.split() if t.startswith("--")}
    assert flags and flags <= _diagnose_flags(), flags - _diagnose_flags()
    caplog.clear()                    # 반대 증거 — 드랍된 게 없으면 안내도 없다
    assert _run(backfill, monkeypatch, "--find", "차량용") == 0
    assert "diagnose_badonion" not in caplog.text


def test_a_dry_run_talks_through_a_copy_of_the_live_session(
        backfill, monkeypatch, tmp_path):
    """`.badonion-session` 은 6시간 타이머가 쓰는 SQLite 다 — 진단이 원본을
    열면 겹칠 때 한쪽이 `database is locked` 로 죽는다. dry-run 류는 형제
    `diagnose_badonion` 처럼 **복사본**으로 접속하고, 끝나면 지운다(2차 리뷰)."""
    live = Path(f"{backfill.SESSION_PATH}.session")
    live.write_bytes(b"LIVE")
    _seed()
    assert _run(backfill, monkeypatch, "--dry-run") == 0
    client = _Client.instances[-1]
    assert client.session != backfill.SESSION_PATH
    assert client.session_bytes == b"LIVE"          # auth key 를 그대로 가져간다
    assert live.read_bytes() == b"LIVE"             # 원본 미변경
    assert not Path(f"{client.session}.session").exists()   # 복사본은 정리
    assert _run(backfill, monkeypatch) == 0          # 반대 증거(#25): 실제 실행은
    assert _Client.instances[-1].session == backfill.SESSION_PATH   # 원본으로,
    assert live.read_bytes() == b"LIVE+touched"      # 가짜가 정말 파일을 건드린다


def test_a_dry_run_without_a_live_session_logs_in_on_the_live_path(
        backfill, monkeypatch, caplog):
    """원본이 없으면 복사할 게 없다 — 복사본에 로그인하면 실행이 끝날 때 지워져
    로그인이 사라지므로 라이브 경로를 쓴다. 복사가 실패해도 라이브로 돌아가되
    **말한다**(#12)."""
    _seed()
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--dry-run") == 0
    assert _Client.instances[-1].session == backfill.SESSION_PATH
    assert "세션 복사 실패" not in caplog.text     # 없는 걸 복사하려다 경고하지 않는다
    caplog.clear()
    Path(f"{backfill.SESSION_PATH}.session").write_bytes(b"LIVE")

    def _boom(*a, **k):
        raise OSError("복사 실패(테스트)")

    monkeypatch.setattr(backfill.shutil, "copy2", _boom)
    assert _run(backfill, monkeypatch, "--dry-run") == 0
    assert _Client.instances[-1].session == backfill.SESSION_PATH
    assert "dry-run 세션 복사 실패" in caplog.text and "OSError" in caplog.text


def test_a_dry_run_that_fails_to_start_does_not_page_anyone(
        backfill, monkeypatch, caplog):
    """사람이 터미널에서 돌린 진단의 시작 실패를 폰에 '나쁜양파 동기화 — 시작
    실패' 로 보내면 6시간 타이머의 장애로 읽힌다(#82) — 후보 상한과 같은
    원리다(2차 리뷰 P11·#264). 실제 실행은 여전히 알린다(반대 증거는
    `test_a_failed_sync_does_not_record_so_the_next_tick_retries`)."""
    _seed()
    _Client.fail_start = True
    caplog.set_level("INFO")
    assert _run(backfill, monkeypatch, "--dry-run", "--find", "텔레칩스") == 1
    assert backfill._test_notes == []
    assert "진단 실행이라 알리지 않는다" in caplog.text
    assert not backfill._test_state.exists()
