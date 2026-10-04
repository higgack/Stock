"""동기화 실행 예산 — FloodWait 대기가 유닛 타임아웃을 넘으면 systemd 가 **알림 없이**
죽인다(#12). 스크립트가 예산(`TRADE_SYNC_DEADLINE_S`)을 알고 그 전에 알림과 함께
끝나는지, 그리고 유닛의 예산이 타임아웃보다 작은지(두 값이 갈리지 않는지, #38)를 잰다.
"""
import ast
import re
from pathlib import Path

import pytest

from trade import tg_entities as tg

_REPO = Path(__file__).resolve().parents[2]


def test_no_budget_means_no_abort(monkeypatch):
    """예산이 없으면(사람이 여는 넓은 백필) 어떤 대기도 막지 않는다."""
    monkeypatch.delenv(tg.SYNC_DEADLINE_ENV, raising=False)
    assert tg.flood_wait_overruns(10_000, started=0.0, now=599.0) is None
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, bad)
        assert tg.sync_deadline_s() is None, bad


def test_budget_boundary_includes_the_margin(monkeypatch):
    """남은 예산 = 예산 − 경과. 대기 + 여유(알림·기록 몫)가 남은 예산을 넘으면 중단.
    여유는 **리터럴**(30초)로 못박는다 — 상수를 그대로 읽어 경계를 만들면 여유를 바꾸는
    변형이 통과한다(독립 리뷰 D11, #66)."""
    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    # 경과 400 → 남은 140: 대기 110 + 여유 30 = 140 은 들어간다, 111 은 넘는다.
    assert tg.flood_wait_overruns(110, started=0.0, now=400.0) is None
    why = tg.flood_wait_overruns(111, started=0.0, now=400.0)
    assert why and "남은 140s" in why and "예산 540s" in why and tg.SYNC_DEADLINE_ENV in why
    # 예산을 이미 넘긴 뒤의 대기는 음수가 아니라 '남은 0s' 로 적는다(독립 리뷰 D10).
    assert "남은 0s" in tg.flood_wait_overruns(10, started=0.0, now=900.0)
    # 인자로 준 예산이 환경보다 먼저다.
    assert tg.flood_wait_overruns(50, started=0.0, now=0.0, deadline_s=60) is not None


def _directive(txt, key):
    m = re.search(rf"^{re.escape(key)}=(.+)$", txt, re.M)
    return m.group(1).strip() if m else None


def _names_flood_wait(t) -> bool:
    if isinstance(t, ast.Tuple):
        return any(_names_flood_wait(e) for e in t.elts)
    return ((isinstance(t, ast.Name) and t.id == "FloodWaitError")
            or (isinstance(t, ast.Attribute) and t.attr == "FloodWaitError"))


