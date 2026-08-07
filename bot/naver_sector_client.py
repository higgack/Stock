"""Naver 증권 시세 스크래퍼 — 업종/테마 등락 + 상한가·하한가.

- fetch_sector_movers(): sise_group.naver?type=upjong → 업종 상승/하락 TOP.
- fetch_themes(): sise/theme.naver → 테마별 등락(별도 페이지).
- fetch_upper_lower(): 상한가·하한가(sise_upper/lower). 52주 신고저 페이지는
  불안정해 제거(2026-06-10) — KR 신고저는 유니버스 스캔이 후속 과제.

무료·무키. euc-kr 디코딩. graceful — 실패/빈 결과 시 빈값. 사용자 정책
2026-06-10: 리스크 없는 한 가장 빠르게 → 장중 30초 캐시(session-aware, 장 밖 재fetch 0).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.naver_sector")

def _now_kst_label() -> str:
    """명시적 KST 라벨 — 서버 로컬타임 의존 제거 (사용자 정책: 모든 표기
    한국시간 기준, 2026-06-11)."""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


_BASE = "https://finance.naver.com/sise"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_sector"
_CACHE_TTL_SEC = 30  # 30초(사용자 2026-06-15 '업종·테마 30초·네이버 문제없음') — 1 HTTP/요청

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://finance.naver.com/sise/",
}

# 업종/테마 공통 — sise_group_detail.naver?type=upjong|theme&no=N 링크의 이름
_GROUP_RE = re.compile(
    r'sise_group_detail\.naver\?type=(?:upjong|theme)[^"]*?no=\d+"[^>]*>([^<]+)</a>',
    re.I)
_PCT_RE = re.compile(r'([+\-]?)(\d{1,3}\.\d{1,2})\s*%')
_ITEM_RE = re.compile(r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', re.I)
# 업종 그룹 링크(번호+이름) — 멤버 스캔용 (parse_groups 의 _GROUP_RE 는 이름만 캡처).
_UPJONG_NO_RE = re.compile(
    r'sise_group_detail\.naver\?type=upjong[^"]*?no=(\d+)"[^>]*>([^<]+)</a>', re.I)


def _get(url: str, **kwargs) -> Optional[str]:
    try:
        from bot.finviz_client import naver_paused
        if naver_paused():       # NAVER_PAUSE → fetch skip(호출부 캐시 폴백)
            return None
    except Exception:
        pass
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, **kwargs)
        resp.encoding = "euc-kr"
        if resp.status_code != 200 or not resp.text:
            return None
        return resp.text
    except Exception as exc:
        log.warning("naver_sector: fetch failed %s: %s", url, exc)
        return None


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&middot;", "·")
    return " ".join(s.split()).strip()


def _cell_texts(row_html: str) -> list[str]:
    out = []
    for inner in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.I):
        out.append(_clean(inner))
    return out


def _pct_from_row(row_html: str) -> Optional[float]:
    """행의 등락률(%) — 부호 텍스트 우선, 없으면 색 클래스(red/nv)로 추정."""
    m = _PCT_RE.search(_clean(row_html))
    if not m:
        return None
    num = float(m.group(2))
    sign = m.group(1)
    if sign == "-":
        return -num
    if sign != "+" and re.search(r"(nv01|nv02|blue|down)", row_html, re.I):
        return -num
    return num


def parse_groups(html: str) -> list[dict]:
    """업종/테마 표 → [{name, pct}] (등락률 %)."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        a = _GROUP_RE.search(row)
        if not a:
            continue
        name = _clean(a.group(1))
        if not name or name in seen:
            continue
        pct = _pct_from_row(row)
        if pct is None:
            continue
        seen.add(name)
        out.append({"name": name, "pct": round(pct, 2)})
    return out


_THEME_LINK_RE = re.compile(
    r'sise_group_detail\.naver\?type=theme[^"]*?no=(\d+)"[^>]*>([^<]+)</a>', re.I)
_SIGNED_PCT_RE = re.compile(r'([+\-]?)(\d{1,3}\.\d{1,2})\s*%')


