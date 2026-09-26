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


def _start(ts="2026-09-24T23:53:10", *, drop_log=True, vouch=True, pid=4242,
           inbox="/home/h/.trade/inbox.jsonl",
           allowed=f"{{{_DEST}}}", origin="{'badonions', 'beon_beclear'}") -> str:
    """봇 시작 줄. 기본은 **지금 판**(버림 기록 + 재게시 보증, 실수 #406·#411).
    `vouch=False` = 버림은 적지만 보증을 모르는 판(2026-09-25 80c0e6c) · `drop_log=False`
    = 둘 다 없는 더 옛 판."""
    tail = (" drop_log=on" + (" relay_vouch=on" if vouch else "")) if drop_log else ""
    return _jl(ts, f"trade-bot starting — inbox={inbox} media=/home/h/.trade/media "
                   f"allowed={allowed} origin={origin} concurrency=8{tail}", pid=pid)


# ── 생산자 ↔ 소비자: 실제 로그 형식에서 픽스처를 만든다(#155) ─────────────────

def _log_format(path: Path, head: str, level: str = "info") -> str:
    """소스의 `log.<level>("...", ...)` 첫 인자(문자열 연결 포함)를 AST 로 꺼낸다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == level
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
    assert p["vouch"] is True                    # 재게시 보증을 받는 판이라는 표식(#411)
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
                "who": ["backfill_badonion"]}
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
                   procs_fn=lambda own: [], deploys_fn=lambda: ([], ""))
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
    # 재게시 보증 기록도 데이터 디렉터리에서 읽는다(실수 #411) — 운영 파일을 읽으면 운영자가
    # 재게시 글을 포워드하는 순간 무관한 테스트의 판정이 바뀐다(#373·#30).
    monkeypatch.setattr(hc, "DATA_DIR", tmp_path)
    sent = []
    # 전달됐다고 답한다 — 기록은 전달된 알림만 한다(3차 독립 리뷰 M4). 실패는 따로 잰다.
    monkeypatch.setattr(hc, "_notify", lambda msg: sent.append(msg) or True)
    hc._sent = sent
    return hc


def _drive(monkeypatch, *, bot, relay, clock=None):
    """health_check 가 부르는 `delivery_check` 를 **진짜 함수**로 두고 저널만 가짜로 — 알림
    기록(seen) 배선까지 태운다(옛 판은 `delivery_check` 를 통째로 갈아끼워 표식 키를 줄여도
    통과했다 — 2차 리뷰 생존 뮤테이션 B21·B22·H01). `bot`·`relay` 는 부를 때 읽는다(테스트가
    실행 사이에 줄을 더할 수 있다). 부른 창을 돌려준다."""
    real = bh.delivery_check
    windows = []

    def read(units, since):
        lo = (clock or [NOW])[0] - timedelta(seconds=int(since.split()[0]))
        src = relay if units == bh.RELAY_UNITS else bot
        return [ln for ln in src if bh._ts(ln) >= lo], "", ""

    def check(window, **kw):
        windows.append(window)
        return real(window, now=(clock or [NOW])[0], read=read, **kw)
    monkeypatch.setattr(bh, "delivery_check", check)
    return windows


def test_health_check_alerts_once_on_a_gap(hc, monkeypatch):
    """같은 누락은 한 번만 — 알린 사실의 신원(`fact_id`)을 기록해 다음 실행이 빼고 대조한다
    (2차 독립 리뷰 L4). 기록은 파일로 — 실행은 매시간 새 프로세스다."""
    fwd = _jl("2026-09-25T07:49:09", "done: forwarded 27 of 27 candidate messages "
              "(skipped_units=0)", logger="backfill_badonion", pid=99)
    windows = _drive(monkeypatch, bot=[_poll("2026-09-25T08:29:50")], relay=[fwd])
    hc.check_delivery_gap()
    hc.check_delivery_gap()
    assert len(hc._sent) == 1, hc._sent                    # 같은 누락은 한 번만
    assert "27건" in hc._sent[0] and "python -m trade.bot_health" in hc._sent[0]
    assert "한 건도 못 받았습니다" in hc._sent[0]
    assert windows == [hc.DELIVERY_WINDOW_S] * 2
    saved = json.loads((hc.MARKER_DIR / hc.DELIVERY_ALERTED).read_text(encoding="utf-8"))
    want = bh.fact_id("fwd", {"who": "backfill_badonion", "pid": 99, "n": 27,
                              "ts": datetime(2026, 9, 25, 7, 49, 9, tzinfo=_KST)})
    assert list(saved) == [want], saved


@pytest.mark.parametrize("kind", ["ok", "none", "unknown"])
def test_health_check_is_quiet_unless_a_gap(hc, monkeypatch, caplog, kind):
    monkeypatch.setattr(bh, "delivery_check",
                        lambda since, **kw: {"kind": kind, "sent": 0, "got": 0, "err": "권한"})
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

    def boom(since, **kw):
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
                        lambda since, **kw: ran.append("delivery") or {"kind": "none", "sent": 0,
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
    직접 쓴 글은 게이트가 제 일을 한 것이다. 릴레이가 아닌 곳의 포워드는 **여기서 못
    가른다**(3차 독립 리뷰 M2 — 나쁜양파의 재게시면 손실이다, 문구는 아래 전용 테스트).
    어느 쪽이든 ❌ 로 세면 BeOn 되포워드마다 거짓 경보가 되고 진짜 ❌ 를 가린다(#260) —
    그래서 ⚠️ 메모이고 rc 는 0 이다."""
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
                "who": ["listen_badonion"]}
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


def test_split_at_restart_boundary_measured_start_guess_and_moved_events():
    """2차 독립 리뷰 M2·V04 — 옛 `split_by_start`(끝으로만 가르고 수신은 재시작 뒤부터)는
    재시작을 걸친 실행의 옛 프로세스 몫을 버려 멀쩡한 실행을 누락으로 오보했다. 지금 계약:
    ① 끝이 재시작과 **같은** 시각이면 지금 몫(경계 포함 — 생존 뮤테이션 V04 `>` 가 통과했다)
    ② 잰 시작(같은 PID 접속 줄)·리스너 여유가 재시작 전이면 수신을 거기부터 센다(nb 당김)
    ③ 당긴 창에 끝이 걸린 옛 사건은 지금 몫으로 옮긴다 ④ 백필 끝에서 30분 **짐작한** 시작은
    경계를 넘지 않는다(옛 사건을 끌어들여 재시작 전 누락이 지금 누락으로 둔갑한다)
    ⑤ 재시작을 모르면 가르지 않는다."""
    t = NOW - timedelta(hours=2)
    at = _fwd(t, 2)                                     # 리스너 · 끝 == 재시작
    before = _fwd(t - timedelta(minutes=10), 3)
    und = _fwd(None, 1)
    cur, old, nb, st = bh.split_at_restart([before, at, und], t)
    assert [x["n"] for x in cur] == [2, 1] and [x["n"] for x in old] == [3]
    assert nb == t - timedelta(seconds=bh.LISTEN_SLACK_S) and st == [at]
    run = {**_fwd(t + timedelta(minutes=1), 27, done=True, who="backfill_badonion"),
           "start": t - timedelta(minutes=1)}
    cur2, old2, nb2, st2 = bh.split_at_restart([before, run], t)
    assert nb2 == t - timedelta(minutes=1) and st2 == [run] and old2 == [before]
    near = {**_fwd(t - timedelta(seconds=20), 1), "start": t - timedelta(seconds=50)}
    cur3, old3, nb3, st3 = bh.split_at_restart([near, run], t)
    assert old3 == [] and nb3 == t - timedelta(minutes=1)
    assert any(x is near for x in cur3) and any(x is near for x in st3)
    # 짐작한 시작의 옛 사건도 잰 사건이 당긴 창에 끝이 걸리면 옮겨 오고, 재시작 전부터 센
    # 사건으로 밝힌다(끝이 재시작 전이다 — 짐작한 시작만 보면 빠진다, 생존 뮤테이션 N17)
    g_old = _fwd(t - timedelta(seconds=30), 2, done=True, who="backfill_beon")
    cur5, old5, nb5, st5 = bh.split_at_restart([g_old, run], t)
    assert old5 == [] and any(x is g_old for x in cur5) and any(x is g_old for x in st5)
    guess = {**_fwd(t + timedelta(minutes=5), 27, done=True, who="backfill_badonion"),
             "start": t - timedelta(minutes=25), "guessed": True}
    cur4, old4, nb4, st4 = bh.split_at_restart([before, guess], t)
    assert nb4 == t and old4 == [before] and st4 == []
    bare = _fwd(t + timedelta(minutes=5), 27, done=True)         # 시작 칸 없는 백필 = 짐작
    assert bh.split_at_restart([before, bare], t)[2] == t
    assert bh.split_at_restart([before, at], None) == ([before, at], [], None, [])


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
    assert (g["kind"], g["got"], g["dropped"]) == ("ok", 1, 2), g
    # 릴레이 원천의 글을 버린 것도 받음이다(수 대조) — 그걸 알리는 것은 버림 줄에서 따로
    # 세는 판정·매시간 알림의 일이다(`test_delivery_check_classifies_relay_drops…`)
    g2 = bh.delivery_gap(fw, ing, NOW, dropped=[{**drops[0], "relay": True}, drops[1]])
    assert (g2["kind"], g2["dropped"]) == ("ok", 2)
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
    """생존 뮤테이션 N02(판정이 partial 을 무시) — 일부 누락도 ❌ 이고, 받은 수·빠진 수를
    숫자로 적는다. 옛 판의 '마지막 포워드 뒤 수신 N건' 은 뺐다 — 백필 'done' 줄은 실행 **끝**에
    찍혀 그 실행의 수신은 전부 그 앞이라 구조적으로 0 이었고, 멀쩡한 실행에도 '뒤로 0건' 을
    적어 오해를 샀다(2차 독립 리뷰 L8)."""
    t0 = NOW - timedelta(minutes=40)
    ing = [t0 + timedelta(seconds=1), t0 + timedelta(seconds=90), t0 + timedelta(seconds=91)]
    g = bh.delivery_gap([_fwd(t0, 3), _fwd(t0 + timedelta(seconds=60), 2)], ing, NOW)
    assert (g["kind"], g["got"]) == ("partial", 3), g
    assert "after_last" not in g
    f = _good()
    f["gap"] = g
    rc, out = _v(f)
    red = [ln for ln in out.splitlines() if ln.startswith("❌ 릴레이가 5건을 포워드했는데")]
    assert rc == 1 and red, out
    assert "봇이 받은 것은 3건이다" in red[0] and "2건은 수신·버림 어느 줄에도 없다" in red[0]
    assert "마지막 포워드 뒤" not in out, out


def test_unexplained_partial_with_third_party_drops_does_not_claim_no_drops():
    """H2 — 버림이 있었는데 '버림 기록도 없는데' 라고 쓰면 거짓이다. 일부 누락은 '텔레그램이
    전부 안 줬다' 가 아니다. 짐작한 시작(창 밖에서 시작한 백필)이면 그렇다고 밝힌다."""
    f = _good()
    f["gap"] = {"kind": "partial", "sent": 5, "got": 1, "dropped": 2,
                "first": NOW - timedelta(minutes=40), "who": ["listen_beon"], "guessed": False}
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
    """봇 저널은 **두 번** 읽는다 — 판정은 사용자가 준 창, 수신은 RUN_SLACK_S 앞부터(2차 독립
    리뷰 L5: 옛 판은 넓힌 창 하나로 판정까지 해 사용자가 창으로 뺀 사건이 ❌ 가 됐다).
    넓혀 읽기가 실패하면 같은 창으로 세고 **그렇다고** 밝힌다."""
    asked = []

    def read(units, since):
        asked.append((units, since))
        return [], "", "rotated"

    def run(read_fn):
        return bh.collect("86400 seconds ago", now=NOW,
                          env={"token": "", "dest": _DEST, "src": {}, "err": "", "inbox": ""},
                          facts_fn=lambda: {"ok": True, "s_MainPID": "0"}, read=read_fn,
                          start_fn=lambda pid: (None, ""), tg_fn=lambda *a: {"token": False},
                          procs_fn=lambda own: [], deploys_fn=lambda: ([], ""))
    f = run(read)
    bot_reads = [s for u, s in asked if u == (bh._SERVICE,)]
    assert bot_reads == ["86400 seconds ago", f"{86400 + bh.RUN_SLACK_S} seconds ago"], asked
    assert [s for u, s in asked if u == bh.RELAY_UNITS] == ["86400 seconds ago"]
    head = bh.render(f, "86400 seconds ago")[2]
    assert "판정은 이 창 · 봇 수신은 30분 앞부터 센다" in head, head

    def read_wide_fails(units, since):
        if units == (bh._SERVICE,) and since != "86400 seconds ago":
            return [], "권한 없음", "denied"
        return [], "", "rotated"
    f2 = run(read_wide_fails)
    assert f2["journal"] is not None and not f2["bot_since_widened"]
    head2 = bh.render(f2, "86400 seconds ago")[2]
    assert "넓혀 읽지 못해 수신도 이 창으로 센다 — 권한 없음" in head2, head2


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
    assert (g["kind"], g["dropped"], len(g["relay_drops"])) == ("ok", 3, 0)
    assert not bh.gap_needs_alert(g)
    by_name = [_drop("2026-09-25T07:49:10", i, -100999, "Badonions") for i in (1, 2, 3)]
    assert len(check(by_name, [fwd])["relay_drops"]) == 3
    renamed = [_drop("2026-09-25T07:49:10", i, _BAD, "NewName") for i in (1, 2, 3)]
    assert check(renamed, [fwd])["relay_drops"] == []          # ID 를 모르면 못 알아본다
    g4 = check(renamed, [src, fwd])
    assert len(g4["relay_drops"]) == 3 and bh.gap_needs_alert(g4)
    assert [d["msg"] for d in g4["new_drops"]] == [1, 2, 3]
    # 이미 알린 버림은 다시 알리지 않는다(사실마다 신원, 2차 독립 리뷰 L4)
    g5 = bh.delivery_check(7200, now=NOW, seen=set(g4["ids"]), read=lambda u, s: (
        ([src, fwd] if u == bh.RELAY_UNITS else renamed), "", ""))
    assert g5["new_drops"] == [] and not bh.gap_needs_alert(g5)


