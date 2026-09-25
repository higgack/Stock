"""나쁜양파 리스너 — 재게시 글은 큐에 넣기 **전에** 보증한다 (실수 #411, 2026-09-25).

나쁜양파가 다른 채널에서 퍼 온 글은 텔레그램이 원래 출처를 달아 보내 봇의 출처 게이트가
버렸다(27건). 리스너는 실시간 경로라 여기서 보증이 빠지면 새 재게시 글이 전부 다시
버려진다 — 그래서 리스너를 **실제 이벤트로** 태운다: 가짜 클라이언트가 핸들러를 받아
두었다가 글을 흘리고, 포워드가 불리는 순간 보증 기록에 그 글이 있는지 잰다(#20 헬퍼만
재면 배선을 떼는 변형이 통과한다). 스크립트는 sys.modules 에 등록하지 않는 사설 모듈로
읽는다(#397).
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import logging
import re
import sys
import types
from pathlib import Path

import pytest

from trade import relay_origins as ro
from trade import tg_entities as _tg  # noqa: F401 — 가짜 telethon 을 꽂기 전에 진짜로 올린다

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "listen_badonion.py"
_ROOT = Path(__file__).resolve().parents[2]
_REPOST = -1009990000001        # 합성 — 운영 재게시 채널 ID 를 쓰지 않는다(#393)
_KRI = ("**🇰🇷 8월 수입 한국**\n\n**▶️ 텔레칩스 — 차량용 AP·프로세서**\n\n"
        "**26년08월: $2,175.2M  (+49.4% YoY)  (+7.3% MoM)**")
_CHATTER = "AMD 신고가 돌파"


def _msg(mid, text="", *, post=None, grouped_id=None, user_origin=False):
    fwd = None
    if post is not None:
        fwd = types.SimpleNamespace(
            from_id=types.SimpleNamespace(channel_id=abs(_REPOST) - 10 ** 12, id=_REPOST),
            channel_post=post)
    elif user_origin:
        fwd = types.SimpleNamespace(from_id=types.SimpleNamespace(user_id=5, id=5),
                                    channel_post=None)
    return types.SimpleNamespace(id=mid, text=text, grouped_id=grouped_id, fwd_from=fwd)


class _Client:
    """리스너가 쓰는 표면만 — 핸들러를 받아 두고 `run_until_disconnected` 에서 글을 흘린다."""
    handler = None
    events: list = []
    forwarded: list = []
    at_forward: list = []
    vouch_path: Path | None = None

    def __init__(self, *a, **k):
        pass

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_input_entity(self, ref):
        return types.SimpleNamespace(id=-100111 if ref == "Badonions" else -100222)

    def on(self, _event):
        def deco(fn):
            _Client.handler = fn
            return fn
        return deco

    async def forward_messages(self, dest, ids, from_peer=None):
        _Client.at_forward.append((list(ids), set(ro.load(_Client.vouch_path)[0])))
        _Client.forwarded.append(list(ids))

    async def run_until_disconnected(self):
        for m in _Client.events:
            await _Client.handler(types.SimpleNamespace(message=m))
        for _ in range(20):                   # 앨범 debounce(0초)·직렬 워커(간격 0초)가 돌 틈
            await asyncio.sleep(0.01)

    async def disconnect(self):
        return None


def _fake_telethon() -> dict:
    tl = types.ModuleType("telethon")
    tl.TelegramClient = _Client
    events = types.ModuleType("telethon.events")
    events.NewMessage = lambda **k: ("NewMessage", k)
    tl.events = events
    utils = types.ModuleType("telethon.utils")
    utils.get_peer_id = lambda p: getattr(p, "id", 0)
    tl.utils = utils
    errors = types.ModuleType("telethon.errors")
    for n in ("AuthKeyError", "FloodWaitError", "SessionPasswordNeededError"):
        setattr(errors, n, type(n, (Exception,), {}))
    return {"telethon": tl, "telethon.events": events, "telethon.utils": utils,
            "telethon.errors": errors}


@pytest.fixture
def listener(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_TELETHON_API_ID", "1")
    monkeypatch.setenv("TRADE_TELETHON_API_HASH", "stub")
    monkeypatch.setenv("TRADE_CHANNEL_CHAT_IDS", "-100222")
    monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TRADE_BOT_TOKEN", raising=False)
    for name, mod in _fake_telethon().items():
        monkeypatch.setitem(sys.modules, name, mod)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("_listen_badonion_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                       # sys.modules 에 등록하지 않는다
    notes: list = []
    monkeypatch.setattr(mod, "_notify", notes.append)
    monkeypatch.setattr(mod, "ALBUM_DEBOUNCE_S", 0.0)
    monkeypatch.setattr(mod, "PACE_S", 0.0)
    # 세션은 cwd 상대 경로다 — 레포 루트의 파일을 재지 않게(#30)
    monkeypatch.setattr(mod, "SESSION_PATH", str(tmp_path / ".badonion-listener-session"))
    for attr, val in (("handler", None), ("events", []), ("forwarded", []),
                      ("at_forward", []), ("vouch_path", ro.path_in(tmp_path))):
        monkeypatch.setattr(_Client, attr, val)
    mod._test_notes = notes
    return mod


def _run(mod, events) -> int:
    _Client.events = events
    return asyncio.run(mod._run_listener())


def test_a_single_repost_is_vouched_before_its_forward(listener):
    assert _run(listener, [_msg(501, _KRI, post=4242)]) == 0
    assert _Client.at_forward == [([501], {(_REPOST, 4242)})], _Client.at_forward
    assert ro.load(_Client.vouch_path)[0][(_REPOST, 4242)]["by"] == "listener"


def test_an_album_vouches_every_repost_member_before_the_one_forward(listener):
    """캡션은 한 멤버에만 있다 — 사진 멤버도 각자 원래 글번호로 오고 봇은 한 장씩 받는다."""
    events = [_msg(501, _KRI, post=10, grouped_id=7), _msg(502, "", post=11, grouped_id=7)]
    assert _run(listener, events) == 0
    assert _Client.at_forward == [([501, 502], {(_REPOST, 10), (_REPOST, 11)})], \
        _Client.at_forward


def test_a_native_post_is_forwarded_without_a_vouch(listener):
    assert _run(listener, [_msg(501, _KRI)]) == 0
    assert _Client.forwarded == [[501]]
    assert not _Client.vouch_path.exists()


def test_an_irrelevant_repost_is_neither_vouched_nor_forwarded(listener):
    """보증은 관련성 필터를 통과한 글만 — 무관한 재게시까지 보증하면 봇이 그 글을 받을 길을
    여는 셈이다(릴레이가 포워드하지 않으니 해는 없지만 기록이 소음이 된다)."""
    assert _run(listener, [_msg(501, _CHATTER, post=4242),
                           _msg(502, "", post=4243, grouped_id=9)]) == 0
    assert _Client.forwarded == [] and not _Client.vouch_path.exists()


def test_a_repost_that_cannot_be_vouched_is_not_queued_and_is_alerted(
        listener, monkeypatch, caplog):
    """보증을 못 쓰면 큐에 넣지 않는다(포워드하면 봇이 버린다) — 조용하지 않게 알리고,
    같은 흐름의 다른 글은 그대로 간다. 주기 sync 가 다시 보증해 포워드한다. 알림은 **같은
    사유면 프로세스당 한 번**이다 — 재게시 글마다 알리면 같은 장애가 알림 폭탄이 되고 그때마다
    알림(curl)이 이벤트 루프를 세운다(독립 리뷰 #411 L7). 사유가 바뀌면 다시 알리고, 건마다의
    실패는 로그에 남는다."""
    calls: list = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("쓰기 권한 없음(테스트)")
        raise OSError("디스크 가득(테스트)")
    monkeypatch.setattr(ro, "vouch", boom)
    caplog.set_level(logging.INFO)
    events = [_msg(501, _KRI, post=4242), _msg(502, _KRI),
              _msg(503, _KRI, post=20, grouped_id=8), _msg(504, "", post=21, grouped_id=8),
              _msg(505, _KRI, post=4250)]
    assert _run(listener, events) == 0
    assert _Client.forwarded == [[502]], _Client.forwarded
    alerts = [n for n in listener._test_notes if "재게시 출처 보증 실패" in n]
    assert len(alerts) == 2, listener._test_notes                  # 사유마다 한 번
    assert "쓰기 권한 없음(테스트)" in alerts[0] and "디스크 가득(테스트)" in alerts[1], alerts
    assert "msg=501: 재게시 출처 보증 실패" in caplog.text
    assert "album gid=8: 재게시 출처 보증 실패" in caplog.text     # 두 번째는 로그로만
    assert "msg=505: 재게시 출처 보증 실패" in caplog.text


def test_an_unvouchable_forward_is_forwarded_as_before_and_counted(listener, caplog):
    """원래 출처가 개인 계정이면 보증할 수 없다 — 막지 않고(옛 동작) 봇이 버린다고 적는다."""
    caplog.set_level(logging.INFO)
    assert _run(listener, [_msg(501, _KRI, user_origin=True)]) == 0
    assert _Client.forwarded == [[501]]
    assert not _Client.vouch_path.exists()
    assert "보증할 수 없다" in caplog.text


# ── 배포: 리스너는 자기가 import 하는 모듈이 바뀌어도 재시작한다 ──────────────
def _listener_restart_regex() -> re.Pattern:
    """`deploy/trade-auto-update.sh` 가 나쁜양파 리스너를 재시작하는 조건(grep -E)."""
    sh = (_ROOT / "deploy" / "trade-auto-update.sh").read_text(encoding="utf-8")
    m = re.search(r"""^BADONION_LISTENER_RELEVANT=\$\(echo "\$CHANGED_FILES" \| grep -E '([^']+)' """
                  r"""\|\| true\)""", sh, re.M)
    assert m, "재시작 조건 줄의 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    return re.compile(m.group(1))


