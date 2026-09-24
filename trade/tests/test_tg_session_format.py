"""텔레그램 세션 **형식** 가드 (실수 #404, 2026-09-24).

VM 실측: 운영 유닛은 `.backfill-venv`(telethon 1.36.0 고정 = 세션 DB v7)로
도는데, 상한 없는 다른 venv(`~/stock-trade/.venv`, 최신 telethon = v8)로
트레이드 스크립트를 돌리게 한 안내 탓에 `.badonion-session` 이 v8 로 올라갔다.
그 뒤 1.36.0 은 그 파일을 **생성자**에서 `too many values to unpack (expected 5)`
로 못 열었고, 생성이 알림 경로 밖이라 6시간 동기화가 조용히 죽었다(#12).

여기서 재는 것: (1) 형식을 **실측**으로 판정한다(예외 문구를 읽지 않는다, #65)
(2) 못 여는 형식이면 무엇을 깔아야 하는지 말한다 (3) 운영 세션을 새 형식으로
올리는 것은 운영 venv 가 고정판일 때만 허용한다 — 운영 venv 가 아니면 판이
무엇이든 막는다(4차 리뷰 M1: 판으로 가르면 다른 venv 가 마침 고정판과 같은 판일
때 뚫린다) (4) 모든 생성 지점이 가드를 거친다(디렉터리 전수 · 별칭·partial·재바인딩
우회까지, #24) (5) 리스너는
형식 오류면 EX_CONFIG(78)로 끝나 재시작 루프를 막고 알린다 (6) 형제 백필도 생성
실패를 알린다.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import re
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from trade import tg_entities as tg
# ⚠️ env 를 바꾸기 **전에** 올린다 — 이 모듈들은 import 시점에 경로를 굳힌다
# (형제 test_backfill_badonion_sync 의 독립 리뷰 L4 와 같은 이유).
from trade import badonion_sources as _srcs  # noqa: F401
from trade import ignored as _ignored  # noqa: F401
from trade import listener_health as _lh  # noqa: F401

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "trade" / "scripts"

# telethon 1.36.0 의 세션 스키마(DB v7 · sessions 5열) — 운영 고정판이었던 판을
# 원본 소스에서 옮겼다(`Telethon-1.36.0/telethon/sessions/sqlite.py`).
_V7_SCHEMA = """
create table version (version integer primary key);
insert into version values (7);
create table sessions (dc_id integer primary key, server_address text,
                       port integer, auth_key blob, takeout_id integer);
create table entities (id integer primary key, hash integer not null,
                       username text, phone integer, name text, date integer);
create table sent_files (md5_digest blob, file_size integer, type integer,
                         id integer, hash integer,
                         primary key(md5_digest, file_size, type));
create table update_state (id integer primary key, pts integer, qts integer,
                           date integer, seq integer);