def _exc(ts, msg, *, user="Badonions", chat=_BAD, otype="channel", pid=4242, omsg=4242) -> str:
    """봇의 에러 핸들러가 찍는 채널 글 예외 줄 — 형식은 **소스에서** 꺼낸다(#155). `omsg` =
    원래 채널의 글번호(실수 #411 판부터 찍힌다 — 보증 대조의 열쇠)."""
    fmt = _log_format(_REPO / "trade" / "bot.py", "handler error update=channel_post", "error")
    return _jl(ts, fmt % (msg, otype, chat, user, omsg, "OSError",
                          "[Errno 28] No space left on device"),
               pid=pid).replace("[INFO]", "[ERROR]")


def test_handler_exceptions_in_the_gap_window_are_the_cause():
    """(옛 이름 그대로 — 계약은 2차 독립 리뷰 H1 로 넓어졌다.) 받은 채널 글을 처리하다 예외로
    끝나 수신 줄이 없는 번호는 손실의 **직접 증거**다 — 수 대조(gap)와 **무관하게** 판정한다.
    옛 판은 누락이 보일 때만 ❌ 로 읽어, 같은 창의 다른 글 수신·버림이 그 손실을 덮으면 ✅ 에
    '빠진 것이 없다' 메모를 달았다(리뷰 재현 s2·s5). 지금: 릴레이 원천 ❌ · 출처를 모름(출처를
    적지 않는 판) ❓ · 직접 쓴 글·다른 출처 ⚠️ · 번호가 기록됐으면(기록 뒤 단계) ⚠️. 어느
    업데이트였는지 안 적힌 옛 판 예외(PTB 기본 문구)는 원인을 못 짚은 갈래가 '표본부터' 로
    말한다(텔레그램 탓으로 단정하지 않는다)."""
    t0 = NOW - timedelta(minutes=40)
    exc = _exc("2026-09-25T07:50:05", 77)
    f = _good()
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), exc])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW)
    rc, out = _v(f)
    assert rc == 1 and "❌ 봇이 릴레이 원천의 채널 글 1건을 처리하다 예외로 놓쳤다(번호 77)" in out, out
    assert "텔레그램이 전달하지 않았다" not in out
    # 수 대조가 ok 여도 ❌ 다 — 남의 글 버림이 받음으로 세어져 손실을 덮은 경우(재현 s5)
    other = _drop("2026-09-25T07:50:06", 78, -100555, "SomeNews")
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), exc, other])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW,
                               dropped=bh.forward_drops(f["journal"]["drops_origin"], ["Badonions"]))
    assert f["gap"]["kind"] == "ok", f["gap"]
    rc1, out1 = _v(f)
    assert rc1 == 1 and "예외로 놓쳤다(번호 77)" in out1 and "✅" not in out1, out1
    # 출처를 적지 않는 판이 찍은 줄 — 릴레이 글인지 모른다: ❓(✅ 도 ❌ 도 아니다, #165)
    old_fmt = _jl("2026-09-25T07:50:05", "handler error update=channel_post msg=77 exc=OSError: "
                  "x").replace("[INFO]", "[ERROR]")
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), old_fmt])
    f["gap"] = {"kind": "none"}
    rc2, out2 = _v(f)
    assert rc2 == 2 and "❓ 봇이 채널 글 1건을 처리하다 예외로 놓쳤는데(번호 77" in out2, out2
    # 릴레이 원천이 아닌 글(채널에 직접 쓴 명령 등) — 릴레이 데이터가 아니다: ⚠️
    cmd = _exc("2026-09-25T07:50:05", 77, otype="none", chat=None, user=None)
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), cmd])
    rc3, out3 = _v(f)
    assert rc3 == 0 and "⚠️ 릴레이 원천이 아닌 채널 글 1건" in out3, out3
    # 같은 번호가 기록됐으면(기록 뒤 단계의 예외) 손실이 아니다 — 누락(2건 중 1건)이 있어도
    # 그 예외를 원인으로 읽으면 안 된다. 번호 대조를 지우면 여기서 거짓 ❌ 가 난다.
    ok = _jl("2026-09-25T07:50:04", "ingested msg=77 mg=- caption=1 photo=-")
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), ok, exc])
    f["gap"] = bh.delivery_gap([_fwd(t0, 2)], f["journal"]["ingested"], NOW)
    assert f["gap"]["kind"] == "partial"
    _rc4, out4 = _v(f)
    assert "예외로 놓쳤" not in out4 and "그 번호는 전부 수신 줄이 있다" in out4, out4
    # 표식 없는 옛 판 예외 — 원인을 못 짚은 갈래가 그걸 말한다
    ptb = _jl("2026-09-25T07:50:05", "No error handlers are registered, logging exception.",
              logger="telegram.ext.Application").replace("[INFO]", "[ERROR]")
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), ptb])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW)
    _rc5, out5 = _v(f)
    assert "PTB 예외가 1번 찍혔다" in out5 and "텔레그램이 전달하지 않았다" not in out5, out5
    f["running"] = bh.parse_start_line(_start(drop_log=False))
    _rc6, out6 = _v(f)
    assert "옛 판이라" in out6 and "PTB 예외가 1번 찍혔다" in out6, out6


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
    # 가림 = 명령에 붙은 '토큰 모양을 지우는 sed'. 낱말 'sed' 가 아무 데나 있으면 통과시키던
    # 옛 판은 'used'·'based' 같은 낱말에 속았다(2차 리뷰 생존 뮤테이션 KF02)
    mask = _re.compile(r"\|\s*sed\s+-E\s+'s/\[0-9\]\{8,10\}:")
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
            if pat.search(node.value) or (jpat.search(node.value)
                                          and not mask.search(node.value)):
                hits.append(f"{p.relative_to(_REPO)}:{node.lineno}")
    assert scanned > 50, scanned                          # 대조 0건은 통과가 아니다(#54)
    assert not hits, hits
    # 반대 증거(#25): 패턴은 실제 옛 문구를 잡는다
    assert pat.search("리스너가 죽었다. `systemctl status trade-bot` 부터")
    assert not pat.search("systemctl status trade-bot-beon-listener")
    assert jpat.search("판정 불가(`sudo journalctl -u <unit> -n 50` 로 확인)")
    assert not jpat.search("journalctl -u trade-bot-jpx-codes")
    assert not mask.search("(`sudo journalctl -u <unit> -n 50` 로 확인 — often used)")
    assert mask.search("`sudo journalctl -u <unit> -n 50 | sed -E "
                       "'s/[0-9]{8,10}:[A-Za-z0-9_-]{30,}/<TOKEN>/g'`")


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
                   tg_fn=lambda *a: {"token": False}, procs_fn=lambda own: [], deploys_fn=lambda: ([], ""))
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


def test_old_build_is_undecidable_even_without_a_gap(monkeypatch, capsys):
    """실수 #409 — 옛 판은 게이트 버림을 적지 않아 이 진단이 '받고 버렸나 / 아예 안 왔나' 를
    못 가른다. 증상이 없을 때 그걸 ⚠️ 메모(rc 0)로 두면 배포 직후 `bot_health && 다시 포워드`
    가 봇이 새 판으로 재시작되기 **전**에 다시 포워드를 흘려, 그 글이 증거 없이 또 사라진다
    — 판정 불가(❓, rc 2)여야 셸의 `&&` 가 멈춘다. 같은 사실의 새 판은 ✅ · rc 0 이다(반대
    증거, #25). 셸이 보는 것은 `main` 의 종료코드라 거기까지 태운다(#20)."""
    f = _good()
    f["running"] = bh.parse_start_line(_start(drop_log=False))
    rc, out = _v(f)
    assert rc == 2, out
    assert "❓ 실행 중인 봇은 게이트 버림을 저널에 적지 않는 옛 판이다" in out, out
    assert "✅" not in out and "⚠️" not in out and out.count("옛 판") == 1, out
    f["running"] = bh.parse_start_line(_start(drop_log=True))
    rc_new, out_new = _v(f)
    assert rc_new == 0 and out_new.startswith("✅") and "옛 판" not in out_new, out_new
    base = _good()
    base.update(facts={}, journal_err="", forwards=[],
                env={"dest": _DEST, "src": {}, "err": "", "inbox": "/home/h/.trade/inbox.jsonl"})
    for drop_log, want in ((False, 2), (True, 0)):
        g = {**base, "running": bh.parse_start_line(_start(drop_log=drop_log))}
        monkeypatch.setattr(bh, "collect", lambda since, g=g: g)
        assert bh.main([]) == want, drop_log
    capsys.readouterr()


