"""KR 거래량 상위 — 네이버 domestic 랭킹 (사용자 2026-09-16).

"네이버와 같은 형식으로 거래량상위라는 대시보드도 만들어주고" — 네이버 실시간
랭킹 '거래량 상위' 의 칼럼(종목명·현재가·전일대비·거래량·거래대금·고가·저가·
시가총액)을 그대로 옮긴다.

⚠️ **정렬 키를 우리가 지어내지 않는다.** 이 엔드포인트(`domestic/stock/list`)
가 받는 `sortType` 의 enum 은 원천이 갖고 있고, 이름을 추측해 배선하면 죽은
경로를 배포한다(#151·#345). 그래서 **원천에게 묻는다** — 미끼 값을 보내면
zod 검증이 허용값 목록을 적어 돌려준다(테마 보드가 같은 방식으로 상한 200 을
알아냈다, #350·#353). 배운 키는 디스크에 적어 두고, 그 키가 다시 거절되면
지우고 **다시 배운다**(원천이 enum 을 바꾸면 따라간다, #24 — 목록을 우리가
적으면 새 이름을 못 잡는다).

고가·저가는 목록 응답이 줄 때만 싣는다. 안 주면 지어내지 않고 **비운 사유를
말한다**(#32·#43 — 빈칸이 낫고, 왜 비었는지도 말해야 한다).
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

def pick_volume_sort(allowed: tuple) -> str:
    """원천이 밝힌 허용값 → **거래량** 정렬 키(순수). 없으면 "".

    거래'대금'(value/amount)은 다른 보드이므로 배제한다 — 이름이 비슷하다고
    받으면 화면이 제목과 다른 것을 그린다(#34·#221).
    """
    vol, best = [], ""
    for v in allowed or ():
        lv = v.lower()
        if "volume" not in lv:
            continue
        if "value" in lv or "amount" in lv:
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


def learn_sort_type(force: bool = False) -> tuple[str, str]:
    """(정렬 키, 사유). 디스크에 배운 게 있으면 그대로, 없으면 원천에게 묻는다."""
    from bot.finviz_client import _cache_write, _cached
    if not force:
        c = _cached(_SORT_CACHE, ttl=30 * 86400)
        if isinstance(c, dict) and c.get("sort"):
            return str(c["sort"]), ""
    from bot.naver_sector_client import allowed_values, list_truncated
    _d, why = _fetch(_PARAM_JUNK, 1)
    vals = allowed_values(why)
    if not vals:
        if list_truncated(why):
            return "", "원천이 허용값을 적어 보냈지만 사유 길이 제한에 잘렸습니다"
        return "", (why or "원천이 미끼 값을 거절하지 않았습니다 — "
                    "이 엔드포인트는 sortType 을 검증하지 않는 것으로 보입니다")
    sort = pick_volume_sort(vals)
    if not sort:
        return "", ("원천이 밝힌 허용값에 거래량 정렬이 없습니다: "
                    + ", ".join(vals))
    _cache_write(_SORT_CACHE, {"sort": sort, "allowed": list(vals)})
    log.info("kr_volume: 원천이 밝힌 정렬 키 채택 %s (허용 %d종)", sort, len(vals))
    return sort, ""


def fetch_kr_volume_top(limit: int = 50) -> dict:
    """거래량 상위 — {rows, ts, sort, reason, has_hl, source}. graceful."""
    from bot.finviz_client import _cache_write, _cached, _now_label
    out: dict = {"rows": [], "ts": _now_label(), "sort": "", "reason": "",
                 "has_hl": False,
                 "source": "네이버 증권 거래량 상위(전종목·한글명)"}
    c = _cached(_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c.get("rows"):
        return c
    sort, why = learn_sort_type()
    if not sort:
        out["reason"] = why
        stale = _cached(_CACHE, ttl=86400)
        if isinstance(stale, dict) and stale.get("rows"):
            stale = dict(stale)
            stale["reason"] = why
            stale["stale"] = True
            return stale
        return out
    out["sort"] = sort
    rows: list = []
    reason = ""
    for page in range(1, limit // _PAGE_SIZE + 2):
        st, why = _fetch(sort, page)
        if not st:
            reason = why
            # 배운 키가 거절되면 **다시 배운다**(원천이 enum 을 바꿨을 수 있다).
            if page == 1 and _nd.status_from(why or "") in (400, 422):
                sort2, why2 = learn_sort_type(force=True)
                if sort2 and sort2 != sort:
                    out["sort"] = sort = sort2
                    st, reason = _fetch(sort, 1)
            if not st:
                break
        rows += st
        if len(st) < _PAGE_SIZE or len(rows) >= limit:
            break
    from bot.naver_ranking_client import _is_real_stock
    out["rows"] = [row_from(s) for s in rows if _is_real_stock(s)][:limit]
    out["has_hl"] = any(r.get("high") is not None for r in out["rows"])
    if not out["has_hl"] and out["rows"]:
        # 값이 없으면 왜 없는지 말한다 — 침묵하면 '0' 으로 읽힌다(#43·#181).
        out["reason"] = "원천 목록이 고가·저가를 주지 않아 두 칸은 비어 있습니다"
    elif not out["rows"]:
        out["reason"] = reason or "원천이 0행"
    if out["rows"]:
        _cache_write(_CACHE, out)
    return out
