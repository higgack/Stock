"""Condition-based watchlist alerts (inspired by vibe-trade's heartbeat +
trigger pattern, 2026-06-04 user request).

Cheap, deterministic monitoring — NO LLM. A systemd timer runs `check_all()`
periodically; for each watched ticker it fetches one yfinance series (reusing
chart_data.fetch_chart_payload) and evaluates the user's conditions. When a
condition flips false→true (edge-triggered, so it fires once not every cycle)
it sends a Telegram alert to the chat that registered the watch, suggesting
`/<TICKER>` for a full analysis. Educational stance preserved — we ALERT, we
never execute trades.

Conditions (space/comma separated):
  rsi<30  rsi>70       — RSI(14) crosses a threshold
  price>950  price<800 — current price crosses a level
  >sma50  <sma200      — price above/below a moving average
  52whigh  52wlow      — price at/near 52-week high/low
  earnings             — next earnings within 5 days (best-effort)

Storage: ~/.tradingagents/watchlist.json  (atomic writes).
Cost: ₩0 (yfinance only). LLM only when the user later runs /<TICKER>.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
WATCHLIST_PATH = Path.home() / ".tradingagents" / "watchlist.json"
ALERTS_PATH = Path.home() / ".tradingagents" / "watchlist_alerts.jsonl"

MAX_WATCHES_PER_CHAT = 50
MAX_CONDITIONS = 8

# ── condition parsing ────────────────────────────────────────────────────
# KR 수급 전환 (외인/기관 5일 순매수) — .KS/.KQ 만, edge-trigger 가 곧
# "전환"(순매도→순매수) 을 잡는다. pykrx (KRX_ID) 필요, 없으면 graceful skip.
_FLOW_CONDS = {"foreignbuy", "foreignsell", "instbuy", "instsell"}
_COND_PATTERNS = [
    re.compile(r"^rsi([<>])(\d{1,3}(?:\.\d+)?)$", re.IGNORECASE),
    re.compile(r"^price([<>])(\d+(?:\.\d+)?)$", re.IGNORECASE),
    re.compile(r"^([<>])sma(50|200)$", re.IGNORECASE),
    re.compile(r"^52w(high|low)$", re.IGNORECASE),
    re.compile(r"^earnings$", re.IGNORECASE),
    re.compile(r"^(foreignbuy|foreignsell|instbuy|instsell)$", re.IGNORECASE),
]


def parse_conditions(text: str) -> tuple[list[str], list[str]]:
    """Split + validate a conditions string. Returns (valid, invalid).
    Tokens are normalized to lowercase canonical form."""
    if not text:
        return [], []
    raw = [t.strip().lower() for t in re.split(r"[\s,]+", text) if t.strip()]
    valid, invalid = [], []
    for tok in raw:
        if any(p.match(tok) for p in _COND_PATTERNS):
            if tok not in valid:
                valid.append(tok)
        else:
            invalid.append(tok)
    return valid[:MAX_CONDITIONS], invalid


# ── storage ──────────────────────────────────────────────────────────────
def _load() -> list[dict]:
    try:
        if WATCHLIST_PATH.exists():
            data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception as exc:
        log.warning("watchlist: load failed: %s", exc)
    return []


def _save(watches: list[dict]) -> None:
    try:
        WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = WATCHLIST_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(watches, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(WATCHLIST_PATH)
    except Exception as exc:
        log.warning("watchlist: save failed: %s", exc)


def add_watch(ticker: str, chat_id, conditions: list[str]) -> dict:
    """Add (or extend) a watch for (chat_id, ticker). If one exists, merge
    conditions. Returns the resulting watch dict."""
    ticker = ticker.strip().upper()
    chat_id = str(chat_id)
    watches = _load()
    existing = next(
        (w for w in watches
         if str(w.get("chat_id")) == chat_id and w.get("ticker") == ticker),
        None,
    )
    if existing is not None:
        merged = list(dict.fromkeys((existing.get("conditions") or []) + conditions))
        existing["conditions"] = merged[:MAX_CONDITIONS]
        _save(watches)
        return existing
    # cap per chat
    chat_count = sum(1 for w in watches if str(w.get("chat_id")) == chat_id)
    if chat_count >= MAX_WATCHES_PER_CHAT:
        raise ValueError(f"워치 한도 초과 (최대 {MAX_WATCHES_PER_CHAT}개)")
    w = {
        "id": secrets.token_hex(3),
        "ticker": ticker,
        "chat_id": chat_id,
        "conditions": conditions[:MAX_CONDITIONS],
        "added": datetime.now(_KST).isoformat(timespec="seconds"),
        "fired": {},   # condition -> last fired ts (edge-trigger state)
    }
    watches.append(w)
    _save(watches)
    return w


def remove_watch(chat_id, key: str) -> int:
    """Remove watches for chat_id matching `key` (id or ticker). Returns count.
    key='all' removes every watch for the chat."""
    chat_id = str(chat_id)
    key = (key or "").strip()
    watches = _load()
    before = len(watches)

    def keep(w):
        if str(w.get("chat_id")) != chat_id:
            return True
        if key.lower() == "all":
            return False
        return not (w.get("id") == key or w.get("ticker") == key.upper())

    kept = [w for w in watches if keep(w)]
    _save(kept)
    return before - len(kept)


def list_watches(chat_id) -> list[dict]:
    chat_id = str(chat_id)
    return [w for w in _load() if str(w.get("chat_id")) == chat_id]


# ── evaluation ───────────────────────────────────────────────────────────
def _last(arr):
    if not arr:
        return None
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def _next_earnings_days(ticker: str):
    """Best-effort days until next earnings (None if unknown)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = getattr(t, "calendar", None)
        ed = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            if isinstance(v, (list, tuple)) and v:
                ed = v[0]
            else:
                ed = v
        if ed is None:
            return None
        d = ed.date() if hasattr(ed, "date") else ed
        today = datetime.now(_KST).date()
        return (d - today).days
    except Exception:
        return None