"""


def _session(tmp_path, version: int, name: str = "live") -> Path:
    p = tmp_path / f"{name}.session"
    con = sqlite3.connect(p)
    con.executescript(_V7_SCHEMA.replace("values (7)", f"values ({version})"))
    con.commit()
    con.close()
    return p


def _pin(monkeypatch, pin):
    monkeypatch.setattr(tg, "pinned_telethon", lambda path=None: pin)


def _lib(monkeypatch, ver, db):
    monkeypatch.setattr(tg, "telethon_db_version", lambda: (ver, db))


# VM 의 두 인터프리터 — 운영 유닛의 venv 와, 사고를 낸 NOAH `.venv`.
_PROD_EXE = f"/home/higgack/stock-trade/{tg.PROD_VENV}/bin/python"
_OTHER_EXE = "/home/higgack/stock/.venv/bin/python"


def _exe(monkeypatch, exe):
    monkeypatch.setattr(tg.sys, "executable", exe)


# ── 실측 함수 ────────────────────────────────────────────────────────────
def test_the_session_version_is_read_read_only(tmp_path):
    """형식을 재는 쪽이 운영 파일을 바꾸거나 잠그면 안 된다(#264)."""
    p = _session(tmp_path, 8)
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    assert tg.session_db_version(p) == 8
    assert tg.session_db_version(str(p)[:-len(".session")]) == 8   # telethon 규약
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before
    assert sorted(x.name for x in tmp_path.iterdir()) == [p.name]  # -journal 없음


def test_a_missing_or_foreign_file_is_not_a_version(tmp_path):
    assert tg.session_db_version(tmp_path / "nope") is None
    junk = tmp_path / "junk.session"
    junk.write_bytes(b"not a sqlite file")
    assert tg.session_db_version(junk) is None
    empty = tmp_path / "empty.session"
    sqlite3.connect(empty).close()                     # version 테이블 없음
    assert tg.session_db_version(empty) is None


def test_an_empty_version_table_is_not_a_version(tmp_path):
    """version 테이블은 있는데 행이 없으면 형식을 모르는 것이다 — 0 으로 읽으면
    '아주 옛 형식' 이 되어 승격 판정이 엉뚱하게 돈다(4차 리뷰 T06)."""
    p = tmp_path / "e.session"
    con = sqlite3.connect(p)
    con.execute("create table version (version integer primary key)")
    con.commit()
    con.close()
    assert tg.session_db_version(p) is None
    assert tg._session_db_probe(p) == (None, "version 테이블에 행이 없다")


def test_measuring_never_rolls_back_a_hot_journal(tmp_path):
    """쓰기 모드로 열면 SQLite 가 **핫 저널을 롤백**해 운영 파일을 바꾼다 — 형식을
    재는 쪽은 읽기 전용이어야 한다(#264). 읽기 전용은 롤백하지 못해 못 잰 것으로
    끝난다(4차 리뷰 T05 — 옛 테스트는 ro 와 rw 를 못 갈랐다)."""
    import subprocess
    import textwrap
    p = _session(tmp_path, 7)
    code = textwrap.dedent(f"""
        import os, sqlite3
        con = sqlite3.connect({str(p)!r}, isolation_level=None)
        con.execute("pragma cache_size=1")
        con.execute("begin exclusive")
        con.execute("create table big (x blob)")
        for i in range(200):
            con.execute("insert into big values (randomblob(4000))")
        con.execute("update version set version = 99")
        os._exit(0)                                   # 커밋 없이 죽는다 → 핫 저널
    """)
    subprocess.run([sys.executable, "-c", code], check=True)
    journal = Path(str(p) + "-journal")
    assert journal.exists()
    before = p.read_bytes()
    v, why = tg._session_db_probe(p)
    assert v is None and why                           # 못 잰 것 — 사유가 있다
    assert p.read_bytes() == before and journal.exists()


def test_the_installed_telethon_format_is_measured_not_assumed(monkeypatch):
    fake = types.ModuleType("telethon")
    fake.__version__ = "9.9.9"
    sessions = types.ModuleType("telethon.sessions")
    sq = types.ModuleType("telethon.sessions.sqlite")
    sq.CURRENT_VERSION = 42
    sessions.sqlite = sq
    for name, mod in (("telethon", fake), ("telethon.sessions", sessions),
                      ("telethon.sessions.sqlite", sq)):
        monkeypatch.setitem(sys.modules, name, mod)
    assert tg.telethon_db_version() == ("9.9.9", 42)
    monkeypatch.setitem(sys.modules, "telethon", None)       # 미설치
    assert tg.telethon_db_version() == (None, None)


def test_the_pin_is_read_from_the_file_the_production_venv_installs(tmp_path):
    """고정판의 단일 출처 — 운영 venv 를 까는 그 파일이다(trade/README.md).
    판 리터럴을 박지 않는다 — 핀을 올릴 때마다 무관한 빨간불이 된다(#67)."""
    assert tg._REQUIREMENTS == _SCRIPTS / "requirements.txt"
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", tg.pinned_telethon() or "")
    f = tmp_path / "req.txt"
    f.write_text("# 주석\npython-dotenv==1.0.1\n  Telethon == 1.2.3  \n", encoding="utf-8")
    assert tg.pinned_telethon(f) == "1.2.3"
    f.write_text("telethon>=1.40\n", encoding="utf-8")          # 고정이 아니다
    assert tg.pinned_telethon(f) is None
    assert tg.pinned_telethon(tmp_path / "missing.txt") is None


def test_the_pin_reads_the_session_format_we_found_on_the_vm():
    """1.45.0 은 DB v8 을 읽는다 — VM 의 세션이 v8 이었다. 고정판을 낮추면
    운영이 다시 그 파일을 못 연다(이 판에서만 실측 가능 — 다른 판이 깔렸으면 건너뛴다)."""
    import importlib.metadata as md
    try:
        installed = md.version("telethon")
    except md.PackageNotFoundError:
        pytest.skip("telethon 미설치")
    if installed != tg.pinned_telethon():
        pytest.skip(f"설치판 {installed} ≠ 고정판 — 고정판 형식을 여기서 못 잰다")
    sq = pytest.importorskip("telethon.sessions.sqlite")
    assert sq.CURRENT_VERSION >= 8


# ── 판정 ────────────────────────────────────────────────────────────────
def test_a_session_newer_than_this_telethon_names_the_fix(tmp_path, monkeypatch):
    """VM 그대로 — 1.36.0(v7)이 v8 을 연다. 무엇을 깔아야 하는지 말한다."""
    p = _session(tmp_path, 8)
    _lib(monkeypatch, "1.36.0", 7)
    _pin(monkeypatch, "1.45.0")
    prod = f"/home/higgack/stock-trade/{tg.PROD_VENV}/bin/python"
    monkeypatch.setattr(tg.sys, "executable", prod)        # VM 의 그 인터프리터
    msg = tg.session_format_problem(p)
    assert "DB v8" in msg and "1.36.0" in msg and "v7" in msg
    assert f"{prod} -m pip install telethon==1.45.0" in msg
    # 운영 venv 가 아닌 인터프리터(NOAH `.venv`)면 **거기에** 깔라고 하지 않는다 —
    # 다른 프로젝트의 telethon 을 바꾸게 된다. 운영 venv 를 가리킨다(#404).
    other = "/home/higgack/stock/.venv/bin/python"
    monkeypatch.setattr(tg.sys, "executable", other)
    msg = tg.session_format_problem(p)
    assert f"{other} -m pip" not in msg
    assert f"{tg.PROD_VENV}/bin/python" in msg
    assert f"{tg.PROD_VENV}/bin/pip install telethon==1.45.0" in msg
    # 고정판 자신이 못 읽으면 '고정판을 깔라' 는 헛걸음이다 — 핀을 올리라고 말한다.
    # 운영 venv 에서도 마찬가지다 — 이미 깔린 판을 다시 깔라고 하지 않는다(T10).
    _pin(monkeypatch, "1.36.0")
    for exe in (other, prod):
        monkeypatch.setattr(tg.sys, "executable", exe)
        msg = tg.session_format_problem(p)
        assert "고정판" in msg and "pip install telethon==1.36.0" not in msg
    # 핀을 못 읽었으면 'telethon None' 을 고정판처럼 적지 않는다(4차 리뷰 L2).
    _pin(monkeypatch, None)
    msg = tg.session_format_problem(p)
    assert "None" not in msg and "requirements.txt" in msg
    # 복사본이어도 못 여는 건 못 여는 것이다.
    assert tg.session_format_problem(p, live=False)


def test_only_the_production_venv_on_the_pin_may_upgrade_a_live_session(
        tmp_path, monkeypatch):
    """사고의 원인 방향 — 다른 venv 의 telethon 이 운영 세션을 열면 파일이 올라가
    운영 유닛이 그 뒤로 못 연다. 판별 기준은 **인터프리터**다(4차 리뷰 M1): 옛 판은
    '판 ≠ 고정판' 으로 갈라, NOAH `.venv` 가 마침 고정판과 같은 판(1.45.0)이면
    통과시켰다 — 핀은 git pull 로 움직이지만 운영 venv 는 pip 를 돌려야 움직인다."""
    p = _session(tmp_path, 7)
    _pin(monkeypatch, "1.45.0")
    _exe(monkeypatch, _OTHER_EXE)
    for ver in ("1.46.0", "1.45.0"):                   # 판이 무엇이든 — 고정판이어도
        _lib(monkeypatch, ver, 8)
        msg = tg.session_format_problem(p, live=True)
        assert msg and "운영 venv" in msg and f"{tg.PROD_VENV}/bin/python" in msg, ver
        assert tg.session_format_problem(p, live=False) is None     # 복사본은 무해
    _exe(monkeypatch, _PROD_EXE)
    _lib(monkeypatch, "1.45.0", 8)                     # 운영 venv 의 고정판이 올린다
    assert tg.session_format_problem(p, live=True) is None           # = 정상 업그레이드
    # 운영 venv 인데 고정판이 아니면 **고정판을 깔라** 고 한다 — 이미 그 venv 에
    # 있는 사람에게 '그 venv 로 돌려라' 는 처방이 아니다(4차 리뷰 L1).
    _pin(monkeypatch, "1.46.0")
    msg = tg.session_format_problem(p, live=True)
    assert msg and f"{_PROD_EXE} -m pip install telethon==1.46.0" in msg
    assert "cd ~/stock-trade" not in msg


def test_a_same_version_foreign_venv_cannot_upgrade_the_live_session_for_real(
        tmp_path, monkeypatch):
    """M1 을 **실물 telethon** 으로 — 다른 venv 가 고정판과 같은 판이어도, 가드가
    없으면 생성자가 운영 세션을 올린다(리뷰어 재현). 가드는 만들기 전에 멈춰야
    하고, 파일은 그대로여야 한다."""
    telethon = pytest.importorskip("telethon")
    sq = pytest.importorskip("telethon.sessions.sqlite")
    if sq.CURRENT_VERSION <= 7:
        pytest.skip("설치판이 v7 을 쓴다 — 올라갈 형식이 없다")
    p = _session(tmp_path, 7)
    con = sqlite3.connect(p)                           # 옛 판이 쓴 모양 그대로
    con.execute("insert into sessions values (2, '149.154.167.51', 443, x'00', null)")
    con.commit()
    con.close()
    _pin(monkeypatch, str(telethon.__version__))       # 그 venv 의 판 = 고정판
    _exe(monkeypatch, _OTHER_EXE)
    with pytest.raises(tg.SessionFormatError, match="운영 venv"):
        tg.guarded_client(telethon.TelegramClient, str(p)[:-len(".session")], 1, "x")
    assert tg.session_db_version(p) == 7               # 올라가지 않았다


def test_matching_or_unmeasurable_formats_never_block_but_say_so(
        tmp_path, monkeypatch, caplog):
    """못 잰 것으로 막으면 첫 인증부터 막힌다 — 재지 못하면 통과하되 **말한다**
    (#54 는 '말하라' 다 — 옛 판은 한 줄도 안 남겼다, 4차 리뷰 L3). 보호할 파일이
    아예 없는 것(첫 인증)은 못 잰 것이 아니라 조용하다."""
    caplog.set_level("WARNING", logger="trade.tg_entities")
    p = _session(tmp_path, 8)
    _pin(monkeypatch, "1.45.0")
    _exe(monkeypatch, _PROD_EXE)
    _lib(monkeypatch, "1.99.0", 8)                   # 판이 달라도 형식이 같으면 무해
    assert tg.session_format_problem(p) is None
    assert tg.session_format_problem(tmp_path / "new") is None     # 새 세션
    assert caplog.records == []                      # 잰 것·없는 것은 조용하다
    _lib(monkeypatch, None, None)                    # telethon 을 못 잼
    assert tg.session_format_problem(p) is None
    assert "telethon" in caplog.records[-1].getMessage()
    _lib(monkeypatch, "1.46.0", 9)
    junk = tmp_path / "junk.session"
    junk.write_bytes(b"not a sqlite file")
    assert tg.session_format_problem(junk) is None   # 손상 — 막지 않고 말한다
    assert "junk.session" in caplog.records[-1].getMessage()
    _pin(monkeypatch, None)                          # 운영 venv 인데 고정판을 못 잼
    assert tg.session_format_problem(p, live=True) is None
    assert "고정판을 못 읽어" in caplog.records[-1].getMessage()
    _exe(monkeypatch, _OTHER_EXE)                    # 운영 venv 가 아니면 핀과 무관
    assert tg.session_format_problem(p, live=True)


def test_the_guard_refuses_before_building_and_passes_through_otherwise(
        tmp_path, monkeypatch):
    built: list = []

    def factory(*a, **k):
        built.append((a, k))
        return "CLIENT"

    monkeypatch.setattr(tg, "session_format_problem", lambda s, live=True: "처방")
    with pytest.raises(tg.SessionFormatError, match="처방"):
        tg.guarded_client(factory, "s", 1, "h")
    assert built == []
    seen: list = []

    def _ok(s, live=True):
        seen.append(live)
        return None

    monkeypatch.setattr(tg, "session_format_problem", _ok)
    assert tg.guarded_client(factory, "s", 1, "h", live=False, proxy=None) == "CLIENT"
    assert built == [(("s", 1, "h"), {"proxy": None})] and seen == [False]


def test_the_failure_note_carries_the_prescription():
    assert tg.startup_failure_note(tg.SessionFormatError("처방 문장")) == "처방 문장"
    assert (tg.startup_failure_note(tg.SessionNotAuthorizedError("로그인 처방"))
            == "로그인 처방")


def test_the_failure_text_says_a_prescription_once():
    """4차 리뷰 L4: 처방을 담아 던진 예외는 원문이 곧 사유다 — 옛 알림은 같은
    처방을 두 번(앞의 것은 200자에서 잘라) 실었다. 처방은 200자를 넘는다."""
    long = "처방 문장 " * 60
    for exc in (tg.SessionFormatError(long), tg.SessionNotAuthorizedError(long)):
        assert tg.prescribed_failure(exc)
        assert tg.startup_failure_text(exc) == f"{type(exc).__name__}: {long}"
    other = RuntimeError("x" * 300)                  # 예상 못 한 예외(반대 증거)
    assert not tg.prescribed_failure(other)
    text = tg.startup_failure_text(other)
    assert text == ("RuntimeError: " + "x" * 200 + "\n"
                    + tg.startup_failure_note(other))


class _StartSpy:
    def __init__(self, authorized: bool):
        self.authorized, self.calls = authorized, []

    async def start(self):
        self.calls.append("start")

    async def connect(self):
        self.calls.append("connect")

    async def is_user_authorized(self):
        self.calls.append("authorized?")
        return self.authorized


def test_a_session_copy_is_never_logged_into():
    """복사본(dry-run·진단)에 `start()` 하면 인증이 풀린 세션에서 전화번호
    로그인을 묻고, 그 로그인은 복사본과 함께 지워진다(#403 3차 리뷰 L2).
    복사본은 접속·인증 확인만 하고 멈춘다 — 처방은 문장에 담는다."""
    c = _StartSpy(authorized=False)
    with pytest.raises(tg.SessionNotAuthorizedError) as e:
        asyncio.run(tg.start_client(c, copy=True))
    assert c.calls == ["connect", "authorized?"]
    assert "로그인하지 않는다" in str(e.value) and "README" in str(e.value)
    c = _StartSpy(authorized=True)                   # 인증된 복사본은 그대로 쓴다
    asyncio.run(tg.start_client(c, copy=True))
    assert c.calls == ["connect", "authorized?"]
    c = _StartSpy(authorized=False)                  # 라이브는 로그인 흐름을 탄다
    asyncio.run(tg.start_client(c, copy=False))      # (처음 인증하는 유일한 길)
    assert c.calls == ["start"]


# ── 실제 telethon 으로 사고 메커니즘을 재현 ────────────────────────────────
def test_a_newer_telethon_upgrades_an_old_session_on_open(tmp_path):
    """'새 판이 한 번 열면 파일이 올라간다' 를 **실물 라이브러리**로 잰다 —
    가정이 아니라 사고의 메커니즘이다. 설치판이 v7 이면 올라가지 않는다."""
    sq = pytest.importorskip("telethon.sessions.sqlite")
    p = _session(tmp_path, 7)
    assert tg.session_db_version(p) == 7
    s = sq.SQLiteSession(str(p))
    s.close()
    assert tg.session_db_version(p) == max(7, sq.CURRENT_VERSION)


# ── 배선 — 생성 지점 전수(#24) ───────────────────────────────────────────
def _prod_py():
    for f in sorted((_ROOT / "trade").rglob("*.py")):
        if "tests" not in f.parts:
            yield f


def _unguarded_client_refs(tree) -> list[int]:
    """클래스를 **참조**하는 자리 중 `guarded_client(...)` 첫 인자가 아닌 곳의 줄.
    호출만 보면 `from telethon import TelegramClient as _TC` · `partial(TelegramClient,
    …)` · `_mk = TelegramClient` 로 빠져나간다(4차 리뷰 L7) — 그래서 호출이 아니라
    **그 이름을 읽는 모든 자리**를 본다(별칭 포함 · `telethon.TelegramClient` 속성)."""
    names = {a.asname or a.name for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("telethon")
             for a in n.names if a.name == "TelegramClient"}
    ok = {id(c.args[0]) for c in ast.walk(tree)
          if isinstance(c, ast.Call) and c.args
          and (getattr(c.func, "id", None) or getattr(c.func, "attr", None))
          == "guarded_client"}
    return [n.lineno for n in ast.walk(tree)
            if ((isinstance(n, ast.Name) and n.id in names
                 and isinstance(n.ctx, ast.Load))
                or (isinstance(n, ast.Attribute) and n.attr == "TelegramClient"))
            and id(n) not in ok]


def test_the_client_guard_scan_catches_every_evasion():
    """위 검사가 우회를 실제로 잡는지 합성 소스로 태운다(#286 fires 테스트)."""
    base = "from telethon import TelegramClient\n"
    for evasion in ("c = TelegramClient('s', 1, 'h')\n",
                    "import functools\nmk = functools.partial(TelegramClient, 's')\n",
                    "mk = TelegramClient\n",
                    "import telethon\nc = telethon.TelegramClient('s', 1, 'h')\n"):
        assert _unguarded_client_refs(ast.parse(base + evasion)), evasion
    alias = "from telethon import TelegramClient as _TC\nc = _TC('s', 1, 'h')\n"
    assert _unguarded_client_refs(ast.parse(alias))
    fine = (base + "from trade.tg_entities import guarded_client\n"
            "c = guarded_client(TelegramClient, 's', 1, 'h', live=True)\n")
    assert _unguarded_client_refs(ast.parse(fine)) == []


def test_no_production_code_builds_a_telegram_client_directly():
    """새 스크립트가 옛 방식(`TelegramClient(...)`)으로 돌아가면 형식 가드를
    건너뛴다 — 디렉터리 전수로 잡는다. 클래스는 `guarded_client` 첫 인자로만
    나타나야 한다(가드가 만든다)."""
    bad, guarded = [], []
    for f in _prod_py():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        bad += [f"{f.relative_to(_ROOT)}:{ln}" for ln in _unguarded_client_refs(tree)]
        if any(isinstance(n, ast.Call)
               and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
               == "guarded_client" for n in ast.walk(tree)):
            guarded.append(f.name)
    assert not bad, bad
    # 대조 0건은 통과가 아니다(#54) — 실제 생성 지점이 가드를 거치고 있어야 한다.
    assert {"backfill_badonion.py", "backfill_beon.py", "listen_badonion.py",
            "listen_beon.py"} <= set(guarded), guarded


def test_every_telethon_unit_runs_the_venv_the_guard_names():
    """가드가 '이 venv 로 돌려라' 고 말하는 venv 가 실제 운영 유닛의 venv 여야
    한다 — 틀린 venv 를 안내한 것이 이 사고의 시작이다(#371 · #404). 유닛을
    열거하지 않고, ExecStart 가 부르는 스크립트가 telethon 을 쓰면 잰다(#24)."""
    checked = []
    for unit in sorted((_ROOT / "deploy").glob("trade-bot-*.service")):
        m = re.search(r"(?m)^ExecStart=(\S+/python)\s+(?:-m\s+(\S+)|(\S+\.py))",
                      unit.read_text(encoding="utf-8"))
        if not m:
            continue
        target = (_ROOT / (m.group(2).replace(".", "/") + ".py")
                  if m.group(2) else _ROOT / m.group(3))
        if not target.is_file() or "telethon" not in target.read_text(encoding="utf-8"):
            continue
        checked.append(unit.name)
        assert f"/{tg.PROD_VENV}/bin/python" in m.group(1), (unit.name, m.group(1))
    assert len(checked) >= 4, checked                 # 백필 2 · 리스너 2


# ── 리스너 — 형식 오류는 설정 오류: 78 로 끝나고 알린다 ─────────────────────
class _FakeClient:
    built: list = []

    def __init__(self, *a, **k):
        _FakeClient.built.append(a)


def _fake_telethon_modules() -> dict:
    tl = types.ModuleType("telethon")
    tl.TelegramClient = _FakeClient
    events = types.ModuleType("telethon.events")
    events.NewMessage = lambda **k: ("NewMessage", k)
    tl.events = events
    utils = types.ModuleType("telethon.utils")
    utils.get_peer_id = lambda p: 0
    tl.utils = utils
    errors = types.ModuleType("telethon.errors")
    for n in ("AuthKeyError", "FloodWaitError", "SessionPasswordNeededError"):
        setattr(errors, n, type(n, (Exception,), {}))
    message = types.ModuleType("telethon.tl.custom.message")
    message.Message = object
    return {"telethon": tl, "telethon.events": events, "telethon.utils": utils,
            "telethon.errors": errors,
            "telethon.tl": types.ModuleType("telethon.tl"),
            "telethon.tl.custom": types.ModuleType("telethon.tl.custom"),
            "telethon.tl.custom.message": message}


def _load(monkeypatch, tmp_path, script: str):
    monkeypatch.setenv("TRADE_TELETHON_API_ID", "1")
    monkeypatch.setenv("TRADE_TELETHON_API_HASH", "stub")
    monkeypatch.setenv("TRADE_CHANNEL_CHAT_IDS", "-100222")
    monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TRADE_BOT_TOKEN", raising=False)
    for name, mod in _fake_telethon_modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(_FakeClient, "built", [])
    spec = importlib.util.spec_from_file_location(
        f"_{script}_under_test", _SCRIPTS / f"{script}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)           # sys.modules 에 등록하지 않는다(#397)
    notes: list = []
    monkeypatch.setattr(mod, "_notify", notes.append)
    return mod, notes


@pytest.mark.parametrize("script", ["listen_badonion", "listen_beon"])
def test_a_listener_with_an_unreadable_session_stops_cleanly_and_says_why(
        monkeypatch, tmp_path, script):
    """형식 오류는 재시작으로 안 풀린다 — 1 로 끝나면 유닛의 Restart=on-failure 가
    15초마다 되살려 같은 오류를 반복한다. EX_CONFIG(78, RestartPreventExitStatus)로
    끝내고 처방을 알린다."""
    mod, notes = _load(monkeypatch, tmp_path, script)
    monkeypatch.setattr(tg, "session_format_problem",
                        lambda s, live=True: "세션 형식 처방(테스트)")
    monkeypatch.setattr(sys, "argv", [f"{script}.py"])
    with pytest.raises(SystemExit) as ex:
        mod.main()
    assert ex.value.code == 78 == mod.EX_CONFIG
    assert _FakeClient.built == []                     # 만들기 전에 막았다
    assert notes and "세션 형식 불일치" in notes[-1]
    assert "세션 형식 처방(테스트)" in notes[-1]
    unit = (_ROOT / "deploy" / f"trade-bot-{script.split('_')[1]}-listener.service"
            ).read_text(encoding="utf-8")
    assert "RestartPreventExitStatus=78" in unit       # 78 이 정말 루프를 막는다


def _live_spy(monkeypatch) -> list:
    """`session_format_problem` 이 받은 `live` 를 기록하고 막는다. 옛 스텁은
    `live` 를 무시해, 호출부가 운영 세션을 **복사본으로** 재도(live=False — 승격
    가드가 꺼진다) 전부 통과했다(4차 리뷰 T19·L03·N02·E03·Q02)."""
    seen: list = []

    def _spy(s, live=True):
        seen.append(live)
        return "세션 형식 처방(테스트)"

    monkeypatch.setattr(tg, "session_format_problem", _spy)
    return seen


@pytest.mark.parametrize("script", ["listen_badonion", "listen_beon"])
def test_a_listener_measures_its_session_as_live(monkeypatch, tmp_path, script):
    mod, _notes = _load(monkeypatch, tmp_path, script)
    seen = _live_spy(monkeypatch)
    monkeypatch.setattr(sys, "argv", [f"{script}.py"])
    with pytest.raises(SystemExit):
        mod.main()
    assert seen == [True]


def test_the_beon_backfill_measures_its_session_as_live(monkeypatch, tmp_path):
    mod, _notes = _load(monkeypatch, tmp_path, "backfill_beon")
    seen = _live_spy(monkeypatch)
    assert asyncio.run(mod.run(mod._parse_date("2026-09-20"), None, False, 100)) == 1
    assert seen == [True]


def test_the_dedup_diagnostic_measures_the_live_session_as_live(monkeypatch, tmp_path):
    """`diagnose_dedup` 은 BeOn 의 **운영** 세션을 그대로 연다 — 다른 venv 로
    돌리면 그 파일을 올린다. 운영 세션으로 재야 한다."""
    beon, _notes = _load(monkeypatch, tmp_path, "backfill_beon")
    # 진단은 `trade.scripts.backfill_beon` 을 import 한다 — 가짜 telethon 위에서
    # 올린 모듈을 그 이름으로 잠시 보인다(teardown 에 되돌린다, #397).
    monkeypatch.setitem(sys.modules, "trade.scripts.backfill_beon", beon)
    spec = importlib.util.spec_from_file_location(
        "_diagnose_dedup_under_test", _SCRIPTS / "diagnose_dedup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = _live_spy(monkeypatch)
    with pytest.raises(tg.SessionFormatError):
        asyncio.run(mod.run(1, None, 0))
    assert seen == [True] and _FakeClient.built == []


@pytest.mark.parametrize("script", ["listen_badonion", "listen_beon"])
def test_interactive_auth_also_goes_through_the_guard(monkeypatch, tmp_path, script):
    """재인증(`--auth`)도 같은 파일을 연다 — 다른 venv 로 인증하면 거기서도 올라간다."""
    mod, _notes = _load(monkeypatch, tmp_path, script)
    monkeypatch.setattr(tg, "session_format_problem",
                        lambda s, live=True: "세션 형식 처방(테스트)")
    with pytest.raises(tg.SessionFormatError):
        asyncio.run(mod._run_auth())
    assert _FakeClient.built == []


# ── 형제 백필(BeOn) — 생성 실패도 알리고, dry-run 은 알리지 않는다 ────────
def test_the_beon_backfill_pages_on_a_session_it_cannot_open(monkeypatch, tmp_path):
    mod, notes = _load(monkeypatch, tmp_path, "backfill_beon")
    monkeypatch.setattr(tg, "session_format_problem",
                        lambda s, live=True: "세션 형식 처방(테스트)")
    since = "2026-09-20"
    assert asyncio.run(mod.run(mod._parse_date(since), None, False, 100)) == 1
    assert _FakeClient.built == []
    assert notes and "시작 실패" in notes[-1] and "세션 형식 처방(테스트)" in notes[-1]
    # 처방은 한 번만(4차 리뷰 L4 — 형제 백필과 같은 헬퍼 #38).
    assert notes[-1].count("세션 형식 처방(테스트)") == 1
    notes.clear()
    assert asyncio.run(mod.run(mod._parse_date(since), None, True, 100)) == 1
    assert notes == []                                  # 사람이 돌린 진단


def test_the_beon_backfill_logs_a_prescribed_failure_without_a_traceback(
        monkeypatch, tmp_path, caplog):
    """4차 리뷰 L5 — 처방을 담아 던진 예외는 그 문장이 곧 진단이다. 트레이스백에
    묻히면 무엇을 깔아야 하는지 안 보인다(형제 backfill_badonion 과 같은 규약)."""
    mod, _notes = _load(monkeypatch, tmp_path, "backfill_beon")
    monkeypatch.setattr(tg, "session_format_problem",
                        lambda s, live=True: "세션 형식 처방(테스트)")
    caplog.set_level("INFO")
    for dry in (False, True):
        caplog.clear()
        assert asyncio.run(mod.run(mod._parse_date("2026-09-20"), None, dry,
                                   100)) == 1
        assert [r for r in caplog.records if r.exc_info] == [], dry
        assert "세션 형식 처방(테스트)" in caplog.text
