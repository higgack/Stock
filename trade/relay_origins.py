"""릴레이가 보증한 재게시 글 — 봇의 출처 게이트가 읽는다 (실수 #411, 2026-09-25).

⚠️ 왜 있나. 나쁜양파는 다른 채널의 글을 **재게시**하고, 텔레그램은 포워드의
포워드에도 **원래 출처**를 단다. 그래서 릴레이가 관련 글로 골라 비공개 채널에
포워드한 27건을 봇의 출처 게이트(`TRADE_SOURCE_ORIGIN` — 원천 사용자명 대조)가
'다른 출처' 로 버렸다. VM `trade.bot_health` 실측: 수신 0 · 출처 게이트 버림 27
(전부 원래 출처 `-1003901069327`). kri(한국 수입 회사별) 보드가 '0개 회사' 였던
것(실수 #373c)도 이 갈래였다 — 채널엔 글이 있었고 릴레이도 포워드했는데 봇이
버렸다.

⚠️ 왜 그 채널을 출처 목록(.env)에 더하지 않나. 나쁜양파가 다음에 또 다른 채널을
재게시하면 같은 유실이 반복된다 — 사람이 매번 알아채고 .env 를 고쳐야 한다(#83·
#261·#330 계열 — 새 모양은 늘 조용히 버려진 쪽에 숨는다). 그리고 채널을 통째로
허용하면 BeOn 릴레이가 **같은 채널**의 무관 글을 되포워드할 때 그것까지 받는다 —
BeOn 릴레이는 채널 전체를 되포워드하고(관련성 필터 없음) 그 되포워드를 버리는 것이
이 게이트의 원래 일이다.

그래서 보증은 **글 단위**다. 나쁜양파 릴레이(백필·리스너)는 관련성 필터를 통과한
유닛을 포워드하기 **직전에** 그 유닛 중 재게시 글의 (원래 채널, 원래 글 번호) 를
여기 적고, 봇은 출처 목록에 없는 포워드라도 그 짝이 여기 있으면 받는다. 기록이
포워드보다 먼저라 봇이 받을 때는 이미 있다. 기록을 못 하면 릴레이는 그 유닛을
**포워드하지 않는다** — 포워드하면 봇이 버리고, inbox 에 없으니 다음 동기화가 같은
글을 또 포워드해 또 버린다. BeOn 릴레이는 보증하지 않는다(그 되포워드는 지금처럼
버려진다).

파일: `<TRADE_DATA_DIR 또는 ~/.trade>/relay_origins.json` — 봇과 두 릴레이가 같은
사용자·같은 데이터 디렉터리를 쓴다(deploy/ 유닛 전부 `User=higgack`, 경로는 같은
`TRADE_DATA_DIR` 규약). 쓰기는 파일 락 + 원자적 교체다 — 리스너와 6시간 백필이
겹쳐 쓸 수 있고, 봇은 쓰다 만 파일을 읽으면 안 된다(#379 · #280). 읽기는 **어떤
바이트가 와도 안 던진다**(#331) — 봇 게이트가 부른다. 오래된 보증은 쓸 때
걷어낸다(`KEEP_DAYS`) — 텔레그램은 봇이 못 가져간 업데이트를 24시간만 보관하므로
그보다 한참 뒤의 보증은 진단(`trade.bot_health`)의 창 말고는 쓸 데가 없다.

⚠️ 못 보는 축(#274):
  · 원래 출처가 **개인 계정**(사람이 쓴 글의 포워드)이거나 원문이 출처를 숨기면
    채널 글 번호가 없어 봇이 받는 모양(`forward_origin.chat` + `message_id`)과
    짝을 지을 수 없다 — 보증하지 못하고 릴레이가 그 수를 로그로 센다. 그 글은
    봇이 계속 버리고 `bot_health` 가 '다른 출처 포워드' 로 말한다.
  · 봇과 릴레이가 **다른 호스트**로 갈리면 봇은 이 파일을 못 본다 — 그때 재게시
    글은 다시 버려진다(`bot_health` 가 보증된 글의 버림을 ❌ 로 말한다).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FILE_NAME = "relay_origins.json"
# 보증을 얼마나 남기나 — 봇의 수신(24시간 보관)보다 한참 길게, 진단 창(기본 24시간 ·
# 사람이 넓힐 수 있다)을 덮게. 파일 크기는 관련 재게시 글 수에 비례해 작다.
KEEP_DAYS = 60
_VER = 1
_TITLE_MAX = 80


def default_dir() -> Path:
    """**호출 시점**의 데이터 디렉터리 — `TRADE_DATA_DIR` 또는 `~/.trade`.

    import 시점에 굳히지 않는다 — 굳히면 환경을 바꾼 테스트·호출이 옛 경로(운영
    파일)를 연다(`trade.ignored` 가 실제로 그랬다, #373)."""
    return Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))


