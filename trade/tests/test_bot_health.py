"""trade.bot_health — 포워드가 inbox 에 안 들어왔을 때 갈래를 가르는 진단 (실수 #406).

2026-09-25: 40일 회수가 27건을 포워드("done: forwarded 27 of 27")했는데 32분 뒤에도
inbox 가 한 줄도 안 늘었고 trade-bot 저널의 `ingested msg=` 는 0 이었다. 그 0 은
갈래가 여럿이고 처방이 다르다(#82). 이 파일은 (a) 판정이 갈래마다 옳은 말을 하는지
(b) 읽기 전용 계약(getUpdates 금지 · 파일 안 씀)과 토큰 비노출 (c) 생산자(봇·릴레이의
실제 로그 형식)와 소비자(파서)가 안 갈리는지 (d) 매시간 알림 배선을 잰다.

python-telegram-bot 없이 돈다 — 진단이 봇 import 에 기대지 않는다는 계약 자체다.
"""

from __future__ import annotations

import ast
import json
import logging
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trade import bot_health as bh

_REPO = Path(__file__).resolve().parents[2]
_KST = timezone(timedelta(hours=9))
# ⚠️ 토큰 모양 리터럴을 소스에 두면 시크릿 스캐너가 문다 — 조립해서 만든다
# (tests/test_regression.py 의 같은 선례 · 실수 #407).
TOKEN = "123456789" + ":" + "AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"
_DEST = -1003715527602
_BAD = -1003322526960
_BEON = -1002695068357
NOW = datetime(2026, 9, 25, 8, 30, tzinfo=_KST)


def _jl(ts: str, msg: str, pid: int = 4242, logger: str = "trade-bot") -> str:
    """journalctl -o short-iso 한 줄 — 봇·릴레이의 logging 형식 그대로."""
    return (f"{ts}+0900 telegram-bot-usc python[{pid}]: "
            f"{ts.replace('T', ' ')},000 [INFO] {logger} — {msg}")


def _poll(ts: str, code: str = "200 OK") -> str:
    return _jl(ts, f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getUpdates '
                   f'"HTTP/1.1 {code}"', logger="httpx")


def _start(ts="2026-09-24T23:53:10", *, drop_log=True, pid=4242, inbox="/home/h/.trade/inbox.jsonl",
           allowed=f"{{{_DEST}}}", origin="{'badonions', 'beon_beclear'}") -> str:
    tail = " drop_log=on" if drop_log else ""
    return _jl(ts, f"trade-bot starting — inbox={inbox} media=/home/h/.trade/media "
                   f"allowed={allowed} origin={origin} concurrency=8{tail}", pid=pid)


# ── 생산자 ↔ 소비자: 실제 로그 형식에서 픽스처를 만든다(#155) ─────────────────

