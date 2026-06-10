"""US 상장 전 종목 회사명 디렉토리 — NASDAQ Trader 공식 심볼 파일 (무료·무키).

실적 캘린더 'BKHA(...)' 표기용: S&P500 명단(finviz)만으로는 소형주 이름이
비어 '일부만 이름' 문제(사용자 2026-06-11). NASDAQ Trader 의 공개 심볼
디렉토리 2개(nasdaqlisted/otherlisted, pipe 구분 텍스트)로 NYSE/NASDAQ/AMEX
전 종목(~8천) 커버. 7일 디스크 캐시, 실패 시 빈 dict(호출부가 S&P500 폴백).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("bot.us_symbol_names")

_CACHE_FILE = (Path.home() / ".tradingagents" / "cache" / "finviz"
               / "us_symbol_names_v3.json")  # v3: Beneficial Interest 추가
_TTL = 7 * 86400

_URLS = (
    "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
)


import re as _re

# NYSE(otherlisted) 표기는 대시 없이 ' Common Stock' 등이 붙음 — GBX
# 'Greenbrier Companies, Inc. (The) Common Stock' 류(사용자 2026-06-11).
_TAIL_RE = _re.compile(
    r"\s+(?:Class\s+[A-Z]\s+)?(?:Common Stock|Ordinary Shares?|"
    r"American Depositary Shares?(?:\s*\(.*\))?|Depositary Shares?.*|"
    r"Common Shares?(?:\s+of\s+Beneficial\s+Interest)?|"
    r"Shares?\s+of\s+Beneficial\s+Interest|Units?|Warrants?)\s*$",
    _re.IGNORECASE)


def _clean_name(raw: str) -> str:
    """'Apple Inc. - Common Stock' / 'Greenbrier Companies, Inc. (The)
    Common Stock' → 회사명만 (NASDAQ 대시형 + NYSE 무대시형 suffix 제거,
    '(The)' 정리)."""
    name = (raw or "").strip()
    # 1) NASDAQ 형: ' - ' 뒤 = 증권 종류 설명 → 첫 세그먼트
    if " - " in name:
        name = name.split(" - ", 1)[0].strip()
    # 2) NYSE 형: 대시 없는 종류 suffix 제거 (반복 — 'Class A Common Stock')
    for _ in range(2):
        new = _TAIL_RE.sub("", name).strip()
        if new == name:
            break
        name = new or name
    # 3) '(The)' 후치 관사 — 'Greenbrier Companies, Inc. (The)' → 제거
    name = _re.sub(r"\s*\(The\)\s*", " ", name).strip().rstrip(",")
    return name


def us_symbol_names() -> dict:
    """{SYMBOL: 회사명} — 전 미국 상장. 7d 캐시, 실패 시 {}."""
    try:
        if (_CACHE_FILE.exists()
                and time.time() - _CACHE_FILE.stat().st_mtime < _TTL):
            return json.loads(_CACHE_FILE.read_text("utf-8"))
    except Exception:
        pass
    names: dict = {}
    try:
        import httpx
        for url in _URLS:
            try:
                r = httpx.get(url, timeout=20, follow_redirects=True)
                if r.status_code != 200 or not r.text:
                    continue
                lines = r.text.splitlines()
                if not lines:
                    continue
                header = lines[0].split("|")
                try:
                    si = next(i for i, h in enumerate(header)
                              if h.strip() in ("Symbol", "ACT Symbol"))
                    ni = next(i for i, h in enumerate(header)
                              if h.strip() == "Security Name")
                except StopIteration:
                    continue
                for ln in lines[1:]:
                    parts = ln.split("|")
                    if len(parts) <= max(si, ni):
                        continue
                    sym = parts[si].strip()
                    if not sym or sym.startswith("File Creation"):
                        continue
                    nm = _clean_name(parts[ni])
                    if nm:
                        names[sym] = nm
            except Exception as exc:
                log.warning("us_symbol_names: %s fetch failed: %s", url, exc)
    except Exception as exc:
        log.warning("us_symbol_names: unavailable: %s", exc)
    if len(names) > 1000:
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps(names, ensure_ascii=False),
                                   encoding="utf-8")
        except Exception:
            pass
    return names
