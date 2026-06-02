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

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from trade import customs, industry, insights, llm_insights

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh-signals")

_FP_KEY = "data_fp"
_CARDS_KEY = "llm_insight_cards"


def _send_dm(body: str) -> bool:
    """운영자 DM. 등록된 운영자 chat이 없으면 조용히 skip(무알림)."""
    from trade import operator
    from trade.scripts import customs_alert

    chat = operator.get()
    if not chat:
        log.info("no operator chat recorded — skip update DM")
        return False
    return customs_alert._send(chat, body)


def _latest_ym(conn) -> tuple[str, int]:
    by_ind = industry.load_stored(conn)
    series = industry.industry_series(by_ind) if by_ind else {}
    latest = ""
    for pts in series.values():
        if pts and pts[-1]["ym"] > latest:
            latest = pts[-1]["ym"]
    return latest, len(series)


def _refresh_llm_cards(conn) -> list[dict]:
    """LLM 추가신호 카드를 새로 생성·저장(pipeline_state). 비활성/키없음/
    오류면 generate()가 []를 주고 박스는 미표시. 반환=카드 리스트. (1 LLM 콜)"""
    by_ind = industry.load_stored(conn)
    by_imp = industry.load_stored_imports(conn)
    by_mti = industry.load_mti_stored(conn)
    by_mti_imp = industry.load_mti_imports(conn)
    digest = llm_insights.build_digest(by_ind, by_imp, by_mti, by_mti_imp)
    cards = llm_insights.generate(digest)
    insights.set_state(conn, _CARDS_KEY, json.dumps(cards, ensure_ascii=False))
    return cards


def _cards_for(conn, *, regen: bool) -> tuple[list[dict], bool]:
    """(cards, called_api). regen=True면 LLM 새로 생성(1 콜). False면 저장된
    카드를 그대로 재사용(0 콜) — 저장된 카드가 없을 때만 1회 생성. 프리뷰용
    --force가 매번 과금되지 않도록."""
    if not regen:
        cached = insights.get_state(conn, _CARDS_KEY)
        if cached is not None:
            try:
                cards = json.loads(cached)
                log.info("reusing %d cached LLM cards (no API call)", len(cards))
                return cards, False
            except Exception:
                pass
    return _refresh_llm_cards(conn), True


def _dm_body(latest: str, n_ind: int, n_cards: int) -> str:
    sig = f"\n🔍 추가신호 {n_cards}개 생성" if n_cards else ""
    return (
        "✅ <b>관세청 데이터 갱신</b>\n"
        f"최신 확정월 <code>{latest or '—'}</code> · 산업 {n_ind}개\n"
        f"급등률·급증액·산업트렌드 갱신됨{sig}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="fingerprint·baseline 무시하고 즉시 아카이브·DM 갱신 "
                         "(프리뷰용). LLM은 저장된 카드를 재사용(0 콜) — 새로 "
                         "부르려면 --regen-llm 추가.")
    ap.add_argument("--regen-llm", action="store_true",
                    help="LLM 추가신호를 새로 생성(1 콜). 기본은 데이터 변동 시에만 "
                         "새로 생성하고, --force 단독은 저장 카드를 재사용한다.")
    args = ap.parse_args(argv)

    with customs.session() as conn:
        fp = insights.data_fingerprint(conn)
        last = insights.get_state(conn, _FP_KEY)
        data_changed = last is not None and fp != last

        if not args.force:
            if last is None:
                insights.set_state(conn, _FP_KEY, fp)
                log.info("baseline fingerprint recorded — silent (no DM)")
                return 0
            if not data_changed:
                log.info("no data change since last tick — silent")
                return 0

        # 변동 감지(또는 --force) → LLM 추가신호(변동/명시적 재생성 시만 새로 호출,
        #  --force 단독은 재사용) → 월별 아카이브 기록 → 완료 DM → fingerprint 전진
        from trade import industry_archive, llm_usage

        # LLM 새 호출 조건: 명시적 --regen-llm, 또는 (무인) 타이머가 실제
        # 데이터 변동을 감지한 경우. --force(수동 재렌더/재동결)는 데이터가
        # 바뀌어 있어도 저장 카드를 재사용 → 항상 0콜(과금 예측 가능).
        regen = args.regen_llm or (data_changed and not args.force)
        latest, n_ind = _latest_ym(conn)
        before = llm_usage.summary()["d30"]["cost_krw"]
        cards, called_api = _cards_for(conn, regen=regen)
        cost_krw = max(0, llm_usage.summary()["d30"]["cost_krw"] - before)
        try:
            industry_archive.record_snapshot(conn, cards, cost_krw=cost_krw or None)
            industry_archive.regenerate()
        except Exception as exc:           # 아카이브 실패가 갱신을 막지 않게
            log.warning("industry archive update failed: %s", exc)
        sent = _send_dm(_dm_body(latest, n_ind, len(cards)))
        insights.set_state(conn, _FP_KEY, fp)
        log.info("%s (latest=%s) — %d LLM cards (%s), archive updated, DM %s, fp advanced",
                 "forced" if args.force else "data changed", latest or "—",
                 len(cards), "new 1 call" if called_api else "reused 0 call",
                 "sent" if sent else "skipped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
