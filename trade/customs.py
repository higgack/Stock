"""관세청 품목별 수출입실적(GW) — fetch + local monthly cache.

data.go.kr dataset 15101609. Pulls official monthly CONFIRMED export/
import figures by HS code and caches them locally so the signal layer
(threshold alerts, item comparison) can query without re-hitting the
API. This is the numeric layer the trade-bot otherwise lacks: BeOn
relays the same customs figures as IMAGES (the bot never OCRs them), so
store.db has no machine-readable numbers — this module supplies them
for operator-pinned items only (see trade/hs_map.py for the bridge).

What it does NOT do: replace BeOn. BeOn's value is the EARLY 旬(10-day)
preliminary signal + the 관련종목 stock mapping; this API is monthly,
CONFIRMED-only (published ~15th), HS-keyed, no stocks. It is a
complement, not a substitute.

API shape (confirmed against a live 2026.01 response):
    GET …/1220000/Itemtrade/getItemtradeList
        ?serviceKey=…&strtYymm=YYYYMM&endYymm=YYYYMM&hsSgn=<HS prefix>
    → XML <response><body><items><item>…</item>…
       item fields: hsCode (10-digit leaf), statKor (Korean name),
       year ("YYYY.MM"), expDlr / impDlr / balPayments (USD),
       expWgt / impWgt (kg). balPayments == expDlr - impDlr.

`hsSgn` is a PREFIX: querying 1902 returns every 10-digit leaf under it.
So a pinned 6-digit code aggregates several leaves; aggregate_by_month
sums them per month at the pinned granularity. A pinned exact 10-digit
code (e.g. 1902301010 라면) returns just that line.

Auth: serviceKey from env TRADE_DATA_GO_KR_KEY (host .env only — never
committed). One data.go.kr key covers every dataset the operator has
applied for, so this same key serves all 관세청 endpoints if we add more.

Amounts are raw USD, weights raw kg (verified: 라면 2026.01 expDlr
129,673,809 / expWgt 31,427,100 kg → ~$4.1/kg, a sane noodle unit price;
thousand-dollar units would give absurd $4,100/kg).
"""

from __future__ import annotations

import os
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional


ENDPOINT = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"

_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
DEFAULT_DB = _DATA_DIR / "customs.db"

# data.go.kr standard paging knobs. A single HS prefix rarely has more
# than a few dozen leaf lines per month; 1000 covers a multi-month pull
# comfortably in one request.
_DEFAULT_ROWS = 1000

_HTTP_TIMEOUT_S = 20


class CustomsAPIError(RuntimeError):
    """Raised when the API returns a non-success resultCode, so callers
    can distinguish a real upstream failure (alert-worthy) from an empty
    but successful window (normal)."""


# ---------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------

def _norm_year(raw: str) -> str:
    """'2026.01' → '2026-01'. Leaves already-dashed or odd values as-is
    after a best-effort dot swap so a format drift doesn't crash the parse."""
    return (raw or "").strip().replace(".", "-")


