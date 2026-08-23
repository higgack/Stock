"""분기 실적분석 인포그래픽 — 분기 스코어카드 PNG (전 시장).

사용자 2026-08-19 (X @Jaymin_Alpha 스타일). 종목 상세 '분기실적' 탭에서
온디맨드 생성. 구성(위→아래):
  헤더(종목명·티커·마켓·통화·시총·최신분기) / 헤드라인 콜아웃(LLM, 없으면
  생략) / 지표 타일 5종(매출·영업이익·영업이익률·당기순이익·TTM PER,
  YoY·QoQ) / **세로 2단 차트**(각 전체폭: 위=금액 막대, 아래=이익률 % —
  분리된 축이라 이중 Y축 오독이 없다) / 성장동력·리스크 카드(LLM, 없으면
  섹션 생략) / TTM 푸터·각주·출처·면책.

데이터 소스(2026-08-16 멀티마켓 확장):
  KR      = bot/dart_quarterly.get_quarterly_series (DART 단일분기 원천)
  그 외    = bot/quarterly_series.series_from_yfinance (스냅샷에 이미 전
            시장 공통으로 수집된 yfinance 분기 손익 — 추가 호출 0)
숫자는 전부 실측값을 그대로 주입 — 렌더러가 값을 만들지 않는다(환각 0).
이상치(매출 음수·보고서 간 계정 불일치)는 보정하지 않고 해당 항목의 TTM·
PSR 산출에서 제외한 뒤 각주로 이유를 밝힌다. 정성 카드만 LLM
(bot/dart_growth_risk)이고 DART 원문 전용이라 **KR 에서만** 제공된다.

캐시: 파일명 자체가 캐시 키({티커}_{분기키}_{날짜}.png). 분기키는 KR 이
{연도}{reprt_code}, 그 외는 분기 종료일 — 새 분기가 나오면 파일명이 바뀌어
시간 TTL 없이 자동 갱신된다. 날짜를 넣는 이유는 cache_path 주석 참조.

폰트: NanumGothic 부재 시 None 반환(daily_byte_infographic 과 동일 규약)
→ 호출부가 표(HTML)만 보여준다.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger("bot.quarterly_infographic")

# pyplot 은 전역 상태(rcParams·figure 스택)라 동시 렌더가 서로를 오염시킬 수
# 있다. 대시보드는 ThreadingHTTPServer 라 동시 요청이 실제로 가능 → 직렬화.
_RENDER_LOCK = threading.Lock()

# ⚠️ 락 **대기**와 실제 **그리기**를 나눠 재야 한다(2026-08-22 `render_png=
# 39.15s`): pyplot 전역 상태 때문에 렌더가 프로세스 전체에서 직렬화되므로,
# 다른 종목이 그리는 동안 여기서 **기다리기만** 할 수 있다. 둘을 합쳐 재면
# "그리기가 느리다"로 오진하고 엉뚱한 곳을 고친다(#92 '느리다는 진단이 아니다').
import time as _rt_time


def _mark_png(tkey: str, stage: str, part: str, sec: float) -> None:
    if tkey and stage:
        _RENDER_TIMING.set(tkey, f"png.{stage}.{part}", sec)


# 팔레트 — daily_byte/realestate 인포그래픽과 동일 톤(대시보드 일관성).
_BG = "#070a14"; _PANEL = "#131a2e"; _PANEL2 = "#1a2238"; _LINE = "#2a3656"
_TEXT = "#e8ecf6"; _MUTED = "#93a0bd"; _ACCENT = "#4da3ff"; _ACCENTW = "#22d3ee"
_POS = "#34d399"; _NEG = "#f87171"; _GOLD = "#fbbf24"; _PUR = "#a78bfa"

_IMG_DIR = Path.home() / ".tradingagents" / "archive" / "quarterly_infographic_img"

# 렌더 버전 — 파일명에 들어가 **레이아웃/표기 변경 시 캐시를 무효화**한다.
# 캐시 키가 (티커, 분기, 날짜)뿐이면 오늘 이미 본 종목은 배포 후에도 옛
# 그림이 그대로 뜬다("배포완료 ≠ 화면에 보임", 실수 #11). 렌더러의 출력이
# 달라지는 변경을 하면 이 값을 **반드시** 올릴 것.
#   v2 (2026-08-16) 타일 2줄 YoY/QoQ · Forward PER · 막대 값 라벨 · 축 확대
#   v3 (2026-08-16) 성장동력·리스크 카드 상자를 항목 수에 맞춰 가변 높이로
#       (4번 항목이 상자 밖으로 넘치던 것) + 항목 상한 4→6
#   v4 (2026-08-16) 수주잔고·재고자산 막대차트(데이터 있는 것만) + 시총·PER·
#       PSR 라이브화(캐시 버킷 일→시)
#   v5 (2026-08-19) 구성요소 매출(증권·은행·보험)을 FnGuide 총액으로 보강 +
#       못 채우면 차트 범례·제목도 실제 계정명(이자수익)으로 — 옛 그림은
#       "매출 5,787억 < 영업이익 6,812억" 으로 읽혔다
#   v6 (2026-08-19) 손익 계정을 손익계산서에서만 채택 — 자본변동표(SCE)의
#       **누적** 당기순이익이 이겨 2·3분기 순이익이 부풀어 있었다
#   v7 (2026-08-19) ROE·ROA 를 TTM(최근 4분기 합) 기준으로 — 분기 하나로
#       계산해 네이버(10.08%)와 3배 어긋나 보였다
_RENDER_VER = "v11"  # v11: 헤더에 현재가 추가

# 세로 섹션 이름 — 조각을 나누는 단위. 그리는 코드는 `_render_locked` 한
# 곳뿐이고, 어느 섹션을 담을지만 골라 두 번 부른다. `combo()` 가 fig/ax 를
# 잡는 155줄짜리 클로저라 조각마다 함수를 복제하면 반드시 갈라진다(#38).
_SECTIONS = ("head", "call", "tiles", "charts", "extra", "foot")
# 사용자 2026-08-21 배치: [지표·차트] → 제품 표 → 가동률 표 →
# [수주잔고·재고자산] → 성장동력 카드 → 출처·면책(HTML).
# ⚠️ TTM 타일·각주(`foot`)는 **상단** 조각이다(사용자 2026-08-21 "이 첫번째
# TTM 매출부분은 당기순이익 밑으로 가야돼"). `_render_locked` 는 섹션을
# 코드 순서(head→call→tiles→charts→extra→foot)로 그리므로, 상단에서 extra 만
# 빠지면 TTM 이 당기순이익 차트 바로 아래에 온다.
_PART_TOP = ("head", "call", "tiles", "charts", "foot")
_PART_BOTTOM = ("extra",)
# 본 이미지 파일명에 붙는 조각 접미사 — purge 가 이 목록에서 보존 대상을
# 만든다(#24: 이름 열거 금지).
_PIECE_SUFFIXES = ("_b", "_cards")


def _eok(v, currency: str = "KRW") -> str:
    """금액 표기 — 통화 인지(조/억 · 兆/億 · T/B/M). None 이면 '—'.

    옛 구현은 `/1e8` → 억/조 로 **KRW 를 물리적으로 가정**했다. 멀티마켓
    확장(사용자 2026-08-16)에서 달러 종목이 '억' 으로 나오는 것을 막기 위해
    `quarterly_series.fmt_money`(dashboard._fmt_mcap 단위 규약 재사용)에
    위임한다. 기본값 KRW 라 기존 호출부는 동작 불변."""
    from bot.quarterly_series import fmt_money
    return fmt_money(v, currency)


def _fmt_price(v, currency: str = "KRW") -> str:
    """주가 표기 — 포맷 규약은 `quarterly_series.fmt_price` 하나가 정본이다."""
    from bot.quarterly_series import fmt_price
    return fmt_price(v, currency)


def _pct(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _chg(now, prev) -> float | None:
    """증감률(%). 분모가 0/None 이거나 부호가 뒤집히면(적자→흑자 등) None
    — 그런 구간의 '%'는 의미가 없어 배지를 비운다(억지 숫자 금지)."""
    if now is None or prev is None or prev == 0:
        return None
    if prev < 0:
        return None
    return (now / prev - 1) * 100


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", (ticker or "").upper())


def _today_kst() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _now_hour_kst() -> str:
    """캐시 버킷 = KST 날짜+시(YYYY-MM-DD_HH). 시세 기반 값의 갱신 주기."""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d_%H")


_RENDER_SIG: str | None = None


def _render_sig() -> str:
    """이 모듈 소스의 지문 — 그림·문구를 고치면 캐시가 **자동으로** 무효.

    ⚠️ `_RENDER_VER` 는 손으로 올리는 리터럴이라 **또 잊었다**(2026-08-22
    LG화학: 각주에 계정쌍을 싣도록 고쳤는데 화면은 옛 PNG 그대로였다).
    같은 실패가 이 레포에서 네 번째다 — #18 아카이브 · #21b 파싱 캐시 ·
    #95 재무 캐시(v4·v5 를 주석에 적어 두고도 v6 을 잊었다) · 여기.
    규율로 기억할 일을 **구조로** 옮긴다(#1039 파서 지문과 같은 처방).
    재렌더 비용은 matplotlib 1회(₩0)이고 LLM 은 rcept_no 캐시라 재과금 없다.
    """
    global _RENDER_SIG
    if _RENDER_SIG is None:
        try:
            import hashlib
            import pathlib
            _RENDER_SIG = "-" + hashlib.sha1(
                pathlib.Path(__file__).read_bytes()).hexdigest()[:8]
        except Exception:                                      # noqa: BLE001
            _RENDER_SIG = ""       # 지문을 못 구하면 옛 규약대로(버전만)
    return _RENDER_SIG


def cache_path(ticker: str, period_key, reprt_code=None,
               asof: str | None = None) -> Path:
    """캐시 파일 경로 = 캐시 키. 새 분기면 파일명이 달라져 자동 재렌더.

    ⚠️ 파일명에 **KST 날짜+시**를 포함한다 — 이미지에 시가총액·TTM PER·PSR
    같은 **시세 기반 값**이 구워지는데, 분기 키만 쓰면 다음 분기까지(최대
    3개월) 그 값이 얼어붙는다(2026-08-19 code-review). 하루 1회로는 아침에
    한 번 그린 값이 종일 남아 장중에 보면 어긋난다(사용자 2026-08-16
    "스냅샷이 되면 안돼") → 시 단위 버킷으로 바꿨다. 재렌더는 matplotlib
    1회(₩0)이고 LLM 은 rcept_no 캐시라 재과금 없다.

    `period_key` 는 분기 식별자다. KR 은 (연도, 보고서코드) 2인자 형태를
    유지하고(하위호환), 비-KR 은 분기 종료일 문자열 하나를 준다 —
    reprt_code 가 DART 전용 개념이라 멀티마켓에선 쓸 수 없다."""
    key = f"{period_key}{reprt_code}" if reprt_code is not None else str(period_key)
    return (_IMG_DIR /
            f"{_safe_name(ticker)}_{key}_{asof or _now_hour_kst()}"
            f"_{_RENDER_VER}{_render_sig()}.png")


def _font_ok() -> bool:
    from bot.daily_byte_infographic import _font_ready, _setup_font
    return bool(_font_ready() and _setup_font())


def render_infographic(payload: dict, out_path: str,
                       sections: tuple = _SECTIONS, stage: str = "",
                       tkey: str = "") -> str | None:
    """payload → PNG. 성공 시 out_path, 실패(폰트 부재·오류) 시 None.

    ⚠️ 전 구간을 try 로 감싼다 — 그리기 단계 예외가 새면 호출부(API 핸들러)가
    500 을 내며 무료 DART 표 폴백조차 못 보여준다(2026-08-19 code-review).
    pyplot 전역 상태 + rcParams 를 만지므로 **렌더 락**으로 직렬화한다
    (ThreadingHTTPServer 에서 동시 렌더 시 figure 교차오염 방지).

    payload = {ticker, company, market, market_cap, currency, quarters[],
               ttm{}, per, psr, growth_risk{}}
    quarters = 오래된→최신 순 [{label, financials{}, ratios{}}...]"""
    _t_wait = _rt_time.time()
    with _RENDER_LOCK:
        _mark_png(tkey, stage, "wait", _rt_time.time() - _t_wait)
        _t_draw = _rt_time.time()
        try:
            return _render_locked(payload, out_path, sections)
        except Exception as exc:
            log.warning("quarterly_infographic: render failed: %s", exc)
            try:
                import matplotlib.pyplot as plt
                plt.close("all")      # 예외 시 figure 누수 방지
            except Exception:
                pass
            return None
        finally:
            _mark_png(tkey, stage, "draw", _rt_time.time() - _t_draw)


# 차트 눈금 파라미터 — 함수 안에 박아두면 테스트가 소스 grep 밖에 못 한다.
# ⚠️ steps 에서 **2.5 를 뺐다**: 0.25 간격이 잡히면 dec=1 포맷이 0.2·0.5·0.8
# 로 반올림해 눈금이 불규칙해 보인다(렌더 실측). 1/2/5 계열만 쓴다.
# nbins 은 최대 구간 수라 작으면 한 단계 굵은 간격으로 떨어진다 — 12 로는
# 0~2.5 에서 0.2(12.5구간)가 탈락해 0.5 간격 5칸이 됐다(옛 7보다 성겼다).
# 카드 항목 한 줄에 들어가는 글자 수 — 카드 폭 실측치(가용 812px, 8pt 기준
# 34자 782px · 38자 874px 넘침). LLM 프롬프트는 dart_growth_risk.ITEM_CHARS
# 로 이보다 짧게 요구해 말줄임이 예외가 되게 한다.
# 34 → 31: 도화지 9.8in 에서 카드 한 줄의 실측 한계(칸 폭 40.9 단위).
# ⚠️ 도화지를 좁히면 같은 글자수가 더 넓어진다 — 이 둘은 **한 쌍**이라
# 한쪽만 바꾸면 항목이 카드 밖으로 나간다(회귀가 픽셀로 잡는다).
# 카드 한 줄에 넣을 글자수. ⚠️ 이제 **절단폭이 아니라 줄바꿈 폭**이다 —
# 넘치면 자르지 않고 둘째 줄로 넘긴다(2026-08-21). 옛 절단은 LLM 이 쓴
# 근거를 화면에서 잘라 없앴다("…"), 그게 정보 손실이었다.
# ⚠️ 값의 근거: VM 실측(소스 주석에 남아 있던 것) 8.5pt **한글** 30자 =
# 43.6 데이터 단위 → 전각 1자 = 1.453. 카드 안쪽 가용폭은
# 46.5 − 5.0(번호) − 1.6(우여백) = 39.9 이므로 한 줄은 39.9/1.453 ≈ 27.5 폭.
# ⚠️ **글자수가 아니라 폭**으로 잰다(사용자 2026-08-21 "공간이 있는데 왜
# 줄 바꿈을 해서 작성한거야?"). 라틴은 전각의 절반쯤이라 글자수로 재면
# `주요 판매 제품군, IT·Mobile·Auto 분야 집중`(폭 21.9)이 24자 한도에
# 걸려 **자리가 남는데도** 둘째 줄로 넘어갔다. 한도를 24→27.5 로 올리는
# 것만으론 라틴 섞인 항목이 여전히 일찍 접힌다 — 단위를 바꿔야 한다.
_CARD_LINE_W = 27.5
_CARD_MAX_LINES = 2
# 절단은 **최후 수단**(두 줄로도 안 들어가는 비정상 입력)만.
# 단위가 폭이므로 이것도 '전각 환산 글자수'다(LLM 지시 ITEM_CHARS 비교용).
_CARD_CHARS = int(_CARD_LINE_W * _CARD_MAX_LINES)

_TICK_STEPS = [1, 2, 5, 10]
# 상한 — 실제 nbins 는 **축의 픽셀 높이**에서 계산한다(_nbins_for).
# 고정 상수는 두 방향으로 틀린다: 크면 낮은 축(% 패널은 142px 뿐)에서
# 9pt 라벨이 겹치고, 작으면 큰 축에서 한 단계 굵은 간격으로 떨어져
# 오히려 성겨진다(둘 다 2026-08-16 렌더 실측). 픽셀 기준이면 레이아웃
# 상수를 바꿔도 자동으로 따라온다.
_AMT_NBINS, _PCT_NBINS = 16, 9
# 라벨당 최소 세로 공간(px). 9pt @180dpi 의 em 은 ≈22.5px 이고 숫자 글리프
# 자체는 그보다 낮아(≈16px) 23px 이면 눈에 보이는 간격이 남는다(렌더 실측).
# ⚠️ 이 값을 조금만 키워도 한 단계 굵은 눈금으로 **떨어진다** — 실측 기준
# 331px 축에서 23 → 14칸(0.2 간격), 25 → 13칸이라 0.2(13.1칸 필요)가
# 탈락해 0.5 간격 6개가 됐다. 바꿀 땐 반드시 렌더로 확인할 것.
_TICK_MIN_PX = 23.0


def _nbins_for(height_px: float, cap: int) -> int:
    """축 픽셀 높이에 들어가는 최대 눈금 수(라벨 겹침 없이). 상한은 cap."""
    return max(3, min(cap, int((height_px or 0) // _TICK_MIN_PX)))


def _label_decimals(scaled_peak: float, dec: int) -> int:
    """막대 값 라벨의 소수 자릿수. **축 눈금(dec)보다 촘촘해야 한다** —
    축 자릿수를 그대로 쓰면 2.15·2.24·2.31 이 전부 '2.1/2.2/2.3' 으로
    뭉개져 라벨을 붙인 목적(분기간 차이 식별)이 사라진다(렌더 실측).

    ⚠️ **시리즈별로** 호출할 것. 두 시리즈의 공통 peak 로 한 번만 정하면,
    스케일이 100배 작은 쪽(매출 2.31B 옆 영업이익 4.8M)이 전 분기 '0.00'
    으로 찍힌다(2026-08-16 독립 리뷰). 작은 값은 **유효숫자 2자리**가
    보이도록 자릿수를 늘린다."""
    import math
    p = abs(scaled_peak or 0)
    if p >= 100:
        d = 0
    elif p >= 10:
        d = 1
    elif p >= 1 or p == 0:
        d = 2
    else:
        # 0.0048 → 4자리("0.0048") = 유효숫자 2개. 6자리에서 끊는다.
        d = min(6, int(math.floor(-math.log10(p))) + 2)
    return max(dec, d)


# 분기실적 탭 추가 막대차트 — (financials 키, 화면 제목) 순서대로 그린다.
# 사용자 2026-08-16: "여기는 영업이익률이나 순이익률은 필요없고 막대그래프만".
# 시장 게이트가 아니라 **데이터 유무 게이트**다(값이 없으면 패널 자체가 생략)
# — 다른 시장·항목에 소스가 생기면 이 목록에 한 줄 추가하면 켜진다.
# ⚠️ **생산자가 있는 키만 올린다.** 올리는 순간 Help 에도 등록해야 하고,
# 생산자가 없으면 영원히 안 그려지는 죽은 항목이 된다(2026-08-16 독립 리뷰).
#   · 재고자산 = DART 재무제표 계정(`_DART_NAME_MAP`)
#   · 수주잔고 = 정기보고서 본문 파서(`bot/dart_backlog` → `_fill_backlog`)
#     2026-08-17 VM 프로브로 16종목 원문을 받아 3형식 파서를 짠 뒤 켰다.
_EXTRA_CHARTS = (("수주잔고", "수주잔고"), ("재고자산", "재고자산"))


# 분기 간 최대/최소 배수가 이 값 이상이면 **파싱 의심**(위 주석의 실측 근거).
_BACKLOG_SPREAD_MAX = 20.0
# 미공시 판정 전에 최신부터 몇 분기까지 시도하나. 2 = 최신 보고서 문서가
# 누락돼도 직전 분기로 회복. 미공시 회사의 대용량 다운로드는 최대 2회.
_BACKLOG_PROBE_N = 2


def _fill_backlog(dart, ticker: str, qs: list) -> None:
    """분기별 수주잔고를 `financials["수주잔고"]` 에 채운다(제자리 수정).

    재고자산과 달리 수주잔고는 재무제표 계정이 아니라 **정기보고서 본문**의
    「매출 및 수주상황」표에서 나온다 — 분기마다 원문(최대 40MB)을 받아야 한다.

    ⚠️ 그래서 **최신 분기를 먼저 본다.** 수주잔고는 의무 공시가 아니라 안 쓰는
    회사가 다수인데(프로브 16종목 중 2곳은 아예, 3곳은 형식 미지원), 전 분기를
    먼저 받으면 대부분의 종목에서 5회 대용량 다운로드가 통째로 버려진다.
    최신이 없으면 그 종목은 공시 안 하는 회사로 보고 즉시 중단한다.

    graceful: 실패·부재는 조용히 건너뛴다 — 값이 없으면 `_extra_series` 가
    패널 자체를 생략하므로 화면에 빈 축이나 0 막대가 남지 않는다."""
    if not dart or not qs:
        return
    try:
        from bot.dart_backlog import backlog_probe
    except Exception as exc:
        log.debug("quarterly_infographic: dart_backlog import: %s", exc)
        return
    # ⚠️ **최신 하나만 보고 포기하면 안 된다**(2026-08-21 사용자 "있는데
    # 누락시키고 싶지 않아"). DART 가 그 접수건 문서를 안 주는 경우가 실재하고
    # (한화에어로 `status=014` 실측), 그러면 나머지 4분기가 다 있어도 이 회사가
    # 통째로 '수주잔고 미공시'로 처리됐다. 생산능력·제품 표는 롤링(최신부터
    # 거슬러 첫 성공)인데 여기만 1회였다 — 같은 규율로 맞춘다.
    # 비용은 여전히 유계다: 미공시 회사는 최대 _BACKLOG_PROBE_N 번만 받는다.
    probed: dict[int, tuple] = {}
    for i in range(len(qs) - 1, max(-1, len(qs) - 1 - _BACKLOG_PROBE_N), -1):
        probed[i] = backlog_probe(dart, ticker, qs[i]["year"],
                                  qs[i]["reprt_code"])
        if probed[i][0] is not None:
            break
    if all(v is None for v, _w in probed.values()):
        return          # 이 회사는 수주잔고를 안 쓴다 — 과거분 조회 불필요
    # ⚠️ 나머지 분기는 서로 **독립**이다 — 직렬로 걸으면 분기당 최대 40MB
    # 다운로드+정규식이 그대로 더해진다(2026-08-22 실측 `bp.backlog=42.4s`).
    # `bot.pool` 은 **말단 팬아웃 전용**이고 여기서 도는 작업은 공용 풀에
    # 다시 제출하지 않으므로 규약을 지킨다(#110 교착 금지).
    rest = [i for i in range(len(qs)) if i not in probed]
    got: dict[int, tuple] = {}
    if rest:
        try:
            from bot.pool import map_bounded
            res = map_bounded(
                lambda i: backlog_probe(dart, ticker, qs[i]["year"],
                                        qs[i]["reprt_code"]), rest)
            got = {i: (r or (None, "조회실패"))
                   for i, r in zip(rest, res)}
        except Exception as exc:                               # noqa: BLE001
            log.debug("quarterly_infographic: 수주잔고 병렬 실패, 직렬로: %s",
                      exc)
            got = {i: backlog_probe(dart, ticker, qs[i]["year"],
                                    qs[i]["reprt_code"]) for i in rest}
    missing = []
    whys: dict[str, str] = {}
    for i, q in enumerate(qs):
        v, why = probed[i] if i in probed else got.get(i, (None, "미조회"))
        if v is not None:
            q["financials"]["수주잔고"] = v
        else:
            _lbl = q.get("label") or "?"
            missing.append(_lbl)
            # ⚠️ 사유를 **버리지 않는다**. `backlog_probe` 는 왜 못 냈는지
            # 이미 알려주는데 `backlog_for` 가 값만 꺼내 던지고 있었다 —
            # 그래서 화면이 "앞 시기는 없는거야?"에 답을 못 했다(사용자
            # 2026-08-22 인텔리안테크). 아는 걸 화면이 말하게 한다(#123).
            whys[_lbl] = why or "사유미상"
    # ⚠️ 빈 막대에 **이유를 붙인다.** 값이 없으면 막대만 사라져서 화면만 봐선
    # '집계 실패'인지 '원천에 없음'인지 알 수 없다 — 사용자가 "2분기는 아예
    # 안 나오네"라고 물은 지점(2026-08-17). 실측된 사유는 DART 가 그 접수건의
    # 원문을 안 주는 것이다(`status=014`, 한화에어로 2026 1분기 — 정정도 없다).
    if missing:
        _meta = qs[-1].setdefault("_meta", {})
        _meta["backlog_missing"] = missing
        _meta["backlog_why"] = whys

    # ⚠️ **빈칸보다 나쁜 건 틀린 숫자다.** 스윕 실측(2026-08-18, 53종목):
    #   한국항공우주 0.00조·0.08조·26.63조·0.00조 (실제 ~26조 — 26.63 만 맞다)
    #   한전KPS      2.38조 … 0.06조            (실제 ~2조대)
    # 둘 다 「형식혼재」였다 — 분기마다 다른 표를 잡아 한쪽이 1000배 틀렸다.
    # 수주잔고는 **저량**이라 계속기업이 분기 사이에 20배씩 변할 수 없다.
    # 임계 20배는 실측으로 정했다: 위 둘만 걸리고(333배·40배), 정상 급변인
    # 테스 5배·테크윙 6배·넥스틴 3배·HD현대 3배는 안 걸린다.
    # ⚠️ **지우지는 않는다** — 어느 분기가 틀렸는지 원문 없이는 못 가른다
    # (KAI 는 오히려 '튀는 값'이 정답이었다). 화면에 의심을 표시하고
    # 미스로그에 남겨 격주 리포트가 잡게 한다.
    _vals = [v for v in ((q.get("financials") or {}).get("수주잔고") for q in qs)
             if isinstance(v, (int, float)) and v > 0]
    if len(_vals) >= 2 and max(_vals) / min(_vals) >= _BACKLOG_SPREAD_MAX:
        qs[-1].setdefault("_meta", {})["backlog_spread"] = round(
            max(_vals) / min(_vals), 1)
        try:
            from bot.dart_backlog import _log_miss
            _log_miss(ticker, qs[-1]["year"], qs[-1]["reprt_code"], "시계열이상")
        except Exception as exc:
            log.debug("quarterly_infographic: backlog spread log: %s", exc)


def _extra_series(qs: list) -> list[tuple[str, str, list]]:
    """[(키, 제목, 값들)] — **값이 하나라도 있는 항목만**. 없으면 빈 목록."""
    out = []
    for key, title in _EXTRA_CHARTS:
        vals = [(q.get("financials") or {}).get(key) for q in qs]
        if any(v is not None for v in vals):
            out.append((key, title, vals))
    return out


def _footnotes(payload: dict, qs: list) -> list[tuple[str, str]]:
    """푸터 각주 (문구, 색). **레이아웃보다 먼저** 호출돼 H_FOOT 높이를
    정한다 — 줄 수 고정이면 각주가 늘 때 아래 출처줄을 덮어쓴다."""
    cur = payload.get("currency") or "KRW"
    label = (qs[-1] if qs else {}).get("label", "")
    # 기준 기간 명시 — 사용자가 "이 영업이익률이 연간이야 분기야?"라고 물은
    # 지점(2026-08-16). 값만 있고 기간 표기가 없어 판별 불가였다.
    # ⚠️ 두 줄로 나눈다: 한 줄이면 100 단위 폭을 넘겨 bbox_inches="tight" 가
    #    PNG 폭을 종목마다 다르게 늘린다(독립 리뷰 실측).
    # ⚠️ "TTM PER = 최근 4분기 합" 은 **틀린 설명**이었다 — per 는 기본적으로
    #    야후 trailingPE(후행 12개월)이고, 4분기 합을 직접 쓰는 건 PSR 과
    #    자체계산 폴백뿐이다(per_self 각주가 그 경우를 따로 밝힌다).
    notes: list[tuple[str, str]] = [
        (f"* 상단 지표 타일 = {label} 단일분기 기준 "
         f"(YoY = 전년 동기 · QoQ = 직전 분기, 이익률은 %p 차이)", _MUTED),
        ("* TTM PER = 후행 12개월 · Forward PER = 예상실적 기준 "
         "· PSR = 시총 ÷ 최근 4분기 매출 합", _MUTED),
    ]
    # 원천에 없는 분기는 **채우지 않고 밝힌다**(사용자 2026-08-18 LPK.DE —
    # 25.3Q 가 통째로 빠졌는데 화면엔 아무 표시가 없었다).
    from bot.quarterly_series import missing_quarters
    _miss = missing_quarters(qs)
    if _miss:
        notes.append((f"! {', '.join(_miss)} 는 원천에 없어 표에서 빠졌습니다 "
                      f"— 임의로 채우지 않습니다", _NEG))
    bad_keys = payload.get("anomaly_keys") or []
    if bad_keys:
        lbls = payload.get("anomaly_labels") or []
        where = f"({', '.join(lbls)}) " if lbls else ""
        _mm = payload.get("mismatched_accounts") or []
        # 어느 계정끼리 어긋났는지까지 말한다 — '불일치 가능'만으로는
        # 고칠 수 있는 건지 원천 한계인지 아무도 못 가른다(#43·#55).
        _det = (" [" + " · ".join(_mm) + "]") if _mm else ""
        notes.append((
            f"! {'·'.join(bad_keys)} 이상치 감지 {where}— DART 계정 불일치"
            f"{_det}. 해당 TTM·PSR 산출 제외(추정 보정 없음)", _NEG))
    if payload.get("per_self"):
        notes.append(("* TTM PER = 시가총액 ÷ TTM 순이익 자체계산"
                      "(데이터 소스가 PER 미제공)", _MUTED))
    if payload.get("revenue_source"):
        notes.append((f"* 매출 = 영업수익 총액({payload['revenue_source']}) "
                      "— DART 총액 계정 미공시분 보강(영업이익 교차 확인)",
                      _MUTED))
    comp = payload.get("component_accounts") or {}
    if comp:
        # 이상치와 달리 값은 유효하다 — 막지 않고 '총액 아님'만 알린다.
        notes.append((
            "! " + " · ".join(f"{k} = {v}(구성요소 계정)"
                              for k, v in sorted(comp.items()))
            # ⚠️ PNG 각주는 plain text 다 — 마크다운 강조를 쓰면 별표가 그대로
            # 그려진다. 한 줄이 길면 bbox_inches="tight" 가 PNG 폭을 늘린다.
            + " — 총수익 계정 미공시, 총수익의 일부만 표시(합산·추정 없음)"
            + (". 영업이익률·순이익률·PSR 산출 제외"
               if "매출" in comp else ""),
            _GOLD))
    if payload.get("currency_mismatch"):
        notes.append((f"! 재무({cur})와 시총({payload.get('trade_currency','')}) "
                      "통화가 달라 PER·PSR 산출 제외(환산 없이 나누면 틀린 배수)",
                      _NEG))
    if payload.get("fiscal_note"):
        notes.append((f"* {payload['fiscal_note']}", _MUTED))
    miss = payload.get("backlog_missing") or []
    if miss:
        # ⚠️ 옛 문구는 사유를 **하나로 단정**했다("DART 가 원문을 제공하지
        # 않음"). 실제 사유는 분기마다 다르다(원문에 표 없음 / 형식 미지원 /
        # 명시적 미공시 …) — 단정하면 사용자가 "앞 시기는 없는거야?"에 대한
        # 답을 잘못 얻는다(#50 내 가정을 원천의 보장으로 착각하지 말 것).
        _why = payload.get("backlog_why") or {}
        _grp: dict[str, list[str]] = {}
        for _lb in miss:
            _grp.setdefault(_why.get(_lb) or "사유미상", []).append(_lb)
        _parts = [f"{'·'.join(v)} {k[:40]}" for k, v in _grp.items()]
        notes.append((f"* 수주잔고 미표시 — {' / '.join(_parts)}"
                      "(추정 보정 없음)", _MUTED))
    _tw = payload.get("ttm_why") or {}
    if _tw:
        _tg: dict[str, list[str]] = {}
        for _lb, _w in _tw.items():
            _tg.setdefault(_w, []).append(_lb)
        notes.append(("* TTM " + " / ".join(
            f"{'·'.join(v)} 미표시 — {k[:44]}" for k, v in _tg.items())
            + (". PSR 산출 제외" if "매출" in _tw else "")
            + "(부분합으로 대체하지 않음)", _MUTED))
    _fw = payload.get("fcf_why") or {}
    if _fw:
        _g: dict[str, list[str]] = {}
        for _lb, _w in _fw.items():
            _g.setdefault(_w, []).append(_lb)
        notes.append(("* FCF 미표시 — "
                      + " / ".join(f"{'·'.join(v)} {k[:44]}"
                                   for k, v in _g.items())
                      + "(OCF 만으로 대체하지 않음)", _MUTED))
    spread = payload.get("backlog_spread")
    if spread:
        notes.append((f"! 수주잔고 분기 간 격차 {spread}배 — 일부 분기 파싱이 "
                      "틀렸을 수 있음(수주잔고는 저량이라 이런 변동이 정상적이지 "
                      "않음). 값을 지우지 않고 표시만 함", _NEG))
    return notes


# 11.6 → 9.8 (2026-08-21 사용자 "차트의 글씨나 항목들의 글씨가 가독성이
# 떨어져"). 레이아웃은 데이터 좌표(W=100)라 그대로이고, 글씨만 **점 단위**라
# 도화지를 좁히면 이미지 대비 1.18배 커진다 — 크기를 하나하나 올리는 것보다
# 비율이 안 깨진다. 실측: 화면 1200px 기준 8pt 가 11.5px → 13.6px(HTML 표 13px
# 와 같은 급). 출력 1411px 라 1200px 표시에서도 축소만 되고 흐려지지 않는다.
_FIG_W = 9.8           # inch — 모든 조각이 같은 폭이라야 세로로 이어 붙는다
# ⚠️ dpi 는 **선명도만** 바꾼다 — 화면 CSS px 는 `pt/72 × 컨테이너폭 ÷
# _FIG_W` 라 dpi 가 약분돼 사라진다. 그래서 크기 조절은 `_FIG_W`, 선명도는
# 여기다(둘을 헷갈리면 글자 크기를 못 고치면서 파일만 무거워진다).
# 144 → 216 (2026-08-21 사용자 "여전히 가독성이 별로"): 컨테이너가 CSS
# 1100px 인데 출력이 1411px 이라, 배율 2 화면(물리 2200px)에서는 브라우저가
# **확대**해 그려 글자 가장자리가 뭉갠다(사용자 캡처가 1801px 였다 = 고DPI).
# 216 → 9.8×216 = 2117px 로 배율 2 에서도 축소만 된다.
_FIG_DPI = 216
# 데이터 단위 여백. 옛 `pad_inches=0.15` (11.6in 폭에 W=100) 와 같은 비율:
# 0.15 / 11.6 * 100 ≈ 1.29.
_FIG_PAD = 1.29
# ── 패널 기하(단일 출처) ────────────────────────────────────────────────
# ⚠️ 차트 패널과 푸터 패널은 같은 x·폭을 쓰지만, 차트는 **안쪽 여백(LM)**
# 뒤에서 플롯 프레임이 시작한다. 푸터 글자를 패널 기준(6.0)으로 두면 위
# 차트의 플롯 프레임(2.5+8.0=10.5)보다 왼쪽에 떠서 좌우가 안 맞는다
# (사용자 2026-08-21 "좀 더 오른쪽으로 이동해서 위의 차트랑 균형을").
# 값을 두 군데 적으면 한쪽만 바뀌어 다시 어긋나므로 여기 한 곳에서 읽는다.
_PANEL_X, _PANEL_W = 2.5, 95.0
# 차트 섹션 높이. `_CHART_BASE` = 매출·영업이익 + 당기순이익 **2단** 고정.
# FCF 는 그 아래 한 단을 더한다(사용자 2026-08-21 "당기순이익 밑에 별도의
# 차트를 같은 형식으로"). ⚠️ 두 단의 높이는 `_CHART_BASE` 에서 도출한다 —
# `H_CHART` 에서 도출하면 FCF 가 붙는 순간 위 두 단이 같이 쪼그라든다.
_CHART_BASE = 88.0
_FCF_H = 34.0
_CHART_LM, _CHART_RM = 8.0, 3.0


def provenance_line(payload: dict) -> tuple[str, str]:
    """(수치 출처·기준시각, 면책) — 화면 **맨 아래** 한 줄.

    기준시각 표기 의무(CLAUDE.md 실수기록 10-b): 시총/PER/PSR 은 시세 기반이라
    '언제 기준'인지 없으면 오래된 값을 현재값으로 오인한다.

    ⚠️ 2026-08-21 이 줄을 PNG 에서 HTML 로 옮겼다 — 성장동력 카드가 본
    이미지 **뒤**에 오는 배치에서, PNG 안에 있으면 면책이 카드보다 위로
    올라간다. 문구를 두 벌 두면 한쪽만 고쳐지므로 여기 하나만 둔다(#38)."""
    src = payload.get("source_label") or "DART 정기보고서(K-IFRS 연결)"
    # asof 는 캐시 버킷(YYYY-MM-DD_HH) 이라 그대로 찍으면 '2026-08-16_14'
    # 라는 날것이 화면에 나간다 — 사람이 읽는 형태로 바꾼다(독립 리뷰).
    asof = payload.get("asof") or _now_hour_kst()
    _asof_s = asof.replace("_", " ") + "시" if "_" in asof else asof
    return (f"수치: {src} · 시총·PER {_asof_s} 기준(KST) · 환각 0",
            "투자 참고용이며 매수·매도를 권유하지 않습니다")


def _new_canvas(plt, W: float, H: float):
    """모든 조각이 **같은 픽셀 폭**으로 나오는 도화지 → (fig, ax).

    ⚠️ `bbox_inches="tight"` 를 쓰지 않는다. tight 는 그려진 내용에 맞춰
    잘라내므로 조각마다 폭이 달라진다 — 실측에서 본 이미지 1672px, 카드
    이미지 1661px 이었다. 둘을 `width:100%` 로 세로로 놓으면 같은 x 좌표가
    이미지 폭 대비 0.27% 어긋나 패널 왼쪽 선이 안 맞는다(1200px 화면에서
    3.3px). 조각이 셋 이상 되고 그 사이에 표가 끼면 바로 보인다.
    여백을 **데이터 좌표**로 주고 축이 도화지를 꽉 채우게 하면, W=100 좌표가
    항상 같은 픽셀에 떨어져 조각 수와 무관하게 정렬이 보장된다.
    pad_inches 도 조각마다 0.12/0.15 로 갈려 있었다 — 여기 하나로 모은다."""
    pad = _FIG_PAD
    fig, ax = plt.subplots(
        figsize=(_FIG_W, _FIG_W * (H + 2 * pad) / (W + 2 * pad)),
        dpi=_FIG_DPI)
    ax.set_position([0, 0, 1, 1])
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)
    ax.set_xlim(-pad, W + pad)
    ax.set_ylim(H + pad, -pad)          # 위→아래 좌표계
    ax.axis("off")
    return fig, ax


def render_cards(payload: dict, out_path: str, stage: str = "cards",
                 tkey: str = "") -> str | None:
    """성장동력·리스크 카드**만** 담은 별도 PNG. 없으면 None.

    사용자 2026-08-20: 생산능력·가동률 표가 "확인된 성장동력 바로 위"에
    와야 하는데, 카드가 본 인포그래픽 PNG 안에 있어 HTML 표는 이미지 전체
    **뒤**로 밀렸다. 카드를 별도 장으로 떼어 내면 화면 순서가
    [지표·차트·재고자산] → 생산능력 표 → [카드] 가 된다.

    분리 이후 카드를 그리는 곳은 **여기 하나뿐**이다(`_render_locked` 는
    더 이상 그리지 않는다) — 그리기 코드를 두 벌 두면 한쪽만 고쳐져 모양이
    갈라지므로 `_draw_cards()` 로 떼어 두고 여기서만 부른다."""
    _t_wait = _rt_time.time()
    with _RENDER_LOCK:
        _mark_png(tkey, stage, "wait", _rt_time.time() - _t_wait)
        _t_draw = _rt_time.time()
        try:
            return _render_cards_locked(payload, out_path)
        except Exception as exc:                               # noqa: BLE001
            log.warning("quarterly_infographic: card render failed: %s", exc)
            try:
                import matplotlib.pyplot as plt
                plt.close("all")
            except Exception:
                pass
            return None
        finally:
            _mark_png(tkey, stage, "draw", _rt_time.time() - _t_draw)


def _cards_of(payload: dict) -> tuple[list, list]:
    gr = payload.get("growth_risk") or {}
    if not gr.get("ok"):
        return [], []
    return (gr.get("growth_drivers") or []), (gr.get("sustain_risks") or [])


# 폭 계산은 `bot.textwidth` 한 곳 — DART 제품 표의 열 정렬 판정도 같은
# 계산을 쓴다. 두 곳이 각자 세면 판정이 갈라진다(#38).
from bot.textwidth import vlen as _vlen, vtrim as _vtrim   # noqa: E402


def _card_lines(s: str) -> list[str]:
    """카드 항목 한 줄 → 표시할 줄들(최대 `_CARD_MAX_LINES`).

    ⚠️ 어절(공백) 경계에서 끊는다. 낱글자로 끊으면 `반도체검사용 소/켓`
    처럼 단어가 갈라진다(HTML 카드에서 `word-break: keep-all` 로 막은 것과
    같은 이유). 한 어절이 줄보다 길면 그 어절만 강제로 자른다.

    ⚠️ 마지막 줄이 넘치면 그때만 `…` — 옛 구현은 **무조건** 30자에서 잘라
    LLM 이 쓴 근거를 화면에서 없앴다."""
    words, lines, cur = (s or "").split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if _vlen(cand) <= _CARD_LINE_W:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
            if len(lines) == _CARD_MAX_LINES:
                break
        while _vlen(w) > _CARD_LINE_W and len(lines) < _CARD_MAX_LINES:
            head = _vtrim(w, _CARD_LINE_W)
            lines.append(head)
            w = w[len(head):]
        if len(lines) == _CARD_MAX_LINES:
            cur = ""
            break
        cur = w
    if cur and len(lines) < _CARD_MAX_LINES:
        lines.append(cur)
    if not lines:
        return [""]
    # 담지 못하고 남은 게 있으면 마지막 줄에만 말줄임을 붙인다.
    if len(" ".join(lines)) < len(" ".join(words)):
        lines[-1] = _vtrim(lines[-1], _CARD_LINE_W - 1.0) + "…"
    return lines


_CARD_ROW = 3.4           # 한 줄짜리 항목의 세로 간격
_CARD_LINE_GAP = 2.6      # 둘째 줄이 더 먹는 높이


def _card_rows(items: list) -> float:
    """항목들이 차지하는 세로 크기 — 줄 수에서 도출.

    ⚠️ `_card_height`(도화지)와 `_draw_cards`(그리기)가 **같은 함수**를
    봐야 한다. 복제하면 두 줄짜리가 생기는 순간 카드가 잘린다(#38)."""
    from bot.dart_growth_risk import MAX_ITEMS as _M
    tot = 0.0
    for it in (items or [])[:_M]:
        n = len(_card_lines(it))
        tot += _CARD_ROW + _CARD_LINE_GAP * (n - 1)
    return tot


def _card_height(drivers: list, risks: list) -> float:
    """카드 상자 높이 — 항목 수에서 도출. 카드 PNG 의 도화지 높이와 상자
    높이가 같은 식을 봐야 한다(복제하면 잘림이 생긴다, #38)."""
    # ⚠️ 줄바꿈(2026-08-21) 이후 항목마다 높이가 다르다 — 개수로 세면
    # 두 줄짜리가 섞이는 순간 카드가 잘린다. 두 열 중 **큰 쪽**을 쓴다.
    return max(_card_rows(drivers), _card_rows(risks)) + 6.55


def _draw_cards(ax, txt, panel, Rectangle, FancyBboxPatch,
                y: float, h: float, drivers: list, risks: list) -> None:
    """카드 2단 그리기. 호출부는 `_render_cards_locked` 하나 — 본 이미지에
    되돌릴 때도 이 함수를 부르면 되고, 코드를 복제해선 안 된다(#38)."""
    from bot.dart_growth_risk import MAX_ITEMS as _MAX_CARD_ITEMS

    def card_col(x0, w, title, items, color):
        panel(x0, y, w, h, fc=_PANEL, rad=1.6)
        ax.add_patch(Rectangle((x0, y), w, 0.7, facecolor=color,
                               edgecolor="none"))
        txt(x0 + 2, y + 3.2, title, size=9.5, weight="bold")
        iy = y + 6.6
        for i, s in enumerate(items[:_MAX_CARD_ITEMS], 1):
            ax.add_patch(FancyBboxPatch(
                (x0 + 2, iy - 1.15), 2.4, 2.3,
                boxstyle="round,pad=0,rounding_size=1.15",
                facecolor=color, edgecolor="none", mutation_aspect=1))
            txt(x0 + 3.2, iy, str(i), size=7.5, color="#0b1020",
                weight="bold", ha="center")
            # ⚠️ 8.5pt 로 30자를 한 줄에 넣으면 카드 밖으로 나간다(VM 실측
            # 100.2 vs 카드끝 97.5). 그래서 **줄바꿈**으로 간다 — 자르지
            # 않으면서 글자를 키운다(사용자 2026-08-21 "여전히 가독성이
            # 별로. 글씨가 좀 작은것 같기도").
            lines = _card_lines(s)
            ly = iy
            for ln in lines:
                txt(x0 + 5.0, ly, ln, size=8.5)
                ly += _CARD_LINE_GAP
            iy += _CARD_ROW + _CARD_LINE_GAP * (len(lines) - 1)

    card_w = 46.5
    card_col(2.5, card_w, "확인된 성장동력", drivers, _POS)
    card_col(51.0, card_w, "지속조건 · 무효화 리스크", risks, _NEG)


def _render_cards_locked(payload: dict, out_path: str) -> str | None:
    if not _font_ok():
        return None
    drivers, risks = _cards_of(payload)
    if not (drivers or risks):
        return None
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as exc:                                   # noqa: BLE001
        log.warning("quarterly_infographic: matplotlib import: %s", exc)
        return None
    W = 100.0
    h = _card_height(drivers, risks)
    H = h + 5.0
    fig, ax = _new_canvas(plt, W, H)

    def panel(x, y, w, hh, fc=_PANEL, ec=_LINE, rad=1.8):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, hh,
            boxstyle=f"round,pad=0,rounding_size={rad}",
            facecolor=fc, edgecolor=ec, linewidth=0.8, mutation_aspect=1))

    def txt(x, y, s, size=10, color=_TEXT, weight="normal", ha="left",
            va="center", **kw):
        ax.text(x, y, s, fontsize=size, color=color, fontweight=weight,
                ha=ha, va=va, **kw)

    _draw_cards(ax, txt, panel, Rectangle, FancyBboxPatch,
                2.5, h, drivers, risks)
    fig.savefig(out_path, facecolor=_BG)
    plt.close(fig)
    return out_path


