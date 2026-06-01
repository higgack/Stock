"""전 chapter 관세청 급변 스캐너 — auto-discovery of surging HS items.

Unlike fetch_customs (which pulls ONLY operator-pinned HS codes), this
module sweeps the whole HS table (chapters 01~97) once, computes each
10-digit leaf's month-over-month export move, and ranks two views the
operator asked for:

  📈 급등률 TOP — 전월비 +PCT% 이상 '상승만', % 내림차순, 상위 N
  💵 급증액 TOP — 변화 금액($) 내림차순 (가장 많이 늘어난 순), 상위 N

Design notes / blast-radius guards (see CLAUDE.md):
  - Cost 0: 관세청 data.go.kr is free; a full sweep is ~chapters × pages
    calls/day, well under the 10,000/day free quota.
  - Hard cap: only top-N per section land — blast radius ≤ 2·N by
    construction, independent of any value floor.
  - First-run baseline-silent: the very first sweep marks every current
    top item 'seen' WITHOUT alerting, so enabling the feature never
    blasts a wall of historical surges. Only items NEW to a later
    sweep alert.
  - Refresh + archive: the two live sections are REPLACED each run
    (latest snapshot only). Items that drop out are NOT lost — they
    persist forever in customs_surge_archive, surfaced on the dashboard
    so past surges stay inspectable (retention: unlimited, per operator).

The customs API requires an hsSgn, so the "whole table" is covered by
querying each 2-digit chapter (which returns all leaves under it) with
pagination. Pure ranking is separated from I/O so tests exercise it
against recorded rows.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from trade import customs

log = logging.getLogger("customs-scan")

# HS chapters 01~97 (98/99 are special/■ not used for goods trade).
CHAPTERS = [f"{i:02d}" for i in range(1, 98)]

# Tunables (env-overridable so the operator never edits code).
TOP_N = int(os.environ.get("TRADE_CUSTOMS_SURGE_TOP_N") or "30")
PCT_THRESHOLD = float(os.environ.get("TRADE_CUSTOMS_ALERT_PCT") or "30")
# Export-value floor for the 📈 급등률 (rate) section ONLY. Monthly customs
# figures are noisy: a dry-run on 2026-05 data showed 940 items above +30%
# at a $1M floor, mostly tiny lines where one shipment doubles a $300 base.
# Ranking by % would let those crowd out meaningful surges, so the rate
# section requires this minimum latest-month export value. The 💵 급증액
# (amount) section needs NO floor — ranking by Δ$ already buries small
# lines. $50M chosen from the operator's read of that histogram (260 items
# survive → top-30 are real, stock-relevant movers). Env-tunable.
RATE_MIN_USD = int(os.environ.get("TRADE_CUSTOMS_RATE_MIN_USD") or "50000000")
# Safety: max pages per chapter so a runaway never hammers the quota.
MAX_PAGES = int(os.environ.get("TRADE_CUSTOMS_SCAN_MAX_PAGES") or "60")
# Alert cap (shared shape with customs_alert).
ALERT_CAP = int(os.environ.get("TRADE_CUSTOMS_ALERT_CAP") or "10")
TEASER = 3

SECTION_RATE = "rate"      # 📈 급등률
SECTION_AMOUNT = "amount"  # 💵 급증액


# ───────────────────────── I/O: paged chapter fetch ─────────────────────────

def fetch_chapter(
    chapter: str,
    start_yymm: str,
    end_yymm: str,
    *,
    key: Optional[str] = None,
    fetcher: Optional[Callable[[str], str]] = None,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """All leaf rows under a 2-digit `chapter` over [start, end], paged.

    customs.fetch only grabs page 1 (fine for a narrow pinned prefix);
    a chapter rolls up hundreds–thousands of leaf×month rows, so we page.

    CRITICAL pagination guard: data.go.kr's Itemtrade endpoint can ignore
    pageNo and return page 1 for every request. The naive "stop when a
    page is short" never triggers for a full chapter (every page is 1000
    rows), so we'd refetch the same page up to max_pages times — and since
    build_series SUMS by (leaf, month), that inflates every figure by the
    page count (observed: 디램 $9.2B × 60 pages = $554B). So we dedup by
    (hs_code, year_month) — each leaf-month is one national row — and stop
    as soon as a page introduces NO new (hs_code, year_month) key. This is
    correct whether the API truly paginates (new keys each page until
    exhausted) or ignores pageNo (page 2 adds nothing → stop).

    `fetcher` resolves to customs._http_get at CALL time (not def time)
    so a test can monkeypatch customs._http_get and have it take."""
    fetcher = fetcher or customs._http_get
    key = key or os.environ.get("TRADE_DATA_GO_KR_KEY") or ""
    if not key:
        raise customs.CustomsAPIError("TRADE_DATA_GO_KR_KEY not set")
    by_key: dict[tuple[str, str], dict] = {}
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode({
            "serviceKey": key,
            "strtYymm": start_yymm,
            "endYymm": end_yymm,
            "hsSgn": chapter,
            "numOfRows": customs._DEFAULT_ROWS,
            "pageNo": page,
        })
        page_rows = customs.parse_response(fetcher(f"{customs.ENDPOINT}?{qs}"))
        if not page_rows:
            break
        before = len(by_key)
        for r in page_rows:
            by_key[(r["hs_code"], r["year_month"])] = r
        # No new (leaf, month) keys → the API isn't actually advancing
        # (pageNo ignored, or we've seen everything). Stop to avoid
        # summing duplicate pages.
        if len(by_key) == before:
            break
        if len(page_rows) < customs._DEFAULT_ROWS:
            break
    return list(by_key.values())


# ───────────────────────── pure ranking ─────────────────────────

def build_series(rows: list[dict]) -> dict[str, dict]:
    """Group raw leaf rows into {hs_code: {name, months:{ym: figures}}}.

    Only true 10-digit leaves are kept (a chapter response can echo the
    chapter aggregate row); aggregation guarantees one figure per
    (leaf, month)."""
    leaves: dict[str, dict] = {}
    for r in rows:
        hs = r["hs_code"]
        if len(hs) != 10 or not hs.isdigit():
            continue
        ym = r["year_month"]
        if not ym or len(ym) < 6:
            continue
        # parse_response emits the Korean name under 'stat_kor' (e.g.
        # '디램'), not 'name'; reading the wrong key made every label fall
        # back to the bare HS code. Keep the latest non-empty name.
        name = r.get("stat_kor") or r.get("name")
        node = leaves.setdefault(hs, {"name": name or hs, "months": {}})
        if name:
            node["name"] = name
        # ASSIGN, not sum: a 10-digit leaf has exactly one national figure
        # per month, so the latest row for (leaf, month) wins. (Summing was
        # how duplicate pages inflated figures ~60× before fetch_chapter's
        # dedup; assignment is a second line of defence.)
        node["months"][ym] = {
            "exp_dlr": r.get("exp_dlr") or 0,
            "imp_dlr": r.get("imp_dlr") or 0,
        }
    return leaves


def _latest_move(months: dict) -> Optional[dict]:
    """Latest-vs-previous export move, or None when < 2 usable months.

    The subtlety (2026-06-01 'live wiped' bug): data.go.kr returns rows
    for FUTURE months (current calendar month and beyond) pre-filled with
    expDlr=0 because they aren't confirmed yet. Naively taking the two
    calendar-latest months compares those zeros and zeroes the whole
    ranking early each month. But we must NOT just skip ALL zero months —
    a genuine '0 → $200M' first-ever export is a real surge worth ranking.

    So we drop only months STRICTLY AFTER the current calendar month
    (unpublished future), then take the latest two of what remains. A past
    month sitting at 0 (a real new-export baseline) is kept, so 0→값 still
    ranks; a not-yet-confirmed future 0 is dropped, so the comparison
    tracks the latest two real months (e.g. 4월 vs 3월 until 5월 confirms).
    delta is always defined; pct is None when prev=0."""
    ordered = sorted(months)
    # Trim the UNCONFIRMED tail: drop trailing zero-export months. 관세청
    # confirms a month ~the 15th of the following month and data.go.kr
    # pre-fills not-yet-confirmed months (current + next, sometimes more)
    # with expDlr=0, so the newest real datum is the last NON-zero month.
    # Trimming only the trailing zeros (not interior ones) means a genuine
    # 0→값 first-export keeps its 0 as `prev` (the 0 isn't trailing — a
    # bigger value follows it) and still ranks, while not-yet-published
    # trailing zeros are removed — fixing the 2026-06-01 'live wiped' bug
    # without dropping real new-export surges.
    while ordered and (months[ordered[-1]].get("exp_dlr") or 0) == 0:
        ordered.pop()
    if len(ordered) < 2:
        return None
    cur_ym, prev_ym = ordered[-1], ordered[-2]
    curr = months[cur_ym].get("exp_dlr") or 0
    prev = months[prev_ym].get("exp_dlr") or 0
    pct = ((curr - prev) / prev * 100.0) if prev else None
    return {
        "year_month": cur_ym,
        "prev": prev,
        "curr": curr,
        "delta": curr - prev,
        "pct": pct,
    }


def rank(
    leaves: dict[str, dict],
    *,
    top_n: int = TOP_N,
    pct_threshold: float = PCT_THRESHOLD,
    rate_min_usd: int = RATE_MIN_USD,
) -> dict[str, list[dict]]:
    """Two ranked lists from the scanned leaves.

    rate   — pct >= +pct_threshold (상승만) AND latest export ≥ rate_min_usd
             (noise floor — small lines double easily), sorted by pct desc.
    amount — sorted by export Δ$ desc (가장 많이 늘어난 순), NO floor
             (Δ$ ranking already buries small lines).
    Each row: hs_code, name, year_month, prev, curr, delta, pct.

    All ranked rows share ONE reference month: the latest confirmed month
    across the whole scan (global max year_month among per-leaf moves).
    Items whose freshest confirmed month is older (e.g. an intermittent
    line last shipped 2 months ago) are excluded so the panel never mixes
    '2026-04' and '2026-03' rows — every row compares the same month vs
    its prior. Operator chose this (uniform reference) over per-item latest;
    the trade-off is that a surge in a lagging-month item won't show until
    that item reports the current month."""
    moves = []
    for hs, node in leaves.items():
        mv = _latest_move(node["months"])
        if mv is None:
            continue
        mv = dict(mv)
        mv["hs_code"] = hs
        mv["name"] = node["name"]
        moves.append(mv)

    # Unify the reference month: keep only items whose latest confirmed
    # month is THE latest across all items.
    if moves:
        ref_ym = max(m["year_month"] for m in moves)
        moves = [m for m in moves if m["year_month"] == ref_ym]

    rate = [
        m for m in moves
        if m["pct"] is not None
        and m["pct"] >= pct_threshold
        and (m["curr"] or 0) >= rate_min_usd
    ]
    rate.sort(key=lambda m: m["pct"], reverse=True)

    amount = sorted(moves, key=lambda m: m["delta"], reverse=True)

    return {
        SECTION_RATE: rate[:top_n],
        SECTION_AMOUNT: amount[:top_n],
    }


def floor_histogram(leaves: dict[str, dict], pct_threshold: float = PCT_THRESHOLD) -> dict:
    """For --dry-run: count how many +PCT% surges survive at each export
    value floor, so a floor (if ever wanted) is chosen from data not a
    guess. Returns {floor_usd: count}."""
    floors = [1_000_000, 10_000_000, 50_000_000, 100_000_000]
    counts = {f: 0 for f in floors}
    for node in leaves.values():
        mv = _latest_move(node["months"])
        if mv is None or mv["pct"] is None or mv["pct"] < pct_threshold:
            continue
        for f in floors:
            if mv["curr"] >= f:
                counts[f] += 1
    return counts


# ───────────────────────── persistence ─────────────────────────

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS customs_surge_live (
            section TEXT NOT NULL, rank INTEGER NOT NULL,
            hs_code TEXT NOT NULL, name TEXT,
            year_month TEXT, prev INTEGER, curr INTEGER,
            pct REAL, delta INTEGER,
            PRIMARY KEY (section, rank)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS customs_surge_archive (
            hs_code TEXT NOT NULL, year_month TEXT NOT NULL, section TEXT NOT NULL,
            name TEXT, prev INTEGER, curr INTEGER, pct REAL, delta INTEGER,
            first_ts REAL, last_ts REAL,
            PRIMARY KEY (hs_code, year_month, section)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS customs_surge_seen (
            hs_code TEXT NOT NULL, year_month TEXT NOT NULL, section TEXT NOT NULL,
            PRIMARY KEY (hs_code, year_month, section)
        )"""
    )
    conn.commit()


