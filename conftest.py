"""레포 루트 conftest — **바깥 원천 차단**만 둔다.

`tests/conftest.py` 에 두면 `pytest bot/tests` 처럼 그 디렉터리를 단독으로
수집하는 실행에서 차단이 통째로 안 걸린다(2026-09-11 독립 리뷰 실측:
`pytest bot/tests` → `Session.request` 가 pristine). pytest.ini 의
`testpaths = tests bot/tests` 는 수집 순서 덕에 우연히 걸렸을 뿐이다 —
우연에 기대지 말고 **루트**에 둔다(#24 어느 목록에도 안 드는 자리).
"""
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
    """자식 프로세스 축 — **위 소켓 패치는 이 프로세스만 덮는다**.

    2026-09-22 독립 리뷰 실측: `trade/tests/test_jp_master_probe.py` 가
    `subprocess.run([py, "-c", …yfinance…])` 로 자식을 띄워 `make test` 한
    번에 Yahoo ~6 · JPX 9 요청이 나갔는데 위 그물은 한 글자도 못 봤다.
    바로 위 독스트링이 "소켓에서 막으면 **어느 라이브러리든** 걸린다" 고
    적고 있었으므로 그 주장부터 거짓이었다(#286).

    자식이 파이썬이면 `sitecustomize` 가 자동 import 되므로, 같은 가드를 담은
    디렉터리를 **`PYTHONPATH` 맨 앞**에 둔다 — 호출부를 열거하지 않으므로
    새 테스트가 자식을 띄워도 자동으로 걸린다(#24·#119 규율이 아니라 구조).
    기존 `sitecustomize` 는 **가리지 않고 이어서 실행**한다.

    ⚠️ **못 보는 축**(#274): 파이썬이 아닌 자식(curl·git), `-I`/`-E`/`-S` 로
    띄운 파이썬, 그리고 `env=` 를 **직접 조립해** 넘기는 호출(그 dict 에
    `PYTHONPATH` 가 없으면 자식이 가드를 못 읽는다)은 이 그물 밖이다.
    """
    import atexit
    import os
    import pathlib
    import shutil
    import tempfile

    body = (
        "# pytest 자식 프로세스용 소켓 가드 (루트 conftest 가 심는다)\n"
        "import os as _o, sys as _sy\n"
        # 체이닝을 **관측 가능**하게 둔다 — `'sitecustomize' in sys.modules` 로
        # 재면 **우리 것**이 들어와도 참이라 아무것도 안 잰다(#91b 실측).
        "ORIGINAL = ''\n"
        "OTHERS = []\n"
        "_mine = _o.path.dirname(_o.path.abspath(__file__))\n"
        "for _p in list(_sy.path):\n"
        "    _c = _o.path.join(_p or '.', 'sitecustomize.py')\n"
        "    if _o.path.abspath(_p or '.') != _mine and _o.path.isfile(_c):\n"
        "        OTHERS.append(_c)\n"
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
    )
    d = tempfile.mkdtemp(prefix="noah-test-childguard-")
    (pathlib.Path(d) / "sitecustomize.py").write_text(body, encoding="utf-8")
    atexit.register(shutil.rmtree, d, True)
    cur = os.environ.get("PYTHONPATH") or ""
    os.environ["PYTHONPATH"] = d + (os.pathsep + cur if cur else "")
    return d


_INSTALLED = _install_outbound_block()
_SOCKET_BLOCKED = _install_socket_backstop()
_CHILD_GUARD_DIR = _install_child_backstop()


# ── 운영 디스크 캐시 차단 ────────────────────────────────────────────────────
# 2026-09-11 실측: `make test` 한 번이 운영 캐시
# `~/.tradingagents/cache/naver_sector/kr_industry_fail.json` 에 **실패 도장**을
# 남겼다. 그 도장은 15분 백오프의 근거라, 다음 운영 실행이 업종맵을 안 만든다 —
# 테스트가 프로덕션 동작을 바꾸는 경로다(#30).
#
# ⚠️ `tests/conftest.py` 의 **함수 스코프** fixture 로는 못 막는다. 렌더가 띄운
# **daemon 스레드**가 teardown 뒤에 쓰기 때문이다 — 바로 위 네트워크 차단이
# 세션 스코프인 것과 **같은 이유**이고, 같은 실측으로 확인했다(클래스 단독
# 실행에선 안 나오고 여러 클래스를 이어 돌릴 때만 파일이 생긴다). 그래서
# 여기서 **import 시점에 한 번** 갈아끼우고 **되돌리지 않는다**.
#
# ⚠️ 이 목록은 **완전하지 않다** — `bot/` 에 `~/.tradingagents` 아래를 가리키는
# 모듈 상수가 131개 있다(AST 실측). 전부 갈아끼우려면 conftest 가 130개 모듈을
# import 해야 해서 비용이 크다. 그러니 "테스트는 운영 캐시를 못 만진다"고
# **주장하지 않는다**(#286 지시서가 자기 자신에 대해 거짓이면 다음 사람이 가드를
# 건너뛴다). 오염을 관측하면 그 모듈을 여기 한 줄로 추가할 것.
_REDIRECTED: list = []


