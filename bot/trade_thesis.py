"""트레이드 씨시스(투자논거) 생애주기 추적 + 포스트모템 (2026-07-26).

claude-trading-skills 저장소(tradermonty/claude-trading-skills)의
trader-memory-core/weekly-performance-digest/trade-performance-coach 개념을
우리 아키텍처로 이식 — paper_trading.py 는 체결·포지션만 추적하고 "왜 그
트레이드를 했는지"·청산 후 복기는 전혀 남기지 않던 갭을 메운다.

설계:
- **훅이 아닌 reconcile 패턴**: 청산은 수동 매도·자동매도신호·5거래일 만기
  등 여러 경로로 일어난다(paper_trading.py 참조). 각 경로를 전부 훅하는
  대신, `sync_closed()`가 주기적으로 "ACTIVE 씨시스인데 더 이상 보유 중이
  아님"을 감지해 paper_trading 의 체결 로그(trades)에서 매도 내역을 찾아
  청산 처리한다 — 청산 경로 추가/변경 시에도 이 모듈은 그대로.
- **MFE/MAE 는 샘플링 근사**: 틱 단위 가격 이력을 보관하지 않으므로,
  `update_mfe_mae()`가 (텔레그램 30분 periodic 태스크가) 호출될 때마다
  그 시점 미실현수익률로 최고/최저를 갱신 — 진짜 인트라데이 고저가 아닌
  30분 해상도 근사임을 문서화.
- entry_cost_krw 를 생성 시점에 그대로 저장해 realized_pct 계산 시 별도
  환율 재조회 불요(포지션엔 KRW 원가가 이미 있음).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_HOME = Path.home() / ".tradingagents" / "paper"
_THESES = _HOME / "theses.json"
_CAP = 1000   # 보관 상한 — 초과 시 오래된 CLOSED 부터 제거(ACTIVE 는 항상 보존)


def _load() -> dict:
    try:
        return json.loads(_THESES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"theses": []}


def _save(data: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _THESES.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_THESES)
    except OSError as exc:
        log.warning("trade_thesis: save failed: %s", exc)


def _trim(data: dict) -> None:
    theses = data.get("theses", [])
    if len(theses) <= _CAP:
        return
    active = [t for t in theses if t.get("status") == "ACTIVE"]
    closed = [t for t in theses if t.get("status") != "ACTIVE"]
    closed.sort(key=lambda t: t.get("exit_ts") or 0)
    keep_closed = closed[-(max(0, _CAP - len(active))):]
    data["theses"] = active + keep_closed


def open_thesis(ticker: str, market: str, entry_ts: float,
                entry_price_native: float, qty: float, entry_cost_krw: float, *,
                thesis_statement: str = "", chain: Optional[dict] = None,
                planned_exit_date: Optional[str] = None,
                stop_loss: Optional[float] = None,
                setup_type: Optional[str] = None) -> str:
    """새 씨시스 생성(ACTIVE). 반환값 = thesis_id. 매수 체결 직후 호출부
    (paper_signals.on_analysis) 가 원가·수량을 그대로 넘겨 재계산 불요."""
    data = _load()
    tid = uuid.uuid4().hex[:12]
    data.setdefault("theses", []).append({
        "id": tid, "ticker": (ticker or "").upper(), "market": market,
        "status": "ACTIVE",
        "thesis_statement": (thesis_statement or "")[:500],
        "chain": chain or {}, "setup_type": setup_type,
        "created_ts": time.time(),
        "entry_ts": entry_ts, "entry_price_native": entry_price_native,
        "qty": qty, "entry_cost_krw": entry_cost_krw,
        "planned_exit_date": planned_exit_date, "stop_loss": stop_loss,
        "mfe_pct": 0.0, "mae_pct": 0.0, "mfe_mae_sampled": False,
        "exit_ts": None, "exit_price_native": None, "exit_reason": None,
        "realized_krw": None, "realized_pct": None,
    })
    _trim(data)
    _save(data)
    return tid


def _active(data: dict) -> list[dict]:
    return [t for t in data.get("theses", []) if t.get("status") == "ACTIVE"]


def list_theses(status: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """씨시스 목록(최신순). status='ACTIVE'/'CLOSED' 필터, limit 개수 제한."""
    theses = _load().get("theses", [])
    if status:
        theses = [t for t in theses if t.get("status") == status]
    theses = sorted(theses, key=lambda t: t.get("entry_ts") or 0, reverse=True)
    return theses[:limit] if limit else theses


def update_mfe_mae() -> None:
    """열린 씨시스마다 현재 미실현수익률로 MFE/MAE 갱신 — 30분 주기 샘플
    (틱 단위 아님, 모듈 독스트링 참조). periodic 태스크가 호출."""
    data = _load()
    active = _active(data)
    if not active:
        return
    try:
        from bot import paper_trading
        rows = {r["ticker"]: r for r in paper_trading.positions_with_pnl()}
    except Exception as exc:
        log.debug("trade_thesis: update_mfe_mae price fetch failed: %s", exc)
        return
    changed = False
    for t in active:
        row = rows.get(t["ticker"])
        if not row or row.get("ret_pct") is None:
            continue
        r = row["ret_pct"]
        if r > t["mfe_pct"]:
            t["mfe_pct"] = r
            changed = True
        if r < t["mae_pct"]:
            t["mae_pct"] = r
            changed = True
        if not t.get("mfe_mae_sampled"):
            t["mfe_mae_sampled"] = True   # 최초 샘플 표시(0.0 기본값과 구분)
            changed = True
    if changed:
        _save(data)


def sync_closed() -> list[dict]:
    """ACTIVE 씨시스 중 더 이상 보유하지 않는 종목을 paper_trading 체결
    로그(trades)에서 매도 내역을 찾아 CLOSED 로 전환 + 포스트모템 계산.
    청산 경로(수동매도·자동매도신호·5거래일 만기) 어느 쪽이든 동일하게
    감지 — 개별 청산 호출부를 훅하지 않는다(모듈 독스트링 참조). 아직
    체결 로그에 반영 전(레이스)이면 다음 호출에 재시도. 새로 닫힌 씨시스
    목록(dict 복사본) 반환 — 알림/다이제스트용."""
    data = _load()
    active = _active(data)
    if not active:
        return []
    try:
        from bot import paper_trading
        acct = paper_trading.get_account()
    except Exception as exc:
        log.debug("trade_thesis: sync_closed account load failed: %s", exc)
        return []
    open_tickers = set(acct.get("positions", {}).keys())
    trades = acct.get("trades", [])
    newly_closed = []
    for t in active:
        if t["ticker"] in open_tickers:
            continue
        sells = [tr for tr in trades if tr.get("ticker") == t["ticker"]
                and tr.get("side") == "sell" and (tr.get("ts") or 0) >= t["entry_ts"]]
        if not sells:
            continue
        last = max(sells, key=lambda tr: tr["ts"])
        realized = sum(tr.get("realized_krw") or 0 for tr in sells)
        t["status"] = "CLOSED"
        t["exit_ts"] = last["ts"]
        t["exit_price_native"] = last.get("fill_native")
        t["realized_krw"] = realized
        t["realized_pct"] = (realized / t["entry_cost_krw"] * 100
                             if t.get("entry_cost_krw") else None)
        # exit_reason 추정: 손절가 이하 체결=stop, planned_exit_date 존재+
        # 그 무렵 청산=horizon, 그 외=signal(자동매도신호/수동판단).
        if t.get("stop_loss") and t.get("exit_price_native") is not None \
                and t["exit_price_native"] <= t["stop_loss"]:
            t["exit_reason"] = "stop"
        elif t.get("planned_exit_date"):
            t["exit_reason"] = "horizon"
        else:
            t["exit_reason"] = "signal"
        newly_closed.append(dict(t))
    if newly_closed:
        _trim(data)
        _save(data)
    return newly_closed


def postmortem_text(t: dict) -> str:
    """씨시스 1건 포스트모템 — 결과·이유·MFE 대비 실제 청산 갭(이익실현
    타이밍 코칭) 한 줄. CLOSED 아니면 빈 문자열."""
    if t.get("status") != "CLOSED":
        return ""
    pct = t.get("realized_pct")
    if pct is None:
        return ""
    verdict = "✅ 승" if pct > 0 else ("➖ 보합" if abs(pct) < 0.05 else "❌ 패")
    reason = {"stop": "손절", "horizon": "만기청산", "signal": "매도신호"}.get(
        t.get("exit_reason"), t.get("exit_reason") or "—")
    mfe = t.get("mfe_pct") or 0.0
    gap_note = ""
    # mfe_mae_sampled 없으면(30분 주기가 한 번도 못 돈 채 청산) mfe_pct 는
    # 생성 시 기본값 0.0 일 뿐 실제 관측이 아님 — '고점 대비 반납'으로
    # 오표기 방지(리뷰: 전패 케이스에서 '최고 +0.0%p' 오탐 발견).
    if t.get("mfe_mae_sampled") and mfe - pct > 3.0:
        gap_note = f" · 최고 +{mfe:.1f}%p 갔다가 반납(이익실현 타이밍 재검토)"
    return f"{verdict} {pct:+.1f}% ({reason}){gap_note}"


def digest_stats(days: int = 7) -> dict:
    """최근 N일 청산 씨시스 기준 성과 통계 — 승률·기대값(expectancy)·
    손익비(profit factor). 순수 계산(청산 없으면 n=0 dict)."""
    cutoff = time.time() - days * 86400
    closed = [t for t in _load().get("theses", [])
             if t.get("status") == "CLOSED" and (t.get("exit_ts") or 0) >= cutoff]
    pcts = [t["realized_pct"] for t in closed if t.get("realized_pct") is not None]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p < 0]
    n = len(pcts)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": n, "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": (len(wins) / n * 100) if n else None,
        "expectancy_pct": (sum(pcts) / n) if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_win_pct": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_pct": (sum(losses) / len(losses)) if losses else None,
        "closed": closed,
    }


def weekly_digest_text(days: int = 7) -> Optional[str]:
    """텔레그램 주간 다이제스트 텍스트. 청산 0건이면 None(발송 스킵)."""
    s = digest_stats(days)
    if not s["n"]:
        return None
    lines = [f"📔 트레이드 씨시스 주간 다이제스트 (최근 {days}일, {s['n']}건 청산)",
             f"승률 {s['win_rate']:.0f}% (승 {s['n_wins']}/패 {s['n_losses']}) · "
             f"기대값 {s['expectancy_pct']:+.2f}%/건"]
    if s["profit_factor"] is not None:
        lines.append(f"손익비(profit factor) {s['profit_factor']:.2f}")
    # 승/패 어느 한쪽이 0건(전승 또는 전패)이면 그쪽 평균은 None — 각자
    # 독립적으로 포맷(리뷰: 둘 다 있다고 가정하면 전승/전패 구간에서 크래시).
    avg_parts = []
    if s["avg_win_pct"] is not None:
        avg_parts.append(f"평균 승 {s['avg_win_pct']:+.1f}%")
    if s["avg_loss_pct"] is not None:
        avg_parts.append(f"평균 패 {s['avg_loss_pct']:+.1f}%")
    if avg_parts:
        lines.append(" · ".join(avg_parts))
    return "\n".join(lines)
