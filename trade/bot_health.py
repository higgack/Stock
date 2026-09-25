"""trade-bot 수신 경로 진단 — **읽기 전용** (`python -m trade.bot_health`).

⚠️ 왜 있나 (실수 #406, 2026-09-25). 40일 회수가 나쁜양파 27건을 비공개 채널로
포워드했다("done: forwarded 27 of 27")는데 32분 뒤에도 inbox 가 한 줄도 안
늘었고, trade-bot 저널의 `ingested msg=` 는 0 이었다. 그 0 은 갈래가 여럿이고
처방이 전부 다르다(#82):

  · 봇 프로세스가 안 돈다 / 폴링이 멈췄다          → 되살리기(24시간 안이면 밀린 글이 온다)
  · 같은 토큰으로 다른 곳이 폴링·웹훅(409)          → 그쪽을 끄기
  · 텔레그램 수신 종류에 channel_post 가 빠졌다     → 봇 재시작(수신 종류를 명시하는 판) 뒤 다시 포워드
  · 봇이 대상 채널의 관리자가 아니다                → 관리자로 다시 추가 뒤 다시 포워드
  · 받고 채널·출처 게이트에서 버렸다                → `.env` 를 고치고 재시작 뒤 다시 포워드
  · 릴레이가 보증한 재게시 글을 버렸다(실수 #411)   → 보증 기록·데이터 디렉터리 확인 뒤 다시 포워드

그걸 사람이 명령 네 개로 맞춰 보게 하지 않고 한 번에 가른다(§Automation-first
· #252). 그리고 같은 판정(`delivery_gap`)을 `trade.scripts.health_check` 가
매시간 돌려, 릴레이가 보낸 만큼 봇이 못 받았으면 **사람이 알아채기 전에**
알린다.

'받았다' 는 봇 저널의 **수신 줄(ingested) + 출처 게이트 버림 줄**이다 — 버림은
게이트의 판정이지 수신 실패가 아니다. BeOn 은 다른 채널(AWAKE 플러스 등)의 글을
그대로 되포워드하는데, 텔레그램은 포워드의 포워드에도 **원래 출처**를 달아 봇이
그 글을 출처 게이트에서 버린다 — 그걸 '못 받았다' 로 세면 매번 거짓 누락이다
(독립 리뷰 H2). 릴레이 원천의 글을 버렸으면 그건 따로 ❌ 다. 나쁜양파가 다른 채널에서
퍼 온 **재게시** 글은 릴레이가 포워드 전에 보증하고(`trade.relay_origins`, 실수 #411) 봇이
받는다 — 보증 **뒤의** 버림은 릴레이 글의 버림이라 ❌ 다(보증 경로가 깨졌다: 봇이 기록을
못 읽었거나 데이터 디렉터리가 갈렸다 — 버림 줄의 `vouch=` 칸이 봇 쪽 사유다). 보증 **전의**
버림(버릴 땐 보증이 없었다 — 보증하기 전의 판이나 다른 릴레이가 포워드했다)은 봇의 보증 수용
줄(`accepted … reason=relay_vouch`)로 다시 받았는지까지 메모한다. 보증 **뒤의** 버림도 그 뒤
같은 원래 글의 수용 줄이 있으면 받은 것이다 — ❌ 가 아니라 메모다(독립 리뷰 #411 L5).
⚠️ 보증이 없는 다른 출처 포워드의 버림은 **판정하지 않는다** — 보증하기 전의 판이 포워드한
재게시 글과 BeOn 의 되포워드가 같은 모양으로 와서 여기선 못 가른다(3차 독립 리뷰 M2). ⚠️ 메모가 출처·건수와 가르는 명령(`FIND_CMD` — 그 글이 여전히
to-forward 면 손실)을 말한다.

판정은 **지금 도는 프로세스(MainPID)의 줄**로 한다 — 재시작 전 프로세스의 409·
버림·예외로 놓친 릴레이 글은 옛 판·옛 설정의 일이라 지금 상태의 증거가 아니다(사실
메모로 따로 말한다, 독립 리뷰 H1). 판정은 `--since` 창 안의 줄로만 한다 — 봇 저널을
그보다 앞에서 읽는 것은 **수신을 셀 때만**이다(2차 독립 리뷰 L5). 지금 도는 봇이 버림을
적지 않는 **옛 판**이면 증상이 없어도 ❓(rc 2)다 — 이 진단이 가르려는 '받고 버렸나 /
아예 안 왔나' 를 그 저널은 말하지 않는다. 재게시 보증을 모르는 판(시작 줄에
`relay_vouch=on` 이 없다)도 ❓ 다 — 다시 포워드해도 재게시 글을 또 버린다(실수 #411). 그래서
배포 직후 `이 명령 && 다시 포워드` 는 봇이 새 판으로 재시작되기 전엔 다시 포워드를 내보내지
않는다(실수 #409).

채널 글을 처리하다 예외로 끝나 수신 줄이 없는 번호는 손실의 **직접 증거**다 — 그
글의 포워드 출처가 릴레이 원천이면 수 대조와 무관하게 ❌ 다(2차 독립 리뷰 H1). 수
대조는 번호로 짝을 짓지 않아, 같은 창의 다른 글 수신·버림이 진짜 손실을 **덮을 수
있다**(아래 못 보는 축).

읽기 전용 계약 — 텔레그램엔 `READ_ONLY_METHODS`(getMe · getWebhookInfo ·
getChatMember · getChat)만 묻는다. ⚠️ getUpdates 는 **절대** 부르지 않는다: 돌고 있는
봇과 409 로 부딪히고, 여기서 받아 간 업데이트는 봇이 영영 못 본다. 파일은
읽기만 한다(#264). 토큰은 어떤 출력에도 싣지 않는다 — 오류 문구에 URL 이
섞여도 지운다. 봇은 이 판부터 자기 로그의 토큰을 가리지만(`trade/bot.py`
`_TokenRedactFormatter`), 그 전 판이 찍은 getUpdates 줄엔 토큰이 **평문**이고
저널 보존기간 동안 남는다(§Secrets).

python-telegram-bot 을 import 하지 않는다 — 봇 import 가 깨진 날에도 이 진단은
돌아야 한다(그게 가를 갈래 중 하나다). 판정은 순수 함수라 의존성 없이 태운다
(#176).

⚠️ 못 보는 축(#274): 릴레이 원천(사용자명)은 `trade/scripts/*.py` 의 모듈 수준
`SOURCE_USERNAME` 에서 파생하고, 릴레이 유닛 목록(`RELAY_UNITS`)은 손으로 적되
`deploy/` 의 유닛과 회귀가 대조한다. 손으로 돌린 백필은 저널에 안 남아 '릴레이
포워드' 계수에 안 잡힌다(systemd 유닛의 실행만 센다) — 그 수신은 받음으로는 세어져
같은 창의 누락을 덮을 수 있다. 수 대조(보냄 vs 받음)는 번호로 짝을 짓지 않는다 —
운영자가 채널에 직접 포워드한 글이 버려지거나 기록되면 그만큼 받음이 늘어 **예외 없는**
손실(텔레그램이 안 준 글)을 가릴 수 있다. 예외로 끝난 글은 그 줄 자체가 증거라 이
한계 밖이다. 채널 게이트 버림은 봇이 채널마다 프로세스당 한 번만 적어 **건수로 못
센다** — 목적지가 걸리면 그 사실만 ❌ 로 말한다.

Usage on host (시각은 journalctl 규약 — 시간대를 붙이지 않으면 **서버 로컬**
시각이다. 어디서나 같은 뜻은 UTC 로 적는다, 규칙 10a):
    cd ~/stock-trade && .venv/bin/python -m trade.bot_health
    cd ~/stock-trade && .venv/bin/python -m trade.bot_health --since "2026-09-24 22:40 UTC"
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade import relay_origins as _relay
from trade.listener_health import _ts, listener_verdict, scanned_span

_VER = 1
_UNIT = "trade-bot"
_SERVICE = f"{_UNIT}.service"
_REPO = Path(__file__).resolve().parents[1]
_KST = timezone(timedelta(hours=9))
USAGE = "cd ~/stock-trade && .venv/bin/python -m trade.bot_health"
# 한 글이 **어느 갈래**(포워드할 것 · 이미 inbox · 필터 밖)인지 보는 명령 — 수로 센 메모
# 끝에서 '다음 수' 로 건넨다(3차 독립 리뷰 M2·M3). 인터프리터는 동기화 유닛과 같은
# `.backfill-venv`(deploy/trade-bot-badonion-sync.service) · `--find` 는 dry-run 을 강제한다.
FIND_CMD = ("cd ~/stock-trade && .backfill-venv/bin/python trade/scripts/backfill_badonion.py "
            "--dry-run --since <YYYY-MM-DD> --find <글자>")

# 텔레그램엔 이것만 묻는다 — 목록 밖 메서드는 `tg_call` 이 거부한다.
READ_ONLY_METHODS = ("getMe", "getWebhookInfo", "getChatMember", "getChat")
_ADMIN = ("creator", "administrator")

# 채널 글을 비공개 채널로 넘기는 유닛들(포워드 계수의 모집단). ⚠️ 손으로 적는다 —
# 회귀가 `deploy/*.service` 중 `SOURCE_USERNAME` 을 둔 스크립트를 띄우는 유닛과
# **양방향**으로 대조한다(하나를 빼면 그 릴레이의 포워드는 조용히 안 세어진다).
RELAY_UNITS = ("trade-bot-badonion-listener", "trade-bot-beon-listener",
               "trade-bot-badonion-sync", "trade-bot-beon-sync")

# 폴링이 이보다 오래 없으면 멈춘 것 — watchdog(deploy/trade-watchdog.sh) 과 같은 창.
POLL_STALE_S = 300
# 포워드 뒤 이만큼은 기다려야 '안 왔다' 고 말한다 — 봇은 보통 몇 초 안에 받는다.
GAP_GRACE_S = 180
# 백필의 'done: forwarded N' 은 **실행 끝**에 한 번 찍힌다 — 그 N 건의 실제 포워드는
# 그보다 앞이다(2026-09-25 실측 17초 · 큰 BeOn 회수는 더 길다). 수신은 그 실행이 접속
# 직후 찍는 `source(marked)=` 줄(같은 PID)부터 센다. 그 줄이 창 밖이면(실행이 창 앞에서
# 시작) 끝에서 이만큼 앞부터 센다 — 이보다 긴 실행은 앞부분 수신을 못 세어 '일부 누락'
# 으로 과대보고할 수 있어, 그 경우는 판정 줄이 그렇다고 밝힌다.
RUN_SLACK_S = 1800
# 리스너의 'forwarded N msg(s)' 는 forward 호출이 돌아온 **뒤** 찍힌다 — 봇이 그보다
# 먼저 받을 수 있다(같은 서버·같은 시계라 보통 1초 안이지만, 리스너 이벤트 루프가
# 바쁘면 로그가 늦는다). 그만큼 앞의 수신도 그 포워드의 몫으로 센다(독립 리뷰 M1).
LISTEN_SLACK_S = 30

# 봇이 main() 에서 `trade-bot starting — …` 를 찍는다(trade/bot.py).
_START_RE = re.compile(
    r"trade-bot starting — inbox=(?P<inbox>.+?) media=(?P<media>.+?) "
    r"allowed=(?P<allowed><discovery>|\{[^}]*\}) "
    r"origin=(?P<origin><any>|\{[^}]*\}) concurrency=(?P<conc>\d+)(?P<rest>.*)$")
_PID_RE = re.compile(r"\[(\d+)\]:")
_POLL_RE = re.compile(r'/getUpdates "HTTP/[\d.]+ (\d{3})')
_FWD_RE = re.compile(r"(?P<who>[\w.]+) — (?:done: )?forwarded (?P<n>\d+) "
                     r"(?:of \d+ candidate messages|msg\(s\))")
# 릴레이가 접속 직후 찍는 줄(리스너 'connected: source(marked)=…' · 백필 'source(marked)=…')
_SRC_RE = re.compile(r"source\(marked\)=(?P<src>-?\d+)")
_ERR_RE = re.compile(r"\[(?:ERROR|CRITICAL)\]|Traceback|telegram\.error\.")
_DROP_ORIGIN_RE = re.compile(r"dropped msg=(?P<msg>\S+) reason=origin origin_type=(?P<type>\S+) "
                             r"origin_chat=(?P<chat>\S+) origin_username=(?P<user>\S+)"
                             r"(?: origin_msg=(?P<omsg>\S+))?")
# 버림 줄 꼬리(실수 #411 판부터): 보증 대조 결과 · 원래 채널 제목(repr — 사람이 붙인 자유 문자열이라
# 줄 끝에 둔다). 옛 판 줄엔 없다(None — 지어내지 않는다, #165).
_VOUCH_FIELD_RE = re.compile(r" vouch=(?P<v>\S+)")
_TITLE_RE = re.compile(r" origin_title=(?P<t>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|None)\s*$")
# 봇이 릴레이 보증으로 받은 재게시 글(trade/bot.py `_log_vouch_accept`) — 그 경로가 일했다는 긍정 증거
_ACCEPT_RE = re.compile(r"accepted msg=(?P<msg>\d+) reason=relay_vouch origin_chat=(?P<chat>\S+) "
                        r"origin_msg=(?P<omsg>\S+)")
_DROP_CHANNEL_RE = re.compile(r"dropped channel=(?P<chat>-?\d+)")
_INGEST_RE = re.compile(r" ingested msg=(?P<msg>\d+)")
# 봇의 에러 핸들러(trade/bot.py `_on_error`)가 찍는 한 줄 표식 — 핸들러 예외와 폴링 오류를
# 가른다. 채널 글이면 포워드 출처도 버림 줄과 같은 규약으로 싣는다(2차 독립 리뷰 H1).
# 옛 판(에러 핸들러 없음)은 PTB 기본 문구만 남겨 어느 업데이트였는지 모른다.
_EXC_RE = re.compile(r"(?P<kind>handler) error update=(?P<upd>\S+) msg=(?P<msg>\S+)"
                     r"(?: origin_type=(?P<type>\S+) origin_chat=(?P<chat>\S+)"
                     r" origin_username=(?P<user>\S+)(?: origin_msg=(?P<omsg>\S+))?)?"
                     r"|(?P<pkind>polling) error exc=")
_PTB_UNLABELED = "No error handlers are registered"
# 줄 끝의 **남이 쓴** 자유 문자열 — 원래 채널 제목(repr, 채널 주인이 붙인다)과 예외 문구(라이브러리·
# 글 내용). 분류는 그 **앞**(봇이 쓴 칸)만 본다 — 제목에 `ingested msg=999` 나
# `/getUpdates "HTTP/1.1 200 OK` 가 들어 있으면 그 버림 줄이 수신·정상 폴링으로 읽혀 버림이
# 사라지고 없는 수신이 생겼다(독립 리뷰 #411 L2 실측). 앞쪽 칸은 봇이 쓴 값뿐이다 — 사용자명은
# [A-Za-z0-9_], 허용 목록은 운영자 .env — 그래서 **첫** 표식에서 자른다. 제목·원문 줄은 따로
# 원문에서 읽는다(`_title`·`scrub`).
_FREE_TAILS = (" origin_title=", " exc=")


def _bot_part(line: str) -> str:
    """분류용 — 줄에서 남이 쓴 꼬리를 뗀다(표식 자체는 남긴다: `polling error exc=` 판별)."""
    for sep in _FREE_TAILS:
        i = line.find(sep)
        if i >= 0:
            line = line[:i + len(sep)]
    return line


def _sig() -> str:
    """이 파일의 소스 지문 — 어느 코드가 이 출력을 만들었나(#364·#21)."""
    try:
        return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
    except Exception:                                          # noqa: BLE001
        return "지문불가"


def scrub(text, token: str = "") -> str:
    """출력 직전의 비밀값 제거 — 토큰 리터럴 + 봇 토큰 모양 전부(§Secrets)."""
    from bot.daily_kr_flow import redact

    s = str(text)
    if token:
        s = s.replace(token, "<TOKEN>")
    return redact(s)


def _kst(t: datetime | None) -> str:
    return t.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S KST") if t else "—"


def _age(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 90:
        return f"{sec}초"
    if sec < 5400:
        return f"{sec // 60}분"
    if sec < 172800:
        return f"{sec // 3600}시간"
    return f"{sec // 86400}일"


def _msgs(items, cap: int = 8) -> str:
    """메시지 번호 목록 — 길면 앞 몇 개와 나머지 수(자른 사실을 말한다, #45)."""
    ids = [str(e.get("msg")) for e in items]
    return ", ".join(ids[:cap]) + (f" 외 {len(ids) - cap}건" if len(ids) > cap else "")


# ── 순수 판정 ───────────────────────────────────────────────────────────

def parse_start_line(line: str) -> dict | None:
    """봇 시작 줄 → 실행 중 설정. 순수 함수.

    `allowed`/`origin` 은 `"any"`(목록 없음 = 전부 받는다) | 집합 | `None`
    (읽지 못함 — 판정 불가, 전부 받는다고 **가정하지 않는다**, #165).
    `drop_log` 는 이 프로세스가 게이트 버림을 저널에 적는 판인가(실수 #406),
    `vouch` 는 릴레이가 보증한 재게시 글을 받는 판인가(실수 #411).
    """
    m = _START_RE.search(line or "")
    if not m:
        return None

    def _set(raw: str, any_word: str):
        if raw == any_word:
            return "any"
        try:
            v = ast.literal_eval(raw)
        except Exception:                                      # noqa: BLE001
            return None
        return set(v) if isinstance(v, (set, frozenset, list, tuple)) else None

    pid = _PID_RE.search(line)
    return {"ts": _ts(line), "pid": int(pid.group(1)) if pid else None,
            "inbox": m.group("inbox"),
            "allowed": _set(m.group("allowed"), "<discovery>"),
            "origin": _set(m.group("origin"), "<any>"),
            "drop_log": "drop_log=on" in m.group("rest"),
            "vouch": "relay_vouch=on" in m.group("rest")}


def _pid(line: str) -> int | None:
    m = _PID_RE.search(line or "")
    return int(m.group(1)) if m else None


def _int(raw) -> int | None:
    s = str(raw or "")
    return int(s) if s.lstrip("-").isdigit() else None


def _origin_of(m) -> dict:
    """버림·예외 줄의 포워드 출처 칸(`origin_type/chat/username/msg`) — 두 줄이 **같은
    규약**(trade/bot.py `_origin_fields`)으로 찍고 여기서 같은 규약으로 읽는다(#38).
    원래 글번호(`omsg`)는 실수 #411 판부터 찍힌다 — 옛 줄은 None."""
    omsg = m.group("omsg") if "omsg" in m.re.groupindex else None
    return {"type": m.group("type"), "chat": _int(m.group("chat")),
            "user": "" if m.group("user") == "None" else m.group("user"),
            "omsg": _int(omsg)}


def _title(line: str) -> str | None:
    """줄 끝 `origin_title=…`(repr) → 채널 제목. 없거나 못 읽으면 None(지어내지 않는다)."""
    m = _TITLE_RE.search(line or "")
    if not m:
        return None
    try:
        v = ast.literal_eval(m.group("t"))
    except Exception:                                          # noqa: BLE001
        return None
    return scrub(v) if isinstance(v, str) else None


def journal_facts(lines) -> dict:
    """trade-bot 저널 줄 → 폴링·수신·버림·예외 사실. 순수 함수(비밀값은 지워서 싣는다).

    수신·버림·예외는 **시각과 메시지 번호**까지 싣는다 — 대조 창(`delivery_gap`)과
    '예외로 놓친 글인가'(수신 줄이 없는 번호, `lost_posts`) 판정이 그걸로 한다. 채널 글
    예외는 포워드 출처도 싣는다 — 릴레이 원천의 글인지 가른다. 출처를 적지 않는 판이 찍은
    예외 줄은 `type` 이 None 이다(출처를 모른다 — 지어내지 않는다, #165).
    """
    polls: Counter = Counter()
    last_ok = last_poll = None
    ingested: list = []
    ingested_ids: set[int] = set()
    drops_origin: list[dict] = []
    drops_channel: list[dict] = []
    vouch_accepts: list[dict] = []
    starts: list[dict] = []
    errors: list[str] = []
    exceptions: list[dict] = []
    for ln in lines or []:
        v = _bot_part(ln)
        m = _POLL_RE.search(v)
        if m:
            polls[m.group(1)] += 1
            t = _ts(ln)
            last_poll = t or last_poll
            if m.group(1) == "200":
                last_ok = t or last_ok
            continue
        m = _INGEST_RE.search(v)
        if m:
            ingested.append(_ts(ln))
            ingested_ids.add(int(m.group("msg")))
            continue
        m = _ACCEPT_RE.search(v)
        if m:
            vouch_accepts.append({"ts": _ts(ln), "msg": _int(m.group("msg")),
                                  "chat": _int(m.group("chat")),
                                  "omsg": _int(m.group("omsg")), "title": _title(ln)})
            continue
        m = _DROP_ORIGIN_RE.search(v)
        if m:
            vm = _VOUCH_FIELD_RE.search(v, m.end())
            drops_origin.append({"ts": _ts(ln), "line": scrub(ln), "msg": _int(m.group("msg")),
                                 **_origin_of(m), "vouch": vm.group("v") if vm else None,
                                 "title": _title(ln)})
            continue
        m = _DROP_CHANNEL_RE.search(v)
        if m:
            drops_channel.append({"ts": _ts(ln), "line": scrub(ln), "chat": int(m.group("chat"))})
            continue
        if "trade-bot starting" in v:
            p = parse_start_line(ln)
            if p:
                starts.append(p)
            continue
        m = _EXC_RE.search(v)
        if m:
            exceptions.append({"ts": _ts(ln), "line": scrub(ln),
                               "kind": m.group("kind") or m.group("pkind"),
                               "update": m.group("upd"), "msg": _int(m.group("msg")),
                               **(_origin_of(m) if m.group("type") is not None else
                                  {"type": None, "chat": None, "user": "", "omsg": None})})
        elif _PTB_UNLABELED in v:
            exceptions.append({"ts": _ts(ln), "line": scrub(ln), "kind": "unlabeled",
                               "update": None, "msg": None, "type": None, "chat": None,
                               "user": "", "omsg": None})
        if _ERR_RE.search(ln):
            errors.append(scrub(ln))
    return {"n": len(lines or []), "span": scanned_span(lines or []),
            "polls": dict(polls), "last_ok": last_ok, "last_poll": last_poll,
            "ingested": ingested, "ingested_ids": ingested_ids,
            "drops_origin": drops_origin, "drops_channel": drops_channel,
            "vouch_accepts": vouch_accepts,
            "starts": starts, "exceptions": exceptions, "errors": errors[-6:]}


def relay_forwards(lines) -> list[dict]:
    """릴레이 유닛 저널 → 포워드 사건(시각·건수·누가·언제부터 세나). 순수 함수.

    `start` 는 그 사건의 봇 수신을 **어디부터** 셀지다:
      · 리스너('forwarded N msg(s)')는 포워드 직후 찍힌다 → 그 시각 − `LISTEN_SLACK_S`
      · 백필('done: forwarded N …')은 실행 **끝**에 찍힌다 → 같은 PID 가 접속 직후 찍은
        `source(marked)=` 줄(포워드 전) 시각. 그 줄이 창 밖이면(실행이 창 앞에서 시작)
        끝 − `RUN_SLACK_S` 로 짐작하고 `guessed` 로 표시한다(판정 줄이 그렇다고 밝힌다).
    시각을 못 읽은 사건은 `start` 도 None(지어내지 않는다).
    """
    out = []
    run_at: dict[int, datetime] = {}
    for ln in lines or []:
        t = _ts(ln)
        pid = _pid(ln)
        if _SRC_RE.search(ln) and pid is not None and t is not None:
            run_at[pid] = t                  # 같은 PID 의 가장 최근 접속(재사용돼도 최근 것)
            continue
        m = _FWD_RE.search(ln)
        if not m or int(m.group("n")) <= 0:
            continue
        done = "done: forwarded" in ln
        begun = run_at.pop(pid, None) if done and pid is not None else None
        guessed = False
        if t is None:
            start = None
        elif not done:
            start = t - timedelta(seconds=LISTEN_SLACK_S)
        elif begun is not None and begun <= t:
            start = begun
        else:
            start, guessed = t - timedelta(seconds=RUN_SLACK_S), True
        out.append({"ts": t, "n": int(m.group("n")), "who": m.group("who"), "pid": pid,
                    "done": done, "start": start, "guessed": guessed})
    return out


def relay_source_ids(lines) -> set[int]:
    """릴레이 저널이 스스로 밝힌 원천 채널 ID(`source(marked)=`). 순수 함수.

    원천이 사용자명을 바꾸면 봇의 버림 줄엔 **새 이름**이 찍혀 이름으로는 릴레이인지
    못 알아본다 — ID 는 안 바뀐다. 매시간 알림은 텔레그램·inbox 를 안 보므로 이게
    유일한 ID 원천이다(창 안에 그 릴레이가 접속한 줄이 있을 때만)."""
    out: set[int] = set()
    for ln in lines or []:
        m = _SRC_RE.search(ln)
        if m:
            out.add(int(m.group("src")))
    return out


def vouch_order(d: dict, vouched=None) -> str | None:
    """버림·예외 줄의 (원래 채널, 원래 글번호) 가 릴레이 보증(`trade.relay_origins`)에
    있나 — 순수 함수. 실수 #411.

    "before" = 그 글이 오기 **전에** 보증돼 있었다 — 봇이 받았어야 한다(보증 경로가
    깨졌다). "after" = 버린 **뒤에** 보증됐다 — 버릴 땐 보증이 없었다(보증하기 전의 판이
    포워드했거나 다른 릴레이가 같은 원래 글을 되포워드했다 — 여기선 못 가른다, #165).
    None = 보증에 없다 · 원래 글번호를 적지 않는 판의 줄 · 보증이나 줄의 시각을 모른다
    (순서를 가정하지 않는다, #165). 저널 시각은 초 단위로 잘려 찍힌다 — 같은 초 안의 보증은 '전' 으로 본다(보증은
    포워드보다 늘 먼저다)."""
    if not vouched or d.get("chat") is None or d.get("omsg") is None:
        return None
    v = vouched.get((d["chat"], d["omsg"]))
    at, ts = (v or {}).get("at"), d.get("ts")
    if v is None or at is None or ts is None:
        return None
    return "before" if at < ts + timedelta(seconds=1) else "after"


def vouch_recovered(d: dict, accepts=()) -> bool:
    """보증 뒤에 버린 재게시 글을 **그 뒤에** 보증으로 받았나 — 같은 원래 글(채널, 글번호)의
    수용 줄(`accepted … reason=relay_vouch`)이 버림보다 **늦게** 있으면 그 글은 들어왔다.
    순수 함수 — 판정(`verdict`)과 매시간 알림(`delivery_check`)이 같이 쓴다(#38).

    ⚠️ 왜 있나(독립 리뷰 #411 L5). 없으면 원인을 고치고 다시 포워드해 받았는데도 ❌ 가
    그 줄이 창에서 빠질 때까지 남아 `bot_health && 다시 포워드` 를 막았다. 시각을 모르거나
    같은 초면(저널은 초 단위다) 받았다고 단정하지 않는다(#165 — 막는 쪽이 안전하다)."""
    t0 = d.get("ts")
    if t0 is None or d.get("chat") is None or d.get("omsg") is None:
        return False
    return any(a.get("chat") == d["chat"] and a.get("omsg") == d["omsg"]
               and a.get("ts") is not None and a["ts"] > t0 for a in accepts or ())


def relay_origin(d: dict, relay_names=(), relay_ids=(), vouched=None) -> bool | None:
    """버림·예외 줄의 포워드 출처가 **릴레이 원천**인가. 순수 함수 — 버림과 예외가 같은
    규칙으로 가른다(#38).

    이름(대소문자 무시) **또는** 숫자 ID 로 알아본다 — 사용자명을 바꾼 원천은 ID 로만,
    ID 를 모르면 이름으로만 알아볼 수 있다. 원천의 이름·ID 가 아니어도 릴레이가 그 글을
    **받기 전에** 보증했으면(`vouch_order` == "before", 나쁜양파의 재게시 글) 릴레이 글이다
    (실수 #411). 출처 칸이 없는 줄(출처를 적지 않는 판)은 None — 모르는 것을 '아니다' 로
    접지 않는다(#165). 직접 쓴 글(type=none)은 포워드가 아니라 릴레이 원천일 수 없다(False)."""
    t = d.get("type")
    if t is None:
        return None
    if t == "none":
        return False
    names = {str(n).lower() for n in relay_names or ()}
    ids = {int(i) for i in relay_ids or ()}
    if (d.get("user") or "").lower() in names or d.get("chat") in ids:
        return True
    return vouch_order(d, vouched) == "before"


def forward_drops(drops_origin, relay_names=(), relay_ids=(), vouched=None) -> list[dict]:
    """출처 게이트 버림 중 **포워드**만(운영자가 직접 쓴 글 제외) — 릴레이 원천의
    글인지(`relay`)·왜 그렇게 봤는지(`why`: "name" 원천 이름·ID | "vouch" 릴레이 보증 |
    "")·보증과의 순서(`vouched`, `vouch_order`)를 달아 돌려준다. 순수 함수.

    버린 포워드는 봇이 **받은** 것이다 — 대조(`delivery_gap`)에선 받음으로 센다. 그중
    릴레이 원천의 글은 설정이 틀려 잃은 것이라 따로 ❌·알림이다. 원천 이름·ID 로 버린 것과
    **보증된 재게시 글**을 버린 것은 처방이 다르다(#82 — .env 대 보증 기록·데이터
    디렉터리). 보증이 없는 다른 출처의 글은 **여기서 못 가른다**(3차 독립 리뷰 M2) —
    BeOn 이 되포워드한 AWAKE 플러스면 게이트가 제 일을 한 것이지만, 보증하기 전의 판이
    포워드한 나쁜양파 **재게시** 글(다른 채널에서 퍼 온 글)이면 손실이다. 텔레그램은
    재포워드에도 원래 출처를 달아 둘이 같은 모양으로 온다 — 판정하지 않고 사실(출처·
    건수)만 메모로 말한다(#165)."""
    out = []
    for d in drops_origin or []:
        if d.get("type") == "none":
            continue
        by_name = bool(relay_origin(d, relay_names, relay_ids))
        order = vouch_order(d, vouched)
        out.append({**d, "relay": by_name or order == "before", "vouched": order,
                    "why": "name" if by_name else ("vouch" if order == "before" else "")})
    return out


def lost_posts(exceptions, ingested_ids, relay_names=(), relay_ids=(), vouched=None) -> list[dict]:
    """채널 글을 처리하다 예외로 끝났고 그 번호의 수신 줄이 **없는** 글 — 손실의 직접
    증거다. 순수 함수. 각 글에 `relay`(`relay_origin`: True · False · None=출처 모름)를 단다
    — 릴레이가 받기 전에 보증한 재게시 글도 릴레이 글이다(실수 #411).

    수 대조(보냄 vs 받음)는 번호로 짝을 짓지 않아 같은 창의 다른 글 수신·버림이 손실을
    **덮을 수 있다** — 이건 그 줄 자체가 증거라 대조와 무관하게 판정한다(2차 독립 리뷰
    H1). 같은 번호의 수신 줄이 있으면 기록 **뒤** 단계의 예외라 inbox 엔 있다 — 그래서
    `ingested_ids` 는 판정 창보다 넓게 읽은 수신까지 넘길 것(예외 직전의 기록 줄이 창
    경계 앞에 있을 수 있다). 채널 글이 아닌 업데이트(DM 명령 등)의 예외는 여기 안 든다 —
    채널 글 번호와 대조할 수 없다."""
    got = set(ingested_ids or ())
    return [{**e, "relay": relay_origin(e, relay_names, relay_ids, vouched)}
            for e in exceptions or []
            if e.get("kind") == "handler" and e.get("update") == "channel_post"
            and e.get("msg") is not None and e["msg"] not in got]


def event_start(f: dict, *, slack_s: int = RUN_SLACK_S,
                listen_slack_s: int = LISTEN_SLACK_S) -> datetime | None:
    """포워드 사건의 수신을 **어디부터** 셀지 — 사건이 스스로 아는 시작(`relay_forwards`
    의 `start`), 없으면 종류별 여유로 짐작한다(백필 끝 − RUN_SLACK_S · 리스너 − LISTEN_SLACK_S).
    시각을 못 읽은 사건은 None."""
    if f.get("start") is not None:
        return f["start"]
    if f.get("ts") is None:
        return None
    return f["ts"] - timedelta(seconds=slack_s if f.get("done") else listen_slack_s)


def delivery_gap(forwards, ingested_ts, now: datetime, *,
                 grace_s: int = GAP_GRACE_S, slack_s: int = RUN_SLACK_S,
                 listen_slack_s: int = LISTEN_SLACK_S,
                 not_before: datetime | None = None, dropped=()) -> dict:
    """릴레이가 보낸 만큼 봇이 받았나. 순수 함수.

    `kind`: none(기다릴 만큼 지난 포워드가 없다) · ok · partial(일부만) ·
    total(포워드 뒤로 **한 건도** 못 받았다) · unknown(시각을 못 읽음).
    받음 = 수신 줄(`got`) + 출처 게이트가 버린 포워드(`dropped`, `forward_drops` 의 결과 —
    버림은 게이트의 판정이지 수신 실패가 아니다). 릴레이 원천의 버림(잃은 글)은 판정이
    버림 줄에서 따로 센다. `due` 는 대조한 사건들이다(매시간 알림이 사건마다 신원을 단다).
    ⚠️ 포워드 한 건이 봇에게 업데이트 한 건이라는 전제다(앨범 멤버도 각각). 채널
    게이트 버림은 봇이 채널마다 한 번만 적어 여기서 못 센다 — 그 경우 판정의 ❌ 가
    따로 말한다. 시각을 못 읽은 사건은 세지 않고 **센 수를 말한다**(#54 — 모르는 것을
    '누락' 으로 세면 없는 결함을 만든다).
    ⚠️ 못 보는 축(#274): 수로 대조하고 번호로 짝을 짓지 않는다 — 받음에 상한이 없어
    같은 창의 **다른** 글(운영자가 직접 포워드한 글·손으로 돌린 백필·기다리는 중인 새
    포워드)의 수신·버림이 진짜 손실을 덮을 수 있다(2차 독립 리뷰 H1). 예외로 끝난 글은
    그 줄 자체가 증거라 `lost_posts` 가 이와 무관하게 잡는다.
    `not_before` 보다 앞의 수신은 세지 않는다 — 지금 프로세스가 뜬 뒤의 포워드는
    지금 프로세스만 받을 수 있으므로, 그 전 수신(옛 포워드의 몫)을 세면 누락을 가린다.
    재시작을 걸친 실행은 `split_at_restart` 가 이 값을 그 실행의 시작으로 당겨 넘긴다.
    """
    due = [f for f in forwards or []
           if f.get("ts") is not None and (now - f["ts"]).total_seconds() >= grace_s]
    undated = sum(1 for f in forwards or [] if f.get("ts") is None)
    if not due:
        return {"kind": "unknown" if undated else "none", "sent": 0, "got": 0,
                "dropped": 0, "undated": undated, "due": []}
    first = min(f["ts"] for f in due)
    start = min(event_start(f, slack_s=slack_s, listen_slack_s=listen_slack_s) for f in due)
    if not_before is not None and not_before > start:
        start = not_before
    sent = sum(f["n"] for f in due)
    got = sum(1 for t in ingested_ts or [] if t is not None and t >= start)
    drops = [d for d in dropped or () if d.get("ts") is not None and d["ts"] >= start]
    recv = got + len(drops)
    kind = "ok" if recv >= sent else ("total" if recv == 0 else "partial")
    return {"kind": kind, "sent": sent, "got": got, "dropped": len(drops),
            "first": first, "last": max(f["ts"] for f in due), "start": start,
            "guessed": any(f.get("guessed") for f in due), "undated": undated,
            "who": sorted({f["who"] for f in due}), "due": due}


def split_at_restart(forwards, run_ts) -> tuple[list[dict], list[dict], datetime | None,
                                                   list[dict]]:
    """포워드를 '지금 프로세스의 몫'(cur)과 '그 전'(old)으로 가르고, 지금 몫의 수신을
    **어디부터** 셀지(`nb`)를 정한다. 순수 함수 → (cur, old, nb, straddle).

    사건은 **끝**(로그 줄 시각)으로 가른다 — 끝이 재시작 뒤면 cur(경계 포함). 그런데
    **시작**이 재시작 전인 사건(재시작을 걸친 실행 — 배포가 봇을 재시작하는 동안 sync 가
    돌면 흔하다: trade-auto-update 는 `trade/`·`bot/` 커밋마다 봇을 재시작하고 sync 는
    10분까지 돈다)은 옛 프로세스도 그 실행의 글을 받았다. 그 몫을 버리면 멀쩡한 실행이
    누락으로 보이고 판정이 '텔레그램이 안 줬다' 로 끝난다(2차 독립 리뷰 M2). 그래서 수신은
    cur 의 가장 이른 시작부터 **두 프로세스를 같이** 센다(`nb`). 그렇게 당긴 창에 끝이 걸린
    옛 사건은 같은 수신을 두고 다투므로 cur 로 옮기고 다시 당긴다(더 안 바뀔 때까지).
    `straddle` = cur 중 수신을 재시작 **전부터** 센 사건(재시작을 걸친 실행과 그 창에 끝이
    걸려 옮겨 온 옛 사건 — 판정·⑤ 가 그렇게 셌다고 밝힌다).
    ⚠️ 경계를 넘어 당기는 것은 **잰** 시작(같은 PID 의 접속 줄)과 리스너 여유(몇십 초)
    뿐이다. 접속 줄이 창 밖이라 백필 끝에서 30분을 **짐작한** 시작은 경계를 넘지 않는다 —
    짐작으로 옛 사건을 끌어들이면 재시작 전에 잃은 글이 지금 프로세스의 누락으로 둔갑한다.
    재시작 시각을 모르면 가르지 않는다(전부 cur · nb None — 옛 동작). 시각을 못 읽은
    사건은 cur 에 둔다 — `delivery_gap` 이 판정 불가로 센다(#54).
    """
    fw = list(forwards or [])
    if run_ts is None:
        return fw, [], None, []

    def _pull(x):
        s = event_start(x)
        guessed = x.get("guessed") or (x.get("start") is None and x.get("done"))
        return max(s, run_ts) if guessed else s
    cur = [x for x in fw if x.get("ts") is None or x["ts"] >= run_ts]
    old = [x for x in fw if x.get("ts") is not None and x["ts"] < run_ts]
    nb = run_ts
    while True:
        nb = min([nb, *(_pull(x) for x in cur if x.get("ts") is not None)])
        moved = [x for x in old if x["ts"] >= nb]
        if not moved:
            break
        cur += moved
        old = [x for x in old if x["ts"] < nb]
    straddle = [x for x in cur if x.get("ts") is not None
                and (x["ts"] < run_ts or _pull(x) < run_ts)]
    return cur, old, nb, straddle


def relay_sources(scripts_dir: Path) -> dict[str, list[str]]:
    """포워드하는 스크립트의 원천 채널 — 모듈 수준 `SOURCE_USERNAME` 에서(AST)."""
    out: dict[str, list[str]] = {}
    for p in sorted(Path(scripts_dir).glob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "SOURCE_USERNAME"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                out.setdefault(node.value.value, []).append(p.stem)
    return out


def origin_accepts(origins, username: str, known_ids=()) -> bool | None:
    """실행 중인 봇의 출처 게이트가 이 원천의 포워드를 받나 — `_origin_matches` 규칙.

    숫자 ID 로만 적혀 있고 그 원천의 ID 를 모르면 **판정 불가**(None)다.
    """
    if origins == "any":
        return True
    if origins is None:
        return None
    if (username or "").lower() in origins:
        return True
    numeric = {int(s) for s in origins if str(s).lstrip("-").isdigit()}
    known = {int(i) for i in (known_ids or ())}
    if numeric & known:
        return True
    if numeric and not known:
        return None
    return False


def inbox_facts(path: Path, now: datetime) -> dict:
    """inbox 의 마지막 기록과 원천별 마지막 수신(읽기 전용)."""
    path = Path(path)
    f: dict = {"path": str(path), "exists": path.exists()}
    if not f["exists"]:
        return f
    st = path.stat()
    f.update(size=st.st_size, mtime=datetime.fromtimestamp(st.st_mtime, _KST))
    by: dict[str, dict] = {}
    last = None
    n = bad = 0
    cutoff = now - timedelta(hours=24)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            n += 1
            try:
                rec = json.loads(ln)
            except ValueError:
                bad += 1
                continue
            if not isinstance(rec, dict):
                bad += 1
                continue
            try:
                t = datetime.fromisoformat(str(rec.get("ingested_at")))
            except ValueError:
                t = None
            if t is not None and t.tzinfo is None:
                t = None                    # 시간대 없는 시각은 가정하지 않는다(#165)
            u = str(rec.get("forward_origin_chat_username") or "").lower()
            e = by.setdefault(u, {"last": None, "n24": 0, "ids": set()})
            cid = rec.get("forward_origin_chat_id")
            if isinstance(cid, int):
                e["ids"].add(cid)
            if t is not None:
                if e["last"] is None or t > e["last"]:
                    e["last"] = t
                if t >= cutoff:
                    e["n24"] += 1
                if last is None or t > last[0]:
                    last = (t, u)
    f.update(lines=n, bad=bad, by=by, last=last)
    return f


# 옛 판은 ❓(판정 불가)다 — ⚠️ 메모(rc 0)로 두면 배포 직후 `bot_health && 다시 포워드` 가
# 봇이 새 판으로 재시작되기 **전**에 다시 포워드를 흘려, 그 글이 증거 없이 또 사라진다
# (실수 #409). 이 진단이 가르려는 것이 바로 '받고 버렸나 / 아예 안 왔나' 다.
_OLD_BUILD_UNK = ("실행 중인 봇은 게이트 버림을 저널에 적지 않는 옛 판이다 — 위의 '버림 0건' 은 "
                  "버리지 않았다는 증거가 아니라 '받고 버렸나 / 아예 안 왔나' 를 가를 수 없다. "
                  "이 수정이 배포돼 봇이 재시작되면 사라진다 — 다시 포워드하는 것은 그 뒤에"
                  "(이 명령을 `&&` 로 앞에 두면 그때까지 막힌다)")
# 같은 이유로 ❓ 다(실수 #411·#409) — 재게시 보증을 모르는 판은 나쁜양파가 다른 채널에서 퍼 온
# 글을 다시 포워드해도 또 버린다. `bot_health && 다시 포워드` 가 봇 재시작 **전**에 흐르면 그
# 포워드가 또 사라진다(2026-09-25 에 27건이 두 번 그렇게 버려졌다).
_OLD_VOUCH_UNK = ("실행 중인 봇은 릴레이가 보증한 재게시 글을 받지 않는 옛 판이다(시작 줄에 "
                  "relay_vouch=on 이 없다) — 나쁜양파가 다른 채널에서 퍼 온 글은 다시 포워드해도 "
                  "출처 게이트가 또 버린다. 이 수정이 배포돼 봇이 재시작되면 사라진다 — 다시 "
                  "포워드하는 것은 그 뒤에(이 명령을 `&&` 로 앞에 두면 그때까지 막힌다)")


def verdict(f: dict) -> tuple[int, list[str]]:
    """모든 사실 → 판정 줄. 순수 함수. rc: 0 이상 없음 · 1 문제 · 2 판정 불가.

    ❌ 는 고칠 수 있는 것만, ❓ 는 못 잰 것(통과가 아니다, #54), ⚠️ 는 사실 메모.
    """
    bad: list[str] = []          # 원인(고칠 것)
    unk: list[str] = []
    notes: list[str] = []
    symptom: list[str] = []      # 증상(보낸 만큼 못 받음) — 원인이 없으면 따로 말한다
    now = f["now"]
    # 릴레이가 보증한 재게시 글(실수 #411) — 원천 이름·ID 가 아닌 버림·예외를 릴레이 글로
    # 알아보는 열쇠다. 못 읽었으면 알아보지 못한다는 사실을 메모로 말한다(아래).
    vf = f.get("vouch") or {}
    vouched = vf.get("pairs") or {}

    unit = f.get("unit") or {}
    kind = unit.get("kind")
    alive = kind == "running"
    if kind == "unknown":
        unk.append(f"systemd 에 못 물었다 — {unit.get('text')}")
    elif not alive:
        bad.append(f"봇 프로세스가 안 돈다 — {unit.get('text')}. 텔레그램은 봇이 못 "
                   "가져간 채널 글을 **24시간** 보관한다(웹훅이 없을 때) — 그 안에 "
                   "되살리면 밀린 글이 들어온다")

    gap = f.get("gap") or {}
    if gap.get("kind") in ("total", "partial"):
        dropped = gap.get("dropped") or 0
        recv = gap["got"] + dropped
        head = (f"릴레이가 {gap['sent']}건을 포워드했는데(첫 {_kst(gap['first'])} · "
                f"{', '.join(gap['who'])}) 봇이 받은 것은 {recv}건이다"
                + (f"(inbox 기록 {gap['got']} · 출처 게이트 버림 {dropped})" if dropped else ""))
        straddle = gap.get("straddle") or 0
        symptom.append(head + (" — **한 건도 못 받았다**" if gap["kind"] == "total"
                               else f" — {gap['sent'] - recv}건은 수신·버림 어느 줄에도 없다")
                       + (f"(재시작 전부터 센 포워드 {straddle}건 포함 — 그 시작부터 두 "
                          "프로세스의 수신을 같이 셌다)" if straddle else ""))
    elif gap.get("kind") == "unknown":
        unk.append("릴레이 포워드와 봇 수신을 대조 못 했다 — "
                   + (gap.get("err") or f"포워드 {gap.get('undated')}건의 시각을 못 읽었다"))
    gb = f.get("gap_before") or {}
    if gb.get("kind") in ("total", "partial"):
        started = _kst((f.get("running") or {}).get("ts"))
        # 수로 센 것이다 — 어느 글이 빠졌는지는 모르고, 재시작 뒤 수신도 같이 센다(그래서
        # 다시 포워드해 받으면 이 메모가 사라진다 · 재시작 뒤 수신이 많으면 모자람을 덜
        # 말한다, #274). 'inbox 에 없다' 를 단정하지 않는다(#165).
        gd = gb.get("dropped") or 0
        notes.append(f"지금 프로세스가 뜨기 전(시작 {started}) 릴레이가 {gb['sent']}건을 "
                     f"포워드했는데(첫 {_kst(gb['first'])}) 그 뒤 봇 수신 줄은 {gb['got']}건"
                     + (f"·출처 게이트 버림 줄은 {gd}건" if gd else "")
                     + "이다 — 모자란 만큼은 inbox 에 안 들어갔다고 봐야 한다(수로 센 것이라 "
                     "어느 글인지는 모른다). 지금 판정이 이상 없으면 그 기간을 다시 포워드할 "
                     "것 — 백필은 inbox 에 이미 있는 글을 건너뛴다. ⚠️ 손으로 돌린 백필은 "
                     "저널에 안 남아 위 '포워드' 수에 안 든다 — 이미 다시 포워드했는데도 이 "
                     "수가 그대로면 봇이 그 글을 못 받은 것이다(수로 센 것이라 단정은 못 한다). "
                     "같은 포워드를 되풀이하지 말고 그 글이 어느 갈래인지부터 볼 것: "
                     f"`{FIND_CMD}`")

    tg = f.get("tg") or {}
    dest = (f.get("env") or {}).get("dest")
    if not tg.get("token"):
        unk.append("토큰(TRADE_BOT_TOKEN)을 못 읽었다 — 웹훅·수신 종류·관리자 여부는 판정 불가")
    else:
        me = tg.get("me") or {}
        if not me.get("ok") and me.get("code") == 401:
            bad.append("텔레그램이 토큰을 거절했다(401) — 토큰이 폐기·교체됐다. 봇도 이 "
                       "토큰이면 폴링부터 실패한다 — BotFather 에서 확인 뒤 .env 갱신")
        elif not me.get("ok"):
            unk.append(f"텔레그램에 못 물었다({me.get('err')}) — 웹훅·관리자 판정 불가")
        wh = tg.get("webhook") or {}
        if wh.get("ok"):
            r = wh.get("result") or {}
            if r.get("url"):
                bad.append(f"웹훅이 걸려 있다(호스트 {_host(r['url'])}) — 그러면 폴링이 409 로 "
                           "막혀 채널 글을 한 건도 못 받는다. 봇은 시작할 때 웹훅을 지운다"
                           "(python-telegram-bot 기본) — 재시작 뒤에도 다시 걸려 있으면 같은 "
                           "토큰으로 웹훅을 거는 다른 무언가가 있다")
            au = r.get("allowed_updates")
            if isinstance(au, list) and au and "channel_post" not in au:
                bad.append(f"텔레그램에 저장된 수신 종류에 channel_post 가 없다({', '.join(au)}) "
                           "— 채널 글 업데이트가 **만들어지지도 않는다**. 수신 종류를 매 "
                           "시작에 명시하는 판(실수 #406)으로 봇이 재시작하면 풀린다 — 그 전에 "
                           "포워드한 글은 다시 포워드해야 한다")
        elif wh and me.get("ok"):
            unk.append(f"웹훅 정보를 못 물었다({wh.get('err')})")
        mem = tg.get("member")
        if dest is None:
            unk.append("릴레이 목적지(.env TRADE_CHANNEL_CHAT_IDS)를 몰라 관리자 여부 판정 불가")
        elif mem is None:
            pass                                   # getMe 실패 — 위에서 이미 말했다
        elif mem.get("ok"):
            status = (mem.get("result") or {}).get("status")
            if status not in _ADMIN:
                bad.append(f"봇이 대상 채널 {dest} 의 관리자가 아니다(status={status}) — 채널 "
                           "글은 관리자인 봇에게만 전달된다. 채널 관리자에 봇을 다시 추가한 "
                           "뒤 다시 포워드")
        elif mem.get("code") in (400, 403):
            bad.append(f"대상 채널 {dest} 에서 봇을 못 찾았다({mem.get('err')}) — 봇이 그 "
                       "채널에 없거나 쫓겨났다. 관리자로 다시 추가")
        else:
            unk.append(f"관리자 여부를 못 물었다({mem.get('err')})")

    known = {u: e["ids"] for u, e in ((f.get("inbox") or {}).get("by") or {}).items()}
    # getMe 가 실패했으면 원천 조회도 같은 이유로 실패했다 — 같은 원인을 줄마다 되풀이하지 않는다
    sources = (tg.get("sources") or {}) if (tg.get("me") or {}).get("ok") else {}
    # 릴레이 원천의 숫자 ID — inbox 에서 배운 것 + 지금 getChat 이 준 것(출처 버림 판정용)
    relay_ids: set = set()
    for u in (f.get("relays") or {}):
        relay_ids |= set(known.get(u.lower()) or ())
    for u, res in sorted(sources.items()):
        if res.get("ok") and isinstance((res.get("result") or {}).get("id"), int):
            relay_ids.add(res["result"]["id"])
        if res.get("ok"):
            r = res.get("result") or {}
            now_name = str(r.get("username") or "")
            ids = known.get(u.lower()) or set()
            if now_name.lower() != u.lower():
                bad.append(f"원천 @{u} 의 현재 사용자명이 @{now_name or '없음'} 이다 — 봇의 "
                           "출처 게이트는 사용자명으로 대조하므로 이 원천 포워드를 전부 버린다. "
                           f".env TRADE_SOURCE_ORIGIN 에 숫자 ID {r.get('id')} 를 더하고 봇 재시작")
            elif ids and r.get("id") not in ids:
                notes.append(f"원천 @{u} 의 ID({r.get('id')})가 inbox 에서 본 ID {sorted(ids)} 와 "
                             "다르다 — 같은 사용자명을 다른 채널이 쓰고 있을 수 있다")
        elif res.get("code") == 400:
            unk.append(f"원천 @{u} 를 텔레그램이 못 찾았다({res.get('err')}) — 사용자명이 "
                       "바뀌었거나 비공개로 바뀌었으면 봇의 출처 게이트(사용자명 대조)가 이 "
                       "원천 포워드를 전부 버린다. 채널의 현재 사용자명을 확인할 것")
        else:
            unk.append(f"원천 @{u} 를 못 물었다({res.get('err')})")

    relay_ids |= set(f.get("relay_ids") or ())      # 릴레이 저널이 스스로 밝힌 원천 ID
    relay_names = {u.lower() for u in (f.get("relays") or {})}
    run = f.get("running")
    unlabeled: list[dict] = []                      # 어느 업데이트였는지 안 적힌 PTB 예외(대조 창 안)
    j = f.get("journal")
    if j is None:
        unk.append(f"trade-bot 저널을 못 읽었다 — {f.get('journal_err') or '사유 미상'}")
    else:
        # 판정은 **지금 프로세스(MainPID)의 줄**로 한다 — 재시작 전 프로세스의 409·버림·
        # 예외는 옛 판·옛 설정의 일이라 지금 상태의 증거가 아니다(독립 리뷰 H1). 그 전
        # 것은 사실 메모로 말한다. PID 를 모르면(유닛이 안 돌거나 systemd 에 못 물음) 창
        # 전체로 본다(옛 동작) — 그때 '지금 프로세스' 라고 말하지 않는다.
        # `j`·`jc` 는 사용자가 준 --since 창의 줄이다. 수신을 세려고 넓혀 읽은 저널
        # (`journal_wide`)은 수신 대조(기록된 번호)에만 쓴다 — 넓힌 30분의 409·버림·예외를
        # 판정에 섞으면 사용자가 창으로 뺀 사건이 '지금' 의 ❌ 가 된다(2차 독립 리뷰 L5).
        jc = f.get("journal_cur")
        cur = jc if jc is not None else j
        prior = "재시작 전 프로세스가"
        n409 = cur["polls"].get("409", 0)
        old409 = j["polls"].get("409", 0) - n409 if jc is not None else 0
        if n409:
            others = f.get("others") or []
            where = (f"이 서버에 다른 trade.bot 프로세스가 있다: "
                     f"{', '.join(str(o['pid']) for o in others)}" if others else
                     "이 서버엔 다른 trade.bot 프로세스가 없다 — 다른 서버·PC 에서 같은 "
                     "토큰으로 봇을 돌리는지 볼 것")
            bad.append(f"폴링이 409 Conflict 로 {n409}회 거절됐다 — 같은 토큰으로 다른 "
                       f"곳이 폴링(또는 웹훅)해 채널 글을 그쪽이 가져간다. {where}")
        elif old409:
            notes.append(f"{prior} 폴링을 409 Conflict 로 {old409}회 거절당했다 — 지금 "
                         "프로세스의 줄엔 없다(그 사이 같은 토큰의 다른 폴러가 꺼졌거나 아직 "
                         "안 부딪혔다). 그 동안 온 글은 그쪽이 가져갔을 수 있다")
        if alive:
            started = (run or {}).get("ts")
            ref = cur["last_ok"]
            if ref is None:
                # 막 뜬 프로세스는 아직 첫 폴링 전일 수 있다 — 멈춤과 같은 창으로 봐 준다.
                if not (started is not None
                        and (now - started).total_seconds() <= POLL_STALE_S):
                    bad.append(f"{'지금 프로세스' if jc is not None else '창 안'}에 정상 폴링"
                               "(getUpdates 200)이 한 번도 없다 — 봇이 텔레그램에서 아무것도 "
                               "못 받고 있다" + (f"(프로세스 시작 {_kst(started)})" if started else ""))
            elif (now - ref).total_seconds() > POLL_STALE_S:
                bad.append(f"폴링이 {_age((now - ref).total_seconds())} 전에 멈췄다"
                           f"(마지막 정상 {_kst(ref)}) — watchdog 가 5분 안에 재시작"
                           "해야 정상이다")
        # 버림이 다 결함은 아니다(#82) — 다른 채널의 글·운영자가 직접 쓴 글은 정상이다.
        mine = [d for d in cur["drops_channel"] if dest is not None and d["chat"] == dest]
        old_mine = ([d for d in j["drops_channel"] if dest is not None and d["chat"] == dest]
                    if jc is not None and not mine else [])
        if mine:
            bad.append("봇이 릴레이 목적지의 글을 **채널 게이트**에서 버렸다 — "
                       + mine[-1]["line"][-240:])
        elif old_mine:
            notes.append(f"{prior} 릴레이 목적지의 글을 채널 게이트에서 버렸다(지금 프로세스의 "
                         "줄엔 없다 — 채널마다 첫 버림만 적는다). 그 뒤 설정을 고쳤어도 그 전 "
                         "포워드는 다시 포워드해야 들어온다 — " + old_mine[-1]["line"][-200:])
        others_ch = sorted({d["chat"] for d in j["drops_channel"]} - {dest})
        if others_ch:
            notes.append(f"허용 목록 밖 채널 {others_ch} 의 글을 버렸다 — 봇이 관리자인 다른 "
                         "채널이면 정상이다")
        relay_drops = [d for d in forward_drops(cur["drops_origin"], relay_names, relay_ids,
                                                vouched) if d["relay"]]
        by_name = [d for d in relay_drops if d["why"] == "name"]
        accepts = j.get("vouch_accepts") or ()
        by_vouch = [d for d in relay_drops if d["why"] == "vouch"]
        healed = [d for d in by_vouch if vouch_recovered(d, accepts)]
        by_vouch = [d for d in by_vouch if not vouch_recovered(d, accepts)]
        if by_name:
            bad.append(f"봇이 릴레이 포워드 {len(by_name)}건을 **출처 게이트**에서 버렸다 — "
                       "예: " + by_name[-1]["line"][-240:])
        if by_vouch:
            # 처방이 원천 이름 버림과 다르다(#82) — .env 가 아니라 보증 기록·데이터 디렉터리다.
            bad.append(f"봇이 릴레이가 **보증한** 재게시 글 {len(by_vouch)}건을 출처 게이트에서 "
                       "버렸다(보증이 글보다 먼저였다 — 실수 #411). 버림 줄의 vouch= 칸이 봇 쪽 "
                       "사유다: unreadable = 봇이 보증 기록을 못 읽었다(권한·형식) · miss = 봇이 "
                       "본 기록엔 그 글이 없었다(봇과 릴레이의 데이터 디렉터리가 갈렸나 — ③ 의 "
                       "inbox 경로와 아래 보증 기록 경로). 고친 뒤 그 기간을 다시 포워드할 것. "
                       "예: " + by_vouch[-1]["line"][-240:])
        if healed:
            # 받았으면 손실이 아니다 — 그래도 한 번 깨졌던 사실은 말한다(#43).
            notes.append(f"보증한 재게시 글 {len(healed)}건을 보증 뒤에 버렸다가 그 뒤 보증으로 "
                         "받았다(같은 원래 글의 수용 줄이 버림보다 늦다) — 버릴 때의 봇 쪽 사유는 "
                         "vouch= 칸이다. 예: " + healed[-1]["line"][-200:])
        if not relay_drops and jc is not None:
            old_relay = [d for d in forward_drops(j["drops_origin"], relay_names, relay_ids,
                                                  vouched) if d["relay"]]
            if old_relay:
                notes.append(f"{prior} 릴레이 포워드 {len(old_relay)}건을 출처 게이트에서 "
                             "버렸다 — 그 글은 그때 inbox 에 안 들어갔다(그 뒤 다시 포워드해 "
                             "받지 않았다면 지금도 없다). 예: " + old_relay[-1]["line"][-200:])
        fd_all = forward_drops(j["drops_origin"], relay_names, relay_ids, vouched)
        n_direct = sum(1 for d in j["drops_origin"] if d["type"] == "none")
        other_src = [d for d in fd_all if not d["relay"]]
        # 버린 **뒤에** 릴레이가 보증한 재게시 글 — 버릴 땐 보증이 없었다(보증하기 전의 판이
        # 포워드했거나 다른 릴레이가 되포워드했다 — 여기선 못 가른다, #165). 받았는지는 봇의
        # 보증 수용 줄(같은 원래 글)이 말한다(실수 #411 · #86).
        later = [d for d in other_src if d["vouched"] == "after"]
        took = {(a["chat"], a["omsg"]) for a in j.get("vouch_accepts") or ()}
        back = [d for d in later if (d["chat"], d["omsg"]) in took]
        blind = [d for d in other_src if d["vouched"] != "after"]
        if n_direct or other_src:
            names = sorted({f"@{d['user']}" if d["user"] else
                            (f"{d['chat']}({d['title']!r})" if d.get("title") else str(d["chat"]))
                            for d in blind})
            notes.append(f"출처 게이트가 릴레이 원천이 아닌 글을 버렸다(직접 쓴 글 {n_direct} · "
                         f"다른 출처 포워드 {len(other_src)}"
                         + (f": {', '.join(names[:3])}{' 외' if len(names) > 3 else ''}"
                            if names else "")
                         + ")"
                         + (f" — 그중 {len(later)}건은 버린 **뒤에** 릴레이가 보증한 재게시 글이다"
                            "(버릴 땐 보증이 없었다 — 보증하기 전의 판이나 다른 릴레이가 "
                            "포워드했다) — 그 뒤 보증으로 받은 줄이 "
                            f"{len(back)}건"
                            + (f"(나머지 {len(later) - len(back)}건은 아직 받은 줄이 없다 — "
                               f"`{FIND_CMD}` 로 볼 것)" if len(back) < len(later) else "")
                            if later else "")
                         # 직접 쓴 글은 데이터가 아니다 — 거기까지만 '제 일' 이라 말한다. 보증이
                         # 없는 다른 출처 포워드는 여기서 못 가른다(3차 독립 리뷰 M2): 단정하지
                         # 않고 가르는 방법을 건넨다(#165 — 옛 판은 둘 다 '게이트가 제 일을 한
                         # 것' 이라 했다).
                         + (" — 보증 없는 다른 출처 포워드는 여기서 못 가른다: BeOn 이 되포워드한 "
                            "남의 글(AWAKE 플러스 등)이면 게이트가 제 일을 한 것이지만, 나쁜양파가 "
                            "**재게시**한 글(다른 채널에서 퍼 온 글 — 텔레그램은 재포워드에도 "
                            "원래 출처를 단다)이면 릴레이가 포워드 **전에** 보증해야 봇이 받는다"
                            "(실수 #411 — 이 판의 백필·리스너는 보증한다. 보증하기 전의 판이 "
                            "포워드한 글이면 다시 포워드하면 받는다). 그 글이 "
                            f"`{FIND_CMD}` 에서 to-forward 로 남으면 아직 inbox 에 없다 — 그 "
                            "기간을 다시 포워드할 것"
                            if blind else
                            "" if other_src else
                            " — 채널에 직접 쓴 글·명령이라 게이트가 제 일을 한 것이다"))
        if vf.get("err"):
            notes.append(f"재게시 보증 기록을 못 읽었다({vf['err']}) — 재게시 글의 버림을 릴레이 "
                         "것으로 못 알아본다. 봇도 이 파일을 못 읽으면 그 글을 버린다(버림 줄 "
                         f"vouch=unreadable) — 다음 보증이 새로 쓴다: {vf.get('path')}")
        # 받은 채널 글을 처리하다 예외로 끝나 수신 줄이 **없는** 번호는 손실의 직접 증거다
        # — 수 대조(gap)와 무관하게 판정한다. 수는 번호로 짝을 짓지 않아 같은 창의 다른 글
        # 수신·버림이 그 손실을 덮는다(2차 독립 리뷰 H1 — 옛 판은 누락이 보일 때만 ❌ 로
        # 읽고 나머지는 '빠진 것이 없다' 고 거짓 메모를 달았다). 기록 여부는 넓혀 읽은
        # 저널의 수신으로 대조한다(예외 직전의 기록 줄이 --since 경계 앞일 수 있다).
        jw = f.get("journal_wide") or j
        got_ids = set(jw.get("ingested_ids") or ())
        lost = lost_posts(cur["exceptions"], got_ids, relay_names, relay_ids, vouched)
        mine_lost = [e for e in lost if e["relay"]]
        unk_lost = [e for e in lost if e["relay"] is None]
        other_lost = [e for e in lost if e["relay"] is False]
        if mine_lost:
            # ⚠️ 다시 포워드한 글은 비공개 채널에서 **새 번호**를 받아 이 예외 줄과 짝이 안
            # 맞는다 — 고치고 다시 포워드해 받았어도 이 줄이 창에 남는 동안 ❌ 가 남는다(3차
            # 독립 리뷰 M1). 번호로 복구를 알아볼 수 없으니 그 사실과 창을 좁히는 법을 적는다.
            bad.append(f"봇이 릴레이 원천의 채널 글 {len(mine_lost)}건을 처리하다 예외로 놓쳤다"
                       f"(번호 {_msgs(mine_lost)}) — 수신 줄 없이 끝나 inbox 에 없다. 원인을 "
                       "고친 뒤 그 기간을 다시 포워드할 것(다시 포워드한 글은 새 번호로 들어와 "
                       "이 줄과 짝이 안 맞는다 — 이미 다시 포워드해 받았다면 이 ❌ 는 이 줄이 "
                       "창에 남는 동안 계속 뜬다: 다시 포워드한 **뒤** 시각으로 --since 를 좁혀 "
                       "다시 볼 것). 예: " + mine_lost[-1]["line"][-240:])
        if unk_lost:
            unk.append(f"봇이 채널 글 {len(unk_lost)}건을 처리하다 예외로 놓쳤는데(번호 "
                       f"{_msgs(unk_lost)} — inbox 에 없다) 그 줄은 포워드 출처를 적지 않는 판이 "
                       "찍어 릴레이 원천의 글인지 모른다 — 릴레이 글이었으면 그 기간을 다시 "
                       "포워드할 것. 예: " + unk_lost[-1]["line"][-200:])
        if other_lost:
            notes.append(f"릴레이 원천이 아닌 채널 글 {len(other_lost)}건(직접 쓴 글·다른 출처 "
                         f"포워드)을 처리하다 예외로 놓쳤다(번호 {_msgs(other_lost)}) — 릴레이 "
                         "데이터는 아니다. 예외 자체는 위 오류 표본으로 볼 것. 예: "
                         + other_lost[-1]["line"][-200:])
        after = [e for e in cur["exceptions"] if e["kind"] == "handler"
                 and e.get("update") == "channel_post" and e.get("msg") in got_ids]
        if after:
            notes.append(f"채널 글을 처리하다 예외가 {len(after)}번 났는데 그 번호는 전부 수신 "
                         "줄이 있다 — 기록 **뒤** 단계의 예외라 그 글은 inbox 에 있다. 예: "
                         + after[-1]["line"][-200:])
        if jc is not None:
            # 재시작 전 프로세스가 예외로 놓친 글 — 지금 상태의 증거는 아니지만 그 글은 그때
            # inbox 에 안 들어갔다(사실 메모, 모듈 독스트링의 약속).
            now_lines = {e["line"] for e in cur["exceptions"]}
            old_lost = [e for e in lost_posts([e for e in j["exceptions"]
                                               if e["line"] not in now_lines],
                                              got_ids, relay_names, relay_ids, vouched)
                        if e["relay"] is not False]
            if old_lost:
                n_unk = sum(1 for e in old_lost if e["relay"] is None)
                notes.append(f"{prior} 채널 글 {len(old_lost)}건을 처리하다 예외로 놓쳤다(번호 "
                             f"{_msgs(old_lost)}"
                             + (f" · 그중 {n_unk}건은 출처를 적지 않는 판이 찍어 릴레이 글인지 "
                                "모른다" if n_unk else "")
                             + ") — 그 글은 그때 inbox 에 안 들어갔다(그 뒤 다시 포워드해 받지 "
                             "않았다면 지금도 없다). 예: " + old_lost[-1]["line"][-200:])
        # 어느 업데이트였는지 안 적힌 옛 판 예외(PTB 기본 문구)는 대조 창 안의 것만 — 원인을
        # 못 짚은 갈래가 '표본부터' 로 말한다(창 밖 예외는 이 누락과 무관하다).
        gstart = gap.get("start")
        unlabeled = [e for e in cur["exceptions"] if e["kind"] == "unlabeled"
                     and gstart is not None and e.get("ts") is not None and e["ts"] >= gstart]
        n_starts = len(j["starts"])
        restarts = (f.get("facts") or {}).get("s_NRestarts")
        if n_starts >= 3:
            notes.append(f"창 안에서 봇이 {n_starts}번 시작했다"
                         + (f"(systemd 자동 재시작 누적 {restarts}회)" if restarts not in
                            (None, "") else "")
                         + " — 배포·수동 재시작이 아니면 봇이 반복해 죽는다: 위 오류 표본을 볼 것")

    if run is None:
        unk.append(f"실행 중인 봇의 시작 줄을 못 찾았다({f.get('running_err') or '사유 미상'}) "
                   "— 허용 채널·출처·inbox 경로를 릴레이와 대조 못 했다")
    else:
        allowed = run["allowed"]
        if allowed is None:
            unk.append("실행 중인 봇의 허용 채널 목록을 못 읽었다")
        elif allowed != "any" and dest is not None and dest not in allowed:
            bad.append(f"실행 중인 봇이 허용하는 채널 {sorted(allowed)} 에 릴레이 목적지 {dest} "
                       "가 없다 — 그 채널로 포워드한 글은 전부 채널 게이트에서 버려진다"
                       "(.env 를 바꾼 뒤 봇을 재시작 안 했거나 그 반대)")
        for u, scripts in sorted((f.get("relays") or {}).items()):
            acc = origin_accepts(run["origin"], u, known.get(u.lower()))
            if acc is False:
                bad.append(f"실행 중인 봇의 출처 목록 {sorted(run['origin'])} 이 {u}"
                           f"({', '.join(scripts)}) 포워드를 버린다 — .env TRADE_SOURCE_ORIGIN "
                           f"에 {u} 를 넣고 봇 재시작")
            elif acc is None:
                unk.append(f"출처 게이트가 {u} 를 받는지 판정 불가(목록을 못 읽었거나 숫자 "
                           "ID 로만 적혀 있고 그 ID 를 inbox 에서 못 배웠다)")
        expected = f.get("inbox_expected")
        if (f.get("env") or {}).get("as_root"):
            unk.append("root 로 돌려 HOME 기준 inbox 경로가 봇과 다를 수 있다 — sudo 없이 "
                       "봇과 같은 사용자로 다시 돌릴 것")
        elif expected and run["inbox"] != expected:
            bad.append(f"봇은 {run['inbox']} 에 쓰는데 백필·이 진단은 {expected} 를 읽는다 "
                       "— TRADE_DATA_DIR 이 갈렸다")
        if not run["drop_log"]:
            unk.append(_OLD_BUILD_UNK)
        elif not run.get("vouch"):
            unk.append(_OLD_VOUCH_UNK)

    ib = f.get("inbox") or {}
    if ib and not ib.get("exists"):
        bad.append(f"inbox 가 없다({ib.get('path')}) — 이 경로로는 한 번도 안 들어왔다")

    wh_r = ((tg.get("webhook") or {}).get("result") or {}) if tg.get("token") else {}
    pend = wh_r.get("pending_update_count")
    if pend:
        notes.append(f"텔레그램에 봇이 아직 안 가져간 업데이트 {pend}건 — 폴링 중이면 곧 "
                     "빠진다(계속 남으면 봇이 못 가져가는 것)")

    # 옛 판 ❓ 는 아래 증상 갈래가 더 구체적인 한 줄로 대신한다 — '다른 못 잰 조건' 에 넣지 않는다.
    other_unk = [u for u in unk if u != _OLD_BUILD_UNK]
    if symptom and not bad and other_unk:
        # 못 잰 조건이 남아 있으면 '다 맞는다' 고 말할 수 없다(#165) — 그것부터.
        unk.append("원인을 짚지 못했다 — 위 ❓ 의 못 잰 조건이 남아 있으니 그것부터 잴 것")
    elif symptom and not bad:
        # 증상은 있는데 위에서 원인을 하나도 못 짚었다 — 남은 갈래를 **남은 사실대로**
        # 말한다(#165). 전부/일부, 버림이 있었나, 어느 업데이트였는지 안 적힌 예외가 있나에
        # 따라 할 말이 다르다(독립 리뷰 H2 — 버림이 있는데 '버림 기록도 없는데' 라고 쓰면
        # 거짓이다).
        missing = gap["sent"] - gap["got"] - (gap.get("dropped") or 0)
        exc = (f" 그 창에 어느 업데이트였는지 안 적힌 PTB 예외가 {len(unlabeled)}번 찍혔다 — "
               "위 오류 표본부터 볼 것(처리하다 죽은 글일 수 있다)." if unlabeled else "")
        if run is not None and not run["drop_log"]:
            unk.append(f"위 조건은 전부 맞는데 {missing}건을 못 받았다 — 실행 중인 봇이 버림을 "
                       "적지 않는 옛 판이라 '받고 버렸나 / 아예 안 왔나' 를 저널이 말하지 않는다."
                       + exc + " 이 수정을 배포한 뒤(봇 재시작) 다시 포워드하면 버림 줄이나 "
                       "수신 줄이 답한다")
            unk.remove(_OLD_BUILD_UNK)        # 같은 사실을 두 줄로 말하지 않는다(#395)
        elif unlabeled:
            unk.append(f"위 조건은 전부 맞는데 {missing}건이 수신·버림 어느 줄에도 없다 —"
                       + exc)
        elif gap["kind"] == "total":
            # total 은 받음(기록+버림)이 0 이다 — 여기서 '버림 기록도 없다' 는 사실이다.
            unk.append("위 조건은 전부 맞고 버림·예외 기록도 없는데 받은 기록이 없다 — 텔레그램이 "
                       "전달하지 않았다는 뜻이다(드묾). 봇 재시작 뒤 다시 포워드해 볼 것")
        else:
            dropped = gap.get("dropped") or 0
            unk.append(f"위 조건은 전부 맞는데 {missing}건이 수신·버림·예외 어느 줄에도 없다"
                       + (f"(받은 {gap['got'] + dropped}건 중 {dropped}건은 출처 게이트가 버린 "
                          "다른 출처 포워드다)" if dropped else "")
                       + (" — 30분 넘게 돈 백필이 창 앞에서 시작했으면 앞부분 수신을 못 세어 "
                          "이렇게 보인다(그 실행의 시작 줄이 창 밖이라 시작을 짐작했다) — "
                          "--since 를 넓혀 다시 돌려 볼 것" if gap.get("guessed") else
                          " — 텔레그램이 일부를 전달하지 않았다는 뜻이다(드묾). 빠진 기간을 "
                          "다시 포워드해 볼 것"))
    bad = symptom + bad
    rc = 1 if bad else (2 if unk else 0)
    out = [f"❌ {t}" for t in bad] + [f"❓ {t}" for t in unk] + [f"⚠️ {t}" for t in notes]
    if rc == 0:
        out.insert(0, "✅ 봇은 폴링 중이고, 텔레그램 쪽 조건(웹훅 없음 · 수신 종류 · 대상 채널 "
                      "관리자)과 실행 중 설정(채널·출처·inbox 경로)이 릴레이와 맞는다")
    return rc, out


def _host(url: str) -> str:
    """웹훅 URL 은 **호스트만** — 경로에 비밀값을 넣는 관행이 있다(§Secrets)."""
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname or "?"
    except Exception:                                          # noqa: BLE001
        return "?"


# ── 입출력(읽기 전용) ───────────────────────────────────────────────────

def _urlopen_fetch(url: str, timeout: float) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:        # 4xx·5xx 도 본문에 사유가 있다
        return e.code, e.read()


def tg_call(token: str, method: str, params: dict | None = None, *,
            fetch=None, timeout: float = 10.0) -> dict:
    """Bot API 한 번 — `{ok, code, result|err}`. 오류 문구에서 토큰을 지운다.

    ⚠️ `READ_ONLY_METHODS` 밖은 거부한다 — 특히 getUpdates(봇과 409 로 부딪히고
    업데이트를 빼앗는다)."""
    if method not in READ_ONLY_METHODS:
        raise ValueError(f"읽기 전용 계약 밖의 메서드: {method}")
    from urllib.parse import urlencode

    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urlencode(params)
    try:
        status, body = (fetch or _urlopen_fetch)(url, timeout)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "code": None,
                "err": scrub(f"{type(exc).__name__}: {exc}", token)[:200]}
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:                                          # noqa: BLE001
        return {"ok": False, "code": status, "err": f"HTTP {status} · JSON 아님"}
    if isinstance(data, dict) and data.get("ok"):
        return {"ok": True, "code": status, "result": data.get("result")}
    desc = data.get("description") if isinstance(data, dict) else None
    code = (data.get("error_code") if isinstance(data, dict) else None) or status
    return {"ok": False, "code": code, "err": scrub(desc or f"HTTP {status}", token)[:200]}


def telegram_facts(token: str, dest: int | None, sources=(), *, call=tg_call) -> dict:
    """getMe → getWebhookInfo → getChatMember(대상 채널, 봇) → getChat(@원천). 읽기 전용.

    원천 채널을 묻는 이유: 봇의 출처 게이트는 **사용자명**으로 대조한다. 원천이
    사용자명을 바꾸면 릴레이는 세션 캐시(숫자 ID)로 계속 포워드하는데 봇은 그
    글을 전부 버린다 — 양쪽 로그가 다 멀쩡해 보이는 조용한 유실이다(#406)."""
    f: dict = {"token": bool(token)}
    if not token:
        return f
    f["me"] = call(token, "getMe")
    bot_id = ((f["me"].get("result") or {}).get("id") if f["me"].get("ok") else None)
    f["username"] = (f["me"].get("result") or {}).get("username") if f["me"].get("ok") else None
    f["webhook"] = call(token, "getWebhookInfo")
    if dest is not None and bot_id is not None:
        f["member"] = call(token, "getChatMember", {"chat_id": dest, "user_id": bot_id})
    f["sources"] = {u: call(token, "getChat", {"chat_id": f"@{u}"}) for u in sources}
    return f


def trade_env() -> dict:
    """봇과 **같은 파일**(이 체크아웃의 `.env`)에서 필요한 키만 읽는다.

    ⚠️ 값은 판정에만 쓰고 절대 찍지 않는다 — 출처(`.env`/환경변수/없음)만 찍는다
    (§Secrets · #23 진단은 .env 를 안 읽는다). 봇은 systemd 로 떠서 셸 환경이
    없으므로 `.env` 가 먼저다.
    """
    keys = ("TRADE_BOT_TOKEN", "TRADE_CHANNEL_CHAT_IDS", "TRADE_DATA_DIR")
    vals: dict = {}
    err = ""
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(_REPO / ".env") or {}
    except Exception as exc:                                   # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"[:120]
    got, src = {}, {}
    for k in keys:
        v = (vals.get(k) or "").strip()
        if v:
            got[k], src[k] = v, ".env"
        elif (os.environ.get(k) or "").strip():
            got[k], src[k] = os.environ[k].strip(), "환경변수"
        else:
            got[k], src[k] = "", "없음"
    ids = [x.strip() for x in got["TRADE_CHANNEL_CHAT_IDS"].split(",") if x.strip()]
    try:
        dest = int(ids[0]) if ids else None
    except ValueError:
        dest = None
    data_dir = Path(got["TRADE_DATA_DIR"] or str(Path.home() / ".trade"))
    return {"token": got["TRADE_BOT_TOKEN"], "dest": dest, "src": src, "err": err,
            "inbox": str(data_dir / "inbox.jsonl"), "data_dir": str(data_dir),
            "as_root": hasattr(os, "geteuid") and os.geteuid() == 0}


def read_journal(units, since: str, *, run=subprocess.run) -> tuple[list[str], str, str]:
    """유닛 저널을 창으로 읽는다 → (줄, 사유, 갈래). 갈래는 구조로 준다(#19).

    갈래: ""(읽음) · "rotated"(저널은 읽히는데 이 유닛 줄이 창 안에 0건 — 조용한
    것이지 못 읽은 게 아니다) · "denied"(권한) · "unknown"(판정 불가) · "error"
    (journalctl 실패). 뒤의 셋은 판정 불가로 다룬다(#54·#82).
    """
    cmd = ["journalctl", "--no-pager", "-o", "short-iso", "--since", since]
    for u in units:
        cmd += ["-u", u]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:                                   # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:160], "error"
    if r.returncode != 0:
        return [], scrub((r.stderr or "").strip()[:160] or f"rc={r.returncode}"), "error"
    lines = [ln for ln in (r.stdout or "").splitlines()
             if ln.strip() and not ln.strip().startswith("-- ")]
    if not lines:
        from bot.daily_kr_flow import (journal_empty_kind, journal_empty_reason,
                                       journal_readable)

        readable = journal_readable()
        return [], journal_empty_reason(readable), journal_empty_kind(readable)
    return lines, "", ""


def _readable(kind: str) -> bool:
    """줄이 0건이어도 '못 읽은 것' 은 아닌 갈래인가."""
    return kind in ("", "rotated")


def find_start_line(pid: int, *, popen=subprocess.Popen, max_lines: int = 500):
    """지금 도는 프로세스의 시작 줄 — 창 밖에서 떴어도 찾는다(`_PID=` 로 한정).

    그 프로세스 저널의 **앞쪽** 몇 줄만 읽고 멈춘다(`-n` 은 꼬리라 못 쓴다).
    """
    cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-b",
           f"_SYSTEMD_UNIT={_SERVICE}", f"_PID={pid}"]
    try:
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"[:120]
    found = None
    raw = ""
    try:
        for i, ln in enumerate(p.stdout):
            if "trade-bot starting" in ln:
                raw = ln.rstrip("\n")
                found = parse_start_line(raw)
                break
            if i >= max_lines:
                break
    finally:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:                                      # noqa: BLE001
            pass
    if found:
        return found, ""
    if raw:
        # 줄은 있는데 형식을 못 읽었다 — '없다' 와 처방이 다르다(형식이 바뀐 것, #82).
        return None, (f"PID {pid} 의 시작 줄은 있는데 형식을 못 읽었다 — "
                      f"{scrub(raw)[-160:]}")
    return None, (f"PID {pid} 의 앞 {max_lines}줄에 시작 줄이 없다"
                  "(저널 보존기간 밖일 수 있다)")


def other_bot_processes(own: set[int], proc_root: Path = Path("/proc")) -> list[dict]:
    """같은 서버에서 `trade.bot` 을 돌리는 **다른** 프로세스(409 의 흔한 원인)."""
    out = []
    for d in proc_root.glob("[0-9]*"):
        try:
            args = [a.decode("utf-8", "replace")
                    for a in (d / "cmdline").read_bytes().split(b"\0") if a]
        except Exception:                                      # noqa: BLE001
            continue
        if int(d.name) in own or not _is_bot_cmd(args):
            continue
        # ⚠️ 가린 **뒤에** 자른다 — 자르고 가리면 잘린 토큰 조각이 패턴을 빠져나간다
        # (`sudo … env TRADE_BOT_TOKEN=… python -m trade.bot` 같은 명령줄, 독립 리뷰 M3a).
        out.append({"pid": int(d.name), "cmd": scrub(" ".join(args))[:160]})
    return out


_PY_RE = re.compile(r"python[\d.]*$")
_PY_OPT_ARG = ("-X", "-W", "-Q")          # 인자를 하나 더 받는 인터프리터 옵션


def _is_bot_cmd(args: list[str]) -> bool:
    """파이썬이 `-m trade.bot` 또는 `…/trade/bot.py` 를 **실행하는** 명령인가.

    인터프리터 뒤 첫 비옵션 인자가 스크립트다 — `vim trade/bot.py` · `grep … trade/bot.py`
    · `python -m pytest trade/bot.py` 는 봇이 아니다(독립 리뷰 M3b). `sudo`·`env` 를
    앞에 둔 실행도 잡는다(인터프리터를 명령줄에서 찾는다). `trade.bot_health` 같은
    형제도 아니다."""
    for i, a in enumerate(args):
        if not _PY_RE.match(Path(a).name):
            continue
        rest = args[i + 1:]
        k = 0
        while k < len(rest):
            opt = rest[k]
            if opt == "-m":
                return k + 1 < len(rest) and rest[k + 1] == "trade.bot"
            if opt == "-c":
                return False
            if opt in _PY_OPT_ARG:
                k += 2
                continue
            if opt.startswith("-"):
                k += 1
                continue
            return opt.endswith("trade/bot.py")
        return False
    return False


def _forwards(read, since: str) -> tuple[list[dict], set[int], str]:
    """릴레이 유닛 저널 → (포워드 사건, 릴레이가 밝힌 원천 ID, 못 읽었으면 사유)."""
    lines, err, kind = read(RELAY_UNITS, since)
    return (relay_forwards(lines), relay_source_ids(lines),
            ("" if _readable(kind) else err or kind))


_SINCE_UNIT_S = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                 "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
                 "h": 3600, "hour": 3600, "hours": 3600,
                 "d": 86400, "day": 86400, "days": 86400}
