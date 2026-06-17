"""기업 중심 보고서 — 회사 → DART 제품 구성 + 관세청 수출입 품목 노출 (사용자
2026-06-17 '별도 버튼으로 보고서 뽑기, 무료+유료').

두 모드:
  • free  : 순수 데이터 조립 — DART 매출구성(G1) + 그 회사가 연결된 관세청 품목의
            수출입 추세. **LLM 0·₩0.**
  • llm   : free 데이터에 Gemini 산문 요약(해석)을 얹음 — 사용자 명시 opt-in(클릭),
            비용 발생(trade.llm_usage 기록). 실패/키부재 시 free 로 graceful 강등.

데이터 출처(전부 기보유): 회사→제품 = trade.dart_revenue(G1, DART 사업보고서),
회사→관세청 품목 노출 = mti_companies(품목→회사 수동·채널) 역조회 + industry.load_
mti_stored(저장된 수출입 집계). 신규 fetch·매칭 비용 0. graceful(데이터 없으면 빈 섹션).
"""
from __future__ import annotations

import html as _html
import logging
import os

log = logging.getLogger("trade.company_report")


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


def _eok_usd(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x / 1e8:,.1f}억$" if x else "—"


def _company_exposure(name: str, by_mti: dict, pairs: list) -> list[dict]:
    """회사명 → 연결된 관세청 품목 [{item, industry, latest_usd}] (수출액 내림차순).
    mti_companies(수동+채널) 역조회. 순수(단위테스트)."""
    from trade import mti_companies
    target = _norm(name)
    if not target:
        return []
    out: list[dict] = []
    for mti6, node in (by_mti or {}).items():
        item = (node.get("name") or mti6).strip()
        cos = set(mti_companies.companies_for(item)) | set(
            mti_companies.channel_companies_for(item, pairs))
        if not any(_norm(c) == target or target in _norm(c) or _norm(c) in target
                   for c in cos if c):
            continue
        months = node.get("months") or {}
        latest = months[max(months)] if months else None
        out.append({"item": item, "industry": node.get("industry", ""),
                    "latest_usd": latest})
    out.sort(key=lambda x: -(x["latest_usd"] or 0))
    return out[:20]


def gather(query: str, api_key: str | None = None) -> dict:
    """회사(이름 또는 6자리 코드) → 보고서 데이터 {query, code, name, products,
    exposure}. 네트워크: DART(corp_code·매출표). graceful."""
    from bot.dart_client import get_dart
    dart = get_dart()
    q = (query or "").strip()
    code, name = None, q
    digits = q.upper().split(".")[0]
    if digits.isdigit() and len(digits) == 6:
        code = digits
        name = dart.stock_code_to_name(code) or q
    else:
        try:
            hits = dart.find_by_name(q)
        except Exception:
            hits = []
        if hits:
            code, name = hits[0].get("stock_code"), hits[0].get("name") or q
    products = []
    if code:
        try:
            from trade.dart_revenue import fetch_company_products
            r = fetch_company_products(code, api_key)
            if r:
                products = r.get("products", [])
        except Exception as exc:
            log.warning("company_report DART %s: %s", code, exc)
    exposure: list[dict] = []
    try:
        from trade import customs, industry, mti_companies
        with customs.session() as conn:
            by_mti = industry.load_mti_stored(conn)
        pairs = mti_companies.load_channel_pairs()
        exposure = _company_exposure(name, by_mti, pairs)
    except Exception as exc:
        log.warning("company_report exposure %s: %s", name, exc)
    return {"query": q, "code": code, "name": name,
            "products": products, "exposure": exposure}


