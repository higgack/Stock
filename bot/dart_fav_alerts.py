"""관심종목 DART 공시 알림 — /dart_alert on|off (사용자 2026-06-11).

#19(소송/리스크 전 종목 채널 푸시)의 교체. 전 종목 무차별 알림은 스팸인
데다, '신규' 판정을 휘발성 큐 파일로 해 봇 재시작마다 같은 알림이 통째로
재발송되는 버그가 있었음 → 제거하고 관심종목(market_favorites) 한정으로
피드가 수집하는 **모든 카테고리** 공시를 알림.

설계 (큐 파일 없음 — 프로세스 간 레이스 구조적 제거):
- dart-feed 타이머가 쓰는 아카이브(SSoT)를 봇 asyncio 태스크가 75초 주기로
  스캔. 별도 DART 호출 0 (비용 ₩0).
- 영구 seen-set(rcept_no)으로 재시작/자정 재발송 차단.
- 활성화 시 + 관심종목에 새 종목 추가 시, 그 종목의 기존 공시는 seed
  (무발송) — blog/reddit watcher 의 '첫 run seed' 패턴. 이후 신규만 알림.
- DART 는 KR 전용이라 관심종목 중 KR 6자리 코드만 매칭 (US 티커 무시).

저장: ~/.tradingagents/dart_fav_alerts.json
  {"enabled": bool, "chat_id": int, "codes": [...], "seen": [...]}
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("bot.dart_fav_alerts")

_STATE_FILE = Path.home() / ".tradingagents" / "dart_fav_alerts.json"
_SEEN_CAP = 5000          # rcept_no 보존 상한 (FIFO)
_SEND_CAP_PER_POLL = 10   # 폴당 발송 상한 — 초과분은 seen 미기록 → 다음 폴
_SCAN_DAYS_BACK = 3       # 아카이브 스캔 윈도 (피드 fetch 윈도와 동일 계열)

_KR_TICKER_RE = re.compile(r"^(\d{6})(?:\.(?:KS|KQ))?$", re.IGNORECASE)


def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            st = json.loads(_STATE_FILE.read_text("utf-8"))
            if isinstance(st, dict):
                return st
    except Exception:
        pass
    return {"enabled": False, "chat_id": None, "codes": [], "seen": []}


def _save_state(st: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if len(st.get("seen", [])) > _SEEN_CAP:
            st["seen"] = st["seen"][-_SEEN_CAP:]
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
        tmp.replace(_STATE_FILE)
    except Exception as exc:
        log.warning("dart_fav_alerts: state save failed: %s", exc)


# 관심종목 목록을 못 읽었을 때의 마지막 사유 — `enable`·`status` 가 사람 말로
# 옮긴다. 경고는 사유별로 한 시간에 한 번(75초 폴마다 같은 줄이 쌓이지 않게,
# #317 '모델당 1회' 선례).
_LAST_FAV_ERROR = ""
_FAV_WARNED: dict = {}
_FAV_WARN_EVERY = 3600


def fav_kr_codes() -> "set[str] | None":
    """관심종목 중 KR 6자리 코드 집합 ('005930.KS'/'247540' → '005930'...).

    ⚠️ 목록을 **못 읽으면 None** 이다 — 빈 집합(관심종목이 정말 없다)과 갈라야
    한다. 옛 판은 실패를 빈 집합으로 접어 `poll_new` 가 상태의 `codes` 를 [] 로
    덮었고, 파일이 돌아오면 전 종목을 '새로 담긴 종목' 으로 seed 해 **그 사이 공시를
    영영 안 보냈다**(독립 델타 리뷰 2026-10-06 M1, 실수 #436 — 못 읽음 ≠ 비었음,
    #82). 사유는 `_LAST_FAV_ERROR` 에 남는다.
    """
    global _LAST_FAV_ERROR
    try:
        from bot import market_favorites as _mf
    except Exception as exc:                                   # noqa: BLE001
        why = f"{type(exc).__name__}: {exc}"
    else:
        try:
            codes: set[str] = set()
            for f in _mf.get_favorites(strict=True) or []:
                m = _KR_TICKER_RE.match(str(f.get("ticker", "")).strip())
                if m:
                    codes.add(m.group(1))
            _LAST_FAV_ERROR = ""
            return codes
        except Exception as exc:                               # noqa: BLE001
            # 화면(대시보드 API)과 같은 문장 — 규칙은 `error_text` 한 곳(#38).
            why = _mf.error_text(exc)
    _LAST_FAV_ERROR = why[:300]
    now = time.time()
    if now - _FAV_WARNED.get(_LAST_FAV_ERROR, 0.0) >= _FAV_WARN_EVERY:
        _FAV_WARNED[_LAST_FAV_ERROR] = now
        log.warning("dart_fav_alerts: 관심종목 목록을 못 읽어 이번 폴을 건너뛴다"
                    "(상태 그대로 — 같은 사유는 한 시간에 한 번만 적는다): %s",
                    _LAST_FAV_ERROR)
    return None


def _scan_archive_items() -> list[dict]:
    try:
        from bot.dart_feed import load_all_archives
        out: list[dict] = []
        for items in load_all_archives(days_back=_SCAN_DAYS_BACK).values():
            out.extend(items)
        return out
    except Exception as exc:
        log.warning("dart_fav_alerts: archive scan failed: %s", exc)
        return []


def _ids_for_codes(codes: set[str]) -> set[str]:
    return {str(it.get("rcept_no"))
            for it in _scan_archive_items()
            if it.get("stock_code") in codes and it.get("rcept_no")}


def enable(chat_id: int) -> dict:
    """알림 활성화 — 현재 관심종목의 기존 공시를 전부 seed(무발송).

    관심종목 목록을 못 읽으면 **켜지 않고**(상태 그대로) 사유를 돌려준다 — 빈 목록
    으로 켜면 복구 뒤 전 종목이 '새 종목' seed 가 된다(`fav_kr_codes`)."""
    st = _load_state()
    codes = fav_kr_codes()
    if codes is None:
        # `error` 는 사유 그대로 — 틀(켜지 못했다)은 부르는 쪽이 댄다(텔레그램 답).
        return {"codes": 0, "seeded": 0,
                "error": _LAST_FAV_ERROR or "관심종목 목록을 못 읽었습니다"}
    seeded = _ids_for_codes(codes)
    seen = set(st.get("seen", []))
    st.update({
        "enabled": True,
        "chat_id": int(chat_id),
        "codes": sorted(codes),
        "seen": list(seen | seeded),
    })
    _save_state(st)
    return {"codes": len(codes), "seeded": len(seeded - seen)}


def disable() -> None:
    st = _load_state()
    st["enabled"] = False
    _save_state(st)


def status() -> dict:
    """알림 상태. `codes` 는 목록을 못 읽으면 None 이고 사유가 `codes_error` 에 —
    0 으로 적으면 '관심종목이 없다' 로 읽힌다(#82). 이 함수는 결산·보고서가
    `chat_id` 를 꺼내러 부르므로 던지지 않는다."""
    st = _load_state()
    codes = fav_kr_codes()
    return {"enabled": bool(st.get("enabled")),
            "chat_id": st.get("chat_id"),
            "codes": len(codes) if codes is not None else None,
            "codes_error": _LAST_FAV_ERROR if codes is None else "",
            "seen": len(st.get("seen", []))}


def poll_new() -> tuple[list[dict], int | None]:
    """신규 관심종목 공시 → (발송 대상, chat_id). 비활성 시 ([], None).

    관심종목에 '새로 추가된' 종목의 기존 공시는 seed 만 하고 발송하지 않음
    (추가 시점 과거 공시 폭주 방지). 폴당 발송 상한 초과분은 seen 미기록
    → 다음 폴(75초)이 이어서 발송.
    """
    st = _load_state()
    if not st.get("enabled") or not st.get("chat_id"):
        return [], None

    codes = fav_kr_codes()
    if codes is None:
        # 목록을 못 읽은 폴은 **상태를 건드리지 않고** 건너뛴다 — `codes` 를 [] 로
        # 덮으면 복구 뒤 전 종목이 '새로 담긴 종목' seed 가 돼 그 사이 공시가
        # 사라진다(델타 리뷰 M1). 복구되면 날짜 가드(어제 이후) 안의 공시는 보낸다.
        return [], st.get("chat_id")
    known = set(st.get("codes", []))
    seen = set(st.get("seen", []))
    dirty = False

    new_codes = codes - known
    if new_codes:
        seeded = _ids_for_codes(new_codes)
        seen |= seeded
        dirty = True
        log.info("dart_fav_alerts: %d new favorite(s) seeded (%d 공시)",
                 len(new_codes), len(seeded))
    if codes != known:
        st["codes"] = sorted(codes)
        dirty = True

    # 날짜 가드 — 접수일이 그제 이전인 항목은 알림 제외(seed 만). 백필이
    # 과거 공시를 늦게 추가해도 '신규'로 오인해 폭주하지 않게 (2026-06-11).
    from datetime import datetime, timedelta, timezone
    _kst = timezone(timedelta(hours=9))
    _cutoff = (datetime.now(_kst).date() - timedelta(days=1)).strftime("%Y%m%d")

    fresh: list[dict] = []
    for it in _scan_archive_items():
        rno = str(it.get("rcept_no") or "")
        if not rno or it.get("stock_code") not in codes or rno in seen:
            continue
        if str(it.get("date") or "")[:8] < _cutoff:
            seen.add(rno)   # 오래된 늦참(백필) — 무발송 seed
            dirty = True
            continue
        fresh.append(it)
    # 접수번호 오름차순(시간순) 발송, 상한 초과분은 다음 폴로 이월
    fresh.sort(key=lambda x: str(x.get("rcept_no", "")))
    send_now = fresh[:_SEND_CAP_PER_POLL]
    for it in send_now:
        seen.add(str(it.get("rcept_no")))
        dirty = True

    if dirty:
        st["seen"] = list(seen)
        _save_state(st)
    return send_now, st.get("chat_id")
