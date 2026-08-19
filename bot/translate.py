"""Dashboard text → 한국어 번역 (정적 사전 + Gemini Flash 영구 캐시).

정적 사전: 섹터/산업/국가/리서치 등급·액션 — ₩0.
Flash 번역: 기업 설명·뉴스 제목 — 영구 디스크 캐시로 텍스트당 1회만 번역.
chart_translate.py 와 동일 패턴(캐시 파일만 분리).
"""
from __future__ import annotations

import hashlib
import json
import os
from bot.genai_factory import effective_key as _effective_key
import re
import time
from pathlib import Path

_HOME = Path.home() / ".tradingagents"
_CACHE = _HOME / "translate_kr.json"
_USAGE = _HOME / "usage.jsonl"
_USD_TO_KRW = 1330.0
_FLASH_IN, _FLASH_OUT = 0.30, 2.50
_MAX_BATCH = 20


# ── static dictionaries ────────────────────────────────────────────

_SECTOR_KR: dict[str, str] = {
    "Technology": "기술",
    "Healthcare": "헬스케어",
    "Financial Services": "금융",
    "Communication Services": "커뮤니케이션 서비스",
    "Consumer Cyclical": "경기소비재",
    "Consumer Defensive": "필수소비재",
    "Industrials": "산업재",
    "Energy": "에너지",
    "Basic Materials": "소재",
    "Real Estate": "부동산",
    "Utilities": "유틸리티",
}