def _log_format(path: Path, head: str) -> str:
    """소스의 `log.info("...", ...)` 첫 인자(문자열 연결 포함)를 AST 로 꺼낸다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "info"
                and node.args):
            try:
                fmt = ast.literal_eval(node.args[0])
            except ValueError:
                continue
            if isinstance(fmt, str) and fmt.startswith(head):
                return fmt
    raise AssertionError(f"{path}: '{head}' 로 시작하는 log.info 가 없다")


def test_start_line_parser_reads_the_real_bot_format():
    """봇 시작 줄 형식이 바뀌면 이 진단의 ③ 이 조용히 '못 찾음' 이 된다 — 소스에서
    형식을 꺼내 채워 넣고 파싱한다(리터럴 픽스처는 형식 변경을 축복한다, #19)."""
    fmt = _log_format(_REPO / "trade" / "bot.py", "trade-bot starting")
    msg = fmt % (Path("/home/h/.trade/inbox.jsonl"), Path("/home/h/.trade/media"),
                 {_DEST}, {"badonions", "beon_beclear"}, 8)
    p = bh.parse_start_line(_jl("2026-09-24T23:53:10", msg))
    assert p is not None, msg
    assert p["inbox"] == "/home/h/.trade/inbox.jsonl"
    assert p["allowed"] == {_DEST}
    assert p["origin"] == {"badonions", "beon_beclear"}
    assert p["drop_log"] is True                 # 이 판은 버림을 적는다는 표식
    assert p["pid"] == 4242 and p["ts"] is not None
    # 탐색 모드(목록 없음) 는 '전부 받는다' 로 읽혀야 한다 — 판정 불가가 아니다
    msg2 = fmt % (Path("/x/inbox.jsonl"), Path("/x/media"), "<discovery>", "<any>", 8)
    p2 = bh.parse_start_line(_jl("2026-09-24T23:53:10", msg2))
    assert p2["allowed"] == "any" and p2["origin"] == "any"


def test_start_line_old_build_and_unreadable_sets():
    old = bh.parse_start_line(_start(drop_log=False))
    assert old["drop_log"] is False
    # 집합을 못 읽으면 '전부 받는다' 로 가정하지 않는다(#165) — None = 판정 불가
    bad = bh.parse_start_line(_start(origin="{badonions, x}"))
    assert bad["origin"] is None
    assert bh.parse_start_line("trade-bot starting — 형식이 다른 줄") is None


def test_relay_forward_parser_reads_every_real_relay_format():
    """릴레이 넷의 실제 로그 형식 — 하나라도 파서와 갈리면 그 릴레이의 포워드는 대조에서
    영영 빠진다(#38·#24 목록이 아니라 소스에서)."""
    scripts = _REPO / "trade" / "scripts"
    cases = {
        "backfill_badonion": ("done: forwarded", (27, 27, 0)),
        "backfill_beon": ("done: forwarded", (5, 6, 1)),
        "listen_badonion": ("forwarded %d msg", (3, [11, 12, 13])),
        "listen_beon": ("forwarded %d msg", (1, [7])),
    }
    lines = []
    for name, (head, args) in cases.items():
        fmt = _log_format(scripts / f"{name}.py", head)
        logger = "backfill" if name == "backfill_beon" else name
        lines.append(_jl("2026-09-25T07:49:09", fmt % args, logger=logger))
    got = bh.relay_forwards(lines)
    assert [(g["who"], g["n"], g["done"]) for g in got] == [
        ("backfill_badonion", 27, True), ("backfill", 5, True),
        ("listen_badonion", 3, False), ("listen_beon", 1, False)]
    # 0건 실행은 사건이 아니다(대조 분모를 부풀리지 않는다)
    zero = _jl("2026-09-25T07:49:09", "done: forwarded 0 of 0 candidate messages (skipped_units=0)",
               logger="backfill_badonion")
    assert bh.relay_forwards([zero]) == []


def test_every_forwarding_script_declares_source_username():
    """릴레이 목록은 `SOURCE_USERNAME` 에서 파생한다 — 포워드하는 스크립트가 그 상수를
    안 두면 출처 게이트 대조에서 빠진다. 이름 열거가 아니라 디렉터리 전수(#24)."""
    scripts = _REPO / "trade" / "scripts"
    found = bh.relay_sources(scripts)
    assert found == {"Badonions": ["backfill_badonion", "listen_badonion"],
                     "BeOn_BeClear": ["backfill_beon", "listen_beon"]}
    forwarding = []
    for p in sorted(scripts.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        # 문자열이 아니라 **호출**을 본다 — diagnose_badonion 은 독스트링에 "안 부른다"
        # 고 forward_messages 를 적는다(#59b 소스 검사는 독스트링을 걷어내고 볼 것).
        if any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "forward_messages"
               for n in ast.walk(tree)):
            forwarding.append(p.stem)
            assert any(p.stem in v for v in found.values()), (
                f"{p.name} 는 포워드하는데 모듈 수준 SOURCE_USERNAME 이 없다")
    # 반대 증거(#25): 포워드하는 스크립트를 실제로 봤다
    assert sorted(forwarding) == ["backfill_badonion", "backfill_beon",
                                  "listen_badonion", "listen_beon"], forwarding


# ── 저널 사실 ────────────────────────────────────────────────────────────

def test_journal_facts_counts_polls_ingest_drops_and_hides_the_token():
    lines = [
        _start(),
        _poll("2026-09-25T07:48:30"),
        _poll("2026-09-25T07:48:40", "409 Conflict"),
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T07:49:01", "ingested msg=77 mg=- caption=12 photo=-"),
        _jl("2026-09-25T07:49:02", "dropped msg=78 reason=origin origin_type=channel "
                                   f"origin_chat={_BAD} origin_username=Other allowed_origins=['x']"),
        _jl("2026-09-25T07:49:03", f"dropped channel=-100111 reason=channel allowed=[{_DEST}]"),
        _jl("2026-09-25T07:49:04", f"[ERROR] boom https://api.telegram.org/bot{TOKEN}/x",
            logger="telegram.ext.Updater").replace("[INFO] ", ""),
    ]
    j = bh.journal_facts(lines)
    assert j["polls"] == {"200": 2, "409": 1}
    assert j["last_ok"] == datetime(2026, 9, 25, 8, 29, 50, tzinfo=_KST)
    assert len(j["ingested"]) == 1 and len(j["drops_origin"]) == 1 and len(j["drops_channel"]) == 1
    assert len(j["starts"]) == 1 and j["starts"][0]["pid"] == 4242
    assert len(j["errors"]) == 1
    blob = json.dumps(j, default=str)
    assert TOKEN not in blob and TOKEN.split(":")[1] not in blob


# ── 보낸 만큼 받았나 ─────────────────────────────────────────────────────

def _fwd(ts, n, *, done=False, who="listen_badonion"):
    return {"ts": ts, "n": n, "who": who, "done": done}


def test_delivery_gap_branches():
    t0 = NOW - timedelta(minutes=40)
    got = [t0 + timedelta(seconds=2)] * 3
    assert bh.delivery_gap([_fwd(t0, 3)], got, NOW)["kind"] == "ok"
    g = bh.delivery_gap([_fwd(t0, 27, done=True, who="backfill_badonion")], [], NOW)
    assert g["kind"] == "total" and g["sent"] == 27 and g["got"] == 0
    p = bh.delivery_gap([_fwd(t0, 3)], got[:1], NOW)
    assert p["kind"] == "partial" and p["got"] == 1
    # 기다릴 만큼 안 지난 포워드는 아직 '안 왔다' 고 말하지 않는다
    fresh = bh.delivery_gap([_fwd(NOW - timedelta(seconds=30), 3)], [], NOW)
    assert fresh["kind"] == "none"
    # 시각을 못 읽은 사건은 세지 않고 판정 불가로 말한다(#54)
    assert bh.delivery_gap([_fwd(None, 3)], [], NOW)["kind"] == "unknown"
    assert bh.delivery_gap([], [], NOW)["kind"] == "none"


def test_delivery_gap_counts_ingest_before_a_backfill_done_line():
    """'done: forwarded' 는 실행 **끝**에 찍힌다 — 그 실행의 수신은 그보다 앞이다
    (2026-09-25 실측 17초). 앞쪽 수신을 안 세면 멀쩡한 실행을 누락으로 오보한다."""
    done = NOW - timedelta(minutes=10)
    ingested = [done - timedelta(seconds=15)] * 27
    assert bh.delivery_gap([_fwd(done, 27, done=True)], ingested, NOW)["kind"] == "ok"
    # 대조군: 리스너 사건(실행 끝이 아니다)엔 그 여유를 주지 않는다
    lis = bh.delivery_gap([_fwd(done, 27)], ingested, NOW)
    assert lis["kind"] == "total"
    # 리스너는 봇이 로그보다 몇 초 먼저 받을 수 있다 — 그 정도는 센다
    early = bh.delivery_gap([_fwd(done, 1)], [done - timedelta(seconds=2)], NOW)
    assert early["kind"] == "ok"


# ── 원천·출처 게이트 ─────────────────────────────────────────────────────

def test_origin_accepts_mirrors_the_bot_gate():
    assert bh.origin_accepts("any", "Badonions") is True
    assert bh.origin_accepts({"badonions"}, "Badonions") is True        # 대소문자 무시
    assert bh.origin_accepts({str(_BAD)}, "Badonions", {_BAD}) is True  # 숫자 ID 로 적힌 목록
    assert bh.origin_accepts({str(_BAD)}, "Badonions", set()) is None   # ID 를 모른다 = 판정 불가
    assert bh.origin_accepts({"beon_beclear"}, "Badonions", {_BAD}) is False
    assert bh.origin_accepts(None, "Badonions") is None


def test_inbox_facts_per_source(tmp_path):
    p = tmp_path / "inbox.jsonl"
    rows = [
        {"ingested_at": "2026-09-24T10:00:00+00:00", "forward_origin_chat_username": "Badonions",
         "forward_origin_chat_id": _BAD},
        {"ingested_at": "2026-09-25T00:00:00+00:00", "forward_origin_chat_username": "BeOn_BeClear",
         "forward_origin_chat_id": _BEON},
        {"ingested_at": "2026-09-25T00:10:00", "forward_origin_chat_username": "BeOn_BeClear"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    f = bh.inbox_facts(p, NOW)
    assert f["lines"] == 4 and f["bad"] == 1
    assert f["by"]["badonions"]["ids"] == {_BAD}
    assert f["by"]["badonions"]["n24"] == 1
    # 시간대 없는 시각은 가정하지 않는다 — 마지막 수신을 그 줄로 당기지 않는다(#165)
    assert f["by"]["beon_beclear"]["last"] == datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)
    assert f["last"][1] == "beon_beclear"
    assert bh.inbox_facts(tmp_path / "없음.jsonl", NOW) == {
        "path": str(tmp_path / "없음.jsonl"), "exists": False}


# ── 텔레그램: 읽기 전용 · 토큰 비노출 ────────────────────────────────────

def test_tg_call_refuses_get_updates():
    """getUpdates 는 돌고 있는 봇과 409 로 부딪히고, 받아 간 업데이트를 봇이 영영
    못 본다 — 진단이 운영 상태를 바꾸면 안 된다(#264)."""
    with pytest.raises(ValueError):
        bh.tg_call(TOKEN, "getUpdates", fetch=lambda *a: (200, b"{}"))


def test_tg_call_scrubs_the_token_from_errors():
    def boom(url, timeout):
        raise OSError(f"cannot reach {url}")
    r = bh.tg_call(TOKEN, "getMe", fetch=boom)
    assert r["ok"] is False and TOKEN not in r["err"] and TOKEN.split(":")[1] not in r["err"]

    def http_err(url, timeout):
        return 400, json.dumps({"ok": False, "error_code": 400,
                                "description": f"Bad Request: chat not found {url}"}).encode()
    r2 = bh.tg_call(TOKEN, "getChatMember", {"chat_id": _DEST, "user_id": 1}, fetch=http_err)
    assert r2 == {"ok": False, "code": 400, "err": r2["err"]}
    assert "chat not found" in r2["err"] and TOKEN.split(":")[1] not in r2["err"]
    r3 = bh.tg_call(TOKEN, "getMe", fetch=lambda u, t: (502, b"<html>"))
    assert r3["ok"] is False and r3["code"] == 502


def test_telegram_facts_asks_only_read_only_methods():
    asked = []

    def call(tok, method, params=None):
        asked.append((method, params))
        if method == "getMe":
            return {"ok": True, "result": {"id": 42, "username": "tb"}}
        return {"ok": True, "result": {}}
    f = bh.telegram_facts(TOKEN, _DEST, ["Badonions"], call=call)
    assert {m for m, _ in asked} <= set(bh.READ_ONLY_METHODS)
    assert ("getChatMember", {"chat_id": _DEST, "user_id": 42}) in asked
    assert ("getChat", {"chat_id": "@Badonions"}) in asked
    assert f["username"] == "tb"
    assert bh.telegram_facts("", _DEST, ["Badonions"], call=call) == {"token": False}


# ── 판정: 갈래마다 옳은 말 ───────────────────────────────────────────────

def _good() -> dict:
    """모든 조건이 맞는 사실 — 각 테스트가 하나만 바꾼다."""
    run = bh.parse_start_line(_start())
    return {
        "now": NOW,
        "env": {"dest": _DEST, "inbox": "/home/h/.trade/inbox.jsonl"},
        "unit": {"kind": "running", "text": "살아 있다"},
        "gap": {"kind": "ok", "sent": 3, "got": 3},
        "tg": {"token": True,
               "me": {"ok": True, "result": {"id": 42, "username": "tb"}},
               "webhook": {"ok": True, "result": {"url": "", "pending_update_count": 0}},
               "member": {"ok": True, "result": {"status": "administrator"}},
               "sources": {"Badonions": {"ok": True, "result": {"id": _BAD, "username": "Badonions"}}}},
        "journal": bh.journal_facts([_start(), _poll("2026-09-25T08:29:50")]),
        "running": run,
        "relays": {"Badonions": ["backfill_badonion", "listen_badonion"],
                   "BeOn_BeClear": ["backfill_beon", "listen_beon"]},
        "inbox_expected": "/home/h/.trade/inbox.jsonl",
        "inbox": {"exists": True, "path": "/home/h/.trade/inbox.jsonl", "lines": 10,
                  "last": (NOW - timedelta(minutes=5), "badonions"),
                  "by": {"badonions": {"last": NOW - timedelta(minutes=5), "n24": 3,
                                       "ids": {_BAD}}}},
        "others": [],
    }


def _v(f):
    rc, lines = bh.verdict(f)
    return rc, "\n".join(lines)


def test_all_good_is_green():
    rc, out = _v(_good())
    assert rc == 0 and out.startswith("✅"), out
    assert "❌" not in out and "❓" not in out


@pytest.mark.parametrize("mutate, needle", [
    (lambda f: f.update(unit={"kind": "stopped", "text": "멈춰 있다"}), "24시간"),
    (lambda f: f["tg"]["webhook"]["result"].update(url="https://hook.example.com/s3cr3t-path"),
     "웹훅이 걸려 있다(호스트 hook.example.com)"),
    (lambda f: f["tg"]["webhook"]["result"].update(allowed_updates=["message", "callback_query"]),
     "channel_post 가 없다"),
    (lambda f: f["tg"].update(member={"ok": True, "result": {"status": "member"}}), "관리자가 아니다"),
    (lambda f: f["tg"].update(member={"ok": False, "code": 400, "err": "Bad Request: chat not found"}),
     "봇을 못 찾았다"),
    (lambda f: f["tg"].update(me={"ok": False, "code": 401, "err": "Unauthorized"}), "401"),
    (lambda f: f.update(journal=bh.journal_facts([_poll("2026-09-25T08:29:50", "409 Conflict")])),
     "409 Conflict"),
    (lambda f: f.update(journal=bh.journal_facts([_poll("2026-09-25T08:00:00")])), "멈췄다"),
    (lambda f: f.update(journal=bh.journal_facts([])), "정상 폴링(getUpdates 200)이 한 번도 없다"),
    (lambda f: f.update(journal=bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T08:00:00", f"dropped channel={_DEST} reason=channel allowed=[1]")])),
     "채널 게이트"),
    (lambda f: f.update(journal=bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T08:00:00", "dropped msg=9 reason=origin origin_type=channel "
                                   f"origin_chat={_BAD} origin_username=Badonions "
                                   "allowed_origins=['y']")])),
     "출처 게이트"),
    # 원천이 사용자명을 바꿨으면 버림 줄의 username 은 새 이름이다 — ID 로 알아본다
    (lambda f: f.update(journal=bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T08:00:00", "dropped msg=9 reason=origin origin_type=channel "
                                   f"origin_chat={_BAD} origin_username=NewName "
                                   "allowed_origins=['badonions']")])),
     "릴레이 포워드 1건"),
    (lambda f: f.update(running=bh.parse_start_line(_start(allowed="{-100999}"))), "릴레이 목적지"),
    (lambda f: f.update(running=bh.parse_start_line(_start(origin="{'beon_beclear'}"))),
     "Badonions(backfill_badonion, listen_badonion) 포워드를 버린다"),
    (lambda f: f.update(running=bh.parse_start_line(_start(inbox="/other/inbox.jsonl"))),
     "TRADE_DATA_DIR"),
    (lambda f: f["tg"]["sources"].update(Badonions={"ok": True, "result": {"id": _BAD, "username": "NewName"}}),
     "@NewName"),
    (lambda f: f.update(inbox={"exists": False, "path": "/x/inbox.jsonl"}), "inbox 가 없다"),
])
def test_each_cause_is_a_red_line(mutate, needle):
    f = _good()
    mutate(f)
    rc, out = _v(f)
    assert rc == 1, out
    red = [ln for ln in out.splitlines() if ln.startswith("❌")]
    assert any(needle in ln for ln in red), out
    assert "✅" not in out