def waits_on_flood(src: str) -> bool:
    """`except FloodWaitError` 중 **끝나지 않는**(본문 마지막이 return·raise 가 아닌)
    핸들러가 있나 = FloodWait 을 기다렸다 다시 하는 스크립트. 바로 끝나는 핸들러
    (`return 0` — 다음 폴링이 다시 한다)는 대상이 아니다: 기다리지 않으니 예산이
    필요 없다."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ExceptHandler) and node.type is not None \
                and _names_flood_wait(node.type):
            last = node.body[-1] if node.body else None
            if not isinstance(last, (ast.Return, ast.Raise)):
                return True
    return False


def _called(src: str) -> set:
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            f = node.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def _exec_script(exec_start: str, repo: Path):
    """ExecStart 가 도는 파이썬 스크립트 파일 — `… python path.py` 또는 `… -m a.b`."""
    toks = exec_start.split()
    for i, tok in enumerate(toks):
        if tok == "-m" and i + 1 < len(toks):
            f = repo / (toks[i + 1].replace(".", "/") + ".py")
            return f if f.is_file() else None
        if tok.endswith(".py"):
            f = repo / tok
            return f if f.is_file() else None
    return None


_BUDGET_CALLS = ("flood_wait_overruns", "run_budget_spent", "cap_flood_sleep")


def sync_budget_violations(repo: Path) -> tuple:
    """(위반 목록, 대상 스크립트). 대상은 **구조에서 파생**한다(#24 · 독립 리뷰 M3):
    `Type=oneshot` 유닛이 도는 스크립트 중 FloodWait 을 **기다렸다 다시 하는** 것.
    옛 판은 '`flood_wait_overruns(` 를 부르는 스크립트' 에서 골라 순환이었다 — 예산
    호출을 빠뜨린 새 동기화는 대상에서 빠져, 이 가드가 막으려던 바로 그 누락을 통과
    시켰다. 대상은 예산 판정 셋(대기·유닛 루프·telethon 자체 대기)을 다 부르고, 그
    유닛은 예산 변수를 `TimeoutStartSec` 보다 여유(30초) 이상 작게 넘겨야 한다.
    반대 증거: 예산을 단 유닛은 전부 타임아웃이 있고 그보다 작다."""
    bad, targets = [], set()
    for unit in sorted((repo / "deploy").glob("*.service")):
        txt = unit.read_text(encoding="utf-8")
        budget = re.search(rf"^Environment={tg.SYNC_DEADLINE_ENV}=(\d+)$", txt, re.M)
        timeout = _directive(txt, "TimeoutStartSec")
        if budget and not (timeout and timeout.isdigit()
                           and int(timeout) - int(budget.group(1)) >= 30):
            bad.append(f"{unit.name}: 예산 {budget.group(1)} 이 타임아웃 {timeout} 보다 "
                       "여유 30초 이상 작지 않다")
        if _directive(txt, "Type") != "oneshot":
            continue
        script = _exec_script(_directive(txt, "ExecStart") or "", repo)
        if script is None:
            continue
        src = script.read_text(encoding="utf-8")
        if not waits_on_flood(src):
            continue
        rel = script.relative_to(repo).as_posix()
        targets.add(rel)
        missing = [c for c in _BUDGET_CALLS if c not in _called(src)]
        if missing:
            bad.append(f"{rel}: FloodWait 을 기다리는데 {', '.join(missing)} 를 안 부른다")
        if not budget:
            bad.append(f"{unit.name}: {rel} 을 도는데 {tg.SYNC_DEADLINE_ENV} 가 없다")
    return bad, targets


def test_every_flood_waiting_sync_unit_carries_a_budget_below_its_timeout():
    """레포 전수 — 위반 0 · 대상은 나쁜양파·BeOn 둘 이상(줄면 가드가 눈먼다)."""
    bad, targets = sync_budget_violations(_REPO)
    assert bad == [], bad
    assert {"trade/scripts/backfill_badonion.py",
            "trade/scripts/backfill_beon.py"} <= targets, targets


def test_the_budget_guard_finds_a_new_sync_that_forgot_its_budget(tmp_path):
    """발화 — 예산 호출도 예산 변수도 없이 FloodWait 을 기다리는 새 동기화(리뷰 M3
    재현: 옛 판은 이걸 대상에서 빼 `1 passed` 였다). 반대 증거 셋: 바로 끝나는 핸들러
    (`return 0`)는 대상이 아니다 · 예산을 다 갖춘 동기화는 통과한다 · oneshot 이 아닌
    유닛(상시 리스너)은 대상이 아니다."""
    (tmp_path / "deploy").mkdir()
    (tmp_path / "trade" / "scripts").mkdir(parents=True)

    def unit(name, script, *, typ="oneshot", budget=None):
        env = f"Environment={tg.SYNC_DEADLINE_ENV}={budget}\n" if budget else ""
        (tmp_path / "deploy" / f"{name}.service").write_text(
            f"[Service]\nType={typ}\nExecStart=/x/python -m trade.scripts.{script}\n"
            f"{env}TimeoutStartSec=600\n", encoding="utf-8")

    def script(name, body):
        (tmp_path / "trade" / "scripts" / f"{name}.py").write_text(body, encoding="utf-8")

    script("zz_new_sync", "async def f(c):\n    try:\n        await c.go()\n"
           "    except FloodWaitError as e:\n        delay = e.seconds\n")
    unit("zz-new-sync", "zz_new_sync")
    script("zz_poll", "async def f(c):\n    try:\n        await c.go()\n"
           "    except FloodWaitError as e:\n        log(e.seconds)\n        return 0\n")
    unit("zz-poll", "zz_poll")
    script("zz_ok", "async def f(c):\n    run_budget_spent(started=0)\n"
           "    cap_flood_sleep(c, started=0)\n    try:\n        await c.go()\n"
           "    except (ValueError, FloodWaitError) as e:\n"
           "        flood_wait_overruns(e.seconds, started=0)\n")
    unit("zz-ok", "zz_ok", budget=540)
    script("zz_listener", "async def f(c):\n    try:\n        await c.go()\n"
           "    except errors.FloodWaitError as e:\n        delay = e.seconds\n")
    unit("zz-listener", "zz_listener", typ="simple")
    bad, targets = sync_budget_violations(tmp_path)
    assert targets == {"trade/scripts/zz_new_sync.py", "trade/scripts/zz_ok.py"}, targets
    assert any("zz_new_sync.py" in b and "flood_wait_overruns" in b for b in bad), bad
    assert any("zz-new-sync.service" in b and tg.SYNC_DEADLINE_ENV in b for b in bad), bad
    assert not any("zz_ok" in b or "zz-ok" in b or "zz_poll" in b for b in bad), bad


def test_run_budget_spent_boundary_is_the_margin(monkeypatch):
    """다음 유닛을 시작해도 되는 경계 = 남은 예산 ≥ 여유(30초 — 리터럴로 못박는다: 상수를
    그대로 읽으면 여유를 바꾸는 변형이 통과한다, #66). 사유는 FloodWait 이라 하지 않는다."""
    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    assert tg.SYNC_DEADLINE_MARGIN_S == 30
    assert tg.run_budget_spent(started=0.0, now=510.0) is None          # 남은 30
    why = tg.run_budget_spent(started=0.0, now=511.0)                  # 남은 29
    assert why and "실행 예산 소진" in why and "남은 29s" in why and "FloodWait" not in why
    assert "남은 0s" in tg.run_budget_spent(started=0.0, now=900.0)    # 음수는 0 으로
    monkeypatch.delenv(tg.SYNC_DEADLINE_ENV)
    assert tg.run_budget_spent(started=0.0, now=10_000.0) is None       # 예산 없음


def test_cap_flood_sleep_fits_every_retry_into_the_budget(monkeypatch):
    """(재시도 + 2) × 임계 ≤ 남은 예산 − 여유, 기본값(60초)보다 올리지 않는다, 예산이
    없으면 손대지 않는다, 재시도를 못 읽거나 무한이면 0(모든 FloodWait 이 호출부로)."""
    class _C:
        flood_sleep_threshold = 60
        _request_retries = 5

    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    c = _C()
    assert tg.cap_flood_sleep(c, started=0.0, now=0.0) == 60 == c.flood_sleep_threshold
    assert tg.cap_flood_sleep(c, started=0.0, now=400.0) == 15            # (140-30)/7
    assert tg.cap_flood_sleep(c, started=0.0, now=505.0) == 0             # (35-30)/7 < 1
    assert tg.cap_flood_sleep(c, started=0.0, now=900.0) == 0
    c._request_retries = 1
    assert tg.cap_flood_sleep(c, started=0.0, now=400.0) == 36            # (140-30)/3
    for bad in (None, -1, True, "5"):
        c._request_retries = bad
        assert tg.cap_flood_sleep(c, started=0.0, now=0.0) == 0, bad
    del _C._request_retries
    assert tg.cap_flood_sleep(_C(), started=0.0, now=400.0) == 15         # 기본 5 로 본다
    monkeypatch.delenv(tg.SYNC_DEADLINE_ENV)
    c2 = _C()
    assert tg.cap_flood_sleep(c2, started=0.0, now=500.0) is None
    assert c2.flood_sleep_threshold == 60                                 # 손대지 않았다


telethon = pytest.importorskip("telethon")


def test_beon_forward_aborts_before_a_wait_that_would_outlive_the_unit(monkeypatch):
    """BeOn 동기화도 같은 규약 — 대기가 예산을 넘으면 기다리지 않고 중단한다.
    반대 증거: 예산이 넉넉하면 기다렸다가 다시 보낸다."""
    import asyncio
    import os
    # 모듈이 import 시점에 읽는 값 — 이미 있으면 그대로 두고, 없을 때만 이 테스트 동안 넣는다
    # (`os.environ.setdefault` 는 세션 끝까지 남아 뒤 테스트로 샌다).
    for k, v in (("TRADE_TELETHON_API_ID", "0"), ("TRADE_TELETHON_API_HASH", "stub"),
                 ("TRADE_CHANNEL_CHAT_IDS", "-1000000000000")):
        if k not in os.environ:
            monkeypatch.setenv(k, v)
    from telethon.errors import FloodWaitError
    from trade.scripts import backfill_beon as bb

    slept: list = []

    async def _sleep(s):
        slept.append(s)

    class _Client:
        def __init__(self):
            self.calls = 0

        async def forward_messages(self, dest, ids, from_peer=None):
            self.calls += 1
            if self.calls == 1:
                raise FloodWaitError(request=None, capture=100)

    class _M:
        id = 7

    monkeypatch.setattr(bb.asyncio, "sleep", _sleep)
    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    monkeypatch.setattr(bb, "_RUN_T0", bb.time.monotonic() - 500)   # 남은 40s
    with pytest.raises(bb.BackfillAborted) as ex:
        asyncio.run(bb._forward_unit(_Client(), None, [_M()], None))
    assert "실행 예산" in str(ex.value) and slept == []

    monkeypatch.setattr(bb, "_RUN_T0", bb.time.monotonic())          # 남은 540s
    c = _Client()
    assert asyncio.run(bb._forward_unit(c, None, [_M()], None)) is True
    assert slept == [101] and c.calls == 2


@pytest.fixture
def beon(monkeypatch, tmp_path):
    """BeOn 동기화 `run()` 을 가짜 클라이언트·가짜 시계로 태운다 — 형제 나쁜양파의
    `backfill` 픽스처와 같은 축(#38). 시계는 `tg_entities` 의 단조 시계와 스크립트의
    `asyncio.sleep` 을 **한** 가짜로 묶는다(대기·페이스가 시계를 민다, #128)."""
    import asyncio
    import os
    import types
    from datetime import datetime, timedelta, timezone
    for k, v in (("TRADE_TELETHON_API_ID", "0"), ("TRADE_TELETHON_API_HASH", "stub"),
                 ("TRADE_CHANNEL_CHAT_IDS", "-1000000000000")):
        if k not in os.environ:
            monkeypatch.setenv(k, v)
    from trade.scripts import backfill_beon as bb

    clock = {"t": 0.0}
    monkeypatch.setattr(tg, "time", types.SimpleNamespace(monotonic=lambda: clock["t"]))

    async def _sleep(s):
        clock["t"] += s

    async def _nodisk(*a, **k):
        return None

    notes: list = []
    monkeypatch.setattr(bb.asyncio, "sleep", _sleep)
    monkeypatch.setattr(bb, "_RUN_T0", 0.0)
    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    monkeypatch.setattr(bb, "_current_pause", lambda n: 1.5)
    monkeypatch.setattr(bb, "_maybe_pause_for_disk", _nodisk)
    monkeypatch.setattr(bb, "_notify", lambda text, buttons=None: notes.append(text))
    monkeypatch.setattr(bb, "SESSION_PATH", str(tmp_path / ".backfill-session"))
    monkeypatch.setattr(bb, "INBOX_PATH", tmp_path / "inbox.jsonl")
    monkeypatch.setattr(bb._beon_skip, "contains", lambda mid: False)
    monkeypatch.setattr(bb, "_tutils", types.SimpleNamespace(get_peer_id=lambda p: p.id))

    class _Peer:
        def __init__(self, pid):
            self.id = pid

    now = datetime.now(timezone.utc)

    class _Msg:
        def __init__(self, mid):
            self.id, self.text, self.fwd_from, self.grouped_id = mid, "", None, None
            self.date = now - timedelta(hours=1) + timedelta(seconds=mid)

    class _Client:
        msgs: list = []
        flood_at: dict = {}            # 몇 번째 포워드에서 몇 초 FloodWait 인가
        scan_flood = 0
        scan_step = 0.0                # 훑기에서 글 하나마다 흐르는 시간(초)
        forwarded: list = []
        seen: list = []
        scan_seen: list = []           # 훑기 중 (시각, telethon 자동 대기 상한)

        def __init__(self, session, api_id, api_hash):
            self.calls = 0

        async def start(self):
            return None

        async def disconnect(self):
            return None

        async def get_input_entity(self, ref):
            return _Peer(-100111 if ref == bb.SOURCE_USERNAME else -100222)

        async def iter_messages(self, source, offset_date=None, reverse=False):
            if _Client.scan_flood:
                raise bb.FloodWaitError(request=None, capture=_Client.scan_flood)
            for m in _Client.msgs:
                _Client.scan_seen.append((clock["t"], getattr(self, "flood_sleep_threshold", None)))
                clock["t"] += _Client.scan_step
                yield m

        async def forward_messages(self, dest, ids, from_peer=None):
            self.calls += 1
            if self.calls in _Client.flood_at:
                raise bb.FloodWaitError(request=None, capture=_Client.flood_at[self.calls])
            _Client.seen.append((clock["t"], getattr(self, "flood_sleep_threshold", None)))
            _Client.forwarded.append(list(ids))

    for k in ("forwarded", "seen", "scan_seen"):
        monkeypatch.setattr(_Client, k, [])
    monkeypatch.setattr(bb, "TelegramClient", _Client)
    since = now - timedelta(days=2)

    def run(n=80, *, dry=False):
        _Client.msgs = [_Msg(1000 + k) for k in range(n)]
        return asyncio.run(bb.run(since, None, dry, 5000))

    return types.SimpleNamespace(bb=bb, clock=clock, notes=notes, client=_Client, run=run)


def test_beon_forward_loop_rechecks_the_budget_after_an_allowed_wait(beon):
    """독립 리뷰 M1a 의 BeOn 판 — 대기를 허용한 뒤에도 매 유닛 앞에서 예산을 다시 본다.
    옛 판은 FloodWait 이 났을 때만 판정해, 재개 뒤 남은 유닛이 600초를 넘겼다."""
    beon.clock["t"] = 100.0
    beon.client.flood_at = {1: 400}
    assert beon.run(80) == 1
    times = [t for t, _ in beon.client.seen]
    assert times and times[0] == 501.0, times            # 대기는 허용됐다
    assert max(times) <= 540 - tg.SYNC_DEADLINE_MARGIN_S, max(times)
    note = beon.notes[-1]
    assert note.splitlines()[0] == "⏸ <b>BeOn 동기화 — 실행 예산 소진</b>", note
    assert f"진행: {len(times)}/80 msgs" in note


def test_beon_caps_telethons_own_flood_sleep_to_the_budget(beon):
    """독립 리뷰 M1b 의 BeOn 판 — telethon 이 혼자 자는 대기도 남은 예산 안으로."""
    beon.client.flood_at = {}
    beon.run(80)
    assert beon.client.seen
    for t, thr in beon.client.seen:
        assert thr is not None and (5 + 2) * thr <= (540 - t) - tg.SYNC_DEADLINE_MARGIN_S, (t, thr)


def test_beon_scan_flood_wait_aborts_with_a_note_and_dry_run_stays_quiet(beon):
    """훑기의 긴 FloodWait — 옛 판은 트레이스백으로 죽었다(알림 없음). 알림과 rc 1 로
    끝내고, dry-run(사람이 돌린 진단)은 알리지 않는다."""
    beon.client.scan_flood = 3600
    assert beon.run(5) == 1
    assert beon.notes and "훑기" in beon.notes[-1] and "FloodWait 3600s" in beon.notes[-1]
    beon.notes.clear()
    assert beon.run(5, dry=True) == 1
    assert beon.notes == []


def test_beon_scan_stops_when_the_budget_runs_out(beon):
    """훑기도 실행 예산 안에서 — 글마다 남은 예산을 본다. 옛 판은 훑기에서 예산을 안 봐
    느린 훑기(짧은 FloodWait 이 쌓이는 경우)가 600초를 넘기면 systemd 가 알림 없이 죽였다
    (#432 리뷰 M1 후속). 글 하나에 10초 → 520초째에 남은 20초 < 여유 30초라 멈춘다."""
    beon.client.scan_step = 10.0
    assert beon.run(80) == 1
    assert beon.clock["t"] <= 540
    note = beon.notes[-1]
    assert note.splitlines()[0] == "⏸ <b>BeOn 동기화 — 실행 예산 소진</b>", note
    assert "훑기 중" in note and "FloodWait" not in note, note
    assert beon.client.forwarded == []


def test_beon_scan_keeps_telethons_own_flood_sleep_inside_the_budget(beon):
    """훑기의 telethon 자동 대기 상한 — 첫 페이지 요청 **전에** 맞추고(그 요청은 첫 글보다
    먼저 나간다) 글마다 다시 맞춘다. 300초에 시작하면 첫 글의 상한은 기본 60 이 아니라
    (540−300−30)/7 = 30 이고, 시간이 흐를수록 준다."""
    beon.clock["t"] = 300.0
    beon.client.scan_step = 2.0
    beon.run(80)
    seen = beon.client.scan_seen
    assert len(seen) == 80
    for t, thr in seen:
        assert thr is not None and (5 + 2) * thr <= (540 - t) - tg.SYNC_DEADLINE_MARGIN_S, (t, thr)
    assert seen[0][1] == int((540 - 300 - tg.SYNC_DEADLINE_MARGIN_S) / 7)
    assert seen[-1][1] < seen[0][1]


def test_cap_flood_sleep_against_the_installed_telethon():
    """가정을 설치본으로 잰다(#25) — 실물 클라이언트의 기본 임계가 60·재시도 5 이고,
    `cap_flood_sleep` 이 쓴 값을 telethon 이 읽는 그 속성(`flood_sleep_threshold`)이
    돌려준다. 판이 바뀌어 이름이 달라지면 여기서 걸린다."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    c = TelegramClient(StringSession(), 1, "x")
    assert c.flood_sleep_threshold == tg.TELETHON_FLOOD_SLEEP_S
    assert c._request_retries == 5
    assert tg.cap_flood_sleep(c, started=0.0, now=400.0, deadline_s=540) == 15
    assert c.flood_sleep_threshold == 15