def parse_themes_full(html: str) -> list[dict]:
    """테마 표 → [{name, no, pct, pct3, leaders}].

    pct=전일대비 등락률, pct3=최근3일 평균 등락률, leaders=주도주(최대 2)."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        m = _THEME_LINK_RE.search(row)
        if not m:
            continue
        no, name = m.group(1), _clean(m.group(2))
        if not name or no in seen:
            continue
        # % 값들 — 순서대로 전일대비, 최근3일
        pcts: list[float] = []
        for pm in _SIGNED_PCT_RE.finditer(_clean(row)):
            v = float(pm.group(2))
            if pm.group(1) == "-":
                v = -v
            pcts.append(round(v, 2))
        pct = pcts[0] if pcts else None
        pct3 = pcts[1] if len(pcts) > 1 else None
        # 주도주 — /item/main.naver?code= 의 종목명+코드(최대 2) → 종목분석 링크용
        leaders = []
        seen_codes: set = set()
        for lm in re.finditer(
                r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', row, re.I):
            code = lm.group(1)
            nm = _clean(lm.group(2))
            if nm and code not in seen_codes:
                seen_codes.add(code)
                leaders.append({"name": nm, "code": code})
        seen.add(no)
        out.append({"name": name, "no": no, "pct": pct,
                    "pct3": pct3, "leaders": leaders[:2]})
    return out


def _cached(name: str, ttl: float | None = None):
    """ttl=None(기본) = 세션-인지(KR 장중 30초/장 밖 재fetch 0) — 업종·테마·
    상한가/하한가처럼 '장 마감 후엔 더 바뀔 값이 없는' 실시간 시세 전용.

    ttl=<초> 명시 시 그 값으로 **평시간 TTL** 사용(장 개폐 무관) — 2026-08-03
    fix: 투자자예탁금(deposit.json)은 KOFIA 가 T-1 데이터를 자체 스케줄로
    공표(KR 장마감 15:30 이후·저녁에도 갱신될 수 있음, 사용자 실측 —
    TNBfolio 는 18:35 에 07/31 반영했는데 우리는 장중 스냅샷에 갇혀 07/30
    고정)해 KR 세션과 무관하다. 세션-frozen 캐시를 그대로 쓰면 장 마감 후
    새 공표본이 나와도 다음 장 개장까지 재fetch 자체가 안 됨 — fsc_client.
    _kofia_series 의 안쪽 3h 캐시(날짜-keyed, 하루종일 재확인)가 있어도 이
    바깥 캐시가 먼저 막아 무의미해짐."""
    cache_file = _CACHE_DIR / name
    if cache_file.exists():
        try:
            mt = cache_file.stat().st_mtime
            if ttl is not None:
                fresh = (time.time() - mt < ttl)
            else:
                # 세션-인지(사용자 2026-06-15 '업종 30초'): KR 장중 30초 / 장
                # 밖엔 마지막 산출본 fresh(재fetch 0). _session_fresh 부재 시
                # _CACHE_TTL_SEC 폴백.
                try:
                    from bot.finviz_client import _session_fresh
                    fresh = _session_fresh("KR", mt, _CACHE_TTL_SEC)
                except Exception:
                    fresh = (time.time() - mt < _CACHE_TTL_SEC)
            if fresh:
                return json.loads(cache_file.read_text())
        except Exception:
            pass
    return None


def _cache_write(name: str, obj) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / name).write_text(json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


# ── KR 종목코드 → 업종(한글) 맵 (사용자 2026-06-14 'KR 신고가·급등락에 업종 한글') ──
# 네이버 domestic 시세 객체엔 업종이 없음(_kr_row 'ind 미제공'). 업종 그룹
# (sise_group upjong) 의 각 그룹 상세 페이지 멤버를 스캔해 코드→업종 맵 구축.
# 업종 멤버십은 안정적 → 7일 캐시 + SWR 백그라운드(렌더 블로킹 0, world_upjong_name
# 패턴). 업종명은 네이버 한글 그대로(KR 은 번역 안 함 — 사용자 '그냥 한글로 당연히').
_KR_IND_CACHE = "kr_industry_map.json"
_KR_IND_TTL = 7 * 86400
_kr_ind_building = False
_kr_ind_lock = threading.Lock()


def _build_kr_industry_map() -> None:
    """네이버 업종 그룹 멤버 스캔 → {6자리코드 → 업종명(한글)}. 백그라운드 1회."""
    global _kr_ind_building
    try:
        html = _get(f"{_BASE}/sise_group.naver", params={"type": "upjong"})
        groups: list = []
        seen: set = set()
        if html:
            for m in _UPJONG_NO_RE.finditer(html):
                no, name = m.group(1), _clean(m.group(2))
                if no not in seen and name:
                    seen.add(no)
                    groups.append((no, name))
        if not groups:
            return

        def _members(grp):
            no, name = grp
            dh = _get(f"{_BASE}/sise_group_detail.naver",
                      params={"type": "upjong", "no": no})
            res = []
            if dh:
                for im in _ITEM_RE.finditer(dh):
                    res.append((im.group(1), name))
            return res

        out: dict = {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            for members in ex.map(_members, groups):
                for code, name in members:
                    out.setdefault(code, name)   # 한 종목 = 첫 업종(소속 1개)
        if out:
            _cache_write(_KR_IND_CACHE, out)
    except Exception as exc:
        log.warning("kr industry map build: %s", exc)
    finally:
        with _kr_ind_lock:
            _kr_ind_building = False


def kr_industry_map() -> dict:
    """{6자리 종목코드 → 업종명(한글)} — 네이버 업종 그룹 멤버 스캔. 7일 캐시 +
    SWR 백그라운드(렌더 블로킹 0). 첫 방문 시 빌드 kick 후 빈/스테일 반환, 이후
    방문부터 채워짐. graceful {}."""
    fp = _CACHE_DIR / _KR_IND_CACHE
    raw = None
    try:
        if fp.exists():
            raw = json.loads(fp.read_text())
            if time.time() - fp.stat().st_mtime < _KR_IND_TTL:
                return raw
    except Exception:
        raw = None
    global _kr_ind_building
    with _kr_ind_lock:
        if not _kr_ind_building:
            _kr_ind_building = True
            threading.Thread(target=_build_kr_industry_map, daemon=True,
                             name="kr-industry-map").start()
    return raw or {}


def apply_kr_industry(items: list) -> list:
    """KR 항목 리스트에 ind(업종 한글) 백필 — ticker '005930.KS' → 코드 005930 →
    kr_industry_map. in-place·순수(맵 1회 조회)·graceful. 사용자 2026-06-14."""
    if not items:
        return items
    try:
        imap = kr_industry_map()
    except Exception:
        return items
    if not imap:
        return items
    for it in items:
        if it.get("ind"):
            continue
        code = str(it.get("ticker") or "").split(".")[0]
        ind = imap.get(code)
        if ind:
            it["ind"] = ind
    return items


def fetch_sector_movers(top_n: int = 10) -> dict:
    """업종 등락률 → {'up': [...], 'down': [...], 'ts': iso}. 장중 30초 캐시."""
    c = _cached("upjong.json")
    if c is not None:
        return c
    html = _get(f"{_BASE}/sise_group.naver", params={"type": "upjong"})
    groups = parse_groups(html) if html else []
    if not groups:
        return {"up": [], "down": [], "ts": ""}
    ups = sorted([s for s in groups if s["pct"] > 0],
                 key=lambda x: x["pct"], reverse=True)[:top_n]
    downs = sorted([s for s in groups if s["pct"] < 0],
                   key=lambda x: x["pct"])[:top_n]
    out = {"up": ups, "down": downs,
           "ts": _now_kst_label()}
    _cache_write("upjong.json", out)
    return out


def _first_big(cells: list) -> Optional[float]:
    """행에서 첫 큰 숫자(5자리+) — 고객예탁금 추정."""
    for cc in cells[1:]:
        m = re.search(r"[\d,]{5,}", cc)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


# 신용잔고 현실 범위(억원) — 잘못된 컬럼 매칭 값 차단 가드
_CRED_MIN, _CRED_MAX = 150000.0, 800000.0

# deposit.json 산출 스키마 버전 — 필드 추가/변경 시 +1 (구버전 캐시 1회 무효화)
_DEPOSIT_SCHEMA_V = 3   # 2=코스피/코스닥 신용 분리 · 3=예탁증권담보융자 (2026-07-08)


def _fsc_date(d: str) -> str:
    return f"{d[:4]}.{d[4:6]}.{d[6:8]}" if d and len(d) >= 8 else (d or "")


def _fetch_deposit_fsc() -> dict:
    """FSC(금융투자협회) 공식 API → 고객예탁금·신용잔고 둘 다 견고하게.

    Naver 탭 구조가 불안정해 신용잔고가 안 나오던 문제 해소(사용자 2026-06-10).
    값=억원, 일별 시계열 ~6개월. 키(DATA_GO_KR_API_KEY) 부재/실패 시 {}."""
    try:
        from bot import fsc_client
        dser = fsc_client.deposit_series_eok(130)
        cser = fsc_client.credit_series_eok(130)
    except Exception as exc:
        log.warning("deposit FSC failed: %s", exc)
        return {}
    out: dict = {}
    if dser:
        out["date"] = _fsc_date(dser[-1][0])
        out["deposit"] = round(dser[-1][1], 1)
        if len(dser) >= 2:
            out["deposit_chg"] = round(dser[-1][1] - dser[-2][1], 1)
        out["deposit_series"] = [{"d": _fsc_date(d), "v": round(v, 1)} for d, v in dser]
    if cser:
        out["credit"] = round(cser[-1][1], 1)
        if len(cser) >= 2:
            out["credit_chg"] = round(cser[-1][1] - cser[-2][1], 1)
        out["credit_series"] = [{"d": _fsc_date(d), "v": round(v, 1)} for d, v in cser]
    # 시장별(코스피/코스닥) 신용잔고 — 같은 KOFIA 신용공여 응답의 시장 필드
    # (사용자 2026-07-06 '왼쪽처럼 코스피·코스닥 신용잔고 추가'). 필드명
    # 런타임 발견(fsc_client) — 미발견 시 키 자체가 없어 위젯 graceful 생략.
    try:
        split = fsc_client.credit_split_series_eok(130)
    except Exception as exc:
        log.warning("credit split fetch failed: %s", exc)
        split = {}
    for mkt, prefix in (("kospi", "credit_kospi"), ("kosdaq", "credit_kosdaq")):
        ser = split.get(mkt) or []
        if not ser:
            continue
        out[prefix] = round(ser[-1][1], 1)
        if len(ser) >= 2:
            out[f"{prefix}_chg"] = round(ser[-1][1] - ser[-2][1], 1)
        out[f"{prefix}_series"] = [{"d": _fsc_date(d), "v": round(v, 1)}
                                   for d, v in ser]
    # 예탁증권담보융자 — 6번째 지표(사용자 2026-07-08 '하나 더'). 같은 신용
    # 공여 응답 필드라 추가 호출 0. graceful.
    try:
        coll = fsc_client.collateral_loan_series_eok(130)
    except Exception as exc:
        log.warning("collateral loan fetch failed: %s", exc)
        coll = []
    if coll:
        out["collateral"] = round(coll[-1][1], 1)
        if len(coll) >= 2:
            out["collateral_chg"] = round(coll[-1][1] - coll[-2][1], 1)
        out["collateral_series"] = [{"d": _fsc_date(d), "v": round(v, 1)}
                                    for d, v in coll]
    if out:
        # 출처 정직화 (2026-06-10 사용자 review): 데이터는 FSC(금융투자협회
        # 종합통계)인데 위젯/차트 라벨이 'Naver' 하드코딩 — 06.05(FSC 최신)
        # vs Naver 페이지 06.08 비교 혼란의 절반이 이 라벨. 렌더가 이 필드로
        # 실제 출처 + 데이터 일자를 표기.
        out["source"] = "금융투자협회"
    return out


def fetch_deposit() -> dict:
    """고객예탁금·신용잔고 → {date, deposit, credit, deposit_chg, credit_chg,
    deposit_series, credit_series}. 억원. 3h TTL(세션-인지 아님, 2026-08-03 —
    KOFIA T-1 공표가 KR 장 마감 이후·저녁에도 나올 수 있어 fsc_client.
    _kofia_series 의 안쪽 3h 캐시와 같은 리듬으로 하루종일 재확인).

    1차 FSC(금융투자협회 공식 API — 둘 다 견고·일별 시계열), 실패 시 Naver
    sise_deposit 폴백(고객예탁금만 견고, 신용은 컬럼 가드)."""
    c = _cached("deposit.json", ttl=3 * 3600)
    # 스키마 버전 게이트(2026-07-08): 세션-인지 캐시는 장 밖에서 무기한
    # fresh 라 새 필드(코스피/코스닥 신용 분리)가 다음 장까지 안 나타남 —
    # 구버전 산출본이면 miss 취급해 1회 재생성(재생성분엔 _v 기록).
    if c is not None and c.get("_v") == _DEPOSIT_SCHEMA_V:
        return c
    out = _fetch_deposit_fsc()
    if not out or out.get("deposit") is None:
        out = _fetch_deposit_naver()
    # 주식형펀드 — 네이버 trendDeposit/chart 보조(사용자 2026-06-14, 예탁금/신용은
    # FSC 유지). 신용잔고 옆 3번째 차트(렌더 스캐폴드는 이미 merged).
    try:
        ef = _fetch_equity_fund_naver()
        if ef and isinstance(out, dict):
            out["equity_fund"] = round(ef[-1][1], 1)
            if len(ef) >= 2:
                out["equity_fund_chg"] = round(ef[-1][1] - ef[-2][1], 1)
            out["equity_fund_series"] = [{"d": _fsc_date(d), "v": round(v, 1)}
                                         for d, v in ef]
            out.setdefault("source", "금융투자협회")
    except Exception as exc:
        log.warning("equity fund merge failed: %s", exc)
    if isinstance(out, dict) and out:
        out["_v"] = _DEPOSIT_SCHEMA_V
    _cache_write("deposit.json", out)
    return out


def _fetch_equity_fund_naver() -> list:
    """네이버 trendDeposit/chart → 주식형펀드(beneficiaryCertificateStock) 시계열
    [(bizdate, 억원)] (사용자 2026-06-14, VM probe 구조 확정). 예탁금/신용도 같은
    응답에 있으나 위젯은 FSC 유지, 주식형펀드만 보조 추가. graceful []."""
    from bot.finviz_client import naver_paused
    if naver_paused():
        return []
    import requests
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=220)
    url = ("https://stock.naver.com/api/domestic/market/trendDeposit/chart"
           f"?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}")
    try:
        r = requests.get(url, timeout=12, headers={
            "User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Referer": "https://stock.naver.com/"})
        rows = r.json() if r.status_code == 200 else []
    except Exception as exc:
        log.warning("naver equity fund fetch failed: %s", exc)
        return []
    out = []
    for it in rows if isinstance(rows, list) else []:
        d = str(it.get("bizdate") or "")
        try:
            v = float(str(it.get("beneficiaryCertificateStock")).replace(",", ""))
        except Exception:
            v = None
        if len(d) == 8 and v is not None:
            out.append((d, v))    # 억원
    return sorted(out)


def _fetch_deposit_naver() -> dict:
    """Naver sise_deposit 폴백 — 고객예탁금 견고(첫 큰 숫자), 신용은 가드."""
    out: dict = {}
    html = _get(f"{_BASE}/sise_deposit.naver")
    if html:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I)
        cred_idx = None
        for row in rows:                       # 헤더에서 신용잔고 컬럼 인덱스
            cells = _cell_texts(row)
            if any("고객예탁금" in cc for cc in cells):
                for i, cc in enumerate(cells):
                    if cred_idx is None and "신용" in cc:
                        cred_idx = i
                break

        def _num(cells: list, idx) -> Optional[float]:
            if idx is not None and idx < len(cells):
                m = re.search(r"-?[\d,]{4,}", cells[idx])
                if m:
                    try:
                        return float(m.group(0).replace(",", ""))
                    except ValueError:
                        return None
            return None

        data_rows = [c for row in rows
                     for c in [_cell_texts(row)]
                     if c and re.match(r"\d{2,4}[.\-/]\d{1,2}[.\-/]\d{1,2}", c[0])]
        if data_rows:
            cur = data_rows[0]
            prev = data_rows[1] if len(data_rows) > 1 else None
            dep = _first_big(cur)
            cred = _num(cur, cred_idx)
            if cred is not None and not (_CRED_MIN <= cred <= _CRED_MAX):
                cred = cred_idx = None          # 비현실 → 잘못된 컬럼, 생략
            out = {"date": cur[0], "deposit": dep, "credit": cred}
            if prev:
                pd = _first_big(prev)
                pc = _num(prev, cred_idx)
                if dep is not None and pd is not None:
                    out["deposit_chg"] = round(dep - pd, 1)
                if cred is not None and pc is not None:
                    out["credit_chg"] = round(cred - pc, 1)
            # 시계열(오래된→최신, ~6개월) — 그래프용
            dser, cser = [], []
            for cells in reversed(data_rows[:130]):
                dv = _first_big(cells)
                if dv is not None:
                    dser.append({"d": cells[0], "v": dv})
                cv = _num(cells, cred_idx)
                if cv is not None and _CRED_MIN <= cv <= _CRED_MAX:
                    cser.append({"d": cells[0], "v": cv})
            out["deposit_series"] = dser
            out["credit_series"] = cser
    if out:
        out["source"] = "Naver"   # 폴백 경로 — 라벨 정직화
    return out


def fetch_themes() -> dict:
    """테마별 시세 → {'themes': [{name, no, pct, pct3, leaders}] 등락률 내림차순,
    'ts'}. 장중 30초 캐시. 여러 페이지(전 테마) 수집."""
    c = _cached("theme.json")
    if c is not None:
        return c
    themes: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 8):  # 테마 ~200개 → 페이지네이션
        html = _get(f"{_BASE}/theme.naver", params={"page": page})
        if not html:
            break
        page_rows = parse_themes_full(html)
        if not page_rows:
            break
        for t in page_rows:
            if t["no"] in seen:
                continue
            seen.add(t["no"])
            themes.append(t)
    themes.sort(key=lambda x: (x.get("pct") is None, -(x.get("pct") or 0)))
    out = {"themes": themes,
           "ts": _now_kst_label() if themes else ""}
    _cache_write("theme.json", out)
    return out


# ── 상한가·하한가 (신고가/신저가 페이지가 불안정해 대체 — 사용자 2026-06-10) ──
# Naver: sise_upper.naver(상한가) / sise_lower.naver(하한가) — 표준 시세 페이지.
def _parse_stock_rows(html: str, limit: int) -> list[dict]:
    """종목 표(상한/하한/등락 공통) → [{code, name, price, pct}]."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        a = _ITEM_RE.search(row)
        if not a:
            continue
        code, name = a.group(1), _clean(a.group(2))
        if code in seen:
            continue
        cells = _cell_texts(row)
        price = ""
        for cterm in cells:
            if re.fullmatch(r"[\d,]{3,}", cterm):
                price = cterm
                break
        pct = _pct_from_row(row)
        # 거래량(사용자 2026-06-13) = 등락률(%) 셀 다음의 콤마 정수 (Naver
        # sise_upper 컬럼순서: 현재가·전일비·등락률·거래량·시가·고가·저가).
        # %가 셀 텍스트에 없을 수 있어 graceful None (못 찾으면 '—').
        vol = None
        after_pct = False
        for cterm in cells:
            if "%" in cterm:
                after_pct = True
                continue
            if after_pct and re.fullmatch(r"[\d,]{4,}", cterm):
                try:
                    vol = int(cterm.replace(",", ""))
                except ValueError:
                    vol = None
                break
        seen.add(code)
        out.append({"code": code, "name": name, "price": price,
                    "pct": round(pct, 2) if pct is not None else None,
                    "vol": vol})
        if len(out) >= limit:
            break
    return out


def fetch_upper_lower(limit: int = 50) -> dict:
    """상한가·하한가 → {'upper': [...], 'lower': [...], 'ts'}. 장중 30초 캐시. graceful."""
    c = _cached("upper_lower.json")
    if c is not None:
        return c
    out = {"upper": [], "lower": [], "ts": ""}
    for key, page in (("upper", "sise_upper.naver"), ("lower", "sise_lower.naver")):
        html = _get(f"{_BASE}/{page}")
        out[key] = _parse_stock_rows(html, limit) if html else []
    if out["upper"] or out["lower"]:
        out["ts"] = _now_kst_label()
    _cache_write("upper_lower.json", out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mv = fetch_sector_movers()
    print(f"업종 상승 {len(mv['up'])} / 하락 {len(mv['down'])}")
    th = fetch_themes()
    print(f"테마 {len(th['themes'])}")
    for t in th["themes"][:5]:
        print("  ", t["name"], t["pct"])
    ul = fetch_upper_lower()
    print(f"상한가 {len(ul['upper'])} / 하한가 {len(ul['lower'])}")
