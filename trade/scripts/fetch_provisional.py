"""Fetch 관세청 10일 단위 잠정치 4종 → '잠정 속보' 스냅샷(customs.db).

산업트렌드 본체(확정 HSK-MTI 집계)와 **분리된** 가벼운 선행 신호. 4개
엔드포인트(수출/수입 × 품목/국가)를 작년 1월~올해 12월 창으로 한 번씩
긁어, 각 종류의 '최신 누적창 + 작년 동월·동순 YoY'를 계산해 스냅샷으로
저장한다. 대시보드 렌더(_load_industry_html)는 이 스냅샷만 읽으므로
5분 새로고침마다 API를 때리지 않는다.

SILENT by design — Telegram 0. 잠정 속보는 '표시'만 하는 참고 박스라
DM/채널 알림 없음(확정 ±30% 알림과 달리 blast-radius 없음). 무료 OpenAPI,
하루 4종×4틱 = 16콜(일일 트래픽 10,000 대비 무시 가능).

Failure handling (모두 silent — journal only):
  - TRADE_DATA_GO_KR_KEY unset → log + exit 0
  - per-kind 403/빈응답/파싱오류 → log + 계속(그 종류만 빈 채로). 박스는
    가용한 종류만 그리고, 전부 비면 motie 배너가 폴백.

Schedule: trade-bot-customs-fetch.service의 한 패스(4×/day). 잠정치는
익월 1일 + 월중 10일 단위로 갱신되니 idempotent 폴링이 수 시간 내 반영.

Run by hand:
    .venv/bin/python -m trade.scripts.fetch_provisional
"""

import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from trade import customs, customs_provisional as prov

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("fetch-provisional")


def _window(now: datetime | None = None) -> tuple[str, str]:
    """(start_yymm, end_yymm) = 작년 1월 ~ 올해 12월. YoY엔 작년 동월이
    필요하고, 미래월 요청은 그냥 빈 행이라 안전(넉넉히 커버)."""
    now = now or datetime.now(timezone.utc)
    return f"{now.year - 1:04d}01", f"{now.year:04d}12"


def run() -> int:
    key = os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        log.info("TRADE_DATA_GO_KR_KEY not set — skipping (silent, no alert)")
        return 0

    start_yymm, end_yymm = _window()
    log.info("fetching 잠정치 4종, window %s→%s", start_yymm, end_yymm)

    ok = 0
    failed = 0
    with customs.session() as conn:
        prov.ensure_schema(conn)
        for kind, labels in prov.LABELS.items():
            try:
                rows = prov.fetch(kind, start_yymm, end_yymm, key=key)
            except Exception as exc:
                failed += 1
                log.warning("%s fetch failed: %s — continuing", kind, exc)
                continue
            sig = prov.latest_signal(rows, labels)
            if not sig:
                log.info("%s: no rows in window", kind)
                continue
            prov.store_signal(conn, kind, sig)
            ok += 1
            log.info(
                "%s: stored %s %s (전체 %s, YoY %s)",
                kind, sig["ym"], sig["window"],
                customs.fmt_usd(sig.get("total_usd")),
                customs.fmt_pct(sig.get("total_yoy")),
            )

    log.info("done: kinds_ok=%d failed=%d (silent — no Telegram)", ok, failed)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