def _import_closure(start: Path) -> tuple[set[str], set[str]]:
    """`start` 가 (함수 안의 늦은 import 까지) 닿는 trade.* 모듈 파일 · bot.* 모듈 파일.
    소스를 AST 로 훑는다 — 이름을 적어 두면 새 파서를 더할 때 빠진다(#24). 모듈을 import 하면
    그 **위 패키지들의 `__init__.py`** 도 실행되므로 같이 센다 — 진입점이 `-m
    trade.scripts.listen_badonion` 이라 `trade/scripts/__init__.py` 는 어느 import 문에도 안
    나오는데도 돈다(독립 리뷰 #411 L9)."""
    def path_of(mod: str) -> Path | None:
        f = _ROOT.joinpath(*mod.split(".")).with_suffix(".py")
        if f.is_file():
            return f
        d = _ROOT.joinpath(*mod.split(".")) / "__init__.py"
        return d if d.is_file() else None

    def add(p: Path) -> None:
        rel = p.relative_to(_ROOT).as_posix()
        (bot_mods if rel.startswith("bot/") else trade_mods).add(rel)
        if rel.startswith("trade/"):
            todo.append(p)

    done: set = set()
    todo, trade_mods, bot_mods = [start], set(), set()
    while todo:
        f = todo.pop()
        if f in done:
            continue
        done.add(f)
        pkg = f.relative_to(_ROOT).parent.parts
        for i in range(1, len(pkg) + 1):                   # 위 패키지들의 __init__.py
            init = _ROOT.joinpath(*pkg[:i]) / "__init__.py"
            if init.is_file() and pkg[0] in ("trade", "bot"):
                add(init)
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    up = list(pkg[:len(pkg) - (n.level - 1)])
                    base = ".".join(up + ([base] if base else []))
                mods = [base] + [f"{base}.{a.name}" for a in n.names]
            else:
                continue
            for m in mods:
                top = m.split(".")[0]
                p = path_of(m) if top in ("trade", "bot") else None
                if p is not None:
                    add(p)
    return trade_mods, bot_mods