def evaluate(ticker: str, conditions: list[str]) -> dict:
    """Fetch one series + evaluate each condition. Returns
    {cond: (bool_met, detail_str)}. Empty dict when data unavailable."""
    try:
        from bot.chart_data import fetch_chart_payload
        payload = fetch_chart_payload(ticker, interval="1d", period="1y")
    except Exception as exc:
        log.warning("watchlist: fetch failed for %s: %s", ticker, exc)
        return {}
    if not payload:
        return {}
    closes = [c for c in (payload.get("close") or []) if c is not None]
    if not closes:
        return {}
    price = closes[-1]
    rsi = _last(payload.get("rsi"))
    sma50 = _last(payload.get("sma50"))
    sma200 = _last(payload.get("sma200"))
    hi52, lo52 = max(closes), min(closes)
    cur = payload.get("currency", "$")

    # KR 수급 (외인/기관 5일 순매수) — 해당 조건이 있을 때만 1회 fetch.
    flow = None
    if any(c in _FLOW_CONDS for c in conditions):
        try:
            from bot.pykrx_client import get_kr_trading_flow
            flow = get_kr_trading_flow(ticker, days_back=5)
        except Exception as exc:
            log.warning("watchlist: flow fetch failed for %s: %s", ticker, exc)
            flow = None

    def _eok(won):
        try:
            return f"{won / 1e8:,.0f}억"
        except Exception:
            return str(won)

    out: dict = {}
    for cond in conditions:
        met, detail = False, ""
        m = re.match(r"^rsi([<>])(\d{1,3}(?:\.\d+)?)$", cond)
        if m and rsi is not None:
            thr = float(m.group(2))
            met = rsi < thr if m.group(1) == "<" else rsi > thr
            detail = f"RSI {rsi} {m.group(1)} {thr:g}"
            out[cond] = (met, detail); continue
        m = re.match(r"^price([<>])(\d+(?:\.\d+)?)$", cond)
        if m:
            thr = float(m.group(2))
            met = price < thr if m.group(1) == "<" else price > thr
            detail = f"현재가 {cur}{price:g} {m.group(1)} {cur}{thr:g}"
            out[cond] = (met, detail); continue
        m = re.match(r"^([<>])sma(50|200)$", cond)
        if m:
            sma = sma50 if m.group(2) == "50" else sma200
            if sma is not None:
                met = price < sma if m.group(1) == "<" else price > sma
                detail = f"현재가 {cur}{price:g} {m.group(1)} {m.group(2)}SMA {cur}{sma:g}"
            out[cond] = (met, detail); continue
        m = re.match(r"^52w(high|low)$", cond)
        if m:
            if m.group(1) == "high":
                met = price >= hi52 * 0.999
                detail = f"현재가 {cur}{price:g} ≈ 52주 최고 {cur}{hi52:g}"
            else:
                met = price <= lo52 * 1.001
                detail = f"현재가 {cur}{price:g} ≈ 52주 최저 {cur}{lo52:g}"
            out[cond] = (met, detail); continue
        if cond == "earnings":
            days = _next_earnings_days(ticker)
            if days is not None:
                met = 0 <= days <= 5
                detail = f"다음 실적 D-{days}"
            out[cond] = (met, detail); continue
        if cond in _FLOW_CONDS:
            if flow:
                fn = flow.get("foreign_net", 0)
                inn = flow.get("institutional_net", 0)
                if cond == "foreignbuy":
                    met = fn > 0; detail = f"외인 5일 순매수 {_eok(fn)}"
                elif cond == "foreignsell":
                    met = fn < 0; detail = f"외인 5일 순매도 {_eok(fn)}"
                elif cond == "instbuy":
                    met = inn > 0; detail = f"기관 5일 순매수 {_eok(inn)}"
                else:  # instsell
                    met = inn < 0; detail = f"기관 5일 순매도 {_eok(inn)}"
            out[cond] = (met, detail); continue
    return out


