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


def _poll(ts: str, code: str = "200 OK", pid: int = 4242) -> str:
    return _jl(ts, f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getUpdates '
                   f'"HTTP/1.1 {code}"', logger="httpx", pid=pid)


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


def _relay_line(script: str, head: str, args, ts: str = "2026-09-25T07:49:09",
                pid: int = 99) -> tuple[str, str]:
    """릴레이 스크립트의 **실제** 로깅 형식(`basicConfig(format=…)`)·로거 이름
    (`getLogger(…)`)·메시지 형식으로 저널 한 줄을 만든다 → (줄, 로거 이름).

    형식·로거 이름을 손으로 적으면 그 둘이 바뀌어도 테스트가 축복한다 — 구분자(' — ')
    나 로거 이름이 바뀌면 `_FWD_RE` 가 그 릴레이의 포워드를 조용히 못 센다(독립 리뷰
    P01·P03 생존 뮤테이션, #155·#91b)."""
    path = _REPO / "trade" / "scripts" / f"{script}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fmt = name = None
    for node in tree.body:
        call = node.value if isinstance(node, (ast.Expr, ast.Assign)) else None
        if not isinstance(call, ast.Call):
            continue
        attr = getattr(call.func, "attr", "")
        if attr == "basicConfig":
            fmt = next(ast.literal_eval(k.value) for k in call.keywords if k.arg == "format")
        elif attr == "getLogger" and isinstance(node, ast.Assign) and call.args:
            name = ast.literal_eval(call.args[0])
    assert fmt and name, (script, fmt, name)
    rec = logging.LogRecord(name, logging.INFO, str(path), 1,
                            _log_format(path, head) % args, None, None)
    return f"{ts}+0900 host python[{pid}]: {logging.Formatter(fmt).format(rec)}", name


