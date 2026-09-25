"""`bot.env_keys` 가 건넨 비밀값은 어느 로그 레코드에도 평문으로 남지 않는다(실수 #416).

독립 리뷰(2026-09-25) H1: 관세청 클라이언트는 키를 URL 에 싣는데 httpx 는 매 요청 URL 을
INFO 로 찍는다(`HTTP Request: GET …?serviceKey=…`). 클라이언트의 `_mask` 는 **자기가 만든
사유 문자열**만 덮어, 그 줄은 봇 저널로 6시간마다 3줄씩 그대로 갔다 — 기존 테스트는 사유
문자열만 재서 통과했다(거짓 안심). 같은 병이 FRED(`raise_for_status` 예외 문구의 URL 속
`api_key=`)·ECOS(경로 속 키)에도 있었다. 키를 건네는 **단일 헬퍼**가 값을 기억하고 레코드
팩토리 하나가 모든 레코드(메시지·트레이스백)에서 가린다 — 로거마다 필터를 다는 목록은 다음
클라이언트를 못 잡는다(#24).

네트워크 0 — httpx 는 **전송 계층**(`HTTPTransport.handle_request`)에서 스텁해, httpx 자신이
찍는 그 INFO 줄을 실제 경로로 태운다(#20 배선은 태워야 보인다 · #155 원천의 모양대로).
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import pytest

from bot import env_keys

# 실제 data.go.kr 디코딩 키처럼 `+ / =` 가 섞인 긴 값 — URL 에 실리면 %2B·%2F·%3D 로 바뀐다.
# 조립한다(시크릿 스캐너 대상 아님, #407).
_KEY = "".join(("Ab12Cd34", "+Ef56/Gh78", "Ij90Kl12Mn34", "Op56Qr78==")) * 2


@pytest.fixture()
def fresh(monkeypatch):
    """가림 목록을 테스트마다 비운다 — 모듈 전역이라 남으면 다음 테스트의 로그를 가린다(#30)."""
    monkeypatch.setattr(env_keys, "_SECRETS", ())
    monkeypatch.setattr(env_keys, "_TRIED", set())
    return env_keys


def _leaked(text: str, key: str, n: int = 8) -> list[str]:
    """`text` 에 남은 키 조각(n 자 이상) — 원문·인코딩 모양 둘 다 본다."""
    out = []
    for form in (key, quote(key, safe="")):
        out += [form[i:i + n] for i in range(len(form) - n + 1) if form[i:i + n] in text]
    return out


def test_env_key_remembers_what_it_hands_out_and_every_record_hides_it(fresh, monkeypatch,
                                                                      caplog):
    monkeypatch.setenv("DATA_GO_KR_API_KEY", _KEY)
    assert fresh.env_key("DATA_GO_KR_API_KEY") == _KEY          # 값은 그대로 건넨다
    caplog.set_level(logging.INFO)
    log = logging.getLogger("bot.anything")
    log.warning("요청 실패: %s", f"https://x/y?serviceKey={_KEY}&a=1")
    log.warning("인코딩된 URL: https://x/y?serviceKey=%s", quote(_KEY, safe=""))
    log.info("경로에 실린 키 https://ecos/api/StatisticSearch/%s/json", _KEY)
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 3 and all("***" in m for m in msgs), msgs
    assert not _leaked("\n".join(msgs), _KEY), msgs


def test_the_dotenv_path_remembers_too(fresh, monkeypatch, tmp_path, caplog):
    """환경에 없고 `.env` 에서 읽은 값도 가린다 — 두 반환 경로가 다 기억해야 한다."""
    # 빈 값으로 **기록해 둔다** — `env_key` 가 `.env` 값을 환경에 채우므로, delenv(없을 때)는
    # 되돌릴 게 없어 다음 테스트로 키가 샌다(#30)
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "")
    monkeypatch.setattr(fresh, "_dotenv_lookup", lambda name: (_KEY, "test", ""))
    assert fresh.env_key("DATA_GO_KR_API_KEY") == _KEY
    caplog.set_level(logging.INFO)
    logging.getLogger("bot.x").warning("k=%s", _KEY)
    assert caplog.records[-1].getMessage() == "k=***"


