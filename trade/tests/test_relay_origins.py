"""trade.relay_origins — 릴레이가 보증한 재게시 글 기록 (실수 #411, 2026-09-25).

사고: 나쁜양파가 다른 채널(`-1003901069327`)에서 퍼 온 글 27건을 릴레이가 관련 글로 골라
포워드했는데, 텔레그램이 포워드의 포워드에도 **원래 출처**를 달아 봇의 출처 게이트가 전부
'다른 출처' 로 버렸다. 이 모듈이 그 글을 **글 단위로** 보증한다 — 릴레이가 포워드 전에
적고 봇 게이트가 읽는다.

이 파일은 모듈 자체의 계약을 잰다: 읽기는 어떤 바이트에도 안 던진다(#331 — 봇 게이트가
부른다), 쓰기는 락 안에서 **다시 읽어** 합친다(리스너와 백필이 겹쳐 쓴다 — #379), 원자적
교체, 바뀐 것이 없으면 안 쓴다, 오래된 보증은 걷어낸다, 재게시 판정은 채널 원천만. 배선
(백필·리스너·봇·진단)은 각 테스트 파일이 잰다.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from trade import relay_origins as ro

_REPO = Path(__file__).resolve().parents[2]
# 합성 ID — 운영의 실제 재게시 채널(-1003901069327)을 쓰면 운영 기록과 우연히 겹칠 수
# 있는 값이 된다(#393 픽스처에 실재 식별자를 쓰지 말 것).
_SRC = -1009990000001
_SRC2 = -1009990000002
T0 = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


def _chan_msg(post, *, chat=_SRC, title=None):
    """텔레톤 재게시 메시지의 이 모듈이 읽는 표면만 — `fwd_from.from_id` 가 채널."""
    peer = NS(channel_id=abs(chat) - 10 ** 12, id=chat)
    return NS(fwd_from=NS(from_id=peer, channel_post=post),
              forward=NS(chat=NS(title=title)) if title else None)


def _peer_id(p):                     # 테스트의 get_peer_id — 호출부가 넘기는 함수 자리
    return p.id


# ── 읽기: 어떤 바이트에도 안 던진다 ────────────────────────────────────────

def test_a_missing_file_is_the_normal_empty_state(tmp_path):
    """아직 재게시 글을 포워드한 적 없으면 파일이 없다 — 오류가 아니다(사유 "")."""
    assert ro.load(ro.path_in(tmp_path)) == ({}, "")


@pytest.mark.parametrize("raw, why", [
    (b"", "빈 파일"),
    (b"   \n", "빈 파일"),
    (b"{not json", "형식 오류"),
    (b"\xff\xfe\x00garbage", "형식 오류"),
    (b"[1, 2]", "chats 가 없다"),
    (json.dumps({"v": 1}).encode(), "chats 가 없다"),
    (json.dumps({"v": 9, "chats": {}}).encode(), "모르는 판"),
])
def test_an_unreadable_file_never_raises_and_says_why(tmp_path, raw, why):
    """봇 게이트가 매 글마다 부른다 — 던지면 그 글 처리가 예외로 끝나 **아무 흔적 없이**
    사라진다(#331 한 바이트가 보드 셋을 비웠다). 사유는 삼키지 않고 돌려준다(#12)."""
    p = ro.path_in(tmp_path)
    p.write_bytes(raw)
    pairs, err = ro.load(p)
    assert pairs == {} and why in err, (pairs, err)


def test_a_directory_in_place_of_the_file_is_a_reason_not_a_crash(tmp_path):
    p = ro.path_in(tmp_path)
    p.mkdir()
    pairs, err = ro.load(p)
    assert pairs == {} and "읽기 실패" in err, err


def test_bad_entries_are_skipped_counted_and_the_rest_kept(tmp_path):
    p = ro.path_in(tmp_path)
    p.write_text(json.dumps({"v": 1, "chats": {
        "x": {"posts": {"1": {}}},                                  # 채널 키가 숫자가 아니다
        str(_SRC): {"title": "원채널", "posts": {
            "z": {},                                                # 글 키가 숫자가 아니다
            "7": {"at": "2026-09-25T10:00:00+00:00", "by": "backfill"},
            "8": {"at": "2026-09-25T10:00:00"},                     # 시간대 없음 → at 모름
            "9": "문자열"}},                                        # 항목이 dict 가 아니다
        str(_SRC2): "채널 항목이 dict 가 아니다"}}), encoding="utf-8")
    pairs, err = ro.load(p)
    assert set(pairs) == {(_SRC, 7), (_SRC, 8), (_SRC, 9)}, pairs
    assert pairs[(_SRC, 7)] == {"at": T0, "by": "backfill", "title": "원채널"}
    assert pairs[(_SRC, 8)]["at"] is None          # 시간대 없는 시각은 가정하지 않는다(#165)
    assert "항목 3개를 못 읽었다" in err and "나머지 3건" in err, err


# ── 쓰기: 합치기 · 첫 시각 · 원자적 · 안 바뀌면 안 쓴다 ─────────────────────

def test_vouch_round_trips_and_keeps_the_first_time_and_relay(tmp_path):
    p = ro.path_in(tmp_path)
    assert ro.vouch({(_SRC, 1): "원채널", (_SRC, 2): None}, by="backfill", path=p, now=T0) == 2
    later = T0 + timedelta(hours=3)
    assert ro.vouch({(_SRC, 1): None, (_SRC2, 5): None}, by="listener", path=p, now=later) == 1
    pairs, err = ro.load(p)
    assert err == ""
    # 처음 보증한 시각·릴레이를 지킨다 — 진단이 '보증 **뒤의** 버림' 을 가르는 기준이다
    assert pairs[(_SRC, 1)] == {"at": T0, "by": "backfill", "title": "원채널"}
    assert pairs[(_SRC, 2)]["title"] == "원채널"              # 제목은 채널 단위다
    assert pairs[(_SRC2, 5)] == {"at": later, "by": "listener", "title": None}


def _count_writes(monkeypatch) -> list:
    """쓰기를 **센다**(진짜 쓰기는 그대로). ⚠️ inode 로 재면 안 된다 — 쓰기가 두 번이면
    두 번째 임시 파일이 첫 교체로 풀린 inode 를 다시 받을 수 있어 '안 썼다' 가 우연히
    참이 된다(뮤테이션 '늘 쓴다' 가 그렇게 살아남았다, #91b)."""
    calls: list = []
    real = ro._write

    def spy(path, doc):
        calls.append(path)
        return real(path, doc)
    monkeypatch.setattr(ro, "_write", spy)
    return calls


def test_an_unchanged_vouch_does_not_rewrite_the_file(tmp_path, monkeypatch):
    """바뀐 것이 없으면 쓰지 않는다 — 봇이 매 글 읽는 파일이고, 리스너가 같은 글을 다시
    보증할 때마다(재시작·재포워드) 쓰면 잠금 경합만 는다."""
    p = ro.path_in(tmp_path)
    writes = _count_writes(monkeypatch)
    ro.vouch({(_SRC, 1): "원채널"}, by="backfill", path=p, now=T0)
    assert len(writes) == 1
    assert ro.vouch({(_SRC, 1): "원채널"}, by="listener", path=p, now=T0) == 0
    assert ro.vouch({(_SRC, 1): None}, by="listener", path=p, now=T0) == 0
    assert len(writes) == 1, writes
    assert ro.vouch({}, by="backfill", path=tmp_path / "없는" / ro.FILE_NAME) == 0
    assert not (tmp_path / "없는").exists()                   # 보증할 것이 없으면 만들지도 않는다
    assert len(writes) == 1


def test_a_new_title_is_applied_to_the_whole_channel(tmp_path, monkeypatch):
    p = ro.path_in(tmp_path)
    ro.vouch({(_SRC, 1): "옛 제목", (_SRC, 2): None}, by="backfill", path=p, now=T0)
    writes = _count_writes(monkeypatch)
    assert ro.vouch({(_SRC, 2): "새 제목"}, by="listener", path=p, now=T0) == 0
    assert len(writes) == 1                                    # 제목만 바뀌어도 쓴다
    pairs, _ = ro.load(p)
    assert {v["title"] for v in pairs.values()} == {"새 제목"}, pairs
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["chats"][str(_SRC)]["title"] == "새 제목"


def test_old_vouches_are_pruned_on_write_and_pruning_alone_writes(tmp_path, monkeypatch):
    """오래된 보증은 쓸 때 걷어낸다(파일을 유계로) — 봇은 24시간 안에 받는다. 새로 더할
    것이 없어도 걷어낼 것이 있으면 쓴다. 경계(딱 `KEEP_DAYS` 전)는 남긴다."""
    p = ro.path_in(tmp_path)
    t1 = T0 - timedelta(days=ro.KEEP_DAYS + 1)          # T0 에는 창 밖
    t2 = T0 - timedelta(days=ro.KEEP_DAYS)              # T0 에는 딱 경계
    ro.vouch({(_SRC, 1): None}, by="backfill", path=p, now=t1)
    ro.vouch({(_SRC2, 2): None}, by="backfill", path=p, now=t2)
    assert set(ro.load(p)[0]) == {(_SRC, 1), (_SRC2, 2)}    # t2 에는 둘 다 창 안
    writes = _count_writes(monkeypatch)
    assert ro.vouch({(_SRC2, 2): None}, by="listener", path=p, now=T0) == 0   # 더할 것 없음
    assert len(writes) == 1                                   # 걷어낼 것만으로도 쓴다
    pairs, _ = ro.load(p)
    assert set(pairs) == {(_SRC2, 2)}                         # 경계는 남고 창 밖은 걷혔다
    assert pairs[(_SRC2, 2)]["at"] == t2                      # 처음 시각 그대로(다시 보증해도)
    # 이미 걷힌 글을 다시 보증하면 **새 보증**이다(그 시각부터 다시 센다)
    assert ro.vouch({(_SRC, 1): None}, by="listener", path=p, now=T0) == 1
    assert ro.load(p)[0][(_SRC, 1)]["at"] == T0


def test_writing_over_an_unreadable_file_warns_and_starts_clean(tmp_path, caplog):
    """못 읽은 기록은 새로 쓴다 — 막으면 이 포워드가 막힌다. 잃는 것(진단의 옛 보증)을
    로그로 남긴다(#43)."""
    p = ro.path_in(tmp_path)
    p.write_bytes(b"{broken")
    with caplog.at_level(logging.WARNING, logger="trade.relay_origins"):
        assert ro.vouch({(_SRC, 1): None}, by="backfill", path=p, now=T0) == 1
    assert ro.load(p) == ({(_SRC, 1): {"at": T0, "by": "backfill", "title": None}}, "")
    assert any("못 읽어" in r.getMessage() and "형식 오류" in r.getMessage()
               for r in caplog.records), caplog.text


def test_vouch_raises_when_it_cannot_write(tmp_path):
    """쓰기 실패는 **던진다** — 부르는 쪽이 그 유닛을 포워드하지 않는다(포워드하면 봇이
    버리고 다음 동기화가 또 포워드한다)."""
    blocker = tmp_path / "파일"
    blocker.write_text("디렉터리 자리에 파일", encoding="utf-8")
    with pytest.raises(OSError):
        ro.vouch({(_SRC, 1): None}, by="backfill", path=blocker / ro.FILE_NAME, now=T0)


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path, monkeypatch):
    """봇은 쓰다 만 파일을 읽으면 안 된다(#280·#379) — 임시 파일에 쓰고 교체한다. 교체
    직전에 실패하면 원본이 그대로다."""
    p = ro.path_in(tmp_path)
    ro.vouch({(_SRC, 1): None}, by="backfill", path=p, now=T0)
    before = p.read_bytes()
    assert sorted(x.name for x in tmp_path.iterdir()) == [ro.FILE_NAME, ro.FILE_NAME + ".lock"]

    def boom(src, dst):
        raise OSError("교체 실패(테스트)")
    monkeypatch.setattr(ro.os, "replace", boom)
    with pytest.raises(OSError):
        ro.vouch({(_SRC, 2): None}, by="backfill", path=p, now=T0)
    assert p.read_bytes() == before                            # 원본은 그대로


