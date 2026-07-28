"""기존 store.db 행을 raw_text로 재파싱 — 파서 개선(회사 접두 분리 등)을 이미
저장된 alert에 소급 적용한다.

왜: parse_caption은 ingest 시점에 1회만 돈다(ingest_inbox는 멱등이라 기존 행 재파싱
안 함). 파서를 고쳐도 옛 행은 옛 분류(예: '휴스틸: 대구경 철강제 관'이 통째 item)로
남아 있어, 회사별/품목별 뷰가 계속 틀리게 나온다. 이 스크립트가 전 행의 raw_text를
새 파서로 다시 파싱해 item/stocks/title_kind/region/dedup_key 등을 갱신한다.

안전:
  - **원본 raw_text는 절대 안 건드림**(재파싱 소스). source/ingested/posted/media도 유지.
  - **--dry-run(기본)**: 바뀔 건수 + before→after 샘플만 출력, 쓰기 0.
  - **--apply**: 실제 UPDATE(트랜잭션 1회).
  - 멱등(재파싱 결정적). 전 행 일괄이라 dedup_key가 바뀌어도 같은 item의 잠정↔확정은
    같은 새 키로 묶여 유지(부분 적용으로 형제가 갈리지 않게 한 번에).

Run by hand:
    .venv/bin/python -m trade.scripts.reparse_store              # dry-run
    .venv/bin/python -m trade.scripts.reparse_store --apply      # 실제 적용
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from trade.parser import parse_caption
from trade.store import alert_to_row, open_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("reparse-store")

# 재파싱이 유도하는 컬럼만 갱신 — raw_text/source/ingested/posted/media는 유지.
_UPDATE_SQL = """
UPDATE alerts SET
  direction=:direction, status=:status,
  period_start=:period_start, period_end=:period_end, period_kind=:period_kind,
  title_kind=:title_kind, item=:item, item_raw=:item_raw,
  is_composite=:is_composite, composite_parts=:composite_parts,
  region=:region, country=:country, regions=:regions, countries=:countries,
  stocks=:stocks, stocks_meta=:stocks_meta, has_etc=:has_etc,
  commentary=:commentary, parse_warnings=:parse_warnings, dedup_key=:dedup_key
WHERE id=:id
"""

# 변경 판정 비교 컬럼(파싱 유도).
_CMP = ("title_kind", "item", "item_raw", "is_composite", "composite_parts",
        "region", "country", "regions", "countries", "stocks", "stocks_meta",
        "has_etc", "commentary", "dedup_key")


def _db_path() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade") / "store.db"


def run(apply: bool = False) -> int:
    db = _db_path()
    if not db.exists():
        log.info("no store.db at %s — skip", db)
        return 0
    conn = open_db(db)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM alerts").fetchall()]
        changed = []
        for r in rows:
            parsed = parse_caption(r.get("raw_text") or "")
            if parsed is None:
                continue                       # 파싱 불가(헤더 누락 등) → 원행 유지
            new = alert_to_row(
                parsed,
                source_chat_id=r["source_chat_id"],
                source_message_id=r["source_message_id"],
                media_group_id=r.get("media_group_id"),
                ingested_at=r["ingested_at"],
                posted_at=r["posted_at"],
                raw_text=r["raw_text"],
                media_paths=json.loads(r["media_paths"]) if r.get("media_paths") else [],
            )
            if any(str(new[k]) != str(r.get(k)) for k in _CMP):
                new["id"] = r["id"]
                changed.append((r, new))

        log.info("재파싱 변경 대상: %d / %d", len(changed), len(rows))
        for old, new in changed[:30]:
            log.info("  [%s] item %r→%r | stocks %s→%s | kind %s→%s",
                     old["id"], old["item"], new["item"],
                     old["stocks"], new["stocks"], old["title_kind"], new["title_kind"])

        if not changed:
            log.info("변경 없음.")
        elif apply:
            conn.executemany(_UPDATE_SQL, [new for _old, new in changed])
            conn.commit()
            log.info("APPLIED %d updates ✅", len(changed))
        else:
            log.info("DRY-RUN — 쓰기 0. 적용하려면 --apply (위 샘플 먼저 확인)")
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return run(apply="--apply" in argv)


if __name__ == "__main__":
    sys.exit(main())
