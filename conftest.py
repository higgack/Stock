"""레포 루트 conftest — 테스트 프로세스가 **운영 환경을 못 건드리게** 한다.

세 겹이다: ① 실제 홈 격리(HOME → 세션 임시 디렉터리 + 실제 홈 쓰기 가드)
② 바깥 원천 차단 ③ `sys.modules` 오염 감지.

`tests/conftest.py` 에 두면 `pytest bot/tests` 처럼 그 디렉터리를 단독으로
수집하는 실행에서 차단이 통째로 안 걸린다(2026-09-11 독립 리뷰 실측:
`pytest bot/tests` → `Session.request` 가 pristine). pytest.ini 의
`testpaths = tests bot/tests` 는 수집 순서 덕에 우연히 걸렸을 뿐이다 —
우연에 기대지 말고 **루트**에 둔다(#24 어느 목록에도 안 드는 자리).
"""
# ── 실제 홈 격리 (실수 #421) ────────────────────────────────────────────────
# 2026-09-27 실측(테스트마다 쓰기·삭제·이름변경을 감사 훅으로 기록한 일회성 진단,
# 세 트리 전부): **90개 테스트가 운영 디렉터리에 131가지** 쓰기를 했다 — 가계부
# `budget.json` 을 갈아 쓰고, 모의투자 `portfolio.json`·`audit.jsonl` 에 가짜 체결을
# 남기고, `paper/auto_audit.jsonl` 을 **지우고**, 캐시 mtime 을 밀어(`os.utime`) 운영
# TTL 을 늘렸다. `~/.trade` 에도 `run_ledger.json`·`.scan_notified.json`·공유 페이지를
# 썼다. 그전까지의 방어는 캐시 상수를 **하나씩** 갈아 끼우는 목록(17줄)이었는데 `bot/`
# 에만 홈을 가리키는 상수가 131개다 — 목록은 누가 오염을 **우연히** 발견할 때만
# 자랐다(#24·#30·#312·#344·#384·#417).
#
# 구조로 막는다(#119) — 두 겹:
#   ① **HOME 을 세션 임시 디렉터리로 옮긴다**(import 시점, 되돌리지 않는다). 레포의 홈
#      경로는 전부 `Path.home()`·`expanduser("~")` 에서 오고 이 conftest 가 어떤 레포
#      모듈보다 먼저 import 되므로, 모듈 상수까지 통째로 따라온다. 자식 프로세스는 환경을
#      물려받는다. `.env` 자동 로드(`load_dotenv`)도 끈다 — `TRADE_DATA_DIR` 같은 절대경로가
#      들어오면 ①을 우회한다(#294 운영 .env 를 읽는 시한폭탄과 같은 뿌리). 스위치를 모르는 옛
#      python-dotenv 는 **동작으로** 재서 갈아 끼운다. 셸에서 실제 홈을 가리키는 경로 지정 변수
#      (XDG 기본 디렉터리·`TRADE_*`·`TRADINGAGENTS_*`)도 지운다.
#   ② **그래도 실제 홈 아래를 쓰면 막고 그 테스트를 실패시킨다**(감사 훅). 절대경로·
#      환경변수·`pwd` 로 실제 홈을 가리키는 코드는 ①을 우회하므로 여기서 잡는다.
#      ⚠️ 막기만 하면 안 보인다 — 이 레포엔 `except Exception: pass` 가 흔해서 앱 코드가
#      그 예외를 삼키면 테스트는 초록이다(#12·#315). 그래서 기록해 두고 **fixture 가**
#      실패시킨다(삼킬 수 없는 자리). 자식 프로세스는 sitecustomize 가 같은 소스로 막고
#      기록 파일에 적는다(#401 자식은 부모의 그물을 빠져나간다).
#
# ⚠️ 못 보는 축(#274): 레포 체크아웃 **안**의 쓰기(VM 에선 그게 운영 NOAH 체크아웃이다 —
# `.pytest_cache`·`__pycache__` 가 거기 생겨 막을 수 없다) · C 확장이 직접 여는 파일(sqlite 는
# **연결한 파일만** 잡는다 — `ATTACH DATABASE`·`VACUUM INTO` 가 여는 파일과 3.12 이하 `dbm` 은
# 못 본다) · 감사 이벤트가 없는 연산(`os.mkfifo`·`os.mknod`) · `dir_fd` 상대 경로 · 홈 **밖**에 둔
# 링크를 거친 쓰기(심볼릭 링크, 그리고 세션 **전부터** 있던 하드 링크 — 세션 중에 홈 안 파일에
# 거는 하드 링크는 막는다) · 파이썬이 아닌 자식(git·curl)과 `PYTHONPATH` 까지 지운 자식(`clear=True`
# 창에서 띄운 자식 포함 — 아래 자식 가드의 못 보는 축과 같다) · 이 conftest 가 import 되기 **전**의
# 코드(진입점 플러그인)와 `python -m unittest`·`--noconftest` 실행 · 세션이 끝난 뒤(atexit) 쓰기와
# 우리보다 나중에 도는 플러그인의 `pytest_sessionfinish` 쓰기(막히지만 보고되지 않는다) · 지킬 홈이
# 레포·임시 디렉터리와 **같은** 자리(`TMPDIR=$HOME` — 같은 길이면 허용이 이긴다) · 읽기(실제 홈을
# **읽는** 테스트는 이 가드가 잡지 않는다 — ①이 경로를 옮겨 줄 뿐이다).
import os as _os
import pathlib as _pathlib
import sys as _sys

_REPO_ROOT = str(_pathlib.Path(__file__).resolve().parent)
# 자식 프로세스가 물려받는 약속 — 바깥 세션이 정하면 중첩 세션도 그 홈을 그대로 지킨다.
_PROTECTED_ENV = "NOAH_TEST_PROTECTED_HOME"   # 쓰면 안 되는 홈(os.pathsep 로 여럿)
_ALLOWED_ENV = "NOAH_TEST_HOME_ALLOWED"       # 그 안에서도 쓸 수 있는 곳(레포·임시)
_GUARD_LOG_ENV = "NOAH_TEST_HOME_GUARD_LOG"   # 자식이 막은 쓰기를 적는 파일
_CHILD_GUARD_MARK = "# noah-test-child-guard"  # 자식 가드 sitecustomize 의 첫 줄 — 서로를 잇지 않게


