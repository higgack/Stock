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

from dotenv import load_dotenv

from trade import ignored as _ignored
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
        "  • <b>무관 자료</b> ([비온 인사이트] / 공지 등) → 봇 DM에 "
        "<code>/ignore &lt;msg_id&gt;</code> → 다음부터 알림 제외"
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
    _notify(format_alert(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
