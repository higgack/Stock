"""분기 실적분석 — 성장동력·리스크 카드 (DART 공시 원문 근거 LLM 요약).

분기 실적분석 인포그래픽(bot/quarterly_infographic.py)의 정성 카드 2종
("확인된 성장동력" / "지속조건·무효화 리스크")과 헤드라인 한 줄을 생성한다.

⚠️ 근거는 **DART 정기보고서 원문**뿐이다. 참고 레퍼런스(X 게시물)는 증권사
리포트를 사람이 큐레이션해 인용했지만 우리는 그 원문에 접근할 API 가 없다 —
없는 리포트를 인용한 것처럼 쓰면 그게 곧 날조다. 출처 라벨은 항상 "DART 공시".

흐름: find_periodic_report(rcept_no) → dart_feed._fetch_doc_text(원문 평문)
→ extract_business_narrative(사업의 내용 섹션만) → Gemini flash-lite 1콜
→ {headline, risk_subline, growth_drivers[], sustain_risks[]}.

비용: 분기보고서 1건당 1콜(=분기당 1회). 캐시 키가 (ticker, rcept_no) 라
같은 분기 보고서를 다시 볼 땐 재과금 0 — 날짜 캐시가 아니라 **보고서 캐시**
(분기 데이터는 하루 단위로 바뀌지 않으므로).

DATA OFFLINE 가드: 키 부재·원문 미수신·섹션 매칭 실패·JSON 파싱 실패 중
어느 하나라도 걸리면 {"ok": False} 를 돌려주고, 렌더러는 **해당 카드 섹션을
통째로 생략**한다(빈 카드로 형식만 채우는 fabrication 금지).
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("bot.dart_growth_risk")

_CACHE_DIR = Path.home() / ".tradingagents" / "quarterly_llm_cache"
_USAGE_LOG = Path.home() / ".tradingagents" / "usage.jsonl"
# technical_analysis 와 동일 티어 — 이 작업은 "판단"이 아니라 주어진 원문에서
# 근거 문장을 뽑아 요약하는 것이라 flash-lite 로 충분(비용 최소).
_MODEL = "gemini-2.5-flash-lite"
# 캐시 스키마 버전 — 파일명에 들어가 **bump 하면 기존 요약이 전부 무효화**된다.
#   v2 (2026-08-16) 항목 상한 4→6 + 프롬프트에 개수 지시 추가(사용자 확정
#      '전부 재생성'). ⚠️ 일괄 재과금은 아니다 — build_growth_risk 는
#      run_llm=False(평상시 로드)면 캐시 미스에도 LLM 을 부르지 않고
#      {"ok": False, "error": "not_run"} 을 돌려준다. 대시보드가 '생성(AI 1회)'
#      버튼을 다시 띄우고, 사용자가 누른 종목만 1회씩 과금된다.
_SCHEMA_VER = "v2"

# "사업의 내용" 섹션 시작 마커. DART 정기보고서는 회사마다 목차 번호 서식이
# 제각각(로마숫자 Ⅱ / ASCII II / 아라비아 2)이라 **번호를 빼고 제목 텍스트로**
# 찾는다. 공백 변형(사업 의 내용)도 흡수하도록 정규식.
_SEC_START = re.compile(r"사\s*업\s*의\s*내\s*용")
# 다음 섹션(= 추출 종료점) 후보. "재무에 관한 사항"이 사업의 내용 바로 다음
# 오는 표준 순서(기업공시서식 작성기준).
_SEC_END = re.compile(r"재\s*무\s*에\s*관\s*한\s*사\s*항"
                      r"|이\s*사\s*의\s*경\s*영\s*진\s*단"
                      r"|감\s*사\s*인\s*의\s*감\s*사\s*의\s*견")
_MIN_CHARS = 200      # 이보다 짧으면 목차 줄만 잡힌 오탐으로 간주


def extract_business_narrative(text: str | None, max_chars: int = 6000) -> str | None:
    """공시 원문 평문 → "사업의 내용" 섹션 텍스트. 실패 시 None.

    ⚠️ 신뢰도 주의: DART 원문 XML 은 표준 스키마가 없고 회사마다 목차 서식이
    달라 이 문자열 매칭이 깨질 수 있다. 실패는 None 으로 조용히 흘리되(호출부가
    카드 생략), 성공률은 VM 표본 측정으로 따로 확인할 것 — 낮으면 dart-fss
    같은 전용 파서 도입을 재검토.

    목차(TOC)에도 같은 제목이 나오므로 **마지막 매칭**을 본문으로 본다(목차는
    문서 앞부분, 본문은 뒤). 매칭 뒤 다음 섹션 헤더까지 자르고, 너무 짧으면
    (목차 줄만 잡힌 경우) 그 매칭은 버리고 이전 매칭을 시도한다."""
    if not text:
        return None
    starts = [m.end() for m in _SEC_START.finditer(text)]
    if not starts:
        return None
    for s in reversed(starts):          # 뒤(본문)부터 시도
        m_end = _SEC_END.search(text, s)
        chunk = text[s:m_end.start()] if m_end else text[s:s + max_chars]
        chunk = chunk.strip()
        if len(chunk) >= _MIN_CHARS:
            return chunk[:max_chars]
    return None


def _cache_file(ticker: str, rcept_no: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (ticker or "").upper())
    return _CACHE_DIR / f"{safe}__{rcept_no}__{_SCHEMA_VER}.json"


def cached_summary(ticker: str, rcept_no: str) -> dict | None:
    """이 분기 보고서에 대해 이미 산출된 요약(있으면). 비용 0."""
    if not rcept_no:
        return None
    f = _cache_file(ticker, rcept_no)
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8"))
        except Exception:
            return None
    return None


def _log_usage(pt: int, ot: int) -> None:
    try:
        from bot.usage_tracker import estimate_cost_usd
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "type": "llm_call", "model": _MODEL,
               "prompt_tokens": pt, "completion_tokens": ot,
               "cost_usd": round(estimate_cost_usd(_MODEL, pt, ot), 6),
               "subsystem": "quarterly_infographic"}
        with open(_USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("dart_growth_risk: usage log fail: %s", exc)


def _prompt(ticker: str, company: str, business_text: str,
            quarterly_context: dict) -> str:
    return (
        "너는 DART 공시 원문만 근거로 쓰는 애널리스트다. 아래 규칙을 어기면 실패다.\n"
        "1) '공시 원문 발췌'에 실제로 있는 사실만 근거로 써라. 원문에 없는 전망·\n"
        "   경쟁사 비교·업황 예측을 지어내지 마라.\n"
        "2) 수치는 아래 '분기 재무(사실)'에 이미 주어진 것만 인용해라. 그 외 숫자를\n"
        "   새로 만들거나 계산해 쓰지 마라.\n"
        "3) 특정 증권사 리포트를 인용한 것처럼 쓰지 마라(우리는 그 원문이 없다).\n"
        "   근거 출처는 오직 DART 공시다.\n"
        "4) 근거가 부족한 항목은 억지로 채우지 말고 빈 배열([])로 둬라. 항목 수를\n"
        "   맞추려고 일반론('시장 변동성 주의' 등)을 넣지 마라.\n"
        f"5) 한국어 자연 문체, 각 항목 1문장({ITEM_CHARS}자 이내), 번역체·기계적\n"
        "   병렬 회피. 넘으면 카드에서 말줄임(…)되어 문장이 끊긴다.\n"
        "6) growth_drivers = 원문이 실제로 서술한 성장 동인. sustain_risks = 그\n"
        "   성장이 유지되려면 필요한 조건 또는 무효화 요인.\n"
        f"7) 두 배열은 각각 최대 {MAX_ITEMS}개. 근거가 있는 만큼만 써라 —\n"
        "   개수를 채우려고 늘리지 마라(규칙 4 가 우선한다).\n\n"
        f"종목: {company or ''} ({ticker})\n"
        f"분기 재무(사실, 이 값만 인용 가능): "
        f"{json.dumps(quarterly_context, ensure_ascii=False)}\n\n"
        f"공시 원문 발췌(사업의 내용):\n{business_text}\n\n"
        "아래 JSON 스키마로만 출력(설명·코드펜스 금지):\n"
        '{"headline":"이번 분기 실적 한 줄 요약(사실 기반, 25자 내외)",'
        '"risk_subline":"확인할 리스크 한 줄(25자 내외)",'
        '"growth_drivers":["성장동력 1문장", "..."],'
        '"sustain_risks":["지속조건/무효화 리스크 1문장", "..."]}'
    )


def _parse_json(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except Exception:
        return None


# 카드 항목 상한 — **여기 하나가 정본**. 인포그래픽 렌더러도 이 값을 임포트해
# 슬라이스와 상자 높이를 잡는다. 옛 코드는 생산 단계 `cap=4` 와 렌더 단계
# `items[:4]` 가 따로 박혀 있어 한쪽만 바꾸면 조용히 어긋나는 표면이었다
# (사용자 2026-08-16 '최대 4번까지만 나오게 한거야?'). 4 → 6 (사용자 확정).
MAX_ITEMS = 6
# 항목 1개의 글자 수 상한 — 인포그래픽 카드 한 줄에 들어가는 실측 한계에서
# 왔다(카드 텍스트 가용폭 38.9 단위 = 812px, 8pt 기준 34자 782px / 38자
# 874px 넘침). 프롬프트가 이보다 길게 요구하면(옛 '40자 내외') 렌더러가
# 말줄임해 **문장이 중간에 끊긴다** — 두 숫자를 여기서 함께 잡는다.
# 렌더러 절단은 34자, 프롬프트는 그보다 짧게 요구해 말줄임을 예외로 만든다.
ITEM_CHARS = 32


def _clean_list(v, cap: int = MAX_ITEMS) -> list:
    """LLM 출력 정규화 — 문자열 리스트만, 빈 항목 제거, 최대 cap 개."""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = str(x).strip()
        if s and s.lower() not in ("none", "null", "n/a", "-"):
            out.append(s)
        if len(out) >= cap:
            break
    return out


def summarize_growth_risk(ticker: str, rcept_no: str, business_text: str,
                          quarterly_context: dict, company: str = "",
                          *, force: bool = False) -> dict:
    """원문 발췌 → Gemini 1콜 → 성장동력/리스크 카드. 캐시 우선(재과금 0).

    반환 {ok: True, headline, risk_subline, growth_drivers, sustain_risks,
    source, cached} 또는 {ok: False, error}. 실패 시 호출부는 카드 섹션을
    **생략**해야 한다(빈 카드 렌더 금지)."""
    if not force:
        c = cached_summary(ticker, rcept_no)
        if c:
            c["cached"] = True
            return c
    if not business_text:
        return {"ok": False, "error": "공시 원문 섹션 추출 실패(DATA OFFLINE)"}
    try:
        from bot.genai_factory import effective_key, make_client
        if not effective_key():
            return {"ok": False, "error": "AI 키 미설정(DATA OFFLINE)"}
        client = make_client()
        prompt = _prompt(ticker, company, business_text, quarterly_context)
        try:
            from google.genai import types as _t
            cfg = _t.GenerateContentConfig(response_mime_type="application/json")
            resp = client.models.generate_content(model=_MODEL, contents=prompt,
                                                  config=cfg)
        except Exception:
            resp = client.models.generate_content(model=_MODEL, contents=prompt)
    except Exception as exc:
        log.warning("dart_growth_risk: LLM call %s: %s", ticker, exc)
        return {"ok": False, "error": "LLM 호출 실패"}
    text = (getattr(resp, "text", None) or "").strip()
    pt = ot = 0
    try:
        um = getattr(resp, "usage_metadata", None)
        if um:
            pt = int(getattr(um, "prompt_token_count", 0) or 0)
            ot = int(getattr(um, "candidates_token_count", 0) or 0)
    except Exception:
        pass
    _log_usage(pt, ot)
    data = _parse_json(text)
    if not data:
        return {"ok": False, "error": "응답 파싱 실패"}
    out = {
        "ok": True,
        "headline": str(data.get("headline") or "").strip(),
        "risk_subline": str(data.get("risk_subline") or "").strip(),
        "growth_drivers": _clean_list(data.get("growth_drivers")),
        "sustain_risks": _clean_list(data.get("sustain_risks")),
        # 화면·인포그래픽 출처 라벨 — 증권사 리포트 인용으로 오인되지 않게 고정.
        "source": "DART 공시",
        "rcept_no": rcept_no,
        "cached": False,
    }
    if not (out["growth_drivers"] or out["sustain_risks"] or out["headline"]):
        return {"ok": False, "error": "근거 부족(모든 항목 비어 있음)"}
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(ticker, rcept_no).write_text(
            json.dumps(out, ensure_ascii=False), "utf-8")
    except Exception as exc:
        log.warning("dart_growth_risk: cache write fail: %s", exc)
    return out


def build_growth_risk(dart, ticker: str, year: int, reprt_code: str,
                      quarterly_context: dict, company: str = "",
                      *, run_llm: bool = False) -> dict:
    """오케스트레이션 — rcept_no 조회 → 원문 → 섹션 추출 → (선택)LLM.

    run_llm=False(기본)면 **비용 0**: 이미 캐시된 요약이 있으면 그것만 돌려주고
    없으면 {"ok": False, "error": "not_run"}. 사용자가 명시적으로 실행할 때만
    run_llm=True 로 과금(대시보드 &run=1 게이트, technical_analysis 패턴)."""
    code = (ticker or "").upper().split(".")[0]
    rep = dart.find_periodic_report(code, year, reprt_code) if dart else None
    if not rep or not rep.get("rcept_no"):
        return {"ok": False, "error": "정기보고서 rcept_no 미확인"}
    rcept_no = rep["rcept_no"]
    cached = cached_summary(ticker, rcept_no)
    if cached:
        cached["cached"] = True
        return cached
    if not run_llm:
        return {"ok": False, "error": "not_run", "rcept_no": rcept_no}
    try:
        from bot.dart_feed import _fetch_doc_text
        api_key = getattr(dart, "api_key", "") or ""
        raw = _fetch_doc_text(rcept_no, api_key)
    except Exception as exc:
        log.warning("dart_growth_risk: doc fetch %s: %s", rcept_no, exc)
        raw = None
    narrative = extract_business_narrative(raw)
    if not narrative:
        return {"ok": False, "error": "공시 원문 섹션 추출 실패(DATA OFFLINE)",
                "rcept_no": rcept_no}
    return summarize_growth_risk(ticker, rcept_no, narrative,
                                 quarterly_context, company)
