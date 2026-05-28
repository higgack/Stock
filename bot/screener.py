"""Bottleneck Screener — Phase α MVP (2026-05-28).

Multi-stock idea generation distinct from NOAH /ticker (single-stock deep
dive). Implements the 'ruthless bottleneck' framework: Theory of
Constraints, rerate focus, niche 2-3 layers below obvious narratives,
global mandate (US/KR/JP/TW/EU/CN), 3 size tiers. See CLAUDE.md
'Bottleneck Screener' section for full design.

Phase α scope:
  - Single Pro call orchestrating Phases 1·2·4·5 in one shot
  - yfinance ticker validation (reject hallucinated tickers)
  - Forward-signal weighted output (Tier A/B/C/D)
  - Telegram-friendly chunked output + disclaimer

Deferred to Phase β:
  - Phase 3 build_instrument_context parallel deep-dive
  - 24h cache (~/.tradingagents/screener_cache/)
  - Dashboard archive + URL link
  - NOAH /ticker deep link
  - Multiple domain registry (EV / pharma / 신재생 / rare earth ...)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("bot.screener")

# ── Theme registry — Wave 1 (2026-05-29) ────────────────────────────────
# 5 domains live in bot/screener_themes/{bottleneck,ev,defense,pharma,solar}.py
# Each module exports a top-level THEME dict; the registry auto-discovers
# them on import. /screener with no argument routes to 'bottleneck' (AI
# Data Center), preserving Phase α/β default behavior. New Wave 2/3
# domains are added by dropping another module — no edits here required.
from bot.screener_themes import resolve as _resolve_theme, available_summary

# ── Pricing for Gemini cost tracking (per 1M tokens, USD → KRW @ 1330) ─
_PRO_INPUT_USD_PER_M = 1.25      # gemini-2.5-pro input
_PRO_OUTPUT_USD_PER_M = 10.00    # gemini-2.5-pro output
_USD_TO_KRW = 1330.0
_USAGE_LOG = os.path.expanduser("~/.tradingagents/screener_usage.jsonl")


@dataclass
class ScreenerResult:
    domain: str
    raw_output: str
    validated_tickers: list[str]
    rejected_tickers: list[str]
    elapsed_sec: float
    cost_krw: float


# ── Phase 1·2·4·5 orchestration prompt (Phase α: single Pro call) ──────

def _today_kst_iso() -> str:
    """오늘 (KST) YYYY-MM-DD 문자열 — Pro 의 시제 기준 anchor."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _build_prompt(theme: dict) -> str:
    """Construct the ruthless-buy-side prompt for the given theme.

    Phase α merges Phase 1·2·4·5 into one Pro call for orchestration
    simplicity. Phase β will split into Pro(Phase 1·2) + Flash(Phase 3) +
    Pro(Phase 4·5) for parallelism and cost control.
    """
    layers = "\n".join(f"  - {l}" for l in theme["binding_layer_taxonomy"])
    catalysts = "\n".join(f"  - {c}" for c in theme["catalyst_types"])
    regions = "\n".join(
        f"  - {layer}: {region}"
        for layer, region in theme["regional_concentration"].items()
    )
    today = _today_kst_iso()

    return f"""오늘 (today, KST): {today}

너는 냉혹한 buy-side 애널리스트다. 어떤 종목·섹터·서사·과거
콜에도 충성심 없음. 오직 수익. 컨센서스에 제값 주는 건 알레르기. choke
point owner 가 rent 독식한다는 Theory of Constraints 가 작업 anchor.

OBJECTIVE — 도메인 "{theme['domain']}" ({theme['horizon']} 관점) 의
binding constraint 가 어디 있고, choke point owner 가 누구인지 식별.
4-6개 niche 테마 surface — 2-3 layers 깊은 sub-layer (GPU·전력 같은
1차 헤드라인 X).

이 도메인의 주요 binding layer (참고):
{layers}

주요 catalyst (참고):
{catalysts}

지리적 집중 (이미 알려진 SPOF):
{regions}

GLOBAL MANDATE — US 편향 금지. KR (KRX) / JP (TSE) / TW (TWSE/TPEx) /
EU (Germany .DE / France .PA / Switzerland .SW / Netherlands .AS /
Nordics) / CN (A주 .SS/.SZ · H주 .HK) 적극 포함. ADR 있으면 병기.

SIZE TIERS — 각 테마별 3종목 (~$100M micro / ~$1B mid / ~$10B large).
clean public name 없으면 'no clean public name' 명시 + 가장 가까운
대체. 가짜 ticker 절대 금지 — 모든 ticker 는 사후 yfinance 검증됨.
시총은 USD 환산 후 분류.

SIGNAL WEIGHTING — bottleneck rerating 은 forward signal 이 결정,
재무제표는 lagging. 점수 배분:
- Tier A (50%, Catalyst): 신제품/신기술 / 신규 계약·qual / 경쟁사
  stumble / 정책 변경 (IRA·BIS·보조금·관세) / 캐파 expansion + online
  date / sell-side PT·rating 변경 (30일).
- Tier B (25%, 실적 Content): forward guidance / 본인 constraint 인용
  ('limited by X' → 한 단계 아래 진짜 수익자) / backlog QoQ + RPO /
  가격 인상 + 효력 시점 / utilization tightness.
- Tier C (15%, 시황): 30/90일 sector 상대강도 / 옵션 IV (이벤트 임박) /
  외인·기관·港股통 flow / 단기 모멘텀.
- Tier D (10%, 재무 sanity only): 시총·PER·PSR (priced in 평가) /
  매출 YoY / 적자 의도된 투자 vs 무너지는 모델 구분.

OUTPUT FORMAT — Telegram HTML 호환. 다음 순서로 출력:

1. <b>📍 현재 binding constraint</b> — 한 단락. 지금 어느 layer 가
   binding 중이고 다음에 binding 할 layer 는 어디인지.

2. <b>📊 Master Table</b> — 4-6 테마 × 3 티어 = 12-18 행. 각 행:
   • <b>[테마]</b> · 티어 · <code>TICKER</code> (+ADR/시장) · 회사명
     │ Tier A 신호 1줄 │ Tier B 신호 1줄 │ Tier C 신호 1줄 │ 가격에
     반영도 1줄 │ catalyst+시기 │ kill trigger
   각 신호는 'sourced' (출처 publisher/date) 또는 'inferred' (LLM 추론)
   명시. 추론을 사실로 위장 금지.

3. <b>🏆 Top 3 conviction picks</b> — 전체에서 3개. 각: 이유 1줄 + 티어
   + 접근 경로 (ADR / local / illiquid 여부).

4. <b>💡 Bottom line</b> — 가장 강한 1개 종목 + why now.

5. ⚠️ <b>Disclaimer</b>: '본 출력은 6-{theme['horizon'].split('-')[1]}
   thesis. 5거래일 트레이드 아님. NOAH /TICKER 로 deep dive 권장. 교육
   목적, 추천 아님.'

6. 🤖 <b>TOP_3 MACHINE-PARSEABLE TAIL</b> — 출력의 **맨 마지막 줄에** 다음
   JSON 블록 추가 (백엔드 outcome 추적용 — 5/15/30일 알파 자동 계산
   파이프라인에 feed. 사용자에게는 화면에서 strip 처리):

   <TOP_3_JSON>
   [
     {{"rank": 1, "ticker": "267260.KS", "tier": "M", "company": "HD Hyundai Electric", "thesis_line": "변압기 공급 부족 핵심 수혜주 — 3년+ 수주잔고"}},
     {{"rank": 2, "ticker": "VRT", "tier": "L", "company": "Vertiv Holdings", "thesis_line": "..."}},
     {{"rank": 3, "ticker": "089030.KQ", "tier": "S", "company": "Techwing", "thesis_line": "..."}}
   ]
   </TOP_3_JSON>

   - ticker 는 yfinance 정확한 심볼 (예: '267260.KS', 'VRT', '0700.HK',
     'TPRO.MI', 'MRN.PA') — 백엔드가 가격 fetch 에 그대로 사용.
   - tier 는 AUTHORITATIVE TIER 값 (S/M/L).
   - rank 1/2/3 만, 정확히 3개.
   - thesis_line 은 한국어 1줄 (~80자 이내).
   - 이 JSON block 은 본문 paragraph 도, master table 도 아니라 별도
     machine-parseable tail. Disclaimer 후 출력.

7. 🤖 <b>TICKERS_USED MACHINE-PARSEABLE TAIL</b> — TOP_3_JSON 바로 뒤에
   본문에서 cite 한 **모든** ticker 를 정확히 한 번씩 포함한 JSON 배열
   별도 출력 (검증 noise 차단 — 2026-05-29 defense review 에서 AESA /
   AUKUS / K9 / ICBM 등 텍스트 약어가 regex ticker 추출에 오인된 사례):

   <TICKERS_USED_JSON>
   ["RHM.DE", "NOC", "012450.KS", "RTX", "2340.TW", "BWXT", "HII",
    "DRO.AX", "ESLT", "PLTR"]
   </TICKERS_USED_JSON>

   - 본문 Master Table 행 + Top-3 picks 에 실제 등장한 ticker 만 포함.
   - 누락 금지 (백엔드가 이 list 의 ticker 만 yfinance 검증). 본문에
     없는 ticker 추가 금지 (가짜 검증 통과 차단).
   - yfinance 정확 심볼 (suffix 포함) — yfinance 가 인식하지 못하는
     로컬 약어 (CATL · K9 · ICBM 등) 절대 포함 금지.
   - 이 JSON 도 화면에서는 strip — 백엔드 ticker 검증의 primary source.

RULES — 절대:
- 모든 외부 figure 에 날짜 stamp + FX stamp.
- 추론과 sourced fact 분리.
- ticker 가짜 생성 금지 (yfinance 검증됨).
- niche layer 우선, 1차 헤드라인 (NVDA/AAPL 등) 후순위.
- 5거래일 horizon 언급 금지 — 본 출력은 6-18개월 thesis.
- **유동성 경고 의무 (S 티어 ~$100M micro-cap)**: 모든 S 티어 행은
  Kill Trigger 다음 줄에 다음 형식으로 유동성 caveat 명시 — '⚠️ 시총
  ~$XXM 소형주 — 일일 거래대금/유동성 제한 + 단기 변동성 ±10-20%
  정상 범위 + 기관 진입 어려움 (한국 KOSDAQ S/T tier · TPEx 등 illiquid
  로컬 라인 동일 적용)'. 시총 USD 추정치 명시. M/L tier 는 생략.
  (416180.KQ Shinsung ST, 131290.KQ TSE 같은 KOSDAQ S 티어를 거론
  하면서 유동성 경고 미명시 시 reader 가 동일 size weight 로 오해
  가능 — 2026-05-29 외부 리뷰 surfaced).

DEPTH REQUIREMENTS — Pro capacity 충분히 활용 의무 (2026-05-29 첫
런이 ₩39 / ~3분 = capacity 의 ~12% 만 사용. 시장이 깊이를 요구한다):

- **Master Table 행 수 하한 — 15행 이상**: 4-6 테마 × 3 티어 가 floor.
  niche 가 부족하다 싶으면 sub-layer 더 깊게 (예: '액체냉각' 을 'QD
  커넥터' + 'manifold' + 'TIM compound' 3개 sub-theme 으로 분해).
  output 단축 절대 금지.

- **각 행에 정량 수치 의무**: 추상 형용사 ('strong', 'leading') 만으로
  rationale 작성 금지. months / units / MW / kW / dollars / % 같은
  measurable 단위로 cite. 예시:
    ❌ 'leading market share' / 'strong demand growth'
    ✅ '시장 점유율 ~70% (FY24)' / 'backlog $2.4B (+45% QoQ)' /
       '리드타임 38-week → 52-week' / 'ASP +28% (Q1 24 → Q1 25)'

- **각 행에 sourced citation ≥2개**: Tier A·B·C 신호 중 최소 2개는
  publisher + date 명시 ('Bloomberg 2026-04-12', 'TSMC Q1 24 call,
  2024-04-18'). 모두 inferred 인 행은 conviction 낮음으로 강등 +
  하단 별도 'low confidence — sourced data 부재' 섹션으로 격리.

- **단락당 depth**: 'binding constraint' 단락 (Section 1) 최소 4문장 —
  현 binding 어디 / why / 다음 binding 후보 layer + timing / 시장이
  아직 못 잡은 신호. 'Top 3 conviction' 각 항목 2-3문장 (왜 지금 +
  티어 + 접근 경로). 'Bottom line' 단일 종목 핵심 한 단락 (3-4문장).

- **Date stamping rigor**: 모든 'as of' / 'FY' / 'Q' / 'H' 인용에 정확
  한 calendar date 명시. 'recent' / '최근' 같은 모호한 시제 금지 —
  반드시 specific date or quarter window.
- **Wildcard 금지** (2026-05-29 외부 리뷰 surfaced): 날짜 인용 시 정확한
  YYYY-MM-DD 형식 또는 quarter / half window (YYYY-Q1/Q2/Q3/Q4, YYYY-H1/H2)
  중 하나만 허용. underscore (`2026-01-_`), `?` (`2026-?-?`), `XX`
  (`2026-XX-15`) 같은 wildcard 절대 금지. 정확한 day/month 모르면 quarter
  단위로 반올림: '2026-01-_' → '2026-Q1', '2026-05-?' → '2026-Q2'.

TENSE DISCIPLINE (시제 규율 — 2026-05-29 surfaced):
**프롬프트 최상단의 '오늘 (today, KST)' 날짜를 anchor 로 모든 시제 판단.**
- 오늘 이전 (PAST) 사건은 반드시 과거형: '발생', '실적 공시됨',
  '실적 발표됨', '수주 확정', '실적 확인됨', '공시됨', '진행됨'.
- 오늘 이후 (FUTURE) 사건만 '전망' / '기대' / '예상' / 'expected' /
  'forecast' 허용.
- ❌ FORBIDDEN — 오늘 이전 사건에 '전망' / '기대' 사용:
  - 오늘이 2026-05-28 인데 '2025년 하반기부터 턴어라운드 전망' → 2025-H2
    는 이미 종료, '실적 공시됨' / '이미 진행' 으로 cite.
  - 오늘이 2026-05-28 인데 '2026 Q1 실적 기대' → Q1 (1-3월) 은 이미
    공시됨, web search 로 실 결과 verify + 'sourced (publisher 2026-MM-DD,
    매출 ±X%)' 형식.
  - 오늘이 2026-05-28 인데 '2025년 적자 → 2026년 흑자 전환 기대' →
    FY2025 결산 완료, FY2026 H1 진행 중. 'FY2025 적자 공시 (Q4 결산,
    2026-02-XX) + FY2026 Q1 흑자 전환 진행 (Q1 실적, 2026-04-XX)' 식
    각 분기 sourced cite.
- 학습 cutoff (2024-Q2) 기준 미래로 보이는 사건이라도 오늘 기준 과거이면
  반드시 web search 로 실 결과 verify 후 PAST tense + sourced 인용.
  '곧 발표될 예정' / '예상' 추측 금지.
- 'Catalyst+시기' 컬럼은 오로지 오늘 이후 future event 만 (예: '2026-H2',
  '2027-Q1'). 오늘 이전 event 를 catalyst 로 cite 금지.
- ❌ FORBIDDEN (강화 — 2026-05-29 pharma review): 외부 source 의 'YYYY
  완공 목표', 'YYYY 가동 개시', 'YYYY 출시 예정', 'YYYY 양산 시작' 등
  과거 target date 가 포함된 prose 를 그대로 cite 금지. 검증 의무:
  (a) target date > 오늘 → '예정' 유지 가능 (외부 source 의 'sourced
      YYYY-MM-DD' 와 별개로 target 자체가 미래 기준이므로 안전)
  (b) target date ≤ 오늘 → 실제 완공·가동·출시 여부 web search 재
      검증 의무. 검증 결과 (verify 성공/실패) 에 따라:
      - 성공 (가동 확인): PAST tense 로 '실제 YYYY-MM 가동 완료 확인'
      - 실패 (가동 미확정): 'YYYY 목표였으나 {today} 시점 가동 미확정'
        또는 OMIT
  - 위반 예시 (Pharma 2026-05-29): 300037.SZ Tinci '2025년 완공 목표로
    미국 및 모로코에 신규 생산기지 건설 중 (sourced 2026-05-29)' — 'sourced
    2026-05-29' 라 source 자체는 fresh 이지만 target date 2025 가 이미
    과거. 실제 완공/지연 여부 verify 없이 '건설 중' 그대로 출력 = 시제
    leak.

WEB SEARCH MANDATORY (2026-05-29 enabled — google_search tool wired):
- 현재 날짜 기준 (2026년) 최신 데이터를 web search 로 적극 fetch:
  hyperscaler 최근 capex 가이드 / 분기 실적 발표 (지난 30-90일) /
  sell-side notes 의 estimate revisions / IR · press release /
  industry reports (TrendForce / SEMI / DigiTimes / Nikkei Asia 등).
- 학습 cutoff (2024-Q2) 데이터는 stale — 동일 내용을 2026년 최신
  소스로 web search 해 confirm 또는 update 의무.
- 검색 결과는 publication date + URL 인용 — 'Bloomberg, 2026-04-12'
  형식. 2024-2025년 데이터를 2026년 인용처럼 위장 금지.
- 검색 결과가 학습 메모리와 충돌 시 web 결과 우선.

OUTPUT FORMATTING (2026-05-29 가독성 fix):
- Markdown 문법 (`**bold**`, `*italic*`, `_underline_`, `~strike~`,
  `## 헤더`, `# 헤더`, `### 헤더`) **절대 금지** — Telegram HTML 만
  (`<b>...</b>`, `<i>...</i>`, `<code>...</code>`). 강조 시 `<b>` 사용.
  섹션 헤더는 markdown `##` 대신 `<b>` + 줄바꿈 으로 (예: `<b>📊 Master
  Table</b>` + 빈 줄). 2026-05-29 surfaced — Pro 가 출력 상단에 `## AI
  데이터센터 구축 투자 전략` 같은 markdown 헤더 노출.
- 종목 행 인라인 `│` 가로 구분자 금지 — 모바일에서 줄바꿈 깨짐.
  각 신호 항목 (Tier A · B · C · 가격 반영도 · catalyst · kill
  trigger · 유동성 경고) 은 **반드시 별도 줄**로 분리. 형식 예시:
    <b>[고압 변압기]</b> · L · <code>ETN</code> · Eaton Corp
      • A (Catalyst): 데이터센터향 수주잔고 +50% YoY (Q1 2026 IR,
        2026-04-30, sourced via web)
      • B (실적): 변압기 리드타임 70-100주 심화 (TrendForce 2026-05-15)
      • C (시황): IRA 기반 전력망 투자 확대 직접 수혜 (sourced)
      • 가격 반영도: 반영 중 — PER 25x (5년 median 18x)
      • Catalyst+시기: FY26 Q2/Q3 실적 데이터센터 매출 가속 (2026-07/10)
      • Kill Trigger: 글로벌 침체로 산업 Capex 삭감
- '*' / '-' / '1.' markdown list 마커 금지. 줄 시작 inline bullet 은
  `•` 또는 `▪` 만 사용. 강조는 `<b>`.

TIER 분류 사용 규율 (2026-05-29 surfaced — NVTS $6.77B / Kinsus $11B
가 S-Tier 로 잘못 분류된 모순 + EV 도메인 review 에서 AMPX 가 시총 ~수억
달러인데 L-Tier 분류된 재발):
- 각 종목 context 상단에 'AUTHORITATIVE TIER:' 라벨 명시 — Python 백엔드
  가 yfinance 시가총액을 USD 환산해 분류 완료. S=<$300M / M=$300M-$3B /
  L=>$3B. Pro 임의 재분류 절대 금지.
- 'Master Table' 의 티어 컬럼 + Top-3 picks 의 'Tier:' 표기 + S-Tier
  유동성 경고 발화 조건 = 모두 AUTHORITATIVE TIER 그대로 사용. 매출 규모
  / 적자 여부 / 성장률 / 인지도 등 다른 휴리스틱 으로 tier 재추정 금지.
- 시총 표기는 'mcap' 필드 값 그대로 cite ('$6.77B USD' / '$420M USD' 등).
  Pro 가 자체 시총 추정·환산 금지.
- 위반 시 백엔드 post-process pass 가 자동 치환 + 로그 — 잘못된 tier 는
  사용자 출력 단에서 안 보이지만, 위반 횟수 누적은 모니터링 됨.

CORP ACTION 환각 차단 (058470.KS 리노공업 2026-05-29 surfaced):
- yfinance / 주입된 instrument context 의 EPS/PER 비정상값 (예: PER >
  500x · EPS 음수 + 시총 정상 · 현재가 vs 52주 최고가 30%+ 차이) 을
  발견해도 '기업 분할' / 'corp action' / '합병' / '액면분할' 같은 corp
  event 발생 추측 절대 금지.
- 추측 대신 둘 중 하나: (a) 'yfinance 데이터 stale — 거래소 공시 verify
  필요 (DART/EDINET/MOPS)' 명시 + 그 종목 PER/EPS 인용 자제 + PBR /
  PSR 같은 다른 valuation 지표 사용, (b) 그 종목 row 자체 OMIT.
- 'corp action 의심' / '데이터 transitional' 문구는 NOAH /ticker 가
  실시간 공시 fetch 결과로만 발화하는 가드 — screener 는 공시 fetch
  안 함 → 이 문구 그대로 인용 절대 금지.

VALUATION DISTORTION 가드 (cyclical bottom 인지 — 2026-05-29 외부
리뷰 ② 반영):
- 현재 PER > 100x 종목은 '단순 고평가' 결론 금지. 일시적 이익 훼손
  (cyclical bottom / 적자 전환 직후 / 분기 일회성 손실) 에 의한 PER
  왜곡 가능성 명시 의무.
- 대체 valuation 지표 cite 의무: PBR · Fwd EV/EBITDA · PSR · EV/Sales
  중 2개 이상. 예시: 'Techwing PER 417x — FY26 적자 직후 cyclical
  bottom 에 의한 왜곡, PBR 4.2x / Fwd EV/EBITDA 18x 가 더 적절한
  valuation 렌즈'.
- Forward PER 이 정상 범위 (10-40x) 면 cyclical recovery 기대 신호로
  해석. Forward PER 도 비정상 (>100x or 음수) 이면 'thesis 무효, 종목
  OMIT 검토' 명시.
- **PER N/M (적자 기업) 가이드** (2026-05-29 외부 리뷰 surfaced): PER 이
  'N/M' / 음수 / 'N/A' (적자) 인 종목은 한 문장 더 명시 의무:
  (1) 적자 배경 — 'FY[YYYY] 영업 적자 (cyclical bottom / 일회성 비용 /
      신규 사업 투자 단계 등)', web verify 시 sourced cite, 미확정 시
      'inferred' 명시.
  (2) 턴어라운드 stage — '[YYYY]-Q[N] 흑자 전환 [기대 / 진행 / 확인됨]
      (catalyst: HBM 수요 / 신규 capa / 가격 인상 등 구체적)', 또는
      catalyst 미확정 시 'web verify 권장'.
  (3) 대체 valuation 지표 — PBR + PSR + EV/Sales 중 2개 이상.
  예시: '유니테스트 (086390.KQ) PER N/M(적자) — FY2025 적자 (HBM 라인
  투자 단계, sourced via web), 2026-Q4 흑자 전환 기대 (SK하이닉스 HBM4
  핸들러 공급, inferred). PBR 3.46x, PSR 2.1x 기준 valuation 평가.'

DATA INTEGRITY (불일치 종목 OMIT):
- yfinance 가 반환한 company_name 이 Pro 가 식별한 회사와 다르면
  (예: 103660.KS yfinance=씨앗, 기대=일진전기 / 3161.T yfinance=
  AZEARTH Corporation Textile, 기대=JITEC) 그 종목 row 를 **전체
  OMIT**. 'Low Confidence — 데이터 불일치' 섹션에 넣지 말 것 —
  reader 혼란 가중. 같은 테마 다른 ticker 로 대체하거나 'no clean
  public name' 선언.
- Pro 가 cite 한 sell-side report / earnings call quote / industry
  forecast 가 학습 메모리 (cutoff 2024-Q2) 만으로 작성된 경우:
  - web search 로 2026년 최신 데이터 verify 시도
  - verify 성공 → 'sourced (Bloomberg 2026-04-12)' 정상 cite
  - verify 실패 / 2024년 이전 데이터만 있음 → 반드시 'inferred
    (training data, 2024-Q2 cutoff — verify 권장)' 명시. 'sourced'
    라벨 절대 사용 금지.

LANGUAGE — 출력 전체를 **한국어**로 작성. 산문·근거 설명·평가·결론·
narrative·disclaimer 모두 한국어. 영어 paragraph / 영어 intro 문구
('Alright', 'Let's cut the noise', 'My job is to ...' 등) 절대 금지.
아래만 영어 원어 유지 허용:
- Ticker symbol (ROG, 2330.TW, 005930.KS, MRN.PA, 0700.HK 등)
- 회사명 영문 (Technoprobe, Parker-Hannifin) — 필요시 한국어 약칭 병기
- 기술 약어 (HBM, CoWoS, ABF, TIM, QD, CPO, MLCC, EMS, GaN, SiC, RPO,
  ASP, IRA, BIS, FOMC, FDA, CBAM, BMS, EV, AI, ML)
- 인덱스명 (S&P 500, NASDAQ, KOSPI, KOSDAQ, Hang Seng, Nikkei 225, TAIEX)
- 통화 코드 (USD, KRW, JPY, TWD, HKD, EUR, CNY)
- 출처 publisher 명 (Bloomberg, Reuters, 鉅亨網, 第一财经 등)
- 표 라벨 (Tier A·B·C / Priced-in / Catalyst+시기 / Kill Trigger)
그 외 모든 분석 문장 (예: "현재 binding constraint 는 ...", "관전 포인트
는 ...", "본 종목의 강점은 ..." 등) 은 반드시 한국어 자연문으로 작성.
"""


