"""JPX 상장종목 코드→영문명 로컬 마스터 빌더(`~/.trade/jpx_codes.json`).

혼합시장 보드의 도쿄 상장사 딥링크가 **신원 확인**에 쓴다(`trade.stock_link.
jp_confirms`). 형제 `build_krx_codes` 와 규약이 같다: `--if-stale` 면 마스터가
없거나 오래됐을 때만 받고, 성공(하한 이상)일 때만 원자적으로 덮어쓴다. 실패하면
**기존 마스터를 유지**한다. 단 **자리는 다르다** — 제 유닛(`trade-bot-jpx-codes`
6시간 점검)에서 돈다. dashboard-refresh 안에 두면 한 시간 예산을 적재·렌더와
나눠 써, 느린 원천이 화면 갱신을 막을 수 있었다(독립 리뷰 2026-09-23 · #116).

⚠️ 실패는 **짧게 쉬고** 다시 묻는다(#303·#384): 5분 타이머가 원천 장애 동안
매 틱 두드리지 않도록 마지막 실패 뒤 6시간은 건너뛴다. 실패 사유는 곁파일
(`jpx_codes.fail.json`)에 남아 다음 사람이 갈래를 읽는다(#82). 손으로 돌리면
(= `--if-stale` 없이) 쉬는 시간을 무시하고 바로 받는다.
⚠️ 어떤 예외든 **종료코드 0** 이고 경고 로그에 사유를 남긴다(#116·#12). 요청마다
총 시간 상한(`jpx_master._DEADLINE_S`)이 있다.
⚠️ **시도 전에** '시도 중' 을 실패 곁파일에 적는다 — 유닛 타임아웃·강제 종료는
파이썬 예외가 아니라서 실패 기록을 남길 기회가 없다. 그 기록이 없으면 쉬는 시간이
안 걸려 매 틱 원천을 다시 두드린다(독립 리뷰 2026-09-23). 성공하면 지운다.

Run by hand (트레이드 체크아웃 — 유닛과 같은 인터프리터):
    cd ~/stock-trade && .venv/bin/python -m trade.scripts.build_jpx_codes
    cd ~/stock-trade && .venv/bin/python -m trade.scripts.build_jpx_codes --if-stale
이상 없을 때의 마지막 줄:
    JPX master built: <N> codes (JPX 기준 YYYY-MM-DD, via 목록 페이지 링크 · 버림 <M> · 중복 <K>) → …/jpx_codes.json
(`--if-stale` 로 신선하면 `JPX master fresh — skip` 한 줄.)
유닛: `trade-bot-jpx-codes.{service,timer}` — `journalctl -u trade-bot-jpx-codes`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from trade import jpx_master as jm
from trade.price_provider import _atomic_write_json    # 형제와 같은 원자 쓰기(#38)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("build-jpx-codes")

# 전 상장(프라임·스탠더드·그로스 + ETF 등)은 수천 행이다 — 그보다 한참 적으면
# 파싱이 깨진 것이지 목록이 줄어든 게 아니다. 부분을 완전본으로 굽지 않는다(#280).
# ⚠️ 행 수 자체는 아직 재지 않았다 — 빌드 로그가 첫 실측이다(#165).
MIN_CODES = 1000
FAIL_COOLDOWN_S = 6 * 3600


def _is_stale(now: float) -> bool:
    """없거나, 오래됐거나, **로더가 못 쓰는** 파일이면 True.

    ⚠️ mtime 만 보면 깨진 파일·옛 형식이 '신선' 으로 읽혀 최대 7일 동안 전
    링크가 평문인 채 다시 만들지 않는다(독립 리뷰 2026-09-23) — 신선도는
    '쓸 수 있는 목록이 있나' 까지 물어야 한다(#25)."""
    max_age = float(os.environ.get("TRADE_JPX_MASTER_MAX_AGE_DAYS") or "7")
    try:
        age = now - jm.PATH.stat().st_mtime
    except OSError:
        return True                                   # 없음 → 빌드
    return age > max_age * 86400 or not jm.load()


def _last_failure(now: float) -> dict | None:
    """쉬는 중이면 그 기록(at·reason), 아니면 None."""
    try:
        rec = json.loads(jm.fail_path().read_text(encoding="utf-8"))
        at = float(rec.get("at") or 0)
    except Exception:                                 # noqa: BLE001
        return None
    return rec if 0 <= now - at < FAIL_COOLDOWN_S else None


def _mark_failure(now: float, reason: str) -> None:
    try:
        _atomic_write_json(jm.fail_path(), {"at": now, "reason": reason[:500]})
    except OSError as e:
        log.warning("fail marker not written: %s", e)


def run(*, if_stale: bool = False, get=None, now: float | None = None) -> int:
    now = time.time() if now is None else now
    if if_stale:
        if not _is_stale(now):
            log.info("JPX master fresh — skip")
            return 0
        rec = _last_failure(now)
        if rec:
            left = FAIL_COOLDOWN_S - (now - float(rec["at"]))
            log.info("JPX master: 마지막 시도가 실패해 %.1f시간 뒤 다시 시도 "
                     "(사유: %s)", left / 3600, rec.get("reason"))
            return 0
    # 끝나지 못한 시도도 쉬는 시간을 걸게 **먼저** 적는다(위 독스트링).
    _mark_failure(now, "시도 중 — 끝나지 않았다(타임아웃·강제 종료 의심)")
    try:
        master, stats = jm.fetch(get=get)
    except Exception as e:                            # noqa: BLE001
        reason = f"{type(e).__name__}: {e}"
        _mark_failure(now, reason)
        log.warning("JPX master not rebuilt — keep existing: %s", reason)
        return 0
    if len(master) < MIN_CODES:
        reason = (f"코드 {len(master)}개 < 하한 {MIN_CODES} — 파싱이 깨졌을 수 "
                  f"있다(행 {stats.get('rows')} · 버림 {stats.get('rejected')} "
                  f"예 {stats.get('rejected_sample')})")
        _mark_failure(now, reason)
        log.warning("JPX master not rebuilt — keep existing: %s", reason)
        return 0
    env = {"codes": master, "effective": stats.get("effective") or "",
           "url": stats.get("url") or "", "via": stats.get("via") or "",
           "built_at": datetime.fromtimestamp(now, timezone.utc).isoformat()}
    try:
        _atomic_write_json(jm.PATH, env)
    except OSError as e:
        _mark_failure(now, f"마스터 쓰기 실패: {e}")     # '시도 중' 을 덮는다(#82)
        log.warning("JPX master write failed: %s", e)
        return 0
    try:
        jm.fail_path().unlink()
    except OSError:
        pass
    log.info("JPX master built: %d codes (JPX 기준 %s, via %s · 버림 %d · 중복 %d)"
             " → %s", len(master), env["effective"] or "미상", env["via"],
             stats.get("rejected", 0), stats.get("dupes", 0), jm.PATH)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        return run(if_stale="--if-stale" in argv)
    except Exception as e:                            # noqa: BLE001
        log.warning("JPX master build crashed — keep existing: %s: %s",
                    type(e).__name__, e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
