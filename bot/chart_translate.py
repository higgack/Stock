"""차트 공시 제목 CN/JP/TW → 한국어 번역 (Gemini Flash, 영구 캐시).

CN/JP/TW 공시 제목은 자유 텍스트라 사전 완역 불가 → Flash 로 번역. 비용은 **영구
디스크 캐시**로 최소화(제목당 1회만 번역 → 이후 ₩0) + **배치**(여러 제목 한 콜).
graceful: GOOGLE_API_KEY 부재/실패 시 {} 반환(원문 유지). KR(원래 한국어)·US(고정
사전 완역)는 호출 안 함 — chart_events 가 CN/JP/TW 에서만 호출.

비용: Flash, 제목당 토큰 소액 + 영구 캐시라 단일 채널 기준 무시 가능. usage.jsonl 에
subsystem='chart_translate' 로 기록(메인 대시보드 총합에 포함, 분석 버킷으로 폴딩).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_HOME = Path.home() / ".tradingagents"
_CACHE = _HOME / "chart_title_kr.json"
_USAGE = _HOME / "usage.jsonl"
_USD_TO_KRW = 1330.0
_FLASH_IN, _FLASH_OUT = 0.30, 2.50   # gemini-2.5-flash $/M
_MAX_BATCH = 40                       # 한 콜당 최대 제목 수(토큰 bound)


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
                                "subsystem": "chart_translate"}) + "\n")
    except OSError:
        pass


_IND_CACHE = _HOME / "industry_en.json"


def _load_ind() -> dict:
    try:
        return json.loads(_IND_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_ind(d: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _IND_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _IND_CACHE)
    except OSError:
        pass


def translate_industries_en(names: list[str]) -> dict:
    """{한글 업종명 → 영문} — 네이버 업종(reutersIndustryName/industryGroupKor)을
    표준 영문 산업명으로(사용자 2026-06-14 '모두 영문'). Flash 배치·영구 캐시
    (industry_en.json, ~150개 1회 → 이후 ₩0). graceful(키부재/실패 시 빠짐 →
    호출부 원문 유지)."""
    uniq = [n for n in dict.fromkeys(names) if n and n.strip()]
    if not uniq:
        return {}
    cache = _load_ind()
    out = {n: cache[n] for n in uniq if cache.get(n)}
    todo = [n for n in uniq if n not in cache][:_MAX_BATCH]
    if not todo:
        return out
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = (
        "다음은 주식시장 '업종(산업) 분류명'입니다. 각 줄을 표준 영문 산업명으로 "
        "번역하세요.\n- 예: 반도체와반도체장비 → Semiconductors & Equipment, "
        "건설및엔지니어링 → Construction & Engineering, 다각화된통신서비스 → "
        "Diversified Telecommunication Services\n- 번역문만, 입력과 동일한 "
        "'번호. 번역' 형식, 같은 번호 유지. 설명 금지.\n\n" + lines)
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
            en = m.group(2).strip()
            if 0 <= idx < len(todo) and en:
                out[todo[idx]] = en
                cache[todo[idx]] = en
        _save_ind(cache)
    except Exception:
        pass
    return out


_NAME_CACHE = _HOME / "names_en.json"


def _load_name() -> dict:
    try:
        return json.loads(_NAME_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_name(d: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _NAME_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _NAME_CACHE)
    except OSError:
        pass


def translate_names_en(names: list[str]) -> dict:
    """{中文/native 회사명 → 영문} — 대만 소형주 등 yfinance longName 부재 종목을
    통용 영문 회사명으로(사용자 2026-06-14 '대만 소형주 中文→영문 번역으로 reliable
    하게'). translate_industries_en 인프라 재사용 패턴. Flash 배치·영구 캐시
    (names_en.json → 종목당 1회·이후 ₩0). graceful(키부재/실패 시 빠짐 → 호출부
    원문 유지)."""
    uniq = [n for n in dict.fromkeys(names) if n and n.strip()]
    if not uniq:
        return {}
    cache = _load_name()
    out = {n: cache[n] for n in uniq if cache.get(n)}
    todo = [n for n in uniq if n not in cache][:_MAX_BATCH]
    if not todo:
        return out
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = (
        "다음은 대만/중화권 상장사의 中文 회사명입니다. 각 줄을 통용되는 영문 "
        "회사명으로 번역하세요.\n- 예: 台積電 → TSMC, 鴻海 → Hon Hai (Foxconn), "
        "聯發科 → MediaTek, 長榮 → Evergreen Marine\n- 공식 영문명이 있으면 그것을, "
        "없으면 음역. 번역문만, 입력과 동일한 '번호. 번역' 형식, 같은 번호 유지. "
        "설명 금지.\n\n" + lines)
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
            en = m.group(2).strip()
            if 0 <= idx < len(todo) and en:
                out[todo[idx]] = en
                cache[todo[idx]] = en
        _save_name(cache)
    except Exception:
        pass
    return out


_NAME_KR_CACHE = _HOME / "names_kr.json"


def _load_name_kr() -> dict:
    try:
        return json.loads(_NAME_KR_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_name_kr(d: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _NAME_KR_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _NAME_KR_CACHE)
    except OSError:
        pass


def translate_names_kr(pairs: list) -> dict:
    """[(ticker, 현지명)…] → {ticker: 한글 회사명} — CN/TW/HK 종목명을 **영문 통용명
    기준 한글 음역**으로(사용자 2026-06-15 '화봉전 말고 윈본드'). 한자음독(화봉전)
    대신 한국 금융권 통용 표기(윈본드·폭스콘·미디어텍), 영문 약자가 더 통용되면 영문
    유지(TSMC·UMC·SMIC). Flash 배치·영구 캐시(names_kr.json, **티커당 1회** → 이후
    ₩0). graceful(키부재/실패 시 빠짐 → 호출부 원문 유지). 티커가 disambiguation
    힌트라 현지명이 한자음독이어도 정확."""
    seen: set = set()
    uniq: list = []
    for tk, nm in pairs:
        tk = str(tk or "").strip()
        if tk and tk not in seen:
            seen.add(tk)
            uniq.append((tk, str(nm or "").strip()))
    if not uniq:
        return {}
    cache = _load_name_kr()
    out = {tk: cache[tk] for tk, _ in uniq if cache.get(tk)}
    todo = [(tk, nm) for tk, nm in uniq if tk not in cache][:_MAX_BATCH]
    if not todo:
        return out
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {tk} | {nm}" for i, (tk, nm) in enumerate(todo))
    prompt = (
        "다음은 대만/중국/홍콩 상장사입니다('티커 | 현지명'). 각 줄을 한국 투자자에게 "
        "통용되는 **간결한 한글 회사명**으로 바꾸세요.\n"
        "- 영문 통용명 기준 음역: 華邦電 → 윈본드, 鴻海 → 폭스콘, 聯發科 → 미디어텍, "
        "比亞迪 → 비야디, 騰訊 → 텐센트, 阿里巴巴 → 알리바바\n"
        "- 한국에서 영문 약자로 더 통용되면 영문 유지: 台積電 → TSMC, 聯電 → UMC, "
        "中芯國際 → SMIC, 日月光 → ASE\n"
        "- 한자 음독(화봉전 등) 금지. 법인격(Corporation/股份有限公司/控股) 생략, "
        "핵심 브랜드만. 한 줄당 결과 하나, 입력과 동일한 '번호. 결과' 형식, 같은 "
        "번호 유지. 설명 금지.\n\n" + lines)
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
                tk = todo[idx][0]
                out[tk] = kr
                cache[tk] = kr
        _save_name_kr(cache)
    except Exception:
        pass
    return out


def translate_titles_kr(titles: list[str], cache_only: bool = False) -> dict:
    """[제목…] → {원문제목: 한국어}. 캐시 우선, 미캐시만 Flash 배치 번역. graceful.

    cache_only=True 면 캐시된 번역만 반환(Flash 호출 0 — 렌더-세이프 경로용,
    사용자 2026-06-16 TW 렌더 8.2s 블록 제거). 실패/키부재 시 번역 못 한 제목은
    dict 에서 빠짐(호출부가 원문 유지)."""
    uniq = [t for t in dict.fromkeys(titles) if t and t.strip()]
    if not uniq:
        return {}
    cache = _load()
    out = {t: cache[t] for t in uniq if t in cache and cache[t]}
    todo = [t for t in uniq if t not in cache][:_MAX_BATCH]
    if cache_only or not todo:        # 렌더-세이프(캐시만) 또는 전부 캐시됨
        return out
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return out  # graceful — 원문 유지
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = (
        "다음은 해외 증시 공시 제목입니다. 각 줄을 자연스럽고 간결한 한국어로 번역하세요.\n"
        "- 회사명은 한국에서 통용되는 명칭(예: 贵州茅台→귀주모태주, トヨタ→도요타)\n"
        "- 번역문만, 입력과 동일한 '번호. 번역' 형식으로 같은 번호 유지\n"
        "- 군더더기 설명 금지, 한 줄당 한 번역\n\n" + lines)
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
                cache[todo[idx]] = kr
        _save(cache)
    except Exception:
        pass
    return out
