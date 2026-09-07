"""전 chapter 관세청 급변 스캔 — daily auto-discovery entrypoint.

Sweeps HS chapters 01~97, ranks two views (📈 급등률 / 💵 급증액), refreshes
the live snapshot, appends to the permanent archive, and DMs the operator
the items NEW to this run (baseline-silent on first run). Replaces the
pin-only fetch as the panel's data source.

Modes:
  --dry-run     scan + print a value-floor histogram; NO DB writes, NO
                alerts. Use once to see how many surges exist before
                trusting the live feed.
  (default)     scan → store_live (replace) → upsert_archive (forever) →
                DM new entrants. Idempotent: re-running the same month
                re-confirms the same snapshot without duplicate alerts.

One-time migration: on first successful real run, the legacy pin file
(~/.trade/hs_map.tsv) is backed up to hs_map.bak.<ts>.tsv and cleared —
the operator asked for a fresh start where the panel is surge-driven.
Guarded by a marker (~/.trade/.surge_migrated) so it happens exactly
once; manual /hs pins added later are untouched. Skip with
--keep-pins.

Exit codes: 0 success / nothing to do; 1 every chapter fetch failed.

Schedule: trade-bot-customs-fetch.timer (daily), chained after the
pinned fetch_customs so manual pins still cache too.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import customs, customs_scan, hs_map

# Load host .env so a manual run (e.g. --dry-run from a shell) sees
# TRADE_DATA_GO_KR_KEY. systemd injects it via EnvironmentFile, but the
# sibling scripts (fetch_customs / customs_alert) load_dotenv too so an
# operator can run any of them by hand; this one was missing it.
load_dotenv()

log = logging.getLogger("scan-customs")

# Scan compares the latest two CONFIRMED months. 관세청 confirms a month
# ~the 15th of the following month, so early in any month the freshest
# confirmed data is ~2 months back (on 2026-06-01 the newest was 4월, and
# 5·6월 had no rows at all). A 3-month window then yields only ONE real
# month → no comparison → empty ranking (the bug that wiped live on
# 6/1). 6 months guarantees ≥2 confirmed months year-round with slack.
# Decoupled from fetch_customs' 12-month history window via its OWN env
# var.
# 16개월 (2026-07-01 밤 14→16 — YoY 재-off-by-one): 14개월(2026-06-12)은
# '최신 데이터월 = now 또는 now-1' 가정이었으나, **매월 1~15일엔 최신 확정월이
# now-2** (전월 확정이 익월 ~15일이라, 7/1엔 최신=2026-05=now-2, 6월 확정은
# 7/15). now-2 의 전년동월은 now-14 인데 14개월 윈도(now-13..now)는 now-13 까지만
# → 전년동월 밖 → 히트맵 YoY 전부 '신규(전기 0)'=무색 (2026-07-01 실측: ref=
# 2026-05, 필요=2025-05, 14개월 윈도 시작=2025-06). 16개월(now-15..now)이면
# now-2 최신월도 전년동월 포함 + now-3 까지 1달 슬랙. 비용 페이지수 월수 비례
# (+14% vs 14) « 10,000 무료 한도. 스윕은 월 ~3회라 절대비용 무시 가능.
LOOKBACK_MONTHS_DEFAULT = int(
    os.environ.get("TRADE_CUSTOMS_SCAN_LOOKBACK_MONTHS")
    or str(customs.YOY_LOOKBACK_MONTHS)   # 단일 소스(=16) — 재발 방지
)
_MIGRATE_MARKER = Path.home() / ".trade" / ".surge_migrated"
# 배포 직후 1회 강제 풀 스윕 마커 (버전드). probe 는 관세청 무변경이면 스윕을
# 건너뛰어, 새 스냅샷 필드(단가 중량·수입 랭킹 등)가 다음 관세청 갱신(월 ~3회)
# 까지 안 채워진다 → 마커 내용이 현재 _SCAN_ROLLOUT_VERSION 과 다르면 무변경
# 이어도 1회 강제 풀 스윕으로 즉시 재적재(성공 저장 경로에서만 버전 기록 →
# 실패 시 다음 probe 재시도). **새 populate-필요 기능 배포 시 버전만 bump.**
# (옛 `.weights_backfilled` 경로 재사용 — 기존 마커는 timestamp 라 버전과
# 달라 자동으로 1회 강제됨.)
_ROLLOUT_MARKER = Path.home() / ".trade" / ".weights_backfilled"
_SCAN_ROLLOUT_VERSION = "2026-07-01-lookback16"   # 16개월 윈도 재적재(YoY 무색 fix)


def _window(lookback_months: int, now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    end = now.strftime("%Y%m")
    y, m = now.year, now.month
    back = lookback_months - 1
    while back > 0:
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        back -= 1
    return f"{y:04d}{m:02d}", end


def _reset_pins_once() -> str | None:
    """Back up + clear the legacy pin file exactly once. Returns a human
    note for the deploy/alert log, or None if nothing to do / already
    migrated."""
    if _MIGRATE_MARKER.exists():
        return None
    entries = hs_map.entries()
    _MIGRATE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    if entries:
        src = Path(
            os.environ.get("TRADE_HS_MAP_PATH")
            or str(Path.home() / ".trade" / "hs_map.tsv")
        )
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = src.with_name(f"hs_map.bak.{ts}.tsv")
        try:
            bak.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("pin backup failed (%s) — not clearing", exc)
            return None
        for item, _code in list(entries):
            hs_map.remove(item)
        _MIGRATE_MARKER.write_text(str(time.time()))
        return f"기존 핀 {len(entries)}개 백업({bak.name}) 후 초기화"
    _MIGRATE_MARKER.write_text(str(time.time()))
    return None


def _rollout_scan_pending() -> bool:
    """현 _SCAN_ROLLOUT_VERSION 의 강제 populate 스윕이 아직이면 True — 배포
    직후 첫 probe 가 관세청 무변경이어도 1회 강제 풀 스윕으로 새 스냅샷 필드를
    즉시 채운다. 마커 내용 != 버전이면 pending. 성공 저장 경로에서만 버전
    기록(graceful) → 실패 시 다음 probe 재시도."""
    try:
        return _ROLLOUT_MARKER.read_text(encoding="utf-8").strip() != _SCAN_ROLLOUT_VERSION
    except OSError:
        return True   # 마커 부재 = pending


def _mark_rollout_done() -> None:
    try:
        _ROLLOUT_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _ROLLOUT_MARKER.write_text(_SCAN_ROLLOUT_VERSION, encoding="utf-8")
    except OSError:
        pass


def _send_alert(body: str) -> bool:
    """Reuse customs_alert's sender + the recorded operator chat.

    operator.get() returns the single recorded operator chat_id (or None
    before the operator has DM'd the bot). The earlier all_ids() call did
    not exist and crashed _send_alert with AttributeError whenever a scan
    produced new entrants (seen 2026-06-01 17:59). Mirror customs_alert,
    which also targets operator.get()."""
    from trade import operator
    from trade.scripts import customs_alert
    chat = operator.get()
    if not chat:
        log.info("no operator chat recorded — skipping surge alert")
        return False
    return customs_alert._send(chat, body)


# 관세청 데이터 갱신 알림 (사용자 2026-06-13) — 채널 포워드 급증 알림과
# 구분되는 별도 헤더('✅ 관세청 데이터 갱신'). 4회/일 스캔이지만 지문
# (확정월+수출입 합+작년동월 합)이 바뀔 때만 발화 → 관세청 실제 갱신
# (~월 3회: 11·21·월초) + YoY 베이스라인 최초 충전에만 1회, 그 외 무음.
_NOTIFY_MARKER = Path.home() / ".trade" / ".scan_notified.json"


def _scan_fingerprint(hm_rows: list[dict], new_entrants: list) -> dict:
    """변경 감지 지문 — 순수. 작년동월 합(sey/siy)을 포함해 YoY 색이
    처음 채워지는 순간도 '갱신'으로 잡는다(사용자가 기다리던 신호)."""
    ref = max((r.get("ref_ym") or "" for r in hm_rows), default="")
    return {
        "ref_ym": ref,
        "se": sum(int(r.get("exp") or 0) for r in hm_rows),
        "si": sum(int(r.get("imp") or 0) for r in hm_rows),
        "sey": sum(int(r.get("exp_py") or 0) for r in hm_rows),
        "siy": sum(int(r.get("imp_py") or 0) for r in hm_rows),
        "ne": len(new_entrants),
    }


def _fingerprint_changed(prev: dict, fp: dict) -> bool:
    """확정월·수출입 합·작년동월 합 중 하나라도 다르면 변경(순수).
    prev 비어있으면(최초/배포 후) True — 1회 갱신 알림 후 안정."""
    if not prev:
        return True
    return any(prev.get(k) != fp.get(k)
               for k in ("ref_ym", "se", "si", "sey", "siy"))


def _maybe_notify_refresh(hm_rows: list[dict], leaves: dict,
                          new_entrants: list, *, send=None) -> bool:
    """변경 시에만 '✅ 관세청 데이터 갱신' 운영자 알림. send 주입(테스트)."""
    import json as _json
    send = send or _send_alert
    fp = _scan_fingerprint(hm_rows, new_entrants)
    try:
        prev = _json.loads(_NOTIFY_MARKER.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    if not _fingerprint_changed(prev, fp):
        return False   # 무변경 — 4회/일 스캔 무음(스팸 차단)
    try:
        from trade.mti_map import industry_of
        n_ind = len({iv for hs in leaves
                     if (iv := industry_of(str(hs)))})
    except Exception:
        n_ind = 0
    yoy_ready = fp["sey"] > 0 or fp["siy"] > 0
    body = (
        "✅ <b>관세청 데이터 갱신</b>\n"
        f"최신 확정월 {fp['ref_ym']} · 산업 {n_ind}개\n"
        "급등률·급증액·산업트렌드·히트맵 갱신"
        + ("" if yoy_ready else " <i>(YoY 대기)</i>")
        + f"\n🔍 신규 급증 {len(new_entrants)}건"
    )
    ok = send(body)
    try:
        _NOTIFY_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _NOTIFY_MARKER.write_text(_json.dumps(fp), encoding="utf-8")
    except OSError:
        pass
    return ok


# 10분 probe 모드 (사용자 2026-06-13 '4회/일보다 빠르게, 리스크 없이') —
# 풀 스윕(~500콜)을 늘리는 대신 1콜 probe(85류 1페이지·최근 2개월)로
# '새 데이터 떴나'만 확인, 변경 시에만 풀 스윕+알림 즉시 발동.
# 비용: probe 144회/일 × 1콜 ≈ 150콜 + 변경 시 스윕(월 ~3회) — 한도 3%.
# 시간당 풀 스윕(~12,000콜/일 = 한도 초과)의 안전한 대체.
_PROBE_MARKER = Path.home() / ".trade" / ".scan_probe.json"
_PROBE_RUN_GUARD = Path.home() / ".trade" / ".scan_probe_run.ts"


def _exc_detail(exc: Exception | None, limit: int = 160) -> str:
    """예외를 알림에 쓸 한 줄로 — 그냥 클래스명("HTTPError")만으로는 서버
    일시장애(5xx)·서비스키 문제(401/403)·트래픽제한(429)을 구분할 수 없어
    사용자가 재발 시 원인을 못 짚었다(2026-07-31 첫 발생 후 개선 요청).

    urllib.error.HTTPError 는 실제 상태코드+사유를 붙이고, 그 외에는 클래스명 +
    **메시지**를 붙인다 — 관세청 CustomsAPIError 는 메시지 자체가
    `resultCode=99 resultMsg='...'` 라는 진단이라, 클래스명만 찍으면 정작 필요한
    사유가 버려진다(2026-08-01 '97챕터 전부 실패' 알림이 정확히 그랬다).
    ⚠️ 알림은 parse_mode=HTML 이라 API 원문의 <,>,& 는 호출부에서 escape 할 것."""
    if exc is None:
        return "Unknown"
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTPError {exc.code} ({exc.reason})"
    name = type(exc).__name__
    msg = str(exc).strip()
    if not msg:
        return name
    if len(msg) > limit:
        msg = msg[:limit - 1] + "…"
    return f"{name}: {msg}"


def probe_fail_kind(exc: Exception | None) -> str:
    """probe 실패 예외 → **갈래** 한 낱말(원장 적립용). 순수·테스트 가능.

    ⚠️ 왜 갈래인가: 결산이 `probe 오류 42회` 라고만 말하면 운영자가 원인을
    짐작해야 하는데, 갈래마다 **처방이 정반대**다 — 타임아웃·원천장애는
    손쓸 게 없고(기다린다), 요청한도는 호출을 줄이고, 인증·키는 키를
    갈아야 한다(#82 · #279 갈래는 이름으로 부를 것).

    ⚠️ 모르는 예외는 **단정하지 않는다** — 클래스명을 그대로 돌려준다
    (#165 재지 않은 귀속을 이름으로 단정하지 말 것). 그러면 결산에 낯선
    낱말이 떠서 그 자체가 '새 실패모드' 신호가 된다.

    ⚠️ 관세청 `resultCode` 는 **코드까지** 싣는다 — 22(요청한도)와
    30/31(키 미등록·기간만료)은 같은 CustomsAPIError 지만 처방이 다르다.
    """
    if exc is None:
        return "미상"
    if isinstance(exc, urllib.error.HTTPError):
        code = getattr(exc, "code", 0) or 0
        if code == 429:
            return "요청한도"
        if code in (401, 403):
            return "인증·키"
        if 500 <= code < 600:
            return f"원천장애 {code}"
        return f"요청오류 {code}"
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return "타임아웃"
    if isinstance(exc, urllib.error.URLError):
        # URLError 는 reason 에 진짜 원인이 들어 있다(타임아웃·DNS·연결거부).
        # HTTPError 는 URLError 의 하위라 위에서 이미 갈렸다.
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "타임아웃"
        return "네트워크"
    if isinstance(exc, customs.CustomsAPIError):
        # ⚠️ `customs._http_get` 은 HTTPError 를 **CustomsAPIError 로 재포장**
        # 한다(`raise … from exc`) — 그래서 프로덕션 probe 실패는 위 HTTPError
        # 분기에 **한 번도 도달하지 않는다**. 429·403·5xx 가 전부 '원천응답'
        # 한 통에 담기면 이 갈래 기능 자체가 무의미해진다(2026-09-07 독립
        # 리뷰 실측). 원인은 `__cause__` 에 살아 있으므로 풀어서 다시 본다
        # (#20 배선은 태워야 보인다 · #141 호출이 있어도 조건이 막으면 안 돈다).
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, (urllib.error.URLError, TimeoutError,
                              socket.timeout)):
            return probe_fail_kind(cause)
        msg = str(exc)
        if "not set" in msg:
            return "인증·키"
        # resultCode 는 `INFO-00`·`ERROR-300` 처럼 하이픈을 쓰기도 한다.
        m = re.search(r"resultCode=([\w-]+)", msg)
        return f"원천응답 {m.group(1)}" if m else "원천응답"
    if isinstance(exc, ET.ParseError):
        return "응답파싱"
    return type(exc).__name__


def _err_summary(errs: list, top: int = 3) -> str:
    """실패 사유 리스트 → 알림 본문 한 덩어리(빈도순 상위 top). 순수·테스트 가능.

    97챕터가 같은 이유로 죽는 게 보통이라 전부 나열할 필요는 없고, 사유별 건수만
    보이면 서비스키 문제인지 일시 장애인지 즉시 갈린다. ⚠️ 알림이 parse_mode=HTML
    이므로 API 원문에 섞인 <,>,& 를 여기서 escape 한다(실수#7)."""
    import html as _html
    from collections import Counter
    if not errs:
        return "사유 미확인"
    cnt = Counter(errs)
    # quote=False — 텔레그램 HTML 은 <,>,& 만 escape 하면 되고, 따옴표까지 바꾸면
    # resultMsg='...' 가 &#x27; 범벅이 돼 읽기 어렵다.
    parts = [f"· {_html.escape(reason, quote=False)} ×{n}"
             for reason, n in cnt.most_common(top)]
    if len(cnt) > top:
        parts.append(f"· 그 외 {len(cnt) - top}종")
    return "\n".join(parts)


def _probe_fingerprint(key: str) -> dict | None:
    """1콜 — 85류(전기전자, 최대 챕터·매월 필수 존재) 최근 2개월 1페이지.
    지문 = 최신 실월(미래 0행 제외) + 그 월 수출입 합. 관세청이 새 월을
    싣거나 기존 월을 정정하면 변함. 실패 시 None(보수적 — 스윕 안 함)."""
    now = datetime.now(timezone.utc)
    end = now.strftime("%Y%m")
    y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    start = f"{y:04d}{m:02d}"
    # data.go.kr 일시적 지연(20초 타임아웃 1회 초과) false alarm 감소 — 2회 시도 후에만
    # 알림(probe 는 5분마다라 1회 transient 는 안전망에 흡수, 진짜 장애만 알림).
    rows = None
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            rows = customs_scan.fetch_chapter("85", start, end, key=key,
                                              max_pages=1)
            break
        except Exception as exc:
            last_exc = exc
            if _attempt == 0:
                time.sleep(3)
    if rows is None:
        log.warning("probe fetch failed (2회): %s", last_exc)
        try:
            from trade import run_ledger
            first = run_ledger.bump("probe_fail") == 1   # 일 1회 dedup
            if first:
                _send_alert(f"❌ <b>관세청 probe 오류</b>\n{_exc_detail(last_exc)}"
                            " — 정기 4회/일 풀스윕이 안전망으로 계속 작동")
            # 갈래를 같이 적립 — 결산이 `probe 오류 42회(타임아웃 40 · …)`
            # 로 처방까지 말하게 한다(#82 · #279). ⚠️ 순서 둘: **총계를 먼저**
            # 올려야 소계가 총계를 넘지 않고(#45), **알림 뒤에** 둬야 적립이
            # 던져도 일 1회 운영자 알림이 조용히 사라지지 않는다(이 블록 전체가
            # `except Exception: pass` 안이다 — silent-fail 금지, #12).
            run_ledger.bump_kind("probe_fail", probe_fail_kind(last_exc))
        except Exception:
            pass
        return None
    cur_cal = now.strftime("%Y-%m")
    best_ym = ""
    sums: dict[str, list[int]] = {}
    for r in rows:
        ym = r.get("year_month") or ""
        if not ym or ym > cur_cal:      # 미래 월 0-행 (2026-06-01 클래스)
            continue
        se_si = sums.setdefault(ym, [0, 0])
        se_si[0] += int(r.get("exp_dlr") or 0)
        se_si[1] += int(r.get("imp_dlr") or 0)
    for ym, (se, si) in sums.items():
        if (se or si) and ym > best_ym:
            best_ym = ym
    if not best_ym:
        return None
    return {"ym": best_ym, "se": sums[best_ym][0], "si": sums[best_ym][1]}


def _probe_says_skip(key: str) -> bool:
    """--if-changed 게이트 — True 면 스윕 생략(데이터 무변경/판단불가/
    스윕 in-flight). 순수 비교 + 30분 run-guard."""
    import json as _json
    fp = _probe_fingerprint(key)
    if fp is None:
        return True     # probe 실패 — 보수적 skip (정기 4회/일 풀스윕이 안전망)
    try:
        prev = _json.loads(_PROBE_MARKER.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    if prev == fp:
        log.info("probe: 무변경 (%s) — 스윕 생략", fp["ym"])
        return True
    try:
        if (_PROBE_RUN_GUARD.exists()
                and time.time() - _PROBE_RUN_GUARD.stat().st_mtime < 1800):
            log.info("probe: 변경 감지했으나 스윕 in-flight(<30m) — skip")
            return True
        _PROBE_RUN_GUARD.parent.mkdir(parents=True, exist_ok=True)
        _PROBE_RUN_GUARD.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass
    log.info("probe: 변경 감지 (%s) — 풀 스윕 발동", fp)
    return False


def _probe_save(key: str) -> None:
    """스윕 성공 후 probe 지문 저장 — 정기 풀스윕 직후에도 갱신해 다음
    probe 가 중복 발동하지 않게 (모드 무관 호출)."""
    import json as _json
    fp = _probe_fingerprint(key)
    if fp is None:
        return
    try:
        _PROBE_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _PROBE_MARKER.write_text(_json.dumps(fp), encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--if-changed", action="store_true",
                        help="1콜 probe 로 변경 감지 시에만 풀 스윕 "
                             "(10분 타이머용 — 무변경이면 즉시 종료)")
    parser.add_argument("--keep-pins", action="store_true",
                        help="skip the one-time legacy-pin reset")
    parser.add_argument("--top-n", type=int, default=customs_scan.TOP_N)
    parser.add_argument("--pct", type=float, default=customs_scan.PCT_THRESHOLD)
    parser.add_argument("--lookback-months", type=int, default=LOOKBACK_MONTHS_DEFAULT)
    parser.add_argument("--db", default=None)
    parser.add_argument("--max-chapters", type=int, default=len(customs_scan.CHAPTERS),
                        help="limit chapters scanned (testing/throttling)")
    parser.add_argument(
        "--min-coverage", type=float,
        default=float(os.environ.get("TRADE_CUSTOMS_SCAN_MIN_COVERAGE") or "0.9"),
        help="min fraction of chapters that must succeed before the scan "
             "may overwrite the live snapshot (default 0.9; a partial scan "
             "below this keeps the last good snapshot)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    key = os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        log.warning("TRADE_DATA_GO_KR_KEY not set — skip")
        return 0

    # 단가 중량 1회 백필 — 마커 부재면 관세청 무변경이어도 강제 풀 스윕.
    force_rollout = _rollout_scan_pending()
    if args.if_changed and not force_rollout and _probe_says_skip(key):
        return 0
    if force_rollout:
        log.info("rollout populate pending (%s) — 강제 풀 스윕 1회",
                 _SCAN_ROLLOUT_VERSION)

    start, end = _window(args.lookback_months)
    chapters = customs_scan.CHAPTERS[: args.max_chapters]

    all_rows: list[dict] = []
    ok = fail = 0
    errs: list[str] = []          # 실패 사유 — 알림에 실어 원인을 바로 보이게
    kinds: list[str] = []         # 실패 **갈래** — 결산이 처방을 말하게(#82·#290)
    for ch in chapters:
        try:
            # fetch_chapter_range — 13개월 윈도를 ≤12개월로 쪼개 호출.
            # 관세청이 1년 초과 조회를 resultCode 99 로 전부 거부해
            # ok=0 fail=97 rows=0 (히트맵 영구 empty) 였던 근본 원인:
            # splitter(_split_windows)는 구현·테스트돼 있었는데 이
            # 호출부만 raw fetch_chapter 를 쓰던 배선 누락 (2026-06-12
            # scan_customs_kick.log 로 적발).
            all_rows.extend(
                customs_scan.fetch_chapter_range(ch, start, end, key=key))
            ok += 1
        except Exception as exc:
            fail += 1
            errs.append(_exc_detail(exc))
            kinds.append(probe_fail_kind(exc))
            log.warning("chapter %s failed: %s", ch, exc)
    log.info("scan: chapters ok=%d fail=%d rows=%d", ok, fail, len(all_rows))
    from trade import run_ledger
    if ok == 0:
        # 오류 알람 (사용자 2026-06-13) — 일 1회 dedup(원장 첫 발생만).
        # 옛 문구는 운영자에게 journal 을 직접 열어보라고만 해서 VM 에 들어가
        # 로그를 뒤져야 했다(자동화 원칙 위반 — 알림이 스스로 원인을 말해야 한다,
        # 사용자 2026-08-01). 실패 사유를 빈도순으로 요약해 싣는다.
        if run_ledger.bump("scan_fail") == 1:
            _send_alert("❌ <b>관세청 스캔 실패</b>\n"
                        f"{len(chapters)}챕터 전부 실패 (이전 스냅샷은 유지됨)\n"
                        f"{_err_summary(errs)}")
        # 결산이 `스캔 전체실패 3회` 라고만 말하면 처방을 못 고른다(#290).
        # ⚠️ 총계는 **실행 1회당 1** 이므로 갈래도 1개만 — 챕터마다 적립하면
        # 소계가 총계를 훌쩍 넘는다(#45). 가장 흔한 갈래가 그 실행을 대표한다.
        if kinds:
            from collections import Counter
            run_ledger.bump_kind("scan_fail", Counter(kinds).most_common(1)[0][0])
        return 1
    coverage = ok / (ok + fail) if (ok + fail) else 0.0

    leaves = customs_scan.build_series(all_rows)
    ranked = customs_scan.rank(leaves, top_n=args.top_n, pct_threshold=args.pct)
    # 수입 방향 랭킹 병합 (사용자 2026-06-13) — 같은 leaves(imp_dlr) 재사용,
    # API 0. store/archive/seen/alert 가 section 단위라 추가만으로 동작.
    ranked.update(customs_scan.rank(leaves, top_n=args.top_n,
                                    pct_threshold=args.pct, direction="import"))
    log.info("ranked: rate=%d amount=%d rate_imp=%d amount_imp=%d (leaves=%d)",
             len(ranked[customs_scan.SECTION_RATE]),
             len(ranked[customs_scan.SECTION_AMOUNT]),
             len(ranked.get(customs_scan.SECTION_RATE_IMP, [])),
             len(ranked.get(customs_scan.SECTION_AMOUNT_IMP, [])), len(leaves))

    if args.dry_run:
        hist = customs_scan.floor_histogram(leaves, pct_threshold=args.pct)
        print(f"[dry-run] leaves={len(leaves)} "
              f"rate(+{args.pct:.0f}%, ≥{customs.fmt_usd(customs_scan.RATE_MIN_USD)})"
              f"={len(ranked[customs_scan.SECTION_RATE])} "
              f"amount={len(ranked[customs_scan.SECTION_AMOUNT])}")
        print("[dry-run] +%.0f%% surges surviving each export floor:" % args.pct)
        for floor, cnt in sorted(hist.items()):
            print(f"  ≥ {customs.fmt_usd(floor)}: {cnt}건")

        # Preview the ACTUAL top items that would be registered this run —
        # dry-run writes nothing, but this shows what the live panel would
        # contain so the operator can sanity-check before enabling.
        def _preview(title: str, rows: list[dict], metric: str) -> None:
            print(f"\n[dry-run] {title} — 실제 등록 예정 (상위 {len(rows)}):")
            if not rows:
                print("  (해당 없음)")
                return
            for i, m in enumerate(rows, 1):
                move = (
                    customs.fmt_pct(m["pct"]) if metric == "pct"
                    else "Δ" + customs.fmt_usd(m["delta"])
                )
                print(
                    f"  {i:2d}. {m['name']} ({m['hs_code']}) "
                    f"{customs.fmt_usd(m['prev'])}→{customs.fmt_usd(m['curr'])} "
                    f"[{move}]"
                )

        _preview("📈 급등률 TOP", ranked[customs_scan.SECTION_RATE], "pct")
        _preview("💵 급증액 TOP", ranked[customs_scan.SECTION_AMOUNT], "amount")
        return 0

    db = Path(args.db) if args.db else customs.DEFAULT_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    note = None
    if not args.keep_pins:
        note = _reset_pins_once()
        if note:
            log.info("migration: %s", note)

    # Coverage guard: a partial scan (many chapters failed — API outage or
    # FloodWait storm) ranks only the chapters that DID respond, which then
    # overwrites a complete prior snapshot, dropping every item from the
    # missing chapters. Observed 2026-06-01 17:59: ok=9 fail=88 → live went
    # 24→4 items (only ch 90/93 survived) AND mis-fired 27 'new entrant'
    # alerts. Below the coverage floor, keep the last good snapshot.
    if coverage < args.min_coverage:
        log.warning(
            "chapter coverage %.0f%% (ok=%d fail=%d) < %.0f%% floor — "
            "partial scan, keeping previous live snapshot (no store/alert)",
            coverage * 100, ok, fail, args.min_coverage * 100,
        )
        if run_ledger.bump("scan_partial") == 1:
            _send_alert(f"⚠️ <b>관세청 부분 스캔</b>\n커버리지 "
                        f"{coverage * 100:.0f}% (ok={ok} fail={fail}) — "
                        "이전 스냅샷 유지, 다음 스캔 재시도")
        # 부분 스캔은 예외가 아니라 **커버리지 게이트**다 — 그래도 어느 갈래가
        # 챕터를 죽였는지는 결산이 말해야 한다(#82). 갈래를 못 세면 게이트
        # 자체를 사유로 남긴다(침묵이 최악, #43).
        if kinds:
            from collections import Counter
            run_ledger.bump_kind("scan_partial",
                                 Counter(kinds).most_common(1)[0][0])
        else:
            run_ledger.bump_kind("scan_partial", "커버리지미달")
        return 0

    empty = not any(ranked.get(s) for s in (
        customs_scan.SECTION_RATE, customs_scan.SECTION_AMOUNT,
        customs_scan.SECTION_RATE_IMP, customs_scan.SECTION_AMOUNT_IMP))
    with customs.session(db) as conn:
        customs_scan.init_db(conn)
        if empty:
            # Defensive: an all-empty ranking (e.g. a transient API hiccup,
            # or early in a month before any month has confirmed figures)
            # must NOT wipe the last good live snapshot. Skip store_live so
            # the panel keeps showing the most recent real ranking until a
            # non-empty scan replaces it. Archive/alerts also no-op here.
            log.warning("ranking empty (rate=0 amount=0) — keeping previous "
                        "live snapshot, skipping store/archive")
            return 0
        customs_scan.store_live(conn, ranked)
        archived = customs_scan.upsert_archive(conn, ranked)
        new_entrants = customs_scan.eval_new_entrants(conn, ranked)
        # 히트맵 leaf 스냅샷 (2026-06-12) — 같은 스윕 데이터 재사용(API 0).
        # ranking 비어있지 않은(=커버리지 양호) 경로에서만 교체 저장.
        hm_rows = customs_scan.heatmap_rows(leaves)
        customs_scan.store_heatmap(conn, hm_rows)
    log.info("stored live; archived=%d new_entrants=%d heatmap=%d",
             archived, len(new_entrants), len(hm_rows))
    if force_rollout:
        _mark_rollout_done()            # 성공 저장 후에만 (실패=다음 probe 재시도)
        log.info("rollout populate 완료 (%s)", _SCAN_ROLLOUT_VERSION)

    # 작동 원장 (자정 결산용) — 스윕 1·신규급증 n
    try:
        run_ledger.bump("sweeps")
        if new_entrants:
            run_ledger.bump("entrants", len(new_entrants))
    except Exception:
        pass

    # 관세청 데이터 갱신 알림 (변경 감지 시 1회) — 급증 알림과 별도 헤더.
    try:
        if _maybe_notify_refresh(hm_rows, leaves, new_entrants):
            run_ledger.bump("refresh")
    except Exception as exc:
        log.warning("refresh-notify failed (non-fatal): %s", exc)

    if new_entrants:
        body = customs_scan.format_alert(new_entrants)
        _send_alert(body)
    # probe 지문 저장 (1콜) — 정기/probe 모드 모두, 스윕 성공 경로에서만.
    try:
        _probe_save(key)
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
