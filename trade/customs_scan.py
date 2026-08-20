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
# Prev-month floor for the rate section (2026-06-12, 운영자 승인) — prev 가
# 사실상 0(수천$~수십만$)인 low-base 행이 +12,782,507% 같은 무의미한 %로
# 급등률 1~3위를 점거하던 것(나프타 0.00억$→0.75억$ 실사례). prev <
# $1M(0.01억$)이면 % 랭킹에서 제외 — 진짜 신규/재개 수출은 💵 급증액에
# Δ$ 로 그대로 랭크되므로 사라지지 않음 (수출($) 컬럼의 '0.00억$→0.75억$'
# 표기가 신규임을 그대로 전달). Env-tunable.
RATE_PREV_MIN_USD = int(
    os.environ.get("TRADE_CUSTOMS_RATE_PREV_MIN_USD") or "1000000")
# Safety: max pages per chapter so a runaway never hammers the quota.
MAX_PAGES = int(os.environ.get("TRADE_CUSTOMS_SCAN_MAX_PAGES") or "60")
# Alert cap (shared shape with customs_alert).
ALERT_CAP = int(os.environ.get("TRADE_CUSTOMS_ALERT_CAP") or "10")
TEASER = 3

SECTION_RATE = "rate"      # 📈 급등률 (수출)
SECTION_AMOUNT = "amount"  # 💵 급증액 (수출)
# 수입 방향 (사용자 2026-06-13 '수입도 수출처럼 급등률·급증액 TOP + 아카이브').
# 같은 section 컬럼의 별도 값 — export 행은 'rate'/'amount' 그대로라 기존 적재·
# seen 무변경(마이그레이션 0), 수입은 신규 section 으로 추가만 됨.
SECTION_RATE_IMP = "rate_import"      # 📈 급등률 (수입)
SECTION_AMOUNT_IMP = "amount_import"  # 💵 급증액 (수입)
_DIR_SECTIONS = {
    "export": (SECTION_RATE, SECTION_AMOUNT),
    "import": (SECTION_RATE_IMP, SECTION_AMOUNT_IMP),
}


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


def _yymm_minus(yymm: str, months: int) -> str:
    """'202606' minus N months → 'YYYYMM'."""
    y, m = int(yymm[:4]), int(yymm[4:6])
    total = y * 12 + (m - 1) - months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _split_windows(start_yymm: str, end_yymm: str, max_span: int = 12) -> list[tuple[str, str]]:
    """Split [start, end] into sub-windows each spanning ≤ max_span months.

    관세청 Itemtrade rejects any range wider than 1 year (resultCode 99:
    '조회기간은 1년이내'). YoY needs ≥13 months, so a single request can't
    cover it — we chunk into ≤12-month windows and the caller merges
    (build_series dedups by (hs, month), so overlap is harmless).
    Windows returned oldest-first."""
    def _idx(yymm):
        return int(yymm[:4]) * 12 + int(yymm[4:6]) - 1
    s, e = _idx(start_yymm), _idx(end_yymm)
    out = []
    cur = s
    while cur <= e:
        win_end = min(cur + max_span - 1, e)
        out.append((f"{cur // 12:04d}{cur % 12 + 1:02d}",
                    f"{win_end // 12:04d}{win_end % 12 + 1:02d}"))
        cur = win_end + 1
    return out


