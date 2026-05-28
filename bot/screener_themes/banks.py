"""Banks — L3 under Financials.

US Big 4 + Regional + Global GSIB + KR/JP 메가뱅크 + CN 国有 4대. Fed
funds rate path + Basel III endgame + CRE 만기 wall + 港股 southbound
+ KR DLF 후속 규제가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Banks (은행)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "banks",
        "bank_l3",
        "diversified_banks",
        "regional_banks",
        "지역은행",
        "다각화은행",
        "메가뱅크",
        "gsib",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "US Big 4 Diversified (JPM · BAC · C · WFC)",
        "US Regional (USB · PNC · TFC · KEY · MTB · FITB · CFG · HBAN)",
        "Global GSIB (HSBC · Deutsche Bank · BNP · Barclays · UBS · SAN)",
        "Japan Mega-banks (MUFG · SMFG · Mizuho)",
        "Korea 금융지주 (KB · 신한 · 하나 · 우리 · IBK)",
        "China 国有 + Joint-Stock + Insurance Bank",
    ],
    "catalyst_types": [
        "Fed funds rate path + Powell 의장 임기 만료 후임자 인선",
        "Basel III endgame 최종 룰 + CCAR stress test → 배당/자사주 ceiling",
        "美 CRE 만기 wall (2024-2026 ~$2T) + office vacancy + delinquency",
        "Regional bank NIM compression + 신주발행 dilution 압박",
        "中国 PBoC LPR + 부동산 신용 압박 + 港股통 southbound 은행 매수",
        "한국 DLF·부동산 PF 후속 규제 + 자본 적정성 (CET1) 변화",
    ],
    "regional_concentration": {
        "US Big 4": "JPMorgan Chase JPM · Bank of America BAC · Wells Fargo WFC · Citigroup C",
        "US Regional": "U.S. Bancorp USB · PNC Financial PNC · Truist Financial TFC · KeyCorp KEY · M&T Bank MTB · Fifth Third FITB · Citizens Financial CFG · Huntington Bancshares HBAN · Regions Financial RF · First Horizon FHN · Zions ZION · Comerica CMA · Webster WBS · East West Bank EWBC · Synovus SNV · Bank OZK OZK · Pinnacle Financial PNFP · Western Alliance WAL · Cullen/Frost CFR · Texas Capital TCBI",
        "Global GSIB Europe": "HSBC 0005.HK · Deutsche Bank DBK.DE · BNP Paribas BNP.PA · Barclays BARC.L · Santander SAN.MC · UBS UBSG.SW · Standard Chartered STAN.L · Credit Suisse 비상장 (UBS 인수 후) · Société Générale GLE.PA · ING ING · Lloyds LLOY.L · NatWest NWG.L · Commerzbank CBK.DE · KBC KBC.BR · BPER BPE.MI · UniCredit UCG.MI",
        "Japan Mega-banks + Regional": "Mitsubishi UFJ 8306.T · SMFG 8316.T · Mizuho 8411.T · Resona 8308.T · Bank of Kyoto 8369.T · Concordia FG 7186.T · Chiba Bank 8331.T",
        "Korea 금융지주": "KB 105560.KS · 신한 055550.KS · 하나 086790.KS · 우리 316140.KS · IBK 024110.KS · BNK 138930.KS · DGB 139130.KS · JB 175330.KS · 카카오뱅크 323410.KS",
        "China 국유 + Joint-Stock": "ICBC 1398.HK + 601398.SS · CCB 0939.HK + 601939.SS · Bank of China 3988.HK · ABC 1288.HK · BOCOM 3328.HK · 招商 3968.HK · 中信 0998.HK · 民生 1988.HK · 兴业 601166.SS · 浦发 600000.SS",
        "Other Asia + EM": "DBS D05.SI · OCBC O39.SI · UOB U11.SI · HDFC Bank HDB · ICICI Bank IBN · State Bank of India SBIN.NS · Itau Unibanco ITUB · Banco Santander Brasil BSBR · Banco BBVA BBVA · BBVA Banco Frances BBAR · Tinkoff TCS · Sberbank SBER.MM",
    },
}
