"""Bulk-ingest the inbox.jsonl into store.db.

Reads every line in ~/.trade/inbox.jsonl, groups Telegram messages by
media_group_id (so a BeOn album of one caption + N photos becomes one
alert row carrying all photo paths), parses each captioned member with
trade.parser, and upserts to store.db.

Idempotent. Reruns skip already-stored alerts (UNIQUE on
source_chat_id + source_message_id), so it's safe to invoke after each
new burst of forwards or after improving the parser — old work isn't
redone but newly-eligible captions land.

Usage:
    .venv/bin/python -m trade.scripts.ingest_inbox
    .venv/bin/python -m trade.scripts.ingest_inbox --verbose
    .venv/bin/python -m trade.scripts.ingest_inbox --inbox PATH --db PATH
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import badonion_sources as _srcs
from trade import ignored as _ignored
from trade import jp_exports as _jp
from trade.parser import parse_caption
from trade.store import (
    alert_to_row,
    open_db,
    stats,
    update_media_paths,
    upsert_alert,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ingest")

DATA_DIR = Path(
    os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade")
)
DEFAULT_INBOX = DATA_DIR / "inbox.jsonl"
DEFAULT_MEDIA_ROOT = DATA_DIR / "media"
DEFAULT_DB = DATA_DIR / "store.db"


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("inbox line %d: malformed JSON (%s)", n, e)
    return rows


def _resolve_photo_path(media_root: Path, row: dict) -> str | None:
    """media/<YYYY-MM-DD>/<file_unique_id>.jpg if the file exists.

    The date partition comes from the row's `date` field (Telegram
    receive time, matching what bot.py writes), so we stay consistent
    even if the operator changed MEDIA_ROOT layout.
    """
    uid = row.get("photo_file_unique_id")
    if not uid:
        return None
    date_iso = row.get("date") or ""
    day = date_iso.split("T", 1)[0] if "T" in date_iso else date_iso[:10]
    if not day:
        return None
    full = media_root / day / f"{uid}.jpg"
    if not full.exists():
        return None
    # Store relative to ~/.trade for portability (so moving the data
    # dir doesn't invalidate every row).
    return str(full.relative_to(media_root.parent))


def _stored_media_empty(conn, chat_id: int, msg_id: int) -> bool:
    """True when the already-stored alert has no media_paths yet — the
    signal that its photo finished downloading AFTER ingest first ran
    (the backfill/burst race), so a re-link is warranted. Missing row →
    False (nothing to heal; the upsert will insert it instead)."""
    cur = conn.execute(
        "SELECT media_paths FROM alerts "
        "WHERE source_chat_id=? AND source_message_id=?",
        (chat_id, msg_id),
    )
    r = cur.fetchone()
    if r is None:
        return False
    try:
        return not json.loads(r[0] or "[]")
    except Exception:
        return False


def _group_messages(rows: list[dict]) -> list[list[dict]]:
    """Group rows by media_group_id. Solo rows (None) each become a
    one-element group. Preserves the original arrival order so the
    'first captioned row' rule maps to the album's primary message.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    solos: list[list[dict]] = []
    for row in rows:
        gid = row.get("media_group_id")
        if gid:
            groups[gid].append(row)
        else:
            solos.append([row])
    return list(groups.values()) + solos


_UNPARSED_SHOW_CAP = 50   # --show-unparsed 가 찍는 최대 줄 수(자르면 고지)


