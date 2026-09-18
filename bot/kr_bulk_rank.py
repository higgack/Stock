"""KR 전종목 벌크 랭킹 — 네이버 front-api 가 막힌 날의 폴백(KRX/KIS).

사용자 2026-09-18 "급등/급락이랑 거래량 상위… 제대로 쭉 잘 되다가 왜 그러는거야".
증상: 두 보드가 빈 화면 + `원천이 HTTP 400 — 원천: sortType: Invalid input:
expected "dividend" · dividendSortType: Invalid option: expected one of
"rate"|"value"`.

측정된 것 / 안 된 것을 갈라 적는다(#12·#165):
  ✅ `front-api/domestic/stock/list` 가 **우리 요청 모양을 거절한다** — 같은
     본문이 `sortType=up`(하드코딩 · `fetch_kr_movers`)과 `__probe__`(미끼 ·
     `kr_volume_client.learn_sort_type`) **양쪽에 동일하게** 왔다. 값마다 다른
     사유가 오지 않는다는 것은 거절 대상이 그 값이 아니라는 뜻이다.
  ✅ 같은 호스트·같은 base 의 `domestic/theme/list` 는 정상이다(테마별 시세
     화면이 뜬다) — 즉 네이버 전체 장애가 아니라 **이 경로**의 문제다.
  ❌ 새 요청 모양이 무엇인지는 **재지 못했다** — 샌드박스에서 네이버가
     프록시 403 이다. `bot.scripts.kr_board_probe --shape` 가 VM 에서 잰다.

⚠️ 형제 보드는 멀쩡해 보였다. 52주 신고저는 **같은 죽은 엔드포인트**를 쓰는데
(`fetch_kr_highlow` → `domestic/stock/list?sortType=high52week`) 화면이 살아
있다 — `intl_highlow._compute_kr_full` 에 **pykrx 폴백**이 있어서다. 급등·급락과
거래량 상위만 폴백이 없어 빈 화면이 됐고, 그 비대칭 자체가 원인을 가리키는
단서였다(#51 화면 칸끼리 대조하면 외부 자료 없이 판정된다). 그래서 형제가 이미
증명한 사다리를 그대로 깐다(§작업 원칙 선행 사례 먼저 · #136 폴백 조건은
'실패했나' 가 아니라 '요구를 충족했나' · §작업 원칙 "외부 원천 폴백은 삭제
대상이 아니라 fix 였다").

시장특정 예외 사유(§UNIVERSAL): KRX/KIS 가 KR 전 종목 시세를 한 호출로 주는
공식 원천이고, 다른 시장엔 이 레포에 검증된 동급 원천이 없다(`bollinger_board.
_SESSION_FILL` 이 같은 사유로 KR 만 채운다).

⚠️ **종가다.** pykrx 벌크는 그날 종가·누적거래량이라 장중에는 '현재가' 가
아니다. 값을 고치지 말고 **화면이 그렇게 말하게** 한다(#43·#136 payload 가
밝힌 원천을 화면이 따라야 한다 · #34 라벨에 기준을 박을 것).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("bot.kr_bulk_rank")

_KST = ZoneInfo("Asia/Seoul")
_EOK = 1e8          # 원 → 억 (`naver_ranking_client._kr_row` 와 같은 규약, #38)
SOURCE_LABEL = "KRX 벌크 종가(pykrx·KIS 마스터)"


# ── 순수 ────────────────────────────────────────────────────────────────

def _num(v):
    """숫자만 — NaN·inf·문자열은 None(#235·#242 `or 0` 은 '없음'을 0 으로 바꾼다)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _pick(*vals):
    """첫 **비-None** 값 — `a or b` 는 `0.0`(정당한 등락률 0%)을 버린다(#235·#242)."""
    for v in vals:
        if v is not None:
            return v
    return None


def _col(df, *needles: str):
    """컬럼 이름을 **부분일치**로 집는다 — pykrx 판마다 표기가 흔들리고, 리터럴을
    박으면 그 판에서 컬럼이 통째로 사라진다(#19·#337 writer 가 만드는 실제
    이름으로 재라). 못 찾으면 None 이고 호출부가 그 칸을 비운다(#43)."""
    # ⚠️ `getattr(df, "columns", []) or []` 로 쓰면 **pandas Index 의 진리값**을
    # 묻게 되어 `ValueError: The truth value of a Index is ambiguous` 로 죽는다
    # (실제 프레임으로 픽스처를 만들자마자 터졌다 — 손으로 만든 dict 였으면
    # 영영 안 보였다, #155). 진리값을 묻지 말고 None 만 가른다.
    cols = getattr(df, "columns", None)
    cols = [] if cols is None else list(cols)
    for n in needles:
        for c in cols:
            if n in str(c):
                return c
    return None


def is_real_stock(name: str) -> bool:
    """스팩 제외 — 네이버 경로의 `_is_real_stock` 과 같은 정책(#38).

    ⚠️ 네이버는 `stockEndType=='stock'` 이라는 **원천 필드**로 가르는데 KRX
    벌크엔 그 필드가 없다. 그래서 여기서 거르는 축은 이름 하나뿐이고, ETF·ETN 은
    KRX 가 애초에 다른 시장으로 주므로 `get_market_ohlcv_by_ticker` 에 안 섞인다
    — 섞여 들어오면 그건 **재지 않은 가정이 틀린 것**이므로 화면에 보인다(#50)."""
    return "스팩" not in str(name or "")


def sort_rows(rows: list, key: str, *, desc: bool = True, limit: int = 30) -> list:
    """정렬 — 키가 없는 행은 **뺀다**(None 을 0 으로 읽으면 하락 TOP 이 결측으로
    채워진다, #235). 동률은 거래대금으로 가른다(재현 가능한 순서)."""
    ok = [r for r in rows if _num(r.get(key)) is not None]
    ok.sort(key=lambda r: (_num(r.get(key)), _num(r.get("value")) or 0.0),
            reverse=desc)
    return ok[:limit]


# ── 원천 ────────────────────────────────────────────────────────────────

def today_is_final(now) -> bool:
    """오늘 KRX 종가가 **확정**됐나(순수 — `now` 주입 가능).

    ⚠️ 장중에 오늘 행을 받아 '종가'·'해당 거래일 확정치' 라고 적으면 화면이
    거짓말한다(#34 라벨에 기준을 박을 것 · #55). 정규장이 끝난 15:30 부터가
    확정이고, `after_close` 는 자정을 넘으므로(20:00~08:00) **시각도 같이**
    본다 — 새벽 7시의 '오늘' 은 아직 거래가 없다."""
    try:
        from bot.kr_session import phase
        key = phase("KRX", now)[0]
    except Exception:                                          # noqa: BLE001
        return False
    if key in ("regular", "pre", "pre_close"):
        return False
    return now.hour >= 15


def _pykrx():
    """(stock 모듈, 사유) — 함수 존재가 아니라 **실호출 가능 여부**로 판정(#151·#25)."""
    try:
        from bot.pykrx_client import krx_login_ready
        if not krx_login_ready():
            return None, "KRX 자격증명 없음(.env 의 KRX_ID/KRX_PW)"
        from pykrx import stock
    except Exception as exc:                                   # noqa: BLE001
        return None, f"pykrx 없음({type(exc).__name__})"
    return stock, ""


def _frame(stock, name: str, *args, **kw):
    """(df 또는 None, 메모). ⚠️ 메모는 **실패 사유일 수도 성공 시 단서일 수도**
    있다 — `market` 인자를 못 받는 옛 pykrx 로 물러난 경우가 그렇다. 그때는
    코스닥이 통째로 빠지므로 조용히 넘어가면 유니버스가 반쪽이 된다(#190 실측:
    벌크 914종목 · 서희건설 035890.KQ 가 '벌크에 없음'). 성공/실패는 첫 값이
    None 인지로 가른다."""
    fn = getattr(stock, name, None)
    if not callable(fn):
        return None, f"{name} 없음(설치본)"
    try:
        df = fn(*args, **kw)
    except TypeError as exc:
        if "market" not in kw:
            return None, f"{name} 실패(TypeError: {str(exc)[:60]})"
        try:
            df = fn(*args, **{k: v for k, v in kw.items() if k != "market"})
        except Exception as exc2:                              # noqa: BLE001
            return None, f"{name} 실패({type(exc2).__name__})"
        note = f"{name}: market 인자 미지원 — 코스닥 누락 가능"
        return (df, note) if df is not None and len(df) else (None, note)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"{name} 실패({type(exc).__name__})"
    if df is None or len(df) == 0:
        return None, f"{name} 응답 비어 있음"
    return df, ""


def _kis_names() -> tuple[dict, str]:
    """{6자리코드: (종목명, 접미사)} — KIS 마스터(무키·무로그인).

    ⚠️ pykrx 시세 프레임은 **종목명을 안 준다**(index=코드). 이름 없이 코드만
    2,800행 그리면 화면이 못 읽히므로, 이 레포가 이미 실측 파싱해 쓰는 KIS
    마스터를 재사용한다(`bollinger_board._rung_kis` · #38 복제 금지 · #150 이미
    부르는 호출이 무엇을 더 주는지 먼저 볼 것). 접미사(.KS/.KQ)도 여기서 온다 —
    pykrx 로 시장을 가르려면 호출이 하나 더 든다.

    ⚠️ `_rung_kis()` 를 쓰면 안 된다 — 그건 **지수 구성종목**(KOSPI200+
    KOSDAQ150 ≈ 350종목)만 돌려준다(독립 리뷰 2026-09-18 실측). 전 종목 랭킹에
    그걸 쓰면 나머지 ~2,350행이 접미사 없는 맨 6자리가 되어 `/lookup/123456` 이
    **미국 심볼로 해석**되고 링크가 통째로 깨진다. 마스터 **전 행**을 쓴다.
    """
    out: dict = {}
    notes: list = []
    try:
        from bot.bollinger_board import _kis_master_rows
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"KIS 마스터 실패({type(exc).__name__})"
    for book, suf in (("kospi", ".KS"), ("kosdaq", ".KQ")):
        try:
            rows, note = _kis_master_rows(book)
        except Exception as exc:                               # noqa: BLE001
            notes.append(f"{book} 실패({type(exc).__name__})")
            continue
        if not rows:
            notes.append(f"{book} 0행({note})")
            continue
        for r in rows:
            code = str(r.get("code") or "").zfill(6)
            if len(code) == 6:
                out[code] = (str(r.get("name") or ""), suf)
    if not out:
        return {}, "KIS 마스터가 0종목(" + " · ".join(notes) + ")"
    return out, " · ".join(notes)


def _rows_on(stock, ds: str, names: dict) -> tuple[list, str]:
    """`ds`(YYYYMMDD) 하루치 전종목 행. (행, 메모)."""
    notes: list = []

    def _note(m: str) -> None:
        if m:
            notes.append(m)

    # 종목명·등락률·거래량·거래대금을 한 호출로 주는 프레임(있으면 1순위)
    chg, m = _frame(stock, "get_market_price_change_by_ticker", ds, ds,
                    market="ALL")
    _note(m)
    ohlcv, m = _frame(stock, "get_market_ohlcv_by_ticker", ds, market="ALL")
    _note(m)
    if chg is None and ohlcv is None:
        return [], " · ".join(notes) or "가격 프레임을 하나도 못 받았습니다"
    cap, m = _frame(stock, "get_market_cap_by_ticker", ds, market="ALL")
    _note(m)

    c_name = _col(chg, "종목명") if chg is not None else None
    c_close = _col(chg, "종가") if chg is not None else None
    c_pct = _col(chg, "등락률") if chg is not None else None
    c_vol = _col(chg, "거래량") if chg is not None else None
    c_val = _col(chg, "거래대금") if chg is not None else None
    o_close = _col(ohlcv, "종가") if ohlcv is not None else None
    o_pct = _col(ohlcv, "등락률") if ohlcv is not None else None
    o_vol = _col(ohlcv, "거래량") if ohlcv is not None else None
    o_val = _col(ohlcv, "거래대금") if ohlcv is not None else None
    o_high = _col(ohlcv, "고가") if ohlcv is not None else None
    o_low = _col(ohlcv, "저가") if ohlcv is not None else None
    p_cap = _col(cap, "시가총액") if cap is not None else None

    def _cell(df, idx, col):
        if df is None or col is None:
            return None
        try:
            return _num(df.loc[idx, col])
        except Exception:                                      # noqa: BLE001
            return None

    codes: list = []
    seen: set = set()
    for df in (chg, ohlcv):
        if df is None:
            continue
        for i in df.index:
            c = str(i).zfill(6)
            if c not in seen:
                seen.add(c)
                codes.append((c, i))

    rows: list = []
    unnamed = 0
    for code, idx in codes:
        nm, suf = names.get(code, ("", ""))
        if not nm:
            nm = str(_cell(chg, idx, c_name) or "") if c_name else ""
            if not nm and c_name is not None and chg is not None:
                try:
                    nm = str(chg.loc[idx, c_name])
                except Exception:                              # noqa: BLE001
                    nm = ""
        if not nm:
            unnamed += 1
            nm = code
        if not is_real_stock(nm):
            continue
        price = _pick(_cell(chg, idx, c_close), _cell(ohlcv, idx, o_close))
        # ⚠️ 장전에는 KRX 가 그날 행을 **0 값 placeholder** 로 준다(`_fetch_kr_bulk`
        # 실측) — 0 을 가격으로 실으면 등락률 랭킹이 통째로 거짓이 된다(#326·#280).
        if not price or price <= 0:
            continue
        val = _pick(_cell(chg, idx, c_val), _cell(ohlcv, idx, o_val))
        mcap = _cell(cap, idx, p_cap)
        rows.append({
            "ticker": f"{code}{suf}",
            "name": nm,
            "price": price,
            # ⚠️ 컬럼 **유무**로 가르면 안 된다 — 한 프레임에만 있는 종목은
            # 그 칸이 None 이 되고, `sort_rows` 가 pct 없는 행을 빼므로 급등·
            # 급락 두 목록에서 통째로 사라진다(독립 리뷰). 다른 칸과 같은
            # **값 폴백**을 쓴다(#45 한 행 안에서 규약이 갈리면 안 된다).
            "pct": _pick(_cell(chg, idx, c_pct), _cell(ohlcv, idx, o_pct)),
            "vol": _pick(_cell(chg, idx, c_vol), _cell(ohlcv, idx, o_vol)),
            "value": round(val / _EOK, 2) if val else None,
            "mcap": round(mcap / _EOK, 2) if mcap else None,
            "high": _cell(ohlcv, idx, o_high),
            "low": _cell(ohlcv, idx, o_low),
            "ind": None,
        })
    if unnamed and rows:
        # 이름을 못 붙인 수를 **세어 말한다** — 침묵하면 코드만 뜬 화면을 보고
        # 수집 실패로 읽는다(#43·#54 대조 0건은 통과가 아니다).
        _note(f"종목명 미확보 {unnamed}종목(코드로 표기)")
    if not rows:
        _note("전 행이 가격 0 이거나 걸러졌습니다")
    return rows, " · ".join(notes)


_CACHE = "kr_bulk_rank_v1.json"
_BUDGET = 12    # 초 — 렌더 경로 상한(#116). 콜드 미스의 실제 비용(KIS zip 2개 +
#   pykrx 3콜)보다 짧다: 넘기면 이번 응답을 비우고 백그라운드가 캐시를 채운다.
_TTL = 600      # 10분 — 보드 폴링(2분)보다 길어도 되는 이유는 이 값이 **종가**
#   라 장중에도 거의 안 바뀌기 때문이다. 짧게 잡으면 폴링마다 KRX 벌크 3~4콜이
#   나간다(#116 예산과 캐시는 한 세트 · #36 TTL 과 주기의 관계는 값의 성질이
#   정한다). ⚠️ **빈 결과는 굽지 않는다** — 원천 장애 한 번이 10분 빈 화면이
#   된다(#161·#280·#303).


def _kr_bulk_rows_uncached(max_back: int) -> tuple[list, str, str]:
    """(행, 기준일 `YYYY-MM-DD`, 사유/메모).

    행이 있으면 세 번째 값은 **메모**(빈 문자열이 정상), 없으면 **사유**다 —
    갈래를 이름으로 말한다(#82). 휴장·주말은 거슬러 올라가며 찾고, 몇 날을
    훑었는지 사유에 적는다(#43 침묵이 최악)."""
    stock, why = _pykrx()
    if stock is None:
        return [], "", why
    names, nwhy = _kis_names()
    tried: list = []
    today = datetime.now(_KST)
    for back in range(max(0, max_back) + 1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:                 # 토·일은 KRX 가 안 연다
            continue
        if back == 0 and not today_is_final(today):
            continue                          # 장중 값을 '종가' 라 부르지 않는다
        ds = d.strftime("%Y%m%d")
        rows, note = _rows_on(stock, ds, names)
        if rows:
            memo = " · ".join(n for n in (nwhy, note) if n)
            return rows, d.strftime("%Y-%m-%d"), memo
        tried.append(f"{ds}: {note or '행 없음'}")
    return [], "", (f"KRX 벌크가 {len(tried)}거래일에서 행을 못 냈습니다 — "
                    + " / ".join(tried[:3]))


def kr_bulk_rows(max_back: int = 7, *, use_cache: bool = True) -> tuple[list, str, str]:
    """캐시를 두른 진입점 — 보드는 이걸 부른다.

    ⚠️ 캐시는 **가장 아래 계층**에 둔다(#348) — 호출부마다 두면 급등·급락과
    거래량 상위가 같은 벌크를 각자 받는다. 동시 요청은 single-flight 로 하나만
    돈다(#113 캐시는 끝난 뒤에만 도와준다)."""
    from bot.finviz_client import _cache_write, _cached
    if use_cache:
        c = _cached(_CACHE, ttl=_TTL)
        if isinstance(c, dict) and c.get("rows"):
            return list(c["rows"]), str(c.get("asof") or ""), str(c.get("memo") or "")

    def _run():
        rows, asof, memo = _kr_bulk_rows_uncached(max_back)
        if rows and use_cache:
            # ⚠️ 캐시 쓰기는 **작업 안**에 둔다 — 예산을 넘겨 호출부가 먼저
            # 돌아가도 백그라운드가 끝나면 다음 요청이 곧바로 받는다(#116).
            _cache_write(_CACHE, {"rows": rows, "asof": asof, "memo": memo})
        return rows, asof, memo

    def _guarded():
        try:
            from bot.singleflight import once
            return once(f"kr_bulk_rank:{max_back}", _run)
        except Exception:                                      # noqa: BLE001
            return _run()

    if not use_cache:
        return _guarded()
    # ⚠️ 이 경로는 **렌더 안**에서 돈다 — 콜드 미스면 KIS 마스터 zip 2개
    # (각 30초 상한) + pykrx 3콜이 동기로 붙어 대시보드가 통째로 멈춘다
    # (#116 본문이 아닌 값을 본 응답 경로에서 부를 땐 예산과 캐시를 같이).
    # 예산을 넘으면 이번 응답은 비우고 **백그라운드가 캐시를 예열**한다.
    import concurrent.futures as _cf
    ex = _cf.ThreadPoolExecutor(max_workers=1,
                                thread_name_prefix="kr_bulk_rank")
    fut = ex.submit(_guarded)
    ex.shutdown(wait=False)                  # 제출 직후 — 예외 경로 누수 방지(#128)
    try:
        return fut.result(timeout=_BUDGET)
    except _cf.TimeoutError:
        log.info("kr_bulk_rank: 예산 %ss 초과 — 백그라운드가 예열합니다", _BUDGET)
        return [], "", (f"KRX 벌크 수집이 {_BUDGET}초를 넘겨 이번에는 비웠습니다 "
                        "— 백그라운드가 받는 중이라 다음 갱신에 나옵니다")