def test_httpx_request_line_hides_the_customs_key(fresh, monkeypatch, caplog, tmp_path):
    """E2E — 관세청 클라이언트가 키를 받아(`env_key`) httpx 로 부르고, httpx 가 스스로 찍는
    `HTTP Request:` 줄에 키가 안 남는다. 그 줄이 **실제로 찍혔다**는 것도 본다(대조 0건은
    통과가 아니다, #54)."""
    httpx = pytest.importorskip("httpx")
    import bot.customs_trade_client as ct
    monkeypatch.setattr(ct, "_CACHE_DIR", tmp_path / "customs")
    monkeypatch.setattr(ct, "_fail", {})
    monkeypatch.setenv("DATA_GO_KR_API_KEY", _KEY)
    body = ('<?xml version="1.0"?><response><header><resultCode>00</resultCode>'
            '<resultMsg>OK</resultMsg></header><body><items></items></body></response>')
    seen: list[str] = []

    def handle(self, request):
        seen.append(str(request.url))
        return httpx.Response(200, text=body, request=request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    caplog.set_level(logging.INFO, logger="httpx")
    ct.monthly_totals(use_cache=False)
    assert seen and "serviceKey=" in seen[0]                   # 원천에는 키가 간다
    lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert lines and all("HTTP Request:" in ln for ln in lines), lines
    assert all("serviceKey=***" in ln for ln in lines), lines
    assert not _leaked("\n".join(lines), _KEY), lines


def test_an_already_encoded_key_is_hidden_as_given(fresh, monkeypatch, caplog, tmp_path):
    """data.go.kr '인코딩 키'(`%` 포함)는 URL 에 **그대로** 실린다(`_http_get` 의 분기)."""
    httpx = pytest.importorskip("httpx")
    import bot.customs_trade_client as ct
    enc = quote(_KEY, safe="")
    monkeypatch.setattr(ct, "_CACHE_DIR", tmp_path / "customs")
    monkeypatch.setattr(ct, "_fail", {})
    monkeypatch.setenv("DATA_GO_KR_API_KEY", enc)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, request: httpx.Response(500, text="x", request=request))
    caplog.set_level(logging.INFO, logger="httpx")
    ct.monthly_totals(use_cache=False)
    lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert lines and all("serviceKey=***" in ln for ln in lines), lines
    assert enc not in "\n".join(lines)


def test_a_traceback_carrying_the_key_is_hidden(fresh, monkeypatch, caplog):
    """예외 문구엔 URL 이 실린다(requests `raise_for_status`·연결 오류) — `log.exception` 의
    트레이스백도 가린다."""
    monkeypatch.setenv("FRED_API_KEY", _KEY)
    fresh.env_key("FRED_API_KEY")
    caplog.set_level(logging.INFO)
    try:
        raise RuntimeError(f"400 Client Error: for url: https://fred/x?api_key={_KEY}&s=1")
    except RuntimeError:
        logging.getLogger("bot.y").exception("fetch failed")
    assert "fetch failed" in caplog.text and "RuntimeError" in caplog.text
    assert "api_key=***" in caplog.text and not _leaked(caplog.text, _KEY), caplog.text


def test_fred_failure_log_hides_the_api_key(fresh, monkeypatch, caplog, tmp_path):
    """배선 — FRED 헤드라인 경로의 실패 로그(`fred: fetch … failed: <예외>`)가 키를 안 싣는다."""
    import bot.market_overview as mo
    monkeypatch.setenv("FRED_API_KEY", _KEY)
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path / "mo")
    monkeypatch.setattr(mo, "_fred_fail", {})

    def boom(url, timeout=None):
        raise ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(mo.requests, "get", boom)
    caplog.set_level(logging.INFO)
    assert mo._fred_fetch_series("UNRATE", 30) is None
    fails = [r.getMessage() for r in caplog.records if "fetch UNRATE failed" in r.getMessage()]
    assert fails and "api_key=***" in fails[0], fails
    assert not _leaked(fails[0], _KEY), fails