def test_old_build_with_a_gap_and_another_unknown_keeps_both(monkeypatch):
    """#409 의 짝 — 옛 판 ❓ 는 증상 갈래가 더 구체적인 한 줄로 **대신**하지만(#395), 그건
    다른 못 잰 조건이 없을 때만이다. 다른 ❓ 가 있으면 '원인을 짚지 못했다 — 그것부터' 가
    먼저고 옛 판 ❓ 도 그대로 남는다(둘 다 사실이다). 옛 판 ❓ 를 '다른 못 잰 조건' 으로
    세면 옛 판 하나만으로 구체적인 문장이 사라진다(위 `test_unexplained_gap_says_which_branch_is_left`)."""
    f = _good()
    f["gap"] = {"kind": "total", "sent": 27, "got": 0, "first": NOW - timedelta(minutes=40),
                "who": ["backfill_badonion"]}
    f["running"] = bh.parse_start_line(_start(drop_log=False))
    f["tg"]["member"] = {"ok": False, "err": "timeout", "code": None}
    rc, out = _v(f)
    assert rc == 1, out                          # 증상은 ❌ 다
    assert "❓ 관리자 여부를 못 물었다(timeout)" in out, out
    assert "❓ 원인을 짚지 못했다 — 위 ❓ 의 못 잰 조건이 남아 있으니 그것부터 잴 것" in out, out
    assert "❓ 실행 중인 봇은 게이트 버림을 저널에 적지 않는 옛 판이다" in out, out
    assert "옛 판이라 '받고 버렸나 / 아예 안 왔나'" not in out, out


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
    조용했다. 2차 리뷰: 그 다음 판의 키(창 안 첫 포워드 + 릴레이 이름)를 '릴레이 이름만' 으로
    줄여도 이 테스트가 두 **다른** 릴레이만 써서 통과했다(생존 뮤테이션 B21). 이제 사실마다
    신원이고 **같은 릴레이**의 두 누락으로 잰다. 같은 누락은 여전히 한 번만."""
    relay = [_jl("2026-09-25T07:49:09", "forwarded 2 msg(s): [1, 2]", logger="listen_badonion",
                 pid=55)]
    bot = [_poll("2026-09-25T08:29:50")]
    _drive(monkeypatch, bot=bot, relay=relay)
    hc.check_delivery_gap()                                # 첫 누락 → 알림
    hc.check_delivery_gap()                                # 같은 누락 → 조용
    relay.append(_jl("2026-09-25T08:10:00", "forwarded 3 msg(s): [3, 4, 5]",
                     logger="listen_badonion", pid=55))
    bot.append(_jl("2026-09-25T08:10:01", "ingested msg=90 mg=- caption=1 photo=-"))
    hc.check_delivery_gap()                                # 같은 릴레이의 새 누락 → 알림
    assert len(hc._sent) == 2, hc._sent
    assert "2건을 비공개 채널로" in hc._sent[0] and "한 건도 못 받았습니다" in hc._sent[0]
    assert "3건을 비공개 채널로" in hc._sent[1] and "1건만 받았습니다" in hc._sent[1], hc._sent[1]
    hc.check_delivery_gap()
    assert len(hc._sent) == 2, hc._sent


def test_health_check_alerts_relay_gate_drops_but_not_third_party(hc, monkeypatch):
    """H2 — 남의 글 버림(BeOn 이 되포워드한 AWAKE)은 알리지 않고, 릴레이 원천 글의 버림은
    알린다(한 번). 받음(버림 포함)이 다 차면 '수신 누락' 이 아니다."""
    fwd = _jl("2026-09-25T07:49:09", "forwarded 3 msg(s): [1, 2, 3]", logger="listen_beon", pid=55)
    third = [_drop("2026-09-25T07:49:10", i, -100555, "awake_plus") for i in (1, 2, 3)]
    bot = [_poll("2026-09-25T08:29:50"), *third]
    _drive(monkeypatch, bot=bot, relay=[fwd])
    hc.check_delivery_gap()
    assert hc._sent == []
    bot[1:] = [_drop("2026-09-25T07:49:10", i, -100999, "BeOn_BeClear") for i in (1, 2)] + [third[2]]
    hc.check_delivery_gap()
    assert len(hc._sent) == 1 and "출처 게이트 버림" in hc._sent[0], hc._sent
    assert "포워드 <b>2건을 출처 게이트에서 버렸습니다</b>" in hc._sent[0], hc._sent[0]
    assert "수신 누락" not in hc._sent[0]
    hc.check_delivery_gap()
    assert len(hc._sent) == 1                              # 같은 버림은 한 번만


def test_health_check_alert_record_prunes_and_survives_garbage(hc, monkeypatch, caplog):
    """알린 사실의 기록(`delivery-alerted.json`) — 2일 지난 신원은 읽을 때 빼고 다음 쓰기에서
    사라진다(파일 유계 — 옛 표식 정리의 자리. 2차 리뷰 생존 뮤테이션 H01: 정리 호출을 지워도
    통과했다). 못 읽으면 빈 기록 + 경고(알림을 막지 않되 다시 알릴 수 있다고 밝힌다). 쓰기는
    원자적 — 임시 파일이 남지 않는다."""
    import time as _t
    now = _t.time()
    p = hc.MARKER_DIR / hc.DELIVERY_ALERTED
    p.write_text(json.dumps({"fwd:old": now - 3 * 86400, "fwd:new": now - 3600, "bad": "x"}),
                 encoding="utf-8")
    assert hc._load_alerted(now) == {"fwd:new": now - 3600}
    fwd = _jl("2026-09-25T07:49:09", "forwarded 1 msg(s): [1]", logger="listen_badonion", pid=55)
    _drive(monkeypatch, bot=[_poll("2026-09-25T08:29:50")], relay=[fwd])
    hc.check_delivery_gap()
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert set(saved) - {"fwd:new"} and "fwd:old" not in saved and "bad" not in saved, saved
    assert len(saved) == 2 and "fwd:new" in saved, saved
    assert not list(hc.MARKER_DIR.glob("*.tmp"))
    with caplog.at_level(logging.WARNING, logger="health-check"):
        p.write_text("{not json", encoding="utf-8")
        assert hc._load_alerted() == {}
        p.write_text("[1, 2]", encoding="utf-8")
        assert hc._load_alerted() == {}
    msgs = [r.getMessage() for r in caplog.records]
    assert any("알림 기록을 못 읽었다" in m for m in msgs), msgs
    assert any("알림 기록 형식이 아니다" in m for m in msgs), msgs


# ── 2차 독립 리뷰(6b005d9..e5b528a) 반영 ─────────────────────────────────
# H1 예외로 놓친 글 = 직접 증거 · M2 재시작을 걸친 실행 · L4 사실마다 알림 · L5 판정 창 ·
# L6 재시작 전 예외 메모 · 생존 뮤테이션(B07·B12·B13) — 리뷰 재현 스크립트 s1~s5 를 이식했다.

def test_exception_line_from_the_bot_source_parses_with_its_origin():
    """생산자 = 소비자(#155) — 에러 핸들러의 채널 글 예외 줄 형식을 **소스에서** 꺼내 채워
    진단 파서로 읽는다. 출처 칸이 버림 줄과 같은 규약으로 읽혀야 릴레이 글인지 가른다(2차
    독립 리뷰 H1). 채널 글이 아닌 업데이트의 줄엔 출처 칸이 없다(모름 = None)."""
    bot_py = _REPO / "trade" / "bot.py"
    fmt = _log_format(bot_py, "handler error update=channel_post", "error")
    e = bh.journal_facts([_jl("2026-09-25T08:00:00", fmt % (77, "channel", _BAD, "Badonions",
                                                           4242, "OSError", "disk"))]
                         )["exceptions"][0]
    assert (e["kind"], e["update"], e["msg"], e["type"], e["chat"], e["user"], e["omsg"]) == (
        "handler", "channel_post", 77, "channel", _BAD, "Badonions", 4242), e
    d = bh.journal_facts([_jl("2026-09-25T08:00:00", fmt % (78, "none", None, None, None,
                                                           "OSError", "disk"))])["exceptions"][0]
    assert (d["type"], d["chat"], d["user"], d["omsg"]) == ("none", None, "", None), d
    other = _log_format(bot_py, "handler error update=%s", "error")
    o = bh.journal_facts([_jl("2026-09-25T08:00:00", other % ("Update", 5, "ValueError", "x"))]
                         )["exceptions"][0]
    assert (o["update"], o["msg"], o["type"]) == ("Update", 5, None), o
    lost = bh.lost_posts([e, d, o], set(), ["Badonions"])
    assert [(x["msg"], x["relay"]) for x in lost] == [(77, True), (78, False)], lost


def test_dm_exception_is_not_a_lost_channel_post():
    """2차 리뷰 생존 뮤테이션 B12 — 채널 글이 아닌 업데이트(봇 DM 의 /watch 등)의 예외는 채널
    글 번호와 대조할 수 없다. 그걸 '예외로 놓친 채널 글' 로 읽으면 없는 손실을 만든다."""
    fmt = _log_format(_REPO / "trade" / "bot.py", "handler error update=%s", "error")
    dm = _jl("2026-09-25T07:50:05", fmt % ("Update", 5, "ValueError", "x")).replace("[INFO]",
                                                                                "[ERROR]")
    f = _good()
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), dm])
    rc, out = _v(f)
    assert rc == 0 and "예외로 놓쳤" not in out and "예외가 1번" not in out, out
    assert bh.lost_posts(f["journal"]["exceptions"], set()) == []


def test_unlabeled_exception_outside_the_gap_window_is_not_blamed():
    """2차 리뷰 생존 뮤테이션 B13 — 어느 업데이트였는지 안 적힌 옛 판 예외(PTB 기본 문구)는
    대조 창(가장 이른 포워드의 시작) **안**의 것만 이 누락의 후보로 말한다. 창 밖 예외를
    끌어오면 무관한 사건을 원인처럼 가리키고 '텔레그램이 안 줬다' 갈래를 지운다."""
    t0 = NOW - timedelta(minutes=40)
    ptb = _jl("2026-09-25T06:00:00", "No error handlers are registered, logging exception.",
              logger="telegram.ext.Application").replace("[INFO]", "[ERROR]")
    f = _good()
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), ptb])
    f["gap"] = bh.delivery_gap([_fwd(t0, 1)], f["journal"]["ingested"], NOW)
    _rc, out = _v(f)
    assert "PTB 예외" not in out and "텔레그램이 전달하지 않았다" in out, out


def test_delivery_gap_counts_from_the_events_own_start():
    """2차 리뷰 생존 뮤테이션 B07 — 사건이 스스로 아는 시작(같은 PID 접속 줄)을 안 쓰고 종류별
    짐작(백필 끝 − 30분)으로 세면, 30분보다 긴 실행의 앞쪽 수신을 못 세어 멀쩡한 실행을
    누락으로 오보한다(BeOn 대량 회수는 더 길다)."""
    done = NOW - timedelta(minutes=10)
    run = {**_fwd(done, 3, done=True, who="backfill_beon"), "start": done - timedelta(minutes=45)}
    assert bh.event_start(run) == done - timedelta(minutes=45)
    assert bh.delivery_gap([run], [done - timedelta(minutes=44)] * 3, NOW)["kind"] == "ok"


def test_backfill_straddling_a_restart_counts_both_processes():
    """2차 독립 리뷰 M2(재현 s1) — 백필이 07:40:00 에 접속해 07:41:30 에 끝났고 그 사이 배포가
    봇을 재시작했다(07:40:50). 옛 프로세스가 10건, 새 프로세스가 17건을 받아 27건이 전부 inbox
    에 있는데, 옛 판은 재시작 **뒤** 수신만 세어 '10건은 어느 줄에도 없다' + '텔레그램이 일부를
    전달하지 않았다' 를 찍었다(같은 데이터로 매시간 알림은 ok — 두 도구가 갈렸다)."""
    src, _ = _relay_line("backfill_badonion", "source(marked)=", (_BAD, _DEST),
                         ts="2026-09-25T07:40:00", pid=99)
    done, _ = _relay_line("backfill_badonion", "done: forwarded", (27, 27, 0),
                          ts="2026-09-25T07:41:30", pid=99)
    bot = [_start(ts="2026-09-25T06:00:00", pid=1111), _poll("2026-09-25T07:39:50", pid=1111)]
    bot += [_jl(f"2026-09-25T07:40:{5 + i:02d}", f"ingested msg={i} mg=- caption=5 photo=-",
                pid=1111) for i in range(10)]
    bot.append(_start(ts="2026-09-25T07:40:50", pid=4242))
    bot += [_jl(f"2026-09-25T07:41:{i - 5:02d}", f"ingested msg={i} mg=- caption=5 photo=-")
            for i in range(10, 27)]
    bot.append(_poll("2026-09-25T08:29:50"))
    f, _ = _collect(bot, [src, done])
    g = f["gap"]
    assert (g["kind"], g["sent"], g["got"], g["straddle"]) == ("ok", 27, 27, 1), g
    assert f["gap_before"]["kind"] == "none"
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 0, out
    r5 = [ln for ln in bh.render(f, "x") if ln.startswith("⑤")][0]
    assert "재시작 전부터 센 포워드 1건 포함" in r5, r5
    hourly = bh.delivery_check(7200, now=NOW, read=lambda u, s: (
        ([src, done] if u == bh.RELAY_UNITS else bot), "", ""))
    assert hourly["kind"] == "ok"                           # 두 도구가 같은 말을 한다
    # 대조군: 옛 프로세스 몫이 정말 빠졌으면 걸친 실행의 시작부터 세도 모자라다 — ❌ 이고,
    # 그렇게 셌다고 밝힌다
    lost_old = [ln for ln in bot if not ("python[1111]" in ln and "ingested" in ln)]
    f2, _ = _collect(lost_old, [src, done])
    assert (f2["gap"]["kind"], f2["gap"]["got"]) == ("partial", 17), f2["gap"]
    f2["tg"] = _good()["tg"]
    rc2, out2 = _v(f2)
    assert rc2 == 1 and "재시작 전부터 센 포워드 1건 포함" in out2, out2


def test_a_masked_loss_is_red_and_alerted_once():
    """2차 독립 리뷰 H1(재현 s2·s5) — 릴레이 글 77 이 예외로 사라졌는데 같은 창에 남의 글 버림이
    있어 수 대조는 ok 였고, 옛 판은 ✅ + '빠진 것이 없다' 메모 · 매시간 알림 없음이었다. 그 줄
    자체가 증거다 — ❌ 이고 알린다(한 번). 직접 쓴 글의 예외는 알리지 않는다."""
    relay = [_jl("2026-09-25T08:20:03", "forwarded 2 msg(s): [5, 6]", logger="listen_badonion",
                 pid=55)]
    bot = [_start(ts="2026-09-25T06:00:00"),
           _exc("2026-09-25T08:20:01", 77),
           _jl("2026-09-25T08:20:02", "ingested msg=78 mg=- caption=5 photo=-"),
           _drop("2026-09-25T08:21:00", 79, -100555, "SomeNews"),
           _poll("2026-09-25T08:29:50")]
    f, _ = _collect(bot, relay)
    assert f["gap"]["kind"] == "ok", f["gap"]              # 수는 덮였다
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 1 and "❌ 봇이 릴레이 원천의 채널 글 1건을 처리하다 예외로 놓쳤다(번호 77)" in out, out
    assert "✅" not in out and "빠진 것이 없다" not in out

    def check(lines, seen=()):
        return bh.delivery_check(7200, now=NOW, seen=seen, read=lambda u, s: (
            (relay if u == bh.RELAY_UNITS else lines), "", ""))
    g = check(bot)
    assert g["kind"] == "ok" and [e["msg"] for e in g["new_lost"]] == [77], g
    assert bh.gap_needs_alert(g)
    text = bh.gap_alert_text(g)
    assert "처리 중 예외로 놓친 글" in text and "수신 누락" not in text, text
    assert "<b>1건을 처리하다 예외로 놓쳤습니다</b>(번호 77" in text, text
    assert not bh.gap_needs_alert(check(bot, set(g["ids"])))
    cmd = [_exc("2026-09-25T08:20:01", 77, otype="none", chat=None, user=None)
           if "msg=77" in ln else ln for ln in bot]
    g3 = check(cmd)
    assert g3["lost"] == [] and not bh.gap_needs_alert(g3), g3
    # 출처를 적지 않는 판이 찍은 줄은 알린다 — 릴레이 글이 아니라고 단정할 수 없다(#165)
    unk = [_jl("2026-09-25T08:20:01", "handler error update=channel_post msg=77 exc=OSError: "
               "x").replace("[INFO]", "[ERROR]") if "msg=77" in ln else ln for ln in bot]
    g4 = check(unk)
    assert [e["relay"] for e in g4["new_lost"]] == [None] and bh.gap_needs_alert(g4)
    assert "그중 1건은 출처를 모릅니다" in bh.gap_alert_text(g4)


def test_a_sustained_outage_alerts_each_forward_exactly_once(hc, monkeypatch):
    """2차 독립 리뷰 L4(재현 s3) — 봇이 아무것도 못 받는 동안 리스너가 10분마다 포워드했다.
    옛 판의 표식(창 안 첫 포워드)은 매시간 바뀌어 한 포워드가 두 번씩 알림에 실렸다('같은
    누락은 한 번' 과 모순, 12시간 12통). 사실마다 신원이면 각 포워드는 **정확히 한 알림**에만
    실리고, 새로 빠진 포워드는 매번 알린다(다른 누락은 막지 않는다)."""
    t0 = datetime(2026, 9, 25, 0, 0, tzinfo=_KST)
    fmt = "%Y-%m-%dT%H:%M:%S"
    relay = [_jl((t0 + timedelta(minutes=10 * i)).strftime(fmt), "forwarded 1 msg(s): [1]",
                 logger="listen_badonion", pid=55) for i in range(6 * 8)]
    bot = [_poll((t0 + timedelta(minutes=i)).strftime(fmt)) for i in range(60 * 8)]
    clock = [t0]
    _drive(monkeypatch, bot=bot, relay=relay, clock=clock)
    per_run = []
    for h in range(2, 8):
        clock[0] = t0 + timedelta(hours=h, minutes=5)
        before = set(hc._load_alerted())
        hc.check_delivery_gap()
        per_run.append(set(hc._load_alerted()) - before)
    assert len(hc._sent) == 6, hc._sent
    ids = [i for run in per_run for i in run]
    assert len(ids) == len(set(ids)) == 12 + 5 * 6, (len(ids), len(set(ids)))
    assert all(i.startswith("fwd:listen_badonion:55:") for i in ids)


def test_judgment_uses_the_since_window_and_receipts_use_the_wide_one():
    """2차 독립 리뷰 L5(재현 s4) — 사용자가 --since 08:00 으로 07:45 의 알려진 사건(409)을
    빼고 봤는데, 옛 판은 봇 저널을 07:30 부터 읽은 그 넓은 창으로 **판정**까지 해 같은
    프로세스의 07:45 409 가 '지금' 의 ❌ 가 됐다. 판정은 창 안의 줄로, 넓힌 저널은 수신
    대조에만 — 예외 직전의 기록 줄이 창 앞에 있으면 그 번호는 기록된 것이다."""
    bot_all = [_start(ts="2026-09-25T06:00:00"),
               _poll("2026-09-25T07:45:00", "409 Conflict"),
               _poll("2026-09-25T07:45:10", "409 Conflict"),
               _jl("2026-09-25T07:59:59", "ingested msg=77 mg=- caption=1 photo=-"),
               _exc("2026-09-25T08:00:01", 77),
               _poll("2026-09-25T08:29:50")]
    asked = []

    def read(units, since):
        asked.append((units, since))
        if units != (bh._SERVICE,):
            return [], "없다", "rotated"
        form = "%Y-%m-%d %H:%M:%S" if since.count(":") == 2 else "%Y-%m-%d %H:%M"
        lo = datetime.strptime(since, form).replace(tzinfo=_KST)
        return [ln for ln in bot_all if bh._ts(ln) >= lo], "", ""
    f = bh.collect("2026-09-25 08:00", now=NOW,
                   env={"token": "", "dest": _DEST, "src": {}, "err": "", "inbox": ""},
                   facts_fn=lambda: {"ok": True, "s_LoadState": "loaded", "s_ActiveState": "active",
                                     "s_SubState": "running", "s_MainPID": "4242",
                                     "s_NRestarts": "0"},
                   read=read, start_fn=lambda pid: (bh.parse_start_line(bot_all[0]), ""),
                   tg_fn=lambda *a: {"token": False}, procs_fn=lambda own: [], deploys_fn=lambda: ([], ""))
    assert [s for u, s in asked if u == (bh._SERVICE,)] == ["2026-09-25 08:00",
                                                          "2026-09-25 07:30:00"], asked
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert "409" not in out, out
    assert "예외로 놓쳤" not in out and "그 번호는 전부 수신 줄이 있다" in out, out
    assert rc == 0, out


def test_losses_to_exceptions_before_the_restart_are_a_note():
    """2차 독립 리뷰 L6 — 독스트링은 '재시작 전 프로세스의 예외로 놓친 릴레이 글은 사실 메모로
    말한다' 고 했는데 그 메모가 없었다(예외는 지금 프로세스 줄만 봤다). 옛 프로세스가 놓친 글은
    그때 inbox 에 안 들어갔다 — 지금 판정(❌)은 아니지만 말한다. 대조군: 지금 프로세스면 ❌."""
    old = [_start(ts="2026-09-25T06:00:00", pid=1111), _exc("2026-09-25T06:30:00", 55, pid=1111)]
    new = [_start(ts="2026-09-25T08:00:00", pid=4242), _poll("2026-09-25T08:29:50")]
    f, _ = _collect(old + new)
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 0, out
    assert "⚠️ 재시작 전 프로세스가 채널 글 1건을 처리하다 예외로 놓쳤다(번호 55)" in out, out
    f2, _ = _collect(new + [_exc("2026-09-25T08:10:00", 55)])
    f2["tg"] = _good()["tg"]
    rc2, out2 = _v(f2)
    assert rc2 == 1 and "재시작 전" not in out2 and "예외로 놓쳤다(번호 55)" in out2, out2


def test_hourly_marks_forwards_only_when_the_alert_is_about_them():
    """2차 독립 리뷰 L4 의 설계점 — 알림이 **버림** 때문에 나갔는데 그 창의 포워드 사건까지
    '알림' 으로 적으면, 아직 기다리는 새 포워드의 수신이 잠시 덮은 누락이 영영 안 알려진다.
    ① 08:30 — E(2건: 77 은 텔레그램이 안 줌 · 78 수신)·F(1건: 릴레이 글, 출처 게이트가 버림)는
    대조 대상, G(1건: 79 수신)는 아직 기다리는 중. 받음 3 ≥ 보냄 3(G 의 79 가 덮었다) → 누락
    없음, 버림만 알린다. ② 08:40 — G 도 대조 대상이 되면 보냄 4 · 받음 3 → 누락을 알린다.
    포워드 사건을 ①에서 '알렸다' 로 적으면 ②는 G 하나만 대조해 조용하다."""
    relay = [_jl("2026-09-25T08:20:03", "forwarded 2 msg(s): [5, 6]", logger="listen_badonion",
                 pid=55),
             _jl("2026-09-25T08:24:59", "forwarded 1 msg(s): [7]", logger="listen_badonion",
                 pid=55),
             _jl("2026-09-25T08:29:00", "forwarded 1 msg(s): [8]", logger="listen_badonion",
                 pid=55)]
    bot = [_jl("2026-09-25T08:20:02", "ingested msg=78 mg=- caption=5 photo=-"),
           _drop("2026-09-25T08:25:00", 80, _BAD, "Badonions"),
           _jl("2026-09-25T08:29:01", "ingested msg=79 mg=- caption=5 photo=-"),
           _poll("2026-09-25T08:39:50")]

    def check(now, seen):
        return bh.delivery_check(7200, now=now, seen=seen, read=lambda u, s: (
            (relay if u == bh.RELAY_UNITS else bot), "", ""))
    g1 = check(NOW, set())
    assert g1["fresh"]["kind"] == "ok" and [d["msg"] for d in g1["new_drops"]] == [80], g1
    assert g1["ids"] and all(i.startswith("drop:") for i in g1["ids"]), g1["ids"]
    g2 = check(NOW + timedelta(minutes=10), set(g1["ids"]))
    assert (g2["fresh"]["kind"], g2["fresh"]["sent"]) == ("partial", 4), g2["fresh"]
    assert bh.gap_needs_alert(g2) and g2["new_drops"] == []
    assert "3건만 받았습니다" in bh.gap_alert_text(g2)


def test_hourly_alerts_only_facts_inside_its_window():
    """매시간 알림은 봇 저널을 수신을 세려고 30분 **앞**부터 읽는다 — 그 앞 30분의 예외·버림은
    직전 실행의 창이다(거기서 이미 알렸거나 알릴 일이 아니었다). 창 안의 것만 알린다."""
    early = [_exc("2026-09-25T06:10:00", 70), _drop("2026-09-25T06:11:00", 71, _BAD, "Badonions")]
    late = [_exc("2026-09-25T07:10:00", 72), _drop("2026-09-25T07:11:00", 73, _BAD, "Badonions")]
    g = bh.delivery_check(7200, now=NOW, read=lambda u, s: (
        ([] if u == bh.RELAY_UNITS else early + late), "", ""))
    assert [e["msg"] for e in g["lost"]] == [72] and [d["msg"] for d in g["relay_drops"]] == [73]
    assert bh.gap_needs_alert(g)


def test_health_check_undated_forwards_do_not_swallow_other_alerts(hc, monkeypatch, caplog):
    """시각을 못 읽은 포워드가 있어 대조가 판정 불가(kind=unknown)여도 **저널은 읽혔다** —
    예외로 놓친 글·릴레이 버림 알림은 그대로 나간다. 일찍 돌아가는 것은 저널을 못 읽었을
    때(err)뿐이다. 시각을 못 읽은 수는 경고로 말한다(#54)."""
    g = {"kind": "unknown", "sent": 0, "got": 0, "undated": 2, "err": "",
         "fresh": {"kind": "none"}, "new_drops": [], "ids": ["lost:77:1"],
         "new_lost": [{"msg": 77, "ts": NOW, "relay": True}]}
    monkeypatch.setattr(bh, "delivery_check", lambda window, **kw: dict(g))
    with caplog.at_level(logging.WARNING, logger="health-check"):
        hc.check_delivery_gap()
    assert len(hc._sent) == 1 and "예외로 놓쳤습니다" in hc._sent[0], hc._sent
    assert any("2건의 시각을 못 읽어" in r.getMessage() for r in caplog.records)
    assert json.loads((hc.MARKER_DIR / hc.DELIVERY_ALERTED).read_text(encoding="utf-8")).keys() == {
        "lost:77:1"}


def test_collect_counts_receipts_before_since_for_a_run_that_ended_inside_it():
    """독립 리뷰 M1 의 진단판 — 2차 리뷰 L5 로 봇 저널을 두 번 읽게 되자, 수신 대조가 좁은
    판정 창을 써도 멀쩡한 테스트만 있었다(생존 뮤테이션 N20: 두 번의 읽기가 같은 줄을 돌려주는
    가짜라 눈이 멀었다). --since 08:00 직후에 끝난 백필(접속 줄은 창 밖)의 수신 3건은 07:59 에
    있다 — 넓힌 저널로 세야 ok 다."""
    relay_all = [_jl("2026-09-25T08:00:30", "done: forwarded 3 of 3 candidate messages "
                     "(skipped_units=0)", logger="backfill_badonion", pid=99)]
    bot_all = [_start(ts="2026-09-25T06:00:00"),
               *[_jl(f"2026-09-25T07:59:3{i}", f"ingested msg={i} mg=- caption=5 photo=-")
                 for i in range(3)],
               _poll("2026-09-25T08:29:50")]

    def read(units, since):
        form = "%Y-%m-%d %H:%M:%S" if since.count(":") == 2 else "%Y-%m-%d %H:%M"
        lo = datetime.strptime(since, form).replace(tzinfo=_KST)
        src = relay_all if units == bh.RELAY_UNITS else bot_all
        return [ln for ln in src if bh._ts(ln) >= lo], "", ""
    f = bh.collect("2026-09-25 08:00", now=NOW,
                   env={"token": "", "dest": _DEST, "src": {}, "err": "", "inbox": ""},
                   facts_fn=lambda: {"ok": True, "s_LoadState": "loaded", "s_ActiveState": "active",
                                     "s_SubState": "running", "s_MainPID": "4242",
                                     "s_NRestarts": "0"},
                   read=read, start_fn=lambda pid: (bh.parse_start_line(bot_all[0]), ""),
                   tg_fn=lambda *a: {"token": False}, procs_fn=lambda own: [], deploys_fn=lambda: ([], ""))
    assert f["journal"]["ingested"] == []                  # 판정 창엔 수신 줄이 없다
    assert (f["gap"]["kind"], f["gap"]["got"], f["gap"]["guessed"]) == ("ok", 3, True), f["gap"]


# ── 3차 독립 리뷰(e5b528a..abf0836) 반영 ─────────────────────────────────

def _note_with(out: str, needle: str) -> str:
    """판정 출력에서 `needle` 이 든 **그 줄 하나** — 페이지 전체 grep 은 다른 줄이 대신
    만족시킨다(#55·#75)."""
    got = [ln for ln in out.splitlines() if needle in ln]
    assert len(got) == 1, (needle, out)
    return got[0]


def test_other_origin_drops_are_not_called_benign_and_say_how_to_tell():
    """3차 독립 리뷰 M2 — 다른 출처 포워드의 버림을 '게이트가 제 일을 한 것' 이라 **단정**했다.
    나쁜양파는 관련 글만 포워드하는데 그 채널이 **재게시**한 글(다른 채널에서 퍼 온 글)은
    텔레그램이 원래 출처를 달아 BeOn 의 AWAKE 되포워드와 같은 모양으로 온다 — 여기선 둘을 못
    가른다(#165). 판정은 그대로 ⚠️(rc 0 — BeOn 되포워드마다 ❌·❓ 면 `bot_health && 백필`
    이 늘 막히고 진짜 ❌ 를 가린다, #260), 대신 메모가 단정을 빼고 가르는 명령을 건넨다.
    반대 증거: 직접 쓴 글만이면 '제 일' 이라 말해도 참이다(데이터가 아니다).

    ⚠️ 처방이 바뀌었다(실수 #411, #222 — 지우지 않고 다시 썼다). 옛 판은 "그 출처를 .env
    TRADE_SOURCE_ORIGIN 에 더하라" 고 했는데, 채널을 통째로 허용하면 BeOn 이 **같은 채널**의
    무관 글을 되포워드할 때 그것까지 받고, 나쁜양파가 다음에 다른 채널을 재게시하면 같은
    유실이 반복된다. 지금 처방은 '보증하는 판의 릴레이로 다시 포워드' 다 — 그 권고가 남아
    있으면 운영자가 옛 처방대로 .env 를 넓힌다."""
    f = _good()
    f["journal"] = bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _drop("2026-09-25T08:00:02", 6, -100777, "badonion_kr"),
    ])
    rc, out = _v(f)
    assert rc == 0, out
    note = _note_with(out, "다른 출처 포워드 1: @badonion_kr")
    assert note.startswith("⚠️"), note
    assert "여기서 못 가른다" in note and "재게시" in note, note
    assert bh.FIND_CMD in note and "to-forward" in note, note
    assert "보증" in note and "다시 포워드" in note, note
    assert "TRADE_SOURCE_ORIGIN" not in note, note       # 옛 처방(채널 통째 허용)을 권하지 않는다
    # 옛 판의 단정 — 다른 출처가 섞이면 '제 일' 은 **조건부**로만 나온다
    assert "게이트가 제 일을 한 것이다" not in note, note
    # 반대 증거: 직접 쓴 글만 — 거기엔 가를 것이 없다
    f["journal"] = bh.journal_facts([
        _poll("2026-09-25T08:29:50"),
        _jl("2026-09-25T08:00:01", "dropped msg=5 reason=origin origin_type=none "
                                   "origin_chat=None origin_username=None allowed_origins=['x']"),
    ])
    rc2, out2 = _v(f)
    note2 = _note_with(out2, "직접 쓴 글 1 · 다른 출처 포워드 0")
    assert rc2 == 0 and "채널에 직접 쓴 글·명령이라 게이트가 제 일을 한 것이다" in note2, note2
    assert "재게시" not in note2 and bh.FIND_CMD not in note2, note2


