"""테스트가 **실제 홈**(운영 데이터)을 못 건드린다 — 루트 conftest 의 HOME 격리 + 쓰기 가드
(실수 #421, 2026-09-27).

실측(감사 훅으로 테스트마다 쓰기·삭제·이름변경을 기록한 일회성 진단, 세 트리 전부): **90개
테스트가 운영 디렉터리에 131가지** 쓰기를 했다 — 가계부 `budget.json` 갈아 쓰기 · 모의투자
`portfolio.json`·`audit.jsonl` 가짜 체결 · `paper/auto_audit.jsonl` **삭제** · 캐시 mtime 밀기
(`os.utime`, 운영 TTL 이 늘어난다) · `~/.trade/run_ledger.json`·`.scan_notified.json`·공유 페이지.
캐시 상수를 하나씩 옮기던 목록(17줄)은 누가 우연히 발견할 때만 자랐다(#24·#417).

여기서 재는 계약:
  ① 순수 판정(`_home_write_guard` 의 target) — 무엇이 쓰기이고 어디가 실제 홈인가
  ② 실제 인터프리터에서 **진짜 연산**이 막힌다 — 이벤트 모양을 손으로 짓지 않는다(#155)
  ③ 중첩 pytest — 예외를 삼켜도 그 테스트가 실패하고, 삭제·자식 쓰기·수집 시점 쓰기도
     잡히며, `Path.home()`·tmp_path 쓰기는 통과한다(반대 증거, #25)
  ④ 이 세션 자체가 격리돼 있다(HOME · `.env` · 자식 가드가 같은 소스)
⚠️ ②③ 은 가짜 '실제 홈'(tmp)을 지키게 한 **자식 프로세스**에서 잰다 — 가드가 깨졌을 때
진짜 홈에 쓰는 일이 없게(#30 테스트가 운영 상태를 오염시키면 안 된다).
"""
from __future__ import annotations

import errno
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
import xml.etree.ElementTree as ET

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_W = os.O_WRONLY | os.O_CREAT | os.O_TRUNC


def _conftest():
    """**이미 로드된** 루트 conftest — 경로로 새로 import 하면 격리를 한 번 더 걸어
    HOME 이 또 옮겨진다. 모듈 이름은 importmode 에 따라 갈리므로 파일로 찾는다."""
    want = str(ROOT / "conftest.py")
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if f and str(pathlib.Path(f).resolve()) == want:
            return mod
    raise AssertionError("루트 conftest.py 가 sys.modules 에 없다 — 격리가 안 걸렸다")


def _protected() -> list:
    return [p for p in os.environ.get("NOAH_TEST_PROTECTED_HOME", "").split(os.pathsep) if p]


# ── ① 순수 판정 ─────────────────────────────────────────────────────────────
@pytest.fixture
def guard(tmp_path):
    """가짜 '실제 홈' + 그 안의 레포(VM `~/stock` 처럼) + 짧은 허용 루트 `/tmp`."""
    cf = _conftest()
    home = tmp_path / "home"
    repo = home / "stock"
    repo.mkdir(parents=True)
    rec: list = []
    hook, target = cf._home_write_guard([str(home)], [str(repo), "/tmp"],
                                        lambda e, p: rec.append((e, p)))
    return {"home": home, "repo": repo, "hook": hook, "target": target, "rec": rec}


def test_writes_under_the_real_home_are_named(guard):
    """쓰기 계열 이벤트 전부 — 이름을 바꾸면 **양쪽** 경로를 본다(내보내기도 삭제다)."""
    p = str(guard["home"] / ".tradingagents" / "budget.json")
    cases = {
        "open-w": ("open", (p, "w", _W)),
        "os.open": ("open", (p, None, os.O_CREAT | os.O_WRONLY)),
        "append": ("open", (p, "a", os.O_WRONLY | os.O_APPEND | os.O_CREAT)),
        "rw": ("open", (p, "r+", os.O_RDWR)),
        "remove": ("os.remove", (p, -1)),
        "rename-src": ("os.rename", (p, "/tmp/elsewhere", -1, -1)),
        "rename-dst": ("os.rename", ("/tmp/elsewhere", p, -1, -1)),
        "mkdir": ("os.mkdir", (p, 0o777, -1)),
        "rmdir": ("os.rmdir", (p, -1)),
        "rmtree": ("shutil.rmtree", (p, None)),
        "utime": ("os.utime", (p, None, None, -1)),
        "chmod": ("os.chmod", (p, 0o644, -1)),
        "chown": ("os.chown", (p, 0, 0, -1)),
        "truncate": ("os.truncate", (p, 0)),
        "link-dst": ("os.link", ("/tmp/x", p, -1, -1)),
        "symlink-dst": ("os.symlink", ("/tmp/x", p, -1)),
        "sqlite": ("sqlite3.connect", (p,)),
        "sqlite-uri-rw": ("sqlite3.connect", (f"file:{p}?mode=rw",)),
        "bytes": ("open", (p.encode(), "wb", _W)),
        "pathlike": ("os.remove", (pathlib.Path(p), -1)),
        # 리눅스에선 `//x` 가 `/x` 다 — `normpath` 는 선두 `//` 를 남겨 루트 비교를 빠져나갔다
        "double-slash": ("open", ("/" + p, "w", _W)),
        # 2026-09-27 독립 리뷰가 실측한 우회·빈틈(L5·L7)
        "link-src": ("os.link", (p, "/tmp/alias", -1, -1)),       # 홈 안 파일에 홈 밖 이름
        "setxattr": ("os.setxattr", (p, "user.k", b"v", 0)),
        "removexattr": ("os.removexattr", (p, "user.k")),
        "rdonly-trunc": ("open", (p, "r", os.O_RDONLY | os.O_TRUNC)),   # 리눅스는 이것도 비운다
        "rmtree-3.10": ("shutil.rmtree", (p,)),                    # 3.10 은 인자가 하나다(실측)
        "sqlite-uri-localhost": ("sqlite3.connect", (f"file://localhost{p}",)),
        "sqlite-uri-empty-host": ("sqlite3.connect", (f"file://{p}",)),
        "sqlite-uri-bytes-3.10": ("sqlite3.connect", (f"file:{p}".encode(),)),   # 3.10 은 bytes
        "sqlite-uri-fragment": ("sqlite3.connect", (f"file:{p}#frag",)),   # sqlite 는 # 뒤를 버린다
    }
    miss = {k: guard["target"](e, a) for k, (e, a) in cases.items()
            if guard["target"](e, a) != p}
    assert not miss, miss
    # sqlite 는 URI 의 %HH 를 풀어 그 이름으로 만든다(3.10~3.13 실측) — 푼 이름으로 판정한다
    q = str(guard["home"] / "a b%.db")
    assert guard["target"]("sqlite3.connect", ("file:" + urllib.parse.quote(q),)) == q


