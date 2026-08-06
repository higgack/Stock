"""Daily 'no alert lost' integrity check.

Schedule: 00:00 KST (15:00 UTC the previous day). Cross-references
every captioned inbox.jsonl row against store.db; if any captioned
message hasn't landed in the store, posts a single ⚠️ Telegram
alert listing the count plus up to 5 raw caption samples so the
operator can forward them for a new parser RULE.

Silent on success — no alert when every captioned row is accounted
for, so the trade channel doesn't get a daily 'all good' ping.

Grace period: alerts arriving within the last GRACE_HOURS (default 2)
are skipped so a pending ingest cycle doesn't false-positive.

Schedule via systemd:
    OnCalendar=*-*-* 15:00:00 UTC   # = 00:00 KST daily

Run by hand to test:
    .venv/bin/python -m trade.scripts.unstored_check
"""

import html
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # test env fallback
    def load_dotenv(*args, **kwargs):
        return False

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
from trade.store import open_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("unstored-check")

DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
INBOX_PATH = DATA_DIR / "inbox.jsonl"
STORE_PATH = DATA_DIR / "store.db"

# Durable record of every caption that parse_caption couldn't turn
# into a store.db row. The daily ⚠️ alert scrolls away in the channel;
# this file is the accumulating regression backlog so an unhandled
# BeOn format becomes a test fixture instead of a problem we
# re-diagnose from scratch each time it recurs weeks later. Append-only,
# deduped on (chat_id, message_id) — a miss that persists across daily
# runs (operator hasn't added a RULE yet) is logged exactly once.
EVAL_MISS_PATH = DATA_DIR / "eval_misses.jsonl"

GRACE_HOURS = int(os.environ.get("TRADE_UNSTORED_GRACE_HOURS") or "2")
SAMPLE_COUNT = int(os.environ.get("TRADE_UNSTORED_SAMPLE") or "5")