def test_find_cmd_uses_the_sync_units_interpreter_and_real_flags():
    """메모가 건네는 명령은 **실재하는 인터프리터·플래그**여야 한다(#316·#371 — 기억으로 적은
    플래그가 unrecognized arguments 로 한 라운드를 태웠다). 동기화 유닛의 ExecStart 와
    백필의 argparse 에서 잰다."""
    unit = (_REPO / "deploy" / "trade-bot-badonion-sync.service").read_text(encoding="utf-8")
    exec_start = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    interp, script = exec_start.split("=", 1)[1].split()[:2]
    assert interp.endswith("/stock-trade/.backfill-venv/bin/python"), exec_start
    assert f".backfill-venv/bin/python {script}" in bh.FIND_CMD, (exec_start, bh.FIND_CMD)
    assert bh.FIND_CMD.startswith("cd ~/stock-trade && "), bh.FIND_CMD
    tree = ast.parse((_REPO / "trade" / "scripts" / "backfill_badonion.py").read_text(
        encoding="utf-8"))
    flags = {a.value for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "add_argument"
             for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    used = {tok for tok in bh.FIND_CMD.split() if tok.startswith("--")}
    assert used == {"--dry-run", "--since", "--find"} and used <= flags, (used, flags)


def test_exception_red_line_says_reforwarded_posts_get_new_ids():
    """3차 독립 리뷰 M1 — 예외로 놓친 릴레이 글의 ❌ 는 '다시 포워드할 것' 을 처방하는데,
    다시 포워드한 글은 비공개 채널에서 **새 번호**를 받아 그 예외 줄과 짝이 안 맞는다 —
    고치고 다시 포워드해 받았어도 줄이 창에 남는 동안 ❌ 가 남는다(재현: 27건 예외 → 디스크
    정리 → 새 번호로 27건 수신 → 여전히 rc 1). 번호로 복구를 알아볼 수 없으니 그 사실과 창을
    좁히는 법을 **그 ❌ 줄에** 적는다."""
    f = _good()
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"),
                                     _exc("2026-09-25T07:50:05", 77)])
    rc, out = _v(f)
    red = _note_with(out, "예외로 놓쳤다(번호 77)")
    assert rc == 1 and red.startswith("❌"), out
    assert "새 번호" in red and "--since" in red and "다시 포워드한 **뒤** 시각" in red, red