def test_relay_forward_parser_reads_every_real_relay_format():
    """릴레이 넷의 실제 로그 형식 — 하나라도 파서와 갈리면 그 릴레이의 포워드는 대조에서
    영영 빠진다(#38·#24 목록이 아니라 소스에서). 로깅 형식·로거 이름까지 소스에서."""
    cases = {
        "backfill_badonion": ("done: forwarded", (27, 27, 0)),
        "backfill_beon": ("done: forwarded", (5, 6, 1)),
        "listen_badonion": ("forwarded %d msg", (3, [11, 12, 13])),
        "listen_beon": ("forwarded %d msg", (1, [7])),
    }
    lines, names = [], []
    for script, (head, args) in cases.items():
        ln, name = _relay_line(script, head, args)
        lines.append(ln)
        names.append(name)
    got = bh.relay_forwards(lines)
    assert [(g["who"], g["n"], g["done"]) for g in got] == [
        (names[0], 27, True), (names[1], 5, True), (names[2], 3, False), (names[3], 1, False)]
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
    ingested = [done - timedelta(seconds=60)] * 27
    assert bh.delivery_gap([_fwd(done, 27, done=True)], ingested, NOW)["kind"] == "ok"
    # 대조군: 리스너 사건(실행 끝이 아니다)엔 그 여유를 주지 않는다
    lis = bh.delivery_gap([_fwd(done, 27)], ingested, NOW)
    assert lis["kind"] == "total"
    # 리스너는 봇이 로그보다 먼저 받을 수 있다 — LISTEN_SLACK_S 안은 센다(경계 포함)
    early = bh.delivery_gap([_fwd(done, 1)], [done - timedelta(seconds=2)], NOW)
    assert early["kind"] == "ok"
    edge = bh.delivery_gap([_fwd(done, 1)], [done - timedelta(seconds=bh.LISTEN_SLACK_S)], NOW)
    assert edge["kind"] == "ok", edge                  # 시작과 같은 시각의 수신도 센다


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
    monkeypatch.setattr(bh, "delivery_check", lambda window: windows.append(window) or dict(g))
    hc.check_delivery_gap()
    hc.check_delivery_gap()
    assert len(hc._sent) == 1, hc._sent                    # 같은 누락은 한 번만
    assert "27건" in hc._sent[0] and "python -m trade.bot_health" in hc._sent[0]
    assert windows == [hc.DELIVERY_WINDOW_S] * 2


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
    g = bh.delivery_check(7200, now=NOW, read=read)
    assert asked == [bh.RELAY_UNITS, (bh._SERVICE,)]
    assert g["kind"] == "total" and g["sent"] == 3
    # 봇 저널 권한이 없으면 판정 불가 — '못 받았다' 로 단정하지 않는다
    g2 = bh.delivery_check(7200, now=NOW, read=lambda u, s: (
        ([_jl("2026-09-25T07:49:09", "forwarded 3 msg(s): [1]", logger="listen_beon")], "", "")
        if u == bh.RELAY_UNITS else ([], "권한 없음", "denied")))
    assert g2["kind"] == "unknown" and "권한" in g2["err"]
    # 릴레이 저널에 줄이 없으면(조용함) 판정할 것이 없다
    g3 = bh.delivery_check(7200, now=NOW, read=lambda u, s: ([], "없다", "rotated"))
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
    했다(#38). 그리고 `systemctl status` 는 저널 꼬리를 같이 찍는데 봇이 토큰을 가리기
    전 판이 찍은 줄엔 토큰이 평문이다 — 그 명령을 권하면 붙여 넣는 순간 샌다(§Secrets)."""
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
    g = bh.delivery_check(7200, now=NOW, read=lambda u, s: ([], "권한 없음", "denied"))
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
    assert "[-100111]" in out and "직접 쓴 글 1 · 다른 출처 포워드 1: @SomeNews" in out
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


# ── 독립 리뷰(a814a77..6f407d9) 반영 ─────────────────────────────────────
# H1 재시작 범위 · H2 버림=받음 · M1 창 경계 · M2 핸들러 예외 · M3 비밀값 · M4 알림 키 ·
# Low · 생존 뮤테이션(리뷰 실측 13종) — 각 테스트 독스트링이 무엇을 막는지 적는다.

def _drop(ts: str, msg: int, chat, user, pid: int = 4242) -> str:
    return _jl(ts, f"dropped msg={msg} reason=origin origin_type=channel origin_chat={chat} "
                   f"origin_username={user} allowed_origins=['badonions', 'beon_beclear']",
               pid=pid)


def test_gate_drops_are_received_not_lost():
    """H2 — 봇이 출처 게이트에서 버린 포워드는 **받은** 것이다. BeOn 이 되포워드한 남의
    글(원래 출처가 붙는다)을 '못 받음' 으로 세면 매번 거짓 누락 알림이다."""
    t0 = NOW - timedelta(minutes=40)
    fw = [_fwd(t0, 3, who="listen_beon")]
    ing = [t0 + timedelta(seconds=1)]
    drops = [{"ts": t0 + timedelta(seconds=2), "relay": False},
             {"ts": t0 + timedelta(seconds=3), "relay": False}]
    g = bh.delivery_gap(fw, ing, NOW, dropped=drops)
    assert (g["kind"], g["got"], g["dropped"], g["relay_dropped"]) == ("ok", 1, 2, 0), g
    assert not bh.gap_needs_alert(g)
    # 릴레이 원천의 글을 버렸으면 받았어도 알린다 — inbox 에 안 들어가기는 마찬가지다
    g2 = bh.delivery_gap(fw, ing, NOW, dropped=[{**drops[0], "relay": True}, drops[1]])
    assert g2["kind"] == "ok" and g2["relay_dropped"] == 1 and bh.gap_needs_alert(g2)
    # 대조 창 앞의 버림으로 지금 누락을 가리지 않는다
    old = [{"ts": t0 - timedelta(hours=1), "relay": False}] * 2
    g3 = bh.delivery_gap(fw, ing, NOW, dropped=old)
    assert g3["kind"] == "partial" and g3["dropped"] == 0


def test_forward_drops_classifies_relay_by_name_or_id_and_skips_direct_posts():
    """직접 쓴 글(type=none)은 포워드가 아니라 받음에 안 센다. 릴레이 원천은 이름(대소문자
    무시) **또는** ID 로 알아본다 — 이름을 바꾼 원천은 ID 로만, ID 를 모르면 이름으로만
    알아볼 수 있다(생존 뮤테이션 M05: 이름 절을 지워도 통과했다)."""
    raw = [{"type": "none", "chat": None, "user": ""},
           {"type": "channel", "chat": _BAD, "user": "NewName"},
           {"type": "channel", "chat": -100555, "user": "Badonions"},
           {"type": "channel", "chat": -100777, "user": "awake_plus"},
           {"type": "legacy", "chat": -100888, "user": ""}]
    assert [d["relay"] for d in bh.forward_drops(raw, ["Badonions"], {_BAD})] == [
        True, True, False, False]
    assert [d["relay"] for d in bh.forward_drops(raw, ["badonions"], ())] == [
        False, True, False, False]


def test_third_party_origin_drops_through_collect_are_not_a_gap(tmp_path):
    """H2 E2E — 리뷰 재현(BeOn 이 AWAKE 플러스 글 둘을 되포워드, 봇이 둘 다 버림)을 수집기로
    태운다: 대조는 ok, 판정은 ✅ + 다른 출처 메모, '텔레그램이 안 줬다' 는 없다."""
    fwd, _ = _relay_line("listen_beon", "forwarded %d msg", (3, [1, 2, 3]),
                         ts="2026-09-25T07:50:00", pid=55)
    bot = [_start(), _poll("2026-09-25T08:29:50"),
           _jl("2026-09-25T07:50:01", "ingested msg=10 mg=- caption=12 photo=-"),
           _drop("2026-09-25T07:50:02", 11, -100555, "awake_plus"),
           _drop("2026-09-25T07:50:03", 12, -100555, "awake_plus")]
    f, _ = _collect(bot, [fwd])
    assert f["gap"]["kind"] == "ok" and f["gap"]["dropped"] == 2, f["gap"]
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 0, out
    assert "다른 출처 포워드 2: @awake_plus" in out
    assert "텔레그램이 전달하지 않았다" not in out


def test_partial_gap_is_a_red_symptom_with_the_numbers():
    """생존 뮤테이션 N02(판정이 partial 을 무시)·M02(after_last=0) — 일부 누락도 ❌ 이고,
    받은 수·빠진 수·마지막 포워드 뒤 수신을 숫자로 적는다."""
    t0 = NOW - timedelta(minutes=40)
    ing = [t0 + timedelta(seconds=1), t0 + timedelta(seconds=90), t0 + timedelta(seconds=91)]
    g = bh.delivery_gap([_fwd(t0, 3), _fwd(t0 + timedelta(seconds=60), 2)], ing, NOW)
    assert (g["kind"], g["got"], g["after_last"]) == ("partial", 3, 2), g
    f = _good()
    f["gap"] = g
    rc, out = _v(f)
    red = [ln for ln in out.splitlines() if ln.startswith("❌ 릴레이가 5건을 포워드했는데")]
    assert rc == 1 and red, out
    assert "봇이 받은 것은 3건이다" in red[0] and "2건은 수신·버림 어느 줄에도 없다" in red[0]
    assert "마지막 포워드 뒤 수신 2건" in red[0], red[0]


def test_unexplained_partial_with_third_party_drops_does_not_claim_no_drops():
    """H2 — 버림이 있었는데 '버림 기록도 없는데' 라고 쓰면 거짓이다. 일부 누락은 '텔레그램이
    전부 안 줬다' 가 아니다. 짐작한 시작(창 밖에서 시작한 백필)이면 그렇다고 밝힌다."""
    f = _good()
    f["gap"] = {"kind": "partial", "sent": 5, "got": 1, "dropped": 2, "relay_dropped": 0,
                "first": NOW - timedelta(minutes=40), "who": ["listen_beon"],
                "after_last": 0, "guessed": False}
    _rc, out = _v(f)
    assert "2건이 수신·버림·예외 어느 줄에도 없다(받은 3건 중 2건은 출처 게이트가 버린" in out
    assert "버림·예외 기록도 없는데" not in out and "버림 기록도 없는데" not in out
    f["gap"]["guessed"] = True
    _rc, out2 = _v(f)
    assert "시작을 짐작했다" in out2 and "--since 를 넓혀" in out2
    assert "텔레그램이 일부를 전달하지 않았다" not in out2


def test_verdict_judges_the_running_process_and_notes_the_previous_one():
    """H1 — 재시작 전 프로세스의 409·릴레이 버림·목적지 채널 버림은 옛 판·옛 설정의 일이다.
    지금 판정(❌)에 섞지 말고 메모로. 대조군: 같은 줄이 **지금** 프로세스 것이면 ❌."""
    old = [_start(ts="2026-09-25T06:00:00", pid=1111),
           _poll("2026-09-25T06:10:00", "409 Conflict", pid=1111),
           _drop("2026-09-25T06:20:00", 9, _BAD, "Badonions", pid=1111),
           _jl("2026-09-25T06:21:00", f"dropped channel={_DEST} reason=channel allowed=[1]",
               pid=1111)]
    new = [_start(ts="2026-09-25T08:00:00", pid=4242), _poll("2026-09-25T08:29:50")]
    f, _ = _collect(old + new)
    assert f["journal_cur"]["n"] == 2 and f["running"]["pid"] == 4242
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 0, out
    assert "⚠️ 재시작 전 프로세스가 폴링을 409 Conflict 로 1회" in out
    assert "⚠️ 재시작 전 프로세스가 릴레이 포워드 1건을 출처 게이트에서" in out
    assert "⚠️ 재시작 전 프로세스가 릴레이 목적지의 글을 채널 게이트에서" in out
    cur = [_poll("2026-09-25T08:10:00", "409 Conflict"),
           _drop("2026-09-25T08:20:00", 9, _BAD, "Badonions"),
           _jl("2026-09-25T08:21:00", f"dropped channel={_DEST} reason=channel allowed=[1]")]
    f2, _ = _collect([*new, *cur])
    f2["tg"] = _good()["tg"]
    rc2, out2 = _v(f2)
    red = [ln for ln in out2.splitlines() if ln.startswith("❌")]
    assert rc2 == 1 and len(red) == 3, out2
    assert "409 Conflict 로 1회" in red[0] and "재시작 전" not in out2


def test_a_just_started_process_is_not_called_stalled_before_its_first_poll():
    """H1 로 폴링 판정을 지금 프로세스 줄로 좁히면, 막 뜬 프로세스는 첫 폴링 전이라 '폴링
    0회' 다 — 멈춤과 같은 창(POLL_STALE_S)만큼은 봐 준다. 대조군: 오래 떠 있는데 0회면 ❌."""
    f, _ = _collect([_start(ts="2026-09-25T08:28:00")])
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert "정상 폴링(getUpdates 200)이 한 번도 없다" not in out and rc == 0, out
    f2, _ = _collect([_start(ts="2026-09-25T08:00:00")])
    f2["tg"] = _good()["tg"]
    rc2, out2 = _v(f2)
    assert rc2 == 1 and "❌ 지금 프로세스에 정상 폴링(getUpdates 200)이 한 번도 없다" in out2


def test_relay_run_start_comes_from_the_same_pid_connect_line():
    """M1 — 백필 'done' 은 실행 끝에 찍힌다. 수신은 같은 PID 가 접속 직후 찍은
    `source(marked)=` 줄부터 센다(30분 짐작이 아니라). 그 줄이 창 밖이면 짐작하고 표시한다.
    리스너는 포워드 시각 − LISTEN_SLACK_S. 원천 ID 도 그 줄에서 배운다(형식은 소스에서)."""
    src, _ = _relay_line("backfill_badonion", "source(marked)=", (_BAD, _DEST),
                         ts="2026-09-25T07:40:00", pid=99)
    done, _ = _relay_line("backfill_badonion", "done: forwarded", (27, 27, 0), pid=99)
    other, _ = _relay_line("backfill_beon", "source(marked)=", (_BEON, _DEST),
                           ts="2026-09-25T07:45:00", pid=77)
    fw = bh.relay_forwards([src, other, done])
    assert fw[0]["start"] == datetime(2026, 9, 25, 7, 40, tzinfo=_KST) and not fw[0]["guessed"]
    fw2 = bh.relay_forwards([done])
    assert fw2[0]["guessed"] and fw2[0]["start"] == (
        datetime(2026, 9, 25, 7, 49, 9, tzinfo=_KST) - timedelta(seconds=bh.RUN_SLACK_S))
    lsrc, _ = _relay_line("listen_badonion", "connected: source(marked)=", (_BAD, _DEST),
                          ts="2026-09-24T00:00:00", pid=55)
    lfw, _ = _relay_line("listen_badonion", "forwarded %d msg", (3, [1, 2, 3]),
                         ts="2026-09-25T08:00:00", pid=55)
    f3 = bh.relay_forwards([lsrc, lfw])
    assert f3[0]["start"] == (datetime(2026, 9, 25, 8, 0, tzinfo=_KST)
                              - timedelta(seconds=bh.LISTEN_SLACK_S))
    assert bh.relay_source_ids([src, other, lsrc, done]) == {_BAD, _BEON}


def test_delivery_gap_counts_from_the_earliest_start_among_mixed_forwards():
    """생존 뮤테이션 M04 의 새 모양 — 여러 사건이 섞이면 가장 이른 시작부터 센다(나중 것
    기준이면 앞 실행의 수신을 못 세어 거짓 누락)."""
    run_start = NOW - timedelta(minutes=50)
    fw = [{**_fwd(NOW - timedelta(minutes=40), 3, done=True, who="backfill_badonion"),
           "start": run_start},
          _fwd(NOW - timedelta(minutes=10), 1)]
    ing = [run_start + timedelta(seconds=5)] * 3 + [NOW - timedelta(minutes=10)]
    assert bh.delivery_gap(fw, ing, NOW)["kind"] == "ok"


def test_delivery_gap_boundaries():
    """생존 뮤테이션 M01(시작과 같은 시각의 수신) · M03(딱 grace 만큼 지난 포워드)."""
    t = NOW - timedelta(minutes=30)
    assert bh.delivery_gap([_fwd(t, 1)], [t - timedelta(seconds=bh.LISTEN_SLACK_S)],
                           NOW)["kind"] == "ok"
    due = bh.delivery_gap([_fwd(NOW - timedelta(seconds=bh.GAP_GRACE_S), 1)], [], NOW)
    assert due["kind"] == "total", due


def test_widen_since_moves_back_only_forms_it_understands():
    """M1 — 봇 저널을 릴레이 창보다 앞에서부터 읽으려면 사용자의 --since 를 앞당겨야 한다.
    모르는 꼴은 지어내지 않고 None(같은 창으로 읽고 그렇다고 밝힌다)."""
    assert bh.widen_since("86400 seconds ago", 1800) == "88200 seconds ago"
    assert bh.widen_since("2 hours ago", 60) == "7260 seconds ago"
    assert bh.widen_since("@1758000000", 1800) == "@1757998200"
    assert bh.widen_since("2026-09-25 07:40", 1800) == "2026-09-25 07:10:00"
    assert bh.widen_since("2026-09-24 22:40:30 UTC", 1800) == "2026-09-24 22:10:30 UTC"
    assert bh.widen_since("yesterday", 1800) is None
    assert bh.widen_since("5 fortnights ago", 1) is None


def test_delivery_check_reads_the_bot_journal_earlier_than_the_relay_window():
    """M1 — 리뷰 재현: 백필이 창 첫머리에 끝났고 그 실행의 수신은 창 **앞**에 있다. 같은 창
    으로 읽으면 멀쩡한 실행을 누락으로 알린다. 봇 저널은 RUN_SLACK_S 더 앞부터 읽는다.
    그리고 봇 수신을 실제로 세는지(생존 뮤테이션 N01: 봇 저널을 버려도 통과했다)."""
    t_done = datetime(2026, 9, 25, 8, 0, tzinfo=_KST)
    fmt = "%Y-%m-%dT%H:%M:%S"
    bot = [_jl((t_done - timedelta(seconds=28 - i)).strftime(fmt),
               f"ingested msg={i} mg=- caption=5 photo=-") for i in range(27)]
    relay = [_jl(t_done.strftime(fmt), "done: forwarded 27 of 27 candidate messages "
                 "(skipped_units=0)", logger="backfill_badonion", pid=99)]
    asked = []
    now = t_done + timedelta(seconds=7200 - 10)

    def read(units, since):
        asked.append((units, since))
        lo = now - timedelta(seconds=int(since.split()[0]))
        src = bot if units == (bh._SERVICE,) else relay
        return [ln for ln in src if bh._ts(ln) >= lo], "", ""
    g = bh.delivery_check(7200, now=now, read=read)
    assert (g["kind"], g["got"]) == ("ok", 27), g
    assert asked == [(bh.RELAY_UNITS, "7200 seconds ago"),
                     ((bh._SERVICE,), f"{7200 + bh.RUN_SLACK_S} seconds ago")]


def test_collect_reads_the_bot_journal_with_the_widened_window():
    asked = {}

    def read(units, since):
        asked[units] = since
        return [], "", "rotated"
    f = bh.collect("86400 seconds ago", now=NOW, env={"token": "", "dest": _DEST, "src": {},
                                                     "err": "", "inbox": ""},
                   facts_fn=lambda: {"ok": True, "s_MainPID": "0"}, read=read,
                   start_fn=lambda pid: (None, ""), tg_fn=lambda *a: {"token": False},
                   procs_fn=lambda own: [])
    assert asked[(bh._SERVICE,)] == f"{86400 + bh.RUN_SLACK_S} seconds ago"
    assert asked[bh.RELAY_UNITS] == "86400 seconds ago"
    head = bh.render(f, "86400 seconds ago")[2]
    assert "봇 저널은 수신을 세려고 30분 앞부터" in head, head


def test_delivery_check_classifies_relay_drops_by_name_and_by_journal_id():
    """H2 — 매시간 알림은 받음(기록+버림)으로 누락을 세고, **릴레이 원천의** 버림은 따로
    센다. 원천이 이름을 바꿨으면 버림 줄엔 새 이름이 찍히므로, 릴레이 저널의 접속 줄
    (`source(marked)=`)에서 배운 ID 로 알아본다. 다른 출처(AWAKE)는 알리지 않는다."""
    fwd = _jl("2026-09-25T07:49:09", "forwarded 3 msg(s): [1, 2, 3]", logger="listen_beon",
              pid=55)
    src = _jl("2026-09-25T07:00:00", f"connected: source(marked)={_BAD} dest(marked)={_DEST}",
              logger="listen_badonion", pid=56)

    def check(bot_lines, relay_lines):
        return bh.delivery_check(7200, now=NOW, read=lambda u, s: (
            (relay_lines if u == bh.RELAY_UNITS else bot_lines), "", ""))
    third = [_drop("2026-09-25T07:49:10", i, -100555, "awake_plus") for i in (1, 2, 3)]
    g = check(third, [fwd])
    assert (g["kind"], g["dropped"], g["relay_dropped"]) == ("ok", 3, 0) and not bh.gap_needs_alert(g)
    by_name = [_drop("2026-09-25T07:49:10", i, -100999, "Badonions") for i in (1, 2, 3)]
    assert check(by_name, [fwd])["relay_dropped"] == 3
    renamed = [_drop("2026-09-25T07:49:10", i, _BAD, "NewName") for i in (1, 2, 3)]
    assert check(renamed, [fwd])["relay_dropped"] == 0          # ID 를 모르면 못 알아본다
    g4 = check(renamed, [src, fwd])
    assert g4["relay_dropped"] == 3 and bh.gap_needs_alert(g4)


def test_handler_exceptions_in_the_gap_window_are_the_cause():
    """M2 — 받은 채널 글을 처리하다 예외로 끝난 번호(수신 줄 없음)는 '텔레그램이 안 줬다'
    가 아니라 원인(❌)이다. 기록된 뒤의 예외·대조 밖의 예외는 메모. 어느 업데이트였는지
    안 적힌 옛 판 예외(PTB 기본 문구)는 원인을 못 짚은 갈래에서 '표본부터' 로 말한다."""
    t0 = NOW - timedelta(minutes=40)
    exc = _jl("2026-09-25T07:50:05", "handler error update=channel_post msg=77 exc=OSError: "
              "[Errno 28] No space left on device").replace("[INFO]", "[ERROR]")
    f = _good()
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), exc])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW)
    rc, out = _v(f)
    assert rc == 1 and "❌ 봇이 받은 채널 글 1건을 처리하다 예외로 놓쳤다" in out, out
    assert "텔레그램이 전달하지 않았다" not in out
    # 같은 번호가 기록됐으면(기록 뒤 단계의 예외) 원인이 아니다 — 메모
    ok = _jl("2026-09-25T07:50:04", "ingested msg=77 mg=- caption=1 photo=-")
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), ok, exc])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW)
    rc2, out2 = _v(f)
    assert rc2 == 0 and "⚠️ 채널 글을 처리하다 예외가 1번 났다" in out2, out2
    # 누락은 있는데(2건 중 1건) 예외 난 번호는 **기록됐다** — 그 예외를 누락의 원인으로
    # 읽으면 안 된다(기록 뒤 단계였다). 번호 대조를 지우면 여기서 거짓 ❌ 가 난다.
    f["gap"] = bh.delivery_gap([_fwd(t0, 2)], f["journal"]["ingested"], NOW)
    assert f["gap"]["kind"] == "partial"
    _rc, out2b = _v(f)
    assert "예외로 놓쳤다" not in out2b and "⚠️ 채널 글을 처리하다 예외가 1번 났다" in out2b
    # 표식 없는 옛 판 예외 — 원인을 못 짚은 갈래가 그걸 말한다(텔레그램 탓으로 단정하지 않는다)
    ptb = _jl("2026-09-25T07:50:05", "No error handlers are registered, logging exception.",
              logger="telegram.ext.Application").replace("[INFO]", "[ERROR]")
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), ptb])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW)
    _rc3, out3 = _v(f)
    assert "PTB 예외가 1번 찍혔다" in out3 and "텔레그램이 전달하지 않았다" not in out3, out3
    f["running"] = bh.parse_start_line(_start(drop_log=False))
    _rc4, out4 = _v(f)
    assert "옛 판이라" in out4 and "PTB 예외가 1번 찍혔다" in out4, out4


