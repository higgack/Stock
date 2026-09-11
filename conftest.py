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
    애초에 위 패치가 못 본다. 소켓에서 막으면 **어느 라이브러리든** 걸린다.

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


_INSTALLED = _install_outbound_block()
_SOCKET_BLOCKED = _install_socket_backstop()