def test_pre_restart_shortfall_note_says_manual_backfills_are_invisible():
    """3차 독립 리뷰 M3 — 재시작 전 모자람 메모는 '그 기간을 다시 포워드할 것' 으로 끝났는데,
    손으로 돌린 백필은 저널에 안 남아 '포워드' 수에 안 들어 그 메모가 **다시 포워드한 뒤에도
    그대로** 남는다 — 같은 처방을 되풀이하게 만드는 순환이다. 메모가 그 사실과 '어느 갈래인지
    먼저 볼 것' 을 말한다."""
    f = _good()
    f["running"] = bh.parse_start_line(_start(ts="2026-09-25T08:00:00"))
    f["gap_before"] = bh.delivery_gap(
        [_fwd(datetime(2026, 9, 25, 7, 49, 9, tzinfo=_KST), 27, done=True,
              who="backfill_badonion")], [], NOW)
    assert f["gap_before"]["kind"] == "total", f["gap_before"]
    rc, out = _v(f)
    note = _note_with(out, "지금 프로세스가 뜨기 전")
    assert rc == 0 and note.startswith("⚠️"), out
    assert "손으로 돌린 백필은 저널에 안 남아" in note and bh.FIND_CMD in note, note
    assert "되풀이하지 말고" in note, note


def test_hourly_alert_is_recorded_only_when_delivered(hc, monkeypatch, caplog):
    """3차 독립 리뷰 M4 — 알림 전달이 실패해도(429·네트워크) 사실을 '알렸다' 로 기록해 다음
    실행이 다시 안 알렸다(재현: 429 → 'notify not delivered' 경고 → 신원 기록 → 재시도 없음).
    '한 번만' 은 '한 번 시도' 가 아니라 '한 번 **전달**' 이어야 한다. 대조군: 전달되면 기록하고
    그 다음은 조용하다."""
    fwd = _jl("2026-09-25T07:49:09", "done: forwarded 27 of 27 candidate messages "
              "(skipped_units=0)", logger="backfill_badonion", pid=99)
    _drive(monkeypatch, bot=[_poll("2026-09-25T08:29:50")], relay=[fwd])
    tries = []
    monkeypatch.setattr(hc, "_notify", lambda msg: tries.append(msg) or False)
    with caplog.at_level(logging.WARNING, logger="health-check"):
        hc.check_delivery_gap()
    assert len(tries) == 1 and "27건" in tries[0], tries
    assert not (hc.MARKER_DIR / hc.DELIVERY_ALERTED).exists()
    assert any("전달되지 않아 기록하지 않았다" in r.getMessage() for r in caplog.records)
    # 다음 실행 — 같은 사실을 다시 알린다(이번엔 전달된다) · 그 다음은 조용하다
    monkeypatch.setattr(hc, "_notify", lambda msg: tries.append(msg) or True)
    hc.check_delivery_gap()
    hc.check_delivery_gap()
    assert len(tries) == 2 and tries[1] == tries[0], tries
    saved = json.loads((hc.MARKER_DIR / hc.DELIVERY_ALERTED).read_text(encoding="utf-8"))
    assert len(saved) == 1 and next(iter(saved)).startswith("fwd:"), saved


def test_notify_reports_delivery_and_never_logs_the_command(monkeypatch, caplog):
    """`_notify` 가 **전달됐는가**를 돌려준다(3차 독립 리뷰 M4) — ok:true 일 때만 True,
    건너뜀(토큰 없음)·거절·예외는 False. 예외 문구는 찍지 않는다: subprocess.TimeoutExpired
    는 명령줄 전체(토큰이 든 URL)를 문구에 싣는다(§Secrets)."""
    import subprocess

    from trade.scripts import health_check as hc

    tok = "123456789" + ":" + "AAH" + "x" * 32                # 조립한다 — 리터럴은 스캐너에 걸린다
    monkeypatch.setenv("TRADE_BOT_TOKEN", tok)
    monkeypatch.setenv("TRADE_CHANNEL_CHAT_IDS", "-100123")

    def run_with(stdout: bytes, rc: int = 0):
        return lambda cmd, **kw: subprocess.CompletedProcess(cmd, rc, stdout, b"")
    monkeypatch.setattr(hc.subprocess, "run", run_with(b'{"ok": true, "result": {}}'))
    assert hc._notify("hi") is True
    monkeypatch.setattr(hc.subprocess, "run", run_with(
        b'{"ok":false,"error_code":429,"description":"Too Many Requests"}'))
    assert hc._notify("hi") is False
    monkeypatch.setattr(hc.subprocess, "run", run_with(b"", rc=28))
    assert hc._notify("hi") is False

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)
    monkeypatch.setattr(hc.subprocess, "run", boom)
    with caplog.at_level(logging.WARNING, logger="health-check"):
        assert hc._notify("hi") is False
    msgs = [r.getMessage() for r in caplog.records]
    assert any("TimeoutExpired" in m for m in msgs), msgs
    assert not any(tok in m for m in msgs), msgs
    monkeypatch.delenv("TRADE_BOT_TOKEN")
    assert hc._notify("hi") is False                       # 건너뜀도 전달이 아니다