def store_live(conn: sqlite3.Connection, ranked: dict[str, list[dict]]) -> None:
    """Replace the live snapshot (refresh) — old top items vanish from
    the live view but survive in the archive (upsert_archive)."""
    conn.execute("DELETE FROM customs_surge_live")
    for section, rows in ranked.items():
        for i, m in enumerate(rows):
            conn.execute(
                "INSERT INTO customs_surge_live "
                "(section, rank, hs_code, name, year_month, prev, curr, pct, delta) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (section, i, m["hs_code"], m["name"], m["year_month"],
                 m["prev"], m["curr"], m["pct"], m["delta"]),
            )
    conn.commit()


def upsert_archive(conn: sqlite3.Connection, ranked: dict[str, list[dict]],
                   now: float | None = None) -> int:
    """Append every current top item to the permanent archive. Keyed by
    (hs_code, year_month, section) so re-runs within the same month
    refresh last_ts without duplicating. Returns rows touched."""
    now = now if now is not None else time.time()
    n = 0
    for section, rows in ranked.items():
        for m in rows:
            conn.execute(
                """INSERT INTO customs_surge_archive
                   (hs_code, year_month, section, name, prev, curr, pct, delta, first_ts, last_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hs_code, year_month, section) DO UPDATE SET
                     name=excluded.name, prev=excluded.prev, curr=excluded.curr,
                     pct=excluded.pct, delta=excluded.delta, last_ts=excluded.last_ts""",
                (m["hs_code"], m["year_month"], section, m["name"],
                 m["prev"], m["curr"], m["pct"], m["delta"], now, now),
            )
            n += 1
    conn.commit()
    return n