def fetch_chapter_range(
    chapter: str,
    start_yymm: str,
    end_yymm: str,
    *,
    key: Optional[str] = None,
    fetcher: Optional[Callable[[str], str]] = None,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """fetch_chapter over a span wider than the API's 1-year limit, by
    splitting into ≤12-month windows and concatenating. Rows may repeat a
    boundary month across windows; build_series dedups by (hs, month)."""
    rows: list[dict] = []
    for s, e in _split_windows(start_yymm, end_yymm, 12):
        rows.extend(fetch_chapter(chapter, s, e, key=key,
                                  fetcher=fetcher, max_pages=max_pages))
    return rows


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
        # 중량(kg)도 보존 (2026-06-13 단가 $/kg) — parse_response 가 이미
        # 주는 필드를 여기서 버리고 있었음. 기존 소비자(rank/heatmap/
        # aggregate)는 전부 키 접근(.get)이라 additive 무해, API 호출 0.
        node["months"][ym] = {
            "exp_dlr": r.get("exp_dlr") or 0,
            "imp_dlr": r.get("imp_dlr") or 0,
            "exp_wgt": r.get("exp_wgt") or 0,
            "imp_wgt": r.get("imp_wgt") or 0,
        }
    return leaves


def _latest_move(months: dict, direction: str = "export") -> Optional[dict]:
    """Latest-vs-previous move (방향별 — export=exp_dlr / import=imp_dlr),
    or None when < 2 usable months.

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
    fld = "imp_dlr" if direction == "import" else "exp_dlr"
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
    while ordered and (months[ordered[-1]].get(fld) or 0) == 0:
        ordered.pop()
    if len(ordered) < 2:
        return None
    cur_ym, prev_ym = ordered[-1], ordered[-2]
    curr = months[cur_ym].get(fld) or 0
    prev = months[prev_ym].get(fld) or 0
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
    rate_prev_min_usd: int = RATE_PREV_MIN_USD,
    direction: str = "export",
) -> dict[str, list[dict]]:
    """Two ranked lists from the scanned leaves.

    rate   — pct >= +pct_threshold (상승만) AND latest export ≥ rate_min_usd
             (noise floor — small lines double easily) AND prev ≥
             rate_prev_min_usd (low-base 컷 — prev 수천$ 행의 +1,000만%
             류가 % 랭킹 점거 방지, 2026-06-12), sorted by pct desc.
    amount — sorted by export Δ$ desc (가장 많이 늘어난 순), NO floor
             (Δ$ ranking already buries small lines — low-base 신규
             수출도 여기엔 그대로 랭크).
    Each row: hs_code, name, year_month, prev, curr, delta, pct.

    All ranked rows share ONE reference month: the latest confirmed month
    across the whole scan (global max year_month among per-leaf moves).
    Items whose freshest confirmed month is older (e.g. an intermittent
    line last shipped 2 months ago) are excluded so the panel never mixes
    '2026-04' and '2026-03' rows — every row compares the same month vs
    its prior. Operator chose this (uniform reference) over per-item latest;
    the trade-off is that a surge in a lagging-month item won't show until
    that item reports the current month."""
    rate_sec, amt_sec = _DIR_SECTIONS.get(direction, _DIR_SECTIONS["export"])
    moves = []
    for hs, node in leaves.items():
        mv = _latest_move(node["months"], direction)
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
        and (m["prev"] or 0) >= rate_prev_min_usd
    ]
    rate.sort(key=lambda m: m["pct"], reverse=True)

    amount = sorted(moves, key=lambda m: m["delta"], reverse=True)

    return {
        rate_sec: rate[:top_n],
        amt_sec: amount[:top_n],
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
    # 히트맵 leaf 스냅샷 (2026-06-12, 사용자 'Finviz식 히트맵') — 스윕이
    # 메모리에 들고 있던 전 leaf 의 (기준월, 전월, 작년동월) 수출·수입을
    # 저장 (추가 API 콜 0). 매 스윕 REPLACE (최신 스냅샷만).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS customs_heatmap_leaf (
            hs_code TEXT PRIMARY KEY, name TEXT, ref_ym TEXT,
            exp INTEGER, exp_pm INTEGER, exp_py INTEGER,
            imp INTEGER, imp_pm INTEGER, imp_py INTEGER,
            exp_wgt INTEGER, exp_wgt_pm INTEGER, exp_wgt_py INTEGER,
            imp_wgt INTEGER, imp_wgt_pm INTEGER, imp_wgt_py INTEGER
        )"""
    )
    # 중량(물량) 6열 마이그레이션 (사용자 2026-06-22 '판가 vs 물량') — 구 스냅샷
    # 테이블엔 금액만 있어 ADD COLUMN. 관세청 API가 이미 주는 exp_wgt/imp_wgt 를
    # 저장해 단가($/kg)=금액/중량 → 금액 증감을 판가·물량으로 분해. 멱등(존재 시 skip).
    for _col in ("exp_wgt", "exp_wgt_pm", "exp_wgt_py",
                 "imp_wgt", "imp_wgt_pm", "imp_wgt_py"):
        try:
            conn.execute(f"ALTER TABLE customs_heatmap_leaf ADD COLUMN {_col} INTEGER")
        except sqlite3.OperationalError:
            pass        # 이미 있음
    conn.commit()