# ── push ─────────────────────────────────────────────────────────────────
def _push(chat_id, text: str) -> bool:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        log.warning("watchlist: TELEGRAM_BOT_TOKEN missing — cannot alert")
        return False
    try:
        import httpx
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        return r.status_code == 200
    except Exception as exc:
        log.warning("watchlist: push failed: %s", exc)
        return False


def _log_alert(ticker: str, chat_id, hits: list[str]) -> None:
    """Append a fired alert to the history log (dashboard 알림 이력)."""
    try:
        ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "ticker": ticker,
            "chat_id": str(chat_id),
            "hits": hits,
        }
        with ALERTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("watchlist: alert log failed: %s", exc)


def load_alerts(limit: int = 200) -> list[dict]:
    """Recent fired alerts (newest first) for the dashboard."""
    try:
        if not ALERTS_PATH.exists():
            return []
        lines = ALERTS_PATH.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        out.reverse()
        return out
    except Exception:
        return []


def all_watches() -> list[dict]:
    """Every watch across all chats (for the dashboard)."""
    return _load()


# ── checker (timer entry) ────────────────────────────────────────────────
def check_all() -> int:
    """Evaluate every watch; fire edge-triggered alerts. Returns # fired."""
    watches = _load()
    if not watches:
        return 0
    # group by ticker so each symbol is fetched once even if multiple chats
    by_ticker: dict[str, list[dict]] = {}
    for w in watches:
        by_ticker.setdefault(w.get("ticker", ""), []).append(w)

    fired_total = 0
    now = datetime.now(_KST).isoformat(timespec="seconds")
    for ticker, group in by_ticker.items():
        if not ticker:
            continue
        # union of all conditions across watches on this ticker
        all_conds = set()
        for w in group:
            all_conds.update(w.get("conditions") or [])
        results = evaluate(ticker, list(all_conds))
        if not results:
            continue
        for w in group:
            fired = w.setdefault("fired", {})
            hits = []
            for cond in (w.get("conditions") or []):
                res = results.get(cond)
                if not res:
                    continue
                met, detail = res
                if met and cond not in fired:
                    hits.append(detail or cond)
                    fired[cond] = now
                elif not met and cond in fired:
                    # reset — edge trigger so it can fire again next time
                    del fired[cond]
            if hits:
                body = "\n".join("• " + h for h in hits)
                msg = (f"🔔 <b>{ticker}</b> 조건 충족\n{body}\n\n분석: /{ticker}")
                _log_alert(ticker, w.get("chat_id"), hits)
                if _push(w.get("chat_id"), msg):
                    fired_total += 1
    _save(watches)
    # 대시보드 갱신 (알림 이력 / fired state 반영) — best-effort.
    try:
        from bot.dashboard import regenerate_watchlist_index
        regenerate_watchlist_index()
    except Exception as exc:
        log.warning("watchlist: dashboard regen failed: %s", exc)
    log.info("watchlist: checked %d ticker(s), fired %d alert(s)",
             len(by_ticker), fired_total)
    return fired_total


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    # Load .env for TELEGRAM_BOT_TOKEN when run standalone via timer.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass
    check_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
