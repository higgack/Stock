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

import os

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
    except Exception:
        pass
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