@pytest.mark.parametrize("mutate, needle", [
    (lambda f: f.update(unit={"kind": "unknown", "text": "x"}), "systemd 에 못 물었다"),
    (lambda f: f.update(tg={"token": False}), "토큰(TRADE_BOT_TOKEN)을 못 읽었다"),
    (lambda f: f["tg"].update(me={"ok": False, "code": None, "err": "OSError"}), "텔레그램에 못 물었다"),
    (lambda f: f.update(journal=None, journal_err="시스템 저널 읽기 권한이 없다"), "저널을 못 읽었다"),
    (lambda f: f.update(running=None, running_err="x"), "시작 줄을 못 찾았다"),
    (lambda f: f["tg"]["sources"].update(Badonions={"ok": False, "code": 400, "err": "chat not found"}),
     "텔레그램이 못 찾았다"),
    (lambda f: f.update(gap={"kind": "unknown", "err": "릴레이 저널을 못 읽었다 — x"}), "대조 못 했다"),
])
def test_what_we_could_not_measure_is_not_green(mutate, needle):
    """판정 불가는 통과가 아니다(#54) — ❓ 와 rc 2, ✅ 는 안 찍는다."""
    f = _good()
    mutate(f)
    rc, out = _v(f)
    assert rc == 2, out
    assert any(needle in ln for ln in out.splitlines() if ln.startswith("❓")), out
    assert "✅" not in out