def _notify(text: str) -> None:
    token = os.environ.get("TRADE_BOT_TOKEN")
    chat_ids = os.environ.get("TRADE_CHANNEL_CHAT_IDS", "")
    if not token or not chat_ids:
        log.info("notify skipped: no TRADE_BOT_TOKEN / TRADE_CHANNEL_CHAT_IDS")
        return
    chat_id = chat_ids.split(",")[0].strip()
    try:
        subprocess.run(
            [
                "curl", "-s", "-m", "10",
                "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "--data-urlencode", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}",
                "--data-urlencode", "parse_mode=HTML",
            ],
            timeout=15,
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def _parse_ingested(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value)
    except Exception:
        return None


def find_unstored() -> list[dict]:
    """Captioned inbox rows whose (chat_id, message_id) is missing
    from store.db AND whose ingested_at is older than GRACE_HOURS.

    Returns the parsed rows so the caller can sample raw captions.
    """
    if not INBOX_PATH.exists():
        return []

    stored: set[tuple[int, int]] = set()
    if STORE_PATH.exists():
        conn = open_db(STORE_PATH)
        try:
            for r in conn.execute(
                "SELECT source_chat_id, source_message_id FROM alerts"
            ):
                stored.add((int(r[0]), int(r[1])))
        finally:
            conn.close()

    ignored_ids = _ignored.load()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=GRACE_HOURS)
    missing: list[dict] = []
    with INBOX_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # We only count rows that carry a caption — photo-only
            # album siblings have no metadata to parse and are
            # legitimately absent from store.db.
            if not r.get("caption_present"):
                continue
            caption = r.get("caption") or r.get("text") or ""
            if not caption.strip():
                continue
            # Operator-curated ignore list — promo / off-topic posts
            # the operator has chosen to drop with /ignore <msg_id>.
            if r.get("message_id") in ignored_ids:
                continue
            # Hard-coded recurring promo prefixes (e.g. '[비온 인사이트]').
            # Same off-topic class as the msg_id list but covers a whole
            # series so the daily alert doesn't keep re-listing them.
            if _ignored.matches_prefix(caption):
                continue
            # Body-marker filter (DART 공시 릴레이 etc.) — varying line 1
            # but a stable substring elsewhere in the message.
            if _ignored.matches_contains(caption):
                continue
            # 일본 수출 데이터(BeOn) — 한국 store.db 가 아니라 별도 jp.db 로 ingest
            # 된다(ingest_inbox: parse_caption None → parse_jp_export 폴백 → jp.db).
            # 따라서 store.db alerts 에 없는 게 정상 → '미등록'으로 오탐하면 안 됨.
            # JP 포맷으로 인식되는 캡션은 JP 파이프라인이 처리하므로 여기서 제외
            # (한국 ignore 필터와 동일한 '이 store 의 alert 아님' 스킵). JP 파이프라인
            # 자체 적재 카운트는 ingest_inbox 의 jp_inserted 로그가 별도 추적.
            if _jp.parse_jp_export(caption) is not None:
                continue
            # 대만 수출 데이터(나쁜양파) — 별도 tw.db, 동일 사유로 제외(사용자 2026-07-10).
            if _tw.parse_tw_export(caption) is not None:
                continue
            # 중국 수출 데이터(나쁜양파) — 별도 cn.db, 동일 사유로 제외(사용자 2026-07-11).
            if _cn.parse_cn_export(caption) is not None:
                continue
            # 일본 수출 데이터(나쁜양파, BeOn 과 별도 두 번째 소스) — 별도 jp2.db,
            # 동일 사유로 제외(사용자 2026-07-11).
            if _jp2.parse_jp2_export(caption) is not None:
                continue
            # 태국·말레이시아·필리핀·멕시코 수출 데이터(나쁜양파, 같은 채널) —
            # 각각 별도 th.db/my.db/ph.db/mx.db, 동일 사유로 제외(사용자 2026-07-26).
            if _th.parse_th_export(caption) is not None:
                continue
            if _my.parse_my_export(caption) is not None:
                continue
            if _ph.parse_ph_export(caption) is not None:
                continue
            if _mx.parse_mx_export(caption) is not None:
                continue
            # 미국 수입 데이터(나쁜양파, 같은 채널) — 별도 us.db, 동일 사유로 제외.
            if _us.parse_us_import(caption) is not None:
                continue
            try:
                key = (int(r["chat_id"]), int(r["message_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if key in stored:
                continue
            ingested = _parse_ingested(r.get("ingested_at"))
            if ingested and ingested > cutoff:
                # Too fresh — give next ingest tick a chance.
                continue
            missing.append(r)
    return missing


def _load_logged_miss_keys(path: Path) -> set[tuple[int, int]]:
    """(chat_id, message_id) keys already present in the eval-miss
    log. Malformed / partial lines are skipped so a stray edit can't
    abort the scan."""
    if not path.exists():
        return set()
    keys: set[tuple[int, int]] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                keys.add((int(rec["chat_id"]), int(rec["message_id"])))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return keys


def log_eval_misses(missing: list[dict], path: Path | None = None) -> int:
    """Append never-before-seen unstored captions to the eval-miss log.

    Idempotent on (chat_id, message_id): re-running on a miss that's
    still unresolved doesn't duplicate it. Returns the count of
    newly-appended rows so the caller can log how much the backlog
    grew this cycle.

    Each row carries the raw caption verbatim — that's the fixture an
    operator (or Claude) lifts straight into trade/tests/test_parser.py
    when adding the RULE that finally parses the format.
    """
    if path is None:
        path = EVAL_MISS_PATH
    if not missing:
        return 0
    seen = _load_logged_miss_keys(path)
    detected_at = datetime.now(timezone.utc).isoformat()
    new = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in missing:
            try:
                key = (int(r["chat_id"]), int(r["message_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            rec = {
                "detected_at": detected_at,
                "chat_id": key[0],
                "message_id": key[1],
                "ingested_at": r.get("ingested_at"),
                "caption": r.get("caption") or r.get("text") or "",
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            new += 1
    return new


def format_alert(missing: list[dict]) -> str:
    samples = missing[:SAMPLE_COUNT]
    lines = [
        "⚠️ <b>미등록 alert 감지</b>",
        f"inbox.jsonl에는 있지만 store.db에 안 들어간 캡션 <b>{len(missing)}건</b>",
        "",
        "샘플 raw 캡션:",
    ]
    for i, r in enumerate(samples, 1):
        caption = (r.get("caption") or r.get("text") or "")[:240]
        msg_id = r.get("message_id")
        ingested = (r.get("ingested_at") or "")[:19]
        lines.append(f"<b>[{i}]</b> msg={msg_id} · {ingested}")
        lines.append(f"<code>{html.escape(caption)}</code>")
        lines.append("")
    if len(missing) > len(samples):
        lines.append(f"… 외 {len(missing) - len(samples)}건")
        lines.append("")
    lines.append("→ 처리 방법:")
    lines.append(
        "  • <b>새 포맷 변종</b> (실제 수출입 알림) → 캡션을 Claude에 공유 "
        "→ 새 RULE 추가 → <code>rm ~/.trade/store.db && "
        "python -m trade.scripts.ingest_inbox</code> 로 복구"
    )
    lines.append(
        "  • <b>무관 자료</b> (공지 / 일회성 promo) → 봇 DM에 "
        "<code>/ignore &lt;msg_id&gt;</code> → 다음부터 알림 제외"
    )
    lines.append(
        "  • <b>반복 promo 시리즈</b> ([비온 인사이트] / DART 공시 등) → "
        "<code>trade/ignored.py</code>의 <code>IGNORED_PREFIXES</code> "
        "(접두어) 또는 <code>IGNORED_CONTAINS</code> (본문 substring)에 "
        "추가 → 시리즈 전체 자동 skip"
    )
    msg = "\n".join(lines)
    # Telegram cap is 4096 UTF-16 units; truncate defensively.
    if len(msg.encode("utf-16-le")) // 2 > 3900:
        msg = msg[:3500] + "\n…(잘림)"
    return msg


def main() -> int:
    missing = find_unstored()
    if not missing:
        log.info("OK: every captioned inbox row is in store.db")
        return 0
    log.warning(
        "found %d unstored captioned rows older than %dh",
        len(missing),
        GRACE_HOURS,
    )
    newly_logged = log_eval_misses(missing)
    log.info(
        "eval-miss log: %d new appended to %s (misses this run: %d)",
        newly_logged, EVAL_MISS_PATH, len(missing),
    )
    _notify(format_alert(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
