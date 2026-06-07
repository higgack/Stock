"""Dashboard text → 한국어 번역 (정적 사전 + Gemini Flash 영구 캐시).

정적 사전: 섹터/산업/국가/리서치 등급·액션 — ₩0.
Flash 번역: 기업 설명·뉴스 제목 — 영구 디스크 캐시로 텍스트당 1회만 번역.
chart_translate.py 와 동일 패턴(캐시 파일만 분리).
"""
from __future__ import annotations

import hashlib
import json
import os
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


def translate_description_kr(desc: str) -> str:
    """Translate a company description to Korean. Permanent cache.

    Returns Korean text, or original on failure/key-absent."""
    if not desc or not desc.strip():
        return desc
    key = _cache_key(desc)
    cache = _load()
    if key in cache:
        return cache[key]
    api_key = os.environ.get("GOOGLE_API_KEY")
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
    api_key = os.environ.get("GOOGLE_API_KEY")
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