def test_journal_facts_reads_exception_kinds_and_ingested_ids():
    lines = [
        _jl("2026-09-25T07:49:01", "ingested msg=77 mg=- caption=12 photo=-"),
        _jl("2026-09-25T07:49:02", "handler error update=channel_post msg=78 exc=OSError: x"),
        _jl("2026-09-25T07:49:03", "polling error exc=Conflict: terminated by other "
                                   "getUpdates request"),
        _jl("2026-09-25T07:49:04", "No error handlers are registered, logging exception.",
            logger="telegram.ext.Application"),
    ]
    j = bh.journal_facts(lines)
    assert j["ingested_ids"] == {77}
    assert [(e["kind"], e["update"], e["msg"]) for e in j["exceptions"]] == [
        ("handler", "channel_post", 78), ("polling", None, None), ("unlabeled", None, None)]
    assert all(e["ts"] is not None for e in j["exceptions"])
    assert all(d.get("ts") is not None for d in bh.journal_facts(
        [_drop("2026-09-25T07:49:05", 1, _BAD, "x")])["drops_origin"])


def test_other_bot_processes_scrubs_before_truncating_and_needs_an_interpreter(tmp_path):
    """M3a — 명령줄에 토큰이 든 실행(`sudo … env TRADE_BOT_TOKEN=… python -m trade.bot`)을
    ⑦ 에 그대로 찍었다(가리기 전에 자르면 잘린 조각이 패턴을 빠져나간다). M3b — 편집기·
    검색·테스트가 `trade/bot.py` 를 인자로 받은 것은 봇이 아니다."""
    def proc(pid, *argv):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    tok = "7123456789" + ":" + "AAH" + "f" * 32
    proc(10, "sudo", "-u", "higgack", "env", f"TRADE_BOT_TOKEN={tok}", "/v/bin/python",
         "-m", "trade.bot")
    proc(11, "vim", "trade/bot.py")
    proc(12, "grep", "-n", "x", "/home/h/stock-trade/trade/bot.py")
    proc(13, "/v/bin/python3", "-m", "pytest", "trade/bot.py")
    proc(14, "/usr/bin/python3.11", "-X", "dev", "-m", "trade.bot")
    proc(15, "/v/bin/python", "-c", "import trade.bot")
    # 토큰이 `KEY=` 없이 위치 인자로, 자르는 경계(160자)에 걸친 실행 — 자른 **뒤** 가리면
    # 잘린 조각(30자 미만)이 토큰 패턴을 빠져나가 앞부분이 샌다(`KEY=` 꼴은 다른 패턴이
    # 자르기와 무관하게 가려서 그것만으론 순서를 못 잰다)
    pad = ["--x"] * 25
    proc(16, "/v/bin/python", "-m", "trade.bot", *pad, tok)
    head = len(" ".join(["/v/bin/python", "-m", "trade.bot", *pad])) + 1 + len("7123456789:")
    # 경계에서 잘린 비밀 조각이 10~29자여야 이 픽스처가 순서를 잰다(30자 이상이면 토큰
    # 패턴이 잘린 조각도 잡고, 10자 미만이면 아래 단언이 못 본다 — 첫 판이 6자라 눈멀었다)
    assert 10 <= 160 - head < 30, 160 - head
    got = bh.other_bot_processes(set(), proc_root=tmp_path)
    assert sorted(o["pid"] for o in got) == [10, 14, 16], got
    line = [ln for ln in bh.render({"now": NOW, "others": got}, "x") if ln.startswith("⑦")][0]
    secret = tok.split(":")[1]
    assert "PID 10" in line and "PID 16" in line, line
    assert not any(secret[i:i + 10] in line for i in range(len(secret) - 9)), line