def test_save_alerted_is_atomic_and_per_process(hc, monkeypatch, caplog):
    """3차 독립 리뷰 L3 — '원자적 쓰기' 를 문서가 적는데 그걸 재는 테스트가 없었다(비원자적
    쓰기 뮤테이션 M25 생존). 교체(rename)가 실패하면 **옛 기록이 그대로** 남아야 하고, 임시
    파일 이름엔 PID 가 붙어 겹친 실행이 서로의 임시 파일을 덮지 않는다."""
    import os

    p = hc.MARKER_DIR / hc.DELIVERY_ALERTED
    p.write_text(json.dumps({"fwd:old": 1.0}), encoding="utf-8")
    moves = []
    real_replace = os.replace

    def failing_replace(src, dst):
        moves.append((Path(src).name, Path(dst).name))
        raise OSError("disk full")
    monkeypatch.setattr(hc.os, "replace", failing_replace)
    with caplog.at_level(logging.WARNING, logger="health-check"):
        hc._save_alerted({"fwd:new": 2.0})
    assert json.loads(p.read_text(encoding="utf-8")) == {"fwd:old": 1.0}
    assert moves == [(f"{hc.DELIVERY_ALERTED}.{os.getpid()}.tmp", hc.DELIVERY_ALERTED)], moves
    assert any("알림 기록을 못 썼다" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(hc.os, "replace", real_replace)
    hc._save_alerted({"fwd:new": 2.0})
    assert json.loads(p.read_text(encoding="utf-8")) == {"fwd:new": 2.0}
    # 성공한 교체는 임시 파일을 남기지 않는다(실패로 남았던 같은 이름도 그 쓰기가 가져갔다)
    assert [x.name for x in hc.MARKER_DIR.iterdir()] == [hc.DELIVERY_ALERTED]


def test_msgs_caps_the_list_and_says_how_many_were_cut():
    """3차 독립 리뷰 L4 — 번호 목록 상한(8)이 무가드였다(뮤테이션 M33 생존). 자르면 **자른
    수를 말한다**(#45)."""
    items = [{"msg": i} for i in range(1, 13)]
    assert bh._msgs(items) == "1, 2, 3, 4, 5, 6, 7, 8 외 4건"
    assert bh._msgs(items[:8]) == "1, 2, 3, 4, 5, 6, 7, 8"


def test_pre_restart_exceptions_of_other_origin_posts_are_not_called_lost_relay_data():
    """3차 독립 리뷰 L4 — 재시작 전 예외 메모의 `relay is not False` 필터가 무가드였다
    (뮤테이션 M28 생존). 직접 쓴 글·다른 출처 글의 예외는 릴레이 데이터 손실 메모에 들지
    않는다. 대조군: 릴레이 원천 글이면 든다."""
    old = [_start(ts="2026-09-25T06:00:00", pid=1111),
           _exc("2026-09-25T06:30:00", 56, chat=-100555, user="SomeNews", pid=1111)]
    new = [_start(ts="2026-09-25T08:00:00", pid=4242), _poll("2026-09-25T08:29:50")]
    f, _ = _collect(old + new)
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 0 and "재시작 전 프로세스가 채널 글" not in out, out
    f2, _ = _collect([old[0], _exc("2026-09-25T06:30:00", 55, pid=1111)] + new)
    f2["tg"] = _good()["tg"]
    _rc2, out2 = _v(f2)
    assert "재시작 전 프로세스가 채널 글 1건을 처리하다 예외로 놓쳤다(번호 55)" in out2, out2


# ── 재게시 보증(실수 #411) ────────────────────────────────────────────────
# 나쁜양파가 다른 채널에서 퍼 온 글은 원래 출처로 와서 봇의 출처 목록에 안 걸린다. 릴레이가
# 포워드 **전에** 그 글을 보증하고 봇이 받는다. 진단은 (a) 봇 로그의 새 칸(원래 글번호·보증
# 대조 결과·채널 제목)을 소스의 실제 형식으로 읽고 (b) 보증 **뒤의** 버림을 ❌ 로, 보증 **전의**
# 버림을 받았는지와 함께 ⚠️ 로 가르며 (c) 보증을 모르는 옛 판 봇이면 ❓ 로 `&&` 를 막는다.
_REPOST = -1009990000001        # 합성 — 운영 재게시 채널 ID 를 쓰지 않는다(#393)
_REPOST2 = -1009990000002       # 합성 — 같은 글번호를 가진 다른 재게시 채널


def _bot_line(ts: str, head: str, args, level: str = "info") -> str:
    """봇 소스의 **실제** 형식(`log.<level>("…")`)을 채운 저널 줄(#155 — 생산자 = 소비자)."""
    fmt = _log_format(_REPO / "trade" / "bot.py", head, level)
    return _jl(ts, fmt % args)


def _vdrop(ts: str, msg: int, omsg: int, *, chat=_REPOST, vouch="miss", title="퍼온 채널",
           pid: int = 4242) -> str:
    fmt = _log_format(_REPO / "trade" / "bot.py", "dropped msg=")
    return _jl(ts, fmt % (msg, "channel", chat, None, omsg, ["badonions", "beon_beclear"],
                          vouch, title), pid=pid)


def _vaccept(ts: str, msg: int, omsg: int, *, chat=_REPOST, title="퍼온 채널") -> str:
    fmt = _log_format(_REPO / "trade" / "bot.py", "accepted msg=")
    return _jl(ts, fmt % (msg, chat, omsg, title))


def _vouched(*pairs, at=datetime(2026, 9, 25, 7, 0, tzinfo=_KST)) -> dict:
    return {(c, m): {"at": at, "by": "backfill", "title": "퍼온 채널"} for c, m in pairs}


def test_drop_and_accept_lines_from_the_bot_source_parse_every_new_field():
    """버림 줄의 새 칸 — 원래 글번호(보증 대조의 열쇠) · 보증 대조 결과 · 채널 제목 — 을 봇
    소스의 형식 그대로 채워 읽는다. 제목은 사람이 붙인 자유 문자열이라 따옴표·가짜 칸이 섞여도
    뒤틀리지 않아야 한다(줄 끝 repr 로 둔 이유). 옛 줄은 그 칸이 None 이다(지어내지 않는다)."""
    tricky = "퍼온 '채널' vouch=hit origin_msg=1"
    j = bh.journal_facts([
        _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable", title=tricky),
        _vdrop("2026-09-25T08:00:01", 6, 4243, title=None),
        _vaccept("2026-09-25T08:00:02", 7, 4244),
        _drop("2026-09-25T08:00:03", 8, -100555, "old_build"),
    ])
    got = [(d["msg"], d["chat"], d["omsg"], d["vouch"], d["title"]) for d in j["drops_origin"]]
    assert got == [(5, _REPOST, 4242, "unreadable", tricky), (6, _REPOST, 4243, "miss", None),
                   (8, -100555, None, None, None)], got
    assert [(a["msg"], a["chat"], a["omsg"], a["title"]) for a in j["vouch_accepts"]] == [
        (7, _REPOST, 4244, "퍼온 채널")]
    assert len(j["ingested"]) == 0                 # 보증 수용 줄은 수신 줄이 아니다(따로 센다)


def test_a_channel_title_cannot_become_another_journal_fact():
    """제목은 **남(채널 주인)이 쓴** 글이다 — 그 안의 `ingested msg=…`·정상 폴링 문구·수용 줄
    문구가 다른 사실로 읽히면 버림이 사라지고 없는 수신·폴링·보증 수용이 생긴다(독립 리뷰
    #411 L2 실측). 분류는 봇이 쓴 칸까지만 본다 — 수용 줄의 제목에 버림 줄 문구가 들어
    있어도, 예외 줄의 예외 문구에 수신 줄 문구가 들어 있어도 같다. 제목 자체는 그대로 읽는다."""
    fake_ingest = "x ingested msg=999 y"
    fake_poll = 'x /getUpdates "HTTP/1.1 200 OK" y'
    fake_accept = f"accepted msg=7 reason=relay_vouch origin_chat={_REPOST} origin_msg=5"
    fake_drop = (f" dropped msg=8 reason=origin origin_type=channel origin_chat={_REPOST} "
                 "origin_username=None origin_msg=6")
    exc_fmt = _log_format(_REPO / "trade" / "bot.py", "handler error update=channel_post", "error")
    exc = _jl("2026-09-25T08:00:04", exc_fmt % (77, "channel", _BAD, "Badonions", 4242,
                                                 "ValueError", "caption ingested msg=55"))
    j = bh.journal_facts([
        _vdrop("2026-09-25T08:00:00", 1, 101, title=fake_ingest),
        _vdrop("2026-09-25T08:00:01", 2, 102, title=fake_poll),
        _vdrop("2026-09-25T08:00:02", 3, 103, title=fake_accept),
        _vaccept("2026-09-25T08:00:03", 4, 104, title=fake_drop),
        exc,
    ])
    assert [(d["msg"], d["title"]) for d in j["drops_origin"]] == [
        (1, fake_ingest), (2, fake_poll), (3, fake_accept)], j["drops_origin"]
    assert j["ingested_ids"] == set() and j["ingested"] == [], j["ingested_ids"]
    assert j["polls"] == {} and j["last_ok"] is None, j["polls"]
    assert [(a["msg"], a["omsg"], a["title"]) for a in j["vouch_accepts"]] == [(4, 104, fake_drop)]
    assert [(e["msg"], e["chat"]) for e in j["exceptions"]] == [(77, _BAD)], j["exceptions"]


def test_vouch_order_is_by_time_with_the_journals_one_second_grain():
    """보증 **전의** 버림과 **뒤의** 버림은 뜻이 반대다 — 앞은 보증하기 전의 판이 포워드한
    것(다시 포워드하면 받는다), 뒤는 보증 경로가 깨진 것(❌). 저널 시각은 초로 잘려 같은 초
    안의 보증은 '전' 이다(보증은 포워드보다 늘 먼저다). 모르면 None(#165)."""
    at = datetime(2026, 9, 25, 7, 0, 0, 500000, tzinfo=_KST)
    v = {(_REPOST, 1): {"at": at}}
    d = {"chat": _REPOST, "omsg": 1}
    assert bh.vouch_order({**d, "ts": at.replace(microsecond=0)}, v) == "before"
    assert bh.vouch_order({**d, "ts": at + timedelta(seconds=5)}, v) == "before"
    assert bh.vouch_order({**d, "ts": at - timedelta(seconds=2)}, v) == "after"
    assert bh.vouch_order({**d, "omsg": None, "ts": at}, v) is None
    assert bh.vouch_order({**d, "ts": None}, v) is None
    assert bh.vouch_order({**d, "ts": at}, {(_REPOST, 1): {"at": None}}) is None
    assert bh.vouch_order({**d, "ts": at}, {}) is None
    assert bh.vouch_order({**d, "omsg": 2, "ts": at}, v) is None
    # 릴레이 글 판정: 보증 **뒤의** 버림만 릴레이 글이다 — 원천 이름·ID 와 같은 자리
    assert bh.relay_origin({**d, "type": "channel", "user": "", "ts": at}, (), (), v) is True
    assert bh.relay_origin({**d, "type": "channel", "user": "",
                            "ts": at - timedelta(seconds=2)}, (), (), v) is False
    fd = bh.forward_drops([{**d, "type": "channel", "user": "", "ts": at}], (), (), v)
    assert [(x["relay"], x["why"], x["vouched"]) for x in fd] == [(True, "vouch", "before")]
    fn = bh.forward_drops([{**d, "type": "channel", "user": "Badonions", "ts": at}],
                          ["Badonions"], (), v)
    assert fn[0]["why"] == "name"                  # 원천 이름이 먼저다 — 처방이 .env 쪽이다


def _vf(pairs=None, err="", exists=True, path="/home/h/.trade/relay_origins.json") -> dict:
    return {"path": path, "exists": exists, "pairs": pairs or {}, "err": err}


def test_a_drop_after_the_vouch_is_red_with_the_bots_own_reason():
    """보증이 글보다 먼저였는데 봇이 버렸다 = 보증 경로가 깨졌다(봇이 기록을 못 읽음 · 데이터
    디렉터리가 갈림). 원천 이름 버림과 처방이 다르다(#82) — .env 를 권하지 않는다."""
    f = _good()
    f["vouch"] = _vf(_vouched((_REPOST, 4242)))
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"),
                                     _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable")])
    rc, out = _v(f)
    assert rc == 1, out
    line = _note_with(out, "보증한** 재게시 글 1건")
    assert line.startswith("❌") and "vouch=unreadable" in line, line
    assert "TRADE_SOURCE_ORIGIN" not in line and "데이터 디렉터리" in line, line
    assert "릴레이 포워드 1건을 **출처 게이트**에서 버렸다" not in out     # 원천 이름 갈래가 아니다
    # 같은 버림이라도 보증이 없으면 ❌ 가 아니다 — 판정할 수 없는 다른 출처 메모다
    f["vouch"] = _vf()
    rc2, out2 = _v(f)
    assert rc2 == 0 and "보증한** 재게시 글" not in out2, out2


def test_a_vouched_drop_that_came_back_later_is_a_note_not_red():
    """보증 뒤에 버렸어도 **그 뒤** 같은 원래 글을 보증으로 받았으면 손실이 아니다 — ❌ 로 두면
    원인을 고치고 다시 포워드해 받았는데도 그 줄이 창에서 빠질 때까지 `bot_health && 다시
    포워드` 가 막힌다(독립 리뷰 #411 L5). 한 번 깨졌던 사실은 메모로 남긴다(#43). 다른 글을
    받은 것은 이 글의 회복이 아니고, 같은 초의 수용 줄은 순서를 모른다 — 받았다고 단정하지
    않는다(#165)."""
    f = _good()
    f["vouch"] = _vf(_vouched((_REPOST, 4242), (_REPOST, 4243)))
    drop = _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable")

    def with_accept(ts, omsg):
        f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"), drop,
                                         _vaccept(ts, 9, omsg)])
        return _v(f)
    rc, out = with_accept("2026-09-25T08:20:00", 4242)
    assert rc == 0, out
    note = _note_with(out, "보증 뒤에 버렸다가 그 뒤 보증으로 받았다")
    assert note.startswith("⚠️") and "vouch=unreadable" in note, note
    assert "보증한** 재게시 글" not in out, out
    rc2, out2 = with_accept("2026-09-25T08:20:00", 4243)          # 다른 글을 받았다
    assert rc2 == 1 and "보증한** 재게시 글 1건" in out2, out2
    rc3, out3 = with_accept("2026-09-25T08:00:00", 4242)          # 같은 초 — 순서를 모른다
    assert rc3 == 1 and "보증한** 재게시 글 1건" in out3, out3