# ── yfinance ticker validation ────────────────────────────────────────────

_TICKER_RE = re.compile(r"\b([A-Z0-9]{1,6}(?:[.\-][A-Z0-9]{1,4})?)\b")

# Common false-positive tokens that look like tickers but aren't.
# 2026-05-28 first /screener run reject noise added: NYSE, ST, H1, FX, MJC
# (exchange names / period designators / company abbreviations that aren't
# the actual listed ticker).
# 2026-05-29 EV 도메인 review: EBITDA/FEC/DLE/CATL/GAAP/ROIC 등 도메인
# 약어 + 화학식 + 상장명-아닌-기업명 약자 추가. CATL 은 텍스트 약자이고
# 실제 상장은 300750.SZ — 'CATL' 만 잡히면 yfinance reject 노이즈.
_TICKER_BLACKLIST = {
    "USD", "EUR", "JPY", "KRW", "CNY", "TWD", "HKD", "GBP", "CHF",
    "AI", "USA", "EU", "UK", "TSE", "KRX", "TPEx", "TWSE", "ADR",
    "GDR", "ETF", "ETN", "IPO", "M&A", "API", "B", "M", "T", "K",
    "B&P", "PER", "PBR", "PSR", "EV", "FY", "Q1", "Q2", "Q3", "Q4",
    "CEO", "CFO", "CTO", "GPU", "HBM", "MLCC", "ASP", "RPO", "TIM",
    "BIS", "IRA", "CBAM", "FOMC", "DRAM", "NAND", "SSD", "PCIE",
    "FAB", "OEM", "ODM", "EMS", "GAN", "SIC", "EUV", "DUV",
    # Exchange / market identifiers — frequently appear in screener prose
    "NYSE", "NASDAQ", "AMEX", "LSE", "TSX", "ASX", "JSE", "SEHK",
    # Period / time designators
    "H1", "H2", "YTD", "YOY", "QOQ", "FY24", "FY25", "FY26", "FY27",
    # Misc finance acronyms — Wave 1 review 추가
    "FX", "PT", "RM", "MOQ", "BOM", "BTO", "JV", "LBO", "SPAC",
    "AOP", "OPEX", "CAPEX", "SAR", "RSU", "ESG",
    "EBITDA", "GAAP", "ROIC", "ROE", "ROA", "FCF", "IRR", "NPV",
    "WACC", "DCF", "PEG", "EPS", "BPS", "DPS", "CAGR", "LBO", "MBO",
    # Domain acronyms — EV / pharma / defense / solar / AI 데이터센터
    # 모두 cover. 본문에서 약어로 자주 등장하지만 실제 상장명 아님.
    "CATL", "LFP", "NCM", "NCA", "LMFP", "BMS", "VC", "FEC", "DLE",
    "ESS", "GLP", "CRO", "CDMO", "ADC", "DCT", "HRT", "PCT", "EUA",
    "MOSFET", "IGBT", "OLED", "AMOLED", "QLED", "LCD", "TFT", "QD",
    "CPU", "DPU", "TPU", "FPGA", "ASIC", "SoC", "PCB", "TSV", "SIP",
    "AAV", "VIE", "SPV", "PLC", "LLC", "LLP", "GP", "LP", "PE", "VC",
    "DoD", "FAA", "NATO", "BOEM", "AESO", "ERCOT", "CAISO", "PJM",
    "CHIPS", "FDA", "EMA", "CHMP", "NICE", "OFAC", "SAMR", "CAC", "BIS",
    # 2026-05-29 defense screener review 추가 — 도메인-specific 무기/
    # 시스템 약어가 본문 binding layer 에 자연 등장 → ticker 오인 차단.
    "AESA", "AUKUS", "ICBM", "ATGM", "SLBM", "MRBM", "IRBM", "BMD",
    "K2", "K2A", "K9", "K10", "K11", "KSS", "KF21", "KF16", "FA50",
    "F35", "F22", "F18", "F16", "F15", "B21", "B2", "B1",
    "THAAD", "SRM", "HIMARS", "GMLRS", "JDAM", "JASSM", "LRASM",
    "NGAD", "LRHW", "HACM", "GCAP", "FCAS", "JADC2", "OPIR", "SDA",
    "HBTSS", "RAM", "ESSM", "VLS", "SAM", "ABM", "MAD", "ROE",
    "ISR", "C5ISR", "SIGINT", "ELINT", "EW", "CUAS", "UAS",
    # Pharma 추가 — FDA / EMA 프로세스 약어
    "PDUFA", "sBLA", "BLA", "NDA", "IND", "EUA", "HTA", "GxP",
    "DMD", "GBM", "TNBC", "HCC", "RCC", "CRC", "AML", "CLL", "MDS",
    # AI 데이터센터 추가 — HBM/패키징/인터커넥트 세대 명
    "CoWoS", "HBM3", "HBM3E", "HBM4", "CXL", "NVLink", "GDDR",
    "PCIe", "InfiniBand", "DDR", "GDDR6", "GDDR7", "LPDDR",
    # Solar / grid / energy 추가
    "BOS", "PCS", "GIS", "HVDC", "HVAC", "FACTS", "STATCOM", "FCEV",
    "BESS", "CCUS", "DAC", "PEM", "AEM", "SOFC", "PEMFC",
    # 일반 finance + 시장 약어 보강
    "NPV", "IRR", "WACC", "DCF", "PEG", "CAGR", "GAAP", "GAAP",
    "TAM", "SAM", "SOM", "CAC", "LTV", "MRR", "ARR", "GMV", "GTV",
    # Company-name abbreviations that appear NEXT to real tickers in
    # narrative ('2396.T MJC', 'Shinsung ST', etc.) — the real ticker
    # is the .T / .KQ neighbor, the abbrev itself is not listed.
    "MJC", "ST", "SKC",
}