def _home_write_guard(protected, allowed, record):
    """실제 홈 아래 쓰기를 막는 감사 훅 — `(hook, target)` 을 돌려준다.

    `target(event, args)` 는 순수 판정(막을 경로 또는 None)이고, `hook` 은 그걸 불러
    `record(event, path)` 한 뒤 `PermissionError` 로 막는다. 경로마다 **가장 긴** 일치
    루트가 이긴다 — 레포가 홈 안에 있어도(VM `~/stock`) 레포는 허용이고, 허용된 임시
    디렉터리 안에 둔 가짜 홈(회귀)은 막힌다. 길이가 같으면 허용이 이긴다.

    ⚠️ **자기 완결**이어야 한다(바깥 이름을 안 쓴다) — 자식 프로세스의 sitecustomize
    가 이 함수의 **소스를 그대로** 실행한다(규칙을 두 벌로 적으면 갈린다, #38).
    던지는 예외는 운영체제가 막을 때와 같은 타입이다(`OSError` 계열 — `except OSError`
    호출부의 동작이 안 바뀐다, 바깥 원천 차단과 같은 규약).
    """
    import errno
    import os
    import sys
    import threading

    sep = os.sep

    def _abs(x):
        a = os.path.abspath(x)
        # POSIX `normpath` 는 선두 `//` **두 개만** 남긴다 — 리눅스에선 `/` 와 같은 자리라 접는다.
        # 안 접으면 `//root/x` 가 실제 홈 `/root/x` 를 쓰면서 루트 비교를 빠져나간다(2026-09-27 실측).
        return a[1:] if sep == "/" and a.startswith("//") else a

    def _roots(xs):
        out = set()
        for x in xs:
            if x:
                a = _abs(os.fspath(x))
                out.update((a, os.path.realpath(a)))   # 링크 홈(`/home/x`→`/data/x`)은 두 표기로
        out.discard(sep)                        # "/" 를 지키면 모든 쓰기가 막힌다(허용도 마찬가지)
        return out

    prot, allow = _roots(protected), _roots(allowed)
    # 허용 루트가 링크 홈의 **실경로** 안이면 링크 표기로도 허용한다 — 레포 루트는 `resolve()`
    # 로 구해 실경로만 아는데, 홈 안의 레포(VM `~/stock`)는 링크 표기로도 불린다.
    for p in list(prot):
        rp = os.path.realpath(p)
        allow.update(p + a[len(rp):] for a in list(allow) if rp != p and a.startswith(rp + sep))
    roots = sorted([(len(r), 0, r) for r in prot] + [(len(r), 1, r) for r in allow], reverse=True)
    # `PYTHONPYCACHEPREFIX`(`-X pycache_prefix`)면 `.pyc` 가 `__pycache__` 없이 그 접두 아래에
    # 모인다 — 같은 인터프리터 캐시다(2026-09-27 실측: 접두가 실제 홈 안이면 세션에서 처음
    # import 하는 모듈마다 막혀, 그 테스트가 실패했다).
    pyc = _abs(sys.pycache_prefix) if sys.pycache_prefix else None
    wflags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    # 이벤트 → ((경로 인자 위치, 그 경로의 dir_fd 인자 위치), …) — CPython 3.11 실측 모양.
    # `shutil.copyfile`·`move`·`copytree` 는 안에서 아래 이벤트를 다시 내므로 뺀다.
    # 하드 링크는 **원본**도 본다 — 홈 안 파일에 홈 밖 이름을 붙이면 그 이름으로 쓴 것이 홈에 닿는다.
    table = {
        "open": ((0, None),),
        "os.remove": ((0, 1),), "os.rmdir": ((0, 1),), "os.mkdir": ((0, 2),),
        "os.rename": ((0, 2), (1, 3)), "os.link": ((0, 2), (1, 3)), "os.symlink": ((1, 2),),
        "os.utime": ((0, 3),), "os.chmod": ((0, 2),), "os.chown": ((0, 3),),
        "os.truncate": ((0, None),), "shutil.rmtree": ((0, 1),),
        "os.setxattr": ((0, None),), "os.removexattr": ((0, None),),
        "sqlite3.connect": ((0, None),),
    }
    busy = threading.local()

    def _path(raw, event):
        if raw is None or isinstance(raw, int):
            return None                          # 파일 디스크립터
        try:
            s = os.fsdecode(os.fspath(raw))
            if event == "sqlite3.connect":
                if s.startswith("file:"):
                    s, _, q = s[5:].partition("?")
                    if "mode=ro" in q or "mode=memory" in q or "immutable=1" in q:
                        return None
                    s = s.partition("#")[0]
                    if s.startswith("//"):               # file://<권한>/경로 — sqlite 는 빈 권한·localhost 만 연다
                        host, sl, rest = s[2:].partition("/")
                        if host not in ("", "localhost"):
                            return None
                        s = sl + rest
                    if "%" in s:                         # sqlite 가 %HH 를 풀어 그 이름으로 만든다(3.10~3.13 실측)
                        import urllib.parse
                        s = urllib.parse.unquote(s)
                if not s or s == ":memory:":
                    return None
            return _abs(s)
        except Exception:                        # noqa: BLE001 — cwd 가 사라진 경우 등
            return None

    def target(event, args):
        spec = table.get(event)
        if spec is None:
            return None
        if event == "open" and not (len(args) > 2 and isinstance(args[2], int)
                                    and args[2] & wflags):
            return None                          # 읽기
        for pi, di in spec:
            if pi >= len(args):
                continue
            if di is not None and di < len(args):
                fd = args[di]
                if isinstance(fd, int) and fd != -1:
                    continue                     # dir_fd 상대 경로 — 어디인지 모른다
            p = _path(args[pi], event)
            if (p is None or f"{sep}__pycache__{sep}" in p + sep   # 그 디렉터리를 만드는 것까지
                    or (pyc is not None and (p == pyc or p.startswith(pyc + sep)))):
                continue                         # 인터프리터의 .pyc 캐시
            for _n, kind, r in roots:
                if p == r or p.startswith(r if r.endswith(sep) else r + sep):
                    if kind == 0:
                        return p
                    break
        return None

    def hook(event, args):
        if event not in table or getattr(busy, "on", False):
            return
        busy.on = True
        hit = None
        try:
            hit = target(event, args)
            if hit is not None:
                try:
                    record(event, hit)
                except Exception:                # noqa: BLE001 — 기록 실패가 막기를 못 막는다
                    pass
        finally:
            busy.on = False
        if hit is not None:
            raise PermissionError(
                errno.EACCES,
                "[conftest] 테스트가 실제 홈에 쓰려 해서 막았습니다 — HOME 은 임시 디렉터리"
                f"인데 이 경로는 그걸 우회했습니다({event} · 루트 conftest `_home_write_guard`)",
                hit)

    return hook, target