def test_an_accept_of_the_same_number_in_another_channel_does_not_heal():
    """글번호는 채널마다 따로 센다 — 다른 재게시 채널의 같은 번호를 받은 것은 이 글의 회복이
    아니다. 회복으로 치면 손실이 메모(rc 0)가 되고 매시간 알림에서도 빠진다(배포 전 독립 리뷰
    L5: `vouch_recovered` 의 채널 대조를 지우는 뮤테이션이 살아남았다)."""
    f = _good()
    f["vouch"] = _vf(_vouched((_REPOST, 4242), (_REPOST2, 4242)))
    f["journal"] = bh.journal_facts([
        _start(), _poll("2026-09-25T08:29:50"),
        _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable"),
        _vaccept("2026-09-25T08:20:00", 9, 4242, chat=_REPOST2)])
    rc, out = _v(f)
    assert rc == 1 and "보증한** 재게시 글 1건" in out, out
    assert "보증 뒤에 버렸다가 그 뒤 보증으로 받았다" not in out, out


def test_a_healed_drop_now_does_not_hide_the_previous_process_relay_drop():
    """지금 프로세스의 **치유된** 보증 버림(메모)이 재시작 전 프로세스의 릴레이 버림 메모를
    가렸다 — 옛 게이트가 치유분까지 센 목록을 봤다(배포 전 독립 리뷰 L3, 이 델타가 만든 회귀).
    치유된 버림은 ❌ 가 아니므로 옛 메모를 막을 이유가 없다. 그리고 그 메모의 건수는 재시작
    전 몫만 센다(창 전체를 세면 지금 프로세스의 버림이 섞인다)."""
    old = [_start(ts="2026-09-25T06:00:00", pid=1111),
           _drop("2026-09-25T06:20:00", 9, _BAD, "Badonions", pid=1111)]
    new = [_start(ts="2026-09-25T07:50:00", pid=4242), _poll("2026-09-25T08:29:50"),
           _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable"),
           _vaccept("2026-09-25T08:20:00", 11, 4242)]
    f, _ = _collect(old + new)
    assert f["journal_cur"] is not None and f["running"]["pid"] == 4242
    f["tg"] = _good()["tg"]
    f["vouch"] = _vf(_vouched((_REPOST, 4242)))
    rc, out = _v(f)
    assert rc == 0, out
    assert _note_with(out, "보증 뒤에 버렸다가 그 뒤 보증으로 받았다"), out
    assert "⚠️ 재시작 전 프로세스가 릴레이 포워드 1건을 출처 게이트에서" in out, out


def test_a_drop_before_the_vouch_is_a_note_that_says_whether_it_came_back():
    """버린 **뒤에** 보증됐다 = 버릴 땐 보증이 없었다(보증하기 전의 판이 포워드했거나 다른 릴레이가
    되포워드했다 — 진단은 둘을 못 가르므로 원인을 단정하지 않는다, 독립 리뷰 #411 I12 · #165).
    받았는지는 봇의 보증 수용 줄이 말한다 — 받았으면 그렇다고, 아니면 볼 곳을."""
    late = datetime(2026, 9, 25, 8, 10, tzinfo=_KST)
    f = _good()
    f["vouch"] = _vf(_vouched((_REPOST, 4242), (_REPOST, 4243), at=late))
    f["journal"] = bh.journal_facts([
        _start(), _poll("2026-09-25T08:29:50"),
        _vdrop("2026-09-25T08:00:00", 5, 4242), _vdrop("2026-09-25T08:00:01", 6, 4243),
        _vaccept("2026-09-25T08:10:05", 9, 4242)])
    rc, out = _v(f)
    assert rc == 0, out
    note = _note_with(out, "버린 **뒤에** 릴레이가 보증한 재게시 글")
    assert note.startswith("⚠️") and "그중 2건" in note and "받은 줄이 1건" in note, note
    assert "나머지 1건은 아직 받은 줄이 없다" in note and bh.FIND_CMD in note, note
    assert "버릴 땐 보증이 없었다 — 보증하기 전의 판이나 다른 릴레이가" in note, note
    assert "(보증하기 전의 판이 포워드했다)" not in note, note       # 한 원인으로 단정하지 않는다
    # 보증 없는 다른 출처가 없으니 '여기서 못 가른다' 는 안 붙는다(할 말이 없는 갈래)
    assert "보증 없는 다른 출처 포워드는 여기서 못 가른다" not in note, note


def test_a_bot_that_does_not_know_vouches_blocks_the_reforward_chain():
    """버림은 적지만 보증을 모르는 판(2026-09-25 80c0e6c) — 다시 포워드해도 재게시 글을 또
    버린다. ❓(rc 2)라 `bot_health && 다시 포워드` 가 봇 재시작 전엔 흐르지 않는다(#409). 더
    옛 판(버림도 안 적음)은 그 ❓ 하나만 — 같은 사실을 두 줄로 말하지 않는다(#395)."""
    f = _good()
    f["running"] = bh.parse_start_line(_start(vouch=False))
    rc, out = _v(f)
    assert rc == 2 and bh._OLD_VOUCH_UNK in out, out
    f["running"] = bh.parse_start_line(_start(drop_log=False))
    rc2, out2 = _v(f)
    assert rc2 == 2 and bh._OLD_BUILD_UNK in out2 and bh._OLD_VOUCH_UNK not in out2, out2


def test_the_deployed_bot_lets_the_reforward_chain_through(tmp_path):
    """배포 **직후**(보증을 아는 새 판 봇 · 다시 포워드하기 전)엔 `bot_health && 다시 포워드`
    가 흘러야 한다 — 창 안에 옛 프로세스가 버린 재게시 27건이 그대로 있어도. 그 옛 버림을
    ❌·❓ 로 올리면 사슬이 영영 막힌다: 그 글은 다시 포워드해야 돌아오는데, 다시 포워드는 이
    명령이 0 을 내야 나간다(#409 의 반대편 — 막아야 할 때만 막는다). 옛 버림은 사실 메모로
    남는다(#41). 2026-09-25 운영 절차(배포 → `bot_health && 백필 --since`)를 그대로 태운다."""
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text("{}\n", encoding="utf-8")
    old = _start(ts="2026-09-25T07:40:00", pid=1111, vouch=False, inbox=str(inbox))
    fwd = _jl("2026-09-25T07:49:09", "done: forwarded 27 of 27 candidate messages "
              "(skipped_units=0)", logger="backfill_badonion", pid=99)
    drops = [_drop("2026-09-25T07:49:%02d" % (10 + i), 900 + i, _REPOST, "None", pid=1111)
             for i in range(27)]
    new = _start(ts="2026-09-25T08:10:00", pid=4242, inbox=str(inbox))
    f, _ = _collect([old, *drops, new, _poll("2026-09-25T08:29:50")], [fwd], tmp=tmp_path)
    f["tg"] = _good()["tg"]
    rc, out = _v(f)
    assert rc == 0, out
    assert f"다른 출처 포워드 27: {_REPOST}" in _note_with(out, "출처 게이트가"), out
    # 대조군: 같은 창인데 옛 판이 아직 돈다(배포 전) — 그때는 ❓(rc 2)로 막는다
    facts = {"ok": True, "s_LoadState": "loaded", "s_ActiveState": "active",
             "s_SubState": "running", "s_MainPID": "1111", "s_NRestarts": "0"}
    f2, _ = _collect([old, *drops, _poll("2026-09-25T08:29:50", pid=1111)], [fwd],
                     tmp=tmp_path, facts=facts)
    f2["tg"] = _good()["tg"]
    rc2, out2 = _v(f2)
    assert rc2 == 2 and bh._OLD_VOUCH_UNK in out2, out2


def test_an_unreadable_vouch_record_is_a_note_not_a_block():
    """못 읽은 보증 기록은 ⚠️ 다 — ❓ 로 막으면 그 기록을 새로 쓸 다음 보증(= 다시 포워드)을
    `&&` 가 막는다. 무엇을 못 하는지(재게시 버림을 알아보기)와 경로를 말한다(#82)."""
    f = _good()
    f["vouch"] = _vf(err="형식 오류(JSONDecodeError)")
    rc, out = _v(f)
    assert rc == 0, out
    note = _note_with(out, "재게시 보증 기록을 못 읽었다")
    assert note.startswith("⚠️") and "형식 오류" in note and "relay_origins.json" in note, note


def test_render_names_the_vouch_state_and_the_accepted_count():
    f = _good()
    f["vouch"] = _vf(_vouched((_REPOST, 1), (_REPOST, 2), (-1009990000002, 3)))
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"),
                                     _vaccept("2026-09-25T08:00:00", 9, 1)])
    out = "\n".join(bh.render(f, "x"))
    assert "재게시 보증 받음" in _note_with(out, "③ 실행 중 설정")
    assert "보증 수용 1건" in _note_with(out, "④ 저널")
    vline = _note_with(out, "재게시 보증 기록 ")
    assert "채널 2개 · 글 3건" in vline and f"{_REPOST}('퍼온 채널') 2건" in vline, vline
    f["running"] = bh.parse_start_line(_start(vouch=False))
    f["vouch"] = _vf(exists=False)
    out2 = "\n".join(bh.render(f, "x"))
    assert "재게시 보증 모름(옛 판)" in out2
    assert "없음(아직 재게시 글을 보증한 적 없다" in _note_with(out2, "재게시 보증 기록 ")
    f["vouch"] = _vf(err="빈 파일")
    assert "못 읽음(빈 파일)" in _note_with("\n".join(bh.render(f, "x")), "재게시 보증 기록 ")


def test_collect_reads_the_vouch_record_where_the_bot_does(tmp_path):
    """진단은 봇과 **같은 데이터 디렉터리**의 기록을 **같은 함수**로 읽는다(#35) — 그리고 수
    대조의 버림 분류에도 넘긴다(보증 뒤의 버림 = 릴레이 글)."""
    from trade import relay_origins as ro
    ro.vouch({(_REPOST, 4242): "퍼온 채널"}, by="listener", path=ro.path_in(tmp_path),
             now=datetime(2026, 9, 25, 7, 0, tzinfo=_KST))
    f, _ = _collect([_start(), _poll("2026-09-25T08:29:50"),
                     _vdrop("2026-09-25T08:00:00", 5, 4242)], tmp=tmp_path)
    assert f["vouch"]["path"] == str(ro.path_in(tmp_path)) and f["vouch"]["err"] == ""
    assert set(f["vouch"]["pairs"]) == {(_REPOST, 4242)}
    rc, out = _v(f)
    assert rc == 1 and "보증한** 재게시 글 1건" in out, out


def test_the_hourly_check_alerts_a_dropped_vouched_repost_with_its_own_advice(
        hc, monkeypatch):
    """매시간 알림도 보증 뒤의 버림을 알린다 — `health_check` 가 봇과 같은 데이터 디렉터리의
    기록을 넘긴다(배선은 진짜 `delivery_check` 로 태운다). 처방은 .env 가 아니다(#82). 기록이
    없으면 같은 버림은 보증 없는 다른 출처라 알리지 않는다(#260)."""
    from trade import relay_origins as ro
    drop = _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="miss")
    _drive(monkeypatch, bot=[_poll("2026-09-25T08:29:50"), drop], relay=[])
    hc.check_delivery_gap()
    assert hc._sent == [], hc._sent
    ro.vouch({(_REPOST, 4242): "퍼온 채널"}, by="listener", path=ro.path_in(hc.DATA_DIR),
             now=datetime(2026, 9, 25, 7, 0, tzinfo=_KST))
    hc.check_delivery_gap()
    assert len(hc._sent) == 1, hc._sent
    assert "보증한</b> 재게시 글 <b>1건을 출처 게이트에서 버렸습니다</b>" in hc._sent[0], hc._sent[0]
    assert "TRADE_SOURCE_ORIGIN" not in hc._sent[0]
    hc.check_delivery_gap()
    assert len(hc._sent) == 1                              # 같은 버림은 한 번만