_REL_SINCE = re.compile(r"^\s*(\d+)\s*([a-z]+)\s+ago\s*$")
_EPOCH_SINCE = re.compile(r"^\s*@(\d+)\s*$")
_ABS_SINCE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)(\s+UTC)?\s*$")


def widen_since(since: str, sec: int) -> str | None:
    """journalctl `--since` 값을 `sec` 초 앞당긴다. 순수 함수.

    알아듣는 꼴만 — 상대('N 단위 ago') · '@epoch' · 'YYYY-MM-DD HH:MM[:SS][ UTC]'. 모르는
    꼴이면 None(호출부가 같은 창으로 읽고 그렇다고 밝힌다 — 지어내지 않는다). 시간대 없는
    절대 시각은 journalctl 이 서버 로컬로 읽는다 — 같은 꼴로 되돌려 그 해석을 보존한다
    (서버 시간대가 서머타임을 안 쓰는 한 정확하다)."""
    s = since or ""
    m = _REL_SINCE.match(s)
    if m and m.group(2) in _SINCE_UNIT_S:
        return f"{int(m.group(1)) * _SINCE_UNIT_S[m.group(2)] + int(sec)} seconds ago"
    m = _EPOCH_SINCE.match(s)
    if m:
        return f"@{int(m.group(1)) - int(sec)}"
    m = _ABS_SINCE.match(s)
    if m:
        fmt = "%Y-%m-%d %H:%M:%S" if m.group(1).count(":") == 2 else "%Y-%m-%d %H:%M"
        t = datetime.strptime(m.group(1), fmt) - timedelta(seconds=int(sec))
        return t.strftime("%Y-%m-%d %H:%M:%S") + (" UTC" if m.group(2) else "")
    return None


