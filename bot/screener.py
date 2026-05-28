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

# ── Theme registry — Phase α inlines AI Data Center only. ────────────────
# Phase β / Wave 1 will extract to bot/screener_themes/*.yaml. Keeping
# inline now so the orchestrator + telegram wiring are validated first.

_AI_DATACENTER_THEME = {
    "domain": "AI Data Center Buildout",
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "HBM / 첨단 패키징 (CoWoS / ABF substrate)",
        "액체냉각 (Quick disconnect / TIM / Vapor chamber)",
        "전력 (Transformer / Busbar / GaN / SiC)",
        "광통신 (CPO / Co-packaged optics / Fiber)",
        "특수가스 / Wet chemistry",
        "Test / Burn-in / Probe card",
        "수동소자 (MLCC / 저항 / 인덕터)",
        "EMS / AI server 조립",
    ],
    "catalyst_types": [
        "하이퍼스케일러 capex 가이드 (Microsoft / Meta / Google / Amazon)",
        "TSMC / SK Hynix / Samsung HBM·CoWoS 캐파 expansion 발표",
        "美 BIS 對中 수출규제 / entity list 변경",
        "NVIDIA Blackwell / Rubin 채택률 데이터",
        "전력 인프라 grid 병목 + 데이터센터 부지 승인",
    ],
    "regional_concentration": {
        "HBM": "KR (Samsung 005930.KS / SK Hynix 000660.KS), US (Micron MU)",
        "ABF substrate": "JP (Ibiden 4062.T / Shinko 5703.T)",
        "Cooling": "TW (Auras 3324.TW / AVC 3017.TW), JP (Sunon 2421.TW)",
        "CoWoS": "TW (TSMC 2330.TW)",
        "Power": "EU (Siemens Energy ENR.DE / Schneider SU.PA), US (Eaton ETN / Vertiv VRT)",
        "Optical": "TW (Hon Hai 2317.TW), US (Coherent COHR / Lumentum LITE)",
        "Specialty gas": "JP (Air Water 4088.T), KR (SK Materials)",
    },
}

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

    return f"""너는 냉혹한 buy-side 애널리스트다. 어떤 종목·섹터·서사·과거
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
    # Misc finance acronyms
    "FX", "PT", "RM", "MOQ", "BOM", "BTO", "JV", "LBO", "SPAC",
    "AOP", "OPEX", "CAPEX", "SAR", "RSU", "ESG",
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
        candidates.add(sym)
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

def _log_usage(prompt_tok: int, output_tok: int, cost_krw: float, domain: str) -> None:
    from datetime import datetime, timezone, timedelta
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
        log.warning("screener: usage log failed: %s", exc)


# ── Phase β: 2-pass orchestration (Pro 1·2 + 병렬 context fetch + Pro 4·5) ─

def _call_pro(api_key: str, prompt: str, model: str = "gemini-2.5-pro") -> tuple[str, int, int]:
    """Helper: call Gemini Pro, return (text, prompt_tokens, output_tokens)."""
    from google import genai
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=model, contents=prompt)
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
    return f"""너는 냉혹한 buy-side 애널리스트. Theory of Constraints anchor.
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
KOSDAQ / TPEx / Mothers 같은 small-cap exchange 적극 활용.
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


def _fetch_contexts_parallel(tickers: list[str], max_workers: int = 8) -> dict[str, str]:
    """Phase 3: parallel build_instrument_context per ticker. Returns
    {ticker: context_text} dict; tickers that timeout / fail come back ''.
    build_instrument_context is the SAME function NOAH /ticker analysts use,
    so the screener sees identical real-time forward-signal data."""
    try:
        from tradingagents.agents.utils.agent_utils import build_instrument_context
    except ImportError:
        log.warning("screener phase 3: build_instrument_context unavailable")
        return {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, str] = {}
    # analyst_id='news' yields the broadest mix (macro+news+technical+공시)
    # — best fit for forward-signal screening.
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="screener") as ex:
        futures = {
            ex.submit(build_instrument_context, t, "news"): t
            for t in tickers
        }
        for fut in as_completed(futures, timeout=180):
            t = futures[fut]
            try:
                ctx = fut.result(timeout=60)
                results[t] = ctx or ""
            except Exception as exc:
                log.warning("screener phase 3: %s fetch failed: %s", t, exc)
                results[t] = ""
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
        if not ctx:
            ctx_block += (
                f"\n\n=== {t} ({c.get('company_name', '')}) — 실시간 데이터 부재"
                f" (fetch 실패 또는 yfinance 미커버) ===\n"
                f"테마: {c.get('theme')} · 티어: {c.get('tier')} · 시장: {c.get('listing')}\n"
                f"reasoning_seed: {c.get('reasoning_seed')}\n"
            )
            continue
        # Trim verbose context to ~2500 chars per ticker (token budget control)
        trimmed = ctx if len(ctx) <= 2500 else ctx[:2500] + "\n... (trimmed)"
        ctx_block += (
            f"\n\n=== {t} ({c.get('company_name', '')}) — 실시간 forward-signal 데이터 ===\n"
            f"테마: {c.get('theme')} · 티어: {c.get('tier')} · 시장: {c.get('listing')}\n"
            f"{trimmed}\n"
        )

    # Reuse the full base prompt directives by extracting from _build_prompt
    base = _build_prompt(theme)
    # Replace the front "OBJECTIVE" + theme intro with Phase β framing.
    # Keep all RULES / DEPTH REQUIREMENTS / LANGUAGE / output format intact.
    phase_b_intro = f"""너는 냉혹한 buy-side 애널리스트. Theory of Constraints anchor.
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
    _log_usage(total_pt, total_ot, cost_krw, theme["domain"] + " (Phase β)")

    return ScreenerResult(
        domain=theme["domain"] + " (Phase β · 실시간 데이터)",
        raw_output=p45_text,
        validated_tickers=validated,
        rejected_tickers=rejected,
        elapsed_sec=time.time() - started,
        cost_krw=cost_krw,
    )


def run_screener(domain: str = "bottleneck") -> Optional[ScreenerResult]:
    """Run the screener for the given domain. Phase β (real-time data) is
    the default; SCREENER_PHASE=alpha env var forces the legacy single-Pro
    path. On Phase β failure (JSON parse / 0 validated tickers / Pro crash)
    the orchestrator falls back to Phase α automatically.

    Phase α: only AI Data Center inline theme. Wave 1 will extend domains
    via bot/screener_themes/*.yaml registry.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("screener: GOOGLE_API_KEY missing")
        return None

    if domain not in ("bottleneck", "ai", "ai_datacenter", ""):
        log.warning("screener: domain '%s' not in Phase α — falling back to bottleneck", domain)
    theme = _AI_DATACENTER_THEME
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
