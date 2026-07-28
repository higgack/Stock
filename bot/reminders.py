"""알람(리마인더) — 카드별 메모 알람 서버 저장 + due 판정 (사용자 2026-06-26).

★/📝 와 동일 surface/id 키. 사용자가 카드에서 시각(HH:MM, KST)을 지정 → 매일 그 시각에
텔레그램으로 메모+카드내용 발송. ✅확인 누르면 종료, 미확인 시 다음날 같은 시각 재발송.
대시보드 서버(쓰기)와 봇 스케줄러(읽기·발송)가 ~/.tradingagents 공유 파일로 통신.

    { "<surface>": { "<id>": {"time":"HH:MM","memo":..,"card":..,"active":bool,
                              "last_sent":"YYYY-MM-DD","created":ts} } }
모든 시각 = KST.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

from bot.important_marks import SURFACES, _MAX_ID

log = logging.getLogger("bot.reminders")

_KST = timezone(timedelta(hours=9))
_FILE = Path.home() / ".tradingagents" / "reminders.json"
_LOCK = threading.Lock()
# 알람 시각 2종(KST):
#   매일   = 'HH:MM'           (매일 그 시각 반복)         예 08:17
#   특정일 = 'MM.DD.HH:MM'     (그 날짜부터 매일 그 시각)  예 06.26.04:30
_DT_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{1,2}):(\d{2})$")
_HM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_MAX_TEXT = 8000


def _parse_dt(s: str):
    """'MM.DD.HH:MM' → (month, day, hour, minute) 정수 튜플, 무효는 None."""
    m = _DT_RE.match((s or "").strip())
    if not m:
        return None
    mo, da, hh, mi = (int(x) for x in m.groups())
    if 1 <= mo <= 12 and 1 <= da <= 31 and 0 <= hh <= 23 and 0 <= mi <= 59:
        return (mo, da, hh, mi)
    return None


def _parse_hm(s: str):
    """'HH:MM'(매일) → (hour, minute), 무효는 None."""
    m = _HM_RE.match((s or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _norm_time(s: str) -> str:
    """입력 정규화 → 'MM.DD.HH:MM'(특정일) 또는 'HH:MM'(매일), 0패딩. 무효는 ''."""
    p = _parse_dt(s)
    if p:
        return "%02d.%02d.%02d:%02d" % p
    hm = _parse_hm(s)
    if hm:
        return "%02d:%02d" % hm
    return ""


def key_of(surface: str, rem_id: str) -> str:
    """텔레그램 callback_data 용 짧은 키(≤64B). surface/id → 16hex."""
    return hashlib.sha1((surface + "\x1f" + rem_id).encode("utf-8")).hexdigest()[:16]


def _load() -> dict:
    if _FILE.exists():
        try:
            d = json.loads(_FILE.read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(_FILE)


def all_reminders() -> dict[str, dict[str, dict]]:
    """{surface: {id: {time, active}}} — 클라가 ⏰ 표시·시각 prefill 용(본문 제외)."""
    with _LOCK:
        data = _load()
    out: dict[str, dict[str, dict]] = {}
    for surf, items in data.items():
        if isinstance(items, dict):
            out[surf] = {k: {"time": v.get("time", ""), "active": bool(v.get("active"))}
                         for k, v in items.items() if isinstance(v, dict)}
    return out


def set_reminder(surface: str, rem_id: str, time_str: str, on: bool,
                 memo: str = "", card: str = "") -> dict:
    """알람 설정/해제. time='MM.DD.HH:MM'(KST). on=False 또는 무효 → 삭제.
    반환 {ok, active, time}."""
    surface = (surface or "").strip()
    rem_id = (rem_id or "").strip()
    norm = _norm_time(time_str)
    if surface not in SURFACES or not rem_id or len(rem_id) > _MAX_ID:
        return {"ok": False, "error": "invalid surface/id"}
    with _LOCK:
        data = _load()
        bucket = data.get(surface)
        if not isinstance(bucket, dict):
            bucket = {}
            data[surface] = bucket
        if on and norm:
            prev = bucket.get(rem_id) if isinstance(bucket.get(rem_id), dict) else {}
            bucket[rem_id] = {
                "time": norm,
                "memo": (memo or prev.get("memo", ""))[:_MAX_TEXT],
                "card": (card or prev.get("card", ""))[:_MAX_TEXT],
                "active": True,
                "last_sent": "" if prev.get("time") != norm else prev.get("last_sent", ""),
                "created": prev.get("created", int(time.time())),
            }
            res = {"ok": True, "active": True, "time": norm}
        else:
            bucket.pop(rem_id, None)
            if not bucket:
                data.pop(surface, None)
            res = {"ok": True, "active": False, "time": ""}
        _save(data)
    return res


def due(now_dt: datetime, today: str) -> list[dict]:
    """오늘(today) 미발송 활성 알람 중 발송 대상. 지정 날짜(MM.DD) 도달 이후, 매일
    지정 시각(HH:MM, KST) 에 발송 — 확인 전까지 다음날 같은 시각 재발송. 봇이 정확한
    분을 놓쳐도 같은날 따라잡도록 그날 now 시각 >= 지정 시각이면 발송 대상."""
    now_hm = now_dt.hour * 60 + now_dt.minute
    out = []
    with _LOCK:
        data = _load()
    for surf, items in data.items():
        if not isinstance(items, dict):
            continue
        for rid, v in items.items():
            if not isinstance(v, dict) or not v.get("active"):
                continue
            if v.get("last_sent") == today:
                continue
            t = v.get("time", "")
            p = _parse_dt(t)
            if p:                                   # 특정일 MM.DD.HH:MM
                mo, da, hh, mi = p
                ok = (now_dt.month, now_dt.day) >= (mo, da) and now_hm >= hh * 60 + mi
            else:                                   # 매일 HH:MM
                hm = _parse_hm(t)
                ok = bool(hm) and now_hm >= hm[0] * 60 + hm[1]
            if ok:
                out.append({"surface": surf, "id": rid, "time": t,
                            "memo": v.get("memo", ""), "card": v.get("card", ""),
                            "key": key_of(surf, rid)})
    return out


def mark_sent(surface: str, rem_id: str, today: str) -> None:
    with _LOCK:
        data = _load()
        v = data.get(surface, {}).get(rem_id)
        if isinstance(v, dict):
            v["last_sent"] = today
            _save(data)


def confirm_by_key(key: str) -> dict | None:
    """✅확인 콜백 — key 로 알람 찾아 종료(삭제). 반환 종료된 알람 정보 or None."""
    with _LOCK:
        data = _load()
        for surf, items in list(data.items()):
            if not isinstance(items, dict):
                continue
            for rid in list(items.keys()):
                if key_of(surf, rid) == key:
                    info = items.pop(rid)
                    if not items:
                        data.pop(surf, None)
                    _save(data)
                    return {"surface": surf, "id": rid, "info": info}
    return None