def test_clean_records_are_left_untouched(fresh, monkeypatch, caplog):
    """비밀값이 없는 레코드는 한 글자도 안 바꾼다(args 유지) — 가림이 다른 로깅을 바꾸지 않게."""
    monkeypatch.setenv("DATA_GO_KR_API_KEY", _KEY)
    fresh.env_key("DATA_GO_KR_API_KEY")
    caplog.set_level(logging.INFO)
    logging.getLogger("bot.z").info("값 %s · %d", "평범", 3)
    rec = caplog.records[-1]
    assert rec.args == ("평범", 3) and rec.msg == "값 %s · %d"


def test_short_values_are_not_remembered(fresh, monkeypatch, caplog):
    """16자 미만은 기억하지 않는다 — 흔한 짧은 글자를 가리는 오탐의 대가로(못 보는 축, #274)."""
    monkeypatch.setenv("SOME_SHORT_KEY", "short123")
    assert fresh.env_key("SOME_SHORT_KEY") == "short123"
    assert fresh._SECRETS == ()
    caplog.set_level(logging.INFO)
    logging.getLogger("bot.z").info("short123")
    assert caplog.records[-1].getMessage() == "short123"


def test_redact_covers_the_url_shapes(fresh):
    fresh._remember(_KEY)
    import re
    enc = quote(_KEY, safe="")
    lower = re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), enc)
    assert lower != enc                         # 픽스처가 소문자 모양을 실제로 만든다(#91c)
    for form in (_KEY, enc, lower, quote(_KEY)):
        assert fresh.redact(f"a={form}&b") == "a=***&b", form
    assert fresh.redact("") == "" and fresh.redact("평범한 글") == "평범한 글"


def test_the_factory_is_installed_once(fresh):
    """모듈을 다시 적재해도 두 겹이 되지 않는다 — 겹치면 레코드마다 두 번 포맷한다."""
    f1 = logging.getLogRecordFactory()
    assert getattr(f1, "_noah_redact", False)
    fresh._install_record_redaction()
    assert logging.getLogRecordFactory() is f1


# ── 2차 독립 리뷰(217aace..ef33923) 반영 ─────────────────────────────────────
def test_realistic_key_lengths_are_hidden_and_the_threshold_is_sixteen(fresh, monkeypatch,
                                                                       caplog):
    """M2 — 80자 픽스처만 쓰면 하한을 21·33 으로 올려도 전부 통과한다(2차 리뷰 실측). 실제 키
    길이로 잰다: ECOS·Finnhub 20자 영숫자 · FRED·EDINET 32자 소문자 hex. 경계는 15자 안 기억 ·
    16자 기억."""
    ecos = "".join(("ABCD", "1234", "EFGH", "5678", "IJKL"))
    fred = "".join(("0a1b2c3d", "4e5f6a7b", "8c9d0e1f", "2a3b4c5d"))
    assert len(ecos) == 20 and len(fred) == 32
    monkeypatch.setenv("BOK_ECOS_API_KEY", ecos)
    monkeypatch.setenv("FRED_API_KEY", fred)
    fresh.env_key("BOK_ECOS_API_KEY")
    fresh.env_key("FRED_API_KEY")
    caplog.set_level(logging.INFO)
    log = logging.getLogger("bot.len")
    log.warning("ecos: HTTP fetch failed: %s",
                RuntimeError(f"Max retries exceeded with url: /api/StatisticSearch/{ecos}/json"))
    log.warning("fred: fetch failed: %s",
                f"https://api.stlouisfed.org/fred/series/observations?api_key={fred}&s=1")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert ecos not in text and fred not in text and text.count("***") == 2, text
    fresh._remember("a" * 15)
    fresh._remember("b" * 16)
    assert "a" * 15 not in fresh._SECRETS and "b" * 16 in fresh._SECRETS


def test_the_decoded_form_of_an_encoded_key_is_hidden(fresh, caplog):
    """`.env` 에 인코딩 키(`%2B…`)를 넣었어도, 어떤 경로가 그걸 풀어 찍으면 원문 모양이다."""
    fresh.remember_secret(quote(_KEY, safe=""))
    caplog.set_level(logging.INFO)
    logging.getLogger("bot.x").info("decoded %s", _KEY)
    assert caplog.records[-1].getMessage() == "decoded ***"