def fact_id(kind: str, d: dict) -> str:
    """알린 사실의 신원 — 같은 사실은 한 번만 알린다(2차 독립 리뷰 L4). 순수 함수.

    포워드 사건(`fwd`) = 유닛·PID·시각·건수, 버림(`drop`)·예외로 놓친 글(`lost`) = 메시지
    번호·시각. 창이 굴러도 사실은 그대로라 신원도 그대로다 — 옛 판은 '창 안 첫 포워드' 로
    표식을 만들어 매시간 표식이 바뀌었고, 끊김이 이어지면 같은 포워드를 두 번씩 알렸다
    (리뷰 재현: 12시간 12통)."""
    t = d.get("ts")
    stamp = int(t.timestamp()) if t is not None else "?"
    if kind == "fwd":
        return f"fwd:{d.get('who')}:{d.get('pid')}:{stamp}:{d.get('n')}"
    return f"{kind}:{d.get('msg')}:{stamp}"


def delivery_check(window_s: int, *, seen=(), now: datetime | None = None,
                   read=read_journal, vouched=None) -> dict:
    """릴레이 포워드 vs 봇 수신 + 예외로 놓친 글 + 릴레이 원천 글의 출처 게이트 버림 —
    저널 두 개와 레포의 릴레이 선언만 본다(텔레그램·inbox 는 안 본다).

    `trade.scripts.health_check` 가 매시간 부른다. 저널을 못 읽으면 `kind=
    "unknown"` 과 사유 — 판정 불가를 '이상 없음' 으로 접지 않는다(#54).
    `seen` = 이미 알린 사실의 신원(`fact_id`). 누락은 **아직 안 알린 포워드 사건만으로**
    다시 대조한다(`fresh`) — 알린 누락이 창에 남아 있어도 같은 누락을 또 알리지 않고, 새
    사건의 누락은 막지 않는다(2차 독립 리뷰 L4). 알릴 사실의 신원은 `ids` 에 싣는다(호출부가
    알린 뒤 기록한다). 대조에서 모자람이 없던 사건은 싣지 않는다 — 아직 기다리는 새 포워드의
    수신이 그 사건의 누락을 잠시 덮었을 수 있어, 다음 실행이 다시 대조해야 한다.
    ⚠️ 봇 저널은 릴레이 창보다 `RUN_SLACK_S` **앞에서부터** 읽는다 — 창 첫머리에 찍힌
    백필 'done' 줄은 그 실행의 수신이 창 앞에 있을 수 있어, 같은 창으로 읽으면 멀쩡한
    실행을 누락으로 오보한다(독립 리뷰 M1). 예외·버림은 **창 안의** 줄만 알린다(넓힌 앞
    30분은 직전 실행의 창이다).
    ⚠️ 릴레이 포워드가 없어도 봇 저널은 읽는다 — 예외로 놓친 글은 손으로 돌린 백필(저널에
    안 남는다)의 글일 수도 있다. 출처를 모르는 예외(출처를 적지 않는 판)도 알린다 — 릴레이
    글이 아니라고 단정할 수 없다(#165).
    ⚠️ 여기는 재시작 전후를 **가르지 않는다**(`collect` 는 가른다) — 알림이 묻는 것은
    '지난 두 시간에 글이 빠졌나' 이고 그건 어느 프로세스의 일이든 사실이다. 지금 상태가
    원인인지는 알림이 가리키는 `bot_health` 가 가른다(재시작 전 누락은 ⚠️ 메모).
    `vouched` = 재게시 보증 기록(`relay_origins.load` 의 짝) — 호출부가 봇과 같은 데이터
    디렉터리에서 읽어 넘긴다. 보증된 재게시 글을 보증 **뒤에** 버렸으면 릴레이 글의 버림으로
    알린다(실수 #411). 안 넘기면 원천 이름·ID 로만 가른다.
    ⚠️ 못 보는 축(#274): 원천이 사용자명을 바꿨는데 창 안에 그 릴레이의 접속 줄
    (`source(marked)=`)이 없으면 그 버림을 릴레이 것으로 못 알아본다 — 받음으로만 센다.
    `bot_health` 는 텔레그램·inbox 로 ID 를 배워 잡는다. 수 대조의 상쇄 한계는
    `delivery_gap` 독스트링.
    """
    now = now or datetime.now(timezone.utc)
    empty = {"lost": [], "relay_drops": [], "new_lost": [], "new_drops": [],
             "fresh": {"kind": "none"}, "ids": []}
    fwd, ids, ferr = _forwards(read, f"{int(window_s)} seconds ago")
    if ferr:
        return {"kind": "unknown", "sent": 0, "got": 0, "undated": 0, **empty,
                "err": f"릴레이 저널을 못 읽었다 — {ferr}"}
    bl, berr, bkind = read((_SERVICE,), f"{int(window_s) + RUN_SLACK_S} seconds ago")
    if not _readable(bkind):
        return {"kind": "unknown", "sent": sum(x["n"] for x in fwd), "got": 0,
                "undated": 0, **empty, "err": f"trade-bot 저널을 못 읽었다 — {berr or bkind}"}
    jf = journal_facts(bl)
    names = relay_sources(_REPO / "trade" / "scripts")
    drops = forward_drops(jf["drops_origin"], names, ids, vouched)
    g = delivery_gap(fwd, jf["ingested"], now, dropped=drops)
    cut = now - timedelta(seconds=int(window_s))

    def _inside(d):
        return d.get("ts") is not None and d["ts"] >= cut
    seen = set(seen or ())
    g["relay_drops"] = [d for d in drops if d["relay"] and _inside(d)
                        and not (d.get("why") == "vouch"
                                 and vouch_recovered(d, jf["vouch_accepts"]))]
    g["lost"] = [e for e in lost_posts(jf["exceptions"], jf["ingested_ids"], names, ids,
                                       vouched)
                 if e["relay"] is not False and _inside(e)]
    new_fwd = [x for x in g["due"] if fact_id("fwd", x) not in seen]
    g["fresh"] = (delivery_gap(new_fwd, jf["ingested"], now, dropped=drops) if new_fwd
                  else {"kind": "none"})
    g["new_drops"] = [d for d in g["relay_drops"] if fact_id("drop", d) not in seen]
    g["new_lost"] = [e for e in g["lost"] if fact_id("lost", e) not in seen]
    short = g["fresh"].get("kind") in ("total", "partial")
    g["ids"] = (([fact_id("fwd", x) for x in new_fwd] if short else [])
                + [fact_id("drop", d) for d in g["new_drops"]]
                + [fact_id("lost", e) for e in g["new_lost"]])
    g["err"] = ""
    return g