def _extract_candidate_tickers(text: str) -> set[str]:
    """Pull every plausible ticker symbol from the LLM output.

    yfinance accepts patterns like 'AAPL', 'TSM', '005930.KS', '2330.TW',
    '8306.T', '0700.HK', 'BRK-B', 'ENR.DE'. Use a permissive regex then
    filter aggressively to avoid year / FX / random-number false positives
    (2026-05-28 first run flagged 35 reject incl. '12', '155', '1355',
    '2024', '2025', '2026', '2027' — all noise from FX/date stamps).

    Rules:
      - Must contain at least one ALPHA char OR have a market suffix
        (.KS / .KQ / .T / .TWO / .TW / .HK / .DE / .PA / .AS / .SW /
        .SS / .SZ / .BJ / .MI / .L / .ST / .OL / .HE / .CO).
      - Pure-digit tokens without a suffix are rejected (years, FX rates).
      - Base part (before . or -) must be ≥2 chars.
    """
    candidates: set[str] = set()
    for m in _TICKER_RE.finditer(text):
        sym = m.group(1)
        if sym in _TICKER_BLACKLIST:
            continue
        base = sym.split(".")[0].split("-")[0]
        if len(base) < 2:
            continue
        has_alpha = any(c.isalpha() for c in sym)
        has_market_suffix = "." in sym and not sym.split(".")[-1].isdigit()
        # Pure-digit token without a real market suffix = year / FX / count
        # (e.g. '2026', '1355', '155'). Reject before yfinance call to
        # avoid 35-row reject noise in the validation summary.
        if not has_alpha and not has_market_suffix:
            continue
        # 2026-05-29 Wave 1 review fix: digit-LED base without a market
        # suffix is a quantity / period designator, not a ticker. Catches
        # 150M ($150M 시총), 800V (high-voltage spec), 92M ($92M revenue),
        # 2026-H2 (period — base '2026' after split('-'), no '.' suffix),
        # 2027-Q4 등. Real CN/KR/JP/TW/HK digit tickers ALWAYS carry a
        # market suffix (`.SS`, `.SZ`, `.KS`, `.T`, `.TW`, `.HK`) so this
        # rule is safe to apply universally.
        if base and base[0].isdigit() and not has_market_suffix:
            continue
        candidates.add(sym)
    return candidates