_INDUSTRY_KR: dict[str, str] = {
    "Information Technology Services": "IT 서비스",
    "Software - Infrastructure": "소프트웨어 - 인프라",
    "Software - Application": "소프트웨어 - 애플리케이션",
    "Semiconductors": "반도체",
    "Semiconductor Equipment & Materials": "반도체 장비·소재",
    "Consumer Electronics": "가전",
    "Electronic Components": "전자부품",
    "Computer Hardware": "컴퓨터 하드웨어",
    "Scientific & Technical Instruments": "과학·기술 장비",
    "Communication Equipment": "통신장비",
    "Internet Content & Information": "인터넷 콘텐츠·정보",
    "Internet Retail": "인터넷 리테일",
    "Electronic Gaming & Multimedia": "게임·멀티미디어",
    "Entertainment": "엔터테인먼트",
    "Telecom Services": "통신 서비스",
    "Advertising Agencies": "광고",
    "Publishing": "출판",
    "Broadcasting": "방송",
    "Banks - Diversified": "종합 은행",
    "Banks - Regional": "지방 은행",
    "Capital Markets": "자본시장",
    "Insurance - Diversified": "종합 보험",
    "Insurance - Life": "생명보험",
    "Insurance - Property & Casualty": "손해보험",
    "Financial Data & Stock Exchanges": "금융 데이터·거래소",
    "Asset Management": "자산운용",
    "Credit Services": "신용·결제",
    "Insurance Brokers": "보험 중개",
    "Mortgage Finance": "주택금융",
    "Drug Manufacturers - General": "제약 - 대형",
    "Drug Manufacturers - Specialty & Generic": "제약 - 전문·제네릭",
    "Biotechnology": "바이오",
    "Medical Devices": "의료기기",
    "Medical Instruments & Supplies": "의료장비·용품",
    "Health Information Services": "의료 정보 서비스",
    "Medical Care Facilities": "의료시설",
    "Diagnostics & Research": "진단·연구",
    "Pharmaceutical Retailers": "약국·유통",
    "Healthcare Plans": "건강보험",
    "Auto Manufacturers": "자동차 제조",
    "Auto Parts": "자동차 부품",
    "Restaurants": "외식",
    "Travel Services": "여행",
    "Lodging": "숙박",
    "Resorts & Casinos": "리조트·카지노",
    "Apparel Manufacturing": "의류 제조",
    "Apparel Retail": "의류 유통",
    "Luxury Goods": "명품",
    "Home Improvement Retail": "홈 인테리어",
    "Specialty Retail": "전문 소매",
    "Department Stores": "백화점",
    "Discount Stores": "할인점",
    "Residential Construction": "주택 건설",
    "Leisure": "레저",
    "Gambling": "도박",
    "Footwear & Accessories": "신발·액세서리",
    "Packaging & Containers": "포장·용기",
    "Household & Personal Products": "생활용품",
    "Beverages - Non-Alcoholic": "음료 - 비알콜",
    "Beverages - Alcoholic": "음료 - 알콜",
    "Beverages - Brewers": "맥주",
    "Beverages - Wineries & Distilleries": "와인·주류",
    "Packaged Foods": "식품",
    "Food Distribution": "식품 유통",
    "Farm Products": "농산물",
    "Confectioners": "제과",
    "Grocery Stores": "식료품점",
    "Tobacco": "담배",
    "Education & Training Services": "교육",
    "Aerospace & Defense": "항공우주·방산",
    "Airlines": "항공사",
    "Railroads": "철도",
    "Trucking": "화물운송",
    "Marine Shipping": "해운",
    "Integrated Freight & Logistics": "물류",
    "Waste Management": "폐기물 관리",
    # yfinance 업종 중 미번역으로 화면에 영문이 그대로 뜨던 것들
    # (사용자 2026-08-19 JP/CN/HK/TW 신고저·급등락 스크린샷에서 확인).
    "Auto & Truck Dealerships": "자동차 딜러",
    "Airports & Air Services": "공항·항공 서비스",
    "Electronics & Computer Distribution": "전자·컴퓨터 유통",
    "Furnishings, Fixtures & Appliances": "가구·인테리어·가전",
    "Infrastructure Operations": "인프라 운영",
    "Insurance - Reinsurance": "재보험",
    "Insurance - Specialty": "특종보험",
    "Medical Distribution": "의약품 유통",
    "Metal Fabrication": "금속 가공",
    "Oil & Gas Drilling": "유전 시추",
    "Personal Services": "개인 서비스",
    "Pollution & Treatment Controls": "환경·오염방지 설비",
    "Recreational Vehicles": "레저용 차량",
    "Specialty Business Services": "전문 기업서비스",
    "Textile Manufacturing": "섬유 제조",
    "Tools & Accessories": "공구·액세서리",
    "Industrial Distribution": "산업재 유통",
    "Specialty Industrial Machinery": "전문 산업기계",
    "Farm & Heavy Construction Machinery": "농기계·건설장비",
    "Electrical Equipment & Parts": "전기장비·부품",
    "Building Products & Equipment": "건축자재·장비",
    "Engineering & Construction": "엔지니어링·건설",
    "Conglomerates": "복합기업",
    "Rental & Leasing Services": "임대·리스",
    "Security & Protection Services": "보안",
    "Staffing & Employment Services": "인재·고용",
    "Consulting Services": "컨설팅",
    "Business Equipment & Supplies": "사무장비",
    "Oil & Gas Integrated": "석유·가스 종합",
    "Oil & Gas E&P": "석유·가스 시추·생산",
    "Oil & Gas Midstream": "석유·가스 미드스트림",
    "Oil & Gas Refining & Marketing": "석유·가스 정제·유통",
    "Oil & Gas Equipment & Services": "석유·가스 장비·서비스",
    "Uranium": "우라늄",
    "Thermal Coal": "석탄",
    "Solar": "태양광",
    "Chemicals": "화학",
    "Specialty Chemicals": "특수 화학",
    "Agricultural Inputs": "농업 투입재",
    "Building Materials": "건축 소재",
    "Steel": "철강",
    "Aluminum": "알루미늄",
    "Copper": "구리",
    "Gold": "금",
    "Silver": "은",
    "Other Industrial Metals & Mining": "산업 금속·광업",
    "Other Precious Metals & Mining": "귀금속·광업",
    "Coking Coal": "코크스탄",
    "Lumber & Wood Production": "목재",
    "Paper & Paper Products": "제지",
    "REIT - Diversified": "리츠 - 종합",
    "REIT - Industrial": "리츠 - 산업",
    "REIT - Office": "리츠 - 오피스",
    "REIT - Residential": "리츠 - 주거",
    "REIT - Retail": "리츠 - 리테일",
    "REIT - Healthcare Facilities": "리츠 - 의료",
    "REIT - Hotel & Motel": "리츠 - 호텔",
    "REIT - Specialty": "리츠 - 특수",
    "REIT - Mortgage": "리츠 - 모기지",
    "Real Estate Services": "부동산 서비스",
    "Real Estate - Development": "부동산 개발",
    "Real Estate - Diversified": "부동산 - 종합",
    "Utilities - Regulated Electric": "전력 - 규제",
    "Utilities - Diversified": "유틸리티 - 종합",
    "Utilities - Regulated Gas": "가스 - 규제",
    "Utilities - Regulated Water": "수도",
    "Utilities - Independent Power Producers": "독립발전",
    "Utilities - Renewable": "신재생 전력",
    "Shell Companies": "페이퍼 컴퍼니",
    "Financial Conglomerates": "금융 복합기업",

    # ── NASDAQ screener 어휘(미국 신고저·급등락·장전장후) ──────────────
    # ⚠️ 위쪽은 yfinance/GICS 계열("Software - Infrastructure")인데, 미국
    # 페이지의 업종은 **NASDAQ screener** 분류라 어휘가 통째로 다르다
    # ("EDP Services"·"Biotechnology: Pharmaceutical Preparations").
    # 그래서 한국·일본 화면은 한글인데 미국만 영문이었다(사용자 2026-08-19).
    # 여기에는 **화면에서 전체 문자열을 확인한 것만** 넣는다 — 잘린 라벨을
    # 추측해 넣으면 엉뚱한 번역이 굳는다. 나머지는
    # `bot.scripts.industry_kr_probe` 가 미번역 값을 빈도순으로 뽑아 준다.
    "Pharmaceuticals": "제약",
    "Other Pharmaceuticals": "기타 제약",
    "Major Banks": "대형은행",
    "Commercial Banks": "상업은행",
    "Savings Institutions": "저축은행",
    "Finance: Consumer Services": "금융: 소비자 서비스",
    "Investment Managers": "자산운용",
    "Biotechnology: Pharmaceutical Preparations": "바이오·제약(제제)",
    "Biotechnology: Biological Products (No Diagnostic Substances)":
        "바이오 의약품(진단시약 제외)",
    "Biotechnology: Laboratory Analytical Instruments": "바이오 분석장비",
    "Biotechnology: In Vitro & In Vivo Diagnostic Substances": "체외·체내 진단시약",
    "Biotechnology: Commercial Physical & Biological Resarch": "바이오 수탁연구",
    "Medical/Dental Instruments": "의료·치과 기기",
    "Medical/Nursing Services": "의료·요양 서비스",
    "Medical Specialities": "의료 전문",
    "Hospital/Nursing Management": "병원·요양 운영",
    "Oil & Gas Production": "석유·가스 생산",
    "Oilfield Services/Equipment": "유전 서비스·장비",
    "Integrated oil Companies": "종합 석유",
    "Natural Gas Distribution": "천연가스 유통",
    "Marine Transportation": "해운",
    "Transportation Services": "운송 서비스",
    "Trucking Freight/Courier Services": "육상운송·택배",
    "Air Freight/Delivery Services": "항공화물·배송",
    "EDP Services": "전산(EDP) 서비스",
    "Computer Software: Prepackaged Software": "소프트웨어(패키지)",
    "Computer Software: Programming Data Processing": "소프트웨어(프로그래밍·데이터처리)",
    "Computer Manufacturing": "컴퓨터 제조",
    "Computer peripheral equipment": "컴퓨터 주변기기",
    "Telecommunications Equipment": "통신장비",
    "Industrial Machinery/Components": "산업기계·부품",
    "Metal Fabrications": "금속 가공품",
    "Building Products": "건축자재",
    "Building operators": "건물 운영",
    "Homebuilding": "주택건설",
    "Beverages (Production/Distribution)": "음료(생산·유통)",
    "Auto Parts:O.E.M.": "자동차 부품(OEM)",
    "Auto Manufacturing": "자동차 제조",
    "Automotive Aftermarket": "자동차 애프터마켓",
    "Durable Goods": "내구재",
    "Consumer Electronics/Appliances": "가전·소비자 전자",
    "Consumer Specialties": "소비재 전문",
    "Other Consumer Services": "기타 소비자 서비스",
    "Recreational Games/Products/Toys": "완구·게임용품",
    "Services-Misc. Amusement & Recreation": "오락·레저 서비스",
    "Real Estate": "부동산",
    "Real Estate Investment Trusts": "리츠(부동산투자신탁)",
    "Professional Services": "전문 서비스",
    "Environmental Services": "환경 서비스",
    "Pollution Control Equipment": "환경·오염방지 설비",
    "Military/Government/Technical": "방산·정부·기술",
    "Miscellaneous": "기타",
    "Major Chemicals": "화학",
    "Containers/Packaging": "포장·용기",
    "Precious Metals": "귀금속",
    "Steel/Iron Ore": "철강·철광",
    "Electric Utilities Central": "전력 유틸리티",
    "Apparel": "의류",
    "Clothing/Shoe/Accessory Stores": "의류·신발·잡화 소매",
    "Department/Specialty Retail Stores": "백화점·전문 소매",
    "Food Distributors": "식품 유통",
    "Farming/Seeds/Milling": "농업·종자·제분",
    "Package Goods/Cosmetics": "생활용품·화장품",
    "Advertising": "광고",
    "Aerospace": "항공우주",
    "Marine Transportation Services": "해운 서비스",
    "Ordnance And Accessories": "무기·부속",

    # ── NASDAQ 어휘 2차(프로브 실측 빈도순) ──
    # `industry_kr_probe` 가 원천 캐시에서 **전체 문자열**을 세어 준
    # 미번역 목록 그대로(2026-08-19: 6,365종목 중 한글화 70.6%).
    # 추측이 0 이라 잘린 라벨을 잘못 옮길 위험이 없다.
    "Blank Checks": "스팩(SPAC)",
    "Trusts Except Educational Religious and Charitable": "신탁(교육·종교·자선 제외)",
    "Business Services": "기업 서비스",
    "Finance/Investors Services": "금융·투자 서비스",
    "Property-Casualty Insurers": "손해보험",
    "Electric Utilities: Central": "전력 유틸리티",
    "Investment Bankers/Brokers/Service": "투자은행·중개",
    "Finance Companies": "여신전문금융",
    "Industrial Specialties": "산업 특수품",
    "Metal Mining": "금속 광업",
    "Other Specialty Stores": "기타 전문 소매",
    "Diversified Commercial Services": "종합 기업서비스",
    "Hotels/Resorts": "호텔·리조트",
    "Specialty Insurers": "특종보험",
    "Electrical Products": "전기제품",
    "Life Insurance": "생명보험",
    "Catalog/Specialty Distribution": "카탈로그·전문 유통",
    "Biotechnology: Electromedical & Electrotherapeutic Apparatus":
        "의료용 전자·전기치료 기기",
    "Medicinal Chemicals and Botanical Products": "원료의약품·식물성 제제",
    "Mining & Quarrying of Nonmetallic Minerals (No Fuels)":
        "비금속 광물 채굴(연료 제외)",
    "Power Generation": "발전",
    "Banks": "은행",
    "Radio And Television Broadcasting And Communications Equipment":
        "방송·통신 장비",
    "Home Furnishings": "가구·홈퍼니싱",
    "Retail-Auto Dealers and Gas Stations": "자동차 딜러·주유소",
    "Cable & Other Pay Television Services": "케이블·유료방송",
    "Computer Communications Equipment": "컴퓨터 통신장비",
    "RETAIL: Building Materials": "건축자재 소매",
    "Construction/Ag Equipment/Trucks": "건설·농기계·트럭",
    "Oil and Gas Field Machinery": "유전·가스전 기계",
    "Movies/Entertainment": "영화·엔터테인먼트",
    "Oil/Gas Transmission": "석유·가스 수송",
    "Food Chains": "식품 소매체인",
    "Water Supply": "수도",
    "Multi-Sector Companies": "복합기업",
    "Other Metals and Minerals": "기타 금속·광물",
    "Coal Mining": "석탄 채굴",
    "Agricultural Chemicals": "농약·비료",
    "Office Equipment/Supplies/Services": "사무기기·용품·서비스",
    "Miscellaneous manufacturing industries": "기타 제조업",
    # ── GICS sub-industry(S&P500 경로) ──
    # 같은 프로브의 두 번째 소스 — 한글화가 20.9% 뿐이었다.
    # 위 NASDAQ 어휘와 **다른 계열**이라 키가 겹치지 않는다.
    "Health Care Equipment": "의료기기",
    "Electric Utilities": "전력",
    "Application Software": "애플리케이션 소프트웨어",
    "Industrial Machinery & Supplies & Components": "산업기계·부품",
    "Multi-Utilities": "복합 유틸리티",
    "Asset Management & Custody Banks": "자산운용·수탁",
    "Oil & Gas Exploration & Production": "석유·가스 탐사·생산",
    "Technology Hardware, Storage & Peripherals": "IT 하드웨어·스토리지·주변기기",
    "Transaction & Payment Processing Services": "결제·거래처리",
    "Financial Exchanges & Data": "거래소·금융데이터",
    "Life Sciences Tools & Services": "생명과학 도구·서비스",
    "Hotels, Resorts & Cruise Lines": "호텔·리조트·크루즈",
    "Property & Casualty Insurance": "손해보험",
    "Packaged Foods & Meats": "가공식품·육류",
    "Diversified Banks": "종합은행",
    "Communications Equipment": "통신장비",
    "Multi-Family Residential REITs": "다세대 주거 리츠",
    "Investment Banking & Brokerage": "투자은행·중개",
    "Regional Banks": "지역은행",
    "Systems Software": "시스템 소프트웨어",
    "Life & Health Insurance": "생명·건강보험",
    "Paper & Plastic Packaging Products & Materials": "제지·플라스틱 포장재",
    "Electrical Components & Equipment": "전기부품·장비",
    "Semiconductor Materials & Equipment": "반도체 소재·장비",
    "Health Care Services": "헬스케어 서비스",
    "Consumer Staples Merchandise Retail": "생활필수품 소매",
    "Retail REITs": "리테일 리츠",
    "Movies & Entertainment": "영화·엔터테인먼트",
    "IT Consulting & Other Services": "IT 컨설팅·서비스",
    "Air Freight & Logistics": "항공화물·물류",
    "Health Care Distributors": "의약품 유통",
    "Construction Machinery & Heavy Transportation Equipment": "건설기계·대형운송장비",
    "Managed Health Care": "관리의료(보험)",
    "Household Products": "생활용품",
    "Soft Drinks & Non-alcoholic Beverages": "음료(비알코올)",
    "Construction & Engineering": "건설·엔지니어링",
    "Electronic Equipment & Instruments": "전자장비·계측기",
    "Oil & Gas Storage & Transportation": "석유·가스 저장·수송",
    "Apparel, Accessories & Luxury Goods": "의류·액세서리·명품",
    "Environmental & Facilities Services": "환경·시설 서비스",
}

