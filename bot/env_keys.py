"""API 키를 `.env` 까지 보고 읽는 단일 헬퍼. stdlib + python-dotenv 만.

⚠️ **왜 필요한가.** `load_dotenv()` 를 호출하는 건 봇 엔트리포인트
(`telegram_bot.py` · `dashboard_server.py`)뿐이다. `python -m bot.scripts.…`
로 도는 진단·크론 스크립트는 `os.environ` 이 비어 있어, 키가 **있는데도**
readiness 체크가 전부 False 를 돌려준다.

이 함정에 2026-08-18 하루에만 두 번 빠졌다:
  · KRX_ID/KRX_PW — 프로브가 '미설정' 이라 보고해 이미 넣어둔 키를 다시
    넣으라고 안내할 뻔했다(실수기록 #23).
  · FRED_API_KEY — #23 을 적어놓고 **바로 다음 프로브에서 똑같이** 반복했다.
    그래서 금리 지연의 원인을 여전히 못 가렸다.

세 번째 반복을 규율로 막지 않는다 — **헬퍼 하나로 통일**한다.

⚠️ `load_dotenv()` 가 아니라 `dotenv_values()` 를 쓴다. 전자는 `.env` 의
**모든** 키(TELEGRAM_BOT_TOKEN·DASHBOARD_PASSWORD·SMTP_PASS …)를 프로세스
환경에 주입한다 — 키 하나를 읽는 부작용으로는 과하다.
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger("bot.env_keys")

_TRIED: set[str] = set()


def env_key(name: str) -> str:
    """`name` 값(공백 제거). 환경에 없으면 `.env` 에서 **그 키만** 읽어 채운다.

    파일 I/O 는 키마다 한 번. 못 찾으면 빈 문자열."""
    v = (os.environ.get(name) or "").strip()
    if v or name in _TRIED:
        return v
    _TRIED.add(name)
    try:
        from pathlib import Path as _P

        from dotenv import dotenv_values, find_dotenv
        for p in (find_dotenv(usecwd=True), str(_P.home() / "stock" / ".env")):
            if not p:
                continue
            got = (dotenv_values(p) or {}).get(name)
            if got:
                os.environ[name] = str(got).strip()
                return str(got).strip()
    except Exception as exc:                                   # noqa: BLE001
        # ⚠️ 여기서 삼키면 "키가 .env 에 **있는데** 미설정으로 보고" 가
        # 원인 불명이 된다(2026-08-21 실측: VM 의 `grep -c` 는 1 인데
        # 프로브는 미설정이라 했다 — python-dotenv 미설치 같은 원인이
        # 조용히 묻힌다). silent-except 금지(실수 #12).
        _log.warning("env_key(%s): .env 폴백 실패 — %s: %s",
                     name, type(exc).__name__, exc)
    return ""


def env_ready(*names: str) -> bool:
    """모든 키가 채워지면 True(`.env` 폴백 포함)."""
    return all(env_key(n) for n in names)


def env_source(name: str) -> str:
    """진단용 — 값의 **출처**만 알려준다(값은 절대 돌려주지 않는다)."""
    pre = bool((os.environ.get(name) or "").strip())
    if pre and name not in _TRIED:
        return "환경변수"
    return ".env 파일" if env_key(name) else "없음"


def env_why(name: str) -> str:
    """'없음' 의 **이유**(값은 절대 돌려주지 않는다).

    ⚠️ 왜 필요한가 — 2026-08-21 실측: VM 에서 `.env` 를 grep 하면 키
    줄이 **1건** 잡히는데 프로브는 '미설정'이라
    보고했다. `env_source` 는 '없음'까지만 말해 주므로 그다음을 사람이
    추측하게 된다(추측 금지 규율의 반대). 파일을 찾았는지 · 그 파일에
    키가 있는지 · 값이 비어 있는지를 갈라서 말한다.

    반환은 진단 문자열이고 값의 **길이**까지만 노출한다(길이는 오타·빈값
    판별에 필요하고 값 자체는 절대 아니다)."""
    v = (os.environ.get(name) or "").strip()
    if v:
        return f"환경변수(길이 {len(v)})"
    try:
        from pathlib import Path as _P

        from dotenv import dotenv_values, find_dotenv
    except Exception as exc:                                   # noqa: BLE001
        return f"python-dotenv 없음({type(exc).__name__})"
    seen = []
    for p in (find_dotenv(usecwd=True), str(_P.home() / "stock" / ".env")):
        if not p or not _P(p).exists():
            continue
        seen.append(p)
        try:
            vals = dotenv_values(p) or {}
        except Exception as exc:                               # noqa: BLE001
            return f"{p}: 읽기 실패({type(exc).__name__})"
        if name not in vals:
            continue
        got = (vals.get(name) or "").strip()
        return (f"{p}: 값 있음(길이 {len(got)})" if got
                else f"{p}: 키는 있으나 **값이 비었다**")
    if not seen:
        return ".env 파일을 못 찾음"
    return f"{' · '.join(seen)}: 파일엔 있으나 키 없음"