def parse_response(xml_text: str) -> list[dict]:
    """Parse the getItemtradeList XML body into a list of leaf-row dicts.

    Each dict: hs_code, stat_kor, year_month ('YYYY-MM'), exp_dlr,
    imp_dlr, bal_payments, exp_wgt, imp_wgt (ints; missing/blank → 0).

    Raises CustomsAPIError when the response header reports anything
    other than resultCode 00 (e.g. key not registered, quota exceeded),
    so the caller never silently caches an error page as data.
    """
    root = ET.fromstring(xml_text)

    code_el = root.find(".//header/resultCode")
    code = (code_el.text or "").strip() if code_el is not None else ""
    if code and code != "00":
        msg_el = root.find(".//header/resultMsg")
        msg = (msg_el.text or "").strip() if msg_el is not None else ""
        raise CustomsAPIError(f"resultCode={code} resultMsg={msg!r}")

    def _int(el, tag) -> int:
        child = el.find(tag)
        if child is None or not (child.text or "").strip():
            return 0
        try:
            return int(child.text.strip())
        except ValueError:
            return 0

    def _txt(el, tag) -> str:
        child = el.find(tag)
        return (child.text or "").strip() if child is not None else ""

    rows: list[dict] = []
    for item in root.findall(".//body/items/item"):
        hs = _txt(item, "hsCode")
        ym = _norm_year(_txt(item, "year"))
        if not hs or not ym:
            continue
        rows.append({
            "hs_code": hs,
            "stat_kor": _txt(item, "statKor"),
            "year_month": ym,
            "exp_dlr": _int(item, "expDlr"),
            "imp_dlr": _int(item, "impDlr"),
            "bal_payments": _int(item, "balPayments"),
            "exp_wgt": _int(item, "expWgt"),
            "imp_wgt": _int(item, "impWgt"),
        })
    return rows


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "trade-bot/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


def fetch(
    hs_code: str,
    start_yymm: str,
    end_yymm: str,
    *,
    key: Optional[str] = None,
    fetcher: Callable[[str], str] = _http_get,
) -> list[dict]:
    """Fetch raw leaf rows for `hs_code` (prefix) over [start, end] YYYYMM.

    `fetcher` is injectable so tests exercise parse logic against a
    recorded response without a live call. `key` defaults to env
    TRADE_DATA_GO_KR_KEY; a missing key raises so the caller fails loud
    rather than hitting the API unauthenticated.
    """
    key = key or os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        raise CustomsAPIError("TRADE_DATA_GO_KR_KEY not set")
    qs = urllib.parse.urlencode({
        "serviceKey": key,
        "strtYymm": start_yymm,
        "endYymm": end_yymm,
        "hsSgn": hs_code,
        "numOfRows": _DEFAULT_ROWS,
        "pageNo": 1,
    })
    return parse_response(fetcher(f"{ENDPOINT}?{qs}"))


def aggregate_by_month(rows: list[dict], hs_prefix: str) -> dict[str, dict]:
    """Sum leaf rows whose hs_code starts with `hs_prefix`, grouped by
    year_month. Returns {ym: {exp_dlr, imp_dlr, bal_payments,
    exp_wgt, imp_wgt}} with bal_payments recomputed as exp-imp so it
    stays consistent after summation.

    Pinning an exact 10-digit code collapses to that single leaf;
    pinning a shorter prefix rolls its sub-items up to one number per
    month — matching the granularity the operator chose.
    """
    out: dict[str, dict] = {}
    for r in rows:
        if not r["hs_code"].startswith(hs_prefix):
            continue
        ym = r["year_month"]
        acc = out.setdefault(ym, {
            "exp_dlr": 0, "imp_dlr": 0, "exp_wgt": 0, "imp_wgt": 0,
        })
        acc["exp_dlr"] += r["exp_dlr"]
        acc["imp_dlr"] += r["imp_dlr"]
        acc["exp_wgt"] += r["exp_wgt"]
        acc["imp_wgt"] += r["imp_wgt"]
    for ym, acc in out.items():
        acc["bal_payments"] = acc["exp_dlr"] - acc["imp_dlr"]
    return out


def month_over_month(series: list[dict], field: str = "exp_dlr") -> Optional[float]:
    """Percent change of `field` from the second-latest to the latest
    month in a month-ascending series. None when < 2 points or the
    previous value is 0 (can't form a ratio). Pure — the signal layer
    calls this to decide whether to alert."""
    if len(series) < 2:
        return None
    prev = series[-2].get(field) or 0
    curr = series[-1].get(field) or 0
    if prev == 0:
        return None
    return (curr - prev) / prev * 100.0