def test_collect_excludes_itself_and_the_running_bot_from_other_processes():
    """생존 뮤테이션 M13 — 지금 도는 봇(MainPID)을 '다른 trade.bot 프로세스' 로 세면 409 의
    원인으로 자기 자신을 가리킨다."""
    import os
    seen = []
    bh.collect("x", now=NOW, env={"token": "", "dest": _DEST, "src": {}, "err": "", "inbox": ""},
               facts_fn=lambda: {"ok": True, "s_MainPID": "4242"},
               read=lambda u, s: ([], "", "rotated"), start_fn=lambda pid: (None, ""),
               tg_fn=lambda *a: {"token": False}, procs_fn=lambda own: seen.append(own) or [])
    assert seen == [{os.getpid(), 4242}]


def test_no_trade_code_recommends_systemctl_status_for_the_bot_unit():
    """M3c — `systemctl status trade-bot` 은 저널 꼬리를 같이 찍고, 가림 이전 판이 찍은
    getUpdates 줄엔 토큰이 평문이다. 그 명령을 권하면 붙여 넣는 순간 샌다. 디렉터리 전수
    (#24) · 독스트링은 걷어내고(규칙을 설명하는 글이 스스로 걸린다, #59b)."""
    import re as _re
    pat = _re.compile(r"systemctl\s+status\b(?:\s+-\S+)*\s+trade-bot(?:\.service)?(?![\w.-])")
    # `journalctl` 로 trade-bot(또는 자리표시 `<unit>`)의 원문을 권하려면 가림을 같이 줘야 한다
    jpat = _re.compile(r"journalctl\b[^`\n]*-u\s+(?:trade-bot(?:\.service)?(?![\w.-])|<unit>)")
    hits, scanned = [], 0
    for p in [*sorted((_REPO / "trade").rglob("*.py")), _REPO / "bot" / "daily_kr_flow.py"]:
        if "tests" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docs.add(id(first.value))
        scanned += 1
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs):
                continue
            if pat.search(node.value) or (jpat.search(node.value) and "sed" not in node.value):
                hits.append(f"{p.relative_to(_REPO)}:{node.lineno}")
    assert scanned > 50, scanned                          # 대조 0건은 통과가 아니다(#54)
    assert not hits, hits
    # 반대 증거(#25): 패턴은 실제 옛 문구를 잡는다
    assert pat.search("리스너가 죽었다. `systemctl status trade-bot` 부터")
    assert not pat.search("systemctl status trade-bot-beon-listener")
    assert jpat.search("판정 불가(`sudo journalctl -u <unit> -n 50` 로 확인)")
    assert not jpat.search("journalctl -u trade-bot-jpx-codes")