def test_the_listener_restarts_when_any_module_it_imports_changes():
    """실수 #411 — 옛 규칙은 `listen_badonion.py` 한 파일만 봐서, 리스너가 import 하는 보증
    모듈(`relay_origins`)이나 관련성 필터(`badonion_sources` 와 그 파서들)만 바뀐 배포는
    리스너를 재시작하지 않았다(다른 이유로 재시작되기 전까지 **옛 코드**) — 보증 형식이
    바뀌면 옛 리스너가 옛 형식으로 쓰고 새 봇이 못 읽어 재게시 글을 다시 버린다. 리스너의
    trade.* import 폐포(위 패키지의 `__init__.py` 포함)가 전부 재시작 조건에 걸리는지 소스에서
    잰다(새 파서를 더해도 이름을 적을 필요가 없다)."""
    rx = _listener_restart_regex()
    trade_mods, bot_mods = _import_closure(_SCRIPT)
    # 반대 증거 — 폐포가 눈멀지 않았다(#54): 보증·필터·세션 가드와 파서들이 실제로 잡힌다
    assert {"trade/relay_origins.py", "trade/badonion_sources.py", "trade/tg_entities.py",
            "trade/kr_stock_imports.py", "trade/__init__.py",
            "trade/scripts/__init__.py"} <= trade_mods, sorted(trade_mods)
    assert len(trade_mods) >= 20, sorted(trade_mods)
    missing = sorted(m for m in trade_mods | {"trade/scripts/listen_badonion.py"}
                     if not rx.search(m))
    assert missing == [], f"바뀌어도 리스너가 재시작하지 않는 모듈: {missing}"
    # 폐포 밖의 파일로는 재시작하지 않는다 — 테스트·주기 백필(oneshot)·NOAH 쪽
    for other in ("trade/tests/test_relay_origins.py", "trade/scripts/backfill_badonion.py",
                  "bot/market.py", "docs/tests.md"):
        assert not rx.search(other), other
    # ⚠️ bot.* 의존은 재시작 조건 **밖**이다(못 보는 축, #274) — NOAH 배포마다 텔레톤 리스너를
    # 재시작하지 않으려고다. 지금은 `trade/stock_link.py` 의 링크 렌더가 **함수 안에서** 부르는
    # 하나뿐이고 리스너 경로(관련성 판정·보증)는 그걸 부르지 않는다. 늘면 여기서 다시 물을 것.
    assert bot_mods == {"bot/market.py"}, sorted(bot_mods)
