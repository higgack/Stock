"""Robotics & Humanoid Buildout — Wave 1 trend theme (2026-05-29).

사용자 prioritization 2026-05-29: "다음으로 딴것보다 로봇(Robot) 먼저
하자. 이게 지금 엄청 핫한 테마이니까." Wave 2-B (소비재·통신·부동산·
유틸·소재) 보다 우선 add.

ToC 관점 — 휴머노이드 commercialization cycle 시작점 (Tesla Optimus
양산 가이드 / NVIDIA GR00T foundation model 출시 / 中国 14차 5개년
휴머노이드 보조금 wave / 美 BLS reshoring 인건비 vs 로봇 TCO cross-
over) 의 binding constraint 식별:

 1) 휴머노이드 본사 (Tesla / Figure / 1X / Apptronik / Unitree / UBTech)
    는 1차 헤드라인 — 진짜 rent 독식은 부품 단계.
 2) **감속기 / Reducer 가 가장 강한 binding** — 휴머노이드 관절당 2-6개
    필요, Harmonic Drive + Nabtesco + Sumitomo Heavy 가 글로벌 capacity
    95%+ 점유. 中国 Leader Drive / Zhongdali 의 추격 속도가 rate-
    limiter. 매월 출하 capacity 가 humanoid 양산 일정 직결.
 3) Servo motor + actuator (Yaskawa / Mitsubishi / Nidec / Inovance) 는
    binding 차순위 — 표준품 대비 humanoid 용 미니어처 high-torque
    spec 가 별도 capacity.
 4) 산업 로봇 OEM (Fanuc / Yaskawa / ABB / KUKA / Estun) cycle 는
    자동차 + 반도체 capex 의존 — 휴머노이드 와 별도 traction.
 5) 협동로봇 (Cobot) - 두산 + 레인보우 + Universal Robots — humanoid
    전 단계 시장에서 가격 / 안전성 우위, 美 reshoring 직접 수혜.
 6) AMR / Warehouse (Symbotic / KION) — Amazon + Walmart capex 의존.
 7) 수술로봇 (ISRG / SYK MAKO) — healthcare 와 부분 overlap, robot
    모듈에서는 sub-layer 만 cover.
 8) Vision / 3D / Tactile 센서 + Edge AI / VLA Foundation Model —
    NVIDIA GR00T 채택률 + Keyence / Cognex / Sony imaging 의 humanoid
    perception stack 침투율.
"""

from __future__ import annotations