def test_reads_and_unplaceable_paths_pass(guard, monkeypatch):
    """막지 않는 것 — 읽기 · 파일 디스크립터 · dir_fd 상대 경로(어디인지 모른다) ·
    인터프리터 `.pyc` · 메모리/읽기전용 sqlite · 모르는 이벤트.

    ⚠️ cwd 를 가짜 홈으로 옮긴다 — dir_fd 상대 이름을 cwd 로 풀면 **홈 안**이 되는
    자리라야 'dir_fd 면 건너뛴다' 를 지우는 변형이 잡힌다(#91c)."""
    h = guard["home"]
    monkeypatch.chdir(h)
    p = str(h / ".tradingagents" / "budget.json")
    cases = {
        "read": ("open", (p, "r", os.O_RDONLY | os.O_CLOEXEC)),
        "fd": ("open", (7, "w", _W)),
        "dir_fd-relative": ("os.remove", ("x.json", 5)),
        "rename-dir_fd": ("os.rename", ("a", "b", 5, 5)),
        "pycache": ("open", (str(h / "lib" / "__pycache__" / "m.cpython-311.pyc"), "wb", _W)),
        "pycache-dir": ("os.mkdir", (str(h / "lib" / "__pycache__"), 0o777, -1)),   # 리뷰 L3
        "rmdir-dir_fd": ("os.rmdir", ("x.json", 5)),
        "mkdir-dir_fd": ("os.mkdir", ("x.json", 0o777, 5)),
        "utime-dir_fd": ("os.utime", ("x.json", None, None, 5)),
        "sqlite-memory": ("sqlite3.connect", (":memory:",)),
        "sqlite-empty": ("sqlite3.connect", ("",)),              # 임시 DB — cwd 로 풀면 홈 안이 된다
        "sqlite-ro-uri": ("sqlite3.connect", (f"file:{p}?mode=ro",)),
        "sqlite-memory-uri": ("sqlite3.connect", (f"file:{p}?mode=memory",)),
        "sqlite-immutable-uri": ("sqlite3.connect", (f"file:{p}?immutable=1",)),
        "sqlite-other-host": ("sqlite3.connect", (f"file://elsewhere{p}",)),   # sqlite 가 안 연다
        "unknown-event": ("os.listdir", (p,)),
    }
    got = {k: guard["target"](e, a) for k, (e, a) in cases.items()}
    assert all(v is None for v in got.values()), got
    # 반대 증거(#25): 같은 cwd 에서 dir_fd 가 없으면(-1) 상대 이름도 홈 안으로 풀려 막힌다 —
    # 이벤트마다 dir_fd 자리가 다르다(remove·rmdir 1 · mkdir 2 · utime 3)
    for e, a in (("os.remove", ("x.json", -1)), ("os.rmdir", ("x.json", -1)),
                 ("os.mkdir", ("x.json", 0o777, -1)), ("os.utime", ("x.json", None, None, -1))):
        assert guard["target"](e, a) == str(h / "x.json"), e


def test_a_relocated_pycache_prefix_is_the_interpreters_cache(tmp_path):
    """`PYTHONPYCACHEPREFIX`(`-X pycache_prefix`)면 `.pyc` 가 `__pycache__` 없이 그 접두 아래에
    온다 — 그것도 인터프리터 캐시다(2026-09-27 실측: 접두가 실제 홈 안이면 처음 import 하는
    모듈마다 막혀 그 테스트가 실패했다). 반대 증거(#25): 접두가 없으면 같은 경로가 막히고,
    접두와 **이름만 겹치는** 형제 디렉터리는 접두가 있어도 막힌다."""
    cf = _conftest()
    home = tmp_path / "home"
    pyc = home / ".cache" / "pyc"
    f = str(pyc / "usr" / "lib" / "python3.11" / "colorsys.cpython-311.pyc.1400")
    sib = str(home / ".cache" / "pyc-other" / "x")
    old = sys.pycache_prefix
    try:
        sys.pycache_prefix = None
        _, bare = cf._home_write_guard([str(home)], [], lambda *a: None)
        sys.pycache_prefix = "/" + str(pyc)       # 선두 `//` 로 줘도 같은 자리다
        _, t = cf._home_write_guard([str(home)], [], lambda *a: None)
    finally:
        sys.pycache_prefix = old
    assert bare("open", (f, None, _W)) == f
    assert t("open", (f, None, _W)) is None
    assert t("os.mkdir", (str(pyc), 0o777, -1)) is None
    assert t("open", (sib, "w", _W)) == sib
    assert t("open", (str(home / "budget.json"), "w", _W)) == str(home / "budget.json")


def test_the_longest_matching_root_decides(guard):
    """레포가 홈 안이어도(VM `~/stock`) 레포는 허용 · 형제 체크아웃(`~/stock-trade`)은 보호 ·
    접두어만 같은 형제 경로는 무관 · 허용 `/tmp` 안에 둔 가짜 홈은 보호(짧은 허용 루트가 긴
    보호 루트를 못 이긴다 — tmp_path 가 `/tmp` 아래라 위 단언들이 그걸 이미 태운다)."""
    h, repo, t = guard["home"], guard["repo"], guard["target"]
    assert t("open", (str(repo / ".pytest_cache" / "v"), "w", _W)) is None
    trade = str(h / "stock-trade" / "x")
    assert t("open", (trade, "w", _W)) == trade
    assert t("open", (str(h) + "2/x", "w", _W)) is None
    assert t("open", (str(h), "w", _W)) == str(h)
    cf = _conftest()
    _, tie = cf._home_write_guard([str(h)], [str(h)], lambda *a: None)
    assert tie("open", (str(h / "x"), "w", _W)) is None          # 같은 길이면 허용
    # "/" 는 루트가 되지 않는다 — `HOME=/` 인 컨테이너에서 모든 쓰기를 막으면 세션이 통째로
    # 죽는다. 허용 쪽도 같다("/" 를 허용하면 모든 보호가 풀린다).
    _, root = cf._home_write_guard([os.sep], [], lambda *a: None)
    assert root("open", ("/etc/x", "w", _W)) is None
    _, open_all = cf._home_write_guard([str(h)], [os.sep], lambda *a: None)
    assert open_all("open", (str(h / "x"), "w", _W)) == str(h / "x")