def test_webhook_url_path_never_reaches_the_output():
    f = _good()
    f["tg"]["webhook"]["result"]["url"] = "https://hook.example.com/bot-secret-9f8e7d"
    _rc, out = _v(f)
    lines = bh.render({**f, "env": {"dest": _DEST, "src": {}}, "facts": {}}, "x")
    assert "bot-secret-9f8e7d" not in out + "\n".join(lines)


def test_unexplained_gap_says_which_branch_is_left():
    """원인 줄이 하나도 없는데 못 받았으면 — 옛 판이면 '버림이 안 적혀 가를 수 없다' 를,
    버림을 적는 판이면 '텔레그램이 안 줬다' 를 말한다. 같은 사실을 두 줄로 말하지 않는다
    (옛 판 메모는 그 ❓ 가 대신한다, #395)."""
    f = _good()
    f["gap"] = {"kind": "total", "sent": 27, "got": 0, "first": NOW - timedelta(minutes=40),
                "who": ["backfill_badonion"], "after_last": 0}
    f["running"] = bh.parse_start_line(_start(drop_log=False))
    rc, out = _v(f)
    assert rc == 1
    assert "❌ 릴레이가 27건을 포워드했는데" in out and "한 건도 못 받았다" in out
    assert "옛 판이라 '받고 버렸나 / 아예 안 왔나'" in out
    assert out.count("옛 판") == 1, out
    f["running"] = bh.parse_start_line(_start(drop_log=True))
    _rc, out2 = _v(f)
    assert "텔레그램이 전달하지 않았다" in out2
    # 원인이 짚이면 그 ❓ 는 안 붙는다 — 원인이 답이다
    f["tg"]["member"] = {"ok": True, "result": {"status": "left"}}
    _rc, out3 = _v(f)
    assert "관리자가 아니다" in out3 and "텔레그램이 전달하지 않았다" not in out3