def _child_home_guard_install(guard, state, names, baked=((), (), "")) -> bool:
    """자식 프로세스에서 **sitecustomize 가** 부른다 — 부모가 물려준 환경으로 같은 가드를 건다.

    ⚠️ 자기 완결이어야 한다(소스를 그대로 옮겨 실행한다). 막은 쓰기는 부모가 정한 기록
    파일에 한 줄로 적는다 — 부모 세션의 fixture 가 그 줄로 범인 테스트를 지목한다.
    `state["on"]` 은 이 자식이 **pytest 세션**이면 그 세션의 conftest 가 꺼서 넘겨받는다
    (안 그러면 이 훅이 먼저 막고 **바깥** 기록 파일에 적어, 그 세션이 범인을 못 본다).
    환경에 그 값이 없으면(테스트가 `clear=True` 창·`env={…}` 로 지웠다) 이 파일을 심은 세션이
    **구워 둔** `baked`(보호·허용·기록)를 쓴다 — 안 그러면 그 자식은 가드도 기록도 없이 실제 홈에
    썼다(2026-09-27 독립 리뷰 L1 실측). `PYTHONPATH` 까지 지운 자식은 이 파일을 아예 못 읽는다.
    """
    import os
    import sys

    prot_env, allow_env, log_env = names
    env = os.environ
    prot = [p for p in env.get(prot_env, "").split(os.pathsep) if p] or list(baked[0])
    if not prot:
        return False
    allow = [p for p in env.get(allow_env, "").split(os.pathsep) if p] or list(baked[1])
    log = env.get(log_env, "") or baked[2]

    def record(event, path):
        if not log:
            return
        fields = (str(os.getpid()), event, path, env.get("PYTEST_CURRENT_TEST", ""),
                  " ".join(sys.argv)[:200])
        line = "\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in fields)
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    hook, _target = guard(prot, allow, record)

    def gated(event, args):
        if state["on"]:
            hook(event, args)

    sys.addaudithook(gated)
    return True


def _real_homes(env) -> list:
    """지켜야 할 실제 홈 — 바깥 세션이 정했으면 그 값(중첩 세션), 아니면 `$HOME`. 계정 홈은 **늘** 더한다.

    ⚠️ `$HOME` 을 바꿔 띄운 셸이어도 `pwd` 의 계정 홈엔 운영 데이터가 있다. 바깥 값이 셸에 **낡은 채**
    남아 있어도(리뷰 L6) 계정 홈은 빠지지 않는다 — 중첩 세션에선 바깥이 이미 넣은 값이라 같다.
    """
    given = [p for p in env.get(_PROTECTED_ENV, "").split(_os.pathsep) if p]
    out = given or ([env["HOME"]] if env.get("HOME") else [])
    try:
        import pwd
        out.append(pwd.getpwuid(_os.getuid()).pw_dir)
    except Exception:                                   # noqa: BLE001 — 비-POSIX
        pass
    return out


def _protected_homes(env) -> list:
    """내보낼 보호 루트 — 정규화하고 `/` 는 뺀다. 가드는 `/` 를 루트로 안 받는데(모든 쓰기가 막힌다)
    그대로 내보내면 자식·검사가 '지킨다' 고 읽는다(리뷰 L4 실측: 컨테이너처럼 `HOME=/` 면 빨간불)."""
    return sorted({_os.path.abspath(p) for p in _real_homes(env)} - {_os.sep})


# 레포가 읽는 **경로 지정 변수**의 이름공간 — 가드가 막을 자리를 가리키면 세션 시작에 지운다(그 변수를
# 읽는 코드가 임시 홈 기본값으로 돌아간다). 리뷰 M3 실측: `TRADE_DATA_DIR` 가 실제 홈을 가리키면
# trade/tests 99건이 빨간불(쓰기는 막혔다). 새 경로 변수가 이 밖에 생기면 회귀가 잡는다(#24).
_DIR_VAR_PREFIXES = ("TRADE_", "TRADINGAGENTS_")


def _home_dir_var(name: str) -> bool:
    """세션 시작에 값을 대조할 변수인가 — XDG 기본 디렉터리(`*_HOME`) · 레포 이름공간."""
    return (name.startswith("XDG_") and name.endswith("_HOME")) or name.startswith(_DIR_VAR_PREFIXES)


def _dotenv_force_switch() -> None:
    """`load_dotenv` 가 `PYTHON_DOTENV_DISABLED` 를 따르게 갈아 끼운다(스위치가 꺼져 있으면 원래대로).

    python-dotenv 1.2 부터 그 스위치를 스스로 알고, 1.0.1·1.1.1 엔 그 이름이 아예 없다(리뷰 M1 실측 —
    `trade/requirements.txt` 가 1.0.1 을 고정한다). ⚠️ 자기 완결 — 자식 sitecustomize 가 소스 그대로
    실행한다(#38). 이미 갈아 끼운 판은 다시 감싸지 않는다(중첩 세션).
    """
    import os
    import dotenv
    import dotenv.main as main

    if getattr(main.load_dotenv, "_noah_switch", False):
        return
    real = main.load_dotenv

    def load_dotenv(*args, **kwargs):
        if os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in {"1", "true", "t", "yes", "y"}:
            return False
        return real(*args, **kwargs)

    load_dotenv._noah_switch = True
    dotenv.load_dotenv = main.load_dotenv = load_dotenv


def _dotenv_switch() -> str:
    """이 인터프리터의 `load_dotenv` 가 스위치를 따르나 — 판 번호가 아니라 **동작으로** 잰다(#25).

    임시 `.env` 를 실제로 읽혀 본다. 'native'(스스로 안다) · 'patched'(옛 판 — 갈아 끼웠다, 자식도
    같이) · 'absent'(설치 안 됨). ⚠️ `PYTHON_DOTENV_DISABLED` 를 켠 **뒤에** 부를 것.
    """
    try:
        import dotenv
    except Exception:                                   # noqa: BLE001
        return "absent"
    if getattr(dotenv.load_dotenv, "_noah_switch", False):
        return "patched"                                # 바깥 세션의 자식 가드가 이미 갈아 끼웠다
    import tempfile
    key = "NOAH_TEST_DOTENV_PROBE"
    fd, path = tempfile.mkstemp(prefix="noah-test-dotenv-", suffix=".env")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "=1\n")
        try:
            honored = dotenv.load_dotenv(path) is False and key not in _os.environ
        except Exception:                               # noqa: BLE001 — 못 재면 갈아 끼운다(안전한 쪽)
            honored = False
    finally:
        _os.environ.pop(key, None)
        _os.unlink(path)
    if honored:
        return "native"
    _dotenv_force_switch()
    return "patched"


