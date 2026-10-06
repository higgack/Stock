"""Market favorites (관심종목) — CRUD for market.html watchlist.

Stores saved tickers with snapshot data (price at save time, estimates)
in a simple JSON file. No LLM, no recurring cost — yfinance only.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import threading as _threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.market_favorites")

_FAVORITES_FILE = Path.home() / ".tradingagents" / "market_favorites.json"


def _future_or_none(d_str):
    """다음 실적일이 오늘 이전(stale)이면 None — 빈칸 표시용."""
    try:
        if d_str and str(d_str)[:10] >= datetime.now().strftime("%Y-%m-%d"):
            return str(d_str)[:10]
    except Exception:
        pass
    return None


def _tkey(t) -> str:
    """티커 동일성 키 — 대소문자·앞뒤 공백 무시.

    목록을 고치는 모든 경로(추가·삭제·순서·별표)와 캐시 덧입히기(`overlay_derived`)
    가 **같은** 판정을 쓴다(#38) — 한 경로만 다르게 비교하면 화면엔 보이는데 지울 수
    없는 행이 생긴다(사용자 2026-10-06 "또 갑자기 버튼이 안먹혀", 실수 #436).
    """
    return str(t or "").strip().upper()


class FavoritesUnreadable(Exception):
    """목록 파일이 **있는데 못 읽었다**(깨진 JSON·열기 오류·목록이 아님).

    '비었다' 와 처방이 정반대다(#82) — 비었으면 담으면 되고, 못 읽으면 파일을
    고쳐야 한다. 옛 `_load` 는 둘 다 `[]` 로 접었다(독립 리뷰 2026-10-06 L5, 실수
    #436): 화면은 '저장한 종목이 없다' 를 그렸고(#43), **담기**는 빈 목록 위에 써서
    남은 목록을 통째로 덮었으며, 삭제·별표·순서는 바꿀 행을 못 찾아 **조용한
    no-op** 이었다 — 사용자가 본 '✕ 무반응' 과 같은 증상이다(그 경로였는지는 재지
    않았다 — 델타 리뷰 L2). `reason` 은 화면에 그대로 싣는 사람 문장이다.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def error_text(exc: BaseException) -> str:
    """관심종목 경로의 오류를 사람 문장으로 — 대시보드 API 다섯(조회·담기·삭제·
    순서·별표)과 DART 공시 알림이 **같이 쓴다**(#38).

    사유를 꺼내는 규칙이 자리마다 갈려 있었다(독립 델타 리뷰 2026-10-06 M3·L3):
    조회는 `reason` 을, 쓰기는 `str(exc)` 를 실었고, `_save` 의 OSError 는 tmp
    파일의 **서버 경로**까지 화면 alert 로 나갔다. 못 읽은 목록은 사람 문장
    (`reason`) 그대로, OSError 는 `종류: 사유` 만, 그 밖은 `종류: 내용`. 판정은
    이름이 아니라 `isinstance` — `.reason` 덕타이핑은 UnicodeDecodeError·URLError
    의 `.reason` 까지 집는다.
    """
    if isinstance(exc, FavoritesUnreadable):
        return exc.reason
    if isinstance(exc, OSError) and exc.strerror:
        return f"{type(exc).__name__}: {exc.strerror}"         # 경로는 싣지 않는다
    return f"{type(exc).__name__}: {exc}"[:300]


_TMP_SEQ = itertools.count(1)


# 사유 끝에 붙는 처방 — 갈래마다 다르다(#82). 내용이 깨진 갈래는 파일을 고쳐야
# 하지만, 열지 못한 갈래(권한·경로)는 데이터가 멀쩡할 수 있어 '되돌리라' 고 하면
# 최근 변경을 잃는 처방이 된다(델타 리뷰 L6 · #319 이행 불가능한 처방 금지). 이
# 파일엔 자동 백업이 없다 — CLI 정리 백업(`market_favorites.backup-*.json`)뿐이다.
_FIX_CONTENT = (" — 덮어쓰지 않도록 쓰기를 멈췄습니다. market_favorites.json 을"
                " 고치거나 백업이 있으면 되돌리세요.")
_FIX_ACCESS = (" — 데이터는 그대로일 수 있습니다. 쓰기를 멈췄으니 파일 권한·경로를"
               " 먼저 확인하세요.")


def _load(*, strict: bool = False) -> list[dict]:
    """디스크 목록. 파일이 없으면 `[]`(아직 안 담았다 — 정상).

    `strict=True` — 파일이 있는데 못 읽으면 `FavoritesUnreadable`. **쓰는 경로와
    화면 조회, 그리고 목록으로 상태를 고치는 소비자**(DART 공시 알림이 관심종목
    코드를 상태에 적는다)가 쓴다: 못 읽은 목록을 빈 목록으로 알고 쓰면 파일·상태를
    덮고, 빈 목록으로 그리면 화면이 거짓말한다.
    `strict=False`(기본) — 종전처럼 `[]` 지만 **조용히 비우지 않는다**(#12): 경고를
    남긴다. 빈 목록이면 아무것도 쓰지 않는 자리(진단 프로브·name_kr 백필·CLI 의
    되읽기 확인)용이다 — 처음엔 '알림' 도 여기 넣었는데 DART 알림은 빈 목록으로
    상태를 고쳐 그 사이 공시를 삼켰다(델타 리뷰 M1).
    """
    try:
        # BOM 이 붙은 파일(편집기 저장)도 받는다 — 우리 writer 는 BOM 을 안 쓴다.
        raw = _FAVORITES_FILE.read_text("utf-8-sig")
    except FileNotFoundError:
        return []
    except UnicodeDecodeError as exc:
        # UTF-8 이 아닌 바이트는 **내용**이 깨진 것이다 — 아래 열기 갈래로 보내면
        # '권한·경로를 먼저' 라는 틀린 처방이 붙는다(배포전 셀프리뷰, #82).
        why = (f"관심종목 파일 내용을 읽지 못했습니다({type(exc).__name__}: "
               f"{str(exc)[:120]})" + _FIX_CONTENT)
    except Exception as exc:                                   # noqa: BLE001
        # OSError 의 str 은 서버 경로를 싣는다 — 화면에 갈 문장엔 사유만(인증 뒤라도
        # 경로는 운영 로그로 충분하다). 그리고 `exists()` 를 먼저 묻지 않는다 —
        # 디렉터리 권한(EACCES)이면 `exists()` 가 원시 PermissionError 를 던져
        # 이 판정을 건너뛰었다(델타 리뷰 L3b).
        detail = (exc.strerror if isinstance(exc, OSError) and exc.strerror
                  else str(exc))
        why = (f"관심종목 파일을 열지 못했습니다({type(exc).__name__}: "
               f"{str(detail)[:120]})" + _FIX_ACCESS)
    else:
        try:
            data = json.loads(raw)
        except Exception as exc:                               # noqa: BLE001
            why = (f"관심종목 파일 내용을 읽지 못했습니다({type(exc).__name__}: "
                   f"{str(exc)[:120]})" + _FIX_CONTENT)
        else:
            if not isinstance(data, list):
                why = (f"관심종목 파일이 목록이 아닙니다({type(data).__name__})"
                       + _FIX_CONTENT)
            else:
                bad = next((i for i, f in enumerate(data)
                            if not isinstance(f, dict)), None)
                if bad is None:
                    return data
                why = (f"관심종목 목록에 종목이 아닌 항목이 섞여 있습니다"
                       f"({bad + 1}번째: {type(data[bad]).__name__})" + _FIX_CONTENT)
    if strict:
        raise FavoritesUnreadable(why)
    log.warning("favorites: %s — 빈 목록으로 읽는다(%s)", why, _FAVORITES_FILE)
    return []


def _save(favorites: list[dict], *, invalidate_cache: bool = True) -> None:
    """디스크에 목록을 쓴다. `invalidate_cache=False` 는 **표시 속성 전용**.

    ⚠️ 기본값(True)은 추가/삭제/순서변경용이다 — 캐시를 비워 다음 조회가 가격
    갱신을 다시 건다. 화면의 **구성·순서**가 맞는 것은 이제 이 무효화가 아니라
    읽는 시점의 덧입히기(`overlay_derived`) 덕이다: 옛 판은 이 무효화에 기댔는데
    (사용자 2026-06-16 '휴지통 작동 안 함'), 진행 중이던 갱신이 끝나며 옛 목록을
    캐시에 다시 올려 무력화됐다(2026-10-06 재발, 실수 #436). 그래서 무효화는
    정합성엔 더는 필요 없고, 쓰기마다 전 종목 갱신을 다시 거는 비용만 남았다 —
    그 비용을 줄이는 것은 별도 과제로 남겼다(독립 리뷰 L7).

    ⚠️ 반대로 **별표(중요표시)처럼 목록·순서를 안 바꾸는 속성**에서 이걸
    태우면 별 한 번 누를 때마다 전 종목 가격이 통째로 `—` 가 됐다가 데몬이
    다시 채운다(139종목 실측 수십 초) — 사용자는 그걸 고장으로 읽는다.
    그런 속성은 `invalidate_cache=False` 로 쓰고, **읽는 시점에 디스크에서
    덧입힌다**(`_apply_stars`) — 어느 캐시 층이 행을 주든 정본과 같아진다
    (#18·#21b 캐시가 fix 를 가리는 실패를 규율이 아니라 구조로 막는다).
    """
    _FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ tmp 이름이 상수면 **두 프로세스**(봇의 5분 갱신 · 대시보드의 클릭)가 같은
    # 파일을 쓴다 — 한쪽의 `replace` 가 ENOENT 로 죽거나 남의 내용을 올린다(독립
    # 리뷰 2026-10-06 (b)). 프로세스·쓰기마다 갈라 준다(`naver_research_client.
    # detail_cache_flush` 와 같은 규약 — 스레드 id 는 재사용돼 못 쓴다). 실패하면
    # 남은 tmp 를 지운다.
    tmp = _FAVORITES_FILE.with_suffix(f".{os.getpid()}.{next(_TMP_SEQ)}.tmp")
    try:
        tmp.write_text(json.dumps(favorites, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(_FAVORITES_FILE)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if invalidate_cache:
        global _FAV_CACHE          # _FAV_CACHE 는 아래에서 정의(런타임 global)
        _FAV_CACHE = None


def _detect_country(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")):
        return "KR"
    if t.endswith(".T"):
        return "JP"
    if t.endswith((".TW", ".TWO", ".TT")):
        return "TW"     # .TT = 블룸버그 대만 표기(야후엔 없다) — 아래 _YF_ALIAS
    if t.endswith((".SS", ".SZ")):
        return "CN"
    if t.endswith(".HK"):
        return "HK"
    if t.endswith((".L", ".IL")):
        return "UK"
    if t.endswith((".DE", ".F")):
        return "DE"
    if t.endswith(".PA"):
        return "FR"
    return "US"


# 야후가 쓰지 않는 표기로 저장된 티커 — **목록은 사용자 것이므로 원문을 고치지
# 않고**, 조회할 때만 야후 표기 후보로 바꾼다. 실측 2026-09-08: `2467.TT`
# (블룸버그 대만 표기)가 `_detect_country` 에서 조용히 'US' 로 떨어져 네이버도
# 야후도 못 찾고 **가격 미수신 1건**이 됐다 — 알 수 없는 접미를 US 로 추측한
# 것이 원인이다(#46 위치·형태로 추정하지 말 것 · #82 갈래를 이름으로).
# 후보가 둘인 이유: 대만은 上市(.TW)와 上櫃(.TWO)가 갈리고 어느 쪽인지는
# **재야** 안다(finviz_client 의 `.TWO` 폴백과 같은 규율, §작업 원칙 선행 사례).
_YF_ALIAS: dict[str, tuple[str, ...]] = {".TT": (".TW", ".TWO")}
_YF_RESOLVED: dict[str, str] = {}


def yf_candidates(ticker: str) -> list[str]:
    """야후에 물어볼 심볼 후보 — 순서가 곧 우선순위. 순수 함수.

    야후가 아는 표기면 원문 하나뿐이다(대부분의 종목에서 no-op).
    """
    t = str(ticker or "").strip()
    for suf, alts in _YF_ALIAS.items():
        if t.upper().endswith(suf):
            base = t[: -len(suf)]
            return [base + a for a in alts]
    return [t] if t else []


# 부정 해석의 **만료** — '오늘 안 온다'가 영원이 되면 안 된다.
# ⚠️ 빈 프레임은 '없다'와 '못 물었다'를 **구별해 주지 않는다**(독립 리뷰
# 2026-09-08 실측): yfinance 는 `hide_exceptions` 기본값 때문에 read timeout·
# 5xx 에도 예외 없이 `empty_df()` 를 돌려주고, 로그 문구도 스스로
# "possibly delisted" 라고 적는다. 그래서 예외 유무로 갈라 **영구** 기억하면
# 야후의 30초 블립 하나가 그 티커를 프로세스 수명 내내 굳힌다(화면은 값이
# 빈 채로, 로그 한 줄도 없이 — #43·#52 조용한 것과 죽은 것). 갈래를 못 재면
# 단정하지 말고(#165) **시간으로 만료**시킨다. 상장 직후 편입도 이걸로 회복된다.
_YF_NEG_TTL_SEC = 3600
_YF_EMPTY: dict[str, float] = {}


def _resolve_yf(ticker: str) -> str:
    """후보 중 **실제로 데이터가 오는** 심볼. 못 찾으면 원문 그대로.

    ⚠️ 이름이나 규칙이 아니라 **실측**으로 고른다(#25). 성공은 프로세스 수명
    동안 기억하고(후보가 하나면 네트워크 0), **빈 답은 `_YF_NEG_TTL_SEC` 만큼만**
    기억한다 — 빈 답이 '없다'인지 '못 물었다'인지 원천이 안 갈라 주기 때문이다
    (위 주석). 그동안은 원문을 쓰되 그 사실을 로그로 남긴다(#43 침묵 금지).
    """
    cands = yf_candidates(ticker)
    if len(cands) <= 1:
        return cands[0] if cands else ticker
    if ticker in _YF_RESOLVED:
        return _YF_RESOLVED[ticker]
    seen = _YF_EMPTY.get(ticker)
    if seen is not None and (time.time() - seen) < _YF_NEG_TTL_SEC:
        log.info("favorites: %s — 야후 표기 후보가 %d분 전 전부 비어 원문을 쓴다"
                 "(%d분 뒤 다시 묻는다)", ticker, int((time.time() - seen) // 60),
                 max(0, int((_YF_NEG_TTL_SEC - (time.time() - seen)) // 60)))
        return ticker
    import yfinance as yf
    asked_all = True     # 후보를 **전부 물어봤나** — 예외가 있으면 못 물은 것
    for c in cands:
        try:
            h = yf.Ticker(c).history(period="5d")
            if h is not None and len(h):
                _YF_RESOLVED[ticker] = c
                _YF_EMPTY.pop(ticker, None)
                # 폴백을 로그로만 알리면 사용자는 영영 모른다(#42a) — 화면도
                # `yf_ticker` 로 같이 밝힌다(#136).
                log.info("favorites: %s → 야후 표기 %s 로 조회", ticker, c)
                return c
        except Exception as exc:
            asked_all = False
            log.debug("favorites: %s 후보 %s 실패: %s", ticker, c, exc)
    if asked_all:
        # 전부 빈 답 — **만료되는** 부정 기억. 갱신 주기(3분)마다 후보 수만큼
        # 나가던 순손실 호출을 줄이되, 영구히 굳히지는 않는다.
        _YF_EMPTY[ticker] = time.time()
        log.warning("favorites: %s — 야후 표기 후보 %s 가 전부 비었다(원문 유지 · "
                    "%d분 뒤 재시도)", ticker, cands, _YF_NEG_TTL_SEC // 60)
    else:
        # 예외는 '없다'가 아니라 **못 물었다** 다 — 기억조차 하지 않는다(#143).
        log.warning("favorites: %s — 야후 표기 후보 %s 를 못 물었다(재시도 대상)",
                    ticker, cands)
    return ticker


_CURRENCY_MAP = {
    "KRW": "₩", "JPY": "¥", "TWD": "NT$", "CNY": "¥",
    "HKD": "HK$", "GBP": "£", "EUR": "€", "USD": "$",
}


def _naver_quote_for(ticker: str) -> Optional[dict]:
    """네이버 실시간 시세 {price, pct, mcap, name} — KR=네이버 국내·US/JP/CN/HK=
    네이버 해외 (사용자 2026-06-15 '관심종목 시총·현재가 네이버, 대만 제외').
    한 콜로 현재가·시총·한글명을 모두 받는다. TW(.TW)·기타(EU 등)·실패는 None
    → 호출부가 yfinance 폴백. (네이버는 KR 국내·US/JP/CN/HK 해외만 커버)."""
    country = _detect_country(ticker)
    try:
        if country == "KR":
            from bot.naver_quote import fetch_kr_quote
            return fetch_kr_quote(ticker)
        if country in ("US", "JP", "CN", "HK"):
            from bot.world_quote import fetch_world_quote
            return fetch_world_quote(ticker)
    except Exception as exc:
        log.debug("favorites: naver quote failed for %s: %s", ticker, exc)
    return None


def _resolve_kr_name(ticker: str, fallback: str) -> str:
    """네이버 한글 종목명 (정적, add_favorite 1회 영속). KR/US/JP/CN/HK=네이버.
    TW(.TW/.TWO)=네이버 미커버 → 대만 신고가 페이지와 **동일한** chart_translate
    번역(사용자 2026-06-15 '대만도 대만신고가처럼'). names_kr.json 캐시가 티커
    기준이라 신고가가 이미 채운 한글명을 그대로 공유(₩0·동일 표기). 그 외(EU
    등)·실패는 영문 fallback."""
    q = _naver_quote_for(ticker)
    if q and q.get("name"):
        return q["name"]
    if _detect_country(ticker) == "TW":
        try:
            from bot.chart_translate import translate_names_kr
            kr = translate_names_kr([(ticker, fallback)])
            if kr.get(ticker):
                return kr[ticker]
        except Exception as exc:
            log.debug("favorites: TW name translate failed for %s: %s", ticker, exc)
    return fallback


def _per_from_shown(price, eps) -> Optional[float]:
    """예상 PER = **화면의 현재가 ÷ 화면의 예상 EPS**.

    ⚠️ 2026-08-20 사용자 검증 요청에서 드러난 것: 이 표는 `현재가`·`예상 EPS`
    ·`예상 PER` 을 나란히 놓는데 셋이 **서로 다른 출처**였다.
      · 현재가  = 네이버 실시간(KR) / yfinance
      · 예상 EPS = forwardEps, 없으면 trailingEps, 없으면 calendar 컨센서스
      · 예상 PER = yfinance forwardPE/trailingPE (**자기 EPS·자기 가격** 기준)
    미국 종목은 우연히 셋이 맞아떨어졌지만(SKHY 157.01÷32.14=4.9 ✓) 국내는
    어긋났다 — 삼성전자 247,500÷14,227=17.4 인데 화면은 3.7, 쿠콘 10.7 인데
    8.9. 사용자가 눈으로 나눗셈을 하면 안 맞는다 = 표가 거짓말이다.
    EPS 가 없으면 PER 도 비운다(소스 PER 만 남겨 두면 검산이 불가능하고,
    'PER 은 있는데 EPS 는 —' 라는 설명 불가능한 행이 생긴다).
    """
    if price is None or eps is None:
        return None
    try:
        price, eps = float(price), float(eps)
    except (TypeError, ValueError):
        return None
    if eps == 0 or price <= 0:
        return None
    return price / eps


def add_favorite(ticker: str) -> Optional[dict]:
    """Fetch snapshot from yfinance and **prepend** to favorites. None on dupe/error.

    ⚠️ prepend 다 — 새로 저장한 종목이 목록 **맨 위**에 온다(사용자 2026-09-07).
    문구를 'append' 로 두면 다음 사람이 순서를 반대로 읽는다(#55).
    """
    import yfinance as yf

    favorites = _load(strict=True)
    if any(_tkey(f.get("ticker")) == _tkey(ticker) for f in favorites):
        return None

    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as exc:
        log.warning("favorites: yfinance failed for %s: %s", ticker, exc)
        return None

    eps_est = info.get("forwardEps")
    per_is_trailing = info.get("forwardEps") is None
    lfy = info.get("lastFiscalYearEnd")
    fy_label = None
    if lfy and isinstance(lfy, (int, float)):
        fy_label = f"FY{datetime.fromtimestamp(lfy).year % 100:02d}"
    next_earn = None
    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            earn_dates = cal.get("Earnings Date") or []
            if earn_dates:
                d = earn_dates[0]
                next_earn = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            if cal.get("Earnings Average") is not None:
                eps_est = cal["Earnings Average"]
    except Exception:
        pass

    price = info.get("regularMarketPrice") or info.get("currentPrice")
    currency = info.get("currency", "USD")
    now = datetime.now()

    _en_name = info.get("longName") or info.get("shortName") or ticker
    entry = {
        "ticker": ticker,
        "name": _en_name,
        "name_kr": _resolve_kr_name(ticker, _en_name),
        "country": _detect_country(ticker),
        "saved_date": now.strftime("%Y-%m-%d"),
        "saved_time": now.strftime("%H:%M"),
        "saved_price": price,
        "currency": currency,
        "currency_symbol": _CURRENCY_MAP.get(currency, "$"),
        "market_cap": info.get("marketCap"),
        "eps_estimate": eps_est,
        "eps_is_actual": (info.get("forwardEps") is None
                          and info.get("trailingEps") is not None),
        "eps_fy_label": fy_label if (info.get("forwardEps") is None
                                     and info.get("trailingEps") is not None) else None,
        "eps_negative": (eps_est is not None and eps_est < 0),
        # PER 은 위 두 칸(현재가·예상 EPS)에서 **직접** 만든다 — 아래 helper 참조.
        "per": _per_from_shown(price, eps_est),
        "per_is_trailing": per_is_trailing,
        # 과거 날짜(yfinance KR calendar stale)는 빈칸 — 사용자 2026-06-11.
        "next_earnings": _future_or_none(next_earn),
    }

    # ⚠️ **맨 앞**에 넣는다 — 새로 저장한 종목이 목록 맨 위에 온다(사용자
    # 2026-09-07 "최신에 저장한게 가장 위쪽으로 가게해줘. 현재는 반대로 되어
    # 있어"). 옛 `append` 는 새 종목을 154번째 줄에 놓아 페이지를 끝까지
    # 넘겨야 보였다. 화면은 저장 순서를 그대로 그리므로(정렬 헤더·↕ 버튼은
    # 그 위에서 동작) 이 한 줄이 곧 표시 순서다 — 기존 항목의 **수동 순서는
    # 건드리지 않는다**(↕ 로 직접 맞춘 배열을 날짜 정렬로 덮으면 사용자가
    # 요청해 만든 기능이 무의미해진다, #222 계약을 바꿀 땐 범위를 먼저).
    # ⚠️ 위 `_load()` 는 **네트워크 전** 스냅샷이라 이미 낡았다 — 락 안에서
    # 다시 읽고 중복도 다시 본다(그 사이 다른 탭이 담았을 수 있다).
    with _DISK_LOCK:
        favorites = _load(strict=True)
        if any(_tkey(f.get("ticker")) == _tkey(ticker) for f in favorites):
            return None
        favorites.insert(0, entry)
        _save(favorites)
    return entry


def remove_favorite(ticker: str) -> bool:
    """Remove ticker from favorites. Returns True if removed.

    load→save 는 `_DISK_LOCK` 안에서 — 락은 전원이 참여할 때만 상호배제다.
    """
    key = _tkey(ticker)
    if not key:            # 빈 키로 비교하면 티커 없는 행을 통째로 지운다
        return False
    with _DISK_LOCK:
        favorites = _load(strict=True)
        before = len(favorites)
        favorites = [f for f in favorites if _tkey(f.get("ticker")) != key]
        if len(favorites) < before:
            _save(favorites)
            return True
    return False


def get_favorites(*, strict: bool = False) -> list[dict]:
    """Return all saved favorites. `strict=True` 면 못 읽은 목록을 빈 목록으로
    접지 않고 `FavoritesUnreadable` 을 던진다(`_load` 독스트링)."""
    return _load(strict=strict)


def sort_by_saved(favorites: list[dict]) -> list[dict]:
    """저장일 **내림차순**(최신이 위)으로 정렬한 새 리스트. 순수 함수.

    ⚠️ 같은 날짜·시각은 **원래 순서를 유지**한다(stable) — 안 그러면 정리
    한 번에 사용자가 ↕ 로 맞춰 둔 배열이 무작위로 섞인다.
    ⚠️ `saved_date` 가 없는 항목은 **맨 아래**로. 모르는 값을 '최신' 으로
    올리면 담은 적 없는 종목이 맨 위에 온다 — 빈칸이 틀린 라벨보다 낫다(#29).
    """
    return sorted(favorites,
                  key=lambda f: (str(f.get("saved_date") or ""),
                                 str(f.get("saved_time") or "")),
                  reverse=True)


# ── 별표(중요표시) ────────────────────────────────────────────────────
# 사용자 2026-09-11: "종목앞에 별표로 중요표시해서 내가 선택할수 있게 해주고,
# 위쪽에 중요표시것만 선택해서 볼수있게 필터같은거 만들어줘. 특히 팔로우업
# 해야하는 종목에 대해서 체크하려는 용도야."
#
# ⚠️ 별표는 **목록 속성**이지 가격 파생값이 아니다. 그래서 두 가지를 지킨다:
#   (a) 쓸 때 가격 캐시를 무효화하지 않는다(`_save(..., invalidate_cache=False)`)
#   (b) 읽을 때 **디스크 정본에서 덧입힌다**(`_apply_stars`)
# (b)가 없으면 `_FAV_CACHE`(3분)·디스크 스냅샷(6시간)이 별표 없는 옛 행을
# 계속 줘서 "별을 눌렀는데 안 켜진다"가 된다 — 이 레포에서 캐시가 fix 를
# 가린 실패가 여섯 번 반복됐고(#18·#21b·#95·#124·#198·#216), 매번 규율로는
# 졌다. 구조로 막는다(#119).
_STAR_KEY = "starred"
# ⚠️ 이 파일의 **모든 writer** 가 같은 락 아래 load→save 를 한다. 락은 **전원이
# 참여할 때만** 상호배제이고, 하나라도 빠지면 그 하나가 남의 쓰기를 덮는다.
# 처음엔 "추가·삭제·순서는 전부 사용자 클릭이라 서로 직렬" 이라고 적고 별표와
# 데몬 둘만 걸었는데, 독립 리뷰가 **재현**했다(2026-09-11): `ThreadingHTTPServer`
# 는 `/api/favorite_add` 와 `/api/favorite_star` 를 다른 스레드로 처리하고,
# `add_favorite` 는 `_load()` 뒤 **yfinance `.info`/`.calendar` 로 수 초**를
# 쓴 다음 그 낡은 목록을 쓴다 — 그 사이 찍은 별표가 통째로 사라지고(화면은
# ★ 를 칠했다가 60초 뒤 ☆ 로 되돌아간다), 반대로 별표 쓰기가 방금 담은 종목을
# 지우기도 한다. 창이 좁기는커녕 **수 초**다(#295 사용자 입력 유실 · #286
# 주석이 자기 자신에 대해 거짓이면 다음 사람이 그대로 믿는다).
# ⚠️ 네트워크 I/O 는 **락 밖**이다 — 안에 두면 느린 yfinance 한 건이 별표
# 클릭을 몇 초씩 막는다. 대신 쓰기 직전에 락 안에서 **다시 읽는다**.
_DISK_LOCK = _threading.Lock()


def _star_map() -> dict:
    """디스크 정본의 {티커(대문자): 별표} — 순수-ish(읽기 전용)."""
    return {_tkey(f.get("ticker")): bool(f.get(_STAR_KEY)) for f in _load()}


def apply_stars(rows: list, stars: dict) -> list:
    """행에 별표를 덧입힌 **새 리스트**(순수).

    입력을 손대지 않는다 — `_FAV_CACHE` 의 dict 를 제자리에서 고치면 캐시가
    별표를 굽게 되고, 그러면 (b)의 요점이 사라진다.
    목록에 없는 티커(스냅샷에만 남은 행)는 False — 지어내지 않는다(#165).
    """
    return [{**r, _STAR_KEY: bool(stars.get(_tkey(r.get("ticker"))))}
            for r in rows or []]


def _apply_stars(rows: list) -> list:
    """`apply_stars` + 디스크 정본 읽기(화면 경로 전용)."""
    return apply_stars(rows, _star_map())


def set_favorite_star(ticker: str, starred: bool) -> bool:
    """별표 저장. **값이 실제로 바뀌었을 때만** True.

    ⚠️ 바뀔 게 없으면 아무것도 쓰지 않는다 — 파괴적이지 않은 write 라도
    매 클릭 디스크를 다시 쓰면 `name_kr` 백필과 겹칠 때 잃을 게 생긴다
    (#295 "바뀔 게 없으면 아무것도 쓰지 않는 것이 가장 강한 방어").
    """
    want = bool(starred)
    key = _tkey(ticker)
    if not key:            # 빈 키는 티커 없는 행에 별을 찍는다
        return False
    with _DISK_LOCK:
        favorites = _load(strict=True)
        # 같은 키의 행 **전부**를 고친다(`remove_favorite` 가 전부 지우는 것과 같은
        # 규약) — 첫 행만 고치면 읽는 쪽(`_star_map`·`is_starred`)이 다른 행을 보고
        # ☆ 를 답해 별표가 영영 안 먹는다(델타 리뷰 L5 — 락 안 재검사가 들어오기
        # 전의 동시 담기로 중복 행이 남아 있을 수 있다).
        hits = [f for f in favorites if _tkey(f.get("ticker")) == key]
        if not hits:
            return False
        if all(bool(f.get(_STAR_KEY)) == want for f in hits):
            return False
        for f in hits:
            f[_STAR_KEY] = want
        _save(favorites, invalidate_cache=False)
    return True


def starred_tickers() -> list:
    """별표된 티커(저장 순서) — 텔레그램·알림 쪽에서 쓸 읽기 전용 헬퍼."""
    return [f.get("ticker") for f in _load() if f.get(_STAR_KEY)]


def is_starred(ticker) -> bool:
    """디스크 정본에서 그 티커의 별표 — 덧입히기(`_star_map`)와 **같은 키**(`_tkey`).

    별표 응답이 따로 `.upper()` 로 비교하면 공백 붙은 티커에서 디스크는 ★ 인데
    응답은 ☆ 라 화면이 되돌린다(독립 리뷰 2026-10-06 L4 — `_tkey` 독스트링이
    약속한 '같은 키' 가 응답 한 곳에서 깨져 있었다, #38).
    """
    return bool(_star_map().get(_tkey(ticker)))


def reorder_favorite(ticker: str, direction: str) -> bool:
    """Move a ticker in the saved order. Persists.

    direction: 'up'/'down' (한 칸) | 'top'/'bottom' (맨 위/아래 — 사용자 2026-06-17
    '하나씩 올리면 끝까지 한참'). Returns True if order changed."""
    key = _tkey(ticker)
    if not key:
        return False
    with _DISK_LOCK:          # load→save 는 한 덩어리(`_DISK_LOCK` 주석)
        favorites = _load(strict=True)
        idx = next((i for i, f in enumerate(favorites)
                    if _tkey(f.get("ticker")) == key), None)
        if idx is None:
            return False
        if direction == "up" and idx > 0:
            favorites[idx - 1], favorites[idx] = favorites[idx], favorites[idx - 1]
        elif direction == "down" and idx < len(favorites) - 1:
            favorites[idx + 1], favorites[idx] = favorites[idx], favorites[idx + 1]
        elif direction == "top" and idx > 0:
            favorites.insert(0, favorites.pop(idx))      # 맨 위로
        elif direction == "bottom" and idx < len(favorites) - 1:
            favorites.append(favorites.pop(idx))         # 맨 아래로
        else:
            return False
        _save(favorites)
    return True


_FAV_CACHE: "list | None" = None
_FAV_CACHE_TS: float = 0.0
_FAV_TTL = 180   # 관심종목 가격 캐시 3분 — 위젯 반복 로드(브라우저 /api/favorites)가
#                  매번 fast_info 를 버스트해 회로차단을 재트립하던 것 차단 (사용자
#                  2026-06-14 '야후 멈춤 너무 힘들어'). dashboard_server 단일 프로세스 캐시.
_FAV_REFRESHING = False
_FAV_LOCK = _threading.Lock()


# ── 디스크 스냅샷 ────────────────────────────────────────────────────
# ⚠️ 사용자 2026-08-22: "관심종목이 너무 자주 꺼져". 캐시가 **메모리 전용**이라
# 봇이 재시작될 때마다(배포·watchdog) 139종목이 통째로 `—` 가 됐고, 다시
# 채우는 데 수십 초가 걸렸다. 마지막 성공분을 디스크에 남겨 두면 재시작 직후
# 에도 화면이 비지 않는다.
# ⚠️ 다만 **낡은 값을 '현재'로 내보내면 안 된다**(2026-08-20 감사에서 잡힌 그
# 사고) — 그래서 스냅샷에는 `as_of` 를 같이 실어 화면이 기준시각을 밝히고,
# 너무 낡으면(_SNAP_MAX_AGE) 아예 안 쓴다(#43 침묵이 최악).
_SNAP_MAX_AGE = 6 * 3600


def _snap_path() -> Path:
    return _FAVORITES_FILE.with_name("favorites_snapshot.json")


def _snapshot_save(rows: list, ts: float) -> None:
    try:
        _snap_path().write_text(
            json.dumps({"as_of": ts, "rows": rows}, ensure_ascii=False),
            encoding="utf-8")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("favorites snapshot save: %s", exc)


def _snapshot_load() -> tuple[list | None, float]:
    """(행, 기준시각) — 없거나 너무 낡으면 (None, 0)."""
    import time as _time
    try:
        p = _snap_path()
        if not p.exists():
            return None, 0.0
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = float(d.get("as_of") or 0)
        rows = d.get("rows")
        if not rows or _time.time() - ts > _SNAP_MAX_AGE:
            return None, 0.0
        return rows, ts
    except Exception as exc:                                   # noqa: BLE001
        log.warning("favorites snapshot load: %s", exc)
        return None, 0.0


def _as_of_dict(ts: float) -> dict:
    """수집 시각(epoch) → {"ts": KST 문자열, "age": 초}. 없으면 빈 dict
    (재료가 없으면 판정 불가 — 지어내지 않는다, #54·#165)."""
    import datetime as _dt
    import time as _time
    if not ts:
        return {}
    kst = _dt.timezone(_dt.timedelta(hours=9))
    return {"ts": _dt.datetime.fromtimestamp(ts, kst).strftime("%Y-%m-%d %H:%M"),
            "age": max(0.0, _time.time() - ts)}


def favorites_rows_with_as_of() -> tuple[list[dict], dict]:
    """(행, 그 행의 수집 시각) — **한 번의 읽기로 묶어서** 돌려준다.

    행과 시각을 따로 읽으면 그 사이 SWR 백그라운드가 `_FAV_CACHE`/`_FAV_CACHE_TS`
    를 다시 바인딩해 **낡은 행에 방금 시각이 찍힌다**(2026-09-11 독립 리뷰).
    콜드 스타트 분기는 더 나쁘다 — 값이 전부 None 인 표에 '값 수집 <지금>' 이
    붙는다. 창이 마이크로초라 드물지만, 순서에 기댄 안전은 언젠가 깨진다(#102a).
    여기서 `_FAV_LOCK` 아래 한 번에 집으면 **구조적으로** 어긋날 수 없다.
    """
    # ⚠️ 별표는 **여기서** 디스크 정본으로 덧입힌다 — 캐시(3분)·스냅샷(6시간)
    # 어느 층이 행을 주든 화면의 별표가 정본과 같다(`set_favorite_star` 주석).
    rows = _apply_stars(get_favorites_with_prices())
    with _FAV_LOCK:
        ts = _FAV_CACHE_TS if (_FAV_CACHE is not None and _FAV_CACHE_TS) else 0.0
    if not ts:
        _snap, sts = _snapshot_load()
        ts = sts if _snap else 0.0
    return rows, _as_of_dict(ts)


def favorites_as_of() -> dict:
    """관심종목 값의 **수집 시각** — {"ts": "YYYY-MM-DD HH:MM", "age": 초} (KST).

    "이거 최신이야?" 에 화면이 답하지 못하면 그게 결함이다(#43·#304 값 수집 시각).
    ⚠️ 행과 같이 쓸 거면 `favorites_rows_with_as_of()` 를 쓸 것 — 따로 읽으면
    낡은 행에 새 시각이 붙는다. 이 함수는 행 없이 시각만 필요한 자리용이다."""
    with _FAV_LOCK:
        ts = _FAV_CACHE_TS if (_FAV_CACHE is not None and _FAV_CACHE_TS) else 0.0
    if not ts:
        _snap, sts = _snapshot_load()
        ts = sts if _snap else 0.0
    return _as_of_dict(ts)


def get_favorites_with_prices() -> list[dict]:
    """관심종목 + 현재가/추정치 — **렌더-세이프 SWR (사용자 2026-06-16 '오래걸려')**.

    엔드포인트(/api/favorites)가 종목당 yfinance(tk.info EPS/PER·fast_info·history)를
    동기로 때려 '불러오는 중…'이 오래 걸리던 것 해소: 신선 캐시 즉시 / 스테일·콜드 시
    **백그라운드 daemon 갱신 + 즉시 반환**(스테일 있으면 스테일, 없으면 이름만 —
    위젯 폴이 곧 가격 채움). 가격 캐시 3분 + fast_info 회로차단 게이트 유지.

    ⚠️ 목록의 **구성·순서**는 어느 층에서 값을 받든 **지금 디스크**에서 온다 — 캐시
    층(메모리 3분 · 디스크 스냅샷 6시간)은 같은 티커의 **파생값만** 덧입힌다
    (`overlay_derived`). 옛 판은 신선·스테일 메모리 캐시를 **통째로** 돌려줬는데, 그
    캐시는 갱신이 **시작할 때** 읽은 목록이라 갱신 도중의 삭제·추가·순서 변경을 갱신이
    끝날 때 되돌렸다 — 지운 종목이 되살아나고, 그 뒤 ✕ 는 디스크에 이미 없어 아무 일도
    안 했다(사용자 2026-10-06 "또 갑자기 버튼이 안먹혀", 실수 #436). 별표를 읽는 시점에
    덧입히는 것(#344)과 같은 구조다 — 규율이 아니라 구조로 막는다(#119).
    """
    import time as _time
    now = _time.time()
    # 캐시 참조와 시각을 지역 변수로 한 번씩 집는다 — 아래 판정과 덧입히기가 **같은
    # 행 목록**을 보게. ⚠️ 두 전역은 갱신이 따로 대입하므로 이 읽기도 원자적이진
    # 않다((새 행, 옛 시각)이 보일 수 있다) — 그 결과는 갱신을 한 번 더 거는 것
    # (중복은 `_kick_fav_refresh` 가 막는다)뿐이라 무해하다(독립 리뷰 L8a).
    cache, cache_ts = _FAV_CACHE, _FAV_CACHE_TS
    # 못 읽은 목록을 빈 목록으로 그리지 않는다 — 화면이 사유를 말한다(L5).
    disk = _load(strict=True)
    if cache is not None:
        rows, missing = overlay_derived(disk, cache)
        # 스테일이거나 **갱신 뒤에 담긴 종목**이 있으면 뒤에서 다시 받는다(비차단·
        # 중복 방지는 `_kick_fav_refresh` 가 한다). 담은 종목이 다음 TTL 까지 값 없이
        # 머물지 않게 — 담은 직후 다시 받는 것은 옛 판(`_save` 가 캐시를 비움)과 같다.
        if missing or (now - cache_ts) >= _FAV_TTL:
            _kick_fav_refresh()
        return rows
    _kick_fav_refresh()              # 콜드 → 백그라운드 full 갱신(비차단)
    # 재시작 직후엔 메모리 캐시가 없다 — 마지막 성공분을 기준시각과 함께 낸다.
    snap, ts = _snapshot_load()
    if snap:
        import datetime as _dt
        _as_of = _dt.datetime.fromtimestamp(
            ts, _dt.timezone(_dt.timedelta(hours=9))).strftime("%m-%d %H:%M")
        rows, missing = overlay_derived(disk, snap)
        absent = {_tkey(t) for t in missing}
        # 기준시각은 스냅샷 값을 실제로 받은 행에만 — 빈 행에 시각을 붙이면 그
        # 빈칸이 그 시각에 확인한 '없음' 으로 읽힌다(#43·#165). ⚠️ 이건 **payload
        # 계약**이다 — 지금 화면(renderFavs)은 행별 `as_of` 를 안 읽고 헤더의
        # `d.as_of` 만 쓴다(독립 리뷰 L8b). 읽는 쪽이 생겨도 거짓이 안 되게 둔다.
        return [r if _tkey(r.get("ticker")) in absent else {**r, "as_of": _as_of}
                for r in rows]
    return _cold_rows(disk)          # 첫 로드 — 이름만(가격은 위젯 다음 폴에 채워짐)


# 종목 추가 시점에 디스크로 굳는 **휘발성 파생값**. 콜드 로드에서 그대로
# 내보내면 화면이 몇 달 전 숫자를 '현재' 로 보여준다(2026-08-20 감사에서
# 발각: 관심종목 108행이 `현재가 None` 인데 PER 은 6.289547 로 남아 있었다 —
# 종목을 담던 날의 값이다). 위 docstring 의 의도("첫 로드 — 이름만")를
# 구현이 안 지키고 있었다. `saved_price`·`saved_date` 는 **의도적 과거값**이라
# 남긴다.
_VOLATILE_FIELDS = ("current_price", "per", "eps_estimate", "market_cap",
                    "eps_trailing_src",
                    "eps_is_actual", "eps_fy_label", "eps_negative",
                    "per_is_trailing", "eps_trailing", "per_trailing",
                    # 야후 표기 별칭 해석 결과(2467.TT → 2467.TW). 없으면
                    # 재시작마다 "→ … 로 조회" 힌트가 사라졌다가 다음 데몬
                    # 갱신에 돌아온다 — 화면이 깜빡이면 사용자는 그걸 결함으로
                    # 읽는다(독립 리뷰 2026-09-08 · #163 되살린 값 규약).
                    "yf_ticker")


# 가격 갱신(`_compute_favorites_with_prices`)이 **만드는** 칸 — 캐시 층이 디스크
# 목록에 덧입히는 것은 이것뿐이다. 목록의 구성·순서·저장 시점 값(`saved_*`)·별표는
# 언제나 디스크가 정본이다(실수 #436). `next_earnings` 는 담던 날 디스크에도 굳지만
# 갱신이 미래 일정으로 고치고, `name_kr` 은 갱신이 해석한다(빈 값이면 디스크 것을
# 남긴다). ⚠️ 갱신이 새 칸을 만들면 **여기에 같이** 적을 것 — 안 적으면 화면에서
# 그 칸만 조용히 빈다. 회귀가 갱신 본문의 `f["…"] =` 전수와 대조한다(#24).
_DERIVED_FIELDS = _VOLATILE_FIELDS + ("next_earnings", "name_kr")


def _cold_row(f: dict) -> dict:
    """한 행의 휘발성 파생값을 지운 **새** dict — 낡은 숫자를 '현재'로 내보내지
    않는다(입력은 손대지 않는다)."""
    return {**f, **{k: None for k in _VOLATILE_FIELDS if k in f}}


def _cold_rows(rows: "list | None" = None) -> list[dict]:
    """디스크 원본에서 휘발성 파생값을 지운 사본 — 낡은 숫자를 '현재'로
    내보내지 않는다. 빈칸은 다음 폴에서 daemon 이 채운다."""
    return [_cold_row(f) for f in (_load() if rows is None else rows)]


def overlay_derived(disk: list, rows) -> tuple[list, list]:
    """(디스크 목록에 캐시 행의 파생값만 덧입힌 **새** 리스트, 캐시에 없던 티커).

    순수 함수. 결과의 **구성·순서**는 `disk` 그대로다 — 캐시에만 있는 행(갱신 도중에
    지운 종목)은 버리고, 캐시에 없는 행(갱신이 디스크를 읽은 뒤 담은 종목)은 콜드 행
    규약대로 휘발성 칸을 비운다(담던 날의 숫자를 '현재'로 내보내지 않는다, #43).
    짝이 있는 행도 디스크 행의 휘발성 칸을 먼저 비운 뒤 캐시 값을 얹는다 — 지웠다가
    다시 담은 종목이면 디스크엔 새 `saved_*`, 캐시엔 옛 행이 있어서다.
    입력은 손대지 않는다 — 캐시가 들고 있는 dict 를 고치면 디스크 값이 캐시에
    구워진다(#344 덧입히기의 요점).
    """
    by: dict = {}
    for r in rows or []:
        by.setdefault(_tkey(r.get("ticker")), r)
    out, missing = [], []
    for d in disk or []:
        r = by.get(_tkey(d.get("ticker")))
        if r is None:
            missing.append(d.get("ticker"))
            out.append(_cold_row(d))
            continue
        out.append({**_cold_row(d),
                    **{k: r[k] for k in _DERIVED_FIELDS
                       if k in r and (k != "name_kr" or r[k])}})
    return out, missing


def _kick_fav_refresh() -> None:
    """백그라운드 daemon — full yfinance 갱신(_compute). dedup + daemon(종료 블로킹 0)."""
    global _FAV_REFRESHING
    with _FAV_LOCK:
        if _FAV_REFRESHING:
            return
        _FAV_REFRESHING = True

    def _run():
        global _FAV_REFRESHING
        try:
            _compute_favorites_with_prices()
        except Exception as exc:
            log.warning("favorites refresh: %s", exc)
        finally:
            with _FAV_LOCK:
                _FAV_REFRESHING = False

    _threading.Thread(target=_run, daemon=True, name="fav-refresh").start()


# ── 국내 실적 EPS(현재 PER 용) ────────────────────────────────────────
# ⚠️ 사용자 2026-08-23: 관심종목의 국내 종목 '현재 PER' 이 전부 `—` 였다
# (한텍·뉴파워프라즈마·쿠콘·삼성전자). yfinance 가 KR 종목의 trailingEps 를
# 안 준다 — 감사 로그에도 `No fundamentals data found for symbol: 005930.KS`
# 로 찍힌다. 대신 **KRX 투자지표**는 전 종목 EPS 를 한 번에 준다(레포에
# 이미 있는 `stock_screener._fetch_kr_bulk` = HTTP 2건). 140종목을 종목마다
# 치지 않아도 되는 이유다(#미니멀: 이미 있으면 재사용).
_KR_EPS: dict | None = None
_KR_EPS_TS: float = 0.0
_KR_EPS_TTL = 6 * 3600      # KRX 투자지표는 일 1회 갱신 — 6시간이면 충분


def _kr_eps_bulk() -> dict:
    """{6자리코드: 실적 EPS} — 실패하면 빈 dict(호출부는 그대로 비운다).

    ⚠️ 실패 사유를 삼키지 않는다(#12 silent-fail 금지) — 자격증명 부재와
    원천 오류는 다른 문제이고, '없음'만 말하면 다음 라운드를 낭비한다(#82).
    """
    global _KR_EPS, _KR_EPS_TS
    import time as _time
    now = _time.time()
    if _KR_EPS is not None and (now - _KR_EPS_TS) < _KR_EPS_TTL:
        return _KR_EPS
    out: dict = {}
    try:
        from bot.stock_screener import _fetch_kr_bulk
        bulk = _fetch_kr_bulk()
        if not bulk:
            log.warning("favorites: KRX 벌크 실적지표 없음 — 국내 현재 PER 은 "
                        "빈칸으로 둔다(자격증명 또는 원천 확인)")
        else:
            for code, row in bulk.items():
                eps = row.get("EPS")
                # KRX 는 적자 종목의 EPS 를 0 으로 준다 — 0 은 '적자'와 '미제공'을
                # 구별하지 못하므로 값으로 쓰지 않는다(#43 모르면 비운다).
                if eps and eps > 0:
                    out[str(code).zfill(6)] = float(eps)
            log.info("favorites: KRX 실적 EPS %d종목 적재", len(out))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("favorites: KRX 벌크 실적지표 실패 — %s: %s",
                    type(exc).__name__, exc)
    _KR_EPS, _KR_EPS_TS = out, now
    return out


def _compute_favorites_with_prices() -> list[dict]:
    """관심종목 full 갱신(yfinance per-ticker) — 백그라운드 daemon 전용. _FAV_CACHE 적재."""
    global _FAV_CACHE, _FAV_CACHE_TS
    import time as _time
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    # 못 읽으면 던진다 — 빈 목록으로 알고 진행하면 감사(`board_audit`)가 '0종목
    # ✅' 를 찍는다(#54). 캐시·스냅샷은 그대로 남는다(호출부가 경고를 남긴다).
    favorites = _load(strict=True)
    if not favorites:
        return favorites
    # fast_info 허용? 회로차단 쿨다운/정지 중이면 skip → .info/history 폴백
    try:
        from bot.finviz_client import fast_info_ok, yf_paused
        _fi_allowed = fast_info_ok() and not yf_paused()
    except Exception:
        _fi_allowed = True
    # 국내 EPS 는 **풀 밖에서 한 번**만 받는다 — 스레드마다 부르면 같은 벌크를
    # 140번 두드린다(#113 진행 중인 중복은 캐시가 못 막는다).
    _kr_eps = _kr_eps_bulk() if any(
        _detect_country(f.get("ticker") or "") == "KR" for f in favorites) else {}

    def _refresh(f: dict) -> None:
        # 네이버 실시간 시세 우선 (사용자 2026-06-15 '시총·현재가 네이버, 대만
        # 제외') — 현재가·시총·한글명을 한 콜로. TW·기타·실패는 None → yfinance.
        nq = _naver_quote_for(f["ticker"])
        naver_price = nq.get("price") if nq else None
        # name_kr 미해결(부재 OR 영문 fallback==name)이면 (재)해석 — #419 이전
        # 영문으로 영속된 TW / Naver 일시실패분을 자가치유(if-not-name_kr 게이트가
        # 영문 영속분을 영구 고착시키던 것 해소). 진짜 한글명(≠name)은 skip.
        # translate_names_kr·Naver 둘 다 캐시라 재호출 싸다.
        _cur_kr = f.get("name_kr")
        # ⚠️ 되읊기가 **영속된** 값도 미해결이다 — `3296.TWO | 승덕` 은 `name`
        # 과 달라 옛 게이트를 통과해 영구 고착됐다(2026-09-17 실측, #381·#18).
        # 정화는 읽는 경계가 하지만 여기 값은 **복사본**이라 안 따라온다(#38).
        try:
            from bot.chart_translate import clean_answer as _clean
            _echoed = bool(_cur_kr) and _clean(_cur_kr) != _cur_kr
        except Exception:                                      # noqa: BLE001
            _echoed = False
        if not _cur_kr or _cur_kr == f.get("name") or _echoed:
            _new_kr = ((nq.get("name") if nq else None)
                       or _resolve_kr_name(f["ticker"], f.get("name") or f["ticker"]))
            if _new_kr:
                f["name_kr"] = _new_kr
        try:
            _sym = _resolve_yf(f["ticker"])
            # 원문과 다르면 화면이 밝힌다 — 조용히 바꾸면 사용자가 자기 목록의
            # 티커와 화면 값을 대조하지 못한다(#136·#43).
            f["yf_ticker"] = _sym if _sym != f["ticker"] else None
            tk = yf.Ticker(_sym)
            price = naver_price       # 네이버 있으면 fast_info 생략(야후 부하·글리치↓)
            info = None
            prev_close = None
            if price is None and _fi_allowed:
                try:
                    fi = tk.fast_info
                    price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                    prev_close = getattr(fi, "previous_close", None)
                except Exception as _exc:
                    try:    # rate-limit 이면 회로차단 발동(전 소비처 skip·30분 쿨다운)
                        from bot.finviz_client import (fast_info_trip,
                                                       is_rate_limit_error)
                        if is_rate_limit_error(_exc):
                            fast_info_trip("favorites")
                    except Exception:
                        pass
            if price is None:
                try:
                    info = tk.info or {}
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    prev_close = prev_close or info.get("previousClose")
                except Exception:
                    info = {}
            # 가격 글리치 가드 — yfinance 폴백 경로만 (네이버 실시간은 클린이라
            # skip). KLAC 클래스: 직전 종가 대비 ±75% 초과면 직전 종가로 교체.
            glitched = False
            if naver_price is None:
                try:
                    from bot.price_sanity import quote_glitch_gap
                    if quote_glitch_gap(price, prev_close):
                        price = prev_close
                        glitched = True
                    # 2차 — price·prev 가 둘 다 같은 미조정 기준이면 1차가 장님
                    # (KLAC $2,411 vs $2,398 통과). 조정 일봉 종가와 교차.
                    if not glitched and price:
                        hist = tk.history(period="5d")
                        if hist is not None and len(hist) and "Close" in hist:
                            hc = float(hist["Close"].dropna().iloc[-1])
                            if hc > 0 and quote_glitch_gap(price, hc):
                                price = hc
                                prev_close = hc
                                glitched = True
                except Exception:
                    pass
            f["current_price"] = price

            if info is None:
                try:
                    info = tk.info or {}      # EPS/PER/주식수/실적일 — yfinance 유지
                except Exception:
                    info = {}

            # 시총: 네이버 우선 (KR=원 신뢰 / 해외=price×shares 단위 sanity 통과
            # 시만 — 네이버 해외 시총 단위 불확실, 원/달러 혼동 방지), 없으면
            # yfinance (글리치 시 직전종가×주식수 재산출).
            mcap = info.get("marketCap")
            if glitched and mcap:
                shares = info.get("sharesOutstanding")
                mcap = (shares * prev_close
                        if (shares and prev_close) else None)
            naver_mcap = nq.get("mcap") if nq else None
            if naver_mcap and naver_price:
                if _detect_country(f["ticker"]) == "KR":
                    mcap = naver_mcap          # 국내 marketValueFullRaw = 원, 신뢰
                else:
                    sh = info.get("sharesOutstanding")
                    implied = naver_price * sh if sh else 0
                    if implied and 0.5 <= naver_mcap / implied <= 2.0:
                        mcap = naver_mcap      # 해외: 단위 일치 확인 시만
            f["market_cap"] = mcap

            fwd = info.get("forwardEps")
            trail = info.get("trailingEps")
            f["eps_estimate"] = fwd if fwd is not None else trail
            f["eps_is_actual"] = (fwd is None and trail is not None)
            # 현재 PER 용 **실적 기준** EPS — 예상과 따로 든다(사용자 2026-08-22
            # "예상 EPS 를 현재 PER 로 바꿔줘"). 둘 다 **화면의 현재가**에서
            # 만들어야 눈으로 나눠 봐도 맞는다(#33).
            f["eps_trailing"] = trail
            f["eps_trailing_src"] = "yfinance" if trail is not None else None
            # 국내는 yfinance 가 trailingEps 를 안 준다 — KRX 투자지표로 채운다.
            # ⚠️ PER 은 **화면의 현재가**로 만든다(아래 `_per_from_shown`) —
            # 원천 PER 을 그대로 실으면 눈으로 나눠 봤을 때 안 맞는다(#33).
            if f["eps_trailing"] is None and _kr_eps:
                _kr = _kr_eps.get(str(f["ticker"]).split(".")[0].zfill(6))
                if _kr:
                    f["eps_trailing"] = _kr
                    f["eps_trailing_src"] = "KRX"

            # ⚠️ 소스 PER(forwardPE/trailingPE)을 쓰지 않는다 — 그 값은
            # yfinance 자신의 EPS·가격 기준이라 이 표의 현재가·예상 EPS 와
            # 나눗셈이 안 맞는다(2026-08-20 실측: 삼성전자 3.7 vs 17.4).
            f["per_is_trailing"] = f["eps_is_actual"]

            lfy = info.get("lastFiscalYearEnd")
            fy_label = None
            if lfy and isinstance(lfy, (int, float)):
                fy_label = f"FY{datetime.fromtimestamp(lfy).year % 100:02d}"
            f["eps_fy_label"] = fy_label if f.get("eps_is_actual") else None
            f["eps_negative"] = (f.get("eps_estimate") is not None
                                 and f["eps_estimate"] < 0)

            try:
                cal = tk.calendar
                if isinstance(cal, dict):
                    if cal.get("Earnings Average") is not None and fwd is None:
                        f["eps_estimate"] = cal["Earnings Average"]
                        f["eps_is_actual"] = False
                    earn_dates = cal.get("Earnings Date") or []
                    if earn_dates:
                        d = earn_dates[0]
                        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                        f["next_earnings"] = _future_or_none(ds)
            except Exception:
                pass
            # 저장돼 있던 옛 날짜가 이미 과거면 빈칸 (yfinance 가 새 일정을
            # 안 주는 KR 케이스 — SK hynix 2026-04-22 사용자 2026-06-11).
            if f.get("next_earnings"):
                f["next_earnings"] = _future_or_none(f["next_earnings"])
            # calendar 부재/과거 → 실적탭과 동일 소스(earnings_dates, 미래
            # 추정 포함)로 폴백 — 하이닉스 2026-07-29 케이스(사용자 2026-06-11).
            if not f.get("next_earnings"):
                try:
                    ed = tk.earnings_dates
                    if ed is not None and len(ed.index):
                        today_s = datetime.now().strftime("%Y-%m-%d")
                        fut = sorted(str(ix)[:10] for ix in ed.index
                                     if str(ix)[:10] >= today_s)
                        if fut:
                            f["next_earnings"] = fut[0]
                except Exception:
                    pass
            # ⚠️ **맨 마지막**에 계산한다 — 위 calendar 블록이 eps_estimate 를
            # 컨센서스로 갈아끼울 수 있어서, 그 전에 만들면 EPS 와 PER 이 다시
            # 어긋난다(이 버그의 원래 형태가 정확히 그거였다).
            f["per"] = _per_from_shown(f.get("current_price"),
                                       f.get("eps_estimate"))
            f["per_is_trailing"] = bool(f.get("eps_is_actual"))
            f["per_trailing"] = _per_from_shown(f.get("current_price"),
                                                f.get("eps_trailing"))
        except Exception:
            f["current_price"] = None
            f["per"] = None

    with ThreadPoolExecutor(max_workers=min(len(favorites), 8)) as pool:
        pool.map(_refresh, favorites)

    # name_kr 백필분만 깨끗이 영속 (정적이라 1회) — volatile 가격은 저장 안 함:
    # 디스크 재로드 → name_kr 복사 → save. 이후 cold load 부턴 재호출 0.
    try:
        by_t = {f["ticker"]: f for f in favorites}
        # ⚠️ 별표 write 와 같은 락 — 이 블록이 `_load()` 와 `_save()` 사이에
        # 별표를 삼키지 않게 한다(`_DISK_LOCK` 주석). `acquire()` + 플래그로
        # 쓰면 `acquire()` 가 돌아온 직후·플래그 대입 전에 들어온 시그널이
        # 락을 **영구히** 남긴다(그러면 이후 모든 별표 write 가 멈춘다) —
        # `with` 면 그 틈이 없다.
        with _DISK_LOCK:
            disk = _load()
            _chg = False
            for d in disk:
                f = by_t.get(d["ticker"])
                nk = f.get("name_kr") if f else None
                if not nk:
                    continue
                # 한글명(영문 name 과 다름)이면 갱신(영문→한글 치유 포함);
                # 디스크가 비어있으면 영문 fallback 이라도 채움. 기존 한글명을
                # 영문으로 안 덮음.
                if ((nk != f.get("name") and nk != d.get("name_kr"))
                        or not d.get("name_kr")):
                    d["name_kr"], _chg = nk, True
            if _chg:
                _save(disk)
    except Exception:
        pass

    _FAV_CACHE = favorites
    _FAV_CACHE_TS = _time.time()
    _snapshot_save(favorites, _FAV_CACHE_TS)
    return favorites


# ── 1회성 정리 CLI ───────────────────────────────────────────────────
# ⚠️ **엔트리포인트는 파일 맨 끝**이다 — 중간에 두면 그 아래 정의가 영영
# 안 닿는다(#276). 그리고 인쇄하는 실행 안내엔 `cd ~/stock &&` 를 반드시
# 붙인다: `python -m` 은 cwd 에서 패키지를 찾으므로 홈에서 돌리면
# `ModuleNotFoundError` 다(#278).
def _cli_sort_saved(apply_it: bool) -> int:
    """저장일 내림차순으로 목록을 정리한다 → rc(0 정상).

    ⚠️ **바꾸기 전에 백업**한다. 이 정렬은 사용자가 ↕ 로 맞춰 둔 배열을
    덮어쓰므로 되돌릴 길이 없으면 안 된다 — 백업 경로와 복구 명령을 같이
    찍는다(#43 침묵이 최악).
    ⚠️ 무엇을 바꿨는지 **숫자로** 보여준다 — '정리했습니다' 만으로는 사용자가
    확인할 방법이 없다(#202·#274 이상 없음도 말할 것).
    """
    import json
    import shutil
    from datetime import datetime as _dt

    # ⚠️ '비었다' 와 '못 읽는다' 는 처방이 정반대다(#82·#279). 못 읽는 갈래는
    # `_load(strict=True)` 가 이름을 대 준다 — 여기서 분류기를 따로 두면 `_load`
    # 에 갈래가 늘 때 갈라진다(델타 리뷰 L1: 목록이 아닌 JSON 을 '정상 JSON 인데
    # 비어 있다' 로 찍었다, #38).
    try:
        cur = _load(strict=True)
    except FavoritesUnreadable as exc:
        print(f"❌ {exc.reason}\n   경로: {_FAVORITES_FILE}")
        return 1
    if not cur:
        why = ("파일이 없다 — 관심종목을 한 번도 안 담았다"
               if not _FAVORITES_FILE.exists() else "목록이 비어 있다(정상 JSON)")
        print(f"❌ 관심종목이 0건이다 — {why}\n   경로: {_FAVORITES_FILE}")
        return 1
    new = sort_by_saved(cur)
    no_date = [f.get("ticker") for f in cur if not f.get("saved_date")]
    moved = sum(1 for a, b in zip(cur, new)
                if a.get("ticker") != b.get("ticker"))

    def _line(f):
        return f"{f.get('saved_date') or '날짜없음':>10}  {f.get('ticker')}"

    print(f"관심종목 {len(cur)}건 · 저장일 내림차순 정리"
          f"{' (미리보기)' if not apply_it else ''}")
    print("  [현재 위 3]  " + " | ".join(_line(f) for f in cur[:3]))
    print("  [정리 후 위 3] " + " | ".join(_line(f) for f in new[:3]))
    print("  [정리 후 아래 3] " + " | ".join(_line(f) for f in new[-3:]))
    print(f"  자리가 바뀌는 항목 {moved}건 · 저장일 없는 항목 "
          f"{len(no_date)}건(맨 아래로){' — ' + ', '.join(map(str, no_date[:5])) if no_date else ''}")
    # ⚠️ 개수만 세면 `sorted()` 가 길이를 안 바꾸므로 **영원히 통과하는 죽은
    # 가드**다(#291 늘 ✅ 인 축은 판정이 아니다). 티커 **다중집합**으로 재야
    # 정렬이 항목을 바꾸거나 잃는 변형을 실제로 잡는다.
    if sorted(map(str, (f.get("ticker") for f in new))) != \
       sorted(map(str, (f.get("ticker") for f in cur))):
        print(f"❌ 종목 구성이 달라졌다 {len(cur)} → {len(new)} — 중단(쓰지 않음)")
        return 1
    if moved == 0:
        # ⚠️ 바꿀 게 없는데 백업하고 다시 쓰면, 그 백업은 **이미 정렬된 것**이라
        # 되돌릴 원본이 사라진다(독립 리뷰 실측). 계산해 둔 `moved` 를 판정에
        # 쓴다(#123 계열) — 이상 없음도 한 줄로 말한다(#274).
        print("\n✅ 이미 저장일 순이다 — 바꿀 것이 없어 파일을 건드리지 않았다.")
        return 0
    if not apply_it:
        print("\n미리보기만 했다. 실제로 바꾸려면 `--apply` 를 붙일 것:")
        print("  cd ~/stock && .venv/bin/python -m bot.market_favorites"
              " --sort-saved --apply")
        return 0
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    bak = _FAVORITES_FILE.with_name(f"market_favorites.backup-{ts}.json")
    if bak.exists():
        # ⚠️ 초 해상도라 같은 초에 두 번 돌면 **유일한 백업이 정렬본으로
        # 덮인다** — 그 순간 되돌릴 길이 0 이다(#43 백업 의무가 무력화).
        print(f"❌ 같은 이름의 백업이 이미 있다 — 중단(쓰지 않음)\n   {bak}")
        return 1
    shutil.copy2(_FAVORITES_FILE, bak)
    # ⚠️ 같은 프로세스 안의 다른 writer 와 겹치지 않게 락 안에서 쓴다(락은
    # 전원이 참여할 때만 상호배제다 — 회귀가 전수로 강제). **다른 프로세스**
    # (봇 ↔ 이 CLI)까지는 못 막는다 — 그래서 바로 아래 되읽기 검증이 있다.
    with _DISK_LOCK:
        _save(new)
    # ⚠️ 쓴 뒤 **되읽어 확인**한다 — 대시보드의 name_kr 백필도 같은 파일을
    # `_load`→`_save` 하므로 겹치면 옛 순서로 되덮일 수 있다(#79 그 경로가
    # 실제로 반영됐나).
    back = [str(f.get("ticker")) for f in _load()]
    want = [str(f.get("ticker")) for f in new]
    if back != want:
        print(f"\n❌ 썼는데 되읽은 순서가 다르다 — 다른 프로세스가 같이 썼을 수 있다."
              f"\n   되돌리려면: cp {bak} {_FAVORITES_FILE}")
        return 1
    print(f"\n✅ 정리 완료 · {moved}건 이동 · 백업 {bak}")
    print(f"   되돌리려면: cp {bak} {_FAVORITES_FILE}")
    print("   대시보드는 다음 목록 조회(새로고침·60초 폴)에 새 순서를 보여준다 —"
          " 목록의 구성·순서는 읽는 시점의 디스크에서 오고 캐시는 시세 칸만"
          " 덧입힌다(실수 #436).")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    import argparse

    _ap = argparse.ArgumentParser(
        description="관심종목 유지보수 — 저장일 내림차순 1회성 정리")
    _ap.add_argument("--sort-saved", action="store_true",
                     help="저장일 내림차순(최신이 위)으로 정리")
    _ap.add_argument("--apply", action="store_true",
                     help="실제로 파일을 바꾼다(없으면 미리보기)")
    _a = _ap.parse_args()
    if _a.sort_saved:
        raise SystemExit(_cli_sort_saved(_a.apply))
    _ap.print_help()
