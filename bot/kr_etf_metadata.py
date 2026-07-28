"""KR ETF metadata table (KODEX / TIGER / KBSTAR / ARIRANG / HANARO).

B2 (Step 2A item ⑦, 2026-05-19): KR ETF 종목을 generic fund 모드가
아닌 KR-ETF specific 모드로 처리. KODEX/TIGER 등 ETF 종목 분석 시
회사 펀더멘털 대신:
 • 기초자산 / 추적 index (KOSPI 200, KOSDAQ 150, 반도체, 2차전지 등)
 • 운용사 (삼성자산운용 / 미래에셋자산운용 / KB자산운용 등)
 • 환헤지 여부 (환헤지 H / 비헤지 — 글로벌 ETF 핵심 변수)
 • Leveraged / Inverse (KODEX 200선물인버스2X 등)
 • 분배금 schedule (대부분 분기 / 일부 월배당)

자료 출처: 운용사 공식 product 페이지 + KRX 정보데이터시스템. 일부
주요 ETF (top ~50) hardcoded — yfinance .info 가 KR ETF metadata
부재하기 때문 (운용사 / index / hedge 모두 미확보).

KR ETF 종목 탐지: ticker prefix 또는 yfinance shortName / longName
패턴.
"""

from __future__ import annotations

from typing import Optional


# KR ETF metadata table. ticker → metadata dict.
# Fields:
#  • name_ko: 한국어 정식 명칭
#  • tracking: 기초 index / 자산 (예: 'KOSPI 200', '반도체 (KRX 300 반도체)')
#  • manager: 운용사 (예: '삼성자산운용')
#  • hedge: 환헤지 여부 ('비헤지' / '환헤지' / 'N/A' 국내 ETF)
#  • leverage: leverage factor ('1x' / '2x' / '-1x' / '-2x')
#  • theme: 주요 sector / theme (분석가 directive 용)
#  • distribution: 분배금 schedule (월간 / 분기 / 연간 / 없음)
#
# 상위 50개 거래량 ETF 만 cover — 미커버 ETF 는 generic fund mode 로
# fallback.

