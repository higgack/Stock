"""Biotech & Pharma — Wave 1 domain.

ToC choke point 분석:
 1) GLP-1 (Lilly LLY / Novo NVO) — 수요는 단기 무한 (비만 + 심혈관 +
    CKD + 슬립apnea 라벨 확장 + Medicare 부분 커버). Binding constraint
    는 'fill-finish' capacity (피하주사 펜 카트리지 + 무균 충전 라인).
    Lilly 캐파 빌드 미국 NC + Concord, Novo 덴마크 + Catalent 인수. 진짜
    rent 독식 = (a) GLP-1 본사 + (b) fill-finish CDMO + (c) cardiotmetabolic
    Med Device (인슐린 펜 매니저).
 2) CDMO biologics (단백질 / mRNA / ADC) — WuXi BIOSECURE Act 후폭풍 →
    중국 비중 (WuXi Biologics / WuXi AppTec) 매출이 한국 (삼성바이오) +
    유럽 (Lonza) + 미국 (Catalent · Lilly 인수) 로 시프트. 한국 송도
    capacity 가 최대 수혜.
 3) CRO clinical trials (IQVIA / ICON / Charles River) — 임상 1·2상
    pipeline 정체 → 매출 단기 약화, 단 GLP-1 라벨 확장 + 비만 후속
    실험 trial 수 증가가 catalyst.
 4) Biosimilar — 美 IRA 협상가 + Stelara·Humira biosimilar 침투 →
    Sandoz NVS / Samsung Bioepis (삼성바이오 자회사) / Celltrion 셀트
    리온 매출 확장.
 5) Gene/Cell therapy (Vertex VRTX / CRISPR CRSP / BMRN) — 비싼 가격
    + 협소 환자 풀로 binding 약함, Wave 1 비중 낮춤.

Catalyst weighting: FDA PDUFA 일정 (GLP-1 라벨 확장 / biosimilar 승인)
가 최우선 선행지표. Medicare 협상 1차 10개 약가 발표 (2026 effective)
가 IRA 영향 정량화. 中国 BIOSECURE 법안 (House/Senate) 통과 시점이
CDMO 시프트 trigger.
"""

from __future__ import annotations

THEME = {
    "domain": "Biotech & Pharma (GLP-1 / CDMO / Biosimilar)",
    "aliases": [
        "pharma",
        "biotech",
        "bio",
        "바이오",
        "제약",
        "glp1",
        "cdmo",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "GLP-1 본사 + 라벨 확장 (비만 / CV / CKD / sleep apnea)",
        "GLP-1 fill-finish + 펜 카트리지 CDMO",
        "CDMO biologics (단백질 / mRNA / ADC)",
        "CRO 임상시험 (Phase 1-3 + 사후관리)",
        "Biosimilar (Humira / Stelara / Keytruda 차세대)",
        "Cell & gene therapy (CRISPR / AAV / CAR-T)",
        "Specialty drug pricing 협상 (Medicare 1차 10개)",
    ],
    "catalyst_types": [
        "FDA PDUFA + Advisory Committee 일정 (GLP-1 + biosimilar 승인)",
        "Medicare 협상가 발표 (1차 10개 → 2차 15개) + IRA 후속",
        "中国 BIOSECURE Act 통과 일정 (House 통과 시점 / Senate 표결)",
        "GLP-1 매출 가이드 (Lilly Mounjaro / Novo Wegovy 분기 콘퍼런스 콜)",
        "Biosimilar 침투율 (Humira 1년 후 / Stelara 출시)",
        "EU EMA CHMP 의견 + 영국 NICE HTA 승인",
        "AI 신약발견 + Big Pharma 라이선스 계약 (DeepMind / Recursion)",
    ],
    "regional_concentration": {
        "GLP-1 본사": "US (Eli Lilly LLY — Mounjaro/Zepbound), DK (Novo Nordisk NVO — Ozempic/Wegovy)",
        "GLP-1 도전자": "US (Amgen AMGN — MariTide / Viking Therapeutics VKTX / Structure Therapeutics GPCR), CN (Innovent 1801.HK)",
        "CDMO biologics (글로벌)": "KR (삼성바이오 207940.KS — 글로벌 1위 mAb capacity), CH (Lonza LONN.SW), US (Catalent 비상장 — Novo 인수 / Thermo Fisher TMO)",
        "CDMO biologics (中)": "WuXi Biologics 2269.HK / WuXi AppTec 2359.HK (BIOSECURE 영향)",
        "CRO 임상": "US (IQVIA IQV / ICON ICLR / Charles River CRL / Medpace MEDP / Syneos 비상장)",
        "Biosimilar 본사": "KR (셀트리온 068270.KS / 삼성바이오로직스 자회사 Samsung Bioepis), CH (Sandoz SDZ.SW / Roche ROG.SW), IL (Teva TEVA), IN (Biocon 532523.BO)",
        "Gene therapy + AAV": "US (Vertex VRTX — CRISPR cas9 partner / CRISPR Therapeutics CRSP / BioMarin BMRN / Sarepta SRPT / Solid Biosciences SLDB)",
        "AI 신약발견": "US (Recursion RXRX / Schrödinger SDGR / AbCellera ABCL), CN (Insilico 비상장)",
        "Diabetes Med Device": "US (Insulet PODD — Omnipod / Dexcom DXCM — CGM / Tandem TNDM), DK (Coloplast COLO-B.CO)",
    },
}
