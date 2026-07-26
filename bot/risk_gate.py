"""실행 Risk Gate — 주문 전 하드 한도(코드 강제·fail-closed).

docs/execution_architecture.md §5. 페이퍼(E0.5)에서 먼저 검증하고, **같은
게이트가 실거래(E2)에서도 동일하게** 동작한다(broker-무관·universal). 순수
함수 — 계좌 상태 + 의도 주문만 보고 (ok, 사유) 반환. 라이브 가격 불요(저장
상태 기반 캡)이라 단위테스트 가능.

매수만 게이트(신규 리스크). **매도/청산은 항상 허용**(de-risk) — kill-switch
도 매수만 막는다(사용자가 포지션을 정리할 수 있어야 함).

⚠️ 캡 값은 **페이퍼 프로파일**(테스트가 막히지 않게 관대). 실거래(E2) 전
§5 값(거래당 2% / 종목당 10% / 동시 5종목 / 일일손실 3%)으로 조일 것 —
게이트 **로직은 동일**, 상수만 LIVE 프로파일로 분리 예정.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# 페이퍼 프로파일 캡 (자본 기준 비율). 실거래는 §5 로 조임.
PER_TRADE_PCT = 0.60       # 거래당 최대 (LIVE: 0.02)
PER_POSITION_PCT = 0.80    # 종목당 최대 비중 (LIVE: 0.10)
MAX_POSITIONS = 10         # 동시 보유 종목 수 (LIVE: 5)
DAILY_LOSS_PCT = 0.30      # 일일 실현손실 한도 → 당일 신규 매수 차단 (LIVE: 0.03)

# 2026-07-26 확장(claude-trading-skills drawdown-circuit-breaker/pre-trade-
# discipline-gate 이식) — 기존 '오늘' 단위 손실한도만 있던 사각 보완:
# 연속손실 쿨다운(하루 손실%는 안 넘겨도 며칠 연속 소액손실이 누적되는
# 패턴 포착) · 주간/월간 drawdown(더 긴 호라이즌) · 복수매매(revenge trade,
# 손실청산 직후 같은 종목 재진입) 방지. 페이퍼 프로파일도 다른 캡과 동일
# 비율로 느슨(테스트 안 막히게) — LIVE 값은 주석 참조.
CONSECUTIVE_LOSS_LIMIT = 2   # 연속 손실 N회 → 쿨다운 (LIVE 값 동일 유지 검토)
LOSS_COOLDOWN_HOURS = 24     # 연속손실 쿨다운 지속시간
WEEKLY_LOSS_PCT = 0.50       # 주간 실현손실 한도 (LIVE: 0.05)
MONTHLY_LOSS_PCT = 0.80      # 월간 실현손실 한도 (LIVE: 0.08)
REVENGE_WINDOW_HOURS = 2     # 손실청산 후 같은 종목 재진입 경계 시간

# kill-switch — 봇 외부(SSH·cron)에서도 touch 가능해야 봇이 죽어도 정지 보장.
_HALT_FILE = Path.home() / ".tradingagents" / "TRADING_HALT"
_KST = timezone(timedelta(hours=9))


def halt_active() -> bool:
    """kill-switch 파일 존재 여부."""
    try:
        return _HALT_FILE.exists()
    except OSError:
        return False


def set_halt(on: bool) -> None:
    """kill-switch 켜기/끄기 (파일 touch/remove)."""
    try:
        if on:
            _HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _HALT_FILE.write_text(datetime.now(_KST).isoformat(), encoding="utf-8")
        else:
            _HALT_FILE.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("risk_gate: set_halt(%s) failed: %s", on, exc)


def _today_realized_krw(account: dict) -> float:
    """오늘(KST) 실현손익 합 — 일일 손실 한도 판정용."""
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    total = 0.0
    for t in account.get("trades", []):
        r, ts = t.get("realized_krw"), t.get("ts")
        if r is None or not ts:
            continue
        try:
            if datetime.fromtimestamp(ts, _KST).strftime("%Y-%m-%d") == today:
                total += r
        except (TypeError, ValueError, OSError):
            continue
    return total


def _closed_trades(account: dict) -> list[dict]:
    """청산(realized_krw 존재) 체결만, ts 오름차순."""
    closes = [t for t in account.get("trades", []) if t.get("realized_krw") is not None]
    closes.sort(key=lambda t: t.get("ts") or 0)
    return closes


def _consecutive_losses(account: dict) -> int:
    """가장 최근 청산부터 거슬러 연속 손실 횟수(승 나오면 중단)."""
    streak = 0
    for t in reversed(_closed_trades(account)):
        if (t.get("realized_krw") or 0) < 0:
            streak += 1
        else:
            break
    return streak


def _last_close_ts(account: dict):
    closes = _closed_trades(account)
    return closes[-1].get("ts") if closes else None


def _realized_since(account: dict, since_ts: float) -> float:
    return sum(t.get("realized_krw") or 0 for t in _closed_trades(account)
              if (t.get("ts") or 0) >= since_ts)


def _week_start_kst() -> float:
    """이번 주 월요일 00:00 KST (timestamp)."""
    now = datetime.now(_KST)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def _month_start_kst() -> float:
    """이번 달 1일 00:00 KST (timestamp)."""
    now = datetime.now(_KST)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.timestamp()


def _revenge_trade_recent(account: dict, ticker: str) -> bool:
    """같은 종목이 REVENGE_WINDOW_HOURS 이내에 손실 청산됐으면 True —
    복수매매(revenge trade, 손실 직후 감정적 재진입) 차단용."""
    cutoff = time.time() - REVENGE_WINDOW_HOURS * 3600
    for t in account.get("trades", []):
        if t.get("ticker") != ticker or t.get("side") != "sell":
            continue
        r, ts = t.get("realized_krw"), t.get("ts") or 0
        if r is not None and r < 0 and ts >= cutoff:
            return True
    return False


def check_order(account: dict, ticker: str, side: str, cost_krw: float):
    """주문 전 게이트. (ok, 사유) 반환. 매도/청산은 kill-switch 무관 항상 허용.

    `cost_krw` = 매수 시 qty*price*fx (매도는 0/무시). `account` = paper_trading
    계좌 dict(starting_capital_krw·positions·trades). fail-closed — 위반은 거부."""
    if side == "sell":
        return True, ""                                    # de-risk 항상 허용

    if halt_active():
        return False, "⛔ 거래 중지(kill-switch) — 신규 매수 차단. 해제: /paper resume"

    base = float(account.get("starting_capital_krw") or 0)
    if base <= 0:
        return True, ""                                    # 자본 기준 없음 → 캡 미적용

    # 일일 실현손실 한도 → 당일 신규 매수 차단(매도는 위에서 이미 허용).
    if _today_realized_krw(account) <= -base * DAILY_LOSS_PCT:
        return False, (f"⛔ 일일 손실 한도(-{DAILY_LOSS_PCT:.0%}, "
                       f"₩{base * DAILY_LOSS_PCT:,.0f}) 도달 — 당일 신규 매수 차단")

    # 연속손실 쿨다운(2026-07-26) — 하루 손실%는 안 넘겨도 N회 연속 손실이면
    # LOSS_COOLDOWN_HOURS 정지. streak≥LIMIT 이면 가장 최근 청산이 곧 그
    # 연속손실의 마지막 항이라 그 시각을 쿨다운 기산점으로 사용.
    if _consecutive_losses(account) >= CONSECUTIVE_LOSS_LIMIT:
        last_ts = _last_close_ts(account)
        if last_ts is not None:
            elapsed_h = (time.time() - last_ts) / 3600
            if elapsed_h < LOSS_COOLDOWN_HOURS:
                return False, (f"⛔ 연속손실 {CONSECUTIVE_LOSS_LIMIT}회+ 쿨다운 — "
                               f"{LOSS_COOLDOWN_HOURS - elapsed_h:.1f}시간 후 재개")

    # 주간/월간 drawdown 정지(2026-07-26) — 일일 한도보다 긴 호라이즌의
    # 누적손실 포착(매일 조금씩 잃는 패턴은 일일% 는 안 걸림).
    if _realized_since(account, _week_start_kst()) <= -base * WEEKLY_LOSS_PCT:
        return False, (f"⛔ 주간 손실 한도(-{WEEKLY_LOSS_PCT:.0%}) 도달 — "
                       "이번 주 신규 매수 차단(다음 주 월요일 해제)")
    if _realized_since(account, _month_start_kst()) <= -base * MONTHLY_LOSS_PCT:
        return False, (f"⛔ 월간 손실 한도(-{MONTHLY_LOSS_PCT:.0%}) 도달 — "
                       "이번 달 신규 매수 차단(다음 달 1일 해제)")

    # 복수매매(revenge trade) 차단(2026-07-26) — 같은 종목 손실청산 직후
    # 감정적 재진입 방지(de-risk 매도는 항상 허용, 이건 신규 매수만 막음).
    if _revenge_trade_recent(account, ticker):
        return False, (f"⛔ 복수매매(revenge trade) 경계 — {ticker} 손실청산 "
                       f"{REVENGE_WINDOW_HOURS}시간 이내 재진입 차단")

    # 거래당 최대.
    if cost_krw > base * PER_TRADE_PCT + 1e-6:
        return False, (f"⛔ 거래당 한도 초과 — 최대 ₩{base * PER_TRADE_PCT:,.0f} "
                       f"(자본 {PER_TRADE_PCT:.0%}), 주문 ₩{cost_krw:,.0f}")

    positions = account.get("positions", {})
    # 동시 보유 종목 수(신규 종목일 때만).
    if ticker not in positions and len(positions) >= MAX_POSITIONS:
        return False, (f"⛔ 동시 보유 종목 수 한도({MAX_POSITIONS}) — "
                       "기존 보유 종목만 추가 매수 가능")

    # 종목당 최대 비중(기존 원가 + 신규).
    existing = float(positions.get(ticker, {}).get("cost_basis_krw") or 0)
    if (existing + cost_krw) > base * PER_POSITION_PCT + 1e-6:
        return False, (f"⛔ 종목당 비중 한도({PER_POSITION_PCT:.0%}) 초과 — "
                       f"{ticker} 최대 ₩{base * PER_POSITION_PCT:,.0f}")

    return True, ""


def status_line() -> str:
    """대시보드/`/paper` 표시용 한 줄 — 캡·kill-switch 상태."""
    halt = "⛔ 거래중지" if halt_active() else "✅ 정상"
    return (f"Risk Gate: {halt} · 거래당 ≤{PER_TRADE_PCT:.0%} · 종목당 "
            f"≤{PER_POSITION_PCT:.0%} · 최대 {MAX_POSITIONS}종목 · 일손실 "
            f"{DAILY_LOSS_PCT:.0%}/주 {WEEKLY_LOSS_PCT:.0%}/월 {MONTHLY_LOSS_PCT:.0%} "
            f"HALT · 연속손실{CONSECUTIVE_LOSS_LIMIT}+ {LOSS_COOLDOWN_HOURS}h 쿨다운 · "
            f"복수매매 {REVENGE_WINDOW_HOURS}h 차단 (페이퍼 프로파일)")
