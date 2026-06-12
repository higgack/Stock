"""자정 결산 — '어제 실제로 작동한 것'만 운영자 DM (사용자 2026-06-13).

00:03 KST 에 어제(KST) 하루를 결산:
  📨 BeOn 포워드 N건 (store.db ingested_at 기준)
  📦 관세청: 스윕 n회 · 데이터 갱신 m회 · 신규 급증 k건 (run_ledger)
  ❌ 오류: probe/스캔 실패·부분 스캔 횟수 (있을 때만)
  ⛔ 시스템: BeOn 리스너 비활성 / 대시보드 렌더 정체 (지금 상태 점검)

**활동·오류가 전혀 없으면 무음** — '실제 작동하는 날에만' (사용자 정책).
unstored_check(00:00, 유실 감지)와 별개·보완: 그쪽은 '잃은 것', 이쪽은
'한 것'. LLM 0·₩0.

수동: .venv/bin/python -m trade.scripts.daily_digest [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import run_ledger
from trade.store import open_db

load_dotenv()
log = logging.getLogger("daily-digest")
_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")


_STORE_PATH = _DATA_DIR / "store.db"


def _kst_date_of(ts: str) -> str:
    """ingested_at(UTC isoformat, bot.py 가 기록) → KST 날짜 문자열.
    오프셋/Z/naive 전부 tolerant — naive 는 UTC 로 간주."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_KST).date().isoformat()
    except Exception:
        return ""


def _forward_count(date_key: str) -> int:
    """store.db 에서 해당 KST 날짜에 수집된 alert 수. ingested_at 은
    UTC isoformat — KST 하루는 UTC 로 '전일 15:00~당일 15:00' 이라
    substr 비교 불가(새벽 수집분이 UTC 전일 문자열). UTC 날짜가
    (전일, 당일) 두 값뿐이므로 그걸로 프리필터 후 행별 KST 정밀 판정."""
    try:
        prev_key = (datetime.fromisoformat(date_key).date()
                    - timedelta(days=1)).isoformat()
        conn = open_db(_STORE_PATH)
        try:
            rows = conn.execute(
                "SELECT ingested_at FROM alerts "
                "WHERE substr(ingested_at,1,10) IN (?,?)",
                (prev_key, date_key)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("forward count failed: %s", exc)
        return 0
    return sum(1 for (ts,) in rows if _kst_date_of(ts) == date_key)


def _listener_inactive() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "--quiet", "trade-bot-beon-listener"],
            timeout=10, check=False)
        return out.returncode != 0
    except Exception:
        return False   # 판단 불가 — 경고 안 함 (보수적)


def _dashboard_stale_min() -> int | None:
    """index.html mtime 경과(분) — 30분+ 면 렌더 정체 (2026-06-12 12:52
    동결 클래스의 영구 감시). 파일 없으면 None."""
    try:
        p = _DATA_DIR / "dashboard" / "index.html"
        return int((time.time() - p.stat().st_mtime) / 60)
    except OSError:
        return None


def compose(date_key: str, fwd: int, counts: dict,
            listener_down: bool, stale_min: int | None) -> str | None:
    """결산 본문 — 활동·오류 전무면 None(무음). 순수(단위테스트)."""
    mmdd = date_key[5:].replace("-", "/")
    work: list[str] = []
    if fwd:
        work.append(f"📨 BeOn 포워드 {fwd}건 수집")
    sweeps = counts.get("sweeps", 0)
    refresh = counts.get("refresh", 0)
    entrants = counts.get("entrants", 0)
    if refresh or entrants:
        seg = f"📦 관세청 스윕 {sweeps}회 · 데이터 갱신 {refresh}회"
        if entrants:
            seg += f" · 🔍 신규 급증 {entrants}건"
        work.append(seg)
    errs: list[str] = []
    for field, label in (("probe_fail", "probe 오류"),
                         ("scan_fail", "스캔 전체실패"),
                         ("scan_partial", "부분 스캔")):
        n = counts.get(field, 0)
        if n:
            errs.append(f"{label} {n}회")
    sys_warn: list[str] = []
    if listener_down:
        sys_warn.append("⛔ BeOn 리스너 비활성 — 포워드 중단 상태")
    if stale_min is not None and stale_min > 30:
        sys_warn.append(f"⛔ 대시보드 렌더 정체 — index.html {stale_min}분 미갱신")
    if not work and not errs and not sys_warn:
        return None   # 실제 작동/이상 없는 날 — 무음
    lines = [f"📊 <b>{mmdd} 결산</b>"]
    lines += work or ["(작동 내역 없음)"]
    if errs:
        lines.append("❌ 오류: " + " · ".join(errs))
    lines += sys_warn
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="결산 대상 KST 날짜 (기본=어제)")
    args = parser.parse_args(argv)
    date_key = args.date or (
        datetime.now(_KST).date() - timedelta(days=1)).isoformat()

    body = compose(
        date_key,
        _forward_count(date_key),
        run_ledger.day_counts(date_key),
        _listener_inactive(),
        _dashboard_stale_min(),
    )
    if body is None:
        log.info("digest %s: 활동·이상 없음 — 무음", date_key)
        return 0
    from trade.scripts.scan_customs import _send_alert
    ok = _send_alert(body)
    log.info("digest %s: %s", date_key, "sent" if ok else "send failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
