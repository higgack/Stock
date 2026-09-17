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

고가·저가는 목록 응답이 줄 때만 싣는다. 안 주면 지어내지 않는다(#32).
⚠️ 사용자 2026-09-17: **못 가져오면 칸 자체를 뺀다** — 옛 판은 빈 두 칸을
그려 놓고 각주로 사유를 적었는데(#43), 영원히 안 오는 칸을 매번 설명하는
것은 화면만 시끄럽게 한다(#25·#260 늘 뜨는 배지는 아무것도 안 재는 것과
같다). 대신 **원천이 정말 안 주는지**를 재서 로그·`--why` 가 말한다 —
`hl_key_candidates` 가 행의 키 중 high/low 를 담은 이름을 전부 모으므로,
'원천이 안 준다' 와 '우리 파서가 이름을 모른다' 가 갈린다(#372 그대로).

⚠️ 사유는 **모아서** 말한다 — 한 변수에 `=` 로 여러 번 쓰면 나중 것이 앞의
것을 지운다(#207). 그리고 **부분 수신은 캐시에 굽지 않는다**(#280).
"""
from __future__ import annotations

import contextlib
import logging
import os

from bot import naver_diag as _nd
from bot import singleflight as _sf

log = logging.getLogger("bot.kr_volume_client")

_BASE = "https://m.stock.naver.com/front-api"
_LIST = f"{_BASE}/domestic/stock/list"
_HDRS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
         "Accept": "application/json", "Referer": "https://m.stock.naver.com/"}
_PAGE_SIZE = 50
_SORT_CACHE = "kr_volume_sort.json"     # 원천에게 배운 정렬 키
_SORT_TTL = 7 * 86400   # 배운 키를 주기적으로 다시 잰다(독립 리뷰 M2)
_LEARN_FAIL = "kr_volume_learn_fail.json"   # 학습 실패 냉각(아래 _LEARN_COOL)
_LEARN_COOL = 600       # 10분. 학습은 실패해도 **캐시되지 않으므로**, 확정이
#   안 되는 상태(휴장·원천 장애로 거래량이 전부 0·동률 → 어느 후보도 내림차순
#   판정을 못 받음)에서는 60초 TTL 마다 미끼 1 + 후보 6 = **7콜**이 나간다.
#   느림의 원인은 양이 아니라 실패 재시도다(#346·#349) — 짧게만 믿고(#303·#152
#   길게 믿으면 장애 한 번이 하루를 지운다) 식으면 반드시 다시 시도한다(#178).
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

# 이름에 `volume` 이 들어가도 **거래량 랭킹이 아닌** 키들 — 순서 힌트에서
# 뒤로 민다(#34·#221. 독립 리뷰 2026-09-16 L15: `volumePower`(체결강도)).
_NOT_VOLUME = ("value", "amount", "power", "strength", "rate", "ratio",
               "turnover", "change")
# 이름은 **시험 순서만** 정한다 — 판정은 `is_volume_desc` 실측이다(#46 위치·
# 형태로 추정하지 말 것 · #25 능력은 이름이 아니라 실측).
_VOL_HINT = ("quant", "volume", "거래량", "trade")
_TRIAL_BUDGET = 6          # 허용값 전수를 다 치지 않는다(#116 예산)
_TRIAL_ROWS = 30           # 시총순·거래대금순과 갈라지려면 5행으론 부족하다


def volume_sort_candidates(allowed: tuple) -> tuple:
    """허용값 → **시험 순서**(순수). 이름은 순서만 정하고 판정은 실측이다.

    ⚠️ 2026-09-16 VM 실측이 옛 판정을 뒤집었다: 이 엔드포인트의 허용값은
    ``marketValue|up|down|quantTop|priceTop|searchTop|newStock|management|
    high52week|low52week|dividend|konex`` — **'volume' 이 든 이름이 하나도
    없다**. 그래서 "이름에 volume 이 있는 것을 고른다"(옛 `pick_volume_sort`)
    는 무엇도 못 고르는 **발화 경로 없는 코드**였다(#291). 그렇다고
    `quantTop` 을 이름만 보고 박으면 죽은 경로를 배포하는 그 실수다
    (#151·#345) — 후보를 **실제로 불러** 거래량 내림차순인 것을 고른다.

    ⚠️ 정직하게 적자면 이름이 **순서만** 정한다는 건 절반이다(#55): 학습이
    첫 확정에서 멈추므로, 둘 이상이 실측을 통과할 수 있는 상황에서는 순서가
    곧 결과다. 실측상 `quantTop` 이 1순위라 오늘은 무해하지만, 그 전제가
    깨지면(1순위가 429 를 맞는 등) 뒤 후보가 이길 수 있다 — 그래서 배운 키의
    유효기간을 30일에서 7일로 줄여 주기적으로 다시 잰다(독립 리뷰 M2).
    """
    def _rank(v: str) -> tuple:
        lv = v.lower()
        hint = next((i for i, w in enumerate(_VOL_HINT) if w in lv), len(_VOL_HINT))
        # 2차 힌트: 이 원천의 랭킹 키는 `…Top` 으로 끝난다(quantTop·priceTop·
        # searchTop). 예산(#116)이 `dividend`·`konex` 같은 비-랭킹 키에 먼저
        # 쓰이면 진짜 후보에 닿기 전에 소진된다 — **순서만** 바꾸고 판정은
        # 여전히 실측이다.
        rank_ish = 0 if lv.endswith("top") else 1
        return (hint, rank_ish, 1 if any(w in lv for w in _NOT_VOLUME) else 0, v)
    return tuple(sorted(allowed or (), key=_rank))


def is_volume_desc(rows: list, min_rows: int = 10) -> bool:
    """이 행들이 **누적 거래량 내림차순**인가(순수) — '거래량 상위'의 정의다.

    시총순(`marketValue`)·거래대금순(`priceTop`)과 갈리는 자리다. 값이 거의
    다 같으면(휴장·빈 응답) 어떤 정렬이든 '내림차순'이라 판정 불가이므로
    **서로 다른 값의 수**를 같이 요구한다(#54 대조 0건은 통과가 아니다 ·
    #25 '있다'를 묻는 검사엔 반대 증거를 같이).
    """
    vols = [_first(r, ("accumulatedTradingVolume", "accumulatedTradingVolumeRaw"))
            for r in (rows or []) if isinstance(r, dict)]
    vols = [v for v in vols if v is not None]
    if len(vols) < min_rows or len(set(vols)) < max(3, len(vols) // 2):
        return False
    return all(a >= b for a, b in zip(vols, vols[1:]))


def hl_key_candidates(row: dict) -> tuple:
    """행의 키 중 고가·저가로 **보이는 이름 전부**(순수) — 우리가 읽는 것 포함.

    ⚠️ 이건 값이 아니라 **측정**이다. `_HIGH_KEYS`/`_LOW_KEYS` 는 알려진 표기
    목록이라, 원천이 다른 이름을 쓰면 우리는 '원천이 안 준다' 고 오판한다
    (#372 — 진단이 ❌ 를 찍으면 '원천이 안 준 것'인지 '내 파서가 못 읽은 것'
    인지부터 가를 것). 그래서 이름 후보를 **넓게** 모아 두고 판정은 사람이
    한다 — 여기서 자동으로 값을 집으면 `high52week`(52주 고가)를 오늘 고가로
    읽어 화면이 거짓말한다(#34 같은 이름 다른 시리즈).
    """
    known = set(_HIGH_KEYS) | set(_LOW_KEYS)
    out = []
    for k in (row or {}):
        lk = str(k).lower()
        if "high" in lk or "low" in lk:
            out.append(f"{k}{'' if k in known else ' (미읽음)'}")
    return tuple(sorted(out))


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


def _fetch(sort_type: str, page: int, size: int | None = None) -> tuple[list, str]:
    d, why = _nd.get_json(_LIST, headers=_HDRS, log=log, tag="kr_volume",
                          params={"sortType": sort_type, "category": "all",
                                  "page": page, "pageSize": size or _PAGE_SIZE})
    return _rows(d), why


def learn_sort_type(force: bool = False) -> tuple[str, str, list]:
    """학습 진입점 — **동시 요청은 하나만 배운다**(#113).

    학습은 미끼 1 + 후보 최대 6 = **7콜**이고 디스크 캐시는 끝난 뒤에만
    도와주므로(#113 "진행 중인 중복은 캐시가 못 막는다"), 탭 N 개가 같은
    순간에 열리면 7N 콜이 바깥 원천으로 나간다 — 그게 `naver_paused()` 를
    건드리면 네이버 보드 전부가 죽는다(#371 Blocking 과 같은 형태). 막는
    자리는 서버다.

    ⚠️ 키를 `force` 로 가른다: 진행 중인 **일반** 학습은 디스크에 배운 옛
    키를 그대로 돌려줄 수 있는데, 재학습을 부른 쪽은 바로 그 키가 거절당한
    것을 본 쪽이다 — 공유하면 죽은 키를 되돌려준다(#82 처방이 다르면 갈래도
    다르다).
    """
    return _sf.once(f"kr_volume_learn:{int(bool(force))}",
                    lambda: _learn_sort_type(bool(force)))


def _learn_sort_type(force: bool = False) -> tuple[str, str, list]:
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
        # 7일 — 옛 30일은 "한 번 잘못 배우면 한 달" 이었다(재학습 트리거가
        # 400/422 뿐인데 유효한 키는 그걸 안 낸다, 독립 리뷰 M2).
        c = _cached(_SORT_CACHE, ttl=_SORT_TTL)
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
    # 확정이 안 되는 상태에서 매 렌더 7콜을 쏘지 않는다(#346·#116). 진단·
    # 재학습(`force`)은 이 냉각을 타지 않는다 — 프로브는 계속 재야 한다(#345c).
    if not force:
        f = _cached(_LEARN_FAIL, ttl=_LEARN_COOL)
        if isinstance(f, dict) and f.get("why"):
            # 상한이 아니라 **남은 시간**을 말한다 — 형제 `_theme_fail_memo` 가
            # 이미 그렇게 한다(#202 숫자로 · #165 재지 않은 것을 적지 말 것).
            from bot.finviz_client import cache_age_sec
            age = cache_age_sec(_LEARN_FAIL)
            left = max(0, _LEARN_COOL - int(age)) if age is not None else _LEARN_COOL
            return "", f"{f['why']} · {left // 60}분 {left % 60}초 뒤 다시 시험합니다", rows
    # 이름으로 고르지 않는다 — 후보를 **실제로 불러** 거래량 내림차순인
    # 것을 고른다(#46·#151·#345. 2026-09-16 실측: 허용값 12종에 'volume' 이
    # 든 이름이 하나도 없다). 예산 안에서 첫 확정을 쓰고, 하나도 확정 안 되면
    # 그 사실과 무엇을 시험했는지 말한다(#43·#82).
    tried: list = []
    fails: list = []          # (사유) — 버리면 429 가 '내림차순 아님' 이 된다
    any_rows = False
    for cand in volume_sort_candidates(vals)[:_TRIAL_BUDGET]:
        got, why_c = _fetch(cand, 1, size=_TRIAL_ROWS)
        tried.append(cand)
        if got:
            any_rows = True
        elif why_c:
            fails.append(why_c)
        if is_volume_desc(got):
            _cache_write(_SORT_CACHE, {"sort": cand, "allowed": list(vals)})
            _cache_write(_LEARN_FAIL, {})       # 확정했으면 냉각을 푼다(#72)
            log.info("kr_volume: 실측으로 정렬 키 확정 %s (허용 %d종 · 시험 %d종)",
                     cand, len(vals), len(tried))
            return cand, "", got
    # ⚠️ 행이 **한 번도 안 온** 실행을 "불러 봤지만 내림차순이 아니었다" 로
    # 적으면 두 번 거짓말이다 — 부르긴 했지만 값을 못 받았고(#286), 유일하게
    # 행동 가능한 사실(429·타임아웃·일시정지 — 처방이 정반대다, #82·#279)을
    # 버린다. 형제(`naver_sector_client._note_theme_fail`)는 이미 그렇게 한다(#38).
    worst = min(fails, key=_nd.reason_rank) if fails else ""
    if not any_rows:
        why_fail = ("후보 %d종을 물었지만 원천이 행을 주지 않았습니다"
                    "(시험: %s)" % (len(tried), ", ".join(tried)))
        if worst:
            why_fail += f" · 최상위 사유: {worst}"
    else:
        why_fail = ("원천이 밝힌 허용값 %d종 중 %d종을 실제로 불러 봤지만 "
                    "거래량 내림차순인 것이 없었습니다(시험: %s)"
                    % (len(vals), len(tried), ", ".join(tried)))
    # 일시정지(rank 0)는 **안 물어본 것**이지 실패가 아니다 — 도장을 찍으면
    # 정지 해제 뒤까지 10분을 막는다(#79·#143·#345, 형제 가드 그대로).
    if not (worst and _nd.reason_rank(worst) == 0):
        _cache_write(_LEARN_FAIL, {"why": why_fail})
    return "", why_fail, rows


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
    # 원천 행이 고가·저가로 보이는 키를 갖고 있나 — 화면엔 안 싣고 로그·
    # `--why` 가 말한다(위 독스트링). 첫 행 하나면 충분하다(같은 스키마).
    out["hl_keys"] = list(hl_key_candidates(kept[0] if kept else {}))
    if dropped:
        # 총계와 소계가 다른 모집단을 세면 사용자가 눈으로 잡는다(#45).
        notes.append(f"ETF·ETN·스팩 {dropped}종을 뺀 {len(out['rows'])}종목입니다")
    if out["rows"] and not out["has_hl"]:
        # 화면은 칸을 아예 안 그리므로 각주를 달지 않는다(사용자 2026-09-17).
        # 대신 **로그로 알린다** — 조용한 폴백은 '가끔 이상하다'로만 보인다(#42a).
        log.info("kr_volume: 원천 목록에 고가·저가 없음 — 두 칸을 뺐습니다"
                 "(행의 high/low 후보 키: %s)",
                 ", ".join(out["hl_keys"]) or "없음")
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
                 "has_hl": False, "hl_keys": [], "partial": False, "limit": limit,
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
            notes.append("정렬 키를 못 배워 원천 기본 정렬로 보여 줍니다")
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


def why_banner() -> str:
    """이 진단을 만든 **코드의 지문**(#364·#365).

    ⚠️ 왜 필요한가: 배포가 VM 에 닿기 전에 돌린 출력과 닿은 뒤의 출력이
    **글자만 봐선 구별되지 않는다** — 2026-09-13 에 그것 때문에 고친 것이
    안 먹은 줄 알고 한 라운드를 썼다(#364). 여기선 특히 그렇다: 고가·저가
    칸을 켜고 끄는 판정이 코드에 있으므로, 옛 코드가 찍은 '두 칸 비었음'을
    새 판정으로 읽으면 엉뚱한 데를 고친다.

    ⚠️ 지문은 **이 모듈의 전이 폐포**다 — `audit_fingerprint` 를 그대로
    쓴다(#38 형제 배너와 같은 함수). 한 파일만 해싱하면 판정의 절반이
    사는 `naver_diag`·`naver_sector_client` 를 안 덮는 과대 주장이 된다
    (#364d).
    """
    try:
        from bot.audit_sweep import audit_fingerprint
        sig = audit_fingerprint(("bot.kr_volume_client",))
    except Exception as exc:                                   # noqa: BLE001
        # 못 구하면 **모른다고 말한다** — 조용히 비우면 낡은 체크아웃이
        # 신선한 것과 구별되지 않는다(#54·#43).
        sig = f"지문불가({type(exc).__name__})"
    return (f"# kr_volume --why · 코드 지문 {sig} · "
            "화면이 쓰는 그 경로(fetch_kr_volume_top)를 태웁니다(#35)")


@contextlib.contextmanager
def _keep_learn_cooldown():
    """진단이 **새 냉각을 심지 않게** 학습 실패 도장을 되돌린다.

    ⚠️ `--why` 는 화면 경로를 태우므로(#35) 정렬 키를 못 배운 순간에 돌리면
    `_LEARN_FAIL` 도장이 찍혀 **다음 10분의 실수집을 막는다** — 진단이 자기가
    읽을 신호를 오염시키는 그 자리다(#264·#283·#30). 파일 내용과 **mtime
    둘 다** 되돌린다(TTL 이 mtime 이라 내용만 되돌리면 냉각이 그대로 산다).
    """
    from bot.finviz_client import _CACHE_DIR
    p = _CACHE_DIR / _LEARN_FAIL
    try:
        before = (p.read_bytes(), p.stat().st_mtime) if p.exists() else None
    except OSError:
        before = None
    try:
        yield
    finally:
        try:
            if before is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(before[0])
                os.utime(p, (before[1], before[1]))
        except OSError:
            pass


def hl_verdict(rows: list, hl_keys: list) -> str:
    """고가·저가 갈래(순수) — '원천이 안 준다' 와 '우리가 못 읽는다' 를 가른다.

    #372 그대로: 진단이 ❌ 를 찍으면 원천을 의심하기 전에 **내 파서가 못 읽은
    것인지**부터 갈라야 한다. 대조할 행이 없으면 ✅ 도 ❌ 도 아니다(#54).
    """
    if not rows:
        return "❓ 판정 불가 — 행이 없어 원천 스키마를 못 봤습니다"
    unread = [k for k in hl_keys if k.endswith("(미읽음)")]
    if any(r.get("high") is not None for r in rows):
        return f"✅ 원천이 줍니다 — 두 칸을 그립니다(키: {', '.join(hl_keys)})"
    if unread:
        # ⚠️ ❌ 로 찍지 않는다 — `high52week`(52주 고가)처럼 **오늘 고가가
        # 아닌** 이름이 걸리면 영원히 안 꺼지는 경보가 된다(#25·#260·#34).
        return ("⚠️ 원천 행에 high/low 이름이 있는데 우리가 안 읽습니다: "
                + ", ".join(unread)
                + " — 그중 **오늘** 고가·저가인 것이 있으면 _HIGH_KEYS/"
                  "_LOW_KEYS 에 더할 것(52주 류는 아니다, #34)")
    return "✅ 원천이 안 줍니다(high/low 이름 0개) — 두 칸을 뺐습니다"


def why() -> int:
    """`--why` — 거래량 보드가 지금 무엇을 들고 있나(0=행 있음, 1=없음).

    갈래를 이름으로 말한다(#82): 정렬 키를 배웠나 / 원천이 행을 주나 /
    고가·저가를 주나 / 업종이 붙나. 반복 확인은 명령을 건네지 말고 제품에
    심는다(§Automation-first·#252).
    """
    from bot.finviz_client import _cached, cache_age_sec
    print(why_banner())

    print("① 정렬 키")
    c = _cached(_SORT_CACHE, ttl=_SORT_TTL)
    if isinstance(c, dict) and c.get("sort"):
        age = cache_age_sec(_SORT_CACHE)
        print(f"   ✅ 배운 키 {c['sort']} · 허용값 {len(c.get('allowed') or [])}종"
              + (f" · {int(age) // 3600}시간 전 학습" if age is not None else ""))
    else:
        print("   ❓ 아직 못 배웠습니다(아래 수집이 학습을 시도합니다)")
    f = _cached(_LEARN_FAIL, ttl=_LEARN_COOL)
    if isinstance(f, dict) and f.get("why"):
        print(f"   ⏸ 학습 냉각 중 — {f['why']}")

    print("② 수집(화면 경로)")
    with _keep_learn_cooldown():
        d = fetch_kr_volume_top(limit=50)
    rows = d.get("rows") or []
    print(f"   행 {len(rows)}개 · 원시 {d.get('scanned', 0)}개 · "
          f"제외(ETF·ETN·스팩) {d.get('excluded', 0)}개 · "
          f"부분 {'예' if d.get('partial') else '아니오'} · "
          f"저장분 {'예' if d.get('stale') else '아니오'} · "
          f"정렬 {d.get('sort') or '(없음)'} · 기준 {d.get('ts') or '(없음)'}")
    if d.get("reason"):
        print(f"   사유: {d['reason']}")

    print("③ 고가·저가")
    print("   " + hl_verdict(rows, list(d.get("hl_keys") or [])))

    print("④ 업종(신고저·급등락 보드와 같은 맵)")
    try:
        from bot.naver_sector_client import (apply_kr_industry,
                                             kr_industry_fail_reason)
        probe = [dict(r) for r in rows]          # 원본을 손대지 않는다(#326)
        apply_kr_industry(probe)
        got = sum(1 for r in probe if r.get("ind"))
        if not rows:
            print("   ❓ 판정 불가 — 행이 없습니다")
        elif got:
            print(f"   ✅ {got}/{len(rows)}종목에 업종이 붙습니다")
        else:
            print(f"   ❌ 0/{len(rows)} — {kr_industry_fail_reason() or '사유 미기록'}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   ❌ 업종 맵 조회 실패 — {type(exc).__name__}: {exc}")

    return 0 if rows else 1


if __name__ == "__main__":                       # pragma: no cover
    # ⚠️ 엔트리포인트는 **맨 끝**이다 — 파일 중간에 두면 그 아래 정의가
    # 영영 안 닿는다(#276).
    import argparse
    import sys

    _ap = argparse.ArgumentParser(prog="python -m bot.kr_volume_client")
    _ap.add_argument("--why", action="store_true",
                     help="거래량 보드 상태를 갈래로 진단")
    _a = _ap.parse_args()
    if not _a.why:
        _ap.print_help()
        sys.exit(2)
    sys.exit(why())