_KR_ETF_METADATA: dict[str, dict] = {
    # ─── 대표 broad-market ETFs (KOSPI / KOSDAQ)
    "069500.KS": {
        "name_ko": "KODEX 200",
        "tracking": "KOSPI 200 지수",
        "manager": "삼성자산운용",
        "hedge": "N/A (국내)",
        "leverage": "1x",
        "theme": "broad KOSPI 대형주 추종",
        "distribution": "분기",
    },
    "102110.KS": {
        "name_ko": "TIGER 200",
        "tracking": "KOSPI 200 지수",
        "manager": "미래에셋자산운용",
        "hedge": "N/A (국내)",
        "leverage": "1x",
        "theme": "broad KOSPI 대형주 추종",
        "distribution": "분기",
    },
    "229200.KS": {
        "name_ko": "KODEX 코스닥150",
        "tracking": "KOSDAQ 150 지수",
        "manager": "삼성자산운용",
        "hedge": "N/A (국내)",
        "leverage": "1x",
        "theme": "KOSDAQ 우량주 (반도체·바이오·게임 위주)",
        "distribution": "분기",
    },
    "232080.KS": {
        "name_ko": "TIGER 코스닥150",
        "tracking": "KOSDAQ 150 지수",
        "manager": "미래에셋자산운용",
        "hedge": "N/A (국내)",
        "leverage": "1x",
        "theme": "KOSDAQ 우량주",
        "distribution": "분기",
    },
    # ─── Leveraged / Inverse
    "122630.KS": {
        "name_ko": "KODEX 레버리지",
        "tracking": "KOSPI 200 (일별 2배)",
        "manager": "삼성자산운용",
        "hedge": "N/A",
        "leverage": "2x",
        "theme": "KOSPI 200 일별 2배 — 추적오차 + 변동성 decay 위험",
        "distribution": "없음 (대부분 재투자)",
    },
    "252670.KS": {
        "name_ko": "KODEX 200선물인버스2X",
        "tracking": "KOSPI 200 선물 일별 -2배",
        "manager": "삼성자산운용",
        "hedge": "N/A",
        "leverage": "-2x",
        "theme": "KOSPI 200 일별 -2배 (하락 베팅) — leveraged inverse"
                 " 특유의 variance drag 매우 큼",
        "distribution": "없음",
    },
    "114800.KS": {
        "name_ko": "KODEX 인버스",
        "tracking": "KOSPI 200 일별 -1배",
        "manager": "삼성자산운용",
        "hedge": "N/A",
        "leverage": "-1x",
        "theme": "KOSPI 200 일별 -1배 (하락 베팅)",
        "distribution": "없음",
    },
    # ─── Sector ETFs
    "091160.KS": {
        "name_ko": "KODEX 반도체",
        "tracking": "KRX 반도체 지수 (삼성전자 / SK하이닉스 등)",
        "manager": "삼성자산운용",
        "hedge": "N/A",
        "leverage": "1x",
        "theme": "반도체 sector — 삼성전자 + SK하이닉스 비중 70%+,"
                 " 글로벌 메모리 사이클 + AI 수요 dominant 변수",
        "distribution": "분기",
    },
    "305720.KS": {
        "name_ko": "KODEX 2차전지산업",
        "tracking": "FnGuide 2차전지 산업 지수",
        "manager": "삼성자산운용",
        "hedge": "N/A",
        "leverage": "1x",
        "theme": "2차전지 sector — LG에너지솔루션 / 삼성SDI / 포스코"
                 " 퓨처엠 / 에코프로비엠 비중 큼. 美 IRA + 锂 가격 +"
                 " EV demand 가 dominant.",
        "distribution": "분기",
    },
    "139660.KS": {
        "name_ko": "KODEX 200IT TR",
        "tracking": "KOSPI 200 IT 섹터",
        "manager": "삼성자산운용",
        "hedge": "N/A",
        "leverage": "1x",
        "theme": "KOSPI 200 IT 종목 (삼성전자 가중 매우 큼)",
        "distribution": "분기 (TR 은 재투자)",
    },
    "364980.KS": {
        "name_ko": "TIGER KRX BBIG K-뉴딜",
        "tracking": "K-뉴딜 (배터리 / 바이오 / 인터넷 / 게임)",
        "manager": "미래에셋자산운용",
        "hedge": "N/A",
        "leverage": "1x",
        "theme": "K-뉴딜 4개 sector 균등 — 정부 산업 정책 의존도 높음",
        "distribution": "분기",
    },
    # ─── 해외 broad-market ETFs (KR-listed but US/global asset)
    "381170.KS": {
        "name_ko": "TIGER 미국S&P500",
        "tracking": "S&P 500 지수 (USD)",
        "manager": "미래에셋자산운용",
        "hedge": "비헤지",
        "leverage": "1x",
        "theme": "美 S&P 500 비헤지 — USD/KRW 환율이 2차 dominant"
                 " 변수 (수익 = S&P 변동 + USD/KRW 변동)",
        "distribution": "분기",
    },
    "133690.KS": {
        "name_ko": "TIGER 미국나스닥100",
        "tracking": "NASDAQ 100 지수 (USD)",
        "manager": "미래에셋자산운용",
        "hedge": "비헤지",
        "leverage": "1x",
        "theme": "美 NASDAQ 100 비헤지 — 美 빅테크 비중 + USD/KRW",
        "distribution": "분기",
    },
    "143850.KS": {
        "name_ko": "TIGER 미국S&P500선물(H)",
        "tracking": "S&P 500 선물 (KRW 환헤지)",
        "manager": "미래에셋자산운용",
        "hedge": "환헤지 (H)",
        "leverage": "1x",
        "theme": "美 S&P 500 환헤지 — USD/KRW 변동 차단,"
                 " 純 美 시장 노출",
        "distribution": "분기",
    },
    "360750.KS": {
        "name_ko": "TIGER 미국나스닥100선물(H)",
        "tracking": "NASDAQ 100 선물 (KRW 환헤지)",
        "manager": "미래에셋자산운용",
        "hedge": "환헤지 (H)",
        "leverage": "1x",
        "theme": "美 NASDAQ 100 환헤지",
        "distribution": "분기",
    },
    # ─── 채권 / 금리 / 통화
    "302190.KS": {
        "name_ko": "TIGER 미국채10년선물",
        "tracking": "美 10Y 국채 선물",
        "manager": "미래에셋자산운용",
        "hedge": "비헤지",
        "leverage": "1x",
        "theme": "美 10Y 금리 inverse 노출 (금리 하락 시 가격 상승)",
        "distribution": "분기",
    },
    "069660.KS": {
        "name_ko": "KOSEF 200",
        "tracking": "KOSPI 200",
        "manager": "키움자산운용",
        "hedge": "N/A",
        "leverage": "1x",
        "theme": "KOSPI 200 추종 (KOSEF 시리즈)",
        "distribution": "분기",
    },
}


