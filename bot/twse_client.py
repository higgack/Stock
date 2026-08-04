"""TWSE(대만증권거래소) 시세 — 업종(類股) 등락 + 상한가/하한가 (사용자
2026-06-13, VM 검증 200 OK). 무료·무키 공개 JSON.

`MI_INDEX?response=json&type=ALL&date=YYYYMMDD` 한 응답에:
  • 類股指數 테이블 — ~30 업종 등락(섹터 ETF 4개보다 풍부) → 메인 위젯 업그레이드
  • 每日收盤行情(全部) 테이블 — 전종목 종가·등락 → 상한가/하한가(TW ±10%)

방어적 파싱: 테이블 순서/포맷 변형에 견디게 **필드 시그니처로 스캔**(고정
인덱스 의존 X) + 신·구 포맷(tables / dataN) 둘 다 + 실패 시 빈 결과(graceful,
위젯 자동 생략). 라이브 구조 검증: `python -m bot.twse_client --probe`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
# 상한가/하한가 = OpenAPI STOCK_DAY_ALL (전종목 일일, 평평한 JSON 배열).
# legacy MI_INDEX type=ALL 은 매일 13:30~13:45(TW) 점검으로 type=ALL 만 중단
# 되므로(stat 메시지), 점검 무관한 별도 OpenAPI 호스트를 쓴다. 업종(類股)은
# OpenAPI 동등물이 없어 legacy MI_INDEX 를 best-effort 로 유지(실패 시 ETF 폴백).
_OPENAPI_STOCK = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
# 上櫃(TPEx) 전종목 일일 — TWSE STOCK_DAY_ALL 의 上櫃 등가물(T9 2026-06-16, VM
# probe 로 필드 확인: SecuritiesCompanyCode/CompanyName/Close/Change/TradingShares/
# TransactionAmount, Date=ROC 7자리). parse_stock_day_all 이 두 필드셋 공용 처리.
_OPENAPI_TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
# 상장법인 기본자료(MOPS 공시 미러, 사용자 2026-08-04 '대만 급등락 업종 —
# 캐싱구조 한계 조사해줘') — 종목별 産業別(업종) 포함 **전종목 일괄** JSON.
# 지금까지 TW 업종은 yfinance 개별조회(.info, 백그라운드·250개/회 상한)에만
# 의존해 무버 TOP30 신규진입 종목(변동성 큰 소형주 위주 — 캐시가 항상 콜드)이
# 늘 '—' 로 빠졌다. STOCK_DAY_ALL 과 같은 OpenAPI 호스트 계열(t187ap03_L =
# 상장법인 기본자료). ⚠️ 이 URL/필드명은 샌드박스에서 openapi.twse.com.tw 가
# 프록시에 403 차단돼 라이브 검증 불가 — fetch_tw_industry_map 은 필드를
# 이름(부분일치, "代號"/"產業")으로 탐색해 스키마가 달라도 조용히 {} 반환하고
# (개별조회 yfinance 폴백 그대로 유지, 회귀 0) 이 파일 실행 시(`--probe`) 실제
# 응답을 찍는다 — VM 실행 결과로 최종 확인 필요.
_OPENAPI_LISTED_INFO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
# TPEx(上櫃) 기본자료 등가물 — 정확한 경로 미검증(같은 이유). 실패해도
# graceful(상장 TWSE 분만 채워짐 — 上櫃 분은 기존 yfinance 폴백 유지).
_OPENAPI_OTC_INFO = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_TW_IND_CACHE_TTL = 24 * 3600   # 상장법인 기본자료는 하루 내 사실상 불변
_HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.twse.com.tw/",
         "Accept": "application/json"}
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "twse"
_CACHE_TTL_SEC = 5 * 60
_TW_LIMIT = 9.9      # TW 일일 한도 = 플랫 ±10%. **진정한 의미의 상한가/하한가**
                     # = 한도 도달(틱 반올림으로 종가 등락 ≈ ±9.9~10.0%) — 사용자
                     # 2026-06-13 'TW 도 진정한 의미의 상한가 하한가로'(JP ストップ
                     # 高/安 미러). 옛 9.5(근접권 넓게)에서 한도-도달로 전환.

# 繁體 類股名 → 한국어 (가독성, 미매핑은 繁體 그대로)
_SECTOR_KR = {
    "水泥": "시멘트", "食品": "식품", "塑膠": "플라스틱", "紡織纖維": "섬유",
    "電機機械": "전기기계", "電器電纜": "전선", "化學生技醫療": "화학·바이오",
    "化學": "화학", "生技醫療": "바이오·의료", "玻璃陶瓷": "유리·도자",
    "造紙": "제지", "鋼鐵": "철강", "橡膠": "고무", "汽車": "자동차",
    "半導體": "반도체", "電腦及週邊": "컴퓨터·주변",
    "電腦及週邊設備": "컴퓨터·주변기기",   # TWSE 정식명(類指數 strip 후) — 設備 suffix
    "光電": "광전(디스플레이)",
    "通信網路": "통신·네트워크", "電子零組件": "전자부품", "電子通路": "전자유통",
    "資訊服務": "IT서비스", "其他電子": "기타전자", "建材營造": "건설·건자재",
    "航運": "해운", "觀光": "관광", "觀光餐旅": "관광·외식", "金融保險": "금융·보험",
    "貿易百貨": "무역·백화", "油電燃氣": "석유·전력·가스", "綠能環保": "그린·환경",
    "數位雲端": "디지털·클라우드", "運動休閒": "스포츠·레저", "居家生活": "홈리빙",
    "電子工業": "전자", "其他": "기타",
    # 레버리지/인버스 + 복합 업종 (사용자 2026-06-15 '대만 중국어도 한글로') —
    # TWSE 類股 에 섞여 나오던 미매핑분 보강.
    "電子兩倍槓桿": "전자 2배(레버리지)", "電子反向": "전자 인버스",
    "塑膠化工": "플라스틱·화학", "機電": "기계·전기", "電機": "전기기계",
    "塑膠工業": "플라스틱", "化學工業": "화학", "電子類": "전자",
    "半導體業": "반도체", "其他電子業": "기타전자", "貿易百貨業": "무역·백화",
}

# MOPS t187ap03의 `產業別`은 업종명이 아니라 이 2자리 코드로 내려온다.
_INDUSTRY_CODE_KR = {
    "01": "시멘트", "02": "식품", "03": "플라스틱", "04": "섬유",
    "05": "전기기계", "06": "전선", "08": "유리·도자", "09": "제지",
    "10": "철강", "11": "고무", "12": "자동차", "14": "건설·건자재",
    "15": "해운", "16": "관광·외식", "17": "금융·보험", "18": "무역·백화",
    "20": "기타", "21": "화학", "22": "바이오·의료", "23": "석유·전력·가스",
    "24": "반도체", "25": "컴퓨터·주변기기", "26": "광전(디스플레이)",
    "27": "통신·네트워크", "28": "전자부품", "29": "전자유통",
    "30": "IT서비스", "31": "기타전자", "32": "문화창작",
    "33": "농업기술", "34": "전자상거래", "35": "그린·환경",
    "36": "디지털·클라우드", "37": "스포츠·레저", "38": "홈리빙",
}


def _sector_kr(core: str) -> str:
    """繁體 類股명 → 한국어. 정확 매칭 우선, 미스 시 接尾 변형 자가매칭(電腦及週邊設備
    ↔ 電腦及週邊 — 길이 4+ prefix 만, 오매칭 방지) → 끝내 미스면 繁體 그대로(사용자
    2026-06-16 한자 누수 fix). TWSE 類股명에 設備/業 등 suffix 변형이 섞여 나오던 것 흡수."""
    core = str(core or "").strip()
    if core.isdigit():
        return _INDUSTRY_CODE_KR.get(core.zfill(2), "기타")
    if core in _SECTOR_KR:
        return _SECTOR_KR[core]
    # core(TWSE 정식명)가 map key(짧은 형)로 시작할 때만 매칭 — 設備/業 등 suffix
    # 변형 흡수. 역방향(k.startswith(core))은 짧은 core 의 오매칭(電子→電子兩倍槓桿)
    # 위험이라 제외. 가장 긴(구체적) key 우선.
    best = None
    for k in _SECTOR_KR:
        if len(k) >= 4 and core.startswith(k):
            if best is None or len(k) > len(best):
                best = k
    return _SECTOR_KR[best] if best else core


def _now_kst_label() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")


def _tw_today() -> str:
    # 대만(UTC+8) 기준 당일
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d")


def _cached(name: str) -> Optional[dict]:
    try:
        fp = _CACHE_DIR / f"{name}.json"
        if fp.exists() and (time.time() - fp.stat().st_mtime) < _CACHE_TTL_SEC:
            return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _cached_stale(name: str, max_age_sec: int = 86400) -> Optional[dict]:
    """live TTL(5분) 무시하고 max_age 안의 마지막 스냅샷 반환 — 장 마감/점검
    으로 live 가 비었을 때 '직전 좋은 데이터' 복원용(사용자 2026-06-15 '대만 장중엔
    제대로 나오더니 마감 후 ETF 4개로 degrade'). 24h 안이면 당일 마지막 장중 스냅샷."""
    try:
        fp = _CACHE_DIR / f"{name}.json"
        if fp.exists() and (time.time() - fp.stat().st_mtime) < max_age_sec:
            return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _cache_write(name: str, obj: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{name}.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _to_tables(js: dict) -> list[dict]:
    """신(tables) / 구(data1..data9 + fields1..) 포맷 모두 → [{fields, data}]."""
    if isinstance(js.get("tables"), list):
        return [t for t in js["tables"] if isinstance(t, dict)]
    out = []
    for i in range(1, 12):
        d = js.get(f"data{i}")
        f = js.get(f"fields{i}")
        if isinstance(d, list) and isinstance(f, list):
            out.append({"fields": f, "data": d})
    return out


def _num(s) -> Optional[float]:
    """'1,234.5' / '+0.83' / '<p ...>+</p>' 등 → float. HTML/콤마/부호 처리."""
    if s is None:
        return None
    t = re.sub(r"<[^>]+>", "", str(s)).replace(",", "").strip()
    t = t.replace("＋", "+").replace("－", "-")
    if t in ("", "--", "-", "X", "x"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _sign_from(s) -> int:
    """漲跌(+/-) 셀 → +1/-1/0. 색(red=상승/green=하락) 또는 +/- 문자."""
    t = str(s)
    if "red" in t or "＋" in t or re.search(r">\s*\+", t) or t.strip() in ("+", "＋"):
        return 1
    if "green" in t or "－" in t or re.search(r">\s*-", t) or t.strip() in ("-", "－"):
        return -1
    return 0


def _field_idx(fields: list, *needles: str) -> Optional[int]:
    for i, f in enumerate(fields):
        fl = str(f)
        if all(n in fl for n in needles):
            return i
    return None


def parse_sector_rows(tables: list[dict]) -> list[dict]:
    """類股指數 테이블 → [{name(한국어), pct}]. 필드 시그니처로 스캔. 순수."""
    for t in tables:
        fields = t.get("fields") or []
        pct_i = _field_idx(fields, "漲跌百分比")
        if pct_i is None:
            pct_i = _field_idx(fields, "漲跌", "%")
        name_i = 0
        if pct_i is None or not t.get("data"):
            continue
        rows = []
        for r in t["data"]:
            if not isinstance(r, list) or len(r) <= pct_i:
                continue
            raw_nm = re.sub(r"<[^>]+>", "", str(r[name_i])).strip()
            # '半導體類指數' / '金融保險類' → 핵심 섹터명
            if "類" not in raw_nm:
                continue
            core = raw_nm.replace("類指數", "").replace("指數", "").replace("類", "").strip()
            pct = _num(r[pct_i])
            if pct is None or not core:
                continue
            rows.append({"name": _sector_kr(core), "pct": round(pct, 2)})
        if len(rows) >= 5:          # 류股 테이블로 확정(섹터 다수)
            return rows
    return []


def parse_stock_rows(tables: list[dict]) -> list[dict]:
    """每日收盤行情 테이블 → [{code,name,close,pct}]. 漲跌價差+부호로 % 산출."""
    for t in tables:
        fields = t.get("fields") or []
        code_i = _field_idx(fields, "證券代號")
        close_i = _field_idx(fields, "收盤價")
        diff_i = _field_idx(fields, "漲跌價差")
        sign_i = _field_idx(fields, "漲跌(+/-)") or _field_idx(fields, "漲跌(+/")
        name_i = _field_idx(fields, "證券名稱")
        if code_i is None or close_i is None or diff_i is None:
            continue
        out = []
        for r in t.get("data") or []:
            if not isinstance(r, list) or len(r) <= max(code_i, close_i, diff_i):
                continue
            close = _num(r[close_i])
            diff = _num(r[diff_i])
            if close is None or diff is None or close == 0:
                continue
            sign = _sign_from(r[sign_i]) if sign_i is not None and len(r) > sign_i else (
                1 if diff > 0 else -1 if diff < 0 else 0)
            schg = sign * abs(diff)
            prev = close - schg
            pct = (schg / prev * 100.0) if prev else 0.0
            out.append({
                "code": re.sub(r"<[^>]+>", "", str(r[code_i])).strip(),
                "name": (re.sub(r"<[^>]+>", "", str(r[name_i])).strip()
                         if name_i is not None and len(r) > name_i else ""),
                "close": close, "pct": round(pct, 2),
            })
        if len(out) >= 50:          # 전종목 테이블로 확정
            return out
    return []


def parse_stock_day_all(rows: list) -> list[dict]:
    """OpenAPI STOCK_DAY_ALL(평평한 dict 배열) → [{code,name,close,pct}].
    Change=부호 포함 가격변동. pct = Change/(Close-Change). 필드명 변형 tolerant."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        close = _num(r.get("ClosingPrice") or r.get("Close"))
        chg = _num(r.get("Change"))
        if close is None or chg is None or close == 0:
            continue
        prev = close - chg
        pct = (chg / prev * 100.0) if prev else 0.0
        # 거래량(주식수)·거래대금(NT$) — TWSE STOCK_DAY_ALL(TradeVolume/TradeValue)
        # + TPEx daily_close_quotes(TradingShares/TransactionAmount) 둘 다 tolerant
        # (T9 2026-06-16 上櫃 확장 — 한 파서로 上市/上櫃 공용).
        vol = _num(r.get("TradeVolume") or r.get("成交股數") or r.get("TradingShares"))
        val = _num(r.get("TradeValue") or r.get("成交金額") or r.get("TransactionAmount"))
        if val is None and vol is not None and close:
            val = close * vol
        out.append({
            "code": str(r.get("Code") or r.get("代號")
                        or r.get("SecuritiesCompanyCode") or "").strip(),
            "name": str(r.get("Name") or r.get("名稱")
                        or r.get("CompanyName") or "").strip(),
            "close": close, "pct": round(pct, 2),
            "vol": int(vol) if vol is not None else None,
            "value": round(val / 1e8, 2) if val is not None else None})  # 억 NT$
    return out


def _roc_to_iso(s) -> str:
    """ROC 날짜 'YYYMMDD'(115=2026) → 'YYYY-MM-DD'. 실패 시 ''."""
    t = str(s or "").strip()
    if len(t) == 7 and t.isdigit():
        return f"{int(t[:3]) + 1911}-{t[3:5]}-{t[5:7]}"
    return ""


def fetch_stock_day_all(force: bool = False) -> dict:
    """OpenAPI 전종목 일일 → {rows:[{code,name,close,pct,vol,value}], date}.
    시장-인지 신선도(_session_fresh TW, 장중 1h / 장 밖 마지막 마감 이후 재산출 0
    — 사용자 2026-06-14 '모두 장중 1h'). 옛 플랫 5분 대체. graceful.
    force=True 면 신선도 게이트 건너뛰고 즉시 refetch(장중 슬롯 스캔 1h 경계
    jitter skip 방지, 사용자 2026-06-16)."""
    fp = _CACHE_DIR / "stock_day_all.json"
    try:
        if not force and fp.exists():
            from bot.finviz_client import _HL_INTRA_TTL, _session_fresh
            if _session_fresh("TW", fp.stat().st_mtime, _HL_INTRA_TTL):
                return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        # 신선도 판정 불가(finviz import 실패 등) → 기존 5분 캐시로 폴백
        c = _cached("stock_day_all")
        if c is not None:
            return c
    try:
        r = requests.get(_OPENAPI_STOCK, headers=_HDRS, timeout=15)
        r.raise_for_status()
        raw = r.json()
        rows = parse_stock_day_all(raw)
        date = _roc_to_iso(raw[0].get("Date")) if isinstance(raw, list) and raw else ""
    except Exception as exc:
        log.warning("twse STOCK_DAY_ALL fetch error: %s", exc)
        try:                       # fetch 실패 시 스테일 캐시라도(블랭크 방지)
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"rows": [], "date": ""}
    out = {"rows": rows, "date": date}
    if rows:
        _cache_write("stock_day_all", out)
    return out


def fetch_tpex_day_all(force: bool = False) -> dict:
    """上櫃(TPEx) 전종목 일일 → {rows:[{code,name,close,pct,vol,value}], date}.
    STOCK_DAY_ALL(上市) 의 上櫃 등가물(T9 2026-06-16) — 무버/상한가 보드 유니버스를
    上市+上櫃 합집합으로 확장. parse_stock_day_all 공용(TPEx 필드명 tolerant).
    시장-인지 신선도(_session_fresh TW, 장중 1h). graceful — 실패 시 스테일/빈.
    force=True 면 게이트 건너뛰고 즉시 refetch(장중 슬롯 스캔, 사용자 2026-06-16)."""
    fp = _CACHE_DIR / "tpex_day_all.json"
    try:
        if not force and fp.exists():
            from bot.finviz_client import _HL_INTRA_TTL, _session_fresh
            if _session_fresh("TW", fp.stat().st_mtime, _HL_INTRA_TTL):
                return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        c = _cached("tpex_day_all")
        if c is not None:
            return c
    try:
        r = requests.get(_OPENAPI_TPEX, headers=_HDRS, timeout=15)
        r.raise_for_status()
        raw = r.json()
        rows = parse_stock_day_all(raw)
        date = _roc_to_iso(raw[0].get("Date")) if isinstance(raw, list) and raw else ""
    except Exception as exc:
        log.warning("tpex day_all fetch error: %s", exc)
        try:
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"rows": [], "date": ""}
    out = {"rows": rows, "date": date}
    if rows:
        _cache_write("tpex_day_all", out)
    return out


def fetch_mi_index() -> dict:
    """TWSE MI_INDEX(type=ALL) → {sectors:[...], stocks:[...], ts}. graceful."""
    c = _cached("mi_index")
    if c is not None:
        return c
    try:
        params = {"response": "json", "type": "ALL", "date": _tw_today()}
        r = requests.get(_URL, params=params, headers=_HDRS, timeout=12)
        r.raise_for_status()
        js = r.json()
    except Exception as exc:
        log.warning("twse MI_INDEX fetch error: %s", exc)
        return {"sectors": [], "stocks": [], "ts": ""}
    if js.get("stat") != "OK":
        return {"sectors": [], "stocks": [], "ts": ""}
    tables = _to_tables(js)
    out = {
        "sectors": parse_sector_rows(tables),
        "stocks": parse_stock_rows(tables),
        "ts": _now_kst_label(),
    }
    if out["sectors"] or out["stocks"]:
        _cache_write("mi_index", out)
    return out


def _fetch_one_industry_source(url: str, label: str) -> dict[str, str]:
    """OpenAPI 상장법인 기본자료 1개 소스 → {종목코드: 업종(한글)}. 필드명은
    고정 인덱스 대신 **부분일치로 탐색**(代號/產業 포함 키) — 이 파일의 다른
    파서(_field_idx)와 같은 방어 스타일. 응답 형태·필드명이 예상과 다르면
    로그만 남기고 {} (호출부가 yfinance 개별조회로 자연 폴백, 회귀 0)."""
    try:
        r = requests.get(url, headers=_HDRS, timeout=15)
        rows = r.json() if r.status_code == 200 else None
    except Exception as exc:
        log.warning("twse industry map (%s) fetch failed: %s", label, exc)
        return {}
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        log.warning("twse industry map (%s): 예상 밖 응답 형태(list[dict] 아님) — "
                   "스킵(yfinance 폴백 유지)", label)
        return {}
    sample = rows[0]
    code_key = next((k for k in sample if "代號" in k), None)
    ind_key = next((k for k in sample if "產業" in k), None)
    if not code_key or not ind_key:
        log.warning("twse industry map (%s): 필드 미탐색(code=%s ind=%s, keys=%s) — "
                   "스키마 변경 의심, 스킵", label, code_key, ind_key, list(sample)[:12])
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get(code_key) or "").strip()
        ind = str(row.get(ind_key) or "").strip()
        if code and ind:
            out[code] = _sector_kr(ind)
    log.info("twse industry map (%s): %d종목 (code_key=%s ind_key=%s)",
             label, len(out), code_key, ind_key)
    return out


def fetch_tw_industry_map(force: bool = False) -> dict[str, str]:
    """{종목코드: 업종(한글)} — 상장(TWSE)+상장(TPEx上櫃) 전종목 기본자료
    일괄 조회(24h 캐시, 사용자 2026-08-04). TW 무버/52주 페이지가 지금까지
    yfinance 개별조회(백그라운드·상한 250개/회)에만 의존해 신규진입 종목
    (변동성 큰 소형주 위주라 캐시가 늘 콜드)이 항상 업종 '—' 로 빠지던 것의
    근본 해소 시도 — 전종목 일괄이라 렌더-세이프(캐시-only) 경로에서도
    즉시 채워짐. 두 소스 중 하나만 성공해도 부분 반환(graceful) — TPEx 실패
    시 TWSE 상장분만이라도 개선. 코드 키는 6자리 zero-pad 없이 원문 그대로
    (finviz_client._industries_for 가 호출측에서 티커 정규화)."""
    if not force:
        c = _cached_stale("tw_industry_map", max_age_sec=_TW_IND_CACHE_TTL)
        if isinstance(c, dict) and c:
            normalized = {code: _sector_kr(ind) for code, ind in c.items()}
            if normalized != c:
                _cache_write("tw_industry_map", normalized)
            return normalized
    out: dict[str, str] = {}
    out.update(_fetch_one_industry_source(_OPENAPI_LISTED_INFO, "上市"))
    out.update(_fetch_one_industry_source(_OPENAPI_OTC_INFO, "上櫃"))
    if out:
        _cache_write("tw_industry_map", out)
    return out


def fetch_tw_sector_movers(top_n: int = 10) -> dict:
    """TW 업종 등락 — TWSE 類股 지수(약 20-40업종). 장 마감/점검으로 live 가 비면
    직전 좋은 類股 스냅샷(stale 캐시)을 복원해 ETF 4개 폴백 degrade 방지
    (사용자 2026-06-15). 그래도 없으면 {} (호출부가 ETF 폴백).

    freeze 윈도 = **4일**(사용자 2026-06-17 'TW 업종 4개로 떨어짐' 진단): 24h 였을
    때, 어제 14:30 캐시가 오늘 14:30 만료 → 오늘 장중(10:00-14:30) 類股 fetch 가
    배포 churn/TWSE 일시장애로 못 들어오면 그 직후 윈도에 캐시 0 → ETF 4개 degrade.
    4일이면 전일·주말(금→월) 캐시가 fallback 으로 유지돼 transient/overnight 갭에도
    類股(39) 서빙. ts 라벨이 실제 스냅샷 시각을 보여줘 stale 여부는 화면에서 정직."""
    mi = fetch_mi_index()
    secs = mi.get("sectors") or []
    if not secs:
        # 장 마감/점검/일시장애 — 직전 좋은 類股 스냅샷(4일 내) 복원
        cached = _cached_stale("mi_index", max_age_sec=4 * 86400) or {}
        c_secs = cached.get("sectors") or []
        if c_secs:
            mi, secs = cached, c_secs
    if not secs:
        return {"up": [], "down": [], "ts": mi.get("ts", ""), "source": ""}
    up = sorted([s for s in secs if s["pct"] > 0], key=lambda s: s["pct"], reverse=True)[:top_n]
    down = sorted([s for s in secs if s["pct"] < 0], key=lambda s: s["pct"])[:top_n]
    return {"up": up, "down": down, "ts": mi.get("ts", ""),
            "source": "TWSE 類股", "n": len(secs)}


def _is_common_stock(code: str) -> bool:
    """순수 일반종목만 (사용자 2026-06-13 '순수종목만') — TW 일반주는 4자리
    숫자(1101~9962, 첫자리 1-9). ETF(0050·00910·00400A)·워런트(6자리)·
    우선주(2887A 등 문자) 제외 → 상한가(±10%) 한도가 적용되는 종목만."""
    c = str(code or "")
    return len(c) == 4 and c.isdigit() and c[0] != "0"


def _tw_all_common(include_tpex: bool = True, force: bool = False) -> tuple[list, str]:
    """上市(STOCK_DAY_ALL) + 上櫃(TPEx) 일반종목 합집합 (T9 2026-06-16) — 무버/
    상한가 유니버스를 두 거래소 전종목으로 확장. code dedup(上市 우선). 무버 보드는
    가격/등락을 OpenAPI 가 직접 줘 비용 0(52주 신고저는 yfinance 1년 스캔이라
    上櫃 미포함 — fetch_stock_day_all 직접 사용 경로 유지). (rows, date).
    force=True 면 양 거래소 즉시 refetch(장중 슬롯 스캔, 사용자 2026-06-16)."""
    data = fetch_stock_day_all(force=force)
    rows = [s for s in data.get("rows", []) if _is_common_stock(s.get("code"))]
    date = data.get("date", "")
    if include_tpex:
        try:
            seen = {s.get("code") for s in rows}
            tpex = fetch_tpex_day_all(force=force)
            for s in tpex.get("rows", []):
                c = s.get("code")
                if c and c not in seen and _is_common_stock(c):
                    rows.append(s)
                    seen.add(c)
            if not date:
                date = tpex.get("date", "")
        except Exception as exc:
            log.warning("tpex merge skipped: %s", exc)
    return rows, date


def fetch_tw_upper_lower(limit: int = 80) -> dict:
    """TW 상한가/하한가권 — 上市+上櫃 전종목 중 ±9.9%+ (TW 한도 ±10% 도달).
    순수 일반종목만(ETF·워런트 제외). 점검 무관. {upper,lower,ts,date}."""
    stocks, date = _tw_all_common()
    upper = sorted([s for s in stocks if s["pct"] >= _TW_LIMIT],
                   key=lambda s: s["pct"], reverse=True)[:limit]
    lower = sorted([s for s in stocks if s["pct"] <= -_TW_LIMIT],
                   key=lambda s: s["pct"])[:limit]
    return {"upper": upper, "lower": lower, "ts": _now_kst_label(), "date": date}


def fetch_tw_movers(limit: int = 30, force: bool = False) -> dict:
    """TW 급등/급락 — 上市(STOCK_DAY_ALL)+上櫃(TPEx) 전종목(일반종목) 등락 상·하위
    (T9 2026-06-16 上櫃 확장, JP/CN/HK 무버 형태). {up,down,ts,date}.
    force=True 면 OpenAPI 즉시 refetch(장중 시간대별 슬롯 스캔, 사용자 2026-06-16)."""
    stocks, date = _tw_all_common(force=force)
    up = sorted(stocks, key=lambda s: s["pct"], reverse=True)[:limit]
    down = sorted(stocks, key=lambda s: s["pct"])[:limit]
    return {"up": up, "down": down, "ts": _now_kst_label(), "date": date}


if __name__ == "__main__":   # VM 라이브 구조 검증
    logging.basicConfig(level=logging.INFO)
    # ① 상한가/하한가 — OpenAPI STOCK_DAY_ALL (점검 무관, 언제나)
    ul = fetch_tw_upper_lower()
    stk = fetch_stock_day_all().get("rows", [])
    print(f"[OpenAPI {ul.get('date','')}] 전종목 {len(stk)}개 · "
          f"상한가 {len(ul['upper'])} / 하한가 {len(ul['lower'])}")
    for s in ul["upper"][:5]:
        print("  ▲", s["code"], s["name"], s["pct"], "%")
    # ② 업종(類股) — legacy MI_INDEX (13:30~13:45 TW 점검 시간 외에 실행)
    mi = fetch_mi_index()
    print(f"[legacy 類股] 업종 {len(mi['sectors'])}개 (점검시간이면 0=정상)")
    for s in mi["sectors"][:5]:
        print("  업종", s["name"], s["pct"], "%")
    # ③ 종목별 업종 맵 — 상장법인 기본자료(2026-08-04 신규, URL/필드 미검증
    # — 샌드박스가 openapi.twse.com.tw 프록시 403 이라 이 실행 결과로 최종
    # 확인 필요). 0종목이면 _fetch_one_industry_source 의 warning 로그에서
    # 실제 응답 keys 를 확인해 코드의 code_key/ind_key 탐색 로직 조정.
    imap = fetch_tw_industry_map(force=True)
    print(f"[업종맵] {len(imap)}종목")
    for code, ind in list(imap.items())[:8]:
        print("  ", code, ind)