_COUNTRY_KR: dict[str, str] = {
    "United States": "미국", "China": "중국", "Japan": "일본",
    "South Korea": "한국", "Taiwan": "대만", "Hong Kong": "홍콩",
    "United Kingdom": "영국", "Germany": "독일", "France": "프랑스",
    "Canada": "캐나다", "Australia": "호주", "India": "인도",
    "Brazil": "브라질", "Switzerland": "스위스", "Netherlands": "네덜란드",
    "Sweden": "스웨덴", "Norway": "노르웨이", "Denmark": "덴마크",
    "Finland": "핀란드", "Ireland": "아일랜드", "Italy": "이탈리아",
    "Spain": "스페인", "Belgium": "벨기에", "Israel": "이스라엘",
    "Singapore": "싱가포르", "Mexico": "멕시코", "South Africa": "남아공",
    "Russia": "러시아", "Indonesia": "인도네시아", "Malaysia": "말레이시아",
    "Thailand": "태국", "Philippines": "필리핀", "Vietnam": "베트남",
    "New Zealand": "뉴질랜드", "Luxembourg": "룩셈부르크",
    "Bermuda": "버뮤다", "Cayman Islands": "케이맨제도",
    "Netherlands Antilles": "네덜란드령 안틸레스",
    "Puerto Rico": "푸에르토리코", "Argentina": "아르헨티나",
    "Chile": "칠레", "Colombia": "콜롬비아", "Peru": "페루",
    "Portugal": "포르투갈", "Austria": "오스트리아", "Poland": "폴란드",
    "Czech Republic": "체코", "Hungary": "헝가리", "Greece": "그리스",
    "Turkey": "튀르키예", "Saudi Arabia": "사우디아라비아",
    "United Arab Emirates": "UAE", "Qatar": "카타르", "Kuwait": "쿠웨이트",
    "Egypt": "이집트", "Nigeria": "나이지리아", "Kenya": "케냐",
}