def gap_needs_alert(g: dict) -> bool:
    """알릴 일인가 — **아직 안 알린** 사실 중에 ① 포워드의 누락(`fresh`) ② 예외로 놓친 글
    (릴레이 원천이거나 출처를 모르는 것) ③ 릴레이 원천 글의 출처 게이트 버림이 있다 —
    릴레이가 **보증한** 재게시 글을 보증 뒤에 버린 것도 여기 든다(실수 #411). 보증 없는
    다른 출처의 버림은 알리지 않는다 — BeOn 이 되포워드한 남의 글이 대부분이라 매시간 못
    고칠 경고가 된다(#260). ⚠️ 그래서 보증하기 **전의** 판이 포워드한 나쁜양파 재게시 글의
    버림(3차 독립 리뷰 M2)은 여기서 안 보인다 — 여기선 둘을 못 가른다. `python -m
    trade.bot_health` 의 메모가 출처·건수와 가르는 방법(`FIND_CMD`)을 말한다."""
    return ((g.get("fresh") or {}).get("kind") in ("total", "partial")
            or bool(g.get("new_lost")) or bool(g.get("new_drops")))


def gap_alert_text(g: dict) -> str:
    """`delivery_check` 결과 → 텔레그램 알림(HTML). **아직 안 알린** 사실만 싣는다 —
    토큰·원문 없음."""
    fr = g.get("fresh") or {}
    short = fr.get("kind") in ("total", "partial")
    nl, nd = g.get("new_lost") or [], g.get("new_drops") or []
    heads = ((["수신 누락"] if short else []) + (["처리 중 예외로 놓친 글"] if nl else [])
             + (["출처 게이트 버림"] if nd else []))
    out = [f"⚠️ <b>trade-bot {' · '.join(heads) or '수신 점검'}</b>"]
    if short:
        recv = fr["got"] + (fr.get("dropped") or 0)
        what = ("<b>한 건도 못 받았습니다</b>" if fr["kind"] == "total"
                else f"{recv}건만 받았습니다")
        out.append(f"릴레이가 {fr['sent']}건을 비공개 채널로 포워드했는데(첫 {_kst(fr['first'])} · "
                   f"{', '.join(fr.get('who') or [])}) 봇은 {what}.")
    if nl:
        n_unk = sum(1 for e in nl if e.get("relay") is None)
        out.append(f"봇이 채널 글 <b>{len(nl)}건을 처리하다 예외로 놓쳤습니다</b>(번호 {_msgs(nl)}"
                   " — 수신 줄 없이 끝났습니다"
                   + (f" · 그중 {n_unk}건은 출처를 모릅니다" if n_unk else "") + ").")
    nv = [d for d in nd if d.get("why") == "vouch"]
    if len(nd) > len(nv):
        out.append(f"봇이 릴레이 원천의 포워드 <b>{len(nd) - len(nv)}건을 출처 게이트에서 "
                   "버렸습니다</b>(.env TRADE_SOURCE_ORIGIN 과 원천 사용자명 확인).")
    if nv:
        # 처방이 다르다(#82) — .env 가 아니라 보증 기록·데이터 디렉터리(실수 #411).
        out.append(f"봇이 릴레이가 <b>보증한</b> 재게시 글 <b>{len(nv)}건을 출처 게이트에서 "
                   "버렸습니다</b>(봇이 보증 기록을 못 읽었거나 봇과 릴레이의 데이터 디렉터리가 "
                   "갈렸다 — 버림 줄의 vouch= 칸).")
    out += ["inbox·대시보드에 안 들어갑니다. 원인 가르기(읽기 전용):", f"<code>{USAGE}</code>"]
    return "\n".join(out)