def test_relay_units_match_the_deploy_units_that_run_relay_scripts():
    """생존 뮤테이션 Q01 — RELAY_UNITS 에서 하나를 빼도 통과했다. 손으로 적은 목록은
    `deploy/*.service` 중 SOURCE_USERNAME 을 둔 스크립트를 띄우는 유닛과 **양방향**으로
    같아야 한다(빠지면 그 릴레이의 포워드는 대조에서 조용히 빠진다, #24)."""
    import re as _re
    relay_scripts = {s for v in bh.relay_sources(_REPO / "trade" / "scripts").values() for s in v}
    runs = {}
    for unit in sorted((_REPO / "deploy").glob("*.service")):
        m = _re.search(r"^ExecStart=.*?(?:-m trade\.scripts\.(\w+)|trade/scripts/(\w+)\.py)",
                       unit.read_text(encoding="utf-8"), _re.M)
        if m:
            runs[unit.stem] = m.group(1) or m.group(2)
    want = {u for u, s in runs.items() if s in relay_scripts}
    assert want == set(bh.RELAY_UNITS), (want, bh.RELAY_UNITS)
    assert len(want) == 4                                  # 반대 증거: 실제로 넷을 찾았다


def test_trade_env_prefers_the_env_file_and_flags_root(tmp_path, monkeypatch):
    """생존 뮤테이션 M11(환경변수가 .env 를 이김)·M12(root 표식 꺼짐) — 봇은 systemd 로 떠
    셸 환경이 없으므로 `.env` 가 먼저다. 값은 판정에만, 출력엔 출처만."""
    (tmp_path / ".env").write_text("TRADE_BOT_TOKEN=from-file\nTRADE_CHANNEL_CHAT_IDS=-1001,-1002\n")
    monkeypatch.setattr(bh, "_REPO", tmp_path)
    monkeypatch.setenv("TRADE_BOT_TOKEN", "from-env")
    monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setattr(bh.os, "geteuid", lambda: 0, raising=False)
    e = bh.trade_env()
    assert e["token"] == "from-file" and e["src"]["TRADE_BOT_TOKEN"] == ".env"
    assert e["dest"] == -1001
    assert e["src"]["TRADE_DATA_DIR"] == "환경변수"
    assert e["inbox"] == str(tmp_path / "d" / "inbox.jsonl")
    assert e["as_root"] is True
    monkeypatch.setattr(bh.os, "geteuid", lambda: 1000, raising=False)
    assert bh.trade_env()["as_root"] is False


