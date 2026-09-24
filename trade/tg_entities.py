"""Telethon 엔티티 해석 · 시작 실패 사유 — trade 스크립트 공용 헬퍼.

왜(2026-08-27 실측, CLAUDE.md #258): 나쁜양파 동기화가
`FloodWaitError: A wait of 8073 seconds is required (caused by
ResolveUsernameRequest)` 로 죽고, 알림은 "Telethon 세션 만료 또는 채널
접근 불가 — 재인증 확인"을 띄웠다. 결함이 둘 겹친 것:

1. `client.get_entity("<username>")` 은 **매 실행마다** ResolveUsernameRequest
   를 쏜다 — 텔레그램은 그 호출을 계정 단위로 강하게 제한한다. 백필 2종
   (6h/2h 타이머) + 리스너 2종(크래시 시 systemd 재시작마다 재해석!) +
   진단 스크립트가 같은 계정으로 반복 해석하니 한도에 걸린다.
   `get_input_entity` 는 **세션 DB 캐시를 먼저** 본다(username 포함) —
   캐시 미스(새 세션)일 때만 네트워크로 나가고, 그 결과가 세션에 저장돼
   다음 실행부터 해석 요청이 0 이 된다.
2. FloodWait 는 세션 만료가 아니다 — 기다리면 풀린다. 사유를 뭉뚱그리면
   운영자가 멀쩡한 세션을 재인증하러 간다(#82 갈래를 이름으로 부를 것).

telethon 을 module-level 로 import 하지 않는다 — 판정은 예외 이름·속성으로
가르므로(순수) telethon 이 없는 샌드박스에서도 테스트가 돈다(#41).

세션 **형식** 가드(2026-09-24 실측, 실수 #404): telethon 은 세션 SQLite 를
열 때 자기가 아는 최신 형식으로 **올리고**(1.45.0 = DB v8, `sessions` 에
`tmp_auth_key` 열 추가), 옛 판은 새 형식을 못 읽는다(1.36.0 = v7 —
`sqlite.py` 64줄 `too many values to unpack (expected 5)`). 운영 유닛은
`.backfill-venv`(고정판)로 도는데, 상한 없는 다른 venv 의 새 telethon 으로
트레이드 스크립트를 한 번 돌리자 `.badonion-session` 이 v8 로 올라가 6시간
동기화가 **생성자에서** 죽었다 — 알림 경로 앞이라 조용했다(#12).
`guarded_client` 가 생성 전에 세션 형식을 재서 (a) 못 읽는 형식이면 처방을
말하고 (b) 운영 세션을 새 형식으로 올리는 것은 **운영 venv 가 고정판일 때만**
허용한다 — 운영 venv 가 아닌 인터프리터는 판이 무엇이든 막는다. 고정판은
`trade/scripts/requirements.txt` 한 곳이다(#38).

⚠️ 판별 기준은 **판이 아니라 인터프리터**다(4차 리뷰 M1). 옛 판은 '판 ≠ 고정판'
을 '운영 venv 가 아니다' 의 대용으로 썼는데, 상한 없는 NOAH `.venv` 가 마침
고정판과 같은 판으로 풀리면(오늘 1.45.0) 그 venv 로 돈 실행이 운영 세션을 올려도
통과했다 — 핀은 `git pull` 로 움직이지만 운영 venv 는 사람이 pip 를 돌려야
움직이므로, 그 사이 운영 유닛이 옛 판인 창에서 #404 가 그대로 재발한다.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger("trade.tg_entities")

# 트레이드 텔레그램 스크립트가 쓰는 telethon 고정판의 **단일 출처** — 운영
# venv 를 까는 파일이다(trade/README.md). 여기서 읽어야 핀과 가드가 안 갈린다.
_REQUIREMENTS = Path(__file__).resolve().parent / "scripts" / "requirements.txt"
# 운영 유닛(deploy/trade-bot-*.service ExecStart)이 쓰는 venv — 회귀가 대조한다.
PROD_VENV = ".backfill-venv"


class SessionFormatError(RuntimeError):
    """세션 파일 형식이 이 인터프리터의 telethon 과 안 맞는다. 재시작으로는
    안 풀리는 **설정 오류**다(리스너는 EX_CONFIG 로 끝나 재시작 루프를 막는다)."""


def pinned_telethon(path=None) -> str | None:
    """`trade/scripts/requirements.txt` 의 `telethon==X` → X. 못 읽으면 None."""
    try:
        text = Path(path or _REQUIREMENTS).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"(?mi)^\s*telethon\s*==\s*([0-9][0-9A-Za-z.+-]*)\s*$", text)
    return m.group(1) if m else None


def _session_db_probe(session) -> tuple[int | None, str]:
    """(DB 형식 버전, 못 잰 사유). 파일이 없으면 `(None, "")` — 보호할 파일이
    없는 것(첫 인증)이지 못 잰 것이 아니다. 파일은 있는데 못 읽으면(핫 저널 ·
    잠금 · 손상 · version 행 없음) 사유를 이름으로 준다(#82).

    **읽기 전용**(`mode=ro`)으로 연다 — 형식을 재는 쪽이 운영 파일을 건드리거나
    잠그면 안 된다(#264). 쓰기 모드로 열면 SQLite 가 핫 저널을 **롤백**해 파일을
    바꾼다 — 읽기 전용은 롤백하지 못해 오류로 끝나고, 그건 못 잰 것이다."""
    name = str(session)
    p = Path(name if name.endswith(".session") else f"{name}.session")
    if not p.is_file():
        return None, ""
    try:
        con = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("select version from version").fetchone()
        finally:
            con.close()
    except Exception as exc:                                   # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if not row:
        return None, "version 테이블에 행이 없다"
    try:
        return int(row[0]), ""
    except (TypeError, ValueError):
        return None, f"version 값을 못 읽었다({row[0]!r})"


def session_db_version(session) -> int | None:
    """세션 파일의 DB 형식 버전(telethon 의 `version` 테이블). 파일이 없거나
    못 읽으면 None(`_session_db_probe` 가 둘을 가른다)."""
    return _session_db_probe(session)[0]


def telethon_db_version() -> tuple[str | None, int | None]:
    """(설치된 telethon 판, 그 판이 쓰는 세션 DB 형식). 못 재면 (None, None)."""
    try:
        import telethon
        from telethon.sessions import sqlite as _sq
        return str(telethon.__version__), int(_sq.CURRENT_VERSION)
    except Exception:                                          # noqa: BLE001
        return None, None


def in_prod_venv() -> bool:
    """이 인터프리터가 운영 유닛의 venv 인가 — 판이 아니라 **경로**로 잰다(M1)."""
    return f"/{PROD_VENV}/" in sys.executable.replace("\\", "/")


def session_format_problem(session, *, live: bool = True) -> str | None:
    """세션을 열기 **전에** 형식을 잰다 → 막아야 하면 처방을 담은 문장, 아니면 None.

    - 파일 형식 > 이 telethon 형식: 못 연다(열면 telethon 이 알아볼 수 없는
      unpack 오류로 죽는다). 어느 판을 깔아야 하는지 말한다 — 운영 venv 면 그
      venv 에 고정판을, 아니면 운영 venv 로 돌리라고(다른 프로젝트의 venv 에
      깔라고 하지 않는다), 고정판 자신이 못 읽으면 핀을 올리라고.
    - 파일 형식 < 이 telethon 형식 + **운영 세션**(`live`): 열면 파일이 새 형식으로
      올라간다 — 운영 유닛이 모두 운영 venv 로 도므로, 그 venv 가 **고정판**일 때만
      허용한다(핀을 올리는 정상 업그레이드). 운영 venv 가 아니면 **판이 무엇이든**
      막는다 — 2026-09 사고의 원인 방향이고, 판으로 가르면 다른 venv 가 마침
      고정판과 같은 판일 때 뚫린다(4차 리뷰 M1). 운영 venv 인데 고정판이 아니면
      고정판을 깔라고 한다(L1 — 이미 그 venv 에 있는 사람에게 '그 venv 로 돌려라'
      는 처방이 아니다).
    - 재지 못하면(세션 파일을 못 읽음 · telethon 을 못 잼 · 운영 venv 인데 핀을
      못 읽음) 막지 않고 **경고 로그로 말한다** — 못 잰 것으로 막으면 첫 인증부터
      막힌다(#54 는 '말하라' 이지 '멈추라' 가 아니다, 4차 리뷰 L3). 파일이 아예
      없으면(첫 인증) 보호할 것이 없어 조용히 통과한다.
    - 복사본(`live=False` — dry-run·진단)은 올라가도 무해하다."""
    name = Path(str(session)).name
    f, why = _session_db_probe(session)
    if f is None:
        if why:
            log.warning("세션 %s 형식을 못 쟀다(%s) — 형식 가드를 건너뛴다",
                        name, why)
        return None
    ver, lib = telethon_db_version()
    if lib is None:
        log.warning("설치된 telethon 의 세션 형식을 못 쟀다 — 세션 %s(DB v%d) "
                    "형식 가드를 건너뛴다", name, f)
        return None
    pin = pinned_telethon()
    prod = in_prod_venv()
    if f > lib:
        if prod and pin and pin != ver:
            fix = f"{sys.executable} -m pip install telethon=={pin}"
        elif not prod and pin and pin != ver:
            # 운영 venv 가 아니면 **이** 인터프리터에 깔라고 하지 않는다 — 다른
            # 프로젝트의 venv(NOAH `.venv`)의 telethon 을 바꾸게 된다. 운영 유닛이
            # 도는 venv 를 가리킨다(실수 #404 — 틀린 venv 가 이 사고의 시작이다).
            fix = (f"운영 유닛은 {PROD_VENV} 로 돈다 — 그 venv 로 돌릴 것: cd "
                   f"~/stock-trade && {PROD_VENV}/bin/python … (그 venv 의 telethon 이 "
                   f"고정판이 아니면 {PROD_VENV}/bin/pip install telethon=={pin})")
        elif pin:
            fix = (f"고정판(telethon {pin}, trade/scripts/requirements.txt)도 이 형식을 "
                   "못 읽는다 — 고정판을 그 파일을 쓴 판 이상으로 올려 다시 깔 것")
        else:
            # 핀을 못 읽었으면 'telethon None' 을 고정판처럼 적지 않는다(L2).
            fix = ("고정판을 못 읽었다(trade/scripts/requirements.txt 의 telethon== "
                   f"줄) — 그 줄을 확인하고 이 파일을 쓴 판 이상을 {PROD_VENV} 에 깔 것")
        return (f"세션 {name} 은 DB v{f} 형식인데 이 인터프리터의 telethon {ver} 은 "
                f"v{lib} 까지만 읽는다 — 더 새 telethon 이 이 파일을 연 적이 있다"
                f"(다른 venv 로 돌린 실행). 처방: {fix}")
    if f < lib and live:
        if not prod:
            return (f"이 인터프리터({sys.executable})는 운영 venv({PROD_VENV})가 "
                    f"아니다 — 그 telethon {ver}(DB v{lib})이 운영 세션 {name}(DB v{f})"
                    "을 새 형식으로 올리면 운영 유닛이 그 뒤로 이 파일을 못 열 수 있다"
                    "(실수 #404 — 판이 고정판과 같아도 운영 venv 에 그 판이 깔렸다는 "
                    "보장은 없다). 운영과 같은 venv 로 돌릴 것: cd ~/stock-trade && "
                    f"{PROD_VENV}/bin/python …")
        if not pin:
            log.warning("고정판을 못 읽어(trade/scripts/requirements.txt) 운영 venv 의 "
                        "telethon %s 이 고정판인지 모른다 — 세션 %s 을 DB v%d → v%d 로 "
                        "올리는 것을 막지 않는다", ver, name, f, lib)
            return None
        if ver != pin:
            return (f"운영 venv 의 telethon {ver}(DB v{lib})이 고정판({pin})이 아니다 — "
                    f"이대로 열면 운영 세션 {name}(DB v{f})이 고정판이 아닌 판의 형식으로 "
                    f"올라간다. 고정판을 깔 것: {sys.executable} -m pip install "
                    f"telethon=={pin}")
    return None


def guarded_client(factory, session, *args, live: bool = True, **kwargs):
    """`TelegramClient(session, …)` 대신 — 형식 가드를 통과해야 만든다.
    막히면 `SessionFormatError`(처방 문장)를 올리고 클라이언트를 만들지 않는다."""
    problem = session_format_problem(session, live=live)
    if problem:
        raise SessionFormatError(problem)
    return factory(session, *args, **kwargs)


class SessionNotAuthorizedError(RuntimeError):
    """세션 **복사본**(dry-run·진단)이 인증되지 않았다 — 복사본에는 로그인하지
    않는다(`start_client`)."""


async def start_client(client, *, copy: bool) -> None:
    """라이브 세션이면 `start()` — 인증이 풀렸으면 로그인 흐름을 타고, 그
    로그인은 원본 파일에 남는다. **복사본**이면 로그인하지 않는다: 인증이 풀린
    세션을 `start()` 하면 전화번호·코드를 묻고, 그 로그인은 복사본에 저장됐다가
    실행 끝에 임시 디렉터리와 함께 지워진다(#403 3차 리뷰 L2). 그래서 복사본은
    `connect()` 뒤 인증만 확인하고, 안 됐으면 멈춰 처방을 말한다."""
    if not copy:
        await client.start()
        return
    await client.connect()
    if not await client.is_user_authorized():
        raise SessionNotAuthorizedError(
            "세션이 인증되지 않았다 — 복사본(dry-run·진단)으로는 로그인하지 "
            "않는다(그 로그인은 복사본과 함께 지워진다). 이 세션을 쓰는 실제 "
            "실행을 대화형으로 한 번 돌려 로그인한 뒤 다시 돌릴 것(trade/README.md)")


async def resolve_peer(client, ref):
    """username/id → InputPeer. **세션 캐시 우선**(get_input_entity) —
    문자열 username 을 get_entity 로 해석하면 매번 ResolveUsernameRequest 가
    나간다. 캐시 미스(새 세션) 첫 1회만 get_entity 로 채운다.

    다운스트림(iter_messages · forward_messages · events.NewMessage(chats=) ·
    telethon.utils.get_peer_id)은 전부 InputPeer 를 받는다 — 단 `.id` 속성은
    InputPeer 에 없으므로 로깅은 get_peer_id 로 할 것.
    """
    try:
        return await client.get_input_entity(ref)
    except Exception:                                          # noqa: BLE001
        return await client.get_entity(ref)


def startup_failure_note(exc) -> str:
    """세션/접근 실패 알림에 붙일 **사유 갈래**(#82). 갈래별 처방이 다르다 —
    FloodWait 에 '재인증 확인'이라고 적으면 운영자가 헛걸음한다."""
    name = type(exc).__name__
    secs = getattr(exc, "seconds", None)
    if name in ("SessionFormatError", "SessionNotAuthorizedError"):
        return str(exc)            # 처방까지 담은 문장(guarded_client·start_client)
    if name == "FloodWaitError":
        tail = ""
        if isinstance(secs, (int, float)) and secs == secs:
            tail = f" {int(secs)}초(≈{int(secs) // 60}분)"
        return (f"텔레그램 요청 제한(FloodWait{tail}) — 세션은 정상이고 "
                "재인증 불필요. 대기시간이 지나면 다음 정기 실행이 자동 "
                "재시도합니다.")
    if name in ("AuthKeyError", "AuthKeyUnregisteredError",
                "AuthKeyDuplicatedError", "SessionRevokedError",
                "SessionExpiredError", "SessionPasswordNeededError",
                "UnauthorizedError"):
        return "Telethon 세션 만료/철회 — 재인증이 필요합니다."
    if name in ("ChannelPrivateError", "ChannelInvalidError",
                "UsernameNotOccupiedError", "UsernameInvalidError"):
        return ("채널 접근 불가(비공개 전환·링크 변경 가능성) — 채널 상태를 "
                "확인하세요.")
    return "원인 미상 — 세션/채널/네트워크 갈래를 로그로 확인하세요."