def test_a_symlinked_home_is_guarded_in_both_spellings(tmp_path):
    """홈이 심볼릭 링크면(`/home/x` → `/data/x`) `Path.home().resolve()` 로 만든 **실경로**가
    링크 표기 루트와의 문자열 비교를 빠져나간다 — 두 표기를 다 지킨다. 반대로 그 홈 안의
    레포(VM `~/stock`)는 두 표기 모두 허용이다 — 레포 루트는 `resolve()` 로 구해 실경로만
    알지만 링크 표기로도 불린다(안 그러면 멀쩡한 레포 쓰기가 막힌다)."""
    real = tmp_path / "data_home"
    (real / "stock").mkdir(parents=True)
    link = tmp_path / "home_link"
    link.symlink_to(real, target_is_directory=True)
    _, t = _conftest()._home_write_guard([str(link)], [str(real / "stock")], lambda *a: None)
    assert t("open", (str(link / "x"), "w", _W)) == str(link / "x")
    assert t("open", (str(real / "x"), "w", _W)) == str(real / "x")
    assert t("open", (str(real / "stock" / "a"), "w", _W)) is None
    assert t("open", (str(link / "stock" / "a"), "w", _W)) is None


def test_the_hook_records_then_raises_the_os_error_type(guard):
    """막을 때 던지는 건 운영체제가 거절할 때와 같은 `PermissionError` — `except OSError`
    호출부의 동작이 안 바뀐다. 기록이 먼저이고, 읽기·허용 경로는 조용하다."""
    p = str(guard["home"] / "x.json")
    with pytest.raises(PermissionError) as ei:
        guard["hook"]("open", (p, "w", _W))
    assert ei.value.errno == errno.EACCES and ei.value.filename == p
    assert "_home_write_guard" in str(ei.value)
    guard["hook"]("open", (p, "r", os.O_RDONLY))
    guard["hook"]("open", (str(guard["repo"] / "x"), "w", _W))
    assert guard["rec"] == [("open", p)]


def test_a_failing_record_still_blocks(tmp_path):
    """기록이 실패해도(디스크 가득 등) 막기는 그대로다 — 기록 실패가 운영 쓰기를 통과시키면
    안 된다."""
    def boom(event, path):
        raise RuntimeError("log disk full")
    hook, _ = _conftest()._home_write_guard([str(tmp_path)], [], boom)
    with pytest.raises(PermissionError):
        hook("open", (str(tmp_path / "x"), "w", _W))


def test_real_homes_prefers_what_the_outer_session_said():
    """중첩 세션은 바깥 세션이 정한 홈을 지킨다(자기 HOME 은 이미 임시 홈이다) · 그게 없으면
    `$HOME` · 계정 홈(`pwd`)은 **늘** 더한다 — HOME 을 바꿔 띄운 셸이어도, 바깥 값이 셸에 **낡은
    채** 남아 있어도 계정 홈엔 운영 데이터가 있다. 내보낼 목록엔 `/` 가 없다(컨테이너 `HOME=/`).

    ⚠️ 2026-09-27 다시 썼다(#222): 옛 계약은 바깥 값**만** 돌려줬다 — 독립 리뷰 L6 이 낡은 값이
    계정 홈을 통째로 빼는 것을, L4 가 `HOME=/` 에서 `/` 를 내보내 빨간불이 되는 것을 짚었다."""
    import pwd
    cf = _conftest()
    acct = pwd.getpwuid(os.getuid()).pw_dir
    got = cf._real_homes({"NOAH_TEST_PROTECTED_HOME": os.pathsep.join(["/a", "/b"]), "HOME": "/x"})
    assert got[:2] == ["/a", "/b"] and acct in got and "/x" not in got, got
    got = cf._real_homes({"HOME": "/x"})
    assert "/x" in got and acct in got, got
    prot = cf._protected_homes({"HOME": "/"})
    assert os.sep not in prot and os.path.abspath(acct) in prot, prot



def test_the_session_allows_the_repo_and_temp_even_inside_a_protected_home():
    """VM 은 레포(`~/stock`)가 지킬 홈 **안**이다 — 이 세션이 실제로 만든 허용 목록이 레포·임시
    디렉터리·임시 홈을 담고 자식에게 **같은 목록**을 넘긴다. 목록에서 레포를 빼면 VM 에서
    `.pytest_cache` 가 막혀 `make test` 가 빨간불이다(2026-09-27 독립 리뷰 M2 실측 — 판정 규칙만
    재던 회귀는 세션이 넘기는 목록을 안 봐서 셋 다 빼도 통과했다)."""
    cf = _conftest()
    allowed = cf._HOME["allowed"]
    assert os.environ["NOAH_TEST_HOME_ALLOWED"].split(os.pathsep) == allowed
    assert {cf._REPO_ROOT, tempfile.gettempdir(), cf._HOME["home"]} <= set(allowed), allowed
    # VM 모양으로 태운다 — 레포의 부모를 지킬 홈으로 두고 **세션이 만든** 목록으로 판정한다
    parent = os.path.dirname(cf._REPO_ROOT)
    _, t = cf._home_write_guard([parent], allowed, lambda *a: None)
    assert t("os.mkdir", (os.path.join(cf._REPO_ROOT, ".pytest_cache"), 0o777, -1)) is None
    sib = os.path.join(parent, "stock-trade", "x")
    assert t("open", (sib, "w", _W)) == sib