def path_in(data_dir) -> Path:
    """데이터 디렉터리 → 보증 파일. 봇(`INBOX_DIR`)·릴레이·진단이 같은 규약으로 찾는다."""
    return Path(data_dir) / FILE_NAME


def _parse_at(raw) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None      # 시간대 없는 시각은 가정하지 않는다(#165)


def load(path) -> tuple[dict, str]:
    """보증 기록 → ({(원래 채널, 원래 글번호): {"at", "by", "title"}}, 사유).

    **예외를 올리지 않는다**(#331) — 봇 게이트가 매번 부른다. 파일이 없으면 ({}, "")
    — 아직 재게시 글을 포워드한 적 없는 정상 상태다. 통째로 못 읽으면 ({}, 사유),
    일부 항목만 못 읽으면 나머지는 살리고 사유에 그 수를 적는다. 사유를 삼키지 않고
    돌려준다 — 부르는 쪽이 남긴다(#12). `at` 은 처음 보증한 시각(UTC, 못 읽으면 None).
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return {}, f"읽기 실패({type(exc).__name__}: {exc})"[:200]
    if not raw.strip():
        return {}, "빈 파일"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {}, f"형식 오류({type(exc).__name__})"
    if not isinstance(doc, dict) or not isinstance(doc.get("chats"), dict):
        return {}, "형식 오류(chats 가 없다)"
    if doc.get("v") != _VER:
        return {}, f"모르는 판(v={doc.get('v')!r}, 이 코드는 v={_VER})"
    out: dict = {}
    bad = 0
    for ck, cv in doc["chats"].items():
        if not isinstance(cv, dict) or not isinstance(cv.get("posts"), dict):
            bad += 1
            continue
        title = cv.get("title") if isinstance(cv.get("title"), str) else None
        for mk, mv in cv["posts"].items():
            try:
                key = (int(ck), int(mk))
            except (TypeError, ValueError):
                bad += 1
                continue
            mv = mv if isinstance(mv, dict) else {}
            out[key] = {"at": _parse_at(mv.get("at")), "by": str(mv.get("by") or ""),
                        "title": title}
    return out, (f"항목 {bad}개를 못 읽었다(나머지 {len(out)}건은 읽었다)" if bad else "")


def is_vouched(pairs: dict, chat_id, msg_id) -> bool:
    """이 (원래 채널, 원래 글번호) 가 보증돼 있나. 번호가 아니면 False(지어내지 않는다)."""
    try:
        return (int(chat_id), int(msg_id)) in (pairs or {})
    except (TypeError, ValueError):
        return False


def _dump(pairs: dict) -> dict:
    chats: dict = {}
    for (cid, mid), v in sorted(pairs.items()):
        c = chats.setdefault(str(cid), {"title": None, "posts": {}})
        if v.get("title"):
            c["title"] = v["title"]
        at = v.get("at")
        c["posts"][str(mid)] = {"at": at.isoformat() if at else None, "by": v.get("by") or ""}
    return {"v": _VER, "chats": chats}


def _write(p: Path, doc: dict) -> None:
    """원자적 교체 — 봇이 **찢어진 JSON 이나 길이 0** 을 보지 않게(#280·#379). 임시
    파일 이름에 PID 를 붙인다(리스너와 백필이 서로의 임시 파일을 덮지 않게)."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


@contextmanager
def _locked(p: Path):
    """프로세스 간 배타 락 — 리스너와 백필이 동시에 더해도 한쪽 보증을 잃지 않게.

    락을 못 걸면(플랫폼·권한) **그냥 진행한다** — 잠금 실패로 포워드를 멈추는 것보다
    드물게 한쪽 보증을 잃는 편이 낫다(그 글은 봇이 버리고 다음 동기화가 다시 보증해
    포워드한다). 대신 그 사실을 로그로 남긴다(#42a 폴백은 조용하면 안 된다)."""
    f = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        f = open(p.with_name(p.name + ".lock"), "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("relay_origins: 파일 락 없이 진행합니다(%s: %s)", type(exc).__name__, exc)
        if f is not None:
            f.close()
            f = None
    try:
        yield
    finally:
        if f is not None:
            try:
                import fcntl as _fc
                _fc.flock(f.fileno(), _fc.LOCK_UN)
            finally:
                f.close()


def vouch(pairs: dict, *, by: str, path, now: datetime | None = None) -> int:
    """{(원래 채널, 원래 글번호): 채널 제목|None} 을 보증 기록에 더한다 → 새로 더한 수.

    ⚠️ 포워드 **전에** 부를 것 — 봇은 받는 순간 이 파일을 본다. 쓰기에 실패하면
    **던진다** — 부르는 쪽은 그 유닛을 포워드하지 않는다(포워드하면 봇이 버리고
    다음 동기화가 또 포워드한다). 이미 있는 짝은 처음 보증 시각을 지킨다(진단이
    '보증 **뒤의** 버림' 을 가른다). 바뀐 것이 없으면 쓰지 않는다.
    """
    if not pairs:
        return 0
    now = now or datetime.now(timezone.utc)
    p = Path(path)
    with _locked(p):
        cur, err = load(p)
        if err:
            # 못 읽은 기록은 새로 쓴다 — 보증은 봇이 받을 때까지(초~분)만 필요하고, 막으면
            # 이 포워드가 막힌다. 잃는 것은 진단의 옛 보증뿐이라 그 사실을 남긴다(#43).
            log.warning("relay_origins: %s 를 못 읽어(%s) 읽은 만큼만 두고 새로 쓴다", p, err)
        cutoff = now - timedelta(days=KEEP_DAYS)
        kept = {k: v for k, v in cur.items() if v.get("at") is None or v["at"] >= cutoff}
        changed = len(kept) != len(cur) or bool(err)
        # 제목은 **채널 단위**로 저장된다(`_dump`) — 새 제목이 오면 그 채널 전체가 따른다.
        titles = {k[0]: v["title"] for k, v in kept.items() if v.get("title")}
        added = 0
        for key, title in pairs.items():
            cid, mid = int(key[0]), int(key[1])
            t = (str(title).strip()[:_TITLE_MAX] or None) if title else None
            if t and titles.get(cid) != t:
                titles[cid] = t
                changed = True
            if (cid, mid) not in kept:
                kept[(cid, mid)] = {"at": now, "by": by, "title": None}
                added += 1
                changed = True
        if changed:
            for k, v in kept.items():
                v["title"] = titles.get(k[0])
            _write(p, _dump(kept))
    return added


def _title_of(msg) -> str | None:
    """텔레톤 메시지 → 원래 채널 제목(최선) — 응답에 그 채널 엔티티가 실려 왔을 때만."""
    try:
        chat = getattr(getattr(msg, "forward", None), "chat", None)
        title = getattr(chat, "title", None)
    except Exception:                                          # noqa: BLE001
        return None
    return title if isinstance(title, str) and title.strip() else None


def repost_pairs(messages, get_peer_id) -> tuple[dict, int]:
    """릴레이 유닛(텔레톤 메시지들) → ({(원래 채널, 원래 글번호): 제목|None}, 보증 못 한 포워드 수).

    원천 채널 자신의 글(포워드가 아님)은 보증할 것이 없다 — 봇이 사용자명으로 받는다.
    재게시는 `fwd_from.from_id` 가 **채널**이고 `channel_post` 가 있을 때만 보증한다 —
    봇이 받는 모양(`forward_origin.chat.id` + `forward_origin.message_id`)과 짝이 맞는
    것이 그것뿐이다. 개인 계정 글의 포워드·출처를 숨긴 포워드는 세기만 한다(모듈
    독스트링 '못 보는 축').

    `get_peer_id` 는 부르는 쪽의 텔레톤 함수 — 백필의 중복 제거 키(`_msg_key_with_origin`)
    와 **같은 함수**라야 봇이 받는 ID(-100…)와 맞는다(#38).
    """
    pairs: dict = {}
    blind = 0
    for m in messages or ():
        fwd = getattr(m, "fwd_from", None)
        if fwd is None:
            continue
        peer = getattr(fwd, "from_id", None)
        post = getattr(fwd, "channel_post", None)
        if peer is None or getattr(peer, "channel_id", None) is None or post is None:
            blind += 1
            continue
        try:
            key = (int(get_peer_id(peer)), int(post))
        except (TypeError, ValueError):
            blind += 1
            continue
        pairs[key] = _title_of(m) or pairs.get(key)
    return pairs, blind


def describe(pairs: dict, limit: int = 3) -> str:
    """보증할 짝들 → 로그 한 줄 조각(`-100…(제목) 글 N건`). 채널을 자르면 자른 수를 말한다(#45)."""
    by_chat: dict = {}
    for (cid, _mid), title in (pairs or {}).items():
        e = by_chat.setdefault(cid, {"n": 0, "title": None})
        e["n"] += 1
        e["title"] = e["title"] or title
    parts = [f"{cid}({e['title']!r}) 글 {e['n']}건" if e["title"] else f"{cid} 글 {e['n']}건"
             for cid, e in sorted(by_chat.items())]
    return ", ".join(parts[:limit]) + (f" 외 채널 {len(parts) - limit}개" if len(parts) > limit
                                       else "")