def test_the_hourly_check_skips_a_vouched_drop_that_came_back(hc, monkeypatch):
    """매시간 알림도 같은 규칙이다(#38) — 보증 뒤의 버림이라도 그 뒤 같은 원래 글을 받았으면
    알릴 손실이 아니다(독립 리뷰 #411 L5). 대조군: 받은 줄이 없으면 알린다."""
    from trade import relay_origins as ro
    ro.vouch({(_REPOST, 4242): "퍼온 채널"}, by="listener", path=ro.path_in(hc.DATA_DIR),
             now=datetime(2026, 9, 25, 7, 0, tzinfo=_KST))
    bot = [_poll("2026-09-25T08:29:50"), _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable"),
           _vaccept("2026-09-25T08:20:00", 9, 4242)]
    _drive(monkeypatch, bot=bot, relay=[])           # `_drive` 는 부를 때 줄을 읽는다
    hc.check_delivery_gap()
    assert hc._sent == [], hc._sent
    del bot[-1]                                      # 대조군: 받은 줄이 없다 → 알린다
    hc.check_delivery_gap()
    assert len(hc._sent) == 1, hc._sent


def test_the_hourly_check_does_not_heal_with_another_channels_same_number(hc, monkeypatch):
    """매시간 알림도 같은 규칙이다(#38) — 다른 재게시 채널의 같은 글번호를 받은 줄은 이 글의
    회복이 아니다(배포 전 독립 리뷰 L5)."""
    from trade import relay_origins as ro
    ro.vouch({(_REPOST, 4242): "퍼온 채널"}, by="listener", path=ro.path_in(hc.DATA_DIR),
             now=datetime(2026, 9, 25, 7, 0, tzinfo=_KST))
    bot = [_poll("2026-09-25T08:29:50"), _vdrop("2026-09-25T08:00:00", 5, 4242, vouch="unreadable"),
           _vaccept("2026-09-25T08:20:00", 9, 4242, chat=_REPOST2)]
    _drive(monkeypatch, bot=bot, relay=[])
    hc.check_delivery_gap()
    assert len(hc._sent) == 1, hc._sent


def test_the_hourly_check_says_when_it_cannot_read_the_vouch_record(hc, monkeypatch, caplog):
    """보증 기록을 못 읽으면 **그 갈래만** 못 본다는 사실을 남기고 나머지 대조는 그대로 한다
    (#54 — 판정 불가를 '이상 없음' 으로 접지 않는다). 독립 리뷰 #411 에서 이 경고를 지워도
    전부 통과했다(뮤테이션 HC2)."""
    from trade import relay_origins as ro
    rec = ro.path_in(hc.DATA_DIR)
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text("{broken", encoding="utf-8")
    _drive(monkeypatch, bot=[_poll("2026-09-25T08:29:50"), _exc("2026-09-25T08:00:05", 77)],
           relay=[])
    with caplog.at_level(logging.WARNING):
        hc.check_delivery_gap()
    warn = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("재게시 보증 기록을 못 읽었다" in m and "형식 오류" in m for m in warn), warn
    assert len(hc._sent) == 1, hc._sent          # 나머지 대조(예외로 놓친 글)는 그대로 알린다


def test_an_exception_on_a_vouched_repost_is_a_lost_relay_post():
    """재게시 글을 처리하다 예외로 놓치면(수신 줄 없음) 릴레이 글의 손실이다 — 원천 이름이
    아니어도 보증이 그 글을 릴레이 것으로 알아본다(버림과 **같은 규칙**, #38 — 헬퍼만 재면
    예외 쪽 배선을 떼어도 통과했다: 뮤테이션 H2). 보증이 없으면 다른 출처 글이라 메모다."""
    f = _good()
    f["vouch"] = _vf(_vouched((_REPOST, 4242)))
    f["journal"] = bh.journal_facts([_start(), _poll("2026-09-25T08:29:50"),
                                     _exc("2026-09-25T08:00:05", 77, user="None", chat=_REPOST)])
    rc, out = _v(f)
    assert rc == 1, out
    assert "❌ 봇이 릴레이 원천의 채널 글 1건을 처리하다 예외로 놓쳤다(번호 77)" in out, out
    f["vouch"] = _vf()
    rc2, out2 = _v(f)
    assert rc2 == 0 and "릴레이 원천이 아닌 채널 글 1건" in out2, out2


# ── 봇 시작 ↔ 배포 대조 (실수 #420) ─────────────────────────────────────────
# 2026-09-26 VM: 24시간 창에 시작 4회 = base merge 4회(80c0e6c·2a0b3da·af57db1·88944f2)
# 인데 매번 "창 안에서 봇이 4번 시작했다 … 배포·수동 재시작이 아니면 봇이 반복해 죽는다"
# 가 떴다. 배포 여부는 체크아웃이 안다(#86) — auto-update 는 `git reset --hard` 로 HEAD 를
# 옮기므로 reflog 에 그 시각이 남는다.

_UTC = timezone.utc
# VM 실측 시작 시각(KST)과 그 직전 체크아웃 갱신(auto-update 타이머 틱)
_VM_STARTS = ("2026-09-25T17:48:12", "2026-09-25T22:28:40", "2026-09-26T02:37:05",
              "2026-09-26T05:49:34")


def _kst_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_KST)


def _starts_journal(stamps, pid0=5000):
    return bh.journal_facts([_start(ts, pid=pid0 + i) for i, ts in enumerate(stamps)]
                            + [_poll("2026-09-26T08:29:50", pid=pid0 + len(stamps) - 1)])


def _deploys(stamps, lead_s=9):
    return {"times": [_kst_dt(s) - timedelta(seconds=lead_s) for s in stamps], "err": ""}


def _restart_note(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "번 시작했" in ln]


def test_starts_explained_by_deploys_do_not_warn():
    """재현: 시작 4회가 전부 배포 직후면 경고하지 않는다(늘 뜨는 경고는 아무것도 안 잰다
    — #25·#260). 옛 판은 이 픽스처에서 ⚠️ 를 찍었다."""
    f = _good()
    f["journal"] = _starts_journal(_VM_STARTS)
    f["deploys"] = _deploys(_VM_STARTS)
    rc, out = _v(f)
    assert rc == 0 and not _restart_note(out), out
    # 사실은 버리지 않는다 — ④ 줄이 배포로 설명됐다고 말한다(#43)
    f.update(facts={"s_NRestarts": "0"}, journal_err="", forwards=[],
             env={"dest": _DEST, "src": {}, "err": "", "inbox": "/home/h/.trade/inbox.jsonl"})
    r4 = [ln for ln in bh.render(f, "x") if ln.startswith("④")]
    assert r4 and "시작 4회(전부 배포 직후)" in r4[0], r4


def test_unexplained_start_among_deploys_still_warns_and_names_it():
    stamps = _VM_STARTS + ("2026-09-26T07:10:00",)          # 배포 없이 한 번 더
    f = _good()
    f["journal"] = _starts_journal(stamps)
    f["deploys"] = _deploys(_VM_STARTS)
    f["facts"] = {"s_NRestarts": "1"}
    rc, out = _v(f)
    note = _restart_note(out)
    assert rc == 0 and len(note) == 1, out
    assert "5번 시작했" in note[0] and "1번은 배포" in note[0], note
    assert "2026-09-26 07:10:00 KST" in note[0], note          # 어느 시작인지 이름을 댄다
    assert "systemd 자동 재시작 누적 1회" in note[0], note
    assert "17:48:12" not in note[0], note                      # 설명된 시작은 나열하지 않는다


def test_crash_loop_after_one_deploy_is_not_explained_by_it():
    """배포 한 번은 시작 한 번만 설명한다 — 새 판이 뜨자마자 죽어 systemd 가 10초마다
    다시 띄우면(Restart=always · RestartSec=10) 그 나머지는 배포가 아니다."""
    loop = ("2026-09-26T05:49:34", "2026-09-26T05:49:46", "2026-09-26T05:49:58")
    f = _good()
    f["journal"] = _starts_journal(loop)
    f["deploys"] = _deploys(loop[:1])
    _rc, out = _v(f)
    note = _restart_note(out)
    assert len(note) == 1 and "2번은 배포" in note[0], out


def test_start_before_or_long_after_a_deploy_is_not_explained():
    s = ("2026-09-26T01:00:00", "2026-09-26T02:00:00", "2026-09-26T03:00:00")
    f = _good()
    f["journal"] = _starts_journal(s)
    # 첫째는 배포 5초 **전**, 둘째는 배포 한참 뒤, 셋째만 직후
    f["deploys"] = {"times": [_kst_dt(s[0]) + timedelta(seconds=5),
                              _kst_dt(s[1]) - timedelta(seconds=bh.DEPLOY_START_SLACK_S + 60),
                              _kst_dt(s[2]) - timedelta(seconds=30)], "err": ""}
    _rc, out = _v(f)
    note = _restart_note(out)
    assert len(note) == 1 and "2번은 배포" in note[0], out
    assert "01:00:00" in note[0] and "02:00:00" in note[0] and "03:00:00" not in note[0], note


def test_unreadable_deploy_record_keeps_the_old_warning_with_the_reason():
    """배포 기록을 못 읽으면 '배포라서 괜찮다' 고 가정하지 않는다(#54·#165)."""
    f = _good()
    f["journal"] = _starts_journal(_VM_STARTS)
    f["deploys"] = {"times": None, "err": "fatal: detected dubious ownership"}
    _rc, out = _v(f)
    note = _restart_note(out)
    assert len(note) == 1 and "4번 시작했다" in note[0], out
    assert "배포 기록(git reflog)을 못 읽어" in note[0] and "dubious ownership" in note[0], note
    f.pop("deploys")
    _rc, out2 = _v(f)
    assert len(_restart_note(out2)) == 1, out2


def test_attribute_starts_pairs_each_deploy_with_one_start():
    d = datetime(2026, 9, 26, 0, 0, tzinfo=_UTC)
    starts = [d + timedelta(seconds=10), d + timedelta(seconds=130), None]
    # 배포 둘이 2분 간격 — 각자 자기 뒤의 시작 하나씩(먼저 온 배포가 두 시작을 다 먹지 않는다)
    a = bh.attribute_starts(starts, [d, d + timedelta(seconds=120)])
    assert a["explained"] == 2 and a["unexplained"] == [None], a
    a2 = bh.attribute_starts(starts, [d])
    assert a2["explained"] == 1 and a2["unexplained"] == [starts[1], None], a2
    assert bh.attribute_starts(starts, [])["explained"] == 0


def test_read_deploys_parses_a_real_git_reflog(tmp_path):
    """생산자(git)의 실제 출력으로 잰다 — 손으로 쓴 reflog 줄은 형식 변경을 축복한다(#155)."""
    import shutil
    import subprocess
    if not shutil.which("git"):
        pytest.skip("git 없음")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, env=env,
                       capture_output=True)
    g("init", "-q")
    (tmp_path / "a").write_text("1")
    g("add", "a")
    g("commit", "-qm", "one")
    (tmp_path / "a").write_text("2")
    g("commit", "-qam", "two")
    before = datetime.now(_UTC) - timedelta(minutes=1)
    g("reset", "--hard", "-q", "HEAD~1")                # auto-update 가 하는 그 이동
    times, err = bh.read_deploys(tmp_path)
    assert err == "" and len(times) == 3, (times, err)
    assert all(t.tzinfo is not None for t in times)
    assert max(times) >= before, times
    # 저장소가 아니면 판정 불가(None) — 빈 목록('배포 없음')으로 접지 않는다(#82)
    t2, e2 = bh.read_deploys(tmp_path / "nope")
    assert t2 is None and e2, (t2, e2)


def test_read_deploys_never_raises():
    def boom(*a, **k):
        raise FileNotFoundError("git")
    t, e = bh.read_deploys(_REPO, run=boom)
    assert t is None and "FileNotFoundError" in e


def test_collect_wires_the_deploy_record():
    seen = []

    def deploys_fn():
        seen.append(1)
        return [datetime(2026, 9, 25, tzinfo=_UTC)], ""
    f = bh.collect("x", now=NOW, env={"token": "", "dest": _DEST, "src": {}, "err": "",
                                      "inbox": ""},
                   facts_fn=lambda: {"ok": True, "s_MainPID": "4242"},
                   read=lambda units, since: ([_start(), _poll("2026-09-25T08:29:50")], "", ""),
                   start_fn=lambda pid: (None, "없음"), tg_fn=lambda *a: {"token": False},
                   procs_fn=lambda own: [], deploys_fn=deploys_fn)
    assert seen == [1] and f["deploys"] == {"times": [datetime(2026, 9, 25, tzinfo=_UTC)],
                                            "err": ""}
