"""Semiconductor Equipment & Materials — L4 under Semiconductors (Technology).

전공정 장비 (litho·etch·deposition·CMP) + 검사·계측 + 후공정/패키징 장비
+ 소재 (웨이퍼·포토레지스트·특수가스). ASML EUV 독점·WFE capex·對中 규제가
dominant. 부모 L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "Semiconductor Equipment & Materials (반도체 장비·소재)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["wfe", "etcher", "전공정장비", "후공정장비", "반도체장비l4",
                "litho", "포토레지스트", "웨이퍼", "semi_equip_l4"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "노광 (EUV·DUV — ASML 독점 + High-NA 도입)",
        "식각·증착·CMP (etch · CVD/ALD · 평탄화)",
        "검사·계측 (defect inspection · OCD · metrology)",
        "후공정·패키징·테스트 장비 (본더 · TC bonder · 테스터 · probe)",
        "소재 (실리콘 웨이퍼 · 포토레지스트 · 특수가스 · CMP 슬러리)",
    ],
    "catalyst_types": [
        "글로벌 WFE capex 가이드 + 파운드리/메모리 증설 사이클 전환",
        "ASML High-NA EUV 출하 + DUV 對中 수출 규제(NL/JP 동조) 영향",
        "HBM TSV·하이브리드 본딩 장비 수요 (한미반도체 TC bonder)",
        "美 BIS 對中 장비 규제 4차 + 中 국산 장비 대체율",
    ],
    "regional_concentration": {
        "전공정 (Global·US)": "ASML ASML(EUV 독점) · Applied Materials AMAT · "
                          "Lam Research LRCX · KLA KLAC · Onto Innovation ONTO · "
                          "Axcelis ACLS · MKS Instruments MKSI · Entegris ENTG",
        "전공정 (JP)": "Tokyo Electron 8035.T · Disco 6146.T · Advantest 6857.T · "
                    "Screen 7735.T · Lasertec 6920.T · Kokusai 6525.T",
        "소재": "Shin-Etsu 4063.T · SUMCO 3436.T(웨이퍼) · JSR 4185.T(레지스트) · "
              "동진쎄미켐 005290.KS · 솔브레인 357780.KQ",
        "후공정·테스트 (KR/TW)": "한미반도체 042700.KS · 이오테크닉스 039030.KQ · "
                            "리노공업 058470.KQ · Camtek CAMT · Cohu COHU · "
                            "ASE 3711.TW",
    },
}
