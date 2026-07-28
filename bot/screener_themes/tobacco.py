"""Tobacco — L3 under Consumer Staples.

Philip Morris (IQOS) + Altria (US 시장 + JUUL) + BAT (Vuse) + Japan
Tobacco (Ploom) + KT&G. 美 FDA menthol ban + PMTA Vape 승인 + Heat-Not-
Burn 침투율이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Tobacco (담배)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "tobacco",
        "담배_l3",
        "tobacco_l3",
        "vape",
        "전자담배",
        "iqos",
        "heat_not_burn",
        "hnb",
        "nicotine_pouch",
        "니코틴파우치",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "International (Philip Morris International — IQOS 글로벌)",
        "US Domestic (Altria — Marlboro · NJOY · 분리)",
        "EU/UK (British American Tobacco — Vuse · glo · Velo)",
        "Japan (Japan Tobacco — Ploom · Camel · Winston)",
        "Korea + Asia (KT&G + China Tobacco 비상장)",
        "차세대 Vape + Nicotine Pouch (NJOY · Vuse · ZYN · Velo)",
    ],
    "catalyst_types": [
        "美 FDA menthol ban 시행 + PMTA (Vape) 신규 승인 일정",
        "Philip Morris IQOS 미국 진출 (NJOY 인수 후) + 분기 매출 가이드",
        "British American Tobacco Vuse 분기 매출 + EU 일회용 vape 규제",
        "美 ZYN/Velo nicotine pouch 매출 폭증 + 청소년 흡연 통계",
        "中国 담배 산업 (China Tobacco 비상장) 价格 인상 + 한국 KT&G 수출",
        "EU 새로운 담배 세금 + tobacco product directive 개정",
    ],
    "regional_concentration": {
        "International + US": "Philip Morris International PM (IQOS 글로벌 + NJOY) · Altria MO (Marlboro US · NJOY 합병 후) · British American Tobacco BTI (Vuse · glo · Velo) · Japan Tobacco 2914.T (Camel · Winston · Ploom) · Imperial Brands IMB.L · Reynolds American 비상장 (BAT 자회사) · Vector Group VGR · Universal Corporation UVV (담배 거래 leaf)",
        "EU Vape + Specialty": "British American Tobacco BTI · Imperial Brands IMB.L · Philip Morris PM · Pyxus International 비상장 · Aurora Cannabis 비상장 (Vape 관련) · ELF Beauty ELF (FDA 비흡연 제품 인접 분야)",
        "Korea + Asia": "KT&G 033780.KS (담배 + 인삼) · China Tobacco 비상장 (中国烟草总公司 — 비상장 국유) · 上海烟草 비상장 · Wuhan Tobacco 비상장 · JT (Japan Tobacco) 2914.T · ITC Limited ITC.NS (인도 - 담배 + 호텔 + FMCG)",
        "Vape Hardware + 차세대": "Smoore International 6969.HK · 思摩尔 6969.HK · Relx 비상장 (vape 글로벌) · NJOY (Altria 자회사) · Juul Labs 비상장 · BAT Vuse 자회사 BTI · KT&G 릴 lil 33780.KS · NICOventures (BAT 자회사)",
        "Nicotine Pouch / 무연 담배": "Swedish Match 비상장 (Philip Morris PM 자회사 - ZYN · General · Lyft 브랜드) · British American Tobacco BTI (Velo) · 22nd Century 22NDC · Charlie's Holdings 비상장",
    },
}