def _render_locked(payload: dict, out_path: str,
                   sections: tuple = _SECTIONS) -> str | None:
    if not _font_ok():
        log.warning("quarterly_infographic: Nanum 폰트 없음 — skip")
        return None
    qs = payload.get("quarters") or []
    if not qs:
        return None
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as exc:
        log.warning("quarterly_infographic: matplotlib import failed: %s", exc)
        return None

    gr = payload.get("growth_risk") or {}
    has_llm = bool(gr.get("ok"))
    # ⚠️ drivers/risks 는 여기서 안 읽는다 — 카드는 별도 PNG(`render_cards`).
    headline = (gr.get("headline") or "").strip() if has_llm else ""
    risk_sub = (gr.get("risk_subline") or "").strip() if has_llm else ""

    # 세로 레이아웃(W=100 좌표계). LLM 섹션이 없으면 그 높이만큼 줄인다.
    W = 100.0
    _on = lambda k: k in sections          # noqa: E731 — 섹션 게이트
    H_HEAD = 16.0 if _on("head") else 0.0
    H_CALL = (9.0 if headline else 0.0) if _on("call") else 0.0
    # H_CHART 26 → 62: 차트를 좌우 2분할에서 **세로 2단**(각 전체폭)으로
    # 바꿨다. 옛 배치는 inset 폭이 37.5 단위(≈652px)뿐이라 항목이 뭉갰다
    # (사용자 2026-08-16). 이제 86 단위(≈1500px) = 2.3배. 이 상수 하나로
    # H·figsize 가 자동으로 따라 커진다(아래 H 계산 + y 누적 방식).
    # H_TILE 17 → 22: YoY/QoQ 를 한 줄 → **두 줄**로 나눠 폰트를 7.5→9.0 으로
    # 키웠다(사용자 2026-08-16 '숫자가 잘 안 보여'). 두 줄이 들어갈 높이 확보.
    # H_CHART 62 → 88: 같은 지시의 차트 판("더 길게"). 0 기준선 막대에서
    # 매출 2.15→2.31B(+7%)는 짧은 축에선 눈으로 구분이 안 된다 — 축을 늘려
    # 같은 델타가 더 많은 픽셀을 차지하게 하고, 눈금을 촘촘히 하고, 막대에
    # 값 라벨을 붙여 '변화가 안 보인다'를 세 겹으로 해결한다.
    H_TILE = 22.0 if _on("tiles") else 0.0
    # FCF 는 원천에 있는 종목만 그린다 — 빈 패널은 없는 사실을 그린 것.
    _fcf_vals = [(q.get("financials") or {}).get("FCF") for q in qs]
    _has_fcf = _on("charts") and any(v is not None for v in _fcf_vals)
    H_CHART = ((_CHART_BASE + (_FCF_H if _has_fcf else 0.0))
               if _on("charts") else 0.0)
    # 추가 막대차트(수주잔고·재고자산) — 사용자 2026-08-16 "미래의 수익을
    # 가늠해보고 싶어서". **데이터가 있는 것만** 그리고, 없으면 높이 0 이라
    # 레이아웃이 통째로 줄어든다(빈 패널 = 없는 사실을 그린 것).
    # 이익률 선이 없는 순수 막대라 한 단은 위 2단보다 낮게 잡는다.
    _extra = _extra_series(qs)
    _EXTRA_H = 34.0
    H_EXTRA = _EXTRA_H * len(_extra) if _on("extra") else 0.0
    # 카드 상자 높이는 **항목 수에서 도출**한다(H_FOOT 을 각주 줄 수로 잡은
    # 것과 같은 패턴). 옛 코드는 20.0 고정이라 패널이 17.0 뿐이었는데 4번
    # 항목의 칩 하단이 17.95 라 **상자 밖으로 0.95 단위(≈20px) 튀어나갔다**
    # (사용자 2026-08-16 스크린샷). 3개 이하에선 안 보이던 잠복 버그다.
    # 1번 중심 6.6 + 간격 3.4×(n-1) + 칩 반높이 1.15 + 하단 여백 2.2.
    # 카드를 별도 PNG 로 분리(2026-08-20)했으므로 이 이미지에서는 **항상 0**.
    # 높이 식(`_card_height`)은 `_render_cards_locked` 한 곳만 쓴다(#38).
    H_CARDS = 0.0
    # 각주를 **레이아웃 전에** 만들어 높이를 실제 줄 수로 잡는다. 옛 코드는
    # H_FOOT 을 15.0 으로 고정해 두고 아래에서 각주를 만들었다 — 각주가
    # 3줄을 넘으면 맨 아래 출처·면책 줄을 덮어쓴다(기준기간 각주를 추가하며
    # 실제로 그 한계에 닿았다). 줄 수에 따라 커지게 해 구조적으로 막는다.
    notes = _footnotes(payload, qs)
    # 끝의 +1.5 = 마지막 각주 글자 높이 + 아래 여백. 옛 값은 +4.2 였는데 그건
    # 맨 아래 출처·면책 줄 자리였다 — 그 줄을 HTML 로 뺐으므로(2026-08-21)
    # 같이 줄인다. 안 줄이면 이미지 끝에 빈 띠가 남는다.
    H_FOOT = (8.4 + len(notes) * 2.4 + 1.5) if _on("foot") else 0.0
    # 끝의 +2 = 이미지 하단 여백. 옛 +6 은 출처·면책 줄이 `H - 2.6` 에 있던
    # 시절의 자리다 — 그 줄을 HTML 로 뺐으므로 같이 줄인다(안 줄이면 140px
    # 짜리 빈 띠가 남는다, 실측).
    _content = (H_HEAD + H_CALL + H_TILE + H_CHART + H_EXTRA + H_CARDS
                + H_FOOT)
    # ⚠️ 담을 게 하나도 없으면 **이미지를 만들지 않는다.** foot 이 상단으로
    # 옮겨간 뒤(2026-08-21) 수주잔고·재고자산이 없는 종목은 하단 조각이
    # 통째로 비는데, 그대로 그리면 화면에 정체불명의 얇은 검은 띠가 남는다.
    if _content <= 0:
        return None
    H = _content + 2

    fig, ax = _new_canvas(plt, W, H)

    def panel(x, y, w, h, fc=_PANEL, ec=_LINE, rad=1.8):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rad}",
            facecolor=fc, edgecolor=ec, linewidth=1.0, mutation_aspect=1))

    def txt(x, y, s, size=10, color=_TEXT, weight="normal", ha="left",
            va="center"):
        ax.text(x, y, s, fontsize=size, color=color, weight=weight, ha=ha,
                va=va, transform=ax.transData)

    cur = payload.get("currency") or "KRW"

    def amt(v) -> str:
        return _eok(v, cur)

    last = qs[-1]
    lf, lr = last.get("financials") or {}, last.get("ratios") or {}
    prev_q = qs[-2] if len(qs) > 1 else None
    yoy_q = qs[-5] if len(qs) >= 5 else None   # 4분기 전 = 전년 동기

    # y = 세로 커서. 섹션 게이트 **밖**에 둔다 — 헤더 없는 조각(하단 파트)도
    # 여기서부터 쌓아야 한다(안에 두면 UnboundLocalError, 실측).
    y = 3.0
    if _on("head"):
        # ── 헤더 ────────────────────────────────────────────────────────
        panel(2.5, y, 95, H_HEAD - 4, fc="#1d4ed8", ec="#1d4ed8", rad=2.6)
        company = payload.get("company") or payload.get("ticker") or ""
        txt(6, y + 3.0, f"{last.get('label','')} 실적 분석", size=9.5,
            color="#cfe3ff", weight="bold")
        txt(6, y + 7.4, company, size=17, color="white", weight="bold")
        mk = payload.get("market") or ""
        # 연결/별도는 실제 fs_div 를 따라야 한다 — get_quarterly_series 는 CFS 가
        # 없으면 OFS 로 폴백하는데 헤더에 '연결'을 박아두면 푸터 출처 라벨과
        # 모순되고 숫자 성격을 오인하게 된다(2026-08-19 code-review).
        # 연결/별도는 KR(DART) 전용 개념 — fs_div 가 없는 시장엔 표기하지 않는다
        # (없는 구분을 그리면 숫자 성격을 오인시킨다). 대신 통화가 다르면
        # '재무 CNY · 시총 HKD' 처럼 명시한다(HK 본토 자회사에서 흔함).
        _bits = [mk, payload.get("ticker", "")]
        if last.get("fs_div"):
            _bits.append("연결" if last.get("fs_div") == "CFS" else "별도")
        if payload.get("currency_mismatch"):
            _bits.append(f"재무 {cur} · 시총 {payload.get('trade_currency', '')}")
        elif cur and cur != "KRW":
            _bits.append(cur)
        txt(6, y + 11.0, " · ".join(b for b in _bits if b), size=9,
            color="#dbe9ff")
        # ⚠️ 시총·현재가는 **거래통화** — 재무통화(amt)로 찍으면 HK 처럼 둘이
        # 다른 종목에서 통화기호가 틀린다(렌더 스모크에서 실측: HKD 시총이
        # ¥로 표기됨). 재무제표 금액만 amt(재무통화)를 쓴다.
        _tc = payload.get("trade_currency") or cur
        # 현재가 + 시가총액을 오른쪽에 나란히(사용자 2026-08-21, 전 시장 공통).
        # 있는 것만 그리고 오른쪽 끝에서부터 왼쪽으로 쌓는다 — 한쪽이 없어도
        # 빈자리가 안 남는다.
        # ⚠️ 칸 간격을 글자수로 **추정하지 않는다** — 처음엔 그렇게 했다가
        # `₩1,730,000` 과 `₩1,263.75조` 가 79.5 에서 딱 붙었다(실측).
        # 오른쪽 끝 두 자리를 고정하고, 겹침은 회귀가 픽셀로 잡는다.
        for _ax, _lab, _val in (
                (94.0, "시가총액", _eok(payload.get("market_cap"), _tc)
                 if payload.get("market_cap") else None),
                (76.0, "현재가", _fmt_price(payload.get("price"), _tc)
                 if payload.get("price") else None)):
            if not _val:
                continue
            txt(_ax, y + 4.5, _lab, size=8.5, color="#cfe3ff", ha="right")
            txt(_ax, y + 8.6, _val, size=14, color="white", weight="bold",
                ha="right")
        y += H_HEAD

    # ── 헤드라인 콜아웃(LLM) ────────────────────────────────────────
    if headline and _on("call"):
        panel(2.5, y, 95, H_CALL - 2.5, fc=_PANEL2, rad=1.8)
        txt(6, y + 2.6, headline, size=11.5, weight="bold")
        if risk_sub:
            txt(6, y + 5.6, f"확인할 리스크  {risk_sub}", size=9, color=_MUTED)
        y += H_CALL

    if _on("tiles"):
        # ── 지표 타일 5종 ───────────────────────────────────────────────
        def _fin(q, k):
            return ((q or {}).get("financials") or {}).get(k)

        def _rat(q, k):
            return ((q or {}).get("ratios") or {}).get(k)

        def _subs(now, yoy_v, qoq_v, *, pp: bool = False):
            """YoY·QoQ 를 **두 줄**로. 옛 코드는 한 줄 size 7.5 _MUTED 라
            사용자가 '숫자가 잘 안 보인다'고 지적했다(2026-08-16). 줄을 나눠
            폰트를 키우고 부호색(증가 초록·감소 빨강)을 입힌다.

            pp=True 는 **비율 지표**용 — 이익률의 변화는 '변화율(%)'이 아니라
            **%p 차이**다. 20.8% → 21.0% 를 '+1.0%' 로 쓰면 0.2%p 상승을
            1% 상승으로 오독시킨다."""
            out = []
            for tag, prev in (("YoY", yoy_v), ("QoQ", qoq_v)):
                if now is None or prev is None:
                    continue
                if pp:
                    d = now - prev
                    s = f"{tag} {d:+.1f}%p"
                else:
                    d = _chg(now, prev)
                    if d is None:
                        continue
                    s = f"{tag} {d:+.1f}%"
                out.append((s, _POS if d >= 0 else _NEG))
            return out

        _fwd = payload.get("per_forward")
        # '매출' 자리에 구성요소(이자수익)가 들어간 회사는 이름을 바꿔 부른다 —
        # 안 그러면 "매출 5,787억 · 영업이익 6,812억" 처럼 모순돼 보인다
        # (사용자 2026-08-19 NH투자증권 "매출보다 영익이 더 나오는데").
        _rev_nm = (payload.get("component_accounts") or {}).get("매출") or "매출"
        tiles = [
            (_rev_nm, amt(lf.get("매출")), _ACCENT,
             _subs(lf.get("매출"), _fin(yoy_q, "매출"), _fin(prev_q, "매출"))),
            ("영업이익", amt(lf.get("영업이익")), _POS,
             _subs(lf.get("영업이익"), _fin(yoy_q, "영업이익"),
                   _fin(prev_q, "영업이익"))),
            # ⚠️ **최근 단일분기** 이익률이다(연간 아님 — 헤더의 분기 라벨 기준).
            # 사용자가 "이게 연간이야 분기야?"라고 물은 지점 — 값만 있고 비교가
            # 없어 판별 불가였다. YoY/QoQ %p 를 붙이면 분기 기준이 자명해진다.
            ("영업이익률" if _rev_nm == "매출" else "영업이익률(산출불가)",
             _pct(lr.get("영업이익률")), _GOLD,
             _subs(lr.get("영업이익률"), _rat(yoy_q, "영업이익률"),
                   _rat(prev_q, "영업이익률"), pp=True)),
            ("당기순이익", amt(lf.get("당기순이익")), _PUR,
             _subs(lf.get("당기순이익"), _fin(yoy_q, "당기순이익"),
                   _fin(prev_q, "당기순이익"))),
            # '*' = 자체계산(시총÷TTM순이익). ASCII 라 폰트 결손 위험이 없다
            # (이모지·특수기호는 NanumGothic 에서 두부로 나올 수 있음).
            # 서브라인 = Forward PER(예상실적 기준). 미제공이면 'N/A' 를 **명시**
            # — 빈칸이면 '계산 중'인지 '없는지' 구분이 안 된다(사용자 2026-08-16).
            ("TTM PER" + ("*" if payload.get("per_self") else ""),
             ("—" if payload.get("per") is None
              else f"{payload['per']:,.2f}배"), _ACCENTW,
             [(f"Fwd {_fwd:,.2f}배" if _fwd is not None else "Fwd N/A",
               _ACCENTW if _fwd is not None else _MUTED)]),
        ]
        tw, gap = 18.0, 1.5
        tx = 2.5
        for name, val, col, subs in tiles:
            panel(tx, y, tw, H_TILE - 3, fc=_PANEL, rad=1.6)
            ax.add_patch(Rectangle((tx, y), tw, 0.7, facecolor=col,
                                   edgecolor="none"))
            txt(tx + 1.6, y + 3.4, name, size=8.5, color=_MUTED, weight="bold")
            txt(tx + 1.6, y + 7.8, val, size=12.5, color=col, weight="bold")
            sy = y + 12.4
            for s, scol in subs[:2]:
                txt(tx + 1.6, sy, s, size=9.0, color=scol, weight="bold")
                sy += 3.6
            tx += tw + gap
        y += H_TILE

    # 차트 두 단은 charts 섹션에서만 그린다. `combo` 정의는 위에 두어
    # extra(수주잔고·재고자산) 섹션도 같은 렌더러를 쓴다 — 복제하면
    # 두 조각의 축·눈금·색 규약이 갈라진다(#38).
    # ── 콤보차트 2개(막대 + 선, 실제 matplotlib 축) ─────────────────
    labels = [q.get("label", "") for q in qs]
    rev = [(q.get("financials") or {}).get("매출") for q in qs]
    op = [(q.get("financials") or {}).get("영업이익") for q in qs]
    ni = [(q.get("financials") or {}).get("당기순이익") for q in qs]
    opm = [(q.get("ratios") or {}).get("영업이익률") for q in qs]
    nim = [(q.get("ratios") or {}).get("순이익률") for q in qs]

    bad_labels = set(payload.get("anomaly_labels") or [])

    def combo(rect, bars, bar_labels, bar_colors, line, line_color,
              line_title, title_base):
        """rect=(x0,y0,w,h) 데이터좌표 패널 → 금액 막대(위) + 이익률 %(아래)를
        **분리된 두 축**으로 그린다.

        옛 구현은 `twinx()` 이중 Y축이었다. 두 스케일의 정렬 기준이 임의라
        존재하지 않는 상관을 만들어 내고, 실제로 '25.4Q 매출 -21조인데
        이익률 선은 0% 근처'처럼 읽혀 오독을 유발했다(사용자 2026-08-16).
        축을 나누면 그 오독이 **구조적으로 불가능**해진다. 두 축의 xlim 을
        같게 잡아 같은 분기가 세로로 정렬되고, x 라벨은 아래 축에만 둔다."""
        from matplotlib.ticker import FuncFormatter, MaxNLocator
        x0, y0, w, h = rect
        panel(x0, y0, w, h, fc=_PANEL, rad=1.6)
        # 금액 단위 자동 선택 — 억 단위로 두면 대형주가 100만(억)대라
        # matplotlib 이 y축에 '1e6' 오프셋을 붙여 제목을 가리고 값도 안 읽힌다.
        # 최대값이 1조 이상이면 조 단위로 스케일(라벨도 함께 바꾼다).
        from bot.quarterly_series import chart_unit
        peak = max((abs(v) for b in bars for v in b if v is not None),
                   default=0)
        div, unit, dec = chart_unit(cur, peak)
        txt(x0 + 2.5, y0 + 2.6, f"{title_base} ({unit})", size=10.5,
            weight="bold")
        # data 좌표 → figure fraction. ⚠️ x/W 로 직접 나누면 안 된다 —
        # 메인 ax 는 figure 전체가 아니라 기본 여백 안쪽만 차지하므로
        # 축이 패널 밖으로 삐져나온다. 실제 transData→transFigure 변환 사용.
        inv = fig.transFigure.inverted()

        def _d2f(dx, dy):
            return inv.transform(ax.transData.transform((dx, dy)))

        # 여백: 좌 8(y 눈금 라벨 — 폰트를 키웠으므로 옛 6 에서 확대)·우 3·
        # 위 7(제목+범례)·아래 4(x 라벨). y 축이 invert 돼 '아래'가 큰 y 값.
        LM, RM, TP, BP, GAP = _CHART_LM, _CHART_RM, 7.0, 4.0, 1.6
        # 이익률이 전량 결측이면(매출 미제공·0 등) % 패널을 만들지 않는다.
        # 빈 축을 그리면 '0% / 0% / -0%' 눈금이 붙어 **마진 0%** 로 읽힌다
        # (없는 사실을 그린 셈 — 2026-08-16 독립 리뷰, "환각 0" 원칙).
        has_pct = any(v is not None for v in (line or []))
        plot_h = h - TP - BP - (GAP if has_pct else 0.0)
        bar_h = plot_h * (0.70 if has_pct else 1.0)
        pct_h = plot_h - bar_h

        def _axes(top, bot):
            fx0, fy0 = _d2f(x0 + LM, y0 + bot)
            fx1, fy1 = _d2f(x0 + w - RM, y0 + top)
            a = fig.add_axes([fx0, fy0, fx1 - fx0, fy1 - fy0])
            a.set_facecolor(_PANEL)
            for sp in a.spines.values():
                sp.set_color(_LINE)
            a.grid(axis="y", color=_LINE, linewidth=0.5, alpha=0.6, zorder=0)
            a.set_axisbelow(True)
            return a

        bax = _axes(TP, TP + bar_h)
        pax = (_axes(TP + bar_h + GAP, TP + bar_h + GAP + pct_h)
               if has_pct else None)

        n = len(labels)
        idx = list(range(n))
        # 결측 분기를 0 으로 바꾸면 '실적 0' 막대가 그려져 없는 사실을
        # 그린 셈이 된다("환각 0" 푸터와 모순, 2026-08-19 code-review).
        # NaN 은 matplotlib 이 막대를 아예 안 그린다 → 결측이 결측으로 보인다.
        nan = float("nan")
        vals = [[(nan if v is None else v / div) for v in b] for b in bars]
        width = 0.34 if len(bars) > 1 else 0.46
        offs = ([-width / 2, width / 2] if len(bars) > 1 else [0])
        # 값 라벨 자릿수는 **축 눈금보다 촘촘해야 한다** — 축 자릿수(dec)를
        # 그대로 쓰면 2.15·2.24·2.31 이 전부 '2.1/2.2/2.3' 으로 뭉개져
        # 라벨을 붙인 목적(분기간 차이 식별)이 사라진다(렌더 실측).
        for k, series in enumerate(vals):
            # 자릿수는 **이 시리즈의 peak** 기준 — 공통 peak 로 정하면
            # 작은 쪽 시리즈가 전 분기 '0.00' 이 된다(독립 리뷰 실측).
            _spk = max((abs(v) for v in series if v == v), default=0)
            ldec = _label_decimals(_spk, dec)
            _c = bax.bar([i + offs[k] for i in idx], series, width=width,
                         color=bar_colors[k], label=bar_labels[k], zorder=2)
            # 막대 값 라벨 — 0 기준선 막대는 분기간 델타가 축 높이의 몇 %에
            # 불과해(매출 +7% = 눈으로 거의 동일) '변화가 안 보인다'는 지적의
            # 근본 원인이다(사용자 2026-08-16). 숫자를 직접 얹으면 축 스케일과
            # 무관하게 읽힌다. NaN(결측)은 빈 라벨 — 없는 값을 0 으로 쓰지 않는다.
            bax.bar_label(
                _c, labels=[("" if v != v else f"{v:,.{ldec}f}") for v in series],
                fontsize=8.5, color=bar_colors[k], padding=1.5, fontweight="bold")
        # 값 라벨이 축 경계에 붙으면 잘린다 — 헤드룸 8% 확보. **아래쪽도**
        # 넓힌다: 적자 분기의 라벨은 막대 아래에 그려져 축 밖으로 나가
        # x 라벨·아래 % 패널과 겹친다(2026-08-16 독립 리뷰 실측).
        _b0, _b1 = bax.get_ylim()
        _pad = (_b1 - _b0) * 0.08
        bax.set_ylim(_b0 - (_pad if _b0 < 0 else 0), _b1 + _pad)
        # 지수 오프셋('1e6') 금지 — 위 단위 스케일링으로 자릿수를 이미 줄였고,
        # 오프셋 텍스트가 축 위에 그려져 제목을 침범한다.
        bax.ticklabel_format(axis="y", style="plain", useOffset=False)
        # 눈금 세밀화 — 옛 코드엔 locator 설정이 아예 없어 축 높이에 맞춰
        # 3~4개로 성기게 잡혔다(사용자 2026-08-16 '숫자간격 더 세밀하게').
        # 눈금 세밀화(사용자 2026-08-16 '왼쪽 축의 숫자를 더 세밀하게').
        # nbins 은 축의 실제 픽셀 높이에서 — 근거는 _nbins_for 참조.
        bax.yaxis.set_major_locator(MaxNLocator(
            nbins=_nbins_for(bax.get_window_extent().height, _AMT_NBINS),
            steps=_TICK_STEPS))
        bax.yaxis.set_major_formatter(
            FuncFormatter(lambda v, _p: f"{v:,.{dec}f}"))
        bax.tick_params(axis="y", labelsize=9, colors=_MUTED, length=2)
        bax.tick_params(axis="x", length=0, labelbottom=False)

        if pax is not None:
            pax.plot(idx, [nan if v is None else v for v in line],
                     color=line_color, marker="o", markersize=4.2,
                     linewidth=2.0, zorder=3)
            # % 패널은 금액 패널의 ~30% 높이(실측 142px)뿐이라 같은 상수를
            # 쓰면 라벨이 겹친다 — 여기서도 픽셀 기준으로 뽑는다.
            pax.yaxis.set_major_locator(MaxNLocator(
                nbins=_nbins_for(pax.get_window_extent().height, _PCT_NBINS),
                steps=_TICK_STEPS))
            # 정수 %로 고정하면 마진 폭이 좁을 때 '11% 10% 10% 10%' 처럼 눈금이
            # 중복 표기된다(2026-08-16 독립 리뷰, 렌더 실측). 축 범위에 맞춰
            # 소수 자리를 자동 확보한다.
            _lo, _hi = pax.get_ylim()
            _pdec = 0 if (_hi - _lo) >= 4 else (1 if (_hi - _lo) >= 0.4 else 2)
            pax.yaxis.set_major_formatter(
                FuncFormatter(lambda v, _p: f"{v:,.{_pdec}f}%"))
            pax.tick_params(axis="y", labelsize=9, colors=_MUTED, length=2)
            pax.tick_params(axis="x", length=0)
            # 시리즈가 1개인 축은 범례 대신 제목이 이름을 담당한다.
            # ⚠️ set_ylabel 은 쓰지 않는다 — 축이 낮아 세로쓰기 한글이 글자끼리
            # 겹쳐 뭉갠다(렌더 스모크에서 실측). 축 위 좌측 가로 제목으로.
            pax.set_title(line_title, fontsize=8.5, color=line_color,
                          loc="left", pad=2.0)

        # x 라벨은 맨 아래 축에만(두 축이 세로로 정렬돼 같은 분기가 일치).
        x_ax = pax if pax is not None else bax
        for a in (bax, pax):
            if a is None:
                continue
            a.set_xlim(-0.6, n - 0.4)   # 두 축 x 정렬(같은 분기가 세로로 일치)
            a.set_xticks(idx)
        if x_ax is bax:
            bax.tick_params(axis="x", length=0, labelbottom=True)
        x_ax.set_xticklabels(labels, fontsize=9, color=_MUTED)
        # 이상치가 붙은 분기는 x 라벨을 경고색으로 — 어느 분기가 문제인지
        # 그림 안에서 바로 보이게(각주와 짝).
        for lab, t_ in zip(labels, x_ax.get_xticklabels()):
            if lab in bad_labels:
                t_.set_color(_NEG)
                t_.set_weight("bold")
        # 범례는 축 **바깥 위**로 — 옛 loc="upper left" 는 가장 높은 첫 분기
        # 막대와 정면 충돌했다(frameon=False 라 가려지는 게 그대로 보였음).
        # 시리즈가 1개면 범례를 아예 두지 않는다(패널 제목이 이름을 담당).
        if len(bars) > 1:
            lg = bax.legend(loc="lower left", bbox_to_anchor=(0, 1.02),
                            fontsize=8.5, frameon=False, labelcolor=_MUTED,
                            ncol=len(bars), handlelength=1.2,
                            columnspacing=1.4)
            if lg:
                lg.set_zorder(4)

    if _on("charts"):
        _ch = (_CHART_BASE - 4) / 2.0    # 두 단 각각의 패널 높이(FCF 무관)
        # ⚠️ 범례·제목도 **실제 계정명**으로 부른다(2026-08-19 NH투자증권).
        # 타일은 '이자수익'이라 고쳤는데 차트만 '매출'이라, 이자수익 막대가
        # 영업이익보다 낮은 그림이 "매출 < 영업이익" 으로 읽혔다. 같은 값에
        # 두 이름을 쓰면 화면이 스스로 모순된다.
        _rev_lbl = (payload.get("component_accounts") or {}).get("매출") or "매출"
        combo((_PANEL_X, y, _PANEL_W, _ch), [rev, op], [_rev_lbl, "영업이익"],
              [_ACCENT, _POS], opm, _GOLD, "영업이익률",
              f"{_rev_lbl} · 영업이익")
        combo((_PANEL_X, y + _ch + 2.0, _PANEL_W, _ch), [ni], ["당기순이익"], [_PUR],
              nim, _NEG, "순이익률", "당기순이익")
        if _has_fcf:
            # 당기순이익 **밑**. line 을 전부 None 으로 넘기면 combo 의
            # has_pct 가 False → % 패널 없이 막대가 패널 전체를 쓴다
            # (수주잔고·재고자산과 같은 형태).
            combo((_PANEL_X, y + 2 * (_ch + 2.0), _PANEL_W, _FCF_H - 2.0),
                  [_fcf_vals], ["FCF"], [_ACCENTW],
                  [None] * len(labels), _MUTED, "", "FCF (잉여현금흐름)")
        y += H_CHART

    if _on("extra"):
        # ── 수주잔고 · 재고자산 (막대만, 있는 항목만) ───────────────────
        # line 을 전부 None 으로 넘기면 combo 의 has_pct 가 False → % 패널을
        # 만들지 않고 막대가 패널 전체를 쓴다(사용자가 요청한 형태).
        _EX_COLOR = {"수주잔고": _ACCENTW, "재고자산": _GOLD}
        for _i, (_k, _title, _vals) in enumerate(_extra):
            combo((_PANEL_X, y + _i * _EXTRA_H, _PANEL_W, _EXTRA_H - 2.0), [_vals], [_title],
                  [_EX_COLOR.get(_k, _ACCENT)], [None] * len(labels), _MUTED,
                  "", _title)
        y += H_EXTRA

    # ── 성장동력 / 리스크 카드 ─────────────────────────────────────
    # ⚠️ **여기서 그리지 않는다.** 사용자 2026-08-20 요청으로 카드는 별도
    # PNG(`render_cards`)로 분리했다 — 생산능력·가동률 표가 "확인된 성장동력
    # 바로 위"에 와야 하는데, 카드가 이 이미지 안에 있으면 HTML 표는 이미지
    # 전체 뒤로 밀려 카드 **아래**가 되기 때문이다.
    # 높이 계산(H_CARDS)은 0 이 되어 이 이미지가 그만큼 짧아진다.
    # 그리기 코드는 `_draw_cards` 하나뿐이라 두 이미지가 갈라질 수 없다.

    if _on("foot"):
        # ── 푸터(TTM + 출처 + 면책) ─────────────────────────────────────
        ttm = payload.get("ttm") or {}
        _net_v, _net_k = ttm_net(ttm)
        panel(_PANEL_X, y, _PANEL_W, 6.4, fc=_PANEL2, rad=1.6)
        foot_items = [
            ("TTM 매출", amt(ttm.get("매출"))),
            ("TTM 영업이익", amt(ttm.get("영업이익"))),
            # 옆 칸 'TTM PER' 의 분모와 **같은 계정**이어야 한다(#33) —
            # 값을 고르는 규칙은 `ttm_net` 하나뿐이다.
            ("TTM 순이익" + ("(지배주주)" if _net_k == "지배주주순이익" else ""),
             amt(_net_v)),
            ("TTM PER" + ("*" if payload.get("per_self") else ""),
             "—" if payload.get("per") is None
             else f"{payload['per']:,.2f}배"),
            # 타일 서브라인과 같은 값 — 두 표면이 어긋나면 그게 곧 버그다.
            ("Forward PER", "N/A" if payload.get("per_forward") is None
             else f"{payload['per_forward']:,.2f}배"),
            ("PSR", "—" if payload.get("psr") is None
             else f"{payload['psr']:,.2f}배"),
        ]
        # 위 차트의 플롯 프레임과 같은 x 에서 시작한다(위 상수 주석 참조).
        _fx0 = _PANEL_X + _CHART_LM
        fx = _fx0
        # 항목이 늘어도 패널 안에 들어오게 — 폭도 기하에서 도출한다.
        _fgap = (_PANEL_W - _CHART_LM - _CHART_RM) / max(len(foot_items), 1)
        for name, val in foot_items:
            txt(fx, y + 2.2, name, size=8.5, color=_MUTED)
            txt(fx, y + 4.6, val, size=10.5, weight="bold")
            fx += _fgap
        # 이상치·자체계산 각주 — 값이 '—' 로 비었을 때 "왜 비었나"를 화면에서
        # 알 수 있어야 한다(빈칸만 두면 데이터 없음과 구분 불가). 이모지 대신
        # ASCII 마커 + 색으로 표기(NanumGothic 글리프 결손 회피).
        _ny = y + 7.6
        for _note, _ncol in notes:
            # 각주도 같이 옮긴다 — 사용자 "밑에 주석까지 포함해서".
            txt(_fx0, _ny, _note, size=8.5, color=_ncol)
            _ny += 2.4
        # ⚠️ 출처·면책 줄은 **여기서 그리지 않는다**(2026-08-21). 사용자가 요청한
        # 배치에서 성장동력 카드가 이 이미지 뒤에 오는데, 면책 문구는 화면 맨
        # 아래여야 한다 — PNG 안에 있으면 카드보다 위로 올라간다. HTML 로 빼서
        # 조각 순서와 무관하게 최하단을 지킨다(`provenance_line`).

    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fig.savefig(out_path, facecolor=_BG)
        plt.close(fig)
        return out_path
    except Exception as exc:
        log.warning("quarterly_infographic: savefig failed: %s", exc)
        try:
            plt.close(fig)
        except Exception:
            pass
        return None


