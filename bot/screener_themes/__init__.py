"""Bottleneck Screener theme registry — Wave 1 (5 domains).

Each domain lives in its own module (bot/screener_themes/<slug>.py) as a
top-level ``THEME`` dict with five required keys:

  - ``domain`` (str)                — display name surfaced to Telegram + dashboard
  - ``horizon`` (str)               — e.g. "6-18 months"; used by Pro for tense / kill-trigger
  - ``binding_layer_taxonomy`` (list[str]) — 4-8 niche sub-layers (Theory of Constraints)
  - ``catalyst_types`` (list[str])  — 5-7 leading-signal categories (Tier A weight)
  - ``regional_concentration`` (dict[str, str]) — SPOF map: layer → "REGION (Ticker NAME / Ticker NAME)"

Optionally:
  - ``aliases`` (list[str])         — extra strings that route to this theme via /screener

Resolution order in ``resolve()``:
  1) exact slug match (filename without ``.py``, lowercased)
  2) ``aliases`` exact match (lowercased)
  3) fuzzy: substring match against slug or aliases (only when unambiguous —
     two hits → return None so the user sees an error rather than the wrong theme)

Adding a new theme = drop a new module here. No edits to bot/screener.py or
bot/telegram_bot.py needed — the registry auto-discovers via pkgutil. This
keeps Wave 2/3/∞ domain additions structural (one file per theme), not
sprinkled across the orchestrator.

A registry-level smoke test on import validates each module's THEME shape
so a typo (e.g. ``bindng_layer_taxonomy``) fails fast at startup, not three
minutes into a Pro call.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Optional

log = logging.getLogger("bot.screener_themes")

# Slugs are derived from filenames at import time below. Aliases populate
# the lookup table once. Both maps are lowercased keys for case-insensitive
# /screener input ('/screener EV' == '/screener ev' == '/screener 전기차').
_THEMES: dict[str, dict] = {}
_ALIAS_TO_SLUG: dict[str, str] = {}


_REQUIRED_KEYS = (
    "domain",
    "horizon",
    "binding_layer_taxonomy",
    "catalyst_types",
    "regional_concentration",
)

# 3-layer 도메인 모델 (2026-05-29 사용자 정식 분류 = 미국 GICS-like):
#  - L1_TREND   : cross-cutting cycle 베팅, 공식 sector 분류 외 (AI DC /
#                 EV / 방산 / 바이오 cycle / 신재생 / 로봇 등).
#  - L2_SECTOR  : 11 공식 sector (Industrials / Health Care / Financials
#                 / Consumer Discretionary / Consumer Staples / Energy /
#                 Basic Materials / Real Estate / Utilities / Communication
#                 Services / Technology).
#  - L3_INDUSTRY: 각 L2 아래 sub-industry (예: Industrials → Aerospace
#                 & Defense / Airlines / Building Products ...).
#  - L4_SUBINDUSTRY: 각 L3 아래 세부 sub-industry (2026-06-14 신설 — GICS
#                 sub-industry 깊이). 예: Semiconductors → Memory / Foundry /
#                 Equipment / EDA / Logic AI ... L3 의 binding_layer 가
#                 독립 lens 로 분리. slug = `<L3slug>_<세부>` (parent prefix).
#  - AD_HOC     : `/screener <자유어>` Phase 0 즉석 생성 도메인. 캐시
#                 24h, audit log, 정식 모듈 promote 는 user 수동. 2026-
#                 05-29 신설.
# Layer 표기 누락은 default L1_TREND 로 fallback (back-compat).
_VALID_LAYERS = ("L1_TREND", "L2_SECTOR", "L3_INDUSTRY", "L4_SUBINDUSTRY",
                 "AD_HOC")

_L4_DOMAIN_KO = {
    "aerospace_defense_commercial_aviation_oem": "Commercial Aviation OEM(방산)",
    "aerospace_defense_drones_counter_uas": "Drones & Counter-UAS(방산)",
    "aerospace_defense_military_prime": "Military Prime(방산)",
    "aerospace_defense_naval_shipbuilding": "Naval Shipbuilding(방산)",
    "aerospace_defense_space_launch": "Space Launch + 위성(방산)",
    "aerospace_defense_sub_tier": "Sub-tier(방산)",
    "airlines_asia_low_cost": "Asia Low-cost(항공사)",
    "airlines_asia_premium": "Asia Premium(항공사)",
    "airlines_eu_flag_carriers": "EU Flag Carriers(항공사)",
    "airlines_eu_low_cost": "EU Low-cost(항공사)",
    "airlines_us_low_cost": "US Low-cost(항공사)",
    "airlines_us_network_carriers": "US Network Carriers(항공사)",
    "apparel_luxury_eu_ch": "EU/CH 시계 + 보석(의류럭셔리)",
    "apparel_luxury_eu_hard_luxury": "EU Hard Luxury(의류럭셔리)",
    "apparel_luxury_fast_fashion_asia": "Fast Fashion + Asia(의류럭셔리)",
    "apparel_luxury_us": "US 운동 의류(의류럭셔리)",
    "apparel_luxury_us_apparel": "US Apparel + 액세서리(의류럭셔리)",
    "automotive_auto_parts_adas": "Auto Parts + ADAS(자동차)",
    "automotive_auto_retail": "Auto Retail(자동차)",
    "automotive_ev_pure_play": "EV Pure-play(자동차)",
    "automotive_ice_hybrid_oem": "ICE + Hybrid OEM(자동차)",
    "automotive_luxury_oem": "Luxury OEM(자동차)",
    "automotive_tire": "Tire(자동차)",
    "banks_china_joint_stock_insuranc": "China 国有 + Joint-Stock + Insurance Bank(은행)",
    "banks_global_gsib": "Global GSIB(은행)",
    "banks_japan_mega_banks": "Japan Mega-banks(은행)",
    "banks_korea": "Korea 금융지주(은행)",
    "banks_us_big_4_diversified": "US Big 4 Diversified(은행)",
    "banks_us_regional": "US Regional(은행)",
    "bdc_pe_sponsor_affiliated": "PE Sponsor Affiliated(BDC)",
    "bdc_tier_1_externally_managed": "Tier 1 — Externally Managed Mega-BDCs(BDC)",
    "bdc_tier_2_internally_managed": "Tier 2 — Internally Managed Quality(BDC)",
    "bdc_tier_3_specialty_niche": "Tier 3 — Specialty Niche(BDC)",
    "bdc_venture_debt_focused": "Venture Debt Focused(BDC)",
    "beverages_beer": "Beer(음료)",
    "beverages_energy_drinks_functional": "Energy Drinks + Functional(음료)",
    "beverages_japan_korea": "Japan + Korea 양조(음료)",
    "beverages_non_alcoholic_soft_drinks": "Non-Alcoholic / Soft Drinks(음료)",
    "beverages_spirits_wine": "Spirits + Wine(음료)",
    "beverages_sub": "中国 백주(음료)",
    "building_products_building_products_distribu": "Building products distribution(건축자재)",
    "building_products_elevators_escalators": "Elevators + Escalators(건축자재)",
    "building_products_engineering_construction": "Engineering & Construction(건축자재)",
    "building_products_hvac": "HVAC(건축자재)",
    "building_products_insulation_roofing": "Insulation + Roofing(건축자재)",
    "building_products_water_heating": "Water Heating(건축자재)",
    "capital_markets_asset_management_alternati": "Asset Management — Alternatives(자본시장)",
    "capital_markets_asset_management_tradition": "Asset Management — Traditional(자본시장)",
    "capital_markets_financial_data_ratings": "Financial Data + Ratings(자본시장)",
    "capital_markets_financial_exchanges": "Financial Exchanges(자본시장)",
    "capital_markets_investment_banking_brokera": "Investment Banking + Brokerage(자본시장)",
    "chemicals_agricultural_chemicals": "Agricultural Chemicals + 비료(화학)",
    "chemicals_diversified_specialty": "Diversified + Specialty(화학)",
    "chemicals_industrial_gases": "Industrial Gases(화학)",
    "chemicals_petrochemicals": "Petrochemicals(화학)",
    "chemicals_specialty_for_ev_battery_s": "Specialty for EV Battery + Semiconductor(화학)",
    "commercial_services_facility_services": "Facility Services(상업서비스)",
    "commercial_services_payroll_hcm": "Payroll + HCM(상업서비스)",
    "commercial_services_research_data_analytics": "Research / Data Analytics(상업서비스)",
    "commercial_services_security": "Security(상업서비스)",
    "commercial_services_staffing_recruitment": "Staffing + Recruitment(상업서비스)",
    "construction_materials_asia_cement_korea_japan": "Asia Cement + Korea + Japan(건설자재)",
    "construction_materials_china_cement": "China Cement(건설자재)",
    "construction_materials_concrete_ready_mix": "Concrete + Ready-mix(건설자재)",
    "construction_materials_global_cement": "Global Cement(건설자재)",
    "construction_materials_us_aggregates_cement": "US Aggregates + Cement(건설자재)",
    "consumer_finance_auto_student_lending": "Auto + Student Lending(소비자금융)",
    "consumer_finance_card_networks_duopoly": "Card Networks Duopoly(소비자금융)",
    "consumer_finance_mortgage_finance": "Mortgage Finance(소비자금융)",
    "consumer_finance_premium_card_travel": "Premium Card + Travel(소비자금융)",
    "consumer_finance_subprime_private_label": "Subprime + Private Label(소비자금융)",
    "digital_assets_bitcoin_mining_hashrate_pu": "Bitcoin Mining + Hashrate Pure-play(디지털자산)",
    "digital_assets_bitcoin_treasury_strategy": "Bitcoin Treasury Strategy(디지털자산)",
    "digital_assets_blockchain_infrastructure": "Blockchain Infrastructure(디지털자산)",
    "digital_assets_crypto_exchanges": "Crypto Exchanges(디지털자산)",
    "digital_assets_spot_bitcoin_ethereum_etf": "Spot Bitcoin/Ethereum ETF Issuers(디지털자산)",
    "digital_assets_stablecoin_ecosystem": "Stablecoin Ecosystem(디지털자산)",
    "education_services_children_s_learning_toys": "Children's Learning + Toys(교육)",
    "education_services_higher_ed_online_universit": "Higher Ed + Online University(교육)",
    "education_services_korean_hagwon_asia": "Korean Hagwon + Asia 사교육(교육)",
    "education_services_k_12_online_tutoring": "K-12 + Online Tutoring(교육)",
    "education_services_test_prep_standardized_tes": "Test Prep + Standardized Testing(교육)",
    "electrical_equipment_backup_generator_ats": "Backup Generator + ATS(전기장비)",
    "electrical_equipment_gis": "GIS(전기장비)",
    "electrical_equipment_industrial_automation": "Industrial Automation(전기장비)",
    "electrical_equipment_power_cable": "Power Cable(전기장비)",
    "electrical_equipment_transformer": "Transformer(전기장비)",
    "electrical_equipment_ups_data_center_power_mgmt": "UPS + Data Center Power Mgmt(전기장비)",
    "electric_utility_california": "California(전력)",
    "electric_utility_mega_cap_regulated": "Mega-cap Regulated(전력)",
    "electric_utility_mid_cap_regulated": "Mid-cap Regulated(전력)",
    "electric_utility_multi_utility_combined": "Multi-utility Combined(전력)",
    "electric_utility_texas_ercot": "Texas + ERCOT(전력)",
    "energy_services_drilling_contractors_offsh": "Drilling Contractors — Offshore(에너지)",
    "energy_services_drilling_contractors_onsho": "Drilling Contractors — Onshore(에너지)",
    "energy_services_frac_pressure_pumping": "Frac + Pressure Pumping(에너지)",
    "energy_services_integrated_oilfield_servic": "Integrated Oilfield Services(에너지)",
    "energy_services_ofs_equipment_tools": "OFS Equipment + Tools(에너지)",
    "energy_services_subsea_epc": "Subsea + 해상 EPC(에너지)",
    "entertainment_cinema_movie_theater": "Cinema + Movie Theater(엔터)",
    "entertainment_japanese_animation_studios": "Japanese Animation + Studios(엔터)",
    "entertainment_korean_entertainment_big_4": "Korean Entertainment Big 4(엔터)",
    "entertainment_live_events_sports": "Live Events + Sports(엔터)",
    "entertainment_music_streaming_labels": "Music Streaming + Labels(엔터)",
    "entertainment_streaming_studio": "Streaming + Studio(엔터)",
    "food_products_agricultural_commodities_t": "Agricultural Commodities + Trading(식품)",
    "food_products_dairy_yogurt": "Dairy + Yogurt(식품)",
    "food_products_frozen_specialty": "Frozen + Specialty(식품)",
    "food_products_meat_protein": "Meat + Protein(식품)",
    "food_products_packaged_foods": "Packaged Foods(식품)",
    "food_products_snacks_confectionery": "Snacks + Confectionery(식품)",
    "food_retailing_convenience_stores": "Convenience Stores(식료품유통)",
    "food_retailing_dollar_stores": "Dollar Stores(식료품유통)",
    "food_retailing_eu_asia_grocery": "EU + Asia Grocery(식료품유통)",
    "food_retailing_mass_merchant_warehouse_cl": "Mass Merchant + Warehouse Club(식료품유통)",
    "food_retailing_traditional_grocery": "Traditional Grocery(식료품유통)",
    "forest_paper_pulp_paper_brands": "Pulp + Paper Brands(산림종이)",
    "forest_paper_specialty_paper_tissue": "Specialty Paper + Tissue(산림종이)",
    "forest_paper_timber_lumber": "Timber + Lumber(산림종이)",
    "forest_paper_wood_products_building": "Wood Products + Building(산림종이)",
    "gaming_aaa_publishers": "AAA Publishers(게임)",
    "gaming_console_mfg_platform": "Console Mfg + Platform(게임)",
    "gaming_japanese_mobile_anime_swit": "Japanese Mobile + Anime + Switch(게임)",
    "gaming_korean_mobile_pc": "Korean Mobile/PC(게임)",
    "gaming_online_mobile": "Online + Mobile(게임)",
    "gaming_web3_blockchain_gaming": "Web3 + Blockchain Gaming(게임)",
    "gas_water_asia_gas_water": "Asia Gas + Water(가스수도)",
    "gas_water_eu_water_gas": "EU Water + Gas(가스수도)",
    "gas_water_regulated_gas_distribution": "Regulated Gas Distribution(가스수도)",
    "gas_water_water_equipment_tech": "Water Equipment + Tech(가스수도)",
    "gas_water_water_wastewater_us": "Water/Wastewater US(가스수도)",
    "hardware_storage_communications_equipment_a": "Communications Equipment + AI Fabric Switch(하드웨어)",
    "hardware_storage_consumer_electronics": "Consumer Electronics(하드웨어)",
    "hardware_storage_networking_connectivity": "Networking + Connectivity(하드웨어)",
    "hardware_storage_storage_devices_memory_for": "Storage Devices + Memory Form Factor(하드웨어)",
    "hardware_storage_taiwan_odm_ems": "Taiwan ODM/EMS(하드웨어)",
    "hardware_storage_tech_hardware": "Tech Hardware(하드웨어)",
    "healthcare_providers_animal_health_diagnostics": "Animal Health + Diagnostics(의료서비스)",
    "healthcare_providers_diagnostics_research_cro": "Diagnostics & Research / CRO(의료서비스)",
    "healthcare_providers_digital_health_telehealth": "Digital Health + Telehealth(의료서비스)",
    "healthcare_providers_hospitals_care_facilities": "Hospitals & Care Facilities(의료서비스)",
    "healthcare_providers_managed_health_care": "Managed Health Care(의료서비스)",
    "healthcare_providers_medical_distribution": "Medical Distribution(의료서비스)",
    "homebuilding_building_products": "Building Products(주택가구)",
    "homebuilding_home_furnishings": "Home Furnishings(주택가구)",
    "homebuilding_manufactured_homes_modular": "Manufactured Homes + Modular(주택가구)",
    "homebuilding_multi_family_build_to_rent": "Multi-Family + Build-to-Rent(주택가구)",
    "homebuilding_single_family_homebuilders": "Single-Family Homebuilders(주택가구)",
    "hospitality_cruise_lines": "Cruise Lines(숙박여가)",
    "hospitality_fast_casual_qsr_restaurant": "Fast Casual + QSR Restaurants(숙박여가)",
    "hospitality_full_service_specialty_res": "Full-Service + Specialty Restaurants(숙박여가)",
    "hospitality_gambling_casino_operators": "Gambling + Casino Operators(숙박여가)",
    "hospitality_hotels_resorts": "Hotels + Resorts(숙박여가)",
    "hospitality_travel_ota": "Travel + OTA(숙박여가)",
    "household_personal_household_cleaning_paper": "Household Cleaning + Paper(생활용품)",
    "household_personal_japan_cosmetics_skincare": "Japan Cosmetics + Skincare(생활용품)",
    "household_personal_korea_k_beauty": "Korea K-Beauty(생활용품)",
    "household_personal_mass_cosmetics_personal_ca": "Mass Cosmetics + Personal Care(생활용품)",
    "household_personal_oral_care_hygiene": "Oral Care + Hygiene(생활용품)",
    "household_personal_premium_cosmetics": "Premium Cosmetics(생활용품)",
    "insurance_insurance_brokers": "Insurance Brokers(보험)",
    "insurance_life_health": "Life + Health(보험)",
    "insurance_p_c_auto_home": "P&C — Auto + Home(보험)",
    "insurance_p_c_commercial_specialty": "P&C — Commercial + Specialty(보험)",
    "insurance_reinsurance": "Reinsurance(보험)",
    "interactive_media_china_internet_mega_cap": "China Internet Mega-cap(미디어)",
    "interactive_media_dating_matching": "Dating + Matching(미디어)",
    "interactive_media_direct_to_consumer_marketp": "Direct-to-Consumer + Marketplace(미디어)",
    "interactive_media_korea_japan_internet": "Korea + Japan Internet(미디어)",
    "interactive_media_search_advertising": "Search + Advertising(미디어)",
    "interactive_media_social_media": "Social Media(미디어)",
    "ipp_renewable_ess_integrator_storage": "ESS Integrator + Storage(신재생)",
    "ipp_renewable_eu_uk_offshore_wind_develo": "EU/UK Offshore Wind Developer(신재생)",
    "ipp_renewable_geothermal_hydrogen": "Geothermal + Hydrogen(신재생)",
    "ipp_renewable_us_ipp": "US IPP(신재생)",
    "ipp_renewable_us_solar": "US Solar(신재생)",
    "ipp_renewable_wind_turbine_oem": "Wind Turbine OEM(신재생)",
    "it_fintech_buy_now_pay_later_online_l": "Buy-Now-Pay-Later + Online Lending(IT핀테크)",
    "it_fintech_card_networks_payment_rail": "Card Networks + Payment Rails(IT핀테크)",
    "it_fintech_india_it_outsourcing": "India IT Outsourcing(IT핀테크)",
    "it_fintech_it_services_consulting": "IT Services + Consulting(IT핀테크)",
    "it_fintech_payment_processing_fintech": "Payment Processing + Fintech(IT핀테크)",
    "it_fintech_specialty_financial_softwa": "Specialty Financial Software(IT핀테크)",
    "machinery_bearings_power_transmissio": "Bearings + Power Transmission(기계공구)",
    "machinery_diesel_engines_powertrain": "Diesel Engines + Powertrain(기계공구)",
    "machinery_factory_automation": "Factory Automation(기계공구)",
    "machinery_farm_machinery": "Farm Machinery(기계공구)",
    "machinery_general_industrial": "General Industrial(기계공구)",
    "machinery_heavy_construction": "Heavy Construction(기계공구)",
    "medical_equipment_cardiovascular": "Cardiovascular(의료기기)",
    "medical_equipment_dental_vision": "Dental + Vision(의료기기)",
    "medical_equipment_diabetes_care_cgm": "Diabetes Care + CGM(의료기기)",
    "medical_equipment_diagnostic_imaging_lab_too": "Diagnostic Imaging + Lab Tools(의료기기)",
    "medical_equipment_orthopedic_implants": "Orthopedic Implants(의료기기)",
    "medical_equipment_surgical_devices_robotics": "Surgical Devices + Robotics(의료기기)",
    "metals_mining_aluminum": "Aluminum(금속광물)",
    "metals_mining_copper_diversified": "Copper + Diversified(금속광물)",
    "metals_mining_lithium": "Lithium(금속광물)",
    "metals_mining_precious_metals": "Precious Metals(금속광물)",
    "metals_mining_rare_earth": "Rare Earth(금속광물)",
    "metals_mining_steel": "Steel(금속광물)",
    "oil_gas_coal_thermal_metallurgical": "Coal Thermal + Metallurgical(석유가스)",
    "oil_gas_e_p_us_shale_focused": "E&P US Shale Focused(석유가스)",
    "oil_gas_integrated_majors": "Integrated Majors(석유가스)",
    "oil_gas_lng_producers_infra": "LNG Producers + Infra(석유가스)",
    "oil_gas_midstream": "Midstream(석유가스)",
    "oil_gas_refining_marketing": "Refining & Marketing(석유가스)",
    "oil_gas_uranium_mining_enrichment": "Uranium Mining + Enrichment(석유가스)",
    "packaging_glass_specialty": "Glass + Specialty(포장)",
    "packaging_metal_containers_beverage": "Metal Containers + Beverage Cans(포장)",
    "packaging_paper_corrugated_box": "Paper + Corrugated Box(포장)",
    "packaging_plastic_flexible": "Plastic + Flexible(포장)",
    "packaging_sustainable_bio_based_pack": "Sustainable + Bio-based Packaging(포장)",
    "pharma_biotech_adc": "ADC(Pharmaceuticals & Biotechnology)",
    "pharma_biotech_ai_drug_discovery": "AI Drug Discovery(Pharmaceuticals & Biotechnology)",
    "pharma_biotech_biotechnology": "Biotechnology(Pharmaceuticals & Biotechnology)",
    "pharma_biotech_cell_gene_therapy": "Cell & Gene Therapy(Pharmaceuticals & Biotechnology)",
    "pharma_biotech_major_pharmaceuticals": "Major Pharmaceuticals(Pharmaceuticals & Biotechnology)",
    "pharma_biotech_specialty_generic_pharma": "Specialty & Generic Pharma(Pharmaceuticals & Biotechnology)",
    "real_estate_services_commercial_real_estate_bro": "Commercial Real Estate Brokerage(부동산서비스)",
    "real_estate_services_property_listings_data": "Property Listings + Data(부동산서비스)",
    "real_estate_services_property_management_tech": "Property Management + Tech(부동산서비스)",
    "real_estate_services_residential_brokerage_mort": "Residential Brokerage + Mortgage(부동산서비스)",
    "reits_data_center_reits": "Data Center REITs(REIT)",
    "reits_healthcare_reits": "Healthcare REITs(REIT)",
    "reits_industrial_reits": "Industrial REITs(REIT)",
    "reits_mortgage_reits": "Mortgage REITs(REIT)",
    "reits_office_reits": "Office REITs(REIT)",
    "reits_residential_reits": "Residential REITs(REIT)",
    "reits_retail_reits": "Retail REITs(REIT)",
    "reits_specialty_reits": "Specialty REITs(REIT)",
    "retail_beauty_personal_care": "Beauty + Personal Care(소매)",
    "retail_broadline_retail_discounte": "Broadline Retail + Discounters(소매)",
    "retail_e_commerce_direct_marketin": "E-commerce + Direct Marketing(소매)",
    "retail_home_improvement": "Home Improvement(소매)",
    "retail_specialty_retail": "Specialty Retail(소매)",
    "semiconductors_analog": "Analog·Power·IDM(아날로그·전력·마이크로컨트롤러)",
    "semiconductors_eda": "EDA & Semiconductor IP(설계 자동화·반도체 IP)",
    "semiconductors_equipment": "Semiconductor Equipment & Materials(반도체 장비·소재)",
    "semiconductors_foundry": "Foundry(파운드리 — 반도체 위탁생산)",
    "semiconductors_logic_ai": "AI·Datacenter Logic(AI·데이터센터 로직 반도체)",
    "semiconductors_logic_mobile": "Mobile·Consumer Logic(모바일·소비자 로직 반도체)",
    "semiconductors_memory": "Memory Semiconductors(메모리 반도체 — DRAM·HBM·NAND)",
    "semiconductors_specialty": "Specialty·Compound Semi(특수·화합물 반도체 SiC·GaN)",
    "software_application_crm_erp": "Application — CRM/ERP(소프트웨어)",
    "software_application_productivity_a": "Application — Productivity + AI Agent(소프트웨어)",
    "software_cloud_database": "Cloud + Database(소프트웨어)",
    "software_cybersecurity": "Cybersecurity(소프트웨어)",
    "software_enterprise_vertical_saas": "Enterprise Vertical SaaS(소프트웨어)",
    "software_observability_devops": "Observability + DevOps(소프트웨어)",
    "telecom_asia_4_mega_tier": "Asia 4 Mega-tier(Telecommunication Services)",
    "telecom_communication_infrastructu": "Communication Infrastructure REIT(Telecommunication Services)",
    "telecom_eu_integrated": "EU Integrated(Telecommunication Services)",
    "telecom_korea_specialty_telecom": "Korea + Specialty Telecom(Telecommunication Services)",
    "telecom_us_cable_broadband": "US Cable + Broadband(Telecommunication Services)",
    "telecom_us_integrated_wireless": "US Integrated Wireless(Telecommunication Services)",
    "tobacco_eu_uk": "EU/UK(Tobacco)",
    "tobacco_international": "International(Tobacco)",
    "tobacco_japan": "Japan(Tobacco)",
    "tobacco_korea_asia": "Korea + Asia(Tobacco)",
    "tobacco_us_domestic": "US Domestic(Tobacco)",
    "tobacco_vape_nicotine_pouch": "차세대 Vape + Nicotine Pouch(Tobacco)",
    "transport_logistics_air_freight_logistics": "Air Freight & Logistics(Transportation & Logistics)",
    "transport_logistics_marine_shipping": "Marine Shipping(Transportation & Logistics)",
    "transport_logistics_railroads": "Railroads(Transportation & Logistics)",
    "transport_logistics_ride_share_last_mile": "Ride-share / Last-mile(Transportation & Logistics)",
    "transport_logistics_trucking_ground": "Trucking & Ground(Transportation & Logistics)",
    "waste_management_environmental_consulting_r": "Environmental Consulting + Remediation(Waste & Environmental Services)",
    "waste_management_hazardous_waste_industrial": "Hazardous Waste + Industrial Cleaning(Waste & Environmental Services)",
    "waste_management_recycling_recovery": "Recycling + Recovery(Waste & Environmental Services)",
    "waste_management_solid_waste_collection": "Solid Waste Collection(Waste & Environmental Services)",
    "waste_management_water_wastewater_treatment": "Water/Wastewater Treatment(Waste & Environmental Services)",
}

def _validate(slug: str, theme: dict) -> None:
    """Fail-fast import-time check. Catches typos in dict keys before they
    cost 3 minutes of Pro latency. Layer taxonomy lower bound is 3 so the
    Pro has enough niche signal to surface 4-6 sub-themes."""
    for k in _REQUIRED_KEYS:
        if k not in theme:
            raise ValueError(f"theme '{slug}' missing required key: {k}")
    # Min 2 layers (Energy + Real Estate L2 in user's official taxonomy
    # each have only 2 L3 sub-industries — Oil/Gas + Energy Equipment;
    # Real Estate Services + REITs). 2026-05-29 user taxonomy update
    # relaxed from min 3 to accommodate the official sector structure.
    if not isinstance(theme["binding_layer_taxonomy"], list) or len(theme["binding_layer_taxonomy"]) < 2:
        raise ValueError(f"theme '{slug}' binding_layer_taxonomy must be a list of ≥2 layers")
    if not isinstance(theme["catalyst_types"], list) or len(theme["catalyst_types"]) < 3:
        raise ValueError(f"theme '{slug}' catalyst_types must be a list of ≥3 catalysts")
    # Min 2 SPOF entries — Energy/Real Estate L2 each have 2 L3 sub-
    # industries and a matching 2-entry regional map. Relaxed alongside
    # binding_layer_taxonomy 2026-05-29.
    if not isinstance(theme["regional_concentration"], dict) or len(theme["regional_concentration"]) < 2:
        raise ValueError(f"theme '{slug}' regional_concentration must be a dict of ≥2 SPOF entries")
    layer = theme.get("layer")
    if layer is not None and layer not in _VALID_LAYERS:
        raise ValueError(
            f"theme '{slug}' layer={layer!r} not in {_VALID_LAYERS}"
        )


def _discover() -> None:
    """Walk the package, import each submodule, register its THEME dict.
    Idempotent — re-running on hot-reload is safe (dict overwrite).
    Submodules without a top-level THEME are skipped (the registry helpers
    themselves live alongside theme files)."""
    if _THEMES:
        return  # already discovered
    pkg = importlib.import_module(__name__)
    for finder, mod_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if is_pkg or mod_name.startswith("_"):
            continue
        full_name = f"{__name__}.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:
            log.exception("screener_themes: failed to import %s: %s", full_name, exc)
            continue
        theme = getattr(mod, "THEME", None)
        if not isinstance(theme, dict):
            continue
        slug = mod_name.lower()
        try:
            _validate(slug, theme)
        except ValueError as exc:
            log.error("screener_themes: skipping invalid theme '%s': %s", slug, exc)
            continue
        _THEMES[slug] = theme
        _ALIAS_TO_SLUG[slug] = slug
        for alias in theme.get("aliases", []) or []:
            a = str(alias).strip().lower()
            if a and a not in _ALIAS_TO_SLUG:
                _ALIAS_TO_SLUG[a] = slug
    log.info("screener_themes: discovered %d themes — %s",
             len(_THEMES), ", ".join(sorted(_THEMES)))


def resolve(name: str) -> Optional[dict]:
    """Look up a theme by slug or alias (case-insensitive). Returns the
    theme dict or None when no theme matches. Substring fuzzy match is
    deliberately conservative — two ambiguous hits return None so the
    user gets an error + domain list rather than a wrong-domain run."""
    _discover()
    if not name:
        name = "bottleneck"
    key = name.strip().lower()
    slug = _ALIAS_TO_SLUG.get(key)
    if slug:
        return _THEMES.get(slug)
    # Conservative substring fallback — only succeeds when exactly one
    # alias/slug contains the query.
    hits = [s for k, s in _ALIAS_TO_SLUG.items() if key in k]
    uniq = set(hits)
    if len(uniq) == 1:
        return _THEMES.get(next(iter(uniq)))
    return None


def resolve_slug(name: str) -> Optional[str]:
    """Same resolution rules as ``resolve()`` but returns the canonical
    slug instead of the theme dict. Used by ``bot.screener_cache`` so
    alias inputs ('/screener AI 데이터센터', '/screener ai_datacenter') 가
    같은 cache 행 (bottleneck) 으로 collapse 된다."""
    _discover()
    if not name:
        name = "bottleneck"
    key = name.strip().lower()
    slug = _ALIAS_TO_SLUG.get(key)
    if slug:
        return slug
    hits = [s for k, s in _ALIAS_TO_SLUG.items() if key in k]
    uniq = set(hits)
    if len(uniq) == 1:
        return next(iter(uniq))
    return None


def list_domains() -> list[dict]:
    """Return a list of ``{"slug", "domain", "aliases"}`` for each
    registered theme, sorted by slug. Used by /screener error messages
    and by _HELP_TEXT generation to keep the public domain list synced
    with the registry without hand-editing."""
    _discover()
    out = []
    for slug in sorted(_THEMES):
        theme = _THEMES[slug]
        domain = theme.get("domain", slug)
        if theme.get("layer") == "L4_SUBINDUSTRY":
            domain = _L4_DOMAIN_KO.get(slug, domain)
        out.append({
            "slug": slug,
            "domain": domain,
            "aliases": list(theme.get("aliases", []) or []),
            "layer": theme.get("layer", "L1_TREND"),
        })
    return out


def available_summary() -> str:
    """One-line user-facing summary of registered domains for error
    messages: '/screener {bottleneck | ev | defense | pharma | solar}'."""
    _discover()
    return "/screener {" + " | ".join(sorted(_THEMES)) + "}"