_GRADE_KR: dict[str, str] = {
    "Buy": "매수", "Strong Buy": "강력 매수", "Outperform": "시장 상회",
    "Overweight": "비중확대", "Market Outperform": "시장 상회",
    "Sector Outperform": "섹터 상회", "Positive": "긍정",
    "Top Pick": "최선호", "Long-Term Buy": "장기 매수",
    "Hold": "보유", "Neutral": "중립", "Equal-Weight": "동일비중",
    "Market Perform": "시장 수준", "Sector Perform": "섹터 수준",
    "In-Line": "시장 수준", "Peer Perform": "동종 수준",
    "Mixed": "혼합", "Sector Weight": "섹터비중",
    "Sell": "매도", "Strong Sell": "강력 매도", "Underperform": "시장 하회",
    "Underweight": "비중축소", "Market Underperform": "시장 하회",
    "Sector Underperform": "섹터 하회", "Negative": "부정", "Reduce": "축소",
}

_ACTION_KR: dict[str, str] = {
    "main": "신규", "up": "상향", "down": "하향", "init": "개시",
    "reit": "유지",
    "Upgrade": "상향", "Downgrade": "하향", "Initiated": "커버리지 개시",
    "Reiterated": "유지", "Maintained": "유지", "Reinstated": "재개시",
    "Resumed": "재개", "Suspended": "중단",
}


