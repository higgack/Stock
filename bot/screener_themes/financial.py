"""Financial — Wave 2 broad sector (Finviz Financial).

Finviz Financial 12+ industries 전체 cover: Banks Diversified/Regional · Life
Insurance · P&C Insurance · Reinsurance · Insurance Brokers · Asset Mgmt ·
Capital Markets · Financial Data & Exchanges · Credit Services · Mortgage
Finance · Fintech.

ToC 관점:
 1) US regional banks — CRE 만기 2024-2026 wall + office vacancy 25%+ +
    NIM compression. binding constraint = CRE 손실 인식 + 신주발행 dilution.
 2) Big banks - 자기자본 규칙 (Basel III endgame, 2026 final rule) 이
    capital return (배당+자사주매입) ceiling. NII는 yield curve 의존.
 3) Insurance - life: variable annuity hedge book + GMxB rider 부담. 美
    국채 10Y 변동에 자본여력 swing. P&C: 자연재해 reinsurance 가격 +
    auto+homeowners loss ratio cycle.
 4) Asset Mgmt - private alternatives (BX/KKR/APO/ARES) AUM 성장 vs 전통
    long-only fee compression. ETF 시장 BLK 독점 + IVV 비용 0bp 경쟁.
 5) Exchanges + Financial Data - 거래량 의존 (ICE/CME/NDAQ) vs 데이터
    구독 (MSCI/SPGI/MCO/FDS). AI 도입으로 raw data 가치 상승.
 6) Card Networks - V/MA duopoly + Durbin extension 위협. AXP T&E 회복.
    PayPal/Block 매출 marketplace 둔화. Stablecoin 결제 disrupt 잠재.
 7) Fintech - GENIUS / CLARITY Act 통과 시 stablecoin issuer 합법화 →
    Coinbase / Robinhood 거래량 확장 + 은행 deposit base 위협.

Wave 1 와 겹침 없음 — 본 모듈이 financial sector 의 first dedicated lens.
"""

from __future__ import annotations

THEME = {
    "domain": "Financial (Finviz Sector — Banks · Insurance · Asset Mgmt · Exchanges · Cards · Fintech)",
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
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Banks — Diversified (US Big 4 · 글로벌 GSIB)",
        "Banks — Regional (CRE 만기 wall · NIM compression)",
        "Insurance — Life (variable annuity GMxB · 美 10Y 민감도)",
        "Insurance — P&C / Reinsurance (자연재해 cycle · auto loss ratio)",
        "Insurance — Brokers (AON · MMC · WLTW commission)",
        "Asset Management — Traditional + Alternatives (PE/HF · ETF)",
        "Capital Markets (GS / MS / SCHW trading · M&A · IPO)",
        "Financial Data & Exchanges (ICE / CME / MSCI / SPGI / MCO)",
        "Credit Services (V / MA / AXP card networks · BNPL)",
        "Fintech / Crypto / Stablecoin (PYPL · SQ · COIN · HOOD)",
        "Mortgage Finance (FNMA · Rocket · loanDepot)",
    ],
    "catalyst_types": [
        "Fed funds rate path + Powell 의장 (2026-05 임기 만료) 후임자 인선",
        "Basel III endgame 최종 룰 + CCAR stress test 결과 → 배당/자사주 ceiling",
        "美 CRE 만기 wall (2024-2026 ~$2T) + office vacancy + delinquency",
        "Insurance variable annuity hedge book + 美 10Y 급변 시 자본여력 swing",
        "Asset Mgmt 분기 net flow (BLK ETF + KKR/APO 인수 deal)",
        "Exchanges 거래량 + 데이터 구독 ARR 성장 (MSCI / SPGI / MCO)",
        "GENIUS Act + CLARITY Act (stablecoin 입법) 표결 일정",
        "Card 네트워크 Durbin 확대 / Visa-Mastercard 합의금 + 美 BNPL 규제",
        "中国 LPR + 부동산 신용 압박 + 港股통 southbound 보험 매수 변동",
    ],
    "regional_concentration": {
        "Banks — Diversified (US Big 4)": "JPMorgan JPM / Bank of America BAC / Citigroup C / Wells Fargo WFC",
        "Banks — Diversified (Global GSIB)": "HSBC 0005.HK / Deutsche Bank DBK.DE / BNP Paribas BNP.PA / Barclays BARC.L / Santander SAN.MC / UBS UBS / Mitsubishi UFJ 8306.T / SMFG 8316.T / Mizuho 8411.T",
        "Banks — Regional US": "US Bancorp USB / PNC PNC / Truist TFC / Fifth Third FITB / Regions RF / Huntington HBAN / KeyCorp KEY / M&T MTB / Comerica CMA / Zions ZION / Webster WBS",
        "Banks — KR/Asia": "KR (KB 105560.KS / 신한 055550.KS / 하나 086790.KS / 우리 316140.KS / IBK 024110.KS), CN (ICBC 1398.HK / CCB 0939.HK / Bank of China 3988.HK / ABC 1288.HK / BOCOM 3328.HK / 招商 3968.HK)",
        "Insurance — Life": "MetLife MET / Prudential PRU / Lincoln Financial LNC / Manulife MFC / Sun Life SLF, JP (Dai-ichi 8750.T / T&D 8795.T / MS&AD 8725.T), CH (Swiss Life SLHN.SW), KR (삼성생명 032830.KS / 한화생명 088350.KS)",
        "Insurance — P&C / Reinsurance": "Travelers TRV / Progressive PGR / Allstate ALL / Chubb CB / Hartford HIG / W.R. Berkley WRB / Markel MKL / RGA RGA / RenaissanceRe RNR / Everest EG / Swiss Re SREN.SW / Munich Re MUV2.DE",
        "Insurance — Brokers": "Aon AON / Marsh McLennan MMC / WTW WTW / Arthur J. Gallagher AJG / Brown & Brown BRO",
        "Asset Management — Traditional": "BlackRock BLK / T. Rowe Price TROW / Franklin Resources BEN / Invesco IVZ / Janus Henderson JHG, EU (Schroders SDR.L / Amundi AMUN.PA)",
        "Asset Management — Alternatives (PE/HF)": "Blackstone BX / KKR KKR / Apollo APO / Ares ARES / Carlyle CG / Brookfield BAM / TPG TPG / Bridgepoint BPT.L / EQT EQT.ST",
        "Capital Markets": "Goldman Sachs GS / Morgan Stanley MS / Charles Schwab SCHW / Raymond James RJF / Stifel SF / LPL LPLA / Interactive Brokers IBKR",
        "Financial Data & Exchanges": "ICE ICE / CME CME / Nasdaq NDAQ / MSCI MSCI / S&P Global SPGI / Moody's MCO / FactSet FDS / Tradeweb TW / MarketAxess MKTX / 홍콩 HKEX 0388.HK / Deutsche Börse DB1.DE / LSEG LSEG.L / Japan Exchange 8697.T",
        "Credit Services": "Visa V / Mastercard MA / American Express AXP / Capital One COF / Discover DFS / Synchrony SYF / Ally ALLY",
        "Fintech / Crypto / Stablecoin": "PayPal PYPL / Block SQ / Affirm AFRM / SoFi SOFI / Robinhood HOOD / Coinbase COIN / Nu Holdings NU / Adyen ADYEN.AS / Wise WISE.L, KR (카카오뱅크 323410.KS / 토스 비상장)",
        "Mortgage Finance": "Rocket RKT / loanDepot LDI / UWM Holdings UWMC / Mr. Cooper COOP",
    },
}
