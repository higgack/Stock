"""JP/CN/HK 52주 신고가/신저가 — TW 패턴 일반화 (사용자 2026-06-13 '다른 나라도
모두 적용'). finviz_client._compute_highlow_from(US 신고저 스캐너) 재사용 +
SWR(신선 30분/스테일+백그라운드 킥). **동기 계산 안 함**.

유니버스(2026-08-01 정정):
  - JP/HK = 공식 상장목록 **전종목**(JPX data_j / HKEX ListOfSecurities,
    `bot/intl_universe.full_universe`). fetch 실패 시 peer 주요종목 폴백.
  - CN_A  = CSI300+500(AKShare) ~800, 실패 시 peer 폴백.
  - KR    = 전종목(pykrx) 또는 KIS.
  - TW 는 별도 모듈(`bot/tw_highlow.py`, 上市+上櫃 전종목).
⚠️ JP/HK 는 한때 환경변수 opt-in 으로 바뀌어 기본값이 peer(102·51종목)로 조용히
축소돼 있었다 — 보드가 거의 비어 보이던 원인(`_universe` 주석 참조).
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# market → (peer 맵 속성명, 캐시명, 상태명, 위젯 라벨, native 접미사 필터)
# 접미사 필터 = peer 맵에 섞인 해외 비교군 제외(KR 반도체에 TSM/NVDA 등).
# JP/CN/HK 는 맵이 이미 native-only 라 필터가 no-op(검증됨).
_CFG = {
    "JP": ("_JP_INDUSTRY_PEERS", "highlow_jp_v3.json",
           "jp_highlow_status.json", "일본 주요종목", (".T",)),
    # CN_A 52? = A? ???(??? 2026-08-11 ?? ??). ?? A? ??
    # ????? ??? ?? ?? ??, ?? ? CSI300+500 / peer ? ??.
    # ???? ??? ?? ??? coverage ??.
    "CN_A": ("_CN_A_INDUSTRY_PEERS", "highlow_cn_v2.json",
             "cn_highlow_status.json", "?? A? ???", (".SS", ".SZ")),
    "HK": ("_HK_INDUSTRY_PEERS", "highlow_hk_v4.json",
           "hk_highlow_status.json", "홍콩 주요종목", (".HK",)),
    # KR — 사용자 2026-06-13 '한국도 신고가신저가'. KIS 신고저 순위 엔드포인트
    # (1콜·더 쌈)는 VM 검증 대기 → 우선 검증된 yfinance 유니버스 스캔으로.
    "KR": ("_KR_INDUSTRY_PEERS", "highlow_kr_v2.json",
           "kr_highlow_status.json", "한국 주요종목", (".KS", ".KQ")),
}
# v3 (2026-06-14): 네이버 이름맵 + 거래대금(value) 반영 — stale 장-밖 캐시 강제
# 재스캔(옛 v2 는 이름/거래대금 비어있던 포맷).
# 신선도는 시장-인지(finviz_client._session_fresh, 장중 1h / 장 밖 마지막 마감
# 이후 재스캔 0)로 통일 — 사용자 2026-06-13 '모두 장중에만 1h'.
_running: dict[str, bool] = {}
_lock = threading.Lock()


def _cap_by_liquidity(full: list[str], cap: int, market: str) -> list[str]:
    """전종목을 네이버 worldstock 시총 상위 N 으로 캡 — yfinance 1y 스캔 부하↓.
    HK 코드(5자리 '00700')↔yfinance(4자리 '0700')·JP 4자리는 선행 0 제거 정수로
    매칭. world_stock_map 부재 시 원본 앞 N(저번호=대개 메인보드 대형주) 폴백."""
    try:
        from bot.naver_ranking_client import world_stock_map
        wsm = world_stock_map(market)
    except Exception:
        wsm = {}
    if not wsm:
        return full[:cap]
    mc: dict = {}
    for k, v in wsm.items():
        c = str(k).split(".")[0].upper()
        if not c:
            continue
        cap_v = (v or {}).get("mcap") or 0.0
        mc[c] = cap_v                       # 원 코드(영숫자 285A 키옥시아 포함)
        if c.isdigit():
            mc[str(int(c))] = cap_v         # 선행0 정규화 별칭(JP 4자리·HK pad)

    def _rank(t: str) -> float:
        # 영숫자 코드(285A)도 시총으로 랭크 — 옛 isdigit-only 는 신규 대형주를 0
        # (최하위)로 떨궈 캡 적용 시 탈락시켰음(사용자 2026-06-19).
        c = str(t).split(".")[0].upper()
        key = str(int(c)) if c.isdigit() else c
        return -(mc.get(key) or mc.get(c) or 0.0)
    return sorted(full, key=_rank)[:cap]


def _cap_by_liquidity_hk(full: list[str], cap: int) -> list[str]:
    """HK 캡 — _cap_by_liquidity(market='HK') 하위호환 별칭."""
    return _cap_by_liquidity(full, cap, "HK")


def _universe(market: str) -> tuple[list[str], dict]:
    """peer 맵의 unique 티커(주요종목). names 는 ticker 기본(맵에 명칭 없음)."""
    cfg = _CFG.get(market)
    if not cfg:
        return [], {}
    # JP/HK: 공식 상장목록 **전종목**(사용자 2026-06-13 full-market) — 실패 시 아래
    # peer 폴백. ⚠️ 2026-07-29 `fe766e3`(회귀 스위트 안정화 커밋)가 이걸 환경변수
    # opt-in 으로 바꿔 기본값이 peer(JP 102·HK 51종목)로 **조용히 축소**됐었다.
    # 그 결과 "일본 52주 신고가 1종목" 처럼 시장 전체가 아니라 큐레이션 대형주
    # 100개 안에서만 잡혀 보드가 사실상 비어 보였다(사용자 2026-08-01 지적).
    # TW(전종목 ~2000)·US(전 상장)와 커버리지가 어긋나던 것도 이것 때문.
    # 이제 기본 ON, 환경변수는 **opt-out**(HIGHLOW_USE_FULL_UNIVERSE=0)으로만.
    if market in ("JP", "HK") and os.getenv("HIGHLOW_USE_FULL_UNIVERSE", "1") != "0":
        try:
            from bot.intl_universe import full_universe
            full = full_universe(market)
            if len(full) > 100:
                # ⚠️ 네이버 worldstock 합집합 가드는 철회(2026-06-19 probe) — 부작용이
                # 이득을 초과: JP 는 네이버가 REIT(2971/2989/3226 등 — JPX div 로 정확히
                # 제외한 것)를 재편입해 보드 오염, HK 는 코드형식 불일치(네이버 '00001.HK'
                # 5자리 ↔ 공식 '0001.HK' 4자리)로 dedup 실패·유니버스 2배(5543)+유령
                # '00000.HK'. 공식 상장목록(JPX/HKEX) 파싱이 **이미 완전**(키옥시아 285A
                # 도 #546 영숫자 fix 로 공식 유니버스 3733 에 포함) → 공식 소스 단독이
                # 정답. '신규종목 누락 방지'는 _jp_pick 의 일반 규칙(4자 영숫자, 형식 무관)
                # + 라이브 공식목록 + 벌크 누락 재시도(_compute_highlow_from)로 보장.
                _cap = int(os.getenv("HIGHLOW_UNIVERSE_CAP", "5000"))
                if len(full) > _cap:
                    full = _cap_by_liquidity(full, _cap, market)
                return full, {t: t for t in full}
        except Exception as exc:
            log.warning("intl full_universe %s: %s", market, exc)
    if market == "CN_A":
        # CN_A = A? ???. ??? AKShare ??? ??? ?? ????,
        # ?? ? CSI300+500, ????? peer ??.
        try:
            from bot.akshare_client import list_cn_a_universe, list_csi300_500
            cn = list_cn_a_universe()
            if len(cn) > 100:
                full = list(cn.keys())
                _cap = int(os.getenv("HIGHLOW_UNIVERSE_CAP", "5000"))
                if len(full) > _cap:
                    full = _cap_by_liquidity(full, _cap, "CN_A")
                return full, {t: cn.get(t, t) for t in full}
            csi = list_csi300_500()
            if len(csi) > 100:
                full = list(csi.keys())
                _cap = int(os.getenv("HIGHLOW_UNIVERSE_CAP", "5000"))
                if len(full) > _cap:
                    full = _cap_by_liquidity(full, _cap, "CN_A")
                return full, {t: csi.get(t, t) for t in full}
        except Exception as exc:
            log.warning("intl CN A-share universe (AKShare): %s", exc)
        # AKShare ??/?? ? ?? peer ???? ??(?? 0)
    try:
        from bot import market as mkt
        peers = getattr(mkt, cfg[0], {}) or {}
    except Exception as exc:
        log.warning("intl highlow universe error (%s): %s", market, exc)
        return [], {}
    suffix = cfg[4] if len(cfg) > 4 else None   # native 시장 접미사(해외 비교군 제외)
    seen, uni, names = set(), [], {}
    for vals in peers.values():
        for x in (vals if isinstance(vals, (list, tuple)) else [vals]):
            t = str(x[0] if isinstance(x, (list, tuple)) else x).strip()
            if not t or t in seen:
                continue
            if suffix and not t.endswith(suffix):
                continue
            seen.add(t)
            uni.append(t)
            names[t] = t
    return uni, names


def _status_write(market: str, state: str, **kw) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_CFG[market][2], {"state": state, "ts": time.time(), **kw})
    except Exception:
        pass


def intl_highlow_status(market: str) -> dict:
    try:
        from bot.finviz_client import _cached
        return _cached(_CFG[market][2], ttl=86400) or {}
    except Exception:
        return {}


def _kr_full_universe() -> tuple[list[str], dict]:
    """KR 전종목(KOSPI+KOSDAQ 주식) 코드+한글명 — pykrx(get_market_price_change
    가 종목명 컬럼·시장당 1콜). ETF 미포함(주식만), SPAC(스팩) 제외. 7d 디스크
    캐시. creds/pykrx 부재 시 ([],{}) → 호출부 폴백. (사용자 2026-06-13 — KIS
    near-highlow 캡 ~30이 ETF/SPAC에 잠식돼 실종목 1~3개뿐 → 전종목 스캔으로)."""
    from bot.finviz_client import _cached, _cache_write
    cache_name = "kr_full_universe.json"
    c = _cached(cache_name, ttl=7 * 86400)
    if isinstance(c, list) and len(c) > 500:
        return [x[0] for x in c], {x[0]: x[1] for x in c}
    try:
        from bot.pykrx_client import _quiet_pykrx_logging, krx_login_ready
        if not krx_login_ready():
            return [], {}
        _quiet_pykrx_logging()
        from datetime import datetime, timedelta
        from pykrx import stock as _pk
        d = datetime.now()
        ds, ds_prev = d.strftime("%Y%m%d"), (d - timedelta(days=10)).strftime("%Y%m%d")
        pairs: list = []
        for mkt, suf in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
            df = _pk.get_market_price_change(ds_prev, ds, market=mkt)
            if df is None or "종목명" not in df:
                continue
            for code in df.index:
                nm = str(df.loc[code, "종목명"]).strip()
                if not nm or "스팩" in nm:        # SPAC 제외
                    continue
                pairs.append([f"{code}{suf}", nm])
        if len(pairs) > 500:
            _cache_write(cache_name, pairs)
            return [p[0] for p in pairs], {p[0]: p[1] for p in pairs}
    except Exception as exc:
        log.warning("kr full universe (pykrx): %s", exc)
    return [], {}


def _compute_kr_full() -> None:
    """KR 52주 신고저 — **네이버 우선**(전종목·한글명·1콜씩, 사용자 2026-06-13
    '신고가 신저가는 네이버에 있어'). 네이버 front-api 가 ready 52주 최고/최저를
    주므로 pykrx+yfinance 전종목 스캔(수 분) 불필요. 네이버 실패 시 pykrx 스캔
    폴백(회귀 0). 한글명 둘 다 native(번역 불요)."""
    from bot.finviz_client import _cache_write, _compute_highlow_from
    # 1) 네이버 — 빠름(1콜씩)·한글명·ETF/ETN/스팩 제외
    try:
        from bot.naver_ranking_client import fetch_kr_highlow
        nv = fetch_kr_highlow()
        if nv.get("high") or nv.get("low"):
            _cache_write(_CFG["KR"][1], nv)
            _status_write("KR", "done", high=len(nv["high"]),
                          low=len(nv["low"]), src="naver")
            return
    except Exception as exc:
        log.warning("naver KR 52w 실패 → pykrx 폴백: %s", exc)
    # 2) 폴백: pykrx 전종목 → yfinance 당일 52주 갱신 스캔
    uni, names = _kr_full_universe()
    if not uni:                                   # pykrx 부재 → peer-83 폴백
        uni, names = _universe("KR")
        label = "한국 주요종목(yfinance · pykrx 폴백)"
        src = "peer"
    else:
        label = "한국 전종목(pykrx 목록·yfinance 당일 52주 신고가/신저가 갱신)"
        src = "krx-full"
    if not uni:
        _status_write("KR", "failed", detail="pykrx + peer empty")
        return
    _status_write("KR", "running", total=len(uni))
    out = _compute_highlow_from(uni, names, _CFG["KR"][1], label, "KR")
    _status_write("KR", "done", high=len(out.get("high", [])),
                  low=len(out.get("low", [])), src=src)


def _compute_kr_kis() -> None:
    """(레거시 — _compute_kr_full 로 대체, 2026-06-13) KR 52주 신고저 = KIS
    near-new-highlow + yfinance mcap/ind 백필. KIS 캡 ~30이 ETF/SPAC에 잠식돼
    실종목이 1~3개뿐이라 전종목 스캔(_compute_kr_full)으로 교체. 보존(미사용)."""
    from bot import kis_client
    from bot.finviz_client import (_cache_write, _compute_highlow_from,
                                   _fetch_industries, _fetch_mcaps, _now_label)
    raw = kis_client.fetch_kr_new_highlow()
    if not raw.get("high") and not raw.get("low"):
        # KIS 빈 결과(creds 부재 등) → 기존 peer-83 yfinance 스캔 폴백
        uni, names = _universe("KR")
        if uni:
            _status_write("KR", "running", total=len(uni))
            out = _compute_highlow_from(
                uni, names, _CFG["KR"][1],
                "한국 주요종목 산출(yfinance · KIS 폴백)", "KR")
            _status_write("KR", "done", high=len(out.get("high", [])),
                          low=len(out.get("low", [])), src="peer")
        else:
            _status_write("KR", "failed", detail="KIS empty + peer empty")
        return
    try:
        from bot.market import normalize_kr_ticker_suffix as _norm
    except Exception:
        _norm = None
    # ETF/ETN 견고 제외 — pykrx 주식 코드셋(get_market_ticker_list = 주식만,
    # ETF/ETN 미포함)과 교차. WON/KoAct 등 브랜드 키워드 누락분까지 제거.
    # SPAC 은 주식이라 셋에 있음 → KIS _nhl_is_etf_bond '스팩' 필터가 별도 제거.
    # creds/pykrx 부재로 셋이 비면 교차 생략(키워드 필터에 의존, no-op).
    _codes: set = set()
    try:
        from bot.market import _kr_market_code_sets
        _ks, _kq = _kr_market_code_sets()
        _codes = _ks | _kq
    except Exception:
        _codes = set()
    if _codes:
        raw["high"] = [o for o in raw["high"] if str(o.get("code") or "") in _codes]
        raw["low"] = [o for o in raw["low"] if str(o.get("code") or "") in _codes]

    def _conv(lst):
        items = []
        for o in lst:
            code = str(o.get("code") or "")
            tk = f"{code}.KS"
            if _norm:
                try:
                    tk = _norm(tk)
                except Exception:
                    pass
            items.append({"ticker": tk, "name": o.get("name") or tk,
                          "price": o.get("price"), "pct": o.get("pct"),
                          "vol": o.get("vol")})
        return items
    high, low = _conv(raw["high"]), _conv(raw["low"])
    allt = [it["ticker"] for it in high + low]
    try:
        mcaps, inds = _fetch_mcaps(allt), _fetch_industries(allt)
        for it in high + low:
            mc = mcaps.get(it["ticker"])
            it["mcap"] = round(mc / 1e8, 2) if mc else None   # 억원
            it["ind"] = inds.get(it["ticker"])
    except Exception as exc:
        log.warning("intl highlow KR mcap/ind 백필: %s", exc)
    out = {"high": high, "low": low, "ts": _now_label(),
           "source": "KIS 신고가/신저가 근접(전 시장 스캔)"}
    _cache_write(_CFG["KR"][1], out)
    _status_write("KR", "done", high=len(high), low=len(low), src="kis")


def _compute(market: str) -> None:
    try:
        if market == "KR":
            _compute_kr_full()
            return
        from bot.finviz_client import _compute_highlow_from
        uni, names = _universe(market)
        if not uni:
            _status_write(market, "failed", detail="universe empty")
            return
        _status_write(market, "running", total=len(uni))
        # 라벨은 **실제로 스캔한 유니버스**를 반영해야 한다 — 고정 문자열
        # ("일본 주요종목")은 전종목으로 확대된 뒤에도 그대로라 화면이 거짓말을
        # 하게 되고, 반대로 공식 상장목록 fetch 가 실패해 peer 로 폴백해도 그걸
        # 알 수 없다. 종목 수를 함께 찍어 커버리지를 한눈에 확인 가능하게 한다
        # (사용자 2026-08-01 '제대로 잡아내고 있는지').
        label = _CFG[market][3]
        if market in ("JP", "HK") and len(uni) > 500:
            label = {"JP": "일본 전종목(JPX 상장)", "HK": "홍콩 전종목(HKEX 상장)"}[market]
        out = _compute_highlow_from(
            uni, names, _CFG[market][1],
            f"{label} {len(uni):,}종목 산출(yfinance · 당일 52주 고저 갱신)", market)
        _status_write(market, "done", high=len(out.get("high", [])),
                      low=len(out.get("low", [])))
    except Exception as exc:
        log.warning("intl highlow compute (%s): %s", market, exc)
        _status_write(market, "failed", detail=str(exc)[:80])
    finally:
        with _lock:
            _running[market] = False


def _kick(market: str) -> None:
    with _lock:
        if _running.get(market):
            return
        _running[market] = True
    threading.Thread(target=_compute, args=(market,), daemon=True,
                     name=f"intl-highlow-{market}").start()


# 네이버 worldstock 으로 live 52주 비교 가능한 시장(TW=TWSE 별도·KR=네이버 domestic 별도).
_LIVE_MARKETS = ("JP", "CN_A", "HK")


def _market_open(market: str) -> bool:
    """장중(평일·세션 시간 내) 여부 — _SESSIONS_UTC(UTC 세션) 기준."""
    from datetime import datetime, timezone
    from bot.finviz_client import _SESSIONS_UTC
    s = _SESSIONS_UTC.get(market)
    if not s:
        return False
    oh, om, ch, cm = s
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and (oh, om) <= (now.hour, now.minute) < (ch, cm)


def _load_baseline(market: str) -> dict:
    from bot.finviz_client import _cached
    b = _cached(f"highlow_baseline_{market}.json", ttl=7 * 86400)
    return b if isinstance(b, dict) else {}


def fetch_intl_highlow_live(market: str) -> dict | None:
    """EOD baseline(오늘 제외 52주 고저, yfinance 스캔이 저장) × 네이버 현재가 → live
    52주 신고가/신저가. JP/CN_A/HK 만(네이버 worldstock). baseline/현재가 없으면 None
    → 호출부가 기존 스캔 캐시로 폴백. 10분 캐시(장 밖 freeze). 정확성: 동일 baseline
    공식, 비교만 yfinance 오늘바 → 네이버 현재가('현재 신고가'; 장중 찍고 내려온 건 제외)."""
    if market not in _LIVE_MARKETS:
        return None
    from bot.finviz_client import (_CACHE_DIR, _cache_write, _cached,
                                   _industries_for, _now_label, _session_fresh)
    cache = f"highlow_live_{market}.json"
    c = _cached(cache, ttl=86400)
    if isinstance(c, dict) and c:
        try:
            mt = (_CACHE_DIR / cache).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh(market, mt, 600):       # 장중 10분 / 장 밖 freeze
            return c
    base = _load_baseline(market)
    if not base:
        return None
    try:
        from bot.naver_ranking_client import world_live_map
        live = world_live_map(market)
    except Exception as exc:
        log.warning("intl highlow live %s: naver %s", market, exc)
        live = {}
    if not live:
        return None
    high, low = [], []
    for tk, b in base.items():
        q = live.get(tk)
        if not q or q.get("price") is None:
            continue
        # HK RMB 이중상장 카운터(네이버 '… (CNY)') 제외 — HKD 주카운터의 위안화
        # 중복 상장이라 초저유동(거래 수백 주)이고, HKD baseline 과 통화가 달라
        # 비교 자체가 무의미해 엉뚱한 신저가로 샌다(사용자 2026-06-29: 82333.HK
        # 장성자동차(CNY) 단독 신저가 오탐). 데이터소스(Naver) 라벨 기반 정확 제외.
        if "(CNY)" in (q.get("name") or ""):
            continue
        try:
            price = float(q["price"])
        except (TypeError, ValueError):
            continue
        h52, l52, vol = b.get("h52"), b.get("l52"), q.get("vol")
        rec = {"ticker": tk, "name": q.get("name") or b.get("name") or tk,
               "price": round(price, 4), "pct": q.get("pct"), "vol": vol,
               "value": (round(price * vol / 1e8, 2) if vol else None),
               "mcap": q.get("mcap"), "ind": None}
        if h52 is not None and price >= float(h52):
            high.append(rec)
        elif l52 is not None and price <= float(l52):
            low.append(rec)
    if not high and not low:
        return None
    # 2026-08-03 fix — 업종이 항상 None 이라 신고가/신저가 표에서 '업종' 컬럼과
    # '업종 분포' 요약이 빠졌었다(사용자 스크린샷, 급등락 페이지엔 정상 표시).
    # 정적 스캔 경로(finviz_client._compute_highlow_from)는 같은 헬퍼로 업종을
    # 채우는데 이 live 병합 경로만 빠져 있었음. **hi/lo 확정 후에만 조회**
    # (2026-08-03 code-review 발견: 최초 구현이 base.keys() 전체 — JP/HK 전종목
    # 수천 개 — 를 조회해 정적 경로가 hits2(당일 실제 신고/신저 히트만)로 좁히던
    # 것과 달리 페이지 요청 경로에서 불필요한 (HK 는 yfinance .info 폴백까지
    # 걸리는) 블로킹 비용을 냈다. 실제 결과에 필요한 티커만 조회하도록 스코프 축소).
    hit_tickers = [r["ticker"] for r in high + low]
    inds = _industries_for(hit_tickers, market)
    for r in high + low:
        r["ind"] = inds.get(r["ticker"])
    high.sort(key=lambda r: (r.get("mcap") or 0), reverse=True)
    low.sort(key=lambda r: (r.get("mcap") or 0), reverse=True)
    out = {"high": high, "low": low, "ts": _now_label(),
           "source": f"{_CFG[market][3]} — 네이버 현재가 × 52주 baseline(live)"}
    _cache_write(cache, out)
    return out


def fetch_intl_highlow(market: str) -> dict:
    """JP/CN_A/HK/KR 52주 신고가/신저가 — **동기 계산 안 함**. 시장-인지 신선도
    (KR=장중 30초[네이버] / JP·HK=장중 1h[yfinance 스캔] / 장 밖 마지막 마감 이후
    재스캔 0) 즉시 / 스테일+백그라운드 킥 / 캐시부재 building. 실패 5분 백오프·
    진행중 30분 dedup. {high,low,ts,source,building,status}."""
    if market not in _CFG:
        return {"high": [], "low": [], "ts": "", "source": "", "building": False}
    # JP/CN_A/HK — 장중엔 네이버 현재가 × EOD baseline live 비교(10분, 저부하·실시간).
    # baseline 부재/네이버 실패/빈결과/킬스위치(HIGHLOW_LIVE=0) 시 아래 기존 스캔 캐시로
    # 자동 폴백(회귀 0). 장 밖엔 기존 경로(마지막 마감본 freeze).
    import os as _os
    if (market in _LIVE_MARKETS and _os.getenv("HIGHLOW_LIVE", "1") != "0"
            and _market_open(market)):
        try:
            _lv = fetch_intl_highlow_live(market)
        except Exception as _lexc:
            log.warning("intl highlow live %s dispatch: %s", market, _lexc)
            _lv = None
        if _lv and (_lv.get("high") or _lv.get("low")):
            return _lv
    from bot.finviz_client import (_CACHE_DIR, _HL_INTRA_TTL, _MOVERS_INTRA_TTL,
                                   _cached, _session_fresh)
    cache = _CFG[market][1]
    # 시장-인지 신선도: KR=네이버(fetch_kr_highlow 1콜씩, 야후 무관) → **장중 30초**
    # (사용자 2026-06-15 '한국 신고저 네이버니 실시간'). JP/CN/HK=yfinance 유니버스
    # 스캔(heavy·수 분) → 장중 1h 유지. 장 밖 마지막 마감 이후 재스캔 0(전 시장 공통).
    _intra = _MOVERS_INTRA_TTL if market == "KR" else _HL_INTRA_TTL
    stale = _cached(cache, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / cache).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh(market, mt, _intra):
            return stale
    st = intl_highlow_status(market)
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        from bot.finviz_client import yf_paused
        if not yf_paused():        # YF_PAUSE → 재산출 kick 안 함(스테일 유지)
            _kick(market)
    if stale is not None:
        return {**stale, "building": st.get("state") == "running"}
    return {"high": [], "low": [], "ts": "", "source": "",
            "building": True, "status": st}