def heatmap_rows(leaves: dict[str, dict]) -> list[dict]:
    """스윕 leaves → 히트맵 행 (순수 — 단위테스트).

    기준월 = 전 leaf 공통 최신 실월(트레일링 0 제외, rank 와 동일 원칙).
    각 leaf: 기준월/전월/작년동월의 수출·수입 USD. 기준월 데이터 없는
    leaf 는 제외 (간헐 수출 라인 — 트리맵 박스 0 크기 방지)."""
    moves = []
    for hs, node in leaves.items():
        mv = _latest_move(node["months"])
        if mv is not None:
            moves.append((hs, mv["year_month"]))
    if not moves:
        return []
    ref_ym = max(ym for _, ym in moves)
    pm_ym = _yymm_minus(ref_ym.replace("-", ""), 1)
    py_ym = _yymm_minus(ref_ym.replace("-", ""), 12)
    pm_key = f"{pm_ym[:4]}-{pm_ym[4:6]}"
    py_key = f"{py_ym[:4]}-{py_ym[4:6]}"
    out: list[dict] = []
    for hs, node in leaves.items():
        m = node["months"]
        ref = m.get(ref_ym)
        if not ref:
            continue
        exp = int(ref.get("exp_dlr") or 0)
        imp = int(ref.get("imp_dlr") or 0)
        if exp <= 0 and imp <= 0:
            continue
        pm = m.get(pm_key) or {}
        py = m.get(py_key) or {}
        out.append({
            "hs_code": hs, "name": node.get("name") or hs, "ref_ym": ref_ym,
            "exp": exp, "exp_pm": int(pm.get("exp_dlr") or 0),
            "exp_py": int(py.get("exp_dlr") or 0),
            "imp": imp, "imp_pm": int(pm.get("imp_dlr") or 0),
            "imp_py": int(py.get("imp_dlr") or 0),
            # 중량(물량) — 판가·물량 분해용 (사용자 2026-06-22). API가 이미 주는 값.
            "exp_wgt": int(ref.get("exp_wgt") or 0),
            "exp_wgt_pm": int(pm.get("exp_wgt") or 0),
            "exp_wgt_py": int(py.get("exp_wgt") or 0),
            "imp_wgt": int(ref.get("imp_wgt") or 0),
            "imp_wgt_pm": int(pm.get("imp_wgt") or 0),
            "imp_wgt_py": int(py.get("imp_wgt") or 0),
        })
    return out


