"""관련종목 시세 캐시 워머 — 렌더와 분리해 백그라운드에서 시세를 채운다.

문제: store에 BeOn 관련종목이 ~480종목인데, 대시보드 렌더가 그걸 동기로
KIS/data.go.kr에 직렬 호출하면 렌더 1회가 100초+ 걸려 systemd 타임아웃에
잘리고 일부만 캐시됐다(3→10). 해결: 렌더(_stock_quotes_for)는 캐시만
읽고(fetch=False), 실제 API 호출은 이 워머가 백그라운드에서 한다.

동작:
  - store.db 전 alert의 관련종목을 모아 복합명('A / B 등')을 분리.
  - 시간 예산(기본 60s) 안에서 청크로 워밍(get_quotes_by_name fetch=True).
    **라운드로빈 커서**로 매 틱 멈춘 자리에서 이어 돌아, 한 번에 다 못 채워도
    저빈도 꼬리까지 몇 틱 안에 전부 커버(옛 '매 틱 위에서부터'는 꼬리 영구 굶음).
    시세 TTL 30분이라 한 바퀴 도는 동안 표시가 안 빠진다.
  - 장중/마감 TTL은 recommended_ttl()이 자동 결정(장중 라이브, 마감 freeze).
  - 공급자 off(키 없음)/예외 → 조용히 0종목.

평상시 SILENT. 단, 워밍이 전건 0(KIS·EOD 양쪽 무응답)인 '전면 블랙아웃'으로
진입/복구하는 전환에만 운영자 DM 1회(5분 스팸 X — 마커 파일로 상태 유지).
dashboard-refresh.service의 렌더 직전 패스로 돌아, 다음 렌더가 채워진 캐시를
즉시 읽는다.

Run by hand:
    .venv/bin/python -m trade.scripts.fetch_quotes
    TRADE_QUOTES_BUDGET_S=120 .venv/bin/python -m trade.scripts.fetch_quotes
"""

import logging
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from trade import price_provider as pp
from trade.store import list_all_alerts, open_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("fetch-quotes")

_CHUNK = 20


def _store_db() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade") / "store.db"


def _marker() -> Path:
    """블랙아웃 상태 마커 — 존재=블랙아웃 중. 전환에만 알림 보내려고 상태 유지."""
    return (Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
            / ".quotes_blackout")


def _cursor_path() -> Path:
    """라운드로빈 워밍 커서 — 지난 틱이 멈춘 인덱스를 저장해 다음 틱이 이어감."""
    return (Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
            / ".quotes_cursor")


def _load_cursor() -> int:
    try:
        return max(0, int(_cursor_path().read_text().strip()))
    except Exception:
        return 0


def _save_cursor(i: int) -> None:
    try:
        p = _cursor_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(int(i)))
    except OSError:
        pass


def _notify(text: str) -> None:
    """운영자 DM(관세청 급변 알림과 동일 경로). 토큰/대상 없으면 silent skip —
    크레드 미설정이 배포·워밍을 깨면 안 되므로(표시 전용 기능)."""
    token = os.environ.get("TRADE_BOT_TOKEN")
    try:
        from trade import operator
        chat_id = operator.get()
    except Exception:
        chat_id = None
    if not token or not chat_id:
        return
    try:
        subprocess.run(
            ["curl", "-s", "-m", "10", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "--data-urlencode", f"chat_id={chat_id}",
             "--data-urlencode", f"text={text}",
             "--data-urlencode", "parse_mode=HTML"],
            timeout=15, check=False, capture_output=True,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def _update_blackout_state(blackout: bool, *, total: int) -> None:
    """전면 블랙아웃 진입/복구 전환에만 운영자 DM 1회. 그 외(계속 정상·계속
    블랙아웃)는 무음 — 5분 워머가 매 틱 스팸하지 않게 마커로 전환만 감지."""
    m = _marker()
    was = m.exists()
    if blackout and not was:
        _notify(f"⚠️ <b>관련종목 시세 전면 불가</b> — 워머가 {total}종목 전부 0건. "
                "KIS·data.go.kr 양쪽 무응답(키/쿼터/장애 점검 필요). "
                "대시보드 시세칩은 빈 채로 유지됩니다.")
        try:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.touch()
        except OSError:
            pass
    elif not blackout and was:
        _notify("✅ <b>관련종목 시세 복구</b> — 워머가 다시 시세를 채우는 중입니다.")
        try:
            m.unlink()
        except OSError:
            pass


def run() -> int:
    if not pp.provider_active():
        log.info("price provider off (no key) — skip")
        return 0
    db = _store_db()
    if not db.exists():
        log.info("no store.db at %s — skip", db)
        return 0

    conn = open_db(db)
    try:
        alerts = list_all_alerts(conn)
    finally:
        conn.close()

    # 빈도순으로 안정 정렬한 뒤 **라운드로빈 커서**로 매 틱 멈춘 자리에서 이어 돈다.
    # 옛 방식(매 틱 위에서부터 + 60s 예산)은 저빈도 꼬리가 매번 잘려 영구히 안
    # 채워지던 함정 — 커서가 한 바퀴를 보장한다. 시세 TTL은 30분(recommended_ttl)
    # 이라 한 바퀴(현재 ~2틱) 도는 동안 표시가 안 빠짐 → 전 종목 커버.
    freq: Counter = Counter()
    for a in alerts:
        for nm in pp.split_names(a.get("stocks") or []):
            freq[nm] += 1
    names = [nm for nm, _ in freq.most_common()]
    n = len(names)
    if not n:
        log.info("no related stocks in store — nothing to warm")
        return 0

    budget = float(os.environ.get("TRADE_QUOTES_BUDGET_S") or "60")
    ttl = pp.recommended_ttl()
    start = _load_cursor() % n
    rotated = names[start:] + names[:start]
    t0 = time.time()
    warmed = 0
    processed = 0
    for i in range(0, n, _CHUNK):
        chunk = rotated[i:i + _CHUNK]
        got = pp.get_quotes_by_name(chunk, ttl_s=ttl, fetch=True)
        warmed += len(got)
        processed += len(chunk)
        if time.time() - t0 > budget:   # 청크 후 검사 — 매 틱 최소 1청크 진행 보장
            break
    _save_cursor((start + processed) % n)

    # warmed==0인데 종목은 있다 = 캐시 신선분도 신규 페치도 0 = 전면 블랙아웃
    # (공급자 off는 위에서 이미 return). EOD 폴백이 사니 KIS 토큰사망만으론
    # 여기 안 떨어지고, 양쪽 다 죽었을 때만 0 → 그때만 WARNING + 전환 알림.
    blackout = warmed == 0 and bool(names)
    log.log(
        logging.WARNING if blackout else logging.INFO,
        "warmed %d quotes (%d/%d from cursor %d→%d) in %.1fs (budget %.0fs, ttl %ds)",
        warmed, processed, n, start, (start + processed) % n,
        time.time() - t0, budget, ttl,
    )
    _update_blackout_state(blackout, total=n)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