def vouch_facts(path) -> dict:
    """재게시 보증 기록(읽기 전용) — 봇 게이트와 **같은 함수**(`relay_origins.load`)로 읽는다
    (#35 진단은 화면이 쓰는 그 경로를). 못 읽으면 사유를 싣는다(#54 — 판정에서 뺀다)."""
    p = Path(path)
    pairs, err = _relay.load(p)
    return {"path": str(p), "exists": p.exists(), "pairs": pairs, "err": err}


def collect(since: str, *, now: datetime | None = None, env=None, facts_fn=None,
            read=read_journal, start_fn=find_start_line, tg_fn=telegram_facts,
            procs_fn=other_bot_processes) -> dict:
    """모든 사실을 모은다(읽기 전용). 각 원천은 주입 가능 — 테스트가 태운다."""
    now = now or datetime.now(timezone.utc)
    env = env if env is not None else trade_env()
    if facts_fn is None:
        from bot.daily_kr_flow import systemd_facts

        def facts_fn():
            return systemd_facts(timer=None, service=_SERVICE,
                                 extra=("MainPID", "NRestarts"))
    facts = facts_fn()
    f: dict = {"now": now, "env": env, "facts": facts,
               "unit": listener_verdict(facts, unit=_UNIT, reauth=False),
               "inbox_expected": env.get("inbox"),
               "relays": relay_sources(_REPO / "trade" / "scripts")}
    # 판정은 사용자가 준 --since 창의 줄로 한다. 읽히는데 창 안에 0줄이면 '못 읽음' 이
    # 아니라 **아무것도 안 찍었다** 는 사실이다 — 빈 사실로 판정을 태운다(살아 있다면
    # 폴링 0회 = 문제, #52).
    lines, err, kind = read((_SERVICE,), since)
    f["journal"] = journal_facts(lines) if _readable(kind) else None
    f["journal_err"] = err
    # 수신은 그보다 RUN_SLACK_S 앞부터 센다 — 창 첫머리 백필의 수신이 창 앞에 있을 수
    # 있다(독립 리뷰 M1). 그 넓힌 저널을 판정에까지 쓰면 사용자가 창으로 뺀 사건(그 전
    # 409 등)이 '지금' 의 ❌ 가 된다 — 한 번 더 읽어 수신 대조에만 쓴다(2차 독립 리뷰 L5).
    # 못 알아듣는 --since 꼴이거나 넓혀 읽기가 실패하면 같은 창으로 세고 밝힌다.
    wide = widen_since(since, RUN_SLACK_S) if f["journal"] is not None else None
    f["bot_since"], f["bot_since_widened"], f["bot_since_err"] = since, False, ""
    wlines = lines
    if wide is not None:
        wl, werr, wkind = read((_SERVICE,), wide)
        if _readable(wkind):
            wlines, f["bot_since"], f["bot_since_widened"] = wl, wide, True
        else:
            f["bot_since_err"] = werr or wkind
    f["journal_wide"] = journal_facts(wlines) if f["journal"] is not None else None
    main_pid = facts.get("s_MainPID")
    main_pid = int(main_pid) if str(main_pid or "").isdigit() and int(main_pid) > 0 else None
    # 지금 프로세스의 줄만 — 판정은 이걸로 한다(재시작 전 프로세스의 일은 메모, 독립 리뷰 H1).
    f["journal_cur"] = (journal_facts([ln for ln in lines if _pid(ln) == main_pid])
                        if f["journal"] is not None and main_pid else None)
    run = None
    if f["journal_wide"]:
        # 시작 줄은 넓힌 저널에서도 찾는다 — 창 첫머리 직전에 뜬 프로세스면 PID 조회 없이 끝난다
        mine = [s for s in f["journal_wide"]["starts"] if main_pid and s["pid"] == main_pid]
        run = mine[-1] if mine else None
    f["running_err"] = ""
    if run is None and main_pid:
        run, f["running_err"] = start_fn(main_pid)
    elif run is None:
        f["running_err"] = ("systemd 에 못 물어 실행 중인 PID 를 모른다" if not facts.get("ok")
                            else "실행 중인 프로세스(MainPID)가 없다")
    f["running"] = run
    fwd, rids, ferr = _forwards(read, since)
    f["forwards"] = fwd
    f["relay_ids"] = sorted(rids)
    # 보증 기록은 대조 **전에** 읽는다 — 보증된 재게시 글의 버림이 릴레이 것으로 든다(#411).
    # 데이터 디렉터리는 봇과 같은 규약(.env TRADE_DATA_DIR 또는 ~/.trade — `trade_env`).
    ddir = env.get("data_dir") or (str(Path(env["inbox"]).parent) if env.get("inbox") else "")
    f["vouch"] = vouch_facts(_relay.path_in(ddir)) if ddir else {}
    # inbox 는 대조 **전에** 읽는다 — 거기서 배운 원천 ID 가 버림 분류(릴레이 것인가)에 든다.
    f["inbox"] = inbox_facts(Path(env.get("inbox") or ""), now) if env.get("inbox") else {}
    if ferr:
        f["gap"] = {"kind": "unknown", "err": f"릴레이 저널을 못 읽었다 — {ferr}", "undated": 0}
    elif f["journal"] is None:
        f["gap"] = ({"kind": "unknown", "undated": 0,
                     "err": f"trade-bot 저널을 못 읽었다 — {err}"} if fwd else {"kind": "none"})
    else:
        jw = f["journal_wide"]
        ids = set(rids)
        for u in f["relays"]:
            ids |= set((((f["inbox"].get("by") or {}).get(u.lower())) or {}).get("ids") or ())
        # 수 대조는 버림을 받음으로 셀 뿐 릴레이 글인지는 안 본다 — 보증(#411)은 여기 안 넘긴다
        # (넘겨도 관측되는 차이가 없는 배선은 가드도 못 한다, #291). 판정은 `verdict` 가
        # `f["vouch"]` 로 한다.
        drops = forward_drops(jw["drops_origin"], f["relays"], ids)
        # 판정은 **지금 프로세스의 몫**으로만 한다 — 재시작 전 포워드는 옛 판·옛 설정이
        # 받았거나 버린 것이라 지금 상태의 증거가 아니고, 다시 포워드한 뒤에도 24시간
        # 창에 남아 '보냄 54 / 받음 27' 같은 거짓 누락을 만든다. 그 전 누락은 사실
        # 메모로 따로 말한다(다시 포워드해야 들어온다). 재시작을 **걸친** 실행은 그 시작부터
        # 두 프로세스의 수신을 같이 센다(`split_at_restart`, 2차 독립 리뷰 M2).
        cur, old, nb, straddle = split_at_restart(fwd, (run or {}).get("ts"))
        f["gap"] = delivery_gap(cur, jw["ingested"], now, not_before=nb, dropped=drops)
        due = {id(x) for x in f["gap"].get("due") or []}
        f["gap"]["straddle"] = sum(1 for x in straddle if id(x) in due)
        f["gap_before"] = (delivery_gap(old, jw["ingested"], now, dropped=drops)
                           if old else {"kind": "none"})
    f["tg"] = tg_fn(env.get("token") or "", env.get("dest"), sorted(f["relays"]))
    f["others"] = procs_fn({os.getpid()} | ({main_pid} if main_pid else set()))
    return f


