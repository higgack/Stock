"""52주 신고가/신저가 슬롯 스캐너 — stock-bot 과 **분리된 독립 oneshot**.

사용자 2026-06-19 '중간에 (배포로) 변경해도 아시아·미국 신고가는 그대로 돌게':
옛 in-process asyncio task(telegram_bot._periodic_highlow_scan)는 stock-bot
재시작(매 배포)마다 죽어 진행 중 스캔이 끊겼다 — 잦은 배포일(10회+)엔 14:00 에
멈춤. 이 모듈을 `highlow-scan.timer`(:00/:15/:30/:45) 가 독립 프로세스로 호출 →
봇 배포·재시작과 무관하게 슬롯 스캔 지속. telegram/langgraph 등 무거운 deps 는
import 하지 않음(finviz_client/intl_highlow/tw_highlow/twse_client 만).

슬롯별 시장 분산은 yfinance 병렬 과부하(throttle) 회피 — 한 슬롯에 동시 heavy
스캔 1개가 되도록 시장 장중 시간대에 맞춰 배치(_HL_SCAN_SLOTS).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("bot.highlow_scan")

# 슬롯(분) → 그 슬롯에 산출할 시장 토큰. (telegram_bot 에서 이관, 단일 소스)
#   :00 — US 52주(전량) + JP 52주        :15 — HK 52주
#   :30 — CN_A 52주 + US 52주(전량 갱신)  :45 — TW 52주 + TW 무버
# US 는 새벽(KST)이라 장중 :00·:30 force 로 전량 재산출(저부하). 타 시장은 자기
# 장중에만 force, 장 밖이면 게이트(EOD 1회 후 freeze). (사용자 2026-06-19)
_HL_SCAN_SLOTS = {
    0:  ("US", "JP"),
    15: ("HK",),
    30: ("CN_A", "US"),
    45: ("TW", "TWMV"),
}


def run_slot(slot: int) -> None:
    """한 슬롯의 52주 보드 산출 — 장중이면 force 재산출, 장 밖이면 게이트 fetch
    (마감 직후 stale 면 EOD 1회 재산출 → 이후 freeze). force 경로는 YF_PAUSE 존중.
    각 시장 _kick 은 daemon 스레드 — 호출부(main)가 완료를 join 해야 한다."""
    from bot.finviz_client import _SESSIONS_UTC, yf_paused
    now = datetime.now(timezone.utc)

    def _open(m: str) -> bool:
        s = _SESSIONS_UTC.get(m)
        if not s:
            return False
        oh, om, ch, cm = s
        return now.weekday() < 5 and (oh, om) <= (now.hour, now.minute) < (ch, cm)

    try:
        paused = yf_paused()
    except Exception:
        paused = False

    for tok in _HL_SCAN_SLOTS.get(slot, ()):
        try:
            if tok == "US":
                from bot.finviz_client import fetch_high_low
                fetch_high_low(force=(_open("US") and not paused))
            elif tok in ("JP", "HK", "CN_A"):
                from bot.intl_highlow import _kick, fetch_intl_highlow
                if _open(tok) and not paused:
                    _kick(tok)                 # 장중 force(전 유니버스 재스크레이프)
                else:
                    fetch_intl_highlow(tok)    # 장 밖 게이트 → EOD 1회 후 freeze
            elif tok == "TW":
                from bot.tw_highlow import _kick_tw_highlow, fetch_tw_highlow
                if _open("TW") and not paused:
                    _kick_tw_highlow()
                else:
                    fetch_tw_highlow()
            elif tok == "TWMV":
                from bot.twse_client import fetch_tw_movers
                fetch_tw_movers(force=_open("TW"))
        except Exception as exc:
            log.warning("highlow slot %s/%s: %s", slot, tok, exc)


def _current_slot(now: datetime | None = None) -> int:
    """현재 분 → 직전 슬롯 경계(floor). 타이머가 :00/:15/:30/:45 에 발화하지만
    수 초 jitter 를 허용해 내림 매핑. 순수(테스트)."""
    m = (now or datetime.now(timezone.utc)).minute
    for s in (45, 30, 15, 0):
        if m >= s:
            return s
    return 0


def _join_workers(timeout_s: float = 1500.0) -> int:
    """run_slot 이 띄운 daemon 스캔 스레드(intl/tw/us _compute)들이 끝날 때까지
    대기 — oneshot 프로세스가 먼저 종료하면 스캔이 중간에 죽어(=바로 그 버그)
    캐시가 안 써짐. main 스레드 외 살아있는 스레드를 join. 반환=대기한 스레드 수."""
    main_t = threading.main_thread()
    seen = 0
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t is not main_t and t.is_alive()]
        if not alive:
            break
        seen = max(seen, len(alive))
        for t in alive:
            t.join(timeout=10)
    return seen


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    slot = _current_slot()
    log.info("highlow-scan tick: slot=%d markets=%s",
             slot, _HL_SCAN_SLOTS.get(slot, ()))
    run_slot(slot)
    n = _join_workers()
    log.info("highlow-scan tick done: slot=%d (joined %d worker thread(s))", slot, n)


if __name__ == "__main__":
    main()