# ── 수집 배선 ────────────────────────────────────────────────────────────

def _collect(bot_lines, relay_lines=(), *, bot_kind="", relay_kind="", start=None,
             facts=None, tmp=None):
    def read(units, since):
        if units == (bh._SERVICE,):
            return list(bot_lines), ("" if bot_lines else "why"), bot_kind
        return list(relay_lines), ("" if relay_lines else "why"), relay_kind
    facts = facts or {"ok": True, "s_LoadState": "loaded", "s_ActiveState": "active",
                      "s_SubState": "running", "s_MainPID": "4242", "s_NRestarts": "0"}
    starts = []

    def start_fn(pid):
        starts.append(pid)
        return start, ("" if start else "없음")
    inbox = (tmp / "inbox.jsonl") if tmp else None
    env = {"token": "", "dest": _DEST, "src": {}, "err": "",
           "inbox": str(inbox) if inbox else ""}
    f = bh.collect("x", now=NOW, env=env, facts_fn=lambda: facts, read=read,
                   start_fn=start_fn, tg_fn=lambda *a: {"token": False},
                   procs_fn=lambda own: [])
    return f, starts


def test_collect_uses_the_running_pid_start_line_and_falls_back_to_pid_lookup():
    other = _start(pid=1111, inbox="/old/inbox.jsonl")
    mine = _start(ts="2026-09-25T01:00:00", pid=4242)
    f, starts = _collect([other, mine, _poll("2026-09-25T08:29:50")])
    assert f["running"]["pid"] == 4242 and f["running"]["inbox"] != "/old/inbox.jsonl"
    assert starts == []                       # 창 안에 있으면 따로 안 찾는다
    # 창 안에 지금 PID 의 시작 줄이 없으면 PID 로 찾는다(창 밖에서 떴다)
    f2, starts2 = _collect([other, _poll("2026-09-25T08:29:50")],
                           start=bh.parse_start_line(mine))
    assert starts2 == [4242] and f2["running"]["pid"] == 4242


def test_collect_rotated_journal_is_an_empty_fact_not_unreadable():
    """읽히는데 창 안에 0줄 = 봇이 아무것도 안 찍었다(살아 있다면 폴링 0회 = ❌).
    권한 없음 = 판정 불가(❓). 둘을 같은 '못 읽음' 으로 접으면 처방이 틀린다(#82)."""
    f, _ = _collect([], bot_kind="rotated")
    assert f["journal"] is not None and f["journal"]["n"] == 0
    rc, out = _v(f)
    assert "정상 폴링(getUpdates 200)이 한 번도 없다" in out and rc == 1
    f2, _ = _collect([], bot_kind="denied")
    assert f2["journal"] is None


def test_collect_wires_the_gap_from_both_journals(tmp_path):
    relay = [_jl("2026-09-25T07:49:09", "done: forwarded 27 of 27 candidate messages "
                 "(skipped_units=0)", logger="backfill_badonion", pid=99)]
    f, _ = _collect([_start(), _poll("2026-09-25T08:29:50")], relay, tmp=tmp_path)
    assert f["gap"]["kind"] == "total" and f["gap"]["sent"] == 27
    f2, _ = _collect([_start(), _poll("2026-09-25T08:29:50")], [], relay_kind="denied")
    assert f2["gap"]["kind"] == "unknown"


def test_main_prints_every_section_and_returns_the_verdict_rc(monkeypatch, capsys):
    f = _good()
    f.update(facts={"s_NRestarts": "0"}, journal_err="", forwards=[],
             env={"dest": _DEST, "src": {"TRADE_BOT_TOKEN": ".env"}, "err": "",
                  "inbox": "/home/h/.trade/inbox.jsonl"})
    seen = []
    monkeypatch.setattr(bh, "collect", lambda since: seen.append(since) or f)
    assert bh.main([]) == 0
    out = capsys.readouterr().out
    assert seen == ["86400 seconds ago"]
    for mark in ("# trade.bot_health v", "①", "②", "③", "④", "⑤", "⑦", "⑧ 판정:", "✅"):
        assert mark in out, mark
    assert TOKEN not in out


# ── 프로세스 · 시작 줄 조회 ──────────────────────────────────────────────

def test_other_bot_processes_ignores_itself_and_siblings(tmp_path):
    def proc(pid, *argv):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    proc(10, "/v/bin/python", "-m", "trade.bot")
    proc(11, "/v/bin/python", "-m", "trade.bot_health")
    proc(12, "/v/bin/python", "/home/h/stock-trade/trade/bot.py")
    proc(13, "/v/bin/python", "-m", "trade.bot")
    got = bh.other_bot_processes({13}, proc_root=tmp_path)
    assert sorted(o["pid"] for o in got) == [10, 12]