_EXC_KO = {"handler": "핸들러", "polling": "폴링", "unlabeled": "표식 없음(옛 판)"}


def _vouch_line(vf: dict) -> str:
    """재게시 보증 기록 한 줄 — 어느 채널의 글을 몇 건, 언제까지 보증했나(실수 #411)."""
    if not vf:
        return "재게시 보증 기록: 데이터 디렉터리를 몰라 안 읽었다"
    if vf.get("err"):
        return f"재게시 보증 기록 {vf.get('path')}: 못 읽음({vf['err']})"
    pairs = vf.get("pairs") or {}
    if not pairs:
        return (f"재게시 보증 기록 {vf.get('path')}: "
                + ("비어 있음" if vf.get("exists") else
                   "없음(아직 재게시 글을 보증한 적 없다 — 보증하는 판의 릴레이가 포워드하면 생긴다)"))
    chats: dict = {}
    for (cid, _mid), v in pairs.items():
        e = chats.setdefault(cid, {"n": 0, "title": v.get("title"), "last": None})
        e["n"] += 1
        at = v.get("at")
        if at is not None and (e["last"] is None or at > e["last"]):
            e["last"] = at
    ranked = sorted(chats.items(), key=lambda kv: kv[1]["last"] or datetime.min.replace(
        tzinfo=timezone.utc), reverse=True)
    parts = [f"{cid}" + (f"({e['title']!r})" if e["title"] else "") + f" {e['n']}건"
             + (f"·최근 {_kst(e['last'])}" if e["last"] else "") for cid, e in ranked[:3]]
    return (f"재게시 보증 기록 {vf.get('path')}: 채널 {len(chats)}개 · 글 {len(pairs)}건 — "
            + ", ".join(parts) + (f" 외 채널 {len(chats) - 3}개" if len(chats) > 3 else ""))