# 이상치 플래그 → 그 플래그가 오염시키는 항목. 두 플래그 모두 '매출'
# canonical 계정 판정에서 나오므로 매출만 막는다(영업이익·당기순이익은
# 별도 계정이라 무관 — 실제로 메리츠금융지주도 순이익만 정상이었다).
_ANOMALY_AFFECTS: dict[str, tuple[str, ...]] = {
    "_anomaly_revenue_negative": ("매출",),
    "_anomaly_account_mismatch": ("매출",),
}


def anomalous_keys(qs: list) -> set:
    """이 분기 구간에서 신뢰할 수 없는 항목 집합."""
    out: set = set()
    for q in qs or []:
        fin = q.get("financials") or {}
        for flag, keys in _ANOMALY_AFFECTS.items():
            if fin.get(flag):
                out.update(keys)
    return out


def mismatched_accounts(qs: list) -> list:
    """어긋난 **계정쌍**(`매출: 매출액 ↔ 영업수익` 꼴). 중복 제거·정렬.

    ⚠️ `_diff_quarter` 는 어느 계정끼리 어긋났는지 이미 기록해 두는데
    (`_mismatched_accounts`) 화면은 "DART 계정 불일치 가능" 이라고만 말해,
    이게 고칠 수 있는 건지(총액끼리 라벨만 다름) 원천 한계인지(구성요소가
    끼어듦) 사용자도 나도 못 갈랐다(사용자 2026-08-22 LG화학 25.4Q
    "계정이 불일치한거 좀 봐줄수 있어?"). 아는 걸 화면이 말하게 한다(#43).
    """
    seen: list[str] = []
    for q in qs or []:
        for m in (q.get("financials") or {}).get("_mismatched_accounts") or []:
            if m not in seen:
                seen.append(m)
    return seen