def test_find_start_line_reads_the_head_and_terminates():
    class P:
        def __init__(self, lines):
            self.stdout = iter(lines)
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0
    procs = []

    def popen(cmd, **kw):
        assert "_PID=4242" in cmd and f"_SYSTEMD_UNIT={bh._SERVICE}" in cmd
        p = P(["x\n", _start() + "\n", "never read\n"])
        procs.append(p)
        return p
    got, err = bh.find_start_line(4242, popen=popen)
    assert got["pid"] == 4242 and err == "" and procs[0].terminated

    def popen_none(cmd, **kw):
        return P(["x\n"] * 3)
    got2, err2 = bh.find_start_line(4242, popen=popen_none)
    assert got2 is None and "4242" in err2


# ── 매시간 알림 배선(health_check) ───────────────────────────────────────

@pytest.fixture
def hc(tmp_path, monkeypatch):
    from trade.scripts import health_check as hc
    monkeypatch.setattr(hc, "MARKER_DIR", tmp_path)        # 운영 ~/.trade 를 안 건드린다(#30)
    sent = []
    monkeypatch.setattr(hc, "_notify", lambda msg: sent.append(msg))
    hc._sent = sent
    return hc


def test_health_check_alerts_once_on_a_gap(hc, monkeypatch):
    g = {"kind": "total", "sent": 27, "got": 0, "first": NOW, "who": ["backfill_badonion"],
         "after_last": 0, "err": ""}
    windows = []
    monkeypatch.setattr(bh, "delivery_check", lambda since: windows.append(since) or dict(g))
    hc.check_delivery_gap()
    hc.check_delivery_gap()
    assert len(hc._sent) == 1, hc._sent                    # 같은 누락은 한 번만
    assert "27건" in hc._sent[0] and "python -m trade.bot_health" in hc._sent[0]
    assert windows == [f"{hc.DELIVERY_WINDOW_S} seconds ago"] * 2


@pytest.mark.parametrize("kind", ["ok", "none", "unknown"])
def test_health_check_is_quiet_unless_a_gap(hc, monkeypatch, caplog, kind):
    monkeypatch.setattr(bh, "delivery_check",
                        lambda since: {"kind": kind, "sent": 0, "got": 0, "err": "권한"})
    with caplog.at_level(logging.INFO, logger="health-check"):
        hc.check_delivery_gap()
    assert hc._sent == []
    if kind == "unknown":                                  # 판정 불가는 조용하지 않다(#54)
        assert any("판정 불가" in r.getMessage() for r in caplog.records)


def test_health_check_main_runs_both_signals_and_fails_loudly(hc, monkeypatch, caplog):
    """한 신호가 던져도 다른 신호는 돈다 — 그리고 조용하지 않다(트레이스백 + rc 1 =
    유닛 실패로 보인다, #12). 양쪽 방향을 다 잰다."""
    ran = []
    monkeypatch.setattr(hc, "check_cycle_gap", lambda: ran.append("cycle"))

    def boom(since):
        raise RuntimeError("journal exploded")
    monkeypatch.setattr(bh, "delivery_check", boom)
    with caplog.at_level(logging.WARNING, logger="health-check"):
        assert hc.main() == 1
    assert ran == ["cycle"]
    rec = [r for r in caplog.records if "check_delivery_gap failed" in r.getMessage()]
    assert rec and rec[0].exc_info is not None

    def cycle_boom():
        raise RuntimeError("store exploded")
    monkeypatch.setattr(hc, "check_cycle_gap", cycle_boom)
    monkeypatch.setattr(bh, "delivery_check",
                        lambda since: ran.append("delivery") or {"kind": "none", "sent": 0,
                                                                 "got": 0, "err": ""})
    assert hc.main() == 1
    assert ran == ["cycle", "delivery"]
    monkeypatch.setattr(hc, "check_cycle_gap", lambda: None)
    assert hc.main() == 0                                  # 둘 다 조용하면 0


def test_delivery_check_reads_journals_only():
    """매시간 도는 경로는 텔레그램도 파일도 안 건드린다 — 저널 두 번만."""
    asked = []

    def read(units, since):
        asked.append(tuple(units))
        if units == bh.RELAY_UNITS:
            return [_jl("2026-09-25T07:49:09", "forwarded 3 msg(s): [1, 2, 3]",
                        logger="listen_badonion", pid=9)], "", ""
        return [_poll("2026-09-25T08:29:50")], "", ""
    g = bh.delivery_check("7200 seconds ago", now=NOW, read=read)
    assert asked == [bh.RELAY_UNITS, (bh._SERVICE,)]
    assert g["kind"] == "total" and g["sent"] == 3
    # 봇 저널 권한이 없으면 판정 불가 — '못 받았다' 로 단정하지 않는다
    g2 = bh.delivery_check("x", now=NOW, read=lambda u, s: (
        ([_jl("2026-09-25T07:49:09", "forwarded 3 msg(s): [1]", logger="listen_beon")], "", "")
        if u == bh.RELAY_UNITS else ([], "권한 없음", "denied")))
    assert g2["kind"] == "unknown" and "권한" in g2["err"]
    # 릴레이 저널에 줄이 없으면(조용함) 판정할 것이 없다
    g3 = bh.delivery_check("x", now=NOW, read=lambda u, s: ([], "없다", "rotated"))
    assert g3["kind"] == "none"


# ── 리스너 판정의 일반화(trade-bot 에 쓰려고) ─────────────────────────────