def test_find_start_line_stops_at_the_cap_and_names_an_unparseable_line():
    """생존 뮤테이션 M09(상한 제거) — 시작 줄이 없는 긴 저널을 끝까지 읽으면 안 된다.
    Low — 줄은 있는데 형식을 못 읽은 것과 '없다' 는 처방이 다르다."""
    read = []

    class P:
        def __init__(self, lines):
            self.stdout = (read.append(ln) or ln for ln in lines)

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0
    got, err = bh.find_start_line(4242, popen=lambda cmd, **kw: P(["x\n"] * 10_000),
                                  max_lines=50)
    assert got is None and len(read) == 51, len(read)
    bad = f"2026-09-25T08:00:00+0900 h python[4242]: trade-bot starting — 형식이 바뀐 줄 {TOKEN}\n"
    got2, err2 = bh.find_start_line(4242, popen=lambda cmd, **kw: P([bad]))
    assert got2 is None and "형식을 못 읽었다" in err2 and TOKEN.split(":")[1] not in err2


def test_running_err_says_systemd_was_not_asked():
    """Low — systemd 에 못 물었는데 '실행 중인 프로세스가 없다' 고 단정하지 않는다(#165)."""
    f = bh.collect("x", now=NOW, env={"token": "", "dest": _DEST, "src": {}, "err": "", "inbox": ""},
                   facts_fn=lambda: {"ok": False, "err": "boom"},
                   read=lambda u, s: ([], "", "rotated"), start_fn=lambda pid: (None, ""),
                   tg_fn=lambda *a: {"token": False}, procs_fn=lambda own: [])
    assert f["running_err"] == "systemd 에 못 물어 실행 중인 PID 를 모른다"


