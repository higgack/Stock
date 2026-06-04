"""포트폴리오 저장·집계·요약 (자산관리 P1 증분3).

흐름: parse_export(뱅샐 zip/xlsx) → resolve_ticker(종목명→티커) → 집계 모델 →
저장(JSON). 대시보드(증분4)·텔레그램 핸들러가 이 모델을 소비. 라이브 가격·
NOAH 분석 오버레이는 증분4·5에서 모델 위에 얹는다.

저장: ~/.tradingagents/portfolio.json (atomic). 파서가 1.고객정보·가계부를 애초에
제외하므로 저장 데이터엔 이름/이메일/소비내역이 없다 — 보유종목·자산군·순자산·
대출·보험만. (PII 최소화.)

순수 함수(build_model/format/_won)는 단위테스트 가능. ingest 만 I/O.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from bot.portfolio_parser import parse_export
from bot.portfolio_resolve import resolve_ticker

PORTFOLIO_PATH = Path.home() / ".tradingagents" / "portfolio.json"


def _won(v) -> str:
    """₩ 금액 → 억/만 약식(가독). None→'-'. (차트 fmtAxis 와 동일 철학.)"""
    if v is None:
        return "-"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    neg = n < 0
    n = abs(n)
    if n >= 1e8:
        s = f"{n / 1e8:.1f}억"
    elif n >= 1e4:
        s = f"{n / 1e4:,.0f}만"
    else:
        s = f"{n:,.0f}"
    return ("-" if neg else "") + s + "원"


def build_model(parsed: dict, resolve=resolve_ticker) -> dict:
    """파서 결과 → 대시보드/요약용 집계 모델.

    - holdings: 각 보유에 ticker/market/matched + 평가손익(평가금액−투자원금) 부착.
    - by_broker: 증권사별 평가금액·원금·손익·종목수.
    - asset_allocation: 재무현황 자산 카테고리별 합(예적금/투자/부동산/동산/연금…).
    - net_worth: 총자산/총부채/순자산(파서가 항목 합으로 산출).
    - top_gainers/losers: 수익률 정렬 상·하위 5.
    """
    holdings = []
    for h in parsed.get("holdings", []):
        r = resolve(h.get("상품명") or "")
        ev, cost = h.get("평가금액"), h.get("투자원금")
        pnl = (ev - cost) if (ev is not None and cost is not None) else None
        holdings.append({
            **h, "ticker": r["ticker"], "market": r["market"],
            "matched": r["matched"], "평가손익": pnl,
        })
    by_broker: dict[str, dict] = {}
    for h in holdings:
        b = h.get("금융사") or "?"
        d = by_broker.setdefault(b, {"평가금액": 0.0, "투자원금": 0.0, "종목수": 0})
        d["평가금액"] += h.get("평가금액") or 0.0
        d["투자원금"] += h.get("투자원금") or 0.0
        d["종목수"] += 1
    for d in by_broker.values():
        d["평가손익"] = d["평가금액"] - d["투자원금"]
    fin = parsed.get("finance", {})
    alloc: dict[str, float] = {}
    for cat, items in fin.get("assets", {}).items():
        tot = sum(it.get("amount") or 0.0 for it in items)
        if tot:
            alloc[cat] = tot
    rated = [h for h in holdings if h.get("수익률") is not None]
    return {
        "as_of": parsed.get("as_of"),
        "net_worth": {
            "총자산": fin.get("총자산"), "총부채": fin.get("총부채"),
            "순자산": fin.get("순자산"),
        },
        "holdings": holdings,
        "by_broker": by_broker,
        "asset_allocation": alloc,
        "liabilities": fin.get("liabilities", {}),
        "loans": parsed.get("loans", []),
        "insurance": parsed.get("insurance", []),
        "top_gainers": sorted(rated, key=lambda h: h["수익률"], reverse=True)[:5],
        "top_losers": sorted(rated, key=lambda h: h["수익률"])[:5],
        "holding_count": len(holdings),
        "matched_count": sum(1 for h in holdings if h["matched"]),
    }


def format_summary_text(model: dict) -> str:
    """텔레그램 회신용 한 화면 요약 (증권사별·자산배분)."""
    nw = model.get("net_worth", {})
    lines = [
        "📂 자산 요약 (뱅크샐러드 기준)",
        f"순자산 {_won(nw.get('순자산'))}  "
        f"(자산 {_won(nw.get('총자산'))} − 부채 {_won(nw.get('총부채'))})",
        f"주식 {model.get('holding_count', 0)}종목 · 티커매칭 {model.get('matched_count', 0)}",
    ]
    if model.get("by_broker"):
        lines.append("— 증권사별 —")
        for b, d in sorted(model["by_broker"].items(), key=lambda kv: -kv[1]["평가금액"]):
            lines.append(f"• {b}: {_won(d['평가금액'])} ({d['종목수']}종목, 손익 {_won(d['평가손익'])})")
    if model.get("asset_allocation"):
        lines.append("— 자산 배분 —")
        for cat, amt in sorted(model["asset_allocation"].items(), key=lambda kv: -kv[1]):
            lines.append(f"• {cat}: {_won(amt)}")
    if model.get("loans"):
        lines.append(f"— 대출 {len(model['loans'])}건 · 보험 {len(model.get('insurance', []))}건 —")
    return "\n".join(lines)


def save(model: dict) -> None:
    """atomic write to portfolio.json."""
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {**model, "_saved_ts": time.time()}
    tmp = PORTFOLIO_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PORTFOLIO_PATH)


def load() -> dict | None:
    try:
        return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def ingest(data, password=None) -> dict:
    """zip/xlsx(바이트 또는 경로) → 파싱·resolve·집계 → 저장. 모델 반환.

    텔레그램 핸들러(증분3 wiring)·CLI 가 호출. password 기본은 호출부가 .env
    BANKSALAD_ZIP_PW 에서 주입(코드/깃에 비번 박지 않음)."""
    parsed = parse_export(data, password=password)
    model = build_model(parsed)
    save(model)
    return model