def _redirect_disk_caches() -> list:
    import tempfile
    from pathlib import Path as _P
    root = _P(tempfile.mkdtemp(prefix="noah-test-caches-"))
    targets = (
        ("bot.naver_sector_client", "_CACHE_DIR", "naver_sector"),
        ("bot.market_timing", "_VOL_CACHE_DIR", "market_timing"),
        ("bot.finviz_client", "_CACHE_DIR", "finviz"),
        ("bot.market_favorites", "_FAVORITES_FILE", "market_favorites.json"),
        # 상세 캐시(목표가·투자의견)는 렌더 경로가 디스크에 굽는다 — 함수
        # 스코프 fixture 로는 `pytest bot/tests` 프로세스가 안 덮인다(#344).
        ("bot.naver_research_client", "_CACHE_DIR", "naver_research"),
        # 번역 캐시 3종 — `make test` 가 운영 `~/.tradingagents/translate_miss.json`
        # 을 **읽고**(진단 테스트) 깨진 파일이면 정리본을 **쓴다**(독립 리뷰
        # 2026-09-17 H2 실측). 위 주석이 적은 대로 관측했으니 한 줄씩 더한다.
        ("bot.chart_translate", "_MISS_CACHE", "translate_miss.json"),
        ("bot.chart_translate", "_CACHE", "chart_title_kr.json"),
        ("bot.chart_translate", "_NAME_KR_CACHE", "names_kr.json"),
        # 번역 회귀가 `_call_pro` 를 스텁하면 `_log_usage` 가 **운영 비용 원장**에
        # 쓴다(#284·#312, 독립 리뷰 2026-09-17 L3 — 리뷰 프로브가 실제로 한 줄 썼다).
        ("bot.chart_translate", "_USAGE", "usage.jsonl"),
        # TW 업종 맵 — 2026-09-17 캐시가 소스별 봉투가 되며 `fetch_tw_industry_map`
        # 이 **실패해도** 재시도 시각을 굽는다(#384·#303). 그래서 개발기에서 프로브를
        # 한 번 돌리면 그 쿨다운이 남아, 이 상수를 안 쓰는 회귀가 원천을 못 타고
        # 조용히 빈 맵을 받는다(실측: `TestTwIndustryMapTpexEnglishKeys` 가 전체
        # 실행에서만 빨간불). 위 주석이 적은 대로 관측했으니 한 줄 더한다.
        ("bot.twse_client", "_CACHE_DIR", "twse"),
        # 수주잔고 미스 원장 — `make test` 가 `backlog_probe` 를 태우면
        # `_log_miss` 가 운영 원장에 **가짜 티커**를 남긴다(2026-09-18 실측:
        # `X`·`005930` 이 픽스처 본문 "수주잔고 없음" 과 함께 들어 있었다).
        # 그 원장이 곧 격주 파서 리뷰 DM 이라, 운영자는 그걸 **진짜 미스**로
        # 읽고 없는 버그를 쫓는다. 발췌를 싣게 된 이번 변경이 그 오염을
        # '파서를 고칠 유일한 근거' 로 승격시켜 더 나빠졌다(#30·#312·#344).
        ("bot.dart_backlog", "_MISS_LOG", "backlog_misses.jsonl"),
    )
    done = []
    for mod, attr, leaf in targets:
        try:
            import importlib
            m = importlib.import_module(mod)
            if not hasattr(m, attr):
                continue           # 상수 이름이 바뀌었다 — 아래 회귀가 잡는다
            setattr(m, attr, root / leaf)
            done.append(f"{mod}.{attr}")
        except Exception:
            continue
    return done


_REDIRECTED = _redirect_disk_caches()


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
    now = _sys.modules
    for name, mod in list(now.items()):
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