@pytest.mark.parametrize("facts", [
    {"ok": False, "err": "boom"},
    {"ok": True, "s_LoadState": "not-found"},
    {"ok": True, "s_LoadState": "masked"},
    {"ok": True, "s_LoadState": "bad-setting"},
    {"ok": True, "s_LoadState": "loaded", "s_ActiveState": "failed",
     "s_SubState": "failed", "s_Result": "exit-code", "s_ExecMainStatus": "78"},
])
def test_listener_verdict_for_trade_bot_names_the_right_unit(facts):
    """옛 판은 처방에 BeOn 리스너 이름을 박아 둬 trade-bot 에 쓰면 남의 유닛을 고치라고
    했다(#38). 그리고 `systemctl status` 는 저널 꼬리를 같이 찍는데 trade-bot 저널엔
    토큰이 평문이다 — 그 명령을 권하면 붙여 넣는 순간 샌다(§Secrets)."""
    from trade.listener_health import listener_verdict
    out = listener_verdict(facts, unit="trade-bot", reauth=False)
    assert "beon" not in out["text"].lower(), out
    assert "systemctl status" not in out["text"], out
    if facts.get("s_LoadState") in ("not-found", "masked"):
        assert "trade-bot.service" in out["text"], out


def test_exit_78_is_not_reauth_for_a_unit_without_a_session():
    """세션이 없는 유닛(trade-bot)에 '재인증' 을 시키면 없는 명령이다(#187b) — 멈춤으로.
    대조군: 기본값(리스너)은 여전히 재인증으로 말한다."""
    from trade.listener_health import listener_verdict
    facts = {"ok": True, "s_LoadState": "loaded", "s_ActiveState": "failed",
             "s_SubState": "failed", "s_Result": "exit-code", "s_ExecMainStatus": "78"}
    assert listener_verdict(facts, unit="trade-bot", reauth=False)["kind"] == "stopped"
    assert listener_verdict(facts)["kind"] == "needs_auth"


def test_systemd_facts_extra_properties(monkeypatch):
    import subprocess

    import bot.daily_kr_flow as dk
    asked = []

    class _R:
        returncode = 0
        stdout = "LoadState=loaded\nMainPID=4242\nNRestarts=3\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: asked.extend(cmd[3:]) or _R())
    out = dk.systemd_facts(timer=None, service="trade-bot.service", extra=("MainPID", "NRestarts"))
    assert "-pMainPID" in asked and "-pNRestarts" in asked
    assert out["s_MainPID"] == "4242" and out["s_NRestarts"] == "3"


def test_read_journal_names_each_branch(monkeypatch):
    """줄 0건은 갈래가 셋이다 — 조용함(rotated) · 권한(denied) · 판정 불가(unknown).
    그리고 journalctl 실패 문구도 토큰을 지워서 싣는다(§Secrets)."""
    import bot.daily_kr_flow as dk

    def run_with(stdout="", stderr="", rc=0):
        return lambda cmd, **kw: types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)
    lines, err, kind = bh.read_journal(("a",), "x", run=run_with("-- No entries --\nL1\n"))
    assert (lines, kind) == (["L1"], "")
    for readable, want in ((True, "rotated"), (False, "denied"), (None, "unknown")):
        monkeypatch.setattr(dk, "journal_readable", lambda r=readable: r)
        lines, err, kind = bh.read_journal(("a",), "x", run=run_with("-- No entries --\n"))
        assert lines == [] and kind == want and err
    lines, err, kind = bh.read_journal(("a",), "x", run=run_with(stderr=f"bad {TOKEN}", rc=1))
    assert kind == "error" and TOKEN.split(":")[1] not in err
    asked = []
    bh.read_journal(("u1", "u2"), "7200 seconds ago",
                    run=lambda cmd, **kw: asked.append(cmd) or types.SimpleNamespace(
                        returncode=0, stdout="x\n", stderr=""))
    assert asked[0][-4:] == ["-u", "u1", "-u", "u2"] and "7200 seconds ago" in asked[0]


def test_delivery_check_relay_journal_unreadable_is_unknown():
    g = bh.delivery_check("x", now=NOW, read=lambda u, s: ([], "권한 없음", "denied"))
    assert g["kind"] == "unknown" and "릴레이" in g["err"]



def test_benign_drops_are_notes_not_failures():
    """버림이 다 결함은 아니다(#82) — 봇이 관리자인 **다른** 채널의 글, 운영자가 채널에
    직접 쓴 글, 릴레이가 아닌 곳의 포워드는 게이트가 제 일을 한 것이다. 그걸 ❌ 로
    세면 매번 거짓 경보가 되고 진짜 ❌ 를 가린다(#260)."""
    f = _good()
    f["journal"] = bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T08:00:00", "dropped channel=-100111 reason=channel allowed=[1]"),
        _jl("2026-09-25T08:00:01", "dropped msg=5 reason=origin origin_type=none "
                                   "origin_chat=None origin_username=None allowed_origins=['x']"),
        _jl("2026-09-25T08:00:02", "dropped msg=6 reason=origin origin_type=channel "
                                   "origin_chat=-100777 origin_username=SomeNews "
                                   "allowed_origins=['x']"),
    ])
    rc, out = _v(f)
    assert rc == 0, out
    assert "[-100111]" in out and "직접 쓴 글 1 · 다른 곳 포워드 1" in out
    assert not [ln for ln in out.splitlines() if ln.startswith("❌")]


def test_unexplained_gap_with_unmeasured_conditions_says_measure_first():
    """못 잰 조건이 남았는데 '위 조건은 전부 맞는데' 라고 말하면 거짓이다(#165)."""
    f = _good()
    f["tg"] = {"token": False}
    f["gap"] = {"kind": "total", "sent": 3, "got": 0, "first": NOW - timedelta(minutes=40),
                "who": ["listen_badonion"], "after_last": 0}
    rc, out = _v(f)
    assert rc == 1 and "원인을 짚지 못했다 — 위 ❓" in out
    assert "위 조건은 전부 맞" not in out


def test_source_checks_are_skipped_when_telegram_is_unreachable():
    """getMe 가 못 닿았으면 원천 조회도 같은 이유로 실패했다 — 같은 원인을 줄마다
    되풀이하지 않는다."""
    f = _good()
    f["tg"]["me"] = {"ok": False, "code": None, "err": "URLError: timed out"}
    f["tg"]["sources"] = {"Badonions": {"ok": False, "code": None, "err": "URLError: timed out"}}
    _rc, out = _v(f)
    assert "텔레그램에 못 물었다" in out and "원천 @Badonions" not in out


