"""국가별 섹터 — 5개국(KR·US·JP·TW·CN) 전 종목의 하루치를 장 마감 뒤 TradingView 에서 한 번
받아 저장하고, 섹터·업종으로 묶어 시장 대비 등락·상대 거래대금을 잰다(사용자 2026-10-12).

세 보드의 공용 재료다 — 「국가별 섹터혼합」(나라 사이 업종 비교) · 「국가별 심리지도」(나라별
업종 × 요일) · 「국가별 섹터종목」(한·미 섹터 ETF 상위 보유 종목의 요일별 표).

계산 규칙(사용자가 준 블로그 캡처에서 옮긴 것 + 캡처가 말하지 않아 **우리가 정한 것**):
- 업종·섹터 등락 = 그날 종목 등락률의 **시가총액(전일 기준) 가중 평균** — 업종 지수처럼(우리가
  정함). 시장 등락도 같은 방식으로 그 나라 전 종목.
- 시장 대비 %p = 업종 등락 − 시장 등락. 여러 날이면 각각 누적한 뒤 뺀다.
- 상대 거래대금 = (업종 거래대금 ÷ 업종의 직전 20거래일 평균) ÷ (같은 비율의 시장 값)
  — 캡처의 설명문 그대로. 자체 이력이 20거래일이 안 되는 동안은 TradingView 의
  30일 평균 거래량 × 그날 종가로 **근사**하고 화면이 그렇게 밝힌다(#171).
- 심리 6갈래 — 하루 칸 ±1%p · 1.1배, 주간 칸 ±2%p · 1.05배(캡처 설명문 그대로).

수집 — 각 시장 세션 마감 `READY_MIN` 분 뒤부터 다음 세션 개장 전까지 한 번. 직전 세션과 값이
거의 같으면(원천이 아직 그날을 안 실었거나 달력에 없는 휴장) 굽지 않는다. 그 창을 놓친
세션은 다시 받을 수 없다 — TradingView 는 '지금' 값만 주므로 빈 날로 남고 화면이 그렇게 말한다.
진단 — `cd ~/stock && .venv/bin/python -m bot.world_sectors --why`(읽기 전용) ·
`--probe [시장]`(받아 보기만, 아무것도 안 쓴다).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.world_sectors")

_KST = timezone(timedelta(hours=9))
_DIR = Path(os.environ.get("HOME") or str(Path.home())) / ".tradingagents" / "world_sectors"
_SNAP = _DIR / "snap"
_REC_DIR = _DIR / "records"
_STATE = _DIR / "state.json"

MARKETS = ("KR", "US", "JP", "TW", "CN_A")
READY_MIN = 180            # 마감 뒤 이만큼 지나서 받는다(지연 15분 + 시간외 단일가·정산 여유)
RETRY_MIN = (30, 60, 120, 180)   # 같은 세션 연속 실패 시 다음 시도까지(분)
KEEP_SNAP_DAYS = 45        # 종목별 원자료(gz) 보관 — 주간 표·20거래일 평균에 충분
KEEP_AGG_DAYS = 120        # 섹터·업종 집계(작다) 보관
KEEP_REC_DAYS = 180        # 확정된 섹터혼합 기록 보관
MIN_N = 3                  # 업종이 '그 나라에 있다' 로 칠 최소 종목 수(1종목 업종은 그 종목 등락이다)
BASE_N, BASE_MIN = 20, 15  # 상대 거래대금 기준 = 직전 20거래일 중 15일 이상 자체 이력이 있을 때
MAP_THR = {"day": (1.0, 1.1), "week": (2.0, 1.05)}    # 심리지도 (%p, 배) — 캡처 설명문
MIX_THR = {"day": 0.5, "week": 1.0}                  # 섹터혼합 강세·약세 경계(%p) — 우리가 정함
MIN_ROWS = 300             # 한 시장 종목이 이보다 적으면 굽지 않는다
_FALLBACK_HOURS = {        # 달력이 그 날짜를 모를 때만(평일 가정) — (개장 h,m, 마감 h,m) UTC
    "KR": (0, 0, 6, 30), "US": (14, 30, 21, 0), "JP": (0, 0, 6, 30),
    "TW": (1, 0, 5, 30), "CN_A": (1, 30, 7, 0),
}
ROW = ("sym", "name", "sector", "industry", "chg", "val", "cap", "avg", "close", "fx")

SENTIMENT = {              # 키 → (화면 이름, 칸 색 토큰)
    "hot_up": ("거래 몰린 상승", "hu"), "quiet_up": ("조용한 상승", "qu"),
    "calm": ("소강", "ca"), "churn": ("손바뀜", "ch"),
    "fade": ("관심 이탈", "fa"), "hot_down": ("거래 몰린 하락", "hd"),
    "up": ("상승", "qu"), "flat": ("보합", "ca"), "down": ("하락", "fa"),
}


# ── 시각·달력 ───────────────────────────────────────────────────────────────
def _now(now: datetime | None = None) -> datetime:
    t = now or datetime.now(_KST)
    return t.astimezone(_KST) if t.tzinfo else t.replace(tzinfo=_KST)


def _iso(t: datetime) -> str:
    return t.astimezone(_KST).isoformat(timespec="seconds")


def _parse(ts) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_KST)


def _cal(market: str):
    try:
        from bot.market_calendar import _calendar
        return _calendar(market)
    except Exception:                                          # noqa: BLE001
        return None


def session_times(market: str, d: str) -> tuple[datetime, datetime] | None:
    """그 시장 날짜 `d` 가 세션이면 (개장, 마감) UTC, 아니면 None.

    거래소 달력(exchange_calendars)을 쓰고, 달력이 그 날짜를 모르면(XSHG 는 2026-12-31 까지)
    평일로 가정한다 — 그때 놓치는 휴장은 '직전 세션과 값이 같다' 검사가 막는다."""
    cal = _cal(market)
    if cal is not None:
        try:
            import pandas as pd
            ts = pd.Timestamp(d)
            if not cal.is_session(ts):
                return None
            return (cal.session_open(ts).to_pydatetime(),
                    cal.session_close(ts).to_pydatetime())
        except Exception:                                      # noqa: BLE001
            pass
    dd = _date.fromisoformat(d)
    if dd.weekday() >= 5:
        return None
    oh, om, ch, cm = _FALLBACK_HOURS[market]
    base = datetime(dd.year, dd.month, dd.day, tzinfo=timezone.utc)
    return base.replace(hour=oh, minute=om), base.replace(hour=ch, minute=cm)


def due_session(market: str, now: datetime | None = None) -> str | None:
    """지금 받아야 할 그 시장의 마지막 세션 — 마감 + `READY_MIN` 이 지난 가장 최근 세션."""
    now_u = _now(now).astimezone(timezone.utc)
    d = _now(now).date()          # KST 날짜 — 어느 시장의 세션 날짜보다 이르지 않다
    for i in range(15):
        ds = (d - timedelta(days=i)).isoformat()
        t = session_times(market, ds)
        if t and now_u >= t[1] + timedelta(minutes=READY_MIN):
            return ds
    return None


def next_open(market: str, d: str) -> datetime | None:
    """`d` 다음 세션의 개장(UTC) — 그 뒤엔 원천이 다음 세션 값을 주므로 `d` 를 못 받는다."""
    dd = _date.fromisoformat(d)
    for i in range(1, 15):
        t = session_times(market, (dd + timedelta(days=i)).isoformat())
        if t:
            return t[0]
    return None


def sessions_in(market: str, start: str, end: str) -> list[str]:
    a, b = _date.fromisoformat(start), _date.fromisoformat(end)
    out = []
    while a <= b:
        if session_times(market, a.isoformat()):
            out.append(a.isoformat())
        a += timedelta(days=1)
    return out


def prev_sessions(market: str, d: str, n: int) -> list[str]:
    """`d` **앞** 세션 n 개(최근 것부터). 60일 안에서 못 채우면 있는 만큼."""
    dd, out = _date.fromisoformat(d), []
    for i in range(1, 61):
        x = (dd - timedelta(days=i)).isoformat()
        if session_times(market, x):
            out.append(x)
            if len(out) >= n:
                break
    return out


def week_monday(d: str) -> _date:
    dd = _date.fromisoformat(d)
    return dd - timedelta(days=dd.weekday())


# ── 저장소 ─────────────────────────────────────────────────────────────────
def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None


def snap_path(market: str, d: str) -> Path:
    return _SNAP / market / f"{d}.json.gz"


def agg_path(market: str, d: str) -> Path:
    return _SNAP / market / f"{d}.agg.json"


def load_state() -> dict:
    s = _read_json(_STATE)
    return s if isinstance(s, dict) else {}


def _save_state(st: dict) -> bool:
    try:
        _write_json(_STATE, st)
        return True
    except Exception as exc:                                   # noqa: BLE001
        log.warning("world_sectors: 상태 저장 실패: %s", exc)
        return False


_CACHE: dict = {"agg": {}, "snap": {}}
_CACHE_LOCK = threading.Lock()
# 업종 합계(.agg)는 작고(나라·하루당 수십 KB) 매 재생성마다 20세션 기준선으로 다시 읽힌다 —
# 넉넉히 둔다. 하루치 원본(.json.gz)은 미국 한 장이 메모리에서 수 MB 라 64장이면 수백 MB 가
# 상주한다 — 직전 세션 대조(검증)와 주간 종목표가 연달아 읽는 몇 장만 둔다.
_CACHE_MAX = {"agg": 256, "snap": 6}


def cache_clear() -> None:
    with _CACHE_LOCK:
        for d in _CACHE.values():
            d.clear()


def _cached_load(path: Path, loader, kind: str):
    """파일 내용을 mtime 으로 메모(LRU) — 15분마다 같은 파일을 다시 풀지 않게."""
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return None
    key = str(path)
    cache = _CACHE[kind]
    with _CACHE_LOCK:
        hit = cache.pop(key, None)
        if hit and hit[0] == mt:
            cache[key] = hit
            return hit[1]
    obj = loader(path)
    with _CACHE_LOCK:
        cache.pop(key, None)
        while len(cache) >= _CACHE_MAX[kind]:
            cache.pop(next(iter(cache)))
        cache[key] = (mt, obj)
    return obj


def _gz_load(path: Path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("world_sectors: %s 를 못 읽음(%s)", path.name, type(exc).__name__)
        return None


def load_snapshot(market: str, d: str) -> dict | None:
    obj = _cached_load(snap_path(market, d), _gz_load, "snap")
    return obj if isinstance(obj, dict) and isinstance(obj.get("rows"), list) else None


def load_agg(market: str, d: str) -> dict | None:
    obj = _cached_load(agg_path(market, d), _read_json, "agg")
    return obj if isinstance(obj, dict) and isinstance(obj.get("mkt"), dict) else None


def stored_dates(market: str) -> list[str]:
    try:
        return sorted(p.name[:10] for p in (_SNAP / market).glob("????-??-??.agg.json"))
    except OSError:
        return []


def aggs_for(market: str, dates) -> dict[str, dict]:
    out = {}
    for d in dates:
        a = load_agg(market, d)
        if a:
            out[d] = a
    return out


def _prune(now: datetime) -> None:
    cut_s = (now.date() - timedelta(days=KEEP_SNAP_DAYS)).isoformat()
    cut_a = (now.date() - timedelta(days=KEEP_AGG_DAYS)).isoformat()
    cut_r = (now.date() - timedelta(days=KEEP_REC_DAYS)).isoformat()
    try:
        for m in MARKETS:
            for p in (_SNAP / m).glob("????-??-??.json.gz"):
                if p.name[:10] < cut_s:
                    p.unlink()
            for p in (_SNAP / m).glob("????-??-??.agg.json"):
                if p.name[:10] < cut_a:
                    p.unlink()
        for p in _REC_DIR.glob("????-??-??.json"):
            if p.name[:10] < cut_r:
                p.unlink()
    except Exception as exc:                                   # noqa: BLE001
        log.debug("world_sectors: 정리 실패: %s", exc)


# ── 정규화·집계(순수) ─────────────────────────────────────────────────────────
def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def normalize(row: dict) -> list | None:
    """스캐너 한 행 → 저장 행(`ROW` 순서). 기호가 없으면 None."""
    sym = str(row.get("symbol") or "")
    if not sym:
        return None
    chg, val = _num(row.get("change")), _num(row.get("Value.Traded"))
    cap, close = _num(row.get("market_cap_basic")), _num(row.get("close"))
    av = _num(row.get("average_volume_30d_calc"))
    avg = av * close if av is not None and close else None
    cur, fcur = row.get("currency"), row.get("fundamental_currency_code")
    # 시총이 거래 통화가 아닌 통화로 올 수 있다(재무제표 통화 ≠ 거래 통화, #139) — 그런 종목은
    # 시총 가중에서 뺀다(거래대금·종목 수엔 남는다). 통화 열을 원천이 안 주면 판정하지 않는다.
    fx = 1 if (cur and fcur and str(cur) != str(fcur)) else 0
    name = row.get("description") or row.get("name") or sym
    return [sym, str(name), row.get("sector") or None, row.get("industry") or None,
            chg, val, cap, avg, close, fx]


def labels_of(rows: list[dict]) -> dict:
    """영문 키 → 한국어 이름(원천 `sector.tr`·`industry.tr`). 없으면 비운다(화면이 영문을 쓴다)."""
    sec, ind = {}, {}
    for r in rows:
        if r.get("sector") and r.get("sector.tr"):
            sec.setdefault(r["sector"], str(r["sector.tr"]))
        if r.get("industry") and r.get("industry.tr"):
            ind.setdefault(r["industry"], str(r["industry.tr"]))
    return {"sec": sec, "ind": ind}


def _g() -> dict:
    return {"n": 0, "w": 0.0, "wr": 0.0, "val": 0.0, "vb": 0.0, "base": 0.0, "cap": 0.0}


def aggregate(rows: list[list]) -> dict:
    """저장 행 → 시장·섹터·업종 합계(순수). 등락은 전일 시총 가중(`wr/w`), 거래대금은 합.

    전일 시총 = 오늘 시총 ÷ (1 + 등락률) — 업종 시총 합의 하루 변화율과 같다(지수 방식).
    `vb`/`base` 는 근사 기준(30일 평균 거래량 × 종가)이 있는 종목만의 거래대금·기준 합이다."""
    out = {"mkt": _g(), "sec": {}, "ind": {}, "rows": 0, "fx_skip": 0, "no_cap": 0}
    for r in rows:
        _sym, _name, sec, ind, chg, val, cap, avg, _close, fx = r
        if chg is None or val is None or val < 0:
            continue
        out["rows"] += 1
        groups = [out["mkt"]]
        if sec:
            groups.append(out["sec"].setdefault(sec, _g()))
        if ind:
            groups.append(out["ind"].setdefault(ind, _g()))
        prev = None
        if fx:
            out["fx_skip"] += 1
        elif cap and cap > 0 and chg > -100:
            prev = cap / (1 + chg / 100)
        else:
            out["no_cap"] += 1
        for g in groups:
            g["n"] += 1
            g["val"] += val
            if prev:
                g["w"] += prev
                g["wr"] += prev * chg
                g["cap"] += cap
            if avg:
                g["vb"] += val
                g["base"] += avg
    return out


def ret(g: dict | None) -> float | None:
    """그룹 하루 등락(%) — 시총 가중. 가중치가 없으면 None."""
    return g["wr"] / g["w"] if g and g.get("w") else None


def classify(rel: float | None, rv: float | None, thr: float, vthr: float) -> str | None:
    """심리 6갈래(순수) — 시장 대비 %p 와 상대 거래대금. 거래대금 기준이 없으면 3갈래."""
    if rel is None:
        return None
    side = "up" if rel >= thr else "down" if rel <= -thr else "flat"
    if rv is None:
        return side
    hot = rv >= vthr
    return {("up", True): "hot_up", ("up", False): "quiet_up",
            ("flat", True): "churn", ("flat", False): "calm",
            ("down", True): "hot_down", ("down", False): "fade"}[(side, hot)]


def base_aggs(market: str, d: str, aggs: dict[str, dict]) -> list[dict] | None:
    """상대 거래대금의 '직전 20거래일' — 달력 세션 20개 중 자체 이력이 `BASE_MIN` 개 이상이면
    그 이력들, 아니면 None(근사로 간다)."""
    prev = prev_sessions(market, d, BASE_N)
    have = [aggs[x] for x in prev if x in aggs]
    if len(prev) >= BASE_N and len(have) >= BASE_MIN:
        return have
    return None


def value_parts(agg: dict, kind: str, key: str | None, base: list[dict] | None):
    """(그룹 분자, 그룹 분모, 시장 분자, 시장 분모, 기준) — 상대 거래대금 = (분자/분모)÷(시장 분자/분모).

    기준 `20d` = 직전 20거래일 자체 이력 평균 · `approx` = 30일 평균 거래량 × 종가 근사.
    못 구하면 None. 여러 날을 합칠 땐 각 조각을 더해서 나눈다(주간 칸)."""
    g = agg["mkt"] if kind == "mkt" else (agg.get(kind) or {}).get(key)
    m = agg["mkt"]
    if not g:
        return None
    if base:
        if kind == "mkt":
            mg = sum(a["mkt"]["val"] for a in base) / len(base)
        else:
            mg = sum(((a.get(kind) or {}).get(key) or {}).get("val", 0.0) for a in base) / len(base)
        mm = sum(a["mkt"]["val"] for a in base) / len(base)
        if mg > 0 and mm > 0 and m["val"] > 0:
            return g["val"], mg, m["val"], mm, "20d"
    if g.get("base", 0) > 0 and m.get("base", 0) > 0 and m.get("vb", 0) > 0:
        return g["vb"], g["base"], m["vb"], m["base"], "approx"
    return None


def rv_of(parts) -> float | None:
    if not parts:
        return None
    gn, gd, mn, md, _b = parts
    if gd <= 0 or md <= 0 or mn <= 0:
        return None
    return (gn / gd) / (mn / md)


def group_cell(agg: dict, kind: str, key: str, base, thr_key: str = "day") -> dict:
    """하루 칸 — 시장 대비 %p · 상대 거래대금 · 갈래."""
    g = (agg.get(kind) or {}).get(key)
    rg, rm = ret(g), ret(agg["mkt"])
    rel = rg - rm if rg is not None and rm is not None else None
    parts = value_parts(agg, kind, key, base)
    rv = rv_of(parts)
    thr, vthr = MAP_THR[thr_key]
    return {"rel": rel, "ret": rg, "rv": rv, "basis": parts[4] if parts else None,
            "s": classify(rel, rv, thr, vthr), "n": (g or {}).get("n", 0)}


def week_cell(items: list[tuple[str, dict, list | None]], kind: str, key: str) -> dict:
    """주간 칸 — 각 날 등락을 누적해 뺀 %p, 거래대금 조각을 더해 나눈 상대 거래대금.
    `items` = [(날짜, 집계, 기준)] (그 주에 받은 날만)."""
    cg = cm = 1.0
    n_used = wins = 0
    gn = gd = mn = md = 0.0
    bases = set()
    for _d, agg, base in items:
        g = (agg.get(kind) or {}).get(key)
        rg, rm = ret(g), ret(agg["mkt"])
        if rg is None or rm is None:
            continue
        cg *= 1 + rg / 100
        cm *= 1 + rm / 100
        n_used += 1
        wins += rg > rm
        parts = value_parts(agg, kind, key, base)
        if parts:
            gn += parts[0]
            gd += parts[1]
            mn += parts[2]
            md += parts[3]
            bases.add(parts[4])
    rel = (cg - cm) * 100 if n_used else None
    rv = (gn / gd) / (mn / md) if gd > 0 and md > 0 and mn > 0 else None
    thr, vthr = MAP_THR["week"]
    basis = None if not bases else ("20d" if bases == {"20d"} else
                                    "approx" if bases == {"approx"} else "mixed")
    return {"rel": rel, "ret": (cg - 1) * 100 if n_used else None, "rv": rv, "basis": basis,
            "s": classify(rel, rv, thr, vthr), "wins": wins, "N": n_used}


def mix_verdict(cells: dict[str, float | None], thr: float) -> tuple[str, str]:
    """섹터혼합 판정(순수) — 값이 있는 나라가 2개 이상일 때만 판정한다.

    전부 같은 쪽(±thr 이상) = 'N국 공통 강세·약세' · 강세(또는 약세)가 2국 이상이고 반대쪽이
    없으면 'K국 강세·약세' · 강세와 약세가 같이 있으면 '엇갈림' · 그 밖 = '뚜렷하지 않음'.
    캡처의 3국 표(미국 -0.86 은 약세로 안 세고 일본·한국만 세어 '2국 약세')와 같은 규칙이다."""
    present = {m: v for m, v in cells.items() if v is not None}
    if len(present) < 2:
        return "na", ""
    up = [m for m, v in present.items() if v >= thr]
    dn = [m for m, v in present.items() if v <= -thr]
    n = len(present)
    if up and dn:
        return "mixed", "엇갈림"
    if len(up) == n:
        return "all_up", f"{n}국 공통 강세"
    if len(dn) == n:
        return "all_down", f"{n}국 공통 약세"
    if len(up) >= 2:
        return "k_up", f"{len(up)}국 강세"
    if len(dn) >= 2:
        return "k_down", f"{len(dn)}국 약세"
    return "none", "뚜렷하지 않음"


def mix_sort_key(verdict: str, cells: dict[str, float | None], thr: float) -> tuple:
    """정렬 — 공통(나라 수 많은 순) → K국(많은 순) → 엇갈림 → 뚜렷하지 않음, 같은 묶음에선
    신호 낸 나라들의 |%p| 평균이 큰 순."""
    present = [v for v in cells.values() if v is not None]
    sig = [abs(v) for v in present if abs(v) >= thr] or [abs(v) for v in present] or [0.0]
    rank = {"all_up": 0, "all_down": 0, "k_up": 1, "k_down": 1, "mixed": 2, "none": 3}.get(verdict, 4)
    k = len([v for v in present if abs(v) >= thr])
    return (rank, -k, -sum(sig) / len(sig))


# ── 수집 ───────────────────────────────────────────────────────────────────
def validate(rows: list[list], prev: dict | None) -> tuple[bool, str, str]:
    """굽기 전 검사 — (통과, 사유, 갈래). 직전 세션과 값이 거의 같으면 원천이 아직 그날을
    안 실은 것(또는 달력에 없는 휴장)이다 — 같은 값을 두 날로 세면 그 주가 통째로 틀린다."""
    n = len(rows)
    if n < MIN_ROWS:
        return False, f"종목이 {n}개뿐 — 원천 응답이 잘린 것으로 보임", "small"
    good = sum(1 for r in rows if r[4] is not None and r[5] is not None)
    if good < 0.9 * n:
        return False, f"등락·거래대금이 빈 종목이 많음({n - good}/{n})", "partial"
    if prev and isinstance(prev.get("rows"), list):
        pn = len(prev["rows"])
        if pn and n < 0.7 * pn:
            return False, f"종목이 {n}개 — 직전 세션 {pn}개의 70% 미만", "small"
        old = {r[0]: (r[4], r[5]) for r in prev["rows"]}
        common = [r for r in rows if r[0] in old]
        if len(common) >= 100:
            same = sum(1 for r in common if old[r[0]] == (r[4], r[5])) / len(common)
            if same >= 0.9:
                return (False, f"직전 세션({prev.get('date')})과 값이 같음({same:.0%}) — 원천이 "
                        "아직 이 세션 값을 싣지 않았거나 달력에 없는 휴장", "stale")
    return True, "", ""


def save_snapshot(market: str, d: str, rows: list[list], labels: dict, meta: dict,
                  now: datetime) -> dict:
    """gz 원자료를 먼저, 집계를 나중에 쓴다 — 집계 파일이 '받았음' 표식이다."""
    p = snap_path(market, d)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump({"market": market, "date": d, "fetched_at": _iso(now), "rows": rows,
                   "labels": labels, "meta": meta}, fh, ensure_ascii=False)
    os.replace(tmp, p)
    agg = aggregate(rows)
    agg.update({"date": d, "fetched_at": _iso(now), "labels": labels, "meta": meta})
    _write_json(agg_path(market, d), agg)
    return agg


def _scanner():
    from bot import tv_scanner
    return tv_scanner


def _collect(market: str, now: datetime, st: dict, res: dict) -> None:
    d = due_session(market, now)
    ms = st.setdefault("markets", {}).setdefault(market, {})
    if not d:
        ms.update({"due": None})
        return
    ms["due"] = d
    if agg_path(market, d).exists():
        return
    # 창을 놓쳤는지를 백오프보다 **먼저** 본다 — 백오프 대기 중에 다음 세션이 열리면 그 세션은
    # 더는 받을 수 없는데, 대기부터 보면 '실패 중' 으로 남아 다음 시도 시각을 계속 약속한다.
    no = next_open(market, d)
    if no and now.astimezone(timezone.utc) >= no:
        if ms.get("missed") != d:
            log.warning("world_sectors: %s %s 를 받을 창을 놓쳤다 — 다음 세션이 이미 열렸다",
                        market, d)
        ms.update({"missed": d, "date": d, "kind": "missed", "next_try": None,
                   "reason": f"{d} 를 받을 창을 놓쳤다(다음 세션이 이미 열림 — 원천은 '지금' 값만 준다)"})
        return
    nt = _parse(ms.get("next_try"))
    if ms.get("date") == d and nt and now < nt:
        return
    n = (int(ms.get("n") or 0) if ms.get("date") == d else 0) + 1
    tv = _scanner()
    try:
        got = tv.scan(market)
    except tv.ScanError as exc:
        _fail_market(market, ms, d, n, now, exc.reason, exc.kind)
        res["failed"].append(market)
        return
    rows = [r for r in (normalize(x) for x in got["rows"]) if r]
    prev_d = next((x for x in reversed(stored_dates(market)) if x < d), None)
    ok, why, kind = validate(rows, load_snapshot(market, prev_d) if prev_d else None)
    if not ok:
        _fail_market(market, ms, d, n, now, why, kind)
        res["failed"].append(market)
        return
    meta = {"total": got.get("total"), "pages": got.get("pages"),
            "dropped": got.get("dropped") or []}
    save_snapshot(market, d, rows, labels_of(got["rows"]), meta, now)
    ms.update({"date": d, "n": 0, "kind": "ok", "reason": "", "next_try": None,
               "last_ok": _iso(now), "rows": len(rows), "dropped": meta["dropped"]})
    res["stored"].append(f"{market}:{d}")


def _fail_market(market: str, ms: dict, d: str, n: int, now: datetime, reason: str,
                 kind: str) -> None:
    wait = RETRY_MIN[min(n, len(RETRY_MIN)) - 1]
    ms.update({"date": d, "n": n, "kind": kind, "reason": reason,
               "next_try": _iso(now + timedelta(minutes=wait))})
    log.warning("world_sectors: %s %s 수집 실패(%s) — %s · %d분 뒤 다시(연속 %d회)",
                market, d, kind, reason, wait, n)


def tick(now: datetime | None = None) -> dict:
    """한 주기 — 받을 세션이 있는 시장만 원천을 부른다(보통 하루 시장당 1~3콜)."""
    now = _now(now)
    st = load_state()
    res = {"stored": [], "failed": [], "status": "idle"}
    st["last_try"] = _iso(now)
    for m in MARKETS:
        try:
            _collect(m, now, st, res)
        except Exception as exc:                               # noqa: BLE001
            log.exception("world_sectors: %s 수집 중 예외", m)
            ms = st.setdefault("markets", {}).setdefault(m, {})
            ms.update({"kind": "error", "reason": f"수집 중 예외({type(exc).__name__}: {str(exc)[:120]})",
                       "next_try": _iso(now + timedelta(minutes=RETRY_MIN[0]))})
            res["failed"].append(m)
    if res["stored"]:
        st["last_ok"] = _iso(now)
        try:
            finalize_records(now)
        except Exception as exc:                               # noqa: BLE001
            log.warning("world_sectors: 섹터혼합 기록 확정 실패: %s", exc)
    res["status"] = "ok" if res["stored"] else ("fail" if res["failed"] else "idle")
    _save_state(st)
    _prune(now)
    return res


def market_verdict(market: str, state: dict, now: datetime | None = None) -> tuple[str, str]:
    """한 나라 수집 상태 — (갈래, 사람이 읽는 한 줄). 화면 상태 줄·`--why`·감사가 같이 쓴다(#35).

    ok = 받아야 할 세션을 받음 · wait = 받을 때가 됐고 다음 주기에 받는다 · stale = 원천이 아직
    그 세션 값을 싣지 않음(우리가 고칠 게 없다, #260) · fail = 수집 실패(사유) · missed = 그
    세션을 받을 창을 놓침(다시 받을 수 없다) · none = 아직 받을 세션이 없음."""
    now = _now(now)
    ms = (state.get("markets") or {}).get(market) or {}
    have = stored_dates(market)
    last = have[-1] if have else None
    due = due_session(market, now)
    tail = f" · 마지막으로 받은 세션 {last or '—'}"
    if due is None:
        return "none", "아직 받을 세션 없음" + tail
    if last == due:
        return "ok", f"{due} 받음"
    if ms.get("missed") == due:
        return "missed", f"{due} 를 받을 창을 놓쳤다(원천은 '지금' 값만 준다 — 다시 받을 수 없다)" + tail
    if ms.get("date") == due and ms.get("kind") not in (None, "ok"):
        nt = _parse(ms.get("next_try"))
        when = f" · 다음 시도 {nt.strftime('%m-%d %H:%M')}" if nt else ""
        kind = "stale" if ms.get("kind") == "stale" else "fail"
        return kind, f"{due} 수집 실패 {ms.get('n') or 1}회 — {ms.get('reason') or ms.get('kind')}{when}" + tail
    return "wait", f"{due} 세션 수집 대기(다음 주기)" + tail


# ── 섹터혼합(나라 사이 업종 비교) ─────────────────────────────────────────────
def market_day_status(market: str, d: str, now: datetime) -> str:
    """그 시장의 `d` — ok(받음) · closed(휴장) · pending(아직이거나 받는 중) · missing(못 받음).

    '못 받음' 은 **받을 창이 지났을 때만**이다 — 다음 세션이 열리기 전엔 실패해도 다시 묻고
    있으므로 'pending' 이다(창이 남았는데 '수집 못 함' 이라 적으면 거짓 단정이다, #165)."""
    if agg_path(market, d).exists():
        return "ok"
    if not session_times(market, d):
        return "closed"
    due = due_session(market, now)
    if due is None or due < d:
        return "pending"
    no = next_open(market, d)
    if no is not None and _now(now).astimezone(timezone.utc) < no:
        return "pending"
    return "missing"


def mix_record(d: str, now: datetime | None = None) -> dict:
    """기준일 `d` 의 섹터혼합 — 업종마다 나라별 (하루 %p, 주간 %p, 이긴 날 n/N, 종목 수).

    주간 = d 를 끝으로 한 7일(달력) 안의 그 나라 세션들 — 나라마다 휴장이 달라 N 이 다를 수
    있다(캡처의 한국 'n/3' 처럼). 업종이 '있다' = 그 주에 받은 마지막 세션에 `MIN_N`종목 이상."""
    now = _now(now)
    start = (_date.fromisoformat(d) - timedelta(days=6)).isoformat()
    markets = {}
    labels: dict[str, str] = {}
    per: dict[str, dict] = {}
    for m in MARKETS:
        sess = sessions_in(m, start, d)
        aggs = aggs_for(m, sess)
        st_day = market_day_status(m, d, now)
        markets[m] = {"day": st_day, "sessions": len(sess), "got": len(aggs),
                      "last": max(aggs) if aggs else None}
        if not aggs:
            continue
        last = aggs[max(aggs)]
        for k, v in (last.get("labels") or {}).get("ind", {}).items():
            labels.setdefault(k, v)
        items = sorted(aggs.items())
        day_agg = aggs.get(d)
        for k, g in (last.get("ind") or {}).items():
            if g.get("n", 0) < MIN_N:
                continue
            w = week_cell([(x, a, None) for x, a in items], "ind", k)
            day = None
            if day_agg is not None:
                gd = (day_agg.get("ind") or {}).get(k)
                rg, rm = ret(gd), ret(day_agg["mkt"])
                day = rg - rm if rg is not None and rm is not None else None
            per.setdefault(k, {})[m] = {"day": day, "week": w["rel"], "wins": w["wins"],
                                        "N": w["N"], "n": g["n"]}
    rows = []
    for k, cells in per.items():
        if len(cells) < 2:
            continue
        row = {"key": k, "label": labels.get(k) or k, "cells": cells}
        for win in ("day", "week"):
            vals = {m: c.get(win) for m, c in cells.items()}
            v, lab = mix_verdict(vals, MIX_THR[win])
            row[f"v_{win}"] = v
            row[f"l_{win}"] = lab
            row[f"k_{win}"] = list(mix_sort_key(v, vals, MIX_THR[win]))
        rows.append(row)
    # 굳히는 조건 — 나라마다 그날을 받았거나 · 휴장이거나 · 받을 창이 지났다(더 기다려도
    # 안 바뀐다). 창이 남은 실패는 기다린다.
    final = all(s["day"] in ("ok", "closed", "missing") for s in markets.values())
    return {"date": d, "markets": markets, "rows": rows, "final": final,
            "computed_at": _iso(now)}


def mix_dates(now: datetime | None = None, limit: int = 10) -> list[str]:
    """보여 줄 기준일 — 어느 나라든 세션을 받은 날(최근 것부터)."""
    seen = set()
    for m in MARKETS:
        seen.update(stored_dates(m))
    return sorted(seen, reverse=True)[:limit]


def load_mix_record(d: str) -> dict | None:
    r = _read_json(_REC_DIR / f"{d}.json")
    return r if isinstance(r, dict) and r.get("date") == d else None


def finalize_records(now: datetime | None = None) -> list[str]:
    """다 받은(또는 휴장인) 기준일의 섹터혼합을 굳힌다 — 원자료 보관 기간이 지나도 남게."""
    now = _now(now)
    done = []
    for d in mix_dates(now, limit=15):
        if load_mix_record(d):
            continue
        rec = mix_record(d, now)
        if rec["final"]:
            _write_json(_REC_DIR / f"{d}.json", rec)
            done.append(d)
    return done


def mix_records(now: datetime | None = None, limit: int = 10) -> list[dict]:
    """화면용 — 굳힌 기록은 그대로, 아직 안 굳은 날은 지금 값으로 계산."""
    now = _now(now)
    out = []
    for d in mix_dates(now, limit):
        out.append(load_mix_record(d) or mix_record(d, now))
    return out


# ── 심리지도(나라별 업종 × 요일) ─────────────────────────────────────────────
def map_week(market: str, monday: _date, now: datetime | None = None,
             kinds=("sec", "ind")) -> dict:
    """그 주(월~금)의 심리지도 — 칸 = 갈래 · 시장 대비 %p · 상대 거래대금."""
    now = _now(now)
    days = [(monday + timedelta(days=i)).isoformat() for i in range(5)]
    all_dates = stored_dates(market)
    # 기준(직전 20거래일)용 집계까지 한 번에 읽는다
    lo = (monday - timedelta(days=45)).isoformat()
    aggs = aggs_for(market, [x for x in all_dates if lo <= x <= days[-1]])
    cols = [{"date": x, "status": market_day_status(market, x, now)} for x in days]
    ok_days = [x for x in days if x in aggs]
    bases = {x: base_aggs(market, x, aggs) for x in ok_days}
    out = {"market": market, "monday": monday.isoformat(), "cols": cols, "groups": {}}
    if not ok_days:
        return out
    last = aggs[ok_days[-1]]
    labels = last.get("labels") or {}
    for kind in kinds:
        keys = set()
        for x in ok_days:
            for k, g in (aggs[x].get(kind) or {}).items():
                if kind == "sec" or g.get("n", 0) >= MIN_N:
                    keys.add(k)
        rows = []
        for k in keys:
            cells = {x: group_cell(aggs[x], kind, k, bases[x]) for x in ok_days}
            wk = week_cell([(x, aggs[x], bases[x]) for x in ok_days], kind, k)
            n = ((last.get(kind) or {}).get(k) or {}).get("n", 0)
            rows.append({"key": k, "label": (labels.get(kind) or {}).get(k) or k,
                         "n": n, "cells": cells, "week": wk})
        rows.sort(key=lambda r: (r["week"]["rel"] is None, -(r["week"]["rel"] or 0)))
        out["groups"][kind] = rows
    out["basis"] = sorted({c["basis"] for r in out["groups"].get("sec", [])
                           for c in r["cells"].values() if c.get("basis")})
    return out


def latest_monday(market: str) -> _date | None:
    ds = stored_dates(market)
    return week_monday(ds[-1]) if ds else None


# ── 종목 주간 표(심리지도 칸 · 섹터종목 공용) ────────────────────────────────
def code_of(sym: str) -> str:
    return str(sym).split(":")[-1]


def week_stocks(market: str, monday: _date) -> dict:
    """그 주에 받은 날들의 종목별 등락·거래대금 — {days, ok, stocks{code: …}, mval[]}.

    `stocks[code]` = {sym, name, sector, industry, cap(마지막 날), chg[날], val[날]}."""
    days = [(monday + timedelta(days=i)).isoformat() for i in range(5)]
    snaps = {x: load_snapshot(market, x) for x in days}
    stocks: dict[str, dict] = {}
    mval = []
    for i, x in enumerate(days):
        s = snaps[x]
        if not s:
            mval.append(None)
            continue
        tot = 0.0
        for r in s["rows"]:
            sym, name, sec, ind, chg, val, cap, _avg, _close, fx = r
            if val:
                tot += val
            c = code_of(sym)
            it = stocks.get(c)
            if it is None:
                it = stocks[c] = {"sym": sym, "name": name, "sector": sec, "industry": ind,
                                  "cap": None, "fx": fx, "chg": [None] * 5, "val": [None] * 5}
            it["chg"][i], it["val"][i] = chg, val
            if cap:
                it["cap"] = cap
            it["name"], it["sector"], it["industry"] = name, sec, ind
        mval.append(tot)
    labels = {"sec": {}, "ind": {}}
    for x in days:
        s = snaps[x]
        if s:
            for kind in ("sec", "ind"):
                labels[kind].update((s.get("labels") or {}).get(kind) or {})
    return {"days": days, "ok": [bool(snaps[x]) for x in days], "stocks": stocks,
            "mval": mval, "labels": labels}


def stock_week_row(it: dict, mval: list) -> dict:
    """한 종목의 주간 칸(순수) — 주간 등락(누적) · 상승일 거래(%) · 거래 몫(%) · 시총 증감.

    상승일 거래 = 그 주 거래대금 중 **오른 날**(등락 > 0)의 몫 · 거래 몫 = 그 종목 주간 거래대금
    ÷ 시장 전체 주간 거래대금 · 시총 증감 = 마지막 시총 − 마지막 시총 ÷ (1 + 주간 등락)."""
    cum, n = 1.0, 0
    up_val = tot_val = 0.0
    mtot = 0.0
    for chg, val, mv in zip(it["chg"], it["val"], mval):
        if chg is None:
            continue
        cum *= 1 + chg / 100
        n += 1
        if val:
            tot_val += val
            if chg > 0:
                up_val += val
        if mv:
            mtot += mv
    wk = (cum - 1) * 100 if n else None
    cap = it.get("cap")
    dcap = cap - cap / (1 + wk / 100) if cap and wk is not None and wk > -100 else None
    return {"week": wk, "upshare": up_val / tot_val * 100 if tot_val > 0 else None,
            "share": tot_val / mtot * 100 if mtot > 0 and tot_val > 0 else None,
            "dcap": dcap}


# ── 봇 주기 태스크 한 단위 ─────────────────────────────────────────────────────
TICK_TIMEOUT_SEC = 600
_TICK_LOCK = threading.Lock()


def _refresh_holdings(now: datetime) -> dict:
    """섹터종목의 ETF 보유 목록 — 하루 한 번(이미 받았거나 오늘 시도했으면 건너뛴다)."""
    from bot import etf_holdings as eh
    out = {}
    for m in ("US", "KR"):
        kr_date = None
        if m == "KR":
            kr_date = due_session("KR", now) or (stored_dates("KR") or [None])[-1]
        try:
            out[m] = eh.refresh(m, now, kr_date=kr_date)
        except Exception as exc:                               # noqa: BLE001
            log.warning("world_sectors: %s ETF 보유 목록 갱신 예외: %s", m, exc)
            out[m] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


REGEN_MIN = 60
_REGEN_LOCK = threading.Lock()
_REGEN_LAST: dict = {}


def _holdings_changed(holdings: dict) -> bool:
    return any(isinstance(v, dict) and (v.get("ok") or v.get("failed") or v.get("error"))
               for v in (holdings or {}).values())


def maybe_regenerate(now: datetime | None = None, *, changed: bool) -> list[str] | None:
    """세 페이지를 다시 그릴 때만 그린다 — 재료가 바뀌었거나(받음·실패·보유 목록) 나라별 상태 줄이
    바뀌었거나(대기→놓침 같은 시각 전이) `REGEN_MIN` 이 지났을 때. 재생성은 그 주의 하루치 원본을
    나라마다 다시 읽으므로 15분마다 돌릴 값이 없다(내용이 같으면 어차피 안 쓴다, #442).
    다른 스레드가 그리는 중이면 건너뛴다(같은 임시 파일 이름을 두 스레드가 쓰지 않게).
    None = 건너뜀."""
    from bot.world_sector_pages import regenerate
    now = _now(now)
    if not _REGEN_LOCK.acquire(blocking=False):
        log.info("world_sectors: 다른 스레드가 페이지를 그리는 중 — 이번 재생성은 건너뛴다")
        return None
    try:
        st = load_state()
        sig = tuple(market_verdict(m, st, now) for m in MARKETS)
        last = _REGEN_LAST.get("at")
        due = (changed or last is None or sig != _REGEN_LAST.get("sig")
               or abs(now - last) >= timedelta(minutes=REGEN_MIN))
        if not due:
            return None
        wrote = regenerate(now)
        _REGEN_LAST.update(at=now, sig=sig)
        return wrote
    finally:
        _REGEN_LOCK.release()


def tick_and_render(now: datetime | None = None) -> dict:
    """수집 → ETF 보유 목록 → 세 페이지 재생성(필요할 때만, `maybe_regenerate`). 앞 주기가 안
    끝났으면 수집은 건너뛴다. 다른 프로세스(`--run`)와도 파일 잠금으로 한 번에 하나."""
    if not _TICK_LOCK.acquire(blocking=False):
        log.warning("world_sectors: 앞 주기가 아직 안 끝나 이번 수집은 건너뛴다")
        wrote = maybe_regenerate(now, changed=False)
        return {"status": "busy", "wrote": wrote or [], "regenerated": wrote is not None}
    fh = None
    res: dict = {"status": "busy"}
    try:
        try:
            import fcntl
            _DIR.mkdir(parents=True, exist_ok=True)
            fh = open(_DIR / ".tick.lock", "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("world_sectors: 다른 프로세스가 수집 중 — 이번 수집은 건너뛴다")
        except Exception as exc:                               # noqa: BLE001
            log.debug("world_sectors: 파일 잠금 불가(%s) — 프로세스 내 잠금만 쓴다", exc)
            res = {}
        else:
            res = {}
        if res == {}:
            try:
                res = tick(now)
            except Exception as exc:                           # noqa: BLE001
                log.exception("world_sectors: tick 실패")
                res = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            res["holdings"] = _refresh_holdings(_now(now))
    finally:
        if fh is not None:
            fh.close()
        _TICK_LOCK.release()
    changed = bool(res.get("stored") or res.get("failed") or res.get("status") == "error"
                   or _holdings_changed(res.get("holdings")))
    wrote = maybe_regenerate(now, changed=changed)
    res["wrote"], res["regenerated"] = wrote or [], wrote is not None
    if res.get("stored"):
        log.info("world_sectors: 받음 %s · 실패 %s", res.get("stored"), res.get("failed"))
    return res


# ── 진단 CLI ────────────────────────────────────────────────────────────────
_RUN_HINT = "cd ~/stock && .venv/bin/python -m bot.world_sectors --why"


def _src_sig() -> str:
    """수집·판정을 만드는 파일들의 소스 지문 — 진단 출력이 어느 코드에서 나왔는지(#364)."""
    import hashlib
    h = hashlib.sha1()
    try:
        for name in ("world_sectors.py", "tv_scanner.py", "world_sector_pages.py",
                     "etf_holdings.py"):
            h.update((Path(__file__).with_name(name)).read_bytes())
    except Exception:                                          # noqa: BLE001
        return "지문불가"
    return h.hexdigest()[:10]


VERDICT_MARK = {"ok": "✅", "wait": "⏳", "stale": "⚠️", "fail": "❌", "missed": "⚠️",
                "none": "❓"}


def _fmt_ret(v) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def why(now: datetime | None = None) -> int:
    """읽기 전용 — 원천을 부르지도 쓰지도 않는다(#264·#283). 다섯 나라가 다 최신이면 0."""
    now = _now(now)
    st = load_state()
    print(f"# world_sectors --why · 지문 {_src_sig()} · {now.strftime('%Y-%m-%d %H:%M KST')}")
    print(f"① 상태 파일: {_STATE} ({'있음' if _STATE.exists() else '없음'}) · 마지막 시도 "
          f"{st.get('last_try') or '—'} · 마지막 수집 {st.get('last_ok') or '—'}")
    bad = 0
    for m in MARKETS:
        ms = (st.get("markets") or {}).get(m) or {}
        have = stored_dates(m)
        due = due_session(m, now)
        last = have[-1] if have else None
        kind, line = market_verdict(m, st, now)
        mark = VERDICT_MARK.get(kind, "❓")
        bad += kind == "fail"
        print(f"② {m}: 받아야 할 세션 {due or '—'} · 마지막으로 받은 세션 {last or '—'} "
              f"(보관 {len(have)}일) {mark} {line}")
        if ms.get("reason"):
            print(f"   사유({ms.get('kind')}): {ms['reason']} · 연속 {ms.get('n') or 0}회 · 다음 시도 "
                  f"{ms.get('next_try') or '—'}")
        if ms.get("dropped"):
            print(f"   원천이 몰라 뺀 선택 열: {', '.join(ms['dropped'])}")
        a = load_agg(m, last) if last else None
        if a:
            base = base_aggs(m, last, aggs_for(m, stored_dates(m)))
            print(f"   {last}: 종목 {a.get('rows', 0):,} · 섹터 {len(a.get('sec') or {})} · 업종 "
                  f"{len(a.get('ind') or {})} · 시장 등락 {_fmt_ret(ret(a['mkt']))}"
                  f" · 시총 통화가 달라 가중에서 뺀 종목 {a.get('fx_skip', 0)} · 상대 거래대금 기준 "
                  f"{'직전 20거래일' if base else '30일 평균 거래량 근사(자체 이력 부족)'}")
            lab = (a.get("labels") or {})
            print(f"   한국어 이름: 섹터 {len(lab.get('sec') or {})} · 업종 {len(lab.get('ind') or {})}"
                  " (0 이면 원천이 sector.tr/industry.tr 을 안 준 것 — 화면은 영문)")
    try:
        from bot import etf_holdings as eh
        for m in ("KR", "US"):
            got = []
            for t in eh.etfs(m):
                h = eh.load(t) or {}
                got.append(f"{t.split('.')[0]}:{len(h.get('rows') or [])}"
                           + ("✗" if h.get("error") and h.get("tried_day") != h.get("fetched_day") else ""))
            print(f"③ {m} ETF 보유 목록: {' '.join(got)}")
            for t in eh.etfs(m):
                h = eh.load(t) or {}
                if h.get("error"):
                    print(f"   {t}: {h['error']}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"③ ETF 보유 목록: 판정 불가({type(exc).__name__}: {exc})")
    print("판정: " + ("✅ 다섯 나라 다 최신" if not bad else f"❌ {bad}개 나라 수집 실패 — 사유는 위"))
    return 0 if not bad else 1


def probe(market: str | None = None) -> int:
    """원천을 **받아 보기만** 한다(아무것도 안 쓴다) — 나라마다 응답 모양을 잰다."""
    tv = _scanner()
    print(f"# world_sectors --probe · 지문 {_src_sig()}")
    rc = 0
    for m in ([market] if market else list(MARKETS)):
        try:
            got = tv.scan(m)
        except tv.ScanError as exc:
            print(f"❌ {m}: {exc.kind} — {exc.reason}")
            rc = 1
            continue
        rows = got["rows"]
        norm = [r for r in (normalize(x) for x in rows) if r]
        agg = aggregate(norm)
        cols = [*tv.CORE, *tv.OPTIONAL]
        empty = [c for c in cols if c not in got["dropped"]
                 and sum(1 for r in rows if r.get(c) not in (None, "")) < 0.5 * len(rows)]
        print(f"✅ {m}: 원천 totalCount {got['total']} · 받은 행 {len(rows):,} · {got['pages']}쪽 · "
              f"섹터 {len(agg['sec'])} · 업종 {len(agg['ind'])} · 시장 등락 "
              f"{_fmt_ret(ret(agg['mkt']))}")
        if got["dropped"]:
            print(f"   원천이 몰라 뺀 열: {', '.join(got['dropped'])}")
        if empty:
            print(f"   절반 넘게 비어 온 열: {', '.join(empty)}")
        han = sum(1 for r in norm if re.search("[가-힣]", r[1]))
        print(f"   종목 이름: 한글 {han:,}/{len(norm):,} · 예: {', '.join(r[1] for r in norm[:3])}")
        print(f"   표본: {json.dumps(rows[0], ensure_ascii=False)[:400]}")
    return rc


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bot.world_sectors")
    ap.add_argument("--why", action="store_true", help="읽기 전용 상태 진단")
    ap.add_argument("--probe", nargs="?", const="", metavar="시장",
                    help="원천을 받아 보기만(아무것도 안 쓴다) — KR·US·JP·TW·CN_A")
    ap.add_argument("--run", action="store_true", help="한 주기 수집 + 페이지 재생성")
    a = ap.parse_args(argv)
    try:
        from bot.env_keys import env_key  # noqa: F401  (.env 를 읽는 단일 경로)
    except Exception:                                          # noqa: BLE001
        pass
    if a.probe is not None:
        return probe(a.probe or None)
    if a.run:
        res = tick_and_render()
        print(json.dumps({k: v for k, v in res.items() if k != "holdings"}, ensure_ascii=False))
        return 0
    return why()


if __name__ == "__main__":
    import sys
    sys.exit(main())
