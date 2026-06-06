"""KIS 모의투자/실전 주문 어댑터 (E1).

E0 의 자체 체결(live_native_price → 즉시)을 KIS 서버 체결로 교체.
paper_trading._order 가 KR + E1 키 존재 시 이 어댑터로 자동 라우팅.

모의: https://openapivts.koreainvestment.com:29443 (가짜 돈, 리스크 0)
실전: https://openapi.koreainvestment.com:9443 (진짜 돈, E2+)

인터페이스는 pluggable — 나중에 IBKR/Toss 도 같은 shape.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.kis_trading")

_BASE_PAPER = "https://openapivts.koreainvestment.com:29443"
_BASE_LIVE = "https://openapi.koreainvestment.com:9443"
_KST = timezone(timedelta(hours=9))
_HTTP_TIMEOUT = 15
_TOKEN_MARGIN = 3600  # 만료 1h 전 갱신


def _env(key: str) -> str:
    return os.environ.get(key, "").strip()


def is_e1_available() -> bool:
    """E1 모드 가능 — KIS 모의투자 3개 env 모두 set."""
    return bool(_env("KIS_PAPER_APP_KEY")
                and _env("KIS_PAPER_APP_SECRET")
                and _env("KIS_PAPER_ACCT_NO"))


# ─── numeric helper ──────────────────────────────────────────────────────────
def _to_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        s = str(v).replace(",", "").strip()
        return int(float(s)) if s else None
    except (ValueError, TypeError):
        return None


# ─── adapter ─────────────────────────────────────────────────────────────────
class KisTradingAdapter:
    """KIS 주문 어댑터. 모의(paper) / 실전(live) 모드 분기."""

    def __init__(self, mode: str = "paper"):
        self.mode = mode
        if mode == "paper":
            self.base_url = _BASE_PAPER
            self.app_key = _env("KIS_PAPER_APP_KEY")
            self.app_secret = _env("KIS_PAPER_APP_SECRET")
            acct_raw = _env("KIS_PAPER_ACCT_NO")
            self._token_path = (Path.home() / ".tradingagents"
                                / "cache" / "kis_paper_token.json")
        else:
            self.base_url = _BASE_LIVE
            self.app_key = _env("KIS_LIVE_APP_KEY") or _env("KIS_APP_KEY")
            self.app_secret = (_env("KIS_LIVE_APP_SECRET")
                               or _env("KIS_APP_SECRET"))
            acct_raw = _env("KIS_LIVE_ACCT_NO")
            self._token_path = (Path.home() / ".tradingagents"
                                / "cache" / "kis_live_order_token.json")

        parts = acct_raw.replace("-", "")
        self.cano = parts[:8] if len(parts) >= 8 else parts
        self.acnt_prdt_cd = parts[8:10] if len(parts) >= 10 else "01"

    def _tr_id(self, base: str) -> str:
        """'TTC0802U' → 'VTTC0802U'(paper) / 'TTTC0802U'(live)."""
        return ("V" if self.mode == "paper" else "T") + base

    # ── OAuth2 token ──────────────────────────────────────────────────────
    def _get_token(self) -> Optional[str]:
        try:
            if self._token_path.exists():
                cached = json.loads(self._token_path.read_text())
                if time.time() < cached.get("expires_at", 0) - _TOKEN_MARGIN:
                    return cached.get("access_token")
        except Exception:
            pass

        try:
            resp = requests.post(
                f"{self.base_url}/oauth2/tokenP",
                headers={"Content-Type": "application/json"},
                json={"grant_type": "client_credentials",
                      "appkey": self.app_key,
                      "appsecret": self.app_secret},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if token:
                payload = {"access_token": token,
                           "expires_at": time.time()
                           + int(data.get("expires_in", 86400))}
                self._token_path.parent.mkdir(parents=True, exist_ok=True)
                self._token_path.write_text(json.dumps(payload))
                log.info("kis_trading[%s]: token issued", self.mode)
                return token
        except Exception as exc:
            log.warning("kis_trading: token failed: %s", exc)
        return None

    def _headers(self, tr_id: str) -> Optional[dict]:
        token = self._get_token()
        if not token:
            return None
        return {
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
        }

    # ── HTTP helpers ──────────────────────────────────────────────────────
    def _post(self, path: str, tr_id: str, body: dict) -> Optional[dict]:
        headers = self._headers(tr_id)
        if not headers:
            return None
        try:
            resp = requests.post(f"{self.base_url}{path}",
                                 headers=headers, json=body,
                                 timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                log.warning("kis_trading: %s rt_cd=%s msg=%s",
                            tr_id, data.get("rt_cd"), data.get("msg1", ""))
                return None
            return data
        except Exception as exc:
            log.warning("kis_trading: %s POST failed: %s", tr_id, exc)
            return None

    def _get_api(self, path: str, tr_id: str,
                 params: dict) -> Optional[dict]:
        headers = self._headers(tr_id)
        if not headers:
            return None
        try:
            resp = requests.get(f"{self.base_url}{path}",
                                headers=headers, params=params,
                                timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                log.warning("kis_trading: %s rt_cd=%s msg=%s",
                            tr_id, data.get("rt_cd"), data.get("msg1", ""))
                return None
            return data
        except Exception as exc:
            log.warning("kis_trading: %s GET failed: %s", tr_id, exc)
            return None

    # ── 주문 제출 ─────────────────────────────────────────────────────────
    def submit_order(self, code: str, side: str, qty: int,
                     ord_type: str = "market",
                     limit_price: int = 0) -> Optional[dict]:
        """주문 제출 → {order_no, org_no} or None.

        code: 6자리 종목코드 ("005930")
        side: "buy" | "sell"
        ord_type: "market" | "limit"
        """
        tr_base = "TTC0802U" if side == "buy" else "TTC0801U"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": "01" if ord_type == "market" else "00",
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": (str(int(limit_price))
                         if ord_type == "limit" else "0"),
        }
        data = self._post("/uapi/domestic-stock/v1/trading/order-cash",
                          self._tr_id(tr_base), body)
        if not data:
            return None
        out = data.get("output") or {}
        return {"order_no": out.get("ODNO", ""),
                "org_no": out.get("KRX_FWDG_ORD_ORGNO", "")}

    # ── 잔고 조회 ─────────────────────────────────────────────────────────
    def get_balance(self) -> Optional[dict]:
        """→ {cash, positions: [{code,name,qty,avg_price,current_price,pnl_amt}],
             total_eval}."""
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        data = self._get_api(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            self._tr_id("TTC8434R"), params)
        if not data:
            return None

        positions = []
        for row in (data.get("output1") or []):
            qty = _to_int(row.get("hldg_qty"))
            if not qty:
                continue
            positions.append({
                "code": row.get("pdno", ""),
                "name": row.get("prdt_name", ""),
                "qty": qty,
                "avg_price": _to_int(row.get("pchs_avg_pric")),
                "current_price": _to_int(row.get("prpr")),
                "eval_amt": _to_int(row.get("evlu_amt")),
                "pnl_amt": _to_int(row.get("evlu_pfls_amt")),
            })

        out2 = (data.get("output2") or [{}])
        s = out2[0] if out2 else {}
        return {
            "cash": _to_int(s.get("dnca_tot_amt")) or 0,
            "positions": positions,
            "total_eval": _to_int(s.get("tot_evlu_amt")) or 0,
        }

    # ── 당일 체결 내역 ────────────────────────────────────────────────────
    def get_executions(self) -> list[dict]:
        """→ [{order_no, code, side, qty, price, amount, ts}]."""
        today = datetime.now(_KST).strftime("%Y%m%d")
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "INQR_STRT_DT": today, "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00",
            "PDNO": "", "CCLD_DVSN": "01",
            "ORD_GNO_BRNO": "", "ODNO": "",
            "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        data = self._get_api(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            self._tr_id("TTC8001R"), params)
        if not data:
            return []
        results = []
        for row in (data.get("output1") or []):
            qty = _to_int(row.get("tot_ccld_qty"))
            if not qty:
                continue
            results.append({
                "order_no": row.get("odno", ""),
                "code": row.get("pdno", ""),
                "side": ("sell" if row.get("sll_buy_dvsn_cd") == "01"
                         else "buy"),
                "qty": qty,
                "price": (_to_int(row.get("avg_prvs"))
                          or _to_int(row.get("ccld_pric"))),
                "amount": _to_int(row.get("tot_ccld_amt")),
                "ts": row.get("ord_tmd", ""),
            })
        return results

    def find_fill(self, order_no: str) -> Optional[dict]:
        """order_no 로 체결 검색. 미체결이면 None."""
        if not order_no:
            return None
        for fill in self.get_executions():
            if fill.get("order_no") == order_no:
                return fill
        return None

    # ── 주문 취소 ─────────────────────────────────────────────────────────
    def cancel_order(self, org_no: str, order_no: str,
                     qty: int = 0) -> bool:
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(int(qty)) if qty else "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            self._tr_id("TTC0803U"), body)
        return data is not None


# ─── singleton ───────────────────────────────────────────────────────────────
_adapter: Optional[KisTradingAdapter] = None


def get_adapter(mode: str = "paper") -> Optional[KisTradingAdapter]:
    global _adapter
    if not is_e1_available():
        return None
    if _adapter is None or _adapter.mode != mode:
        _adapter = KisTradingAdapter(mode)
    return _adapter


# ─── reconcile ───────────────────────────────────────────────────────────────
def reconcile() -> Optional[dict]:
    """KIS 잔고 ↔ 우리 ledger 비교. 불일치 → 상세 dict, 일치 → None."""
    adapter = get_adapter()
    if not adapter:
        return None
    balance = adapter.get_balance()
    if not balance:
        return {"error": "KIS 잔고 조회 실패"}

    try:
        from bot.paper_trading import get_account
    except ImportError:
        return {"error": "paper_trading import 실패"}
    acct = get_account()

    kis_pos = {p["code"]: p["qty"]
               for p in balance.get("positions", [])}
    our_pos: dict[str, int] = {}
    for tkr, pos in acct.get("positions", {}).items():
        if tkr.endswith(".KS") or tkr.endswith(".KQ"):
            our_pos[tkr.split(".")[0]] = int(pos.get("qty", 0))

    drifts = []
    for code in set(list(kis_pos) + list(our_pos)):
        ours = our_pos.get(code, 0)
        kis = kis_pos.get(code, 0)
        if ours != kis:
            drifts.append({"code": code, "ours": ours, "kis": kis})

    if drifts:
        return {"position_drifts": drifts,
                "kis_cash": balance.get("cash", 0)}
    return None