def anomalous_labels(qs: list) -> list:
    """이상치가 붙은 분기 라벨(화면에 '어느 분기가 문제인지' 표시용)."""
    return [q.get("label", "") for q in qs or []
            if any((q.get("financials") or {}).get(f) for f in _ANOMALY_AFFECTS)]


def ttm_net(ttm: dict) -> tuple:
    """TTM 순이익 — `(값, 어느 계정인가)`. **지배주주 귀속분 우선**(FnGuide
    산식, #193).

    ⚠️ 이 함수를 두 곳이 함께 쓰는 이유: 푸터의 'TTM 순이익' 칸과 그 옆
    'TTM PER' 의 분모가 **다른 계정이면** 사용자가 눈으로 나눠 봤을 때 안
    맞는다(#33). 값을 고르는 규칙을 복제하면 언젠가 한쪽만 바뀐다(#38).
    """
    t = ttm or {}
    v = t.get("지배주주순이익")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v, "지배주주순이익"
    return t.get("당기순이익"), "당기순이익"


def _ttm(qs: list) -> dict:
    """최근 4분기 합 = TTM. 4개 미만이면 빈 dict(부분합으로 TTM 이라 부르면
    틀린 값 — 억지로 만들지 않는다).

    ⚠️ 이상치 플래그가 붙은 분기가 창 안에 있으면 **그 항목은 만들지
    않는다**. 옛 코드는 플래그를 무시하고 그냥 합산해서, DART 계정 승자
    불일치로 한 분기 매출이 -21조가 된 종목의 TTM 매출을 -10.30조로 찍고
    거기서 PSR -1.87배까지 파생시켰다(사용자 2026-08-16 메리츠금융지주).
    오염된 합계를 'TTM' 이라 부르지 않는다 — 빈칸이 틀린 숫자보다 낫다.

    ⚠️ **연속한 4분기가 아니면 만들지 않는다**(2026-08-22 6488.TWO·6239.TW):
    원천에 25.3Q 가 없어 표가 25.1Q·25.2Q·**25.4Q**·26.1Q 로 이어졌는데,
    옛 코드는 `qs[-4:]` 를 그냥 더해 **5분기에 걸친 합**을 'TTM' 이라 불렀다
    (거기서 TTM PER·PSR 까지 파생됐다). MSFT 의 15개월 TTM EPS(#138)와
    같은 병이 다른 모듈에 있던 것이다 — 화면은 결측 분기를 각주로 이미
    말하고 있었는데 합산만 그걸 안 봤다."""
    if len(qs) < 4:
        return {}
    window = qs[-4:]
    if window_gaps(window):
        return {}
    bad = anomalous_keys(window)
    out: dict = {}
    # ⚠️ **지배주주순이익도 합한다** — 밸류에이션 탭의 TTM PER 은 지배주주
    # 귀속분을 분자로 쓴다(FnGuide 산식, #193). 여기만 연결 총액으로 나누면
    # 같은 종목의 TTM PER 이 탭마다 갈린다(사용자 2026-08-23 "분기실적탭에
    # TTM PER 는 다른곳과도 일치되는게 맞는거겠지?"). 같은 계산을 하는 두
    # 화면은 같은 분자를 써야 한다(#38·#147).
    for k in ("매출", "영업이익", "당기순이익", "지배주주순이익"):
        if k in bad:
            continue
        vals = [(q.get("financials") or {}).get(k) for q in window]
        if all(v is not None for v in vals):
            out[k] = sum(vals)
    return out