def test_blame_skips_frames_inside_the_repo_venv():
    """범인 지목은 **레포 코드의 줄**이다 — 레포 안 `.venv` 의 라이브러리 줄은 건너뛴다(그 줄로는
    고칠 자리를 모른다, #114). 가짜 파일 이름으로 컴파일한 두 단 호출로 잰다."""
    cf = _conftest()
    lib = os.path.join(cf._REPO_ROOT, ".venv", "lib", "python3.11", "site-packages", "libx.py")
    ns = {"rec": cf._record_home_write}
    exec(compile("def hook():\n    rec('open', '/nowhere/x')\n"
                 "def lib_write():\n    hook()\n", lib, "exec"), ns)
    n0 = len(cf._HOME_WRITES)
    ns["lib_write"]()
    rec = cf._HOME_WRITES.pop()                  # 이 테스트의 fixture 가 '쓰기' 로 세지 않게
    assert len(cf._HOME_WRITES) == n0
    assert rec["where"] and not any(".venv" in w for w in rec["where"]), rec
    assert rec["where"][0].startswith("tests/test_home_isolation_20260927.py:"), rec


def test_a_child_line_names_the_test_that_spawned_it():
    """백그라운드 자식은 띄운 테스트가 끝난 뒤에 써서 **다른** 테스트 창에 든다 — 줄이 그 자식을
    띄운 테스트(물려받은 `PYTEST_CURRENT_TEST`)를 말해야 범인이 보인다(리뷰 L2 실측)."""
    cf = _conftest()
    line = cf._fmt_child_write("12\topen\t/h/x\ttests/a.py::test_bg (call)\t-c")
    assert line.startswith("open /h/x  [자식 pid 12 · 띄운 테스트 tests/a.py::test_bg (call): -c]"), line
    assert "띄운 테스트" not in cf._fmt_child_write("12\topen\t/h/x\t\t-c")


def test_every_path_env_var_the_repo_reads_is_checked_at_session_start():
    """레포가 읽는 경로 지정 변수(`…_DIR`·`…_PATH`·`…_HOME`·`…_FILE`)는 전부 세션 시작에 값을
    대조받는다 — 이름공간 목록이 새 변수를 놓치면 여기서 걸린다(#24). 셸이 실제 홈을 가리키게
    둔 `TRADE_DATA_DIR` 은 trade/tests 99건을 빨간불로 만들었다(리뷰 M3 실측, 쓰기는 막혔다)."""
    cf = _conftest()
    pat = re.compile(r"""(?:environ\.get|getenv|environ\[|environ\.setdefault)\(?\s*["']([A-Z][A-Z0-9_]*)["']""")
    names = set()
    for base in ("bot", "trade"):
        for f in (ROOT / base).rglob("*.py"):
            if "tests" not in f.relative_to(ROOT).parts:
                names.update(pat.findall(f.read_text(encoding="utf-8", errors="replace")))
    pathish = {n for n in names if re.search(r"(DIR|PATH|HOME|FILE)$", n)}
    assert {"TRADE_DATA_DIR", "TRADINGAGENTS_HOME", "TRADE_HS_MAP_PATH"} <= pathish, pathish  # #54
    missing = sorted(n for n in pathish - {"HOME", "PATH", "PYTHONPATH"} if not cf._home_dir_var(n))
    assert not missing, missing
    assert cf._home_dir_var("XDG_CACHE_HOME") and not cf._home_dir_var("XDG_RUNTIME_DIR")


def test_child_log_reads_only_complete_lines(tmp_path, monkeypatch):
    """자식이 **쓰는 중인** 마지막 줄은 다음에 읽는다 — 반쪽 줄을 읽고 오프셋을 넘기면 그 줄은
    영영 안 보인다(#54)."""
    cf = _conftest()
    log = tmp_path / "blocked.tsv"
    log.write_bytes(b"1\topen\t/h/a\t\t-c\n2\tos.remove\t/h/b")
    monkeypatch.setitem(cf._HOME, "log", str(log))
    lines, off = cf._child_home_writes(0)
    assert lines == ["1\topen\t/h/a\t\t-c"] and off == len(b"1\topen\t/h/a\t\t-c\n")
    with open(log, "ab") as f:
        f.write(b"\t\t-c\n")
    lines, off2 = cf._child_home_writes(off)
    assert lines == ["2\tos.remove\t/h/b\t\t-c"] and off2 == log.stat().st_size


# ── ② 진짜 연산 — 실제 인터프리터에서 ──────────────────────────────────────
_REAL_OPS = textwrap.dedent(r'''
    import importlib.util, json, os, pathlib, shutil, sqlite3, sys
    root, fake, outside = (pathlib.Path(a) for a in sys.argv[1:4])
    spec = importlib.util.spec_from_file_location("root_conftest_probe", root / "conftest.py")
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)
    keep, sub = fake / "keep.txt", fake / "dir"
    ops = {
        "write_text": lambda: (fake / "a.json").write_text("{}"),
        "open_append": lambda: open(keep, "a").write("x"),
        "os_open": lambda: os.open(str(fake / "b"), os.O_CREAT | os.O_WRONLY),
        "unlink": lambda: keep.unlink(),
        "rename_out": lambda: os.rename(keep, outside / "moved"),
        "replace_in": lambda: os.replace(outside / "src", fake / "r"),
        "mkdir": lambda: (fake / "new").mkdir(),
        "makedirs_exist_ok": lambda: os.makedirs(sub, exist_ok=True),
        "rmdir": lambda: sub.rmdir(),
        "rmtree": lambda: shutil.rmtree(sub),
        "touch": lambda: keep.touch(),
        "chmod": lambda: os.chmod(keep, 0o600),
        "truncate": lambda: os.truncate(keep, 0),
        "symlink": lambda: os.symlink(outside / "src", fake / "ln"),
        "copy2_in": lambda: shutil.copy2(outside / "src", fake / "c"),
        "move_in": lambda: shutil.move(str(outside / "src"), str(fake / "m")),
        "sqlite": lambda: sqlite3.connect(str(fake / "db.sqlite")),
        "double_slash": lambda: open("/" + str(fake / "d"), "w"),
    }
    res, rec = {}, {}
    for k, fn in ops.items():
        n0 = len(cf._HOME_WRITES)
        try:
            fn()
            res[k] = "done"
        except PermissionError:
            res[k] = "blocked"
        except Exception as e:                   # noqa: BLE001
            res[k] = "other:" + type(e).__name__
        rec[k] = len(cf._HOME_WRITES) - n0
    read = keep.read_text()                      # 읽기는 막지 않는다
    p = pathlib.Path.home() / ".tradingagents" / "x.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")                           # 홈에서 파생한 경로는 임시 홈이다
    print(json.dumps({"res": res, "rec": rec, "read": read, "home_write": str(p),
                      "recorded": sorted({w["event"] for w in cf._HOME_WRITES})}))
''')


