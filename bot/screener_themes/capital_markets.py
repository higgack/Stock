"""Capital Markets & Investment — L3 under Financials.

Investment Banks + Brokerage + Asset Management (Traditional + Alternatives)
+ Custodians + Exchanges + Financial Data. IPO/M&A volume + AUM net flow
+ ETF inflow + 거래량이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Capital Markets & Investment (자본 시장 및 투자)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "capital_markets",
        "investment_banking",
        "투자은행",
        "ib",
        "brokerage",
        "증권",
        "asset_management_l3",
        "자산운용_l3",
        "exchanges",
        "거래소",
        "alt_assets",
        "대체투자",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Investment Banking + Brokerage (GS · MS · Schwab · IBKR · LPL)",
        "Asset Management — Traditional (BLK · TROW · BEN · IVZ · STT · BK)",
        "Asset Management — Alternatives (BX · KKR · APO · ARES · CG · BAM · TPG)",
        "Financial Exchanges (CME · ICE · NDAQ · 홍콩HKEX · LSEG)",
        "Financial Data + Ratings (SPGI · MCO · MSCI · FDS · MarketAxess)",
    ],
    "catalyst_types": [
        "美 IPO + M&A volume + 분기 investment banking fee + advisory backlog",
        "BLK · KKR · APO 분기 AUM net flow + 신규 fund 모집 + DPI/IRR 가이드",
        "ETF spot Bitcoin/Ethereum inflow + BLK iShares 점유율",
        "분기 거래량 (CME 옵션 + ICE energy + NDAQ Cash Equity)",
        "MSCI 인덱스 rebalance + 신규 ESG/AI 테마 ETF 출시",
        "美 Reg BI + EU MiFID II/III 후속 + retail brokerage 수수료 압박",
    ],
    "regional_concentration": {
        "Investment Banking + Brokerage": "Goldman Sachs GS · Morgan Stanley MS · Charles Schwab SCHW · Interactive Brokers IBKR · LPL Financial LPLA · Stifel SF · Houlihan Lokey HLI · Raymond James RJF · Lazard LAZ · Evercore EVR · Moelis MC · PJT Partners PJT · Jefferies JEF · StoneX SNEX · BGC Group BGC · TD Cowen 비상장",
        "Asset Mgmt Traditional": "BlackRock BLK · T. Rowe Price TROW · Franklin Resources BEN · Invesco IVZ · State Street STT · BNY Mellon BK · Northern Trust NTRS · Affiliated Managers AMG · Federated Hermes FHI · WisdomTree WT · Janus Henderson JHG · Victory Capital VCTR · Virtus VRTS · Cohen & Steers CNS",
        "Asset Mgmt Alternatives (PE/HF)": "Blackstone BX · KKR KKR · Apollo APO · Ares ARES · Carlyle CG · Brookfield BAM · TPG TPG · Bridge Investment 비상장 · Hamilton Lane HLNE · StepStone STEP · Patria PAX · GCM Grosvenor GCMG · BlueRock 비상장 · EQT EQT.ST",
        "Financial Exchanges": "ICE ICE · CME CME · Nasdaq NDAQ · MarketAxess MKTX · Tradeweb TW · Cboe CBOE · 홍콩 HKEX 0388.HK · Deutsche Börse DB1.DE · LSEG LSEG.L · Japan Exchange 8697.T · Euronext ENX.PA · ASX ASX.AX · B3 B3SA3.SA · Singapore Exchange S68.SI · MSCI MSCI",
        "Financial Data + Ratings": "S&P Global SPGI · Moody's MCO · MSCI MSCI · FactSet FDS · Morningstar MORN · MarketAxess MKTX · Verisk VRSK · CME CME · MarketWise MKTW · Compagnie Financière Tradition CFT.SW",
        "Korea + Japan + EU Brokers": "미래에셋증권 006800.KS · 한국투자증권 비상장 · 키움증권 039490.KS · NH투자증권 005940.KS · 삼성증권 016360.KS · 메리츠증권 008560.KS · 대신증권 003540.KS · JP (Nomura 8604.T · Daiwa 8601.T · SBI Holdings 8473.T · Monex 8698.T) · EU (Amundi AMUN.PA · Schroders SDR.L · Man Group EMG.L · Pollen Street POLN.L · Bridgepoint BPT.L)",
    },
}