_HOME_WRITES: list = []      # 이 프로세스가 막은 쓰기 — fixture 가 테스트별로 읽는다


def _record_home_write(event, path) -> None:
    """막은 쓰기를 적는다 — **어느 테스트가 · 어느 줄에서** 를 같이(#114 범인 지목)."""
    import threading
    where, outside = [], None
    here = _os.path.abspath(__file__)
    try:
        f = _sys._getframe(2)                   # 0 = 여기 · 1 = 훅 · 2 = 쓰기를 부른 자리
        while f is not None and len(where) < 3:
            raw = f.f_code.co_filename
            fn = "" if raw.startswith("<") else _os.path.abspath(raw)   # <frozen …> 은 파일이 아니다
            if fn and fn != here:
                if (fn.startswith(_REPO_ROOT + _os.sep)
                        and f"{_os.sep}.venv{_os.sep}" not in fn):
                    where.append(f"{_os.path.relpath(fn, _REPO_ROOT)}:{f.f_lineno}")
                elif outside is None and f"{_os.sep}lib{_os.sep}python" not in fn:
                    outside = f"{fn}:{f.f_lineno}"          # 레포 밖 테스트 파일(회귀의 임시 파일)
            f = f.f_back
    except Exception:                                   # noqa: BLE001
        pass
    if not where and outside:
        where = [outside]
    _HOME_WRITES.append({"event": event, "path": path, "where": where,
                         "test": _os.environ.get("PYTEST_CURRENT_TEST", ""),
                         "thread": threading.current_thread().name})


def _isolate_home() -> dict:
    """HOME 을 세션 임시 디렉터리로 옮기고 실제 홈 쓰기 가드를 건다 — **import 시점에 한 번**.

    ⚠️ fixture 가 아니다(되돌리지 않는다): 레포 모듈은 홈 경로를 **import 시점에** 상수로
    굳히고(`_CACHE_DIR = Path.home() / …`), 렌더가 띄운 daemon 스레드는 teardown 뒤에도
    쓴다(#312·#344 — 바깥 원천 차단이 세션 스코프인 것과 같은 이유).
    """
    import atexit
    import shutil
    import tempfile

    env = _os.environ
    protected = _protected_homes(env)
    home = tempfile.mkdtemp(prefix="noah-test-home-")
    atexit.register(shutil.rmtree, home, True)
    env["HOME"] = home
    env["PYTHON_DOTENV_DISABLED"] = "1"
    logdir = tempfile.mkdtemp(prefix="noah-test-homeguard-")
    atexit.register(shutil.rmtree, logdir, True)
    allowed = [_REPO_ROOT, home, tempfile.gettempdir()]
    info = {"protected": protected, "allowed": allowed, "home": home,
            "log": _os.path.join(logdir, "blocked.tsv")}
    info["dotenv"] = _dotenv_switch()   # 스위치를 모르는 옛 판이면 갈아 끼운다(자식도, 리뷰 M1)
    hook, info["target"] = _home_write_guard(protected, allowed, _record_home_write)
    for k in [k for k in env if _home_dir_var(k)]:
        v = env[k]
        if v and _os.path.isabs(v) and info["target"]("os.mkdir", (v, 0o777, -1)):
            del env[k]      # 가드가 막을 자리 — 기본값($HOME/…)이 곧 임시 홈이다(가드와 한 판정, #38)
    env[_PROTECTED_ENV] = _os.pathsep.join(protected)
    env[_ALLOWED_ENV] = _os.pathsep.join(allowed)
    env[_GUARD_LOG_ENV] = info["log"]
    # 중첩 세션(회귀가 pytest 를 자식으로 띄운다): 바깥 세션이 심은 자식 가드가 이 프로세스
    # 에도 걸려 있다 — 이 세션의 가드가 넘겨받는다(`_child_home_guard_install` 독스트링).
    state = getattr(_sys.modules.get("sitecustomize"), "NOAH_HOME_GUARD", None)
    if isinstance(state, dict):
        state["on"] = False
    _sys.addaudithook(hook)
    return info


_HOME = _isolate_home()


# ── 바깥 원천 차단 ──────────────────────────────────────────────────────────
# 2026-09-11 실측: `make test` 한 번이 **409건**을 22개 호스트로 내보내고 있었다
# (stock.naver.com 84 · sec.gov 70 · finance.naver.com 49 · openapi.koreainvestment
# **인증 토큰** 15 …). 개별 테스트가 스텁을 빠뜨리면 조용히 진짜 원천을 치고,
# 렌더가 띄운 **daemon 스레드**는 mock 이 풀린 뒤까지 살아 친다(#312 실측).
# 결과: 남의 레이트리밋을 태우고, 과금 경로(LLM 번역)를 밟고, 운영 캐시를
# 오염시킨다(#30). 테스트마다 monkeypatch 하는 건 목록형 방어라 새 테스트를
# 못 잡는다(#24) — 여기서 한 번에 막는다.
#
# ⚠️ 던지는 예외는 **원천이 실패할 때와 같은 타입**이어야 한다. 그래야 3,786개
# 테스트의 동작이 안 바뀐다(샌드박스는 프록시 403 으로 이미 전부 실패 경로를
# 타며 green — 그게 이 차단이 무해하다는 실측 증거다). 새 타입으로 던지면
# `except requests.RequestException` 만 잡는 호출부가 통째로 터진다.
#
# ⚠️ yfinance 1.6 은 **curl_cffi** 를 우선 쓴다(requests 만 막으면 야후가 샌다) —
# 두 전송 계층을 다 막고, 그 사실을 회귀가 값으로 고정한다(#25 '있다'만 묻는
# 검사는 눈이 먼다).
# ⚠️ **세션 스코프**다(테스트마다 monkeypatch 가 아니라). 렌더가 띄운 daemon
# 스레드는 fixture teardown **뒤에** 원천을 치므로, 테스트 끝에 원래 함수로
# 복원하면 그 한 건이 그대로 빠져나간다 — 실측으로 확인했다(테스트별 차단:
# 164건 → **1건**이 남았고 그게 `TestFavoritesPagination20260616` 의 백그라운드
# 갱신이었다. 세션 스코프로 올리자 0건). 복원하지 않으므로 개별 테스트가
# 자기 스텁을 걸었다 풀어도 **우리 차단으로** 돌아온다.
import re as _re

_BLOCKED: list = []      # 차단된 URL — 진단용. 차단 자체는 조용하지 않다(예외).