_TTM_KEYS = ("매출", "영업이익", "당기순이익")


def window_gaps(window: list) -> list:
    """TTM 창 안의 **빠진 달력 분기** 라벨. 판정은 `missing_quarters` 하나에서
    온다 — 복제하면 각주와 합산이 서로 다른 답을 낸다(#38)."""
    try:
        from bot.quarterly_series import missing_quarters
        return missing_quarters(window)
    except Exception:                                          # noqa: BLE001
        return []


def ttm_missing_why(qs: list) -> dict:
    """`_ttm` 이 **만들지 않은** 항목 → 사람이 읽는 사유.

    ⚠️ 값을 비우는 코드는 그 자리에서 **사유를 같이 남겨야** 한다 — 안 그러면
    사용자가 "매출액이 비는건 왜 그런거야?" 라고 물어야 한다(사용자 2026-08-22
    603259.SS: 25.3Q 매출이 원천에 없어 TTM 매출·PSR 이 통째로 빈 화면).
    FCF·수주잔고는 이미 사유를 적고 있었는데 TTM 만 침묵했다(#43·#129 —
    이 세션에서 다섯 번째다).

    ⚠️ `_ttm` 과 **정확히 반대**여야 한다(만든 항목엔 사유가 없고, 안 만든
    항목엔 반드시 있다) — 회귀가 그 불변식을 고정한다. 두 곳에 판정을 적으면
    언젠가 갈라지므로, 여기서 쓰는 조건은 `_ttm` 과 같은 것만 본다(#38).
    """
    if len(qs) < 4:
        return {k: f"분기 표본 {len(qs)}개(4개 필요)" for k in _TTM_KEYS}
    window = qs[-4:]
    gaps = window_gaps(window)
    if gaps:
        return {k: f"{'·'.join(gaps)} 결측 — 연속 4분기가 아님"
                for k in _TTM_KEYS}
    bad = anomalous_keys(window)
    why: dict = {}
    for k in _TTM_KEYS:
        if k in bad:
            why[k] = "창 안에 이상치 분기가 있어 합산 제외"
            continue
        gaps = [_q_label(q) for q in window
                if (q.get("financials") or {}).get(k) is None]
        if gaps:
            why[k] = f"{'·'.join(gaps)} 원천 미제공(4분기 합 불가)"
    return why


