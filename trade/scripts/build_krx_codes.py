"""KRX 상장종목 이름→코드 로컬 마스터 빌더.

resolve_codes가 data.go.kr 정확일치 검색(지연·누락·음성캐시)에 의존하던 걸
로컬 마스터(`~/.trade/krx_codes.json`)로 즉시 해석하게 만든다 — 렌더(fetch=False)
까지 외부 호출 없이 대부분 해석 → 관련종목 시세 커버리지 즉시 상승.

데이터 소스는 기존 키(getStockPriceInfo)를 그대로 써 별도 승인 불필요. 전 종목을
이름필터 없이 페이지네이션 → 코드 dedup → {종목명: 코드}.

`--if-stale`: 마스터가 없거나 TRADE_KRX_MASTER_MAX_AGE_DAYS(기본 7)보다 오래됐을
때만 빌드. dashboard-refresh.service의 한 패스로 매 틱 호출돼도 주 1회만 실제
재빌드(나머진 즉시 skip) — fetch_provisional --if-stale와 동일 패턴.

멱등: 성공(≥1건) 시에만 원자적으로 덮어씀. 키 없거나 0건이면 기존 마스터 유지
(silent). 코드는 거의 안 바뀌므로 한 번 만들어두면 신규 상장만 주간 반영.

Run by hand:
    .venv/bin/python -m trade.scripts.build_krx_codes
    .venv/bin/python -m trade.scripts.build_krx_codes --if-stale
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from trade import price_provider as pp

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("build-krx-codes")


_FAIL_BACKOFF_S = 3600


def _ver_path() -> Path:
    return pp._KRX_MASTER.with_suffix(".ver")


def _is_stale() -> bool:
    """마스터가 없거나 max-age보다 오래됐거나 **다른 판의 코드가 만들었으면** True(재빌드 필요)."""
    try:
        if _ver_path().read_text(encoding="utf-8").strip() != str(pp.KRX_MASTER_VER):
            return True
    except OSError:
        return True                       # 판 기록 없음 = 옛 판이 만든 마스터
    max_age_days = float(os.environ.get("TRADE_KRX_MASTER_MAX_AGE_DAYS") or "7")
    try:
        age = time.time() - pp._KRX_MASTER.stat().st_mtime
    except OSError:
        return True                       # 파일 없음 → 빌드
    return age > max_age_days * 86400


def run(if_stale: bool = False) -> int:
    if not os.environ.get("TRADE_DATA_GO_KR_KEY"):
        log.info("no TRADE_DATA_GO_KR_KEY — skip")
        return 0
    if if_stale and not _is_stale():
        log.info("KRX master fresh — skip (use --if-stale off to force)")
        return 0
    fail = pp._KRX_MASTER.with_suffix(".fail")
    try:   # 직전 실패 뒤 1시간은 다시 걷지 않는다 — 원천 장애 중 5분마다 전수 페이지를 치지 않게(#303)
        if if_stale and time.time() - fail.stat().st_mtime < _FAIL_BACKOFF_S:
            log.info("KRX master: last build failed <%ds ago — backoff", _FAIL_BACKOFF_S)
            return 0
    except OSError:
        pass
    master = pp.fetch_krx_master()
    if not master:
        log.warning("fetched 0 KRX names — keep existing master")
        try:
            fail.parent.mkdir(parents=True, exist_ok=True)
            fail.touch()
        except OSError:
            pass
        return 0
    path = pp._KRX_MASTER
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)                 # 원자적 교체(부분쓰기 노출 방지)
        _ver_path().write_text(str(pp.KRX_MASTER_VER), encoding="utf-8")   # 마스터 **뒤에** 판을 적는다
        fail.unlink(missing_ok=True)
    except OSError as e:
        log.warning("write failed: %s", e)
        return 0
    log.info("KRX master built: %d names → %s", len(master), path)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return run(if_stale="--if-stale" in argv)


if __name__ == "__main__":
    sys.exit(main())