def render_free(data: dict) -> str:
    """데이터 → 자체완결 HTML 보고서(인라인 스타일·₩0). 순수."""
    e = _html.escape
    name = e(data.get("name") or data.get("query") or "—")
    code = e(data.get("code") or "")
    products = data.get("products") or []
    exposure = data.get("exposure") or []
    head = (f'<div style="font-size:18px;font-weight:700;margin-bottom:2px">{name}'
            f'{" · " + code if code else ""}</div>'
            '<div style="font-size:12px;color:#9aa0aa;margin-bottom:12px">'
            '기업 중심 보고서 · DART 매출구성 + 관세청 수출입 품목 노출 · 무료(데이터)</div>')
    # 제품 구성 (DART)
    if products:
        prows = "".join(
            f'<tr><td style="padding:4px 8px">{e(p.get("name",""))}</td>'
            f'<td style="padding:4px 8px;text-align:right">'
            f'{(str(p["share_pct"]) + "%") if p.get("share_pct") is not None else "—"}</td></tr>'
            for p in products[:15])
        prod_html = ('<div style="font-weight:600;margin:10px 0 4px">📦 제품 구성 (DART 매출비중)</div>'
                     '<table style="border-collapse:collapse;font-size:13px;width:100%">'
                     '<thead><tr><th style="text-align:left;padding:4px 8px;color:#9aa0aa">제품</th>'
                     '<th style="text-align:right;padding:4px 8px;color:#9aa0aa">매출비중</th></tr></thead>'
                     f'<tbody>{prows}</tbody></table>')
    else:
        prod_html = ('<div style="color:#9aa0aa;font-size:13px;margin:10px 0">📦 제품 구성 — '
                     'DART 매출표 미확보(비상장·해외·미발견)</div>')
    # 관세청 노출
    if exposure:
        erows = "".join(
            f'<tr><td style="padding:4px 8px">{e(x["item"])}</td>'
            f'<td style="padding:4px 8px;color:#9aa0aa">{e(x.get("industry",""))}</td>'
            f'<td style="padding:4px 8px;text-align:right">{_eok_usd(x.get("latest_usd"))}</td></tr>'
            for x in exposure)
        exp_html = ('<div style="font-weight:600;margin:14px 0 4px">🚢 관세청 수출입 품목 노출 '
                    '(최신월 수출액순)</div>'
                    '<table style="border-collapse:collapse;font-size:13px;width:100%">'
                    '<thead><tr><th style="text-align:left;padding:4px 8px;color:#9aa0aa">품목</th>'
                    '<th style="text-align:left;padding:4px 8px;color:#9aa0aa">산업</th>'
                    '<th style="text-align:right;padding:4px 8px;color:#9aa0aa">최신월 수출</th></tr></thead>'
                    f'<tbody>{erows}</tbody></table>')
    else:
        exp_html = ('<div style="color:#9aa0aa;font-size:13px;margin:14px 0">🚢 관세청 노출 — '
                    '매핑된 품목 없음(mti_companies 큐레이션·채널 미등록)</div>')
    return f'<div style="line-height:1.5">{head}{prod_html}{exp_html}</div>'


def _llm_digest(data: dict) -> str:
    """LLM 입력용 압축 텍스트(데이터만 — 환각 차단)."""
    lines = [f"회사: {data.get('name')} ({data.get('code') or '비상장/해외'})"]
    if data.get("products"):
        lines.append("제품(매출비중): " + ", ".join(
            f"{p.get('name')}({p['share_pct']}%)" if p.get("share_pct") is not None
            else str(p.get("name")) for p in data["products"][:12]))
    if data.get("exposure"):
        lines.append("관세청 수출입 품목 노출(최신월 수출): " + ", ".join(
            f"{x['item']}={_eok_usd(x.get('latest_usd'))}" for x in data["exposure"][:12]))
    return "\n".join(lines)


_LLM_SYS = (
    "너는 한국 수출입·기업 애널리스트다. 아래 '데이터'(DART 매출구성 + 관세청 수출입 "
    "품목 노출)만 근거로 5~7문장 한국어 요약을 써라. 규칙: (1) 주어진 수치/품목만 사용, "
    "새 숫자·사건 날조 금지 (2) 주력 제품·매출 집중도, 수출입 품목 노출(수혜/부담 방향은 "
    "데이터 범위 내에서만 중립 서술) (3) 투자 권유 금지·교육 목적. 마크다운 없이 평문."
)


def render_llm(data: dict, model: str | None = None) -> tuple[str, dict]:
    """free 보고서 + Gemini 산문 요약. (html, meta{used, cost_note}). 키부재/실패 시
    free 로 graceful (used=False)."""
    free = render_free(data)
    try:
        from trade import llm_insights, llm_usage
        if not llm_insights._api_key():
            return (free + _note("⚠️ AI 요약 생략 — GOOGLE_API_KEY 없음(무료 보고서만)."),
                    {"used": False})
        from langchain_google_genai import ChatGoogleGenerativeAI
        mdl = model or llm_insights.DEFAULT_MODEL
        llm = ChatGoogleGenerativeAI(model=mdl, temperature=0.3,
                                     google_api_key=llm_insights._api_key())
        resp = llm.invoke([("system", _LLM_SYS), ("human", _llm_digest(data))])
        um = getattr(resp, "usage_metadata", None) or {}
        try:
            llm_usage.record(mdl, um.get("input_tokens", 0), um.get("output_tokens", 0))
        except Exception:
            pass
        txt = _html.escape((getattr(resp, "content", "") or "").strip())
        if not txt:
            return (free + _note("⚠️ AI 요약 비어있음 — 무료 보고서만."), {"used": False})
        ai = ('<div style="font-weight:600;margin:14px 0 4px">🤖 AI 요약 (Gemini · 유료)</div>'
              f'<div style="font-size:13px;background:#1c1f26;border:1px solid #2a2e37;'
              f'border-radius:8px;padding:10px 12px;white-space:pre-wrap">{txt}</div>')
        return (free + ai, {"used": True, "model": mdl})
    except Exception as exc:
        log.warning("company_report LLM: %s", exc)
        return (free + _note(f"⚠️ AI 요약 실패({exc.__class__.__name__}) — 무료 보고서만."),
                {"used": False})


