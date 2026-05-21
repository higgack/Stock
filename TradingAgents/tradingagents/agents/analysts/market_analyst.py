from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    finalize_analyst_result,
    get_analyst_directive,
    get_indicators,
    get_language_instruction,
    get_macro_for,
    get_risk_metrics_for,
    get_sector_strength_for,
    get_stock_data,
)
from tradingagents.dataflows.config import get_config


def create_market_analyst(llm):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]
        instrument_context = build_instrument_context(symbol, analyst_id="market")

        # Pre-fetch ALL three deterministic snapshots (macro, risk metrics,
        # sector strength) in Python and inject them as already-fetched
        # facts. journalctl confirmed the analyst LLM was skipping the
        # MANDATORY tool calls for all three across the 2026-05 batch
        # despite the prompt marking them MANDATORY. Doing the fetch
        # outside the LLM and removing the tools from the tool list
        # guarantees the data ends up in the report.
        macro_snapshot = get_macro_for(symbol, current_date)
        if macro_snapshot:
            instrument_context += (
                "\n\n=== Pre-fetched macro snapshot (use VERBATIM in the"
                " '거시 배경' subsection — do NOT claim the data is"
                " unavailable, do NOT call any macro tool, this IS the"
                f" macro data for {current_date}) ===\n{macro_snapshot}"
            )
        else:
            instrument_context += (
                "\n\n=== Macro snapshot was attempted at node entry but"
                " yfinance returned no usable series. Write the '거시"
                " 배경' subsection as a single sentence: '거시 배경은 본"
                " 분석에서 미수집되었습니다.' Do NOT apologise; do NOT"
                " attempt a tool call. ==="
            )

        risk_snapshot = get_risk_metrics_for(symbol, current_date)
        if risk_snapshot:
            instrument_context += (
                "\n\n=== Pre-fetched risk metrics snapshot (use VERBATIM"
                " in the '리스크 지표' subsection — annual vol, Sharpe,"
                " VaR/CVaR, max drawdown, beta come from this snapshot."
                " Do NOT claim the data is unavailable, do NOT call any"
                f" risk tool) ===\n{risk_snapshot}"
            )
        else:
            instrument_context += (
                "\n\n=== Risk metrics snapshot was attempted at node entry"
                " but the deterministic computation failed. Write the"
                " '리스크 지표' subsection as a single sentence: '리스크"
                " 지표는 본 분석에서 미수집되었습니다.' Do NOT apologise;"
                " do NOT attempt a tool call. ==="
            )

        sector_snapshot = get_sector_strength_for(symbol, current_date)
        if sector_snapshot:
            instrument_context += (
                "\n\n=== Pre-fetched sector relative-strength snapshot"
                " (use VERBATIM to ground the SECTOR PRIMER's 'leader vs"
                " laggard' claim — replace any vague '섹터 내 강세' with"
                " the actual percent-point differential from this table."
                " Do NOT call any sector tool) ===\n{sector_snapshot}"
            ).replace("{sector_snapshot}", sector_snapshot)
        else:
            instrument_context += (
                "\n\n=== Sector relative-strength snapshot was attempted"
                " at node entry but yfinance returned no usable benchmark"
                " data. Note '섹터 상대 강도는 본 분석에서 미수집' in the"
                " sector primer; do NOT apologise; do NOT attempt a tool"
                " call. ==="
            )

        tools = [
            get_stock_data,
            get_indicators,
            # get_risk_metrics, get_sector_relative_strength,
            # get_macro_context — all three pre-fetched above.
        ]

        system_message = (
            """You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

- Select indicators that provide diverse and complementary information. Avoid redundancy (e.g., do not select both rsi and stochrsi). Also briefly explain why they are suitable for the given market context. When you tool call, please use the exact name of the indicators provided above as they are defined parameters, otherwise your call will fail. Please make sure to call get_stock_data first to retrieve the CSV that is needed to generate indicators. Then use get_indicators with the specific indicator names. Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."""
            + " STRUCTURE: Output the Markdown summary table FIRST (right after a 1-2 line"
            " opening), THEN the detailed body analysis. This protects the most useful"
            " reference content from being cut if the response hits the output budget."
            + " SECTOR PRIMER: Before diving into the company-specific technicals,"
            " write a short paragraph (3-5 sentences) of sector context — what segment"
            " of the market the ticker belongs to (e.g. 'AI 인프라 반도체', '클라우드"
            " 소프트웨어', '대체에너지 유틸리티'), what the dominant theme of that"
            " segment has been over the last 4 weeks (rotation in/out, sector indices"
            " up/down, peer drivers), and where this specific name sits inside the"
            " segment (leader, laggard, niche, recent breakout). Use this as the"
            " opening paragraph of the body so a reader can place the chart pattern in"
            " context before reading indicator-by-indicator commentary."
            + " THREE PRE-FETCHED SUBSECTIONS — all three data payloads are"
            " ALREADY in your instrument context above. Do NOT attempt to"
            " call any macro / risk / sector tool — those tools are no"
            " longer in your tool list. Build these subsections from the"
            " embedded snapshots:"
            "\n  (a) '리스크 지표' subsection — use the pre-fetched risk"
            " metrics snapshot (annual vol, Sharpe, VaR/CVaR, max drawdown,"
            " beta). Quote the numbers verbatim and add one sentence on"
            " whether the figures are typical/elevated for this sector"
            " (e.g. high-growth tech routinely runs σ ≥ 40%, an S&P"
            " tracker is closer to 15-18%).\n"
            "      BETA LABEL (MANDATORY): the beta reported in this"
            " snapshot is the 90거래일 회귀계수 vs the market's broad"
            " benchmark (SPY for US, KODEX 200 for KR, TOPIX 1306 for JP)."
            " ALWAYS render it as '베타 (90일, vs {benchmark_name}): X.XX'"
            " — e.g. '베타 (90일, vs TOPIX 1306): 0.02'. Without the"
            " window + benchmark label, readers see two different beta"
            " values in one report (this 90-day one and the 5-year"
            " monthly beta from yfinance .info in the fundamentals"
            " section) and assume one is wrong. Toyota 7203.T 2026-05-18"
            " surfaced this: 시장 0.02 vs 펀더멘털 0.327 — same stock,"
            " different windows + benchmarks, no label = reader confusion."
            "\n  (b) '거시 배경' subsection — use the pre-fetched macro"
            " snapshot. Connect the actual numbers (10Y yield, VIX, DXY,"
            " oil) to the chart — e.g. rising 10Y yields hurt high-multiple"
            " growth, oil spike helps energy names, VIX expansion"
            " compresses risk appetite."
            "\n  (c) SECTOR PRIMER — use the pre-fetched sector"
            " relative-strength snapshot to ground the 'leader vs laggard'"
            " claim. Replace any vague '섹터 내 강세' wording with the"
            " actual percent-point differential from the snapshot table"
            " (e.g. '90D +12.4%p vs SOXX → 명확한 섹터 리더'). Include"
            " the table inline in the sector primer paragraph or"
            " immediately after."
            + " NO TOOL APOLOGIES: The remaining tools (get_stock_data,"
            " get_indicators) are always callable — call them and use"
            " whatever they return. The risk / macro / sector data is"
            " ALREADY in your instrument context (pre-fetched in Python),"
            " so you don't need to call any tool for those subsections."
            " NEVER write phrases like 'tools are not available', 'tool"
            " is not available', 'I cannot provide', 'Therefore I cannot',"
            " '도구를 사용할 수"
            " 없습니다', '죄송합니다'. If a particular subsection's data"
            " genuinely came back empty, mention it in one short sentence"
            " (e.g. '리스크 지표는 본 분석에서 미수집') and move on with"
            " the rest of the report."
            " F1 v3 (Hon Hai 2317.TW 2026-05-20 재발): 시장 분석가의"
            " 기술 분석 본문에서 '종가 X.X / 최근 종가 Y.Y / 현재가"
            " Z.Z' 같이 SAME REPORT 안에서 다른 가격 두 개 인용 금지."
            " yfinance .history Close (전일 종가) 와 .info currentPrice"
            " (intraday/latest) 가 미세 다른 케이스에도 본 보고서의"
            " 시작부터 끝까지 build_instrument_context 가 inject 한"
            " single 'canonical current price' 만 사용. 'Close Price'"
            " / '종가' / '현재가' / '주가' 모두 같은 값. 시점 다른"
            " 가격 (5일 전, 4주 전) 인용 시 시점 라벨 동반 의무."
            + get_analyst_directive()
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # F1-MVP Gemini context caching propagation (B1-bn, 2026-05-21)
        cache_name = state.get("gemini_cache_name", "")
        active_llm = llm
        if cache_name:
            try:
                active_llm = llm.bind(cached_content=cache_name)
            except Exception:
                active_llm = llm
        chain = prompt | active_llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        result, report = finalize_analyst_result(
            prompt, llm, state["messages"], result, "market"
        )

        return {
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node
