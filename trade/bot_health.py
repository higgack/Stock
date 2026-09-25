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

그걸 사람이 명령 네 개로 맞춰 보게 하지 않고 한 번에 가른다(§Automation-first
· #252). 그리고 같은 판정(`delivery_gap`)을 `trade.scripts.health_check` 가
매시간 돌려, 릴레이가 보낸 만큼 봇이 못 받았으면 **사람이 알아채기 전에**
알린다.

읽기 전용 계약 — 텔레그램엔 `READ_ONLY_METHODS`(getMe · getWebhookInfo ·
getChatMember · getChat)만 묻는다. ⚠️ getUpdates 는 **절대** 부르지 않는다: 돌고 있는
봇과 409 로 부딪히고, 여기서 받아 간 업데이트는 봇이 영영 못 본다. 파일은
읽기만 한다(#264). 토큰은 어떤 출력에도 싣지 않는다 — 오류 문구에 URL 이
섞여도 지운다(trade-bot 저널의 getUpdates 줄엔 토큰이 **평문**이다, §Secrets).

python-telegram-bot 을 import 하지 않는다 — 봇 import 가 깨진 날에도 이 진단은
돌아야 한다(그게 가를 갈래 중 하나다). 판정은 순수 함수라 의존성 없이 태운다
(#176).

⚠️ 못 보는 축(#274): 릴레이 목록은 `trade/scripts/*.py` 의 모듈 수준
`SOURCE_USERNAME` 에서 파생한다 — 포워드하는 스크립트는 그 상수를 둬야 한다
(회귀가 강제한다). 손으로 돌린 백필은 저널에 안 남아 '릴레이 포워드' 계수에
안 잡힌다(systemd 유닛의 실행만 센다).

Usage on host:
    cd ~/stock-trade && .venv/bin/python -m trade.bot_health
    cd ~/stock-trade && .venv/bin/python -m trade.bot_health --since "2026-09-25 07:40"
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

from trade.listener_health import _ts, listener_verdict, scanned_span

_VER = 1
_UNIT = "trade-bot"
_SERVICE = f"{_UNIT}.service"
_REPO = Path(__file__).resolve().parents[1]
_KST = timezone(timedelta(hours=9))
USAGE = "cd ~/stock-trade && .venv/bin/python -m trade.bot_health"

# 텔레그램엔 이것만 묻는다 — 목록 밖 메서드는 `tg_call` 이 거부한다.
READ_ONLY_METHODS = ("getMe", "getWebhookInfo", "getChatMember", "getChat")
_ADMIN = ("creator", "administrator")

# 채널 글을 비공개 채널로 넘기는 유닛들(포워드 계수의 모집단).
RELAY_UNITS = ("trade-bot-badonion-listener", "trade-bot-beon-listener",
               "trade-bot-badonion-sync", "trade-bot-beon-sync")

# 폴링이 이보다 오래 없으면 멈춘 것 — watchdog(deploy/trade-watchdog.sh) 과 같은 창.
POLL_STALE_S = 300
# 포워드 뒤 이만큼은 기다려야 '안 왔다' 고 말한다 — 봇은 보통 몇 초 안에 받는다.
GAP_GRACE_S = 180
# 백필의 'done: forwarded N' 은 **실행 끝**에 한 번 찍힌다 — 그 N 건의 실제 포워드는
# 그보다 앞이다(2026-09-25 실측 17초 · 큰 BeOn 회수는 더 길다). 수신은 그만큼 앞에서
# 부터 센다. 이보다 긴 실행은 그 앞부분 수신을 못 세어 '일부 누락' 으로 과대보고할 수
# 있다 — 그래서 일부 누락은 수까지 적어 사람이 판정하게 한다.
RUN_SLACK_S = 1800

# 봇이 main() 에서 `trade-bot starting — …` 를 찍는다(trade/bot.py).
_START_RE = re.compile(
    r"trade-bot starting — inbox=(?P<inbox>.+?) media=(?P<media>.+?) "
    r"allowed=(?P<allowed><discovery>|\{[^}]*\}) "
    r"origin=(?P<origin><any>|\{[^}]*\}) concurrency=(?P<conc>\d+)(?P<rest>.*)$")
_PID_RE = re.compile(r"\[(\d+)\]:")
_POLL_RE = re.compile(r'/getUpdates "HTTP/[\d.]+ (\d{3})')
_FWD_RE = re.compile(r"(?P<who>[\w.]+) — (?:done: )?forwarded (?P<n>\d+) "
                     r"(?:of \d+ candidate messages|msg\(s\))")
_ERR_RE = re.compile(r"\[(?:ERROR|CRITICAL)\]|Traceback|telegram\.error\.")
_DROP_ORIGIN_RE = re.compile(r"dropped msg=\S+ reason=origin origin_type=(?P<type>\S+) "
                             r"origin_chat=(?P<chat>\S+) origin_username=(?P<user>\S+)")
_DROP_CHANNEL_RE = re.compile(r"dropped channel=(?P<chat>-?\d+)")


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


# ── 순수 판정 ───────────────────────────────────────────────────────────

def parse_start_line(line: str) -> dict | None:
    """봇 시작 줄 → 실행 중 설정. 순수 함수.

    `allowed`/`origin` 은 `"any"`(목록 없음 = 전부 받는다) | 집합 | `None`
    (읽지 못함 — 판정 불가, 전부 받는다고 **가정하지 않는다**, #165).
    `drop_log` 는 이 프로세스가 게이트 버림을 저널에 적는 판인가(실수 #406).
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
            "drop_log": "drop_log=on" in m.group("rest")}


def journal_facts(lines) -> dict:
    """trade-bot 저널 줄 → 폴링·수신·버림 사실. 순수 함수(비밀값은 지워서 싣는다)."""
    polls: Counter = Counter()
    last_ok = last_poll = None
    ingested: list = []
    drops_origin: list[dict] = []
    drops_channel: list[dict] = []
    starts: list[dict] = []
    errors: list[str] = []
    for ln in lines or []:
        m = _POLL_RE.search(ln)
        if m:
            polls[m.group(1)] += 1
            t = _ts(ln)
            last_poll = t or last_poll
            if m.group(1) == "200":
                last_ok = t or last_ok
            continue
        if " ingested msg=" in ln:
            ingested.append(_ts(ln))
            continue
        m = _DROP_ORIGIN_RE.search(ln)
        if m:
            chat = m.group("chat")
            drops_origin.append({"line": scrub(ln), "type": m.group("type"),
                                 "chat": int(chat) if chat.lstrip("-").isdigit() else None,
                                 "user": "" if m.group("user") == "None" else m.group("user")})
            continue
        m = _DROP_CHANNEL_RE.search(ln)
        if m:
            drops_channel.append({"line": scrub(ln), "chat": int(m.group("chat"))})
            continue
        if "trade-bot starting" in ln:
            p = parse_start_line(ln)
            if p:
                starts.append(p)
            continue
        if _ERR_RE.search(ln):
            errors.append(scrub(ln))
    return {"n": len(lines or []), "span": scanned_span(lines or []),
            "polls": dict(polls), "last_ok": last_ok, "last_poll": last_poll,
            "ingested": ingested, "drops_origin": drops_origin,
            "drops_channel": drops_channel, "starts": starts,
            "errors": errors[-6:]}


def relay_forwards(lines) -> list[dict]:
    """릴레이 유닛 저널 → 포워드 사건(시각·건수·누가). 순수 함수."""
    out = []
    for ln in lines or []:
        m = _FWD_RE.search(ln)
        if m and int(m.group("n")) > 0:
            out.append({"ts": _ts(ln), "n": int(m.group("n")), "who": m.group("who"),
                        "done": "done: forwarded" in ln})
    return out


def delivery_gap(forwards, ingested_ts, now: datetime, *,
                 grace_s: int = GAP_GRACE_S, slack_s: int = RUN_SLACK_S) -> dict:
    """릴레이가 보낸 만큼 봇이 받았나. 순수 함수.

    `kind`: none(기다릴 만큼 지난 포워드가 없다) · ok · partial(일부만) ·
    total(포워드 뒤로 **한 건도** 못 받았다) · unknown(시각을 못 읽음).
    ⚠️ 수신은 1:1 이다 — 포워드한 메시지 하나가 채널 글 하나, 봇 `ingested`
    한 줄이다(앨범 멤버도 각각). 시각을 못 읽은 사건은 세지 않고 **센 수를
    말한다**(#54 — 모르는 것을 '누락' 으로 세면 없는 결함을 만든다).
    """
    due = [f for f in forwards or []
           if f.get("ts") is not None and (now - f["ts"]).total_seconds() >= grace_s]
    undated = sum(1 for f in forwards or [] if f.get("ts") is None)
    if not due:
        return {"kind": "unknown" if undated else "none", "sent": 0, "got": 0,
                "undated": undated}
    first = min(f["ts"] for f in due)
    last = max(f["ts"] for f in due)
    # 백필 'done' 줄은 실행 끝 시각이라 그 실행의 수신은 더 앞에서 시작한다.
    start = first - timedelta(seconds=slack_s if any(f["done"] for f in due) else 5)
    sent = sum(f["n"] for f in due)
    got = sum(1 for t in ingested_ts or [] if t is not None and t >= start)
    after_last = sum(1 for t in ingested_ts or [] if t is not None and t >= last)
    kind = "ok" if got >= sent else ("total" if got == 0 else "partial")
    return {"kind": kind, "sent": sent, "got": got, "after_last": after_last,
            "first": first, "last": last, "undated": undated,
            "who": sorted({f["who"] for f in due})}


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


_OLD_BUILD_NOTE = ("실행 중인 봇은 게이트 버림을 저널에 적지 않는 옛 판이다 — 위의 '버림 0건' 은 "
                   "버리지 않았다는 증거가 아니다(이 수정이 배포돼 봇이 재시작되면 적힌다)")


def verdict(f: dict) -> tuple[int, list[str]]:
    """모든 사실 → 판정 줄. 순수 함수. rc: 0 이상 없음 · 1 문제 · 2 판정 불가.

    ❌ 는 고칠 수 있는 것만, ❓ 는 못 잰 것(통과가 아니다, #54), ⚠️ 는 사실 메모.
    """
    bad: list[str] = []          # 원인(고칠 것)
    unk: list[str] = []
    notes: list[str] = []
    symptom: list[str] = []      # 증상(보낸 만큼 못 받음) — 원인이 없으면 따로 말한다
    now = f["now"]

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
        head = (f"릴레이가 {gap['sent']}건을 포워드했는데(첫 {_kst(gap['first'])} · "
                f"{', '.join(gap['who'])}) 봇이 받은 것은 {gap['got']}건이다")
        symptom.append(head + (" — **한 건도 못 받았다**" if gap["kind"] == "total"
                               else f" — 마지막 포워드 뒤 수신 {gap['after_last']}건"))
    elif gap.get("kind") == "unknown":
        unk.append("릴레이 포워드와 봇 수신을 대조 못 했다 — "
                   + (gap.get("err") or f"포워드 {gap.get('undated')}건의 시각을 못 읽었다"))

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

    j = f.get("journal")
    if j is None:
        unk.append(f"trade-bot 저널을 못 읽었다 — {f.get('journal_err') or '사유 미상'}")
    else:
        n409 = j["polls"].get("409", 0)
        if n409:
            others = f.get("others") or []
            where = (f"이 서버에 다른 trade.bot 프로세스가 있다: "
                     f"{', '.join(str(o['pid']) for o in others)}" if others else
                     "이 서버엔 다른 trade.bot 프로세스가 없다 — 다른 서버·PC 에서 같은 "
                     "토큰으로 봇을 돌리는지 볼 것")
            bad.append(f"폴링이 409 Conflict 로 {n409}회 거절됐다 — 같은 토큰으로 다른 "
                       f"곳이 폴링(또는 웹훅)해 채널 글을 그쪽이 가져간다. {where}")
        if alive:
            if j["last_ok"] is None:
                bad.append("창 안에 정상 폴링(getUpdates 200)이 한 번도 없다 — 봇이 텔레그램"
                           "에서 아무것도 못 받고 있다")
            elif (now - j["last_ok"]).total_seconds() > POLL_STALE_S:
                bad.append(f"폴링이 {_age((now - j['last_ok']).total_seconds())} 전에 멈췄다"
                           f"(마지막 정상 {_kst(j['last_ok'])}) — watchdog 가 5분 안에 재시작"
                           "해야 정상이다")
        # 버림이 다 결함은 아니다(#82) — 다른 채널의 글·운영자가 직접 쓴 글은 정상이다.
        mine = [d for d in j["drops_channel"] if dest is not None and d["chat"] == dest]
        if mine:
            bad.append("봇이 릴레이 목적지의 글을 **채널 게이트**에서 버렸다 — "
                       + mine[-1]["line"][-240:])
        others_ch = sorted({d["chat"] for d in j["drops_channel"]} - {dest})
        if others_ch:
            notes.append(f"허용 목록 밖 채널 {others_ch} 의 글을 버렸다 — 봇이 관리자인 다른 "
                         "채널이면 정상이다")
        relay_names = {u.lower() for u in (f.get("relays") or {})}
        # 직접 쓴 글(type=none)은 chat·username 이 없어 여기 걸릴 수 없다 — 따로 거르지 않는다
        relay_drops = [d for d in j["drops_origin"]
                       if d["user"].lower() in relay_names or d["chat"] in relay_ids]
        if relay_drops:
            bad.append(f"봇이 릴레이 포워드 {len(relay_drops)}건을 **출처 게이트**에서 버렸다 — "
                       "예: " + relay_drops[-1]["line"][-240:])
        n_direct = sum(1 for d in j["drops_origin"] if d["type"] == "none")
        n_other = len(j["drops_origin"]) - len(relay_drops) - n_direct
        if n_direct or n_other:
            notes.append(f"출처 게이트가 릴레이 아닌 글을 버렸다(직접 쓴 글 {n_direct} · 다른 "
                         f"곳 포워드 {n_other}) — 릴레이 경로가 아니면 정상이다")

    run = f.get("running")
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
            notes.append(_OLD_BUILD_NOTE)

    ib = f.get("inbox") or {}
    if ib and not ib.get("exists"):
        bad.append(f"inbox 가 없다({ib.get('path')}) — 이 경로로는 한 번도 안 들어왔다")

    wh_r = ((tg.get("webhook") or {}).get("result") or {}) if tg.get("token") else {}
    pend = wh_r.get("pending_update_count")
    if pend:
        notes.append(f"텔레그램에 봇이 아직 안 가져간 업데이트 {pend}건 — 폴링 중이면 곧 "
                     "빠진다(계속 남으면 봇이 못 가져가는 것)")

    if symptom and not bad and unk:
        # 못 잰 조건이 남아 있으면 '다 맞는다' 고 말할 수 없다(#165) — 그것부터.
        unk.append("원인을 짚지 못했다 — 위 ❓ 의 못 잰 조건이 남아 있으니 그것부터 잴 것")
    elif symptom and not bad:
        # 증상은 있는데 위에서 원인을 하나도 못 짚었다 — 남은 갈래를 사실대로(#165).
        if run is not None and not run["drop_log"]:
            unk.append("위 조건은 전부 맞는데 못 받았다 — 실행 중인 봇이 버림을 적지 않는 옛 "
                       "판이라 '받고 버렸나 / 아예 안 왔나' 를 저널이 말하지 않는다. 이 수정을 "
                       "배포한 뒤(봇 재시작) 다시 포워드하면 버림 줄이나 수신 줄이 답한다")
            notes.remove(_OLD_BUILD_NOTE)     # 같은 사실을 두 줄로 말하지 않는다(#395)
        else:
            unk.append("위 조건은 전부 맞고 버림 기록도 없는데 받은 기록이 없다 — 텔레그램이 "
                       "전달하지 않았다는 뜻이다(드묾). 봇 재시작 뒤 다시 포워드해 볼 것")
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
            "inbox": str(data_dir / "inbox.jsonl"),
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
    try:
        for i, ln in enumerate(p.stdout):
            if "trade-bot starting" in ln:
                found = parse_start_line(ln.rstrip("\n"))
                break
            if i >= max_lines:
                break
    finally:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:                                      # noqa: BLE001
            pass
    return found, ("" if found else f"PID {pid} 의 앞 {max_lines}줄에 시작 줄이 없다"
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
        out.append({"pid": int(d.name), "cmd": " ".join(args)[:160]})
    return out


def _is_bot_cmd(args: list[str]) -> bool:
    """`-m trade.bot` 또는 `…/trade/bot.py` — `trade.bot_health` 같은 형제는 아니다."""
    for i, a in enumerate(args):
        if a == "-m" and i + 1 < len(args) and args[i + 1] == "trade.bot":
            return True
        if a.endswith("trade/bot.py"):
            return True
    return False


def _forwards(read, since: str) -> tuple[list[dict], str]:
    """릴레이 유닛 저널 → (포워드 사건, 못 읽었으면 사유)."""
    lines, err, kind = read(RELAY_UNITS, since)
    return relay_forwards(lines), ("" if _readable(kind) else err or kind)


def delivery_check(since: str, *, now: datetime | None = None,
                   read=read_journal) -> dict:
    """릴레이 포워드 vs 봇 수신 — 저널만 읽는다(텔레그램·파일 안 건드림).

    `trade.scripts.health_check` 가 매시간 부른다. 저널을 못 읽으면 `kind=
    "unknown"` 과 사유 — 판정 불가를 '이상 없음' 으로 접지 않는다(#54).
    """
    now = now or datetime.now(timezone.utc)
    fwd, ferr = _forwards(read, since)
    if ferr:
        return {"kind": "unknown", "sent": 0, "got": 0, "undated": 0,
                "err": f"릴레이 저널을 못 읽었다 — {ferr}"}
    if not fwd:
        return {"kind": "none", "sent": 0, "got": 0, "undated": 0, "err": ""}
    bl, berr, bkind = read((_SERVICE,), since)
    if not _readable(bkind):
        return {"kind": "unknown", "sent": sum(x["n"] for x in fwd), "got": 0,
                "undated": 0, "err": f"trade-bot 저널을 못 읽었다 — {berr or bkind}"}
    g = delivery_gap(fwd, journal_facts(bl)["ingested"], now)
    g["err"] = ""
    return g


def gap_alert_text(g: dict) -> str:
    """`delivery_check` 결과 → 텔레그램 알림(HTML). 토큰·원문 없음."""
    what = ("<b>한 건도 못 받았습니다</b>" if g["kind"] == "total"
            else f"{g['got']}건만 받았습니다")
    return ("⚠️ <b>trade-bot 수신 누락</b>\n"
            f"릴레이가 {g['sent']}건을 비공개 채널로 포워드했는데(첫 {_kst(g['first'])} · "
            f"{', '.join(g.get('who') or [])}) 봇은 {what}.\n"
            "inbox·대시보드에 안 들어갑니다. 원인 가르기(읽기 전용):\n"
            f"<code>{USAGE}</code>")


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
    lines, err, kind = read((_SERVICE,), since)
    # 읽히는데 창 안에 0줄이면 '못 읽음' 이 아니라 **아무것도 안 찍었다** 는 사실이다
    # — 빈 사실로 판정을 태운다(살아 있다면 폴링 0회 = 문제, #52).
    f["journal"] = journal_facts(lines) if _readable(kind) else None
    f["journal_err"] = err
    main_pid = facts.get("s_MainPID")
    main_pid = int(main_pid) if str(main_pid or "").isdigit() and int(main_pid) > 0 else None
    run = None
    if f["journal"]:
        mine = [s for s in f["journal"]["starts"] if main_pid and s["pid"] == main_pid]
        run = mine[-1] if mine else None
    f["running_err"] = ""
    if run is None and main_pid:
        run, f["running_err"] = start_fn(main_pid)
    elif run is None:
        f["running_err"] = "실행 중인 프로세스(MainPID)가 없다"
    f["running"] = run
    fwd, ferr = _forwards(read, since)
    f["forwards"] = fwd
    if ferr:
        f["gap"] = {"kind": "unknown", "err": f"릴레이 저널을 못 읽었다 — {ferr}", "undated": 0}
    elif f["journal"] is None:
        f["gap"] = ({"kind": "unknown", "undated": 0,
                     "err": f"trade-bot 저널을 못 읽었다 — {err}"} if fwd else {"kind": "none"})
    else:
        f["gap"] = delivery_gap(fwd, f["journal"]["ingested"], now)
    f["inbox"] = inbox_facts(Path(env.get("inbox") or ""), now) if env.get("inbox") else {}
    f["tg"] = tg_fn(env.get("token") or "", env.get("dest"), sorted(f["relays"]))
    f["others"] = procs_fn({os.getpid()} | ({main_pid} if main_pid else set()))
    return f


def render(f: dict, since: str) -> list[str]:
    """사실 → 사람이 읽는 줄(판정 제외). 토큰·원문 비밀값 없음."""
    now = f["now"]
    env = f.get("env") or {}
    out = [f"# trade.bot_health v{_VER} · 코드 지문 {_sig()} · 인터프리터 {sys.executable}",
           "# 읽기 전용 — 텔레그램엔 getMe·getWebhookInfo·getChatMember 만 묻는다"
           "(getUpdates 는 안 부른다). 토큰은 어디에도 찍지 않는다.",
           f"# 창 --since {since!r} · 설정 출처 {env.get('src')}"
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
            parts.append(f"수신 종류 {', '.join(au)}" if isinstance(au, list) and au
                         else "수신 종류 저장값 없음(텔레그램 기본값)")
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
                   f"{_fmt(run['origin'])} · 버림 기록 {'켜짐' if run['drop_log'] else '없음(옛 판)'}")
    out.append(f"   릴레이 목적지 {env.get('dest')} · 원천 "
               + ", ".join(f"{u}({'/'.join(s)})" for u, s in sorted((f.get('relays') or {}).items())))

    j = f.get("journal")
    if j is None:
        out.append(f"④ 저널: 못 읽음 — {f.get('journal_err')}")
    else:
        sp = j["span"]
        polls = " · ".join(f"{k}×{v}" for k, v in sorted(j["polls"].items())) or "없음"
        ing = [t for t in j["ingested"] if t is not None]
        last_ok = (f"{_age((now - j['last_ok']).total_seconds())} 전" if j["last_ok"] else "없음")
        out.append(f"④ 저널 {j['n']}줄({sp.get('first')}~{sp.get('last')}): 폴링 {polls} · "
                   f"마지막 정상 폴링 {last_ok} · 수신(ingested) {len(j['ingested'])}건"
                   + (f"(마지막 {_kst(max(ing))})" if ing else "")
                   + f" · 버림 채널 {len(j['drops_channel'])}·출처 {len(j['drops_origin'])} · "
                   f"시작 {len(j['starts'])}회")
        for ln in j["errors"][-3:]:
            out.append(f"   오류 표본: {ln[-220:]}")
    fwd = f.get("forwards") or []
    gap = f.get("gap") or {}
    if fwd:
        by: Counter = Counter()
        for x in fwd:
            by[x["who"]] += x["n"]
        out.append(f"⑤ 릴레이 포워드 {sum(by.values())}건("
                   + ", ".join(f"{k} {v}" for k, v in sorted(by.items()))
                   + f") · 마지막 {_kst(max((x['ts'] for x in fwd if x['ts']), default=None))}"
                   + (f" · 대조: 보냄 {gap.get('sent')} / 받음 {gap.get('got')}"
                      if gap.get("kind") not in (None, "none", "unknown") else ""))
    else:
        out.append("⑤ 릴레이 포워드: 창 안에 없음(systemd 유닛 실행만 센다 — 손으로 돌린 백필은 안 잡힌다)")

    ib = f.get("inbox") or {}
    if ib.get("exists"):
        last = ib.get("last")
        src = sorted(((u or "(출처 없음)", e) for u, e in (ib.get("by") or {}).items()),
                     key=lambda x: x[1]["last"] or datetime.min.replace(tzinfo=timezone.utc),
                     reverse=True)[:5]
        out.append(f"⑥ inbox {ib['path']} · {ib['lines']}줄 · 마지막 기록 "
                   f"{_kst(last[0]) if last else '—'}({last[1] or '출처 없음' if last else ''})")
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
                    help='journalctl --since 값 (기본: 24시간 전, 예: "2026-09-25 07:40")')
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