def render(f: dict, since: str) -> list[str]:
    """사실 → 사람이 읽는 줄(판정 제외). 토큰·원문 비밀값 없음."""
    now = f["now"]
    env = f.get("env") or {}
    out = [f"# trade.bot_health v{_VER} · 코드 지문 {_sig()} · 인터프리터 {sys.executable}",
           f"# 읽기 전용 — 텔레그램엔 {'·'.join(READ_ONLY_METHODS)} 만 묻는다"
           "(getUpdates 는 안 부른다). 토큰은 어디에도 찍지 않는다.",
           f"# 창 --since {since!r}"
           + (f"(판정은 이 창 · 봇 수신은 {RUN_SLACK_S // 60}분 앞부터 센다: "
              f"{f.get('bot_since')!r})" if f.get("bot_since_widened") else
              f"(봇 저널을 넓혀 읽지 못해 수신도 이 창으로 센다 — {f.get('bot_since_err')} · "
              "창 첫머리 백필의 수신을 덜 셀 수 있다)" if f.get("bot_since_err") else
              "(이 꼴은 못 넓혀 수신도 이 창으로 센다 — 창 첫머리 백필의 수신을 덜 셀 수 있다)"
              if f.get("journal") is not None and f.get("bot_since") else "")
           + f" · 설정 출처 {env.get('src')}"
           + (f" · .env 읽기 실패 {env.get('err')}" if env.get("err") else "")]
    facts = f.get("facts") or {}
    restarts = facts.get("s_NRestarts")
    out.append(f"① 유닛: {(f.get('unit') or {}).get('text')}"
               + (f" · 자동 재시작 누적 {restarts}회" if restarts not in (None, "") else ""))

    tg = f.get("tg") or {}
    if not tg.get("token"):
        out.append("② 텔레그램: 토큰 없음 — 묻지 않았다")
    else:
        me = tg.get("me") or {}
        wh = tg.get("webhook") or {}
        r = wh.get("result") or {}
        parts = [f"봇 @{tg.get('username')}" if me.get("ok") else f"getMe 실패({me.get('err')})"]
        if wh.get("ok"):
            parts.append(f"웹훅 {'있음(' + _host(r['url']) + ')' if r.get('url') else '없음'}")
            parts.append(f"대기 업데이트 {r.get('pending_update_count', 0)}건")
            au = r.get("allowed_updates")
            # 응답에 없으면 '기본값' 이라고 단정하지 않는다 — 웹훅이 없을 때 텔레그램이
            # 이 칸을 채우는지 재지 않았다(#165). 판정에도 안 쓴다.
            parts.append(f"수신 종류 {', '.join(au)}" if isinstance(au, list) and au
                         else "수신 종류 응답에 없음(판정 안 함)")
            if r.get("last_error_date"):
                le = datetime.fromtimestamp(int(r["last_error_date"]), timezone.utc)
                parts.append(f"마지막 전달 오류 {_kst(le)} · {scrub(r.get('last_error_message'))[:120]}")
        elif wh:
            parts.append(f"웹훅 정보 실패({wh.get('err')})")
        for u, res in sorted((tg.get("sources") or {}).items()):
            r = res.get("result") or {}
            parts.append(f"원천 @{u} → " + (f"@{r.get('username')}({r.get('id')})" if res.get("ok")
                                            else f"조회 실패({res.get('err')})"))
        mem = tg.get("member")
        if mem is not None:
            parts.append(f"대상 채널 {env.get('dest')} 에서 봇 = "
                         + ((mem.get("result") or {}).get("status") or "?" if mem.get("ok")
                            else f"조회 실패({mem.get('err')})"))
        out.append("② 텔레그램: " + " · ".join(parts))

    run = f.get("running")
    if run is None:
        out.append(f"③ 실행 중 설정: 시작 줄 없음 — {f.get('running_err')}")
    else:
        def _fmt(v):
            return "전부(목록 없음)" if v == "any" else ("읽기 실패" if v is None else str(sorted(v)))
        out.append(f"③ 실행 중 설정(PID {run['pid']} · 시작 {_kst(run['ts'])}): inbox "
                   f"{run['inbox']} · 허용 채널 {_fmt(run['allowed'])} · 출처 "
                   f"{_fmt(run['origin'])} · 버림 기록 {'켜짐' if run['drop_log'] else '없음(옛 판)'}"
                   f" · 재게시 보증 {'받음' if run.get('vouch') else '모름(옛 판)'}")
    out.append(f"   릴레이 목적지 {env.get('dest')} · 원천 "
               + ", ".join(f"{u}({'/'.join(s)})" for u, s in sorted((f.get('relays') or {}).items())))
    out.append("   " + _vouch_line(f.get("vouch") or {}))

    j = f.get("journal")
    if j is None:
        out.append(f"④ 저널: 못 읽음 — {f.get('journal_err')}")
    else:
        sp = j["span"]
        polls = " · ".join(f"{k}×{v}" for k, v in sorted(j["polls"].items())) or "없음"
        ing = [t for t in j["ingested"] if t is not None]
        last_ok = (f"{_age((now - j['last_ok']).total_seconds())} 전" if j["last_ok"] else "없음")
        exc = Counter(e["kind"] for e in j["exceptions"])
        out.append(f"④ 저널 {j['n']}줄({sp.get('first')}~{sp.get('last')}): 폴링 {polls} · "
                   f"마지막 정상 폴링 {last_ok} · 수신(ingested) {len(j['ingested'])}건"
                   + (f"(마지막 {_kst(max(ing))})" if ing else "")
                   + f" · 보증 수용 {len(j.get('vouch_accepts') or ())}건"
                   + f" · 버림 채널 {len(j['drops_channel'])}·출처 {len(j['drops_origin'])} · "
                   f"시작 {len(j['starts'])}회"
                   + (" · 예외 " + " · ".join(f"{_EXC_KO.get(k, k)} {v}"
                                              for k, v in sorted(exc.items())) if exc else ""))
        jc = f.get("journal_cur")
        if jc is not None:
            out.append(f"   지금 프로세스(PID {(f.get('facts') or {}).get('s_MainPID')}) 줄 "
                       f"{jc['n']} · 폴링 "
                       + (" · ".join(f"{k}×{v}" for k, v in sorted(jc["polls"].items())) or "없음")
                       + f" · 수신 {len(jc['ingested'])} · 버림 채널 {len(jc['drops_channel'])}"
                       f"·출처 {len(jc['drops_origin'])} — 판정은 이 줄들로 한다")
        for ln in j["errors"][-3:]:
            out.append(f"   오류 표본: {ln[-220:]}")
    fwd = f.get("forwards") or []
    gap = f.get("gap") or {}
    gb = f.get("gap_before") or {}
    if fwd:
        by: Counter = Counter()
        for x in fwd:
            by[x["who"]] += x["n"]
        out.append(f"⑤ 릴레이 포워드 {sum(by.values())}건("
                   + ", ".join(f"{k} {v}" for k, v in sorted(by.items()))
                   + f") · 마지막 {_kst(max((x['ts'] for x in fwd if x['ts']), default=None))}"
                   + (f" · 지금 프로세스 뒤 대조: 보냄 {gap.get('sent')} / 받음 "
                      f"{gap.get('got', 0) + (gap.get('dropped') or 0)}"
                      + (f"(기록 {gap.get('got')} · 게이트 버림 {gap.get('dropped')})"
                         if gap.get("dropped") else "")
                      + (f" · 재시작 전부터 센 포워드 {gap.get('straddle')}건 포함(그 시작부터 두 "
                         "프로세스의 수신을 같이 셌다)" if gap.get("straddle") else "")
                      if gap.get("kind") not in (None, "none", "unknown") else "")
                   + (f" · 재시작 전 포워드 {gb.get('sent')}건 / 그 뒤 수신 {gb.get('got')}건"
                      + (f"·버림 {gb.get('dropped')}건" if gb.get("dropped") else "")
                      if gb.get("kind") not in (None, "none", "unknown") else ""))
    else:
        out.append("⑤ 릴레이 포워드: 창 안에 없음(systemd 유닛 실행만 센다 — 손으로 돌린 백필은 안 잡힌다)")

    ib = f.get("inbox") or {}
    if ib.get("exists"):
        last = ib.get("last")
        src = sorted(((u or "(출처 없음)", e) for u, e in (ib.get("by") or {}).items()),
                     key=lambda x: x[1]["last"] or datetime.min.replace(tzinfo=timezone.utc),
                     reverse=True)[:5]
        out.append(f"⑥ inbox {ib['path']} · {ib['lines']}줄 · 마지막 기록 "
                   + (f"{_kst(last[0])}({last[1] or '출처 없음'})" if last
                      else "시각을 읽을 수 있는 기록 없음"))
        out.append("   원천별 마지막 수신(24시간 건수): "
                   + " · ".join(f"{u} {_kst(e['last'])}({e['n24']})" for u, e in src))
    elif ib:
        out.append(f"⑥ inbox 없음: {ib.get('path')}")
    others = f.get("others") or []
    out.append("⑦ 같은 서버의 다른 trade.bot 프로세스: "
               + (", ".join(f"PID {o['pid']} {o['cmd']}" for o in others) if others else "없음"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="trade-bot 수신 경로 진단(읽기 전용)")
    ap.add_argument("--since", default="86400 seconds ago",
                    help='journalctl --since 값 (기본: 24시간 전). 시간대 없는 시각은 서버 '
                         '로컬로 읽힌다 — 어디서나 같은 뜻은 UTC 로: "2026-09-24 22:40 UTC"')
    args = ap.parse_args(argv)
    f = collect(args.since)
    for ln in render(f, args.since):
        print(ln)
    rc, lines = verdict(f)
    print("⑧ 판정:")
    for ln in lines:
        print(f"   {ln}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
