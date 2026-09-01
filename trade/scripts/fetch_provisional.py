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

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from trade import customs, customs_provisional as prov

load_dotenv()

# 5분마다 도는 대시보드 새로고침에서 --if-stale로 호출할 때, 이 시간 안에
# 이미 수집했고 4종 시계열이 다 차 있으면 API를 안 때리고 건너뛴다(잠정치는
# 10일 단위라 자주 긁을 필요 없음). env로 조정.
MAX_AGE_H_DEFAULT = float(os.environ.get("TRADE_PROV_MAX_AGE_H") or "6")

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


def run(if_stale: bool = False, max_age_h: float = MAX_AGE_H_DEFAULT) -> int:
    key = os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        log.info("TRADE_DATA_GO_KR_KEY not set — skipping (silent, no alert)")
        return 0

    # --if-stale: 신선하면 API 0콜로 건너뜀(5분 새로고침에 끼워도 무해).
    if if_stale:
        with customs.session() as conn:
            if not prov.is_stale(conn, max_age_h):
                log.info("data fresh (≤%.1fh) & complete — skip fetch", max_age_h)
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
            # 헤드라인 신호 + 전체 시계열(10일 모멘텀 뷰용) 둘 다 저장.
            # 타임라인 적립은 대시보드 렌더가 이 저장본에서 재빌드한다.
            prov.store_signal(conn, kind, sig, rows=rows)
            ok += 1
            log.info(
                "%s: stored %s %s (전체 %s, YoY %s)",
                kind, sig["ym"], sig["window"],
                customs.fmt_usd(sig.get("total_usd")),
                customs.fmt_pct(sig.get("total_yoy")),
            )

    # 잠정 타임라인 적립은 대시보드 렌더(provisional_archive.refresh)가
    # 저장 데이터에서 매 렌더마다 재빌드 — 여기선 데이터만 저장한다.
    # (customs-fetch / dashboard-refresh 둘 다 fetch_provisional 직후
    #  trade.dashboard를 실행하므로 적립 누락 없음.)
    log.info("done: kinds_ok=%d failed=%d (silent — no Telegram)", ok, failed)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="관세청 10일 단위 잠정치 4종 → 잠정 속보 스냅샷+시계열 (silent).")
    ap.add_argument(
        "--if-stale", action="store_true",
        help=("신선하면(4종 시계열 완비 + 최근 수집 ≤TRADE_PROV_MAX_AGE_H) "
              "API 0콜로 건너뜀. 5분 대시보드 새로고침에 끼워 self-heal용."))
    ap.add_argument(
        "--max-age-h", type=float, default=MAX_AGE_H_DEFAULT, metavar="H",
        help=f"--if-stale 신선도 기준 시간(기본 {MAX_AGE_H_DEFAULT})")
    ap.add_argument(
        "--why", nargs="?", const="", metavar="YYYY-MM",
        help=("'이 달 잠정, 이거 맞아?' 진단 — 저장된 창(D1/D2/FULL)별 절대액을 "
              "작년 동창과 나란히 찍고 누적 불변식 위반을 표시한다. API 0콜."))
    args = ap.parse_args()
    if args.why is not None:
        return _why(args.why or None)
    return run(if_stale=args.if_stale, max_age_h=args.max_age_h)


def _why(ym) -> int:
    """저장된 시계열만 읽어 창 진단(네트워크 0). 쓰기 없음."""
    import sys as _sys
    print(f"[잠정 진단] 인터프리터 {_sys.executable}")
    conn = customs.open_db(customs.DEFAULT_DB)
    try:
        rows_by_kind = prov.load_rows(conn)
    finally:
        conn.close()
    if not rows_by_kind:
        # 대조 0건은 통과가 아니라 진단 실패다(#54).
        print("❌ 저장된 잠정 시계열이 없다 — 먼저 수집:"
              " .venv/bin/python -m trade.scripts.fetch_provisional")
        return 1
    for line in prov.explain_windows(rows_by_kind, ym):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
