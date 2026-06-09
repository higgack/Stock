"""Naver 증권 시세 스크래퍼 — 업종/테마 등락 + 신고가·신저가.

- fetch_sector_movers(): sise_group.naver?type=upjong → 업종 상승/하락 TOP.
- fetch_themes(): sise/theme.naver → 테마별 등락(별도 페이지).
- fetch_high_low(): 52주 신고가·신저가(별도 페이지, best-effort).

무료·무키. euc-kr 디코딩. graceful — 실패/빈 결과 시 빈값. 사용자 정책
2026-06-10: 리스크 없는 한 가장 빠르게 → 4분 캐시.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.naver_sector")

_BASE = "https://finance.naver.com/sise"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_sector"
_CACHE_TTL_SEC = 4 * 60  # 리스크 없이 빠르게(1 HTTP/요청) — 사용자 2026-06-10

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


def _get(url: str, **kwargs) -> Optional[str]:
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


def _cached(name: str):
    cache_file = _CACHE_DIR / name
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _CACHE_TTL_SEC:
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


def fetch_sector_movers(top_n: int = 10) -> dict:
    """업종 등락률 → {'up': [...], 'down': [...], 'ts': iso}. 4분 캐시."""
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
           "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime())}
    _cache_write("upjong.json", out)
    return out


def fetch_deposit() -> dict:
    """고객예탁금·신용잔고 (sise_deposit.naver) → {date, deposit, credit,
    deposit_chg, credit_chg}. 단위 억원(추정). 4분 캐시. best-effort·graceful.

    ⚠️ Naver 페이지 컬럼 구조 미검증 — 헤더 매칭 + 첫 큰 숫자 폴백. 빗나가면
    부분/빈값(호출부가 위젯 생략)."""
    c = _cached("deposit.json")
    if c is not None:
        return c
    out: dict = {}
    html = _get(f"{_BASE}/sise_deposit.naver")
    if html:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I)
        dep_idx = cred_idx = None
        for row in rows:                       # 헤더 행에서 컬럼 인덱스 매칭
            cells = _cell_texts(row)
            if any("고객예탁금" in cc for cc in cells):
                for i, cc in enumerate(cells):
                    if dep_idx is None and "고객예탁금" in cc:
                        dep_idx = i
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

        data_rows = []
        for row in rows:                       # 날짜로 시작하는 데이터 행
            cells = _cell_texts(row)
            if cells and re.match(r"\d{2,4}[.\-/]\d{1,2}[.\-/]\d{1,2}", cells[0]):
                data_rows.append(cells)
        if data_rows:
            cur = data_rows[0]
            prev = data_rows[1] if len(data_rows) > 1 else None
            dep = _num(cur, dep_idx)
            if dep is None:                    # 폴백: 첫 큰 숫자 = 고객예탁금
                for cc in cur[1:]:
                    m = re.search(r"[\d,]{5,}", cc)
                    if m:
                        dep = float(m.group(0).replace(",", ""))
                        break
            cred = _num(cur, cred_idx)
            out = {"date": cur[0], "deposit": dep, "credit": cred}
            if prev:
                pd, pc = _num(prev, dep_idx), _num(prev, cred_idx)
                if dep is not None and pd is not None:
                    out["deposit_chg"] = round(dep - pd, 1)
                if cred is not None and pc is not None:
                    out["credit_chg"] = round(cred - pc, 1)
    _cache_write("deposit.json", out)
    return out


def fetch_themes() -> dict:
    """테마별 시세 → {'themes': [{name, pct}] 등락률 내림차순, 'ts'}. 4분 캐시."""
    c = _cached("theme.json")
    if c is not None:
        return c
    html = _get(f"{_BASE}/theme.naver")
    themes = parse_groups(html) if html else []
    themes.sort(key=lambda x: x["pct"], reverse=True)
    out = {"themes": themes,
           "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime()) if themes else ""}
    _cache_write("theme.json", out)
    return out


# ── 52주 신고가·신저가 ───────────────────────────────────────────────
# Naver: sise_high_low.naver?type=high|low (gubun=signal 52주). best-effort —
# 페이지 구조/파라미터가 빗나가도 graceful 빈 리스트(호출부가 '데이터 없음').
def _parse_high_low(html: str) -> list[dict]:
    """신고가/신저가 종목 표 → [{code, name, price, pct}]."""
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
            if re.fullmatch(r"[\d,]{2,}", cterm):
                price = cterm
                break
        pct = _pct_from_row(row)
        seen.add(code)
        out.append({"code": code, "name": name, "price": price,
                    "pct": round(pct, 2) if pct is not None else None})
    return out


def fetch_high_low(limit: int = 40) -> dict:
    """52주 신고가·신저가 → {'high': [...], 'low': [...], 'ts'}. 4분 캐시.

    ⚠️ Naver 페이지 파라미터 best-effort — 빗나가면 빈 리스트(graceful)."""
    c = _cached("high_low.json")
    if c is not None:
        return c
    out = {"high": [], "low": [], "ts": ""}
    # type=up/down 또는 gubun 파라미터 후보 — 첫 plausible 결과 채택
    for key, params in (("high", {"type": "up", "gubun": "high52"}),
                        ("low", {"type": "down", "gubun": "low52"})):
        html = _get(f"{_BASE}/sise_high_low.naver", params=params)
        rows = _parse_high_low(html)[:limit] if html else []
        out[key] = rows
    if out["high"] or out["low"]:
        out["ts"] = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    _cache_write("high_low.json", out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mv = fetch_sector_movers()
    print(f"업종 상승 {len(mv['up'])} / 하락 {len(mv['down'])}")
    th = fetch_themes()
    print(f"테마 {len(th['themes'])}")
    for t in th["themes"][:5]:
        print("  ", t["name"], t["pct"])
    hl = fetch_high_low()
    print(f"신고가 {len(hl['high'])} / 신저가 {len(hl['low'])}")
