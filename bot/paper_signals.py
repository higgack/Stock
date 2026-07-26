"""NOAH 판정 → 페이퍼 자동 주문 (E0.5b).

설계 §1 '분석 ≠ 실행 분리': analyzer 는 신호만 내고, **이 레이어가** 그
신호를 페이퍼 주문으로 변환한다(analyzer 는 paper 를 모름). `auto on` 일
때만 동작 — 자동매매는 opt-in 이 안전.

- 매수(Buy/Overweight) → 자본 `AUTO_SIZING_PCT` 사이징 페이퍼 매수(Risk Gate
  통과 시), 진입 후 `HORIZON_DAYS` 거래일 자동 청산(NOAH 평가 윈도 일치).
- 매도(Sell/Underweight) → 보유 중이면 청산.
- 보유(Hold) → 무시.
멱등: idem=auto:{ticker}:{KST date} → 같은 날 같은 종목 자동 주문 1회.
graceful — 어떤 예외도 분석 흐름을 깨지 않게 None 으로 degrade.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_AUTO_AUDIT = Path.home() / ".tradingagents" / "paper" / "auto_audit.jsonl"

AUTO_SIZING_PCT = 0.05     # 매수당 자본 비율 (Risk Gate 캡 안)
HORIZON_DAYS = 5           # 진입 후 자동 청산까지 거래일 (NOAH 5거래일 윈도)
_KST = timezone(timedelta(hours=9))

# 컨빅션 게이트(자동매매 안전): 최종 판정이 분석가·트레이더와 갈린(contested)
# 신호면 신규 매수를 자동 실행하지 않는다. 요약 카드에 아래 배너 마커가 있으면
# contested 로 본다(방향 상충 / 시스템 강제 / PM OVERRIDE 자동 차단 등). 청산
# (de-risk)은 contested 여도 진행 — Risk Gate 와 동일 철학(매도 항상 허용).
_CONTESTED_MARKERS = ("방향 상충", "시스템 강제", "PM OVERRIDE", "자동 차단",
                      "다른 결론")


def _is_contested(summary: str) -> bool:
    s = summary or ""
    return any(m in s for m in _CONTESTED_MARKERS)


def _majority_from_stance_bar(summary: str) -> Optional[str]:
    """요약 카드 stance bar(📈 시장: 매수 · 💬 감정: 보유 …)에서 분석가 다수
    방향 산출 — buy/sell/hold(strict plurality, 동률/분열 시 hold). portfolio_
    manager._analyst_majority_direction 와 동일 규칙(여기선 카드 텍스트 파싱)."""
    stances = re.findall(
        r"(?:시장|감정|뉴스|펀더멘털)\s*[:：]\s*(강?매수|강?매도|보유)", summary or "")
    if not stances:
        return None
    dirs = ["buy" if "매수" in s else ("sell" if "매도" in s else "hold") for s in stances]
    b, sl, h = dirs.count("buy"), dirs.count("sell"), dirs.count("hold")
    if b > sl and b > h:
        return "buy"
    if sl > b and sl > h:
        return "sell"
    return "hold"


def _extract_chain(rating: str, summary: str, full: str) -> dict:
    """자동매매 결정 체인 — RM 추천 / 트레이더 액션 / PM 최종 / 분석가 다수 /
    discipline 발화. RM·트레이더는 full report 섹션에서, 다수·discipline 은
    요약 카드에서 파싱. 추적성 audit 용 (어느 매니저가 뭘 말했는지 영구 기록)."""
    f, s = full or "", summary or ""
    rm = re.search(r"투자\s*계획.*?추천\s*[:：]\s*([A-Za-z가-힣/]+)", f, re.DOTALL)
    trader = re.search(r"거래\s*액션\s*[:：]\s*([A-Za-z가-힣/]+)", f)
    return {
        "rm": rm.group(1).strip() if rm else None,          # 리서치 매니저 추천
        "trader": trader.group(1).strip() if trader else None,  # 트레이더 액션
        "pm": (rating or "").strip(),                        # PM 최종(자동 신호 근거)
        "majority": _majority_from_stance_bar(s),            # 분석가 다수
        "discipline_forced": any(m in s for m in ("시스템 강제", "PM OVERRIDE", "자동 차단")),
        "contested": _is_contested(s),
    }


def _chain_str(c: dict) -> str:
    """알림용 compact 체인 — 'RM Overweight·Tr Buy·PM Hold·다수 보유'."""
    parts = []
    if c.get("rm"):
        parts.append(f"RM {c['rm']}")
    if c.get("trader"):
        parts.append(f"Tr {c['trader']}")
    parts.append(f"PM {c.get('pm') or '?'}")
    if c.get("majority"):
        parts.append(f"다수 {({'buy':'매수','sell':'매도','hold':'보유'}).get(c['majority'], c['majority'])}")
    if c.get("discipline_forced"):
        parts.append("⚖️강제보정")
    return " · ".join(parts)


def _log_auto_audit(ticker: str, chain: dict, outcome: str) -> None:
    """자동매매 결정 체인 + 결과를 audit.jsonl 에 append (추적성). graceful."""
    try:
        _AUTO_AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUTO_AUDIT, "a", encoding="utf-8") as fp:
            fp.write(json.dumps({
                "ts": time.time(), "ticker": ticker,
                "chain": chain, "outcome": outcome,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _direction(rating: str) -> str:
    """판정 문자열 → up(매수) / down(매도) / hold."""
    r = (rating or "").strip().lower()
    if any(k in r for k in ("buy", "overweight", "매수")):
        return "up"
    if any(k in r for k in ("sell", "underweight", "매도")):
        return "down"
    return "hold"


def _open_thesis(ticker: str, chain: dict, summary: str, today: str) -> None:
    """자동매수 체결 직후 트레이드 씨시스 생성(2026-07-26, trader-memory-core
    이식) — 방금 채워진 포지션에서 원가·수량을 그대로 읽어 재계산 불요.
    실패해도 매수 자체는 이미 성공했으니 조용히 무시(graceful, 부가기능).

    ⚠️ 종목당 ACTIVE 씨시스는 항상 최대 1개(불변식) — 이미 ACTIVE 인 씨시스가
    있으면 스킵(같은 종목 평단추가매수는 새 씨시스 아님). 이 가드가 없으면
    sync_closed() 가 같은 청산 체결을 두 씨시스 모두에 매칭시켜 실현손익이
    중복 기록·중복 포스트모템 알림·다이제스트/백테스트리뷰 통계 중복반영으로
    이어짐(독립 코드리뷰 발견, 2026-07-26)."""
    try:
        from bot import paper_trading, trade_thesis
        if any(t.get("ticker") == ticker for t in trade_thesis.list_theses(status="ACTIVE")):
            return
        pos = paper_trading.get_account().get("positions", {}).get(ticker)
        if not pos:
            return
        trade_thesis.open_thesis(
            ticker, pos.get("market") or "", time.time(),
            pos.get("avg_cost_native"), pos.get("qty"),
            pos.get("cost_basis_krw"),
            thesis_statement=summary, chain=chain,
            planned_exit_date=pos.get("auto_close_date"),
            setup_type="auto_signal")
    except Exception as exc:
        log.debug("paper_signals: open_thesis failed for %s: %s", ticker, exc)


def on_analysis(ticker: str, rating: str, summary: str = "",
                full: str = "") -> Optional[str]:
    """분석 완료 시 호출 — auto on 이면 판정대로 페이퍼 주문. 알림 텍스트 또는
    None(무동작/auto off/예외) 반환. 호출부는 반환 텍스트를 채널에 전달.

    `summary`/`full` = 요약 카드/전체 리포트. contested(분석가·트레이더와 PM
    갈림) 신호면 신규 매수 자동 보류(돈 안전). 매수/청산/보류 시 **결정 체인**
    (RM/트레이더/PM/분석가 다수/discipline)을 auto_audit.jsonl 에 기록(추적성)."""
    try:
        from bot import paper_trading
        if not paper_trading.auto_enabled():
            return None
        tkr = (ticker or "").strip().upper()
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        d = _direction(rating)
        if d == "hold":
            return None   # 무동작 — 자동매매 미관여, audit 불요

        chain = _extract_chain(rating, summary, full)
        cs = _chain_str(chain)

        if d == "up":
            # 컨빅션 게이트 — contested 매수 신호는 자동 실행 안 함(수동 확인).
            if chain["contested"]:
                _log_auto_audit(tkr, chain, "buy_held(contested)")
                return f"🤖 자동매수 보류 (contested · {cs}) — 분석가/트레이더와 PM 갈림, 수동 확인"
            base = paper_trading.starting_capital_krw()
            if base <= 0:
                return None
            pct = paper_trading.auto_size_pct()   # 사용자 설정(/paper auto size N), 기본 5%
            ok, msg = paper_trading.buy_value(
                tkr, base * pct,
                idem=f"auto:{tkr}:{today}", horizon_days=HORIZON_DAYS)
            _log_auto_audit(tkr, chain, ("buy_filled" if ok else "buy_blocked") + f": {msg}")
            if ok:
                _open_thesis(tkr, chain, summary, today)
            return (f"🤖 자동매수 ({cs}): {msg}" if ok
                    else f"🤖 자동매수 보류 ({cs}): {msg}")

        if d == "down":
            held = tkr in paper_trading.get_account().get("positions", {})
            if not held:
                return None
            ok, msg = paper_trading.close_position(tkr, idem=f"auto:{tkr}:{today}")
            _log_auto_audit(tkr, chain, ("close" if ok else "close_blocked") + f": {msg}")
            return f"🤖 자동청산 ({cs}): {msg}" if ok else None

        return None
    except Exception as exc:
        log.debug("paper_signals.on_analysis(%s) failed: %s", ticker, exc)
        return None
