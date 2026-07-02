"""거버넌스 브리핑 (/gov) — DART 지배구조·지분·주총·주주환원 요약.

open-proxy-mcp(github.com/MarcoYou/open-proxy-mcp) 검토(2026-07-04)에서 아이디어만
채택 — 코드 이식 없음(PolyForm NC), 우리 DART 스택으로 네이티브 구현(키 로컬 유지,
제3자 서버 전송 0). 텔레그램·대시보드 명령 콘솔 공용(단일 레지스트리 등록).

구성(전부 graceful — 소스 하나 실패해도 나머지 섹션 출력):
  ① 최대주주 현황(hyslr, 사업보고서) ② 임원·주요주주 소유(elestock 최신)
  ③ 최근 90일 거버넌스 공시(dart_feed 아카이브 — 지분·주주환원·배당·회사구조·
     소송 + 주총/경영권 키워드, 🔥 significance 표시) ④ Gemini flash 3줄 종합
     (키 없으면 skip, usage.jsonl subsystem='gov' 로 비용 합산 — 기존 NOAH
     통합 비용에 자동 포함, 별도 surface 아님).

KR(DART) 전용 — 데이터소스 사유(CLAUDE.md universal 예외). US 활동주의(13D)는
레딧 인사이더 표면이 별도 커버.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_HOME = os.path.expanduser("~")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
_USD_TO_KRW = 1330.0
_FLASH_IN, _FLASH_OUT = 0.30, 2.50   # gemini-2.5-flash $/1M tok

# 거버넌스 관련 공시 필터 — 카테고리 OR 제목 키워드 (dart_feed 분류 재사용)
_GOV_CATS = ("지분공시", "주주환원", "배당", "회사구조", "소송")
_GOV_KW = ("주주총회", "주주제안", "소집", "의결권", "경영참가", "경영권",
           "자기주식", "소각", "배당", "대량보유", "최대주주", "기업가치제고",
           "임원ㆍ주요주주", "공개매수")


def resolve_kr_target(query: str) -> tuple[str | None, str, list[dict]]:
    """질의 → (stock_code|None, name, 후보목록). 6자리 코드 직접 / 이름 검색.

    미해석 시 (None, query, candidates) — 후보 여러 개면 호출부가 목록 안내."""
    q = (query or "").strip()
    code = q.upper().split(".")[0]
    from bot.dart_client import DartClient
    dc = DartClient()
    if code.isdigit() and len(code) == 6:
        name = dc.stock_code_to_name(code) or code
        return code, name, []
    hits = [h for h in dc.find_by_name(q) if h.get("stock_code")]
    if len(hits) == 1:
        return hits[0]["stock_code"], hits[0].get("name", q), []
    # 정확 일치 1건이 앞에 오는 구조 — 첫 히트 이름이 질의와 동일(정규화)이면 채택
    if hits and hits[0].get("name", "").replace(" ", "") == q.replace(" ", ""):
        return hits[0]["stock_code"], hits[0]["name"], hits
    return None, q, hits


def _gov_disclosures(stock_code: str, days_back: int = 90,
                     limit: int = 10) -> list[dict]:
    """dart_feed 아카이브에서 이 종목의 거버넌스 공시(최신순 limit)."""
    from bot.dart_feed import load_all_archives
    out: list[dict] = []
    try:
        archives = load_all_archives(days_back=days_back)
    except Exception:
        return []
    for date_str in sorted(archives, reverse=True):
        for it in archives[date_str]:
            if str(it.get("stock_code", "")) != str(stock_code):
                continue
            rn = it.get("report_nm", "")
            if (it.get("category") in _GOV_CATS
                    or any(k in rn for k in _GOV_KW)):
                it = dict(it)
                it["_date"] = date_str
                out.append(it)
        if len(out) >= limit:
            break
    return out[:limit]


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v or "—")


def _llm_summary(name: str, context: str) -> str | None:
    """Gemini flash 3줄 종합 — 키 없음/실패 시 None (브리핑은 그대로 출력)."""
    try:
        from bot.genai_factory import effective_key
        api_key = effective_key()
        if not api_key:
            return None
        from bot.screener import _call_pro
        prompt = (
            f"다음은 한국 상장사 {name}의 DART 지배구조 데이터다. 3줄 이내로 "
            "지배구조 특징(지분 집중/분산, 대주주 성격)과 최근 거버넌스 신호"
            "(활동주의·주주환원·분쟁 등)를 한국어로 요약해라. 투자 권유 금지, "
            "데이터에 없는 내용 금지, 서두/맺음말 없이 요점만.\n\n" + context)
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        if not text:
            return None
        cost_usd = (pt * _FLASH_IN + ot * _FLASH_OUT) / 1e6
        try:
            os.makedirs(os.path.dirname(_NOAH_USAGE_LOG), exist_ok=True)
            with open(_NOAH_USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": _time.time(), "type": "llm_call",
                    "model": "gemini-2.5-flash", "prompt_tokens": pt,
                    "completion_tokens": ot, "cost_usd": round(cost_usd, 6),
                    "subsystem": "gov"}, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("gov: usage log failed: %s", exc)
        return text.strip()
    except Exception as exc:
        log.warning("gov: LLM summary failed: %s", exc)
        return None


def build_gov_brief(query: str, with_llm: bool = True) -> str:
    """/gov 본문(HTML, 텔레그램 4096 안전) — 순수 조립, 소스별 graceful."""
    code, name, cands = resolve_kr_target(query)
    if not code:
        if cands:
            lines = "\n".join(
                f"· {_html.escape(c.get('name', ''))} "
                f"<code>{_html.escape(c.get('stock_code', ''))}</code>"
                for c in cands[:8])
            return (f"🔎 <b>{_html.escape(query)}</b> — 후보가 여러 개야. "
                    f"코드로 다시 시도해줘 (/gov 005930):\n{lines}")
        return (f"⚠️ <b>{_html.escape(query)}</b> 를 KR 상장사로 못 찾았어. "
                "/gov 는 DART 기반 <b>한국 종목 전용</b>이야 — 6자리 코드 "
                "또는 정확한 회사명으로 시도해줘. (미국 내부자/활동주의는 "
                "📨 미국 레딧 대시보드 참고)")

    from bot.dart_client import DartClient
    dc = DartClient()
    now = datetime.now(_KST)
    parts: list[str] = [
        f"🏛 <b>{_html.escape(name)}</b> <code>{code}</code> 거버넌스 브리핑",
        f"<i>기준 {now:%Y-%m-%d %H:%M} KST · 출처 DART</i>",
    ]

    ctx_lines: list[str] = []   # LLM 컨텍스트 (plain)

    try:
        majors = dc.get_major_shareholders(code) or []
    except Exception:
        majors = []
    if majors:
        parts.append("\n👑 <b>최대주주 현황</b> (사업보고서)")
        for r in majors[:6]:
            nm = _html.escape(str(r.get("name", ""))[:20])
            rel = _html.escape(str(r.get("relation", "") or ""))
            pct = _fmt_pct(r.get("pct"))
            seg = f"· {nm} — {pct}" + (f" ({rel})" if rel else "")
            parts.append(seg)
            ctx_lines.append(f"대주주 {r.get('name')} {pct} {rel}")

    try:
        insiders = dc.get_insider_holdings(code) or []
    except Exception:
        insiders = []
    if insiders:
        parts.append("\n👔 <b>임원·주요주주 소유</b> (최신 보고)")
        for r in insiders[:5]:
            nm = _html.escape(str(r.get("name", ""))[:20])
            role = _html.escape(str(r.get("role", "") or ""))
            pct = _fmt_pct(r.get("pct"))
            parts.append(f"· {nm} {role} — {pct}")
            ctx_lines.append(f"임원/주요주주 {r.get('name')} {role} {pct}")

    discl = _gov_disclosures(code)
    if discl:
        from bot.dart_feed import significance
        parts.append("\n📢 <b>최근 거버넌스 공시</b> (90일)")
        for it in discl:
            rn = str(it.get("report_nm", ""))
            try:
                sig = significance(it)
            except Exception:
                sig = None
            fire = f" 🔥{_html.escape(sig)}" if sig else ""
            parts.append(f"· {it['_date'][5:]} {_html.escape(rn[:44])}{fire}")
            ctx_lines.append(
                f"공시 {it['_date']} {rn}" + (f" [중요: {sig}]" if sig else ""))
    else:
        parts.append("\n📢 최근 90일 거버넌스 공시 없음 (피드 아카이브 기준)")

    if with_llm and ctx_lines:
        summary = _llm_summary(name, "\n".join(ctx_lines[:60]))
        if summary:
            parts.append("\n🤖 <b>종합</b> (Gemini flash)")
            parts.append(_html.escape(summary)[:700])

    parts.append("\n📊 공시 상세: 대시보드 → DART 공시 (종목명 검색)")
    text = "\n".join(parts)
    if len(text) > 3900:   # 텔레그램 4096 안전 마진
        text = text[:3900] + "\n… (생략)"
    return text
