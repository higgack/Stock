"""E0/E1 페이퍼 트레이딩 엔진 — 실거래 실행 골격에서 '돈만 뺀 것' (리스크 0).

docs/execution_architecture.md 부록 A 의 첫 단계. NOAH 판정/수동 명령을 모의
주문으로 받아 포지션·평단·실현/미실현 P&L 을 추적한다. 여기서 검증한 Order
Manager + Ledger 골격 위에 나중에 KIS/IBKR '실주문' 어댑터가 얹힌다(모의 체결을
실주문으로 교체).

설계 원칙 반영(문서 §1):
- **paper-first**: 이 모듈은 모의 전용. 실주문 코드 없음(돈 0).
- **idempotency**: 모든 주문에 idem key — 재시도/중복 탭에도 1회만 적용.
- **fail-closed**: 가격 글리치/티커 부재/현금 부족/통화 미지원 → 거부.
- **분석 ≠ 실행 분리**: 이 엔진은 신호를 받기만, 분석을 호출하지 않음.

E0 범위: **시장가 즉시 체결**(glitch-guarded 현재가) · KR(₩)+US($) · 수동 명령.
E1 범위: KR 종목을 KIS 모의투자 서버로 라우팅(서버 체결 — 슬리피지·장중 제한·
부분체결 반영). KIS_PAPER_APP_KEY + KIS_PAPER_APP_SECRET + KIS_PAPER_ACCT_NO
env 설정 시 자동 활성. US 종목은 E0 유지. KIS 미설정 시 전체 E0 폴백.

통화: 단일 **KRW 계좌**. US 매수는 체결 시점 USD/KRW 로 환산해 원화 차감
(원화 투자자가 미국주식 사는 모델). 포지션 평단은 native 보관(% 수익률 깔끔),
원가는 KRW cost-basis 로 보관(환차손익까지 정확). graceful — 네트워크/키 부재
시 명령은 거부 메시지, 크래시 0.
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_KST = timezone(timedelta(hours=9))

log = logging.getLogger(__name__)

_HOME = Path.home() / ".tradingagents" / "paper"
_ACCOUNT = _HOME / "portfolio.json"
_AUDIT = _HOME / "audit.jsonl"

STARTING_CAPITAL_KRW = 10_000_000      # 페이퍼 시작 자본
_FX_FALLBACK_USDKRW = 1380.0           # USD/KRW fetch 실패 시
_SEEN_CAP = 500                        # idempotency 키 보관 상한
_TRADES_CAP = 500                      # 계좌 내 최근 체결 보관(전체는 audit.jsonl)
_E0_MARKETS = ("KR", "US")             # E0 지원 시장(통화)
_KIS_FILL_WAIT = 2.0                  # KIS 체결 조회 전 대기 (초)
_KIS_FILL_RETRIES = 3                 # KIS 체결 조회 재시도


# ─── persistence ─────────────────────────────────────────────────────────────
def _new_account() -> dict:
    return {
        "starting_capital_krw": float(STARTING_CAPITAL_KRW),
        "cash_krw": float(STARTING_CAPITAL_KRW),
        "realized_pnl_krw": 0.0,
        "positions": {},        # ticker -> {qty, avg_cost_native, cost_basis_krw, currency, market, auto_close_date?}
        "trades": [],           # 최근 체결(cap)
        "seen_keys": [],        # idempotency
        "auto_enabled": False,  # NOAH 판정 → 자동 페이퍼 주문 (E0.5b, 기본 OFF)
        "pending": [],          # 지정가 대기 주문 [{id,ticker,side,qty,limit,…}]
        "equity_history": [],   # [{date, equity_krw}] 일별 1점 (E0.5d equity curve)
        "created_ts": time.time(),
    }


def get_account() -> dict:
    try:
        acct = json.loads(_ACCOUNT.read_text(encoding="utf-8"))
        # 누락 필드 보정(스키마 진화 안전).
        base = _new_account()
        for k, v in base.items():
            acct.setdefault(k, v)
        return acct
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _new_account()


def _save_account(acct: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _ACCOUNT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(acct, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_ACCOUNT)
    except OSError as exc:
        log.warning("paper: account save failed: %s", exc)


def _audit_log(rec: dict) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ─── FX + price ──────────────────────────────────────────────────────────────
_fx_cache: dict = {"ts": 0.0, "rate": None}


def _usdkrw() -> float:
    """USD/KRW (yfinance KRW=X, 10분 캐시). 실패 시 fallback 상수."""
    if _fx_cache["rate"] is not None and (time.time() - _fx_cache["ts"]) < 600:
        return _fx_cache["rate"]
    rate = _FX_FALLBACK_USDKRW
    try:
        import yfinance as yf
        fi = yf.Ticker("KRW=X").fast_info
        v = None
        for attr in ("last_price", "lastPrice", "regularMarketPrice"):
            try:
                v = fi[attr] if hasattr(fi, "__getitem__") else getattr(fi, attr, None)
            except Exception:
                v = None
            if v:
                break
        if v and 500 < float(v) < 3000:    # sane USD/KRW band(글리치 방어)
            rate = float(v)
    except Exception as exc:
        log.debug("paper: USD/KRW fetch failed (%s) — fallback %.0f", exc, rate)
    _fx_cache.update(ts=time.time(), rate=rate)
    return rate


def _market_fx(market: str):
    """(currency_code, fx_to_krw) — E0 미지원 시장은 (None, None)."""
    if market == "KR":
        return ("KRW", 1.0)
    if market == "US":
        return ("USD", _usdkrw())
    return (None, None)


def live_native_price(ticker: str):
    """(price_native, market) — glitch-guarded 현재가(chart_data 재사용) + 시장.
    실패/데이터 없음 → (None, market|None). 절대 raise 안 함."""
    market = None
    try:
        from bot.market import detect_market
        market = detect_market(ticker)
    except Exception:
        pass
    try:
        from bot.chart_data import fetch_chart_payload
        p = fetch_chart_payload(ticker)
        if not p:
            return None, market
        px = p.get("last_price")
        if px is None:
            closes = [c for c in (p.get("close") or []) if c is not None]
            px = closes[-1] if closes else None
        return (float(px) if px is not None else None), market
    except Exception as exc:
        log.debug("paper: price fetch failed for %s: %s", ticker, exc)
        return None, market


# ─── pure ledger math (unit-testable, no I/O) ────────────────────────────────
def _apply_buy(acct: dict, ticker: str, qty: float, fill_native: float,
               fx: float, currency: str, market: str, ts: float, idem: str):
    """매수 적용(계좌 dict 변형). (ok, msg) 반환. 순수 — 가격/FX 는 호출부가 결정."""
    if idem and idem in acct["seen_keys"]:
        return True, "중복 주문 무시(idempotent)"
    if qty <= 0:
        return False, "수량은 0보다 커야 합니다"
    cost_krw = qty * fill_native * fx
    if cost_krw > acct["cash_krw"] + 1e-6:
        return False, (f"현금 부족 — 필요 ₩{cost_krw:,.0f} · 보유 "
                       f"₩{acct['cash_krw']:,.0f}")
    pos = acct["positions"].get(ticker)
    if pos:
        new_qty = pos["qty"] + qty
        pos["avg_cost_native"] = (
            (pos["avg_cost_native"] * pos["qty"] + fill_native * qty) / new_qty)
        pos["cost_basis_krw"] += cost_krw
        pos["qty"] = new_qty
    else:
        acct["positions"][ticker] = {
            "qty": qty, "avg_cost_native": fill_native, "cost_basis_krw": cost_krw,
            "currency": currency, "market": market,
        }
    acct["cash_krw"] -= cost_krw
    _record_trade(acct, ts, ticker, "buy", qty, fill_native, fx, currency, cost_krw, None, idem)
    return True, (f"매수 체결: {ticker} {qty:g}주 @ {fill_native:,.2f}{_sym(currency)} "
                  f"(₩{cost_krw:,.0f})")


def _apply_sell(acct: dict, ticker: str, qty: float, fill_native: float,
                fx: float, currency: str, market: str, ts: float, idem: str):
    if idem and idem in acct["seen_keys"]:
        return True, "중복 주문 무시(idempotent)"
    pos = acct["positions"].get(ticker)
    if not pos or pos["qty"] <= 0:
        return False, f"보유 없음: {ticker}"
    if qty <= 0:
        return False, "수량은 0보다 커야 합니다"
    if qty > pos["qty"] + 1e-9:
        return False, f"보유 수량 초과 — 보유 {pos['qty']:g}주"
    proceeds_krw = qty * fill_native * fx
    cost_removed_krw = pos["cost_basis_krw"] * (qty / pos["qty"])
    realized = proceeds_krw - cost_removed_krw
    acct["realized_pnl_krw"] += realized
    acct["cash_krw"] += proceeds_krw
    pos["qty"] -= qty
    pos["cost_basis_krw"] -= cost_removed_krw
    if pos["qty"] <= 1e-9:
        acct["positions"].pop(ticker, None)
    _record_trade(acct, ts, ticker, "sell", qty, fill_native, fx, currency,
                  proceeds_krw, realized, idem)
    return True, (f"매도 체결: {ticker} {qty:g}주 @ {fill_native:,.2f}{_sym(currency)} "
                  f"(실현 손익 ₩{realized:,.0f})")


def _record_trade(acct, ts, ticker, side, qty, fill_native, fx, currency,
                  krw, realized, idem):
    rec = {"ts": ts, "ticker": ticker, "side": side, "qty": qty,
           "fill_native": fill_native, "fx": fx, "currency": currency,
           "krw": krw, "realized_krw": realized}
    acct["trades"].append(rec)
    if len(acct["trades"]) > _TRADES_CAP:
        acct["trades"] = acct["trades"][-_TRADES_CAP:]
    if idem:
        acct["seen_keys"].append(idem)
        if len(acct["seen_keys"]) > _SEEN_CAP:
            acct["seen_keys"] = acct["seen_keys"][-_SEEN_CAP:]
    _audit_log(rec)


def _sym(currency: str) -> str:
    return {"KRW": "₩", "USD": "$"}.get(currency, "")


# ─── E1: KIS 모의투자 routing ────────────────────────────────────────────────
def _e1_available() -> bool:
    """KR 종목에 KIS 모의투자 어댑터 사용 가능 여부."""
    try:
        from bot.kis_trading import is_e1_available
        return is_e1_available()
    except ImportError:
        return False


def e1_mode() -> Optional[str]:
    """현재 E1 모드 → "paper" | "live" | None(비활성)."""
    if not _e1_available():
        return None
    return "paper"


def _ticker_to_code(ticker: str) -> str:
    """005930.KS → '005930' (KIS 종목코드 6자리)."""
    return ticker.split(".")[0].lstrip("0").zfill(6) if "." in ticker else ticker.zfill(6)


def _order_e1(acct: dict, ticker: str, qty: float, side: str,
              px_fallback: float, fx: float, currency: str, market: str,
              idem: str, limit: Optional[float] = None):
    """KIS 모의투자 서버로 주문 제출 + 체결 확인 → 레저 반영. (ok, msg).
    px_fallback = 체결 조회 실패 시 사용할 가격(glitch-guarded 현재가)."""
    try:
        from bot.kis_trading import get_adapter
    except ImportError:
        return False, "kis_trading 모듈 로드 실패"

    adapter = get_adapter("paper")
    if adapter is None:
        return False, "KIS 모의투자 어댑터 초기화 실패(env 확인)"

    code = _ticker_to_code(ticker)
    ord_type = "limit" if limit is not None else "market"
    limit_price = int(limit) if limit else 0

    result = adapter.submit_order(code, side, int(qty),
                                  ord_type=ord_type, limit_price=limit_price)
    if result is None:
        return False, f"KIS 주문 제출 실패: {ticker} (서버 거부/네트워크)"

    order_no = result.get("order_no", "")
    log.info("kis_e1: order submitted %s %s %s qty=%d → order_no=%s",
             side, ticker, ord_type, int(qty), order_no)

    fill_price = px_fallback
    if order_no and ord_type == "market":
        import time as _time
        for attempt in range(_KIS_FILL_RETRIES):
            _time.sleep(_KIS_FILL_WAIT)
            fill = adapter.find_fill(order_no)
            if fill and fill.get("price"):
                fill_price = float(fill["price"])
                log.info("kis_e1: fill confirmed order_no=%s price=%s",
                         order_no, fill_price)
                break
        else:
            log.info("kis_e1: fill not confirmed, using fallback price %.2f",
                     px_fallback)

    return _apply_fill_direct(acct, ticker, qty, fill_price, fx, currency,
                              market, idem, source="kis_e1",
                              kis_order_no=order_no, side=side)


def _apply_fill_direct(acct: dict, ticker: str, qty: float, fill_native: float,
                       fx: float, currency: str, market: str, idem: str,
                       source: str = "e0", kis_order_no: str = "",
                       side: str = "buy"):
    """체결 확인된 주문을 레저에 직접 반영(재귀 없이). KIS 체결/fill_pending 용."""
    fn = _apply_buy if side == "buy" else _apply_sell
    ok, msg = fn(acct, ticker, float(qty), float(fill_native), float(fx),
                 currency, market, time.time(), idem)
    if ok:
        if kis_order_no:
            pos = acct["positions"].get(ticker)
            if pos:
                pos["kis_order_no"] = kis_order_no
        _save_account(acct)
        suffix = " [KIS 모의투자]" if source == "kis_e1" else ""
        msg = msg + suffix
    return ok, msg


# ─── public order API (I/O wrappers) ─────────────────────────────────────────
def _order(ticker: str, qty: float, side: str, idem: Optional[str],
           limit: Optional[float] = None):
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return False, "티커를 입력하세요"
    px, market = live_native_price(ticker)
    if market not in _E0_MARKETS:
        return False, (f"E0 미지원 시장({market or '?'}) — 현재 KR/US 만. "
                       "다른 시장은 라우터 확장 후.")
    if px is None or px <= 0:
        return False, f"현재가를 가져올 수 없습니다: {ticker}(데이터 없음/이상치)"
    currency, fx = _market_fx(market)
    if fx is None:
        return False, f"통화 환산 불가: {market}"
    if idem is None:
        # 멱등 토큰 미제공(프로그램/테스트 호출) → 매번 고유키 = dedup 안 함.
        # ⚠️ 과거 '티커:방향:수량:분' 파생키는 서로 다른 주문(sell 5 ↔ close 5)이
        # 같은 분에 같은 (티커,방향,수량)이면 충돌해 잘못 dedup 했다(2026-06-06).
        # 진짜 멱등 단위는 '논리적 주문 1건' — 호출부(텔레그램)가 message_id 를
        # idem 으로 넘기면 재전송만 dedup 되고 의도된 별개 주문은 각각 체결된다.
        idem = uuid.uuid4().hex
    # 지정가(E0.5d limit): 현재가가 limit 조건을 아직 안 채우면 PENDING 보관
    # (체결 X). 체결은 _periodic_paper_pending 이 fill_pending() 으로 가격 도달
    # 시 시장가 실행 → Risk Gate 도 그 시점에 적용. marketable 이면 그냥 통과해
    # 아래에서 현재가 체결(지정가보다 유리/동일).
    if limit is not None and not _marketable(side, px, float(limit)):
        acct = get_account()
        if any(p.get("idem") == idem for p in acct.get("pending", [])):
            return True, "지정가 주문 이미 대기(중복 무시)"
        pid = uuid.uuid4().hex[:6]
        acct.setdefault("pending", []).append({
            "id": pid, "ticker": ticker, "side": side, "qty": float(qty),
            "limit": float(limit), "market": market, "currency": currency,
            "idem": idem, "ts": time.time()})
        _save_account(acct)
        return True, (f"⏳ 지정가 {'매수' if side == 'buy' else '매도'} 대기: {ticker} "
                      f"{qty:g}주 @ {_sym(currency)}{float(limit):,.2f} (현재 "
                      f"{_sym(currency)}{px:,.2f}, id {pid}) — 가격 도달 시 자동 체결")
    acct = get_account()
    # Risk Gate (E0.5) — 주문 전 하드 한도/kill-switch (fail-closed). 매도는
    # 항상 통과(de-risk). 같은 게이트가 실거래(E2)에도 재사용된다.
    try:
        from bot.risk_gate import check_order
        _cost_krw = float(qty) * float(px) * float(fx) if side == "buy" else 0.0
        gate_ok, gate_reason = check_order(acct, ticker, side, _cost_krw)
        if not gate_ok:
            return False, gate_reason
    except Exception as exc:
        log.debug("paper: risk_gate skipped (%s)", exc)
    # E1 routing: KR 종목 + KIS 모의투자 키 존재 시 KIS 서버로 주문
    if market == "KR" and _e1_available():
        return _order_e1(acct, ticker, float(qty), side, float(px), float(fx),
                         currency, market, idem, limit=limit)
    fn = _apply_buy if side == "buy" else _apply_sell
    ok, msg = fn(acct, ticker, float(qty), float(px), float(fx), currency, market,
                 time.time(), idem)
    if ok:
        _save_account(acct)
    return ok, msg


def _marketable(side: str, px: float, limit: float) -> bool:
    """지정가 체결 가능 여부 — 매수는 현재가 ≤ 지정가, 매도는 현재가 ≥ 지정가."""
    return (side == "buy" and px <= limit) or (side == "sell" and px >= limit)


def buy(ticker: str, qty: float, idem: Optional[str] = None,
        limit: Optional[float] = None):
    """매수 — limit 없으면 시장가 즉시, 있으면 도달 시 체결(PENDING). (ok, 메시지)."""
    return _order(ticker, qty, "buy", idem, limit=limit)


def sell(ticker: str, qty: float, idem: Optional[str] = None,
         limit: Optional[float] = None):
    """매도 — limit 없으면 시장가, 있으면 도달 시 체결. (ok, 메시지)."""
    return _order(ticker, qty, "sell", idem, limit=limit)


def list_pending() -> list:
    return get_account().get("pending", [])


def cancel_pending(token: str) -> tuple:
    """지정가 대기 주문 취소 — id 또는 ticker 또는 'all'. (ok, 메시지)."""
    acct = get_account()
    pend = acct.get("pending", [])
    tok = (token or "").strip().upper()
    if tok == "ALL":
        n = len(pend)
        acct["pending"] = []
    else:
        acct["pending"] = [p for p in pend
                           if p.get("id", "").upper() != tok
                           and p.get("ticker", "").upper() != tok]
        n = len(pend) - len(acct["pending"])
    if n:
        _save_account(acct)
        return True, f"⏳ 지정가 대기 {n}건 취소"
    return False, f"취소할 대기 주문 없음: {token}"


def fill_pending() -> list:
    """가격 도달한 지정가 대기 주문을 시장가로 체결. 체결 메시지 리스트 반환.
    periodic 태스크가 호출. 각 체결은 _order(Risk Gate 포함)를 거친다.
    KR + E1 활성 시 KIS 서버로 지정가 주문 제출(서버가 체결 관리)."""
    pend = list(get_account().get("pending", []))
    if not pend:
        return []
    filled, keep = [], []
    for p in pend:
        try:
            px, _ = live_native_price(p.get("ticker"))
            if px is not None and _marketable(p.get("side"), px, p.get("limit")):
                ok, msg = _order(p["ticker"], p["qty"], p["side"], p.get("idem"))
                if ok:
                    filled.append(msg)
                    continue
            keep.append(p)
        except Exception:
            keep.append(p)
    acct = get_account()   # _order 가 저장했으므로 재읽기 후 pending 갱신
    acct["pending"] = keep
    _save_account(acct)
    return filled


def run_reconcile() -> Optional[dict]:
    """KIS 잔고 ↔ 우리 ledger 비교(E1 활성 시만). drift 시 dict, 일치 시 None."""
    if not _e1_available():
        return None
    try:
        from bot.kis_trading import reconcile
        return reconcile()
    except Exception as exc:
        log.warning("paper: reconcile failed: %s", exc)
        return None


def close_position(ticker: str, idem: Optional[str] = None):
    """보유 전량 매도."""
    ticker = (ticker or "").strip().upper()
    pos = get_account()["positions"].get(ticker)
    if not pos:
        return False, f"보유 없음: {ticker}"
    return _order(ticker, pos["qty"], "sell", idem)


def reset():
    """계좌 초기화(페이퍼). 되돌릴 수 없음 — 호출부가 확인 책임.

    **데이터**(포지션·체결·대기·equity 곡선·idempotency·결정 이력)만 지우고
    **설정**(자동매매 on/off·사이징)은 보존 — 'reset = 테스트 데이터 삭제,
    설정 유지'(2026-06-06 사용자 요청). 대시보드 '🤖 자동매매 결정 이력'
    표(auto_audit.jsonl)도 함께 비워 페이퍼 대시보드 전체가 clean."""
    prev = get_account()
    acct = _new_account()
    acct["auto_enabled"] = bool(prev.get("auto_enabled", False))
    if prev.get("auto_size_pct"):
        acct["auto_size_pct"] = prev["auto_size_pct"]
    _save_account(acct)
    snapshot_equity(float(STARTING_CAPITAL_KRW))   # equity curve baseline
    try:
        (_HOME / "auto_audit.jsonl").unlink(missing_ok=True)   # 결정 이력 표 clear
    except OSError:
        pass
    _audit_log({"ts": time.time(), "event": "reset"})
    return True, (f"페이퍼 계좌 초기화 — 시작 자본 ₩{STARTING_CAPITAL_KRW:,} "
                  "(포지션·체결·대기·곡선·결정이력 삭제, 자동매매 설정 유지)")


def purge_ticker(ticker: str) -> tuple:
    """개별 종목 리셋 — 그 종목 흔적 전부 삭제(테스트 정리용). 오픈 포지션은
    **원가를 현금에 환원**(매수 취소처럼, 매도 아님 → 실현손익 안 만듦) + 대기
    주문·체결 기록·그 종목 실현·결정 이력(auto_audit)에서 제거. `/paper close`
    (실제 매도, 실현손익 기록)와 다른 '깨끗한 삭제'. (ok, 메시지)."""
    tkr = (ticker or "").strip().upper()
    if not tkr:
        return False, "티커를 입력하세요"
    acct = get_account()
    touched = False
    pos = acct.get("positions", {}).pop(tkr, None)
    if pos:
        acct["cash_krw"] += float(pos.get("cost_basis_krw") or 0.0)   # 매수 취소
        touched = True
    before = len(acct.get("pending", []))
    acct["pending"] = [p for p in acct.get("pending", [])
                       if (p.get("ticker") or "").upper() != tkr]
    if len(acct["pending"]) < before:
        touched = True
    dropped = [t for t in acct.get("trades", [])
               if (t.get("ticker") or "").upper() == tkr]
    if dropped:
        acct["realized_pnl_krw"] -= sum(t.get("realized_krw") or 0 for t in dropped)
        acct["trades"] = [t for t in acct.get("trades", [])
                          if (t.get("ticker") or "").upper() != tkr]
        touched = True
    if not touched:
        return False, f"페이퍼에 해당 종목 흔적 없음: {tkr}"
    _save_account(acct)
    # 결정 이력(auto_audit.jsonl)에서 그 종목 줄 제거.
    try:
        ap = _HOME / "auto_audit.jsonl"
        if ap.exists():
            kept = []
            for ln in ap.read_text(encoding="utf-8").splitlines():
                try:
                    if (json.loads(ln).get("ticker") or "").upper() != tkr:
                        kept.append(ln)
                except json.JSONDecodeError:
                    continue
            ap.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    except OSError:
        pass
    _audit_log({"ts": time.time(), "event": "purge", "ticker": tkr})
    return True, (f"🧹 {tkr} 페이퍼 리셋 — 포지션·대기·체결·결정이력 삭제 "
                  "(오픈 포지션 원가는 현금 환원, 매도 아님)")


def snapshot_equity(equity_krw: Optional[float] = None) -> None:
    """오늘(KST) 자산 포인트를 equity_history 에 기록(같은 날이면 갱신 = 일별
    1점). `equity_krw` 미지정 시 summary 로 산출. 365점 cap. E0.5d 자산 추이
    커브용 — regenerate_paper_index(매 페이퍼 액션)·reset·자정 regen 이 호출."""
    try:
        if equity_krw is None:
            equity_krw = summary().get("total_equity_krw")
        if equity_krw is None:
            return
        acct = get_account()
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        hist = acct.get("equity_history") or []
        if hist and hist[-1].get("date") == today:
            hist[-1]["equity_krw"] = float(equity_krw)
        else:
            hist.append({"date": today, "equity_krw": float(equity_krw)})
        acct["equity_history"] = hist[-365:]
        _save_account(acct)
    except Exception as exc:
        log.debug("paper: snapshot_equity failed: %s", exc)


# ─── E0.5b: NOAH 판정 → 자동 주문 지원 (auto flag · 금액 사이징 · 5거래일 청산) ─
def auto_enabled() -> bool:
    return bool(get_account().get("auto_enabled"))


def set_auto(on: bool) -> None:
    acct = get_account()
    acct["auto_enabled"] = bool(on)
    _save_account(acct)


def starting_capital_krw() -> float:
    return float(get_account().get("starting_capital_krw") or 0)


DEFAULT_AUTO_SIZE_PCT = 0.05   # 자동매수 기본 사이징(자본 비율)


def auto_size_pct() -> float:
    """자동매수 사이징(자본 비율). 미설정 시 기본 5%."""
    v = get_account().get("auto_size_pct")
    try:
        return float(v) if v else DEFAULT_AUTO_SIZE_PCT
    except (TypeError, ValueError):
        return DEFAULT_AUTO_SIZE_PCT


def set_auto_size(pct: float) -> float:
    """자동매수 사이징 설정(1~50% clamp). 반영된 비율 반환."""
    pct = max(0.01, min(0.50, float(pct)))
    acct = get_account()
    acct["auto_size_pct"] = pct
    _save_account(acct)
    return pct


def trade_stats() -> dict:
    """청산 실현손익 기반 통계 — 거래수·승률·평균손익(페이퍼 성과)."""
    realized = [t["realized_krw"] for t in get_account().get("trades", [])
                if t.get("realized_krw") is not None]
    wins = sum(1 for r in realized if r > 0)
    losses = sum(1 for r in realized if r < 0)
    n = wins + losses
    return {
        "n_closes": len(realized), "wins": wins, "losses": losses,
        "win_rate": (wins / n * 100) if n else None,
        "avg_realized_krw": (sum(realized) / len(realized)) if realized else None,
    }


def buy_value(ticker: str, krw_target: float, idem: Optional[str] = None,
              horizon_days: Optional[int] = None):
    """목표 금액(KRW)만큼 시장가 매수 — qty = floor(목표 / (현재가×fx)). 자동
    신호의 '자본 N% 매수' 사이징용. `horizon_days` 면 진입 후 그 거래일 뒤
    auto_close_date 를 포지션에 기록(자동 청산 대상). (ok, 메시지)."""
    ticker = (ticker or "").strip().upper()
    px, market = live_native_price(ticker)
    if market not in _E0_MARKETS:
        return False, f"E0 미지원 시장({market or '?'})"
    if px is None or px <= 0:
        return False, f"현재가를 가져올 수 없습니다: {ticker}"
    _, fx = _market_fx(market)
    if not fx:
        return False, f"통화 환산 불가: {market}"
    qty = math.floor(krw_target / (px * fx))
    if qty < 1:
        return False, (f"목표 금액 ₩{krw_target:,.0f} 으로 1주도 매수 불가 "
                       f"(1주 ≈ ₩{px * fx:,.0f})")
    ok, msg = _order(ticker, qty, "buy", idem)
    if ok and horizon_days:
        _set_horizon(ticker, market, horizon_days)
    return ok, msg


def _set_horizon(ticker: str, market: str, days: int) -> None:
    """auto 매수 포지션에 자동 청산일(진입+거래일 days) 기록. 휴장일 캘린더
    부재 시 캘린더-일 근사로 폴백."""
    try:
        acct = get_account()
        pos = acct["positions"].get(ticker)
        if not pos:
            return
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        close_date = None
        try:
            from bot.market_calendar import add_trading_days
            close_date = add_trading_days(market, today, days)
        except Exception:
            close_date = None
        if close_date is None:   # 폴백: 거래일≈캘린더일×1.5 근사
            close_date = (datetime.now(_KST)
                          + timedelta(days=int(days * 1.5) + 1)).strftime("%Y-%m-%d")
        pos["auto_close_date"] = close_date
        _save_account(acct)
    except Exception as exc:
        log.debug("paper: set_horizon failed for %s: %s", ticker, exc)


def close_due_positions():
    """auto_close_date 가 오늘(KST) 이하인 포지션 전량 청산(5거래일 horizon).
    periodic 태스크가 호출. 청산 메시지 리스트 반환(없으면 빈 리스트)."""
    acct = get_account()
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    due = [t for t, p in acct.get("positions", {}).items()
           if p.get("auto_close_date") and p["auto_close_date"] <= today]
    out = []
    for t in due:
        ok, msg = close_position(t, idem=f"horizon:{t}:{today}")
        if ok:
            out.append(msg)
    return out


# ─── valuation / summary (price_fn 주입으로 테스트 가능) ──────────────────────
def positions_with_pnl(price_fn=None) -> list[dict]:
    """각 포지션 + 현재가·평가액(KRW)·미실현 P&L. price_fn(ticker)->(px_native,
    market) 미지정 시 live_native_price 사용(네트워크). 가격 실패 시 그 포지션은
    cur=None 으로 graceful."""
    price_fn = price_fn or live_native_price
    acct = get_account()
    out = []
    for tkr, pos in acct["positions"].items():
        try:
            px, _ = price_fn(tkr)
        except Exception:
            px = None
        _, fx = _market_fx(pos.get("market") or "")
        row = {
            "ticker": tkr, "qty": pos["qty"], "avg_cost_native": pos["avg_cost_native"],
            "currency": pos.get("currency"), "market": pos.get("market"),
            "cost_basis_krw": pos["cost_basis_krw"], "cur_native": px,
            "auto_close_date": pos.get("auto_close_date"),
        }
        if px is not None and fx:
            row["value_krw"] = pos["qty"] * px * fx
            row["unrealized_krw"] = row["value_krw"] - pos["cost_basis_krw"]
            row["ret_pct"] = (px / pos["avg_cost_native"] - 1.0) * 100 if pos["avg_cost_native"] else None
        else:
            row["value_krw"] = None
            row["unrealized_krw"] = None
            row["ret_pct"] = None
        out.append(row)
    out.sort(key=lambda r: -(r.get("value_krw") or r["cost_basis_krw"]))
    return out


def summary(price_fn=None) -> dict:
    """계좌 요약: 현금·포지션 평가·총자산·실현/미실현·총수익률."""
    acct = get_account()
    rows = positions_with_pnl(price_fn)
    pos_value = sum(r["value_krw"] for r in rows if r.get("value_krw") is not None)
    cost_open = sum(r["cost_basis_krw"] for r in rows if r.get("value_krw") is not None)
    unreal = sum(r["unrealized_krw"] for r in rows if r.get("unrealized_krw") is not None)
    total = acct["cash_krw"] + pos_value
    start = acct["starting_capital_krw"] or 1.0
    return {
        "cash_krw": acct["cash_krw"],
        "positions_value_krw": pos_value,
        "total_equity_krw": total,
        "starting_capital_krw": acct["starting_capital_krw"],
        "realized_pnl_krw": acct["realized_pnl_krw"],
        "unrealized_pnl_krw": unreal,
        "total_return_pct": (total / start - 1.0) * 100,
        "n_positions": len(rows),
        "rows": rows,
        "trades": acct.get("trades", []),
        "stats": trade_stats(),
        "auto_size_pct": auto_size_pct(),
        "pending": acct.get("pending", []),
        "equity_history": acct.get("equity_history", []),
        "priced_all": all(r.get("value_krw") is not None for r in rows),
        "e1_mode": e1_mode(),
    }