def sector_kr(s: str) -> str:
    return _SECTOR_KR.get(s, s)

def industry_kr(s: str) -> str:
    return _INDUSTRY_KR.get(s, s)

def country_kr(s: str) -> str:
    return _COUNTRY_KR.get(s, s)

def grade_kr(g: str) -> str:
    return _GRADE_KR.get(g, g)

def action_kr(a: str) -> str:
    return _ACTION_KR.get(a, a)


# ── Flash translation (permanent cache) ────────────────────────────

def _load() -> dict:
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(d: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _CACHE)
    except OSError:
        pass


def _log_usage(pt: int, ot: int) -> None:
    try:
        cost_usd = (pt * _FLASH_IN + ot * _FLASH_OUT) / 1e6
        with open(_USAGE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "type": "llm_call",
                                "model": "gemini-2.5-flash",
                                "prompt_tokens": pt, "completion_tokens": ot,
                                "cost_usd": round(cost_usd, 6),
                                "subsystem": "dashboard_translate"}) + "\n")
    except OSError:
        pass


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


_CACHE_ONLY = False


def set_cache_only(val: bool) -> None:
    """When True, translation functions return cached results only (no API)."""
    global _CACHE_ONLY
    _CACHE_ONLY = val


def translate_description_kr(desc: str) -> str:
    """Translate a company description to Korean. Permanent cache.

    Returns Korean text, or original on failure/key-absent."""
    if not desc or not desc.strip():
        return desc
    key = _cache_key(desc)
    cache = _load()
    if key in cache:
        return cache[key]
    if _CACHE_ONLY:
        return desc
    api_key = _effective_key()
    if not api_key:
        return desc
    prompt = (
        "다음 영문 기업 소개를 자연스러운 한국어로 번역하세요.\n"
        "- 고유명사(회사명·제품명·인명)는 영문 그대로 유지\n"
        "- 간결하고 자연스러운 문어체\n"
        "- 번역문만 출력, 설명·서문 금지\n\n" + desc[:3000])
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                  enable_grounding=False)
        _log_usage(pt, ot)
        if text and text.strip():
            kr = text.strip()
            cache[key] = kr
            _save(cache)
            return kr
    except Exception:
        pass
    return desc