def _classify_tier_by_mcap_usd(mcap_usd: float) -> str:
    """Hardcode tier from USD market cap.
    S = micro (< $300M, centroid ~$100M)
    M = mid   ($300M-$3B, centroid ~$1B)
    L = large (> $3B, centroid ~$10B)
    Reviewer 2026-05-29: NVTS ($6.77B) + Kinsus ($11B) 가 S-Tier 분류된
    수학적 모순 → Pro 자율 분류 신뢰 불가, Python 강제 분류 필요."""
    if mcap_usd is None or mcap_usd <= 0:
        return "?"
    if mcap_usd < 300e6:
        return "S"
    if mcap_usd < 3e9:
        return "M"
    return "L"


_FX_RATE_CACHE: dict[str, float] = {}


def _fx_to_usd(amount: float, currency: str) -> float | None:
    """Convert local-currency amount to USD via yfinance FX rate. Returns
    None on failure. Caches FX rates per-process to avoid duplicate calls."""
    if not amount or amount <= 0:
        return None
    cur = (currency or "").upper().strip()
    if cur in ("USD", "", "USDOLLAR"):
        return float(amount)
    fx_sym = f"{cur}=X"
    if fx_sym in _FX_RATE_CACHE:
        return float(amount) / _FX_RATE_CACHE[fx_sym]
    try:
        import yfinance as yf
        fi = yf.Ticker(fx_sym).fast_info
        rate = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
        if rate and rate > 0:
            _FX_RATE_CACHE[fx_sym] = float(rate)
            return float(amount) / float(rate)
    except Exception as exc:
        log.debug("screener: FX rate fetch failed for %s: %s", fx_sym, exc)
    return None


def _override_tiers_from_mcap(candidates: list[dict]) -> list[dict]:
    """For each candidate, fetch real-time market cap from yfinance,
    convert to USD, and OVERRIDE Pro's tier classification with the
    hardcoded Python rule. Mutates candidate dicts in place — adds
    `mcap_usd` and overrides `tier`. Logs disagreements for audit.
    Reviewer 2026-05-29 #1 fix."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("screener: yfinance unavailable — tier override skipped")
        return candidates
    for c in candidates:
        t = c.get("ticker", "")
        if not t:
            continue
        try:
            info = yf.Ticker(t).info or {}
            mcap_native = info.get("marketCap")
            currency = info.get("currency") or c.get("currency") or "USD"
            if not mcap_native or mcap_native <= 0:
                continue
            mcap_usd = _fx_to_usd(mcap_native, currency)
            if mcap_usd is None:
                continue
            new_tier = _classify_tier_by_mcap_usd(mcap_usd)
            old_tier = (c.get("tier") or "").upper()[:1]
            c["tier"] = new_tier
            c["mcap_usd"] = mcap_usd
            if old_tier and old_tier != new_tier:
                log.info(
                    "screener: tier override %s: Pro=%s → Python=%s "
                    "(mcap $%.2fB %s)",
                    t, old_tier, new_tier, mcap_usd / 1e9, currency,
                )
        except Exception as exc:
            log.debug("screener: tier override failed for %s: %s", t, exc)
    return candidates


def _validate_with_yfinance(tickers: set[str]) -> tuple[list[str], list[str]]:
    """Return (validated, rejected) split — rejection = no yfinance data."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("screener: yfinance unavailable, skipping validation")
        return list(tickers), []

    validated: list[str] = []
    rejected: list[str] = []
    for sym in sorted(tickers):
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info
            px = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
            if px and px > 0:
                validated.append(sym)
            else:
                rejected.append(sym)
        except Exception as exc:
            log.debug("screener: ticker validation failed for %s: %s", sym, exc)
            rejected.append(sym)
    return validated, rejected


# ── Cost logging (KST-based, mirrors SV pattern) ──────────────────────────

_NOAH_USAGE_LOG = os.path.expanduser("~/.tradingagents/usage.jsonl")
_SCREENER_ARCHIVE_DIR = os.path.expanduser("~/.tradingagents/screener_archive")
_SCREENER_MEMORY_LOG = os.path.expanduser(
    "~/.tradingagents/memory/screener_memory.md"
)