def test_running_as_root_does_not_compare_inbox_paths():
    """sudo 로 돌리면 HOME 이 /root 라 기본 inbox 경로가 봇과 달라진다 — 그걸
    'TRADE_DATA_DIR 이 갈렸다' 로 적으면 없는 결함을 고치러 간다."""
    f = _good()
    f["env"]["as_root"] = True
    f["inbox_expected"] = "/root/.trade/inbox.jsonl"
    rc, out = _v(f)
    assert rc == 2 and "root 로 돌려" in out and "TRADE_DATA_DIR" not in out


def test_relay_drop_is_recognised_by_inbox_id_even_when_telegram_is_unreachable():
    """원천 ID 는 두 곳에서 배운다 — inbox 에 쌓인 과거 수신과 지금의 getChat. 텔레그램에
    못 닿는 날에도 inbox 가 아는 ID 로 '릴레이 포워드를 버렸다' 를 알아봐야 한다
    (사용자명이 바뀌어 버림 줄의 username 이 새 이름일 때)."""
    f = _good()
    f["tg"]["me"] = {"ok": False, "code": None, "err": "URLError: timed out"}
    f["journal"] = bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T08:00:00", "dropped msg=9 reason=origin origin_type=channel "
                                   f"origin_chat={_BAD} origin_username=NewName "
                                   "allowed_origins=['badonions']")])
    rc, out = _v(f)
    assert rc == 1 and "❌ 봇이 릴레이 포워드 1건을" in out, out


def test_split_by_start():
    t = NOW - timedelta(hours=2)
    fwd = [_fwd(t - timedelta(minutes=1), 3), _fwd(t + timedelta(minutes=1), 2), _fwd(None, 1)]
    cur, old = bh.split_by_start(fwd, t)
    assert [x["n"] for x in cur] == [2, 1] and [x["n"] for x in old] == [3]
    assert bh.split_by_start(fwd, None) == (fwd, [])     # 시작을 모르면 가르지 않는다


def test_losses_before_the_running_process_are_a_note_not_the_verdict():
    """재시작(배포) 전의 누락은 **지금** 상태의 증거가 아니다 — 옛 판·옛 설정의 일이다.
    그걸 판정에 섞으면 옛 판이 버림을 안 적었다는 사실이 새 판의 '버림 0건' 으로 둔갑해
    '텔레그램이 안 줬다' 로 오보한다(#165). 그 전 누락은 사실 메모로 따로 말하고, 다시
    포워드해 받았으면 메모도 사라진다(그 기간의 글이 결국 inbox 에 들어왔다)."""
    old_fwd = _jl("2026-09-25T07:49:09", "done: forwarded 27 of 27 candidate messages "
                  "(skipped_units=0)", logger="backfill_badonion", pid=99)
    new_fwd = _jl("2026-09-25T08:05:00", "done: forwarded 27 of 27 candidate messages "
                  "(skipped_units=0)", logger="backfill_badonion", pid=98)
    restarted = _start(ts="2026-09-25T08:00:00", pid=4242, drop_log=True)
    ingested = [_jl("2026-09-25T08:04:5%d" % (i % 10), f"ingested msg={i} mg=- caption=1 photo=-")
                for i in range(27)]

    def verdict_of(f):
        f["tg"] = _good()["tg"]
        return _v(f)
    # A. 재시작 뒤, 다시 포워드하기 전: 지금 판정은 깨끗하고 그 전 누락은 메모
    fa, _ = _collect([restarted, _poll("2026-09-25T08:29:50")], [old_fwd])
    assert fa["gap"]["kind"] == "none" and fa["gap_before"]["kind"] == "total"
    rc, out = verdict_of(fa)
    assert rc == 0, out
    assert "⚠️ 지금 프로세스가 뜨기 전" in out and "그 뒤 봇 수신 줄은 0건" in out
    assert "inbox 에 없다" not in out            # 수로 센 것을 inbox 사실로 단정하지 않는다
    assert "텔레그램이 전달하지 않았다" not in out
    # ⑤ 줄도 두 기준을 갈라 적는다 — 한 쌍으로 뭉치면 '보냄 27 / 받음 0' 이 지금 일로 읽힌다
    r5 = [ln for ln in bh.render(fa, "x") if ln.startswith("⑤")]
    assert r5 and "재시작 전 포워드 27건 / 그 뒤 수신 0건" in r5[0], r5
    assert "지금 프로세스 뒤 대조" not in r5[0], r5    # 재시작 뒤 포워드가 없으면 그 쌍은 없다
    # B. 다시 포워드해 다 받았다: 지금 판정도 그 전 메모도 깨끗하다
    fb, _ = _collect([restarted, _poll("2026-09-25T08:29:50"), *ingested], [old_fwd, new_fwd])
    assert fb["gap"]["kind"] == "ok" and fb["gap_before"]["kind"] == "ok"
    rc, out = verdict_of(fb)
    assert rc == 0 and "지금 프로세스가 뜨기 전" not in out, out
    # C. 대조군: 재시작 뒤 포워드를 못 받았으면 그건 지금의 ❌ 다 — 재시작 **전** 수신을
    #    지금 포워드의 몫으로 세면 안 된다(그러면 누락이 가려진다)
    before = [_jl("2026-09-25T07:50:0%d" % (i % 10), f"ingested msg={i} mg=- caption=1 photo=-",
                  pid=1111) for i in range(27)]
    fc, _ = _collect([*before, restarted, _poll("2026-09-25T08:29:50")], [old_fwd, new_fwd])
    assert fc["gap"]["kind"] == "total", fc["gap"]
    rc, out = verdict_of(fc)
    assert rc == 1 and "❌ 릴레이가 27건을 포워드했는데" in out
