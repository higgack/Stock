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

from trade import cn_exports as _cn
from trade import ignored as _ignored
from trade import jp2_exports as _jp2
from trade import jp_exports as _jp
from trade import mx_exports as _mx
from trade import my_exports as _my
from trade import ph_exports as _ph
from trade import th_exports as _th
from trade import tw_exports as _tw
from trade import us_imports as _us
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


def _ingest_group(
    conn,
    group: list[dict],
    media_root: Path,
    counters: dict,
    ignored_ids: set[int],
    jp_conn=None,
    tw_conn=None,
    cn_conn=None,
    jp2_conn=None,
    th_conn=None,
    my_conn=None,
    ph_conn=None,
    mx_conn=None,
    us_conn=None,
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
        # 대만 수출 데이터(나쁜양파) — 한국·일본 둘 다 아님. TW 파서로 3차 폴백 →
        # 별도 tw.db(사용자 2026-07-10, jp_exports 와 동일 fallback 패턴).
        if tw_conn is not None and _tw.parse_tw_export(caption_text) is not None:
            tw_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    tw_media.append(p)
            stored = _tw.ingest(
                tw_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=tw_media)
            counters["tw_inserted"] = counters.get("tw_inserted", 0) + (1 if stored else 0)
            return
        # 중국 수출 데이터(나쁜양파) — 한국·일본·대만 전부 아님. CN 파서로 4차 폴백 →
        # 별도 cn.db(사용자 2026-07-11, tw_exports 와 동일 fallback 패턴).
        if cn_conn is not None and _cn.parse_cn_export(caption_text) is not None:
            cn_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    cn_media.append(p)
            stored = _cn.ingest(
                cn_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=cn_media)
            counters["cn_inserted"] = counters.get("cn_inserted", 0) + (1 if stored else 0)
            return
        # 일본 수출 데이터 — 나쁜양파 소스(BeOn 과 별도 두 번째 채널). 한국·
        # JP(BeOn)·대만·중국 전부 아님. JP2 파서로 5차 폴백 → 별도 jp2.db
        # (사용자 2026-07-11, tw_exports 와 동일 fallback 패턴).
        if jp2_conn is not None and _jp2.parse_jp2_export(caption_text) is not None:
            jp2_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    jp2_media.append(p)
            stored = _jp2.ingest(
                jp2_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=jp2_media)
            counters["jp2_inserted"] = counters.get("jp2_inserted", 0) + (1 if stored else 0)
            return
        # 태국 수출 데이터(나쁜양파, 같은 채널) — 위 전부 아님. TH 파서로 6차
        # 폴백 → 별도 th.db(사용자 2026-07-26, tw_exports 와 동일 fallback 패턴).
        if th_conn is not None and _th.parse_th_export(caption_text) is not None:
            th_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    th_media.append(p)
            stored = _th.ingest(
                th_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=th_media)
            counters["th_inserted"] = counters.get("th_inserted", 0) + (1 if stored else 0)
            return
        # 말레이시아 수출 데이터(나쁜양파, 같은 채널) — 위 전부 아님. MY 파서로
        # 7차 폴백 → 별도 my.db(사용자 2026-07-26).
        if my_conn is not None and _my.parse_my_export(caption_text) is not None:
            my_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    my_media.append(p)
            stored = _my.ingest(
                my_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=my_media)
            counters["my_inserted"] = counters.get("my_inserted", 0) + (1 if stored else 0)
            return
        # 필리핀 수출 데이터(나쁜양파, 같은 채널) — 위 전부 아님. PH 파서로
        # 8차 폴백 → 별도 ph.db(사용자 2026-07-26).
        if ph_conn is not None and _ph.parse_ph_export(caption_text) is not None:
            ph_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    ph_media.append(p)
            stored = _ph.ingest(
                ph_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=ph_media)
            counters["ph_inserted"] = counters.get("ph_inserted", 0) + (1 if stored else 0)
            return
        # 멕시코 수출 데이터(나쁜양파, 같은 채널) — 위 전부 아님. MX 파서로
        # 9차 폴백 → 별도 mx.db(사용자 2026-07-26).
        if mx_conn is not None and _mx.parse_mx_export(caption_text) is not None:
            mx_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    mx_media.append(p)
            stored = _mx.ingest(
                mx_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=mx_media)
            counters["mx_inserted"] = counters.get("mx_inserted", 0) + (1 if stored else 0)
            return
        # 미국 수입 데이터(나쁜양파, 같은 채널) — 위 전부 아님. US 파서로
        # 10차 폴백 → 별도 us.db(사용자 2026-08-05).
        if us_conn is not None and _us.parse_us_import(caption_text) is not None:
            us_media = []
            for r in group:
                p = _resolve_photo_path(media_root, r)
                if p:
                    us_media.append(p)
            stored = _us.ingest(
                us_conn, caption_text,
                source_message_id=primary.get("message_id"),
                posted_at=primary.get("forward_origin_date")
                or primary.get("date") or "",
                media_paths=us_media)
            counters["us_inserted"] = counters.get("us_inserted", 0) + (1 if stored else 0)
            return
        counters["unparseable"] += 1
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
    # 대만 수출(나쁜양파) 폴백 저장소 — 한국 store.db·일본 jp.db 와 분리(무영향).
    tw_conn = _tw.open_tw_db(args.db.parent / "tw.db")
    # 중국 수출(나쁜양파) 폴백 저장소 — 나머지 전부와 분리(무영향).
    cn_conn = _cn.open_cn_db(args.db.parent / "cn.db")
    # 일본 수출 나쁜양파 소스(BeOn 과 별도 두 번째 채널) 폴백 저장소 — 나머지
    # 전부와 분리(무영향).
    jp2_conn = _jp2.open_jp2_db(args.db.parent / "jp2.db")
    # 태국·말레이시아·필리핀·멕시코 수출(나쁜양파, 같은 채널) 폴백 저장소
    # (사용자 2026-07-26) — 나머지 전부와 분리(무영향).
    th_conn = _th.open_th_db(args.db.parent / "th.db")
    my_conn = _my.open_my_db(args.db.parent / "my.db")
    ph_conn = _ph.open_ph_db(args.db.parent / "ph.db")
    mx_conn = _mx.open_mx_db(args.db.parent / "mx.db")
    us_conn = _us.open_us_db(args.db.parent / "us.db")

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
        "tw_inserted": 0,
        "cn_inserted": 0,
        "jp2_inserted": 0,
        "th_inserted": 0,
        "my_inserted": 0,
        "ph_inserted": 0,
        "mx_inserted": 0,
        "us_inserted": 0,
        "multi_caption_album": 0,
        "with_warnings": 0,
        "media_relinked": 0,
    }
    for grp in groups:
        _ingest_group(conn, grp, args.media_root, counters, ignored_ids,
                      jp_conn, tw_conn, cn_conn, jp2_conn,
                      th_conn, my_conn, ph_conn, mx_conn, us_conn)

    log.info("ingest counters: %s", counters)
    s = stats(conn)
    log.info("store stats: %s", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
