"""관세청 데이터 변동 감지 → (변동 시에만) 완료 DM [+ B단계 LLM 추가신호].

customs-fetch 파이프라인에서 `industry_report --store` 다음, `trade.dashboard`
앞에 실행된다. 저장 스냅샷 fingerprint를 직전 값과 비교해 '바뀐 틱'에서만
동작한다:
  - 운영자 DM '✅ 관세청 데이터 갱신' (변동 요약)
  - (B단계) LLM 추가신호 카드 생성·저장 → 대시보드 🔍 박스

fingerprint가 그대로면 조용히 종료(4회/일 스팸 방지). 최초 1회는 baseline만
기록하고 무음(첫 배포 시 가짜 '갱신' 알림 방지) — scan_customs의 baseline-
silent 패턴 미러.
"""
from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

from trade import customs, industry, insights

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh-signals")

_FP_KEY = "data_fp"


def _send_dm(body: str) -> bool:
    """운영자 DM. 등록된 운영자 chat이 없으면 조용히 skip(무알림)."""
    from trade import operator
    from trade.scripts import customs_alert

    chat = operator.get()
    if not chat:
        log.info("no operator chat recorded — skip update DM")
        return False
    return customs_alert._send(chat, body)


def _summary(conn) -> tuple[str, str]:
    """(최신 확정월, 산업 수)를 읽어 완료 DM 본문을 만든다."""
    by_ind = industry.load_stored(conn)
    series = industry.industry_series(by_ind) if by_ind else {}
    latest = ""
    for pts in series.values():
        if pts and pts[-1]["ym"] > latest:
            latest = pts[-1]["ym"]
    body = (
        "✅ <b>관세청 데이터 갱신</b>\n"
        f"최신 확정월 <code>{latest or '—'}</code> · 산업 {len(series)}개\n"
        "급등률·급증액·산업트렌드 갱신됨"
    )
    return body, latest


def main(argv: list[str] | None = None) -> int:
    with customs.session() as conn:
        fp = insights.data_fingerprint(conn)
        last = insights.get_state(conn, _FP_KEY)

        if last is None:
            insights.set_state(conn, _FP_KEY, fp)
            log.info("baseline fingerprint recorded — silent (no DM)")
            return 0
        if fp == last:
            log.info("no data change since last tick — silent")
            return 0

        # 데이터 변동 감지 — 완료 DM (B단계에서 LLM 신호 생성이 이 사이에 들어감)
        body, latest = _summary(conn)
        sent = _send_dm(body)
        insights.set_state(conn, _FP_KEY, fp)
        log.info("data changed (latest=%s) — update DM %s, fingerprint advanced",
                 latest or "—", "sent" if sent else "skipped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