def test_real_operations_are_blocked_in_a_real_interpreter(tmp_path):
    """이벤트 모양은 CPython 이 정한다 — 손으로 지은 모양(①)이 틀리면 전부 초록인 채 뚫린다
    (#155 픽스처는 원천이 실제로 보내는 모양대로). 진짜 `pathlib`·`os`·`shutil`·`sqlite3`
    연산을 가짜 실제 홈에 걸어 **전부** 막히는지, 그리고 그 홈이 한 글자도 안 바뀌는지 본다."""
    fake, outside = tmp_path / "realhome", tmp_path / "outside"
    (fake / "dir").mkdir(parents=True)
    (fake / "keep.txt").write_text("keep")
    outside.mkdir()
    (outside / "src").write_text("s")
    before = sorted((p.relative_to(fake).as_posix(), p.stat().st_mtime_ns, p.stat().st_mode)
                    for p in fake.rglob("*"))
    env = dict(os.environ, NOAH_TEST_PROTECTED_HOME=os.pathsep.join([str(fake), *_protected()]))
    r = subprocess.run([sys.executable, "-c", _REAL_OPS, str(ROOT), str(fake), str(outside)],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["read"] == "keep"
    # 연산마다 가드에 **걸렸다**(기록 ≥ 1) — 그리고 전부 막혔다. 단 하나,
    # `os.makedirs(…, exist_ok=True)` 는 이미 있는 디렉터리면 **표준 라이브러리가** 가드의
    # `PermissionError` 를 삼킨다(`except OSError: if not exist_ok …`). 앱의 흔한
    # `_DIR.mkdir(parents=True, exist_ok=True)` 도 같다 — 그래서 막기만으로는 부족하고
    # fixture 가 **기록을** 읽어 실패시켜야 한다(③, #12).
    assert out["rec"] and all(n >= 1 for n in out["rec"].values()), out["rec"]
    swallowed = {"makedirs_exist_ok"}
    assert {k for k, v in out["res"].items() if v != "blocked"} == swallowed, out["res"]
    assert out["res"]["makedirs_exist_ok"] == "done"
    assert "noah-test-home-" in out["home_write"], out
    assert {"open", "os.remove", "os.rename", "os.mkdir", "os.rmdir", "shutil.rmtree",
            "os.utime", "os.chmod", "os.truncate", "os.symlink",
            "sqlite3.connect"} <= set(out["recorded"]), out["recorded"]
    after = sorted((p.relative_to(fake).as_posix(), p.stat().st_mtime_ns, p.stat().st_mode)
                   for p in fake.rglob("*"))
    assert after == before and (fake / "keep.txt").read_text() == "keep"
    assert (outside / "src").read_text() == "s"          # 옮기기(move_in)도 막혔다


_PYC = textwrap.dedent(r'''
    import importlib.util, json, pathlib, sys
    root, fake, mods = (pathlib.Path(a) for a in sys.argv[1:4])
    spec = importlib.util.spec_from_file_location("root_conftest_probe", root / "conftest.py")
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)
    sys.path.insert(0, str(mods))
    n0 = len(cf._HOME_WRITES)
    import noah_pyc_probe_mod                    # noqa: F401 — 가드가 걸린 뒤 처음 import 한다
    on_import = [w["path"] for w in cf._HOME_WRITES[n0:]]
    try:
        (fake / "a.json").write_text("{}")
        other = "done"
    except PermissionError:
        other = "blocked"
    pyc = sorted(p.name for p in pathlib.Path(sys.pycache_prefix).rglob("noah_pyc_probe_mod*"))
    print(json.dumps({"on_import": on_import, "pyc": pyc, "other": other}))
''')


def test_a_pycache_prefix_inside_the_home_does_not_fail_imports(tmp_path):
    """실제 인터프리터로 — 접두를 가짜 실제 홈 안에 두고 가드가 걸린 뒤 모듈을 처음 import 한다.
    `.pyc` 가 **실제로 써지고**(막지 않았다) 가드 기록은 0건이며, 같은 홈의 다른 쓰기는 여전히
    막힌다(#25). 옛 판은 import 가 막혀 기록이 남았다(`importlib` 가 그 예외를 삼키므로 import
    자체는 성공한다 — 그래서 fixture 가 그 테스트를 실패시켰다)."""
    fake, mods = tmp_path / "realhome", tmp_path / "mods"
    fake.mkdir()
    mods.mkdir()
    (mods / "noah_pyc_probe_mod.py").write_text("X = 1\n")
    pyc = fake / ".cache" / "pyc"
    env = dict(os.environ, NOAH_TEST_PROTECTED_HOME=os.pathsep.join([str(fake), *_protected()]))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    r = subprocess.run([sys.executable, "-X", f"pycache_prefix={pyc}", "-c", _PYC,
                        str(ROOT), str(fake), str(mods)],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["on_import"] == [], out
    assert out["pyc"] and all(n.endswith(".pyc") for n in out["pyc"]), out
    assert out["other"] == "blocked" and not (fake / "a.json").exists(), out


# ── ③ 중첩 pytest — 범인 지목 · 삼킨 예외 · 자식 · 수집 시점 ─────────────────
_E2E = textwrap.dedent(r'''
    import os, subprocess, sys
    from pathlib import Path
    import pytest
    FAKE = Path(os.environ["E2E_FAKE"])
    try:
        (FAKE / "stray.txt").write_text("x")     # 수집 시점 — 어느 테스트 창에도 안 든다
    except OSError:
        pass
    subprocess.run([sys.executable, "-c", "open(%r, 'w').write('x')" % str(FAKE / "early_child.txt")],
                   capture_output=True)          # 수집 시점 **자식** — 첫 테스트 창 앞에 적힌다


    @pytest.fixture(scope="session", autouse=True)
    def _late_writes():
        yield                                    # 마지막 테스트 창 **뒤** — 세션 끝에서만 보인다
        try:
            (FAKE / "late_inproc.txt").write_text("x")
        except OSError:
            pass
        subprocess.run([sys.executable, "-c",
                        "open(%r, 'w').write('x')" % str(FAKE / "late_child.txt")], capture_output=True)


    def test_swallowed_write():
        try:
            (FAKE / "direct.json").write_text("{}")
        except Exception:                        # 앱 코드처럼 삼킨다 — 그래도 실패해야 한다
            pass


    def test_delete():
        try:
            os.remove(FAKE / "keep.txt")
        except OSError:
            pass


    def test_child_write():
        subprocess.run([sys.executable, "-c",
                        "open(%r, 'w').write('x')" % str(FAKE / "child.txt")],
                       capture_output=True)


    def test_child_bare_env_write():             # 환경을 지운 자식(`env={…}`) — 세션이 구운 값으로 지킨다
        subprocess.run([sys.executable, "-c",
                        "open(%r, 'w').write('x')" % str(FAKE / "bare_child.txt")],
                       env={"PYTHONPATH": os.environ["PYTHONPATH"]}, capture_output=True)


    def test_home_and_env_are_isolated():
        assert Path.home().name.startswith("noah-test-home-")
        p = Path.home() / ".tradingagents" / "x.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
        assert "XDG_CACHE_HOME" not in os.environ          # 실제 홈을 가리키던 XDG 는 지운다
        assert "TRADE_DATA_DIR" not in os.environ          # 레포 이름공간 경로 변수도(리뷰 M3)
        assert os.environ.get("XDG_RUNTIME_DIR", "").endswith("run")   # *_HOME 이 아니면 홈 안이어도 둔다
        assert os.environ.get("XDG_DATA_HOME", "").endswith("xdg_data")   # 홈 밖을 가리키면 둔다
        assert os.environ.get("TRADINGAGENTS_DATA_DIR", "").endswith("ta_data")   # 홈 밖 → 둔다
        assert os.environ.get("TRADE_REL_PROBE", "").startswith("..")    # 상대경로는 대조 안 한다


    def test_tmp_write_passes(tmp_path):
        (tmp_path / "ok.txt").write_text("x")


    def test_dotenv_is_off(tmp_path):
        import dotenv
        e = tmp_path / ".env"
        e.write_text("NOAH_E2E_DOTENV=1\n")
        assert dotenv.load_dotenv(e) is False and "NOAH_E2E_DOTENV" not in os.environ
''')


def _nested(tmp_path, *args: str):
    """루트 conftest 를 `-p conftest` 로 주입해 임시 테스트를 돌린다 — 레포 밖에 두는
    이유는 `TestSysModulesLeakGuard20260921._run` 과 같다(#383·#328)."""
    fake = tmp_path / "realhome"
    fake.mkdir(exist_ok=True)
    (fake / "keep.txt").write_text("keep")
    ntmp = tmp_path / "ntmp"                     # 안쪽 세션의 TMPDIR — 끝나고 `noah-test-*` 가 안 남는지 본다
    ntmp.mkdir(exist_ok=True)
    f = tmp_path / "test_home_e2e.py"
    f.write_text(_E2E, encoding="utf-8")
    junit = tmp_path / "result.xml"
    outer_log = tmp_path / "outer_guard.tsv"
    env = dict(os.environ, E2E_FAKE=str(fake),
               NOAH_TEST_PROTECTED_HOME=os.pathsep.join([str(fake), *_protected()]),
               NOAH_TEST_HOME_GUARD_LOG=str(outer_log),
               XDG_CACHE_HOME=str(fake / ".cache"), XDG_RUNTIME_DIR=str(fake / "run"),
               XDG_DATA_HOME=str(tmp_path / "xdg_data"), TRADE_DATA_DIR=str(fake / ".trade"),
               TRADINGAGENTS_DATA_DIR=str(tmp_path / "ta_data"),
               TRADE_REL_PROBE=os.path.relpath(fake, ROOT), TMPDIR=str(ntmp))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "conftest",
                        "-p", "no:cacheprovider", f"--junitxml={junit}", *args, str(f)],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    res = {}
    if junit.exists():
        for tc in ET.parse(junit).iter("testcase"):
            kinds = [c.tag for c in tc if c.tag in ("failure", "error")]
            res[tc.get("name")] = kinds[0] if kinds else "passed"
    return r, res, fake, outer_log


def _stray_section(out: str) -> str:
    """세션 끝 '어느 테스트에도 속하지 않은' 보고 **그 절만** — 출력 전체에서 찾으면 테스트별
    보고가 대신 만족시킨다(#75). 없으면 빈 문자열."""
    lines = out.splitlines()
    i = next((k for k, ln in enumerate(lines) if "어느 테스트에도 속하지 않은" in ln), None)
    if i is None:
        return ""
    body = []
    for ln in lines[i + 1:]:
        if not ln.startswith("  "):
            break
        body.append(ln)
    return "\n".join(body)


def _no_leftover_temp(tmp_path) -> list:
    return sorted(p.name for p in (tmp_path / "ntmp").iterdir() if p.name.startswith("noah-test-"))


def test_a_nested_session_fails_the_culprits_and_keeps_the_home(tmp_path):
    """⚠️ 핵심 계약 — 앱 코드가 `PermissionError` 를 **삼켜도** 그 테스트가 실패한다(#12·#315).
    삭제(mtime 으로는 안 보이던 것, #417)·자식 프로세스 쓰기(#401)도 같다. 범인은 **테스트와
    줄**로 지목하고(#114), 가짜 실제 홈은 한 글자도 안 바뀐다. 반대 증거(#25): 홈에서 파생한
    경로·tmp_path 쓰기·XDG·`.env` 차단은 통과한다."""
    r, res, fake, outer_log = _nested(tmp_path)
    out = r.stdout + r.stderr
    assert res == {"test_swallowed_write": "error", "test_delete": "error",
                   "test_child_write": "error", "test_child_bare_env_write": "error",
                   "test_home_and_env_are_isolated": "passed",
                   "test_tmp_write_passes": "passed", "test_dotenv_is_off": "passed"}, (
        res, out[-4000:])
    assert r.returncode == 1, (r.returncode, out[-3000:])       # TESTS_FAILED — 중단(2)이 아니다
    for name in ("direct.json", "keep.txt", "child.txt"):
        assert name in out, (name, out[-4000:])
    # 호출 **줄**까지 지목한다 — 그 기록 줄 하나를 잘라서 본다. 출력 전체에서 찾으면 pytest 의
    # 노드 이름(`test_home_e2e.py::test_delete`)이 `…py:` 를 대신 만족시킨다(#75 · 뮤테이션 실측).
    line = next((ln for ln in out.splitlines() if "direct.json" in ln and "스레드" in ln), "")
    assert re.search(r"test_home_e2e\.py:\d+$", line.strip()), (line, out[-4000:])
    # 자식 줄은 이벤트·경로 순서로, 그 자식을 **띄운 테스트**까지 말한다(리뷰 L2)
    assert re.search(r"open \S*/child\.txt  \[자식 pid \d+ · 띄운 테스트 \S*test_child_write", out), (
        out[-4000:])
    # 창 밖 쓰기는 세션 끝에서 **따로** — 테스트 전(수집 시점 자식)·마지막 테스트 뒤(프로세스·자식).
    # 이미 테스트에 지목된 쓰기는 다시 안 센다(이중 보고면 범인이 둘로 보인다)
    stray = _stray_section(out)
    for name in ("stray.txt", "early_child.txt", "late_inproc.txt", "late_child.txt"):
        assert name in stray, (name, stray, out[-3000:])
    for name in ("direct.json", "keep.txt", "/child.txt", "bare_child.txt"):
        assert name not in stray, (name, stray)
    assert _no_leftover_temp(tmp_path) == [], _no_leftover_temp(tmp_path)   # 임시 홈·기록·자식 가드 정리
    assert sorted(p.name for p in fake.iterdir()) == ["keep.txt"], list(fake.iterdir())
    assert (fake / "keep.txt").read_text() == "keep"
    # 바깥 세션이 심은 자식 가드는 이 pytest 세션이 넘겨받았다 — 안 넘겨받으면 그 훅이 먼저
    # 막고 **바깥** 기록 파일에 적어, 이 세션은 범인을 못 본다(위 삼킨 예외가 초록이 된다).
    assert not outer_log.exists() or outer_log.read_text() == "", outer_log.read_text()


def test_a_write_outside_every_test_window_fails_the_session(tmp_path):
    """수집 시점(모듈 import)·테스트 사이 백그라운드 쓰기는 어느 테스트 탓도 아니다 — 엉뚱한
    테스트를 지목하지 않고 세션 끝에서 따로 말하되, **조용히 통과시키지 않는다**(#54·#114).
    통과하는 테스트 하나만 골라 돌려도 종료 코드가 0 이 아니어야 한다."""
    r, res, fake, _log = _nested(tmp_path, "-k", "test_tmp_write_passes")
    out = r.stdout + r.stderr
    assert res == {"test_tmp_write_passes": "passed"}, (res, out[-3000:])
    assert r.returncode == 1, (r.returncode, out[-3000:])
    stray = _stray_section(out)
    for name in ("stray.txt", "early_child.txt", "late_inproc.txt", "late_child.txt"):
        assert name in stray, (name, out[-3000:])
    assert sorted(p.name for p in fake.iterdir()) == ["keep.txt"], list(fake.iterdir())


# ── ④ 이 세션 ───────────────────────────────────────────────────────────────
def test_this_session_runs_with_a_temp_home_and_dotenv_off(tmp_path):
    """이 프로세스 자체가 격리돼 있다 — HOME 은 conftest 가 만든 임시 디렉터리이고 지킬 홈
    밖이며, `.env` 자동 로드는 꺼져 있고, 지킬 홈 아래 쓰기는 막는 판정이다(쓰지 않고 판정만
    본다 — 가드가 깨졌을 때 진짜 홈에 쓰면 안 된다). `.env` 는 **변수가 아니라 동작으로** 잰다 —
    옛 python-dotenv 는 그 변수를 모른다(리뷰 M1: 변수만 보던 이 검사는 1.0.1 에서도 통과했다)."""
    cf = _conftest()
    prot = _protected()
    assert prot, "지킬 실제 홈을 모른다(대조 0건, #54)"
    home = str(pathlib.Path.home())
    assert home == cf._HOME["home"] and pathlib.Path(home).name.startswith("noah-test-home-")
    assert not any(home == p or home.startswith(p + os.sep) for p in prot)
    assert os.environ.get("PYTHON_DOTENV_DISABLED") == "1"
    import dotenv
    e = tmp_path / ".env"
    e.write_text("NOAH_SESSION_DOTENV=1\n", encoding="utf-8")
    assert dotenv.load_dotenv(e) is False and "NOAH_SESSION_DOTENV" not in os.environ
    assert cf._HOME["dotenv"] in ("native", "patched"), cf._HOME["dotenv"]
    t = cf._HOME["target"]
    assert all(t("open", (os.path.join(p, ".tradingagents", "x"), "w", _W)) for p in prot)


def test_the_child_guard_runs_the_same_source():
    """자식 프로세스의 가드는 conftest 두 함수의 **소스 그대로**다 — 손으로 옮겨 적으면
    한쪽만 고쳐져 갈린다(#38)."""
    cf = _conftest()
    body = (pathlib.Path(cf._CHILD_GUARD_DIR) / "sitecustomize.py").read_text(encoding="utf-8")
    assert inspect.getsource(cf._home_write_guard) in body
    assert inspect.getsource(cf._child_home_guard_install) in body
    r = subprocess.run([sys.executable, "-c", "import sitecustomize as s; print(s.HOME_GUARD)"],
                       capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "True", (r.stdout, r.stderr[-2000:])


def test_two_guard_layers_do_not_chain_into_each_other(tmp_path):
    """중첩 세션이면 바깥·안쪽 세션의 가드 디렉터리가 **둘 다** `PYTHONPATH` 에 있다. 옛 판은
    서로를 이어 실행하다 `RecursionError` 가 났고 `site.py` 가 그걸 **삼켜** 손자 프로세스엔
    가드가 하나도 안 걸렸다 — 네트워크 가드도 #401 부터 같은 구멍이었다(실수 #421 의 ③ E2E 가
    잡았다). 우리 파일은 첫 줄 표식으로 가려 잇지 않고, 원래 sitecustomize 만 잇는다."""
    import shutil
    cf = _conftest()
    src = pathlib.Path(cf._CHILD_GUARD_DIR) / "sitecustomize.py"
    dirs = []
    for name in ("outer", "inner"):
        d = tmp_path / name
        d.mkdir()
        shutil.copy(src, d / "sitecustomize.py")
        dirs.append(str(d))
    code = ("import sitecustomize as s\n"
            "print(s.HOME_GUARD, sum(1 for o in s.OTHERS if 'outer' in o or 'inner' in o))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=os.pathsep.join(dirs)))
    assert r.stdout.split() == ["True", "0"], (r.stdout, r.stderr[-2000:])
    assert "Error in sitecustomize" not in r.stderr, r.stderr[-2000:]


# ── ⑤ 스위치를 모르는 옛 python-dotenv (리뷰 M1) ─────────────────────────────
# 1.0.1·1.1.1 엔 `PYTHON_DOTENV_DISABLED` 가 아예 없다(리뷰 실측 — `trade/requirements.txt` 가
# 1.0.1 을 고정하고 `~/stock-trade/.venv` 는 그걸로 만든다). 테스트 인터프리터는 1.2+ 라 그 판을
# 못 태우므로, 스위치가 **없는** 같은 API 의 대역을 쓴다(실물 1.0.1 설치본으로도 한 번 쟀다 —
# 옛 판에선 중첩 세션의 `test_dotenv_is_off` 가 실패했고 고친 판에선 통과했다, 2026-09-27).
_OLD_DOTENV = {
    "dotenv/__init__.py": "from .main import load_dotenv, dotenv_values\n",
    "dotenv/main.py": textwrap.dedent('''
        import os


        def dotenv_values(dotenv_path=None, **kw):
            out = {}
            with open(dotenv_path, encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    if k:
                        out[k] = v
            return out


        def load_dotenv(dotenv_path=None, stream=None, verbose=False, override=False, **kw):
            for k, v in dotenv_values(dotenv_path).items():
                if override or k not in os.environ:
                    os.environ[k] = v
            return True
    '''),
}

_DOTENV_CHILD = textwrap.dedent(r'''
    import importlib.util, json, os, pathlib, subprocess, sys
    root, envfile = pathlib.Path(sys.argv[1]), sys.argv[2]
    spec = importlib.util.spec_from_file_location("root_conftest_probe", root / "conftest.py")
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)
    import dotenv
    here = [dotenv.load_dotenv(envfile), os.environ.get("NOAH_E2E_OLD_DOTENV")]
    # 같은 프로세스에서 conftest 를 **한 번 더** — 중첩 세션처럼 이미 갈아 끼운 판을 만나도
    # 'native' 로 잘못 읽지 않고 제 자식 가드에 패치를 싣는다
    spec2 = importlib.util.spec_from_file_location("root_conftest_probe2", root / "conftest.py")
    cf2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(cf2)
    inner = [cf2._HOME["dotenv"], "_dotenv_force_switch" in
             (pathlib.Path(cf2._CHILD_GUARD_DIR) / "sitecustomize.py").read_text(encoding="utf-8")]
    code = ("import dotenv, json, os; print(json.dumps([dotenv.load_dotenv(%r), "
            "os.environ.get('NOAH_E2E_OLD_DOTENV'), dotenv.__file__]))" % envfile)
    g = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    print(json.dumps({"switch": cf._HOME["dotenv"], "from": dotenv.__file__, "here": here,
                      "inner": inner, "grandchild": g.stdout.strip(), "err": g.stderr[-800:]}))
''')


def test_an_old_dotenv_without_the_switch_is_turned_off_here_and_in_children(tmp_path):
    """스위치를 모르는 판이면 **이 프로세스**에선 conftest 가, **자식**에선 그 세션이 심은
    sitecustomize 가 `load_dotenv` 를 스위치를 따르는 판으로 갈아 끼운다(같은 소스, #38).
    판별은 판 번호가 아니라 동작이다(#25). 반대 증거: 대역은 정말 스위치를 모른다."""
    import importlib.util
    pkg = tmp_path / "olddotenv"
    for rel, src in _OLD_DOTENV.items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(src, encoding="utf-8")
    envfile = tmp_path / "e.env"
    envfile.write_text("NOAH_E2E_OLD_DOTENV=1\n", encoding="utf-8")
    # 반대 증거 — 대역을 그대로 부르면 스위치가 켜져 있어도(이 세션) 읽어 들인다
    spec = importlib.util.spec_from_file_location("old_dotenv_probe", pkg / "dotenv" / "main.py")
    raw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(raw)
    try:
        assert os.environ.get("PYTHON_DOTENV_DISABLED") == "1"
        assert raw.load_dotenv(str(envfile)) is True and os.environ["NOAH_E2E_OLD_DOTENV"] == "1"
    finally:
        os.environ.pop("NOAH_E2E_OLD_DOTENV", None)
    env = dict(os.environ)
    # 가드 디렉터리(sitecustomize)는 맨 앞 그대로, 대역은 **그 바로 뒤** — 다른 dotenv 가 경로에
    # 먼저 있으면 자식이 그걸 가져가 대역을 못 잰다(실물 1.0.1 을 경로에 얹은 재현에서 실측)
    parts = [x for x in env.get("PYTHONPATH", "").split(os.pathsep) if x]
    env["PYTHONPATH"] = os.pathsep.join(parts[:1] + [str(pkg)] + parts[1:])
    r = subprocess.run([sys.executable, "-c", _DOTENV_CHILD, str(ROOT), str(envfile)],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["from"].startswith(str(pkg)), out                   # 대역이 실제로 쓰였다
    assert out["switch"] == "patched", out
    assert out["here"] == [False, None], out
    assert out["inner"] == ["patched", True], out
    g = json.loads(out["grandchild"] or "null")
    assert g and g[:2] == [False, None] and g[2].startswith(str(pkg)), out
