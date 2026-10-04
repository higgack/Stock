"""동기화 실행 예산 — FloodWait 대기가 유닛 타임아웃을 넘으면 systemd 가 **알림 없이**
죽인다(#12). 스크립트가 예산(`TRADE_SYNC_DEADLINE_S`)을 알고 그 전에 알림과 함께
끝나는지, 그리고 유닛의 예산이 타임아웃보다 작은지(두 값이 갈리지 않는지, #38)를 잰다.
"""
import re
from pathlib import Path

import pytest

from trade import tg_entities as tg

_REPO = Path(__file__).resolve().parents[2]


def test_no_budget_means_no_abort(monkeypatch):
    """예산이 없으면(사람이 여는 넓은 백필) 어떤 대기도 막지 않는다."""
    monkeypatch.delenv(tg.SYNC_DEADLINE_ENV, raising=False)
    assert tg.flood_wait_overruns(10_000, started=0.0, now=599.0) is None
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, bad)
        assert tg.sync_deadline_s() is None, bad


def test_budget_boundary_includes_the_margin(monkeypatch):
    """남은 예산 = 예산 − 경과. 대기 + 여유(알림·기록 몫)가 남은 예산을 넘으면 중단."""
    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    m = tg.SYNC_DEADLINE_MARGIN_S
    # 경과 400 → 남은 140: 대기 110 + 여유 30 = 140 은 들어간다, 111 은 넘는다.
    assert tg.flood_wait_overruns(140 - m, started=0.0, now=400.0) is None
    why = tg.flood_wait_overruns(141 - m, started=0.0, now=400.0)
    assert why and "남은 140s" in why and "예산 540s" in why and tg.SYNC_DEADLINE_ENV in why
    # 인자로 준 예산이 환경보다 먼저다.
    assert tg.flood_wait_overruns(50, started=0.0, now=0.0, deadline_s=60) is not None


def _units():
    out = {}
    for p in sorted((_REPO / "deploy").glob("trade-bot-*.service")):
        txt = p.read_text(encoding="utf-8")
        out[p.name] = txt
    return out


def _directive(txt, key):
    m = re.search(rf"^{re.escape(key)}=(.+)$", txt, re.M)
    return m.group(1).strip() if m else None


def test_every_flood_waiting_sync_unit_carries_a_budget_below_its_timeout():
    """FloodWait 을 기다리는 스크립트(= `flood_wait_overruns` 를 부르는 것)를 도는
    유닛은 전부 예산을 넘기고, 그 예산은 `TimeoutStartSec` 보다 여유만큼 작아야 한다.
    이름 열거가 아니라 **스크립트 소스에서 파생**한다(#24) — 새 동기화가 생기면 여기서
    걸린다. 반대 증거: 예산을 단 유닛은 전부 타임아웃이 있고 그보다 작다."""
    scripts = sorted(p.relative_to(_REPO).as_posix()
                     for p in (_REPO / "trade" / "scripts").glob("*.py")
                     if "flood_wait_overruns(" in p.read_text(encoding="utf-8"))
    assert len(scripts) >= 2, scripts          # 나쁜양파·BeOn — 줄면 가드가 눈먼다
    units = _units()
    seen = set()
    for name, txt in units.items():
        exec_start = _directive(txt, "ExecStart") or ""
        hit = [s for s in scripts if s in exec_start]
        budget = re.search(rf"^Environment={tg.SYNC_DEADLINE_ENV}=(\d+)$", txt, re.M)
        if hit:
            seen.update(hit)
            assert budget, f"{name}: {hit} 를 돌리는데 {tg.SYNC_DEADLINE_ENV} 가 없다"
        if budget:
            timeout = _directive(txt, "TimeoutStartSec")
            assert timeout and timeout.isdigit(), f"{name}: 예산은 있는데 타임아웃이 없다"
            assert int(timeout) - int(budget.group(1)) >= tg.SYNC_DEADLINE_MARGIN_S, (
                name, timeout, budget.group(1))
    assert seen == set(scripts), f"유닛이 안 도는 스크립트: {set(scripts) - seen}"


telethon = pytest.importorskip("telethon")


def test_beon_forward_aborts_before_a_wait_that_would_outlive_the_unit(monkeypatch):
    """BeOn 동기화도 같은 규약 — 대기가 예산을 넘으면 기다리지 않고 중단한다.
    반대 증거: 예산이 넉넉하면 기다렸다가 다시 보낸다."""
    import asyncio
    import os
    os.environ.setdefault("TRADE_TELETHON_API_ID", "0")
    os.environ.setdefault("TRADE_TELETHON_API_HASH", "stub")
    os.environ.setdefault("TRADE_CHANNEL_CHAT_IDS", "-1000000000000")
    from telethon.errors import FloodWaitError
    from trade.scripts import backfill_beon as bb

    slept: list = []

    async def _sleep(s):
        slept.append(s)

    class _Client:
        def __init__(self):
            self.calls = 0

        async def forward_messages(self, dest, ids, from_peer=None):
            self.calls += 1
            if self.calls == 1:
                raise FloodWaitError(request=None, capture=100)

    class _M:
        id = 7

    monkeypatch.setattr(bb.asyncio, "sleep", _sleep)
    monkeypatch.setenv(tg.SYNC_DEADLINE_ENV, "540")
    monkeypatch.setattr(bb, "_RUN_T0", bb.time.monotonic() - 500)   # 남은 40s
    with pytest.raises(bb.BackfillAborted) as ex:
        asyncio.run(bb._forward_unit(_Client(), None, [_M()], None))
    assert "실행 예산" in str(ex.value) and slept == []

    monkeypatch.setattr(bb, "_RUN_T0", bb.time.monotonic())          # 남은 540s
    c = _Client()
    assert asyncio.run(bb._forward_unit(c, None, [_M()], None)) is True
    assert slept == [101] and c.calls == 2