def eval_new_entrants(conn: sqlite3.Connection, ranked: dict[str, list[dict]]) -> list[dict]:
    """Return items NEW to the live snapshot since last run (for the DM).

    Baseline-silent: if the seen table is empty (first ever run), mark
    everything seen and return [] — no alert flood on enable. Dedup key
    is (hs_code, year_month, section)."""
    cur = conn.execute("SELECT COUNT(*) FROM customs_surge_seen")
    is_baseline = (cur.fetchone()[0] == 0)
    new_entrants: list[dict] = []
    for section, rows in ranked.items():
        for m in rows:
            seen = conn.execute(
                "SELECT 1 FROM customs_surge_seen WHERE hs_code=? AND year_month=? AND section=?",
                (m["hs_code"], m["year_month"], section),
            ).fetchone()
            if not seen:
                conn.execute(
                    "INSERT OR IGNORE INTO customs_surge_seen "
                    "(hs_code, year_month, section) VALUES (?,?,?)",
                    (m["hs_code"], m["year_month"], section),
                )
                if not is_baseline:
                    e = dict(m)
                    e["section"] = section
                    new_entrants.append(e)
    conn.commit()
    return new_entrants


# ───────────────────────── read helpers (UI) ─────────────────────────

def get_live(conn: sqlite3.Connection, section: str) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM customs_surge_live WHERE section=? ORDER BY rank ASC",
        (section,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_archive(conn: sqlite3.Connection, section: str | None = None,
                limit: int = 200) -> list[dict]:
    """Archive newest-month first. Optional section filter."""
    if section:
        cur = conn.execute(
            "SELECT * FROM customs_surge_archive WHERE section=? "
            "ORDER BY year_month DESC, delta DESC LIMIT ?",
            (section, limit),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM customs_surge_archive "
            "ORDER BY year_month DESC, delta DESC LIMIT ?",
            (limit,),
        )
    return [dict(r) for r in cur.fetchall()]


# ───────────────────────── alert formatting ─────────────────────────

def format_alert(new_entrants: list[dict]) -> str:
    thr = f"{PCT_THRESHOLD:.0f}"
    head = f"🆕 <b>관세청 급변 신규 진입</b> (TOP{TOP_N} 갱신)"
    lines = [head, ""]
    rate = [e for e in new_entrants if e["section"] == SECTION_RATE]
    amount = [e for e in new_entrants if e["section"] == SECTION_AMOUNT]

    def _line(e: dict) -> str:
        return (
            f"• <b>{e['name']}</b> ({e['hs_code']})  "
            f"{customs.fmt_usd(e['prev'])} → {customs.fmt_usd(e['curr'])} "
            f"({customs.fmt_pct(e['pct'])}, Δ{customs.fmt_usd(e['delta'])})"
        )

    shown = 0
    if rate:
        lines.append(f"📈 <b>급등률 +{thr}%</b>")
        for e in rate[:ALERT_CAP - shown]:
            lines.append(_line(e)); shown += 1
    if amount and shown < ALERT_CAP:
        lines.append("💵 <b>급증액</b>")
        for e in amount[:ALERT_CAP - shown]:
            lines.append(_line(e)); shown += 1
    overflow = len(new_entrants) - shown
    if overflow > 0:
        lines.append(f"… 외 {overflow}건 더 (cap {ALERT_CAP}) — 대쉬보드에서 전체 확인")
    return "\n".join(lines)


# ───────────────────────── rendering (dashboard + DM) ─────────────────────────

def _esc(s) -> str:
    import html
    return html.escape(str(s if s is not None else ""))


def render_surge_html(db_path=None) -> str:
    """HTML cards for the dashboard: 📈 급등률 / 💵 급증액 (live, refreshed
    each run) + 🗄 급변 아카이브 (collapsible, unlimited history). Returns
    '' when there's no scan data yet so the dashboard omits the section."""
    db = Path(db_path) if db_path else customs.DEFAULT_DB
    if not db.exists():
        return ""
    try:
        with customs.session(db) as conn:
            init_db(conn)
            rate = get_live(conn, SECTION_RATE)
            amount = get_live(conn, SECTION_AMOUNT)
            archive = get_archive(conn, limit=300)
    except Exception:
        return ""
    if not rate and not amount and not archive:
        return ""

    def _live_card(title: str, rows: list[dict], metric: str) -> str:
        if not rows:
            return ""
        body = []
        for m in rows:
            cls = "up" if (m.get("delta") or 0) > 0 else ("down" if (m.get("delta") or 0) < 0 else "")
            highlight = (
                f"<td class='{cls}'>{customs.fmt_pct(m.get('pct'))}</td>"
                if metric == "pct" else
                f"<td class='{cls}'>{customs.fmt_usd(m.get('delta'))}</td>"
            )
            body.append(
                f"<tr><td>{_esc(m.get('name'))}</td>"
                f"<td>{_esc(m.get('hs_code'))}</td>"
                f"<td>{_esc(m.get('year_month'))}</td>"
                f"<td>{customs.fmt_usd(m.get('prev'))}→{customs.fmt_usd(m.get('curr'))}</td>"
                f"{highlight}</tr>"
            )
        metric_th = "전월비" if metric == "pct" else "증감액"
        # No 'open' — start collapsed so the operator expands on click
        # (the panel can be long; default-closed keeps the page compact).
        return (
            "<details class='customs-panel'>"
            f"<summary>{title} ({len(rows)})</summary>"
            "<table class='customs-table'><thead><tr>"
            f"<th>품목</th><th>HS</th><th>월</th><th>수출($)</th><th>{metric_th}</th>"
            "</tr></thead><tbody>" + "".join(body) + "</tbody></table></details>"
        )

    def _archive_card(rows: list[dict]) -> str:
        if not rows:
            return ""
        # Group by month (newest first). Within a month, MERGE the two
        # sections so one item that surged on both 📈 and 💵 shows as a
        # single line with both markers (was two separate rows before).
        by_month: dict[str, dict[str, dict]] = {}
        for r in rows:
            ym = r.get("year_month") or "?"
            code = r.get("hs_code")
            merged = by_month.setdefault(ym, {})
            cur = merged.get(code)
            if cur is None:
                cur = {
                    "name": r.get("name"), "hs_code": code,
                    "pct": r.get("pct"), "delta": r.get("delta"),
                    "sections": set(),
                }
                merged[code] = cur
            cur["sections"].add(r.get("section"))
            # keep the larger |delta| / defined pct if rows differ
            if (r.get("delta") or 0) and abs(r.get("delta") or 0) > abs(cur.get("delta") or 0):
                cur["delta"] = r.get("delta")
            if cur.get("pct") is None and r.get("pct") is not None:
                cur["pct"] = r.get("pct")

        months = sorted(by_month, reverse=True)
        # Total distinct items across all months (after merging a品목's
        # 📈/💵 rows). The header used to say 'N개월' = number of distinct
        # months, which read like a 'N-month retention cap' — misleading,
        # since retention is unlimited. Count items instead.
        item_count = sum(len(v) for v in by_month.values())
        blocks = []
        for idx, ym in enumerate(months):
            # newest month expanded; older months collapsed (volume grows
            # ~60 rows/month, so default-open only the latest).
            open_attr = " open" if idx == 0 else ""
            items = sorted(
                by_month[ym].values(),
                key=lambda m: abs(m.get("delta") or 0), reverse=True,
            )
            lis = []
            for m in items:
                mk = ""
                if SECTION_RATE in m["sections"]:
                    mk += "📈"
                if SECTION_AMOUNT in m["sections"]:
                    mk += "💵"
                lis.append(
                    f"<li>{mk} {_esc(m.get('name'))} "
                    f"<span class='muted'>({_esc(m.get('hs_code'))})</span> "
                    f"{customs.fmt_pct(m.get('pct'))} · Δ{customs.fmt_usd(m.get('delta'))}</li>"
                )
            blocks.append(
                f"<details class='archive-month'{open_attr}>"
                f"<summary>{_esc(ym)} ({len(items)})</summary>"
                "<ul>" + "".join(lis) + "</ul></details>"
            )
        return (
            "<details class='customs-panel'>"
            f"<summary>🗄 급변 아카이브 ({item_count}건 · 무제한 보관)</summary>"
            + "".join(blocks) + "</details>"
        )

    floor_lbl = customs.fmt_usd(RATE_MIN_USD)
    return (
        _live_card(f"📈 급등률 TOP <small>(수출 ≥{floor_lbl})</small>", rate, "pct")
        + _live_card("💵 급증액 TOP", amount, "amount")
        + _archive_card(archive)
    )


def render_surge_text(db_path=None, limit: int = 10) -> str:
    """Compact DM body for /customs — top of each live section. Returns
    '' when there's no scan data."""
    db = Path(db_path) if db_path else customs.DEFAULT_DB
    if not db.exists():
        return ""
    try:
        with customs.session(db) as conn:
            init_db(conn)
            rate = get_live(conn, SECTION_RATE)[:limit]
            amount = get_live(conn, SECTION_AMOUNT)[:limit]
    except Exception:
        return ""
    if not rate and not amount:
        return ""
    out = []
    if rate:
        out.append(
            f"📈 <b>급등률 TOP</b> (전월비 +{PCT_THRESHOLD:.0f}%, "
            f"수출 ≥{customs.fmt_usd(RATE_MIN_USD)})"
        )
        for m in rate:
            out.append(
                f"  ▲ {m['name']} ({m['hs_code']}) "
                f"{customs.fmt_usd(m['curr'])} ({customs.fmt_pct(m['pct'])})"
            )
    if amount:
        out.append("💵 <b>급증액 TOP</b> (증감액)")
        for m in amount:
            out.append(
                f"  Δ{customs.fmt_usd(m['delta'])} {m['name']} ({m['hs_code']}) "
                f"→ {customs.fmt_usd(m['curr'])}"
            )
    return "\n".join(out)
