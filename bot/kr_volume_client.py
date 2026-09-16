"""KR 거래량 상위 — 네이버 domestic 랭킹 (사용자 2026-09-16).

"네이버와 같은 형식으로 거래량상위라는 대시보드도 만들어주고" — 네이버 실시간
랭킹 '거래량 상위' 의 칼럼(종목명·현재가·전일대비·거래량·거래대금·고가·저가·
시가총액)을 **그 순서 그대로** 옮긴다(`stock_panel(show_hl=True)`).

⚠️ **정렬 키를 우리가 지어내지 않는다.** 이 엔드포인트(`domestic/stock/list`)
가 받는 `sortType` 의 enum 은 원천이 갖고 있고, 이름을 추측해 배선하면 죽은
경로를 배포한다(#151·#345). 그래서 **원천에게 묻는다** — 미끼 값을 보내면
zod 검증이 허용값 목록을 적어 돌려준다(테마 보드가 같은 방식으로 상한 200 을
알아냈다, #350·#353). 배운 키는 디스크에 적어 두고, 그 키가 다시 거절되면
지우고 **다시 배운다**(원천이 enum 을 바꾸면 따라간다, #24 — 목록을 우리가
적으면 새 이름을 못 잡는다).

고가·저가는 목록 응답이 줄 때만 싣는다. 안 주면 지어내지 않고 **비운 사유를
말한다**(#32·#43 — 빈칸이 낫고, 왜 비었는지도 말해야 한다).

⚠️ 사유는 **모아서** 말한다 — 한 변수에 `=` 로 여러 번 쓰면 나중 것이 앞의
것을 지운다(#207). 그리고 **부분 수신은 캐시에 굽지 않는다**(#280).
"""
from __future__ import annotations

import logging

from bot import naver_diag as _nd

log = logging.getLogger("bot.kr_volume_client")

_BASE = "https://m.stock.naver.com/front-api"
_LIST = f"{_BASE}/domestic/stock/list"
_HDRS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
         "Accept": "application/json", "Referer": "https://m.stock.naver.com/"}
_PAGE_SIZE = 50
_SORT_CACHE = "kr_volume_sort.json"     # 원천에게 배운 정렬 키
_CACHE = "kr_volume_top.json"
_TTL = 60                               # 60초 — 화면 폴링 2분보다 **짧아야** 매
#   주기가 캐시에 걸리지 않는다(#36 TTL < 주기). 2분으로 두면 절반이 같은
#   바이트 재서빙이라 "2분마다 갱신" 이 거짓이 된다.
_PARAM_JUNK = "__probe__"
# 고가·저가 후보 키 — **추측이 아니라 관측 대기**다. 원천이 주는 이름이
# 무엇인지 모르므로 알려진 표기들을 훑고, 하나도 없으면 비운 사유를 남긴다.
_HIGH_KEYS = ("highPrice", "highPriceRaw", "high", "highestPrice")
_LOW_KEYS = ("lowPrice", "lowPriceRaw", "low", "lowestPrice")


# ── 순수 ────────────────────────────────────────────────────────────────

# 이름에 `volume` 이 들어가도 **거래량 랭킹이 아닌** 키들 — 받으면 화면이
# 제목과 다른 것을 그린다(#34·#221. 독립 리뷰 2026-09-16 L15: `volumePower`
# (체결강도)가 그 예다).
_NOT_VOLUME = ("value", "amount", "power", "strength", "rate", "ratio",
               "turnover", "change")


def pick_volume_sort(allowed: tuple) -> str:
    """원천이 밝힌 허용값 → **거래량** 정렬 키(순수). 없으면 "".

    거래'대금'(value/amount)·체결강도(power) 등은 다른 지표이므로 배제한다.
    """
    vol, best = [], ""
    for v in allowed or ():
        lv = v.lower()
        if "volume" not in lv:
            continue
        if any(w in lv for w in _NOT_VOLUME):
            continue
        vol.append(v)
    for v in vol:                      # 누적 거래량 표기를 선호(랭킹의 정의)
        if "accum" in v.lower():
            return v
    if vol:
        best = vol[0]
    return best


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _first(d: dict, keys) -> float | None:
    for k in keys:
        if k in d:
            n = _num(d.get(k))
            if n is not None:
                return n
    return None


