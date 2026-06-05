"""E0 페이퍼 트레이딩 엔진 — 실거래 실행 골격에서 '돈만 뺀 것' (리스크 0).

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
지정가/next-open PENDING · 하드 캡 Risk Gate · NOAH 자동신호 = E0.5+ 증분.

통화: 단일 **KRW 계좌**. US 매수는 체결 시점 USD/KRW 로 환산해 원화 차감
(원화 투자자가 미국주식 사는 모델). 포지션 평단은 native 보관(% 수익률 깔끔),
원가는 KRW cost-basis 로 보관(환차손익까지 정확). graceful — 네트워크/키 부재
시 명령은 거부 메시지, 크래시 0.
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
_ACCOUNT = _HOME / "portfolio.json"
_AUDIT = _HOME / "audit.jsonl"

STARTING_CAPITAL_KRW = 10_000_000      # 페이퍼 시작 자본
_FX_FALLBACK_USDKRW = 1380.0           # USD/KRW fetch 실패 시
_SEEN_CAP = 500                        # idempotency 키 보관 상한
_TRADES_CAP = 500                      # 계좌 내 최근 체결 보관(전체는 audit.jsonl)
_E0_MARKETS = ("KR", "US")             # E0 지원 시장(통화)


# ─── persistence ─────────────────────────────────────────────────────────────
def _new_account() -> dict:
    return {
        "starting_capital_krw": float(STARTING_CAPITAL_KRW),
        "cash_krw": float(STARTING_CAPITAL_KRW),
        "realized_pnl_krw": 0.0,
        "positions": {},        # ticker -> {qty, avg_cost_native, cost_basis_krw, currency, market}
        "trades": [],           # 최근 체결(cap)
        "seen_keys": [],        # idempotency
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


# ─── public order API (I/O wrappers) ─────────────────────────────────────────
def _order(ticker: str, qty: float, side: str, idem: Optional[str]):
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
    acct = get_account()
    fn = _apply_buy if side == "buy" else _apply_sell
    ok, msg = fn(acct, ticker, float(qty), float(px), float(fx), currency, market,
                 time.time(), idem)
    if ok:
        _save_account(acct)
    return ok, msg


def buy(ticker: str, qty: float, idem: Optional[str] = None):
    """시장가 매수 — glitch-guarded 현재가 즉시 체결. (ok, 메시지)."""
    return _order(ticker, qty, "buy", idem)


def sell(ticker: str, qty: float, idem: Optional[str] = None):
    """시장가 매도. (ok, 메시지)."""
    return _order(ticker, qty, "sell", idem)


def close_position(ticker: str, idem: Optional[str] = None):
    """보유 전량 매도."""
    ticker = (ticker or "").strip().upper()
    pos = get_account()["positions"].get(ticker)
    if not pos:
        return False, f"보유 없음: {ticker}"
    return _order(ticker, pos["qty"], "sell", idem)


def reset():
    """계좌 초기화(페이퍼). 되돌릴 수 없음 — 호출부가 확인 책임."""
    _save_account(_new_account())
    _audit_log({"ts": time.time(), "event": "reset"})
    return True, f"페이퍼 계좌 초기화 — 시작 자본 ₩{STARTING_CAPITAL_KRW:,}"


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
        # 가격을 못 가져온 포지션이 있으면 평가/총자산은 부분값(표시 시 주의).
        "priced_all": all(r.get("value_krw") is not None for r in rows),
    }
