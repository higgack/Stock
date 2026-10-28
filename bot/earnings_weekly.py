"""주간 어닝 캘린더(미국) — 월~금 5열 · 장전/장후 칸 · 관심종목 체크포인트 (사용자 2026-10-28).

월간 달력(`bot.earnings_calendar`)과 **같은 원천·같은 캐시**를 읽는다(#38) —
Finnhub 실적(`us_month` — 최근 7일이 걸친 달 1h · 나머지 6h 캐시). 이 페이지는 재료를 새로 받지 않고 주 단위로
다시 묶을 뿐이다.

한국은 싣지 않는다(사용자 2026-10-28 "실적발표일 공개를 안하면 그냥 빼줘") —
국내 기업은 실적 발표일을 미리 공지하지 않고, 월간 달력이 싣는 KIND IR일정은
실적 발표일이 아니라 기업설명회 일정이다.

표시 순서(칸마다 상위 N개, 나머지는 '+N건 더보기'):
  ① ★중요 → 관심종목  ② Finnhub **매출 추정치** 큰 순.
  시총으로 거르지 않는다 — 시총은 원천 응답에 없고, 받으려면 종목마다 호출이
  든다. 매출 추정치는 같은 응답에 이미 있다(#150).

날짜 기준: 미국 동부시간(ET) 발표일(Finnhub 원문).
"""

from __future__ import annotations

import html as _html
import logging
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.earnings_weekly")

_KST = timezone(timedelta(hours=9))
_WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일")
_SHOW = 6                      # 칸마다 먼저 보이는 타일 수
# 원천이 말하는 날짜 연도 범위 — 형제 `/earnings` 핸들러와 같다. 밖이면 오늘 주로(달력 끝에서 OverflowError, 무의미한 원천 호출 방지).
_YEARS = (2000, 2099)