# ---------------------------------------------------------------------
# Local cache (SQLite) — mirrors watchlist.py's self-contained store
# ---------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customs_stats (
  hs_code TEXT NOT NULL,        -- the PINNED prefix, not the leaf code
  year_month TEXT NOT NULL,     -- 'YYYY-MM'
  exp_dlr INTEGER NOT NULL,
  imp_dlr INTEGER NOT NULL,
  bal_payments INTEGER NOT NULL,
  exp_wgt INTEGER NOT NULL,
  imp_wgt INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (hs_code, year_month)
);
"""


def open_db(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def session(path: Path | str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    conn = open_db(path)
    try:
        yield conn
    finally:
        conn.close()


def upsert_month(
    conn: sqlite3.Connection,
    hs_code: str,
    year_month: str,
    agg: dict,
) -> bool:
    """Insert or replace one (hs_code, month) aggregate. Returns True if
    this month's row was new (caller uses that to gate first-seen alerts:
    a brand-new month is a candidate to notify, a refreshed existing
    month is not)."""
    existed = conn.execute(
        "SELECT 1 FROM customs_stats WHERE hs_code = ? AND year_month = ?",
        (hs_code, year_month),
    ).fetchone() is not None
    conn.execute(
        "INSERT OR REPLACE INTO customs_stats "
        "(hs_code, year_month, exp_dlr, imp_dlr, bal_payments, "
        " exp_wgt, imp_wgt, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            hs_code, year_month,
            agg["exp_dlr"], agg["imp_dlr"], agg["bal_payments"],
            agg["exp_wgt"], agg["imp_wgt"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return not existed


def get_series(conn: sqlite3.Connection, hs_code: str) -> list[dict]:
    """All cached months for an HS code, oldest-first (ready for
    month_over_month and comparison ranking)."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM customs_stats WHERE hs_code = ? "
            "ORDER BY year_month ASC",
            (hs_code,),
        )
    ]


# ---------------------------------------------------------------------
# Comparison view — shared by the dashboard panel and the /customs DM
# ---------------------------------------------------------------------

def fmt_usd(n: int | None) -> str:
    """Compact USD: 129673809 → '$129.7M', -5364818 → '-$5.4M'. The API
    returns raw dollars, so amounts are large — abbreviate for a one-line
    cell."""
    n = n or 0
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1_000_000_000:
        return f"{sign}${a / 1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.0f}K"
    return f"{sign}${a}"


def fmt_pct(p: float | None) -> str:
    """전월비 → '+30.2%' / '-12.0%' / '—' when undefined (first month or
    prior value 0)."""
    if p is None:
        return "—"
    return f"{p:+.1f}%"


def summary_rows(
    conn: sqlite3.Connection,
    pins: list[tuple[str, str]],
) -> list[dict]:
    """One comparison row per pinned (item, hs_code).

    Each row: item, hs_code, has_data, and — when cached data exists —
    year_month (latest), exp_dlr / imp_dlr / bal_payments (latest month)
    and exp_mom / imp_mom (% vs previous cached month).

    Pins with no cached month yet (just added, fetch hasn't run) come back
    has_data=False so the UI can show '수집 대기' instead of dropping them.
    Sorted data-first, then by latest export value desc — the operator's
    biggest export lines lead.
    """
    out: list[dict] = []
    for item, hs in pins:
        series = get_series(conn, hs)
        if not series:
            out.append({"item": item, "hs_code": hs, "has_data": False})
            continue
        latest = series[-1]
        out.append({
            "item": item,
            "hs_code": hs,
            "has_data": True,
            "year_month": latest["year_month"],
            "exp_dlr": latest["exp_dlr"],
            "imp_dlr": latest["imp_dlr"],
            "bal_payments": latest["bal_payments"],
            "exp_mom": month_over_month(series, "exp_dlr"),
            "imp_mom": month_over_month(series, "imp_dlr"),
            "months": len(series),
        })
    out.sort(
        key=lambda r: (r["has_data"], r.get("exp_dlr") or 0),
        reverse=True,
    )
    return out
