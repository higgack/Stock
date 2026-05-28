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


# ── Orchestrator ─────────────────────────────────────────────────────────

def run_screener(domain: str = "bottleneck") -> Optional[ScreenerResult]:
    """Run the screener for the given domain. Phase α supports 'bottleneck'
    only — Wave 1 will extend to ev / solar / pharma / defense etc.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("screener: GOOGLE_API_KEY missing")
        return None

    # Phase α: only the AI Data Center domain is wired.
    if domain not in ("bottleneck", "ai", "ai_datacenter", ""):
        log.warning("screener: domain '%s' not in Phase α — falling back to bottleneck", domain)
    theme = _AI_DATACENTER_THEME

    started = time.time()
    prompt = _build_prompt(theme)

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
        )
        raw = (resp.text or "").strip()
        if not raw:
            log.error("screener: empty Pro response")
            return None

        # Cost accounting
        pt = ot = 0
        try:
            um = getattr(resp, "usage_metadata", None)
            if um is not None:
                pt = int(getattr(um, "prompt_token_count", 0) or 0)
                ot = int(getattr(um, "candidates_token_count", 0) or 0)
        except Exception:
            pass
        cost_usd = (pt * _PRO_INPUT_USD_PER_M + ot * _PRO_OUTPUT_USD_PER_M) / 1e6
        cost_krw = cost_usd * _USD_TO_KRW
        _log_usage(pt, ot, cost_krw, theme["domain"])

        # yfinance ticker validation
        candidates = _extract_candidate_tickers(raw)
        validated, rejected = _validate_with_yfinance(candidates)
        log.info(
            "screener: %d candidate tickers → %d validated / %d rejected",
            len(candidates), len(validated), len(rejected),
        )

        return ScreenerResult(
            domain=theme["domain"],
            raw_output=raw,
            validated_tickers=validated,
            rejected_tickers=rejected,
            elapsed_sec=time.time() - started,
            cost_krw=cost_krw,
        )
    except Exception as exc:
        log.exception("screener: orchestration failed: %s", exc)
        return None


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