def et_today() -> date:
    """미국 동부시간의 오늘 — 열의 날짜가 ET 발표일이므로 '오늘'·기본 주도 ET 로 잰다.
    KST 화요일 07:00 은 ET 월요일 18:00 이라 KST 로 재면 월요일 장후 발표가 '지난 날' 이 된다."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:                                          # noqa: BLE001 — tzdata 없음
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def week_start(d: date) -> date:
    """d 가 속한 주의 월요일. 토·일이면 **다음 주** 월요일 — 주말에 여는
    사람은 다가오는 주를 보러 온다(원본 이미지도 금요일에 다음 주를 낸다)."""
    if d.weekday() >= 5:
        return d + timedelta(days=7 - d.weekday())
    return d - timedelta(days=d.weekday())


def _months(mon: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in range(5):
        d = mon + timedelta(days=i)
        if (d.year, d.month) not in out:
            out.append((d.year, d.month))
    return out


def _norm(t) -> str:
    """대문자 + `-` 를 `.` 로 — 관심종목 `BRK-B` 와 Finnhub `BRK.B` 를 같은 종목으로 본다."""
    return str(t or "").upper().strip().replace("-", ".")


def _favorites() -> tuple[set[str], set[str]]:
    """(관심종목 키, ★중요 키) — 키는 `_norm` 한 티커."""
    _k = _norm
    try:
        from bot import market_favorites as mf
        favs = {_k(f.get("ticker")) for f in mf.get_favorites()}
        try:
            stars = {_k(t) for t in mf.starred_tickers()}
        except Exception:                                      # noqa: BLE001
            stars = set()
        return favs - {""}, stars - {""}
    except Exception as exc:                                   # noqa: BLE001
        log.warning("earnings_weekly: 관심종목 읽기 실패 — %s", exc)
        return set(), set()


def _us_events(mon: date) -> tuple[list[dict], str]:
    from bot import earnings_calendar as ec
    days = {(mon + timedelta(days=i)).isoformat() for i in range(5)}
    rows: list[dict] = []
    reasons: list[str] = []
    for y, m in _months(mon):
        r, why = ec.us_month(y, m)
        rows += [e for e in r if e.get("date") in days]
        if why:
            reasons.append(why)
    # 사유는 달 순서가 아니라 **행동 가능한 순서**로 고른다(#82·#275) — 9월은 0건,
    # 10월은 호출 실패인 주를 "0건으로 응답" 이라 적으면 장애가 가려진다.
    # 값이 있어도 한 달이 실패했으면 그 사실은 남긴다(#43).
    if rows:
        return rows, ("http" if "http" in reasons else "")
    return rows, next((r for r in ("key", "http", "empty") if r in reasons), "")


def _rev(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _slot(e: dict) -> str:
    return {"bmo": "pre", "amc": "post"}.get(str(e.get("hour") or ""), "tbd")


_SLOT_LABEL = {"pre": "☀ 장전 발표", "post": "☾ 장후 발표", "tbd": "· 시간 미정"}


def build_week(mon: date) -> dict:
    """화면 재료 — 렌더와 분리해 값으로 잰다."""
    favs, stars = _favorites()
    raw, reason = _us_events(mon)
    try:
        from bot.earnings_calendar import _us_name_map
        names = _us_name_map()
    except Exception:                                          # noqa: BLE001
        names = {}
    items: list[dict] = []
    seen: set = set()
    for e in raw:
        sym = str(e.get("symbol") or "").upper()
        if not sym or (sym, e.get("date")) in seen:
            continue
        seen.add((sym, e.get("date")))
        items.append({
            "key": sym, "sym": sym, "date": e.get("date"), "slot": _slot(e),
            "name": e.get("name") or names.get(sym) or names.get(sym.replace(".", "-")) or "",
            "fav": _norm(sym) in favs, "star": _norm(sym) in stars,
            "link": "lookup/" + quote(sym, safe=".-"),
            "eps": e.get("eps_estimate"), "rev": e.get("revenue_estimate"),
            "eps_act": e.get("eps_actual"), "rev_act": e.get("revenue_actual"),
            "_rank": -_rev(e.get("revenue_estimate")),
        })
    items.sort(key=lambda x: (not x["star"], not x["fav"], x["_rank"], x["sym"]))
    days = []
    for i in range(5):
        d = (mon + timedelta(days=i)).isoformat()
        day = [x for x in items if x["date"] == d]
        slots = {s: [x for x in day if x["slot"] == s] for s in ("pre", "post", "tbd")}
        days.append({"date": d, "label": _WEEKDAYS[i], "total": len(day),
                     "slots": slots, "favs": [x for x in day if x["fav"]]})
    return {"mon": mon.isoformat(), "days": days, "total": len(items),
            "reason": reason, "n_fav": sum(1 for x in items if x["fav"])}


def _money(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    for div, sfx in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            return f"${v / div:.1f}{sfx}"
    return f"${v:,.0f}"


def _tile(x: dict, hidden: bool) -> str:
    cls = "ew-tile" + (" fav" if x["fav"] else "") + (" star" if x["star"] else "")
    hide = ' style="display:none" data-extra="1"' if hidden else ""
    sym = _html.escape(str(x["sym"]))
    nm = _html.escape(str(x["name"] or ""))
    sub = nm if nm and nm != sym else ""
    star = "★" if x["star"] else ""
    body = (f'<span class="ew-sym">{star}{sym}</span>'
            + (f'<span class="ew-nm">{sub}</span>' if sub else ""))
    inner = f'<a href="{_html.escape(x["link"])}">{body}</a>'
    return f'<div class="{cls}"{hide} title="{nm}">{inner}</div>'


def _slot_html(slot: str, rows: list[dict]) -> str:
    if not rows:
        return (f'<div class="ew-slot empty"><div class="ew-sh">{_SLOT_LABEL[slot]}</div>'
                '<div class="ew-none">—</div></div>')
    shown = min(_SHOW, len(rows))
    tiles = "".join(_tile(x, i >= _SHOW) for i, x in enumerate(rows))
    more = (f'<div class="ew-more" data-n="{len(rows) - shown}">+{len(rows) - shown}건 더보기</div>'
            if len(rows) > shown else "")
    return (f'<div class="ew-slot"><div class="ew-sh">{_SLOT_LABEL[slot]}</div>'
            f'<div class="ew-cnt">전체 {len(rows)} · 표시 {shown}</div>'
            f'<div class="ew-grid">{tiles}</div>{more}</div>')


# ── 발표 후 종가(발표일·다음 거래일) ─────────────────────────────────────────
# 네트워크는 여기서만 — 발표치가 채워진 관심종목만, 확정된 종가만 디스크에 굳힌다(확정 종가는 안 바뀐다).
_CLOSE_MARGIN_MIN = 20         # 정규장 마감(16:00 ET) 뒤 이 시간이 지나야 그날 봉을 종가로 본다
_CLOSE_RETRY_MIN = 10          # 못 받았으면 이 시간 동안은 다시 묻지 않는다(렌더마다 20초 대기 방지, #303)
_CLOSE_KEEP_DAYS = 120
_D1_GAP_DAYS = 5               # 발표일 뒤 첫 봉이 이보다 멀면 Yahoo 가 하루를 빠뜨린 것 — 다음날 종가로 안 쓴다(#29)


def _et_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:                                          # noqa: BLE001 — tzdata 없음
        return datetime.now(timezone.utc) - timedelta(hours=5)


def _closes_path() -> Path:
    return Path.home() / ".tradingagents" / "cache" / "earnings_cal" / "closes.json"


def _load_closes() -> dict:
    try:
        import json
        d = json.loads(_closes_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:                                          # noqa: BLE001 — 없음·파손은 빈 캐시(다시 받는다, #331)
        return {}


def _save_closes(cache: dict) -> None:
    import json
    import os
    try:
        p = _closes_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        log.warning("earnings_weekly: 종가 캐시 저장 실패 — %s", exc)


def _confirmed(d: str, now: datetime) -> bool:
    """그 날(ET)의 봉이 마감 뒤 확정된 종가인가 — 오늘 봉은 마감 전이면 장중 값이다."""
    dd = date.fromisoformat(d)
    today = now.date()
    if dd < today:
        return True
    return dd == today and now.hour * 60 + now.minute >= 16 * 60 + _CLOSE_MARGIN_MIN


def _next_weekday(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def _ysym(sym: str) -> str:
    return sym.replace(".", "-")


def _reported(x: dict) -> bool:
    return _num(x.get("eps_act")) is not None or _num(x.get("rev_act")) is not None


def _px_view(sym: str, d: str, cache: dict, now: datetime) -> dict:
    """한 종목의 발표일·다음 거래일 종가 — 값 = (날짜, 종가) · 못 정했으면 사유 `wait`(마감 전) / `miss`(마감 뒤인데 없음)."""
    bars = (cache.get("bars") or {}).get(_ysym(sym)) or {}
    out: dict = {}
    if not _confirmed(d, now):
        out["d0"] = {"wait": True}
    elif isinstance(bars.get(d), (int, float)):
        out["d0"] = {"d": d, "px": float(bars[d])}
    else:
        out["d0"] = {"miss": True}
    nxt = _next_weekday(date.fromisoformat(d)).isoformat()
    if not _confirmed(nxt, now):
        out["d1"] = {"wait": True}
    else:
        later = sorted(k for k in bars if k > d)
        k = later[0] if later else None
        if k and (date.fromisoformat(k) - date.fromisoformat(d)).days <= _D1_GAP_DAYS \
                and isinstance(bars[k], (int, float)):
            out["d1"] = {"d": k, "px": float(bars[k])}
        else:
            out["d1"] = {"miss": True}
    return out


def _attach_closes(data: dict, now: datetime | None = None, download=None) -> None:
    """발표치가 채워진 관심종목 줄에 `px`(발표일·다음 거래일 종가)를 단다 — 곁들이라 어떤 실패도 화면을 멈추지 않는다(#315)."""
    now = now or _et_now()
    rows = [x for d in data["days"] for x in d["favs"]
            if _reported(x) and x.get("date") and date.fromisoformat(x["date"]) <= now.date()]
    if not rows:
        return
    cache = _load_closes()
    bars = cache.setdefault("bars", {})

    def _lacking(x) -> bool:
        v = _px_view(x["sym"], x["date"], cache, now)
        return bool(v["d0"].get("miss") or v["d1"].get("miss"))

    need = sorted({_ysym(x["sym"]) for x in rows if _lacking(x)})
    t = now.timestamp()
    if need and t - float(cache.get("tried") or 0) >= _CLOSE_RETRY_MIN * 60:
        why = ""
        try:
            from bot.finviz_client import yf_paused
            paused = yf_paused()
        except Exception:                                      # noqa: BLE001
            paused = False
        if paused:
            why = "yfinance 일시정지(YF_PAUSE) — 종가를 묻지 않음"
        else:
            if download is None:
                from bot.holdings_news import _yf_daily as download
            start = (date.fromisoformat(min(x["date"] for x in rows)) - timedelta(days=3)).isoformat()
            end = (now.date() + timedelta(days=1)).isoformat()
            try:
                got, why = download(need, start, end)
            except Exception as exc:                           # noqa: BLE001
                got, why = {}, f"종가 조회 실패({type(exc).__name__})"
            for y, b in (got or {}).items():
                # 확정된 종가만 굳힌다 — 장중 봉은 다음 응답이 마감값으로 갈아 끼운다
                keep = {d: v for d, v in b.items() if _confirmed(d, now)}
                if keep:
                    bars.setdefault(y, {}).update(keep)
        cache["tried"], cache["why"] = t, why
        old = (now.date() - timedelta(days=_CLOSE_KEEP_DAYS)).isoformat()
        for y in list(bars):
            bars[y] = {d: v for d, v in bars[y].items() if d >= old}
            if not bars[y]:
                bars.pop(y)
        _save_closes(cache)
    for x in rows:
        x["px"] = _px_view(x["sym"], x["date"], cache, now)


def _px_bit(x: dict) -> str:
    """체크포인트 줄 끝의 종가 조각 — 기준 날짜를 같이 적는다(발표일과 다음 거래일은 요일이 건너뛸 수 있다)."""
    p = x.get("px")
    if not p:
        return ""

    def one(lbl: str, v: dict) -> str:
        if "px" in v:
            return f"{lbl} {v['d'][5:]} ${v['px']:,.2f}"
        return f"{lbl} 종가 대기" if v.get("wait") else f"{lbl} 종가 미수신"

    return " · ".join((one("발표일", p["d0"]), one("다음날", p["d1"])))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None           # NaN 은 값이 아니다


def _vs(act, est) -> str:
    """발표치가 추정치보다 크면 ▲, 작으면 ▼ — 둘 중 하나라도 없으면 표시하지 않는다."""
    a, e = _num(act), _num(est)
    if a is None or e is None or a == e:
        return ""
    return ' <span class="ew-up">▲</span>' if a > e else ' <span class="ew-dn">▼</span>'


def _check_bits(x: dict) -> list[str]:
    """체크포인트 한 줄의 조각(HTML) — 추정치 옆에 발표치(있을 때만)."""
    bits = []
    has_eps = x["eps"] is not None
    if has_eps:
        bits.append(_html.escape(f"EPS 추정 {x['eps']}"))
    if _num(x.get("eps_act")) is not None:
        # 추정치가 없으면 무엇의 발표치인지 이름을 붙인다
        bits.append(_html.escape(f"{'' if has_eps else 'EPS '}발표 {x['eps_act']}")
                    + _vs(x["eps_act"], x["eps"]))
    has_rev = bool(x["rev"])
    if has_rev:
        bits.append(_html.escape(f"매출 추정 {_money(x['rev'])}"))
    if _num(x.get("rev_act")) is not None:
        bits.append(_html.escape(f"{'' if has_rev else '매출 '}발표 {_money(x['rev_act'])}")
                    + _vs(x["rev_act"], x["rev"]))
    bits.append({"pre": "장전", "post": "장후"}.get(x["slot"], "시간 미정"))
    px = _px_bit(x)
    if px:
        bits.append(_html.escape(px))
    return bits


def _check_html(day: dict) -> str:
    # 좁은 화면에선 체크포인트 칸이 요일 열 아래로 따로 쌓이므로 칸이 스스로 날짜를 말한다.
    head = f'<div class="ew-ckd">{day["label"]} {day["date"][5:]}</div>'
    rows = day["favs"]
    if not rows:
        return f'<div class="ew-ck">{head}<div class="ew-none">관심종목 일정 없음</div></div>'
    lines = []
    for x in rows:
        lines.append(f'<div class="ew-ckl"><b>{"★" if x["star"] else ""}'
                     f'{_html.escape(str(x["sym"]))}</b> '
                     f'{" · ".join(_check_bits(x))}</div>')
    return '<div class="ew-ck">' + head + "".join(lines) + "</div>"


def _asof(mon: date) -> str:
    """원천 캐시가 받아진 시각(KST) — 렌더 시각이 아니다(규칙 10b)."""
    try:
        from bot.earnings_calendar import _CACHE_DIR as d
        ps = [d / f"{y:04d}-{m:02d}.json" for y, m in _months(mon)]
        ts = [p.stat().st_mtime for p in ps if Path(p).exists()]
        if ts:
            return datetime.fromtimestamp(min(ts), tz=_KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:                                          # noqa: BLE001
        pass
    return ""


_CSS = """<style>
:root{--bg:#f3f8f2;--card:#fff;--hd:#1e5b2e;--hd-text:#fff;--soft:#e7f2e4;--border:#cfe1cb;
--text:#16261a;--muted:#5d6f61;--accent:#1e7a3a;--fav:#1e7a3a;--star:#b7791f;--ck:#fff8e8;--title:#1e5b2e}
[data-theme="dark"]{--bg:#0e1511;--card:#141d17;--hd:#1d4a2a;--hd-text:#eaf5ec;--soft:#18241b;
--border:#2b3d30;--text:#e3ede5;--muted:#93a597;--accent:#6fcf8c;--fav:#6fcf8c;--star:#e6b450;--ck:#1c1a12;--title:#6fcf8c}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif;
background:var(--bg);color:var(--text);padding:20px;max-width:1400px;margin:0 auto}
a{color:inherit;text-decoration:none}
.back{color:var(--accent);font-size:13px;display:inline-block;margin-bottom:10px}
.ew-top{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;margin-bottom:14px}
.ew-title{flex:1;min-width:260px}
.ew-title .en{font-size:40px;font-weight:800;color:var(--title);line-height:1;letter-spacing:1px}
.ew-title .en2{font-size:22px;font-weight:800;color:var(--accent);letter-spacing:1px}
.ew-title .ko{font-size:15px;font-weight:700;margin-top:6px}
.ew-box{background:var(--soft);border:1px solid var(--border);border-radius:12px;padding:12px 18px;min-width:240px}
.ew-box .rng{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}
.ew-box .sub{color:var(--muted);font-size:13px;margin-top:3px}
.ew-nav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.ew-btn{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:6px 14px;font-size:13px;font-weight:600}
.ew-btn.active{background:var(--hd);color:var(--hd-text);border-color:var(--hd)}
.ew-sep{flex:1}
.ew-note{background:rgba(255,170,0,.12);border-radius:8px;padding:9px 12px;font-size:12px;color:var(--muted);margin-bottom:12px}
.ew-cols{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
.ew-day{display:flex;flex-direction:column;gap:8px;min-width:0}
.ew-dh{background:var(--hd);color:var(--hd-text);border-radius:12px 12px 4px 4px;padding:10px 12px}
.ew-dh .w{font-size:19px;font-weight:800}
.ew-dh .d{font-size:13px;opacity:.9;font-variant-numeric:tabular-nums}
.ew-bar{height:5px;border-radius:3px;background:rgba(255,255,255,.25);margin-top:7px;overflow:hidden}
.ew-bar i{display:block;height:100%;background:#7ee29a}
.ew-dh.today{outline:3px solid var(--star)}
.ew-slot{background:var(--soft);border:1px solid var(--border);border-radius:10px;padding:9px}
.ew-sh{font-weight:800;font-size:14px}
.ew-cnt{color:var(--muted);font-size:11px;margin:2px 0 6px}
.ew-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}
.ew-tile{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:6px 5px;text-align:center;min-width:0}
.ew-tile.fav{border:2px solid var(--fav)}
.ew-tile.star{border:2px solid var(--star)}
.ew-tile a:hover .ew-sym{text-decoration:underline}
.ew-sym{display:block;font-weight:800;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ew-nm{display:block;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ew-more{color:var(--muted);font-size:11px;margin-top:6px;cursor:pointer}
.ew-more:hover{color:var(--accent);text-decoration:underline}
.ew-none{color:var(--muted);font-size:12px}
.ew-slot.empty{opacity:.7}
.ew-ckh{font-weight:800;font-size:16px;margin:18px 0 8px}
.ew-ckh span{font-size:11px;font-weight:400;color:var(--muted);margin-left:6px}
.ew-ck{background:var(--ck);border:1px solid var(--border);border-radius:10px;padding:9px;font-size:12px;line-height:1.55;min-height:60px}
.ew-ckl b{color:var(--accent)}
.ew-up{color:#2f9e55;font-weight:700}.ew-dn{color:#d9534f;font-weight:700}
.ew-ckd{font-weight:700;font-size:11px;color:var(--muted);margin-bottom:4px}
.ew-foot{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;border-top:1px solid var(--border);
margin-top:18px;padding-top:10px;font-size:11px;color:var(--muted)}
@media(max-width:900px){.ew-cols{grid-template-columns:1fr}.ew-title .en{font-size:30px}body{padding:12px}}
</style>"""


def render_page(start: date | None = None,
                today: date | None = None) -> str:
    today = today or et_today()
    if start is not None and not (_YEARS[0] <= start.year <= _YEARS[1]):
        start = None
    mon = week_start(start or today)
    data = build_week(mon)
    try:
        _attach_closes(data)
    except Exception as exc:                                   # noqa: BLE001 — 곁들이가 본체를 지우지 않게(#315)
        log.warning("earnings_weekly: 발표 후 종가 붙이기 실패 — %s", exc)
    fri = mon + timedelta(days=4)
    tz = "미국 동부시간(ET) 발표일 기준"
    src = "Finnhub 실적 일정"

    def _wk(d: date, lbl: str, act: bool = False) -> str:
        return (f'<a class="ew-btn{" active" if act else ""}" '
                f'href="?week={d.isoformat()}">{lbl}</a>')

    this_mon = week_start(today)
    nav = (_wk(mon - timedelta(days=7), "← 지난주")
           + _wk(this_mon, "이번 주" if today.weekday() < 5 else "다가오는 주", mon == this_mon)
           + _wk(mon + timedelta(days=7), "다음 주 →")
           + f'<a class="ew-btn" href="earnings?month={mon:%Y-%m}&amp;market=us">📅 월간 달력</a>')

    note = ""
    if data["total"] == 0:
        from bot.earnings_calendar import _US_REASON
        why = _US_REASON.get(data["reason"], "")
        note = _html.escape(f"이 주에 일정이 없습니다 — {why}" if why else
                            "이 주에 원천이 준 일정이 없습니다.")
    elif data["reason"] == "http":
        note = "이 주에 걸친 달 중 하나를 원천에서 받지 못해 일부 일정이 빠졌을 수 있습니다."
    note_html = f'<div class="ew-note">{note}</div>' if note else ""

    peak = max([d["total"] for d in data["days"]] + [1])
    cols, cks = [], []
    for d in data["days"]:
        pct = round(100 * d["total"] / peak)
        dd = date.fromisoformat(d["date"])
        cls = "ew-dh today" if dd == today else "ew-dh"
        slots = ["pre", "post"] + (["tbd"] if d["slots"]["tbd"] else [])
        cols.append(
            f'<div class="ew-day"><div class="{cls}"><div class="w">{d["label"]}</div>'
            f'<div class="d">{d["date"]}</div><div class="d">전체 {d["total"]}건</div>'
            f'<div class="ew-bar"><i style="width:{pct}%"></i></div></div>'
            + "".join(_slot_html(s, d["slots"][s]) for s in slots)
            + "</div>")
        cks.append(_check_html(d))

    # 원천을 못 받은(키 없음·호출 실패) 주는 옛 캐시 시각을 '수신' 이라 적지 않는다(규칙 10b) —
    # 그 파일의 내용은 이 화면에 없다.
    asof = "" if data["reason"] in ("key", "http") else _asof(mon)
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>주간 어닝 캘린더</title>
{_CSS}
</head><body>
<a class="back" href="market.html">← 홈으로</a>
<div class="ew-top">
  <div class="ew-title"><div class="en">EARNINGS</div><div class="en2">WEEKLY CALENDAR</div>
  <div class="ko">주간 어닝 캘린더 · 🇺🇸 미국</div></div>
  <div class="ew-box"><div class="rng">{mon.isoformat()} ~ {fri.isoformat()}</div>
  <div class="sub">전체 {data["total"]}건 · 관심종목 {data["n_fav"]}건</div>
  <div class="sub">{tz}</div></div>
</div>
<div class="ew-nav">{nav}</div>
{note_html}
<div class="ew-cols">{"".join(cols)}</div>
<div class="ew-ckh">📌 관심종목 체크포인트<span>관심종목에 담긴 종목만 · ★=중요 표시 · 발표 = Finnhub 발표치(▲ 추정 상회 · ▼ 하회) · 발표일·다음날 = 정규장 종가(Yahoo, 마감 확정 뒤)</span></div>
<div class="ew-cols">{"".join(cks)}</div>
<div class="ew-foot">
  <div>출처: {_html.escape(src)}{(" · " + asof + " 수신") if asof else ""}</div>
  <div>표시 순서: ★중요 → 관심종목(테두리) → 매출 추정치(Finnhub) 큰 순 · 칸마다 상위 {_SHOW}개, 나머지는 '더보기' · 최근 7일 일정은 페이지를 열 때 받은 지 1시간이 지났으면 다시 받음(발표치는 Finnhub 가 채운 뒤 보임) · 발표 일정은 변경될 수 있습니다</div>
</div>
<script>
(function(){{var h=parseInt(new Intl.DateTimeFormat('en-US',{{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}}).format(new Date()),10)%24;document.documentElement.dataset.theme=(h>=19||h<7)?'dark':'light';}})();
document.querySelectorAll('.ew-more').forEach(function(b){{b.addEventListener('click',function(){{
  var ex=b.parentNode.querySelectorAll('[data-extra]');var show=ex.length&&ex[0].style.display==='none';
  ex.forEach(function(x){{x.style.display=show?'':'none';}});
  b.textContent=show?'접기':('+'+b.dataset.n+'건 더보기');}});}});
</script>
</body></html>"""