def _blocked_url(url) -> str:
    """예외 문구·`_BLOCKED` 에 실을 **안전한** 형태로 줄인다.

    이 문자열은 pytest 트레이스백과 CI 로그에 그대로 실린다 — §Secrets 키값
    echo 금지. 쿼리스트링만 떼면 부족하다: 이 레포는 텔레그램 토큰을 **경로**에
    박는 호출부가 20곳이다(`https://api.telegram.org/bot{token}/sendMessage` —
    bot/portfolio_watch·watchlist·reddit_insider_watch·daily_kr_flow·
    us_market_daily·trade/scripts/*). 2026-09-11 독립 리뷰가 실측으로 보였다.
    그래서 **구조로** 줄인다 — scheme://host + 경로(비밀 조각은 마스킹).
    """
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url))
    except Exception:                                   # noqa: BLE001
        return "<unparsable-url>"
    if not u.scheme and not u.netloc:                   # 상대경로·이상값
        return str(url).split("?")[0]
    host = (u.hostname or "") + (f":{u.port}" if u.port else "")
    path = _redact_path(u.path or "")
    return f"{u.scheme}://{host}{path}" if u.scheme else f"//{host}{path}"


# 경로 조각이 비밀로 보이면 가린다. 이름 열거가 아니라 **모양**으로 본다(#24) —
# 새 원천이 `/v1/<apikey>/…` 로 와도 걸린다.
_SECRET_SEG = _re.compile(
    r"^(?:bot\d+:\S+"                       # 텔레그램 `bot<id>:<token>`
    r"|[A-Za-z0-9_\-]{24,}"                  # 길고 구분자 없는 토막 = 키/토큰
    r"|[A-Za-z0-9+/=%]{40,})$")              # base64·URL 인코딩된 키


def _redact_path(path: str) -> str:
    out = []
    for seg in path.split("/"):
        if seg.startswith("bot") and ":" in seg:
            out.append("bot***")
        elif _SECRET_SEG.match(seg):
            out.append("***")
        else:
            out.append(seg)
    return "/".join(out)


def _install_outbound_block() -> list:
    """두 전송 계층을 막고 **막은 이름 목록**을 돌려준다(회귀가 값으로 본다).

    curl_cffi 가 없으면 그 항목이 빠진다 — '있다'만 묻지 말고 무엇을 막았는지
    세어야 눈이 안 먼다(#25·#54).
    """
    import requests
    import requests.sessions as _rs

    def _deny(self, method, url, *a, **k):
        u = _blocked_url(url)
        _BLOCKED.append(u)
        raise requests.exceptions.ConnectionError(
            f"[conftest] 테스트가 바깥 원천을 쳤습니다: {method} {u} — "
            "그 호출을 스텁하거나, 렌더가 띄우는 백그라운드 킥을 막으세요"
            "(tests/conftest.py `_install_outbound_block`)")

    _rs.Session.request = _deny
    out = ["requests"]
    try:                                   # yfinance 1.6+ 의 기본 전송 계층
        from curl_cffi import requests as _cc

        def _deny_cc(self, method, url, *a, **k):
            u = _blocked_url(url)
            _BLOCKED.append(u)
            raise _cc.errors.RequestsError(
                f"[conftest] 테스트가 바깥 원천을 쳤습니다(curl_cffi): {method} {u}")

        _cc.Session.request = _deny_cc
        out.append("curl_cffi")
    except Exception:                       # noqa: BLE001 — 미설치 환경
        pass
    return out


