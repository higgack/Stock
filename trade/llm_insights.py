"""산업트렌드 '🔍 데이터가 말하는 추가 신호' — Gemini 2.5 Pro.

원본 reference의 '본문에서 따로 잡아야 할 투자 신호'는 산업부 보도자료
본문(프로즈)에서 LLM이 뽑은 것이지만, 우리 소스(관세청 OpenAPI)는 숫자
뿐이다. 같은 취지를 재해석: 우리 전체 집계(20산업+MTI+수입+YoY/MoM/TTM)를
압축 다이제스트로 LLM에 주고, 표준 카드·표가 '가리는' 비자명 신호(표 밖
유망품목·산업 간 선행/후행·산업 내 믹스 반전·동반 급등 소재군)를 카드로
받는다.

비용·안전 가드:
  - 데이터 변동 시에만 호출(refresh_signals) → 사실상 월 1~2회.
  - 킬스위치 ``TRADE_LLM_INSIGHTS=0``, 일 호출 상한
    ``TRADE_LLM_MAX_CALLS_PER_DAY``(기본 10).
  - GOOGLE_API_KEY(또는 GEMINI_API_KEY) 없거나 lib 미설치 → 조용히 [].
  - 어떤 예외든 [] 반환 → 파이프라인/렌더 안 깨짐.
  - 호출은 llm_usage에 토큰·비용 기록.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os

from trade import llm_usage

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("TRADE_LLM_MODEL") or "gemini-2.5-pro"
_MAX_CARDS = 6

_SYS_PROMPT = (
    "당신은 한국 수출입(관세청 확정치) 데이터를 읽는 통상·산업 애널리스트입니다. "
    "아래는 산업/세부품목(MTI)별 월 수출·수입과 YoY·MoM·ΔYoY·TTM 요약입니다. "
    "대시보드 표준 표(산업 카드·급등률·급증액)에 '이미 드러난' 사실은 빼고, "
    "표가 가리는 '비자명한 신호'만 3~6개 뽑으세요. 예: 표 밖 유망품목, 산업 간 "
    "선행/후행(수입 자본재↑→생산 선행), 산업 내 믹스 반전(총액↓이나 특정 세부품목↑), "
    "동반 급등 소재군. 각 카드는 한국어, title(12자 내)·body(2문장 내). "
    "오직 JSON 배열만 출력하세요: [{\"title\":\"...\",\"body\":\"...\"}]"
)


def _enabled() -> bool:
    return (os.environ.get("TRADE_LLM_INSIGHTS") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def _api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _max_calls() -> int:
    try:
        return int(os.environ.get("TRADE_LLM_MAX_CALLS_PER_DAY") or "10")
    except ValueError:
        return 10


def _parse(text: str) -> list[dict]:
    """LLM 출력(JSON 배열, 코드펜스 허용) → [{title, body}]. 견고하게."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1 and s[:nl].strip().lower() in ("json", ""):
            s = s[nl + 1:]
    data = None
    try:
        data = json.loads(s)
    except Exception:
        i, j = s.find("["), s.rfind("]")
        if i != -1 and j > i:
            try:
                data = json.loads(s[i:j + 1])
            except Exception:
                data = None
    cards = []
    for it in (data if isinstance(data, list) else []):
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        b = str(it.get("body") or "").strip()
        if t and b:
            cards.append({"title": t[:40], "body": b[:300]})
        if len(cards) >= _MAX_CARDS:
            break
    return cards


def generate(digest: str, *, model: str | None = None) -> list[dict]:
    """다이제스트 → 신호 카드 리스트. 비활성/키없음/상한/오류 시 []."""
    model = model or DEFAULT_MODEL
    if not _enabled():
        log.info("TRADE_LLM_INSIGHTS off — skip insights")
        return []
    if not _api_key():
        log.info("no GOOGLE_API_KEY — skip LLM insights")
        return []
    if not (digest or "").strip():
        return []
    if llm_usage.calls_today() >= _max_calls():
        log.warning("LLM daily call cap reached — skip insights")
        return []
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        log.warning("langchain_google_genai not installed — skip LLM insights")
        return []
    try:
        llm = ChatGoogleGenerativeAI(
            model=model, temperature=0.4, google_api_key=_api_key())
        resp = llm.invoke([("system", _SYS_PROMPT), ("human", digest)])
        um = getattr(resp, "usage_metadata", None) or {}
        llm_usage.record(model, um.get("input_tokens", 0), um.get("output_tokens", 0))
        return _parse(getattr(resp, "content", "") or "")
    except Exception as exc:  # network / quota / parse-upstream — never break pipeline
        log.warning("LLM insight generation failed: %s", exc)
        return []


