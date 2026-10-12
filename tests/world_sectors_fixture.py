"""국가별 섹터 3보드의 공용 픽스처 — 가짜 TradingView 원천으로 며칠을 실제 수집 경로(`tick`)로
돌려 디스크에 쌓고, 그 위에서 세 페이지를 그린다.

`tests/test_world_sectors_20261012.py` 와 `tests/test_regression.py` 의 CSS 전수 가드가 **같은
픽스처**를 쓴다(빈 픽스처는 껍데기만 그려 가드가 눈이 먼다, #91c — 여기선 근사 칸·휴장 칸·
신호 약한 업종·ETF 표가 전부 그려진다). 원천·보유 목록은 전부 가짜라 네트워크 0.
"""
from __future__ import annotations

import contextlib
import random
from datetime import datetime, timedelta, timezone

K = timezone(timedelta(hours=9))

SECTORS = {
    "Electronic Technology": ("전자 기술", ["Semiconductors", "Telecommunications Equipment"]),
    "Finance": ("금융", ["Major Banks", "Regional Banks"]),
    "Consumer Non-Durables": ("소비재 비내구재", ["Food: Specialty/Candy", "Household/Personal Care"]),
    "Technology Services": ("기술 서비스", ["Packaged Software", "Information Technology Services"]),
}
IND_KO = {"Semiconductors": "세미컨덕터", "Telecommunications Equipment": "통신 장비",
          "Major Banks": "주요 은행", "Regional Banks": "지역 은행",
          "Food: Specialty/Candy": "식품: 특산품/과자", "Household/Personal Care": "가정/개인 위생용품",
          "Packaged Software": "패키지 소프트웨어",
          "Information Technology Services": "정보 기술 서비스"}
PFX = {"KR": "KRX", "US": "NASDAQ", "JP": "TSE", "TW": "TWSE", "CN_A": "SSE"}
CUR = {"KR": "KRW", "US": "USD", "JP": "JPY", "TW": "TWD", "CN_A": "CNY"}
N_STOCKS = 320


def code_for(market: str, i: int) -> str:
    if market == "KR":
        return f"{i:06d}"
    if market == "US":
        return f"T{i}"
    return f"{1000 + i}"


def fake_rows(market: str, session: str, n: int = N_STOCKS) -> list[dict]:
    """세션마다 다른(그러나 결정적인) 값 — 직전 세션과 같으면 '원천이 안 실음' 으로 거부된다."""
    rnd = random.Random(f"{market}{session}")
    secs = list(SECTORS.items())
    rows = []
    for i in range(n):
        sk, (sko, inds) = secs[i % 4]
        ind = inds[(i // 4) % 2]
        chg = rnd.gauss(0.6 if sk == "Finance" else -0.2, 1.5)
        rows.append({"symbol": f"{PFX[market]}:{code_for(market, i)}", "name": code_for(market, i),
                     "description": f"{market}<회사>{i}", "sector": sk, "industry": ind,
                     "close": 100.0, "change": chg,
                     "Value.Traded": rnd.uniform(1e8, 5e9) * (2.0 if sk == "Finance" else 1.0),
                     "market_cap_basic": 1e12 / (i + 1), "sector.tr": sko, "industry.tr": IND_KO[ind],
                     "average_volume_30d_calc": rnd.uniform(1e6, 3e7), "currency": CUR[market],
                     "fundamental_currency_code": CUR[market]})
    return rows


class FakeScanner:
    """`tv_scanner.scan` 대역 — 지금 시각에 받아야 할 세션의 값을 준다. `calls` 로 횟수를 잰다."""

    def __init__(self, ws):
        self.ws, self.now, self.calls, self.fail = ws, None, [], {}

    def __call__(self, market, **kw):
        from bot import tv_scanner as tv
        self.calls.append(market)
        if market in self.fail:
            raise tv.ScanError(self.fail[market], "http")
        d = self.ws.due_session(market, self.now)
        return {"rows": fake_rows(market, d), "total": N_STOCKS, "dropped": [], "pages": 1}


@contextlib.contextmanager
def patched(root):
    """모듈 상태를 `root` 아래로 옮기고 원천을 가짜로 — 끝나면 전부 되돌린다(테스트 밖 쓰기 0)."""
    from bot import etf_holdings as eh
    from bot import tv_scanner as tv
    from bot import world_sectors as ws
    saved = {(ws, k): getattr(ws, k) for k in ("_DIR", "_SNAP", "_REC_DIR", "_STATE")}
    saved[(eh, "_DIR")] = eh._DIR
    saved[(tv, "scan")] = tv.scan
    saved[(eh, "fetch_us")] = eh.fetch_us
    saved[(eh, "fetch_kr")] = eh.fetch_kr
    fake = FakeScanner(ws)
    ws._DIR = root / "ws"
    ws._SNAP = ws._DIR / "snap"
    ws._REC_DIR = ws._DIR / "records"
    ws._STATE = ws._DIR / "state.json"
    eh._DIR = ws._DIR / "holdings"
    tv.scan = fake
    eh.fetch_us = lambda t, session=None: (
        [{"code": f"T{i * 4}", "name": f"US{i}", "weight": 10 - i * 0.3} for i in range(15)],
        "As of Oct 12 2026")
    eh.fetch_kr = lambda t, d: (
        [{"code": f"{i * 4:06d}", "name": f"KR{i}", "weight": 9 - i * 0.2} for i in range(14)]
        + [{"code": "999999", "name": "수집 안 된 종목", "weight": 0.5}], d)
    ws.cache_clear()
    ws._REGEN_LAST.clear()
    try:
        yield fake
    finally:
        for (mod, k), v in saved.items():
            setattr(mod, k, v)
        ws.cache_clear()
        ws._REGEN_LAST.clear()


def simulate(fake, start: datetime, days: int) -> None:
    """하루 두 번(09:30·23:30 KST) 실제 수집 경로를 돌린다 — 아시아는 저녁, 미국은 이튿날 아침에 들어온다."""
    from bot import world_sectors as ws
    for day in range(days):
        for hh, mm in ((9, 30), (23, 30)):
            fake.now = start + timedelta(days=day, hours=hh, minutes=mm)
            ws.tick(fake.now)


def build_pages(root) -> dict:
    """2026-09-28 ~ 10-13 아침까지 수집 → 세 페이지 HTML + 나라별 종목 JSON."""
    from bot import world_sector_pages as wp
    from bot import world_sectors as ws
    with patched(root) as fake:
        simulate(fake, datetime(2026, 9, 28, tzinfo=K), 15)
        now = datetime(2026, 10, 13, 9, 30, tzinfo=K)
        fake.now = now
        ws.tick(now)
        ws._refresh_holdings(now)
        st = ws.load_state()
        pages, jsons = wp.map_payloads(now)
        recs = ws.mix_records(now, limit=wp.RENDER_DAYS)
        sp = wp.stocks_payloads(now)
        return {"map": wp.render_map(pages, st, now), "mix": wp.render_mix(recs, st, now),
                "stocks": wp.render_stocks(sp, st, now), "jsons": jsons, "recs": recs,
                "map_payload": pages, "stocks_payload": sp, "now": now}