def _install_socket_backstop() -> bool:
    """마지막 그물 — **전송 계층을 열거하지 않는다**(#24).

    위 두 패치는 `requests`·`curl_cffi` 라는 **이름 목록**이라, 새 전송 계층이
    들어오면 조용히 샌다. 2026-09-11 독립 리뷰 실측: `bot/` 에 httpx 20곳 ·
    `urllib.request` 8곳이 이미 있고(오늘은 httpx 미설치라 안 타지만 VM 엔
    langchain 경유로 깔릴 수 있다), `Session.send`·`aiohttp`·raw socket 은
    애초에 위 패치가 못 본다. 소켓에서 막으면 **이 프로세스 안에서는** 어느
    라이브러리든 걸린다 — **자식 프로세스는 못 본다**(2026-09-22 실측).
    그 축은 `_install_child_backstop` 이 맡는다.

    루프백은 통과시킨다 — `tests/test_dashboard_gzip.py` 가 자기 서버를 띄워
    127.0.0.1 로 친다(실측: 전 세션에서 비-루프백 유출 0건 · 루프백만 있었다).
    """
    import socket

    def _is_local(host) -> bool:
        h = str(host or "")
        return (h in ("localhost", "", "::1") or h.startswith("127.")
                or h.startswith("::ffff:127."))

    real_connect = socket.socket.connect

    def _guard(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_local(host):
            _BLOCKED.append(f"socket://{host}")
            raise OSError(
                f"[conftest] 테스트가 바깥 원천을 쳤습니다(socket): {host} — "
                "그 호출을 스텁하거나, 렌더가 띄우는 백그라운드 킥을 막으세요"
                "(tests/conftest.py `_install_socket_backstop`)")
        return real_connect(self, address, *a, **k)

    socket.socket.connect = _guard
    return True


def _install_child_backstop() -> str:
    """자식 프로세스 축 — **위 소켓 패치와 홈 가드는 이 프로세스만 덮는다**.

    2026-09-22 독립 리뷰 실측: `trade/tests/test_jp_master_probe.py` 가
    `subprocess.run([py, "-c", …yfinance…])` 로 자식을 띄워 `make test` 한
    번에 Yahoo ~6 · JPX 9 요청이 나갔는데 위 그물은 한 글자도 못 봤다.
    바로 위 독스트링이 "소켓에서 막으면 **어느 라이브러리든** 걸린다" 고
    적고 있었으므로 그 주장부터 거짓이었다(#286).

    자식이 파이썬이면 `sitecustomize` 가 자동 import 되므로, 같은 가드를 담은
    디렉터리를 **`PYTHONPATH` 맨 앞**에 둔다 — 호출부를 열거하지 않으므로
    새 테스트가 자식을 띄워도 자동으로 걸린다(#24·#119 규율이 아니라 구조).
    기존 `sitecustomize` 는 **가리지 않고 이어서 실행**한다.

    두 가드를 싣는다: 소켓 가드, 그리고 실제 홈 쓰기 가드(실수 #421 — 자식은 HOME
    리다이렉트를 환경으로 물려받지만, 절대경로로 실제 홈을 겨누는 자식은 이 가드가
    막는다). 홈 가드는 위 `_home_write_guard` 의 **소스를 그대로** 옮긴다(#38). 이 세션의
    보호·허용·기록 값을 파일에 **구워** 환경을 지운 자식도 지키고, 이 인터프리터의
    python-dotenv 가 스위치를 모르면 그 패치도 같은 소스로 싣는다.

    ⚠️ **못 보는 축**(#274): 파이썬이 아닌 자식(curl·git), `-I`/`-E`/`-S` 로
    띄운 파이썬, 그리고 `env=` 를 **직접 조립해** 넘기는 호출(그 dict 에
    `PYTHONPATH` 가 없으면 자식이 가드를 못 읽는다)은 이 그물 밖이다.
    """
    import atexit
    import inspect
    import os
    import pathlib
    import shutil
    import tempfile

    body = (
        _CHILD_GUARD_MARK + " — pytest 자식 프로세스용 소켓·홈 가드 (루트 conftest 가 심는다)\n"
        "import os as _o, sys as _sy\n"
        # 체이닝을 **관측 가능**하게 둔다 — `'sitecustomize' in sys.modules` 로
        # 재면 **우리 것**이 들어와도 참이라 아무것도 안 잰다(#91b 실측).
        "ORIGINAL = ''\n"
        "OTHERS = []\n"
        "_mine = _o.path.dirname(_o.path.abspath(__file__))\n"
        # ⚠️ **우리 가드 파일은 이어 실행하지 않는다**(실수 #421 실측): 중첩 세션(회귀가
        # pytest 를 자식으로 띄운다)이면 바깥·안쪽 세션의 가드 디렉터리가 둘 다 경로에 있어
        # 서로를 이어 실행하다 `RecursionError` 가 났고, `site.py` 가 그걸 삼켜 손자 프로세스엔
        # **가드가 하나도** 안 걸렸다(네트워크 가드도, #401 부터). 첫 줄 표식으로 가른다.
        "for _p in list(_sy.path):\n"
        "    _c = _o.path.join(_p or '.', 'sitecustomize.py')\n"
        "    if _o.path.abspath(_p or '.') != _mine and _o.path.isfile(_c):\n"
        "        try:\n"
        "            with open(_c, encoding='utf-8', errors='replace') as _fh:\n"
        "                _ours = _fh.readline().startswith(" + repr(_CHILD_GUARD_MARK) + ")\n"
        "        except OSError:\n"
        "            _ours = False\n"
        "        if not _ours:\n"
        "            OTHERS.append(_c)\n"
        "for _c in OTHERS[:1]:\n"
        "    exec(compile(open(_c).read(), _c, 'exec'), {'__file__': _c})\n"
        "    ORIGINAL = _c\n"
        "import socket as _s\n"
        "_real = _s.socket.connect\n"
        "def _guard(self, address, *a, **k):\n"
        "    h = address[0] if isinstance(address, tuple) and address else address\n"
        "    h = str(h or '')\n"
        "    if not (h in ('localhost', '', '::1') or h.startswith('127.')\n"
        "            or h.startswith('::ffff:127.')):\n"
        "        raise OSError('[conftest-child] 테스트의 **자식 프로세스**가 "
        "바깥 원천을 쳤습니다: ' + h + ' — 그 자식을 스텁하세요"
        "(conftest.py `_install_child_backstop`)')\n"
        "    return _real(self, address, *a, **k)\n"
        "_s.socket.connect = _guard\n"
        "\n# ── 실제 홈 쓰기 가드(실수 #421) — 루트 conftest 두 함수의 소스 그대로(#38)\n"
        + inspect.getsource(_home_write_guard) + "\n\n"
        + inspect.getsource(_child_home_guard_install) + "\n\n"
        "NOAH_HOME_GUARD = {'on': True}\n"
        "HOME_GUARD = _child_home_guard_install(_home_write_guard, NOAH_HOME_GUARD, "
        + repr((_PROTECTED_ENV, _ALLOWED_ENV, _GUARD_LOG_ENV)) + ", "
        # 환경을 지운 자식(`clear=True` 창·`env={…}`)도 이 세션의 값으로 지킨다(리뷰 L1)
        + repr((tuple(_HOME["protected"]), tuple(_HOME["allowed"]), _HOME["log"])) + ")\n"
    )
    if _HOME.get("dotenv") == "patched":
        # 이 인터프리터의 python-dotenv 가 스위치를 모른다 — 자식도 부모처럼 갈아 끼운다(리뷰 M1)
        body += ("\n# ── python-dotenv < 1.2 스위치(루트 conftest 소스 그대로, #38)\n"
                 + inspect.getsource(_dotenv_force_switch) + "\n\n"
                 "try:\n    _dotenv_force_switch()\nexcept Exception:\n    pass\n")
    d = tempfile.mkdtemp(prefix="noah-test-childguard-")
    (pathlib.Path(d) / "sitecustomize.py").write_text(body, encoding="utf-8")
    atexit.register(shutil.rmtree, d, True)
    cur = os.environ.get("PYTHONPATH") or ""
    os.environ["PYTHONPATH"] = d + (os.pathsep + cur if cur else "")
    return d


_INSTALLED = _install_outbound_block()
_SOCKET_BLOCKED = _install_socket_backstop()
_CHILD_GUARD_DIR = _install_child_backstop()


# ── sys.modules 오염 감지 ────────────────────────────────────────────────────
# 2026-09-21 실측: `tests/` 전체 실행이 **9건 빨간불**인데 그 9건만 골라
# 돌리면 green 이라, 몇 주 동안 '샌드박스 선재 실패'로 오인됐다(배포 때마다
# base 와 대조해 "같으니 무관" 으로 넘겼다). 원인은 단 하나 —
# `tests/test_dart_production.py` 의 `TestFscBreaker20260821._stub` 가
# `sys.modules["httpx"]` 를 `SimpleNamespace` 로 **직접 대입**하고 복원하지
# 않은 것이다. 알파벳순으로 그 파일이 앞이라, 뒤에서 `telegram`·`langgraph`
# 를 **처음** import 하는 테스트가 가짜 httpx 를 보고
# `AttributeError: … has no attribute 'Proxy'` 로 죽었다. 게다가
# `importorskip` 은 ImportError 만 skip 하므로 그게 skip 이 아니라 **실패**로
# 남았고, "샌드박스엔 telegram 이 없다" 는 주석이 그 오진을 굳혔다(#55).
#
# 규율("복원해라")로는 이미 네 번 졌다(#30·#312·#344·#384) — 구조로 옮긴다(#119).
# ⚠️ **이름을 열거하지 않는다**(#24): 무엇이 오염인지는 두 가지 구조로 판정한다.
#   ① 세션 시작 뒤 새로 나타난 값이 **모듈이 아니다**(SimpleNamespace·MagicMock…)
#   ② 세션 시작에 있던 이름이 **다른 객체로 교체**됐다(진짜 모듈 → 가짜 모듈)
# 세션 시작 시점의 스냅샷을 기준선으로 삼으므로, `bot/tests/conftest.py` 가
# 모듈 레벨에서 꽂는 MagicMock 처럼 **의도된 것은 자동으로 면제**된다 —
# 면제 목록을 내가 적지 않아도 된다(그 목록은 반드시 새 항목을 놓친다).
import pathlib as _pathlib
import sys as _sys
import types as _types

_MOD_BASELINE: dict = {}


def pytest_sessionstart(session):
    """기준선 스냅샷 — conftest 들이 이미 import 된 **뒤**의 상태다.

    ⚠️ `None` 값은 넣지 않는다. 파이썬은 실패한 import 자리에 `None` 을
    캐시하는데, 그걸 기준선에 담으면 `get()` 의 `None` 이 "기준선에 있었고
    값이 None" 과 "기준선에 아예 없다" 두 뜻을 겸한다(#34 한 자리가 두 뜻을
    대표하면 한쪽은 반드시 거짓말)."""
    _MOD_BASELINE.update(
        {k: v for k, v in _sys.modules.items() if v is not None})


def _repo_packages() -> frozenset:
    """레포 최상위 패키지 — **파일 시스템에서 파생**한다(#24 열거 금지).

    새 최상위 패키지가 생기면 자동으로 포함되고, 서드파티는 자동으로 빠진다.
    """
    root = _pathlib.Path(__file__).resolve().parent
    return frozenset(d.name for d in root.iterdir()
                     if d.is_dir() and (d / "__init__.py").exists())


_REPO_PKGS = _repo_packages()


def _module_pollution() -> list:
    """되돌려지지 않은 `sys.modules` 변경 → 사람이 읽는 줄(없으면 []).

    네 축을 본다(독립 리뷰 2026-09-21 이 ②를 뺀 셋 중 둘을 실측으로 잡았다):
      ① 모듈이 아닌 값(SimpleNamespace·MagicMock)
      ② 기준선에 있던 이름이 **다른 객체로 교체**됨
      ③ **손으로 만든 빈 모듈**이 레포 모듈 자리를 차지 — `types.ModuleType
         ("x")` 는 `__spec__` 이 None 이다. 이 축이 없으면 "가짜 **모듈**로
         대체" 형태가 통째로 새는데, 그게 바로 이 커밋이 고친
         `_mock_news_clients` 의 모양이다(리뷰 실측: 되돌려도 첫 범인은
         통과하고 **두 번째** 테스트가 error 라 범인도 오지목). 판정 범위는
         아래 주석대로 **레포 패키지 안**이다.
      ④ 기준선에 있던 이름이 `sys.modules` 에서 **사라짐**. 순회만으로는 삭제가
         안 보인다 — `finally: pop` 으로 진짜 모듈까지 지우는 형태(이 커밋의
         `bot.world_quote` 자리)가 그대로 샜다.
    """
    bad = []
    # ⚠️ 살아 있는 `sys.modules` 를 순회하지 않는다 — 테스트가 띄운 백그라운드
    # 스레드가 그 사이 import 하면 `dictionary changed size during iteration` 으로
    # **가드 자신이** 그 테스트를 error 로 만든다(2026-09-25 `make test` 실측:
    # TestIntlHighLow52 · 앞선 두 실행은 통과 = 타이밍 경쟁). `list(d.items())` 는
    # 항목마다 튜플을 만들며 GC 가 돌 수 있어 그 틈에 스레드가 끼어든다 —
    # `dict.copy()` 는 C 에서 한 번에 복사한다.
    now = _sys.modules.copy()
    for name, mod in now.items():
        if mod is None:
            continue            # None 은 파이썬이 실패한 import 자리에 넣는다
        prev = _MOD_BASELINE.get(name)
        hit = None
        if prev is None:
            if not isinstance(mod, _types.ModuleType):
                hit = f"{name} → {type(mod).__name__}(모듈이 아니다)"
            elif (getattr(mod, "__spec__", None) is None
                    and name.partition(".")[0] in _REPO_PKGS):
                # ⚠️ 손으로 만든 `types.ModuleType(...)` 은 `__spec__` 이 None
                # 이다. 그러나 그것만으로는 못 가른다 — C/Rust 확장이 정상
                # 등록하는 서브모듈도 같은 모양이다(실측 오탐: `_openssl`·
                # `_cython_3_2_4`·`xml.parsers.expat.model`·
                # `cryptography.hazmat.primitives.ciphers.algorithms`). 디스크에
                # 소스가 있나로 갈라 봐도 마지막 것이 통과한다. **레포 패키지
                # 안**으로 좁힌다 — 우리 모듈은 예외 없이 파일에서 import 되고,
                # 실제 사고(`bot.naver_news_client` 를 가짜로 덮기)가 전부
                # 거기 있다. 서드파티를 덮는 첫 건은 아래 교체 축이 두 번째
                # 부터 잡는다(못 보는 축, #274).
                hit = f"{name} → 손으로 만든 빈 모듈이 레포 모듈 자리를 차지했다"
            else:
                _MOD_BASELINE[name] = mod      # 정상 import — 기준선에 편입
        elif prev is not mod:
            hit = f"{name} → 다른 객체로 교체됨"
        if hit:
            bad.append(hit)
            # 한 번 말했으면 **새 기준선**으로 — 같은 오염을 매 테스트마다
            # 다시 말하지 않는다. 별도 '보고함' 집합을 두면 그 이름의 **다음**
            # 오염까지 영구히 가린다(리뷰 지적).
            _MOD_BASELINE[name] = mod
    for name in [n for n in _MOD_BASELINE if n not in now]:
        bad.append(f"{name} → sys.modules 에서 사라졌다(복원 없는 pop)")
        del _MOD_BASELINE[name]
    return bad


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _no_sys_modules_leak():
    """테스트가 `sys.modules` 를 오염시킨 채 끝나면 **그 테스트**를 실패시킨다.

    ⚠️ 여기서 잡아야 범인이 지목된다. 세션 끝에 한 번만 보면 '오염이 있다'
    까지만 알고 어느 테스트인지 모른다(#114 마지막으로 본 것이 남는다).
    ⚠️ **autouse fixture 다**(`pytest_runtest_teardown` 훅이 아니라). 그 훅에서
    raise 하면 pytest 의 SetupState 가 정리를 못 마쳐 뒤 테스트가
    `previous item was not torn down properly` 로 **연쇄로** 깨진다(실측).
    ⚠️ autouse 는 요청형 fixture 보다 **먼저** setup 되므로 teardown 은 나중이다
    — `monkeypatch` 가 이미 되돌린 뒤에 보기 때문에 정상 사용을 오염으로
    오인하지 않는다(그 반대 증거를 회귀가 값으로 고정한다, #25).
    ⚠️ 처방은 `monkeypatch.setitem(sys.modules, …)`/`monkeypatch.setattr` —
    pytest 가 teardown 에서 반드시 되돌린다. 직접 대입은 되돌릴 사람이 없다.
    """
    yield
    bad = _module_pollution()
    if bad:
        raise AssertionError(
            "sys.modules 를 되돌리지 않고 끝났다 — 뒤에 도는 테스트가 이 "
            "가짜를 본다(전체 실행에서만 빨간불이 되어 '선재 실패'로 오인된다).\n"
            "  " + "\n  ".join(bad)
            + "\n  → `monkeypatch.setitem(sys.modules, …)` 로 꽂을 것.")


# ── 실제 홈 쓰기 → 그 테스트를 실패시킨다(실수 #421) ────────────────────────────
_HOME_SEEN = [0, 0]      # [이 프로세스 기록 인덱스, 자식 기록 파일 바이트 오프셋]
_HOME_STRAY: list = []   # 어느 테스트 창에도 안 든 기록(수집 시점·테스트 사이)
_HOME_SHOW = 20          # 한 번에 보여 줄 줄 — 넘치면 **자른 사실**을 말한다(#45)


def _child_home_writes(offset: int) -> tuple:
    """자식 프로세스가 막고 적은 줄 — `offset` 뒤의 **완결된 줄**만, 그리고 새 오프셋."""
    try:
        with open(_HOME["log"], "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset                   # 아직 어떤 자식도 막지 않았다(파일 없음)
    data = data[: data.rfind(b"\n") + 1]    # 쓰는 중인 마지막 줄은 다음에 읽는다
    lines = [ln for ln in data.decode("utf-8", "replace").split("\n") if ln.strip()]
    return lines, offset + len(data)


def _fmt_home_write(r: dict) -> str:
    where = " ← ".join(r["where"]) or "레포 밖에서 부름(위치 미상)"
    return f"{r['event']} {r['path']}  [스레드 {r['thread']}] {where}"


def _fmt_child_write(line: str) -> str:
    """자식이 적은 한 줄 — 그 자식을 **띄운 테스트**(물려받은 `PYTEST_CURRENT_TEST`)도 적는다.
    백그라운드 자식은 띄운 테스트가 끝난 뒤에 써서 다른 테스트 창에 든다 — 그 창의 테스트만
    지목하면 범인이 안 보인다(리뷰 L2 실측, #114)."""
    pid, event, path, test, argv = (line.split("\t") + [""] * 5)[:5]
    by = f" · 띄운 테스트 {test}" if test else ""
    return f"{event} {path}  [자식 pid {pid}{by}: {argv}]"


def _shown(lines: list) -> str:
    more = len(lines) - _HOME_SHOW
    tail = [f"… 외 {more}건(처음 {_HOME_SHOW}건만 보였다)"] if more > 0 else []
    return "\n  ".join(lines[:_HOME_SHOW] + tail)


@_pytest.fixture(autouse=True)
def _no_real_home_writes():
    """실제 홈 아래 쓰기가 **막혔으면** 그 테스트를 실패시킨다(막기만 하면 안 보인다).

    ⚠️ 감사 훅은 `PermissionError` 로 막지만 앱 코드가 `except Exception: pass` 로
    삼키면 테스트는 초록이다(#12·#315) — 그래서 기록을 여기서 읽는다(삼킬 수 없는 자리).
    ⚠️ 테스트 사이·수집 시점의 기록은 앞 테스트 탓으로 돌리지 않고 세션 끝에 따로
    말한다(`pytest_sessionfinish`) — 엉뚱한 테스트를 지목하면 다음 라운드를 헛쓴다(#114).
    ⚠️ autouse fixture 다(teardown 훅에서 raise 하면 연쇄로 깨진다 — 위 `sys.modules`
    가드와 같은 이유).
    """
    n0 = len(_HOME_WRITES)
    _HOME_STRAY.extend(_fmt_home_write(r) for r in _HOME_WRITES[_HOME_SEEN[0]:n0])
    lines, off0 = _child_home_writes(_HOME_SEEN[1])
    _HOME_STRAY.extend(_fmt_child_write(ln) for ln in lines)
    yield
    n1 = len(_HOME_WRITES)
    new = [_fmt_home_write(r) for r in _HOME_WRITES[n0:n1]]
    lines, off1 = _child_home_writes(off0)
    new += [_fmt_child_write(ln) for ln in lines]
    _HOME_SEEN[:] = [n1, off1]
    if new:
        raise AssertionError(
            "테스트가 **실제 홈**에 쓰려 해서 막았다(앱 코드가 그 예외를 삼켰어도 여기서 "
            "실패시킨다). HOME 은 이미 임시 디렉터리다 — 절대경로·환경변수·`pwd` 로 실제 "
            "홈을 가리키는 경로를 찾아 임시 경로로 돌릴 것(루트 conftest `_isolate_home`).\n  "
            + _shown(new))


def pytest_sessionfinish(session, exitstatus):
    """어느 테스트 창에도 안 든 실제 홈 쓰기(수집 시점 import · 테스트 사이 백그라운드
    스레드)를 말하고 세션을 실패로 끝낸다 — 조용히 통과시키지 않는다(#54)."""
    stray = list(_HOME_STRAY)
    stray += [_fmt_home_write(r) for r in _HOME_WRITES[_HOME_SEEN[0]:]]
    lines, _off = _child_home_writes(_HOME_SEEN[1])
    stray += [_fmt_child_write(ln) for ln in lines]
    if not stray:
        return
    msg = ("[conftest] 어느 테스트에도 속하지 않은 **실제 홈** 쓰기를 막았다 — 수집 시점"
           "(모듈 import) 또는 테스트 사이 백그라운드 스레드다(루트 conftest "
           "`_isolate_home`):\n  " + _shown(stray))
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(msg, red=True)
    else:
        print(msg, file=_sys.stderr)
    if session.exitstatus == 0:
        session.exitstatus = _pytest.ExitCode.TESTS_FAILED