def test_render_details_low_findings():
    """Low — 머리말이 실제로 묻는 메서드를 다 적는다(getChat 누락) · 수신 종류가 응답에
    없으면 '기본값' 이라 단정하지 않는다 · 시각 없는 inbox 는 '—()' 가 아니다 · 텔레그램
    전달 오류 문구의 토큰은 가린다(생존 뮤테이션 M10)."""
    f = _good()
    f["tg"]["webhook"]["result"].update(last_error_date=1758700000,
                                        last_error_message=f"Wrong response {TOKEN}")
    f["inbox"] = {"exists": True, "path": "/x/inbox.jsonl", "lines": 3, "last": None, "by": {}}
    lines = bh.render({**f, "env": {"dest": _DEST, "src": {}}, "facts": {}}, "x")
    blob = "\n".join(lines)
    # `getChat` 은 `getChatMember` 의 부분문자열이다 — 포함으로 재면 빠져도 통과한다(#75)
    listed = lines[1].split("텔레그램엔 ", 1)[1].split(" 만 묻는다", 1)[0].split("·")
    assert set(listed) == set(bh.READ_ONLY_METHODS), lines[1]
    assert "수신 종류 응답에 없음(판정 안 함)" in blob and "기본값" not in blob
    assert "—()" not in blob and "시각을 읽을 수 있는 기록 없음" in blob
    assert TOKEN.split(":")[1] not in blob and "마지막 전달 오류" in blob


def test_pending_updates_409_where_and_inbox_24h_count(tmp_path):
    """생존 뮤테이션 M15(대기 업데이트 메모) · M19(409 에 이 서버의 다른 프로세스를 짚음) ·
    M16(24시간 건수가 오래된 기록까지 셈)."""
    f = _good()
    f["tg"]["webhook"]["result"]["pending_update_count"] = 5
    _rc, out = _v(f)
    assert "⚠️ 텔레그램에 봇이 아직 안 가져간 업데이트 5건" in out
    f["journal"] = bh.journal_facts([_poll("2026-09-25T08:29:50", "409 Conflict"),
                                     _poll("2026-09-25T08:29:55")])
    f["others"] = [{"pid": 777, "cmd": "python -m trade.bot"}]
    _rc, out2 = _v(f)
    assert "이 서버에 다른 trade.bot 프로세스가 있다: 777" in out2
    ib = tmp_path / "inbox.jsonl"
    rows = [{"ingested_at": (NOW - timedelta(hours=h)).isoformat(),
             "forward_origin_chat_username": "Badonions", "forward_origin_chat_id": _BAD}
            for h in (1, 2, 30)]
    ib.write_text("".join(json.dumps(r) + "\n" for r in rows))
    e = bh.inbox_facts(ib, NOW)["by"]["badonions"]
    assert e["n24"] == 2 and e["ids"] == {_BAD}