def _q_label(q: dict) -> str:
    """각주에 쓰는 분기 라벨. 없으면 기간 문자열로 되돌아간다."""
    return str(q.get("label") or q.get("period") or "?")


# PER 범위 가드 — dart_feed._compute_per 와 동일 기준(음수·비현실 값 차단).
_PER_MIN, _PER_MAX = 0.0, 500.0


def self_per(market_cap, ttm_net_income) -> float | None:
    """시총 ÷ TTM 순이익. 야후가 trailingPE 를 안 주는 종목(보험·금융지주
    에서 흔하다)의 폴백 — 두 값 모두 payload 안에 이미 있어 추가 호출 0.
    전 시장 공통(통화가 같을 때만 호출할 것 — 호출부 책임)."""
    if not market_cap or not ttm_net_income or ttm_net_income <= 0:
        return None
    per = market_cap / ttm_net_income
    return per if _PER_MIN < per < _PER_MAX else None


# 지원 시장 — KR 은 DART(단일분기 원천), 그 외는 yfinance 분기 손익.
SUPPORTED_MARKETS = ("KR", "US", "JP", "TW", "CN_A", "HK")

# 시장 → 거래소 라벨 폴백(스냅샷 exchange 가 비었을 때).
_MARKET_LABEL = {"US": "US", "JP": "TSE", "TW": "TWSE", "CN_A": "A주",
                 "HK": "HKEX"}