THEME = {
    "domain": "Robotics & Humanoid Buildout",
    "aliases": [
        "robot",
        "robots",
        "robotics",
        "로봇",
        "로보틱스",
        "humanoid",
        "휴머노이드",
        "cobot",
        "협동로봇",
        "산업로봇",
        "automation",
        "자동화",
        "optimus",
        "figure",
        "감속기",
        "actuator",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "휴머노이드 본사 (Tesla Optimus · Figure · 1X · Apptronik · 中 Unitree/UBTech)",
        "감속기 / Reducer (Harmonic Drive · Nabtesco · RV / cycloidal — humanoid 양산의 가장 명확한 binding)",
        "Servo motor / Actuator (정밀 모터 + 드라이브)",
        "산업 로봇 OEM (Fanuc · Yaskawa · ABB · Kawasaki · 中 Estun)",
        "협동로봇 (Cobot — 두산로보 · 레인보우 · Universal Robots)",
        "AMR / Warehouse 로봇 (Symbotic · KION · Daifuku)",
        "Vision · 3D · Tactile 센서 (Keyence · Cognex · Sony imaging · Omron)",
        "Edge AI / Robot Brain + VLA Foundation Model (NVIDIA GR00T · Qualcomm)",
    ],
    "catalyst_types": [
        "Tesla Optimus 양산 ramp 가이드 + 외부 판매 단가 발표 (Musk Q&A vs 실제 출하)",
        "NVIDIA GR00T (humanoid foundation model) OEM 채택 발표 (CES / GTC / Computex)",
        "Figure / 1X / Apptronik / Agility 자금 조달 + BMW/Mercedes/Amazon 파일럿 결과",
        "中国 14차 5개년 휴머노이드 보조금 + Unitree H1 / UBTech Walker 양산 일정",
        "日 산업 로봇 분기 수주잔고 + 자동차 + 반도체 capex 사이클 회복",
        "KR 협동로봇 수출 (두산 H series · 레인보우 RB series) + 인도/북미 reshoring 데이터",
        "美 BLS Wage Cost Index vs 로봇 TCO cross-over + reshoring announcements",
        "감속기 캐파 증설 발표 (Harmonic Drive 토치기 / Nabtesco 츠 / 中 Leader Drive Nanjing)",
    ],
    "regional_concentration": {
        "휴머노이드 본사 (US)": "Tesla TSLA (Optimus) · Boston Dynamics 비상장 (Hyundai 자회사) · Figure 비상장 · 1X 비상장 · Apptronik 비상장 · Agility Robotics 비상장 · Sanctuary AI 비상장",
        "휴머노이드 본사 (CN)": "Unitree 비상장 (H1 / G1) · UBTech 9880.HK (Walker) · Fourier 비상장 (GR-1) · EngineAI 비상장 · XPeng 9868.HK (Iron robot)",
        "휴머노이드 본사 (KR/JP)": "KR (현대차 005380.KS — Boston Dynamics 모회사 / 한화로보틱스 비상장 / 레인보우 277810.KS humanoid R&D), JP (Sony 6758.T / Honda 7267.T Asimo legacy)",
        "감속기 / Reducer (글로벌 binding)": "JP (Harmonic Drive 6324.T — wave-gear 독점 / Nabtesco 6268.T — RV reducer / Sumitomo Heavy 6302.T — cycloidal), CN (Leader Drive 688017.SS / Zhongdali 300979.SZ / Estun 002747.SZ reducer 자회사), KR (에스피지 058610.KQ / SBB테크 366030.KQ — 신규 진입)",
        "Servo / Actuator": "JP (Yaskawa 6506.T · Mitsubishi Electric 6503.T · Nidec 6594.T · Sanyo Denki 6516.T), DE (Bosch Rexroth 비상장), CN (Inovance 300124.SZ · Estun 002747.SZ servo · Moons Industries 002527.SZ), KR (LS ELECTRIC 010120.KS)",
        "산업 로봇 OEM": "JP (Fanuc 6954.T · Yaskawa 6506.T · Kawasaki Heavy 7012.T · Mitsubishi Electric 6503.T · DENSO 비상장), CH (ABB ABBN.SW), DE (KUKA — 中 Midea 인수 후 비상장), CN (Estun 002747.SZ · Inovance 300124.SZ · Siasun 300024.SZ · Step Electric 002527.SZ)",
        "협동로봇 (Cobot)": "KR (두산로보틱스 454910.KS · 레인보우로보틱스 277810.KS · 뉴로메카 348340.KQ · 유진로봇 056080.KQ), DK (Teradyne TER — Universal Robots 모회사), JP (Yaskawa MOTOMAN-HC 6506.T · FANUC CRX series 6954.T), CN (JAKA 비상장 · Dobot 비상장 · Elite Robot 비상장)",
        "AMR / Warehouse 로봇": "US (Symbotic SYM — Walmart 파트너 · Honeywell HON Intelligrated), DE (KION KGX.DE Dematic), JP (Hitachi 6501.T · Daifuku 6383.T), CN (Geek+ 비상장 · Quicktron 비상장)",
        "수술 로봇 (healthcare overlap)": "US (Intuitive Surgical ISRG · Stryker SYK MAKO · Globus Medical GMED · Vicarious 비상장), CN (MicroPort 0853.HK), KR (큐렉소 060280.KQ · 미래컴퍼니 049950.KQ · 고영 098460.KQ)",
        "Vision / 3D / Camera (perception)": "JP (Keyence 6861.T · Omron 6645.T · Sony Imaging 6758.T), US (Cognex CGNX · Teledyne TDY)",
        "LiDAR (자율주행 + 로봇 navigation)": "US (Luminar LAZR · Aeva AEVA · Ouster OUST · Innoviz INVZ), CN (Hesai HSAI · RoboSense 2498.HK)",
        "Tactile / Force / Torque sensor": "US (Sensata ST · Tekscan 비상장 · ATI Industrial 비상장), JP (Sumitomo Riko 5191.T · Wacoh-Tech 비상장), DE (HBK 비상장)",
        "End-effector / Gripper": "DE (Schunk 비상장 · SCHMALZ 비상장 · Festo 비상장), JP (SMC 6273.T · Misumi 9962.T), US (Soft Robotics 비상장 · Righthand 비상장)",
        "Edge AI / Robot Brain + VLA": "US (NVIDIA NVDA — GR00T foundation / Qualcomm QCOM · Ambarella AMBA · Hailo 비상장), TW (MediaTek 2454.TW · Realtek 2379.TW · Andes 6533.TW RISC-V), JP (Renesas 6723.T MCU · Sony 6758.T edge AI 자회사)",
    },
}