def store_heatmap(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """히트맵 스냅샷 교체 저장. 빈 rows 면 기존 스냅샷 유지(부분 스캔 보호
    — coverage guard 와 동일 철학)."""
    if not rows:
        return
    conn.execute("DELETE FROM customs_heatmap_leaf")
    conn.executemany(
        "INSERT OR REPLACE INTO customs_heatmap_leaf "
        "(hs_code, name, ref_ym, exp, exp_pm, exp_py, imp, imp_pm, imp_py, "
        " exp_wgt, exp_wgt_pm, exp_wgt_py, imp_wgt, imp_wgt_pm, imp_wgt_py) "
        "VALUES (:hs_code,:name,:ref_ym,:exp,:exp_pm,:exp_py,:imp,:imp_pm,:imp_py,"
        " :exp_wgt,:exp_wgt_pm,:exp_wgt_py,:imp_wgt,:imp_wgt_pm,:imp_wgt_py)",
        rows)
    conn.commit()


def load_heatmap(conn: sqlite3.Connection) -> list[dict]:
    try:
        # SELECT * — 중량(물량) 6열은 마이그레이션 후 자동 포함, 구 스냅샷이면
        # 그 열만 없는 채로(report 가 .get 으로 graceful) (사용자 2026-06-22).
        cur = conn.execute("SELECT * FROM customs_heatmap_leaf")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


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

    Baseline-silent **per section**: a section with zero seen rows (first
    ever run, OR a newly-added section like 수입 rate_import/amount_import)
    seeds silently — no alert flood on enable. Dedup key (hs_code,
    year_month, section). 수입 추가(2026-06-13) 시 기존 export seen 이 있어도
    수입 section 은 자기 기준 baseline 이라 첫 등장에 홍수 안 남."""
    new_entrants: list[dict] = []
    for section, rows in ranked.items():
        sc = conn.execute("SELECT COUNT(*) FROM customs_surge_seen "
                          "WHERE section=?", (section,)).fetchone()[0]
        is_baseline = (sc == 0)
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

    def _line(e: dict) -> str:
        return (
            f"• <b>{e['name']}</b> ({e['hs_code']})  "
            f"{customs.fmt_usd(e['prev'])} → {customs.fmt_usd(e['curr'])} "
            f"({customs.fmt_pct(e['pct'])}, Δ{customs.fmt_usd(e['delta'])})"
        )

    # (방향, section, 헤더) 순서 — 수출 먼저, 수입 뒤. 같은 cap 공유.
    groups = [
        ("📈 <b>급등률 +%s%% (수출)</b>" % thr, SECTION_RATE),
        ("💵 <b>급증액 (수출)</b>", SECTION_AMOUNT),
        ("📈 <b>급등률 +%s%% (수입)</b>" % thr, SECTION_RATE_IMP),
        ("💵 <b>급증액 (수입)</b>", SECTION_AMOUNT_IMP),
    ]
    shown = 0
    for hdr, sec in groups:
        bucket = [e for e in new_entrants if e.get("section") == sec]
        if not bucket or shown >= ALERT_CAP:
            continue
        lines.append(hdr)
        for e in bucket[:ALERT_CAP - shown]:
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
            rate_imp = get_live(conn, SECTION_RATE_IMP)
            amount_imp = get_live(conn, SECTION_AMOUNT_IMP)
            archive = get_archive(conn, limit=600)   # 수출+수입 합쳐 분리 렌더
            # 방향별 전체 병합건수(월×품목 distinct) — 표시가 600행 캡에
            # 걸려 잘렸는지 대조용. 라벨이 '무제한 보관'인데 표시는 조용히
            # 잘리면 옛 달이 사라진 걸 아무도 모른다(silent cap 금지 —
            # 2026-08-20 전수 감사. DB 보관 자체는 실제로 무제한).
            arch_totals = {}
            for _d, _secs in (("수출", (SECTION_RATE, SECTION_AMOUNT)),
                              ("수입", (SECTION_RATE_IMP, SECTION_AMOUNT_IMP))):
                arch_totals[_d] = conn.execute(
                    "SELECT COUNT(DISTINCT year_month || '|' || hs_code) "
                    "FROM customs_surge_archive WHERE section IN (?,?)",
                    _secs).fetchone()[0]
    except Exception:
        return ""
    if not (rate or amount or rate_imp or amount_imp or archive):
        return ""

    def _live_card(title: str, rows: list[dict], metric: str,
                   dir_label: str = "수출") -> str:
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
            f"<th>품목</th><th>HS</th><th>월</th><th>{dir_label}($)</th><th>{metric_th}</th>"
            "</tr></thead><tbody>" + "".join(body) + "</tbody></table></details>"
        )

    def _archive_card(rows: list[dict], rate_sec: str, amt_sec: str,
                      dir_label: str = "수출", total_items: int = 0) -> str:
        rows = [r for r in rows if r.get("section") in (rate_sec, amt_sec)]
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
                if rate_sec in m["sections"]:
                    mk += "📈"
                if amt_sec in m["sections"]:
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
        # 표시가 fetch 캡(600행)에 걸려 잘렸으면 라벨이 사실을 말해야 한다 —
        # '무제한 보관'은 DB 얘기고 화면은 최근 우선 일부일 수 있다.
        if total_items > item_count:
            log.warning(
                "급변 아카이브(%s) 표시 잘림: %d/%d건 — get_archive limit 상향 고려",
                dir_label, item_count, total_items)
            cnt_label = (f"최근 {item_count}건 표시 / 전체 {total_items}건 · "
                         "DB 무제한 보관")
        else:
            cnt_label = f"{item_count}건 · 무제한 보관"
        return (
            "<details class='customs-panel'>"
            f"<summary>🗄 급변 아카이브 ({dir_label}) "
            f"({cnt_label})</summary>"
            + "".join(blocks) + "</details>"
        )

    floor_lbl = customs.fmt_usd(RATE_MIN_USD)
    # 사용자 2026-06-13: 수입을 수출 바로 옆/아래로 나란히, 한눈에. 수입 데이터
    # 없으면(_live_card rows 빈) 자동 생략 — 첫 수입 스캔 전엔 수출만 보임.
    return (
        _live_card(f"📈 급등률 TOP <small>(수출 ≥{floor_lbl})</small>", rate, "pct", "수출")
        + _live_card(f"📈 급등률 TOP <small>(수입 ≥{floor_lbl})</small>",
                     rate_imp, "pct", "수입")
        + _live_card("💵 급증액 TOP <small>(수출)</small>", amount, "amount", "수출")
        + _live_card("💵 급증액 TOP <small>(수입)</small>", amount_imp, "amount", "수입")
        + _archive_card(archive, SECTION_RATE, SECTION_AMOUNT, "수출",
                        total_items=arch_totals.get("수출", 0))
        + _archive_card(archive, SECTION_RATE_IMP, SECTION_AMOUNT_IMP, "수입",
                        total_items=arch_totals.get("수입", 0))
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
            rate_imp = get_live(conn, SECTION_RATE_IMP)[:limit]
            amount_imp = get_live(conn, SECTION_AMOUNT_IMP)[:limit]
    except Exception:
        return ""
    if not (rate or amount or rate_imp or amount_imp):
        return ""
    out = []

    def _rate_block(rows, dirn):
        if not rows:
            return
        out.append(f"📈 <b>급등률 TOP ({dirn})</b> (전월비 +{PCT_THRESHOLD:.0f}%, "
                   f"{dirn} ≥{customs.fmt_usd(RATE_MIN_USD)})")
        for m in rows:
            out.append(f"  ▲ {m['name']} ({m['hs_code']}) "
                       f"{customs.fmt_usd(m['curr'])} ({customs.fmt_pct(m['pct'])})")

    def _amt_block(rows, dirn):
        if not rows:
            return
        out.append(f"💵 <b>급증액 TOP ({dirn})</b> (증감액)")
        for m in rows:
            out.append(f"  Δ{customs.fmt_usd(m['delta'])} {m['name']} ({m['hs_code']}) "
                       f"→ {customs.fmt_usd(m['curr'])}")

    _rate_block(rate, "수출")
    _rate_block(rate_imp, "수입")
    _amt_block(amount, "수출")
    _amt_block(amount_imp, "수입")
    return "\n".join(out)
