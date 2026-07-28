"""Financials — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (6):
 1. Banks (Diversified + Regional)
 2. Capital Markets & Investment (IB/Brokerage · Asset Mgmt · Exchanges)
 3. Consumer Finance
 4. Insurance (P&C + Life/Health + Brokers/Reinsurance)
 5. Business Development Companies (BDCs)
 6. Digital Assets & Cryptocurrency

다국적 종목 분포 (US/KR/JP/EU/CN/HK) 명시. Pro 가 web search 로 KR
금융지주 / JP 메가뱅크 / EU GSIB / CN 国有 은행 추가 발굴.
"""

from __future__ import annotations

THEME = {
    "domain": "Financials (금융)",
    "layer": "L2_SECTOR",
    "aliases": [
        "financial",
        "financials",
        "finance",
        "금융",
        "은행",
        "bank",
        "banks",
        "insurance",
        "보험",
        "asset_management",
        "자산운용",
        "fintech",
        "핀테크",
        "payments",
        "결제",
        "crypto",
        "가상화폐",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Banks (은행 — Diversified + Regional)",
        "Capital Markets & Investment (자본 시장 및 투자 — IB · Asset Mgmt · Exchanges)",
        "Consumer Finance (소비자 금융)",
        "Insurance (보험 — P&C + Life/Health + Brokers/Reinsurance)",
        "Business Development Companies (BDCs)",
        "Digital Assets & Cryptocurrency (디지털 자산 및 가상화폐)",
    ],
    "catalyst_types": [
        "Fed funds rate path + Powell 의장 임기 만료 후임자 인선",
        "Basel III endgame 최종 룰 + CCAR stress test → 배당/자사주 ceiling",
        "美 CRE 만기 wall (2024-2026 ~$2T) + office vacancy + delinquency",
        "Insurance variable annuity hedge book + 美 10Y 급변 시 자본여력 swing",
        "Asset Mgmt 분기 net flow (BLK ETF + KKR/APO 인수 deal)",
        "GENIUS Act + CLARITY Act (stablecoin 입법) 표결 일정",
        "BTC ETF inflow + 美 SEC crypto enforcement + Coinbase 분기 거래량",
        "中国 LPR + 부동산 신용 압박 + 港股통 southbound 보험 매수 변동",
    ],
    "regional_concentration": {
        "Banks (Diversified + Regional)": "US Big 4 (JPMorgan JPM · Bank of America BAC · Wells Fargo WFC · Citigroup C), Regional (US Bancorp USB · PNC PNC · Truist TFC · KeyCorp KEY · M&T MTB · Fifth Third FITB · Citizens CFG · Huntington HBAN), Global GSIB (HSBC 0005.HK · Deutsche Bank DBK.DE · BNP Paribas BNP.PA · Barclays BARC.L · Santander SAN.MC · UBS UBSG.SW · MUFG 8306.T · SMFG 8316.T · Mizuho 8411.T), KR (KB 105560.KS · 신한 055550.KS · 하나 086790.KS · 우리 316140.KS · IBK 024110.KS), CN (ICBC 1398.HK · CCB 0939.HK · BoC 3988.HK · ABC 1288.HK · 招商 3968.HK)",
        "Capital Markets & Investment": "Investment Banking (Goldman Sachs GS · Morgan Stanley MS · Charles Schwab SCHW · Interactive Brokers IBKR · LPL LPLA · Stifel SF · Houlihan Lokey HLI), Asset Mgmt (BlackRock BLK · KKR KKR · Blackstone BX · Apollo APO · T. Rowe Price TROW · State Street STT · BNY Mellon BK · Franklin BEN), Exchanges (S&P Global SPGI · Moody's MCO · CME CME · ICE ICE · Nasdaq NDAQ · MSCI MSCI · 홍콩 HKEX 0388.HK · Deutsche Börse DB1.DE · LSEG LSEG.L · Japan Exchange 8697.T), KR (미래에셋증권 006800.KS · 한국투자증권 비상장 · 키움증권 039490.KS), JP (Nomura 8604.T · Daiwa 8601.T)",
        "Consumer Finance": "US (American Express AXP · Capital One COF · Discover DFS · Ally ALLY · Synchrony SYF · Rocket RKT · loanDepot LDI), Card Networks (Visa V · Mastercard MA), Fintech (PayPal PYPL · Block SQ · Affirm AFRM · SoFi SOFI · Robinhood HOOD · Nu Holdings NU · Adyen ADYEN.AS · Wise WISE.L), KR (카카오뱅크 323410.KS · 토스 비상장 · 케이뱅크 비상장 · 삼성카드 029780.KS · 현대카드 비상장), JP (Orient 8585.T · Credit Saison 8253.T)",
        "Insurance": "P&C US (Berkshire BRK.B · Progressive PGR · Travelers TRV · Allstate ALL · Chubb CB · WR Berkley WRB · Markel MKL · Hartford HIG), Life/Health (MetLife MET · Prudential PRU · Aflac AFL · AIG AIG · Globe Life GL · Manulife MFC · Sun Life SLF), Brokers/Reinsurance (Marsh McLennan MMC · Aon AON · Gallagher AJG · Everest EG · RGA RGA · RenaissanceRe RNR · Swiss Re SREN.SW · Munich Re MUV2.DE), KR (삼성생명 032830.KS · 한화생명 088350.KS · 삼성화재 000810.KS · DB손해보험 005830.KS · 메리츠금융지주 138040.KS), JP (Dai-ichi 8750.T · T&D 8795.T · MS&AD 8725.T · Tokio Marine 8766.T), CN (Ping An 2318.HK · AIA 1299.HK)",
        "Business Development Companies (BDCs)": "US (Ares Capital ARCC · Main Street MAIN · Blue Owl Capital OBDC · Hercules Capital HTGC · Gladstone Capital GLAD · Saratoga SAR · Goldman BDC GSBD), 비상장 PE 영역 (Carlyle CG · TPG TPG · Brookfield BAM)",
        "Digital Assets & Cryptocurrency": "Exchanges (Coinbase COIN · Robinhood HOOD · Marathon Digital MARA · Riot Platforms RIOT), Mining (Riot RIOT · Marathon MARA · CleanSpark CLSK · Bitfarms BITF · TeraWulf WULF · CIFR CIFR), Related (MicroStrategy MSTR — Bitcoin treasury · Galaxy Digital BRPHF · Block SQ · Bitcoin ETF spot 발행사 BlackRock IBIT · Fidelity FBTC), KR (Bithumb 비상장 · Upbit-두나무 비상장 · Wemade 112040.KQ blockchain)",
    },
}