def _row_head(row: dict, width: int = 160) -> str:
    """(시각 · 메시지id · 캡션 머리) 한 줄 — 진단 출력용(순수).

    ⚠️ 형제(`backfill_badonion._unit_head`)와 **같은 시각 포맷**으로 찍는다 —
    한쪽은 채널에서 드랍된 글을, 이쪽은 inbox 에 들어왔는데 어느 파서도
    안 받은 글을 보여 주므로, 둘을 나란히 놓아야 "채널에 없나 / 받아는
    왔는데 못 읽나" 가 갈린다(#82·#143). 입력 타입이 달라(Telethon
    Message vs inbox jsonl dict) 함수를 공유하지는 못한다.
    ⚠️ v1 은 raw ISO(`2026-09-01T00:00:00+00:00`)를 찍어 형제의
    `2026-09-01 00:00 UTC` 와 **눈으로 대조가 안 됐다** — docstring 이 내건
    목적이 거짓이 된다(독립 리뷰 2026-09-17 M3 · #55). msg id 는 우리만
    가진 축이라 남긴다(`/ignore <id>` 가 그걸 받는다).
    """
    cap = row.get("caption") or row.get("text") or ""
    head = " ".join(str(cap).split())[:width] or "(캡션 없음)"
    raw = str(row.get("forward_origin_date") or row.get("date") or "?")
    try:
        when = datetime.fromisoformat(raw).astimezone(
            timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        when = raw   # 파싱 못 하면 원문 — 지어내지 않는다(#165)
    return f"{when} · msg {row.get('message_id')} · {head}"


def _ingest_group(
    conn,
    group: list[dict],
    media_root: Path,
    counters: dict,
    ignored_ids: set[int],
    jp_conn=None,
    badonion_conns: dict | None = None,
    unparsed: list | None = None,
) -> None:
    """Resolve one album/solo into a single alert row + media paths."""
    captioned = [r for r in group if r.get("caption_present")]
    if not captioned:
        # Photo-only album with no caption — typical when the captioned
        # sibling hasn't arrived yet at scan time. Skip; the next run
        # picks it up.
        counters["skipped_no_caption"] += 1
        return
    if len(captioned) > 1:
        counters["multi_caption_album"] += 1

    primary = captioned[0]
    # Operator-curated ignore list — promo posts ([비온 인사이트] etc.)
    # that aren't export/import alerts. Never reach store.db, never
    # surface in the daily integrity check.
    if primary.get("message_id") in ignored_ids:
        counters["skipped_ignored"] += 1
        return
    caption_text = primary.get("caption") or primary.get("text") or ""
    # Recurring promo prefixes (currently '[비온 인사이트]') — silently
    # dropped without needing per-msg_id /ignore. Same downstream effect
    # as the msg_id list but covers the whole series at once.
    if _ignored.matches_prefix(caption_text):
        counters["skipped_prefix"] += 1
        return
    # Body-marker filter (currently 'dart.fss.or.kr' for DART 공시 릴레이
    # — line 1 ticker varies per post so prefix matching can't catch it,
    # but the DART link in the body is a reliable marker).
    if _ignored.matches_contains(caption_text):
        counters["skipped_contains"] += 1
        return
    parsed = parse_caption(caption_text)
    if parsed is None:
        # 일본 수출 데이터(BeOn) — 한국 포맷 아님. JP 파서로 폴백 → 별도 jp.db.
        if jp_conn is not None and _jp.parse_jp_export(caption_text) is not None:
            jp_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    jp_media.append(p)
            stored = _jp.ingest(
                jp_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=jp_media)
            counters["jp_inserted"] = counters.get("jp_inserted", 0) + (1 if stored else 0)
            return
        # 나쁜양파 소스 — 한국 store.db·일본 BeOn 둘 다 아니면 여기로 온다.
        # 각 소스는 자기 DB 로 분리 저장(스키마가 서로 다름). 소스 목록은
        # badonion_sources 레지스트리(여기 나열하면 드리프트 표면이 된다).
        #
        # ⚠️ **순서가 계약이다** — 순차 fallback 이라 앞선 파서가 먼저
        # 캡션을 가져간다. 순서는 `badonion_sources.SOURCES` 하나가 정하고
        # 테스트가 pin 한다. 옛 코드는 이 블록이 소스마다 18줄씩 복붙된
        # 146줄이었고, 같은 목록이 5개 파일에 흩어져 실제로 드리프트했다
        # (2026-08-16 한국 추가 시 로그 문구 누락).
        for _src in _srcs.SOURCES:
            _conn = (badonion_conns or {}).get(_src.key)
            if _conn is None or _src.parse(caption_text) is None:
                continue
            _media = []
            for r in group:
                _p = _resolve_photo_path(media_root, r)
                if _p:
                    _media.append(_p)
            stored = _src.ingest(
                _conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=_media)
            _ck = f"{_src.key}_inserted"
            counters[_ck] = counters.get(_ck, 0) + (1 if stored else 0)
            return
        counters["unparseable"] += 1
        # ⚠️ 숫자만 세는 원장은 다음 라운드를 엉뚱한 데로 보낸다(#82·#332).
        # '어느 소스도 안 받은 캡션' 이야말로 새 형식이 조용히 유실되는
        # 자리이고(#83·#261·#330·#332·#370 — 이 레포에서 일곱 번), 그걸
        # 보려면 사람이 채널을 다시 열어야 했다. 머리만 모아 둔다.
        if unparsed is not None:
            unparsed.append(_row_head(primary))
        return

    media_paths = []
    for r in group:
        p = _resolve_photo_path(media_root, r)
        if p:
            media_paths.append(p)

    row = alert_to_row(
        parsed,
        source_chat_id=primary["chat_id"],
        source_message_id=primary["message_id"],
        media_group_id=primary.get("media_group_id"),
        ingested_at=primary.get("ingested_at")
        or datetime.now(timezone.utc).isoformat(),
        posted_at=primary.get("forward_origin_date") or primary.get("date") or "",
        raw_text=caption_text,
        media_paths=media_paths,
    )
    inserted = upsert_alert(conn, row)
    if inserted:
        counters["inserted"] += 1
        if parsed.parse_warnings:
            counters["with_warnings"] += 1
    else:
        counters["already_present"] += 1
        # Self-heal the download↔ingest race. upsert is ON CONFLICT DO
        # NOTHING, so a backfilled/burst alert that was ingested BEFORE
        # its own photo finished the background download stays imageless
        # forever — the file lands seconds later but the row is never
        # revisited. When we now resolve media (file on disk) and the
        # stored row still has none, re-link it so the next 5-min refresh
        # cycle surfaces the image. (Album siblings already use
        # update_media_paths in the live path; this covers the whole-
        # alert race the BeOn backfill exposes — 732 imageless cards
        # 2026-06-15.) Gated on stored-empty so healed rows aren't
        # rewritten every cycle.
        if media_paths and _stored_media_empty(
            conn, primary["chat_id"], primary["message_id"]
        ):
            update_media_paths(
                conn, primary["chat_id"], primary["message_id"], media_paths
            )
            counters["media_relinked"] += 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bulk-ingest inbox.jsonl → store.db (idempotent)."
    )
    ap.add_argument(
        "--inbox", type=Path, default=DEFAULT_INBOX,
        help=f"jsonl path (default: {DEFAULT_INBOX})",
    )
    ap.add_argument(
        "--media-root", type=Path, default=DEFAULT_MEDIA_ROOT,
        help=f"media dir (default: {DEFAULT_MEDIA_ROOT})",
    )
    ap.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"SQLite path (default: {DEFAULT_DB})",
    )
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--show-unparsed", action="store_true",
        help=("어느 소스도 받지 않은 캡션의 머리(160자)를 찍는다 — "
              "새 카드 형식이 파서 없이 버려지고 있는지 보는 용도. "
              "이 플래그 자체는 아무것도 더 쓰지 않지만 ingest_inbox 는 "
              "적재 명령이다(형제 `backfill_badonion --show-irrelevant` "
              "처럼 읽기 전용이 아니다). 못 보는 축: 형제 파서가 먼저 "
              "가져간 캡션과 ingest 가 조용히 stored=False 로 끝난 건은 "
              "여기 안 잡힌다(#274)."),
    )
    args = ap.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    rows = _read_jsonl(args.inbox)
    log.info("inbox: %d rows from %s", len(rows), args.inbox)
    if not rows:
        log.warning("nothing to ingest; exiting")
        return 0

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(args.db)
    log.info("store: %s (existing alerts: %d)", args.db, stats(conn)["total"])
    # 일본 수출(BeOn) 폴백 저장소 — 한국 store.db 와 분리(무영향).
    jp_conn = _jp.open_jp_db(args.db.parent / "jp.db")
    # 나쁜양파 소스별 폴백 저장소 — 스키마가 서로 달라 파일을 분리한다
    # (한국 store.db·일본 jp.db 와도 무관, 상호 무영향). 목록은
    # badonion_sources 단일 레지스트리 → 소스 추가 시 여기는 안 건드린다.
    badonion_conns = {
        s.key: s.open_db(args.db.parent / s.db_file) for s in _srcs.SOURCES
    }

    groups = _group_messages(rows)
    log.info("grouped into %d send units", len(groups))

    ignored_ids = _ignored.load()
    log.info("operator ignore list: %d entries", len(ignored_ids))

    counters = {
        "inserted": 0,
        "already_present": 0,
        "skipped_no_caption": 0,
        "skipped_ignored": 0,
        "skipped_prefix": 0,
        "skipped_contains": 0,
        "unparseable": 0,
        "jp_inserted": 0,
        **{f"{s.key}_inserted": 0 for s in _srcs.SOURCES},
        "multi_caption_album": 0,
        "with_warnings": 0,
        "media_relinked": 0,
    }
    unparsed: list = [] if args.show_unparsed else None
    for grp in groups:
        _ingest_group(conn, grp, args.media_root, counters, ignored_ids,
                      jp_conn, badonion_conns, unparsed=unparsed)

    log.info("ingest counters: %s", counters)
    if args.show_unparsed:
        # 0건도 말한다 — 빈 출력이 정답인 도구는 없다(#274).
        # ⚠️ 상한을 두되 **자른 사실을 고지**한다 — 조용한 절단은 '전부 봤다'
        # 로 읽힌다(#45·#264 "매치 N건 중 최신 M건"). inbox.jsonl 은 로테이션이
        # 없어 누적 미파싱이 수천 건일 수 있다.
        log.info("어느 소스도 받지 않은 캡션: %d건%s", len(unparsed),
                 f" (아래는 최신 {_UNPARSED_SHOW_CAP}건)"
                 if len(unparsed) > _UNPARSED_SHOW_CAP else "")
        for line in unparsed[-_UNPARSED_SHOW_CAP:]:
            log.info("unparsed unit %s", line)
    s = stats(conn)
    log.info("store stats: %s", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