def _live_quote(ticker: str, market: str, shares: float | None = None) -> dict:
    """장중 라이브 시세 {price, mcap}. 실패하면 {}(호출부가 스냅샷 폴백).

    `dashboard.build_live_quote` 의 LIGHT 경로와 **같은 소스**를 쓴다 —
    KR=네이버 국내, US/JP/HK/CN=네이버 해외, TW=TWSE. 키 불필요·₩0·
    VM 차단 없음. 두 화면이 다른 소스를 쓰면 같은 종목의 시총이 탭마다
    달라진다(사용자 2026-08-16 "스냅샷이 되면 안돼").

    ⚠️ **해외 시총은 단위 sanity 통과 시에만** 채택한다. 네이버 해외
    `marketValueFullRaw` 는 단위가 불확실해 원/달러가 섞여 나오는 사례가
    있고(`market_favorites.py` 가 같은 이유로 같은 게이트를 건다), 그대로
    쓰면 PSR·시총이 통째로 자릿수가 틀린다(2026-08-16 독립 리뷰).
    주식수를 모르면 검증할 수 없으므로 해외 시총은 채택하지 않는다 —
    스냅샷 시총으로 폴백하는 편이 안전하다. KR 국내는 원 단위가 확정이다."""
    try:
        if market == "KR":
            from bot.naver_quote import fetch_kr_quote
            q = fetch_kr_quote(ticker)
        elif market == "TW":
            from bot.tw_quote import fetch_tw_quote
            q = fetch_tw_quote(ticker)
        elif market in ("US", "JP", "HK", "CN_A"):
            from bot.world_quote import fetch_world_quote
            q = fetch_world_quote(ticker)
        else:
            return {}
    except Exception as exc:
        log.debug("quarterly_infographic: live quote %s: %s", ticker, exc)
        return {}
    out: dict = {}
    for k in ("price", "mcap"):
        v = (q or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[k] = float(v)
    if "mcap" in out and market != "KR":
        implied = (out.get("price") or 0) * (shares or 0)
        if not implied or not (0.5 <= out["mcap"] / implied <= 2.0):
            log.debug("quarterly_infographic: %s 해외 시총 단위 sanity 실패 "
                      "(mcap=%s implied=%s) — 스냅샷 사용", ticker,
                      out["mcap"], implied or None)
            out.pop("mcap")
    return out


def _dart_name(dart, ticker: str) -> str | None:
    """DART corp_code 맵의 회사명(디스크 캐시 · 네트워크 0). 실패 시 None."""
    if not dart:
        return None
    try:
        return dart.stock_code_to_name((ticker or "").upper().split(".")[0])
    except Exception as exc:
        log.debug("quarterly_infographic: corp name %s: %s", ticker, exc)
        return None


def build_payload(ticker: str, snap: dict | None = None, *,
                  run_llm: bool = False) -> dict | None:
    """분기 시계열 + 스냅샷(시총/PER) + (선택)LLM 카드 → 렌더 payload.

    KR = DART 정기보고서(단일분기 원천), 그 외 = yfinance 분기 손익계산서
    (스냅샷에 이미 8분기치가 전 시장 공통으로 수집돼 있어 추가 호출 0 —
    사용자 2026-08-16 '다른 나라도'). 분기 데이터가 없으면 None.
    """
    import time as _bp_time

    from bot.market import detect_market
    from bot.quarterly_series import fiscal_note, series_from_yfinance
    t = (ticker or "").upper()
    mkt = detect_market(t)
    if mkt not in SUPPORTED_MARKETS:
        return None
    snap = snap or {}
    is_kr = mkt == "KR"
    dart = None
    if is_kr:
        try:
            from bot.dart_client import get_dart
            from bot.dart_quarterly import get_quarterly_series
            dart = get_dart()
            _bt0 = _bp_time.time()
            qs = get_quarterly_series(dart, t, n=5)
            _RENDER_TIMING.set(timing_key(t, run_llm), "bp.series", _bp_time.time() - _bt0)
        except Exception as exc:
            log.warning("quarterly_infographic: series %s: %s", t, exc)
            return None
        # ⚠️ 제품·가동률 표는 핸들러가 **나중에** 부르는데(실측 최대 76초)
        # 여기서 이미 같은 정기보고서를 걷는다 — 미리 데워 두면 아래 수주잔고
        # (실측 최대 83초)와 **동시에** 돌아 직렬 합이 최대값 하나로 줄어든다.
        try:
            from bot.dart_production import prefetch_tables
            prefetch_tables(dart, t, qs)
        except Exception as exc:                               # noqa: BLE001
            log.debug("quarterly_infographic: 표 미리받기 건너뜀: %s", exc)
        # ⚠️ 수주잔고는 분기마다 40MB 상한으로 원문을 받아 훑는다 — 따로
        # 재지 않으면 `build_payload` 51.5초가 어디서 나는지 알 수 없다(#69).
        _bt0 = _bp_time.time()
        _fill_backlog(dart, t, qs)
        _RENDER_TIMING.set(timing_key(t, run_llm), "bp.backlog", _bp_time.time() - _bt0)
    else:
        # KR 은 야후로 폴백하지 않는다 — DART 가 단일분기를 직접 주므로
        # 소스를 섞으면 같은 화면에서 숫자 성격이 달라진다.
        qs = series_from_yfinance(snap, n=5)
    if not qs:
        return None
    latest = qs[-1]
    ttm = _ttm(qs)
    # 스냅샷 최상위는 snake `market_cap` 하나뿐 — 옛 camel 시도는 항상
    # 죽은 코드였다(stock_snapshot.py 는 marketCap 을 그 이름으로 저장하지 않음).
    # 라이브 시총 우선 — 스냅샷 시총은 (a) 마지막 수집 시각에 얼어붙고
    # (b) KR 은 캐시가 cold 면 아예 없어 '—' 로 뜬다(사용자 2026-08-16
    # "일주일 후에 다시 보면 그때 시점의 시총이 되어야 돼").
    live = _live_quote(t, mkt, snap.get("shares_outstanding"))
    mcap = live.get("mcap") or snap.get("market_cap")
    # 통화 — 재무제표는 financialCurrency 기준, 시총·주가는 currency 기준.
    trade_cur = (snap.get("currency") or "").upper()
    fin_cur = (snap.get("financial_currency") or "").upper() or trade_cur
    if is_kr:
        fin_cur = fin_cur or "KRW"
        trade_cur = trade_cur or "KRW"
    # 통화 불일치 HARD GUARD — HK 본토 자회사는 거래 HKD·재무 CNY 가 흔하다
    # (CLAUDE_REFERENCE: yfinance financialCurrency mismatch HK > JP 빈도).
    # 시총 ÷ 매출·순이익은 서로 다른 통화라 그냥 나누면 틀린 배수가 된다.
    cur_mismatch = bool(trade_cur and fin_cur and trade_cur != fin_cur)

    # 소스 제공 PER 은 **후행(TTM) · 선행(Forward) 둘 다 같은 규칙**으로
    # 정제한다. 옛 코드는 trailingPE 를 그대로 썼는데, 그러면 (a) 문자열이
    # 오면 f-string 포맷에서 터져 렌더 전체가 죽고 (b) 자체계산(self_per)엔
    # 걸리는 _PER_MIN/_PER_MAX 범위 가드가 소스값엔 안 걸려 같은 타일에
    # 'TTM 1,234.50배 / Fwd N/A' 같은 비대칭이 난다(2026-08-16 독립 리뷰).
    # 둘 다 거래통화 안에서 계산돼 오므로 통화 불일치 가드는 불필요하다
    # (자체계산 self_per 과 다른 점 — 그쪽은 시총÷재무통화 순이익).
    def _clean_per(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if _PER_MIN < f < _PER_MAX else None

    # PER 을 라이브화하되 **다른 탭과 같은 분모**를 쓴다.
    #   1순위: 라이브 주가 ÷ 야후 EPS — `dashboard.build_live_quote` 가
    #          PER/PBR 을 재산출하는 것과 **똑같은 공식**이라 분기실적 탭과
    #          종합·밸류에이션 탭의 PER 이 어긋나지 않는다.
    #   2순위: 스냅샷 trailingPE(수집 시점 주가로 굳은 값)
    #   3순위: 시총 ÷ DART TTM 순이익 자체계산(`*` 각주로 출처 표기)
    # 옛 시도는 곧장 3순위를 우선했는데, 그러면 같은 종목의 PER 이 탭마다
    # 달라 보인다(연결 총이익 vs 지배주주 EPS 분모, 2026-08-16 독립 리뷰).
    # ⚠️ EPS 는 재무통화 기준이라 거래통화 주가와 나누려면 통화가 같아야
    # 한다 — cur_mismatch 면 1순위·3순위 모두 건너뛴다.
    def _live_per(eps_key):
        eps = snap.get(eps_key)
        if (live.get("price") and not cur_mismatch
                and isinstance(eps, (int, float)) and not isinstance(eps, bool)
                and eps > 0):
            return _clean_per(live["price"] / eps)
        return None

    per_self = False
    per = _live_per("trailingEps") or _clean_per(snap.get("trailingPE"))
    if per is None and not cur_mismatch:
        # 야후가 trailingPE·EPS 를 안 주는 종목(보험·금융지주에서 흔함) 폴백 —
        # 옛 코드는 단일 소스라 그냥 '—' 였다(사용자 2026-08-16).
        per = self_per(mcap, ttm_net(ttm)[0])
        per_self = per is not None
    # Forward PER — 라이브 주가 ÷ 야후 forwardEps(컨센서스라 장중 불변).
    # ⚠️ 국내는 원천이 다르다: 야후가 KR `forwardEps` 를 안 줘서 밸류에이션
    # 탭은 **네이버(FnGuide) 추정 EPS** 로 만든다(#206·사용자 2026-08-23).
    # 여기만 야후 forwardPE 를 쓰면 같은 종목의 선행 PER 이 탭마다 갈린다
    # (#38·#147) — 규칙은 `kr_forward_from_naver` 하나뿐이고 여기서도 그걸
    # 부른다. 국내인데 네이버 추정치가 없으면 **비운다**(야후 폴백 금지 —
    # 커버리지가 달라 화면의 다른 칸과 기준이 어긋난다).
    per_fwd = None
    if is_kr:
        from bot.dashboard import kr_forward_from_naver
        _kr_px = live.get("price") or snap.get("current_price")
        # ⚠️ **아카이브 스냅샷엔 `naver_val` 이 없다** — 밸류에이션 탭은
        # 상세 보강이 채워 주지만 이 인포그래픽은 저장된 스냅샷을 그대로
        # 받는다. 그래서 화면엔 `PER (선행) 13.16x` 가 있는데 카드는
        # `Fwd N/A` 였다(사용자 2026-08-24 NHN KCP). 없으면 여기서 받는다
        # (#198 새 필드를 더할 때마다 '아카이브엔 없다'를 먼저 물을 것).
        _nv = (snap.get("kr") or {}).get("naver_val")
        if not _nv:
            try:
                from bot.naver_finance_client import get_naver_valuation
                _nv = get_naver_valuation(ticker)
            except Exception as exc:                           # noqa: BLE001
                log.debug("quarterly: naver_val %s: %s", ticker, exc)
        _kr_fe, _kr_fp = kr_forward_from_naver(_kr_px, _nv)
        per_fwd = _clean_per(_kr_fp) if (_kr_fp and not cur_mismatch) else None
    if per_fwd is None:
        # 사용자 2026-08-24: 국내도 **네이버 우선 · yfinance 폴백**이다
        # (밸류에이션 탭과 같은 규칙 — 여기만 다르면 탭마다 갈린다).
        per_fwd = _live_per("forwardEps")
    if per_fwd is None:
        # 야후가 forwardPE 는 주면서 forwardEps 는 안 주는 경우가 흔하다
        # (039030.KQ 실측 2026-08-16: forwardPE 30.72 · forwardEps None).
        # forwardPE = 수집시점주가 ÷ forwardEps 이므로 EPS 를 역산해 라이브
        # 주가로 다시 나누면 **정확히** 같은 컨센서스 기준의 현재 배수가 된다
        # (근사가 아니다 — 두 값이 같은 스냅샷에서 온다).
        _sp, _fpe = snap.get("current_price"), _clean_per(snap.get("forwardPE"))
        if (live.get("price") and _fpe and not cur_mismatch
                and isinstance(_sp, (int, float)) and _sp > 0):
            per_fwd = _clean_per(live["price"] / (_sp / _fpe))
    if per_fwd is None:
        per_fwd = _clean_per(snap.get("forwardPE"))
    psr = None
    _ttm_rev = ttm.get("매출")
    # ⚠️ 분모가 **총액이 아니면** PSR 을 만들지 않는다. NH투자증권처럼 총수익
    # 계정을 공시하지 않는 금융사는 '매출' 자리에 이자수익이 들어가, 그대로
    # 나누면 실제보다 몇 배 높은 PSR 이 나온다(사용자 2026-08-19).
    _rev_is_comp = any("매출" in ((q.get("financials") or {}).get(
        "_component_accounts") or {}) for q in (qs[-4:] if len(qs) >= 4 else qs))
    # 음수 분모 가드 — 옛 코드는 truthy 검사만 해서 매출이 음수여도 그대로
    # 나눠 PSR -1.87배를 찍었다.
    if mcap and _ttm_rev and _ttm_rev > 0 and not cur_mismatch and not _rev_is_comp:
        psr = mcap / _ttm_rev
    _window = qs[-4:] if len(qs) >= 4 else qs
    _fs_div = latest.get("fs_div")
    if is_kr:
        src_label = (f"DART 정기보고서"
                     f"(K-IFRS {'연결' if _fs_div == 'CFS' else '별도'})")
        mkt_label = "KOSPI" if t.endswith(".KS") else "KOSDAQ"
    else:
        src_label = "yfinance 분기 손익계산서"
        mkt_label = snap.get("exchange") or _MARKET_LABEL.get(mkt, mkt)
    payload = {
        "ticker": t,
        # 스냅샷은 long_name(스네이크) 키를 쓴다 — longName/shortName 은
        # 절대 안 잡혀 폴백이 죽어 있었다(2026-08-19 code-review).
        # KR 은 스냅샷이 cold 여도 DART corp_code 맵(디스크 캐시)에서 회사명을
        # 얻는다 — 그게 없으면 헤더가 '039030.KQ' 로 뜬다(독립 리뷰).
        "company": ((snap.get("kr", {}) or {}).get("corp_name")
                    or snap.get("long_name") or _dart_name(dart, t) or t),
        "market": mkt_label,
        "market_cap": mcap,
        # 현재가 — 헤더가 시총 옆에 같이 보인다(사용자 2026-08-21 "시가총액
        # 앞에 현재가도 추가해주고 … 전 나라 공통"). 라이브가 우선이고
        # 없으면 스냅샷. 거래통화 기준(시총과 같은 통화).
        "price": live.get("price") or snap.get("current_price"),
        "quarters": qs,
        "ttm": ttm,
        # 값을 비운 자리엔 사유를 같이 남긴다(#43·#129) — FCF·수주잔고는
        # 이미 적고 있었는데 TTM 만 침묵했다(사용자 2026-08-22 603259.SS).
        "ttm_why": ttm_missing_why(qs),
        "per": per,
        "per_forward": per_fwd,
        # 야후 제공값과 자체계산을 구분해 표기(출처 표기 의무).
        "per_self": per_self,
        "psr": psr,
        "currency": fin_cur or "KRW",
        "trade_currency": trade_cur or fin_cur or "KRW",
        "currency_mismatch": cur_mismatch,
        "fiscal_note": fiscal_note(snap.get("fiscal_year_end")) if not is_kr else "",
        # 수주잔고 빈 분기 사유(각주). `_fill_backlog` 이 최신 분기에 심는다.
        "backlog_missing": (qs[-1].get("_meta") or {}).get("backlog_missing")
        if qs else None,
        # 분기마다 **왜** 비었는지(원문에 표 없음 / 형식 미지원 / 명시적
        # 미공시 …). `backlog_probe` 가 이미 알려주는데 옛 코드는 값만
        # 꺼내고 버렸다 — 아는 걸 화면이 말하게 한다(#123).
        "backlog_why": (qs[-1].get("_meta") or {}).get("backlog_why")
        if qs else None,
        # FCF 빈 분기 사유 — `attach_to_series` 가 분기마다 심는다(#129 의
        # 짝: 재료가 없어 비우는 건 옳지만 **왜** 비었는지는 말해야 한다).
        "fcf_why": {q.get("label") or "?": (q.get("_meta") or {})["fcf_why"]
                    for q in (qs or [])
                    if (q.get("_meta") or {}).get("fcf_why")} or None,
        # 분기 간 배수가 비정상이면 어느 한 분기의 파싱이 틀렸다는 뜻 —
        # 숫자를 지우지 않고 '의심'을 화면에 밝힌다(어느 쪽이 틀렸는지는
        # 원문 없이 못 가른다).
        "backlog_spread": (qs[-1].get("_meta") or {}).get("backlog_spread")
        if qs else None,
        # LLM 성장동력/리스크는 DART 원문 전용 — 비-KR 은 버튼 자체를 숨긴다
        # (누를 수 없는 버튼을 보여주지 않는다).
        "llm_supported": is_kr,
        # 렌더러가 '어느 항목이·어느 분기가' 신뢰 불가인지 표시하도록 전달.
        # 플래그를 만들어 두고 렌더러가 안 읽던 게 -10.30조가 그대로
        # 화면에 나간 이유였다(dashboard 표에만 배지가 있었음).
        # 두 값의 창을 맞춘다 — labels 만 5분기로 잡으면 TTM 창 밖 분기가
        # 빨간 x라벨로만 뜨고 각주가 안 붙는다(2026-08-16 독립 리뷰).
        "anomaly_keys": sorted(anomalous_keys(_window)),
        "anomaly_labels": anomalous_labels(_window),
        "mismatched_accounts": mismatched_accounts(_window),
        # 구성요소 계정에서 온 값 — 이상치와 달리 **값은 유효**하므로 TTM 을
        # 막지 않고 표기만 한다(총매출이 아니라는 사실만 알림).
        # 총액 미공시사 보강 출처(FnGuide) — 어느 값이 어디서 왔는지 표기.
        "revenue_source": next(
            (q["financials"]["_revenue_source"] for q in _window
             if (q.get("financials") or {}).get("_revenue_source")), ""),
        "component_accounts": {
            k: v for q in _window
            for k, v in ((q.get("financials") or {}).get(
                "_component_accounts") or {}).items()},
        "latest_year": latest.get("year"),
        "latest_reprt_code": latest.get("reprt_code"),
        # 캐시 키 — KR 은 (연도+보고서코드), 그 외는 분기 종료일.
        "period_key": (f"{latest.get('year')}{latest.get('reprt_code')}"
                       if is_kr else
                       str(latest.get("period", "")).replace("-", "")),
        "source_label": src_label,
        # 시세 기반 값(시총·PER·PSR)의 기준시각 = 렌더 시각(KST, 시 단위).
        # 캐시 키와 **같은 값**이라 화면 스탬프와 재렌더 주기가 어긋나지 않는다.
        "asof": _now_hour_kst(),
    }
    if not is_kr:
        payload["growth_risk"] = {"ok": False, "error": "not_supported"}
        return payload
    try:
        from bot.dart_growth_risk import build_growth_risk
        # ⚠️ LLM 에도 **실제 계정명**을 준다 — '매출'이라 넘기면 요약 headline 이
        # "매출 5787억, 영업이익 6812억" 처럼 모순돼 보인다(사용자 2026-08-19
        # NH투자증권). 이 회사는 총수익 계정을 공시하지 않아 이자수익이 그
        # 자리에 온다.
        _ctx_rev = ((latest.get("financials") or {}).get(
            "_component_accounts") or {}).get("매출") or "매출"
        ctx = {"분기": latest.get("label"),
               _ctx_rev: (latest.get("financials") or {}).get("매출"),
               "영업이익": (latest.get("financials") or {}).get("영업이익"),
               "당기순이익": (latest.get("financials") or {}).get("당기순이익")}
        payload["growth_risk"] = build_growth_risk(
            dart, t, latest.get("year"), latest.get("reprt_code"), ctx,
            company=payload["company"], run_llm=run_llm)
    except Exception as exc:
        log.warning("quarterly_infographic: growth_risk %s: %s", t, exc)
        payload["growth_risk"] = {"ok": False, "error": "요약 실패"}
    return payload


def table_html(payload: dict) -> str:
    """PNG 폴백 — 서버에 한글 폰트가 없어 렌더가 None 일 때 같은 숫자를 표로.

    ⚠️ 컬럼은 **오래된→최신**(최신이 오른쪽) — 이 표가 대신하는 PNG 차트가
    그 방향이고, 같은 페이지의 밸류에이션 탭 분기표도 2026-08-16 사용자
    지시로 같은 방향이 됐다. 여기만 뒤집혀 있으면 폰트 없는 서버에서 한
    화면의 두 분기표가 서로 반대를 가리킨다(2026-08-16 독립 리뷰)."""
    import html as _h
    qs = list(payload.get("quarters") or [])
    if not qs:
        return ""
    cur = payload.get("currency") or "KRW"
    head = "<th>항목</th>" + "".join(
        f"<th class='num'>{_h.escape(str(q.get('label','')))}</th>" for q in qs)
    rows = ""
    # 폰트 없는 서버에서는 이 표가 유일한 화면이다 — PNG 와 **같은 이름**을
    # 써야 한다(구성요소 계정이면 '이자수익' 등, 사용자 2026-08-19).
    _rev_nm = (payload.get("component_accounts") or {}).get("매출") or "매출"
    for label, key in ((_rev_nm, "매출"), ("영업이익", "영업이익"),
                       ("당기순이익", "당기순이익")):
        cells = "".join(
            f"<td class='num'>{_eok((q.get('financials') or {}).get(key), cur)}</td>"
            for q in qs)
        rows += f"<tr><td>{_h.escape(label)}</td>{cells}</tr>"
    for label, key in (("영업이익률", "영업이익률"), ("순이익률", "순이익률")):
        cells = "".join(
            f"<td class='num'>{_pct((q.get('ratios') or {}).get(key))}</td>"
            for q in qs)
        rows += f"<tr><td>{_h.escape(label)}</td>{cells}</tr>"
    tbl = ('<table class="si-table"><thead><tr>' + head
           + "</tr></thead><tbody>" + rows + "</tbody></table>")
    # 배수 요약 — PNG 타일·푸터와 같은 값을 같은 규칙으로. 이 표는 폰트가
    # 없어 PNG 를 못 만들 때 뜨는 **유일한 화면**이라, 여기에만 없으면
    # 그 경로에서 지표가 통째로 사라진다(실수 #10 표면 동기화).
    _mults = [
        ("TTM PER" + ("*" if payload.get("per_self") else ""),
         "—" if payload.get("per") is None else f"{payload['per']:,.2f}배"),
        ("Forward PER", "N/A" if payload.get("per_forward") is None
         else f"{payload['per_forward']:,.2f}배"),
        ("PSR", "—" if payload.get("psr") is None
         else f"{payload['psr']:,.2f}배"),
    ]
    tbl += ('<div style="font-size:12px;color:var(--fg-soft);margin-top:6px">'
            + " · ".join(f"{_h.escape(k)} <b>{_h.escape(v)}</b>"
                         for k, v in _mults) + "</div>")
    # 각주 — PNG 와 **같은 소스**(_footnotes)를 쓴다. 옛 코드는 구성요소
    # 계정 한 줄만 따로 손으로 복제해, 통화 불일치·이상치로 값이 '—' 가
    # 됐을 때 그 이유가 이 화면에서만 사라졌다(폰트 없는 서버에서 이게
    # 유일한 화면이다 — 2026-08-16 독립 리뷰). 색은 PNG 팔레트를 그대로.
    _fn = _footnotes(payload, payload.get("quarters") or [])
    if _fn:
        tbl += "".join(
            f'<div style="font-size:11px;color:{c};margin-top:4px">'
            f'{_h.escape(t)}</div>' for t, c in _fn)
    return tbl


# 단계별 소요시간(마지막 호출) — `/api/quarterly` 가 **108초**로 관측됐는데
# (2026-08-21 `api-timing` 실측, 300120.KQ) 그중 무엇이 payload 수집이고
# 무엇이 PNG 렌더인지 알 방법이 없었다. 추측하지 말고 잰다(#69).
# ⚠️ **티커별**로 가른다 — 전역 dict 하나면 동시 요청이 서로를 덮어 로그가
# 다른 종목의 값을 말한다(2026-08-22 실측, bot/timing.py 참조).
from bot.timing import Stages as _Stages

_RENDER_TIMING = _Stages()


def timing_key(ticker: str, run_llm: bool = False) -> str:
    """계측 키 — **티커만으로는 부족하다**(2026-08-22 실측).

    single-flight 키가 `quarterly:{ticker}:{run}` 이라 `run` 이 다른 두
    요청은 **같은 티커로 동시에** 돈다. 티커만 키로 쓰면 서로의 단계를
    덮어써서 로그가 한 줄에 **두 실행의 값을 섞어** 찍는다 — 실측:
    `051910.KS render_png=106.249s total=17.528s build_payload=17.514s
    cached=1.0s`(캐시 히트인데 렌더 106초가 붙어 있다). #117 을 고치며
    단위를 티커로 잡은 것이 여기서 부족했다 — 키는 **요청**이어야 한다.
    """
    return f"{ticker}:{int(bool(run_llm))}"


def last_render_timing(ticker: str = "", run_llm: bool = False) -> dict:
    """그 요청 `get_or_render` 의 단계별 소요(초). 읽기 전용."""
    return _RENDER_TIMING.snapshot(timing_key(ticker, run_llm))


def get_or_render(ticker: str, snap: dict | None = None, *,
                  run_llm: bool = False) -> dict:
    """온디맨드 진입점. 캐시(파일명=분기 키) 우선, 없으면 렌더.
    반환 {ok, image, payload} — image 는 PNG 경로(폰트 부재 시 None)."""
    import time as _time
    _tk = timing_key(ticker, run_llm)
    _RENDER_TIMING.start(_tk)
    _t_all = _t0 = _time.time()
    payload = build_payload(ticker, snap, run_llm=run_llm)
    _RENDER_TIMING.set(_tk, "build_payload", _time.time() - _t0)
    _t_pre = _time.time()      # ⚠️ 리셋 — 안 하면 pre_render 가 build_payload
                               # 와 **같은 값**이 나와 빈 구간을 못 본다(실측)
    if not payload:
        return {"ok": False,
                "error": "분기 재무 데이터 없음(소스 미제공 또는 미지원 시장)"}
    p = cache_path(ticker, payload.get("period_key") or "na",
                   asof=payload.get("asof"))
    # LLM 카드가 **이번에 새로** 붙었을 때만 기존 PNG 를 버린다.
    # ⚠️ 옛 조건은 `ok and run_llm` 이었다 — 탭을 열면 항상 run_llm=True 가
    # 된 뒤로(사용자 2026-08-16 자동 실행) 캐시가 **영구 미스**가 되어 매
    # 조회마다 전체 인포그래픽을 다시 그리고 저장했다(2026-08-16 독립 리뷰).
    # `cached` 는 dart_growth_risk 가 캐시에서 꺼냈을 때만 True 다.
    _gr = payload.get("growth_risk") or {}
    fresh_llm = bool(_gr.get("ok")) and not _gr.get("cached")
    # 카드 전용 PNG(사용자 2026-08-20 — 생산능력 표가 카드 위로 와야 해서
    # 분리). 본 이미지와 **같은 캐시 키**에 접미사만 붙여 함께 갱신된다.
    pc = p.with_name(p.stem + "_cards" + p.suffix)
    # 하단 조각(수주잔고·재고자산·TTM) — 사용자 2026-08-21 배치에서 표가
    # 이 조각 **위**로 와야 해서 본 이미지를 둘로 나눴다.
    pb = p.with_name(p.stem + "_b" + p.suffix)
    # ⚠️ 여기까지가 `build_payload` 뒤·렌더 앞이다. 2026-08-22 실측
    # `build_payload=15.7s render_png=35.5s total=229.469s` — 합이 51초인데
    # 총합이 229초였다. **측정되지 않은 구간이 있으면 그게 범인이다**(#69)
    # → 남는 구간을 전부 이름 붙여 다음 줄이 스스로 답하게 한다.
    _RENDER_TIMING.set(_tk, "pre_render", _time.time() - _t_pre)
    if p.exists() and not fresh_llm:
        _t0 = _time.time()
        # 짝 PNG 가 없으면 **여기서** 그린다 — 캐시 경로인데도 렌더가 돈다.
        _bot = str(pb) if pb.exists() else render_infographic(
            payload, str(pb), _PART_BOTTOM, "bottom", _tk)
        _crd = str(pc) if pc.exists() else render_cards(payload, str(pc),
                                                        "cards", _tk)
        _RENDER_TIMING.set(_tk, "render_parts", _time.time() - _t0)
        _RENDER_TIMING.set(_tk, "cached", 1.0)
        _RENDER_TIMING.set(_tk, "total", _time.time() - _t_all)
        # 짝이 없으면 **여기서 만든다** — 본 이미지만 캐시에 남아 있으면
        # 그 조각이 화면에서 통째로 사라진 채 캐시 키가 도는 날까지 방치된다
        # (한 번의 렌더 실패가 영구 결손이 되는 구멍, 실수 #11).
        # 내용이 없는 종목이면 렌더러가 matplotlib 전에 None 을 낸다.
        return {"ok": True, "image": str(p), "image_bottom": _bot,
                "cards_image": _crd, "payload": payload, "cached": True}
    _t0 = _time.time()
    img = render_infographic(payload, str(p), _PART_TOP, "top", _tk)
    bottom = render_infographic(payload, str(pb), _PART_BOTTOM, "bottom", _tk)
    cards = render_cards(payload, str(pc), "cards", _tk)
    _RENDER_TIMING.set(_tk, "render_png", _time.time() - _t0)
    _RENDER_TIMING.set(_tk, "total", _time.time() - _t_all)
    if img:
        _purge_stale(ticker, p)
    return {"ok": True, "image": img, "image_bottom": bottom,
            "cards_image": cards, "payload": payload, "cached": False}


def _purge_stale(ticker: str, keep: Path) -> None:
    """같은 티커의 옛 PNG 정리. 캐시 키에 **시**가 들어가 하루 최대 24장씩
    쌓이는데(분기·렌더버전까지 곱해지면 더) 지우는 곳이 아무 데도 없었다
    (2026-08-16 독립 리뷰). 방금 만든 것만 남긴다 — 옛 파일은 캐시 키가
    달라 어차피 다시 안 읽힌다."""
    # ⚠️ 카드 전용 PNG(`*_cards.png`)도 **살려 둔다** — 같은 캐시 키의 짝이라
    # 여기서 지우면 방금 만든 카드 이미지가 사라져 화면에서 카드가 통째로
    # 빠진다(2026-08-20 분리 작업 중 실측한 함정: 본 이미지 렌더 직후
    # _purge_stale 이 짝을 삭제).
    # ⚠️ 조각이 늘 때마다 여기를 손대야 한다 — 이름을 열거하지 말고 접미사
    # 목록에서 만든다(조각을 추가하고 purge 를 잊으면 방금 만든 그림이
    # 바로 지워진다, 2026-08-20 카드 분리 때 실측한 함정).
    keep_all = {keep} | {keep.with_name(keep.stem + sfx + keep.suffix)
                         for sfx in _PIECE_SUFFIXES}
    try:
        for old in _IMG_DIR.glob(f"{_safe_name(ticker)}_*.png"):
            if old not in keep_all:
                old.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("quarterly_infographic: 옛 PNG 정리 실패: %s", exc)
