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
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("bot.kr_bulk_rank")

_KST = ZoneInfo("Asia/Seoul")
_EOK = 1e8          # 원 → 억 (`naver_ranking_client._kr_row` 와 같은 규약, #38)
SOURCE_LABEL = "KRX 벌크 종가(pykrx·KIS 마스터)"

_NAMES_CACHE = "kr_bulk_rank_names_v1.json"
_NAMES_TTL = 12 * 3600   # 상장 종목명·시장 접미사는 하루 한 번 바뀌는 값이다


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


def _kis_names(*, write: bool = True) -> tuple[dict, str]:
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

    ⚠️ **이름표는 따로 캐시한다.** `_kis_master_rows` 자체엔 캐시가 없어(공용
    헬퍼라 이 커밋에서 안 건드린다) 부를 때마다 zip 2개를 받는다 — 각 30초
    상한이다(⚠️ "콜드 비용의 **대부분**이 여기다" 는 계측이 아니라 상한에서
    나온 추정이다. 이 경로엔 타이밍 계측이 없다 — 재지 않은 것을 단정하지
    않는다, #165·#69). 그런데 상장 종목명·시장 접미사는
    하루에 한 번 바뀌는 값이라 10분마다 다시 받을 이유가 없다(#61 이 비용이
    어느 단계를 줄이나). 캐시해 두면 이후 갱신은 pykrx 호출만 남는다.
    ⚠️ **반쪽 맵은 굽지 않는다** — 코스닥 zip 만 실패한 맵을 완전본으로 구우면
    코스닥 전 종목이 12시간 동안 접미사 없는 맨 코드가 된다(#280·#384 부분을
    완전본으로 굽지 말 것).
    ⚠️ `write=False`(진단)는 **읽기만** 한다 — 진단이 운영 캐시를 채우면
    자기가 읽을 신호를 오염시킨다(#30·#264·#283).
    """
    from bot.finviz_client import _cached
    cached = _cached(_NAMES_CACHE, ttl=_NAMES_TTL)
    if isinstance(cached, dict) and isinstance(cached.get("map"), dict) \
            and cached["map"]:
        # ⚠️ JSON 왕복은 튜플을 **리스트**로 바꾼다 — 호출부가 `nm, suf` 로
        # 푸는 지금은 둘 다 되지만, 모양을 원래대로 돌려 두는 것이 계약이다
        # (#22 디스크 캐시를 왕복하는 값은 읽는 지점에서 복원할 것).
        return ({str(k): (str(v[0]), str(v[1])) for k, v in cached["map"].items()
                 if isinstance(v, (list, tuple)) and len(v) == 2},
                str(cached.get("note") or ""))
    out: dict = {}
    notes: list = []
    try:
        from bot.bollinger_board import _kis_master_rows
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"KIS 마스터 실패({type(exc).__name__})"
    books = (("kospi", ".KS"), ("kosdaq", ".KQ"))
    filled = 0
    for book, suf in books:
        try:
            rows, note = _kis_master_rows(book)
        except Exception as exc:                               # noqa: BLE001
            notes.append(f"{book} 실패({type(exc).__name__})")
            continue
        if not rows:
            notes.append(f"{book} 0행({note})")
            continue
        filled += 1
        for r in rows:
            code = str(r.get("code") or "").zfill(6)
            if len(code) == 6:
                out[code] = (str(r.get("name") or ""), suf)
    if not out:
        return {}, "KIS 마스터가 0종목(" + " · ".join(notes) + ")"
    note = " · ".join(notes)
    if write and filled == len(books):
        from bot.finviz_client import _cache_write
        _cache_write(_NAMES_CACHE, {
            "map": {k: [v[0], v[1]] for k, v in out.items()}, "note": note})
    return out, note


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


# ── 캐시 · 시도 기록 ─────────────────────────────────────────────────────
#
# 2026-09-19 사용자가 붙인 일일 감사: `❌ 급등·급락 0행 … KRX 벌크 수집이 12초를
# 넘겨 이번에는 비웠습니다 — 백그라운드가 받는 중이라 **다음 갱신에 나옵니다**`.
# 그 마지막 문장이 참인지 아무도 재지 않았고(#380·#165), **참이어도 수렴하지
# 않는 구조**였다:
#   (a) TTL(10분)이 지나면 `_cached` 가 None 을 줘 **이미 받아 둔 종가를 버리고**
#       또 비웠다 — 종가는 사실상 안 바뀌는 값인데도(#163 SWR 은 되살린 값에
#       기준시각을 같이 싣는 것이지 버리는 게 아니다).
#   (b) 일일 감사는 **매번 콜드 프로세스**라 정의상 예산을 넘긴다 = 영원히 ❌.
#       못 고칠 ❌ 가 매일 오면 진짜 ❌ 를 가린다(#260).
#   (c) 비는 갈래가 넷인데(자격증명 없음 / pykrx 없음 / 원천 빈 응답 / 아직 안
#       끝남) 화면은 한 문장이었다 — 처방이 전부 다르다(#82).
# 그래서 ① 만료분을 **나이와 함께** 내고 갱신은 뒤에서 받고(SWR) ② 시도마다
# **관측 기록**을 남겨 화면·감사가 약속 대신 사실을 말하게 한다(#86 상태는 아는
# 쪽에 물어라).
_CACHE = "kr_bulk_rank_v1.json"
_STATE = "kr_bulk_rank_state_v1.json"
_BUDGET = 12    # 초 — 렌더 경로 상한(#116). 콜드 미스의 실제 비용(KIS zip 2개 +
#   pykrx 3콜)보다 짧다: 넘기면 이번 응답을 비우고 백그라운드가 캐시를 채운다.
_TTL = 600      # 10분 — 이 값이 **종가**라 장중에도 거의 안 바뀐다. 짧게 잡으면
#   폴링(2분)마다 KRX 벌크 3~4콜이 나간다(#116 예산과 캐시는 한 세트 · #36).
#   ⚠️ **빈 결과는 굽지 않는다** — 원천 장애 한 번이 10분 빈 화면이 된다
#   (#161·#280·#303). 만료분은 버리지 않고 `_stale_ok` 범위 안이면 낸다.
_WARM_MAX = 300  # 초 — '예열 중' 이라고 말해 줄 수 있는 상한. 넘으면 그건 예열이
#   아니라 **멈춘 것**이므로 ⚠️ 로 숨기지 않는다(#41 여유로 사실을 덮지 말 것).

_KICK = threading.Lock()   # 논블로킹 — 갱신 스레드를 하나만 띄운다


def _ago(sec) -> str:
    """나이를 사람 말로 — 라벨만 말고 **숫자로**(#202)."""
    s = _num(sec)
    if s is None or s < 0:
        return "얼마 전"
    s = int(s)
    if s < 60:
        return f"{s}초"
    if s < 3600:
        return f"{s // 60}분"
    if s < 86400:
        return f"{s // 3600}시간"
    return f"{s // 86400}일"


def attempt_record() -> dict:
    """마지막 수집 시도의 **관측 기록**(없거나 못 읽으면 {}).

    ⚠️ 이 파일은 `_cache_write`(truncate 후 쓰기)로 쓰이므로 다른 프로세스가
    **쓰다 만 것**을 읽을 수 있다(#379). 그때 `_cached` 가 JSON 오류를 삼켜
    None 을 주고 우리는 '기록 없음' 으로 떨어진다 — 값이 틀어지지는 않고 그
    호출만 판정 불가가 된다. 공용 헬퍼라 호출부가 100곳이어서 이 커밋에서
    원자 교체로 바꾸지 않았다(못 보는 축, #274).
    """
    try:
        from bot.finviz_client import _cached
        rec = _cached(_STATE, ttl=float("inf"))
    except Exception:                                          # noqa: BLE001
        return {}
    return rec if isinstance(rec, dict) else {}


def _record(obj: dict) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_STATE, obj)
    except Exception:                                          # noqa: BLE001
        pass


def attempt_note(rec, now: float, *, board_empty: bool = True) -> str:
    """마지막 시도의 **관측 사실** 한 조각(순수). 기록이 없으면 ''.

    갈래마다 처방이 다르므로 이름을 달리 부른다(#82):
      - 받는 중  → 기다리면 된다
      - 실패     → **그 사유를 고쳐야 한다**(행동 가능)
      - 성공했는데 화면이 비었다 → 캐시 쓰기를 봐야 한다(전혀 다른 자리)

    ⚠️ `board_empty=False`(행이 실려 있다)면 **성공 기록은 적지 않는다** —
    옛 판은 이 문장을 두 문맥이 그대로 나눠 써서, 행이 멀쩡히 실린 화면에
    "그런데 화면이 비었다면 캐시 쓰기를 봐야 합니다" 라는 **다른 상황용
    진단**과 시도 행 수(2,500행)가 서빙된 복사본 옆에 붙었다(독립 리뷰
    2026-09-19 M2 · #45 두 모집단 · #25 안 맞는 문구는 소음이다).
    """
    if not isinstance(rec, dict):
        return ""
    st = _num(rec.get("started"))
    if st is None:
        return ""
    fin = _num(rec.get("finished"))
    if fin is None:
        return f"{_ago(now - st)}째 받는 중입니다"
    took = _num(rec.get("secs"))
    tail = f"({_ago(took)} 걸림)" if took else ""
    if rec.get("ok"):
        if not board_empty:
            return ""            # 행이 실려 있다 — 성공 기록은 더할 말이 없다
        n = int(_num(rec.get("rows")) or 0)
        return (f"지난 시도는 {_ago(now - fin)} 전 성공했습니다{tail} — "
                f"{n:,}행. 그런데 화면이 비었다면 캐시 쓰기를 봐야 합니다")
    why = str(rec.get("reason") or "사유 미기록")
    return f"지난 시도가 {_ago(now - fin)} 전 실패했습니다{tail}: {why}"


# 연속 실패 간격 — 원천이 오래 죽었을 때 **하루 몇 번인가**를 세고 정한다
# (#384 그대로). 렌더는 장중 30초·2분 주기로 도므로 백오프가 없으면 죽은
# KRX/KIS 를 그 속도로 계속 두드린다(독립 리뷰 2026-09-19 M4).
_BACKOFF = (60.0, 300.0, 900.0, 1800.0, 3600.0)


def retry_wait(rec, now: float) -> float:
    """다음 갱신까지 **남은 초**(순수). 지금 쳐도 되면 0.

    성공했거나 기록이 없으면 0 — 백오프는 **연속 실패**에만 건다(#82 갈래는
    처방이 다르다). 지수적으로 늘리되 상한 1시간(#384 의 그 규약).
    """
    if not isinstance(rec, dict):
        return 0.0
    fin = _num(rec.get("finished"))
    if fin is None or rec.get("ok"):
        return 0.0
    n = max(1, int(_num(rec.get("fails")) or 1))
    wait = _BACKOFF[min(n, len(_BACKOFF)) - 1]
    return max(0.0, (fin + wait) - now)


def warming_note(rec, now: float) -> str:
    """**지금 예열 중**이라 비어 있는 것인가 — 그렇다면 그 사실을, 아니면 ''.

    ⚠️ 예열은 **유예이지 면죄가 아니다**. 시작만 하고 `_WARM_MAX` 를 넘도록
    안 끝났으면 그건 예열이 아니라 멈춘 것이므로 '' 를 돌려 감사가 ❌ 를 내게
    한다 — 안 그러면 stuck 상태가 영원히 ⚠️ 로 숨는다(#41·#260·#25).
    """
    if not isinstance(rec, dict):
        return ""
    st = _num(rec.get("started"))
    if st is None or _num(rec.get("finished")) is not None:
        return ""
    age = now - st
    if age < 0 or age > _WARM_MAX:
        return ""
    return f"{_ago(age)}째 받는 중입니다"


def warming_reason(rec, now: float, budget: float) -> str:
    """콜드 미스로 이번 응답을 비울 때 화면이 적을 사유(순수).

    ⚠️ 옛 문구는 "백그라운드가 받는 중이라 **다음 갱신에 나옵니다**" 였다 —
    재지 않은 약속이다(#380). 우리가 아는 사실은 둘뿐이다: *이번 요청을
    백그라운드로 넘겼다*(우리 코드에 대한 주장, #375)와 *마지막 시도가 어떻게
    끝났나*(기록). 그 둘만 적는다.
    """
    head = (f"KRX 벌크가 {budget:g}초 예산 안에 안 끝나 이번 응답은 비웠습니다"
            "(수집은 백그라운드로 넘겼습니다)")
    note = attempt_note(rec, now)
    if not note:
        return head + " — 아직 한 번도 끝난 적이 없습니다"
    return f"{head} — {note}"


_REFRESH_WORDS = {
    "live": "갱신은 백그라운드가 받습니다",
    "idle": "갱신을 지금 띄우지 못했습니다(다음 조회에서 다시 시도합니다)",
    "stuck": "갱신이 시작만 하고 안 끝나고 있습니다",
    "backoff": "연속 실패라 갱신을 잠시 쉬고 있습니다",
}


def stale_note(asof: str, age_sec, rec=None, now: float | None = None,
               *, refreshing: str = "live", memo: str = "") -> str:
    """만료된 복사본을 낼 때 화면이 적을 사실(순수).

    낡음을 숨기지 않는다 — **기준일**과 **언제 받았는지**를 같이 적는다
    (#43·#163 되살린 값엔 기준시각을 반드시 · #202 라벨 말고 숫자로).
    갱신 시도가 실패했으면 그게 더 행동 가능하므로 같이 싣는다(#275).

    ⚠️ 받은 시각은 **절대 시각**이다. 옛 판은 `11분 전에 받았습니다` 였는데,
    이 문자열이 그대로 페이지 세션 캐시(`kr_movers_v1.json`)에 구워져 몇 시간
    뒤에도 `11분 전` 이라고 적었다(독립 리뷰 2026-09-19 M1 실측). 상대 시각은
    **다시 재지 않으면 반드시 거짓이 된다** — 절대 시각은 안 늙는다(#18·#43).

    ⚠️ `memo`(수집기가 남긴 경고 — 이름표 미확보 등)를 **같이 싣는다**. 신선
    경로는 이 memo 를 그대로 내보내는데 만료 경로만 버리고 있었다: KIS 마스터가
    0종목이라 티커가 맨 6자리가 된 배치가 `_TTL` 뒤에도 **경고 없이** 최대
    `max_back` 일 동안 다시 서빙됐다(독립 리뷰 H3 · #45 · #55).

    ⚠️ `refreshing` 은 **재서** 받는다(문자열 4갈래) — `_kick` 이 스레드를 못
    띄웠는데 "갱신은 백그라운드가 받습니다" 라고 적으면 재지 않은 것을 단정하는
    것이고(#165·#375), 늘 켜지는 '갱신 중' 배지는 아무것도 안 재는 것과 같다
    (#25·#260·#343). 2026-09-19 전제 변경(#222): 옛 판은 bool 이라 '멈춤' 과
    '쉬는 중' 을 '못 띄웠다' 와 같은 문장으로 적었다.
    """
    _now = time.time() if now is None else now
    head = f"{asof} 종가입니다 — {_recv_label(_now, age_sec)}에 받았습니다"
    head += " · " + _REFRESH_WORDS.get(str(refreshing), _REFRESH_WORDS["idle"])
    parts = [head]
    if str(memo or "").strip():
        parts.append(str(memo).strip())
    note = attempt_note(rec, _now, board_empty=False)
    if note and "받는 중" not in note:
        parts.append(note)
    return " · ".join(parts)


def _recv_label(now: float, age_sec) -> str:
    """받은 **절대 시각**(KST). 못 재면 '얼마 전인지 모름'(#54·#165)."""
    a = _num(age_sec)
    if a is None or a < 0:
        return "알 수 없는 시각"
    try:
        return datetime.fromtimestamp(now - a, _KST).strftime("%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "알 수 없는 시각"


def _stale_ok(asof: str, max_back: int, now=None) -> bool:
    """기준일이 **신선 경로가 받아들이는 범위** 안인가(순수).

    상한을 임의로 정하지 않는다 — `_kr_bulk_rows_uncached` 가 오늘부터
    `max_back` 일을 거슬러 찾으므로, 그 범위의 종가는 신선 경로가 '지금 낼
    값' 으로 인정하는 바로 그것이다(#269 문턱은 추측하지 말고 주기에서 도출).
    ⚠️ 기준일을 못 읽으면 **안 낸다** — 나이를 못 적는 값을 화면에 실으면
    사용자가 그걸 '현재' 로 읽는다(#43·#163).
    """
    try:
        d = datetime.strptime(str(asof), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    today = (now or datetime.now(_KST)).date()
    return 0 <= (today - d).days <= max(0, max_back)


def _kr_bulk_rows_uncached(max_back: int, *,
                           write: bool = True) -> tuple[list, str, str]:
    """(행, 기준일 `YYYY-MM-DD`, 사유/메모).

    행이 있으면 세 번째 값은 **메모**(빈 문자열이 정상), 없으면 **사유**다 —
    갈래를 이름으로 말한다(#82). 휴장·주말은 거슬러 올라가며 찾고, 몇 날을
    훑었는지 사유에 적는다(#43 침묵이 최악)."""
    stock, why = _pykrx()
    if stock is None:
        return [], "", why
    names, nwhy = _kis_names(write=write)
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


def _run_attempt(max_back: int, *, write: bool) -> tuple[list, str, str]:
    """한 번 받아 오고 **기록을 남긴다**.

    ⚠️ `write=False`(진단·프로브)는 캐시도 기록도 안 건드린다 — 진단이 자기가
    읽을 신호를 오염시키면 안 된다(#30·#264·#283).
    """
    t0 = time.time()
    if write:
        _record({"started": t0, "finished": None, "ok": None,
                 "secs": None, "rows": 0, "reason": ""})
    try:
        rows, asof, memo = _kr_bulk_rows_uncached(max_back, write=write)
    except BaseException as exc:                               # noqa: BLE001
        if write:
            _record({"started": t0, "finished": time.time(), "ok": False,
                     "secs": round(time.time() - t0, 1), "rows": 0,
                     "fails": _fails_after(False),
                     "reason": f"예외({type(exc).__name__}: {str(exc)[:120]})"})
        raise
    if write:
        if rows:
            _cache_put({"rows": rows, "asof": asof, "memo": memo})
        # 행이 0 이면 `ok=False` + **그 사유**를 남긴다 — 다음 요청이 약속 대신
        # 이 사실을 적는다(#82·#86).
        _record({"started": t0, "finished": time.time(), "ok": bool(rows),
                 "secs": round(time.time() - t0, 1), "rows": len(rows),
                 "fails": _fails_after(bool(rows)),
                 "reason": "" if rows else memo})
    return rows, asof, memo


def _fails_after(ok: bool) -> int:
    """직전 기록을 이어 **연속 실패 횟수**를 센다(성공이면 0).

    백오프(`retry_wait`)가 이 수를 쓴다 — 실패가 이어질수록 간격을 늘려야
    원천이 오래 죽은 동안 바깥 요청이 폭주하지 않는다(#384·#116).
    """
    if ok:
        return 0
    prev = attempt_record()
    n = int(_num(prev.get("fails")) or 0) if isinstance(prev, dict) else 0
    return n + 1


def _cache_put(obj: dict) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_CACHE, obj)
    except Exception:                                          # noqa: BLE001
        pass


def _guarded(max_back: int, *, write: bool) -> tuple[list, str, str]:
    """동시 요청은 하나만 돈다(#113 캐시는 끝난 뒤에만 도와준다).

    ⚠️ 키에 `write` 를 넣는다 — 진단(`write=False`)이 렌더의 리더가 되면 그
    호출의 캐시·기록 쓰기가 통째로 사라진다(#346 `force` 로 키를 가른 선례).
    ⚠️ `except` 는 **ImportError 만** 잡는다(옛 판은 `Exception`). 넓게 잡으면
    리더가 되던 예외를 삼키고 `_run_attempt` 를 **한 번 더** 돌려 바깥 원천을
    두 번 친다 — 그건 재시도 정책이지 import 폴백이 아니다(#315 넓은 try 는
    덜 중요한 것까지 삼킨다). 호출부 셋은 모두 `except Exception` 으로 감싸고
    사유를 화면에 적으므로 전파돼도 조용히 죽지 않는다.
    """
    # ⚠️ `try` 는 **import 한 줄만** 감싼다. 옛 판은 호출까지 감싸서,
    # `fn` 안의 지연 import(pykrx·pandas·`bot.finviz_client`)가 ImportError 를
    # 내면 `_run_attempt` 를 **한 번 더** 돌려 바깥 원천을 두 번 쳤다 — 바로
    # 이 독스트링이 막는다고 적은 그 일이다(독립 리뷰 2026-09-19 M3 실측
    # `fetch attempts inside one _guarded() call: 2`).
    try:
        from bot.singleflight import once
    except ImportError:
        return _run_attempt(max_back, write=write)
    return once(f"kr_bulk_rank:{max_back}:{int(write)}",
                lambda: _run_attempt(max_back, write=write))


def _kick(max_back: int, rec=None, now: float | None = None) -> str:
    """갱신을 **기다리지 않고** 띄운다. 반환은 **지금 상태**(4갈래).

    `live`(진행 중) / `stuck`(시작만 하고 `_WARM_MAX` 초과) / `backoff`(연속
    실패라 쉬는 중) / `idle`(못 띄웠다). 화면이 그 값으로 문장을 고르므로
    뜻을 정확히 맞춘다(#25·#343 — 늘 켜지는 배지는 아무것도 안 재는 것과 같고,
    꺼져 있어야 할 때 켜지면 거짓말이다).

    2026-09-19 전제 변경(#222): 옛 판은 bool 이었고 락이 잡혀 있으면 **상한
    없이** True 였다 — 스레드가 멈추면(pykrx 호출에 명시 타임아웃이 없다)
    화면이 "갱신은 백그라운드가 받습니다" 를 영원히 주장하는 동안 복사본이
    늙어 보드가 빈다(독립 리뷰 2026-09-19 L4). `warming_note` 는 바로 그
    이유로 `_WARM_MAX` 로 묶여 있었는데 여기만 안 묶여 있었다(#38).

    ⚠️ 매 렌더마다 스레드를 띄우면 폴링(2분)마다 쌓인다 — 논블로킹 락으로
    '하나만' 을 보장한다. `start()` 가 던지면 **반드시 풀어 준다**: 안 풀면
    그 보드의 갱신이 영구 정지한다(#280·#371 실측한 그 함정).
    """
    _now = time.time() if now is None else now
    _rec = attempt_record() if rec is None else rec
    if not _KICK.acquire(blocking=False):
        # 진행 중이다 — 단 **얼마나 됐는지** 를 묶어서 본다(#41 유예는 면죄가
        # 아니다). ⚠️ '멈췄다' 는 기록이 **실제로 그렇게 말할 때만** 이다:
        # 시작 도장이 있고 끝나지 않았는데 예열 창을 넘겼을 때. 기록이 없으면
        # 모르는 것이므로 관측한 사실(락이 잡혀 있다 = 진행 중)만 말한다
        # (#54·#165 재지 않은 것을 단정하지 말 것).
        _st = _num(_rec.get("started")) if isinstance(_rec, dict) else None
        _fin = _num(_rec.get("finished")) if isinstance(_rec, dict) else None
        if _st is not None and _fin is None and (_now - _st) > _WARM_MAX:
            return "stuck"
        return "live"
    if retry_wait(_rec, _now) > 0:
        # 연속 실패 — 지금 또 쳐도 같은 답이다. 락은 되돌려 놓는다.
        _KICK.release()
        return "backoff"

    def _bg() -> None:
        try:
            _guarded(max_back, write=True)
        except Exception as exc:                               # noqa: BLE001
            log.info("kr_bulk_rank: 백그라운드 갱신 실패 — %s", exc)
        finally:
            _KICK.release()

    try:
        threading.Thread(target=_bg, name="kr_bulk_rank_kick",
                         daemon=True).start()
    except Exception as exc:                                   # noqa: BLE001
        _KICK.release()
        log.warning("kr_bulk_rank: 갱신 스레드 기동 실패 — %s", exc)
        return "idle"
    return "live"


def kr_bulk_rows(max_back: int = 7, *,
                 use_cache: bool = True) -> tuple[list, str, str]:
    """캐시를 두른 진입점 — 보드는 이걸 부른다.

    ⚠️ 캐시는 **가장 아래 계층**에 둔다(#348) — 호출부마다 두면 급등·급락과
    거래량 상위가 같은 벌크를 각자 받는다.

    단 셋(#82 — 화면이 어느 단에서 왔는지 말한다):
      ① 신선(TTL 안) → 그대로.
      ② 만료분이 `_stale_ok` 범위 안 → **그대로 내고** 갱신은 뒤에서(SWR).
         여기서 비우면 10분마다 보드가 깜빡인다.
      ③ 복사본이 아예 없음 → 예산 안에서 받아 보고, 넘기면 비우되 **마지막
         시도의 관측 사실**을 적는다(약속 금지, #380).
    """
    from bot.finviz_client import _cached, cache_age_sec
    if not use_cache:
        return _guarded(max_back, write=False)

    fresh = _cached(_CACHE, ttl=_TTL)
    if isinstance(fresh, dict) and fresh.get("rows"):
        return (list(fresh["rows"]), str(fresh.get("asof") or ""),
                str(fresh.get("memo") or ""))

    old = _cached(_CACHE, ttl=float("inf"))
    if isinstance(old, dict) and old.get("rows"):
        asof = str(old.get("asof") or "")
        if _stale_ok(asof, max_back):
            # ⚠️ 기록은 **kick 전에** 읽는다 — `_kick` 이 띄운 스레드의 첫 일이
            # `started` 로 덮어쓰는 것이라, 뒤에 읽으면 직전 실패 사유가 "받는
            # 중" 에 가려진다(독립 리뷰 2026-09-19 H2 실측 20회 중 5회). 콜드
            # 경로는 이미 그렇게 하고 있었는데 여기만 순서가 반대였다(#38).
            rec = attempt_record()
            state = _kick(max_back, rec)
            return (list(old["rows"]), asof,
                    stale_note(asof, cache_age_sec(_CACHE), rec,
                               refreshing=state,
                               memo=str(old.get("memo") or "")))

    # ⚠️ 여기부터가 **콜드**다. 렌더 안에서 도므로 KIS 마스터 zip 2개(각 30초
    # 상한) + pykrx 3콜이 동기로 붙으면 대시보드가 통째로 멈춘다(#116).
    # 예산을 넘기면 이번 응답만 비우고 작업은 **취소하지 않는다** — 그래야
    # 다음 요청(과 일일 감사의 다음 실행)이 복사본을 받는다.
    # ⚠️ 기록은 **제출 전에** 읽는다 — `_run_attempt` 가 곧바로 `started` 로
    # 덮어쓰므로, 뒤에 읽으면 직전 시도의 실패 사유가 "받는 중" 에 가려진다
    # (#275 더 행동 가능한 쪽을 남길 것).
    prev = attempt_record()
    import concurrent.futures as _cf
    ex = _cf.ThreadPoolExecutor(max_workers=1,
                                thread_name_prefix="kr_bulk_rank")
    fut = ex.submit(_guarded, max_back, write=True)
    ex.shutdown(wait=False)                  # 제출 직후 — 예외 경로 누수 방지(#128)
    try:
        return fut.result(timeout=_BUDGET)
    except _cf.TimeoutError:
        log.info("kr_bulk_rank: 예산 %ss 초과 — 백그라운드로 넘겼습니다", _BUDGET)
        return [], "", warming_reason(prev, time.time(), _BUDGET)