def build_digest(by_ind, by_imp, by_mti, by_mti_imp) -> str:
    """저장된 집계 → 토큰 절약 텍스트 다이제스트(산업 최신지표 + 수입 YoY +
    MTI 수출 MoM 무버 + MTI 수입 YoY 무버)."""
    from trade import industry

    lines: list[str] = []
    series = industry.industry_series(by_ind) if by_ind else {}
    imp_series = industry.industry_series(by_imp) if by_imp else {}

    def f(v, suf="%"):
        return "—" if v is None else f"{v:+.0f}{suf}"

    rows = []
    for ind, pts in series.items():
        if not pts:
            continue
        p = pts[-1]
        prev = next((q["exp"] for q in pts
                     if q["ym"] == industry._prev_month(p["ym"])), None)
        mom = ((p["exp"] - prev) / prev * 100.0) if (prev and prev > 0) else None
        ip = imp_series.get(ind) or []
        imp_yoy = ip[-1].get("yoy") if ip else None
        rows.append((p["exp"], ind, p, mom, imp_yoy))
    rows.sort(reverse=True)
    if rows:
        lines.append("[산업] 수출억$ YoY MoM ΔYoY TTMyoy | 수입YoY")
        for exp, ind, p, mom, imp_yoy in rows[:25]:
            lines.append(
                f"{ind} {exp/1e8:.0f} {f(p.get('yoy'))} {f(mom)} "
                f"{f(p.get('dyoy'),'%p')} {f(p.get('ttm_yoy'))} | {f(imp_yoy)}")

    if by_mti:
        mti_rows = []
        for m6, node in by_mti.items():
            pts = industry.industry_series({m6: node["months"]}).get(m6) or []
            if not pts:
                continue
            p = pts[-1]
            prev = next((q["exp"] for q in pts
                         if q["ym"] == industry._prev_month(p["ym"])), None)
            mom = ((p["exp"] - prev) / prev * 100.0) if (prev and prev > 0) else None
            if mom is not None and (p["exp"] or 0) >= 100_000_000:
                mti_rows.append((mom, node.get("name", m6),
                                 node.get("industry", ""), p["exp"]))
        mti_rows.sort(reverse=True)
        if mti_rows:
            lines.append("[MTI 수출 MoM 상위]")
            for mom, nm, ind, exp in mti_rows[:15]:
                lines.append(f"{nm}({ind}) {exp/1e8:.0f}억 MoM{mom:+.0f}%")

    if by_mti_imp:
        imp_rows = []
        for m6, node in by_mti_imp.items():
            pts = industry.industry_series({m6: node["months"]}).get(m6) or []
            if not pts:
                continue
            y = pts[-1].get("yoy")
            cur = pts[-1].get("exp") or 0
            if y is not None and cur >= 100_000_000:
                imp_rows.append((y, node.get("name", m6),
                                 node.get("industry", ""), cur))
        imp_rows.sort(reverse=True)
        if imp_rows:
            lines.append("[MTI 수입 YoY 상위(자본재·소재 선행)]")
            for y, nm, ind, cur in imp_rows[:12]:
                lines.append(f"{nm}({ind}) {cur/1e8:.0f}억 YoY{y:+.0f}%")

    return "\n".join(lines)


def render_html(cards: list[dict]) -> str:
    """🔍 추가신호 박스 HTML. cards 비면 ''(박스 미표시)."""
    if not cards:
        return ""
    items = "".join(
        f"<div class='ins-card'><h4>{_html.escape(str(c['title']))}</h4>"
        f"<p>{_html.escape(str(c['body']))}</p></div>"
        for c in cards if c.get("title") and c.get("body")
    )
    if not items:
        return ""
    return (
        "<section class='ins-box'>"
        "<h3>🔍 데이터가 말하는 추가 신호</h3>"
        "<div class='ins-sub'>표준 표에 안 드러난 비자명 신호 · "
        "Gemini가 데이터 변동 시 갱신</div>"
        f"<div class='ins-grid'>{items}</div></section>"
    )