def _note(msg: str) -> str:
    return (f'<div style="font-size:12px;color:#ff9500;margin-top:12px">'
            f'{_html.escape(msg)}</div>')


def render_telegram(data: dict, ai_text: str = "") -> str:
    """보고서 → 텔레그램 HTML(≤4096, 표 대신 줄 목록). 채널 전송용. 순수."""
    e = _html.escape
    name = e(data.get("name") or data.get("query") or "—")
    code = e(data.get("code") or "")
    lines = [f"🏢 <b>{name}</b>{(' · ' + code) if code else ''} — 기업 보고서",
             "DART 매출구성 + 관세청 수출입 품목 노출", ""]
    products = data.get("products") or []
    if products:
        lines.append("📦 <b>제품 구성</b> (DART 매출비중)")
        for p in products[:12]:
            sh = f" ({p['share_pct']}%)" if p.get("share_pct") is not None else ""
            lines.append(f"• {e(str(p.get('name','')))}{sh}")
    else:
        lines.append("📦 제품 구성 — DART 매출표 미확보")
    lines.append("")
    exposure = data.get("exposure") or []
    if exposure:
        lines.append("🚢 <b>관세청 수출입 품목 노출</b> (최신월 수출)")
        for x in exposure[:12]:
            lines.append(f"• {e(x['item'])} — {_eok_usd(x.get('latest_usd'))}")
    else:
        lines.append("🚢 관세청 노출 — 매핑된 품목 없음")
    if ai_text:
        lines += ["", "🤖 <b>AI 요약</b>", e(ai_text)]
    out = "\n".join(lines)
    return out[:4000]


def send_to_channel(query: str, mode: str = "free", api_key: str | None = None) -> dict:
    """보고서를 trade 텔레그램 채널로 전송 (사용자 2026-06-17 '버튼→채널'). free=₩0,
    llm=AI 요약 포함(유료). {ok, sent}."""
    data = gather(query, api_key)
    ai_text = ""
    if mode == "llm":
        try:
            from trade import llm_insights
            if llm_insights._api_key():
                from langchain_google_genai import ChatGoogleGenerativeAI
                from trade import llm_usage
                mdl = llm_insights.DEFAULT_MODEL
                resp = ChatGoogleGenerativeAI(
                    model=mdl, temperature=0.3,
                    google_api_key=llm_insights._api_key()).invoke(
                        [("system", _LLM_SYS), ("human", _llm_digest(data))])
                um = getattr(resp, "usage_metadata", None) or {}
                try:
                    llm_usage.record(mdl, um.get("input_tokens", 0), um.get("output_tokens", 0))
                except Exception:
                    pass
                ai_text = (getattr(resp, "content", "") or "").strip()
        except Exception as exc:
            log.warning("send_to_channel LLM: %s", exc)
    body = render_telegram(data, ai_text)
    ids = [int(x) for x in (os.environ.get("TRADE_CHANNEL_CHAT_IDS") or "").split(",")
           if x.strip()]
    if not ids:
        return {"ok": False, "error": "TRADE_CHANNEL_CHAT_IDS 미설정"}
    sent = 0
    try:
        from trade.scripts import customs_alert
        for cid in ids:
            if customs_alert._send(cid, body):
                sent += 1
    except Exception as exc:
        log.warning("send_to_channel: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": sent > 0, "sent": sent}


def build(query: str, mode: str = "free", api_key: str | None = None) -> str:
    """query + mode('free'|'llm') → 보고서 HTML."""
    data = gather(query, api_key)
    if mode == "llm":
        return render_llm(data)[0]
    return render_free(data)


def main(argv: list[str] | None = None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="기업 중심 보고서 (회사→제품+관세청 노출)")
    p.add_argument("query", help="회사명 또는 6자리 코드 (예: 삼성전자 / 005930)")
    p.add_argument("--llm", action="store_true", help="AI 산문 요약 추가(유료)")
    args = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        for c in (Path.home() / "stock" / ".env", Path.cwd() / ".env"):
            if c.exists():
                load_dotenv(c, override=False)
    except Exception:
        pass
    data = gather(args.query)
    print(f"회사: {data['name']} ({data.get('code') or '—'}) | 제품 {len(data['products'])} | "
          f"관세청 노출 {len(data['exposure'])}")
    html = render_llm(data)[0] if args.llm else render_free(data)
    out = os.path.join("/tmp", f"company_report_{data.get('code') or 'x'}.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"💾 {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