def _log_usage(prompt_tok: int, output_tok: int, cost_krw: float, domain: str) -> None:
    """Dual-log: screener_usage.jsonl (KST date-tagged, screener-specific)
    + ~/.tradingagents/usage.jsonl (NOAH llm_call format) so screener Pro
    calls flow into /usage aggregation alongside analyzer/researcher costs.
    """
    from datetime import datetime, timezone, timedelta
    import time

    # (a) Screener-specific log
    try:
        os.makedirs(os.path.dirname(_USAGE_LOG), exist_ok=True)
        now = datetime.now(timezone(timedelta(hours=9)))
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "month": now.date().isoformat()[:7],
            "domain": domain,
            "prompt_tok": prompt_tok,
            "output_tok": output_tok,
            "cost_krw": round(cost_krw, 4),
        }
        with open(_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("screener: screener_usage.jsonl write failed: %s", exc)

    # (b) NOAH usage.jsonl in llm_call format — picked up by /usage and
    # bot/dashboard cost card automatically.
    try:
        os.makedirs(os.path.dirname(_NOAH_USAGE_LOG), exist_ok=True)
        cost_usd = cost_krw / _USD_TO_KRW
        rec_noah = {
            "ts": time.time(),
            "type": "llm_call",
            "model": "gemini-2.5-pro",
            "prompt_tokens": prompt_tok,
            "completion_tokens": output_tok,
            "cost_usd": round(cost_usd, 6),
            "subsystem": "screener",
            "domain": domain,
        }
        with open(_NOAH_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec_noah, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("screener: NOAH usage.jsonl write failed: %s", exc)


# ── Top-3 extraction + archive + memory log ──────────────────────────────

_TOP3_TAG_RE = re.compile(
    r"<TOP_3_JSON>\s*(\[.*?\])\s*</TOP_3_JSON>", re.DOTALL
)
_TICKERS_USED_RE = re.compile(
    r"<TICKERS_USED_JSON>\s*(\[.*?\])\s*</TICKERS_USED_JSON>", re.DOTALL
)


def _extract_tickers_used_json(output: str) -> tuple[list[str], str]:
    """Pull the TICKERS_USED_JSON tail (Fix #5, 2026-05-29 defense review).
    Returns (ticker_list, output_cleaned). Empty list + original output
    when the tail is absent (caller falls back to regex extraction).

    Pro 의 self-declared ticker list 가 yfinance 검증의 primary source.
    regex 추출은 K9 / AESA / ICBM 같은 도메인 약어 false-positive 가
    구조적으로 발생 → blacklist 가 영원히 따라가야 했음. JSON tail 은
    Pro 가 실제 cite 한 ticker 만 선언하므로 noise 0 보장."""
    m = _TICKERS_USED_RE.search(output)
    if not m:
        return [], output
    try:
        raw = json.loads(m.group(1))
        if not isinstance(raw, list):
            return [], output
        tickers = []
        seen: set[str] = set()
        for t in raw:
            sym = str(t).strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                tickers.append(sym)
    except json.JSONDecodeError as exc:
        log.warning("screener: TICKERS_USED_JSON parse failed: %s", exc)
        return [], output
    cleaned = (output[: m.start()].rstrip()
               + "\n"
               + output[m.end():].lstrip()).strip()
    return tickers, cleaned


# Master Table row: '[테마] · L · TICKER · ...' — captures theme prefix,
# tier letter, ticker (USD-letter / digit-suffix forms both). Multiline
# DOTALL not needed; tier line is always single-line in current format.
_MT_TIER_ROW_RE = re.compile(
    r"(\[[^\]\n]+\]\s*·\s*)([LMS?])(\s*·\s*)([A-Z0-9][A-Z0-9.\-]{0,12})",
)
# Top-3 parenthetical: '(Tier: L, 접근 경로: ...)' — captures just the
# letter for in-place substitution.
_TOP3_TIER_PAREN_RE = re.compile(r"\(Tier:\s*([LMS?])(\s*[,\)])")


def _correct_tiers_post_pro(
    text: str, candidates: list[dict]
) -> tuple[str, int]:
    """Belt-and-suspenders enforcement of AUTHORITATIVE TIER values in the
    Pro Phase 4·5 output. Walks the rendered Master Table + Top-3 picks
    for `[theme] · X · TICKER` and `(Tier: X)` occurrences; when the X
    letter disagrees with the Python-computed tier for TICKER, substitute.

    Returns (corrected_text, n_fixes). Logs each correction so monitoring
    can spot when Pro discipline drifts at scale (EV 2026-05-29 review
    surfaced AMPX shown as L despite Python-assigned tier from mcap).

    Master Table rows are the primary surface — substitution is safe even
    when the row format varies slightly (tier letter is always the first
    `·`-separated field after the theme bracket). Top-3 picks pick up
    `(Tier: X` parenthetical via a separate line walk so the ticker
    context can be derived from the same line.
    """
    if not text or not candidates:
        return text, 0
    by_ticker: dict[str, str] = {}
    for c in candidates:
        tk = (c.get("ticker") or "").upper()
        tier = (c.get("tier") or "").upper()[:1]
        if tk and tier in ("L", "M", "S"):
            by_ticker[tk] = tier
    if not by_ticker:
        return text, 0
    n_fixes = 0

    # Pass 1 — Master Table rows.
    def _mt_sub(m: re.Match) -> str:
        nonlocal n_fixes
        prefix, cur_tier, sep, ticker = m.groups()
        tk = ticker.upper()
        true_tier = by_ticker.get(tk)
        if true_tier and cur_tier != true_tier:
            log.warning(
                "screener tier correction (MT row): %s %s → %s",
                tk, cur_tier, true_tier,
            )
            n_fixes += 1
            return f"{prefix}{true_tier}{sep}{ticker}"
        return m.group(0)
    text = _MT_TIER_ROW_RE.sub(_mt_sub, text)

    # Pass 2 — Top-3 picks. Match line-by-line because each pick is a
    # single line and we need ticker context (preceding `(TICKER)` form).
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = _TOP3_TIER_PAREN_RE.search(ln)
        if not m:
            continue
        cur_tier = m.group(1)
        # Use the last `(TICKER)` token before the (Tier: ...) marker.
        before = ln[: m.start()]
        tk_matches = list(re.finditer(r"\(([A-Z0-9][A-Z0-9.\-]{0,12})\)", before))
        if not tk_matches:
            continue
        tk = tk_matches[-1].group(1).upper()
        true_tier = by_ticker.get(tk)
        if true_tier and cur_tier != true_tier:
            lines[i] = (
                ln[: m.start(1)] + true_tier + ln[m.end(1):]
            )
            n_fixes += 1
            log.warning(
                "screener tier correction (Top-3): %s %s → %s",
                tk, cur_tier, true_tier,
            )
    text = "\n".join(lines)
    return text, n_fixes


def _correct_top3_json_tiers(
    top3: list[dict], candidates: list[dict]
) -> int:
    """Fix tier letters inside the TOP_3_JSON tail too. Same Python truth
    as _correct_tiers_post_pro but applied to the parsed JSON dict so the
    dashboard archive + telegram top-3 display surface stays in sync.
    Returns n_fixes."""
    if not top3 or not candidates:
        return 0
    by_ticker: dict[str, str] = {}
    for c in candidates:
        tk = (c.get("ticker") or "").upper()
        tier = (c.get("tier") or "").upper()[:1]
        if tk and tier in ("L", "M", "S"):
            by_ticker[tk] = tier
    n_fixes = 0
    for p in top3:
        if not isinstance(p, dict):
            continue
        tk = (p.get("ticker") or "").upper()
        cur = (p.get("tier") or "").upper()[:1]
        true_tier = by_ticker.get(tk)
        if true_tier and cur and cur != true_tier:
            log.warning(
                "screener tier correction (JSON tail): %s %s → %s",
                tk, cur, true_tier,
            )
            p["tier"] = true_tier
            n_fixes += 1
    return n_fixes


_MT_TIER_COUNT_RE = re.compile(r"\[[^\]\n]+\]\s*·\s*([LMS])\s*·")
# Theme-aware version — captures theme/binding-layer name + tier letter
# so per-theme distribution drift can be logged (Pharma 2026-05-29 review:
# 'CDMO 탈중국' theme had 2 L 0 M, 'ADC 플랫폼 기술' theme had only 1 L
# — domain-level total looked fine but per-theme S/M/L mandate violated).
_MT_TIER_THEME_RE = re.compile(r"\[([^\]\n]+?)\]\s*·\s*([LMS])\s*·")


def _log_tier_distribution(domain: str, text: str) -> None:
    """Count S/M/L tier letters in Master Table rows; log distribution.
    Block 은 안 함 — defense 같이 micro-cap 부재한 도메인은 L-skew 가
    구조적 합리. 시간 누적 추세 모니터링 용도 (Fix #6, 2026-05-29).

    2026-05-29 pharma review 추가: per-theme tier 분포도 logging — Pro
    가 도메인 전체 S/M/L 채워도 theme/binding-layer 단위로 보면 한 layer
    에 L 2 + M 0 (예: 'CDMO 탈중국' L=2/M=0/S=1) 처럼 'theme 별 S/M/L
    1행 의무' mandate violation 가능. WARNING 로깅 → 추세 모니터링.
    """
    from collections import defaultdict
    counts = {"L": 0, "M": 0, "S": 0}
    per_theme: dict[str, dict[str, int]] = defaultdict(
        lambda: {"L": 0, "M": 0, "S": 0}
    )
    for m in _MT_TIER_THEME_RE.finditer(text or ""):
        theme_name = m.group(1).strip()
        tier = m.group(2)
        counts[tier] += 1
        per_theme[theme_name][tier] += 1
    total = sum(counts.values())
    if total == 0:
        return
    missing = [k for k, v in counts.items() if v == 0]
    log.info(
        "screener tier distribution [%s]: L=%d M=%d S=%d (total=%d)%s",
        domain, counts["L"], counts["M"], counts["S"], total,
        f" — missing {','.join(missing)}" if missing else "",
    )
    # Per-theme — warn when a theme has 2+ rows but missing S or M.
    # Single-row themes can't violate distribution (Pro may have run out
    # of candidates for that niche layer). 2+ rows with missing S/M =
    # L-skew that prompt mandate explicitly forbids.
    for theme, tc in per_theme.items():
        theme_total = sum(tc.values())
        if theme_total < 2:
            continue
        theme_missing = [k for k, v in tc.items() if v == 0]
        if theme_missing:
            log.warning(
                "screener theme tier skew [%s / %s]: L=%d M=%d S=%d "
                "— missing %s (theme total %d)",
                domain, theme[:50],
                tc["L"], tc["M"], tc["S"],
                ",".join(theme_missing), theme_total,
            )


def _extract_top3_json(output: str) -> tuple[list[dict], str]:
    """Extract Top-3 JSON tail from Pro Phase 4·5 output. Returns
    (top3_list, output_cleaned) where the cleaned output has the tag
    stripped for user-visible display. Empty list + original output on
    parse failure (Pro may have skipped the tail)."""
    m = _TOP3_TAG_RE.search(output)
    if not m:
        return [], output
    try:
        top3 = json.loads(m.group(1))
        if not isinstance(top3, list):
            top3 = []
    except json.JSONDecodeError as exc:
        log.warning("screener: TOP_3_JSON parse failed: %s", exc)
        top3 = []
    cleaned = (output[:m.start()].rstrip()
               + "\n"
               + output[m.end():].lstrip()).strip()
    return top3, cleaned


def _parse_screener_sections(raw: str) -> dict:
    """Extract narrative sections from Pro Phase 4·5 output. Used by the
    archive writer + dashboard renderer so each run preserves the prose
    (binding constraint paragraph, Top-3 picks rationale, bottom line)
    alongside the structured mini-table. Empty strings when missing.

    Tolerates Pro's `<b>...</b>` HTML + markdown bold/headers around the
    section markers (2026-05-29 surfaced: 'Pro emits `<b>📍 현재
    binding constraint</b>` → plain regex anchor failed → sections came
    back empty). Strips formatting noise before regex."""
    out = {"binding_constraint": "", "master_table": "",
           "top3_section": "", "bottom_line": ""}
    if not raw:
        return out
    # Normalise away formatting noise that breaks emoji-marker anchoring
    clean = raw
    clean = re.sub(r"<\/?[a-zA-Z]+[^>]*>", "", clean)   # <b>, </b>, <i>, etc.
    clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean)    # **bold** → bold
    clean = re.sub(r"^#+\s*", "", clean, flags=re.MULTILINE)  # ## headers
    m = re.search(
        r"📍\s*현재\s*binding\s*constraint\s*\n+(.*?)(?=\n+(?:📊|🏆|💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m:
        out["binding_constraint"] = m.group(1).strip()
    m = re.search(
        r"📊\s*Master\s*Table\s*\n+(.*?)(?=\n+(?:🏆|💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m:
        out["master_table"] = m.group(1).strip()
    m = re.search(
        r"🏆\s*Top\s*3\s*conviction\s*picks\s*\n+(.*?)(?=\n+(?:💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m:
        out["top3_section"] = m.group(1).strip()
    m = re.search(
        r"💡\s*Bottom\s*line\s*\n+(.*?)(?=\n+(?:⚠️|🤖)|\Z)",
        clean, re.DOTALL,
    )
    if m:
        out["bottom_line"] = m.group(1).strip()
    return out


def _save_screener_archive(
    result: "ScreenerResult", top3: list[dict],
) -> Optional[str]:
    """Save the screener run to ~/.tradingagents/screener_archive/
    YYYY-MM-DD/HHMMSS_{domain_slug}.json. Returns the saved path or None
    on failure. Mirrors NOAH bot/archive.py pattern but date-grouped
    instead of ticker-grouped."""
    from datetime import datetime, timezone, timedelta
    try:
        now = datetime.now(timezone(timedelta(hours=9)))
        date_dir = os.path.join(_SCREENER_ARCHIVE_DIR, now.date().isoformat())
        os.makedirs(date_dir, exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", result.domain).strip("_").lower()[:40]
        fname = f"{now.strftime('%H%M%S')}_{slug}.json"
        path = os.path.join(date_dir, fname)
        sections = _parse_screener_sections(result.raw_output)
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "domain": result.domain,
            "raw_output": result.raw_output,
            "binding_constraint": sections["binding_constraint"],
            "master_table": sections["master_table"],
            "top3_section": sections["top3_section"],
            "bottom_line": sections["bottom_line"],
            "validated_tickers": result.validated_tickers,
            "rejected_tickers": result.rejected_tickers,
            "elapsed_sec": round(result.elapsed_sec, 2),
            "cost_krw": round(result.cost_krw, 4),
            "top_3_picks": top3,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        log.info("screener: archived to %s", path)
        return path
    except Exception as exc:
        log.warning("screener: archive save failed: %s", exc)
        return None


def _log_screener_memory(domain: str, top3: list[dict]) -> None:
    """Append Top-3 picks to screener_memory.md in NOAH-compatible TAG_RE
    format so the outcome resolver (auto_resolve.py) can pick them up at
    5/15/30 trading-day windows. Each line:
        [YYYY-MM-DD | TICKER | screener·{domain_slug}·{tier}·rank{N} | pending]
    """
    if not top3:
        return
    from datetime import datetime, timezone, timedelta
    try:
        today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", domain).strip("_").lower()[:20]
        os.makedirs(os.path.dirname(_SCREENER_MEMORY_LOG), exist_ok=True)
        written = 0
        with open(_SCREENER_MEMORY_LOG, "a", encoding="utf-8") as f:
            for p in top3:
                if not isinstance(p, dict):
                    continue
                ticker = (p.get("ticker") or "").strip().upper()
                tier = (p.get("tier") or "?").strip()[:1].upper()
                rank = p.get("rank")
                company = (p.get("company") or "").strip()[:60]
                thesis = (p.get("thesis_line") or "").strip()[:140]
                if not ticker or rank not in (1, 2, 3):
                    continue
                rating = f"screener·{slug}·{tier}·rank{rank}"
                f.write(f"[{today} | {ticker} | {rating} | pending]\n")
                if company or thesis:
                    line = company + (" — " + thesis if thesis else "")
                    f.write(f"{line}\n")
                f.write("<!-- ENTRY_END -->\n\n")
                written += 1
        log.info("screener: memory log appended (%d picks)", written)
    except Exception as exc:
        log.warning("screener: memory log append failed: %s", exc)


# ── Phase β: 2-pass orchestration (Pro 1·2 + 병렬 context fetch + Pro 4·5) ─

def _call_pro(api_key: str, prompt: str, model: str = "gemini-2.5-pro",
              enable_grounding: bool = True) -> tuple[str, int, int]:
    """Helper: call Gemini Pro, return (text, prompt_tokens, output_tokens).

    WEB GROUNDING (2026-05-29 enabled): When `enable_grounding` is True
    (default), the Pro call wires google_search tool so it can fetch
    real-time data from the web. Critical for screener freshness — Pro's
    training cutoff (2024-Q2) makes sell-side reports / earnings call
    commentary / industry forecasts stale by 2 years. Grounding turns
    those into current 2026 data with cited URLs.

    Cost: +~$0.035 per grounded request (Gemini pricing). Falls back to
    non-grounded call if the SDK version doesn't support the grounding API
    (try/except on types import).
    """
    from google import genai
    client = genai.Client(api_key=api_key)

    grounding_config = None
    if enable_grounding:
        try:
            from google.genai import types as _genai_types
            grounding_config = _genai_types.GenerateContentConfig(
                tools=[_genai_types.Tool(
                    google_search=_genai_types.GoogleSearch()
                )]
            )
        except (ImportError, AttributeError) as exc:
            log.warning("screener: google_search grounding unavailable: %s — "
                        "falling back to non-grounded Pro call (stale data risk)", exc)
            grounding_config = None

    try:
        if grounding_config is not None:
            resp = client.models.generate_content(
                model=model, contents=prompt, config=grounding_config,
            )
        else:
            resp = client.models.generate_content(model=model, contents=prompt)
    except Exception as exc:
        # If grounded call fails (e.g., quota / tool config rejected),
        # retry once without grounding to preserve user-visible output.
        if grounding_config is not None:
            log.warning("screener: grounded Pro call failed (%s) — retry without grounding", exc)
            resp = client.models.generate_content(model=model, contents=prompt)
        else:
            raise

    text = (resp.text or "").strip()
    pt = ot = 0
    try:
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            pt = int(getattr(um, "prompt_token_count", 0) or 0)
            ot = int(getattr(um, "candidates_token_count", 0) or 0)
    except Exception:
        pass
    return text, pt, ot


def _build_phase_12_prompt(theme: dict) -> str:
    """Phase 1·2 Pro prompt: identify binding + candidate tickers as JSON.
    Trimmed prompt — no full master table yet; that's Phase 4·5's job after
    we inject real per-ticker data."""
    layers = "\n".join(f"  - {l}" for l in theme["binding_layer_taxonomy"])
    regions = "\n".join(
        f"  - {layer}: {region}"
        for layer, region in theme["regional_concentration"].items()
    )
    today = _today_kst_iso()
    return f"""오늘 (today, KST): {today}

너는 냉혹한 buy-side 애널리스트. Theory of Constraints anchor.
충성심·내러티브 없음. 오직 수익. 도메인 "{theme['domain']}" ({theme['horizon']})
의 binding constraint 와 choke point owner 식별.

이 도메인의 binding layer (참고):
{layers}

지리적 집중 (참고):
{regions}

GLOBAL MANDATE — US/KR/JP/TW/EU/CN A·H 모두 포함. niche 2-3 layers down.
SIZE TIERS — ~$100M micro / ~$1B mid / ~$10B large (USD 환산 후 분류).
가짜 ticker 절대 금지 (사후 yfinance 검증). clean public name 부재 시
'NO_PUBLIC' 명시.

출력은 **JSON ONLY** (코드블록 ```json 안에 포함). 다른 prose 금지.
다음 schema 정확히 준수:

```json
{{
  "binding_constraint": "현재 binding layer + 다음 binding 후보 layer 한국어
                         3-4 문장 (timing + 시장이 미반영한 신호 포함)",
  "candidates": [
    {{
      "theme": "binding layer 이름 (한국어, 예: 'HBM 프로브 카드')",
      "tier": "L|M|S",
      "ticker": "ROG | 2330.TW | 005930.KS | MRN.PA | 0700.HK | NO_PUBLIC",
      "company_name": "Rogers Corp / Technoprobe SpA / SK하이닉스 등",
      "listing": "NYSE | TWSE | KRX | Borsa Italiana | KRX-KOSDAQ 등",
      "currency": "USD | TWD | KRW | EUR | HKD 등",
      "exposure_pct_estimate": 40,
      "reasoning_seed": "왜 이 종목이 이 layer 의 choke point owner 인가 (한국어 1문장)"
    }}
  ]
}}
```

목표: 4-6 niche 테마 × 3 티어 = **12-18 candidates**. 각 테마의 S 티어는
KOSDAQ / TPEx / Mothers / ASX small-cap / NEO 같은 small-cap exchange
적극 활용 — 글로벌 web search 로 후보 확보 의무.

TIER 분포 강제 (2026-05-29 defense review 누락 surfaced):
- 각 binding layer 마다 mcap 분포 의식적으로 S/M/L 모두 채우려 시도.
  '방산 = 대부분 대형' 같은 사전 학습 편향 으로 L 만 채우는 패턴 금지.
- 구조적으로 micro-cap 부재한 layer (예: defense 잠수함 원자로 = BWXT/
  HII 외 micro-cap 없음, 우라늄 enrichment = Cameco / Urenco 외 부재)
  는 candidates 항목에 'tier_unavailable_reason' 필드 추가해 명시:
  "S-Tier unavailable — industry-wide micro-cap 부재 (subscale operator
   부재 + 정부 인증 진입장벽)". 임의로 L 만 채우지 말 것.
- 그 외 layer 는 S 후보 적극 탐색 — KOSDAQ Mothers / TPEx · ASX small-
  cap · NEO Exchange · 인도 SME · 영국 AIM · 독일 Scale 등 모든 글로벌
  small-cap 거래소 web search 동원.
"""


def _parse_phase_12_response(text: str) -> Optional[tuple[str, list[dict]]]:
    """Extract JSON {binding_constraint, candidates} from Pro Phase 1·2.
    Returns (binding_summary, candidates_list) or None on parse failure."""
    # Extract first ```json``` block, then fall back to first {...} block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"(\{[\s\S]*\})", text)
    if not m:
        log.warning("screener phase 1·2: no JSON block found in Pro response")
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        log.warning("screener phase 1·2: JSON parse failed: %s", exc)
        return None
    binding = (data.get("binding_constraint") or "").strip()
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        log.warning("screener phase 1·2: no candidates in JSON")
        return None
    # Clean each candidate: ensure required keys, drop NO_PUBLIC entries
    cleaned: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        ticker = (c.get("ticker") or "").strip().upper()
        if not ticker or ticker == "NO_PUBLIC":
            continue
        cleaned.append({
            "theme": str(c.get("theme") or "").strip(),
            "tier": str(c.get("tier") or "").strip().upper()[:1] or "M",
            "ticker": ticker,
            "company_name": str(c.get("company_name") or "").strip(),
            "listing": str(c.get("listing") or "").strip(),
            "currency": str(c.get("currency") or "").strip(),
            "exposure_pct": c.get("exposure_pct_estimate") or c.get("exposure_pct"),
            "reasoning_seed": str(c.get("reasoning_seed") or "").strip(),
        })
    if not cleaned:
        return None
    return binding, cleaned


def _fetch_contexts_parallel(tickers: list[str], max_workers: int = 8,
                              hard_timeout_sec: int = 120) -> dict[str, str]:
    """Phase 3: parallel build_instrument_context per ticker. Returns
    {ticker: context_text} dict; tickers that timeout / fail come back ''.
    build_instrument_context is the SAME function NOAH /ticker analysts use,
    so the screener sees identical real-time forward-signal data.

    HUNG-THREAD PROTECTION (2026-05-29 surfaced): Python ThreadPoolExecutor
    cannot kill running threads, and `with ThreadPoolExecutor(...)` calls
    shutdown(wait=True) on exit — so a hung HTTP call inside
    build_instrument_context (yfinance/DART/EDINET without internal timeout)
    blocked the whole orchestrator indefinitely (user's /screener stuck
    >15 min). Fix: hard `hard_timeout_sec` cap via as_completed timeout +
    explicit shutdown(wait=False, cancel_futures=True) so hung threads
    finish in background while we return with partial data.
    """
    try:
        from tradingagents.agents.utils.agent_utils import build_instrument_context
    except ImportError:
        log.warning("screener phase 3: build_instrument_context unavailable")
        return {}

    from concurrent.futures import (
        ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout,
    )

    results: dict[str, str] = {}
    ex = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="screener")
    try:
        futures = {
            ex.submit(build_instrument_context, t, "news"): t
            for t in tickers
        }
        try:
            for fut in as_completed(futures, timeout=hard_timeout_sec):
                t = futures[fut]
                try:
                    ctx = fut.result(timeout=30)
                    results[t] = ctx or ""
                except Exception as exc:
                    log.warning("screener phase 3: %s fetch failed: %s", t, exc)
                    results[t] = ""
        except FuturesTimeout:
            done = len(results)
            log.warning(
                "screener phase 3: hard timeout %ds — proceeding with %d/%d "
                "tickers (hung fetches finish in background)",
                hard_timeout_sec, done, len(tickers),
            )
    finally:
        # Don't wait for hung threads — let them finish naturally in
        # background. cancel_futures=True cancels pending (not running) ones.
        ex.shutdown(wait=False, cancel_futures=True)
    return results


def _build_phase_45_prompt(
    theme: dict,
    binding_summary: str,
    candidates: list[dict],
    contexts: dict[str, str],
) -> str:
    """Phase 4·5 Pro prompt: full master table + top 3 + bottom line with
    real per-ticker data injected. Reuses every directive from the Phase α
    _build_prompt — LANGUAGE / DEPTH / liquidity warning / format rules."""

    # Inject real-data context blocks (trim each to keep input bound)
    ctx_block = ""
    for c in candidates:
        t = c["ticker"]
        ctx = contexts.get(t, "")
        mcap = c.get("mcap_usd")
        mcap_str = (
            f"${mcap/1e9:.2f}B USD" if mcap is not None and mcap >= 1e9
            else f"${mcap/1e6:.0f}M USD" if mcap is not None and mcap > 0
            else "N/A (시총 fetch 실패)"
        )
        if not ctx:
            ctx_block += (
                f"\n\n=== {t} ({c.get('company_name', '')}) — 실시간 데이터 부재"
                f" (fetch 실패 또는 yfinance 미커버) ===\n"
                f"테마: {c.get('theme')} · 시장: {c.get('listing')}\n"
                f"AUTHORITATIVE TIER (Python 하드코드, mcap 기반):"
                f" {c.get('tier', '?')} · 시총 {mcap_str}\n"
                f"reasoning_seed: {c.get('reasoning_seed')}\n"
            )
            continue
        # Trim verbose context to ~2500 chars per ticker (token budget control)
        trimmed = ctx if len(ctx) <= 2500 else ctx[:2500] + "\n... (trimmed)"
        ctx_block += (
            f"\n\n=== {t} ({c.get('company_name', '')}) — 실시간 forward-signal 데이터 ===\n"
            f"테마: {c.get('theme')} · 시장: {c.get('listing')}\n"
            f"AUTHORITATIVE TIER (Python 하드코드, mcap 기반):"
            f" {c.get('tier', '?')} · 시총 {mcap_str}\n"
            f"{trimmed}\n"
        )

    # Reuse the full base prompt directives by extracting from _build_prompt
    base = _build_prompt(theme)
    # Replace the front "OBJECTIVE" + theme intro with Phase β framing.
    # Keep all RULES / DEPTH REQUIREMENTS / LANGUAGE / output format intact.
    today = _today_kst_iso()
    phase_b_intro = f"""오늘 (today, KST): {today}

너는 냉혹한 buy-side 애널리스트. Theory of Constraints anchor.
도메인: "{theme['domain']}" ({theme['horizon']}).

Phase 1·2 에서 식별된 binding constraint:
{binding_summary}

아래는 Phase 1·2 후보 종목 {len(candidates)}개의 **실시간 fetch 데이터**
(yfinance + 공시 + 뉴스 + 매크로 + 기술 지표 — NOAH /ticker 와 동일
파이프라인). 각 종목의 현재가·시총·peer·최근 catalyst 가 이미 포함되어
있다. **반드시 이 실시간 수치를 verbatim cite 하라** — LLM 메모리/추정치
사용 금지. 데이터 부재 종목은 'inferred' 명시 + low confidence 격리.

REAL-TIME CONTEXTS:
{ctx_block}

이제 위 데이터를 기반으로 다음을 생성:"""

    # Find where the OBJECTIVE block ends and append from "OUTPUT FORMAT" onward
    output_idx = base.find("OUTPUT FORMAT")
    if output_idx < 0:
        log.warning("screener: OUTPUT FORMAT marker missing — falling back to whole base")
        return phase_b_intro + "\n\n" + base
    tail = base[output_idx:]
    return phase_b_intro + "\n\n" + tail


# ── Orchestrator ─────────────────────────────────────────────────────────

def _run_phase_alpha(api_key: str, theme: dict, started: float) -> Optional[ScreenerResult]:
    """Legacy single-Pro-call path. Kept as fallback when Phase β fails
    JSON parse or build_instrument_context is unavailable."""
    prompt = _build_prompt(theme)
    try:
        text, pt, ot = _call_pro(api_key, prompt)
    except Exception as exc:
        log.exception("screener phase α: Pro call failed: %s", exc)
        return None
    if not text:
        log.error("screener phase α: empty Pro response")
        return None
    cost_usd = (pt * _PRO_INPUT_USD_PER_M + ot * _PRO_OUTPUT_USD_PER_M) / 1e6
    cost_krw = cost_usd * _USD_TO_KRW
    _log_usage(pt, ot, cost_krw, theme["domain"])
    cands = _extract_candidate_tickers(text)
    validated, rejected = _validate_with_yfinance(cands)
    log.info("screener phase α: %d candidates → %d validated / %d rejected",
             len(cands), len(validated), len(rejected))
    return ScreenerResult(
        domain=theme["domain"],
        raw_output=text,
        validated_tickers=validated,
        rejected_tickers=rejected,
        elapsed_sec=time.time() - started,
        cost_krw=cost_krw,
    )


def _run_phase_beta(api_key: str, theme: dict, started: float) -> Optional[ScreenerResult]:
    """Phase β: 2-pass orchestration with real-time forward-signal fetch.
       Phase 1·2 (Pro JSON) → Phase 3 (parallel build_instrument_context)
       → Phase 4·5 (Pro synthesis with real data).
    Returns None to signal caller to fall back to Phase α."""
    # Phase 1·2: discover binding + candidates
    p12_prompt = _build_phase_12_prompt(theme)
    try:
        p12_text, p12_pt, p12_ot = _call_pro(api_key, p12_prompt)
    except Exception as exc:
        log.warning("screener phase β/1·2: Pro call failed: %s", exc)
        return None
    parsed = _parse_phase_12_response(p12_text)
    if not parsed:
        return None
    binding, candidates = parsed
    log.info("screener phase β/1·2: %d candidates discovered", len(candidates))

    # Validate every ticker
    raw_tickers = {c["ticker"] for c in candidates}
    validated, rejected = _validate_with_yfinance(raw_tickers)
    log.info("screener phase β/yf-validate: %d/%d candidates passed",
             len(validated), len(raw_tickers))
    if not validated:
        log.warning("screener phase β: 0 validated tickers — falling back")
        return None
    candidates = [c for c in candidates if c["ticker"] in set(validated)]

    # Override Pro's tier classification with Python-computed tier based on
    # actual yfinance market cap (USD-converted). Reviewer 2026-05-29 fix #1.
    candidates = _override_tiers_from_mcap(candidates)

    # Phase 3: parallel context fetch (NOAH-equivalent forward-signal data)
    phase3_start = time.time()
    contexts = _fetch_contexts_parallel(validated)
    log.info("screener phase β/3: %d/%d contexts fetched in %.1fs",
             sum(1 for v in contexts.values() if v), len(validated),
             time.time() - phase3_start)

    # Phase 4·5: synthesize with real-data context injection
    p45_prompt = _build_phase_45_prompt(theme, binding, candidates, contexts)
    try:
        p45_text, p45_pt, p45_ot = _call_pro(api_key, p45_prompt)
    except Exception as exc:
        log.warning("screener phase β/4·5: Pro call failed: %s", exc)
        return None
    if not p45_text:
        log.warning("screener phase β/4·5: empty Pro response")
        return None

    total_pt = p12_pt + p45_pt
    total_ot = p12_ot + p45_ot
    cost_usd = (total_pt * _PRO_INPUT_USD_PER_M + total_ot * _PRO_OUTPUT_USD_PER_M) / 1e6
    cost_krw = cost_usd * _USD_TO_KRW
    _log_usage(total_pt, total_ot, cost_krw, theme["domain"])

    # Tier 보정 (2026-05-29 EV review fix #3): Pro 가 AUTHORITATIVE TIER
    # 디렉티브를 무시하고 자체 분류 (예: AMPX L 로 출력하지만 mcap 기반
    # Python 분류 결과는 M 또는 S) 한 경우, 본문 + JSON tail 모두 Python
    # 진실로 자동 치환. Master Table 행 + Top-3 picks 의 (Tier: X) +
    # TOP_3_JSON 의 "tier" 필드 모두 동기화.
    p45_text, n_body_fixes = _correct_tiers_post_pro(p45_text, candidates)
    if n_body_fixes:
        log.info("screener: %d tier corrections applied to Pro body", n_body_fixes)

    # Extract machine-parseable JSON tails (TICKERS_USED first so the
    # subsequent body parsing doesn't include it). Order: TICKERS_USED →
    # TOP_3 → body. Both tails are stripped from user-visible output.
    used_tickers, p45_text = _extract_tickers_used_json(p45_text)
    top3, p45_cleaned = _extract_top3_json(p45_text)
    n_json_fixes = _correct_top3_json_tiers(top3, candidates)
    if n_json_fixes:
        log.info("screener: %d tier corrections applied to Top-3 JSON", n_json_fixes)
    log.info("screener phase β/Top-3: %d picks extracted", len(top3))

    # Ticker scope 격리 (2026-05-29 외부 리뷰 surfaced): Phase 1·2 candidate
    # 검증 결과 (validated / rejected) 를 사용자에게 그대로 노출하면, Pro
    # 가 본문(Phase 4·5)에서 제외한 후보까지 'reject 카운트' 로 보여 reader
    # 혼란. 037530.KQ / 452420.KQ / HDELY 가 본문에 없는데 reject 표시된 케
    # 이스. Fix: 최종 본문 (p45_cleaned) 에서 ticker 재추출 → 그 결과만
    # 사용자에게 표시. Phase 1·2 검증은 internal scope.
    #
    # Fix #5 (2026-05-29 defense review): TICKERS_USED_JSON tail 이 있으면
    # Pro 의 self-declared ticker list 를 primary 로 사용 — regex 추출이
    # K9/AESA/ICBM/AUKUS 같은 도메인 약어 false-positive 를 만들었던 패턴
    # 영구 차단. Tail 누락 시 regex 폴백.
    if used_tickers:
        body_tickers = set(used_tickers)
        # Cross-check: regex 가 추가로 잡은 ticker (Pro 가 tail 에 누락한
        # 케이스) 가 있는지 보고 — 보강 가능. Pro 가 reasonable 한 경우
        # used_tickers 가 정확 — 보강 비활성. mismatch 만 INFO 로깅.
        regex_set = _extract_candidate_tickers(p45_cleaned)
        only_in_regex = regex_set - body_tickers
        only_in_json = body_tickers - regex_set
        if only_in_regex:
            log.info(
                "screener: regex found %d tickers not in TICKERS_USED tail: %s "
                "(noise filtered out)",
                len(only_in_regex), ", ".join(sorted(only_in_regex))[:200],
            )
        if only_in_json:
            log.info(
                "screener: TICKERS_USED has %d tickers not in body regex: %s "
                "(body may have new mentions)",
                len(only_in_json), ", ".join(sorted(only_in_json))[:200],
            )
    else:
        log.warning(
            "screener: TICKERS_USED_JSON tail missing — falling back to regex"
            " extraction (may include domain-acronym noise)"
        )
        body_tickers = _extract_candidate_tickers(p45_cleaned)
    body_validated, body_rejected = _validate_with_yfinance(body_tickers)

    # Fix #6 (2026-05-29 defense review): tier 분포 모니터링 — block 안 함.
    # defense 같이 구조적으로 micro-cap 부재한 도메인은 L-skew 가 합리적.
    # 시간 누적 추세로 distribution drift 추적용 INFO 로깅.
    _log_tier_distribution(theme["domain"], p45_cleaned)

    result = ScreenerResult(
        domain=theme["domain"],
        raw_output=p45_cleaned,
        validated_tickers=body_validated,
        rejected_tickers=body_rejected,
        elapsed_sec=time.time() - started,
        cost_krw=cost_krw,
    )

    # Archive + memory log (after successful run only). Both are best-effort
    # — failure here doesn't block the user-visible result.
    try:
        _save_screener_archive(result, top3)
    except Exception as exc:
        log.warning("screener: archive write failed: %s", exc)
    try:
        _log_screener_memory(theme["domain"], top3)
    except Exception as exc:
        log.warning("screener: memory log failed: %s", exc)
    # Refresh the static screener.html so the dashboard reflects this run
    # immediately (5/15/30d columns stay '⏳' until auto_resolve fills them).
    try:
        from bot.dashboard import regenerate_screener_index
        regenerate_screener_index()
    except Exception as exc:
        log.warning("screener: dashboard regen failed: %s", exc)

    return result


def run_screener(domain: str = "bottleneck") -> Optional[ScreenerResult]:
    """Run the screener for the given domain. Phase β (real-time data) is
    the default; SCREENER_PHASE=alpha env var forces the legacy single-Pro
    path. On Phase β failure (JSON parse / 0 validated tickers / Pro crash)
    the orchestrator falls back to Phase α automatically.

    Wave 1 (2026-05-29): theme registry under bot/screener_themes/ —
    bottleneck (AI Data Center) / ev / defense / pharma / solar. Unknown
    domain → None with available domains logged for the telegram caller
    to surface in the error message.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("screener: GOOGLE_API_KEY missing")
        return None

    theme = _resolve_theme(domain)
    if theme is None:
        log.warning(
            "screener: domain '%s' unknown — available: %s",
            domain, available_summary(),
        )
        return None
    started = time.time()

    forced_alpha = os.environ.get("SCREENER_PHASE", "beta").strip().lower() == "alpha"
    if not forced_alpha:
        try:
            result = _run_phase_beta(api_key, theme, started)
            if result is not None:
                return result
            log.warning("screener: Phase β returned None — falling back to Phase α")
        except Exception as exc:
            log.exception("screener: Phase β crashed (%s) — falling back to Phase α", exc)
    return _run_phase_alpha(api_key, theme, started)


# ── Output formatting for Telegram ────────────────────────────────────────

_TELEGRAM_LIMIT = 4096
_CHUNK_TARGET = 3800


def format_for_telegram(result: ScreenerResult) -> list[str]:
    """Split the Pro output into Telegram-sized chunks with a header card."""
    header = (
        f"📊 <b>Bottleneck Screener — {result.domain}</b>\n"
        f"⏱ {result.elapsed_sec:.0f}초 · 💰 ₩{result.cost_krw:.1f}"
        f" · ✅ ticker 검증: {len(result.validated_tickers)}개 통과 /"
        f" {len(result.rejected_tickers)}개 reject\n\n"
    )

    if result.rejected_tickers:
        rejected_str = ", ".join(f"<code>{t}</code>" for t in result.rejected_tickers[:10])
        if len(result.rejected_tickers) > 10:
            rejected_str += f" ... ({len(result.rejected_tickers)-10}개 더)"
        header += (
            f"⚠️ <b>yfinance 검증 실패</b> (가짜 ticker 가능, 본문에서 확인 권장): {rejected_str}\n\n"
        )

    full = header + result.raw_output

    # Telegram message chunking
    if len(full) <= _CHUNK_TARGET:
        return [full]

    chunks: list[str] = []
    current = ""
    for line in full.splitlines(keepends=True):
        if len(current) + len(line) > _CHUNK_TARGET and current:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks or [full[:_CHUNK_TARGET]]