def get_kr_etf_metadata(ticker: str) -> Optional[dict]:
    """Return KR ETF metadata dict or None when ticker is not in the
    curated table. Caller falls back to generic fund_product mode."""
    if not ticker:
        return None
    return _KR_ETF_METADATA.get(ticker.upper())


def format_kr_etf_block(metadata: dict, ticker: str) -> str:
    """Render KR ETF metadata as analyst directive — replaces generic
    fund_product narrative for tickers in the curated table."""
    if not metadata:
        return ""
    return (
        f"\n\nIMPORTANT: `{ticker}` is a KR-listed ETF — generic"
        f" 'company news / executive / earnings' 분석 path 적용 안 됨."
        f" 다음 ETF-specific 변수로 분석 진행:\n"
        f"  • 정식 명칭: {metadata['name_ko']}\n"
        f"  • 기초 / 추적: {metadata['tracking']}\n"
        f"  • 운용사: {metadata['manager']}\n"
        f"  • 환헤지: {metadata['hedge']} —"
        f" {'(환헤지면 USD/KRW 변동 차단, 純 기초자산 노출)' if metadata['hedge'].startswith('환헤지') else '(비헤지면 USD/KRW 변동이 수익에 직접 추가)' if metadata['hedge'] == '비헤지' else '(국내 자산이라 환율 영향 없음)'}\n"
        f"  • Leverage: {metadata['leverage']}"
        f" {'— leveraged 또는 inverse ETF 는 일별 리밸런싱 + 변동성 decay 위험 큼. Long-term hold 비추, 단기 trading 도구로 적합' if metadata['leverage'] in ('2x', '-1x', '-2x') else ''}\n"
        f"  • Theme: {metadata['theme']}\n"
        f"  • 분배금: {metadata['distribution']}\n\n"
        f"분석 방향 (ETF-specific 5거래일 horizon):\n"
        f"  (1) 기초자산 ({metadata['tracking']}) 의 5거래일 가격 동인"
        f" 분석 — 단일 종목 펀더멘털 분석 X\n"
        f"  (2) 환율 노출이 있는 경우 (환헤지/비헤지 명시) USD/KRW"
        f" 또는 USD/EUR 변동 영향 명시\n"
        f"  (3) leveraged / inverse ETF 면 일별 추적오차 + variance"
        f" drag 위험 + 5거래일 horizon 의 적합성 명시\n"
        f"  (4) 운용 보수 (TER) / 추적오차 (Tracking Error) 는"
        f" yfinance 가 안 주지만 ETF specific 평가 변수 — 가능하면"
        f" 본인 지식으로 한 줄 언급\n"
        f"  (5) AUM 추이 / 거래량 — 유동성 신호 (낮은 AUM ETF 는"
        f" 매도 시 spread 큼)\n"
        f"  (6) 분배금 schedule 직전이면 ex-dividend 가격 효과 인지"
    )