def test_every_occurrence_in_one_record_is_hidden(fresh, caplog):
    """requests 의 연쇄 트레이스백엔 한 레코드에 키가 세 번 실린다(2차 리뷰 실측)."""
    fresh.remember_secret(_KEY)
    caplog.set_level(logging.INFO)
    logging.getLogger("bot.x").info("%s | %s | %s", _KEY, _KEY, _KEY)
    assert caplog.records[-1].getMessage() == "*** | *** | ***"


def test_stack_info_is_hidden(fresh):
    """`stack_info` 도 가린다 — 레코드 팩토리를 타는 `makeRecord` 로 직접 만든다."""
    fresh.remember_secret(_KEY)
    rec = logging.getLogger("bot.x").makeRecord(
        "bot.x", logging.INFO, "f.py", 1, "m", (), None,
        sinfo=f"Stack (most recent call last):\n  url={_KEY}")
    assert _KEY not in rec.stack_info and "url=***" in rec.stack_info


def test_concurrent_remembers_do_not_lose_a_key(fresh):
    """L1 — 두 스레드가 동시에 등록하면 한쪽 값이 사라졌다(2차 리뷰 실측: 8스레드·300회 중 73회).
    스레드 전환 간격을 좁혀 경합을 강제한다 — 락이 없으면 50회 안에 사실상 반드시 잃는다."""
    # ⚠️ 스레드를 차례로 띄우기만 하면 앞 스레드가 끝난 뒤 다음이 시작해 경합이 안 생긴다 —
    # 첫 판이 그래서 락을 지워도 통과했다(#91c). 배리어로 **동시에** 출발시키고 스레드마다
    # 여러 번 등록한다.
    import sys
    import threading
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for run in range(20):
            fresh._SECRETS = ()
            gate = threading.Barrier(8)
            keys = [[f"{run:02d}-{t:01d}-{i:02d}-" + "k" * 16 for i in range(20)]
                    for t in range(8)]

            def work(mine, gate=gate):
                gate.wait()
                for k in mine:
                    fresh.remember_secret(k)
            ts = [threading.Thread(target=work, args=(mine,)) for mine in keys]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            missing = [k for mine in keys for k in mine if k not in fresh._SECRETS]
            assert not missing, (run, len(missing))
    finally:
        sys.setswitchinterval(old)


def test_a_malformed_log_call_does_not_print_the_key_to_stderr(fresh, monkeypatch, capsys):
    """L2 — 포맷이 깨진 호출은 로깅이 `Arguments: (…)` 로 인자를 **통째로** stderr 에 찍는다."""
    fresh.remember_secret(_KEY)
    lg = logging.getLogger("bot.badfmt")
    h = logging.StreamHandler()
    lg.addHandler(h)
    monkeypatch.setattr(lg, "propagate", False)
    try:
        lg.warning("bad %d", f"https://x/y?serviceKey={_KEY}&a=1")
    finally:
        lg.removeHandler(h)
    err = capsys.readouterr().err
    assert "Arguments:" in err and "serviceKey=***" in err, err
    assert not _leaked(err, _KEY), err


def test_dart_client_registers_the_key_it_reads_itself(fresh, monkeypatch, caplog):
    """M1(2차 리뷰) — DART 키는 `env_key` 를 안 거친다(자체 .env 폴백 · 빈 문자열='키 없음').
    등록이 없으면 `crtfc_key=` 가 든 예외 URL 이 실패 로그로 그대로 샌다. 환경에서 읽은 키와
    인자로 받은 키 둘 다."""
    from bot import dart_client
    dart = "".join(("0123456789", "abcdef0123", "456789abcd", "ef01234567"))
    monkeypatch.setenv("DART_API_KEY", dart)
    dart_client.DartClient()
    caplog.set_level(logging.INFO)
    logging.getLogger("bot.dart").warning(
        "dart: company.json failed: %s",
        ConnectionError(f"Max retries exceeded with url: /api/company.json?crtfc_key={dart}&c=1"))
    m = caplog.records[-1].getMessage()
    assert dart not in m and "crtfc_key=***" in m, m
    other = dart[::-1]
    dart_client.DartClient(other)
    logging.getLogger("bot.dart").warning("k=%s", other)
    assert caplog.records[-1].getMessage() == "k=***"