def test_relay_drop_is_recognised_by_the_getchat_id_when_the_inbox_has_none():
    """생존 뮤테이션 N07 — 원천 ID 를 지금의 getChat 에서도 배운다. 원천이 사용자명을 여럿
    가질 때(예: @Badonions 가 보조 이름이면 getChat 은 주 이름을 준다) 버림 줄엔 주 이름이
    찍혀 이름으로는 못 알아본다 — inbox 가 비어 있으면(첫 수신 전) getChat 이 유일한 ID 다."""
    f = _good()
    f["inbox"] = {"exists": True, "path": "/x/inbox.jsonl", "lines": 0, "last": None, "by": {}}
    f["tg"]["sources"] = {"Badonions": {"ok": True, "result": {"id": _BAD,
                                                              "username": "badonion_main"}}}
    f["journal"] = bh.journal_facts([_poll("2026-09-25T08:29:50"),
                                     _drop("2026-09-25T08:00:00", 9, _BAD, "badonion_main")])
    _rc, out = _v(f)
    assert "❌ 봇이 릴레이 포워드 1건을 **출처 게이트**에서 버렸다" in out, out


def test_main_returns_the_verdict_rc(monkeypatch, capsys):
    """생존 뮤테이션 M08 — main 이 늘 0 을 돌려주면 셸의 `&&` 가 ❌·❓ 에서도 다음 명령
    (재포워드)을 돌린다. 판정의 rc 를 그대로 돌려줘야 한다."""
    base = _good()
    base.update(facts={}, journal_err="", forwards=[],
                env={"dest": _DEST, "src": {}, "err": "", "inbox": "/home/h/.trade/inbox.jsonl"})
    bad = {**base, "unit": {"kind": "stopped", "text": "멈춰 있다"}}
    unk = {**base, "unit": {"kind": "unknown", "text": "x"}}
    for f, want in ((bad, 1), (unk, 2)):
        monkeypatch.setattr(bh, "collect", lambda since, f=f: f)
        assert bh.main([]) == want
    capsys.readouterr()


def test_restart_count_note_when_the_bot_keeps_starting():
    """Low — 한 스냅샷의 상태만 보면 재시작 루프를 놓친다. 창 안 시작 횟수로 말한다."""
    f = _good()
    f["facts"] = {"s_NRestarts": "7"}
    f["journal"] = bh.journal_facts([_start(ts=f"2026-09-25T0{h}:00:00") for h in (5, 6, 7)]
                                    + [_poll("2026-09-25T08:29:50")])
    _rc, out = _v(f)
    assert "⚠️ 창 안에서 봇이 3번 시작했다(systemd 자동 재시작 누적 7회)" in out


# ── 매시간 알림: 누락마다 표식(M4) · 일부 누락도 알림(생존 뮤테이션 M07) ─────────

def test_health_check_alerts_a_new_gap_even_right_after_another(hc, monkeypatch):
    """M4 — 옛 판은 표식 하나('delivery-gap')로 6시간을 막아 그 사이 **다른** 누락이
    조용했다. 누락마다 표식이다. 같은 누락은 여전히 한 번만."""
    t1 = NOW - timedelta(minutes=40)
    gaps = [{"kind": "total", "sent": 27, "got": 0, "dropped": 0, "relay_dropped": 0,
             "first": t1, "who": ["backfill_badonion"], "after_last": 0, "err": ""},
            {"kind": "partial", "sent": 3, "got": 1, "dropped": 0, "relay_dropped": 0,
             "first": t1 + timedelta(minutes=20), "who": ["listen_beon"], "after_last": 0,
             "err": ""}]
    it = iter([gaps[0], gaps[0], gaps[1]])
    monkeypatch.setattr(bh, "delivery_check", lambda window: dict(next(it)))
    for _ in range(3):
        hc.check_delivery_gap()
    assert len(hc._sent) == 2, hc._sent
    assert "1건만 받았습니다" in hc._sent[1]
    assert bh.gap_alert_key(gaps[0]) != bh.gap_alert_key(gaps[1])


def test_health_check_alerts_relay_gate_drops_but_not_third_party(hc, monkeypatch):
    base = {"kind": "ok", "sent": 3, "got": 0, "dropped": 3, "first": NOW, "who": ["listen_beon"],
            "after_last": 0, "err": ""}
    monkeypatch.setattr(bh, "delivery_check", lambda window: {**base, "relay_dropped": 0})
    hc.check_delivery_gap()
    assert hc._sent == []
    monkeypatch.setattr(bh, "delivery_check", lambda window: {**base, "relay_dropped": 2})
    hc.check_delivery_gap()
    assert len(hc._sent) == 1 and "출처 게이트 버림" in hc._sent[0]
    assert "2건은 봇이 출처 게이트에서 버렸습니다" in hc._sent[0]


def test_health_check_prunes_old_delivery_markers_only(hc):
    import os
    import time as _t
    old = hc.MARKER_DIR / "delivery-gap-1-old"
    fresh = hc.MARKER_DIR / "delivery-gap-2-new"
    other = hc.MARKER_DIR / "cycle-gap-2026-09-01"
    for m in (old, fresh, other):
        m.touch()
    past = _t.time() - 3 * 86400
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    hc._prune_delivery_markers()
    assert not old.exists() and fresh.exists() and other.exists()