def row_from(s: dict) -> dict:
    """네이버 목록 행 → 보드 행(순수). 고가·저가는 **있을 때만**."""
    from bot.naver_ranking_client import _kr_row
    r = dict(_kr_row(s))
    r["high"] = _first(s, _HIGH_KEYS)
    r["low"] = _first(s, _LOW_KEYS)
    return r


# ── 수집 ────────────────────────────────────────────────────────────────

def _rows(payload) -> list:
    if isinstance(payload, dict):
        res = payload.get("result")
        if isinstance(res, dict):
            for k in ("stocks", "list", "items"):
                if isinstance(res.get(k), list):
                    return res[k]
        if isinstance(res, list):
            return res
        for k in ("stocks", "list", "items"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _fetch(sort_type: str, page: int) -> tuple[list, str]:
    d, why = _nd.get_json(_LIST, headers=_HDRS, log=log, tag="kr_volume",
                          params={"sortType": sort_type, "category": "all",
                                  "page": page, "pageSize": _PAGE_SIZE})
    return _rows(d), why


def learn_sort_type(force: bool = False) -> tuple[str, str, list]:
    """(정렬 키, 사유, 미끼 응답 행). 디스크에 배운 게 있으면 그대로.

    ⚠️ 미끼 요청이 **거절되지 않으면**(원천이 sortType 을 검증 안 함) 그
    응답은 기본 정렬의 정상 목록이다 — 그걸 버리면 보드가 영영 빈다(리뷰
    H3 · #148 '없다' 고 말하기 전에 우리가 버린 건 아닌지). 호출부가 쓰도록
    같이 돌려준다.
    ⚠️ 재학습(`force`)은 **옛 키를 지우고** 시작한다 — 안 지우면 재학습이
    실패했을 때 죽은 키가 30일 TTL 동안 살아남는다(리뷰 L16).
    """
    from bot.finviz_client import _cache_write, _cached
    if not force:
        c = _cached(_SORT_CACHE, ttl=30 * 86400)
        if isinstance(c, dict) and c.get("sort"):
            return str(c["sort"]), "", []
    else:
        _cache_write(_SORT_CACHE, {})
    from bot.naver_sector_client import allowed_values, list_truncated
    rows, why = _fetch(_PARAM_JUNK, 1)
    vals = allowed_values(why)
    if not vals:
        if list_truncated(why):
            return "", "원천이 허용값을 적어 보냈지만 사유 길이 제한에 잘렸습니다", rows
        return "", (why or "원천이 미끼 값을 거절하지 않았습니다 — "
                    "이 엔드포인트는 sortType 을 검증하지 않는 것으로 보입니다"), rows
    sort = pick_volume_sort(vals)
    if not sort:
        return "", ("원천이 밝힌 허용값에 거래량 정렬이 없습니다: "
                    + ", ".join(vals)), rows
    _cache_write(_SORT_CACHE, {"sort": sort, "allowed": list(vals)})
    log.info("kr_volume: 원천이 밝힌 정렬 키 채택 %s (허용 %d종)", sort, len(vals))
    return sort, "", rows


def _finish(out: dict, raw: list, notes: list, partial: bool) -> dict:
    """원시 행 → 보드 행 + 사유 정리(순수에 가깝게 — 캐시는 호출부가)."""
    from bot.naver_ranking_client import _is_real_stock
    limit = out["limit"]
    kept = [r for r in raw if _is_real_stock(r)]
    dropped = len(raw) - len(kept)
    out["rows"] = [row_from(r) for r in kept][:limit]
    out["scanned"] = len(raw)
    out["excluded"] = dropped
    out["has_hl"] = any(r.get("high") is not None for r in out["rows"])
    if dropped:
        # 총계와 소계가 다른 모집단을 세면 사용자가 눈으로 잡는다(#45).
        notes.append(f"ETF·ETN·스팩 {dropped}종을 뺀 {len(out['rows'])}종목입니다")
    if out["rows"] and not out["has_hl"]:
        notes.append("원천 목록이 고가·저가를 주지 않아 두 칸은 비어 있습니다")
    if partial:
        notes.append("원천이 요청한 수를 다 주지 못해 일부만 실렸습니다")
    out["partial"] = partial
    out["reason"] = " · ".join(n for n in notes if n)
    return out


def fetch_kr_volume_top(limit: int = 50) -> dict:
    """거래량 상위 — {rows, ts, sort, reason, has_hl, partial, …}. graceful."""
    from bot.finviz_client import _cache_write, _cached, _now_label

    def _stale(notes: list) -> dict | None:
        """저장분 폴백 — 값이 없을 때 **모든** 실패 경로가 같이 쓴다(리뷰 M8).
        그리고 저장분이라는 사실을 화면이 말해야 한다(#306·#335)."""
        st = _cached(_CACHE, ttl=86400)
        if not (isinstance(st, dict) and st.get("rows")):
            return None
        st = dict(st)
        st["stale"] = True
        st["reason"] = " · ".join(
            [n for n in notes if n] + ["아래는 직전 저장분입니다"])
        return st

    out: dict = {"rows": [], "ts": _now_label(), "sort": "", "reason": "",
                 "has_hl": False, "partial": False, "limit": limit,
                 "scanned": 0, "excluded": 0, "stale": False,
                 "source": "네이버 증권 거래량 상위(전종목·한글명)"}
    c = _cached(_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c.get("rows"):
        return c
    notes: list = []
    sort, why, probe_rows = learn_sort_type()
    if not sort:
        notes.append(why)
        if probe_rows:
            # 미끼 응답이 정상 목록이면 그걸 쓴다 — 이미 받은 것을 버리고
            # 빈 화면을 내면 안 된다(리뷰 H3·#148). 정렬 기준은 밝힌다(#43).
            notes.append("정렬 키를 못 배워 **원천 기본 정렬**로 보여 줍니다")
            _finish(out, probe_rows, notes, partial=True)
            return out                     # 부분/기본정렬은 캐시하지 않는다(#280)
        return _stale(notes) or _finish(out, [], notes, partial=False)
    out["sort"] = sort
    raw: list = []
    partial = False
    # ⚠️ 필터(ETF·ETN·스팩) **뒤**의 수가 limit 을 채워야 한다 — 원시 수로
    # 세면 거래량 상위처럼 ETF 가 많은 랭킹에서 절반만 남는다(리뷰 H2 ·
    # 형제 `naver_ranking_client.fetch_kr_movers` 는 이미 넉넉히 받는다, #38).
    want = max(limit * 2, 100)
    for page in range(1, want // _PAGE_SIZE + 2):
        st, why = _fetch(sort, page)
        if not st:
            if page == 1 and _nd.status_from(why or "") in (400, 422):
                # 배운 키가 거절되면 **다시 배운다**(원천 enum 드리프트, #24).
                sort2, why2, _r = learn_sort_type(force=True)
                if sort2 and sort2 != sort:
                    out["sort"] = sort = sort2
                    st, why = _fetch(sort, 1)
            if not st:
                partial = bool(raw)        # 받다 끊겼으면 부분이다
                if why:
                    notes.append(why)
                break
        raw += st
        if len(st) < _PAGE_SIZE:
            break                          # 원천 목록 끝 — 부분이 아니다
        if len(raw) >= want:
            break
    if not raw:
        return _stale(notes) or _finish(out, [], notes, partial=False)
    _finish(out, raw, notes, partial=partial)
    if out["rows"] and not partial:
        _cache_write(_CACHE, out)          # 부분은 굽지 않는다(#280)
    return out
