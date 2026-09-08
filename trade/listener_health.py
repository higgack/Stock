"""BeOn 리스너 상태 판정 — **의존성 없는 순수 모듈**.

⚠️ 왜 여기 있나. 판정을 `listen_beon.py` 안에 두면 telethon 이 없는
환경에서 import 자체가 안 돼 회귀가 통째로 스킵된다 — 감사가 화면과
같은 함수를 쓰려면 그 함수가 **떼어 태울 수 있어야** 한다(#176·#41).
"""

from __future__ import annotations

import re

# ⚠️ 왜 있나. 2026-09-08 에 리스너 상태를 물으려고 `systemctl --user status
# trade-bot-beon-listener` 를 안내했다가 `Unit ... could not be found` 만
# 받았다 — 유닛은 **시스템** 스코프(`WantedBy=multi-user.target`)인데 내가
# 스코프를 추측한 것이다. 반복될 확인은 명령을 건네는 게 아니라 제품에
# 심는다(§Automation-first · #252 · #12). 읽기 전용 — 아무것도 시작·설치하지
# 않는다(#264).
_UNIT = "trade-bot-beon-listener"
_SERVICE = f"{_UNIT}.service"

# LoadState 갈래마다 처방이 정반대다 — 하나로 뭉뚱그리면 `masked` 에
# `enable --now` 를 시켜 실패한다(#82).
_LOAD_FIX = {
    "not-found": (f"유닛이 없다 — `systemctl status {_SERVICE}` 로 확인하고, "
                  f"없으면 `deploy/install-trade-units.sh`. ⚠️ `--user` 를 "
                  f"붙이면 **시스템 유닛이 안 보인다**(그 스코프엔 없다)."),
    "masked": f"마스크부터 해제: `sudo systemctl unmask {_SERVICE}` 뒤 "
              f"`sudo systemctl enable --now {_UNIT}`",
    "error": "유닛 파일을 못 읽었다 — `sudo systemctl daemon-reload`",
    "bad-setting": f"유닛 파일 설정 오류 — `systemctl status {_SERVICE}`",
}


def listener_verdict(facts: dict, unit: str = _UNIT) -> dict:
    """long-running 리스너의 상태를 **갈래로** 말한다(#82). 순수 함수.

    타이머(`Type=oneshot`)와 의미가 다르다 — 여기선 `active/running` 이
    정상이고, `activating/auto-restart` 는 **재시작 루프**(#258 의 증폭기)다.
    판정 불가는 통과가 아니다(#54·#12).
    """
    if not facts.get("ok"):
        return {"kind": "unknown",
                "text": (f"systemd 에 못 물었다({facts.get('err') or '사유 미상'})"
                         f" — `systemctl status {unit}.service`")}
    load = facts.get("s_LoadState") or "?"
    if load != "loaded":
        return {"kind": {"not-found": "not_installed"}.get(load, load.replace("-", "_")),
                "text": f"유닛 상태가 `{load}` 다 — "
                        + _LOAD_FIX.get(load, f"`systemctl status {unit}.service`")}
    active = facts.get("s_ActiveState") or "?"
    sub = facts.get("s_SubState") or "?"
    started = facts.get("s_ExecMainStartTimestamp") or "?"
    if active == "active" and sub == "running":
        return {"kind": "running",
                "text": f"살아 있다(ActiveState={active} SubState={sub}, "
                        f"시작 {started})"}
    if sub == "auto-restart" or active == "activating":
        # RestartSec=15 — 이 루프가 매 실행 username 해석을 다시 쏜다(#258).
        return {"kind": "restart_loop",
                "text": (f"**재시작 루프**다(ActiveState={active} SubState={sub}) "
                         f"— 아래 저널의 실패 사유부터 볼 것")}
    status = str(facts.get("s_ExecMainStatus") or "")
    result = facts.get("s_Result") or ""
    if status == "78":
        # 유닛이 RestartPreventExitStatus=78 로 hot-loop 를 막는다 —
        # 이걸 '설치 문제'로 적으면 운영자가 헛걸음한다(#187b).
        return {"kind": "needs_auth",
                "text": ("세션이 **미인증**이라 멈췄다(exit=78) — 재인증: "
                         "`cd ~/stock-trade && .backfill-venv/bin/python -m "
                         "trade.scripts.listen_beon --auth` 뒤 "
                         f"`sudo systemctl restart {unit}`")}
    return {"kind": "stopped",
            "text": (f"멈춰 있다(ActiveState={active} SubState={sub} · "
                     f"Result={result or '?'} exit={status or '?'}, "
                     f"마지막 시작 {started})")}


_FLOOD_RE = re.compile(r"wait of (\d+) seconds", re.I)


def floodwait_state(lines, now: str | None = None) -> dict:
    """저널 줄에서 FloodWait 를 읽어 **남은 시간까지** 잰다. 순수 함수.

    ⚠️ 시각을 못 읽었으면 남은 시간을 **단정하지 않는다**(None, #165) —
    "곧 풀린다"를 지어내면 운영자가 기다리다 만다.
    """
    best = None
    for ln in lines or []:
        m = _FLOOD_RE.search(ln)
        if not m:
            continue
        sec = int(m.group(1))
        if best is None or sec > best[0]:
            best = (sec, ln)
    if best is None:
        return {"seen": False, "seconds": None, "at": None, "remaining": None}
    sec, ln = best
    at = ln.split(" ", 1)[0] if ln[:4].isdigit() else None
    remaining = None
    if at and now:
        try:
            from datetime import datetime
            t0 = datetime.strptime(at, "%Y-%m-%dT%H:%M:%S%z")
            t1 = datetime.strptime(now, "%Y-%m-%dT%H:%M:%S%z")
            remaining = max(0, sec - int((t1 - t0).total_seconds()))
        except Exception:                                      # noqa: BLE001
            remaining = None
    return {"seen": True, "seconds": sec, "at": at, "remaining": remaining}


