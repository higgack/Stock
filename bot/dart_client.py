"""Thin client for DART (전자공시시스템) OpenAPI.

Provides three pieces of KR-market data that yfinance either doesn't
have or returns garbage for:

1. Recent disclosures (공시) — what the company filed in the last
   N days; surfaces guidance updates, lawsuits, M&A announcements
   etc. that move the stock but never show up in English news feeds.
2. Insider / major shareholder holdings (임원·주요주주 지분) — Form 4
   equivalent for Korea. yfinance returns 0% / N/A for these on
   KRX-listed names.
3. Next-earnings window estimate — DART doesn't publish a 'next
   earnings date' field, but quarterly reports are due within 45
   days of quarter end by law, so we infer a window from the
   current date and Q-end pattern.

Design goals:
- **Graceful degradation**: if `DART_API_KEY` is unset, the network
  is down, or DART returns an error, every method returns empty / None
  and logs a warning. The rest of the bot must keep running for non-KR
  analyses, and a KR analysis with missing DART data is better than no
  analysis at all.
- **Disk-cached corp_code mapping**: DART uses an 8-digit `corp_code`
  internal ID, not the 6-digit stock code. The mapping is downloaded
  as a zipped XML from `/api/corpCode.xml` and cached for 30 days at
  `~/.tradingagents/cache/dart_corpcode.json`. ~80k entries, ~3 MB.
- **No singleton magic**: callers construct `DartClient()` once and
  reuse it. Module-level `get_dart()` returns a process-wide cached
  instance.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.dart")

_DART_BASE = "https://opendart.fss.or.kr/api"
# 주식의 총수 탐색 예산 — 리터럴로 못박는다(자기 상수로 자기를 검증하면
# 상한을 올리는 뮤테이션이 그대로 통과한다, #66).
_SHARE_TOT_PROBE_N = 4
# 이력 조회 예산 — 리터럴로 못박는다(자기 상수로 자기를 검증하면 상한을
# 올리는 뮤테이션이 통과한다, #66).
_SHARE_TOT_SERIES_N = 8


def _share_int(v) -> Optional[int]:
    """DART 는 값이 없으면 '-' 를 준다. 숫자만 통과."""
    try:
        t = str(v).replace(",", "").strip()
    except Exception:
        return None
    if not t or not t.lstrip("-").isdigit():
        return None
    n = int(t)
    return n if n >= 0 else None


def _pick_share_totals(rows: list) -> dict:
    """주식의 총수 행에서 {issued, treasury, distributed} 를 고른다.

    ⚠️ `se`(구분) 는 회사마다 '합계' · '보통주' · '우선주' 로 나뉜다.
    FnGuide 분모는 **보통주+우선주 합계**라 합계 행이 있으면 그걸 쓰고,
    없으면 종류주 행을 **더한다**(하나만 쓰면 우선주가 통째로 빠진다).
    """
    tot = {"issued": 0, "treasury": 0, "distributed": 0}
    summed = False
    for r in rows or []:
        se = str(r.get("se") or "")
        # ⚠️ `isu_stock_totqy` 는 **발행할 주식의 총수(수권주식수)** 다 —
        # 실제 발행분은 `istc_totqy`. POSCO홀딩스 실측(2026-08-23): 전자가
        # 200,000,000 인데 실제 발행은 79,241,527 이었다. 이름이 그럴듯해
        # 발행주식수로 읽으면 자사주 차감이 통째로 틀린다(#50 내 가정을
        # 원천의 보장으로 착각하지 말 것).
        iss = (_share_int(r.get("istc_totqy"))
               or _share_int(r.get("now_to_isu_stock_totqy")))
        tes = _share_int(r.get("tesstk_co"))
        dis = _share_int(r.get("distb_stock_co"))
        if iss is None and dis is None:
            continue
        if "합계" in se:            # 합계 행이 있으면 그게 정답이다
            out = {"issued": iss or 0, "treasury": tes or 0,
                   "distributed": dis if dis is not None
                   else max((iss or 0) - (tes or 0), 0)}
            return out if out["distributed"] else {}
        if "주" in se:              # 보통주 · 우선주 등 종류주 행 → 합산
            tot["issued"] += iss or 0
            tot["treasury"] += tes or 0
            tot["distributed"] += (dis if dis is not None
                                   else max((iss or 0) - (tes or 0), 0))
            summed = True
    return tot if (summed and tot["distributed"]) else {}
_CACHE_DIR = Path.home() / ".tradingagents" / "cache"
# v2 cache includes both stock_code→corp_code and normalized_name→entries.
# Old v1 file (stock_code → corp_code only) is ignored and superseded;
# anyone upgrading just re-downloads the corp_code.xml on first call.
_CORPCODE_CACHE = _CACHE_DIR / "dart_corpcode_v2.json"
_CORPCODE_TTL_DAYS = 30
_HTTP_TIMEOUT = 10  # seconds — keep tight so a slow DART doesn't stall analysis
_HOT_CACHE_TTL_HOURS = 12  # disclosures / insider holdings change at most daily


def _cache_ver(ver: int):
    """`@_disk_cache_daily` 위에 얹어 **파서 버전**을 캐시 키에 싣는다.

    파싱 결과를 디스크에 캐시하는 함수는 파서를 고쳐도 그날의 캐시가
    옛 값을 그대로 준다(#21b — 이 레포에서 일곱 번째). 버전을 올리면
    고친 날 바로 다시 받는다.
    """
    def deco(fn):
        fn._cache_ver = ver
        return _disk_cache_daily(fn)
    return deco


def _disk_cache_daily(fn):
    """F3 (2026-05-29 audit): per-(stock_code, today) 12h disk cache for the
    DART network methods that previously hit the network on EVERY call.

    build_instrument_context runs up to 8× per analysis + the inline KR D1
    block, so `get_recent_disclosures` / `get_insider_holdings` were each
    fetched ~8× over the network per KR analysis — while every sibling KR
    client (pykrx, naver, krx_alert, kis) is already disk-cached. DART was
    the lone uncached outlier and the most likely 429 / stall point
    (CLAUDE.md warns of 'DART 429 / rate limits').

    Only caches TRUTHY results — an empty list (key missing / corp_code
    unresolved / transient DART error) is NOT cached, so a temporary
    failure doesn't get pinned for 12h and recovery is immediate.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(self, stock_code, *args, **kwargs):
        arg_sig = "_".join(str(a) for a in args)
        if kwargs:
            arg_sig += "_" + "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        # ⚠️ 파서를 고쳐도 **오늘치 캐시가 옛 결과를 서빙한다** — 2026-08-23
        # 실측: `get_share_totals` 의 발행주식수를 수권주식수에서 실제 발행분
        # 으로 고쳤는데 같은 날 프로브가 여전히 200,000,000 을 찍었다. 파싱
        # 결과를 디스크에 캐시하면 코드를 고쳐도 안 바뀐다(#21b). 파서가 바뀐
        # 함수는 `@_cache_ver(n)` 로 버전을 달아 키에 실는다.
        _ver = getattr(fn, "_cache_ver", 0)
        cache_path = _CACHE_DIR / (
            f"dart_{fn.__name__}_{stock_code}_{arg_sig}"
            + (f"_v{_ver}" if _ver else "")
            + f"_{date.today().isoformat()}.json"
        )
        if cache_path.exists():
            age_h = (time.time() - cache_path.stat().st_mtime) / 3600
            if age_h < _HOT_CACHE_TTL_HOURS:
                try:
                    return json.loads(cache_path.read_text("utf-8"))
                except Exception:
                    pass
        result = fn(self, stock_code, *args, **kwargs)
        if result:  # only cache non-empty (don't pin transient failures)
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(result, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return result

    return wrapper


def _normalize_name(name: str) -> str:
    """Lowercase + strip common Korean corporate suffixes / whitespace
    so '삼성전자', '삼성전자(주)', '주식회사 삼성전자' all collapse to
    the same key. Used to make name lookup forgiving of how the user
    types it vs how DART stores it."""
    n = (name or "").strip()
    for noise in ("(주)", "㈜", "(유)", "주식회사 ", "주식회사", "유한회사 ", "유한회사"):
        n = n.replace(noise, "")
    return n.strip().lower()


# ─────────────────────────────────────────────────────────────────────
# D1 Phase 2 (2026-05-19): DART 계정과목 정규화 + 재무비율 계산기
# StandardView (StanLee5767/standardview) 의 CODE_MAP / NAME_MAP /
# calc_ratios 패턴 차용 (라이선스 동의 받음 2026-05-19).
# 목적: yfinance 의 KR 종목 totalRevenue / 영업이익 등 단위 mismatch
# (SMIC 50x / 현대차증권 47x) 또는 financialCurrency=USD glitch 시
# DART 직접 정규화 데이터로 자동 override.
# ─────────────────────────────────────────────────────────────────────

# DART API 의 account_id → 통일 용어 매핑. K-IFRS / dart_ / us-gaap
# 표준 prefix 모두 cover. 같은 항목이 회사마다 다른 account_id 로
# 보고되므로 multi-source 매핑.
_DART_CODE_MAP: dict[str, str] = {
    "ifrs-full_Revenue": "매출",
    "ifrs-full_RevenueFromContractsWithCustomers": "매출",
    "dart_Revenue": "매출",
    "ifrs-full_CostOfSales": "매출원가",
    "dart_CostOfSales": "매출원가",
    "ifrs-full_GrossProfit": "매출총이익",
    "ifrs-full_SellingGeneralAndAdministrativeExpense": "판관비",
    "dart_SellingGeneralAdministrativeExpenses": "판관비",
    "ifrs-full_AdministrativeExpense": "판관비",
    "dart_OperatingIncomeLoss": "영업이익",
    "ifrs-full_ProfitLossFromOperatingActivities": "영업이익",
    "ifrs-full_FinanceIncome": "금융수익",
    "ifrs-full_FinanceCosts": "금융비용",
    "ifrs-full_ProfitLossBeforeTax": "세전이익",
    "ifrs-full_IncomeTaxExpenseContinuingOperations": "법인세비용",
    "ifrs-full_ProfitLoss": "당기순이익",
    "dart_ProfitLoss": "당기순이익",
    "us-gaap_NetIncomeLoss": "당기순이익",
    # ⚠️ **지배주주 귀속분** — 주당지표의 분자다. 사용자가 신뢰 기준으로
    # 제시한 FnGuide 산식(2026-08-23)이 명시한다:
    #   EPS = (지배주주지분)당기순이익 × 1000 / 수정평균발행주식수
    #   BPS = (지배주주지분)자본총계 / 수정기말발행주식수
    # 우리는 연결 **총액**(비지배 포함)으로 나눠 왔다. 총액과 지배주주분이
    # 크게 갈리는 회사에서는 EPS 가 통째로 부풀고, 값이 다 '있어서' 어떤
    # 감사도 안 걸린다(#96 채워진 칸은 틀려도 조용하다).
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "지배주주순이익",
    "ifrs-full_EquityAttributableToOwnersOfParent": "지배주주자본",
    "ifrs-full_BasicEarningsLossPerShare": "EPS",
    "ifrs-full_BasicEarningsPerShare": "EPS",
    "dart_BasicEarningsLossPerShare": "EPS",
    # 재고자산 — 분기실적 탭 '재고자산 추이' 차트(사용자 2026-08-16 "미래의
    # 수익을 가늠"). 저량 항목이라 4분기 차분 금지(_STOCK_KEYS 등재).
    "ifrs-full_Inventories": "재고자산",
    "dart_Inventories": "재고자산",
    "us-gaap_InventoryNet": "재고자산",
    # 현금흐름표 — FCF(= 영업활동현금흐름 − |CAPEX|) 재료. 사용자
    # 2026-08-21 "이건 모든 나라에 적용". 유량 항목이라 4분기는 차분으로
    # 파생된다(_STOCK_KEYS 에 넣지 않는다).
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "영업활동현금흐름",
    "dart_CashFlowsFromUsedInOperatingActivities": "영업활동현금흐름",
    "us-gaap_NetCashProvidedByUsedInOperatingActivities": "영업활동현금흐름",
    # ⚠️ CAPEX 는 DART 에 **단일 표준 계정이 없다** — 회사마다 유형자산·
    # 무형자산 취득을 따로 적는다. 둘을 합산해야 FnGuide CAPEX 와 맞는다.
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities":
        "유형자산취득",
    "dart_PurchaseOfPropertyPlantAndEquipment": "유형자산취득",
    "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities":
        "무형자산취득",
    "dart_PurchaseOfIntangibleAssets": "무형자산취득",
    "ifrs-full_CurrentAssets": "유동자산",
    "ifrs-full_NoncurrentAssets": "비유동자산",
    "ifrs-full_Assets": "자산총계",
    "us-gaap_Assets": "자산총계",
    "ifrs-full_CurrentLiabilities": "유동부채",
    "ifrs-full_NoncurrentLiabilities": "비유동부채",
    "ifrs-full_Liabilities": "부채총계",
    "ifrs-full_RetainedEarnings": "이익잉여금",
    "ifrs-full_Equity": "자본총계",
    "us-gaap_StockholdersEquity": "자본총계",
    # ⚠️ 여기 있던 `EquityAttributableToOwnersOfParent → 자본총계` 를
    # 지웠다. **같은 키가 위(지배주주자본)에도 있어 파이썬이 조용히 마지막
    # 것만 남겼고**, 그래서 `지배주주자본` 이 전 종목에서 한 번도 안 채워졌다
    # (POSCO홀딩스 005490.KS 프로브 실측 2026-08-23: 6분기 전부 `—`).
    # 결과로 BPS·ROE 분모가 연결 총액(비지배 포함)으로 떨어져 FnGuide 보다
    # 체계적으로 어긋났다. 자본총계가 이 계정으로만 오는 회사는 아래
    # `_fill_equity_fallbacks` 가 채운다 — 중복 키로 해결하지 않는다.
    # 비지배지분 — 지배주주자본을 원천이 안 줄 때 빼서 만든다.
    "ifrs-full_NoncontrollingInterests": "비지배지분",
    "ifrs-full_MinorityInterest": "비지배지분",
    # 비지배 **순이익** — 지배주주순이익을 원천이 안 줄 때 빼서 만든다.
    # 분자와 분모의 급이 갈리면 ROE 가 통째로 틀린다(#34).
    "ifrs-full_ProfitLossAttributableToNoncontrollingInterests": "비지배순이익",
}

# account_id 가 비표준이거나 nullable 시 account_nm (한글 텍스트) 매칭
# fallback. 회사마다 분기 / 사업보고서 양식 차이로 다른 alias 사용.
_DART_NAME_MAP: dict[str, str] = {
    "매출액": "매출", "수익(매출액)": "매출", "영업수익": "매출",
    "매출": "매출", "도급공사수익": "매출", "분양수익": "매출",
    # 보험사(IFRS17)는 총수익 계정이 없다 — 아래 셋은 전부 **구성요소**로
    # 잡히고(_ACCOUNT_GROUPS 그룹 2) 총액은 FnGuide 폴백이 채운다.
    # 2026-08-19 현대해상 실측: 보험영업수익 14.73조 · 보험수익 14.14조 ·
    # 투자영업수익 3.16조 — 어느 하나도 총수익이 아니다.
    "이자수익": "매출", "보험영업수익": "매출", "보험수익": "매출",
    "투자영업수익": "매출",
    "매출원가": "매출원가", "도급공사원가": "매출원가",
    "매출총이익": "매출총이익", "매출총이익(손실)": "매출총이익",
    "매출총손익": "매출총이익",
    "판매비와관리비": "판관비", "판매비및관리비": "판관비",
    "판매비와 관리비": "판관비", "영업비용": "판관비",
    "영업이익": "영업이익", "영업이익(손실)": "영업이익",
    "영업손익": "영업이익",
    "금융수익": "금융수익", "이자및배당금수익": "금융수익",
    "금융비용": "금융비용", "이자비용": "금융비용", "금융원가": "금융비용",
    "법인세비용차감전순이익": "세전이익",
    "법인세비용차감전순이익(손실)": "세전이익",
    "법인세차감전순이익": "세전이익",
    "법인세비용차감전순손익": "세전이익",
    "법인세비용": "법인세비용", "법인세수익": "법인세비용",
    "당기순이익": "당기순이익", "당기순이익(손실)": "당기순이익",
    "분기순이익": "당기순이익", "반기순이익": "당기순이익",
    "당기순손익": "당기순이익",
    # ⚠️ 이름 매칭은 **모호하지 않은 어구만**. `지배기업 소유주지분` 은
    # 손익계산서와 재무상태표에 **둘 다** 나오므로 그 자체로는 못 쓴다 —
    # 계정과목에 순이익/자본이 함께 적힌 것만 등재하고, 나머지는 표준
    # 태그(account_id)에 맡긴다. 제표 구분(_IS_KEYS/_BS_KEYS)이 2차 방어다.
    "지배기업의 소유주에게 귀속되는 당기순이익": "지배주주순이익",
    "지배기업 소유주지분 당기순이익": "지배주주순이익",
    "지배기업소유주지분 당기순이익": "지배주주순이익",
    "지배주주지분 당기순이익": "지배주주순이익",
    "지배기업의 소유주에게 귀속되는 자본": "지배주주자본",
    "지배기업 소유주지분 자본총계": "지배주주자본",
    "지배주주지분 자본총계": "지배주주자본",
    # 원천이 실제로 쓰는 다른 표기들 — 이름 열거는 새 표기를 못 잡으므로
    # (#24) 아래 `_fill_equity_fallbacks` 의 뺄셈이 최종 그물이다.
    "지배기업의 소유주에게 귀속되는 지분": "지배주주자본",
    "지배기업 소유주에게 귀속되는 자본": "지배주주자본",
    "지배기업소유주지분": "지배주주자본",
    "지배기업의 소유주지분": "지배주주자본",
    "지배기업 소유주지분": "지배주주자본",
    "비지배지분": "비지배지분",
    "비지배주주지분": "비지배지분",
    "비지배지분순이익": "비지배순이익",
    "비지배주주순이익": "비지배순이익",
    "비지배지분에귀속되는당기순이익": "비지배순이익",
    "비지배주주지분순이익": "비지배순이익",
    "기본주당순이익": "EPS", "기본주당이익": "EPS",
    "기본주당순이익(손실)": "EPS", "보통주기본주당이익(손실)": "EPS",
    # ⚠️ 총액 계정만. '상품및제품' 같은 **구성요소**를 별칭으로 넣으면
    # 총액이 없는 회사에서 부분값이 재고자산으로 표시된다(과소 표기).
    "재고자산": "재고자산", "재고자산(순액)": "재고자산",
    "재고자산 순액": "재고자산",
    "유동자산": "유동자산",
    "비유동자산": "비유동자산", "고정자산": "비유동자산",
    "자산총계": "자산총계", "자산 합계": "자산총계",
    "유동부채": "유동부채",
    "비유동부채": "비유동부채", "고정부채": "비유동부채",
    "부채총계": "부채총계", "부채 합계": "부채총계",
    "이익잉여금": "이익잉여금", "이익잉여금(결손금)": "이익잉여금",
    "미처분이익잉여금": "이익잉여금",
    "자본총계": "자본총계", "자본 합계": "자본총계",
    # 현금흐름표(FCF 재료). 앞머리 번호는 `_norm_acct_nm` 이 이미 벗긴다.
    # ⚠️ 공백은 `_norm_acct_nm` 이 지우므로 **어절 구성이 다른 것만** 적으면
    # 된다. 회사마다 표기가 갈려 하나만 두면 조용히 빈칸이 된다.
    "영업활동현금흐름": "영업활동현금흐름",
    "영업활동으로인한현금흐름": "영업활동현금흐름",
    "영업활동으로부터의현금흐름": "영업활동현금흐름",
    "영업활동으로인한순현금흐름": "영업활동현금흐름",
    "영업활동순현금흐름": "영업활동현금흐름",
    # ⚠️ `영업에서 창출된 현금흐름` 은 **여기 없다.** 그건 이자·법인세
    # 납부 **전** 소계라 영업활동현금흐름보다 크다 — 승자 규칙이 '절댓값
    # 큰 행'이라 그 줄이 이기면 FCF 가 통째로 부풀려진다. 실측(2026-08-24
    # NHN KCP 060250.KQ): 우리 OCF 2,073.7억 vs FnGuide 2,003억(+70.7억),
    # CAPEX 는 47.4억으로 정확히 같았다 — 차이가 전부 이 계정에서 왔다.
    # 같은 이름처럼 보여도 **다른 개념**이면 매핑하지 말 것(#34).
    # ⚠️ CAPEX 는 **총액 계정만**. `토지의 취득`·`건물의 취득` 같은
    # 구성요소를 넣으면 총액이 없는 회사에서 부분값이 CAPEX 가 되어
    # FCF 가 과대표시된다(재고자산에서 겪은 것과 같은 함정).
    "유형자산의취득": "유형자산취득", "유형자산취득": "유형자산취득",
    "유형자산의증가": "유형자산취득",
    "유형자산및무형자산의취득": "유형자산취득",
    "무형자산의취득": "무형자산취득", "무형자산취득": "무형자산취득",
    "무형자산의증가": "무형자산취득",
}

# EPS 만 float (원 단위 소수점 가능), 나머지 absolute KRW int.
_EPS_KEYS = {"EPS"}


def _parse_dart_amount(raw: str, as_float: bool = False):
    """DART API 의 thstrm_amount (당기 금액) 파싱.

    DART 는 amount 를 콤마 포함 string 으로 반환 ('1,234,567,890').
    음수는 '(123,456)' 또는 '-123,456' 형식. 빈 string 또는 '-' 일 때
    None 반환. EPS 는 소수점 가능 (₩1,234.56) 이라 float 옵션.
    """
    s = (raw or "").strip()
    if not s or s in ("-", "—", "N/A"):
        return None
    # 괄호 음수 처리
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace(",", "").strip()
    if not s:
        return None
    try:
        if as_float:
            v = float(s)
        else:
            # 소수점 있는 case (예: EPS '1234.56') 도 안전 처리
            v = int(float(s))
    except (ValueError, TypeError):
        return None
    return -v if negative else v


# 한 canonical 키에 여러 계정이 매핑되는 경우의 **결정론적 우선순위**.
# 배경(사용자 2026-08-16 메리츠금융지주 TTM 매출 -10.30조): `_DART_NAME_MAP`
# 은 영업수익·매출액·이자수익을 모두 `매출` 로 보내는데, 금융지주 손익
# 계산서엔 이들이 동시에 존재한다. 옛 코드는 "절댓값 최대 row" 만으로
# 승자를 뽑았고, 4분기 파생은 연간(thstrm_amount)과 9개월 누적
# (thstrm_add_amount)에서 **각각 독립적으로** 승자를 뽑았다. 승자가 서로
# 다르면 다른 계정끼리 빼서 대규모 음수가 나온다(순이익은 계정이 유일해
# 정상이었던 것과 정합). 순위를 고정하면 두 보고서가 같은 계정을 고른다.
#
# **동의어 그룹** 단위로 정의한다 — 같은 그룹 안의 이름들은 회사·양식마다
# 다르게 쓰는 같은 개념이다(연간보고서는 '매출액', 분기보고서는 '수익
# (매출액)' 처럼 한 회사 안에서도 갈린다). 그룹이 같으면 계정이 바뀐 게
# 아니므로 차분 가드가 발화하면 안 된다 — 이름 문자열을 직접 비교하면
# 멀쩡한 회사의 4분기 매출·TTM·PSR 이 통째로 비어 버린다.
_NAME_MAP_NORM: dict[str, str] = {}   # _norm_acct_nm(이름) → canonical (아래에서 채움)

_ACCOUNT_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    # 앞 그룹일수록 우선. 금융·보험은 '영업수익'이 손익계산서 최상단
    # 총수익이고, '이자수익'은 그 구성요소라 총액을 대표할 수 없다.
    "매출": (
        ("영업수익",),                                              # 0 총수익
        ("매출액", "수익(매출액)", "매출", "도급공사수익", "분양수익"),  # 1 일반 매출액
        # 2 구성요소 — 총액을 대표하지 못하는 계정.
        # 보험사(IFRS17)는 총수익 계정이 없고 `보험영업수익`(dart_Operating
        # IncomeInsurance) · `보험수익`(ifrs-full_InsuranceRevenue) ·
        # `투자영업수익` 으로 쪼개 공시한다 — 2026-08-19 현대해상 실측
        # (보험영업수익 14.73조 · 보험수익 14.14조 · 투자영업수익 3.16조).
        # 어느 하나를 매출로 쓰면 화면이 실제 총수익과 어긋나므로 전부
        # 구성요소로 두고 총액은 FnGuide 폴백이 채운다(은행 이자수익과 동일).
        ("이자수익", "보험영업수익", "보험수익", "투자영업수익"),
    ),
}

# **구성요소** 그룹 — 총액을 대표하지 못하는 계정. 값은 그대로 쓰되(제거하면
# 다른 종목이 회귀) '이건 총매출이 아니다'를 플래그로 알린다.
# 배경(사용자 2026-08-16 VM probe): 메리츠금융지주 2025 사업보고서엔
# 총수익(영업수익) 계정이 **아예 없고** 보험수익·이자수익 등 구성요소로만
# 공시된다. 그래서 연간 경로에서 승자가 이자수익(4.15조)이 되는데, 이걸
# 그냥 '매출'로 표기하면 실제 총수익(3분기 누적만 24.95조)과 6배 어긋난다.
_COMPONENT_GROUPS: dict[str, set[int]] = {"매출": {2}}


# ⚠️ **정규화 재무 디스크 캐시 버전.** 파서가 내는 canonical 키가 바뀌면
# 반드시 올린다 — 안 올리면 최근 조회분이 옛 결과를 최대 7일간 그대로
# 서빙한다(실수 #18). 이 함수에서만 세 번 겪었다:
#   v4 (2026-08-16) `_src`·`_component_accounts` 사이드채널 추가
#   v5 (2026-08-19) 계정 우선순위 변경(표준 태그·이름 정규화)
#   v8 (2026-08-21) 현금흐름 계정 + FCF 추가
#   v10 (2026-08-23) 지배주주순이익·지배주주자본 추가(FnGuide 산식 정렬)
#   v11 (2026-08-23) 중복 키로 죽어 있던 `지배주주자본` 부활 + 비지배지분
#   v12 (2026-08-24) `영업에서창출된현금흐름`(이자·법인세 전 소계) 제거
#   v13 (2026-08-24) 비지배순이익 추가(지배주주순이익 항등식 폴백)
# 규율로는 세 번 다 실패했으므로 아래 `_CANONICAL_KEYS` 를 회귀가 고정한다
# — 키가 늘면 테스트가 깨지고, 고치려면 이 숫자를 올려야 한다.
_FIN_CACHE_VER = 13

# 파서가 낼 수 있는 canonical 키 전량. **여기가 바뀌면 캐시 버전을 올려라.**
# 목록을 손으로 적는 게 아니라 두 매핑에서 도출하므로 새 계정을 추가하면
# 자동으로 달라지고, 회귀가 그때 발화한다(#24 열거형 회피).
_CANONICAL_KEYS = frozenset(_DART_CODE_MAP.values()) | frozenset(
    _DART_NAME_MAP.values())


# 손익 계정은 **손익계산서에서만** 뽑는다(2026-08-19 VM 프로브 확정).
#
# NH투자증권 005940 25.반기: `당기순이익` 이 4개 재무제표에 동명으로 나온다 —
# 포괄손익계산서(CIS) 2,569억(당기 3개월), 현금흐름표(CF) 2,569억,
# **자본변동표(SCE) 4,650억(연초~반기 누적)**. 승자 규칙이 '절댓값 큰 행'
# 이라 SCE 누적이 이겨 화면에 반기 순이익이 4,650억으로 떴다(네이버 2,569억).
# 매출·영업이익은 CIS 한 행뿐이라 멀쩡했고, 1분기는 누적=단독이라 우연히
# 맞았으며, 4분기는 누적−누적이라 상쇄돼 맞았다 — 그래서 **2·3분기만**
# 틀리는 형태로 오래 숨어 있었다.
#
# 같은 이름이어도 재무제표가 다르면 **기간 의미가 다르다**. 그러니 키마다
# 허용 sj_div 를 두고, 허용 제표에 행이 하나도 없을 때만 다른 제표를 쓴다
# (값을 잃지 않으면서 오염만 막는다).
_IS_KEYS = frozenset({"매출", "매출원가", "매출총이익", "판관비", "영업이익",
                      "금융수익", "금융비용", "세전이익", "법인세비용",
                      "당기순이익", "지배주주순이익", "비지배순이익",
                      "EPS"})
_BS_KEYS = frozenset({"자산총계", "부채총계", "자본총계", "유동자산",
                      "유동부채", "비유동자산", "비유동부채", "재고자산",
                      "이익잉여금", "지배주주자본", "비지배지분"})
# ⚠️ 현금흐름 계정을 제표로 안 묶으면 같은 이름이 주석·자본변동표에서
# 잡혀 기간 의미가 갈린다(NH투자증권 당기순이익이 자본변동표 누적으로
# 잡혔던 것과 같은 함정, 이 파일 위 주석 참조).
_CF_KEYS = frozenset({"영업활동현금흐름", "유형자산취득", "무형자산취득"})


def _stmt_tier(canonical: str, sj_div: str) -> int:
    """0 = 이 계정이 원래 있어야 할 재무제표 · 1 = 그 외(차선)."""
    sj = (sj_div or "").strip().upper()
    if canonical in _IS_KEYS:
        return 0 if sj in ("IS", "CIS") else 1
    if canonical in _BS_KEYS:
        return 0 if sj == "BS" else 1
    if canonical in _CF_KEYS:
        return 0 if sj == "CF" else 1
    return 0


# 계정명 앞머리 번호("Ⅰ.", "I.", "1.", "가.")와 공백은 회사마다 붙였다 뗐다
# 한다 — 이름 비교 전에 벗긴다. 이게 없으면 "Ⅰ. 영업수익" 이 목록에 없는
# 이름으로 취급돼 **구성요소보다 뒤로 밀린다**(2026-08-19 실측).
_ACCT_PREFIX = re.compile(r"^[\s\dⅠ-ⅩIVXivx가-힣]{0,4}[.．)\]]\s*")


def _norm_acct_nm(acct_nm: str) -> str:
    nm = (acct_nm or "").strip()
    nm = _ACCT_PREFIX.sub("", nm).strip()
    return nm.replace(" ", "")


# 공백·번호를 벗긴 이름으로도 찾을 수 있게 미리 만들어 둔다.
_NAME_MAP_NORM.update({_norm_acct_nm(k): v for k, v in _DART_NAME_MAP.items()})


# **총액을 뜻하는 표준 태그.** 이름이 목록에 없어도 이 태그면 총액이다.
_TOTAL_REVENUE_CODES = frozenset({
    "ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers",
    "dart_Revenue",
})


def _account_rank(canonical: str, acct_nm: str, acct_id: str = "") -> int:
    """동의어 그룹 인덱스(작을수록 우선). 미정의/미등재는 동순위(맨 뒤).

    승자 선택의 우선순위이자, `dart_quarterly._diff_quarter` 의 계정 일치
    판정 단위이기도 하다(같은 그룹 = 같은 계정으로 취급).

    ⚠️ **이름만 보면 안 된다**(2026-08-19 NH투자증권). `ifrs-full_Revenue`
    같은 표준 태그로 총액이 정확히 잡혀도, 한글 이름이 그룹 목록에 없으면
    최하위(3)로 밀려 구성요소인 이자수익(2)에게 진다 — 그러면 화면에
    영업이익이 매출보다 큰 표가 뜬다. 태그가 총액을 말하면 총액으로 친다."""
    groups = _ACCOUNT_GROUPS.get(canonical)
    if not groups:
        return 0
    nm = _norm_acct_nm(acct_nm)
    for i, grp in enumerate(groups):
        if nm in {_norm_acct_nm(g) for g in grp}:
            return i
    if canonical == "매출" and (acct_id or "").strip() in _TOTAL_REVENUE_CODES:
        # 표준 총액 태그 — 이름을 몰라도 '일반 매출액' 급으로 본다.
        return 1
    return len(groups)


def revenue_label(financials: dict | None) -> str:
    """손익 최상단 행에 쓸 이름. 총액 계정이면 "매출", 구성요소가 승자면
    **그 계정명**(예: "이자수익").

    ⚠️ 왜(사용자 2026-08-19 NH투자증권 "매출보다 영익이 더 나오는데"):
    이자수익을 '매출'이라 부르면 영업이익이 매출보다 큰 표가 되어 읽는
    사람이 데이터 오류로 오해한다. 실제 계정명을 쓰면 모순이 사라진다.
    """
    return ((financials or {}).get("_component_accounts") or {}).get("매출") or "매출"


def _extract_dart_financials(items: list, amount_field: str = "thstrm_amount") -> dict:
    """DART fnlttSinglAcntAll.json 응답의 list[item] → 정규화 dict.

    Item 각각 account_id (K-IFRS / dart_ / us-gaap 표준) 또는 account_nm
    (한글 텍스트) 보유. CODE_MAP 우선 매칭, 미매칭 시 NAME_MAP fallback.
    같은 canonical 키에 여러 row 매칭 시 absolute value 큰 것 선택
    (parent vs consolidated / 본사 vs 종속 등 표준 row 우선).

    amount_field(2026-08-19, VM 실측 확인 — 삼성전자 25.반기/25.3분기 원본
    응답): 분기/반기보고서(reprt_code 11012/11013/11014)의 손익계산서
    (sj_div=IS) 항목은 **thstrm_amount 자체가 이미 '당기 3개월'(단일분기)
    값**이고, thstrm_add_amount 가 '당기누적'(연초~해당분기말 누적)이다 —
    반대로 짐작하기 쉽지만(라벨이 '반기'/'3분기'라 누적처럼 보임) 실측
    결과 정반대. 사업연도(11011)엔 thstrm_add_amount 자체가 없음(전체가
    이미 연간 누적이라 구분 불요). amount_field='thstrm_add_amount' 로
    호출하면 그 누적치를 뽑을 수 있음 — 4분기(연간-9개월누적) 단독 산출
    에만 필요(bot/dart_quarterly.py)."""
    if not items:
        return {}
    res: dict = {}
    src: dict = {}      # canonical → 채택된 동의어 그룹 인덱스 (승자 추적)
    rank: dict = {}     # canonical → 채택된 계정의 우선순위(작을수록 우선)
    comp_nm: dict = {}  # canonical → 구성요소 계정명(총액 대표 불가) 또는 None
    for item in items:
        acct_id = (item.get("account_id") or "").strip()
        acct_nm = (item.get("account_nm") or "").strip()
        canonical = (_DART_CODE_MAP.get(acct_id)
                     or _DART_NAME_MAP.get(acct_nm)
                     or _NAME_MAP_NORM.get(_norm_acct_nm(acct_nm)))
        if not canonical:
            continue
        v = _parse_dart_amount(
            item.get(amount_field, ""),
            as_float=(canonical in _EPS_KEYS),
        )
        if v is None:
            continue
        pr = _account_rank(canonical, acct_nm, acct_id)
        # 제표 tier 를 순위에 **한 자리 더** 붙인다 — 이름 우선순위가 같아도
        # 손익 계정은 손익계산서 행이 자본변동표·현금흐름표 행을 이긴다.
        st = _stmt_tier(canonical, item.get("sj_div") or "")
        key = (pr, st)
        prev = rank.get(canonical)
        # 우선순위가 높은(작은) 계정이 무조건 이긴다. 동순위 안에서만
        # 기존 규칙(absolute value 큰 row = consolidated 우선)을 적용.
        if canonical not in res or key < prev or (
                key == prev and abs(v) > abs(res[canonical])):
            res[canonical] = v
            # 이름이 아니라 **동의어 그룹 인덱스**를 남긴다 — 같은 개념을
            # 연간·분기 보고서가 다른 이름으로 쓰는 게 정상이라, 이름을
            # 비교하면 멀쩡한 회사에서 차분 가드가 오발화한다.
            src[canonical] = pr
            rank[canonical] = key
            comp_nm[canonical] = (acct_nm or acct_id
                                  if pr in _COMPONENT_GROUPS.get(canonical, ())
                                  else None)
    # 구성요소 계정이 승자가 된 항목 — 값은 보존하고 '총액 아님'만 알린다.
    _comp = {k: v for k, v in comp_nm.items() if v}
    if _comp:
        res["_component_accounts"] = _comp
    if src:
        # 어느 계정 그룹이 채택됐는지 — 보고서 간 차분(4분기 = 연간 − 9개월
        # 누적) 시 양쪽이 같은 개념인지 검증하는 재료. `_` 접두 사이드채널
        # 이라 숫자 항목 순회에는 걸리지 않는다.
        res["_src"] = src
    return res


_TTM_QUARTERS = 4


def _avg_denom(cur, prev):
    """FnGuide 산식의 **평균 분모** — (당기 + 전기)/2. 전기가 없으면 당기.

    ⚠️ 전기를 못 구할 때 그냥 당기(기말)를 쓰면 ROE 가 체계적으로 **낮게**
    나온다(자본이 늘어나는 회사에서 평균 < 기말). 그래서 어느 쪽을 썼는지
    호출부가 화면에 밝힌다(#43).
    """
    if cur is None:
        return None, False
    if prev is None:
        return cur, False
    return (cur + prev) / 2.0, True


def apply_ttm_returns(entries: list) -> int:
    """분기 시리즈의 ROE·ROA 를 **TTM 분자 + 평균 분모**로 다시 계산.

    ⚠️ 사용자가 신뢰 기준으로 제시한 FnGuide 산식(2026-08-23):
        ROE = (지배주주지분)당기순이익[당기]
              / ((지배주주지분)자본총계[당기] + [전기]) / 2
        ROA = 당기순이익[당기] / (자산총계[당기] + [전기]) / 2
    우리는 **기말** 잔액으로 나눠 왔다 — 자본이 늘어나는 회사에서 평균보다
    커서 ROE 가 체계적으로 낮게 나온다. 기아 000270.KS 실측: 우리 ROE
    12.3% · ROA 7.6% vs 네이버 12.92 · 7.88 — 두 지표가 **같은 방향으로**
    어긋난 것이 분모 문제의 신호였다(#51 여러 축을 대조하면 판정된다).

    `entries` = 오래된→최신 [{financials, ratios}]. 분자는 최근 4분기 합
    (연율), 분모의 '전기' 는 **4분기 전**(1년 전) 잔액이다. 4분기가 안
    모이는 앞쪽 항목은 비운다 — 분기 하나로 연율을 흉내 내면 틀린 숫자다.

    반환 = 값을 채운 항목 수."""
    n = 0
    for i, e in enumerate(entries):
        fin = e.get("financials") or {}
        rat = e.setdefault("ratios", {})
        window = entries[i - _TTM_QUARTERS + 1:i + 1]
        nets = [(w.get("financials") or {}).get("당기순이익")
                for w in window]
        if len(window) < _TTM_QUARTERS or any(v is None for v in nets):
            rat["ROE"] = None
            rat["ROA"] = None
            continue
        owns = [(w.get("financials") or {}).get("지배주주순이익")
                for w in window]
        owner = all(v is not None for v in owns)
        prev_fin = ((entries[i - _TTM_QUARTERS].get("financials") or {})
                    if i - _TTM_QUARTERS >= 0 else {})
        # ⚠️ **분자와 분모는 같은 급이어야 한다.** 옛 코드는 둘을 따로 골라,
        # 지배주주순이익이 한 분기라도 없으면 분자만 연결 총액으로 떨어지고
        # 분모는 지배주주자본으로 남았다 — 비지배지분이 큰 회사에서 ROE 가
        # 크게 부풀려진다(뉴파워프라즈마 144960.KQ 실측 2026-08-24: 우리
        # 26.1Q 8.0% vs 네이버 5.96%, 비지배지분이 자본의 33%다).
        # 총액/총액 · 지배/지배 둘 중 하나로만 간다(#34).
        eq_key = ("지배주주자본"
                  if (owner and fin.get("지배주주자본") is not None)
                  else "자본총계")
        eq, eq_avg = _avg_denom(fin.get(eq_key), prev_fin.get(eq_key))
        asset, as_avg = _avg_denom(fin.get("자산총계"),
                                   prev_fin.get("자산총계"))
        rat["ROE"] = (sum(owns if owner else nets) / eq * 100) if eq else None
        rat["ROA"] = (sum(nets) / asset * 100) if asset else None
        # 어느 기준으로 만들었는지 화면이 말할 수 있게 남긴다(#43·#189).
        rat["_returns_basis"] = {
            "numerator": "지배주주순이익" if owner else "당기순이익(연결 총액)",
            "denominator": eq_key,
            "averaged": bool(eq_avg and as_avg),
            # 분모를 우리가 빼서 만들었으면 그렇게 밝힌다(#43·#123) —
            # 원천이 준 값과 파생값은 다른 신뢰도다.
            "denominator_derived": (fin.get("_derived_from") or {}).get(eq_key),
        }
        n += 1
    return n


def _yr_of(e) -> int | None:
    """항목의 회계연도 — 평평한 dict 와 `{"financials": …}` 둘 다 지원."""
    for src in (e or {}, (e or {}).get("financials") or {}):
        v = src.get("year")
        if isinstance(v, int):
            return v
        try:
            return int(str(v)[:4])
        except (TypeError, ValueError):
            continue
    return None


def apply_annual_returns(entries: list) -> int:
    """연간 시리즈의 ROE·ROA 를 같은 규약(평균 분모·지배주주 분자)으로.

    ⚠️ 분기 표만 고치면 **기업 탭 연간 ROE 는 그대로 어긋난다** — 같은
    계산을 하는 화면을 같이 고칠 것(#38·#147). `entries` = 오래된→최신.
    """
    n = 0
    for i, e in enumerate(entries):
        fin = e.get("financials") or e            # ts 는 평평한 dict 다
        rat = e.setdefault("ratios", e) if "financials" in e else e
        prev = (entries[i - 1] if i >= 1 else {})
        # ⚠️ **'전기' 는 바로 앞 해여야 한다.** 원천이 한 해를 안 주면 목록의
        # 앞 항목이 2년 전이 되는데, 그걸 전기로 평균을 내면 분모가 통째로
        # 다른 시점 값이다(#29·#168 인접 판정은 간격을 재야 한다). 실측
        # 2026-08-24 현대이지웰 090850.KQ: FY2021 이 없어 FY2022 의 '전기'가
        # FY2020 이 됐다. 인접이 아니면 **기말로 떨어진다**(각주가 그렇게 말한다).
        _y, _py = _yr_of(e), _yr_of(prev)
        if _y is not None and _py is not None and _py != _y - 1:
            prev = {}
        prev_fin = prev.get("financials") or prev
        net = fin.get("당기순이익")
        if net is None:
            continue
        own = fin.get("지배주주순이익")
        # 분자·분모는 같은 급으로(위 `apply_ttm_returns` 주석 참조)
        eq_key = ("지배주주자본"
                  if (own is not None and fin.get("지배주주자본") is not None)
                  else "자본총계")
        eq, eq_avg = _avg_denom(fin.get(eq_key), prev_fin.get(eq_key))
        asset, as_avg = _avg_denom(fin.get("자산총계"),
                                   prev_fin.get("자산총계"))
        if eq:
            rat["ROE"] = (own if own is not None else net) / eq * 100
        if asset:
            rat["ROA"] = net / asset * 100
        rat["_returns_basis"] = {
            "numerator": "지배주주순이익" if own is not None
                         else "당기순이익(연결 총액)",
            "denominator": eq_key,
            "averaged": bool(eq_avg and as_avg),
            "denominator_derived": (fin.get("_derived_from") or {}).get(eq_key),
        }
        n += 1
    return n


def _fill_equity_fallbacks(fin: dict) -> None:
    """`지배주주자본` 을 원천이 안 주면 **빼서** 만든다 — 그리고 그 반대도.

    BPS·ROE 의 분모다(FnGuide 산식). 원천이 이 계정을 안 주면 분모가 연결
    총액(비지배 포함)으로 떨어져 두 지표가 **같은 방향으로** 낮게 나온다
    (POSCO홀딩스 005490.KS 실측 2026-08-23). 계정 이름 열거로는 새 표기를
    못 잡으므로(#24) `자본총계 − 비지배지분` 이라는 **항등식**을 최종
    그물로 둔다.

    ⚠️ 반대 방향도 필요하다 — 자본을 지배주주분으로만 주는 회사가 있어
    옛 코드는 그 계정을 `자본총계` 로도 매핑해 뒀었다(같은 키 중복이라
    파이썬이 마지막 것만 남겨 `지배주주자본` 이 전 종목에서 죽어 있었다).
    중복 키 대신 여기서 채운다. 만든 값에는 `_derived_from` 을 남긴다(#43).
    """
    def _n(v):
        return (float(v) if isinstance(v, (int, float))
                and not isinstance(v, bool) else None)

    # 손익도 같은 항등식 — 지배주주순이익 = 당기순이익 − 비지배순이익.
    # ROE 분자가 없어 총액으로 떨어지면 분모까지 총액으로 내려가 FnGuide 와
    # 갈린다(뉴파워프라즈마 144960.KQ 실측). 이름 열거는 새 표기를 못
    # 잡으므로(#24) 뺄셈을 최종 그물로 둔다.
    net, nown, nmi = (_n(fin.get("당기순이익")), _n(fin.get("지배주주순이익")),
                      _n(fin.get("비지배순이익")))
    if nown is None and net is not None and nmi is not None:
        fin["지배주주순이익"] = net - nmi
        fin.setdefault("_derived_from", {})["지배주주순이익"] = "당기순이익 − 비지배순이익"
    elif net is None and nown is not None and nmi is not None:
        fin["당기순이익"] = nown + nmi
        fin.setdefault("_derived_from", {})["당기순이익"] = "지배주주순이익 + 비지배순이익"

    tot, own, mi = (_n(fin.get("자본총계")), _n(fin.get("지배주주자본")),
                    _n(fin.get("비지배지분")))
    if own is None and tot is not None and mi is not None:
        fin["지배주주자본"] = tot - mi
        fin.setdefault("_derived_from", {})["지배주주자본"] = "자본총계 − 비지배지분"
    elif tot is None and own is not None:
        # 비지배지분이 없으면 지배주주분이 곧 총계다(단일기업·완전자회사)
        fin["자본총계"] = own + (mi or 0.0)
        fin.setdefault("_derived_from", {})["자본총계"] = (
            "지배주주자본 + 비지배지분" if mi else "지배주주자본(비지배 없음)")


def calc_kr_financial_ratios(financials: dict) -> dict:
    """DART 정규화 dict → 10 재무비율 (영업이익률 / 순이익률 / ROE /
    ROA / 부채비율 / 유동비율 / 이자보상배율 / 매출총이익률 / 이익잉여금
    비율 / ROIC). 분모 0 / None 시 해당 row 만 None — graceful.

    StandardView (StanLee5767/standardview) 의 calc_ratios 차용 (license
    동의 2026-05-19). 단위는 % (백분율).
    """
    rev = financials.get("매출") or 0
    op = financials.get("영업이익")
    net = financials.get("당기순이익")
    asset = financials.get("자산총계") or 0
    eq = financials.get("자본총계") or 0
    dbt = financials.get("부채총계") or 0
    ca = financials.get("유동자산") or 0
    cl = financials.get("유동부채") or 0
    fc = financials.get("금융비용") or 0
    gp = financials.get("매출총이익") or 0
    er = financials.get("이익잉여금") or 0
    pretax = financials.get("세전이익")
    tax = financials.get("법인세비용")
    # ROIC(투하자본이익률) 근사치(2026-08-16) — DART 표준 요약 API 는
    # 이자부채/비이자부채를 구분해 주지 않아, 투하자본은 통상적 근사식
    # 자산총계-유동부채(≈자기자본+비유동부채) 사용. 실효세율은 DART 실측값
    # (법인세비용/세전이익)을 그대로 쓰고, 세전이익이 없거나 실효세율이
    # 0~1 범위를 벗어나면(적자 등으로 왜곡) 세후조정 없이 영업이익 그대로
    # 사용(고정 법인세율 가정 = 창작이라 지양, 데이터 없으면 보정 생략).
    invested_capital = (asset - cl) if asset else None
    nopat = None
    if op is not None:
        eff_tax_rate = (tax / pretax) if (tax is not None and pretax) else None
        if eff_tax_rate is not None and 0 <= eff_tax_rate <= 1:
            nopat = op * (1 - eff_tax_rate)
        else:
            nopat = op
    # ⚠️ **분모가 총액이 아니면 비율을 만들지 않는다.** NH투자증권(2026-08-19
    # 사용자 지적)은 총수익 계정을 공시하지 않아 '매출' 자리에 이자수익이
    # 들어간다 — 그대로 나누면 영업이익률 117.7% 처럼 **불가능한 숫자**가
    # 화면에 오른다. 값(이자수익)은 보존하되 비율은 비운다(빈칸 > 틀린 숫자).
    _rev_is_component = "매출" in (financials.get("_component_accounts") or {})
    _rev_ok = rev if not _rev_is_component else 0
    return {
        "영업이익률": (op / _rev_ok * 100) if (op is not None and _rev_ok) else None,
        "순이익률": (net / _rev_ok * 100) if (net is not None and _rev_ok) else None,
        "ROE": (net / eq * 100) if (net is not None and eq) else None,
        "ROA": (net / asset * 100) if (net is not None and asset) else None,
        "부채비율": (dbt / eq * 100) if eq else None,
        "유동비율": (ca / cl * 100) if cl else None,
        "이자보상배율": (op / fc) if (op is not None and fc) else None,
        "매출총이익률": (gp / _rev_ok * 100) if (gp and _rev_ok) else None,
        "이익잉여금비율": (er / asset * 100) if (er and asset) else None,
        "ROIC": (nopat / invested_capital * 100)
                if (nopat is not None and invested_capital) else None,
    }


_ENV_KEY_TRIED = False
# 파일에서 읽어낸 키를 **캐시**한다. 플래그만 두고 값을 안 남기면 프로세스에서
# 두 번째로 만든 DartClient 부터 키가 빈 채로 생성돼, 첫 클라이언트만 동작하고
# 나머지는 조용히 아무것도 못 한다(2026-08-16 독립 리뷰 실측 — governance 는
# 한 흐름에 클라이언트를 2개 만든다).
_ENV_KEY_CACHED = ""


def _dart_key_from_env_file() -> str:
    """.env 에서 DART_API_KEY 만 읽는다(파일 I/O 는 한 번). 실패 시 ''.

    ⚠️ `load_dotenv()` 가 아니라 `dotenv_values()` — 전자는 .env 의 **모든
    키**(TELEGRAM_BOT_TOKEN·DASHBOARD_PASSWORD·KIS_* …)를 프로세스 환경에
    주입한다. 키 하나를 읽는 부작용으로는 과하다."""
    global _ENV_KEY_TRIED, _ENV_KEY_CACHED
    if _ENV_KEY_TRIED:
        return _ENV_KEY_CACHED
    _ENV_KEY_TRIED = True
    try:
        from pathlib import Path as _P

        from dotenv import dotenv_values, find_dotenv
        for _p in (find_dotenv(usecwd=True), str(_P.home() / "stock" / ".env")):
            if not _p:
                continue
            v = ((dotenv_values(_p) or {}).get("DART_API_KEY") or "").strip()
            if v:
                _ENV_KEY_CACHED = v
                break
    except Exception as exc:
        log.debug("dart: .env 직접 로드 실패: %s", exc)
    return _ENV_KEY_CACHED


class DartClient:
    """Single-key DART client. Cheap to instantiate; reuse across calls
    to amortize the corp_code mapping load."""

    def __init__(self, api_key: Optional[str] = None):
        # Read key lazily so a missing env var doesn't crash module import.
        # 환경변수가 비어 있으면 .env 를 **직접** 읽는다 — 이 클래스는
        # 호출부가 미리 load_dotenv() 한 것에 의존해 왔고(운영 데몬은
        # bot/telegram_bot.py 가 로드), 그래서 스크립트·프로브 진입점은
        # 조용히 키 없이 돌아 DART 가 `status=100 인증키 누락` 을 반환했다
        # (사용자 2026-08-16 프로브 실측). bot/dart_feed._dart_api_key 와
        # 같은 규약 — dotenv_values 로 **그 키만** 읽어 다른 비밀값을
        # 프로세스 환경에 주입하지 않는다.
        # ⚠️ `api_key is not None` — 빈 문자열은 **명시적인 '키 없음'** 이다.
        # `or` 체인으로 두면 `DartClient("")` 가 .env 에서 진짜 키를 주워와
        # 오프라인 가드 테스트가 실제 DART 를 호출한다(2026-08-16 독립 리뷰).
        if api_key is not None:
            self.api_key = api_key.strip()
        else:
            self.api_key = (os.getenv("DART_API_KEY")
                            or _dart_key_from_env_file() or "").strip()
        self._corp_code_map: dict[str, str] | None = None  # stock_code → corp_code
        # normalized name → list of {name, stock_code, corp_code} entries.
        # One normalized key can map to multiple companies when a search
        # like "현대" matches several entries — caller decides what to do.
        self._name_map: dict[str, list[dict]] | None = None
        # Reverse map: stock_code → corp_name. Built lazily on first
        # stock_code_to_name() call from _name_map; not persisted to
        # disk because it's cheap to rebuild from the v2 cache.
        self._stock_to_name: dict[str, str] | None = None

    # ── corp_code mapping ───────────────────────────────────────────────
    def _load_corp_code_map(self) -> dict[str, str]:
        """Stock code (6-digit) → corp_code (8-digit). DART exposes the
        mapping as a single zipped XML; we cache it locally for 30 days
        so we don't re-download on every analysis. The cache also carries
        the reverse name→entries map so `find_by_name()` doesn't have to
        re-parse the XML."""
        if self._corp_code_map is not None and self._name_map is not None:
            return self._corp_code_map

        # Disk cache check (v2 format: dict with 'stock_to_corp' and
        # 'name_to_entries' keys).
        if _CORPCODE_CACHE.exists():
            try:
                age_days = (time.time() - _CORPCODE_CACHE.stat().st_mtime) / 86400
                if age_days < _CORPCODE_TTL_DAYS:
                    data = json.loads(_CORPCODE_CACHE.read_text())
                    self._corp_code_map = data.get("stock_to_corp", {})
                    self._name_map = data.get("name_to_entries", {})
                    return self._corp_code_map
            except Exception as exc:
                log.warning("dart: corp_code cache read failed: %s", exc)

        # Fetch fresh.
        if not self.api_key:
            log.warning("dart: DART_API_KEY missing — corp_code map unavailable")
            self._corp_code_map = {}
            self._name_map = {}
            return self._corp_code_map

        try:
            resp = requests.get(
                f"{_DART_BASE}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
            root = ET.fromstring(xml_bytes)
        except Exception as exc:
            log.warning("dart: corp_code download failed: %s", exc)
            self._corp_code_map = {}
            self._name_map = {}
            return self._corp_code_map

        stock_to_corp: dict[str, str] = {}
        name_to_entries: dict[str, list[dict]] = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            # Only KRX-listed entries have a 6-digit stock_code; skip the
            # rest (DART also tracks unlisted entities).
            if not (stock_code and len(stock_code) == 6 and corp_code):
                continue
            stock_to_corp[stock_code] = corp_code
            norm = _normalize_name(corp_name)
            if norm:
                name_to_entries.setdefault(norm, []).append({
                    "name": corp_name,
                    "stock_code": stock_code,
                    "corp_code": corp_code,
                })
        log.info(
            "dart: loaded %d corp_code / %d unique normalized names",
            len(stock_to_corp), len(name_to_entries),
        )

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CORPCODE_CACHE.write_text(json.dumps(
                {"stock_to_corp": stock_to_corp, "name_to_entries": name_to_entries},
                ensure_ascii=False,
            ))
        except Exception as exc:
            log.warning("dart: corp_code cache write failed: %s", exc)

        self._corp_code_map = stock_to_corp
        self._name_map = name_to_entries
        return stock_to_corp

    def find_by_name(self, query: str) -> list[dict]:
        """Resolve a Korean / English company name to listed-entity entries.

        Returns a list of {name, stock_code, corp_code} dicts ordered by
        match specificity:
          - Exact normalized match (e.g. '삼성전자' → 005930)
          - Prefix or substring match if no exact hit
          - Empty list when nothing matches at all

        Capped at 20 to keep ambiguous queries (e.g. '현대') tractable
        for the caller's UX."""
        norm = _normalize_name(query)
        if not norm:
            return []
        self._load_corp_code_map()
        if not self._name_map:
            return []

        # Exact-normalized match wins.
        exact = self._name_map.get(norm) or []
        if exact:
            return exact[:20]

        # Fallback: prefix match first (more specific), then substring.
        prefix: list[dict] = []
        contains: list[dict] = []
        for n, entries in self._name_map.items():
            if n.startswith(norm):
                prefix.extend(entries)
            elif norm in n:
                contains.extend(entries)
        return (prefix + contains)[:20]

    def stock_code_to_corp_code(self, stock_code: str) -> Optional[str]:
        """Resolve 6-digit KRX stock code → 8-digit DART corp_code.
        Strips a trailing `.KS`/`.KQ` for caller convenience."""
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        return self._load_corp_code_map().get(code)

    def stock_code_to_name(self, stock_code: str) -> Optional[str]:
        """Reverse lookup: 6-digit stock code → Korean corp name.
        Used by the analyzer to render '삼성전자 / 005930.KS' style
        titles. Builds a stock→name map lazily on first call from the
        cached _name_map so we don't pay the O(n) cost more than once
        per bot lifetime."""
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        self._load_corp_code_map()
        if not self._name_map:
            return None
        if self._stock_to_name is None:
            self._stock_to_name = {
                e["stock_code"]: e["name"]
                for entries in self._name_map.values()
                for e in entries
            }
        return self._stock_to_name.get(code)

    def news_search_name(self, stock_code: str) -> Optional[str]:
        """Best Korean news-search term for a KR ticker.

        Prefers the Korean corp_name from company.json ('네이버(주)') over
        the corp_code.xml name, which is sometimes the English DART
        registration ('NAVER' for 035420) that Korean news search returns
        0 hits for. Strips the corporate-form suffix so '네이버(주)' →
        '네이버'. Falls back to the corp_code.xml name when company.json
        is unavailable. Surfaced 2026-06-08 (NAVER 0-news bug)."""
        from bot.market import kr_news_query_name, has_hangul
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        # company.json corp_name is reliably Korean for almost every filer.
        try:
            ci = self.get_company_info(code) or {}
            korean = kr_news_query_name(ci.get("corp_name"))
            if korean and has_hangul(korean):
                return korean
        except Exception:
            pass
        # Fallback: corp_code.xml name (strip suffix; may be English).
        bare = kr_news_query_name(self.stock_code_to_name(code))
        return bare or None

    # ── /api/company.json — corp info incl. KSIC industry code ─────────
    def get_company_info(self, stock_code: str) -> Optional[dict]:
        """Return DART company info dict for the listed entity. The most
        useful field is `induty_code` — 5-digit KSIC (한국표준산업분류)
        code identifying the company's primary line of business. This is
        the authoritative industry classification (vs yfinance's English
        industry tag which is often wrong for KR stocks — 010140.KS
        삼성중공업 surfaced 2026-05-23 as 'Aerospace & Defense' when
        the actual KSIC is C31111 강선건조업 = Shipbuilding).

        Disk-cached 30 days at ~/.tradingagents/cache/dart_company_{corp_code}.json
        because companies rarely change industry classification.

        Returns None on key missing / network failure / corp_code
        unresolved / DART error response. Never raises — callers should
        fall back to whatever they were using before (yfinance industry)."""
        if not self.api_key:
            return None
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return None
        cache_path = _CACHE_DIR / f"dart_company_{corp_code}.json"
        if cache_path.exists():
            age_days = (time.time() - cache_path.stat().st_mtime) / 86400
            if age_days < 30:
                try:
                    return json.loads(cache_path.read_text("utf-8"))
                except Exception:
                    pass
        try:
            resp = requests.get(
                f"{_DART_BASE}/company.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: company.json for %s failed: %s", stock_code, exc)
            return None
        if payload.get("status") not in ("000",):
            return None
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            )
        except Exception:
            pass
        return payload

    # ── /api/list.json — recent disclosures ─────────────────────────────
    @_disk_cache_daily
    def get_recent_disclosures(
        self, stock_code: str, days_back: int = 30, limit: int = 20
    ) -> list[dict]:
        """Return up to `limit` most recent disclosure entries within the
        last `days_back` calendar days. Each entry has 'date', 'title',
        'reporter', 'url'. Empty list when key missing / network fails /
        corp_code unresolved — never raises."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []

        end = date.today()
        bgn = end - timedelta(days=days_back)
        # 페이지네이션 — 한 페이지 100건. limit 이 100 초과면(차트 풀히스토리) 다음
        # 페이지를 이어 받음. 안전 캡 6페이지(600건). limit≤100 이면 1페이지로 종료
        # (build_instrument_context 등 기존 호출 동작 불변).
        out: list[dict] = []
        page_no = 1
        while len(out) < limit and page_no <= 6:
            try:
                resp = requests.get(
                    f"{_DART_BASE}/list.json",
                    params={
                        "crtfc_key": self.api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn.strftime("%Y%m%d"),
                        "end_de": end.strftime("%Y%m%d"),
                        "page_no": page_no,
                        "page_count": 100,
                    },
                    timeout=_HTTP_TIMEOUT,
                )
                payload = resp.json()
            except Exception as exc:
                log.warning("dart: list.json fetch for %s failed: %s", stock_code, exc)
                break
            # DART error envelope: status "000" = success, anything else = no data.
            if payload.get("status") not in ("000",):
                break
            for r in payload.get("list") or []:
                out.append({
                    "date": r.get("rcept_dt") or "",
                    "title": (r.get("report_nm") or "").strip(),
                    "reporter": (r.get("flr_nm") or "").strip(),
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no', '')}",
                })
            try:
                if page_no >= int(payload.get("total_page") or 1):
                    break
            except (TypeError, ValueError):
                break
            page_no += 1
        return out[:limit]

    # 사업연도(year)·reprt_code → (report_nm 키워드, 검색 시작일, 검색 종료일).
    # next_earnings_window()(1052-1079행)의 법정 제출기한(분기 45일/연간 90일)
    # 을 그대로 근거로 삼되, 조기제출·소폭지연을 흡수하도록 앞뒤 여유를 둠.
    # 11013(1분기)·11014(3분기)는 report_nm 이 둘 다 "분기보고서"라 서로
    # 겹치지 않는 이 날짜창으로만 구분한다(4~5월 vs 10~11월, 안전하게 분리).
    def _periodic_report_window(self, year: int, reprt_code: str) -> tuple[str, str, str]:
        y = str(year)
        y1 = str(year + 1)
        return {
            "11013": ("분기보고서", f"{y}0401", f"{y}0531"),
            "11012": ("반기보고서", f"{y}0701", f"{y}0831"),
            "11014": ("분기보고서", f"{y}1001", f"{y}1130"),
            "11011": ("사업보고서", f"{y1}0101", f"{y1}0430"),
        }[reprt_code]

    def find_periodic_report(self, stock_code: str, year: int,
                             reprt_code: str) -> Optional[dict]:
        """분기 실적분석 대시보드(2026-08-16) — 해당 (year, reprt_code) 정기
        보고서의 rcept_no 확보(document.xml 원문 요청에 필요). list.json 을
        pblntf_ty='A'(정기공시)로 좁혀 report_nm 키워드+법정 제출기한 기반
        날짜창으로 매칭. 여러 건 매치 시(정정보고서 등) 가장 최근 접수건.
        DART 키 없음/corp_code 미상/매치 없음 시 None(graceful)."""
        if not self.api_key:
            return None
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return None
        keyword, bgn, end = self._periodic_report_window(year, reprt_code)
        try:
            resp = requests.get(
                f"{_DART_BASE}/list.json",
                params={
                    "crtfc_key": self.api_key, "corp_code": corp_code,
                    "bgn_de": bgn, "end_de": end, "pblntf_ty": "A",
                    "page_count": 20,
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("find_periodic_report: list.json fetch for %s failed: %s",
                        stock_code, exc)
            return None
        if payload.get("status") != "000":
            return None
        matches = [r for r in payload.get("list") or []
                  if keyword in (r.get("report_nm") or "")]
        if not matches:
            return None
        best = max(matches, key=lambda r: r.get("rcept_dt") or "")
        return {"rcept_no": best.get("rcept_no"), "report_nm": best.get("report_nm"),
               "rcept_dt": best.get("rcept_dt")}

    def find_periodic_reports(self, stock_code: str, year: int,
                              reprt_code: str) -> list[dict]:
        """같은 (year, reprt_code) 에 매치되는 정기보고서 **후보 전부**
        (최근 접수 순).

        ⚠️ `find_periodic_report` 는 가장 최근 1건만 준다. 그런데 그 1건에
        **문서가 없는 경우가 있다** — 한화에어로 2025 사업보고서(rcept
        20260319000633)·2026 1분기보고서(20260513000860)가 실측 사례로,
        document.xml 이 `status=014 파일이 존재하지 않습니다` 를 돌려준다
        (정정·첨부 계열 접수건은 원본을 참조만 하고 자체 문서가 없다).
        그러면 원본이 가려져 그 분기가 통째로 빈다 — 차트 막대가 두 칸
        비어 있던 원인이다(사용자 2026-08-17). 호출부가 순서대로 시도해야
        한다."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []
        keyword, bgn, end = self._periodic_report_window(year, reprt_code)
        try:
            resp = requests.get(
                f"{_DART_BASE}/list.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code,
                        "bgn_de": bgn, "end_de": end, "pblntf_ty": "A",
                        "page_count": 20},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: list.json for %s failed: %s", stock_code, exc)
            return []
        if payload.get("status") != "000":
            return []
        matches = [r for r in payload.get("list") or []
                   if keyword in (r.get("report_nm") or "")]
        matches.sort(key=lambda r: r.get("rcept_dt") or "", reverse=True)
        out = [{"rcept_no": r.get("rcept_no"), "report_nm": r.get("report_nm"),
                "rcept_dt": r.get("rcept_dt")} for r in matches]

        # 2차 — **창 밖에 늦게 접수된 정정**. 제출기한 창(예: 1분기 4/01~5/31)만
        # 보면 그 뒤에 낸 정정이 통째로 안 보인다. 정정이 나오면 원본 문서가
        # 내려가는 경우가 있어(한화에어로 2026 1분기: 원본 20260513000860 이
        # `status=014 파일이 존재하지 않습니다`) 그 분기가 영영 빈다.
        # ⚠️ 창을 넓히면 **같은 키워드의 다음 분기**가 딸려온다(1분기와 3분기가
        # 둘 다 '분기보고서'). 그래서 보고서명의 기간 접미사 `(YYYY.MM)` 로
        # 정확히 걸러낸다. 접미사가 없거나 결산월이 달라 안 맞는 회사는 이
        # 2차 목록이 비므로 기존 동작 그대로다(1차 결과만 쓴다).
        period = {"11013": f"{year}.03", "11012": f"{year}.06",
                  "11014": f"{year}.09", "11011": f"{year}.12"}[reprt_code]
        try:
            import datetime as _dt
            end2 = (_dt.datetime.strptime(end, "%Y%m%d")
                    + _dt.timedelta(days=210)).strftime("%Y%m%d")
            resp2 = requests.get(
                f"{_DART_BASE}/list.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code,
                        "bgn_de": end, "end_de": end2, "pblntf_ty": "A",
                        "page_count": 50},
                timeout=_HTTP_TIMEOUT,
            )
            pay2 = resp2.json()
            if pay2.get("status") == "000":
                seen = {r["rcept_no"] for r in out}
                late = [r for r in pay2.get("list") or []
                        if keyword in (r.get("report_nm") or "")
                        and period in (r.get("report_nm") or "")
                        and r.get("rcept_no") not in seen]
                late.sort(key=lambda r: r.get("rcept_dt") or "", reverse=True)
                out += [{"rcept_no": r.get("rcept_no"),
                         "report_nm": r.get("report_nm"),
                         "rcept_dt": r.get("rcept_dt")} for r in late]
        except Exception as exc:
            log.debug("find_periodic_reports: 후행 정정 조회 실패 %s: %s",
                      stock_code, exc)
        return out

    # ── /api/elestock.json — insider / major shareholder holdings ──────
    @_disk_cache_daily
    def get_insider_holdings(self, stock_code: str) -> list[dict]:
        """Return rows of officer / major shareholder current holdings.
        Each row: 'name', 'role', 'shares', 'pct', 'changed_on'. Empty
        list on any failure mode."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []

        try:
            resp = requests.get(
                f"{_DART_BASE}/elestock.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: elestock for %s failed: %s", stock_code, exc)
            return []

        if payload.get("status") not in ("000",):
            return []
        rows = payload.get("list") or []
        out: list[dict] = []
        for r in rows:
            # DART returns holding rows with rolling history; we only want
            # the latest per-person snapshot. Caller can dedupe later if
            # needed — for now expose everything and let the prompt
            # builder cap the list.
            #
            # Field names per DART /api/elestock.json spec (verified
            # against 한솔케미칼 2026-05-17 case where all 5 entries
            # showed 0.00% with the old 'stkqy' / 'stkrt' guesses —
            # those field names don't exist on this endpoint):
            #   sp_stock_lmp_cnt   — 특정증권등 소유수
            #   sp_stock_lmp_rate  — 특정증권등 소유비율 (%, e.g. "1.65")
            # Fallback to the old names just in case DART exposes them
            # for some response shapes; defensive against future schema
            # changes.
            shares_raw = str(
                r.get("sp_stock_lmp_cnt") or r.get("stkqy") or "0"
            ).replace(",", "")
            try:
                shares = int(shares_raw)
            except ValueError:
                shares = 0
            pct_raw = str(
                r.get("sp_stock_lmp_rate") or r.get("stkrt") or "0"
            ).replace(",", "")
            try:
                pct = float(pct_raw)
            except ValueError:
                pct = 0.0
            # ⚠️ pct sanity (Samsung 005930 2026-05-31 review): DART
            # elestock 의 sp_stock_lmp_rate 는 가끔 100.0 (해당 임원 본인
            # 보유분 중 특정증권 비율 = 항상 100%) 으로 와, 이를 "회사 전체
            # 지분율" 로 오인하면 "이종민 부사장 100% 지분" 같은 금융정보
            # 파괴 발생. 시총 2,000조 회사의 회사 지분 100% 를 개인이 가질
            # 수 없음. ≥50% 는 회사 지분율이 아닌 본인 보유분 비율로 판단,
            # pct 를 None 처리 (표시단에서 '회사 지분율 N/A' 로 렌더).
            pct_is_company = pct < 50.0
            out.append({
                "name": (r.get("repror") or "").strip(),
                "role": (r.get("isu_exctv_ofcps") or "").strip(),
                "shares": shares,
                "pct": pct if pct_is_company else None,
                "pct_suspect": not pct_is_company,
                "changed_on": (r.get("rcept_dt") or "").strip(),
            })
        return out

    # ── 최대주주 현황 (hyslrSttus.json) ──────────────────────────────

    # ── /api/stockTotqySttus.json — 주식의 총수 현황 ───────────────────
    # FnGuide BPS 산식(사용자 2026-08-23 제공):
    #   BPS = (지배주주지분)자본총계 / 수정기말발행주식수
    #         (보통주+우선주, **자사주차감**)
    # 우리는 상장주식수(자사주 포함)로 나눠 왔다 — 자사주가 많은 회사에서
    # BPS 가 체계적으로 높게 나온다. POSCO홀딩스 005490.KS 실측(2026-08-23):
    # 우리 809,716 vs 네이버 759,917 (+6.6%). 자사주는 유통주식이 아니고
    # 자본은 이미 취득원가만큼 차감돼 있어 분모에 넣으면 과대계상이다.
    # ⚠️ EPS 는 **그대로 상장주식수**로 나눈다 — FnGuide 는 EPS 분모에만
    # 자사주를 포함한다(자기 산식에 그렇게 적혀 있다, #204 실측).
    @_cache_ver(2)          # v2: 수권주식수(isu_stock_totqy) → 실제 발행분
    def get_share_totals(self, stock_code: str) -> dict:
        """{issued, treasury, distributed, basis} — 없으면 빈 dict.

        `basis` = 어느 보고서에서 왔는지(`2026 11012` 등) — 화면이 기준을
        말할 수 있어야 한다(#43). 최신 정기보고서부터 거슬러 첫 성공을
        쓴다(자사주 수는 분기마다 바뀐다).
        """
        if not self.api_key:
            return {}
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return {}
        today = date.today()
        # 최신 → 과거. 분기보고서까지 훑어야 자사주 취득이 반영된다.
        plan: list = []
        for yr in (today.year, today.year - 1):
            for rc in ("11014", "11012", "11013", "11011"):
                plan.append((str(yr), rc))
        for bsns_year, reprt_code in plan[:_SHARE_TOT_PROBE_N]:
            rows = self._fetch_share_totals(corp_code, bsns_year, reprt_code)
            if not rows:
                continue
            got = _pick_share_totals(rows)
            if got:
                got["basis"] = f"{bsns_year} {reprt_code}"
                return got
        return {}

    # 분기별 **발행주식수 이력** — EPS 분모(수정평균 발행주식수)의 재료다.
    # FnGuide 산식(사용자 2026-08-23 제공):
    #   EPS = (지배주주지분)당기순이익 × 1000 / 수정평균발행주식수
    # 우리는 **기말** 상장주식수로 나눠 왔다 — 주식수가 기중에 변한 회사
    # (자사주 소각·증자)에서 EPS 가 갈린다.
    @_cache_ver(1)
    def get_share_totals_series(self, stock_code: str, n: int = 5) -> list:
        """최신 → 과거 순으로 최대 `n` 개 보고서의 주식의 총수 현황.

        각 항목 = {year, reprt_code, period(YYYY.MM), issued, treasury,
        distributed}. 없으면 빈 리스트.
        """
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []
        out: list = []
        tried = 0
        for yr in (date.today().year, date.today().year - 1,
                   date.today().year - 2):
            # 한 해 안에서 **최신 → 과거**(사업보고서 → 3Q → 반기 → 1Q)
            for rc, mm in (("11011", "12"), ("11014", "09"),
                           ("11012", "06"), ("11013", "03")):
                if len(out) >= n or tried >= _SHARE_TOT_SERIES_N:
                    return out
                tried += 1
                got = _pick_share_totals(
                    self._fetch_share_totals(corp_code, str(yr), rc))
                if got:
                    got.update({"year": yr, "reprt_code": rc,
                                "period": f"{yr}.{mm}"})
                    out.append(got)
        return out

    def _fetch_share_totals(self, corp_code: str, bsns_year: str,
                            reprt_code: str) -> Optional[list]:
        try:
            resp = requests.get(
                f"{_DART_BASE}/stockTotqySttus.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code,
                        "bsns_year": bsns_year, "reprt_code": reprt_code},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: stockTotqySttus %s/%s/%s failed: %s",
                        corp_code, bsns_year, reprt_code, exc)
            return None
        if payload.get("status") != "000":
            log.debug("dart: stockTotqySttus %s %s/%s status=%s msg=%s",
                      corp_code, bsns_year, reprt_code, payload.get("status"),
                      payload.get("message", ""))
            return None
        return payload.get("list") or None

    def _fetch_hyslr(self, corp_code: str, bsns_year: str) -> Optional[list]:
        """Raw fetch for hyslrSttus. Returns row list or None."""
        try:
            resp = requests.get(
                f"{_DART_BASE}/hyslrSttus.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": "11011",
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: hyslrSttus %s/%s failed: %s", corp_code, bsns_year, exc)
            return None
        status = payload.get("status")
        if status != "000":
            log.info("dart: hyslrSttus %s year=%s status=%s msg=%s",
                     corp_code, bsns_year, status, payload.get("message", ""))
            return None
        rows = payload.get("list") or []
        if rows:
            log.info("dart: hyslrSttus %s year=%s rows=%d keys=%s",
                     corp_code, bsns_year, len(rows), list(rows[0].keys())[:15])
        return rows if rows else None

    _HYSLR_SHARES_CANDIDATES = [
        "trmend_posesn_stock_co", "bsis_posesn_stock_co",
        "posesn_stock_co", "trmend_posesn_stkqy", "bsis_posesn_stkqy",
        "posesn_stkqy", "stock_co", "stkqy",
    ]
    _HYSLR_PCT_CANDIDATES = [
        "trmend_posesn_stock_qota_rt", "bsis_posesn_stock_qota_rt",
        "posesn_stock_qota_rt", "trmend_posesn_stkqy_rate",
        "bsis_posesn_stkqy_rate", "posesn_stkqy_rate", "stock_qota_rt",
    ]
    _HYSLR_SKIP_KEYS = frozenset({
        "rcept_no", "corp_cls", "corp_code", "corp_name", "nm",
        "relate", "rm", "bsns_year", "reprt_code", "stock_knd",
        "se", "change_on", "change_cause",
    })

    @staticmethod
    def _hyslr_extract_int(row: dict, candidates: list) -> int:
        for key in candidates:
            v = row.get(key)
            if v is None:
                continue
            vs = str(v).replace(",", "").strip()
            if not vs or vs == "-":
                continue
            try:
                return int(vs)
            except ValueError:
                try:
                    return int(float(vs))
                except (ValueError, OverflowError):
                    continue
        return 0

    @staticmethod
    def _hyslr_extract_float(row: dict, candidates: list) -> float:
        for key in candidates:
            v = row.get(key)
            if v is None:
                continue
            vs = str(v).replace(",", "").strip()
            if not vs or vs == "-":
                continue
            try:
                return float(vs)
            except (ValueError, OverflowError):
                continue
        return 0.0

    def _parse_hyslr_rows(self, rows: list) -> list[dict]:
        if not rows:
            return []
        try:
            log.info("dart: hyslrSttus FULL first row: %s",
                     json.dumps(rows[0], ensure_ascii=False)[:600])
        except Exception:
            pass

        first_keys = set(rows[0].keys())
        extra_shares: list[str] = []
        extra_pct: list[str] = []
        for k in sorted(first_keys - self._HYSLR_SKIP_KEYS):
            if k in self._HYSLR_SHARES_CANDIDATES or k in self._HYSLR_PCT_CANDIDATES:
                continue
            kl = k.lower()
            if any(p in kl for p in ("_rt", "_rate", "qota", "pct")):
                extra_pct.append(k)
            elif any(p in kl for p in ("_co", "_qy", "stock", "stkqy", "cnt")):
                extra_shares.append(k)

        all_shares = list(self._HYSLR_SHARES_CANDIDATES) + extra_shares
        all_pct = list(self._HYSLR_PCT_CANDIDATES) + extra_pct
        log.info("dart: hyslrSttus candidates: shares=%s pct=%s",
                 all_shares[:10], all_pct[:10])

        out: list[dict] = []
        for r in rows:
            nm = (r.get("nm") or r.get("inv_prm") or r.get("aflte_nm")
                  or r.get("cmpny_nm") or "").strip()
            if not nm:
                continue
            rel = (r.get("relate") or r.get("rel_btr_at")
                   or r.get("relt") or r.get("rel_corp_nm") or "").strip()
            shares = self._hyslr_extract_int(r, all_shares)
            pct = self._hyslr_extract_float(r, all_pct)
            note = (r.get("rm") or r.get("bsn_sumry") or "").strip()
            out.append({
                "name": nm,
                "relation": rel,
                "shares": shares,
                "pct": pct,
                "note": note,
            })
        return out

    def get_major_shareholders(self, stock_code: str) -> list[dict]:
        """DART 최대주주 현황 — major shareholder list from annual report.
        Tries year-1 then year-2 fallback. Each row: {name, relation, shares, pct, note}."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []
        ck = f"majsh3_{corp_code}"
        cached = self._disk_get(ck)
        if cached is not None:
            return cached
        y = date.today().year
        rows = self._fetch_hyslr(corp_code, str(y - 1))
        if not rows:
            rows = self._fetch_hyslr(corp_code, str(y - 2))
        if not rows:
            return []
        out = self._parse_hyslr_rows(rows)
        if out:
            self._disk_set(ck, out)
        return out

    # ── 타법인 출자현황 (otrCprInvstmntSttus.json) — 계열회사/자회사 ──

    def get_affiliate_investments(self, stock_code: str) -> list[dict]:
        """DART 타법인 출자현황 — investment in other corporations (계열회사 proxy).
        Tries year-1 then year-2 fallback.
        Each row: {name, purpose, shares, pct, book_value, total_assets, note}."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []
        ck = f"affinv_{corp_code}"
        cached = self._disk_get(ck)
        if cached is not None:
            return cached
        y = date.today().year
        rows = self._fetch_otr_cpr(corp_code, str(y - 1))
        if not rows:
            rows = self._fetch_otr_cpr(corp_code, str(y - 2))
        if not rows:
            return []
        out = self._parse_otr_cpr_rows(rows)
        if out:
            self._disk_set(ck, out)
        return out

    def _fetch_otr_cpr(self, corp_code: str, bsns_year: str) -> Optional[list]:
        """Raw fetch for otrCprInvstmntSttus."""
        try:
            resp = requests.get(
                f"{_DART_BASE}/otrCprInvstmntSttus.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": "11011",
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: otrCprInvstmntSttus %s/%s failed: %s",
                        corp_code, bsns_year, exc)
            return None
        status = payload.get("status")
        if status != "000":
            log.info("dart: otrCprInvstmntSttus %s year=%s status=%s msg=%s",
                     corp_code, bsns_year, status, payload.get("message", ""))
            return None
        rows = payload.get("list") or []
        if rows:
            log.info("dart: otrCprInvstmntSttus %s year=%s rows=%d keys=%s",
                     corp_code, bsns_year, len(rows), list(rows[0].keys())[:15])
        return rows if rows else None

    def _parse_otr_cpr_rows(self, rows: list) -> list[dict]:
        out: list[dict] = []
        for r in rows:
            nm = (r.get("inv_prm") or r.get("cmpny_nm") or r.get("nm")
                  or r.get("aflte_nm") or "").strip()
            if not nm or nm == "-":
                continue
            purpose = (r.get("invstmnt_purps") or r.get("inv_purps")
                       or r.get("purps") or "").strip()
            shares_raw = str(
                r.get("trmend_blce_qy") or r.get("bsis_blce_qy")
                or r.get("posesn_stkqy") or "0"
            ).replace(",", "").strip()
            try:
                shares = int(shares_raw) if shares_raw and shares_raw != "-" else 0
            except ValueError:
                shares = 0
            pct_raw = str(
                r.get("trmend_blce_qota_rt") or r.get("bsis_blce_qota_rt")
                or r.get("qota_rt") or "0"
            ).replace(",", "").strip()
            try:
                pct = float(pct_raw) if pct_raw and pct_raw != "-" else 0.0
            except ValueError:
                pct = 0.0
            bv_raw = str(
                r.get("trmend_blce_acntbk_amount") or r.get("acntbk_amount")
                or "0"
            ).replace(",", "").strip()
            try:
                book_value = int(bv_raw) if bv_raw and bv_raw != "-" else 0
            except ValueError:
                book_value = 0
            ta_raw = str(
                r.get("recent_bsns_year_fnnr_sttus_tot_assets")
                or r.get("tot_assets") or "0"
            ).replace(",", "").strip()
            try:
                total_assets = int(ta_raw) if ta_raw and ta_raw != "-" else 0
            except ValueError:
                total_assets = 0
            note = (r.get("rm") or "").strip()
            out.append({
                "name": nm,
                "purpose": purpose,
                "shares": shares,
                "pct": pct,
                "book_value": book_value,
                "total_assets": total_assets,
                "note": note,
            })
        return out

    def _disk_get(self, key: str):
        p = Path.home() / ".tradingagents" / "cache" / "dart" / f"{key}.json"
        if not p.exists():
            return None
        try:
            age_h = (time.time() - p.stat().st_mtime) / 3600
            if age_h >= 168:  # 7 day cache (yearly report data)
                return None
            return json.loads(p.read_text())
        except Exception:
            return None

    def _disk_set(self, key: str, data):
        try:
            d = Path.home() / ".tradingagents" / "cache" / "dart"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    # ── earnings window estimate ────────────────────────────────────────
    # ── D1 Phase 2: 정규화 재무제표 + 비율 ───────────────────────────

    def get_normalized_financials(
        self,
        ticker: str,
        year: Optional[int] = None,
        fs_div: str = "CFS",
        reprt_code: str = "11011",
    ) -> Optional[dict]:
        """yfinance 의 KR 종목 재무 corruption (단위 mismatch / financial
        Currency=USD glitch / TTM vs FY divergence) 발생 시 DART 의
        K-IFRS 정기보고서에서 직접 추출한 KRW 단위 재무 데이터로
        override. D1 Phase 2 (2026-05-19). reprt_code 확장(2026-08-16,
        분기 실적분석 대시보드) — 분기 재무는 **누적치**(11012=반기누적,
        11014=3분기누적)라 단일분기 산출은 호출부(bot/dart_quarterly.py)가
        인접 reprt_code 결과를 차분해야 함(이 함수는 원자 조회만 담당).

        Returns dict like:
            {
                "year": 2025,
                "reprt_code": "11011",  # 11011=사업보고서(연간) 11012=반기
                                         # 11013=1분기 11014=3분기(모두 누적)
                "fs_div": "CFS",  # 연결 (CFS) / 별도 (OFS)
                "financials": {
                    "매출": int (원), "영업이익": int, "당기순이익": int,
                    "자산총계": int, "부채총계": int, "자본총계": int,
                    "유동자산": int, "유동부채": int, "이익잉여금": int,
                    "EPS": float (원), ...
                },
                "ratios": {
                    "영업이익률": float (%), "순이익률": float (%),
                    "ROE": float (%), "ROA": float (%), "부채비율": float (%),
                    "유동비율": float (%), "이자보상배율": float, ...
                },
            }
        또는 None (DART 키 없음 / 종목 미상장 / 보고서 미발표 등).

        Args:
            ticker: yfinance ticker (005930.KS / 035720.KQ 등).
            year: 사업연도. None 시 직전 사업연도 자동 (date.today().year - 1).
            fs_div: 'CFS' = 연결재무제표 (default), 'OFS' = 별도재무제표.
            reprt_code: '11011'=사업보고서(연간, default, 하위호환) '11012'=
                반기보고서 '11013'=1분기보고서 '11014'=3분기보고서.
        """
        if not self.api_key:
            return None
        code = (ticker or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        try:
            corp_map = self._load_corp_code_map()
        except Exception as exc:
            log.warning("get_normalized_financials: corp_code map load failed: %s", exc)
            return None
        corp_code = corp_map.get(code)
        if not corp_code:
            return None

        target_year = year or (date.today().year - 1)

        # 7일 디스크 캐시 — reprt_code 확장(최대 4종×연도)으로 분기 시계열
        # 조립 시 콜 수가 늘어나 캐시 없이는 페이지 방문마다 DART 를 수 회
        # 두드리게 됨(get_major_shareholders 등과 동일 _disk_get/_disk_set
        # 관례, 786-833행 참조). 확정된(과거) 분기는 안 바뀌므로 7일 충분 —
        # "이번 분기가 아직 미확정"인 최신분기 캐시 단축은 호출부(probe)가
        # 별도 6~12h 캐시로 처리(원자 조회 자체는 길게 캐시해도 무해).
        # ⚠️ 캐시 키 v2(2026-08-19, code-review 발견): financials_cumulative
        # 필드를 이 커밋에서 추가했는데 키를 안 바꾸면, 오늘 이미 조회돼
        # 캐시된(이 필드 없는 구버전) 엔트리가 7일간 그대로 서빙돼 4분기
        # 파생이 계속 조용히 실패한다(nine_mo=None→break). qfin→qfin2 로
        # 버저닝해 구 캐시를 자연스럽게 우회(dart_corpcode_v2.json 등 기존
        # 관례와 동일).
        # v4(2026-08-16): financials 에 `_src`(채택 그룹) + `_component_accounts`
        # (구성요소 계정) 사이드채널이 추가됐다. 옛 캐시(7일 TTL)엔 그게 없어
        # 계정 일치 가드와 '총액 아님' 표기가 최대 일주일 조용히 무력화된다
        # — 정작 이 fix 를 유발한 138040.KS 가 그 캐시에 들어 있다.
        # 같은 실패를 이 함수에서만 두 번 겪었다(qfin→qfin2→qfin3).
        # v5(2026-08-19): 계정 우선순위가 바뀌었다(표준 태그·이름 정규화) —
        # 옛 캐시엔 이자수익이 '매출'로 굳어 있어 7일간 그대로 서빙된다.
        # **코드를 고쳐도 캐시는 안 바뀐다**(실수 #18) → 키를 올려 즉시 무효화.
        # v8(2026-08-21): 현금흐름 계정(영업활동현금흐름·유형/무형자산취득)
        # + FCF 가 추가됐다. 이번에도 **버전을 안 올려** 최근 조회분이 CF 없는
        # 옛 캐시를 7일간 서빙했다 — 사용자 화면에서 FY2025·25.3Q~26.2Q 만
        # FCF 가 비고 캐시가 없던 옛 기간만 값이 나왔다(프로브로 확인).
        # 같은 실패가 이 함수에서 **세 번째**(v4·v5·v8)라 규율로는 못 막는다
        # → 아래 상수 하나에서 키를 만들고, 회귀가 "파서가 내는 canonical
        # 키가 바뀌면 버전을 올려라"를 강제한다.
        ck = f"qfin{_FIN_CACHE_VER}_{corp_code}_{target_year}_{reprt_code}_{fs_div}"
        cached = self._disk_get(ck)
        if cached is not None:
            return cached

        # fnlttSinglAcntAll.json — 단일 회사 전체 재무제표 (전체 항목)
        url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(target_year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        try:
            resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning(
                "get_normalized_financials: fetch failed for %s (%d, %s, %s): %s",
                code, target_year, reprt_code, fs_div, exc,
            )
            return None

        if payload.get("status") != "000":
            # 013 = 조회된 데이터 없음, 020 = 사용 한도 초과, etc.
            log.info(
                "get_normalized_financials: DART status=%s for %s (%d, %s) — skipping",
                payload.get("status"), code, target_year, reprt_code,
            )
            return None

        items = payload.get("list") or []
        financials = _extract_dart_financials(items)
        if not financials:
            return None
        # FCF — 산식은 `bot.fcf` 한 곳(#38).
        # ⚠️ **사업보고서(11011)에만** 붙인다. 분기/반기보고서의 현금흐름은
        # DART 가 **누적**(연초~해당분기말)으로 주기 때문에 여기서 계산하면
        # 그 값은 '이 분기의 FCF' 가 아니라 '연초부터의 FCF' 다 — 이름은
        # 같은데 뜻이 다른 값이 dict 에 남는다(#34 의 씨앗). 단일분기 FCF 는
        # `dart_quarterly` 가 누적을 되돌린 **뒤에** 만든다.
        if reprt_code == "11011":
            try:
                from bot.dart_quarterly import _attach_fcf as _fcf
                _fcf([{"financials": financials}])
            except Exception as exc:                           # noqa: BLE001
                log.info("dart_client: FCF 계산 건너뜀(%s): %s", ticker, exc)
        _fill_equity_fallbacks(financials)
        ratios = calc_kr_financial_ratios(financials)
        result = {
            "year": target_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
            "financials": financials,
            "ratios": ratios,
        }
        # 분기/반기보고서는 thstrm_add_amount(당기누적)도 함께 보존 — 4분기
        # 단독 실적(연간-9개월누적)을 산출하려면 3분기보고서(11014)의 이
        # 누적치가 필요하다(bot/dart_quarterly.py, 2026-08-19 VM 실측 확인).
        # 사업보고서(11011)는 add_amount 필드 자체가 없어 None.
        if reprt_code != "11011":
            cum = _extract_dart_financials(items, amount_field="thstrm_add_amount")
            if cum:
                result["financials_cumulative"] = cum
        self._disk_set(ck, result)
        return result

    def next_earnings_window(self, stock_code: str, today: date | None = None) -> Optional[tuple[date, date]]:
        """Infer the most-likely next KR earnings disclosure window.

        Korean listed companies must file quarterly reports within 45
        days of quarter end (분기보고서) and annual/Q4 reports within
        90 days of fiscal year end (사업보고서). Assuming a Dec fiscal
        year (true for ~95% of KOSPI), the windows are:
          - Q1 (Mar-end) → due by May 15
          - Q2 (Jun-end) → due by Aug 14
          - Q3 (Sep-end) → due by Nov 14
          - Q4 + annual (Dec-end) → due by Apr 1 next year

        Returns (window_start, window_end) of the NEXT upcoming window,
        or None if computation fails."""
        today = today or date.today()
        year = today.year
        # Build the four nominal due-by dates for this fiscal year.
        candidates = [
            (date(year, 5, 15), date(year, 4, 15)),    # Q1 window 4/15-5/15
            (date(year, 8, 14), date(year, 7, 14)),    # Q2 window 7/14-8/14
            (date(year, 11, 14), date(year, 10, 14)),  # Q3 window 10/14-11/14
            (date(year + 1, 4, 1), date(year + 1, 3, 1)),  # Q4/annual 3/1-4/1
        ]
        for due, window_start in candidates:
            if today <= due:
                return (window_start, due)
        # All windows for this calendar year passed — return next year's Q1.
        return (date(year + 1, 4, 15), date(year + 1, 5, 15))


# Process-wide cached instance so the corp_code map only loads once
# per bot lifetime. Reset by restarting the bot.
_singleton: DartClient | None = None


def get_dart() -> DartClient:
    """Return the shared DartClient. Call this from analysts / pre-fetch
    helpers instead of constructing a new client per analysis."""
    global _singleton
    if _singleton is None:
        _singleton = DartClient()
    return _singleton