def translate_news_titles_kr(titles: list[str]) -> dict[str, str]:
    """Batch translate news titles to Korean. Permanent cache.

    Returns {original: korean}. Missing titles keep original."""
    uniq = [t for t in dict.fromkeys(titles) if t and t.strip()]
    if not uniq:
        return {}
    cache = _load()
    out: dict[str, str] = {}
    todo: list[str] = []
    for t in uniq:
        k = _cache_key(t)
        if k in cache:
            out[t] = cache[k]
        else:
            todo.append(t)
    todo = todo[:_MAX_BATCH]
    if not todo:
        return out
    if _CACHE_ONLY:
        return out
    api_key = _effective_key()
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = (
        "다음 금융 뉴스 제목들을 자연스러운 한국어로 번역하세요.\n"
        "- 회사명·제품명·인명은 영문 그대로 유지\n"
        "- 번역문만, '번호. 번역' 형식으로 같은 번호 유지\n"
        "- 군더더기 설명 금지\n\n" + lines)
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                  enable_grounding=False)
        _log_usage(pt, ot)
        for line in (text or "").splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            kr = m.group(2).strip()
            if 0 <= idx < len(todo) and kr:
                out[todo[idx]] = kr
                cache[_cache_key(todo[idx])] = kr
        _save(cache)
    except Exception:
        pass
    return out
