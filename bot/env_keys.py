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

    파일 I/O 는 키마다 한 번. 못 찾으면 빈 문자열.

    ⚠️ 스캔은 `_dotenv_lookup` **하나**를 쓴다 — 예전엔 여기와 `env_why` 가
    같은 루프를 각각 갖고 있어, 한쪽만 고치면 두 진단이 같은 상태를 다르게
    말할 수 있었다(#38).
    """
    v = (os.environ.get(name) or "").strip()
    if v or name in _TRIED:
        return v
    _TRIED.add(name)
    got, _why, err = _dotenv_lookup(name)
    if err:
        # ⚠️ 여기서 삼키면 "키가 .env 에 **있는데** 미설정으로 보고" 가
        # 원인 불명이 된다(2026-08-21 실측: VM 의 `grep -c` 는 1 인데
        # 프로브는 미설정이라 했다 — python-dotenv 미설치 같은 원인이
        # 조용히 묻힌다). silent-except 금지(실수 #12).
        _log.warning("env_key(%s): .env 폴백 실패 — %s", name, err)
    if got:
        os.environ[name] = got
        return got
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


def _dotenv_lookup(name: str) -> tuple[str | None, str, str]:
    """`.env` 를 **`_TRIED` 캐시를 건너뛰고** 다시 읽어 (값, 사유, 오류)를 낸다.

    ⚠️ 값은 호출부에서 **존재 여부 판정에만** 쓰고 절대 찍지 않는다(§Secrets)
    — 사유 문자열엔 길이까지만 실린다(길이는 빈값·오타 판별에 필요하다, #82).
    ⚠️ `env_key`·`env_why`·`env_diag` 가 **같은 스캔**을 써야 한다 — 복제하면
    한쪽만 고쳐져 세 자리가 다른 말을 한다(#38).

    반환 셋째 값은 **오류 갈래**(없으면 빈 문자열)다 — 호출부가 사유를
    문자열에서 냄새 맡지 않게 구조로 준다(#19).
    """
    v = (os.environ.get(name) or "").strip()
    if v:
        return v, f"환경변수(길이 {len(v)})", ""
    try:
        from pathlib import Path as _P

        from dotenv import dotenv_values, find_dotenv
    except Exception as exc:                                   # noqa: BLE001
        return None, f"python-dotenv 없음({type(exc).__name__})", f"{type(exc).__name__}: {exc}"
    seen, empty = [], ""
    for p in (find_dotenv(usecwd=True), str(_P.home() / "stock" / ".env")):
        if not p or not _P(p).exists():
            continue
        seen.append(p)
        try:
            vals = dotenv_values(p) or {}
        except Exception as exc:                               # noqa: BLE001
            return None, f"{p}: 읽기 실패({type(exc).__name__})", f"{type(exc).__name__}: {exc}"
        if name not in vals:
            continue
        got = (vals.get(name) or "").strip()
        if got:
            return got, f"{p}: 값 있음(길이 {len(got)})", ""
        # ⚠️ 빈 값에서 **멈추면 안 된다** — 뒤 경로에 진짜 값이 있을 수 있다.
        # 옛 `env_key` 는 falsy 면 계속 돌았고, 합치면서 그 동작을 잃을 뻔했다
        # (배포전 셀프리뷰가 잡음 — 함수를 합칠 땐 **반환 조건**을 먼저 볼 것).
        empty = empty or f"{p}: 키는 있으나 **값이 비었다**"
    if empty:
        return None, empty, ""
    if not seen:
        return None, ".env 파일을 못 찾음", ""
    return None, f"{' · '.join(seen)}: 파일엔 있으나 키 없음", ""


def env_why(name: str) -> str:
    """'없음' 의 **이유**(값은 절대 돌려주지 않는다).

    ⚠️ 왜 필요한가 — 2026-08-21 실측: VM 에서 `.env` 를 grep 하면 키
    줄이 **1건** 잡히는데 프로브는 '미설정'이라
    보고했다. `env_source` 는 '없음'까지만 말해 주므로 그다음을 사람이
    추측하게 된다(추측 금지 규율의 반대). 파일을 찾았는지 · 그 파일에
    키가 있는지 · 값이 비어 있는지를 갈라서 말한다.

    반환은 진단 문자열이고 값의 **길이**까지만 노출한다(길이는 오타·빈값
    판별에 필요하고 값 자체는 절대 아니다)."""
    return _dotenv_lookup(name)[1]


def env_diag(*names: str) -> str:
    """미설정 경고에 붙일 **키별 갈래 문자열**(채워진 키는 안 적는다).

    ⚠️ 왜 — '미설정' 이라고만 적으면 그다음(파일을 못 찾았나 · 키가 없나 ·
    값이 비었나 · dotenv 미설치인가)을 운영자가 짐작한다(#82). 갈래마다
    처방이 다르므로 이름을 대야 한다.

    ⚠️ 그리고 갈래가 하나 더 있다: `env_key` 는 **첫 조회 실패를 `_TRIED`
    에 기록하고 다시 안 읽는다**. 그 뒤에 `.env` 가 읽히게 되면(cwd 가
    바뀌거나 파일이 나중에 생기거나) **경고는 남고 값은 있는** 모순이
    생긴다 — 2026-09-07 VM 실측에서 `pykrx: KRX_ID/KRX_PW 미설정` 바로
    뒤에 라이브러리가 `KRX 로그인 완료` 를 찍었다. 그 상태를 추측이 아니라
    **재서** 이름으로 부른다(#165 안 잰 것을 단정하지 말 것 · #279).
    """
    out: list[str] = []
    for n in names:
        if env_key(n):
            continue                       # 이 키는 정상 — 적을 게 없다
        val, why, _err = _dotenv_lookup(n)
        if val is not None:
            # 지금 다시 읽으면 있다 = 첫 조회 시점에만 못 읽은 것이다.
            why += (" — ⚠️ 지금 다시 읽으면 있다(첫 조회가 실패해 캐시됨:"
                    " 실행 cwd·.env 생성 시점 확인)")
        out.append(f"{n}: {why}")
    return " · ".join(out)
