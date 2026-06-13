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
_HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.twse.com.tw/",
         "Accept": "application/json"}
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "twse"
_CACHE_TTL_SEC = 5 * 60
_TW_LIMIT = 9.5      # TW 일일 한도 ±10% → 상한가/하한가권 임계(틱 라운딩 여유)

# 繁體 類股名 → 한국어 (가독성, 미매핑은 繁體 그대로)
_SECTOR_KR = {
    "水泥": "시멘트", "食品": "식품", "塑膠": "플라스틱", "紡織纖維": "섬유",
    "電機機械": "전기기계", "電器電纜": "전선", "化學生技醫療": "화학·바이오",
    "化學": "화학", "生技醫療": "바이오·의료", "玻璃陶瓷": "유리·도자",
    "造紙": "제지", "鋼鐵": "철강", "橡膠": "고무", "汽車": "자동차",
    "半導體": "반도체", "電腦及週邊": "컴퓨터·주변", "光電": "광전(디스플레이)",
    "通信網路": "통신·네트워크", "電子零組件": "전자부품", "電子通路": "전자유통",
    "資訊服務": "IT서비스", "其他電子": "기타전자", "建材營造": "건설·건자재",
    "航運": "해운", "觀光": "관광", "觀光餐旅": "관광·외식", "金融保險": "금융·보험",
    "貿易百貨": "무역·백화", "油電燃氣": "석유·전력·가스", "綠能環保": "그린·환경",
    "數位雲端": "디지털·클라우드", "運動休閒": "스포츠·레저", "居家生活": "홈리빙",
    "電子工業": "전자", "其他": "기타",
}


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
            rows.append({"name": _SECTOR_KR.get(core, core), "pct": round(pct, 2)})
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
        out.append({
            "code": str(r.get("Code") or r.get("代號") or "").strip(),
            "name": str(r.get("Name") or r.get("名稱") or "").strip(),
            "close": close, "pct": round(pct, 2)})
    return out


def fetch_stock_day_all() -> list[dict]:
    """OpenAPI 전종목 일일 → [{code,name,close,pct}]. 5분 캐시·graceful."""
    c = _cached("stock_day_all")
    if c is not None:
        return c.get("rows", [])
    try:
        r = requests.get(_OPENAPI_STOCK, headers=_HDRS, timeout=15)
        r.raise_for_status()
        rows = parse_stock_day_all(r.json())
    except Exception as exc:
        log.warning("twse STOCK_DAY_ALL fetch error: %s", exc)
        return []
    if rows:
        _cache_write("stock_day_all", {"rows": rows})
    return rows


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


def fetch_tw_sector_movers(top_n: int = 10) -> dict:
    """TW 업종 등락 — TWSE 類股 지수. 빈 결과 시 {} (호출부가 ETF 폴백)."""
    mi = fetch_mi_index()
    secs = mi.get("sectors") or []
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


def fetch_tw_upper_lower(limit: int = 80) -> dict:
    """TW 상한가/하한가권 — OpenAPI 전종목 중 ±9.5%+ (TW 한도 ±10%). 순수
    일반종목만(ETF·워런트 제외). 점검 무관. {upper,lower,ts}."""
    stocks = [s for s in fetch_stock_day_all() if _is_common_stock(s.get("code"))]
    upper = sorted([s for s in stocks if s["pct"] >= _TW_LIMIT],
                   key=lambda s: s["pct"], reverse=True)[:limit]
    lower = sorted([s for s in stocks if s["pct"] <= -_TW_LIMIT],
                   key=lambda s: s["pct"])[:limit]
    return {"upper": upper, "lower": lower, "ts": _now_kst_label()}


if __name__ == "__main__":   # VM 라이브 구조 검증
    logging.basicConfig(level=logging.INFO)
    # ① 상한가/하한가 — OpenAPI STOCK_DAY_ALL (점검 무관, 언제나)
    ul = fetch_tw_upper_lower()
    stk = fetch_stock_day_all()
    print(f"[OpenAPI] 전종목 {len(stk)}개 · 상한가권 {len(ul['upper'])} / "
          f"하한가권 {len(ul['lower'])}")
    for s in ul["upper"][:5]:
        print("  ▲", s["code"], s["name"], s["pct"], "%")
    # ② 업종(類股) — legacy MI_INDEX (13:30~13:45 TW 점검 시간 외에 실행)
    mi = fetch_mi_index()
    print(f"[legacy 類股] 업종 {len(mi['sectors'])}개 (점검시간이면 0=정상)")
    for s in mi["sectors"][:5]:
        print("  업종", s["name"], s["pct"], "%")