def test_vouch_rereads_the_file_under_the_lock(tmp_path, monkeypatch):
    """락을 잡은 **뒤에** 읽어 합친다 — 먼저 읽으면 기다리는 사이 다른 릴레이가 쓴 보증을
    덮어 잃는다(리스너와 6시간 백필이 겹친다). 락을 잡는 순간 다른 프로세스의 쓰기가 막
    끝난 상태를 결정적으로 만든다(시간·스레드에 기대면 단독 green · 전체 red, #128)."""
    p = ro.path_in(tmp_path)
    real = ro._locked

    @contextmanager
    def racing(path):
        with real(path):
            ro._write(path, ro._dump({(_SRC2, 9): {"at": T0, "by": "listener", "title": None}}))
            yield
    monkeypatch.setattr(ro, "_locked", racing)
    ro.vouch({(_SRC, 1): None}, by="backfill", path=p, now=T0)
    assert set(ro.load(p)[0]) == {(_SRC, 1), (_SRC2, 9)}


def test_two_processes_vouching_at_once_lose_nothing(tmp_path):
    """실제 프로세스 둘이 같은 파일에 번갈아 쓴다 — 락 + 락 안 다시 읽기 + PID 가 붙은 임시
    파일이 한 세트로 일해야 60건이 다 남는다."""
    p = ro.path_in(tmp_path)
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_REPO)!r})
        from trade import relay_origins as ro
        base = int(sys.argv[1])
        for i in range(30):
            ro.vouch({{({_SRC}, base + i): None}}, by="p", path={str(p)!r})
    """)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(b)]) for b in (0, 1000)]
    assert [pr.wait(timeout=60) for pr in procs] == [0, 0]
    pairs, err = ro.load(p)
    assert err == "" and len(pairs) == 60, (len(pairs), err)


def test_without_a_lock_it_proceeds_and_says_so(tmp_path, monkeypatch, caplog):
    """락을 못 걸면 멈추지 않는다(포워드가 막히는 것보다 드문 보증 유실이 낫다) — 대신
    조용하지 않다(#42a)."""
    import fcntl

    def no_lock(*a, **k):
        raise OSError("flock 불가(테스트)")
    monkeypatch.setattr(fcntl, "flock", no_lock)
    with caplog.at_level(logging.WARNING, logger="trade.relay_origins"):
        assert ro.vouch({(_SRC, 1): None}, by="backfill", path=ro.path_in(tmp_path)) == 1
    assert "락 없이" in caplog.text and "flock 불가" in caplog.text


# ── 재게시 판정 · 조회 · 설명 ──────────────────────────────────────────────

def test_repost_pairs_takes_only_channel_origins_and_counts_the_rest():
    """봇이 받는 모양(`forward_origin.chat.id` + `message_id`)과 짝이 맞는 것은 **채널**
    원천의 재게시뿐이다. 원천 채널 자신의 글은 보증할 것이 없고, 개인 계정 글·출처를 숨긴
    포워드·번호가 없는 포워드는 세기만 한다(모듈 독스트링 '못 보는 축')."""
    native = NS(fwd_from=None)
    user = NS(fwd_from=NS(from_id=NS(user_id=5, id=5), channel_post=None))
    hidden = NS(fwd_from=NS(from_id=None, channel_post=None, from_name="숨김"))
    no_post = NS(fwd_from=NS(from_id=NS(channel_id=1, id=_SRC2), channel_post=None))
    pairs, blind = ro.repost_pairs(
        [native, _chan_msg(11, title="원채널"), _chan_msg(12), user, hidden, no_post], _peer_id)
    assert pairs == {(_SRC, 11): "원채널", (_SRC, 12): None}
    assert blind == 3


def test_repost_pairs_uses_the_callers_peer_id():
    """키는 부르는 쪽의 텔레톤 `get_peer_id` 로 만든다 — 백필의 중복 제거 키와 같은 함수라야
    봇이 받는 ID 와 맞는다(#38). 그 함수가 못 읽으면 지어내지 않고 센다."""
    seen = []

    def gpi(p):
        seen.append(p)
        return -1005550000000
    pairs, blind = ro.repost_pairs([_chan_msg(3)], gpi)
    assert pairs == {(-1005550000000, 3): None} and blind == 0 and len(seen) == 1

    def broken(p):
        raise TypeError("peer 아님")
    assert ro.repost_pairs([_chan_msg(3)], broken) == ({}, 1)


def test_a_title_lookup_that_raises_is_not_fatal():
    class Boom:
        @property
        def forward(self):
            raise RuntimeError("엔티티 없음")
    m = Boom()
    m.fwd_from = NS(from_id=NS(channel_id=1, id=_SRC), channel_post=4)
    assert ro.repost_pairs([m], _peer_id) == ({(_SRC, 4): None}, 0)


def test_is_vouched_only_answers_for_numbers():
    pairs = {(_SRC, 4): {}}
    assert ro.is_vouched(pairs, _SRC, 4) and ro.is_vouched(pairs, str(_SRC), "4")
    assert not ro.is_vouched(pairs, _SRC, 5)
    assert not ro.is_vouched(pairs, None, 4) and not ro.is_vouched(pairs, _SRC, None)
    assert not ro.is_vouched({}, _SRC, 4) and not ro.is_vouched(None, _SRC, 4)


def test_describe_names_channels_and_says_what_it_cut():
    pairs = {(_SRC, 1): "원채널", (_SRC, 2): None, (-1001, 1): None, (-1002, 1): None,
             (-1003, 1): None}
    got = ro.describe(pairs, limit=3)
    assert f"{_SRC}('원채널') 글 2건" in got
    assert got.endswith(" 외 채널 1개"), got                  # 자른 수를 말한다(#45)
    assert ro.describe({}) == ""


def test_the_default_directory_is_read_when_called(monkeypatch, tmp_path):
    """import 시점에 굳히면 환경을 바꾼 호출이 옛 경로(운영 파일)를 연다(#373)."""
    monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path))
    assert ro.default_dir() == tmp_path
    monkeypatch.delenv("TRADE_DATA_DIR")
    assert ro.default_dir() == Path.home() / ".trade"
    assert ro.path_in(tmp_path) == tmp_path / "relay_origins.json"
